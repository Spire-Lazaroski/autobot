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
