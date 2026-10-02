"""Recalibration switch study (autobot.recal_fix_study): the period split, the weekly account
figures, the three pass criteria and the 'today matches the earlier replay' check."""
import json
from datetime import date, timedelta

import pytest

from autobot import recal_fix_study as rf
from autobot import recalibrate as rc

NOJOB = rc.describe(None)


def row(sym, fri, nxt=0.0, job="ema", score=1.0, bh=1.0, expo=0.5, decision="keep_same", changed=False):
    return {"symbol": sym, "friday": fri, "decision": decision, "changed": changed, "job": job, "job_score": score,
            "next_%": nxt if job != NOJOB else 0.0, "next_exposure": expo if job != NOJOB else 0.0, "next_trades": 2 if job != NOJOB else 0,
            "next_cost_%": 0.04 if job != NOJOB else 0.0, "buy_hold_next_%": bh, "matched_bh_next_%": bh * (expo if job != NOJOB else 0.0),
            "buy_hold_day_next_%": bh / 2, "matched_bh_day_next_%": bh / 2 * (expo if job != NOJOB else 0.0),
            "challenger": job, "challenger_score": score, "challenger_next_%": nxt, "strategies": []}


FR = [str(date(2026, 1, 2) + timedelta(days=7 * k)) for k in range(8)]


def test_period_split_keeps_every_design_outcome_before_the_split():
    fr = [rf.SPLIT - timedelta(days=d) for d in (21, 14, 10, 7)] + [rf.SPLIT + timedelta(days=4)]
    design, confirm = rf.period_fridays(fr, "design"), rf.period_fridays(fr, "confirm")
    assert design + confirm == fr and all(f + timedelta(days=7) < rf.SPLIT for f in design)
    assert all(f + timedelta(days=7) >= rf.SPLIT for f in confirm)


def test_weekly_account_and_references():
    rows = [row("AAA", FR[0], nxt=1.0, bh=2.0, expo=0.5), row("BBB", FR[0], nxt=-1.0, bh=4.0, job=NOJOB)]
    s = rf.summarize(rows, alloc=0.1, months=0.25)
    assert s["account"]["picks"]["total_%"] == pytest.approx(0.1)           # 10% of the account in AAA, +1%
    assert s["account"]["matched"]["total_%"] == pytest.approx(0.1)         # 0.1 x 2% x 0.5 time in the market
    assert s["account"]["buy_hold"]["total_%"] == pytest.approx(0.6)        # 0.1 x (2% + 4%)
    assert s["with_job"] == 1 and s["no_job"] == 1 and s["round_trips"] == 2


def test_criterion_a_and_b():
    worse = [row("AAA", f, nxt=-1.0 - 0.1 * k, bh=1.0) for k, f in enumerate(FR)]           # always below matched
    s = rf.summarize(worse, 0.1, 2)
    assert s["vs"]["matched"]["clearly_worse"] and rf.criteria(s)["a"] is False
    cash = [row("AAA", f, job=NOJOB, score=0.0, nxt=0.0) for f in FR]                          # no job at all
    s2 = rf.summarize(cash, 0.1, 2)
    assert rf.criteria(s2) == {"a": True, "b": True}                                           # passes (a) by sitting in cash
    neg = [row("AAA", f, score=-0.5 if k in (2, 3, 4) else 0.5) for k, f in enumerate(FR)]    # 3 weeks negative in a row
    assert rf.summarize(neg, 0.1, 2)["negative_score_jobs"]["longest_run_weeks"] == 3
    assert rf.criteria(rf.summarize(neg, 0.1, 2))["b"] is False
    two = [row("AAA", f, score=-0.5 if k in (2, 3) else 0.5) for k, f in enumerate(FR)]
    assert rf.criteria(rf.summarize(two, 0.1, 2))["b"] is True


def test_matches_today(tmp_path):
    ref = [row("AAA", f, nxt=0.1 * k) for k, f in enumerate(FR)]
    p = tmp_path / "cost.json"
    p.write_text(json.dumps({"replay": {"rows": ref}}))
    assert rf.matches_today(ref, p)["equal"]
    changed = [dict(x) for x in ref]
    changed[3]["next_%"] += 1e-6
    out = rf.matches_today(changed, p)
    assert not out["equal"] and out["different"] == 1


def test_versions_and_costs():
    base = rc.settings({"live": {"timeframe": "5Min", "risk": {}, "jobs": []}})
    assert rf.VERSIONS["today"] == {} and rc.validate({**base, **rf.VERSIONS["options12_edge"]}) == []
    r = rf._r_for(base, "options12", "SPY", "etf1bp")
    assert r["allow_drop"] and r["benchmark"] == "matched_buy_hold" and rc.costs_for("SPY", r).slippage_bps == 1.0
    assert rc.costs_for("GLD", rf._r_for(base, "today", "GLD", "etf1bp")).slippage_bps == 2.0   # only SPY and QQQ
    assert base["costs"]["etf"]["slippage_bps"] == 2.0                                           # the settings are not changed
