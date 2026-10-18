"""Invited users' own bots: config derived from the lab's with the user's setup, the user's own keys and home only,
Start / Stop, resume after an update, and every user sees only their own bot."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from api import auth, credentials, userbots, users
from autobot import presets
from autobot.liveconfig import bot_state, validate_live

ROOT = Path(__file__).resolve().parent.parent
LAB_CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
PW = "correct horse battery"


@pytest.fixture
def lab(monkeypatch, tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump(LAB_CFG, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(userbots, "CONFIG_PATH", cfg)
    monkeypatch.setattr(userbots, "USERS_HOME", tmp_path / "users")
    monkeypatch.setattr(users, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(auth, "LOGS", tmp_path)
    monkeypatch.setattr(credentials, "LOGS", tmp_path)
    monkeypatch.setattr(credentials, "_fernet", None)
    monkeypatch.setattr(auth, "_key", None)
    for k, v in {"AUTH_REQUIRED": "1", "LAB_USER": "spire", "LAB_PASSWORD_HASH": auth.make_hash(PW),
                 "ALPACA_API_KEY": "PKADMIN", "ALPACA_SECRET_KEY": "admin-secret", "CREDENTIALS_KEY": ""}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CREDENTIALS_KEY")
    monkeypatch.delenv("ALPACA_OAUTH_CLIENT_ID", raising=False)
    auth._fails.clear()
    auth._locked.clear()
    monkeypatch.setattr(credentials.requests, "get", lambda url, headers=None, timeout=None: SimpleNamespace(
        ok=True, status_code=200, json=lambda: {"account_number": "PA0000" + headers.get("APCA-API-KEY-ID", "")[-4:]}))
    monkeypatch.setattr(userbots, "account", lambda user: {"ok": True, "equity": 100000.0, "cash": 100000.0, "day_pnl": 0.0,
                                                           "day_pnl_pct": 0.0, "positions": []})
    started = []

    def fake_popen(args, **kw):
        started.append(SimpleNamespace(args=args, env=kw["env"]))
        return SimpleNamespace(pid=4242)
    monkeypatch.setattr(userbots.subprocess, "Popen", fake_popen)
    from api.main import app
    admin = TestClient(app)
    admin.post("/api/login", json={"user": "spire", "password": PW})
    clients = {}
    for name, key in (("ana", "PKANA1"), ("bob", "PKBOB2")):
        tok = admin.post("/api/admin/invites", json={"note": name}).json()["token"]
        c = TestClient(app)
        c.post("/api/signup", json={"invite": tok, "user": name, "password": "a long password 1"})
        assert c.post("/api/alpaca/keys", json={"key_id": key, "secret_key": f"{name}-secret"}).status_code == 200
        clients[name] = c
    return SimpleNamespace(app=app, admin=admin, ana=clients["ana"], bob=clients["bob"], started=started, tmp=tmp_path)


def test_derived_config_uses_the_setup_and_switches_jobs_off():
    st = {"setup": "growth", "jobs_on": False}
    cfg = userbots.derive_config(LAB_CFG, st)
    assert validate_live(cfg["live"]) == []
    assert presets.current(cfg["live"]) == "growth"
    assert all(j.get("enabled") is False for j in cfg["live"]["jobs"])
    assert "llm_judge" not in cfg
    on = userbots.derive_config(LAB_CFG, {"setup": "careful", "jobs_on": True})
    assert [j.get("enabled", True) for j in on["live"]["jobs"]] == [j.get("enabled", True) for j in LAB_CFG["live"]["jobs"]]


def test_setup_start_stop_and_only_the_users_own_keys(lab):
    assert lab.ana.post("/api/my/start").status_code == 400                    # no setup yet
    r = lab.ana.post("/api/my/setup", json={"setup": "growth", "jobs_on": False})
    assert r.status_code == 200
    cfg = yaml.safe_load((lab.tmp / "users" / "ana" / "config.yaml").read_text())
    assert presets.current(cfg["live"]) == "growth"
    r = lab.ana.post("/api/my/start")
    assert r.status_code == 200, r.text
    env = lab.started[-1].env
    assert lab.started[-1].args[-1].endswith("run_bot.py")
    assert env["ALPACA_API_KEY"] == "PKANA1" and env["ALPACA_SECRET_KEY"] == "ana-secret"
    assert env["AUTOBOT_HOME"] == str(lab.tmp / "users" / "ana")
    assert not any(k.startswith("LAB_") for k in env) and "CREDENTIALS_KEY" not in env       # none of the lab's secrets
    assert userbots.settings("ana")["want_running"] is True
    assert lab.ana.post("/api/my/stop", json={"confirm": True}).status_code == 409           # not running (nothing really started)


def test_stop_writes_the_users_stop_file_and_a_server_stop_keeps_the_wish(lab):
    lab.ana.post("/api/my/setup", json={"setup": "balanced"})
    lab.ana.post("/api/my/start")
    userbots.stop("ana", by_user=False)
    assert json.loads((lab.tmp / "users" / "ana" / "STOP").read_text()) == {"include_core": False}
    assert userbots.settings("ana")["want_running"] is True
    userbots.stop("ana", include_core=True)
    assert json.loads((lab.tmp / "users" / "ana" / "STOP").read_text())["include_core"] is True
    assert userbots.settings("ana")["want_running"] is False


def test_each_user_sees_only_their_own_bot(lab):
    lab.ana.post("/api/my/setup", json={"setup": "growth"})
    lab.bob.post("/api/my/setup", json={"setup": "careful", "jobs_on": True})
    a, b = lab.ana.get("/api/my/bot").json(), lab.bob.get("/api/my/bot").json()
    assert a["user"] == "ana" and a["settings"]["setup"] == "growth"
    assert b["user"] == "bob" and b["settings"]["setup"] == "careful" and b["settings"]["jobs_on"] is True
    assert lab.ana.get("/api/live/snapshot").status_code == 403
    assert lab.admin.get("/api/my/bot").status_code == 400                       # the lab's bot is on the Live page
    rows = {u["name"]: u for u in lab.admin.get("/api/admin/users").json()["users"]}
    assert rows["ana"]["setup"] == "growth" and rows["bob"]["connected"] is True


def test_sync_follows_the_labs_config(lab):
    lab.ana.post("/api/my/setup", json={"setup": "balanced"})
    assert userbots.sync_all() == []                                            # already in step
    cfg = yaml.safe_load(userbots.CONFIG_PATH.read_text())
    cfg["live"]["jobs"][0]["params"] = {**cfg["live"]["jobs"][0].get("params", {})}
    cfg["live"]["trade_rules"] = {**(cfg["live"].get("trade_rules") or {}), "min_change_pct": 12}
    userbots.CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))
    assert userbots.sync_all() == ["ana"]
    mine = yaml.safe_load((lab.tmp / "users" / "ana" / "config.yaml").read_text())
    assert mine["live"]["trade_rules"]["min_change_pct"] == 12 and presets.current(mine["live"]) == "balanced"


def test_a_fresh_heartbeat_from_another_container_counts_as_busy(lab):
    h = lab.tmp / "users" / "ana" / "logs"
    h.mkdir(parents=True)
    (h / "bot.pid").write_text(json.dumps({"pid": 999999, "heartbeat": time.time() - 5, "started": time.time() - 60}))
    assert userbots.busy("ana")                                                 # the old version's bot is still closing
    (h / "bot.pid").write_text(json.dumps({"pid": 999999, "heartbeat": time.time() - 600, "started": time.time() - 900}))
    assert not userbots.busy("ana")


def test_bot_files_live_in_its_home(tmp_path):
    env = {**os.environ, "AUTOBOT_HOME": str(tmp_path / "home")}
    out = subprocess.run([sys.executable, "-c", "import live, run_bot; from autobot import liveconfig as l; "
                          "print(live.LOGS, live.STOP_FILE, l.PID_FILE, run_bot.LOGS, sep='|')"],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    logs, stop, pid, sup = out.stdout.strip().splitlines()[-1].split("|")
    home = tmp_path / "home"
    assert Path(logs) == home / "logs" and Path(stop) == home / "STOP"
    assert Path(pid) == home / "logs" / "bot.pid" and Path(sup) == home / "logs"
    assert bot_state(home)["running"] is False
