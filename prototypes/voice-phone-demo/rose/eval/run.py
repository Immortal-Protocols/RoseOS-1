"""End-to-end evaluation: the real Rose pipeline, a scripted user, a fake client.

Builds the same components as ``rose.app`` (runtime, brain, listener,
speaker, phone, tasks, conductor) with a ``FakeTransport`` in place of the
browser. The user's speech (macOS ``say``) enters the mic in 32 ms frames
paced in real time, and a faint noise floor is fed whenever nobody speaks:
the listener's endpointing and the conductor's turn taking only behave as
in real use on a continuous, real-time stream. The fake client plays Rose's
audio in real time and reports progress exactly like the browser player.

Every bus event, audio piece sent, client report and harness action is
recorded with a monotonic timestamp. Checks and metrics are derived from that
trace afterwards, so every number can be traced back to events in the JSONL.

    .venv/bin/python -m rose.eval.run [--only greeting,barge_in] [--repeat 3]
        [--out out/eval_v3] [--voice zoey] [--user-voice Samantha] [--transcribe]

Writes ``<out>/<scenario>.jsonl`` (events), ``results.json`` and
``summary.md``. All GPU work runs in this process: do not run it next to the
app or another GPU job.
"""

from __future__ import annotations

import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import argparse
import collections
import contextlib
import copy
import hashlib
import importlib
import json
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np
import soundfile as sf

from rose.textnorm import speakable

from .scenarios import (BY_NAME, SCENARIOS, Check, Expect, Mark, Notify, Say, SayWhileRoseSpeaks, Scenario,
                        Settle, Wait, WaitFor, WaitIdle, clip, say, words)

ROOT = Path(__file__).resolve().parents[2]
MIC_SR, OUT_SR, FRAME = 16_000, 24_000, 512
FRAME_S = FRAME / MIC_SR
NOISE_FLOOR = 3e-4  # about -70 dBFS: a quiet room, not digital zero
NOT_ACTIVITY = {"vad", "stats", "level"}
FINISHED = {"done", "failed", "cancelled"}
PARAKEET = "mlx-community/parakeet-tdt-0.6b-v3"
COMPONENTS = [("runtime", "Runtime"), ("bus", "Bus"), ("brain", "Brain"), ("transport", "FakeTransport"),
              ("listener", "Listener"), ("speaker", "Speaker"), ("phone", "Phone"),
              ("tasks", "TaskManager"), ("conductor", "Conductor")]
LATENCY_ORDER = ["voice_to_voice_ms", "turn_end_after_voice_ms", "endpoint_ms", "asr_ms",
                 "llm_first_token_ms", "first_speech_text_ms", "tts_first_audio_ms", "conductor_v2v_ms",
                 "speaker_latency_ms"]


class MissingPieces(RuntimeError):
    pass


def load_components() -> SimpleNamespace:
    """The pipeline's classes, or one error naming every piece that is missing or broken."""

    found, missing = {}, []
    for module, name in COMPONENTS:
        try:
            found[name] = getattr(importlib.import_module(f"rose.{module}"), name)
        except Exception as exc:  # report all of them, not just the first
            missing.append(f"rose.{module}.{name}: {type(exc).__name__}: {exc}")
    if missing:
        raise MissingPieces("rose.eval needs the whole pipeline; missing or broken:\n  " + "\n  ".join(missing))
    return SimpleNamespace(**found)


# ------------------------------------------------------------------ trace
class Trace:
    """Every event of the run with the monotonic time it was seen (thread-safe, append-only)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.events: list[dict] = []
        self.last_activity = time.monotonic()

    def on_bus(self, msg: dict) -> None:
        self._append({**msg, "mono": time.monotonic()})

    def add(self, kind: str, **fields) -> dict:
        ev = {**fields, "kind": kind, "t": time.time(), "mono": time.monotonic()}
        self._append(ev)
        return ev

    def _append(self, ev: dict) -> None:
        with self._lock:
            self.events.append(ev)
            if ev["kind"] not in NOT_ACTIVITY:
                self.last_activity = ev["mono"]

    def window(self, start: float, end: float | None = None) -> list[dict]:
        with self._lock:
            events = list(self.events)
        return [e for e in events if e["mono"] >= start and (end is None or e["mono"] <= end)]

    def find(self, kind: str, since: float, pred: Callable[[dict], bool] | None = None) -> dict | None:
        """Earliest ``kind`` event at or after ``since`` matching ``pred``."""

        with self._lock:
            events = self.events[:]
        hit = None
        for e in reversed(events):
            if e["mono"] < since - 1.0:  # threads may append slightly out of order
                break
            if e["kind"] == kind and e["mono"] >= since and (pred is None or pred(e)):
                hit = e
        return hit


class Playback:
    """Live view of the fake client: is Rose audible right now, and since when."""

    def __init__(self):
        self._lock = threading.Lock()
        self.playing = False
        self.since: float | None = None
        self.changed = time.monotonic()

    def sent(self) -> None:
        with self._lock:
            if not self.playing:
                self.playing, self.since, self.changed = True, time.monotonic(), time.monotonic()

    def report(self, msg: dict) -> None:
        with self._lock:
            if msg.get("type") in ("idle", "stopped") and self.playing:
                self.playing, self.since, self.changed = False, None, time.monotonic()


def _recording(cls: type, trace: Trace, playback: Playback) -> type:
    """A subclass of the fake client that also logs what is sent to it."""

    class Recording(cls):
        after_send: Callable[[], None] | None = None

        def send_audio(self, seg: int, pcm: np.ndarray) -> None:
            trace.add("eval.send", seg=int(seg), samples=int(len(pcm)))
            playback.sent()
            super().send_audio(seg, pcm)
            if self.after_send is not None:
                self.after_send()

        def send_control(self, msg: dict) -> None:
            trace.add("eval.control", **_fields(msg))
            super().send_control(msg)

    Recording.__name__ = Recording.__qualname__ = f"Recording{cls.__name__}"
    return Recording


def _fields(msg: dict) -> dict:
    return {k: v for k, v in msg.items() if k not in ("kind", "t", "mono")}


# ---------------------------------------------------------------- pipeline
@dataclass
class Pipeline:
    runtime: Any
    bus: Any
    brain: Any
    transport: Any
    listener: Any
    speaker: Any
    phone: Any
    tasks: Any
    conductor: Any
    trace: Trace
    playback: Playback
    load_s: dict = field(default_factory=dict)
    keepalive: Any = None
    _audible: bool = False

    def wire(self) -> None:
        """What rose.app does: mic -> listener, client reports -> speaker and conductor,
        speaker audibility -> listener (echo guard), phone changes -> conductor."""

        self.transport.on_mic = self.listener.push
        self.transport.on_client = self._client
        if hasattr(self.speaker, "on_audible"):
            self.speaker.on_audible = self.listener.set_rose_audible
        else:
            self.transport.after_send = self._sync_audible
        self.phone.listeners.append(self._phone_changed)

    def _client(self, msg: dict) -> None:
        self.trace.add("eval.client", **_fields(msg))
        self.playback.report(msg)
        self.speaker.on_client(msg)
        hook = getattr(self.conductor, "on_client", None)
        if hook is not None:
            hook(msg)
        if not hasattr(self.speaker, "on_audible"):
            self._sync_audible()

    def _sync_audible(self) -> None:
        audible = bool(getattr(self.speaker, "audible", False))
        if audible != self._audible:
            self._audible = audible
            if hasattr(self.listener, "set_rose_audible"):
                self.listener.set_rose_audible(audible)

    def _phone_changed(self, what: str, info: dict | None = None) -> None:
        info = info or {}
        if getattr(self.phone, "bus", None) is None:
            if what == "notification":
                self.bus.emit("notification", **info)
            self.bus.emit("phone", version=getattr(self.phone, "version", 0), what=what)
        self.conductor.on_phone(what, info)

    def reset(self, timeout: float = 30.0) -> None:
        """Fresh phone data and a fresh conversation; models stay loaded."""

        old = getattr(self.conductor, "talker", None)
        if hasattr(self.phone, "reset"):
            self.phone.reset()
        reset = getattr(self.conductor, "reset", None)
        if reset is None:
            self.trace.add("warning", message="eval: conductor has no reset(); conversation carries over")
            return
        reset()
        if old is None:
            time.sleep(1.0)
            return
        deadline = time.monotonic() + timeout
        while getattr(self.conductor, "talker", None) is old and time.monotonic() < deadline:
            time.sleep(0.05)

    def busy(self) -> str | None:
        if getattr(self.listener, "in_speech", False):
            return "user speech in progress"
        if self.playback.playing:
            return "Rose audio playing"
        running = self.tasks.running() if hasattr(self.tasks, "running") else []
        if running:
            return f"{len(running)} task(s) running"
        return None

    def played_samples(self) -> int:
        if hasattr(self.transport, "played_samples"):
            return int(self.transport.played_samples())
        return len(self.transport.played_audio())

    def close(self) -> None:
        for fn in (getattr(self.listener, "stop", None), getattr(self.conductor, "stop", None),
                   getattr(self.keepalive, "stop", None), getattr(self.transport, "close", None),
                   self.runtime.stop):
            if fn is not None:
                with contextlib.suppress(Exception):
                    fn()


def build_pipeline(voice: str, parts: SimpleNamespace | None = None, keepalive: bool = True,
                   log: Callable[[str], None] = print) -> Pipeline:
    """Load and start every component the way rose.app does (``parts`` overrides the classes, for tests)."""

    m = parts or load_components()
    from rose.runtime import LISTEN

    trace, playback = Trace(), Playback()
    runtime = m.Runtime()
    bus = m.Bus(history=5000)
    bus.subscribe(trace.on_bus)
    load_s: dict[str, float] = {}

    def timed(label: str, fn: Callable[[], Any]) -> Any:
        t = time.monotonic()
        log(f"loading {label}...")
        out = fn()
        load_s[label] = round(time.monotonic() - t, 1)
        return out

    brain = timed("brain", lambda: runtime.run(m.Brain, priority=LISTEN, name="eval-load-brain", timeout=900))
    task_brain = brain
    task_repo = os.environ.get("ROSE_TASK_BRAIN")
    if task_repo and task_repo != getattr(brain, "repo", None):  # as app.py's --task-brain
        task_brain = timed("task brain", lambda: runtime.run(lambda: m.Brain(task_repo), priority=LISTEN,
                                                             name="eval-load-task-brain", timeout=900))
    ref = SimpleNamespace(conductor=None)  # components are built before the conductor that consumes them

    def on_listen(ev: Any) -> None:
        if ref.conductor is not None:
            ref.conductor.on_listen(ev)

    def on_task_done(task: Any) -> None:
        if ref.conductor is not None:
            ref.conductor.on_task_done(task)

    transport = _recording(m.FakeTransport, trace, playback)()
    listener = m.Listener(runtime, bus, on_listen)
    speaker = m.Speaker(runtime, bus, transport)
    try:
        phone = m.Phone(bus=bus)
    except TypeError:
        phone = m.Phone()
    tasks = m.TaskManager(runtime, task_brain, phone, bus, on_task_done)
    conductor = ref.conductor = m.Conductor(runtime, brain, listener, speaker, tasks, phone, bus)
    pipe = Pipeline(runtime, bus, brain, transport, listener, speaker, phone, tasks, conductor,
                    trace, playback, load_s)
    pipe.wire()
    timed("listener", listener.load)
    timed("speaker", lambda: speaker.load(voice))
    timed("tasks", tasks.prepare)
    timed("conductor", conductor.start)
    listener.start()
    if keepalive:
        pipe.keepalive = _keepalive(runtime, log)
    return pipe


def _keepalive(runtime: Any, log: Callable[[str], None]) -> Any:
    """The app's GPU keepalive (macOS idles the Metal queue after ~1 s; the app keeps it warm)."""

    try:
        from rose.app import GpuKeepalive
    except Exception as exc:
        log(f"no GPU keepalive ({type(exc).__name__}: {exc}); latencies after idle gaps may be worse than the app's")
        return None
    k = GpuKeepalive(runtime)
    k.start()
    return k


# ---------------------------------------------------------------- user side
@dataclass(eq=False)
class UserUtterance:
    index: int
    text: str
    voice: str
    role: str  # "turn" | "barge" | "backchannel"
    audio_s: float
    t_queued: float
    t_first_fed: float | None = None
    t_voice_start: float | None = None  # monotonic time the first voiced frame was pushed
    t_voice_end: float | None = None    # ... the last voiced frame
    t_done: float | None = None
    rose_playing: bool = False          # Rose was audible when the user started
    done: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self, start: float) -> dict:
        def rel(t: float | None) -> float | None:
            return None if t is None else round(t - start, 3)

        return {"index": self.index, "text": self.text, "voice": self.voice, "role": self.role,
                "audio_s": round(self.audio_s, 2), "voice_start_s": rel(self.t_voice_start),
                "voice_end_s": rel(self.t_voice_end), "done_s": rel(self.t_done),
                "rose_playing": self.rose_playing}


def voiced_bounds(audio: np.ndarray, sr: int = MIC_SR) -> tuple[int, int]:
    """First and last sample of the voiced part (10 ms RMS windows)."""

    win = sr // 100
    n = len(audio) // win
    if n == 0:
        return 0, max(0, len(audio) - 1)
    rms = np.sqrt(np.mean(audio[: n * win].reshape(n, win) ** 2, axis=1))
    voiced = np.flatnonzero(rms > max(0.003, 0.05 * float(rms.max())))
    if voiced.size == 0:
        return 0, len(audio) - 1
    return int(voiced[0] * win), int((voiced[-1] + 1) * win - 1)


class Synth:
    """User speech from macOS ``say`` at 16 kHz mono, cached on disk by voice and text."""

    def __init__(self, cache: Path):
        self.cache = cache
        cache.mkdir(parents=True, exist_ok=True)
        self._mem: dict[tuple[str, str], tuple[np.ndarray, int, int]] = {}

    def get(self, text: str, voice: str) -> tuple[np.ndarray, int, int]:
        key = (voice, text)
        if key not in self._mem:
            digest = hashlib.sha1(f"{voice}\n{text}".encode()).hexdigest()[:16]
            path = self.cache / f"{voice}-{digest}.wav"
            if not path.exists():
                self._say(text, voice, path)
            audio, sr = sf.read(path, dtype="float32", always_2d=False)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            if sr != MIC_SR:
                raise RuntimeError(f"{path} is {sr} Hz, expected {MIC_SR}")
            self._mem[key] = (audio, *voiced_bounds(audio))
        return self._mem[key]

    @staticmethod
    def _say(text: str, voice: str, path: Path) -> None:
        tmp = path.with_name(path.stem + ".part.wav")
        cmd = ["say", "-v", voice, "--file-format=WAVE", f"--data-format=LEI16@{MIC_SR}", "-o", str(tmp), text]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        except FileNotFoundError as exc:
            raise RuntimeError("macOS `say` is required to synthesize the user's speech") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"`say -v {voice}` failed: {exc.stderr.decode(errors='replace').strip()}") from exc
        if not tmp.exists() or tmp.stat().st_size < 1000:
            raise RuntimeError(f"`say -v {voice}` produced no audio (is the voice installed?)")
        tmp.replace(path)


class MicFeeder:
    """A real-time microphone: one 32 ms frame every 32 ms, speech when queued, else a noise floor.

    The first and last voiced frame of each utterance are timestamped as they
    are pushed: "the user stopped speaking" is when the listener received the
    last voiced audio, which is where voice-to-voice latency starts.
    """

    def __init__(self, push: Callable[[np.ndarray], None], trace: Trace, playback: Playback,
                 noise: float = NOISE_FLOOR):
        self.push, self.trace, self.playback, self.noise = push, trace, playback, noise
        self._queue: collections.deque[list] = collections.deque()
        self._current: list | None = None
        self._stop = threading.Event()
        self._rng = np.random.default_rng(1234)
        self._thread = threading.Thread(target=self._run, name="eval-mic", daemon=True)
        self.reset_stats()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    @property
    def busy(self) -> bool:
        return self._current is not None or bool(self._queue)

    def speak(self, u: UserUtterance, audio: np.ndarray, onset: int, offset: int) -> None:
        self._queue.append([u, audio, onset, offset, 0])

    def reset_stats(self) -> None:
        self.frames, self.late, self.max_late_ms = 0, 0, 0.0

    def stats(self) -> dict:
        return {"frames": self.frames, "late_frames": self.late, "max_late_ms": round(self.max_late_ms, 1)}

    def _run(self) -> None:
        t0, n = time.monotonic(), 0
        while not self._stop.is_set():
            target = t0 + n * FRAME_S
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            late_ms = (time.monotonic() - target) * 1000
            self.frames += 1
            self.max_late_ms = max(self.max_late_ms, late_ms)
            self.late += late_ms > FRAME_S * 1000  # a whole frame behind
            try:
                self._frame()
            except Exception:
                traceback.print_exc()
            n += 1

    def _frame(self) -> None:
        frame = (self._rng.standard_normal(FRAME) * self.noise).astype(np.float32)
        if self._current is None and self._queue:
            self._current = self._queue.popleft()
            u = self._current[0]
            u.t_first_fed = time.monotonic()
            self.trace.add("eval.say_start", utterance=u.index, text=u.text, voice=u.voice, role=u.role)
        if self._current is None:
            self.push(frame)
            return
        u, audio, onset, offset, pos = self._current
        chunk = audio[pos : pos + FRAME]
        frame[: len(chunk)] += chunk
        now = time.monotonic()
        self.push(frame)
        if pos <= onset < pos + FRAME:
            u.t_voice_start, u.rose_playing = now, self.playback.playing
            self.trace.add("eval.voice_start", utterance=u.index, role=u.role, rose_playing=u.rose_playing)
        if pos <= offset < pos + FRAME:
            u.t_voice_end = now
            self.trace.add("eval.voice_end", utterance=u.index)
        pos += FRAME
        if pos >= len(audio):
            u.t_done, self._current = now, None
            self.trace.add("eval.say_done", utterance=u.index)
            u.done.set()
        else:
            self._current[4] = pos


# ----------------------------------------------------------------- analysis
def _ms(dt: float | None) -> float | None:
    return None if dt is None else round(dt * 1000, 1)


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


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


def _same_call(a: dict, b: dict) -> bool:
    if a.get("name") != b.get("name"):
        return False
    norm = lambda c: json.dumps({k: " ".join(words(str(v))) for k, v in (c.get("arguments") or {}).items()},
                                sort_keys=True)
    if norm(a) == norm(b):
        return True
    if a.get("name") in ("phone_task", "think"):
        key = "goal" if a.get("name") == "phone_task" else "question"
        wa = set(words(str((a.get("arguments") or {}).get(key, ""))))
        wb = set(words(str((b.get("arguments") or {}).get(key, ""))))
        return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.8
    return False


class Context:
    """What checks see: one scenario's events, the user's utterances, derived turns and timings."""

    def __init__(self, events: list[dict], utterances: list[UserUtterance], marks: dict[str, float],
                 phone: Any, phone_start: dict, start: float, end: float):
        self.all_events = sorted(events, key=lambda e: e["mono"])
        self.utterances, self.marks = utterances, marks
        self.phone, self.phone_start = phone, phone_start
        self.start, self.end = start, end
        self._cache: dict[str, Any] = {}

    # ------------------------------------------------------------- raw
    def events(self, kind: str | None = None, since: float | None = None, until: float | None = None,
               pred: Callable[[dict], bool] | None = None) -> list[dict]:
        return [e for e in self.all_events
                if (kind is None or e["kind"] == kind) and (since is None or e["mono"] >= since)
                and (until is None or e["mono"] <= until) and (pred is None or pred(e))]

    def first(self, kind: str, since: float | None = None, until: float | None = None,
              pred: Callable[[dict], bool] | None = None) -> dict | None:
        hits = self.events(kind, since, until, pred)
        return hits[0] if hits else None

    def phone_data(self) -> dict:
        with getattr(self.phone, "_lock", None) or contextlib.nullcontext():
            return copy.deepcopy(self.phone.data)

    def tool_calls(self, name: str | None = None) -> list[dict]:
        return self.events("tool_call", pred=lambda e: name is None or e.get("name") == name)

    # ---------------------------------------------------------- replies
    def _seg_starts(self) -> dict[int, float]:
        """When each segment started playing at the client.

        A report's ``samples`` is the position inside the piece at the head of
        the queue, so ``report time - samples / 24 kHz`` is when that piece
        started; the earliest over a segment's reports is the segment's start
        (never before it was sent).
        """

        sent: dict[int, float] = {}
        est: dict[int, float] = {}
        for e in self.all_events:
            if e["kind"] == "eval.send":
                sent.setdefault(e["seg"], e["mono"])
            elif e["kind"] == "eval.client" and e.get("type") in ("played", "stopped"):
                seg, n = e.get("seg"), e.get("samples") or 0
                if seg is None or seg < 0 or n <= 0:
                    continue
                s = e["mono"] - n / OUT_SR
                est[seg] = min(est.get(seg, s), s)
        return {seg: max(s, sent.get(seg, s)) for seg, s in est.items()}

    def replies(self) -> list[dict]:
        """Rose's replies by id, ordered by when she started generating them.

        A reply still being spoken when the window closes (the end-of-scenario
        settle timed out) has no ``rose_turn`` yet; it gets the chunks sent to
        TTS as its text and ``unfinished``, so a long loop is still judged.
        """

        if "replies" in self._cache:
            return self._cache["replies"]
        by: dict[Any, dict] = {}
        for e in self.all_events:
            if e["kind"] not in ("rose_partial", "rose_say", "rose_audio_start", "rose_heard", "rose_turn",
                                 "tool_call") or e.get("id") is None:
                continue
            r = by.setdefault(e["id"], {"id": e["id"], "start": None, "t": None, "text": "", "heard": None,
                                        "interrupted": False, "said": [], "segs": [], "generated": "",
                                        "end": None, "tools": [], "unfinished": False})
            kind = e["kind"]
            if kind in ("rose_partial", "rose_say", "rose_audio_start") and r["start"] is None:
                r["start"], r["t"] = e["mono"], e["t"]
            if kind == "rose_partial":
                r["generated"] = e.get("text", "")
            elif kind == "rose_say":
                r["said"].append(e.get("text", ""))
                if e.get("seg") is not None:
                    r["segs"].append(e["seg"])
            elif kind == "rose_heard":
                r["heard"] = e.get("text", "")
                r["interrupted"] |= bool(e.get("interrupted"))
            elif kind == "rose_turn":
                r["text"], r["end"] = e.get("text", ""), e["mono"]
                r["interrupted"] |= bool(e.get("interrupted"))
            elif kind == "tool_call":
                r["tools"].append(e.get("name"))
        replies = sorted((r for r in by.values() if r["start"] is not None or r["end"] is not None),
                         key=lambda r: r["start"] if r["start"] is not None else r["end"])
        starts, sent = self._seg_starts(), {}
        for e in self.events("eval.send"):
            sent.setdefault(e["seg"], e["mono"])
        for i, r in enumerate(replies):
            if r["start"] is None:
                r["start"], r["t"] = r["end"], self._unix(r["end"])
            segs = r["segs"]
            if not segs:  # no segment ids in rose_say: attribute audio sent while this reply was current
                nxt = replies[i + 1]["start"] if i + 1 < len(replies) and replies[i + 1]["start"] else self.end
                segs = [s for s, t in sent.items() if r["start"] <= t < nxt]
            played = [starts[s] for s in segs if s in starts]
            r["first_played"] = min(played) if played else None
            r["said"] = " ".join(x.strip() for x in r["said"] if x)
        # Only the last reply can be cut by the window: any earlier one was finalized
        # (rose_heard) or abandoned as a draft before the user's next turn.
        last = replies[-1] if replies else None
        if last is not None and last["end"] is None and last["heard"] is None and last["said"] \
                and self.first("barge", last["start"], pred=lambda e: e.get("stage") == "abandon") is None:
            last["text"], last["unfinished"] = last["said"], True
        self._cache["replies"] = replies
        return replies

    def _unix(self, mono: float) -> float:
        e = min(self.all_events, key=lambda e: abs(e["mono"] - mono), default=None)
        return e["t"] + (mono - e["mono"]) if e else time.time()

    def rose_turns(self) -> list[dict]:
        return [r for r in self.replies() if r["text"].strip()]

    def rose_text(self, since: float | None = None, until: float | None = None) -> str:
        return " ".join(r["text"].strip() for r in self.rose_turns()
                        if (since is None or r["start"] >= since) and (until is None or r["start"] < until))

    # ---------------------------------------------------------- metrics
    def _owners(self) -> dict[int, dict]:
        """Each utterance's user_turn: the last one before the next utterance began.

        A turn committed too early and then resumed produces two user_turns; the
        conductor drops the first draft and answers the second (carrying the text).
        """

        utts = [u for u in self.utterances if u.t_voice_start is not None]
        out: dict[int, dict] = {}
        for e in self.events("user_turn"):
            prior = [u for u in utts if u.t_voice_start <= e["mono"]]
            if prior:
                out[max(prior, key=lambda u: u.t_voice_start).index] = e
        return out

    def turns(self) -> list[dict]:
        """Per user turn: what was recognized, Rose's reply, and where the time went."""

        if "turns" in self._cache:
            return self._cache["turns"]
        replies = {r["id"]: r for r in self.replies()}
        owners = self._owners()
        metrics = {e["turn"].get("id"): e["turn"] for e in self.events("metrics") if isinstance(e.get("turn"), dict)}
        audio_start = {e.get("id"): e for e in self.events("rose_audio_start")}
        rows = []
        for u in self.utterances:
            if u.role == "backchannel":
                continue
            row: dict[str, Any] = {"utterance": u.index, "role": u.role, "user_text": u.text}
            ut = owners.get(u.index)
            if ut is None:
                row["error"] = "no user_turn for this utterance"
                rows.append(row)
                continue
            row["asr_text"] = ut.get("text", "")
            row["asr_wer"] = round(wer(u.text, row["asr_text"]), 3)
            te = [e for e in self.events("turn_end", since=u.t_voice_start, until=ut["mono"])]
            if te:
                te = te[-1]
                row.update({k: te.get(k) for k in ("endpoint_ms", "asr_ms", "smart_turn_p") if te.get(k) is not None})
                if u.t_voice_end is not None:
                    row["turn_end_after_voice_ms"] = _ms(te["mono"] - u.t_voice_end)
            rid = ut.get("id")
            row["reply_id"] = rid
            r = replies.get(rid)
            if r is not None:
                row["reply_text"] = r["text"]
                row["first_played"] = r["first_played"]
                if r["first_played"] is not None and u.t_voice_end is not None:
                    row["voice_to_voice_ms"] = _ms(r["first_played"] - u.t_voice_end)
            m = metrics.get(rid) or {}
            for k, v in m.items():
                if _num(v) and k.endswith("_ms"):
                    row.setdefault("conductor_v2v_ms" if k == "voice_to_voice_ms" else k, v)
            if "tts_first_audio_ms" not in row and _num(m.get("voice_to_voice_ms")) \
                    and _num(m.get("first_speech_text_ms")):
                row["tts_first_audio_ms"] = m["voice_to_voice_ms"] - m["first_speech_text_ms"]
            a = audio_start.get(rid)
            if a is not None and _num(a.get("latency_ms")):
                row["speaker_latency_ms"] = a["latency_ms"]
            rows.append(row)
        self._cache["turns"] = rows
        return rows

    def barges(self) -> list[dict]:
        """Per utterance spoken over Rose: duck and stop timing, and what she remembers saying."""

        replies = self.replies()
        rows = []
        for u in self.utterances:
            if u.role not in ("barge", "backchannel") or u.t_voice_start is None:
                continue
            t0 = u.t_voice_start
            playing = [r for r in replies if r["first_played"] is not None and r["first_played"] <= t0]
            target = playing[-1] if playing else None
            horizon = t0 + 8.0
            if target is not None and target["end"] is not None:
                horizon = min(horizon, max(target["end"], t0) + 0.5)
            duck = (self.first("eval.control", t0, horizon, lambda e: e.get("type") == "duck")
                    or self.first("barge", t0, horizon, lambda e: e.get("stage") == "duck"))
            stop_cmd = self.first("eval.control", t0, horizon, lambda e: e.get("type") == "stop")
            stopped = self.first("eval.client", t0, horizon, lambda e: e.get("type") == "stopped")
            pause = self.first("eval.control", t0, horizon, lambda e: e.get("type") == "pause")
            resumed = (self.first("eval.control", t0, horizon, lambda e: e.get("type") == "unduck")
                       or self.first("barge", t0, horizon, lambda e: e.get("stage") == "resume"))
            row = {"utterance": u.index, "role": u.role, "user_text": u.text, "t_voice_start": t0,
                   "rose_playing": u.rose_playing, "reply_id": target["id"] if target else None,
                   "duck_ms": _ms(duck["mono"] - t0) if duck else None,
                   "stop_cmd_ms": _ms(stop_cmd["mono"] - t0) if stop_cmd else None,
                   "stop_ms": _ms(stopped["mono"] - t0) if stopped else None,
                   "pause_ms": _ms(pause["mono"] - t0) if pause else None,
                   "resumed": resumed is not None}
            # Silent = the pause has faded out (40 ms ramp) or the stop report came.
            silent = [v for v in (row["pause_ms"] and row["pause_ms"] + 40, row["stop_ms"]) if v]
            row["silent_ms"] = min(silent) if silent else None
            if target is not None:
                row.update(heard=target["heard"], said=target["said"], generated=target["generated"],
                           interrupted=target["interrupted"])
            rows.append(row)
        return rows

    def unprompted(self) -> list[dict]:
        """Rose turns with no user turn, finished task or notification since her previous turn."""

        triggers = [e["mono"] for e in self.all_events
                    if e["kind"] in ("user_turn", "eval.notify", "notification", "event_queued")
                    or (e["kind"] == "task" and (e.get("task") or {}).get("status") in ("done", "failed"))]
        out, prev = [], self.start
        for r in self.rose_turns():
            if not any(prev <= t <= r["start"] for t in triggers):
                out.append(r)
            prev = r["start"]
        return out

    def talk_overs(self) -> list[dict]:
        """Replies whose audio started while the user was still speaking (a premature end of turn)."""

        out = []
        for r in self.replies():
            fp = r["first_played"]
            if fp is None:
                continue
            for u in self.utterances:
                if u.role != "backchannel" and u.t_voice_start is not None and u.t_voice_end is not None \
                        and u.t_voice_start < fp < u.t_voice_end:
                    out.append({"id": r["id"], "utterance": u.index, "early_ms": _ms(u.t_voice_end - fp),
                                "text": r["text"]})
        return out

    def repeated_calls(self) -> list[dict]:
        seen, dups = [], []
        for c in self.tool_calls():
            if any(_same_call(c, s) for s in seen):
                dups.append(c)
            seen.append(c)
        return dups

    def tasks(self) -> list[dict]:
        by: dict[Any, dict] = {}
        for e in self.events("task"):
            d = e.get("task") or {}
            if d.get("id") is None:
                continue
            row = by.setdefault(d["id"], {"id": d["id"], "t_started": e["mono"], "t_finished": None})
            row.update(kind=d.get("kind"), goal=d.get("goal", ""), status=d.get("status"),
                       result=d.get("result", ""), steps=list(d.get("steps") or []))
            if d.get("status") in FINISHED and row["t_finished"] is None:
                row["t_finished"] = e["mono"]
            s, f = d.get("started"), d.get("finished")
            row["duration_s"] = round(f - s, 2) if _num(s) and _num(f) else None
        for row in by.values():
            row["step_ms"] = [st.get("ms") for st in row["steps"] if isinstance(st, dict) and _num(st.get("ms"))]
        return sorted(by.values(), key=lambda r: r["t_started"])

    def notify_latencies(self) -> list[float]:
        out = []
        for n in self.events("eval.notify"):
            after = [r["first_played"] for r in self.replies()
                     if r["first_played"] is not None and r["first_played"] >= n["mono"]]
            if after:
                out.append(_ms(min(after) - n["mono"]))
        return out

    def metrics(self) -> dict:
        tasks = [{k: v for k, v in t.items() if k != "steps"} | {"n_steps": len(t["steps"])}
                 for t in self.tasks()]
        return {
            "turns": self.turns(),
            "barges": self.barges(),
            "unprompted": [{"id": r["id"], "text": r["text"]} for r in self.unprompted()],
            "repeated_calls": [{"name": c.get("name"), "arguments": c.get("arguments")}
                               for c in self.repeated_calls()],
            "talk_overs": self.talk_overs(),
            "tool_calls": [{"name": c.get("name"), "arguments": c.get("arguments")} for c in self.tool_calls()],
            "tasks": tasks,
            "notify_to_audio_ms": self.notify_latencies(),
        }


def evaluate(c: Check, ctx: Context, prefix: str = "") -> dict:
    try:
        out = c.fn(ctx)
    except Exception as exc:
        ok, detail = False, f"check raised {type(exc).__name__}: {exc}"
    else:
        ok, detail = out if isinstance(out, tuple) else (out, "")
    return {"name": prefix + c.name, "ok": bool(ok), "soft": c.soft, "detail": str(detail)}


# ------------------------------------------------------------------ harness
class Transcriber:
    """Parakeet over the client's played audio: did the speakers say what the text events claim?"""

    def __init__(self, runtime: Any):
        from rose.runtime import BACKGROUND

        def load():
            from mlx_audio.stt.utils import load_model

            return load_model(PARAKEET)

        self.runtime, self.priority = runtime, BACKGROUND
        self.model = runtime.run(load, priority=BACKGROUND, name="eval-parakeet", timeout=300)

    def __call__(self, audio24: np.ndarray) -> str:
        from scipy.signal import resample_poly

        audio = resample_poly(np.asarray(audio24, np.float32), 2, 3).astype(np.float32)
        texts = []
        for chunk in _split_quiet(audio, MIC_SR, max_s=20.0):
            if len(chunk) < MIC_SR // 4:
                continue

            def run(chunk=chunk):
                import mlx.core as mx

                return self.model.generate(mx.array(chunk)).text

            texts.append(self.runtime.run(run, priority=self.priority, name="eval-transcribe", timeout=120))
        return " ".join(t.strip() for t in texts if t)


def _split_quiet(audio: np.ndarray, sr: int, max_s: float) -> list[np.ndarray]:
    """Chunks of at most ``max_s``, cut at the quietest 50 ms in each chunk's last quarter."""

    out, n, win = [], int(max_s * sr), sr // 20
    while len(audio) > n:
        tail = audio[int(0.75 * n) : n]
        k = len(tail) // win
        energy = (tail[: k * win].reshape(k, win) ** 2).mean(axis=1)
        cut = int(0.75 * n) + int(np.argmin(energy)) * win + win // 2
        out.append(audio[:cut])
        audio = audio[cut:]
    out.append(audio)
    return out


def _describe(step: Any) -> str:
    match step:
        case Say(text=text):
            return f"say {text!r}"
        case SayWhileRoseSpeaks(text=text, after_ms=after, role=role):
            return f"{role} {text!r} after {after} ms of Rose audio"
        case Wait(seconds=s):
            return f"wait {s:g} s"
        case WaitFor(kind=kind, timeout=t):
            return f"wait_for {kind} ({t:g} s)"
        case WaitIdle():
            return "wait until Rose is quiet"
        case Notify(app=app, title=title):
            return f"notify {app} from {title}"
        case Mark(name=name):
            return f"mark {name}"
        case Settle(quiet=q):
            return f"settle ({q:g} s quiet)"
        case Expect(check=c):
            return f"expect {c.name}"
    return repr(step)


class Harness:
    """Performs scenarios against a running pipeline and judges them."""

    def __init__(self, pipe: Pipeline, synth: Synth, user_voice: str | None = None,
                 transcriber: Transcriber | None = None, log: Callable[[str], None] = print):
        self.pipe, self.synth, self.user_voice, self.transcriber, self.log = pipe, synth, user_voice, transcriber, log
        self.trace = pipe.trace
        self.feeder = MicFeeder(pipe.transport.mic, pipe.trace, pipe.playback)
        self.utterances: list[UserUtterance] = []
        self.marks: dict[str, float] = {}
        self._start = self._step_start = time.monotonic()

    def start(self) -> None:
        self.feeder.start()

    def stop(self) -> None:
        self.feeder.stop()

    def voice(self, step: Say | SayWhileRoseSpeaks) -> str:
        return self.user_voice or step.voice

    def prepare(self, scenarios: list[Scenario]) -> None:
        """Synthesize every utterance up front (fails fast on a missing voice)."""

        for sc in scenarios:
            for step in sc.steps:
                if isinstance(step, (Say, SayWhileRoseSpeaks)):
                    self.synth.get(step.text, self.voice(step))

    def warmup(self) -> dict:
        sc = Scenario("warmup", "first-turn compile and cache warm-up", [say("Hello there.")],
                      settle=Settle(quiet=1.5, timeout=40.0))
        record, _ = self.run(sc)
        return record

    # -------------------------------------------------------------- run
    def run(self, sc: Scenario, rep: int = 0) -> tuple[dict, list[dict]]:
        self.pipe.reset()
        self._settle(Settle(quiet=1.0, timeout=30.0))
        self.utterances, self.marks = [], {}
        with getattr(self.pipe.phone, "_lock", None) or contextlib.nullcontext():
            phone_start = copy.deepcopy(self.pipe.phone.data)
        played0 = self.pipe.played_samples()
        self.feeder.reset_stats()
        self._start = start = time.monotonic()
        self.trace.add("eval.scenario", name=sc.name, rep=rep)
        results: list[dict] = []
        for i, step in enumerate(sc.steps, 1):
            self._step_start = time.monotonic()
            self.trace.add("eval.step", n=i, step=_describe(step))
            try:
                problem = self._do(step, i, phone_start, results)
            except Exception as exc:
                traceback.print_exc()
                problem = f"error: {type(exc).__name__}: {exc}"
            if problem:
                results.append({"name": f"step {i}: {_describe(step)}", "ok": False, "soft": False,
                                "detail": problem})
                break
        unsettled = self._settle(sc.settle)
        end = time.monotonic()
        self.trace.add("eval.end", name=sc.name, rep=rep)
        ctx = Context(self.trace.window(start, end), self.utterances, self.marks, self.pipe.phone,
                      phone_start, start, end)
        results += [evaluate(c, ctx) for c in sc.checks]
        if unsettled:
            results.append({"name": "settled at the end", "ok": False, "soft": True, "detail": unsettled})
        transcript = None
        if self.transcriber is not None:
            transcript = self._transcribe(ctx, played0, results)
        record = {
            "name": sc.name, "rep": rep, "about": sc.about,
            "passed": all(r["ok"] for r in results if not r["soft"]),
            "duration_s": round(end - start, 1),
            "checks": results,
            "metrics": ctx.metrics(),
            "utterances": [u.to_dict(start) for u in self.utterances],
            "user_turns": [{"id": e.get("id"), "text": e.get("text"), "at_s": round(e["mono"] - start, 2)}
                           for e in ctx.events("user_turn")],
            "rose": [{"id": r["id"], "text": r["text"], "interrupted": r["interrupted"],
                      "unfinished": r["unfinished"], "at_s": round(r["start"] - start, 2)}
                     for r in ctx.rose_turns()],
            "feeder": self.feeder.stats(),
            "played_transcript": transcript,
        }
        return record, ctx.all_events

    def _do(self, step: Any, n: int, phone_start: dict, results: list[dict]) -> str | None:
        match step:
            case Say():
                return self._say(step.text, self.voice(step), "turn", step.trail_ms)
            case SayWhileRoseSpeaks():
                return (self._wait_rose(step.after_ms / 1000, step.timeout)
                        or self._say(step.text, self.voice(step), step.role, step.trail_ms))
            case Wait():
                time.sleep(step.seconds)
            case WaitFor():
                return self._wait_for(step)
            case WaitIdle():
                return self._wait_idle(step)
            case Notify():
                notify = getattr(self.pipe.phone, "notify", None)
                if notify is None:
                    return "the phone has no notify()"
                self.trace.add("eval.notify", app=step.app, title=step.title, text=step.text)
                notify(step.app, step.title, step.text)
            case Mark():
                self.marks[step.name] = self.trace.add("eval.mark", name=step.name)["mono"]
            case Settle():
                return self._settle(step)
            case Expect():
                now = time.monotonic()
                ctx = Context(self.trace.window(self._start, now), self.utterances, self.marks,
                              self.pipe.phone, phone_start, self._start, now)
                results.append(evaluate(step.check, ctx, prefix=f"at step {n}: "))
            case _:
                raise TypeError(f"unknown step {step!r}")
        return None

    def _say(self, text: str, voice: str, role: str, trail_ms: int) -> str | None:
        audio, onset, offset = self.synth.get(text, voice)
        audio = np.concatenate([audio, np.zeros(int(trail_ms * MIC_SR / 1000), np.float32)])
        u = UserUtterance(len(self.utterances), text, voice, role, len(audio) / MIC_SR, time.monotonic())
        self.utterances.append(u)
        self.feeder.speak(u, audio, onset, offset)
        if not u.done.wait(u.audio_s + 10.0):
            return "the mic feeder stalled"
        return None

    def _anchor(self, since: str) -> float:
        if since == "say":
            u = self.utterances[-1] if self.utterances else None
            return (u.t_voice_start or u.t_first_fed or u.t_queued) if u else self._start
        if since == "step":
            return self._step_start
        if since == "start":
            return self._start
        if since in self.marks:
            return self.marks[since]
        raise KeyError(f"unknown anchor {since!r} (say, step, start or a mark name)")

    def _wait_rose(self, after_s: float, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pb = self.pipe.playback
            since = pb.since
            if pb.playing and since is not None and time.monotonic() - since >= after_s:
                return None
            time.sleep(0.005)
        return f"Rose did not speak for {after_s:g} s straight within {timeout:g} s"

    def _wait_for(self, step: WaitFor) -> str | None:
        anchor = self._anchor(step.since)
        deadline = time.monotonic() + step.timeout
        while time.monotonic() < deadline:
            if self.trace.find(step.kind, anchor, step.pred) is not None:
                return None
            time.sleep(0.02)
        return f"no matching {step.kind} event within {step.timeout:g} s"

    def _wait_idle(self, step: WaitIdle) -> str | None:
        anchor = self._anchor("say")
        deadline = time.monotonic() + step.timeout
        while time.monotonic() < deadline:
            pb = self.pipe.playback
            spoke = self.trace.find("eval.send", anchor) is not None
            if spoke and not pb.playing and time.monotonic() - pb.changed >= step.quiet_ms / 1000:
                return None
            time.sleep(0.01)
        return f"Rose did not finish speaking within {step.timeout:g} s"

    def _settle(self, s: Settle) -> str | None:
        deadline = time.monotonic() + s.timeout
        while True:
            now = time.monotonic()
            reason = "user speech queued" if self.feeder.busy else self.pipe.busy()
            if reason is None and now - self.trace.last_activity >= s.quiet:
                return None
            if now >= deadline:
                return f"not quiet after {s.timeout:g} s ({reason or 'events still arriving'})"
            time.sleep(0.05)

    def _transcribe(self, ctx: Context, played0: int, results: list[dict]) -> str | None:
        try:
            audio = self.pipe.transport.played_audio()[played0:]
            text = self.transcriber(audio) if len(audio) >= OUT_SR // 4 else ""
        except Exception as exc:
            results.append({"name": "played audio transcribed", "ok": False, "soft": True,
                            "detail": f"{type(exc).__name__}: {exc}"})
            return None
        ref = words(speakable(" ".join(r["heard"] if r["heard"] is not None else r["text"]
                                        for r in ctx.rose_turns())))
        hyp = set(words(speakable(text)))
        recall = sum(w in hyp for w in ref) / len(ref) if ref else float(not hyp)
        results.append({"name": "played audio matches Rose's text (word recall >= 0.7)", "ok": recall >= 0.7,
                        "soft": True, "detail": f"recall {recall:.2f}; heard by ASR: {clip(text)}"})
        return text


# ------------------------------------------------------------------ reports
def _json_default(o: Any) -> Any:
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return f"<array {o.shape}>"
    if hasattr(o, "to_dict"):
        return o.to_dict()
    return str(o)


def write_events(path: Path, events: list[dict], start: float) -> None:
    with path.open("w") as f:
        for e in events:
            f.write(json.dumps({"t_rel": round(e["mono"] - start, 4), **e}, default=_json_default) + "\n")


def _pct(xs: list[float], q: float) -> float | None:
    return round(float(np.percentile(xs, q)), 1) if xs else None


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.0f}" if abs(v) >= 10 else f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


def summary_markdown(records: list[dict], meta: dict) -> str:
    out = [f"# Rose v3 eval, {meta['started']}", ""]
    passed = sum(r["passed"] for r in records)
    out += [f"**{passed}/{len(records)} scenario runs passed** (hard checks; soft checks are targets). "
            f"Rose voice `{meta['voice']}`, user voice `{meta['user_voice'] or 'per step (Samantha)'}`, "
            f"model load {meta.get('load_s', {})} s.", ""]
    out += ["| Scenario | Rep | Result | Hard | Soft | Voice-to-voice ms | Failed hard checks |",
            "|---|---|---|---|---|---|---|"]
    for r in records:
        hard = [c for c in r["checks"] if not c["soft"]]
        soft = [c for c in r["checks"] if c["soft"]]
        v2v = [t["voice_to_voice_ms"] for t in r["metrics"]["turns"] if t.get("voice_to_voice_ms") is not None]
        failed = "; ".join(c["name"] for c in hard if not c["ok"])
        out.append(f"| {r['name']} | {r['rep'] + 1} | {'PASS' if r['passed'] else 'FAIL'} | "
                   f"{sum(c['ok'] for c in hard)}/{len(hard)} | {sum(c['ok'] for c in soft)}/{len(soft)} | "
                   f"{', '.join(_fmt(v) for v in v2v) or '-'} | {failed} |")

    turns = [t for r in records for t in r["metrics"]["turns"]]
    keys = [k for k in LATENCY_ORDER if any(_num(t.get(k)) for t in turns)]
    keys += sorted({k for t in turns for k, v in t.items() if k.endswith("_ms") and _num(v)} - set(keys))
    out += ["", "## Latency, all user turns", "",
            "Voice-to-voice: last voiced mic frame fed -> first Rose audio played at the fake client. "
            "`conductor_v2v_ms` is the conductor's own measure (listener's end-of-speech estimate -> "
            "first audio sent).", "", "| Metric | n | p50 | p90 | max |", "|---|---|---|---|---|"]
    for k in keys:
        xs = [float(t[k]) for t in turns if _num(t.get(k))]
        out.append(f"| {k} | {len(xs)} | {_fmt(_pct(xs, 50))} | {_fmt(_pct(xs, 90))} | {_fmt(max(xs))} |")
    wers = [t["asr_wer"] for t in turns if _num(t.get("asr_wer"))]
    if wers:
        out.append(f"\nASR word error rate vs the script: mean {np.mean(wers):.3f} over {len(wers)} turns.")

    barges = [(r["name"], b) for r in records for b in r["metrics"]["barges"]]
    if barges:
        out += ["", "## Talking over Rose", "",
                "| Scenario | Role | Utterance | Rose audible | Duck ms | Stop ms | Resumed | Heard |",
                "|---|---|---|---|---|---|---|---|"]
        for name, b in barges:
            out.append(f"| {name} | {b['role']} | {b['user_text']} | {b['rose_playing']} | {_fmt(b['duck_ms'])} | "
                       f"{_fmt(b['stop_ms'])} | {b['resumed']} | {clip(b.get('heard') or '', 60)} |")
        stops = [b["stop_ms"] for _, b in barges if b["role"] == "barge" and _num(b["stop_ms"])]
        ducks = [b["duck_ms"] for _, b in barges if _num(b["duck_ms"])]
        out.append(f"\nBarge-in stop p50 {_fmt(_pct(stops, 50))} / p90 {_fmt(_pct(stops, 90))} ms; "
                   f"duck p50 {_fmt(_pct(ducks, 50))} / p90 {_fmt(_pct(ducks, 90))} ms.")

    tasks = [(r["name"], t) for r in records for t in r["metrics"]["tasks"]]
    if tasks:
        out += ["", "## Background tasks", "",
                "| Scenario | Task | Status | Duration s | Steps | Step ms p50 | Goal | Result |",
                "|---|---|---|---|---|---|---|---|"]
        for name, t in tasks:
            out.append(f"| {name} | {t['kind']} #{t['id']} | {t['status']} | {_fmt(t.get('duration_s'))} | "
                       f"{t['n_steps']} | {_fmt(_pct(t['step_ms'], 50))} | {clip(t['goal'], 60)} | "
                       f"{clip(t['result'], 60)} |")

    unprompted = sum(len(r["metrics"]["unprompted"]) for r in records)
    repeated = sum(len(r["metrics"]["repeated_calls"]) for r in records)
    talk_over = sum(len(r["metrics"]["talk_overs"]) for r in records)
    notify = [v for r in records for v in r["metrics"]["notify_to_audio_ms"]]
    out += ["", "## Behaviour", "", f"- Unprompted Rose turns: {unprompted}",
            f"- Repeated tool calls: {repeated}",
            f"- Rose started talking while the user was still speaking: {talk_over}",
            f"- Notification -> first audio: {', '.join(_fmt(v) for v in notify) or '-'} ms",
            f"- Mic feeder: max lateness {max((r['feeder']['max_late_ms'] for r in records), default=0):.0f} ms, "
            f"{sum(r['feeder']['late_frames'] for r in records)} frames more than one frame (32 ms) late"]

    out += ["", "## Checks", ""]
    for r in records:
        out.append(f"### {r['name']} (run {r['rep'] + 1}): {'PASS' if r['passed'] else 'FAIL'}")
        out.append(f"{r['about']}  ")
        out.append(f"User: {' / '.join(u['text'] for u in r['utterances']) or '(nothing)'}  ")
        out.append(f"Rose: {' / '.join(clip(x['text'], 120) for x in r['rose']) or '(nothing)'}")
        out.append("")
        for c in r["checks"]:
            mark = "x" if c["ok"] else " "
            out.append(f"- [{mark}] {c['name']}{' (soft)' if c['soft'] else ''}"
                       + (f": {c['detail']}" if c["detail"] else ""))
        out.append("")
    return "\n".join(out)


def write_reports(out: Path, records: list[dict], meta: dict) -> None:
    (out / "results.json").write_text(json.dumps({"meta": meta, "scenarios": records}, indent=1,
                                                 default=_json_default))
    (out / "summary.md").write_text(summary_markdown(records, meta))


# ---------------------------------------------------------------------- cli
def select(only: str) -> list[Scenario]:
    if not only.strip():
        return list(SCENARIOS)
    names = [n.strip() for n in only.split(",") if n.strip()]
    unknown = [n for n in names if n not in BY_NAME]
    if unknown:
        raise SystemExit(f"unknown scenario(s): {', '.join(unknown)}; known: {', '.join(BY_NAME)}")
    return [BY_NAME[n] for n in names]


def main(argv: list[str] | None = None, parts: SimpleNamespace | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rose.eval.run", description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default="", help="comma-separated scenario names (default: all)")
    ap.add_argument("--repeat", type=int, default=1, help="run the selection N times")
    ap.add_argument("--out", default=str(ROOT / "out" / "eval_v3"), help="output directory")
    ap.add_argument("--voice", default="zoey", help="Rose's TTS voice")
    ap.add_argument("--user-voice", default=None, help="macOS voice for every user utterance")
    ap.add_argument("--transcribe", action="store_true", help="spot-check played audio with Parakeet")
    ap.add_argument("--no-warmup", action="store_true", help="skip the untimed warm-up turn")
    ap.add_argument("--no-keepalive", action="store_true", help="no GPU keepalive (the app has one by default)")
    ap.add_argument("--list", action="store_true", help="list scenarios and exit")
    args = ap.parse_args(argv)

    if args.list:
        for sc in SCENARIOS:
            print(f"{sc.name:22s} {sc.about}")
        return 0
    chosen = select(args.only)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log = lambda msg: print(f"[eval {time.strftime('%H:%M:%S')}] {msg}", flush=True)  # noqa: E731

    synth = Synth(out / "_say_cache")
    for sc in chosen:  # fail fast, before loading ~25 GB of models
        for step in sc.steps:
            if isinstance(step, (Say, SayWhileRoseSpeaks)):
                synth.get(step.text, args.user_voice or step.voice)
    try:
        pipe = build_pipeline(args.voice, parts, keepalive=not args.no_keepalive, log=log)
    except MissingPieces as exc:
        print(exc, file=sys.stderr)
        return 2
    meta = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "voice": args.voice, "user_voice": args.user_voice,
            "repeat": args.repeat, "scenarios": [s.name for s in chosen], "load_s": pipe.load_s,
            "keepalive": pipe.keepalive is not None}
    transcriber = Transcriber(pipe.runtime) if args.transcribe else None
    harness = Harness(pipe, synth, args.user_voice, transcriber, log)
    harness.start()
    records: list[dict] = []
    try:
        if not args.no_warmup:
            w = harness.warmup()
            log(f"warm-up: Rose said {' / '.join(x['text'] for x in w['rose']) or '(nothing)'}")
        total = len(chosen) * args.repeat
        for rep in range(args.repeat):
            for sc in chosen:
                record, events = harness.run(sc, rep)
                stem = sc.name if args.repeat == 1 else f"{sc.name}.r{rep + 1}"
                write_events(out / f"{stem}.jsonl", events, harness._start)
                records.append(record)
                write_reports(out, records, meta)
                v2v = [t.get("voice_to_voice_ms") for t in record["metrics"]["turns"]]
                bad = [c["name"] for c in record["checks"] if not c["ok"] and not c["soft"]]
                log(f"[{len(records)}/{total}] {sc.name}: {'PASS' if record['passed'] else 'FAIL'}"
                    f"  v2v {v2v} ms" + (f"  failed: {'; '.join(bad)}" if bad else ""))
    except KeyboardInterrupt:
        log("interrupted; writing what ran")
    finally:
        if records:
            meta["runtime"] = pipe.runtime.stats() if hasattr(pipe.runtime, "stats") else {}
            write_reports(out, records, meta)
        harness.stop()
        pipe.close()
    log(f"{sum(r['passed'] for r in records)}/{len(records)} passed; reports in {out}")
    return 0 if records and all(r["passed"] for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
