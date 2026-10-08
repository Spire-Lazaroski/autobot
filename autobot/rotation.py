"""
Rotation sleeve: a monthly industry rotation held next to the core (chosen 2026-10-08, option 3;
evidence: docs/STRATEGY_EVIDENCE.md sections 23-24).

  live:
    rotation: {enabled: true, allocation_pct: 40, top_n: 5, lookback_months: 12, trend_months: 10,
               vol_target_pct: 12, rebalance_band_pct: 1, symbols: [XLK, XLV, ...]}

Once a month (the first open tick of the month's first trading day, or at once when first switched on)
the bot reads the sector funds' daily closes of COMPLETED months and:
  1. ranks the funds by their return from 12 month-ends ago to 1 month-end ago (the latest month is
     skipped, the standard momentum measure);
  2. keeps the top_n, but only those whose last month-end close is above the average of their last
     trend_months month-end closes; a fund that fails leaves its share in cash;
  3. sizes each kept fund at allocation_pct / top_n x min(1, vol_target_pct / its yearly volatility over
     the last 12 monthly returns), so a jumpy fund gets less and the sleeve never exceeds allocation_pct.
It trades a fund only when its weight is more than rebalance_band_pct points from its target (or to buy
or sell it completely). Like the core, the sleeve is kept overnight and by Stop, and the daily loss
limit and the profit lock ignore it. The same weights as autobot.rs_study.weights(R, "full", top_n)
(tests/test_rotation.py checks that), on adjusted closes, so splits and dividends don't distort it.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

SECTORS = ["XLK", "XLV", "XLE", "XLF", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]
DEFAULTS = {"enabled": False, "symbols": SECTORS, "allocation_pct": 40, "top_n": 5, "lookback_months": 12,
            "trend_months": 10, "vol_target_pct": 12, "rebalance_band_pct": 1}
NY = "America/New_York"


def settings(cfg: Optional[Dict]) -> Dict:
    return {**DEFAULTS, **(cfg or {})}


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate(cfg, job_symbols=(), core_symbol: Optional[str] = None, core_max_pct: float = 0.0,
             max_gross_pct: float = 100.0) -> List[str]:
    """Problems with live.rotation (empty = OK)."""
    if cfg is None:
        return []
    if not isinstance(cfg, dict):
        return ["rotation must be a mapping"]
    errs = []
    unknown = set(cfg) - set(DEFAULTS)
    if unknown:
        errs.append(f"rotation: unknown settings {', '.join(sorted(unknown))}")
    c = settings(cfg)
    if not isinstance(c["enabled"], bool):
        errs.append("rotation.enabled must be true or false")
    syms = c["symbols"]
    if not isinstance(syms, list) or not syms or not all(isinstance(s, str) and s and "/" not in s for s in syms):
        errs.append("rotation.symbols must be a list of stock or ETF symbols (no crypto)")
        syms = []
    a = c["allocation_pct"]
    if not _num(a) or not 0 < a <= 100:
        errs.append("rotation.allocation_pct must be between 0 and 100")
    elif c["enabled"] and a + core_max_pct > max_gross_pct:
        errs.append(f"rotation.allocation_pct ({a}) plus the core's largest share ({core_max_pct:g}) can't be above "
                    f"risk.max_gross_pct ({max_gross_pct:g})")
    for k, lo, hi in (("top_n", 1, 50), ("lookback_months", 3, 36), ("trend_months", 2, 24)):
        v = c[k]
        if not (isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi):
            errs.append(f"rotation.{k} must be a whole number from {lo} to {hi}")
    if isinstance(c["top_n"], int) and syms and c["top_n"] > len(syms):
        errs.append("rotation.top_n can't be more than the number of symbols")
    if not (_num(c["vol_target_pct"]) and 1 <= c["vol_target_pct"] <= 50):
        errs.append("rotation.vol_target_pct must be between 1 and 50")
    if not (_num(c["rebalance_band_pct"]) and 0 < c["rebalance_band_pct"] <= 20):
        errs.append("rotation.rebalance_band_pct must be between 0 and 20")
    if c["enabled"]:
        clash = sorted(set(syms) & set(job_symbols))
        if clash:
            errs.append(f"rotation.symbols {', '.join(clash)} also in an enabled job; the sleeve needs symbols of its own")
        if core_symbol and core_symbol in syms:
            errs.append(f"rotation.symbols include the core symbol {core_symbol}")
    return errs


def month_end_closes(daily: pd.Series, today: str) -> pd.Series:
    """Last close of each COMPLETED month (months before today's), indexed by month."""
    d = daily.dropna()
    idx = d.index.tz_convert(NY) if getattr(d.index, "tz", None) is not None else d.index
    s = pd.Series(d.to_numpy(), index=pd.PeriodIndex(idx, freq="M"))
    m = s.groupby(level=0).last()
    return m[m.index < pd.Period(today, "M")]


def targets(daily: Dict[str, pd.Series], cfg: Optional[Dict], today: str) -> Dict:
    """This month's sleeve: {as_of, ranking [(symbol, momentum %, above trend)], weights {symbol: % of the
    account}, reasons {symbol: text}}; funds without enough history are left out."""
    c = settings(cfg)
    L, T, n = int(c["lookback_months"]), int(c["trend_months"]), int(c["top_n"])
    rows = []
    as_of = None
    for sym, s in daily.items():
        m = month_end_closes(s, today)
        if len(m) < max(L + 1, T, 13):
            continue
        as_of = max(as_of, m.index[-1]) if as_of is not None else m.index[-1]
        mom = float(m.iloc[-2] / m.iloc[-(L + 1)] - 1)                 # month-end t-1 over month-end t-L (t = last completed)
        trend = bool(m.iloc[-1] > m.iloc[-T:].mean())
        r = m.pct_change().iloc[-12:]
        vol = float(r.std(ddof=1) * math.sqrt(12))
        rows.append({"symbol": sym, "mom": mom, "above_trend": trend, "vol": vol})
    rows.sort(key=lambda x: -x["mom"])
    weights, reasons, ranking = {}, {}, []
    for k, x in enumerate(rows, start=1):
        ranking.append({"rank": k, "symbol": x["symbol"], "momentum_%": round(x["mom"] * 100, 2), "above_trend": x["above_trend"],
                        "volatility_%": round(x["vol"] * 100, 1)})
        if k <= n and x["above_trend"]:
            scale = min(1.0, float(c["vol_target_pct"]) / 100 / x["vol"]) if x["vol"] > 0 else 1.0
            w = float(c["allocation_pct"]) / n * scale
            weights[x["symbol"]] = round(w, 3)
            reasons[x["symbol"]] = (f"rank {k} of {len(rows)} by the {L}-month return ({x['mom'] * 100:+.1f}%), above its "
                                    f"{T}-month average; {w:.1f}% of the account (volatility {x['vol'] * 100:.0f}% a year)")
        elif k <= n:
            reasons[x["symbol"]] = f"rank {k} of {len(rows)} but below its {T}-month average: its share stays in cash"
    return {"as_of": str(as_of) if as_of is not None else None, "ranking": ranking, "weights": weights, "reasons": reasons,
            "invested_%": round(sum(weights.values()), 2)}


def orders(held: Dict[str, float], prices: Dict[str, float], equity: float, tgt: Dict, cfg: Optional[Dict]) -> List[Dict]:
    """Whole-share orders that bring the sleeve to this month's targets (beyond the band only)."""
    c = settings(cfg)
    band = float(c["rebalance_band_pct"])
    out = []
    for sym in sorted(set(c["symbols"]) | set(held)):
        if sym not in c["symbols"]:
            continue
        px = prices.get(sym)
        if not px or px <= 0 or equity <= 0:
            continue
        qty = float(held.get(sym, 0.0))
        w_now = qty * px / equity * 100
        w_tgt = float(tgt["weights"].get(sym, 0.0))
        target_qty = float(np.floor(equity * w_tgt / 100 / px))
        if w_tgt > 0 and qty > 0 and abs(w_now - w_tgt) <= band:
            continue
        delta = target_qty - qty
        if delta == 0:
            continue
        why = tgt["reasons"].get(sym) or f"no longer in the top {c['top_n']}: sold"
        out.append({"symbol": sym, "qty": delta, "target_qty": target_qty, "weight_%": round(w_now, 2), "target_%": w_tgt,
                    "reason": f"Rotation: {sym} {why}"})
    return out
