#!/bin/bash
# Keeps a copy of every Dream-1 run trace (last-run.csv is rewritten by each new run), named by the run start time.
while true; do
  f=~/.dreamlabs/last-run.csv
  t=$(sed -n 2p "$f" 2>/dev/null | cut -d, -f1)
  if [ -n "$t" ]; then cp -f "$f" ~/run-traces/run-$(date -d @${t%.*} +%H%M%S).csv; fi
  sleep 2
done
