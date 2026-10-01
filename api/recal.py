"""
Recalibration in the lab: the latest proposal, Apply (with confirm) / Reject, and Run now.

Apply only touches config.yaml through autobot.recal_apply (validated, backed up, atomic, never
mid-position). The bot picks the change up at its next bar; the lab never signals the bot.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from autobot import recal_apply, recalibrate, universe

from . import jobs
from . import reports

router = APIRouter()


class ApplyReq(BaseModel):
    confirm: bool = False
    date: Optional[str] = None          # must match the latest proposal (guards against applying a stale page)
    symbols: Optional[List[str]] = None


class RejectReq(BaseModel):
    date: str


def _client():
    from alpaca.trading.client import TradingClient

    from autobot.data import _keys, with_timeout
    return with_timeout(TradingClient(*_keys(), paper=True))


@router.get("/api/recalibration")
def latest() -> Dict[str, Any]:
    prop = recalibrate.latest()
    st = recal_apply.status()
    return {"proposal": prop, "mode": recalibrate.settings().get("mode", "propose"),
            "status": (st["decisions"].get(prop["date"]) or {}).get("status") if prop else None,
            "history": (st["decisions"].get(prop["date"]) or {}).get("history", [])[-5:] if prop else [],
            "pending": st["pending"], "backups": st["backups"], "scheduler": reports._recal,
            "universe": universe.latest()}


@router.post("/api/recalibration/apply")
def apply(req: ApplyReq):
    if not req.confirm:
        raise HTTPException(400, "Apply changes the bot's jobs. Send {\"confirm\": true}.")
    prop = recalibrate.latest()
    if not prop:
        raise HTTPException(404, "No recalibration proposal yet.")
    if req.date and req.date != prop["date"]:
        raise HTTPException(409, f"The proposal changed ({prop['date']}); reload the page before applying.")
    if not prop.get("changes"):
        raise HTTPException(400, "This proposal has no changes to apply.")
    market_open, positions = recal_apply.broker_view(_client())
    return recal_apply.apply(prop, market_open, positions, now=datetime.now(timezone.utc), only=req.symbols, by="lab")


@router.post("/api/recalibration/reject")
def reject(req: RejectReq):
    prop = recalibrate.latest()
    if not prop or prop["date"] != req.date:
        raise HTTPException(409, "That is not the latest proposal; reload the page.")
    return recal_apply.reject(req.date)


@router.post("/api/recalibration/run")
def run_now():
    """Run the recalibration now as of the last finished session (a background job). Proposal only."""
    if reports._recal.get("running"):
        raise HTTPException(409, "The nightly recalibration is running; try again in a few minutes.")

    def work(progress):
        tc = _client()
        day = reports._last_session(tc, datetime.now(timezone.utc))
        res = recalibrate.run(as_of=day, progress=progress)
        return {"date": res["date"], "changes": res["changes"]}
    return jobs.submit("recalibration", work).public()
