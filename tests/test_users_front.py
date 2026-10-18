"""Invited users see the lab's pages with only their own account: Live (snapshot, WebSocket, Start/Stop, close and
pause), Setup and Reports answer for the signed-in user; News is read-only; the lab's own pages stay closed."""
import json

import pytest
import yaml
from fastapi.testclient import TestClient

import api.live as L
from api import userbots
from tests.test_user_bots import lab  # noqa: F401  (two invited users, ana and bob, connected with paper keys)


class _TC:
    def __init__(self, tag):
        self.tag, self.closed = tag, []

    def get_account(self):
        from types import SimpleNamespace
        return SimpleNamespace(account_number=f"PA{self.tag}", status="ACTIVE", equity="100000", last_equity="100000",
                               cash="100000", buying_power="200000")

    def get_all_positions(self):
        from types import SimpleNamespace
        return [SimpleNamespace(symbol="VOO", side="long", qty="5", avg_entry_price="500", current_price="510",
                                market_value="2550", unrealized_pl="50", unrealized_plpc="0.02", unrealized_intraday_pl="5")]

    def get_orders(self, *a, **k):
        return []

    def get_clock(self):
        from types import SimpleNamespace
        return SimpleNamespace(is_open=False, next_open=None, next_close=None)

    def close_position(self, sym):
        from types import SimpleNamespace
        self.closed.append(sym)
        return SimpleNamespace(id="c1")


@pytest.fixture
def front(lab, monkeypatch):  # noqa: F811
    from api import viewer
    clients = {"ana": _TC("ANA1"), "bob": _TC("BOB2")}
    monkeypatch.setattr(viewer, "trading_client", lambda v: clients[v.user] if v.is_user else _TC("LAB0"))
    L._snaps.clear()
    lab.clients = clients
    return lab


def test_live_snapshot_is_the_users_own(front):
    front.ana.post("/api/my/setup", json={"setup": "growth"})
    d = front.ana.get("/api/live/snapshot").json()
    assert d["viewer"] == "user" and d["account"]["number_tail"] == "ANA1" and d["jobs_vs_hold"] is None
    assert front.bob.get("/api/live/snapshot").json()["account"]["number_tail"] == "BOB2"
    with front.ana.websocket_connect("/ws/live") as ws:
        assert ws.receive_json()["account"]["number_tail"] == "ANA1"


def test_close_and_pause_act_on_the_users_own_account_and_config(front):
    front.ana.post("/api/my/setup", json={"setup": "balanced"})
    r = front.ana.post("/api/live/close", json={"symbols": ["VOO"], "confirm": True})
    assert r.status_code == 200 and front.clients["ana"].closed == ["VOO"] and front.clients["bob"].closed == []
    mine = yaml.safe_load((front.tmp / "users" / "ana" / "config.yaml").read_text())
    assert mine["live"]["paused"] == ["VOO"]
    assert "paused" not in yaml.safe_load(userbots.CONFIG_PATH.read_text())["live"]       # the lab's untouched
    userbots.write_config("ana")                                                           # a re-sync keeps the pause
    assert yaml.safe_load((front.tmp / "users" / "ana" / "config.yaml").read_text())["live"]["paused"] == ["VOO"]
    front.ana.post("/api/live/resume", json={"symbols": ["VOO"]})
    assert "paused" not in yaml.safe_load((front.tmp / "users" / "ana" / "config.yaml").read_text())["live"]


def test_start_and_setup_from_the_live_page_are_the_users_own(front):
    front.ana.post("/api/my/setup", json={"setup": "careful"})
    assert front.ana.get("/api/setups").json()["current"] == "careful"
    assert front.ana.post("/api/setups", json={"name": "growth", "confirm": True}).status_code == 200
    assert userbots.settings("ana")["setup"] == "growth"
    r = front.ana.post("/api/live/start")
    assert r.status_code == 200 and front.started[-1].env["ALPACA_API_KEY"] == "PKANA1"


def test_reports_are_the_users_own(front):
    rep = front.tmp / "users" / "ana" / "reports"
    rep.mkdir(parents=True)
    (rep / "2026-10-19.html").write_text("<p>ana</p>")
    assert [r["name"] for r in front.ana.get("/api/reports").json()["reports"]] == ["2026-10-19"]
    assert front.ana.get("/reports/2026-10-19.html").text == "<p>ana</p>"
    assert front.bob.get("/api/reports").json()["reports"] == []
    assert front.bob.get("/reports/2026-10-19.html").status_code == 404                   # not bob's


def test_news_is_read_only_and_the_labs_pages_stay_closed(front):
    assert front.ana.get("/api/news/status").status_code == 200
    assert front.ana.post("/api/news/score-claude", json={"limit": 5}).status_code == 403
    assert front.ana.post("/api/news/impact", json={}).status_code == 403
    for path in ("/api/config", "/api/admin/users", "/api/recal/proposal", "/api/universe"):
        assert front.ana.get(path).status_code == 403, path
    assert front.ana.post("/api/reports/backtest", json={}).status_code == 403
