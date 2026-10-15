#!/usr/bin/env bash
# One-time setup of autobot on a Linux server (Ubuntu/Debian tested commands; run as the user that will
# run the bot, NOT root). Run from the cloned repo:   bash deploy/linux/setup.sh
# Safe to run again (after a git pull): it only updates the environment, the web build and the services.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
echo "autobot folder: $ROOT"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1. Install it first (see deploy/linux/README.md)."; exit 1; }; }
need python3
need node
need npm

# 1. Python environment
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# 2. The lab's web pages, built once and served by the API itself (no Node needed while running)
(cd web && npm ci --no-audit --no-fund && npm run build)

# 3. Keys: .env is never in git; create it from the example and stop if the keys are still placeholders
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created .env. Put your Alpaca PAPER keys in it (nano .env), then run this script again."
  exit 0
fi
chmod 600 .env
if grep -q "PKXXXX" .env; then
  echo ".env still has the example keys. Edit it (nano .env), then run this script again."
  exit 1
fi
mkdir -p logs reports data

# 4. Services (systemd, per user, start at boot even when nobody is logged in)
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
for u in autobot-lab autobot-bot autobot-news; do
  sed "s#@ROOT@#$ROOT#g" "deploy/linux/$u.service" > "$UNIT_DIR/$u.service"
done
systemctl --user daemon-reload
loginctl enable-linger "$USER" 2>/dev/null || echo "Note: ask the server admin to run: sudo loginctl enable-linger $USER  (keeps the services running after you log out)"
systemctl --user enable autobot-lab autobot-news autobot-bot >/dev/null
echo
echo "Done. Nothing is started yet. Make sure the bot is NOT running on any other computer, then:"
echo "  systemctl --user start autobot-lab autobot-news autobot-bot"
echo "Status:  systemctl --user status autobot-bot      Logs:  tail -f $ROOT/logs/bot.log"
