"""Universe screener: measures, ranking, the floor, hysteresis and the proposal file."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from autobot import universe as uv


def bars(days=25, vol=1000.0, rng=0.01, price=100.0, crypto=False, seed=0):
    """5-minute bars over `days` sessions; high-low = rng x price; volume per bar = vol."""
    r = np.random.default_rng(seed)
    idx = []
    for d in pd.bdate_range("2026-08-03", periods=days):
        start = pd.Timestamp(d.date(), tz="America/New_York") + (pd.Timedelta(0) if crypto else pd.Timedelta(hours=9, minutes=30))
        idx += list(pd.date_range(start, periods=288 if crypto else 78, freq="5min"))
    n = len(idx)
    close = price * (1 + r.normal(0, 1e-4, n)).cumprod()
    return pd.DataFrame({"open": close, "high": close * (1 + rng / 2), "low": close * (1 - rng / 2), "close": close,
                         "volume": np.full(n, vol)}, index=pd.DatetimeIndex(idx).tz_convert("UTC"))


def test_measures_dollar_volume_and_range():
    m = uv.measures(bars(days=25, vol=1000, rng=0.01, price=100), 20)
    assert m["days"] == 20
    assert m["dollar_volume"] == pytest.approx(78 * 1000 * 100, rel=0.01)  # 78 bars x 1000 shares x ~$100
    assert m["range_pct"] == pytest.approx(1.0, rel=0.01)


U = {"lookback_days": 20, "hysteresis": 2, "weights": {"dollar_volume": 0.5, "range_pct": 0.5}, "min_dollar_volume": 0}


def meas(dv, rg, days=20):
    return {"days": days, "dollar_volume": dv, "range_pct": rg}


def test_ranking_floor_and_missing_history():
    spec = {"min": 2, "max": 2, "candidates": ["A", "B", "C", "D", "E"], "min_dollar_volume": 50}
    m = {"A": meas(300, 3), "B": meas(200, 2), "C": meas(100, 1), "D": meas(10, 9), "E": meas(999, 9, days=3)}
    b = uv.rank_bucket("x", spec, m, [], U)
    assert b["selected"] == ["A", "B"]
    rows = {r["symbol"]: r for r in b["rows"]}
    assert rows["C"]["outcome"] == "not_selected" and rows["C"]["rank"] == 3
    assert rows["D"]["outcome"] == "floor" and "IEX" in rows["D"]["reason"]
    assert rows["E"]["outcome"] == "no_data"
    assert b["warning"] is None


def test_hysteresis_keeps_a_held_symbol_until_rank_n_plus_2():
    spec = {"min": 2, "max": 2, "candidates": list("ABCDE")}
    m = {s: meas(v, v) for s, v in zip("ABCDE", [5, 4, 3, 2, 1])}  # ranks A=1 .. E=5
    b = uv.rank_bucket("x", spec, m, ["D"], U)                       # D held at rank 4 = N + 2: kept
    assert b["selected"] == ["A", "D"]
    assert {r["symbol"]: r["outcome"] for r in b["rows"]}["D"] == "kept_hysteresis"
    b = uv.rank_bucket("x", spec, m, ["E"], U)                       # E held at rank 5 > N + 2: dropped
    assert b["selected"] == ["A", "B"]
    assert {r["symbol"]: r["outcome"] for r in b["rows"]}["E"] == "dropped"


def test_bucket_short_warning():
    spec = {"min": 3, "max": 3, "candidates": ["A", "B"]}
    b = uv.rank_bucket("x", spec, {"A": meas(1, 1), "B": meas(2, 2)}, [], U)
    assert "only 2 symbol(s) qualified, fewer than the minimum of 3" in b["warning"]


def test_run_writes_a_proposal_and_uses_the_last_one_as_held(tmp_path):
    cfg = {"live": {"timeframe": "5Min"}, "universe": {
        "every_days": 7, "total": {"min": 3, "max": 4},
        "buckets": {"stocks": {"min": 2, "max": 2, "candidates": ["AAA", "BBB", "CCC"]},
                    "crypto": {"min": 1, "max": 1, "candidates": ["BTC/USD"], "min_dollar_volume": 0}}}}
    data = {"AAA": bars(vol=3000, rng=0.03), "BBB": bars(vol=2000, rng=0.02), "CCC": bars(vol=1000, rng=0.01),
            "BTC/USD": bars(vol=1, rng=0.01, crypto=True)}
    fetch = lambda syms, tf, days: {s: data[s] for s in syms}  # noqa: E731
    r1 = uv.run(date(2026, 9, 1), cfg, fetch, out_dir=tmp_path)
    assert r1["selected"] == ["AAA", "BBB", "BTC/USD"] and r1["previous"] == []
    assert (tmp_path / "2026-09-01.json").exists()
    assert not uv.due(date(2026, 9, 5), uv.settings(cfg), tmp_path) and uv.due(date(2026, 9, 8), uv.settings(cfg), tmp_path)
    data["BBB"], data["CCC"] = bars(vol=1000, rng=0.01), bars(vol=2000, rng=0.02)   # BBB falls to rank 3 = N + 1
    r2 = uv.run(date(2026, 9, 8), cfg, fetch, out_dir=tmp_path)
    assert r2["previous"] == ["AAA", "BBB", "BTC/USD"]
    assert r2["selected"] == ["AAA", "BBB", "BTC/USD"] and r2["added"] == [] and r2["dropped"] == []  # hysteresis


def test_a_failing_symbol_does_not_hide_the_others(tmp_path):
    cfg = {"live": {"timeframe": "5Min"}, "universe": {"total": {}, "buckets": {
        "s": {"min": 1, "max": 2, "candidates": ["OK", "BAD"]}}}}

    def fetch(syms, tf, days):
        if "BAD" in syms:
            raise RuntimeError("no such symbol")
        return {"OK": bars()}
    r = uv.run(date(2026, 9, 1), cfg, fetch, out_dir=tmp_path)
    assert r["selected"] == ["OK"] and "BAD" in r["errors"]
