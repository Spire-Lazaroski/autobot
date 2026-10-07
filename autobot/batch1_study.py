"""
Batch 1 of the user's strategy list (2026-10-07): nine classic trading ideas, tested on the data we
have. Diagnosis only: nothing here changes the bot.

    .venv-ml/Scripts/python -m autobot.batch1_study --part all     # data/batch1_study.json
    parts: fib, rr, gap, sweep, profile, shortfade, statarb, events, vrp

Common rules (fixed before any run): the 11 symbols' 5-minute bars (2 years), regular hours; results
on the same 386 unseen sessions as the other studies (sessions 121 on); one trade at a time per
symbol and rule; every trade is closed by the 15:50 bar's close (the bot is flat at every close);
costs 2 bps per side (twice for fills at the opening bar or in the close-out bar). Limit fills
(targets, retracement entries) need the price to trade at least 1 cent beyond; when a bar reaches both
the stop and the target, the stop counts first. Results: per trade (net bps with a 95% range clustered
by day, win rate), and per day vs cash (equal weight over symbols). Long and short sides are reported
separately, which answers the short-selling question for every rule.

  fib        Fibonacci retracement: after a swing (the day's low then high, at least 0.5% apart, from
             10:00), buy the pullback to 38.2 / 50 / 61.8%, stop under the swing low, target the swing
             high; mirrored for down swings (short)
  rr         0.5 : 2 risk / reward on the bot's entry signals (11 strategies, no quick re-entry): stop
             0.5 x and target 2 x the 20-bar average range; also 1 : 2; vs the strategy's own exit
  gap        gap fill (fade 0.2-1% and over 1% gaps at the open, target yesterday's close, stop at
             twice the gap) and gap-and-go (over 1% gaps whose first bar follows through)
  sweep      stop hunt / liquidity grab: a bar pokes beyond yesterday's high/low (or the first 30 minutes'
             range) and closes back inside: trade the reversal, stop beyond the poke, target 2 x risk
  profile    volume profile and market profile (time at price) of the previous session from 5-minute
             bars (an approximation: true profiles need every trade): the 80% rule (open outside the
             value area, back inside for 2 bars -> to the other side) and value-area breakouts
  shortfade  overbought fade (RSI 14 > 80 and above the upper Bollinger band: short, target the day's
             average price, stop 1 range above)
  statarb    residual reversal (Avellaneda & Lee 2010): each stock's move not explained by SPY and QQQ
             (betas from the previous 60 sessions), traded against its last hour when it is 2 standard
             deviations out, hedged; plus a nonlinear version (trees forecasting the next hour's residual)
  events     earnings (keyword-scored stories, direction from their tone) and dividend raises/initiations
             or cuts in the news: next open, held 1 / 5 / 20 days, vs SPY over the same days (daily;
             note the bot is flat every night, so this is evidence only)
  vrp        the option seller's premium (theta decay, economics only): the VIX against the S&P 500's
             volatility over the next month, and a 1-month variance-swap seller's result, 2016-2026
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import cost_study as cs
from . import indicators as ind
from . import recalibrate as rc
from . import trade_rules as tr
from .short_tp_study import _paired, cached_symbols
from .strategies import STRATEGIES, get_signal

NY = "America/New_York"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "batch1_study.json"
FIRST_TEST_SESSION = 120
CLOSEOUT_MIN, OPEN_MIN, LAST_ENTRY_MIN = 950, 570, 920
TICK = 0.01
RULES = {"no_quick_reentry": True}


# ----------------------------------------------------------------- shared: one symbol's bars
class Bars:
    def __init__(self, df: pd.DataFrame, side_cost: float):
        ny = df.index.tz_convert(NY)
        self.df = df
        self.o, self.h, self.l, self.c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        self.v = df.volume.to_numpy(float)
        self.day = np.asarray(ny.date)
        self.minute = np.asarray(ny.hour * 60 + ny.minute)
        self.days = sorted(set(self.day))
        self.test_days = set(self.days[FIRST_TEST_SESSION:])
        self.side = side_cost
        n = len(df)
        self.starts = np.flatnonzero(np.r_[True, self.day[1:] != self.day[:-1]])
        self.ends = np.r_[self.starts[1:], n]
        self.closeout = np.zeros(n, int)                       # index of each bar's close-out bar
        for a, b in zip(self.starts, self.ends):
            k = a + int(np.searchsorted(self.minute[a:b], CLOSEOUT_MIN))
            self.closeout[a:b] = min(k, b - 1)
        prev_c = np.r_[np.nan, self.c[:-1]]
        trr = np.nanmax(np.c_[self.h - self.l, np.abs(self.h - prev_c), np.abs(self.l - prev_c)], axis=1)
        self.atr = pd.Series(trr).rolling(20).mean().to_numpy()

    def is_test(self, i: int) -> bool:
        return self.day[i] in self.test_days


def simulate(B: Bars, entries: List[Dict]) -> List[Dict]:
    """entries: {i, px, side (+1/-1), stop, target (or None), same_bar, open_fill}; filled at px on bar
    i (at its close; inside the bar when same_bar, e.g. a limit or the open, and then the stop and target
    are checked from bar i itself); open_fill = filled at the opening bar (costs 2x). One trade at a
    time; returns the trades."""
    trades, busy_until = [], -1
    for e in sorted(entries, key=lambda x: x["i"]):
        i = e["i"]
        if i <= busy_until or not B.is_test(i) or B.minute[i] >= CLOSEOUT_MIN:
            continue
        side, px, stop, tgt = e["side"], e["px"], e["stop"], e.get("target")
        end = B.closeout[i]
        k0 = i if e.get("same_bar") else i + 1
        exit_px, kind, k = B.c[end], "close", end
        for k in range(k0, end + 1):
            if side > 0:
                if B.l[k] <= stop:
                    exit_px, kind = min(stop, B.o[k]) if k > i else stop, "stop"
                    break
                if tgt is not None and B.h[k] >= tgt + TICK:
                    exit_px, kind = max(tgt, B.o[k]) if k > i else tgt, "target"
                    break
            else:
                if B.h[k] >= stop:
                    exit_px, kind = max(stop, B.o[k]) if k > i else stop, "stop"
                    break
                if tgt is not None and B.l[k] <= tgt - TICK:
                    exit_px, kind = min(tgt, B.o[k]) if k > i else tgt, "target"
                    break
        else:
            k = end
        if kind == "close":
            exit_px, k = B.c[end], end
        c_in = B.side * (2 if e.get("open_fill") else 1)
        c_out = B.side * (2 if kind == "close" else 1)
        gross = side * (exit_px / px - 1)
        trades.append({"day": B.day[i], "side": side, "net": gross - c_in - c_out, "kind": kind})
        busy_until = k
    return trades


def summarize(trades_by_symbol: Dict[str, List[Dict]], test_days: List) -> Dict:
    """Per side and both: trades, win rate, net bps per trade (95% range by day), per-day account vs cash."""
    out = {}
    for side_name, sel in (("both", None), ("long", 1), ("short", -1)):
        rows = [dict(t, symbol=s) for s, ts in trades_by_symbol.items() for t in ts if sel is None or t["side"] == sel]
        if not rows:
            out[side_name] = {"trades": 0}
            continue
        T = pd.DataFrame(rows)
        cm = cs.clustered_mean(T.net.to_numpy(), T.day.astype(str).to_numpy())
        per_sym = []
        for s in trades_by_symbol:
            d = T[T.symbol == s].groupby("day").net.sum().reindex(test_days, fill_value=0.0)
            per_sym.append(d.to_numpy())
        acc = pd.Series(np.mean(per_sym, axis=0), index=pd.to_datetime(test_days))
        out[side_name] = {"trades": int(len(T)), "win_rate_%": round(float((T.net > 0).mean() * 100), 1),
                          "net_bps_per_trade": round(cm["mean"] * 1e4, 2),
                          "ci95_bps": [round(cm["lo"] * 1e4, 2) if cm["lo"] is not None else None,
                                       round(cm["hi"] * 1e4, 2) if cm["hi"] is not None else None],
                          "total_%": round(float(((1 + acc).prod() - 1) * 100), 2), "vs_cash": _paired(acc, acc * 0),
                          "exits": T.kind.value_counts().to_dict()}
    return out


# ----------------------------------------------------------------- 1. Fibonacci retracement
def fib_entries(B: Bars, f: float) -> List[Dict]:
    out = []
    for a, b in zip(B.starts, B.ends):
        hi, lo, hi_i, lo_i = B.h[a], B.l[a], a, a
        done_long = done_short = False
        for k in range(a + 1, b):
            m = B.minute[k]
            if m >= 600 and m < LAST_ENTRY_MIN and hi / lo - 1 >= 0.005:
                rng = hi - lo
                if not done_long and lo_i < hi_i:                        # swing up: buy the pullback
                    lvl = hi - f * rng
                    if B.l[k] <= lvl - TICK and B.o[k] > lvl:
                        out.append({"i": k, "px": lvl, "side": 1, "stop": lo - TICK, "target": hi, "same_bar": True})
                        done_long = True
                if not done_short and hi_i < lo_i:                       # swing down: sell the bounce
                    lvl = lo + f * rng
                    if B.h[k] >= lvl + TICK and B.o[k] < lvl:
                        out.append({"i": k, "px": lvl, "side": -1, "stop": hi + TICK, "target": lo, "same_bar": True})
                        done_short = True
            if B.h[k] > hi:
                hi, hi_i = B.h[k], k
            if B.l[k] < lo:
                lo, lo_i = B.l[k], k
    return out


# ----------------------------------------------------------------- 2. 0.5 : 2 risk / reward on the bot's entries
def rr_entries(B: Bars, sig: np.ndarray, k_stop: float, k_tgt: float) -> List[Dict]:
    out = []
    for i in range(1, len(sig)):
        if sig[i] > 0 and (sig[i - 1] <= 0 or B.day[i] != B.day[i - 1]) and np.isfinite(B.atr[i]) and B.minute[i] < LAST_ENTRY_MIN:
            out.append({"i": i, "px": B.c[i], "side": 1, "stop": B.c[i] - k_stop * B.atr[i], "target": B.c[i] + k_tgt * B.atr[i]})
    return out


# ----------------------------------------------------------------- 3. gaps
def gap_entries(B: Bars, kind: str) -> List[Dict]:
    out = []
    for n, (a, b) in enumerate(zip(B.starts, B.ends)):
        if n == 0:
            continue
        pc = B.c[a - 1]
        g = B.o[a] / pc - 1
        sgn = np.sign(g)
        if kind in ("fade_small", "fade_large"):
            ok = 0.002 <= abs(g) < 0.01 if kind == "fade_small" else abs(g) >= 0.01
            if ok:
                out.append({"i": a, "px": B.o[a], "side": -sgn, "target": pc, "stop": B.o[a] + sgn * abs(B.o[a] - pc),
                            "same_bar": True, "open_fill": True})
        elif kind == "go_large" and abs(g) >= 0.01 and sgn * (B.c[a] - B.o[a]) > 0:
            out.append({"i": a, "px": B.c[a], "side": sgn, "target": None, "stop": B.l[a] - TICK if sgn > 0 else B.h[a] + TICK})
    return out


# ----------------------------------------------------------------- 4. stop hunt / liquidity grab
def sweep_entries(B: Bars, levels: str) -> List[Dict]:
    out = []
    for n, (a, b) in enumerate(zip(B.starts, B.ends)):
        if levels == "prev_day":
            if n == 0:
                continue
            pa, pb = B.starts[n - 1], B.ends[n - 1]
            top, bot = B.h[pa:pb].max(), B.l[pa:pb].min()
        else:                                                            # the first 30 minutes' range
            orr = slice(a, min(a + 6, b))
            top, bot = B.h[orr].max(), B.l[orr].min()
        done_hi = done_lo = False
        for k in range(a + 6, b):
            if not (600 <= B.minute[k] < LAST_ENTRY_MIN):
                continue
            if not done_hi and B.h[k] >= top + TICK and B.c[k] < top:
                risk = B.h[k] + TICK - B.c[k]
                out.append({"i": k, "px": B.c[k], "side": -1, "stop": B.h[k] + TICK, "target": B.c[k] - 2 * risk})
                done_hi = True
            if not done_lo and B.l[k] <= bot - TICK and B.c[k] > bot:
                risk = B.c[k] - (B.l[k] - TICK)
                out.append({"i": k, "px": B.c[k], "side": 1, "stop": B.l[k] - TICK, "target": B.c[k] + 2 * risk})
                done_lo = True
    return out


# ----------------------------------------------------------------- 5. volume / market profile
def profile(h: np.ndarray, l: np.ndarray, w: np.ndarray, ref: float, va: float = 0.70):
    """POC, value-area low / high from bars' ranges, each bar's weight spread evenly over its range."""
    step = ref * 0.0005
    lo0 = l.min()
    nb = int((h.max() - lo0) / step) + 1
    hist = np.zeros(nb)
    for hh, ll, ww in zip(h, l, w):
        i0, i1 = int((ll - lo0) / step), int((hh - lo0) / step)
        hist[i0:i1 + 1] += ww / (i1 - i0 + 1)
    poc = int(hist.argmax())
    lo_i = hi_i = poc
    tot, acc = hist.sum(), hist[poc]
    while acc < va * tot and (lo_i > 0 or hi_i < nb - 1):
        up = hist[hi_i + 1] if hi_i < nb - 1 else -1
        dn = hist[lo_i - 1] if lo_i > 0 else -1
        if up >= dn:
            hi_i += 1
            acc += up
        else:
            lo_i -= 1
            acc += dn
    return lo0 + (poc + 0.5) * step, lo0 + lo_i * step, lo0 + (hi_i + 1) * step


def profile_entries(B: Bars, weight: str, rule: str) -> List[Dict]:
    out = []
    for n, (a, b) in enumerate(zip(B.starts, B.ends)):
        if n == 0:
            continue
        pa, pb = B.starts[n - 1], B.ends[n - 1]
        w = B.v[pa:pb] if weight == "volume" else np.ones(pb - pa)
        if w.sum() <= 0:
            continue
        poc, val, vah = profile(B.h[pa:pb], B.l[pa:pb], w, B.c[pb - 1])
        o = B.o[a]
        inside = 0
        for k in range(a, b):
            if B.minute[k] >= LAST_ENTRY_MIN:
                break
            c = B.c[k]
            if rule == "80pct" and (o > vah or o < val):
                inside = inside + 1 if val < c < vah else 0
                if inside == 2:
                    hi_so_far, lo_so_far = B.h[a:k + 1].max(), B.l[a:k + 1].min()
                    if o > vah:
                        out.append({"i": k, "px": c, "side": -1, "target": val, "stop": hi_so_far + TICK})
                    else:
                        out.append({"i": k, "px": c, "side": 1, "target": vah, "stop": lo_so_far - TICK})
                    break
            elif rule == "breakout" and val <= o <= vah:
                above = c > vah and B.c[k - 1] > vah if k > a else False
                below = c < val and B.c[k - 1] < val if k > a else False
                if above and c > poc:
                    out.append({"i": k, "px": c, "side": 1, "stop": poc, "target": c + 2 * (c - poc)})
                    break
                if below and c < poc:
                    out.append({"i": k, "px": c, "side": -1, "stop": poc, "target": c - 2 * (poc - c)})
                    break
    return out


# ----------------------------------------------------------------- 6. overbought fade (short)
def shortfade_entries(B: Bars) -> List[Dict]:
    c = pd.Series(B.c)
    rsi = ind.rsi(c, 14).to_numpy()
    _, _, up = ind.bollinger(c, 20, 2.0)
    vwap = ind.session_vwap(B.df).to_numpy()
    out = []
    for k in range(25, len(B.c)):
        if 600 <= B.minute[k] < LAST_ENTRY_MIN and rsi[k] > 80 and B.c[k] > up.iloc[k] and vwap[k] < B.c[k] and np.isfinite(B.atr[k]):
            out.append({"i": k, "px": B.c[k], "side": -1, "stop": B.c[k] + B.atr[k], "target": vwap[k]})
    return out


# ----------------------------------------------------------------- runners for the bar-level parts
def _bars(symbols: Optional[List[str]] = None):
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    syms = symbols or cached_symbols()
    data = cs.Data(syms)
    r = rc.settings(cfg)
    bars = {s: Bars(data.bars(s, "5Min"), rc.costs_for(s, r).per_turn) for s in syms}
    test_days = sorted(set(bars["SPY"].days[FIRST_TEST_SESSION:])) if "SPY" in bars else sorted(next(iter(bars.values())).test_days)
    return cfg, r, data, bars, test_days


def run_rules(make, variants: Dict[str, dict], bars: Dict[str, Bars], test_days: List, progress=print) -> Dict:
    res = {}
    for name, kw in variants.items():
        trades = {s: simulate(B, make(B, **kw)) for s, B in bars.items()}
        res[name] = summarize(trades, test_days)
        b = res[name]["both"]
        progress(f"  {name}: {b.get('trades', 0)} trades, {b.get('net_bps_per_trade', 'n/a')} bps/trade, total {b.get('total_%', 'n/a')}%")
    return res


def part_rr(cfg, r, data, bars, test_days, progress=print) -> Dict:
    from .scalp_study import simulate as strat_alone
    names = cs.strategies_for("5Min", r)
    res = {}
    for label, ks, kt in (("strategy exit", None, None), ("0.5 : 2", 0.5, 2.0), ("1 : 2", 1.0, 2.0)):
        trades = {}
        for s, B in bars.items():
            ts = []
            for n in names:
                try:
                    sig = tr.apply(get_signal(n, B.df, STRATEGIES[n].defaults, False), B.df, RULES).to_numpy(float)
                except Exception:  # noqa: BLE001
                    continue
                if ks is None:
                    test = np.array([B.is_test(i) for i in range(len(sig))])
                    for d, ret, kind in strat_alone(B.o, B.h, B.l, B.c, sig, B.minute, B.day, test, B.side, None, None):
                        ts.append({"day": d, "side": 1, "net": ret, "kind": kind})
                else:
                    ts += simulate(B, rr_entries(B, sig, ks, kt))
            trades[s] = ts
        res[label] = summarize(trades, test_days)
        b = res[label]["both"]
        progress(f"  {label}: {b['trades']} trades, win {b['win_rate_%']}%, {b['net_bps_per_trade']} bps/trade")
    return res


# ----------------------------------------------------------------- 7. statistical arbitrage on residuals
def part_statarb(cfg, r, data, bars, test_days, progress=print) -> Dict:
    import lightgbm as lgb
    stocks = [s for s in bars if s not in ("SPY", "QQQ")]
    idx = bars["SPY"].df.index
    rets = pd.DataFrame({s: bars[s].df.close.reindex(idx).pct_change() for s in bars}).fillna(0.0) * 1e4
    day = bars["SPY"].day
    minute = bars["SPY"].minute
    first = np.r_[True, day[1:] != day[:-1]]
    rets[first] = 0.0
    days = bars["SPY"].days
    H = 12
    res_lin = {s: [] for s in stocks}
    res_nl = {s: [] for s in stocks}
    for k in range(FIRST_TEST_SESSION, len(days), 20):
        fm, tm = np.isin(day, days[k - 60:k]), np.isin(day, days[k:k + 20])
        X_tr, y_tr, pack = [], [], {}
        for s in stocks:
            A = np.c_[np.ones(fm.sum()), rets.SPY[fm], rets.QQQ[fm]]
            beta = np.linalg.lstsq(A, rets[s][fm], rcond=None)[0]
            e = (rets[s] - beta[1] * rets.SPY - beta[2] * rets.QQQ).to_numpy()
            sd = e[fm].std()
            cum = pd.Series(e).groupby(day).transform(lambda x: x.rolling(H, min_periods=H).sum()).to_numpy()
            score = cum / (sd * math.sqrt(H))
            fut = pd.Series(e).groupby(day).transform(lambda x: x[::-1].rolling(H, min_periods=1).sum()[::-1].shift(-1)).to_numpy()
            feats = np.c_[score, np.r_[np.nan, e[:-1]], e, pd.Series(np.abs(e)).rolling(12).mean(), minute, rets.SPY.rolling(6).sum()]
            pack[s] = (beta, e, score, feats, fut)
            ok = fm & np.isfinite(fut) & np.isfinite(feats).all(axis=1)
            X_tr.append(feats[ok]); y_tr.append(fut[ok])
        mdl = lgb.LGBMRegressor(n_estimators=200, learning_rate=0.03, num_leaves=15, min_child_samples=300, verbose=-1).fit(
            np.vstack(X_tr), np.concatenate(y_tr))
        for s in stocks:
            beta, e, score, feats, fut = pack[s]
            gross = 1 + abs(beta[1]) + abs(beta[2])
            cost_bps = (bars[s].side + bars["SPY"].side * abs(beta[1]) + bars["QQQ"].side * abs(beta[2])) / gross * 1e4
            ti = np.flatnonzero(tm)
            pred = np.full(len(e), np.nan)
            okp = tm & np.isfinite(feats).all(axis=1)
            pred[okp] = mdl.predict(feats[okp])
            for label, out in (("linear", res_lin), ("trees", res_nl)):
                pos = 0.0
                for i in ti:
                    if day[i] != day[i - 1]:
                        pos = 0.0
                    new = pos
                    if minute[i] >= CLOSEOUT_MIN:
                        new = 0.0
                    elif label == "linear":
                        if pos == 0 and minute[i] < LAST_ENTRY_MIN and np.isfinite(score[i]) and 2 <= abs(score[i]) < 4:
                            new = -np.sign(score[i])
                        elif pos != 0 and (abs(score[i]) <= 0.5 or abs(score[i]) >= 4):
                            new = 0.0
                    else:
                        p = pred[i]
                        if np.isfinite(p) and minute[i] < LAST_ENTRY_MIN and abs(p) * 1 / gross > 2 * cost_bps:
                            new = np.sign(p)
                        elif pos != 0 and (not np.isfinite(p) or np.sign(p) != pos):
                            new = 0.0
                    mult = 2.0 if minute[i] >= CLOSEOUT_MIN else 1.0
                    pnl = pos * e[i] / gross - abs(new - pos) * cost_bps * mult           # e[i]: this bar, held from the last close
                    out[s].append((day[i], pnl / 1e4, int(new != pos and new != 0), np.sign(new) if new != pos and new != 0 else 0))
                    pos = new
        progress(f"  window from {days[k]} done")
    res = {}
    for label, out in (("linear residual reversal", res_lin), ("trees on residuals", res_nl)):
        per, n_tr = [], 0
        for s in stocks:
            d = pd.DataFrame(out[s], columns=["day", "pnl", "entry", "side"])
            n_tr += int(d.entry.sum())
            per.append(d.groupby("day").pnl.sum().reindex(test_days, fill_value=0.0).to_numpy())
        acc = pd.Series(np.mean(per, axis=0), index=pd.to_datetime(test_days))
        res[label] = {"stocks": stocks, "trades": n_tr, "total_%": round(float(((1 + acc).prod() - 1) * 100), 2), "vs_cash": _paired(acc, acc * 0)}
        progress(f"  {label}: {n_tr} trades, total {res[label]['total_%']}%")
    return res


# ----------------------------------------------------------------- 8. earnings and dividend news
DIV_UP = re.compile(r"dividend", re.I)
DIV_RAISE = re.compile(r"\b(raise|raises|raised|increase|increases|increased|hike|hikes|boost|boosts|initiat\w*|special)\b", re.I)
DIV_CUT = re.compile(r"\b(cut|cuts|slash\w*|suspend\w*|eliminat\w*)\b", re.I)


def part_events(cfg, r, data, bars, test_days, progress=print) -> Dict:
    from .event_study import news_events
    ev = news_events(with_first=False)
    ev = ev[ev.relevance == "main"]
    stocks = [s for s in bars if s not in ("SPY", "QQQ", "GLD", "SLV", "USO")]
    daily = {s: data.bars(s, "1Day") for s in bars}
    spy = daily["SPY"]
    out = {}
    for kind in ("earnings", "dividend"):
        rows = []
        for s in stocks:
            e = ev[ev.symbol == s]
            if kind == "earnings":
                e = e[e.category == "earnings"]
            else:
                e = e[e.headline.str.contains(DIV_UP) & ~e.headline.str.contains("ex-dividend", case=False)]
            if e.empty:
                continue
            e = e.assign(nyday=e.t_info.dt.tz_convert(NY).dt.date)
            d = daily[s]
            dd = np.asarray(d.index.tz_convert(NY).date)
            for nyday, g in e.groupby("nyday"):
                if kind == "earnings":
                    tone = g.sentiment.sum()
                    if tone == 0:
                        continue
                    side = 1 if tone > 0 else -1
                else:
                    h = " ".join(g.headline)
                    side = 1 if DIV_RAISE.search(h) else -1 if DIV_CUT.search(h) else 0
                    if side == 0:
                        continue
                t0 = g.t_info.min().tz_convert(NY)
                start = int(np.searchsorted(dd, nyday, side="left" if t0.hour * 60 + t0.minute < OPEN_MIN else "right"))
                if start >= len(d):
                    continue
                row = {"symbol": s, "day": str(nyday), "side": side}
                for hold in (1, 5, 20):
                    end = start + hold - 1
                    if end >= len(d):
                        continue
                    px0, px1 = float(d.open.iloc[start]), float(d.close.iloc[end])
                    sd = spy.index.get_indexer([d.index[start], d.index[end]])
                    if (sd < 0).any():
                        continue
                    m = float(spy.close.iloc[sd[1]]) / float(spy.open.iloc[sd[0]]) - 1
                    row[f"abn_{hold}d_bps"] = side * ((px1 / px0 - 1) - m) * 1e4 - 2 * rc.costs_for(s, r).per_turn * 1e4
                rows.append(row)
        R = pd.DataFrame(rows)
        res = {"events": int(len(R)), "long": int((R.side > 0).sum()) if len(R) else 0, "short": int((R.side < 0).sum()) if len(R) else 0}
        for hold in (1, 5, 20):
            col = f"abn_{hold}d_bps"
            if len(R) and col in R:
                x = R[col].dropna()
                cm = cs.clustered_mean(x.to_numpy() / 1e4, R.loc[x.index, "day"].to_numpy())
                res[f"{hold}d"] = {"n": int(len(x)), "mean_bps": round(cm["mean"] * 1e4, 1),
                                   "ci95_bps": [round(cm["lo"] * 1e4, 1) if cm["lo"] is not None else None, round(cm["hi"] * 1e4, 1) if cm["hi"] is not None else None],
                                   "win_%": round(float((x > 0).mean() * 100), 1)}
        out[kind] = res
        progress(f"  {kind}: {res}")
    return out


# ----------------------------------------------------------------- 9. the option seller's premium
def part_vrp(progress=print) -> Dict:
    from .macro_study import DIR

    def fred(n):
        f = pd.read_csv(DIR / f"{n}.csv")
        s = pd.to_numeric(f.iloc[:, 1], errors="coerce")
        s.index = pd.to_datetime(f.iloc[:, 0])
        return s.dropna()
    sp, vix = fred("SP500"), fred("VIXCLS")
    vix = vix.reindex(sp.index, method="ffill")
    r = sp.pct_change()
    n = 21
    rows = []
    for i in range(1, len(sp) - n, n):                                    # non-overlapping months
        iv = float(vix.iloc[i])
        rv = float(np.sqrt((r.iloc[i + 1:i + 1 + n] ** 2).mean() * 252) * 100)
        rows.append({"start": str(sp.index[i].date()), "vix": iv, "realized": rv, "gap": iv - rv,
                     "varswap_vega": (iv ** 2 - rv ** 2) / (2 * iv)})
    D = pd.DataFrame(rows)
    worst = D.nsmallest(3, "varswap_vega")
    cum = D.varswap_vega.cumsum()
    return {"months": int(len(D)), "from": D.start.iloc[0], "to": D.start.iloc[-1],
            "avg_vix": round(float(D.vix.mean()), 2), "avg_realized": round(float(D.realized.mean()), 2),
            "avg_gap_vol_points": round(float(D.gap.mean()), 2), "months_vix_above_realized_%": round(float((D.gap > 0).mean() * 100), 1),
            "seller_avg_per_month_vega": round(float(D.varswap_vega.mean()), 2), "seller_median_vega": round(float(D.varswap_vega.median()), 2),
            "seller_worst_months": worst[["start", "vix", "realized", "varswap_vega"]].round(2).to_dict("records"),
            "seller_total_vega": round(float(cum.iloc[-1]), 1), "seller_worst_drawdown_vega": round(float((cum - cum.cummax()).min()), 1),
            "note": "1 vega = the result per 1 volatility point of notional; a seller earns the gap most months and loses big in crashes"}


# ----------------------------------------------------------------- main
PARTS = ("fib", "rr", "gap", "sweep", "profile", "shortfade", "statarb", "events", "vrp")


def run(parts=PARTS, progress=print) -> Dict:
    res = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    need_bars = [p for p in parts if p != "vrp"]
    if need_bars:
        cfg, r, data, bars, test_days = _bars()
        res["test_sessions"], res["first_test_day"] = len(test_days), str(test_days[0])
    for p in parts:
        progress(f"part {p}")
        if p == "fib":
            res[p] = run_rules(fib_entries, {f"retrace {f:g}": {"f": f} for f in (0.382, 0.5, 0.618)}, bars, test_days, progress)
        elif p == "rr":
            res[p] = part_rr(cfg, r, data, bars, test_days, progress)
        elif p == "gap":
            res[p] = run_rules(gap_entries, {k: {"kind": k} for k in ("fade_small", "fade_large", "go_large")}, bars, test_days, progress)
        elif p == "sweep":
            res[p] = run_rules(sweep_entries, {f"sweep of {k}": {"levels": k} for k in ("prev_day", "opening_range")}, bars, test_days, progress)
        elif p == "profile":
            res[p] = run_rules(profile_entries, {f"{w} profile, {rule}": {"weight": w, "rule": rule}
                                                 for w in ("volume", "time") for rule in ("80pct", "breakout")}, bars, test_days, progress)
        elif p == "shortfade":
            res[p] = run_rules(lambda B: shortfade_entries(B), {"overbought fade": {}}, bars, test_days, progress)
        elif p == "statarb":
            res[p] = part_statarb(cfg, r, data, bars, test_days, progress)
        elif p == "events":
            res[p] = part_events(cfg, r, data, bars, test_days, progress)
        elif p == "vrp":
            res[p] = part_vrp(progress)
        res["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", nargs="+", default=["all"])
    a = ap.parse_args()
    parts = PARTS if "all" in a.part else tuple(a.part)
    run(parts)
    print("saved", OUT)


if __name__ == "__main__":
    main()
