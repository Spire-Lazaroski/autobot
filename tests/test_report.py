"""Decision records and the plain-English report: complete records, no invented reasons."""
import json
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yaml

import live
from autobot import report
from autobot.backtest import run_backtest
from autobot.data import synthetic_bars
from autobot.decisions import Context, Recorder, backtest_decisions, read_jsonl, regime_params
from autobot.meta import classify_regimes
from autobot.strategies import get_signal
from tests.test_live import BASE, FakeTC

DECISION_KEYS = {"type", "time", "bar_time", "job", "symbol", "strategy", "active", "regime", "regime_values",
                 "indicators", "signal", "position_before", "target", "action", "order_ids", "fill_price", "reason", "price"}


@pytest.fixture(scope="module")
def demo():
    return synthetic_bars(["SPY", "QQQ"], "15Min", 40, seed=5)


def test_live_step_writes_complete_decisions(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "LOGS", tmp_path)
    bars = synthetic_bars(["X"], "15Min", 60, seed=9)["X"]
    monkeypatch.setattr(live, "_download", lambda *a, **k: bars)
    cfg = yaml.safe_load(yaml.safe_dump(BASE))
    cfg["live"]["jobs"][1].update(strategy="regime_switch", params={})
    bot = live.Bot(cfg, tc=FakeTC())
    bot.rec = Recorder(tmp_path / "decisions.jsonl")
    bot.step_once()
    recs = read_jsonl(tmp_path / "decisions.jsonl")
    decisions = [r for r in recs if r["type"] == "decision"]
    assert {r["symbol"] for r in decisions} == {"SPY", "AAPL"}
    for r in decisions:
        assert DECISION_KEYS <= set(r), DECISION_KEYS - set(r)
        assert r["reason"] and "error" not in r["action"]
    meta = next(r for r in decisions if r["strategy"] == "regime_switch")
    assert meta["regime_values"]["trend_measure"] == "adx" and "trend_strength" in meta["regime_values"]
    traded = [r for r in decisions if r["action"] != "no change"]
    assert all(r["order_ids"] for r in traded)


def test_recorder_logs_regime_change_with_before_after(tmp_path, demo):
    df = demo["SPY"]
    reg = classify_regimes(df, **regime_params("ema_cross", {}))  # the recorder's settings
    i = next(k for k in range(200, len(df)) if reg.iloc[k] != reg.iloc[k - 1] and reg.iloc[k - 1] != "warmup")
    rec = Recorder(tmp_path / "d.jsonl")
    for k in (i - 1, i):
        rec.bar("job", "SPY", Context("ema_cross", df.iloc[: k + 1], {}, False), 0, 0, "no change")
    changes = [r for r in read_jsonl(tmp_path / "d.jsonl") if r["type"] == "regime"]
    assert len(changes) == 1
    c = changes[0]
    assert c["from"] == reg.iloc[i - 1] and c["to"] == reg.iloc[i]
    assert "trend_strength" in c["before"] and "trend_strength" in c["after"]
    assert c["reason"].startswith("SPY moved from")


def test_backtest_decisions_match_position_changes(demo):
    df = demo["QQQ"]
    recs = backtest_decisions(df, "QQQ", "regime_switch", {})
    sig = get_signal("regime_switch", df, {})
    changes = int((sig.diff().fillna(sig) != 0).sum())
    assert len([r for r in recs if r["type"] == "decision"]) == changes
    assert all(r["from"] != r["to"] for r in recs if r["type"] in ("regime", "switch"))
    assert all(r["reason"] for r in recs)


def test_report_numbers_come_from_the_backtest(demo):
    ri = report.from_backtest(demo, "ema_cross", is_demo=True)
    a = report.analyse(ri)
    bts = [run_backtest(df, get_signal("ema_cross", df, {})) for df in demo.values()]
    port = pd.concat([b.ret for b in bts], axis=1).fillna(0).mean(axis=1)
    assert a["pnl_pct"] == pytest.approx(((1 + port).prod() - 1) * 100, rel=1e-9)
    html = report.render(ri)
    assert "Demo data" in html and "reason not recorded" not in html
    assert f"{len(ri.trades)} trade" in report.headline(ri, a, report.Words())


def test_missing_reason_is_never_invented(demo):
    ri = report.from_backtest(demo, "ema_cross", is_demo=True)
    ri.trades[0]["entry_reason"] = "reason not recorded"
    assert "reason not recorded" in report.render(ri)


class ReportTC:
    """Fake paper account for one day: 5-minute equity and two fills."""
    def __init__(self, day):
        idx = pd.date_range(f"{day} 13:30", f"{day} 20:00", freq="5min", tz="UTC")
        self.idx = idx
        self.eq = 100_000 + np.linspace(0, 250, len(idx))

    def get_portfolio_history(self, req):
        return SimpleNamespace(equity=list(self.eq), timestamp=[int(t.timestamp()) for t in self.idx])

    def get_orders(self, req):
        mk = lambda t, side, px, oid: SimpleNamespace(filled_at=self.idx[t].to_pydatetime(), symbol="SPY",  # noqa: E731
                                                      side=SimpleNamespace(value=side), filled_qty="10", filled_avg_price=str(px), id=oid)
        return [mk(10, "buy", 500.0, "o-1"), mk(40, "sell", 502.5, "o-2")]


def test_live_report_uses_logged_reasons(tmp_path, monkeypatch):
    day = "2026-09-29"
    monkeypatch.setattr(report, "LOGS", tmp_path)
    monkeypatch.setattr(report, "REPORTS", tmp_path / "reports")
    tc = ReportTC(day)
    lines = [
        {"type": "decision", "source": "live", "time": tc.idx[10].isoformat(), "bar_time": tc.idx[9].isoformat(), "job": "trend",
         "symbol": "SPY", "strategy": "ema_cross", "active": "ema_cross", "regime": "trend", "regime_values": {"direction": "up"},
         "indicators": {}, "signal": 1, "position_before": 0, "target": 10, "action": "buy", "qty": 10,
         "order_ids": ["o-1"], "fill_price": 500.0, "reason": "LOGGED ENTRY REASON."},
    ]
    (tmp_path / "decisions.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    import autobot.data
    bars = synthetic_bars(["SPY"], "5Min", 3)["SPY"]
    monkeypatch.setattr(autobot.data, "_download", lambda *a, **k: bars)
    ri = report.from_live(date.fromisoformat(day), date.fromisoformat(day), tc=tc,
                          config={"live": {"timeframe": "5Min", "risk": {"max_daily_loss_pct": 2}}})
    assert len(ri.trades) == 1 and ri.trades[0]["pnl"] == pytest.approx(25.0)
    html = report.render(ri)
    assert "logged entry reason" in html.lower()
    assert "reason not recorded" in html  # the exit had no decision record, and the report says so
    assert "made $250.00" in html


def test_cash_jumps_are_left_out_of_the_account_curve():
    idx = pd.date_range("2026-09-30 13:30", periods=6, freq="5min", tz="UTC")
    eq = pd.Series([100000, 100000, 200000, 199990, 199950, 199941.0], index=idx)  # Alpaca: deposit counted twice
    fixed, jumps = report.remove_cash_jumps(eq)
    assert list(fixed) == [100000, 100000, 100000, 99990, 99950, 99941]
    assert jumps == [(idx[2], 100000.0)]
    same, none = report.remove_cash_jumps(pd.Series([100000, 99000, 101000.0], index=idx[:3]))  # 1-2% moves are trading
    assert none == [] and list(same) == [100000, 99000, 101000]
