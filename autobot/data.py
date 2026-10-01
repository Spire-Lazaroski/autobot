"""
Market data: Alpaca historical bars (stocks + crypto) with a local cache,
plus a synthetic generator used only for the offline demo / tests.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent / "data_cache"

TIMEFRAMES = {  # label -> (amount, unit)
    "1Min": (1, "Minute"), "5Min": (5, "Minute"), "15Min": (15, "Minute"),
    "30Min": (30, "Minute"), "1Hour": (1, "Hour"), "1Day": (1, "Day"),
}


def _keys():
    from dotenv import load_dotenv
    load_dotenv()
    key, sec = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError("Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env (paper keys).")
    return key, sec


# alpaca-py sends requests without a timeout, so one dead connection can block forever
# (a bot once hung for hours with a half-closed socket). Every client gets one.
HTTP_TIMEOUT = (5, 20)  # seconds: connect, read


def with_timeout(client, timeout=HTTP_TIMEOUT):
    """Give an alpaca-py client (trading or data) default request timeouts."""
    from requests import Session

    class _TimeoutSession(Session):
        def request(self, method, url, **kw):
            kw.setdefault("timeout", timeout)
            return super().request(method, url, **kw)

    old = client._session
    client._session = _TimeoutSession()
    client._session.headers.update(old.headers)
    old.close()
    return client


def wait_for_network(host: str = "paper-api.alpaca.markets", timeout_s: float = 120.0,
                     resolve=None, sleep=None, clock=None, log=None) -> bool:
    """At log-on Windows may not be connected yet; wait (up to timeout_s) until `host`
    resolves instead of crashing on the first request. Returns False if the network is
    still down, and the caller starts anyway."""
    import logging
    import socket
    import time
    resolve = resolve or (lambda h: socket.getaddrinfo(h, 443))
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    log = log or logging.getLogger("autobot")
    end, warned = clock() + timeout_s, False
    while True:
        try:
            resolve(host)
            if warned:
                log.info("NETWORK %s reachable, starting", host)
            return True
        except OSError:
            if clock() >= end:
                log.warning("NETWORK %s still not reachable after %.0f s; starting anyway", host, timeout_s)
                return False
            if not warned:
                log.info("NETWORK waiting for %s (the PC may still be connecting)", host)
                warned = True
            sleep(5)


def _tf(label: str):
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    amt, unit = TIMEFRAMES[label]
    return TimeFrame(amt, getattr(TimeFrameUnit, unit))


def is_crypto(symbol: str) -> bool:
    return "/" in symbol


def fetch_bars(symbols: List[str], timeframe="15Min", days=90, end: datetime | None = None,
               rth_only=True, use_cache=True, feed="iex") -> Dict[str, pd.DataFrame]:
    """Download OHLCV bars from Alpaca. Returns {symbol: df} indexed by UTC timestamp.

    feed: 'iex' is free with every Alpaca account; 'sip' needs a paid data plan.
    """
    end = end or datetime.now(timezone.utc) - timedelta(minutes=16)  # free plan: no last 15 min
    start = end - timedelta(days=days)
    CACHE_DIR.mkdir(exist_ok=True)
    out = {}
    for sym in symbols:
        cache = CACHE_DIR / f"{sym.replace('/', '-')}_{timeframe}_{days}d_{end:%Y%m%d}.csv.gz"
        if use_cache and cache.exists():
            df = pd.read_csv(cache, index_col=0, parse_dates=True)
        else:
            df = _download(sym, timeframe, start, end, feed)
            df.to_csv(cache)
        if rth_only and not is_crypto(sym) and timeframe != "1Day":
            ny = df.index.tz_convert("America/New_York")
            mins = ny.hour * 60 + ny.minute
            df = df[(mins >= 9 * 60 + 30) & (mins < 16 * 60)]
        df.attrs["symbol"] = sym  # lets news-aware strategies find this symbol's news
        out[sym] = df
    return out


def _download(sym, timeframe, start, end, feed) -> pd.DataFrame:
    key, sec = _keys()
    if is_crypto(sym):
        from alpaca.data.historical import CryptoHistoricalDataClient
        from alpaca.data.requests import CryptoBarsRequest
        client = with_timeout(CryptoHistoricalDataClient(key, sec))
        bars = client.get_crypto_bars(CryptoBarsRequest(
            symbol_or_symbols=sym, timeframe=_tf(timeframe), start=start, end=end))
    else:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        client = with_timeout(StockHistoricalDataClient(key, sec))
        bars = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=sym, timeframe=_tf(timeframe), start=start, end=end,
            adjustment=Adjustment.ALL, feed=DataFeed(feed)))
    df = bars.df
    if df.empty:
        raise RuntimeError(f"No bars returned for {sym}.")
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(sym, level="symbol")
    df.index = pd.to_datetime(df.index, utc=True)
    out = df[["open", "high", "low", "close", "volume"] + (["vwap"] if "vwap" in df else [])].copy()
    out.attrs["symbol"] = sym
    return out


# ----------------------------------------------------------------- demo data
def synthetic_bars(symbols: List[str], timeframe="15Min", days=90, seed=7) -> Dict[str, pd.DataFrame]:
    """Regime-switching random walk (trending + choppy periods). FOR TESTING ONLY:
    results on this data say nothing about real markets."""
    rng = np.random.default_rng(seed)
    step = {"1Min": 1, "5Min": 5, "15Min": 15, "30Min": 30, "1Hour": 60, "1Day": 390}[timeframe]
    per_day = max(1, 390 // step)
    sessions = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=int(days * 5 / 7))
    idx = []
    for d in sessions:
        base = pd.Timestamp(d.date()).tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30)
        idx += [base + pd.Timedelta(minutes=step * i) for i in range(per_day)]
    idx = pd.DatetimeIndex(idx).tz_convert("UTC")
    out = {}
    for k, sym in enumerate(symbols):
        n = len(idx)
        vol = 0.012 / np.sqrt(per_day)
        regime = np.repeat(rng.choice([-1, 0, 0, 1], size=n // (per_day * 5) + 1), per_day * 5)[:n]
        drift = regime * vol * 0.12
        # choppy regimes get mean-reverting noise
        eps = rng.normal(0, vol, n)
        ar = np.zeros(n)
        for i in range(1, n):
            ar[i] = (-0.25 if regime[i] == 0 else 0.05) * ar[i - 1] + eps[i]
        close = (100 + 20 * k) * np.exp(np.cumsum(drift + ar))
        opn = np.r_[close[0], close[:-1]]
        spread = np.abs(rng.normal(0, vol, n)) * close
        df = pd.DataFrame({"open": opn, "high": np.maximum(opn, close) + spread,
                           "low": np.minimum(opn, close) - spread, "close": close,
                           "volume": rng.integers(5_000, 50_000, n).astype(float)}, index=idx)
        out[sym] = df
    return out
