"""
Does an inflation / interest-rate filter add anything to the core's volatility targeting (option B)?
Diagnosis only: nothing here changes the bot.

    .venv-ml/Scripts/python -m autobot.core_combo_study      # data/ml/study_core_combo.json

Same data, days, band rule and costs as autobot.vol_target_study (S&P 500 daily closes from FRED, scored
from September 2019, decided at a close and traded a day later, only when more than 5 points from the
target, 2 bps). Fixed before the run:
  B            the live rule: 30% x normal / EWMA volatility, kept between 15% and 45%
  B + inflation  B's target halved (never below 10%) while CPI inflation (year on year) is higher than
               3 months earlier; CPI for month M counts from the 1st of month M+2 (it comes out mid M+1)
  B + rates    B's target halved while the 10-year yield (DGS10, from the next day) is above its 12-month
               (252-day) average
  B + either   halved while either is rising
  plain 30% + either   for reference: the filter on the plain core
Compared with B on the same days, also at the same average amount invested.
Caveat: 7 years with one long inflation and rate shock (2022), which the filters were designed around.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict

import numpy as np
import pandas as pd

from . import vol_target_study as vt
from .macro_study import DIR

OUT = vt.OUT.parent / "study_core_combo.json"
CUT, FLOOR = 0.5, 0.10


def fred(n: str) -> pd.Series:
    f = pd.read_csv(DIR / f"{n}.csv")
    s = pd.to_numeric(f.iloc[:, 1], errors="coerce")
    s.index = pd.to_datetime(f.iloc[:, 0])
    return s.dropna()


def inflation_rising(index: pd.DatetimeIndex) -> pd.Series:
    cpi = fred("CPIAUCSL")
    yoy = cpi / cpi.shift(12) - 1
    rising = (yoy > yoy.shift(3)).where(yoy.shift(3).notna())
    avail = rising.copy()
    avail.index = avail.index + pd.offsets.MonthBegin(2)                 # month M usable from the 1st of M+2
    return avail.reindex(index, method="ffill").fillna(False).astype(bool)


def rates_rising(index: pd.DatetimeIndex) -> pd.Series:
    y = fred("DGS10")
    up = (y > y.rolling(252).mean()).where(y.rolling(252).mean().notna())
    up.index = up.index + pd.Timedelta(days=1)                            # known the next day
    return up.reindex(index, method="ffill").fillna(False).astype(bool)


def cut(target: pd.Series, flag: pd.Series) -> pd.Series:
    return target.where(~flag, (target * CUT).clip(lower=FLOOR))


def run(progress=print) -> Dict:
    df = vt.load()
    T = df.index >= df.index[vt.TRAIN]
    b = vt.target_weight(vt.ewma_var(df.r))
    inf, rat = inflation_rising(df.index), rates_rising(df.index)
    either = inf | rat
    targets = {"B": b, "B + inflation": cut(b, inf), "B + rates": cut(b, rat), "B + either": cut(b, either),
               "plain 30%": pd.Series(vt.BASE, index=df.index), "plain 30% + either": cut(pd.Series(vt.BASE, index=df.index), either)}
    targets = {k: v.where(T) for k, v in targets.items()}
    frames = {k: vt.replay(df, t)[T] for k, t in targets.items()}
    base = frames["B"]
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "test": [str(df.index[T][0].date()), str(df.index[-1].date())],
           "share_of_days_flagged_%": {"inflation rising": round(float(inf[T].mean() * 100), 1), "rates rising": round(float(rat[T].mean() * 100), 1),
                                       "either": round(float(either[T].mean() * 100), 1)},
           "as_traded": {k: vt.stats(f) for k, f in frames.items()}, "same_amount_as_B": {}, "vs_B": {},
           "crises_%": {name: {k: round(float(((1 + f.ret[a:z]).prod() - 1) * 100), 2) for k, f in frames.items()}
                        for name, (a, z) in vt.CRISES.items()}}
    for k, f in frames.items():
        ratio = base.weight.mean() / f.weight.mean()
        g = f.copy()
        g["ret"], g["weight"] = f.ret * ratio, f.weight * ratio
        out["same_amount_as_B"][k] = vt.stats(g)
        if k != "B":
            out["vs_B"][k] = vt.boot(g.ret, base.ret)
    out["lines"] = lines(out)
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out


def lines(o: Dict) -> list:
    L = [f"Core: B vs B with an inflation / rate filter, {o['test'][0]} to {o['test'][1]}; days flagged {o['share_of_days_flagged_%']}"]
    for k, s in o["as_traded"].items():
        m = o["same_amount_as_B"][k]
        L.append(f"  {k:<20} as traded {s['yearly_%']:+6.2f}%/yr, max drop {s['max_drop_%']:+6.2f}%, avg weight {s['avg_weight_%']}%, "
                 f"{s['trades_per_year']} trades/yr | same amount as B: {m['yearly_%']:+6.2f}%/yr, per risk {m['per_risk']:+.3f}, max drop {m['max_drop_%']:+.2f}%")
    for k, v in o["vs_B"].items():
        L.append(f"  {k} vs B (same amount): per risk {v['per_risk_diff']:+.3f} ({v['per_risk_ci95'][0]:+.3f} to {v['per_risk_ci95'][1]:+.3f}); "
                 f"max drop {v['max_drop_diff_points']:+.2f} pts ({v['max_drop_ci95_points'][0]:+.2f} to {v['max_drop_ci95_points'][1]:+.2f})")
    for name, row in o["crises_%"].items():
        L.append(f"  {name}: " + ", ".join(f"{k} {v:+.2f}%" for k, v in row.items()))
    return L


def main():
    o = run()
    print("\n".join(o["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
