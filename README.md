# autobot: Alpaca paper-trading strategy lab

Compare common trading strategies on real Alpaca data, search their parameters
without fooling yourself, then run the winners as paper-trading jobs.

## Quick start (Windows)
1. Install Python 3.10+ from python.org (tick "Add to PATH").
2. In Alpaca, switch to **Paper Trading**, then API Keys, then Generate.
3. Install Node.js 20+ from nodejs.org (for the web UI).
4. Double-click `start.bat`. The first run installs packages and opens `.env`, where you paste the keys.
   Run `start.bat` (or `start_lab.bat`) again and the lab opens at http://127.0.0.1:5190.

Manual: `pip install -r requirements.txt`, then `python -m api` and, in `web/`, `npm install` and `npm run dev`.
Ports live in `.env` (`API_PORT`, `WEB_PORT`); everything binds to 127.0.0.1 only.
No keys yet? Pick **Demo** in the settings bar, or run `python run_backtest.py --demo`.

## What's inside
| File | What it does |
|---|---|
| `web/` | Lab UI (React): Backtest Lab, Param search, Walk-forward, Strategy shifting, Live monitor |
| `api/` | Local API (FastAPI) the UI talks to; wraps the `autobot` package |
| `autobot/strategies.py` | 14 strategies (add your own: write a function, register a `StrategySpec`) |
| `autobot/meta.py` | Strategy shifting: regime switch, performance rotation, ensemble vote |
| `autobot/indicators.py` | Indicator math shared by strategies and charts |
| `autobot/backtest.py` | Vectorized backtester, metrics, train/test parameter search, walk-forward |
| `autobot/liveconfig.py` | Config validation and the single-bot lock, shared by the bot and the API |
| `autobot/data.py` | Alpaca bars (stocks + crypto) with local cache; synthetic demo data |
| `live.py` | Paper-trading runner for the jobs in `config.yaml` |
| `run_backtest.py` | Same comparison from the command line |
| `config.yaml` | All settings (the Jobs editor edits it) |
| `tests/` | `pytest`: no look-ahead, walk-forward, meta-strategies, live bot with a fake broker, API |

## Strategies
| Family | Strategies |
|---|---|
| Benchmark | buy_and_hold |
| Trend | sma_cross, ema_cross, macd, momentum |
| Mean reversion | rsi_reversion, bollinger_reversion, connors_rsi2, ibs_reversion |
| Breakout | donchian_breakout |
| Intraday | vwap_reversion, opening_range_breakout, intraday_momentum, noise_area |
| Meta (strategy shifting) | regime_switch, perf_rotation, ensemble_vote |

The research-based ones cite their source in the UI. Published edges tend to shrink after publication,
so judge them on your own out-of-sample results.

## Automatic start
`autostart_on.bat` creates two Task Scheduler tasks that run 2 minutes after you log on: **Spire autobot**
(the bot under `run_bot.py`, which restarts it after a crash, max 3 per hour, logging to `logs/bot.log`) and
**Spire autobot lab** (API, dashboard, news collector). `autostart_off.bat` disables them.
Manual: `start_bot.bat` / `stop_bot.bat` (stop closes every position).

## Paper bot (`python live.py` or Start on the Live monitor)
- Each **job** = one strategy + its symbols + allocation %. One job per symbol, because Alpaca nets positions per symbol.
- Acts once per closed bar (e.g. every 5 min) and trades at market.
- Re-reads `config.yaml` every bar: edits in the Jobs editor apply without a restart and are logged. Only one bot can run at a time.
- Risk: max daily loss % (flattens and halts), max $ per symbol, flatten N min before the close.
- Kill switch: the **Stop + flatten** button (asks to confirm), or create a file named `STOP` in this folder.
- `TradingClient(paper=True)` is hard-coded. There is no live-money switch.
- Logs: `logs/bot.log`, `logs/trades.csv`, `logs/status.json`.
- `python live.py --once` runs a single tick for testing. `python live.py --flatten` closes everything.

## Reading results honestly
- Always compare against **buy_and_hold**.
- Trust **test_sharpe** (unseen data) over train. Points far below the diagonal in the search chart are overfit.
- Fewer than ~30 trades means the result is mostly noise. Extend the history or add symbols.
- Keep slippage at ≥2 bps for 1–5 min bars. High-turnover strategies die on costs.
- Free Alpaca data is the IEX feed (a few % of volume), so intraday bars are thinner than the full tape.

## Speed limits
Alpaca REST gives ~50–200 ms per order and 200 requests/min. 1-minute bars is the realistic floor.
Going faster means websocket streaming (`alpaca.data.live.StockDataStream`), a natural next step.
