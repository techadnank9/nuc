#!/usr/bin/env bash
# Start the Fortune Teller: the voice agent and the web page together. Ctrl+C stops both.
#
#   ./start_web.sh                       # robot connected, dry run; press "Turn on live mode" in the page to move the arms
#   DRAW_MODE=manual ./start_web.sh      # no robot at all: random cards (test the page anywhere)
#
# Then open http://127.0.0.1:8765 in Chrome or Safari (not an embedded browser: it needs the mic).
# The page also shows the robot's three cameras: the server starts a camera relay on the station while it runs
# (stop this script before using run.sh or the teleop dashboard, which need the cameras).
set -euo pipefail
export DRAW_MODE="${DRAW_MODE:-robot-dry}"
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
