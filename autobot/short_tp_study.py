"""
Shorts and take-profit study: do short trades win, and when? Do a take-profit and / or a
stop-loss (the exit layer's take_profit_pct / stop_pct) help? Diagnosis only: nothing here
changes config.yaml, the bot or recalibration.

    python -m autobot.short_tp_study              # writes data/short_tp_study.json (~20 min, 2 workers)
    python -m autobot.short_tp_study --summary    # only reprint the saved summary

Same data and replay as the cost study (autobot.cost_study): 2 years of regular-hours IEX 5-minute
bars per universe stock/ETF, resampled to 15Min / 1Hour / 1Day, each asset class's costs, intraday
positions closed 5 minutes before the close like the bot (fills at the open and in the last bar
cost 2x), every strategy at its default settings, recalibration's warm-up skipped.

Part 1  shorts    every strategy run with shorts allowed; each round trip is labelled long or short
                  and by the market at entry: SPY's previous close above or below its 50-day
                  average ("market up / down"), and the same for the symbol itself. Per trade, in
                  bps: gross, cost, net, with 95% ranges clustered by symbol-day (trades on the
                  same day move together). "Clearly positive" = the whole range above zero.
Part 2  exits     on 5Min and 1Hour, long only and long + short: every take-profit x stop-loss
                  setting (and none) for each strategy x symbol. In-sample: each setting all the
                  time. Walk-forward: in each window the setting that made the most on the 60
                  TRAIN days is used on the next 20 TEST days, so its result is out of sample;
                  compared with "none" on the same days (95% range of the daily difference).
After an exit the strategy must go out and back in before a new entry (re-entry fresh_cross);
the late-entry rule is off so only the take-profit / stop act. Shorts: Alpaca paper charges no
borrow fee for easy-to-borrow stocks held intraday; a real account may.
"""
from __future__ import annotations

import json
import math
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from . import cost_study as cs
from . import exits
from . import overnight_study as ov
from . import recalibrate as rc
from .strategies import STRATEGIES, get_signal

NY = "America/New_York"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "short_tp_study.json"
SHORT_TFS = ["5Min", "15Min", "1Hour", "1Day"]
EXIT_TFS = ["5Min", "1Hour"]
GRID = {"5Min": {"tp": [0.25, 0.5, 1.0, 2.0], "stop": [0.25, 0.5, 1.0]},
        "1Hour": {"tp": [0.5, 1.0, 2.0, 4.0], "stop": [0.5, 1.0, 2.0]}}
TRAIN, TEST = 60, 20       # walk-forward windows, in sessions
TREND_DAYS = 50            # "market up": previous close above its 50-day average


def cached_symbols() -> List[str]:
    """Universe stocks/ETFs that already have the 2-year 5-minute download (no new downloads)."""
    from .data import CACHE_DIR
    return [s for s in cs.universe_symbols() if list(CACHE_DIR.glob(f"{s}_5Min_{ov.LONG_DAYS}d_*.csv.gz"))]


def combos(tf: str) -> List[Optional[Dict]]:
    g = GRID[tf]
    out = []
    for tp in [None] + g["tp"]:
        for sl in [None] + g["stop"]:
            out.append(None if tp is None and sl is None else
                       {k: v for k, v in (("take_profit_pct", tp), ("stop_pct", sl)) if v is not None})
    return out


def label(c: Optional[Dict]) -> str:
    if not c:
        return "none"
    return " · ".join(x for x in (f"take profit {c['take_profit_pct']:g}%" if "take_profit_pct" in c else "",
                                  f"stop {c['stop_pct']:g}%" if "stop_pct" in c else "") if x)


def trend_up(df5: pd.DataFrame) -> Dict[date, bool]:
    """{session: the PREVIOUS session's close is above its TREND_DAYS-session average} (causal)."""
    d = cs.resample(df5, "1Day").close
    up = (d > d.rolling(TREND_DAYS).mean()).shift(1)
    return {ts.tz_convert(NY).date(): bool(v) for ts, v in up.items() if not pd.isna(v)}


def clustered(x: np.ndarray, cluster: np.ndarray) -> Dict:
    m = cs.clustered_mean(x, cluster)
    r = lambda v: None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, 2)  # noqa: E731
    return {"mean": r(m["mean"]), "lo": r(m["lo"]), "hi": r(m["hi"])}


def trip_stats(t: pd.DataFrame) -> Dict:
    """Per round trip, in bps, with 95% ranges clustered by symbol-day."""
    if t.empty:
        return {"trades": 0}
    key = (t.symbol + "|" + t.day.astype(str)).to_numpy()
    net = clustered(((t.gross - t.cost) * 1e4).to_numpy(), key)
    return {"trades": int(len(t)), "gross_bps": clustered((t.gross * 1e4).to_numpy(), key),
            "cost_bps": round(float(t.cost.mean() * 1e4), 2), "net_bps": net,
            "win_rate_%": round(float(((t.gross - t.cost) > 0).mean() * 100), 1),
            "clearly_positive": bool(net["lo"] is not None and net["lo"] > 0),
            "clearly_negative": bool(net["hi"] is not None and net["hi"] < 0)}


# ----------------------------------------------------------------- per symbol (worker)
def _symbol(sym: str, closes, as_of: date, market_up: Dict[date, bool], cfg: Dict) -> Dict:
    data = cs.Data([sym], closes=closes, as_of=as_of)
    r = rc.settings(cfg)
    fm = int(r["flatten_min"]) or 5
    costs = rc.costs_for(sym, r)
    own_up = trend_up(data.df5[sym])
    trips, walk = [], []
    # part 1: shorts allowed, every round trip labelled
    for tf in SHORT_TFS:
        df = data.bars(sym, tf)
        d0 = data.start(sym, int(r["warmup_days"]) if tf != "1Day" else 230)
        for name in cs.strategies_for(tf, r):
            try:
                sig = get_signal(name, df, STRATEGIES[name].defaults, True)
                if not (sig < 0).any():
                    continue  # a long-only design: no short side to measure
                f = cs._period(cs.sim(df, data.cal[sym], name, {}, costs, tf, fm, signal=sig)["frame"], d0)
            except Exception:  # noqa: BLE001  a strategy that can't run on these bars is skipped, like the cost study
                continue
            tr = cs.round_trips(f)
            if tr.empty:
                continue
            pos = f.position.to_numpy()
            for row in tr.itertuples():
                trips.append((sym, tf, name, STRATEGIES[name].family, "short" if pos[row.start] < 0 else "long", str(row.day),
                              row.gross, row.cost, market_up.get(row.day), own_up.get(row.day)))
    # part 2: take-profit x stop, fixed and walk-forward
    for tf in EXIT_TFS:
        df = data.bars(sym, tf)
        d0 = data.start(sym, int(r["warmup_days"]))
        grid = combos(tf)
        for name in cs.strategies_for(tf, r):
            for side in ("long", "long_short"):
                try:
                    base = get_signal(name, df, STRATEGIES[name].defaults, side == "long_short")
                except Exception:  # noqa: BLE001
                    continue
                daily, full, trades = {}, {}, {}
                for c in grid:
                    sig = base if c is None else exits.apply_exit_layer(
                        base, df, {**c, "reentry": "fresh_cross", "no_entry_last_min": 0}, crypto=False)
                    f = cs._period(cs.sim(df, data.cal[sym], name, {}, costs, tf, fm, signal=sig)["frame"], d0)
                    lab = label(c)
                    daily[lab] = cs._daily(f.ret)
                    full[lab] = round(float(((1 + f.ret).prod() - 1) * 100), 3)
                    trades[lab] = int(len(cs.round_trips(f)))
                D = pd.DataFrame(daily).fillna(0.0)
                wf, base_t, chosen = [], [], []
                for a in range(TRAIN, len(D) - 1, TEST):
                    tr_, te = D.iloc[a - TRAIN:a], D.iloc[a:a + TEST]
                    best = ((1 + tr_).prod() - 1).idxmax()
                    chosen.append(best)
                    wf.append(te[best])
                    base_t.append(te["none"])
                if not wf:
                    continue
                wf_s, none_s = pd.concat(wf), pd.concat(base_t)
                walk.append({"symbol": sym, "timeframe": tf, "strategy": name, "family": STRATEGIES[name].family, "side": side,
                             "full_return_%": full, "trades": trades,
                             "test_days": [str(d) for d in wf_s.index], "wf": wf_s.round(8).tolist(), "none": none_s.round(8).tolist(),
                             "fixed_test": {k: D.loc[wf_s.index, k].round(8).tolist() for k in D.columns},
                             "chosen": chosen})
    return {"symbol": sym, "trips": trips, "walk": walk}


# ----------------------------------------------------------------- aggregation
def shorts_summary(trips: List[tuple]) -> Dict:
    t = pd.DataFrame(trips, columns=["symbol", "timeframe", "strategy", "family", "side", "day", "gross", "cost", "market_up", "own_up"])
    out = {}
    for tf in SHORT_TFS:
        x = t[t.timeframe == tf]
        s, lg = x[x.side == "short"], x[x.side == "long"]
        res = {"short": trip_stats(s), "long": trip_stats(lg), "by_market": {}, "by_own_trend": {}, "by_family": {}, "by_strategy": []}
        for lab, v in (("market up", True), ("market down", False)):
            res["by_market"][lab] = {"short": trip_stats(s[s.market_up == v]), "long": trip_stats(lg[lg.market_up == v])}
            res["by_own_trend"][lab.replace("market", "symbol")] = {"short": trip_stats(s[s.own_up == v]), "long": trip_stats(lg[lg.own_up == v])}
        for fam, g in s.groupby("family"):
            res["by_family"][fam] = {"short": trip_stats(g), "short_market_down": trip_stats(g[g.market_up == False])}  # noqa: E712
        for name, g in s.groupby("strategy"):
            res["by_strategy"].append({"strategy": name, **trip_stats(g), "market_down": trip_stats(g[g.market_up == False])})  # noqa: E712
        res["by_strategy"].sort(key=lambda r: -(r["net_bps"]["mean"] or -1e9))
        out[tf] = res
    return out


def _paired(a: pd.Series, b: pd.Series) -> Dict:
    d = (a - b).dropna()
    n = len(d)
    if n < 2:
        return {"days": n, "mean_bps": None, "ci95_bps": [None, None], "significant": False}
    mu, se = d.mean(), d.std(ddof=1) / math.sqrt(n)
    return {"days": n, "mean_bps": round(mu * 1e4, 2), "ci95_bps": [round((mu - 1.96 * se) * 1e4, 2), round((mu + 1.96 * se) * 1e4, 2)],
            "significant": bool(mu - 1.96 * se > 0 or mu + 1.96 * se < 0)}


def _account(rows: List[Dict], key: str, sub: Optional[str] = None) -> pd.Series:
    """Equal-weight account over strategy x symbol pairs, daily, on their test days."""
    cols = [pd.Series(r[key] if sub is None else r[key][sub], index=pd.to_datetime(r["test_days"])) for r in rows]
    return pd.concat(cols, axis=1).mean(axis=1).sort_index() if cols else pd.Series(dtype=float)


def exits_summary(walk: List[Dict]) -> Dict:
    out = {}
    for tf in EXIT_TFS:
        for side in ("long", "long_short"):
            rows = [w for w in walk if w["timeframe"] == tf and w["side"] == side]
            if not rows:
                continue
            labels = list(rows[0]["full_return_%"])
            none_a, wf_a = _account(rows, "none"), _account(rows, "wf")
            fixed = []
            for lab in labels:
                acc = _account(rows, "fixed_test", lab)
                fixed.append({"setting": lab, "avg_full_return_%": round(float(np.mean([w["full_return_%"][lab] for w in rows])), 2),
                              "test_return_%": round(float(((1 + acc).prod() - 1) * 100), 2),
                              "vs_none": _paired(acc, none_a),
                              "trades": int(sum(w["trades"][lab] for w in rows))})
            fixed.sort(key=lambda f: -f["avg_full_return_%"])
            pair_beats = [_paired(pd.Series(w["wf"]), pd.Series(w["none"])) for w in rows]
            ch = pd.Series([c for w in rows for c in w["chosen"]]).value_counts()
            by_family = {}
            for fam in sorted({w["family"] for w in rows}):
                fr = [w for w in rows if w["family"] == fam]
                by_family[fam] = {"pairs": len(fr), "wf_vs_none": _paired(_account(fr, "wf"), _account(fr, "none")),
                                  "none_test_return_%": round(float(((1 + _account(fr, "none")).prod() - 1) * 100), 2),
                                  "wf_test_return_%": round(float(((1 + _account(fr, "wf")).prod() - 1) * 100), 2)}
            out[f"{tf} {side}"] = {
                "pairs": len(rows), "test_days": len(none_a),
                "none_test_return_%": round(float(((1 + none_a).prod() - 1) * 100), 2),
                "wf_test_return_%": round(float(((1 + wf_a).prod() - 1) * 100), 2),
                "wf_vs_none": _paired(wf_a, none_a),
                "pairs_wf_clearly_better": sum(p["significant"] and p["mean_bps"] > 0 for p in pair_beats),
                "pairs_wf_clearly_worse": sum(p["significant"] and p["mean_bps"] < 0 for p in pair_beats),
                "expected_by_luck": round(0.025 * len(rows), 1),
                "chosen_most": [{"setting": k, "share_%": round(v / ch.sum() * 100, 1)} for k, v in ch.head(5).items()],
                "fixed": fixed, "by_family": by_family}
    for tf in EXIT_TFS:  # do shorts help as a whole? long + short vs long only, strategy alone, same test days
        lo = [w for w in walk if w["timeframe"] == tf and w["side"] == "long"]
        ls = [w for w in walk if w["timeframe"] == tf and w["side"] == "long_short"]
        if lo and ls:
            a, b = _account(ls, "none"), _account(lo, "none")
            out[f"{tf} shorts added"] = {"long_short_test_return_%": round(float(((1 + a).prod() - 1) * 100), 2),
                                         "long_test_return_%": round(float(((1 + b).prod() - 1) * 100), 2),
                                         "difference": _paired(a, b.reindex(a.index).fillna(0))}
    return out


def _r(s: Dict) -> str:
    n = s.get("net_bps") or {}
    if not s.get("trades"):
        return "no trades"
    rng = f" ({n['lo']:+.1f} to {n['hi']:+.1f})" if n.get("lo") is not None else ""
    return f"{n['mean']:+.1f} bps per trade after costs{rng}, {s['trades']} trades, {s['win_rate_%']:.0f}% winners"


def summary(res: Dict) -> List[str]:
    """Plain-English lines for the checkpoint."""
    L = [f"Data: {len(res['symbols'])} stocks/ETFs, 2 years to {res['as_of']}, default settings, after costs, flat before the close."]
    for tf, v in res["shorts"].items():
        L.append(f"{tf} shorts: {_r(v['short'])}; longs: {_r(v['long'])}.")
        for lab, m in v["by_market"].items():
            L.append(f"   {lab}: shorts {_r(m['short'])}")
        pos = [s["strategy"] for s in v["by_strategy"] if s.get("clearly_positive")]
        L.append(f"   strategies whose shorts are clearly positive: {', '.join(pos) if pos else 'none'}")
    for k, v in res["exits"].items():
        if k.endswith("shorts added"):
            d = v["difference"]
            L.append(f"{k}: long+short {v['long_short_test_return_%']:+.2f}% vs long only {v['long_test_return_%']:+.2f}% "
                     f"(daily difference {d['mean_bps']:+.2f} bps, {d['ci95_bps'][0]:+.2f} to {d['ci95_bps'][1]:+.2f})")
            continue
        d = v["wf_vs_none"]
        L.append(f"{k} take-profit/stop, walk-forward: {v['wf_test_return_%']:+.2f}% vs strategy alone {v['none_test_return_%']:+.2f}% on "
                 f"{v['test_days']} test days (daily difference {d['mean_bps']:+.2f} bps, {d['ci95_bps'][0]:+.2f} to {d['ci95_bps'][1]:+.2f}"
                 f"{', clearly' if d['significant'] else ', not clearly'} different); pairs clearly better {v['pairs_wf_clearly_better']}"
                 f", worse {v['pairs_wf_clearly_worse']} (luck alone ~{v['expected_by_luck']})")
        best = v["fixed"][0]
        L.append(f"   best fixed setting in hindsight: {best['setting']} ({best['avg_full_return_%']:+.2f}% average per pair; none: "
                 f"{next(f['avg_full_return_%'] for f in v['fixed'] if f['setting'] == 'none'):+.2f}%)")
    return L


def run(cfg: Dict, symbols: Optional[Sequence[str]] = None, workers: int = 2, progress=print) -> Dict:
    syms = list(symbols or cached_symbols())
    data = cs.Data(syms)  # the common last full day and the session calendar, once
    mk = trend_up(data.df5["SPY"]) if "SPY" in data.df5 else trend_up(cs.Data(["SPY"], closes=data.closes, as_of=data.as_of).df5["SPY"])
    parts = []
    if workers <= 1:  # in this process (tests, quick checks)
        for k, sym in enumerate(syms):
            parts.append(_symbol(sym, data.closes, data.as_of, mk, cfg))
            progress(f"{k + 1}/{len(syms)} {sym} done")
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_symbol, s, data.closes, data.as_of, mk, cfg): s for s in syms}
            for k, f in enumerate(futs):
                parts.append(f.result())
                progress(f"{k + 1}/{len(syms)} {futs[f]} done")
    trips = [t for p in parts for t in p["trips"]]
    walk = [w for p in parts for w in p["walk"]]
    res = {"as_of": str(data.as_of), "symbols": syms, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "settings": {"grid": GRID, "train_sessions": TRAIN, "test_sessions": TEST, "trend_days": TREND_DAYS,
                        "reentry": "fresh_cross"},
           "shorts": shorts_summary(trips), "exits": exits_summary(walk)}
    res["summary"] = summary(res)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def main():
    import argparse
    import sys
    import yaml
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Shorts and take-profit study (diagnosis only).")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    if a.summary:
        print("\n".join(json.loads(OUT.read_text(encoding="utf-8"))["summary"]))
        return
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    res = run(cfg, [s for s in a.symbols.split(",") if s] or None, a.workers)
    print("\n".join(res["summary"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
