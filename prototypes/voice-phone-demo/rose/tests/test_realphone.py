"""RealPhone without a phone: a canned Rose Agent link checks the screen rendering and the commands sent.

    .venv/bin/python -m rose.tests.test_realphone
"""

from __future__ import annotations

import asyncio
import json

from rose.realphone import PhoneLink, RealPhone, alarm_time
from rose.tasks import _direct, _execute
from rose.brain import ToolCall

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

    def call(self, action, timeout=25.0, **args):
        self.calls.append((action, args))
        if action == "inspect_ui":
            return SCREEN
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
    if not r.ok or link.calls[-1] != ("open_app", {"packageName": "com.google.android.apps.messaging"}):
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

    if bad:
        raise SystemExit("FAILED:\n  " + "\n  ".join(bad))
    print("realphone checks passed")


if __name__ == "__main__":
    main()
