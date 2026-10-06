"""
Swing-based sizing, step A (backtest). Diagnosis only: nothing here changes the bot.

    .venv-ml/Scripts/python -m autobot.swing_sizing_study     # data/ml/study_swing_sizing.json

Question: does sizing each trade by the expected size of the next hour's move (the ML study's
walk-forward trees forecast, data/ml/pred_trees.pkl.gz) give better results per unit of risk than
today's fixed size, or than simply sizing by recent volatility (ATR)?

Four ways, all on the same trades (each strategy x symbol the bot can run: defaults + live jobs,
long only, no quick re-entry; plus "hold from the open to the close" per symbol), same 386 unseen
sessions, same costs:
  fixed       today: the same amount on every trade
  atr_daily   the bot's existing 'risk_balanced' idea: amount / average daily range of the last 20 days
  atr_bars    amount / average 5-minute range of the last 20 bars
  swing       amount / the trees' forecast of the next hour's swing
For the last three, size = clip(normal / now, 0.25, 2.0), where 'normal' is the symbol's median of the
same measure over the previous 20 sessions (known at the time). The size is set when the trade opens
(the decision bar) and kept until it closes, so resizing never adds trading costs.

Fair comparison: each method's daily results are scaled to the same average amount invested as
'fixed', then compared on: daily ups and downs (standard deviation), worst day, and result per unit
of risk (mean / standard deviation, yearly). 95% ranges by resampling days (paired, 2,000 draws).
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
from . import ml_study as ms
from . import recalibrate as rc
from . import trade_rules as tr
from .strategies import STRATEGIES, get_signal

NY = mf.NY
OUT = mf.OUT_DIR / "study_swing_sizing.json"
METHODS = ("fixed", "atr_daily", "atr_bars", "swing")
LO, HI, NORMAL_SESSIONS = 0.25, 2.0, 20
RULES = {"no_quick_reentry": True}


def relative_size(x: pd.Series, day: np.ndarray, sessions: int = NORMAL_SESSIONS) -> pd.Series:
    """clip(normal / x, LO, HI); normal = median of the daily medians of x over the previous
    `sessions` sessions (earlier sessions only)."""
    dm = x.groupby(day).median()
    normal = dm.rolling(sessions, min_periods=5).median().shift(1)
    nrm = normal.reindex(day).to_numpy()
    w = nrm / x.to_numpy()
    return pd.Series(np.clip(w, LO, HI), index=x.index)


def daily_atr_pct(df: pd.DataFrame, days: int = 20) -> pd.Series:
    """Per bar: average true range of the previous `days` completed sessions, % of their close."""
    day = np.asarray(df.index.tz_convert(NY).date)
    d = pd.DataFrame({"h": df.high.groupby(day).max(), "l": df.low.groupby(day).min(), "c": df.close.groupby(day).last()})
    trd = pd.concat([d.h - d.l, (d.h - d.c.shift()).abs(), (d.l - d.c.shift()).abs()], axis=1).max(axis=1)
    atr = (trd.rolling(days).mean() / d.c * 100).shift(1)            # completed days only
    return pd.Series(atr.reindex(day).to_numpy(), index=df.index)


def sized(base: pd.Series, w: pd.Series) -> pd.Series:
    """Position = base x the size at the bar the trade opened (kept until it closes). The bot sells
    everything before each close, so every session opens a new trade with a new size. A missing
    size (no forecast yet) counts as 1."""
    b = base.to_numpy(float)
    day = np.asarray(base.index.tz_convert(NY).date)
    wv = np.nan_to_num(w.to_numpy(float), nan=1.0)
    out = np.zeros(len(b))
    cur = 1.0
    for i in range(len(b)):
        if b[i] != 0 and (i == 0 or b[i - 1] == 0 or day[i] != day[i - 1]):
            cur = wv[i]
        out[i] = b[i] * cur if b[i] != 0 else 0.0
    return pd.Series(out, index=base.index)


# ----------------------------------------------------------------- scoring
def stats(d: pd.Series) -> Dict:
    sd = d.std(ddof=1)
    return {"total_%": round(float(((1 + d).prod() - 1) * 100), 2), "mean_bps": round(float(d.mean() * 1e4), 3),
            "sd_bps": round(float(sd * 1e4), 3), "worst_day_%": round(float(d.min() * 100), 3),
            "per_risk_yearly": round(float(d.mean() / sd * math.sqrt(252)), 3) if sd > 0 else None}


def boot(a: pd.Series, b: pd.Series, n: int = 2000, seed: int = 0) -> Dict:
    """Paired by day: per-risk (a) minus per-risk (b), and sd(a)/sd(b) - 1, with 95% ranges."""
    rng = np.random.default_rng(seed)
    A, B = a.to_numpy(), b.to_numpy()
    k = len(A)
    pr = lambda x: x.mean() / x.std(ddof=1) * math.sqrt(252)  # noqa: E731
    d_pr, d_sd = [], []
    for _ in range(n):
        i = rng.integers(0, k, k)
        d_pr.append(pr(A[i]) - pr(B[i]))
        d_sd.append(A[i].std(ddof=1) / B[i].std(ddof=1) - 1)
    q = lambda x: [round(float(np.percentile(x, 2.5)), 3), round(float(np.percentile(x, 97.5)), 3)]  # noqa: E731
    return {"per_risk_diff": round(float(pr(A) - pr(B)), 3), "per_risk_diff_ci95": q(d_pr),
            "sd_change_%": round(float((A.std(ddof=1) / B.std(ddof=1) - 1) * 100), 1), "sd_change_ci95_%": [round(x * 100, 1) for x in q(d_sd)]}


def run(cfg: Dict, symbols: Optional[List[str]] = None, progress=print) -> Dict:
    syms, data, P = ms.panel(symbols)
    P = P.merge(pd.read_pickle(mf.OUT_DIR / "pred_trees.pkl.gz"), on=["symbol", "t"], how="left")
    days = sorted(P.day.unique())
    test_days = {d for a, b in ms.windows(days) for d in days if a <= d <= b}
    r = rc.settings(cfg)
    fm = int(r["flatten_min"]) or 5
    rows = []
    for s in syms:
        df = data.bars(s, "5Min")
        day = np.asarray(df.index.tz_convert(NY).date)
        test = np.isin(day, list(test_days))
        p = P[P.symbol == s].set_index("t")
        weights = {"fixed": pd.Series(1.0, index=df.index),
                   "atr_daily": relative_size(daily_atr_pct(df), day),
                   "atr_bars": relative_size(p.atr_pct.reindex(df.index), day),
                   "swing": relative_size(p.pred_swing.reindex(df.index), day)}
        costs = rc.costs_for(s, r)
        sim = lambda sig: cs.sim(df, data.cal[s], "sizing", {}, costs, "5Min", fm, signal=sig)["frame"]  # noqa: E731
        pairs = [(n, STRATEGIES[n].defaults, "default") for n in cs.strategies_for("5Min", r)]
        pairs += [(j["strategy"], j.get("params") or {}, f"live job {j['name']}") for j in cfg["live"].get("jobs") or []
                  if j.get("enabled", True) and j["symbols"][0] == s]
        sigs = []
        for name, params, kind in pairs:
            try:
                sigs.append((name, kind, tr.apply(get_signal(name, df, params, False), df, RULES)))
            except Exception:  # noqa: BLE001
                continue
        sigs.append(("hold open to close", "hold", pd.Series(test.astype(float), index=df.index)))
        for name, kind, base in sigs:
            base = base.where(test, 0.0)
            row = {"symbol": s, "strategy": name, "kind": kind}
            for m in METHODS:
                f = sim(sized(base, weights[m]))
                f = f[test]
                row[m] = cs._daily(f.ret)
                row[m + "_exposure"] = float(f.position.abs().mean())
            rows.append(row)
        progress(f"{s}: {len(sigs)} trade sets")

    def group(sel: List[Dict]) -> Dict:
        acc = {m: pd.concat([x[m] for x in sel], axis=1).mean(axis=1).sort_index() for m in METHODS}
        expo = {m: float(np.mean([x[m + "_exposure"] for x in sel])) for m in METHODS}
        matched = {m: acc[m] * (expo["fixed"] / expo[m]) if expo[m] > 0 else acc[m] for m in METHODS}
        out = {"trade_sets": len(sel), "days": len(acc["fixed"]),
               "average_amount_vs_fixed": {m: round(expo[m] / expo["fixed"], 3) for m in METHODS},
               "as_traded": {m: stats(acc[m]) for m in METHODS},
               "same_amount": {m: stats(matched[m]) for m in METHODS},
               "swing_vs_fixed": boot(matched["swing"], matched["fixed"]),
               "swing_vs_atr_bars": boot(matched["swing"], matched["atr_bars"]),
               "swing_vs_atr_daily": boot(matched["swing"], matched["atr_daily"]),
               "atr_bars_vs_fixed": boot(matched["atr_bars"], matched["fixed"])}
        return out

    strat = [x for x in rows if x["kind"] != "hold"]
    live = [x for x in rows if x["kind"].startswith("live job")]
    hold = [x for x in rows if x["kind"] == "hold"]
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": str(data.as_of), "symbols": syms,
           "size_limits": [LO, HI], "normal_sessions": NORMAL_SESSIONS, "test_sessions": len(test_days),
           "first_test_day": str(min(test_days)),
           "strategies": group(strat), "live_jobs": group(live) if live else None, "hold_open_to_close": group(hold)}
    res["lines"] = lines(res)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def lines(res: Dict) -> List[str]:
    out = [f"Sizing by expected swing vs fixed vs ATR, {res['test_sessions']} unseen sessions from {res['first_test_day']}, after costs, "
           f"scaled to the same average amount invested."]
    for key, title in (("strategies", "Strategies (defaults + live jobs)"), ("live_jobs", "Live jobs only"), ("hold_open_to_close", "Holding open to close")):
        g = res.get(key)
        if not g:
            continue
        out.append(f"{title}: {g['trade_sets']} trade sets, {g['days']} days")
        for m in METHODS:
            s = g["same_amount"][m]
            out.append(f"   {m:<10} total {s['total_%']:+7.2f}%  daily ups/downs {s['sd_bps']:6.1f} bps  worst day {s['worst_day_%']:+6.2f}%  "
                       f"per risk {s['per_risk_yearly']:+.2f}  (amount as traded x{g['average_amount_vs_fixed'][m]})")
        for k in ("swing_vs_fixed", "swing_vs_atr_bars", "swing_vs_atr_daily", "atr_bars_vs_fixed"):
            b = g[k]
            out.append(f"   {k}: per risk {b['per_risk_diff']:+.3f} ({b['per_risk_diff_ci95'][0]:+.3f} to {b['per_risk_diff_ci95'][1]:+.3f}); "
                       f"ups/downs {b['sd_change_%']:+.1f}% ({b['sd_change_ci95_%'][0]:+.1f} to {b['sd_change_ci95_%'][1]:+.1f})")
    return out


def main():
    import yaml
    cfg = yaml.safe_load((mf.ROOT / "config.yaml").read_text(encoding="utf-8"))
    res = run(cfg)
    print("\n".join(res["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
