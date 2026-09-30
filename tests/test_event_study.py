"""Event study core: no look-ahead entry, horizons that respect the session, abnormal
returns vs SPY, big-move detection that only uses the past, one count per symbol-day."""
import numpy as np
import pandas as pd
import pytest

from autobot import event_study as es


def bars(days=3, drift=0.0, seed=0, start="2026-03-02"):
    idx = []
    for d in pd.bdate_range(start, periods=days):
        base = pd.Timestamp(d.date()).tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30)
        idx += [base + pd.Timedelta(minutes=5 * i) for i in range(78)]
    idx = pd.DatetimeIndex(idx).tz_convert("UTC")
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.001, len(idx))))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c), "low": np.minimum(o, c), "close": c, "volume": 1e4}, index=idx)


@pytest.fixture
def panel():
    spy = bars(seed=1)
    return es.Panel(bars(seed=2), spy, False), spy


def test_entry_is_strictly_after_the_information(panel):
    p, _ = panel
    t0 = p.t[10]
    assert p.entry(t0) == 10            # arrived exactly at a bar start: that bar's open is after it
    assert p.entry(t0 + 10**9) == 11    # one second later: the next bar
    assert p.entry(p.t[-1] + 10**9) is None


def test_horizons_respect_the_session(panel):
    p, _ = panel
    last_of_day1 = p.day_last[0]
    h = p.horizons(last_of_day1 - 2)    # 3 bars before the close
    assert h["5m"] is not None and h["15m"] is not None and h["1h"] is None  # +1h would cross the close
    assert h["eod"] == pytest.approx(p.ar(last_of_day1 - 2, last_of_day1))
    assert h["1d"] == pytest.approx(p.ar(last_of_day1 - 2, p.day_last[1]))
    assert h["3d"] is None               # only 3 sessions in the data


def test_abnormal_return_is_stock_minus_spy(panel):
    p, spy = panel
    i0, i1 = 5, 20
    stock = p.close[i1] / p.open[i0] - 1
    market = spy.close.iloc[i1] / spy.open.iloc[i0] - 1
    assert p.ar(i0, i1) == pytest.approx(stock - market)


def test_outside_hours_story_enters_at_next_open(panel):
    p, _ = panel
    evening = p.t[p.day_last[0]] + 3 * 3600 * 10**9  # 3 h after the last bar of day 1
    i0 = p.entry(evening)
    assert i0 == p.day_first[1] and p.outside_hours(evening, i0)


def test_big_move_detection_ignores_the_future():
    spy = bars(days=30, seed=1)
    stock = bars(days=30, seed=3)
    k = 25 * 78 + 30
    stock.iloc[k:k + 3, stock.columns.get_loc("close")] *= 1.02  # a sharp 15-minute move on day 26
    stock["open"] = np.r_[stock.close.iloc[0], stock.close.iloc[:-1].to_numpy()]
    cut = 27 * 78
    full = es.spikes({"X": es.Panel(stock, spy, False)}, {})
    part = es.spikes({"X": es.Panel(stock.iloc[:cut], spy.iloc[:cut], False)}, {})
    early_full = full[full.time < stock.index[cut - 20].timestamp()][["time", "move", "sigma"]].reset_index(drop=True)
    early_part = part[part.time < stock.index[cut - 20].timestamp()][["time", "move", "sigma"]].reset_index(drop=True)
    pd.testing.assert_frame_equal(early_full, early_part)
    assert len(early_full) >= 1


def test_same_symbol_day_counts_once():
    t = pd.Timestamp("2026-03-02 15:00", tz="UTC").timestamp()
    df = pd.DataFrame({"symbol": ["AAPL"] * 3 + ["NVDA"], "entry_time": [t, t + 60, t + 120, t], "g": "x",
                       **{h: [0.01, 0.01, 0.01, -0.01] for h in es.HORIZONS}, "peak_1h": 0.0})
    row = es.summarise(df, "g")[0]
    assert row["n"] == 4 and row["days"] == 2
    assert row["horizons"]["1d"]["mean"] == pytest.approx(0.0)  # (+1% for AAPL's day, -1% for NVDA's) / 2
