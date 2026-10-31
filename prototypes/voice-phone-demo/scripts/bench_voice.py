"""Per-stage timing of the VoiceChat streaming loop (short prompt, speech input).

    .venv/bin/python scripts/bench_voice.py [--model ...] [--no-speedups]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="mlx-community/NemotronLabs-VoiceChat-11B-8bit")
    parser.add_argument("--no-speedups", action="store_true")
    parser.add_argument("--seconds", type=float, default=12.0)
    parser.add_argument("--quantize-perception", action="store_true")
    parser.add_argument("--with-brain", action="store_true",
                        help="run phone-agent jobs on the brain during the benchmark")
    args = parser.parse_args()

    from mlx_vlm import load

    if not args.no_speedups:
        from demo import speedups

        speedups.apply()

    model, processor = load(args.model)
    if not args.no_speedups:
        speedups.quantize_speech_path(model, perception=args.quantize_perception)
    voicechat = model.create_session(processor)
    stream = voicechat.create_streaming_session(
        system_prompt="You are a helpful assistant. Answer briefly.", seed=0, profile=True
    )
    with tempfile.TemporaryDirectory() as tmp:
        aiff, wav = Path(tmp) / "q.aiff", Path(tmp) / "q.wav"
        subprocess.run(["say", "-o", str(aiff), "Tell me a short fun fact about octopuses."], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], check=True)
        speech, _ = sf.read(wav, dtype="float32")
    sr = 16_000
    audio = np.concatenate([np.zeros(sr // 2, np.float32), speech])
    audio = np.pad(audio, (0, max(0, int(args.seconds * sr) - len(audio))))
    if args.with_brain:
        import threading

        from demo import brain
        from demo.phone import Phone

        def load_brain():
            while True:
                brain.run_phone("Turn on do not disturb", Phone())

        threading.Thread(target=load_brain, daemon=True).start()
    text = ""
    for i in range(0, len(audio), 1280):
        for ev in stream.push_audio(audio[i : i + 1280], sample_rate=sr):
            if ev.kind == "assistant_text_delta":
                text = ev.text
    s = stream.profile.summary(drop_first=10)
    print(json.dumps({
        "model": args.model,
        "speedups": not args.no_speedups,
        "reply": text,
        "realtime_factor": round(s["realtime_factor"], 3),
        "p95_total_ms": round(s["stages"]["total"]["p95_ms"], 1),
        "mean_ms": {k: round(v["mean_ms"], 1) for k, v in s["stages"].items()},
    }, indent=2))


if __name__ == "__main__":
    main()
