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
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from . import indicators as ind

# ----------------------------------------------------------------- helpers
_ema = ind.ema
_rsi = ind.rsi


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
    vwap = ind.session_vwap(df)
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


# ----------------------------------------------------------------- from published research
# These are here because they are well documented, not because they are known to work now.
# Published edges usually shrink after publication; judge them on your own test_sharpe.
def connors_rsi2(df, trend=200, rsi_period=2, entry=5, exit_sma=5, allow_short=False, **_):
    """Connors RSI(2): in an uptrend (close > SMA trend), buy a very oversold short RSI,
    exit when the close gets back above a short SMA. Designed for daily bars."""
    r = ind.rsi(df.close, rsi_period)
    long_trend, exit_ma = ind.sma(df.close, trend), ind.sma(df.close, exit_sma)
    le = (df.close > long_trend) & (r < entry)
    lx = df.close > exit_ma
    se, sx = ((df.close < long_trend) & (r > 100 - entry), df.close < exit_ma) if allow_short else (None, None)
    return _state_machine(le, lx, se, sx)


def ibs_reversion(df, low=0.2, high=0.8, allow_short=False, **_):
    """Internal bar strength: buy a close near the bar's low, exit on a close near its high.
    Documented mainly on daily bars of equity index ETFs."""
    v = ind.ibs(df)
    le, lx = v < low, v > high
    se, sx = (v > high, v < low) if allow_short else (None, None)
    return _state_machine(le, lx, se, sx)


def opening_range_breakout(df, range_min=30, allow_short=False, **_):
    """Opening range breakout: after the first range_min minutes, go with a break of that
    range; stop out on the opposite side; always flat at the close. Stocks, intraday bars only."""
    if not ind.is_intraday(df):
        return pd.Series(0.0, index=df.index)
    hi, lo = ind.opening_range(df, range_min)
    eod = ind.last_bar_of_session(df)
    le, lx = (df.close > hi) & ~eod, (df.close < lo) | eod
    se, sx = ((df.close < lo) & ~eod, (df.close > hi) | eod) if allow_short else (None, None)
    return _state_machine(le, lx, se, sx)


def intraday_momentum(df, signal_min=30, hold_min=30, allow_short=False, **_):
    """Gao/Han/Li/Zhou: the return from yesterday's close to the first signal_min minutes
    predicts the last hold_min minutes. Hold in that direction into the close, then flat."""
    if not ind.is_intraday(df):
        return pd.Series(0.0, index=df.index)
    step = ind.step_minutes(df)
    mins = ind.minute_of_day(df)
    r = ind.first_period_return(df, signal_min)
    # signal on bar t is held over bar t+1, so start one bar before the window and stop one bar early
    window = (mins + step >= ind.RTH_CLOSE - hold_min) & ~ind.last_bar_of_session(df)
    sig = np.sign(r).where(window, 0).fillna(0)
    return _apply_side(sig, allow_short)


def noise_area(df, lookback=14, band_mult=1.0, allow_short=False, **_):
    """Zarattini/Aziz/Barbon 'Beat the Market': trade breaks out of the normal intraday
    'noise area' around the open, trail a stop at the band or VWAP, flat at the close."""
    if not ind.is_intraday(df):
        return pd.Series(0.0, index=df.index)
    up, lo = ind.noise_bands(df, lookback, band_mult)
    vwap = ind.session_vwap(df)
    eod = ind.last_bar_of_session(df)
    le = (df.close > up) & ~eod
    lx = (df.close < np.maximum(up, vwap)) | eod | up.isna()
    se, sx = None, None
    if allow_short:
        se = (df.close < lo) & ~eod
        sx = (df.close > np.minimum(lo, vwap)) | eod | lo.isna()
    return _state_machine(le, lx, se, sx)


# ----------------------------------------------------------------- registry
@dataclass
class StrategySpec:
    name: str
    func: Callable
    family: str
    description: str
    defaults: Dict
    grid: Dict[str, List] = field(default_factory=dict)  # parameter search space
    warmup_days: int = 0      # calendar days of history the live bot must load (0 = default)
    source: str = ""          # where the idea comes from (shown in the UI)
    explain: Optional[Callable] = None  # meta only: (df, params, allow_short) -> per-bar active labels


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
        StrategySpec("connors_rsi2", connors_rsi2, "mean_reversion",
                     "Buy RSI(2) dips while above the 200 SMA; exit above a 5 SMA. Daily bars.",
                     {"trend": 200, "rsi_period": 2, "entry": 5, "exit_sma": 5},
                     {"rsi_period": [2, 3], "entry": [5, 10, 15], "exit_sma": [5, 10]},
                     warmup_days=400, source="Connors & Alvarez, Short Term Trading Strategies That Work (2008)"),
        StrategySpec("ibs_reversion", ibs_reversion, "mean_reversion",
                     "Buy closes near the bar's low, sell closes near its high. Daily ETF bars.",
                     {"low": 0.2, "high": 0.8},
                     {"low": [0.1, 0.2, 0.3], "high": [0.7, 0.8, 0.9]},
                     source="Pagonidis (2013); Pal & Bhattacharya, arXiv 2306.12434 (2023)"),
        StrategySpec("opening_range_breakout", opening_range_breakout, "intraday",
                     "Trade the break of the first N minutes' range, flat at the close.",
                     {"range_min": 30},
                     {"range_min": [5, 15, 30, 60]},
                     source="Zarattini, Barbon & Aziz, A Profitable Day Trading Strategy (2024)"),
        StrategySpec("intraday_momentum", intraday_momentum, "intraday",
                     "First half-hour return sets the direction for the last half-hour.",
                     {"signal_min": 30, "hold_min": 30},
                     {"signal_min": [30, 60], "hold_min": [30, 60]},
                     source="Gao, Han, Li & Zhou, Market Intraday Momentum, JFE (2018)"),
        StrategySpec("noise_area", noise_area, "intraday",
                     "Break out of the usual intraday noise band; VWAP trailing stop; flat at close.",
                     {"lookback": 14, "band_mult": 1.0},
                     {"lookback": [10, 14, 20], "band_mult": [0.8, 1.0, 1.2]},
                     warmup_days=30, source="Zarattini, Aziz & Barbon, Beat the Market, SFI 24-97 (2024)"),
    ]
}


def get_signal(name: str, df: pd.DataFrame, params: Dict | None = None, allow_short=False) -> pd.Series:
    spec = STRATEGIES[name]
    p = {**spec.defaults, **(params or {})}
    return spec.func(df, allow_short=allow_short, **p).astype(float)
