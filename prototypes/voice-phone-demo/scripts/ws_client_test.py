"""Talk to a running Rose server exactly the way the browser page does.

Streams 16 kHz Int16 mic frames over /ws/audio in real time (silence, then
speech synthesized with macOS `say`), plays Rose's 24 kHz audio on a
simulated real-time clock, and sends the same played/stopped reports as
rose/web/audio.js. Measures end of user speech -> first Rose audio at the
"browser", including after a long idle pause (GPU keepalive check).

    bash scripts/rose.sh &       # or: .venv/bin/python -m rose.app
    .venv/bin/python scripts/ws_client_test.py [--idle 60]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import websockets

MIC_SR, FRAME, PLAY_SR = 16_000, 512, 24_000


def say(text: str, voice: str = "Samantha") -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        subprocess.run(["say", "-v", voice, "--file-format=WAVE", "--data-format=LEI16@16000",
                        "-o", str(wav), text], check=True, timeout=60)
        audio, _ = sf.read(wav, dtype="float32")
    return audio


class Browser:
    """The audio.js player and capture loop, in Python."""

    def __init__(self, ws):
        self.ws = ws
        self.queue: list[list] = []  # [seg, n_samples, pos]
        self.first_audio: list[float] = []
        self.controls: list[tuple[float, dict]] = []
        self.holding = False

    async def receive(self):
        async for msg in self.ws:
            now = time.monotonic()
            if isinstance(msg, bytes):
                seg = struct.unpack("<I", msg[:4])[0]
                n = (len(msg) - 4) // 2
                if not self.queue:
                    self.first_audio.append(now)
                self.queue.append([seg, n, 0])
            else:
                m = json.loads(msg)
                self.controls.append((now, m))
                if m.get("type") == "pause":
                    self.holding = True
                elif m.get("type") in ("resume", "unduck"):
                    self.holding = False
                elif m.get("type") == "stop":
                    await asyncio.sleep(0.04)  # fade
                    head = self.queue[0] if self.queue else None
                    self.queue.clear()
                    self.holding = False
                    await self.ws.send(json.dumps({"type": "stopped", "seg": head[0] if head else -1,
                                                   "samples": head[2] if head else 0}))

    async def play(self):
        """Consume queued audio in real time; report every 100 ms like the worklet."""

        step = PLAY_SR // 10
        while True:
            await asyncio.sleep(0.1)
            if self.holding or not self.queue:
                continue
            budget = step
            while budget and self.queue:
                head = self.queue[0]
                take = min(budget, head[1] - head[2])
                head[2] += take
                budget -= take
                done = head[2] >= head[1]
                await self.ws.send(json.dumps({"type": "played", "seg": head[0], "samples": head[2],
                                               "done": done}))
                if done:
                    self.queue.pop(0)

    async def mic(self, audio: np.ndarray):
        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
        t0 = time.monotonic()
        for k, i in enumerate(range(0, len(pcm) - FRAME + 1, FRAME)):
            await self.ws.send(pcm[i:i + FRAME].tobytes())
            delay = t0 + (k + 1) * FRAME / MIC_SR - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)


async def main(args) -> None:
    base = f"127.0.0.1:{args.port}"
    lines = ["Hi Rose, what's the capital of Japan?", "Text Jordan that I'm in for soccer on Saturday.",
             "What time is it?"]
    clips = [say(t) for t in lines]
    events: list[dict] = []

    async with websockets.connect(f"ws://{base}/ws/events", max_size=None) as ev_ws, \
            websockets.connect(f"ws://{base}/ws/audio", max_size=None) as au_ws:
        await au_ws.send(json.dumps({"type": "hello", "captureRate": MIC_SR, "playRate": PLAY_SR}))
        browser = Browser(au_ws)

        async def read_events():
            async for raw in ev_ws:
                m = json.loads(raw)
                if not m.get("replay") and m.get("kind") in ("user_turn", "rose_turn", "tool_call", "task",
                                                            "metrics", "error", "warning"):
                    events.append(m)

        tasks = [asyncio.create_task(c) for c in (browser.receive(), browser.play(), read_events())]
        silence = np.zeros(int(MIC_SR * 1.0), np.float32)
        results = []
        for n, (text, clip) in enumerate(zip(lines, clips)):
            if n == len(lines) - 1 and args.idle:
                print(f"... idle {args.idle:.0f} s (mic streams silence) ...", flush=True)
                await browser.mic(np.zeros(int(MIC_SR * args.idle), np.float32))
            heard_before = len(browser.first_audio)
            await browser.mic(np.concatenate([silence[: MIC_SR // 2], clip]))
            t_end = time.monotonic()
            await browser.mic(silence)  # trailing silence so the turn can end
            for _ in range(100):
                if len(browser.first_audio) > heard_before:
                    break
                await browser.mic(np.zeros(FRAME * 2, np.float32))
            v2v = (browser.first_audio[heard_before] - t_end) * 1000 if len(browser.first_audio) > heard_before \
                else None
            await browser.mic(np.zeros(int(MIC_SR * 6), np.float32))  # let Rose finish
            results.append((text, v2v))
            print(f"{text!r}: first Rose audio {v2v:.0f} ms after the user stopped" if v2v else
                  f"{text!r}: no reply", flush=True)
        for t in tasks:
            t.cancel()
    print("\nRose said:")
    for m in events:
        if m["kind"] == "rose_turn":
            print("  -", m["text"])
        elif m["kind"] == "tool_call":
            print("  [tool]", m["name"], m.get("arguments"))
        elif m["kind"] in ("error", "warning"):
            print("  [!]", m.get("message"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--idle", type=float, default=60.0, help="idle seconds before the last turn")
    asyncio.run(main(ap.parse_args()))
