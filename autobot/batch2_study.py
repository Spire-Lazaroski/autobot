"""
Batch 2 of the user's strategy list (2026-10-07): inflation / interest-rate trading, market sentiment and
insider sentiment. Monthly decisions. Diagnosis only: nothing here changes the bot.

    .venv-ml/Scripts/python -m autobot.batch2_study --part macro long insider    # data/batch2_study.json

Every rule was fixed before the run. Every input is used only once public: daily FRED series from the next
day, monthly CPI from the end of the following month (it comes out mid-month), consumer sentiment from the
end of the following month; then the rule acts on the NEXT month (decided at month-end, held through the
month). Switching costs 2 bps of the amount moved (ETFs) or 0.1% (the long-history index data, as in the
macro study). Each rule is compared with a fixed mix holding the same average amount in stocks
(or the same benchmark), so a rule can't look good just by holding less.

macro  (ETFs, adjusted daily closes from Alpaca's SIP feed, 2016-2026: SPY, TLT, IEF, TIP, GLD, DBC)
  A inflation trend   CPI inflation (year on year) higher than 3 months earlier -> inflation basket
                      (TIP, GLD, DBC, a third each), else 60/40 SPY/TLT;           vs 60/40
  B breakeven trend   5-year expected inflation (T5YIE) above its 6-month average -> inflation basket,
                      else 60/40;                                                  vs 60/40
  C rate trend        10-year yield (DGS10) above its 6-month average (rates rising) -> 60% SPY / 40% IEF
                      (short bonds suffer less), else 60/40 SPY/TLT;               vs 60/40
  D real-yield gold   gold (GLD) when the 10-year real yield (DFII10) is below its 12-month average,
                      else cash;                                                   vs holding GLD
long   (monthly S&P total return and 3-month bills since the 1950s, autobot.macro_study.build)
  L1 inflation        stocks while CPI inflation is not rising (vs 3 months earlier), else bills
  L2 rates            stocks while the 10-year yield is below its 12-month average, else bills
  L3 consumer mood    (contrarian) 100% stocks when Michigan consumer sentiment is below its trailing
                      10-year median, else 50%                                     from 1978
  L4 fear             (contrarian) 100% stocks when the VIX is in the top 20% of its trailing 5 years,
                      else 60%                                                     from 1995
insider (SEC Form 4 data sets, 2016-2026; the bot's 20 universe stocks, ETFs have no insiders)
  open-market purchases (code P) and sales (code S) by officers and directors, summed in dollars per
  stock over the last 3 months (by filing date): buy the stocks whose insiders were net BUYERS, compared
  each month with holding all of them equally; and the stocks with the heaviest net SELLING vs all
"""
from __future__ import annotations

import argparse
import io
import json
import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
MACRO = ROOT / "data" / "macro"
SEC = ROOT / "data" / "sec"
OUT = ROOT / "data" / "batch2_study.json"
ETFS = ["SPY", "TLT", "IEF", "TIP", "GLD", "DBC"]
ETF_COST = 0.0002


def fred(n: str) -> pd.Series:
    f = pd.read_csv(MACRO / f"{n}.csv")
    s = pd.to_numeric(f.iloc[:, 1], errors="coerce")
    s.index = pd.to_datetime(f.iloc[:, 0])
    return s.dropna()


def known_monthly(s: pd.Series, lag_months: int) -> pd.Series:
    """Monthly series of month M usable from the end of month M + lag (index = month-end decision month)."""
    m = s.copy()
    m.index = m.index.to_period("M") + lag_months
    return m[~m.index.duplicated(keep="last")]


def known_daily(s: pd.Series, months: pd.PeriodIndex) -> pd.Series:
    """Last daily value strictly before the end of each month's last day + 1 (i.e. known at that close)."""
    me = s.groupby(s.index.to_period("M")).last()
    return me.reindex(months)


# ----------------------------------------------------------------- scoring
def mix_returns(R: pd.DataFrame, W: pd.DataFrame, cost: float) -> pd.Series:
    """Monthly result of weights W decided at the end of month t-1, held in month t, minus switching costs."""
    w = W.shift(1).reindex(R.index).fillna(0.0)
    turn = w.diff().abs().sum(axis=1).fillna(w.abs().sum(axis=1))
    return (w * R.fillna(0.0)).sum(axis=1) - turn * cost


def stats(r: pd.Series) -> Dict:
    eq = (1 + r).cumprod()
    yrs = len(r) / 12
    sd = r.std(ddof=1)
    return {"months": int(len(r)), "from": str(r.index[0]), "yearly_%": round(float((eq.iloc[-1] ** (1 / yrs) - 1) * 100), 2),
            "worst_drop_%": round(float((eq / eq.cummax() - 1).min() * 100), 1),
            "per_risk": round(float(r.mean() / sd * math.sqrt(12)), 2) if sd > 0 else None}


def vs(a: pd.Series, b: pd.Series) -> Dict:
    d = (a - b.reindex(a.index)).dropna()
    mu, se = d.mean(), d.std(ddof=1) / math.sqrt(len(d))
    return {"mean_%_per_year": round(mu * 1200, 2), "ci95": [round((mu - 1.96 * se) * 1200, 2), round((mu + 1.96 * se) * 1200, 2)],
            "significant": bool(mu - 1.96 * se > 0 or mu + 1.96 * se < 0)}


# ----------------------------------------------------------------- macro: ETFs 2016-2026
def etf_monthly() -> pd.DataFrame:
    from autobot.data import _download
    cache = ROOT / "data" / "batch2_etf_daily.pkl.gz"
    if cache.exists():
        px = pd.read_pickle(cache)
    else:
        px = pd.DataFrame({s: _download(s, "1Day", datetime(2016, 1, 1, tzinfo=timezone.utc), None, "sip").close for s in ETFS})
        px.to_pickle(cache)
    px.index = px.index.tz_convert("America/New_York").tz_localize(None)
    m = px.groupby(px.index.to_period("M")).last()
    return m.pct_change().iloc[1:]


def part_macro(progress=print) -> Dict:
    R = etf_monthly()
    months = R.index
    cpi = known_monthly(fred("CPIAUCSL"), 1)
    infl = (cpi / cpi.shift(12) - 1) * 100
    rising_infl = (infl > infl.shift(3)).reindex(months)
    be = fred("T5YIE")
    be_up = (be > be.rolling(126).mean()).groupby(be.index.to_period("M")).last().reindex(months)
    y10 = fred("DGS10")
    rates_up = (y10 > y10.rolling(126).mean()).groupby(y10.index.to_period("M")).last().reindex(months)
    ry = fred("DFII10")
    ry_low = (ry < ry.rolling(252).mean()).groupby(ry.index.to_period("M")).last().reindex(months)

    def W(**cols):
        w = pd.DataFrame(0.0, index=months, columns=ETFS)
        for k, v in cols.items():
            w[k] = v
        return w
    sixty = W(SPY=0.6, TLT=0.4)
    basket = W(TIP=1 / 3, GLD=1 / 3, DBC=1 / 3)

    def switch(cond: pd.Series, a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
        c = cond.fillna(False).astype(bool).to_numpy()[:, None]
        return pd.DataFrame(np.where(c, a.to_numpy(), b.to_numpy()), index=months, columns=ETFS)
    rules = {"A inflation trend": switch(rising_infl, basket, sixty),
             "B breakeven trend": switch(be_up, basket, sixty),
             "C rate trend": switch(rates_up, W(SPY=0.6, IEF=0.4), sixty),
             "D real-yield gold": switch(ry_low, W(GLD=1.0), W())}
    bench = {"60/40 SPY/TLT": sixty, "SPY": W(SPY=1.0), "GLD": W(GLD=1.0), "inflation basket": basket,
             "equal weight (6 ETFs)": W(**{s: 1 / 6 for s in ETFS})}
    series = {k: mix_returns(R, w, ETF_COST).iloc[1:] for k, w in {**rules, **bench}.items()}
    res = {"months": f"{months[1]} to {months[-1]}", "benchmarks": {k: stats(series[k]) for k in bench}, "rules": {}}
    for k, w in rules.items():
        ref = "GLD" if k.startswith("D") else "60/40 SPY/TLT"
        res["rules"][k] = {**stats(series[k]), "vs": ref, "difference": vs(series[k], series[ref]),
                           "months_in_switch_%": round(float((w.diff().abs().sum(axis=1) > 0).mean() * 100), 1)}
        progress(f"  {k}: {res['rules'][k]['yearly_%']}%/yr vs {ref} {res['benchmarks'][ref]['yearly_%']}%, difference {res['rules'][k]['difference']}")
    return res


# ----------------------------------------------------------------- long history (index vs bills)
def part_long(progress=print) -> Dict:
    from . import macro_study as ms
    df = ms.build()
    months = df.index
    cpi = known_monthly(fred("CPIAUCSL"), 1).reindex(months)
    infl = cpi / cpi.shift(12) - 1
    rules = {}
    rules["L1 inflation"] = (~(infl > infl.shift(3))).astype(float).where(infl.notna())
    gs = df.GS10
    rules["L2 rates"] = (gs < gs.rolling(12).mean()).astype(float).where(gs.rolling(12).mean().notna())
    um = known_monthly(fred("UMCSENT"), 1).reindex(months)
    med = um.rolling(120, min_periods=60).median()
    rules["L3 consumer mood"] = pd.Series(np.where(um < med, 1.0, 0.5), index=months).where(med.notna())
    vix = fred("VIXCLS")
    vm = vix.groupby(vix.index.to_period("M")).last().reindex(months)
    p80 = vm.rolling(60, min_periods=60).quantile(0.8)
    rules["L4 fear"] = pd.Series(np.where(vm >= p80, 1.0, 0.6), index=months).where(p80.notna())
    res = {}
    for k, pos in rules.items():
        pos = pos.dropna()
        sub = df.loc[pos.index]
        f = ms.replay(sub, pos)
        avg = float(f.w.mean())
        fixed = ms.replay(sub, pd.Series(avg, index=pos.index))
        late = ms.replay(sub, pos.shift(1).dropna())                    # acting a month later (honest version)
        hold = ms.replay(sub, pd.Series(1.0, index=pos.index))
        res[k] = {"rule": ms.stats(f, sub.cash), "fixed_same_amount": ms.stats(fixed, sub.cash), "hold": ms.stats(hold, sub.cash),
                  "a_month_later": ms.stats(late, sub.cash),
                  "vs_fixed_same_amount": ms.paired(f.ret, fixed.ret), "late_vs_fixed": ms.paired(late.ret, fixed.ret.reindex(late.index))}
        progress(f"  {k}: from {res[k]['rule']['from']}, {res[k]['rule']['yearly_%']}%/yr vs fixed {res[k]['fixed_same_amount']['yearly_%']}% "
                 f"(in stocks {res[k]['rule']['in_stocks_%']}%), diff {res[k]['vs_fixed_same_amount']}")
    return res


# ----------------------------------------------------------------- insider purchases and sales
def insider_trades(tickers: List[str], progress=print) -> pd.DataFrame:
    """Open-market purchases (P) and sales (S) by officers / directors of these issuers, from the SEC data sets."""
    cache = SEC / "insider_ps_v3.pkl.gz"
    if cache.exists():
        return pd.read_pickle(cache)
    rows = []
    want = set(tickers)
    for z in sorted(SEC.glob("*_form345.zip")):
        with zipfile.ZipFile(z) as f:
            sub = pd.read_csv(io.BytesIO(f.read("SUBMISSION.tsv")), sep="\t", dtype=str,
                              usecols=["ACCESSION_NUMBER", "FILING_DATE", "ISSUERTRADINGSYMBOL", "DOCUMENT_TYPE"])
            sub = sub[sub.ISSUERTRADINGSYMBOL.str.upper().isin(want) & sub.DOCUMENT_TYPE.isin(["4", "4/A"])]
            if sub.empty:
                continue
            own = pd.read_csv(io.BytesIO(f.read("REPORTINGOWNER.tsv")), sep="\t", dtype=str,
                              usecols=["ACCESSION_NUMBER", "RPTOWNER_RELATIONSHIP"])
            tr = pd.read_csv(io.BytesIO(f.read("NONDERIV_TRANS.tsv")), sep="\t", dtype=str,
                             usecols=["ACCESSION_NUMBER", "TRANS_CODE", "TRANS_SHARES", "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD"])
        tr = tr[tr.ACCESSION_NUMBER.isin(sub.ACCESSION_NUMBER) & tr.TRANS_CODE.isin(["P", "S"])]
        rel = own.groupby("ACCESSION_NUMBER").RPTOWNER_RELATIONSHIP.agg(lambda x: ",".join(x.dropna()))
        tr = tr.merge(sub, on="ACCESSION_NUMBER").assign(rel=lambda d: d.ACCESSION_NUMBER.map(rel).fillna(""))
        tr = tr[tr.rel.str.contains("Officer|Director", case=False)]
        rows.append(pd.DataFrame({"symbol": tr.ISSUERTRADINGSYMBOL.str.upper(), "filed": pd.to_datetime(tr.FILING_DATE, format="%d-%b-%Y", errors="coerce"),
                                  "code": tr.TRANS_CODE, "shares": pd.to_numeric(tr.TRANS_SHARES, errors="coerce"),
                                  "price": pd.to_numeric(tr.TRANS_PRICEPERSHARE, errors="coerce")}).dropna())
        progress(f"  {z.name}: {len(rows[-1])} officer/director purchases and sales")
    T = pd.concat(rows, ignore_index=True)
    T.to_pickle(cache)
    return T


def part_insider(progress=print) -> Dict:
    import yaml
    from autobot.data import _download
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    stocks = [s for b, v in cfg["universe"]["buckets"].items() if b not in ("index_etf", "commodity_etf", "crypto")
              for s in v.get("candidates") or [] if "/" not in s and s != "SPCX"]
    T = insider_trades(stocks, progress)
    cache = ROOT / "data" / "batch2_insider_px.pkl.gz"
    if cache.exists():
        px = pd.read_pickle(cache)
    else:
        px = pd.DataFrame({s: _download(s, "1Day", datetime(2016, 1, 1, tzinfo=timezone.utc), None, "sip").close for s in stocks})
        px.to_pickle(cache)
    px.index = px.index.tz_convert("America/New_York").tz_localize(None)
    R = px.groupby(px.index.to_period("M")).last().pct_change().iloc[1:]
    months = R.index
    T = T[T.filed.notna()].copy()
    T["month"] = T.filed.dt.to_period("M")
    # the filing's own price (unadjusted, right for that day); a few filings carry impossible values, dropped:
    # a price above $10,000 a share or a single trade above $3 billion
    T["usd"] = T.shares * T.price
    bad = (T.price <= 0) | (T.price > 10_000) | (T.usd > 3e9)
    T = T[~bad]
    net = (T.assign(s=np.where(T.code == "P", T.usd, -T.usd)).groupby(["month", "symbol"]).s.sum().unstack().reindex(months).fillna(0.0))
    buys = (T[T.code == "P"].groupby(["month", "symbol"]).usd.sum().unstack().reindex(months).fillna(0.0))
    net3, buy3 = net.rolling(3).sum(), buys.rolling(3).sum()
    cols = [s for s in stocks if s in R.columns]
    eq = pd.DataFrame(1.0 / len(cols), index=months, columns=cols)
    rbuy = buy3.reindex(columns=cols).fillna(0) > 0
    wb = rbuy.div(rbuy.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    n3 = net3.reindex(columns=cols).fillna(0)
    worst = n3.rank(axis=1, method="first") <= 3                     # the 3 heaviest net sellers
    ws = worst.astype(float).div(3)
    r_eq, r_b, r_s = mix_returns(R[cols], eq, ETF_COST), mix_returns(R[cols], wb, ETF_COST), mix_returns(R[cols], ws, ETF_COST)
    have = wb.shift(1).reindex(R.index).sum(axis=1) > 0                # months with at least one buyer stock
    res = {"stocks": cols, "dropped_impossible_rows": int(bad.sum()), "officer_director_trades": {"purchases": int((T.code == "P").sum()), "sales": int((T.code == "S").sum()),
                                                       "purchases_usd_m": round(float(T[T.code == "P"].usd.sum() / 1e6), 1),
                                                       "sales_usd_m": round(float(T[T.code == "S"].usd.sum() / 1e6), 1)},
           "months_with_a_buyer_stock": int(have.sum()), "months": int(len(R) - 1),
           "equal_weight": stats(r_eq.iloc[1:]),
           "net_buyers_when_any": {**stats(r_b[have]), "vs_equal_same_months": vs(r_b[have], r_eq[have])},
           "heaviest_sellers": {**stats(r_s.iloc[3:]), "vs_equal": vs(r_s.iloc[3:], r_eq.iloc[3:])}}
    progress(f"  insider: {res['officer_director_trades']}, buyer months {res['months_with_a_buyer_stock']}")
    return res


def part_real(progress=print) -> Dict:
    """L1 and L2 again on REAL month-end closes (SPY total return, 2016-2026, cash = 3-month bills), the check
    that sank the 10-month rule (monthly average prices flatter timing rules)."""
    R = etf_monthly()
    months = R.index
    tb = fred("TB3MS")
    cash = (tb.groupby(tb.index.to_period("M")).last().reindex(months).ffill() / 1200)
    cpi = known_monthly(fred("CPIAUCSL"), 1).reindex(months)
    infl = cpi / cpi.shift(12) - 1
    gs = fred("GS10")
    gs = gs.groupby(gs.index.to_period("M")).last().reindex(months)
    rules = {"L1 inflation": (~(infl > infl.shift(3))).astype(float).where(infl.notna()),
             "L2 rates": (gs < gs.rolling(12).mean()).astype(float).where(gs.rolling(12).mean().notna())}
    res = {}
    for k, pos in rules.items():
        for late in (0, 1):
            w = pos.shift(1 + late)
            turn = w.diff().abs().fillna(0.0)
            r = (w * R.SPY + (1 - w) * cash - turn * ETF_COST).dropna()
            avg = float(w.reindex(r.index).mean())
            fixed = (avg * R.SPY + (1 - avg) * cash).reindex(r.index)
            hold = R.SPY.reindex(r.index)
            res[f"{k}{' (a month later)' if late else ''}"] = {**stats(r), "in_stocks_%": round(avg * 100, 1), "fixed_same_amount": stats(fixed),
                                                                "hold": stats(hold), "vs_fixed": vs(r, fixed)}
            progress(f"  {k}{' late' if late else ''}: {stats(r)['yearly_%']}%/yr vs fixed {stats(fixed)['yearly_%']}% vs hold {stats(hold)['yearly_%']}%, {vs(r, fixed)}")
    return res


def run(parts, progress=print) -> Dict:
    res = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    for p in parts:
        progress(f"part {p}")
        res[p] = {"macro": part_macro, "long": part_long, "real": part_real, "insider": part_insider}[p](progress)
        res["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", nargs="+", default=["macro", "long", "insider"])
    run(ap.parse_args().part)
    print("saved", OUT)


if __name__ == "__main__":
    main()
