"""
Compare the account rules on the bot's own jobs (Phase 5): what would the daily loss limit and
the profit lock have done?

The enabled jobs in config.yaml are backtested as one account with backtest.portfolio_backtest
(the live bot's sizing, per-asset-class costs from `recalibration.costs`, stocks flat before the
close like the live bot), three ways:
  off            no account rules
  loss_limit     the daily loss limit only (risk.max_daily_loss_pct)
  profit_lock    the loss limit + the profit lock with the given trigger / giveback
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

import pandas as pd

from . import recalibrate as rc
from .backtest import portfolio_backtest
from .data import fetch_bars, is_crypto
from .sizing import bucket_map
from .trade_rules import apply as trade_rules_apply
from .strategies import get_signal


def compare(cfg: Dict, days: int = 60, trigger_pct: float = 1.5, giveback_pct: float = 50.0,
            fetch: Optional[Callable] = None, capital: float = 100_000.0) -> Dict:
    live = cfg["live"]
    jobs = [j for j in live.get("jobs") or [] if j.get("enabled", True)]
    if not jobs:
        raise ValueError("No enabled jobs to test.")
    syms = [s for j in jobs for s in j["symbols"]]
    fetch = fetch or (lambda s, tf, d: fetch_bars(s, tf, d, rth_only=True))
    data = fetch(syms, live["timeframe"], days)
    r = rc.settings(cfg)
    flat_min = int((live.get("risk") or {}).get("flatten_before_close_min", 0))
    signals = {}
    for j in jobs:
        for s in j["symbols"]:
            sig = get_signal(j["strategy"], data[s], j.get("params") or {}, bool(live.get("allow_short", False)))
            sig = trade_rules_apply(sig, data[s], live.get("trade_rules"))   # as live
            if is_crypto(s):
                sig = sig.clip(lower=0)
            else:
                sig = rc.flat_before_close(sig, data[s], flat_min)
            signals[s] = sig
    alloc = {s: j["allocation_pct"] for j in jobs for s in j["symbols"]}
    costs = {s: rc.costs_for(s, r) for s in syms}
    buckets = bucket_map(cfg)
    base = dict(live.get("risk") or {})
    variants = {
        "off": ({**base, "profit_lock": {"enabled": False}}, False),
        "loss_limit": ({**base, "profit_lock": {"enabled": False}}, True),
        "profit_lock": ({**base, "profit_lock": {"enabled": True, "trigger_pct": trigger_pct, "giveback_pct": giveback_pct}}, True),
    }
    out = {"symbols": syms, "days": days, "trigger_pct": trigger_pct, "giveback_pct": giveback_pct,
           "loss_limit_pct": base.get("max_daily_loss_pct"), "sizing": base.get("sizing", "fixed"), "results": {}}
    for name, (risk, rules) in variants.items():
        pb = portfolio_backtest(data, signals, risk, alloc, buckets, costs, capital, account_rules=rules)
        eq = pb["frame"].equity
        daily = eq.groupby(eq.index.tz_convert("America/New_York").date).last()
        m = pb["metrics"]
        out["results"][name] = {
            "total_return_%": m["total_return_%"], "sharpe": m["sharpe"], "max_drawdown_%": m["max_drawdown_%"],
            "halts_loss_limit": sum(e["rule"] == "loss_limit" for e in pb["events"]),
            "halts_profit_lock": sum(e["rule"] == "profit_lock" for e in pb["events"]),
            "events": [{**e, "time": pd.Timestamp(e["time"]).isoformat()} for e in pb["events"]][-50:],
            "daily_equity": {str(d): round(float(v), 6) for d, v in daily.items()},
        }
    return out
