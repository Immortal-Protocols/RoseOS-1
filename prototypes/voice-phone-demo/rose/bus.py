"""Event bus: any thread emits, dashboards and tests subscribe.

Every event is a dict ``{"kind": str, "t": unix seconds, ...data}``. Events
are delivered to asyncio subscribers through ``loop.call_soon_threadsafe``
and to plain callbacks synchronously on the emitting thread (keep those
cheap). A bounded history lets a dashboard that connects late catch up.
"""

from __future__ import annotations

import asyncio
import collections
import threading
import time
from typing import Callable

# High-rate kinds that are not kept in history.
TRANSIENT = {"level", "partial", "rose_partial", "stats", "vad"}


class Bus:
    def __init__(self, history: int = 500):
        self.history: collections.deque[dict] = collections.deque(maxlen=history)
        self._queues: set[asyncio.Queue] = set()
        self._callbacks: list[Callable[[dict], None]] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def emit(self, kind: str, **data) -> dict:
        msg = {"kind": kind, "t": time.time(), **data}
        with self._lock:
            if kind not in TRANSIENT:
                self.history.append(msg)
            queues = list(self._queues)
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(msg)
            except Exception:
                import traceback

                traceback.print_exc()
        if self.loop is not None and queues:
            for q in queues:
                try:
                    self.loop.call_soon_threadsafe(_offer, q, msg)
                except RuntimeError:  # loop closed
                    pass
        return msg

    def subscribe_queue(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        with self._lock:
            self._queues.add(q)
        return q

    def unsubscribe_queue(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._queues.discard(q)

    def subscribe(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            self._callbacks.append(callback)

    def unsubscribe(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)


def _offer(q: asyncio.Queue, msg: dict) -> None:
    """Drop events for a subscriber that has stopped reading rather than raise."""

    try:
        q.put_nowait(msg)
    except asyncio.QueueFull:
        pass
