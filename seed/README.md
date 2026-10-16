# Seed files for a new server

Saved results of finished studies (docs/STRATEGY_EVIDENCE.md), so the lab's panels on a new server show them
without re-running the studies (several need large downloads). The container copies them into `/state/data` on
start, **only where the file is missing**: a study re-run on the server is never overwritten.

| File | Shown on |
|---|---|
| `ml/study_vol_target.json` | Lab: long-term holdings (core volatility targeting) |
| `rs_study.json` | Lab: long-term holdings (sector rotation) |
| `event_study.json` | News: market impact of news |
| `news_guard_study.json` | News: does the news guard help? |
| `recal_fix_study.json` | Recalibrate: the recalibration check |
| `presets_study.json` | Live: the three setups' expected return and worst drop |

To refresh one after re-running its study on the PC, copy the new file from `data/` over the one here and commit.
