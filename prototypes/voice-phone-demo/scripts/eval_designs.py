"""Compare Designs A, B and C on scripted spoken requests, in real time.

Each scenario starts a fresh conversation, speaks one request into the
(silent) virtual mic with macOS `say`, waits for the exchange to settle and
records what was spoken before/after the tool result, latency, phone state
and dropped audio. Needs the brain running (scripts/start_brain.sh).

    .venv/bin/python scripts/eval_designs.py [--designs A,B,C] [--only 2,4]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo import brain  # noqa: E402
from demo.orchestrator import Orchestrator  # noqa: E402
from demo.phone import Phone  # noqa: E402
from demo.voice_loop import VoiceLoop  # noqa: E402

SCENARIOS = [
    ("chat", "Hey, how's it going?", lambda p, said: True),
    ("think", "If a recipe for four people needs one and a half cups of flour, how much do I need for seven people?",
     lambda p, said: bool(re.search(r"five.eighths|2 5/8|2\.6|two point six", said, re.I))),
    ("think", "What is the capital of Australia?",
     lambda p, said: "canberra" in said.lower()),
    ("act", "Can you text Sam that I'm running ten minutes late?",
     lambda p, said: p.data["threads"]["Sam Lee"][-1][0] == "me" and "late" in p.data["threads"]["Sam Lee"][-1][1].lower()),
    ("act", "Set an alarm for six thirty tomorrow morning.",
     lambda p, said: any(a["time"] == "6:30 AM" and a["on"] for a in p.data["alarms"])),
    ("act", "Turn on do not disturb.",
     lambda p, said: p.data["settings"]["Do Not Disturb"]),
    ("act", "What's on my calendar this afternoon?",
     lambda p, said: "alex" in said.lower() and bool(re.search(r"\bfour\b|\b4\b", said.lower()))),
    ("chat", "Thanks, that's all for now.", lambda p, said: True),
    # From the first live session:
    ("chat", "I just want to chat.", lambda p, said: True),
    ("any", "Let's see what's in my messages so I can see what we can do this weekend.",
     lambda p, said: bool(re.search(r"jordan|soccer", said, re.I))),
    ("think", "Do I have everything I need to play pickup soccer with Jordan?",
     lambda p, said: bool(re.search(r"saturday|ten|10|repl", said, re.I))),
]


@dataclass
class Result:
    design: str
    kind: str
    utterance: str
    heard: str = ""
    tools: list = field(default_factory=list)
    decision: str = ""
    before_result: str = ""
    after_result: str = ""
    correct: bool = False
    right_route: bool = False
    first_speech_s: float | None = None
    result_spoken_s: float | None = None
    tool_s: float | None = None
    dropped_ms: int = 0
    words: int = 0
    timed_out: bool = False


class Recorder:
    def __init__(self):
        self.events: list[tuple[float, str, dict]] = []
        self.lock = threading.Lock()

    def emit(self, kind: str, data: dict) -> None:
        with self.lock:
            self.events.append((time.time(), kind, data))

    def since(self, t0: float):
        with self.lock:
            return [e for e in self.events if e[0] >= t0]


def wait_for(cond, timeout: float, step: float = 0.1) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def run_scenario(loop: VoiceLoop, rec: Recorder, phone: Phone, design: str, kind, text, check) -> Result:
    res = Result(design, kind, text)
    loop.new_conversation()
    time.sleep(0.5)
    if not wait_for(lambda: loop.status == "listening", 60):
        res.timed_out = True
        return res
    time.sleep(1.0)
    phone.reset()
    t0 = time.time()
    loop.say(text)
    wait_for(lambda: len(loop._sim) > 0, 10, 0.02)
    wait_for(lambda: len(loop._sim) == 0, 30, 0.02)
    t_end = time.time()  # user finished speaking (frames are paced in real time)
    base = len(loop._agent_text)

    # Settle: no new agent text, no tool running, nothing pending, for 4 s.
    last_len, last_change, deadline = base, time.time(), time.time() + 75
    inject_at = None
    while time.time() < deadline:
        time.sleep(0.2)
        n = len(loop._agent_text)
        if n != last_len:
            last_len, last_change = n, time.time()
        for t, k, d in rec.since(t0):
            if k == "injected" and inject_at is None:
                inject_at = (t, len(loop._agent_text))
                last_change = time.time()  # give the voice model time to speak the result
        busy = (
            loop.status == "tool"
            or not loop._results.empty()
            or getattr(loop, "_pending_decision", None) is not None
            or any(k == "tool_start" for _, k, _ in rec.since(t0)) and not any(k == "injected" for _, k, _ in rec.since(t0))
        )
        if not busy and time.time() - last_change > 4.0 and time.time() - t_end > 5.0:
            break
    else:
        res.timed_out = True

    events = rec.since(t0)
    said_all = loop._agent_text[base:]
    for t, k, d in events:
        if k == "tool_call":
            res.tools += [f"{c['name']}{' (decider)' if d.get('by') == 'decider' else ''}" for c in d["calls"][:1]]
        elif k == "decision":
            res.decision = d.get("mode", "")
        elif k == "tool_start" and design.startswith("B"):
            res.tools.append(f"{d['name']} (decider)")
        elif k == "tool_done":
            res.tool_s = round(d["ms"] / 1000, 1)
        elif k == "warning" and "dropped" in d.get("message", ""):
            res.dropped_ms += int(re.search(r"(\d+) ms", d["message"]).group(1))
        elif k == "user_partial":
            res.heard = d["text"] or res.heard
    first = next((t for t, k, d in events if k == "agent_partial" and d.get("text") and t > t_end), None)
    res.first_speech_s = round(first - t_end, 2) if first else None
    if inject_at:
        t_inj, pos = inject_at
        res.before_result = loop._agent_text[base:pos].strip()
        res.after_result = loop._agent_text[pos:].strip()
        after = next((t for t, k, d in events if k == "agent_partial" and t > t_inj and d.get("text")), None)
        res.result_spoken_s = round(after - t_end, 2) if after else None
    else:
        res.after_result = said_all.strip()
    res.words = len(said_all.split())
    used_tool = bool(res.tools)
    res.right_route = used_tool if kind == "any" else used_tool == (kind != "chat")
    res.correct = res.right_route and check(phone, res.after_result if inject_at else said_all)
    return res


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--designs", default="A,B,C",
                        help="designs to run; a suffix picks the voice prompt, e.g. A:v1,C:v2")
    parser.add_argument("--only", default="", help="comma-separated scenario numbers (1-based)")
    parser.add_argument("--out", default=str(ROOT / "out" / "eval"))
    parser.add_argument("--trace", action="store_true", help="print the event stream")
    args = parser.parse_args()
    if not brain.healthy():
        sys.exit("brain is not running: bash scripts/start_brain.sh")

    picks = [int(i) - 1 for i in args.only.split(",") if i] or range(len(SCENARIOS))
    designs = args.designs.split(",")
    os.environ["VOICE_PROMPT"] = designs[0].partition(":")[2] or "v2"
    rec, phone = Recorder(), Phone()
    loop = VoiceLoop(Orchestrator(phone, rec.emit), rec.emit, design=designs[0][0], mic=False, speaker=False)
    loop.start()
    if not wait_for(lambda: loop.status == "listening", 300):
        sys.exit(f"voice loop did not start: {loop.status}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []
    for label in designs:
        design, _, prompt = label.partition(":")
        os.environ["VOICE_PROMPT"] = prompt or "v2"
        loop.set_design(design)
        time.sleep(0.5)
        wait_for(lambda: loop.status == "listening" and loop.design == design, 120)
        for i in picks:
            kind, text, check = SCENARIOS[i]
            t_start = time.time()
            r = run_scenario(loop, rec, phone, label, kind, text, check)
            if args.trace:
                for t, k, d in rec.since(t_start):
                    if k not in ("stats", "agent_partial", "phone"):
                        print(f"  {t - t_start:6.2f} {k} {json.dumps(d)[:160]}")
            results.append(r)
            print(json.dumps(asdict(r)), flush=True)
            (out / "results.jsonl").open("a").write(json.dumps(asdict(r)) + "\n")

    print("\nsummary")
    for design in designs:
        rs = [r for r in results if r.design == design]
        chat_words = [r.words for r in rs if r.kind == "chat"]
        n = len(rs)
        spoke_first = sum(1 for r in rs if r.kind != "chat" and r.before_result)
        lat = [r.first_speech_s for r in rs if r.first_speech_s is not None]
        print(f"{design}: correct {sum(r.correct for r in rs)}/{n}, right route {sum(r.right_route for r in rs)}/{n}, "
              f"spoke before result {spoke_first}, median first speech "
              f"{sorted(lat)[len(lat) // 2] if lat else '-'} s, dropped {sum(r.dropped_ms for r in rs)} ms, "
              f"words per chat turn {sum(chat_words) / max(1, len(chat_words)):.0f}")


if __name__ == "__main__":
    main()
