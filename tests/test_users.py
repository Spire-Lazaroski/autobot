"""Invite-only users: the admin invites, the invitee signs up once, and until their own account is connected an
invited user reaches nothing of the admin's account."""
import pytest
from fastapi.testclient import TestClient

from api import auth, users

PW = "correct horse battery"


@pytest.fixture
def lab(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "LOGS", tmp_path)
    monkeypatch.setattr(users, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(auth, "_key", None)
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.setenv("LAB_USER", "spire")
    monkeypatch.setenv("LAB_PASSWORD_HASH", auth.make_hash(PW))
    auth._fails.clear()
    auth._locked.clear()
    from api.main import app
    return app


def _admin(app):
    c = TestClient(app)
    assert c.post("/api/login", json={"user": "spire", "password": PW}).json()["role"] == "admin"
    return c


def _invite(app, note="Ana"):
    r = _admin(app).post("/api/admin/invites", json={"note": note})
    assert r.status_code == 200
    return r.json()["token"]


def test_invite_once_then_sign_in(lab):
    token = _invite(lab)
    guest = TestClient(lab)
    r = guest.post("/api/signup", json={"invite": token, "user": "Ana.K", "password": "a long password 1"})
    assert r.status_code == 200 and r.json() == {"ok": True, "user": "ana.k", "role": "user"}
    assert guest.get("/api/me").json()["role"] == "user"
    other = TestClient(lab)                                      # the link works once
    assert other.post("/api/signup", json={"invite": token, "user": "bob", "password": "a long password 2"}).status_code == 400
    fresh = TestClient(lab)
    assert fresh.post("/api/login", json={"user": "ana.k", "password": "a long password 1"}).json()["role"] == "user"
    listed = _admin(lab).get("/api/admin/users").json()
    assert [u["name"] for u in listed["users"]] == ["ana.k"] and listed["invites"][0]["state"] == "used"


def test_invited_user_sees_nothing_of_the_admins_account(lab):
    token = _invite(lab)
    guest = TestClient(lab)
    guest.post("/api/signup", json={"invite": token, "user": "ana", "password": "a long password 1"})
    for path in ("/api/live/snapshot", "/api/config", "/api/admin/users", "/api/reports", "/reports/x.html"):
        assert guest.get(path).status_code == 403, path
    assert guest.post("/api/live/start").status_code == 403
    assert guest.post("/api/live/close", json={"symbols": ["VOO"], "confirm": True}).status_code == 403
    assert guest.post("/api/setups", json={"name": "growth", "confirm": True}).status_code == 403
    with pytest.raises(Exception):
        with guest.websocket_connect("/ws/live") as ws:
            ws.receive_json()
    assert guest.get("/api/me").status_code == 200 and guest.get("/api/health").status_code == 200


def test_rules_for_names_passwords_and_links(lab):
    token = _invite(lab)
    g = TestClient(lab)
    assert "12 characters" in g.post("/api/signup", json={"invite": token, "user": "ana", "password": "short"}).json()["detail"]
    assert "User name" in g.post("/api/signup", json={"invite": token, "user": "a b", "password": "a long password 1"}).json()["detail"]
    assert "taken" in g.post("/api/signup", json={"invite": token, "user": "spire", "password": "a long password 1"}).json()["detail"]
    assert g.post("/api/signup", json={"invite": "made-up", "user": "ana", "password": "a long password 1"}).status_code == 400


def test_cancelled_invite_and_switched_off_user(lab):
    admin = _admin(lab)
    t1 = admin.post("/api/admin/invites", json={"note": "x"}).json()
    assert admin.delete(f"/api/admin/invites/{t1['id']}").status_code == 200
    assert TestClient(lab).post("/api/signup", json={"invite": t1["token"], "user": "ana", "password": "a long password 1"}).status_code == 400
    token = _invite(lab)
    guest = TestClient(lab)
    guest.post("/api/signup", json={"invite": token, "user": "ana", "password": "a long password 1"})
    assert admin.post("/api/admin/users/ana/disable", json={"disabled": True}).status_code == 200
    assert guest.get("/api/me").status_code == 401                         # the session ends at once
    assert TestClient(lab).post("/api/login", json={"user": "ana", "password": "a long password 1"}).status_code == 401


def test_only_hashes_are_stored(lab):
    token = _invite(lab)
    TestClient(lab).post("/api/signup", json={"invite": token, "user": "ana", "password": "a long password 1"})
    raw = users.DB_PATH.read_bytes()
    assert token.encode() not in raw and b"a long password 1" not in raw
