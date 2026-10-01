"""news_guard: stand-aside and earnings blackout are causal and do what they say."""
import numpy as np
import pandas as pd
import pytest

from autobot import news_guard as ng
from autobot.data import synthetic_bars
from autobot.strategies import get_signal


@pytest.fixture
def bars():
    df = synthetic_bars(["X"], "5Min", 20, seed=4)["X"]
    df.attrs["symbol"] = "XTEST"
    return df


def fake_news(monkeypatch, rows, earnings=()):
    df = pd.DataFrame(rows, columns=["t_info", "materiality", "headline"])
    df["t_info"] = pd.to_datetime(df.t_info, utc=True)
    df = df.assign(category="other", story_type="new_information", relevance="main").sort_values("t_info").reset_index(drop=True)
    monkeypatch.setattr(ng, "stories", lambda symbol: df)
    monkeypatch.setattr(ng, "earnings_sessions", lambda symbol: list(earnings))


def test_stand_aside_window_starts_at_the_story(bars, monkeypatch):
    t = bars.index[100] + pd.Timedelta(minutes=2)          # arrives inside bar 100
    fake_news(monkeypatch, [(t, 4, "Big news")])
    f = ng.guard_flags(bars, "XTEST", 30, 3, 0, 0)
    aside = f.stand_aside.to_numpy()
    assert not aside[99]                                   # bar 99 closed before the story
    assert aside[100]                                      # bar 100 closes after it: decision at its close sees it
    assert aside[100:106].all() and not aside[106]         # 30 minutes = 6 bars of 5 minutes
    assert "Big news" in f.note.iloc[100]


def test_small_stories_are_ignored(bars, monkeypatch):
    fake_news(monkeypatch, [(bars.index[100], 2, "Minor")])
    assert not ng.guard_flags(bars, "XTEST", 30, 3, 0, 0).stand_aside.any()


def test_later_news_never_changes_earlier_decisions(bars, monkeypatch):
    early = (bars.index[50], 4, "early")
    fake_news(monkeypatch, [early])
    a = ng.news_guard(bars, base="buy_and_hold", mode="flat", blackout_before=0, blackout_after=0)
    fake_news(monkeypatch, [early, (bars.index[300], 5, "later")])
    b = ng.news_guard(bars, base="buy_and_hold", mode="flat", blackout_before=0, blackout_after=0)
    pd.testing.assert_series_equal(a.iloc[:299], b.iloc[:299])  # bar 299 closes as the later story arrives
    assert (b.iloc[299:305] == 0).all() and b.iloc[305] == 1  # 30 min = the 6 decisions from bar 299's close


def test_modes():
    sig = pd.Series([0, 1, 1, 1, 0, 1, -1, -1, 0.0])
    blocked = np.array([False, False, True, True, True, True, True, False, False])
    assert ng.apply_guard(sig, blocked, "flat").tolist() == [0, 1, 0, 0, 0, 0, 0, -1, 0]
    # no_new: keeps the open long, lets it close, won't reopen or flip while blocked
    assert ng.apply_guard(sig, blocked, "no_new").tolist() == [0, 1, 1, 1, 0, 0, 0, -1, 0]


def test_earnings_blackout_sessions(bars, monkeypatch):
    days = sorted(set(pd.DatetimeIndex(bars.index).tz_convert("America/New_York").date))
    fake_news(monkeypatch, [], earnings=[days[5]])
    f = ng.guard_flags(bars, "XTEST", 0, 3, 1, 1)
    day = np.array(pd.DatetimeIndex(bars.index).tz_convert("America/New_York").date)
    blocked_days = sorted(set(day[f.blackout.to_numpy()]))
    assert blocked_days == [days[4], days[5]]  # the session before (release day's close) and the reacting session


def test_without_a_symbol_the_guard_is_the_base_strategy():
    df = synthetic_bars(["X"], "5Min", 10, seed=1)["X"]  # demo data carries no symbol: no real news applies
    pd.testing.assert_series_equal(ng.news_guard(df, base="ema_cross"), get_signal("ema_cross", df, {}), check_names=False)


def test_guard_cannot_wrap_itself(bars):
    with pytest.raises(ValueError):
        ng.news_guard(bars, base="news_guard")


def test_listed_earnings_dates_replace_the_detectors(tmp_path, monkeypatch):
    from datetime import date
    # detector: a false date, a date one day off, and one after the listed ones (3 stories each)
    t = ["2024-10-01 14:00", "2025-07-23 14:00", "2026-07-23 14:00"]
    rows = pd.DataFrame({"t_info": pd.to_datetime([x for x in t for _ in range(3)], utc=True).tz_localize(None).tz_localize("America/New_York"),
                         "category": "earnings", "relevance": "main", "story_type": "new_information"})
    monkeypatch.setattr(ng, "stories", lambda symbol: rows)
    (tmp_path / "config.yaml").write_text("news:\n  earnings_dates:\n    XTEST: ['2025-01-30', '2025-07-24']\n", encoding="utf-8")
    monkeypatch.setattr(ng.news, "ROOT", tmp_path)
    assert ng.earnings_sessions("XTEST") == [date(2025, 1, 30), date(2025, 7, 24), date(2026, 7, 23)]
    assert ng.earnings_sessions("OTHER") == [date(2024, 10, 1), date(2025, 7, 23), date(2026, 7, 23)]  # nothing listed
