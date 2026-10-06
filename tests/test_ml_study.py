"""ML study phase 2: targets stay inside the session and end at the close-out, the simple swing
rule uses earlier sessions only, and the entry / exit rules behave as described."""
import numpy as np
import pandas as pd

from autobot import ml_study as ms
from tests.test_ml_features import bars


def test_targets_stop_at_the_closeout_and_never_cross_days():
    df = bars(days=3)
    t = ms.targets(df)
    ny = df.index.tz_convert(ms.NY)
    m = ny.hour * 60 + ny.minute
    assert t.fwd_ret[m >= ms.CLOSEOUT_MIN].isna().all()           # the close-out bar and the 15:55 bar: no target
    i = int(np.flatnonzero(m == 15 * 60 + 40)[0])                 # 15:40: only 2 bars left to the 15:50 close-out bar
    assert np.isclose(t.fwd_ret.iloc[i], (df.close.iloc[i + 2] / df.close.iloc[i] - 1) * 100)
    assert np.isclose(t.fwd_ret.iloc[0], (df.close.iloc[12] / df.close.iloc[0] - 1) * 100)
    sw = (df.high.iloc[1:13].max() - df.low.iloc[1:13].min()) / df.close.iloc[0] * 100
    assert np.isclose(t.fwd_swing.iloc[0], sw)


def test_simple_swing_rule_uses_earlier_sessions_only():
    from autobot import ml_features as mf
    df = bars(days=12)
    f = mf.build(df, None, None, with_turbulence=False)
    y = ms.targets(df).fwd_swing
    a = ms.naive_swing(f, y)
    y2 = y.copy()
    y2.iloc[-78:] *= 5                                            # the last day's own outcome must not move its forecast
    np.testing.assert_allclose(ms.naive_swing(f, y2).iloc[-78:].to_numpy(), a.iloc[-78:].to_numpy())


def test_entry_and_exit_rules():
    idx = pd.date_range("2026-06-01 13:30", periods=6, freq="5min", tz="UTC")
    pred = pd.Series([0.01, 0.06, 0.02, -0.01, 0.06, np.nan], index=idx)     # in %: 1, 6, 2, -1, 6 bps
    assert ms.entry_signal(pred, np.ones(6, bool)).tolist() == [0, 1, 1, 0, 1, 0]
    base = pd.Series([1, 1, 1, 0, 1, 1], index=idx, dtype=float)
    pred = pd.Series([0.0, -0.06, 0.05, 0.0, 0.0, 0.0], index=idx)
    assert ms.exit_overlay(base, pred).tolist() == [1, 0, 0, 0, 1, 1]       # out until the strategy goes flat itself
