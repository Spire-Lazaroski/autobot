"""Daily report: the "Strategy changes" and "Trading list (universe)" sections."""
import json

import pandas as pd

from autobot import report, report_recal as rr
from autobot.data import synthetic_bars

A = pd.Timestamp("2026-10-02 04:00", tz="UTC")   # Fri 2 Oct, New York midnight
B = pd.Timestamp("2026-10-03 04:00", tz="UTC")


def setup(tmp_path, applied_at="2026-10-01T20:16:00+00:00", pending=None):
    out, uni = tmp_path / "recal", tmp_path / "uni"
    out.mkdir(), uni.mkdir()
    prop = {"date": "2026-10-01", "symbols": {"TSLA": {
        "incumbent": {"label": "no job"}, "proposed": {"label": "switch by market mood: trending → buy the dip"},
        "decision": {"outcome": "new_job", "change": True, "reason": "No job yet. The best option scored 3.64."}}}}
    (out / "2026-10-01.json").write_text(json.dumps(prop))
    (out / "decisions.json").write_text(json.dumps({"2026-10-01": {"status": "applied", "history": [
        {"at": applied_at, "by": "scheduler (apply mode)", "applied": ["TSLA"], "pending": []}]}}))
    if pending:
        (out / "pending.json").write_text(json.dumps(pending))
    (uni / "2026-09-30.json").write_text(json.dumps({"date": "2026-09-30", "selected": ["TSLA", "SPY"],
                                                      "added": ["TSLA"], "dropped": ["AMZN"], "warnings": []}))
    return out, uni


def test_changes_applied_the_night_before_are_shown_with_their_reason(tmp_path):
    out, uni = setup(tmp_path)
    rd = rr.collect(A, B, out, uni, mode="apply")
    lines = rr.change_lines(rd)
    assert len(rd["changes"]) == 1
    assert lines[0].startswith("TSLA: no job → switch by market mood") and "scored 3.64" in lines[0]
    assert "Applied Thu 01 Oct 16:16 (automatically)" in lines[0]
    u = rr.universe_lines(rd)
    assert u[0] == "Trading list of 2026-09-30: 2 symbols (TSLA, SPY)." and u[1] == "Added: TSLA. Dropped: AMZN."


def test_older_changes_are_not_repeated_and_propose_mode_is_explained(tmp_path):
    out, uni = setup(tmp_path, applied_at="2026-09-29T20:16:00+00:00",
                     pending={"date": "2026-10-01", "symbols": ["BTC/USD"]})
    lines = rr.change_lines(rr.collect(A, B, out, uni, mode="propose"))
    assert lines[0] == "No strategy changes took effect in this period."
    assert "Waiting to apply: BTC/USD" in lines[1] and "press Apply" in lines[2]


def test_sections_appear_in_live_reports_only(tmp_path):
    out, uni = setup(tmp_path)
    bars = synthetic_bars(["X"], "15Min", 5, seed=1)
    ri = report.from_backtest(bars, "ema_cross", is_demo=True)
    ri.recal = rr.collect(A, B, out, uni, mode="apply")
    assert "Strategy changes" not in report.render(ri)          # backtests: no live changes
    ri.kind = "live"
    html = report.render(ri)
    assert "<h2>Strategy changes</h2>" in html and "Trading list (universe)" in html and "Recalibration" in html
