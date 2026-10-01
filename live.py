"""
Paper-trading runner.

    python live.py              # run the jobs in config.yaml on your Alpaca PAPER account
    python live.py --once       # evaluate one bar and exit (good for testing)
    python live.py --flatten    # close every position and exit

Create a file named STOP in this folder (or click Stop in the dashboard) to
flatten everything and shut down cleanly.

config.yaml is re-read at every bar: strategy / params / symbols / allocation / risk
changes apply without a restart and every change is logged. An invalid config is
rejected (logged) and the previous one stays in force. A symbol that leaves all
enabled jobs has its position closed.

Only one bot runs at a time: logs/bot.pid holds the pid and a heartbeat.

Hang protection: every Alpaca request has a timeout, and a watchdog thread (with its
own client) keeps working if the main loop gets stuck: it honours the STOP file,
enforces the daily-loss limit, and if the loop makes no progress for stall_seconds
(default 300) it logs CRITICAL and exits WITHOUT trading, so the lab shows it stopped.
The heartbeat is written only while the main loop is making progress.

Every bar, for every symbol, the bot appends WHY it acted (or didn't) to
logs/decisions.jsonl: strategy in charge, regime and the indicator values behind it,
signal, action, order ids and fill price; plus every regime change and strategy
switch with before/after values. Reports are built from this file.

Runs for many days: the daily-loss starting point resets when a new trading day opens, a
loss-limit halt lasts until the next trading day (the process keeps running), and after the PC
wakes from sleep the bot notices the gap, re-reads the market clock and doesn't mistake the
sleep for a hang.

Exit codes (run_bot.py restarts only on crashes):
  0  normal stop (STOP file / Stop + flatten, --once, --flatten)
  3  watchdog: the main loop was stuck
  4  another bot is already running (this copy exits quietly)
  other  crash

Paper only: TradingClient is created with paper=True and there is no switch to change it.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

from autobot.data import TIMEFRAMES, _download, _keys, is_crypto, wait_for_network, with_timeout
from autobot import sizing, trigger
from autobot.decisions import Context, Recorder
from autobot.liveconfig import bot_state, release_lock, validate_live, warmup_days, write_lock
from autobot.strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
STOP_FILE = ROOT / "STOP"

log = logging.getLogger("autobot")
EXIT_OK, EXIT_STALLED, EXIT_ALREADY_RUNNING = 0, 3, 4
NY_TZ = "America/New_York"
SLEEP_GAP_S = 60  # a gap this long between loop turns means the PC slept (or the process was paused)
TRIGGER_BUDGET_S = 30  # display-only trigger levels: at most this long per bar, after all orders


def setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(LOGS / "bot.log"), logging.StreamHandler()])


def tf_minutes(label: str) -> int:
    amt, unit = TIMEFRAMES[label]
    return amt * {"Minute": 1, "Hour": 60, "Day": 1440}[unit]


def enabled_jobs(live: dict) -> list:
    return [j for j in live.get("jobs", []) if j.get("enabled", True)]


def config_diff(old: dict, new: dict) -> list[str]:
    """Human-readable list of what changed in the live section."""
    out = []
    for k in ("timeframe", "allow_short", "poll_seconds"):
        if old.get(k) != new.get(k):
            out.append(f"{k}: {old.get(k)} -> {new.get(k)}")
    for k in sorted(set(old.get("risk", {})) | set(new.get("risk", {}))):
        a, b = old.get("risk", {}).get(k), new.get("risk", {}).get(k)
        if a != b:
            out.append(f"risk.{k}: {a} -> {b}")
    oj = {j["name"]: j for j in old.get("jobs", [])}
    nj = {j["name"]: j for j in new.get("jobs", [])}
    for name in nj.keys() - oj.keys():
        j = nj[name]
        out.append(f"job '{name}' added: {j['strategy']} {j.get('params', {})} on {j['symbols']}"
                   f"{'' if j.get('enabled', True) else ' (disabled)'}")
    for name in oj.keys() - nj.keys():
        out.append(f"job '{name}' removed")
    for name in oj.keys() & nj.keys():
        for k in ("enabled", "strategy", "params", "symbols", "allocation_pct"):
            a, b = oj[name].get(k), nj[name].get(k)
            if k == "enabled":
                a, b = a if a is not None else True, b if b is not None else True
            if a != b:
                out.append(f"job '{name}' {k}: {a} -> {b}")
    return out


class Bot:
    def __init__(self, cfg: dict, tc=None, cfg_path: Path | None = None):
        if tc is None:
            tc = self._new_client()
            self._watch_tc_factory = self._new_client  # the watchdog gets its own connection
        else:
            self._watch_tc_factory = lambda: tc  # injected (tests)
        self.tc = tc
        self._progress = time.time()  # last time the main loop moved forward
        self._exit = os._exit          # replaceable in tests
        self._stop_watch = threading.Event()
        self._quit = threading.Event()  # ends run() cleanly (tests; embedding)
        self.rec = Recorder(LOGS / "decisions.jsonl")
        self.watch_risk_after = 60.0   # seconds of no progress before the watchdog checks the loss limit
        self.cfg_path = cfg_path
        self._cfg_mtime = cfg_path.stat().st_mtime if cfg_path and cfg_path.exists() else None
        errs = validate_live(cfg["live"])
        if errs:
            raise ValueError("config.yaml live section is invalid:\n  " + "\n  ".join(errs))
        self._apply(cfg["live"])
        self.buckets = sizing.bucket_map(cfg)   # universe buckets, for risk_balanced bucket caps
        self._atr: dict = {}                    # ATR% per symbol, refreshed once per New York day
        self._atr_day = None
        self.changes: list[dict] = []
        acct = self.tc.get_account()
        self.day_start_equity = float(acct.last_equity)
        self.trading_day = self._today(self.tc.get_clock())
        self.halted = False
        self.day_peak_pnl = None   # profit lock: the day's best P&L % once armed
        log.info("Paper account %s | equity $%s | jobs: %s", acct.account_number, acct.equity,
                 ", ".join(f"{j['name']}({j['strategy']}:{'/'.join(j['symbols'])})" for j in self.jobs))

    @staticmethod
    def _new_client():
        from alpaca.trading.client import TradingClient
        key, sec = _keys()
        return with_timeout(TradingClient(key, sec, paper=True))  # <- hard-wired to paper

    def _apply(self, live: dict):
        self.cfg = live
        self.stall_s = float(live.get("stall_seconds", 300))
        self.tf = live["timeframe"]
        self.step = tf_minutes(self.tf)
        self.risk = live["risk"]
        self.jobs = enabled_jobs(live)

    # ------------------------------------------------------------ hot reload
    def reload_config(self):
        """Re-read config.yaml if it changed on disk. Bad config -> keep the old one."""
        if not self.cfg_path or not self.cfg_path.exists():
            return
        mtime = self.cfg_path.stat().st_mtime
        if mtime == self._cfg_mtime:
            return
        self._cfg_mtime = mtime
        try:
            full = yaml.safe_load(self.cfg_path.read_text())
            new = full["live"]
        except Exception as e:  # noqa: BLE001
            log.error("CONFIG reload failed, keeping previous config: %s", e)
            return
        self.buckets = sizing.bucket_map(full)
        errs = validate_live(new)
        if errs:
            log.error("CONFIG rejected, keeping previous config: %s", "; ".join(errs))
            self._record(["rejected: " + "; ".join(errs)])
            return
        diff = config_diff(self.cfg, new)
        if not diff:
            return
        old_syms = {s for j in self.jobs for s in j["symbols"]}
        self._apply(new)
        for line in diff:
            log.warning("CONFIG %s", line)
            self._safe(self.rec.event, "config", f"Settings changed: {line}.")
        self._record(diff)
        dropped = old_syms - {s for j in self.jobs for s in j["symbols"]}
        if dropped:
            held = self.positions()
            for sym in sorted(dropped):
                if held.get(self._pos_key(sym), 0.0):
                    log.warning("CONFIG %s left all enabled jobs: closing its position", sym)
                    try:
                        self.tc.close_position(self._pos_key(sym))
                        self._log_trade(sym, 0, "removed from jobs", 0.0, "config", "close_position")
                    except Exception as e:  # noqa: BLE001
                        log.error("could not close %s: %s", sym, e)

    def _record(self, lines):
        now = datetime.now(timezone.utc).isoformat()
        self.changes = (self.changes + [{"time": now, "change": ln} for ln in lines])[-30:]

    # ------------------------------------------------------------ broker helpers
    def positions(self) -> dict:
        return {p.symbol: float(p.qty) for p in self.tc.get_all_positions()}

    @staticmethod
    def _pos_key(sym: str) -> str:
        return sym.replace("/", "")  # Alpaca reports BTC/USD positions as BTCUSD

    def _log_trade(self, sym, qty, reason, price, job, order_id, side=None):
        new = not (LOGS / "trades.csv").exists()
        with open(LOGS / "trades.csv", "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "job", "symbol", "side", "qty", "approx_price", "reason", "order_id"])
            w.writerow([datetime.now(timezone.utc).isoformat(), job, sym, side or "close", abs(qty),
                        round(price, 4), reason, order_id])

    def submit(self, sym: str, qty: float, reason: str, price: float, job: str):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        if qty == 0:
            return
        side = OrderSide.BUY if qty > 0 else OrderSide.SELL
        tif = TimeInForce.GTC if is_crypto(sym) else TimeInForce.DAY
        order = self.tc.submit_order(MarketOrderRequest(symbol=sym, qty=abs(qty), side=side, time_in_force=tif))
        log.info("ORDER %s %s %s @~%.2f (%s) id=%s", side.value, abs(qty), sym, price, reason, order.id)
        self._log_trade(sym, qty, reason, price, job, order.id, side.value)
        return order

    def fill_price(self, order_id) -> float | None:
        """Market orders fill within moments during market hours; ask once."""
        getter = getattr(self.tc, "get_order_by_id", None)
        if getter is None:
            return None
        try:
            time.sleep(0.4)
            o = getter(order_id)
            return float(o.filled_avg_price) if getattr(o, "filled_avg_price", None) else None
        except Exception:  # noqa: BLE001  the report falls back to the account's fills
            return None

    def _safe(self, fn, *args, **kw):
        """Recording must never stop trading."""
        try:
            return fn(*args, **kw)
        except Exception as e:  # noqa: BLE001
            log.error("decision log failed: %s", e)

    def flatten_all(self, why: str):
        log.warning("FLATTEN ALL: %s", why)
        self._safe(self.rec.event, "flatten", f"The bot closed every position: {why}.")
        self.tc.close_all_positions(cancel_orders=True)

    # ------------------------------------------------------------ core
    def recent_bars(self, sym: str, job: dict) -> pd.DataFrame:
        now = datetime.now(timezone.utc)
        days = 400 if self.tf == "1Day" else max(5, math.ceil(300 * self.step / 390) + 4)
        days = max(days, warmup_days(job["strategy"], job.get("params")))
        df = _download(sym, self.tf, now - timedelta(days=days), None, "iex")
        df = df[df.index + pd.Timedelta(minutes=self.step) <= now]  # drop the still-forming bar
        if not is_crypto(sym) and self.tf != "1Day":
            ny = df.index.tz_convert("America/New_York")
            m = ny.hour * 60 + ny.minute
            df = df[(m >= 570) & (m < 960)]
        return df

    @staticmethod
    def _today(clock) -> str:
        """The New York calendar date of the market clock."""
        return pd.Timestamp(clock.timestamp).tz_convert(NY_TZ).date().isoformat()

    def roll_day(self, clock) -> bool:
        """At the first tick of a new trading day (market open): reset the daily-loss starting
        point to the account's previous close and lift a loss-limit halt. Returns True if rolled."""
        today = self._today(clock)
        if today == self.trading_day or not clock.is_open:
            return False
        acct = self.tc.get_account()
        old, self.day_start_equity = self.day_start_equity, float(acct.last_equity)
        was_halted, self.halted = self.halted, False
        self.day_peak_pnl = None
        log.warning("NEW DAY %s: daily-loss start %.2f -> %.2f%s", today, old, self.day_start_equity,
                    " (loss-limit halt lifted)" if was_halted else "")
        self._safe(self.rec.event, "new_day", f"New trading day {today}: the daily loss limit now counts from "
                                              f"${self.day_start_equity:,.2f}" + (", and yesterday's halt is lifted." if was_halted else "."))
        self.trading_day = today
        return True

    def profit_lock_hit(self, pnl_pct: float) -> bool:
        """risk.profit_lock {enabled, trigger_pct, giveback_pct}: once the day's P&L reaches
        trigger_pct, track the day's peak; True when P&L falls below peak x (1 - giveback/100).
        Shared by the main loop and the watchdog (the peak lives on the bot)."""
        pl = self.risk.get("profit_lock") or {}
        if not pl.get("enabled"):
            return False
        if self.day_peak_pnl is None and pnl_pct >= float(pl["trigger_pct"]):
            self.day_peak_pnl = pnl_pct
            log.info("PROFIT LOCK armed: day P&L %.2f%% reached %.2f%%", pnl_pct, float(pl["trigger_pct"]))
        if self.day_peak_pnl is None:
            return False
        self.day_peak_pnl = max(self.day_peak_pnl, pnl_pct)
        return pnl_pct < self.day_peak_pnl * (1 - float(pl["giveback_pct"]) / 100)

    def _halt(self, kind: str, message: str, log_line: str):
        self.flatten_all(log_line)
        self._safe(self.rec.event, kind, message)
        log.warning("Halted for the rest of the trading day (%s); the bot keeps running and resumes tomorrow.", kind)
        self.halted = True

    def check_risk(self) -> bool:
        if self.halted:
            return False  # already flat and halted for today; the next trading day lifts it
        acct = self.tc.get_account()
        eq = float(acct.equity)
        dd = (eq / self.day_start_equity - 1) * 100
        if dd <= -abs(self.risk["max_daily_loss_pct"]):
            self._halt("risk", f"The daily loss limit was reached ({dd:.2f}% vs {self.risk['max_daily_loss_pct']}%); "
                               "the bot sold everything and stopped for the day.", f"daily loss {dd:.2f}% hit limit")
        elif self.profit_lock_hit(dd):
            pl = self.risk["profit_lock"]
            self._halt("profit_lock", f"Profit lock: the day's gain reached {self.day_peak_pnl:.2f}% (lock starts at {pl['trigger_pct']}%) "
                                      f"and fell back to {dd:.2f}%, giving back more than {pl['giveback_pct']}% of it; "
                                      "the bot sold everything to keep the rest and stopped for the day.",
                       f"profit lock: day P&L {dd:.2f}% fell below {self.day_peak_pnl * (1 - float(pl['giveback_pct']) / 100):.2f}% "
                       f"(peak {self.day_peak_pnl:.2f}%)")
        return not self.halted

    def budgets(self, equity: float) -> dict:
        """Dollars each job symbol may hold (autobot.sizing, the same function the backtester uses)."""
        syms = [s for j in self.jobs for s in j["symbols"]]
        alloc = {s: j["allocation_pct"] for j in self.jobs for s in j["symbols"]}
        atr = {}
        if self.risk.get("sizing", "fixed") == "risk_balanced":
            today = pd.Timestamp.now(tz=NY_TZ).date()
            days = int(sizing.settings(self.risk)["atr_days"])
            if self._atr_day != today or set(syms) - set(self._atr):
                for s in syms:
                    try:
                        d = _download(s, "1Day", datetime.now(timezone.utc) - timedelta(days=days * 2 + 15), None, "iex")
                        self._atr[s] = sizing.daily_atr_pct(d, today, days)
                    except Exception as e:  # noqa: BLE001  no ATR -> no budget for that symbol until it works
                        log.error("%s: no daily bars for sizing: %s", s, e)
                        self._atr[s] = None
                self._atr_day = today
            atr = self._atr
        return sizing.budgets(equity, syms, self.risk, atr, alloc, self.buckets)

    def near_close(self, clock) -> bool:
        mins = self.risk.get("flatten_before_close_min", 0)
        return bool(mins) and clock.is_open and (clock.next_close - clock.timestamp) <= timedelta(minutes=mins)

    def step_once(self):
        self.reload_config()
        clock = self.tc.get_clock()
        self.roll_day(clock)
        if not self.check_risk():
            return
        held = self.positions()
        equity = float(self.tc.get_account().equity)
        status = {"time": datetime.now(timezone.utc).isoformat(), "equity": equity,
                  "day_pnl_%": round((equity / self.day_start_equity - 1) * 100, 3),
                  "market_open": clock.is_open, "halted": self.halted, "timeframe": self.tf,
                  "jobs": [{k: j.get(k) for k in ("name", "strategy", "params", "symbols", "allocation_pct")} for j in self.jobs],
                  "config_changes": self.changes, "symbols": {}}
        budget = self.budgets(equity)
        status["sizing"] = {"mode": self.risk.get("sizing", "fixed"), "usd": {s: round(v, 2) for s, v in budget.items()}}
        pl = self.risk.get("profit_lock") or {}
        status["profit_lock"] = {"enabled": bool(pl.get("enabled")), "armed": self.day_peak_pnl is not None,
                                 "peak_pnl_%": None if self.day_peak_pnl is None else round(self.day_peak_pnl, 3),
                                 "lock_below_%": None if self.day_peak_pnl is None else
                                 round(self.day_peak_pnl * (1 - float(pl.get("giveback_pct", 50)) / 100), 3)}
        pending = []  # decision records, written after the display-only trigger is known
        try:
            for job in self.jobs:
                spec = STRATEGIES[job["strategy"]]
                for sym in job["symbols"]:
                    self._progress = time.time()
                    crypto = is_crypto(sym)
                    if not crypto and not clock.is_open:
                        continue
                    cur = held.get(self._pos_key(sym), 0.0)
                    active = None
                    override = None
                    try:
                        df = self.recent_bars(sym, job)
                        if len(df) < 50:
                            log.info("%s: only %d bars, waiting for more history", sym, len(df))
                            continue
                        ctx = Context(job["strategy"], df, job.get("params") or {}, self.cfg.get("allow_short", False))
                        target_dir = float(ctx.signal.iloc[-1])
                        price = float(df.close.iloc[-1])
                        if spec.explain:
                            active = ctx.active[-1]
                    except Exception as e:  # noqa: BLE001
                        log.error("%s: data/signal error: %s", sym, e)
                        self._safe(self.rec.write, {"type": "decision", "job": job["name"], "symbol": sym,
                                                      "strategy": job["strategy"], "bar_time": datetime.now(timezone.utc).isoformat(),
                                                      "action": f"error: {e}", "reason": "The bot couldn't get data or compute the signal for this bar."})
                        continue
                    if not crypto and self.near_close(clock):
                        target_dir, reason = 0.0, "flatten before close"
                        override = (f"The bot closes stock positions {self.risk.get('flatten_before_close_min')} minutes before "
                                    "the US market close so nothing is held overnight.")
                    else:
                        reason = f"{job['strategy']} signal={target_dir:+.2f}" + (f" active={active}" if active else "")
                    if crypto and target_dir < 0:
                        target_dir = 0.0  # no crypto shorting on Alpaca
                    # dollars from autobot.sizing (fixed: min(allocation, max_position_usd)); fractional
                    # targets (ensemble average) size down; whole shares for stocks, 4-decimal crypto
                    target_qty = sizing.to_qty(budget.get(sym, 0.0), price, target_dir, crypto)
                    delta = round(target_qty - cur, 4)
                    orders = []
                    # crossing zero (long->short) must be two orders on Alpaca
                    if cur != 0 and target_qty != 0 and (cur > 0) != (target_qty > 0):
                        orders.append(self.submit(sym, -cur, reason + " (close)", price, job["name"]))
                        delta = target_qty
                    if abs(delta) > 0 and (crypto or abs(delta) >= 1):
                        orders.append(self.submit(sym, delta, reason, price, job["name"]))
                    orders = [o for o in orders if o is not None]
                    traded = round(target_qty - cur, 4) if orders else 0.0
                    action = ("buy" if traded > 0 else "sell") if orders else "no change"
                    if override and orders:
                        action = "flatten before close"
                    fill = self.fill_price(orders[-1].id) if orders else None
                    pending.append(dict(job=job, sym=sym, ctx=ctx, df=df, crypto=crypto, at=datetime.now(timezone.utc).isoformat(),
                                        held_after=target_qty if orders else cur,
                                        bar_args=(job["name"], sym, ctx, cur, target_qty, action, abs(traded),
                                                  [str(o.id) for o in orders], fill, override)))
                    status["symbols"][sym] = {"job": job["name"], "strategy": job["strategy"], "price": price,
                                              "signal": target_dir, "position": cur, "target": target_qty, "active": active}
        finally:  # display-only extras, after every order of this bar has been sent (also when one failed)
            self._annotate(pending, status)
        (LOGS / "status.json").write_text(json.dumps(status, indent=2))

    def _annotate(self, pending: list, status: dict):
        """For the Live page and the decision log only (never used for trading): the price that
        would change each signal at the next bar's close (autobot.trigger) and, for held
        positions, the best price since entry. Bounded by TRIGGER_BUDGET_S per bar so a slow
        meta-strategy can't hold the loop up; every step is guarded by _safe."""
        deadline = time.time() + TRIGGER_BUDGET_S
        trades = self._safe(self._trade_rows) or []
        for p in pending:
            self._progress = time.time()
            sym, job = p["sym"], p["job"]
            trig = peak = None
            if time.time() < deadline:
                trig = self._safe(trigger.trigger_level, job["strategy"], p["df"], job.get("params") or {},
                                  bool(self.cfg.get("allow_short", False)), p["crypto"])
                peak = self._safe(lambda: trigger.peak_since(p["df"], trigger.entry_time(trades, sym, p["held_after"]), p["held_after"]))
            else:
                status["symbols"][sym]["trigger_note"] = "not computed this bar (time budget)"
            status["symbols"][sym]["trigger"] = trig
            status["symbols"][sym]["peak"] = peak
            self._safe(self.rec.bar, *p["bar_args"], extra={"time": p["at"], "trigger": trig, "peak_since_entry": peak})

    def _trade_rows(self) -> list:
        f = LOGS / "trades.csv"
        if not f.exists():
            return []
        with open(f, newline="") as fh:
            return list(csv.DictReader(fh))

    # ------------------------------------------------------------ watchdog
    def _watchdog(self, started: float, tick: float = 1.0):
        """Runs beside the main loop. Heartbeat only while the loop progresses; takes over
        STOP and the loss limit when the loop is stuck; exits the process on a long stall.
        It must never die: every check is guarded and the loop keeps going."""
        state = {"wtc": None, "last_risk": 0.0}
        last_tick = time.time()
        while not self._stop_watch.wait(tick):
            now = time.time()
            if now - last_tick > SLEEP_GAP_S:  # the PC slept: don't count that time as a stuck loop
                log.warning("WAKE: watchdog resumed after %.0f min without ticks (PC sleep?)", (now - last_tick) / 60)
                self._progress = now
            last_tick = now
            try:
                if self._watch_once(started, state):
                    return
            except Exception as e:  # noqa: BLE001
                log.error("WATCHDOG error (continuing): %s", e)

    def _watch_once(self, started: float, state: dict) -> bool:
        """One watchdog check. Returns True when the process is exiting."""
        idle = time.time() - self._progress
        if idle < self.stall_s:
            write_lock(started)
        if STOP_FILE.exists() and time.time() - STOP_FILE.stat().st_mtime > 10:
            log.warning("WATCHDOG: STOP file not handled by the main loop; flattening and exiting")
            state["wtc"] = state["wtc"] or self._watch_tc_factory()
            state["wtc"].close_all_positions(cancel_orders=True)
            STOP_FILE.unlink(missing_ok=True)
            release_lock()
            self._exit(0)
            return True
        if idle > self.watch_risk_after and time.time() - state["last_risk"] > 30 and not self.halted:
            state["last_risk"] = time.time()
            state["wtc"] = state["wtc"] or self._watch_tc_factory()
            eq = float(state["wtc"].get_account().equity)
            dd = (eq / self.day_start_equity - 1) * 100
            if dd <= -abs(self.risk["max_daily_loss_pct"]):
                log.critical("WATCHDOG: main loop stuck and daily loss %.2f%% hit the limit; flattening", dd)
                state["wtc"].close_all_positions(cancel_orders=True)
                self.halted = True
            elif self.profit_lock_hit(dd):
                log.critical("WATCHDOG: main loop stuck and the profit lock triggered (day P&L %.2f%%, peak %.2f%%); flattening",
                             dd, self.day_peak_pnl)
                state["wtc"].close_all_positions(cancel_orders=True)
                self._safe(self.rec.event, "profit_lock", f"Profit lock (watchdog): the day's gain fell from {self.day_peak_pnl:.2f}% "
                                                          f"to {dd:.2f}% while the bot was stuck; the watchdog sold everything.")
                self.halted = True
        if idle >= self.stall_s:
            log.critical("WATCHDOG: main loop made no progress for %.0fs; exiting without trading. "
                         "Positions are left as they are.", idle)
            release_lock()
            self._exit(EXIT_STALLED)
            return True
        return False

    def run(self, once=False) -> int:
        started = time.time()
        last_bar = None
        last_turn = time.time()
        write_lock(started)
        self._progress = time.time()
        watch = threading.Thread(target=self._watchdog, args=(started,), name="watchdog", daemon=True)
        watch.start()
        try:
            while True:
                turn = time.time()
                if turn - last_turn > SLEEP_GAP_S:
                    log.warning("WAKE: resumed after %.0f min (PC sleep?); re-reading the market clock", (turn - last_turn) / 60)
                    self._safe(self.rec.event, "wake", f"The bot resumed after {(turn - last_turn) / 60:.0f} minutes without running "
                                                       "(the PC was probably asleep).")
                    last_bar = None  # act at the next bar with a fresh clock
                last_turn = turn
                self._progress = turn
                if self._quit.is_set():
                    break
                if STOP_FILE.exists():
                    self.flatten_all("STOP file found")
                    STOP_FILE.unlink(missing_ok=True)
                    break
                now = datetime.now(timezone.utc)
                bar = int(now.timestamp() // (self.step * 60))
                # act a few seconds after each bar boundary, once per bar
                if bar != last_bar and (now.timestamp() % (self.step * 60)) >= 3:
                    last_bar = bar
                    try:
                        self.step_once()
                    except Exception as e:  # noqa: BLE001
                        log.exception("step failed: %s", e)
                    if once:
                        break
                self._quit.wait(self.cfg.get("poll_seconds", 5))
        finally:
            self._stop_watch.set()
            release_lock()
        return EXIT_OK


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--flatten", action="store_true")
    a = ap.parse_args()
    setup_logging()
    state = bot_state()
    if (state["running"] or state["unresponsive"]) and not a.flatten:
        log.info("Another bot is already running (pid %s); this copy exits.", state["pid"])
        sys.exit(EXIT_ALREADY_RUNNING)
    cfg_path = Path(a.config)
    cfg = yaml.safe_load(cfg_path.read_text())
    wait_for_network()
    bot = Bot(cfg, cfg_path=cfg_path)
    if a.flatten:
        bot.flatten_all("--flatten")
        return
    bot.run(once=a.once)


if __name__ == "__main__":
    main()
