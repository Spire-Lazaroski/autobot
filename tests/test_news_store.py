"""News store: exact timestamps, edits applied in place (never counted twice), backfill paging."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from autobot import news


def item(i, headline="Apple beats estimates", created="2026-09-29T13:31:00Z", updated=None, syms=("AAPL",)):
    return {"id": i, "headline": headline, "summary": "s", "author": "a", "source": "benzinga", "url": "u",
            "symbols": list(syms), "created_at": created, "updated_at": updated or created}


def test_store_keeps_times_and_origin(tmp_path):
    db = tmp_path / "n.db"
    news.store([item(1)], "stream", received_at="2026-09-29T13:31:04.250000+00:00", path=db)
    news.store([item(2, "Old story")], "backfill", received_at="ignored", path=db)
    with news.connect(db) as con:
        a = dict(con.execute("SELECT * FROM items WHERE id=1").fetchone())
        b = dict(con.execute("SELECT * FROM items WHERE id=2").fetchone())
    assert a["created_at"] == "2026-09-29T13:31:00+00:00" and a["received_at"] == "2026-09-29T13:31:04.250000+00:00"
    assert a["origin"] == "stream" and b["received_at"] is None and b["origin"] == "backfill"


def test_edit_updates_in_place_and_duplicates_are_ignored(tmp_path):
    db = tmp_path / "n.db"
    assert news.store([item(1)], "stream", received_at="t0", path=db)["new"] == 1
    assert news.store([item(1)], "poll", received_at="t1", path=db)["same"] == 1  # the poll sees it again
    c = news.store([item(1, "Apple beats estimates, raises guidance", updated="2026-09-29T13:40:00Z")], "stream", received_at="t2", path=db)
    assert c["updated"] == 1
    with news.connect(db) as con:
        rows = con.execute("SELECT * FROM items").fetchall()
    assert len(rows) == 1
    r = dict(rows[0])
    assert r["n_updates"] == 1 and r["received_at"] == "t0" and r["last_update_received_at"] == "t2"
    assert "guidance" in r["headline"]


def test_hash_ignores_case_punctuation_and_symbol_order():
    assert news.story_hash("Apple Beats Estimates!", ["TSLA", "AAPL"]) == news.story_hash("apple beats estimates", ["aapl", "tsla"])


def test_backfill_pages_month_by_month(tmp_path):
    calls = []

    class Client:
        def get_news(self, req):
            calls.append((req.start, req.end))
            return SimpleNamespace(data={"news": [item(len(calls), f"story {len(calls)}", created=req.start.isoformat())]})
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    c = news.backfill(["AAPL"], start, start + timedelta(days=95), client=Client(), path=tmp_path / "n.db")
    assert c["new"] == len(calls) == 4
    assert calls[0][0] == start and calls[-1][1] == start + timedelta(days=95)
    assert news.meta_get("backfill_to", tmp_path / "n.db") == (start + timedelta(days=95)).isoformat()


def _events(db):
    with news.connect(db) as con:
        return [dict(r) for r in con.execute("SELECT * FROM events ORDER BY id")]


def test_reposts_and_rewrites_collapse_into_one_event(tmp_path):
    db = tmp_path / "n.db"
    news.store([
        item(1, "Apple Beats Q3 Estimates, Raises Guidance", created="2026-09-29T20:05:00Z"),
        item(2, "apple beats q3 estimates raises guidance", created="2026-09-29T20:07:00Z"),          # repost
        {**item(3, "Apple beats Q3 estimates and raises its guidance", created="2026-09-29T20:30:00Z"), "source": "other"},
        item(4, "Tesla recalls 2,000 vehicles", created="2026-09-29T20:10:00Z", syms=("TSLA",)),        # different story
        item(5, "Apple Beats Q3 Estimates, Raises Guidance", created="2026-09-30T08:00:00Z"),          # 12 h later: new event
    ], "backfill", path=db)
    c = news.collapse_events(db)
    ev = _events(db)
    assert c == {"new_events": 3, "joined": 2}
    apple = ev[0]
    assert apple["n_items"] == 3 and apple["n_sources"] == 2 and apple["created_at"] == "2026-09-29T20:05:00+00:00"
    assert news.collapse_events(db) == {"new_events": 0, "joined": 0}  # incremental: nothing left


def test_event_keeps_earliest_receive_time(tmp_path):
    db = tmp_path / "n.db"
    news.store([item(1, "NVDA announces buyback", created="2026-09-29T14:00:00Z", syms=("NVDA",))], "poll", received_at="2026-09-29T14:01:30+00:00", path=db)
    news.store([item(2, "NVDA announces buyback", created="2026-09-29T14:00:05Z", syms=("NVDA",))], "stream", received_at="2026-09-29T14:00:07+00:00", path=db)
    news.collapse_events(db)
    ev = _events(db)
    assert len(ev) == 1 and ev[0]["received_at"] == "2026-09-29T14:00:07+00:00"


def test_data_releases_with_different_figures_stay_separate(tmp_path):
    db = tmp_path / "n.db"
    news.store([item(1, "USA Core PCE Price Index (YoY) For Feb 3.0% Vs 3.0% Est", created="2026-03-27T12:30:00Z", syms=("SPY",)),
                item(2, "USA PCE Price index (YoY) For Feb 2.8% Vs 2.8% Est.", created="2026-03-27T12:30:05Z", syms=("SPY",))], "backfill", path=db)
    assert news.collapse_events(db)["new_events"] == 2


def test_different_analysts_are_different_events(tmp_path):
    db = tmp_path / "n.db"
    news.store([item(1, "Rosenblatt Maintains Buy on NVIDIA, Raises Price Target to $240", created="2026-08-28T12:00:00Z", syms=("NVDA",)),
                item(2, "Goldman Sachs Maintains Buy on NVIDIA, Raises Price Target to $240", created="2026-08-28T12:05:00Z", syms=("NVDA",)),
                item(3, "Rosenblatt Maintains Buy on NVIDIA, Raises Price Target to $240", created="2026-08-28T12:06:00Z", syms=("NVDA",))], "backfill", path=db)
    assert news.collapse_events(db) == {"new_events": 2, "joined": 1}


def test_template_headlines_and_close_figures_stay_separate(tmp_path):
    db = tmp_path / "n.db"
    news.store([item(1, "KC Fed Composite Index For May -3 Vs -4 Prior", created="2026-05-22T15:00:00Z", syms=("SPY",)),
                item(2, "KC Fed Manufacturing Index For May -10 Vs -5 Prior", created="2026-05-22T15:00:02Z", syms=("SPY",)),
                item(3, "USA S&P Global Services PMI For March 52.9 Vs 54.0 Est.", created="2026-03-24T13:45:00Z", syms=("SPY",)),
                item(4, "USA S&P Global Composite PMI For March 52.9 Vs 54.2 Prior", created="2026-03-24T13:45:01Z", syms=("SPY",)),
                item(5, "What's Going On With AMD Stock Tuesday?", created="2026-06-02T15:00:00Z", syms=("NVDA",)),
                item(6, "What's Going On With CoreWeave Stock Tuesday?", created="2026-06-02T15:10:00Z", syms=("NVDA",))], "backfill", path=db)
    assert news.collapse_events(db)["new_events"] == 6


def test_real_rewrites_still_collapse(tmp_path):
    db = tmp_path / "n.db"
    news.store([item(1, "'Makary's Time Atop FDA Over, Diamantas Named Acting Commissioner' - Politico", created="2026-06-02T15:00:00Z", syms=("SPY",)),
                item(2, "'Makary's time atop FDA over, Diamantas named acting commissioner' - Politico", created="2026-06-02T15:03:00Z", syms=("SPY",))], "backfill", path=db)
    assert news.collapse_events(db) == {"new_events": 1, "joined": 1}
