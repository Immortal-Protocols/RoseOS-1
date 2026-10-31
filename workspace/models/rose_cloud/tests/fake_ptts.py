"""A stand-in for the ``ptts.TTS`` engine (fixture: no Phonon model runs here).

It has the three calls PhononProvider uses: ``sample_rate``, ``add_voice(name, path)`` and
``stream(text, voice=name)`` as a context manager yielding float32 chunks. Each word becomes 0.25 s of a 220 Hz
tone in 80 ms chunks, so the audio is "voiced" for PR288's silence trimming. Delays let tests stall the first
chunk or any later one; ``open``/``closed`` count streams entered and left.
"""

from __future__ import annotations

import threading
import time

import numpy as np

RATE = 24000
CHUNK = 1920  # 80 ms


class FakeTTS:
    def __init__(self, first_delay_s=0.0, chunk_delay_s=0.0, rate=RATE):
        self.sample_rate = rate
        self.first_delay_s, self.chunk_delay_s = first_delay_s, chunk_delay_s
        self.voices: dict[str, str] = {}
        self.texts: list[str] = []
        self.opened = self.closed = 0
        self.chunks_made = 0
        self._lock = threading.Lock()

    def add_voice(self, name, path):
        self.voices[name] = str(path)

    def stream(self, text, voice):
        assert voice in self.voices, voice  # never a catalog voice
        self.texts.append(text)
        return _Stream(self, text)


class _Stream:
    def __init__(self, tts, text):
        self.tts, self.text = tts, text

    def __enter__(self):
        with self.tts._lock:
            self.tts.opened += 1
        return self._chunks()

    def __exit__(self, *exc):
        with self.tts._lock:
            self.tts.closed += 1
        return False

    def _chunks(self):
        words = max(1, len(self.text.split()))
        n = int(0.25 * RATE * words)
        t = np.arange(n) / RATE
        audio = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        audio = np.concatenate([np.zeros(2400, np.float32), audio, np.zeros(4800, np.float32)])
        for i in range(0, len(audio), CHUNK):
            time.sleep(self.tts.first_delay_s if i == 0 else self.tts.chunk_delay_s)
            with self.tts._lock:
                self.tts.chunks_made += 1
            yield audio[i:i + CHUNK]
