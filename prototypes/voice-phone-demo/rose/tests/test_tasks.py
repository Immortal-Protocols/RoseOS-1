"""Phone agent and deep-think tasks against the real brain and the fake phone.

    .venv/bin/python -m rose.tests.test_tasks [--rounds 2] [--only a,b,c,d,x,e,race,cancel,update,think]

Loads the brain once (~21 GB, on the runtime thread) and prefills the agent
prompt, then runs every scenario ``rounds`` times on a freshly reset phone:
(a) text Sam, (b) alarm 6:30 AM Gym, (c) Do Not Disturb, (d) Maps (must fail
gracefully), extra goals x1-x6 (new conversation, alarm off, calendar event,
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
import faulthandler
import os
import statistics
import sys
import threading
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from rose.brain import Brain
from rose.bus import Bus
from rose.phone import INITIAL, Phone
from rose.runtime import Runtime
from rose.tasks import Task, TaskManager

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
    ok = d == INITIAL and task.status == "failed" and "map" in task.result.lower()
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--only", default="a,b,c,d,x,e,race,cancel,update,think")
    args = ap.parse_args()
    only = set(args.only.split(","))
    faulthandler.dump_traceback_later(600, exit=True)

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
