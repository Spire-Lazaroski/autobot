"""
News-guard study (Phase 5 step 5 part 2): does wrapping a strategy in `news_guard` help?

    python -m autobot.news_guard_study          # compute and save data/news_guard_study.json

On 5-minute RTH bars over the news history (about two years), for each base strategy
(the live jobs' settings) and symbol, the base alone is compared with the guard's rules
one at a time and together, in both modes ("no_new": keep or close but don't open while
blocked; "flat": no position while blocked). Costs are the backtest section's (2 bps).

Stocks and ETFs are reported separately: the keyword rules mark thousands of SPY/QQQ
stories as material (macro news names the index), so the ETFs use materiality >= 4.

Earnings blackout dates: the published release dates below (the session that reacts:
results after the close move the NEXT session). The dates the news detector finds are
tested too, because that is what the bot would rely on, and compared with the published
ones. Dates marked "news only" are newer than the published list we checked and
come from the detector.

Walk-forward: the guard's settings (including "off") are re-chosen every 20 sessions from
the previous 60 and compared out of sample with the base strategy alone.

Each guarded result is also compared day by day with its base: mean daily difference with a
95% confidence interval, so a small edge can be told apart from noise.
"""
from __future__ import annotations

import contextlib
import json
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from . import news_guard as ng
from .backtest import CostModel, metrics, run_backtest, walk_forward
from .data import fetch_bars
from .strategies import get_signal

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "news_guard_study.json"
DAYS = 740
TIMEFRAME = "5Min"
STOCKS = ["AAPL", "NVDA", "TSLA"]
ETFS = ["SPY", "QQQ"]
MATERIALITY = {"stocks": 3, "etfs": 4}
BASES = {  # the live jobs' settings
    "ema_cross": {"fast": 9, "slow": 21},
    "rsi_reversion": {"period": 7, "oversold": 20, "exit_level": 50},
}
STAND_ASIDE_MIN = 30
SESSION_BARS = 78  # 5-minute bars in a regular session
WF_TRAIN, WF_TEST = 60 * SESSION_BARS, 20 * SESSION_BARS

# The session that reacts to each earnings release, from the companies' published dates.
PUBLISHED_EARNINGS: Dict[str, List[str]] = {
    "AAPL": ["2024-11-01", "2025-01-31", "2025-05-02", "2025-08-01", "2025-10-31", "2026-01-30", "2026-05-01"],
    "NVDA": ["2024-11-21", "2025-02-27", "2025-05-29", "2025-08-28", "2025-11-20", "2026-02-26", "2026-05-21"],
    "TSLA": ["2024-10-24", "2025-01-30", "2025-04-23", "2025-07-24", "2025-10-23", "2026-01-29", "2026-04-23"],
}
PUBLISHED_UNTIL = date(2026, 6, 1)  # after this, the detector's dates are used ("news only")

VARIANTS = {  # name: guard settings (stand-aside minutes, blackout before/after, mode)
    "aside_no_new": dict(stand_aside_min=STAND_ASIDE_MIN, blackout_before=0, blackout_after=0, mode="no_new"),
    "aside_flat": dict(stand_aside_min=STAND_ASIDE_MIN, blackout_before=0, blackout_after=0, mode="flat"),
    "blackout_no_new": dict(stand_aside_min=0, blackout_before=1, blackout_after=1, mode="no_new"),
    "blackout_flat": dict(stand_aside_min=0, blackout_before=1, blackout_after=1, mode="flat"),
    "both_no_new": dict(stand_aside_min=STAND_ASIDE_MIN, blackout_before=1, blackout_after=1, mode="no_new"),
    "both_flat": dict(stand_aside_min=STAND_ASIDE_MIN, blackout_before=1, blackout_after=1, mode="flat"),
}
WF_GRID = {"stand_aside_min": [0, 30, 60, 120], "blackout_before": [0, 1], "blackout_after": [0, 1],
           "mode": ["no_new", "flat"]}


# ----------------------------------------------------------------- earnings dates
def study_earnings(symbol: str, detected: List[date]) -> List[date]:
    """Published dates, plus the detector's dates after the published list ends."""
    pub = [date.fromisoformat(d) for d in PUBLISHED_EARNINGS.get(symbol, [])]
    return sorted(set(pub) | {d for d in detected if d >= PUBLISHED_UNTIL})


@contextlib.contextmanager
def earnings_source(source: str):
    """Within the block, news_guard uses 'published' (study_earnings) or 'detected' dates."""
    original = ng.earnings_sessions
    if source == "published":
        ng.earnings_sessions = lambda s: study_earnings(s, original(s))
    try:
        yield
    finally:
        ng.earnings_sessions = original


def detector_check(symbols: List[str]) -> Dict[str, Dict]:
    """How the news-based detector's dates compare with the published ones (where we have them)."""
    out = {}
    for s in symbols:
        pub = {date.fromisoformat(d) for d in PUBLISHED_EARNINGS.get(s, [])}
        if not pub:
            continue
        det = {d for d in ng.earnings_sessions(s) if d < PUBLISHED_UNTIL}
        lo = min(pub) - timedelta(days=30)
        det_in = {d for d in det if d >= lo}  # ignore dates before the news history
        exact = pub & det_in
        near = {p for p in pub - exact if any(0 < abs((p - d).days) <= 4 for d in det_in)}
        missed = pub - exact - near
        false = {d for d in det_in - exact if not any(abs((p - d).days) <= 4 for p in pub)}
        out[s] = {"published": len(pub), "exact": len(exact), "off_by_days": sorted(str(p) for p in near),
                  "missed": sorted(str(p) for p in missed), "false": sorted(str(d) for d in false),
                  "news_only": [str(d) for d in study_earnings(s, ng.earnings_sessions(s)) if d >= PUBLISHED_UNTIL]}
    return out


# ----------------------------------------------------------------- helpers
def _daily(bt: pd.DataFrame) -> pd.Series:
    day = bt.index.tz_convert("America/New_York").normalize()
    return (1 + bt.ret).groupby(day).prod() - 1


def paired_diff(a: pd.Series, b: pd.Series) -> Dict:
    """Mean daily return difference a - b (in basis points) with a 95% CI and the share of
    days it was positive. Days where both are identical still count (the guard did nothing)."""
    d = (a - b.reindex(a.index).fillna(0)).dropna()
    n = len(d)
    if n < 2:
        return {"days": n, "mean_bps": 0.0, "ci95_bps": [0.0, 0.0], "significant": False}
    m, se = d.mean(), d.std(ddof=1) / math.sqrt(n)
    ci = [m - 1.96 * se, m + 1.96 * se]
    return {"days": n, "mean_bps": round(m * 1e4, 2), "ci95_bps": [round(ci[0] * 1e4, 2), round(ci[1] * 1e4, 2)],
            "days_different": int((d.abs() > 1e-12).sum()), "significant": bool(ci[0] > 0 or ci[1] < 0)}


def _portfolio(rets: List[pd.Series]) -> pd.DataFrame:
    port = pd.concat(rets, axis=1, sort=True).fillna(0).mean(axis=1)
    return pd.DataFrame({"ret": port, "equity": (1 + port).cumprod(), "position": 1}, index=port.index)


PORT_KEYS = ["total_return_%", "cagr_%", "sharpe", "max_drawdown_%"]


def blocked_share(data: Dict[str, pd.DataFrame], materiality: int) -> Dict[str, Dict]:
    """Share of bars each rule blocks, per symbol."""
    out = {}
    for s, df in data.items():
        f = ng.guard_flags(df, s, STAND_ASIDE_MIN, materiality, 1, 1)
        out[s] = {"stand_aside_%": round(f.stand_aside.mean() * 100, 1),
                  "blackout_%": round(f.blackout.mean() * 100, 1),
                  "either_%": round((f.stand_aside | f.blackout).mean() * 100, 1)}
    return out


# ----------------------------------------------------------------- the study
def compare_group(data: Dict[str, pd.DataFrame], materiality: int, costs: CostModel,
                  earnings: str = "published") -> Dict:
    """Every base x variant on every symbol of the group: per-symbol metrics, an equal-weight
    portfolio per variant, and the paired daily difference against the base."""
    out = {}
    with earnings_source(earnings):
        for base, bp in BASES.items():
            rows, port_rets, daily = [], {}, {}
            for s, df in data.items():
                base_bt = run_backtest(df, get_signal(base, df, bp), costs)
                runs = {"base": base_bt}
                for v, g in VARIANTS.items():
                    p = {"base": base, "base_params": bp, "min_materiality": materiality, **g}
                    runs[v] = run_backtest(df, get_signal("news_guard", df, p), costs)
                for v, bt in runs.items():
                    m = metrics(bt)
                    rows.append({"symbol": s, "variant": v, **m,
                                 "bars_changed_%": round(float((bt.position != base_bt.position).mean() * 100), 2)})
                    port_rets.setdefault(v, []).append(bt.ret.rename(s))
            ports = {v: _portfolio(r) for v, r in port_rets.items()}
            for v, pbt in ports.items():
                daily[v] = _daily(pbt)
            summary = []
            for v, pbt in ports.items():
                m = metrics(pbt)
                summary.append({"variant": v, **{k: m[k] for k in PORT_KEYS},
                                "trades": int(sum(r["trades"] for r in rows if r["variant"] == v)),
                                "vs_base": None if v == "base" else paired_diff(daily[v], daily["base"])})
            out[base] = {"params": bp, "summary": summary, "per_symbol": rows}
    return out


def walk_forward_group(data: Dict[str, pd.DataFrame], materiality: int, costs: CostModel,
                       progress: Optional[Callable[[float, str], None]] = None) -> Dict:
    out = {}
    with earnings_source("published"):
        for i, (base, bp) in enumerate(BASES.items()):
            grid = {"base": [base], "base_params": [bp], "min_materiality": [materiality], **WF_GRID}
            off = {"base": base, "base_params": bp, "stand_aside_min": 0, "blackout_before": 0, "blackout_after": 0}
            sub = (lambda f, msg, i=i: progress((i + f) / len(BASES), f"{base}: {msg}")) if progress else None
            wf = walk_forward(data, "news_guard", WF_TRAIN, WF_TEST, costs=costs, grid=grid,
                              fixed_params=off, progress=sub)
            eq = wf["equity"]
            w = wf["windows"]
            guard_on = [bool(p["stand_aside_min"] or p["blackout_before"] or p["blackout_after"]) for p in w.params]
            out[base] = {
                "walk_forward": wf["metrics"]["walk_forward"],
                "base_alone": wf["metrics"]["fixed"],
                "buy_and_hold": wf["metrics"]["buy_and_hold"],
                "vs_base": paired_diff(_daily_from_equity(eq.walk_forward), _daily_from_equity(eq.fixed)),
                "windows": len(w), "windows_guard_on": int(sum(guard_on)),
                "choices": [{"test_start": str(r.test_start.tz_convert("America/New_York").date()),
                             **{k: r.params[k] for k in ("stand_aside_min", "blackout_before", "blackout_after", "mode")},
                             "test_return_%": r["test_return_%"]} for _, r in w.iterrows()],
                "oos_start": str(wf["oos_start"].tz_convert("America/New_York").date()),
            }
    return out


def _daily_from_equity(eq: pd.Series) -> pd.Series:
    r = eq.pct_change().fillna(eq.iloc[0] - 1)
    return _daily(pd.DataFrame({"ret": r}, index=eq.index))


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def run(progress: Optional[Callable[[float, str], None]] = None, walk: bool = True) -> Dict:
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["backtest"]
    costs = CostModel(cfg.get("fee_bps", 0.0), cfg.get("slippage_bps", 2.0))
    say = progress or (lambda f, m: None)
    say(0.02, "loading prices")
    all_data = fetch_bars(STOCKS + ETFS, TIMEFRAME, DAYS, rth_only=True)
    groups = {"stocks": {s: all_data[s] for s in STOCKS}, "etfs": {s: all_data[s] for s in ETFS}}
    first = min(df.index[0] for df in all_data.values())
    last = max(df.index[-1] for df in all_data.values())
    res = {"generated": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
           "timeframe": TIMEFRAME, "start": str(first.date()), "end": str(last.date()),
           "costs_bps": costs.fee_bps + costs.slippage_bps, "stand_aside_min": STAND_ASIDE_MIN,
           "materiality": MATERIALITY, "bases": BASES, "variants": VARIANTS,
           "earnings_check": detector_check(STOCKS),
           "earnings_used": {s: [str(d) for d in study_earnings(s, ng.earnings_sessions(s))] for s in STOCKS},
           "blocked": {g: {f">={m}": blocked_share(d, m) for m in (3, 4)} for g, d in groups.items()},
           "groups": {}}
    for k, (g, d) in enumerate(groups.items()):
        say(0.05 + 0.15 * k, f"{g}: comparing variants")
        res["groups"][g] = {"compare": compare_group(d, MATERIALITY[g], costs)}
    say(0.35, "stocks: detected earnings dates")
    res["groups"]["stocks"]["compare_detected_dates"] = compare_group(groups["stocks"], MATERIALITY["stocks"], costs, "detected")
    if walk:
        for k, (g, d) in enumerate(groups.items()):
            res["groups"][g]["walk_forward"] = walk_forward_group(
                d, MATERIALITY[g], costs, lambda f, m, k=k, g=g: say(0.4 + 0.3 * (k + f), f"{g} walk-forward, {m}"))
    res = _jsonable(res)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1), encoding="utf-8")
    say(1.0, "done")
    return res


def main():
    import argparse
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-walk", action="store_true", help="skip the walk-forward (faster)")
    a = ap.parse_args()
    res = run(lambda f, m: print(f"{f:5.0%} {m}", flush=True), walk=not a.no_walk)
    for g, gr in res["groups"].items():
        for base, c in gr["compare"].items():
            print(f"\n== {g} / {base} {c['params']}")
            for r in c["summary"]:
                vs = r["vs_base"]
                extra = "" if not vs else f"  vs base {vs['mean_bps']:+.2f} bps/day CI {vs['ci95_bps']}{' *' if vs['significant'] else ''}"
                print(f"  {r['variant']:<16} ret {r['total_return_%']:>7}%  sharpe {r['sharpe']:>5}  "
                      f"dd {r['max_drawdown_%']:>7}%  trades {r['trades']:>5}{extra}")
        for base, w in (gr.get("walk_forward") or {}).items():
            print(f"  WF {base}: guarded {w['walk_forward']['total_return_%']}% sharpe {w['walk_forward']['sharpe']} | "
                  f"base {w['base_alone']['total_return_%']}% sharpe {w['base_alone']['sharpe']} | "
                  f"guard on in {w['windows_guard_on']}/{w['windows']} windows | {w['vs_base']}")
    print("\nEarnings detector:", json.dumps(res["earnings_check"], indent=1))
    print("Blocked share:", json.dumps(res["blocked"], indent=1))
    print(f"\nSaved {OUT}")


if __name__ == "__main__":
    main()
