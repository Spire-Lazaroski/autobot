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
    assert "rm -f /app/STOP" in ep and "bot.pid" not in ep.split("rm -f")[1].splitlines()[0]   # the lock survives an update overlap
    assert ep.index("docker/serve.py") < ep.index("run_news.py") < ep.index("run_bot.py")       # /health answers first
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "stop_grace_period: 120s" in compose and "/state" in compose
    assert compose.count("${") == 1                                     # the only placeholder the panel allows, in its exact form
    assert "image: autobot:${RELEASE_SHA:?RELEASE_SHA must be a full commit SHA}" in compose
    assert "env_file: .env" in compose                                  # the panel writes its Environment settings there
    import yaml
    app = yaml.safe_load(compose)["services"]["app"]                   # the panel needs a service named app
    assert "/health" in " ".join(app["healthcheck"]["test"])           # ... with a health check
    assert int(app["healthcheck"]["interval"].rstrip("s")) <= 10       # first check well inside the panel's ~24 s
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert ".env" in ignore and "data" in ignore and "logs" in ignore
    serve = (ROOT / "docker" / "serve.py").read_text(encoding="utf-8")
    assert "api.main:app" in serve
    assert "ANTHROPIC" not in compose                   # no paid API key in the container by default


def test_news_page_can_start_the_collector_and_shows_why_it_stopped(monkeypatch, tmp_path):
    import api.live as L
    import api.news as N
    import run_news
    from api.main import app
    log = tmp_path / "news.log"
    log.write_text("line 1\nERROR backfill failed (SSLError)\n", encoding="utf-8")
    monkeypatch.setattr(run_news, "NEWS_LOG", log)
    monkeypatch.setattr(N, "collector_state", lambda: {"running": False})
    monkeypatch.setattr(N, "supervisor_alive", lambda: False)
    started = []
    monkeypatch.setattr(L, "start_detached", lambda script, out: started.append(script) or 99)
    c = TestClient(app)
    st = c.get("/api/news/status").json()["collector"]
    assert st["supervisor"] is False and st["log_tail"][-1] == "ERROR backfill failed (SSLError)"
    r = c.post("/api/news/start", json={})
    assert r.status_code == 200 and started == ["run_news.py"]
    monkeypatch.setattr(N, "supervisor_alive", lambda: True)          # waiting to restart it: no second copy
    assert c.post("/api/news/start", json={}).status_code == 409
    monkeypatch.setattr(N, "collector_state", lambda: {"running": True})
    assert c.post("/api/news/start", json={}).status_code == 409 and started == ["run_news.py"]


def test_study_results_are_seeded_without_overwriting():
    ep = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    assert 'cp -rn /app/seed/studies/. "$STATE/data/"' in ep             # -n: a newer run on the server is kept
    for f in ("ml/study_vol_target.json", "rs_study.json", "event_study.json", "news_guard_study.json", "recal_fix_study.json"):
        assert (ROOT / "seed" / "studies" / f).stat().st_size > 1000, f
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert "seed" not in ignore
