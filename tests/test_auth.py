"""The lab's own login (api/auth.py): open on the PC by default, required on the server."""
import time

import pytest
from fastapi.testclient import TestClient

from api import auth


@pytest.fixture
def server(monkeypatch, tmp_path):
    from api import users
    monkeypatch.setattr(auth, "LOGS", tmp_path)
    monkeypatch.setattr(users, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(auth, "_key", None)
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.setenv("LAB_USER", "spire")
    monkeypatch.setenv("LAB_PASSWORD_HASH", auth.make_hash("correct horse battery"))
    auth._fails.clear()
    auth._locked.clear()
    from api.main import app
    return TestClient(app)


def test_hash_and_check():
    h = auth.make_hash("s3cret-password")
    assert h.startswith("pbkdf2_sha256:") and "$" not in h
    assert auth.check_password("s3cret-password", h) and not auth.check_password("wrong", h)
    assert not auth.check_password("x", "garbage")


def test_open_on_the_pc_without_a_password(monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("LAB_PASSWORD_HASH", raising=False)
    from api.main import app
    assert TestClient(app).get("/api/strategies").status_code == 200


def test_everything_needs_a_session_except_login_and_health(server):
    assert server.get("/api/strategies").status_code == 401
    r = server.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert server.get("/health").status_code == 200
    assert server.get("/api/me").status_code == 401
    with pytest.raises(Exception):
        with server.websocket_connect("/ws/live") as ws:
            ws.receive_json()


def test_sign_in_sign_out(server):
    assert server.post("/api/login", json={"user": "spire", "password": "nope"}).status_code == 401
    r = server.post("/api/login", json={"user": "spire", "password": "correct horse battery", "remember": True})
    assert r.status_code == 200
    ck = r.headers["set-cookie"].lower()
    assert "httponly" in ck and "samesite=strict" in ck and "max-age=2592000" in ck
    assert server.get("/api/strategies").status_code == 200
    assert server.get("/api/me").json()["user"] == "spire"
    server.post("/api/logout")
    assert server.get("/api/strategies").status_code == 401


def test_tampered_or_expired_session_is_refused(server, monkeypatch):
    token, _ = auth.make_session("spire", False)
    assert auth.session_user(token) == "spire"
    body, sig = token.rsplit(".", 1)
    assert auth.session_user(body + "." + sig[:-1] + ("A" if sig[-1] != "A" else "B")) is None
    later = time.time() + auth.SHORT_S + 60
    monkeypatch.setattr(auth.time, "time", lambda: later)
    assert auth.session_user(token) is None


def test_lockout_after_five_wrong_passwords(server):
    for _ in range(auth.MAX_FAILS):
        assert server.post("/api/login", json={"user": "spire", "password": "bad"}).status_code == 401
    r = server.post("/api/login", json={"user": "spire", "password": "correct horse battery"})
    assert r.status_code == 429 and "minutes" in r.json()["detail"]
