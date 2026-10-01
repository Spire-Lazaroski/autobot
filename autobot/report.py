"""
Plain-English trading report for non-technical readers.

    python -m autobot.report --backtest --strategy regime_switch --symbols SPY,QQQ --demo --days 5
    python -m autobot.report --date 2026-09-30             # the paper account, one day
    python -m autobot.report --from 2026-09-28 --to 2026-09-30

Every sentence is produced by a fixed rule from logged numbers (logs/decisions.jsonl,
trades.csv, the Alpaca paper account, or a backtest's own records). Nothing is guessed:
when a reason wasn't logged the report says "reason not recorded".

Output: reports/<name>.html (Spire-branded, self-contained, printable) + reports/<name>.pdf.
"""
from __future__ import annotations

import argparse
import html
import io
import json
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import plain, report_news
from .backtest import CostModel, round_trips, run_backtest
from .decisions import backtest_decisions, mood_adj, read_jsonl
from .strategies import STRATEGIES, get_signal

ROOT = Path(__file__).resolve().parent.parent
ASSETS = Path(__file__).resolve().parent / "assets"
REPORTS = ROOT / "reports"
LOGS = ROOT / "logs"
NY, LOCAL = ZoneInfo("America/New_York"), ZoneInfo("Europe/Skopje")
BRAND = json.loads((ASSETS / "brand.json").read_text(encoding="utf-8"))
EDGE = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")]
TIMELINE_MAX = 40
SWITCH_LIMIT = 8  # switches per symbol per day above which the report warns (also used by the SOP)


# ----------------------------------------------------------------- the data a report is built from
@dataclass
class ReportInput:
    title: str
    period_label: str
    source: str                      # "Paper account" / "Backtest on demo data" / ...
    kind: str                        # "live" | "backtest"
    is_demo: bool
    start_equity: float
    equity: pd.Series                # account value in $, time-indexed (UTC)
    benchmark: Optional[pd.Series]   # value of simply holding the benchmark, same start
    benchmark_symbol: str
    bars: Dict[str, pd.DataFrame]    # price bars per symbol for the period
    records: List[Dict]              # decision / regime / switch / event records in the period
    trades: List[Dict]               # round trips (see _trade_dict)
    limits: Dict = field(default_factory=dict)
    open_positions: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    news: Dict = field(default_factory=dict)  # report_news.collect(...); empty = no news section


def _trade_dict(symbol, side, qty_text, entry_time, entry_price, exit_time, exit_price, pnl, pnl_pct,
                entry_reason, exit_reason, entry_regime=None, open_=False) -> Dict:
    return dict(symbol=symbol, side=side, qty_text=qty_text, entry_time=entry_time, entry_price=entry_price,
                exit_time=exit_time, exit_price=exit_price, pnl=pnl, pnl_pct=pnl_pct, entry_reason=entry_reason,
                exit_reason=exit_reason, entry_regime=entry_regime, open=open_)


# ----------------------------------------------------------------- from a backtest
def from_backtest(data: Dict[str, pd.DataFrame], strategy: str, params: Dict | None = None, costs: CostModel = CostModel(),
                  allow_short: bool = False, is_demo: bool = False, days: Optional[int] = None,
                  capital: float = 100_000.0, benchmark_symbol: str = "SPY") -> ReportInput:
    """Equal money in every symbol (like the Lab's comparison), shown per $100,000."""
    spec = STRATEGIES[strategy]
    p = {**spec.defaults, **(params or {})}
    per = capital / len(data)
    rets, recs, trades, bars = {}, [], [], {}
    start = None
    if days:
        last = max(df.index[-1] for df in data.values())
        start = last - pd.Timedelta(days=days)
    for sym, df in data.items():
        bt = run_backtest(df, get_signal(strategy, df, p, allow_short), costs)
        sym_recs = backtest_decisions(df, sym, strategy, p, allow_short)
        by_bar = {(r["bar_time"]): r for r in sym_recs if r["type"] == "decision"}
        for t in round_trips(df, bt):
            if start is not None and t["exit_time"] < start:
                continue
            e_rec = by_bar.get(pd.Timestamp(t["entry_time"]).isoformat())
            i_exit = df.index.get_loc(t["exit_time"])
            x_rec = by_bar.get(df.index[i_exit].isoformat())
            pnl = per * t["return_%"] / 100
            still_open = i_exit == len(df) - 1 and bt.position.iloc[-1] != 0  # the data ended, the trade didn't
            trades.append(_trade_dict(
                sym, t["side"], f"about ${per:,.0f} worth", t["entry_time"], t["entry_price"],
                None if still_open else t["exit_time"], None if still_open else t["exit_price"], pnl, t["return_%"],
                e_rec["reason"] if e_rec else "reason not recorded",
                "still open at the end of the period" if still_open else (x_rec["reason"] if x_rec else "reason not recorded"),
                e_rec.get("regime") if e_rec else None, open_=still_open))
        rets[sym] = bt.ret
        recs += sym_recs
        bars[sym] = df
    port = pd.concat(rets, axis=1).fillna(0).mean(axis=1)
    bsym = benchmark_symbol if benchmark_symbol in data else next(iter(data))
    bench_ret = data[bsym].close.pct_change().reindex(port.index).fillna(0)
    if start is not None:
        port, bench_ret = port[port.index >= start], bench_ret[bench_ret.index >= start]
        recs = [r for r in recs if pd.Timestamp(r["bar_time"]) >= start]
        bars = {s: df[df.index >= start] for s, df in bars.items()}
    equity = capital * (1 + port).cumprod()
    bench = capital * (1 + bench_ret).cumprod()
    equity = pd.concat([pd.Series([capital], index=[equity.index[0] - pd.Timedelta(seconds=1)]), equity])
    bench = pd.concat([pd.Series([capital], index=[bench.index[0] - pd.Timedelta(seconds=1)]), bench])
    lo, hi = equity.index[1], equity.index[-1]
    notes = []
    if bsym != benchmark_symbol:
        notes.append(f"{benchmark_symbol} wasn't in this backtest, so the comparison uses simply holding {bsym}.")
    nd = report_news.collect(list(data), lo, hi + pd.Timedelta(seconds=1), "backtest",
                             news_aware=set(data) if strategy == "news_guard" else set(), is_demo=is_demo)
    return ReportInput(
        title=f"Backtest report: {strategy}", period_label=_period(lo, hi),
        source=f"Backtest on {'demo (made-up) data' if is_demo else 'Alpaca market data'} · {', '.join(data)} · "
               f"${capital:,.0f} split equally · costs {costs.fee_bps + costs.slippage_bps:g} bps per trade",
        kind="backtest", is_demo=is_demo, start_equity=capital, equity=equity, benchmark=bench, benchmark_symbol=bsym,
        bars=bars, records=sorted(recs, key=lambda r: r["bar_time"]), trades=sorted(trades, key=lambda t: t["entry_time"]),
        limits={}, notes=notes, news=nd)


# ----------------------------------------------------------------- from the paper account
def _ny_day_bounds(d0: date, d1: date):
    a = datetime(d0.year, d0.month, d0.day, tzinfo=NY).astimezone(timezone.utc)
    b = (datetime(d1.year, d1.month, d1.day, tzinfo=NY) + timedelta(days=1)).astimezone(timezone.utc)
    return pd.Timestamp(a), pd.Timestamp(b)


def from_live(d0: date, d1: date, tc=None, config: Dict | None = None) -> ReportInput:
    """The paper account between two New York dates (inclusive)."""
    import yaml
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest, GetPortfolioHistoryRequest

    from .data import _download, _keys, with_timeout
    if tc is None:
        from alpaca.trading.client import TradingClient
        tc = with_timeout(TradingClient(*_keys(), paper=True))
    cfg = config or yaml.safe_load((ROOT / "config.yaml").read_text())
    a, b = _ny_day_bounds(d0, d1)
    recs = [r for r in read_jsonl(LOGS / "decisions.jsonl")
            if r.get("source", "live") == "live" and a <= pd.Timestamp(r.get("bar_time") or r["time"]) < b]
    notes = []
    if not recs:
        notes.append("No decision log for this period (logs/decisions.jsonl). Trades are listed from the account, "
                     "but their reasons weren't recorded.")

    # account value through the period
    hist = tc.get_portfolio_history(GetPortfolioHistoryRequest(timeframe="5Min", start=a.to_pydatetime(),
                                                               end=b.to_pydatetime(), extended_hours=False))
    eq = pd.Series(hist.equity, index=pd.to_datetime(hist.timestamp, unit="s", utc=True), dtype=float).dropna()
    eq = eq[eq > 0]
    if eq.empty:
        raise ValueError("The account has no value history for this period (market closed?).")
    eq, jumps = remove_cash_jumps(eq)
    for t, v in jumps:
        notes.append(f"The broker's account-value history jumps by {_money(v, True)} at {_ny(t):%H:%M} on {_ny(t):%a %d %b}, "
                     "more than trading could move it in 5 minutes (this happens around deposits and in a paper account's "
                     "first days). The report leaves that jump out, so the result shows trading only.")
    start_equity = float(eq.iloc[0])

    # actual fills in the period
    orders = tc.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, after=(a - pd.Timedelta(days=5)).to_pydatetime(),
                                            until=b.to_pydatetime(), limit=500))
    fills = [{"time": pd.Timestamp(o.filled_at), "symbol": o.symbol, "side": getattr(o.side, "value", str(o.side)),
              "qty": float(o.filled_qty), "price": float(o.filled_avg_price), "order_id": str(o.id)}
             for o in orders if o.filled_at is not None and o.filled_avg_price]
    fills = sorted(fills, key=lambda f: f["time"])
    by_order = {oid: r for r in recs if r["type"] == "decision" for oid in r.get("order_ids") or []}
    first_pos = {}
    for r in recs:
        if r["type"] == "decision" and r["symbol"] not in first_pos:
            first_pos[r["symbol"]] = float(r.get("position_before") or 0)
    trades, open_pos = _round_trips_from_fills(fills, a, b, by_order, recs, first_pos)

    symbols = sorted({f["symbol"] for f in fills if a <= f["time"] < b} | {r["symbol"] for r in recs if "symbol" in r})
    bench_sym = "SPY"
    bars = {}
    for s in dict.fromkeys(symbols + [bench_sym]):
        try:
            df = _download(s if "/" in s or not s.endswith("USD") else s, cfg["live"]["timeframe"], a.to_pydatetime(),
                           min(b, pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=16)).to_pydatetime(), "iex")
            bars[s] = df
        except Exception as e:  # noqa: BLE001
            notes.append(f"Price data for {s} was not available ({e}).")
    bench = None
    if bench_sym in bars and len(bars[bench_sym]):
        c = bars[bench_sym].close
        bench = start_equity * c / c.iloc[0]
    jobs = [j for j in cfg["live"].get("jobs", []) if j.get("enabled", True)]
    job_syms = [s for j in jobs for s in j["symbols"]]
    nd = report_news.collect(sorted(set(symbols) | set(job_syms)), a, b, "live",
                             news_aware={s for j in jobs if j["strategy"] == "news_guard" for s in j["symbols"]},
                             watch_symbols=job_syms)
    return ReportInput(
        title="Daily trading report" if d0 == d1 else "Trading report", period_label=_period(a, b - pd.Timedelta(seconds=1), days_only=True),
        source="Alpaca paper account (pretend money)", kind="live", is_demo=False, start_equity=start_equity, equity=eq,
        benchmark=bench, benchmark_symbol=bench_sym, bars={s: d for s, d in bars.items() if s in symbols or s == bench_sym},
        records=sorted(recs, key=lambda r: r.get("bar_time") or r["time"]), trades=trades,
        limits=dict(cfg["live"].get("risk", {})), open_positions=open_pos, notes=notes, news=nd)


CASH_JUMP = 0.10  # one-bar change in account value above this share can't come from our trading


def remove_cash_jumps(eq: pd.Series):
    """Take out one-bar jumps larger than CASH_JUMP of the account value (deposits, or Alpaca's
    paper history counting the opening deposit twice): every later point is shifted back by the
    jump. Positions are capped far below this (risk.max_position_usd), so trading can't do it."""
    eq = eq.copy()
    jumps = []
    for i in range(1, len(eq)):
        d = eq.iloc[i] - eq.iloc[i - 1]
        if abs(d) > CASH_JUMP * eq.iloc[i - 1]:
            eq.iloc[i:] -= d
            jumps.append((eq.index[i], float(d)))
    return eq, jumps


def _round_trips_from_fills(fills, a, b, by_order, recs, first_pos):
    """Walk the fills per symbol; a trade opens when the position leaves zero and closes when it returns."""
    trades, open_pos = [], {}
    for sym in sorted({f["symbol"] for f in fills}):
        pos = first_pos.get(sym, 0.0)
        cur = None
        for f in [f for f in fills if f["symbol"] == sym]:
            signed = f["qty"] if f["side"] == "buy" else -f["qty"]
            in_period = a <= f["time"] < b
            if pos == 0 and signed != 0:
                cur = {"side": "long" if signed > 0 else "short", "qty": abs(signed), "cost": signed * f["price"],
                       "entry_time": f["time"], "entry_price": f["price"], "entry_order": f["order_id"], "in_period": in_period}
            elif cur is not None:
                cur["cost"] += signed * f["price"]
            pos = round(pos + signed, 6)
            if pos == 0 and cur is not None:
                if in_period:
                    e = by_order.get(cur["entry_order"]) or _nearest(recs, sym, cur["entry_time"])
                    x = by_order.get(f["order_id"]) or _nearest(recs, sym, f["time"])
                    pnl = -cur["cost"]
                    trades.append(_trade_dict(
                        sym, cur["side"], f"{cur['qty']:g} shares", cur["entry_time"], cur["entry_price"], f["time"], f["price"],
                        pnl, pnl / (cur["qty"] * cur["entry_price"]) * 100,
                        e["reason"] if e else "reason not recorded", x["reason"] if x else "reason not recorded",
                        e.get("regime") if e else None))
                cur = None
        if pos != 0:
            open_pos[sym] = pos
            if cur is not None and cur["in_period"]:
                e = by_order.get(cur["entry_order"]) or _nearest(recs, sym, cur["entry_time"])
                trades.append(_trade_dict(sym, cur["side"], f"{cur['qty']:g} shares", cur["entry_time"], cur["entry_price"],
                                          None, None, None, None, e["reason"] if e else "reason not recorded",
                                          "still open at the end of the period", e.get("regime") if e else None, open_=True))
    return sorted(trades, key=lambda t: t["entry_time"]), open_pos


def _nearest(recs, sym, t, window=pd.Timedelta(minutes=15)):
    """The decision with an order just before a fill (when the order id wasn't matched)."""
    best = None
    for r in recs:
        if r["type"] != "decision" or r["symbol"] != sym or r.get("action") in (None, "no change"):
            continue
        rt = pd.Timestamp(r["time"])
        if t - window <= rt <= t + pd.Timedelta(minutes=1):
            best = r
    return best


# ----------------------------------------------------------------- wording helpers
def _ny(ts) -> datetime:
    return pd.Timestamp(ts).tz_convert(NY).to_pydatetime()


def _period(a, b, days_only=False) -> str:
    da, db = _ny(a).date(), _ny(b).date()
    if da == db:
        return da.strftime("%A %d %B %Y")
    return f"{da.strftime('%a %d %b %Y')} to {db.strftime('%a %d %b %Y')}"


def _money(v: float, sign=False) -> str:
    s = f"${abs(v):,.2f}"
    return ("+" if v > 0 else "-" if v < 0 else "") + s if sign else ("-" + s if v < 0 else s)


def _pct(v: float, sign=True) -> str:
    return f"{v:+.2f}%" if sign else f"{v:.2f}%"


def _dur(a, b) -> str:
    m = int((pd.Timestamp(b) - pd.Timestamp(a)).total_seconds() // 60)
    if m < 60:
        return f"{m} min"
    if m < 60 * 24:
        return f"{m // 60} h {m % 60} min"
    return f"{m // 1440} days {(m % 1440) // 60} h"


class Words:
    """Collects glossary terms as the report uses them."""

    def __init__(self):
        self.used: set = set()

    def use(self, *terms):
        for t in terms:
            if t in plain.GLOSSARY:
                self.used.add(t)


def _esc(s) -> str:
    return html.escape(str(s))


# ----------------------------------------------------------------- analysis (fixed rules only)
def analyse(ri: ReportInput) -> Dict:
    end_eq = float(ri.equity.iloc[-1])
    pnl = end_eq - ri.start_equity
    pnl_pct = pnl / ri.start_equity * 100
    bench_pct = None
    if ri.benchmark is not None and len(ri.benchmark):
        bench_pct = (float(ri.benchmark.iloc[-1]) / float(ri.benchmark.iloc[0]) - 1) * 100
    closed = [t for t in ri.trades if not t["open"]]
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] < 0]
    dd = float((ri.equity / ri.equity.cummax() - 1).min() * 100)
    symbols = sorted({t["symbol"] for t in ri.trades})
    regimes = [r for r in ri.records if r["type"] == "regime"]
    switches = [r for r in ri.records if r["type"] == "switch"]
    decisions = [r for r in ri.records if r["type"] == "decision"]
    errors = [r for r in decisions if str(r.get("action", "")).startswith("error")]
    events = [r for r in ri.records if r["type"] == "event"]
    days = max(1, len({_ny(t).date() for t in ri.equity.index}))
    return dict(pnl=pnl, pnl_pct=pnl_pct, bench_pct=bench_pct, closed=closed, wins=wins, losses=losses, dd=dd,
                symbols=symbols, regimes=regimes, switches=switches, decisions=decisions, errors=errors,
                events=events, days=days, end_eq=end_eq)


def went_well(ri, a, w: Words) -> List[str]:
    out = []
    if a["bench_pct"] is not None and a["pnl_pct"] > a["bench_pct"]:
        out.append(f"The bot did better than simply holding {ri.benchmark_symbol}: {_pct(a['pnl_pct'])} against {_pct(a['bench_pct'])}.")
        w.use("Buy and hold")
    if len(a["closed"]) >= 5 and len(a["wins"]) / len(a["closed"]) >= 0.55:
        out.append(f"{len(a['wins'])} of {len(a['closed'])} finished trades made money ({len(a['wins']) / len(a['closed']):.0%}).")
        w.use("Win rate")
    if a["wins"]:
        best = max(a["wins"], key=lambda t: t["pnl"])
        out.append(f"Best trade: {best['symbol']} {best['side']} from {_ny(best['entry_time']):%a %H:%M}, {_money(best['pnl'], True)} ({_pct(best['pnl_pct'])}).")
    by_reg = _pnl_by_regime(a["closed"])
    for reg, (n, p) in by_reg.items():
        if n >= 3 and p > 0 and reg in plain.REGIMES:
            out.append(f"Trades started in {plain.regime_name(reg)} made {_money(p, True)} over {n} trades.")
            w.use("Regime")
    for e in a["events"]:
        if e.get("event") == "profit_lock":
            out.append(f"{_ny(e['time']):%a %H:%M}: {e.get('message', 'Profit lock triggered.')}")
            w.use("Profit lock")
    if not a["errors"] and ri.kind == "live" and a["decisions"]:
        out.append("No errors in the bot's decisions for this period.")
    if not out:
        out.append("Nothing stood out on the positive side by the report's fixed rules.")
    return out


def went_badly(ri, a, w: Words) -> List[str]:
    out = []
    if a["bench_pct"] is not None and a["pnl_pct"] < a["bench_pct"]:
        out.append(f"The bot trailed simply holding {ri.benchmark_symbol}: {_pct(a['pnl_pct'])} against {_pct(a['bench_pct'])}.")
        w.use("Buy and hold")
    rng = [t for t in a["losses"] if t.get("entry_regime") == "range"]
    if len(rng) >= 3 and sum(t["pnl"] for t in rng) < 0 and np.mean([abs(t["pnl_pct"]) for t in rng]) < 0.5:
        out.append(f"Many small losses in a sideways market: {len(rng)} losing trades started while the market was calm and sideways "
                   f"({_money(sum(t['pnl'] for t in rng), True)} in total). Trend-following strategies usually suffer here.")
        w.use("Regime")
    if a["losses"]:
        worst = min(a["losses"], key=lambda t: t["pnl"])
        out.append(f"Worst trade: {worst['symbol']} {worst['side']} from {_ny(worst['entry_time']):%a %H:%M}, {_money(worst['pnl'], True)} ({_pct(worst['pnl_pct'])}).")
    lim = ri.limits.get("max_daily_loss_pct")
    if lim and ri.kind == "live" and a["pnl_pct"] <= -abs(lim):
        out.append(f"The daily loss limit ({lim}%) was reached: the bot sold everything and stopped for the day.")
        w.use("Daily loss limit")
    for e in a["events"]:
        if e.get("event") in ("risk", "stall", "stop"):
            out.append(f"{_ny(e['time']):%a %H:%M}: {e.get('message', e.get('event'))}")
    if a["errors"]:
        syms = sorted({e["symbol"] for e in a["errors"]})
        out.append(f"{len(a['errors'])} bar(s) had data or calculation errors ({', '.join(syms)}); the bot skipped those bars.")
    gaps = _gaps(ri)
    if gaps:
        out.append("Data gaps: " + "; ".join(gaps) + ".")
    per_day = {}
    for s in a["switches"]:
        per_day[(s["symbol"], _ny(s["bar_time"]).date())] = per_day.get((s["symbol"], _ny(s["bar_time"]).date()), 0) + 1
    busy = {k: v for k, v in per_day.items() if v > SWITCH_LIMIT}
    if busy:
        (sym, d), n = max(busy.items(), key=lambda kv: kv[1])
        out.append(f"Strategy switching was very frequent: {sym} switched {n} times on {d:%a %d %b}. Each switch that changes the position costs money.")
        w.use("Strategy switch")
    if len(a["closed"]) < 30 and ri.kind == "backtest":
        out.append(f"Only {len(a['closed'])} finished trades: too few to tell skill from luck.")
    if not out:
        out.append("Nothing stood out on the negative side by the report's fixed rules.")
    return out


def watch_tomorrow(ri, a, w: Words) -> List[str]:
    out = []
    for sym, q in ri.open_positions.items():
        out.append(f"{sym}: {q:g} shares were still held at the end of the period and will be managed by its job from the next bar.")
        w.use("Position")
    last = {}
    for r in ri.records:
        if r["type"] == "decision" and r.get("regime"):
            last[r["symbol"]] = r
    for sym, r in sorted(last.items()):
        rv = r.get("regime_values") or {}
        line = f"{sym} ended the period {mood_adj(r['regime'], rv.get('direction'))}."
        if r["regime"] == "high_vol":
            line += " Jumpy markets make trend and dip-buying signals less reliable."
            w.use("Volatility")
        out.append(line)
        w.use("Regime")
    lim = ri.limits.get("max_daily_loss_pct")
    if lim and ri.kind == "live" and -abs(lim) < a["pnl_pct"] <= -0.7 * abs(lim):
        out.append(f"The loss ({_pct(a['pnl_pct'])}) came close to the daily limit of {lim}%.")
        w.use("Daily loss limit")
    if a["errors"]:
        out.append("Check the bot log (Live page) for the errors listed above before the next session.")
    if any("very frequent" in x for x in went_badly(ri, a, Words())):
        out.append("If switching stays this frequent, test a larger 'confirm_bars' in the Lab before changing the live job.")
    news_watch = report_news.watch_lines(ri.news) if ri.news else []
    if any("quarterly results" in x for x in news_watch):
        w.use("Earnings day")
    out += news_watch
    if not out:
        out.append("No specific warnings from the report's rules.")
    return out


def _pnl_by_regime(closed):
    out = {}
    for t in closed:
        r = t.get("entry_regime") or "unknown"
        n, p = out.get(r, (0, 0.0))
        out[r] = (n + 1, p + t["pnl"])
    return out


def _gaps(ri) -> List[str]:
    """Live only: bars the bot should have seen but didn't (bot down or no data)."""
    if ri.kind != "live":
        return []
    out = []
    for sym in sorted({r["symbol"] for r in ri.records if r["type"] == "decision"}):
        ts = sorted(pd.Timestamp(r["bar_time"]) for r in ri.records if r["type"] == "decision" and r["symbol"] == sym)
        if len(ts) < 3:
            continue
        step = pd.Series(ts).diff().median()
        for x, y in zip(ts, ts[1:]):
            if _ny(x).date() == _ny(y).date() and y - x > 3 * step:
                out.append(f"{sym} {_ny(x):%H:%M}-{_ny(y):%H:%M}")
    return out[:6]


# ----------------------------------------------------------------- sentences
def headline(ri, a, w: Words) -> str:
    who = "the bot" if ri.kind == "live" else "the strategy"
    when = ("Today" if a["days"] == 1 else f"Over these {a['days']} days") if ri.kind == "live" else "In this backtest"
    verb = "made" if a["pnl"] >= 0 else "lost"
    n = len(ri.trades)
    s = (f"{when} {who} {verb} {_money(abs(a['pnl']))} ({_pct(a['pnl_pct'])}). "
         f"{n} trade{'s' if n != 1 else ''} on {len(a['symbols'])} symbol{'s' if len(a['symbols']) != 1 else ''}.")
    if a["bench_pct"] is not None:
        diff = a["pnl_pct"] - a["bench_pct"]
        s += (f" It {'beat' if diff >= 0 else 'trailed'} simply holding {ri.benchmark_symbol} by {abs(diff):.2f} percentage points"
              f" ({ri.benchmark_symbol}: {_pct(a['bench_pct'])}).")
        w.use("Buy and hold")
    w.use("P&L", "Return", "Paper trading" if ri.kind == "live" else "Backtest")
    if ri.is_demo:
        w.use("Demo data")
    return s


def timeline(ri, w: Words) -> List[str]:
    """Regime changes and strategy switches in plain words; a switch at the same bar joins its regime change."""
    sw = {(r["symbol"], r["bar_time"]): r for r in ri.records if r["type"] == "switch"}
    used = set()
    multi_day = len({_ny(r["bar_time"]).date() for r in ri.records if "bar_time" in r}) > 1
    fmt = "%a %d %b %H:%M" if multi_day else "%H:%M"
    items = []
    for r in ri.records:
        if r["type"] == "regime":
            s = sw.get((r["symbol"], r["bar_time"]))
            text = r["reason"].rstrip(".")
            if s:
                used.add((r["symbol"], r["bar_time"]))
                text += f", so the bot switched from '{plain.nick(s['from'])}' to '{plain.nick(s['to'])}'"
                w.use("Strategy switch")
            items.append((r["bar_time"], f"{_ny(r['bar_time']):{fmt}} — {text}."))
            w.use("Regime", "Trend strength")
        elif r["type"] == "switch" and (r["symbol"], r["bar_time"]) not in used:
            items.append((r["bar_time"], f"{_ny(r['bar_time']):{fmt}} — {r['symbol']}: {r['reason']}"))
            w.use("Strategy switch")
        elif r["type"] == "event":
            items.append((r["time"], f"{_ny(r['time']):{fmt}} — {r.get('message', r.get('event'))}"))
    items.sort(key=lambda x: x[0])
    lines = [t for _, t in items]
    if len(lines) > TIMELINE_MAX:
        more = len(lines) - TIMELINE_MAX
        lines = lines[:TIMELINE_MAX // 2] + [f"… {more} more changes (see the charts) …"] + lines[-TIMELINE_MAX // 2:]
    return lines


def trade_sentence(t, w: Words, nd: Optional[Dict] = None) -> str:
    s = _trade_sentence(t, w)
    note = report_news.trade_note(nd or {}, t)
    if note:
        s += " " + note
        w.use("Materiality", "News story")
    return s


def _trade_sentence(t, w: Words) -> str:
    verb = "Bought" if t["side"] == "long" else "Sold short"
    s = f"{verb} {t['symbol']} ({t['qty_text']}) at {_money(t['entry_price'])} on {_ny(t['entry_time']):%a %d %b %H:%M}. Why: {_lower(t['entry_reason'])}"
    if t["open"]:
        s += " It is still open" + (f" ({_money(t['pnl'], True)}, {_pct(t['pnl_pct'])} so far)." if t["pnl"] is not None else ".")
    else:
        res = "a profit" if t["pnl"] > 0 else "a loss" if t["pnl"] < 0 else "no gain or loss"
        s += (f" Closed at {_money(t['exit_price'])} on {_ny(t['exit_time']):%a %H:%M} after {_dur(t['entry_time'], t['exit_time'])}: "
              f"{res} of {_money(abs(t['pnl']))} ({_pct(t['pnl_pct'])}). Why it closed: {_lower(t['exit_reason'])}")
    w.use("Fill", "Position", "P&L")
    txt = (t["entry_reason"] + " " + (t["exit_reason"] or "")).lower()
    for term, keys in {"EMA": ["average"], "Moving average": ["average"], "RSI": ["rsi"], "ADX": ["adx"],
                       "Trend strength": ["trend strength"], "Volatility percentile": ["volatility percentile"],
                       "VWAP": ["day's average price"], "MACD": ["momentum line"], "Bollinger band": ["band"],
                       "Breakout": ["-bar high", "opening range"], "Flatten before close": ["before the close"],
                       "Signal": ["wanted"]}.items():
        if any(k in txt for k in keys):
            w.use(term)
    return s


def _lower(s: str) -> str:
    s = (s or "reason not recorded").strip()
    s = s if s.startswith("'") else s[0].lower() + s[1:]
    return s if s.endswith((".", "!", "?")) else s + "."


# ----------------------------------------------------------------- charts (static SVG, white)
def _mpl():
    import logging

    import matplotlib
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)  # SVG text is drawn by the browser in Inter
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    b, e = BRAND["brand"], BRAND["extra"]
    plt.rcParams.update({
        "font.family": ["Inter", "Segoe UI", "DejaVu Sans"], "font.size": 9,
        "axes.facecolor": b["paper"], "figure.facecolor": b["paper"], "axes.edgecolor": b["line"],
        "axes.labelcolor": b["ink-soft"], "xtick.color": b["ink-soft"], "ytick.color": b["ink-soft"],
        "grid.color": b["line"], "axes.grid": True, "grid.linewidth": 0.8, "axes.spines.top": False,
        "axes.spines.right": False, "legend.frameon": False, "svg.fonttype": "none",
    })
    return plt


def _svg(fig) -> str:
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    fig.clf()
    import matplotlib.pyplot as plt
    plt.close(fig)
    s = buf.getvalue()
    return s[s.index("<svg"):]


def _positions_axis(index: pd.DatetimeIndex, ax, max_ticks=8):
    """Plot against bar number (no overnight gaps) with New York time labels."""
    n = len(index)
    if n == 0:
        return
    step = max(1, n // max_ticks)
    ticks = list(range(0, n, step))
    multi = len({_ny(t).date() for t in index}) > 1
    ax.set_xticks(ticks)
    ax.set_xticklabels([_ny(index[i]).strftime("%d %b %H:%M" if multi else "%H:%M") for i in ticks], fontsize=8)
    ax.set_xlim(-0.5, n - 0.5)


def equity_chart(ri) -> str:
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(9.2, 3.1))
    eq = ri.equity
    ax.plot(range(len(eq)), (eq / eq.iloc[0] - 1) * 100, color=BRAND["brand"]["navy"], lw=2, label="The bot" if ri.kind == "live" else "The strategy")
    if ri.benchmark is not None and len(ri.benchmark):
        bm = ri.benchmark.reindex(eq.index, method="ffill").bfill()
        ax.plot(range(len(bm)), (bm / bm.iloc[0] - 1) * 100, color=BRAND["benchmarkGrey"], lw=1.4, ls=(0, (2, 2)),
                label=f"Simply holding {ri.benchmark_symbol}")
    ax.axhline(0, color=BRAND["extra"]["line-strong"], lw=1)
    ax.set_ylabel("% change")
    _positions_axis(eq.index, ax)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2, fontsize=8.5)
    return _svg(fig)


def price_chart(ri, sym: str) -> Optional[str]:
    df = ri.bars.get(sym)
    if df is None or df.empty:
        return None
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(9.2, 2.9))
    x = np.arange(len(df))
    # regime bands
    regs = [(pd.Timestamp(r["bar_time"]), r.get("regime")) for r in ri.records
            if r["type"] == "decision" and r["symbol"] == sym and r.get("regime")]
    if ri.kind == "backtest":  # decisions are sparse in a backtest; regime records mark the changes
        regs = sorted(regs + [(pd.Timestamp(r["bar_time"]), r["to"]) for r in ri.records if r["type"] == "regime" and r["symbol"] == sym])
    if regs:
        lab = pd.Series([g for _, g in regs], index=[t for t, _ in regs]).sort_index()
        lab = lab[~lab.index.duplicated(keep="last")].reindex(df.index, method="ffill")
        colors = BRAND["states"]
        start = 0
        for i in range(1, len(df) + 1):
            if i == len(df) or lab.iloc[i] != lab.iloc[start]:
                g = lab.iloc[start]
                if isinstance(g, str) and g in colors:
                    ax.axvspan(start - 0.5, i - 0.5, color=colors[g], alpha=0.13 if g != "warmup" else 0.3, lw=0)
                start = i
    ax.plot(x, df.close.values, color=BRAND["brand"]["navy"], lw=1.3)
    marks = report_news.chart_marks(ri.news, sym, df) if ri.news else []
    if marks:  # material stories: small diamonds just above the plot (clear of the legend)
        ax.scatter(marks, [1.03] * len(marks), marker="D", s=16, color=BRAND["brand"]["gold"], edgecolors=BRAND["brand"]["navy"],
                   linewidths=0.5, transform=ax.get_xaxis_transform(), zorder=6, clip_on=False)
    for t in ri.trades:
        if t["symbol"] != sym:
            continue
        for when, price, is_buy in ((t["entry_time"], t["entry_price"], t["side"] == "long"),
                                    (t["exit_time"], t["exit_price"], t["side"] != "long")):
            if when is None:
                continue
            i = df.index.searchsorted(pd.Timestamp(when), side="right") - 1
            if 0 <= i < len(df):
                ax.scatter([i], [price], marker="^" if is_buy else "v", s=46, zorder=5,
                           color=BRAND["extra"]["profit"] if is_buy else BRAND["extra"]["loss"], edgecolors="white", linewidths=0.8)
    ax.set_ylabel("price $")
    _positions_axis(df.index, ax)
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    handles = [Line2D([], [], marker="^", ls="", color=BRAND["extra"]["profit"], label="buy"),
               Line2D([], [], marker="v", ls="", color=BRAND["extra"]["loss"], label="sell")]
    handles += [Patch(color=BRAND["states"][g], alpha=0.35, label=plain.REGIMES[g]["short"]) for g in ("trend", "range", "high_vol")]
    if marks:
        handles.append(Line2D([], [], marker="D", ls="", markersize=4, color=BRAND["brand"]["gold"],
                              markeredgecolor=BRAND["brand"]["navy"], label="news story"))
    ax.legend(handles=handles, loc="upper left", ncol=6, fontsize=8)
    return _svg(fig)


# ----------------------------------------------------------------- HTML
def news_section(ri: ReportInput, w: Words) -> str:
    """'News on our symbols': per-symbol summary, top headlines and the biggest move (live);
    counts and the most material stories (backtest). Empty when there is nothing to say."""
    nd = ri.news
    if not nd:
        return ""
    intro = ("<p class='muted'>Stories about our symbols that could matter to the price (materiality 3 or more for "
             "companies, 4 or more for index funds), scored by keyword rules unless Claude scored them; keyword scores are rough "
             "and sometimes misfile a story (for example a preview of another company's results). The bot does not trade on news"
             f"{' except through the news rules of a news-aware job' if nd.get('news_aware') else ''}; this section is context.</p>")
    if not nd.get("available"):
        return f"<h2>News on our symbols</h2><p class='muted'>{_esc(nd.get('note') or 'No news for this period.')}</p>"
    blocks = report_news.section(nd, ri.bars)
    w.use("Materiality", "News story")
    parts = []
    for blk in blocks:
        if blk["summary"] and "Earnings day" in blk["summary"]:
            w.use("Earnings day")
        head = f"<h3>{_esc(blk['symbol'])}</h3>" if blk["symbol"] else ""
        more = f"<li class='muted'>… {blk['more']} more</li>" if blk.get("more") else ""
        lines = "<ul>" + "".join(f"<li>{_esc(x)}</li>" for x in blk["lines"]) + more + "</ul>" if blk["lines"] else ""
        move = f"<p>{_esc(blk['move'])}</p>" if blk.get("move") else ""
        parts.append(f"<div class='card sunk' style='margin-top:10px'>{head}<p>{_esc(blk['summary'])}</p>{lines}{move}</div>")
    return f"<h2>News on our symbols</h2>{intro}{''.join(parts)}"


HEADER ="""<header class="spire-hdr">
  <span style="display:flex;align-items:center;gap:10px">
    <span class="mark" aria-hidden="true"></span>
    <span><span class="word">SPIRE</span><span class="sub">ANALYTICS</span></span>
  </span>
  <span class="page">{title}</span>
</header>"""


def render(ri: ReportInput) -> str:
    w = Words()
    a = analyse(ri)
    css = (ASSETS / "brand.css").read_text(encoding="utf-8")
    offset = int((datetime.now(LOCAL).utcoffset() - datetime.now(NY).utcoffset()).total_seconds() // 3600)
    head = headline(ri, a, w)
    tl = timeline(ri, w)
    trades = [trade_sentence(t, w, ri.news) for t in ri.trades]
    news_html = news_section(ri, w)
    good, bad, watch = went_well(ri, a, w), went_badly(ri, a, w), watch_tomorrow(ri, a, w)
    strategies = sorted({r.get("strategy") for r in a["decisions"] if r.get("strategy")} |
                        {r.get("active") for r in a["decisions"] if r.get("active") in plain.STRATEGIES})
    for s in strategies:
        if STRATEGIES.get(s) and STRATEGIES[s].family == "meta":
            w.use("Regime", "Strategy switch")
    w.use("Drawdown", "Bar")

    def kpi(label, value, cls=""):
        return f'<div class="card kpi"><div class="label">{_esc(label)}</div><div class="value {cls}">{value}</div></div>'

    pl_cls = "profit" if a["pnl"] > 0 else "loss" if a["pnl"] < 0 else ""
    kpis = "".join([
        kpi("Result", f"{_money(a['pnl'], True)}", pl_cls),
        kpi("Return", _pct(a["pnl_pct"]), pl_cls),
        kpi(f"Holding {ri.benchmark_symbol}", _pct(a["bench_pct"]) if a["bench_pct"] is not None else "–",
            "profit" if (a["bench_pct"] or 0) > 0 else "loss" if (a["bench_pct"] or 0) < 0 else ""),
        kpi("Trades", f"{len(ri.trades)}"),
        kpi("Deepest dip", _pct(a["dd"]), "loss" if a["dd"] < 0 else ""),
    ])
    li = lambda xs: "".join(f"<li>{_esc(x)}</li>" for x in xs)  # noqa: E731
    strat_cards = "".join(
        f'<div class="card sunk"><h3>{_esc(plain.nick(s)).capitalize()} <span class="tag">{_esc(s)}</span></h3>'
        f'<p>{_esc(plain.STRATEGIES[s]["idea"])}</p><p class="muted">Likes: {_esc(plain.STRATEGIES[s]["likes"])} '
        f'Hates: {_esc(plain.STRATEGIES[s]["hates"])}</p></div>'
        for s in strategies if s in plain.STRATEGIES)
    price_charts = ""
    for sym in [s for s in ri.bars if s in a["symbols"] or (ri.kind == "backtest")][:6]:
        svg = price_chart(ri, sym)
        if svg:
            news_cap = " Diamonds above the chart mark material news stories." if report_news.chart_marks(ri.news, sym, ri.bars[sym]) else ""
            price_charts += f"<figure>{svg}<figcaption>{_esc(sym)}: price with the bot's buys (▲) and sells (▼); shading shows the market mood.{news_cap}</figcaption></figure>"
    glossary = "".join(f"<tr><td><b>{_esc(t)}</b></td><td>{_esc(plain.GLOSSARY[t])}</td></tr>" for t in sorted(w.used))
    if len(trades) > 60:  # long backtests: first and last 30, the middle is in the charts and totals
        mid = len(trades) - 60
        trades = trades[:30] + [f"… {mid} more trades in between (all are included in the totals and charts) …"] + trades[-30:]
    trades_html = "".join(f"<li>{_esc(s)}</li>" for s in trades)
    demo = ('<p class="note"><b>Demo data.</b> These prices are made up to test the software. '
            "The numbers below say nothing about how the strategy would do in real markets.</p>") if ri.is_demo else ""
    notes = "".join(f'<p class="note">{_esc(n)}</p>' for n in ri.notes)
    generated = datetime.now(LOCAL).strftime("%d %b %Y %H:%M")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(ri.title)} · {_esc(ri.period_label)} · Spire Analytics</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Manrope:wght@600;700;800&display=swap" rel="stylesheet">
<style>{css}
body{{margin:0}} .sp-doc svg text{{font-family:Inter,sans-serif}}
.sp-doc ol.timeline{{padding-left:18px}} .sp-doc ol.timeline li{{margin:5px 0}}
</style></head>
<body class="sp-doc">
{HEADER.format(title=_esc(ri.title))}
<main>
<p class="muted" style="margin:0">{_esc(ri.period_label)} · {_esc(ri.source)}</p>
<h1>{_esc(head)}</h1>
{demo}{notes}
<div class="grid" style="margin-top:14px">{kpis}</div>

<h2>Account value through the period</h2>
<figure>{equity_chart(ri)}<figcaption>Percentage change from the start of the period. The dotted grey line is simply holding {_esc(ri.benchmark_symbol)} with the same money.</figcaption></figure>

<h2>What happened, in order</h2>
{"<ol class='timeline'>" + li(tl) + "</ol>" if tl else "<p class='muted'>No changes in market mood or strategy were recorded.</p>"}

<h2>Every trade</h2>
{"<ol>" + trades_html + "</ol>" if trades else "<p class='muted'>No trades in this period.</p>"}

<h2>Prices, trades and market mood</h2>
{price_charts or "<p class='muted'>No price data.</p>"}
{news_html}

<div class="grid" style="margin-top:24px">
  <div class="card ok"><h3>What went well</h3><ul>{li(good)}</ul></div>
  <div class="card bad"><h3>What didn't</h3><ul>{li(bad)}</ul></div>
</div>
<div class="card" style="margin-top:12px"><h3>Things to watch {"tomorrow" if ri.kind == "live" else "before using this strategy"}</h3><ul>{li(watch)}</ul></div>

{"<h2>The strategies involved</h2><div class='grid'>" + strat_cards + "</div>" if strat_cards else ""}

<h2>Glossary</h2>
<table>{glossary}</table>

<footer>Times are New York market time (Skopje is {offset:+d} h). Every statement in this report comes from logged numbers
using fixed rules; nothing is estimated or invented. Paper trading only: no real money is involved.
Generated {generated} (Skopje) by the Spire Analytics trading lab.</footer>
</main></body></html>"""


# ----------------------------------------------------------------- output
def write(ri: ReportInput, name: str, pdf: bool = True) -> Dict[str, str]:
    REPORTS.mkdir(exist_ok=True)
    html_path = REPORTS / f"{name}.html"
    html_path.write_text(render(ri), encoding="utf-8")
    out = {"html": str(html_path)}
    if pdf:
        p = to_pdf(html_path)
        if p:
            out["pdf"] = str(p)
    return out


def to_pdf(html_path: Path) -> Optional[Path]:
    """Print to PDF with headless Edge (installed with Windows)."""
    edge = next((e for e in EDGE if e.exists()), None)
    if not edge:
        return None
    pdf = html_path.with_suffix(".pdf")
    subprocess.run([str(edge), "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
                    "--virtual-time-budget=8000", f"--print-to-pdf={pdf}", html_path.resolve().as_uri()],
                   capture_output=True, timeout=120, check=False)
    return pdf if pdf.exists() and pdf.stat().st_size > 1000 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--strategy", default="regime_switch")
    ap.add_argument("--symbols", default="SPY,QQQ")
    ap.add_argument("--timeframe", default="15Min")
    ap.add_argument("--history-days", type=int, default=60)
    ap.add_argument("--days", type=int, default=5, help="report on the last N days of the backtest")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--date")
    ap.add_argument("--from", dest="d0")
    ap.add_argument("--to", dest="d1")
    ap.add_argument("--no-pdf", action="store_true")
    a = ap.parse_args()
    if a.backtest:
        from .data import fetch_bars, synthetic_bars
        syms = [s.strip().upper() for s in a.symbols.split(",")]
        data = synthetic_bars(syms, a.timeframe, a.history_days) if a.demo else fetch_bars(syms, a.timeframe, a.history_days)
        ri = from_backtest(data, a.strategy, is_demo=a.demo, days=a.days)
        name = f"backtest_{a.strategy}_{datetime.now(LOCAL):%Y%m%d_%H%M}"
    else:
        d0 = date.fromisoformat(a.date or a.d0 or datetime.now(NY).date().isoformat())
        d1 = date.fromisoformat(a.date or a.d1 or d0.isoformat())
        ri = from_live(d0, d1)
        name = d0.isoformat() if d0 == d1 else f"{d0.isoformat()}_to_{d1.isoformat()}"
    print(json.dumps(write(ri, name, pdf=not a.no_pdf), indent=1))


if __name__ == "__main__":
    main()
