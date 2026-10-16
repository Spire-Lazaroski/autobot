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
| `LAB_USER`, `LAB_PASSWORD_HASH` | your user name, and the hash from `python -m api.auth hash` (run on the PC; the password itself is never stored) | the app's own sign-in page; set them **before** deploying, or nobody can sign in |
| `ALPACA_OAUTH_CLIENT_ID`, `ALPACA_OAUTH_CLIENT_SECRET`, `APP_URL` | from registering the app with Alpaca (Connect API); `APP_URL` = `https://spire.ristesky.com` | invited users' "Connect with Alpaca" (paper only); without them users can still paste paper keys |
| `CREDENTIALS_KEY` | optional: a Fernet key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) | encrypts users' Alpaca permissions; without it one is made in `/state/logs` |
| `AUTOSTART_BOT` | keep `0` (default) | start the bot from the lab's Live page after each update. During an update the old version runs on for about 30 s, so `1` could briefly start a second bot |

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

## Study results and news on a new server

The saved study results the lab's panels show (`seed/studies`, see `seed/README.md`) are copied into
`/state/data` at every start where they are missing. The news archive is rebuilt by the collector itself (it backfills
from Alpaca); if it stops, the News page shows its last log lines and a **Start collector** button, and after
repeated failures its supervisor waits 15 minutes and tries again on its own.

## Bringing the history from the PC (optional)

Copy the PC's `E:\autobot\data` folder (at least `news.db`, `recalibration/`, `universe/`) to `/state/data`, and
`logs\trades.csv` to `/state/logs/`, while the container is stopped.

## Updates on NextDeploy

The new version must answer `/health` within about 24 seconds, or it is rejected and the old one stays. The old
version is stopped about 30 seconds after the switch (SIGTERM): its bot closes the trading jobs' positions and keeps
the long-term holdings. Wait a minute after an update, check the Live page says the bot is stopped, then press
**Start**. Better still, update while the market is closed.

## Running it without a panel

```bash
printf 'ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
' > .env     # paper keys; never committed
RELEASE_SHA=$(git rev-parse HEAD) docker compose up -d --build
docker compose logs -f
```

The port is only exposed to other containers (a panel's proxy). To open the lab on the server itself, add
`ports: ["127.0.0.1:8080:8080"]` locally, then use an SSH tunnel (`ssh -L 8080:127.0.0.1:8080 user@server`).
Never publish it to the internet without a password in front: the lab has no login of its own.
