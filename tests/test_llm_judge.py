"""Shadow AI judge (no API calls here): only trading-job orders are signals, the question uses
only bars that had ended and news received before the decision, and the estimate needs no API."""
import numpy as np
import pandas as pd

from autobot import llm_judge as lj


def bars():
    idx = pd.date_range("2026-10-05 12:00", periods=60, freq="5min", tz="UTC")   # 08:00-12:55 New York
    c = 100 + np.arange(60) * 0.1
    return pd.DataFrame({"open": c, "high": c + 0.05, "low": c - 0.05, "close": c, "volume": 1.0}, index=idx)


REC = {"type": "decision", "job": "auto_SPY", "symbol": "SPY", "strategy": "donchian_breakout", "action": "buy",
       "qty": 12, "position_before": 0, "target": 12, "reason": "breakout", "time": "2026-10-05T13:45:30+00:00"}


def test_only_trading_job_orders_are_signals():
    assert lj.is_signal(REC)
    assert not lj.is_signal({**REC, "job": "core"})
    assert not lj.is_signal({**REC, "action": "no change"})
    assert not lj.is_signal({**REC, "type": "event"})


def test_question_uses_only_finished_bars_regular_hours_and_earlier_news():
    news = pd.DataFrame({"t_info": pd.to_datetime(["2026-10-05T13:00:00Z", "2026-10-05T14:00:00Z"]),
                         "materiality": [4, 5], "headline": ["before the decision", "after the decision"]})
    q = lj.context(REC, [], bars(), news)
    # decision 09:45:30 NY: the 09:40 bar (ends 09:45) is the last one; 09:45 is still forming
    closes = q.split("Last 5-minute closes")[1].splitlines()[0]
    assert "09:40 " in closes and "09:45 " not in closes and "08:" not in closes   # finished bars, regular hours only
    assert "before the decision" in q and "after the decision" not in q


def test_estimate_needs_no_api(monkeypatch):
    monkeypatch.setattr(lj, "decisions", lambda *a: [REC, {**REC, "action": "sell", "time": "2026-10-05T14:30:00+00:00", "position_before": 12, "target": 0}])
    monkeypatch.setattr(lj, "load_bars", lambda *a: bars())
    monkeypatch.setattr(lj, "load_news", lambda *a: None)
    monkeypatch.setattr(lj, "client", lambda: (_ for _ in ()).throw(AssertionError("no API in estimate")))
    est = lj.estimate("2026-10-05")
    assert est["signals"] == 2 and est["per_model"]["claude-opus-5-5"]["per_day_usd"] > 0
