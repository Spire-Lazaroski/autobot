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
