"""
Trade rules on top of any strategy's signal: a churn brake and no quick re-entry (default OFF).
Written from the 2026-10-05 discussion ("treat every signal as new": most 5-minute flips are noise).
One causal forward pass, like autobot.exits, so a backtest and a live bot could use the same code.

    churn_brake: {max_entries: 2, window_min: 60}
        after `max_entries` entries within `window_min` minutes, no new entry for the rest of the
        session (exits are always allowed; a flip becomes an exit)
    no_quick_reentry: true
        after an exit, a same-session entry on the same side needs a close beyond the best close of
        the position just closed (a new high for longs, a new low for shorts); the other side is free

Stocks: each New York session starts fresh (the bot is flat overnight).

Live (config live.trade_rules, applies to every trading job, not the core holding):
    trade_rules: {no_quick_reentry: true, min_change_pct: 10}
min_change_pct is an order rule, not a signal rule: while a position is held on the same side,
the bot skips an order that changes it by less than this % (the 1-share top-ups and trims that
only come from small sizing changes). Chosen 2026-10-05 after the trade-rules study
(docs/STRATEGY_EVIDENCE.md section 7); the churn brake is available but not switched on.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


SIGNAL_RULES = ("churn_brake", "no_quick_reentry")


def validate(cfg) -> list:
    if cfg is None:
        return []
    if not isinstance(cfg, dict):
        return ["trade_rules must be a mapping"]
    errs = []
    unknown = set(cfg) - set(SIGNAL_RULES) - {"min_change_pct"}
    if unknown:
        errs.append(f"trade_rules: unknown settings {', '.join(sorted(unknown))}")
    if not isinstance(cfg.get("no_quick_reentry", False), bool):
        errs.append("trade_rules.no_quick_reentry must be true or false")
    b = cfg.get("churn_brake")
    if b is not None and not (isinstance(b, dict) and isinstance(b.get("max_entries"), int) and b["max_entries"] >= 1
                              and isinstance(b.get("window_min"), (int, float)) and b["window_min"] > 0):
        errs.append("trade_rules.churn_brake needs max_entries (whole number >= 1) and window_min (> 0)")
    m = cfg.get("min_change_pct", 0)
    if not isinstance(m, (int, float)) or isinstance(m, bool) or not 0 <= m < 100:
        errs.append("trade_rules.min_change_pct must be between 0 and 100")
    return errs


def small_change(cur: float, target: float, min_change_pct: float) -> bool:
    """True when an order would only nudge a held position (same side) by less than the threshold."""
    if not min_change_pct or cur == 0 or target == 0 or (cur > 0) != (target > 0):
        return False
    return abs(target - cur) < abs(target) * float(min_change_pct) / 100


def apply(signal: pd.Series, df: pd.DataFrame, rules: Optional[Dict]) -> pd.Series:
    if not rules or not (rules.get("churn_brake") or rules.get("no_quick_reentry")):
        return signal
    brake = rules.get("churn_brake") or None
    reentry = bool(rules.get("no_quick_reentry"))
    sig = signal.reindex(df.index).fillna(0.0).to_numpy(float)
    close = df.close.to_numpy(float)
    t = df.index.as_unit("ns").asi8
    day = np.asarray(df.index.tz_convert("America/New_York").date)
    win = int(pd.Timedelta(minutes=int(brake["window_min"])).value) if brake else 0
    out = np.zeros(len(sig))
    prev = 0.0
    entries: list = []
    aside = False
    best = np.nan                 # best close of the open position
    wait = {1: np.nan, -1: np.nan}  # re-entry needs a close beyond this, per side
    for i in range(len(sig)):
        if i and day[i] != day[i - 1]:
            prev, entries, aside, best, wait = 0.0, [], False, np.nan, {1: np.nan, -1: np.nan}
        s = sig[i]
        side = int(np.sign(s))
        pside = int(np.sign(prev))
        if pside != 0 and side != pside:                       # the position closes: remember its best close
            wait[pside], best = best, np.nan
        if side != 0 and side != pside:                       # an entry (or the entry half of a flip)
            ok = True
            if brake:
                recent = [x for x in entries if t[i] - x < win]
                if aside or len(recent) >= int(brake["max_entries"]):
                    ok, aside = False, True
            if ok and reentry and not np.isnan(wait[side]) and close[i] * side <= wait[side] * side:
                ok = False
            if not ok:
                s, side = 0.0, 0
            else:
                entries.append(t[i])
                best = close[i]
        elif side != 0:
            best = max(best, close[i]) if side > 0 else min(best, close[i])
        out[i] = s
        prev = s
    return pd.Series(out, index=signal.index)
