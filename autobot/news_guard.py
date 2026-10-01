"""
news_guard: wrap any strategy with two news rules found useful in the Phase 5 event study
(news days move 10-36% more than normal, with no predictable direction):

  stand-aside        after a material story on the symbol, take no NEW position for N minutes
                     (mode "no_new": exits and holds are allowed) or be flat ("flat")
  earnings blackout  no new positions from `blackout_before` sessions before an earnings
                     release session to `blackout_after` sessions from it (after-close releases
                     move the NEXT session, so before=1 also covers the release day's close)

Causal: the decision at a bar's close uses only stories received up to that close.
Earnings sessions come from the news history (days with >= 3 earnings/guidance stories on
the symbol); companies announce these dates weeks ahead, so knowing them in advance is fair.
Dates listed in config.yaml `news.earnings_dates` replace the detector's for that symbol up to
the last listed date (the detector can miss a release or be a day off); after it, the detector's
dates are used. Live, only past releases are known from news unless upcoming ones are listed.

Registered as a strategy (family "news"), so the Lab, Search, Walk-forward, reports and the
live bot use it unchanged. The bars must carry their symbol in df.attrs["symbol"].
"""
from __future__ import annotations

import threading
import time
from datetime import date
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from . import news

_cache: Dict[str, Dict] = {}
_lock = threading.Lock()
CACHE_SECONDS = 60
EARNINGS_MIN_STORIES = 3


def _story_rows(symbol: str) -> pd.DataFrame:
    """Scored stories on the symbol (main or secondary subject): receive time, materiality,
    category, story type, headline. Claude's newest prompt where scored, else keyword rules."""
    from . import news_score as ns
    ns.ensure_schema()
    q = f"""
      SELECT e.id, e.created_at, e.received_at, e.headline,
             COALESCE(c.materiality, r.materiality) materiality, COALESCE(c.category, r.category) category,
             COALESCE(c.story_type, r.story_type) story_type, COALESCE(c.relevance, r.relevance) relevance
      FROM events e JOIN item_symbols i ON i.item_id = e.first_item_id AND i.symbol = ?
      LEFT JOIN scores c ON c.event_id=e.id AND c.symbol=? AND c.scorer='claude' AND c.version=?
      LEFT JOIN scores r ON r.event_id=e.id AND r.symbol=? AND r.scorer='rules' AND r.version=?
      WHERE COALESCE(c.relevance, r.relevance) IN ('main', 'secondary')"""
    with news.connect() as con:
        df = pd.read_sql(q, con, params=(symbol, symbol, ns.PROMPT_VERSION, symbol, ns.RULES_VERSION))
    if df.empty:
        return df
    created = pd.to_datetime(df.created_at, utc=True, format="ISO8601")
    recv = pd.to_datetime(df.received_at, utc=True, format="ISO8601")
    from .event_study import BACKFILL_DELAY
    df["t_info"] = recv.where(recv.notna(), created + BACKFILL_DELAY)
    return df.sort_values("t_info").reset_index(drop=True)


def stories(symbol: str) -> pd.DataFrame:
    """Cached per process for a minute (the live bot calls this every bar)."""
    with _lock:
        hit = _cache.get(symbol)
        if hit and time.time() - hit["at"] < CACHE_SECONDS:
            return hit["df"]
    try:
        df = _story_rows(symbol)
    except Exception:  # noqa: BLE001  no news database: the guard sees no news (and says so via notes)
        df = pd.DataFrame()
    with _lock:
        _cache[symbol] = {"at": time.time(), "df": df}
    return df


def earnings_sessions(symbol: str) -> List[date]:
    """New York dates whose session reacts to an earnings release: the dates listed in config
    news.earnings_dates, then the news detector's dates after the last listed one."""
    out = detected_sessions(symbol)
    listed: List[date] = []
    try:
        import yaml
        cfg = yaml.safe_load((news.ROOT / "config.yaml").read_text(encoding="utf-8"))
        for d in ((cfg.get("news") or {}).get("earnings_dates") or {}).get(symbol, []):
            listed.append(date.fromisoformat(str(d)))
    except (OSError, ValueError):
        pass
    if listed:  # listed (published) dates win: the detector only fills in after the last one
        out = [d for d in out if d > max(listed)] + listed
    return sorted(set(out))


def detected_sessions(symbol: str) -> List[date]:
    """Earnings sessions found from the news alone: days with >= 3 earnings/guidance stories on
    the symbol (days within 4 of each other merged). A release after the close counts for the
    next session."""
    df = stories(symbol)
    out: List[date] = []
    if not df.empty:
        e = df[df.category.isin(["earnings", "guidance"]) & (df.relevance == "main")
               & df.story_type.isin(["new_information", "data_release"])]
        if len(e):
            ny = e.t_info.dt.tz_convert("America/New_York")
            # after 16:00 the next session is the one that reacts
            sess = [(t + pd.Timedelta(days=1)).date() if t.hour >= 16 else t.date() for t in ny]
            counts = pd.Series(1, index=sess).groupby(level=0).sum()
            days = sorted((d, n) for d, n in counts.items() if n >= EARNINGS_MIN_STORIES)
            clusters: List[List] = []
            for d, n in days:  # days within 4 of each other are one release (previews, the next morning)
                if clusters and (d - clusters[-1][-1][0]).days <= 4:
                    clusters[-1].append((d, n))
                else:
                    clusters.append([(d, n)])
            out = [max(c, key=lambda x: (x[1], x[0]))[0] for c in clusters]  # the busiest day is the release
    return out


def guard_flags(df: pd.DataFrame, symbol: str, stand_aside_min: int, min_materiality: int,
                blackout_before: int, blackout_after: int) -> pd.DataFrame:
    """Per bar: stand_aside (a material story arrived in the last N minutes, up to this bar's
    close), blackout (earnings window) and a note naming the story."""
    n = len(df)
    step = pd.Timedelta(minutes=ind.step_minutes(df))
    close_t = (df.index + step).as_unit("ns").asi8  # the decision is made at the bar's close
    aside = np.zeros(n, bool)
    note = np.array([None] * n, dtype=object)
    s = stories(symbol)
    if stand_aside_min > 0 and not s.empty:
        m = s[s.materiality >= min_materiality]
        if len(m):
            nt = m.t_info.dt.tz_convert("UTC").astype("datetime64[ns, UTC]").values.astype("int64")
            win = int(pd.Timedelta(minutes=stand_aside_min).value)
            hi = np.searchsorted(nt, close_t, side="right")          # stories received up to the close
            lo = np.searchsorted(nt, close_t - win, side="right")     # ... within the last N minutes
            aside = hi > lo
            heads = m.headline.to_numpy()
            mats = m.materiality.to_numpy()
            for i in np.flatnonzero(aside):
                k = hi[i] - 1
                note[i] = f"materiality {mats[k]} story: {heads[k][:90]}"
    black = np.zeros(n, bool)
    if (blackout_before > 0 or blackout_after > 0) and ind.is_intraday(df):
        sess = np.array(sorted(set(ind.session_day(df))))
        blocked_days = set()
        for d in earnings_sessions(symbol):
            k = int(np.searchsorted(sess, d))  # the first session on/after the reacting date
            for j in range(k - blackout_before, k + blackout_after):
                if 0 <= j < len(sess):
                    blocked_days.add(sess[j])
        day = ind.session_day(df)
        black = np.array([d in blocked_days for d in day])
        for i in np.flatnonzero(black & ~aside):
            note[i] = "earnings blackout"
    return pd.DataFrame({"stand_aside": aside, "blackout": black, "note": note}, index=df.index)


def apply_guard(sig: pd.Series, blocked: np.ndarray, mode: str) -> pd.Series:
    """flat: no position while blocked. no_new: while blocked, positions may be kept or closed
    but not opened, enlarged or flipped."""
    s = sig.to_numpy(float).copy()
    if mode == "flat":
        s[blocked] = 0.0
        return pd.Series(s, index=sig.index)
    out = np.empty_like(s)
    prev = 0.0
    for i, want in enumerate(s):
        if blocked[i]:
            if prev == 0 or np.sign(want) != np.sign(prev):
                want = 0.0                                   # can't open; a flip becomes an exit
            else:
                want = float(np.sign(prev)) * min(abs(want), abs(prev))  # can't enlarge
        out[i] = want
        prev = want
    return pd.Series(out, index=sig.index)


DEFAULTS = {"base": "ema_cross", "base_params": {}, "stand_aside_min": 30, "min_materiality": 3, "mode": "no_new",
            "blackout_before": 1, "blackout_after": 1}


def _parts(df, p, allow_short):
    from .strategies import STRATEGIES, get_signal
    if p["base"] not in STRATEGIES or p["base"] == "news_guard":
        raise ValueError(f"news_guard needs a base strategy other than itself, got '{p['base']}'")
    sig = get_signal(p["base"], df, p.get("base_params") or {}, allow_short)
    symbol = df.attrs.get("symbol")
    if not symbol:
        return sig, pd.DataFrame({"stand_aside": False, "blackout": False, "note": None}, index=df.index)
    flags = guard_flags(df, symbol, int(p["stand_aside_min"]), int(p["min_materiality"]),
                        int(p["blackout_before"]), int(p["blackout_after"]))
    return sig, flags


def news_guard(df, allow_short=False, **params):
    p = {**DEFAULTS, **params}
    sig, flags = _parts(df, p, allow_short)
    blocked = (flags.stand_aside | flags.blackout).to_numpy()
    return apply_guard(sig, blocked, p["mode"])


def explain(df, params, allow_short=False):
    p = {**DEFAULTS, **params}
    _, flags = _parts(df, p, allow_short)
    names = np.where(flags.stand_aside, "stand aside (news)", np.where(flags.blackout, "earnings blackout", p["base"]))
    return {"names": names.tolist(), "notes": flags.note.tolist(),
            "news_available": bool(df.attrs.get("symbol"))}


@ind.plot("news_guard")
def _(df, p):
    p = {**DEFAULTS, **p}
    _, flags = _parts(df, p, False)
    base = ind.plot_indicators(p["base"], df, {**_base_defaults(p["base"]), **(p.get("base_params") or {})})
    guard = ind._pane("news guard", [ind._line("stand aside", flags.stand_aside.astype(float), ind.COL["down"]),
                                     ind._line("earnings blackout", flags.blackout.astype(float) * 0.5, ind.COL["secondary"])], [], [0, 1])
    return {"overlays": base["overlays"], "panes": base["panes"] + [guard]}


def _base_defaults(name):
    from .strategies import STRATEGIES
    return STRATEGIES[name].defaults if name in STRATEGIES else {}


def register():
    from .strategies import STRATEGIES, StrategySpec
    STRATEGIES["news_guard"] = StrategySpec(
        "news_guard", news_guard, "news",
        "Any strategy plus two news rules: stand aside for N minutes after a material story, and "
        "an earnings blackout around each company's results.",
        DEFAULTS, {"stand_aside_min": [0, 30, 60, 120], "min_materiality": [3, 4], "blackout_before": [0, 1], "blackout_after": [0, 1]},
        warmup_days=0, explain=explain, source="Phase 5 event study (this project)")
