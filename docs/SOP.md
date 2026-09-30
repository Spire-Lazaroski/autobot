# Operating procedure: Spire Analytics paper-trading bot

Version generated 30 September 2026 22:14 (Skopje) from the bot's code and config.yaml. If something here disagrees with the app, regenerate this document (see the end).

## 1. What the bot is, and what it isn't

- It trades a **paper account** at Alpaca: pretend money, real market prices. The code is hard-wired to paper trading; there is no switch to real money.
- It follows rules you choose (the **jobs**): each job is one strategy on one or more symbols with a share of the account. It checks the market once per 5Min bar and sends market orders.
- It protects itself: if the account is down 2.0% in a day it sells everything and stops for the day; no single symbol gets more than $10,000; stock positions are closed 5 minutes before the US close.
- It writes down why it acted (logs/decisions.jsonl) and produces a plain-English report after every trading day.

**What it can't do**

- It can't predict the market or guarantee a profit. Every strategy loses in some markets.
- It doesn't read the news; it only sees prices.
- It can't trade while the PC is off, asleep or offline, and it can't fix a broken internet connection.
- Market orders fill at the best available price, which can differ from the last price it saw, especially at the open and in fast markets.
- It never trades real money, and results on paper can be better than real trading would be.

## 2. Before the market opens

The US market opens at 09:30 New York time, which is usually **15:30** in Skopje (see the table in section 4 for the weeks when it differs).

1. Switch the PC on and make sure it won't sleep: Windows Settings → System → Power → *Sleep* = **Never** (when plugged in). The bot stops trading if the PC sleeps.
2. Double-click **start_lab.bat** in E:\autobot. Two windows open ("autobot API" and "autobot web") and the lab opens in the browser at http://127.0.0.1:5190. Leave both windows open all day.
3. Open the **Live** page. Check the small dot next to *live* is **green** (connected) and the header says **API** with a green dot and **paper only**.
4. Check *Paper equity* shows a dollar amount and the account status is ACTIVE.
5. Scroll to **Jobs · config.yaml** and check which jobs are switched **on** and on which symbols (below). Change nothing on the day unless it was tested in the Lab first.

![The Live page when everything is connected: green dots next to API and live, the paper-only badge, the account figures.](img/live_top.png)
*The Live page when everything is connected: green dots next to API and live, the paper-only badge, the account figures.*

| Job | On? | Strategy | Symbols | Share of account per symbol |
|---|---|---|---|---|
| trend | on | ema_cross (follow the trend) | SPY, QQQ | 20% |
| reversion | on | rsi_reversion (buy the dip) | AAPL | 15% |
| crypto_breakout | off | donchian_breakout (buy the breakout) | BTC/USD | 10% |

![The Jobs editor. Switch a job on or off with its checkbox, then Save. The bot applies saved changes at its next bar.](img/jobs_editor.png)
*The Jobs editor. Switch a job on or off with its checkbox, then Save. The bot applies saved changes at its next bar.*

## 3. Starting the bot

1. On the **Live** page press **▶ Start bot** (top right).
2. Within a few seconds the status changes to **BOT RUNNING** with a green dot, a process number (pid) and *beat 1s ago*. The beat number must stay small (under 10 seconds).
3. Wait for the first tick: within one 5Min bar, *Bot last tick* shows the current time and the *Bot view per symbol* table fills in.
4. If Start is greyed out, a bot is already running (or a stuck one is still alive, shown as *unresponsive*). Never start a second bot.

![What 'running' looks like: BOT RUNNING, the pid, and a heartbeat a second or two old.](img/live_state.png)
*What 'running' looks like: BOT RUNNING, the pid, and a heartbeat a second or two old.*

## 4. Stopping the bot

### Normal stop

1. Live page → **⏹ Stop + flatten**.
2. Read the confirmation and press **Stop + flatten**. The bot closes **every** open position in the paper account (including ones it didn't open) and exits within a few seconds.
3. Confirm all is flat (see below).

![The confirmation dialog. Cancel changes nothing.](img/stop_dialog.png)
*The confirmation dialog. Cancel changes nothing.*

### Emergency stop

1. If the page doesn't respond: create an empty file named **STOP** (no extension) in E:\autobot. In PowerShell: `New-Item E:\autobot\STOP`. The bot checks for it every few seconds, closes everything and exits; if the bot is stuck, its watchdog does the same within about 10 seconds.
2. If that doesn't work within a minute: on the Alpaca website (paper account) → Positions → **Close all positions**, then end the *python … live.py* process in Task Manager.

### Automatic close-of-day flatten

The bot closes all stock positions 5 minutes before the US close (15:55 New York). That is usually **21:55** in Skopje, but it shifts for a few weeks when the US and Europe change their clocks on different dates:

| Dates in 2026 | Close-of-day flatten, Skopje time |
|---|---|
| 01 Jan to 07 Mar | 21:55 |
| 08 Mar to 28 Mar | 20:55 |
| 29 Mar to 24 Oct | 21:55 |
| 25 Oct to 31 Oct | 20:55 |
| 01 Nov to 31 Dec | 21:55 |

### How to confirm all is flat

- Live page → **Positions** says *No open positions*.
- The Alpaca website (paper account) shows no positions and no open orders.
- The status shows **BOT STOPPED** (unless you intend it to keep running).

## 5. The strategies in plain words

### Benchmark

**Just hold it** (`buy_and_hold`)

- Buy once and keep holding. It is the yardstick every other strategy must beat.
- **Likes:** Markets that rise steadily.
- **Hates:** Long falls: it never steps aside.
- **Typical behaviour:** No trading at all; it simply follows the market up and down.

### Trend following

**Follow the trend (slow)** (`sma_cross`)

- Own the stock while its short-term average price is above its long-term average.
- **Likes:** Long, steady trends.
- **Hates:** Choppy, sideways markets where the averages keep crossing.
- **Typical behaviour:** Many small losses while waiting, a few big wins when a trend runs.

**Follow the trend** (`ema_cross`)

- Own the stock while its fast-reacting average price is above its slower average.
- **Likes:** Clear up-trends.
- **Hates:** Sideways markets: it buys late and sells late, again and again.
- **Typical behaviour:** Many small losses, few big wins. Reacts a little faster than the slow version.

**Ride the momentum** (`macd`)

- Own the stock while its upward momentum is strengthening (MACD above its signal line).
- **Likes:** Markets that speed up in one direction.
- **Hates:** Flat, jittery markets.
- **Typical behaviour:** Trades often; small losses in noise, gains when momentum builds.

**Follow recent gains** (`momentum`)

- Own the stock while it is up over the last N bars.
- **Likes:** Persistent trends.
- **Hates:** Sudden reversals.
- **Typical behaviour:** Stays in during trends, gives some back when the trend turns.

### Buy the dip / mean reversion

**Buy the dip** (`rsi_reversion`)

- Buy after a sharp short-term drop (RSI very low) and sell once the price has recovered.
- **Likes:** Calm, sideways markets that bounce back.
- **Hates:** Strong down-trends, where a dip keeps dipping.
- **Typical behaviour:** Many small wins, occasional larger losses.

**Buy the stretch** (`bollinger_reversion`)

- Buy when the price falls unusually far below its recent average and sell when it returns to the average.
- **Likes:** Calm markets that swing around an average.
- **Hates:** Breakouts and crashes that keep going.
- **Typical behaviour:** Frequent small wins, rare large losses.

**Buy the dip in an uptrend** (`connors_rsi2`)

- In a long-term uptrend (price above its 200-bar average), buy very short, sharp dips and sell on the bounce.
- **Likes:** Rising markets with brief pullbacks.
- **Hates:** Bear markets (it simply stays out).
- **Typical behaviour:** High win rate, small average gain; designed for daily bars.

**Buy the weak close** (`ibs_reversion`)

- Buy when a bar closes near its low and sell when a bar closes near its high.
- **Likes:** Index funds in calm markets.
- **Hates:** Trending sell-offs.
- **Typical behaviour:** Short holding times, many small trades; designed for daily bars.

### Breakout

**Buy the breakout** (`donchian_breakout`)

- Buy when the price makes a new high for the last N bars; sell on a new N-bar low.
- **Likes:** Strong moves that break out of a range.
- **Hates:** Fake breakouts in sideways markets.
- **Typical behaviour:** Low win rate, but the winners can be large.

### Intraday (flat every night)

**Back to the day's average** (`vwap_reversion`)

- Buy when the price is well below the day's volume-weighted average price and sell when it gets back to it.
- **Likes:** Quiet trading days.
- **Hates:** Days with a strong one-way move.
- **Typical behaviour:** Small, frequent trades within the day.

**Trade the opening break** (`opening_range_breakout`)

- Watch the first minutes of the day, then trade in the direction the price breaks out of that range; flat by the close.
- **Likes:** Days with a clear direction early on.
- **Hates:** Days that chop back and forth.
- **Typical behaviour:** One or two trades a day, never held overnight.

**First half hour decides the last** (`intraday_momentum`)

- If the market rose in the first half hour, hold it in the last half hour (and the reverse).
- **Likes:** Busy, news-driven days.
- **Hates:** Quiet days with no follow-through.
- **Typical behaviour:** At most one short trade near the close each day.

**Break out of the usual noise** (`noise_area`)

- Only trade when the price moves further from the open than it normally does at that time of day; exit on a trailing stop; flat by the close.
- **Likes:** Strong trending days.
- **Hates:** Range-bound days.
- **Typical behaviour:** Few trades, sits out most of the day.

### Strategy shifting

**Switch by market mood** (`regime_switch`)

- Decide whether the market is trending, calm or jumpy, and hand control to the strategy chosen for that mood.
- **Likes:** Markets that stay in one mood for a while.
- **Hates:** Markets that flip mood constantly (switching costs).
- **Typical behaviour:** Behaves like whichever strategy is in charge; stays flat when the market is jumpy if set to.

**Back the recent winner** (`perf_rotation`)

- Every few bars, give control to the strategy that has done best recently.
- **Likes:** Periods where one style keeps working.
- **Hates:** Markets where yesterday's winner is today's loser.
- **Typical behaviour:** Switches now and then; can sit flat when everything has been losing.

**Majority vote** (`ensemble_vote`)

- Ask several strategies and only take the position most of them agree on.
- **Likes:** Clear markets where signals agree.
- **Hates:** Mixed markets, where it mostly stays out.
- **Typical behaviour:** Fewer trades than any single strategy, smoother results.

### Market moods (regimes)

**A strong trend** (`trend`)

- The price is moving steadily in one direction (trend strength above its threshold).

**A calm, sideways market** (`range`)

- No strong direction: the price moves back and forth.

**A jumpy, high-volatility market** (`high_vol`)

- Price swings are much bigger than usual for this stock.

**Not enough history yet** (`warmup`)

- The bot needs more past prices before it can judge the market.

### Shifting methods

**Switch by market mood** (`regime_switch`)

- Each bar the bot labels the market trending, sideways or jumpy, then uses the strategy you chose for that label. A new mood must last a few bars before the bot switches, to avoid flip-flopping.

**Back the recent winner** (`perf_rotation`)

- Every K bars the bot checks which of the chosen strategies has done best over the last N bars and hands it control. If all have been losing it can stay out.

**Majority vote** (`ensemble_vote`)

- The bot asks every chosen strategy for its position and follows the majority; with 'average' it takes a partial position in proportion to the votes.

## 6. What to watch: daily checklist

Look at the Live page a few times a day and read the daily report after the close. 🟢 green = fine, 🟠 amber = keep an eye on it, 🔴 red = act now.

| Check | Green | Amber | Red: what to do |
|---|---|---|---|
| Day P&L vs the daily loss limit | above -1% | -1% to -1.6% | below -1.6% or the bot halted: the limit (2.0%) will stop it; don't restart it the same day |
| Deepest dip this week (weekly report) | less than 2% | 2% to 4% | more than 4%: review the strategies (section 8) |
| Number of trades vs a normal day | similar to the last 5 reports | twice as many or none at all | many times more: check the log and the jobs; stop the bot if unsure |
| Errors in the bot log (Live page) | none | an occasional data error (the bot skips that bar) | errors every bar, or CRITICAL/WATCHDOG lines: see troubleshooting |
| Last tick / heartbeat | last tick within one 5Min bar; beat under 10 s | last tick 1-3 bars old | heartbeat over 90 s, status 'unresponsive', or last tick more than 3 bars old: stop and restart |
| Strategy switching (reports, Shifting jobs) | 0-4 switches a day per symbol | 5-8 a day | more than 8 a day: switching costs money; test a larger confirm_bars in the Lab |
| A symbol stuck in a position | positions change when signals change | an intraday strategy still holding late in the day | a position held with no job on that symbol: close it on the Live page or the Alpaca website |

## 7. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Header shows **API down** | The 'autobot API' window was closed or crashed | Run start_lab.bat again. The bot keeps running on its own; check it on the Live page. |
| Live page says *reconnecting…* | The API is restarting or not running | Wait 10 seconds; if it stays, run start_lab.bat. |
| Status **unresponsive** | The bot process is alive but hasn't beaten for 90 s (stuck) | Press Stop + flatten. If nothing happens within a minute, use the emergency stop. The watchdog also exits a bot stuck for 300 s. |
| Bot stopped by itself | Daily loss limit, a stall, or the PC slept | Read the bot log tail on the Live page (look for FLATTEN, WATCHDOG, CRITICAL). Restart only if you understand why. |
| **Start** refused: 'already running' | A bot is still running | Don't start another. Stop the running one first if needed. |
| Jobs editor won't save | A rule was broken (message in red), e.g. one symbol in two jobs | Fix what the message says, then Save. |
| No orders all day | Market closed, signals said 'stay out', or the allocation buys less than 1 share | Check the Bot view per symbol table (signal and target) and the reasons in the daily report. |
| Report says *reason not recorded* | The trade happened while an older bot version without the decision log was running | Nothing to fix; new trades will have reasons. |
| No daily report | The lab wasn't running 10 minutes after the close | Reports page → pick the date → Generate. |
| 'data/signal error' lines in the log | Internet or Alpaca data problem | The bot skips those bars. If it lasts, check your connection and Alpaca's status page. |

## 8. Weekly review

1. Reports page → **Last 7 days**. Read the headline, then the charts.
2. Check: the result against simply holding SPY, the number of trades, the worst trade, the deepest dip, and the 'What didn't' list.
3. Compare with the last few weeks' reports; one bad week alone says little.

**When to change or switch off a strategy**

- It trailed simply holding SPY for three weeks in a row while trading a lot.
- Its deepest dip in a week was more than 4%.
- The report keeps saying 'many small losses in a sideways market' for a trend strategy (or the reverse for a dip-buying strategy).
- It switches strategies more than 8 times a day on a symbol, week after week.

> Always test a change in the Lab first: Lab → run it on Alpaca data (at least 90 days), then Search → Walk-forward. Only change the live job if the walk-forward result is better than what runs today. Change one thing at a time, and note the date so the reports can be compared.

## Keeping this document up to date

Run `python scripts/build_sop.py --shots` with the lab running. It re-reads the strategies, limits, jobs and times, takes fresh screenshots and rebuilds this document (Markdown, HTML and PDF).
