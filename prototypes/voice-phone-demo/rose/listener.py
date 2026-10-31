"""Mic stream -> speech events and committed user turns.

Turn taking lives or dies on two numbers: how fast a finished sentence is
committed, and how rarely a thinking pause is mistaken for the end. So the
listener layers cheap signals and only pays for the expensive ones at pauses:

- Silero VAD on every 32 ms frame, on this module's own thread in numpy
  (0.15 ms), so the GPU scheduler can never delay speech detection. Not MLX
  on a CPU stream: MLX's default device is process-global, so entering
  ``mx.stream(cpu)`` here silently moved the runtime thread's unpinned work
  to the CPU (Parakeet 30 -> 120 ms).
- Nemotron cache-aware streaming ASR (one runtime job, LISTEN priority)
  turns speech into live partials for the dashboard and barge-in decisions.
- At a pause candidate (200 ms below threshold): Parakeet transcribes the
  whole utterance (more accurate than the streaming text) and smart-turn
  judges whether the audio sounds finished. The commit rule leans early,
  because the conductor merges a turn that resumes before Rose has really
  spoken, while dead air costs on every turn:
    ends on "and", "the", "to", a comma ...  -> wait 2.0 s of silence
    smart-turn says finished, or a question  -> commit now
    ends on "could", "I", "was" ...          -> wait 1.2 s
    anything else                            -> commit after 0.5 s of silence
  Speech resuming before the commit continues the same turn.
- Parakeet starts early, 64 ms into a dip, so only smart-turn (3-5 ms) is
  left on the critical path; speech before the pause candidate discards that
  run. Streaming steps are held during a dip so they never queue ahead of the
  end-of-turn jobs (same priority, round robin).

Every endpointing timer counts audio samples, never wall time, so tests can
feed audio faster than real time and get the same decisions. While a GPU
job decides a pause, the listener thread blocks on it; incoming audio queues
up and is caught up afterwards (a commit is at most one job late).
"""

from __future__ import annotations

import collections
import queue
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable

import mlx.core as mx
import numpy as np

from .bus import Bus
from .runtime import LISTEN, Job, Runtime, Sleep
from .textnorm import speakable

SR = 16_000
FRAME = 512  # Silero's window at 16 kHz: 32 ms

VAD_REPO = "mlx-community/silero-vad"
TURN_REPO = "mlx-community/smart-turn-v3"
FINAL_ASR_REPO = "mlx-community/parakeet-tdt-0.6b-v3"
STREAM_ASR_REPO = "mlx-community/nemotron-3.5-asr-streaming-0.6b"
# This checkpoint is trained for look-aheads of 0, 3, 6 and 13 encoder frames
# (80 ms each) and ignores transcription_delay_ms. 0 (80 ms chunks) shows the
# first word ~0.15 s sooner than 3 but costs ~23 % GPU while the user speaks
# (one ~20 ms unit per 80 ms) against ~9 % for 3; the final text is Parakeet's.
STREAM_ATT_CONTEXT = [56, 0]
STREAM_IDLE_S = 0.03
STREAM_MAX_QUEUED = 20 * SR  # the session refuses more than 30 s of input
STREAM_HOLD_FRAMES = 10  # silence kept for the streaming session across a held dip

# Words that almost never end a finished turn.
DANGLING = frozenset("""
    and but or nor um uh er erm the a an to of with because like if my your our their
    for from into than as just very really about also whose whether although though
""".split())
# Words that often mean more is coming but can end a sentence ("I think so", "yes I can").
MAYBE_DANGLING = frozenset("""
    so that then its i i'm i've i'll i'd we we're we'll we'd you're they they're he she
    he's she's it's there's that's could would should can will was were is are am be been
    being have has had do does did gonna wanna gotta when where while which who since until
""".split())
_WORD = re.compile(r"[a-z']+")
_LETTER = re.compile(r"[^\W\d_]")
# Faster than anyone talks: Parakeet sometimes adds words to a short clip
# ("Right," became "Right, I'm not sure."; "Yes, Sam." became "Sam, he said.",
# both 8.5+ syllables/s). Real turns in the eval topped out at 6.3.
MAX_SYLLABLES_PER_S = 7.5


def dangling_level(text: str) -> int:
    """2 when ``text`` stops mid-thought (trailing comma, "and", "to", ...),
    1 when it may continue ("could", "I", "was"), else 0."""

    text = text.strip()
    if not text or text.endswith("?"):
        return 0
    if text.endswith((",", ";", ":", "-", "—", "…", "...")):
        return 2
    words = re.findall(r"[a-z0-9:']+", text.lower().replace("’", "'"))
    if not words:
        return 0
    if words[-1] in DANGLING:
        return 2
    return 1 if words[-1] in MAYBE_DANGLING else 0


PREAMBLE = frozenset("hi hey hello yo rose okay ok so well um uh alright right oh and".split())


# "Hi Rose," as the recognizer sometimes writes it, plus one stray word ("High rose, said").
_GREETING = re.compile(r"^\W*(hi|hey|high|hay|hello|yo|okay|ok)\W+rose\b", re.I)


def is_preamble(text: str) -> bool:
    """Greetings and openers ("Hi Rose.", "Okay so") that usually lead into more."""

    words = _WORD.findall(text.lower().replace("’", "'"))
    return (0 < len(words) <= 3 and (set(words) <= PREAMBLE or bool(_GREETING.match(text)))
            and not text.strip().endswith("?"))


def syllables(text: str) -> int:
    """A rough syllable count: vowel groups, less silent endings ("time", "minutes",
    "played"). Numbers count as said ("10,000" = "ten thousand") or a syllable a
    digit ("715" = "seven fifteen"), whichever is fewer."""

    text = re.sub(r"(\d):00\b", r"\1", text)  # "10:00" is usually said "ten"
    return min(_vowel_groups(text), _vowel_groups(speakable(text)))


def _vowel_groups(text: str) -> int:
    n = 0
    for w in re.findall(r"[a-z']+|\d", text.lower().replace("’", "'")):
        if w.isdigit():
            n += 1
            continue
        groups = len(re.findall(r"[aeiouy]+", w))
        if groups > 1 and (
                (w.endswith("e") and not w.endswith(("le", "ee", "ye")))
                or (w.endswith("es") and not w.endswith(("ses", "zes", "ces", "ges", "shes", "ches", "xes")))
                or (w.endswith("ed") and not w.endswith(("ted", "ded")))):
            groups -= 1
        n += max(groups, 1)
    return n


def too_fast(text: str, seconds: float) -> bool:
    """More words than ``seconds`` of speech can hold: the transcript has invented some."""

    n = syllables(text)
    return n >= 3 and n > MAX_SYLLABLES_PER_S * max(seconds, 0.2)


def is_dangling(text: str) -> bool:
    return dangling_level(text) > 0


def has_words(text: str) -> bool:
    return bool(_LETTER.search(text))


class SileroCPU:
    """Silero VAD's 16 kHz branch in numpy, from the loaded mlx-audio model.

    One frame is a 4-column STFT, four small convolutions and one LSTM step,
    so numpy matches MLX's speed and keeps MLX off the listener thread.
    """

    def __init__(self, model):
        branch = model.vad_16k
        cfg = branch.config
        self.cutoff, self.pad, self.context = cfg.cutoff, cfg.pad, cfg.context_size
        self.filter, self.hop = cfg.filter_length, cfg.hop_length
        self.stft = _np(branch.stft_conv.weight)[:, :, 0].T  # (filter, 2 * cutoff)
        self.convs = []
        for conv, stride in ((branch.conv1, 1), (branch.conv2, 2), (branch.conv3, 2), (branch.conv4, 1)):
            weight = _np(conv.weight)  # (out, k, in)
            self.convs.append((weight.reshape(weight.shape[0], -1).T, _np(conv.bias), weight.shape[1], stride))
        self.wx, self.wh, self.bias = _np(branch.lstm.Wx).T, _np(branch.lstm.Wh).T, _np(branch.lstm.bias)
        self.wf, self.bf = _np(branch.final_conv.weight)[:, 0, :].T, _np(branch.final_conv.bias)
        self.reset()

    def reset(self) -> None:
        hidden = self.wh.shape[0]
        self.ctx = np.zeros(self.context, np.float32)
        self.h = np.zeros(hidden, np.float32)
        self.c = np.zeros(hidden, np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = np.concatenate([self.ctx, frame])
        self.ctx = frame[-self.context:].copy()
        x = np.concatenate([x, x[-2:-self.pad - 2:-1]])  # reflect pad on the right
        windows = np.lib.stride_tricks.sliding_window_view(x, self.filter)[::self.hop]
        spec = windows @ self.stft
        y = np.sqrt(spec[:, :self.cutoff] ** 2 + spec[:, self.cutoff:] ** 2)
        for weight, bias, k, stride in self.convs:
            padded = np.pad(y, ((k // 2, k // 2), (0, 0)))
            cols = np.lib.stride_tricks.sliding_window_view(padded, k, axis=0)[::stride]
            y = np.maximum(cols.transpose(0, 2, 1).reshape(cols.shape[0], -1) @ weight + bias, 0.0)
        out = []
        for step in y @ self.wx + self.bias:
            i, f, g, o = np.split(step + self.h @ self.wh, 4)
            self.c = _sigmoid(f) * self.c + _sigmoid(i) * np.tanh(g)
            self.h = _sigmoid(o) * np.tanh(self.c)
            out.append(self.h)
        logits = np.maximum(np.stack(out), 0.0) @ self.wf + self.bf
        return float(_sigmoid(logits).mean())


@dataclass
class Endpointing:
    start_p: float = 0.5
    start_frames: int = 2
    start_p_audible: float = 0.6  # echo guard while Rose is playing
    start_frames_audible: int = 3
    pause_p: float = 0.35
    pause_s: float = 0.2
    early_asr_s: float = 0.064  # start the final transcript this far into a dip
    preroll_s: float = 0.3
    turn_p: float = 0.5
    turn_p_maybe: float = 0.9  # after "could", "I", "was": smart-turn must be sure
    commit_s: float = 1.2           # may continue ("could", "I", ...)
    dangling_commit_s: float = 2.0  # stops mid-thought ("and", "the", ...)
    short_commit_s: float = 0.5     # no strong signal either way
    preamble_commit_s: float = 0.7  # "Hi Rose." alone: give them a moment to go on
    turn_audio_s: float = 8.0  # smart-turn looks at the last 8 s
    event_interval_s: float = 0.1  # vad and partial events: at most 10/s


@dataclass
class ListenEvent:
    kind: str  # "speech_start" | "partial" | "speech_pause" | "speech_resume" | "turn_end" | "noise"
    text: str = ""
    t: float = 0.0  # monotonic time of the event
    speech_ms: float = 0.0  # voiced audio in the current utterance
    data: dict = field(default_factory=dict)  # turn_end: endpoint_ms, asr_ms, smart_turn_p


@dataclass
class _Utterance:
    id: int
    start: int  # first voiced sample (audio time)
    frames: list[np.ndarray]
    voiced: int  # voiced samples
    voice_end: int  # sample after the last voiced frame
    low: int = 0  # samples below the pause threshold
    paused: bool = False
    commit_at: int | None = None  # sample at which a pending pause commits
    text: str = ""  # final transcript at the current pause
    turn_p: float = 0.0
    asr_ms: float = 0.0
    turn_ms: float = 0.0
    wait_ms: float = 0.0  # time the decision held up the listener
    asr_job: Job | None = None  # early final transcript for the current dip
    dangling: bool = False
    dangling_level: int = 0
    partial: str = ""
    pauses: int = 0

    def audio(self) -> np.ndarray:
        if len(self.frames) > 1:
            self.frames = [np.concatenate(self.frames)]
        return self.frames[0]


class Listener:
    def __init__(self, runtime: Runtime, bus: Bus, on_event: Callable[[ListenEvent], None],
                 config: Endpointing | None = None):
        self.runtime = runtime
        self.bus = bus
        self.on_event = on_event
        self.cfg = config or Endpointing()
        self.vad_p = 0.0
        self.in_speech = False
        self.utterance_started_at: float | None = None
        self.rose_audible = False
        self.heard_samples = 0  # audio processed so far (audio clock)
        self.vad_ms: collections.deque[float] = collections.deque(maxlen=500)
        self.early_asr = collections.Counter()  # early final-ASR runs: used / discarded

        self._vad = self._turn = self._final = self._stream = None
        self._q: queue.SimpleQueue[np.ndarray | None] = queue.SimpleQueue()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._job = None

        # Shared with the streaming ASR job, under _lock.
        self._lock = threading.Lock()
        self._asr_utt = 0
        self._asr_hold = False  # a pause decision is in flight: no streaming steps
        self._asr_audio: list[np.ndarray] = []
        self._asr_text: tuple[int, str] = (0, "")

        # Listener thread only.
        self._rest = np.zeros(0, np.float32)
        self._recent: collections.deque[np.ndarray] = collections.deque(
            maxlen=int(np.ceil(self.cfg.preroll_s * SR / FRAME)) + 4)
        self._hot = 0
        self._utt: _Utterance | None = None
        self._next_id = 0
        self._vad_event_at = 0.0
        self._partial_at = 0.0

    # ------------------------------------------------------------ lifecycle
    def load(self) -> None:
        def load_models():
            from mlx_audio.stt.utils import load_model
            from mlx_audio.vad import load as load_vad

            vad, turn = SileroCPU(load_vad(VAD_REPO)), load_vad(TURN_REPO)
            final, stream = load_model(FINAL_ASR_REPO), load_model(STREAM_ASR_REPO)
            stream.default_att_context_size = list(STREAM_ATT_CONTEXT)
            # Compile kernels now so the first turn is not the slow one.
            noise = (np.random.default_rng(0).standard_normal(SR) * 0.01).astype(np.float32)
            turn.predict_endpoint(noise, sample_rate=SR)
            final.generate(mx.array(noise))
            session = stream.create_streaming_session(language="en")
            session.feed(noise)
            for _ in range(16):
                session.step(max_decode_tokens=8)
            session.cancel()
            return vad, turn, final, stream

        self._vad, self._turn, self._final, self._stream = self.runtime.run(
            load_models, priority=LISTEN, name="listener-load", timeout=600)

    def start(self) -> None:
        if self._vad is None:
            raise RuntimeError("Listener.load() first")
        if self._thread is not None and self._thread.is_alive() and not self._stopping.is_set():
            return
        if self._thread is not None or self._stopping.is_set():
            # Restart: a fresh flag and queue. A stop sentinel (or audio) left queued
            # by stop() would end the new thread at once, and a thread still finishing
            # a decision after stop() must exit rather than share the new queue.
            self._stopping, self._q = threading.Event(), queue.SimpleQueue()
        self._job = self.runtime.submit(self._asr_loop, priority=LISTEN, name="stream-asr")
        self._thread = threading.Thread(target=self._run, args=(self._stopping, self._q),
                                        name="listener", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._q.put(None)
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._job is not None:
            self._job.cancel()

    def push(self, samples: np.ndarray) -> None:
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if samples.size:
            # One NaN would poison the VAD's LSTM state for good (and the streaming session).
            self._q.put(np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0))

    def set_rose_audible(self, audible: bool) -> None:
        self.rose_audible = bool(audible)

    def stats(self) -> dict:
        ms = sorted(self.vad_ms)
        return {
            "vad_ms_p50": round(ms[len(ms) // 2], 3) if ms else None,
            "vad_ms_p95": round(ms[int(0.95 * (len(ms) - 1))], 3) if ms else None,
            "heard_s": round(self.heard_samples / SR, 2),
            "stream_asr_busy_s": round(self._job.busy_s, 2) if self._job else None,
            "stream_asr_units": self._job.units if self._job else None,
            "early_asr": dict(self.early_asr),
            "backlog": self._q.qsize(),
        }

    # -------------------------------------------------------- listener thread
    def _run(self, stopping: threading.Event, q: queue.SimpleQueue) -> None:
        # No MLX on this thread (see module docstring); VAD is numpy.
        while not stopping.is_set():
            try:
                chunk = q.get(timeout=self.cfg.event_interval_s / 2)
            except queue.Empty:
                self._flush_partial()
                continue
            if chunk is None:
                break
            try:
                self._consume(chunk)
            except Exception:  # keep listening after a bad frame
                traceback.print_exc()
                self.bus.emit("error", message="listener: frame processing failed")

    def _consume(self, chunk: np.ndarray) -> None:
        buf = np.concatenate([self._rest, chunk]) if self._rest.size else chunk
        n = buf.size // FRAME
        self._rest = buf[n * FRAME:]
        for frame in buf[:n * FRAME].reshape(n, FRAME):
            self._frame(frame)
        self._flush_partial()

    def _frame(self, frame: np.ndarray) -> None:
        t = time.perf_counter()
        p = self._vad(frame)
        self.vad_ms.append((time.perf_counter() - t) * 1000)
        self.vad_p = p
        self.heard_samples += FRAME
        self._recent.append(frame)

        cfg = self.cfg
        start_p, need = ((cfg.start_p_audible, cfg.start_frames_audible) if self.rose_audible
                         else (cfg.start_p, cfg.start_frames))
        self._hot = self._hot + 1 if p >= start_p else 0
        u = self._utt
        if u is None:
            if self._hot >= need:
                self._begin()
        else:
            u.frames.append(frame)
            self._feed_stream(u, frame)
            if not u.paused:
                if p >= cfg.pause_p:
                    u.low = 0
                    u.voiced += FRAME
                    u.voice_end = self.heard_samples
                    if u.asr_job is not None:
                        u.asr_job.cancel()
                        u.asr_job = None
                        self.early_asr["discarded"] += 1
                        self._hold_stream(False)
                else:
                    u.low += FRAME
                    if u.asr_job is None and u.low >= _frames(cfg.early_asr_s) * FRAME:
                        self._hold_stream(True)
                        u.asr_job = self._asr_job(u.audio())
                        uid = u.id
                        u.asr_job.future.add_done_callback(lambda f, uid=uid: self._tentative(uid, f))
                    if u.low >= _frames(cfg.pause_s) * FRAME:
                        self._pause(u)
            elif self._hot >= need:
                self._resume(u)
            elif u.commit_at is not None and self.heard_samples >= u.commit_at:
                self._commit(u, "silence")

        now = time.monotonic()
        if now - self._vad_event_at >= cfg.event_interval_s:
            self._vad_event_at = now
            self.bus.emit("vad", p=round(p, 3), speaking=self.in_speech)

    # ---------------------------------------------------------- state machine
    def _begin(self) -> None:
        voiced = self._hot * FRAME
        recent = np.concatenate(self._recent)
        audio = recent[-(int(self.cfg.preroll_s * SR) + voiced):]
        self._next_id += 1
        u = _Utterance(id=self._next_id, start=self.heard_samples - voiced, frames=[audio],
                       voiced=voiced, voice_end=self.heard_samples)
        self._utt = u
        with self._lock:
            self._asr_utt = u.id
            self._asr_hold = False
            self._asr_audio = [audio]
            self._asr_text = (u.id, "")
        self.in_speech = True
        self.utterance_started_at = time.monotonic()
        self._emit("speech_start", u, t_audio=round(u.start / SR, 3))

    def _pause(self, u: _Utterance) -> None:
        u.paused = True
        u.pauses += 1
        self.in_speech = False
        self._emit("speech_pause", u, t_audio=round(u.voice_end / SR, 3))
        self._decide(u)
        if self._utt is not u:  # noise: already ended
            return
        level = u.dangling_level
        need = self.cfg.turn_p if level == 0 else self.cfg.turn_p_maybe
        preamble = is_preamble(u.text)  # "Hi Rose," / "Okay so": usually more is coming
        # An invented transcript says nothing reliable about the end of the turn.
        doubtful = too_fast(u.text, (u.voice_end - u.start) / SR)
        if (level < 2 and not preamble and not doubtful
                and (u.turn_p >= need or u.text.strip().endswith("?"))):
            self._commit(u, "smart_turn", compute_ms=u.wait_ms)
            return
        wait = {2: self.cfg.dangling_commit_s, 1: self.cfg.commit_s}.get(level, self.cfg.short_commit_s)
        if preamble:
            wait = self.cfg.preamble_commit_s
        u.commit_at = u.voice_end + int(wait * SR)
        if self.heard_samples >= u.commit_at:
            self._commit(u, "silence")

    def _decide(self, u: _Utterance) -> None:
        """Final transcript + smart-turn on the runtime; blocks this thread."""

        t = time.perf_counter()
        audio = u.audio()
        keep = int(self.cfg.turn_audio_s * SR)
        turn = self.runtime.call(lambda: _timed(self._turn.predict_endpoint, audio[-keep:], sample_rate=SR),
                                 priority=LISTEN, name="smart-turn")
        self.early_asr["used" if u.asr_job else "late"] += 1
        asr, u.asr_job = u.asr_job or self._asr_job(audio), None
        try:
            result, u.turn_ms = turn.result(timeout=15)
            u.turn_p = float(result.probability)
        except Exception as exc:  # the silence timer decides instead
            self.bus.emit("warning", message=f"listener: smart-turn failed ({type(exc).__name__})")
            u.turn_p = 0.0
        try:
            result, u.asr_ms = asr.result(timeout=15)
            u.text = result.text.strip()
        except Exception as exc:  # fall back to the streaming text
            self.bus.emit("warning", message=f"listener: final ASR failed ({type(exc).__name__})")
            with self._lock:
                utt, text = self._asr_text
            u.text = (text if utt == u.id else u.partial).strip()
        u.wait_ms = (time.perf_counter() - t) * 1000
        if not has_words(u.text):
            self._end(u)
            self._emit("noise", None, text=u.text, t_audio=round(self.heard_samples / SR, 3),
                        speech_ms=u.voiced * 1000 / SR)
            return
        u.dangling_level = dangling_level(u.text)
        u.dangling = u.dangling_level > 0

    def _tentative(self, uid: int, fut) -> None:
        """Early transcript of a dip (runs on the runtime thread): lets the
        conductor start thinking before the turn is committed."""

        if fut.cancelled() or fut.exception() is not None:
            return
        u = self._utt
        if u is None or u.id != uid or not u.paused and u.low == 0:
            return
        text = fut.result()[0].text.strip()
        if has_words(text):
            self._emit("tentative", u, text=text, t_audio=round(u.voice_end / SR, 3))

    def _asr_job(self, audio: np.ndarray) -> Job:
        return self.runtime.call(lambda: _timed(lambda: self._final.generate(mx.array(audio))),
                                 priority=LISTEN, name="final-asr")

    def _resume(self, u: _Utterance) -> None:
        voiced = self._hot * FRAME
        u.paused, u.commit_at, u.low, u.asr_job = False, None, 0, None
        self._hold_stream(False)
        u.voiced += voiced
        u.voice_end = self.heard_samples
        self.in_speech = True
        self._emit("speech_resume", u, t_audio=round((self.heard_samples - voiced) / SR, 3))

    def _commit(self, u: _Utterance, by: str, compute_ms: float = 0.0) -> None:
        silence_ms = (self.heard_samples - u.voice_end) * 1000 / SR
        data = {
            "endpoint_ms": round(silence_ms + compute_ms),
            "silence_ms": round(silence_ms),
            "asr_ms": round(u.asr_ms, 1),
            "smart_turn_p": round(u.turn_p, 3),
            "smart_turn_ms": round(u.turn_ms, 1),
            "wait_ms": round(u.wait_ms, 1),
            "dangling": u.dangling,
            "by": by,
            "pauses": u.pauses,
            "t_audio": round(self.heard_samples / SR, 3),
            "t_speech_start": round(u.start / SR, 3),
        }
        self._end(u)
        self._emit("turn_end", None, text=u.text, speech_ms=u.voiced * 1000 / SR, data=data,
                   bus={k: data[k] for k in ("t_audio", "endpoint_ms", "asr_ms", "smart_turn_p")})

    def _end(self, u: _Utterance) -> None:
        if u.asr_job is not None:
            u.asr_job.cancel()
        with self._lock:
            self._asr_utt = 0
            self._asr_hold = False
            self._asr_audio = []
        self._utt = None
        self.in_speech = False
        self.utterance_started_at = None

    # -------------------------------------------------------------- partials
    def _feed_stream(self, u: _Utterance, frame: np.ndarray) -> None:
        with self._lock:
            if self._asr_utt == u.id:
                self._asr_audio.append(frame)
                if self._asr_hold and len(self._asr_audio) > STREAM_HOLD_FRAMES:
                    del self._asr_audio[0]

    def _hold_stream(self, hold: bool) -> None:
        with self._lock:
            self._asr_hold = hold

    def _flush_partial(self) -> None:
        u = self._utt
        if u is None:
            return
        with self._lock:
            utt, text = self._asr_text
        now = time.monotonic()
        if utt != u.id or not text.strip() or text == u.partial:
            return
        if now - self._partial_at < self.cfg.event_interval_s:
            return
        self._partial_at = now
        u.partial = text
        self._emit("partial", u, text=text.strip(), t_audio=round(self.heard_samples / SR, 3))

    def _asr_loop(self):
        """Runtime job: one streaming session per utterance, one step per unit.

        A failing session costs that utterance its partials, not the job: a dead
        job would silently end partials (and word-based barge-in) for good."""

        session, utt, text, backlog = None, 0, "", []
        try:
            while not self._stopping.is_set():
                with self._lock:
                    want, held = self._asr_utt, self._asr_hold and self._asr_utt == utt
                    audio = [] if held else self._asr_audio
                    if not held:
                        self._asr_audio = []
                if held:
                    yield Sleep(STREAM_IDLE_S)
                    continue
                deltas = None
                try:
                    if want != utt:
                        if session is not None:
                            session.cancel()
                        session, utt, text, backlog = None, want, "", []
                        if want:
                            session = self._stream.create_streaming_session(language="en")
                    if session is not None:
                        backlog.extend(audio)
                        while backlog and getattr(session, "_queued", 0) < STREAM_MAX_QUEUED:
                            session.feed(backlog.pop(0))
                        deltas = session.step(max_decode_tokens=8)
                except Exception as exc:
                    traceback.print_exc()
                    self.bus.emit("warning", message=f"listener: streaming ASR failed ({type(exc).__name__})")
                    session, backlog = None, []
                if session is None:
                    yield Sleep(STREAM_IDLE_S)
                    continue
                if deltas:
                    text += "".join(deltas)
                    with self._lock:
                        if self._asr_utt == utt:
                            self._asr_text = (utt, text)
                    yield None
                elif backlog or _pending(session):
                    yield None
                else:
                    yield Sleep(STREAM_IDLE_S)
        finally:
            if session is not None:
                session.cancel()

    # ----------------------------------------------------------------- events
    def _emit(self, kind: str, u: _Utterance | None, *, text: str = "", speech_ms: float | None = None,
              data: dict | None = None, bus: dict | None = None, **fields) -> None:
        if speech_ms is None:
            speech_ms = u.voiced * 1000 / SR if u is not None else 0.0
        payload = dict(fields if bus is None else bus)
        if text or kind in ("partial", "turn_end", "noise"):
            payload["text"] = text
        self.bus.emit(kind, **payload)
        ev = ListenEvent(kind=kind, text=text, t=time.monotonic(), speech_ms=speech_ms,
                         data=dict(data if data is not None else fields))
        try:
            self.on_event(ev)
        except Exception:
            traceback.print_exc()


def _np(a: mx.array) -> np.ndarray:
    return np.array(a.astype(mx.float32), dtype=np.float32)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _timed(fn: Callable, *args, **kwargs) -> tuple:
    t = time.perf_counter()
    return fn(*args, **kwargs), (time.perf_counter() - t) * 1000


def _frames(seconds: float) -> int:
    return max(1, round(seconds * SR / FRAME))


def _pending(session) -> bool:
    # Private state of mlx-audio's NemotronStreamingSession: queued input or
    # encoded frames not yet decoded mean another step has work to do.
    return bool(getattr(session, "_encoded", None)) or getattr(session, "_queued", 0) > 0
