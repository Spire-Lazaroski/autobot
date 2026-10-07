"""Pairs study: the trading rule, the money per leg, and the cointegration selection."""
import numpy as np
import pytest

from autobot import pairs_study as ps


def test_trade_rule_enters_exits_and_closes_out():
    n = 120
    spread = np.zeros(n)
    rng = np.random.default_rng(0)
    spread[:100] = rng.normal(0, 0.001, 100)
    spread[100] = 0.004                                  # gap stretches: z between 2 and the stop at 4 -> short the spread
    spread[101:] = -0.0005                               # gap closes (z crosses 0) -> out
    minute = np.full(n, 600)
    day = np.zeros(n, int)
    pos = ps.trade(spread, minute, day, 2.0)
    assert pos[99] == 0 and pos[100] == -1 and pos[101] == 0
    minute[100] = ps.LAST_ENTRY_MIN                      # too late in the day for a new entry
    assert ps.trade(spread, minute, day, 2.0)[100] == 0


def test_pnl_per_dollar_of_both_legs_with_costs():
    pos = np.array([0, 1, 1, 0])
    ra, rb = np.array([0, 0, 0.01, 0.0]), np.array([0, 0, 0.005, 0.0])
    out = ps.pnl(pos, ra, rb, beta=1.0, side_cost=0.0002, minute=np.full(4, 600))
    assert out[1] == pytest.approx(-0.0002)              # opened at bar 1's close
    assert out[2] == pytest.approx((0.01 - 0.005) / 2)   # long A, short B, per dollar of both legs
    assert out[3] == pytest.approx(-0.0002)              # closed at bar 3's close


def test_johansen_finds_a_built_in_relation():
    pytest.importorskip("statsmodels")
    rng = np.random.default_rng(1)
    lb = np.cumsum(rng.normal(0, 0.001, 3000)) + 4
    la = 1.5 * lb + rng.normal(0, 0.0005, 3000)          # A = 1.5 x B + noise: cointegrated
    j = ps.johansen(la, lb)
    assert j["coint"] and j["beta"] == pytest.approx(1.5, abs=0.05)
    false = sum(ps.johansen(np.cumsum(rng.normal(0, 0.001, 3000)), np.cumsum(rng.normal(0, 0.001, 3000)))["coint"] for _ in range(40))
    assert false <= 6                                    # unrelated walks: about 1% false alarms expected at the 1% level
    assert ps.half_life(la - 1.5 * lb) < 3
