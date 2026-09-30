"""
News store (data/news.db, SQLite) and collection helpers.

Times are kept exactly as given, as UTC ISO strings:
  created_at   when the story was published (Alpaca / Benzinga)
  updated_at   when it was last edited
  received_at  when WE first got it (stream or poll). Backfilled items have none: they
               are marked origin='backfill' and the event study adds a fixed delay instead.

An item that arrives again with a later updated_at is an edit: the row is updated in place,
`n_updates` goes up and `last_update_received_at` is set; it is never stored twice.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "news.db"
DEFAULT_SYMBOLS = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id            INTEGER PRIMARY KEY,      -- Alpaca news id
  headline      TEXT NOT NULL,
  summary       TEXT,
  author        TEXT,
  source        TEXT,
  url           TEXT,
  symbols       TEXT NOT NULL,            -- JSON list
  created_at    TEXT NOT NULL,
  updated_at    TEXT,
  received_at   TEXT,                     -- NULL for backfill
  origin        TEXT NOT NULL,            -- stream | poll | backfill
  hash          TEXT NOT NULL,            -- normalised headline + symbols
  n_updates     INTEGER NOT NULL DEFAULT 0,
  last_update_received_at TEXT,
  event_id      INTEGER                   -- set by step 2 (story collapsing)
);
CREATE INDEX IF NOT EXISTS items_created ON items(created_at);
CREATE INDEX IF NOT EXISTS items_hash ON items(hash);
CREATE TABLE IF NOT EXISTS item_symbols (
  item_id INTEGER NOT NULL, symbol TEXT NOT NULL, PRIMARY KEY (item_id, symbol)
);
CREATE INDEX IF NOT EXISTS item_symbols_sym ON item_symbols(symbol);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  first_item_id INTEGER NOT NULL,
  headline      TEXT NOT NULL,            -- of the first copy
  summary       TEXT,
  symbols       TEXT NOT NULL,            -- JSON, union over the copies
  created_at    TEXT NOT NULL,            -- earliest publication
  received_at   TEXT,                     -- earliest time WE received a copy (NULL if all backfilled)
  n_items       INTEGER NOT NULL DEFAULT 1,
  n_sources     INTEGER NOT NULL DEFAULT 1,
  sources       TEXT NOT NULL DEFAULT '[]',
  n_updates     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_created ON events(created_at);
"""

_lock = threading.Lock()


def utc_iso(t) -> Optional[str]:
    if t is None:
        return None
    if isinstance(t, str):
        t = datetime.fromisoformat(t.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm_headline(h: str) -> str:
    """Lower case, no URLs or punctuation; keeps decimals ("52.9") and signs of figures."""
    h = h.lower()
    h = re.sub(r"https?://\S+", " ", h)
    h = re.sub(r"-(?=\d)", "minus", h)            # "-3" and "3" are different figures
    h = re.sub(r"[^a-z0-9%$. ]+", " ", h)
    h = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", h)   # keep only decimal points
    return re.sub(r"\s+", " ", h).strip()


def story_hash(headline: str, symbols: Iterable[str]) -> str:
    key = norm_headline(headline) + "|" + ",".join(sorted(s.upper() for s in symbols))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


@contextmanager
def connect(path: Path = DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")  # the collector writes while the API reads
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _field(n, k, default=None):
    return n.get(k, default) if isinstance(n, dict) else getattr(n, k, default)


def store(items: Iterable, origin: str, received_at: Optional[str] = None, path: Path = DB_PATH) -> Dict[str, int]:
    """Insert new items; apply edits (later updated_at) in place. Returns counts."""
    counts = {"new": 0, "updated": 0, "same": 0}
    with _lock, connect(path) as con:
        for n in items:
            nid = int(_field(n, "id"))
            syms = [s.strip().upper() for s in (_field(n, "symbols") or []) if s and s.strip()]
            headline = _field(n, "headline") or ""
            upd = utc_iso(_field(n, "updated_at"))
            row = con.execute("SELECT updated_at FROM items WHERE id=?", (nid,)).fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO items (id, headline, summary, author, source, url, symbols, created_at, updated_at, "
                    "received_at, origin, hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (nid, headline, _field(n, "summary"), _field(n, "author"), _field(n, "source"), _field(n, "url"),
                     json.dumps(syms), utc_iso(_field(n, "created_at")), upd,
                     received_at if origin != "backfill" else None, origin, story_hash(headline, syms)))
                con.executemany("INSERT OR IGNORE INTO item_symbols VALUES (?,?)", [(nid, s) for s in syms])
                counts["new"] += 1
            elif upd and (row["updated_at"] is None or upd > row["updated_at"]):
                con.execute(
                    "UPDATE items SET headline=?, summary=?, symbols=?, updated_at=?, hash=?, n_updates=n_updates+1, "
                    "last_update_received_at=? WHERE id=?",
                    (headline, _field(n, "summary"), json.dumps(syms), upd, story_hash(headline, syms),
                     received_at or now_iso(), nid))
                con.executemany("INSERT OR IGNORE INTO item_symbols VALUES (?,?)", [(nid, s) for s in syms])
                counts["updated"] += 1
            else:
                counts["same"] += 1
    return counts


def meta_get(key: str, path: Path = DB_PATH) -> Optional[str]:
    with connect(path) as con:
        r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None


def meta_set(key: str, value: str, path: Path = DB_PATH):
    with _lock, connect(path) as con:
        con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))


def configured_symbols() -> List[str]:
    try:
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        return [s.upper() for s in (cfg.get("news") or {}).get("symbols") or DEFAULT_SYMBOLS]
    except OSError:
        return DEFAULT_SYMBOLS


def stats(path: Path = DB_PATH) -> Dict:
    with connect(path) as con:
        tot = con.execute("SELECT COUNT(*) c, MIN(created_at) a, MAX(created_at) b FROM items").fetchone()
        by = {r["origin"]: r["c"] for r in con.execute("SELECT origin, COUNT(*) c FROM items GROUP BY origin")}
        ours = configured_symbols()
        sym = {r["symbol"]: r["c"] for r in con.execute(
            f"SELECT symbol, COUNT(*) c FROM item_symbols WHERE symbol IN ({','.join('?' * len(ours))}) GROUP BY symbol", ours)}
        upd = con.execute("SELECT COUNT(*) c FROM items WHERE n_updates > 0").fetchone()["c"]
        last = con.execute("SELECT MAX(received_at) r FROM items").fetchone()["r"]
    return {"items": tot["c"], "first": tot["a"], "last": tot["b"], "by_origin": by, "by_symbol": sym,
            "edited": upd, "last_received": last}


# ----------------------------------------------------------------- backfill (REST)
def backfill(symbols: List[str], start: datetime, end: datetime, client=None, progress=None, path: Path = DB_PATH) -> Dict[str, int]:
    """Download history month by month (the client pages through each month)."""
    from alpaca.data.requests import NewsRequest
    if client is None:
        from alpaca.data.historical.news import NewsClient

        from .data import _keys, with_timeout
        client = with_timeout(NewsClient(*_keys()))
    total = {"new": 0, "updated": 0, "same": 0}
    t = start
    months = max(1, int((end - start).days / 30) + 1)
    k = 0
    while t < end:
        u = min(t + timedelta(days=31), end)
        r = client.get_news(NewsRequest(symbols=",".join(symbols), start=t, end=u, limit=None, sort="asc"))
        items = r.data.get("news", []) if hasattr(r, "data") else r.get("news", [])
        c = store(items, "backfill", path=path)
        for key in total:
            total[key] += c[key]
        k += 1
        if progress:
            progress(k / months, f"{t:%Y-%m}: {len(items)} items")
        t = u
    meta_set("backfill_to", utc_iso(end), path)
    return total


# ----------------------------------------------------------------- step 2: one event per story
COLLAPSE_WINDOW = timedelta(hours=6)
SIMILAR = 0.75  # share of meaningful headline words in common (template headlines need a high bar)
STOP = set("a an the and or of to in on for at by with from as is are was were be it its this that than into "
           "after over up down new says said report reports amid vs via".split())


RATING = re.compile(r"^(.*?)\s+(maintains|upgrades|downgrades|initiates|reiterates|resumes|assumes|reinstates)\b", re.I)


def rating_firm(headline: str) -> Optional[str]:
    """'Goldman Sachs Maintains Buy on NVIDIA...' -> 'goldman sachs' (analyst actions)."""
    m = RATING.match(headline.strip())
    return norm_headline(m.group(1)) if m else None


def same_story(h1: str, t1: frozenset, h2: str, t2: frozenset) -> float:
    """Similarity of two headlines; two different analysts' actions are never one story."""
    f1, f2 = rating_firm(h1), rating_firm(h2)
    if f1 and f2 and f1 != f2:
        return 0.0
    return similarity(t1, t2)


def tokens(headline: str) -> frozenset:
    return frozenset(w for w in norm_headline(headline).split()
                     if w not in STOP and (len(w) > 1 or any(c.isdigit() for c in w)))


def similarity(a: frozenset, b: frozenset) -> float:
    """Word overlap, but headlines that carry figures (data releases, prices, percentages)
    must carry the same figures: "Core PCE 3.0%" and "PCE 2.8%" are different news."""
    if not (a and b):
        return 0.0
    na, nb = {w for w in a if any(c.isdigit() for c in w)}, {w for w in b if any(c.isdigit() for c in w)}
    if na and nb and na != nb:
        return 0.0
    return len(a & b) / len(a | b)


def collapse_events(path: Path = DB_PATH) -> Dict[str, int]:
    """Give every item without an event one: an existing event of the same story (same hash,
    or a similar headline on a shared symbol within 6 h), or a new event. Incremental."""
    ours = set(configured_symbols())
    made = joined = 0
    with _lock, connect(path) as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM items WHERE event_id IS NULL ORDER BY created_at, id")]
        if not rows:
            return {"new_events": 0, "joined": 0}
        t0 = (datetime.fromisoformat(rows[0]["created_at"]) - COLLAPSE_WINDOW).isoformat()
        recent = []  # open events within the window: dict(id, created, syms, toks, hashes)
        for e in con.execute("SELECT e.*, (SELECT GROUP_CONCAT(hash) FROM items WHERE event_id=e.id) hs FROM events e "
                             "WHERE created_at >= ? ORDER BY created_at", (t0,)):
            recent.append({"id": e["id"], "created": e["created_at"], "syms": set(json.loads(e["symbols"])) & ours or set(json.loads(e["symbols"])),
                           "head": e["headline"], "toks": tokens(e["headline"]), "hashes": set((e["hs"] or "").split(","))})
        for r in rows:
            created = datetime.fromisoformat(r["created_at"])
            lo = (created - COLLAPSE_WINDOW).isoformat()
            recent = [e for e in recent if e["created"] >= lo]
            syms_all = set(json.loads(r["symbols"]))
            syms = syms_all & ours or syms_all
            toks = tokens(r["headline"])
            best, score = None, 0.0
            for e in recent:
                if not (e["syms"] & syms):
                    continue
                sc = 1.0 if r["hash"] in e["hashes"] else same_story(r["headline"], toks, e["head"], e["toks"])
                if sc >= SIMILAR and sc > score:
                    best, score = e, sc
            if best is None:
                cur = con.execute(
                    "INSERT INTO events (first_item_id, headline, summary, symbols, created_at, received_at, n_items, n_sources, sources, n_updates) "
                    "VALUES (?,?,?,?,?,?,1,1,?,?)",
                    (r["id"], r["headline"], r["summary"], json.dumps(sorted(syms_all)), r["created_at"], r["received_at"],
                     json.dumps([r["source"] or ""]), r["n_updates"]))
                eid = cur.lastrowid
                recent.append({"id": eid, "created": r["created_at"], "syms": syms, "head": r["headline"], "toks": toks, "hashes": {r["hash"]}})
                made += 1
            else:
                eid = best["id"]
                e = dict(con.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone())
                srcs = sorted(set(json.loads(e["sources"])) | {r["source"] or ""})
                rec = min(x for x in (e["received_at"], r["received_at"]) if x) if (e["received_at"] or r["received_at"]) else None
                con.execute("UPDATE events SET symbols=?, received_at=?, n_items=n_items+1, n_sources=?, sources=?, "
                            "n_updates=n_updates+? WHERE id=?",
                            (json.dumps(sorted(set(json.loads(e["symbols"])) | syms_all)), rec, len(srcs), json.dumps(srcs),
                             r["n_updates"], eid))
                best["hashes"].add(r["hash"])
                best["syms"] |= syms
                joined += 1
            con.execute("UPDATE items SET event_id=? WHERE id=?", (eid, r["id"]))
    return {"new_events": made, "joined": joined}
