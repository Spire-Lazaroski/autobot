"""
Sizing study: how much of the account should be in stocks, and should that share be fixed or move?
Diagnosis only: nothing here changes config.yaml or the bot.

    python -m autobot.sizing_study              # writes data/sizing_study.json
    python -m autobot.sizing_study --summary    # only reprint the saved summary

The same-money study (docs/STRATEGY_EVIDENCE.md section 26) found that timing adds nothing
measurable: the result is set by how much money is at work. So here the only choice is the share
in stocks (the rest in cash), on two samples:
  daily    S&P 500 2016-2026 (FRED, price + 1.4% a year dividends, as the core study), 5-point band
  monthly  US stocks 1871-2026 (Shiller total return, then SPY; macro study data), checked monthly
and two cash assumptions: 0% (every earlier study; Alpaca's PAPER account pays no interest) and the
3-month T-bill rate of the time (close to what a funded Alpaca account's cash programme pays).

Ways to set the share, each at a base level s = 10%, 20% ... 100% (never above 100%, no borrowing):
  fixed         always s
  vol target    s x normal volatility / recent volatility, at most 100% (daily: EWMA 0.94 as the
                live core; monthly: last 12 months; normal = the median so far)
  trend         s while the index is above its 10-month (daily: 200-day) average, else 0
  drawdown cut  s x (1 - index drop from its peak / 50%), between s/4 and s (less after falls)
and, on the monthly sample only, a share from the Kelly rule (the share that maximises long-run
growth: expected excess return / variance, from data known at the time; first 30 years only
estimate), full and half, at most 100%.

The answer is read as a menu: for the worst drop one is willing to live with (10%, 20%, 30%),
which way of sizing gives the most return? Each method's results across s are interpolated at
those drops. Costs: 2 bps of the amount traded; decisions at day/month t's close, held from t+1.
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
OUT = ROOT / "data" / "sizing_study.json"
LEVELS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
DROPS = [10, 20, 30]
COST = 0.0002
DD_SPAN = 0.5
KELLY_WARMUP = 360            # months used only to estimate the Kelly inputs
DAILY_WARMUP = 252


# ----------------------------------------------------------------- data
def daily_data() -> pd.DataFrame:
    from . import vol_target_study as vt
    df = vt.load()
    return pd.DataFrame({"r": df.r.fillna(0) + vt.DIV_YIELD / 252, "px": df.px, "tbill": df.tbill}).dropna(subset=["px"])


def monthly_data() -> pd.DataFrame:
    from . import macro_study as ms
    df = ms.build()
    px = (1 + df.ret).cumprod()        # total-return index (the methods only use its shape)
    return pd.DataFrame({"r": df.ret, "px": px, "tbill": df.cash})


# ----------------------------------------------------------------- shares
def shares(df: pd.DataFrame, s: float, freq: str) -> Dict[str, pd.Series]:
    """Target share in stocks decided at each close (rest cash), per method, at base level s."""
    idx, r, px = df.index, df.r, df.px
    if freq == "daily":
        var = (r ** 2).ewm(alpha=0.06, adjust=False).mean()
        vol = np.sqrt(var)
        avg = px.rolling(200).mean()
        per_year = 252
    else:
        vol = r.rolling(12).std()
        avg = px.rolling(10).mean()
        per_year = 12
    normal = vol.expanding(min_periods=per_year).median()
    dd = 1 - px / px.cummax()
    out = {"fixed": pd.Series(s, index=idx),
           "vol target": (s * normal / vol).clip(upper=1.0).where(normal.notna(), s),
           "trend": pd.Series(np.where(px > avg, s, 0.0), index=idx).where(avg.notna(), s),
           "drawdown cut": (s * (1 - dd / DD_SPAN)).clip(lower=s / 4, upper=s)}
    return out


def kelly(df: pd.DataFrame, frac: float) -> pd.Series:
    """frac x (expected excess return / variance), from all months up to t; at most 100%."""
    ex = df.r - df.tbill
    mu = ex.expanding(min_periods=KELLY_WARMUP).mean()
    var = ex.expanding(min_periods=KELLY_WARMUP).var()
    return (frac * mu / var).clip(0.0, 1.0)


# ----------------------------------------------------------------- replay
def replay(df: pd.DataFrame, target: pd.Series, cash: Optional[pd.Series], band: float) -> pd.DataFrame:
    """The share decided at t's close is held from t+1; between trades it drifts with prices.
    Trades only when the actual share is more than `band` from the target."""
    r = df.r.fillna(0).to_numpy()
    c = np.zeros(len(df)) if cash is None else cash.fillna(0).to_numpy()
    tgt = target.shift(1).to_numpy()
    ret, w = np.zeros(len(df)), np.zeros(len(df))
    cur = np.nan
    for i in range(len(df)):
        if np.isnan(cur):
            if np.isfinite(tgt[i]):
                cur = tgt[i]
                ret[i] = -cur * COST
            w[i] = 0 if np.isnan(cur) else cur
            continue
        g = cur * r[i] + (1 - cur) * c[i]
        cur = cur * (1 + r[i]) / (1 + g) if (1 + g) > 0 else cur
        ret[i] = g
        t = tgt[i]
        if np.isfinite(t) and abs(cur - t) > band:
            ret[i] -= abs(t - cur) * COST
            cur = t
        w[i] = cur
    return pd.DataFrame({"ret": ret, "w": w}, index=df.index)


def stats(f: pd.DataFrame, per_year: int, cash: Optional[pd.Series]) -> Dict:
    r = f.ret
    eq = (1 + r).cumprod()
    years = len(r) / per_year
    ex = r - (0 if cash is None else cash.reindex(r.index).fillna(0))
    return {"yearly_%": round(float((eq.iloc[-1] ** (1 / years) - 1) * 100), 2),
            "worst_drop_%": round(float((eq / eq.cummax() - 1).min() * 100), 1),
            "per_risk": round(float(ex.mean() / ex.std() * math.sqrt(per_year)), 2) if ex.std() > 0 else 0.0,
            "avg_share_%": round(float(f.w.mean() * 100), 1)}


def at_drop(curve: List[Dict], drop: float) -> Optional[float]:
    """Yearly return interpolated at a worst drop of `drop` %, along a method's levels."""
    pts = sorted((-c["worst_drop_%"], c["yearly_%"]) for c in curve)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    if drop < xs[0] or drop > xs[-1]:
        return None
    return round(float(np.interp(drop, xs, ys)), 2)


def paired_per_year(a: pd.Series, b: pd.Series, per_year: int) -> Dict:
    d = (a - b).dropna()
    mu, se = d.mean(), d.std(ddof=1) / math.sqrt(len(d))
    return {"mean_%_per_year": round(mu * per_year * 100, 2),
            "ci95": [round((mu - 1.96 * se) * per_year * 100, 2), round((mu + 1.96 * se) * per_year * 100, 2)],
            "significant": bool(mu - 1.96 * se > 0 or mu + 1.96 * se < 0)}


# ----------------------------------------------------------------- one sample
def sample(df: pd.DataFrame, freq: str) -> Dict:
    per_year, band = (252, 0.05) if freq == "daily" else (12, 0.0)
    start = df.index[DAILY_WARMUP if freq == "daily" else KELLY_WARMUP]
    T = df.index >= start
    out: Dict = {"from": str(df.index[T][0])[:10], "to": str(df.index[-1])[:10]}
    for cash_name, cash in (("cash 0%", None), ("cash at T-bill", df.tbill)):
        cash_t = None if cash is None else cash[T]
        curves: Dict[str, List[Dict]] = {}
        frames: Dict[str, Dict[float, pd.DataFrame]] = {}
        for s in LEVELS:
            for m, tgt in shares(df, s, freq).items():
                f = replay(df, tgt, cash, band)[T]
                frames.setdefault(m, {})[s] = f
                curves.setdefault(m, []).append({"level_%": int(s * 100), **stats(f, per_year, cash_t)})
        if freq == "monthly":
            for name, frac in (("Kelly (full)", 1.0), ("Kelly (half)", 0.5)):
                f = replay(df, kelly(df, frac), cash, band)[T]
                curves[name] = [{"level_%": None, **stats(f, per_year, cash_t)}]
                frames[name] = {None: f}
        sect: Dict = {"curves": curves,
                      "at_drop": {m: {f"{d}%": at_drop(c, d) for d in DROPS} for m, c in curves.items() if len(c) > 1}}
        # each method vs fixed at the same average share: the fixed line through the method's average share
        fixed = frames["fixed"][1.0]
        vs = {}
        for m, by in frames.items():
            if m == "fixed":
                continue
            f = by.get(0.5, next(iter(by.values())))
            share = f.w.mean()
            base = (0 if cash_t is None else cash_t.fillna(0))
            same = base + (fixed.ret - base) * share       # fixed at the method's average share
            vs[m] = {"at_level_%": None if 0.5 not in by else 50, "avg_share_%": round(float(share * 100), 1),
                     **paired_per_year(f.ret, same, per_year)}
        sect["vs_fixed_same_money"] = vs
        out[cash_name] = sect
    return out


def current_share() -> Optional[Dict]:
    """The account's share invested now, from the bot's status file (read-only)."""
    try:
        s = json.loads((ROOT / "logs" / "status.json").read_text(encoding="utf-8"))
        sp = s["split"]
        eq = float(sp["equity"])
        return {"time": s.get("time"), "core_%": round(sp["core_usd"] / eq * 100, 1), "rotation_%": round(sp["rotation_usd"] / eq * 100, 1),
                "jobs_%": round(sp["jobs_usd"] / eq * 100, 1),
                "invested_%": round((sp["core_usd"] + sp["rotation_usd"] + sp["jobs_usd"]) / eq * 100, 1)}
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
        return None


def run() -> Dict:
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "settings": {"levels": LEVELS, "cost": COST, "drawdown_span": DD_SPAN, "kelly_warmup_months": KELLY_WARMUP},
           "now": current_share(),
           "daily": sample(daily_data(), "daily"),
           "monthly": sample(monthly_data(), "monthly")}
    res["summary"] = summary(res)
    return res


def summary(res: Dict) -> List[str]:
    L = []
    if res.get("now"):
        n = res["now"]
        L.append(f"Account now: {n['invested_%']}% invested (core {n['core_%']}%, rotation {n['rotation_%']}%, jobs {n['jobs_%']}%), the rest cash.")
    for smp in ("daily", "monthly"):
        d = res[smp]
        for cash_name in ("cash 0%", "cash at T-bill"):
            s = d[cash_name]
            L.append(f"[{smp} {d['from']} to {d['to']}, {cash_name}]")
            fx = {c["level_%"]: c for c in s["curves"]["fixed"]}
            L.append("  fixed: " + ", ".join(f"{k}% -> {c['yearly_%']:+.1f}%/yr, drop {c['worst_drop_%']:.0f}%" for k, c in fx.items() if k in (20, 40, 60, 80, 100)))
            for m, a in s["at_drop"].items():
                L.append(f"  {m}: return at a worst drop of " + ", ".join(f"{k} {('%+.1f%%' % v) if v is not None else 'n/a'}" for k, v in a.items()))
            for m, c in s["curves"].items():
                if c[0]["level_%"] is None:
                    x = c[0]
                    L.append(f"  {m}: {x['yearly_%']:+.1f}%/yr, drop {x['worst_drop_%']:.0f}%, average share {x['avg_share_%']:.0f}%, per risk {x['per_risk']:.2f}")
            for m, v in s["vs_fixed_same_money"].items():
                L.append(f"  {m} vs fixed at the same average share ({v['avg_share_%']:.0f}%): {v['mean_%_per_year']:+.2f}% a year "
                         f"({v['ci95'][0]:+.2f} to {v['ci95'][1]:+.2f}){' CLEAR' if v['significant'] else ''}")
    return L


def main():
    import argparse
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Sizing study (diagnosis only).")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    if a.summary:
        print("\n".join(json.loads(OUT.read_text(encoding="utf-8"))["summary"]))
        return
    res = run()
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print("\n".join(res["summary"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
