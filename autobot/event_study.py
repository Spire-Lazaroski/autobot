"""
Event study: do stocks react to news, and do big moves WITHOUT news behave differently?

    python -m autobot.event_study            # compute and save data/event_study.json

For every scored story on one of our symbols:
  entry   = the first 5-minute bar that STARTS after we received the story (backfilled stories
            have no receive time: publish time + BACKFILL_DELAY). No look-ahead.
  return  = abnormal return: the stock minus SPY over the same interval (SPY itself: raw).
  horizons: +5m, +15m, +1h (inside the session), end of day, +1 day, +3 days (session closes).
Stories outside market hours are entered at the next open and kept in their own bucket.

Big 15-minute moves: |3-bar abnormal return| > 2 x its standard deviation over the previous
20 sessions (past only). "No-news" when no story on the symbol appeared from 60 minutes before
the move to its end. Measured from the next bar, signed in the direction of the move, so
continuation is positive and reversal negative.

Everything is summarised as mean +- 95% confidence interval with the event count; groups with
fewer than MIN_EVENTS are flagged.
"""
from __future__ import annotations

import json
import math
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import news, news_score
from .data import fetch_bars

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "event_study.json"
HORIZONS = ["5m", "15m", "1h", "eod", "1d", "3d"]
INTRA_BARS = {"5m": 1, "15m": 3, "1h": 12}
BACKFILL_DELAY = timedelta(seconds=60)
SPIKE_SIGMA, SPIKE_BARS, SIGMA_SESSIONS, NEWS_LOOKBACK = 2.0, 3, 20, timedelta(minutes=60)
MIN_EVENTS = 30


# ----------------------------------------------------------------- price panel
class Panel:
    """5-minute RTH bars for one symbol, aligned to SPY, with session bookkeeping."""

    def __init__(self, df: pd.DataFrame, spy: pd.DataFrame, is_spy: bool):
        self.t = df.index.as_unit("ns").asi8
        self.open = df.open.to_numpy(float)
        self.close = df.close.to_numpy(float)
        ny = df.index.tz_convert("America/New_York")
        self.day = np.asarray(ny.normalize().asi8)
        # SPY price at the same bar times (as-of), so returns are measured over identical intervals
        sp = spy.reindex(df.index, method="ffill")
        self.spy_open, self.spy_close = sp.open.to_numpy(float), sp.close.to_numpy(float)
        self.is_spy = is_spy
        days, first = np.unique(self.day, return_index=True)
        self.days = days
        self.day_first = first
        self.day_last = np.r_[first[1:] - 1, len(self.t) - 1]
        self.day_of = np.searchsorted(days, self.day)
        self.step = int(np.median(np.diff(self.t[: 200]))) if len(self.t) > 2 else 300 * 10**9

    def entry(self, t_ns: int) -> Optional[int]:
        """First bar that starts at or after t (strictly after the information arrived)."""
        i = int(np.searchsorted(self.t, t_ns, side="left"))
        return i if i < len(self.t) else None

    def ar(self, i0: int, i1: int) -> float:
        """Abnormal return from the OPEN of bar i0 to the CLOSE of bar i1: the stock minus SPY.
        For SPY itself this is the raw return; its normal drift is removed later (baseline)."""
        r = self.close[i1] / self.open[i0] - 1
        if self.is_spy:
            return float(r)
        return float(r - (self.spy_close[i1] / self.spy_open[i0] - 1))

    def baseline(self, every: int = 7) -> Dict[str, float]:
        """Average return at each horizon from ordinary bars (every n-th bar, no news selection):
        what 'no reaction' looks like, including the market's drift."""
        vals: Dict[str, List[float]] = {h: [] for h in HORIZONS}
        for i0 in range(0, len(self.t) - 1, every):
            h = self.horizons(i0, with_peak=False)
            for k in HORIZONS:
                if h[k] is not None:
                    vals[k].append(h[k])
        self.abs_base = {k: float(np.mean(np.abs(v))) if v else float("nan") for k, v in vals.items()}
        return {k: float(np.mean(v)) if v else 0.0 for k, v in vals.items()}

    def horizons(self, i0: int, with_peak: bool = True) -> Dict[str, Optional[float]]:
        d = self.day_of[i0]
        last = self.day_last[d]
        out: Dict[str, Optional[float]] = {}
        for h, k in INTRA_BARS.items():
            j = i0 + k - 1
            out[h] = self.ar(i0, j) if j <= last else None  # never crosses the close
        out["eod"] = self.ar(i0, last)
        for h, n in (("1d", 1), ("3d", 3)):
            out[h] = self.ar(i0, self.day_last[d + n]) if d + n < len(self.days) else None
        if not with_peak:
            return out
        # overshoot: the peak move in the first hour vs where it ends up
        j_end = min(i0 + 11, last)
        path = np.array([self.ar(i0, j) for j in range(i0, j_end + 1)])
        k = int(np.argmax(np.abs(path)))
        out["peak_1h"] = float(path[k])
        return out

    def outside_hours(self, t_ns: int, i0: int) -> bool:
        """True when the story arrived outside the session that contains the entry bar."""
        return t_ns < self.t[self.day_first[self.day_of[i0]]]


def load_panels(symbols: List[str], days: int = 740) -> Dict[str, Panel]:
    data = fetch_bars(sorted(set(symbols) | {"SPY"}), "5Min", days, rth_only=True)
    spy = data["SPY"]
    return {s: Panel(data[s], spy, s == "SPY") for s in symbols}


# ----------------------------------------------------------------- news reactions
def news_events(scorer: str = "rules", with_first: bool = True) -> pd.DataFrame:
    """One row per (story, symbol) score of the chosen scorer, with its receive time."""
    version = news_score.RULES_VERSION if scorer == "rules" else news_score.PROMPT_VERSION
    news_score.ensure_schema()
    with news.connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT e.id event_id, e.created_at, e.received_at, e.headline, s.symbol, s.relevance, s.sentiment, s.surprise, "
            "s.category, s.materiality, s.story_type FROM scores s JOIN events e ON e.id = s.event_id "
            "WHERE s.scorer = ? AND s.version = ?", (scorer, version))]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    created = pd.to_datetime(df.created_at, utc=True, format="ISO8601")
    recv = pd.to_datetime(df.received_at, utc=True, format="ISO8601")
    df["t_info"] = recv.where(recv.notna(), created + BACKFILL_DELAY)
    df["received_live"] = recv.notna()
    if with_first:
        main = df[df.relevance != "mention"]
        flags = news_score.first_story_flags(list(zip(main.event_id, main.symbol)), version=version)
        df["first_story"] = [flags.get((e, s), {}).get("first") for e, s in zip(df.event_id, df.symbol)]
    return df


def measure_news(ev: pd.DataFrame, panels: Dict[str, Panel]) -> pd.DataFrame:
    out = []
    for r in ev.itertuples(index=False):
        p = panels.get(r.symbol)
        if p is None:
            continue
        t_ns = pd.Timestamp(r.t_info).value
        if t_ns < p.t[0] or t_ns > p.t[-1]:
            continue
        i0 = p.entry(t_ns)
        if i0 is None:
            continue
        h = p.horizons(i0)
        out.append({**r._asdict(), **h, "entry_time": int(p.t[i0] // 10**9), "outside_hours": p.outside_hours(t_ns, i0)})
    return pd.DataFrame(out)


# ----------------------------------------------------------------- big moves with and without news
def spikes(panels: Dict[str, Panel], news_times: Dict[str, np.ndarray]) -> pd.DataFrame:
    out = []
    for sym, p in panels.items():
        if p.is_spy:
            continue  # SPY is the benchmark: its abnormal return is itself
        n = len(p.t)
        # 3-bar abnormal move ending at bar t (open of t-2 to close of t), same session only
        s = np.full(n, np.nan)
        for t in range(SPIKE_BARS - 1, n):
            i0 = t - SPIKE_BARS + 1
            if p.day[i0] == p.day[t]:
                s[t] = p.ar(i0, t)
        ser = pd.Series(s)
        per = SIGMA_SESSIONS * 78
        sigma = ser.rolling(per, min_periods=per // 2).std().shift(1)  # past only
        nt = news_times.get(sym, np.array([], dtype="int64"))
        t = 0
        while t < n - 1:
            sg = sigma.iloc[t]
            if not np.isnan(s[t]) and not np.isnan(sg) and abs(s[t]) > SPIKE_SIGMA * sg:
                start = p.t[t - SPIKE_BARS + 1]
                end = p.t[t] + p.step
                lo = start - int(NEWS_LOOKBACK.total_seconds() * 10**9)
                k = np.searchsorted(nt, lo, side="left")
                had_news = k < len(nt) and nt[k] <= end
                i0 = t + 1
                if p.day_of[i0] == p.day_of[t]:  # the next bar is still in the same session
                    d = 1.0 if s[t] > 0 else -1.0
                    h = p.horizons(i0)
                    out.append({"symbol": sym, "time": int(p.t[t] // 10**9), "move": float(s[t]), "sigma": float(sg),
                                "direction": d, "news": bool(had_news),
                                **{k2: (None if v is None else d * v) for k2, v in h.items()}})
                t += SPIKE_BARS  # don't count the same move twice
                continue
            t += 1
    return pd.DataFrame(out)


# ----------------------------------------------------------------- summaries
def summarise(df: pd.DataFrame, by: Optional[str], sign_by: Optional[str] = None) -> List[Dict]:
    """Mean +- 95% CI (in %) per horizon, per group. sign_by multiplies by the sign of a column
    (e.g. sentiment) so 'moved the way the news pointed' is positive.

    Stories cluster (one earnings day produces dozens of headlines about the same move), so they
    are not independent: each symbol-day counts once. Stories are averaged within a symbol-day
    and the interval is computed across those days. `n` = stories, `days` = symbol-days;
    the low-data flag uses days."""
    if df.empty:
        return []
    df = df.copy()
    tcol = "entry_time" if "entry_time" in df else "time"
    df["_cluster"] = df.symbol.astype(str) + "|" + pd.to_datetime(df[tcol], unit="s", utc=True).dt.tz_convert("America/New_York").dt.date.astype(str)
    groups = [("all", df)] if by is None else [(str(k), g) for k, g in df.groupby(by, dropna=True)]
    out = []
    for name, g in groups:
        row = {"group": name, "n": int(len(g)), "days": int(g._cluster.nunique()), "horizons": {}}
        for h in HORIZONS + ["peak_1h"]:
            if h not in g:
                continue
            v = pd.to_numeric(g[h], errors="coerce")
            if sign_by:
                v = v * np.sign(g[sign_by]).replace(0, np.nan)
            per_day = (v * 100).groupby(g._cluster).mean().dropna()
            n = len(per_day)
            m = float(per_day.mean()) if n else None
            se = float(per_day.std(ddof=1) / math.sqrt(n)) if n > 1 else None
            row["horizons"][h] = {"n": n, "mean": m, "lo": None if se is None else m - 1.96 * se,
                                  "hi": None if se is None else m + 1.96 * se}
        row["low"] = row["days"] < MIN_EVENTS
        out.append(row)
    return out


def continue_vs_revert(sp: pd.DataFrame) -> List[Dict]:
    """For big moves (signed by direction): share that kept going vs gave back by the close / next day."""
    out = []
    for label, g in (("with news", sp[sp.news]), ("no news", sp[~sp.news])):
        row = {"group": label, "n": int(len(g))}
        for h in ("eod", "1d"):
            v = pd.to_numeric(g[h], errors="coerce").dropna()
            row[f"continued_{h}"] = float((v > 0).mean() * 100) if len(v) else None
            row[f"reverted_{h}"] = float((v < 0).mean() * 100) if len(v) else None
        row["low"] = row["n"] < MIN_EVENTS
        out.append(row)
    return out


def volatility(df: pd.DataFrame, by: str, abs_base: Dict[str, Dict[str, float]], raw: pd.DataFrame) -> List[Dict]:
    """Size of the move after news vs the symbol's normal move size over the same horizon
    (ratio of mean absolute returns, averaged per symbol-day). >1 = news days move more."""
    if df.empty:
        return []
    out = []
    df = df.assign(_day=df.symbol.astype(str) + "|" + pd.to_datetime(df.entry_time, unit="s", utc=True).dt.tz_convert("America/New_York").dt.date.astype(str))
    for name, g in df.groupby(by, dropna=True):
        row = {"group": str(name), "n": int(len(g)), "days": int(g._day.nunique()), "ratio": {}}
        for h in ("15m", "1h", "eod", "1d"):
            r = raw.loc[g.index, h].astype(float).abs() / g.symbol.map(lambda x: abs_base[x][h])
            per_day = r.groupby(g._day).mean().dropna()
            n = len(per_day)
            m = float(per_day.mean()) if n else None
            se = float(per_day.std(ddof=1) / math.sqrt(n)) if n > 1 else None
            row["ratio"][h] = {"mean": m, "lo": None if se is None else m - 1.96 * se, "hi": None if se is None else m + 1.96 * se}
        row["low"] = row["days"] < MIN_EVENTS
        out.append(row)
    return out


def sentiment_bucket(x: float) -> str:
    return "negative" if x < -0.2 else "positive" if x > 0.2 else "neutral"


def run(scorer: str = "rules", progress=None) -> Dict:
    symbols = news.configured_symbols()
    if progress:
        progress(0.05, "loading prices")
    panels = load_panels(symbols)
    if progress:
        progress(0.2, "reading scored news + timing check")
    ev = news_events(scorer)
    if progress:
        progress(0.55, f"measuring {len(ev)} news reactions")
    m = measure_news(ev, panels)
    if not m.empty:
        m["sentiment_bucket"] = m.sentiment.map(sentiment_bucket)
        m["when"] = np.where(m.outside_hours, "outside hours", "during the session")
        m["first"] = m.first_story.map({True: "first story", False: "follow-up"})
    if progress:
        progress(0.75, "finding big moves with and without news")
    with news.connect() as con:
        nt = {s: np.array(sorted(pd.to_datetime([r[0] for r in con.execute(
            "SELECT e.created_at FROM events e JOIN item_symbols i ON i.item_id = e.first_item_id WHERE i.symbol = ?", (s,))],
            utc=True, format="ISO8601").as_unit("ns").asi8), dtype="int64") for s in symbols}
    sp = spikes(panels, nt)
    main_all = m[(m.relevance != "mention")] if not m.empty else m
    # SPY: raw returns minus its normal drift at each horizon; kept apart from the stocks
    spy_base = panels["SPY"].baseline()
    spy = main_all[main_all.symbol == "SPY"].copy()
    for h in HORIZONS:
        spy[h] = pd.to_numeric(spy[h], errors="coerce") - spy_base[h]
    # stocks: the return against SPY also has a normal level (e.g. NVDA beat SPY over these years);
    # remove each symbol's own normal return at each horizon so only the reaction remains
    stock_base = {sym: panels[sym].baseline() for sym in panels if sym != "SPY"}
    main = main_all[main_all.symbol != "SPY"].copy()
    raw = main[HORIZONS].copy()  # before removing the normal level: move sizes use raw returns vs SPY
    abs_base = {sym: panels[sym].abs_base for sym in stock_base}
    for h in HORIZONS:
        main[h] = pd.to_numeric(main[h], errors="coerce") - main.symbol.map(lambda x: stock_base[x][h])
    live = main[~main.outside_hours] if not main.empty else main
    spy_live = spy[~spy.outside_hours] if not spy.empty else spy
    res = {
        "scorer": scorer,
        "version": news_score.RULES_VERSION if scorer == "rules" else news_score.PROMPT_VERSION,
        "generated": pd.Timestamp.now(tz="UTC").isoformat(),
        "period": [str(pd.Timestamp(panels["SPY"].t[0], tz="UTC").date()), str(pd.Timestamp(panels["SPY"].t[-1], tz="UTC").date())],
        "counts": {"scores": int(len(ev)), "measured": int(len(m)), "stock_scores_main_or_secondary": int(len(main)),
                   "spy_scores_main_or_secondary": int(len(spy)),
                   "spikes": int(len(sp)), "spikes_no_news": int((~sp.news).sum()) if len(sp) else 0},
        "min_events": MIN_EVENTS,
        "horizons": HORIZONS,
        "news": {
            # raw abnormal return (not signed): does the stock move on its own news, on average?
            "by_category": summarise(live, "category"),
            "by_materiality": summarise(live, "materiality"),
            "by_story_type": summarise(live, "story_type"),
            "by_first": summarise(live, "first"),
            "by_when": summarise(main, "when"),
            # signed by sentiment: did it move the way the news pointed?
            "by_sentiment_signed": summarise(live[live.sentiment_bucket != "neutral"] if len(live) else live, "sentiment_bucket", sign_by="sentiment"),
            "by_category_signed": summarise(live[live.sentiment_bucket != "neutral"] if len(live) else live, "category", sign_by="sentiment"),
        },
        "volatility": {  # stocks: move size after news / normal move size (during the session)
            "by_materiality": volatility(live, "materiality", abs_base, raw),
            "by_category": volatility(live, "category", abs_base, raw),
            "by_first": volatility(live, "first", abs_base, raw),
        },
        "market": {  # SPY news, return minus SPY's normal drift over the same horizon
            "baseline_pct": {h: v * 100 for h, v in spy_base.items()},
            "stock_baseline_pct": {sym: {h: v * 100 for h, v in b.items()} for sym, b in stock_base.items()},
            "by_category": summarise(spy_live, "category"),
            "by_sentiment_signed": summarise(spy_live[spy_live.sentiment_bucket != "neutral"] if len(spy_live) else spy_live,
                                             "sentiment_bucket", sign_by="sentiment"),
        },
        "spikes": {
            "by_news": summarise(sp.assign(kind=np.where(sp.news, "with news", "no news")), "kind") if len(sp) else [],
            "by_symbol_no_news": summarise(sp[~sp.news], "symbol") if len(sp) else [],
            "continue_vs_revert": continue_vs_revert(sp) if len(sp) else [],
        },
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    if progress:
        progress(1.0, "done")
    return res


def main():
    import argparse
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--scorer", default="rules", choices=["rules", "claude"])
    a = ap.parse_args()
    r = run(a.scorer, progress=lambda f, m: print(f"{f * 100:3.0f}% {m}"))
    print(json.dumps(r["counts"], indent=1))


if __name__ == "__main__":
    main()
