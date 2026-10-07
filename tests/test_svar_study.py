"""SVAR study: news is put in the bar that received it, and a planted news effect is recovered."""
import numpy as np
import pandas as pd
import pytest

from autobot import svar_study as sv
from tests.test_ml_features import bars


def test_news_lands_in_the_bar_that_received_it():
    idx = pd.date_range("2026-06-01 13:30", periods=4, freq="5min", tz="UTC")
    news = pd.DataFrame({"t_info": [idx[1] + pd.Timedelta(minutes=2), idx[1] + pd.Timedelta(minutes=5), idx[2] + pd.Timedelta(minutes=1)],
                         "relevance": ["main", "main", "mention"], "sentiment": [1.0, -0.5, 1.0]})
    f = sv.news_per_bar(idx, news)
    assert f["count"].tolist() == [0, 2, 0, 0] and f.tone.tolist() == [0, 0.5, 0, 0]   # the mention is ignored


def test_planted_news_effect_is_recovered():
    pytest.importorskip("statsmodels")
    df = bars(days=60, seed=3)
    rng = np.random.default_rng(3)
    hits = rng.choice(np.arange(100, len(df) - 30), 300, replace=False)
    news = pd.DataFrame({"t_info": df.index[hits] + pd.Timedelta(minutes=1), "relevance": "main", "sentiment": 1.0})
    c = df.close.to_numpy().copy()
    for h in sorted(hits):
        c[h + 1:] *= 1.002                                           # +20 bps in the bar after each story
    df = df.assign(close=c, open=c, high=c * 1.0005, low=c * 0.9995)
    r = sv.irf_summary(sv.frame(df, None, news), lags=3, h=6)
    p = r["shocks"]["news"]["price_cum_bps"]["after_2h"]
    assert p[0] > p[1] > 0                                           # positive and clearly so
