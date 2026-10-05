"""
Optional exit layer on top of any strategy: a trailing exit, a take-profit, a stop-loss and
re-entry rules (default OFF).

One pure function, apply_exit_layer(signal, df, exit_cfg), used by run_backtest,
portfolio_backtest, the exit-rule study, recalibration and the live bot, so the backtest and the
bot behave identically. exit_states() returns the same targets plus the per-bar state (peak,
trail level, what a re-entry is waiting for, and a reason code for the decision log).

Per job in config.yaml (validated by liveconfig.validate_live through validate()):
  exit:
    trail_pct: 0.25          sell if a bar CLOSES this % below the highest close since entry
    take_profit_pct: 1.0     sell if a bar CLOSES this % above the entry close (take the profit)
    stop_pct: 0.5            sell if a bar CLOSES this % below the entry close (cut the loss)
                             each of the three is off unless present; any one switches the layer on
    reentry: both            fresh_cross | new_high | both | cooldown
    cooldown_bars: 6         only for "cooldown"
    no_entry_last_min: 30    no new positions in the last N minutes before the close (stocks)

Rules (decided at each bar's close from completed bars only; the backtester trades the result on
the next bar, like every signal):
1. While long, track the highest close since entry. If a close is below peak x (1 - trail%), the
   target becomes 0 even if the strategy still says +1.
   Take-profit / stop-loss: the same, measured from the close of the bar the entry was decided
   on (the fill is one bar later, like every signal). The stop is checked first, then the trail,
   then the take-profit.
2. After such an exit (trail, take-profit or stop), stay flat until the re-entry rule allows a new entry, and never on
   the bar right after the exit:
     fresh_cross  the strategy goes to 0 (or -1) and back to +1
     new_high     the strategy says +1 and a bar closes above the peak we exited from
     both         whichever comes first
     cooldown     wait cooldown_bars bars, then follow the strategy again
   A strategy signal for the other side ends the wait (and is followed, if shorts are allowed).
3. A normal strategy exit (signal 0) resets everything, as without the layer.
4. Shorts mirror it: lowest close since entry, exit if a close rises trail% above it; the
   take-profit is BELOW the entry and the stop ABOVE it.
Causal by construction: one pass from the first bar to the last, state only from the past.

Stocks are flattened before the close by the caller first (flat_before_close), and every new New
York session starts fresh for stocks (no stale peak, no wait carried over), like the live bot,
which holds no stocks overnight. Crypto trades round the clock, so its state carries on.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .data import is_crypto

REENTRY = ("fresh_cross", "new_high", "both", "cooldown")
DEFAULTS = {"trail_pct": 0.25, "reentry": "both", "cooldown_bars": 6, "no_entry_last_min": 30}


LEVELS = ("trail_pct", "take_profit_pct", "stop_pct")


def enabled(cfg: Optional[Dict]) -> bool:
    return bool(cfg) and cfg.get("enabled", True) and any(float(cfg.get(k) or 0) > 0 for k in LEVELS)


def validate(cfg, where: str = "exit") -> List[str]:
    if cfg is None:
        return []
    if not isinstance(cfg, dict):
        return [f"{where} must be a mapping"]
    errs = []
    t = cfg.get("trail_pct", DEFAULTS["trail_pct"])
    if not isinstance(t, (int, float)) or not 0 < t < 50:
        errs.append(f"{where}.trail_pct must be between 0 and 50")
    for k in ("take_profit_pct", "stop_pct"):
        v = cfg.get(k)
        if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 < v < 50):
            errs.append(f"{where}.{k} must be between 0 and 50")
    if cfg.get("reentry", DEFAULTS["reentry"]) not in REENTRY:
        errs.append(f"{where}.reentry must be one of {', '.join(REENTRY)}")
    c = cfg.get("cooldown_bars", DEFAULTS["cooldown_bars"])
    if not isinstance(c, int) or not 1 <= c <= 1000:
        errs.append(f"{where}.cooldown_bars must be a whole number from 1 to 1000")
    n = cfg.get("no_entry_last_min", DEFAULTS["no_entry_last_min"])
    if not isinstance(n, (int, float)) or not 0 <= n <= 390:
        errs.append(f"{where}.no_entry_last_min must be between 0 and 390")
    unknown = set(cfg) - set(DEFAULTS) - set(LEVELS) - {"enabled"}
    if unknown:
        errs.append(f"{where}: unknown settings {', '.join(sorted(unknown))}")
    return errs


def _minutes_to_close(df: pd.DataFrame) -> np.ndarray:
    """Minutes from each bar's END to 16:00 New York (negative after the close)."""
    step = pd.Series(df.index).diff().median() if len(df) > 1 else pd.Timedelta(minutes=5)
    ny = (df.index + step).tz_convert("America/New_York")
    return 16 * 60 - np.asarray(ny.hour * 60 + ny.minute)


def exit_states(signal: pd.Series, df: pd.DataFrame, cfg: Optional[Dict], crypto: Optional[bool] = None) -> pd.DataFrame:
    """Targets and state per bar. Columns: target, peak (since entry, or the exit peak while
    waiting), trail (the price a close must stay above / below), state (in | flat | waiting),
    reason (code for the decision log), waiting_for (re-entry rule while waiting), entry (the
    entry close), tp / stop (the take-profit and stop levels, NaN when off or flat)."""
    sig = signal.reindex(df.index).fillna(0.0).to_numpy(dtype=float)
    n = len(sig)
    out_t = sig.copy()
    out = {"peak": np.full(n, np.nan), "trail": np.full(n, np.nan), "state": np.array(["flat"] * n, dtype=object),
           "reason": np.array([None] * n, dtype=object), "waiting_for": np.array([None] * n, dtype=object),
           "entry": np.full(n, np.nan), "tp": np.full(n, np.nan), "stop": np.full(n, np.nan)}
    if not enabled(cfg):
        out["state"] = np.where(sig != 0, "in", "flat").astype(object)
        return pd.DataFrame({"target": out_t, **out}, index=df.index)
    c = {**DEFAULTS, **cfg}
    t = float(cfg.get("trail_pct") or 0) / 100        # each level is off unless set
    tp = float(cfg.get("take_profit_pct") or 0) / 100
    sl = float(cfg.get("stop_pct") or 0) / 100
    mode = c["reentry"]
    cool = int(c["cooldown_bars"])
    if crypto is None:
        crypto = is_crypto(str(df.attrs.get("symbol", "")))
    late = np.zeros(n, bool) if crypto or not c["no_entry_last_min"] else (_minutes_to_close(df) <= float(c["no_entry_last_min"])) & (_minutes_to_close(df) >= 0)
    close = df.close.to_numpy(dtype=float)
    day = np.asarray(df.index.tz_convert("America/New_York").date)
    prev = 0.0            # last target
    peak = np.nan         # best close since entry (highest when long, lowest when short)
    entry = np.nan        # close of the bar the entry was decided on
    wait_side = 0         # +1 / -1 while waiting after a trail exit
    exit_peak = np.nan
    since = 0             # bars since the trail exit
    reset = False         # the strategy went to 0 / the other side since the exit
    for i in range(n):
        s = sig[i]
        side = int(np.sign(s))
        if not crypto and i and day[i] != day[i - 1]:  # stocks: a new session starts fresh (flat overnight)
            wait_side, peak, prev = 0, np.nan, 0.0
        if wait_side:
            since += 1
            if side != wait_side:
                reset = True
            if side == -wait_side and side != 0:     # the strategy wants the other side: the wait ends
                wait_side = 0
            else:
                ok = since >= 2 and side == wait_side and (
                    (mode in ("fresh_cross", "both") and reset)
                    or (mode in ("new_high", "both") and close[i] * wait_side > exit_peak * wait_side)
                    or (mode == "cooldown" and since > cool))
                if ok and not late[i]:
                    wait_side = 0
                    out["reason"][i] = "reentry"
                else:
                    out_t[i] = 0.0
                    out["state"][i] = "waiting"
                    out["peak"][i] = exit_peak
                    out["waiting_for"][i] = mode
                    out["reason"][i] = "late" if ok and late[i] else "waiting"
                    prev = 0.0
                    continue
        tgt = s
        new_entry = side != 0 and np.sign(prev) != side
        if new_entry and late[i]:                    # no new positions near the close
            tgt, side = 0.0, 0
            out["reason"][i] = "late"
        if side == 0:
            peak = entry = np.nan
            out_t[i] = tgt
            prev = tgt
            continue
        if new_entry:
            peak = entry = close[i]
        else:
            peak = max(peak, close[i]) if side > 0 else min(peak, close[i])
        trail = (peak * (1 - t) if side > 0 else peak * (1 + t)) if t else np.nan
        tp_lvl = entry * (1 + side * tp) if tp else np.nan
        sl_lvl = entry * (1 - side * sl) if sl else np.nan
        out["peak"][i], out["trail"][i], out["state"][i] = peak, trail, "in"
        out["entry"][i], out["tp"][i], out["stop"][i] = entry, tp_lvl, sl_lvl
        x = close[i] * side   # "beyond" tests in the position's direction (a short's prices flipped)
        hit = ("stop_exit" if sl and x <= sl_lvl * side else
               "trail_exit" if t and x < trail * side else
               "take_profit" if tp and x >= tp_lvl * side else None)
        if hit:
            out_t[i] = 0.0
            out["state"][i] = "flat"
            out["reason"][i] = hit
            wait_side, exit_peak, since, reset = side, peak, 0, False
            peak = entry = np.nan
            prev = 0.0
            continue
        out_t[i] = tgt
        prev = tgt
    return pd.DataFrame({"target": out_t, **out}, index=df.index)


def apply_exit_layer(signal: pd.Series, df: pd.DataFrame, exit_cfg: Optional[Dict], crypto: Optional[bool] = None) -> pd.Series:
    """The strategy's signal with the trailing exit and re-entry rules applied (unchanged when
    exit_cfg is empty or disabled)."""
    if not enabled(exit_cfg):
        return signal
    return pd.Series(exit_states(signal, df, exit_cfg, crypto)["target"].to_numpy(), index=signal.index)


# ----------------------------------------------------------------- live view and wording
def exit_view_of(state: pd.Series, cfg: Dict, close: float) -> Dict:
    """status.json / decision-log view of the last bar's exit state, with the plain-English
    sentence (plain.EXIT_REASONS) when the layer changed or explains the target."""
    from . import plain
    c = {**DEFAULTS, **cfg}
    s = lambda v: v if isinstance(v, str) else None  # noqa: E731  (empty cells come back as NaN: not valid JSON)
    reason, waiting_for = s(state["reason"]), s(state["waiting_for"])
    peak = None if pd.isna(state["peak"]) else float(state["peak"])
    num = lambda k: None if k not in state or pd.isna(state[k]) else float(state[k])  # noqa: E731
    trail = num("trail")
    entry = num("entry")
    t = plain.EXIT_REASONS
    text = None
    if reason == "trail_exit" and peak:
        down = close < peak
        text = t["trail_exit" if down else "trail_exit_short"].format(
            close=f"{close:,.2f}", pct=f"{abs(close / peak - 1) * 100:.2f}", peak=f"{peak:,.2f}", trail=f"{float(c['trail_pct']):g}")
    elif reason in ("take_profit", "stop_exit") and entry:
        text = t[reason + ("" if (close > entry) == (reason == "take_profit") else "_short")].format(
            close=f"{close:,.2f}", pct=f"{abs(close / entry - 1) * 100:.2f}", entry=f"{entry:,.2f}",
            level=f"{float(c['take_profit_pct' if reason == 'take_profit' else 'stop_pct']):g}")
    elif reason == "waiting":
        what = t["wait_" + str(waiting_for)].format(peak=f"{peak:,.2f}" if peak else "?", n=int(c["cooldown_bars"]))
        text = t["waiting"].format(what=what)
    elif reason == "late":
        text = t["late"].format(n=int(c["no_entry_last_min"]))
    elif reason == "reentry":
        text = t["reentry"].format(rule=t["rule_" + str(c["reentry"])])
    return {"state": s(state["state"]), "peak": peak, "trail_level": trail, "waiting_for": waiting_for,
            "reason": reason, "text": text, "trail_pct": float(cfg.get("trail_pct") or 0), "reentry": c["reentry"],
            "entry": entry, "take_profit_level": num("tp"), "stop_level": num("stop")}
