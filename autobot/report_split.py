"""
Where a live report's result came from, compared fairly (added 2026-10-09).

The account holds three different things: the core (one index fund, volatility-sized), the rotation
sleeve (sector funds, monthly) and the trading jobs (5-minute, flat every night). One comparison against
"all the money in SPY" mixes them up, so the report splits the result and compares each part with what
it is fairly measured against:

  core      its result vs simply keeping the shares it had at the start (the difference is what that
            period's rebalancing did)
  rotation  its result vs an equal mix of all the sector funds with the same money (did picking help?)
  jobs      their result vs buying the same symbols at the open and holding to the close-out with the
            same money (autobot.llm_judge.jobs_vs_hold, from broker fills)
  account   its result vs SPY at the share of the account that was actually invested on average

All from fills, positions and price bars; nothing is estimated beyond what the notes say.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd


def start_quantities(now: Dict[str, float], fills_since_start: Iterable[Dict]) -> Dict[str, float]:
    """Positions at the start of the period: today's minus everything filled since the start."""
    q = dict(now)
    for f in fills_since_start:
        q[f["symbol"]] = q.get(f["symbol"], 0.0) - (f["qty"] if f["side"] == "buy" else -f["qty"])
    return {s: round(v, 6) for s, v in q.items() if abs(v) > 1e-9}


def symbol_pnl(sym: str, q0: float, fills: List[Dict], bars: Optional[pd.DataFrame]) -> Optional[Dict]:
    """A symbol's result over the period: end value - start value - money paid for fills in the period;
    and what simply keeping the start position would have made. Start price = the first bar's open."""
    if bars is None or bars.empty:
        return None
    p0, p1 = float(bars.open.iloc[0]), float(bars.close.iloc[-1])
    ff = [f for f in fills if f["symbol"] == sym]
    paid = sum((f["qty"] if f["side"] == "buy" else -f["qty"]) * f["price"] for f in ff)
    q1 = q0 + sum(f["qty"] if f["side"] == "buy" else -f["qty"] for f in ff)
    return {"symbol": sym, "q0": q0, "q1": q1, "p0": p0, "p1": p1, "start_value": q0 * p0, "end_value": q1 * p1, "money_in": q0 * p0 + paid,
            "pnl": q1 * p1 - q0 * p0 - paid, "keep_start_pnl": q0 * (p1 - p0), "fills": len(ff)}


def invested_share(q0: Dict[str, float], fills: List[Dict], bars: Dict[str, pd.DataFrame], equity: pd.Series) -> Optional[float]:
    """Average share of the account held in positions (|value| / equity) over the period's bars."""
    shares = []
    syms = [s for s in set(q0) | {f["symbol"] for f in fills} if s in bars and not bars[s].empty]
    if not syms or equity.empty:
        return None
    clock = equity.index
    pos_value = pd.Series(0.0, index=clock)
    for s in syms:
        b = bars[s].close.reindex(clock, method="ffill").bfill()
        q = pd.Series(q0.get(s, 0.0), index=clock)
        for f in sorted((f for f in fills if f["symbol"] == s), key=lambda f: f["time"]):
            q[q.index >= f["time"]] += f["qty"] if f["side"] == "buy" else -f["qty"]
        pos_value += (q * b).abs()
    shares = (pos_value / equity).replace([np.inf, -np.inf], np.nan).dropna()
    return float(shares.mean()) if len(shares) else None


def equal_mix_pnl(symbols: List[str], bars: Dict[str, pd.DataFrame], money: float) -> Optional[float]:
    """An equal mix of these funds with `money`, bought at the first bar's open and held to the last close."""
    r = [float(bars[s].close.iloc[-1] / bars[s].open.iloc[0] - 1) for s in symbols if s in bars and not bars[s].empty]
    return money * float(np.mean(r)) if r else None


def build(core_sym: Optional[str], rotation_syms: List[str], q0: Dict[str, float], fills: List[Dict],
          bars: Dict[str, pd.DataFrame], equity: pd.Series, jobs_vs_hold_rows: Optional[List[Dict]], spy: Optional[pd.DataFrame]) -> Dict:
    long_term = ({core_sym} if core_sym else set()) | set(rotation_syms)
    held_lt = sorted(s for s in long_term if s in q0 or any(f["symbol"] == s for f in fills))
    per = {s: symbol_pnl(s, q0.get(s, 0.0), fills, bars.get(s)) for s in held_lt}
    per = {s: v for s, v in per.items() if v}
    total = float(equity.iloc[-1] - equity.iloc[0]) if len(equity) else 0.0
    out: Dict = {"total_pnl": total, "parts": []}
    if core_sym and core_sym in per:
        c = per[core_sym]
        out["parts"].append({"part": "core", "label": f"Core ({core_sym})", "pnl": c["pnl"], "fair": c["keep_start_pnl"],
                             "fair_label": f"keeping the {c['q0']:g} shares held at the start", "fills": c["fills"]})
    rot = [per[s] for s in rotation_syms if s in per]
    if rot:
        money = sum(max(0.0, v["money_in"]) for v in rot)                 # held at the start plus bought in the period
        mix = equal_mix_pnl(rotation_syms, bars, money)
        out["parts"].append({"part": "rotation", "label": f"Rotation ({', '.join(v['symbol'] for v in rot)})",
                             "pnl": sum(v["pnl"] for v in rot), "fair": mix,
                             "fair_label": f"an equal mix of all {len(rotation_syms)} sector funds with the same ${money:,.0f}",
                             "fills": sum(v["fills"] for v in rot)})
    lt_pnl = sum(p["pnl"] for p in out["parts"])
    jobs_pnl = total - lt_pnl
    jv = [r for r in (jobs_vs_hold_rows or []) if "error" not in r]
    out["parts"].append({"part": "jobs", "label": "Trading jobs", "pnl": jobs_pnl,
                         "fair": sum(r["hold_usd"] or 0 for r in jv) if jv else None,
                         "fair_label": "buying the same symbols at the open with the same money and holding to the close-out",
                         "note": "the jobs' result is the account's change minus the long-term holdings' (it includes their costs)"})
    share = invested_share(q0, fills, bars, equity)
    if spy is not None and not spy.empty and share is not None and len(equity):
        spy_r = float(spy.close.iloc[-1] / spy.open.iloc[0] - 1)
        out["account"] = {"pnl": total, "share": share, "spy_matched": float(equity.iloc[0]) * share * spy_r}
    return out
