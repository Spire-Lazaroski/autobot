"""
The lab's own login (on top of a hosting panel's password, if any).

On the PC nothing changes: without LAB_PASSWORD_HASH and without AUTH_REQUIRED=1 the lab stays open on 127.0.0.1.
On the server (the Dockerfile sets AUTH_REQUIRED=1) every page, API call, report and the live WebSocket needs a
session from /login; only /login, /api/login and /health are open.

    python -m api.auth hash        # asks for a password (not shown), prints LAB_PASSWORD_HASH for the panel

Settings (environment): LAB_USER (default "spire"), LAB_PASSWORD_HASH (from the command above; the password itself
is never stored), AUTH_REQUIRED=1. The session key is made once and kept in logs/.session_key (on the server's
permanent volume), so sessions survive restarts. Cookies: HttpOnly, SameSite=Strict, Secure behind https; 12 hours,
or 30 days with "keep me signed in". Five wrong passwords from one address within 15 minutes lock that address out
for 15 minutes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Dict, Optional

from .settings import LOGS

COOKIE = "spire_session"
SHORT_S, LONG_S = 12 * 3600, 30 * 86400
MAX_FAILS, FAIL_WINDOW_S, LOCK_S = 5, 900, 900
ITERATIONS = 600_000
OPEN_PATHS = ("/login", "/login.html", "/api/login", "/api/logout", "/api/signup", "/health", "/favicon.ico",
              "/api/alpaca/callback",                          # Alpaca's return trip: tied to the user by a one-time state
              "/terms", "/privacy", "/terms.html", "/privacy.html", "/legal.css",   # public: Terms of Use, Privacy Policy
              "/manifest.webmanifest", "/sw.js")                # the phone app's description and helper: no data
OPEN_PREFIXES = ("/assets/", "/icons/")                 # the built JS/CSS (code only, no data): the login page needs its own

# what an invited user may reach before their own account is connected: their identity, sign-out, the app's shell
USER_API = ("/api/me", "/api/logout", "/api/health")
USER_PREFIXES = ("/api/alpaca/",)               # their own Alpaca connection

_fails: Dict[str, deque] = defaultdict(deque)
_locked: Dict[str, float] = {}
_lock = threading.Lock()
_key: Optional[bytes] = None


# ----------------------------------------------------------------- settings
def user() -> str:
    return os.environ.get("LAB_USER", "spire").strip() or "spire"


def password_hash() -> str:
    return os.environ.get("LAB_PASSWORD_HASH", "").strip()


def required() -> bool:
    """Login needed: on the server (AUTH_REQUIRED=1) always, on the PC only once a password hash is set."""
    return os.environ.get("AUTH_REQUIRED", "0") == "1" or bool(password_hash())


def configured() -> bool:
    return bool(password_hash())


# ----------------------------------------------------------------- passwords (PBKDF2-SHA256, no extra package)
def make_hash(password: str, salt: Optional[bytes] = None) -> str:
    """pbkdf2_sha256:<iterations>:<salt hex>:<hash hex> (colons only: a '$' could be read as a placeholder by
    the panel's environment file)."""
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256:{ITERATIONS}:{salt.hex()}:{dk.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        algo, it, salt, want = stored.split(":")
        if algo != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(got.hex(), want)
    except (ValueError, TypeError):
        return False


# ----------------------------------------------------------------- sessions (HMAC-signed cookie)
def _session_key() -> bytes:
    global _key
    if _key is None:
        p = LOGS / ".session_key"
        try:
            _key = bytes.fromhex(p.read_text().strip())
        except (OSError, ValueError):
            _key = secrets.token_bytes(32)
            LOGS.mkdir(parents=True, exist_ok=True)
            p.write_text(_key.hex())
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
    return _key


def _sign(body: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(_session_key(), body, hashlib.sha256).digest()).decode().rstrip("=")


def make_session(name: str, days_long: bool) -> tuple[str, int]:
    ttl = LONG_S if days_long else SHORT_S
    body = base64.urlsafe_b64encode(json.dumps({"u": name, "exp": int(time.time()) + ttl}).encode()).decode().rstrip("=")
    return f"{body}.{_sign(body.encode())}", ttl


def session_user(token: Optional[str]) -> Optional[str]:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(body.encode())):
        return None
    try:
        d = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError):
        return None
    if d.get("exp", 0) < time.time() or not role_of(d.get("u", "")):
        return None
    return d["u"]


def role_of(name: str) -> Optional[str]:
    """admin = the lab's own login (environment); user = an invited, not disabled user (api/users.py)."""
    if not name:
        return None
    if configured() and hmac.compare_digest(name.encode(), user().encode()):
        return "admin"
    from . import users
    return "user" if users.active(name) else None


def cookie_from_header(raw: Optional[str]) -> Optional[str]:
    for part in (raw or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            return v
    return None


# ----------------------------------------------------------------- lockout
def locked_for(addr: str) -> float:
    with _lock:
        until = _locked.get(addr, 0)
        return max(0.0, until - time.time())


def record_fail(addr: str):
    now = time.time()
    with _lock:
        q = _fails[addr]
        q.append(now)
        while q and now - q[0] > FAIL_WINDOW_S:
            q.popleft()
        if len(q) >= MAX_FAILS:
            _locked[addr] = now + LOCK_S
            q.clear()


def clear_fails(addr: str):
    with _lock:
        _fails.pop(addr, None)
        _locked.pop(addr, None)


def client_addr(headers: Dict[str, str], peer: Optional[str]) -> str:
    """The visitor's address: the first X-Forwarded-For entry behind the panel's proxy, else the socket peer."""
    fwd = headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (peer or "?")


def is_open(path: str) -> bool:
    return path in OPEN_PATHS or path.startswith(OPEN_PREFIXES)


# ----------------------------------------------------------------- ASGI middleware (HTTP and the WebSocket)
class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not required() or is_open(scope.get("path", "")):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        name = session_user(cookie_from_header(headers.get("cookie")))
        path = scope.get("path", "")
        if name and (role_of(name) == "admin" or not path.startswith(("/api/", "/ws/", "/reports/", "/docs/"))
                     or path in USER_API or path.startswith(USER_PREFIXES)):
            return await self.app(scope, receive, send)
        if name:                                        # an invited user: nothing of the admin's account
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4403})
                return
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": b'{"detail":"Your own account is not connected yet."}'})
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        if path.startswith(("/api/", "/reports/", "/docs/")):
            body, status, extra = b'{"detail":"Please sign in."}', 401, [(b"content-type", b"application/json")]
        else:                                           # a page: to the login, then back here
            body, status, extra = b"", 303, [(b"location", b"/login")]
        await send({"type": "http.response.start", "status": status, "headers": [(b"cache-control", b"no-store"), *extra]})
        await send({"type": "http.response.body", "body": body})


def main():
    import getpass
    import sys
    if sys.argv[1:] != ["hash"]:
        print("usage: python -m api.auth hash")
        return
    pw = getpass.getpass("New lab password (not shown): ")
    if len(pw) < 12:
        print("Use at least 12 characters.")
        return
    if getpass.getpass("Again: ") != pw:
        print("The two didn't match.")
        return
    print("\nPut this in the panel's Environment as LAB_PASSWORD_HASH (and LAB_USER if not 'spire'):\n")
    print(make_hash(pw))


if __name__ == "__main__":
    main()
