"""Sizing: limits are never exceeded, calmer symbols get more dollars, the live bot and the
portfolio backtester size the same way, and fixed mode is unchanged."""
from datetime import date

import numpy as np
import pandas as pd
import pytest
import yaml

import live
from autobot import sizing
from autobot.backtest import CostModel, portfolio_backtest, run_backtest
from autobot.data import synthetic_bars
from autobot.strategies import get_signal
from tests.test_live import FakeTC, env  # noqa: F401

RB = {"max_daily_loss_pct": 2.0, "max_position_usd": 20000, "sizing": "risk_balanced", "gross_budget_pct": 90,
      "bucket_max_pct": {"crypto": 20}, "atr_days": 20}


@pytest.mark.parametrize("seed", range(25))
def test_budgets_never_exceed_any_limit(seed):
    rng = np.random.default_rng(seed)
    syms = [f"S{i}" for i in range(rng.integers(1, 15))] + ["BTC/USD", "ETH/USD"]
    atr = {s: float(rng.uniform(0.2, 8)) for s in syms}
    buckets = {"BTC/USD": "crypto", "ETH/USD": "crypto"}
    eq = float(rng.uniform(1e4, 1e6))
    risk = {**RB, "max_position_usd": float(rng.uniform(1e3, 5e4)), "gross_budget_pct": float(rng.uniform(10, 100))}
    b = sizing.budgets(eq, syms, risk, atr, {}, buckets)
    gross = eq * risk["gross_budget_pct"] / 100
    assert sum(b.values()) <= gross + 1e-6
    assert all(v <= risk["max_position_usd"] + 1e-6 for v in b.values())
    assert b["BTC/USD"] + b["ETH/USD"] <= gross * 0.20 + 1e-6


def test_calmer_symbols_get_more_dollars_and_missing_atr_gets_none():
    b = sizing.budgets(100_000, ["CALM", "WILD", "NEW"], {**RB, "max_position_usd": 1e9}, {"CALM": 1.0, "WILD": 3.0, "NEW": None}, {}, {})
    assert b["CALM"] == pytest.approx(3 * b["WILD"]) and b["NEW"] == 0
    assert b["CALM"] + b["WILD"] == pytest.approx(90_000)


def test_fixed_mode_is_todays_rule():
    risk = {"max_daily_loss_pct": 2, "max_position_usd": 10000}
    b = sizing.budgets(100_000, ["SPY", "AAPL"], risk, {}, {"SPY": 20, "AAPL": 5}, {})
    assert b == {"SPY": 10000, "AAPL": 5000}


def test_jobs_together_never_exceed_the_account():
    risk = {"max_daily_loss_pct": 2, "max_position_usd": 10000}
    syms = [f"S{i}" for i in range(20)]
    b = sizing.budgets(100_000, syms, risk, {}, {s: 10 for s in syms}, {})        # 20 jobs x 10% = 200%
    assert sum(b.values()) == pytest.approx(100_000) and all(v == pytest.approx(5000) for v in b.values())
    b = sizing.budgets(100_000, syms, {**risk, "max_gross_pct": 60}, {}, {s: 10 for s in syms}, {})
    assert sum(b.values()) == pytest.approx(60_000)
    five = sizing.budgets(100_000, syms[:5], risk, {}, {s: 10 for s in syms[:5]}, {})   # 50%: untouched
    assert all(v == 10000 for v in five.values())
    rb = sizing.budgets(100_000, ["A", "B"], {**RB, "max_position_usd": 1e9, "gross_budget_pct": 90, "max_gross_pct": 50}, {"A": 1.0, "B": 1.0}, {}, {})
    assert sum(rb.values()) == pytest.approx(50_000)


def test_quantities_round_down():
    assert sizing.to_qty(10000, 768.64, 1, False) == 13
    assert sizing.to_qty(10000, 768.64, -1, False) == -13
    assert sizing.to_qty(1000, 60000, 1, True) == pytest.approx(0.0166)
    assert sizing.to_qty(10000, 100, 0.5, False) == 50   # half a signal, half the shares


def test_atr_uses_only_completed_days():
    bars = synthetic_bars(["X"], "15Min", 40, seed=3)["X"]
    days = sorted(set(bars.index.tz_convert("America/New_York").date))
    d = days[-5]
    a = sizing.daily_atr_pct(bars, d, 10)
    later = bars.copy()
    later.loc[later.index.tz_convert("America/New_York").date >= d, ["high", "low"]] *= [3, 0.3]  # wild future
    assert sizing.daily_atr_pct(later, d, 10) == pytest.approx(a)


def test_validation():
    assert sizing.validate({"sizing": "bogus", "gross_budget_pct": 150, "bucket_max_pct": {"crypto": 0}, "atr_days": 1}) == [
        "risk.sizing must be 'fixed' or 'risk_balanced'", "risk.gross_budget_pct must be between 0 and 100",
        "risk.bucket_max_pct.crypto must be between 0 and 100", "risk.atr_days must be a whole number between 2 and 250"]
    assert sizing.validate({"max_gross_pct": 150}) == ["risk.max_gross_pct must be between 0 and 100"]
    assert sizing.validate(RB) == []


# ----------------------------------------------------------------- portfolio backtester
@pytest.fixture(scope="module")
def two():
    return synthetic_bars(["A", "B"], "15Min", 60, seed=5)


def test_one_symbol_at_full_allocation_matches_run_backtest(two):
    df = two["A"]
    sig = get_signal("ema_cross", df, {})
    risk = {"max_daily_loss_pct": 2, "max_position_usd": 1e12}
    pb = portfolio_backtest({"A": df}, {"A": sig}, risk, {"A": 100}, costs=CostModel(0, 2))
    single = run_backtest(df, sig, CostModel(0, 2))
    assert pb["frame"].ret.to_numpy() == pytest.approx(single.ret.to_numpy(), abs=1e-12)


def test_portfolio_exposure_respects_the_limits(two):
    sigs = {s: pd.Series(1.0, index=df.index) for s, df in two.items()}   # always fully in
    risk = {**RB, "max_position_usd": 30000, "gross_budget_pct": 60}
    pb = portfolio_backtest(two, sigs, risk, {}, {}, CostModel(0, 2))
    eq_start = pb["frame"].equity.shift(1).fillna(1.0) * 100_000
    dollars = pb["exposure"].mul(eq_start, axis=0)
    assert (pb["exposure"].abs().sum(axis=1) <= 0.60 + 1e-9).all()
    assert (dollars.abs() <= 30000 * 1.06).all().all()     # the cap is set at the day's start; intraday drift only


def test_live_bot_and_backtest_size_the_same(env, monkeypatch, two):  # noqa: F811
    cfg = yaml.safe_load(env.read_text())
    cfg["live"]["risk"].update(sizing="risk_balanced", gross_budget_pct=90, atr_days=10, max_position_usd=1e9)
    tc = FakeTC()
    bot = live.Bot(cfg, tc=tc, cfg_path=None)
    bars = live._download()
    today = pd.Timestamp.now(tz="America/New_York").date()
    atr = sizing.daily_atr_pct(bars, today, 10)
    b = bot.budgets(100_000)
    assert b == sizing.budgets(100_000, ["SPY", "AAPL"], cfg["live"]["risk"], {"SPY": atr, "AAPL": atr}, {}, {})
    assert b["SPY"] == pytest.approx(45_000)          # same bars for both symbols: an equal split of 90%
    bot.step_once()
    assert tc.orders                                   # it really traded
    for o in tc.orders:
        assert abs(o.qty) * float(bars.close.iloc[-30:].max()) <= b[o.symbol] * 1.05
