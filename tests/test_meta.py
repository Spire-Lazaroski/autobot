"""Meta-strategy checks: no look-ahead in regime and rotation logic, sensible regimes, cost rules.

(test_no_lookahead.py also runs the generic truncation test on every meta-strategy.)
"""
import numpy as np
import pandas as pd
import pytest

from autobot.backtest import CostModel, run_backtest
from autobot.data import synthetic_bars
from autobot.meta import classify_regimes, explain_regime, explain_rotation, perf_rotation, regime_switch
from autobot.strategies import get_signal


@pytest.fixture(scope="module")
def df():
    return synthetic_bars(["AAA"], "15Min", 120, seed=4)["AAA"]


def _shock(df, t, factor=1.6):
    """Everything after bar t explodes upward: a huge, unmistakable future to peek at."""
    out = df.copy()
    ramp = np.r_[np.ones(t + 1), np.linspace(1.05, factor, len(df) - t - 1)]
    for c in ("open", "high", "low", "close"):
        out[c] = out[c] * ramp
    return out


@pytest.mark.parametrize("t_frac", [0.4, 0.6, 0.8])
def test_rotation_ignores_future_shock(df, t_frac):
    t = int(len(df) * t_frac)
    params = {"candidates": ["ema_cross", "rsi_reversion", "momentum"], "lookback": 100, "rebalance": 20}
    base, shocked = perf_rotation(df, **params), perf_rotation(_shock(df, t), **params)
    pd.testing.assert_series_equal(base.iloc[: t + 1], shocked.iloc[: t + 1])
    a = explain_rotation(df, params)["names"][: t + 1]
    b = explain_rotation(_shock(df, t), params)["names"][: t + 1]
    assert a == b
    assert (shocked.iloc[t + 1:] != base.iloc[t + 1:]).any(), "shock should change something later"


@pytest.mark.parametrize("measure", ["adx", "er", "vr"])
def test_regimes_ignore_future_shock(df, measure):
    t = int(len(df) * 0.6)
    a = classify_regimes(df, trend_measure=measure, vol_lookback=300)
    b = classify_regimes(_shock(df, t, 3.0), trend_measure=measure, vol_lookback=300)
    assert (a.iloc[: t + 1] == b.iloc[: t + 1]).all()
    p = {"trend_measure": measure, "vol_lookback": 300}
    pd.testing.assert_series_equal(regime_switch(df, **p).iloc[: t + 1], regime_switch(_shock(df, t, 3.0), **p).iloc[: t + 1])


def test_regime_labels_make_sense():
    idx = pd.date_range("2026-01-02 14:30", periods=1200, freq="15min", tz="UTC")
    rng = np.random.default_rng(0)
    trend = 100 * np.exp(np.cumsum(0.002 + rng.normal(0, 0.0005, len(idx))))
    chop = 100 + np.sin(np.arange(len(idx)) / 3) + rng.normal(0, 0.05, len(idx))
    mk = lambda c: pd.DataFrame({"open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": 1e4}, index=idx)
    for measure in ["adx", "er"]:
        rt = classify_regimes(mk(trend), trend_measure=measure, high_vol_pct=1.01, vol_lookback=200).iloc[300:]
        rc = classify_regimes(mk(chop), trend_measure=measure, high_vol_pct=1.01, vol_lookback=200).iloc[300:]
        assert (rt == "trend").mean() > 0.9, measure
        assert (rc == "range").mean() > 0.9, measure


def test_high_vol_regime_goes_flat(df):
    names = explain_regime(df, {"high_vol_pct": 0.8, "vol_lookback": 300})
    pos = regime_switch(df, high_vol_pct=0.8, vol_lookback=300, trend_strategy="buy_and_hold", range_strategy="buy_and_hold")
    hv = np.array(names["regimes"]) == "high_vol"
    assert hv.any()
    assert (pos[hv] == 0).all() and (pos[~hv & (np.array(names["regimes"]) != "warmup")] == 1).all()


def test_switch_without_position_change_is_free(df):
    """Switching between two strategies that are both long must not pay any cost."""
    p = dict(trend_strategy="buy_and_hold", range_strategy="buy_and_hold", high_vol_strategy="buy_and_hold")
    pos = get_signal("regime_switch", df, p)
    assert len(set(explain_regime(df, p)["regimes"][200:])) >= 2  # it really does switch
    bt = run_backtest(df, pos, CostModel(0, 10))
    live = pos.shift(1).fillna(0)
    assert bt.turnover[live.diff().fillna(live) == 0].sum() == 0
    assert bt.turnover.sum() == pytest.approx(1.0)  # only the initial entry after warm-up


def test_meta_cannot_nest(df):
    with pytest.raises(ValueError, match="nested"):
        get_signal("perf_rotation", df, {"candidates": ["ensemble_vote", "ema_cross"]})
