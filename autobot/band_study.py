"""
Dynamic-band exit study: do volatility-scaled sell levels help the bot as it runs today?
Diagnosis only: nothing here changes the bot.

    python -m autobot.band_study           # data/band_study.json (2 workers)
    python -m autobot.band_study --modes   # 5-minute bars, rolling vs session vs adaptive bands: data/band_study_modes.json

The user's idea (2026-10-06): instead of fixed % levels, a LOWER boundary (sell when it falls) and
an UPPER boundary (take the profit) that adjust themselves to how jumpy the stock is:
  atr_stop k     sell if a bar closes more than k ATRs below the best close since entry
  band_profit k  sell if a bar closes above the 20-bar moving average plus k ATRs
(ATR = average true range of the last 20 bars; autobot.exits, the same code the bot would use).
Settings fixed before the run: atr_stop in {none, 2, 3, 4} x band_profit in {none, 2, 3}.
Base = what runs live today: each strategy (defaults; plus the live jobs with their own settings on
5-minute bars), long only, through "no quick re-entry". After a band exit the strategy has to go
out and back in (re-entry fresh_cross). Same data, costs and replay as the cost study.
Walk-forward: in each window the setting (or none) that made the most on the previous 60 sessions
is used on the next 20; compared with "none" on the same days.
"""
from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from . import cost_study as cs
from . import exits
from . import recalibrate as rc
from . import trade_rules as tr
from .short_tp_study import TEST, TRAIN, cached_symbols, exits_summary
from .strategies import STRATEGIES, get_signal

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "band_study.json"
TFS = ["5Min", "1Hour"]
STOPS, PROFITS = [None, 2, 3, 4], [None, 2, 3]
RULES = {"no_quick_reentry": True}


MODES = ("rolling", "session", "adaptive")
OUT_MODES = ROOT / "data" / "band_study_modes.json"


def combos(modes=("rolling",)) -> List[Optional[Dict]]:
    out: List[Optional[Dict]] = [None]
    for m in modes:
        for a in STOPS:
            for b in PROFITS:
                if a is None and b is None:
                    continue
                c = {k: v for k, v in (("atr_stop", a), ("band_profit", b)) if v is not None}
                if m != "rolling" or len(modes) > 1:
                    c["band_mode"] = m
                out.append(c)
    return out


def label(c: Optional[Dict]) -> str:
    if not c:
        return "none"
    s = " · ".join(x for x in (f"stop {c['atr_stop']:g} ATR" if "atr_stop" in c else "",
                               f"profit avg+{c['band_profit']:g} ATR" if "band_profit" in c else "") if x)
    return f"{c['band_mode']}: {s}" if "band_mode" in c else s


def _walk(D: pd.DataFrame, cols: List[str]):
    wf, base_t, chosen = [], [], []
    for a in range(TRAIN, len(D) - 1, TEST):
        best = ((1 + D[cols].iloc[a - TRAIN:a]).prod() - 1).idxmax()
        chosen.append(best)
        wf.append(D.iloc[a:a + TEST][best])
        base_t.append(D.iloc[a:a + TEST]["none"])
    return wf, base_t, chosen


def _modes_symbol(sym: str, closes, as_of, cfg: Dict) -> List[Dict]:
    """5-minute bars, every mode's combos on identical days; one walk-forward per mode and one
    choosing freely among all modes (each a separate row, side = 'long <group>')."""
    data = cs.Data([sym], closes=closes, as_of=as_of)
    r = rc.settings(cfg)
    fm = int(r["flatten_min"]) or 5
    costs = rc.costs_for(sym, r)
    df = data.bars(sym, "5Min")
    d0 = data.start(sym, int(r["warmup_days"]))
    pairs = [(n, STRATEGIES[n].defaults, "default") for n in cs.strategies_for("5Min", r)]
    pairs += [(j["strategy"], j.get("params") or {}, f"live job {j['name']}") for j in cfg["live"].get("jobs") or []
              if j.get("enabled", True) and j["symbols"][0] == sym]
    rows = []
    grid = combos(MODES)
    for name, params, kind in pairs:
        try:
            base = tr.apply(get_signal(name, df, params, False), df, RULES)
        except Exception:  # noqa: BLE001
            continue
        daily, full, trades = {}, {}, {}
        for c in grid:
            sig = base if c is None else exits.apply_exit_layer(base, df, {**c, "reentry": "fresh_cross", "no_entry_last_min": 0}, crypto=False)
            f = cs._period(cs.sim(df, data.cal[sym], name, params, costs, "5Min", fm, signal=sig)["frame"], d0)
            lab = label(c)
            daily[lab] = cs._daily(f.ret)
            full[lab] = round(float(((1 + f.ret).prod() - 1) * 100), 3)
            trades[lab] = int(len(cs.round_trips(f)))
        D = pd.DataFrame(daily).fillna(0.0)
        for group in list(MODES) + ["all"]:
            cols = [c for c in D.columns if c == "none" or group == "all" or c.startswith(group + ":")]
            wf, base_t, chosen = _walk(D, cols)
            if not wf:
                continue
            wf_s, none_s = pd.concat(wf), pd.concat(base_t)
            rows.append({"symbol": sym, "timeframe": "5Min", "strategy": name, "family": STRATEGIES[name].family, "kind": kind,
                         "side": f"long {group}", "full_return_%": {k: full[k] for k in cols}, "trades": {k: trades[k] for k in cols},
                         "test_days": [str(d) for d in wf_s.index], "wf": wf_s.round(8).tolist(), "none": none_s.round(8).tolist(),
                         "fixed_test": {k: D.loc[wf_s.index, k].round(8).tolist() for k in cols}, "chosen": chosen})
    return rows


def run_modes(cfg: Dict, workers: int = 2) -> Dict:
    syms = cached_symbols()
    data = cs.Data(syms)
    rows: List[Dict] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for part in ex.map(_modes_symbol, syms, [data.closes] * len(syms), [data.as_of] * len(syms), [cfg] * len(syms)):
            rows += part
    summ = {}
    for group in list(MODES) + ["all"]:
        sel = [dict(x, side="long") for x in rows if x["side"] == f"long {group}"]
        summ[group] = exits_summary(sel)["5Min long"]
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": str(data.as_of), "symbols": syms,
           "grid": {"atr_stop": STOPS, "band_profit": PROFITS, "band_period": 20, "modes": MODES}, "summary": summ}
    res["lines"] = lines(res)
    OUT_MODES.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def _symbol(sym: str, closes, as_of, cfg: Dict) -> List[Dict]:
    data = cs.Data([sym], closes=closes, as_of=as_of)
    r = rc.settings(cfg)
    fm = int(r["flatten_min"]) or 5
    costs = rc.costs_for(sym, r)
    rows = []
    for tf in TFS:
        df = data.bars(sym, tf)
        d0 = data.start(sym, int(r["warmup_days"]))
        pairs = [(n, STRATEGIES[n].defaults, "default") for n in cs.strategies_for(tf, r)]
        if tf == "5Min":
            pairs += [(j["strategy"], j.get("params") or {}, f"live job {j['name']}") for j in cfg["live"].get("jobs") or []
                      if j.get("enabled", True) and j["symbols"][0] == sym]
        for name, params, kind in pairs:
            try:
                base = tr.apply(get_signal(name, df, params, False), df, RULES)
            except Exception:  # noqa: BLE001
                continue
            daily, full, trades = {}, {}, {}
            for c in combos():  # rolling only (the first study)
                sig = base if c is None else exits.apply_exit_layer(base, df, {**c, "reentry": "fresh_cross", "no_entry_last_min": 0}, crypto=False)
                f = cs._period(cs.sim(df, data.cal[sym], name, params, costs, tf, fm, signal=sig)["frame"], d0)
                lab = label(c)
                daily[lab] = cs._daily(f.ret)
                full[lab] = round(float(((1 + f.ret).prod() - 1) * 100), 3)
                trades[lab] = int(len(cs.round_trips(f)))
            D = pd.DataFrame(daily).fillna(0.0)
            wf, base_t, chosen = [], [], []
            for a in range(TRAIN, len(D) - 1, TEST):
                best = ((1 + D.iloc[a - TRAIN:a]).prod() - 1).idxmax()
                chosen.append(best)
                wf.append(D.iloc[a:a + TEST][best])
                base_t.append(D.iloc[a:a + TEST]["none"])
            if not wf:
                continue
            wf_s, none_s = pd.concat(wf), pd.concat(base_t)
            rows.append({"symbol": sym, "timeframe": tf, "strategy": name, "family": STRATEGIES[name].family, "kind": kind, "side": "long",
                         "full_return_%": full, "trades": trades, "test_days": [str(d) for d in wf_s.index],
                         "wf": wf_s.round(8).tolist(), "none": none_s.round(8).tolist(),
                         "fixed_test": {k: D.loc[wf_s.index, k].round(8).tolist() for k in D.columns}, "chosen": chosen})
    return rows


def lines(res: Dict) -> List[str]:
    L = [f"Dynamic bands on top of today's live set-up (strategies + no quick re-entry), long only, {len(res['symbols'])} symbols, "
         f"2 years to {res['as_of']}, after costs, flat before the close."]
    for k, v in res["summary"].items():
        d = v["wf_vs_none"]
        none = next(f for f in v["fixed"] if f["setting"] == "none")
        best = v["fixed"][0]
        L.append(f"{k}: walk-forward {v['wf_test_return_%']:+.2f}% vs no bands {v['none_test_return_%']:+.2f}% on {v['test_days']} test days "
                 f"(daily difference {d['mean_bps']:+.2f} bps, {d['ci95_bps'][0]:+.2f} to {d['ci95_bps'][1]:+.2f}"
                 f"{', clearly' if d['significant'] else ', not clearly'}); pairs clearly better {v['pairs_wf_clearly_better']}, "
                 f"worse {v['pairs_wf_clearly_worse']} (luck ~{v['expected_by_luck']}); trades with bands vs none: "
                 f"{best['trades']} vs {none['trades']} for the best fixed setting ({best['setting']}, {best['avg_full_return_%']:+.2f}% "
                 f"per pair in hindsight vs {none['avg_full_return_%']:+.2f}%)")
        L.append("   chosen most: " + ", ".join(f"{c['setting']} {c['share_%']}%" for c in v["chosen_most"][:3]))
    lv = res.get("live_jobs") or []
    if lv:
        L.append("Live jobs (5-minute): " + "; ".join(f"{x['symbol']} wf {x['wf_%']:+.2f}% vs none {x['none_%']:+.2f}%" for x in lv))
    return L


def run(cfg: Dict, workers: int = 2) -> Dict:
    syms = cached_symbols()
    data = cs.Data(syms)
    rows: List[Dict] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for part in ex.map(_symbol, syms, [data.closes] * len(syms), [data.as_of] * len(syms), [cfg] * len(syms)):
            rows += part
    summ = exits_summary(rows)
    summ = {k: v for k, v in summ.items() if not k.endswith("shorts added")}
    live = []
    for x in rows:
        if x["kind"].startswith("live"):
            live.append({"symbol": x["symbol"], "strategy": x["strategy"],
                         "wf_%": round(float(((1 + pd.Series(x["wf"])).prod() - 1) * 100), 2),
                         "none_%": round(float(((1 + pd.Series(x["none"])).prod() - 1) * 100), 2)})
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": str(data.as_of), "symbols": syms,
           "grid": {"atr_stop": STOPS, "band_profit": PROFITS, "band_period": 20}, "summary": summ, "live_jobs": live}
    res["lines"] = lines(res)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def main():
    import sys
    import yaml
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    if "--modes" in sys.argv:
        res = run_modes(cfg)
        print("\n".join(res["lines"]))
        print("saved", OUT_MODES)
        return
    res = run(cfg)
    print("\n".join(res["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
