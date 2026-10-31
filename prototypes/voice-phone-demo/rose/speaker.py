"""Rose's voice: streamed talker text -> Phonon TTS on the runtime -> client.

Rose must start talking as soon as the talker has produced a clause, stop
the instant the user barges in, and afterwards remember only what the user
actually heard. So:

- Text is cut into chunks as it streams: the first at the first clause
  boundary with >= 3 words (or after 12 words), then whole sentences. Each
  chunk is one *segment* with a unique, increasing id.
- One runtime job per utterance at SPEAK priority synthesizes one 80 ms Mimi
  frame per unit and sends audio in 80-160 ms pieces. It stays at most
  ~1.5 s ahead of playback, so a stop wastes little work. Utterances speak
  one at a time, in order (the model state is shared and the client plays
  in arrival order).
- The client reports how far it played each piece; per segment that gives
  played samples, and ``heard()`` turns them into words.

Phonon keeps the voice prompt in its flow-LM KV cache. The cache is cut back
to the prompt before every chunk, otherwise earlier text leaks into later
audio. Phonon also pads every chunk with ~0.1-0.2 s of leading and ~0.7-0.9 s
of trailing near-silence: the first audio of a reply keeps only a 20 ms
margin and the gaps between chunks are shortened to natural pauses.
"""

from __future__ import annotations

import itertools
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import mlx.core as mx
import numpy as np

from rose.bus import Bus
from rose.runtime import SPEAK, Job, Runtime, Sleep
from rose.textnorm import speakable
from rose.transport import Transport

REPO = os.environ.get("ROSE_TTS", "gradium/phonon-31m-en")
VOICE_REPO = "gradium/phonon-voices"
VOICES = {
    "zoey": "NbpkqMVS3CJeq2j8", "harper": "4SZHfMpw-p46Ywgs", "everly": "vjFyp2BcCyTlUmYb",
    "juniper": "NIWIOv5vTi5eRMj8", "hadley": "bXwrkHhURL1VFJdG", "delia": "nGzMHMPAWGaJFHOo",
    "easton": "LMDIYE0Oa377Qh6Z", "micah": "40QrMVgAxFiUnP9f", "miles": "d9XCyhjo4JhkHF1d",
}

RATE = 24000
PIECE = 1920              # send once >= 80 ms is pending (pieces are 80-160 ms)
MAX_AHEAD_S = 1.5         # synthesized but unplayed audio before the job steps aside
SILENCE = 0.02            # |sample| below this is near-silence
FIRST_MARGIN = 480        # 20 ms kept before the first voiced sample of a reply
LEAD_KEEP = 1920          # 80 ms kept before later chunks
TAIL_KEEP = {"clause": 960, "sentence": 2880}  # kept after a chunk's last voiced frame
STOP_TIMEOUT_S = 0.5      # give up waiting for the client's "stopped" report

FIRST_MIN_WORDS, FIRST_MAX_WORDS = 3, 12
SHORT_SENTENCE = 4        # later sentences shorter than this merge with the next
MAX_CHUNK_WORDS = 30

_PUNCT = re.compile(r"[.!?,;:]+[\"'”’)\]]*|—")
_ABBREV = {"mr", "mrs", "ms", "dr", "st", "vs", "etc", "jr", "sr", "approx", "inc", "ltd", "mt"}


# -------------------------------------------------------------- chunking
def _nwords(text: str) -> int:
    return len(text.split())


def _abbrev_before(text: str, i: int) -> bool:
    m = re.search(r"(\S+)$", text[:i])
    word = m.group(1).lower().lstrip("\"'(“") if m else ""
    return (len(word) == 1 and word.isalpha()) or "." in word or word in _ABBREV


def _boundaries(text: str, eager: bool) -> Iterator[tuple[int, str]]:
    """(end index, "clause" | "sentence") of confirmed punctuation boundaries.

    A boundary needs whitespace after it, so "6:30", "3.5" and "e.g." never
    split. ``eager`` also accepts one at the very end of the buffer when a
    letter precedes it, which saves waiting a token for the first chunk.
    """

    for m in _PUNCT.finditer(text):
        mark, end = m.group(), m.end()
        if mark != "—":
            after = text[end : end + 1]
            if after == "":
                if not eager or not text[m.start() - 1 : m.start()].isalpha():
                    continue
            elif not after.isspace():
                continue
            if mark[0] == "." and _abbrev_before(text, m.start()):
                continue
        yield end, ("sentence" if any(c in ".!?" for c in mark) else "clause")


def _word_cut(text: str, n: int) -> int | None:
    """Index just after the n-th complete (whitespace-terminated) word."""

    words = [m for m in re.finditer(r"\S+", text) if m.end() < len(text)]
    return words[n - 1].end() if len(words) >= n else None


class Chunker:
    """Cuts streamed text into TTS chunks (see the module docstring)."""

    def __init__(self):
        self.buffer = ""
        self.first = True

    def feed(self, text: str) -> list[str]:
        self.buffer += text
        chunks = []
        while (cut := self._cut()) is not None:
            chunk, self.buffer = self.buffer[:cut].strip(), self.buffer[cut:]
            if chunk:
                chunks.append(chunk)
                self.first = False
        return chunks

    def flush(self) -> list[str]:
        chunk, self.buffer = self.buffer.strip(), ""
        if chunk:
            self.first = False
            return [chunk]
        return []

    def _cut(self) -> int | None:
        text = self.buffer
        if self.first:
            for end, _ in _boundaries(text, eager=True):
                if _nwords(text[:end]) >= FIRST_MIN_WORDS:
                    return end
            return _word_cut(text, FIRST_MAX_WORDS)
        clause = None
        for end, kind in _boundaries(text, eager=False):
            n = _nwords(text[:end])
            if n > MAX_CHUNK_WORDS:
                break
            if kind == "sentence" and n >= SHORT_SENTENCE:
                return end
            if n >= 5:
                clause = end
        if _nwords(text) > MAX_CHUNK_WORDS:
            return clause or _word_cut(text, MAX_CHUNK_WORDS)
        return None


def _chunk_kind(text: str) -> str:
    return "sentence" if text.rstrip("\"'”’)] ")[-1:] in ".!?" else "clause"


# --------------------------------------------------------------- state
@dataclass
class _Voice:
    name: str
    state: dict
    base: int


@dataclass(eq=False)
class _Seg:
    id: int
    utt: "Utterance"
    text: str                 # the talker's words (what heard() returns)
    spoken: str               # normalized text given to TTS
    kind: str
    sent: int = 0             # samples sent to the client
    played: int = 0           # samples the client reported played
    base: int = 0             # samples of fully played pieces
    synth_done: bool = False
    complete: bool = False    # synthesized to the end (not cut short)
    closed: bool = False      # no more reports expected
    voice_start: int = 0      # first / last voiced sample in the segment's audio
    voice_end: int = 0


class Utterance:
    """One reply's speech. ``add`` is called from the conductor as text streams."""

    def __init__(self, speaker: "Speaker", reply_id: int, t_user_end: float | None):
        self.speaker = speaker
        self.id = reply_id
        self.t_user_end = t_user_end
        self.done = threading.Event()
        self.first_audio_at: float | None = None
        self.first_chunk_at: float | None = None  # first chunk ready for TTS
        self.interrupted = False
        self.text = ""
        self._chunker = Chunker()
        self._queue: list[_Seg] = []
        self._segs: list[_Seg] = []
        self._finished = False
        self._cancelled = False
        self._synth_finished = False
        self._submitted = False
        self._job: Job | None = None

    # --------------------------------------------------------------- input
    def add(self, text: str) -> None:
        sp = self.speaker
        with sp._lock:
            if self._finished or self._cancelled or not text:
                return
            self.text += text
            self._push(self._chunker.feed(text))
        self._ensure_job()

    def retract(self, text: str) -> bool:
        """Take back ``text`` if it is still waiting in the chunker (not yet sent
        to TTS). Returns False when some of it was already sent."""

        text = text.strip()
        with self.speaker._lock:
            buf = self._chunker.buffer.rstrip()
            if not text or not buf.endswith(text):
                return False
            self._chunker.buffer = buf[: len(buf) - len(text)]
            if self.text.rstrip().endswith(text):
                self.text = self.text.rstrip()[: len(self.text.rstrip()) - len(text)]
            return True

    def flush(self) -> None:
        """Speak whatever text is buffered now (e.g. before a tool call)."""

        with self.speaker._lock:
            if not (self._finished or self._cancelled):
                self._push(self._chunker.flush())
        self._ensure_job()

    def finish(self) -> None:
        sp = self.speaker
        with sp._lock:
            if self._finished or self._cancelled:
                return
            self._push(self._chunker.flush())
            self._finished = True
            if not self._submitted and not self._queue:
                self._synth_finished = True
            sp._check_done()
        self._ensure_job()

    def cancel(self) -> None:
        sp = self.speaker
        with sp._lock:
            if self._cancelled:
                return
            self._cancelled = True
            if not self.done.is_set():
                self.interrupted = True
            self._queue.clear()
            for seg in self._segs:
                if seg.sent == 0:
                    sp._close(seg)
            job = self._job
            sp._check_done()
        if job is not None:
            job.cancel()

    def _push(self, chunks: list[str]) -> None:
        if self._synth_finished:
            return  # the TTS job died (error): queued text would keep done from ever settling
        now = time.monotonic()
        for chunk in chunks:
            spoken = speakable(chunk)
            if not re.search(r"[A-Za-z0-9]", spoken):
                continue
            seg = _Seg(next(self.speaker._seg_ids), self, chunk, spoken, _chunk_kind(chunk))
            if self.first_chunk_at is None:
                self.first_chunk_at = now
            self._queue.append(seg)
            self._segs.append(seg)
            self.speaker._segs[seg.id] = seg

    def _ensure_job(self) -> None:
        sp = self.speaker
        with sp._lock:
            if self._submitted or self._cancelled or not self._queue:
                return
            self._submitted = True
        job = sp.runtime.submit(self._synth, priority=SPEAK, name=f"tts-{self.id}")
        with sp._lock:
            self._job = job
            if self._cancelled:
                job.cancel()

    # -------------------------------------------------------------- output
    def heard(self) -> str:
        """The talker's words the user has heard: fully played chunks plus a
        proportional (by spoken length) prefix of the one being played."""

        with self.speaker._lock:
            parts = []
            for seg in self._segs:
                if seg.sent == 0:
                    if seg.synth_done:
                        continue  # nothing audible in it
                    break
                if seg.complete and seg.played >= seg.sent:
                    parts.append(seg.text)
                    continue
                parts.append(_prefix(seg, self.speaker._samples_per_unit))
                break
        return " ".join(p for p in parts if p)

    # ----------------------------------------------------------- synthesis
    def _synth(self) -> Iterator[Any]:
        """The utterance's runtime job: one Mimi frame per unit."""

        sp = self.speaker
        sp._tts_waiting.append(self)
        try:
            # Phonon's flow cache and Mimi decoder are shared, and the client
            # plays in arrival order: utterances speak one at a time, in order.
            while sp._tts_owner is not None or sp._tts_waiting[0] is not self:
                if self._cancelled:
                    return
                yield Sleep(0.01)
            sp._tts_waiting.remove(self)
            sp._tts_owner = self
            while not self._cancelled:
                with sp._lock:  # atomic with finish(), which may push a last chunk
                    seg = self._queue.pop(0) if self._queue else None
                    finished = self._finished
                if seg is None:
                    if finished:
                        break
                    yield Sleep(0.01)
                    continue
                try:
                    yield from self._speak(seg)
                finally:
                    with sp._lock:
                        seg.synth_done = True
                        sp._settle()
        except Exception as exc:  # report, then settle so nobody waits forever
            sp.bus.emit("error", message=f"tts: {type(exc).__name__}: {exc}")
        finally:
            if sp._tts_owner is self:
                sp._tts_owner = None
            if self in sp._tts_waiting:
                sp._tts_waiting.remove(self)
            with sp._lock:
                for seg in self._queue:
                    seg.synth_done = True
                    sp._close(seg)
                self._queue.clear()
                self._synth_finished = True
                last = next((s.id for s in reversed(self._segs) if s.sent), None)
                sp._settle()
            if last is not None and not self._cancelled:
                sp.transport.send_control({"type": "end", "seg": last})
            sp._notify_audible()

    def _speak(self, seg: _Seg) -> Iterator[Any]:
        sp = self.speaker
        model, voice = sp.model, sp._voice
        if model is None or voice is None:
            raise RuntimeError("Speaker.load() was not called")
        sp.bus.emit("rose_say", id=self.id, seg=seg.id, text=seg.text, spoken=seg.spoken)
        model._slice_flow_cache(voice.state, voice.base)
        frames = model.generate_audio_stream(voice.state, seg.spoken)
        lead_keep = FIRST_MARGIN if self.first_audio_at is None else LEAD_KEEP
        held: list[np.ndarray] = []   # silent frames not sent yet
        pending: list[np.ndarray] = []
        pos = 0                       # samples of this segment emitted so far
        voiced = False
        try:
            for frame in frames:
                mx.eval(frame)
                x = np.array(frame, dtype=np.float32).reshape(-1)
                loud = np.flatnonzero(np.abs(x) > SILENCE)
                if loud.size == 0:
                    held.append(x)
                else:
                    if not voiced:
                        lead = np.concatenate(held + [x[: loud[0]]])
                        pre = lead[max(0, len(lead) - lead_keep):]
                        parts = [pre, x[loud[0]:]]
                        seg.voice_start = pos + len(pre)
                        seg.voice_end = seg.voice_start + int(loud[-1] - loud[0])
                        voiced = True
                    else:
                        parts = held + [x]
                        seg.voice_end = pos + sum(len(h) for h in held) + int(loud[-1])
                    held = []
                    for p in parts:
                        pending.append(p)
                        pos += len(p)
                    if sum(len(p) for p in pending) >= PIECE:
                        yield from self._emit(seg, np.concatenate(pending))
                        pending = []
                yield None
                while not self._cancelled and (sp._stop_pending or sp.buffered_s() > MAX_AHEAD_S):
                    yield Sleep(0.02)
                if self._cancelled:
                    return
            if voiced:
                tail = np.concatenate(held)[: TAIL_KEEP[seg.kind]] if held else x[:0]
                pending.append(tail)
                yield from self._emit(seg, np.concatenate(pending))
                if not self._cancelled:
                    seg.complete = True
                    sp._learn_rate(seg)
        finally:
            frames.close()

    def _emit(self, seg: _Seg, audio: np.ndarray) -> Iterator[Any]:
        sp = self.speaker
        while sp._stop_pending and not self._cancelled:
            yield Sleep(0.01)  # the client clears anything that arrives during a stop's fade
        if not len(audio):
            return
        now = time.monotonic()
        with sp._lock:
            if self._cancelled:
                return
            seg.sent += len(audio)
            first = self.first_audio_at is None
            if first:
                self.first_audio_at = now
        # A pause held back as silent frames can make this long: 80-160 ms pieces.
        for piece in np.array_split(audio, max(1, len(audio) // PIECE)):
            sp.transport.send_audio(seg.id, piece)
        sp._notify_audible()
        if first:
            sp.bus.emit("rose_audio_start", id=self.id,
                        latency_ms=None if self.t_user_end is None else round((now - self.t_user_end) * 1000),
                        tts_ms=round((now - (self.first_chunk_at or now)) * 1000, 1))


def _weights(text: str) -> list[int]:
    """Rough spoken length of each word (characters once normalized)."""

    return [len(speakable(w)) + 1 for w in text.split()]


def _prefix(seg: _Seg, samples_per_unit: float) -> str:
    words, weights = seg.text.split(), _weights(seg.text)
    total = sum(weights)
    if seg.complete:
        span = seg.voice_end - seg.voice_start
    else:  # cut short or still synthesizing: estimate the whole chunk's length
        span = max(seg.sent - seg.voice_start, total * samples_per_unit)
    frac = min(1.0, max(0.0, (seg.played - seg.voice_start) / max(1.0, span)))
    acc, n = 0.0, 0
    for w in weights:
        if acc + w / 2 > frac * total:
            break
        acc += w
        n += 1
    return " ".join(words[:n])


# ----------------------------------------------------------------- speaker
class Speaker:
    voices: dict[str, str] = dict(VOICES)

    def __init__(self, runtime: Runtime, bus: Bus, transport: Transport):
        self.runtime, self.bus, self.transport = runtime, bus, transport
        if getattr(transport, "on_client", None) is None:
            transport.on_client = self.on_client
        self.model: Any = None
        self.voice: str | None = None
        self.on_audible: Callable[[bool], None] | None = None  # called on audible changes
        self.ducked = False
        self.paused = False
        self.load_s = 0.0
        self._voice: _Voice | None = None
        self._voice_cache: dict[str, _Voice] = {}
        self._lock = threading.RLock()
        self._segs: dict[int, _Seg] = {}    # open segments by id
        self._active: list[Utterance] = []  # not done yet
        self._seg_ids = itertools.count(1)
        self._tts_owner: Utterance | None = None  # runtime thread only
        self._tts_waiting: list[Utterance] = []   # runtime thread only, in job start order
        self._stop_pending = False
        self._stop_token = 0
        self._was_audible = False
        self._samples_per_unit = 0.055 * RATE  # speaking rate, learned from finished chunks

    # -------------------------------------------------------------- loading
    def load(self, voice: str = "zoey") -> None:
        t = time.perf_counter()

        def _load():
            from mlx_audio.tts.utils import load_model

            self.model = load_model(REPO)

        self.runtime.run(_load, priority=SPEAK, name="tts-load")
        self.set_voice(voice)
        self.runtime.run(self._warmup, priority=SPEAK, name="tts-warmup")
        self.load_s = time.perf_counter() - t

    def set_voice(self, voice: str) -> None:
        name = voice.lower() if voice.lower() in self.voices else voice
        voice_id = self.voices.get(name, name)
        if name not in self.voices and voice_id not in self.voices.values():
            raise ValueError(f"unknown voice {voice!r}; choose from {', '.join(self.voices)}")
        if name not in self._voice_cache:
            path = _voice_file(voice_id)  # file lookup (or download) off the MLX thread
            self._voice_cache[name] = self.runtime.run(
                lambda: self._build_voice(name, path), priority=SPEAK, name="tts-voice")
        self._voice = self._voice_cache[name]
        self.voice = name

    def _build_voice(self, name: str, path: str) -> _Voice:
        state =self.model.get_state_for_audio_prompt(self.model._resolve_audio_prompt(path, None, False))
        return _Voice(name, state, self.model._get_flow_cache_num_frames(state))

    def _warmup(self) -> None:
        voice = self._voice
        self.model._slice_flow_cache(voice.state, voice.base)
        for frame in self.model.generate_audio_stream(voice.state, "Hi there."):
            mx.eval(frame)
        self.model._slice_flow_cache(voice.state, voice.base)

    # ------------------------------------------------------------ speaking
    def utterance(self, reply_id: int, t_user_end: float | None) -> Utterance:
        utt = Utterance(self, reply_id, t_user_end)
        with self._lock:
            self._active.append(utt)
        return utt

    def duck(self, gain: float = 0.25) -> None:
        if self._stop_pending:
            return  # the browser player would turn a fading stop into a duck
        self.ducked = True
        self.transport.send_control({"type": "duck", "gain": gain})

    def unduck(self) -> None:
        self.ducked = False
        self.transport.send_control({"type": "unduck"})

    def pause(self) -> None:
        """Fade to silence and hold the position: nothing is lost if Rose resumes."""

        if self._stop_pending:
            return
        self.paused = True
        self.transport.send_control({"type": "pause"})

    def resume(self) -> None:
        """Continue a paused (and/or ducked) reply at full volume."""

        self.paused = self.ducked = False
        self.transport.send_control({"type": "resume"})

    def stop(self) -> None:
        """Fade out and clear the client now; cancels every active utterance.

        Their ``done`` is set once the client reports how far it got (or after
        0.5 s), so ``heard()`` is exact by then.
        """

        with self._lock:
            self._stop_pending = True
            self._stop_token += 1
            token = self._stop_token
            active = [u for u in self._active if not u.done.is_set()]
        for utt in active:
            utt.cancel()
        self.transport.send_control({"type": "stop"})
        timer = threading.Timer(STOP_TIMEOUT_S, self._stop_timeout, args=(token,))
        timer.daemon = True
        timer.start()

    @property
    def audible(self) -> bool:
        with self._lock:
            return any(s.sent > s.played for s in self._segs.values())

    def buffered_s(self) -> float:
        """Audio sent to the client and not yet played."""

        with self._lock:
            return sum(s.sent - s.played for s in self._segs.values()) / RATE

    # ------------------------------------------------------- client reports
    def on_client(self, msg: dict) -> None:
        # "idle" adds nothing (every piece is reported done) and, arriving after
        # the next chunk was sent, would mark that unplayed audio as played.
        kind = msg.get("type")
        if kind not in ("played", "stopped"):
            return
        with self._lock:
            if kind == "played":
                seg = self._segs.get(int(msg.get("seg", -1)))
                if seg is None:
                    return
                n = int(msg.get("samples", 0))
                if msg.get("done"):
                    seg.base += n
                    seg.played = seg.base
                else:
                    seg.played = seg.base + n
                seg.played = min(seg.played, seg.sent)
            else:
                self._stopped(int(msg.get("seg", -1)), int(msg.get("samples", 0)))
            self._settle()
        self._notify_audible()

    def _stopped(self, seg_id: int, samples: int) -> None:
        for seg in list(self._segs.values()):
            if seg.sent == 0 and not seg.utt._cancelled:
                continue  # not sent yet: unaffected
            if seg.id == seg_id:
                seg.played = min(seg.sent, seg.base + samples)
            self._close(seg)
        self._stop_pending = False
        self.ducked = self.paused = False  # the player resets its gain after a stop

    def _stop_timeout(self, token: int) -> None:
        with self._lock:
            if not self._stop_pending or token != self._stop_token:
                return
            self._stopped(-1, 0)
            self._settle()
        self.bus.emit("warning", message="speaker: no stop report from the client within 0.5 s")
        self._notify_audible()

    # ------------------------------------------------------------ accounting
    def _learn_rate(self, seg: _Seg) -> None:
        units = sum(_weights(seg.text))
        if units >= 10 and seg.voice_end > seg.voice_start:
            measured = (seg.voice_end - seg.voice_start) / units
            self._samples_per_unit += 0.2 * (measured - self._samples_per_unit)

    def _close(self, seg: _Seg) -> None:
        seg.closed = True
        self._segs.pop(seg.id, None)
        if seg.played < seg.sent:
            seg.utt.interrupted = True

    def _settle(self) -> None:
        """Close fully played segments and complete finished utterances (lock held)."""

        for seg in list(self._segs.values()):
            if seg.synth_done and seg.played >= seg.sent:
                self._close(seg)
        self._check_done()

    def _check_done(self) -> None:
        for utt in list(self._active):
            open_segs = [s for s in utt._segs if not s.closed]
            if utt._cancelled:
                ready = not (self._stop_pending and any(s.sent for s in open_segs))
            else:
                ready = (utt._finished and utt._synth_finished and not utt._queue
                         and not utt._chunker.buffer.strip() and not open_segs)
            if ready:
                utt.done.set()
                self._active.remove(utt)

    def _notify_audible(self) -> None:
        audible = self.audible
        if audible != self._was_audible:
            self._was_audible = audible
            cb = self.on_audible
            if cb is not None:
                try:
                    cb(audible)
                except Exception:
                    import traceback

                    traceback.print_exc()


def _voice_file(voice_id: str) -> str:
    """Local path of a Phonon voice; downloads only that small file if missing."""

    from huggingface_hub import hf_hub_download

    name = f"voices/{voice_id}.safetensors"
    try:
        return hf_hub_download(VOICE_REPO, name, local_files_only=True)
    except Exception:
        try:
            return hf_hub_download(VOICE_REPO, name)
        except Exception as exc:
            raise RuntimeError(f"voice {voice_id} is not cached and could not be downloaded "
                               f"({type(exc).__name__})") from None
