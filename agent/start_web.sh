#!/usr/bin/env bash
# Start the Fortune Teller: the voice agent and the web page together. Ctrl+C stops both.
#
#   ./start_web.sh                 # random cards, no robot (safe for testing the page)
#   DRAW_MODE=robot-dry ./start_web.sh                 # Dream-1 runs, arms do NOT move
#   DRAW_MODE=robot ROBOT_LIVE=1 ./start_web.sh        # LIVE: arms move (hand on the e-stop)
#
# Then open http://127.0.0.1:8765 in Chrome or Safari (not an embedded browser: it needs the mic).
set -euo pipefail
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
