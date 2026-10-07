"""
Pairs trading with cointegration (VECM / Johansen), 5-minute bars, flat before the close.
Diagnosis only: nothing here changes the bot.

    .venv-ml/Scripts/python -m autobot.pairs_study        # data/ml/study_pairs.json

Idea (Gatev, Goetzmann & Rouwenhorst 2006; Engle & Granger 1987; Johansen 1991): two prices that
share a long-run relation drift apart and come back. Buy the cheap one, sell the dear one short, and
close when the gap closes. Long and short together cancel most of the market's general move.

Walk-forward, the same windows as the other studies (formation = the 60 sessions before each test
window of 20 sessions, sessions 121 on = the same 386 unseen sessions):
  1. formation, for each of the 55 pairs of our 11 symbols, on 5-minute log prices:
     Johansen trace test (constant, 1 lag in differences): cointegrated at 1% (strict: fewer false pairs)? The cointegrating
     vector gives the hedge: spread = log A - beta x log B.
     Half-life of the spread (AR(1) on the 5-minute spread) between 30 minutes and 2 days.
     Pairs that pass both are traded in the next 20 sessions with that beta; others are not.
  2. trading, per bar (decided at the bar's close, filled at that close, like the other studies):
     z = (spread - its average over the last 78 bars) / its standard deviation over the same bars
     enter when |z| >= Z_IN (short the spread if z > 0, buy it if z < 0), no new entries after 15:20,
     exit when z crosses 0, stop when |z| >= Z_STOP, and always flat at the 15:50 bar's close.
     Money: $1 in A against $beta in B (signs by side); results per dollar of both legs together.
     Costs 2 bps per side on each leg's turnover (twice in the close-out bar). Borrowing costs for
     the short leg are left out (small for these large stocks and ETFs).
Z_IN in {1.5, 2.0, 2.5} (3 settings tried: one could look good by luck), Z_STOP 4.
Scored per day against cash (a pair that is market-neutral should beat 0), per pair and overall.
"""
from __future__ import annotations

import itertools
import json
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import cost_study as cs
from . import ml_features as mf
from . import recalibrate as rc
from .short_tp_study import _paired, cached_symbols

NY = mf.NY
OUT = mf.OUT_DIR / "study_pairs.json"
FORM, TEST, FIRST = 60, 20, 120
Z_INS, Z_STOP, Z_WIN = (1.5, 2.0, 2.5), 4.0, 78
HL_MIN, HL_MAX = 6, 156
LAST_ENTRY_MIN, CLOSEOUT_MIN = 920, 950          # 15:20 and the 15:50 bar


def johansen(la: np.ndarray, lb: np.ndarray) -> Optional[Dict]:
    """Johansen trace test on two log-price series; the cointegrating vector as A - beta x B."""
    from statsmodels.tsa.vector_ar.vecm import coint_johansen
    try:
        j = coint_johansen(np.c_[la, lb], det_order=0, k_ar_diff=1)
    except Exception:  # noqa: BLE001
        return None
    v = j.evec[:, 0]
    if abs(v[0]) < 1e-12:
        return None
    beta = -v[1] / v[0]
    return {"trace": float(j.lr1[0]), "crit1": float(j.cvt[0, 2]), "coint": bool(j.lr1[0] > j.cvt[0, 2]), "beta": float(beta)}


def half_life(spread: np.ndarray) -> float:
    """Bars for a gap to halve, from an AR(1) on the spread (inf when it doesn't revert)."""
    s = spread - spread.mean()
    x, y = s[:-1], s[1:]
    phi = float(np.dot(x, y) / np.dot(x, x)) if np.dot(x, x) > 0 else 1.0
    if phi <= 0:
        return 0.0                                                # gaps vanish at once (noise)
    return math.log(0.5) / math.log(phi) if phi < 1 else math.inf


def trade(spread: np.ndarray, minute: np.ndarray, day: np.ndarray, z_in: float) -> np.ndarray:
    """Position in the spread per bar (+1 = long A / short B), decided at each bar's close."""
    n = len(spread)
    s = pd.Series(spread)
    m = s.rolling(Z_WIN, min_periods=Z_WIN).mean().to_numpy()
    sd = s.rolling(Z_WIN, min_periods=Z_WIN).std().to_numpy()
    z = (spread - m) / np.where(sd > 0, sd, np.nan)
    pos = np.zeros(n)
    cur = 0.0
    for i in range(n):
        if i > 0 and day[i] != day[i - 1]:
            cur = 0.0
        if minute[i] >= CLOSEOUT_MIN or not np.isfinite(z[i]):
            cur = 0.0
        elif cur != 0 and (abs(z[i]) >= Z_STOP or np.sign(z[i]) != -np.sign(cur)):
            cur = 0.0                                             # stopped out, or the gap closed (z crossed 0)
        elif cur == 0 and minute[i] < LAST_ENTRY_MIN and z_in <= abs(z[i]) < Z_STOP:
            cur = -np.sign(z[i])                                  # z high: A dear -> short the spread
        pos[i] = cur
    return pos


def pnl(pos: np.ndarray, ra: np.ndarray, rb: np.ndarray, beta: float, side_cost: float, minute: np.ndarray) -> np.ndarray:
    """Per-bar result per dollar of both legs: position decided at bar i-1's close earns bar i."""
    g = 1 + abs(beta)
    held = np.r_[0.0, pos[:-1]]
    ret = held * (ra - beta * rb) / g
    turn = np.abs(np.diff(np.r_[0.0, pos]))                       # change at bar i's close, both legs
    mult = np.where(minute >= CLOSEOUT_MIN, 2.0, 1.0)
    return ret - turn * side_cost * mult                           # cost booked at the bar whose close traded


def run(cfg: Dict, symbols: Optional[List[str]] = None, progress=print) -> Dict:
    syms = symbols or cached_symbols()
    data = cs.Data(syms)
    r = rc.settings(cfg)
    closes = pd.DataFrame({s: data.df5[s].close for s in syms})
    d0 = np.asarray(closes.index.tz_convert(NY).date)
    closes = closes.groupby(d0).ffill().dropna()                   # a bar without trades keeps its last price
    ny = closes.index.tz_convert(NY)
    day = np.asarray(ny.date)
    minute = np.asarray(ny.hour * 60 + ny.minute)
    days = sorted(set(day))
    logp = np.log(closes)
    rets = closes.pct_change().fillna(0.0)
    rets[np.r_[True, day[1:] != day[:-1]]] = 0.0                  # no overnight: flat before every close
    side = {s: rc.costs_for(s, r).per_turn for s in syms}
    pairs = list(itertools.combinations(syms, 2))
    windows = [(k, min(k + TEST, len(days))) for k in range(FIRST, len(days), TEST)]
    test_days = [d for k, e in windows for d in days[k:e]]
    daily = {z: {f"{a}-{b}": pd.Series(0.0, index=pd.Index(test_days)) for a, b in pairs} for z in Z_INS}
    selected, trades = [], {z: 0 for z in Z_INS}
    for k, e in windows:
        f_mask = np.isin(day, days[k - FORM:k])
        t_mask = np.isin(day, days[k:e])
        for a, b in pairs:
            la, lb = logp[a].to_numpy(), logp[b].to_numpy()
            jo = johansen(la[f_mask], lb[f_mask])
            if not jo or not jo["coint"]:
                continue
            spread_f = la[f_mask] - jo["beta"] * lb[f_mask]
            hl = half_life(spread_f)
            if not HL_MIN <= hl <= HL_MAX:
                continue
            selected.append({"window": str(days[k]), "pair": f"{a}-{b}", "beta": round(jo["beta"], 3), "half_life_bars": round(hl, 1),
                             "trace": round(jo["trace"], 1), "crit1": round(jo["crit1"], 1)})
            # z uses the spread with the formation beta, from bars up to each close (history before the window included)
            idx = np.flatnonzero(t_mask)
            lo = max(0, idx[0] - Z_WIN)
            sl = slice(lo, idx[-1] + 1)
            spread = la[sl] - jo["beta"] * lb[sl]
            sub_day, sub_min = day[sl], minute[sl]
            in_test = t_mask[sl]
            for z in Z_INS:
                pos = trade(spread, sub_min, sub_day, z)
                pos[~in_test] = 0.0
                cost = (side[a] + side[b] * abs(jo["beta"])) / (1 + abs(jo["beta"]))
                pr = pnl(pos, rets[a].to_numpy()[sl], rets[b].to_numpy()[sl], jo["beta"], cost, sub_min)
                d = pd.Series(pr[in_test], index=sub_day[in_test]).groupby(level=0).apply(lambda x: float(np.prod(1 + x) - 1))
                daily[z][f"{a}-{b}"].loc[d.index] = d.to_numpy()
                trades[z] += int(((np.diff(np.r_[0.0, pos[in_test]]) != 0) & (pos[in_test] != 0)).sum())
        progress(f"window from {days[k]}: {sum(1 for s in selected if s['window'] == str(days[k]))} pairs selected")

    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": str(data.as_of), "symbols": syms,
           "pairs_tested": len(pairs), "test_sessions": len(test_days), "first_test_day": str(test_days[0]),
           "rules": {"formation_sessions": FORM, "test_sessions": TEST, "z_in": Z_INS, "z_stop": Z_STOP, "z_window_bars": Z_WIN,
                     "half_life_bars": [HL_MIN, HL_MAX]},
           "selections": len(selected), "windows": len(windows),
           "selected_most": pd.Series([s["pair"] for s in selected]).value_counts().head(10).to_dict() if selected else {},
           "by_z": {}}
    for z in Z_INS:
        used = [p for p in daily[z] if any(s["pair"] == p for s in selected)]
        D = pd.DataFrame({p: daily[z][p] for p in used}) if used else pd.DataFrame(index=test_days)
        acc = D.mean(axis=1) if used else pd.Series(0.0, index=test_days)       # equal weight over every pair ever selected
        acc.index = pd.to_datetime(acc.index)
        per = {p: _paired(D[p], D[p] * 0) for p in used}
        res["by_z"][str(z)] = {"total_%": round(float(((1 + acc).prod() - 1) * 100), 2), "vs_cash": _paired(acc, acc * 0),
                               "trades": trades[z],
                               "pairs_clearly_positive": sum(v["significant"] and v["mean_bps"] > 0 for v in per.values()),
                               "pairs_clearly_negative": sum(v["significant"] and v["mean_bps"] < 0 for v in per.values()),
                               "pairs_used": len(used),
                               "per_pair_total_%": {p: round(float(((1 + D[p]).prod() - 1) * 100), 2) for p in used}}
    res["lines"] = lines(res)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    (mf.OUT_DIR / "pairs_selected.json").write_text(json.dumps(selected, indent=1), encoding="utf-8")
    return res


def lines(res: Dict) -> List[str]:
    out = [f"Pairs trading (Johansen cointegration, 5-minute, flat before the close), {res['pairs_tested']} pairs, "
           f"{res['test_sessions']} unseen sessions from {res['first_test_day']}, after costs on both legs.",
           f"  {res['selections']} pair-windows passed the tests over {res['windows']} windows; most often: " +
           ", ".join(f"{k} {v}x" for k, v in res["selected_most"].items())]
    for z, s in res["by_z"].items():
        p = s["vs_cash"]
        rng = f"({p['ci95_bps'][0]:+.2f} to {p['ci95_bps'][1]:+.2f})" if p.get("mean_bps") is not None else ""
        mean = f"{p['mean_bps']:+.2f}" if p.get("mean_bps") is not None else "n/a"
        out.append(f"  enter at |z| >= {z}: total {s['total_%']:+.2f}%, {mean} bps/day {rng}, {s['trades']} trades, "
                   f"pairs clearly positive / negative {s['pairs_clearly_positive']} / {s['pairs_clearly_negative']} of {s['pairs_used']}")
    return out


def main():
    import yaml
    cfg = yaml.safe_load((mf.ROOT / "config.yaml").read_text(encoding="utf-8"))
    res = run(cfg)
    print("\n".join(res["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
