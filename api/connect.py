"""Routes for connecting an invited user's own Alpaca paper account (api/credentials.py)."""
from __future__ import annotations

import html
import urllib.parse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from . import auth, credentials

router = APIRouter()


def _base(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{proto}://{host}"


def _who(request: Request) -> tuple[str, str]:
    name = auth.session_user(request.cookies.get(auth.COOKIE))
    role = auth.role_of(name or "") if name else ("admin" if not auth.required() else None)
    if not role:
        raise HTTPException(401, "Please sign in.")
    return name or "local", role


@router.get("/api/alpaca/status")
def status(request: Request):
    name, role = _who(request)
    if role == "admin":   # the lab's own bot trades with the server's keys (environment), not through this page
        return {"connected": True, "method": "server", "oauth_available": credentials.oauth_available()}
    return {**credentials.status(name), "oauth_available": credentials.oauth_available()}


@router.get("/api/alpaca/connect")
def connect(request: Request):
    """Send the user to Alpaca's own page to allow this app one PAPER account."""
    name, role = _who(request)
    if role != "user":
        raise HTTPException(400, "The lab's own account uses the server's keys.")
    if not credentials.oauth_available():
        raise HTTPException(503, "Connect with Alpaca isn't switched on yet. Use your paper keys below, or ask the admin.")
    return RedirectResponse(credentials.start(name, _base(request)), status_code=303)


def _bounce(target: str) -> HTMLResponse:
    """Back into the app with a navigation started by this site, so the sign-in cookie (SameSite=Strict) is sent."""
    t = html.escape(target, quote=True)
    return HTMLResponse(f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={t}">'
                        f'<script>location.replace({target!r})</script><a href="{t}">Continue</a>',
                        headers={"cache-control": "no-store", "referrer-policy": "no-referrer"})


@router.get(credentials.CALLBACK)
def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Alpaca sends the user back here (no session cookie arrives on this cross-site return; the one-time state
    ties it to the user who started)."""
    if error or not code:
        return _bounce("/#/?alpaca_error=" + urllib.parse.quote("The connection was cancelled at Alpaca."))
    try:
        credentials.finish(state, code, _base(request))
    except ValueError as e:
        return _bounce("/#/?alpaca_error=" + urllib.parse.quote(str(e)))
    return _bounce("/#/?alpaca=connected")


class KeysReq(BaseModel):
    key_id: str
    secret_key: str


@router.post("/api/alpaca/keys")
def keys(req: KeysReq, request: Request):
    """Fallback: a paper API key pair, tested on the paper API, kept encrypted."""
    name, role = _who(request)
    if role != "user":
        raise HTTPException(400, "The lab's own account uses the server's keys.")
    key, secret = req.key_id.strip(), req.secret_key.strip()
    if not key.startswith("PK"):
        raise HTTPException(400, "That isn't a paper key: paper key ids start with PK (Alpaca dashboard, Paper Trading, API Keys).")
    cred = {"key": key, "secret": secret}
    try:
        acct = credentials.paper_account(cred)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    credentials.save(name, "keys", cred, credentials.tail(acct))
    return {"ok": True, **credentials.status(name)}


@router.post("/api/alpaca/disconnect")
def disconnect(request: Request):
    name, role = _who(request)
    if role != "user":
        raise HTTPException(400, "The lab's own account uses the server's keys.")
    credentials.disconnect(name)
    return {"ok": True, "connected": False}
