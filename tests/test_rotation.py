"""Rotation sleeve: the live rule picks exactly what the 1927-2026 study picked, uses completed months
only, validates its settings, and trades only beyond the band."""
import numpy as np
import pandas as pd
import pytest

from autobot import rotation as rot
from autobot import rs_study as rs

CFG = {**rot.DEFAULTS, "enabled": True, "symbols": list("ABCDEFG"), "allocation_pct": 40, "top_n": 3}


def monthly(n=40, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.period_range("2015-01", periods=n, freq="M")
    R = pd.DataFrame(rng.normal(0.008, 0.05, (n, 7)), index=idx, columns=list("ABCDEFG"))
    R["A"] += 0.02
    R["G"] -= 0.02
    return R


def daily_from(R):
    px = (1 + R).cumprod() * 100
    out = {}
    for s in px:
        idx = pd.DatetimeIndex([p.to_timestamp(how="end").normalize() - pd.Timedelta(days=1) for p in px.index]).tz_localize(rot.NY)
        out[s] = pd.Series(px[s].to_numpy(), index=idx)
    return out


def test_same_picks_and_weights_as_the_study():
    R = monthly()
    daily = daily_from(R)
    W = rs.weights(R, "full", 3)
    for t in (20, 30, 38):
        today = str(R.index[t + 1].to_timestamp().date())                     # first day of the next month
        live = rot.targets(daily, CFG, today)
        study = (W.iloc[t] * 40).round(3)
        assert {k: v for k, v in study.items() if v > 0} == pytest.approx(live["weights"])
        assert live["as_of"] == str(R.index[t])


def test_only_completed_months_count():
    R = monthly()
    daily = daily_from(R)
    today = str(R.index[30].to_timestamp().date())
    a = rot.targets(daily, CFG, today)
    for s in daily:                                                           # a wild move later in "today's" month changes nothing
        daily[s].loc[pd.Timestamp(today, tz=rot.NY) + pd.Timedelta(days=3)] = 1e6
    assert rot.targets(daily, CFG, today)["weights"] == a["weights"]


def test_validation():
    assert rot.validate(CFG, job_symbols=["SPY"], core_symbol="VOO", core_max_pct=45) == []
    errs = " | ".join(rot.validate({**CFG, "symbols": ["SPY", "A"], "top_n": 0, "x": 1, "allocation_pct": 60},
                                   job_symbols=["SPY"], core_symbol="VOO", core_max_pct=45))
    assert "unknown settings x" in errs and "top_n" in errs and "also in an enabled job" in errs and "max_gross_pct" in errs


def test_orders_respect_the_band_and_sell_dropped_funds():
    tgt = {"weights": {"A": 8.0, "B": 6.0}, "reasons": {"A": "rank 1", "B": "rank 2"}}
    held = {"A": 79, "C": 50}                                                 # A at 7.9% (inside the band), C dropped
    prices = {"A": 100.0, "B": 50.0, "C": 20.0}
    o = {x["symbol"]: x for x in rot.orders(held, prices, 100_000, tgt, CFG)}
    assert "A" not in o and o["B"]["qty"] == 120 and o["C"]["qty"] == -50 and "no longer in the top 3" in o["C"]["reason"]
