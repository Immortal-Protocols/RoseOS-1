"""Drive a running Rose server (python -m rose.app) with spoken turns and print
the event timeline. Speech is synthesized by the server (/api/say) and fed
into the real listener in real time.

    .venv/bin/python scripts/rose_drive.py "Hey Rose" "Text Sam that I'm running late"
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request

import websockets

BASE = "127.0.0.1:8091"
SHOW = {"speech_start", "turn_end", "user_turn", "tool_call", "rose_audio_start", "rose_turn", "barge",
        "metrics", "task", "noise", "error", "warning", "event_queued"}


def post(path: str, body: dict) -> None:
    req = urllib.request.Request(f"http://{BASE}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


async def main(lines: list[str], settle: float = 6.0) -> None:
    async with websockets.connect(f"ws://{BASE}/ws/events", max_size=None) as ws:
        t0 = time.monotonic()
        done_replay = False

        async def reader():
            nonlocal done_replay
            async for raw in ws:
                m = json.loads(raw)
                if m.get("kind") == "replay_done":
                    done_replay = True
                    continue
                if not done_replay or m.get("replay"):
                    continue
                k = m.get("kind")
                if k not in SHOW:
                    continue
                if k == "task":
                    t = m["task"]
                    if t.get("status") == "running" and t.get("steps"):
                        continue
                    m = {"kind": k, "id": t["id"], "status": t["status"], "result": t.get("result"),
                         "ms": t.get("ms")}
                body = {a: b for a, b in m.items() if a not in ("kind", "t", "replay")}
                print(f"{time.monotonic() - t0:7.2f}s {k:16} {json.dumps(body)[:220]}", flush=True)

        task = asyncio.create_task(reader())
        while not done_replay:
            await asyncio.sleep(0.05)
        for line in lines:
            if line.startswith("wait:"):
                await asyncio.sleep(float(line[5:]))
                continue
            print(f"{time.monotonic() - t0:7.2f}s >>> say: {line}", flush=True)
            post("/api/say", {"text": line})
            await asyncio.sleep(settle)
        await asyncio.sleep(3)
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
