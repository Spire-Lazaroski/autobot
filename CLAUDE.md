# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A strategy lab and paper-trading bot for Alpaca. It backtests the strategies in `autobot/strategies.py` on Alpaca bars, runs a train/test parameter search, and runs the chosen strategies as paper-trading "jobs" from `config.yaml`. See `README.md` for the user-facing overview.

## Commands

Windows, with the venv at `.venv` (created by `start.bat`). Use `.venv/Scripts/python` or activate the venv first.

```
start_lab.bat                                   # strategy lab: API + web, opens http://127.0.0.1:5190
.venv/Scripts/python -m api                     # lab API only, 127.0.0.1:$API_PORT (8710)
cd web && npm run dev                           # lab web only, 127.0.0.1:$WEB_PORT (5190), proxies /api and /ws
cd web && npx tsc --noEmit                      # typecheck the web app
.venv/Scripts/python -m pytest -q tests         # all tests; single test: pytest tests/test_api.py::test_compare
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/streamlit run app.py              # old Streamlit dashboard at http://localhost:8501
.venv/Scripts/python run_backtest.py --demo     # CLI backtest + search on synthetic data (no keys)
.venv/Scripts/python run_backtest.py            # same on real Alpaca data (needs .env); writes results/*.csv
.venv/Scripts/python live.py --once             # one bot tick against the PAPER account, then exit
.venv/Scripts/python live.py --flatten          # close every paper position
```

There is no linter config. `run_backtest.py --demo` is the quickest end-to-end check of `strategies` → `backtest` without network access. `tests/test_no_lookahead.py` recomputes every registered strategy on truncated data and must pass for any new strategy. Ports come from `.env` (`API_PORT`, `WEB_PORT`); 8000/8001 are used by other things on this machine, and everything binds to 127.0.0.1 only. Anything touching `live.py` needs real paper keys in `.env` (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`).

## Architecture

- **Lab (`api/` + `web/`)**: FastAPI wraps the `autobot` package; it never reimplements backtest logic. Slow work (search, walk-forward) runs through `api/jobs.py` (thread pool, poll `GET /api/jobs/{id}`). Bars are cached in memory per request key on top of `data_cache/`. The web app is React + Vite + Tailwind v4 (custom classes used with `@apply` must be declared with `@utility`), TanStack Query, Plotly via `react-plotly.js/factory`, and lightweight-charts v5 (`chart.addSeries(LineSeries, opts, paneIndex)`, `createSeriesMarkers`). Times cross the API as UTC unix seconds and are shown in New York time.
- **Indicators** (`autobot/indicators.py`): causal indicator math shared by strategies and charts. `PLOTS[name]` says what the candle view draws for a strategy (overlays on price, extra panes with guide levels); add one when adding a strategy.
- **Strategy contract** (`autobot/strategies.py`): a strategy is `f(df, allow_short=False, **params) -> pd.Series` of target positions (1 / 0 / -1) aligned to `df.index`. The signal on bar t may use data through bar t's close. To add one, write the function and register a `StrategySpec` in `STRATEGIES` with `defaults` and an optional `grid` (the search space). Everything else (the dashboard tabs, CLI, search, live jobs) discovers strategies through `STRATEGIES` and calls them via `get_signal()`. Use the helper `_state_machine` for entry/exit-style strategies and `_apply_side` to drop shorts when `allow_short=False`.
- **No look-ahead lives in the backtester, not the strategy**: `run_backtest` shifts positions by one bar before applying returns. Strategies must not shift their own output forward. Costs are `(fee_bps + slippage_bps)` per unit of turnover.
- **Parameter search** (`parameter_search`): runs the full backtest once per param combo, then splits results at `train_frac`. It ranks on train and reports test. `_valid()` rejects combos where `fast >= slow` or `exit >= entry`; extend it if a new grid has similar constraints. The `_params` column carries the raw dict (dropped before display/CSV) and is what the dashboard copies into a new live job.
- **Walk-forward** (`walk_forward` in `backtest.py`): windows are cut on the first symbol's bar clock. Every grid combo is backtested once on full history (valid only because strategies are causal), then each window picks the best combo on its train slice and the chosen effective positions are stitched and replayed with `_replay`, so a parameter switch at a window boundary pays costs. `tests/test_walk_forward.py` checks that appending future bars never changes earlier choices.
- **Data** (`autobot/data.py`): `fetch_bars` caches to `data_cache/{SYM}_{tf}_{days}d_{YYYYMMDD}.csv.gz` (crypto `/` → `-`), keyed by end date, so a cache is reused for the whole day. The RTH filter (9:30–16:00 NY) is applied after loading and only to non-crypto intraday bars. The default feed is `iex` (free). A symbol containing `/` is treated as crypto everywhere (`is_crypto`).
- **Live bot** (`live.py`): calls the private helpers `_download` and `_keys` from `autobot.data` directly and bypasses the cache. It ticks a few seconds after each bar boundary, drops the still-forming bar, recomputes the full signal on ~300 bars of history, and trades the difference between target and current qty with market orders. Constraints the code relies on:
  - `TradingClient(..., paper=True)` is intentionally hard-coded in both `live.py` and `app.py`. Do not add a live-money path.
  - A symbol may belong to only one enabled job, because Alpaca nets positions per symbol. Both `Bot.__init__` and the dashboard's save handler enforce this.
  - Crypto positions come back from Alpaca without the slash (`BTC/USD` → `BTCUSD`, see `_pos_key`). Crypto can't be shorted and uses fractional qty with GTC orders; stocks use whole shares with DAY orders.
  - Flipping long↔short is sent as two orders.
  - Risk: the daily-loss limit (measured against `last_equity`) flattens and halts; stocks are flattened N minutes before the close.
- **Dashboard ↔ bot coupling** (`app.py`): the dashboard never talks to the bot process directly. It reads and writes `config.yaml` (the bot reads it only at startup, so a restart is needed to apply changes). It starts `live.py` as a subprocess and stops it by creating the `STOP` file, which the bot polls, then flattens and exits. It shows the bot's state from `logs/status.json` and `logs/trades.csv`. `save_cfg` rewrites `config.yaml` with `yaml.safe_dump`, so comments in that file are lost when the dashboard saves.
