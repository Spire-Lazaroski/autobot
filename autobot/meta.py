"""
Meta-strategies: decide WHICH strategy holds the position at each bar.

Each one is an ordinary strategy (df, **params) -> target position series, registered
in STRATEGIES with family "meta", so the backtester, parameter search, walk-forward and
the live bot use them unchanged.

No look-ahead: the choice made at bar t uses only bars <= t (indicators end at t,
trailing performance ends at t), and the backtester trades it on bar t+1. Every
underlying strategy runs continuously on the full history, so when it takes over it
starts from its own current state, not from a reset. Costs are charged on position
turnover, so a switch that leaves the position unchanged costs nothing.

`explain_*` functions return the active strategy / regime per bar for the UI.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from . import indicators as ind
from .indicators import COL
from .backtest import CostModel, run_backtest
from .strategies import STRATEGIES, StrategySpec, get_signal

FLAT = "flat"
REGIMES = ("trend", "range", "high_vol")
DEFAULT_POOL = ["ema_cross", "donchian_breakout", "rsi_reversion", "bollinger_reversion", "momentum"]


# ----------------------------------------------------------------- helpers
def _check(names: List[str]):
    for n in names:
        if n == FLAT:
            continue
        if n not in STRATEGIES:
            raise ValueError(f"unknown strategy '{n}'")
        if STRATEGIES[n].family == "meta":
            raise ValueError(f"'{n}' is a meta-strategy; meta-strategies can't be nested")


def candidate_signals(df: pd.DataFrame, names: List[str], candidate_params: Dict | None,
                      allow_short: bool) -> pd.DataFrame:
    """Each candidate's own target position on every bar (FLAT = all zeros)."""
    _check(names)
    cp = candidate_params or {}
    cols = {}
    for n in dict.fromkeys(names):  # unique, keep order
        cols[n] = (pd.Series(0.0, index=df.index) if n == FLAT
                   else get_signal(n, df, cp.get(n), allow_short))
    return pd.DataFrame(cols, index=df.index)


def _pick(sigs: pd.DataFrame, active: pd.Series) -> pd.Series:
    """Position = the active candidate's signal on each bar (NaN/None/'flat' -> 0)."""
    out = pd.Series(0.0, index=sigs.index)
    for n in sigs.columns:
        m = (active == n).to_numpy()
        out[m] = sigs[n].to_numpy()[m]
    return out


# ----------------------------------------------------------------- a) regime switch
def classify_regimes(df: pd.DataFrame, trend_measure="adx", adx_period=14, adx_trend=25.0,
                     er_period=20, er_trend=0.3, vr_period=100, vr_trend=1.1,
                     vol_period=20, vol_lookback=500, high_vol_pct=0.9, confirm_bars=1) -> pd.Series:
    """Label each bar 'high_vol', 'trend', 'range' or 'warmup'. High volatility wins,
    then trend strength, otherwise range. All inputs are trailing windows ending at t.

    confirm_bars > 1 adds hysteresis: a new regime is adopted only after it has been the raw
    label for that many consecutive bars (the last N bars, so still causal)."""
    volp = ind.vol_percentile(df.close, vol_period, vol_lookback)
    if trend_measure == "adx":
        strength, thr = ind.adx(df, adx_period), adx_trend
    elif trend_measure == "er":
        strength, thr = ind.efficiency_ratio(df.close, er_period), er_trend
    elif trend_measure == "vr":
        strength, thr = ind.variance_ratio(df.close, vr_period), vr_trend
    else:
        raise ValueError("trend_measure must be adx, er or vr")
    lab = np.where(strength >= thr, "trend", "range").astype(object)
    lab = np.where(volp >= high_vol_pct, "high_vol", lab)
    lab = np.where(strength.isna() | volp.isna(), "warmup", lab)
    raw = pd.Series(lab, index=df.index)
    if confirm_bars <= 1:
        return raw
    codes = pd.Series(pd.factorize(raw)[0], index=df.index, dtype=float)
    win = codes.rolling(int(confirm_bars))
    steady = (win.min() == win.max()) | (raw == "warmup")
    return raw.where(steady).ffill().fillna("warmup")


def _regime_parts(df, p, allow_short):
    regime = classify_regimes(df, p["trend_measure"], p["adx_period"], p["adx_trend"], p["er_period"],
                              p["er_trend"], p["vr_period"], p["vr_trend"], p["vol_period"],
                              p["vol_lookback"], p["high_vol_pct"], p["confirm_bars"])
    mapping = {"trend": p["trend_strategy"], "range": p["range_strategy"], "high_vol": p["high_vol_strategy"]}
    sigs = candidate_signals(df, list(mapping.values()), p.get("candidate_params"), allow_short)
    active = regime.map(lambda r: mapping.get(r, FLAT))
    return regime, active, sigs


REGIME_DEFAULTS = {
    "trend_strategy": "ema_cross", "range_strategy": "rsi_reversion", "high_vol_strategy": FLAT,
    "trend_measure": "adx", "adx_period": 14, "adx_trend": 25.0, "er_period": 20, "er_trend": 0.3,
    "vr_period": 100, "vr_trend": 1.1, "vol_period": 20, "vol_lookback": 500, "high_vol_pct": 0.9,
    "confirm_bars": 3, "candidate_params": {},
}


def regime_switch(df, allow_short=False, **params):
    """Classify the market each bar, hand the position to the strategy mapped to that regime."""
    p = {**REGIME_DEFAULTS, **params}
    _, active, sigs = _regime_parts(df, p, allow_short)
    return _pick(sigs, active)


def explain_regime(df, params, allow_short=False):
    p = {**REGIME_DEFAULTS, **params}
    regime, active, _ = _regime_parts(df, p, allow_short)
    return {"names": [a if r != "warmup" else None for a, r in zip(active, regime)], "regimes": regime.tolist()}


# ----------------------------------------------------------------- b) performance rotation
ROTATION_DEFAULTS = {"candidates": DEFAULT_POOL, "lookback": 200, "rebalance": 50,
                     "cost_bps": 2.0, "flat_if_negative": True, "candidate_params": {}}


def trailing_sharpe(df, sigs: pd.DataFrame, lookback: int, cost_bps: float) -> pd.DataFrame:
    """Each candidate's Sharpe over the last `lookback` bars, ending at t (not annualized).
    Uses the same next-bar execution and costs as the backtester."""
    costs = CostModel(0.0, cost_bps)
    out = {}
    for n in sigs.columns:
        r = run_backtest(df, sigs[n], costs).ret
        mu, sd = r.rolling(lookback).mean(), r.rolling(lookback).std()
        out[n] = (mu / sd.replace(0, np.nan)).fillna(0.0).where(mu.notna())
    return pd.DataFrame(out, index=df.index)


def _rotation_parts(df, p, allow_short):
    names = list(p["candidates"])
    if not names:
        raise ValueError("rotation needs at least one candidate")
    sigs = candidate_signals(df, names, p.get("candidate_params"), allow_short)
    sh = trailing_sharpe(df, sigs, int(p["lookback"]), float(p["cost_bps"]))
    k = max(1, int(p["rebalance"]))
    i = np.arange(len(df))
    decide = ((i + 1) % k == 0) & sh.notna().all(axis=1).to_numpy()
    best = sh.fillna(-np.inf).idxmax(axis=1).where(decide)
    if p["flat_if_negative"]:
        best = best.where(~(decide & (sh.max(axis=1) <= 0).to_numpy()), FLAT)
    # hold each decision until the next one; nothing before the first decision
    active = best.ffill().fillna(FLAT)
    return active, sigs.assign(**{FLAT: 0.0}), sh


def perf_rotation(df, allow_short=False, **params):
    """Every `rebalance` bars, switch to the candidate with the best trailing Sharpe."""
    p = {**ROTATION_DEFAULTS, **params}
    active, sigs, _ = _rotation_parts(df, p, allow_short)
    return _pick(sigs, active)


def explain_rotation(df, params, allow_short=False):
    p = {**ROTATION_DEFAULTS, **params}
    active, _, _ = _rotation_parts(df, p, allow_short)
    return {"names": active.tolist()}


# ----------------------------------------------------------------- c) ensemble vote
ENSEMBLE_DEFAULTS = {"candidates": DEFAULT_POOL, "method": "majority", "threshold": 0.5, "candidate_params": {}}


def _vote(df, p, allow_short):
    sigs = candidate_signals(df, list(p["candidates"]), p.get("candidate_params"), allow_short)
    return sigs.mean(axis=1), sigs


def ensemble_vote(df, allow_short=False, **params):
    """majority: take a side when more than `threshold` of the net vote agrees.
    average: hold the mean of all candidates' positions (fractional sizing)."""
    p = {**ENSEMBLE_DEFAULTS, **params}
    avg, _ = _vote(df, p, allow_short)
    if p["method"] == "average":
        return avg
    return np.sign(avg) * (avg.abs() > float(p["threshold"]))


def explain_ensemble(df, params, allow_short=False):
    p = {**ENSEMBLE_DEFAULTS, **params}
    avg, sigs = _vote(df, p, allow_short)
    pos = ensemble_vote(df, allow_short, **params)
    label = np.where(pos > 0, "long", np.where(pos < 0, "short", FLAT))
    return {"names": label.tolist(), "vote": avg.round(3).tolist(),
            "longs": (sigs > 0).sum(axis=1).tolist(), "shorts": (sigs < 0).sum(axis=1).tolist()}


# ----------------------------------------------------------------- chart panes
@ind.plot("regime_switch")
def _(df, p):
    p = {**REGIME_DEFAULTS, **p}
    m = p["trend_measure"]
    s, thr = {"adx": (ind.adx(df, p["adx_period"]), p["adx_trend"]),
              "er": (ind.efficiency_ratio(df.close, p["er_period"]), p["er_trend"]),
              "vr": (ind.variance_ratio(df.close, p["vr_period"]), p["vr_trend"])}[m]
    vp = ind.vol_percentile(df.close, p["vol_period"], p["vol_lookback"])
    return {"panes": [ind._pane(f"trend strength ({m})", [ind._line(m, s, COL['primary'])], [thr]),
                      ind._pane("vol percentile", [ind._line("vol pct", vp, COL['vol'])], [p["high_vol_pct"]], [0, 1])]}


@ind.plot("perf_rotation")
def _(df, p):
    p = {**ROTATION_DEFAULTS, **p}
    sigs = candidate_signals(df, list(p["candidates"]), p.get("candidate_params"), False)
    sh = trailing_sharpe(df, sigs, int(p["lookback"]), float(p["cost_bps"]))
    return {"panes": [ind._pane(f"trailing {p['lookback']}-bar Sharpe", [ind._line(n, sh[n]) for n in sh.columns], [0])]}


@ind.plot("ensemble_vote")
def _(df, p):
    p = {**ENSEMBLE_DEFAULTS, **p}
    avg, _ = _vote(df, p, False)
    t = float(p["threshold"])
    return {"panes": [ind._pane("net vote", [ind._line("vote", avg, COL['osc'])], [t, -t, 0], [-1, 1])]}


# ----------------------------------------------------------------- registry
for spec in [
    StrategySpec("regime_switch", regime_switch, "meta",
                 "Label each bar trend / range / high-vol (ADX, efficiency ratio or variance ratio + vol "
                 "percentile) and give the position to the strategy mapped to that regime.",
                 REGIME_DEFAULTS, {"adx_trend": [20.0, 25.0, 30.0], "high_vol_pct": [0.8, 0.9, 1.01]},
                 warmup_days=30, explain=explain_regime),
    StrategySpec("perf_rotation", perf_rotation, "meta",
                 "Every K bars, rotate into the candidate with the best trailing Sharpe; "
                 "optionally stay flat when all are negative.",
                 ROTATION_DEFAULTS, {"lookback": [100, 200, 400], "rebalance": [25, 50, 100]},
                 warmup_days=30, explain=explain_rotation),
    StrategySpec("ensemble_vote", ensemble_vote, "meta",
                 "Majority vote (or average) of the selected strategies' positions.",
                 ENSEMBLE_DEFAULTS, {"threshold": [0.0, 0.34, 0.5, 0.67]},
                 warmup_days=30, explain=explain_ensemble),
]:
    STRATEGIES[spec.name] = spec
