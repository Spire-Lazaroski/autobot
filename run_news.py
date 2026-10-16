"""
Supervisor for the news collector: starts news_collector.py and restarts it if it crashes.

    python run_news.py       (start_news.bat and start_lab.bat)

Same rules as run_bot.py: a crash is written to logs/news.log with the last lines of the
collector's output (logs/news_stdout.log), then the collector is restarted after a short
pause, at most 3 times in any hour; after that it waits COOLDOWN_S and tries again (it never trades, so
it never gives up for good: on the server nobody is there to restart it). A second supervisor, or a
collector that finds another collector running, exits quietly.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import run_bot  # noqa: E402  the restart rules are shared with the bot's supervisor

LOGS = ROOT / "logs"
NEWS_LOG = LOGS / "news.log"
OUT_LOG = LOGS / "news_stdout.log"
LOCK = LOGS / "run_news.pid"
COOLDOWN_S = 900


def log(msg: str, level: str = "INFO"):
    LOGS.mkdir(exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S},000 {level} SUPERVISOR {msg}"
    with open(NEWS_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if sys.stdout:
        print(line, flush=True)


def run_once() -> int:
    """Start news_collector.py once and wait for it; its console output goes to news_stdout.log."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    with open(OUT_LOG, "a", encoding="utf-8") as out:
        out.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} collector started by run_news.py ---\n")
        out.flush()
        return subprocess.call([str(python if python.exists() else sys.executable), str(ROOT / "news_collector.py")],
                               cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags)


def other_supervisor_alive() -> bool:
    from autobot.liveconfig import pid_alive
    try:
        pid = json.loads(LOCK.read_text())["pid"]
    except (OSError, ValueError, KeyError):
        return False
    return pid != os.getpid() and pid_alive(int(pid))


def supervise(run=run_once, sleep=time.sleep, clock=time.time) -> int:
    return run_bot.supervise(run, sleep, clock, say=log, what="the news collector", out_log=OUT_LOG,
                             how_to_start="start_news.bat or the News page's Start button", cooldown_s=COOLDOWN_S)


def main() -> int:
    os.chdir(ROOT)
    if other_supervisor_alive():
        print("A news collector supervisor is already running.")
        return 0
    LOGS.mkdir(exist_ok=True)
    LOCK.write_text(json.dumps({"pid": os.getpid(), "started": time.time()}))
    try:
        log("supervisor started")
        code = supervise()
        if code == run_bot.EXIT_ALREADY_RUNNING:
            log("a news collector was already running (started without this supervisor); nothing to do")
            return 0
        return code
    finally:
        try:
            if json.loads(LOCK.read_text()).get("pid") == os.getpid():
                LOCK.unlink()
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    sys.exit(main())
