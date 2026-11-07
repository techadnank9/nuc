#!/usr/bin/env bash
# Asks for each key without showing it, and writes agent/.env (git-ignored).
set -euo pipefail
cd "$(dirname "$0")"
read -r -p "LIVEKIT_URL (wss://...): " URL
read -r -p "LIVEKIT_API_KEY: " KEY
read -r -s -p "LIVEKIT_API_SECRET (hidden): " SECRET; echo
umask 077
printf 'LIVEKIT_URL=%s\nLIVEKIT_API_KEY=%s\nLIVEKIT_API_SECRET=%s\nDRAW_MODE=manual\n' "$URL" "$KEY" "$SECRET" > .env
echo "saved .env"
