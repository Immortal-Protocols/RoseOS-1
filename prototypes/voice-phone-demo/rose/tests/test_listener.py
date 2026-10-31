"""Listener end to end with the real models on synthesized speech.

    .venv/bin/python -m rose.tests.test_listener
Speech comes from macOS `say` (16 kHz via afconvert) over a low noise floor.
Most cases feed 512-sample frames as fast as possible: endpointing runs on
the audio clock, so decisions match real time and the run is deterministic.
Case 1 is replayed paced in real time to measure wall-clock latency, and the
partials case is paced because partials are a real-time feature.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import mlx.core as mx
import numpy as np
import soundfile as sf

from rose.bus import Bus
from rose.listener import FRAME, SR, ListenEvent, Listener, SileroCPU, has_words, is_dangling, is_preamble, too_fast
from rose.runtime import Runtime

SPEECH_KINDS = {"speech_start", "partial", "speech_pause", "speech_resume", "turn_end", "noise"}
TARGET_MS = 350  # DESIGN: turn_end soon after the user stops (reported)
REGRESSION_MS = 600  # hard failure; the GPU is shared with other work, so the target is not asserted


def say(text: str, voice: str = "Samantha") -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        aiff, wav = Path(tmp) / "a.aiff", Path(tmp) / "a.wav"
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)],
                       check=True)
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


class Rig:
    """One listener for all cases; audio positions are absolute sample indexes."""

    def __init__(self):
        self.rng = np.random.default_rng(7)
        self.runtime = Runtime()
        self.bus = Bus()
        self.events: list[ListenEvent] = []
        self.bus_events: list[dict] = []
        self.bus.subscribe(self.bus_events.append)
        self.listener = Listener(self.runtime, self.bus, self.events.append)
        t = time.perf_counter()
        self.listener.load()
        self.load_s = time.perf_counter() - t
        self.listener.start()
        self.pushed = 0

    def floor(self, seconds: float, std: float = 0.002, n: int | None = None) -> np.ndarray:
        n = int(seconds * SR) if n is None else n
        return (self.rng.standard_normal(n) * std).astype(np.float32)

    def clip(self, *parts: np.ndarray | float) -> tuple[np.ndarray, list[tuple[int, int]]]:
        """Concatenate speech and silences (floats, seconds); returns voiced spans."""

        out, spans, n = [], [], 0
        for part in parts:
            if isinstance(part, (int, float)):
                part = np.zeros(int(part * SR), np.float32)
            else:
                loud = np.nonzero(np.abs(part) > 0.01)[0]
                spans.append((n + int(loud[0]), n + int(loud[-1]) + 1))
            out.append(part)
            n += part.size
        audio = np.concatenate(out)
        return audio + self.floor(0, n=audio.size), spans

    def feed(self, audio: np.ndarray, realtime: bool = False) -> tuple[int, float]:
        """Push in 512-sample frames; returns (first sample index, wall time of sample 0)."""

        base, t0 = self.pushed, time.monotonic()
        for i in range(0, audio.size, FRAME):
            if realtime:
                due = t0 + (i + FRAME) / SR
                time.sleep(max(0.0, due - time.monotonic()))
            self.listener.push(audio[i:i + FRAME])
        self.pushed += audio.size
        deadline = time.monotonic() + 30
        while self.listener.heard_samples < self.pushed // FRAME * FRAME:
            if time.monotonic() > deadline:
                raise TimeoutError("listener did not catch up")
            time.sleep(0.005)
        return base, t0

    def run(self, audio: np.ndarray, realtime: bool = False):
        n_ev, n_bus = len(self.events), len(self.bus_events)
        base, t0 = self.feed(audio, realtime)
        self.feed(self.floor(0.4))  # settle before the next case
        evs = self.events[n_ev:]
        bus = [e for e in self.bus_events[n_bus:] if e["kind"] in SPEECH_KINDS]
        return base, t0, evs, bus


def kinds(evs: list[ListenEvent]) -> list[str]:
    return [e.kind for e in evs]


def endpoint_report(ev: ListenEvent, voice_end: int) -> dict:
    """Audio from the true end of voice to the commit decision, plus the time the
    decision held the listener. Fed faster than real time, the early transcript
    gets no head start, so after_voice_ms overstates real-time latency."""

    audio_ms = (ev.data["t_audio"] * SR - voice_end) * 1000 / SR
    compute = ev.data["wait_ms"] if ev.data["by"] == "smart_turn" else 0.0
    return {"endpoint_ms": ev.data["endpoint_ms"], "decision_audio_ms": round(audio_ms),
            "after_voice_ms": round(audio_ms + compute),
            "wait_ms": ev.data["wait_ms"], "asr_ms": ev.data["asr_ms"], "smart_turn_ms": ev.data["smart_turn_ms"],
            "smart_turn_p": ev.data["smart_turn_p"], "silence_ms": ev.data["silence_ms"], "by": ev.data["by"]}


class FakeSession:
    """Streaming session stand-in: one word per step with input, or a failure."""

    def __init__(self, fail: bool):
        self.fail, self._queued, self._encoded = fail, 0, None

    def feed(self, samples: np.ndarray) -> None:
        self._queued += samples.size

    def step(self, max_decode_tokens: int) -> list[str]:
        if self.fail:
            raise RuntimeError("scripted failure")
        words, self._queued = ([" word"] if self._queued else []), 0
        return words

    def cancel(self) -> None:
        pass


def scripted_listener(runtime: Runtime, bus: Bus, events: list) -> Listener:
    """Listener on scripted models (frames >= 0.5 are speech); its first
    streaming session fails. Exercises the state machine without the GPU."""

    sessions = []

    def create_streaming_session(language: str) -> FakeSession:
        sessions.append(FakeSession(fail=not sessions))
        return sessions[-1]

    listener = Listener(runtime, bus, events.append)
    listener._vad = lambda frame: float(frame[0] >= 0.5)
    listener._turn = SimpleNamespace(predict_endpoint=lambda audio, sample_rate: SimpleNamespace(probability=0.9))
    listener._final = SimpleNamespace(generate=lambda audio: SimpleNamespace(text="hello there"))
    listener._stream = SimpleNamespace(create_streaming_session=create_streaming_session)
    return listener


def scripted_utterance(listener: Listener) -> None:
    """0.64 s of 'speech' paced in real time (partials need wall time), then 1.6 s of silence."""

    target = listener.heard_samples + 70 * FRAME
    for _ in range(20):
        listener.push(np.ones(FRAME, np.float32))
        time.sleep(FRAME / SR)
    listener.push(np.zeros(50 * FRAME, np.float32))
    deadline = time.monotonic() + 10
    while listener.heard_samples < target and time.monotonic() < deadline:
        time.sleep(0.005)


def main() -> int:
    results: dict[str, dict] = {}
    failures: list[str] = []

    def check(case: str, ok: bool, msg: str) -> None:
        if not ok:
            failures.append(f"{case}: {msg}")

    # Pure helpers, no models.
    for text, want in [("I was thinking that we could.", True), ("Go to the", True), ("Send it to Sam.", False),
                       ("Is it going to rain?", False), ("Well, um", True), ("and then,", True),
                       ("Thank you.", False), ("I'm", True), ("Turn it on.", False)]:
        check("unit", is_dangling(text) == want, f"is_dangling({text!r}) != {want}")
    for text, want in [("", False), ("...", False), ("12", False), ("ok", True), ("Hmm.", True)]:
        check("unit", has_words(text) == want, f"has_words({text!r}) != {want}")
    for text, s, want in [("Right, I'm not sure.", 0.42, True), ("Sam, he said.", 0.35, True), ("Yes, Sam.", 0.35, False),
                          ("Tell me a bit about the history of Rome.", 1.92, False), ("Okay.", 0.1, False)]:
        check("unit", too_fast(text, s) == want, f"too_fast({text!r}, {s}) != {want}")
    for text, want in [("High rose, said", True), ("Hi Rose.", True), ("Hi Rose, what's the time?", False)]:
        check("unit", is_preamble(text) == want, f"is_preamble({text!r}) != {want}")

    rig = Rig()
    print(f"loaded in {rig.load_s:.1f} s", flush=True)
    rig.feed(rig.floor(0.5))

    # 0. The numpy VAD matches mlx-audio's Silero, and the listener thread leaves
    # the runtime thread's default device alone (mx.stream(cpu) elsewhere does not).
    vad_audio, _ = rig.clip(0.3, say("Can you text Sam that I'm running late?"), 0.5)
    frames = vad_audio[:vad_audio.size // FRAME * FRAME].reshape(-1, FRAME)

    def mlx_vad():
        from mlx_audio.vad import load as load_vad

        model, state, out = load_vad("mlx-community/silero-vad"), None, []
        for frame in frames:
            prob, state = model.feed(frame, state)
            out.append(float(prob.item()))
        return out, SileroCPU(model)

    ref_p, vad = rig.runtime.run(mlx_vad, timeout=60)
    ref_p, ours = np.array(ref_p), np.array([vad(f) for f in frames])
    device = rig.runtime.run(lambda: str(mx.default_device()))
    results["0_vad"] = {"max_abs_diff": float(np.abs(ours - ref_p).max()), "runtime_device": device}
    check("0", float(np.abs(ours - ref_p).max()) < 1e-4, "numpy VAD differs from mlx-audio Silero")
    check("0", "gpu" in device, f"runtime default device is {device}")

    # 1. A complete sentence then 1.5 s of silence: one turn, fast.
    ref1 = "Please send a message to my mother saying I will be home soon."
    s1 = say(ref1)
    audio, spans = rig.clip(0.3, s1, 1.5)
    base, _, evs, bus = rig.run(audio)
    ends = [e for e in evs if e.kind == "turn_end"]
    check("1", len(ends) == 1, f"expected 1 turn_end, got {kinds(evs)}")
    if ends:
        r = endpoint_report(ends[0], base + spans[0][1])
        r.update(text=ends[0].text, wer=round(wer(ref1, ends[0].text), 2), events=kinds(evs))
        results["1_complete_fast"] = r
        check("1", r["wer"] <= 0.15, f"text {ends[0].text!r}")
        check("1", r["by"] == "smart_turn", "complete sentence waited for the silence timer")
        check("1", r["decision_audio_ms"] <= 260, f"decided {r['decision_audio_ms']} ms of audio after the voice")
    check("1", [e["kind"] for e in bus if e["kind"] != "partial"] ==
          ["speech_start", "speech_pause", "turn_end"], f"bus {[e['kind'] for e in bus]}")

    # 1rt. Same audio paced in real time: wall clock from end of voice to turn_end.
    lat = []
    for _ in range(2):
        base, t0, evs, _ = rig.run(audio, realtime=True)
        ends = [e for e in evs if e.kind == "turn_end"]
        check("1rt", len(ends) == 1, f"expected 1 turn_end, got {kinds(evs)}")
        if ends:
            lat.append(round((ends[0].t - (t0 + spans[0][1] / SR)) * 1000))
            results.setdefault("1_complete_realtime", {}).setdefault("runs", []).append(
                endpoint_report(ends[0], base + spans[0][1]))
    results.setdefault("1_complete_realtime", {}).update(
        wall_after_voice_ms=lat, target_ms=TARGET_MS, target_met=bool(lat) and max(lat) <= TARGET_MS)
    check("1rt", bool(lat) and max(lat) <= REGRESSION_MS, f"real-time latency {lat}")

    # 2. A thinking pause after a dangling word continues the same turn.
    a, b = say("I was thinking that we could"), say("maybe go to the beach")
    audio, spans = rig.clip(0.3, a, 0.7, b, 2.5)
    base, _, evs, _ = rig.run(audio)
    ends = [e for e in evs if e.kind == "turn_end"]
    check("2", len(ends) == 1, f"expected 1 turn_end, got {kinds(evs)}")
    check("2", "speech_resume" in kinds(evs), f"no speech_resume in {kinds(evs)}")
    if ends:
        text = ends[0].text.lower()
        check("2", "thinking" in text and "beach" in text, f"text {ends[0].text!r}")
        results["2_pause_continues"] = {**endpoint_report(ends[0], base + spans[1][1]),
                                        "text": ends[0].text, "events": [k for k in kinds(evs) if k != "partial"]}

    # 3. Silence and low noise: nothing at all.
    audio = np.concatenate([rig.floor(1.0, 0.001), rig.floor(1.0, 0.003), rig.floor(1.0, 0.008)])
    _, _, evs, bus = rig.run(audio)
    check("3", not evs and not bus, f"events on noise: {kinds(evs)}")
    results["3_silence_and_noise"] = {"events": kinds(evs)}

    # 4. Two sentences 2.5 s apart: two turns.
    ref4a, ref4b = "What's the weather like tomorrow?", "Turn on do not disturb until I get home."
    audio, spans = rig.clip(0.3, say(ref4a, "Daniel"), 2.5, say(ref4b, "Karen"), 1.5)
    base, _, evs, _ = rig.run(audio)
    ends = [e for e in evs if e.kind == "turn_end"]
    check("4", len(ends) == 2, f"expected 2 turn_ends, got {kinds(evs)}")
    if len(ends) == 2:
        w = [wer(ref4a, ends[0].text), wer(ref4b, ends[1].text)]
        check("4", max(w) <= 0.2, f"texts {[e.text for e in ends]}")
        results["4_two_turns"] = {"texts": [e.text for e in ends], "wer": [round(x, 2) for x in w],
                                  "after_voice_ms": [endpoint_report(e, base + s[1])["after_voice_ms"]
                                                     for e, s in zip(ends, spans)]}

    # 5. Partials stream while a long sentence is spoken (paced in real time).
    ref5 = ("So I was looking at my calendar for next week and I noticed that the meeting "
            "with the design team on Thursday overlaps with my dentist appointment.")
    audio, spans = rig.clip(0.3, say(ref5), 1.5)
    base, t0, evs, _ = rig.run(audio, realtime=True)
    parts = [e for e in evs if e.kind == "partial"]
    ends = [e for e in evs if e.kind == "turn_end"]
    voice_end_wall = t0 + spans[0][1] / SR
    check("5", len(ends) == 1, f"expected 1 turn_end, got {[k for k in kinds(evs) if k != 'partial']}")
    check("5", len(parts) >= 5, f"only {len(parts)} partials")
    if parts and ends:
        during = [p for p in parts if p.t < voice_end_wall]
        gaps = [b.t - a.t for a, b in zip(parts, parts[1:])]
        first_ms = round((parts[0].t - (t0 + spans[0][0] / SR)) * 1000)
        check("5", len(during) >= 3, f"{len(during)} partials before the voice ended")
        check("5", parts[-1].t <= ends[0].t, "partial after turn_end")
        check("5", not gaps or min(gaps) >= 0.095, f"partials faster than 10/s (min gap {min(gaps):.3f})")
        results["5_partials"] = {
            "partials": len(parts), "before_voice_end": len(during), "first_partial_after_voice_start_ms": first_ms,
            "min_gap_ms": round(min(gaps) * 1000) if gaps else None,
            "last_partial_before_end": parts[-1].text if during else None,
            "partial_words_at_voice_end": len(words(during[-1].text)) if during else 0,
            "ref_words": len(words(ref5)), "final": ends[0].text, "final_wer": round(wer(ref5, ends[0].text), 2),
            "wall_after_voice_ms": round((ends[0].t - voice_end_wall) * 1000),
            "endpoint": endpoint_report(ends[0], base + spans[0][1]),
        }

    # 6. A voiced sound that transcribes to no words ends as "noise", not a turn.
    # Synthetic non-words are not reliably transcribed as empty, so the final
    # transcript is stubbed to punctuation; VAD and smart-turn stay real.
    real = rig.listener._final
    rig.listener._final = SimpleNamespace(generate=lambda audio: SimpleNamespace(text=" . "))
    try:
        audio, _ = rig.clip(0.3, say("mm hmm", "Karen"), 1.5)
        _, _, evs, bus = rig.run(audio)
    finally:
        rig.listener._final = real
    check("6", "noise" in kinds(evs) and "turn_end" not in kinds(evs), f"events {kinds(evs)}")
    check("6", [e["kind"] for e in bus] == ["speech_start", "speech_pause", "noise"],
          f"bus {[e['kind'] for e in bus]}")
    results["6_non_word"] = {"events": kinds(evs)}

    # 7. While Rose is audible the start threshold is stricter; real speech still gets through.
    rig.listener.set_rose_audible(True)
    ref7 = "Actually make it fifteen minutes instead."
    audio, spans = rig.clip(0.3, say(ref7, "Daniel"), 1.5)
    base, _, evs, _ = rig.run(audio)
    rig.listener.set_rose_audible(False)
    starts = [e for e in evs if e.kind == "speech_start"]
    ends = [e for e in evs if e.kind == "turn_end"]
    check("7", len(starts) == 1 and len(ends) == 1, f"events {kinds(evs)}")
    if starts and ends:
        delay = round((starts[0].data["t_audio"] * SR - (base + spans[0][0])) * 1000 / SR)
        results["7_rose_audible"] = {"text": ends[0].text, "wer": round(wer(ref7, ends[0].text), 2),
                                     "speech_start_vs_voice_ms": delay}
        check("7", wer(ref7, ends[0].text) <= 0.2, f"text {ends[0].text!r}")

    # 8. A NaN in the mic stream must not deafen the listener: it used to poison the
    # VAD's LSTM state for good and kill the streaming ASR job (session.feed raises).
    ref8 = "Please call my sister tonight."
    audio, _ = rig.clip(0.3, say(ref8), 1.5)
    audio[int(0.9 * SR)] = np.nan
    _, _, evs, _ = rig.run(audio)
    ends = [e for e in evs if e.kind == "turn_end"]
    check("8", len(ends) == 1 and wer(ref8, ends[0].text) <= 0.2, f"events {kinds(evs)}")
    check("8", bool(np.isfinite(rig.listener._vad.h).all()), "VAD state is not finite")
    check("8", not rig.listener._job.future.done(), "streaming ASR job ended")
    results["8_nan_input"] = {"events": [k for k in kinds(evs) if k != "partial"],
                              "text": ends[0].text if ends else None}

    # 9. Scripted models: a failing streaming session costs only its utterance's
    # partials (with a warning); the next utterance still gets them.
    sevents: list[ListenEvent] = []
    sbus = Bus()
    warnings: list[dict] = []
    sbus.subscribe(lambda m: warnings.append(m) if m["kind"] == "warning" else None)
    scripted = scripted_listener(rig.runtime, sbus, sevents)
    scripted.start()
    scripted_utterance(scripted)
    first = list(sevents)
    scripted_utterance(scripted)
    second = sevents[len(first):]
    check("9", kinds(first).count("turn_end") == 1 and "partial" not in kinds(first), f"first {kinds(first)}")
    check("9", len(warnings) == 1, f"warnings {warnings}")
    check("9", kinds(second).count("turn_end") == 1 and "partial" in kinds(second), f"second {kinds(second)}")
    check("9", not scripted._job.future.done(), "streaming ASR job ended")

    # 10. stop() twice, then start(): the new run must not consume a stale stop sentinel.
    scripted.stop()
    scripted.stop()
    scripted.start()
    before = len(sevents)
    scripted_utterance(scripted)
    check("10", scripted._thread.is_alive() and kinds(sevents[before:]).count("turn_end") == 1,
          f"after restart: alive={scripted._thread.is_alive()} {kinds(sevents[before:])}")
    scripted.stop()
    results["9_10_scripted"] = {"first": kinds(first), "second": kinds(second),
                                "after_restart": kinds(sevents[before:]), "warnings": len(warnings)}

    results["listener_stats"] = rig.listener.stats()
    results["runtime"] = rig.runtime.stats()
    rig.listener.stop()
    rig.runtime.stop()
    print(json.dumps(results, indent=1))
    if failures:
        print("FAIL\n  " + "\n  ".join(failures))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
