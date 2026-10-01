"""Display-only trigger levels: exact for ema_cross (hand calculation), the nearest flip for every
strategy, never changes the live signal, and the bot writes it to status.json and the decision log."""
import json

import numpy as np
import pandas as pd
import pytest
import yaml

import live
from autobot import trigger
from autobot.data import synthetic_bars
from autobot.strategies import STRATEGIES, get_signal
from tests.test_live import FakeTC, env  # noqa: F401


def rising(n=200, step=0.05):
    idx = pd.date_range("2026-09-01 13:30", periods=n, freq="5min", tz="UTC")
    c = 100 + step * np.arange(n) + 0.02 * np.sin(np.arange(n))
    return pd.DataFrame({"open": c, "high": c + 0.01, "low": c - 0.01, "close": c, "volume": 1000.0}, index=idx)


def test_ema_cross_trigger_matches_the_hand_calculation():
    df = rising()
    fast, slow = 9, 21
    assert get_signal("ema_cross", df, {"fast": fast, "slow": slow}).iloc[-1] == 1   # long: fast EMA above slow
    F = df.close.ewm(span=fast, adjust=False).mean().iloc[-1]
    S = df.close.ewm(span=slow, adjust=False).mean().iloc[-1]
    af, as_ = 2 / (fast + 1), 2 / (slow + 1)
    # next close P makes the EMAs equal: af P + (1-af) F = as P + (1-as) S
    p_star = ((1 - as_) * S - (1 - af) * F) / (af - as_)
    t = trigger.trigger_level("ema_cross", df, {"fast": fast, "slow": slow})
    assert t["kind"] == "sell" and t["direction"] == "below" and t["flip_to"] == 0
    assert t["trigger_price"] == pytest.approx(p_star, abs=1e-3)
    assert t["distance_pct"] == pytest.approx((p_star / df.close.iloc[-1] - 1) * 100, abs=1e-3)


def test_never_changes_the_live_signal_or_the_bars():
    df = rising()
    before, sig = df.copy(), get_signal("ema_cross", df, {})
    trigger.trigger_level("ema_cross", df, {})
    pd.testing.assert_frame_equal(df, before)
    pd.testing.assert_series_equal(get_signal("ema_cross", df, {}), sig)


@pytest.fixture(scope="module")
def bars():
    return synthetic_bars(["X"], "15Min", 40, seed=12)["X"]


@pytest.mark.parametrize("name", sorted(STRATEGIES))
def test_every_strategy_gets_the_nearest_flip(name, bars):
    t = trigger.trigger_level(name, bars, {})
    if t is None:
        return  # no change within +-5% (e.g. buy and hold)
    sig = lambda p: float(get_signal(name, trigger._with_bars(bars, [p]), {}).iloc[-1])  # noqa: E731
    last = float(bars.close.iloc[-1])
    assert sig(last) == (t["flip_to"] if t["already_past"] else t["current_signal"])
    if t["trigger_price"] is None:
        return  # changes at the next close whatever the price within +-5%
    assert abs(t["distance_pct"]) <= 5.0 + 1e-9
    assert sig(t["trigger_price"]) == t["flip_to"] != t["current_signal"]
    keep = t["trigger_price"] * (1 + (1e-6 if t["direction"] == "below" else -1e-6))   # just across the boundary
    assert sig(keep) == t["current_signal"]
    if not t["already_past"]:  # the nearest change: everything between the close and the trigger keeps the signal
        assert sig(last + (t["trigger_price"] - last) * 0.999) == t["current_signal"]


def test_bars_if_flat_is_consistent_with_a_flat_price():
    df = rising()
    t = trigger.trigger_level("ema_cross", df, {})
    n = t["bars_if_flat"]
    assert n is not None and n >= 2                       # an average crossing needs a few bars
    near = df.close.iloc[-1] * (1 + t["distance_pct"] / 100 + 0.001)   # 0.1% short of a trigger below
    sig = lambda k: float(get_signal("ema_cross", trigger._with_bars(df, [near] * k), {}).iloc[-1])  # noqa: E731
    assert sig(n) != t["current_signal"] and sig(n - 1) == t["current_signal"]


def test_long_only_counts_a_short_signal_as_flat():
    df = rising()
    both = trigger.trigger_level("ema_cross", df, {}, allow_short=True)
    coin = trigger.trigger_level("ema_cross", df, {}, allow_short=True, long_only=True)
    assert both["flip_to"] == -1 and coin["flip_to"] == 0
    assert coin["trigger_price"] == pytest.approx(both["trigger_price"], abs=1e-3)


def test_entry_time_and_peak_since_entry():
    rows = [{"time": "2026-09-30T14:00:00+00:00", "symbol": "SPY", "side": "buy", "qty": "10"},
            {"time": "2026-09-30T15:00:00+00:00", "symbol": "SPY", "side": "close", "qty": "10"},
            {"time": "2026-09-30T16:00:00+00:00", "symbol": "SPY", "side": "buy", "qty": "5"},
            {"time": "2026-09-30T16:30:00+00:00", "symbol": "SPY", "side": "buy", "qty": "8"}]
    assert trigger.entry_time(rows, "SPY", 13) == pd.Timestamp("2026-09-30T16:00:00+00:00")
    assert trigger.entry_time(rows, "SPY", 7) is None         # not explained by the bot's trades
    idx = pd.date_range("2026-09-30 15:50", periods=6, freq="5min", tz="UTC")
    df = pd.DataFrame({"open": 100.0, "high": [99, 101, 105, 103, 102, 101.0], "low": 98.0, "close": [99, 100, 104, 102, 101, 100.0],
                       "volume": 1.0}, index=idx)
    p = trigger.peak_since(df, pd.Timestamp("2026-09-30T16:00:00+00:00"), 13)
    assert p["peak"] == 105 and p["gave_back_pct"] == pytest.approx((105 - 100) / 105 * 100, abs=1e-3)
    s = trigger.peak_since(df, pd.Timestamp("2026-09-30T16:00:00+00:00"), -13)  # short: lowest low
    assert s["peak"] == 98 and s["gave_back_pct"] == pytest.approx((100 - 98) / 98 * 100, abs=1e-3)


def test_bot_writes_trigger_to_status_and_decisions_without_changing_orders(env, monkeypatch):  # noqa: F811
    cfg = yaml.safe_load(env.read_text())
    plain_tc, with_tc = FakeTC(), FakeTC()
    monkeypatch.setattr(live, "TRIGGER_BUDGET_S", 0)          # no time: the bot still trades the same
    live.Bot(cfg, tc=plain_tc).step_once()
    st0 = json.loads((live.LOGS / "status.json").read_text())
    assert all(v["trigger"] is None and v["trigger_note"] for v in st0["symbols"].values())
    monkeypatch.setattr(live, "TRIGGER_BUDGET_S", 30)
    live.Bot(cfg, tc=with_tc).step_once()
    assert [(o.symbol, o.qty, o.side) for o in with_tc.orders] == [(o.symbol, o.qty, o.side) for o in plain_tc.orders]
    st = json.loads((live.LOGS / "status.json").read_text())
    assert set(st["symbols"]) == {"SPY", "AAPL"} and all("trigger" in v and "peak" in v for v in st["symbols"].values())
    decs = [json.loads(l) for l in (live.LOGS / "decisions.jsonl").read_text().splitlines() if '"decision"' in l]
    assert decs and all("trigger" in d and "peak_since_entry" in d for d in decs[-2:])
    assert all(pd.Timestamp(d["time"]) <= pd.Timestamp.now(tz="UTC") for d in decs)
