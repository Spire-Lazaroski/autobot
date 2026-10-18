"""Connecting an invited user's own Alpaca PAPER account: OAuth with env=paper, a one-time state, the code exchanged
on the server, the permission tested on the paper API and stored encrypted; the paper-keys fallback."""
import urllib.parse
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api import auth, credentials, users

PW = "correct horse battery"


@pytest.fixture
def lab(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "LOGS", tmp_path)
    monkeypatch.setattr(credentials, "LOGS", tmp_path)
    monkeypatch.setattr(credentials, "_fernet", None)
    monkeypatch.setattr(users, "DB_PATH", tmp_path / "users.db")
    from api import userbots
    monkeypatch.setattr(userbots, "USERS_HOME", tmp_path / "users")      # users' homes: never the real data/users
    monkeypatch.setattr(auth, "_key", None)
    for k, v in {"AUTH_REQUIRED": "1", "LAB_USER": "spire", "LAB_PASSWORD_HASH": auth.make_hash(PW),
                 "ALPACA_OAUTH_CLIENT_ID": "cid", "ALPACA_OAUTH_CLIENT_SECRET": "csecret", "APP_URL": "https://spire.example"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CREDENTIALS_KEY", raising=False)
    auth._fails.clear()
    auth._locked.clear()
    credentials._states.clear()
    calls = []

    def fake_post(url, data=None, timeout=None):
        calls.append(("post", url, data))
        return SimpleNamespace(ok=data.get("code") == "good", json=lambda: {"access_token": "tok-123", "token_type": "bearer"})

    def fake_get(url, headers=None, timeout=None):
        calls.append(("get", url, headers))
        ok = headers.get("Authorization") == "Bearer tok-123" or headers.get("APCA-API-KEY-ID") == "PKGOOD"
        return SimpleNamespace(ok=ok, status_code=200 if ok else 401, json=lambda: {"account_number": "PA12345678"})
    monkeypatch.setattr(credentials.requests, "post", fake_post)
    monkeypatch.setattr(credentials.requests, "get", fake_get)
    from api.main import app
    admin = TestClient(app)
    admin.post("/api/login", json={"user": "spire", "password": PW})
    token = admin.post("/api/admin/invites", json={"note": "Ana"}).json()["token"]
    guest = TestClient(app)
    guest.post("/api/signup", json={"invite": token, "user": "ana", "password": "a long password 1"})
    return SimpleNamespace(app=app, admin=admin, guest=guest, calls=calls)


def _start(lab):
    r = lab.guest.get("/api/alpaca/connect", follow_redirects=False)
    assert r.status_code == 303
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)
    assert r.headers["location"].startswith(credentials.AUTHORIZE_URL)
    assert q["env"] == ["paper"] and q["redirect_uri"] == ["https://spire.example/api/alpaca/callback"]
    assert set(q["scope"][0].split()) == {"account:write", "trading", "data"}
    return q["state"][0]


def test_connect_with_alpaca_paper_only(lab):
    state = _start(lab)
    alpaca_return = TestClient(lab.app)                          # Alpaca's redirect carries no session cookie
    r = alpaca_return.get(f"/api/alpaca/callback?code=good&state={state}")
    assert r.status_code == 200 and "alpaca=connected" in r.text
    post = [c for c in lab.calls if c[0] == "post"][0]
    assert post[1] == credentials.TOKEN_URL and post[2]["client_secret"] == "csecret"
    get = [c for c in lab.calls if c[0] == "get"][0]
    assert get[1].startswith("https://paper-api.alpaca.markets/")   # tested on the PAPER api only
    st = lab.guest.get("/api/alpaca/status").json()
    assert st["connected"] and st["method"] == "oauth" and st["account_tail"] == "5678"
    assert credentials.load("ana") == {"oauth_token": "tok-123"}
    assert b"tok-123" not in users.DB_PATH.read_bytes()            # stored encrypted
    again = TestClient(lab.app).get(f"/api/alpaca/callback?code=good&state={state}")   # a state works once
    assert "alpaca_error" in again.text


def test_bad_state_code_or_cancel(lab):
    assert "alpaca_error" in TestClient(lab.app).get("/api/alpaca/callback?code=good&state=made-up").text
    state = _start(lab)
    assert "alpaca_error" in TestClient(lab.app).get(f"/api/alpaca/callback?code=bad&state={state}").text
    assert "cancelled" in TestClient(lab.app).get("/api/alpaca/callback?error=access_denied&state=x").text
    assert not lab.guest.get("/api/alpaca/status").json()["connected"]


def test_paper_keys_fallback_and_disconnect(lab):
    r = lab.guest.post("/api/alpaca/keys", json={"key_id": "AKLIVE", "secret_key": "s"})
    assert r.status_code == 400 and "paper key" in r.json()["detail"]
    assert lab.guest.post("/api/alpaca/keys", json={"key_id": "PKWRONG", "secret_key": "s"}).status_code == 400
    r = lab.guest.post("/api/alpaca/keys", json={"key_id": "PKGOOD", "secret_key": "sss"})
    assert r.status_code == 200 and r.json()["method"] == "keys"
    assert b"sss" not in users.DB_PATH.read_bytes()
    assert lab.guest.post("/api/alpaca/disconnect", json={}).json()["connected"] is False
    assert credentials.load("ana") is None


def test_connecting_still_shows_nothing_of_the_admins_account(lab):
    lab.guest.post("/api/alpaca/keys", json={"key_id": "PKGOOD", "secret_key": "sss"})
    assert lab.guest.get("/api/config").status_code == 403                      # the lab's own settings stay closed
    assert lab.admin.get("/api/alpaca/status").json()["method"] == "server"
    assert lab.admin.get("/api/alpaca/connect", follow_redirects=False).status_code == 400


def test_connect_off_without_oauth_settings(lab, monkeypatch):
    monkeypatch.delenv("ALPACA_OAUTH_CLIENT_ID")
    assert lab.guest.get("/api/alpaca/status").json()["oauth_available"] is False
    assert lab.guest.get("/api/alpaca/connect", follow_redirects=False).status_code == 503
