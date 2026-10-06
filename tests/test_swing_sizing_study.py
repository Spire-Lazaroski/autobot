"""Swing sizing backtest: sizes use earlier sessions only, are set when a trade opens and kept."""
import numpy as np
import pandas as pd

from autobot import swing_sizing_study as ss
from tests.test_ml_features import bars


def test_size_is_set_at_entry_and_kept():
    idx = pd.date_range("2026-06-01 13:30", periods=7, freq="5min", tz="UTC")
    base = pd.Series([0, 1, 1, 1, 0, 1, 1], index=idx, dtype=float)
    w = pd.Series([9, 0.5, 2.0, 2.0, 9, np.nan, 1.5], index=idx)
    assert ss.sized(base, w).tolist() == [0, 0.5, 0.5, 0.5, 0, 1.0, 1.0]


def test_normal_level_and_daily_atr_use_earlier_sessions_only():
    df = bars(days=30)
    day = np.asarray(df.index.tz_convert(ss.NY).date)
    x = pd.Series(np.linspace(1, 2, len(df)), index=df.index)
    w = ss.relative_size(x, day)
    x2 = x.copy()
    x2.iloc[-78:] *= 3                                             # today's own level must not move today's normal
    np.testing.assert_allclose(ss.relative_size(x2, day).iloc[-78:], np.clip(w.iloc[-78:] / 3, ss.LO, ss.HI))
    a = ss.daily_atr_pct(df)
    df2 = df.copy()
    df2.iloc[-78:, df2.columns.get_loc("high")] *= 1.5              # the last day's range must not change its own ATR
    np.testing.assert_allclose(ss.daily_atr_pct(df2).iloc[-78:], a.iloc[-78:])
    assert a.iloc[:78 * 20].isna().all() and a.iloc[78 * 21:].notna().all()


def test_each_session_opens_a_new_trade_with_a_new_size():
    idx = pd.DatetimeIndex(["2026-06-01 19:50", "2026-06-01 19:55", "2026-06-02 13:30", "2026-06-02 13:35"], tz="UTC")
    base = pd.Series(1.0, index=idx)                                   # signal stays 'in' overnight
    w = pd.Series([0.5, 2.0, 1.5, 0.3], index=idx)
    assert ss.sized(base, w).tolist() == [0.5, 0.5, 1.5, 1.5]
