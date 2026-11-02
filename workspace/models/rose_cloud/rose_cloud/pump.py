"""A bounded queue between one worker thread and a runtime job (see coop.Waiter for the waiting side).

The worker produces items (token events from vLLM, audio chunks from Phonon) with ``put``, which blocks while the
queue is full, so a reader that stops reading pauses the producer instead of buffering without limit. ``close``
stops the worker: it sets the stop flag, runs the producer's stop hooks (closing its socket, which wakes a blocked
read), and joins the thread within a bound. Nothing put after ``close`` is ever returned.
"""

from __future__ import annotations

import collections
import threading
from typing import Any, Callable

END = object()  # the producer returned


class Pump:
    def __init__(self, waiter, produce: Callable[['Pump'], None], maxsize: int = 64, name: str = 'pump'):
        self.waiter, self.produce, self.maxsize = waiter, produce, maxsize
        self.stopped = threading.Event()
        self.on_stop: list[Callable[[], None]] = []
        self.error: BaseException | None = None
        self._items: collections.deque = collections.deque()
        self._cond = threading.Condition()
        self._done = False
        self.thread = threading.Thread(target=self._run, name=name, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            self.produce(self)
        except BaseException as exc:  # noqa: BLE001 - handed to the reader
            if not self.stopped.is_set():
                self.error = exc
        finally:
            with self._cond:
                self._done = True
                self._cond.notify_all()
            self.waiter.notify()

    def put(self, item: Any) -> bool:
        """Worker side. False once stopped (the worker should return)."""
        with self._cond:
            while len(self._items) >= self.maxsize and not self.stopped.is_set():
                self._cond.wait(0.05)
            if self.stopped.is_set():
                return False
            self._items.append(item)
        self.waiter.notify()
        return True

    def get(self, abort: Callable[[], bool] = lambda: False) -> tuple[bool, Any]:
        """Reader side: (True, item), (True, END) when the producer returned, or (False, None) on abort.
        Re-raises the producer's error."""
        if not self.waiter.wait(lambda: bool(self._items) or self._done, abort):
            return False, None
        with self._cond:
            if self._items:
                item = self._items.popleft()
                self._cond.notify_all()
                return True, item
        if self.error is not None:
            raise self.error
        return True, END

    def close(self, join_s: float = 5.0) -> bool:
        """Stop and join the worker. True if it finished within ``join_s``."""
        self.stopped.set()
        for hook in self.on_stop:
            try:
                hook()
            except Exception:
                pass
        with self._cond:
            self._items.clear()
            self._cond.notify_all()
        self.thread.join(join_s)
        return not self.thread.is_alive()
