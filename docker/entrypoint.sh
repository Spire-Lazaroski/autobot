#!/usr/bin/env bash
# Container start: point the saved state at the permanent volume, start the news collector, the bot (under its
# supervisor) and the lab. On stop (update, restart): the STOP file, so the bot closes the trading jobs'
# positions and keeps the long-term holdings, exactly like the lab's Stop button; then the rest is stopped.
set -uo pipefail
cd /app
STATE="${STATE_DIR:-/state}"

# 1. saved state on the volume: data/, logs/, reports/ and config.yaml (the first deploy copies the repo's config;
#    after that the server's copy is the one in use, also after updates; RESET_CONFIG=1 takes the repo's again)
mkdir -p "$STATE/data" "$STATE/logs" "$STATE/reports"
for d in data logs reports; do
  if [ ! -L "/app/$d" ]; then
    [ -d "/app/$d" ] && rmdir "/app/$d"          # only an empty folder from the image; never a full one
    ln -s "$STATE/$d" "/app/$d"
  fi
done
# saved study results for the lab's panels (seed/README.md): copied only where missing, never over a newer run
cp -rn /app/seed/studies/. "$STATE/data/" 2>/dev/null || true
if [ ! -f "$STATE/config.yaml" ] || [ "${RESET_CONFIG:-0}" = "1" ]; then
  cp /app/config.yaml "$STATE/config.yaml"
  echo "config.yaml copied from the repository to $STATE"
fi
ln -sf "$STATE/config.yaml" /app/config.yaml

# an old STOP file must not stop a new bot. The lock (logs/bot.pid) is kept: during an update the old version
# runs on for about 30 s with the same /state, and its lock is what keeps a second bot from being started
rm -f /app/STOP
# the supervisors' locks hold process numbers from an earlier container; numbers restart in a new one, so an old
# lock can point at an unrelated live process and make the new supervisor think one is already running (the news
# collector stayed off for days that way). They have no heartbeat, unlike the bot's own lock, which is kept.
rm -f /app/logs/run_news.pid /app/logs/run_bot.pid
rm -f /app/data/users/*/logs/run_bot.pid /app/data/users/*/STOP 2>/dev/null || true   # invited users' bots: the same

if [ -z "${ALPACA_API_KEY:-}" ] || [ -z "${ALPACA_SECRET_KEY:-}" ]; then
  echo "ALPACA_API_KEY / ALPACA_SECRET_KEY are not set: the lab starts, the bot and the news collector do not."
  AUTOSTART_BOT=0
  NO_NEWS=1
fi

# 2. processes: the lab first (the panel needs /health within about 24 s), then the news collector and the bot
python docker/serve.py &
API=$!
NEWS=""
[ "${NO_NEWS:-0}" = "1" ] || { python run_news.py & NEWS=$!; }
if [ "${AUTOSTART_BOT:-0}" = "1" ]; then
  python run_bot.py &
  echo "bot started (run_bot.py)"
else
  echo "bot not started (AUTOSTART_BOT=0): use the lab's Live page, Start"
fi

bot_up() {
  python -c "import sys; from autobot.liveconfig import bot_state as b; s = b(); sys.exit(0 if s['running'] or s['unresponsive'] else 1)" 2>/dev/null
}

stop() {
  trap - TERM INT
  python -m api.userbots stop-all 2>/dev/null &   # invited users' bots stop the same way (they come back after the update)
  USERS_STOP=$!
  if bot_up; then
    echo "stopping the bot through the STOP file (the trading jobs are closed, the long-term holdings kept)"
    touch /app/STOP
    for _ in $(seq 100); do bot_up || break; sleep 1; done
  fi
  kill "$API" ${NEWS:+"$NEWS"} 2>/dev/null
  wait
}
trap 'stop; exit 0' TERM INT

wait "$API"
code=$?
echo "the lab stopped (exit $code); stopping the rest"
stop
exit "$code"
