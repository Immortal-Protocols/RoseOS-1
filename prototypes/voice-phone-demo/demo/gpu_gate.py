"""Share one GPU between the real-time voice model and the brain.

Separate processes let macOS time-slice the GPU blindly, so the voice misses
its 80 ms deadline whenever the brain is busy. Here both models live in one
process and take turns explicitly:

* The voice thread holds the gate while it processes a frame.
* Every brain forward pass (``LanguageModel`` / ``VisionModel`` call) runs
  inside the gate and finishes its GPU work before releasing it.
* A brain unit may only start if its estimated cost fits before the next voice
  frame is due. Units that never fit (e.g. a large vision pass) run anyway
  after waiting ``max_starve_s``, costing one late voice frame.
* When the voice is frozen (tool call) there is no deadline: the brain runs
  at full speed.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import mlx.core as mx


class GpuGate:
    def __init__(self, enabled: bool = True, margin_s: float = 0.002, max_starve_s: float = 0.4,
                 overlap: tuple[str, ...] = ()):
        self.enabled = enabled
        # Unit kinds allowed to run concurrently with voice frames (small,
        # latency-bound work such as single decode tokens).
        self.overlap = overlap
        self.margin_s = margin_s
        self.max_starve_s = max_starve_s
        self._cond = threading.Condition()
        self._busy: str | None = None
        self._voice_waiting = False
        self.next_due: float | None = None  # perf_counter time of the next voice frame
        self.estimates: dict[str, float] = {}
        self.brain_units = 0
        self.forced_units = 0  # units that ran over a voice deadline after starving

    @contextmanager
    def voice(self):
        if not self.enabled:
            yield
            return
        with self._cond:
            self._voice_waiting = True
            while self._busy is not None:
                self._cond.wait()
            self._busy, self._voice_waiting = "voice", False
        try:
            yield
        finally:
            with self._cond:
                self._busy = None
                self._cond.notify_all()

    def set_next_due(self, when: float | None) -> None:
        """When the voice expects its next frame; ``None`` = voice is idle/frozen."""

        with self._cond:
            self.next_due = when
            self._cond.notify_all()

    @contextmanager
    def brain(self, kind: str):
        if not self.enabled or kind in self.overlap:
            start = time.perf_counter()
            yield
            self.brain_units += 1
            spent = time.perf_counter() - start
            prev = self.estimates.get(kind)
            self.estimates[kind] = spent if prev is None else 0.7 * prev + 0.3 * spent
            return
        waited_from = time.perf_counter()
        forced = False
        with self._cond:
            while True:
                if self._busy is None and not self._voice_waiting:
                    now = time.perf_counter()
                    est = self.estimates.get(kind, 0.015)
                    if self.next_due is None or now + est + self.margin_s <= self.next_due:
                        break
                    if now - waited_from > self.max_starve_s:
                        forced = True
                        break
                self._cond.wait(timeout=0.002)
            self._busy = "brain"
        start = time.perf_counter()
        try:
            yield
        finally:
            spent = time.perf_counter() - start
            prev = self.estimates.get(kind)
            self.estimates[kind] = spent if prev is None else 0.7 * prev + 0.3 * spent
            self.brain_units += 1
            self.forced_units += forced
            with self._cond:
                self._busy = None
                self._cond.notify_all()


def _flatten_arrays(tree, out: list) -> list:
    if isinstance(tree, mx.array):
        out.append(tree)
    elif isinstance(tree, (list, tuple)):
        for item in tree:
            _flatten_arrays(item, out)
    elif isinstance(tree, dict):
        for item in tree.values():
            _flatten_arrays(item, out)
    return out


def gate_brain_model(model, gate: GpuGate) -> None:
    """Route the brain's language and vision forward passes through ``gate``.

    Each call is evaluated inside the gate (outputs and updated cache state),
    so its GPU work cannot overlap a voice frame.
    """

    lm_cls = type(model.language_model)
    vision_cls = type(model.vision_tower)

    if getattr(lm_cls, "_gated", False):
        return
    lm_call, vision_call = lm_cls.__call__, vision_cls.__call__

    def gated_lm(self, *args, **kwargs):
        x = args[0] if args else kwargs.get("inputs")
        embeds = kwargs.get("inputs_embeds")
        length = (embeds.shape[1] if embeds is not None else x.shape[-1]) if (x is not None or embeds is not None) else 1
        kind = "decode" if length == 1 else f"prefill{length}"
        with gate.brain(kind):
            out = lm_call(self, *args, **kwargs)
            arrays = _flatten_arrays([out.logits], [])
            for c in kwargs.get("cache") or []:
                _flatten_arrays(getattr(c, "state", None), arrays)
            mx.eval(arrays)
        return out

    def gated_vision(self, *args, **kwargs):
        with gate.brain("vision"):
            out = vision_call(self, *args, **kwargs)
            mx.eval(_flatten_arrays(out, []))
        return out

    lm_cls.__call__ = gated_lm
    vision_cls.__call__ = gated_vision
    lm_cls._gated = True
