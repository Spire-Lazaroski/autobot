"""ML study inputs: no look-ahead (cutting off the future never changes earlier rows, news is
counted only once received) and the time-of-day volume baseline uses earlier sessions only."""
import numpy as np
import pandas as pd

from autobot import ml_features as mf


def bars(days=30, seed=0):
    rng = np.random.default_rng(seed)
    idx = []
    for d in pd.bdate_range("2026-06-01", periods=days):
        idx += list(pd.date_range(d.tz_localize(mf.NY) + pd.Timedelta(hours=9, minutes=30), periods=78, freq="5min"))
    idx = pd.DatetimeIndex(idx).tz_convert("UTC")
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, len(idx))))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.001, "low": np.minimum(o, c) * 0.999,
                         "close": c, "volume": rng.integers(1000, 5000, len(idx)).astype(float)}, index=idx)


def news(df):
    t = df.index[[100, 400, 401, 1500, 2000]] + pd.Timedelta(minutes=2)
    return pd.DataFrame({"t_info": t, "relevance": ["main", "secondary", "mention", "main", "main"],
                         "materiality": [3, 4, 5, 2, 1], "sentiment": [1.0, -1.0, 1.0, 0.5, 0.0]})


def test_cutting_off_the_future_changes_nothing_earlier():
    df, spy = bars(days=75), bars(days=75, seed=1)       # 75 sessions: the Markov reading starts after 60
    nw = news(df)
    full = mf.build(df, spy, nw)
    assert full.turb.notna().sum() > 500
    for cut in (1700, 63 * 78 + 20, 71 * 78 + 5):
        part = mf.build(df.iloc[:cut], spy.iloc[:cut], nw)   # all the news is passed: only received stories may count
        pd.testing.assert_frame_equal(full.iloc[:cut], part, check_exact=False, rtol=1e-9)


def test_news_counts_only_after_it_was_received():
    df = bars(days=30)
    f = mf.news_features(df.index, news(df))
    # story received 2 minutes into bar 100: not yet known at the end of bar 99, known at the end of bar 100
    assert f.news_60m.iloc[99] == 0 and f.news_60m.iloc[100] == 1
    assert f.news_mat_60m.iloc[100] == 3
    assert f.news_60m.iloc[100 + 12] == 0                     # gone after 60 minutes
    # the 'mention' story at bar 401 is ignored; the secondary one at bar 400 counts
    assert f.news_60m.iloc[401] == 1 and f.news_sent_60m.iloc[401] == -1.0


def test_volume_baseline_uses_earlier_sessions_only():
    df = bars(days=12)
    v = mf.volume_vs_time_of_day(df)
    assert v.iloc[:78 * 5].isna().all()                       # needs 5 earlier sessions
    df2 = df.copy()
    df2.iloc[-78:, df2.columns.get_loc("volume")] *= 10       # the last day's own volume must not set its baseline
    v2 = mf.volume_vs_time_of_day(df2)
    np.testing.assert_allclose(v2.iloc[-78:].to_numpy(), v.iloc[-78:].to_numpy() * 10)
