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
    "drop": ("Drop {inc}: it scored {is} on days it hadn't been tuned on, below doing nothing (0), and no alternative was good "
             "enough to replace it (best: {ch}, {cs}). The symbol gets no job; any position is closed."),
    "no_data": "No decision: not enough price history for {sym} ({days} days, needs {need}). Nothing changes.",
}
EXIT_REASONS: Dict[str, str] = {
    "trail_exit": "Sold: price {close} fell {pct}% from the {peak} high since entry (trail {trail}%).",
    "trail_exit_short": "Bought back: price {close} rose {pct}% from the {peak} low since entry (trail {trail}%).",
    "waiting": "Stayed out: waiting for {what}.",
    "wait_fresh_cross": "a fresh crossover (the strategy has to go out and back in)",
    "wait_new_high": "a close above {peak}",
    "wait_both": "a fresh crossover or a close above {peak}",
    "wait_cooldown": "the {n}-bar cooldown to end",
    "late": "No new position in the last {n} minutes before the close.",
    "reentry": "Back in after the trailing exit: {rule}.",
    "rule_fresh_cross": "the strategy went out and back in",
    "rule_new_high": "a bar closed beyond the level it had exited from",
    "rule_both": "a fresh crossover or a new high came first",
    "rule_cooldown": "the cooldown is over and the strategy still wants in",
}

EXIT_SOP: Dict[str, str] = {
    "what": ("An optional layer on top of any job's strategy, switched on per job in config.yaml (an <code>exit:</code> block under the job). "
             "While the bot holds a position it remembers the best closing price since it bought; if a 5-minute bar closes more than "
             "<i>trail_pct</i> % below that, it sells, even if the strategy still wants to hold."),
    "reentry": ("After such a sale it stays out until the re-entry rule allows a new buy, and never on the very next bar: "
                "<b>fresh_cross</b> the strategy has to go out and back in; <b>new_high</b> a bar closes above the price it sold from "
                "while the strategy still wants in; <b>both</b> whichever comes first; <b>cooldown</b> wait a number of bars, then follow the strategy."),
    "late": "<i>no_entry_last_min</i>: no new stock positions in the last N minutes before the close (positions already held are kept until the close).",
    "same": ("The backtests, the Lab, the nightly check and the bot all use the same code for this, so the bot does exactly what was tested. "
             "Each sale or wait is explained in the decision log and the daily report, for example "
             "'Sold: price 763.05 fell 0.29% from the 765.27 high since entry (trail 0.25%).'"),
    "lab": ("Lab → <i>Exit rules</i> compares the jobs without the layer and with every setting on the last 90 days, including a walk-forward "
            "test on days the setting was not chosen on. Only switch it on if that walk-forward result beats 'none'."),
    "recal": "The nightly check can also try exit settings for each symbol (<code>recalibration: include_exit: true</code>, off by default).",
}

LIVE_VIEW: Dict[str, str] = {
    "trigger": ("<b>Trigger</b> (Bot view per symbol, and the dashed line on the live chart): the price at which the bot's current "
                "strategy would change its mind at the next bar's close. Red 'sell &lt; 762.70' means it would sell if a bar closes below "
                "762.70; green 'buy &gt; 745.10' means it would buy if a bar closes above 745.10. The percentage is how far that is from "
                "the last close, and '~3 bars if flat' how soon it changes anyway if the price just sits near that level. "
                "For a job with an exit layer the line shows whichever is nearer: the strategy's level or the trailing-exit level "
                "(marked 'trail'), or, after a trailing exit, the price that allows a re-entry (marked 're-entry')."),
    "already_past": ("'sells at the next close unless above X' means the change happens even if the price doesn't move "
                     "(for example an old price drops out of a momentum look-back); only a close beyond X would prevent it."),
    "none": "'–' means no close within 5% of the last price would change the strategy's mind at the next bar.",
    "peak": ("<b>Peak since entry</b> (dotted line, and 'From peak' in Positions): the best price since the bot opened the position, "
             "and how much of it has been given back."),
    "display_only": "These lines are for reading only: the bot doesn't use them to trade, and they never change an order.",
}

REPORT_RECAL: Dict[str, str] = {
    "intro": ("Every night after the close the bot re-tests each symbol's strategy on recent prices (only on days the strategy "
              "had not been tuned on) and, if something is clearly better, switches. These are the changes in effect for this period."),
    "change": "{sym}: {old} → {new}. {reason}",
    "applied_at": "Applied {when} ({by}).",
    "pending": "Waiting to apply: {syms} (stocks change only while the market is closed, crypto only when the bot holds none).",
    "none": "No strategy changes took effect in this period.",
    "mode_propose": "Changes are only proposed (recalibration mode 'propose'): someone has to press Apply on the Recalibrate page.",
    "universe": "Trading list of {date}: {n} symbols ({syms}).",
    "universe_moves": "Added: {added}. Dropped: {dropped}.",
    "universe_none": "No trading list (universe) has been made yet.",
}
RECAL_TAKEOVER = "From now on the autobot manages this symbol: it moves from your job '{job}' to its own auto job."
RECAL_SHAPES: Dict[str, str] = {
    "single": "{nick} ({name}{params})",
    "regime_switch": "switch by market mood: trending → {trend}, sideways → {range}, jumpy → stay out",
    "perf_rotation": "back the recent winner among {names}",
    "none": "no job",
    "exit": ", with a {trail}% trailing exit (re-entry: {reentry})",
}

OVERNIGHT_CONDITIONS: Dict[str, str] = {
    "signal_on": "the strategy still wants the position",
    "in_profit": "the position is in profit",
    "in_profit_x": "the position is more than {x}% in profit",
    "trend_regime": "the market mood is trending",
    "not_friday": "it is not a Friday",
    "no_earnings": "no earnings release before the next open",
    "no_bad_news": "no important bad news (materiality {m}+) in the last {h} hours",
    "no_bad_news_m3": "no notable bad news (materiality {m3}+) in the last {h} hours",
}
OVERNIGHT_POLICIES: Dict[str, str] = {
    "flatten": "sell everything {n} minutes before the close",
    "hold": "keep whatever the strategy holds overnight",
    "conditional": "keep the position overnight only if {conds}",
}
OVERNIGHT_SHORT: Dict[str, str] = {
    "signal_on": "strategy still in", "in_profit": "in profit", "in_profit_x": "in profit by more than {x}%",
    "trend_regime": "trending mood", "not_friday": "not a Friday", "no_earnings": "no earnings next",
    "no_bad_news": "no important bad news", "no_bad_news_m3": "no notable bad news",
}
OVERNIGHT: Dict = {
    "verdict_no": ("Out of sample, after costs, nothing beats selling everything before the close. The overnight rule picked on "
                   "earlier days differed from selling before the close by {mean} bps a day (95% range {lo} to {hi}), "
                   "which is within chance."),
    "verdict_worse": ("Out of sample, after costs, holding overnight did worse than selling before the close: {mean} bps a day "
                      "(95% range {lo} to {hi})."),
    "verdict_yes": ("Out of sample, after costs, the overnight rule picked on earlier days beat selling before the close by "
                    "{mean} bps a day (95% range {lo} to {hi})."),
    "wf_compare": ("On the same test days: the picked rule {picked}%, always selling {n} minutes before the close {flat}%, "
                   "always holding {hold}%, buy and hold {bh}%. The picked rule changed in {changes}% of the windows."),
    "best_fixed": "Best single rule on the test days: '{policy}', {diff} bps a day against selling before the close (95% range {lo} to {hi}){sig}.",
    "best_fixed_chance": ", within chance (and it is the best of many, chosen afterwards)",
    "costs_ranking_same": ("With normal costs at the open and in the last bar (1x instead of 2x) the top of the ranking on the test days "
                           "stays the same: best '{best2}' (rank agreement {rho})."),
    "costs_ranking_changes": ("With normal costs at the open and in the last bar (1x instead of 2x) the ranking on the test days changes: "
                              "best '{best1}' instead of '{best2}' (rank agreement {rho})."),
    "costs_ranking_minor": ("With normal costs at the open and in the last bar (1x instead of 2x) the best rule on the test days stays "
                            "'{best2}', but the places just below it change (rank agreement {rho})."),
    "costs_verdict_1x": "At 1x costs: {verdict}",
    "symbol": ("Holding {sym} overnight whenever its strategy ({nick}) still held it {verb} {diff}% over {period} compared with "
               "selling before the close ({hold}% against {flat}%; buy and hold {bh}%); {nights} nights held, {helped}% of them "
               "gained, worst night {worst}% ({date})."),
    "symbol_none": "{sym}: its strategy ({nick}) never held a position at the close, so the overnight rule makes no difference.",
    "earnings": "Holding {sym} through earnings ({n} nights) made {avg}% a night on average; worst {worst}%.",
    "not_informative": "Not informative for {sym} (blocks more than 60% or fewer than 2% of the nights its strategy held): {conds}.",
    "cost_note": ("{job} ({nick}) made {rt} round trips in {sessions} sessions ({per_day} a day) and paid {cost}% in costs over "
                  "{period}: {avg} bps per unit traded on average against a normal {normal} bps, because fills at the open and "
                  "in the last bar cost double. {forced}% of its round trips ended only because of the sell-before-close rule."),
    "cost_recal": ("Recalibration's score for the current job: {s1} with its usual {c1} bps cost (its pick: {ch1}; it would {d1}), "
                   "{s2} with the study's {c2} bps (its pick: {ch2}; it would {d2}). Nothing was changed."),
    "recal_keep": "keep the current job",
    "recal_change": "propose a change",
    "period": {"long": "{days} days", "90d": "the last 90 days"},
    "caveat": ("These are backtests on IEX prices with {slip}x the usual trading cost at the open and in the last bar. "
               "Only the walk-forward ('out of sample') figures are an honest test; the full-period figures show where the money came from."),
}

COST_STUDY: Dict[str, str] = {
    "score_no": ("Recalibration's score does not usefully predict the following week. Replayed every Friday for {weeks} weeks on "
                 "{syms} symbols with only the data it would have had, the rank correlation between each strategy's score and its "
                 "next week was {rho} (95% range {lo} to {hi}; 1 would be perfect, 0 no link at all); the top fifth of scores "
                 "averaged {top}% the next week, the bottom fifth {bottom}%."),
    "score_yes": ("Recalibration's score predicts the following week a little: rank correlation {rho} (95% range {lo} to {hi})."),
    "picks": ("Its picks made {mean}% a week on average ({sum}% summed over {n} symbol-weeks) against {bh}% for buy and hold scaled "
              "to the same time in the market; they beat that benchmark in {beat}% of the weeks (difference {d}% a week, 95% range "
              "{dlo} to {dhi})."),
    "no_job_gap": ("Once a symbol has a job, recalibration never goes back to 'no job': it only compares the job with other "
                   "strategies. In the replay every symbol had a job within {weeks_first} weeks, and {neg} job-weeks were kept "
                   "although the job's own score had fallen below zero."),
    "edge": ("Before costs the strategies earn almost nothing per trade on intraday bars: the typical round trip made {g5} bps on "
             "5-minute bars and paid {c5} bps in costs ({cp5} of {n5} combinations clearly above zero before costs, about {fp} "
             "expected by luck across all {ntot} tests)."),
    "jobs": "The five live jobs make between {gmin} and {gmax} bps per round trip before costs and pay {cmin} to {cmax} bps; none is clearly above zero.",
    "slower": ("Slower bars trade less, so they lose less: the typical combination ended {n5}% (5-minute), {n15}% (15-minute), "
               "{n60}% (hourly) and {nd}% (daily, over a shorter period because of a 200-day filter) after costs. Per trade the gross grows ({g5}, {g15}, {g60} and {gd} bps) "
               "but so does the cost, because a larger share of fills fall at the open or the close."),
    "close": ("Selling before the close and buying back next morning makes up {lo}-{hi}% of the jobs' costs ({spy}% for SPY). "
              "Even with all of that cost removed (an upper bound) {neg} of {n} jobs would still have lost money."),
    "fills": ("The paper fills are close to the quoted middle price in quiet hours (median {mid} bps from it over {n} fills; SPY and "
              "QQQ within about 1 bp), so 2 bps per side is on the high side for the big ETFs. IEX quotes for single stocks and USO "
              "are too thin to measure their real spreads (they show {tsla} bps for TSLA at midday, far above the market's real spread), "
              "and fills at the open can't be judged against a quote from IEX alone."),
    "walk_none": ("With an honest walk-forward, {beat} of {tests} strategy x symbol x timeframe tests beat buy and hold scaled to the "
                  "same time in the market after costs; about {fp} would be expected by luck, so nothing beat it beyond chance. "
                  "Recalibration's own 'best of all strategies' pick beat it in none of the {nsingle} symbol x timeframe tests "
                  "and was clearly worse in {worse}."),
    "walk_some": ("With an honest walk-forward, {beat} of {tests} tests beat buy and hold scaled to the same time in the market after "
                  "costs, more than the {fp} expected by luck."),
    "recommend": ("Recommendation: treat the current strategies and recalibration as having no proven edge. The cost question is "
                  "secondary: before costs they earn about nothing, so cheaper trading only makes them lose less. Keep the bot on paper "
                  "as a research tool; don't add more strategies or symbols until the selection step can show it predicts anything."),
    "caveats": ("Caveats: IEX prices and quotes (free feed); today's symbol list for the whole 2 years (flatters the results); "
                "recalibration's next-week results use its own 2 bps cost model; daily-bar strategies were tested holding overnight, "
                "which the live bot doesn't do today."),
}
COST_OPTIONS = [
    ("Let recalibration choose 'no job' (and buy and hold) as challengers, and drop a job whose score falls below zero",
     "{neg} job-weeks were kept with a negative score; every symbol got a job within {weeks_first} weeks and kept one."),
    ("Score against buy and hold scaled to the same time in the market instead of against zero",
     "Picks averaged {mean}% a week against {bh}% for that benchmark; a strategy that can't beat it isn't worth the costs."),
    ("Raise min_edge",
     "Weak: the score doesn't predict the next week (rank correlation {rho}), so a higher bar only changes jobs less often."),
    ("A minimum edge per trade (gross per round trip at least 2x its cost, measured on train days)",
     "Only {cp5} of {n5} 5-minute combinations are clearly above zero before costs (about as many as luck gives); this filter would leave most symbols with no job, "
     "which the evidence supports."),
    ("Penalise turnover more in the score (turnover weight {tw} today)",
     "Costs are {c5} bps per round trip against {g5} bps gross; it reduces the damage but doesn't create an edge."),
    ("Move to slower bars (hourly or daily)",
     "Losses shrink with fewer trades ({n5r}% on 5-minute bars to {ndr}% on daily for the typical combination), but no timeframe beat time-matched buy and hold "
     "in the walk-forward; daily would also need the close rule changed."),
    ("Lower the cost assumption for SPY and QQQ (about 0.5-1 bp per side)",
     "Matches the fills, but auto_SPY makes {spy_g} bps gross per round trip (95% range {spy_lo} to {spy_hi}), so it stays at about zero."),
]

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
    "Recalibration": ("The nightly check of which strategy each symbol should trade, tested only on days the strategy "
                      "had not been tuned on; it switches only when the new choice is clearly better."),
    "Profit lock": ("Once the day's gain reaches a set level, the bot watches the day's best result; if the gain then falls back "
                    "by more than a set share of it, the bot sells everything to keep the rest and stops for the day."),
    "Daily loss limit":"If the account is down this much in a day, the bot sells everything and stops for the day.",
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
