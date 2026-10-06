"""Volatility targeting study: the replay trades a day late and only outside the band; forecasts
and the 'normal' level use the past only."""
import numpy as np
import pandas as pd

from autobot import vol_target_study as vt


def frame(n=40, r=0.0):
    idx = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({"px": 100.0, "r": r, "tbill": 0.0}, index=idx)


def test_replay_trades_a_day_late_and_only_outside_the_band():
    df = frame(6)
    t = pd.Series([0.30, 0.30, 0.33, 0.40, 0.40, 0.40], index=df.index)
    f = vt.replay(df, t)
    # first target known at day 0's close -> bought at day 1; 0.33 is inside the 5-point band; 0.40 (day 3) traded at day 4
    assert f.weight.round(4).tolist()[:5] == [0.0, 0.30, 0.30, 0.30, 0.40]
    assert (f.traded > 0).sum() == 2


def test_weights_drift_with_the_market():
    df = frame(4, r=0.0)
    df.loc[df.index[2], "r"] = 0.10
    f = vt.replay(df, pd.Series(0.30, index=df.index))
    w = 0.30 * (1 + 0.10 + vt.DIV_YIELD / 252) / (1 + 0.30 * (0.10 + vt.DIV_YIELD / 252))
    assert abs(f.weight.iloc[2] - w) < 1e-9


def test_target_and_forecasts_use_the_past_only():
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2020-01-01", periods=1100)
    r = pd.Series(rng.normal(0, 0.01, len(idx)), index=idx)
    px = 100 * (1 + r).cumprod()
    df = pd.DataFrame({"px": px, "r": r, "tbill": 0.0, "vix": 15 + 100 * r.abs().rolling(5, min_periods=1).mean()})
    w = vt.target_weight(vt.ewma_var(df.r))
    h = vt.har_forecast(df)
    df2 = df.copy()
    df2.iloc[900:, df2.columns.get_loc("r")] *= 5                        # a future shock
    pd.testing.assert_series_equal(vt.target_weight(vt.ewma_var(df2.r)).iloc[:900], w.iloc[:900])
    pd.testing.assert_series_equal(vt.har_forecast(df2).iloc[:900], h.iloc[:900])
    hv = vt.har_forecast(df, True)
    pd.testing.assert_series_equal(vt.har_forecast(df2, True).iloc[:900], hv.iloc[:900])


def test_vix_is_turned_into_a_daily_variance():
    assert abs(vt.vix_var(pd.Series([16.0])).iloc[0] - 0.16 ** 2 / 252) < 1e-12
