"""
Same-money study: the daily strategies of daily_study compared with buy and hold holding the SAME
average amount of money, and at the same risk. Diagnosis only: nothing here changes config.yaml,
the bot or recalibration.

    python -m autobot.same_money_study              # writes data/same_money_study.json
    python -m autobot.same_money_study --summary    # only reprint the saved summary

Why: daily_study compared every strategy with buy and hold fully invested every day, while a
strategy is in the market only part of the time (rsi_reversion about 10%). Uninvested money earns
nothing in these tests, so part of "holding wins" was simply more money at work. Here, for each
strategy (same data, fills, costs and test period as daily_study):

  same money   buy and hold scaled to the strategy's average time in the market (e.g. 35% in
               the stocks, 65% cash every day). The daily difference strategy minus this, with a
               95% range, says whether the timing itself earns anything.
  same risk    buy and hold scaled to the strategy's own volatility (the Sharpe comparison in
               money terms): what holding would have made taking the same risk.
  per dollar   the strategy's yearly return divided by its time in the market, next to holding's.

Plus the "money line": buy and hold at 10%, 20%, ... 100% invested (rest cash), so each strategy
can be placed against it: above the line means better than holding the same amount.
Cash earns 0% here, as in every earlier study (a money-market rate would lift the part-invested
rows, including the scaled holds, by the same amount per dollar of cash).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from . import cost_study as cs
from . import daily_study as ds

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "same_money_study.json"
LINE = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


def portfolio(cfg: Dict, symbols: Optional[Sequence[str]] = None, data=None):
    """Per-variant portfolio daily returns and average position (equal weight, normal costs),
    exactly as daily_study.run builds them."""
    syms = list(symbols or cs.universe_symbols())
    data = data or {s: ds.load(s) for s in syms}
    syms = [s for s in syms if s in data and len(data[s]) > ds.WARMUP + ds.TEST]
    start = max(data[s].index[ds.WARMUP] for s in syms)
    runs = {s: ds.symbol_runs(s, data[s], cfg, 1.0) for s in syms}
    for name, trend in ((f"relative strength top {ds.TOP_N}", False), (f"relative strength top {ds.TOP_N} + trend", True)):
        for s, f in ds.relative_strength({s: data[s] for s in syms}, cfg, 1.0, trend).items():
            runs[s][name] = f
    out = {}
    for v in dict.fromkeys(v for s in syms for v in runs[s]):
        if v.startswith("news:"):
            continue  # only 2 years and 5 symbols; compared with their own base in daily_study
        R = pd.DataFrame({s: runs[s][v].ret for s in syms if v in runs[s]}).sort_index()
        R = R[R.index >= start].fillna(0.0)
        P = pd.DataFrame({s: runs[s][v].position for s in syms if v in runs[s]}).reindex(R.index).fillna(0.0)
        out[v] = (R.mean(axis=1), P.mean(axis=1))
    meta = {"symbols": syms, "test_from": str(start.tz_convert(ds.NY).date()),
            "test_to": str(max(data[s].index[-1] for s in syms).tz_convert(ds.NY).date())}
    return out, meta


def compare(r: pd.Series, p: pd.Series, bh: pd.Series) -> Dict:
    bh = bh.reindex(r.index).fillna(0.0)
    share = float(p.mean())
    st = ds.stats(r, p)
    row = {**st}
    same_money = bh * share
    sm = ds.stats(same_money)
    row["same_money"] = {"hold_yearly_%": sm["yearly_%"], "hold_worst_drop_%": sm["worst_drop_%"],
                         "diff": ds.paired(r, same_money)}
    k = float(r.std() / bh.std()) if bh.std() > 0 else 0.0
    same_risk = bh * k
    sr = ds.stats(same_risk)
    row["same_risk"] = {"hold_share_%": round(k * 100, 1), "hold_yearly_%": sr["yearly_%"],
                        "hold_worst_drop_%": sr["worst_drop_%"], "diff": ds.paired(r, same_risk)}
    bh_y = ds.stats(bh)["yearly_%"]
    row["per_dollar_yearly_%"] = round(st["yearly_%"] / share, 1) if share > 0.01 and st["yearly_%"] is not None else None
    row["hold_per_dollar_yearly_%"] = bh_y
    return row


def run(cfg: Dict, symbols: Optional[Sequence[str]] = None, data=None) -> Dict:
    port, meta = portfolio(cfg, symbols, data)
    bh = port[ds.BH][0]
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), **meta}
    res["money_line"] = [{"invested_%": int(x * 100), **ds.stats(bh * x)} for x in LINE]
    rows = []
    for v, (r, p) in port.items():
        if v == ds.BH:
            continue
        rows.append({"variant": v, **compare(r, p, bh)})
    rows.sort(key=lambda x: -x["same_money"]["diff"]["mean_bps"])
    res["rows"] = rows
    res["summary"] = summary(res)
    return res


def summary(res: Dict) -> List[str]:
    full = res["money_line"][-1]
    L = [f"{len(res['symbols'])} stocks/ETFs, equal weight; {res['test_from']} to {res['test_to']}; next-open fills, open costs 2x; cash earns 0%.",
         f"Buy and hold fully invested: {full['yearly_%']:+.1f}% a year, worst drop {full['worst_drop_%']:.1f}%, Sharpe {full['sharpe']:.2f}.",
         "Money line (buy and hold at X% invested): " + ", ".join(f"{m['invested_%']}% -> {m['yearly_%']:+.1f}%/{m['worst_drop_%']:.0f}%" for m in res["money_line"])]
    for r in res["rows"]:
        sm, sr = r["same_money"], r["same_risk"]
        d1, d2 = sm["diff"], sr["diff"]
        L.append(f"{r['variant']}: {r['yearly_%']:+.1f}%/yr, drop {r['worst_drop_%']:.0f}%, in the market {r['in_market_%']:.0f}% | "
                 f"same money hold {sm['hold_yearly_%']:+.1f}%/yr (drop {sm['hold_worst_drop_%']:.0f}%): {d1['mean_bps']:+.2f} bps/day "
                 f"({d1['ci95_bps'][0]:+.2f} to {d1['ci95_bps'][1]:+.2f}){' CLEAR' if d1['significant'] else ''} | "
                 f"same risk hold ({sr['hold_share_%']:.0f}% invested) {sr['hold_yearly_%']:+.1f}%/yr: {d2['mean_bps']:+.2f} "
                 f"({d2['ci95_bps'][0]:+.2f} to {d2['ci95_bps'][1]:+.2f}){' CLEAR' if d2['significant'] else ''}")
    return L


def main():
    import argparse
    import sys
    import yaml
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Same-money comparison of the daily strategies (diagnosis only).")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    if a.summary:
        print("\n".join(json.loads(OUT.read_text(encoding="utf-8"))["summary"]))
        return
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    res = run(cfg, [s for s in a.symbols.split(",") if s] or None)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print("\n".join(res["summary"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
