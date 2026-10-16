"""
Invited users (invite-only, a handful of people). The admin is the lab's own login from the environment
(LAB_USER / LAB_PASSWORD_HASH, api/auth.py); everyone else gets an invite link from the admin's Users page, chooses a
user name and password, and is stored here.

Storage: data/users.db (SQLite; on the server data/ is the permanent volume). Passwords only as PBKDF2 hashes
(auth.make_hash); invite links only as SHA-256 hashes, valid 7 days, usable once.

Until a user's own Alpaca paper account is connected (next step), an invited user sees a welcome page only: the
middleware in api/auth.py lets them reach /api/me and /api/logout, nothing about the admin's account.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import threading
import time
from typing import Dict, List, Optional

from .settings import ROOT

DB_PATH = ROOT / "data" / "users.db"
INVITE_DAYS = 7
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            name TEXT PRIMARY KEY, pw_hash TEXT NOT NULL, created REAL NOT NULL,
            invited_by TEXT, note TEXT, disabled INTEGER NOT NULL DEFAULT 0, last_login REAL);
        CREATE TABLE IF NOT EXISTS invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT UNIQUE NOT NULL, note TEXT, created REAL NOT NULL,
            expires REAL NOT NULL, created_by TEXT, used_by TEXT, used_at REAL, revoked INTEGER NOT NULL DEFAULT 0);
    """)
    return c


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name))


# ----------------------------------------------------------------- invites
def create_invite(note: str, by: str) -> Dict:
    """A new invite; the token itself is returned once and never stored."""
    token = secrets.token_urlsafe(24)
    now = time.time()
    with _lock, _db() as c:
        cur = c.execute("INSERT INTO invites(token_hash, note, created, expires, created_by) VALUES (?,?,?,?,?)",
                        (_h(token), note.strip()[:80], now, now + INVITE_DAYS * 86400, by))
        return {"id": cur.lastrowid, "token": token, "expires": now + INVITE_DAYS * 86400}


def list_invites() -> List[Dict]:
    with _db() as c:
        rows = c.execute("SELECT id, note, created, expires, created_by, used_by, used_at, revoked FROM invites ORDER BY id DESC").fetchall()
    now = time.time()
    out = []
    for r in rows:
        d = dict(r)
        d["state"] = ("used" if d["used_by"] else "revoked" if d["revoked"] else "expired" if d["expires"] < now else "open")
        out.append(d)
    return out


def revoke_invite(invite_id: int) -> bool:
    with _lock, _db() as c:
        return c.execute("UPDATE invites SET revoked = 1 WHERE id = ? AND used_by IS NULL", (invite_id,)).rowcount > 0


def invite_ok(token: str) -> Optional[Dict]:
    with _db() as c:
        r = c.execute("SELECT * FROM invites WHERE token_hash = ?", (_h(token),)).fetchone()
    if not r or r["used_by"] or r["revoked"] or r["expires"] < time.time():
        return None
    return dict(r)


def sign_up(token: str, name: str, pw_hash: str, admin_name: str) -> str:
    """Use an invite: creates the user. Returns '' on success, else the reason (plain English)."""
    name = name.strip().lower()
    if not valid_name(name):
        return "User name: 3-32 characters, lower-case letters, digits, '.', '_' or '-'."
    if name == admin_name.lower():
        return "That user name is taken."
    with _lock, _db() as c:
        inv = c.execute("SELECT * FROM invites WHERE token_hash = ?", (_h(token),)).fetchone()
        if not inv or inv["used_by"] or inv["revoked"] or inv["expires"] < time.time():
            return "This invite link is not valid any more. Ask for a new one."
        if c.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone():
            return "That user name is taken."
        c.execute("INSERT INTO users(name, pw_hash, created, invited_by, note) VALUES (?,?,?,?,?)",
                  (name, pw_hash, time.time(), inv["created_by"], inv["note"]))
        c.execute("UPDATE invites SET used_by = ?, used_at = ? WHERE id = ?", (name, time.time(), inv["id"]))
    return ""


# ----------------------------------------------------------------- users
def get(name: str) -> Optional[Dict]:
    try:
        with _db() as c:
            r = c.execute("SELECT * FROM users WHERE name = ?", (name.strip().lower(),)).fetchone()
    except sqlite3.Error:
        return None
    return dict(r) if r else None


def active(name: str) -> bool:
    u = get(name)
    return bool(u and not u["disabled"])


def touch_login(name: str):
    with _lock, _db() as c:
        c.execute("UPDATE users SET last_login = ? WHERE name = ?", (time.time(), name))


def list_users() -> List[Dict]:
    with _db() as c:
        rows = c.execute("SELECT name, created, invited_by, note, disabled, last_login FROM users ORDER BY created").fetchall()
    return [dict(r) for r in rows]


def set_disabled(name: str, disabled: bool) -> bool:
    with _lock, _db() as c:
        return c.execute("UPDATE users SET disabled = ? WHERE name = ?", (1 if disabled else 0, name)).rowcount > 0
