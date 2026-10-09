"""Live page data: today's equity rows only, and bot log tracebacks folded into one entry."""
import json

from api import live as al


def test_equity_today_keeps_the_latest_day(tmp_path, monkeypatch):
    monkeypatch.setattr(al, "LOGS", tmp_path)
    rows = [{"t": "2026-10-08T15:00:00+00:00", "equity": 1}, {"t": "2026-10-09T14:00:00+00:00", "equity": 2},
            {"t": "2026-10-09T14:05:00+00:00", "equity": 3}]
    (tmp_path / "equity.jsonl").write_text("\n".join(json.dumps(r) for r in [{"t": "x"}] + rows) + "\n")
    out = al._equity_today()
    assert [r["equity"] for r in out] == [2, 3] and isinstance(out[0]["t"], int)


def test_log_entries_fold_tracebacks():
    lines = ["2026-10-09 15:49:00,000 INFO tick", "2026-10-09 15:50:11,830 ERROR step failed: timed out",
             "Traceback (most recent call last):", '  File "live.py", line 1', "requests.exceptions.ReadTimeout: timed out",
             "2026-10-09 15:55:00,000 INFO tick"]
    e = al._log_entries(lines)
    assert len(e) == 3 and len(e[1]["details"]) == 3 and e[1]["summary"].startswith("requests.exceptions.ReadTimeout")
