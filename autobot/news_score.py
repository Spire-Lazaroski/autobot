"""
Score news events per symbol: relevance, sentiment, surprise, category, materiality.

    python -m autobot.news_score --rules              # keyword scorer on every unscored event
    python -m autobot.news_score --claude --limit 50  # Claude on the 50 newest unscored events
    python -m autobot.news_score --estimate           # what scoring everything with Claude would cost
    python -m autobot.news_score --sample 40          # print a spread of scored headlines

Two scorers, stored side by side (table `scores`, one row per event x symbol x scorer):
  claude  Claude Haiku 4.5 with a fixed JSON schema (needs ANTHROPIC_API_KEY or an `ant auth` profile)
  rules   keyword/regex fallback, always available, deterministic
Each row records the scorer, model and prompt version, so re-scoring after a prompt change
is explicit and nothing is ever scored twice by accident.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import news

CLAUDE_MODEL = "claude-haiku-4-5"   # the small, fast model asked for
PROMPT_VERSION = "v2"  # v2: sentiment = expected price direction, sign must match the rationale
RULES_VERSION = "rules-v2"
CATEGORIES = ["earnings", "guidance", "m&a", "analyst_rating", "legal_regulatory", "macro", "other"]
RELEVANCE = ["main", "secondary", "mention"]
SURPRISE = ["expected", "unexpected", "unclear"]
PRICE_IN, PRICE_OUT = 1.00, 5.00    # $ per million tokens, Claude Haiku 4.5 (skill price table, 2026-09)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scores (
  event_id       INTEGER NOT NULL,
  symbol         TEXT NOT NULL,
  scorer         TEXT NOT NULL,          -- claude | rules
  version        TEXT NOT NULL,          -- prompt / rules version
  model          TEXT,
  relevance      TEXT NOT NULL,          -- main | secondary | mention
  sentiment      REAL NOT NULL,          -- -1 .. +1
  surprise       TEXT NOT NULL,          -- expected | unexpected | unclear
  category       TEXT NOT NULL,
  materiality    INTEGER NOT NULL,       -- 1 .. 5
  rationale      TEXT,
  scored_at      TEXT NOT NULL,
  PRIMARY KEY (event_id, symbol, scorer, version)
);
CREATE INDEX IF NOT EXISTS scores_symbol ON scores(symbol);
CREATE TABLE IF NOT EXISTS score_calls (    -- real token use per Claude call (one call per story)
  event_id INTEGER NOT NULL, version TEXT NOT NULL, model TEXT NOT NULL,
  input_tokens INTEGER, output_tokens INTEGER, at TEXT NOT NULL
);
"""

# ----------------------------------------------------------------- rules (fallback)
NAMES = {
    "AAPL": ["apple", "iphone", "tim cook", "app store", "mac", "ipad", "vision pro"],
    "NVDA": ["nvidia", "jensen huang", "blackwell", "hopper", "geforce", "cuda"],
    "TSLA": ["tesla", "elon musk", "musk", "cybertruck", "model y", "model 3", "robotaxi", "fsd"],
    "SPY": ["s&p 500", "s&p", "stock market", "wall street", "stocks", "fed", "fomc", "powell", "cpi", "pce", "inflation",
            "payrolls", "jobs report", "unemployment", "gdp", "treasury", "yields", "tariff", "recession"],
    "QQQ": ["nasdaq", "tech stocks", "technology stocks", "big tech", "magnificent seven", "mega-cap"],
}
CAT_RULES = [  # first match wins; macro before company categories so Fed / data / tariff news isn't mislabelled
    ("analyst_rating", r"\b(maintains|upgrades?|downgrades?|initiates|reiterates|price target|overweight|underweight|outperform|underperform)\b"),
    ("macro", r"\b(cpi|pce|ppi|fomc|fed|fed's|federal reserve|powell|rate (cut|hike)s?|interest rates?|payrolls|jobs report|unemployment|gdp|pmi|retail sales|jobless claims|treasury|yields?|inflation|tariffs?|recession)\b"),
    ("earnings", r"\b(earnings|eps|quarterly results|q[1-4] (results|revenue|sales)|revenue of|reports? q[1-4]|beats estimates|misses estimates)\b"),
    ("guidance", r"\b(guidance|outlook|(raises|lowers|cuts|reaffirms) (fy|full[- ]year|annual)|sees (fy|q[1-4]|full[- ]year))\b"),
    ("m&a", r"\b(acquires|acquisition|merger|buyout|takeover|agrees? to (buy|acquire)|deal to (buy|acquire)|to acquire|stake in)\b"),
    ("legal_regulatory", r"\b(lawsuit|sues|sued|probe|investigation|sec|ftc|doj|antitrust|regulators?|recall|fined|fines|court|ruling|settlement|ban(s|ned)?)\b"),
]
POS = r"\b(beats?|raises?|upgrades?|record|surges?|soars?|jumps?|rall(y|ies)|strong|approv(al|es|ed)|wins?|gains?|tops|exceeds?|outperform|buy rating|bullish|higher)\b"
NEG = r"\b(miss(es)?|cuts?|downgrades?|falls?|plunges?|slumps?|drops?|sinks?|probe|lawsuit|recall|weak|delays?|halts?|bearish|lower|underperform|sell rating|warns?|layoffs?|decline)\b"
BASE_MATERIALITY = {"earnings": 4, "guidance": 4, "m&a": 4, "legal_regulatory": 3, "macro": 3, "analyst_rating": 2, "other": 1}
EST = re.compile(r"(-?\d+(?:\.\d+)?)%?\s+vs\.?\s+(-?\d+(?:\.\d+)?)%?\s+est", re.I)


def rules_score(headline: str, summary: Optional[str], symbol: str, symbols: List[str]) -> Dict:
    text = f"{headline} {summary or ''}".lower()
    head = headline.lower()
    names = NAMES.get(symbol, []) + [symbol.lower()]
    in_head = any(re.search(rf"\b{re.escape(n)}\b", head) for n in names)
    in_text = any(re.search(rf"\b{re.escape(n)}\b", text) for n in names)
    relevance = "main" if in_head and len(symbols) <= 3 else "secondary" if in_head or in_text else "mention"
    category = next((c for c, rx in CAT_RULES if re.search(rx, head)), None) or \
        next((c for c, rx in CAT_RULES if re.search(rx, text)), "other")
    p, n = len(re.findall(POS, head)), len(re.findall(NEG, head))
    sentiment = 0.0 if p + n == 0 else round((p - n) / (p + n) * min(1.0, 0.5 + 0.25 * (p + n)), 2)
    surprise = "unclear"
    m = EST.search(headline)
    if m:  # data release: actual vs estimate
        act, est = float(m.group(1)), float(m.group(2))
        surprise = "expected" if abs(act - est) <= max(0.05 * abs(est), 0.05) else "unexpected"
    elif re.search(r"\b(unexpected(ly)?|surprise|shock|abrupt|sudden)\b", head):
        surprise = "unexpected"
    mat = BASE_MATERIALITY[category]
    if category == "macro" and symbol not in ("SPY", "QQQ"):
        mat -= 1
    mat += 1 if relevance == "main" and surprise == "unexpected" else 0
    mat -= 1 if relevance == "mention" else 0
    return {"relevance": relevance, "sentiment": sentiment, "surprise": surprise, "category": category,
            "materiality": max(1, min(5, mat)), "rationale": "keyword rules"}


# ----------------------------------------------------------------- Claude
SYSTEM = f"""You score financial news for a trading-research tool. For each requested ticker, judge the story ONLY as it concerns that ticker.

Fields:
- relevance: "main" (the ticker's company, or for SPY/QQQ the broad US market / Nasdaq, is the main subject), "secondary" (materially discussed but not the focus), "mention" (only named in passing or tagged).
- sentiment: the expected direction of the ticker's price because of this story, -1.0 (clearly down) to +1.0 (clearly up); 0 when neutral, mixed or unclear. For SPY/QQQ, risk-off news (military or geopolitical escalation, energy supply shocks, hawkish central-bank surprises, weak economic data, new trade barriers) is negative; the reverse is positive. The sign must agree with your rationale: if the rationale describes a risk or a headwind for the ticker, the sentiment must not be positive.
- surprise: "expected" (scheduled or widely anticipated, e.g. results in line with estimates), "unexpected" (new information the market likely did not anticipate), "unclear".
- category: one of {", ".join(CATEGORIES)}. analyst_rating = a broker's rating or price-target action. macro = economy, central bank, rates, inflation data, trade policy.
- materiality: 1 (noise) to 5 (likely to move the ticker's price by several percent). Most stories are 1-2; reserve 4-5 for earnings surprises, guidance changes, major deals, major legal/regulatory actions, and big macro surprises.
- rationale: one short sentence that states the direction (up / down / neutral) and why.
- If relevance is "mention", materiality is 1 and sentiment is 0 unless the text says something specific about the ticker.

Judge from the text given only; do not assume facts that are not in it. Return one entry per requested ticker."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "relevance": {"type": "string", "enum": RELEVANCE},
                    "sentiment": {"type": "number"},
                    "surprise": {"type": "string", "enum": SURPRISE},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "materiality": {"type": "integer"},
                    "rationale": {"type": "string"},
                },
                "required": ["symbol", "relevance", "sentiment", "surprise", "category", "materiality", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scores"],
    "additionalProperties": False,
}


def _user_prompt(ev: Dict, targets: List[str]) -> str:
    return (f"Published: {ev['created_at']}\nTagged tickers: {', '.join(json.loads(ev['symbols'])[:25])}\n"
            f"Headline: {ev['headline']}\nSummary: {(ev['summary'] or '').strip()[:1200] or '(none)'}\n\n"
            f"Score these tickers: {', '.join(targets)}")


def claude_client():
    import anthropic
    from dotenv import load_dotenv
    load_dotenv(news.ROOT / ".env")  # picks up a key added after the lab started
    return anthropic.Anthropic(max_retries=4, timeout=60.0)


def claude_score(client, ev: Dict, targets: List[str], usage: Optional[Dict] = None) -> List[Dict]:
    resp = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=1024, system=SYSTEM,
        messages=[{"role": "user", "content": _user_prompt(ev, targets)}],
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined to score this item")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("response cut off")
    if usage is not None and getattr(resp, "usage", None) is not None:
        usage.update(input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)
    data = json.loads(next(b.text for b in resp.content if b.type == "text"))
    out = []
    for s in data["scores"]:
        if s["symbol"].upper() not in targets:
            continue
        s["symbol"] = s["symbol"].upper()
        s["sentiment"] = max(-1.0, min(1.0, float(s["sentiment"])))
        s["materiality"] = max(1, min(5, int(s["materiality"])))
        out.append(s)
    return out


# ----------------------------------------------------------------- driver
def ensure_schema():
    with news.connect() as con:
        con.executescript(SCHEMA_SQL)


def _targets(ev_symbols: str) -> List[str]:
    ours = news.configured_symbols()
    return [s for s in json.loads(ev_symbols) if s in ours]


def unscored(scorer: str, version: str, limit: Optional[int] = None, newest_first=True) -> List[Dict]:
    ensure_schema()
    with news.connect() as con:
        q = ("SELECT e.* FROM events e WHERE NOT EXISTS (SELECT 1 FROM scores s WHERE s.event_id=e.id AND s.scorer=? AND s.version=?) "
             f"ORDER BY e.created_at {'DESC' if newest_first else 'ASC'}" + (f" LIMIT {int(limit)}" if limit else ""))
        return [dict(r) for r in con.execute(q, (scorer, version))]


def _row(event_id, r, scorer, version, model, now):
    return (event_id, r["symbol"], scorer, version, model, r["relevance"], r["sentiment"], r["surprise"], r["category"],
            r["materiality"], r.get("rationale"), now)


def save(event_id: int, rows: List[Dict], scorer: str, version: str, model: Optional[str]):
    save_many([(event_id, rows)], scorer, version, model)


def save_many(batch, scorer: str, version: str, model: Optional[str]):
    """One connection and one transaction for many events (the collector may be writing too)."""
    now = datetime.now(timezone.utc).isoformat()
    with news._lock, news.connect() as con:
        con.executescript(SCHEMA_SQL)
        con.executemany("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        [_row(eid, r, scorer, version, model, now) for eid, rows in batch for r in rows])


def run_rules(limit: Optional[int] = None, chunk: int = 2000) -> int:
    evs = unscored("rules", RULES_VERSION, limit)
    for k in range(0, len(evs), chunk):
        batch = []
        for ev in evs[k:k + chunk]:
            syms = json.loads(ev["symbols"])
            batch.append((ev["id"], [{"symbol": t, **rules_score(ev["headline"], ev["summary"], t, syms)} for t in _targets(ev["symbols"])]))
        save_many(batch, "rules", RULES_VERSION, None)
    return len(evs)


def run_claude(limit: int, progress=None, event_ids: Optional[List[int]] = None) -> Dict:
    """Score the newest `limit` unscored stories, or exactly `event_ids` (skipping ones already scored)."""
    client = claude_client()
    if event_ids is not None:
        ensure_schema()
        with news.connect() as con:
            marks = ",".join("?" * len(event_ids))
            evs = [dict(r) for r in con.execute(
                f"SELECT e.* FROM events e WHERE e.id IN ({marks}) AND NOT EXISTS (SELECT 1 FROM scores s WHERE s.event_id=e.id "
                "AND s.scorer='claude' AND s.version=?)", (*event_ids, PROMPT_VERSION))]
    else:
        evs = unscored("claude", PROMPT_VERSION, limit)
    tin = tout = 0
    done = failed = 0
    import anthropic
    for i, ev in enumerate(evs):
        targets = _targets(ev["symbols"])
        if not targets:
            continue
        try:
            use: Dict = {}
            save(ev["id"], claude_score(client, ev, targets, use), "claude", PROMPT_VERSION, CLAUDE_MODEL)
            if use:
                tin += use["input_tokens"]; tout += use["output_tokens"]
                with news._lock, news.connect() as con:
                    con.execute("INSERT INTO score_calls VALUES (?,?,?,?,?,?)", (ev["id"], PROMPT_VERSION, CLAUDE_MODEL,
                                use["input_tokens"], use["output_tokens"], datetime.now(timezone.utc).isoformat()))
            done += 1
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise RuntimeError(f"Anthropic account problem, stopping: {e.message}") from e
        except anthropic.BadRequestError as e:
            if "credit balance" in str(e.message).lower():  # account-wide: every further call would fail the same way
                raise RuntimeError("Anthropic credit balance is too low: add credits in the Console (Plans & Billing), "
                                   "then run again. Nothing was scored or charged.") from e
            failed += 1
            print(f"event {ev['id']}: {type(e).__name__}: {e.message}")
        except Exception as e:  # noqa: BLE001  one bad item must not stop the run; it stays unscored
            failed += 1
            print(f"event {ev['id']}: {type(e).__name__}: {e}")
        if progress:
            progress((i + 1) / len(evs), f"{i + 1}/{len(evs)}")
    cost = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT
    return {"scored": done, "failed": failed, "input_tokens": tin, "output_tokens": tout, "cost_usd": round(cost, 4)}


def stratified_sample(n: int = 100, seed: int = 7) -> List[int]:
    """Stories spread over symbols, categories (by the keyword scorer) and time, for checking a
    prompt before a full run. Mostly stories where a symbol is more than a passing mention."""
    import random
    rnd = random.Random(seed)
    ensure_schema()
    with news.connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT event_id, symbol, category, relevance FROM scores WHERE scorer='rules' AND version=?", (RULES_VERSION,))]
    cells: Dict = {}
    for r in rows:
        if r["relevance"] != "mention":
            cells.setdefault((r["symbol"], r["category"]), []).append(r["event_id"])
    picked: List[int] = []
    per = max(1, int(n * 0.85) // max(1, len(cells)))
    for key in sorted(cells):
        ids = cells[key]
        rnd.shuffle(ids)
        picked += [i for i in ids[:per] if i not in picked]
    rest = [r["event_id"] for r in rows if r["event_id"] not in set(picked)]
    rnd.shuffle(rest)
    picked += rest[: max(0, n - len(picked))]
    return picked[:n]


def estimate(n_events: Optional[int] = None) -> Dict:
    """Cost of scoring every unscored event with Claude. Uses the measured average tokens per
    story from earlier runs (score_calls) when there are any; otherwise characters / 4."""
    evs = unscored("claude", PROMPT_VERSION)
    if n_events is not None:
        evs = evs[:n_events]
    with news.connect() as con:
        m = con.execute("SELECT COUNT(*) n, AVG(input_tokens) i, AVG(output_tokens) o FROM score_calls").fetchone()
    if m["n"]:
        tin, tout, basis = m["i"] * len(evs), m["o"] * len(evs), f"measured on {m['n']} stories"
    else:
        sys_tok = len(SYSTEM) / 4 + 350
        tin = sum(sys_tok + len(_user_prompt(e, _targets(e["symbols"]))) / 4 for e in evs)
        tout = sum(40 + 70 * max(1, len(_targets(e["symbols"]))) for e in evs)
        basis = "rough, from text length"
    cost = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT
    return {"events": len(evs), "input_tokens": int(tin), "output_tokens": int(tout), "cost_usd": round(cost, 2),
            "cost_usd_batch": round(cost / 2, 2), "model": CLAUDE_MODEL, "basis": basis}


def sample(n: int = 40) -> List[Dict]:
    """A spread across categories (both scorers when available) for a sanity check."""
    ensure_schema()
    with news.connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT e.id, e.created_at, e.headline, s.symbol, s.scorer, s.relevance, s.sentiment, s.surprise, s.category, "
            "s.materiality, s.rationale FROM scores s JOIN events e ON e.id=s.event_id ORDER BY random()")]
    per = max(1, n // len(CATEGORIES))
    out, seen = [], {}
    for r in rows:
        if seen.get(r["category"], 0) < per:
            seen[r["category"]] = seen.get(r["category"], 0) + 1
            out.append(r)
    return out[:n]


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # headlines contain any Unicode
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", action="store_true")
    ap.add_argument("--claude", action="store_true")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--sample", type=int)
    a = ap.parse_args()
    if a.rules:
        print("rules scored events:", run_rules())
    if a.claude:
        print(run_claude(a.limit))
    if a.estimate:
        print(json.dumps(estimate(), indent=1))
    if a.sample:
        for r in sample(a.sample):
            print(f"[{r['scorer']}] {r['symbol']:4} {r['category']:16} rel={r['relevance']:9} sent={r['sentiment']:+.2f} "
                  f"surp={r['surprise']:10} mat={r['materiality']} | {r['headline'][:90]}")


if __name__ == "__main__":
    main()
