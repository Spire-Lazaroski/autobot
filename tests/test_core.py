"""Core holding rule: completed months only, the same decision for the live function and the
backtest signal, the macro study's formula, the switch level, and the next-open replay."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from autobot import core


def daily(month_closes, start="2024-01-01", per_month=20):
    """Business-day bars whose month-end closes are the given values (flat inside each month)."""
    rows, idx = [], []
    for k, c in enumerate(month_closes):
        p = pd.Period(start, "M") + k
        days = pd.bdate_range(p.start_time, p.end_time)[:per_month]
        for d in days:
            idx.append(pd.Timestamp(d).tz_localize("America/New_York").tz_convert("UTC"))
            rows.append(c)
    c = np.asarray(rows, float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=pd.DatetimeIndex(idx))


def test_decision_uses_completed_months_only():
    closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 50]
    df = daily(closes)
    d = core.decide(df)                      # the last month (50) is unfinished without as_of
    assert d["month"] == "2024-10" and d["in"] is True and d["close"] == 109
    d2 = core.decide(df, as_of=date(2024, 12, 2))   # December: November (50) is finished
    assert d2["month"] == "2024-11" and d2["in"] is False
    assert core.decide(daily(closes[:9])) is None   # fewer than 10 completed months


def test_average_includes_the_month_and_switch_level_is_the_other_nine():
    closes = [100, 110, 90, 105, 95, 100, 120, 80, 100, 101, 100]
    d = core.decide(daily(closes), as_of=date(2024, 12, 1))
    assert d["average"] == pytest.approx(np.mean(closes[-10:]))
    nine = np.mean(closes[-9:])
    assert d["switch_level"] == pytest.approx(nine)
    for nxt, want in ((nine + 0.01, True), (nine - 0.01, False)):   # the level really is the boundary
        assert core.decide(daily(closes + [nxt]), as_of=date(2025, 1, 2))["in"] is want


def test_backtest_signal_matches_the_live_decision_every_month():
    rng = np.random.default_rng(3)
    closes = list(100 * np.cumprod(1 + rng.normal(0.005, 0.05, 40)))
    df = daily(closes)
    sig = core.signal(df)
    ny = df.index.tz_convert("America/New_York").tz_localize(None).to_period("M")
    for k in range(10, 40):
        p = pd.Period("2024-01", "M") + k
        first_bar = df.index[ny == p][0]                            # the first session of month p trades ...
        prev = df.index[df.index < first_bar][-1]                   # ... the target from the previous close
        live = core.decide(df[ny < p], as_of=p.start_time.date())
        assert sig[prev] == (1.0 if live["in"] else 0.0)


def test_same_formula_as_the_macro_study():
    rng = np.random.default_rng(5)
    closes = pd.Series(100 * np.cumprod(1 + rng.normal(0.005, 0.05, 30)))
    macro = (closes > closes.rolling(10).mean()).astype(float).where(closes.rolling(10).mean().notna())
    df = daily(list(closes))
    sig = core.signal(df)
    ends = sig[np.r_[df.index.tz_convert("America/New_York").tz_localize(None).to_period("M")[1:]
                     != df.index.tz_convert("America/New_York").tz_localize(None).to_period("M")[:-1], False]]
    assert list(ends.dropna()) == list(macro.iloc[9:29])


def test_replay_trades_at_the_next_open_between_in_and_out():
    idx = pd.date_range("2025-01-06", periods=4, freq="B", tz="UTC")
    px = pd.DataFrame({"in_open": [100, 101, 99, 100.0], "in_close": [100, 102, 98, 101.0],
                       "out_open": [50, 50, 50.01, 50.02], "out_close": [50, 50.01, 50.02, 50.03]}, index=idx)
    f = core.replay(px, pd.Series([1.0, 0.0, 0.0, 0.0], index=idx), cost_side=0.0)
    assert f.w_in.tolist() == [0, 1, 0, 0]
    assert f.ret.iloc[1] == pytest.approx(102 / 101 - 1 + (50 / 50 - 1))              # day 1: in from the open
    assert f.ret.iloc[2] == pytest.approx((99 / 102 - 1) + (50.02 / 50.01 - 1))       # day 2: out at the open, keeps the gap
