"""
No-look-ahead checks for every registered strategy (base and meta).

If a signal at bar t depends only on bars <= t, then computing it on df[:t+1]
must give the same values as computing it on the full df, up to bar t.
Any use of future data (a negative shift, a centered window, a full-sample
statistic) breaks that equality.
"""
import numpy as np
import pandas as pd
import pytest

from autobot.data import synthetic_bars
from autobot.strategies import STRATEGIES, get_signal


@pytest.fixture(scope="module")
def intraday():
    return synthetic_bars(["AAA"], "15Min", 90, seed=3)["AAA"]


@pytest.fixture(scope="module")
def daily():
    return synthetic_bars(["BBB"], "1Day", 900, seed=5)["BBB"]


def _cuts(n):
    return sorted({n // 3, n // 2, 2 * n // 3, n - 7, n - 1})


def _assert_causal(name, df, allow_short):
    full = get_signal(name, df, allow_short=allow_short)
    for cut in _cuts(len(df)):
        part = get_signal(name, df.iloc[: cut + 1], allow_short=allow_short)
        pd.testing.assert_series_equal(part, full.iloc[: cut + 1], check_names=False,
                                       obj=f"{name} cut={cut} short={allow_short}")


@pytest.mark.parametrize("name", list(STRATEGIES))
@pytest.mark.parametrize("allow_short", [False, True])
def test_intraday_signal_is_causal(name, intraday, allow_short):
    _assert_causal(name, intraday, allow_short)


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_daily_signal_is_causal(name, daily):
    _assert_causal(name, daily, False)


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_signal_shape(name, intraday):
    sig = get_signal(name, intraday)
    assert sig.index.equals(intraday.index)
    assert not sig.isna().any()
    assert np.all(np.abs(sig.values) <= 1 + 1e-9)


def test_intraday_strategies_flat_overnight(intraday):
    from autobot.indicators import last_bar_of_session
    eod = last_bar_of_session(intraday)
    for name in ["opening_range_breakout", "intraday_momentum", "noise_area"]:
        sig = get_signal(name, intraday, allow_short=True)
        assert (sig[eod] == 0).all(), name
        assert (sig != 0).any(), f"{name} never trades on the test data"
