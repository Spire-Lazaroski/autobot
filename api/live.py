"""
Live monitor endpoints: bot status, paper account, candles with fills, start / stop.

The API never kills or signals the bot process. Start spawns live.py (refused while a
bot is alive); Stop writes the STOP file, which the bot checks every few seconds before
flattening and exiting. Alpaca is only queried read-only here, with paper=True.
"""
from __future__ import annotations

import asyncio
import csv
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import yaml
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from autobot.data import TIMEFRAMES, _download, _keys, is_crypto, with_timeout
from autobot.liveconfig import STOP_FILE, bot_state

from .serialize import times, values
from .settings import CONFIG_PATH, LOGS, ROOT

router = APIRouter()
PUSH_SECONDS = 3
_tc = None
_tc_lock = threading.Lock()
_snap: Dict[str, Any] = {"at": 0.0, "data": None}
_snap_lock = threading.Lock()


def _client():
    """Read-only use of the PAPER trading client (never live)."""
    global _tc
    with _tc_lock:
        if _tc is None:
            from alpaca.trading.client import TradingClient
            _tc = with_timeout(TradingClient(*_keys(), paper=True))
        return _tc


def _f(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _ts(v) -> Optional[int]:
    return int(pd.Timestamp(v).timestamp()) if v is not None else None


def _enum(v) -> Optional[str]:
    return None if v is None else getattr(v, "value", str(v))


def _order_json(o) -> Dict:
    return {"id": str(o.id), "symbol": o.symbol, "side": _enum(o.side), "type": _enum(o.type),
            "qty": _f(o.qty), "filled_qty": _f(o.filled_qty), "filled_avg_price": _f(o.filled_avg_price),
            "status": _enum(o.status), "submitted_at": _ts(o.submitted_at), "filled_at": _ts(o.filled_at)}


def _account() -> Dict:
    try:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest
        tc = _client()
        a = tc.get_account()
        eq, last = _f(a.equity), _f(a.last_equity)
        positions = [{"symbol": p.symbol, "side": _enum(p.side), "qty": _f(p.qty), "avg_entry_price": _f(p.avg_entry_price),
                      "current_price": _f(p.current_price), "market_value": _f(p.market_value),
                      "unrealized_pl": _f(p.unrealized_pl), "unrealized_plpc": (_f(p.unrealized_plpc) or 0) * 100,
                      "unrealized_intraday_pl": _f(p.unrealized_intraday_pl)} for p in tc.get_all_positions()]
        orders = [_order_json(o) for o in tc.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, limit=50))]
        clock = tc.get_clock()
        return {"ok": True, "account": {
            "number_tail": str(a.account_number)[-4:], "status": _enum(a.status), "equity": eq, "last_equity": last,
            "day_pnl": None if eq is None or last is None else eq - last,
            "day_pnl_pct": None if not eq or not last else (eq / last - 1) * 100,
            "cash": _f(a.cash), "buying_power": _f(a.buying_power)},
            "positions": positions, "orders": orders,
            "market": {"is_open": clock.is_open, "next_open": _ts(clock.next_open), "next_close": _ts(clock.next_close)}}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "positions": [], "orders": []}


def _read_json(path) -> Optional[Dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _trades(limit=100) -> List[Dict]:
    f = LOGS / "trades.csv"
    if not f.exists():
        return []
    with open(f, newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows[-limit:][::-1]


def _log_tail(n=60) -> List[str]:
    f = LOGS / "bot.log"
    if not f.exists():
        return []
    with open(f, "rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 20000))
        lines = fh.read().decode("utf-8", "replace").splitlines()
    return lines[-n:]


def _equity_today(max_lines: int = 3000) -> List[Dict]:
    """The latest trading day's rows of logs/equity.jsonl (written by the bot each tick while the market
    is open): time (unix s), equity, long-term holdings' value and today's P&L split."""
    f = LOGS / "equity.jsonl"
    if not f.exists():
        return []
    with open(f, "rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - max_lines * 200))
        lines = fh.read().decode("utf-8", "replace").splitlines()[1:]
    rows = []
    for l in lines:
        try:
            rows.append(json.loads(l))
        except ValueError:
            continue
    if not rows:
        return []
    day = lambda r: pd.Timestamp(r["t"]).tz_convert("America/New_York").date()  # noqa: E731
    last = day(rows[-1])
    return [{**r, "t": int(pd.Timestamp(r["t"]).timestamp())} for r in rows if day(r) == last]


def _log_entries(lines: List[str]) -> List[Dict]:
    """Bot log lines grouped into entries: a line starting with a date opens an entry, every other line
    (tracebacks) becomes its details, so errors show as one line with the details on demand."""
    out: List[Dict] = []
    for l in lines:
        if len(l) > 10 and l[:4].isdigit() and l[4] == "-" or not out:
            out.append({"line": l, "details": []})
        else:
            out[-1]["details"].append(l)
    for e in out:
        if e["details"]:
            last = next((d for d in reversed(e["details"]) if d.strip()), "")
            e["summary"] = last.strip()[:200]
    return out


_jvh: Dict[str, Any] = {"day": None, "data": None, "busy": False}


def _jobs_vs_hold() -> Optional[Dict]:
    """Trading jobs vs simply holding the same symbols (open to close-out, same size), summed over the
    days since the bot's decision log starts (autobot.llm_judge.jobs_vs_hold, from broker fills). Worked
    out once a day in the background; None until the first result is ready."""
    today = datetime.now(timezone.utc).date().isoformat()
    if _jvh["day"] != today and not _jvh["busy"]:
        _jvh["busy"] = True

        def work():
            try:
                from autobot import llm_judge as lj
                recs = lj.decisions()
                days = sorted({lj._t(r).tz_convert("America/New_York").date().isoformat() for r in recs
                               if r.get("type") == "decision" and r.get("job") not in (None, "core", "rotation")
                               and r.get("action") in ("buy", "sell", "flatten before close")})
                days = [d for d in days if d < today][-20:]
                rows = [r for r in lj.jobs_vs_hold(recs, days) if "error" not in r]
                _jvh["data"] = {"from": days[0] if days else None, "to": days[-1] if days else None, "job_days": len(rows),
                                "strategy_usd": round(sum(r["strategy_usd"] for r in rows), 2),
                                "hold_usd": round(sum(r["hold_usd"] or 0 for r in rows), 2)}
                _jvh["day"] = today
            except Exception as e:  # noqa: BLE001  display only
                _jvh["data"] = {"error": f"{type(e).__name__}: {e}"}
                _jvh["day"] = today
            finally:
                _jvh["busy"] = False
        threading.Thread(target=work, daemon=True, name="jobs_vs_hold").start()
    return _jvh["data"]


def snapshot() -> Dict:
    """Everything the Live page shows. Shared by all WebSocket clients and cached for a
    moment, so several open tabs don't multiply Alpaca requests (3 per refresh)."""
    with _snap_lock:
        if _snap["data"] is not None and time.time() - _snap["at"] < PUSH_SECONDS - 0.5:
            return _snap["data"]
    data = {"time": int(time.time()), "bot": bot_state(), "status": _read_json(LOGS / "status.json"),
            "trades": _trades(), "log": _log_tail(), "log_entries": _log_entries(_log_tail(120)),
            "equity_today": _equity_today(), "jobs_vs_hold": _jobs_vs_hold(), **_account()}
    with _snap_lock:
        _snap.update(at=time.time(), data=data)
    return data


@router.get("/api/live/snapshot")
def live_snapshot():
    return snapshot()


@router.websocket("/ws/live")
async def live_ws(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_text(json.dumps(await asyncio.to_thread(snapshot), default=str))
            await asyncio.sleep(PUSH_SECONDS)
    except (WebSocketDisconnect, RuntimeError):
        pass


@router.get("/api/live/candles")
def live_candles(symbol: str, timeframe: Optional[str] = None, bars: int = 300):
    """Recent bars for one symbol plus the paper account's actual fills in that window."""
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    tf = timeframe or cfg["live"]["timeframe"]
    if tf not in TIMEFRAMES:
        raise HTTPException(422, f"timeframe must be one of {list(TIMEFRAMES)}")
    amt, unit = TIMEFRAMES[tf]
    mins = amt * {"Minute": 1, "Hour": 60, "Day": 1440}[unit]
    days = max(3, int(bars * mins / (1440 if is_crypto(symbol) else 390)) + 3) if tf != "1Day" else bars * 7 // 5 + 5
    try:
        df = _download(symbol, tf, datetime.now(timezone.utc) - timedelta(days=days), None, "iex").tail(bars)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not load bars: {e}") from e
    fills: List[Dict] = []
    try:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest
        start = df.index[0].to_pydatetime() if len(df) else None
        orders = _client().get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=200, after=start,
                                                       symbols=[symbol]))
        step = pd.Timedelta(minutes=mins)
        for o in orders:
            if o.filled_at is None or not o.filled_avg_price:
                continue
            ft = pd.Timestamp(o.filled_at)
            i = df.index.searchsorted(ft, side="right") - 1  # the bar the fill happened in
            if 0 <= i < len(df) and ft < df.index[i] + step * 2:
                fills.append({"time": int(df.index[i].timestamp()), "price": float(o.filled_avg_price),
                              "side": _enum(o.side), "qty": _f(o.filled_qty)})
    except Exception:  # noqa: BLE001  fills are optional (e.g. no keys)
        pass
    return {"symbol": symbol, "timeframe": tf,
            "candles": {"t": times(df.index), "o": values(df.open, 4), "h": values(df.high, 4),
                        "l": values(df.low, 4), "c": values(df.close, 4), "v": values(df.volume, 0)},
            "fills": fills}


class StopReq(BaseModel):
    confirm: bool = False
    include_core: bool = False   # also sell the core holding (live.core); off = the bot keeps it


@router.post("/api/live/start")
def live_start():
    st = bot_state()
    if st["running"]:
        raise HTTPException(409, f"A bot is already running (pid {st['pid']}).")
    if st["unresponsive"]:
        raise HTTPException(409, f"Bot process {st['pid']} is still alive but its heartbeat is "
                                 f"{st['heartbeat_age_s']:.0f}s old. Stop it first (Stop + flatten, or end "
                                 "the process) so two bots can't trade at once.")
    STOP_FILE.unlink(missing_ok=True)
    return {"ok": True, "pid": start_detached("live.py", "bot_stdout.log")}


def start_detached(script: str, out_name: str) -> int:
    """Start a repo script that keeps running if the API restarts: its own process group (Windows) / session (Linux)."""
    detach = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
              if sys.platform == "win32" else {"start_new_session": True})
    out = open(LOGS / out_name, "a")
    proc = subprocess.Popen([sys.executable, str(ROOT / script)], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, close_fds=True, **detach)
    return proc.pid


@router.post("/api/live/stop")
def live_stop(req: StopReq):
    if not req.confirm:
        raise HTTPException(400, "Stop closes every position. Send {\"confirm\": true}.")
    st = bot_state()
    if not (st["running"] or st["unresponsive"]):
        raise HTTPException(409, "The bot isn't running, so there's nothing to stop.")
    STOP_FILE.write_text(json.dumps({"include_core": bool(req.include_core)}))
    what = "all positions" if req.include_core else "all positions except the core holding (if one is set)"
    return {"ok": True, "message": f"Stop requested: the bot will close {what} and exit within a few seconds."}
