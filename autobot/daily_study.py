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
Candidates from docs/STRATEGY_EVIDENCE.md (not registered strategies; defined here only):
  vol target         hold, scaled by (the stock's own past-year volatility / its last 20 days'),
                     at most 1 (no borrowing); changed only when it moves by 0.1 or more
  momentum 12-1      in while the return from 12 months to 1 month ago is positive, checked at
                     each month's last close (Moskowitz, Ooi & Pedersen 2012)
  turn of month      in from the last trading day of a month to the 3rd of the next (calendar
                     known in advance)
  relative strength  at each month's last close, the TOP_N symbols with the best 12-1 return,
                     equal weight; "+ trend" also needs its own 12-1 return above zero, else cash
  news rules         daily versions of the news guard, only for symbols with scored news (from
                     Oct 2024): "earnings out" = flat the day before and the day of an earnings
                     reaction; "bad news out" = flat the day after a materiality-4+ story with
                     negative sentiment arrived (up to that day's close). On buy and hold and on
                     momentum, each compared with its own base on the same symbols and days.
                     (The bot's news_guard itself never triggers on daily bars: its window is
                     30 minutes and its earnings blackout is intraday only.)
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
TOP_N = 4
NEWS_FROM = pd.Timestamp("2024-10-01", tz="UTC")   # scored news for most covered symbols starts here
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
    for k, t in candidates(df).items():
        runs[k] = simulate(df, t, side)
    out = news_out(df)
    if out is not None:
        mom = get_signal("momentum", df, STRATEGIES["momentum"].defaults, False)
        for rule, mask in out.items():
            runs[f"news: {rule}, on buy and hold"] = simulate(df, pd.Series(1.0, index=df.index).where(~mask, 0.0), side)
            runs[f"news: {rule}, on momentum"] = simulate(df, mom.where(~mask, 0.0), side)
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


def month_ends(idx: pd.DatetimeIndex) -> np.ndarray:
    m = idx.tz_localize(None).to_period("M") if idx.tz is None else idx.tz_convert(NY).tz_localize(None).to_period("M")
    return np.r_[m[1:] != m[:-1], False]   # the last bar in the data is never treated as a month end


def mom_12_1(close: pd.Series) -> pd.Series:
    return close.shift(21) / close.shift(252) - 1


def monthly(x: pd.Series, idx: pd.DatetimeIndex) -> pd.Series:
    """A value decided at each month's last close and kept until the next month end."""
    return x.where(month_ends(idx)).ffill().fillna(0.0)


def news_out(df: pd.DataFrame) -> Optional[Dict[str, pd.Series]]:
    """Targets to zero (at day t's close, i.e. out on day t+1), or None without scored news."""
    from . import news_guard as ng
    sym = df.attrs.get("symbol", "")
    st = ng.stories(sym)
    if st.empty:
        return None
    idx = df.index
    d = np.asarray(idx.tz_convert(NY).date)
    close_t = pd.DatetimeIndex([pd.Timestamp(x).tz_localize(NY) + pd.Timedelta(hours=16) for x in d]).tz_convert("UTC")
    bad = st[(st.materiality >= 4) & (st.sentiment < 0)].t_info.dt.tz_convert("UTC").sort_values()
    hi = np.searchsorted(bad.values, close_t.values, side="right")
    lo = np.r_[0, hi[:-1]]
    bad_day = pd.Series(hi > lo, index=idx)                      # a story since the previous close
    held_out = np.zeros(len(idx), bool)                           # days to be flat on
    sess = list(d)
    for k_day in ng.earnings_sessions(sym):
        k = int(np.searchsorted(np.asarray(sess), k_day))
        for j in (k - 1, k):
            if 0 <= j < len(sess):
                held_out[j] = True
    earn = pd.Series(np.r_[held_out[1:], False], index=idx)       # flat on day t+1 -> target 0 at t
    out = {"bad news out": bad_day}
    if held_out.any():
        out["earnings out"] = earn
        out["earnings + bad news out"] = earn | bad_day
    return out


def candidates(df: pd.DataFrame) -> Dict[str, pd.Series]:
    """Per-symbol candidate targets (decided at each close, from data up to that close)."""
    idx, c = df.index, df.close
    r = c.pct_change()
    long_vol, short_vol = r.rolling(252).std(), r.rolling(20).std()
    raw = (long_vol / short_vol).clip(upper=1.0).where(long_vol.notna(), 0.0).fillna(0.0).to_numpy()
    vt = np.zeros(len(raw))
    for i in range(len(raw)):  # a 0.1 band, so the position isn't nudged every day
        vt[i] = raw[i] if i == 0 or abs(raw[i] - vt[i - 1]) >= 0.1 else vt[i - 1]
    vt = pd.Series(vt, index=idx)
    mom = monthly((mom_12_1(c) > 0).astype(float).where(mom_12_1(c).notna(), 0.0), idx)
    # turn of month: held on the last trading day of a month and the first 3 of the next; the
    # target set at day t's close is what is held on day t+1
    m = idx.tz_convert(NY).tz_localize(None).to_period("M")
    first = np.r_[True, m[1:] != m[:-1]]
    day_no = pd.Series(1, index=idx).groupby(np.cumsum(first)).cumsum().to_numpy()
    last = np.r_[m[1:] != m[:-1], False]
    held = (last | (day_no <= 3)).astype(float)
    tom = pd.Series(np.r_[held[1:], 0.0], index=idx)
    return {"vol target (on hold)": vt, "momentum 12-1": mom, "vol target + momentum 12-1": vt * mom,
            "turn of month": tom}


def relative_strength(data: Dict[str, pd.DataFrame], cfg: Dict, cost_mult: float, trend: bool) -> Dict[str, pd.DataFrame]:
    """Top TOP_N symbols by 12-1 return at each month end, as per-symbol frames whose returns
    are scaled so that the plain average over all symbols is the equal-weight top-N portfolio."""
    r = rc.settings(cfg)
    syms = list(data)
    score = pd.DataFrame({s: mom_12_1(data[s].close) for s in syms})
    idx = score.index
    ends = month_ends(idx)
    pick = pd.DataFrame(0.0, index=idx, columns=syms)
    for i in np.flatnonzero(ends):
        row = score.iloc[i].dropna()
        if trend:
            row = row[row > 0]
        pick.iloc[i, [syms.index(s) for s in row.nlargest(TOP_N).index]] = 1.0
    pick = pick.where(pd.Series(ends, index=idx), np.nan).ffill().fillna(0.0)
    k = len(syms) / TOP_N
    out = {}
    for s in syms:
        f = simulate(data[s], pick[s].reindex(data[s].index).fillna(0.0), rc.costs_for(s, r).per_turn * cost_mult)
        f[["ret", "position"]] *= k
        out[s] = f
    return out


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
        for name, trend in ((f"relative strength top {TOP_N}", False), (f"relative strength top {TOP_N} + trend", True)):
            for s, f in relative_strength({s: data[s] for s in syms}, cfg, mult, trend).items():
                runs[s][name] = f
        variants = list(dict.fromkeys(v for s in syms for v in runs[s]))
        acc = {}
        for v in variants:
            R = pd.DataFrame({s: runs[s][v].ret for s in syms if v in runs[s]}).sort_index()
            R = R[R.index >= start].fillna(0.0)
            if v.startswith("news:"):  # only where the news data exists
                R = R[R.index >= NEWS_FROM]
            P = pd.DataFrame({s: runs[s][v].position for s in syms if v in runs[s]}).reindex(R.index).fillna(0.0)
            U = pd.DataFrame({s: runs[s][v].turnover for s in syms if v in runs[s]}).reindex(R.index).fillna(0.0)
            acc[v] = (R.mean(axis=1), P.mean(axis=1), U, R.shape[1])
        bh = acc[BH][0]
        rows = []
        for v, (r, p, u, nsym) in acc.items():
            years = len(r) / 252
            row = {"variant": v, **stats(r, p),
                   "trades_per_year_per_symbol": round(float((u > 0).sum().mean() / 2 / years), 1) if years else 0.0,
                   "by_year": by_year(r)}
            if v != BH:
                row["vs_buy_and_hold"] = paired(r, bh.reindex(r.index))
            if v.startswith("news:"):
                bname = BH if v.endswith("buy and hold") else "momentum default"
                have = [s for s in syms if v in runs[s]]
                base = pd.DataFrame({s: runs[s][bname].ret for s in have}).reindex(r.index).fillna(0.0).mean(axis=1)
                row["vs_own_base"] = paired(r, base)
                row["base_return_%"] = round(float(((1 + base).prod() - 1) * 100), 1)
                row["symbols"] = nsym
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
