"""
Cost study: is there an edge before costs, what do trades really cost, and does recalibration's
score predict anything? Diagnosis only: nothing here changes config.yaml, the bot or recalibration.

    python -m autobot.cost_study                      # every step, writes data/cost_study.json
    python -m autobot.cost_study --steps edges,costs  # some steps (results are merged into the file)

Steps (the user's numbering):
  1/3 edges      per round trip, for each auto job and each strategy x universe symbol (default
                 settings) on 5Min, 15Min, 1Hour and daily bars: gross and cost in bps, break-even
                 cost, trades per day, holding time, win rate, 95% range (clustered by day)
  4   close      how much each auto job pays for selling before the close and buying back next
                 morning; its result with those costs removed (an upper bound only)
  5   costs      the assumed 2 bps per side against the paper fills (vs bar close and vs the quote
                 at submission) and typical IEX quoted spreads at the open, midday and close;
                 suggested per-symbol costs (not applied)
  2   replay     recalibration replayed every Friday over the 2 years with only data up to that
                 day (its own rules and state), its picks measured over the following week; does a
                 higher score predict a better next week?
  6   walk       honest walk-forward per strategy (and recalibration's "best of all") x symbol x
                 timeframe against buy and hold scaled to the same time in the market, after costs

Prices: Alpaca IEX bars (free), regular hours, session closes from Alpaca's calendar. Intraday
timeframes sell 5 minutes before the close like the bot (simulated as in autobot.overnight_study:
a signal on the last bar is filled at the next open; fills at the open and in the last bar cost
2x); daily bars hold overnight (live trading on daily bars would need the close rule changed).
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from . import overnight_study as ov
from . import recalibrate as rc
from .backtest import CostModel, _trades, bars_per_year
from .data import is_crypto
from .strategies import STRATEGIES, get_signal

NY = "America/New_York"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "cost_study.json"
TIMEFRAMES = ["5Min", "15Min", "1Hour", "1Day"]
STEP_MIN = {"5Min": 5, "15Min": 15, "1Hour": 60, "1Day": 390}  # minutes in the market per bar (a session is 390)
DAILY_ONLY = {"connors_rsi2", "ibs_reversion"}          # daily-bar designs (recalibration excludes them intraday)
WF_DAILY = (120, 20)                                    # daily walk-forward windows: 20 train days are ~20 bars


# ----------------------------------------------------------------- data
def universe_symbols() -> List[str]:
    from . import universe
    u = universe.latest()
    return [s for s in (u["selected"] if u else []) if not is_crypto(s)]


def last_full_day(df: pd.DataFrame, cal: pd.DataFrame) -> date:
    """The last New York date whose session has its closing bar (today's partial session excluded)."""
    step = pd.Timedelta(minutes=5)
    end = (df.index + step).to_series().groupby(np.asarray(df.index.tz_convert(NY).date)).max()
    full = [d for d, t in end.items() if d in cal.index and t >= cal.close[d] - step]
    return max(full)


def resample(df5: pd.DataFrame, tf: str) -> pd.DataFrame:
    """15Min / 1Hour bars from regular-hours 5Min bars (bins start at :30, so 9:30 opens a bar);
    1Day: one bar per New York session, stamped 9:30."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if tf == "5Min":
        out = df5[list(agg)].copy()
    elif tf in ("15Min", "1Hour"):
        rule = "15min" if tf == "15Min" else "60min"
        out = df5[list(agg)].resample(rule, offset="30min" if tf == "1Hour" else None).agg(agg).dropna(subset=["close"])
    else:
        day = np.asarray(df5.index.tz_convert(NY).date)
        out = df5[list(agg)].groupby(day).agg(agg)
        out.index = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(NY) + pd.Timedelta(hours=9, minutes=30) for d in out.index]).tz_convert("UTC")
    out.attrs.update(df5.attrs)
    return out


class Data:
    """Every symbol's regular-hours 5Min bars (2 years, cut at a common last full day) and the
    other timeframes on demand."""

    def __init__(self, symbols: Sequence[str], fetch: Optional[Callable] = None, closes=None, as_of: Optional[date] = None):
        raw = {s: (fetch or ov.load_bars)(s, "5Min", ov.LONG_DAYS) for s in symbols}
        if closes is None and fetch is None:
            first = min(np.asarray(d.index.tz_convert(NY).date).min() for d in raw.values())
            closes = ov.trading_calendar(first, date.today() + timedelta(days=10))
        self.closes = closes
        self.cal: Dict[str, pd.DataFrame] = {}
        self.df5: Dict[str, pd.DataFrame] = {}
        ends = []
        for s, df in raw.items():
            cal = ov.calendar(df, closes)
            df = ov.trim_to_calendar(df, cal)
            df.attrs["symbol"] = s
            self.cal[s], self.df5[s] = cal, df
            ends.append(last_full_day(df, cal))
        self.as_of = as_of or min(ends)
        for s, df in self.df5.items():
            cut = df[np.asarray(df.index.tz_convert(NY).date) <= self.as_of].copy()
            cut.attrs["symbol"] = s
            self.df5[s] = cut
        self._tf: Dict = {}

    def bars(self, sym: str, tf: str) -> pd.DataFrame:
        if (sym, tf) not in self._tf:
            self._tf[(sym, tf)] = resample(self.df5[sym], tf)
        return self._tf[(sym, tf)]

    def start(self, sym: str, warmup_days: int) -> date:
        return self.df5[sym].index[0].tz_convert(NY).date() + timedelta(days=warmup_days)


def strategies_for(tf: str, r: Dict) -> List[str]:
    """Registered single strategies (no meta, no news, no benchmark): recalibration's list on
    intraday bars; on daily bars the daily designs instead of the intraday family."""
    out = []
    for name, spec in STRATEGIES.items():
        if spec.family in ("meta", "news", "benchmark"):
            continue
        if tf == "1Day":
            if spec.family == "intraday":
                continue
        elif name in DAILY_ONLY or name in r["exclude"]:
            continue
        out.append(name)
    return out


# ----------------------------------------------------------------- 1/3: edge per round trip
def policy_for(tf: str, flatten_min: int) -> ov.Policy:
    return ov.Policy("hold", "hold", flatten_min) if tf == "1Day" else ov.Policy(f"flatten_{flatten_min}", "flatten", flatten_min)


def sim(df: pd.DataFrame, cal: pd.DataFrame, strategy: str, params: Dict, costs: CostModel, tf: str,
        flatten_min: int, edge_mult: float = 2.0, signal: Optional[pd.Series] = None) -> Dict:
    """One strategy replayed like the bot (see the module notes); returns the overnight engine's
    frame and nights."""
    sig = signal if signal is not None else get_signal(strategy, df, params or {}, False)
    inp = ov.Inputs(df=df, signal=sig, cal=cal, costs=costs, edge_mult=edge_mult)
    out = ov.simulate(inp, policy_for(tf, flatten_min))
    out["inp"] = inp
    return out


def round_trips(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per round trip (a run of bars with the same non-zero position): gross return
    (before costs, incl. any night inside it), the costs of its entry and exit fills, bars held,
    entry day. Returns in fractions."""
    pos = frame.position.to_numpy()
    ret = frame.ret.to_numpy()
    cost = frame.cost.to_numpy()
    n = len(pos)
    rows = []
    i = 0
    day = np.asarray(frame.index.tz_convert(NY).date)
    while i < n:
        if pos[i] == 0:
            i += 1
            continue
        j = i
        while j + 1 < n and pos[j + 1] == pos[i]:
            j += 1
        gross = float((ret[i:j + 1] + cost[i:j + 1]).sum())
        exit_cost = float(cost[j + 1]) if j + 1 < n and pos[j + 1] == 0 else 0.0
        rows.append({"start": i, "end": j, "bars": j - i + 1, "day": day[i], "gross": gross,
                     "cost": float(cost[i]) + exit_cost})
        i = j + 1
    return pd.DataFrame(rows, columns=["start", "end", "bars", "day", "gross", "cost"])


def clustered_mean(x: np.ndarray, cluster: np.ndarray) -> Dict:
    """Mean of x with a 95% range from a cluster-robust standard error (trades on the same day
    move together, so a busy day can't fake certainty)."""
    n = len(x)
    if n < 2:
        return {"mean": float(x.mean()) if n else 0.0, "lo": None, "hi": None}
    mu = x.mean()
    g = pd.Series(x - mu).groupby(cluster).sum().to_numpy()
    G = len(g)
    se = math.sqrt((g ** 2).sum() * G / max(1, G - 1)) / n if G > 1 else float("nan")
    return {"mean": float(mu), "lo": float(mu - 1.96 * se), "hi": float(mu + 1.96 * se)}


def edge_stats(tr: pd.DataFrame, sessions: int, step_min: float, side_bps: float) -> Dict:
    """Per round trip, in bps: average gross with its 95% range, average cost, average net; the
    break-even normal cost per side (where net would be zero); trades per day, holding time,
    win rate after costs."""
    if tr.empty:
        return {"trades": 0, "per_day": 0.0, "gross_bps": None, "gross_lo": None, "gross_hi": None, "cost_bps": None,
                "net_bps": None, "break_even_side_bps": None, "hold_min": None, "win_rate_%": None, "clearly_positive": False}
    g = clustered_mean(tr.gross.to_numpy() * 1e4, tr.day.to_numpy())
    cost = float(tr.cost.mean() * 1e4)
    net = tr.gross - tr.cost
    return {"trades": int(len(tr)), "per_day": round(len(tr) / max(1, sessions), 3),
            "gross_bps": round(g["mean"], 2), "gross_lo": None if g["lo"] is None else round(g["lo"], 2),
            "gross_hi": None if g["hi"] is None else round(g["hi"], 2),
            "cost_bps": round(cost, 2), "net_bps": round(float(net.mean() * 1e4), 2),
            "break_even_side_bps": round(side_bps * g["mean"] / cost, 2) if cost > 0 else None,
            "hold_min": round(float(tr.bars.mean() * step_min), 1),
            "win_rate_%": round(float((net > 0).mean() * 100), 1),
            "clearly_positive": bool(g["lo"] is not None and g["lo"] > 0)}


def _period(frame: pd.DataFrame, d0: date) -> pd.DataFrame:
    return frame[np.asarray(frame.index.tz_convert(NY).date) >= d0]


def edges(data: Data, cfg: Dict, timeframes: Sequence[str] = TIMEFRAMES, progress=None) -> Dict:
    """Steps 1 and 3: every auto job (its own settings, 5Min) and every strategy x symbol x
    timeframe (default settings)."""
    r = rc.settings(cfg)
    say = progress or (lambda f, m: None)
    fm = int(r["flatten_min"]) or 5
    rows, jobs = [], []
    auto = [j for j in cfg["live"].get("jobs") or [] if j.get("enabled", True) and not is_crypto(j["symbols"][0])]
    for j in auto:
        sym = j["symbols"][0]
        if sym not in data.df5:
            continue
        df = data.bars(sym, "5Min")
        out = sim(df, data.cal[sym], j["strategy"], j.get("params") or {}, rc.costs_for(sym, r), "5Min", fm)
        d0 = data.start(sym, int(r["warmup_days"]))
        f = _period(out["frame"], d0)
        sessions = len(set(f.index.tz_convert(NY).date))
        tr = round_trips(f)
        jobs.append({"job": j["name"], "symbol": sym, "strategy": j["strategy"], "params": j.get("params") or {},
                     "timeframe": "5Min", **edge_stats(tr, sessions, 5, rc.costs_for(sym, r).per_turn * 1e4)})
    syms = list(data.df5)
    total = len(syms) * len(timeframes)
    k = 0
    for tf in timeframes:
        for sym in syms:
            say(k / total, f"edges: {sym} {tf}")
            k += 1
            df = data.bars(sym, tf)
            costs = rc.costs_for(sym, r)
            d0 = data.start(sym, int(r["warmup_days"]) if tf != "1Day" else 230)  # 200-day trend filters need history
            for name in strategies_for(tf, r):
                try:
                    out = sim(df, data.cal[sym], name, STRATEGIES[name].defaults, costs, tf, fm)
                except Exception as e:  # noqa: BLE001  a strategy that can't run on these bars is reported, not fatal
                    rows.append({"symbol": sym, "timeframe": tf, "strategy": name, "family": STRATEGIES[name].family, "error": str(e)})
                    continue
                f = _period(out["frame"], d0)
                sessions = len(set(f.index.tz_convert(NY).date))
                rows.append({"symbol": sym, "timeframe": tf, "strategy": name, "family": STRATEGIES[name].family,
                             **edge_stats(round_trips(f), sessions, STEP_MIN[tf], costs.per_turn * 1e4),
                             "net_return_%": round(float(((1 + f.ret).prod() - 1) * 100), 2),
                             "gross_return_%": round(float(((1 + f.ret + f.cost).prod() - 1) * 100), 2),
                             "exposure_%": round(float((f.position != 0).mean() * 100), 1)})
    for row in rows:  # buy and hold over the same bars, for context
        if "error" in row:
            continue
        df = data.bars(row["symbol"], row["timeframe"])
        d0 = data.start(row["symbol"], int(r["warmup_days"]) if row["timeframe"] != "1Day" else 230)
        c = _period(df, d0).close
        row["buy_hold_%"] = round(float((c.iloc[-1] / c.iloc[0] - 1) * 100), 2)
    by_tf = {}
    for tf in timeframes:
        rs = [x for x in rows if x["timeframe"] == tf and "error" not in x and x["trades"]]
        by_tf[tf] = {"combos": len(rs), "clearly_positive": sum(x["clearly_positive"] for x in rs),
                     "positive_after_costs": sum((x["net_bps"] or 0) > 0 for x in rs),
                     "median_gross_bps": round(float(np.median([x["gross_bps"] for x in rs])), 2) if rs else None,
                     "median_cost_bps": round(float(np.median([x["cost_bps"] for x in rs])), 2) if rs else None,
                     "median_trades_per_day": round(float(np.median([x["per_day"] for x in rs])), 3) if rs else None,
                     "total_net_%_median": round(float(np.median([x["net_return_%"] for x in rs])), 2) if rs else None,
                     "total_gross_%_median": round(float(np.median([x["gross_return_%"] for x in rs])), 2) if rs else None}
    say(1.0, "edges done")
    return {"jobs": jobs, "rows": rows, "by_timeframe": by_tf,
            "expected_false_positives": round(0.025 * sum(v["combos"] for v in by_tf.values()), 1)}


# ----------------------------------------------------------------- 4: the close rule
def close_rule(data: Data, cfg: Dict) -> List[Dict]:
    """Per auto job (5Min): the costs of round trips that ended only because of the sell-before-
    close rule (exit at the flatten time while the strategy still wanted the position) and of
    buying back at the next open / first bar; the job's result with those costs removed (an upper
    bound: it ignores the overnight risk), next to actually holding when the strategy is still in."""
    r = rc.settings(cfg)
    fm = int(r["flatten_min"]) or 5
    out = []
    for j in [j for j in cfg["live"].get("jobs") or [] if j.get("enabled", True) and not is_crypto(j["symbols"][0])]:
        sym = j["symbols"][0]
        if sym not in data.df5:
            continue
        df = data.bars(sym, "5Min")
        costs = rc.costs_for(sym, r)
        sig = get_signal(j["strategy"], df, j.get("params") or {}, False)
        res = sim(df, data.cal[sym], j["strategy"], {}, costs, "5Min", fm, signal=sig)
        inp = res["inp"]
        held = ov.simulate(inp, ov.Policy("if_signal_on", "conditional", fm, ("signal_on",)))
        d0 = data.start(sym, int(r["warmup_days"]))
        f = res["frame"]
        idx = {t: i for i, t in enumerate(f.index)}
        starts = {inp.day[a]: a for a, _ in inp.sessions}
        n = res["nights"]
        forced_cost, forced = 0.0, 0
        if len(n):
            n = n[(n.date >= d0) & (n.pos_at_decision != 0) & n.signal_on.astype(bool) & n.next_date.notna()]
            pos, cost = f.position.to_numpy(), f.cost.to_numpy()
            for _, row in n.iterrows():
                i = idx[row.decided_at] + 1                        # the exit fill
                a = starts.get(row.next_date)
                back = 0.0
                if a is not None:                                  # bought back at the open or at the first bar's close
                    if pos[a] != 0:
                        back = cost[a]
                    elif a + 1 < len(pos) and pos[a + 1] != 0:
                        back = cost[a + 1]
                forced_cost += cost[i] + back
                forced += 1
        fp = _period(f, d0)
        hp = _period(held["frame"], d0)
        total_cost = float(fp.cost.sum())
        net = float(fp.ret.sum())
        tr = len(_trades(fp))
        out.append({"job": j["name"], "symbol": sym, "strategy": j["strategy"], "round_trips": tr, "forced_round_trips": forced,
                    "forced_%_of_round_trips": round(forced / max(1, tr) * 100, 1),
                    "cost_%": round(total_cost * 100, 2), "forced_cost_%": round(forced_cost * 100, 2),
                    "forced_%_of_cost": round(forced_cost / total_cost * 100, 1) if total_cost else 0.0,
                    "net_%": round(net * 100, 2), "net_without_forced_costs_%": round((net + forced_cost) * 100, 2),
                    "hold_if_strategy_in_net_%": round(float(hp.ret.sum()) * 100, 2),
                    "note": "sums of daily returns (simple, not compounded)"})
    return out


# ----------------------------------------------------------------- 5: cost realism
def _clients():
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.trading.client import TradingClient
    from .data import _keys, with_timeout
    k, s = _keys()
    return with_timeout(TradingClient(k, s, paper=True)), with_timeout(StockHistoricalDataClient(k, s))


def _quotes(dc, sym: str, start: datetime, end: datetime) -> pd.DataFrame:
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockQuotesRequest
    q = dc.get_stock_quotes(StockQuotesRequest(symbol_or_symbols=sym, start=start, end=end, feed=DataFeed.IEX)).df
    if q is None or q.empty:
        return pd.DataFrame(columns=["bid_price", "ask_price"])
    q = q.reset_index(level=0, drop=True) if isinstance(q.index, pd.MultiIndex) else q
    q = q[(q.bid_price > 0) & (q.ask_price > q.bid_price)]
    return q


def fills(data: Optional[Data] = None, since: date = date(2026, 9, 29)) -> Dict:
    """Paper fills (read-only Alpaca calls) against the bar close the bot used and the IEX quote
    at submission: what each fill cost compared with the middle of the quote, in bps."""
    tc, dc = _clients()
    from alpaca.trading.requests import GetOrdersRequest
    orders = tc.get_orders(GetOrdersRequest(status="closed", after=datetime.combine(since, datetime.min.time(), timezone.utc), limit=500))
    approx = {}
    p = ROOT / "logs" / "trades.csv"
    if p.exists():
        t = pd.read_csv(p)
        approx = dict(zip(t.order_id.astype(str), t.approx_price))
    rows = []
    for o in orders:
        if o.filled_avg_price is None or o.filled_at is None or is_crypto(o.symbol) or "/" in o.symbol:
            continue
        fill = float(o.filled_avg_price)
        side = 1 if o.side.value == "buy" else -1
        sub = o.submitted_at
        q = _quotes(dc, o.symbol, sub - timedelta(seconds=30), sub + timedelta(seconds=1))
        q = q[q.index <= sub]
        mid = spread = None
        if len(q):
            b, a = float(q.bid_price.iloc[-1]), float(q.ask_price.iloc[-1])
            mid, spread = (a + b) / 2, (a - b) / ((a + b) / 2) * 1e4
        ref = approx.get(str(o.id))
        ny = pd.Timestamp(sub).tz_convert(NY)
        mins_open = (ny.hour * 60 + ny.minute) - (9 * 60 + 30)
        rows.append({"time": str(ny), "symbol": o.symbol, "side": o.side.value, "qty": float(o.filled_qty or 0), "fill": fill,
                     "quote_mid": mid, "quote_spread_bps": None if spread is None else round(spread, 2),
                     "vs_mid_bps": None if mid is None else round(side * (fill / mid - 1) * 1e4, 2),
                     "bar_close": ref, "vs_bar_close_bps": None if not ref else round(side * (fill / float(ref) - 1) * 1e4, 2),
                     "fill_delay_s": round((o.filled_at - sub).total_seconds(), 2),
                     "at": "open" if mins_open < 10 else "close" if mins_open >= 380 else "day"})
    df = pd.DataFrame(rows)
    summ = {}
    if len(df):
        for col in ("vs_mid_bps", "vs_bar_close_bps", "quote_spread_bps"):
            v = df[col].dropna()
            summ[col] = {"n": int(len(v)), "mean": round(float(v.mean()), 2) if len(v) else None,
                         "median": round(float(v.median()), 2) if len(v) else None}
    return {"orders": rows, "summary": summ}


def spreads(symbols: Sequence[str], sessions: Sequence[date], windows=(("open", "09:30", "09:40"), ("midday", "12:00", "12:10"),
                                                                       ("close", "15:50", "16:00"))) -> Dict:
    """Median half-spread (bps) of IEX quotes per symbol in each time window over the given
    sessions. IEX shows only its own book, usually wider than the best price across exchanges,
    so these are upper estimates."""
    _, dc = _clients()
    out = {}
    for sym in symbols:
        res = {}
        for name, a, b in windows:
            vals = []
            for d in sessions:
                t0 = pd.Timestamp(f"{d} {a}", tz=NY).tz_convert("UTC").to_pydatetime()
                t1 = pd.Timestamp(f"{d} {b}", tz=NY).tz_convert("UTC").to_pydatetime()
                q = _quotes(dc, sym, t0, t1)
                if len(q):
                    mid = (q.ask_price + q.bid_price) / 2
                    vals.append(((q.ask_price - q.bid_price) / mid * 1e4 / 2).to_numpy())
            v = np.concatenate(vals) if vals else np.zeros(0)
            res[name] = {"quotes": int(len(v)), "half_spread_bps": round(float(np.median(v)), 2) if len(v) else None,
                         "p90": round(float(np.percentile(v, 90)), 2) if len(v) else None}
        out[sym] = res
    return out


def suggest_costs(spr: Dict, assumed_side_bps: float = 2.0, edge_mult: float = 2.0) -> Dict:
    """Per symbol: a normal cost per side (the midday half-spread, rounded up to 0.5 bps, at
    least 0.5) and the open / close multipliers measured against midday. Not applied."""
    out = {}
    for sym, w in spr.items():
        mid = (w.get("midday") or {}).get("half_spread_bps")
        if not mid:
            continue
        base = max(0.5, math.ceil(mid * 2) / 2)
        o = (w.get("open") or {}).get("half_spread_bps")
        c = (w.get("close") or {}).get("half_spread_bps")
        out[sym] = {"side_bps": base, "open_mult": round(o / mid, 2) if o else None, "close_mult": round(c / mid, 2) if c else None,
                    "assumed_side_bps": assumed_side_bps, "assumed_edge_mult": edge_mult}
    return out


def cost_realism(symbols: Sequence[str], sessions: Sequence[date]) -> Dict:
    spr = spreads(symbols, sessions)
    return {"fills": fills(), "spreads": spr, "sessions": [str(d) for d in sessions], "suggested": suggest_costs(spr),
            "caveat": "IEX quotes show only IEX's own book (usually wider than the best price across exchanges); "
                      "few paper fills prove little; paper fills are simulated by Alpaca."}


# ----------------------------------------------------------------- 2: recalibration replay
def _next_week(st: rc.SymbolStudy, spec: Optional[Dict], a: int, b: int) -> Dict:
    if spec is None or b <= a:
        return {"return_%": 0.0, "exposure": 0.0, "trades": 0}
    bt = st.bt(spec).iloc[a:b]
    return {"return_%": float(((1 + bt.ret).prod() - 1) * 100), "exposure": float(bt.position.abs().mean()),
            "trades": int(len(_trades(bt)))}


def _spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    s = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(s) < 3 or s.x.nunique() < 2 or s.y.nunique() < 2:
        return None
    return float(s.x.rank().corr(s.y.rank()))


def replay_symbol(sym: str, df: pd.DataFrame, r: Dict, fridays: List[date], progress=None) -> List[Dict]:
    """Recalibration as it would have run each Friday with data up to that day: its windows,
    challenger, champion/challenger rule and state (since / changes), starting with no job.
    Every strategy's out-of-sample score and the pick are recorded with their next-week result."""
    st = rc.SymbolStudy(sym, df, r, fridays[-1])
    day = st.day
    names = rc.eligible(sym, r)
    inc: Optional[Dict] = None
    state: Dict = {}
    rows = []
    for k, fri in enumerate(fridays):
        cut = int(np.searchsorted(day, fri, side="right"))
        end = int(np.searchsorted(day, fri + timedelta(days=7), side="right"))
        start = fri - timedelta(days=int(r["lookback_days"]) - 1)
        st.win = rc.windows(day[:cut], start, int(r["train_days"]), int(r["test_days"]))
        if not st.win or end <= cut:
            continue
        res = st.study(names, inc)
        ch = res["meta"] if res["meta"]["spec"] and res["meta"]["score"] > res["single"]["score"] else res["single"]
        dec = rc.decide(sym, res["incumbent"], ch, r, state, fri)
        if dec["change"]:
            inc = ch["spec"]
            state = {"since": fri.isoformat(),
                     "changes": [d for d in state.get("changes", []) if (fri - date.fromisoformat(d)).days < 30] + [fri.isoformat()]}
        picked_score = ch["score"] if dec["change"] else res["incumbent"]["score"] if inc else 0.0
        nxt = _next_week(st, inc, cut, end)
        ch_next = _next_week(st, ch["spec"], cut, end)
        c0 = float(df.close.iloc[cut - 1])
        bh = float(df.close.iloc[end - 1] / c0 - 1) * 100
        strat = []
        for n in names:
            s = res["strategies"][n]
            p = s.get("last_params")
            if p is None:
                continue
            strat.append({"strategy": n, "score": float(s["score"]), "next_%": _next_week(st, {"strategy": n, "params": p}, cut, end)["return_%"]})
        rows.append({"symbol": sym, "friday": fri.isoformat(), "decision": dec["outcome"], "changed": bool(dec["change"]),
                     "job": rc.describe(inc), "job_score": float(picked_score), "challenger": rc.describe(ch["spec"]),
                     "challenger_score": float(ch["score"]), "next_%": nxt["return_%"], "next_exposure": nxt["exposure"],
                     "next_trades": nxt["trades"], "challenger_next_%": ch_next["return_%"], "buy_hold_next_%": bh,
                     "matched_bh_next_%": bh * nxt["exposure"], "strategies": strat})
        if progress:
            progress((k + 1) / len(fridays), f"{sym}: week {k + 1}/{len(fridays)}")
    return rows


def _boot_ci(values_by_week: Dict[str, List[float]], stat: Callable[[List[float]], float], n: int = 2000, seed: int = 7) -> List[Optional[float]]:
    weeks = list(values_by_week)
    if len(weeks) < 5:
        return [None, None]
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        pick = rng.choice(len(weeks), len(weeks), replace=True)
        vals = [v for i in pick for v in values_by_week[weeks[i]]]
        s = stat(vals)
        if s is not None and np.isfinite(s):
            out.append(s)
    return [round(float(np.percentile(out, 2.5)), 3), round(float(np.percentile(out, 97.5)), 3)] if out else [None, None]


def replay(data: Data, cfg: Dict, progress=None) -> Dict:
    """Step 2 for every universe symbol; the following week of each pick against no trading, buy
    and hold and buy and hold scaled to the pick's time in the market; does the score predict?"""
    r = rc.settings(cfg)
    say = progress or (lambda f, m: None)
    first = max(data.start(s, int(r["warmup_days"]) + int(r["lookback_days"])) for s in data.df5)
    fridays = [d.date() for d in pd.date_range(first, data.as_of - timedelta(days=7), freq="W-FRI")]
    rows: List[Dict] = []
    syms = list(data.df5)
    for i, sym in enumerate(syms):
        rows += replay_symbol(sym, data.df5[sym], r, fridays,
                              progress=lambda f, m, i=i: say((i + f) / len(syms), m))
    df = pd.DataFrame([{k: v for k, v in x.items() if k != "strategies"} for x in rows])
    out: Dict = {"fridays": len(fridays), "from": str(fridays[0]) if fridays else None, "to": str(fridays[-1]) if fridays else None,
                 "symbols": syms, "rows": rows}
    if df.empty:
        return out
    traded = df[df.next_exposure > 0]
    out["picks"] = {
        "symbol_weeks": int(len(df)), "weeks_with_a_job": int((df.job != rc.describe(None)).sum()),
        "changes": int(df.changed.sum()),
        "mean_next_%": round(float(df["next_%"].mean()), 3),
        "mean_buy_hold_next_%": round(float(df["buy_hold_next_%"].mean()), 3),
        "mean_matched_bh_next_%": round(float(df["matched_bh_next_%"].mean()), 3),
        "sum_next_%": round(float(df["next_%"].sum()), 2), "sum_matched_bh_next_%": round(float(df["matched_bh_next_%"].sum()), 2),
        "when_trading": {"symbol_weeks": int(len(traded)), "mean_next_%": round(float(traded["next_%"].mean()), 3) if len(traded) else None,
                         "mean_matched_bh_next_%": round(float(traded["matched_bh_next_%"].mean()), 3) if len(traded) else None,
                         "beat_matched_bh_%": round(float((traded["next_%"] > traded["matched_bh_next_%"]).mean() * 100), 1) if len(traded) else None,
                         "beat_zero_%": round(float((traded["next_%"] > 0).mean() * 100), 1) if len(traded) else None},
    }
    by_week = {}
    for x in rows:
        by_week.setdefault(x["friday"], []).append(x)
    diff = {w: [v["next_%"] - v["matched_bh_next_%"] for v in xs if v["next_exposure"] > 0] for w, xs in by_week.items()}
    out["picks"]["vs_matched_bh_mean_%"] = round(float(np.mean([v for xs in diff.values() for v in xs])), 3) if any(diff.values()) else None
    out["picks"]["vs_matched_bh_ci95"] = _boot_ci({w: v for w, v in diff.items() if v}, lambda v: float(np.mean(v)) if v else None)
    # does the score predict the next week? (a) the challenger's score vs its next week, pooled
    pairs = {w: [(v["challenger_score"], v["challenger_next_%"]) for v in xs] for w, xs in by_week.items()}
    pooled = lambda v: _spearman([a for a, _ in v], [b for _, b in v])  # noqa: E731
    out["score_predicts"] = {
        "challenger_pooled": {"rank_corr": None if pooled([p for xs in pairs.values() for p in xs]) is None
                              else round(pooled([p for xs in pairs.values() for p in xs]), 3),
                              "ci95": _boot_ci(pairs, pooled), "n": int(sum(len(x) for x in pairs.values()))}}
    # (b) within each symbol-week: the strategies' scores vs their next weeks (average rank correlation)
    within = {}
    for x in rows:
        c = _spearman([s["score"] for s in x["strategies"]], [s["next_%"] for s in x["strategies"]])
        if c is not None:
            within.setdefault(x["friday"], []).append(c)
    allc = [c for cs in within.values() for c in cs]
    out["score_predicts"]["within_symbol_week"] = {"mean_rank_corr": round(float(np.mean(allc)), 3) if allc else None,
                                                   "ci95": _boot_ci(within, lambda v: float(np.mean(v)) if v else None),
                                                   "n": len(allc)}
    # (c) score buckets: next-week result of every strategy by its score fifth
    sc = pd.DataFrame([{"score": s["score"], "next_%": s["next_%"]} for x in rows for s in x["strategies"]])
    if len(sc) >= 50:
        sc["fifth"] = pd.qcut(sc.score.rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
        out["score_predicts"]["by_score_fifth"] = [{"fifth": int(k), "score_from": round(float(g.score.min()), 2),
                                                    "score_to": round(float(g.score.max()), 2), "n": int(len(g)),
                                                    "mean_next_%": round(float(g["next_%"].mean()), 3),
                                                    "share_positive_%": round(float((g["next_%"] > 0).mean() * 100), 1)}
                                                   for k, g in sc.groupby("fifth", observed=True)]
    say(1.0, "replay done")
    return out


# ----------------------------------------------------------------- 6: what would have worked
def _daily(r: pd.Series) -> pd.Series:
    return (1 + r).groupby(np.asarray(r.index.tz_convert(NY).date)).prod() - 1


def walk(data: Data, cfg: Dict, timeframes: Sequence[str] = TIMEFRAMES, progress=None) -> Dict:
    """Per symbol x timeframe: each strategy's walk-forward over its grid (recalibration's
    windows; daily bars 120/20) and recalibration's 'best of all strategies' pick, stitched on
    the test days with recalibration's costs and close rule, against buy and hold scaled to the
    same average time in the market. Daily difference with a 95% range."""
    from .exit_study import paired
    r0 = rc.settings(cfg)
    say = progress or (lambda f, m: None)
    rows = []
    syms = list(data.df5)
    total = len(syms) * len(timeframes)
    k = 0
    for tf in timeframes:
        r = {**r0}
        if tf == "1Day":
            r["train_days"], r["test_days"] = WF_DAILY
        names = strategies_for(tf, r)
        for sym in syms:
            say(k / total, f"walk-forward: {sym} {tf}")
            k += 1
            df = data.bars(sym, tf)
            d0 = data.start(sym, int(r["warmup_days"]) if tf != "1Day" else 230)
            st = rc.SymbolStudy(sym, df, {**r, "lookback_days": (data.as_of - d0).days + 1}, data.as_of)
            if tf == "1Day":
                st.flat_min = 0  # daily bars hold overnight (see the module notes)
            if not st.win:
                continue
            a0, b0 = st.win[0][2], st.win[-1][3]
            bh = df.close.pct_change().fillna(0).iloc[a0:b0]
            per_strategy: Dict[str, List[Dict]] = {n: [] for n in names}
            single = []
            for ta, tb, _, _ in st.win:
                best = None
                for n in names:
                    specs = [{"strategy": n, "params": p} for p in rc.param_combos(n)]
                    sc, sp = max(((st.wscore(s, ta, tb)["score"], s) for s in specs), key=lambda x: x[0])
                    per_strategy[n].append(sp)
                    if best is None or sc > best[0]:
                        best = (sc, sp)
                single.append(best[1])
            for n, picks in list(per_strategy.items()) + [("recalibration_single", single)]:
                f = st.oos_frame(picks)
                expo = float(f.position.abs().mean())
                d = _daily(f.ret)
                m = _daily(bh * expo).reindex(d.index).fillna(0)
                v = paired(f.ret, bh * expo)
                rows.append({"symbol": sym, "timeframe": tf, "strategy": n, "days": len(d),
                             "return_%": round(float(((1 + f.ret).prod() - 1) * 100), 2),
                             "matched_bh_%": round(float(((1 + m).prod() - 1) * 100), 2),
                             "buy_hold_%": round(float(((1 + bh).prod() - 1) * 100), 2),
                             "exposure_%": round(expo * 100, 1), "round_trips": int(len(_trades(f))),
                             "vs_matched_bps": v["mean_bps"], "ci95_bps": v["ci95_bps"], "significant": v["significant"],
                             "beats_matched": bool(v["significant"] and v["mean_bps"] > 0)})
    tests = [x for x in rows if x["strategy"] != "recalibration_single"]
    out = {"rows": rows, "tests": len(tests), "beat_matched": [x for x in rows if x["beats_matched"]],
           "expected_false_positives": round(0.025 * len(tests), 1),
           "windows": {"intraday": [r0["train_days"], r0["test_days"]], "daily": list(WF_DAILY)}}
    say(1.0, "walk-forward done")
    return out


# ----------------------------------------------------------------- 7: summary
def _f(v, d=2, sign=False) -> str:
    return "–" if v is None else (f"{v:+.{d}f}" if sign else f"{v:.{d}f}")


def summary(res: Dict, cfg: Optional[Dict] = None) -> Dict:
    """Step 7: plain-English findings and the options with their evidence (plain.COST_STUDY /
    COST_OPTIONS), filled with the study's numbers only."""
    from . import plain
    T = plain.COST_STUDY
    lines: List[str] = []
    v: Dict[str, str] = {}
    rp = res.get("replay") or {}
    if rp.get("rows"):
        sp = rp["score_predicts"]
        w = sp["within_symbol_week"]
        fifths = sp.get("by_score_fifth") or []
        if w["ci95"][0] is not None and w["ci95"][0] > 0.1:
            lines.append(T["score_yes"].format(rho=_f(w["mean_rank_corr"], 3), lo=_f(w["ci95"][0], 3), hi=_f(w["ci95"][1], 3)))
        else:
            lines.append(T["score_no"].format(weeks=rp["fridays"], syms=len(rp["symbols"]), rho=_f(w["mean_rank_corr"], 3),
                                              lo=_f(w["ci95"][0], 3), hi=_f(w["ci95"][1], 3),
                                              top=_f(fifths[-1]["mean_next_%"], 2, True) if fifths else "–",
                                              bottom=_f(fifths[0]["mean_next_%"], 2, True) if fifths else "–"))
        pk = rp["picks"]
        lines.append(T["picks"].format(mean=_f(pk["mean_next_%"], 2, True), sum=_f(pk["sum_next_%"], 0, True), n=pk["symbol_weeks"],
                                       bh=_f(pk["mean_matched_bh_next_%"], 2, True), beat=_f(pk["when_trading"]["beat_matched_bh_%"], 0),
                                       d=_f(pk["vs_matched_bh_mean_%"], 2, True), dlo=_f(pk["vs_matched_bh_ci95"][0], 2, True),
                                       dhi=_f(pk["vs_matched_bh_ci95"][1], 2, True)))
        df = pd.DataFrame([{k: x[k] for k in ("symbol", "friday", "decision", "job_score")} for x in rp["rows"]])
        kept = df[df.decision.isin(["keep_better", "keep_edge", "keep_trades", "keep_same"])]
        first = df[df.decision == "new_job"].groupby("symbol").friday.min()
        start = pd.Timestamp(rp["from"])
        v.update(neg=str(int((kept.job_score < 0).sum())),
                 weeks_first=str(int(max(((pd.to_datetime(first) - start).dt.days // 7 + 1).max(), 1))) if len(first) else "–",
                 mean=_f(pk["mean_next_%"], 2, True), bh=_f(pk["mean_matched_bh_next_%"], 2, True), rho=_f(w["mean_rank_corr"], 3))
        lines.append(T["no_job_gap"].format(neg=v["neg"], weeks_first=v["weeks_first"]))
    e = res.get("edges") or {}
    if e.get("by_timeframe"):
        bt = e["by_timeframe"]
        b5, b15, b60, bd = (bt.get(k, {}) for k in ("5Min", "15Min", "1Hour", "1Day"))
        ntot = sum(x.get("combos", 0) for x in bt.values())
        v.update(g5=_f(b5.get("median_gross_bps"), 1), c5=_f(b5.get("median_cost_bps"), 1), cp5=str(b5.get("clearly_positive", 0)),
                 n5=str(b5.get("combos", 0)), n5r=_f(b5.get("total_net_%_median"), 1, True), ndr=_f(bd.get("total_net_%_median"), 1, True))
        lines.append(T["edge"].format(g5=v["g5"], c5=v["c5"], cp5=v["cp5"], n5=v["n5"], fp=_f(e["expected_false_positives"], 0), ntot=ntot))
        jobs = e.get("jobs") or []
        if jobs:
            g = [j["gross_bps"] for j in jobs if j["gross_bps"] is not None]
            c = [j["cost_bps"] for j in jobs if j["cost_bps"] is not None]
            line = T["jobs"].format(gmin=_f(min(g), 1, True), gmax=_f(max(g), 1, True), cmin=_f(min(c), 1), cmax=_f(max(c), 1))
            if any(j["clearly_positive"] for j in jobs):
                line = line.replace("; none is clearly above zero.", ".")
            lines.append(line)
            spy = next((j for j in jobs if j["symbol"] == "SPY"), None)
            if spy:
                v.update(spy_g=_f(spy["gross_bps"], 1, True), spy_lo=_f(spy["gross_lo"], 1, True), spy_hi=_f(spy["gross_hi"], 1, True))
        lines.append(T["slower"].format(n5=_f(b5.get("total_net_%_median"), 1, True), n15=_f(b15.get("total_net_%_median"), 1, True),
                                        n60=_f(b60.get("total_net_%_median"), 1, True), nd=_f(bd.get("total_net_%_median"), 1, True),
                                        g5=_f(b5.get("median_gross_bps"), 1), g15=_f(b15.get("median_gross_bps"), 1),
                                        g60=_f(b60.get("median_gross_bps"), 1), gd=_f(bd.get("median_gross_bps"), 0)))
    cr = res.get("close_rule") or []
    if cr:
        shares = [x["forced_%_of_cost"] for x in cr]
        spy = next((x for x in cr if x["symbol"] == "SPY"), None)
        lines.append(T["close"].format(lo=_f(min(shares), 0), hi=_f(max(shares), 0), spy=_f(spy["forced_%_of_cost"], 0) if spy else "–",
                                       neg=sum(x["net_without_forced_costs_%"] < 0 for x in cr), n=len(cr)))
    c = res.get("cost_realism") or {}
    if c.get("fills"):
        fs = c["fills"]["summary"].get("vs_mid_bps", {})
        tsla = ((c.get("spreads") or {}).get("TSLA") or {}).get("midday", {}).get("half_spread_bps")
        lines.append(T["fills"].format(mid=_f(fs.get("median"), 1, True), n=fs.get("n", 0), tsla=_f(tsla * 2 if tsla else None, 0)))
    w6 = res.get("walk_forward") or {}
    if w6.get("rows"):
        single = [x for x in w6["rows"] if x["strategy"] == "recalibration_single"]
        key = "walk_some" if len(w6["beat_matched"]) > 2 * w6["expected_false_positives"] else "walk_none"
        lines.append(T[key].format(beat=len(w6["beat_matched"]), tests=w6["tests"], fp=_f(w6["expected_false_positives"], 0),
                                   nsingle=len(single), worse=sum(x["significant"] and x["vs_matched_bps"] < 0 for x in single)))
    lines.append(T["recommend"])
    lines.append(T["caveats"])
    r = rc.settings(cfg) if cfg else {"score_weights": rc.DEFAULTS["score_weights"]}
    v["tw"] = str(r["score_weights"]["turnover"])
    keys = ("neg", "weeks_first", "mean", "bh", "rho", "cp5", "n5", "n5r", "ndr", "tw", "c5", "g5", "spy_g", "spy_lo", "spy_hi")
    fill = {**{k: "–" for k in keys}, **v}
    opts = [{"option": title.format(**fill), "evidence": evidence.format(**fill)} for title, evidence in plain.COST_OPTIONS]
    return {"lines": lines, "options": opts}


# ----------------------------------------------------------------- run / save
def _clean(o):
    return ov._clean(o)


def load(path: Path = OUT) -> Dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save(res: Dict, path: Path = OUT) -> Path:
    return ov.save(res, path)


STEPS = ["edges", "close", "costs", "replay", "walk"]


def run(cfg: Dict, steps: Sequence[str] = STEPS, symbols: Optional[Sequence[str]] = None, fetch=None, closes=None,
        as_of: Optional[date] = None, progress=None, merge: bool = True, path: Path = OUT) -> Dict:
    say = progress or (lambda f, m: None)
    syms = list(symbols or universe_symbols())
    say(0.0, f"loading {len(syms)} symbols")
    data = Data(syms, fetch=fetch, closes=closes, as_of=as_of)
    res = load(path) if merge else {}
    res.update({"as_of": str(data.as_of), "symbols": syms, "calendar": "alpaca" if data.closes else "guessed from the bars"})
    res.setdefault("generated", {})
    for k, s in enumerate(steps):
        sub = lambda f, m, k=k: say((k + f) / len(steps), m)  # noqa: E731
        if s == "edges":
            res["edges"] = edges(data, cfg, progress=sub)
        elif s == "close":
            res["close_rule"] = close_rule(data, cfg)
        elif s == "costs":
            sessions = sorted(set(data.df5[syms[0]].index.tz_convert(NY).date))[-5:]
            res["cost_realism"] = cost_realism(syms, sessions)
        elif s == "replay":
            res["replay"] = replay(data, cfg, progress=sub)
        elif s == "walk":
            res["walk_forward"] = walk(data, cfg, progress=sub)
        else:
            raise ValueError(f"unknown step {s}")
        res["generated"][s] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(_clean(res), path)  # after every step, so a long run keeps what it has
    res["summary"] = summary(res, cfg)
    save(_clean(res), path)
    return res


def main():
    import argparse
    import sys
    import yaml
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Cost study (diagnosis only).")
    ap.add_argument("--steps", default=",".join(STEPS))
    ap.add_argument("--symbols", default="")
    ap.add_argument("--summary", action="store_true", help="only rewrite the summary of the saved study")
    a = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    if a.summary:
        res = load()
        res["summary"] = summary(res, cfg)
        save(_clean(res))
        print("\n".join(res["summary"]["lines"]))
        for o in res["summary"]["options"]:
            print(f"- {o['option']}: {o['evidence']}")
        return
    run(cfg, [s for s in a.steps.split(",") if s], symbols=[s for s in a.symbols.split(",") if s] or None,
        progress=lambda f, m: print(f"{f:5.1%} {m}", flush=True))
    print("saved", OUT)


if __name__ == "__main__":
    main()
