"""Reports API and the automatic daily report schedule (fake market calendar, no Alpaca)."""
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import api.reports as R
from api.main import app

NY = ZoneInfo("America/New_York")
client = TestClient(app)


class CalTC:
    def __init__(self, close_ny):
        self.close_ny = close_ny

    def get_calendar(self, req):
        if self.close_ny is None:
            return []
        return [SimpleNamespace(date=self.close_ny.date(), close=self.close_ny)]


@pytest.fixture
def sched(tmp_path, monkeypatch):
    monkeypatch.setattr(R.rpt, "REPORTS", tmp_path)
    monkeypatch.setattr(R, "AUTO_STATE", tmp_path / ".auto.json")
    made = []
    monkeypatch.setattr(R.rpt, "from_live", lambda d0, d1, tc=None: ("ri", d0))
    monkeypatch.setattr(R.rpt, "write", lambda ri, name, pdf=True: made.append(name) or {"html": name})
    today = datetime.now(NY).date()
    return made, datetime(today.year, today.month, today.day, 16, 0, tzinfo=NY)


def test_daily_report_waits_for_close_plus_10_min(sched):
    made, close = sched
    tc = CalTC(close)
    assert R.auto_tick(tc, now=(close + timedelta(minutes=9)).astimezone(timezone.utc)) is None
    assert made == []
    assert R.auto_tick(tc, now=(close + timedelta(minutes=10)).astimezone(timezone.utc)) == close.date().isoformat()
    assert R.auto_tick(tc, now=(close + timedelta(minutes=30)).astimezone(timezone.utc)) is None  # only once
    assert made == [close.date().isoformat()]


def test_catch_up_later_in_the_evening(sched):
    made, close = sched
    assert R.auto_tick(CalTC(close), now=(close + timedelta(hours=4)).astimezone(timezone.utc))
    assert made == [close.date().isoformat()]


def test_early_close_uses_the_calendar(sched):
    made, close = sched
    early = close.replace(hour=13)
    assert R.auto_tick(CalTC(early), now=(early + timedelta(minutes=11)).astimezone(timezone.utc))


def test_no_report_on_a_holiday(sched):
    made, _ = sched
    assert R.auto_tick(CalTC(None)) is None and made == []


def test_backtest_report_endpoint_and_serving(tmp_path, monkeypatch):
    monkeypatch.setattr(R.rpt, "REPORTS", tmp_path)
    r = client.post("/api/reports/backtest", json={"data": {"source": "demo", "symbols": ["SPY", "QQQ"], "timeframe": "15Min", "days": 30},
                                                   "strategy": "ema_cross", "days": 5})
    job = r.json()
    for _ in range(300):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    name = job["result"]["name"]
    listing = client.get("/api/reports").json()["reports"]
    assert listing[0]["name"] == name and listing[0]["kind"] == "backtest"
    page = client.get(listing[0]["html"])
    assert page.status_code == 200 and "SPIRE" in page.text and "Every trade" in page.text
    assert client.get("/reports/..%2Fconfig.yaml").status_code == 404
