"""
Machine-learning study, phase 4: does the economy add anything? The phase 2 trees are re-run with
economy inputs added (same windows, settings, costs) and compared with the trees without them.
Diagnosis only.

    .venv-ml/Scripts/python -m autobot.ml_macro          # data/ml/study_economy.json

Economy inputs per session, each only from the day it was public (files in data/macro, see
autobot.macro_study.download):
  daily   (FRED, value of day d used from day d+1)
    curve_10y3m     10-year minus 3-month Treasury yield (T10Y3M)
    sp500_20d       S&P 500 change over the last 20 trading days, %
    sp500_vs_200d   S&P 500 vs its 200-day average, %
  monthly (FRED, month M used from the 8th of month M+1, after the jobs report)
    curve_monthly   10-year minus 3-month bill (GS10 - TB3MS)
    credit_spread   Baa minus Aaa corporate bond yields
    unemp_3m        unemployment rate change over 3 months (UNRATE)
    sahm            Sahm recession indicator as known at the time (SAHMREALTIME)
  annual  (Global Macro Database, year Y used from July of Y+1)
    gmd_infl, gmd_rgdp_growth, gmd_unemp
Over a 2-year sample the monthly and annual ones barely move, so the trees can at most use them
as a backdrop; that is part of what this phase measures.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import ml_features as mf
from . import ml_study as ms
from .macro_study import DIR, GMD_VERSION

OUT = mf.OUT_DIR / "study_economy.json"
ECON = ["curve_10y3m", "sp500_20d", "sp500_vs_200d", "curve_monthly", "credit_spread", "unemp_3m", "sahm",
        "gmd_infl", "gmd_rgdp_growth", "gmd_unemp"]
MONTHLY_DAY = 8


def _fred(name: str) -> pd.Series:
    f = pd.read_csv(DIR / f"{name}.csv")
    s = pd.to_numeric(f.iloc[:, 1], errors="coerce")
    s.index = pd.to_datetime(f.iloc[:, 0])
    return s.dropna()


def _as_of(values: pd.Series, available: pd.DatetimeIndex, days: List[date]) -> np.ndarray:
    """For each session day, the latest value whose availability date is on or before that day."""
    s = pd.Series(values.to_numpy(), index=available).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    pos = np.searchsorted(s.index.values, pd.DatetimeIndex(days).values, side="right") - 1
    return np.where(pos >= 0, s.to_numpy()[np.maximum(pos, 0)], np.nan)


def daily_available(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return idx + pd.Timedelta(days=1)


def monthly_available(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return (idx + pd.offsets.MonthBegin(1)) + pd.Timedelta(days=MONTHLY_DAY - 1)


def economy(days: List[date], fred=_fred, gmd: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    out = pd.DataFrame(index=pd.Index(days, name="day"))
    c = fred("T10Y3M")
    out["curve_10y3m"] = _as_of(c, daily_available(c.index), days)
    sp = fred("SP500")
    for name, s in (("sp500_20d", (sp / sp.shift(20) - 1) * 100), ("sp500_vs_200d", (sp / sp.rolling(200).mean() - 1) * 100)):
        s = s.dropna()
        out[name] = _as_of(s, daily_available(s.index), days)
    gs, tb = fred("GS10"), fred("TB3MS")
    cm = (gs - tb).dropna()
    out["curve_monthly"] = _as_of(cm, monthly_available(cm.index), days)
    cr = (fred("BAA") - fred("AAA")).dropna()
    out["credit_spread"] = _as_of(cr, monthly_available(cr.index), days)
    u = fred("UNRATE")
    u3 = u.diff(3).dropna()
    out["unemp_3m"] = _as_of(u3, monthly_available(u3.index), days)
    sh = fred("SAHMREALTIME")
    out["sahm"] = _as_of(sh, monthly_available(sh.index), days)
    g = gmd if gmd is not None else pd.read_csv(DIR / f"gmd_usa_{GMD_VERSION}.csv").set_index("year")
    g = g.sort_index()
    avail = pd.DatetimeIndex([pd.Timestamp(int(y) + 1, 7, 1) for y in g.index])
    out["gmd_infl"] = _as_of(g.infl, avail, days)
    out["gmd_rgdp_growth"] = _as_of(g.rGDP.pct_change() * 100, avail, days)
    out["gmd_unemp"] = _as_of(g.unemp, avail, days)
    return out


def run(cfg: Dict, symbols: Optional[List[str]] = None, progress=print) -> Dict:
    syms, data, P = ms.panel(symbols)
    days = sorted(P.day.unique())
    E = economy(days)
    P = P.join(E, on="day")
    progress(f"{len(P):,} bars; economy inputs per session, e.g. last day: " +
             ", ".join(f"{k} {v:.2f}" for k, v in E.iloc[-1].items() if pd.notna(v)))
    tp = mf.OUT_DIR / "pred_trees.pkl.gz"
    if not tp.exists():                                   # the phase 2 trees (no economy), same windows
        P["pred_ret"] = ms.walk_predict(P, "fwd_ret", mf.COLUMNS, progress)
        P["pred_swing"] = ms.walk_predict(P, "fwd_swing", mf.COLUMNS, progress)
        P[["symbol", "t", "pred_ret", "pred_swing"]].to_pickle(tp)
        P = P.drop(columns=["pred_ret", "pred_swing"])
    base = pd.read_pickle(tp).rename(columns={"pred_ret": "base_ret", "pred_swing": "base_swing"})
    P = P.merge(base, on=["symbol", "t"], how="left")
    cols = mf.COLUMNS + ECON
    P["econ_ret"] = ms.walk_predict(P, "fwd_ret", cols, progress)
    P["econ_swing"] = ms.walk_predict(P, "fwd_swing", cols, progress)
    test_days = {d for a, b in ms.windows(days) for d in days if a <= d <= b}
    T = P[P.day.isin(test_days)]
    sw = T.dropna(subset=["fwd_swing", "econ_swing", "base_swing"])
    rt = T.dropna(subset=["fwd_ret", "econ_ret", "base_ret"])
    from .ml_lstm import paired_error
    swing = {"with_economy": ms.forecast_scores(sw.fwd_swing, sw.econ_swing, sw.day),
             "without": ms.forecast_scores(sw.fwd_swing, sw.base_swing, sw.day),
             "with_minus_without": paired_error(sw.fwd_swing, sw.econ_swing, sw.base_swing, sw.day),
             "importance": ms.importance(P, "fwd_swing", cols)}
    ret = {"with_economy": ms.forecast_scores(rt.fwd_ret, rt.econ_ret, rt.day),
           "without": ms.forecast_scores(rt.fwd_ret, rt.base_ret, rt.day),
           "importance": ms.importance(P, "fwd_ret", cols)}
    econ_share = {k: round(sum(v for c, v in d["importance"].items() if c in ECON), 1) for k, d in (("return", ret), ("swing", swing))}
    progress(f"swing error with economy {swing['with_economy']['mae']} vs without {swing['without']['mae']}")
    entry, exit_ = ms.trade_tests(P, data, cfg, syms, test_days, "econ_ret", progress)
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": str(data.as_of), "symbols": syms,
           "economy_inputs": ECON, "economy_last_day": {k: (None if pd.isna(v) else round(float(v), 3)) for k, v in E.iloc[-1].items()},
           "distinct_values_over_sample": {k: int(E[k].nunique()) for k in ECON},
           "test_sessions": len(test_days), "first_test_day": str(min(test_days)),
           "return_forecast": ret, "swing": swing, "economy_share_of_top10_importance_%": econ_share,
           "entry": entry, "exit": exit_}
    res["lines"] = lines(res)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def lines(res: Dict) -> List[str]:
    rf, sw, en, ex = res["return_forecast"], res["swing"], res["entry"], res["exit"]
    ci = lambda p: f"{p['mean_bps']:+.2f} bps/day ({p['ci95_bps'][0]:+.2f} to {p['ci95_bps'][1]:+.2f}, {'clear' if p['significant'] else 'not clear'})"  # noqa: E731
    pe = sw["with_minus_without"]
    return [
        f"Trees with vs without economy inputs, walk-forward from {res['first_test_day']} ({res['test_sessions']} unseen sessions), after costs.",
        f"Next-hour return: rank correlation with {rf['with_economy']['daily_rank_corr']:+.4f} ({rf['with_economy']['daily_rank_corr_ci95'][0]:+.4f} to "
        f"{rf['with_economy']['daily_rank_corr_ci95'][1]:+.4f}) vs without {rf['without']['daily_rank_corr']:+.4f}; sign right {rf['with_economy']['sign_right_%']}% vs {rf['without']['sign_right_%']}%",
        f"1. exit: strategies alone {ex['strategies_test_return_%']:+.2f}% vs with exit {ex['with_ml_exit_%']:+.2f}%; {ci(ex['ml_exit_vs_none'])}; "
        f"pairs better {ex['pairs_clearly_better']} / worse {ex['pairs_clearly_worse']} of {ex['pairs']}",
        f"2. swing error with {sw['with_economy']['mae']} vs without {sw['without']['mae']}: difference {pe['mean']:+.4f} ({pe['ci95'][0]:+.4f} to {pe['ci95'][1]:+.4f}), "
        f"better on {pe['days_1_better_%']}% of days",
        f"3. entry: {en['ml_test_return_%']:+.2f}% vs cash 0 / holding {en['hold_open_to_close_%']:+.2f}%; vs cash {ci(en['ml_vs_cash'])}; {en['trades']} trades",
        f"Economy share of the 10 most-used inputs: return {res['economy_share_of_top10_importance_%']['return']}%, swing {res['economy_share_of_top10_importance_%']['swing']}%",
        "Distinct values over the 2 years: " + ", ".join(f"{k} {v}" for k, v in res["distinct_values_over_sample"].items()),
    ]


def main():
    import yaml
    cfg = yaml.safe_load((mf.ROOT / "config.yaml").read_text(encoding="utf-8"))
    res = run(cfg)
    print("\n".join(res["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
