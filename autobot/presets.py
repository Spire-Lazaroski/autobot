"""
Three ready-made setups for the long-term holdings, by risk level: the same mix the evidence supports (core with a
volatility target + sector rotation + cash), in three sizes. Choosing one changes only `live.core` and
`live.rotation` in config.yaml; the trading jobs are left as they are.

    python -m autobot.presets          # backtest the three setups -> data/presets_study.json (seconds, local data)

The backtest (monthly, after costs, cash at the T-bill rate of the time):
  core      the market with its share set like the live core: allocation x normal volatility / recent volatility
            (last 12 months; normal = the median so far), kept between the setup's min and max, decided at each
            month's end for the next month
  rotation  the live sleeve's rule (top 5 by 12-month return, 10-month trend filter, 12% volatility sizing), from
            autobot.rs_study, as its share of the account; what it doesn't invest stays cash inside it
  cash      the rest
on two samples: US sector ETFs 2017-2026 (the funds the bot trades) and Fama-French 10 industries 1927-2026 (long
history, including 1929 and 2008). Past results; no promise of future ones.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "presets_study.json"
SEED = ROOT / "seed" / "studies" / "presets_study.json"

PRESETS: Dict[str, Dict] = {
    "careful": {"label": "Careful", "about": "Smaller holdings, more cash: the smallest drops.",
                "core": {"allocation_pct": 20, "min_pct": 10, "max_pct": 30}, "rotation": {"allocation_pct": 30}},
    "balanced": {"label": "Balanced", "about": "Today's setup: the core around 30% and the sector sleeve up to 40%.",
                 "core": {"allocation_pct": 30, "min_pct": 15, "max_pct": 45}, "rotation": {"allocation_pct": 40}},
    "growth": {"label": "Growth", "about": "Larger holdings, little cash: more return, deeper drops.",
               "core": {"allocation_pct": 40, "min_pct": 20, "max_pct": 60}, "rotation": {"allocation_pct": 40}},
}
COST = {"etf": 0.0002, "french": 0.001}


def apply(cfg: Dict, name: str) -> Dict:
    """config.yaml with the setup's core and rotation sizes (everything else unchanged)."""
    p = PRESETS[name]
    live = cfg["live"]
    core = dict(live.get("core") or {})
    core["allocation_pct"] = p["core"]["allocation_pct"]
    vt = dict(core.get("vol_target") or {})
    vt.update(enabled=True, min_pct=p["core"]["min_pct"], max_pct=p["core"]["max_pct"])
    core["vol_target"] = vt
    live["core"] = core
    live["rotation"] = {**(live.get("rotation") or {}), "allocation_pct": p["rotation"]["allocation_pct"]}
    return cfg


def current(live: Dict) -> Optional[str]:
    """Which setup config.yaml matches now (None = a custom mix)."""
    core, rot = live.get("core") or {}, live.get("rotation") or {}
    vt = core.get("vol_target") or {}
    for name, p in PRESETS.items():
        if (core.get("allocation_pct") == p["core"]["allocation_pct"] and vt.get("min_pct") == p["core"]["min_pct"]
                and vt.get("max_pct") == p["core"]["max_pct"] and rot.get("allocation_pct") == p["rotation"]["allocation_pct"]):
            return name
    return None


# ----------------------------------------------------------------- backtest
def core_weight(m: pd.Series, p: Dict) -> pd.Series:
    vol = m.rolling(12).std()
    normal = vol.expanding(min_periods=12).median()
    w = (p["allocation_pct"] / 100 * normal / vol).clip(p["min_pct"] / 100, p["max_pct"] / 100)
    return w.fillna(p["allocation_pct"] / 100)


def account(R: pd.DataFrame, market: pd.Series, cash: pd.Series, cost: float, preset: Dict) -> pd.DataFrame:
    from . import rs_study as rs
    rot = rs.replay(R, rs.weights(R, "full", 5), cash, cost)
    w = core_weight(market, preset["core"]).shift(1)                 # decided at the end of the month before
    a = preset["rotation"]["allocation_pct"] / 100
    idx = rot.index.intersection(w.dropna().index)
    w, r = w.reindex(idx), rot.reindex(idx)
    turn = w.diff().abs().fillna(0.0)
    ret = w * market.reindex(idx) + a * r.ret + (1 - w - a) * cash.reindex(idx) - turn * cost
    return pd.DataFrame({"ret": ret, "invested": w + a * r.invested})


def stats(f: pd.DataFrame) -> Dict:
    r = f.ret.dropna()
    eq = (1 + r).cumprod()
    years = len(r) / 12
    yearly = (1 + r).groupby(r.index.year).prod() - 1
    return {"from": str(r.index[0]), "to": str(r.index[-1]),
            "yearly_%": round(float((eq.iloc[-1] ** (1 / years) - 1) * 100), 1),
            "worst_drop_%": round(float((eq / eq.cummax() - 1).min() * 100), 1),
            "worst_year_%": round(float(yearly.min() * 100), 1),
            "volatility_%": round(float(r.std() * math.sqrt(12) * 100), 1),
            "avg_invested_%": round(float(f.invested.mean() * 100), 0)}


def run() -> Dict:
    from . import rs_study as rs
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "note": "Monthly backtest after costs, cash at the T-bill rate; past results, no promise of future ones.",
           "presets": PRESETS, "samples": {}}
    e, f = rs.etfs(), rs.french()
    for label, d, start, cost in (("Sector ETFs 2017-2026", e, "2017-01", COST["etf"]),
                                  ("US industries 1927-2026", f, "1927-07", COST["french"])):
        out = {}
        for name, p in PRESETS.items():
            acc = account(d["R"], d["market"], d["cash"], cost, p)
            out[name] = stats(acc[acc.index >= pd.Period(start, "M")])
        mk = pd.DataFrame({"ret": d["market"], "invested": 1.0})
        out["market"] = stats(mk[mk.index >= pd.Period(out["balanced"]["from"], "M")])
        res["samples"][label] = out
    res["lines"] = lines(res)
    return res


def lines(res: Dict) -> List[str]:
    L = []
    for smp, out in res["samples"].items():
        L.append(f"[{smp}]")
        for k, s in out.items():
            L.append(f"  {k}: {s['yearly_%']:+.1f}%/yr, worst drop {s['worst_drop_%']:.0f}%, worst year {s['worst_year_%']:+.0f}%, "
                     f"avg invested {s['avg_invested_%']:.0f}%")
    return L


def saved() -> Optional[Dict]:
    for p in (OUT, SEED):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    res = run()
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print("\n".join(res["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
