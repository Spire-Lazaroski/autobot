"""Walk-forward must only use the past: appending future bars cannot change earlier choices."""
import pandas as pd

from autobot.backtest import CostModel, walk_forward
from autobot.data import synthetic_bars


def _trim(data, n):
    return {s: df.iloc[:n] for s, df in data.items()}


def test_walk_forward_decisions_ignore_future():
    data = synthetic_bars(["A", "B"], "15Min", 90, seed=11)
    full = walk_forward(data, "sma_cross", 500, 120)
    n = len(data["A"]) - 300
    part = walk_forward(_trim(data, n), "sma_cross", 500, 120)
    w_full, w_part = full["windows"], part["windows"]
    done = w_part[~w_part.partial]
    assert len(done) >= 3
    for i, row in done.iterrows():
        assert w_full.loc[i, "params"] == row["params"]
        assert w_full.loc[i, "test_start"] == row["test_start"]
    # the stitched curve agrees wherever both runs have data
    eq_f, eq_p = full["equity"]["walk_forward"], part["equity"]["walk_forward"]
    common = eq_p.index[: -130]
    pd.testing.assert_series_equal(eq_f.loc[common], eq_p.loc[common], check_names=False)


def test_walk_forward_pays_costs_on_param_switch():
    data = synthetic_bars(["A"], "15Min", 90, seed=2)
    free = walk_forward(data, "ema_cross", 400, 100, costs=CostModel(0, 0))
    paid = walk_forward(data, "ema_cross", 400, 100, costs=CostModel(0, 10))
    assert paid["metrics"]["walk_forward"]["total_return_%"] < free["metrics"]["walk_forward"]["total_return_%"]
