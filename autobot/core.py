"""
Core holding: one index ETF held long term (design: docs/CORE_DESIGN.md).

Live (config live.core, chosen 2026-10-05 after phase 1): a PLAIN hold, no timing rule.
  core: {enabled: true, symbol: VOO, allocation_pct: 30, rebalance_band_pct: 5}
The bot buys the core up to allocation_pct of equity, then trades it again only when its weight
drifts more than rebalance_band_pct points away (checked once per trading day, at the first open
tick). It is never sold before the close, by the daily loss limit or by the profit lock (those
measure the trading jobs only), and Stop + flatten keeps it unless "also sell the core" is ticked.

The 10-month-average rule below stays for the record: it failed its checkpoint on real daily
closes (docs/CORE_DESIGN.md, "Phase 1 result"), so the bot does not use it.

Rule, decided at each month's last regular-session close: IN if that close is above the average
of the last `months` month-end closes (that close included), else OUT (money in `out_symbol`, or
cash). Traded at the next session's open. The same function decides for the live bot and for the
backtest below, so they can't drift apart.

    python -m autobot.core --backtest          # VOO / SGOV daily since 2020, writes data/core_backtest.json
    python -m autobot.core --sp500             # the same rule on FRED's daily S&P 500 (10 years, real closes)

Only completed months count: a month is complete once its last trading day has closed, i.e. the
data has a bar from a later month or `as_of` is in a later month. The current month's bars are
never used for a decision.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

NY = "America/New_York"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "core_backtest.json"
DEFAULTS = {"enabled": False, "symbol": "VOO", "allocation_pct": 30, "rebalance_band_pct": 5}
OPEN_MULT = 2.0      # trades at the open cost 2x the normal cost per side (as in the daily study)
COST_SIDE = 0.0002   # 2 bps per side, the ETF cost recalibration uses


def settings(cfg: Optional[Dict]) -> Dict:
    return {**DEFAULTS, **(cfg or {})}


def validate(cfg, job_symbols=(), max_gross_pct: float = 100.0) -> List[str]:
    """Problems with live.core (empty = OK)."""
    if cfg is None:
        return []
    if not isinstance(cfg, dict):
        return ["core must be a mapping"]
    errs = []
    unknown = set(cfg) - set(DEFAULTS)
    if unknown:
        errs.append(f"core: unknown settings {', '.join(sorted(unknown))}")
    if not isinstance(cfg.get("enabled", False), bool):
        errs.append("core.enabled must be true or false")
    sym = cfg.get("symbol", DEFAULTS["symbol"])
    if not isinstance(sym, str) or not sym.strip() or "/" in sym:
        errs.append("core.symbol must be a stock or ETF symbol (no crypto)")
    a = cfg.get("allocation_pct", DEFAULTS["allocation_pct"])
    if not isinstance(a, (int, float)) or isinstance(a, bool) or not 0 < a <= 100:
        errs.append("core.allocation_pct must be between 0 and 100")
    elif a > max_gross_pct:
        errs.append(f"core.allocation_pct ({a}) can't be above risk.max_gross_pct ({max_gross_pct})")
    b = cfg.get("rebalance_band_pct", DEFAULTS["rebalance_band_pct"])
    if not isinstance(b, (int, float)) or isinstance(b, bool) or not 0 < b <= 50:
        errs.append("core.rebalance_band_pct must be between 0 and 50")
    if cfg.get("enabled") and sym in set(job_symbols):
        errs.append(f"core.symbol {sym} is also in an enabled job; the core needs a symbol of its own")
    return errs


def rebalance(qty: float, price: float, equity: float, cfg: Dict, first_today: bool) -> Optional[Dict]:
    """The core order for this tick, or None. Acts only on the first open tick of a trading day:
    to the target when nothing is held yet, or when the weight is more than the band away."""
    c = settings(cfg)
    if not c["enabled"] or not first_today or price <= 0 or equity <= 0:
        return None
    target = float(np.floor(equity * float(c["allocation_pct"]) / 100 / price))
    weight = qty * price / equity * 100
    drift = weight - float(c["allocation_pct"])
    if qty > 0 and abs(drift) <= float(c["rebalance_band_pct"]):
        return None
    delta = target - qty
    if delta == 0:
        return None
    why = (f"Core: buying {c['symbol']} up to {c['allocation_pct']:g}% of the account" if qty == 0 else
           f"Core: {c['symbol']} is {weight:.1f}% of the account, more than {c['rebalance_band_pct']:g} points "
           f"{'above' if drift > 0 else 'below'} its {c['allocation_pct']:g}%; back to the target")
    return {"qty": delta, "target": target, "weight_%": round(weight, 2), "reason": why}


def month_end_closes(daily: pd.DataFrame, as_of: Optional[date] = None) -> pd.Series:
    """The last close of every COMPLETED month, indexed by month (Period). `as_of` (a New York
    date) also completes the month of the last bar when it lies in a later month."""
    if daily is None or daily.empty:
        return pd.Series(dtype=float)
    m = daily.index.tz_convert(NY).tz_localize(None).to_period("M")
    last = daily.close.groupby(m).last()
    cutoff = pd.Period(as_of, "M") if as_of else m[-1]
    return last[last.index < cutoff]


def decide(daily: pd.DataFrame, months: int = 10, as_of: Optional[date] = None) -> Optional[Dict]:
    """The decision from the newest completed month end, or None without `months` of them.
    switch_level: the next month-end close at which the decision would change (with the other
    months fixed, close > average of the 10 incl. itself  <=>  close > average of the other 9)."""
    me = month_end_closes(daily, as_of)
    if len(me) < months:
        return None
    window = me.iloc[-months:]
    avg = float(window.mean())
    close = float(window.iloc[-1])
    nxt = me.iloc[-(months - 1):]
    return {"month": str(me.index[-1]), "in": bool(close > avg), "close": round(close, 4), "average": round(avg, 4),
            "distance_%": round((close / avg - 1) * 100, 2), "switch_level": round(float(nxt.mean()), 4),
            "months": months}


def signal(daily: pd.DataFrame, months: int = 10) -> pd.Series:
    """Per daily bar: 1 on the bar where a month ends with an IN decision and every following
    bar until the next month end, 0 for OUT, NaN before enough history. Decided at each month's
    last bar, so it is a target 'at that close' like every strategy signal (traded next open)."""
    ny = daily.index.tz_convert(NY).tz_localize(None)
    m = ny.to_period("M")
    is_end = np.r_[m[1:] != m[:-1], False]       # the last bar of the data is never a finished month
    ends = daily.close[is_end]
    avg = ends.rolling(months).mean()
    dec = (ends > avg).astype(float).where(avg.notna())
    return dec.reindex(daily.index).ffill()


# ----------------------------------------------------------------- backtest
def replay(px: pd.DataFrame, target_in: pd.Series, cost_side: float = COST_SIDE) -> pd.DataFrame:
    """Daily returns of IN -> `in` column, OUT -> `out` column (price columns of `px`, closes and
    opens), the target from day t's close traded at day t+1's open: overnight gap on yesterday's
    holding, open-to-close on today's, costs on the amount moved at the open."""
    w = target_in.reindex(px.index).ffill().fillna(0.0)
    pos = np.r_[0.0, w.to_numpy()[:-1]]          # weight in IN during day t (target of t-1)
    prev = np.r_[0.0, pos[:-1]]
    rets = {}
    for col, weight, before in (("in", pos, prev), ("out", 1 - pos, 1 - prev)):
        o, c = px[f"{col}_open"].to_numpy(float), px[f"{col}_close"].to_numpy(float)
        gap = np.r_[0.0, o[1:] / c[:-1] - 1]
        x = c / o - 1
        rets[col] = (1 + before * gap) * (1 + weight * x) - 1 - np.abs(weight - before) * cost_side * OPEN_MULT
    ret = rets["in"] + rets["out"]
    return pd.DataFrame({"ret": ret, "w_in": pos}, index=px.index)


def prices(in_df: pd.DataFrame, out_df: Optional[pd.DataFrame]) -> pd.DataFrame:
    px = pd.DataFrame({"in_open": in_df.open, "in_close": in_df.close})
    if out_df is not None:
        o = out_df[["open", "close"]].reindex(px.index).ffill()   # thin IEX days: last price
        px["out_open"], px["out_close"] = o.open, o.close
    else:
        px["out_open"] = px["out_close"] = 1.0                   # plain cash
    return px.dropna()


def _stats(r: pd.Series, w: Optional[pd.Series] = None) -> Dict:
    eq = (1 + r).cumprod()
    years = len(r) / 252
    out = {"return_%": round(float(eq.iloc[-1] - 1) * 100, 1),
           "yearly_%": round(float(eq.iloc[-1] ** (1 / years) - 1) * 100, 1),
           "worst_drop_%": round(float((eq / eq.cummax() - 1).min() * 100), 1),
           "sharpe": round(float(r.mean() / r.std() * np.sqrt(252)), 2) if r.std() > 0 else 0.0}
    if w is not None:
        out["in_%"] = round(float(w.mean() * 100), 1)
    return out


def backtest(in_df: pd.DataFrame, out_df: Optional[pd.DataFrame], months: int = 10) -> Dict:
    sig = signal(in_df, months)
    rows = {}
    start = sig.first_valid_index()
    for label, out in (("rule, SGOV when out", out_df), ("rule, cash when out", None)):
        f = replay(prices(in_df, out), sig)
        f = f[f.index > start]
        rows[label] = f
    hold = replay(prices(in_df, None), pd.Series(1.0, index=in_df.index))
    rows["buy and hold"] = hold[hold.index > start]
    res = {k: _stats(f.ret, f.w_in) for k, f in rows.items()}
    by_year = {k: {str(y): round(float(((1 + g).prod() - 1) * 100), 1)
                   for y, g in f.ret.groupby(f.index.tz_convert(NY).year)} for k, f in rows.items()}
    ny = in_df.index.tz_convert(NY).tz_localize(None).to_period("M")
    decisions = []
    for p in sorted(set(ny)):
        upto = in_df[ny <= p]
        d = decide(upto, months, as_of=(p + 1).to_timestamp().date())
        if d:
            decisions.append(d)
    switches = [d for i, d in enumerate(decisions) if i and d["in"] != decisions[i - 1]["in"]]
    return {"from": str(rows["buy and hold"].index[0].tz_convert(NY).date()), "to": str(in_df.index[-1].tz_convert(NY).date()),
            "results": res, "by_year": by_year, "decisions": decisions, "switches": switches}


def fred_sp500(path: Path = ROOT / "data" / "macro" / "SP500.csv") -> pd.DataFrame:
    """FRED's daily S&P 500 closes (index only, no dividends, no opens: the next close stands in
    for the next open), stamped at 16:00 New York."""
    f = pd.read_csv(path)
    f.columns = ["date", "close"]
    f["close"] = pd.to_numeric(f.close, errors="coerce")
    f = f.dropna()
    idx = (pd.DatetimeIndex(pd.to_datetime(f.date)).tz_localize(NY) + pd.Timedelta(hours=16)).tz_convert("UTC")
    return pd.DataFrame({"open": f.close.to_numpy(), "close": f.close.to_numpy()}, index=idx)


def sp500_check(df: pd.DataFrame, months: int = 10) -> Dict:
    sig = signal(df, months)
    px = pd.DataFrame({"in_open": df.close, "in_close": df.close, "out_open": 1.0, "out_close": 1.0}, index=df.index)
    start = sig.first_valid_index()
    out = {}
    for label, t in (("rule, cash when out", sig), ("buy and hold", pd.Series(1.0, index=df.index))):
        f = replay(px, t)
        f = f[f.index > start]
        out[label] = {**_stats(f.ret, f.w_in),
                      "by_year": {str(y): round(float(((1 + g).prod() - 1) * 100), 1) for y, g in f.ret.groupby(f.index.tz_convert(NY).year)}}
    e = sig.dropna()
    out["switches"] = [(str(t.tz_convert(NY).date()), "IN" if v else "OUT") for t, v in e[e.diff() != 0].items()][1:]
    return out


def main():
    import argparse
    import sys
    from .data import fetch_bars
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Core holding rule.")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--symbol", default=DEFAULTS["symbol"])
    ap.add_argument("--out-symbol", default=DEFAULTS["out_symbol"])
    ap.add_argument("--sp500", action="store_true", help="the rule on data/macro/SP500.csv (FRED)")
    a = ap.parse_args()
    if a.sp500:
        print(json.dumps(sp500_check(fred_sp500()), indent=1))
        return
    today = pd.Timestamp.now(tz=NY).date()
    load = lambda s: (lambda d: d[np.asarray(d.index.tz_convert(NY).date) < today])(fetch_bars([s], "1Day", 2600)[s])  # noqa: E731
    in_df, out_df = load(a.symbol), load(a.out_symbol)
    print("now:", decide(in_df, as_of=today))
    if a.backtest:
        res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "symbol": a.symbol,
               "out_symbol": a.out_symbol, **backtest(in_df, out_df)}
        OUT.write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(json.dumps({k: res[k] for k in ("from", "to", "results", "by_year")}, indent=1))
        print("switches:", [(d["month"], "IN" if d["in"] else "OUT") for d in res["switches"]])
        print("saved", OUT)


if __name__ == "__main__":
    main()
