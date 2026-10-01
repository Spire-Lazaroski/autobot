"""Nightly recalibration: causal (future bars never change a decision), the champion/challenger
rules, the job list, and the once-per-date schedule with catch-up."""
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from autobot import recalibrate as rc
from autobot.data import synthetic_bars
from autobot.strategies import STRATEGIES

KEEP = ["buy_and_hold", "ema_cross", "rsi_reversion", "donchian_breakout"]


def cfg(jobs=(), **over):
    r = {"lookback_days": 30, "warmup_days": 10, "train_days": 8, "test_days": 4, "min_trades": 3,
         "exclude": [n for n in STRATEGIES if n not in KEEP]}
    r.update(over)
    return {"live": {"timeframe": "5Min", "risk": {"flatten_before_close_min": 5}, "jobs": list(jobs)}, "recalibration": r}


@pytest.fixture(scope="module")
def bars():
    return synthetic_bars(["XTEST"], "5Min", 70, seed=11)["XTEST"]


def ny_dates(df):
    return np.asarray(df.index.tz_convert("America/New_York").date)


def test_future_bars_never_change_the_decision(bars, tmp_path):
    days = sorted(set(ny_dates(bars)))
    as_of = days[-12]
    past = bars[ny_dates(bars) <= as_of]
    a = rc.run(as_of, cfg(), fetch=lambda s, tf, d: {"XTEST": past}, symbols=["XTEST"], out_dir=tmp_path, write=False)
    b = rc.run(as_of, cfg(), fetch=lambda s, tf, d: {"XTEST": bars}, symbols=["XTEST"], out_dir=tmp_path, write=False)
    ra, rb = a["symbols"]["XTEST"], b["symbols"]["XTEST"]
    assert ra["decision"]["outcome"] != "no_data" and ra["windows"] >= 3   # a real study, not an empty one
    assert ra["decision"] == rb["decision"]
    for k in ("single", "meta", "challenger"):
        assert ra[k]["score"] == pytest.approx(rb[k]["score"]) and ra[k]["spec"] == rb[k]["spec"]
    assert a["proposed_jobs"] == b["proposed_jobs"]


def test_windows_cover_the_lookback_with_train_then_test():
    day = np.repeat(pd.date_range("2026-01-01", periods=30).date, 3)
    w = rc.windows(day, date(2026, 1, 5), train=8, test=4)
    assert len(w) == 5                                  # 26 days from Jan 5: windows at 0, 4, 8, 12, 16
    ta, tb, xa, xb = w[0]
    assert day[ta] == date(2026, 1, 5) and day[xa] == date(2026, 1, 13) and tb == xa
    assert all(w[i][3] == w[i + 1][2] for i in range(len(w) - 1))  # test slices are back to back


def test_stocks_are_flat_before_the_close(bars):
    sig = pd.Series(1.0, index=bars.index)
    out = rc.flat_before_close(sig, bars, 5)
    ny = bars.index.tz_convert("America/New_York")
    assert (out[(ny.hour == 15) & (ny.minute == 50)] == 0).all()     # the 15:50 bar closes at 15:55
    assert (out[(ny.hour == 15) & (ny.minute == 45)] == 1).all()


def test_pool_excludes_meta_news_daily_and_intraday_on_crypto():
    r = rc.settings(cfg(exclude=["connors_rsi2", "ibs_reversion", "news_guard"]))
    stock, coin = rc.eligible("NVDA", r), rc.eligible("BTC/USD", r)
    assert not {"regime_switch", "perf_rotation", "ensemble_vote", "news_guard", "connors_rsi2"} & set(stock)
    assert "vwap_reversion" in stock and "vwap_reversion" not in coin and "ema_cross" in coin


R = rc.settings(cfg(min_edge=0.25, min_trades=10, min_hold_days=5, max_changes_per_week=1))
INC = {"spec": {"strategy": "ema_cross", "params": {"fast": 9, "slow": 21}}, "score": 1.0, "trades": 30}
CH = {"spec": {"strategy": "rsi_reversion", "params": {"period": 7}}, "score": 1.3, "trades": 30}
TODAY = date(2026, 9, 30)


@pytest.mark.parametrize("ch,hist,outcome,change", [
    ({**CH, "score": 1.2}, {}, "keep_edge", False),                                  # better, but by less than min_edge
    ({**CH, "score": 0.9}, {}, "keep_better", False),
    ({**CH, "trades": 5}, {}, "keep_trades", False),                                 # too few trades
    (CH, {"since": "2026-09-28"}, "keep_hold", False),                               # incumbent only 2 days old
    (CH, {"since": "2026-09-01", "changes": ["2026-09-27"]}, "keep_changes", False), # already changed this week
    (CH, {"since": "2026-09-01", "changes": ["2026-09-20"]}, "replace", True),
    ({**CH, "spec": INC["spec"]}, {}, "keep_same", False),
])
def test_champion_challenger_rules(ch, hist, outcome, change):
    d = rc.decide("XTEST", INC, ch, R, hist, TODAY)
    assert (d["outcome"], d["change"]) == (outcome, change)
    assert d["reason"] and "{" not in d["reason"]


def test_new_job_must_beat_doing_nothing():
    none = {"spec": None, "score": 0.0, "trades": 0}
    assert rc.decide("X", none, {**CH, "score": 0.2}, R, {}, TODAY)["outcome"] == "no_job_weak"
    assert rc.decide("X", none, {**CH, "score": 0.3}, R, {}, TODAY)["outcome"] == "new_job"
    assert rc.decide("X", none, {**CH, "trades": 2}, R, {}, TODAY)["outcome"] == "no_job_trades"


def test_hand_made_symbols_are_left_alone_and_dropped_auto_jobs_removed(bars, tmp_path):
    jobs = [{"name": "trend", "strategy": "ema_cross", "params": {}, "symbols": ["SPY"], "allocation_pct": 20},
            {"name": "auto_OLD", "strategy": "ema_cross", "params": {}, "symbols": ["OLD"], "allocation_pct": 10}]
    uni = {"date": "2026-09-01", "selected": ["SPY", "XTEST"]}
    days = sorted(set(ny_dates(bars)))
    res = rc.run(days[-1], cfg(jobs), fetch=lambda s, tf, d: {"XTEST": bars}, out_dir=tmp_path, write=False,
                 universe_result=uni)
    assert set(res["symbols"]) == {"XTEST", "OLD"} and res["hand_made_symbols"] == ["SPY"]
    assert res["symbols"]["OLD"]["decision"]["outcome"] == "remove"
    assert all(j["name"] != "auto_OLD" for j in res["proposed_jobs"])
    assert all(j["name"].startswith("auto_") and len(j["symbols"]) == 1 for j in res["proposed_jobs"])


# ----------------------------------------------------------------- schedule (api/reports.py)
class Cal:
    def __init__(self, days):
        self.days = days

    def get_calendar(self, req):
        return [SimpleNamespace(date=d, close=datetime(d.year, d.month, d.day, 16, 0)) for d in self.days]


def test_recal_runs_once_per_session_and_catches_up(tmp_path, monkeypatch):
    from api import reports
    monkeypatch.setattr(reports, "_recal_state_path", lambda: tmp_path / ".auto.json")
    monkeypatch.setattr("autobot.universe.due", lambda day, u: False)
    ran = []
    run = lambda as_of, universe_result=None: ran.append(as_of) or {"changes": []}  # noqa: E731
    tc = Cal([date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)])
    before = datetime(2026, 9, 30, 20, 10, tzinfo=timezone.utc)      # 16:10 NY: too early for the 30th
    assert reports.recal_tick(tc, before, run=run) == "2026-09-29"   # catches up the 29th once
    assert reports.recal_tick(tc, before, run=run) is None           # never twice
    after = datetime(2026, 9, 30, 20, 20, tzinfo=timezone.utc)
    assert reports.recal_tick(tc, after, run=run) == "2026-09-30"
    assert reports.recal_tick(tc, after + timedelta(hours=5), run=run) is None
    assert ran == [date(2026, 9, 29), date(2026, 9, 30)]
    assert json.loads((tmp_path / ".auto.json").read_text())["last_date"] == "2026-09-30"
