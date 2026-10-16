"""The three setups: valid configs, recognised, switchable from the API, acted on at the bot's next tick."""
import yaml
from fastapi.testclient import TestClient

import live
from autobot import presets
from autobot.liveconfig import validate_live

ROOT_CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))


def test_every_setup_is_a_valid_live_config_and_is_recognised():
    for name in presets.PRESETS:
        cfg = presets.apply(yaml.safe_load(yaml.safe_dump(ROOT_CFG)), name)
        assert validate_live(cfg["live"]) == [], name
        assert presets.current(cfg["live"]) == name
        assert cfg["live"]["jobs"] == ROOT_CFG["live"]["jobs"]          # the jobs are never touched


def test_setups_rise_in_size():
    p = presets.PRESETS
    assert p["careful"]["core"]["allocation_pct"] < p["balanced"]["core"]["allocation_pct"] < p["growth"]["core"]["allocation_pct"]
    assert p["careful"]["rotation"]["allocation_pct"] <= p["balanced"]["rotation"]["allocation_pct"] <= p["growth"]["rotation"]["allocation_pct"]


def test_saved_study_orders_return_and_drop():
    st = presets.saved()
    assert st is not None
    for rows in st["samples"].values():
        assert rows["careful"]["yearly_%"] < rows["balanced"]["yearly_%"] < rows["growth"]["yearly_%"]
        assert rows["careful"]["worst_drop_%"] > rows["balanced"]["worst_drop_%"] > rows["growth"]["worst_drop_%"]


def test_api_switches_the_setup(tmp_path, monkeypatch):
    import api.main as M
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(ROOT_CFG, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(M, "CONFIG_PATH", cfg_path)
    c = TestClient(M.app)
    assert c.get("/api/setups").json()["current"] == presets.current(ROOT_CFG["live"])
    assert c.post("/api/setups", json={"name": "growth"}).status_code == 400          # needs confirm
    assert c.post("/api/setups", json={"name": "nope", "confirm": True}).status_code == 404
    r = c.post("/api/setups", json={"name": "careful", "confirm": True})
    assert r.status_code == 200 and "Careful" in r.json()["message"]
    assert presets.current(yaml.safe_load(cfg_path.read_text())["live"]) == "careful"


def test_bot_acts_on_a_new_setup_at_its_next_tick(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "LOGS", tmp_path)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(ROOT_CFG, sort_keys=False), encoding="utf-8")
    from tests.test_core_live import CoreTC
    bot = live.Bot(yaml.safe_load(cfg_path.read_text()), tc=CoreTC(), cfg_path=cfg_path)
    bot.core_day, bot.rot_month = "2026-10-16", "2026-10"
    import os
    import time
    new = presets.apply(yaml.safe_load(cfg_path.read_text()), "careful")   # core and sleeve both change
    cfg_path.write_text(yaml.safe_dump(new, sort_keys=False), encoding="utf-8")
    os.utime(cfg_path, (time.time() + 5, time.time() + 5))
    bot.reload_config()
    assert bot.core_day is None and bot.rot_month is None
