"""Demo entry point: voice loop + fake phone + dashboard at http://127.0.0.1:8090.

    .venv/bin/python -m demo.app [--design A|B] [--no-mic] [--no-speaker]
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response

from . import brain
from .orchestrator import Orchestrator
from .phone import Phone
from .voice_loop import VoiceLoop

WEB = Path(__file__).parent / "web"


class Hub:
    """Fan events out to dashboard websockets from any thread."""

    def __init__(self):
        self.history: collections.deque[dict] = collections.deque(maxlen=300)
        self.clients: set[asyncio.Queue] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def emit(self, kind: str, data: dict) -> None:
        msg = {"kind": kind, "t": time.time(), **data}
        if kind not in ("stats", "user_partial", "agent_partial"):
            with self._lock:
                self.history.append(msg)
        if self.loop is None:
            return
        for q in list(self.clients):
            self.loop.call_soon_threadsafe(q.put_nowait, msg)


def build(args) -> FastAPI:
    hub = Hub()
    phone = Phone()
    phone.listeners.append(lambda what: hub.emit("phone", {"version": phone.version}))
    orch = Orchestrator(phone, hub.emit)
    voice = VoiceLoop(
        orch, hub.emit, design=args.design, model_id=args.voice_model,
        mic=not args.no_mic, speaker=not args.no_speaker,
        input_device=args.input_device, output_device=args.output_device,
    )
    app = FastAPI()

    @app.on_event("startup")
    async def startup():
        hub.loop = asyncio.get_running_loop()
        if not brain.healthy():
            hub.emit("warning", {"message": f"brain not reachable at {brain.BRAIN_URL}"})
        voice.start()

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/phone.png")
    def phone_png(marks: int = 0):
        return Response(phone.screenshot_png(marks=bool(marks)), media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/state")
    def state():
        return {"status": voice.status, "design": voice.design, "stats": voice.stats,
                "brain": brain.healthy(), "phone_version": phone.version,
                "ui_tree": phone.ui_tree()}

    @app.post("/api/design/{design}")
    def set_design(design: str):
        voice.set_design(design.upper())
        return {"ok": True}

    @app.post("/api/new_conversation")
    def new_conversation():
        voice.new_conversation()
        return {"ok": True}

    @app.post("/api/reset_phone")
    def reset_phone():
        phone.reset()
        return {"ok": True}

    @app.post("/api/say")
    async def say(body: dict):
        voice.say(str(body.get("text", ""))[:300])
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        q: asyncio.Queue = asyncio.Queue()
        hub.clients.add(q)
        try:
            await socket.send_text(json.dumps({"kind": "hello", "status": voice.status,
                                               "design": voice.design, "history": list(hub.history)}))
            while True:
                await socket.send_text(json.dumps(await q.get(), default=str))
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(q)

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design", default="A", choices=["A", "B", "C"])
    parser.add_argument("--voice-model", default="mlx-community/NemotronLabs-VoiceChat-11B-8bit")
    parser.add_argument("--no-mic", action="store_true", help="feed silence; use the Say box")
    parser.add_argument("--no-speaker", action="store_true")
    parser.add_argument("--input-device", default=None)
    parser.add_argument("--output-device", default=None)
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    uvicorn.run(build(args), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
