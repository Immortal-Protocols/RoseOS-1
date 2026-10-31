"""Voice + brain on one GPU, in one process, with and without the GPU gate.

The voice runs in real time (frames paced at 80 ms, speech mixed in so it
talks). The brain repeatedly does phone-agent-sized requests: a phone
screenshot plus a prompt, then a short generation.

    .venv/bin/python scripts/bench_shared_gpu.py --mode gated
    modes: voice_only | brain_only | gated | ungated
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FRAME, SR = 1280, 16_000
BRAIN_MODEL = "mlx-community/Qwen3.6-35B-A3B-4bit"
PROMPT = (
    "You operate this phone. The user asked: text Sam that I'm running ten minutes late. "
    "Describe what is on the screen in one sentence, then say which element to tap next."
)


def say(text: str) -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        aiff, wav = Path(tmp) / "a.aiff", Path(tmp) / "a.wav"
        subprocess.run(["say", "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], check=True)
        audio, _ = sf.read(wav, dtype="float32")
    return audio


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="gated", choices=["voice_only", "brain_only", "gated", "ungated"])
    parser.add_argument("--overlap", default="", help="unit kinds that may overlap voice, e.g. decode,prefill4")
    parser.add_argument("--seconds", type=float, default=40.0)
    parser.add_argument("--prefill-step", type=int, default=64)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--skip-silent-tts", action="store_true")
    parser.add_argument("--text-only", action="store_true",
                        help="ask_brain-style request: phone data as text, no screenshot")
    args = parser.parse_args()

    from mlx_vlm import apply_chat_template, load, stream_generate

    from demo import speedups
    from demo.gpu_gate import GpuGate, gate_brain_model
    from demo.phone import Phone

    run_voice = args.mode != "brain_only"
    run_brain = args.mode != "voice_only"
    gate = GpuGate(enabled=args.mode == "gated", overlap=tuple(k for k in args.overlap.split(",") if k))

    if run_brain:
        bmodel, bproc = load(BRAIN_MODEL)
        gate_brain_model(bmodel, gate)
        shot = Path(tempfile.mkdtemp()) / "screen.png"
        phone = Phone()
        phone.open_app("Messages")
        shot.write_bytes(phone.screenshot_png())
        if args.text_only:
            question = ("Phone data:\n" + phone.summary() +
                        "\n\nQuestion: Do I have everything I need to play pickup soccer with "
                        "Jordan? Answer in two short sentences.")
            prompt = apply_chat_template(bproc, bmodel.config, question, num_images=0)
        else:
            prompt = apply_chat_template(bproc, bmodel.config, PROMPT, num_images=1)
        images = None if args.text_only else [str(shot)]

    stop = threading.Event()
    voice_stats = {"frames": 0, "late": 0, "proc_ms": [], "lateness_ms": []}
    brain_stats = {"requests": [], "error": None}

    def voice_loop():
        try:
            _voice_loop()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            voice_stats["error"] = f"{type(exc).__name__}: {exc}"

    def _voice_loop():
        # MLX streams are per thread: load and build on the thread that uses them.
        speedups.apply()
        vmodel, vproc = load("mlx-community/NemotronLabs-VoiceChat-11B-8bit")
        speedups.quantize_speech_path(vmodel, perception=True)
        if args.skip_silent_tts:
            speedups.skip_silent_tts(vmodel)
        stream = vmodel.create_session(vproc).create_streaming_session(
            system_prompt="You are a friendly assistant. Answer briefly.", seed=0)
        lines = ["Tell me a fun fact about octopuses.", "What's a good name for a cat?",
                 "Tell me something interesting about the moon."]
        clips = [np.concatenate([say(line), np.zeros(SR * 6, np.float32)]) for line in lines]
        audio = np.concatenate(clips * 4)
        voice_ready.set()  # brain starts only now, so it never runs voice-free
        t0 = time.perf_counter() + 0.2
        k = 0
        while not stop.is_set() and (k + 1) * FRAME <= len(audio):
            due = t0 + k * FRAME / SR
            gate.set_next_due(due)
            wait = due - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            start = time.perf_counter()
            with gate.voice():
                stream.push_audio(audio[k * FRAME : (k + 1) * FRAME], sample_rate=SR)
            end = time.perf_counter()
            voice_stats["frames"] += 1
            voice_stats["proc_ms"].append((end - start) * 1000)
            lateness = (end - (due + FRAME / SR)) * 1000  # finished after the next frame was due
            voice_stats["lateness_ms"].append(max(0.0, lateness))
            voice_stats["late"] += lateness > 0
            k += 1
            # If we fell behind, do not try to catch up past real time.
            behind = int((time.perf_counter() - t0) * SR / FRAME) - k
            if behind > 6:
                k += behind
                voice_stats["skipped"] = voice_stats.get("skipped", 0) + behind
        gate.set_next_due(None)

    def brain_loop():
        try:
            while not stop.is_set():
                start = time.perf_counter()
                last, first = None, None
                for last in stream_generate(bmodel, bproc, prompt, image=images,
                                            max_tokens=args.max_tokens,
                                            prefill_step_size=args.prefill_step, temperature=0.0):
                    if first is None:
                        first = time.perf_counter()
                    if stop.is_set():
                        break
                if last is not None and not stop.is_set():
                    end = time.perf_counter()
                    brain_stats["requests"].append({
                        "wall_s": round(end - start, 2),
                        "ttft_s": round(first - start, 2),
                        "prompt_tokens": last.prompt_tokens,
                        "decode_tps": round((last.generation_tokens - 1) / max(1e-6, end - first), 1),
                        "tokens": last.generation_tokens,
                    })
        except Exception as exc:
            brain_stats["error"] = f"{type(exc).__name__}: {exc}"

    voice_ready = threading.Event()
    if run_voice:
        vt = threading.Thread(target=voice_loop, daemon=True)
        vt.start()
        voice_ready.wait(timeout=300)
    if run_brain:
        bt = threading.Thread(target=brain_loop, daemon=True)
        bt.start()
    threads = [t for t in (locals().get("vt"), locals().get("bt")) if t is not None]
    time.sleep(args.seconds)
    stop.set()
    for t in threads:
        t.join(timeout=30)

    out = {"mode": args.mode, "seconds": args.seconds}
    if run_voice:
        p = sorted(voice_stats["proc_ms"][10:]) or [0]
        lat = voice_stats["lateness_ms"][10:]
        out["voice"] = {
            "frames": voice_stats["frames"],
            "mean_frame_ms": round(sum(p) / len(p), 1),
            "p95_frame_ms": round(p[int(len(p) * 0.95) - 1], 1),
            "late_frames_pct": round(100 * sum(1 for x in lat if x > 0) / max(1, len(lat)), 1),
            "max_late_ms": round(max(lat, default=0), 1),
            "skipped_frames": voice_stats.get("skipped", 0),
        }
    if run_brain:
        reqs = brain_stats["requests"][1:] or brain_stats["requests"]  # drop warm-up
        out["brain"] = {
            "requests": len(brain_stats["requests"]),
            "mean_wall_s": round(sum(r["wall_s"] for r in reqs) / max(1, len(reqs)), 2),
            "mean_ttft_s": round(sum(r["ttft_s"] for r in reqs) / max(1, len(reqs)), 2),
            "mean_decode_tps": round(sum(r["decode_tps"] for r in reqs) / max(1, len(reqs)), 1),
            "prompt_tokens": reqs[0]["prompt_tokens"] if reqs else None,
            "error": brain_stats["error"],
        }
        out["gate"] = {"brain_units": gate.brain_units, "forced_units": gate.forced_units,
                       "estimates_ms": {k: round(v * 1000, 1) for k, v in gate.estimates.items()}}
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
