"""
Long-history study: 150 years of the US stock market, monthly. How deep do buy-and-hold's drops
really go, do slow trend filters avoid them, and do early-warning signals (FRED, and the Global
Macro Database's annual fundamentals) help? Diagnosis only: nothing here changes the bot.

    python -m autobot.macro_study              # writes data/macro_study.json (seconds)
    python -m autobot.macro_study --summary

Data (data/macro/, downloaded once; see `download()`):
  Shiller ie_data.xls  S&P composite monthly since 1871: price (the AVERAGE of the month's daily
                       closes), dividends, CPI, 10-year yield. Ends Sept 2023; extended to the last
                       full month with SPY's monthly average close (Alpaca daily bars) scaled to
                       Shiller's level over 2020-2023, dividends at the last known yield.
  FRED                 TB3MS (3-month bill, cash return and yield curve, from 1934), GS10, BAA
                       (corporate bonds, from 1919), SAHMREALTIME (Sahm rule as known at the time,
                       from 1959), USREC (recession months, for context only).
  Global Macro Database (Mueller, Xu, Lehbib & Chen 2025), version 2026_09, USA, annual: real GDP,
                       inflation, unemployment, short and long rates, banking crises. It contains
                       revised figures and projections; year Y is used only from July of Y+1, held
                       for 12 months (as if just published). Revisions still flatter it a little.

Monthly replay: a rule is decided with what is known at the end of month t and held during
month t+1 (stocks earn price + dividends, cash earns the 3-month bill rate, before 1934 GMD's short
rate). A switch costs 0.1% of the amount moved. Caveat: monthly AVERAGE prices smooth the series,
which understates drops and flatters timing rules a little (all rules alike).
The two "acting a month later" rows are that check. Every rule was fixed before the run (no
tuning); with 13 rules tried, about one would look
"clearly" different by luck at the 95% level.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "macro"
OUT = ROOT / "data" / "macro_study.json"
FRED = ["TB3MS", "GS10", "BAA", "SAHMREALTIME", "USREC", "SP500", "VIXCLS"]   # SP500: daily, 10 years (autobot.core --sp500)
GMD_VERSION = "2026_09"
GMD_VARS = ["rGDP", "infl", "unemp", "strate", "ltrate", "BankingCrisis"]
SWITCH_COST = 0.001
CRASHES = {"1929 crash": ("1929-09", "1932-06"), "1937-42": ("1937-03", "1942-04"), "1973-74": ("1973-01", "1974-12"),
           "2000-02": ("2000-08", "2002-10"), "2008-09": ("2007-10", "2009-03"), "2020 covid": ("2020-02", "2020-04"),
           "2022": ("2022-01", "2022-10")}
PERIODS = {"1871-1949": ("1871", "1949"), "1950-1999": ("1950", "1999"), "2000-now": ("2000", "2100")}


# ----------------------------------------------------------------- data
def download():
    """Fetch the raw files into data/macro (free sources; run once)."""
    import urllib.request
    DIR.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve("http://www.econ.yale.edu/~shiller/data/ie_data.xls", DIR / "ie_data.xls")
    for s in FRED:
        urllib.request.urlretrieve(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={s}", DIR / f"{s}.csv")
    import global_macro_data as g
    g.gmd(variables=GMD_VARS, country="USA", version=GMD_VERSION).to_csv(DIR / f"gmd_usa_{GMD_VERSION}.csv", index=False)


def shiller() -> pd.DataFrame:
    x = pd.read_excel(DIR / "ie_data.xls", sheet_name="Data", header=None, skiprows=8, usecols=[0, 1, 2, 4, 6])
    x.columns = ["date", "P", "D", "CPI", "GS10"]
    x = x[pd.to_numeric(x.date, errors="coerce").notna()].copy()
    d = x.date.astype(float)
    year = d.astype(int)
    month = ((d - year) * 100).round().astype(int)       # 1871.1 is October
    x.index = pd.PeriodIndex.from_fields(year=year, month=month, freq="M")
    return x[["P", "D", "CPI", "GS10"]].apply(pd.to_numeric, errors="coerce")


def spy_monthly() -> pd.Series:
    from .data import fetch_bars
    d = fetch_bars(["SPY"], "1Day", 2600)["SPY"].close
    m = d.groupby(d.index.tz_convert("America/New_York").tz_localize(None).to_period("M")).mean()
    return m.iloc[:-1]  # the current month is unfinished


def fred(name: str) -> pd.Series:
    f = pd.read_csv(DIR / f"{name}.csv")
    s = pd.to_numeric(f.iloc[:, 1], errors="coerce")
    s.index = pd.PeriodIndex(pd.to_datetime(f.iloc[:, 0]), freq="M")
    return s.dropna()


def gmd() -> pd.DataFrame:
    g = pd.read_csv(DIR / f"gmd_usa_{GMD_VERSION}.csv")
    g = g.set_index(g.year.astype(int))[GMD_VARS]
    return g


def build(spy: Optional[pd.Series] = None) -> pd.DataFrame:
    """One row per month: stock total return, cash return and every input, aligned."""
    s = shiller()
    spy = spy if spy is not None else spy_monthly()
    over = s.P.dropna().index.intersection(spy.index)
    over = over[over >= pd.Period("2020-08", "M")]
    k = float((s.P.reindex(over) / spy.reindex(over)).median())
    last = s.P.dropna().index.max()
    ext = (spy[spy.index > last] * k).rename("P")
    df = pd.concat([s, ext.to_frame()]).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    yld = (df.D / df.P).ffill()                            # dividends after Shiller's last: last yield
    df["D"] = df.D.fillna(yld * df.P)
    df["ret"] = (df.P + df.D / 12) / df.P.shift(1) - 1
    g = gmd()
    tb = fred("TB3MS")
    short = pd.Series([g.strate.get(p.year, np.nan) for p in df.index], index=df.index)
    df["cash_rate"] = tb.reindex(df.index).fillna(short).ffill()
    df["cash"] = df.cash_rate / 1200
    df["GS10"] = fred("GS10").reindex(df.index).fillna(df.GS10)
    df["BAA"] = fred("BAA").reindex(df.index)
    df["SAHM"] = fred("SAHMREALTIME").reindex(df.index)
    df["USREC"] = fred("USREC").reindex(df.index)
    df.attrs["spy_scale"] = k
    df.attrs["shiller_last"] = str(last)
    return df.dropna(subset=["ret"])


# ----------------------------------------------------------------- rules (1 = stocks, 0 = cash)
def gmd_rules(idx: pd.PeriodIndex) -> Dict[str, pd.Series]:
    """Annual rules: year Y's figures act from July of Y+1 for 12 months (1 = in stocks,
    0 = the warning is on, NaN = not known)."""
    g = gmd()
    warn = {
        "GMD: real GDP fell": (g.rGDP.pct_change() < 0).where(g.rGDP.pct_change().notna()),
        "GMD: inflation above 5%": (g.infl > 5).where(g.infl.notna()),
        "GMD: unemployment up 1 point+": (g.unemp.diff() >= 1).where(g.unemp.diff().notna()),
        "GMD: yield curve inverted (annual)": ((g.ltrate - g.strate) < 0).where((g.ltrate - g.strate).notna()),
        "GMD: banking crisis": (g.BankingCrisis == 1).where(g.BankingCrisis.notna()),
    }
    year = np.array([p.year - 1 if p.month >= 7 else p.year - 2 for p in idx])   # the newest year published by month p
    out = {}
    for name, w in warn.items():
        v = w.reindex(year).to_numpy(dtype=float)
        out[name] = pd.Series(np.where(np.isnan(v), np.nan, 1.0 - v), index=idx)
    return out


def rules(df: pd.DataFrame) -> Dict[str, pd.Series]:
    """Each rule's position at the end of month t (held during t+1); NaN where its data is missing."""
    P, r = df.P, df.ret
    out: Dict[str, pd.Series] = {"buy and hold": pd.Series(1.0, index=df.index)}
    out["10-month average"] = (P > P.rolling(10).mean()).astype(float).where(P.rolling(10).mean().notna())
    tr12 = (1 + r).rolling(12).apply(np.prod, raw=True).shift(1) - 1           # months t-12..t-1
    cash12 = (1 + df.cash).rolling(12).apply(np.prod, raw=True).shift(1) - 1
    out["momentum 12-1"] = (tr12 > cash12).astype(float).where(tr12.notna() & cash12.notna())
    vs, vl = r.rolling(12).std(), r.rolling(120).std()
    out["vol target"] = (vl / vs).clip(upper=1.0).where(vl.notna())
    curve = df.GS10 - df.cash_rate
    out["yield curve inverted: out"] = (curve >= 0).astype(float).where(curve.notna() & (df.index >= pd.Period("1934-01", "M")))
    inv12 = (curve < 0).astype(float).rolling(12).max()
    out["inverted in last 12 months: out"] = (inv12 == 0).astype(float).where(curve.notna() & (df.index >= pd.Period("1935-01", "M")))
    spread = df.BAA - df.GS10
    out["credit spread up 0.5 pt in 6 months: out"] = (spread.diff(6) < 0.5).astype(float).where(spread.diff(6).notna())
    sahm = df.SAHM.shift(1)                                                     # published early the next month
    out["Sahm rule (real time): out"] = (sahm < 0.5).astype(float).where(sahm.notna())
    out.update(gmd_rules(df.index))
    out["10-month average + Sahm"] = (out["10-month average"] * out["Sahm rule (real time): out"])
    # robustness: monthly AVERAGE prices make consecutive months move together, which flatters any
    # rule acting on the latest average; acting one month later removes that effect
    out["10-month average, acting a month later"] = out["10-month average"].shift(1)
    out["momentum 12-1, acting a month later"] = out["momentum 12-1"].shift(1)
    return out


# ----------------------------------------------------------------- replay and statistics
def replay(df: pd.DataFrame, pos: pd.Series) -> pd.DataFrame:
    """Monthly returns of holding `pos` (decided at the end of t-1) during month t."""
    w = pos.shift(1)
    turn = w.diff().abs().fillna(0.0)
    ret = w * df.ret + (1 - w) * df.cash - turn * SWITCH_COST
    return pd.DataFrame({"ret": ret, "w": w}).dropna()


def stats(f: pd.DataFrame, cash: pd.Series) -> Dict:
    r = f.ret
    eq = (1 + r).cumprod()
    years = len(r) / 12
    ex = r - cash.reindex(r.index)
    return {"from": str(r.index[0]), "months": int(len(r)),
            "yearly_%": round(((eq.iloc[-1]) ** (1 / years) - 1) * 100, 2),
            "worst_drop_%": round(float((eq / eq.cummax() - 1).min() * 100), 1),
            "sharpe": round(float(ex.mean() / ex.std() * math.sqrt(12)), 2) if ex.std() > 0 else 0.0,
            "in_stocks_%": round(float(f.w.mean() * 100), 1),
            "switches_per_decade": round(float((f.w.diff().abs() > 0).sum() / years * 10), 1)}


def paired(a: pd.Series, b: pd.Series) -> Dict:
    d = (a - b.reindex(a.index)).dropna()
    mu, se = d.mean(), d.std(ddof=1) / math.sqrt(len(d))
    return {"mean_%_per_year": round(mu * 1200, 2), "ci95": [round((mu - 1.96 * se) * 1200, 2), round((mu + 1.96 * se) * 1200, 2)],
            "significant": bool(mu - 1.96 * se > 0 or mu + 1.96 * se < 0)}


def drop_in(f: pd.DataFrame, a: str, b: str) -> Optional[float]:
    part = f.ret[(f.index >= pd.Period(a, "M")) & (f.index <= pd.Period(b, "M"))]
    if part.empty:
        return None
    eq = (1 + part).cumprod()
    return round(float((eq / np.maximum.accumulate(np.r_[1.0, eq.to_numpy()])[1:] - 1).min() * 100), 1)


def run() -> Dict:
    df = build()
    rs = rules(df)
    frames = {k: replay(df, v) for k, v in rs.items()}
    bh = frames["buy and hold"]
    rows = []
    for k, f in frames.items():
        base = bh[bh.index >= f.index[0]]                    # buy and hold over the rule's own months
        row = {"rule": k, **stats(f, df.cash), "buy_and_hold_same_months": stats(base, df.cash),
               "crashes": {c: {"rule": drop_in(f, *ab), "buy_and_hold": drop_in(bh, *ab)} for c, ab in CRASHES.items()},
               "periods": {p: stats(f[(f.index.year >= int(a)) & (f.index.year <= int(b))], df.cash)
                           for p, (a, b) in PERIODS.items() if ((f.index.year >= int(a)) & (f.index.year <= int(b))).sum() > 24}}
        if k != "buy and hold":
            row["vs_buy_and_hold"] = paired(f.ret, bh.ret)
        rows.append(row)
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "first_month": str(df.index[0]), "last_month": str(df.index[-1]),
           "shiller_last": df.attrs["shiller_last"], "spy_scale": round(df.attrs["spy_scale"], 4),
           "gmd_version": GMD_VERSION, "rules": rows}
    res["summary"] = summary(res)
    return res


def summary(res: Dict) -> List[str]:
    L = [f"US stocks, monthly, {res['first_month']} to {res['last_month']} (Shiller to {res['shiller_last']}, then SPY); "
         "total return, cash = 3-month bills; 0.1% per switch."]
    for r in res["rules"]:
        b = r["buy_and_hold_same_months"]
        line = (f"{r['rule']}: {r['yearly_%']:+.1f}%/yr, worst drop {r['worst_drop_%']:.0f}%, Sharpe {r['sharpe']:.2f}, "
                f"in stocks {r['in_stocks_%']:.0f}% (hold over the same months from {r['from']}: {b['yearly_%']:+.1f}%/yr, "
                f"drop {b['worst_drop_%']:.0f}%, Sharpe {b['sharpe']:.2f})")
        if "vs_buy_and_hold" in r:
            v = r["vs_buy_and_hold"]
            line += f"; difference {v['mean_%_per_year']:+.2f}%/yr ({v['ci95'][0]:+.2f} to {v['ci95'][1]:+.2f}){' clearly' if v['significant'] else ''}"
        L.append(line)
    L.append("Worst drop inside each crash, rule / buy and hold:")
    for r in res["rules"]:
        L.append(f"   {r['rule']}: " + ", ".join(f"{c} {v['rule'] if v['rule'] is not None else '-'}/{v['buy_and_hold']}"
                                              for c, v in r["crashes"].items()))
    return L


def main():
    import argparse
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Long-history US market study (diagnosis only).")
    ap.add_argument("--download", action="store_true", help="fetch the raw data first")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    if a.summary:
        print("\n".join(json.loads(OUT.read_text(encoding="utf-8"))["summary"]))
        return
    if a.download:
        download()
    res = run()
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print("\n".join(res["summary"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
