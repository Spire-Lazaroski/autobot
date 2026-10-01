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


class RulesReq(BaseModel):
    days: int = 60
    trigger_pct: float = 1.5
    giveback_pct: float = 50.0
    source: str = "alpaca"   # or "demo"


@router.post("/api/account-rules")
def account_rules(req: RulesReq):
    """Backtest the enabled jobs as one account: no rules / loss limit / loss limit + profit lock."""
    import yaml

    from autobot import account_rules as ar
    from autobot.data import synthetic_bars
    if not 5 <= req.days <= 365 or req.trigger_pct <= 0 or not 0 < req.giveback_pct < 100:
        raise HTTPException(400, "days 5-365, trigger above 0, giveback between 0 and 100.")
    cfg = yaml.safe_load(recalibrate.ROOT.joinpath("config.yaml").read_text(encoding="utf-8"))
    fetch = (lambda s, tf, d: synthetic_bars(s, tf, d)) if req.source == "demo" else None

    def work(progress):
        progress(0.1, "backtesting the account three ways")
        return ar.compare(cfg, req.days, req.trigger_pct, req.giveback_pct, fetch=fetch)
    return jobs.submit("account-rules", work).public()


class ExitStudyReq(BaseModel):
    days: int = 90
    source: str = "alpaca"   # or "demo"


@router.post("/api/exit-study")
def exit_study(req: ExitStudyReq):
    """The enabled jobs with no exit layer vs every trailing-exit setting, in-sample and walk-forward."""
    import yaml

    from autobot import exit_study as es
    from autobot.data import synthetic_bars
    if not 30 <= req.days <= 365:
        raise HTTPException(400, "days must be 30-365.")
    cfg = yaml.safe_load(recalibrate.ROOT.joinpath("config.yaml").read_text(encoding="utf-8"))
    fetch = (lambda s, tf, d: synthetic_bars(s, tf, d)) if req.source == "demo" else None
    return jobs.submit("exit-study", lambda progress: es.run(cfg, req.days, fetch=fetch, progress=progress)).public()


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
