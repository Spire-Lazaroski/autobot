"""
Volatility targeting for the core holding: does moving the core between 15% and 45% of the
account by the volatility forecast beat the plain 30% hold? Diagnosis only: nothing here changes the bot.

    .venv-ml/Scripts/python -m autobot.vol_target_study       # data/ml/study_vol_target.json

Literature: Moreira & Muir (2017), Harvey et al. (2018) for; Cederburg et al. (2020) against (weaker
on new data). Volatility itself is predictable (Engle 1982, Bollerslev 1986, Corsi 2009).

Data: S&P 500 daily closes from FRED (data/macro/SP500.csv, 10 years, price only). Dividends are
added as a flat 1.4% a year (the same for every method, in proportion to the amount held). Cash
earns 0 (uninvested cash in this account earns nothing); a T-bill version is reported as a check.

The account here = the core + cash (the trading jobs are left out; they're the same for all methods).
Each method gives a target weight for the core, decided with closes up to day t and traded at day
t+1's close (a full day late, on the cautious side). Like the live core, it trades only when the
actual weight is more than BAND points from the target; costs 2 bps of the amount traded.

Methods (the forecast is next week's daily volatility; target = 30% x normal / forecast, clipped
to 15%-45%; 'normal' = median of the method's own forecasts so far):
  hold_30   today: 30%, rebalanced at the 5-point band
  ewma      RiskMetrics: exponentially weighted variance of daily returns (lambda 0.94)
  har       Corsi's HAR: next week's realized variance from last day / week / month (refit yearly)
  trees     LightGBM on the past 1/5/20/60-day volatility, returns and drawdown (refit yearly)
  vix       the VIX alone (the market's expected volatility for the next month, FRED VIXCLS; the
            close of day t is used for the decision at day t's close)
  har_vix   HAR with the VIX added as a fourth input
  trees_vix the trees with the VIX and its 5-day change added
Walk-forward: the first 750 trading days only train the models; all methods are scored on the same
later days. Results are also shown scaled to the same average amount invested as hold_30.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import ml_features as mf
from .macro_study import DIR

OUT = mf.OUT_DIR / "study_vol_target.json"
BASE, LO, HI = 0.30, 0.15, 0.45
BAND, COST = 0.05, 0.0002
DIV_YIELD = 0.014
TRAIN, REFIT, H = 750, 250, 5
LAMBDA = 0.94
METHODS = ("hold_30", "ewma", "har", "trees", "vix", "har_vix", "trees_vix")
CRISES = {"2020 Covid": ("2020-02-19", "2020-03-23"), "2022 bear": ("2022-01-03", "2022-10-12"),
          "2025 tariff drop": ("2025-02-19", "2025-04-08")}


def load() -> pd.DataFrame:
    f = pd.read_csv(DIR / "SP500.csv")
    px = pd.to_numeric(f.iloc[:, 1], errors="coerce")
    px.index = pd.to_datetime(f.iloc[:, 0])
    px = px.dropna()
    tb = pd.read_csv(DIR / "TB3MS.csv")
    tbs = pd.to_numeric(tb.iloc[:, 1], errors="coerce")
    tbs.index = pd.to_datetime(tb.iloc[:, 0]) + pd.offsets.MonthBegin(1)        # month M known from M+1
    tbill = tbs.reindex(px.index, method="ffill") / 100 / 252
    v = pd.read_csv(DIR / "VIXCLS.csv")
    vix = pd.to_numeric(v.iloc[:, 1], errors="coerce")
    vix.index = pd.to_datetime(v.iloc[:, 0])
    vix = vix.dropna().reindex(px.index, method="ffill")
    return pd.DataFrame({"px": px, "r": px.pct_change(), "tbill": tbill, "vix": vix})


# ----------------------------------------------------------------- volatility forecasts (variance of daily returns)
def future_var(r: pd.Series, h: int = H) -> pd.Series:
    """Target: average squared return over the next h days (only for training and scoring)."""
    return (r ** 2).rolling(h).mean().shift(-h)


def ewma_var(r: pd.Series, lam: float = LAMBDA) -> pd.Series:
    return (r ** 2).ewm(alpha=1 - lam, adjust=False).mean()


def har_inputs(r: pd.Series, vix: Optional[pd.Series] = None) -> pd.DataFrame:
    r2 = r ** 2
    out = pd.DataFrame({"d": r2, "w": r2.rolling(5).mean(), "m": r2.rolling(22).mean()})
    if vix is not None:
        out["vix"] = vix_var(vix)
    return out


def vix_var(vix: pd.Series) -> pd.Series:
    """The VIX as a daily variance (it is quoted as a yearly volatility in %)."""
    return (vix / 100) ** 2 / 252


def tree_inputs(df: pd.DataFrame, with_vix: bool = False) -> pd.DataFrame:
    r = df.r
    out = pd.DataFrame(index=df.index)
    for n in (1, 5, 20, 60):
        out[f"vol_{n}"] = r.rolling(n).std() if n > 1 else r.abs()
        out[f"ret_{n}"] = df.px.pct_change(n)
    out["drawdown"] = df.px / df.px.cummax() - 1
    out["ewma"] = np.sqrt(ewma_var(r))
    if with_vix:
        out["vix"] = df.vix
        out["vix_5d"] = df.vix.pct_change(5)
    return out


def walk(X: pd.DataFrame, y: pd.Series, fit_predict) -> pd.Series:
    """Refit every REFIT days on all earlier rows whose target had ended (index < a - H)."""
    out = pd.Series(np.nan, index=X.index)
    for a in range(TRAIN, len(X), REFIT):
        tr = np.arange(len(X)) < a - H
        ok = tr & X.notna().all(axis=1).to_numpy() & y.notna().to_numpy()
        te = slice(a, min(a + REFIT, len(X)))
        out.iloc[te] = fit_predict(X[ok], y[ok], X.iloc[te])
    return out


def har_forecast(df: pd.DataFrame, with_vix: bool = False) -> pd.Series:
    X, y = har_inputs(df.r, df.vix if with_vix else None), future_var(df.r)

    def fp(Xt, yt, Xs):
        A = np.c_[np.ones(len(Xt)), Xt.to_numpy()]
        b = np.linalg.lstsq(A, yt.to_numpy(), rcond=None)[0]
        p = np.c_[np.ones(len(Xs)), Xs.fillna(0).to_numpy()] @ b
        return np.maximum(p, 1e-8)
    return walk(X, y, fp)


def tree_forecast(df: pd.DataFrame, with_vix: bool = False) -> pd.Series:
    import lightgbm as lgb
    X, y = tree_inputs(df, with_vix), np.log(future_var(df.r) + 1e-10)

    def fp(Xt, yt, Xs):
        m = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.03, num_leaves=7, min_child_samples=50,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1, random_state=0)
        return np.exp(m.fit(Xt.to_numpy(), yt.to_numpy()).predict(Xs.to_numpy()))
    return walk(X, y, fp)


def target_weight(var: pd.Series) -> pd.Series:
    """30% x normal / forecast volatility, clipped to 15%-45%; normal = median of the forecasts so far."""
    vol = np.sqrt(var)
    normal = vol.expanding(min_periods=20).median()
    return (BASE * normal / vol).clip(LO, HI)


# ----------------------------------------------------------------- replay
def replay(df: pd.DataFrame, target: pd.Series, cash_rate: Optional[pd.Series] = None, band: float = BAND) -> pd.DataFrame:
    """Daily account (core + cash). The target decided at day t's close is traded at day t+1's close."""
    r = df.r.fillna(0).to_numpy() + DIV_YIELD / 252
    cr = np.zeros(len(df)) if cash_rate is None else cash_rate.fillna(0).to_numpy()
    tgt = target.shift(1).to_numpy()
    w = np.zeros(len(df))
    ret, cost, traded = np.zeros(len(df)), np.zeros(len(df)), np.zeros(len(df))
    cur = np.nan
    for i in range(len(df)):
        if np.isnan(cur):                                   # first day with a target: buy it
            if np.isfinite(tgt[i]):
                cur = tgt[i]
                cost[i] = cur * COST
                traded[i] = cur
            w[i] = 0 if np.isnan(cur) else cur
            continue
        gross = cur * r[i] + (1 - cur) * cr[i]               # the day's result with yesterday's weight
        cur = cur * (1 + r[i]) / (1 + gross)                  # weight drifts with the market
        ret[i] = gross
        t = tgt[i]
        if np.isfinite(t) and abs(cur - t) > band:
            cost[i] = abs(t - cur) * COST
            traded[i] = abs(t - cur)
            cur = t
        ret[i] -= cost[i]
        w[i] = cur
    return pd.DataFrame({"ret": ret, "weight": w, "cost": cost, "traded": traded}, index=df.index)


def stats(f: pd.DataFrame) -> Dict:
    r = f.ret
    eq = (1 + r).cumprod()
    years = len(r) / 252
    sd = r.std(ddof=1)
    mon = (1 + r).groupby(r.index.to_period("M")).prod() - 1
    return {"yearly_%": round(float((eq.iloc[-1] ** (1 / years) - 1) * 100), 2),
            "yearly_ups_downs_%": round(float(sd * math.sqrt(252) * 100), 2),
            "per_risk": round(float(r.mean() / sd * math.sqrt(252)), 3),
            "max_drop_%": round(float((eq / eq.cummax() - 1).min() * 100), 2),
            "worst_month_%": round(float(mon.min() * 100), 2),
            "avg_weight_%": round(float(f.weight.mean() * 100), 1),
            "trades_per_year": round(float((f.traded > 0).sum() / years), 1),
            "costs_%_per_year": round(float(f.cost.sum() / years * 100), 3)}


def boot(a: pd.Series, b: pd.Series, n: int = 2000, block: int = 21, seed: int = 0) -> Dict:
    """Per-risk and max-drop differences (a minus b) with 95% ranges, resampling 1-month blocks of days (paired)."""
    rng = np.random.default_rng(seed)
    A, B = a.to_numpy(), b.to_numpy()
    k = len(A)
    starts = np.arange(0, k - block)

    def pr(x):
        return x.mean() / x.std(ddof=1) * math.sqrt(252)

    def dd(x):
        e = np.cumprod(1 + x)
        return (e / np.maximum.accumulate(e) - 1).min()
    d1, d2 = [], []
    for _ in range(n):
        idx = np.concatenate([np.arange(s, s + block) for s in rng.choice(starts, k // block + 1)])[:k]
        d1.append(pr(A[idx]) - pr(B[idx]))
        d2.append(dd(A[idx]) - dd(B[idx]))
    q = lambda x, m=1: [round(float(np.percentile(x, 2.5) * m), 3), round(float(np.percentile(x, 97.5) * m), 3)]  # noqa: E731
    return {"per_risk_diff": round(float(pr(A) - pr(B)), 3), "per_risk_ci95": q(d1),
            "max_drop_diff_points": round(float((dd(A) - dd(B)) * 100), 2), "max_drop_ci95_points": q(d2, 100)}


def run(progress=print) -> Dict:
    df = load()
    var = {"ewma": ewma_var(df.r), "har": har_forecast(df), "trees": tree_forecast(df),
           "vix": vix_var(df.vix), "har_vix": har_forecast(df, True), "trees_vix": tree_forecast(df, True)}
    fv = future_var(df.r)
    start = df.index[TRAIN]
    T = df.index >= start
    acc = {}
    for k, v in var.items():
        m = T & fv.notna().to_numpy() & v.notna().to_numpy()
        e = np.sqrt(v[m]) - np.sqrt(fv[m])
        acc[k] = {"error_daily_vol_%": round(float(e.abs().mean() * 100), 4),
                  "corr": round(float(np.corrcoef(np.sqrt(v[m]), np.sqrt(fv[m]))[0, 1]), 3)}
    progress("forecast accuracy (next week's daily volatility): " + ", ".join(f"{k} error {a['error_daily_vol_%']}%, corr {a['corr']}" for k, a in acc.items()))
    targets = {"hold_30": pd.Series(BASE, index=df.index), **{k: target_weight(v) for k, v in var.items()}}
    for k in targets:                                      # all methods scored on the same days
        targets[k] = targets[k].where(T)
    res: Dict = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "data": f"S&P 500 daily closes {df.index[0].date()} to {df.index[-1].date()} (FRED), dividends {DIV_YIELD:.1%} a year flat",
                 "test_from": str(start.date()), "test_to": str(df.index[-1].date()),
                 "rules": {"base": BASE, "range": [LO, HI], "band": BAND, "cost_per_side": COST},
                 "forecast_accuracy": acc}
    for cash_name, cash in (("cash_0", None), ("cash_tbill", df.tbill)):
        frames = {k: replay(df, t, cash)[T] for k, t in targets.items()}
        hold = frames["hold_30"]
        sect = {"as_traded": {k: stats(f) for k, f in frames.items()}}
        # same average amount invested as hold_30: scale each method's weights' effect (excess over cash) by the ratio
        matched = {}
        for k, f in frames.items():
            ratio = hold.weight.mean() / f.weight.mean()
            g = f.copy()
            base_cash = 0 if cash is None else cash[T].fillna(0)
            g["ret"] = base_cash + (f.ret - base_cash) * ratio
            g["weight"] = f.weight * ratio
            matched[k] = g
        sect["same_amount"] = {k: stats(f) for k, f in matched.items()}
        sect["vs_hold"] = {k: boot(matched[k].ret, matched["hold_30"].ret) for k in METHODS if k != "hold_30"}
        sect["crises_%"] = {name: {k: round(float(((1 + f.ret[a:b]).prod() - 1) * 100), 2) for k, f in frames.items()}
                            for name, (a, b) in CRISES.items()}
        res[cash_name] = sect
        progress(f"{cash_name}: " + ", ".join(f"{k} per risk {s['per_risk']}" for k, s in sect["same_amount"].items()))
    res["lines"] = lines(res)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def lines(res: Dict) -> List[str]:
    s = res["cash_0"]
    out = [f"Core 15-45% by volatility forecast vs plain 30%, {res['test_from']} to {res['test_to']}, after costs, cash earns 0. {res['data']}."]
    out.append("Forecast accuracy: " + ", ".join(f"{k} error {a['error_daily_vol_%']}% corr {a['corr']}" for k, a in res["forecast_accuracy"].items()))
    for k in METHODS:
        a, m = s["as_traded"][k], s["same_amount"][k]
        out.append(f"  {k:<8} as traded: {a['yearly_%']:+6.2f}%/yr, ups/downs {a['yearly_ups_downs_%']:5.2f}%, max drop {a['max_drop_%']:+6.2f}%, "
                   f"avg weight {a['avg_weight_%']}%, {a['trades_per_year']} trades/yr | same amount: {m['yearly_%']:+6.2f}%/yr, "
                   f"per risk {m['per_risk']:+.3f}, max drop {m['max_drop_%']:+6.2f}%")
    for k, b in s["vs_hold"].items():
        out.append(f"  {k} vs hold (same amount): per risk {b['per_risk_diff']:+.3f} ({b['per_risk_ci95'][0]:+.3f} to {b['per_risk_ci95'][1]:+.3f}); "
                   f"max drop {b['max_drop_diff_points']:+.2f} points ({b['max_drop_ci95_points'][0]:+.2f} to {b['max_drop_ci95_points'][1]:+.2f})")
    for name, row in s["crises_%"].items():
        out.append(f"  {name}: " + ", ".join(f"{k} {v:+.2f}%" for k, v in row.items()))
    t = res["cash_tbill"]["vs_hold"]
    out.append("  With T-bill cash: " + ", ".join(f"{k} per risk {b['per_risk_diff']:+.3f} ({b['per_risk_ci95'][0]:+.3f} to {b['per_risk_ci95'][1]:+.3f})" for k, b in t.items()))
    return out


def main():
    res = run()
    print("\n".join(res["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
