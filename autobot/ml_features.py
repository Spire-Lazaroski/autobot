"""
Machine-learning study, phase 1: the inputs a model may see at each 5-minute bar.
Diagnosis only: nothing here changes the bot.

    python -m autobot.ml_features                 # build all symbols -> data/ml/features_{SYM}.pkl.gz + a sample day
    python -m autobot.ml_features --sample 2026-09-30 --symbol NVDA

Every input on bar t uses only what was known when bar t ended (the moment the bot decides):
bars up to and including t, news received by the end of bar t, earlier sessions' volume.
tests/test_ml_features.py cuts off the future and checks that earlier rows don't change.

Groups (the economy is left out on purpose; phase 4 adds it to measure what it adds):
  price    returns over 1-24 bars, bar range vs its average, place in the day's range, gap, vs VWAP
  volume   volume vs normal for that time of day (previous 20 sessions), recent volume trend
  indic    RSI, MACD histogram, Bollinger position, ADX, efficiency ratio, volatility rank
  context  time of day, weekday, Markov P(turbulent), SPY's moves, stock minus SPY, daily trend
  news     stories in the last 60 minutes / today, importance, tone, minutes since the last story
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from . import markov

NY = "America/New_York"
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "ml"
BAR = pd.Timedelta(minutes=5)
NEWS_WINDOW = pd.Timedelta(minutes=60)

GROUPS: Dict[str, List[str]] = {
    "price": ["ret_1", "ret_3", "ret_6", "ret_12", "ret_24", "ret_open", "gap", "range_atr", "atr_pct",
              "pos_day", "vwap_atr", "close_loc"],
    "volume": ["vol_tod", "vol_trend"],
    "indic": ["rsi14", "macd_atr", "boll_z", "adx14", "er10", "vol_rank"],
    "context": ["minute", "weekday", "turb", "spy_ret_1", "spy_ret_6", "spy_ret_open", "rel_12", "rel_open",
                "prev_day_ret", "trend20d"],
    "news": ["news_60m", "news_today", "news_mat_60m", "news_sent_60m", "news_since_min"],
}
COLUMNS = [c for g in GROUPS.values() for c in g]

PLAIN = {   # one line each, for the sample-day print and the write-up
    "ret_1": "return of the last bar (%)", "ret_3": "last 15 minutes (%)", "ret_6": "last 30 minutes (%)",
    "ret_12": "last hour (%)", "ret_24": "last 2 hours (%)", "ret_open": "since today's open (%)",
    "gap": "today's open vs yesterday's close (%)", "range_atr": "this bar's range / average range",
    "atr_pct": "average bar range, 20 bars (% of price)", "pos_day": "place in today's range (0 low, 1 high)",
    "vwap_atr": "distance from today's average price, in average ranges", "close_loc": "close within the bar (0 low, 1 high)",
    "vol_tod": "volume / normal volume at this time of day", "vol_trend": "volume of last 6 bars / last 24 bars",
    "rsi14": "RSI 14", "macd_atr": "MACD histogram, in average ranges", "boll_z": "Bollinger position (std devs from 20-bar average)",
    "adx14": "trend strength (ADX 14)", "er10": "efficiency (straight line 1, noise 0)", "vol_rank": "volatility rank vs last ~2 days",
    "minute": "minutes since the open", "weekday": "weekday (0 Monday)", "turb": "Markov P(turbulent)",
    "spy_ret_1": "SPY last bar (%)", "spy_ret_6": "SPY last 30 minutes (%)", "spy_ret_open": "SPY since the open (%)",
    "rel_12": "stock minus SPY, last hour (%)", "rel_open": "stock minus SPY since the open (%)",
    "prev_day_ret": "yesterday's return (%)", "trend20d": "yesterday's close vs its 20-day average (%)",
    "news_60m": "stories in the last 60 minutes", "news_today": "stories since midnight",
    "news_mat_60m": "most important story, last 60 minutes (0-5)", "news_sent_60m": "sum of story tone, last 60 minutes",
    "news_since_min": "minutes since the last story (capped at 1,440)",
}


# ----------------------------------------------------------------- pieces
def _pct(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a / b - 1) * 100


def volume_vs_time_of_day(df: pd.DataFrame, sessions: int = 20) -> pd.Series:
    """Volume / the median volume at the same time of day over the previous `sessions` sessions."""
    ny = df.index.tz_convert(NY)
    slot, day = ny.hour * 60 + ny.minute, np.asarray(ny.date)
    tab = pd.DataFrame({"v": df.volume.to_numpy(), "slot": slot, "day": day}).pivot_table(
        index="day", columns="slot", values="v", aggfunc="sum")
    base = tab.rolling(sessions, min_periods=5).median().shift(1)        # earlier sessions only
    key = pd.MultiIndex.from_arrays([day, slot])
    norm = base.stack().reindex(key).to_numpy()
    return pd.Series(df.volume.to_numpy() / np.where(norm > 0, norm, np.nan), index=df.index)


def daily_context(df: pd.DataFrame) -> pd.DataFrame:
    """Yesterday's return and yesterday's close vs its 20-day average, from the 5-minute closes."""
    day = pd.Series(np.asarray(df.index.tz_convert(NY).date), index=df.index)
    dc = df.close.groupby(day.to_numpy()).last()
    prev = pd.DataFrame({"prev_day_ret": dc.pct_change() * 100, "trend20d": _pct(dc, dc.rolling(20).mean())}).shift(1)
    return prev.reindex(day.to_numpy()).set_index(df.index)


def news_features(index: pd.DatetimeIndex, news: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Per bar: stories (main / secondary relevance) received by the END of that bar."""
    out = pd.DataFrame(0.0, index=index, columns=GROUPS["news"])
    out["news_since_min"] = 1440.0
    if news is None or news.empty:
        return out
    n = news[news.relevance != "mention"].sort_values("t_info")
    t = n.t_info.to_numpy(dtype="datetime64[ns]").astype("int64")
    mat = n.materiality.fillna(0).to_numpy(float)
    sen = n.sentiment.fillna(0).to_numpy(float)
    end = (index + BAR).to_numpy(dtype="datetime64[ns]").astype("int64")
    hi = np.searchsorted(t, end, side="right")                           # stories received by bar end
    lo = np.searchsorted(t, end - NEWS_WINDOW.value, side="right")
    ny = (index + BAR).tz_convert(NY)
    midnight = pd.DatetimeIndex(ny.normalize()).tz_convert("UTC").to_numpy(dtype="datetime64[ns]").astype("int64")
    mid = np.searchsorted(t, midnight, side="right")
    csen = np.concatenate([[0.0], np.cumsum(sen)])
    out["news_60m"] = hi - lo
    out["news_today"] = hi - mid
    out["news_sent_60m"] = csen[hi] - csen[lo]
    out["news_mat_60m"] = [mat[a:b].max() if b > a else 0.0 for a, b in zip(lo, hi)]
    last = np.where(hi > 0, t[np.maximum(hi - 1, 0)], np.iinfo("int64").min)
    since = (end - last) / 6e10
    out["news_since_min"] = np.where(hi > 0, np.minimum(since, 1440.0), 1440.0)
    return out


# ----------------------------------------------------------------- all inputs for one symbol
def build(df: pd.DataFrame, spy: Optional[pd.DataFrame] = None, news: Optional[pd.DataFrame] = None,
          with_turbulence: bool = True) -> pd.DataFrame:
    """One row per regular-hours 5-minute bar, columns COLUMNS (plus 'day'). Causal."""
    c = df.close
    day = pd.Series(np.asarray(df.index.tz_convert(NY).date), index=df.index)
    new_day = day != day.shift(1)
    f = pd.DataFrame(index=df.index)
    for n in (1, 3, 6, 12, 24):
        f[f"ret_{n}"] = _pct(c, c.shift(n))
    day_open = df.open.groupby(day.to_numpy()).transform("first")
    f["ret_open"] = _pct(c, day_open)
    prev_close = c.shift(1).where(new_day).ffill().where(day.shift(1).notna())
    f["gap"] = _pct(day_open, prev_close).where(day.ne(day.iloc[0]))
    tr = pd.concat([df.high - df.low, (df.high - c.shift()).abs(), (df.low - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(20).mean()
    f["range_atr"] = (df.high - df.low) / atr.replace(0, np.nan)
    f["atr_pct"] = atr / c * 100
    hi = df.high.groupby(day.to_numpy()).cummax()
    lo = df.low.groupby(day.to_numpy()).cummin()
    f["pos_day"] = ((c - lo) / (hi - lo).replace(0, np.nan)).fillna(0.5)
    f["vwap_atr"] = (c - ind.session_vwap(df)) / atr.replace(0, np.nan)
    f["close_loc"] = ind.ibs(df)
    f["vol_tod"] = volume_vs_time_of_day(df)
    f["vol_trend"] = df.volume.rolling(6).mean() / df.volume.rolling(24).mean().replace(0, np.nan)
    f["rsi14"] = ind.rsi(c, 14)
    f["macd_atr"] = ind.macd_lines(c, 12, 26, 9)[2] / atr.replace(0, np.nan)
    _, mid, up = ind.bollinger(c, 20, 1.0)
    f["boll_z"] = (c - mid) / (up - mid).replace(0, np.nan)
    f["adx14"] = ind.adx(df, 14)
    f["er10"] = ind.efficiency_ratio(c, 10)
    f["vol_rank"] = ind.vol_percentile(c, 20, 156)
    ny = df.index.tz_convert(NY)
    f["minute"] = ny.hour * 60 + ny.minute - 570
    f["weekday"] = ny.weekday
    f["turb"] = markov.turbulence(df) if with_turbulence else np.nan
    if spy is not None:
        sc = spy.close.reindex(df.index)
        sday_open = spy.open.reindex(df.index).groupby(day.to_numpy()).transform("first")
        f["spy_ret_1"] = _pct(sc, sc.shift(1))
        f["spy_ret_6"] = _pct(sc, sc.shift(6))
        f["spy_ret_open"] = _pct(sc, sday_open)
        f["rel_12"] = f.ret_12 - _pct(sc, sc.shift(12))
        f["rel_open"] = f.ret_open - f.spy_ret_open
    else:
        for k in ("spy_ret_1", "spy_ret_6", "spy_ret_open", "rel_12", "rel_open"):
            f[k] = np.nan
    f = f.join(daily_context(df))
    f = f.join(news_features(df.index, news))
    f = f[COLUMNS]
    f["day"] = day
    return f


# ----------------------------------------------------------------- the study's data
def load(symbols: Optional[List[str]] = None):
    """The same 2-year 5-minute bars as the other studies, plus the keyword-scored news."""
    from . import cost_study as cs
    from .event_study import news_events
    from .short_tp_study import cached_symbols
    syms = symbols or cached_symbols()
    data = cs.Data(sorted(set(syms) | {"SPY"}))
    try:
        ev = news_events(with_first=False)
    except Exception:  # noqa: BLE001  (no news database: news inputs stay at zero)
        ev = pd.DataFrame()
    return syms, data, ev


def build_all(symbols: Optional[List[str]] = None, progress=print) -> Dict[str, pd.DataFrame]:
    syms, data, ev = load(symbols)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    spy = data.df5["SPY"]
    out = {}
    for s in syms:
        nw = ev[ev.symbol == s] if len(ev) else None
        f = build(data.df5[s], spy, nw)
        f.to_pickle(OUT_DIR / f"features_{s}.pkl.gz")
        out[s] = f
        progress(f"{s}: {len(f):,} bars, {f.day.nunique()} sessions, {f[COLUMNS].notna().all(axis=1).mean():.0%} complete rows")
    (OUT_DIR / "features_meta.json").write_text(json.dumps(
        {"as_of": str(data.as_of), "symbols": syms, "groups": GROUPS, "plain": PLAIN}, indent=1), encoding="utf-8")
    return out


def sample(f: pd.DataFrame, day: str, times=("09:55", "11:30", "14:00", "15:30")) -> List[str]:
    d = f[f.day == pd.Timestamp(day).date()]
    ny = d.index.tz_convert(NY).strftime("%H:%M")
    cols = [t for t in times if t in set(ny)]
    rows = d[ny.isin(cols)][COLUMNS].T
    rows.columns = cols
    lines = []
    for g, names in GROUPS.items():
        lines.append(f"-- {g}")
        for n in names:
            vals = "  ".join(f"{v:>9.3f}" if pd.notna(v) else f"{'-':>9}" for v in rows.loc[n])
            lines.append(f"{PLAIN[n][:52]:<52} {vals}")
    return [f"{'(bar starting, New York)':<52} " + "  ".join(f"{c:>9}" for c in cols)] + lines


def main():
    ap = argparse.ArgumentParser(description="ML study phase 1: build the per-bar inputs")
    ap.add_argument("--symbol", nargs="*")
    ap.add_argument("--sample", metavar="YYYY-MM-DD", help="print one day's inputs for the first symbol")
    a = ap.parse_args()
    fs = build_all(a.symbol)
    if a.sample:
        s = (a.symbol or list(fs))[0]
        print(f"\n{s} on {a.sample}")
        print("\n".join(sample(fs[s], a.sample)))


if __name__ == "__main__":
    main()
