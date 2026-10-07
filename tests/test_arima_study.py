"""ARIMA check: the trading rule's costs and timing, and the pattern tests on known series."""
import numpy as np
import pandas as pd
import pytest

from autobot import arima_study as ar


def test_rule_pays_costs_on_every_change_and_starts_each_day_flat():
    pred = np.array([5.0, 5.0, -5.0, 1.0])
    r = np.array([2.0, 2.0, -3.0, 9.0])
    minute = np.full(4, 600)
    day = np.array([1, 1, 1, 1])
    out = ar.rule(pred, r, minute, day, thr=2.0, side_cost_bps=2.0)
    # long, long, short (flip costs 2 x 2), then flat (|1| < 2; closing costs 2)
    assert out.tolist() == [2.0 - 2.0, 2.0, 3.0 - 4.0, 0.0 - 2.0]


def test_pattern_tests_see_a_built_in_reversal():
    pytest.importorskip("scipy")
    rng = np.random.default_rng(0)
    e = rng.normal(0, 10, 20000)
    r = e[1:] - 0.5 * e[:-1]                                  # a bounce: each move partly undone next bar
    ac = ar.autocorr(r)
    assert ac["acf"][0] < -0.3 and ac["ljung_box_p"] < 1e-6
    day = np.repeat(np.arange(len(r) // 78 + 1), 78)[:len(r)]
    assert ar.variance_ratio(pd.Series(r), day, 6)["vr"] < 0.7
    noise = rng.normal(0, 10, 20000)
    assert ar.autocorr(noise)["ljung_box_p"] > 0.001
