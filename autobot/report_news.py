"""
News for the plain-English report (Phase 5 step 6).

Collects the material stories on the report's symbols for its period and turns them into
fixed-template sentences: a per-symbol summary with the top headlines, the biggest 15-minute
move of the day and whether a story arrived in the hour before it, a "news near this trade"
note, upcoming earnings dates and a warning when the news collector seems to have stopped.

Nothing here says the news CAUSED a trade or a move: the live jobs don't trade on news (unless
a job uses news_guard, and then the sentence says so), and a story before a move is reported as
timing only.

Stories are scored with the keyword rules if they haven't been yet (free, no API); Claude's
newest prompt is used where a story was scored by Claude.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import news

ETFS = {"SPY", "QQQ", "IWM", "DIA"}
MATERIALITY = {"stock": 3, "etf": 4}  # same thresholds as the news-guard study
TRADE_WINDOW = pd.Timedelta(minutes=60)
MOVE_WINDOW_MIN = 15
TOP_PER_SYMBOL = 5
TOP_BACKTEST = 10
UPCOMING_DAYS = 7
COLLECTOR_QUIET = pd.Timedelta(hours=2)
NY = "America/New_York"
CONTEXT_ONLY = "The bot does not trade on news; this is context only."
NEWS_RULES = "This job's news rules can act on stories like this; the reason above says whether they did."


def min_materiality(symbol: str) -> int:
    return MATERIALITY["etf" if symbol in ETFS else "stock"]


def load(symbols: List[str], a: pd.Timestamp, b: pd.Timestamp) -> Optional[pd.DataFrame]:
    """Material stories (main or secondary subject) received in [a - 1 h, b) on the symbols:
    one row per story and symbol, with the time we could act on it (t_info). None when there
    is no news database."""
    from . import news_score as ns
    if not news.DB_PATH.exists():
        return None
    try:
        ns.run_rules()  # score stories the collector added since the last run (keyword rules, free)
    except Exception:  # noqa: BLE001  scoring problems must not stop a report
        pass
    syms = [s for s in symbols if "/" not in s]
    if not syms:
        return pd.DataFrame()
    lo = (a - TRADE_WINDOW - pd.Timedelta(days=1)).isoformat()  # created_at lower bound (received is later)
    q = f"""
      SELECT e.id, i.symbol, e.created_at, e.received_at, e.headline,
             COALESCE(c.materiality, r.materiality) materiality, COALESCE(c.category, r.category) category,
             COALESCE(c.sentiment, r.sentiment) sentiment, COALESCE(c.relevance, r.relevance) relevance,
             CASE WHEN c.event_id IS NULL THEN 'keyword rules' ELSE 'Claude' END scorer
      FROM events e JOIN item_symbols i ON i.item_id = e.first_item_id
      LEFT JOIN scores c ON c.event_id=e.id AND c.symbol=i.symbol AND c.scorer='claude' AND c.version=?
      LEFT JOIN scores r ON r.event_id=e.id AND r.symbol=i.symbol AND r.scorer='rules' AND r.version=?
      WHERE i.symbol IN ({','.join('?' * len(syms))}) AND e.created_at >= ? AND e.created_at < ?
        AND COALESCE(c.relevance, r.relevance) IN ('main', 'secondary')"""
    with news.connect() as con:
        df = pd.read_sql(q, con, params=(ns.PROMPT_VERSION, ns.RULES_VERSION, *syms, lo, b.isoformat()))
    if df.empty:
        return df
    created = pd.to_datetime(df.created_at, utc=True, format="ISO8601")
    recv = pd.to_datetime(df.received_at, utc=True, format="ISO8601")
    from .event_study import BACKFILL_DELAY
    df["t_info"] = recv.where(recv.notna(), created + BACKFILL_DELAY)
    df = df[df.materiality >= df.symbol.map(min_materiality)]
    df = df[(df.t_info >= a - TRADE_WINDOW) & (df.t_info < b)]
    return df.sort_values("t_info").reset_index(drop=True)


def last_received(a: pd.Timestamp, b: pd.Timestamp) -> Optional[pd.Timestamp]:
    """The latest time the collector received any story live in [a, b) (stream or poll)."""
    if not news.DB_PATH.exists():
        return None
    with news.connect() as con:
        r = con.execute("SELECT MAX(received_at) m FROM items WHERE received_at >= ? AND received_at < ?",
                        (a.isoformat(), b.isoformat())).fetchone()
    return pd.Timestamp(r["m"]).tz_convert("UTC") if r and r["m"] else None


def backfilled_count(a: pd.Timestamp, b: pd.Timestamp) -> int:
    """Stories published in [a, b) that the collector only filled in afterwards (not received live)."""
    if not news.DB_PATH.exists():
        return 0
    with news.connect() as con:
        return con.execute("SELECT COUNT(*) c FROM items WHERE received_at IS NULL AND created_at >= ? AND created_at < ?",
                           (a.isoformat(), b.isoformat())).fetchone()["c"]


def upcoming_earnings(symbols: List[str], after: date, days: int = UPCOMING_DAYS) -> List[tuple]:
    """(date, symbol) for earnings sessions listed in config news.earnings_dates in the next `days`."""
    from . import news_guard as ng
    out = []
    for s in symbols:
        for d in ng.earnings_sessions(s):
            if after < d <= after + timedelta(days=days):
                out.append((d, s))
    return sorted(out)


def earnings_days(symbols: List[str]) -> Dict[str, set]:
    from . import news_guard as ng
    return {s: set(ng.earnings_sessions(s)) for s in symbols if "/" not in s}


# ----------------------------------------------------------------- collecting for a report
def collect(symbols: List[str], a: pd.Timestamp, b: pd.Timestamp, kind: str, news_aware: Optional[set] = None,
            watch_symbols: Optional[List[str]] = None, is_demo: bool = False) -> Dict:
    """Everything the report's news parts need, gathered once. Never raises: a problem becomes
    a note in the section."""
    out = {"available": False, "note": None, "stories": pd.DataFrame(), "earnings": {}, "upcoming": [],
           "last_received": None, "news_aware": set(news_aware or ()), "symbols": [s for s in symbols if "/" not in s],
           "period": (a, b), "kind": kind}
    if is_demo:
        out["note"] = "Demo data has no news."
        return out
    try:
        st = load(symbols, a, b)
        if st is None:
            out["note"] = "No news database yet (start the news collector)."
            return out
        out.update(available=True, stories=st, earnings=earnings_days(out["symbols"]))
        if kind == "live":
            end_day = (b - pd.Timedelta(seconds=1)).tz_convert(NY).date()
            out["upcoming"] = upcoming_earnings(sorted(set(watch_symbols or symbols)), end_day)
            out["last_received"] = last_received(a, b)
            out["backfilled"] = backfilled_count(a, b)
    except Exception as e:  # noqa: BLE001
        out["available"] = False
        out["note"] = f"News could not be read ({e})."
    return out


# ----------------------------------------------------------------- sentences (fixed templates)
def _ny(t) -> pd.Timestamp:
    return pd.Timestamp(t).tz_convert(NY)


def _cut(h: str, n: int = 110) -> str:
    import html
    h = " ".join(html.unescape(str(h)).split())  # the feed sends some headlines with HTML codes (&#39;)
    return h if len(h) <= n else h[: n - 1].rstrip() + "…"


def story_line(r, with_day: bool = False) -> str:
    when = _ny(r.t_info).strftime("%a %d %b %H:%M" if with_day else "%H:%M")
    return (f"{when} · {str(r.category).replace('_', ' ')} · materiality {int(r.materiality)} · "
            f"‘{_cut(r.headline)}’ ({r.scorer})")


def _mins(x: pd.Timedelta) -> str:
    m = int(round(x.total_seconds() / 60))
    return "just" if m <= 0 else f"{m} minute{'s' if m != 1 else ''}"


def trade_note(nd: Dict, t: Dict) -> Optional[str]:
    """A material story on the trade's symbol in the hour before its entry or exit (the latest one)."""
    if not nd.get("available") or nd["stories"].empty:
        return None
    st = nd["stories"][nd["stories"].symbol == t["symbol"]]
    if st.empty:
        return None
    found = []
    for when, what in ((t["entry_time"], "buy" if t["side"] == "long" else "short sale"),
                       (t.get("exit_time"), "sell" if t["side"] == "long" else "buy-back")):
        if when is None:
            continue
        when = pd.Timestamp(when)
        near = st[(st.t_info <= when) & (st.t_info > when - TRADE_WINDOW)]
        if len(near):
            r = near.iloc[-1]
            found.append(f"a materiality-{int(r.materiality)} {str(r.category).replace('_', ' ')} story arrived "
                         f"{_mins(when - r.t_info)} before this {what}: ‘{_cut(r.headline, 90)}’")
    if not found:
        return None
    tail = NEWS_RULES if t["symbol"] in nd["news_aware"] else CONTEXT_ONLY
    return "News: " + "; ".join(found) + ". " + tail


def biggest_move(nd: Dict, sym: str, df: pd.DataFrame) -> Optional[str]:
    """The largest 15-minute move of the period on the symbol and whether a material story
    arrived in the hour before its end. Timing only: no claim that the story caused it."""
    if df is None or len(df) < 3:
        return None
    step = max(1, int(pd.Series(df.index).diff().median().total_seconds() // 60))
    k = max(1, MOVE_WINDOW_MIN // step)
    day = df.index.tz_convert(NY).date
    ret = df.close / df.close.shift(k) - 1
    ret[pd.Series(day, index=df.index) != pd.Series(day, index=df.index).shift(k)] = np.nan  # not across the night
    if ret.dropna().empty:
        return None
    i = ret.abs().idxmax()
    mv = float(ret.loc[i]) * 100
    end = i + pd.Timedelta(minutes=step)  # the move is complete at that bar's close
    multi = len(set(day)) > 1
    s = f"Biggest 15-minute move: {mv:+.2f}% ending {_ny(end):{'%a %d %b %H:%M' if multi else '%H:%M'}}."
    if nd.get("available"):
        st = nd["stories"][nd["stories"].symbol == sym]
        near = st[(st.t_info <= end) & (st.t_info > end - TRADE_WINDOW)]
        if len(near):
            r = near.iloc[-1]
            s += (f" A materiality-{int(r.materiality)} story arrived {_mins(end - r.t_info)} before it ended: "
                  f"‘{_cut(r.headline, 90)}’. This shows timing only, not that the story caused the move.")
        else:
            s += " No material story arrived in the hour before it."
    return s


def section(nd: Dict, bars: Dict[str, pd.DataFrame]) -> List[Dict]:
    """Per symbol: {symbol, summary, lines, move}. Backtests: one condensed block."""
    if not nd.get("available"):
        return []
    a, b = nd["period"]
    st = nd["stories"]
    st = st[(st.t_info >= a) & (st.t_info < b)]
    days_in = {d for df in bars.values() for d in df.index.tz_convert(NY).date} if bars else set()
    out = []
    if nd["kind"] == "backtest":
        counts = st.groupby("symbol").size().to_dict()
        top = st.sort_values(["materiality", "t_info"], ascending=[False, False]).head(TOP_BACKTEST)
        summary = ", ".join(f"{s} {counts.get(s, 0)}" for s in nd["symbols"])
        out.append({"symbol": None, "summary": f"Material stories in the period: {summary}.",
                    "lines": [f"{r.symbol}: {story_line(r, with_day=True)}" for r in top.itertuples()], "move": None})
        return out
    for sym in nd["symbols"]:
        s = st[st.symbol == sym]
        cats = s.category.str.replace("_", " ").value_counts()
        n = len(s)
        summ = (f"{n} material stor{'y' if n == 1 else 'ies'} ({', '.join(f'{v} {k}' for k, v in cats.items())})" if n else "No material stories"
                ) + f" at materiality {min_materiality(sym)} or more."
        if nd["earnings"].get(sym, set()) & days_in:
            summ += " Earnings day: this session reacted to the company's quarterly results."
        top = s.sort_values(["materiality", "t_info"], ascending=[False, True]).head(TOP_PER_SYMBOL).sort_values("t_info")
        out.append({"symbol": sym, "summary": summ, "lines": [story_line(r) for r in top.itertuples()],
                    "more": max(0, n - TOP_PER_SYMBOL), "move": biggest_move(nd, sym, bars.get(sym))})
    return out


def watch_lines(nd: Dict) -> List[str]:
    out = []
    for d, s in nd.get("upcoming", []):
        out.append(f"{s} reports quarterly results; the session that reacts is {d:%a %d %b}. Prices often move more than usual around results.")
    if nd.get("kind") == "live" and nd.get("available"):
        a, b = nd["period"]
        close = (b - pd.Timedelta(seconds=1)).tz_convert(NY).normalize() + pd.Timedelta(hours=16)
        last = nd.get("last_received")
        if last is None:
            filled = nd.get("backfilled", 0)
            out.append("The news collector wasn't receiving stories live during this period"
                       + (f" ({filled} {'story was' if filled == 1 else 'stories were'} filled in afterwards)" if filled else "")
                       + ": check that its window is open (News page).")
        elif close - last > COLLECTOR_QUIET and last < b:
            out.append(f"The news collector's last story arrived at {_ny(last):%H:%M}, more than 2 hours before the close: "
                       "check that its window is open (News page).")
    elif nd.get("note") and nd.get("kind") == "live":
        out.append(nd["note"])
    return out


def chart_marks(nd: Dict, sym: str, df: pd.DataFrame) -> List[int]:
    """Bar positions of material stories on the symbol (for the price chart)."""
    if not nd.get("available") or df is None or df.empty:
        return []
    st = nd["stories"][nd["stories"].symbol == sym]
    pos = df.index.searchsorted(st.t_info, side="right") - 1
    return sorted({int(p) for p in pos if 0 <= p < len(df)})
