"""
Position sizing, shared by the live bot and the portfolio backtester so both size the same way.

config.yaml live.risk:
  sizing: fixed | risk_balanced          (default fixed: today's behaviour)
  max_position_usd                       hard per-symbol cap in both modes
  max_gross_pct: 100                     both modes: all symbols together never get more than this %
                                         of equity; above it every symbol is scaled down alike
  gross_budget_pct: 90                   risk_balanced: share of equity spread over all job symbols
  bucket_max_pct: {crypto: 20}           risk_balanced: a bucket's cap, % of the gross budget
  atr_days: 20                           risk_balanced: days of average true range

fixed          dollars per symbol = min(equity x job allocation %, max_position_usd)
risk_balanced  gross = equity x gross_budget_pct; each symbol of an enabled job gets a share
               proportional to 1 / ATR% (calmer symbols get more dollars, so each one moves
               the account about equally), then the per-symbol cap and the bucket caps apply.
               Money cut by a cap is NOT handed to the other symbols: it stays as cash, so the
               totals never exceed any limit.
max_gross_pct  last step in both modes: if the dollars of all job symbols add up to more than
               equity x max_gross_pct, each is scaled by the same factor (e.g. 20 jobs at 10%
               become 5% each with the default 100), so the jobs can never borrow on margin.

ATR% = average over the last `atr_days` COMPLETED New York days of the daily true range
(max(high, previous close) - min(low, previous close)) / close x 100. Today's unfinished day
is never used, so the live bot and the backtest see the same numbers.
Buckets come from config `universe.buckets`; symbols not listed there are bucket "other".
"""
from __future__ import annotations

from datetime import date
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

NY = "America/New_York"
DEFAULTS = {"sizing": "fixed", "gross_budget_pct": 90.0, "bucket_max_pct": {"crypto": 20.0}, "atr_days": 20, "max_gross_pct": 100.0}


def settings(risk: Dict) -> Dict:
    s = {**DEFAULTS, **{k: risk[k] for k in DEFAULTS if k in (risk or {})}}
    s["bucket_max_pct"] = {**DEFAULTS["bucket_max_pct"], **(risk.get("bucket_max_pct") or {})}
    s["max_position_usd"] = float(risk["max_position_usd"])
    return s


def validate(risk: Dict) -> List[str]:
    errs = []
    mode = (risk or {}).get("sizing", "fixed")
    if mode not in ("fixed", "risk_balanced"):
        errs.append("risk.sizing must be 'fixed' or 'risk_balanced'")
    g = (risk or {}).get("gross_budget_pct", DEFAULTS["gross_budget_pct"])
    if not isinstance(g, (int, float)) or not 0 < g <= 100:
        errs.append("risk.gross_budget_pct must be between 0 and 100")
    for b, v in ((risk or {}).get("bucket_max_pct") or {}).items():
        if not isinstance(v, (int, float)) or not 0 < v <= 100:
            errs.append(f"risk.bucket_max_pct.{b} must be between 0 and 100")
    m = (risk or {}).get("max_gross_pct", DEFAULTS["max_gross_pct"])
    if not isinstance(m, (int, float)) or isinstance(m, bool) or not 0 < m <= 100:
        errs.append("risk.max_gross_pct must be between 0 and 100")
    a = (risk or {}).get("atr_days", DEFAULTS["atr_days"])
    if not isinstance(a, int) or not 2 <= a <= 250:
        errs.append("risk.atr_days must be a whole number between 2 and 250")
    pl = (risk or {}).get("profit_lock")
    if pl is not None:
        if not isinstance(pl, dict):
            errs.append("risk.profit_lock must be a mapping")
        else:
            t, g = pl.get("trigger_pct"), pl.get("giveback_pct")
            if not isinstance(t, (int, float)) or t <= 0:
                errs.append("risk.profit_lock.trigger_pct must be a positive number")
            if not isinstance(g, (int, float)) or not 0 < g < 100:
                errs.append("risk.profit_lock.giveback_pct must be between 0 and 100")
    return errs


def bucket_map(cfg: Optional[Dict]) -> Dict[str, str]:
    out = {}
    for name, b in (((cfg or {}).get("universe") or {}).get("buckets") or {}).items():
        for s in b.get("candidates") or []:
            out.setdefault(s, name)
    return out


# ----------------------------------------------------------------- volatility
def daily_tr_pct(bars: pd.DataFrame) -> pd.Series:
    """Daily true range % per New York date (the first day has no previous close: dropped)."""
    if bars is None or bars.empty:
        return pd.Series(dtype=float)
    d = bars.groupby(np.asarray(bars.index.tz_convert(NY).date)).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    prev = d.close.shift(1)
    tr = pd.concat([d.high, prev], axis=1).max(axis=1) - pd.concat([d.low, prev], axis=1).min(axis=1)
    return (tr / d.close * 100).iloc[1:]


def atr_before(tr_pct: pd.Series, before: date, days: int) -> Optional[float]:
    part = tr_pct[tr_pct.index < before].tail(days)
    return float(part.mean()) if len(part) else None


def daily_atr_pct(bars: pd.DataFrame, before: date, days: int) -> Optional[float]:
    """Average daily true range % over the last `days` New York days strictly before `before`."""
    return atr_before(daily_tr_pct(bars), before, days)


# ----------------------------------------------------------------- dollars per symbol
def budgets(equity: float, symbols: Iterable[str], risk: Dict, atr: Dict[str, Optional[float]],
            alloc_pct: Dict[str, float], buckets: Dict[str, str]) -> Dict[str, float]:
    """Dollars each symbol may hold (long or short), for the configured sizing mode, then the
    max_gross_pct cap over all symbols together."""
    s = settings(risk)
    syms = list(dict.fromkeys(symbols))
    if s["sizing"] != "risk_balanced":
        return _gross_cap({x: min(equity * alloc_pct.get(x, 0) / 100, s["max_position_usd"]) for x in syms}, equity, s)
    gross = equity * s["gross_budget_pct"] / 100
    inv = {x: 1.0 / atr[x] for x in syms if atr.get(x) and atr[x] > 0}
    if not inv:
        return {x: 0.0 for x in syms}
    tot = sum(inv.values())
    usd = {x: min(gross * inv.get(x, 0.0) / tot, s["max_position_usd"]) for x in syms}  # no ATR yet: 0 (waits)
    for b, cap_pct in s["bucket_max_pct"].items():
        members = [x for x in syms if buckets.get(x, "other") == b]
        have, cap = sum(usd[x] for x in members), gross * cap_pct / 100
        if have > cap > 0:
            for x in members:
                usd[x] *= cap / have
        elif cap <= 0:
            for x in members:
                usd[x] = 0.0
    return _gross_cap(usd, equity, s)


def _gross_cap(usd: Dict[str, float], equity: float, s: Dict) -> Dict[str, float]:
    cap, have = equity * float(s["max_gross_pct"]) / 100, sum(usd.values())
    if have > cap and have > 0:
        k = max(cap, 0.0) / have
        return {x: v * k for x, v in usd.items()}
    return usd


def to_qty(usd: float, price: float, direction: float, crypto: bool) -> float:
    """Whole shares for stocks (rounded down), fractional crypto (4 decimals, rounded down)."""
    if price <= 0 or direction == 0:
        return 0.0
    raw = usd / price * abs(direction)
    q = np.floor(raw * 1e4) / 1e4 if crypto else float(np.floor(raw))
    return float(q) * (1 if direction > 0 else -1)


# ----------------------------------------------------------------- per-day ATR for the backtester
def atr_table(data: Dict[str, pd.DataFrame], atr_days: int) -> Dict[date, Dict[str, Optional[float]]]:
    """{day: {symbol: ATR% from the days before it}} for every New York day in the data."""
    tr = {x: daily_tr_pct(df) for x, df in data.items()}
    days = sorted({d for df in data.values() for d in df.index.tz_convert(NY).date})
    return {d: {x: atr_before(t, d, atr_days) for x, t in tr.items()} for d in days}
