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
