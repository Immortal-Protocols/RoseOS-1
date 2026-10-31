"""A fake phone: app state, a UI tree with element ids, and a PNG renderer.

The brain controls it the way a real phone agent would: it sees a screenshot
(with numbered marks on every interactive element) plus a text UI tree, and
acts with tap / type_text / open_app / back / home. Everything is in memory;
``reset()`` restores the demo data.
"""

from __future__ import annotations

import copy
import io
import re
import threading
import time
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

W, H = 390, 844
FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"


def _font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype(FONT_PATH, size, index=1 if bold else 0)
    except OSError:
        return ImageFont.load_default(size)


F = {
    "status": _font(15, True),
    "title": _font(28, True),
    "body": _font(17),
    "bold": _font(17, True),
    "small": _font(13),
    "mark": _font(11, True),
    "icon": _font(13),
    "big": _font(44),
}

APPS = ["Messages", "Clock", "Calendar", "Settings", "Contacts", "Camera", "Maps", "Music"]
APP_COLORS = {
    "Messages": "#34c759", "Clock": "#1c1c1e", "Calendar": "#ff3b30", "Settings": "#8e8e93",
    "Contacts": "#ff9500", "Camera": "#5856d6", "Maps": "#30b0c7", "Music": "#ff2d55",
}

INITIAL = {
    "contacts": {
        "Sam Lee": "+1 415 555 0134",
        "Mom": "+1 415 555 0101",
        "Alex Chen": "+1 628 555 0177",
        "Jordan Park": "+1 510 555 0190",
        "Priya Patel": "+1 650 555 0122",
    },
    "threads": {
        "Sam Lee": [("them", "Still on for 6:30 at Nopa?"), ("me", "Yes! See you there")],
        "Mom": [("them", "Call me when you get a chance"), ("me", "Will do, love you")],
        "Alex Chen": [("them", "Can you send the deck before the 4pm?")],
        "Jordan Park": [("them", "Pickup soccer Saturday 10am?")],
    },
    "alarms": [
        {"time": "7:00 AM", "label": "Wake up", "on": True},
        {"time": "9:30 PM", "label": "Meds", "on": False},
    ],
    "calendar": [
        {"time": "9:00 AM", "end": "9:30 AM", "title": "Standup", "where": "Zoom"},
        {"time": "12:30 PM", "end": "1:30 PM", "title": "Lunch with Priya", "where": "Tartine"},
        {"time": "4:00 PM", "end": "5:00 PM", "title": "Design review with Alex", "where": "Room 3B"},
        {"time": "6:30 PM", "end": "8:30 PM", "title": "Dinner with Sam", "where": "Nopa"},
    ],
    "settings": {"Wi-Fi": True, "Bluetooth": True, "Do Not Disturb": False, "Airplane Mode": False},
    "brightness": 60,
}


@dataclass
class Element:
    id: str
    kind: str  # button | toggle | field | text | item | key
    label: str
    box: tuple[int, int, int, int]
    value: str | None = None
    action: tuple = ()
    mark: int = 0

    def describe(self) -> str:
        if self.kind == "text":
            return f"text \"{self.label}\""
        bits = [f"[{self.mark}] {self.kind} \"{self.label}\""]
        if self.value is not None:
            bits.append(f"value=\"{self.value}\"")
        return " ".join(bits)


@dataclass
class ActionResult:
    ok: bool
    message: str


@dataclass
class Screen:
    name: str
    title: str
    elements: list[Element] = field(default_factory=list)


class Phone:
    def __init__(self):
        self._lock = threading.RLock()
        self.listeners: list = []
        self.reset()

    # ----- state -------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            self.data = copy.deepcopy(INITIAL)
            self.stack: list[tuple] = [("home",)]
            self.fields: dict[str, str] = {}
            self.toast: str | None = None
            self.last_tap: tuple[int, int, int, int] | None = None
            self.version = 0
            self._screen = self._build()
        self._notify("reset")

    @property
    def route(self) -> tuple:
        return self.stack[-1]

    def _go(self, *route) -> None:
        self.stack.append(route)
        self.fields = {}

    def _notify(self, what: str) -> None:
        self.version += 1
        for listener in list(self.listeners):
            try:
                listener(what)
            except Exception:
                pass

    def summary(self) -> str:
        """Phone data as text, for the brain's ask_brain answers."""

        with self._lock:
            d = self.data
            lines = ["Contacts: " + ", ".join(d["contacts"])]
            lines.append("Text conversations (oldest message first):")
            for name, msgs in d["threads"].items():
                convo = " / ".join(f"{'User' if who == 'me' else name} said \"{text}\"" for who, text in msgs)
                waiting = " (the user has not replied yet)" if msgs and msgs[-1][0] != "me" else ""
                lines.append(f"  - {convo}{waiting}")
            lines.append("Calendar today (" + time.strftime("%A %B %-d") + "):")
            lines += [f"  {e['time']}-{e['end']} {e['title']} at {e['where']}" for e in d["calendar"]]
            lines.append("Alarms: " + ", ".join(
                f"{a['time']} {a['label']} ({'on' if a['on'] else 'off'})" for a in d["alarms"]))
            lines.append("Settings: " + ", ".join(
                f"{k} {'on' if v else 'off'}" for k, v in d["settings"].items()))
            return "\n".join(lines)

    # ----- actions -----------------------------------------------------
    def screen(self) -> Screen:
        with self._lock:
            return self._screen

    def ui_tree(self) -> str:
        s = self.screen()
        lines = [f"Screen: {s.title}"]
        lines += ["  " + e.describe() for e in s.elements]
        if self.toast:
            lines.append(f"  notice: \"{self.toast}\"")
        return "\n".join(lines)

    def _find(self, mark) -> Element | None:
        for e in self._screen.elements:
            if str(e.mark) == str(mark).strip("[]") or e.id == str(mark):
                return e
        return None

    def tap(self, mark) -> ActionResult:
        with self._lock:
            el = self._find(mark)
            if el is None:
                return self._done(ActionResult(False, f"No element {mark} on this screen."))
            self.last_tap = el.box
            self.toast = None
            if el.kind in ("text",) or not el.action:
                return self._done(ActionResult(True, f"Tapped \"{el.label}\" (nothing happens)."))
            before = self.route
            result = self._run(el)
            if self.route != before:
                self.last_tap = None
            return self._done(result)

    def type_text(self, mark, text: str) -> ActionResult:
        with self._lock:
            el = self._find(mark)
            if el is None or el.kind != "field":
                return self._done(ActionResult(False, f"Element {mark} is not a text field."))
            self.last_tap = el.box
            self.fields[el.id] = text
            return self._done(ActionResult(True, f"Typed \"{text}\" into {el.label}."))

    def open_app(self, name: str) -> ActionResult:
        with self._lock:
            match = next((a for a in APPS if a.lower() == name.strip().lower()), None)
            if match is None:
                return self._done(ActionResult(False, f"No app called {name}. Apps: {', '.join(APPS)}."))
            self.stack = [("home",)]
            self._go(*self._app_route(match))
            self.toast = None
            self.last_tap = None
            return self._done(ActionResult(True, f"Opened {match}."))

    def back(self) -> ActionResult:
        with self._lock:
            if len(self.stack) > 1:
                self.stack.pop()
            self.fields = {}
            self.last_tap = None
            return self._done(ActionResult(True, "Went back."))

    def home(self) -> ActionResult:
        with self._lock:
            self.stack = [("home",)]
            self.fields = {}
            self.last_tap = None
            return self._done(ActionResult(True, "Went to the home screen."))

    def _done(self, result: ActionResult) -> ActionResult:
        self._screen = self._build()
        self._notify("action")
        return result

    @staticmethod
    def _app_route(app: str) -> tuple:
        return {
            "Messages": ("messages",), "Clock": ("clock",), "Calendar": ("calendar",),
            "Settings": ("settings",), "Contacts": ("contacts",),
        }.get(app, ("unavailable", app))

    def _run(self, el: Element) -> ActionResult:
        verb, *args = el.action
        d = self.data
        if verb == "open":
            self._go(*self._app_route(args[0]))
            return ActionResult(True, f"Opened {args[0]}.")
        if verb == "go":
            self._go(*args)
            return ActionResult(True, f"Opened {el.label}.")
        if verb == "send":
            name = args[0]
            text = self.fields.get("compose", "").strip()
            if not text:
                return ActionResult(False, "The message box is empty; type the message first.")
            d["threads"].setdefault(name, []).append(("me", text))
            self.fields = {}
            self.toast = f"Message sent to {name}"
            return ActionResult(True, f"Sent \"{text}\" to {name}.")
        if verb == "new_to":
            query = self.fields.get("to", "").strip().lower()
            hits = [n for n in d["contacts"] if query and query in n.lower()]
            if not hits:
                return ActionResult(False, f"No contact matches \"{query}\".")
            self.stack.pop()
            self._go("thread", hits[0])
            return ActionResult(True, f"Opened conversation with {hits[0]}.")
        if verb == "toggle_setting":
            d["settings"][args[0]] = not d["settings"][args[0]]
            state = "on" if d["settings"][args[0]] else "off"
            return ActionResult(True, f"{args[0]} is now {state}.")
        if verb == "brightness":
            d["brightness"] = max(0, min(100, d["brightness"] + args[0]))
            return ActionResult(True, f"Brightness is {d['brightness']}%.")
        if verb == "toggle_alarm":
            alarm = d["alarms"][args[0]]
            alarm["on"] = not alarm["on"]
            return ActionResult(True, f"Alarm {alarm['time']} is {'on' if alarm['on'] else 'off'}.")
        if verb == "save_alarm":
            raw = self.fields.get("alarm_time", "").strip()
            parsed = _parse_time(raw)
            if parsed is None:
                return ActionResult(False, f"\"{raw}\" is not a time like 6:30 AM.")
            label = self.fields.get("alarm_label", "").strip() or "Alarm"
            d["alarms"].append({"time": parsed, "label": label, "on": True})
            d["alarms"].sort(key=lambda a: _minutes(a["time"]))
            self.stack.pop()
            self.fields = {}
            self.toast = f"Alarm set for {parsed}"
            return ActionResult(True, f"Alarm saved for {parsed} ({label}).")
        if verb == "cancel":
            self.stack.pop()
            self.fields = {}
            return ActionResult(True, "Cancelled.")
        return ActionResult(False, f"Unknown action {verb}.")

    # ----- screens -----------------------------------------------------
    def _build(self) -> Screen:
        route = self.route
        kind = route[0]
        builder = getattr(self, f"_screen_{kind}")
        screen = builder(*route[1:])
        mark = 1
        for e in screen.elements:
            if e.kind != "text":
                e.mark = mark
                mark += 1
        return screen

    def _screen_home(self) -> Screen:
        s = Screen("home", "Home screen")
        size, gap, top = 64, 26, 110
        for i, app in enumerate(APPS):
            col, row = i % 4, i // 4
            x = 24 + col * (size + gap)
            y = top + row * (size + 40)
            s.elements.append(Element(f"app_{app}", "button", app, (x, y, x + size, y + size), action=("open", app)))
        return s

    def _list_item(self, s: Screen, i: int, label: str, value=None, action=(), top=120, h=64):
        y = top + i * h
        s.elements.append(Element(f"item_{i}", "item", label, (0, y, W, y + h), value=value, action=action))

    def _screen_messages(self) -> Screen:
        s = Screen("messages", "Messages")
        s.elements.append(Element("new", "button", "New message", (W - 64, 52, W - 16, 92), action=("go", "new_message")))
        for i, (name, msgs) in enumerate(self.data["threads"].items()):
            last = msgs[-1][1] if msgs else ""
            self._list_item(s, i, name, value=last, action=("go", "thread", name), top=110, h=76)
        return s

    def _screen_new_message(self) -> Screen:
        s = Screen("new_message", "New message")
        s.elements.append(Element("to", "field", "To", (16, 110, W - 16, 154), value=self.fields.get("to", "")))
        s.elements.append(Element("open_chat", "button", "Start conversation", (16, 170, W - 16, 214), action=("new_to",)))
        return s

    def _screen_thread(self, name: str) -> Screen:
        s = Screen("thread", f"Conversation with {name}")
        s.elements.append(Element("back", "button", "Back", (8, 52, 80, 92), action=("cancel",)))
        y = 120
        for who, text in self.data["threads"].get(name, [])[-8:]:
            s.elements.append(Element(f"msg_{y}", "text", f"{'You' if who == 'me' else name}: {text}", (16, y, W - 16, y + 44)))
            y += 56
        s.elements.append(Element("compose", "field", "Message", (12, H - 110, W - 80, H - 66), value=self.fields.get("compose", "")))
        s.elements.append(Element("send", "button", "Send", (W - 72, H - 110, W - 12, H - 66), action=("send", name)))
        return s

    def _screen_contacts(self) -> Screen:
        s = Screen("contacts", "Contacts")
        for i, (name, number) in enumerate(self.data["contacts"].items()):
            self._list_item(s, i, name, value=number, action=("go", "thread", name), top=110)
        return s

    def _screen_clock(self) -> Screen:
        s = Screen("clock", "Clock - Alarms")
        s.elements.append(Element("add_alarm", "button", "Add alarm", (W - 64, 52, W - 16, 92), action=("go", "alarm_edit")))
        for i, a in enumerate(self.data["alarms"]):
            y = 110 + i * 84
            s.elements.append(Element(f"alarm_{i}", "toggle", f"{a['time']} {a['label']}", (0, y, W, y + 84),
                                      value="on" if a["on"] else "off", action=("toggle_alarm", i)))
        return s

    def _screen_alarm_edit(self) -> Screen:
        s = Screen("alarm_edit", "New alarm")
        s.elements.append(Element("cancel", "button", "Cancel", (8, 52, 90, 92), action=("cancel",)))
        s.elements.append(Element("save", "button", "Save", (W - 80, 52, W - 8, 92), action=("save_alarm",)))
        s.elements.append(Element("alarm_time", "field", "Time (like 6:30 AM)", (16, 130, W - 16, 190), value=self.fields.get("alarm_time", "")))
        s.elements.append(Element("alarm_label", "field", "Label", (16, 210, W - 16, 254), value=self.fields.get("alarm_label", "")))
        return s

    def _screen_calendar(self) -> Screen:
        s = Screen("calendar", "Calendar - Today, " + time.strftime("%A %B %-d"))
        for i, ev in enumerate(self.data["calendar"]):
            self._list_item(s, i, f"{ev['time']} {ev['title']}", value=ev["where"],
                            action=("go", "event", i), top=110, h=76)
        return s

    def _screen_event(self, index: int) -> Screen:
        ev = self.data["calendar"][index]
        s = Screen("event", ev["title"])
        s.elements.append(Element("back", "button", "Back", (8, 52, 80, 92), action=("cancel",)))
        for j, line in enumerate([ev["title"], f"{ev['time']} - {ev['end']}", f"Where: {ev['where']}"]):
            s.elements.append(Element(f"ev_{j}", "text", line, (16, 120 + j * 44, W - 16, 160 + j * 44)))
        return s

    def _screen_settings(self) -> Screen:
        s = Screen("settings", "Settings")
        for i, (name, on) in enumerate(self.data["settings"].items()):
            y = 110 + i * 64
            s.elements.append(Element(f"set_{i}", "toggle", name, (0, y, W, y + 64),
                                      value="on" if on else "off", action=("toggle_setting", name)))
        y = 110 + len(self.data["settings"]) * 64 + 20
        s.elements.append(Element("bright", "text", f"Brightness {self.data['brightness']}%", (16, y, 220, y + 48)))
        s.elements.append(Element("dimmer", "button", "Brightness down", (230, y, 300, y + 48), action=("brightness", -10)))
        s.elements.append(Element("brighter", "button", "Brightness up", (310, y, W - 16, y + 48), action=("brightness", 10)))
        return s

    def _screen_unavailable(self, app: str) -> Screen:
        s = Screen("unavailable", app)
        s.elements.append(Element("na", "text", f"{app} is not part of this demo.", (16, 300, W - 16, 340)))
        return s

    # ----- rendering ---------------------------------------------------
    def screenshot_png(self, marks: bool = True) -> bytes:
        with self._lock:
            img = self._render(marks)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _render(self, marks: bool) -> Image.Image:
        s = self._screen
        img = Image.new("RGB", (W, H), "#f2f2f7" if s.name != "home" else "#1d2b4f")
        d = ImageDraw.Draw(img)
        dark = s.name == "home"
        fg = "white" if dark else "black"
        d.text((28, 14), time.strftime("%-I:%M"), font=F["status"], fill=fg)
        flags = []
        if self.data["settings"]["Do Not Disturb"]:
            flags.append("DND")
        if self.data["settings"]["Airplane Mode"]:
            flags.append("AIR")
        elif self.data["settings"]["Wi-Fi"]:
            flags.append("WiFi")
        d.text((W - 28, 14), "  ".join(flags + ["100%"]), font=F["status"], fill=fg, anchor="ra")

        if s.name != "home":
            d.text((20, 96 - 4), "", font=F["title"])
            title = s.title if len(s.title) < 26 else s.title[:24] + "..."
            tx = 20 if not any(e.id in ("back", "cancel") for e in s.elements) else 96
            d.text((tx, 58), title, font=F["title"] if len(title) < 18 else F["bold"], fill="black")

        for e in s.elements:
            self._draw_element(d, s, e)

        if self.toast:
            d.rounded_rectangle((40, H - 190, W - 40, H - 140), 22, fill="#1c1c1e")
            d.text((W // 2, H - 165), self.toast, font=F["bold"], fill="white", anchor="mm")
        d.rounded_rectangle((W // 2 - 70, H - 14, W // 2 + 70, H - 9), 3, fill=fg)

        if self.last_tap:
            x0, y0, x1, y1 = self.last_tap
            d.rounded_rectangle((x0 + 2, y0 + 2, x1 - 2, y1 - 2), 10, outline="#ff9f0a", width=3)
        if marks:
            for e in s.elements:
                if e.kind == "text":
                    continue
                x0, y0 = e.box[0], e.box[1]
                d.rounded_rectangle((x0 + 2, y0 + 2, x0 + 24, y0 + 18), 4, fill="#ff3b30")
                d.text((x0 + 13, y0 + 10), str(e.mark), font=F["mark"], fill="white", anchor="mm")
        return img

    def _draw_element(self, d: ImageDraw.ImageDraw, s: Screen, e: Element) -> None:
        x0, y0, x1, y1 = e.box
        if e.kind == "button" and e.id.startswith("app_"):
            d.rounded_rectangle(e.box, 16, fill=APP_COLORS.get(e.label, "#888"))
            d.text(((x0 + x1) // 2, (y0 + y1) // 2), e.label[0], font=F["big"], fill="white", anchor="mm")
            d.text(((x0 + x1) // 2, y1 + 14), e.label, font=F["icon"], fill="white", anchor="mm")
        elif e.kind == "button":
            short = {"New message": "+", "Add alarm": "+", "Brightness down": "-", "Brightness up": "+"}.get(e.label, e.label)
            fill = "#007aff" if e.id in ("send", "open_chat", "save") else "#e5e5ea"
            d.rounded_rectangle(e.box, 12, fill=fill)
            color = "white" if fill == "#007aff" else "#007aff"
            d.text(((x0 + x1) // 2, (y0 + y1) // 2), short, font=F["bold"], fill=color, anchor="mm")
        elif e.kind in ("item", "toggle"):
            d.rectangle((x0, y0, x1, y1 - 1), fill="white")
            d.line((x0 + 16, y1 - 1, x1, y1 - 1), fill="#d1d1d6")
            d.text((x0 + 30, y0 + 12), e.label, font=F["bold"], fill="black")
            if e.kind == "item" and e.value:
                v = e.value if len(e.value) < 40 else e.value[:38] + "..."
                d.text((x0 + 30, y0 + 38), v, font=F["small"], fill="#6e6e73")
            if e.kind == "toggle":
                on = e.value == "on"
                tx = x1 - 76
                ty = (y0 + y1) // 2 - 15
                d.rounded_rectangle((tx, ty, tx + 52, ty + 30), 15, fill="#34c759" if on else "#d1d1d6")
                cx = tx + (37 if on else 15)
                d.ellipse((cx - 13, ty + 2, cx + 13, ty + 28), fill="white")
        elif e.kind == "field":
            d.rounded_rectangle(e.box, 12, fill="white", outline="#c7c7cc")
            if e.value:
                d.text((x0 + 14, (y0 + y1) // 2), e.value, font=F["body"], fill="black", anchor="lm")
            else:
                d.text((x0 + 14, (y0 + y1) // 2), e.label, font=F["body"], fill="#a1a1a6", anchor="lm")
        elif e.kind == "text":
            if s.name == "thread":
                mine = e.label.startswith("You:")
                text = e.label.split(": ", 1)[1]
                tw = min(d.textlength(text, font=F["body"]) + 28, W - 90)
                bx0 = x1 - tw if mine else x0
                d.rounded_rectangle((bx0, y0, bx0 + tw, y1), 18, fill="#007aff" if mine else "#e5e5ea")
                d.text((bx0 + 14, (y0 + y1) // 2), text, font=F["body"], fill="white" if mine else "black", anchor="lm")
            else:
                d.text((x0, y0 + 8), e.label, font=F["body"], fill="black")


def _parse_time(raw: str) -> str | None:
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m?\.?\s*", raw, re.I)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if not (1 <= hour <= 12 and 0 <= minute < 60):
        return None
    return f"{hour}:{minute:02d} {m.group(3).upper()}M"


def _minutes(t: str) -> int:
    hm, ampm = t.split()
    h, m = map(int, hm.split(":"))
    return (h % 12 + (12 if ampm == "PM" else 0)) * 60 + m
