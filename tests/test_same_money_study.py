import numpy as np
import pandas as pd

from autobot import same_money_study as sm


def _bh(n=600, seed=1):
    idx = pd.date_range("2022-01-03", periods=n, freq="B", tz="UTC")
    return pd.Series(np.random.default_rng(seed).normal(0.0005, 0.01, n), index=idx)


def test_holding_a_fixed_share_matches_the_same_money_hold():
    bh = _bh()
    p = pd.Series(0.3, index=bh.index)
    row = sm.compare(bh * 0.3, p, bh)
    assert row["in_market_%"] == 30.0
    assert abs(row["same_money"]["diff"]["mean_bps"]) < 1e-9
    assert not row["same_money"]["diff"]["significant"]
    assert abs(row["same_risk"]["hold_share_%"] - 30.0) < 0.1


def test_perfect_timing_beats_the_same_money_hold():
    bh = _bh()
    p = (bh > 0).astype(float)          # in only on up days: impossible, but must show as clearly better
    row = sm.compare(bh * p, p, bh)
    assert row["same_money"]["diff"]["significant"] and row["same_money"]["diff"]["mean_bps"] > 0
