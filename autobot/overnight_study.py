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

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from . import indicators as ind
from .backtest import CostModel

NY = "America/New_York"

# condition -> plain description (reports and the Lab use plain.OVERNIGHT_CONDITIONS)
CONDITIONS = ["signal_on", "in_profit", "in_profit_x", "trend_regime", "not_friday", "no_earnings", "no_bad_news"]

DEFAULTS = {
    "flatten_mins": [5, 15, 30],
    "edge_mult": 2.0,          # cost multiplier for fills at the open and in the last bar
    "profit_x_pct": 0.5,       # in_profit_x: unrealised gain above this %
    "bad_news_hours": 24,      # no_bad_news: look back this many hours from the decision
    "bad_news_materiality": 3,  # ... at stories of at least this materiality (1-5)
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
        session has no such bar (missing data): then nothing is decided and the strategy alone
        holds or not."""
        m = self.mins_to_close[a:b]
        k = np.flatnonzero((m <= max(flatten_min, self.step_min) + 1e-9) & (m >= self.step_min - 1e-9))
        return a + int(k[0]) if len(k) else None

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
            "_pnl_%": float(pnl),
        }


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
                  bad_news_hours=float(o["bad_news_hours"]), profit_x_pct=float(o["profit_x_pct"]))


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
