"""
Supervisor for the paper bot: starts live.py and restarts it if it crashes.

    python run_bot.py        (start_bot.bat, and the "Spire autobot" task at log-on)

- Crashes (any exit other than a normal stop) are written to logs/bot.log with the last lines
  of the bot's output, and the bot is restarted after a short pause: at most MAX_RESTARTS
  times in any hour, then the supervisor gives up (and says so in the log).
- A normal stop (Stop + flatten in the lab, the STOP file, stop_bot.bat) is not restarted.
- Only one supervisor and one bot run at a time: a second supervisor, or a bot that finds
  another bot running (exit code 4), exits quietly.
The bot itself is unchanged: paper trading only (TradingClient paper=True in live.py).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
BOT_LOG = LOGS / "bot.log"
OUT_LOG = LOGS / "bot_stdout.log"
LOCK = LOGS / "run_bot.pid"
MAX_RESTARTS = 3
WINDOW_S = 3600
PAUSE_S = 15
EXIT_OK, EXIT_ALREADY_RUNNING = 0, 4


def log(msg: str, level: str = "INFO"):
    LOGS.mkdir(exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S},000 {level} SUPERVISOR {msg}"
    with open(BOT_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if sys.stdout:
        print(line, flush=True)


class RestartPolicy:
    """Allow a restart unless MAX_RESTARTS already happened within the last WINDOW_S seconds."""

    def __init__(self, max_restarts: int = MAX_RESTARTS, window_s: float = WINDOW_S):
        self.max, self.window, self.times = max_restarts, window_s, deque()

    def allow(self, now: float) -> bool:
        while self.times and now - self.times[0] > self.window:
            self.times.popleft()
        if len(self.times) >= self.max:
            return False
        self.times.append(now)
        return True


def other_supervisor_alive() -> bool:
    from autobot.liveconfig import pid_alive
    try:
        pid = json.loads(LOCK.read_text())["pid"]
    except (OSError, ValueError, KeyError):
        return False
    return pid != os.getpid() and pid_alive(int(pid))


def tail(path: Path, n: int = 15) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8000))
            return "\n".join(f.read().decode("utf-8", "replace").splitlines()[-n:])
    except OSError:
        return "(no output)"


def run_once() -> int:
    """Start live.py once (no console window) and wait for it; its output goes to bot_stdout.log."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    with open(OUT_LOG, "a", encoding="utf-8") as out:
        out.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} bot started by run_bot.py ---\n")
        out.flush()
        return subprocess.call([str(python if python.exists() else sys.executable), str(ROOT / "live.py")],
                               cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags)


def supervise(run=run_once, sleep=time.sleep, clock=time.time, say=None, what: str = "the bot",
              out_log: Path = OUT_LOG, how_to_start: str = "start_bot.bat or the lab's Start button") -> int:
    """Run, and restart after a crash (RestartPolicy). Also used by run_news.py for the news collector."""
    say = say or log
    policy = RestartPolicy()
    while True:
        code = run()
        if code == EXIT_OK:
            say(f"{what} stopped normally (stop requested); not restarting")
            return EXIT_OK
        if code == EXIT_ALREADY_RUNNING:
            return EXIT_ALREADY_RUNNING  # another copy is running: quiet
        say(f"CRASH: {what} exited with code {code}. Last output:\n{tail(out_log)}", "ERROR")
        if not policy.allow(clock()):
            say(f"giving up: {MAX_RESTARTS} crashes within the last hour. Check the log, then start it again "
                f"({how_to_start}).", "CRITICAL")
            return code
        say(f"restarting in {PAUSE_S} s ({len(policy.times)} of {MAX_RESTARTS} restarts allowed per hour)", "WARNING")
        sleep(PAUSE_S)


def main() -> int:
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    if other_supervisor_alive():
        return 0  # quietly: one supervisor is enough
    LOGS.mkdir(exist_ok=True)
    LOCK.write_text(json.dumps({"pid": os.getpid(), "started": time.time()}))
    try:
        log("supervisor started")
        code = supervise()
        return 0 if code == EXIT_ALREADY_RUNNING else code  # "already running" is not a failure
    finally:
        try:
            if json.loads(LOCK.read_text()).get("pid") == os.getpid():
                LOCK.unlink()
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    sys.exit(main())
