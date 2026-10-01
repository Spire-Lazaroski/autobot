"""
Plain-English wording for non-technical readers: reports and the SOP both use this file,
so a strategy is described the same way everywhere.

Rules for this file: describe what the code does, never promise results.
"""
from __future__ import annotations

from typing import Dict

# nick: short name used in sentences ("switched from 'buy the dip' to 'follow the trend'")
STRATEGIES: Dict[str, Dict[str, str]] = {
    "buy_and_hold": dict(
        nick="just hold it", idea="Buy once and keep holding. It is the yardstick every other strategy must beat.",
        likes="Markets that rise steadily.", hates="Long falls: it never steps aside.",
        behavior="No trading at all; it simply follows the market up and down."),
    "sma_cross": dict(
        nick="follow the trend (slow)", idea="Own the stock while its short-term average price is above its long-term average.",
        likes="Long, steady trends.", hates="Choppy, sideways markets where the averages keep crossing.",
        behavior="Many small losses while waiting, a few big wins when a trend runs."),
    "ema_cross": dict(
        nick="follow the trend", idea="Own the stock while its fast-reacting average price is above its slower average.",
        likes="Clear up-trends.", hates="Sideways markets: it buys late and sells late, again and again.",
        behavior="Many small losses, few big wins. Reacts a little faster than the slow version."),
    "macd": dict(
        nick="ride the momentum", idea="Own the stock while its upward momentum is strengthening (MACD above its signal line).",
        likes="Markets that speed up in one direction.", hates="Flat, jittery markets.",
        behavior="Trades often; small losses in noise, gains when momentum builds."),
    "rsi_reversion": dict(
        nick="buy the dip", idea="Buy after a sharp short-term drop (RSI very low) and sell once the price has recovered.",
        likes="Calm, sideways markets that bounce back.", hates="Strong down-trends, where a dip keeps dipping.",
        behavior="Many small wins, occasional larger losses."),
    "bollinger_reversion": dict(
        nick="buy the stretch", idea="Buy when the price falls unusually far below its recent average and sell when it returns to the average.",
        likes="Calm markets that swing around an average.", hates="Breakouts and crashes that keep going.",
        behavior="Frequent small wins, rare large losses."),
    "donchian_breakout": dict(
        nick="buy the breakout", idea="Buy when the price makes a new high for the last N bars; sell on a new N-bar low.",
        likes="Strong moves that break out of a range.", hates="Fake breakouts in sideways markets.",
        behavior="Low win rate, but the winners can be large."),
    "vwap_reversion": dict(
        nick="back to the day's average", idea="Buy when the price is well below the day's volume-weighted average price and sell when it gets back to it.",
        likes="Quiet trading days.", hates="Days with a strong one-way move.",
        behavior="Small, frequent trades within the day."),
    "momentum": dict(
        nick="follow recent gains", idea="Own the stock while it is up over the last N bars.",
        likes="Persistent trends.", hates="Sudden reversals.",
        behavior="Stays in during trends, gives some back when the trend turns."),
    "connors_rsi2": dict(
        nick="buy the dip in an uptrend", idea="In a long-term uptrend (price above its 200-bar average), buy very short, sharp dips and sell on the bounce.",
        likes="Rising markets with brief pullbacks.", hates="Bear markets (it simply stays out).",
        behavior="High win rate, small average gain; designed for daily bars."),
    "ibs_reversion": dict(
        nick="buy the weak close", idea="Buy when a bar closes near its low and sell when a bar closes near its high.",
        likes="Index funds in calm markets.", hates="Trending sell-offs.",
        behavior="Short holding times, many small trades; designed for daily bars."),
    "opening_range_breakout": dict(
        nick="trade the opening break", idea="Watch the first minutes of the day, then trade in the direction the price breaks out of that range; flat by the close.",
        likes="Days with a clear direction early on.", hates="Days that chop back and forth.",
        behavior="One or two trades a day, never held overnight."),
    "intraday_momentum": dict(
        nick="first half hour decides the last", idea="If the market rose in the first half hour, hold it in the last half hour (and the reverse).",
        likes="Busy, news-driven days.", hates="Quiet days with no follow-through.",
        behavior="At most one short trade near the close each day."),
    "noise_area": dict(
        nick="break out of the usual noise", idea="Only trade when the price moves further from the open than it normally does at that time of day; exit on a trailing stop; flat by the close.",
        likes="Strong trending days.", hates="Range-bound days.",
        behavior="Few trades, sits out most of the day."),
    "regime_switch": dict(
        nick="switch by market mood", idea="Decide whether the market is trending, calm or jumpy, and hand control to the strategy chosen for that mood.",
        likes="Markets that stay in one mood for a while.", hates="Markets that flip mood constantly (switching costs).",
        behavior="Behaves like whichever strategy is in charge; stays flat when the market is jumpy if set to."),
    "perf_rotation": dict(
        nick="back the recent winner", idea="Every few bars, give control to the strategy that has done best recently.",
        likes="Periods where one style keeps working.", hates="Markets where yesterday's winner is today's loser.",
        behavior="Switches now and then; can sit flat when everything has been losing."),
    "news_guard": dict(
        nick="step aside for news", idea="Runs another strategy, but takes no new position for a while after an important news story on the stock, and around its earnings releases.",
        likes="Markets where news days bring sudden, unpredictable swings.", hates="Stocks whose best moves happen right after news.",
        behavior="Fewer trades on news and earnings days; otherwise identical to the strategy it wraps."),
    "ensemble_vote": dict(
        nick="majority vote", idea="Ask several strategies and only take the position most of them agree on.",
        likes="Clear markets where signals agree.", hates="Mixed markets, where it mostly stays out.",
        behavior="Fewer trades than any single strategy, smoother results."),
}

REGIMES: Dict[str, Dict[str, str]] = {
    "trend": dict(name="a strong trend", short="trending", explain="The price is moving steadily in one direction (trend strength above its threshold)."),
    "range": dict(name="a calm, sideways market", short="sideways", explain="No strong direction: the price moves back and forth."),
    "high_vol": dict(name="a jumpy, high-volatility market", short="jumpy", explain="Price swings are much bigger than usual for this stock."),
    "warmup": dict(name="not enough history yet", short="warming up", explain="The bot needs more past prices before it can judge the market."),
}

METHODS: Dict[str, Dict[str, str]] = {
    "regime_switch": dict(name="Switch by market mood",
                          explain="Each bar the bot labels the market trending, sideways or jumpy, then uses the strategy you chose for that label. A new mood must last a few bars before the bot switches, to avoid flip-flopping."),
    "perf_rotation": dict(name="Back the recent winner",
                          explain="Every K bars the bot checks which of the chosen strategies has done best over the last N bars and hands it control. If all have been losing it can stay out."),
    "ensemble_vote": dict(name="Majority vote",
                          explain="The bot asks every chosen strategy for its position and follows the majority; with 'average' it takes a partial position in proportion to the votes."),
}

# Universe screener (autobot/universe.py): one fixed sentence per outcome, filled from the numbers.
UNIVERSE_REASONS: Dict[str, str] = {
    "new": "Selected: ranked {rank} of {n} in {bucket}, inside the top {keep}.",
    "kept": "Kept: ranked {rank} of {n}, still inside the top {keep}.",
    "kept_hysteresis": ("Kept although ranked {rank} of {n}: a symbol already in the list is only dropped below rank {limit}, "
                        "so small changes in the ranking don't swap symbols in and out."),
    "dropped": "Dropped: ranked {rank} of {n}, below rank {limit}.",
    "not_selected": "Not selected: ranked {rank} of {n}; the top {keep} places are taken.",
    "floor": ("Left out: typical daily trading of ${dv} is below the ${floor} minimum. These figures come from the free IEX feed, "
              "which sees only part of the market, so they are only useful for comparing symbols."),
    "no_data": "Left out: only {days} days of prices were available (needs {need}).",
}
UNIVERSE_WARNINGS: Dict[str, str] = {
    "bucket_short": "{bucket}: only {got} symbol(s) qualified, fewer than the minimum of {min}.",
    "total_short": "The list has {got} symbols, fewer than the minimum of {min}.",
    "total_long": "The list has {got} symbols, more than the maximum of {max}.",
}

# Nightly recalibration (autobot/recalibrate.py): one fixed sentence per decision.
RECAL_REASONS: Dict[str, str] = {
    "new_job": ("No job yet. The best option, {ch}, scored {cs} on days it hadn't been tuned on, {d} more than doing nothing "
                "(needs {edge}), with {n} trades. Proposed as a new job."),
    "no_job_weak": "No job: the best option, {ch}, scored {cs} on days it hadn't been tuned on, not enough above doing nothing (needs {edge}).",
    "replace": "Replace {inc} ({is}) with {ch} ({cs}): better by {d} on days neither had been tuned on, at least the {edge} required.",
    "keep_edge": "Keep {inc}: {ch} scored {cs} against {is}, better by only {d} (needs {edge}).",
    "keep_better": "Keep {inc}: it scored {is}, at least as well as the best alternative, {ch} ({cs}).",
    "keep_same": "Keep {inc}: it is still the best choice ({is}).",
    "keep_trades": "Keep {inc}: {ch} made only {n} trades on the test days (needs {min}), too few to trust.",
    "no_job_trades": "No job: the best option, {ch}, made only {n} trades on the test days (needs {min}), too few to trust.",
    "keep_hold": "Keep {inc}: it has been in place {days} days, less than the minimum of {min}.",
    "keep_changes": "Keep {inc}: this symbol already changed {n} times in the last 7 days (maximum {max}).",
    "remove": "Remove {inc}: {sym} is no longer in the trading list; its position will be closed.",
    "no_data": "No decision: not enough price history for {sym} ({days} days, needs {need}). Nothing changes.",
}
RECAL_TAKEOVER = "From now on the autobot manages this symbol: it moves from your job '{job}' to its own auto job."
RECAL_SHAPES: Dict[str, str] = {
    "single": "{nick} ({name}{params})",
    "regime_switch": "switch by market mood: trending → {trend}, sideways → {range}, jumpy → stay out",
    "perf_rotation": "back the recent winner among {names}",
    "none": "no job",
}

GLOSSARY: Dict[str, str] = {
    "Paper trading": "Trading with pretend money in a practice account; no real money is at risk.",
    "Buy and hold": "Buying once and simply keeping it; the baseline every strategy is compared with.",
    "Position": "The shares the bot currently owns (or owes, if short).",
    "Flat": "Owning nothing: no open position.",
    "Short": "Selling shares you don't own to profit if the price falls.",
    "Bar": "One slice of time on the chart (for example 5 minutes) with its open, high, low and close price.",
    "Signal": "What a strategy wants: 1 = own it, 0 = own nothing, -1 = be short.",
    "Fill": "The actual price the broker traded at.",
    "P&L": "Profit and loss: money made or lost.",
    "Return": "Profit or loss as a percentage of the money used.",
    "Moving average": "The average price over the last N bars; it smooths out the noise.",
    "EMA": "Exponential moving average: a moving average that reacts faster to recent prices.",
    "RSI": "Relative Strength Index (0-100): how strongly the price has been rising or falling lately. Low means a sharp drop.",
    "MACD": "The difference between a fast and a slow average; shows whether momentum is building.",
    "ADX": "Average Directional Index: how strong the current trend is, whichever way it points. Above about 25 is a clear trend.",
    "Trend strength": "How clearly the price is moving in one direction (measured with ADX or a similar gauge).",
    "Volatility": "How much the price swings. 'High volatility' means swings much bigger than usual.",
    "Volatility percentile": "Where today's swings rank against the recent past: 0.9 means bigger than 90% of recent bars.",
    "Bollinger band": "A band two standard deviations around the average price; prices outside it are unusually stretched.",
    "VWAP": "Volume-weighted average price for the day: the average price paid, weighted by how much was traded.",
    "Breakout": "The price moving beyond a recent high or low.",
    "Regime": "The market's current mood: trending, sideways or jumpy.",
    "Strategy switch": "The bot handing control from one strategy to another.",
    "Drawdown": "The drop from the highest account value to a later low.",
    "Win rate": "The share of trades that made money.",
    "Daily loss limit": "If the account is down this much in a day, the bot sells everything and stops for the day.",
    "Flatten before close": "The bot sells stock positions a few minutes before the US market closes so nothing is held overnight.",
    "Backtest": "Replaying a strategy on past prices to see how it would have done.",
    "Demo data": "Made-up prices used to test the software. Results on it say nothing about real markets.",
    "News story": "An article from the Alpaca/Benzinga news feed about one of our symbols; reposts of the same story count once.",
    "Materiality": "How much a news story could matter to the price, from 1 (routine) to 5 (major), scored by keyword rules or by Claude.",
    "Earnings day": "The session that reacts to a company's quarterly results (results released after the close move the next day).",
    "Sharpe ratio":"Return divided by how bumpy the ride was; higher is better, above 1 is good.",
}


def nick(strategy: str | None) -> str:
    if not strategy or strategy == "flat":
        return "stay out"
    return STRATEGIES.get(strategy, {}).get("nick", strategy)


def regime_name(regime: str | None) -> str:
    return REGIMES.get(regime or "", {}).get("name", regime or "unknown")
