"""
Shadow-mode AI judge: for every buy / sell signal of a trading job, ask Claude "take or skip, and
why?" using only what was known at that moment, and write the answer to logs/llm_judge.jsonl.
It NEVER changes an order: it runs as its own process, reading the bot's decision log.

    python -m autobot.llm_judge --estimate 2026-10-05        # build that day's questions, estimate the cost (no API call)
    python -m autobot.llm_judge --estimate 2026-10-05 --sample 3   # really ask about 3 of them (costs money)
    python -m autobot.llm_judge --watch                      # shadow mode: follow logs/decisions.jsonl live

Config (top level of config.yaml; --watch refuses to run unless enabled, and stops after `until`):
    llm_judge: {enabled: false, models: [claude-opus-5-5, claude-haiku-4-5], effort: low,
                max_calls_per_day: 100, until: 2026-10-08}
Every signal is put to every listed model (same question), so the models can be compared like
for like: `python -m autobot.llm_judge --compare` (agreement, and the P&L of the trades each
model would have skipped). Effort is sent only to models that accept it (not Haiku 4.5).

What the judge sees (all from the decision time or before): the job, strategy and its own
reason, the proposed order, today's session so far (open / high / low / last, the last 24
five-minute closes), how many entries this symbol already had today, the open position's entry
and P&L when it is an exit, minutes to the close, and the symbol's scored news headlines of the
last 24 hours. Historical days (--estimate) cut bars and news at the decision time, so no later
information leaks in. The model's general knowledge of past markets is the reason this can only
be judged FORWARD (weeks of shadow records), never by a backtest.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
OUT = LOGS / "llm_judge.jsonl"
NY = "America/New_York"
DEFAULTS = {"enabled": False, "models": ["claude-opus-5-5"], "effort": "low", "max_calls_per_day": 100, "until": None}
NO_EFFORT = ("claude-haiku-4-5",)    # effort is rejected on Haiku 4.5
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-4-5": (1.0, 5.0)}  # $ per 1M in / out
PROMPT_VERSION = 1

SYSTEM = """You review signals from a paper-trading bot, one at a time, and say whether you would take each one.

The bot trades US stocks and ETFs on 5-minute bars and closes every position before the close. Its own two-year studies found:
- a typical 5-minute trade gains about 1 basis point before costs, while a round trip costs about 4 to 5 basis points;
- most quick flips in and out within the same hour lose; same-session re-entries soon after an exit lose more often than not;
- news-based filters showed no measurable edge so far.
So the default answer is SKIP unless the situation clearly offers more than a typical signal: a strong, clean move, a fresh catalyst, a sensible exit that protects a gain or limits a loss.

Exits: a SELL that closes a long (or a BUY that closes a short) only removes risk. Say "take" for an exit unless holding on looks clearly better.

Use only the information given. It ends at the decision time; do not use any knowledge of what markets did afterwards.
Answer in the JSON format requested: verdict take or skip, confidence between 0 and 1, and one plain-English sentence of reason."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "confidence", "reason"],
          "properties": {"verdict": {"type": "string", "enum": ["take", "skip"]},
                         "confidence": {"type": "number"},
                         "reason": {"type": "string"}}}


def settings(cfg: Optional[Dict]) -> Dict:
    raw = (cfg or {}).get("llm_judge") or {}
    s = {**DEFAULTS, **raw}
    if raw.get("model") and "models" not in raw:
        s["models"] = [raw["model"]]
    return s


# ----------------------------------------------------------------- which records are signals
def is_signal(rec: Dict) -> bool:
    return (rec.get("type") == "decision" and rec.get("action") in ("buy", "sell") and rec.get("job") != "core"
            and float(rec.get("qty") or 0) > 0)


def decisions(path: Path = LOGS / "decisions.jsonl") -> List[Dict]:
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _t(rec: Dict) -> pd.Timestamp:
    return pd.Timestamp(rec.get("time") or rec["bar_time"]).tz_convert("UTC")


# ----------------------------------------------------------------- the question
def context(rec: Dict, earlier: List[Dict], bars: pd.DataFrame, news: Optional[pd.DataFrame]) -> str:
    """The user message for one signal. `earlier`: decision records before this one (same day),
    `bars`: 5-minute bars of the symbol, cut here at the decision time; `news`: scored stories."""
    t = _t(rec)
    ny = t.tz_convert(NY)
    sym = rec["symbol"]
    b = bars[bars.index + pd.Timedelta(minutes=5) <= t]          # only bars that had ENDED by the decision
    m = b.index.tz_convert(NY)
    mins = m.hour * 60 + m.minute
    b = b[(mins >= 570) & (mins < 960)]                          # regular hours, like the bot
    today = b[b.index.tz_convert(NY).date == ny.date()]
    close_at = pd.Timestamp(ny.date()).tz_localize(NY) + pd.Timedelta(hours=16)
    L = [f"Decision time: {ny:%a %Y-%m-%d %H:%M} New York ({int((close_at - ny).total_seconds() // 60)} minutes to the close)",
         f"Symbol: {sym}. Job {rec['job']}, strategy {rec.get('strategy')}"
         + (f" (sub-strategy in charge: {rec['active']})" if rec.get("active") and rec.get("active") != rec.get("strategy") else ""),
         f"Proposed order: {rec['action'].upper()} {rec.get('qty'):g} shares at about {float(b.close.iloc[-1]) if len(b) else float(rec.get('price') or 0):.2f} "
         f"(position {rec.get('position_before', 0):g} -> {rec.get('target', 0):g})",
         f"The strategy's own reason: {rec.get('reason', 'not recorded')}"]
    if rec.get("regime"):
        L.append(f"Market regime label: {rec['regime']}")
    if len(today):
        o, last = float(today.open.iloc[0]), float(today.close.iloc[-1])
        L.append(f"Today so far: open {o:.2f}, high {today.high.max():.2f}, low {today.low.min():.2f}, last {last:.2f} "
                 f"({(last / o - 1) * 100:+.2f}% from the open)")
        tail = today.tail(24)
        L.append("Last 5-minute closes (time close): " + ", ".join(f"{i.tz_convert(NY):%H:%M} {c:.2f}" for i, c in tail.close.items()))
    same = [e for e in earlier if e.get("symbol") == sym and is_signal(e) and _t(e).tz_convert(NY).date() == ny.date()]
    if same:
        L.append(f"Earlier orders on {sym} today: " + "; ".join(f"{_t(e).tz_convert(NY):%H:%M} {e['action']} {e.get('qty'):g}" for e in same[-8:]))
    else:
        L.append(f"No earlier orders on {sym} today.")
    if rec["action"] == "sell" and float(rec.get("position_before") or 0) > 0 or rec["action"] == "buy" and float(rec.get("position_before") or 0) < 0:
        buys = [e for e in same if e["action"] == ("buy" if float(rec.get("position_before")) > 0 else "sell")]
        if buys:
            ep = float(buys[-1].get("fill_price") or buys[-1].get("price") or 0)
            if ep:
                now = float(b.close.iloc[-1])
                L.append(f"This order closes a position opened at about {ep:.2f}; now {now:.2f} ({(now / ep - 1) * 100 * (1 if float(rec.get('position_before')) > 0 else -1):+.2f}%)")
    if news is not None and len(news):
        n = news[(news.t_info <= t) & (news.t_info > t - pd.Timedelta(hours=24)) & (news.materiality >= 3)].tail(5)
        if len(n):
            L.append("Scored news on this symbol in the last 24 hours (materiality 1-5):")
            L += [f"- {r.t_info.tz_convert(NY):%a %H:%M} [{int(r.materiality)}] {str(r.headline)[:140]}" for r in n.itertuples()]
        else:
            L.append("No material news on this symbol in the last 24 hours.")
    L.append("Would you take this order? Answer verdict, confidence and a one-sentence reason.")
    return "\n".join(L)


def load_bars(sym: str, t: pd.Timestamp) -> pd.DataFrame:
    from .data import _download
    return _download(sym, "5Min", (t - pd.Timedelta(days=4)).to_pydatetime(), None, "iex")


def load_news(sym: str) -> Optional[pd.DataFrame]:
    try:
        from . import news_guard
        s = news_guard.stories(sym)
        return s if len(s) else None
    except Exception:  # noqa: BLE001  the judge works without news
        return None


def questions(day: str, recs: Optional[List[Dict]] = None) -> Iterator[Dict]:
    recs = recs if recs is not None else decisions()
    bars_cache: Dict[str, pd.DataFrame] = {}
    for k, r in enumerate(recs):
        if not is_signal(r) or _t(r).tz_convert(NY).date().isoformat() != day:
            continue
        sym = r["symbol"]
        if sym not in bars_cache:
            bars_cache[sym] = load_bars(sym, _t(r) + pd.Timedelta(hours=8))
        yield {"rec": r, "prompt": context(r, recs[:k], bars_cache[sym], load_news(sym))}


# ----------------------------------------------------------------- asking Claude
def client():
    import anthropic
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    return anthropic.Anthropic(max_retries=3, timeout=120.0)


def ask(c, prompt: str, model: str, effort: str) -> Dict:
    oc = {"format": {"type": "json_schema", "schema": SCHEMA}}
    if model not in NO_EFFORT:
        oc["effort"] = effort
    resp = c.messages.create(model=model, max_tokens=4000, system=SYSTEM,
                             messages=[{"role": "user", "content": prompt}], output_config=oc)
    if resp.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"no verdict ({resp.stop_reason})")
    data = json.loads(next(b.text for b in resp.content if b.type == "text"))
    pin, pout = PRICES.get(model, (0, 0))
    u = resp.usage
    return {**data, "model": model, "effort": effort if model not in NO_EFFORT else None, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
            "cost_usd": round((u.input_tokens * pin + u.output_tokens * pout) / 1e6, 5), "request_id": resp._request_id}


def record(q: Dict, verdict: Dict) -> Dict:
    r = q["rec"]
    out = {"time": datetime.now(timezone.utc).isoformat(), "prompt_version": PROMPT_VERSION, "decision_time": str(_t(r)),
           "job": r["job"], "symbol": r["symbol"], "strategy": r.get("strategy"), "action": r["action"], "qty": r.get("qty"),
           "price": r.get("fill_price") or r.get("price"), "order_ids": r.get("order_ids"), **verdict}
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(out) + "\n")
    return out


def estimate(day: str, out_tokens: int = 400) -> Dict:
    """Cost of judging every signal of `day`, from the real questions (about 4 characters per
    token; thinking at effort low is folded into the output allowance). No API call."""
    qs = list(questions(day))
    sys_tok = len(SYSTEM) / 4
    toks = [sys_tok + len(q["prompt"]) / 4 for q in qs]
    res = {"day": day, "signals": len(qs), "avg_input_tokens": round(sum(toks) / max(1, len(toks))), "assumed_output_tokens": out_tokens,
           "per_model": {}}
    for m, (pin, pout) in PRICES.items():
        per = (res["avg_input_tokens"] * pin + out_tokens * pout) / 1e6
        res["per_model"][m] = {"per_signal_usd": round(per, 4), "per_day_usd": round(per * len(qs), 3),
                               "per_month_usd_21_days": round(per * len(qs) * 21, 2)}
    res["example_question"] = qs[0]["prompt"] if qs else None
    return res


def watch(cfg: Dict, poll_s: float = 10.0):
    """Shadow mode: judge each new signal as the bot writes it. Never touches orders."""
    s = settings(cfg)
    if not s["enabled"]:
        raise SystemExit("llm_judge is not enabled in config.yaml (llm_judge: enabled: true).")
    c = client()
    path = LOGS / "decisions.jsonl"
    pos = path.stat().st_size if path.exists() else 0      # only signals from now on
    calls = {"day": None, "n": 0}
    while True:
        time.sleep(poll_s)
        if not path.exists() or path.stat().st_size <= pos:
            continue
        with open(path, encoding="utf-8") as f:
            f.seek(pos)
            chunk = f.read()
            pos = f.tell()
        recs = decisions()
        if s.get("until") and pd.Timestamp.now(tz=NY).date().isoformat() > str(s["until"]):
            print("llm_judge: past its end date", s["until"], "- stopping", flush=True)
            return
        for line in chunk.splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not is_signal(r):
                continue
            day = _t(r).tz_convert(NY).date().isoformat()
            if calls["day"] != day:
                calls = {"day": day, "n": 0}
            if calls["n"] >= int(s["max_calls_per_day"]):
                continue
            calls["n"] += 1
            k = next((i for i, x in enumerate(recs) if x == r), len(recs))
            try:
                q = {"rec": r, "prompt": context(r, recs[:k], load_bars(r["symbol"], _t(r)), load_news(r["symbol"]))}
            except Exception as e:  # noqa: BLE001  no data for the question: logged, never fatal
                print("llm_judge: could not build the question:", e, flush=True)
                continue
            for model in s["models"]:      # the same question to every model
                try:
                    print(json.dumps(record(q, {**ask(c, q["prompt"], model, s["effort"]), "mode": "shadow"})), flush=True)
                except Exception as e:  # noqa: BLE001  a failed call is logged, never fatal
                    record(q, {"verdict": None, "error": str(e), "model": model, "mode": "shadow"})


# ----------------------------------------------------------------- compare the models afterwards
_PX: Dict = {}


def session_prices(sym: str, day: str) -> Dict[str, Optional[float]]:
    """The session's opening price and the close-out price (the close of the bar ending 15:55,
    when the bot sells everything) from 5-minute bars."""
    key = (sym, day)
    if key not in _PX:
        t = pd.Timestamp(day).tz_localize(NY) + pd.Timedelta(hours=17)
        b = load_bars(sym, t.tz_convert("UTC"))
        ny = b.index.tz_convert(NY)
        m = ny.hour * 60 + ny.minute
        d = b[(ny.date == pd.Timestamp(day).date()) & (m >= 570) & (m < 960)]
        dm = d.index.tz_convert(NY).hour * 60 + d.index.tz_convert(NY).minute
        last = d[dm <= 950]                       # bars that end by 15:55
        _PX[key] = {"open": float(d.open.iloc[0]) if len(d) else None, "closeout": float(last.close.iloc[-1]) if len(last) else None}
    return _PX[key]


def outcomes(recs: List[Dict]) -> Dict[str, Dict]:
    """Per signal (keyed by its first order id), what it was worth, from actual fills:
    entry -> the round trip's result in % (fill to the fill that closed it);
    exit  -> what holding on instead would have made, in %, until the 15:55 close-out.
    Each also carries the dollars (qty x price) so results can be summed in $."""
    out: Dict[str, Dict] = {}
    open_: Dict[str, Dict] = {}
    for r in recs:
        if r.get("type") != "decision" or r.get("job") == "core":
            continue
        sym = r.get("symbol")
        px = float(r.get("fill_price") or r.get("price") or 0)
        before, after = float(r.get("position_before") or 0), float(r.get("target") or 0)
        if not px or r.get("action") not in ("buy", "sell", "flatten before close"):
            continue
        ids = r.get("order_ids") or []
        if before == 0 and after != 0 and ids and is_signal(r):
            open_[sym] = {"id": ids[0], "px": px, "side": 1 if after > 0 else -1, "qty": abs(after)}
        elif before != 0 and after == 0:
            o = open_.pop(sym, None)
            if o:
                pct = (px / o["px"] - 1) * 100 * o["side"]
                out[o["id"]] = {"kind": "entry", "pct": round(pct, 4), "usd": round(pct / 100 * o["px"] * o["qty"], 2),
                                "symbol": sym, "job": r.get("job"), "day": _t(r).tz_convert(NY).date().isoformat()}
            if is_signal(r) and ids:            # a strategy exit (not the close-out): what would holding have done?
                day = _t(r).tz_convert(NY).date().isoformat()
                co = session_prices(sym, day)["closeout"]
                if co:
                    side = 1 if before > 0 else -1
                    pct = (co / px - 1) * 100 * side
                    out[ids[0]] = {"kind": "exit", "pct": round(pct, 4), "usd": round(pct / 100 * px * abs(before), 2),
                                   "symbol": sym, "job": r.get("job"), "day": day}
    return out


def trade_outcomes(recs: List[Dict]) -> Dict[str, float]:
    """Entry round-trip results in % by order id (kept for older callers)."""
    return {k: v["pct"] for k, v in outcomes(recs).items() if v["kind"] == "entry"}


def broker_fills(day: str) -> List[Dict]:
    """Every filled order of a New York day from Alpaca (read-only; includes manual stops)."""
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    from .data import _keys, with_timeout
    k, sec = _keys()
    tc = with_timeout(TradingClient(k, sec, paper=True))
    t0 = pd.Timestamp(day).tz_localize(NY).tz_convert("UTC")
    orders = tc.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, after=t0.to_pydatetime(),
                                            until=(t0 + pd.Timedelta(days=1)).to_pydatetime(), limit=500))
    return [{"symbol": o.symbol, "side": o.side.value, "qty": float(o.filled_qty), "px": float(o.filled_avg_price),
             "t": str(o.filled_at)} for o in orders if o.filled_at and float(o.filled_qty or 0) > 0]


def jobs_vs_hold(recs: List[Dict], days: List[str], fills=None) -> List[Dict]:
    """Per job and day, from the broker's actual fills: the strategy's dollars (realized, plus any
    position left open valued at the 15:55 close-out) against simply buying at the open and
    holding to the close-out with the largest position the job held that day."""
    job_of = {r["symbol"]: r["job"] for r in recs if r.get("type") == "decision" and r.get("job") and r.get("job") != "core"}
    fills = fills or broker_fills
    rows = []
    for day in days:
        try:
            fl = fills(day)
        except Exception as e:  # noqa: BLE001
            rows.append({"day": day, "error": f"no broker fills: {e}"})
            continue
        for sym in sorted({f["symbol"] for f in fl if f["symbol"] in job_of}):
            ff = sorted((f for f in fl if f["symbol"] == sym), key=lambda f: f["t"])
            cash, pos, size = 0.0, 0.0, 0.0
            for f in ff:
                q = f["qty"] if f["side"] == "buy" else -f["qty"]
                cash -= q * f["px"]
                pos += q
                size = max(size, abs(pos) * f["px"])
            sp = session_prices(sym, day)
            if pos and sp["closeout"]:
                cash += pos * sp["closeout"]
            hold = size / sp["open"] * (sp["closeout"] - sp["open"]) if sp["open"] and sp["closeout"] and size else None
            rows.append({"day": day, "job": job_of[sym], "symbol": sym, "fills": len(ff), "strategy_usd": round(cash, 2),
                         "hold_usd": round(hold, 2) if hold is not None else None})
    return rows


def compare(path: Path = OUT, modes=("shadow", "replay")) -> Dict:
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    rows = [r for r in rows if r.get("mode") in modes and r.get("verdict")]
    recs = decisions()
    val = outcomes(recs)
    by_sig: Dict[str, Dict] = {}
    for r in rows:
        key = (r.get("order_ids") or [r["decision_time"]])[0]
        by_sig.setdefault(key, {"action": r["action"], "symbol": r["symbol"], "day": r["decision_time"][:10]})[r["model"]] = r["verdict"]
    models = sorted({r["model"] for r in rows})
    days = sorted({v["day"] for v in by_sig.values()})
    res = {"days": days, "signals": len(by_sig), "models": {}}
    for m in models:
        mine = {k: v[m] for k, v in by_sig.items() if m in v}
        ent = {k: v for k, v in mine.items() if k in val and val[k]["kind"] == "entry"}
        ex = {k: v for k, v in mine.items() if k in val and val[k]["kind"] == "exit"}
        sk_ent = [val[k] for k, v in ent.items() if v == "skip"]
        tk_ent = [val[k] for k, v in ent.items() if v == "take"]
        sk_ex = [val[k] for k, v in ex.items() if v == "skip"]
        rm = [r for r in rows if r["model"] == m]
        gain_usd = -sum(x["usd"] for x in sk_ent) + sum(x["usd"] for x in sk_ex)   # vs the bot taking everything
        res["models"][m] = {
            "judged": len(mine), "skip_%": round(100 * sum(v == "skip" for v in mine.values()) / max(1, len(mine)), 1),
            "entries": {"scored": len(ent), "taken": len(tk_ent), "skipped": len(sk_ent),
                        "avg_%_taken": round(sum(x["pct"] for x in tk_ent) / len(tk_ent), 4) if tk_ent else None,
                        "avg_%_skipped": round(sum(x["pct"] for x in sk_ent) / len(sk_ent), 4) if sk_ent else None,
                        "skipped_that_lost": sum(x["pct"] < 0 for x in sk_ent)},
            "exits": {"scored": len(ex), "skipped": len(sk_ex),
                      "avg_%_if_held_instead": round(sum(x["pct"] for x in sk_ex) / len(sk_ex), 4) if sk_ex else None},
            "usd_vs_bot": round(gain_usd, 2),
            "cost_usd": round(sum(r.get("cost_usd") or 0 for r in rm), 3)}
    if len(models) >= 2:
        a, b = models[:2]
        both = [v for v in by_sig.values() if a in v and b in v]
        res["agreement_%"] = round(100 * sum(v[a] == v[b] for v in both) / max(1, len(both)), 1)
        res["disagreements"] = [{"day": v["day"], "symbol": v["symbol"], "action": v["action"], a: v[a], b: v[b]}
                                for v in both if v[a] != v[b]][:30]
    res["bot_entries_usd"] = round(sum(x["usd"] for x in val.values() if x["kind"] == "entry" and x["day"] in days), 2)
    res["jobs_vs_hold"] = jobs_vs_hold(recs, days)
    return res


def replay(days: List[str], models: List[str], effort: str) -> int:
    """Ask every model about every signal of past days (questions cut at each decision time).
    Both models' training data ends before these days, so they can't know what happened."""
    c = client()
    done = set()
    if OUT.exists():
        for x in OUT.read_text(encoding="utf-8").splitlines():
            r = json.loads(x)
            if r.get("mode") == "replay" and r.get("verdict"):
                done.add(((r.get("order_ids") or [""])[0], r["model"]))
    n = 0
    for day in days:
        for q in questions(day):
            oid = (q["rec"].get("order_ids") or [""])[0]
            for m in models:
                if (oid, m) in done:
                    continue
                try:
                    record(q, {**ask(c, q["prompt"], m, effort), "mode": "replay"})
                except Exception as e:  # noqa: BLE001
                    record(q, {"verdict": None, "error": str(e), "model": m, "mode": "replay"})
                n += 1
    return n


def main():
    import argparse
    import sys
    import yaml
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Shadow-mode AI judge (never changes an order).")
    ap.add_argument("--estimate", metavar="YYYY-MM-DD")
    ap.add_argument("--sample", type=int, default=0, help="with --estimate: really ask about the first N signals (costs money)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--compare", action="store_true", help="compare the models' shadow verdicts with what the trades did")
    ap.add_argument("--replay", nargs="+", metavar="YYYY-MM-DD", help="ask every model about every signal of these past days")
    a = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    s = settings(cfg)
    if a.watch:
        watch(cfg)
        return
    if a.compare:
        print(json.dumps(compare(), indent=1))
        return
    if a.replay:
        print("asked", replay(a.replay, s["models"], s["effort"]), "questions")
        return
    if a.estimate:
        est = estimate(a.estimate)
        print(json.dumps({k: v for k, v in est.items() if k != "example_question"}, indent=1))
        print("\nExample question:\n" + (est["example_question"] or "(no signals that day)"))
        if a.sample:
            c = client()
            for q in list(questions(a.estimate))[:a.sample]:
                print(json.dumps(record(q, {**ask(c, q["prompt"], a.model or s["models"][0], s["effort"]), "mode": "sample"}), indent=1))


if __name__ == "__main__":
    main()
