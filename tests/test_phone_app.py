"""The installable phone app (PWA): manifest, icons, a service worker that never stores account data, and the
login letting the browser fetch them."""
import json
import struct
from pathlib import Path

from fastapi.testclient import TestClient

from api import auth

WEB = Path(__file__).resolve().parent.parent / "web"


def _png_size(p: Path):
    w, h = struct.unpack(">II", p.read_bytes()[16:24])
    return w, h


def test_manifest_and_icons():
    m = json.loads((WEB / "public" / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert m["display"] == "standalone" and m["start_url"].startswith("/") and m["name"] == "Spire Analytics"
    sizes = {}
    for ic in m["icons"]:
        p = WEB / "public" / ic["src"].lstrip("/")
        w, h = _png_size(p)
        assert f"{w}x{h}" == ic["sizes"], ic
        sizes.setdefault(ic.get("purpose", "any"), []).append(w)
    assert 192 in sizes["any"] and 512 in sizes["any"] and 512 in sizes["maskable"]
    assert _png_size(WEB / "public" / "icons" / "apple-touch-icon.png") == (180, 180)
    for page in ("index.html", "login.html"):
        html = (WEB / page).read_text(encoding="utf-8")
        assert 'rel="manifest"' in html and "apple-touch-icon" in html


def test_service_worker_never_keeps_account_data():
    sw = (WEB / "public" / "sw.js").read_text(encoding="utf-8")
    for p in ('"/api/"', '"/ws/"', '"/reports/"', '"/docs/"', '"/login"'):
        assert p in sw.split("const NEVER")[1].split("\n")[0], p
    assert 'req.method !== "GET"' in sw


def test_app_files_load_without_a_session(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "LOGS", tmp_path)
    monkeypatch.setattr(auth, "_key", None)
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.setenv("LAB_PASSWORD_HASH", auth.make_hash("a long test password"))
    for p in ("/manifest.webmanifest", "/sw.js", "/icons/icon-192.png"):
        assert auth.is_open(p), p
    assert not auth.is_open("/api/live/snapshot") and not auth.is_open("/reports/x.html")
    from api.main import app
    assert TestClient(app).get("/api/strategies").status_code == 401
