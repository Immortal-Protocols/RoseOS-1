"""Audio transports: where Rose's voice goes and where the mic comes from.

The speaker and listener never touch sockets or sound cards; they talk to a
Transport. ``BrowserTransport`` is the ``/ws/audio`` websocket (wire protocol
at the top of ``web/audio.js``). ``FakeTransport`` is an in-process client
that plays audio in real time with the same player semantics as the browser
worklet (per-piece ``played`` reports every 100 ms, 40 ms gain ramps, stop =
fade then ``stopped`` and a cleared queue), so tests and the eval harness
exercise exactly the accounting a real browser produces.

Note on reports: like the browser player, ``samples`` in a ``played`` or
``stopped`` report is the position inside the *piece* (one ``send_audio``
call) at the head of the queue, and ``done`` marks the end of that piece, not
of the whole segment. The speaker sums finished pieces per segment.
"""

from __future__ import annotations

import asyncio
import json
import struct
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import numpy as np

MIC_RATE = 16000
MIC_FRAME = 512
PLAY_RATE = 24000
REPORT_EVERY = PLAY_RATE // 10  # 100 ms, as the browser player
FADE_S = 0.04


class Transport(Protocol):
    on_mic: Callable[[np.ndarray], None] | None
    on_client: Callable[[dict], None] | None

    def send_audio(self, seg: int, pcm: np.ndarray) -> None: ...

    def send_control(self, msg: dict) -> None: ...


def encode_audio(seg: int, pcm: np.ndarray) -> bytes:
    """uint32 LE segment id + Int16 PCM (24 kHz mono)."""

    pcm16 = (np.clip(np.asarray(pcm, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2")
    return struct.pack("<I", seg & 0xFFFFFFFF) + pcm16.tobytes()


def decode_mic(data: bytes) -> np.ndarray:
    """Int16 LE PCM (16 kHz mono) -> float32 in [-1, 1]."""

    usable = len(data) - (len(data) % 2)
    return np.frombuffer(data[:usable], dtype="<i2").astype(np.float32) / 32768.0


def _call(cb: Callable[[Any], None] | None, arg: Any) -> None:
    if cb is None:
        return
    try:
        cb(arg)
    except Exception:  # a broken consumer must not kill the transport
        traceback.print_exc()


# ------------------------------------------------------------------ browser
class BrowserTransport:
    """The dashboard's /ws/audio websocket.

    ``send_audio`` / ``send_control`` may be called from any thread: sends are
    scheduled on the websocket's event loop and serialized by an asyncio lock
    (FIFO), so pieces arrive in the order they were sent. With no client
    attached, audio is reported back as played at once (``dropped``) and a
    stop as stopped, so a conversation never waits for a missing browser.
    """

    def __init__(self):
        self.on_mic: Callable[[np.ndarray], None] | None = None
        self.on_client: Callable[[dict], None] | None = None
        self._ws: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._send_lock: asyncio.Lock | None = None
        self._lock = threading.Lock()
        self.sent_bytes = 0
        self.mic_frames = 0

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def attach(self, websocket: Any, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            replaced = self._ws is not None and self._ws is not websocket
            self._ws, self._loop, self._send_lock = websocket, loop, asyncio.Lock()
        if replaced:
            self._lost()

    def detach(self, websocket: Any = None) -> None:
        with self._lock:
            if self._ws is None or (websocket is not None and websocket is not self._ws):
                return
            self._ws = self._loop = self._send_lock = None
        self._lost()

    def _lost(self) -> None:
        # Whatever the old client had queued will never be reported.
        _call(self.on_client, {"type": "stopped", "seg": -1, "samples": 0, "reason": "disconnected"})

    def send_audio(self, seg: int, pcm: np.ndarray) -> None:
        if not self._submit(encode_audio(seg, pcm)):
            _call(self.on_client, {"type": "played", "seg": seg, "samples": int(len(pcm)),
                                   "done": True, "dropped": True})

    def send_control(self, msg: dict) -> None:
        if not self._submit(json.dumps(msg)) and msg.get("type") == "stop":
            _call(self.on_client, {"type": "stopped", "seg": -1, "samples": 0, "dropped": True})

    def _submit(self, data: bytes | str) -> bool:
        with self._lock:
            ws, loop, lock = self._ws, self._loop, self._send_lock
        if ws is None or loop is None or loop.is_closed():
            return False
        try:
            asyncio.run_coroutine_threadsafe(self._send(ws, lock, data), loop)
        except RuntimeError:  # loop shut down
            return False
        return True

    async def _send(self, ws: Any, lock: asyncio.Lock, data: bytes | str) -> None:
        try:
            async with lock:
                if isinstance(data, bytes):
                    await ws.send_bytes(data)
                    self.sent_bytes += len(data)
                else:
                    await ws.send_text(data)
        except Exception:
            pass  # disconnected: serve() sees it and detaches

    async def serve(self, websocket: Any) -> None:
        """Accept (if needed), attach, and pump client messages until disconnect."""

        state = getattr(getattr(websocket, "application_state", None), "name", "CONNECTED")
        if state == "CONNECTING":
            await websocket.accept()
        self.attach(websocket, asyncio.get_running_loop())
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data:
                    self.mic_frames += 1
                    _call(self.on_mic, decode_mic(data))
                    continue
                text = msg.get("text")
                if text:
                    try:
                        parsed = json.loads(text)
                    except ValueError:
                        continue
                    if isinstance(parsed, dict):
                        _call(self.on_client, parsed)
        except Exception:
            pass  # abrupt close
        finally:
            self.detach(websocket)


# --------------------------------------------------------------------- fake
@dataclass(eq=False)
class _Piece:
    seg: int
    data: np.ndarray
    pos: int = 0


class FakeTransport:
    """A simulated browser client that plays Rose's audio in real time.

    A background thread renders 10 ms blocks paced by ``clock`` (``speed`` > 1
    plays faster than real time), applies duck/unduck/stop gain ramps, and
    sends the browser's reports to ``on_client`` from that thread:
    ``played`` every 100 ms of audio and at the end of every piece, ``idle``
    when the queue drains, ``stopped`` after a stop's 40 ms fade.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic, speed: float = 1.0,
                 block: int = 240):
        self.on_mic: Callable[[np.ndarray], None] | None = None
        self.on_client: Callable[[dict], None] | None = None
        self.clock, self.speed, self.block = clock, speed, block
        self.gain = 1.0
        self.reports: list[tuple[float, dict]] = []   # (clock, message) as sent to on_client
        self.controls: list[tuple[float, dict]] = []  # (clock, message) received
        self.sends: list[tuple[float, int, int]] = []  # (clock, seg, samples)
        self._target = 1.0
        self._stopping = False
        self._holding = False  # paused: silent, position kept
        self._since_report = 0
        self._queue: deque[_Piece] = deque()
        self._played: list[np.ndarray] = []
        self._raw: list[np.ndarray] = []
        self._mic_buf = np.zeros(0, dtype=np.float32)
        self._cond = threading.Condition()
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="fake-client", daemon=True)
        self._thread.start()

    # ----------------------------------------------------------- transport
    def send_audio(self, seg: int, pcm: np.ndarray) -> None:
        # Round trip through the wire format, like the browser.
        data = np.frombuffer(encode_audio(seg, pcm)[4:], dtype="<i2").astype(np.float32) / 32768.0
        with self._cond:
            self.sends.append((self.clock(), seg, len(data)))
            if len(data):
                self._queue.append(_Piece(seg, data))
            self._cond.notify()

    def send_control(self, msg: dict) -> None:
        kind = msg.get("type")
        with self._cond:
            self.controls.append((self.clock(), dict(msg)))
            if kind == "duck" and not self._stopping:
                self._target = float(msg.get("gain", 0.25))
            elif kind == "unduck" and not self._stopping and not self._holding:
                self._target = 1.0
            elif kind == "pause" and not self._stopping:
                self._holding, self._target = True, 0.0
            elif kind == "resume":
                self._holding = False
                if not self._stopping:
                    self._target = 1.0
            elif kind == "stop":
                self._stopping, self._target = True, 0.0
            self._cond.notify()

    def mic(self, samples: np.ndarray) -> None:
        """Inject user audio: calls on_mic with 512-sample frames (pacing is the caller's)."""

        pcm = np.asarray(samples, dtype=np.float32).reshape(-1)
        pcm = (np.clip(pcm, -1.0, 1.0) * 32767.0).astype(np.int16).astype(np.float32) / 32768.0
        buf = np.concatenate([self._mic_buf, pcm])
        n = len(buf) // MIC_FRAME * MIC_FRAME
        self._mic_buf = buf[n:]
        for i in range(0, n, MIC_FRAME):
            _call(self.on_mic, buf[i : i + MIC_FRAME])

    # ---------------------------------------------------------- inspection
    def played_audio(self, raw: bool = False) -> np.ndarray:
        """Everything played so far at 24 kHz, after gain (``raw``: before gain)."""

        with self._cond:
            parts = list(self._raw if raw else self._played)
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)

    def played_samples(self) -> int:
        with self._cond:
            return sum(len(p) for p in self._played)

    def queued_samples(self) -> int:
        with self._cond:
            return sum(len(p.data) - p.pos for p in self._queue)

    def clear(self) -> None:
        with self._cond:
            self._played.clear()
            self._raw.clear()
            self.reports.clear()
            self.controls.clear()
            self.sends.clear()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify()
        self._thread.join(timeout=1.0)

    # ------------------------------------------------------------- player
    def _busy(self) -> bool:
        return bool(self._queue) or self._stopping or self.gain != self._target

    def _run(self) -> None:
        next_t: float | None = None
        while True:
            with self._cond:
                while not self._closed and not self._busy():
                    next_t = None
                    self._cond.wait()
                if self._closed:
                    return
                if next_t is None:
                    next_t = self.clock()
                out = self._render(self.block)
                now = self.clock()
                self.reports.extend((now, m) for m in out)
            for m in out:
                _call(self.on_client, m)
            next_t += self.block / PLAY_RATE / self.speed
            delay = next_t - self.clock()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.1:
                next_t = self.clock()  # fell far behind: do not burst to catch up

    def _ramp(self, k: int) -> np.ndarray:
        if self.gain == self._target:
            return np.full(k, self.gain, dtype=np.float32)
        step = 1.0 / (FADE_S * PLAY_RATE)
        idx = np.arange(1, k + 1, dtype=np.float64) * step
        if self._target > self.gain:
            g = np.minimum(self.gain + idx, self._target)
        else:
            g = np.maximum(self.gain - idx, self._target)
        self.gain = float(g[-1])
        return g.astype(np.float32)

    def _take(self, head: _Piece, k: int, gains: np.ndarray) -> None:
        raw = head.data[head.pos : head.pos + k]
        self._raw.append(raw)
        self._played.append(raw * gains[: len(raw)])
        head.pos += k

    def _render(self, n: int) -> list[dict]:
        """Play n samples; mirrors the browser worklet sample loop, vectorized."""

        out: list[dict] = []
        i = 0
        while i < n:
            head = self._queue[0] if self._queue else None
            k = n - i
            if head is not None:
                k = min(k, len(head.data) - head.pos, REPORT_EVERY - self._since_report)
            gains = self._ramp(k)
            if self._stopping:
                zero = np.flatnonzero(gains <= 0.0)
                if zero.size:
                    z = int(zero[0])
                    if head is not None:
                        self._take(head, z, gains)
                        out.append({"type": "stopped", "seg": head.seg, "samples": head.pos})
                    else:
                        out.append({"type": "stopped", "seg": -1, "samples": 0})
                    self._queue.clear()
                    self._stopping = self._holding = False
                    self._target = self.gain = 1.0
                    return out
            if self._holding and head is not None:
                zero = np.flatnonzero(gains <= 0.0)
                if zero.size:
                    # Faded out: play up to silence, then hold the position.
                    z = int(zero[0])
                    if z:
                        self._take(head, z, gains)
                    return out
            if head is None:
                i += k
                continue
            self._take(head, k, gains)
            self._since_report += k
            if self._since_report >= REPORT_EVERY:
                self._since_report = 0
                out.append({"type": "played", "seg": head.seg, "samples": head.pos, "done": False})
            if head.pos >= len(head.data):
                out.append({"type": "played", "seg": head.seg, "samples": head.pos, "done": True})
                self._queue.popleft()
                if not self._queue:
                    out.append({"type": "idle"})
            i += k
        return out
