"""Trade rules: the churn brake stands aside after too many entries, no quick re-entry waits for a
new high, exits always pass, sessions start fresh, and no look-ahead."""
import numpy as np
import pandas as pd

from autobot import trade_rules as tr
from autobot.data import synthetic_bars
from autobot.strategies import get_signal


def bars(closes, start="2026-09-28 13:30"):
    idx = pd.date_range(start, periods=len(closes), freq="5min", tz="UTC")
    c = np.asarray(closes, float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=idx)


def test_churn_brake_blocks_the_third_entry_in_an_hour_and_stays_aside():
    df = bars([100] * 12)
    sig = pd.Series([1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 1], index=df.index, dtype=float)
    out = tr.apply(sig, df, {"churn_brake": {"max_entries": 2, "window_min": 60}})
    assert list(out) == [1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0]   # 3rd entry blocked; aside for the session


def test_brake_resets_next_session():
    a, b = bars([100] * 6, "2026-09-28 13:30"), bars([100] * 2, "2026-09-29 13:30")
    df = pd.concat([a, b])
    sig = pd.Series([1, 0, 1, 0, 1, 0, 1, 1], index=df.index, dtype=float)
    out = tr.apply(sig, df, {"churn_brake": {"max_entries": 2, "window_min": 60}})
    assert list(out[-2:]) == [1, 1]


def test_no_quick_reentry_needs_a_new_high():
    df = bars([100, 102, 101, 101.5, 102.5, 102])
    sig = pd.Series([1, 1, 0, 1, 1, 1], index=df.index, dtype=float)
    out = tr.apply(sig, df, {"no_quick_reentry": True})
    assert list(out) == [1, 1, 0, 0, 1, 1]       # back in only once a close beats 102 (the closed position's best)


def test_flip_becomes_exit_when_blocked_and_the_other_side_is_free():
    df = bars([100, 99, 98, 99, 97])
    sig = pd.Series([-1, -1, 1, -1, -1], index=df.index, dtype=float)
    out = tr.apply(sig, df, {"no_quick_reentry": True})
    assert list(out) == [-1, -1, 1, 0, -1]       # short re-entry waits for a close below 98


def test_no_look_ahead():
    df = synthetic_bars(["X"], "5Min", 30, seed=4)["X"]
    sig = get_signal("ema_cross", df, {}, allow_short=True)
    rules = {"churn_brake": {"max_entries": 2, "window_min": 60}, "no_quick_reentry": True}
    full = tr.apply(sig, df, rules)
    assert (full != sig).any()
    for cut in (len(df) // 3, len(df) - 1):
        pd.testing.assert_series_equal(tr.apply(sig.iloc[:cut + 1], df.iloc[:cut + 1], rules), full.iloc[:cut + 1], check_names=False)
