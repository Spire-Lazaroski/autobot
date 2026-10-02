"""Overnight study engine (autobot.overnight_study): gap accounting, fills at the next open,
edge costs, each hold condition, Fridays, earnings, bad news, half days, walk-forward."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from autobot import overnight_study as ov
from autobot.backtest import CostModel, run_backtest
from autobot.recalibrate import flat_before_close

NY = "America/New_York"
FREE = CostModel(0.0, 0.0)


def bars(days, gaps=None, drift=None, half_days=()):
    """5-minute regular-hours bars. gaps[k]: the k-th session's opening gap; drift[k]: the
    per-bar move inside session k. Prices are exact so results can be checked by hand."""
    rows, idx, price = [], [], 100.0
    for k, d in enumerate(days):
        price *= 1 + (gaps[k] if gaps and k else 0.0)
        end = 13 * 60 if d in half_days else 16 * 60
        for m in range(9 * 60 + 30, end, 5):
            t = pd.Timestamp(d).tz_localize(NY) + pd.Timedelta(minutes=m)
            o = price
            price *= 1 + (drift[k] if drift else 0.0)
            rows.append((o, max(o, price), min(o, price), price, 1000.0))
            idx.append(t.tz_convert("UTC"))
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex(idx))


WEEK = [date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 21)]


def inputs(df, sig, costs=FREE, edge=1.0, **kw):
    return ov.Inputs(df=df, signal=pd.Series(sig, index=df.index, dtype=float), cal=ov.calendar(df),
                     costs=costs, edge_mult=edge, **kw)


def P(mode, n=5, req=()):
    return ov.Policy(f"{mode}_{n}_{'_'.join(req)}", mode, n, tuple(req))


def test_hold_earns_every_gap_and_splits_weekend():
    gaps = [0, 0.01, -0.02, 0.005, 0.01, 0.03]
    df = bars(WEEK, gaps)
    res = ov.simulate(inputs(df, 1.0), P("hold"))
    f = res["frame"]
    assert f.equity.iloc[-1] == pytest.approx(df.close.iloc[-1] / df.close.iloc[0])
    assert f.overnight.sum() == pytest.approx(0.01 - 0.02 + 0.005 + 0.01)
    assert f.weekend.sum() == pytest.approx(0.03)  # Friday -> Monday
    nights = res["nights"]
    assert list(nights.kind) == ["overnight"] * 4 + ["weekend"]
    assert nights["result_%"].tolist() == pytest.approx([1.0, -2.0, 0.5, 1.0, 3.0])


def test_flatten_holds_nothing_and_pays_edge_cost_on_the_last_bar():
    df = bars(WEEK, [0, 0.01, -0.02, 0.005, 0.01, 0.03])
    c = CostModel(0.0, 10.0)
    inp = inputs(df, 1.0, costs=c, edge=2.0)
    f = ov.simulate(inp, P("flatten", 5))["frame"]
    assert f.overnight.sum() == 0 and f.weekend.sum() == 0
    assert (f.position.iloc[[b - 1 for _, b in inp.sessions]] == 0).all()
    # each session: entry at the first bar's close (1x) and exit filled at 15:55 (last bar, 2x)
    assert f.cost.sum() == pytest.approx(len(WEEK) * 3 * 10 / 1e4)


def test_flatten_matches_run_backtest_with_normal_costs():
    rng = np.random.default_rng(1)
    df = bars(WEEK, [0, 0.01, -0.02, 0.005, 0.01, 0.03], drift=[0.001, -0.0005, 0.0007, 0, -0.001, 0.0004])
    sig = pd.Series(rng.choice([-1.0, 0.0, 1.0], len(df)), index=df.index)
    c = CostModel(0.0, 5.0)
    for n in (5, 15, 30):
        res = ov.simulate(inputs(df, sig, costs=c, edge=1.0), P("flatten", n))
        bt = run_backtest(df, flat_before_close(sig, df, n), c)
        np.testing.assert_allclose(res["frame"].ret.to_numpy(), bt.ret.to_numpy(), atol=1e-12)
        np.testing.assert_allclose(res["frame"].position.to_numpy(), bt.position.to_numpy())


def test_a_last_bar_signal_is_filled_at_the_next_open():
    df = bars(WEEK[:2], [0, 0.05])
    sig = np.zeros(len(df))
    sig[77] = 1.0                       # day 1's last bar (15:55) turns the signal on
    sig[78:] = 1.0
    res = ov.simulate(inputs(df, sig, costs=CostModel(0, 10), edge=2.0), P("hold"))
    f = res["frame"]
    assert f.overnight.sum() == 0       # nothing was held through the night: the gap is missed
    assert f.position.iloc[78] == 1.0   # bought at the open
    assert f.cost.iloc[78] == pytest.approx(2 * 10 / 1e4)


def test_in_profit_condition():
    # day 1 rises into the decision, day 2 falls; position entered at day 1 / day 2 first bar close
    df = bars(WEEK[:3], [0, 0.0, 0.0], drift=[0.001, -0.001, 0.0])
    res = ov.simulate(inputs(df, 1.0), P("conditional", 5, ["in_profit"]))
    n = res["nights"]
    assert n.kept.tolist() == [True, False]
    assert n.pos_night.tolist() == [1.0, 0.0]
    assert n.loc[0, "_pnl_%"] > 0
    # the carried position keeps its entry price: day 2's P&L counts from day 1's entry
    df2 = bars(WEEK[:3], [0, 0.0, 0.0], drift=[0.002, -0.0005, 0.0])
    n2 = ov.simulate(inputs(df2, 1.0), P("conditional", 5, ["in_profit"]))["nights"]
    assert n2.kept.tolist() == [True, True]


def test_profit_above_x():
    df = bars(WEEK[:3], drift=[0.0001, 0.0003, 0.0])  # ~ +0.8% and ~ +2.3% since the first entry
    n = ov.simulate(inputs(df, 1.0, profit_x_pct=1.0), P("conditional", 5, ["in_profit_x"]))["nights"]
    assert n.kept.tolist() == [False, True]


def test_signal_on_and_trend_regime():
    df = bars(WEEK[:3])
    sig = np.ones(len(df))
    sig[76] = 0.0                       # off on day 1's decision bar (15:50)
    reg = np.array(["range"] * len(df), dtype=object)
    reg[78 + 76] = "trend"
    inp = inputs(df, sig, regime=reg)
    assert ov.simulate(inp, P("conditional", 5, ["signal_on"]))["nights"].kept.tolist() == [False, True]
    assert ov.simulate(inp, P("conditional", 5, ["trend_regime"]))["nights"].kept.tolist() == [False, True]


def test_friday_night_is_blocked_and_counts_as_weekend():
    df = bars(WEEK, [0, 0, 0, 0, 0, 0.02])
    res = ov.simulate(inputs(df, 1.0), P("conditional", 5, ["not_friday"]))
    n = res["nights"]
    assert n.kept.tolist() == [True, True, True, True, False]
    assert res["frame"].weekend.sum() == 0


def test_earnings_next_session_blocks_the_night():
    df = bars(WEEK[:4])
    inp = inputs(df, 1.0, earnings=frozenset({WEEK[2]}))   # Wednesday reacts to a release
    n = ov.simulate(inp, P("conditional", 5, ["no_earnings"]))["nights"]
    assert n.kept.tolist() == [True, False, True]          # Tuesday night is sold


def test_bad_news_window():
    df = bars(WEEK[:3])
    t1 = pd.Timestamp("2026-09-14 15:00", tz=NY).tz_convert("UTC").value   # before day 1's decision
    t2 = pd.Timestamp("2026-09-15 15:56", tz=NY).tz_convert("UTC").value   # after day 2's decision
    inp = inputs(df, 1.0, bad_news_t=np.array([t1, t2], dtype="int64"), bad_news_hours=24)
    n = ov.simulate(inp, P("conditional", 5, ["no_bad_news"]))["nights"]
    assert n.kept.tolist() == [False, True]


def test_half_day_closes_at_one():
    days = [date(2026, 11, 25), date(2026, 11, 27), date(2026, 11, 30)]
    df = bars(days, half_days={date(2026, 11, 27)})
    cal = ov.calendar(df)
    assert cal.loc[date(2026, 11, 27), "close"] == pd.Timestamp("2026-11-27 13:00", tz=NY)
    res = ov.simulate(inputs(df, 1.0), P("flatten", 5))
    f = res["frame"]
    day = np.asarray(df.index.tz_convert(NY).date)
    assert f.position[day == date(2026, 11, 27)].iloc[-1] == 0


def test_policies_cover_every_condition():
    ps = ov.policies(5)
    names = [p.name for p in ps]
    assert {"flatten_5", "flatten_15", "flatten_30", "hold", "if_all"} <= set(names)
    for c in ov.CONDITIONS:
        assert f"if_{c}" in names
    assert len(names) == len(set(names))


def test_walk_forward_uses_train_choice_on_test_sessions():
    df = bars(WEEK, [0, 0.01, 0.01, 0.01, 0.01, 0.01])
    inp = inputs(df, 1.0)
    pols = [P("flatten", 5), P("hold")]
    frames = {p.name: ov.simulate(inp, p)["frame"] for p in pols}
    starts = [a for a, _ in inp.sessions]
    win = [(starts[0], starts[3], starts[3], len(df))]
    wf = ov.walk_forward(inp, pols, win, frames, lambda f, a, b: f.ret.iloc[a:b].sum(), pols[0])
    assert wf["choices"] == ["hold_5_"]
    f = wf["result"]["frame"]
    assert f.overnight.iloc[starts[3]:].sum() == pytest.approx(0.01)   # Thursday night held
    assert f.weekend.iloc[starts[3]:].sum() == pytest.approx(0.01)     # and Friday's (a weekend)
    assert f.overnight.iloc[:starts[3]].sum() == 0                     # before: the fallback (flatten)


def test_decision_bar_is_the_last_one_that_can_still_trade():
    df = bars(WEEK[:2])
    inp = inputs(df, 1.0)
    a, b = inp.sessions[0]
    t = lambda i: df.index[i].tz_convert(NY).strftime("%H:%M")
    assert t(inp.decision_bar(a, b, 5)) == "15:50"     # the bar ending 15:55
    assert t(inp.decision_bar(a, b, 30)) == "15:25"
    h = df.resample("15min").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    hi = inputs(h, 1.0)
    a, b = hi.sessions[0]
    assert h.index[hi.decision_bar(a, b, 5)].tz_convert(NY).strftime("%H:%M") == "15:30"   # ends 15:45
    assert hi.decision_bar(a, a + 3, 5) is None        # session data ends early: nothing decided


def test_half_day_after_hours_prints_are_trimmed():
    days = [date(2026, 11, 25), date(2026, 11, 27), date(2026, 11, 30)]
    df = bars(days)
    ny = df.index.tz_convert(NY)
    half = (np.asarray(ny.date) == date(2026, 11, 27)) & (ny.hour * 60 + ny.minute >= 13 * 60 + 15)
    df = df[~half]                                       # the half day has prints up to 13:15
    cal = ov.calendar(df)
    assert cal.loc[date(2026, 11, 27), "close"] == pd.Timestamp("2026-11-27 13:00", tz=NY)
    t = ov.trim_to_calendar(df, cal)
    assert len(t) == len(df) - 3                         # 13:00, 13:05 and 13:10 dropped
    inp = ov.Inputs(df=t, signal=pd.Series(1.0, index=t.index), cal=cal, costs=FREE)
    f = ov.simulate(inp, P("flatten", 5))["frame"]
    assert f.overnight.sum() == 0 and f.weekend.sum() == 0
    closes = {date(2026, 11, 27): pd.Timestamp("2026-11-27 13:00")}   # from the exchange calendar
    assert ov.calendar(bars(days), closes).loc[date(2026, 11, 27), "close"] == pd.Timestamp("2026-11-27 13:00", tz=NY)
