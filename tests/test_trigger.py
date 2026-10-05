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


@pytest.mark.parametrize("name", sorted(STRATEGIES))
def test_both_sides_are_true_boundaries_and_the_nearest_matches_trigger_level(name, bars):
    lv = trigger.trigger_levels(name, bars, {})
    assert lv["nearest"] == trigger.trigger_level(name, bars, {})
    sig = lambda p: float(get_signal(name, trigger._with_bars(bars, [p]), {}).iloc[-1])  # noqa: E731
    for kind in ("buy", "sell"):
        for t in lv[kind]:
            assert t["kind"] == kind
            if t["already_past"] or t["trigger_price"] is None:
                continue
            assert (t["flip_to"] > t["current_signal"]) == (kind == "buy")
            assert sig(t["trigger_price"]) == t["flip_to"]
            last = float(bars.close.iloc[-1])
            assert sig(last + (t["trigger_price"] - last) * 0.999) == t["current_signal"]
    if lv["nearest"] and not lv["nearest"]["already_past"]:
        assert len({t["direction"] for t in lv["buy"] + lv["sell"]}) == len(lv["buy"] + lv["sell"])  # at most one per side


def test_take_profit_above_for_rsi_reversion_and_a_rising_sell_level_below_for_donchian():
    idx = pd.date_range("2026-09-01 13:30", periods=60, freq="5min", tz="UTC")
    # a slide to a low RSI, then a small bounce: rsi_reversion is long and sells when the bounce goes far enough
    c = np.r_[np.full(40, 100.0), 100 - 0.4 * np.arange(1, 19), [93.0, 93.1]]
    dip = pd.DataFrame({"open": c, "high": c + 0.05, "low": c - 0.05, "close": c, "volume": 1000.0}, index=idx)
    p = {"period": 7, "oversold": 30, "exit_level": 50}
    assert get_signal("rsi_reversion", dip, p).iloc[-1] == 1
    lv = trigger.trigger_levels("rsi_reversion", dip, p)
    assert [t["direction"] for t in lv["sell"]] == ["above"] and lv["buy"] == []      # take profit, no stop
    # steady rise: donchian_breakout is long, sells only below its exit channel, nothing above
    up = rising(60, 0.1)
    d = {"entry": 20, "exit": 10}
    assert get_signal("donchian_breakout", up, d).iloc[-1] == 1
    lv = trigger.trigger_levels("donchian_breakout", up, d)
    assert [t["direction"] for t in lv["sell"]] == ["below"] and lv["buy"] == []
    assert lv["sell"][0]["trigger_price"] == pytest.approx(up.low.iloc[-10:].min(), abs=1e-3)


def test_exit_layer_trail_and_reentry_in_the_two_columns():
    sell_below = {"kind": "sell", "direction": "below", "distance_pct": -1.0, "trigger_price": 99.0, "source": None}
    sell_above = {"kind": "sell", "direction": "above", "distance_pct": 2.0, "trigger_price": 102.0}
    lv = {"nearest": sell_below, "buy": [], "sell": [sell_below, sell_above], "position": 1.0}
    near = trigger.with_exit_levels(lv, {"state": "in", "trail_level": 99.5}, 100.0)   # trail nearer than 99
    assert [(t["trigger_price"], t.get("source")) for t in near["sell"]] == [(99.5, "trail"), (102.0, None)]
    far = trigger.with_exit_levels(lv, {"state": "in", "trail_level": 98.0}, 100.0)    # strategy nearer: kept
    assert [t["trigger_price"] for t in far["sell"]] == [99.0, 102.0] and far["sell"][0]["source"] == "strategy"
    assert sell_below["source"] is None                                                # inputs untouched
    wait = trigger.with_exit_levels({**lv, "position": 0.0, "buy": [sell_above], "sell": []},
                                    {"state": "waiting", "waiting_for": "new_high", "peak": 103.0}, 100.0)
    assert [(t["kind"], t["trigger_price"], t["source"]) for t in wait["buy"]] == [("buy", 103.0, "re-entry")] and wait["sell"] == []
    assert trigger.with_exit_levels(lv, None, 100.0) is lv


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
    assert all(set(v["triggers"]) == {"buy", "sell"} for v in st["symbols"].values())
    decs = [json.loads(l) for l in (live.LOGS / "decisions.jsonl").read_text().splitlines() if '"decision"' in l]
    assert decs and all("trigger" in d and "triggers" in d and "peak_since_entry" in d for d in decs[-2:])
    assert all(pd.Timestamp(d["time"]) <= pd.Timestamp.now(tz="UTC") for d in decs)


def test_take_profit_and_stop_levels_in_the_sell_column():
    view = {"state": "in", "entry": 100.0, "take_profit_level": 101.0, "stop_level": 99.5, "trail_level": None}
    lv = trigger.with_exit_levels({"nearest": None, "buy": [], "sell": [], "position": 1.0}, view, 100.2)
    assert sorted((t["direction"], t["trigger_price"], t["source"]) for t in lv["sell"]) == [("above", 101.0, "take-profit"), ("below", 99.5, "stop")]
    assert lv["buy"] == []
    short = {"state": "in", "entry": 100.0, "take_profit_level": 99.0, "stop_level": 100.5, "trail_level": None}
    lv = trigger.with_exit_levels({"nearest": None, "buy": [], "sell": [], "position": -1.0}, short, 99.8)
    assert sorted((t["kind"], t["direction"], t["source"]) for t in lv["buy"]) == [("buy", "above", "stop"), ("buy", "below", "take-profit")]
    assert trigger.with_exit(None, view, 100.2)["source"] == "stop"            # 99.5 is nearer than 101
