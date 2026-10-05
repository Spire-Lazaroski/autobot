"""
Markov switching study. Diagnosis only: nothing here changes the bot.

    python -m autobot.markov_study --part a     # 5-minute bars: regime_switch with a Markov turbulence detector
    python -m autobot.markov_study --part b     # daily bars: exposure reduced when turbulence is likely

Part B (option B): daily bars, hold the stock but cut exposure when the Markov model says the
turbulent state is likely. Refit every 21 sessions on the previous 500 sessions of daily returns.
Two rules fixed before the run: "scaled" holds 1 - P(turbulent) (changed only in steps of at least
0.1), "on/off" goes to cash when P(turbulent) > 0.5. Same fills and costs as the daily study (signal
at the close, traded at the next open, open costs 2x); compared with buy and hold and with the daily
study's volatility targeting on the same days; on the daily-study symbols, on VOO, and on FRED's
S&P 500 index (10 years, no dividends, next close for next open).

Part A (option A): regime_switch labels a bar high_vol (and stays out) when recent volatility is
in its top 10% (a fixed rule). Here that one rule is replaced by a 2-state Markov model
(autobot.markov): high_vol when the FILTERED probability of the turbulent state is above 0.5;
trend vs range stays the ADX rule, the 3-bar confirmation stays. The model is refitted every 5
sessions on the previous 60 sessions of within-session 5-minute returns. Same data, costs and
replay as the trade-rules study; pairs: regime_switch at its defaults x the cached universe
symbols, plus the enabled live regime_switch jobs. Also checked: does each detector's high_vol
label come before larger moves in the next hour (what a turbulence detector is for)?
"""
from __future__ import annotations

import json
import math
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from . import cost_study as cs
from . import markov
from . import recalibrate as rc
from .meta import FLAT, REGIME_DEFAULTS, _pick, candidate_signals, classify_regimes
from .short_tp_study import cached_symbols
from .strategies import get_signal

ROOT = Path(__file__).resolve().parent.parent
OUT = {"a": ROOT / "data" / "markov_study_a.json", "b": ROOT / "data" / "markov_study_b.json"}
THRESHOLD = 0.5


def markov_regimes(df: pd.DataFrame, p: Dict, prob: pd.Series) -> pd.Series:
    """regime_switch's labels with the volatility rule replaced by P(turbulent) > THRESHOLD."""
    raw = classify_regimes(df, p["trend_measure"], p["adx_period"], p["adx_trend"], p["er_period"], p["er_trend"],
                           p["vr_period"], p["vr_trend"], p["vol_period"], p["vol_lookback"], 2.0, 1)   # 2.0: never high_vol
    lab = np.where(prob.to_numpy() > THRESHOLD, "high_vol", raw.to_numpy()).astype(object)
    lab = np.where(prob.isna().to_numpy() | (raw == "warmup").to_numpy(), "warmup", lab)
    raw = pd.Series(lab, index=df.index)
    n = int(p["confirm_bars"])
    if n <= 1:
        return raw
    codes = pd.Series(pd.factorize(raw)[0], index=df.index, dtype=float)
    win = codes.rolling(n)
    steady = (win.min() == win.max()) | (raw == "warmup")
    return raw.where(steady).ffill().fillna("warmup")


def regime_signal(df: pd.DataFrame, params: Dict, regime: pd.Series) -> pd.Series:
    p = {**REGIME_DEFAULTS, **params}
    mapping = {"trend": p["trend_strategy"], "range": p["range_strategy"], "high_vol": p["high_vol_strategy"]}
    sigs = candidate_signals(df, list(mapping.values()), p.get("candidate_params"), False)
    return _pick(sigs, regime.map(lambda r: mapping.get(r, FLAT)))


def next_hour_move(df: pd.DataFrame, bars: int = 12) -> pd.Series:
    """Mean absolute 5-minute return over the NEXT `bars` bars of the same session (an outcome,
    used only to score the labels, never to make them)."""
    r = markov.session_returns(df).abs()
    return r[::-1].rolling(bars, min_periods=bars // 2).mean()[::-1].shift(-1)


def _a_symbol(sym: str, closes, as_of, cfg: Dict) -> Dict:
    data = cs.Data([sym], closes=closes, as_of=as_of)
    r = rc.settings(cfg)
    fm = int(r["flatten_min"]) or 5
    costs = rc.costs_for(sym, r)
    df = data.bars(sym, "5Min")
    prob = markov.turbulence(df, train_sessions=60, refit_every=5)
    start = prob.first_valid_index()
    d0 = max(data.start(sym, int(r["warmup_days"])), start.tz_convert(cs.NY).date())
    pairs = [("regime_switch defaults", {})]
    pairs += [(f"live job {j['name']}", j.get("params") or {}) for j in cfg["live"].get("jobs") or []
              if j.get("enabled", True) and j["strategy"] == "regime_switch" and j["symbols"][0] == sym]
    rows = []
    for kind, params in pairs:
        p = {**REGIME_DEFAULTS, **params}
        rule = get_signal("regime_switch", df, params, False)
        mk = regime_signal(df, params, markov_regimes(df, p, prob))
        row = {"symbol": sym, "kind": kind, "daily": {}, "trades": {}, "net_%": {}}
        for v, sig in (("rule", rule), ("markov", mk)):
            f = cs._period(cs.sim(df, data.cal[sym], "regime_switch", params, costs, "5Min", fm, signal=sig)["frame"], d0)
            row["daily"][v] = {str(k): float(x) for k, x in cs._daily(f.ret).items()}
            row["trades"][v] = int(len(cs.round_trips(f)))
            row["net_%"][v] = round(float(((1 + f.ret).prod() - 1) * 100), 2)
        rows.append(row)
    # does each detector's high_vol label come before larger moves?
    p = dict(REGIME_DEFAULTS)
    lab_rule = classify_regimes(df, p["trend_measure"], p["adx_period"], p["adx_trend"], p["er_period"], p["er_trend"],
                                p["vr_period"], p["vr_trend"], p["vol_period"], p["vol_lookback"], p["high_vol_pct"], p["confirm_bars"])
    lab_mk = markov_regimes(df, p, prob)
    fut = next_hour_move(df) * 1e4
    keep = np.asarray(df.index.tz_convert(cs.NY).date) >= d0
    diag = {}
    for name, lab in (("rule", lab_rule), ("markov", lab_mk)):
        hv = (lab == "high_vol") & keep
        calm = lab.isin(["trend", "range"]) & keep
        diag[name] = {"high_vol_share_%": round(float(hv.sum() / max(1, (hv | calm).sum()) * 100), 1),
                      "next_hour_bps_when_high_vol": round(float(fut[hv].mean()), 2),
                      "next_hour_bps_otherwise": round(float(fut[calm].mean()), 2)}
        diag[name]["ratio"] = round(diag[name]["next_hour_bps_when_high_vol"] / max(1e-9, diag[name]["next_hour_bps_otherwise"]), 2)
    return {"symbol": sym, "rows": rows, "labels": diag}


def _paired(a: pd.Series, b: pd.Series) -> Dict:
    d = (a - b).dropna()
    n = len(d)
    mu, se = d.mean(), d.std(ddof=1) / math.sqrt(n)
    return {"mean_bps": round(mu * 1e4, 2), "ci95_bps": [round((mu - 1.96 * se) * 1e4, 2), round((mu + 1.96 * se) * 1e4, 2)],
            "significant": bool(mu - 1.96 * se > 0 or mu + 1.96 * se < 0)}


def summarize_a(parts: List[Dict]) -> Dict:
    rows = [r for p in parts for r in p["rows"]]
    res = {}
    for group, sel in (("all pairs", rows), ("live jobs", [r for r in rows if r["kind"].startswith("live")])):
        if not sel:
            continue
        acc = {v: pd.concat([pd.Series(r["daily"][v]) for r in sel], axis=1).fillna(0).mean(axis=1) for v in ("rule", "markov")}
        per = [_paired(pd.Series(r["daily"]["markov"]), pd.Series(r["daily"]["rule"])) for r in sel]
        res[group] = {"pairs": len(sel), "days": len(acc["rule"]),
                      "rule_net_%": round(float(((1 + acc["rule"]).prod() - 1) * 100), 2),
                      "markov_net_%": round(float(((1 + acc["markov"]).prod() - 1) * 100), 2),
                      "rule_trades": int(sum(r["trades"]["rule"] for r in sel)),
                      "markov_trades": int(sum(r["trades"]["markov"] for r in sel)),
                      "markov_vs_rule": _paired(acc["markov"], acc["rule"]),
                      "pairs_better": int(sum(x["significant"] and x["mean_bps"] > 0 for x in per)),
                      "pairs_worse": int(sum(x["significant"] and x["mean_bps"] < 0 for x in per)),
                      "per_pair": [{"symbol": r["symbol"], "kind": r["kind"], "rule_%": r["net_%"]["rule"], "markov_%": r["net_%"]["markov"]} for r in sel]}
    lab = [p["labels"] for p in parts]
    res["labels"] = {m: {k: round(float(np.mean([x[m][k] for x in lab])), 2) for k in lab[0][m]} for m in ("rule", "markov")}
    return res


def run_a(cfg: Dict, workers: int = 2) -> Dict:
    syms = cached_symbols()
    data = cs.Data(syms)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        parts = list(ex.map(_a_symbol, syms, [data.closes] * len(syms), [data.as_of] * len(syms), [cfg] * len(syms)))
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": str(data.as_of), "symbols": syms,
           "threshold": THRESHOLD, "summary": summarize_a(parts)}
    OUT["a"].write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


# ----------------------------------------------------------------- part B: daily risk overlay
TRAIN_B, REFIT_B = 500, 21


def b_targets(df: pd.DataFrame) -> Dict[str, pd.Series]:
    from .daily_study import candidates
    prob = markov.turbulence(df, train_sessions=TRAIN_B, refit_every=REFIT_B, intraday=False)
    raw = (1 - prob).clip(0, 1)
    v = raw.to_numpy()
    scaled = np.full(len(v), np.nan)
    for i in range(len(v)):
        if np.isnan(v[i]):
            continue
        prev = scaled[i - 1] if i and not np.isnan(scaled[i - 1]) else None
        scaled[i] = v[i] if prev is None or abs(v[i] - prev) >= 0.1 else prev
    out = {"buy and hold": pd.Series(1.0, index=df.index),
           "vol target": candidates(df)["vol target (on hold)"],
           "markov scaled": pd.Series(scaled, index=df.index),
           "markov on/off": (prob <= 0.5).astype(float).where(prob.notna())}
    out["_start"] = prob.first_valid_index()
    out["_prob"] = prob
    return out


def _b_one(name: str, df: pd.DataFrame, cost_side: float) -> Dict:
    from .daily_study import simulate
    t = b_targets(df)
    start = t.pop("_start")
    prob = t.pop("_prob")
    frames = {k: simulate(df, v.fillna(0.0), cost_side) for k, v in t.items()}
    frames = {k: f[f.index > start] for k, f in frames.items()}
    return {"name": name, "frames": frames, "turbulent_share_%": round(float((prob[prob.index > start] > 0.5).mean() * 100), 1)}


def _b_stats(r: pd.Series, w: pd.Series, u: pd.Series) -> Dict:
    from .daily_study import by_year, stats
    years = len(r) / 252
    return {**stats(r, w), "trades_per_year": round(float((u > 0).sum() / 2 / max(years, 1e-9)), 1), "by_year": by_year(r)}


def run_b(cfg: Dict) -> Dict:
    from . import core
    from .daily_study import load
    syms = cs.universe_symbols()
    r = rc.settings(cfg)
    singles = []
    for s_ in syms:
        try:
            df = load(s_)
        except Exception:  # noqa: BLE001
            continue
        if len(df) > TRAIN_B + 100:
            singles.append(_b_one(s_, df, rc.costs_for(s_, r).per_turn))
    variants = list(singles[0]["frames"])
    port = {}
    start = max(min(f.index[0] for f in x["frames"].values()) for x in singles)
    for v in variants:
        R = pd.DataFrame({x["name"]: x["frames"][v].ret for x in singles}).sort_index()
        R = R[R.index >= start].fillna(0.0)
        W = pd.DataFrame({x["name"]: x["frames"][v].position for x in singles}).reindex(R.index).fillna(0.0)
        U = pd.DataFrame({x["name"]: x["frames"][v].turnover for x in singles}).reindex(R.index).fillna(0.0)
        port[v] = (R.mean(axis=1), W.mean(axis=1), U)
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "train_sessions": TRAIN_B, "refit_every": REFIT_B,
           "portfolio": {"symbols": [x["name"] for x in singles], "from": str(start.date()), "results": {}}, "single": {}}
    bh = port["buy and hold"][0]
    vt = port["vol target"][0]
    for v, (ret, w, u) in port.items():
        row = {**_b_stats(ret, w, u.sum(axis=1) / max(1, u.shape[1]))}
        if v != "buy and hold":
            row["vs_buy_and_hold"] = _paired(ret, bh)
        if v.startswith("markov"):
            row["vs_vol_target"] = _paired(ret, vt)
        res["portfolio"]["results"][v] = row
    extra = []
    try:
        voo = load("VOO")
        extra.append(_b_one("VOO", voo, 0.0002))
    except Exception:  # noqa: BLE001
        pass
    try:
        extra.append(_b_one("S&P 500 (FRED)", core.fred_sp500(), 0.0002))
    except Exception:  # noqa: BLE001
        pass
    for x in extra:
        fr = x["frames"]
        res["single"][x["name"]] = {"from": str(fr["buy and hold"].index[0].date()), "turbulent_share_%": x["turbulent_share_%"],
                                    **{v: {**_b_stats(f.ret, f.position, f.turnover),
                                           **({"vs_buy_and_hold": _paired(f.ret, fr["buy and hold"].ret)} if v != "buy and hold" else {})}
                                       for v, f in fr.items()}}
    OUT["b"].write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def main():
    import argparse
    import sys
    import yaml
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Markov switching study (diagnosis only).")
    ap.add_argument("--part", choices=["a", "b"], required=True)
    a = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    if a.part == "a":
        res = run_a(cfg)
        print(json.dumps({k: v for k, v in res["summary"].items()}, indent=1, default=str))
    else:
        res = run_b(cfg)
        print(json.dumps(res, indent=1, default=str))
    print("saved", OUT[a.part])


if __name__ == "__main__":
    main()
