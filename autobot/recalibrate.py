"""
Nightly recalibration: which strategy (and settings) each universe symbol should trade.

    python -m autobot.recalibrate                   # as of the last trading day, writes the proposal
    python -m autobot.recalibrate --date 2026-09-30 --symbols NVDA,TSLA --no-write

For each symbol in the latest universe proposal (minus symbols owned by hand-made jobs):

1. Every eligible strategy x every parameter set in its grid is backtested once on the full
   history (valid because strategies are causal), with the asset class's costs and, for stocks,
   flat before the close exactly like the live bot (risk.flatten_before_close_min).
2. The last `lookback_days` are cut into walk-forward windows of `train_days` / `test_days`
   (New York dates; crypto days are 24 h). In every window, using the TRAIN days only:
     - each strategy's best parameter set,
     - the single best strategy ("single"),
     - the top `top_k` strategies, combined into regime_switch (every trend/sideways pairing,
       jumpy = stay out, confirm bars) and perf_rotation; the best of those ("meta").
   Each choice is then traded on the window's TEST days and the test slices are stitched.
   Because every choice is made on train days only, the stitched result is an honest
   out-of-sample (OOS) score of the whole selection procedure.
3. Score = weighted OOS Sharpe, net return, max drawdown and turnover (`score_weights`).
   The challenger is "meta" if it out-scores "single", else "single"; the job it proposes is
   the LAST window's choice (tuned on the most recent train days).
4. The incumbent is the symbol's current auto job (fixed settings) scored over the same OOS
   days, or "no job" (score 0: doing nothing). Champion/challenger: the challenger replaces
   it only with >= min_edge better score, >= min_trades OOS trades, the incumbent in place
   >= min_hold_days and fewer than max_changes_per_week changes in the last 7 days.
   Every decision carries a plain-English reason (plain.RECAL_REASONS).

Output: data/recalibration/{date}.json. Nothing here edits config.yaml (Phase 3 applies).
Causal: run(as_of=D) only uses bars with a New York date <= D.
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd
import yaml

from . import plain, universe
from .backtest import CostModel, _replay, bars_per_year, metrics, param_combos, run_backtest
from .data import fetch_bars, is_crypto
from .strategies import STRATEGIES, get_signal

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "recalibration"
STATE = OUT_DIR / "state.json"
NY = "America/New_York"
AUTO_PREFIX = "auto_"
ETFS = {"SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "USO", "TLT"}

DEFAULTS = {
    "mode": "propose",
    "take_over_hand_made": False,   # True: hand-made jobs' symbols are recalibrated too (their job is the incumbent)
    "lookback_days": 90,
    "warmup_days": 30,
    "train_days": 20,
    "test_days": 5,
    "top_k": 3,
    "confirm_bars": 3,
    "allocation_pct": 10,
    "min_edge": 0.25,
    "min_trades": 10,
    "min_hold_days": 5,
    "max_changes_per_week": 1,
    "score_weights": {"sharpe": 1.0, "return": 0.5, "drawdown": 0.5, "turnover": 0.05},
    "costs": {  # bps per unit of turnover; crypto: Alpaca tier-1 taker fee 25 bps + thin-venue slippage
        "stock": {"fee_bps": 0.0, "slippage_bps": 2.0},
        "etf": {"fee_bps": 0.0, "slippage_bps": 2.0},
        "crypto": {"fee_bps": 25.0, "slippage_bps": 5.0},
    },
    "exclude": ["connors_rsi2", "ibs_reversion", "news_guard"],   # daily-bar designs; news_guard showed no edge
    "exclude_families_crypto": ["intraday"],                       # they assume a session open/close
}


def settings(cfg: Optional[Dict] = None) -> Dict:
    if cfg is None:
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    r = {**DEFAULTS, **(cfg.get("recalibration") or {})}
    r["costs"] = {**DEFAULTS["costs"], **(r.get("costs") or {})}
    r["score_weights"] = {**DEFAULTS["score_weights"], **(r.get("score_weights") or {})}
    r["timeframe"] = cfg["live"]["timeframe"]
    r["flatten_min"] = int((cfg["live"].get("risk") or {}).get("flatten_before_close_min", 0))
    return r


def asset_class(sym: str) -> str:
    return "crypto" if is_crypto(sym) else "etf" if sym in ETFS else "stock"


def costs_for(sym: str, r: Dict) -> CostModel:
    c = r["costs"][asset_class(sym)]
    return CostModel(float(c.get("fee_bps", 0)), float(c.get("slippage_bps", 0)))


def eligible(sym: str, r: Dict) -> List[str]:
    out = []
    for name, spec in STRATEGIES.items():
        if spec.family in ("meta", "news") or name in r["exclude"]:
            continue
        if is_crypto(sym) and spec.family in r["exclude_families_crypto"]:
            continue
        out.append(name)
    return out


# ----------------------------------------------------------------- building blocks
def flat_before_close(sig: pd.Series, df: pd.DataFrame, minutes: int) -> pd.Series:
    """Stocks: target 0 on bars that close within `minutes` of 16:00 New York, like the live
    bot's flatten rule (the backtester trades that target on the next bar)."""
    if minutes <= 0 or len(df) < 2:
        return sig
    step = pd.Series(df.index).diff().median()
    ny = (df.index + step).tz_convert(NY)
    close_min = np.asarray(ny.hour * 60 + ny.minute)
    out = sig.copy()
    out[(close_min >= 16 * 60 - minutes) | (close_min < 9 * 60 + 30)] = 0.0
    return out


def quick(ret: np.ndarray, pos: np.ndarray, ppy: float, days: int) -> Dict:
    """Fast window statistics: Sharpe, net return %, max drawdown %, turnover per day, trades."""
    if len(ret) == 0:
        return {"sharpe": 0.0, "return_%": 0.0, "max_drawdown_%": 0.0, "turnover_per_day": 0.0, "trades": 0}
    sd = ret.std()
    eq = np.cumprod(1 + ret)
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    turn = np.abs(np.diff(pos, prepend=pos[0] if len(pos) else 0)).sum()
    entries = int(((pos != 0) & (np.r_[0, pos[:-1]] != pos)).sum())
    return {"sharpe": float(ret.mean() / sd * math.sqrt(ppy)) if sd > 0 else 0.0,
            "return_%": float((eq[-1] - 1) * 100), "max_drawdown_%": float(dd * 100),
            "turnover_per_day": float(turn / max(1, days)), "trades": entries}


def score(q: Dict, w: Dict) -> float:
    """Higher is better. Return and drawdown are in %, scaled /10 so 1 Sharpe ~ 10% return."""
    return float(w["sharpe"] * q["sharpe"] + w["return"] * q["return_%"] / 10
                 + w["drawdown"] * q["max_drawdown_%"] / 10 - w["turnover"] * q["turnover_per_day"])


def windows(day: np.ndarray, start_day: date, train: int, test: int):
    """[(train_a, train_b, test_a, test_b)] bar index ranges; `day` = NY date per bar."""
    days = sorted(d for d in set(day) if d >= start_day)
    first = {d: int(np.searchsorted(day, d, side="left")) for d in days}
    end = len(day)
    out = []
    for s in range(0, len(days) - train, test):
        tr, te = days[s:s + train], days[s + train:s + train + test]
        if not te:
            break
        b_te = first[days[s + train + test]] if s + train + test < len(days) else end
        out.append((first[tr[0]], first[te[0]], first[te[0]], b_te))
    return out


def describe(spec: Optional[Dict]) -> str:
    """Plain-English label for a job spec (single strategy, meta, or none)."""
    if not spec:
        return plain.RECAL_SHAPES["none"]
    name, p = spec["strategy"], spec.get("params") or {}
    if name == "regime_switch":
        return plain.RECAL_SHAPES["regime_switch"].format(trend=plain.nick(p.get("trend_strategy")),
                                                          range=plain.nick(p.get("range_strategy")))
    if name == "perf_rotation":
        return plain.RECAL_SHAPES["perf_rotation"].format(names=", ".join(plain.nick(n) for n in p.get("candidates", [])))
    shown = {k: v for k, v in p.items() if k in (STRATEGIES[name].grid or {})}
    ps = (" " + ", ".join(f"{k} {v}" for k, v in shown.items())) if shown else ""
    return plain.RECAL_SHAPES["single"].format(nick=plain.nick(name), name=name, params=ps)


def _key(spec: Dict) -> str:
    return json.dumps(spec, sort_keys=True, default=str)


# ----------------------------------------------------------------- one symbol
class SymbolStudy:
    """All backtests for one symbol, computed once and sliced per window."""

    def __init__(self, sym: str, df: pd.DataFrame, r: Dict, as_of: date):
        self.sym, self.df, self.r = sym, df, r
        self.costs = costs_for(sym, r)
        self.flat_min = 0 if is_crypto(sym) else r["flatten_min"]
        self.day = np.asarray(df.index.tz_convert(NY).date)
        self.ppy = bars_per_year(df.index)
        start = as_of - timedelta(days=int(r["lookback_days"]) - 1)
        self.win = windows(self.day, start, int(r["train_days"]), int(r["test_days"]))
        self._bt: Dict[str, pd.DataFrame] = {}

    def bt(self, spec: Dict) -> pd.DataFrame:
        k = _key(spec)
        if k not in self._bt:
            sig = get_signal(spec["strategy"], self.df, spec.get("params") or {}, False)
            if is_crypto(self.sym):
                sig = sig.clip(lower=0)  # no crypto shorting on Alpaca
            sig = flat_before_close(sig, self.df, self.flat_min)
            self._bt[k] = run_backtest(self.df, sig, self.costs)
        return self._bt[k]

    def wscore(self, spec: Dict, a: int, b: int) -> Dict:
        bt = self.bt(spec)
        days = len(set(self.day[a:b]))
        q = quick(bt.ret.to_numpy()[a:b], bt.position.to_numpy()[a:b], self.ppy, days)
        return {**q, "score": score(q, self.r["score_weights"])}

    def oos(self, picks: List[Dict]) -> Dict:
        """Stitch each window's pick over its test days and replay with costs (a change of pick
        at a window boundary pays for the position change)."""
        if not self.win:
            return {"score": 0.0, "trades": 0}
        a0 = self.win[0][2]
        idx = self.df.index[a0:self.win[-1][3]]
        pos = pd.Series(0.0, index=idx)
        for (_, _, a, b), spec in zip(self.win, picks):
            if spec is not None:
                pos.iloc[a - a0:b - a0] = self.bt(spec).position.to_numpy()[a:b]
        bt = _replay(self.df.loc[idx], pos, self.costs)
        days = len(set(self.day[a0:self.win[-1][3]]))
        q = quick(bt.ret.to_numpy(), bt.position.to_numpy(), self.ppy, days)
        m = metrics(bt)
        return {**q, "score": score(q, self.r["score_weights"]), "trades": m["trades"],
                "win_rate_%": m["win_rate_%"], "days": days, "from": str(self.day[a0]), "to": str(self.day[self.win[-1][3] - 1])}

    def meta_specs(self, top: List[Dict]) -> List[Dict]:
        names = [t["strategy"] for t in top]
        cp = {t["strategy"]: t["params"] for t in top}
        out = []
        for tr in names:
            for rg in names:
                if tr != rg:
                    out.append({"strategy": "regime_switch", "params": {
                        "trend_strategy": tr, "range_strategy": rg, "high_vol_strategy": "flat",
                        "confirm_bars": int(self.r["confirm_bars"]), "candidate_params": {tr: cp[tr], rg: cp[rg]}}})
        if len(names) >= 2:
            out.append({"strategy": "perf_rotation", "params": {
                "candidates": names, "candidate_params": cp, "cost_bps": self.costs.fee_bps + self.costs.slippage_bps}})
        return out

    def study(self, names: List[str], incumbent: Optional[Dict], progress=None) -> Dict:
        combos = {n: [{"strategy": n, "params": p} for p in param_combos(n)] for n in names}
        per_strategy = {n: [] for n in names}       # each window's best params, per strategy
        single, meta = [], []
        for wi, (ta, tb, _, _) in enumerate(self.win):
            best = {}
            for n, specs in combos.items():
                scored = [(self.wscore(s, ta, tb)["score"], s) for s in specs]
                sc, s = max(scored, key=lambda x: x[0])
                best[n] = (sc, s)
                per_strategy[n].append(s)
            ranked = sorted(best.values(), key=lambda x: -x[0])
            single.append(ranked[0][1])
            top = [s for _, s in ranked[: int(self.r["top_k"])]]
            metas = self.meta_specs(top)
            meta.append(max(metas, key=lambda s: self.wscore(s, ta, tb)["score"]) if metas else None)
            if progress:
                progress((wi + 1) / max(1, len(self.win)), f"{self.sym}: window {wi + 1}/{len(self.win)}")
        res = {"strategies": {n: {**self.oos(per_strategy[n]), "last_params": per_strategy[n][-1]["params"] if per_strategy[n] else None}
                              for n in names},
               "single": {**self.oos(single), "spec": single[-1] if single else None,
                          "picks": [s["strategy"] for s in single]},
               "meta": {**self.oos(meta), "spec": meta[-1] if meta else None}}
        res["incumbent"] = ({**self.oos([incumbent] * len(self.win)), "spec": incumbent} if incumbent
                            else {"score": 0.0, "trades": 0, "sharpe": 0.0, "return_%": 0.0, "max_drawdown_%": 0.0, "spec": None})
        res["windows"] = len(self.win)
        return res


# ----------------------------------------------------------------- champion / challenger
def decide(sym: str, inc: Dict, ch: Dict, r: Dict, hist: Dict, today: date) -> Dict:
    """The rule, with its reason. inc/ch: {"spec", "score", "trades"}; hist: state for the symbol."""
    f = dict(inc=describe(inc["spec"]), ch=describe(ch["spec"]), cs=f"{ch['score']:.2f}", is_=f"{inc['score']:.2f}",
             d=f"{ch['score'] - inc['score']:+.2f}", edge=f"{r['min_edge']:.2f}", n=ch["trades"], min=r["min_trades"])
    f["is"] = f.pop("is_")

    def out(outcome, change, **extra):
        return {"outcome": outcome, "change": change, "reason": plain.RECAL_REASONS[outcome].format(**{**f, **extra})}
    if inc["spec"] and ch["spec"] and _key(inc["spec"]) == _key(ch["spec"]):
        return out("keep_same", False)
    if ch["trades"] < r["min_trades"]:
        return out("keep_trades" if inc["spec"] else "no_job_trades", False)
    edge = ch["score"] - inc["score"]
    if edge < r["min_edge"]:
        if not inc["spec"]:
            return out("no_job_weak", False)
        return out("keep_better" if edge <= 0 else "keep_edge", False)
    if inc["spec"]:
        since = hist.get("since")
        if since and (today - date.fromisoformat(since)).days < r["min_hold_days"]:
            return out("keep_hold", False, days=(today - date.fromisoformat(since)).days, min=r["min_hold_days"])
    recent = [d for d in hist.get("changes", []) if (today - date.fromisoformat(d)).days < 7]
    if len(recent) >= r["max_changes_per_week"]:
        return out("keep_changes", False, n=len(recent), max=r["max_changes_per_week"])
    return out("replace" if inc["spec"] else "new_job", True)


def load_state(path: Optional[Path] = None) -> Dict:
    try:
        return json.loads((path or STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def job_spec(job: Dict) -> Dict:
    return {"strategy": job["strategy"], "params": job.get("params") or {}}


# ----------------------------------------------------------------- whole run
def run(as_of: Optional[date] = None, cfg: Optional[Dict] = None, fetch: Optional[Callable] = None,
        symbols: Optional[List[str]] = None, out_dir: Optional[Path] = None, write: bool = True,
        progress: Optional[Callable[[float, str], None]] = None, universe_result: Optional[Dict] = None) -> Dict:
    if cfg is None:
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    r = settings(cfg)
    as_of = as_of or pd.Timestamp.now(tz=NY).date()
    out_dir = out_dir or OUT_DIR
    say = progress or (lambda f, m: None)
    jobs = cfg["live"].get("jobs") or []
    hand_jobs = [j for j in jobs if j.get("enabled", True) and not j["name"].startswith(AUTO_PREFIX)]
    hand_of = {s: j for j in hand_jobs for s in j["symbols"]}
    take = bool(r.get("take_over_hand_made"))
    auto = {j["symbols"][0]: j for j in jobs if j["name"].startswith(AUTO_PREFIX) and j.get("symbols")}
    uni = universe_result or universe.latest(before=(as_of + timedelta(days=1)).isoformat())
    listed = list(symbols or (uni["selected"] if uni else []))
    todo = [s for s in listed if take or s not in hand_of]
    hand = set(hand_of) if not take else set()
    removed = [s for s in auto if s not in listed and not symbols]
    fetch = fetch or (lambda syms, tf, days: fetch_bars(syms, tf, days, rth_only=True))
    state = load_state(out_dir / "state.json")
    rows, proposed = {}, []
    need_days = int(r["train_days"]) + int(r["test_days"])
    for i, sym in enumerate(todo):
        base = i / max(1, len(todo))
        say(base, f"{sym}: loading prices")
        inc_job = auto.get(sym)
        taking = take and inc_job is None and sym in hand_of   # a hand-made job hands this symbol over
        if taking:
            inc_job = hand_of[sym]
        inc_spec = job_spec(inc_job) if inc_job else None
        try:
            df = fetch([sym], r["timeframe"], int(r["lookback_days"]) + int(r["warmup_days"]) + 5).get(sym)
            df = df[np.asarray(df.index.tz_convert(NY).date) <= as_of] if df is not None else None  # causal cut
        except Exception as e:  # noqa: BLE001
            df, err = None, str(e)
        else:
            err = None
        n_days = len(set(df.index.tz_convert(NY).date)) if df is not None and len(df) else 0
        if df is None or n_days < need_days + 1:
            rows[sym] = {"symbol": sym, "error": err, "decision": {
                "outcome": "no_data", "change": False,
                "reason": plain.RECAL_REASONS["no_data"].format(sym=sym, days=n_days, need=need_days + 1)},
                "incumbent": {"spec": inc_spec, "label": describe(inc_spec)}}
            if inc_job and not taking:   # a hand-made job just stays where it is
                proposed.append(inc_job)
            continue
        st = SymbolStudy(sym, df, r, as_of)
        res = st.study(eligible(sym, r), inc_spec,
                       progress=lambda f, m: say(base + f / max(1, len(todo)), m))
        ch = res["meta"] if res["meta"]["spec"] and res["meta"]["score"] > res["single"]["score"] else res["single"]
        ch_kind = "meta" if ch is res["meta"] else "single"
        dec = decide(sym, res["incumbent"], ch, r, state.get(sym, {}), as_of)
        final = ch["spec"] if dec["change"] else inc_spec
        if final:
            proposed.append({"name": AUTO_PREFIX + sym.replace("/", ""), "strategy": final["strategy"],
                             "params": final.get("params") or {}, "symbols": [sym],
                             "allocation_pct": r["allocation_pct"], "enabled": True})
        if taking:  # the job moves from the hand-made job to auto_SYM even when the strategy stays the same
            dec = {**dec, "takeover": True, "reason": dec["reason"] + " " + plain.RECAL_TAKEOVER.format(job=inc_job["name"])}
        rows[sym] = {"symbol": sym, "asset_class": asset_class(sym), "costs_bps": st.costs.fee_bps + st.costs.slippage_bps,
                     "windows": res["windows"], "challenger_kind": ch_kind,
                     "incumbent": {**res["incumbent"], "label": describe(inc_spec) + (f" (your job '{inc_job['name']}')" if taking else "")},
                     "challenger": {**ch, "label": describe(ch["spec"])},
                     "single": {**res["single"], "label": describe(res["single"]["spec"])},
                     "meta": {**res["meta"], "label": describe(res["meta"]["spec"])},
                     "strategies": res["strategies"], "decision": dec,
                     "proposed": {"spec": final, "label": describe(final)}}
    for sym in removed:
        rows[sym] = {"symbol": sym, "incumbent": {"spec": job_spec(auto[sym]), "label": describe(job_spec(auto[sym]))},
                     "decision": {"outcome": "remove", "change": True,
                                  "reason": plain.RECAL_REASONS["remove"].format(inc=describe(job_spec(auto[sym])), sym=sym)},
                     "proposed": {"spec": None, "label": describe(None)}}
    res = {"date": as_of.isoformat(), "generated": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
           "kind": "proposal", "mode": r["mode"], "timeframe": r["timeframe"],
           "universe_date": uni["date"] if uni else None, "hand_made_symbols": sorted(hand & set(listed)),
           "take_over": take, "taken_over": sorted(s for s, v in rows.items() if v["decision"].get("takeover")),
           "settings": {k: r[k] for k in ("lookback_days", "train_days", "test_days", "top_k", "confirm_bars", "min_edge",
                                          "min_trades", "min_hold_days", "max_changes_per_week", "score_weights", "costs")},
           "symbols": rows, "proposed_jobs": proposed,
           "changes": [s for s, v in rows.items() if v["decision"]["change"] or v["decision"].get("takeover")]}
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{as_of.isoformat()}.json").write_text(json.dumps(res, indent=1, default=_json), encoding="utf-8")
    say(1.0, "done")
    return res


def _json(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, (date, pd.Timestamp)):
        return str(o)
    return str(o)


def latest(out_dir: Optional[Path] = None) -> Optional[Dict]:
    d = out_dir or OUT_DIR
    files = sorted(p for p in d.glob("????-??-??.json")) if d.exists() else []
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def main():
    import argparse
    import sys
    import time
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="as-of New York date (default: today)")
    ap.add_argument("--symbols", help="comma-separated; default: the latest universe proposal")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    res = run(date.fromisoformat(a.date) if a.date else None, symbols=a.symbols.split(",") if a.symbols else None,
              write=not a.no_write, progress=lambda f, m: print(f"{f:5.0%} {m}", flush=True))
    print(f"\nRecalibration {res['date']} ({time.time() - t0:.0f} s), universe {res['universe_date']}, "
          f"hand-made symbols left alone: {', '.join(res['hand_made_symbols']) or '-'}")
    for s, v in res["symbols"].items():
        print(f"\n{s}: {v['decision']['outcome'].upper()}  {v['decision']['reason']}")
        if "challenger" in v:
            for k in ("incumbent", "single", "meta"):
                x = v[k]
                print(f"   {k:<9} score {x.get('score', 0):6.2f}  sharpe {x.get('sharpe', 0):5.2f}  ret {x.get('return_%', 0):6.2f}%  "
                      f"dd {x.get('max_drawdown_%', 0):6.2f}%  trades {x.get('trades', 0):>4}  {x['label']}")
    print("\nChanges proposed:", ", ".join(res["changes"]) or "none")


if __name__ == "__main__":
    main()
