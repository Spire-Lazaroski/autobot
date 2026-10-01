"""Profit lock: arms at the trigger, flattens and halts on the giveback, resets at roll_day; the
watchdog enforces it while the loop is stuck; the backtester applies the same rule (and the loss
limit) per New York day."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yaml

import live
from autobot import sizing
from autobot.backtest import CostModel, portfolio_backtest
from tests.test_hang import _wait, stuck_bot  # noqa: F401
from tests.test_live import BASE, FakeTC

LOCK = {"enabled": True, "trigger_pct": 1.5, "giveback_pct": 50}


class EqTC(FakeTC):
    def __init__(self):
        super().__init__()
        self.equity, self.day = 100000.0, datetime.now(timezone.utc)

    def get_account(self):
        return SimpleNamespace(equity=str(self.equity), last_equity="100000", account_number="PA-TEST")

    def get_clock(self):
        return SimpleNamespace(is_open=True, timestamp=self.day, next_close=self.day + timedelta(hours=3))


def bot_with(lock, tmp_path, monkeypatch):
    monkeypatch.setattr(live, "LOGS", tmp_path)
    cfg = yaml.safe_load(yaml.safe_dump(BASE))
    cfg["live"]["risk"]["profit_lock"] = lock
    tc = EqTC()
    return live.Bot(cfg, tc=tc), tc


def events(tmp_path):
    p = tmp_path / "decisions.jsonl"
    return [l for l in p.read_text().splitlines() if '"profit_lock"' in l] if p.exists() else []


def test_arms_halts_on_giveback_and_resets_next_day(tmp_path, monkeypatch):
    bot, tc = bot_with(LOCK, tmp_path, monkeypatch)
    for eq in (100800, 101600, 102400, 102000):     # +0.8% (not armed), +1.6% armed, peak +2.4%, +2.0%
        tc.equity = eq
        assert bot.check_risk()
    assert bot.day_peak_pnl == pytest.approx(2.4) and tc.flattened == 0
    tc.equity = 101150                               # +1.15% < 2.4% x 50% = 1.2%: lock
    assert not bot.check_risk() and bot.halted and tc.flattened == 1
    assert events(tmp_path) and "Profit lock" in events(tmp_path)[0]
    tc.equity = 103000
    assert not bot.check_risk() and tc.flattened == 1  # stays halted for the day, no second flatten
    tc.day += timedelta(days=1)                       # next trading day: lifted, peak forgotten
    assert bot.roll_day(tc.get_clock())
    assert not bot.halted and bot.day_peak_pnl is None


def test_no_lock_below_the_trigger_or_when_off(tmp_path, monkeypatch):
    bot, tc = bot_with(LOCK, tmp_path, monkeypatch)
    for eq in (101400, 100100, 100000):              # never reached +1.5%: never armed
        tc.equity = eq
        assert bot.check_risk()
    off, tc2 = bot_with({**LOCK, "enabled": False}, tmp_path, monkeypatch)
    for eq in (102400, 100100):
        tc2.equity = eq
        assert off.check_risk()
    assert tc.flattened == tc2.flattened == 0


def test_loss_limit_still_comes_first(tmp_path, monkeypatch):
    bot, tc = bot_with(LOCK, tmp_path, monkeypatch)
    tc.equity = 97500
    assert not bot.check_risk() and tc.flattened == 1 and not events(tmp_path)


def test_watchdog_enforces_the_lock_when_stuck(stuck_bot):  # noqa: F811
    bot, tc, exits, th, tmp = stuck_bot
    bot.risk["profit_lock"] = LOCK
    bot.day_peak_pnl = 2.4                          # armed earlier in the day
    tc.equity = "101000"                             # +1.0% < 1.2%
    bot.watch_risk_after = 0.5
    th.start()
    assert _wait(lambda: tc.flattened == 1)
    assert bot.halted


def test_validation_of_the_lock_settings():
    assert sizing.validate({"profit_lock": {"enabled": True, "trigger_pct": 0, "giveback_pct": 100}}) == [
        "risk.profit_lock.trigger_pct must be a positive number", "risk.profit_lock.giveback_pct must be between 0 and 100"]


# ----------------------------------------------------------------- backtester
def path(day_moves):
    """15-minute bars over consecutive days; each day's closes follow the given % moves per bar."""
    idx, closes, c = [], [], 100.0
    for d, moves in enumerate(day_moves):
        start = pd.Timestamp("2026-09-21 09:30", tz="America/New_York") + pd.Timedelta(days=d)
        for i, m in enumerate(moves):
            c *= 1 + m / 100
            idx.append(start + pd.Timedelta(minutes=15 * i))
            closes.append(c)
    s = pd.Series(closes, index=pd.DatetimeIndex(idx).tz_convert("UTC"))
    return pd.DataFrame({"open": s, "high": s, "low": s, "close": s, "volume": 1000.0})


RISK = {"max_daily_loss_pct": 2.0, "max_position_usd": 1e12, "profit_lock": LOCK}


def run(df, risk, rules=True):
    sig = pd.Series(1.0, index=df.index)
    return portfolio_backtest({"X": df}, {"X": sig}, risk, {"X": 100}, costs=CostModel(0, 0), account_rules=rules)


def test_backtest_profit_lock_halts_for_the_day_then_resumes():
    df = path([[0, 1, 1, -1, 1, 1], [0, 1, 1]])     # day 1: +2% then back to ~+1%: lock; day 2 trades again
    pb = run(df, RISK)
    ev = [e for e in pb["events"] if e["rule"] == "profit_lock"]
    assert len(ev) == 1 and ev[0]["peak_%"] == pytest.approx(2.0, abs=0.05) and ev[0]["pnl_%"] < 1.01
    ex = pb["exposure"]["X"].to_numpy()
    assert (ex[4:6] == 0).all()                      # flat from the bar after the lock, rest of the day
    assert (ex[7:] == 1).all()                       # next New York day: trading again
    off = run(df, {**RISK, "profit_lock": {**LOCK, "enabled": False}})
    assert not off["events"] and (off["exposure"]["X"].to_numpy()[1:] == 1).all()


def test_backtest_loss_limit_and_rules_off():
    df = path([[0, -1, -1.5, -1, 2, 2]])
    pb = run(df, RISK)
    assert [e["rule"] for e in pb["events"]] == ["loss_limit"]
    assert (pb["exposure"]["X"].to_numpy()[3:] == 0).all()
    assert not run(df, RISK, rules=False)["events"]


def test_lab_comparison_runs_the_jobs_three_ways():
    from autobot import account_rules as ar
    from autobot.data import synthetic_bars
    cfg = yaml.safe_load(yaml.safe_dump(BASE))
    res = ar.compare(cfg, 30, 1.0, 50, fetch=lambda s, tf, d: synthetic_bars(s, tf, d, seed=4))
    assert set(res["results"]) == {"off", "loss_limit", "profit_lock"} and res["symbols"] == ["SPY", "AAPL"]
    r = res["results"]
    assert r["off"]["halts_loss_limit"] == r["off"]["halts_profit_lock"] == 0
    assert r["loss_limit"]["halts_profit_lock"] == 0
    assert all(len(v["daily_equity"]) > 10 for v in r.values())
