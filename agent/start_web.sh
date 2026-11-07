#!/usr/bin/env bash
# Start the Fortune Teller: the voice agent and the web page together. Ctrl+C stops both.
#
#   ./start_web.sh                       # robot connected: the arms move on every draw and command (hand on the e-stop)
#   DRAW_MODE=manual ROBOT_CAMERAS=0 ./start_web.sh   # no robot at all: random cards (test the conversation anywhere)
#
# Then open http://127.0.0.1:8765 in Chrome or Safari (not an embedded browser: it needs the mic).
# The page also shows the robot's three cameras: the server starts a camera relay on the station while it runs
# (stop this script before using run.sh or the teleop dashboard, which need the cameras).
set -euo pipefail
export DRAW_MODE="${DRAW_MODE:-robot}"
cd "$(dirname "$0")"
trap 'kill 0' EXIT
uv run agent.py dev &
sleep 4
uv run python web_server.py &
sleep 2
echo
echo "  Open http://127.0.0.1:8765   (DRAW_MODE=${DRAW_MODE:-manual})"
echo
wait
