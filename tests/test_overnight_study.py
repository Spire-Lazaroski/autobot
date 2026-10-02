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
    ny = df.index.tz_convert(NY)
    gap = df[~((np.asarray(ny.date) == WEEK[0]) & np.isin(ny.strftime("%H:%M"), ["15:45", "15:50"]))]
    g = inputs(gap, 1.0)
    a, b = g.sessions[0]
    assert t(df.index.get_loc(gap.index[g.decision_bar(a, b, 5)])) == "15:40"   # window bars missing: the bar before
    assert ov.simulate(g, P("flatten", 5))["frame"].overnight.sum() == 0


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


def _gapped(sym, days=120, seed=2):
    from autobot.data import synthetic_bars
    df = synthetic_bars([sym], "5Min", days, seed=seed)[sym]
    rng = np.random.default_rng(seed)
    first = np.r_[True, np.asarray(df.index.tz_convert(NY).date)[1:] != np.asarray(df.index.tz_convert(NY).date)[:-1]]
    df.loc[first, "open"] = df.close.shift(1)[first] * (1 + rng.normal(0, 0.01, first.sum()))
    df.iloc[0, df.columns.get_loc("open")] = df.close.iloc[0]
    df.attrs["symbol"] = sym
    return df


def test_run_writes_a_complete_study(tmp_path):
    cfg = {"live": {"timeframe": "5Min", "risk": {"flatten_before_close_min": 5}, "jobs": [
        {"name": "a", "strategy": "ema_cross", "params": {}, "symbols": ["AAA"], "enabled": True},
        {"name": "b", "strategy": "donchian_breakout", "params": {}, "symbols": ["BBB"], "enabled": True},
        {"name": "c", "strategy": "ema_cross", "params": {}, "symbols": ["BTC/USD"], "enabled": True},
        {"name": "off", "strategy": "ema_cross", "params": {}, "symbols": ["CCC"], "enabled": False}]}}
    data = {"AAA": _gapped("AAA"), "BBB": _gapped("BBB", seed=3)}
    res = ov.run(cfg, 90, long=True, fetch=lambda s, tf, d: data[s], news=False)
    assert [s["symbol"] for s in res["symbols"]] == ["AAA", "BBB"]          # crypto and disabled jobs left out
    assert res["base_policy"] == "flatten_5" and res["calendar"] == "guessed from the bars"
    for key in ("90d", "long"):
        per = res["periods"][key]
        names = [p["name"] for p in per["policies"]]
        assert "hold" in names and "if_all" in names
        flat = next(p for p in per["policies"] if p["name"] == "flatten_5")["total"]
        assert flat["overnight_%"] == 0 and flat["weekend_%"] == 0 and flat["nights"] == 0
        hold = next(p for p in per["policies"] if p["name"] == "hold")["total"]
        assert hold["nights"] > 0
        wf = per["walk_forward"]
        assert wf and set(wf["vs_flatten"]) >= {"mean_bps", "ci95_bps", "significant"}
        assert sum(wf["choices"].values()) > 0
        assert any(r["group"] == "if_all" for r in per["worth_holding"])
        assert per["equity"]["hold"] and per["equity_test"]["walk_forward"]
    assert res["summary"][0].startswith("Out of sample")
    assert isinstance(res["beats_flatten_oos"], bool)
    path = ov.save(res, tmp_path / "ov.json")
    back = ov.load(path)
    assert back["periods"]["long"]["policies"][0]["name"] == res["periods"]["long"]["policies"][0]["name"]


def test_policy_text_reads_plainly():
    o = ov.DEFAULTS
    assert ov.policy_text({"mode": "flatten", "flatten_min": 15, "require": []}, o) == "sell everything 15 minutes before the close"
    t = ov.policy_text({"mode": "conditional", "flatten_min": 5, "require": ["signal_on", "in_profit", "no_earnings"]}, o)
    assert t == ("keep the position overnight only if the strategy still wants the position, the position is in profit "
                 "and no earnings release before the next open")
    assert "materiality 4+" in ov.policy_text({"mode": "conditional", "flatten_min": 5, "require": ["no_bad_news"]}, o)


def test_at_cost_changes_only_open_and_last_bar_fills():
    df = bars(WEEK, [0, 0.01, -0.02, 0.005, 0.01, 0.03])
    c = CostModel(0.0, 10.0)
    two = ov.simulate(inputs(df, 1.0, costs=c, edge=2.0), P("flatten", 5))
    one = ov.at_cost(two, 2.0, 1.0)
    direct = ov.simulate(inputs(df, 1.0, costs=c, edge=1.0), P("flatten", 5))
    np.testing.assert_allclose(one["frame"].ret.to_numpy(), direct["frame"].ret.to_numpy(), atol=1e-12)
    np.testing.assert_array_equal(one["frame"].position.to_numpy(), two["frame"].position.to_numpy())
    assert one["frame"].cost.sum() == pytest.approx(len(WEEK) * 2 * 10 / 1e4)


def test_blocking_shares_and_informative_flags():
    n = pd.DataFrame({c: [True] * 100 for c in ov.CONDITIONS + ["no_bad_news_m3"]})
    n.loc[:69, "in_profit"] = False          # blocks 70%: too often
    n.loc[:0, "no_earnings"] = False         # blocks 1%: too rarely
    n.loc[:29, "trend_regime"] = False       # blocks 30%: informative
    rows = {r["condition"]: r for r in ov._blocking(n)}
    assert rows["in_profit"]["blocks_%"] == 70 and not rows["in_profit"]["informative"]
    assert rows["no_earnings"]["blocks_%"] == 1 and not rows["no_earnings"]["informative"]
    assert rows["trend_regime"]["informative"]
    assert rows["signal_on"]["blocks_%"] == 0 and not rows["signal_on"]["informative"]


def test_switch_count():
    assert ov._switches(["a", "a", "b", "b", "a"]) == {"windows": 5, "changes": 2, "changes_%": 50.0}


def test_cost_report_counts_forced_exits():
    df = bars(WEEK, drift=[0.0005] * 6)
    inp = inputs(df, 1.0, costs=CostModel(0, 2), edge=2.0)
    pol = P("flatten", 5)
    run = type("R", (), {})()
    run.sym, run.job, run.inp = "AAA", {"name": "auto_AAA", "strategy": "ema_cross"}, inp
    run.sims = {"main": {pol.name: ov.simulate(inp, pol)}}
    c = ov.cost_report(run, pol, WEEK[0], WEEK[-1])
    assert c["sessions"] == 6 and c["round_trips"] == 6 and c["round_trips_per_day"] == 1.0
    # the strategy never wanted out; the last session has no next open in the data, so it isn't a night yet
    assert c["forced_exits"] == 5 and c["forced_exits_%"] == pytest.approx(83.3)
    assert c["avg_cost_bps"] == pytest.approx(3.0)                        # entries 1x, exits 2x: (2 + 4) / 2


def test_run_adds_benchmark_costs_and_sensitivity():
    cfg = {"live": {"timeframe": "5Min", "risk": {"flatten_before_close_min": 5}, "jobs": [
        {"name": "auto_AAA", "strategy": "ema_cross", "params": {}, "symbols": ["AAA"], "enabled": True}]}}
    data = {"AAA": _gapped("AAA")}
    res = ov.run(cfg, 90, long=False, fetch=lambda s, tf, d: data[s], news=False, recal_symbols=("AAA",))
    per = res["periods"]["90d"]
    assert set(per["buy_hold"]["per_symbol"]) == {"AAA"}
    p = next(x for x in per["policies"] if x["name"] == "flatten_5")["total"]
    assert p["return_before_costs_%"] >= p["return_%"] and p["round_trips"] > 0
    assert set(p["by_kind"]) == {"overnight", "weekend"}
    wf = per["walk_forward"]
    assert set(wf["compare"]) == {"picked", "flatten_5", "hold", "buy_hold"} and "changes_%" in wf["switches"]
    assert {r["condition"] for r in per["blocking"]["total"]} >= set(ov.CONDITIONS)
    cs = res["cost_sensitivity"]["90d"]
    assert cs["ranking"]["test_days_return"]["best_1x"] in res["policy_text"]
    c = res["costs"][0]
    assert c["job"] == "auto_AAA" and set(c["recalibration"]) == {"usual", "study"}
    assert c["recalibration"]["study"]["cost_bps"] == pytest.approx(c["avg_cost_bps"], abs=0.01)
    assert any(line.startswith("auto_AAA") for line in res["summary"])
