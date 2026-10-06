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


def test_outcomes_and_compare(monkeypatch, tmp_path):
    import json
    monkeypatch.setattr(lj, "session_prices", lambda sym, day: {"open": 99.0, "closeout": 102.0})
    recs = [{**REC, "order_ids": ["a"], "fill_price": 100.0},
            {**REC, "action": "sell", "position_before": 12, "target": 0, "order_ids": ["b"], "fill_price": 101.0, "time": "2026-10-05T14:30:00+00:00"},
            {**REC, "order_ids": ["c"], "fill_price": 101.0, "time": "2026-10-05T15:00:00+00:00"},
            {**REC, "action": "sell", "position_before": 12, "target": 0, "order_ids": ["d"], "fill_price": 100.0, "time": "2026-10-05T15:30:00+00:00"}]
    val = lj.outcomes(recs)
    assert val["a"]["kind"] == "entry" and val["a"]["pct"] == 1.0 and val["a"]["usd"] == 12.0
    assert val["c"]["usd"] == round((100 / 101 - 1) * 101 * 12, 2)
    assert val["d"]["kind"] == "exit" and val["d"]["pct"] == 2.0 and val["d"]["usd"] == 24.0   # holding to the 102 close-out
    f = tmp_path / "j.jsonl"
    rows = [("a", "opus", "take"), ("b", "opus", "take"), ("c", "opus", "skip"), ("d", "opus", "skip"),
            ("a", "haiku", "skip"), ("c", "haiku", "skip")]
    f.write_text(chr(10).join(json.dumps({"mode": "replay", "prompt_version": lj.PROMPT_VERSION, "order_ids": [k], "decision_time": "2026-10-05T14:00:00+00:00",
                                        "symbol": "SPY", "action": "buy", "model": m, "verdict": v, "cost_usd": 0.01}) for k, m, v in rows))
    monkeypatch.setattr(lj, "decisions", lambda *a: recs)
    monkeypatch.setattr(lj, "broker_fills", lambda day: [
        {"symbol": "SPY", "side": "buy", "qty": 12, "px": 100.0, "t": "1"}, {"symbol": "SPY", "side": "sell", "qty": 12, "px": 101.0, "t": "2"},
        {"symbol": "SPY", "side": "buy", "qty": 12, "px": 101.0, "t": "3"}, {"symbol": "SPY", "side": "sell", "qty": 12, "px": 100.0, "t": "4"}])
    res = lj.compare(f)
    assert res["models"]["opus"]["usd_vs_bot"] == round(-val["c"]["usd"] + 24.0, 2)    # skipped a loser, held a winner
    assert res["models"]["haiku"]["usd_vs_bot"] == round(-12.0 - val["c"]["usd"], 2)
    assert res["agreement_%"] == 50.0 and res["bot_entries_usd"] == round(12.0 + val["c"]["usd"], 2)
    assert res["jobs_vs_hold"][0]["strategy_usd"] == round(12 * (101 - 100) + 12 * (100 - 101), 2)
    assert res["jobs_vs_hold"][0]["hold_usd"] == round(12 * 101 / 99 * 3, 2)


def test_settings_accept_one_model_or_several():
    assert lj.settings({"llm_judge": {"model": "claude-haiku-4-5"}})["models"] == ["claude-haiku-4-5"]
    assert lj.settings({"llm_judge": {"models": ["a", "b"]}})["models"] == ["a", "b"]
