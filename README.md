# autobot: Alpaca paper-trading strategy lab

Compare common trading strategies on real Alpaca data, search their parameters
without fooling yourself, then run the winners as paper-trading jobs.

## Quick start (Windows)
1. Install Python 3.10+ from python.org (tick "Add to PATH").
2. In Alpaca, switch to **Paper Trading**, then API Keys, then Generate.
3. Double-click `start.bat`. The first run installs packages and opens `.env`, where you paste the keys.
   Run `start.bat` again and the dashboard opens at http://localhost:8501.

Manual: `pip install -r requirements.txt`, then `streamlit run app.py`.
No keys yet? Choose **Demo (synthetic)** in the sidebar, or run `python run_backtest.py --demo`.

## What's inside
| File | What it does |
|---|---|
| `app.py` | Dashboard: settings, strategy comparison, parameter search, bot control |
| `autobot/strategies.py` | 9 strategies (add your own: write a function, register a `StrategySpec`) |
| `autobot/backtest.py` | Vectorized backtester, metrics, train/test parameter search |
| `autobot/data.py` | Alpaca bars (stocks + crypto) with local cache; synthetic demo data |
| `live.py` | Paper-trading runner for the jobs in `config.yaml` |
| `run_backtest.py` | Same comparison from the command line |
| `config.yaml` | All settings (the dashboard edits it) |

## Strategies
| Family | Strategies |
|---|---|
| Benchmark | buy_and_hold |
| Trend | sma_cross, ema_cross, macd, momentum |
| Mean reversion | rsi_reversion, bollinger_reversion |
| Breakout | donchian_breakout |
| Intraday | vwap_reversion |

## Paper bot (`python live.py` or the Start button)
- Each **job** = one strategy + its symbols + allocation %. One job per symbol, because Alpaca nets positions per symbol.
- Acts once per closed bar (e.g. every 5 min) and trades at market.
- Risk: max daily loss % (flattens and halts), max $ per symbol, flatten N min before the close.
- Kill switch: the **Stop + flatten** button, or create a file named `STOP` in this folder.
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
