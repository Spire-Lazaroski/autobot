"""
Why the bot acts: decision records for logs/decisions.jsonl (live) and for backtests.

Record types (one JSON object per line):
  decision  every bar for every symbol (live) / every position change (backtest):
            strategy in charge, regime + the indicator values behind it, signal,
            action taken or "no change", order ids, bar price, fill price, reason
  regime    the market mood of a symbol changed (before/after values + the rule)
  switch    a meta-strategy handed control to another strategy (before/after + why)
  event     risk limit, STOP, flatten, errors

Reasons are written from the logged numbers with fixed templates when the decision is
made, so a report never has to guess. Anything that couldn't be computed is left out;
reports say "reason not recorded".
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from . import plain
from .meta import REGIME_DEFAULTS, classify_regimes
from .strategies import STRATEGIES, get_signal

MEASURE_LABEL = {"adx": "trend strength (ADX)", "er": "trend efficiency", "vr": "trend persistence"}


# ----------------------------------------------------------------- numbers behind a decision
def _f(v) -> Optional[float]:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def indicator_frame(strategy: str, df: pd.DataFrame, params: Dict) -> pd.DataFrame:
    """Every series the chart draws for this strategy, as columns (same numbers as the chart)."""
    p = {**STRATEGIES[strategy].defaults, **(params or {})}
    plot = ind.plot_indicators(strategy, df, p)
    cols = {}
    for line in plot["overlays"]:
        cols[line["name"]] = line["values"]
    for pane in plot["panes"]:
        for line in pane["series"]:
            cols[line["name"]] = line["values"]
    return pd.DataFrame(cols, index=df.index) if cols else pd.DataFrame(index=df.index)


def regime_params(strategy: str, params: Dict) -> Dict:
    """A regime_switch job uses its own thresholds; every other job gets the defaults,
    so reports can describe the market mood for any strategy."""
    src = {**REGIME_DEFAULTS, **(params or {})} if strategy == "regime_switch" else dict(REGIME_DEFAULTS)
    keys = ["trend_measure", "adx_period", "adx_trend", "er_period", "er_trend", "vr_period", "vr_trend",
            "vol_period", "vol_lookback", "high_vol_pct", "confirm_bars"]
    return {k: src[k] for k in keys}


def regime_frame(df: pd.DataFrame, rp: Dict) -> pd.DataFrame:
    m = rp["trend_measure"]
    strength = {"adx": lambda: ind.adx(df, rp["adx_period"]),
                "er": lambda: ind.efficiency_ratio(df.close, rp["er_period"]),
                "vr": lambda: ind.variance_ratio(df.close, rp["vr_period"])}[m]()
    thr = {"adx": rp["adx_trend"], "er": rp["er_trend"], "vr": rp["vr_trend"]}[m]
    regime = classify_regimes(df, **rp)
    ret20 = df.close.pct_change(20)
    return pd.DataFrame({
        "regime": regime, "trend_strength": strength, "vol_pct": ind.vol_percentile(df.close, rp["vol_period"], rp["vol_lookback"]),
        "direction": np.where(ret20 > 0, "up", np.where(ret20 < 0, "down", "flat")),
    }, index=df.index).assign(trend_measure=m, trend_threshold=thr, vol_threshold=rp["high_vol_pct"],
                              confirm_bars=rp["confirm_bars"])


def _row(frame: pd.DataFrame, i: int) -> Dict:
    if frame.empty:
        return {}
    r = frame.iloc[i]
    out = {}
    for k, v in r.items():
        fv = _f(v) if not isinstance(v, str) else v
        if fv is not None:
            out[str(k)] = round(fv, 4) if isinstance(fv, float) else fv
    return out


def regime_values(row: Dict) -> Dict:
    keys = ("trend_measure", "trend_strength", "trend_threshold", "vol_pct", "vol_threshold", "direction", "confirm_bars")
    return {k: row[k] for k in keys if k in row}


# ----------------------------------------------------------------- plain-English reasons
def _cmp(a, b):
    return "above" if a > b else "below" if a < b else "level with"


def _two(a: float, b: float) -> tuple:
    """Format two close numbers with just enough decimals that they don't look equal."""
    for d in (2, 3, 4, 5):
        x, y = f"{a:,.{d}f}", f"{b:,.{d}f}"
        if x != y or a == b:
            return x, y
    return f"{a:,.5f}", f"{b:,.5f}"


def condition_text(strategy: str, p: Dict, v: Dict, price: Optional[float]) -> Optional[str]:
    """What the strategy's own numbers said on this bar, in words. None if not computable."""
    g = v.get
    try:
        if strategy in ("sma_cross", "ema_cross"):
            kind = "SMA" if strategy == "sma_cross" else "EMA"
            a, b = g(f"{kind} {p['fast']}"), g(f"{kind} {p['slow']}")
            if a is None or b is None:
                return None
            x, y = _two(a, b)
            return f"the {p['fast']}-bar average ({x}) was {_cmp(a, b)} the {p['slow']}-bar average ({y})"
        if strategy == "macd":
            m, s = g("macd"), g("signal")
            if m is None or s is None:
                return None
            x, y = _two(m, s)
            return f"the momentum line ({x}) was {_cmp(m, s)} its signal line ({y})"
        if strategy == "rsi_reversion":
            r = g("rsi")
            return None if r is None else f"RSI was {r:.0f} (buy below {p['oversold']}, sell above {p['exit_level']})"
        if strategy == "bollinger_reversion":
            lo, mid = g("lower"), g("mid")
            return None if lo is None or mid is None or price is None else (
                f"the price ({price:,.2f}) was {_cmp(price, lo)} the lower band ({lo:,.2f}); the average was {mid:,.2f}")
        if strategy == "donchian_breakout":
            hi, lo = g(f"{p['entry']}-bar high"), g(f"{p['exit']}-bar low")
            return None if hi is None or lo is None or price is None else (
                f"the price ({price:,.2f}) against the {p['entry']}-bar high ({hi:,.2f}) and {p['exit']}-bar low ({lo:,.2f})")
        if strategy == "vwap_reversion":
            vw = g("VWAP")
            return None if vw is None or price is None else (
                f"the price was {(price / vw - 1) * 100:+.2f}% from the day's average price ({vw:,.2f}); it buys below -{p['band_pct']}%")
        if strategy == "momentum":
            r = g("roc")
            return None if r is None else f"the price had moved {r:+.2f}% over the last {p['lookback']} bars"
        if strategy == "connors_rsi2":
            r, t = g("rsi"), g(f"SMA {p['trend']}")
            return None if r is None or t is None or price is None else (
                f"RSI({p['rsi_period']}) was {r:.0f} and the price was {_cmp(price, t)} its {p['trend']}-bar average ({t:,.2f})")
        if strategy == "ibs_reversion":
            b = g("ibs")
            return None if b is None else f"the bar closed at {b * 100:.0f}% of its range (buy below {p['low'] * 100:.0f}%, sell above {p['high'] * 100:.0f}%)"
        if strategy == "opening_range_breakout":
            hi, lo = g("OR high"), g("OR low")
            return None if hi is None or lo is None or price is None else f"the price ({price:,.2f}) against the opening range {lo:,.2f}-{hi:,.2f}"
        if strategy == "intraday_momentum":
            r = g("ret")
            return None if r is None else f"the first {p['signal_min']} minutes returned {r:+.2f}%"
        if strategy == "noise_area":
            hi, lo = g("noise +"), g("noise -")
            return None if hi is None or lo is None or price is None else f"the price ({price:,.2f}) against the usual-noise band {lo:,.2f}-{hi:,.2f}"
        if strategy == "buy_and_hold":
            return "it always holds"
    except (KeyError, TypeError, ValueError):
        return None
    return None


def regime_text(rv: Dict, regime: Optional[str] = None) -> Optional[str]:
    if "trend_strength" not in rv:
        return None
    label = MEASURE_LABEL.get(rv.get("trend_measure"), "trend strength")
    s, thr = rv["trend_strength"], rv.get("trend_threshold")
    parts = [f"{label} {_num(rv)} vs threshold {thr}" if thr is not None else f"{label} {_num(rv)}"]
    if "vol_pct" in rv:
        parts.append(f"volatility percentile {rv['vol_pct']:.2f} vs {rv.get('vol_threshold')}")
    cb = int(rv.get("confirm_bars") or 1)
    raw = ("high_vol" if rv.get("vol_pct", 0) >= (rv.get("vol_threshold") or 9) else
           "trend" if thr is not None and s >= thr else "range")
    if regime and regime != raw and cb > 1:
        parts.append(f"the numbers point to {plain.REGIMES[raw]['short']}, but a new mood must last {cb} bars before the bot acts on it")
    return "; ".join(parts)


def _num(rv: Dict, key: str = "trend_strength") -> str:
    v = rv.get(key)
    if v is None:
        return "?"
    return f"{v:.0f}" if rv.get("trend_measure") == "adx" and key == "trend_strength" else f"{v:.2f}"


def mood(regime: Optional[str], direction: Optional[str] = None) -> str:
    if regime == "trend" and direction in ("up", "down"):
        return f"a strong {direction}trend"
    return plain.regime_name(regime)


def mood_adj(regime: Optional[str], direction: Optional[str] = None) -> str:
    if regime == "trend":
        return f"trending {direction} strongly" if direction in ("up", "down") else "trending strongly"
    return {"range": "calm and sideways", "high_vol": "jumpy (unusually high volatility)",
            "warmup": "still warming up (not enough history)"}.get(regime or "", regime or "unknown")


def want(sig: float) -> str:
    return "wanted to own it" if sig > 0 else "wanted to be short" if sig < 0 else "wanted to be out"


def decision_reason(strategy: str, params: Dict, active: Optional[str], active_params: Dict,
                    ind_active: Dict, rv: Dict, regime: Optional[str], sig: float, price: Optional[float],
                    extra: Optional[str] = None) -> str:
    """One sentence built only from logged numbers."""
    if extra:
        return extra
    spec = STRATEGIES[strategy]
    if strategy == "ensemble_vote":
        vote = ind_active.get("vote")
        n = len(params.get("candidates") or [])
        v = f"a net vote of {vote:+.2f}" if vote is not None else "a vote (value not recorded)"
        return (f"'{plain.nick(strategy)}' {want(sig)}: the {n} selected strategies gave {v} "
                f"and it acts when the vote is beyond {params.get('threshold')} ({params.get('method')}).")
    who = active if spec.family == "meta" else strategy
    if spec.family == "meta" and (not active or active == "flat"):
        if strategy == "regime_switch":
            return f"The market was {mood_adj(regime, rv.get('direction'))}, and this job is set to stay out then ({regime_text(rv, regime) or 'values not recorded'})."
        return "No strategy was in charge, so the bot stayed out."
    cond = condition_text(who, active_params, ind_active, price)
    head = f"'{plain.nick(who)}' ({who}) {want(sig)}"
    body = f" because {cond}" if cond else " (reason not recorded)"
    tail = ""
    if strategy == "regime_switch":
        tail = f"; it was in charge because the market was {mood_adj(regime, rv.get('direction'))} ({regime_text(rv, regime) or 'values not recorded'})"
    elif strategy == "perf_rotation":
        tail = "; it was in charge because it had the best recent risk-adjusted return"
    return head + body + tail + "."


def regime_change_reason(symbol: str, a: Optional[str], b: str, before: Dict, after: Dict) -> str:
    """`before` = values when the old mood began, `after` = values now."""
    label = MEASURE_LABEL.get(after.get("trend_measure"), "trend strength")
    s0, s1 = before.get("trend_strength"), after.get("trend_strength")
    v1, thr, vt = after.get("vol_pct"), after.get("trend_threshold"), after.get("vol_threshold")
    cb = int(after.get("confirm_bars") or 1)
    streak = f" for {cb} bars in a row" if cb > 1 else ""
    bits = []
    if s0 is not None and s1 is not None:
        verb = "rose" if s1 > s0 else "fell" if s1 < s0 else "stayed"
        bits.append(f"{label} {verb} from {_num(before)} to {_num(after)}")
    if b == "high_vol" and v1 is not None:
        bits.append(f"volatility percentile above {vt}{streak} (now {v1:.2f})")
    elif b == "trend" and thr is not None:
        bits.append(f"above {thr}{streak}")
    elif b == "range" and thr is not None:
        bits.append(f"below {thr}{streak}" + (f", and volatility back under {vt} (now {v1:.2f})" if a == "high_vol" and v1 is not None else ""))
    why = f" ({'; '.join(bits)})" if bits else ""
    return f"{symbol} moved from {mood(a, before.get('direction'))} to {mood(b, after.get('direction'))}{why}."


# ----------------------------------------------------------------- per-bar context
class Context:
    """All frames for one strategy on one symbol's bars, computed once."""

    def __init__(self, strategy: str, df: pd.DataFrame, params: Dict, allow_short: bool):
        self.strategy, self.df = strategy, df
        self.spec = STRATEGIES[strategy]
        self.params = {**self.spec.defaults, **(params or {})}
        self.allow_short = allow_short
        self.signal = get_signal(strategy, df, self.params, allow_short)
        self.ind = indicator_frame(strategy, df, self.params)
        self.reg = regime_frame(df, regime_params(strategy, self.params))
        self.active = (self.spec.explain(df, self.params, allow_short)["names"] if self.spec.explain
                       else [strategy] * len(df))
        self._cand: Dict[str, pd.DataFrame] = {}

    def cand_params(self, name: str) -> Dict:
        return {**STRATEGIES[name].defaults, **(self.params.get("candidate_params") or {}).get(name, {})} if name in STRATEGIES else {}

    def active_indicators(self, i: int, name: Optional[str]) -> Dict:
        if self.spec.family != "meta":
            return _row(self.ind, i)
        if not name or name not in STRATEGIES:
            return _row(self.ind, i)
        if name not in self._cand:
            self._cand[name] = indicator_frame(name, self.df, self.cand_params(name))
        return {**_row(self._cand[name], i), **{k: v for k, v in _row(self.ind, i).items() if k == "vote"}}

    def snapshot(self, i: int) -> Dict:
        name = self.active[i] if self.spec.family == "meta" else self.strategy
        if self.strategy == "ensemble_vote":
            name = None
        reg = _row(self.reg, i)
        price = _f(self.df.close.iloc[i])
        sig = float(self.signal.iloc[i])
        a_ind = self.active_indicators(i, name)
        return {
            "strategy": self.strategy, "active": name if self.spec.family == "meta" else self.strategy,
            "regime": reg.get("regime"), "regime_values": regime_values(reg),
            "indicators": {**_row(self.ind, i), **({f"{name}: {k}": v for k, v in a_ind.items()} if self.spec.family == "meta" and name else {})},
            "signal": round(sig, 4), "price": price,
            "reason": decision_reason(self.strategy, self.params, name, self.cand_params(name) if name in STRATEGIES else {},
                                      a_ind, regime_values(reg), reg.get("regime"), sig, price),
        }


def _iso(ts) -> str:
    return pd.Timestamp(ts).tz_convert("UTC").isoformat() if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts).tz_localize("UTC").isoformat()


# ----------------------------------------------------------------- live recorder
class Recorder:
    """Appends records to logs/decisions.jsonl and remembers each symbol's last regime and
    strategy in charge, so it can log changes with before/after values."""

    def __init__(self, path: Path):
        self.path = path
        self.last: Dict[str, Dict] = {}

    def write(self, rec: Dict):
        rec = {"time": datetime.now(timezone.utc).isoformat(), "source": "live", **rec}
        self.path.parent.mkdir(exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=_json_default) + "\n")

    def bar(self, job: str, symbol: str, ctx: Context, position_before: float, target: float, action: str,
            qty: float = 0.0, order_ids: Optional[List[str]] = None, fill_price: Optional[float] = None,
            reason_override: Optional[str] = None) -> Dict:
        i = len(ctx.df) - 1
        snap = ctx.snapshot(i)
        if reason_override:
            snap["reason"] = reason_override
        bar_time = _iso(ctx.df.index[i])
        prev = self.last.get(symbol)
        if prev and prev["regime"] != snap["regime"] and snap["regime"] not in (None, "warmup"):
            self.write({"type": "regime", "bar_time": bar_time, "job": job, "symbol": symbol, "from": prev["regime"],
                        "to": snap["regime"], "before": prev["regime_values"], "after": snap["regime_values"],
                        "reason": regime_change_reason(symbol, prev["regime"], snap["regime"], prev["regime_values"], snap["regime_values"])})
        if prev and ctx.spec.family == "meta" and prev["active"] != snap["active"] and snap["active"] is not None:
            self.write(switch_record(job, symbol, bar_time, ctx.strategy, prev["active"], snap["active"], snap))
        began = prev["regime_values"] if prev and prev["regime"] == snap["regime"] else snap["regime_values"]
        self.last[symbol] = {"regime": snap["regime"], "regime_values": began, "active": snap["active"]}
        rec = {"type": "decision", "bar_time": bar_time, "job": job, "symbol": symbol, **snap,
               "position_before": position_before, "target": target, "action": action, "qty": qty,
               "order_ids": order_ids or [], "fill_price": fill_price}
        self.write(rec)
        return rec

    def event(self, kind: str, message: str, **extra):
        self.write({"type": "event", "event": kind, "message": message, **extra})


def switch_record(job, symbol, bar_time, strategy, a, b, snap) -> Dict:
    if strategy == "ensemble_vote":
        word = {"long": "own it", "short": "be short", "flat": "stay out"}
        reason = f"The vote changed from '{word.get(a, a)}' to '{word.get(b, b)}'."
    else:
        if strategy == "regime_switch":
            why = f"the market turned {mood_adj(snap['regime'], snap['regime_values'].get('direction'))}"
        else:
            why = f"'{plain.nick(b)}' had the best recent risk-adjusted return" if b not in (None, "flat") else "every candidate had been losing"
        reason = f"Switched from '{plain.nick(a)}' to '{plain.nick(b)}' because {why}."
    return {"type": "switch", "bar_time": bar_time, "job": job, "symbol": symbol, "strategy": strategy,
            "from": a, "to": b, "regime": snap["regime"], "regime_values": snap["regime_values"], "reason": reason}


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return str(o)


def read_jsonl(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue  # a half-written last line; skip it
    return out


# ----------------------------------------------------------------- backtest records
def backtest_decisions(df: pd.DataFrame, symbol: str, strategy: str, params: Dict, allow_short: bool = False,
                       job: str = "backtest") -> List[Dict]:
    """The same record types for a backtest: every position change, regime change and
    strategy switch. Fills are at the close of the signal bar (how the backtester trades)."""
    ctx = Context(strategy, df, params, allow_short)
    sig = ctx.signal.to_numpy()
    out: List[Dict] = []
    prev_reg, prev_rv, prev_active = None, {}, None
    for i in range(len(df)):
        reg_row = _row(ctx.reg, i)
        reg, rv = reg_row.get("regime"), regime_values(reg_row)
        bar_time = _iso(df.index[i])
        close_time = _iso(df.index[i] + (df.index[i] - df.index[i - 1] if i else pd.Timedelta(0)))
        base = {"time": close_time, "source": "backtest", "bar_time": bar_time, "job": job, "symbol": symbol}
        if prev_reg not in (None, "warmup") and reg not in (None, "warmup") and reg != prev_reg:
            out.append({**base, "type": "regime", "from": prev_reg, "to": reg, "before": prev_rv, "after": rv,
                        "reason": regime_change_reason(symbol, prev_reg, reg, prev_rv, rv)})
        if reg not in (None, "warmup") and reg != prev_reg:
            prev_reg, prev_rv = reg, rv  # values when this mood began
        act = ctx.active[i] if ctx.spec.family == "meta" else strategy
        if ctx.spec.family == "meta" and prev_active is not None and act != prev_active and act is not None:
            snap = ctx.snapshot(i)
            out.append({**base, **switch_record(job, symbol, bar_time, strategy, prev_active, act, snap), "time": close_time})
        if act is not None:
            prev_active = act
        before = sig[i - 1] if i else 0.0
        if sig[i] != before:
            snap = ctx.snapshot(i)
            px = snap["price"]
            out.append({**base, "type": "decision", **snap, "position_before": float(before), "target": float(sig[i]),
                        "action": "buy" if sig[i] > before else "sell", "qty": abs(float(sig[i] - before)),
                        "order_ids": [f"bt-{symbol}-{i}"], "fill_price": px})
    return out
