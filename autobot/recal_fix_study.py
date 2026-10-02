"""
Recalibration switch study: do options 1 (allow_drop), 2 (benchmark: matched_buy_hold) and the
per-trade edge filter make recalibration's picks honest? Study only: it changes no switch, no
config.yaml and leaves recalibration in "propose".

    python -m autobot.recal_fix_study --phase design      # the first ~18 months (all versions, both cost levels)
    python -m autobot.recal_fix_study --phase confirm     # the last ~6 months, ONCE, continuing from design
    python -m autobot.recal_fix_study --report            # data/recal_fix_study.json + the summary

Protocol, fixed before running (the user's criteria):
- Versions (the same week-by-week replay, only data up to each Friday): today | option 1 |
  option 2 | options 1+2 | options 1+2 + min_edge_per_trade (2x); references: no trading, buy and
  hold, buy and hold scaled to the same time in the market (overnight moves included; the
  daytime-only version is reported alongside).
- Thresholds are fixed now (drop below 0, min_edge 0.25, edge multiple 2); nothing is tuned.
- Design period: decisions whose next week ends before SPLIT. Confirmation: the rest, run once
  at the end, continuing each symbol's job and state from the design period. The design report
  is written (and time-stamped) before the confirmation runs.
- Pass, per version, in BOTH periods:
  a. the 95% range of the weekly difference (picks minus time-matched buy and hold, overnight
     included, account level) includes zero or lies above it;
  b. no job is kept more than 2 weeks in a row with a negative out-of-sample score;
  c. with all switches off the results equal today's exactly (checked against the cost study's
     replay, data/cost_study.json).
- Costs: recalibration's 2 bps per side, and 1 bp per side for SPY and QQQ as a sensitivity.
- Statistics are grouped by week (one number per week), so one busy week can't fake certainty.
"""
from __future__ import annotations

import json
import math
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from . import cost_study as cs
from . import recalibrate as rc

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "recal_fix_study.json"
PARTS = ROOT / "data" / "recal_fix"
SPLIT = date(2026, 3, 23)            # data start 2024-09-23 + 18 months
VERSIONS: Dict[str, Dict] = {
    "today": {},
    "option1": {"allow_drop": True},
    "option2": {"benchmark": "matched_buy_hold"},
    "options12": {"allow_drop": True, "benchmark": "matched_buy_hold"},
    "options12_edge": {"allow_drop": True, "benchmark": "matched_buy_hold", "min_edge_per_trade": True},
}
LABELS = {"today": "Today (all switches off)", "option1": "Option 1: drop to no job", "option2": "Option 2: vs matched buy and hold",
          "options12": "Options 1 + 2", "options12_edge": "Options 1 + 2 + edge per trade (2x)"}
COSTS = {"base": None, "etf1bp": {"SPY": 1.0, "QQQ": 1.0}}   # side bps overrides per symbol
MAX_NEG_WEEKS = 2


# ----------------------------------------------------------------- running the replays
def fridays(data: cs.Data, r: Dict) -> List[date]:
    first = max(data.start(s, int(r["warmup_days"]) + int(r["lookback_days"])) for s in data.df5)
    return [d.date() for d in pd.date_range(first, data.as_of - timedelta(days=7), freq="W-FRI")]


def period_fridays(all_fridays: List[date], phase: str) -> List[date]:
    """design: decisions whose next week ends before SPLIT; confirm: the rest."""
    return [f for f in all_fridays if (f + timedelta(days=7) < SPLIT) == (phase == "design")]


def _r_for(base: Dict, version: str, sym: str, cost: str) -> Dict:
    r = {**base, **VERSIONS[version], "costs": {k: dict(v) for k, v in base["costs"].items()}}
    over = (COSTS[cost] or {}).get(sym)
    if over is not None:
        r["costs"] = {**r["costs"], rc.asset_class(sym): {"fee_bps": 0.0, "slippage_bps": float(over)}}
    return r


def _part(cost: str, version: str, phase: str) -> Path:
    return PARTS / f"{cost}_{version}_{phase}.json"


def run_one(cfg: Dict, version: str, cost: str, phase: str, symbols: Sequence[str], closes, as_of: date) -> str:
    """One version x cost level x period, every symbol; writes data/recal_fix/<cost>_<version>_<phase>.json.
    The confirmation continues from the design part's carried jobs and state."""
    data = cs.Data(symbols, closes=closes, as_of=as_of)
    base = rc.settings(cfg)
    fr = period_fridays(fridays(data, base), phase)
    carry_in = {}
    if phase == "confirm":
        d = json.loads(_part(cost, version, "design").read_text(encoding="utf-8"))
        carry_in = d["carry"]
    syms = [s for s in symbols if cost == "base" or s in (COSTS[cost] or {})]
    rows, carry = [], {}
    for sym in syms:
        c = dict(carry_in.get(sym) or {})
        rows += cs.replay_symbol(sym, data.df5[sym], _r_for(base, version, sym, cost), fr, carry=c)
        carry[sym] = c
    PARTS.mkdir(parents=True, exist_ok=True)
    out = {"version": version, "cost": cost, "phase": phase, "fridays": [str(f) for f in fr], "symbols": syms,
           "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": rows, "carry": carry}
    _part(cost, version, phase).write_text(json.dumps(cs._clean(out)), encoding="utf-8")
    return f"{cost} {version} {phase}: {len(rows)} symbol-weeks"


def run_phase(cfg: Dict, phase: str, versions: Sequence[str] = tuple(VERSIONS), costs: Sequence[str] = tuple(COSTS),
              workers: int = 2, symbols: Optional[Sequence[str]] = None, progress=print) -> None:
    syms = list(symbols or cs.universe_symbols())
    data = cs.Data(syms)  # once, for the calendar and the common last day
    if phase == "confirm":
        missing = [str(_part(c, v, "design")) for c in costs for v in versions if not _part(c, v, "design").exists()]
        if missing:
            raise RuntimeError(f"run the design phase first: {missing}")
        if not (PARTS / "design_frozen.json").exists():
            raise RuntimeError("write the design report first (--report), so the design period is frozen before confirmation")
    tasks = [(v, c) for c in costs for v in versions if not _part(c, v, phase).exists()]  # finished parts are kept
    for v, c in [(v, c) for c in costs for v in versions if _part(c, v, phase).exists()]:
        progress(f"{c} {v} {phase}: already done, kept")
    # each worker holds one symbol's backtests in memory (~1-2 GB); keep workers low on this machine
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(run_one, cfg, v, c, phase, syms, data.closes, data.as_of) for v, c in tasks]
        for f in futs:
            progress(f.result())


# ----------------------------------------------------------------- the report
def _t_ci(x: np.ndarray) -> List[Optional[float]]:
    x = np.asarray(x, float)
    if len(x) < 3:
        return [None, None]
    se = x.std(ddof=1) / math.sqrt(len(x))
    return [float(x.mean() - 1.96 * se), float(x.mean() + 1.96 * se)]


def _weekly(rows: pd.DataFrame, alloc: float) -> pd.DataFrame:
    """One line per Friday: the account's next-week result (each symbol gets `alloc` of the account,
    the rest is cash) for the picks and every reference, in % of the account."""
    g = rows.groupby("friday")
    w = pd.DataFrame({
        "picks": g["next_%"].sum() * alloc, "no_trading": 0.0, "buy_hold": g["buy_hold_next_%"].sum() * alloc,
        "matched": g["matched_bh_next_%"].sum() * alloc, "matched_day": g["matched_bh_day_next_%"].sum() * alloc,
        "cost": g["next_cost_%"].sum() * alloc, "invested": g["next_exposure"].sum() * alloc * 100,
        "symbols": g.size(),
    }).sort_index()
    return w


def _curve(x: pd.Series) -> Dict:
    eq = (1 + x / 100).cumprod()
    dd = float((eq / eq.cummax() - 1).min() * 100) if len(eq) else 0.0
    return {"total_%": round(float((eq.iloc[-1] - 1) * 100), 2) if len(eq) else 0.0, "max_drawdown_%": round(dd, 2),
            "worst_week_%": round(float(x.min()), 2) if len(x) else 0.0}


def _vs(w: pd.DataFrame, ref: str) -> Dict:
    d = (w.picks - w[ref]).to_numpy()
    lo, hi = _t_ci(d)
    return {"mean_%": round(float(d.mean()), 4) if len(d) else None, "ci95": [None if lo is None else round(lo, 4), None if hi is None else round(hi, 4)],
            "weeks_ahead_%": round(float((d > 0).mean() * 100), 1) if len(d) else None,
            "clearly_better": bool(lo is not None and lo > 0), "clearly_worse": bool(hi is not None and hi < 0)}


def _neg_runs(rows: pd.DataFrame) -> Dict:
    """Criterion b: the longest run of consecutive weeks a symbol kept a job with a negative score."""
    worst, weeks = 0, 0
    for _, g in rows.sort_values("friday").groupby("symbol"):
        run = 0
        for has, sc in zip(g.job != rc.describe(None), g.job_score):
            run = run + 1 if (has and sc < 0) else 0
            weeks += int(has and sc < 0)
            worst = max(worst, run)
    return {"longest_run_weeks": worst, "negative_job_weeks": weeks}


def _score_predicts(raw: List[Dict]) -> Dict:
    within = {}
    for x in raw:
        c = cs._spearman([s["score"] for s in x["strategies"]], [s["next_%"] for s in x["strategies"]])
        if c is not None:
            within.setdefault(x["friday"], []).append(c)
    allc = [c for v in within.values() for c in v]
    return {"mean_rank_corr": round(float(np.mean(allc)), 3) if allc else None,
            "ci95": cs._boot_ci(within, lambda v: float(np.mean(v)) if v else None), "n": len(allc)}


def summarize(raw: List[Dict], alloc: float, months: float) -> Dict:
    """Everything the user asked for, for one version x period x cost level."""
    rows = pd.DataFrame([{k: v for k, v in x.items() if k != "strategies"} for x in raw])
    if rows.empty:
        return {}
    w = _weekly(rows, alloc)
    has = rows.job != rc.describe(None)
    out = {
        "weeks": int(len(w)), "symbol_weeks": int(len(rows)), "with_job": int(has.sum()), "no_job": int((~has).sum()),
        "with_job_%": round(float(has.mean() * 100), 1),
        "changes": int(rows.changed.sum()), "changes_per_month": round(float(rows.changed.sum() / max(months, 1e-9)), 2),
        "drops": int((rows.decision == "drop").sum()),
        "round_trips": int(rows.next_trades.sum()), "cost_paid_%": round(float(w.cost.sum()), 2),
        "avg_invested_%": round(float(w.invested.mean()), 1),
        "account": {k: _curve(w[k]) for k in ("picks", "buy_hold", "matched", "matched_day", "no_trading")},
        "vs": {ref: _vs(w, ref) for ref in ("no_trading", "buy_hold", "matched", "matched_day")},
        "negative_score_jobs": _neg_runs(rows),
        "score_predicts": _score_predicts(raw),
        "equity": [[f, round(float(v), 5)] for f, v in (1 + w.picks / 100).cumprod().items()],
        "equity_refs": {k: [[f, round(float(v), 5)] for f, v in (1 + w[k] / 100).cumprod().items()] for k in ("buy_hold", "matched")},
    }
    per = {}
    for sym, g in rows.groupby("symbol"):
        gw = _weekly(g, 1.0)
        per[sym] = {"with_job_%": round(float((g.job != rc.describe(None)).mean() * 100), 1), "changes": int(g.changed.sum()),
                    "round_trips": int(g.next_trades.sum()), "picks_%": _curve(gw.picks)["total_%"],
                    "matched_%": _curve(gw.matched)["total_%"], "buy_hold_%": _curve(gw.buy_hold)["total_%"],
                    "vs_matched": _vs(gw, "matched"), "longest_negative_run": _neg_runs(g)["longest_run_weeks"], "job_now": g.job.iloc[-1]}
    out["per_symbol"] = per
    for grp, members in (("etfs", rc.ETFS), ("stocks", None)):
        g = rows[rows.symbol.isin(members)] if members else rows[~rows.symbol.isin(rc.ETFS)]
        if len(g):
            gw = _weekly(g, alloc)
            out[grp] = {"symbols": sorted(g.symbol.unique()), "with_job_%": round(float((g.job != rc.describe(None)).mean() * 100), 1),
                        "account": {k: _curve(gw[k]) for k in ("picks", "matched", "buy_hold")}, "vs_matched": _vs(gw, "matched")}
    return out


def _load_rows(cost: str, version: str, phase: str) -> List[Dict]:
    """Rows of a part; for the 1-bp sensitivity, SPY/QQQ from that run and every other symbol from the base run."""
    p = _part(cost, version, phase)
    if not p.exists():
        return []
    rows = json.loads(p.read_text(encoding="utf-8"))["rows"]
    if cost != "base":
        over = set(COSTS[cost])
        base = json.loads(_part("base", version, phase).read_text(encoding="utf-8"))["rows"] if _part("base", version, phase).exists() else []
        rows = [x for x in base if x["symbol"] not in over] + rows
    return rows


def matches_today(raw: List[Dict], ref_path: Path = cs.OUT) -> Dict:
    """Criterion c: the 'today' replay against the cost study's replay (run before the switches
    existed), symbol-week by symbol-week."""
    if not ref_path.exists():
        return {"checked": 0, "equal": False, "note": "no cost study to compare with"}
    ref = {(x["symbol"], x["friday"]): x for x in json.loads(ref_path.read_text(encoding="utf-8")).get("replay", {}).get("rows", [])}
    n, bad = 0, []
    for x in raw:
        y = ref.get((x["symbol"], x["friday"]))
        if y is None:
            continue
        n += 1
        if x["job"] != y["job"] or x["decision"] != y["decision"] or abs(x["next_%"] - y["next_%"]) > 1e-9 \
                or abs(x["job_score"] - y["job_score"]) > 1e-9:
            bad.append(f"{x['symbol']} {x['friday']}")
    return {"checked": n, "different": len(bad), "examples": bad[:5], "equal": n > 0 and not bad}


def criteria(s: Dict) -> Dict:
    if not s:
        return {"a": None, "b": None}
    hi = s["vs"]["matched"]["ci95"][1]
    return {"a": bool(hi is not None and hi >= 0), "b": bool(s["negative_score_jobs"]["longest_run_weeks"] <= MAX_NEG_WEEKS)}


def report(cfg: Dict, freeze_design: bool = True) -> Dict:
    r = rc.settings(cfg)
    alloc = float(r["allocation_pct"]) / 100
    res: Dict = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "split": str(SPLIT),
                 "versions": {v: {"label": LABELS[v], "switches": VERSIONS[v]} for v in VERSIONS},
                 "thresholds": {"drop_below": 0, "min_edge": r["min_edge"], "edge_multiple": rc.DEFAULT_EDGE_MULTIPLE,
                                "max_negative_weeks": MAX_NEG_WEEKS, "allocation_%": r["allocation_pct"]},
                 "costs": {"base": "recalibration's costs (2 bps per side)", "etf1bp": "1 bp per side for SPY and QQQ"},
                 "results": {}}
    frozen = PARTS / "design_frozen.json"
    phases = [ph for ph in ("design", "confirm") if any(_part("base", v, ph).exists() for v in VERSIONS)]
    for cost in COSTS:
        for v in VERSIONS:
            for ph in phases:
                raw = _load_rows(cost, v, ph)
                if not raw:
                    continue
                fr = sorted({x["friday"] for x in raw})
                months = (date.fromisoformat(fr[-1]) - date.fromisoformat(fr[0])).days / 30.44 + 7 / 30.44
                s = summarize(raw, alloc, months)
                s["from"], s["to"] = fr[0], fr[-1]
                s["criteria"] = criteria(s)
                res["results"].setdefault(cost, {}).setdefault(v, {})[ph] = s
    today_raw = _load_rows("base", "today", "design") + _load_rows("base", "today", "confirm")
    res["criterion_c"] = matches_today(today_raw)
    res["passes"] = {}
    for cost, by_v in res["results"].items():
        for v, by_p in by_v.items():
            crit = {ph: by_p[ph]["criteria"] for ph in by_p}
            ok = len(crit) == 2 and all(c["a"] and c["b"] for c in crit.values()) and res["criterion_c"]["equal"]
            res["passes"].setdefault(cost, {})[v] = {"pass": bool(ok), "periods": crit, "complete": len(crit) == 2}
    # tests run and wins luck alone would give: every version x reference comparison and every
    # version x symbol comparison against matched buy and hold, per period and cost level
    n_tests = sum(len(s["vs"]) + len(s["per_symbol"]) for by_v in res["results"].values() for by_p in by_v.values() for s in by_p.values())
    wins = sum(sum(x["clearly_better"] for x in s["vs"].values()) + sum(p["vs_matched"]["clearly_better"] for p in s["per_symbol"].values())
               for by_v in res["results"].values() for by_p in by_v.values() for s in by_p.values())
    res["tests"] = {"comparisons": n_tests, "clearly_better": wins, "expected_by_luck": round(0.025 * n_tests, 1)}
    if freeze_design and "design" in phases and not frozen.exists() and "confirm" not in phases:
        PARTS.mkdir(parents=True, exist_ok=True)
        frozen.write_text(json.dumps({"frozen_at": res["generated"], "note": "design period reported; nothing may be changed "
                                      "before the confirmation run", "passes_design": {c: {v: x["periods"].get("design")
                                      for v, x in by.items()} for c, by in res["passes"].items()}}), encoding="utf-8")
    if frozen.exists():
        res["design_frozen"] = json.loads(frozen.read_text(encoding="utf-8"))
    res["summary"] = summary(res)
    cs.save(cs._clean(res), OUT)
    return res


# ----------------------------------------------------------------- plain-English summary
def _f(v, d=2, sign=True) -> str:
    return "–" if v is None else (f"{v:+.{d}f}" if sign else f"{v:.{d}f}")


def summary(res: Dict) -> List[str]:
    from . import plain
    T = plain.RECAL_FIX
    lines: List[str] = []
    base = res["results"].get("base", {})
    phases = [ph for ph in ("design", "confirm") if any(ph in by for by in base.values())]
    if "confirm" not in phases:
        lines.append(T["design_only"].format(split=res["split"]))
    for v, by in base.items():
        p = res["passes"]["base"][v]
        verdict = T["pass"] if p["pass"] else T["fail_incomplete"] if not p["complete"] else T["fail"]
        why = []
        for ph, c in p["periods"].items():
            name = T["period_" + ph]
            if not c["a"]:
                why.append(T["why_a"].format(period=name))
            if not c["b"]:
                why.append(T["why_b"].format(period=name, n=by[ph]["negative_score_jobs"]["longest_run_weeks"]))
        if not res["criterion_c"]["equal"]:
            why.append(T["why_c"])
        last = by.get("confirm") or by.get("design")
        m = last["vs"]["matched"]
        lines.append(T["version"].format(label=res["versions"][v]["label"], verdict=verdict, why="; ".join(why) if why else T["all_met"],
                                         period=T["period_" + ("confirm" if "confirm" in by else "design")],
                                         picks=_f(last["account"]["picks"]["total_%"], 1), matched=_f(last["account"]["matched"]["total_%"], 1),
                                         bh=_f(last["account"]["buy_hold"]["total_%"], 1), job=_f(last["with_job_%"], 0, False),
                                         d=_f(m["mean_%"], 3), lo=_f(m["ci95"][0], 3), hi=_f(m["ci95"][1], 3),
                                         rt=last["round_trips"], cost=_f(last["cost_paid_%"], 2, False)))
        if last["with_job_%"] < 20:
            lines.append(T["mostly_cash"].format(label=res["versions"][v]["label"], job=_f(last["with_job_%"], 0, False),
                                                 inv=_f(last["avg_invested_%"], 1, False), rt=last["round_trips"],
                                                 picks=_f(last["account"]["picks"]["total_%"], 1)))
    sens = res["passes"].get("etf1bp", {})
    changed = [res["versions"][v]["label"] for v, x in sens.items() if x["pass"] != res["passes"]["base"].get(v, {}).get("pass")]
    if sens:
        lines.append(T["costs_changed"].format(v=", ".join(changed)) if changed else T["costs_same"])
    sp = [(res["versions"][v]["label"], (by.get("confirm") or by.get("design"))["score_predicts"]) for v, by in base.items()]
    lines.append(T["score"].format(items="; ".join(f"{lab}: {_f(x['mean_rank_corr'], 3)} ({_f(x['ci95'][0], 3)} to {_f(x['ci95'][1], 3)})"
                                                    for lab, x in sp)))
    t = res["tests"]
    lines.append(T["tests"].format(n=t["comparisons"], luck=_f(t["expected_by_luck"], 0, False), wins=t["clearly_better"]))
    c = res["criterion_c"]
    lines.append(T["c_ok"].format(n=c["checked"]) if c["equal"] else T["c_bad"].format(n=c["checked"], d=c.get("different", "–")))
    lines.append(T["limits"])
    return lines


def main():
    import argparse
    import sys
    import yaml
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Recalibration switch study (study only).")
    ap.add_argument("--phase", choices=["design", "confirm"])
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--versions", default=",".join(VERSIONS))
    ap.add_argument("--costs", default=",".join(COSTS))
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    if a.phase:
        run_phase(cfg, a.phase, [v for v in a.versions.split(",") if v], [c for c in a.costs.split(",") if c], a.workers,
                  progress=lambda m: print(m, flush=True))
    if a.report:
        res = report(cfg)
        print("\n".join(res["summary"]))
        print("saved", OUT)


if __name__ == "__main__":
    main()
