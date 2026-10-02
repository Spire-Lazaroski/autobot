"""
Overnight study: is selling everything before the close better than holding, and when?

The live bot closes stock positions `risk.flatten_before_close_min` minutes before the close.
This module replays a job's strategy under other end-of-day policies:

  flatten       flat N minutes before the close (N = 5, 15, 30), today's behaviour
  hold          never force-flatten; the strategy alone decides
  conditional   at the flatten time, keep the position overnight only if every required
                condition holds (CONDITIONS), otherwise flatten as usual

How the night is modelled (bars are regular-hours only):
- The decision is made at the close of the "decision bar", the first bar that ends within N
  minutes of the session close but at least one bar before it (the last moment an order can
  still be filled before the close) (13:00 on half days; the calendar is known in advance, as the
  live bot gets it from Alpaca's clock).
- A target decided on the session's last bar can't be traded at that close (the bot ticks after
  the market has shut); it is filled at the next open. So the position held through the night
  is the one held on the last bar, it earns the gap (first bar's open vs the previous close),
  and the trade to yesterday's last target happens at the open.
- Fills at the open and in the session's last bar pay `edge_mult` x the normal costs (wider
  spreads there).
- Per bar, the return is split into overnight (nights to the next calendar day), weekend (nights
  over a weekend or holiday) and intraday parts; costs are reported separately.

Causal: the decision at a bar's close uses bars up to that close, the strategy signal, the
regime (decisions.regime_params: a regime_switch job's own thresholds, defaults otherwise), the
calendar, earnings sessions published in advance (news_guard.earnings_sessions) and stories
received up to that close. tests/test_no_lookahead.py checks it on truncated data.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from . import indicators as ind
from .backtest import CostModel

NY = "America/New_York"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "overnight_study.json"
LONG_DAYS = 740

# condition -> plain description (reports and the Lab use plain.OVERNIGHT_CONDITIONS)
CONDITIONS = ["signal_on", "in_profit", "in_profit_x", "trend_regime", "not_friday", "no_earnings", "no_bad_news"]

DEFAULTS = {
    "flatten_mins": [5, 15, 30],
    "edge_mult": 2.0,          # cost multiplier for fills at the open and in the last bar
    "profit_x_pct": 0.5,       # in_profit_x: unrealised gain above this %
    "bad_news_hours": 24,      # no_bad_news: look back this many hours from the decision
    "bad_news_materiality": 4,  # ... at stories of at least this materiality (1-5)
    "bad_news_alt_materiality": 3,  # reported alongside as no_bad_news_m3
    "bad_news_sentiment": 0.0,  # ... with sentiment below this (-1..+1)
}


@dataclass(frozen=True)
class Policy:
    name: str
    mode: str = "flatten"            # flatten | hold | conditional
    flatten_min: int = 5             # minutes before the close: the decision (and flatten) time
    require: Tuple[str, ...] = ()    # conditional: every one must hold to keep the position


def policies(flatten_min: int = 5, flatten_mins: Sequence[int] = (5, 15, 30)) -> List[Policy]:
    """The policies the study compares: flatten at each N, hold, each condition alone and the
    sensible combinations (decided at the live flatten time `flatten_min`)."""
    out = [Policy(f"flatten_{n}", "flatten", n) for n in flatten_mins]
    out.append(Policy("hold", "hold", flatten_min))
    for c in CONDITIONS:
        out.append(Policy(f"if_{c}", "conditional", flatten_min, (c,)))
    combos = {
        "if_signal_profit": ("signal_on", "in_profit"),
        "if_signal_trend": ("signal_on", "trend_regime"),
        "if_signal_profit_trend": ("signal_on", "in_profit", "trend_regime"),
        "if_signal_safe": ("signal_on", "no_earnings", "no_bad_news"),
        "if_signal_profit_safe": ("signal_on", "in_profit", "no_earnings", "no_bad_news"),
        "if_all": ("signal_on", "in_profit", "trend_regime", "not_friday", "no_earnings", "no_bad_news"),
        "if_all_fridays_ok": ("signal_on", "in_profit", "trend_regime", "no_earnings", "no_bad_news"),
    }
    out += [Policy(k, "conditional", flatten_min, v) for k, v in combos.items()]
    out.append(Policy("if_no_bad_news_m3", "conditional", flatten_min, ("no_bad_news_m3",)))
    return out


# ----------------------------------------------------------------- calendar
def calendar(df: pd.DataFrame, closes: Optional[Dict[date, pd.Timestamp]] = None) -> pd.DataFrame:
    """Per New York session date: its close time (UTC) and the next session's date.
    `closes`: the exchange calendar's close per date (Alpaca's calendar, see trading_calendar);
    without it, a session whose last bar ends by 14:00 is taken as a half day (13:00 close; IEX
    prints a few bars after a half day's close), otherwise 16:00. Built for the whole history
    and passed around as "known in advance", so truncating the bars does not change it."""
    step = pd.Timedelta(minutes=ind.step_minutes(df))
    ny_end = (df.index + step).tz_convert(NY)
    day = np.asarray(df.index.tz_convert(NY).date)
    last_end = pd.Series(ny_end, index=day).groupby(level=0).max()
    rows = []
    for d, t in last_end.items():
        if closes and d in closes:
            close = pd.Timestamp(closes[d])
            close = close.tz_localize(NY) if close.tzinfo is None else close
        else:
            hh = 13 if t.hour * 60 + t.minute <= 14 * 60 else 16
            close = pd.Timestamp(d).tz_localize(NY) + pd.Timedelta(hours=hh)
        rows.append({"date": d, "close": close.tz_convert(NY)})
    cal = pd.DataFrame(rows).set_index("date").sort_index()
    cal["close"] = pd.to_datetime(cal.close, utc=True)
    cal["next"] = list(cal.index[1:]) + [None]
    return cal


def trim_to_calendar(df: pd.DataFrame, cal: pd.DataFrame) -> pd.DataFrame:
    """Drop bars that end after their session's close (after-hours prints on half days)."""
    step = pd.Timedelta(minutes=ind.step_minutes(df))
    day = np.asarray(df.index.tz_convert(NY).date)
    close = pd.to_datetime(pd.Series(day).map(cal.close).to_numpy(), utc=True)
    keep = np.asarray(df.index + step <= close) | pd.isna(close)
    out = df[keep]
    out.attrs.update(df.attrs)
    return out


def next_weekday(d: date) -> date:
    """Fallback next session for the last date in the data (the live bot asks Alpaca)."""
    n = pd.Timestamp(d) + pd.offsets.BDay(1)
    return n.date()


# ----------------------------------------------------------------- inputs
@dataclass
class Inputs:
    """Everything the simulation needs for one symbol and strategy."""
    df: pd.DataFrame
    signal: pd.Series                       # the strategy's raw targets (-1..1), no flatten rule
    cal: pd.DataFrame                       # calendar(), known in advance
    costs: CostModel = field(default_factory=CostModel)
    edge_mult: float = 2.0
    regime: Optional[np.ndarray] = None     # per-bar regime label (or None: never "trend")
    earnings: frozenset = frozenset()       # session dates that react to an earnings release
    bad_news_t: np.ndarray = field(default_factory=lambda: np.zeros(0, "int64"))  # ns UTC, sorted
    bad_news_hours: float = 24.0
    profit_x_pct: float = 0.5
    bad_news_alt: Dict[str, np.ndarray] = field(default_factory=dict)  # extra flags no_bad_news_<key>

    def __post_init__(self):
        df = self.df
        n = len(df)
        step = pd.Timedelta(minutes=ind.step_minutes(df))
        self.step_min = step.total_seconds() / 60
        self.day = np.asarray(df.index.tz_convert(NY).date)
        self.end_ns = (df.index + step).as_unit("ns").asi8
        close_by_day = {d: int(t.value) for d, t in self.cal.close.items()}
        self.close_ns = np.array([close_by_day.get(d, e) for d, e in zip(self.day, self.end_ns)], dtype="int64")
        self.mins_to_close = (self.close_ns - self.end_ns) / 6e10
        self.first = np.r_[True, self.day[1:] != self.day[:-1]] if n else np.zeros(0, bool)
        o, c = df.open.to_numpy(float), df.close.to_numpy(float)
        prev_c = np.r_[np.nan, c[:-1]]
        self.o, self.c = o, c
        self.gap = np.where(self.first, o / prev_c - 1, 0.0)        # the night before a session's first bar
        self.gap[0] = 0.0
        self.x_first = c / o - 1                                     # first bar: open -> close
        self.r_bar = np.r_[0.0, c[1:] / c[:-1] - 1]                  # other bars: close -> close
        # night kind for each first bar: next calendar day (overnight) or longer (weekend/holiday)
        prev_day = np.r_[self.day[:1], self.day[:-1]]
        self.weekend = np.array([(a - b).days > 1 for a, b in zip(self.day, prev_day)]) & self.first
        self.sig = np.nan_to_num(self.signal.reindex(df.index).to_numpy(float))
        starts = np.flatnonzero(self.first)
        self.sessions = list(zip(starts, np.r_[starts[1:], n]))
        self.session_dates = [self.day[a] for a, _ in self.sessions]

    def decision_bar(self, a: int, b: int, flatten_min: int) -> Optional[int]:
        """The first bar of [a, b) that ends within flatten_min of the close but at least one bar
        before it (an order decided at the last bar's close can't be filled before the close, so
        e.g. 15-minute bars with N = 5 decide at 15:45). Clock-based, so causal. None if the
        session's bars stop before the flatten time (missing data): then nothing is decided and
        the strategy alone holds or not. If the bars inside that window are missing (thin IEX
        data) but a last bar follows, the live bot still acts on the clock with what it knows,
        so the decision falls on the bar before."""
        m = self.mins_to_close[a:b]
        k = np.flatnonzero(m <= max(flatten_min, self.step_min) + 1e-9)
        if not len(k):
            return None
        i = a + int(k[0])
        if m[k[0]] >= self.step_min - 1e-9:
            return i
        return i - 1 if i - 1 >= a else None

    def next_session(self, d: date) -> date:
        nxt = self.cal.next.get(d) if d in self.cal.index else None
        return nxt if nxt is not None and not pd.isna(nxt) else next_weekday(d)

    def flags(self, i: int, pos: float, entry: float) -> Dict[str, bool]:
        """Every condition at the close of bar i, holding `pos` bought/sold at `entry`."""
        pnl = np.sign(pos) * (self.c[i] / entry - 1) * 100 if pos != 0 and entry > 0 else 0.0
        d = self.day[i]
        t = self.end_ns[i]
        lo = np.searchsorted(self.bad_news_t, t - int(self.bad_news_hours * 3.6e12), side="right")
        hi = np.searchsorted(self.bad_news_t, t, side="right")
        return {
            "signal_on": bool(self.sig[i] != 0),
            "in_profit": bool(pos != 0 and pnl > 0),
            "in_profit_x": bool(pos != 0 and pnl > self.profit_x_pct),
            "trend_regime": bool(self.regime is not None and self.regime[i] == "trend"),
            "not_friday": pd.Timestamp(d).weekday() != 4,
            "no_earnings": self.next_session(d) not in self.earnings,
            "no_bad_news": bool(hi <= lo),
            **{f"no_bad_news_{k}": self._quiet(v, t) for k, v in self.bad_news_alt.items()},
            "_pnl_%": float(pnl),
        }

    def _quiet(self, times: np.ndarray, t: int) -> bool:
        lo = np.searchsorted(times, t - int(self.bad_news_hours * 3.6e12), side="right")
        return bool(np.searchsorted(times, t, side="right") <= lo)


# ----------------------------------------------------------------- simulation
def simulate(inp: Inputs, policy: "Policy | Sequence[Policy]") -> Dict:
    """Replay the strategy with an end-of-day policy (one, or one per session for the
    walk-forward). Returns {"frame": per-bar ret / position / turnover / cost / overnight /
    weekend / intraday / equity, "nights": one row per session's decision}."""
    n = len(inp.df)
    per_session = list(policy) if isinstance(policy, (list, tuple)) else [policy] * len(inp.sessions)
    T = inp.sig.copy()
    E = np.zeros(n)
    ret, cost, on, wk, turn = (np.zeros(n) for _ in range(5))
    c = inp.costs.per_turn
    prev, entry = 0.0, np.nan
    nights = []
    pending = None  # the last session's decision row, completed at the next open
    for (a, b), pol in zip(inp.sessions, per_session):
        d = inp.decision_bar(a, b, pol.flatten_min)
        for i in range(a, b):
            if i == 0:
                E[i] = 0.0
                continue
            if i == a:  # the night, then the open: trade to the last bar's target at the open
                night, new = prev, T[i - 1]
                g = inp.gap[i]
                tv = abs(new - night)
                k = tv * c * inp.edge_mult
                r = (1 + night * g) * (1 + new * inp.x_first[i]) - 1 - k
                (wk if inp.weekend[i] else on)[i] = night * g
                if new != 0 and (night == 0 or np.sign(new) != np.sign(night)):
                    entry = inp.o[i]
                if pending is not None:
                    pending.update({"next_date": inp.day[i], "kind": "weekend" if inp.weekend[i] else "overnight",
                                    "gap_%": g * 100, "result_%": night * g * 100})
                    nights.append(pending)
                    pending = None
            else:
                new = T[i - 1]
                tv = abs(new - prev)
                edge = inp.mins_to_close[i - 1] <= inp.step_min + 1e-9  # filled in the session's last bar
                k = tv * c * (inp.edge_mult if edge else 1.0)
                r = new * inp.r_bar[i] - k
                if new != 0 and (prev == 0 or np.sign(new) != np.sign(prev)):
                    entry = inp.c[i - 1]
            E[i], ret[i], cost[i], turn[i] = new, r, k, tv
            prev = new
            if i == d:
                f = inp.flags(i, new, entry)
                keep = pol.mode == "hold" or (pol.mode == "conditional" and all(f[x] for x in pol.require))
                if not keep:
                    T[d:b] = 0.0
                pending = {"date": inp.day[i], "policy": pol.name, "decided_at": inp.df.index[i],
                           "pos_at_decision": float(new), "kept": bool(keep), **f}
        if pending is not None:  # the position actually carried is the last bar's (filled in at the open)
            pending["pos_night"] = float(E[b - 1])
    frame = pd.DataFrame({"ret": ret, "position": E, "turnover": turn, "cost": cost, "overnight": on,
                          "weekend": wk}, index=inp.df.index)
    frame["intraday"] = frame.ret + frame.cost - frame.overnight - frame.weekend
    frame["equity"] = (1 + frame.ret).cumprod()
    return {"frame": frame, "nights": pd.DataFrame(nights)}


def flatten_reference(inp: Inputs, flatten_min: int) -> pd.Series:
    """The live/backtest flatten rule (recalibrate.flat_before_close) on the raw signal; with
    edge_mult=1 simulate(Policy('flatten', ...)) reproduces run_backtest on it."""
    from .recalibrate import flat_before_close
    return flat_before_close(inp.signal, inp.df, flatten_min)


# ----------------------------------------------------------------- per-symbol inputs
def bad_news_times(symbol: str, materiality: int, sentiment_below: float) -> np.ndarray:
    """Receive times (ns UTC, sorted) of material negative stories on the symbol, from the news
    store (keyword scores, or Claude's where scored). Empty if there is no news database."""
    from .news_guard import stories
    s = stories(symbol)
    if s is None or s.empty or "sentiment" not in s:
        return np.zeros(0, "int64")
    m = s[(s.materiality >= materiality) & (s.sentiment < sentiment_below)]
    return np.sort(m.t_info.dt.tz_convert("UTC").astype("datetime64[ns, UTC]").values.astype("int64"))


def build_inputs(symbol: str, df: pd.DataFrame, strategy: str, params: Dict, costs: CostModel,
                 opts: Optional[Dict] = None, cal: Optional[pd.DataFrame] = None,
                 allow_short: bool = False, news: bool = True) -> Inputs:
    from .decisions import regime_params
    from .meta import classify_regimes
    from .news_guard import earnings_sessions
    from .strategies import get_signal
    o = {**DEFAULTS, **(opts or {})}
    df.attrs.setdefault("symbol", symbol)
    sig = get_signal(strategy, df, params or {}, allow_short)
    regime = np.asarray(classify_regimes(df, **regime_params(strategy, params or {})), dtype=object)
    return Inputs(df=df, signal=sig, cal=cal if cal is not None else calendar(df), costs=costs,
                  edge_mult=float(o["edge_mult"]), regime=regime,
                  earnings=frozenset(earnings_sessions(symbol)) if news else frozenset(),
                  bad_news_t=bad_news_times(symbol, int(o["bad_news_materiality"]), float(o["bad_news_sentiment"]))
                  if news else np.zeros(0, "int64"),
                  bad_news_hours=float(o["bad_news_hours"]), profit_x_pct=float(o["profit_x_pct"]),
                  bad_news_alt={"m3": bad_news_times(symbol, int(o["bad_news_alt_materiality"]), float(o["bad_news_sentiment"]))
                                if news else np.zeros(0, "int64")})


# ----------------------------------------------------------------- walk-forward
def session_index(inp: Inputs, bar: int) -> int:
    """Index of the session that contains `bar`."""
    starts = np.array([a for a, _ in inp.sessions])
    return int(np.searchsorted(starts, bar, side="right") - 1)


def walk_forward(inp: Inputs, pols: List[Policy], win: List[Tuple[int, int, int, int]],
                 frames: Dict[str, pd.DataFrame], score_fn, fallback: Policy) -> Dict:
    """In each window the policy that scores best on the TRAIN bars (score_fn(frame, a, b)) is
    used on the next TEST sessions; the whole path is simulated once with those per-session
    policies (a change of policy pays for its position change), so the test slices are out of
    sample. Sessions outside the test windows use `fallback`."""
    per = [fallback] * len(inp.sessions)
    choices = []
    for ta, tb, a, b in win:
        best = max(pols, key=lambda p: score_fn(frames[p.name], ta, tb))
        choices.append(best.name)
        for s in range(session_index(inp, a), session_index(inp, b - 1) + 1):
            per[s] = best
    return {"result": simulate(inp, per), "choices": choices}


# ----------------------------------------------------------------- the study
def trading_calendar(start: date, end: date) -> Optional[Dict[date, pd.Timestamp]]:
    """Session closes from Alpaca's trading calendar (half days included), or None offline."""
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetCalendarRequest
        from .data import _keys, with_timeout
        k, sec = _keys()
        tc = with_timeout(TradingClient(k, sec, paper=True))
        return {c.date: pd.Timestamp(c.close) for c in tc.get_calendar(GetCalendarRequest(start=start, end=end))}
    except Exception:  # noqa: BLE001  no keys / no network: calendar() falls back to the bars
        return None


def load_bars(sym: str, tf: str, days: int) -> pd.DataFrame:
    """Regular-hours bars for the last `days`: the newest cached download of that length if
    there is one (the 740-day 5-minute caches), else a fresh (free IEX) download."""
    from .data import CACHE_DIR, fetch_bars
    files = sorted(CACHE_DIR.glob(f"{sym.replace('/', '-')}_{tf}_{days}d_*.csv.gz"))
    end = None
    if files:
        stamp = files[-1].name.split("_")[-1].split(".")[0]
        end = datetime.strptime(stamp, "%Y%m%d").replace(hour=12, tzinfo=timezone.utc)
    return fetch_bars([sym], tf, days, end=end, rth_only=True)[sym]


def _span(day: np.ndarray, d0: date, d1: date) -> Tuple[int, int]:
    return int(np.searchsorted(day, d0, side="left")), int(np.searchsorted(day, d1, side="right"))


def _held(nights: pd.DataFrame, d0: date, d1: date) -> pd.DataFrame:
    """Nights in [d0, d1] (by decision date) on which a position was carried."""
    if nights is None or nights.empty or "pos_night" not in nights:
        return pd.DataFrame(columns=["symbol", "date", "next_date", "kind", "result_%", "gap_%", "pos_night"])
    n = nights[(nights.date >= d0) & (nights.date <= d1) & nights["result_%"].notna()]
    return n[n.pos_night != 0]


def _mean_ret(rets: List[pd.Series]) -> pd.Series:
    return pd.concat(rets, axis=1, sort=True).fillna(0).mean(axis=1) if rets else pd.Series(dtype=float)


def _stats(rets: List[pd.Series], parts: List[pd.DataFrame], nights: pd.DataFrame) -> Dict:
    """Equal-weight account over symbols: metrics of the averaged returns; costs and the P&L
    split as the average of the symbols' sums (% of the account); night statistics over every
    held symbol-night (% of that position)."""
    from .backtest import _trades, metrics
    ret = _mean_ret(rets)
    m = metrics(pd.DataFrame({"ret": ret, "equity": (1 + ret).cumprod(), "position": 1.0}, index=ret.index)) if len(ret) else {}
    k = max(1, len(parts))
    out = {"return_%": float(m.get("total_return_%", 0.0)), "sharpe": float(m.get("sharpe", 0.0)),
           "max_drawdown_%": float(m.get("max_drawdown_%", 0.0)),
           "trades": int(sum(len(_trades(p)) for p in parts)),
           "cost_%": round(sum(p.cost.sum() for p in parts) / k * 100, 2),
           "intraday_%": round(sum(p.intraday.sum() for p in parts) / k * 100, 2),
           "overnight_%": round(sum(p.overnight.sum() for p in parts) / k * 100, 2),
           "weekend_%": round(sum(p.weekend.sum() for p in parts) / k * 100, 2),
           "nights": int(len(nights))}
    if len(nights):
        r = nights["result_%"].astype(float)
        out.update({"helped_%": round(float((r > 0).mean() * 100), 1), "avg_night_%": round(float(r.mean()), 3),
                    "worst_night_%": round(float(r.min()), 2), "worst_night_date": str(nights["date"].to_numpy()[r.to_numpy().argmin()]),
                    "avg_gap_%": round(float(nights["gap_%"].abs().mean()), 3),
                    "worst_gap_%": round(float(nights["gap_%"].abs().max()), 2)})
    else:
        out.update({"helped_%": None, "avg_night_%": None, "worst_night_%": None, "worst_night_date": None,
                    "avg_gap_%": None, "worst_gap_%": None})
    return out


def _daily_equity(rets: List[pd.Series]) -> List[List]:
    """The equal-weight account's equity at each New York day's last bar, for charts."""
    ret = _mean_ret(rets)
    if ret.empty:
        return []
    eq = (1 + ret).cumprod()
    d = eq.groupby(np.asarray(eq.index.tz_convert(NY).date)).last()
    return [[str(k), round(float(v), 5)] for k, v in d.items()]


def _worth(nights: pd.DataFrame, pols: List[Policy]) -> List[Dict]:
    """Nights the strategy held at the close (the 'hold' policy), grouped by each condition and
    each combination, met or not: how many, average next-open result, share that gained, worst."""
    rows = []
    if nights.empty:
        return rows
    groups = [(c, (c,)) for c in CONDITIONS + ["no_bad_news_m3"] if c in nights]
    groups += [(p.name, p.require) for p in pols if p.mode == "conditional" and len(p.require) > 1]
    for name, req in groups:
        ok = np.logical_and.reduce([nights[c].astype(bool).to_numpy() for c in req])
        for val in (True, False):
            g = nights[ok == val]["result_%"].astype(float)
            rows.append({"group": name, "conditions": list(req), "met": val, "nights": int(len(g)),
                         "avg_%": round(float(g.mean()), 3) if len(g) else None,
                         "gained_%": round(float((g > 0).mean() * 100), 1) if len(g) else None,
                         "worst_%": round(float(g.min()), 2) if len(g) else None,
                         "sum_%": round(float(g.sum()), 2)})
    return rows


def _worst5(nights: pd.DataFrame) -> List[Dict]:
    if nights.empty:
        return []
    w = nights.sort_values("result_%").head(5)
    return [{"symbol": r["symbol"], "date": str(r["date"]), "next_date": str(r["next_date"]), "kind": r["kind"],
             "position": float(r["pos_night"]), "gap_%": round(float(r["gap_%"]), 2), "result_%": round(float(r["result_%"]), 2)}
            for _, r in w.iterrows()]


class SymbolRun:
    """One symbol and its job's strategy: every policy simulated once on the whole history."""

    def __init__(self, sym: str, job: Dict, df: pd.DataFrame, r: Dict, opts: Dict, closes, pols: List[Policy],
                 news: bool = True):
        from . import recalibrate as rc
        from .backtest import bars_per_year
        self.sym, self.job, self.r, self.pols = sym, job, r, pols
        cal = calendar(df, closes)
        df = trim_to_calendar(df, cal)
        self.inp = build_inputs(sym, df, job["strategy"], job.get("params") or {}, rc.costs_for(sym, r), opts, cal, news=news)
        self.sim = {p.name: simulate(self.inp, p) for p in pols}
        for v in self.sim.values():
            if len(v["nights"]):
                v["nights"]["symbol"] = sym
        self.ppy = bars_per_year(df.index)

    def score_fn(self, f: pd.DataFrame, a: int, b: int) -> float:
        from . import recalibrate as rc
        q = rc.quick(f.ret.to_numpy()[a:b], f.position.to_numpy()[a:b], self.ppy, len(set(self.inp.day[a:b])))
        return rc.score(q, self.r["score_weights"])

    def walk(self, d0: date, fallback: Policy) -> Optional[Dict]:
        """Recalibration's windows from d0 (train_days / test_days)."""
        from . import recalibrate as rc
        win = rc.windows(self.inp.day, d0, int(self.r["train_days"]), int(self.r["test_days"]))
        if not win:
            return None
        frames = {name: v["frame"] for name, v in self.sim.items()}
        wf = walk_forward(self.inp, self.pols, win, frames, self.score_fn, fallback)
        if len(wf["result"]["nights"]):
            wf["result"]["nights"]["symbol"] = self.sym
        return {"a": win[0][2], "b": win[-1][3], **wf}


def _period(runs: List[SymbolRun], d0s: Dict[str, date], d1: date, pols: List[Policy], base: Policy) -> Dict:
    """Every policy over [d0, d1] (per symbol and in total), the walk-forward on its test days,
    each fixed policy on the same test days, worst nights and the 'what was worth holding' table."""
    from .exit_study import paired
    out = {"from": str(min(d0s.values())), "to": str(d1), "policies": [], "test_days": [], "walk_forward": None,
           "worst_nights": {}, "worth_holding": [], "worth_holding_by_symbol": {}, "equity": {}, "equity_test": {}}
    for p in pols:
        rets, parts, nights, per_sym = [], [], [], {}
        for run in runs:
            a, b = _span(run.inp.day, d0s[run.sym], d1)
            part = run.sim[p.name]["frame"].iloc[a:b]
            n = _held(run.sim[p.name]["nights"], d0s[run.sym], d1)
            rets.append(part.ret)
            parts.append(part)
            nights.append(n)
            per_sym[run.sym] = _stats([part.ret], [part], n)
        nn = pd.concat([x for x in nights if len(x)]) if any(len(x) for x in nights) else _held(None, d1, d1)
        out["policies"].append({"name": p.name, "mode": p.mode, "flatten_min": p.flatten_min, "require": list(p.require),
                                "total": _stats(rets, parts, nn), "per_symbol": per_sym})
        out["worst_nights"][p.name] = _worst5(nn)
        out["equity"][p.name] = _daily_equity(rets)
        if p.mode == "hold":
            out["worth_holding"] = _worth(nn, pols)
            out["worth_holding_by_symbol"] = {run.sym: _worth(x, pols) for run, x in zip(runs, nights)}
    wf_rets, wf_parts, wf_nights, choices, per_sym = [], [], [], [], {}
    fixed: Dict[str, List[pd.DataFrame]] = {p.name: [] for p in pols}
    for run in runs:
        w = run.walk(d0s[run.sym], base)
        if w is None:
            continue
        a, b = w["a"], w["b"]
        f = w["result"]["frame"].iloc[a:b]
        da, db = run.inp.day[a], run.inp.day[b - 1]
        n = _held(w["result"]["nights"], da, db)
        wf_rets.append(f.ret)
        wf_parts.append(f)
        wf_nights.append(n)
        choices += w["choices"]
        for p in pols:
            fixed[p.name].append(run.sim[p.name]["frame"].iloc[a:b])
        per_sym[run.sym] = {**_stats([f.ret], [f], n), "from": str(da), "to": str(db),
                            "vs_flatten": paired(f.ret, run.sim[base.name]["frame"].iloc[a:b].ret),
                            "choices": {str(k): int(v) for k, v in pd.Series(w["choices"]).value_counts().items()}}
    if wf_rets:
        base_all = _mean_ret([x.ret for x in fixed[base.name]])
        nn = pd.concat([x for x in wf_nights if len(x)]) if any(len(x) for x in wf_nights) else _held(None, d1, d1)
        out["walk_forward"] = {**_stats(wf_rets, wf_parts, nn), "vs_flatten": paired(_mean_ret(wf_rets), base_all),
                               "choices": {str(k): int(v) for k, v in pd.Series(choices).value_counts().items()},
                               "per_symbol": per_sym, "worst_nights": _worst5(nn),
                               "from": min(v["from"] for v in per_sym.values()), "to": max(v["to"] for v in per_sym.values())}
        out["equity_test"]["walk_forward"] = _daily_equity(wf_rets)
        for p in pols:
            fr = fixed[p.name]
            out["test_days"].append({"name": p.name, **_stats([x.ret for x in fr], fr, _held(None, d1, d1)),
                                     "vs_flatten": None if p.name == base.name else paired(_mean_ret([x.ret for x in fr]), base_all)})
            out["equity_test"][p.name] = _daily_equity([x.ret for x in fr])
    return out


def policy_text(p: Dict, opts: Dict) -> str:
    """Plain description of a policy (plain.OVERNIGHT_POLICIES / OVERNIGHT_CONDITIONS)."""
    from . import plain
    if p["mode"] == "flatten":
        return plain.OVERNIGHT_POLICIES["flatten"].format(n=p["flatten_min"])
    if p["mode"] == "hold":
        return plain.OVERNIGHT_POLICIES["hold"]
    conds = [plain.OVERNIGHT_CONDITIONS[c].format(x=opts["profit_x_pct"], m=opts["bad_news_materiality"],
                                                  m3=opts["bad_news_alt_materiality"], h=opts["bad_news_hours"])
             for c in p["require"]]
    text = conds[0] if len(conds) == 1 else ", ".join(conds[:-1]) + " and " + conds[-1]
    return plain.OVERNIGHT_POLICIES["conditional"].format(conds=text)


def _bps(v: Dict) -> Dict[str, str]:
    return {"mean": f"{v['mean_bps']:+.1f}", "lo": f"{v['ci95_bps'][0]:+.1f}", "hi": f"{v['ci95_bps'][1]:+.1f}"}


def summary(res: Dict) -> List[str]:
    """Plain-English findings from plain.OVERNIGHT templates, filled with the numbers only."""
    from . import plain
    T = plain.OVERNIGHT
    lines: List[str] = []
    pkey = "long" if "long" in res["periods"] else "90d"
    per = res["periods"][pkey]
    period = T["period"][pkey].format(days=per.get("days", ""))
    wf = per.get("walk_forward")
    if wf:
        v = wf["vs_flatten"]
        key = "verdict_yes" if v["significant"] and v["mean_bps"] > 0 else "verdict_worse" if v["significant"] else "verdict_no"
        lines.append(T[key].format(**_bps(v)))
        cands = [t for t in per["test_days"] if t["vs_flatten"]]
        if cands:
            b = max(cands, key=lambda t: t["vs_flatten"]["mean_bps"])
            v = b["vs_flatten"]
            sig = "" if v["significant"] else ", within chance (and it is the best of many, chosen afterwards)"
            lines.append(T["best_fixed"].format(policy=res["policy_text"][b["name"]], diff=f"{v['mean_bps']:+.1f}",
                                                lo=f"{v['ci95_bps'][0]:+.1f}", hi=f"{v['ci95_bps'][1]:+.1f}", sig=sig))
    hold = next(p for p in per["policies"] if p["mode"] == "hold")
    flat = next(p for p in per["policies"] if p["name"] == res["base_policy"])
    for s in res["symbols"]:
        sym, nick = s["symbol"], plain.nick(s["strategy"])
        h, f = hold["per_symbol"].get(sym), flat["per_symbol"].get(sym)
        if not h or not f:
            continue
        if not h["nights"]:
            lines.append(T["symbol_none"].format(sym=sym, nick=nick))
            continue
        diff = h["return_%"] - f["return_%"]
        lines.append(T["symbol"].format(sym=sym, nick=nick, verb="added" if diff >= 0 else "lost", diff=f"{abs(diff):.1f}",
                                        period=period, nights=h["nights"], helped=f"{h['helped_%']:.0f}",
                                        worst=f"{h['worst_night_%']:+.2f}", date=h["worst_night_date"]))
        e = next((r for r in per["worth_holding_by_symbol"].get(sym, []) if r["group"] == "no_earnings" and r["met"] is False), None)
        if e and e["nights"]:
            lines.append(T["earnings"].format(sym=sym, n=e["nights"], avg=f"{e['avg_%']:+.2f}", worst=f"{e['worst_%']:+.2f}"))
    lines.append(T["caveat"].format(slip=f"{res['settings']['edge_mult']:g}"))
    return lines


def run(cfg: Dict, days: int = 90, long: bool = True, opts: Optional[Dict] = None,
        fetch: Optional[Callable[[str, str, int], pd.DataFrame]] = None, closes=None,
        as_of: Optional[date] = None, progress: Optional[Callable[[float, str], None]] = None,
        news: bool = True) -> Dict:
    """The study for every enabled job's stock symbols (crypto is never flattened).
    fetch(sym, tf, days) -> bars (tests); default load_bars (the 740-day cache for the long
    period). closes: the exchange calendar (default Alpaca's; None offline: guessed from bars)."""
    from . import recalibrate as rc
    from .data import is_crypto
    say = progress or (lambda f, m: None)
    o = {**DEFAULTS, **(opts or {})}
    live = cfg["live"]
    tf = live["timeframe"]
    jobs = [j for j in live.get("jobs") or [] if j.get("enabled", True)]
    pairs = [(j, s) for j in jobs for s in j["symbols"] if not is_crypto(s)]
    if not pairs:
        raise ValueError("No enabled stock jobs to study (crypto is never flattened).")
    r = rc.settings(cfg)
    n_cfg = int(r["flatten_min"]) or 5
    pols = policies(n_cfg, o["flatten_mins"])
    if all(p.name != f"flatten_{n_cfg}" for p in pols):
        pols.insert(0, Policy(f"flatten_{n_cfg}", "flatten", n_cfg))
    base = next(p for p in pols if p.name == f"flatten_{n_cfg}")
    load = fetch or load_bars
    want = LONG_DAYS if long else days + int(r["warmup_days"]) + 5
    data = {}
    for k, (job, sym) in enumerate(pairs):
        say(0.05 * k / len(pairs), f"{sym}: loading {want} days of {tf} bars")
        data[sym] = load(sym, tf, want)
    end = as_of or min(np.asarray(d.index.tz_convert(NY).date).max() for d in data.values())
    if closes is None and fetch is None:
        start = min(np.asarray(d.index.tz_convert(NY).date).min() for d in data.values())
        closes = trading_calendar(start, end + timedelta(days=10))
    runs = []
    for k, (job, sym) in enumerate(pairs):
        say(0.05 + 0.75 * k / len(pairs), f"{sym}: {len(pols)} overnight policies")
        df = data[sym]
        df = df[np.asarray(df.index.tz_convert(NY).date) <= end].copy()
        df.attrs["symbol"] = sym
        runs.append(SymbolRun(sym, job, df, r, o, closes, pols, news=news))
    periods = {}
    say(0.82, f"last {days} days and walk-forward")
    short0 = end - timedelta(days=days - 1)
    periods["90d"] = {**_period(runs, {x.sym: short0 for x in runs}, end, pols, base), "days": days}
    if long:
        say(0.9, "whole period and walk-forward")
        d0s = {x.sym: x.inp.day[0] + timedelta(days=int(r["warmup_days"])) for x in runs}
        periods["long"] = {**_period(runs, d0s, end, pols, base), "days": (end - min(d0s.values())).days + 1}
    res = {"as_of": str(end), "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "timeframe": tf, "flatten_min": n_cfg, "base_policy": base.name,
           "calendar": "alpaca" if closes else "guessed from the bars",
           "train_days": int(r["train_days"]), "test_days": int(r["test_days"]),
           "settings": {k: o[k] for k in DEFAULTS},
           "symbols": [{"symbol": s, "job": j["name"], "strategy": j["strategy"], "params": j.get("params") or {}} for j, s in pairs],
           "policy_text": {p.name: policy_text({"mode": p.mode, "flatten_min": p.flatten_min, "require": list(p.require)}, o)
                           for p in pols},
           "periods": periods}
    res["summary"] = summary(res)
    wf = (periods.get("long") or periods["90d"]).get("walk_forward")
    res["beats_flatten_oos"] = bool(wf and wf["vs_flatten"]["significant"] and wf["vs_flatten"]["mean_bps"] > 0)
    say(1.0, "done")
    return res


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return round(float(o), 6) if math.isfinite(float(o)) else None
    if isinstance(o, (date, pd.Timestamp)):
        return str(o)
    return o


def save(res: Dict, path: Path = OUT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(_clean(res), indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def load(path: Path = OUT) -> Optional[Dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main():
    import argparse
    import sys
    import yaml
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Overnight study: sell before the close vs hold overnight.")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--no-long", action="store_true", help="only the last --days (skip the 740-day period)")
    a = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    res = run(cfg, a.days, long=not a.no_long, progress=lambda f, m: print(f"{f:4.0%} {m}", flush=True))
    print("\n".join(res["summary"]))
    print("saved", save(res))


if __name__ == "__main__":
    main()
