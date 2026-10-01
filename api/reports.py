"""
Reports: generate (background job), list, serve, and the automatic daily report.

The daily report runs inside the API: 10 minutes after the US close (from Alpaca's market
calendar, so early closes and holidays are handled) it builds the report for that New York
date, once. If the API starts later that evening it catches up for that day.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from autobot import report as rpt

from . import jobs
from .models import RunReq

router = APIRouter()
NY = ZoneInfo("America/New_York")
AUTO_STATE = rpt.REPORTS / ".auto.json"
AUTO_DELAY = timedelta(minutes=10)
_auto: Dict[str, Any] = {"enabled": True, "last_run": None, "last_error": None, "next_run": None}


class LiveReportReq(BaseModel):
    date_from: date
    date_to: Optional[date] = None


class BacktestReportReq(RunReq):
    strategy: str
    params: Dict[str, Any] = Field(default_factory=dict)
    days: Optional[int] = Field(5, ge=1, le=3650)  # None = the whole backtest


def _meta(p) -> Dict:
    name = p.stem
    kind = "backtest" if name.startswith("backtest_") else "daily" if len(name) == 10 else "range"
    return {"name": name, "kind": kind, "html": f"/reports/{p.name}",
            "pdf": f"/reports/{name}.pdf" if p.with_suffix(".pdf").exists() else None,
            "created": int(p.stat().st_mtime), "size": p.stat().st_size}


@router.get("/api/reports")
def list_reports():
    rpt.REPORTS.mkdir(exist_ok=True)
    items = sorted((_meta(p) for p in rpt.REPORTS.glob("*.html")), key=lambda m: m["created"], reverse=True)
    return {"reports": items, "auto": _auto}


@router.get("/reports/{fname}")
def serve(fname: str):
    p = (rpt.REPORTS / fname).resolve()
    if p.parent != rpt.REPORTS.resolve() or p.suffix not in (".html", ".pdf") or not p.exists():
        raise HTTPException(404, "No such report")
    return FileResponse(p, media_type="application/pdf" if p.suffix == ".pdf" else "text/html")


DOCS = rpt.ROOT / "docs"
DOC_TYPES = {".html": "text/html", ".pdf": "application/pdf", ".png": "image/png", ".md": "text/markdown"}


@router.get("/docs/{path:path}")
def serve_doc(path: str):
    """The SOP (docs/SOP.html, .pdf, .md and its screenshots) for the Help page."""
    p = (DOCS / path).resolve()
    if DOCS.resolve() not in p.parents or p.suffix not in DOC_TYPES or not p.exists():
        raise HTTPException(404, "No such document")
    return FileResponse(p, media_type=DOC_TYPES[p.suffix])


def _live_name(d0: date, d1: date) -> str:
    return d0.isoformat() if d0 == d1 else f"{d0.isoformat()}_to_{d1.isoformat()}"


@router.post("/api/reports/live")
def generate_live(req: LiveReportReq):
    d0, d1 = req.date_from, req.date_to or req.date_from
    if d1 < d0:
        raise HTTPException(422, "The end date is before the start date")

    def work(progress):
        progress(0.1, "reading the account and decision log")
        ri = rpt.from_live(d0, d1)
        progress(0.6, "writing HTML and PDF")
        return {**rpt.write(ri, _live_name(d0, d1)), "name": _live_name(d0, d1)}

    return jobs.submit("report", work).public()


@router.post("/api/reports/backtest")
def generate_backtest(req: BacktestReportReq):
    from autobot.backtest import CostModel
    from autobot.strategies import STRATEGIES

    from .main import load
    if req.strategy not in STRATEGIES:
        raise HTTPException(404, f"Unknown strategy '{req.strategy}'")
    data = load(req.data)

    def work(progress):
        progress(0.1, "re-running the backtest with decision records")
        ri = rpt.from_backtest(data, req.strategy, req.params, CostModel(req.fee_bps, req.slippage_bps),
                               req.allow_short, is_demo=req.data.source == "demo", days=req.days)
        progress(0.6, "writing HTML and PDF")
        name = f"backtest_{req.strategy}_{datetime.now(rpt.LOCAL):%Y%m%d_%H%M%S}"
        return {**rpt.write(ri, name), "name": name}

    return jobs.submit("report", work).public()


# ----------------------------------------------------------------- automatic daily report
def _state() -> Dict:
    try:
        return json.loads(AUTO_STATE.read_text())
    except (OSError, ValueError):
        return {}


def _close_today(tc) -> Optional[datetime]:
    """Today's US close (UTC) from Alpaca's calendar, or None if the market isn't open today."""
    from alpaca.trading.requests import GetCalendarRequest
    today = datetime.now(NY).date()
    cal = tc.get_calendar(GetCalendarRequest(start=today, end=today))
    if not cal:
        return None
    c = cal[0]
    close = c.close if isinstance(c.close, datetime) else datetime.combine(c.date, c.close)
    if close.tzinfo is None:
        close = close.replace(tzinfo=NY)
    return close.astimezone(timezone.utc)


def auto_tick(tc, now: Optional[datetime] = None) -> Optional[str]:
    """One check of the daily schedule. Returns the report name if one was written."""
    now = now or datetime.now(timezone.utc)
    close = _close_today(tc)
    if close is None:
        _auto["next_run"] = None
        return None
    due = close + AUTO_DELAY
    _auto["next_run"] = due.isoformat()
    day = close.astimezone(NY).date()
    if now < due or _state().get("last_date") == day.isoformat():
        return None
    ri = rpt.from_live(day, day, tc=tc)
    rpt.write(ri, day.isoformat())
    rpt.REPORTS.mkdir(exist_ok=True)
    AUTO_STATE.write_text(json.dumps({"last_date": day.isoformat(), "at": now.isoformat()}))
    _auto.update(last_run=now.isoformat(), last_error=None)
    return day.isoformat()


# ----------------------------------------------------------------- nightly recalibration
RECAL_DELAY = AUTO_DELAY + timedelta(minutes=5)  # after the daily report
_recal: Dict[str, Any] = {"last_run": None, "last_date": None, "last_error": None, "running": False}


def _recal_state_path():
    from autobot import recalibrate
    return recalibrate.OUT_DIR / ".auto.json"


def _last_session(tc, now: datetime) -> Optional[date]:
    """The latest New York trading day whose close + RECAL_DELAY has passed (Alpaca's calendar)."""
    from alpaca.trading.requests import GetCalendarRequest
    today = now.astimezone(NY).date()
    cal = tc.get_calendar(GetCalendarRequest(start=today - timedelta(days=10), end=today))
    best = None
    for c in cal:
        close = c.close if isinstance(c.close, datetime) else datetime.combine(c.date, c.close)
        if close.tzinfo is None:
            close = close.replace(tzinfo=NY)
        if close.astimezone(timezone.utc) + RECAL_DELAY <= now:
            d = c.date if isinstance(c.date, date) else close.astimezone(NY).date()
            best = max(best, d) if best else d
    return best


def recal_tick(tc, now: Optional[datetime] = None, run=None, run_universe=None) -> Optional[str]:
    """Run the universe screener (when due) and the recalibration once for the latest finished
    session. Catches up once after the PC slept; never runs twice for the same date.
    Proposals only: config.yaml is not touched here."""
    from autobot import recalibrate, universe
    now = now or datetime.now(timezone.utc)
    day = _last_session(tc, now)
    if day is None:
        return None
    path = _recal_state_path()
    try:
        last = json.loads(path.read_text()).get("last_date")
    except (OSError, ValueError):
        last = None
    if last and last >= day.isoformat():
        return None
    _recal["running"] = True
    try:
        u = universe.settings()
        uni = None
        if universe.due(day, u):
            uni = (run_universe or universe.run)(day)
        res = (run or recalibrate.run)(as_of=day, universe_result=uni)
    finally:
        _recal["running"] = False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_date": day.isoformat(), "at": now.isoformat(), "changes": res.get("changes", [])}))
    _recal.update(last_run=now.isoformat(), last_date=day.isoformat(), last_error=None)
    if res.get("changes") and recalibrate.settings().get("mode") == "apply":
        from autobot import recal_apply
        recal_apply.apply(res, *recal_apply.broker_view(tc), now=now, by="scheduler (apply mode)")
    return day.isoformat()


def pending_tick(tc, now: Optional[datetime] = None):
    """Apply pending recalibration changes once they are allowed (market closed / coin flat)."""
    from autobot import recal_apply
    if not recal_apply.status()["pending"]:
        return None
    return recal_apply.tick(*recal_apply.broker_view(tc), now=now)


def _loop():
    from alpaca.trading.client import TradingClient

    from autobot.data import _keys, with_timeout
    tc = None
    while True:
        try:
            if tc is None:
                tc = with_timeout(TradingClient(*_keys(), paper=True))
            auto_tick(tc)
        except Exception as e:  # noqa: BLE001  keep the scheduler alive; show the error on the Reports page
            _auto["last_error"] = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC: {type(e).__name__}: {e}"
        try:
            if tc is not None and time.time() - _recal.get("failed_at", 0) > 3600:  # after a failure, retry hourly
                recal_tick(tc)
            if tc is not None:
                pending_tick(tc)
        except Exception as e:  # noqa: BLE001  a failed recalibration must not stop the report scheduler
            _recal["failed_at"] = time.time()
            _recal["last_error"] = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC: {type(e).__name__}: {e}"
        time.sleep(60)


def start_scheduler():
    threading.Thread(target=_loop, name="daily-report", daemon=True).start()
