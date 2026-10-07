"""
Time-series check on 5-minute price changes: do the last moves predict the next one? Autocorrelation,
variance ratios and a walk-forward ARIMA forecast traded after costs. Diagnosis only.

    .venv-ml/Scripts/python -m autobot.arima_study        # data/ml/study_arima.json

Data: each symbol's 5-minute returns in bps inside the session (log price differenced once, so
ARIMA(p,1,q) on prices = ARMA(p,q) on these returns); the overnight move is left out.
  1. autocorrelation at lags 1-12 and the Ljung-Box test over them (is there any pattern at all?)
  2. variance ratios VR(q) for q = 2, 6, 12, 78 bars (above 1: moves continue; below 1: they reverse;
     approximate 95% range under no pattern)
  3. ARMA walk-forward: every 20 sessions the order (p, q) in {(1,0), (0,1), (1,1), (2,0), (2,2)} with
     the lowest AIC on the previous 60 sessions is fitted, then run forward with fixed parameters to
     forecast each next bar from bars up to the current close. Scored on the same 386 unseen sessions:
     error vs a forecast of zero (out-of-sample R squared), share of signs right, and a rule that holds
     the forecast's side (long or short) for the next bar when |forecast| > threshold: 0 (always), or
     the round-trip cost; flat at the 15:50 bar; costs 2 bps per side (twice in the close-out bar).
"""
from __future__ import annotations

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
OUT = mf.OUT_DIR / "study_arima.json"
FORM, TEST, FIRST = 60, 20, 120
ORDERS = [(1, 0), (0, 1), (1, 1), (2, 0), (2, 2)]
VR_Q = (2, 6, 12, 78)
CLOSEOUT_MIN = 950


def session_returns(df: pd.DataFrame) -> pd.DataFrame:
    ny = df.index.tz_convert(NY)
    day = np.asarray(ny.date)
    first = np.r_[True, day[1:] != day[:-1]]
    r = np.log(df.close).diff() * 1e4
    out = pd.DataFrame({"r": r.to_numpy(), "day": day, "minute": np.asarray(ny.hour * 60 + ny.minute)}, index=df.index)
    return out[~first].dropna()


def autocorr(r: np.ndarray, lags: int = 12) -> Dict:
    x = r - r.mean()
    d = np.dot(x, x)
    ac = [float(np.dot(x[:-k], x[k:]) / d) for k in range(1, lags + 1)]
    n = len(x)
    q = n * (n + 2) * sum(a * a / (n - k) for k, a in enumerate(ac, start=1))
    from scipy.stats import chi2
    return {"acf": [round(a, 4) for a in ac], "band95": round(1.96 / math.sqrt(n), 4),
            "ljung_box_q": round(q, 1), "ljung_box_p": float(chi2.sf(q, lags))}


def variance_ratio(r: pd.Series, day: np.ndarray, q: int) -> Dict:
    """Variance of q-bar sums inside sessions over q x the variance of 1-bar returns."""
    s = pd.Series(r.to_numpy() - r.mean())
    sums = s.groupby(day).transform(lambda x: x.rolling(q).sum()).dropna()
    vr = float(sums.var() / (q * s.var()))
    n = len(s)
    se = math.sqrt(2 * (2 * q - 1) * (q - 1) / (3 * q * n))
    return {"vr": round(vr, 3), "band95": [round(1 - 1.96 * se, 3), round(1 + 1.96 * se, 3)]}


def arma_forecast(r: pd.Series, day: np.ndarray, days: List, progress=None) -> Dict:
    """One-step forecasts on the test windows; the order and parameters from the previous 60 sessions."""
    import warnings
    from statsmodels.tsa.arima.model import ARIMA
    pred = pd.Series(np.nan, index=r.index)
    chosen = []
    for k in range(FIRST, len(days), TEST):
        f_mask = np.isin(day, days[k - FORM:k])
        t_mask = np.isin(day, days[k:k + TEST])
        y = r[f_mask].to_numpy()
        best = None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for p, q in ORDERS:
                try:
                    m = ARIMA(y, order=(p, 0, q), trend="c").fit()
                except Exception:  # noqa: BLE001
                    continue
                if best is None or m.aic < best[1].aic:
                    best = ((p, q), m)
            if best is None:
                continue
            both = np.r_[y, r[t_mask].to_numpy()]
            run = best[1].apply(both, refit=False)
            fc = run.predict()[len(y):]                              # forecast of bar t from bars up to t-1
        pred[t_mask] = fc
        chosen.append(f"ARMA{best[0]}")
    return {"pred": pred, "chosen": chosen}


def rule(pred: np.ndarray, r: np.ndarray, minute: np.ndarray, day: np.ndarray, thr: float, side_cost_bps: float) -> np.ndarray:
    """Hold sign(forecast) for the bar when |forecast| > thr; per-bar result in bps after costs."""
    pos = np.where(np.isfinite(pred) & (np.abs(pred) > thr), np.sign(pred), 0.0)
    pos[minute >= CLOSEOUT_MIN] = 0.0
    prev = np.r_[0.0, pos[:-1]]
    prev[np.r_[True, day[1:] != day[:-1]]] = 0.0                    # every session starts flat
    mult = np.where(minute >= CLOSEOUT_MIN, 2.0, 1.0)
    return pos * r - np.abs(pos - prev) * side_cost_bps * mult


def run(cfg: Dict, symbols: Optional[List[str]] = None, progress=print) -> Dict:
    syms = symbols or cached_symbols()
    data = cs.Data(syms)
    r_cfg = rc.settings(cfg)
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": str(data.as_of), "symbols": syms,
           "per_symbol": {}}
    acc = {k: [] for k in ("gross_always", "net_always", "net_cost")}
    for s in syms:
        x = session_returns(data.df5[s])
        days = sorted(set(x.day))
        side = rc.costs_for(s, r_cfg).per_turn * 1e4
        ac = autocorr(x.r.to_numpy())
        vr = {q: variance_ratio(x.r, x.day.to_numpy(), q) for q in VR_Q}
        fc = arma_forecast(x.r, x.day.to_numpy(), days)
        x["pred"] = fc["pred"]
        T = x[x.pred.notna()]
        r2 = 1 - float(((T.r - T.pred) ** 2).sum() / (T.r ** 2).sum())
        sign = float((np.sign(T.r) == np.sign(T.pred)).mean() * 100)
        out = {"bars": int(len(x)), "autocorr": ac, "variance_ratio": {str(q): v for q, v in vr.items()},
               "orders_chosen": pd.Series(fc["chosen"]).value_counts().to_dict(),
               "oos_r2_%": round(r2 * 100, 4), "sign_right_%": round(sign, 2),
               "forecast_size_bps": {"median_abs": round(float(T.pred.abs().median()), 3), "p99_abs": round(float(T.pred.abs().quantile(0.99)), 3)}}
        pr, rr, mn, dd = T.pred.to_numpy(), T.r.to_numpy(), T.minute.to_numpy(), T.day.to_numpy()
        for key, thr, cost in (("gross_always", 0.0, 0.0), ("net_always", 0.0, side), ("net_cost", 2 * side, side)):
            b = rule(pr, rr, mn, dd, thr, cost)
            daily = pd.Series(b, index=dd).groupby(level=0).sum() / 1e4
            daily.index = pd.to_datetime(daily.index)
            acc[key].append(daily)
            out[key] = {"total_%": round(float(((1 + daily).prod() - 1) * 100), 2), "bars_in_market_%": round(float((np.abs(b) > 0).mean() * 100), 2),
                        "vs_cash": _paired(daily, daily * 0)}
        res["per_symbol"][s] = out
        progress(f"{s}: lag-1 autocorrelation {ac['acf'][0]:+.4f}, R2 {out['oos_r2_%']:+.3f}%, signs {sign:.1f}%, "
                 f"always-trade gross {out['gross_always']['total_%']:+.1f}% net {out['net_always']['total_%']:+.1f}%, "
                 f"only above cost {out['net_cost']['total_%']:+.2f}% ({out['net_cost']['bars_in_market_%']}% of bars)")
    res["account"] = {}
    for key, parts in acc.items():
        a = pd.concat(parts, axis=1).fillna(0.0).mean(axis=1)
        res["account"][key] = {"total_%": round(float(((1 + a).prod() - 1) * 100), 2), "vs_cash": _paired(a, a * 0)}
    res["lines"] = lines(res)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def lines(res: Dict) -> List[str]:
    out = ["ARIMA / autocorrelation check, 5-minute returns inside the session, 11 symbols; ARMA walk-forward on 386 unseen sessions."]
    for s, x in res["per_symbol"].items():
        vr = ", ".join(f"VR{q} {v['vr']}" for q, v in x["variance_ratio"].items())
        out.append(f"  {s:<5} lag-1 {x['autocorr']['acf'][0]:+.3f} (band ±{x['autocorr']['band95']}), Ljung-Box p {x['autocorr']['ljung_box_p']:.3g}; {vr}; "
                   f"ARMA R2 {x['oos_r2_%']:+.3f}%, signs {x['sign_right_%']}%, forecast size median {x['forecast_size_bps']['median_abs']} bps; "
                   f"trade: gross {x['gross_always']['total_%']:+.1f}%, net {x['net_always']['total_%']:+.1f}%, above cost only {x['net_cost']['total_%']:+.2f}%")
    for k, v in res["account"].items():
        p = v["vs_cash"]
        out.append(f"  all symbols, {k}: total {v['total_%']:+.2f}%, {p['mean_bps']:+.2f} bps/day ({p['ci95_bps'][0]:+.2f} to {p['ci95_bps'][1]:+.2f})")
    return out


def main():
    import yaml
    cfg = yaml.safe_load((mf.ROOT / "config.yaml").read_text(encoding="utf-8"))
    res = run(cfg)
    print("\n".join(res["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
