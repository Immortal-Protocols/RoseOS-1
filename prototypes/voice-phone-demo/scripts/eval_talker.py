"""Run scripted text conversations through the conductor (real brain, fake
audio and tasks) and print what Rose would say. For prompt tuning.

    .venv/bin/python scripts/eval_talker.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from rose.tests.test_conductor import FakeListener, FakeSpeaker, FakeTasks  # noqa: E402

CONVERSATIONS = [
    ["Hey Rose!", "Not much, just getting through the day.", "Thanks!"],
    ["What's the capital of Australia?", "Why not Sydney?"],
    ["If a recipe for four people needs one and a half cups of flour, how much do I need for seven?"],
    ["Do I have anything going on tonight?", "Who's that with?", "Remind me what Sam last said."],
    ["Can you text Jordan that I'm in for soccer on Saturday?"],
    ["Wait, hold on a second."],
    ["Set an alarm for six thirty tomorrow morning and turn on do not disturb."],
    ["I'm planning a trip to Japan for ten days in April, how should I split my time between Tokyo, Kyoto and Osaka?"],
    ["What time is it?", "How long until my dinner?"],
    ["Ugh, I'm so stressed about this design review."],
]


def main():
    from rose.phone import Phone
    from rose.brain import Brain
    from rose.bus import Bus
    from rose.conductor import Conductor
    from rose.runtime import Runtime

    bus = Bus()
    rt = Runtime()
    brain = rt.run(lambda: Brain(), timeout=300)
    out = []
    bus.subscribe(lambda m: out.append(m) if m["kind"] in ("rose_turn", "tool_call", "metrics") else None)
    for convo in CONVERSATIONS:
        speaker, tasks = FakeSpeaker(), FakeTasks()
        c = Conductor(rt, brain, FakeListener(), speaker, tasks, Phone(), bus)
        tasks.on_done = c.on_task_done
        c.start()
        print("\n" + "=" * 70)
        for text in convo:
            out.clear()
            c.inject_text_turn(text)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 40:
                time.sleep(0.05)
                r = c.reply
                if r is not None and r.finalized and not c._ops and not c._announce \
                        and time.monotonic() - t0 > 0.5:
                    if not tasks.running():
                        break
            time.sleep(0.3)
            print(f"USER: {text}")
            for m in out:
                if m["kind"] == "tool_call":
                    print(f"   [tool] {m['name']} {m['arguments']}")
                elif m["kind"] == "rose_turn":
                    print(f"ROSE: {m['text']}")
                elif m["kind"] == "metrics" and "llm_first_token_ms" in m["turn"]:
                    print(f"   [first token {m['turn']['llm_first_token_ms']} ms]")
        c.stop()


if __name__ == "__main__":
    main()
