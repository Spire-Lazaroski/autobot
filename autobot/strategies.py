"""
Strategy library.

Each strategy is a function  f(df, **params) -> pd.Series of target positions
(1 = long, 0 = flat, -1 = short) aligned to df.index.

The signal on bar t is computed with data up to and including bar t's close.
The backtester trades it on the NEXT bar, so there is no look-ahead.

`df` has columns: open, high, low, close, volume (and optionally vwap).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List

import numpy as np
import pandas as pd


# ----------------------------------------------------------------- helpers
def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / down.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _state_machine(long_entry, long_exit, short_entry=None, short_exit=None) -> pd.Series:
    """Turn entry/exit boolean series into a held position (1/0/-1)."""
    pos = pd.Series(np.nan, index=long_entry.index)
    pos[long_exit] = 0
    if short_exit is not None:
        pos[short_exit & ~long_entry] = 0
    pos[long_entry] = 1
    if short_entry is not None:
        pos[short_entry] = -1
    return pos.ffill().fillna(0)


def _apply_side(sig: pd.Series, allow_short: bool) -> pd.Series:
    return sig if allow_short else sig.clip(lower=0)


# ----------------------------------------------------------------- strategies
def buy_and_hold(df, **_):
    return pd.Series(1.0, index=df.index)


def sma_cross(df, fast=10, slow=30, allow_short=False, **_):
    """Trend: long when fast SMA > slow SMA."""
    f = df.close.rolling(fast).mean()
    s = df.close.rolling(slow).mean()
    sig = np.sign(f - s).fillna(0)
    return _apply_side(sig, allow_short)


def ema_cross(df, fast=9, slow=21, allow_short=False, **_):
    """Trend: long when fast EMA > slow EMA (reacts faster than SMA)."""
    sig = np.sign(_ema(df.close, fast) - _ema(df.close, slow)).fillna(0)
    sig.iloc[: slow] = 0
    return _apply_side(sig, allow_short)


def macd(df, fast=12, slow=26, signal=9, allow_short=False, **_):
    """Trend/momentum: long when MACD line above its signal line."""
    line = _ema(df.close, fast) - _ema(df.close, slow)
    sig_line = _ema(line, signal)
    sig = np.sign(line - sig_line).fillna(0)
    sig.iloc[: slow + signal] = 0
    return _apply_side(sig, allow_short)


def rsi_reversion(df, period=14, oversold=30, overbought=70, exit_level=50, allow_short=False, **_):
    """Mean reversion: buy oversold, exit when RSI recovers to exit_level."""
    r = _rsi(df.close, period)
    le, lx = r < oversold, r > exit_level
    se, sx = (r > overbought, r < exit_level) if allow_short else (None, None)
    return _state_machine(le, lx, se, sx)


def bollinger_reversion(df, period=20, num_std=2.0, allow_short=False, **_):
    """Mean reversion: buy below lower band, exit at the middle band."""
    mid = df.close.rolling(period).mean()
    sd = df.close.rolling(period).std()
    lower, upper = mid - num_std * sd, mid + num_std * sd
    le, lx = df.close < lower, df.close >= mid
    se, sx = (df.close > upper, df.close <= mid) if allow_short else (None, None)
    return _state_machine(le, lx, se, sx)


def donchian_breakout(df, entry=20, exit=10, allow_short=False, **_):
    """Breakout (Turtle-style): buy new N-bar high, exit on M-bar low."""
    hi = df.high.rolling(entry).max().shift(1)
    lo_exit = df.low.rolling(exit).min().shift(1)
    le, lx = df.close > hi, df.close < lo_exit
    if allow_short:
        lo = df.low.rolling(entry).min().shift(1)
        hi_exit = df.high.rolling(exit).max().shift(1)
        return _state_machine(le, lx, df.close < lo, df.close > hi_exit)
    return _state_machine(le, lx)


def vwap_reversion(df, band_pct=0.3, allow_short=False, **_):
    """Intraday mean reversion around session VWAP. Best on 1–15 min bars.
    Buy when price is band_pct % below VWAP, exit when it touches VWAP."""
    day = df.index.tz_convert("America/New_York").date if df.index.tz is not None else df.index.date
    tp = (df.high + df.low + df.close) / 3
    pv = (tp * df.volume).groupby(day).cumsum()
    vv = df.volume.groupby(day).cumsum().replace(0, np.nan)
    vwap = (pv / vv).ffill()
    dev = (df.close / vwap - 1) * 100
    le, lx = dev < -band_pct, dev >= 0
    se, sx = (dev > band_pct, dev <= 0) if allow_short else (None, None)
    return _state_machine(le, lx, se, sx)


def momentum(df, lookback=20, threshold=0.0, allow_short=False, **_):
    """Time-series momentum: long if return over lookback bars > threshold %."""
    ret = df.close.pct_change(lookback) * 100
    sig = pd.Series(0.0, index=df.index)
    sig[ret > threshold] = 1
    sig[ret < -threshold] = -1
    return _apply_side(sig, allow_short)


# ----------------------------------------------------------------- registry
@dataclass
class StrategySpec:
    name: str
    func: Callable
    family: str
    description: str
    defaults: Dict
    grid: Dict[str, List] = field(default_factory=dict)  # parameter search space


STRATEGIES: Dict[str, StrategySpec] = {
    s.name: s
    for s in [
        StrategySpec("buy_and_hold", buy_and_hold, "benchmark",
                     "Hold the asset the whole time. Every strategy must beat this.", {}),
        StrategySpec("sma_cross", sma_cross, "trend",
                     "Fast/slow simple moving average crossover.",
                     {"fast": 10, "slow": 30},
                     {"fast": [5, 10, 20], "slow": [30, 50, 100]}),
        StrategySpec("ema_cross", ema_cross, "trend",
                     "Fast/slow exponential moving average crossover.",
                     {"fast": 9, "slow": 21},
                     {"fast": [5, 9, 12], "slow": [21, 34, 55]}),
        StrategySpec("macd", macd, "trend",
                     "MACD line vs signal line.",
                     {"fast": 12, "slow": 26, "signal": 9},
                     {"fast": [8, 12], "slow": [21, 26], "signal": [5, 9]}),
        StrategySpec("rsi_reversion", rsi_reversion, "mean_reversion",
                     "Buy when RSI is oversold, sell when it recovers.",
                     {"period": 14, "oversold": 30, "overbought": 70, "exit_level": 50},
                     {"period": [2, 7, 14], "oversold": [10, 20, 30], "exit_level": [50, 60]}),
        StrategySpec("bollinger_reversion", bollinger_reversion, "mean_reversion",
                     "Buy below the lower Bollinger band, exit at the mean.",
                     {"period": 20, "num_std": 2.0},
                     {"period": [10, 20, 40], "num_std": [1.5, 2.0, 2.5]}),
        StrategySpec("donchian_breakout", donchian_breakout, "breakout",
                     "Buy N-bar highs, exit on M-bar lows (Turtle-style).",
                     {"entry": 20, "exit": 10},
                     {"entry": [20, 40, 55], "exit": [10, 20]}),
        StrategySpec("vwap_reversion", vwap_reversion, "intraday",
                     "Fade deviations from session VWAP (intraday bars only).",
                     {"band_pct": 0.3},
                     {"band_pct": [0.15, 0.3, 0.5, 0.8]}),
        StrategySpec("momentum", momentum, "trend",
                     "Long when trailing return over lookback is positive.",
                     {"lookback": 20, "threshold": 0.0},
                     {"lookback": [10, 20, 50, 100], "threshold": [0.0, 0.5]}),
    ]
}


def get_signal(name: str, df: pd.DataFrame, params: Dict | None = None, allow_short=False) -> pd.Series:
    spec = STRATEGIES[name]
    p = {**spec.defaults, **(params or {})}
    return spec.func(df, allow_short=allow_short, **p).astype(float)
