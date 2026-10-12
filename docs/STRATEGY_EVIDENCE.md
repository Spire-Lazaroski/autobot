# Strategy evidence: what the bot has, what is missing

Written 2026-10-05. Our own numbers come from `data/cost_study.json` (5-minute bars, 11 stocks/ETFs,
2 years, after costs) and `data/daily_study.json` (daily bars, 17 stocks/ETFs, July 2021 to
October 2026, traded at the next open, long only, after costs). Published evidence is cited by
author and year; published results are usually weaker after publication (see the caveat at the end).

## 1. What the bot has (18 strategies)

| Strategy | Family | 5-min bars: net per trade | Daily bars: per year / worst drop |
|---|---|---|---|
| sma_cross | trend | −3.5 bps, 0 of 11 clearly positive | +13.8% / −15% |
| ema_cross | trend | −3.9 bps, 0/11 | +13.6% / −14% |
| macd | trend | −3.8 bps, 0/11 | +12.4% / −14% |
| momentum | trend | −3.9 bps, 0/11 | +14.2% / −12% |
| donchian_breakout | breakout | −4.2 bps, 0/11 | +12.2% / **−9%** |
| rsi_reversion | mean reversion | −2.9 bps, 0/11 | +5.7% / −5% (in the market 10%) |
| bollinger_reversion | mean reversion | −3.4 bps, 0/11 | +8.6% / −10% |
| connors_rsi2 | mean reversion (daily design) | not run intraday | +2.4% / −3% (in the market 6%) |
| ibs_reversion | mean reversion (daily design) | not run intraday | +12.3% / −11% |
| vwap_reversion | intraday | −2.6 bps, 1/11 | intraday only |
| opening_range_breakout | intraday | −2.8 bps, 0/11 | intraday only |
| intraday_momentum | intraday | −4.0 bps, 3/11 (before costs) | intraday only |
| noise_area | intraday | −2.9 bps, 1/11 | intraday only |
| regime_switch, perf_rotation, ensemble_vote | meta (pick among the above) | follow their parts | best-of-all walk-forward: +13.3% / −14% |
| news_guard | news filter | no edge in the news-guard study | — |
| buy_and_hold | benchmark | — | **+24.2% / −24%** |

What this says: on 5-minute bars no strategy pays for its costs (about 1 bp gross per trade against
about 4.5 bps of cost). On daily bars all of them make money, but none beats holding; they trade
return for smaller drops. The 5 "clearly positive" combinations out of 121 on 5-minute bars are
before costs and close to the ~3 that luck alone would give (1 in 40).

Two gaps stand out: **every strategy is per symbol and on-or-off** (none compares symbols with each
other, none sizes by risk inside the signal), and **all of them look at price alone** (no calendar,
no earnings, no market-wide signal).

## 2. Well-known strategies the bot does not have

| Candidate | What it does | Published evidence | Fits the bot? |
|---|---|---|---|
| **Volatility targeting** | Hold, but scale the position down when the stock has been jumpy (e.g. target 15% yearly volatility, position = target / recent volatility, max 1) | Moreira & Muir (2017): volatility-managed portfolios raise risk-adjusted returns across many factors; mixed results in later replications (Cederburg et al., 2020) | **Yes**, per symbol and daily; the bot already handles part positions. Aims at our exact problem: keep most of buy-and-hold's return with smaller drops |
| **Time-series momentum, 12 months** | In while the last 12 months' return (skipping the latest month) is positive; checked monthly | Moskowitz, Ooi & Pedersen (2012), 58 futures markets, 1985–2009; Hurst, Ooi & Pedersen (2017) back to 1880 | **Yes** (our `momentum` uses 20 bars; this is a much slower, monthly version). Few trades |
| **Trend timing, 10-month average** | In while the month-end price is above its 10-month average | Faber (2007): similar returns to holding, much smaller drops, 1900s onward | Already tested as "above 200-day average": +14.8%/yr, worst drop −17% |
| **Cross-sectional momentum (relative strength)** | Each month hold the N symbols with the best 6–12 month return | Jegadeesh & Titman (1993); one of the most replicated results, with sharp crashes after bear markets (Daniel & Moskowitz, 2016) | **No, needs a new layer**: the bot's strategies see one symbol at a time |
| **Dual momentum** | Relative strength across assets plus "only if its own trend is up, else cash/bonds" | Antonacci (2014, book); practitioner evidence, less academic testing | Needs the same new layer |
| **Turn of the month** | Hold only from the last trading day of a month to the third of the next | Ariel (1987); Lakonishok & Smidt (1988); McConnell & Xu (2008): most of the US market's return fell in these days | **Yes**, calendar, per symbol, about 12 round trips a year |
| **Overnight holding** | Buy at the close, sell at the open | Cliff, Cooper & Gulen (2008); Lou, Polk & Skouras (2019): much of the US stock return arrives overnight | Opposite of today's flat-before-close rule; two trades a day, so costs decide it. Our overnight study looked at holding vs flattening, not this |
| **Pre-FOMC drift** | Hold the 24 hours before a Fed announcement | Lucca & Moench (2015), 1994–2011; later samples report it much weaker | Calendar (8 days a year); needs the Fed calendar. Small, rare |
| **Post-earnings drift** | After a positive earnings surprise, hold for some weeks | Bernard & Thomas (1989); Martineau (2022): largely gone in large US stocks | Needs surprise data we don't have (we have dates and news scores only). Evidence is weak for our symbols |
| **Pairs trading** | Two related stocks: buy the laggard, short the leader, close when they meet again | Gatev, Goetzmann & Rouwenhorst (2006), with profits falling over time (Do & Faff, 2010) | Needs shorts (our shorts lost) and a two-symbol layer |
| **Low volatility** | Prefer calmer stocks | Ang et al. (2006); Frazzini & Pedersen (2014) | Partly there: `risk_balanced` sizing already gives calmer symbols more dollars |
| **Short-term reversal** | Buy last week's losers | Jegadeesh (1990); Lehmann (1990); costs absorb most of it | Our RSI / IBS / Bollinger daily strategies are the per-symbol form |

## 3. Caveats that apply to every row

- Published effects shrink: McLean & Pontiff (2016) find returns about 26% lower out of sample and
  about 58% lower after publication. Harvey, Liu & Zhu (2016) argue for a much stricter bar
  (t-statistic above 3) because so many strategies have been tried.
- A strategy only counts here if it beats buy and hold (or matches it with clearly smaller drops)
  **on our data, after our costs, out of sample**, the same test the current ones went through.
- Our symbol list was chosen in 2026, which flatters holding (NVDA +1,124%).

## 4. Proposed next step

Test the ones that fit the bot today in the daily study, on the same data, costs and fills:

1. Volatility targeting (on buy and hold, and on the trend strategies)
2. Time-series momentum, 12 months, monthly checks
3. Turn of the month
4. Cross-sectional momentum (top 3–5 of the universe, monthly), inside the study only. It would
   need a portfolio-level layer in the bot if it earns a place.

Overnight holding can be added from the 5-minute data if wanted. Pre-FOMC, earnings drift and
pairs are left out: the evidence is weak for our symbols, or they need data or shorts we don't have.

## 5. Results on our data (daily study, 2026-10-05)

Same data, costs and fills as section 1 (17 symbols, July 2021 to October 2026, next-open fills,
long only). Buy and hold: +24.2% a year, worst drop −24%, Sharpe 1.33.

| Candidate | Per year | Worst drop | Sharpe | vs buy and hold (bps/day, 95% range) |
|---|---|---|---|---|
| Volatility targeting (on hold) | +22.2% | −21% | 1.39 | −0.8 (−1.9 to +0.2), not clearly different |
| Momentum 12-1, monthly | +16.9% | −17% | 1.22 | −2.7 (−5.3 to 0.0) |
| Vol target + momentum 12-1 | +15.5% | −13% | 1.29 | −3.2, clearly worse |
| Turn of the month | +4.8% | −9% | 0.66 | −7.2, clearly worse |
| Relative strength, top 4 monthly | +31.8% | −31% | 1.10 | +3.4 (−3.1 to +9.9), not clearly different |
| Relative strength top 4 + own trend | +31.4% | −31% | 1.09 | +3.3 (−3.2 to +9.7) |

News rules (daily versions; only NVDA, AAPL, TSLA, SPY, QQQ have scored news, mostly from
October 2024; earnings only for the three stocks, 8 each), compared with their own base on the
same symbols and days: every rule is ahead by +0.3 to +2.7 bps a day, **none clearly** (all
95% ranges include zero), from a small sample with 6 rules tried. The bot's `news_guard` itself
never triggers on daily bars (30-minute window, intraday-only earnings blackout).

Reading: relative strength earns the most but with the largest drop, and our 2026-chosen symbol
list flatters it most (it would have held NVDA, AMD, LLY). Volatility targeting is the closest to
holding with a slightly smaller drop. Nothing clearly beats buy and hold.

## 6. 150 years of US stocks, and early-warning signals (macro study, 2026-10-05)

`python -m autobot.macro_study` (data: Shiller's monthly S&P since 1871, extended with SPY; FRED;
the Global Macro Database 2026_09). Monthly decisions, total return, cash = 3-month bills,
0.1% per switch. Shiller's prices are monthly averages, which flatter timing rules, so the main
rule is also shown acting one month later (the honest version).

| Rule | Per year | Worst drop | Sharpe | 2008-09 drop | 2000-02 drop |
|---|---|---|---|---|---|
| Buy and hold (1871 on) | +9.4% | **−82%** (1929-32) | 0.44 | −49% | −41% |
| 10-month average, acting a month later | +9.2% | −49% | 0.58 | −5% | −7% |
| 12-1 momentum, acting a month later | +8.3% | −52% | 0.47 | −14% | −10% |
| Volatility targeting | +9.0% | −67% | 0.46 | −43% | −34% |
| Yield curve inverted → out (1934 on; hold +11.2%) | +11.7% | −49% | 0.69 | −49% | −34% |
| Sahm rule, real time → out (1960 on; hold +10.8%) | +9.1% | −30% | 0.49 | −14% | −20% |
| Credit spread up 0.5 pt → out (1919 on; hold +10.6%) | +9.2% | −53% | 0.49 | −30% | −39% |
| GMD annual rules (GDP fell, inflation > 5%, unemployment up, curve inverted, banking crisis) | +8.1% to +9.7% | −52% to −82% | 0.37 to 0.48 | mostly −49% | −41% |

Reading: over 150 years the 10-month average gives up almost no return and roughly halves the
worst drop, avoiding most of 1973-74, 2000-02, 2008-09 and 2022 (not the fast 2020 crash). The
FRED warnings help in some crashes but cost return; the GMD annual figures arrive too late to
help (at best July of the next year) and none improves on holding. None of the differences in
yearly return is clearly beyond chance. The trend filter is about smaller drops, not more return.

**Correction after the core holding's phase 1 (docs/CORE_DESIGN.md):** on real daily month-end
closes (FRED S&P 500, 2016-2026) the same rule made +6.6% a year against +13.3% for holding, with
21 switches; Shiller's monthly averages hide the quick dips that cause those switches, so the
table above overstates the 10-month rule even in its "acting a month later" form.

## 7. Trade rules on 5-minute bars (trade-rules study, 2026-10-05)

`python -m autobot.trade_rules_study` (`autobot/trade_rules.py`; settings fixed before the run).
126 strategy x symbol pairs plus the 5 live jobs, 2 years, after costs, flat before the close.

| Rule | All pairs | Trades | Daily difference vs alone | Live jobs |
|---|---|---|---|---|
| Strategy alone | −23.3% | 99,695 | — | −26.6% |
| Churn brake (2 entries / 60 min, then stand aside) | −21.6% | −7% | +0.45 bps (+0.23 to +0.67), 13 pairs better / 2 worse | −24.6% |
| **No quick re-entry** (same-session re-entry only beyond the last position's best close) | **−11.8%** | **−37%** | **+2.86 bps (+2.04 to +3.68), 42 better / 1 worse** | **−15.7%** |
| Both | −11.7% | −38% | +2.89 bps, 42 better / 1 worse | −15.6% |

About 3 pairs would look "clearly better" by luck; 42 is far beyond that. The rule roughly halves
the losses by cutting a third of the trades, but it does not create an edge: the live jobs stay
negative after costs. Rule 1 (tiny orders): 5 of the bot's 46 logged orders were under 10% of the
usual size ($1,974 traded for nothing but cost).

## 8. Markov switching regimes (Markov study, 2026-10-06)

`autobot/markov.py`: a 2-state Gaussian Markov switching model (calm / turbulent), refitted
walk-forward and used only through FILTERED probabilities (no later bars). Intraday returns are
first divided by their usual size at that time of day, so the open's normal jumpiness is not
"turbulence". `python -m autobot.markov_study --part a|b`.

**A: inside regime_switch on 5-minute bars** (the volatility rule replaced by P(turbulent) > 0.5;
refit every 5 sessions on the previous 60; 11 symbols + the 3 live regime_switch jobs, 446 days).

| | Fixed rule (today) | Markov |
|---|---|---|
| Share of bars called turbulent | 11% | 18% |
| Next-hour moves when "turbulent" vs otherwise | 1.35x | **1.56x** |
| All pairs, after costs | −24.2% | −25.2% (−0.3 bps/day, −3.2 to +2.6) |
| Live jobs (TSLA, USO, QQQ) | −34.8% | −41.1% (−2.3 bps/day, −5.7 to +1.1) |

The Markov model is the better turbulence detector, but standing aside at better-chosen times
does not make the 5-minute strategies profitable: no difference beyond chance, slightly worse on
the live jobs.

**B: daily risk overlay** (hold, but cut exposure when turbulence is likely; refit every 21 sessions
on the previous 500; next-open fills, open costs 2x).

| | Per year | Worst drop | Sharpe |
|---|---|---|---|
| 17 stocks, mid-2022 on: hold | **+29.7%** | −18% | **1.66** |
| volatility targeting | +26.2% | −17% | 1.65 |
| Markov scaled / on-off | +17.2% / +18.0% | −13% / −15% | 1.59 / 1.50 (both clearly below hold and vol targeting) |
| VOO, mid-2022 on: hold | **+18.7%** | −18.7% | 1.16 |
| VOO Markov on/off (about 5 trades a year) | +16.8% | **−10.0%** | **1.51** (not clearly different) |
| S&P 500 2018-2026: hold | **+13.0%** | −33.9% | 0.72 |
| volatility targeting | +12.7% | −20.8% | **0.88** |
| Markov on/off | +8.7% | **−13.0%** | 0.84 |

Reading: on single stocks the overlay clearly costs return. On the index it is insurance: it
roughly halves the worst drop for about 2-4 points of return a year; volatility targeting buys
less protection more cheaply. Nothing here beats holding on return.

## 9. Dynamic band exits (band study, 2026-10-06)

The user's idea: volatility-scaled sell levels instead of fixed %. `autobot/exits.py` `atr_stop`
(sell more than k ATRs below the best close since entry) and `band_profit` (sell above the 20-bar
average plus k ATRs); `python -m autobot.band_study`. Tested on top of today's live set-up
(strategies + no quick re-entry), long only, 11 symbols, 2 years, after costs; settings
{none, 2, 3, 4} x {none, 2, 3} fixed before the run, chosen walk-forward (60 / 20 sessions).

| | 5-minute (128 pairs) | 1-hour (121 pairs) |
|---|---|---|
| No bands | −9.7% on the test days | −4.3% |
| Walk-forward bands | −11.1% (−0.42 bps/day, −2.88 to +2.04) | −5.0% (−0.17 bps/day, −0.96 to +0.62) |
| Pairs clearly better / worse (luck ~3) | 1 / 2 | 0 / 0 |
| Best setting in hindsight | profit at average + 2 ATR: −10.5% vs −11.8% per pair, 43% fewer trades | stop 2 ATR + profit average + 2 ATR: −5.5% vs −6.1% |
| Chosen most often | none (18%) | none (44%) |

Reading: the bands cut trades a lot (the profit band by about 40%) but the result per day does not
change beyond chance in either direction; the setting chosen on past days does no better than
none. Volatility-scaled exits behave like the fixed-% ones (section 2 of the shorts / take-profit
study): exits re-time a trade, they do not create an edge the entry lacks.

## 10. Day-anchored and adaptive bands (band modes study, 2026-10-06)

Same set-up and grid as section 9 (5-minute, long only, 11 symbols, 2 years, after costs), three
ways to draw the bands, each chosen walk-forward (60 / 20 sessions), plus a free choice across all three:

- **rolling**: 20-bar average of the close, width = 20-bar average true range (section 9).
- **session**: the day's volume-weighted average price from the open; width blends today's bar
  ranges with the previous 5 sessions' (today's weight grows as the day goes on: bars / (bars + 20)).
- **adaptive**: Kaufman adaptive average (follows fast in a clear trend, slow in chop); width = an
  exponentially weighted true range, so recent bars count more and the width reacts to the deviation.

| | rolling | session | adaptive | free choice |
|---|---|---|---|---|
| Walk-forward (no bands: −9.7%) | −11.1% | −12.4% | −12.5% | −11.9% |
| Daily difference, bps (95% range) | −0.42 (−2.88 to +2.04) | −0.75 (−2.92 to +1.43) | −0.77 (−2.92 to +1.39) | −0.62 (−3.09 to +1.84) |
| Pairs clearly better / worse (luck ~3) | 1 / 2 | 1 / 2 | 1 / 3 | 1 / 2 |
| Best setting in hindsight (none: −11.8%) | profit avg + 2 ATR: −10.5% | profit avg + 2 ATR: −10.8% | none | rolling profit avg + 2 ATR |
| Chosen most often | none (18%) | none (17%) | none (19%) | session profit (10%) |

Reading: anchoring the band to the day, or letting it adapt to the trend and the recent deviation,
does not help; all three versions land within chance of no bands and slightly below it. The live
bot keeps exits off (no bands).

## 11. Machine learning: gradient-boosted trees and an LSTM (ML study, 2026-10-06)

Question (the user's, 2026-10-06): can a model that combines everything we have (price, volume,
indicators, the Markov reading, SPY, news; then the economy) see what rules can't, especially for
exits? Code: `autobot/ml_features.py` (inputs), `ml_study.py` (trees), `ml_lstm.py` (LSTM),
`ml_macro.py` (economy); results in `data/ml/`. Separate environment `.venv-ml` (the bot's is untouched).

Set-up, fixed before any run:
- **Inputs**: 35 per 5-minute bar, each only from what was known when the bar ended (tests cut off the
  future and check that earlier rows do not change). News now covers all 11 symbols (keyword-scored;
  the gold, silver and oil funds have very few stories).
- **Targets**: the next hour's return and the next hour's swing (high minus low), never past the 15:55 close-out.
- **Walk-forward**: one model for all 11 symbols, refit every 20 sessions on all earlier sessions,
  scored on the next 20: 386 unseen sessions (March 2025 to September 2026). Costs as in every study.
- **Three uses**: exit (sell a strategy's position early when the model expects the next hour below
  -5 bps; back in only after the strategy goes flat), entry (a stand-alone strategy: buy when the
  model expects more than +5 bps, sell when it turns negative), swing (forecast accuracy).

| | Trees | LSTM (last 2 hours of bars) | Trees + economy |
|---|---|---|---|
| Next-hour direction right | 49.8% | 50.1% | 49.1% |
| Exit, vs strategies alone (−5.4%) | −11.1%; −1.7 bps/day (−4.4 to +1.0) | −8.2%; −0.8 bps/day (−2.0 to +0.4) | −10.3%; −1.4 bps/day (−4.5 to +1.6) |
| Exit, pairs clearly better / worse (128, luck ~3) | 2 / 2 | 0 / 3 | 1 / 1 |
| Entry, vs cash 0 and holding open-to-close +5.0% | −10.2% | −2.2% | −12.0% |
| Swing forecast error (% of price) | **0.246** | 0.250 | 0.248 |

Swing, same rows: simple rule (average swing at this time of day, scaled by today's volatility) 0.330;
straight-line forecast on the same inputs 0.267; trees 0.246 (better than the straight line on 88% of
days, clearly). The LSTM is slightly worse than the trees (better on 44% of days), and the economy
makes the trees slightly worse (48%). The trees lean on recent volatility (about 70%) and the time of day.

Where the faint direction signal comes from (rank correlation of forecast and outcome, 95% range):

| | Within one stock's day | Across stocks at the same time | One stock's good vs bad days |
|---|---|---|---|
| Trees | +0.07 (+0.06 to +0.08) | +0.004 | −0.09 |
| Trees + economy | +0.13 | +0.008 | −0.22 |
| LSTM | +0.14 (+0.13 to +0.15) | +0.008 | −0.26 |

The within-day part holds on bars with a full hour ahead and when each forecast is compared only with
its own average so far that day (+0.05 to +0.10, known at the time). But the forecasts' level is wrong
from day to day (negative last column), which is why trading on them loses. A likely reason for the
within-day part: the free IEX feed's 5-minute closes are noisy last trades, so a close that is too high
tends to be followed by a lower one. That kind of pattern can't be traded at those prices after costs.

Reading:
- **Direction (entry, exit)**: no model beats costs; the exit makes strategies worse with all three.
  This agrees with sections 5 to 10.
- **Size of the next move**: predictable, and the trees do it best. This is the one result with a
  possible use (position size or stop width scaled to the expected swing), which would need its own
  test, shadow first.
- **The economy**: adds nothing at a 1-hour horizon (over 2 years the monthly and annual figures took
  only 3 to 23 different values). Its use stays the slow, years-long picture (sections 6 and the core holding).
- Nothing changes in the live bot.

**Follow-up: sizing by the expected swing (step A backtest, 2026-10-06).** Same 386 sessions and costs;
each trade sized at its opening bar (¼ to 2 times normal, normal = the symbol's previous 20 sessions)
by fixed size (today), daily ATR (the bot's `risk_balanced` idea), the last 20 bars' ATR, or the trees'
swing forecast; all scaled to the same average amount (`autobot/swing_sizing_study.py`).

| Same average amount | Fixed | Daily ATR | Bars ATR | Swing forecast | Swing vs fixed: result per risk (95% range) |
|---|---|---|---|---|---|
| 128 strategy sets: total / daily ups-downs | −5.4% / 37.5 bps | −6.4% / 31.8 | −7.8% / 28.3 | −9.6% / 27.6 | −0.91 (−1.44 to −0.40), worse |
| 7 live jobs | −6.1% / 26.5 | −7.2% / 24.9 | −6.8% / 23.3 | −6.2% / 22.9 | −0.19 (−0.88 to +0.52) |
| Holding open to close, 11 symbols | +5.0% / 111 | +2.9% / 98 | 0.0% / 88 | +6.2% / 95 | +0.07 (−0.33 to +0.58) |

Reading: sizing by the swing forecast makes days calmer (13% to 26% smaller daily ups and downs, worst
day smaller) but does not improve the result per unit of risk, and it does not beat the plain ATR
versions clearly anywhere. For the strategies it is clearly worse: their calm hours are not better
hours. Step B (a shadow test) was not started; the bot keeps fixed sizing.

## 12. Volatility targeting for the core holding (2026-10-06)

Question: should the core move between 15% and 45% of the account (30% normally) by a volatility
forecast? `autobot/vol_target_study.py`; S&P 500 daily closes from FRED (10 years; scored from
September 2019, after 750 training days), dividends 1.4% a year flat, cash earns 0, decided at the
close and traded a day later, only when more than 5 points from the target, 2 bps costs.
Forecasts: EWMA (RiskMetrics), HAR (Corsi), trees, the VIX (FRED VIXCLS), HAR + VIX, trees + VIX.

| | Plain 30% | EWMA | HAR | Trees | VIX | HAR + VIX | Trees + VIX |
|---|---|---|---|---|---|---|---|
| Yearly, as traded | +5.2% | +4.0% | +4.3% | +4.4% | +3.8% | +4.9% | +4.4% |
| Yearly, same average amount | +5.2% | +4.7% | +4.7% | +4.7% | +4.4% | +4.5% | +4.3% |
| Worst drop of the account | −11.2% | −5.9% | −6.7% | −6.1% | −6.6% | −8.7% | −6.8% |
| Covid 2020 / 2022 bear / 2025 tariffs | −11.0 / −7.6 / −6.1 | −5.8 / −4.9 / −4.6 | −6.6 / −6.2 / −5.2 | −5.9 / −5.1 / −4.2 | −6.5 / −6.0 / −4.7 | −6.3 / −8.4 / −5.2 | −5.8 / −6.4 / −5.0 |
| Per risk vs plain (same amount, 95% range) | | +0.08 (−0.19 to +0.31) | +0.07 | +0.13 (−0.18 to +0.39) | +0.02 | +0.06 | +0.04 |
| Trades a year | 1 | 11 | 44 | 68 | 14 | 43 | 60 |

Reading: every version roughly halves the losses in the three crashes, at a cost of about half a
point a year in normal times; the gain per unit of risk is small and within chance (as Cederburg et
al. 2020 found). The VIX tracks next week's volatility best (correlation 0.73 vs 0.61 for EWMA) but
runs above it (it includes a fear premium), and adding it to HAR or the trees does not help. The
simple EWMA rule does as well as anything, with the fewest trades. Only 7 years and 3 crashes.
Nothing changes in the live bot unless the user decides to.

## 13. Scalping: sell as soon as the costs plus a small premium are covered (2026-10-06)

The user's "fast finger" idea: buy on the strategy's signal, sell with a resting limit order at entry x
(1 + round-trip cost + premium), buy again with new levels while the signal is on. `autobot/scalp_study.py`:
the limit fills only when a bar trades a cent beyond it (intrabar, not just at closes); stops (if any)
count first when a bar reaches both; 128 strategy sets, the same 386 unseen sessions, after costs.

| Variant | Total | Trades | Winning trades | Avg win / avg loss (bps) | vs strategy alone, bps/day (95% range) | Pairs worse |
|---|---|---|---|---|---|---|
| Strategy alone | −5.2% | 51,285 | 43% | +60.9 / −48.7 | | |
| +2 bps, no stop | −69.2% | 267,711 | **85%** | +2.6 / −53.8 | −29.1 (−32.2 to −26.0) | 128 of 128 |
| +10 bps, no stop | −51.2% | 171,041 | 76% | +10.3 / −55.0 | −17.2 (−19.7 to −14.7) | 127 |
| +5 bps, stop 1x | −30.3% | 80,432 | 40% | +5.7 / −13.3 | −8.0 (−11.8 to −4.2) | 60 |
| Dynamic (a quarter of the expected swing), stop 1x (best) | −25.3% | 87,449 | 48% | +23.9 / −30.4 | −6.2 (−9.7 to −2.8) | 47 |

All 12 variants are clearly worse than the strategies alone; no pair is better anywhere. Reading: the
small targets win 76-85% of the time, but each win is a few bps while the trades that miss carry the
full loss, and every new set of boundaries pays the costs again (up to 5x the trades). This is the
"win rate is not profit" trap the day-trader studies describe (Barber, Lee, Liu & Odean 2014; Chague,
De-Losso & Giovannetti 2019).

## 14. Trading only when strategies agree (agreement test, 2026-10-07)

The user's idea: combine strategies and trade only when several agree. `autobot/agreement_study.py`:
the 11 five-minute strategies (4 families: trend 4, mean reversion 2, breakout 1, intraday 4), default
settings, combined per symbol and bar; through no quick re-entry, after costs, flat before the close,
the same 386 unseen sessions. Single strategies (121 pairs): −5.3%, −1.3 bps/day vs cash (not clear).

| Combined signal | Total | In the market | Trades | vs single strategies, bps/day (95% range) | vs cash |
|---|---|---|---|---|---|
| ≥3 strategies agree, long | −4.4% | 74% | 6,716 | +0.4 (−3.2 to +4.0) | −0.9, not clear |
| ≥5 agree, long | −10.4% | 37% | 6,269 | −1.3 (−4.1 to +1.4) | −2.7, not clear |
| ≥7 agree, long | −0.8% | 5% | 2,594 | +1.2 (−1.4 to +3.8) | −0.2, not clear |
| ≥2 families agree, long | −6.0% | 81% | 6,261 | +0.1 | −1.3, not clear |
| ≥3 families agree, long | −11.1% | 35% | 6,408 | −1.6 | −2.9, not clear |
| all 4 families agree, long | −0.5% | 0.1% | 76 | +1.2 | −0.1 |
| any of the above with shorts | −10.4% to −37.4% | | 4,644-15,980 | −1.5 to −10.5, mostly clearly worse | clearly worse |

Reading: agreement cuts the number of trades a lot, but none of the long-only versions is clearly
better than the single strategies or than cash; the "best" ones are best mostly because they are
almost never in the market (5% and 0.1% of the time). Agreement across families is rare (all four
agree on 0.1% of bars) and does not carry an edge either. Adding shorts makes every version clearly
worse. Combining signals that each have no edge after costs gives a combined signal without one,
in line with the forecast-combination literature (Bates & Granger 1969; Timmermann 2006) and the
trees in section 11.

## 15. GARCH-family volatility models for the core (2026-10-07)

Added to the section 12 test (same data, days, rules and costs): GARCH(1,1), GJR-GARCH and EGARCH, all
with Student-t errors (`arch` package), parameters refit yearly on earlier days and run forward day by day.

| | EWMA (live) | GARCH | GJR-GARCH | EGARCH | VIX |
|---|---|---|---|---|---|
| Forecast error, next week's daily volatility | 0.400% | 0.367% | 0.344% | **0.338%** | 0.462% |
| Correlation with what followed | 0.61 | 0.70 | **0.74** | 0.73 | 0.73 |
| Worst drop of the account (same amount) | −6.9% | −6.3% | −6.4% | **−6.2%** | −7.8% |
| Covid 2020 / 2022 bear / 2025 tariffs | −5.8 / −4.9 / −4.6 | −6.1 / −5.6 / −5.0 | −6.0 / **−4.5** / −4.8 | −5.8 / −4.7 / −4.9 | −6.5 / −6.0 / −4.7 |
| Per risk vs plain 30% (95% range) | +0.08 (−0.19 to +0.31) | +0.10 | +0.08 | +0.10 (−0.20 to +0.37) | +0.02 |
| Trades a year | **11** | 36 | 42 | 38 | 14 |

Reading: as the literature says (Hansen & Lunde 2005), the models where volatility rises more after
drops (GJR, EGARCH) forecast volatility best, about 15% less error than EWMA. But the portfolio barely
changes: worst drops and crisis losses are within a point of EWMA, the per-risk difference is far inside
chance, and they trade 3-4 times as often (their targets differ from EWMA's by about 6 points on an
average day). A better volatility forecast does not translate into a clearly better core here. The
live core keeps the EWMA rule (simpler, no extra library in the bot, fewest trades).

## 16. Pairs trading with cointegration (VECM / Johansen, 2026-10-07)

`autobot/pairs_study.py`: each of the 55 pairs of our 11 symbols, 5-minute log prices, flat before the
close. In each window a pair is traded only if, on the previous 60 sessions, the Johansen trace test finds
a long-run relation at the 1% level (stricter than 5%: random unrelated prices passed the 5% test up to 17%
of the time in a check) and its gaps halve in 30 minutes to 2 days. Traded on the next 20 sessions: enter
when the gap reaches 1.5 / 2 / 2.5 of its usual size (buy the cheap one, short the dear one), out when it
closes, at 4x, or before the close. After costs on both legs (borrowing costs for the short leg left out),
the same 386 unseen sessions.

| | Enter at 1.5x | 2x | 2.5x |
|---|---|---|---|
| Total (9 pairs ever selected, equal weight) | −2.0% | −1.4% | −1.6% |
| vs cash, bps/day (95% range) | −0.5 (−1.3 to +0.3) | −0.4 (−1.2 to +0.5) | −0.4 (−1.2 to +0.4) |
| Trades | 277 | 239 | 194 |
| Pairs clearly positive / negative | 0 / 0 | 0 / 1 | 0 / 0 |

Only 10 of 1,100 pair-windows (0.9%) passed the tests, about what chance alone gives at the 1% level:
our 11 symbols do not hold steady long-run relations on 5-minute bars (gold-silver passed twice). The
best pairs (META-NVDA +7%, AMD-MSFT +5%) were each selected once and are offset by NVDA-TSLA (−9%) and
others. No result is clearly different from cash. Classic pairs trading needs many candidates (hundreds
of stocks, often within one industry) and holds for days to weeks; neither fits a bot that is flat at
every close with 11 symbols.

## 17. Structural VAR impulse responses: how news and market shocks travel (2026-10-07)

Explanation, not a trading rule. `autobot/svar_study.py` (statsmodels VAR, recursive identification).
5-minute bars, 7 symbols with enough news, about 38,000 bars each, 12 lags (1 hour); variables in order:
news tone received in the bar, SPY's return, the stock's return, its jumpiness vs normal for that time.

| Stock | News tone shock: price same bar (bps ± 95%) | after 2 hours | added after the first bar | News predicts jumpiness? (p) | Market shock (SPY +8.9 bps): stock same bar | added after the first bar |
|---|---|---|---|---|---|---|
| SPY | +0.03 ± 0.09 | −0.17 ± 0.34 | −0.2 | yes (0.03) | | |
| QQQ | −0.08 ± 0.12 | −0.20 ± 0.42 | −0.1 | yes (0.001) | | |
| AAPL | −0.10 ± 0.15 | −0.07 ± 0.56 | +0.0 | yes (<0.001) | +9.2 | −0.5 |
| NVDA | −0.33 ± 0.23 | −1.03 ± 0.95 | −0.7 | no (0.26) | +16.3 | −0.5 |
| TSLA | +0.43 ± 0.30 | +0.85 ± 1.13 | +0.4 | no (0.20) | +17.9 | +0.1 |
| META | −0.13 ± 0.19 | +0.02 ± 0.78 | +0.1 | yes (<0.001) | +11.5 | +0.6 |
| AMD | −0.18 ± 0.29 | −0.02 ± 1.07 | +0.2 | no (0.10) | +17.4 | +1.1 |

News never predicts the direction (all p > 0.24) and ordering news after the return changes nothing.
Reading: (1) a market move reaches each stock within the same 5-minute bar (in proportion to its beta) and
less than ±1.1 bps follows afterwards, far below the ~4.5 bps a round trip costs; (2) news tone moves
prices by fractions of a basis point, mostly within the first bar, never clearly in the next two hours;
(3) news does predict jumpiness for SPY, QQQ, AAPL and META. Prices adjust within minutes; what is left
afterwards is volatility, not direction. This is why every 5-minute direction test failed and the
volatility forecasts worked.

Daily, S&P 500 / VIX / yield curve (FRED, 2016-2026, 5 lags, in-sample): a 1-sd S&P rise (+1.1%) lowers the
VIX by 1.5 points the same day, still −1.2 after 20 days; a VIX jump beyond what the same day's S&P move
explains is followed by −22 bps (± 9) in the S&P over 5 days and −17 (± 7) over 20; a yield-curve shock
has no lasting effect. The fear-shock result fits volatility targeting (less exposure when fear rises),
though as an in-sample fit it is not a tested trading rule (the VIX did not improve the core in section 12).

## 18. ARIMA and autocorrelation on 5-minute price changes (2026-10-07)

`autobot/arima_study.py`: each symbol's 5-minute log-price changes inside the session (the overnight move
left out), 2 years; ARMA walk-forward (order by AIC among 5 small models on the previous 60 sessions,
refit every 20) on the same 386 unseen sessions; costs 2 bps per side.

| | Result |
|---|---|
| Lag-1 autocorrelation | −0.020 to +0.009 (95% band ±0.010); 6 of 11 slightly negative beyond the band |
| Ljung-Box over 12 lags | a pattern exists in all 11 (p from 0.0004 down to 10^-26) |
| Variance ratio over 30 min / 1 hour | 0.84-0.99 / 0.77-0.97: moves partly reverse within the hour (stocks most, gold/silver/oil least) |
| ARMA forecast vs a forecast of zero | worse in all 11 (out-of-sample R² −0.14% to −0.60%; one MSFT window blew up) |
| Signs right | 47.8% to 50.3% |
| Typical forecast size | 0.2 to 0.7 bps (a round trip costs about 4 bps) |
| Trading the forecast's side every bar | before costs +12% (+3.1 bps/day, −2.9 to +9.1, not clear); after costs −99% |
| Only when the forecast exceeds the round-trip cost | −15% (−4.2 bps/day, −6.8 to −1.6, clearly worse) |

Reading: the price changes are not a pure random walk: there is a small, statistically real tendency to
partly undo moves within the hour (variance ratios below 1), the pattern market-microstructure research
attributes to prices bouncing between bid and ask and to noisy prints (the free IEX feed). But it is far too
small and unstable to forecast: ARMA fitted on past days does worse than predicting zero, its forecasts are a
fraction of a basis point, and trading them loses to costs whichever way. Same conclusion as sections 11
and 17, from the classical side.

## 19. Batch 1 of the user's strategy list (2026-10-07)

`autobot/batch1_study.py`; 11 symbols, 5-minute bars, the same 386 unseen sessions, costs 2 bps per side
(2x at the opening bar and the close-out), flat at every close, one trade at a time per symbol and rule,
stop counted first when a bar reaches both. Net bps per trade with 95% ranges clustered by day.

| Rule | Trades | Win rate | Net bps per trade (95%) | Per day vs cash | Verdict |
|---|---|---|---|---|---|
| Fibonacci 38.2% / 50% / 61.8% (long and short) | 3,176-4,344 | 40-54% | −6.1 to −6.9 (all clearly negative) | clearly worse | ✗ |
| 0.5 : 2 risk / reward on the bot's entries | 44,302 | 22% | −3.4 (−4.1 to −2.7) | vs the strategies' own exits −1.3: clearly worse | ✗ |
| 1 : 2 risk / reward | 43,327 | 34% | −4.0 (−5.0 to −3.0) | clearly worse | ✗ |
| Gap fill, 0.2-1% gaps | 1,893 | 48% | −7.1 (−10.0 to −4.1) | clearly worse | ✗ |
| Gap fill, gaps over 1% | 1,556 | 47% | −9.8 (−23.7 to +4.0); short side −18.0, clearly | not clear | ✗ |
| **Gap and go, gaps over 1%** | 741 | 28% | **+10.2 (−6.8 to +27.2)**; long side **+24.3 (−2.8 to +51.4)** | +1.8 bps/day (−1.2 to +4.7), not clear | the only positive one; not proven |
| Stop hunt at yesterday's high / low | 2,190 | 33% | −6.0 (−9.4 to −2.6) | clearly worse | ✗ |
| Stop hunt at the first 30 minutes' range | 4,265 | 34% | −4.0 (−5.5 to −2.5) | clearly worse | ✗ |
| Volume profile, 80% rule | 1,211 | 53% | −5.3 (−12.1 to +1.4) | not clear | ✗ |
| Volume profile, value-area breakout | 1,278 | 42% | +1.5 (−6.3 to +9.3); long +7.9 (−4.2 to +20.1) | not clear | ~ |
| Market profile (time at price), 80% rule / breakout | 1,283 / 1,102 | 59% / 41% | −4.4 / +0.7 | not clear | ✗ |
| Overbought short fade (RSI > 80, above the band) | 285 | 24% | −3.3 (−14.4 to +7.9) | not clear | ✗ |
| Residual reversal vs SPY + QQQ (hedged) | 2,555 | | | −3.4 bps/day, clearly worse | ✗ |
| Trees on the residuals (nonlinear stat-arb) | 5,163 | | | −5.4 bps/day, clearly worse | ✗ |

Short side, every rule: worse than the long side in all 9 rules that trade both ways (gap fill short −8.6 /
−18.0, Fibonacci short −7.4 to −8.2, stop-hunt short −5.1 / −5.3, profile breakouts short −5 / −6 bps).

News events (daily, held from the next open, vs SPY, after costs): earnings stories traded in the direction of
their keyword tone, 138 events: −10 bps after 1 day (−49 to +28), −97 after 5 days (−215 to +21), +27 after 20
(−181 to +234): no edge (the keyword tone is a weak reading of an earnings report). Dividend raises or
initiations: only 6 events, far too few to judge.

The option seller's premium (S&P 500 and VIX, 119 months, 2016-2026): the VIX was above the volatility that
followed in 85% of months, by 3.8 points on average (18.8 vs 15.0). A one-month variance-swap seller earned
+2.2 vega a month on average (median +4.1) and +265 over the 10 years, but lost −58 and −49 in February and
March 2020 and −32 in March 2025, with a worst drawdown of −106: a real premium, paid for by rare large losses.

Reading: with about 40 rule-and-side results, one or two would look good by luck. Gap-and-go on large gaps
(long side) is the closest to a real edge and matches the research (large news gaps tend to continue), but it
is not clear beyond chance on 2 years and 11 symbols. Everything else, including every short side, loses
after costs. The option seller's premium is real but belongs to a different kind of trading (options, with
crash risk), not to the bot's stock jobs.

## 20. Gap-and-go confirmation on 8 earlier years and 24 symbols (2026-10-07)

`autobot/gap_long_study.py`: the exact batch 1 rule (gap of 1% or more, the first 5-minute bar follows
through, stop beyond that bar, out at 15:50, 2 bps per side), fixed before the run, on data it had never
seen: Alpaca SIP 5-minute bars (split- and dividend-adjusted), October 2016 to 20 September 2024 (2,005
sessions, the period before the 2-year window), the 24 stock and ETF symbols of the universe settings.

| | Trades | Win rate | Net bps per trade (95%) |
|---|---|---|---|
| All | 3,787 | 30% | +1.3 (−5.3 to +7.9); per day vs cash +0.1 (−0.4 to +0.6) |
| Long | 2,061 | 29% | +2.3 (−5.8 to +10.4) |
| Short | 1,726 | 31% | +0.1 (−10.8 to +11.0) |
| Tech / index ETFs | 1,978 / 196 | 32% / 27% | +5.7 / +6.3 (both ranges include zero) |
| Staples / health / commodity ETFs | 331 / 448 / 834 | | −12.2 / −4.5 / −1.8 |
| Single years | | | from −11.1 (2023) to +22.8 (2016), sign changing year to year |

Reading: not confirmed. On 8 new years the rule is about break-even after costs (+1.3 bps a trade, far
inside chance), with good and bad years alternating; the +24 bps of the long side in batch 1 shrinks to
+2 bps. The batch 1 result was the expected lucky one among about 40 tries. Even with a symbol list that
favours long rules (chosen in 2026), nothing is left after costs.

## 21. Batch 2: inflation, interest rates, sentiment and insiders (2026-10-07)

`autobot/batch2_study.py`; monthly decisions; every input only from when it was public (daily FRED from the
next day, CPI and consumer sentiment from the end of the following month); each rule compared with a fixed mix
holding the same average amount, so holding less can't look like skill. Downloads approved by the user: FRED
UMCSENT, CPIAUCSL, T5YIE, DGS10, DFII10; SEC Form 3/4/5 data sets 2016 Q1 to 2026 Q1.

**Inflation / rates, ETFs 2016-2026 (128 months, 2 bps per switch):**

| Rule | Per year | vs its benchmark (% a year, 95%) |
|---|---|---|
| 60/40 SPY/TLT (benchmark) / SPY / GLD | 8.7% / 15.7% / 11.5% | |
| A: CPI inflation rising → TIP+GLD+DBC, else 60/40 | 8.0% | −0.8 (−6.3 to +4.8) vs 60/40 |
| B: expected inflation above its 6-month average → basket, else 60/40 | 8.9% | +0.1 (−5.3 to +5.4) |
| C: 10-year yield rising → 60% SPY / 40% IEF, else 60/40 | 9.3% | +0.5 (−1.0 to +1.9) |
| D: gold only when the real yield is below its 12-month average | 7.0% | −4.6 (−10.9 to +1.6) vs GLD |

**Index vs bills, long history (monthly S&P since 1872 / 1948; "a month later" = the cautious version):**

| Rule | Period | Per year (a month later) | Fixed mix, same amount | Hold | Difference vs fixed (a month later) | Worst drop rule / hold |
|---|---|---|---|---|---|---|
| L1: stocks while inflation isn't rising, else bills | 1948-2026 | 9.9% | 8.2% | 11.9% | **+1.6 (+0.3 to +3.0), clear** | −22% / −49% |
| L2: stocks while the 10-year yield is below its 12-month average | 1872-2026 | 8.9% | 7.0% | 9.4% | **+2.2 (+1.1 to +3.3), clear** | −49% / −82% |
| L3: consumer sentiment low → 100% stocks, else 50% (contrarian) | 1980-2026 | 10.0% | 10.4% | 12.3% | −0.3 (−1.2 to +0.6) | |
| L4: VIX in its top 20% → 100% stocks, else 60% (buy fear) | 1995-2026 | 9.6% | 9.0% | 11.5% | +0.7 (−0.3 to +1.7) | |

L1 and L2 on REAL month-end closes, SPY 2016-2026: +1.4 / +2.6 (L1) and +1.6 / +1.9 (L2) % a year vs the
fixed mix (all ranges include zero over only 10 years), while holding SPY made 15% a year against their 8-11%.

**Insiders (19 universe stocks, 2016-2026):** officers and directors made only 312 open-market purchases
($1.2 billion) against 19,362 sales ($151 billion; 2 rows with impossible prices dropped). Holding the stocks
with net insider buying over the last 3 months (in the 102 months with any): +1.9% a year vs holding all equally
(−8.6 to +12.5); the 3 heaviest net sellers: +2.4% (−7.9 to +12.6), not worse. In these mega-caps insiders sell
for diversification and buy rarely; the published insider-purchase effect lives mostly in smaller companies.

Reading: none of the ETF rotations beat their simple benchmark; gold timed by real yields lost to holding gold.
Sentiment (consumer mood, buying fear) adds nothing beyond the same average amount in stocks. Insider data says
nothing useful for these 19 stocks. The one consistent result: getting out of stocks while inflation is rising
(L1) or while long-term rates are rising (L2) has paid about 1.5-2 points a year over a fixed mix with the same
average exposure for 75-150 years, with half the worst drop, and it points the same way on real 2016-2026
closes. It still makes less than simply holding (it is in stocks only about half the time): a risk reducer,
like the core's volatility targeting, not a return booster.

## 22. Option B plus an inflation / rate filter (2026-10-07)

`autobot/core_combo_study.py`: the live core rule B (EWMA volatility target, 15-45%), with its target halved
(never below 10%) while CPI inflation is rising (used from two months later) and/or while the 10-year yield is
above its 12-month average (from the next day). Same data, days (September 2019 on), band and costs as section 12.

| | Avg weight | As traded, per year | Worst drop | Same amount as B: per year / per risk | vs B, per risk (95%) |
|---|---|---|---|---|---|
| B (live) | 26% | +4.0% | −5.9% | +4.0% / 0.97 | |
| B + inflation | 19% | +3.3% | −4.0% | +4.5% / 1.05 | +0.08 (−0.11 to +0.27) |
| B + rates | 17% | +2.7% | −5.9% | +4.0% / 0.87 | −0.10 (−0.33 to +0.18) |
| B + either | 16% | +2.5% | −4.0% | +4.3% / 0.97 | 0.00 (−0.16 to +0.18) |
| plain 30% + either | 18% | +3.4% | −5.3% | +4.9% / 1.01 | +0.04 (−0.23 to +0.34) |

Reading: in these 7 years inflation was "rising" on 57% of days, rates on 67%, one or the other on 87%, so the
filters mostly just hold less. They lower the worst drop and the crisis losses (2022: B −4.9%, B + rates −2.5%)
only in proportion to the smaller amount held; at the same average amount none is clearly better than B, and the
rate filter is slightly worse. The filters overlap with volatility targeting rather than adding to it. The live
core stays on B alone.

## 23. A new strategy from the differences: risk-managed relative strength (2026-10-07)

What worked in sections 1-22 was long only, slow, comparing assets with each other and sizing by risk.
`autobot/rs_study.py` combines them, fixed before the run: each month hold the 3 assets with the best return
over months t-12..t-1 (plain), only if each is above its own 10-month average, else cash (dual), and size each
by 12% target volatility / its own 12-month volatility, at most a third each (full). Costs 0.1% per switch
(index data) or 2 bps (ETFs). Data downloaded with the user's OK: Kenneth French's 10 industry portfolios and
market / T-bill returns (monthly, 1926-2026); sector ETFs from Alpaca (2016-2026).

**French 10 industries, July 1927 to August 2026 (1,190 months):**

| | Per year | Volatility | Worst drop | Sharpe | Invested | vs market at the market's volatility (% a year, 95%) |
|---|---|---|---|---|---|---|
| Market | +10.3% | 18.4% | −84% | 0.45 | 100% | |
| Equal mix of the 10 | +11.0% | 17.9% | −82% | 0.49 | 100% | |
| Market, volatility-sized | +9.2% | 12.5% | −46% | 0.51 | 81% | +1.2 (−0.4 to +2.7) |
| Plain top 3 | **+12.9%** | 18.4% | −74% | 0.58 | 100% | **+2.4 (+0.9 to +3.9)** |
| Dual | +11.8% | 15.0% | −65% | 0.61 | 82% | +2.9 (+0.2 to +5.6) |
| **Full (dual + volatility sizing)** | +10.0% | **9.8%** | **−28%** | **0.69** | 61% | **+4.5 (+1.6 to +7.5)** |

By period, full vs market: 1927-49 +8.0% vs +5.7% (drop −28% vs −84%); 1950-98 +12.3% vs +13.2% (−18% vs −47%);
1999-2026, after the industry-momentum paper: +7.7% vs +9.1% (−13% vs −50%), at the same volatility +2.6% a year
(−2.2 to +7.4, not clear). Plain top 3 after 1999: +11.5% vs +9.1%.

**Sector ETFs, 2017-2026 (118 months):** market (SPY) +15.2% a year (drop −24%); plain top 3 +14.7% (−16%); dual
+12.1% (−11%); full +8.0% (−10%). No version beats SPY, even at the same volatility (full −3.3% a year, −10.2 to +3.7):
a decade led by a few mega-cap tech stocks, which a sector rotation dilutes.

Reading: over a century the combination works as the research says (Moskowitz & Grinblatt 1999; Barroso &
Santa-Clara 2015): about the market's return with half its volatility and a third of its worst drop, clearly
better per unit of risk. Since publication the edge is smaller and not clear, and in the last 10 years of sector
ETFs it did not beat simply holding SPY. It is the strongest candidate found so far for a slow, long-only sleeve,
but not proven for the near future.

## 24. Combining and contrasting: industry rotation variants and mixes (2026-10-07)

Follow-up to section 23 in `autobot/rs_study.py` (`combine`): selection variants (12-month vs an average rank of
3-, 6- and 12-month returns; top 3 vs top 5; all with the trend filter and volatility sizing) and monthly-rebalanced
mixes with the market. Same data and costs.

| French 10 industries, 1927-2026 | Per year | Volatility | Worst drop | Sharpe | vs market at its volatility (95%) |
|---|---|---|---|---|---|
| Market | +10.3% | 18.4% | −84% | 0.45 | |
| Market, volatility-sized (like the core's B) | +9.2% | 12.5% | −46% | 0.51 | +1.2 (−0.4 to +2.7) |
| Rotation, 12 months, top 3 (section 23) | +10.0% | 9.8% | −28% | 0.69 | +4.5 (+1.6 to +7.5) |
| Rotation, 12 months, top 5 | +9.7% | 9.0% | −26% | **0.71** | **+4.9 (+2.1 to +7.8)** |
| Rotation, 3+6+12 months, top 3 / top 5 | +9.5% / +9.4% | 10.0% / 9.1% | −30% / −26% | 0.63 / 0.67 | +3.4 / +4.2 |
| Half market + half rotation | +10.1% | 13.1% | −64% | 0.55 | +2.0 (+0.9 to +3.0) |
| Half B-like market + half rotation | +9.4% | 10.7% | −37% | 0.59 | +2.7 (+0.7 to +4.6) |

After 1999: market +9.1% (drop −50%), rotation top 3 +7.7% (−13%), half market + half rotation +8.3% (−33%).
The rotation moves with the market (correlation 0.68), but in the market's worst 10% of months it lost 3.2% on
average against the market's 9.0%: its value is that it steps aside in bad stretches.

Sector ETFs 2017-2026: nothing beats SPY (+15.2%, drop −24%); half SPY + half rotation +10.2% (−17%); the
3+6+12-month ranking was clearly worse than SPY at the same volatility (−8.2% a year).

Reading: dynamically including and excluding industries is the strongest component over a century, mostly as
protection (a third of the market's worst drop). Averaging several lookbacks did not help; holding 5 instead of 3
was marginally better. Mixing it with the market gives in-between results: half market + half rotation kept
almost the market's return over 100 years with clearly smaller drops. But in the last decade every version and mix
trailed simply holding SPY, by 5 to 7 points a year for the rotation alone and 5 points for the half-half mix.

## 25. The AI judges: Opus 5.5 and Haiku 4.5 vs the bot (2026-10-08)

`python -m autobot.llm_judge --compare` (neutral prompt v2; 3 replayed days 1, 2 and 5 October plus 2 live shadow days
6 and 7 October; result saved as `data/ml/judge_compare_2026-10-08.json`). The judges never placed orders; each of
the bot's 84 trading-job signals was scored by what following the judge would have changed.

| | Opus 5.5 (effort low) | Haiku 4.5 |
|---|---|---|
| Signals judged / skipped | 84 / 69% | 84 / 89% |
| Entries taken: average result | 4 taken, −0.10% | 3 taken, −0.54% |
| Entries skipped: average result (what was missed) | 38 skipped, +0.12% (14 would have lost) | 39 skipped, +0.15% (15 would have lost) |
| Exits skipped (held instead to the close-out): average gain | 12, +0.24% | 28, +0.28% |
| **Dollars vs the bot** | **−$129** | **+$143** |
| API cost for the 5 days | $0.52 | $0.12 |

They agreed on 73% of signals. Neither picked entries well: the few entries they took did worse than the many
they skipped. Haiku's gain came almost entirely from refusing to sell (holding to the close), which paid on
these mostly rising days; that is the market's drift, not judgement. 42 entries and 34 exits over 5 days are far
too few to separate skill from luck.

Strategy vs holding for the same jobs and days (from broker fills): the trading jobs made +$7 over 25 job-days;
buying the same symbols at the open and holding to the close-out would have made +$705.

## 26. Same money: the daily strategies against holding the same amount (2026-10-12)

`python -m autobot.same_money_study` (`data/same_money_study.json`). Sections 1 and 5 compared every
daily strategy with buy and hold **fully invested every day**, while a strategy is invested only part
of the time and its idle money earns nothing. Here each one is compared, on the same data, fills and
costs as section 5 (17 symbols, 2021-07-28 to 2026-10-09), with buy and hold kept at the strategy's
own average share invested (same money), and at the strategy's own volatility (same risk).

Buy and hold at a fixed share of the money is a straight line: 10% invested made +2.4% a year
(worst drop −3%), 30% +7.1% (−8%), 50% +12.0% (−12%), 100% +24.4% (−24%).

| Strategy | Per year | Worst drop | In the market | Hold, same money | vs same money (bps/day, 95%) | vs same risk |
|---|---|---|---|---|---|---|
| Relative strength top 4 | +31.6% | −31% | 100% | +24.4% | +3.3 (−3.2 to +9.8) | −2.8 (−9.4 to +3.8) |
| bollinger_reversion | +8.7% | −10% | 21% | +4.9% | +1.5 (−0.6 to +3.6) | −1.0 (−3.1 to +1.1) |
| rsi_reversion | +5.7% | −5% | 10% | +2.3% | +1.4 (−0.3 to +3.0) | −0.8 (−2.5 to +0.9) |
| ibs_reversion | +12.5% | −11% | 44% | +10.6% | +0.8 (−1.5 to +3.1) | −0.9 (−3.3 to +1.5) |
| donchian_breakout | +12.1% | −9% | 45% | +10.7% | +0.5 (−1.8 to +2.8) | +0.3 (−2.1 to +2.6) |
| Best of all (walk-forward) | +13.3% | −14% | 53% | +12.6% | +0.3 (−1.4 to +2.0) | −0.4 (−2.2 to +1.4) |
| momentum | +14.2% | −12% | 57% | +13.8% | +0.2 (−2.2 to +2.6) | +0.2 (−2.2 to +2.6) |
| Volatility target (on hold) | +22.4% | −21% | 91% | +22.2% | +0.0 (−0.7 to +0.8) | +0.4 (−0.4 to +1.0) |
| sma / ema cross, macd | +12.4 to +13.9% | −14 to −15% | 50-58% | +12.1 to +14.0% | −0.2 to +0.2 | about 0 |
| Momentum 12-1 | +17.1% | −17% | 74% | +18.0% | −0.3 (−2.3 to +1.7) | −0.6 |
| Above 200-day average | +14.8% | −17% | 69% | +16.7% | −0.7 (−3.3 to +2.0) | −0.1 |

(Default settings; the walk-forward versions are within the same ranges. 27 rows in the JSON.)

Reading: **none is clearly different from holding the same amount, in either direction, and none at the
same risk.** The "nothing beats holding" of section 5 was mostly a matter of how much money was at work:
per dollar invested the strategies roughly match holding, they do not lose to it. Their return and their
drops are about what holding that share of the money gives. The mean-reversion rules (rsi, bollinger)
lean ahead per dollar but take more risk per dollar (behind at the same risk), and the ranges include
zero. So the timing adds no measurable value here; what decides the result is **how much of the money
is invested**, a sizing choice (the core's volatility target, the sleeve's size, cash). Cash earned 0%
in all these tests; with a money-market rate the part-invested strategies and the scaled holds would gain
the same amount.
