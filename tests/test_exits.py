"""Exit layer (trailing exit + re-entry): the maths, each re-entry mode, no rebuy on the bar right
after an exit, no entries near the close, the same targets in the backtest and the live bot, no
look-ahead, validation, recalibration with exits, and the Lab study."""
import json

import numpy as np
import pandas as pd
import pytest
import yaml

import live
from autobot import exits, recalibrate as rc
from autobot.backtest import CostModel, run_backtest
from autobot.data import synthetic_bars
from autobot.liveconfig import validate_live
from autobot.strategies import get_signal
from tests.test_live import BASE, FakeTC, env  # noqa: F401


def bars(closes, start="2026-09-28 13:30", crypto=True):
    """5-minute bars with the given closes (crypto by default, so the session rules stay out of the way)."""
    idx = pd.date_range(start, periods=len(closes), freq="5min", tz="UTC")
    c = np.asarray(closes, dtype=float)
    df = pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=idx)
    df.attrs["symbol"] = "BTC/USD" if crypto else "XTEST"
    return df


def run(closes, signal, **cfg):
    df = bars(closes)
    st = exits.exit_states(pd.Series(signal, index=df.index, dtype=float), df, {"trail_pct": 0.25, "reentry": "both", **cfg})
    return st


def test_trail_trigger_maths():
    # peak 102 -> trail level 102 x 0.9975 = 101.745: a close of 101.75 holds, 101.74 sells
    st = run([100, 101, 102, 101.75, 101.74, 101.9], [1] * 6, reentry="fresh_cross")
    assert list(st.target) == [1, 1, 1, 1, 0, 0]
    assert st.trail.iloc[3] == pytest.approx(101.745)
    assert st.reason.iloc[4] == "trail_exit" and st.peak.iloc[2] == 102


def test_short_mirrors_the_trail():
    st = run([100, 99, 98, 98.24, 98.25], [-1] * 5, reentry="fresh_cross")  # trough 98 -> 98.245
    assert list(st.target) == [-1, -1, -1, -1, 0]


EXIT_AT = 3  # closes 100, 102, 103 (peak), 102.5 (< 102.7425: exit on bar 3)


@pytest.mark.parametrize("mode,closes,signal,back_on", [
    # fresh_cross: the strategy must go out and back in (out on bar 5, back in on bar 6)
    ("fresh_cross", [100, 102, 103, 102.5, 104, 104, 104, 104], [1, 1, 1, 1, 1, 0, 1, 1], 6),
    # new_high: a close above the 103 exit peak while the strategy says +1 (bar 5; bar 4 is too soon)
    ("new_high", [100, 102, 103, 102.5, 102.9, 103.2, 103.3, 103.3], [1] * 8, 5),
    # both: whichever first (here the new high on bar 5)
    ("both", [100, 102, 103, 102.5, 102.9, 103.2, 103.3, 103.3], [1] * 8, 5),
    # cooldown 3: bars 4, 5, 6 wait, bar 7 follows the strategy again
    ("cooldown", [100, 102, 103, 102.5, 102.6, 102.6, 102.6, 102.6, 102.6], [1] * 9, 7),
])
def test_each_reentry_mode(mode, closes, signal, back_on):
    st = run(closes, signal, reentry=mode, cooldown_bars=3)
    t = list(st.target)
    assert t[EXIT_AT] == 0 and st.reason.iloc[EXIT_AT] == "trail_exit"
    assert all(x == 0 for x in t[EXIT_AT:back_on]), t
    assert t[back_on] == 1 and st.reason.iloc[back_on] == "reentry"


def test_new_high_mode_ignores_a_fresh_cross_and_fresh_cross_ignores_a_new_high():
    closes, signal = [100, 102, 103, 102.5, 102, 102, 102], [1, 1, 1, 1, 0, 1, 1]
    assert run(closes, signal, reentry="new_high").target.iloc[-1] == 0
    assert run([100, 102, 103, 102.5, 103.5, 104, 104], [1] * 7, reentry="fresh_cross").target.iloc[-1] == 0


def test_no_rebuy_on_the_bar_right_after_a_trail_exit():
    # bar 4 jumps far above the exit peak with the strategy long: still no rebuy on bar 4
    st = run([100, 102, 103, 102.5, 110, 110], [1] * 6, reentry="both")
    assert st.target.iloc[4] == 0 and st.target.iloc[5] == 1


def test_no_new_entries_in_the_last_minutes():
    # stock bars 15:20-15:55 New York; the strategy turns long on the 15:35 bar (ends 15:40, 20 min left)
    idx = pd.date_range("2026-09-30 15:20", periods=8, freq="5min", tz="America/New_York").tz_convert("UTC")
    df = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0}, index=idx)
    sig = pd.Series([0, 0, 0, 1, 1, 1, 1, 1], index=idx, dtype=float)
    st = exits.exit_states(sig, df, {"trail_pct": 0.25, "no_entry_last_min": 30}, crypto=False)
    assert list(st.target) == [0] * 8 and st.reason.iloc[3] == "late"
    held = pd.Series([1] * 8, index=idx, dtype=float)                  # already long before: kept
    assert list(exits.exit_states(held, df, {"trail_pct": 0.25, "no_entry_last_min": 30}, crypto=False).target) == [1] * 8
    coin = exits.exit_states(sig, df, {"trail_pct": 0.25, "no_entry_last_min": 30}, crypto=True)  # crypto: no close
    assert coin.target.iloc[3] == 1


def test_off_means_unchanged():
    df = synthetic_bars(["X"], "5Min", 10, seed=1)["X"]
    sig = get_signal("ema_cross", df, {})
    for cfg in (None, {}, {"enabled": False, "trail_pct": 0.25}):
        pd.testing.assert_series_equal(exits.apply_exit_layer(sig, df, cfg), sig)
        pd.testing.assert_frame_equal(run_backtest(df, sig, CostModel(0, 2), cfg), run_backtest(df, sig, CostModel(0, 2)))


@pytest.mark.parametrize("mode", exits.REENTRY)
def test_no_look_ahead(mode):
    df = synthetic_bars(["X"], "5Min", 30, seed=4)["X"]
    sig = rc.flat_before_close(get_signal("ema_cross", df, {}), df, 5)
    cfg = {"trail_pct": 0.25, "reentry": mode, "cooldown_bars": 4}
    full = exits.apply_exit_layer(sig, df, cfg, crypto=False)
    assert (full != sig).any()  # the layer really acts on this data
    for cut in (len(df) // 3, len(df) // 2, len(df) - 9, len(df) - 1):
        part = exits.apply_exit_layer(sig.iloc[: cut + 1], df.iloc[: cut + 1], cfg, crypto=False)
        pd.testing.assert_series_equal(part, full.iloc[: cut + 1], check_names=False)


def test_validation():
    live_cfg = yaml.safe_load(yaml.safe_dump(BASE))["live"]
    live_cfg["jobs"][0]["exit"] = {"trail_pct": 0, "reentry": "maybe", "cooldown_bars": 0, "no_entry_last_min": 500, "x": 1}
    errs = " | ".join(validate_live(live_cfg))
    for part in ("trail_pct", "reentry must be one of", "cooldown_bars", "no_entry_last_min", "unknown settings x"):
        assert part in errs
    live_cfg["jobs"][0]["exit"] = {"trail_pct": 0.25, "reentry": "both"}
    assert validate_live(live_cfg) == []


def test_live_bot_uses_the_same_targets_as_the_backtest(env, monkeypatch):  # noqa: F811
    cfg = yaml.safe_load(env.read_text())
    ex = {"trail_pct": 0.15, "reentry": "cooldown", "cooldown_bars": 2, "no_entry_last_min": 0}
    for j in cfg["live"]["jobs"]:
        j["exit"] = ex
    bot = live.Bot(cfg, tc=FakeTC())
    bot.step_once()
    st = json.loads((live.LOGS / "status.json").read_text())
    for job in cfg["live"]["jobs"]:
        sym = job["symbols"][0]
        df = bot.recent_bars(sym, job)
        sig = rc.flat_before_close(get_signal(job["strategy"], df, job["params"]), df, 0)
        expected = exits.apply_exit_layer(sig, df, ex, crypto=False).iloc[-1]
        assert st["symbols"][sym]["signal"] == expected
        assert st["symbols"][sym]["exit"]["trail_pct"] == 0.15
        assert run_backtest(df, sig, CostModel(0, 2), ex).position.iloc[-1] == exits.apply_exit_layer(sig, df, ex, crypto=False).iloc[-2]


def test_exit_text_reads_like_the_examples():
    st = run([764, 765.27, 763.05], [1, 1, 1], reentry="both").iloc[-1]
    v = exits.exit_view_of(st, {"trail_pct": 0.25, "reentry": "both"}, 763.05)
    assert v["text"] == "Sold: price 763.05 fell 0.29% from the 765.27 high since entry (trail 0.25%)."
    w = run([764, 765.27, 763.05, 763.5], [1, 1, 1, 1], reentry="both").iloc[-1]
    assert exits.exit_view_of(w, {"trail_pct": 0.25, "reentry": "both"}, 763.5)["text"] == \
        "Stayed out: waiting for a fresh crossover or a close above 765.27."


def test_recalibration_can_include_exits_and_stays_causal(tmp_path):
    df = synthetic_bars(["XTEST"], "5Min", 70, seed=11)["XTEST"]
    keep = ["ema_cross", "rsi_reversion"]
    cfg = {"live": {"timeframe": "5Min", "risk": {"flatten_before_close_min": 5}, "jobs": []},
           "recalibration": {"lookback_days": 30, "warmup_days": 10, "train_days": 8, "test_days": 4, "min_trades": 3,
                             "include_exit": True, "exit_grid": {"trail_pct": [0.25, 0.6], "reentry": ["both"]},
                             "exclude": [n for n in rc.STRATEGIES if n not in keep]}}
    days = sorted(set(df.index.tz_convert("America/New_York").date))
    as_of = days[-12]
    past = df[np.asarray(df.index.tz_convert("America/New_York").date) <= as_of]
    a = rc.run(as_of, cfg, fetch=lambda s, tf, d: {"XTEST": past}, symbols=["XTEST"], out_dir=tmp_path, write=False)
    b = rc.run(as_of, cfg, fetch=lambda s, tf, d: {"XTEST": df}, symbols=["XTEST"], out_dir=tmp_path, write=False)
    assert a["symbols"]["XTEST"]["decision"] == b["symbols"]["XTEST"]["decision"] and a["proposed_jobs"] == b["proposed_jobs"]
    off = rc.run(as_of, {**cfg, "recalibration": {**cfg["recalibration"], "include_exit": False}},
                 fetch=lambda s, tf, d: {"XTEST": past}, symbols=["XTEST"], out_dir=tmp_path, write=False)
    assert "exit" not in off["symbols"]["XTEST"]["single"]["spec"]
    st = rc.SymbolStudy("XTEST", past, {**rc.settings(cfg)}, as_of)
    ta, tb, _, _ = st.win[0]
    chosen = st.with_exits({"strategy": "ema_cross", "params": {}}, ta, tb)
    options = [{"strategy": "ema_cross", "params": {}}] + [{"strategy": "ema_cross", "params": {}, "exit": e} for e in rc.exit_grid(st.r)]
    assert st.wscore(chosen, ta, tb)["score"] == max(st.wscore(o, ta, tb)["score"] for o in options)


def test_lab_exit_study_on_demo_data():
    from autobot import exit_study as es
    cfg = yaml.safe_load(yaml.safe_dump(BASE))
    cfg["live"]["timeframe"] = "5Min"
    res = es.run(cfg, 40, fetch=lambda s, tf, d: synthetic_bars(s, tf, d + 10, seed=6))
    assert len(res["rows"]) == 17 and res["rows"][0]["setting"] == "none"
    assert set(res["walk_forward"]["vs_none"]) >= {"mean_bps", "ci95_bps", "significant"}
    assert isinstance(res["beats_none_oos"], bool) and sum(res["walk_forward"]["choices"].values()) > 0
    json.dumps(res)  # plain JSON for the API
