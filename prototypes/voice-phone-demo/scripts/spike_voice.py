"""Offline spike: can Nemotron VoiceChat (MLX) run in real time and use tools?

Synthesizes a spoken request with macOS `say`, streams it through the model
frame by frame (as fast as possible, timing every frame), answers any tool
call with a canned result, then writes the conversation as a stereo WAV
(left = user, right = assistant).

    .venv/bin/python scripts/spike_voice.py "Text Sam that I'm running ten minutes late"
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import mlx.core as mx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.tools import VOICE_TOOLS, voice_system_message  # noqa: E402
from demo.voice_engine import (  # noqa: E402
    create_tool_session,
    parse_tool_call,
    tools_system_prompt,
)

CANNED = {
    "do_on_phone": {"status": "done", "summary": "Sent a text to Sam Lee: I'm running ten minutes late."},
    "ask_brain": {"answer": "Paris has about two point one million people in the city itself."},
}


def say_to_wav(text: str, out: Path, sample_rate: int = 16_000) -> np.ndarray:
    aiff = out.with_suffix(".aiff")
    subprocess.run(["say", "-o", str(aiff), text], check=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", f"LEI16@{sample_rate}", "-c", "1", str(aiff), str(out)],
        check=True,
    )
    import soundfile as sf

    audio, sr = sf.read(out, dtype="float32")
    assert sr == sample_rate
    return audio


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("utterance", nargs="?", default="Can you text Sam that I'm running ten minutes late?")
    parser.add_argument("--model", default="mlx-community/NemotronLabs-VoiceChat-11B-8bit")
    parser.add_argument("--tail-seconds", type=float, default=10.0)
    parser.add_argument("--no-tools", action="store_true")
    parser.add_argument("--no-speedups", action="store_true")
    parser.add_argument("--skip-silent-tts", action="store_true")
    parser.add_argument("--out", default=str(ROOT / "out" / "spike"))
    args = parser.parse_args()

    from mlx_vlm import load

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    from demo import speedups

    if not args.no_speedups:
        speedups.apply()
    t0 = time.perf_counter()
    model, processor = load(args.model)
    if not args.no_speedups:
        speedups.quantize_speech_path(model, perception=True)
    if args.skip_silent_tts:
        speedups.skip_silent_tts(model)
    voicechat = model.create_session(processor)
    print(f"loaded in {time.perf_counter() - t0:.1f}s")

    prompt = tools_system_prompt(voice_system_message(), [] if args.no_tools else VOICE_TOOLS)
    t0 = time.perf_counter()
    stream = create_tool_session(voicechat, system_prompt=prompt, seed=0, profile=True)
    print(f"prompt prefill ({len(prompt)} chars) in {time.perf_counter() - t0:.1f}s")

    with tempfile.TemporaryDirectory() as tmp:
        speech = say_to_wav(args.utterance, Path(tmp) / "user.wav")
    sr = 16_000
    user = np.concatenate(
        [np.zeros(sr, np.float32), speech, np.zeros(int(sr * args.tail_seconds), np.float32)]
    )

    frame = 1_280
    user_track, agent_track = [], []
    transcript, reply = "", ""
    calls = []
    offset = 0
    while offset < user.shape[0]:
        chunk = user[offset : offset + frame]
        offset += frame
        events = stream.push_audio(chunk, sample_rate=sr)
        user_track.append(chunk)
        got_audio = False
        for ev in events:
            if ev.kind == "user_transcript_delta":
                transcript = ev.text
            elif ev.kind == "assistant_text_delta":
                reply = ev.text
                print(ev.delta or "", end="", flush=True)
            elif ev.kind == "audio":
                agent_track.append(np.asarray(ev.samples, dtype=np.float32))
                got_audio = True
            elif ev.kind == "function_delta" and ev.delta is None:
                parsed = parse_tool_call(ev.text)
                print(f"\n[tool call @ frame {ev.frame_index}] {ev.text!r}")
                results = []
                for call in parsed:
                    calls.append({"name": call.name, "arguments": call.arguments, "error": call.error})
                # One tool at a time: only the first call runs (NVIDIA: parallel calls are unreliable).
                first = parsed[0]
                results.append(CANNED.get(first.name, {"error": f"unknown tool {first.name}"}))
                stream.inject_tool_response(results)
                print(f"[tool response injected in {stream.injection_ms[-1]:.0f} ms] {results}")
        if not got_audio:
            # Keep the agent track aligned while the timeline is frozen.
            agent_track.append(np.zeros(int(len(chunk) * 22_050 / sr), np.float32))
    print()

    summary = stream.profile.summary(drop_first=5)
    import soundfile as sf

    agent = np.concatenate(agent_track)
    user_22k = np.interp(
        np.linspace(0, len(user) - 1, int(len(user) * 22_050 / sr)), np.arange(len(user)), user
    ).astype(np.float32)
    n = max(len(agent), len(user_22k))
    stereo = np.zeros((n, 2), np.float32)
    stereo[: len(user_22k), 0] = user_22k
    stereo[: len(agent), 1] = agent
    sf.write(out_dir / "conversation.wav", stereo, 22_050)
    sf.write(out_dir / "agent.wav", agent, 22_050)

    # Intelligibility check: let the model's own ASR transcribe what it said.
    agent_16k = np.interp(
        np.linspace(0, len(agent) - 1, int(len(agent) * sr / 22_050)), np.arange(len(agent)), agent
    ).astype(np.float32)
    heard = voicechat.generate(mx.array(agent_16k), system_prompt="", use_language_cache=True).user_transcript

    report = {
        "model": args.model,
        "utterance": args.utterance,
        "user_transcript": transcript,
        "assistant_text": reply,
        "assistant_audio_heard_as": heard,
        "tool_calls": calls,
        "injection_ms": stream.injection_ms,
        "realtime_factor": summary["realtime_factor"],
        "frame_ms_mean": summary["stages"]["total"]["mean_ms"],
        "frame_ms_p95": summary["stages"]["total"]["p95_ms"],
        "stages_mean_ms": {k: round(v["mean_ms"], 1) for k, v in summary["stages"].items()},
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
