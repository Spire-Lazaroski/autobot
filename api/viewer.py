"""Whose account a request is about: the lab's own (the admin, or the PC without a login) or an invited user's.

Every page an invited user may open (Live, Reports, setups) asks this first and reads only that account's files and
broker connection: the user's home (data/users/<name>: config.yaml, logs/, reports/) and their own paper keys.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from . import auth
from .settings import CONFIG_PATH, LOGS, ROOT


@dataclass(frozen=True)
class Viewer:
    key: str                     # "lab" or "user:<name>" (cache key)
    user: Optional[str]          # None for the lab
    home: Path
    logs: Path
    cfg_path: Path
    reports: Path

    @property
    def is_user(self) -> bool:
        return self.user is not None


LAB = Viewer("lab", None, ROOT, LOGS, CONFIG_PATH, ROOT / "reports")


def for_user(name: str) -> Viewer:
    from . import userbots
    h = userbots.home(name)
    return Viewer(f"user:{name}", name, h, h / "logs", h / "config.yaml", h / "reports")


def from_cookie(token: Optional[str]) -> Viewer:
    """The middleware has already refused anyone not signed in (on the server); here: lab or user."""
    name = auth.session_user(token) if token else None
    if name and auth.role_of(name) == "user":
        return for_user(name)
    return LAB


def of(request) -> Viewer:
    return from_cookie(request.cookies.get(auth.COOKIE))


_clients: Dict[str, object] = {}
_lock = threading.Lock()


def trading_client(v: Viewer):
    """The PAPER trading client for this account: the lab's keys, or the user's own (never live)."""
    if not v.is_user:
        from .live import _client
        return _client()
    from . import credentials
    cred = credentials.load(v.user)
    if not cred or "key" not in cred:
        raise RuntimeError("Your Alpaca paper account isn't connected.")
    with _lock:
        cached = _clients.get(v.user)
        if cached is None or cached[0] != cred["key"]:
            from alpaca.trading.client import TradingClient

            from autobot.data import with_timeout
            cached = (cred["key"], with_timeout(TradingClient(cred["key"], cred["secret"], paper=True)))
            _clients[v.user] = cached
        return cached[1]
