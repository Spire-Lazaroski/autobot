"""news_collector start-up: it waits for the network and a failed catch-up doesn't stop live collection."""
import sys

import pytest

import news_collector as nc


@pytest.fixture
def started(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(nc, "PID_FILE", tmp_path / "news_collector.pid")   # never the real one
    monkeypatch.setattr(nc, "LOGS", tmp_path)
    monkeypatch.setattr(nc, "heartbeat_loop", lambda: None)
    monkeypatch.setattr(nc, "poll_loop", lambda symbols: None)
    monkeypatch.setattr(nc, "wait_for_network", lambda host, log=None: calls.append(("net", host)) or True)
    monkeypatch.setattr(nc, "run_stream", lambda symbols: calls.append(("stream", tuple(symbols))))
    monkeypatch.setattr(nc.news, "configured_symbols", lambda: ["AAPL"])
    monkeypatch.setattr(nc.logging, "basicConfig", lambda **kw: None)
    return calls


def test_failed_catch_up_still_collects_live(started, monkeypatch):
    def boom(symbols, days):
        raise ConnectionError("getaddrinfo failed")
    monkeypatch.setattr(nc, "catch_up", boom)
    monkeypatch.setattr(sys, "argv", ["news_collector.py"])
    nc.main()
    assert started == [("net", "data.alpaca.markets"), ("stream", ("AAPL",))]


def test_backfill_only_still_reports_the_failure(started, monkeypatch):
    def boom(symbols, days):
        raise ConnectionError("getaddrinfo failed")
    monkeypatch.setattr(nc, "catch_up", boom)
    monkeypatch.setattr(sys, "argv", ["news_collector.py", "--backfill-only"])
    with pytest.raises(ConnectionError):
        nc.main()
