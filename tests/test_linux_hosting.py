"""The Linux server setup (deploy/linux): the bot start from the lab, PDFs and the service files."""
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


def test_lab_start_detaches_the_bot_on_linux(monkeypatch, tmp_path):
    import api.live as L
    from api.main import app
    monkeypatch.setattr(L, "bot_state", lambda: {"running": False, "unresponsive": False, "pid": None})
    monkeypatch.setattr(L, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(L, "LOGS", tmp_path)
    monkeypatch.setattr(L.sys, "platform", "linux")
    seen = {}

    class P:
        pid = 77

    def fake(*a, **k):
        seen.update(k)
        return P()
    monkeypatch.setattr(L.subprocess, "Popen", fake)
    r = TestClient(app).post("/api/live/start")
    assert r.status_code == 200 and r.json()["pid"] == 77
    assert seen.get("start_new_session") is True and "creationflags" not in seen


def test_pdf_uses_chromium_from_the_path_when_there_is_no_edge(monkeypatch):
    import autobot.report as R
    monkeypatch.setattr(R, "EDGE", [Path("/nope/msedge.exe")])
    monkeypatch.setattr(R.shutil, "which", lambda b: "/usr/bin/chromium" if b == "chromium" else None)
    assert R.pdf_browser() == Path("/usr/bin/chromium")
    monkeypatch.setattr(R.shutil, "which", lambda b: None)
    assert R.pdf_browser() is None


def test_service_files():
    d = ROOT / "deploy" / "linux"
    setup = (d / "setup.sh").read_text(encoding="utf-8")
    for u in ("autobot-lab", "autobot-bot", "autobot-news"):
        text = (d / f"{u}.service").read_text(encoding="utf-8")
        assert "@ROOT@" in text and "\r" not in text and u in setup
    bot = (d / "autobot-bot.service").read_text(encoding="utf-8")
    assert "Restart=no" in bot and "touch @ROOT@/STOP" in bot   # stop = the lab's Stop: long-term holdings kept
    assert "\r" not in setup and "PKXXXX" in setup               # refuses to run with the example keys
    ga = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.sh text eol=lf" in ga and "deploy/linux/* text eol=lf" in ga


def test_health_for_the_panel():
    from api.main import app
    r = TestClient(app).get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_config_write_goes_through_a_symlink(tmp_path):
    """On the server config.yaml is a link to the permanent volume; saving must change the file it points at."""
    import os
    import pytest
    from autobot import recal_apply
    real = tmp_path / "state" / "config.yaml"
    real.parent.mkdir()
    real.write_text("a: 1\n", encoding="utf-8")
    link = tmp_path / "config.yaml"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need extra rights on this Windows account")
    recal_apply._atomic_write(link, {"a": 2})
    assert link.is_symlink() and "a: 2" in real.read_text(encoding="utf-8")


def test_container_files():
    ep = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "\r" not in ep
    assert "touch /app/STOP" in ep                      # a stop closes the jobs and keeps the long-term holdings
    assert "rm -rf" not in ep                           # never deletes saved state
    assert "rm -f /app/logs/bot.pid /app/STOP" in ep    # an old lock can't block the new bot
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "stop_grace_period: 120s" in compose and "127.0.0.1:" in compose and "/state" in compose
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert ".env" in ignore and "data" in ignore and "logs" in ignore
    serve = (ROOT / "docker" / "serve.py").read_text(encoding="utf-8")
    assert "api.main:app" in serve
    assert "ANTHROPIC" not in compose                   # no paid API key in the container by default
