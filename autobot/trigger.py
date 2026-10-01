"""
Trigger levels for the Live page (display only, never used for trading).

trigger_level(strategy, df, params, allow_short) answers: at what price would the strategy's
signal change at the next bar's close, and how far is that from the last close?

How: append one hypothetical next bar (open = high = low = close = P, the last bar's volume),
re-run the strategy on the bars plus that bar, and find the P closest to the last close where
the last bar's signal differs from the current one. A coarse scan (every 0.25% out to +-5%)
finds the nearest bracket on each side, then bisection pins the price down. Generic: it only
calls get_signal(), so it works for every registered strategy, meta-strategies included. It
copies the bars, so the live signal is never touched, and it uses no data after the last bar.

Sometimes the signal changes at the next close even if the price doesn't move (momentum: an old
price leaves the lookback). Then `already_past` is True and the trigger is the nearest price that
would KEEP the signal: "sells at the next close unless above X". In both cases trigger_price is the
boundary (on its changing side) and `direction` says on which side of it the signal changes.

bars_if_flat: if the price instead stays just short of the trigger (0.1% of the way back towards
the last close), how many bars until the signal changes anyway (an average crossing can keep
moving towards the price), up to MAX_FLAT_BARS; None if not within that.

Peak since entry: the best price since the position was opened, and how much has been given back.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from .strategies import get_signal

SPAN = 0.05          # search +-5% around the last close
STEP = 0.0025        # coarse scan every 0.25%
BISECT = 20          # bisection steps (0.25% / 2^20: far finer than a cent)
NEAR_SIDE = 0.001    # bars_if_flat: 0.1% short of the trigger
MAX_FLAT_BARS = 50


def _step(df: pd.DataFrame) -> pd.Timedelta:
    return pd.Series(df.index[-50:]).diff().median() if len(df) > 2 else pd.Timedelta(minutes=5)


def _with_bars(df: pd.DataFrame, prices: List[float]) -> pd.DataFrame:
    """The bars plus one hypothetical flat bar per price (open = high = low = close)."""
    step = _step(df)
    idx = pd.DatetimeIndex([df.index[-1] + step * (i + 1) for i in range(len(prices))])
    vol = float(df.volume.iloc[-1]) if "volume" in df else 0.0
    extra = pd.DataFrame({"open": prices, "high": prices, "low": prices, "close": prices, "volume": vol}, index=idx)
    out = pd.concat([df[["open", "high", "low", "close", "volume"]], extra])
    out.attrs = dict(df.attrs)
    return out


def _signal_fn(strategy: str, df: pd.DataFrame, params: Dict, allow_short: bool, long_only: bool) -> Callable[[List[float]], float]:
    def last(prices: List[float]) -> float:
        s = float(get_signal(strategy, _with_bars(df, prices), params, allow_short).iloc[-1])
        return max(s, 0.0) if long_only else s
    return last


def trigger_level(strategy: str, df: pd.DataFrame, params: Optional[Dict] = None, allow_short: bool = False,
                  long_only: bool = False) -> Optional[Dict]:
    """The nearest next-bar close that changes the signal, or None if none within +-5%.
    long_only: crypto (no shorting on Alpaca), so a short signal counts as flat, like the bot."""
    params = params or {}
    if df is None or len(df) < 3:
        return None
    cur = float(get_signal(strategy, df.copy(), params, allow_short).iloc[-1])
    if long_only:
        cur = max(cur, 0.0)
    last = float(df.close.iloc[-1])
    sig = _signal_fn(strategy, df, params, allow_short, long_only)
    differs = lambda p: abs(sig([p]) - cur) > 1e-9  # noqa: E731
    # Usually an unchanged price keeps the signal and we look for the nearest price that changes it.
    # Sometimes the signal changes at the next close even if the price doesn't move (e.g. momentum:
    # an old price leaves the lookback); then we look for the nearest price that would KEEP it.
    # Either way the answer is the nearest boundary between "keeps" and "changes".
    past = differs(last)
    want = (lambda p: not differs(p)) if past else differs
    best = None
    for side in (-1, 1):
        prev = 0.0
        k = 1
        while k * STEP <= SPAN + 1e-12:
            off = k * STEP
            if want(last * (1 + side * off)):
                lo, hi = prev, off  # `want` false at lo, true at hi
                for _ in range(BISECT):
                    mid = (lo + hi) / 2
                    if want(last * (1 + side * mid)):
                        hi = mid
                    else:
                        lo = mid
                if best is None or hi < best[1]:
                    best = (side, hi, lo)
                break
            prev = off
            k += 1
    flip_to = sig([last]) if past else None
    if best is None:
        if not past:
            return None  # no change within +-5%
        return {"current_signal": cur, "flip_to": flip_to, "trigger_price": None, "distance_pct": None, "direction": None,
                "kind": "sell" if flip_to < cur else "buy", "already_past": True, "bars_if_flat": 1, "last_close": last}
    side, off, lo = best
    # the bisection end on the CHANGING side of the boundary (hi when looking for a change, lo when
    # looking for a keep); not rounded, since rounding could cross the boundary
    price = last * (1 + side * (off if not past else lo))
    flip_to = flip_to if past else sig([price])
    flip_side = side if not past else -side   # where the changing prices lie, seen from the boundary
    bars_if_flat = 1 if past else None
    if not past:
        near = last * (1 + side * max(off - NEAR_SIDE, 0.0))
        for n in range(1, MAX_FLAT_BARS + 1):
            if abs(sig([near] * n) - cur) > 1e-9:
                bars_if_flat = n
                break
    return {"current_signal": cur, "flip_to": flip_to, "trigger_price": float(price),
            "distance_pct": round(side * off * 100, 3), "direction": "above" if flip_side > 0 else "below",
            "kind": "sell" if flip_to < cur else "buy", "already_past": past, "bars_if_flat": bars_if_flat,
            "last_close": last}


# ----------------------------------------------------------------- peak since entry
def entry_time(trades: List[Dict], symbol: str, qty: float) -> Optional[pd.Timestamp]:
    """When the current position was opened, from the bot's trades.csv rows (oldest first):
    walk back from the newest trade until the signed quantities add up to the position.
    None if the trades don't explain the position (e.g. opened outside the bot)."""
    if not qty:
        return None
    total = 0.0
    for row in reversed([r for r in trades if r.get("symbol") == symbol]):
        q = float(row.get("qty") or 0)
        side = row.get("side")
        total += q if side == "buy" else -q if side in ("sell", "close") else 0.0
        if abs(total - qty) < 1e-6:
            return pd.Timestamp(row["time"])
    return None


def peak_since(df: pd.DataFrame, since: pd.Timestamp, qty: float) -> Optional[Dict]:
    """Best price since `since` (highest high when long, lowest low when short) and the share
    given back from it at the last close."""
    if df is None or df.empty or since is None or not qty:
        return None
    step = _step(df)
    part = df[df.index + step > since]  # the bar the entry happened in, and everything after
    if part.empty:
        return None
    last = float(df.close.iloc[-1])
    if qty > 0:
        peak = float(part.high.max())
        gave = (peak - last) / peak * 100
    else:
        peak = float(part.low.min())
        gave = (last - peak) / peak * 100
    return {"peak": round(peak, 4), "gave_back_pct": round(max(gave, 0.0), 3), "since": since.isoformat()}
