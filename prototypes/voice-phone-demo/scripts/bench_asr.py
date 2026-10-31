"""Accuracy and latency of candidate speech recognizers on synthesized speech.

    .venv/bin/python scripts/bench_asr.py
parakeet: whole-utterance transcription at end of turn (what it costs then).
nemotron: cache-aware streaming fed 32 ms chunks; time from end of audio to final text.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import mlx.core as mx
import numpy as np
import soundfile as sf

SR = 16_000
UTTERANCES = [
    ("Samantha", "Can you text Sam that I'm running ten minutes late?"),
    ("Daniel", "Set an alarm for six thirty tomorrow morning."),
    ("Karen", "What's on my calendar this afternoon?"),
    ("Samantha", "If a recipe for four people needs one and a half cups of flour, how much do I need for seven?"),
    ("Daniel", "Actually, make it fifteen minutes instead."),
    ("Karen", "Turn on do not disturb until I get home."),
    ("Samantha", "Hey Rose, how's it going?"),
    ("Daniel", "Wait, hold on a second."),
]


def say(voice: str, text: str) -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        aiff, wav = Path(tmp) / "a.aiff", Path(tmp) / "a.wav"
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], check=True)
        audio, _ = sf.read(wav, dtype="float32")
    return audio


def words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]", " ", s.lower()).split()


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(1, len(r))


def bench_parakeet(clips):
    from mlx_audio.stt.utils import load_model

    model = load_model("mlx-community/parakeet-tdt-0.6b-v3")
    model.generate(mx.array(clips[0][2]))
    rows = []
    for voice, ref, audio in clips:
        t = time.perf_counter()
        text = model.generate(mx.array(audio)).text
        rows.append({"ms": round((time.perf_counter() - t) * 1000), "wer": round(wer(ref, text), 2), "text": text})
    return rows


def bench_nemotron(clips, delay_ms):
    from mlx_audio.stt.utils import load_model

    model = load_model("mlx-community/nemotron-3.5-asr-streaming-0.6b")
    rows = []
    for voice, ref, audio in [clips[0]] + clips:  # first pass warms up
        sess = model.create_streaming_session(transcription_delay_ms=delay_ms)
        text, step_ms = [], []
        chunk = 512
        padded = np.concatenate([audio, np.zeros(SR // 4, np.float32)])
        for k in range(0, len(padded), chunk):
            sess.feed(padded[k:k + chunk])
            t = time.perf_counter()
            for _ in range(8):
                deltas = sess.step(max_decode_tokens=8)
                text += deltas
                if not deltas:
                    break
            step_ms.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        sess.close()
        while not sess.done:
            text += sess.step(max_decode_tokens=16)
        final_ms = (time.perf_counter() - t) * 1000
        out = "".join(text).strip()
        rows.append({"final_ms": round(final_ms), "mean_step_ms": round(float(np.mean(step_ms)), 1),
                     "max_step_ms": round(float(np.max(step_ms)), 1), "wer": round(wer(ref, out), 2), "text": out})
    return rows[1:]


if __name__ == "__main__":
    clips = [(v, t, say(v, t)) for v, t in UTTERANCES]
    which = sys.argv[1:] or ["parakeet", "nemotron160", "nemotron560"]
    for name in which:
        try:
            if name == "parakeet":
                rows = bench_parakeet(clips)
            else:
                rows = bench_nemotron(clips, int(name.removeprefix("nemotron")))
            mean_wer = round(sum(r["wer"] for r in rows) / len(rows), 3)
            print(json.dumps({"model": name, "mean_wer": mean_wer, "rows": rows}), flush=True)
        except Exception as exc:
            import traceback; traceback.print_exc()
            print(json.dumps({"model": name, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}), flush=True)
        mx.clear_cache()
