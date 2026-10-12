import numpy as np
import pandas as pd

from autobot import sizing_study as s


def _df(n=400, seed=2):
    idx = pd.date_range("2018-01-01", periods=n, freq="B")
    r = pd.Series(np.random.default_rng(seed).normal(0.0004, 0.012, n), index=idx)
    return pd.DataFrame({"r": r, "px": (1 + r).cumprod(), "tbill": 0.0001})


def test_shares_stay_between_zero_and_one():
    df = _df()
    for lvl in (0.1, 0.5, 1.0):
        for m, t in s.shares(df, lvl, "daily").items():
            assert t.dropna().between(0, 1).all(), m


def test_fixed_share_earns_the_share_of_the_market_plus_cash():
    df = _df()
    f = s.replay(df, pd.Series(0.4, index=df.index), df.tbill, band=0.0)
    i = 10
    assert f.w.iloc[i] == 0.4
    assert abs(f.ret.iloc[i] - (0.4 * df.r.iloc[i] + 0.6 * 0.0001)) < 1e-5   # minus a tiny rebalancing cost


def test_decision_is_held_from_the_next_bar():
    df = _df()
    t = pd.Series(0.0, index=df.index)
    t.iloc[100:] = 1.0
    f = s.replay(df, t, None, band=0.0)
    assert f.w.iloc[100] == 0.0 and f.w.iloc[101] == 1.0


def test_return_at_a_drop_is_interpolated_and_none_outside():
    curve = [{"worst_drop_%": -10.0, "yearly_%": 4.0}, {"worst_drop_%": -20.0, "yearly_%": 8.0}]
    assert s.at_drop(curve, 15) == 6.0
    assert s.at_drop(curve, 30) is None
