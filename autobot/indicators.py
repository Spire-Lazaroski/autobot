"""
Indicator math shared by the strategies and the chart overlays.

Every function here is causal: the value at bar t uses bars <= t only.
`plot_indicators()` returns what the candlestick view draws for a strategy:
overlays on the price pane and extra panes (RSI, MACD, ...) with guide levels.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, List

import numpy as np
import pandas as pd

RTH_OPEN, RTH_CLOSE = 9 * 60 + 30, 16 * 60  # minutes after midnight, New York

# Overlay colours come from the Spire brand (web/src/theme.ts -> autobot/assets/brand.json).
_B = json.loads((Path(__file__).resolve().parent / "assets" / "brand.json").read_text(encoding="utf-8"))
COL = {
    "primary": _B["chart"]["ink"], "secondary": _B["chart"]["mid"], "up": _B["extra"]["profit"],
    "down": _B["extra"]["loss"], "osc": _B["series"][4], "vol": _B["series"][5], "hist": _B["extra"]["line-strong"],
}


# ----------------------------------------------------------------- basics
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / down.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def macd_lines(close: pd.Series, fast: int, slow: int, signal: int):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(close: pd.Series, period: int, num_std: float):
    mid = close.rolling(period).mean()
    sd = close.rolling(period).std()
    return mid - num_std * sd, mid, mid + num_std * sd


def donchian(df: pd.DataFrame, entry: int, exit: int):
    """Channel of the PREVIOUS bars (shifted), so a close above it is a real breakout."""
    return (df.high.rolling(entry).max().shift(1), df.low.rolling(exit).min().shift(1),
            df.low.rolling(entry).min().shift(1), df.high.rolling(exit).max().shift(1))


def ibs(df: pd.DataFrame) -> pd.Series:
    """Internal bar strength: where the close sits in the bar's range (0 = low, 1 = high)."""
    rng = (df.high - df.low).replace(0, np.nan)
    return ((df.close - df.low) / rng).fillna(0.5)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder's ADX: trend strength 0..100, direction-agnostic."""
    up, down = df.high.diff(), -df.low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = pd.concat([df.high - df.low, (df.high - df.close.shift()).abs(),
                    (df.low - df.close.shift()).abs()], axis=1).max(axis=1)
    a = 1 / n
    atr = tr.ewm(alpha=a, adjust=False).mean().replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=a, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=a, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    out = dx.ewm(alpha=a, adjust=False).mean()
    out.iloc[: 2 * n] = np.nan  # not meaningful until it has warmed up
    return out


def efficiency_ratio(close: pd.Series, n: int) -> pd.Series:
    """Kaufman efficiency ratio: net move / path length over n bars (1 = straight line, 0 = noise)."""
    path = close.diff().abs().rolling(n).sum().replace(0, np.nan)
    return (close - close.shift(n)).abs() / path


def variance_ratio(close: pd.Series, n: int, q: int = 4) -> pd.Series:
    """Lo-MacKinlay variance ratio over a rolling n-bar window.
    > 1 = returns persist (trending, Hurst > 0.5); < 1 = returns revert (ranging)."""
    r1 = np.log(close).diff()
    rq = np.log(close).diff(q)
    return rq.rolling(n).var() / (q * r1.rolling(n).var()).replace(0, np.nan)


def realized_vol(close: pd.Series, n: int) -> pd.Series:
    return close.pct_change().rolling(n).std()


def vol_percentile(close: pd.Series, n: int, lookback: int) -> pd.Series:
    """Where current realized vol ranks within its own trailing `lookback` bars (0..1)."""
    return realized_vol(close, n).rolling(lookback, min_periods=max(20, lookback // 4)).rank(pct=True)


# ----------------------------------------------------------------- session helpers
def ny_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    return df.index.tz_convert("America/New_York") if df.index.tz is not None else df.index


def session_day(df: pd.DataFrame) -> np.ndarray:
    return np.asarray(ny_index(df).date)


def minute_of_day(df: pd.DataFrame) -> pd.Series:
    ny = ny_index(df)
    return pd.Series(ny.hour * 60 + ny.minute, index=df.index)


def step_minutes(df: pd.DataFrame) -> float:
    if len(df) < 3:
        return 1440.0
    return float(pd.Series(df.index).diff().median().total_seconds() / 60)


def is_intraday(df: pd.DataFrame) -> bool:
    return step_minutes(df) < 390


def last_bar_of_session(df: pd.DataFrame) -> pd.Series:
    """True on the bar whose close is the 16:00 close. Known from the clock, so causal."""
    return minute_of_day(df) + step_minutes(df) >= RTH_CLOSE


def session_vwap(df: pd.DataFrame) -> pd.Series:
    day = session_day(df)
    tp = (df.high + df.low + df.close) / 3
    pv = (tp * df.volume).groupby(day).cumsum()
    vv = df.volume.groupby(day).cumsum().replace(0, np.nan)
    return (pv / vv).ffill()


def opening_range(df: pd.DataFrame, range_min: int):
    """High/low of the first `range_min` minutes of each session, NaN until the range is complete."""
    day = session_day(df)
    mins = minute_of_day(df)
    in_range = mins < RTH_OPEN + range_min
    done = mins + step_minutes(df) >= RTH_OPEN + range_min  # this bar's close ends the range
    hi = df.high.where(in_range).groupby(day).cummax().groupby(day).ffill()
    lo = df.low.where(in_range).groupby(day).cummin().groupby(day).ffill()
    return hi.where(done), lo.where(done)


def noise_bands(df: pd.DataFrame, lookback: int = 14, band_mult: float = 1.0):
    """Zarattini/Aziz/Barbon 'noise area': at each time of day, the average absolute move
    from the session open over the previous `lookback` sessions (today excluded).
    Returns (upper, lower) bands anchored on max/min(today's open, yesterday's close)."""
    day = pd.Series(session_day(df), index=df.index)
    mins = minute_of_day(df)
    day_open = df.open.groupby(day.values).transform("first")
    move = (df.close / day_open - 1).abs()
    tab = move.groupby([day.values, mins.values]).last().unstack()
    sigma_tab = tab.shift(1).rolling(lookback, min_periods=lookback).mean()  # previous sessions only
    sigma = sigma_tab.stack().rename("s")
    key = pd.MultiIndex.from_arrays([day.values, mins.values])
    s = pd.Series(sigma.reindex(key).values, index=df.index) * band_mult
    prev_close = df.close.groupby(day.values).last().shift(1)
    pc = pd.Series(prev_close.reindex(day.values).values, index=df.index)
    upper = np.maximum(day_open, pc.fillna(day_open)) * (1 + s)
    lower = np.minimum(day_open, pc.fillna(day_open)) * (1 - s)
    return upper, lower


def first_period_return(df: pd.DataFrame, signal_min: int) -> pd.Series:
    """Return from the previous session's close to `signal_min` minutes after the open.
    NaN on each day until that moment has passed."""
    day = session_day(df)
    mins = minute_of_day(df)
    prev_close = df.close.groupby(day).last().shift(1)
    pc = pd.Series(prev_close.reindex(day).values, index=df.index)
    at = mins + step_minutes(df) >= RTH_OPEN + signal_min
    r = (df.close / pc - 1).where(at & ~at.groupby(day).shift(1, fill_value=False))
    return r.groupby(day).ffill()


# ----------------------------------------------------------------- chart overlays
def _line(name, s, color=None, dash=None):
    return {"name": name, "values": s, "color": color, "dash": dash}


def _pane(name, series, levels=(), range_=None):
    return {"name": name, "series": series, "levels": list(levels), "range": range_}


PLOTS: Dict[str, Callable[[pd.DataFrame, Dict], Dict]] = {}


def plot(name):
    def deco(fn):
        PLOTS[name] = fn
        return fn
    return deco


@plot("sma_cross")
def _(df, p):
    return {"overlays": [_line(f"SMA {p['fast']}", sma(df.close, p["fast"]), COL['secondary']),
                         _line(f"SMA {p['slow']}", sma(df.close, p["slow"]), COL['primary'])]}


@plot("ema_cross")
def _(df, p):
    return {"overlays": [_line(f"EMA {p['fast']}", ema(df.close, p["fast"]), COL['secondary']),
                         _line(f"EMA {p['slow']}", ema(df.close, p["slow"]), COL['primary'])]}


@plot("macd")
def _(df, p):
    line, sig, hist = macd_lines(df.close, p["fast"], p["slow"], p["signal"])
    return {"panes": [_pane("MACD", [_line("hist", hist, COL['hist']), _line("macd", line, COL['primary']),
                                      _line("signal", sig, COL['secondary'])], [0])]}


@plot("rsi_reversion")
def _(df, p):
    return {"panes": [_pane(f"RSI {p['period']}", [_line("rsi", rsi(df.close, p["period"]), COL['osc'])],
                            [p["oversold"], p["exit_level"], p["overbought"]], [0, 100])]}


@plot("bollinger_reversion")
def _(df, p):
    lo, mid, hi = bollinger(df.close, p["period"], p["num_std"])
    return {"overlays": [_line("upper", hi, COL['primary'], "dot"), _line("mid", mid, COL['secondary']),
                         _line("lower", lo, COL['primary'], "dot")]}


@plot("donchian_breakout")
def _(df, p):
    hi, lo_exit, _, _ = donchian(df, p["entry"], p["exit"])
    return {"overlays": [_line(f"{p['entry']}-bar high", hi, COL['up'], "dot"),
                         _line(f"{p['exit']}-bar low", lo_exit, COL['down'], "dot")]}


@plot("vwap_reversion")
def _(df, p):
    v = session_vwap(df)
    b = p["band_pct"] / 100
    return {"overlays": [_line("VWAP", v, COL['secondary']), _line("band -", v * (1 - b), COL['primary'], "dot"),
                         _line("band +", v * (1 + b), COL['primary'], "dot")]}


@plot("momentum")
def _(df, p):
    roc = df.close.pct_change(p["lookback"]) * 100
    return {"panes": [_pane(f"ROC {p['lookback']} %", [_line("roc", roc, COL['primary'])],
                            [p["threshold"], -p["threshold"]])]}


@plot("connors_rsi2")
def _(df, p):
    return {"overlays": [_line(f"SMA {p['trend']}", sma(df.close, p["trend"]), COL['primary']),
                         _line(f"SMA {p['exit_sma']}", sma(df.close, p["exit_sma"]), COL['secondary'])],
            "panes": [_pane(f"RSI {p['rsi_period']}", [_line("rsi", rsi(df.close, p["rsi_period"]), COL['osc'])],
                            [p["entry"], 100 - p["entry"]], [0, 100])]}


@plot("ibs_reversion")
def _(df, p):
    return {"panes": [_pane("IBS", [_line("ibs", ibs(df), COL['osc'])], [p["low"], p["high"]], [0, 1])]}


@plot("opening_range_breakout")
def _(df, p):
    hi, lo = opening_range(df, p["range_min"])
    return {"overlays": [_line("OR high", hi, COL['up'], "dot"), _line("OR low", lo, COL['down'], "dot")]}


@plot("intraday_momentum")
def _(df, p):
    r = first_period_return(df, p["signal_min"]) * 100
    return {"panes": [_pane(f"first {p['signal_min']} min return %", [_line("ret", r, COL['primary'])], [0])]}


@plot("noise_area")
def _(df, p):
    up, lo = noise_bands(df, p["lookback"], p["band_mult"])
    return {"overlays": [_line("noise +", up, COL['up'], "dot"), _line("noise -", lo, COL['down'], "dot"),
                         _line("VWAP", session_vwap(df), COL['secondary'])]}


def plot_indicators(name: str, df: pd.DataFrame, params: Dict) -> Dict[str, List]:
    fn = PLOTS.get(name)
    out = fn(df, params) if fn else {}
    return {"overlays": out.get("overlays", []), "panes": out.get("panes", [])}
