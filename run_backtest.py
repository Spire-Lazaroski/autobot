"""
Command-line backtest: compare every strategy + run the parameter search.

    python run_backtest.py            # real Alpaca data (needs .env)
    python run_backtest.py --demo     # synthetic data, no keys needed (plumbing test only)

Writes results/summary.csv, results/search.csv, results/equity.csv
"""
import argparse
from pathlib import Path

import pandas as pd
import yaml

from autobot.backtest import CostModel, best_per_strategy, compare_strategies, parameter_search
from autobot.data import fetch_bars, synthetic_bars
from autobot.strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="use synthetic data")
    a = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())["backtest"]
    loader = synthetic_bars if a.demo else fetch_bars
    kw = {} if a.demo else {"rth_only": cfg["rth_only"]}
    data = loader(cfg["symbols"], cfg["timeframe"], cfg["days"], **kw)
    costs = CostModel(cfg["fee_bps"], cfg["slippage_bps"])

    summary, equity, _ = compare_strategies(data, {n: {} for n in STRATEGIES}, costs, cfg["allow_short"])
    search = parameter_search(data, costs=costs, train_frac=cfg["train_frac"], allow_short=cfg["allow_short"])

    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    summary.to_csv(out / "summary.csv")
    search.drop(columns="_params").to_csv(out / "search.csv", index=False)
    equity.to_csv(out / "equity.csv")

    pd.set_option("display.width", 200)
    print("\n=== Default parameters (sorted by Sharpe) ===")
    print(summary[["total_return_%", "sharpe", "max_drawdown_%", "trades", "win_rate_%", "exposure_%"]])
    print("\n=== Best params per strategy: chosen on TRAIN, scored on unseen TEST ===")
    print(best_per_strategy(search).drop(columns="_params").to_string(index=False))
    if a.demo:
        print("\n(DEMO DATA: these numbers are meaningless for real markets.)")


if __name__ == "__main__":
    main()
