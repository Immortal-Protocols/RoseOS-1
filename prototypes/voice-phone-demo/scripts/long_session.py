"""A long conversation through the conductor (real brain; fake audio and phone
tasks): does Rose get slower or lose the thread as her cache grows?

    .venv/bin/python scripts/long_session.py [--turns 40]

Prints per-turn LLM time to first token (commit -> first token), the cache
size, and checks recall of facts mentioned early in the conversation. Runs the
app's GPU keepalive: without it, every pause over ~2 s makes the next prompt
step ~4x slower (macOS idles the GPU), which the app never sees.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from rose.tests.test_conductor import FakeListener, FakeSpeaker, FakeTasks  # noqa: E402

OPENERS = [
    "My name is Nathan and my sister's name is Ellie.",
    "I'm training for a half marathon in March.",
    "What's a good pace for a beginner half marathon?",
    "How many weeks of training would you suggest?",
]
FILLER = [
    "Tell me a fun fact about octopuses.", "What's the tallest mountain in Africa?",
    "Give me a quick tip for sleeping better.", "What's seven times thirteen?",
    "Recommend a short book for a flight.", "How do I make cold brew coffee?",
    "What's the capital of Canada?", "Any idea for a quick vegetarian dinner?",
    "Why is the sky blue, in one sentence?", "What's a good stretch after running?",
]
RECALL = [("What's my sister's name?", "ellie"), ("What am I training for?", "marathon"),
          ("And what's my name?", "nathan")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=40)
    args = ap.parse_args()

    from rose.app import GpuKeepalive
    from rose.brain import Brain
    from rose.bus import Bus
    from rose.conductor import Conductor
    from rose.phone import Phone
    from rose.runtime import Runtime

    bus = Bus()
    rt = Runtime()
    brain = rt.run(lambda: Brain(), timeout=300)
    GpuKeepalive(rt).start()
    metrics, turns = [], []
    bus.subscribe(lambda m: metrics.append(m["turn"]) if m["kind"] == "metrics" else None)
    bus.subscribe(lambda m: turns.append(m) if m["kind"] == "rose_turn" else None)
    speaker, tasks = FakeSpeaker(), FakeTasks()
    c = Conductor(rt, brain, FakeListener(), speaker, tasks, Phone(), bus)
    tasks.on_done = c.on_task_done
    c.start()
    script = OPENERS + [FILLER[i % len(FILLER)] for i in range(max(0, args.turns - len(OPENERS) - len(RECALL)))]
    script += [q for q, _ in RECALL]
    answers = {}
    for n, text in enumerate(script, 1):
        before = len(turns)
        c.inject_text_turn(text)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 60:
            time.sleep(0.05)
            r = c.reply
            if r is not None and r.finalized and not c._ops and len(turns) > before:
                break
        said = turns[-1]["text"] if len(turns) > before else ""
        answers[text] = said
        ttft = metrics[-1].get("llm_ttft_ms") if metrics else None
        print(f"{n:3d} ctx={len(c.talker.tokens):5d} tok  ttft={ttft} ms  | {text[:40]!r} -> {said[:70]!r}",
              flush=True)
    print("\nRecall:")
    for q, want in RECALL:
        print(f"  {q} -> {answers[q]!r}  {'OK' if want in answers[q].lower() else 'MISSED'}")


if __name__ == "__main__":
    main()
