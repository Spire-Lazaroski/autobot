# Core holding: S&P 500 with a 10-month-average rule (design, 2026-10-05)

Status: **the user chose a PLAIN 30% VOO core (no timing rule) after phase 1's result (see the
end). Phase 2 (bot integration) done; config `live.core` added with `enabled: false`.** Evidence: `docs/STRATEGY_EVIDENCE.md`
sections 5-6 (daily study 2021-2026; macro study 1871-2026: the 10-month average kept almost all
of buy-and-hold's return and cut the worst drop from −82% to −49%).

## Decisions (the user's, 2026-10-05)

| Question | Choice |
|---|---|
| Core share of the account | **30%** (trading jobs share the rest, inside the 100% total cap) |
| Where the money sits when "out" | **Short-term Treasury ETF** (SGOV) |
| Core ticker | **VOO**, separate from the intraday `auto_SPY` job |
| Daily loss limit and profit lock | **Trading jobs only**; the core follows its monthly rule |

## Rule

At each month's last regular-session close: **in** if VOO's close is above the average of its
last 10 month-end closes, else **out** (held in SGOV). Decided after the close, traded at the next
session's first tick (about 9:35 New York), market orders, whole shares. Checked once a month;
about 1-2 switches a year on average. Rebalanced to 30% of equity only at the monthly check.

## Config (proposal)

```yaml
core:
  enabled: false            # switched on in phase 4, with the user's OK
  symbol: VOO
  out_symbol: SGOV          # null = plain cash
  allocation_pct: 30
  rule: {kind: month_average, months: 10}
```

A separate block, not a job: recalibration (`take_over_hand_made: true`), the universe screener
and the Jobs editor never see it; `validate_live` rejects a core symbol used by any job.

## How the bot treats it

- Not sold before the close; held overnight and for months.
- Daily loss limit and profit lock: measured on and applied to the trading jobs only.
- Sizing: core dollars first (30% of equity, its own cap, not `max_position_usd`); trading-job
  budgets are scaled into the remaining 70% by `max_gross_pct`.
- Stop + flatten: the Stop dialog gets a tick box "also sell the core" (off by default).
- State in `data/core/state.json` (last decided month, in/out, date); if the bot was off on the
  decision day it acts at the next tick; when first switched on it acts on the latest month end.
- One function, `autobot/core.py`, decides for the live bot and the backtest.
- Visible: a Core panel on the Live page (in/out, the month-end level that would switch it, next
  check date), decision log lines in plain English, the daily report and the SOP.

## Phases (each committed and shown before the next)

1. `autobot/core.py` rule + backtest of the exact live rule on our data, tests (no look-ahead,
   matches the macro study's month-end version). **Checkpoint: results.**
2. Bot integration: config + validation, monthly scheduler with catch-up, the exemptions above,
   sizing carve-out, decision records, tests (including that the loss limit never sells the core).
3. Live page Core panel, Stop dialog tick box, report and SOP. **Checkpoint: screenshots.**
4. Switch on in paper. **Bot restart only with the user's OK.**

## Phase 1 result (2026-10-05): the exact live rule on real closes

`autobot/core.py` (`decide` for the bot, `signal` / `replay` for the backtest; tests prove they
agree every month and use the macro study's formula).

| Data | Rule per year | Hold per year | Rule worst drop | Hold worst drop |
|---|---|---|---|---|
| VOO / SGOV, IEX daily, May 2021 - Oct 2026 (`--backtest`) | +7.1% (cash when out: +6.5%) | **+13.6%** | −24.7% | −24.4% |
| S&P 500 index, FRED daily, Oct 2016 - Oct 2026 (`--sp500`, no dividends) | +6.6% | **+13.3%** | −28.3% | −33.9% |

The rule was caught by every quick dip: it sold after the drop and bought back after the
recovery (2018, 2019, 2022 twice, 2023, 2025, 2026); 21 switches in 10 years.

Why the 150-year study looked better: Shiller's prices are monthly averages, which smooth
out quick dips, so the rule switched far less on them. In Shiller's data the same 2021-2026
window shows the rule about even with holding; on real month-end closes it trails by 6.5 points
a year. The "acting a month later" check did not remove this effect. Over 150 years the rule
also trails holding in 57% of 5-year windows; its payoff is the rare slow crash (1929, 1973,
2000, 2008), none of which happened in the last 10 years.

## Decision after phase 1 (2026-10-05): plain hold, no rule

Config (inside `live:`, so it is hot-reloaded and validated with the rest):

```yaml
core: {enabled: false, symbol: VOO, allocation_pct: 30, rebalance_band_pct: 5}
```

Bought up to 30% of equity at the first open tick of a day; after that traded only when its
weight is more than 5 points from 30% (checked once per trading day). SGOV and the monthly rule
are not used. Everything else above (exemptions, sizing, Stop tick box, never a job) applies.
The daily loss limit and profit lock measure the day's P&L without the core position's move
today (Alpaca's unrealized_intraday_pl), still as % of the whole account's starting equity.

## Volatility targeting (option B, built 2026-10-06)

Evidence: `docs/STRATEGY_EVIDENCE.md` section 12 (core 15-45% by EWMA volatility roughly halved the
losses in the 2020, 2022 and 2025 drops, about half a point a year less in normal years). Settings:

    core: {enabled: true, symbol: VOO, allocation_pct: 30, rebalance_band_pct: 5,
           vol_target: {enabled: true, min_pct: 15, max_pct: 45, lambda: 0.94, normal_years: 5}}

Each trading morning (first tick after the open) the bot reads VOO's daily closes of completed
sessions, computes the exponentially weighted volatility and its 5-year median, and sets the
day's target = 30% x normal / today's volatility, kept between 15% and 45%. The 5-point band rule
is unchanged. Trading jobs are sized into what the day's target leaves under risk.max_gross_pct.
If the daily data can't be read, the plain 30% applies (logged). The Live page shows the target
and the reason; each rebalance carries the reason in the decision log and the daily report.

## Rotation sleeve (option 3, built 2026-10-09)

Evidence: `docs/STRATEGY_EVIDENCE.md` sections 23-24. Next to the core, 40% of the account rotates monthly among the
11 sector funds (`autobot/rotation.py`, config `live.rotation`):

    rotation: {enabled: true, allocation_pct: 40, top_n: 5, lookback_months: 12, trend_months: 10,
               vol_target_pct: 12, rebalance_band_pct: 1, symbols: [XLK, XLV, XLE, XLF, XLI, XLY, XLP, XLU, XLB, XLRE, XLC]}

At the first open tick of each month (and at once after a start or switch-on) the bot ranks the funds by the return
from 12 month-ends ago to 1 month-end ago, keeps the top 5 that are above their 10-month average, and sizes each at
8% x min(1, 12% / its yearly volatility); the rest of the sleeve stays in cash. It trades a fund only beyond a
1-point band. Like the core it is kept overnight and by Stop (unless "also sell the long-term holdings" is ticked),
and the daily loss limit and profit lock ignore it; the trading jobs are sized into what the core's day target and
the sleeve leave under risk.max_gross_pct (option 3: core about 30%, sleeve 40%, jobs about 30%, kept as a live check).
