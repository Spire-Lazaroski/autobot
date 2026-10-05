"""
Daily long-only study: would the strategies, run once a day on daily bars and holding overnight,
do better than simply holding the same stocks? Diagnosis only: nothing here changes config.yaml,
the bot or recalibration.

    python -m autobot.daily_study              # writes data/daily_study.json (well under a minute)
    python -m autobot.daily_study --summary    # only reprint the saved summary

Data: Alpaca's free IEX daily bars since mid-2020 for the universe stocks/ETFs (today's unfinished
bar dropped). The first WARMUP bars only warm the indicators up; everything is measured from
there ("test period", mid-2021 on, which includes the 2022 fall).

Fills, like a bot that runs after the close: the signal from day t's close is traded at day t+1's
OPEN. A position held overnight earns the gap (previous close -> open), the position after the
open earns open -> close. Each trade at the open costs 2x the asset class's normal cost per side
(the open is the most expensive moment to trade); every row is also shown at double that.

Variants, long only (a short signal counts as flat):
  buy and hold       always in (the benchmark)
  above 200-day avg  in while the previous close is above its 200-day average (a classic reference,
                     not a registered strategy)
  <strategy> default each daily-capable strategy at its default settings
  <strategy> walk-forward  in each window the grid setting that made the most on the previous TRAIN
                     days is used on the next TEST days (out of sample)
  best of all (walk-forward)  every strategy x setting AND buy and hold compete on the train days;
                     the winner is used on the test days
Portfolio = equal weight over the symbols, rebalanced daily. Compared with buy and hold on the
same days: return, yearly growth, worst drop, Sharpe, time in the market, and the daily difference
with a 95% range.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from . import cost_study as cs
from . import recalibrate as rc
from .backtest import param_combos
from .data import fetch_bars
from .strategies import STRATEGIES, get_signal

NY = "America/New_York"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "daily_study.json"
DAYS = 2600          # calendar days requested (the IEX history starts mid-2020)
WARMUP = 252         # bars before the test period
TRAIN, TEST = 252, 63
OPEN_MULT = 2.0      # trades at the open cost this many times the normal cost per side
BH, TREND = "buy and hold", "above 200-day avg"


def load(sym: str, today=None) -> pd.DataFrame:
    df = fetch_bars([sym], "1Day", DAYS)[sym]
    today = today or pd.Timestamp.now(tz=NY).date()
    df = df[np.asarray(df.index.tz_convert(NY).date) < today]  # the unfinished day is never used
    df.attrs["symbol"] = sym
    return df


def simulate(df: pd.DataFrame, target: pd.Series, cost_side: float) -> pd.DataFrame:
    """Daily returns of trading `target` (decided at each close, long only) at the next open."""
    o, c = df.open.to_numpy(float), df.close.to_numpy(float)
    T = np.clip(np.nan_to_num(target.reindex(df.index).to_numpy(float)), 0, None)
    pos = np.r_[0.0, T[:-1]]                       # held from day t's open: the target of day t-1
    prev = np.r_[0.0, pos[:-1]]                    # held overnight into day t
    gap = np.r_[0.0, o[1:] / c[:-1] - 1]
    x = c / o - 1
    turn = np.abs(pos - prev)
    ret = (1 + prev * gap) * (1 + pos * x) - 1 - turn * cost_side * OPEN_MULT
    return pd.DataFrame({"ret": ret, "position": pos, "turnover": turn, "target": T}, index=df.index)


def stats(r: pd.Series, pos: Optional[pd.Series] = None, turn: Optional[pd.Series] = None) -> Dict:
    eq = (1 + r).cumprod()
    years = len(r) / 252
    total = float(eq.iloc[-1] - 1) if len(r) else 0.0
    sd = r.std()
    out = {"return_%": round(total * 100, 1),
           "yearly_%": round(((1 + total) ** (1 / years) - 1) * 100, 1) if years > 0 and total > -1 else None,
           "worst_drop_%": round(float((eq / eq.cummax() - 1).min() * 100), 1),
           "sharpe": round(float(r.mean() / sd * math.sqrt(252)), 2) if sd > 0 else 0.0}
    if pos is not None:
        out["in_market_%"] = round(float(pos.mean() * 100), 1)
    if turn is not None:
        out["trades_per_year"] = round(float((turn > 0).sum() / 2 / years), 1) if years else 0.0
    return out


def paired(a: pd.Series, b: pd.Series) -> Dict:
    d = (a - b).dropna()
    n = len(d)
    mu, se = d.mean(), d.std(ddof=1) / math.sqrt(n)
    return {"mean_bps": round(mu * 1e4, 2), "ci95_bps": [round((mu - 1.96 * se) * 1e4, 2), round((mu + 1.96 * se) * 1e4, 2)],
            "significant": bool(mu - 1.96 * se > 0 or mu + 1.96 * se < 0)}


def by_year(r: pd.Series) -> Dict[str, float]:
    y = (1 + r).groupby(r.index.tz_convert(NY).year).prod() - 1
    return {str(k): round(float(v * 100), 1) for k, v in y.items()}


# ----------------------------------------------------------------- one symbol
def symbol_runs(sym: str, df: pd.DataFrame, cfg: Dict, cost_mult: float = 1.0) -> Dict[str, pd.DataFrame]:
    """Every variant's daily frame (ret / position / turnover) on the symbol's full history."""
    r = rc.settings(cfg)
    side = rc.costs_for(sym, r).per_turn * cost_mult
    runs: Dict[str, pd.DataFrame] = {BH: simulate(df, pd.Series(1.0, index=df.index), side)}
    sma = df.close.rolling(200).mean()
    runs[TREND] = simulate(df, (df.close > sma).astype(float).where(sma.notna(), 0.0), side)
    cand: Dict[str, pd.DataFrame] = {BH: runs[BH]}
    names = cs.strategies_for("1Day", r)
    for name in names:
        runs[f"{name} default"] = simulate(df, get_signal(name, df, STRATEGIES[name].defaults, False), side)
        per = {}
        for p in param_combos(name):
            key = f"{name} {json.dumps(p, sort_keys=True)}"
            per[key] = simulate(df, get_signal(name, df, p, False), side)
        cand.update(per)
        runs[f"{name} walk-forward"] = _walk(per, df, side)
    runs["best of all (walk-forward)"] = _walk(cand, df, side)
    runs["best strategy (walk-forward, no buy and hold)"] = _walk({k: v for k, v in cand.items() if k != BH}, df, side)
    return runs


def _walk(cands: Dict[str, pd.DataFrame], df: pd.DataFrame, side: float) -> pd.DataFrame:
    """In each TEST window starting at day a, the candidate with the best compounded return on
    the TRAIN days up to a-1 (all known at day a-1's close) supplies the targets from that close
    on; the stitched targets are replayed, so a switch between candidates pays its trades."""
    idx = df.index
    R = pd.DataFrame({k: v.ret for k, v in cands.items()})
    T = pd.DataFrame({k: v.target for k, v in cands.items()})
    target = pd.Series(0.0, index=idx)
    chosen = pd.Series("", index=idx, dtype=object)
    for a in range(WARMUP, len(idx), TEST):
        best = ((1 + R.iloc[max(0, a - TRAIN):a]).prod() - 1).idxmax()
        target.iloc[a - 1:a - 1 + TEST] = T[best].iloc[a - 1:a - 1 + TEST]
        chosen.iloc[a:a + TEST] = best
    out = simulate(df, target, side)
    out.attrs["chosen"] = chosen
    return out


# ----------------------------------------------------------------- portfolio
def run(cfg: Dict, symbols: Optional[Sequence[str]] = None, data: Optional[Dict[str, pd.DataFrame]] = None) -> Dict:
    syms = list(symbols or cs.universe_symbols())
    data = data or {s: load(s) for s in syms}
    syms = [s for s in syms if s in data and len(data[s]) > WARMUP + TEST]
    start = max(data[s].index[WARMUP] for s in syms)
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "symbols": syms,
           "first_bar": str(min(data[s].index[0] for s in syms).date()), "test_from": str(start.tz_convert(NY).date()),
           "test_to": str(max(data[s].index[-1] for s in syms).tz_convert(NY).date()),
           "settings": {"warmup": WARMUP, "train": TRAIN, "test": TEST, "open_cost_mult": OPEN_MULT}}
    for label, mult in (("normal costs", 1.0), ("double costs", 2.0)):
        runs = {s: symbol_runs(s, data[s], cfg, mult) for s in syms}
        variants = list(runs[syms[0]])
        acc = {}
        for v in variants:
            R = pd.DataFrame({s: runs[s][v].ret for s in syms}).sort_index()
            R = R[R.index >= start].fillna(0.0)
            P = pd.DataFrame({s: runs[s][v].position for s in syms}).reindex(R.index).fillna(0.0)
            U = pd.DataFrame({s: runs[s][v].turnover for s in syms}).reindex(R.index).fillna(0.0)
            acc[v] = (R.mean(axis=1), P.mean(axis=1), U)
        bh = acc[BH][0]
        rows = []
        for v, (r, p, u) in acc.items():
            years = len(r) / 252
            row = {"variant": v, **stats(r, p),
                   "trades_per_year_per_symbol": round(float((u > 0).sum().mean() / 2 / years), 1) if years else 0.0,
                   "by_year": by_year(r)}
            if v != BH:
                row["vs_buy_and_hold"] = paired(r, bh)
            rows.append(row)
        rows.sort(key=lambda x: -x["sharpe"])
        res[label] = {"portfolio": rows}
        if mult == 1.0:
            per = []
            for s in syms:
                one = {}
                for v in (BH, TREND, "best of all (walk-forward)"):
                    f = runs[s][v]
                    f = f[f.index >= start]
                    one[v] = stats(f.ret, f.position, f.turnover)
                ch = runs[s]["best of all (walk-forward)"].attrs["chosen"]
                ch = ch[ch.index >= start]
                picks = ch.groupby((ch != ch.shift()).cumsum()).first()
                one["picks"] = [p.split(" {")[0] for p in picks]
                per.append({"symbol": s, **one})
            res[label]["per_symbol"] = per
    res["summary"] = summary(res)
    return res


def summary(res: Dict) -> List[str]:
    rows = {r["variant"]: r for r in res["normal costs"]["portfolio"]}
    bh = rows[BH]
    L = [f"{len(res['symbols'])} stocks/ETFs, equal weight; test period {res['test_from']} to {res['test_to']} "
         f"(daily bars from {res['first_bar']}); trade at the next open, open costs 2x normal.",
         f"Buy and hold: {bh['return_%']:+.1f}% ({bh['yearly_%']:+.1f}% a year), worst drop {bh['worst_drop_%']:.1f}%, Sharpe {bh['sharpe']:.2f}."]
    order = [TREND, "best of all (walk-forward)", "best strategy (walk-forward, no buy and hold)"] + \
        [r["variant"] for r in res["normal costs"]["portfolio"] if r["variant"] not in (BH, TREND) and "best" not in r["variant"]]
    for v in order:
        r = rows[v]
        d = r["vs_buy_and_hold"]
        L.append(f"{v}: {r['return_%']:+.1f}% ({r['yearly_%']:+.1f}%/yr), worst drop {r['worst_drop_%']:.1f}%, Sharpe {r['sharpe']:.2f}, "
                 f"in the market {r['in_market_%']:.0f}%, {r['trades_per_year_per_symbol']:.0f} trades/yr per symbol; vs buy and hold "
                 f"{d['mean_bps']:+.2f} bps/day ({d['ci95_bps'][0]:+.2f} to {d['ci95_bps'][1]:+.2f}){' clearly' if d['significant'] else ''}")
    dbl = {r["variant"]: r for r in res["double costs"]["portfolio"]}
    L.append("At double costs: " + "; ".join(f"{v} {dbl[v]['return_%']:+.1f}%" for v in [BH, TREND, "best of all (walk-forward)"]))
    L.append("By year (buy and hold / above 200-day avg / best of all): " + ", ".join(
        f"{y} {bh['by_year'][y]:+.0f}/{rows[TREND]['by_year'].get(y, 0):+.0f}/{rows['best of all (walk-forward)']['by_year'].get(y, 0):+.0f}%"
        for y in bh["by_year"]))
    return L


def main():
    import argparse
    import sys
    import yaml
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Daily long-only study (diagnosis only).")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    if a.summary:
        print("\n".join(json.loads(OUT.read_text(encoding="utf-8"))["summary"]))
        return
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    res = run(cfg, [s for s in a.symbols.split(",") if s] or None)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print("\n".join(res["summary"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
