"""One MLX thread with a cooperative priority scheduler.

MLX streams are per thread, so every model is loaded and run on this thread.
Work arrives as *jobs*: generators whose every ``next()`` does one bounded
unit of GPU work (one decode token, one prefill chunk, one TTS chunk, one ASR
step) and evaluates it. Between units the scheduler picks the most urgent
runnable job, so a background prefill never delays the next word Rose says
by more than one unit.

Priorities (lower runs first): LISTEN (ASR, end of turn) > SPEAK (TTS) >
TALK (the conversational LLM) > BACKGROUND (phone agent, deep thinking).
"""

from __future__ import annotations

import collections
import concurrent.futures
import heapq
import itertools
import threading
import time
from dataclasses import dataclass, field
from collections.abc import Generator
from typing import Any, Callable, Iterator

import mlx.core as mx

LISTEN, SPEAK, TALK, BACKGROUND = 0, 1, 2, 3
NAMES = {LISTEN: "listen", SPEAK: "speak", TALK: "talk", BACKGROUND: "background"}


@dataclass
class Sleep:
    """Yield from a job to step aside for ``seconds`` (other jobs run)."""

    seconds: float


class Cancelled(Exception):
    pass


@dataclass(eq=False)
class Job:
    name: str
    priority: int
    make_gen: Callable[[], Iterator[Any]]
    on_item: Callable[[Any], None] | None = None
    future: concurrent.futures.Future = field(default_factory=concurrent.futures.Future)
    gen: Iterator[Any] | None = None
    wake_at: float = 0.0
    submitted: float = field(default_factory=time.perf_counter)
    started: float | None = None
    units: int = 0
    busy_s: float = 0.0
    _cancelled: bool = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def result(self, timeout: float | None = None) -> Any:
        return self.future.result(timeout)


class Runtime:
    def __init__(self):
        self._cond = threading.Condition()
        self._ready: list[tuple[int, int, Job]] = []  # (priority, seq, job)
        self._sleeping: list[tuple[float, int, Job]] = []
        self._seq = itertools.count()
        self._thread = threading.Thread(target=self._run, name="mlx-runtime", daemon=True)
        self._stop = False
        self.current: Job | None = None
        self.busy_s = collections.Counter()
        self.waits_ms: dict[int, collections.deque] = {
            p: collections.deque(maxlen=200) for p in NAMES}
        self.thread_ident: int | None = None
        self._thread.start()

    # ------------------------------------------------------------- submit
    def submit(self, make_gen: Callable[[], Iterator[Any]], *, priority: int, name: str,
               on_item: Callable[[Any], None] | None = None) -> Job:
        job = Job(name=name, priority=priority, make_gen=make_gen, on_item=on_item)
        with self._cond:
            heapq.heappush(self._ready, (priority, next(self._seq), job))
            self._cond.notify()
        return job

    def call(self, fn: Callable[[], Any], *, priority: int, name: str = "call") -> Job:
        """Run ``fn`` once on the MLX thread as a single unit."""

        def gen():
            return (yield from _once(fn))

        return self.submit(gen, priority=priority, name=name)

    def run(self, fn: Callable[[], Any], *, priority: int = LISTEN, name: str = "run",
            timeout: float | None = None) -> Any:
        """Blocking ``call`` (from a non-MLX thread)."""

        if threading.get_ident() == self.thread_ident:
            return fn()
        return self.call(fn, priority=priority, name=name).result(timeout)

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()

    def on_thread(self) -> bool:
        return threading.get_ident() == self.thread_ident

    # --------------------------------------------------------------- loop
    def _next_job(self) -> Job | None:
        with self._cond:
            while True:
                if self._stop:
                    return None
                now = time.perf_counter()
                while self._sleeping and self._sleeping[0][0] <= now:
                    _, _, job = heapq.heappop(self._sleeping)
                    heapq.heappush(self._ready, (job.priority, next(self._seq), job))
                if self._ready:
                    return heapq.heappop(self._ready)[2]
                timeout = (self._sleeping[0][0] - now) if self._sleeping else None
                self._cond.wait(timeout)

    def _run(self) -> None:
        self.thread_ident = threading.get_ident()
        stream = mx.new_stream(mx.gpu)
        mx.set_default_stream(stream)
        try:
            # Keep model weights wired so an idle pause never pages them out
            # (stream_generate does this; our own loops must too).
            mx.set_wired_limit(mx.device_info()["max_recommended_working_set_size"])
        except Exception:
            pass
        while True:
            job = self._next_job()
            if job is None:
                return
            self.current = job
            requeue = self._step(job)
            self.current = None
            if requeue is None:
                continue
            with self._cond:
                if requeue > 0:
                    job.wake_at = time.perf_counter() + requeue
                    heapq.heappush(self._sleeping, (job.wake_at, next(self._seq), job))
                else:
                    # Same priority goes to the back: round robin.
                    heapq.heappush(self._ready, (job.priority, next(self._seq), job))

    def _step(self, job: Job) -> float | None:
        """Run one unit. Returns None when finished, else a sleep time (0 = ready)."""

        if job.cancelled:
            _close(job)
            if not job.future.done():
                job.future.set_exception(Cancelled(job.name))
            return None
        start = time.perf_counter()
        if job.started is None:
            job.started = start
            self.waits_ms[job.priority].append((start - job.submitted) * 1000)
        try:
            if job.gen is None:
                job.gen = job.make_gen()
                if not isinstance(job.gen, Generator):
                    # Plain function result: treat as finished.
                    value, job.gen = job.gen, None
                    job.future.set_result(value)
                    return None
            item = next(job.gen)
        except StopIteration as stop:
            if not job.future.done():
                job.future.set_result(stop.value)
            return None
        except BaseException as exc:  # noqa: BLE001 - delivered to the waiter
            if not job.future.done():
                job.future.set_exception(exc)
            return None
        finally:
            spent = time.perf_counter() - start
            job.units += 1
            job.busy_s += spent
            self.busy_s[job.priority] += spent
        if isinstance(item, Sleep):
            return max(item.seconds, 1e-4)
        if job.on_item is not None and item is not None:
            try:
                job.on_item(item)
            except Exception:  # a broken consumer must not kill the runtime
                import traceback

                traceback.print_exc()
        return 0.0

    def stats(self) -> dict:
        def pct(xs, q):
            xs = sorted(xs)
            return round(xs[int(q * (len(xs) - 1))], 1) if xs else None

        return {
            "busy_s": {NAMES[p]: round(v, 2) for p, v in self.busy_s.items()},
            "wait_ms": {NAMES[p]: {"p50": pct(w, 0.5), "p95": pct(w, 0.95)}
                        for p, w in self.waits_ms.items() if w},
            "queued": len(self._ready),
            "current": self.current.name if self.current else None,
        }


def _once(fn):
    return fn()
    yield  # pragma: no cover - makes this a generator


def _close(job: Job) -> None:
    if job.gen is not None and hasattr(job.gen, "close"):
        try:
            job.gen.close()
        except Exception:
            pass
