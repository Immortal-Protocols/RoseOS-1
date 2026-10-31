"""Conductor behaviour with the real brain and fake ears, voice and tasks.

    .venv/bin/python -m rose.tests.test_conductor
Fakes keep this independent of the audio stack: the fake speaker "plays"
text at speaking pace, the fake listener only reports whether the user is
speaking, and fake phone tasks finish after two seconds.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from rose.brain import Brain
from rose.bus import Bus
from rose.conductor import (_PLAIN_TIME_Q, Conductor, Part, _bc_ish, _claims_action, _hide_thinking, _is_ack, _is_info_question,
                            _unrelated_question, _wants_phone_action)
from rose.runtime import Runtime

CHARS_PER_S = 15.0


class FakeUtterance:
    def __init__(self, speaker, rid):
        self.speaker, self.rid = speaker, rid
        self.text = ""
        self.finished = False
        self.done = threading.Event()
        self.first_audio_at = None
        self.started = None
        self.interrupted = False
        self._played = 0.0

    def add(self, text):
        if self.first_audio_at is None and text.strip():
            self.first_audio_at = time.monotonic() + 0.02
            self.started = self.first_audio_at
            self.speaker.current = self
        self.text += text

    def finish(self):
        self.finished = True

    def retract(self, text):
        """Like Utterance.retract: only text not yet played can be taken back."""
        text = text.strip()
        body = self.text.rstrip()
        if not text or not body.endswith(text) or self.played_chars() > len(body) - len(text):
            return False
        self.text = body[: len(body) - len(text)]
        return True

    def cancel(self):
        self._played = self.played_chars()
        self.done.set()

    def played_chars(self):
        if self.started is None:
            return 0
        if self.done.is_set():
            return self._played
        return min(len(self.text), (time.monotonic() - self.started) * CHARS_PER_S)

    def heard(self):
        n = int(self.played_chars())
        return self.text[:n].rsplit(" ", 1)[0] if n < len(self.text) else self.text

    def tick(self):
        if not self.done.is_set() and self.finished and self.played_chars() >= len(self.text):
            self._played = len(self.text)
            self.done.set()


class FakeSpeaker:
    def __init__(self):
        self.current = None
        self.ducked = False

    @property
    def audible(self):
        u = self.current
        if u is None:
            return False
        u.tick()
        return u.started is not None and not u.done.is_set()

    def utterance(self, rid, t_user_end):
        return FakeUtterance(self, rid)

    def duck(self):
        self.ducked = True

    def unduck(self):
        self.ducked = False

    def stop(self):
        if self.current is not None:
            self.current.interrupted = True
            self.current.cancel()


class FakeListener:
    in_speech = False


@dataclass
class FakeTask:
    id: int
    kind: str
    goal: str
    status: str = "running"
    result: str = ""


class FakeTasks:
    def __init__(self):
        self.tasks, self.on_done = [], None

    def _start(self, kind, goal):
        t = FakeTask(len(self.tasks) + 1, kind, goal)
        self.tasks.append(t)

        def finish():
            time.sleep(2.0)
            if t.status == "running":
                t.status, t.result = "done", f"Done: {goal}"
                self.on_done(t)
        threading.Thread(target=finish, daemon=True).start()
        return t

    def start_phone(self, goal):
        return self._start("phone", goal)

    def start_think(self, q, ctx):
        return self._start("think", q)

    def running(self):
        return [t for t in self.tasks if t.status == "running"]

    def update(self, i, s):
        return any(t.id == i and t.status == "running" for t in self.tasks)

    def cancel(self, i):
        for t in self.tasks:
            if t.id == i and t.status == "running":
                t.status = "cancelled"
                return True
        return False


@dataclass
class Ev:
    kind: str
    text: str = ""
    t: float = field(default_factory=time.monotonic)
    speech_ms: float = 0.0
    data: dict = field(default_factory=dict)


def unit_checks() -> None:
    """Pure helpers, no models: inputs that once went wrong."""

    bad = []
    for text, wants, info in [
            ("Ride. Play my chill mix.", True, False),  # a misheard "Right,"
            ("Should I turn off Bluetooth to save battery? Put it simply.", False, True),
            ("Should I turn off Bluetooth? Actually, just turn it off.", True, False),
            ("My boss sent a message about the meeting. Remind me what time it starts?", False, False),
            ("There is a text from my dentist. Show me.", False, False),
            ("Turn on do not disturb and set an alarm for seven fifteen tomorrow morning.", True, False),
            ("Where's the nearest pharmacy?", True, False)]:
        if (_wants_phone_action(text), _is_info_question(text)) != (wants, info):
            bad.append(f"request/info({text!r}) != {(wants, info)}")
    for text, plain in [("Actually, what time is it?", True), ("What time is it in Tokyo?", False),
                        ("How much is that in milliliters?", False)]:
        if bool(_PLAIN_TIME_Q.match(text)) != plain:
            bad.append(f"plain time question({text!r}) != {plain}")
    part = Part()
    if "".join(_hide_thinking(part, x) for x in ["<think>", "\n\n", "</think>", "\n\nIt's ", "Kilimanjaro."]).strip() != "It's Kilimanjaro.":
        bad.append("a <think> block split across tokens was spoken")
    if _hide_thinking(Part(), "It's 1:08 AM.\n</think>\n\nOkay.") != "It's 1:08 AM.\nOkay.":
        bad.append("a stray </think> was spoken")
    for text, unrelated in [("Actually, what time is it?", True), ("Is the coffee shop open?", False),
                            ("Can you wake me at seven?", False), ("How far is the bakery?", False)]:
        if _unrelated_question(text) != unrelated:
            bad.append(f"unrelated question({text!r}) != {unrelated}")
    for text, unrelated in [("Can you also tell Priya?", False), ("Yes, can you do that?", False),
                            ("Can you texam that I'm running late?", False), ("What about Priya?", False)]:
        if _unrelated_question(text) != unrelated:
            bad.append(f"unrelated question({text!r}) != {unrelated}")
    for speech in ["Sure, I'll get you walking directions now. Let me check that for you.",
                   "Sure! Turning on Do Not Disturb now.", "Got it. Texting Sam now."]:
        if not _is_ack(speech):
            bad.append(f"acknowledgement not seen: {speech!r}")
    if not _claims_action("It won't make much difference, but sure. I'll turn it off for you now."):
        bad.append("a claimed action after advice was not seen")
    if bool(_PLAIN_TIME_Q.match("Lunch with Priya, what time is it?")):
        bad.append("an event question was taken for a bare time question")
    for text, could_be in [("And n", True), ("MM Ham Hum", True), ("I'm home", False), ("actually what time", False)]:
        if _bc_ish(text) != could_be:
            bad.append(f"_bc_ish({text!r}) != {could_be}")
    if bad:
        raise SystemExit("FAILED unit checks:\n  " + "\n  ".join(bad))
    print("unit checks passed")


def main():
    from rose.phone import Phone

    unit_checks()

    bus = Bus()
    log = []
    bus.subscribe(lambda m: log.append(m) if m["kind"] not in ("status", "rose_partial") else None)
    errors = []
    bus.subscribe(lambda m: errors.append(m["message"]) if m["kind"] == "error" else None)
    rt = Runtime()
    brain = rt.run(lambda: Brain(), timeout=300)
    speaker, listener, tasks = FakeSpeaker(), FakeListener(), FakeTasks()
    c = Conductor(rt, brain, listener, speaker, tasks, Phone(), bus)
    tasks.on_done = c.on_task_done
    c.start()

    def wait_turn(timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r = c.reply
            if r is not None and r.finalized and not c._ops:
                return r
            time.sleep(0.05)
        show("timed out waiting for the reply to finish")
        r = c.reply
        print("reply:", r and {k: getattr(r, k, None) for k in ("id", "finalized", "generating", "speech", "speculative")},
              "ops:", len(c._ops))
        raise TimeoutError

    def show(title):
        print(f"\n== {title}")
        for m in log:
            if m["kind"] in ("user_turn", "rose_turn", "tool_call", "barge", "metrics", "event_queued", "error",
                             "warning"):
                print("  ", m["kind"], {k: v for k, v in m.items() if k not in ("kind", "t")})
        log.clear()

    c.inject_text_turn("Hey Rose, how's it going?")
    time.sleep(0.3); wait_turn(); show("greeting")

    c.inject_text_turn("Can you text Sam that I'm running ten minutes late?")
    time.sleep(0.3); wait_turn(); time.sleep(3.5)
    end = time.monotonic() + 15
    while time.monotonic() < end and not any(m["kind"] == "rose_turn" for m in log if m.get("id", 0) > 2):
        time.sleep(0.1)
    time.sleep(0.5); wait_turn(); show("phone task + announcement")

    c.inject_text_turn("What's on my calendar this afternoon?")
    time.sleep(0.3); wait_turn(); show("calendar")

    c.inject_text_turn("Tell me about the history of Rome.")
    while speaker.current is None or speaker.current.rid != c.reply.id or not speaker.audible:
        time.sleep(0.02)
    time.sleep(2.0)
    listener.in_speech = True
    c.on_listen(Ev("speech_start"))
    time.sleep(0.15)
    c.on_listen(Ev("partial", "actually what time"))
    time.sleep(0.4)
    listener.in_speech = False
    c.on_listen(Ev("turn_end", "Actually, what time is it?", data={"endpoint_ms": 250}))
    time.sleep(0.5); wait_turn(); show("barge-in")

    c.inject_text_turn("Text Mom")
    time.sleep(0.05)
    listener.in_speech = True
    c.on_listen(Ev("speech_start"))
    time.sleep(0.6)
    listener.in_speech = False
    c.on_listen(Ev("turn_end", "that I'll call her tonight.", data={"endpoint_ms": 250}))
    time.sleep(0.5); wait_turn(); time.sleep(3)
    end = time.monotonic() + 15
    while time.monotonic() < end and not c._announce and c.reply and not c.reply.finalized:
        time.sleep(0.1)
    time.sleep(3); wait_turn(); show("resume before speaking (merged turn) + task")

    print("\n== talker history (tail)")
    print(brain.decode(c.talker.tokens)[-2500:])
    print("\nmetrics:", c.turn_metrics)
    if errors:
        print(f"\nFAILED: {len(errors)} conductor error(s), first: {errors[0]}")
        raise SystemExit(1)
    print("\nno conductor errors")


if __name__ == "__main__":
    main()
