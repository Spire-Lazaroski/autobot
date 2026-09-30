"""
News collector: runs alongside the bot and stores every news item for our symbols.

    python news_collector.py                # backfill what's missing, then collect live
    python news_collector.py --backfill-only
    python news_collector.py --days 730     # how far back the first backfill goes

Live items come from Alpaca's news websocket (received_at = the moment we got it). A REST
poll every 60 s catches anything missed during a reconnect (origin 'poll', received_at =
poll time, so the delay is honest). logs/news_collector.pid holds a heartbeat; a second
collector refuses to start while one is alive. It never trades and never touches the bot.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autobot import news
from autobot.liveconfig import pid_alive

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
PID_FILE = LOGS / "news_collector.pid"
POLL_SECONDS = 60
log = logging.getLogger("news")
state = {"started": time.time(), "stream_items": 0, "poll_new": 0, "last_item_at": None, "connected": False}


def collector_state() -> dict:
    """For the API: running = pid alive and heartbeat fresh."""
    try:
        d = json.loads(PID_FILE.read_text())
    except (OSError, ValueError):
        return {"running": False}
    age = time.time() - float(d.get("heartbeat", 0))
    alive = pid_alive(int(d.get("pid", 0)))
    return {**d, "running": alive and age < 90, "heartbeat_age_s": round(age, 1), "process_alive": alive}


def heartbeat():
    PID_FILE.parent.mkdir(exist_ok=True)
    tmp = PID_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": os.getpid(), "heartbeat": time.time(), **state}))
    for _ in range(10):
        try:
            os.replace(tmp, PID_FILE)
            return
        except PermissionError:  # the API is reading it (Windows)
            time.sleep(0.05)


def heartbeat_loop():
    while True:
        try:
            heartbeat()
        except Exception as e:  # noqa: BLE001
            log.error("heartbeat failed: %s", e)
        time.sleep(5)


def catch_up(symbols, days: int):
    """Backfill from the last stored point (or `days` ago) to now."""
    last = news.meta_get("backfill_to")
    start = datetime.fromisoformat(last) - timedelta(hours=1) if last else datetime.now(timezone.utc) - timedelta(days=days)
    end = datetime.now(timezone.utc)
    log.info("backfill %s .. %s for %s", start.date(), end, ",".join(symbols))
    c = news.backfill(symbols, start, end, progress=lambda f, m: log.info("backfill %3.0f%% %s", f * 100, m))
    log.info("backfill done: %s; stories: %s", c, news.collapse_events())


def poll_loop(symbols):
    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest

    from autobot.data import _keys, with_timeout
    client = with_timeout(NewsClient(*_keys()))
    while True:
        time.sleep(POLL_SECONDS)
        try:
            end = datetime.now(timezone.utc)
            r = client.get_news(NewsRequest(symbols=",".join(symbols), start=end - timedelta(minutes=15), end=end, limit=None))
            c = news.store(r.data.get("news", []), "poll", received_at=news.now_iso())
            news.collapse_events()
            state["poll_new"] += c["new"]
            if c["new"]:
                log.warning("poll found %d item(s) the stream missed", c["new"])
        except Exception as e:  # noqa: BLE001
            log.error("poll failed: %s", e)


async def on_news(n):
    received = news.now_iso()
    c = news.store([n], "stream", received_at=received)
    news.collapse_events()
    state["stream_items"] += 1
    state["last_item_at"] = received
    log.info("NEWS %s %s %s (%s)", n.created_at, ",".join(n.symbols or []), n.headline[:90],
             "new" if c["new"] else "edit" if c["updated"] else "dup")


def run_stream(symbols):
    from alpaca.data.live.news import NewsDataStream

    from autobot.data import _keys
    stream = NewsDataStream(*_keys())
    stream.subscribe_news(on_news, *symbols)
    state["connected"] = True
    stream.run()  # reconnects by itself


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--backfill-only", action="store_true")
    a = ap.parse_args()
    LOGS.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(LOGS / "news.log", encoding="utf-8"), logging.StreamHandler()])
    st = collector_state()
    if st.get("running") and not a.backfill_only:
        log.error("A news collector is already running (pid %s). Not starting another.", st.get("pid"))
        sys.exit(1)
    symbols = news.configured_symbols()
    if not a.backfill_only:
        threading.Thread(target=heartbeat_loop, daemon=True, name="heartbeat").start()
    catch_up(symbols, a.days)
    if a.backfill_only:
        return
    threading.Thread(target=poll_loop, args=(symbols,), daemon=True, name="poll").start()
    log.info("collecting live news for %s", ",".join(symbols))
    try:
        run_stream(symbols)
    finally:
        state["connected"] = False
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
