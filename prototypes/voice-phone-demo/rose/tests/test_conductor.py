"""Conductor behaviour with the real brain and fake ears, voice and tasks.

    .venv/bin/python -m rose.tests.test_conductor [--unit-only]
Fakes keep this independent of the audio stack: the fake speaker "plays"
text at speaking pace, the fake listener only reports whether the user is
speaking, and fake phone tasks finish after two seconds.

unit_checks() runs first and loads no model; ``--unit-only`` stops after it.
Its look/claim checks drive the real conductor code with a character-level
fake brain and talker that replay scripted model output (look_checks).
"""

from __future__ import annotations

import collections
import os
import sys
import threading
import time
from dataclasses import dataclass, field

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from rose.brain import GEN_FAST, Brain, Mark, ToolCall, tool_segment
from rose.bus import Bus
from rose.conductor import (_PLAIN_TIME_Q, LOOK_CALL, LOOK_FILLER, LOOK_INTRO, NOT_RUNNING_HINT, SCREEN_HINT, Conductor,
                            Part, Reply, _bc_ish, _Capture, _claims_action, _claims_ui_action, _hide_thinking, _is_ack,
                            _is_info_question, _screen_claim_at, _tool_pieces, _unrelated_question,
                            _wants_phone_action)
from rose.runtime import Runtime, Sleep

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
    for text, wants in [("Can you try the other buttons?", True), ("Try the other buttons.", True),
                        ("Click the Code button.", True), ("Tap it.", True), ("Try the pasta at Nopa.", False)]:
        if _wants_phone_action(text) != wants:
            bad.append(f"_wants_phone_action({text!r}) != {wants}")
    look_checks(bad)
    if bad:
        raise SystemExit("FAILED unit checks:\n  " + "\n  ".join(bad))
    print("unit checks passed")


# ------------------------------------------------- the conductor, model-free
_SPECIAL = {"<|im_end|>": "\ue000", "<|endoftext|>": "\ue001", "<tool_call>": "\ue002", "</tool_call>": "\ue003"}
_IMAGE = 0xE0FF


class CharBrain:
    """Just enough Brain for the conductor: one token per character (special
    tokens are one token each), so scripted replies stream like model output."""

    im_end, tool_call_start = 0xE000, 0xE002
    eos = {0xE000, 0xE001}

    def encode(self, text):
        for k, v in _SPECIAL.items():
            text = text.replace(k, v)
        return [ord(ch) for ch in text]

    def decode(self, ids):
        text = "".join("<|vision_start|>[screenshot]<|vision_end|>" if i == _IMAGE else chr(i) for i in ids)
        for k, v in _SPECIAL.items():
            text = text.replace(v, k)
        return text


class ScriptSeq:
    """A talker cache of tokens; ``generate`` replays the next scripted reply."""

    def __init__(self, brain):
        self.brain, self.tokens, self.last_image = brain, [], None
        self.scripts = collections.deque()
        self.images = []  # the screenshots appended, in order

    def mark(self):
        return Mark(len(self.tokens), [], None, len(self.tokens))

    def rewind(self, mark):
        del self.tokens[mark.tokens:]

    def prefill(self, ids, chunk=256):
        self.tokens += ids
        yield None

    def append_text(self, text, chunk=256):
        yield from self.prefill(self.brain.encode(text))

    def append_image(self, image, max_side=None, **kw):
        self.tokens.append(_IMAGE)
        self.images.append(image)
        self.last_image = {"encode_ms": 1.0, "prefill_ms": 2.0, "max_unit_ms": 1.0}
        yield None
        return 360

    def generate(self, sampling, stop=None, cancel=None):
        stop = stop or self.brain.eos
        for t in self.brain.encode(self.scripts.popleft() if self.scripts else "<|im_end|>"):
            if cancel is not None and cancel():
                return
            self.tokens.append(t)
            yield t
            if t in stop:
                return

    def text(self):
        return self.brain.decode(self.tokens)


class LookPhone:
    """The fake Phone's data, with a look() that counts its screenshots."""

    def __init__(self):
        from PIL import Image
        from rose.phone import Phone

        self.inner, self.looks, self.image = Phone(), 0, Image.new("RGB", (1248, 1972), "white")
        self.data = self.inner.data

    def summary(self):
        return self.inner.summary()

    def look(self, view=None):
        self.looks += 1
        return self.image


def scripted_conductor(running_task=False):
    bus, events = Bus(), []
    bus.subscribe(events.append)
    tasks = FakeTasks()
    tasks.on_done = lambda t: None
    if running_task:
        tasks.tasks.append(FakeTask(1, "phone", "Try the other buttons in the Claude menu"))
    c = Conductor(None, CharBrain(), FakeListener(), FakeSpeaker(), tasks, LookPhone(), bus)
    c.talker = ScriptSeq(c.brain)
    c.talker.tokens += c.brain.encode("<|im_start|>system\nYou are Rose.<|im_end|>\n")
    return c, events


def turn(c, text, *scripts):
    """One user turn through the real conductor: ops run here, in order, and their
    items are handled as the conductor thread would. Returns the reply."""

    c.talker.scripts.extend(scripts)
    c._user_turn(text, t_user_end=None, listen={})
    r = c.reply
    end = time.monotonic() + 10
    while c._ops and time.monotonic() < end:
        for item in c._ops.popleft()():
            if isinstance(item, Sleep):
                time.sleep(item.seconds)
            elif item is not None:
                c._handle(*item)
    u = r.utterance
    if u is not None:  # heard in full: the next turn does not interrupt it
        u._played = len(u.text)
        u.done.set()
    c._finish_reply(r)
    while c._ops:
        for item in c._ops.popleft()():
            if item is not None and not isinstance(item, Sleep):
                c._handle(*item)
    return r


def said(r):
    return " ".join(r.utterance.text.split()) if r.utterance is not None else ""


def look_checks(bad):
    """Seeing the screen and owning up to phone actions, on transcript lines that
    once went wrong (a made-up hamburger menu, "Sure, I'll try the other buttons"
    with no task, a false "yes" to "Are you trying them?")."""

    c, _ = scripted_conductor()
    for text in ["What do you see on the screen?", "What's on my screen?", "Can you read that?", "Is it open?",
                 "Is there a Code button?", "what do you see", "Is Spotify open?", "Take a look at my screen."]:
        if c._tool_hint(text) != SCREEN_HINT:
            bad.append(f"screen question not routed to look_at_screen: {text!r}")
    for text in ["Is the coffee shop open?", "Can you read me my messages?", "What time is it?",
                 "Do you see what I mean?", "Tell me about the history of Rome.", "Text Sam that I'm on my way.",
                 "Are you trying them?", "Is it open on Sundays?", "Is there a button I can press to reset my router?",
                 "Take a look at this recipe idea: pasta with lemon.", "Have a look at my calendar tomorrow.",
                 "What's up there?", "What's on the screen at the movies tonight?"]:
        if c._tool_hint(text) == SCREEN_HINT:
            bad.append(f"not a screen question, routed to look_at_screen: {text!r}")
    # "Could you read that again?" repeats her own words, unless that reply looked.
    c.history.append({"role": "rose", "text": "Your dentist moved it to Tuesday at three.", "id": 1})
    if c._tool_hint("Could you read that again?") == SCREEN_HINT:
        bad.append("'Could you read that again?' after a spoken reply was routed to look_at_screen")
    c.history[-1]["looked"] = True
    if c._tool_hint("Could you read that again?") != SCREEN_HINT:
        bad.append("'Could you read that again?' after a look did not look again")
    c.history.clear()
    c._last_look_t = time.monotonic() - 5
    if "answer from that screenshot" not in c._tool_hint("Is there a Code button?"):
        bad.append("a follow-up right after a look did not answer from that screenshot")
    if c._tool_hint("What do you see now?") != SCREEN_HINT:
        bad.append("'... now?' after a look did not look again")
    c._last_look_t = time.monotonic() - 15  # the user may have changed the real phone's screen since
    c.phone.real = True
    if c._look_fresh():
        bad.append("a 15 s old screenshot counted as the real phone's current screen")
    c.phone.real = False
    if not c._look_fresh():
        bad.append("a 15 s old screenshot on the fake phone was not fresh")
    c._last_look_t = 0.0

    for text, n in [("I see a hamburger menu with Home, Profile, Settings, and Help.", 0),
                    ("Sure. There's a hamburger menu with Home, Profile, Settings, and Help.", 6),
                    ("Your screen shows the Claude app.", 0), ("It's showing the Settings page.", 0),
                    ("I can't see your screen right now.", None), ("I see what you mean.", None),
                    ("Want me to put it on your screen?", None), ("There are three pharmacies nearby.", None),
                    ("There's a setting for that.", None), ("Let me look.", None),
                    ("It's showing at seven tonight.", None),
                    # Menus, tabs, buttons and films off the phone are not the screen.
                    ("Yes, there's a full breakfast menu.", None),
                    ("Dune is showing at the AMC. It's showing a lot of promise.", None),
                    ("There are three buttons on most remotes.", None), ("I see the menu has pasta tonight.", None),
                    ("In the display case there are cakes.", None), ("There's a big screen at the sports bar.", None),
                    ("There's a tab open for you at the bar.", None),
                    ("There's a Code button in the Claude app.", 0)]:
        if _screen_claim_at(text) != n:
            bad.append(f"_screen_claim_at({text!r}) = {_screen_claim_at(text)} != {n}")
    for speech, claims in [("Sure, I'll try the other buttons.", True), ("Yes, I'm trying them now.", True),
                           ("Clicking it now.", True), ("Let me look.", False), ("I'll try to keep it short.", False),
                           ("No, I'm not doing anything on the phone right now.", False),
                           # Advice and how-to steps for the user are not claims.
                           ("Open Settings, then tap Display and turn on Dark mode.", False),
                           ("Try restarting your phone.", False), ("Sure, I'll try my best to remember that.", False),
                           ("Press and hold the power button.", False), ("Opening hours are nine to five.", False),
                           ("Sure, I'll tap Allow.", True), ("Okay, pressing Buy now.", True)]:
        if _claims_ui_action(speech) != claims:
            bad.append(f"_claims_ui_action({speech!r}) != {claims}")
    for speech in ["Sure, I'll try the other buttons.", "Yes, I'm trying them now.", "Clicking it now."]:
        if not _is_ack(speech):
            bad.append(f"acknowledgement not seen: {speech!r}")

    def forced(user, speech, running=False):
        cc, _ = scripted_conductor(running)
        r = Reply(1, user, None)
        p = Part()
        p.splitter.spoken = speech
        r.parts.append(p)
        return cc._should_force_call(r)

    for user, speech, running, want in [
            ("Try the other buttons.", "Sure, I'll try the other buttons.", False, True),
            ("Can you try the other buttons?", "Sure, I'll try the other buttons.", False, True),
            ("Are you trying them?", "Yes, I'm trying them now.", False, True),
            ("Are you trying them?", "Yes, I'm trying them now.", True, False),  # a task really is
            ("Click the Code button.", "Clicking it now.", False, True),
            ("Did it work?", "Clicking it now.", False, True),
            ("What do you see on the screen?", "Let me look.", False, False),
            ("Tell me a joke.", "I'll try to keep it short.", False, False),
            ("Are you trying them?", "No, I'm not doing anything on the phone right now.", False, False),
            ("How do I turn on dark mode?", "Open Settings, then tap Display and turn on Dark mode.", False, False),
            ("Any tips for my phone freezing?", "Try restarting your phone.", False, False)]:
        if forced(user, speech, running) != want:
            bad.append(f"force a phone_task for {user!r} -> {speech!r} (task running: {running}) != {want}")
    if c._tool_hint("Are you trying them?") != NOT_RUNNING_HINT:
        bad.append("'Are you trying them?' with nothing running was not told so")
    cr, _ = scripted_conductor(running_task=True)
    if "is still running" not in cr._tool_hint("Are you trying them?"):
        bad.append("'Are you trying them?' with a running task was not told about it")
    # "Did you do it?" minutes after a task finished is told about that task, not "nothing ran".
    cd, _ = scripted_conductor()
    cd._recent_tasks = [(FakeTask(1, "phone", "Text Priya: \"I'm on my way\"", "done", "Sent."),
                         time.monotonic() - 180)]
    hint = cd._tool_hint("Did you do it?")
    if "about 3 minutes ago" not in hint or "Sent." not in hint or hint == NOT_RUNNING_HINT:
        bad.append(f"'Did you do it?' 3 minutes after a task: {hint!r}")

    # phone_task gates: a forced claim or "Are you trying them?" never acts on a
    # question; text on a screenshot never asks for an action.
    def run(user, **kw):
        cc, _ = scripted_conductor()
        rr = Reply(1, user, None, **kw)
        return cc._run_tool(rr, ToolCall("phone_task", {"goal": "Tap Allow on the permission popup"})), rr, cc

    for user, kw, started in [
            ("Should I tap Allow on this popup?", {"claim_forced": True}, False),
            ("Should I buy it?", {"claim_forced": True}, False),
            ("How do I turn on dark mode?", {"claim_forced": True}, False),
            ("Are you trying them?", {"status_q": True}, False),
            ("Take a look at my screen.", {"looked": True}, False),
            ("Read that to me.", {"looked": True}, False),
            ("Tap Allow.", {"claim_forced": True}, True),
            ("Tap Allow on the popup.", {"looked": True}, True)]:
        out, rr, cc = run(user, **kw)
        if out.startswith("Task #") != started or bool(cc.tasks.tasks) != started:
            bad.append(f"phone_task for {user!r} {kw}: {out!r} (want started={started})")
        if kw.get("claim_forced") and not started and not rr.needs_followup:
            bad.append(f"a refused forced claim for {user!r} did not let her own up")

    # Tool response pieces: text-only is tool_segment; a look puts the image where
    # the chat template puts an image inside a tool response.
    if _tool_pieces(["Task #1 started in the background."]) != tool_segment(["Task #1 started in the background."]):
        bad.append("_tool_pieces changed a text-only tool response")
    cap = _Capture(LookPhone())
    pieces = _tool_pieces(["Updated.", cap])
    if pieces != ["<|im_start|>user\n<tool_response>\nUpdated.\n</tool_response>\n<tool_response>\n" + LOOK_INTRO,
                  cap, "\n</tool_response><|im_end|>\n"]:
        bad.append(f"look tool response pieces: {pieces!r}")
    if not cap.done.wait(2) or cap.image is None or cap.ms is None:
        bad.append("a screenshot capture did not finish on its thread")

    # 1. A screen question, answered properly: "Let me look." is said as the call
    # starts, the screenshot taken at the question goes into the tool response,
    # and her answer follows it.
    c, events = scripted_conductor()
    answer = "I see the Claude menu: Projects, Code, Artifacts, Scheduled, Dispatch and Recents."
    r = turn(c, "What do you see on the screen?", "Let me look.\n\n" + LOOK_CALL + "<|im_end|>",
             answer + "<|im_end|>")
    tail = c.talker.text().split("What do you see on the screen?", 1)[-1]
    want = (f"<|im_end|>\n{GEN_FAST}Let me look.\n\n{LOOK_CALL}<|im_end|>\n<|im_start|>user\n<tool_response>\n"
            f"{LOOK_INTRO}<|vision_start|>[screenshot]<|vision_end|>\n</tool_response><|im_end|>\n{GEN_FAST}"
            f"{answer}<|im_end|>")
    if tail != want:
        bad.append(f"screen question: cache tail {tail!r} != {want!r}")
    if said(r) != f"Let me look. {answer}":
        bad.append(f"screen question: said {said(r)!r}")
    if c.phone.looks != 1 or len(c.talker.images) != 1:
        bad.append(f"screen question: {c.phone.looks} screenshots taken, {len(c.talker.images)} appended (want 1, 1)")
    if not any(e["kind"] == "look" and e.get("ok") and e.get("tokens") == 360 for e in events) or not c._look_fresh():
        bad.append("screen question: no look event / the screenshot is not counted as fresh")
    if r.t_look_answer is None or r.t_look_ready is None:
        bad.append("screen question: look latency not measured")

    # 2. A follow-up answers from the screenshot already in her cache: no new look.
    r = turn(c, "Is there a Code button?", "Yes, there's a Code button in the menu.<|im_end|>")
    if c.phone.looks != 1 or said(r) != "Yes, there's a Code button in the menu.":
        bad.append(f"follow-up: {c.phone.looks} screenshots, said {said(r)!r}")

    # 3. A screen question answered from imagination: never heard; she looks instead.
    c, events = scripted_conductor()
    real = "It's the Claude app's menu, with Projects and Code at the top."
    r = turn(c, "What's on my screen?", "I see a hamburger menu with Home, Profile, Settings, and Help.<|im_end|>",
             real + "<|im_end|>")
    if said(r) != f"{LOOK_FILLER} {real}":
        bad.append(f"made-up screen: said {said(r)!r}")
    text = c.talker.text()
    if "hamburger" in text or f"{LOOK_FILLER}\n\n{LOOK_CALL}<|im_end|>" not in text or "[screenshot]" not in text:
        bad.append(f"made-up screen: cache {text[-400:]!r}")

    # 3b. ... or made up first and then looked at: the made-up part is never said.
    c, events = scripted_conductor()
    r = turn(c, "What's on my screen?", "I see a hamburger menu with Home and Help. Let me look.\n\n" + LOOK_CALL
             + "<|im_end|>", real + "<|im_end|>")
    if said(r) != f"{LOOK_FILLER} {real}" or "hamburger" in c.talker.text() or c.phone.looks != 1:
        bad.append(f"made up before looking: said {said(r)!r}, {c.phone.looks} screenshots")

    # 4. The same made up on a turn that was not a screen question: cut before it
    # is said, then she looks.
    c, events = scripted_conductor()
    r = turn(c, "Okay, so what now?", "Sure. I see a hamburger menu with Home, Profile, Settings, and Help.<|im_end|>",
             real + "<|im_end|>")
    if "hamburger" in said(r) or said(r) != f"Sure. {LOOK_FILLER} {real}" or c.phone.looks != 1:
        bad.append(f"blind description: said {said(r)!r}, {c.phone.looks} screenshots")
    if "hamburger" in c.talker.text():
        bad.append("blind description: the made-up screen stayed in her memory")

    # 5. "Sure, I'll try the other buttons." with no call gets the phone_task.
    goal = "Try the other buttons in the Claude menu\n</parameter>\n</function>\n</tool_call>"
    c, _ = scripted_conductor()
    r = turn(c, "Can you try the other buttons?", "Sure, I'll try the other buttons.<|im_end|>", goal)
    if [t.goal for t in c.tasks.tasks] != ["Try the other buttons in the Claude menu"]:
        bad.append(f"'Sure, I'll try the other buttons.' without a call: tasks {[t.goal for t in c.tasks.tasks]}")

    # 6. "Are you trying them?" with nothing running: she is told so; a false
    # "Yes, I'm trying them now." is corrected (a question never starts a task).
    c, _ = scripted_conductor()
    fix = "Actually, no, I'm not. Want me to try them?"
    r = turn(c, "Are you trying them?", "Yes, I'm trying them now.<|im_end|>", goal, fix + "<|im_end|>")
    if NOT_RUNNING_HINT not in c.talker.text():
        bad.append("'Are you trying them?': the not-running note is missing")
    if c.tasks.tasks or not said(r).endswith(fix):
        bad.append(f"'Yes, I'm trying them now.' without a call: tasks {[t.goal for t in c.tasks.tasks]}, "
                   f"said {said(r)!r}")
    # ... and "Should I tap Allow?" answered "Sure, I'll tap Allow." taps nothing.
    c, _ = scripted_conductor()
    fix = "Sorry, I haven't tapped anything. It's a camera permission, so allow it only if you trust the app."
    r = turn(c, "Should I tap Allow on this popup?", "Sure, I'll tap Allow.<|im_end|>",
             "Tap Allow on the popup\n</parameter>\n</function>\n</tool_call>", fix + "<|im_end|>")
    if c.tasks.tasks or not said(r).endswith(fix):
        bad.append(f"'Should I tap Allow?': tasks {[t.goal for t in c.tasks.tasks]}, said {said(r)!r}")
    c, _ = scripted_conductor(running_task=True)
    r = turn(c, "Are you trying them?", "Yes, it's still trying them.<|im_end|>")
    if len(c.tasks.tasks) != 1:
        bad.append("'Are you trying them?' with a task running started another one")

    # 7. Advice, everyday chat and place questions are answered as said: no forced
    # task, no cut, no screenshot.
    for user, answer in [("How do I turn on dark mode?", "Open Settings, then tap Display and turn on Dark mode."),
                         ("Any tips for my phone freezing?", "Try restarting your phone."),
                         ("Does that diner have breakfast?", "Yes, there's a full breakfast menu."),
                         ("What movie should I see?", "Dune is showing at the AMC. It's showing a lot of promise."),
                         ("Is it open on Sundays?", "Yes, it's open on Sundays from nine to five.")]:
        c, _ = scripted_conductor()
        r = turn(c, user, answer + "<|im_end|>")
        if said(r) != answer or c.tasks.tasks or c.phone.looks:
            bad.append(f"{user!r} -> said {said(r)!r}, tasks {[t.goal for t in c.tasks.tasks]}, "
                       f"{c.phone.looks} screenshots")

    # 8. A barge-in during an earlier part rewinds past a later part's screenshot:
    # it no longer counts as fresh (she would answer from a screenshot she lacks).
    c, _ = scripted_conductor()
    calc = ("Let me check.\n\n<tool_call>\n<function=calculate>\n<parameter=expression>\n2+2\n</parameter>\n"
            "</function>\n</tool_call><|im_end|>")
    c.talker.scripts.extend([calc, "Let me look.\n\n" + LOOK_CALL + "<|im_end|>",
                             "It's four, and Settings is open.<|im_end|>"])
    c._user_turn("What's two plus two, and what's open?", t_user_end=None, listen={})
    r = c.reply
    end = time.monotonic() + 10
    while c._ops and time.monotonic() < end:
        for item in c._ops.popleft()():
            if isinstance(item, Sleep):
                time.sleep(item.seconds)
            elif item is not None:
                c._handle(*item)
    if not c._look_fresh() or len(r.parts) < 3:
        bad.append(f"multi-part look: setup failed ({len(r.parts)} parts, fresh {c._look_fresh()})")
    u = r.utterance
    u._played = 3  # "Let" of part 0, then they spoke
    u.done.set()
    r.interrupted = True
    c._finish_reply(r, cancel=True)
    while c._ops:
        for item in c._ops.popleft()():
            if item is not None and not isinstance(item, Sleep):
                c._handle(*item)
    if c._look_fresh() or "[screenshot]" in c.talker.text():
        bad.append(f"multi-part look rewound: fresh {c._look_fresh()}, cache {c.talker.text()[-200:]!r}")

    # 9. A barge-in during a look's encode stops it: the image is taken back out
    # and a note stays, so the reply to the interruption does not wait on it.
    class SlowSeq(ScriptSeq):
        def append_image(self, image, max_side=None, **kw):
            for _ in range(5):
                self.tokens.append(_IMAGE)
                yield None
            self.images.append(image)
            return 360

    c, _ = scripted_conductor()
    c.talker = SlowSeq(c.brain)
    r = Reply(1, "What's on my screen?", None)
    cap = _Capture(c.phone)
    cap.done.wait(2)
    units = c._append_look(r, cap)
    items = [next(units), next(units)]
    r.cancelled = True
    items += list(units)
    looked = [x for x in items if isinstance(x, tuple) and x[0] == "looked"]
    if (_IMAGE in c.talker.tokens or c.talker.images or "they spoke first" not in c.talker.text()
            or not looked or looked[0][1][1].get("ok")):
        bad.append(f"cancelled look: cache {c.talker.text()!r}, items {items!r}")
    # ... and a capture that finished after they spoke is not appended at all.
    c, _ = scripted_conductor()
    c.talker = SlowSeq(c.brain)
    r = Reply(1, "What's on my screen?", None, cancelled=True)
    cap = _Capture(c.phone)
    cap.done.wait(2)
    list(c._append_look(r, cap))
    if _IMAGE in c.talker.tokens or "they spoke first" not in c.talker.text():
        bad.append(f"look after a barge-in: cache {c.talker.text()!r}")


def main():
    from rose.phone import Phone

    unit_checks()
    if "--unit-only" in sys.argv:
        return

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
