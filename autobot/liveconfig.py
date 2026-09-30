"""
Shared by live.py and the lab API, so both apply the same rules:

- validate_live(): checks the `live:` section of config.yaml before it is used
  (the API refuses to save a bad config; the bot keeps its previous config).
- bot lock: live.py writes logs/bot.pid with a heartbeat every loop; a second bot
  (or the Start button) refuses to start while the first is alive and beating.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

from .data import TIMEFRAMES, synthetic_bars
from .strategies import STRATEGIES, get_signal

ROOT = Path(__file__).resolve().parent.parent
PID_FILE = ROOT / "logs" / "bot.pid"
STOP_FILE = ROOT / "STOP"
HEARTBEAT_STALE_S = 90

_probe = None


def _probe_bars():
    global _probe
    if _probe is None:
        _probe = synthetic_bars(["PROBE"], "15Min", 20, seed=1)["PROBE"]
    return _probe


# ----------------------------------------------------------------- validation
def validate_live(live: Dict) -> List[str]:
    """Return a list of problems (empty = OK)."""
    errs: List[str] = []
    if not isinstance(live, dict):
        return ["live section missing"]
    if live.get("timeframe") not in TIMEFRAMES:
        errs.append(f"timeframe must be one of {list(TIMEFRAMES)}")
    risk = live.get("risk") or {}
    for k in ("max_daily_loss_pct", "max_position_usd"):
        v = risk.get(k)
        if not isinstance(v, (int, float)) or v <= 0:
            errs.append(f"risk.{k} must be a positive number")
    seen: Dict[str, str] = {}
    names = set()
    for i, j in enumerate(live.get("jobs") or []):
        name = j.get("name") or f"job {i + 1}"
        if name in names:
            errs.append(f"duplicate job name '{name}'")
        names.add(name)
        strat = j.get("strategy")
        if strat not in STRATEGIES:
            errs.append(f"{name}: unknown strategy '{strat}'")
            continue
        syms = j.get("symbols") or []
        if not syms or not all(isinstance(s, str) and s.strip() for s in syms):
            errs.append(f"{name}: needs at least one symbol")
        alloc = j.get("allocation_pct")
        if not isinstance(alloc, (int, float)) or not 0 < alloc <= 100:
            errs.append(f"{name}: allocation_pct must be between 0 and 100")
        params = j.get("params") or {}
        if not isinstance(params, dict):
            errs.append(f"{name}: params must be a mapping")
            continue
        unknown = set(params) - set(STRATEGIES[strat].defaults)
        if unknown:
            errs.append(f"{name}: unknown params for {strat}: {', '.join(sorted(unknown))}")
            continue
        try:  # catches bad types / impossible combos before real money-shaped data does
            get_signal(strat, _probe_bars(), params, bool(live.get("allow_short", False)))
        except Exception as e:  # noqa: BLE001
            errs.append(f"{name}: {strat} rejects these params: {e}")
        if not j.get("enabled", True):
            continue
        for s in syms:
            if s in seen:
                errs.append(f"{s} is in enabled jobs '{seen[s]}' and '{name}'. One job per symbol.")
            seen[s] = name
    return errs


def warmup_days(strategy: str, params: Dict | None) -> int:
    """Calendar days of history a live job needs, including a meta-strategy's candidates."""
    spec = STRATEGIES[strategy]
    days = spec.warmup_days
    p = {**spec.defaults, **(params or {})}
    names = list(p.get("candidates") or []) + [p.get(k) for k in ("trend_strategy", "range_strategy", "high_vol_strategy")]
    for n in names:
        if n in STRATEGIES:
            days = max(days, STRATEGIES[n].warmup_days)
    return days


# ----------------------------------------------------------------- bot lock / heartbeat
def pid_alive(pid: int) -> bool:
    """True if a process with this pid exists. Never sends a signal (os.kill on Windows
    would terminate the process), only queries it."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_lock() -> Optional[Dict]:
    try:
        return json.loads(PID_FILE.read_text())
    except (OSError, ValueError):
        return None


def bot_state() -> Dict:
    """running = pid alive AND heartbeat fresh.
    unresponsive = pid alive but heartbeat stale: maybe a stuck bot, maybe a reused pid.
    Never start a second bot in either case (two bots would double-trade)."""
    lock = read_lock()
    if not lock:
        return {"running": False, "unresponsive": False}
    age = time.time() - float(lock.get("heartbeat", 0))
    alive = pid_alive(int(lock.get("pid", 0)))
    fresh = age < HEARTBEAT_STALE_S
    return {"running": alive and fresh, "unresponsive": alive and not fresh, "pid": lock.get("pid"),
            "started": lock.get("started"), "heartbeat_age_s": round(age, 1), "process_alive": alive,
            "stop_requested": STOP_FILE.exists()}


def write_lock(started: float) -> bool:
    """Atomic write so readers never see a half-written file. On Windows the replace fails
    while another process (the lab API) has bot.pid open, so retry briefly; never raise.
    Returns False if this heartbeat couldn't be written (the next one tries again)."""
    PID_FILE.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=PID_FILE.parent, suffix=".pid.tmp")
    with os.fdopen(fd, "w") as f:
        json.dump({"pid": os.getpid(), "started": started, "heartbeat": time.time(), "paper": True}, f)
    for attempt in range(10):
        try:
            os.replace(tmp, PID_FILE)
            return True
        except PermissionError:
            time.sleep(0.02 * (attempt + 1))
    try:
        os.unlink(tmp)
    except OSError:
        pass
    return False


def release_lock():
    lock = read_lock()
    if lock and lock.get("pid") == os.getpid():
        PID_FILE.unlink(missing_ok=True)
