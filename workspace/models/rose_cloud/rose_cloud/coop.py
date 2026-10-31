"""Wait for another thread without stalling PR288's cooperative runtime.

PR288's runtime (rose/runtime.py) runs every job on one thread, one bounded unit per ``next()``. On the Mac a unit
is GPU work. Here some units wait instead: for the next token from vLLM, for the next audio chunk from Phonon.
Those waits run on worker threads, but the job that consumes them cannot step aside the usual way (yielding
``Sleep``): its caller reads every yielded item as a token or an audio frame. So ``Waiter.wait`` stays inside the
waiting unit and, while it waits, runs single units of *strictly higher-priority* ready jobs exactly as the runtime
loop would (sleeping jobs that are due become ready, each job goes back to the ready or sleeping queue after its
unit). The listener (LISTEN) and the speaker (SPEAK) keep running while the talker (TALK) waits for the network,
and the talker keeps running while a background task waits. Lower and equal priorities wait, as they do behind
GPU work on the Mac.

This uses the runtime's internal queues (``_cond``, ``_ready``, ``_sleeping``, ``_seq``, ``_step``, ``current``) at
PR288's pinned head; ``compatible`` checks they are there, and tests run it against PR288's own Runtime. Off the
runtime thread (or with no runtime) it is a plain bounded wait.
"""

from __future__ import annotations

import heapq
import threading
import time
from typing import Any, Callable

RUNTIME_ATTRS = ('_cond', '_ready', '_sleeping', '_seq', '_step', 'current', 'on_thread')


def compatible(runtime: Any) -> bool:
    return all(hasattr(runtime, a) for a in RUNTIME_ATTRS)


class Waiter:
    def __init__(self, runtime: Any = None, poll_s: float = 0.005):
        if runtime is not None and not compatible(runtime):
            raise TypeError('not a PR288 rose.runtime.Runtime: missing ' +
                            ', '.join(a for a in RUNTIME_ATTRS if not hasattr(runtime, a)))
        self.runtime = runtime
        self.poll_s = poll_s
        self.nested_units = 0  # higher-priority units run while waiting (for tests and stats)
        self._wake = threading.Condition()

    def notify(self) -> None:
        """Called by a worker thread when it has something: ends an idle wait early."""
        with self._wake:
            self._wake.notify_all()
        rt = self.runtime
        if rt is not None:
            with rt._cond:
                rt._cond.notify_all()

    def wait(self, ready: Callable[[], bool], abort: Callable[[], bool] = lambda: False,
             timeout_s: float | None = None) -> bool:
        """True once ``ready()``; False on ``abort()``, the job's cancellation or the timeout."""
        rt = self.runtime
        me = rt.current if rt is not None and rt.on_thread() else None
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            if ready():
                return True
            if abort() or (me is not None and me.cancelled):
                return False
            if deadline is not None and time.monotonic() >= deadline:
                return False
            if me is not None and self._step_higher(rt, me):
                continue
            with self._wake:
                if not ready():
                    self._wake.wait(self.poll_s)

    def _step_higher(self, rt: Any, me: Any) -> bool:
        with rt._cond:
            now = time.perf_counter()
            while rt._sleeping and rt._sleeping[0][0] <= now:
                _, _, job = heapq.heappop(rt._sleeping)
                heapq.heappush(rt._ready, (job.priority, next(rt._seq), job))
            if not rt._ready or rt._ready[0][0] >= me.priority:
                return False
            job = heapq.heappop(rt._ready)[2]
        rt.current = job
        try:
            requeue = rt._step(job)
        finally:
            rt.current = me
        self.nested_units += 1
        if requeue is not None:
            with rt._cond:
                if requeue > 0:
                    job.wake_at = time.perf_counter() + requeue
                    heapq.heappush(rt._sleeping, (job.wake_at, next(rt._seq), job))
                else:
                    heapq.heappush(rt._ready, (job.priority, next(rt._seq), job))
        return True
