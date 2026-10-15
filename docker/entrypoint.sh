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
if [ ! -f "$STATE/config.yaml" ] || [ "${RESET_CONFIG:-0}" = "1" ]; then
  cp /app/config.yaml "$STATE/config.yaml"
  echo "config.yaml copied from the repository to $STATE"
fi
ln -sf "$STATE/config.yaml" /app/config.yaml

# a new container: no bot of ours is running yet; an old lock or STOP file must not block or stop the new one
rm -f /app/logs/bot.pid /app/STOP

if [ -z "${ALPACA_API_KEY:-}" ] || [ -z "${ALPACA_SECRET_KEY:-}" ]; then
  echo "ALPACA_API_KEY / ALPACA_SECRET_KEY are not set: the lab starts, the bot and the news collector do not."
  AUTOSTART_BOT=0
  NO_NEWS=1
fi

# 2. processes
NEWS=""
[ "${NO_NEWS:-0}" = "1" ] || { python run_news.py & NEWS=$!; }
if [ "${AUTOSTART_BOT:-1}" = "1" ]; then
  python run_bot.py &
  echo "bot started (run_bot.py)"
else
  echo "bot not started (AUTOSTART_BOT=0): use the lab's Live page, Start"
fi
python docker/serve.py &
API=$!

bot_up() {
  python -c "import sys; from autobot.liveconfig import bot_state as b; s = b(); sys.exit(0 if s['running'] or s['unresponsive'] else 1)" 2>/dev/null
}

stop() {
  trap - TERM INT
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
