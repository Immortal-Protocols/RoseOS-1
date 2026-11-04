"""Phone agent and deep-think tasks against the real brain and the fake phone.

    .venv/bin/python -m rose.tests.test_tasks [--rounds 2] [--only a,b,c,d,x,e,race,cancel,update,think]
    .venv/bin/python -m rose.tests.test_tasks --unit-only   # no model: the guards in code

``unit_checks`` (always run first, no model, a few seconds) drives the real TaskManager with a
scripted brain (one token per character, replies replayed from a script) on the fake phone and
on a fake phone that plays the real one with Rose's workspace: apps the goal names (launcher
taps, finish verification), the send guard and ``ready_to_send``, ``confirmed_send``,
``on_screen``, workspace refusals, the two-failures hint, and that the fake phone's prompt and
old ``start_phone(goal)`` call sites are unchanged.

Loads the brain once (~21 GB, on the runtime thread) and prefills the agent
prompt, then runs every scenario ``rounds`` times on a freshly reset phone:
(a) text Sam, (b) alarm 6:30 AM Gym, (c) Do Not Disturb, (d) open Maps (a
success that changes no data), extra goals x1-x6 (new conversation, alarm off, calendar event,
setting already on, starting from another screen, Camera), (e) b and c at
the same time, a list that shifts under the agent (race), cancel mid-task,
update mid-task, and deep think (one normal
run, one with a tiny budget). Checks the phone's
data and the task, and prints every step with its latency. Exit code 0 only
if every check passed; latency targets are reported, not enforced, because
other jobs may share the GPU.
"""

from __future__ import annotations

import argparse
import collections
import faulthandler
import os
import statistics
import sys
import threading
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from rose.brain import Brain
from rose.bus import Bus
from rose.phone import INITIAL, ActionResult, Phone
from rose.runtime import Runtime
from rose.tasks import (CALL_PREFIX, PHONE_SYSTEM, REAL_PHONE_SYSTEM, Task, TaskManager, _fake_app,
                        _final_control, _named_apps, _same_app)

STEP_TARGET_MS = 800
TEXT_TARGET_S = 4.0
OTHER = ("alarms", "calendar", "settings", "brightness", "contacts")


class Harness:
    def __init__(self):
        self.bus = Bus()
        self.rt = Runtime()
        t = time.monotonic()
        self.brain = self.rt.run(lambda: Brain(), timeout=300)
        self.load_s = time.monotonic() - t
        self.phone = Phone(self.bus)
        self.cond = threading.Condition()
        self.done: dict[int, list[str]] = {}
        self.step_log: list[tuple[float, int, int]] = []  # (time, task id, step n)
        self.bus.subscribe(self._on_event)
        self.tm = TaskManager(self.rt, self.brain, self.phone, self.bus, self._on_done)
        t = time.monotonic()
        self.tm.prepare()
        self.prepare_s = time.monotonic() - t
        self.failures: list[str] = []
        self.stats: dict[str, list[dict]] = {}

    def _on_event(self, msg: dict) -> None:
        if msg["kind"] == "task" and msg["task"]["status"] == "running" and msg["task"]["steps"]:
            t = msg["task"]
            self.step_log.append((time.monotonic(), t["id"], t["steps"][-1]["n"]))

    def _on_done(self, task: Task) -> None:
        with self.cond:
            self.done.setdefault(task.id, []).append(task.status)
            self.cond.notify_all()

    def wait(self, task: Task, timeout: float = 30.0) -> bool:
        with self.cond:
            return self.cond.wait_for(lambda: task.id in self.done, timeout)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        print(f"  {'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}")
        if not ok:
            self.failures.append(f"{name}: {detail}")
        return ok

    def record(self, key: str, task: Task) -> None:
        self.stats.setdefault(key, []).append({
            "s": (task.finished - task.started), "steps": [s["ms"] for s in task.steps],
            "gpu": [s.get("gpu_ms", 0) for s in task.steps], "status": task.status})


def show(task: Task) -> None:
    secs = (task.finished or time.time()) - task.started
    print(f"  #{task.id} {task.status} in {secs:.2f} s, {len(task.steps)} steps -> {task.result!r}")
    for s in task.steps:
        extra = f" prefill {s['prefill_ms']} gpu {s['gpu_ms']}" if "gpu_ms" in s else ""
        stop = f" [{s['stop']}]" if s.get("stop") else ""
        print(f"     {s['n']:>2} {s['ms']:>5} ms ({s['tokens']:>3} tok{extra}) {s['what']}{stop}")
        if "text" in s:
            print(f"        model wrote: {s['text']!r}")


# ----- phone checks: (ok, detail) from the phone's data and the task ----------
def _same(d: dict, *keys: str) -> bool:
    return all(d[k] == INITIAL[k] for k in keys)


def _new_messages(d: dict, name: str) -> list[tuple]:
    return d["threads"].get(name, [])[len(INITIAL["threads"].get(name, [])):]


def _threads_same_except(d: dict, name: str) -> bool:
    return ({k: v for k, v in d["threads"].items() if k != name}
            == {k: v for k, v in INITIAL["threads"].items() if k != name})


def check_a(d: dict, task: Task) -> tuple[bool, str]:
    new = _new_messages(d, "Sam Lee")
    text = new[0][1].lower() if len(new) == 1 and new[0][0] == "me" else ""
    ok = ("late" in text and ("ten" in text or "10" in text) and _threads_same_except(d, "Sam Lee")
          and _same(d, *OTHER) and task.status == "done")
    return ok, f"Sam got {new}"


def check_b(d: dict, task: Task) -> tuple[bool, str]:
    new = [a for a in d["alarms"] if a not in INITIAL["alarms"]]
    ok = (len(d["alarms"]) == 3 and len(new) == 1 and new[0]["time"] == "6:30 AM"
          and new[0]["label"].strip().lower() == "gym" and new[0]["on"]
          and _same(d, "threads", "calendar", "settings", "brightness") and task.status == "done")
    return ok, f"new alarms {new}"


def check_c(d: dict, task: Task) -> tuple[bool, str]:
    ok = (d["settings"] == {**INITIAL["settings"], "Do Not Disturb": True}
          and _same(d, "threads", "alarms", "calendar", "brightness") and task.status == "done")
    return ok, f"settings {d['settings']}"


def check_d(d: dict, task: Task) -> tuple[bool, str]:
    # The fake phone has had a working Maps since places were added: opening it is a success
    # that changes no data (it used to be "must fail gracefully"; x6 Camera still checks that).
    ok = d == INITIAL and task.status == "done" and "map" in task.result.lower()
    return ok, f"status {task.status}, data unchanged {d == INITIAL}"


def check_x1(d: dict, task: Task) -> tuple[bool, str]:
    new = _new_messages(d, "Priya Patel")
    text = new[0][1].lower() if len(new) == 1 and new[0][0] == "me" else ""
    ok = ("late" in text and _threads_same_except(d, "Priya Patel") and _same(d, *OTHER)
          and task.status == "done")
    return ok, f"Priya got {new}"


def check_x2(d: dict, task: Task) -> tuple[bool, str]:
    want = [{**a, "on": False} if a["time"] == "7:00 AM" else a for a in INITIAL["alarms"]]
    ok = d["alarms"] == want and _same(d, "threads", "calendar", "settings") and task.status == "done"
    return ok, f"alarms {d['alarms']}"


def check_x3(d: dict, task: Task) -> tuple[bool, str]:
    new = [e for e in d["calendar"] if e not in INITIAL["calendar"]]
    ok = (len(new) == 1 and "dentist" in new[0]["title"].lower() and new[0]["time"] == "3:00 PM"
          and _same(d, "threads", "alarms", "settings") and task.status == "done")
    return ok, f"new events {new}"


def check_x4(d: dict, task: Task) -> tuple[bool, str]:
    ok = (d["settings"] == {**INITIAL["settings"], "Do Not Disturb": True}
          and _same(d, "threads", "alarms", "calendar") and task.status == "done")
    return ok, f"settings {d['settings']}"


def check_x5(d: dict, task: Task) -> tuple[bool, str]:
    ok = (d["settings"] == {**INITIAL["settings"], "Wi-Fi": False}
          and _same(d, "threads", "alarms", "calendar") and task.status == "done")
    return ok, f"settings {d['settings']}"


def check_x6(d: dict, task: Task) -> tuple[bool, str]:
    ok = d == INITIAL and task.status == "failed" and "camera" in task.result.lower()
    return ok, f"status {task.status}, data unchanged {d == INITIAL}"


def _dnd_already_on(phone: Phone) -> None:
    with phone._lock:
        phone.data["settings"]["Do Not Disturb"] = True


# Extra goals: (goal, check, setup before the task, setup also counted as "initial").
EXTRA = {
    "x1": ("text Priya that I'll be five minutes late to lunch", check_x1, None),
    "x2": ("turn off my 7 AM alarm", check_x2, None),
    "x3": ("add Dentist at 3 PM to my calendar", check_x3, None),
    "x4": ("turn on Do Not Disturb", check_x4, _dnd_already_on),
    "x5": ("turn off Wi-Fi", check_x5, lambda p: p.open_app("Settings")),
    "x6": ("take a photo", check_x6, None),
}

GOALS = {
    "a": ("text Sam that I'm running ten minutes late", check_a),
    "b": ("set an alarm for 6:30 AM tomorrow labelled Gym", check_b),
    "c": ("turn on Do Not Disturb", check_c),
    "d": ("open Maps", check_d),
}


# ----- scenarios ------------------------------------------------------------
def run_goal(h: Harness, key: str, rnd: int) -> None:
    goal, check = GOALS[key]
    print(f"\n[{key} round {rnd}] {goal}")
    h.phone.reset()
    task = h.tm.start_phone(goal)
    finished = h.wait(task)
    show(task)
    if not h.check(f"{key}{rnd} finished", finished, "" if finished else "timed out"):
        h.tm.cancel(task.id)
        return
    h.record(key, task)
    h.check(f"{key}{rnd} phone state", *check(h.phone.data, task))
    h.check(f"{key}{rnd} on_done once", h.done.get(task.id) == [task.status], str(h.done.get(task.id)))
    secs = task.finished - task.started
    slow = [s["ms"] for s in task.steps if s["ms"] > STEP_TARGET_MS]
    if key == "a" and secs > TEXT_TARGET_S:
        print(f"  TARGET MISS: text Sam took {secs:.2f} s (> {TEXT_TARGET_S} s)")
    if slow:
        print(f"  TARGET MISS: steps over {STEP_TARGET_MS} ms: {slow}")


def run_extra(h: Harness, key: str, rnd: int) -> None:
    goal, check, setup = EXTRA[key]
    print(f"\n[{key} round {rnd}] {goal}")
    h.phone.reset()
    if setup:
        setup(h.phone)
    task = h.tm.start_phone(goal)
    finished = h.wait(task)
    show(task)
    if h.check(f"{key}{rnd} finished", finished, "" if finished else "timed out"):
        h.record("x", task)
        h.check(f"{key}{rnd} phone state", *check(h.phone.data, task))
    else:
        h.tm.cancel(task.id)


def run_concurrent(h: Harness, rnd: int) -> None:
    print(f"\n[e round {rnd}] b and c at once")
    h.phone.reset()
    h.step_log.clear()
    tb = h.tm.start_phone(GOALS["b"][0])
    tc = h.tm.start_phone(GOALS["c"][0])
    time.sleep(0.6)
    print("  describe() while running:\n    " + h.tm.describe().replace("\n", "\n    "))
    ok = h.wait(tb) and h.wait(tc)
    show(tb)
    show(tc)
    if not h.check(f"e{rnd} both finished", ok):
        return
    h.record("e", tb)
    h.record("e", tc)
    h.check(f"e{rnd} alarm set", *check_b({**h.phone.data, "settings": INITIAL["settings"]}, tb))
    h.check(f"e{rnd} DND on", *check_c({**h.phone.data, "alarms": INITIAL["alarms"]}, tc))
    order = [tid for _, tid, _ in h.step_log if tid in (tb.id, tc.id)]
    switches = sum(1 for x, y in zip(order, order[1:]) if x != y)
    overlap = min(tb.finished, tc.finished) - max(tb.started, tc.started)
    h.check(f"e{rnd} interleaved", switches >= 2 and overlap > 0,
            f"step order {order}, {switches} switches, overlap {overlap:.2f} s, "
            f"wall {max(tb.finished, tc.finished) - min(tb.started, tc.started):.2f} s")


def run_race(h: Harness, rnd: int) -> None:
    """Another task inserts an alarm after the agent read the Clock list: ids shift under it."""

    print(f"\n[race round {rnd}] the alarm list changes between observation and action")
    h.phone.reset()
    view = h.phone.session("stale")
    view.open_app("Clock")
    shown = view.element(2).label
    with h.phone._lock:
        h.phone.data["alarms"].insert(0, {"time": "5:00 AM", "label": "Early", "on": True})
    view.tap(2)  # resolved on the list as it was shown
    h.phone.close_session("stale")
    early, wake = h.phone.data["alarms"][0], h.phone.data["alarms"][1]
    h.check(f"race{rnd} stale id acts on what it showed", shown == "7:00 AM Wake up"
            and early["on"] and not wake["on"], str(h.phone.data["alarms"]))

    h.phone.reset()
    original = h.tm._observation
    injected = []

    def observation(task, session, turn):
        segment = original(task, session, turn)
        if not injected and "Clock - Alarms" in segment:
            with h.phone._lock:
                h.phone.data["alarms"].insert(0, {"time": "6:30 AM", "label": "Gym", "on": True})
            injected.append(True)
        return segment

    h.tm._observation = observation
    try:
        task = h.tm.start_phone("turn off my 7 AM alarm")
        finished = h.wait(task)
    finally:
        h.tm._observation = original
    show(task)
    alarms = {a["label"]: a["on"] for a in h.phone.data["alarms"]}
    h.check(f"race{rnd} right alarm", finished and bool(injected) and task.status == "done"
            and alarms == {"Gym": True, "Wake up": False, "Meds": False}, f"alarms {alarms}")


def run_cancel(h: Harness, rnd: int) -> None:
    print(f"\n[cancel round {rnd}] cancel after the first step")
    h.phone.reset()
    task = h.tm.start_phone("text Mom that I'll call her tonight after dinner")
    end = time.monotonic() + 15
    while not task.steps and task.status == "running" and time.monotonic() < end:
        time.sleep(0.005)
    t = time.monotonic()
    h.check(f"cancel{rnd} accepted", h.tm.cancel(task.id), f"status {task.status}")
    finished = h.wait(task, 5)
    latency = (time.monotonic() - t) * 1000
    steps = len(task.steps)
    time.sleep(1.0)
    show(task)
    h.check(f"cancel{rnd} status", finished and task.status == "cancelled",
            f"{task.status} {latency:.0f} ms after cancel()")
    h.check(f"cancel{rnd} stopped", len(task.steps) == steps and h.done.get(task.id) == ["cancelled"]
            and not _new_messages(h.phone.data, "Mom"), f"steps {steps}->{len(task.steps)}, "
            f"on_done {h.done.get(task.id)}, Mom {_new_messages(h.phone.data, 'Mom')}")
    h.check(f"cancel{rnd} refused twice", not h.tm.cancel(task.id) and not h.tm.update(task.id, "x"))
    early = h.tm.start_phone("turn on Do Not Disturb")
    h.tm.cancel(early.id)
    h.check(f"cancel{rnd} before start", h.wait(early, 5) and early.status == "cancelled"
            and h.done.get(early.id) == ["cancelled"], f"{early.status} {h.done.get(early.id)}")


def run_update(h: Harness, rnd: int) -> None:
    print(f"\n[update round {rnd}] change the message mid-task")
    h.phone.reset()
    task = h.tm.start_phone("text Jordan that I'm in for soccer on Saturday")
    end = time.monotonic() + 15
    while not task.steps and task.status == "running" and time.monotonic() < end:
        time.sleep(0.005)
    h.check(f"update{rnd} accepted", h.tm.update(task.id, "Also tell him I'll bring the ball."))
    h.wait(task)
    show(task)
    new = _new_messages(h.phone.data, "Jordan Park")
    text = new[-1][1].lower() if new else ""
    h.check(f"update{rnd} applied", task.status == "done" and len(new) == 1 and "ball" in text
            and ("soccer" in text or "saturday" in text), f"Jordan got {new}")


def run_think(h: Harness, rnd: int) -> None:
    print(f"\n[think round {rnd}]")
    task = h.tm.start_think("What is 17 times 23, and is that number prime?",
                            "Recent conversation:\nUser: I'm doing math homework.")
    h.wait(task, 60)
    show(task)
    h.check(f"think{rnd} answer", task.status == "done"
            and ("391" in task.result or "three hundred ninety-one" in task.result.lower())
            and task.result.count(".") <= 3, task.result)
    budget = h.tm.think_budget
    h.tm.think_budget = 48
    try:
        short = h.tm.start_think("Plan a three-day trip to Kyoto with a different theme each day.", "")
        h.wait(short, 30)
    finally:
        h.tm.think_budget = budget
    show(short)
    h.check(f"think{rnd} budget cut", short.status == "done" and bool(short.result)
            and "budget ran out" in short.steps[0]["what"], short.result)


def summary(h: Harness) -> None:
    print("\n== summary")
    print(f"brain load {h.load_s:.1f} s, agent prompt prefill {h.prepare_s * 1000:.0f} ms "
          f"({len(h.tm._agent.tokens)} tokens)")
    for key, runs in h.stats.items():
        steps = [ms for r in runs for ms in r["steps"]]
        gpu = [ms for r in runs for ms in r["gpu"]]
        print(f"{key}: runs {len(runs)}, task s {[round(r['s'], 2) for r in runs]}, "
              f"steps/run {[len(r['steps']) for r in runs]}, step ms median "
              f"{statistics.median(steps):.0f} max {max(steps)}, gpu ms median {statistics.median(gpu):.0f}")


# ----- offline: the guards in code, with a scripted brain ---------------------
_SPECIAL = {"<|im_end|>": "\ue000", "<|endoftext|>": "\ue001", "</think>": "\ue004"}


class ScriptBrain:
    """Just enough Brain for TaskManager: one token per character; each agent reply is replayed
    from ``scripts`` (the next one starts when an observation ending in CALL_PREFIX goes in)."""

    eos = {0xE000, 0xE001}

    def __init__(self):
        self.scripts: collections.deque[str] = collections.deque()
        self.prompts: list[str] = []  # every observation appended, in order

    def encode(self, text):
        for k, v in _SPECIAL.items():
            text = text.replace(k, v)
        return [ord(ch) for ch in text]

    def decode(self, ids):
        text = "".join(chr(i) for i in ids)
        for k, v in _SPECIAL.items():
            text = text.replace(v, k)
        return text

    def system_prefix(self, system, tools=None):
        return f"<|im_start|>system\n{system}<|im_end|>\n"

    def sequence(self, name="seq"):
        return ScriptSeq(self)


class ScriptSeq:
    def __init__(self, brain: ScriptBrain):
        self.brain, self.tokens, self.last_image = brain, [], None
        self.reply: list[int] = []
        self.pos = 0

    def fork(self, name):
        seq = ScriptSeq(self.brain)
        seq.tokens = list(self.tokens)
        return seq

    def mark(self):
        return len(self.tokens)

    def rewind(self, mark):
        del self.tokens[mark:]

    def prefill(self, ids, chunk=256):
        # Forced tool-call syntax must be exactly what the script says next.
        want = self.reply[self.pos:self.pos + len(ids)]
        assert self.pos == 0 and not self.reply or want == list(ids), (
            f"forced {self.brain.decode(ids)!r}, script has {self.brain.decode(want)!r}")
        self.pos += len(ids) if self.reply else 0
        self.tokens += ids
        yield None

    def append_text(self, text, chunk=256):
        ids = self.brain.encode(text)
        self.tokens += ids
        if text.endswith(CALL_PREFIX):
            self.brain.prompts.append(text)
            script = self.brain.scripts.popleft() if self.brain.scripts else "<|im_end|>"
            self.reply, self.pos = self.brain.encode(script), 0
        yield None

    def append_image(self, image, max_side=None, **kw):
        raise AssertionError("no screenshots offline")

    def generate(self, sampling, stop=None, cancel=None):
        for _ in range(sampling.max_tokens):
            tok = self.reply[self.pos] if self.pos < len(self.reply) else 0xE000
            self.pos += 1
            self.tokens.append(tok)
            yield tok
            if tok in self.brain.eos:
                return


def calls(*items: tuple) -> str:
    """One scripted agent reply: calls as (name, {param: value}) in schema order, then the turn's end."""

    text = "\n".join(f"<tool_call>\n<function={name}>\n"
                     + "".join(f"<parameter={k}>\n{v}\n</parameter>\n" for k, v in params.items())
                     + "</function>\n</tool_call>" for name, params in items)
    return text.removeprefix(CALL_PREFIX) + "<|im_end|>"


class WorkspacePhone:
    """The fake phone dressed as the real one with Rose's workspace (RealPhone's background API).

    Sessions are fake views; without ``on_screen`` they are "background" and Amazon is refused
    the way rose.background words it. ``workspace_app()`` is the app open in the workspace."""

    real = True
    vision = False

    def __init__(self, inner: Phone):
        self.inner = inner
        self.session_calls: list[tuple[str, bool]] = []
        self.ws_app: str | None = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def apps(self):
        from rose.phone import APPS
        return [{"label": a, "packageName": f"com.example.{a.lower()}"} for a in APPS + ["Amazon"]]

    def workspace_app(self, view=None):
        return self.ws_app

    def session(self, name, on_screen=False):
        self.session_calls.append((name, on_screen))
        return WorkspaceSession(self, self.inner.session(name), background=not on_screen)


class WorkspaceSession:
    def __init__(self, phone: WorkspacePhone, inner, background: bool):
        self.phone, self.inner, self.background = phone, inner, background

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def tap(self, mark):
        if self.background and self.phone.ws_app is None:
            return ActionResult(False, "Rose's workspace has no app open; use open_app first.")
        return self.inner.tap(mark)

    def open_app(self, app):
        if self.background and app.strip().lower() == "amazon":
            return ActionResult(False, "Android doesn't let Rose open Amazon in her workspace; nothing was opened.")
        r = self.inner.open_app(app)
        if r.ok and self.background:
            self.phone.ws_app = app.strip().title()
        return r


def _marks(phone: Phone, *steps) -> dict[str, int]:
    """Element ids by label after walking ``steps`` (an app, then labels to tap) on a scratch view."""

    view = phone.session("probe")
    view.open_app(steps[0])
    for label in steps[1:]:
        view.tap(next(e.mark for e in phone.screen("probe").elements if e.label == label))
    out = {e.label: e.mark for e in phone.screen("probe").elements if e.kind != "text"}
    phone.close_session("probe")
    return out


def _draft_is(d, text: str, where: str) -> bool:
    """ready_to_send names this draft and app (it also records which screen it was typed on)."""

    return isinstance(d, dict) and d.get("text") == text and d.get("where") == where


def unit_checks() -> list[str]:
    """The guards in code, offline (no model). Returns the failures."""

    bad: list[str] = []

    def expect(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'} unit {name}{': ' + detail if detail and not ok else ''}")
        if not ok:
            bad.append(f"unit {name}: {detail}")

    print("\n[unit] helpers")
    apps = ["Calculator", "Amazon", "Maps", "Phone", "Messages", "Clock", "Settings", "Claude"]
    for goal, want in [("Open Calculator and add 1 + 2 + ... + 50", ["Calculator"]),
                       ("Open the Amazon app", ["Amazon"]), ("open the amazon app", ["Amazon"]),
                       ("Open the Spotify app and play jazz", ["Spotify"]),  # named, not installed
                       ("Open Code in the Claude menu", ["Claude"]), ("open google maps", ["Maps"]),
                       ("text Sam on my phone that I'm late", []), ("set an alarm for 6:30 on Monday", []),
                       ("text Sam that I'm in Settings", []), ("turn on Do Not Disturb", []),
                       ("text Sam that I'm running ten minutes late", []), ("take a photo", []),
                       ("add Dentist at 3 PM to my calendar", []), ("Open the same app again", [])]:
        got = _named_apps(goal, apps)
        expect(f"named apps {goal!r}", got == want, f"{got} != {want}")
    for label, typed, want in [("Send", False, "send"), ("Send SMS", False, "send"), ("Post", False, "send"),
                               ("Reply", False, ""), ("Reply", True, "send"), ("Sending options", True, ""),
                               ("Posts", True, ""), ("Sent", True, ""), ("Buy now", False, "purchase"),
                               ("Place your order", False, "purchase"), ("Pay", False, "purchase"),
                               ("Proceed to checkout", False, "purchase"), ("PayPal", False, ""),
                               ("Submit", False, "final"), ("Publish", False, "final"), ("Add to cart", False, ""),
                               ("Message", True, ""), ("Send money", False, "purchase")]:
        got = _final_control(label, typed)
        expect(f"final control {label!r} typed={typed}", got == want, f"{got!r} != {want!r}")
    # A calculation's success must show its answer (the Pixel 10 run that "calculated" 1225).
    from rose.tasks import _press_keys, _Run, _unseen_number
    from rose.phone import ActionResult, Element

    class _Shown:
        def __init__(self, tree: str):
            self.tree = tree

        def ui_tree(self) -> str:
            return self.tree

    calc = "Open the Calculator app and calculate the sum of numbers from 1 to 50."
    for goal, summary, tree, ok in [
            (calc, "I've calculated the sum, which is 1225.", 'Screen: Calculator\n  text "0"', False),
            (calc, "The sum from 1 to 50 is 1,275.", 'Screen: Calculator\n  text "50×51÷2"\n  text "1275"', True),
            ("What's 12 times 3 on the calculator?", "12 times 3 is 36.", 'text "36"', True),
            ("Set an alarm for 6:30", "Your alarm is set for 6:30.", "", True),
            ("Add milk to my shopping list", "I added milk to your list.", "", True),
            # Live, 2026-11-01: "add" in an Amazon goal made a calculation of it.
            ("Search Amazon for a USB-C cable and get the first two prices. Do not add anything to the cart.",
             "The first is $9.99 with 4.5 stars.", 'text "USB-C Cable"', True),
            # A rounded answer of what the screen shows (33.984).
            ("Open Calculator app and calculate 18% tip on 86.40, split by 3", "Each pays $33.98.",
             'Screen: Calculator\n  text "86.40×1.18÷3"\n  text "33.984"', True),
            ("Open Calculator app and calculate 18% tip on 86.40, split by 3", "Each pays $5.18.",
             'Screen: Calculator\n  text "33.984"', False),
            # Live: the summary repeats the inputs the way they were said, not as the screen shows them.
            ("Open the calculator app and work out an eighteen percent tip on eighty-six dollars and forty cents",
             "An 18% tip on $86.40, split three ways, is $5.18 each.",
             'Screen: Calculator\n  text "86.4×0.18÷3"\n  text "5.184"', True),
            # Live: a wrong answer next to an input the screen happens to show.
            ("In Calculator, calculate an eighteen percent tip on eighty-six dollars and forty cents",
             "The 18% tip on $86.40 is $15.55, and the total split by three is $34.15.",
             'Screen: Calculator\n  text "86×0.18+86÷3"\n  text "44.1466"\n  text "18"', False)]:
        why, _ = _unseen_number(goal, summary, _Shown(tree), _Run())
        expect(f"unseen number {summary!r}", (why == "") == ok, why)
    run = _Run()
    _unseen_number(calc, "It is 1225.", _Shown(""), run)
    expect("unseen number fails the second time", _unseen_number(calc, "It is 1225.", _Shown(""), run)[1] is not None)

    class _Keys:
        """A calculator screen: tapping a key appends it to the display."""

        def __init__(self):
            names = [str(d) for d in range(10)] + ["plus", "minus", "times", "divide", "equals", "point", "Send"]
            self.screen = type("S", (), {"elements": [Element(f"k{i}", "button", n, (0, 0, 1, 1), mark=i + 1)
                                                      for i, n in enumerate(names)]})()
            self.tapped: list[str] = []

        def tap(self, mark):
            self.tapped.append(self.screen.elements[int(mark) - 1].label)
            return ActionResult(True, "Tapped.", changed=True)

    keys = _Keys()
    r = _press_keys(keys, "50×51÷2=")
    expect("press_keys taps each key by name", r.ok and keys.tapped == ["5", "0", "times", "5", "1", "divide", "2",
                                                                       "equals"], f"{r.message} {keys.tapped}")
    keys = _Keys()
    expect("press_keys refuses non-keys", not _press_keys(keys, "Send").ok and not keys.tapped, str(keys.tapped))
    keys = _Keys()
    r = _press_keys(keys, "2^3")
    expect("press_keys refuses unknown keys", not r.ok and not keys.tapped, r.message)
    expect("same app", _same_app("Google Maps", "Maps") and _same_app("the Claude app", "Claude")
           and not _same_app("Maps", "Messages"))
    expect("fake app", _fake_app(("thread", "Sam Lee")) == "Messages" and _fake_app(("home",)) == "the home screen"
           and _fake_app(("unavailable", "Camera")) == "Camera")
    expect("real prompt: sends need a yes", "Tap Send only when the Goal says the user confirmed sending" in REAL_PHONE_SYSTEM
           and "do not tap Send" not in REAL_PHONE_SYSTEM and "start with open_app" in REAL_PHONE_SYSTEM)
    expect("fake prompt keeps tapping Send", "tap Send and finish" in PHONE_SYSTEM)

    rt, bus = Runtime(), Bus()
    brain = ScriptBrain()
    events: dict[int, threading.Event] = {}  # by id(task): task ids restart in every TaskManager
    events_lock = threading.Lock()

    def done(task: Task) -> threading.Event:
        with events_lock:
            return events.setdefault(id(task), threading.Event())

    def run(tm: TaskManager, goal: str, *replies: str, **kw) -> Task:
        brain.scripts.extend(replies)
        task = tm.start_phone(goal, **kw) if kw else tm.start_phone(goal)
        ok = done(task).wait(30)
        brain.scripts.clear()
        if not ok:
            tm.cancel(task.id)
        return task

    def results(task: Task) -> list[str]:
        return [a["result"] for s in task.steps for a in s.get("actions", [])]

    try:
        fake = Phone(bus, vision=False)
        tm = TaskManager(rt, brain, fake, bus, lambda t: done(t).set())
        home = {e.label: e.mark for e in fake.screen().elements if e.kind != "text"}
        thread = _marks(fake, "Messages", "Sam Lee")
        fake.reset()

        print("\n[unit] fake phone: old call sites and prompt")
        t = run(tm, "turn on Do Not Disturb", calls(("finish", {"summary": "Done.", "success": "false"})))
        first = brain.prompts[-1]
        expect("start_phone(goal) still works", t.status == "failed" and not t.confirmed_send and not t.on_screen,
               f"{t.status}")
        expect("fake prompt unchanged", "Goal: turn on Do Not Disturb\nNow: " in first and "Screen:" not in
               first.split("Now:")[0] and "confirmed" not in first, repr(first[-400:]))
        d = t.to_dict()
        expect("to_dict fields", all(k in d for k in ("ready_to_send", "confirmed_send", "on_screen", "background"))
               and d["ready_to_send"] is None, str(d))
        expect("guard off on the fake phone", tm.guard_sends is False)

        print("\n[unit] apps the goal names")
        fake.reset()
        t = run(tm, "Open the Clock app",
                calls(("tap", {"id": home["Maps"]})),  # the wrong icon on the home screen
                calls(("finish", {"summary": "I opened the Clock app for you.", "success": "true"})),
                calls(("open_app", {"app": "Clock"})),
                calls(("finish", {"summary": "I opened the Clock app for you.", "success": "true"})))
        res = results(t)
        expect("launcher tap of another app refused", any("not Maps" in r and "open_app with Clock" in r for r in res)
               and fake.route_of("task%d" % t.id) != ("maps",), str(res))
        expect("finish refused while the home screen shows",
               any("the screen shows the home screen" in r for r in res), str(res))
        expect("finish accepted in Clock", t.status == "done" and t.result == "I opened the Clock app for you.",
               f"{t.status}: {t.result}")
        fake.reset()
        t = run(tm, "Open the Clock app",
                calls(("open_app", {"app": "Maps"})),
                calls(("finish", {"summary": "I opened the Clock app for you.", "success": "true"})),
                calls(("finish", {"summary": "I opened the Clock app for you.", "success": "true"})))
        expect("false success in the wrong app ends failed", t.status == "failed"
               and t.result == "I couldn't get Clock open; Maps is open instead.", f"{t.status}: {t.result}")
        fake.reset()
        t = run(tm, "Open the Clock app", calls(("tap", {"id": 98})), calls(("tap", {"id": 97})),
                calls(("finish", {"summary": "I couldn't.", "success": "false"})))
        res = results(t)
        expect("two failures in a row: open_app hint", "open_app" not in res[0] and "use open_app with Clock" in res[1],
               str(res))
        fake.reset()
        t = run(tm, "turn on Do Not Disturb", calls(("tap", {"id": home["Settings"]})),
                calls(("finish", {"summary": "Done.", "success": "true"})))
        expect("no app named: launcher taps and finishes as before", t.status == "done", f"{t.status} {results(t)}")

        print("\n[unit] sends (the real phone's guard)")
        tm.guard_sends = True
        draft = "I'm running ten minutes late"
        typing = calls(("type_text", {"id": thread["Message"], "text": draft}), ("tap", {"id": thread["Send"]}),
                       ("finish", {"summary": "I told Sam you're running late.", "success": "true"}))
        to_sam = [calls(("open_app", {"app": "Messages"})), calls(("tap", {"id": _marks(fake, "Messages")["Sam Lee"]}))]
        fake.reset()
        t = run(tm, "text Sam that I'm running ten minutes late", *to_sam, typing)
        expect("Send refused without a yes", not _new_messages(fake.data, "Sam Lee"), str(_new_messages(fake.data, "Sam Lee")))
        expect("ready_to_send", t.status == "done" and _draft_is(t.ready_to_send, draft, "Messages")
               and t.result == "Your message is typed in Messages, ready to send."
               and t.to_dict()["ready_to_send"] == t.ready_to_send, f"{t.status}: {t.result} {t.ready_to_send}")
        fake.reset()
        t = run(tm, "text Sam that I'm running ten minutes late", *to_sam, typing, confirmed_send=True)
        new = _new_messages(fake.data, "Sam Lee")
        expect("confirmed_send taps Send once", t.status == "done" and new == [("me", draft)] and t.ready_to_send is None
               and t.confirmed_send, f"{t.status}: {t.result} {new}")
        fake.reset()
        t = run(tm, "text Sam that I'm running ten minutes late", *to_sam,
                calls(("type_text", {"id": thread["Message"], "text": draft})),
                calls(("finish", {"summary": "Your message to Sam is ready.", "success": "true"})))
        expect("typed and finished: ready_to_send too", t.status == "done" and not _new_messages(fake.data, "Sam Lee")
               and _draft_is(t.ready_to_send, draft, "Messages"), f"{t.status} {t.ready_to_send}")
        tm.guard_sends = False

        print("\n[unit] Rose's workspace and on_screen")
        ws = WorkspacePhone(Phone(bus, vision=False))
        tm2 = TaskManager(rt, brain, ws, bus, lambda t: done(t).set())
        expect("guard on for a real phone", tm2.guard_sends is True)
        t = run(tm2, "Open the Amazon app", calls(("open_app", {"app": "Amazon"})),
                calls(("open_app", {"app": "Maps"})))
        first = brain.prompts[-2] if len(brain.prompts) >= 2 else ""
        expect("workspace refusal ends the task", t.status == "failed" and "in her workspace" in t.result
               and len(t.steps) == 1 and t.background and ws.session_calls[-1][1] is False,
               f"{t.status}: {t.result} steps {len(t.steps)} {ws.session_calls}")
        expect("workspace prompt note", "Screen: Rose's own workspace" in brain.prompts[-1].split("Now:")[0]
               or "Screen: Rose's own workspace" in first, repr(brain.prompts[-1][-300:]))
        ws.ws_app = None
        t = run(tm2, "Open the Clock app", calls(("tap", {"id": 99})),
                calls(("open_app", {"app": "Clock"})),
                calls(("finish", {"summary": "Clock is open in my workspace.", "success": "true"})))
        expect("finish checks the workspace app", t.status == "done" and ws.ws_app == "Clock", f"{t.status}: {t.result}")
        expect("a recoverable workspace failure does not end the task", len(t.steps) == 3, str(results(t)))
        ws.ws_app = "Maps"
        t = run(tm2, "Open the Clock app", calls(("finish", {"summary": "Done.", "success": "true"})),
                calls(("finish", {"summary": "Done.", "success": "true"})))
        expect("workspace on another app: success refused", t.status == "failed" and "Maps is open" in t.result,
               f"{t.status}: {t.result}")
        t = run(tm2, "Open the Amazon app", calls(("open_app", {"app": "Amazon"})),
                calls(("finish", {"summary": "Done.", "success": "false"})), on_screen=True)
        prompt = brain.prompts[-2]
        expect("on_screen opens the visible screen", ws.session_calls[-1][1] is True and t.on_screen
               and not t.background and "Screen: the user's own screen" in prompt, f"{ws.session_calls[-1]} {t.to_dict()}")
        t = run(tm2, "text Sam that I'm late", calls(("finish", {"summary": "x", "success": "false"})),
                confirmed_send=True)
        expect("confirmed note on the real phone", "The user confirmed sending" in brain.prompts[-1], "")

        # The whole stack below the talker: TaskManager -> RealPhone/RealSession -> Workspace ->
        # test_background's FakeController (which enforces Controller.java's rules). No phone.
        print("\n[unit] integrated: TaskManager on RealPhone with Rose's workspace")
        import rose.realphone as realphone
        from rose.tests import test_background as tb
        settle, realphone.SETTLE_S = realphone.SETTLE_S, 0.0
        try:
            fc = tb.FakeController(tb.Clock())
            rp, link = tb.real(fc)
            rp.vision = False  # the scripted brain takes no screenshots
            tm3 = TaskManager(rt, brain, rp, bus, lambda t: done(t).set())
            t = run(tm3, "Open the Calculator app", calls(("open_app", {"app": "Calculator"})),
                    calls(("finish", {"summary": "Calculator is open.", "success": "true"})))
            starts = [c for c in fc.calls if c[0] == "start"]
            expect("integrated: open_app runs in the workspace", t.status == "done" and t.background
                   and rp.workspace_app() == "Calculator" and len(starts) == 1
                   and not any(a == "open_app" for a, _ in link.visible),
                   f"{t.status}: {t.result} bg={t.background} ws={rp.workspace_app()} starts={len(starts)} "
                   f"visible={[a for a, _ in link.visible]}")
            expect("integrated: workspace prompt note", any("Screen: Rose's own workspace" in p for p in brain.prompts[-2:]), "")
            t = run(tm3, "Open the Clock app", calls(("finish", {"summary": "Done.", "success": "true"})),
                    calls(("finish", {"summary": "Done.", "success": "true"})))
            expect("integrated: success refused while Calculator is in the workspace",
                   t.status == "failed" and "Calculator is open" in t.result, f"{t.status}: {t.result}")
            t = run(tm3, "Add 1 + 2 in the Calculator app",
                    calls(("finish", {"summary": "Calculator is still open.", "success": "true"})))
            starts = [c for c in fc.calls if c[0] == "start"]
            expect("integrated: a follow-up task continues in the same workspace",
                   t.status == "done" and t.background and len(starts) == 1
                   and "Screen: Calculator" in brain.prompts[-1], f"{t.status}: {t.result} starts={len(starts)}")
            t = run(tm3, "Open the Calculator app", calls(("finish", {"summary": "x", "success": "false"})),
                    on_screen=True)
            expect("integrated: on_screen uses the visible screen", t.on_screen and not t.background
                   and any(a == "inspect_ui" for a, _ in link.visible), f"{t.to_dict()}")
            t = run(tm3, "Open the Amazon app", calls(("open_app", {"app": "Amazon"})),
                    calls(("finish", {"summary": "Amazon is open.", "success": "true"})))
            expect("integrated: 'the Amazon app' is Amazon Shopping on this phone",
                   t.status == "done" and rp.workspace_app() == "Amazon Shopping", f"{t.status}: {t.result}")
        finally:
            realphone.SETTLE_S = settle

        review_checks(expect, tm, fake, run, results, rt, brain, bus, done)
    finally:
        rt.stop()
    return bad


def review_checks(expect, tm, fake, run, results, rt, brain, bus, done) -> None:
    """Checks for the review findings fixed after integration (no model, no phone)."""

    from types import SimpleNamespace
    from rose.phone import Element, Screen
    from rose.tasks import (_Run, _app_matches, _draft_field, _message_goal, _worst_control)

    print("\n[unit] review fixes: money, send controls, drafts, apps")
    for label, want in [("Send Payment Now", "purchase"), ("Send $20", "purchase"), ("Payment", "purchase"),
                        ("Transfer", "purchase"), ("Request ride", "purchase"), ("Book now", "purchase"),
                        ("Reserve", "purchase"), ("Start free trial", "purchase"), ("Donate", "purchase"),
                        ("Confirm UberX", "final"), ("Post Office", ""), ("Post office near me", ""),
                        ("Send feedback", ""), ("Send message", "send"), ("Send to Sam", "send"),
                        ("Send end-to-end encrypted message to Alexandra Konstantinopoulou-Smythe", "send")]:
        got = _final_control(label, typed=True)
        expect(f"final control {label!r}", got == want, f"{got!r} != {want!r}")
    expect("view-id hints count a send anywhere", _worst_control(["id:ic send"]) == "send"
           and _worst_control(["id:btn send button", "Pay"]) == "purchase" and _worst_control(["id:post office row"]) == "")
    expect("a search is no message draft", not _message_goal("Search Maps for coffee and tell me the nearest one")
           and _message_goal("tell Sam I'm late") and not _draft_field("Search here") and _draft_field("Text message"))
    expect("a longer installed label is the named app", _app_matches("Amazon Shopping", "Amazon")
           and _app_matches("Keep Notes", "the notes app") and not _app_matches("Maps", "Messages")
           and _named_apps("Open the Amazon app", ["Amazon Shopping", "Maps"]) == ["Amazon Shopping"])

    # The send guard judges the real node: an icon-only Send (view id ic_send) and a point tap
    # on it are refused without a yes, whatever the agent calls them.
    class Guarded:
        name, background = "x", False

        def __init__(self, names):
            self.names = names

        def control_labels(self, mark):
            return self.names

        def labels_at(self, x, y):
            return self.names
    old = tm.guard_sends
    tm.guard_sends = True
    try:
        task = Task(1, "phone", "text Sam that I'm late")
        for call in (SimpleNamespace(name="tap", arguments={"id": "3"}),
                     SimpleNamespace(name="tap_point", arguments={"x": 900, "y": 950, "target": "paper plane icon"})):
            r = _Run(typed="I'm late", typed_field="Text message", typed_where="Messages")
            why, end, sending = tm._refuse(task, r, Guarded(["id:ic send"]), call, None)
            expect(f"{call.name} on an icon-only Send is refused", bool(why) and not sending
                   and end is not None and end[0] == "done", f"{why!r} {end}")
        task.ready_to_send = None
        r = _Run(typed="I'm late", typed_field="Text message")
        why, _, _ = tm._refuse(task, r, Guarded([]), SimpleNamespace(name="tap_point", arguments={"x": 1, "y": 1}),
                               None)
        expect("a nameless tap after a typed message is refused", "no name" in why, why)
        # Tapping a "Post Office" search result after typing the query: no send, no draft.
        task = Task(2, "phone", "Find the nearest post office in Maps")
        r = _Run(typed="post office", typed_field="Search here")
        el = SimpleNamespace(label="Post Office", mark=4)
        why, end, _ = tm._refuse(task, r, Guarded([]), SimpleNamespace(name="tap", arguments={"id": "4"}), el)
        tm._note_draft(task, r, SimpleNamespace(background=False, route=("maps",), obs=None))
        expect("a Post Office result is not a send", not why and end is None and task.ready_to_send is None,
               f"{why!r} {task.ready_to_send}")

        # A confirmed Send only while the approved draft is in the field, in the same workspace session.
        class DraftPhone:
            real = True
            field = "I'm late"
            session_id = "s1"

            def screen(self, name=None):
                return Screen("phone", "Messages", [Element("1", "field", "Text message", (0, 0, 1, 1),
                                                            self.field, mark=1)])

            def workspace_session_id(self, view=None):
                return self.session_id
        dp = DraftPhone()
        tm_d = TaskManager(rt, brain, dp, bus, lambda t: done(t).set())
        approved = {"text": "I'm late", "where": "Messages", "background": True, "on_screen": False, "session": "s1"}
        task = Task(3, "phone", "Send the message typed in Messages: \"I'm late\"", confirmed_send=True,
                    background=True, approved=approved)
        sess = SimpleNamespace(name="task3", background=True)
        expect("confirmed Send of the approved draft", tm_d._not_approved(task, _Run(), sess) == "")
        dp.field = "I'm late, Mom"
        expect("confirmed Send refused for another text", "approved" in tm_d._not_approved(task, _Run(), sess))
        dp.field, dp.session_id = "I'm late", "s2"
        expect("confirmed Send refused once the workspace changed", "no longer open" in
               tm_d._not_approved(task, _Run(), sess))
        dp.session_id = "s1"
        expect("confirmed Send refused for a retyped other text", "not the message" in
               tm_d._not_approved(task, _Run(typed="ok"), sess))

        # _current_app never waits on the phone (it runs on the MLX thread).
        class NoWait:
            real = True

            def cached_label(self, pkg):
                return None

            def app_label(self, pkg):
                raise AssertionError("app_label would wait on the phone")
        tm_n = TaskManager(rt, brain, NoWait(), bus, lambda t: done(t).set())
        here = tm_n._current_app(SimpleNamespace(obs={"status": "ok", "packageName": "com.x.app"}, background=False))
        expect("_current_app never loads the app list", here == "com.x.app", str(here))
    finally:
        tm.guard_sends = old

    # A direct intent did the goal: no "use open_app with Spotify" refusal.
    r = _Run(named=["Spotify"], direct_ok=True)
    expect("a direct intent satisfies the named app",
           tm._wrong_app(r, SimpleNamespace(route=("home",), obs=None, background=False)) == ("", None))
    # A goal that names one app and correctly ends in another (fake phone, guard off).
    fake.reset()
    t = run(tm, "Text Sam from my Contacts", calls(("open_app", {"app": "Contacts"})),
            calls(("tap", {"id": _marks(fake, "Contacts").get("Sam Lee", 1)})),
            calls(("finish", {"summary": "Sam's thread is open.", "success": "true"})))
    expect("Contacts then Messages is not a wrong app", t.status == "done", f"{t.status}: {t.result} {results(t)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--only", default="a,b,c,d,x,e,race,cancel,update,think")
    ap.add_argument("--unit-only", action="store_true", help="only the offline checks (no model)")
    args = ap.parse_args()
    only = set(args.only.split(","))
    faulthandler.dump_traceback_later(600, exit=True)
    unit_bad = unit_checks()
    if args.unit_only or unit_bad:
        print("\nUNIT PASSED" if not unit_bad else "\nUNIT FAILED:\n  " + "\n  ".join(unit_bad))
        return 0 if not unit_bad else 1

    h = Harness()
    print(f"brain loaded in {h.load_s:.1f} s; prompts prefilled in {h.prepare_s * 1000:.0f} ms")
    for rnd in range(1, args.rounds + 1):
        for key in "abcd":
            if key in only:
                run_goal(h, key, rnd)
        for key in EXTRA:
            if key in only or "x" in only:
                run_extra(h, key, rnd)
        if "e" in only:
            run_concurrent(h, rnd)
        if "race" in only:
            run_race(h, rnd)
        if "cancel" in only:
            run_cancel(h, rnd)
        if "update" in only:
            run_update(h, rnd)
        if "think" in only:
            run_think(h, rnd)
    summary(h)
    print(f"runtime: {h.rt.stats()}")
    print("\nALL PASSED" if not h.failures else "\nFAILED:\n  " + "\n  ".join(h.failures))
    h.rt.stop()
    return 0 if not h.failures else 1


if __name__ == "__main__":
    sys.exit(main())
