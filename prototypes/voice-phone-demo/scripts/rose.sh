#!/usr/bin/env bash
# Rose v3: one process (speech in, brain, speech out, fake phone), dashboard at
# http://127.0.0.1:8091. Click the mic button in the page to talk.
#   bash scripts/rose.sh [--voice zoey] [--local-audio] [--port 8091]
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p out
# Models are already downloaded by scripts/setup.sh; skip Hugging Face update checks.
export HF_HUB_OFFLINE=1
PORT=8091
for ((i = 1; i <= $#; i++)); do
  if [[ "${!i}" == "--port" ]]; then j=$((i + 1)); PORT="${!j}"; fi
done
( for _ in $(seq 1 240); do
    curl -sf "127.0.0.1:$PORT/api/state" >/dev/null && { open "http://127.0.0.1:$PORT"; exit; }
    sleep 1
  done ) &
exec .venv/bin/python -m rose.app "$@"
