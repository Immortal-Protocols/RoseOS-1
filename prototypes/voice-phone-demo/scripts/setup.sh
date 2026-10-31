#!/usr/bin/env bash
# One-time setup: Python venv + model downloads into ~/.cache/huggingface.
#   bash scripts/setup.sh            Rose v3 (~22 GB): bash scripts/rose.sh
#   bash scripts/setup.sh --duplex   also the old full-duplex demo (~37 GB more,
#                                    needs llama.cpp: brew install llama.cpp)
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements.txt

# Rose v3
.venv/bin/hf download mlx-community/Qwen3.6-35B-A3B-4bit
.venv/bin/hf download mlx-community/silero-vad
.venv/bin/hf download mlx-community/smart-turn-v3
.venv/bin/hf download mlx-community/nemotron-3.5-asr-streaming-0.6b
.venv/bin/hf download mlx-community/parakeet-tdt-0.6b-v3
.venv/bin/hf download gradium/phonon-31m-en --include "*.json" "*.safetensors" "*.jinja"
for id in NbpkqMVS3CJeq2j8 4SZHfMpw-p46Ywgs vjFyp2BcCyTlUmYb NIWIOv5vTi5eRMj8 bXwrkHhURL1VFJdG \
          nGzMHMPAWGaJFHOo LMDIYE0Oa377Qh6Z 40QrMVgAxFiUnP9f d9XCyhjo4JhkHF1d; do
  .venv/bin/hf download gradium/phonon-voices "voices/$id.safetensors" >/dev/null
done

if [[ "${1:-}" == "--duplex" ]]; then
  command -v llama-server >/dev/null || { echo "llama-server not found: brew install llama.cpp" >&2; exit 1; }
  .venv/bin/hf download mlx-community/NemotronLabs-VoiceChat-11B-8bit
  .venv/bin/hf download unsloth/Qwen3.6-35B-A3B-GGUF Qwen3.6-35B-A3B-UD-Q4_K_M.gguf mmproj-F16.gguf
fi
echo "Setup done. Start Rose with: bash scripts/rose.sh"
