"""Cost study (autobot.cost_study): resampling, round trips, clustered ranges, edge statistics,
strategy lists per timeframe, and the recalibration replay's causality on synthetic data."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from autobot import cost_study as cs
from autobot import overnight_study as ov
from autobot import recalibrate as rc
from autobot.backtest import CostModel

NY = "America/New_York"


def bars5(days):
    rows, idx, p = [], [], 100.0
    for d in days:
        for m in range(9 * 60 + 30, 16 * 60, 5):
            t = pd.Timestamp(d).tz_localize(NY) + pd.Timedelta(minutes=m)
            o = p
            p *= 1.0005
            rows.append((o, p * 1.001, o * 0.999, p, 100.0))
            idx.append(t.tz_convert("UTC"))
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex(idx))


DAYS = [date(2026, 9, 14), date(2026, 9, 15), date(2026, 3, 16)]  # an EDT week and an EST day


def test_resample_aligns_to_the_open():
    df = bars5(sorted(DAYS))
    for tf, first, last, n in (("15Min", "09:30", "15:45", 26), ("1Hour", "09:30", "15:30", 7)):
        out = cs.resample(df, tf)
        ny = out.index.tz_convert(NY)
        for d in DAYS:
            day = out[np.asarray(ny.date) == d]
            t = day.index.tz_convert(NY).strftime("%H:%M")
            assert (t[0], t[-1], len(day)) == (first, last, n), (tf, d)
            src = df[np.asarray(df.index.tz_convert(NY).date) == d]
            assert day.open.iloc[0] == src.open.iloc[0] and day.close.iloc[-1] == src.close.iloc[-1]
    daily = cs.resample(df, "1Day")
    assert len(daily) == 3 and (daily.index.tz_convert(NY).strftime("%H:%M") == "09:30").all()
    assert daily.volume.iloc[0] == 78 * 100.0


def test_round_trips_gross_and_costs():
    idx = pd.date_range("2026-09-14 13:30", periods=8, freq="5min", tz="UTC")
    f = pd.DataFrame({"position": [0, 1, 1, 1, 0, 0, 1, 0], "ret": [0, .01 - .0002, .02, -.005, -.0003, 0, .003 - .0002, -.0002],
                      "cost": [0, .0002, 0, 0, .0003, 0, .0002, .0002]}, index=idx)
    tr = cs.round_trips(f)
    assert list(tr.bars) == [3, 1]
    assert tr.gross.tolist() == pytest.approx([.025, .003])
    assert tr.cost.tolist() == pytest.approx([.0005, .0004])


def test_clustered_range_is_wider_when_trades_move_together():
    rng = np.random.default_rng(1)
    day_effect = np.repeat(rng.normal(0, 10, 50), 10)
    x = day_effect + rng.normal(0, 1, 500)
    together = cs.clustered_mean(x, np.repeat(np.arange(50), 10))
    alone = cs.clustered_mean(x, np.arange(500))
    assert (together["hi"] - together["lo"]) > 2 * (alone["hi"] - alone["lo"])
    assert together["mean"] == pytest.approx(x.mean())


def test_edge_stats_break_even():
    tr = pd.DataFrame({"start": range(4), "end": range(4), "bars": [2, 2, 4, 4], "day": [date(2026, 1, d) for d in (5, 6, 7, 8)],
                       "gross": [.0006, .0004, .0005, .0005], "cost": [.0004] * 4})
    s = cs.edge_stats(tr, sessions=8, step_min=5, side_bps=2.0)
    assert s["gross_bps"] == pytest.approx(5.0) and s["cost_bps"] == pytest.approx(4.0) and s["net_bps"] == pytest.approx(1.0)
    assert s["break_even_side_bps"] == pytest.approx(2.5)      # 2 bps per side costs 4 per round trip; 5 gross breaks even at 2.5
    assert s["per_day"] == 0.5 and s["hold_min"] == 15.0 and s["win_rate_%"] == 75.0   # a trade that nets exactly 0 is no win
    assert s["clearly_positive"]


def test_strategy_lists_per_timeframe():
    r = rc.settings({"live": {"timeframe": "5Min", "risk": {}, "jobs": []}})
    intraday, daily = cs.strategies_for("5Min", r), cs.strategies_for("1Day", r)
    assert "connors_rsi2" not in intraday and "connors_rsi2" in daily
    assert "opening_range_breakout" in intraday and "opening_range_breakout" not in daily
    assert not {"buy_and_hold", "regime_switch", "news_guard"} & (set(intraday) | set(daily))


def test_daily_bars_hold_overnight_and_fill_at_the_open():
    df = cs.resample(bars5([date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16)]), "1Day")
    cal = ov.calendar(bars5([date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16)]))
    out = cs.sim(df, cal, "x", {}, CostModel(0, 2), "1Day", 5, signal=pd.Series([1.0, 1.0, 1.0], index=df.index))
    f = out["frame"]
    assert f.position.tolist() == [0.0, 1.0, 1.0]                 # decided at day 1's close, bought at day 2's open
    assert f.cost.iloc[1] == pytest.approx(2 * 2 / 1e4)            # an open fill costs 2x
    assert f.overnight.iloc[2] != 0 or df.open.iloc[2] == df.close.iloc[1]


def test_replay_is_causal():
    """A pick made on a Friday must not change when later bars are added."""
    from autobot.data import synthetic_bars
    df = synthetic_bars(["AAA"], "15Min", 160, seed=9)["AAA"]
    r = {**rc.settings({"live": {"timeframe": "15Min", "risk": {"flatten_before_close_min": 5}, "jobs": []}}),
         "lookback_days": 40, "exclude": rc.DEFAULTS["exclude"] + ["noise_area", "macd", "sma_cross", "momentum", "intraday_momentum"]}
    days = sorted(set(df.index.tz_convert(NY).date))
    fridays = [d for d in days[60:] if pd.Timestamp(d).weekday() == 4][:3]
    full = cs.replay_symbol("AAA", df, r, fridays)
    cut_at = fridays[1] + timedelta(days=7)
    part_df = df[np.asarray(df.index.tz_convert(NY).date) <= cut_at]
    part = cs.replay_symbol("AAA", part_df, r, fridays[:2])
    assert [x["job"] for x in part] == [x["job"] for x in full[:2]]
    assert [x["challenger_score"] for x in part] == pytest.approx([x["challenger_score"] for x in full[:2]])
    assert [x["next_%"] for x in part] == pytest.approx([x["next_%"] for x in full[:2]])


def test_summary_fills_every_template():
    """Step 7 on a small made-up result: every line and option is filled in (no '{' left), and
    'nothing beat it beyond chance' is said when the hits are within luck."""
    rows = [{"symbol": "AAA", "friday": f"2026-01-{d:02d}", "decision": dec, "job_score": sc, "strategies": []}
            for d, dec, sc in ((2, "new_job", 1.0), (9, "keep_better", -0.5), (16, "keep_same", 0.2))]
    res = {
        "replay": {"rows": rows, "fridays": 3, "symbols": ["AAA"], "from": "2026-01-02",
                   "picks": {"mean_next_%": -0.2, "sum_next_%": -0.6, "symbol_weeks": 3, "mean_matched_bh_next_%": 0.1,
                             "when_trading": {"beat_matched_bh_%": 33.3}, "vs_matched_bh_mean_%": -0.3, "vs_matched_bh_ci95": [-0.5, -0.1]},
                   "score_predicts": {"within_symbol_week": {"mean_rank_corr": 0.02, "ci95": [-0.01, 0.05], "n": 3},
                                      "by_score_fifth": [{"mean_next_%": -0.1}, {"mean_next_%": -0.3}]}},
        "edges": {"by_timeframe": {tf: {"combos": 10, "clearly_positive": 0, "median_gross_bps": 1.0, "median_cost_bps": 4.0,
                                        "total_net_%_median": -10.0} for tf in ("5Min", "15Min", "1Hour", "1Day")},
                  "expected_false_positives": 1.0,
                  "jobs": [{"symbol": "SPY", "gross_bps": 2.0, "gross_lo": -3.0, "gross_hi": 7.0, "cost_bps": 4.8, "clearly_positive": False}]},
        "close_rule": [{"symbol": "SPY", "forced_%_of_cost": 42.0, "net_without_forced_costs_%": -3.0}],
        "cost_realism": {"fills": {"summary": {"vs_mid_bps": {"median": 0.1, "n": 10}}}, "spreads": {"TSLA": {"midday": {"half_spread_bps": 12.0}}}},
        "walk_forward": {"rows": [{"strategy": "recalibration_single", "significant": False, "vs_matched_bps": -1.0}],
                         "tests": 40, "beat_matched": [], "expected_false_positives": 1.0},
    }
    s = cs.summary(res)
    text = " ".join(s["lines"] + [o["option"] + o["evidence"] for o in s["options"]])
    assert "{" not in text and "does not usefully predict" in text and "nothing beat it beyond chance" in text
    assert "1 job-weeks were kept" in text and "0.05" in text


def test_replay_can_continue_from_an_earlier_period():
    """Two periods run back to back with the carried state give exactly one long run."""
    from autobot.data import synthetic_bars
    df = synthetic_bars(["AAA"], "15Min", 160, seed=9)["AAA"]
    r = {**rc.settings({"live": {"timeframe": "15Min", "risk": {"flatten_before_close_min": 5}, "jobs": []}}),
         "lookback_days": 40, "exclude": rc.DEFAULTS["exclude"] + ["noise_area", "macd", "sma_cross", "momentum", "intraday_momentum"]}
    days = sorted(set(df.index.tz_convert(NY).date))
    fridays = [d for d in days[60:] if pd.Timestamp(d).weekday() == 4][:4]
    whole = cs.replay_symbol("AAA", df, r, fridays)
    carry = {}
    split = cs.replay_symbol("AAA", df, r, fridays[:2], carry=carry) + cs.replay_symbol("AAA", df, r, fridays[2:], carry=carry)
    keys = ("job", "decision", "next_%", "job_score", "next_cost_%", "buy_hold_day_next_%")
    assert [{k: x[k] for k in keys} for x in split] == [{k: x[k] for k in keys} for x in whole]


def test_daytime_returns_skip_the_overnight_gap():
    df = bars5([date(2026, 9, 14), date(2026, 9, 15)])
    df.loc[df.index[78], "open"] = df.close.iloc[77] * 1.05      # a 5% gap at day 2's open
    d = cs.daytime_returns(df)
    assert d[78] == pytest.approx(df.close.iloc[78] / df.open.iloc[78] - 1)
    assert d[1] == pytest.approx(df.close.iloc[1] / df.close.iloc[0] - 1)
