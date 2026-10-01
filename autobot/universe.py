"""
Universe screener: proposes which symbols the bot should trade, bucket by bucket.

    python -m autobot.universe            # rank now and write data/universe/{date}.json
    python -m autobot.universe --show     # print the latest proposal

Config `universe:` (config.yaml): buckets with min/max counts and candidate lists, ranking
weights, a dollar-volume floor and how often to run.

For each candidate, on the bot's timeframe (live.timeframe) over the last `lookback_days`
trading days:
  dollar volume  median of each day's sum(close x volume)       (how much is traded)
  range %        median over bars of (high - low) / close x 100  (how much it moves per bar)
Inside a bucket each measure becomes a percentile rank (0..1) and the score is their weighted
sum. A symbol below the floor, or with too little history, is left out.

Volume comes from Alpaca's free IEX feed, which sees only a few percent of all trading, so the
dollar figures are only good for comparing symbols with each other (crypto volume is
Alpaca's own venue). The floor is in the same IEX dollars.

Hysteresis: a symbol selected last time ("held") stays unless it falls below rank N + 2
(N = the bucket's max), so the list doesn't churn on small ranking changes.

The output is a PROPOSAL only: nothing here changes config.yaml or the bot. Applying it goes
through the recalibration apply step.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd
import yaml

from . import plain
from .data import fetch_bars, is_crypto

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "universe"
NY = "America/New_York"

DEFAULTS = {
    "every_days": 7,
    "lookback_days": 20,
    "hysteresis": 2,
    "weights": {"dollar_volume": 0.5, "range_pct": 0.5},
    "min_dollar_volume": 1_000_000,  # IEX dollars per day
    "total": {"min": 10, "max": 15},
    "buckets": {
        "tech": {"min": 5, "max": 6, "candidates": ["NVDA", "AAPL", "MSFT", "AMZN", "META", "TSLA", "AMD"]},
        "index_etf": {"min": 2, "max": 2, "candidates": ["QQQ", "SPY"]},
        "commodity_etf": {"min": 2, "max": 3, "candidates": ["USO", "GLD", "SLV"]},
        "crypto": {"min": 1, "max": 2, "candidates": ["BTC/USD", "ETH/USD"], "min_dollar_volume": 0},
    },
}


def settings(cfg: Optional[Dict] = None) -> Dict:
    """config.yaml `universe:` over the defaults, plus the bot's timeframe."""
    if cfg is None:
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    u = {**DEFAULTS, **(cfg.get("universe") or {})}
    u["timeframe"] = u.get("timeframe") or cfg["live"]["timeframe"]
    return u


# ----------------------------------------------------------------- measures
def measures(df: pd.DataFrame, lookback_days: int) -> Dict:
    """Median daily dollar volume and median bar range % over the last `lookback_days`
    trading days (New York dates) of the bars."""
    if df is None or df.empty:
        return {"days": 0}
    day = df.index.tz_convert(NY).date
    days = sorted(set(day))[-lookback_days:]
    part = df[pd.Series(day, index=df.index).isin(days).to_numpy()]
    dollar = (part.close * part.volume).groupby(part.index.tz_convert(NY).date).sum()
    rng = ((part.high - part.low) / part.close * 100).replace([np.inf, -np.inf], np.nan).dropna()
    return {"days": len(days), "dollar_volume": float(dollar.median()) if len(dollar) else 0.0,
            "range_pct": float(rng.median()) if len(rng) else 0.0}


def _money(v: float) -> str:
    return f"{v / 1e6:,.1f}M" if v >= 1e6 else f"{v:,.0f}"


# ----------------------------------------------------------------- ranking one bucket
def rank_bucket(name: str, spec: Dict, meas: Dict[str, Dict], held: List[str], u: Dict) -> Dict:
    """Score, rank and select one bucket. `meas` = {symbol: measures(...)}."""
    keep = int(spec["max"])
    limit = keep + int(u.get("hysteresis", 2))
    floor = float(spec.get("min_dollar_volume", u.get("min_dollar_volume", 0)))
    need = max(5, int(u["lookback_days"]) // 2)
    rows, ok = [], []
    for s in spec["candidates"]:
        m = meas.get(s) or {"days": 0}
        row = {"symbol": s, **m, "held": s in held}
        if m["days"] < need:
            row.update(selected=False, outcome="no_data",
                       reason=plain.UNIVERSE_REASONS["no_data"].format(days=m["days"], need=need))
        elif m["dollar_volume"] < floor:
            row.update(selected=False, outcome="floor",
                       reason=plain.UNIVERSE_REASONS["floor"].format(dv=_money(m["dollar_volume"]), floor=_money(floor)))
        else:
            ok.append(row)
        rows.append(row)
    n = len(ok)
    if n:
        w = u["weights"]
        dv = pd.Series([r["dollar_volume"] for r in ok]).rank(pct=True)
        rg = pd.Series([r["range_pct"] for r in ok]).rank(pct=True)
        tot = sum(w.values()) or 1.0
        for i, r in enumerate(ok):
            r["score"] = round((w.get("dollar_volume", 0) * dv[i] + w.get("range_pct", 0) * rg[i]) / tot, 4)
        # ties: the larger dollar volume first, then the symbol (stable, reproducible)
        ok.sort(key=lambda r: (-r["score"], -r["dollar_volume"], r["symbol"]))
        for i, r in enumerate(ok, 1):
            r["rank"] = i
        stay = [r for r in ok if r["held"] and r["rank"] <= limit]
        new = [r for r in ok if r not in stay][: max(0, keep - len(stay))]
        chosen = {r["symbol"] for r in stay + new}
        for r in ok:
            f = dict(rank=r["rank"], n=n, bucket=name, keep=keep, limit=limit)
            r["selected"] = r["symbol"] in chosen
            if r["selected"] and r["held"]:
                r["outcome"] = "kept" if r["rank"] <= keep else "kept_hysteresis"
            elif r["selected"]:
                r["outcome"] = "new"
            elif r["held"]:
                r["outcome"] = "dropped"
            else:
                r["outcome"] = "not_selected"
            r["reason"] = plain.UNIVERSE_REASONS[r["outcome"]].format(**f)
    selected = [r["symbol"] for r in sorted(rows, key=lambda r: r.get("rank", 1e9)) if r.get("selected")]
    out = {"min": spec["min"], "max": keep, "drop_below_rank": limit, "floor": floor, "rows": rows, "selected": selected,
           "warning": None}
    if len(selected) < int(spec["min"]):
        out["warning"] = plain.UNIVERSE_WARNINGS["bucket_short"].format(bucket=name, got=len(selected), min=spec["min"])
    return out


# ----------------------------------------------------------------- whole run
def latest(before: Optional[str] = None, out_dir: Optional[Path] = None) -> Optional[Dict]:
    """The newest proposal file (optionally strictly before a date 'YYYY-MM-DD')."""
    d = out_dir or OUT_DIR
    files = sorted(p for p in d.glob("*.json") if before is None or p.stem < before) if d.exists() else []
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def due(today: date, u: Dict, out_dir: Optional[Path] = None) -> bool:
    prev = latest(out_dir=out_dir)
    if prev is None:
        return True
    return (today - date.fromisoformat(prev["date"])).days >= int(u["every_days"])


def run(today: Optional[date] = None, cfg: Optional[Dict] = None, fetch: Optional[Callable] = None,
        out_dir: Optional[Path] = None, write: bool = True) -> Dict:
    """Rank every bucket and (by default) write data/universe/{date}.json.
    fetch(symbols, timeframe, days) -> {symbol: bars}; defaults to autobot.data.fetch_bars."""
    u = settings(cfg)
    today = today or pd.Timestamp.now(tz=NY).date()
    out_dir = out_dir or OUT_DIR
    prev = latest(before=today.isoformat(), out_dir=out_dir)
    held = prev["selected"] if prev else []
    cal_days = int(math.ceil(u["lookback_days"] * 7 / 5)) + 7  # calendar days that cover the trading days
    fetch = fetch or (lambda syms, tf, days: fetch_bars(syms, tf, days, rth_only=True))
    syms = list(dict.fromkeys(s for b in u["buckets"].values() for s in b["candidates"]))
    meas, errors = {}, {}
    try:
        bars = fetch(syms, u["timeframe"], cal_days)
    except Exception:  # noqa: BLE001  one symbol at a time so one bad symbol doesn't hide the rest
        bars = {}
        for s in syms:
            try:
                bars.update(fetch([s], u["timeframe"], cal_days))
            except Exception as e:  # noqa: BLE001
                errors[s] = str(e)
    for s in syms:
        meas[s] = measures(bars.get(s), int(u["lookback_days"]))
    buckets = {name: rank_bucket(name, spec, meas, held, u) for name, spec in u["buckets"].items()}
    selected = [s for b in buckets.values() for s in b["selected"]]
    warnings = [b["warning"] for b in buckets.values() if b["warning"]]
    tot = u.get("total") or {}
    if tot.get("min") and len(selected) < tot["min"]:
        warnings.append(plain.UNIVERSE_WARNINGS["total_short"].format(got=len(selected), min=tot["min"]))
    if tot.get("max") and len(selected) > tot["max"]:
        warnings.append(plain.UNIVERSE_WARNINGS["total_long"].format(got=len(selected), max=tot["max"]))
    res = {"date": today.isoformat(), "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "kind": "proposal", "timeframe": u["timeframe"], "lookback_days": u["lookback_days"], "weights": u["weights"],
           "volume_note": "Volume is from the IEX feed (part of the market): dollar figures are for comparing symbols only.",
           "previous_date": prev["date"] if prev else None, "previous": held, "selected": selected,
           "added": [s for s in selected if s not in held], "dropped": [s for s in held if s not in selected],
           "warnings": warnings, "errors": errors, "buckets": buckets}
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{today.isoformat()}.json").write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
    return res


def main():
    import argparse
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="print the latest proposal instead of running")
    a = ap.parse_args()
    res = latest() if a.show else run()
    if not res:
        print("No universe proposal yet.")
        return
    print(f"Universe proposal {res['date']} ({res['timeframe']}, {res['lookback_days']} trading days)")
    for name, b in res["buckets"].items():
        print(f"\n{name}: {', '.join(b['selected']) or '-'}")
        for r in sorted(b["rows"], key=lambda r: r.get("rank", 99)):
            dv = f"${_money(r['dollar_volume'])}/day" if r.get("days") else "-"
            rg = f"{r['range_pct']:.3f}%" if r.get("days") else "-"
            print(f"  {r.get('rank', '-')!s:>2} {r['symbol']:<8} {dv:>14} range {rg:>7}  {r['reason']}")
    for w in res["warnings"]:
        print("WARNING", w)
    print("\nAdded:", ", ".join(res["added"]) or "-", "| Dropped:", ", ".join(res["dropped"]) or "-")


if __name__ == "__main__":
    main()
