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


def test_small_change_and_validation():
    assert tr.small_change(68, 69, 10) and tr.small_change(69, 68, 10)       # the 1-share USO nudges
    assert not tr.small_change(0, 68, 10) and not tr.small_change(68, 0, 10)  # entries and exits always pass
    assert not tr.small_change(68, -68, 10) and not tr.small_change(68, 69, 0)
    assert tr.validate({"no_quick_reentry": True, "min_change_pct": 10}) == []
    errs = " | ".join(tr.validate({"no_quick_reentry": "yes", "min_change_pct": 150, "x": 1}))
    assert "no_quick_reentry" in errs and "min_change_pct" in errs and "unknown settings x" in errs


def test_live_bot_uses_the_same_rule_as_the_study(monkeypatch, tmp_path):
    import yaml
    import live
    from tests.test_live import BASE, FakeTC
    from autobot import liveconfig
    monkeypatch.setattr(live, "LOGS", tmp_path)
    monkeypatch.setattr(liveconfig, "PID_FILE", tmp_path / "bot.pid")
    closes = [100.0] * 56 + [100, 102, 101, 101.5]                       # one session: in, out, wants back in below 102
    df = bars(closes)
    want = pd.Series([0.0] * 56 + [1, 1, 0, 1], index=df.index)
    monkeypatch.setattr(live, "Context", lambda strat, d, p, s: type("C", (), {"signal": want.reindex(d.index), "active": [None] * len(d)})())

    def run(rules):
        cfg = yaml.safe_load(yaml.safe_dump(BASE))
        cfg["live"]["jobs"] = [cfg["live"]["jobs"][0]]
        if rules:
            cfg["live"]["trade_rules"] = rules
        tc = FakeTC()
        bot = live.Bot(cfg, tc=tc)
        monkeypatch.setattr(bot, "recent_bars", lambda *a: df)
        monkeypatch.setattr(live, "_download", lambda *a, **k: df)
        bot.step_once()
        return [o for o in tc.orders if o.symbol == "SPY"]

    assert len(run(None)) == 1                                            # control: without the rule it buys back
    assert run({"no_quick_reentry": True}) == []                          # with it, the re-entry at 101.5 waits
