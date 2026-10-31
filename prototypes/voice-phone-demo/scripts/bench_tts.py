"""Time-to-first-audio and real-time factor for candidate TTS models.

    .venv/bin/python scripts/bench_tts.py kokoro pocket soprano kitten qwen3
Writes one wav per model/sentence under out/tts/ for listening.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import mlx.core as mx
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "tts"

MODELS = {
    "kokoro": ("mlx-community/Kokoro-82M-bf16", {"voice": "af_heart"}),
    "pocket": ("mlx-community/pocket-tts", {"voice": "cosette", "stream": True, "streaming_interval": 0.24}),
    "soprano": ("mlx-community/Soprano-1.1-80M-bf16", {}),
    "kitten": ("mlx-community/kitten-tts-mini-0.8", {}),
    "qwen3": ("mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit",
              {"voice": "Vivian", "stream": True, "streaming_interval": 0.32}),
}

SENTENCES = [
    "Sure, I'm on it.",
    "Done! I texted Sam that you're running ten minutes late.",
    "You've got a design review with Alex at four, and dinner with Sam at six thirty at Nopa.",
]


def run(name: str) -> dict:
    from mlx_audio.tts.utils import load_model

    repo, kwargs = MODELS[name]
    t = time.perf_counter()
    model = load_model(repo)
    load_s = time.perf_counter() - t
    sr = int(getattr(model, "sample_rate", 24000))
    for _ in model.generate("Warming up the voice.", **kwargs):
        pass
    rows = []
    for i, text in enumerate(SENTENCES):
        start = time.perf_counter()
        first, chunks = None, []
        for res in model.generate(text, **kwargs):
            audio = np.asarray(res.audio, dtype=np.float32).reshape(-1)
            if first is None:
                first = time.perf_counter() - start
            chunks.append(audio)
        total = time.perf_counter() - start
        audio = np.concatenate(chunks) if chunks else np.zeros(1, np.float32)
        dur = len(audio) / sr
        OUT.mkdir(parents=True, exist_ok=True)
        sf.write(OUT / f"{name}_{i}.wav", audio, sr)
        rows.append({"ttfa_ms": round(first * 1000), "total_ms": round(total * 1000),
                     "audio_s": round(dur, 2), "rtf": round(total / max(dur, 1e-6), 3)})
    del model
    mx.clear_cache()
    return {"model": name, "repo": repo, "sr": sr, "load_s": round(load_s, 1), "runs": rows}


if __name__ == "__main__":
    for name in sys.argv[1:] or list(MODELS):
        try:
            print(json.dumps(run(name)), flush=True)
        except Exception as exc:  # report and continue with the next model
            print(json.dumps({"model": name, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}), flush=True)
