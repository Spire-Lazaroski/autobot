"""Daily long-only study: the open-fill replay, and a walk-forward that never looks ahead."""
import numpy as np
import pandas as pd
import pytest
import yaml

from autobot import daily_study as ds


def days(n=400, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-03", periods=n, freq="B", tz="America/New_York").tz_convert("UTC")
    c = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    o = c * (1 + rng.normal(0, 0.003, n))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.002, "low": np.minimum(o, c) * 0.998, "close": c, "volume": 1e6}, index=idx)


def test_always_in_earns_close_to_close_and_one_trade_costs_twice():
    df = days()
    f = ds.simulate(df, pd.Series(1.0, index=df.index), cost_side=0.0002)
    exp = df.close.pct_change().to_numpy()[2:]
    assert np.allclose(f.ret.to_numpy()[2:], exp)                  # held: gap + day = close to close
    first = df.close.iloc[1] / df.open.iloc[1] - 1 - 0.0002 * ds.OPEN_MULT
    assert f.ret.iloc[1] == pytest.approx(first)                   # bought at day 1's open, 2x cost
    assert f.ret.iloc[0] == 0 and f.position.iloc[0] == 0


def test_signal_is_traded_at_the_next_open():
    df = days(10)
    t = pd.Series([0, 0, 1, 1, 0, 0, 0, 0, 0, 0], index=df.index, dtype=float)
    f = ds.simulate(df, t, 0.0)
    assert list(f.position) == [0, 0, 0, 1, 1, 0, 0, 0, 0, 0]
    assert f.ret.iloc[3] == pytest.approx(df.close.iloc[3] / df.open.iloc[3] - 1)   # in from the open of day 3
    assert f.ret.iloc[5] == pytest.approx(df.open.iloc[5] / df.close.iloc[4] - 1)   # out at day 5's open: keeps the gap


def test_walk_forward_ignores_the_future():
    cfg = yaml.safe_load(open("config.yaml"))
    df = days(ds.WARMUP + 3 * ds.TEST + 5)
    full = ds.symbol_runs("XTEST", df, cfg)["best of all (walk-forward)"]
    cut = ds.WARMUP + 2 * ds.TEST
    wild = df.copy()
    wild.iloc[cut:, :4] *= np.linspace(1, 3, len(df) - cut)[:, None]  # a different future
    part = ds.symbol_runs("XTEST", wild, cfg)["best of all (walk-forward)"]
    pd.testing.assert_series_equal(full.ret.iloc[:cut], part.ret.iloc[:cut])
