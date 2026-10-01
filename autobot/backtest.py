"""
Vectorized backtester + parameter search.

- Signal from bar t is executed at bar t+1 (no look-ahead).
- Costs: every change in position pays (fee_bps + slippage_bps) per unit turned over.
- Parameter search ranks on the TRAIN slice and reports the untouched TEST slice,
  so you can see which strategies hold up out-of-sample instead of just curve-fitting.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from .strategies import STRATEGIES, get_signal


@dataclass
class CostModel:
    fee_bps: float = 0.0        # Alpaca stock commissions are $0; regulatory fees are tiny
    slippage_bps: float = 2.0   # realistic for liquid ETFs on market orders

    @property
    def per_turn(self) -> float:
        return (self.fee_bps + self.slippage_bps) / 1e4


def bars_per_year(index: pd.DatetimeIndex) -> float:
    if len(index) < 3:
        return 252
    step_min = pd.Series(index).diff().median().total_seconds() / 60
    if step_min >= 60 * 20:          # daily or longer
        return 252 * (1440 / step_min) if step_min < 1440 * 5 else 52
    return 252 * max(1.0, 390 / step_min)  # regular session ~390 minutes


def run_backtest(df: pd.DataFrame, position: pd.Series, costs: CostModel = CostModel()) -> pd.DataFrame:
    pos = position.reindex(df.index).fillna(0).shift(1).fillna(0)  # trade next bar
    ret = df.close.pct_change().fillna(0)
    turnover = pos.diff().abs().fillna(pos.abs())
    strat_ret = pos * ret - turnover * costs.per_turn
    equity = (1 + strat_ret).cumprod()
    return pd.DataFrame({"position": pos, "ret": strat_ret, "equity": equity,
                         "turnover": turnover, "asset_ret": ret})


def _trades(bt: pd.DataFrame) -> pd.Series:
    """Return per-trade % returns (a trade = consecutive bars with the same non-zero position)."""
    pos = bt.position
    trade_id = (pos != pos.shift()).cumsum()
    in_trade = pos != 0
    if not in_trade.any():
        return pd.Series(dtype=float)
    grp = (1 + bt.ret[in_trade]).groupby(trade_id[in_trade]).prod() - 1
    return grp


def fills(df: pd.DataFrame, bt: pd.DataFrame) -> List[Dict]:
    """Every position change as a fill for chart markers.
    A change that takes effect on bar t was decided on bar t-1 and filled at its close."""
    pos = bt.position.values
    out = []
    for t in np.flatnonzero(np.diff(pos, prepend=0.0) != 0):
        if t == 0:
            continue
        prev, new = pos[t - 1], pos[t]
        out.append({"time": df.index[t - 1], "price": float(df.close.iloc[t - 1]),
                    "side": "buy" if new > prev else "sell",
                    "kind": "entry" if abs(new) > abs(prev) or prev * new < 0 else "exit",
                    "from": float(prev), "to": float(new)})
    return out


def round_trips(df: pd.DataFrame, bt: pd.DataFrame) -> List[Dict]:
    """One row per trade (a run of bars with the same non-zero position)."""
    pos = bt.position
    tid = (pos != pos.shift()).cumsum()
    rows = []
    for _, g in bt[pos != 0].groupby(tid[pos != 0]):
        i0 = df.index.get_loc(g.index[0])
        rows.append({"entry_time": df.index[max(i0 - 1, 0)], "exit_time": g.index[-1],
                     "side": "long" if g.position.iloc[0] > 0 else "short", "bars": len(g),
                     "entry_price": float(df.close.iloc[max(i0 - 1, 0)]),
                     "exit_price": float(df.close.loc[g.index[-1]]),
                     "return_%": round(float((1 + g.ret).prod() - 1) * 100, 3)})
    return rows


def metrics(bt: pd.DataFrame) -> Dict:
    r = bt.ret
    n = len(r)
    ppy = bars_per_year(bt.index)
    total = bt.equity.iloc[-1] - 1 if n else 0.0
    years = n / ppy if ppy else 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else -1.0
    std = r.std()
    sharpe = (r.mean() / std * np.sqrt(ppy)) if std > 0 else 0.0
    dstd = r[r < 0].std()
    sortino = (r.mean() / dstd * np.sqrt(ppy)) if dstd and dstd > 0 else 0.0
    dd = bt.equity / bt.equity.cummax() - 1
    max_dd = dd.min() if n else 0.0
    tr = _trades(bt)
    wins, losses = tr[tr > 0], tr[tr < 0]
    pf = wins.sum() / -losses.sum() if len(losses) and losses.sum() != 0 else (np.inf if len(wins) else 0.0)
    return {
        "total_return_%": round(total * 100, 2),
        "cagr_%": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "max_drawdown_%": round(max_dd * 100, 2),
        "calmar": round(cagr / abs(max_dd), 2) if max_dd < 0 else 0.0,
        "trades": int(len(tr)),
        "win_rate_%": round(len(wins) / len(tr) * 100, 1) if len(tr) else 0.0,
        "avg_trade_%": round(tr.mean() * 100, 3) if len(tr) else 0.0,
        "profit_factor": round(float(pf), 2) if np.isfinite(pf) else 99.0,
        "exposure_%": round((bt.position != 0).mean() * 100, 1),
    }


# ----------------------------------------------------------------- comparisons
def compare_strategies(data: Dict[str, pd.DataFrame], strategies: Dict[str, Dict],
                       costs: CostModel = CostModel(), allow_short=False):
    """
    data:        {symbol: ohlcv df}
    strategies:  {strategy_name: params}
    Returns (summary_df, equity_curves) where equity_curves[strategy] is the
    equal-weight average equity across symbols.
    """
    rows, curves = [], {}
    for name, params in strategies.items():
        per_sym = []
        for sym, df in data.items():
            sig = get_signal(name, df, params, allow_short)
            bt = run_backtest(df, sig, costs)
            m = metrics(bt)
            rows.append({"strategy": name, "symbol": sym, **m})
            per_sym.append(bt.ret.rename(sym))
        port_ret = pd.concat(per_sym, axis=1).fillna(0).mean(axis=1)
        curves[name] = (1 + port_ret).cumprod()
    per_symbol = pd.DataFrame(rows)
    num = per_symbol.drop(columns=["symbol"]).groupby("strategy").mean(numeric_only=True).round(2)
    # portfolio-level metrics from the combined curve are more honest than averages
    for name, eq in curves.items():
        pbt = pd.DataFrame({"ret": eq.pct_change().fillna(eq.iloc[0] - 1), "equity": eq,
                            "position": 1}, index=eq.index)
        pm = metrics(pbt)
        for k in ["total_return_%", "cagr_%", "sharpe", "sortino", "max_drawdown_%", "calmar"]:
            num.loc[name, k] = pm[k]
    num = num.sort_values("sharpe", ascending=False)
    return num, pd.DataFrame(curves), per_symbol


def _valid(params: Dict) -> bool:
    if "fast" in params and "slow" in params and params["fast"] >= params["slow"]:
        return False
    if "exit" in params and "entry" in params and params["exit"] >= params["entry"]:
        return False
    return True


def param_combos(name: str, grid: Dict[str, List] | None = None) -> List[Dict]:
    """Every valid parameter set in a strategy's grid (or an override grid), defaults filled in."""
    spec = STRATEGIES[name]
    grid = spec.grid if grid is None else grid
    keys = list(grid)
    out = []
    for combo in itertools.product(*[grid[k] for k in keys]) if keys else [()]:
        params = {**spec.defaults, **dict(zip(keys, combo))}
        if _valid(params):
            out.append(params)
    return out


def parameter_search(data: Dict[str, pd.DataFrame], names: List[str] | None = None,
                     costs: CostModel = CostModel(), train_frac=0.7, allow_short=False,
                     rank_by="sharpe", progress: Optional[Callable[[float, str], None]] = None,
                     grids: Dict[str, Dict[str, List]] | None = None) -> pd.DataFrame:
    """Grid-search every strategy's parameter space. Rank on train, report test.

    progress(fraction, message) is called after each parameter set, if given.
    grids overrides the search space per strategy, e.g. {"ema_cross": {"fast": [5, 9]}}.
    """
    names = names or [n for n in STRATEGIES if STRATEGIES[n].grid]
    grids = grids or {}
    todo = [(name, params) for name in names for params in param_combos(name, grids.get(name))]
    out = []
    for i, (name, params) in enumerate(todo):
        spec = STRATEGIES[name]
        keys = list(grids.get(name, spec.grid))
        tr_m, te_m = [], []
        for sym, df in data.items():
            sig = get_signal(name, df, params, allow_short)
            bt = run_backtest(df, sig, costs)
            cut = int(len(bt) * train_frac)
            tr_m.append(metrics(bt.iloc[:cut].assign(equity=(1 + bt.ret.iloc[:cut]).cumprod())))
            te_m.append(metrics(bt.iloc[cut:].assign(equity=(1 + bt.ret.iloc[cut:]).cumprod())))
        tr = pd.DataFrame(tr_m).mean(numeric_only=True)
        te = pd.DataFrame(te_m).mean(numeric_only=True)
        out.append({
            "strategy": name,
            "params": ", ".join(f"{k}={params[k]}" for k in keys),
            "train_sharpe": round(tr["sharpe"], 2),
            "test_sharpe": round(te["sharpe"], 2),
            "train_return_%": round(tr["total_return_%"], 2),
            "test_return_%": round(te["total_return_%"], 2),
            "test_max_dd_%": round(te["max_drawdown_%"], 2),
            "test_trades": int(te["trades"]),
            "_params": params,
        })
        if progress:
            progress((i + 1) / len(todo), f"{name} {i + 1}/{len(todo)}")
    res = pd.DataFrame(out)
    if res.empty:
        return res
    return res.sort_values(f"train_{rank_by}" if f"train_{rank_by}" in res else "train_sharpe",
                           ascending=False).reset_index(drop=True)


def best_per_strategy(search: pd.DataFrame) -> pd.DataFrame:
    """Top train-ranked parameter set of each strategy, with its test score."""
    return search.groupby("strategy", as_index=False).first().sort_values("test_sharpe", ascending=False)


# ----------------------------------------------------------------- walk-forward
def _replay(df: pd.DataFrame, pos: pd.Series, costs: CostModel) -> pd.DataFrame:
    """Backtest frame for an already-effective position series (no extra shift).
    The first bar pays to enter whatever position it starts with."""
    ret = df.close.pct_change().reindex(pos.index).fillna(0)
    turnover = pos.diff().abs().fillna(pos.abs())
    r = pos * ret - turnover * costs.per_turn
    return pd.DataFrame({"position": pos, "ret": r, "equity": (1 + r).cumprod(),
                         "turnover": turnover, "asset_ret": ret})


def _slice_metric(bt: pd.DataFrame, a: int, b: int, key: str) -> float:
    part = bt.iloc[a:b]
    m = metrics(part.assign(equity=(1 + part.ret).cumprod()))
    v = m.get(key, m["sharpe"])
    return float(v) if np.isfinite(v) else -1e9


def walk_forward(data: Dict[str, pd.DataFrame], name: str, train_bars: int, test_bars: int,
                 step_bars: int | None = None, costs: CostModel = CostModel(), allow_short=False,
                 rank_by: str = "sharpe", grid: Dict[str, List] | None = None,
                 fixed_params: Dict | None = None,
                 progress: Optional[Callable[[float, str], None]] = None) -> Dict:
    """Rolling re-optimization.

    Windows are cut on the first symbol's bar clock: train on [s, s+train_bars), trade the
    best parameter set (ranked on train, averaged over symbols) on the next test_bars, move
    forward by step_bars (default test_bars). The traded test slices are stitched into one
    out-of-sample curve, which pays costs whenever the position changes, including when a
    new parameter set takes over at a window boundary.

    Returns DataFrames/dicts: windows, equity (walk_forward, fixed, buy_and_hold from the
    first test bar on), metrics for each of those, and the chosen parameters per window.
    """
    step_bars = step_bars or test_bars
    combos = param_combos(name, grid)
    if not combos:
        raise ValueError(f"{name}: no valid parameter sets to search")
    syms = list(data)
    clock = data[syms[0]].index
    n = len(clock)
    if n < train_bars + 10:
        raise ValueError(f"Need more than {train_bars + 10} bars for this window size; have {n}.")

    # each combo's full-history backtest per symbol (valid because every strategy is causal)
    bts: List[Dict[str, pd.DataFrame]] = []
    for i, p in enumerate(combos):
        bts.append({s: run_backtest(df, get_signal(name, df, p, allow_short), costs) for s, df in data.items()})
        if progress:
            progress(0.6 * (i + 1) / len(combos), f"backtesting {i + 1}/{len(combos)} parameter sets")

    def cut(sym, t):  # bar position in this symbol at or after clock time t
        return int(data[sym].index.searchsorted(t))

    starts = list(range(0, n - train_bars, step_bars))
    windows, chosen = [], []
    for w, s in enumerate(starts):
        te_a, te_b = s + train_bars, min(s + train_bars + test_bars, n)
        if te_a >= n:
            break
        t0, t1, t2 = clock[s], clock[te_a], clock[te_b - 1]
        scores = []
        for bt_by_sym in bts:
            vals = [_slice_metric(bt_by_sym[sym], cut(sym, t0), cut(sym, t1), rank_by) for sym in syms]
            scores.append(float(np.mean(vals)))
        best = int(np.argmax(scores))
        test_sh = float(np.mean([_slice_metric(bts[best][sym], cut(sym, t1), cut(sym, t2) + 1, "sharpe") for sym in syms]))
        test_ret = float(np.mean([_slice_metric(bts[best][sym], cut(sym, t1), cut(sym, t2) + 1, "total_return_%") for sym in syms]))
        chosen.append((t1, t2, best))
        windows.append({"window": w + 1, "train_start": clock[s], "train_end": clock[te_a - 1],
                        "test_start": t1, "test_end": t2, "test_bars": te_b - te_a,
                        "partial": te_b - te_a < test_bars, "params": combos[best],
                        "train_score": round(scores[best], 3), "test_sharpe": round(test_sh, 2),
                        "test_return_%": round(test_ret, 2)})
        if progress:
            progress(0.6 + 0.4 * (w + 1) / len(starts), f"window {w + 1}/{len(starts)}")
        if te_b >= n:
            break

    oos_start = chosen[0][0]
    spec = STRATEGIES[name]
    fixed = {**spec.defaults, **(fixed_params or {})}
    curves = {"walk_forward": [], "fixed": [], "buy_and_hold": []}
    trades = {"walk_forward": 0, "fixed": 0, "buy_and_hold": 0}
    per_sym_bt = {}
    for sym, df in data.items():
        oos = df.loc[df.index >= oos_start]
        pos = pd.Series(0.0, index=oos.index)
        for i, (t1, t2, best) in enumerate(chosen):
            end = chosen[i + 1][0] if i + 1 < len(chosen) else oos.index[-1] + pd.Timedelta(seconds=1)
            m = (oos.index >= t1) & (oos.index < end)
            pos[m] = bts[best][sym].position.reindex(oos.index)[m]
        full_fixed = run_backtest(df, get_signal(name, df, fixed, allow_short), costs).position.reindex(oos.index)
        for key, p in (("walk_forward", pos), ("fixed", full_fixed.fillna(0)),
                       ("buy_and_hold", pd.Series(1.0, index=oos.index))):
            bt = _replay(df, p, costs)
            curves[key].append(bt.ret.rename(sym))
            trades[key] += metrics(bt)["trades"]
            if key == "walk_forward":
                per_sym_bt[sym] = bt

    equity, summary = {}, {}
    for key, rets in curves.items():
        port = pd.concat(rets, axis=1).fillna(0).mean(axis=1)
        eq = (1 + port).cumprod()
        equity[key] = eq
        m = metrics(pd.DataFrame({"ret": port, "equity": eq, "position": 1}, index=eq.index))
        m["trades"] = int(trades[key])
        m.pop("win_rate_%"), m.pop("avg_trade_%"), m.pop("profit_factor"), m.pop("exposure_%")
        summary[key] = m
    return {"windows": pd.DataFrame(windows), "equity": pd.DataFrame(equity), "metrics": summary,
            "fixed_params": fixed, "combos": len(combos), "oos_start": oos_start}


# ----------------------------------------------------------------- account-level (portfolio) backtest
def portfolio_backtest(data: Dict[str, pd.DataFrame], signals: Dict[str, pd.Series], risk: Dict,
                       alloc_pct: Dict[str, float] | None = None, buckets: Dict[str, str] | None = None,
                       costs: "Dict[str, CostModel] | CostModel" = CostModel(), capital: float = 100_000.0) -> Dict:
    """The whole account, sized like the live bot.

    Every New York day, each symbol's dollars come from sizing.budgets() (the function live.py
    uses) with that day's starting equity and, for risk_balanced, ATR% from the days before.
    Exposure = dollars / equity x the strategy's position, traded on the bar after its signal
    (as run_backtest). Costs per unit of exposure change, per symbol. Shares are fractional here;
    the live bot rounds stocks down to whole shares, so it can hold up to one share less per symbol.

    Returns {"frame": equity / ret / gross exposure per bar, "exposure": per-symbol exposure,
    "metrics": metrics() of the account}.
    """
    from . import sizing
    syms = list(data)
    alloc_pct = alloc_pct or {}
    buckets = buckets or {}
    cost = {s: (costs[s] if isinstance(costs, dict) else costs).per_turn for s in syms}
    idx = pd.DatetimeIndex(sorted(set().union(*[df.index for df in data.values()])))
    ret = pd.DataFrame({s: data[s].close.pct_change().reindex(idx).fillna(0.0) for s in syms}, index=idx)
    pos = pd.DataFrame({s: signals[s].reindex(data[s].index).fillna(0).shift(1).fillna(0).reindex(idx).ffill().fillna(0.0)
                        for s in syms}, index=idx)
    st = sizing.settings(risk)
    atr = sizing.atr_table(data, int(st["atr_days"])) if st["sizing"] == "risk_balanced" else {}
    day = np.asarray(idx.tz_convert("America/New_York").date)
    R, P, C = ret.to_numpy(), pos.to_numpy(), np.array([cost[s] for s in syms])
    n, k = len(idx), len(syms)
    out_r, out_eq, exp_all = np.zeros(n), np.zeros(n), np.zeros((n, k))
    eq, prev = capital, np.zeros(k)
    w, cur_day = np.zeros(k), None
    for i in range(n):
        if day[i] != cur_day:  # a new New York day: dollars per symbol from the day's starting equity
            cur_day = day[i]
            b = sizing.budgets(eq, syms, risk, atr.get(cur_day, {}), alloc_pct, buckets)
            w = np.array([b[s] / eq if eq > 0 else 0.0 for s in syms])
        exp = w * P[i]
        r = float((exp * R[i]).sum() - (np.abs(exp - prev) * C).sum())
        eq *= 1 + r
        out_r[i], out_eq[i], exp_all[i] = r, eq, exp
        prev = exp
    frame = pd.DataFrame({"ret": out_r, "equity": out_eq / capital, "position": np.abs(exp_all).sum(axis=1)}, index=idx)
    return {"frame": frame, "exposure": pd.DataFrame(exp_all, index=idx, columns=syms), "metrics": metrics(frame)}
