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
from typing import Dict, List

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


def parameter_search(data: Dict[str, pd.DataFrame], names: List[str] | None = None,
                     costs: CostModel = CostModel(), train_frac=0.7, allow_short=False,
                     rank_by="sharpe") -> pd.DataFrame:
    """Grid-search every strategy's parameter space. Rank on train, report test."""
    names = names or [n for n in STRATEGIES if STRATEGIES[n].grid]
    out = []
    for name in names:
        spec = STRATEGIES[name]
        keys = list(spec.grid)
        for combo in itertools.product(*[spec.grid[k] for k in keys]) if keys else [()]:
            params = {**spec.defaults, **dict(zip(keys, combo))}
            if not _valid(params):
                continue
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
    res = pd.DataFrame(out)
    if res.empty:
        return res
    return res.sort_values(f"train_{rank_by}" if f"train_{rank_by}" in res else "train_sharpe",
                           ascending=False).reset_index(drop=True)


def best_per_strategy(search: pd.DataFrame) -> pd.DataFrame:
    """Top train-ranked parameter set of each strategy, with its test score."""
    return search.groupby("strategy", as_index=False).first().sort_values("test_sharpe", ascending=False)
