"""News endpoints: collector status, what is stored, latest items."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter

from autobot import news

router = APIRouter()


def collector_state() -> dict:
    import news_collector  # the script at the repo root (the API runs from there)
    return news_collector.collector_state()


@router.get("/api/news/status")
def status():
    return {"collector": collector_state(), "db": news.stats(), "symbols": news.configured_symbols()}


@router.get("/api/news/items")
def items(symbol: Optional[str] = None, limit: int = 100):
    limit = max(1, min(limit, 500))
    with news.connect() as con:
        if symbol:
            rows = con.execute(
                "SELECT i.* FROM items i JOIN item_symbols s ON s.item_id = i.id WHERE s.symbol = ? "
                "ORDER BY i.created_at DESC LIMIT ?", (symbol.upper(), limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM items ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["symbols"] = json.loads(d["symbols"])
        out.append(d)
    return out


# ----------------------------------------------------------------- scores (step 3)
from pydantic import BaseModel, Field  # noqa: E402

from autobot import news_score  # noqa: E402

from . import jobs  # noqa: E402


_est = {"at": 0.0, "value": None}


def cached_estimate(force: bool = False):
    """The Claude cost estimate scans every unscored story (~10 s); keep it for 10 minutes."""
    import time
    if force or _est["value"] is None or time.time() - _est["at"] > 600:
        _est.update(at=time.time(), value=news_score.estimate())
    return _est["value"]


@router.get("/api/news/scores")
def scores(scorer: str = "rules", category: Optional[str] = None, symbol: Optional[str] = None,
           min_materiality: int = 1, limit: int = 60):
    news_score.ensure_schema()
    version = news_score.RULES_VERSION if scorer == "rules" else news_score.PROMPT_VERSION
    q = ("SELECT e.id event_id, e.created_at, e.headline, e.n_items, s.symbol, s.relevance, s.sentiment, s.surprise, "
         "s.category, s.materiality, s.rationale, s.model FROM scores s JOIN events e ON e.id = s.event_id "
         "WHERE s.scorer = ? AND s.version = ? AND s.materiality >= ?")
    args: list = [scorer, version, min_materiality]
    if category:
        q += " AND s.category = ?"; args.append(category)
    if symbol:
        q += " AND s.symbol = ?"; args.append(symbol.upper())
    q += " ORDER BY e.created_at DESC LIMIT ?"; args.append(max(1, min(limit, 500)))
    with news.connect() as con:
        rows = [dict(r) for r in con.execute(q, args)]
        summary = {s: {r["category"]: r["c"] for r in con.execute(
            "SELECT category, COUNT(*) c FROM scores WHERE scorer=? AND version=? GROUP BY category", (s, v))}
            for s, v in (("rules", news_score.RULES_VERSION), ("claude", news_score.PROMPT_VERSION))}
    return {"rows": rows, "summary": summary, "versions": {"rules": news_score.RULES_VERSION, "claude": news_score.PROMPT_VERSION},
            "model": news_score.CLAUDE_MODEL, "estimate": cached_estimate()}


class ClaudeRunReq(BaseModel):
    limit: int = Field(50, ge=1, le=2000)


@router.post("/api/news/score-claude")
def score_claude(req: ClaudeRunReq):
    def work(progress):
        out = news_score.run_claude(req.limit, progress)
        cached_estimate(force=True)
        return out
    return jobs.submit("news-score", work).public()


# ----------------------------------------------------------------- impact study (step 4)
from autobot import event_study  # noqa: E402


class ImpactReq(BaseModel):
    scorer: str = Field("rules", pattern="^(rules|claude)$")


@router.get("/api/news/impact")
def impact():
    """The last saved event study (data/event_study.json), or 404 if it hasn't been run."""
    from fastapi import HTTPException
    if not event_study.OUT.exists():
        raise HTTPException(404, "No impact study yet. Run it from the News page.")
    return json.loads(event_study.OUT.read_text(encoding="utf-8"))


@router.post("/api/news/impact")
def run_impact(req: ImpactReq):
    return jobs.submit("impact", lambda progress: {"counts": event_study.run(req.scorer, progress)["counts"]}).public()


# ----------------------------------------------------------------- news-guard study (step 5)
from autobot import news_guard_study  # noqa: E402


@router.get("/api/news/guard-study")
def guard_study():
    """The last saved news-guard study (data/news_guard_study.json), or 404 if it hasn't been run."""
    from fastapi import HTTPException
    if not news_guard_study.OUT.exists():
        raise HTTPException(404, "No news-guard study yet. Run it from the News page.")
    return json.loads(news_guard_study.OUT.read_text(encoding="utf-8"))


@router.post("/api/news/guard-study")
def run_guard_study():
    return jobs.submit("guard-study", lambda progress: {"generated": news_guard_study.run(progress)["generated"]}).public()
