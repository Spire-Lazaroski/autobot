"""
Gap-and-go on a longer, wider sample (confirmation of section 19's only positive result).
Diagnosis only: nothing here changes the bot.

    .venv/Scripts/python -m autobot.gap_long_study        # data/gap_long_study.json

The rule is exactly the one from batch 1 (autobot.batch1_study.gap_entries "go_large"), fixed before
this run: the day opens at least 1% away from yesterday's close and the first 5-minute bar closes
further in the gap's direction -> enter at that close in the gap's direction, stop just beyond the
first bar's other end, sell at the 15:50 bar's close; costs 2 bps per side (twice in the close-out bar).

New data the rule has never seen: 5-minute bars from Alpaca's consolidated feed (SIP, split- and
dividend-adjusted), regular hours, from October 2016 to 20 September 2024 (the day before the 2-year
window used so far starts), for the 24 stock and ETF symbols of the bot's universe settings (crypto
and SPCX, listed recently, left out). Caveat: that list was chosen in 2026 and holds some of the decade's
big winners (NVDA, META, AMD, LLY), which flatters any long rule; results are therefore also shown
per bucket, per year and for the short side.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import yaml

from . import batch1_study as b1
from . import cost_study as cs
from . import recalibrate as rc

NY = "America/New_York"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "gap_long_study.json"
CACHE = ROOT / "data" / "gap_long"
START, END = datetime(2016, 10, 1, tzinfo=timezone.utc), datetime(2024, 9, 21, tzinfo=timezone.utc)


def symbols(cfg: Dict) -> Dict[str, str]:
    out = {}
    for bucket, b in cfg["universe"]["buckets"].items():
        for s in b.get("candidates") or []:
            if "/" not in s and s != "SPCX":
                out[s] = bucket
    return out


def load(sym: str, progress=print) -> pd.DataFrame:
    """5-minute SIP bars, regular hours, cached in data/gap_long (downloaded once, year by year)."""
    from autobot.data import _download
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{sym}.pkl.gz"
    if f.exists():
        return pd.read_pickle(f)
    parts = []
    for y in range(START.year, END.year + 1):
        a, b = max(START, datetime(y, 1, 1, tzinfo=timezone.utc)), min(END, datetime(y + 1, 1, 1, tzinfo=timezone.utc))
        try:
            parts.append(_download(sym, "5Min", a, b, "sip"))
        except RuntimeError:
            continue
    df = pd.concat(parts)
    df = df[~df.index.duplicated()].sort_index()
    ny = df.index.tz_convert(NY)
    m = ny.hour * 60 + ny.minute
    df = df[(m >= 570) & (m < 960) & (ny.weekday < 5)]
    df.to_pickle(f)
    progress(f"  {sym}: {len(df):,} bars {df.index[0].date()} to {df.index[-1].date()}")
    return df


def run(progress=print) -> Dict:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    r = rc.settings(cfg)
    syms = symbols(cfg)
    trades: Dict[str, List[Dict]] = {}
    days_all = set()
    for s, bucket in syms.items():
        df = load(s, progress)
        B = b1.Bars(df, rc.costs_for(s, r).per_turn)
        B.test_days = set(B.days)
        days_all |= B.test_days
        trades[s] = b1.simulate(B, b1.gap_entries(B, "go_large"))
    test_days = sorted(days_all)
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "symbols": syms,
           "period": [str(test_days[0]), str(test_days[-1])], "sessions": len(test_days),
           "all": b1.summarize(trades, test_days)}
    res["by_bucket"] = {bk: b1.summarize({s: trades[s] for s in syms if syms[s] == bk}, test_days)["both"]
                        for bk in sorted(set(syms.values()))}
    by_year = {}
    for y in sorted({d.year for d in test_days}):
        dy = [d for d in test_days if d.year == y]
        by_year[str(y)] = b1.summarize({s: [t for t in ts if t["day"].year == y] for s, ts in trades.items()}, dy)
    res["by_year"] = {y: {k: v[k] for k in ("both", "long", "short")} for y, v in by_year.items()}
    res["per_symbol"] = {s: b1.summarize({s: trades[s]}, test_days)["both"] for s in syms}
    res["lines"] = lines(res)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def _f(x: Dict) -> str:
    if not x.get("trades"):
        return "no trades"
    ci = x["ci95_bps"]
    return f"{x['trades']} trades, win {x['win_rate_%']}%, {x['net_bps_per_trade']:+.1f} bps/trade ({ci[0]:+.1f} to {ci[1]:+.1f})"


def lines(res: Dict) -> List[str]:
    a = res["all"]
    out = [f"Gap-and-go, {len(res['symbols'])} symbols, {res['period'][0]} to {res['period'][1]} ({res['sessions']} sessions), SIP 5-minute, after costs.",
           f"  all: {_f(a['both'])}; per day vs cash {a['both']['vs_cash']['mean_bps']:+.2f} bps ({a['both']['vs_cash']['ci95_bps'][0]:+.2f} to {a['both']['vs_cash']['ci95_bps'][1]:+.2f})",
           f"  long: {_f(a['long'])}", f"  short: {_f(a['short'])}"]
    for bk, x in res["by_bucket"].items():
        out.append(f"  bucket {bk}: {_f(x)}")
    for y, x in res["by_year"].items():
        out.append(f"  {y}: {_f(x['both'])} | long {x['long'].get('net_bps_per_trade', 'n/a')} short {x['short'].get('net_bps_per_trade', 'n/a')}")
    return out


def main():
    res = run()
    print("\n".join(res["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
