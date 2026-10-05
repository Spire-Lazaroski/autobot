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

trigger_levels: the same search, but keeping the nearest boundary on EACH side (below and above),
sorted into buy and sell levels, for the separate 'Trigger buy' / 'Trigger sell' columns.

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


def _setup(strategy, df, params, allow_short, long_only):
    cur = float(get_signal(strategy, df.copy(), params, allow_short).iloc[-1])
    if long_only:
        cur = max(cur, 0.0)
    return cur, float(df.close.iloc[-1]), _signal_fn(strategy, df, params, allow_short, long_only)


def _scan(want: Callable[[float], bool], last: float, side: int) -> Optional[tuple]:
    """Nearest offset on one side (-1 below, +1 above) where `want` becomes true: (hi, lo) with
    `want` true at hi and false at lo (fractions of the last close), or None within +-SPAN."""
    prev = 0.0
    k = 1
    while k * STEP <= SPAN + 1e-12:
        off = k * STEP
        if want(last * (1 + side * off)):
            lo, hi = prev, off
            for _ in range(BISECT):
                mid = (lo + hi) / 2
                if want(last * (1 + side * mid)):
                    hi = mid
                else:
                    lo = mid
            return hi, lo
        prev = off
        k += 1
    return None


def _level(sig, cur: float, last: float, side: int, off: float, lo: float, past: bool, flip_to: Optional[float]) -> Dict:
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


def trigger_level(strategy: str, df: pd.DataFrame, params: Optional[Dict] = None, allow_short: bool = False,
                  long_only: bool = False) -> Optional[Dict]:
    """The nearest next-bar close that changes the signal, or None if none within +-5%.
    long_only: crypto (no shorting on Alpaca), so a short signal counts as flat, like the bot."""
    params = params or {}
    if df is None or len(df) < 3:
        return None
    cur, last, sig = _setup(strategy, df, params, allow_short, long_only)
    differs = lambda p: abs(sig([p]) - cur) > 1e-9  # noqa: E731
    # Usually an unchanged price keeps the signal and we look for the nearest price that changes it.
    # Sometimes the signal changes at the next close even if the price doesn't move (e.g. momentum:
    # an old price leaves the lookback); then we look for the nearest price that would KEEP it.
    # Either way the answer is the nearest boundary between "keeps" and "changes".
    past = differs(last)
    want = (lambda p: not differs(p)) if past else differs
    best = None
    for side in (-1, 1):
        r = _scan(want, last, side)
        if r and (best is None or r[0] < best[1]):
            best = (side, r[0], r[1])
    flip_to = sig([last]) if past else None
    if best is None:
        if not past:
            return None  # no change within +-5%
        return {"current_signal": cur, "flip_to": flip_to, "trigger_price": None, "distance_pct": None, "direction": None,
                "kind": "sell" if flip_to < cur else "buy", "already_past": True, "bars_if_flat": 1, "last_close": last}
    return _level(sig, cur, last, *best, past, flip_to)


def trigger_levels(strategy: str, df: pd.DataFrame, params: Optional[Dict] = None, allow_short: bool = False,
                   long_only: bool = False) -> Optional[Dict]:
    """Both sides at once, for the separate 'Trigger buy' / 'Trigger sell' columns:
    {"nearest": <same as trigger_level>, "buy": [levels], "sell": [levels], "position": current signal}.
    Normally there is one boundary below and one above the last close (either may be missing within
    +-5%), and each is a buy or a sell: a long rsi_reversion sells ABOVE (it takes its profit), a
    long donchian_breakout sells BELOW (its exit channel, which rises with the price) and has
    nothing above. When the signal changes at the next close anyway (already_past) there is only
    the one level that would prevent it, as in trigger_level."""
    params = params or {}
    if df is None or len(df) < 3:
        return None
    cur, last, sig = _setup(strategy, df, params, allow_short, long_only)
    differs = lambda p: abs(sig([p]) - cur) > 1e-9  # noqa: E731
    out = {"nearest": None, "buy": [], "sell": [], "position": cur}
    if differs(last):
        out["nearest"] = trigger_level(strategy, df, params, allow_short, long_only)
        if out["nearest"]:
            out[out["nearest"]["kind"]].append(out["nearest"])
        return out
    for side in (-1, 1):
        r = _scan(differs, last, side)
        if r:
            lv = _level(sig, cur, last, side, r[0], r[1], False, None)
            out[lv["kind"]].append(lv)
            if out["nearest"] is None or abs(lv["distance_pct"]) < abs(out["nearest"]["distance_pct"]):
                out["nearest"] = lv
    return out


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


def with_exit(trig: Optional[Dict], exit_view: Optional[Dict], last: float) -> Optional[Dict]:
    """The chart line shows the NEAREST level that changes the position at the next close: the
    strategy's own flip level, or the exit layer's trail level (while in a position) or its
    new-high re-entry level (while waiting after a trail exit)."""
    if not exit_view:
        return trig
    lvl = None
    if exit_view.get("state") == "in" and exit_view.get("trail_level"):
        tl = exit_view["trail_level"]
        long = tl < last
        lvl = {"current_signal": 1.0 if long else -1.0, "flip_to": 0.0, "trigger_price": tl,
               "distance_pct": round((tl / last - 1) * 100, 3), "direction": "below" if long else "above",
               "kind": "sell" if long else "buy", "already_past": False, "bars_if_flat": None, "last_close": last,
               "source": "trail"}
    elif exit_view.get("state") == "waiting" and exit_view.get("waiting_for") in ("new_high", "both") and exit_view.get("peak"):
        pk = exit_view["peak"]
        lvl = {"current_signal": 0.0, "flip_to": 1.0 if pk > last else -1.0, "trigger_price": pk,
               "distance_pct": round((pk / last - 1) * 100, 3), "direction": "above" if pk > last else "below",
               "kind": "buy" if pk > last else "sell", "already_past": False, "bars_if_flat": None, "last_close": last,
               "source": "re-entry"}
    if lvl is None:
        return trig
    if exit_view.get("state") == "waiting" or trig is None or trig.get("distance_pct") is None:
        return lvl  # while waiting, the strategy's own flip doesn't trade; the re-entry level does
    return lvl if abs(lvl["distance_pct"]) < abs(trig["distance_pct"]) else {**trig, "source": "strategy"}


def with_exit_levels(levels: Optional[Dict], exit_view: Optional[Dict], last: float) -> Optional[Dict]:
    """trigger_levels plus the exit layer, by the same rules as with_exit: in a position the trail
    level replaces a strategy level on the same side of the price only if it is nearer; while
    waiting after a trail exit the strategy's own levels don't trade, only the re-entry level does."""
    if not exit_view or levels is None:
        return levels
    lvl = with_exit(None, exit_view, last)
    if lvl is None:
        return levels
    if lvl["source"] == "re-entry":
        return {**levels, "buy": [lvl] if lvl["kind"] == "buy" else [], "sell": [lvl] if lvl["kind"] == "sell" else []}
    out = {**levels, "buy": [dict(lv) for lv in levels["buy"]], "sell": [dict(lv) for lv in levels["sell"]]}
    side = [lv for lv in out[lvl["kind"]] if lv.get("direction") == lvl["direction"] and lv.get("distance_pct") is not None]
    if side and abs(side[0]["distance_pct"]) <= abs(lvl["distance_pct"]):
        side[0]["source"] = "strategy"
        return out
    out[lvl["kind"]] = [lvl] + [lv for lv in out[lvl["kind"]] if lv.get("direction") != lvl["direction"]]
    return out
