"""live.py with a fake broker: hot reload, rejected configs, closing dropped symbols,
the single-bot lock, and a full step. Nothing here talks to Alpaca."""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import live
from autobot import liveconfig
from autobot.data import synthetic_bars

BASE = {
    "backtest": {},
    "live": {
        "timeframe": "15Min", "poll_seconds": 5, "allow_short": False,
        "risk": {"max_daily_loss_pct": 2.0, "max_position_usd": 10000, "flatten_before_close_min": 0},
        "jobs": [
            {"name": "trend", "strategy": "ema_cross", "params": {"fast": 9, "slow": 21}, "symbols": ["SPY"], "allocation_pct": 20, "enabled": True},
            {"name": "rev", "strategy": "rsi_reversion", "params": {"period": 7}, "symbols": ["AAPL"], "allocation_pct": 10, "enabled": True},
        ],
    },
}


class FakeTC:
    def __init__(self, positions=None):
        self.pos = dict(positions or {})
        self.orders, self.closed, self.flattened = [], [], 0

    def get_account(self):
        return SimpleNamespace(equity="100000", last_equity="100000", account_number="PA-TEST")

    def get_clock(self):
        now = datetime.now(timezone.utc)
        return SimpleNamespace(is_open=True, timestamp=now, next_close=now + timedelta(hours=3))

    def get_all_positions(self):
        return [SimpleNamespace(symbol=s, qty=str(q)) for s, q in self.pos.items()]

    def submit_order(self, req):
        self.orders.append(req)
        return SimpleNamespace(id=f"o{len(self.orders)}")

    def close_position(self, sym):
        self.closed.append(sym)
        self.pos.pop(sym, None)

    def close_all_positions(self, cancel_orders=True):
        self.flattened += 1


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "LOGS", tmp_path)
    monkeypatch.setattr(liveconfig, "PID_FILE", tmp_path / "bot.pid")
    bars = synthetic_bars(["X"], "15Min", 60, seed=9)["X"]
    monkeypatch.setattr(live, "_download", lambda *a, **k: bars)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(BASE, sort_keys=False))
    return cfg_path


def _rewrite(path: Path, mutate):
    cfg = yaml.safe_load(path.read_text())
    mutate(cfg["live"])
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    t = time.time() + 5
    os.utime(path, (t, t))  # make sure the mtime visibly changes


def test_hot_reload_applies_and_logs(env):
    bot = live.Bot(yaml.safe_load(env.read_text()), tc=FakeTC(), cfg_path=env)

    def change(l):
        l["jobs"][0]["params"] = {"fast": 5, "slow": 34}
        l["jobs"][1]["strategy"] = "regime_switch"
        l["jobs"][1]["params"] = {}
        l["risk"]["max_position_usd"] = 5000
    _rewrite(env, change)
    bot.reload_config()
    assert bot.jobs[0]["params"] == {"fast": 5, "slow": 34}
    assert bot.jobs[1]["strategy"] == "regime_switch"
    assert bot.risk["max_position_usd"] == 5000
    text = " | ".join(c["change"] for c in bot.changes)
    assert "job 'trend' params" in text and "job 'rev' strategy: rsi_reversion -> regime_switch" in text
    assert "risk.max_position_usd: 10000 -> 5000" in text


def test_invalid_config_is_rejected(env):
    bot = live.Bot(yaml.safe_load(env.read_text()), tc=FakeTC(), cfg_path=env)
    _rewrite(env, lambda l: l["jobs"][1].update(symbols=["SPY"]))  # SPY in two enabled jobs
    bot.reload_config()
    assert bot.jobs[1]["symbols"] == ["AAPL"]
    assert bot.changes and bot.changes[-1]["change"].startswith("rejected")
    _rewrite(env, lambda l: l["jobs"][0].update(params={"nope": 1}))
    bot.reload_config()
    assert bot.jobs[0]["params"] == {"fast": 9, "slow": 21}


def test_dropped_symbol_is_closed(env):
    tc = FakeTC(positions={"AAPL": 12, "SPY": 3})
    bot = live.Bot(yaml.safe_load(env.read_text()), tc=tc, cfg_path=env)
    _rewrite(env, lambda l: l["jobs"][1].update(enabled=False))
    bot.reload_config()
    assert tc.closed == ["AAPL"]
    assert [j["name"] for j in bot.jobs] == ["trend"]


def test_step_trades_meta_job(env):
    tc = FakeTC()
    cfg = yaml.safe_load(env.read_text())
    cfg["live"]["jobs"][0].update(strategy="buy_and_hold", params={})
    cfg["live"]["jobs"][1].update(strategy="ensemble_vote", params={"method": "average",
                                  "candidates": ["buy_and_hold", "ema_cross"]})
    bot = live.Bot(cfg, tc=tc, cfg_path=None)
    bot.step_once()
    spy = [o for o in tc.orders if o.symbol == "SPY"]
    assert spy and spy[0].qty == int(10000 / bot_price(env))  # 20% of 100k, capped at max_position_usd
    status = (live.LOGS / "status.json").read_text()
    assert '"active"' in status and "ensemble_vote" in status


def bot_price(env):
    return float(live._download().close.iloc[-1])


def test_lock(env, monkeypatch):
    assert liveconfig.bot_state()["running"] is False
    liveconfig.write_lock(time.time())
    st = liveconfig.bot_state()
    assert st["running"] is True and st["pid"] == os.getpid()
    lock = liveconfig.read_lock()
    lock["heartbeat"] = time.time() - 600
    liveconfig.PID_FILE.write_text(__import__("json").dumps(lock))
    assert liveconfig.bot_state()["running"] is False  # stale heartbeat = not our bot
    liveconfig.release_lock()
    assert not liveconfig.PID_FILE.exists()


def test_pid_alive_does_not_kill():
    assert liveconfig.pid_alive(os.getpid())
    assert liveconfig.pid_alive(os.getpid())  # still alive after being checked
    assert not liveconfig.pid_alive(999999)


def test_paper_is_hardwired():
    src = Path(live.__file__).read_text(encoding="utf-8")
    assert "paper=True" in src and "paper=False" not in src


class DayTC(FakeTC):
    """Clock and account we can move to the next day."""
    def __init__(self):
        super().__init__()
        self.now = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)
        self.open, self.eq, self.last_eq = True, "97000", "100000"

    def get_clock(self):
        return SimpleNamespace(is_open=self.open, timestamp=self.now, next_close=self.now + timedelta(hours=5))

    def get_account(self):
        return SimpleNamespace(equity=self.eq, last_equity=self.last_eq, account_number="PA-TEST")


def test_loss_limit_halts_until_the_next_trading_day(env):
    tc = DayTC()
    bot = live.Bot(yaml.safe_load(env.read_text()), tc=tc)
    assert bot.check_risk() is False and bot.halted and tc.flattened == 1   # -3% vs a 2% limit
    assert bot.check_risk() is False and tc.flattened == 1                  # halted: no repeated flattening
    tc.now += timedelta(hours=12)
    tc.open = False
    assert bot.roll_day(tc.get_clock()) is False and bot.halted             # overnight, market closed: still halted
    tc.now += timedelta(hours=6)
    tc.open, tc.eq, tc.last_eq = True, "97100", "97000"                     # next session opens
    assert bot.roll_day(tc.get_clock()) is True
    assert not bot.halted and bot.day_start_equity == 97000.0               # new starting point: yesterday's close
    assert bot.check_risk() is True


def test_second_copy_exits_quietly(env, monkeypatch):
    monkeypatch.setattr(live, "bot_state", lambda: {"running": True, "unresponsive": False, "pid": 4242})
    monkeypatch.setattr(sys, "argv", ["live.py"])
    monkeypatch.setattr(live, "setup_logging", lambda: None)
    with pytest.raises(SystemExit) as e:
        live.main()
    assert e.value.code == live.EXIT_ALREADY_RUNNING
