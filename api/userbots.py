"""
Invited users' own bots (multi-user step 3): the same engine as the lab's bot, one per user, each in its own home.

  home        data/users/<name>/ (on the server: the permanent volume) with config.yaml, logs/ and STOP
  settings    data/users.db (table user_settings): the setup (careful / balanced / growth, autobot/presets.py),
              trading jobs on or off, and whether the user wants the bot running
  config      derived from the lab's own config.yaml (its jobs, as the nightly recalibration keeps them) with the
              user's setup sizes and, with jobs off, every trading job switched off; rewritten when either
              changes (sync_all runs every few minutes inside the API), so the bot picks it up at its next bar
  keys        the user's own paper key pair (api/credentials.py), passed only to that user's bot process together
              with AUTOBOT_HOME; none of the lab's own secrets reach it
  start/stop  run_bot.py in the home (restarts after a crash, at most 3 times an hour); Stop writes the STOP file
              (job positions closed, long-term holdings kept unless asked)

After a server restart or an update, bots the users had running are started again once the old version is gone
(resume_wanted, ~75 s after the API starts). Paper only: the bot hard-codes paper=True.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from . import credentials, users
from .settings import CONFIG_PATH, ROOT

USERS_HOME = ROOT / "data" / "users"
SYNC_EVERY_S = 300
RESUME_AFTER_S = 75
_SECRET_PREFIXES = ("ALPACA_", "LAB_", "ANTHROPIC")
_SECRET_NAMES = {"CREDENTIALS_KEY"}
_lock = threading.Lock()


def home(user: str) -> Path:
    return USERS_HOME / user


# ----------------------------------------------------------------- settings
def _db():
    c = users._db()
    c.execute("""CREATE TABLE IF NOT EXISTS user_settings (
        user TEXT PRIMARY KEY, setup TEXT NOT NULL, jobs_on INTEGER NOT NULL DEFAULT 0,
        want_running INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL)""")
    return c


def settings(user: str) -> Optional[Dict]:
    with _db() as c:
        r = c.execute("SELECT setup, jobs_on, want_running, updated FROM user_settings WHERE user = ?", (user,)).fetchone()
    return {"setup": r["setup"], "jobs_on": bool(r["jobs_on"]), "want_running": bool(r["want_running"]), "updated": r["updated"]} if r else None


def save_settings(user: str, setup: str, jobs_on: bool):
    from autobot import presets
    if setup not in presets.PRESETS:
        raise ValueError(f"unknown setup {setup}")
    with _lock, _db() as c:
        c.execute("""INSERT INTO user_settings(user, setup, jobs_on, want_running, updated) VALUES (?,?,?,0,?)
                     ON CONFLICT(user) DO UPDATE SET setup = excluded.setup, jobs_on = excluded.jobs_on, updated = excluded.updated""",
                  (user, setup, 1 if jobs_on else 0, time.time()))
    write_config(user)


def _want(user: str, on: bool):
    with _lock, _db() as c:
        c.execute("UPDATE user_settings SET want_running = ? WHERE user = ?", (1 if on else 0, user))


def all_settings() -> Dict[str, Dict]:
    with _db() as c:
        rows = c.execute("SELECT user, setup, jobs_on, want_running FROM user_settings").fetchall()
    return {r["user"]: {"setup": r["setup"], "jobs_on": bool(r["jobs_on"]), "want_running": bool(r["want_running"])} for r in rows}


# ----------------------------------------------------------------- the user's config
def derive_config(lab_cfg: Dict, st: Dict, paused: Optional[List[str]] = None) -> Dict:
    """The lab's config with the user's setup sizes; with jobs off, every trading job switched off."""
    from autobot import presets
    cfg = presets.apply(copy.deepcopy(lab_cfg), st["setup"])
    live = cfg["live"]
    if not st["jobs_on"]:
        for j in live.get("jobs") or []:
            j["enabled"] = False
    live.pop("paused", None)
    if paused:
        live["paused"] = sorted(set(paused))
    cfg.pop("llm_judge", None)          # the lab's own experiments stay with the lab
    return cfg


def write_config(user: str) -> bool:
    """Write the user's config.yaml if it differs (atomic, validated). Returns True when it changed."""
    from autobot.liveconfig import validate_live
    st = settings(user)
    if not st:
        return False
    h = home(user)
    path = h / "config.yaml"
    paused = None
    try:
        paused = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("live", {}).get("paused")
    except (OSError, ValueError):
        pass
    cfg = derive_config(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")), st, paused)
    if validate_live(cfg["live"]):
        return False
    text = yaml.safe_dump(cfg, sort_keys=False)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    (h / "logs").mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=h, suffix=".yaml.tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return True


def sync_all() -> List[str]:
    """Bring every user's config up to date with the lab's (after the nightly recalibration, say)."""
    changed = []
    for u in all_settings():
        try:
            if write_config(u):
                changed.append(u)
        except Exception:  # noqa: BLE001  one user's problem must not stop the others
            pass
    return changed


# ----------------------------------------------------------------- the bot process
def state(user: str) -> Dict:
    from autobot.liveconfig import bot_state
    return bot_state(home(user))


BUSY_HEARTBEAT_S = 120


def busy(user: str) -> bool:
    """Running here, stuck, or with a recent heartbeat: during an update the previous version's bot runs on in
    another container, whose process this one can't see, so a fresh heartbeat alone means 'don't start another'."""
    s = state(user)
    age = s.get("heartbeat_age_s")
    return bool(s["running"] or s["unresponsive"] or (age is not None and age < BUSY_HEARTBEAT_S and not s.get("process_alive")))


def child_env(user: str, cred: Dict) -> Dict[str, str]:
    """The user's bot gets their own paper keys and its home, and none of the lab's secrets."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(_SECRET_PREFIXES) and k not in _SECRET_NAMES}
    env.update(ALPACA_API_KEY=cred["key"], ALPACA_SECRET_KEY=cred["secret"], AUTOBOT_HOME=str(home(user)))
    return env


def start(user: str) -> int:
    """Start the user's bot (under its supervisor). Raises ValueError with a plain reason."""
    st = settings(user)
    if not st:
        raise ValueError("Choose your setup first.")
    cred = credentials.load(user)
    if not cred:
        raise ValueError("Connect your Alpaca paper account first.")
    if "key" not in cred:
        raise ValueError("Your connection can't run a bot yet: please connect with your paper API keys.")
    s = state(user)
    if s["running"]:
        raise ValueError("Your bot is already running.")
    if busy(user):
        raise ValueError("Your bot is still shutting down (or the server is switching versions). Try again in a minute.")
    write_config(user)
    h = home(user)
    (h / "STOP").unlink(missing_ok=True)
    (h / "logs").mkdir(parents=True, exist_ok=True)
    detach = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
              if sys.platform == "win32" else {"start_new_session": True})
    out = open(h / "logs" / "supervisor_stdout.log", "a")
    proc = subprocess.Popen([sys.executable, str(ROOT / "run_bot.py")], cwd=ROOT, env=child_env(user, cred),
                            stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, close_fds=True, **detach)
    _want(user, True)
    return proc.pid


def stop(user: str, include_core: bool = False, by_user: bool = True):
    """The STOP file in the user's home: the bot closes the job positions (and the long-term holdings only if
    asked), then exits. by_user=False (a server shutdown) keeps the wish to run, so it comes back afterwards."""
    h = home(user)
    h.mkdir(parents=True, exist_ok=True)
    (h / "STOP").write_text(json.dumps({"include_core": bool(include_core)}))
    if by_user:
        _want(user, False)


def resume_wanted() -> List[str]:
    """Start the bots users had running (after a server restart or an update)."""
    started = []
    for u, st in all_settings().items():
        if st["want_running"] and users.active(u) and not busy(u):
            try:
                start(u)
                started.append(u)
            except Exception:  # noqa: BLE001
                pass
    return started


def background():
    """Inside the API: resume wanted bots once the previous version has gone, then keep configs in sync."""
    def loop():
        time.sleep(RESUME_AFTER_S)
        for _ in range(10):          # the old version's bots may still be closing: try for about 5 minutes
            try:
                if not [u for u, s in all_settings().items() if s["want_running"] and users.active(u) and not state(u)["running"]]:
                    break
                resume_wanted()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(30)
        while True:
            try:
                sync_all()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(SYNC_EVERY_S)
    threading.Thread(target=loop, name="user-bots", daemon=True).start()


# ----------------------------------------------------------------- what the user's page shows
_acct_cache: Dict[str, tuple] = {}


def account(user: str) -> Dict:
    """Balance and positions of the user's own paper account (read-only, their keys; cached 10 s)."""
    now = time.time()
    hit = _acct_cache.get(user)
    if hit and now - hit[0] < 10:
        return hit[1]
    cred = credentials.load(user)
    if not cred or "key" not in cred:
        return {"ok": False}
    try:
        from alpaca.trading.client import TradingClient

        from autobot.data import with_timeout
        tc = with_timeout(TradingClient(cred["key"], cred["secret"], paper=True))
        a = tc.get_account()
        eq, last = float(a.equity), float(a.last_equity)
        pos = [{"symbol": p.symbol, "qty": float(p.qty), "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl), "unrealized_plpc": round(float(p.unrealized_plpc) * 100, 2)}
               for p in tc.get_all_positions()]
        out = {"ok": True, "equity": eq, "cash": float(a.cash), "day_pnl": round(eq - last, 2),
               "day_pnl_pct": round((eq / last - 1) * 100, 3) if last else 0.0, "positions": pos}
    except Exception as e:  # noqa: BLE001
        out = {"ok": False, "error": f"Alpaca: {e.__class__.__name__}"}
    _acct_cache[user] = (now, out)
    return out


def recent(user: str, n_trades: int = 20, n_log: int = 12) -> Dict:
    lg = home(user) / "logs"
    trades: List[Dict] = []
    try:
        import csv
        with open(lg / "trades.csv", newline="") as f:
            trades = list(csv.DictReader(f))[-n_trades:][::-1]
    except OSError:
        pass
    lines: List[str] = []
    try:
        with open(lg / "bot.log", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 6000))
            lines = f.read().decode("utf-8", "replace").splitlines()[-n_log:]
    except OSError:
        pass
    return {"trades": trades, "log": lines}


def stop_all_and_wait(timeout_s: float = 100) -> None:
    """Server shutdown: STOP every running user bot (their wish to run is kept) and wait for them to exit."""
    running = [u for u in all_settings() if state(u)["running"] or state(u)["unresponsive"]]
    for u in running:
        stop(u, by_user=False)
    end = time.time() + timeout_s
    while running and time.time() < end:
        running = [u for u in running if state(u)["running"] or state(u)["unresponsive"]]
        time.sleep(1)


if __name__ == "__main__":
    if sys.argv[1:] == ["stop-all"]:
        stop_all_and_wait()
