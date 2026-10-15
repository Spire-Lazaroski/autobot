# Hosting autobot in Docker (e.g. NextDeploy)

One container runs everything: the lab (pages, daily report, nightly recalibration), the news collector and
the paper bot. Paper trading only.

## What the host must provide

| Setting | Value | Why |
|---|---|---|
| Port | `8080` (or set `PORT`) | the lab and `/health` (the panel's check) |
| Permanent storage | a volume mounted at `/state` | keeps `config.yaml`, `data/`, `logs/`, `reports/` across updates |
| Stop time | at least **120 seconds** before a forced kill | the bot closes the trading jobs' positions on a stop |
| Password | the panel's access password, on **every** path (incl. `/ws/live`) | the lab has no login of its own |
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | your Alpaca **paper** keys | check them: some panels fill names ending in `_KEY` with random values |
| `AUTOSTART_BOT` | `0` (default) until the PC's bot is off, then `1` | `1` = the bot starts with the container (and after every update); `0` = start it from the lab's Live page |

Not needed: `ANTHROPIC_API_KEY` (the AI judge has ended and the news uses the free keyword rules). Leaving it out
means nothing in the container can spend money on Claude.

## Before the first deploy: only one bot

Stop the bot on the Windows PC (Live page, **Stop + flatten**, long-term holdings kept), run `autostart_off.bat`,
and close the lab windows. Two bots must never trade the same paper account.

## What happens on start, update and stop

- **First start**: `config.yaml` from the repository is copied to `/state`. From then on the server's copy is the
  one in use (the Jobs editor and the nightly recalibration change it there); a later update does not overwrite
  it. To take the repository's version again, deploy once with `RESET_CONFIG=1`, then remove it.
- **Every start**: the news collector, the bot (under its supervisor, which restarts it after a crash, at most 3
  times an hour) and the lab start.
- **Stop or update**: the container writes the STOP file, exactly like the lab's Stop button: the trading jobs'
  positions are closed, the long-term holdings (VOO and the sector funds) are kept, the bot exits, then the rest.
  Prefer updating while the market is closed.

## Bringing the history from the PC (optional)

Copy the PC's `E:\autobot\data` folder (at least `news.db`, `recalibration/`, `universe/`) to `/state/data`, and
`logs\trades.csv` to `/state/logs/`, while the container is stopped.

## Running it without a panel

```bash
ALPACA_API_KEY=... ALPACA_SECRET_KEY=... docker compose up -d --build
docker compose logs -f
```

The port is published to the server itself only (`127.0.0.1:8080`); reach it through the panel's proxy or an SSH
tunnel (`ssh -L 8080:127.0.0.1:8080 user@server`, then http://127.0.0.1:8080).
