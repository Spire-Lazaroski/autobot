"""Scalping test: target fills only when traded through, stop counts first in a bar that reaches
both, re-entry after a target, close-out at the 15:50 bar."""
import numpy as np

from autobot import scalp_study as sc


def arrays(o, h, l, c, b, minute=None):
    n = len(c)
    m = np.array(minute if minute is not None else [570 + 5 * k for k in range(n)])
    return (np.array(o, float), np.array(h, float), np.array(l, float), np.array(c, float), np.array(b, float),
            m, np.array(["d"] * n), np.ones(n, bool))


def test_target_needs_a_cent_through_then_buys_again():
    a = arrays(o=[100, 100, 100, 100.1, 100.15], h=[100, 100.095, 100.12, 100.15, 100.2], l=[100] * 5, c=[100, 100, 100.1, 100.15, 100.15],
               b=[1, 1, 1, 1, 0])
    t = sc.simulate(*a, side_cost=0.0002, prem_frac=np.full(5, 0.0005), stop_k=None)
    # target = 100 x (1 + 0.0004 + 0.0005) = 100.09: bar 1's high 100.095 is not a cent through; bar 2's 100.12 is
    assert t[0][2] == "target" and abs(t[0][1] - (0.0009 - 0.0004)) < 1e-9
    assert t[1][2] == "signal"                     # bought again at bar 2's close 100.1, sold when the signal ends


def test_stop_counts_first_and_waits_for_a_fresh_signal():
    a = arrays(o=[100, 100, 100, 100], h=[100, 101, 101, 101], l=[100, 99, 99, 99], c=[100, 100, 100, 100], b=[1, 1, 1, 1])
    t = sc.simulate(*a, side_cost=0.0002, prem_frac=np.full(4, 0.0005), stop_k=1.0)
    assert len(t) == 1 and t[0][2] == "stop"       # no re-entry while the same signal is still on


def test_strategy_alone_holds_to_the_closeout():
    a = arrays(o=[100] * 3, h=[100] * 3, l=[100] * 3, c=[100, 101, 102], b=[1, 1, 1], minute=[940, 945, 950])
    t = sc.simulate(*a, side_cost=0.0002, prem_frac=None, stop_k=None)
    assert t[0][2] == "close" and abs(t[0][1] - (0.02 - 0.0002 - 0.0004)) < 1e-9
