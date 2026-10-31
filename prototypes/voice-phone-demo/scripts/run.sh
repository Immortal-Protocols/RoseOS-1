#!/usr/bin/env bash
# Start the brain (if it is not already up) and the demo, then open the dashboard.
#   bash scripts/run.sh [--design A|B] [--no-mic] [--no-speaker] ...
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p out

PORT=${BRAIN_PORT:-8081}
if ! curl -sf "127.0.0.1:$PORT/health" >/dev/null; then
  echo "Starting brain on :$PORT (log: out/brain.log)"
  bash scripts/start_brain.sh > out/brain.log 2>&1 &
  BRAIN_PID=$!
  trap 'kill $BRAIN_PID 2>/dev/null || true' EXIT
  for _ in $(seq 1 120); do
    curl -sf "127.0.0.1:$PORT/health" >/dev/null && break
    kill -0 $BRAIN_PID 2>/dev/null || { echo "brain failed; see out/brain.log" >&2; exit 1; }
    sleep 1
  done
fi

( sleep 4 && open "http://127.0.0.1:8090" ) &
echo "Dashboard: http://127.0.0.1:8090  (wear headphones: no echo cancellation)"
.venv/bin/python -m demo.app "$@"
