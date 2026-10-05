# Core holding: S&P 500 with a 10-month-average rule (design, 2026-10-05)

Status: **phase 1 done; the rule failed its checkpoint on real daily closes (see the end).
Phases 2-4 on hold until the user decides.** Evidence: `docs/STRATEGY_EVIDENCE.md`
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
