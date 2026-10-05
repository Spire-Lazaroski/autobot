"""Markov switching (2-state Gaussian HMM): recovers known calm / turbulent stretches, labels the
turbulent state consistently, and the walk-forward probabilities never use later bars."""
import numpy as np
import pandas as pd

from autobot import markov


def test_fit_finds_the_two_states_and_filter_tracks_them():
    rng = np.random.default_rng(1)
    calm, wild = rng.normal(0, 1, 600), rng.normal(0, 4, 300)
    x = np.r_[calm, wild, calm[:300]]
    p = markov.fit(x)
    assert p["sd"][1] > 2.5 * p["sd"][0]                       # state 1 = turbulent
    prob = markov.filtered(x, p)
    assert prob[100:550].mean() < 0.2 and prob[650:880].mean() > 0.8 and prob[950:].mean() < 0.2


def test_walk_forward_is_causal():
    rng = np.random.default_rng(2)
    idx = pd.bdate_range("2026-01-05", periods=90)
    stamps = [d + pd.Timedelta(hours=14, minutes=30 + 5 * k) for d in idx for k in range(30)]
    vol = np.repeat(np.where(np.arange(90) % 20 < 10, 0.0005, 0.002), 30)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 1, len(stamps)) * vol))
    df = pd.DataFrame({"close": c}, index=pd.DatetimeIndex(stamps, tz="UTC"))
    full = markov.turbulence(df, train_sessions=30, refit_every=5)
    assert full.notna().sum() > 1000
    cut = len(df) * 2 // 3
    wild = df.copy()
    wild.iloc[cut:, 0] *= np.exp(np.cumsum(rng.normal(0, 0.01, len(df) - cut)))   # a different future
    part = markov.turbulence(wild, train_sessions=30, refit_every=5)
    pd.testing.assert_series_equal(full.iloc[:cut], part.iloc[:cut])
