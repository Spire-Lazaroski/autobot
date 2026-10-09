"""Report split: start positions from today's minus the period's fills, each part's result and its fair comparison."""
import pandas as pd
import pytest

from autobot import report_split as rs


def bars(o, c, n=3, start="2026-10-09 13:30"):
    idx = pd.date_range(start, periods=n, freq="5min", tz="UTC")
    import numpy as np
    closes = np.linspace(o, c, n)
    return pd.DataFrame({"open": [o] + list(closes[:-1]), "high": closes, "low": closes, "close": closes, "volume": 1.0}, index=idx)


def test_start_quantities():
    f = [{"symbol": "VOO", "side": "buy", "qty": 10}, {"symbol": "XLK", "side": "buy", "qty": 15}]
    assert rs.start_quantities({"VOO": 55, "XLK": 15}, f) == {"VOO": 45}


def test_parts_and_fair_comparisons():
    t = pd.Timestamp("2026-10-09 13:35", tz="UTC")
    fills = [{"symbol": "VOO", "side": "buy", "qty": 10, "price": 101.0, "time": t},
             {"symbol": "XLK", "side": "buy", "qty": 10, "price": 200.0, "time": t}]
    b = {"VOO": bars(100, 102), "XLK": bars(200, 198), "XLE": bars(50, 51), "SPY": bars(500, 505)}
    eq = pd.Series([100_000.0, 100_020.0, 100_050.0], index=b["VOO"].index)
    out = rs.build("VOO", ["XLK", "XLE"], {"VOO": 45}, fills, b, eq, [{"hold_usd": 12.0}], b["SPY"])
    core = next(p for p in out["parts"] if p["part"] == "core")
    assert core["pnl"] == pytest.approx(55 * 102 - 45 * 100 - 10 * 101)          # 100
    assert core["fair"] == pytest.approx(45 * 2)                                    # keeping the 45 shares
    rot = next(p for p in out["parts"] if p["part"] == "rotation")
    assert rot["pnl"] == pytest.approx(10 * 198 - 10 * 200)                        # -20
    assert rot["fair"] == pytest.approx(2000 * ((198 / 200 - 1) + (51 / 50 - 1)) / 2)
    jobs = next(p for p in out["parts"] if p["part"] == "jobs")
    assert jobs["pnl"] == pytest.approx(50 - 100 + 20) and jobs["fair"] == 12.0
    assert 0 < out["account"]["share"] < 1 and out["account"]["spy_matched"] > 0
