"""news_guard_study helpers: earnings-date sources, the detector check and the paired daily difference."""
from datetime import date

import pandas as pd

from autobot import news_guard as ng
from autobot import news_guard_study as st


def test_study_earnings_uses_published_then_detector(monkeypatch):
    monkeypatch.setitem(st.PUBLISHED_EARNINGS, "XTEST", ["2025-01-30"])
    got = st.study_earnings("XTEST", [date(2025, 1, 29), date(2026, 7, 23)])
    assert got == [date(2025, 1, 30), date(2026, 7, 23)]  # detector dates only after the published list


def test_earnings_source_patches_and_restores(monkeypatch):
    monkeypatch.setattr(ng, "detected_sessions", lambda s: [date(2025, 1, 29)])
    monkeypatch.setattr(ng, "earnings_sessions", lambda s: [date(2020, 1, 1)])  # e.g. config-listed dates
    monkeypatch.setitem(st.PUBLISHED_EARNINGS, "XTEST", ["2025-01-30"])
    original = ng.earnings_sessions
    with st.earnings_source("published"):
        assert ng.earnings_sessions("XTEST") == [date(2025, 1, 30)]
    assert ng.earnings_sessions is original
    with st.earnings_source("detected"):
        assert ng.earnings_sessions("XTEST") == [date(2025, 1, 29)]


def test_detector_check_classifies(monkeypatch):
    monkeypatch.setitem(st.PUBLISHED_EARNINGS, "XTEST", ["2025-01-30", "2025-04-23", "2025-07-24"])
    detected = [date(2024, 12, 1), date(2025, 1, 30), date(2025, 7, 23), date(2025, 9, 1)]
    monkeypatch.setattr(ng, "detected_sessions", lambda s: detected)
    c = st.detector_check(["XTEST"])["XTEST"]
    assert c["exact"] == 1
    assert c["off_by_days"] == ["2025-07-24"]
    assert c["missed"] == ["2025-04-23"]
    assert c["false"] == ["2025-09-01"]  # 2024-12-01 is before the news window (more than 30 days earlier)


def test_paired_diff():
    idx = pd.date_range("2025-01-01", periods=50)
    base = pd.Series(0.0, index=idx)
    same = st.paired_diff(base, base)
    assert same["mean_bps"] == 0 and not same["significant"] and same["days_different"] == 0
    better = st.paired_diff(base + 0.001 + pd.Series(range(50), index=idx) * 1e-6, base)
    assert better["significant"] and better["ci95_bps"][0] > 0
