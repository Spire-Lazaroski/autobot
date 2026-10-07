"""Relative-strength rule: momentum skips the latest month, top 3 only, trend filter and volatility caps,
and a decision never uses the month it is applied to."""
import numpy as np
import pandas as pd

from autobot import rs_study as rs


def frame(n=30, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.period_range("2000-01", periods=n, freq="M")
    R = pd.DataFrame(rng.normal(0.01, 0.04, (n, 5)), index=idx, columns=list("ABCDE"))
    R["A"] += 0.03                                         # A trends up strongly
    return R


def test_top3_skip_month_and_caps():
    R = frame()
    w = rs.weights(R, "plain")
    assert (w.iloc[13:].sum(axis=1).round(6) == 1.0).all() and (w.iloc[13:]["A"] > 0).all()
    R2 = R.copy()
    R2.iloc[20, 1] = 5.0                                  # a huge month for B in month 20 ...
    assert rs.weights(R2, "plain").iloc[20].equals(rs.weights(R, "plain").iloc[20])   # ... is skipped by month 20's decision
    full = rs.weights(R, "full")
    assert (full <= 1 / 3 + 1e-12).all().all() and (full.sum(axis=1) <= 1 + 1e-9).all()


def test_replay_applies_weights_the_next_month():
    R = frame(15)
    W = pd.DataFrame(0.0, index=R.index, columns=R.columns)
    W.iloc[5, 0] = 1.0                                    # decided at the end of month 5 -> held in month 6 only
    cash = pd.Series(0.0, index=R.index)
    f = rs.replay(R, W, cash, 0.0)
    assert f.ret.loc[R.index[6]] == R.iloc[6, 0] and f.ret.loc[R.index[5]] == 0.0
