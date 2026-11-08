#!/usr/bin/env bash
# Start the Fortune Teller: the voice agent and the web page together. Ctrl+C stops both.
#
#   ./start_web.sh                       # robot connected: the arms move on every draw and command (hand on the e-stop)
#   DRAW_MODE=manual ROBOT_CAMERAS=0 ./start_web.sh   # no robot at all: random cards (test the conversation anywhere)
#   DRAW_CHECKPOINT=dream-1-ft:<id> ./start_web.sh    # run our fine-tune (the id finetune_steps.py prints) instead of base Dream-1
#
# Then open http://127.0.0.1:8765 in Chrome or Safari (not an embedded browser: it needs the mic).
# The page also shows the robot's three cameras: the server starts a camera relay on the station while it runs
# (stop this script before using run.sh or the teleop dashboard, which need the cameras).
set -euo pipefail
export DRAW_MODE="${DRAW_MODE:-robot}"
cd "$(dirname "$0")"
PORT="${WEB_PORT:-8765}"

# Only one Fortune Teller per laptop: stop an older copy (its page server and agent) before starting.
here=$(pwd -P)
old=""
for pid in $(pgrep -f "python3? .*(web_server\.py|agent\.py dev)" || true); do
  # only processes started from this folder, never another project's agent
  [ "$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')" = "$here" ] && old="$old $pid"
done
if [ -n "$old" ] || lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Stopping the Fortune Teller that was already running…"
  kill $old 2>/dev/null || true
  lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
  sleep 3
fi

trap 'kill 0' EXIT
uv run agent.py dev &
AGENT=$!
sleep 4
uv run python web_server.py &
WEB=$!
sleep 3
if ! kill -0 "$WEB" 2>/dev/null; then
  echo "The page server did not start (see the error above). Stopping." >&2
  exit 1
fi
echo
echo "  Open http://127.0.0.1:$PORT   (DRAW_MODE=$DRAW_MODE)"
echo
# If either part stops, stop both, so a half-running Fortune Teller is never left behind.
while kill -0 "$AGENT" 2>/dev/null && kill -0 "$WEB" 2>/dev/null; do sleep 2; done
echo "The agent or the page server stopped: stopping the Fortune Teller." >&2
