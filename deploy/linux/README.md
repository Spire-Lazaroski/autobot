# Running autobot on a Linux server

From the GitHub repository to a running paper bot, in plain steps. Paper trading only, as on the PC.

## 0. Before you start: only one bot at a time

The paper account must be traded by **one** bot. Before starting it on the server:

1. On the Windows PC, stop the bot (Live page, **Stop + flatten**, leave "also sell the long-term holdings" unticked:
   the trading jobs are closed, VOO and the sector funds are kept).
2. Turn off the PC's autostart (`autostart_off.bat`) so it doesn't start again at the next log-on, and close the lab
   windows (the news collector too).

## 1. What the server needs

- Git, Python 3.11 or newer with `venv`, Node.js 20 or newer (only to build the web pages once).
- Optional: Chromium, for the PDF version of the daily report (without it you get the HTML report only).

On Ubuntu / Debian (the server admin may need to run this):

```bash
sudo apt install -y git python3 python3-venv nodejs npm chromium
```

## 2. Get the code from GitHub

The repository is private. Either add the server's user as a collaborator, or (better for a server) add a
**read-only deploy key**: on the server run `ssh-keygen -t ed25519`, then paste the contents of
`~/.ssh/id_ed25519.pub` into GitHub > the repository > Settings > Deploy keys > Add (leave write access off).

```bash
git clone git@github.com:Spire-Lazaroski/autobot.git
cd autobot
```

## 3. Set it up

```bash
bash deploy/linux/setup.sh
```

The first run creates `.env` and stops. Put your Alpaca **paper** keys in it (`nano .env`), save, and run
`bash deploy/linux/setup.sh` again. It installs the Python packages, builds the lab's pages and registers three
services: the lab (`autobot-lab`), the news collector (`autobot-news`) and the bot (`autobot-bot`). Nothing is
started yet. `.env` is readable by your user only; never commit it or send it to anyone.

## 4. Optional: bring the history from the PC

The `data/` folder is not in git. Without it the bot still trades, but the news history, the recalibration
history (which limits how often jobs may change) and the lab's saved studies start empty. To keep them, copy
these from the PC's `E:\autobot` into the server's `autobot` folder (e.g. with WinSCP or `scp -r`):
`data/` (all of it, or at least `news.db`, `recalibration/`, `universe/`), and `logs/trades.csv`.

## 5. Start

```bash
systemctl --user start autobot-lab autobot-news autobot-bot
systemctl --user status autobot-bot          # should say "active (running)"
tail -f logs/bot.log                         # Ctrl+C to stop watching
```

They start again by themselves after a server reboot. A `systemctl --user stop autobot-bot` (or a reboot) stops
the bot the same way as the Stop button: the trading jobs are closed, the long-term holdings are kept.

## 6. Open the lab from your PC

The lab has no password, so it only listens on the server itself and must **never** be opened to the internet.
Reach it through an SSH tunnel from your PC:

```bash
ssh -L 8710:127.0.0.1:8710 youruser@your-server
```

Leave that window open and browse to http://127.0.0.1:8710 on your PC.

## 7. Updating after a change on GitHub

```bash
cd autobot && git pull && bash deploy/linux/setup.sh
systemctl --user restart autobot-lab
```

Restart the bot itself only while the market is closed (`systemctl --user restart autobot-bot`).
The nightly recalibration edits `config.yaml` on the server; if `git pull` complains about it, the server's
copy is the one in use: `git stash`, `git pull`, `git stash pop`.
