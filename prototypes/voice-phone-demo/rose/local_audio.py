"""Audio clients that live in this process: the sound card, or nobody at all.

The speaker learns what the user actually heard from the client's playback
reports, so every client must behave like the browser player in
``web/audio.js``: segments queue up, duck/unduck ramp the gain over 40 ms,
"stop" fades out and reports how far the current segment got, progress is
reported every 100 ms and "idle" when the queue drains.

``Player`` is that state machine. ``LocalTransport`` drives it from a
sounddevice output callback (``--local-audio``; there is no echo
cancellation, so use headphones). ``SilentTransport`` drives it from a clock
and discards the samples, so Rose's turns complete at the real pace when no
browser is connected (Speak box before the mic is started, curl, tests).
"""

from __future__ import annotations

import collections
import queue
import threading
import time
from typing import Callable

import numpy as np

MIC_RATE = 16000
PLAY_RATE = 24000
MIC_FRAME = 512                 # 32 ms, what the listener's VAD consumes
REPORT_EVERY = PLAY_RATE // 10  # 100 ms
RAMP_S = 0.04


class Player:
    """Playback queue with gain ramps and played-sample accounting (thread-safe)."""

    def __init__(self, rate: int = PLAY_RATE):
        self.rate = rate
        self._lock = threading.Lock()
        self._queue: collections.deque[list] = collections.deque()  # [seg, data, pos]
        self._gain = self._target = 1.0
        self._stopping = False
        self._holding = False  # paused: silent, position kept
        self._since_report = 0
        self.played_samples = 0

    @property
    def busy(self) -> bool:
        """Audio queued, or a stop still fading out."""
        return bool(self._queue) or self._stopping

    def push(self, seg: int, pcm: np.ndarray) -> None:
        data = np.asarray(pcm, dtype=np.float32).reshape(-1)
        if data.size:
            with self._lock:
                self._queue.append([int(seg), data, 0])

    def control(self, msg: dict) -> None:
        kind = msg.get("type")
        with self._lock:
            if kind == "duck":
                if not self._stopping:
                    self._target = float(msg.get("gain", 0.25))
            elif kind == "unduck":
                if not self._stopping and not self._holding:
                    self._target = 1.0
            elif kind == "pause":
                if not self._stopping:
                    self._holding, self._target = True, 0.0
            elif kind == "resume":
                self._holding = False
                if not self._stopping:
                    self._target = 1.0
            elif kind == "stop":
                self._stopping, self._target = True, 0.0

    def pull(self, frames: int) -> tuple[np.ndarray, list[dict]]:
        """Next ``frames`` output samples plus the reports they produced."""

        out = np.zeros(frames, np.float32)
        reports: list[dict] = []
        with self._lock:
            step = 1.0 / (RAMP_S * self.rate)
            ramp = np.arange(1, frames + 1, dtype=np.float32) * step
            if self._target >= self._gain:
                gains = np.minimum(self._gain + ramp, self._target)
            else:
                gains = np.maximum(self._gain - ramp, self._target)
            self._gain = float(gains[-1])
            n = frames
            if self._stopping or self._holding:
                silent = np.flatnonzero(gains <= 0.0)
                if silent.size:
                    n = int(silent[0])
            filled = 0
            while filled < n and self._queue:
                item = self._queue[0]
                seg, data, pos = item
                take = min(n - filled, len(data) - pos)
                out[filled:filled + take] = data[pos:pos + take]
                item[2] = pos + take
                filled += take
                self._since_report += take
                self.played_samples += take
                if item[2] >= len(data):
                    self._since_report = 0
                    reports.append({"type": "played", "seg": seg, "samples": item[2], "done": True})
                    self._queue.popleft()
                    if not self._queue:
                        reports.append({"type": "idle"})
                elif self._since_report >= REPORT_EVERY:
                    self._since_report = 0
                    reports.append({"type": "played", "seg": seg, "samples": item[2], "done": False})
            out[:n] *= gains[:n]
            if self._stopping and n < frames:
                head = self._queue[0] if self._queue else None
                reports.append({"type": "stopped", "seg": head[0] if head else -1,
                                "samples": head[2] if head else 0})
                self._queue.clear()
                self._stopping = self._holding = False
                self._gain = self._target = 1.0
        return out, reports


class _Reporter:
    """Delivers player reports off the audio thread (on_client may take a lock)."""

    def __init__(self, deliver: Callable[[dict], None]):
        self._q: queue.SimpleQueue[dict | None] = queue.SimpleQueue()
        self._deliver = deliver
        threading.Thread(target=self._run, name="playback-reports", daemon=True).start()

    def put(self, reports: list[dict]) -> None:
        for r in reports:
            self._q.put(r)

    def close(self) -> None:
        self._q.put(None)

    def _run(self) -> None:
        while (msg := self._q.get()) is not None:
            try:
                self._deliver(msg)
            except Exception:
                import traceback

                traceback.print_exc()


class SilentTransport:
    """A client that 'plays' Rose's audio in real time into nothing."""

    name = "silent"

    def __init__(self, tick_s: float = 0.02):
        self.on_mic: Callable[[np.ndarray], None] = lambda samples: None
        self.on_client: Callable[[dict], None] = lambda msg: None
        self.player = Player()
        self._reports = _Reporter(lambda msg: self.on_client(msg))
        self._tick_s = tick_s
        self._wake = threading.Event()
        threading.Thread(target=self._run, name="silent-client", daemon=True).start()

    def send_audio(self, seg: int, pcm: np.ndarray) -> None:
        self.player.push(seg, pcm)
        self._wake.set()

    def send_control(self, msg: dict) -> None:
        self.player.control(msg)
        self._wake.set()

    @property
    def busy(self) -> bool:
        return self.player.busy

    def _run(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            start, pulled = time.monotonic(), 0
            while self.player.busy:
                time.sleep(self._tick_s)
                due = int((time.monotonic() - start) * PLAY_RATE) - pulled
                if due > 0:
                    _, reports = self.player.pull(due)
                    pulled += due
                    self._reports.put(reports)


class LocalTransport:
    """Sound card mic (16 kHz) and speaker (24 kHz) through sounddevice."""

    name = "local"

    def __init__(self, input_device: str | int | None = None, output_device: str | int | None = None):
        self.on_mic: Callable[[np.ndarray], None] = lambda samples: None
        self.on_client: Callable[[dict], None] = lambda msg: None
        self.player = Player()
        self._reports = _Reporter(lambda msg: self.on_client(msg))
        self._devices = (input_device, output_device)
        self._streams: list = []

    def start(self) -> None:
        import sounddevice as sd

        mic = sd.InputStream(samplerate=MIC_RATE, channels=1, dtype="float32", blocksize=MIC_FRAME,
                             device=self._devices[0], callback=self._mic_callback)
        out = sd.OutputStream(samplerate=PLAY_RATE, channels=1, dtype="float32", blocksize=480,
                              latency="low", device=self._devices[1], callback=self._out_callback)
        self._streams = [mic, out]
        for s in self._streams:
            s.start()

    def stop(self) -> None:
        for s in self._streams:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        self._streams = []
        self._reports.close()

    def send_audio(self, seg: int, pcm: np.ndarray) -> None:
        self.player.push(seg, pcm)

    def send_control(self, msg: dict) -> None:
        self.player.control(msg)

    @property
    def busy(self) -> bool:
        return self.player.busy

    def _mic_callback(self, indata, frames, time_info, status) -> None:
        self.on_mic(indata[:, 0].copy())

    def _out_callback(self, outdata, frames, time_info, status) -> None:
        out, reports = self.player.pull(frames)
        outdata[:, 0] = out
        if reports:
            self._reports.put(reports)
