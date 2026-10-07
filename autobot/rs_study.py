"""
Risk-managed relative strength: the user's "new strategy from the differences" (2026-10-07).
Diagnosis only: nothing here changes the bot.

    .venv/Scripts/python -m autobot.rs_study            # data/rs_study.json

What worked in sections 1-22: long only, slow decisions, comparing assets with each other, sizing by risk.
The rule, fixed before the run (monthly, decided at a month's last close, held through the next month):
  1. momentum: each asset's return over months t-12 .. t-1 (the latest month skipped, the standard measure)
  2. hold the top 3 ...
  3. ... but only those whose own total-return index is above its 10-month average (dual momentum);
     an asset that fails sits in cash
  4. sizing (full version): each holding gets 1/3 x (12% target volatility / its own volatility over the
     last 12 months), never more than 1/3; the rest is cash
Versions reported: plain top 3 (1+2, equal), dual (1+2+3, equal), full (1-4). Benchmarks: the market, an
equal mix of all the assets, and the market with the same volatility sizing (so the gain can be traced).
Costs: 0.1% of the amount moved for the index data (as in the macro study), 2 bps for ETFs.

Data:
  French 10 industry portfolios (value-weighted, monthly, July 1926 to August 2026; Kenneth French's data
  library) with his market return and 1-month T-bill rate. Scored from July 1927. Subperiods include
  1999-2026, after the industry-momentum paper (Moskowitz & Grinblatt 1999), the honest out-of-sample part.
  Sector ETFs (XLK, XLV, XLE, XLF, XLI, XLY, XLP, XLU, XLB, plus XLRE and XLC once they exist) from
  Alpaca's SIP feed, adjusted daily closes, 2016-2026; market = SPY, cash = 3-month bills (FRED TB3MS).
"""
from __future__ import annotations

import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
FR = ROOT / "data" / "french"
OUT = ROOT / "data" / "rs_study.json"
TOP, TREND_M, VOL_TARGET = 3, 10, 0.12
SECTORS = ["XLK", "XLV", "XLE", "XLF", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]
PERIODS = {"1927-1949": ("1927-07", "1949-12"), "1950-1998": ("1950-01", "1998-12"), "1999-2026 (after publication)": ("1999-01", "2026-12")}


def _french_block(path: Path, title: str) -> pd.DataFrame:
    lines = path.read_text(encoding="latin-1").splitlines()
    i = next(i for i, l in enumerate(lines) if title in l)
    start = i if lines[i].lstrip().startswith(",") else i + 1        # the title line may itself be the header
    rows = []
    for l in lines[start:]:
        if not l.strip():
            break
        rows.append(l)
    df = pd.read_csv(io.StringIO("\n".join(rows)), index_col=0)
    df.index = pd.PeriodIndex(df.index.astype(str).str.strip(), freq="M")
    df.columns = [c.strip() for c in df.columns]
    return df.astype(float).replace([-99.99, -999], np.nan) / 100


def french() -> Dict[str, pd.DataFrame]:
    ind = _french_block(FR / "10_Industry_Portfolios.csv", "Average Value Weighted Returns -- Monthly")
    fac = _french_block(FR / "F-F_Research_Data_Factors.csv", "Mkt-RF")
    idx = ind.index.intersection(fac.index)
    return {"R": ind.loc[idx], "market": (fac["Mkt-RF"] + fac["RF"]).loc[idx], "cash": fac["RF"].loc[idx]}


def weights(R: pd.DataFrame, version: str) -> pd.DataFrame:
    """Target weights decided at the end of each month t (applied in month t+1)."""
    tri = (1 + R.fillna(0)).cumprod().where(R.notna())
    mom = tri.shift(1) / tri.shift(12) - 1                         # months t-12 .. t-1
    rank = mom.rank(axis=1, ascending=False, method="first")
    top = (rank <= TOP) & mom.notna()
    if version in ("dual", "full"):
        top &= tri > tri.rolling(TREND_M).mean()
    w = top.astype(float) / TOP
    if version == "full":
        vol = R.rolling(12).std() * math.sqrt(12)
        w = (w * (VOL_TARGET / vol)).clip(upper=1 / TOP).where(top, 0.0)
    return w.fillna(0.0)


def market_vol_sized(m: pd.Series) -> pd.Series:
    vol = m.rolling(12).std() * math.sqrt(12)
    return (VOL_TARGET / vol).clip(upper=1.0)


def replay(R: pd.DataFrame, W: pd.DataFrame, cash: pd.Series, cost: float) -> pd.DataFrame:
    w = W.shift(1)
    inv = w.sum(axis=1)
    turn = w.diff().abs().sum(axis=1)
    ret = (w * R.fillna(0)).sum(axis=1) + (1 - inv) * cash - turn * cost
    return pd.DataFrame({"ret": ret, "invested": inv}).dropna()


def stats(r: pd.Series, cash: pd.Series, invested: pd.Series = None) -> Dict:
    eq = (1 + r).cumprod()
    yrs = len(r) / 12
    ex = r - cash.reindex(r.index)
    return {"from": str(r.index[0]), "months": int(len(r)), "yearly_%": round(float((eq.iloc[-1] ** (1 / yrs) - 1) * 100), 2),
            "volatility_%": round(float(r.std() * math.sqrt(12) * 100), 1),
            "worst_drop_%": round(float((eq / eq.cummax() - 1).min() * 100), 1),
            "sharpe": round(float(ex.mean() / ex.std() * math.sqrt(12)), 2),
            "avg_invested_%": round(float(invested.mean() * 100), 1) if invested is not None else 100.0}


def diff(a: pd.Series, b: pd.Series) -> Dict:
    d = (a - b.reindex(a.index)).dropna()
    mu, se = d.mean(), d.std(ddof=1) / math.sqrt(len(d))
    return {"%_per_year": round(mu * 1200, 2), "ci95": [round((mu - 1.96 * se) * 1200, 2), round((mu + 1.96 * se) * 1200, 2)],
            "significant": bool(mu - 1.96 * se > 0 or mu + 1.96 * se < 0)}


def same_vol(r: pd.Series, ref: pd.Series, cash: pd.Series) -> pd.Series:
    """r's excess return scaled to ref's volatility (a fair comparison of risk-adjusted results)."""
    k = ref.std() / r.std()
    c = cash.reindex(r.index)
    return c + (r - c) * k


def evaluate(R: pd.DataFrame, market: pd.Series, cash: pd.Series, cost: float, start: str, periods: Dict) -> Dict:
    res = {"assets": list(R.columns), "series": {}}
    series = {}
    for v in ("plain", "dual", "full"):
        f = replay(R, weights(R, v), cash, cost)
        series[v] = f
    m = pd.DataFrame({"ret": market, "invested": 1.0})
    eqw = replay(R, pd.DataFrame(1 / R.shape[1], index=R.index, columns=R.columns).where(R.notna(), 0.0), cash, cost)
    mv = market_vol_sized(market)
    mvs = replay(pd.DataFrame({"m": market}), pd.DataFrame({"m": mv}), cash, cost)
    series.update({"market": m, "equal mix": eqw, "market, vol-sized": mvs})
    series = {k: f[f.index >= pd.Period(start, "M")] for k, f in series.items()}
    common = series["full"].index
    for k in series:
        series[k] = series[k].reindex(common).dropna()
    res["whole"] = {k: stats(f.ret, cash, f.invested) for k, f in series.items()}
    mk = series["market"].ret
    res["vs_market"] = {k: diff(series[k].ret, mk) for k in ("plain", "dual", "full", "equal mix", "market, vol-sized")}
    res["vs_market_same_vol"] = {k: diff(same_vol(series[k].ret, mk, cash), mk) for k in ("plain", "dual", "full", "market, vol-sized")}
    res["full_vs_vol_sized_market_same_vol"] = diff(same_vol(series["full"].ret, series["market, vol-sized"].ret, cash), series["market, vol-sized"].ret)
    res["periods"] = {}
    for name, (a, b) in periods.items():
        sl = {k: f[(f.index >= pd.Period(a, "M")) & (f.index <= pd.Period(b, "M"))] for k, f in series.items()}
        if len(sl["full"]) < 24:
            continue
        res["periods"][name] = {k: stats(f.ret, cash, f.invested) for k, f in sl.items()}
        res["periods"][name]["full_vs_market"] = diff(sl["full"].ret, sl["market"].ret)
        res["periods"][name]["full_vs_market_same_vol"] = diff(same_vol(sl["full"].ret, sl["market"].ret, cash), sl["market"].ret)
    return res


def etfs() -> Dict:
    from autobot.data import _download
    cache = ROOT / "data" / "rs_sector_daily.pkl.gz"
    if cache.exists():
        px = pd.read_pickle(cache)
    else:
        cols = {}
        for s in SECTORS + ["SPY"]:
            try:
                cols[s] = _download(s, "1Day", datetime(2015, 1, 1, tzinfo=timezone.utc), None, "sip").close
            except RuntimeError:
                pass
        px = pd.DataFrame(cols)
        px.to_pickle(cache)
    px.index = px.index.tz_convert("America/New_York").tz_localize(None)
    m = px.groupby(px.index.to_period("M")).last()
    R = m.pct_change().iloc[1:]
    tb = pd.read_csv(ROOT / "data" / "macro" / "TB3MS.csv")
    tbs = pd.to_numeric(tb.iloc[:, 1], errors="coerce")
    tbs.index = pd.PeriodIndex(pd.to_datetime(tb.iloc[:, 0]), freq="M")
    cash = (tbs / 1200).reindex(R.index).ffill()
    return {"R": R[[c for c in SECTORS if c in R]], "market": R["SPY"], "cash": cash}


def run(progress=print) -> Dict:
    f = french()
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "rule": {"top": TOP, "trend_months": TREND_M, "vol_target": VOL_TARGET}}
    res["french"] = evaluate(f["R"], f["market"], f["cash"], 0.001, "1927-07", PERIODS)
    progress("French industries done")
    e = etfs()
    res["etf"] = evaluate(e["R"], e["market"], e["cash"], 0.0002, "2017-01", {})
    progress("sector ETFs done")
    res["lines"] = lines(res)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def _row(k: str, s: Dict) -> str:
    return (f"    {k:<18} {s['yearly_%']:+6.2f}%/yr  vol {s['volatility_%']:5.1f}%  worst drop {s['worst_drop_%']:6.1f}%  "
            f"Sharpe {s['sharpe']:.2f}  invested {s['avg_invested_%']}%")


def lines(res: Dict) -> List[str]:
    out = []
    for key, title in (("french", "French 10 industries"), ("etf", "Sector ETFs")):
        r = res[key]
        out.append(f"{title}, from {r['whole']['full']['from']} ({r['whole']['full']['months']} months):")
        out += [_row(k, s) for k, s in r["whole"].items()]
        for k, d in r["vs_market"].items():
            out.append(f"    {k} vs market: {d['%_per_year']:+.2f}%/yr ({d['ci95'][0]:+.2f} to {d['ci95'][1]:+.2f}); at the market's volatility: "
                       f"{r['vs_market_same_vol'].get(k, {}).get('%_per_year', 'n/a')}")
        d = r["full_vs_vol_sized_market_same_vol"]
        out.append(f"    full vs the vol-sized market, same volatility: {d['%_per_year']:+.2f}%/yr ({d['ci95'][0]:+.2f} to {d['ci95'][1]:+.2f})")
        for name, p in r.get("periods", {}).items():
            out.append(f"  {name}: full {p['full']['yearly_%']:+.2f}%/yr (drop {p['full']['worst_drop_%']}%) vs market {p['market']['yearly_%']:+.2f}% "
                       f"(drop {p['market']['worst_drop_%']}%); same vol: {p['full_vs_market_same_vol']['%_per_year']:+.2f}%/yr "
                       f"({p['full_vs_market_same_vol']['ci95'][0]:+.2f} to {p['full_vs_market_same_vol']['ci95'][1]:+.2f})")
    return out


def main():
    res = run()
    print("\n".join(res["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
