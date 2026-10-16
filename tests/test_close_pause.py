"""Closing positions by hand: the symbols are paused, the bot never buys them back until Resume."""
import json
from types import SimpleNamespace

import yaml
from fastapi.testclient import TestClient

import live
from autobot.liveconfig import validate_live
from tests.test_core_live import CoreTC, cfg_with_core
from tests.test_live import BASE, env  # noqa: F401


def test_paused_core_is_not_rebalanced_and_paused_job_symbols_are_skipped(env):  # noqa: F811
    cfg = cfg_with_core(env)
    job_syms = [s for j in cfg["live"]["jobs"] if j.get("enabled", True) for s in j["symbols"]]
    cfg["live"]["paused"] = ["VOO", job_syms[0]]
    tc = CoreTC()
    bot = live.Bot(cfg, tc=tc)
    bot.step_once()
    assert [o for o in tc.orders if o.symbol in ("VOO", job_syms[0])] == []
    st = json.loads((live.LOGS / "status.json").read_text())
    assert st["core"]["paused"] is True and st["paused"] == sorted(["VOO", job_syms[0]])
    assert st["symbols"][job_syms[0]]["paused"] is True


def test_submit_never_buys_a_paused_symbol_but_may_sell(env):  # noqa: F811
    bot = live.Bot(yaml.safe_load(env.read_text()), tc=CoreTC())
    bot.paused = {"XLE"}
    assert bot.submit("XLE", 5, "test", 50.0, "rotation") is None and bot.tc.orders == []
    assert bot.submit("XLE", -5, "test", 50.0, "rotation") is not None


def test_config_validation_and_diff():
    live_cfg = yaml.safe_load(yaml.safe_dump(BASE))["live"]
    assert validate_live({**live_cfg, "paused": ["VOO"]}) == []
    assert any("paused" in e for e in validate_live({**live_cfg, "paused": "VOO"}))
    assert any("paused" in e for e in live.config_diff(live_cfg, {**live_cfg, "paused": ["VOO"]}))


class _TC:
    def __init__(self):
        self.closed = []

    def get_all_positions(self):
        return [SimpleNamespace(symbol="VOO", qty="55"), SimpleNamespace(symbol="TSLA", qty="3")]

    def close_position(self, sym):
        self.closed.append(sym)
        return SimpleNamespace(id=f"c-{sym}")


def test_api_close_pauses_then_sells_and_resume_unpauses(tmp_path, monkeypatch):
    import api.live as L
    from api.main import app
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(BASE, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(L, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(L, "LOGS", tmp_path)
    tc = _TC()
    monkeypatch.setattr(L, "_client", lambda: tc)
    c = TestClient(app)
    assert c.post("/api/live/close", json={"symbols": ["VOO"]}).status_code == 400          # needs confirm
    r = c.post("/api/live/close", json={"symbols": ["voo", "XLK"], "confirm": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert tc.closed == ["VOO"]                                   # XLK: nothing held, only paused
    assert body["paused"] == ["VOO", "XLK"] and "Resume" in body["message"]
    assert yaml.safe_load(cfg_path.read_text())["live"]["paused"] == ["VOO", "XLK"]
    ev = [json.loads(l) for l in (tmp_path / "decisions.jsonl").read_text().splitlines()]
    assert ev[-1]["event"] == "manual_close" and "VOO" in ev[-1]["message"]
    r = c.post("/api/live/resume", json={"symbols": ["VOO", "XLK"]})
    assert r.json()["paused"] == [] and "paused" not in yaml.safe_load(cfg_path.read_text())["live"]
