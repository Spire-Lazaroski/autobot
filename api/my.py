"""An invited user's own bot: status, setup, Start, Stop (api/userbots.py). Only ever the signed-in user's own."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import credentials, userbots
from .connect import _who

router = APIRouter()


def _user(request: Request) -> str:
    name, role = _who(request)
    if role != "user":
        raise HTTPException(400, "The lab's own bot is run from the Live page.")
    return name


@router.get("/api/my/bot")
def my_bot(request: Request):
    from autobot import presets
    name = _user(request)
    st = userbots.settings(name)
    conn = credentials.status(name)
    return {"user": name, "connected": conn.get("connected", False), "connection": conn, "settings": st,
            "presets": presets.PRESETS, "study": presets.saved(), "bot": userbots.state(name),
            "account": userbots.account(name) if conn.get("connected") else None, **userbots.recent(name)}


class SetupReq(BaseModel):
    setup: str
    jobs_on: bool = False


@router.post("/api/my/setup")
def my_setup(req: SetupReq, request: Request):
    name = _user(request)
    try:
        userbots.save_settings(name, req.setup, req.jobs_on)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    running = userbots.state(name)["running"]
    return {"ok": True, "settings": userbots.settings(name),
            "message": "Saved." + (" Your running bot uses it from its next bar; the long-term holdings are resized at its "
                                   "next tick while the market is open." if running else "")}


@router.post("/api/my/start")
def my_start(request: Request):
    name = _user(request)
    try:
        pid = userbots.start(name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "pid": pid, "message": "Your bot is starting. It trades while the US market is open (15:30-22:00 your time)."}


class StopReq(BaseModel):
    confirm: bool = False
    include_core: bool = False


@router.post("/api/my/stop")
def my_stop(req: StopReq, request: Request):
    name = _user(request)
    if not req.confirm:
        raise HTTPException(400, "Stopping closes the trading jobs' positions. Send {\"confirm\": true}.")
    s = userbots.state(name)
    if not (s["running"] or s["unresponsive"]):
        raise HTTPException(409, "Your bot isn't running.")
    userbots.stop(name, req.include_core)
    return {"ok": True, "message": "Your bot is stopping: it closes the trading jobs' positions"
                                   + (" and the long-term holdings" if req.include_core else " and keeps the long-term holdings")
                                   + ", then stops."}
