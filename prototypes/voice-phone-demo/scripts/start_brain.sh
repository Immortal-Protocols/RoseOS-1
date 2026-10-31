#!/usr/bin/env bash
# Serve the brain (Qwen3.6-35B-A3B, 4-bit, with vision) on an OpenAI-style API.
#   BRAIN_PORT=8081 UBATCH=64 bash scripts/start_brain.sh
# UBATCH keeps each GPU submission short so the voice model, which shares the
# GPU, can interleave its 80 ms frames; larger values prefill faster.
set -euo pipefail
cd "$(dirname "$0")/.."

REPO=unsloth/Qwen3.6-35B-A3B-GGUF
MODEL_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
MMPROJ_FILE=mmproj-F16.gguf
PORT=${BRAIN_PORT:-8081}
UBATCH=${UBATCH:-512}
# BRAIN_PRIORITY=background asks macOS to favor the voice process on the GPU.
PRIORITY=${BRAIN_PRIORITY:-background}

MODEL=$(.venv/bin/hf download "$REPO" "$MODEL_FILE" --quiet)
MMPROJ=$(.venv/bin/hf download "$REPO" "$MMPROJ_FILE" --quiet)

PREFIX=()
if [[ "$PRIORITY" == background ]]; then PREFIX=(taskpolicy -b); fi

exec "${PREFIX[@]}" llama-server -m "$MODEL" --mmproj "$MMPROJ" \
  --host 127.0.0.1 --port "$PORT" --alias brain \
  -c 32768 -ngl 99 -fa on --jinja \
  -b "$UBATCH" -ub "$UBATCH"
