"""Shorts / take-profit study helpers: the settings grid, the causal trend label, and the
per-trade statistics."""
import numpy as np
import pandas as pd

from autobot import short_tp_study as st
from autobot.data import synthetic_bars


def test_grid_has_none_and_every_take_profit_stop_pair():
    g = st.combos("5Min")
    assert g[0] is None and len(g) == (len(st.GRID["5Min"]["tp"]) + 1) * (len(st.GRID["5Min"]["stop"]) + 1)
    assert st.label({"take_profit_pct": 0.5, "stop_pct": 0.25}) == "take profit 0.5% · stop 0.25%"
    assert st.label({"stop_pct": 1.0}) == "stop 1%" and st.label(None) == "none"


def test_trend_label_uses_only_earlier_days():
    df = synthetic_bars(["X"], "5Min", 120, seed=2)["X"]
    up = st.trend_up(df)
    days = sorted(up)
    later = df.copy()
    cut = days[-10]
    later.loc[np.asarray(later.index.tz_convert("America/New_York").date) >= cut, ["open", "high", "low", "close"]] *= 3
    assert all(st.trend_up(later)[d] == up[d] for d in days if d <= cut)   # a wild future can't change past labels


def test_trip_stats_net_is_gross_minus_cost():
    t = pd.DataFrame({"symbol": ["A"] * 4 + ["B"] * 4, "day": [1, 1, 2, 2, 1, 1, 2, 2],
                      "gross": [0.002, 0.001, -0.001, 0.003] * 2, "cost": [0.0004] * 8})
    s = st.trip_stats(t)
    assert s["trades"] == 8 and s["cost_bps"] == 4.0
    assert s["net_bps"]["mean"] == round(s["gross_bps"]["mean"] - 4.0, 2)
