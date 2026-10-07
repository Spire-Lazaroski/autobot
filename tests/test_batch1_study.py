"""Batch 1: the shared trade simulator (stop first, a cent through for targets, close-out, costs)
and a few of the rules on hand-made bars."""
import numpy as np
import pandas as pd
import pytest

from autobot import batch1_study as b1


def make(rows, start="2026-06-01 13:30"):
    idx = pd.date_range(start, periods=len(rows), freq="5min", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1000.0
    B = b1.Bars(df, 0.0002)
    B.test_days = set(B.days)
    return B


def test_stop_counts_first_and_target_needs_a_cent_through():
    B = make([[100, 100, 100, 100], [100, 101.5, 98.5, 100], [100, 101.0, 99.9, 101], [101, 101.02, 100.9, 101]])
    t = b1.simulate(B, [{"i": 0, "px": 100.0, "side": 1, "stop": 99.0, "target": 101.0}])
    assert t[0]["kind"] == "stop" and t[0]["net"] == pytest.approx(-0.01 - 0.0004)
    t = b1.simulate(B, [{"i": 1, "px": 100.0, "side": 1, "stop": 99.0, "target": 101.0}])
    assert t[0]["kind"] == "target" and t[0]["net"] == pytest.approx(0.01 - 0.0004)   # 101.02 is a cent through, 101.0 is not


def test_closeout_and_one_trade_at_a_time():
    rows = [[100, 100.2, 99.9, 100]] * 78
    B = make(rows)
    t = b1.simulate(B, [{"i": 2, "px": 100.0, "side": -1, "stop": 105, "target": 90}, {"i": 3, "px": 100.0, "side": 1, "stop": 95, "target": 110}])
    assert len(t) == 1 and t[0]["kind"] == "close" and t[0]["net"] == pytest.approx(-0.0002 - 0.0004)   # close-out costs twice


def test_gap_fade_and_sweep_rules():
    day1 = [[100, 100.5, 99.5, 100]] * 78
    day2 = [[101.5, 101.6, 99.95, 100.2]] + [[100.2, 100.3, 100.1, 100.2]] * 77            # +1.5% gap, fills in the first bar
    idx = pd.date_range("2026-06-01 13:30", periods=78, freq="5min", tz="UTC").append(pd.date_range("2026-06-02 13:30", periods=78, freq="5min", tz="UTC"))
    df = pd.DataFrame(day1 + day2, columns=["open", "high", "low", "close"], index=idx).assign(volume=1000.0)
    B = b1.Bars(df, 0.0002)
    B.test_days = set(B.days)
    e = b1.gap_entries(B, "fade_large")
    assert len(e) == 1 and e[0]["side"] == -1 and e[0]["target"] == 100 and e[0]["open_fill"]
    t = b1.simulate(B, e)
    assert t[0]["kind"] == "target"
    sw = b1.sweep_entries(B, "prev_day")
    assert all(x["i"] >= 78 for x in sw)


def test_profile_value_area_holds_most_of_the_weight():
    h = np.array([101, 101, 101, 105.0]); l = np.array([99, 99, 99, 104.0]); w = np.array([1, 1, 1, 1.0])
    poc, val, vah = b1.profile(h, l, w, 100.0)
    assert 99 <= val < poc < vah <= 101.2                      # the lone bar at 104-105 is outside the value area
