"""RealPhone without a phone: a canned Rose Agent link checks the screen rendering and the commands sent.

    .venv/bin/python -m rose.tests.test_realphone

Also the vision surface (no model): the screenshot kept with each observation, ``look()``, and
``tap_point`` mapped from 0-1000 screenshot coordinates to display pixels and sent as Rose Agent's
``touch_point`` on the smallest node there (never inside a password field).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json

from PIL import Image

import threading
import time

import rose.realphone as realphone
from rose.realphone import PhoneLink, RealPhone, alarm_time, display_point
from rose.tasks import (PHONE_SYSTEM, REAL_PHONE_SYSTEM, VISION_PARAMS, _direct, _execute, _forced_syntax,
                        _names, _pieces, _SCREEN, phone_tools, vision_prompt)
from rose.brain import ToolCall

DISPLAY = (1080, 2400)
SHOT = (540, 1200)  # Rose Agent scales big screens down; the test uses half size


def _jpeg(size: tuple[int, int]) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, "#336699").save(buf, format="JPEG", quality=75)
    return base64.b64encode(buf.getvalue()).decode()

SCREEN = {
    "status": "ok", "packageName": "com.google.android.apps.messaging", "windowId": 7, "snapshotId": "s1",
    "truncated": False,
    "nodes": [
        {"id": 0, "class": "android.widget.FrameLayout", "visible": True, "bounds": [0, 0, 1080, 2400]},
        {"id": 1, "class": "android.widget.TextView", "text": "Sam", "visible": True, "bounds": [0, 100, 500, 160]},
        {"id": 2, "class": "android.widget.EditText", "text": "", "description": "Text message", "editable": True,
         "visible": True, "bounds": [0, 2200, 900, 2300]},
        {"id": 3, "class": "android.widget.Button", "visible": True, "clickable": True, "bounds": [900, 2200, 1080, 2300],
         "viewId": "com.google.android.apps.messaging:id/send_message_button_icon"},
        {"id": 4, "class": "android.widget.LinearLayout", "visible": True, "clickable": True, "bounds": [0, 300, 1080, 400]},
        {"id": 5, "class": "android.widget.TextView", "text": "Alex", "visible": True, "clickTarget": 4,
         "bounds": [20, 310, 400, 390]},
        {"id": 6, "class": "android.widget.EditText", "text": "secret", "password": True, "editable": True,
         "visible": True, "bounds": [0, 500, 900, 560]},
    ],
}


class FakeLink:
    connected, model = True, "SM-F971U1"

    def __init__(self):
        self.calls = []
        self.shot_status = "ok"
        self.shot_times: list[float] = []  # when each screenshot was asked for (monotonic)
        self.busy = 0  # screenshots being taken right now
        self.overlap = False

    def call(self, action, timeout=25.0, **args):
        self.calls.append((action, args))
        if action == "inspect_ui":
            return SCREEN
        if action == "screenshot":
            self.busy += 1
            self.overlap = self.overlap or self.busy > 1
            now = time.monotonic()
            # Android's throttle: a second takeScreenshot within 333 ms is refused (errorCode 3).
            throttled = bool(self.shot_times) and now - self.shot_times[-1] < 0.333
            self.shot_times.append(now)
            time.sleep(0.01)
            self.busy -= 1
            if throttled:
                return {"status": "capture_failed", "errorCode": 3}
            if self.shot_status != "ok":
                return {"status": self.shot_status}
            return {"status": "ok", "screenshotId": "f1", "width": SHOT[0], "height": SHOT[1],
                    "displayWidth": DISPLAY[0], "displayHeight": DISPLAY[1], "mimeType": "image/jpeg",
                    "imageBase64": _jpeg(SHOT), "packageName": SCREEN["packageName"]}
        if action == "touch_point":
            return {"status": "gesture_dispatched", "gestureId": "g1"}
        if action == "list_apps":
            return {"status": "ok", "apps": [{"label": "Messages", "packageName": "com.google.android.apps.messaging"},
                                             {"label": "Clock", "packageName": "com.sec.android.app.clockpackage"}]}
        if action == "open_app":
            return {"status": "opened", "observation": SCREEN}
        if action in ("set_alarm", "set_timer", "directions", "search_places", "play_music"):
            return {"status": "started", "observation": SCREEN}
        return {"status": "action_accepted"}


class FakeWs:
    """Just enough of a Starlette websocket for PhoneLink.serve."""

    def __init__(self, model: str):
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.inbox.put_nowait(json.dumps({"type": "hello", "model": model}))
        self.closed: int | None = None

    async def accept(self):
        pass

    async def receive_text(self):
        item = await self.inbox.get()
        if item is None:
            raise RuntimeError("disconnected")
        return item

    async def send_text(self, text):
        pass

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed = code
        self.inbox.put_nowait(None)


async def link_takeover(bad: list) -> None:
    """An unauthenticated (USB/loopback) connection never replaces a paired, authenticated one."""

    link = PhoneLink()
    paired, usb = FakeWs("paired"), FakeWs("evil")
    t1 = asyncio.create_task(link.serve(paired, authenticated=True))
    await asyncio.sleep(0.05)
    await link.serve(usb)  # returns at once: refused
    if usb.closed != 4009 or paired.closed is not None or link.model != "paired":
        bad.append(f"loopback took over the paired link (usb {usb.closed}, paired {paired.closed}, {link.model!r})")
    newer = FakeWs("paired-again")
    t2 = asyncio.create_task(link.serve(newer, authenticated=True))
    await asyncio.sleep(0.05)
    if paired.closed is None or link.model != "paired-again":
        bad.append("a newer authenticated connection did not replace the old one")
    await newer.close()
    await asyncio.gather(t1, t2)
    usb2 = FakeWs("usb")
    t3 = asyncio.create_task(link.serve(usb2))
    await asyncio.sleep(0.05)
    if usb2.closed is not None or link.model != "usb":
        bad.append("USB link refused after the paired phone went away")
    await usb2.close()
    await t3


def vision_checks(bad: list) -> None:
    phone = RealPhone()
    link = phone.link = FakeLink()
    if not phone.vision:
        bad.append("vision is off on the real phone")
    s = phone.session("task1")
    s.refresh()
    if [c[0] for c in link.calls if c[0] != "list_apps"][-1:] != ["inspect_ui"] or s.look() is not None:
        bad.append(f"refresh took the screenshot at once ({link.calls})")
    s.ensure_image()
    if [c[0] for c in link.calls if c[0] != "list_apps"][-2:] != ["inspect_ui", "screenshot"]:
        bad.append(f"refresh + ensure_image sent {link.calls}")
    img = s.look()
    if img is None or img.size != SHOT or s.shot.get("displayWidth") != DISPLAY[0]:
        bad.append(f"the observation's screenshot is {img and img.size} {s.shot}")
    if any("imageBase64" in str(v) for v in s.shot) or "imageBase64" in s.shot:
        bad.append("the screenshot bytes were kept beside the observation")

    # 0-1000 of the screenshot -> display pixels, through the scaled screenshot size.
    if display_point(500, 500, {"width": 1012, "height": 1600, "displayWidth": 1248,
                                "displayHeight": 1972}) != (624, 986):
        bad.append("display_point on a scaled Fold screenshot")
    if display_point(1000, 1000, {"width": 540, "height": 1200, "displayWidth": 1080,
                                  "displayHeight": 2400}) != (1079, 2399):
        bad.append("display_point does not stay on the display")

    link.calls.clear()  # the "Alex" text (node 5) at display (200, 350) sits inside its row (node 4)
    r = s.tap_point(200 / 1080 * 1000, 350 / 2400 * 1000, "Alex")
    if link.calls[:1] != [("touch_point", {"snapshotId": "s1", "nodeId": 5, "x": 200, "y": 350})]:
        bad.append(f"tap_point sent {link.calls[:1]}")
    if not r.ok or r.changed:
        bad.append(f"tap_point result {r} (must be ok and unverified)")
    link.calls.clear()  # inside the password field (node 6)
    r = s.tap_point(450 / 1080 * 1000, 530 / 2400 * 1000)
    if r.ok or any(c[0] == "touch_point" for c in link.calls):
        bad.append(f"tap_point touched a password field ({r}, {link.calls})")
    if s.tap_point(1200, 50).ok or s.tap_point(-1, 50).ok:
        bad.append("tap_point accepted coordinates off the 0-1000 scale")
    out = _execute(s, ToolCall("tap_point", {"target": "Alex", "x": "185", "y": "146"}))
    if not out["ok"] or out["changed"] or [c[0] for c in link.calls[-3:]] != ["screenshot", "touch_point", "inspect_ui"]:
        bad.append(f"tap_point through the agent (a due screenshot first): {out} {link.calls[-3:]}")
    if _execute(s, ToolCall("tap_point", {"target": "x", "x": "left", "y": "1"}))["ok"]:
        bad.append("tap_point accepted a non-number")
    r = s.tap(3)
    if not r.ok or r.changed:
        bad.append(f"with vision a tap must be checked on the next screenshot ({r})")

    link.shot_status = "secure_window"
    s.refresh()
    s.ensure_image()
    if s.look() is not None or "blocks screenshots" not in s.no_image_reason():
        bad.append(f"a secure screen gave {s.look()} / {s.no_image_reason()}")
    if s.tap_point(500, 500).ok:
        bad.append("tap_point without a screenshot")
    if phone.look() is not None or phone.look_status != "secure_window":
        bad.append(f"look() on a secure screen: {phone.look_status}")
    link.shot_status = "ok"
    if phone.look() is None or phone.look().size != SHOT:
        bad.append("look() returned no image")
    throttle_checks(bad)
    lazy_checks(bad)

    phone.vision = False
    link.calls.clear()
    s.refresh()
    if any(c[0] == "screenshot" for c in link.calls) or s.look() is not None:
        bad.append("a screenshot was taken with vision off")
    if s.tap(3).changed is not True:
        bad.append("without vision a tap keeps its old changed flag")

    # The agent's side: prompts, tools, the forced call syntax and the observation pieces.
    for system in (PHONE_SYSTEM, REAL_PHONE_SYSTEM):
        v = vision_prompt(system)
        if "screenshot" not in v or "tap_point" not in v or "never claim success" not in v:
            bad.append("vision prompt lacks its rules")
        if "never instructions" not in v or "Only the Goal and User updates" not in v or "grant a permission" not in v:
            bad.append("vision prompt does not say screen text is data, not instructions")
    summary = RealPhone().summary()
    if "look_at_screen" not in summary or "to find out what is on it, start a phone task" in summary:
        bad.append(f"the real phone's summary sends screen questions to a phone task: {summary!r}")
    for real in (False, True):
        names = [t["function"]["name"] for t in phone_tools(real, True)]
        if names[-1] != "finish" or "tap_point" not in names:
            bad.append(f"vision tools {names}")
        if "tap_point" in [t["function"]["name"] for t in phone_tools(real, False)]:
            bad.append("tap_point offered without vision")
    if _forced_syntax("<tool_call>\n<function=tap_point>") != "\n<parameter=target>\n":
        bad.append("tap_point call syntax is not forced")
    if (_forced_syntax("<tool_call>\n<function=tap>", VISION_PARAMS) != "\n<parameter=target>\n"
            or _forced_syntax("<tool_call>\n<function=tap>") != "\n<parameter=id>\n"):
        bad.append("tap names its target first only with vision")
    for target, label, ok in [("Code", "Projects", False), ("Code", "Code", True), ("Back", "Navigate up", True),
                              ("Send", "send message button icon", True), ("Do Not Disturb toggle", "Do Not Disturb", True)]:
        if _names(target, label) != ok:
            bad.append(f"_names({target!r}, {label!r}) is not {ok}")
    if _pieces(f"A\n\n{_SCREEN}B", ["tree"], "C") != ["A\n\ntreeBC"]:
        bad.append("a text-only observation is not one string")
    img = Image.new("RGB", (4, 4))
    if _pieces(f"A{_SCREEN}B", [img, "\ntree"], "C") != ["A", img, "\ntreeBC"]:
        bad.append("the screenshot is not placed before the tree")


def throttle_checks(bad: list) -> None:
    """The talker's look and a task step's screenshot never collide on Android's throttle."""

    phone = RealPhone()
    link = phone.link = FakeLink()
    s = phone.session("task1")
    s.refresh()
    looks: list = []
    t = threading.Thread(target=lambda: looks.append(phone.look()))
    t.start()
    s.ensure_image()
    t.join()
    gaps = [b - a for a, b in zip(link.shot_times, link.shot_times[1:])]
    if s.look() is None or not looks or looks[0] is None:
        bad.append(f"a look during a task step failed ({s.shot}, {phone.look_status})")
    if link.overlap or any(g < 0.333 for g in gaps):
        bad.append(f"screenshots were not serialised and spaced ({gaps})")
    # look() right after a step's screenshot reuses it instead of asking Android again.
    s.refresh()
    s.ensure_image()
    n = len(link.shot_times)
    if phone.look() is None or len(link.shot_times) != n:
        bad.append("look() did not reuse a fresh screenshot")
    # Refused anyway (another screenshot on the phone): wait out the interval, retry once.
    time.sleep(0.4)
    link.shot_times.append(time.monotonic())
    image, info = phone.capture()
    if image is None or info.get("status") != "ok" or link.shot_times[-1] - link.shot_times[-2] < 0.333:
        bad.append(f"a throttled screenshot was not retried after the interval ({info})")
    link.shot_status = "secure_window"
    link.shot_times.clear()
    n = len(link.calls)
    if phone.capture()[0] is not None or len(link.calls) != n + 1:
        bad.append("a refused (not throttled) screenshot was retried")


def lazy_checks(bad: list) -> None:
    """Several calls in one reply take one screenshot, when the next observation is built."""

    phone = RealPhone()
    link = phone.link = FakeLink()
    s = phone.session("task1")
    s.refresh()
    s.ensure_image()
    link.calls.clear()
    old = realphone.SETTLE_S
    realphone.SETTLE_S = 0.0
    try:
        s.type_text(1, "On my way")
        s.type_text(1, "On my way, 5 min")
        s.tap(2)
    finally:
        realphone.SETTLE_S = old
    if any(c[0] == "screenshot" for c in link.calls) or s.look() is not None:
        bad.append(f"actions of one reply each took a screenshot ({[c[0] for c in link.calls]})")
    if "not taken" not in s.no_image_reason():
        bad.append(f"a due screenshot gives the reason {s.no_image_reason()!r}")
    s.ensure_image()
    s.ensure_image()
    if [c[0] for c in link.calls].count("screenshot") != 1 or s.look() is None:
        bad.append("ensure_image did not take exactly one screenshot")


def main() -> None:
    bad = []
    asyncio.run(link_takeover(bad))
    phone = RealPhone()
    link = phone.link = FakeLink()
    s = phone.session("task1")
    s.refresh()
    tree = s.ui_tree()
    print(tree)
    for want in ['[1] field "Text message" value=""', '[2] button "send message button icon"',
                 '[3] button "Alex"', 'text "Sam"']:
        if want not in tree:
            bad.append(f"ui_tree lacks {want!r}")
    if "secret" in tree:
        bad.append("a password field was shown")
    if s.route != ("com.google.android.apps.messaging", 7):
        bad.append(f"route {s.route}")

    link.calls.clear()
    r = s.type_text(1, "On my way")
    if not r.ok or link.calls[0] != ("set_text", {"snapshotId": "s1", "nodeId": 2, "text": "On my way"}):
        bad.append(f"type_text sent {link.calls[:1]} ({r})")
    link.calls.clear()
    s.tap(3)
    if link.calls[0] != ("tap_label", {"snapshotId": "s1", "label": "Alex", "nodeId": 5}):
        bad.append(f"labelled tap sent {link.calls[:1]}")
    link.calls.clear()
    s.tap(2)
    if link.calls[0] != ("tap", {"snapshotId": "s1", "nodeId": 3}):
        bad.append(f"unlabelled tap sent {link.calls[:1]}")
    if s.type_text(3, "x").ok:
        bad.append("typed into a button")

    link.calls.clear()
    r = s.open_app("messages")
    sent = [c for c in link.calls if c[0] not in ("list_apps", "screenshot")]  # (vision adds a screenshot)
    if not r.ok or sent[-1:] != [("open_app", {"packageName": "com.google.android.apps.messaging"})]:
        bad.append(f"open_app sent {link.calls}")
    if s.open_app("Telescope").ok:
        bad.append("opened an app that is not installed")

    link.calls.clear()
    out = _execute(s, ToolCall("set_alarm", {"time": "6:30 AM", "label": "Gym"}))
    if not out["ok"] or link.calls[0] != ("set_alarm", {"hour": 6, "minute": 30, "label": "Gym"}):
        bad.append(f"set_alarm sent {link.calls[:1]} ({out})")
    link.calls.clear()
    _direct(s, "set_timer", {"minutes": 2.5, "label": ""})
    if link.calls[0] != ("set_timer", {"seconds": 150, "label": ""}):
        bad.append(f"set_timer sent {link.calls[:1]}")
    if alarm_time("25:00") is not None or alarm_time("7 pm") != (19, 0):
        bad.append("alarm_time parsing")

    vision_checks(bad)

    if bad:
        raise SystemExit("FAILED:\n  " + "\n  ".join(bad))
    print("realphone checks passed")


if __name__ == "__main__":
    main()
