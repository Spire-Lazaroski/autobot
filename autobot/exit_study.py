"""
Exit-rule study for the Lab: does the trailing exit / re-entry layer help the enabled jobs?

For each enabled job's symbols, over the last `days` (default 90) at the bot's timeframe, with each
asset class's costs and stocks flat before the close (as the live bot):
  none     the strategy alone
  fixed    every trail_pct x reentry setting, applied all the time
  walk-forward  in each window the setting (or none) that scored best on the TRAIN days is used on
           the next TEST days (recalibration's windows), so its result is out of sample.

Reported for the whole account (equal weight per symbol): net return, Sharpe, max drawdown, trades,
average gain kept per winning trade, both over all days ("in-sample": the settings were not tuned,
but picking the best of 16 afterwards would be) and on the test days only, plus the walk-forward's
daily difference to "none" with a 95% interval. Only that last line answers "does it help out of
sample after costs". Note: when recalibration chose the jobs' strategies on these same days, "none"
over all days is flattered too; compare on the test days.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from . import recalibrate as rc
from .backtest import _trades, metrics
from .data import fetch_bars

TRAILS = [0.15, 0.25, 0.4, 0.6]
MODES = ["fresh_cross", "new_high", "both", "cooldown"]


def _label(e: Optional[Dict]) -> str:
    return "none" if not e else f"trail {e['trail_pct']}% · {e['reentry'].replace('_', ' ')}"


def _summary(frames: List[pd.DataFrame]) -> Dict:
    """Equal-weight account over symbols: metrics of the combined returns, trades and the
    average gain of winning trades (summed over symbols)."""
    rets = pd.concat([f.ret for f in frames], axis=1, sort=True).fillna(0).mean(axis=1)
    m = metrics(pd.DataFrame({"ret": rets, "equity": (1 + rets).cumprod(), "position": 1}, index=rets.index))
    tr = pd.concat([_trades(f) for f in frames]) if frames else pd.Series(dtype=float)
    wins = tr[tr > 0]
    return {"return_%": float(m["total_return_%"]), "sharpe": float(m["sharpe"]), "max_drawdown_%": float(m["max_drawdown_%"]),
            "trades": int(len(tr)), "avg_win_%": round(float(wins.mean() * 100), 3) if len(wins) else 0.0, "_ret": rets}


def _daily(r: pd.Series) -> pd.Series:
    return (1 + r).groupby(r.index.tz_convert("America/New_York").date).prod() - 1


def paired(a: pd.Series, b: pd.Series) -> Dict:
    d = (_daily(a) - _daily(b).reindex(_daily(a).index).fillna(0)).dropna()
    n = len(d)
    if n < 2:
        return {"days": n, "mean_bps": 0.0, "ci95_bps": [0.0, 0.0], "significant": False}
    mu, se = d.mean(), d.std(ddof=1) / math.sqrt(n)
    lo, hi = mu - 1.96 * se, mu + 1.96 * se
    return {"days": n, "mean_bps": round(float(mu) * 1e4, 2), "ci95_bps": [round(float(lo) * 1e4, 2), round(float(hi) * 1e4, 2)],
            "significant": bool(lo > 0 or hi < 0)}


def run(cfg: Dict, days: int = 90, fetch: Optional[Callable] = None, as_of: Optional[date] = None,
        progress: Optional[Callable[[float, str], None]] = None) -> Dict:
    live = cfg["live"]
    jobs = [j for j in live.get("jobs") or [] if j.get("enabled", True)]
    if not jobs:
        raise ValueError("No enabled jobs to test.")
    r = {**rc.settings(cfg), "lookback_days": days, "include_exit": False}
    say = progress or (lambda f, m: None)
    fetch = fetch or (lambda s, tf, d: fetch_bars(s, tf, d, rth_only=True))
    as_of = as_of or pd.Timestamp.now(tz="America/New_York").date()
    grid = [None] + [{"trail_pct": t, "reentry": m} for t in TRAILS for m in MODES]
    pairs = [(j, s) for j in jobs for s in j["symbols"]]
    full = {_label(e): [] for e in grid}
    test = {_label(e): [] for e in grid}
    wf, wf_choices = [], []
    for k, (job, sym) in enumerate(pairs):
        say(k / len(pairs), f"{sym}: backtesting {len(grid)} exit settings")
        df = fetch([sym], live["timeframe"], days + int(r["warmup_days"]) + 5)[sym]
        df = df[np.asarray(df.index.tz_convert("America/New_York").date) <= as_of]
        st = rc.SymbolStudy(sym, df, r, as_of)
        if not st.win:
            continue
        base = {"strategy": job["strategy"], "params": job.get("params") or {}}
        specs = {_label(e): ({**base, "exit": e} if e else base) for e in grid}
        a0, b0 = st.win[0][0], st.win[-1][3]
        for lab, spec in specs.items():
            full[lab].append(st.bt(spec).iloc[a0:b0])
            test[lab].append(st.oos_frame([spec] * len(st.win)))
        picks = []
        for ta, tb, _, _ in st.win:  # walk-forward: the train days pick the setting (none included)
            best = max(specs.values(), key=lambda s: st.wscore(s, ta, tb)["score"])
            picks.append(best)
            wf_choices.append({"symbol": sym, "choice": _label(best.get("exit"))})
        wf.append(st.oos_frame(picks))
    rows = []
    for lab in full:
        a, b = _summary(full[lab]), _summary(test[lab])
        rows.append({"setting": lab, "all_days": {k: v for k, v in a.items() if k != "_ret"},
                     "test_days": {k: v for k, v in b.items() if k != "_ret"}})
    w = _summary(wf)
    none_test = _summary(test["none"])
    vs = paired(w["_ret"], none_test["_ret"])
    counts = pd.Series([c["choice"] for c in wf_choices]).value_counts().to_dict() if wf_choices else {}
    say(1.0, "done")
    return {"as_of": str(as_of), "days": days, "symbols": [s for _, s in pairs], "timeframe": live["timeframe"],
            "train_days": r["train_days"], "test_days": r["test_days"], "rows": rows,
            "walk_forward": {**{k: v for k, v in w.items() if k != "_ret"}, "vs_none": vs, "choices": counts},
            "none_test_days": {k: v for k, v in none_test.items() if k != "_ret"},
            "beats_none_oos": bool(vs["significant"] and vs["mean_bps"] > 0)}
