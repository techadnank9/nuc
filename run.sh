#!/usr/bin/env bash
# run.sh: run Dream-1 on the robot station with one command, from your laptop.
#
#   ./run.sh                          dry run (the arms do NOT move)
#   ./run.sh --live                   live run (the arms MOVE; hand on the e-stop)
#   ./run.sh --task "draw the front card from the deck and place it in the stand" --checkpoint dream-1-ft:<id>
#
# While it runs, open http://localhost:8093 to see what the model sees.
# Ctrl+C stops it: the arms go home, then switch off and go limp (support them).
set -euo pipefail

HOST="${ROBOT_HOST:-pah@10.103.0.133}"
VIEW_PORT="${VIEW_PORT:-8093}"
TASK="draw the front card from the deck and place it in the stand"
CHECKPOINT=""
ACTION_SPACE=""
LIVE=0

usage() {
  cat <<EOF
Usage: ./run.sh [--live] [--task "text"] [--checkpoint MODEL_ID [--action-space joint|eef]] [--host user@ip]

  --live         actually move the arms (default is a dry run)
  --task         the instruction for the model (default: "$TASK")
  --checkpoint   a fine-tuned model id, e.g. dream-1-ft:abc123-fortune
                 (default: the Dream-1 base model)
  --action-space with --checkpoint: joint (default, for our YAM fine-tunes) or eef
  --host         the station's login (default: \$ROBOT_HOST or $HOST)

Env vars: ROBOT_HOST, VIEW_PORT (default 8093)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --live) LIVE=1 ;;
    --task) TASK="${2:?--task needs a value}"; shift ;;
    --checkpoint) CHECKPOINT="${2:?--checkpoint needs a value}"; shift ;;
    --action-space) ACTION_SPACE="${2:?--action-space needs a value}"; shift ;;
    --host) HOST="${2:?--host needs a value}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 1 ;;
  esac
  shift
done

args=(robot run --task "$TASK" --view --view-port "$VIEW_PORT")
if [[ -n "$CHECKPOINT" ]]; then
  args+=(--checkpoint "$CHECKPOINT")
  # Our YAM fine-tunes drive the joints they learned (docs/running_your_model.md on the station).
  args+=(--action-space "${ACTION_SPACE:-joint}")
fi

if (( LIVE )); then
  echo "⚠️  LIVE RUN: the arms will move to home, then try: \"$TASK\""
  echo "   Hand on the e-stop. Nobody within reach of the arms."
  read -r -p '   Type "go" to continue: ' answer
  [[ "$answer" == "go" ]] || { echo "Cancelled."; exit 1; }
else
  args+=(--dry-run)
  echo "Dry run: the model runs for real, nothing is sent to the arms. (--live to move them)"
fi

# Built as one remote command: refuse if something already holds the arms, then start dreamcli.
remote_cmd="if pgrep -af 'bin/(dreamcli|dhk-collect)'; then
  echo; echo 'The arms are in use by the process(es) above. Stop that first (Ctrl+C in its terminal).'; exit 3
fi
source ~/dream-env/bin/activate && cd ~/physical-ai-hacks && exec dreamcli $(printf '%q ' "${args[@]}")"

echo "Connecting to $HOST ... then open http://localhost:$VIEW_PORT"
# -t: Ctrl+C reaches dreamcli so it stops cleanly. -L: forwards the model-view page to your laptop.
exec ssh -t -L "$VIEW_PORT:localhost:$VIEW_PORT" "$HOST" "$remote_cmd"
