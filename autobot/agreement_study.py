"""
Agreement test: trade only when several strategies agree (the user's idea, 2026-10-07).
Diagnosis only: nothing here changes the bot.

    .venv/Scripts/python -m autobot.agreement_study       # data/agreement_study.json

Per symbol and 5-minute bar, every 5-minute strategy (default settings) gives its position.
Combined signals:
  count k      long when at least k strategies are long (k = 3, 5, 7)
  families F   long when at least F different families each have a strategy long
               (families: trend, breakout, mean reversion, intraday; F = 2, 3, 4)
  ... and the same with shorts allowed (short when as many agree on short and the long side doesn't).
Each combined signal goes through "no quick re-entry" (as live) and is replayed like the bot, after
costs, flat before the close, on the same 386 unseen sessions as the ML study (sessions 121 on).
Compared per day with the single strategies (each through no quick re-entry, equal-weight average)
and with cash (0).
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import cost_study as cs
from . import recalibrate as rc
from . import trade_rules as tr
from .short_tp_study import _account, _paired, cached_symbols
from .strategies import STRATEGIES, get_signal

NY = "America/New_York"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "agreement_study.json"
FIRST_TEST_SESSION = 120
COUNTS, FAMS = (3, 5, 7), (2, 3, 4)
RULES = {"no_quick_reentry": True}


def combine(sigs: Dict[str, pd.Series], families: Dict[str, str], how: str, n: int, shorts: bool) -> pd.Series:
    """Combined target position from the strategies' positions on the same bars."""
    S = pd.DataFrame(sigs)
    if how == "count":
        up, dn = (S > 0).sum(axis=1) >= n, (S < 0).sum(axis=1) >= n
    else:
        fam = pd.Series(families)[S.columns]
        up = (S > 0).T.groupby(fam.to_numpy()).any().sum() >= n
        dn = (S < 0).T.groupby(fam.to_numpy()).any().sum() >= n
    pos = up.astype(float)
    if shorts:
        pos = pos - (dn & ~up).astype(float)
    return pos


def variants() -> List[tuple]:
    out = []
    for shorts in (False, True):
        out += [("count", k, shorts) for k in COUNTS] + [("families", f, shorts) for f in FAMS]
    return out


def label(how, n, shorts) -> str:
    s = f"at least {n} strategies agree" if how == "count" else f"at least {n} families agree"
    return s + (" (long + short)" if shorts else " (long only)")


def run(cfg: Dict, symbols: Optional[List[str]] = None, progress=print) -> Dict:
    syms = symbols or cached_symbols()
    data = cs.Data(syms)
    r = rc.settings(cfg)
    fm = int(r["flatten_min"]) or 5
    names = cs.strategies_for("5Min", r)
    families = {n: STRATEGIES[n].family for n in names}
    rows, singles = [], []
    for s in syms:
        df = data.bars(s, "5Min")
        day = np.asarray(df.index.tz_convert(NY).date)
        days = sorted(set(day))
        test_days = set(days[FIRST_TEST_SESSION:])
        test = np.isin(day, list(test_days))
        costs = rc.costs_for(s, r)

        def run_sig(sig):
            f = cs.sim(df, data.cal[s], "agreement", {}, costs, "5Min", fm, signal=sig.where(test, 0.0))["frame"]
            d = cs._daily(f.ret)
            return d[d.index.isin(test_days)], int(len(cs.round_trips(f[test])))

        raw = {}
        for shorts in (False, True):
            raw[shorts] = {}
            for n in names:
                try:
                    raw[shorts][n] = get_signal(n, df, STRATEGIES[n].defaults, shorts)
                except Exception:  # noqa: BLE001
                    continue
        for n, sig in raw[False].items():                       # single strategies, long only, as live
            d, k = run_sig(tr.apply(sig, df, RULES))
            singles.append({"symbol": s, "strategy": n, "family": families[n], "test_days": [str(x) for x in d.index],
                            "ret": d.tolist(), "trades": k})
        row = {"symbol": s}
        for how, n, shorts in variants():
            sig = combine(raw[shorts], families, how, n, shorts)
            d, k = run_sig(tr.apply(sig, df, RULES))
            lab = label(how, n, shorts)
            row[lab] = d.tolist()
            row[lab + "#trades"] = k
            row[lab + "#in_market_%"] = round(float((sig[test] != 0).mean() * 100), 2)
            row["test_days"] = [str(x) for x in d.index]
        rows.append(row)
        progress(f"{s}: {len(raw[False])} strategies combined")

    single_acc = _account(singles, "ret")
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": str(data.as_of), "symbols": syms,
           "strategies": names, "families": dict(Counter(families.values())),
           "single": {"pairs": len(singles), "test_days": len(single_acc),
                      "total_%": round(float(((1 + single_acc).prod() - 1) * 100), 2),
                      "vs_cash": _paired(single_acc, single_acc * 0),
                      "trades": sum(x["trades"] for x in singles)},
           "combined": {}}
    for v in variants():
        lab = label(*v)
        acc = _account(rows, lab)
        sym_single = {s: _account([x for x in singles if x["symbol"] == s], "ret") for s in syms}
        per = [_paired(pd.Series(x[lab], index=pd.to_datetime(x["test_days"])), sym_single[x["symbol"]]) for x in rows]
        out["combined"][lab] = {
            "total_%": round(float(((1 + acc).prod() - 1) * 100), 2),
            "vs_single": _paired(acc, single_acc.reindex(acc.index)),
            "vs_cash": _paired(acc, acc * 0),
            "symbols_better_than_single": sum(p["significant"] and p["mean_bps"] > 0 for p in per),
            "symbols_worse_than_single": sum(p["significant"] and p["mean_bps"] < 0 for p in per),
            "trades": sum(x[lab + "#trades"] for x in rows),
            "in_market_%": round(float(np.mean([x[lab + "#in_market_%"] for x in rows])), 2)}
    out["lines"] = lines(out)
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out


def lines(res: Dict) -> List[str]:
    s = res["single"]
    ci = lambda p: f"{p['mean_bps']:+.2f} bps/day ({p['ci95_bps'][0]:+.2f} to {p['ci95_bps'][1]:+.2f})"  # noqa: E731
    out = [f"Agreement test, {len(res['symbols'])} symbols, {len(res['strategies'])} strategies {res['families']}, "
           f"{s['test_days']} unseen sessions, after costs, no quick re-entry.",
           f"  single strategies (average of {s['pairs']}): total {s['total_%']:+.2f}%, vs cash {ci(s['vs_cash'])}, {s['trades']} trades"]
    for lab, c in res["combined"].items():
        out.append(f"  {lab:<44} total {c['total_%']:+7.2f}%  in market {c['in_market_%']:5.1f}%  trades {c['trades']:>6}  "
                   f"vs single {ci(c['vs_single'])}  vs cash {ci(c['vs_cash'])}  symbols better/worse {c['symbols_better_than_single']}/{c['symbols_worse_than_single']}")
    return out


def main():
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    res = run(cfg)
    print("\n".join(res["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
