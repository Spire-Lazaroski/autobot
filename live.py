"""
Paper-trading runner.

    python live.py              # run the jobs in config.yaml on your Alpaca PAPER account
    python live.py --once       # evaluate one bar and exit (good for testing)
    python live.py --flatten    # close every position and exit

Create a file named STOP in this folder (or click Stop in the dashboard) to
flatten everything and shut down cleanly.

Paper only: TradingClient is created with paper=True and there is no switch to change it.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

from autobot.data import TIMEFRAMES, _download, _keys, is_crypto
from autobot.strategies import get_signal

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
STOP_FILE = ROOT / "STOP"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.FileHandler(LOGS / "bot.log"), logging.StreamHandler()])
log = logging.getLogger("autobot")


def tf_minutes(label: str) -> int:
    amt, unit = TIMEFRAMES[label]
    return amt * {"Minute": 1, "Hour": 60, "Day": 1440}[unit]


class Bot:
    def __init__(self, cfg: dict):
        from alpaca.trading.client import TradingClient
        key, sec = _keys()
        self.tc = TradingClient(key, sec, paper=True)  # <- hard-wired to paper
        self.cfg = cfg["live"]
        self.tf = self.cfg["timeframe"]
        self.step = tf_minutes(self.tf)
        self.risk = self.cfg["risk"]
        self.jobs = [j for j in self.cfg["jobs"] if j.get("enabled", True)]
        seen = {}
        for j in self.jobs:
            for s in j["symbols"]:
                if s in seen:
                    raise ValueError(f"{s} is in jobs '{seen[s]}' and '{j['name']}'. One job per symbol.")
                seen[s] = j["name"]
        acct = self.tc.get_account()
        self.day_start_equity = float(acct.last_equity)
        self.halted = False
        log.info("Paper account %s | equity $%s | jobs: %s", acct.account_number, acct.equity,
                 ", ".join(f"{j['name']}({j['strategy']}:{'/'.join(j['symbols'])})" for j in self.jobs))

    # ------------------------------------------------------------ broker helpers
    def positions(self) -> dict:
        return {p.symbol: float(p.qty) for p in self.tc.get_all_positions()}

    @staticmethod
    def _pos_key(sym: str) -> str:
        return sym.replace("/", "")  # Alpaca reports BTC/USD positions as BTCUSD

    def submit(self, sym: str, qty: float, reason: str, price: float, job: str):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        if qty == 0:
            return
        side = OrderSide.BUY if qty > 0 else OrderSide.SELL
        tif = TimeInForce.GTC if is_crypto(sym) else TimeInForce.DAY
        order = self.tc.submit_order(MarketOrderRequest(symbol=sym, qty=abs(qty), side=side, time_in_force=tif))
        log.info("ORDER %s %s %s @~%.2f (%s) id=%s", side.value, abs(qty), sym, price, reason, order.id)
        new = not (LOGS / "trades.csv").exists()
        with open(LOGS / "trades.csv", "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "job", "symbol", "side", "qty", "approx_price", "reason", "order_id"])
            w.writerow([datetime.now(timezone.utc).isoformat(), job, sym, side.value, abs(qty),
                        round(price, 4), reason, order.id])

    def flatten_all(self, why: str):
        log.warning("FLATTEN ALL: %s", why)
        self.tc.close_all_positions(cancel_orders=True)

    # ------------------------------------------------------------ core
    def recent_bars(self, sym: str) -> pd.DataFrame:
        now = datetime.now(timezone.utc)
        days = 400 if self.tf == "1Day" else max(5, math.ceil(300 * self.step / 390) + 4)
        df = _download(sym, self.tf, now - timedelta(days=days), None, "iex")
        df = df[df.index + pd.Timedelta(minutes=self.step) <= now]  # drop the still-forming bar
        if not is_crypto(sym) and self.tf != "1Day":
            ny = df.index.tz_convert("America/New_York")
            m = ny.hour * 60 + ny.minute
            df = df[(m >= 570) & (m < 960)]
        return df

    def check_risk(self) -> bool:
        acct = self.tc.get_account()
        eq = float(acct.equity)
        dd = (eq / self.day_start_equity - 1) * 100
        if dd <= -abs(self.risk["max_daily_loss_pct"]):
            self.flatten_all(f"daily loss {dd:.2f}% hit limit")
            self.halted = True
        return not self.halted

    def near_close(self, clock) -> bool:
        mins = self.risk.get("flatten_before_close_min", 0)
        return bool(mins) and clock.is_open and (clock.next_close - clock.timestamp) <= timedelta(minutes=mins)

    def step_once(self):
        clock = self.tc.get_clock()
        if not self.check_risk():
            return
        held = self.positions()
        equity = float(self.tc.get_account().equity)
        status = {"time": datetime.now(timezone.utc).isoformat(), "equity": equity,
                  "day_pnl_%": round((equity / self.day_start_equity - 1) * 100, 3),
                  "market_open": clock.is_open, "halted": self.halted, "symbols": {}}
        for job in self.jobs:
            for sym in job["symbols"]:
                crypto = is_crypto(sym)
                if not crypto and not clock.is_open:
                    continue
                cur = held.get(self._pos_key(sym), 0.0)
                try:
                    df = self.recent_bars(sym)
                    if len(df) < 50:
                        log.info("%s: only %d bars, waiting for more history", sym, len(df))
                        continue
                    sig = get_signal(job["strategy"], df, job.get("params"), self.cfg.get("allow_short", False))
                    target_dir = float(sig.iloc[-1])
                    price = float(df.close.iloc[-1])
                except Exception as e:  # noqa: BLE001
                    log.error("%s: data/signal error: %s", sym, e)
                    continue
                if not crypto and self.near_close(clock):
                    target_dir, reason = 0.0, "flatten before close"
                else:
                    reason = f"{job['strategy']} signal={target_dir:+.0f}"
                if crypto and target_dir < 0:
                    target_dir = 0.0  # no crypto shorting on Alpaca
                usd = min(equity * job["allocation_pct"] / 100, self.risk["max_position_usd"])
                raw = usd / price
                target_qty = (round(raw, 4) if crypto else math.floor(raw)) * target_dir
                delta = round(target_qty - cur, 4)
                # crossing zero (long->short) must be two orders on Alpaca
                if cur != 0 and target_qty != 0 and (cur > 0) != (target_qty > 0):
                    self.submit(sym, -cur, reason + " (close)", price, job["name"])
                    delta = target_qty
                if abs(delta) > 0 and (crypto or abs(delta) >= 1):
                    self.submit(sym, delta, reason, price, job["name"])
                status["symbols"][sym] = {"job": job["name"], "strategy": job["strategy"], "price": price,
                                          "signal": target_dir, "position": cur, "target": target_qty}
        (LOGS / "status.json").write_text(json.dumps(status, indent=2))

    def run(self, once=False):
        last_bar = None
        while True:
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
            if self.halted:
                log.warning("Halted for the day by risk limit.")
                break
            time.sleep(self.cfg.get("poll_seconds", 5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--flatten", action="store_true")
    a = ap.parse_args()
    cfg = yaml.safe_load(Path(a.config).read_text())
    bot = Bot(cfg)
    if a.flatten:
        bot.flatten_all("--flatten")
        return
    bot.run(once=a.once)


if __name__ == "__main__":
    main()
