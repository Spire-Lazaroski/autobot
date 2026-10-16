"""
Each invited user's connection to their own Alpaca PAPER account (multi-user step 2).

Two ways in:
  1. Connect with Alpaca (OAuth, Alpaca's official "Connect API"): the user signs in on Alpaca's own page and allows
     this app to trade one paper account (env=paper). Alpaca sends back a one-time code; the server exchanges it for
     a token. The user's Alpaca password never reaches this app.
  2. Fallback: the user pastes a paper API key pair (key ids of paper keys start with "PK").
Either way the credential is tested against the PAPER API before it is kept, and stored encrypted (Fernet,
cryptography) in data/users.db. The encryption key: CREDENTIALS_KEY from the environment if set, else made once and
kept in logs/.credentials_key (both on the server's permanent volume; the environment is the better place).

Settings (environment): ALPACA_OAUTH_CLIENT_ID, ALPACA_OAUTH_CLIENT_SECRET (from registering the app with Alpaca),
APP_URL (e.g. https://spire.ristesky.com: the redirect address registered with Alpaca is APP_URL + CALLBACK).
Paper only: there is no live-money path; tokens are only ever used against paper-api.alpaca.markets.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.parse
from typing import Dict, Optional

import requests

from . import users
from .settings import LOGS

AUTHORIZE_URL = "https://app.alpaca.markets/oauth/authorize"
TOKEN_URL = "https://api.alpaca.markets/oauth/token"
PAPER_API = "https://paper-api.alpaca.markets"
CALLBACK = "/api/alpaca/callback"
SCOPES = "account:write trading data"
STATE_TTL_S = 600

_states: Dict[str, Dict] = {}          # OAuth state -> {user, at}: one use, 10 minutes
_lock = threading.Lock()
_fernet = None


# ----------------------------------------------------------------- settings
def client_id() -> str:
    return os.environ.get("ALPACA_OAUTH_CLIENT_ID", "").strip()


def client_secret() -> str:
    return os.environ.get("ALPACA_OAUTH_CLIENT_SECRET", "").strip()


def oauth_available() -> bool:
    return bool(client_id() and client_secret())


def redirect_uri(fallback_base: str) -> str:
    return (os.environ.get("APP_URL", "").strip().rstrip("/") or fallback_base.rstrip("/")) + CALLBACK


# ----------------------------------------------------------------- encryption
def _f():
    global _fernet
    if _fernet is None:
        from cryptography.fernet import Fernet
        key = os.environ.get("CREDENTIALS_KEY", "").strip().encode()
        if not key:
            p = LOGS / ".credentials_key"
            try:
                key = p.read_bytes().strip()
            except OSError:
                key = Fernet.generate_key()
                LOGS.mkdir(parents=True, exist_ok=True)
                p.write_bytes(key)
                try:
                    os.chmod(p, 0o600)
                except OSError:
                    pass
        _fernet = Fernet(key)
    return _fernet


def _db() -> sqlite3.Connection:
    c = users._db()
    c.execute("""CREATE TABLE IF NOT EXISTS credentials (
        user TEXT PRIMARY KEY, method TEXT NOT NULL, secret BLOB NOT NULL, account_tail TEXT, connected REAL NOT NULL)""")
    return c


def save(user: str, method: str, cred: Dict, account_tail: str):
    blob = _f().encrypt(json.dumps(cred).encode())
    with _lock, _db() as c:
        c.execute("INSERT OR REPLACE INTO credentials(user, method, secret, account_tail, connected) VALUES (?,?,?,?,?)",
                  (user, method, blob, account_tail, time.time()))


def load(user: str) -> Optional[Dict]:
    """The decrypted credential: {"oauth_token": …} or {"key": …, "secret": …} (for the user's own bot only)."""
    with _db() as c:
        r = c.execute("SELECT secret FROM credentials WHERE user = ?", (user,)).fetchone()
    return json.loads(_f().decrypt(r["secret"])) if r else None


def status(user: str) -> Dict:
    with _db() as c:
        r = c.execute("SELECT method, account_tail, connected FROM credentials WHERE user = ?", (user,)).fetchone()
    return {"connected": bool(r), **({"method": r["method"], "account_tail": r["account_tail"], "since": r["connected"]} if r else {})}


def disconnect(user: str) -> bool:
    with _lock, _db() as c:
        return c.execute("DELETE FROM credentials WHERE user = ?", (user,)).rowcount > 0


# ----------------------------------------------------------------- checks against the PAPER API
def paper_account(cred: Dict) -> Dict:
    """GET /v2/account on the paper API with this credential; raises ValueError with a plain reason if it fails."""
    if "oauth_token" in cred:
        headers = {"Authorization": f"Bearer {cred['oauth_token']}"}
    else:
        headers = {"APCA-API-KEY-ID": cred["key"], "APCA-API-SECRET-KEY": cred["secret"]}
    try:
        r = requests.get(f"{PAPER_API}/v2/account", headers=headers, timeout=15)
    except requests.RequestException as e:
        raise ValueError(f"Alpaca could not be reached ({e.__class__.__name__}).") from e
    if r.status_code in (401, 403):
        raise ValueError("Alpaca refused it: this is not a working paper-account permission.")
    if not r.ok:
        raise ValueError(f"Alpaca answered {r.status_code}.")
    return r.json()


def tail(acct: Dict) -> str:
    n = str(acct.get("account_number", ""))
    return n[-4:] if n else ""


# ----------------------------------------------------------------- OAuth
def start(user: str, fallback_base: str) -> str:
    """The Alpaca address to send the user to (paper account only)."""
    state = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        for k in [k for k, v in _states.items() if now - v["at"] > STATE_TTL_S]:
            _states.pop(k, None)
        _states[state] = {"user": user, "at": now}
    q = {"response_type": "code", "client_id": client_id(), "redirect_uri": redirect_uri(fallback_base),
         "state": state, "scope": SCOPES, "env": "paper"}
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(q, quote_via=urllib.parse.quote)}"


def finish(state: str, code: str, fallback_base: str) -> str:
    """Exchange the code (server to server) and keep the token. Returns the user name; raises ValueError."""
    with _lock:
        st = _states.pop(state or "", None)
    if not st or time.time() - st["at"] > STATE_TTL_S:
        raise ValueError("This connection attempt expired or was not started here. Please press Connect again.")
    try:
        r = requests.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": code, "client_id": client_id(),
                                           "client_secret": client_secret(), "redirect_uri": redirect_uri(fallback_base)},
                          timeout=15)
    except requests.RequestException as e:
        raise ValueError(f"Alpaca could not be reached ({e.__class__.__name__}).") from e
    if not r.ok:
        raise ValueError("Alpaca did not accept the connection. Please try again.")
    token = r.json().get("access_token")
    if not token:
        raise ValueError("Alpaca sent no permission back. Please try again.")
    cred = {"oauth_token": token}
    acct = paper_account(cred)
    save(st["user"], "oauth", cred, tail(acct))
    return st["user"]
