"""
Scalping ("fast finger") test: buy on the strategy's signal, sell with a resting limit order as soon
as the price covers the costs plus a small premium, then start again with new levels. Diagnosis only.

    .venv-ml/Scripts/python -m autobot.scalp_study        # data/ml/study_scalp.json

Entries: the bot's strategy signals as today (each strategy x symbol: defaults + live jobs, long only,
no quick re-entry), bought at the decision bar's close. Exits, whichever comes first:
  target   a limit sell at entry x (1 + round-trip cost + premium), filled when a later bar's high
           trades at least 1 cent beyond it (at the bar's open if it opens above the target)
  stop     (variants) a stop at entry x (1 - k x the target distance), filled at the stop or the open
           if the bar opens below it; if a bar reaches both, the stop counts first (worst case)
  signal   the strategy goes flat: sold at that bar's close
  close    the 15:50 bar's close (the bot's close-out)
After a target, it buys again at that bar's close if the signal is still on (new levels from the new
price). After a stop, it waits for the strategy to go flat and signal again.
Premium (fixed before the run): +2, +5, +10 bps, or 'dyn' = a quarter of the trees' forecast of the
next hour's swing (data/ml/pred_trees.pkl.gz). Stop: none, 1x or 2x the target distance.
Costs per side as everywhere (2 bps for stocks), twice that in the close-out bar.
Same 386 unseen sessions as the ML study; compared with the strategies alone (the same simulator
without target or stop) per day, paired.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import cost_study as cs
from . import ml_features as mf
from . import ml_study as ms
from . import recalibrate as rc
from . import trade_rules as tr
from .short_tp_study import _account, _paired
from .strategies import STRATEGIES, get_signal

NY = mf.NY
OUT = mf.OUT_DIR / "study_scalp.json"
PREMIUMS = (2.0, 5.0, 10.0, "dyn")
STOPS = (None, 1.0, 2.0)
CLOSEOUT_MIN = 950
TICK = 0.01
RULES = {"no_quick_reentry": True}


def label(prem, stop) -> str:
    if prem is None:
        return "strategy alone"
    p = "dyn" if prem == "dyn" else f"+{prem:g} bps"
    return f"target cost {p}, " + ("no stop" if stop is None else f"stop {stop:g}x")


def simulate(o, h, l, c, b, minute, day, test, side_cost, prem_frac, stop_k) -> List[tuple]:
    """One pass; returns (day, return, kind) per round trip. prem_frac: per-bar premium (fraction) or None
    for the strategy alone."""
    n = len(c)
    trades = []
    i = 0
    wait_flat = False
    while i < n:
        if wait_flat:
            if b[i] <= 0 or (i > 0 and day[i] != day[i - 1]):
                wait_flat = False
            else:
                i += 1
                continue
        if not test[i] or b[i] <= 0 or minute[i] >= CLOSEOUT_MIN:
            i += 1
            continue
        e = c[i]
        # the session's close-out bar
        end = i
        while end + 1 < n and day[end + 1] == day[i] and minute[end] < CLOSEOUT_MIN:
            end += 1
        k_sig = next((k for k in range(i + 1, end + 1) if b[k] <= 0), None)
        k_end = k_sig if k_sig is not None else end
        tgt = stp = None
        k_tgt = k_stp = None
        if prem_frac is not None:
            g = 2 * side_cost + prem_frac[i]
            tgt = e * (1 + g)
            if stop_k:
                stp = e * (1 - stop_k * g)
            for k in range(i + 1, k_end + 1):
                if stp is not None and l[k] <= stp:
                    k_stp = k
                    break
                if h[k] >= tgt + TICK:
                    k_tgt = k
                    break
        cost_out = side_cost * (2 if (k_end == end and k_sig is None) else 1)
        if k_stp is not None:
            px, kind, k = min(stp, o[k_stp]), "stop", k_stp
            cost_out = side_cost
        elif k_tgt is not None:
            px, kind, k = max(tgt, o[k_tgt]), "target", k_tgt
            cost_out = side_cost
        else:
            px, kind, k = c[k_end], ("signal" if k_sig is not None else "close"), k_end
        trades.append((day[i], px / e - 1 - side_cost - cost_out, kind))
        if kind == "target":
            i = k                                    # buy again at this bar's close if the signal is still on
        elif kind == "stop":
            wait_flat, i = True, k + 1
        else:
            i = k + 1
    return trades


def daily(trades: List[tuple], days: List) -> pd.Series:
    s = pd.Series(0.0, index=pd.Index(days))
    if trades:
        t = pd.DataFrame(trades, columns=["day", "ret", "kind"]).groupby("day").ret.sum()
        s.loc[t.index] = t.to_numpy()
    return s


def run(cfg: Dict, symbols: Optional[List[str]] = None, progress=print) -> Dict:
    syms, data, P = ms.panel(symbols)
    P = P.merge(pd.read_pickle(mf.OUT_DIR / "pred_trees.pkl.gz"), on=["symbol", "t"], how="left")
    days = sorted(P.day.unique())
    test_days = sorted({d for a, b in ms.windows(days) for d in days if a <= d <= b})
    r = rc.settings(cfg)
    variants = [(None, None)] + [(p, s) for p in PREMIUMS for s in STOPS]
    rows, kinds = [], {label(*v): {"target": 0, "stop": 0, "signal": 0, "close": 0} for v in variants}
    trade_stats = {label(*v): [] for v in variants}
    for sym in syms:
        df = data.bars(sym, "5Min")
        ny = df.index.tz_convert(NY)
        day = np.asarray(ny.date)
        minute = np.asarray(ny.hour * 60 + ny.minute)
        test = np.isin(day, test_days)
        o, h, l, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        swing = P[P.symbol == sym].set_index("t").pred_swing.reindex(df.index).to_numpy()
        dyn = np.where(np.isfinite(swing), swing / 100 / 4, 5e-4)       # a quarter of the expected swing
        side = rc.costs_for(sym, r).per_turn
        pairs = [(n, STRATEGIES[n].defaults, "default") for n in cs.strategies_for("5Min", r)]
        pairs += [(j["strategy"], j.get("params") or {}, f"live job {j['name']}") for j in cfg["live"].get("jobs") or []
                  if j.get("enabled", True) and j["symbols"][0] == sym]
        for name, params, kind in pairs:
            try:
                b = tr.apply(get_signal(name, df, params, False), df, RULES).to_numpy(float)
            except Exception:  # noqa: BLE001
                continue
            row = {"symbol": sym, "strategy": name, "kind": kind, "test_days": [str(d) for d in test_days]}
            for prem, stop in variants:
                pf = None if prem is None else (dyn if prem == "dyn" else np.full(len(c), prem / 1e4))
                t = simulate(o, h, l, c, b, minute, day, test, side, pf, stop)
                lab = label(prem, stop)
                row[lab] = daily(t, test_days).tolist()
                for _, ret, k in t:
                    kinds[lab][k] += 1
                trade_stats[lab] += [x[1] for x in t]
            rows.append(row)
        progress(f"{sym}: {len(pairs)} strategy sets")

    def summarise(sel: List[Dict]) -> Dict:
        base = _account(sel, "strategy alone")
        out = {}
        for v in variants:
            lab = label(*v)
            acc = _account(sel, lab)
            pair = [_paired(pd.Series(x[lab]), pd.Series(x["strategy alone"])) for x in sel]
            out[lab] = {"total_%": round(float(((1 + acc).prod() - 1) * 100), 2), "vs_strategy": _paired(acc, base),
                        "pairs_better": sum(p["significant"] and p["mean_bps"] > 0 for p in pair),
                        "pairs_worse": sum(p["significant"] and p["mean_bps"] < 0 for p in pair)}
        return out

    def tstats(x: List[float]) -> Dict:
        a = np.array(x)
        w, ls = a[a > 0], a[a <= 0]
        return {"trades": int(len(a)), "win_rate_%": round(float((a > 0).mean() * 100), 1) if len(a) else None,
                "avg_win_bps": round(float(w.mean() * 1e4), 2) if len(w) else None,
                "avg_loss_bps": round(float(ls.mean() * 1e4), 2) if len(ls) else None,
                "avg_trade_bps": round(float(a.mean() * 1e4), 2) if len(a) else None}

    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": str(data.as_of), "symbols": syms,
           "test_sessions": len(test_days), "first_test_day": str(test_days[0]), "pairs": len(rows),
           "all": summarise(rows), "live_jobs": summarise([x for x in rows if x["kind"].startswith("live job")]),
           "trades": {k: {**tstats(v), "exits": kinds[k]} for k, v in trade_stats.items()}}
    res["lines"] = lines(res)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def lines(res: Dict) -> List[str]:
    out = [f"Scalping vs the strategies alone, {res['pairs']} strategy sets, {res['test_sessions']} unseen sessions from {res['first_test_day']}, after costs."]
    for lab, s in res["all"].items():
        t = res["trades"][lab]
        p = s["vs_strategy"]
        vs = "" if lab == "strategy alone" else (f" | vs strategy {p['mean_bps']:+.2f} bps/day ({p['ci95_bps'][0]:+.2f} to {p['ci95_bps'][1]:+.2f})"
                                                f" better/worse {s['pairs_better']}/{s['pairs_worse']}")
        out.append(f"  {lab:<32} total {s['total_%']:+7.2f}%  trades {t['trades']:>7}  wins {t['win_rate_%']}%  "
                   f"avg win {t['avg_win_bps']} / loss {t['avg_loss_bps']} / trade {t['avg_trade_bps']} bps{vs}")
    lj = res["live_jobs"]
    best = max((k for k in lj if k != "strategy alone"), key=lambda k: lj[k]["total_%"])
    out.append(f"  live jobs: strategy alone {lj['strategy alone']['total_%']:+.2f}%; best scalp variant {best} {lj[best]['total_%']:+.2f}% "
               f"({lj[best]['vs_strategy']['mean_bps']:+.2f} bps/day, {lj[best]['vs_strategy']['ci95_bps'][0]:+.2f} to {lj[best]['vs_strategy']['ci95_bps'][1]:+.2f})")
    return out


def main():
    import yaml
    cfg = yaml.safe_load((mf.ROOT / "config.yaml").read_text(encoding="utf-8"))
    res = run(cfg)
    print("\n".join(res["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
