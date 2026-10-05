"""
Trade-rules study: do the churn brake and no-quick-re-entry rules (autobot.trade_rules) help on
5-minute bars after costs? Diagnosis only: nothing here changes the bot.

    python -m autobot.trade_rules_study        # data/trade_rules_study.json (a few minutes, 2 workers)

Same data and replay as the cost study (2 years of IEX 5-minute bars, the cached universe stocks /
ETFs, each asset class's costs, flat 5 minutes before the close, recalibration's warm-up skipped).
Pairs: every 5-minute strategy at its defaults x symbol, plus the enabled live jobs with their own
settings. The rules' settings were fixed before the run (no tuning):
  churn brake        2 entries within 60 minutes -> no new entry for the rest of the session
  no quick re-entry  same-session re-entry on the same side only beyond the closed position's best
Each variant is compared with the strategy alone on the same days (daily difference, 95% range).
"""
from __future__ import annotations

import json
import math
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import cost_study as cs
from . import recalibrate as rc
from . import trade_rules as tr
from .short_tp_study import cached_symbols
from .strategies import STRATEGIES, get_signal

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "trade_rules_study.json"
VARIANTS = {"none": None,
            "churn brake": {"churn_brake": {"max_entries": 2, "window_min": 60}},
            "no quick re-entry": {"no_quick_reentry": True},
            "both": {"churn_brake": {"max_entries": 2, "window_min": 60}, "no_quick_reentry": True}}


def _symbol(sym: str, closes, as_of, cfg: Dict) -> List[Dict]:
    data = cs.Data([sym], closes=closes, as_of=as_of)
    r = rc.settings(cfg)
    fm = int(r["flatten_min"]) or 5
    costs = rc.costs_for(sym, r)
    df = data.bars(sym, "5Min")
    d0 = data.start(sym, int(r["warmup_days"]))
    pairs = [(name, STRATEGIES[name].defaults, "default") for name in cs.strategies_for("5Min", r)]
    pairs += [(j["strategy"], j.get("params") or {}, f"live job {j['name']}") for j in cfg["live"].get("jobs") or []
              if j.get("enabled", True) and j["symbols"][0] == sym]
    out = []
    for name, params, kind in pairs:
        try:
            base = get_signal(name, df, params, False)
        except Exception:  # noqa: BLE001
            continue
        row = {"symbol": sym, "strategy": name, "kind": kind, "daily": {}, "trades": {}, "net_%": {}}
        for v, rules in VARIANTS.items():
            sig = tr.apply(base, df, rules) if rules else base
            f = cs._period(cs.sim(df, data.cal[sym], name, params, costs, "5Min", fm, signal=sig)["frame"], d0)
            dd = cs._daily(f.ret)
            row["daily"][v] = {str(k): float(x) for k, x in dd.items()}
            row["trades"][v] = int(len(cs.round_trips(f)))
            row["net_%"][v] = round(float(((1 + f.ret).prod() - 1) * 100), 2)
        out.append(row)
    return out


def _paired(a: pd.Series, b: pd.Series) -> Dict:
    d = (a - b).dropna()
    n = len(d)
    if n < 2:
        return {"mean_bps": None, "ci95_bps": [None, None], "significant": False}
    mu, se = d.mean(), d.std(ddof=1) / math.sqrt(n)
    return {"mean_bps": round(mu * 1e4, 2), "ci95_bps": [round((mu - 1.96 * se) * 1e4, 2), round((mu + 1.96 * se) * 1e4, 2)],
            "significant": bool(mu - 1.96 * se > 0 or mu + 1.96 * se < 0)}


def summarize(rows: List[Dict]) -> Dict:
    res = {}
    for group, sel in (("all pairs", rows), ("live jobs", [r for r in rows if r["kind"].startswith("live")])):
        if not sel:
            continue
        acc = {v: pd.concat([pd.Series(r["daily"][v]) for r in sel], axis=1).fillna(0).mean(axis=1) for v in VARIANTS}
        g = {"pairs": len(sel), "days": len(acc["none"])}
        for v in VARIANTS:
            row = {"net_%": round(float(((1 + acc[v]).prod() - 1) * 100), 2),
                   "trades": int(sum(r["trades"][v] for r in sel)),
                   "pairs_positive": int(sum(r["net_%"][v] > 0 for r in sel))}
            if v != "none":
                row["vs_none"] = _paired(acc[v], acc["none"])
                per = [_paired(pd.Series(r["daily"][v]), pd.Series(r["daily"]["none"])) for r in sel]
                row["pairs_clearly_better"] = int(sum(p["significant"] and p["mean_bps"] > 0 for p in per))
                row["pairs_clearly_worse"] = int(sum(p["significant"] and p["mean_bps"] < 0 for p in per))
            g[v] = row
        res[group] = g
    res["live_jobs"] = [{"symbol": r["symbol"], "strategy": r["strategy"], "net_%": r["net_%"], "trades": r["trades"]}
                        for r in rows if r["kind"].startswith("live")]
    return res


def tiny_orders(path: Path = ROOT / "logs" / "trades.csv") -> Dict:
    """Rule 1 from the bot's own order log: orders under 10% of that symbol's usual order size."""
    if not path.exists():
        return {}
    t = pd.read_csv(path)
    t = t[t.side.isin(["buy", "sell"])]
    med = t.groupby("symbol").qty.transform("median")
    tiny = t[t.qty < 0.1 * med]
    return {"orders": int(len(t)), "tiny_orders": int(len(tiny)), "tiny_share_%": round(len(tiny) / max(1, len(t)) * 100, 1),
            "tiny_notional_usd": round(float((tiny.qty * tiny.approx_price).sum()), 0),
            "examples": tiny.tail(5)[["time", "symbol", "side", "qty", "reason"]].to_dict("records")}


def lines(res: Dict) -> List[str]:
    L = [f"5-minute bars, {len(res['symbols'])} symbols, 2 years to {res['as_of']}, after costs, flat before the close."]
    for group in ("all pairs", "live jobs"):
        g = res["summary"].get(group)
        if not g:
            continue
        n = g["none"]
        L.append(f"{group} ({g['pairs']} pairs, {g['days']} days): strategy alone {n['net_%']:+.1f}%, {n['trades']} trades, "
                 f"{n['pairs_positive']} pairs positive")
        for v in list(VARIANTS)[1:]:
            x = g[v]
            d = x["vs_none"]
            L.append(f"   {v}: {x['net_%']:+.1f}%, {x['trades']} trades ({(x['trades'] / max(1, n['trades']) - 1) * 100:+.0f}%), "
                     f"{x['pairs_positive']} positive; daily difference {d['mean_bps']:+.2f} bps ({d['ci95_bps'][0]:+.2f} to "
                     f"{d['ci95_bps'][1]:+.2f}){' clearly' if d['significant'] else ''}; pairs clearly better {x['pairs_clearly_better']}, "
                     f"worse {x['pairs_clearly_worse']} (luck ~{round(0.025 * g['pairs'], 1)})")
    t = res.get("tiny_orders") or {}
    if t:
        L.append(f"Rule 1 (bot's order log): {t['tiny_orders']} of {t['orders']} orders were under 10% of the usual size "
                 f"(${t['tiny_notional_usd']:,.0f} traded for nothing but cost).")
    return L


def run(cfg: Dict, workers: int = 2) -> Dict:
    syms = cached_symbols()
    data = cs.Data(syms)
    rows: List[Dict] = []
    if workers <= 1:
        for s in syms:
            rows += _symbol(s, data.closes, data.as_of, cfg)
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for part in ex.map(_symbol, syms, [data.closes] * len(syms), [data.as_of] * len(syms), [cfg] * len(syms)):
                rows += part
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": str(data.as_of), "symbols": syms,
           "variants": VARIANTS, "summary": summarize(rows), "tiny_orders": tiny_orders()}
    res["lines"] = lines(res)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def main():
    import sys
    import yaml
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    res = run(cfg)
    print("\n".join(res["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
