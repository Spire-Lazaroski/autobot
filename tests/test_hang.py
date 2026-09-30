"""Hang protection: request timeouts, and a watchdog that keeps STOP, the loss limit and
the heartbeat honest when the main loop is stuck (the real failure: a half-closed socket
blocked the old bot for hours, ignoring STOP)."""
import os
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import yaml

import live
from autobot import liveconfig
from autobot.data import with_timeout
from tests.test_live import BASE, FakeTC


def test_requests_time_out_instead_of_hanging():
    from alpaca.trading.client import TradingClient
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)  # accepts the connection, never answers
    port = srv.getsockname()[1]
    tc = with_timeout(TradingClient("k", "s", paper=True, url_override=f"http://127.0.0.1:{port}"), timeout=(1, 1))
    t0 = time.time()
    with pytest.raises(Exception) as e:
        tc.get_clock()
    assert time.time() - t0 < 5
    assert "timed out" in str(e.value).lower() or "timeout" in type(e.value).__name__.lower()
    srv.close()


class StuckTC(FakeTC):
    """Every call blocks (like a dead socket) once `stuck` is set."""
    def __init__(self, equity="100000"):
        super().__init__()
        self.stuck, self.release = threading.Event(), threading.Event()
        self.equity = equity

    def get_clock(self):
        if self.stuck.is_set():
            self.release.wait(30)
            raise RuntimeError("released")
        return super().get_clock()

    def get_account(self):
        return SimpleNamespace(equity=self.equity, last_equity="100000", account_number="PA-TEST")


@pytest.fixture
def stuck_bot(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "LOGS", tmp_path)
    monkeypatch.setattr(live, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(liveconfig, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(liveconfig, "PID_FILE", tmp_path / "bot.pid")
    tc = StuckTC()
    tc.stuck.set()
    cfg = yaml.safe_load(yaml.safe_dump(BASE))
    bot = live.Bot(cfg, tc=tc)
    exits = []
    bot._exit = exits.append
    bot.step = 1 / 60  # 1-second bars so the loop enters step_once (and gets stuck) at once
    th = threading.Thread(target=bot.run, daemon=True)
    yield bot, tc, exits, th, tmp_path
    bot.halted = True  # let the main loop finish after the test
    tc.release.set()
    th.join(10)


def _wait(cond, secs=8):
    t = time.time() + secs
    while time.time() < t:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_watchdog_honours_stop_when_loop_is_stuck(stuck_bot):
    bot, tc, exits, th, tmp = stuck_bot
    th.start()
    assert _wait(lambda: (tmp / "bot.pid").exists())
    stop = tmp / "STOP"
    stop.touch()
    old = time.time() - 20
    os.utime(stop, (old, old))  # unhandled for a while
    assert _wait(lambda: exits == [0])
    assert tc.flattened == 1 and not stop.exists() and not (tmp / "bot.pid").exists()


def test_watchdog_enforces_loss_limit_when_stuck(stuck_bot):
    bot, tc, exits, th, tmp = stuck_bot
    tc.equity = "97000"  # -3% vs a 2% limit
    bot.watch_risk_after = 0.5
    th.start()
    assert _wait(lambda: tc.flattened == 1)
    assert bot.halted


def test_watchdog_exits_on_stall_without_trading(stuck_bot):
    bot, tc, exits, th, tmp = stuck_bot
    bot.stall_s = 2
    th.start()
    assert _wait(lambda: exits == [3])
    assert tc.flattened == 0 and tc.orders == []
    assert not (tmp / "bot.pid").exists()


def test_heartbeat_stops_when_loop_stalls(stuck_bot):
    bot, tc, exits, th, tmp = stuck_bot
    bot.stall_s = 3
    bot._exit = lambda code: None  # observe the heartbeat instead of exiting
    th.start()
    assert _wait(lambda: (tmp / "bot.pid").exists())
    time.sleep(4)
    age = time.time() - liveconfig.read_lock()["heartbeat"] if liveconfig.read_lock() else 99
    assert age >= 1.5  # no fresh heartbeat once the loop is stuck
