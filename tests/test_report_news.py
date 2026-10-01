"""News in the plain-English report: which stories appear, the news line on trades, the biggest
move, the watch list, and that every news sentence says it is context or timing only."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from autobot import news, report, report_news as rn
from autobot import news_guard as ng
from autobot.data import synthetic_bars

DAY_A = pd.Timestamp("2026-09-29 04:00", tz="UTC")   # midnight New York
DAY_B = pd.Timestamp("2026-09-30 04:00", tz="UTC")
EARN = "Apple Q4 Earnings Beat Estimates, Revenue Rises 8%"   # rules: earnings, materiality 4


def _db(tmp_path, monkeypatch, stories):
    """stories: (id, headline, symbol, received time UTC)"""
    db = tmp_path / "n.db"
    monkeypatch.setattr(news, "DB_PATH", db)
    for i, h, s, t in stories:
        news.store([{"id": i, "headline": h, "symbols": [s], "created_at": t}], "stream", received_at=pd.Timestamp(t).isoformat())
    news.collapse_events(db)
    return db


@pytest.fixture
def db(tmp_path, monkeypatch):
    return _db(tmp_path, monkeypatch, [
        (1, EARN, "AAPL", "2026-09-29T14:30:00Z"),
        (2, "Apple Shares Are Trading Higher; Here's Why", "AAPL", "2026-09-29T15:00:00Z"),   # materiality 1
        (3, "Fed Raises Rates By 50 Basis Points", "SPY", "2026-09-29T17:30:00Z"),            # 3: below the ETF bar of 4
        (4, "Apple Raises Guidance After Strong iPhone Sales", "AAPL", "2026-09-30T14:00:00Z"),  # the next day
    ])


def test_load_keeps_material_stories_in_the_period(db):
    st = rn.load(["AAPL", "SPY"], DAY_A, DAY_B)
    assert list(st.headline) == [EARN]
    assert st.iloc[0].scorer == "keyword rules" and st.iloc[0].materiality == 4


def test_no_database_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(news, "DB_PATH", tmp_path / "missing.db")
    nd = rn.collect(["AAPL"], DAY_A, DAY_B, "live")
    assert not nd["available"] and "No news database" in nd["note"]
    assert rn.watch_lines(nd) == [nd["note"]]


def _trade(entry, exit_=None, sym="AAPL"):
    return dict(symbol=sym, side="long", entry_time=pd.Timestamp(entry, tz="UTC"),
                exit_time=pd.Timestamp(exit_, tz="UTC") if exit_ else None)


def test_trade_note_is_context_only(db):
    nd = rn.collect(["AAPL"], DAY_A, DAY_B, "live")
    note = rn.trade_note(nd, _trade("2026-09-29 14:50", "2026-09-29 19:00"))
    assert "20 minutes before this buy" in note and "earnings" in note
    assert note.endswith(rn.CONTEXT_ONLY)
    assert "sell" not in note  # the exit was hours later
    assert rn.trade_note(nd, _trade("2026-09-29 16:00")) is None  # more than an hour after the story
    nd["news_aware"] = {"AAPL"}
    assert rn.trade_note(nd, _trade("2026-09-29 14:50")).endswith(rn.NEWS_RULES)


def test_biggest_move_reports_timing_only(db):
    idx = pd.date_range("2026-09-29 13:30", "2026-09-29 19:55", freq="5min", tz="UTC")
    close = np.full(len(idx), 100.0)
    k = idx.get_loc(pd.Timestamp("2026-09-29 14:40", tz="UTC"))
    close[k:] = 103.0  # +3% in the bar from 14:40, complete at 14:45
    df = pd.DataFrame({"close": close}, index=idx)
    nd = rn.collect(["AAPL"], DAY_A, DAY_B, "live")
    s = rn.biggest_move(nd, "AAPL", df)
    assert s.startswith("Biggest 15-minute move: +3.00% ending 10:45")
    assert "15 minutes before it ended" in s and "not that the story caused the move" in s
    nd["stories"] = nd["stories"].iloc[0:0]
    assert "No material story arrived in the hour before it" in rn.biggest_move(nd, "AAPL", df)


def test_watch_lines_upcoming_earnings_and_quiet_collector(db, monkeypatch):
    monkeypatch.setattr(ng, "earnings_sessions", lambda s: [date(2026, 10, 2), date(2026, 11, 30)] if s == "AAPL" else [])
    nd = rn.collect(["AAPL", "SPY"], DAY_A, DAY_B, "live", watch_symbols=["AAPL", "SPY"])
    lines = rn.watch_lines(nd)
    assert any(x.startswith("AAPL reports quarterly results") and "Fri 02 Oct" in x for x in lines)
    assert not any("30 Nov" in x for x in lines)  # beyond the next 7 days
    assert any("last story arrived at 13:30" in x for x in lines)  # 17:30 UTC is 13:30 NY, 2.5 h before the close


def test_report_html_has_the_news_section(db, monkeypatch):
    monkeypatch.setattr(ng, "earnings_sessions", lambda s: [date(2026, 9, 29)] if s == "AAPL" else [])
    bars = synthetic_bars(["AAPL"], "5Min", 3, seed=1)["AAPL"]
    shift = pd.Timestamp("2026-09-29 13:30", tz="UTC") - bars.index[0]  # move the bars onto the test day (09:30 NY)
    bars.index = bars.index + shift
    bars = bars[bars.index < DAY_B]
    ri = report.from_backtest({"AAPL": bars}, "ema_cross", is_demo=False)
    html = report.render(ri)  # backtest: condensed
    assert "News on our symbols" in html and "Material stories in the period: AAPL 1." in html and EARN[:30] in html
    assert "The bot does not trade on news" in html and "Materiality" in html
    ri.kind, ri.news = "live", rn.collect(["AAPL"], DAY_A, DAY_B, "live")  # the daily layout, per symbol
    html = report.render(ri)
    assert "1 material story (1 earnings)" in html and "Earnings day" in html and "Biggest 15-minute move" in html


def test_demo_reports_have_no_news(tmp_path, monkeypatch):
    monkeypatch.setattr(news, "DB_PATH", tmp_path / "missing.db")
    bars = synthetic_bars(["AAPL"], "15Min", 5, seed=2)
    ri = report.from_backtest(bars, "ema_cross", is_demo=True)
    assert "Demo data has no news." in report.render(ri)


def test_collector_warning_counts_backfilled_stories(tmp_path, monkeypatch):
    db = tmp_path / "n.db"
    monkeypatch.setattr(news, "DB_PATH", db)
    news.store([{"id": 1, "headline": EARN, "symbols": ["AAPL"], "created_at": "2026-09-29T14:30:00Z"}], "backfill", path=db)
    news.collapse_events(db)
    lines = rn.watch_lines(rn.collect(["AAPL"], DAY_A, DAY_B, "live"))
    assert any("wasn't receiving stories live" in x and "(1 story was filled in afterwards)" in x for x in lines)
