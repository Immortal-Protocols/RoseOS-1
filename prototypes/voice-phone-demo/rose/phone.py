"""A fake phone: app data, a compact text UI tree with element ids, a PNG renderer.

Rose's phone agent drives it the way a real phone agent would: it reads the
UI tree (one line per element, numbered ids on everything it can act on) and
acts with ``open_app`` / ``tap`` / ``type_text`` / ``back`` / ``home``.
Everything is in memory; ``reset()`` restores the demo data.

Background tasks each get their own *view* (navigation stack, typed text,
toast) over the shared data, like a virtual display: two tasks can work at
the same time without stealing each other's screen, and every action is
atomic under one lock. The dashboard shows whichever view acted last.
``notify()`` simulates an incoming message so Rose can be proactive.

Vision is off by default (``Phone.vision``; ``vision=True`` or ``ROSE_FAKE_VISION=1`` turns it
on), which keeps the text-only agent's latency and eval baselines. With it on, the phone agent
also sees ``look()`` (the screen rendered without id marks) and may ``tap_point`` in it.

Listeners are called as ``listener(what, info)`` outside the lock, on the
thread that made the change ("reset", "action", "notification").
"""

from __future__ import annotations

import copy
import io
import os
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from .bus import Bus

W, H = 390, 844
MAIN = "main"
BANNER_S = 6.0
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
    # Music: invented playlists and artists.
    "music": {
        "playlists": {
            "Chill Mix": [("Golden Hour Drive", "The Velvet Coast"), ("Slow Tide", "Marisol Vega"),
                          ("Paper Lanterns", "Northbound"), ("Late Bloom", "June Arden")],
            "Running": [("Faster Than Light", "Neon Harbor"), ("Pulse", "Kite Season"),
                        ("Mile Twelve", "Redline Social")],
            "Focus": [("Deep Water", "Lumen Fields"), ("Quiet Hours", "Lumen Fields"),
                      ("Long Exposure", "Field Notes")],
            "Dinner Jazz": [("Blue Corner", "The Ellis Trio"), ("Candlelight", "Mara Quinn Quartet"),
                            ("Night Market", "The Ellis Trio")],
        },
        "now": None,  # {"title", "artist", "playlist", "index"}
        "playing": False,
    },
    # Maps: invented places around the user's home (no real businesses).
    "places": [
        {"name": "Valencia Pharmacy", "kind": "pharmacy", "address": "2690 Mission St",
         "miles": 0.3, "walk": 6, "drive": 3, "hours": "open until 10 PM", "phone": "+1 415 555 0161"},
        {"name": "Market Street Drugs", "kind": "pharmacy", "address": "731 Market St",
         "miles": 1.8, "walk": 36, "drive": 11, "hours": "open 24 hours", "phone": "+1 415 555 0162"},
        {"name": "Sunrise Coffee Roasters", "kind": "coffee cafe", "address": "1026 Valencia St",
         "miles": 0.4, "walk": 8, "drive": 4, "hours": "open until 6 PM", "phone": "+1 415 555 0171"},
        {"name": "Fog City Espresso", "kind": "coffee cafe", "address": "375 Valencia St",
         "miles": 0.9, "walk": 18, "drive": 6, "hours": "open until 8 PM", "phone": "+1 415 555 0172"},
        {"name": "Harbor Grocery", "kind": "grocery supermarket food", "address": "1745 Folsom St",
         "miles": 1.0, "walk": 20, "drive": 7, "hours": "open until 9 PM", "phone": "+1 415 555 0181"},
        {"name": "Eighteenth Street Market", "kind": "grocery supermarket food", "address": "3639 18th St",
         "miles": 0.7, "walk": 14, "drive": 5, "hours": "open until 11 PM", "phone": "+1 415 555 0182"},
        {"name": "Mission Fuel", "kind": "gas station fuel", "address": "1798 Mission St",
         "miles": 0.8, "walk": 16, "drive": 4, "hours": "open 24 hours", "phone": "+1 415 555 0191"},
        {"name": "Nopa", "kind": "restaurant dinner", "address": "560 Divisadero St",
         "miles": 2.3, "walk": 46, "drive": 14, "hours": "open until 11 PM", "phone": "+1 415 555 0193"},
        {"name": "Tartine", "kind": "bakery restaurant lunch cafe", "address": "600 Guerrero St",
         "miles": 0.6, "walk": 12, "drive": 5, "hours": "open until 7 PM", "phone": "+1 415 555 0194"},
        {"name": "Dolores Urgent Care", "kind": "urgent care clinic doctor hospital", "address": "2100 Market St",
         "miles": 1.2, "walk": 24, "drive": 8, "hours": "open until 9 PM", "phone": "+1 415 555 0195"},
        {"name": "Mission Climbing Gym", "kind": "gym fitness", "address": "1555 Folsom St",
         "miles": 1.1, "walk": 22, "drive": 7, "hours": "open until 10 PM", "phone": "+1 415 555 0196"},
    ],
    "navigation": None,  # {"to", "mode", "minutes"}
}

_PLACE_WORDS = {"drugstore": "pharmacy", "drug": "pharmacy", "medicine": "pharmacy", "pharmacies": "pharmacy",
                "cafe": "coffee", "coffees": "coffee", "supermarket": "grocery", "groceries": "grocery",
                "gas": "gas", "petrol": "gas", "hospital": "urgent", "doctor": "urgent",
                "restaurants": "restaurant", "gyms": "gym"}


@dataclass
class Element:
    id: str
    kind: str  # button | toggle | field | text | item
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

    def to_dict(self) -> dict:
        return {"id": self.mark or None, "kind": self.kind, "label": self.label,
                "value": self.value, "box": list(self.box)}


@dataclass
class ActionResult:
    ok: bool
    message: str
    changed: bool = False  # the phone's data changed (sent, saved, toggled)


@dataclass
class Screen:
    name: str
    title: str
    elements: list[Element] = field(default_factory=list)


@dataclass
class View:
    """One display: where it is, what is typed, what it shows."""

    name: str
    stack: list[tuple] = field(default_factory=lambda: [("home",)])
    fields: dict[str, str] = field(default_factory=dict)
    toast: str | None = None
    last_tap: tuple[int, int, int, int] | None = None
    screen: Screen | None = None

    @property
    def route(self) -> tuple:
        return self.stack[-1]

    def go(self, *route) -> None:
        self.stack.append(route)
        self.fields = {}


class Phone:
    def __init__(self, bus: Bus | None = None, vision: bool | None = None):
        if vision is None:
            vision = os.environ.get("ROSE_FAKE_VISION", "").strip().lower() in ("1", "true", "yes", "on")
        self.vision = vision  # the phone agent gets screenshots and tap_point
        self._lock = threading.RLock()
        self.listeners: list[Callable[[str, dict], None]] = []
        self.bus = bus
        self.version = 0
        self.reset()

    # ----- state -------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            self.data = copy.deepcopy(INITIAL)
            self.views: dict[str, View] = {MAIN: View(MAIN)}
            self.shown = MAIN
            self.banner: dict | None = None
            self._rebuild(self.views[MAIN])
        self._changed("reset", {})

    @property
    def route(self) -> tuple:
        return self.views[MAIN].route

    def _view(self, name: str | None) -> View:
        name = name or MAIN
        v = self.views.get(name)
        if v is None:
            # A new display starts where the user's screen is.
            v = View(name, stack=list(self.views[MAIN].stack))
            self._rebuild(v)
            self.views[name] = v
        return v

    def session(self, name: str, on_screen: bool = False) -> PhoneSession:
        """A task's display. ``on_screen`` is accepted for the real phone's surface: the fake
        phone has no separate workspace, so every task works on its own view as before."""

        with self._lock:
            self._view(name)
        return PhoneSession(self, name)

    # ----- Rose's workspace: the real phone's background screen (rose/background.py). The fake
    # phone has none, so these keep the eval's behaviour unchanged.
    def background_available(self) -> bool:
        return False

    def app_labels(self) -> list[str]:
        """The fake phone's app labels (RealPhone.app_labels() on the real one)."""

        return list(APPS)

    def workspace_app(self, view: str | None = None) -> str | None:
        return None

    def look_workspace(self) -> Image.Image | None:
        self.look_status = "the fake phone has no separate workspace"
        return None

    def close_session(self, name: str) -> None:
        """Drop a task's view; if it is on show, it becomes the main screen."""

        if name == MAIN:
            return
        with self._lock:
            v = self.views.pop(name, None)
            if v is None or self.shown != name:
                return
            v.name = MAIN
            self.views[MAIN] = v
            self.shown = MAIN

    def route_of(self, view: str | None = None) -> tuple:
        with self._lock:
            return self._view(view).route

    def _changed(self, what: str, info: dict) -> None:
        with self._lock:
            self.version += 1
            version = self.version
        for listener in list(self.listeners):
            try:
                listener(what, info)
            except Exception:
                traceback.print_exc()
        if self.bus is not None:
            extra = {"view": info["view"]} if "view" in info else {}
            self.bus.emit("phone", version=version, what=what, **extra)

    def summary(self) -> str:
        """Phone data as text, for the talker's context."""

        with self._lock:
            d = self.data
            lines = ["Contacts: " + ", ".join(d["contacts"])]
            lines.append("Text conversations (oldest message first):")
            for name, msgs in d["threads"].items():
                convo = " / ".join(f"{'User' if who == 'me' else name} said \"{text}\"" for who, text in msgs)
                waiting = " (the user has not replied yet)" if msgs and msgs[-1][0] != "me" else ""
                lines.append(f"  - {convo}{waiting}")
            lines.append("Calendar today (" + time.strftime("%A %B %-d") + "):")
            lines += [f"  {e['time']}-{e['end']} {e['title']}" + (f" at {e['where']}" if e["where"] else "")
                      for e in d["calendar"]]
            lines.append("Alarms: " + ", ".join(
                f"{a['time']} {a['label']} ({'on' if a['on'] else 'off'})" for a in d["alarms"]))
            lines.append("Settings: " + ", ".join(
                f"{k} {'on' if v else 'off'}" for k, v in d["settings"].items()))
            m = d["music"]
            if m["now"]:
                lines.append(f"Music: {'playing' if m['playing'] else 'paused'} \"{m['now']['title']}\" by "
                             f"{m['now']['artist']} (playlist {m['now']['playlist']})")
            else:
                lines.append("Music: nothing playing. Playlists: " + ", ".join(m["playlists"]))
            if d["navigation"]:
                n = d["navigation"]
                lines.append(f"Maps: {n['mode']} directions to {n['to']} are on ({n['minutes']} min)")
            return "\n".join(lines)

    # ----- observation -------------------------------------------------
    def screen(self, view: str | None = None) -> Screen:
        with self._lock:
            return self._view(view).screen

    def ui_tree(self, view: str | None = None) -> str:
        """Compact text: one line per element, ``[id] kind "label" value="..."``."""

        with self._lock:
            v = self._view(view)
            self._rebuild(v)
            lines = [f"Screen: {v.screen.title}"]
            lines += ["  " + e.describe() for e in v.screen.elements]
            if v.toast:
                lines.append(f"  notice: \"{v.toast}\"")
            return "\n".join(lines)

    def element(self, mark, view: str | None = None) -> Element | None:
        with self._lock:
            return self._find(self._view(view), mark)

    def snapshot(self) -> dict:
        """What the dashboard draws: the view on show plus status flags."""

        with self._lock:
            v = self.views.get(self.shown) or self.views[MAIN]
            s = v.screen
            st = self.data["settings"]
            return {
                "version": self.version, "view": v.name, "route": [str(r) for r in v.route],
                "screen": s.name, "title": s.title, "toast": v.toast, "banner": self._banner(),
                "last_tap": list(v.last_tap) if v.last_tap else None, "size": [W, H],
                "elements": [e.to_dict() for e in s.elements],
                "status": {"time": time.strftime("%-I:%M"), "dnd": st["Do Not Disturb"],
                           "wifi": st["Wi-Fi"], "airplane": st["Airplane Mode"]},
            }

    def _banner(self) -> dict | None:
        b = self.banner
        if b is None or time.time() - b["t"] > BANNER_S:
            return None
        return {k: b[k] for k in ("app", "title", "text")}

    def _find(self, v: View, mark) -> Element | None:
        key = str(mark).strip().strip("[]")
        for e in v.screen.elements:
            if (e.kind != "text" and str(e.mark) == key) or e.id == key:
                return e
        return None

    # ----- incoming ----------------------------------------------------
    def notify(self, app: str, title: str, text: str) -> None:
        """An incoming notification; a Messages one lands in the sender's thread."""

        with self._lock:
            app = next((a for a in APPS if a.lower() == app.strip().lower()), app.strip())
            if app == "Messages":
                title = self._contact(title)
                self.data["threads"].setdefault(title, []).append(("them", text))
            self.banner = {"app": app, "title": title, "text": text, "t": time.time()}
            for v in self.views.values():
                self._rebuild(v)
        info = {"app": app, "title": title, "text": text}
        self._changed("notification", info)
        if self.bus is not None:
            self.bus.emit("notification", **info)

    def _contact(self, name: str) -> str:
        name = name.strip()
        if name in self.data["contacts"]:
            return name
        low = name.lower()
        return next((c for c in self.data["contacts"] if c.lower().split()[0] == low), name)

    # ----- actions -----------------------------------------------------
    def _act(self, view: str | None, what: str, fn: Callable[[View], ActionResult]) -> ActionResult:
        with self._lock:
            v = self._view(view)
            try:
                result = fn(v)
            except Exception as exc:  # a bad argument must not break the phone
                result = ActionResult(False, f"Error: {exc}")
            self._rebuild(v)
            self.shown = v.name
            info = {"view": v.name, "action": what, "ok": result.ok, "message": result.message}
        self._changed("action", info)
        return result

    def tap(self, mark, view: str | None = None) -> ActionResult:
        return self._act(view, "tap", lambda v: self._tap(v, mark))

    def tap_point(self, x: int, y: int, view: str | None = None) -> ActionResult:
        """Tap screen pixel (x, y) of a W x H screen: the smallest element there, like a finger."""

        return self._act(view, "tap_point", lambda v: self._tap_at(v, x, y))

    def type_text(self, mark, text: str, view: str | None = None) -> ActionResult:
        return self._act(view, "type_text", lambda v: self._type(v, mark, text))

    def open_app(self, name: str, view: str | None = None) -> ActionResult:
        return self._act(view, "open_app", lambda v: self._open(v, name))

    def back(self, view: str | None = None) -> ActionResult:
        def fn(v: View) -> ActionResult:
            if len(v.stack) > 1:
                v.stack.pop()
            v.fields, v.last_tap = {}, None
            return ActionResult(True, "Went back.")

        return self._act(view, "back", fn)

    def home(self, view: str | None = None) -> ActionResult:
        def fn(v: View) -> ActionResult:
            v.stack, v.fields, v.last_tap = [("home",)], {}, None
            return ActionResult(True, "Went to the home screen.")

        return self._act(view, "home", fn)

    def _tap(self, v: View, mark) -> ActionResult:
        el = self._find(v, mark)
        if el is None:
            return ActionResult(False, f"No element {mark} on this screen.")
        v.last_tap = el.box
        v.toast = None
        if el.kind == "field":
            return ActionResult(True, f"Tapped the {el.label} field; use type_text to type.")
        if el.kind == "text" or not el.action:
            return ActionResult(True, f"Tapped \"{el.label}\" (nothing happens).")
        before = v.route
        result = self._run(v, el)
        if v.route != before:
            v.last_tap = None
        return result

    def _tap_at(self, v: View, x: int, y: int) -> ActionResult:
        hits = [e for e in v.screen.elements if e.box[0] <= x < e.box[2] and e.box[1] <= y < e.box[3]]
        if not hits:
            return ActionResult(False, "Nothing to tap at that point.")
        # Controls before plain text, then the smallest box (the innermost one).
        el = min(hits, key=lambda e: (e.kind == "text", (e.box[2] - e.box[0]) * (e.box[3] - e.box[1])))
        if el.kind == "text":
            v.last_tap = el.box
            return ActionResult(True, f"Touched \"{el.label}\" (nothing happens).")
        return self._tap(v, el.mark)

    def _type(self, v: View, mark, text: str) -> ActionResult:
        el = self._find(v, mark)
        if el is None:
            return ActionResult(False, f"No element {mark} on this screen.")
        if el.kind != "field":
            return ActionResult(False, f"[{el.mark}] is the {el.kind} \"{el.label}\", not a text field.")
        v.last_tap = el.box
        v.fields[el.id] = text
        return ActionResult(True, f"Typed \"{text}\" into {el.label}.")

    def _open(self, v: View, name: str) -> ActionResult:
        match = next((a for a in APPS if a.lower() == name.strip().lower()), None)
        if match is None:
            return ActionResult(False, f"No app called {name}. Apps: {', '.join(APPS)}.")
        v.stack = [("home",)]
        v.go(*self._app_route(match))
        v.toast = v.last_tap = None
        if match not in ROUTES:
            return ActionResult(True, f"Opened {match}, but {match} does not work on this phone.")
        return ActionResult(True, f"Opened {match}.")

    @staticmethod
    def _app_route(app: str) -> tuple:
        return ROUTES.get(app, ("unavailable", app))

    def _run(self, v: View, el: Element) -> ActionResult:
        verb, *args = el.action
        d = self.data
        if verb == "open":
            v.go(*self._app_route(args[0]))
            return ActionResult(True, f"Opened {args[0]}.")
        if verb == "go":
            v.go(*args)
            return ActionResult(True, f"Opened {el.label}.")
        if verb == "send":
            name = args[0]
            text = v.fields.get("compose", "").strip()
            if not text:
                return ActionResult(False, "The message box is empty; type the message first.")
            d["threads"].setdefault(name, []).append(("me", text))
            v.fields = {}
            v.toast = f"Message sent to {name}"
            return ActionResult(True, f"Sent \"{text}\" to {name}.", changed=True)
        if verb == "new_to":
            query = v.fields.get("to", "").strip().lower()
            hits = [n for n in d["contacts"] if query and query in n.lower()]
            if not hits:
                return ActionResult(False, f"No contact matches \"{query}\".")
            v.stack.pop()
            v.go("thread", hits[0])
            return ActionResult(True, f"Opened conversation with {hits[0]}.")
        if verb == "toggle_setting":
            d["settings"][args[0]] = not d["settings"][args[0]]
            state = "on" if d["settings"][args[0]] else "off"
            return ActionResult(True, f"{args[0]} is now {state}.", changed=True)
        if verb == "brightness":
            d["brightness"] = max(0, min(100, d["brightness"] + args[0]))
            return ActionResult(True, f"Brightness is {d['brightness']}%.", changed=True)
        if verb == "toggle_alarm":
            alarm = args[0]  # the alarm itself: another view may have shifted the list since
            if not any(a is alarm for a in d["alarms"]):
                return ActionResult(False, "That alarm is no longer there.")
            alarm["on"] = not alarm["on"]
            return ActionResult(True, f"Alarm {alarm['time']} {alarm['label']} is "
                                      f"{'on' if alarm['on'] else 'off'}.", changed=True)
        if verb == "save_alarm":
            raw = v.fields.get("alarm_time", "").strip()
            parsed = _parse_time(raw)
            if parsed is None:
                return ActionResult(False, f"\"{raw}\" is not a time like 6:30 AM.")
            label = v.fields.get("alarm_label", "").strip() or "Alarm"
            d["alarms"].append({"time": parsed, "label": label, "on": True})
            d["alarms"].sort(key=lambda a: _minutes(a["time"]))
            v.stack.pop()
            v.fields = {}
            v.toast = f"Alarm set for {parsed}"
            return ActionResult(True, f"Alarm saved for {parsed} ({label}).", changed=True)
        if verb == "save_event":
            title = v.fields.get("event_title", "").strip()
            start = _parse_time(v.fields.get("event_start", ""))
            if not title or start is None:
                return ActionResult(False, "An event needs a title and a start time like 3:00 PM.")
            end = _parse_time(v.fields.get("event_end", "")) or _clock(_minutes(start) + 60)
            where = v.fields.get("event_where", "").strip()
            d["calendar"].append({"time": start, "end": end, "title": title, "where": where})
            d["calendar"].sort(key=lambda e: _minutes(e["time"]))
            v.stack.pop()
            v.fields = {}
            v.toast = f"Event added at {start}"
            return ActionResult(True, f"Added \"{title}\" today at {start}-{end}.", changed=True)
        if verb == "cancel":
            v.stack.pop()
            v.fields = {}
            return ActionResult(True, "Cancelled.")
        if verb == "play_playlist":
            return self._play(v, args[0], 0)
        if verb == "play_song":
            return self._play(v, args[0], args[1])
        if verb == "toggle_play":
            m = d["music"]
            if not m["now"]:
                return ActionResult(False, "Nothing is loaded; pick a playlist or a song first.")
            m["playing"] = not m["playing"]
            v.toast = ("Playing " if m["playing"] else "Paused ") + m["now"]["title"]
            return ActionResult(True, v.toast + ".", changed=True)
        if verb == "next_song":
            m = d["music"]
            if not m["now"]:
                return ActionResult(False, "Nothing is playing.")
            songs = m["playlists"][m["now"]["playlist"]]
            return self._play(v, m["now"]["playlist"], (m["now"]["index"] + 1) % len(songs))
        if verb == "music_search":
            q = v.fields.get("music_q", "").strip()
            if not q:
                return ActionResult(False, "The search box is empty; type what to look for first.")
            v.go("music_results", q)
            return ActionResult(True, f"Searched music for \"{q}\".")
        if verb == "map_search":
            q = (args[0] if args else v.fields.get("map_q", "")).strip()
            if not q:
                return ActionResult(False, "The search box is empty; type a place or a kind of place first.")
            v.go("map_results", q)
            n = len(_search_places(d["places"], q))
            return ActionResult(True, f"Searched Maps for \"{q}\": {n} result{'s' if n != 1 else ''}.")
        if verb == "start_nav":
            place, mode = args
            minutes = place["walk"] if mode == "walking" else place["drive"]
            d["navigation"] = {"to": place["name"], "mode": mode, "minutes": minutes}
            v.toast = f"Navigating to {place['name']}: {minutes} min {mode}"
            return ActionResult(True, f"Started {mode} directions to {place['name']} ({minutes} min).",
                                changed=True)
        if verb == "stop_nav":
            d["navigation"] = None
            v.toast = "Navigation ended"
            return ActionResult(True, "Ended navigation.", changed=True)
        return ActionResult(False, f"Unknown action {verb}.")

    def _play(self, v: View, playlist: str, index: int) -> ActionResult:
        songs = self.data["music"]["playlists"].get(playlist)
        if not songs:
            return ActionResult(False, f"No playlist called {playlist}.")
        title, artist = songs[index % len(songs)]
        self.data["music"].update(now={"title": title, "artist": artist, "playlist": playlist,
                                       "index": index % len(songs)}, playing=True)
        v.toast = f"Playing {title}"
        return ActionResult(True, f"Playing \"{title}\" by {artist} from {playlist}.", changed=True)

    # ----- screens -----------------------------------------------------
    def _rebuild(self, v: View) -> None:
        kind, *args = v.route
        screen = getattr(self, f"_screen_{kind}")(v, *args)
        mark = 1
        for e in screen.elements:
            if e.kind != "text":
                e.mark = mark
                mark += 1
        v.screen = screen

    def _screen_home(self, v: View) -> Screen:
        s = Screen("home", "Home screen")
        size, top = 64, 110
        for i, app in enumerate(APPS):
            col, row = i % 4, i // 4
            x = 24 + col * (size + 26)
            y = top + row * (size + 40)
            s.elements.append(Element(f"app_{app}", "button", app, (x, y, x + size, y + size), action=("open", app)))
        return s

    @staticmethod
    def _list_item(s: Screen, i: int, label: str, value=None, action=(), top=120, h=64):
        y = top + i * h
        s.elements.append(Element(f"item_{i}", "item", label, (0, y, W, y + h), value=value, action=action))

    def _screen_messages(self, v: View) -> Screen:
        s = Screen("messages", "Messages")
        s.elements.append(Element("new", "button", "New message", (W - 64, 52, W - 16, 92), action=("go", "new_message")))
        for i, (name, msgs) in enumerate(self.data["threads"].items()):
            last = msgs[-1][1] if msgs else ""
            self._list_item(s, i, name, value=last, action=("go", "thread", name), top=110, h=76)
        return s

    def _screen_new_message(self, v: View) -> Screen:
        s = Screen("new_message", "New message")
        s.elements.append(Element("to", "field", "To", (16, 110, W - 16, 154), value=v.fields.get("to", "")))
        s.elements.append(Element("open_chat", "button", "Start conversation", (16, 170, W - 16, 214), action=("new_to",)))
        return s

    def _screen_thread(self, v: View, name: str) -> Screen:
        s = Screen("thread", f"Conversation with {name}")
        s.elements.append(Element("back", "button", "Back", (8, 52, 80, 92), action=("cancel",)))
        y = 120
        for who, text in self.data["threads"].get(name, [])[-8:]:
            s.elements.append(Element(f"msg_{y}", "text", f"{'You' if who == 'me' else name}: {text}", (16, y, W - 16, y + 44)))
            y += 56
        s.elements.append(Element("compose", "field", "Message", (12, H - 110, W - 80, H - 66), value=v.fields.get("compose", "")))
        s.elements.append(Element("send", "button", "Send", (W - 72, H - 110, W - 12, H - 66), action=("send", name)))
        return s

    def _screen_contacts(self, v: View) -> Screen:
        s = Screen("contacts", "Contacts")
        for i, (name, number) in enumerate(self.data["contacts"].items()):
            self._list_item(s, i, name, value=number, action=("go", "thread", name), top=110)
        return s

    def _screen_clock(self, v: View) -> Screen:
        s = Screen("clock", "Clock - Alarms")
        s.elements.append(Element("add_alarm", "button", "Add alarm", (W - 64, 52, W - 16, 92), action=("go", "alarm_edit")))
        for i, a in enumerate(self.data["alarms"]):
            y = 110 + i * 84
            s.elements.append(Element(f"alarm_{i}", "toggle", f"{a['time']} {a['label']}", (0, y, W, y + 84),
                                      value="on" if a["on"] else "off", action=("toggle_alarm", a)))
        return s

    def _screen_alarm_edit(self, v: View) -> Screen:
        s = Screen("alarm_edit", "New alarm")
        s.elements.append(Element("cancel", "button", "Cancel", (8, 52, 90, 92), action=("cancel",)))
        s.elements.append(Element("save", "button", "Save", (W - 80, 52, W - 8, 92), action=("save_alarm",)))
        s.elements.append(Element("alarm_time", "field", "Time (like 6:30 AM)", (16, 130, W - 16, 190), value=v.fields.get("alarm_time", "")))
        s.elements.append(Element("alarm_label", "field", "Label", (16, 210, W - 16, 254), value=v.fields.get("alarm_label", "")))
        return s

    def _screen_calendar(self, v: View) -> Screen:
        s = Screen("calendar", "Calendar - Today, " + time.strftime("%A %B %-d"))
        s.elements.append(Element("add_event", "button", "Add event", (W - 64, 52, W - 16, 92), action=("go", "event_edit")))
        for i, ev in enumerate(self.data["calendar"]):
            self._list_item(s, i, f"{ev['time']} {ev['title']}", value=ev["where"],
                            action=("go", "event", ev), top=110, h=76)
        return s

    def _screen_event(self, v: View, ev: dict) -> Screen:
        s = Screen("event", ev["title"])
        s.elements.append(Element("back", "button", "Back", (8, 52, 80, 92), action=("cancel",)))
        for j, line in enumerate([ev["title"], f"{ev['time']} - {ev['end']}", f"Where: {ev['where']}"]):
            s.elements.append(Element(f"ev_{j}", "text", line, (16, 120 + j * 44, W - 16, 160 + j * 44)))
        return s

    def _screen_event_edit(self, v: View) -> Screen:
        s = Screen("event_edit", "New event (today)")
        s.elements.append(Element("cancel", "button", "Cancel", (8, 52, 90, 92), action=("cancel",)))
        s.elements.append(Element("save", "button", "Save", (W - 80, 52, W - 8, 92), action=("save_event",)))
        for i, (key, label) in enumerate([("event_title", "Title"), ("event_start", "Start (like 3:00 PM)"),
                                          ("event_end", "End (optional)"), ("event_where", "Where (optional)")]):
            y = 130 + i * 70
            s.elements.append(Element(key, "field", label, (16, y, W - 16, y + 52), value=v.fields.get(key, "")))
        return s

    def _screen_settings(self, v: View) -> Screen:
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

    def _now_playing(self, s: Screen, y: int) -> int:
        m = self.data["music"]
        if not m["now"]:
            return y
        n = m["now"]
        state = "Now playing" if m["playing"] else "Paused"
        s.elements.append(Element("now", "text", f"{state}: {n['title']} - {n['artist']}", (16, y, W - 16, y + 40)))
        s.elements.append(Element("play_pause", "button", "Pause" if m["playing"] else "Play",
                                  (16, y + 44, 186, y + 88), action=("toggle_play",)))
        s.elements.append(Element("next", "button", "Next song", (204, y + 44, W - 16, y + 88), action=("next_song",)))
        return y + 100

    def _screen_music(self, v: View) -> Screen:
        s = Screen("music", "Music")
        y = self._now_playing(s, 104)
        s.elements.append(Element("music_q", "field", "Search songs, artists, playlists", (12, y, W - 104, y + 44),
                                  value=v.fields.get("music_q", "")))
        s.elements.append(Element("music_search", "button", "Search", (W - 96, y, W - 12, y + 44),
                                  action=("music_search",)))
        for i, name in enumerate(self.data["music"]["playlists"]):
            songs = self.data["music"]["playlists"][name]
            self._list_item(s, i, f"Playlist: {name}", value=f"{len(songs)} songs",
                            action=("go", "playlist", name), top=y + 60, h=64)
        return s

    def _screen_playlist(self, v: View, name: str) -> Screen:
        s = Screen("playlist", f"Playlist: {name}")
        s.elements.append(Element("back", "button", "Back", (8, 52, 80, 92), action=("cancel",)))
        s.elements.append(Element("play_all", "button", "Play playlist", (16, 104, W - 16, 148),
                                  action=("play_playlist", name)))
        for i, (title, artist) in enumerate(self.data["music"]["playlists"][name]):
            self._list_item(s, i, title, value=artist, action=("play_song", name, i), top=164, h=64)
        return s

    def _screen_music_results(self, v: View, query: str) -> Screen:
        s = Screen("music_results", f"Music results for \"{query}\"")
        s.elements.append(Element("back", "button", "Back", (8, 52, 80, 92), action=("cancel",)))
        q = query.lower()
        hits = []
        for pl, songs in self.data["music"]["playlists"].items():
            if q in pl.lower():
                hits.append((f"Playlist: {pl}", f"{len(songs)} songs", ("go", "playlist", pl)))
            for i, (title, artist) in enumerate(songs):
                if q in title.lower() or q in artist.lower():
                    hits.append((title, f"{artist} - {pl}", ("play_song", pl, i)))
        if not hits:
            s.elements.append(Element("none", "text", "No results. Try a playlist name.", (16, 120, W - 16, 160)))
        for i, (label, value, action) in enumerate(hits[:8]):
            self._list_item(s, i, label, value=value, action=action, top=110)
        return s

    def _screen_maps(self, v: View) -> Screen:
        s = Screen("maps", "Maps")
        y = 104
        nav = self.data["navigation"]
        if nav:
            s.elements.append(Element("nav", "text", f"Directions to {nav['to']}: {nav['minutes']} min {nav['mode']}",
                                      (16, y, W - 16, y + 40)))
            s.elements.append(Element("end_nav", "button", "End directions", (16, y + 44, W - 16, y + 88),
                                      action=("stop_nav",)))
            y += 100
        s.elements.append(Element("map_q", "field", "Search places", (12, y, W - 104, y + 44),
                                  value=v.fields.get("map_q", "")))
        s.elements.append(Element("map_search", "button", "Search", (W - 96, y, W - 12, y + 44),
                                  action=("map_search",)))
        for i, (label, query) in enumerate([("Pharmacy", "pharmacy"), ("Coffee", "coffee"),
                                            ("Grocery", "grocery"), ("Gas", "gas")]):
            x = 12 + i * 94
            s.elements.append(Element(f"cat_{query}", "button", label, (x, y + 60, x + 86, y + 100),
                                      action=("map_search", query)))
        return s

    def _screen_map_results(self, v: View, query: str) -> Screen:
        s = Screen("map_results", f"Places for \"{query}\", nearest first")
        s.elements.append(Element("back", "button", "Back", (8, 52, 80, 92), action=("cancel",)))
        hits = _search_places(self.data["places"], query)
        if not hits:
            s.elements.append(Element("none", "text", "No places found nearby.", (16, 120, W - 16, 160)))
        for i, p in enumerate(hits[:6]):
            self._list_item(s, i, p["name"], value=f"{p['miles']} mi, {p['address']}, {p['hours']}",
                            action=("go", "place", p), top=110, h=72)
        return s

    def _screen_place(self, v: View, p: dict) -> Screen:
        s = Screen("place", p["name"])
        s.elements.append(Element("back", "button", "Back", (8, 52, 80, 92), action=("cancel",)))
        info = [p["name"], p["address"], f"{p['miles']} miles away: {p['walk']} min walk, {p['drive']} min drive",
                p["hours"][:1].upper() + p["hours"][1:], f"Phone {p['phone']}"]
        for j, line in enumerate(info):
            s.elements.append(Element(f"pl_{j}", "text", line, (16, 110 + j * 40, W - 16, 146 + j * 40)))
        y = 110 + len(info) * 40 + 16
        s.elements.append(Element("walk", "button", "Walking directions", (16, y, W - 16, y + 44),
                                  action=("start_nav", p, "walking")))
        s.elements.append(Element("drive", "button", "Driving directions", (16, y + 56, W - 16, y + 100),
                                  action=("start_nav", p, "driving")))
        return s

    def _screen_unavailable(self, v: View, app: str) -> Screen:
        s = Screen("unavailable", app)
        s.elements.append(Element("na", "text", f"{app} is not available on this phone.", (16, 300, W - 16, 340)))
        return s

    # ----- rendering ---------------------------------------------------
    def look(self, view: str | None = None) -> Image.Image:
        """The screen as a phone screenshot (no id marks): ``view``, or the one on show."""

        with self._lock:
            return self._render(self.views.get(view or self.shown) or self.views[MAIN], marks=False)

    def screenshot_png(self, marks: bool = True, view: str | None = None) -> bytes:
        with self._lock:
            img = self._render(self.views.get(view or self.shown) or self.views[MAIN], marks)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _render(self, v: View, marks: bool) -> Image.Image:
        s = v.screen
        dark = s.name == "home"
        img = Image.new("RGB", (W, H), "#1d2b4f" if dark else "#f2f2f7")
        d = ImageDraw.Draw(img)
        fg = "white" if dark else "black"
        d.text((28, 14), time.strftime("%-I:%M"), font=F["status"], fill=fg)
        st = self.data["settings"]
        flags = ["DND"] if st["Do Not Disturb"] else []
        if st["Airplane Mode"]:
            flags.append("AIR")
        elif st["Wi-Fi"]:
            flags.append("WiFi")
        d.text((W - 28, 14), "  ".join(flags + ["100%"]), font=F["status"], fill=fg, anchor="ra")

        if not dark:
            title = s.title if len(s.title) < 26 else s.title[:24] + "..."
            tx = 96 if any(e.id in ("back", "cancel") for e in s.elements) else 20
            d.text((tx, 58), title, font=F["title"] if len(title) < 18 else F["bold"], fill="black")

        for e in s.elements:
            self._draw_element(d, s, e)

        if v.toast:
            d.rounded_rectangle((40, H - 190, W - 40, H - 140), 22, fill="#1c1c1e")
            d.text((W // 2, H - 165), v.toast, font=F["bold"], fill="white", anchor="mm")
        d.rounded_rectangle((W // 2 - 70, H - 14, W // 2 + 70, H - 9), 3, fill=fg)

        banner = self._banner()
        if banner:
            d.rounded_rectangle((10, 40, W - 10, 112), 18, fill="#fbfbfd", outline="#d1d1d6")
            d.text((26, 50), f"{banner['app'].upper()}  {banner['title']}", font=F["small"], fill="#6e6e73")
            text = banner["text"] if len(banner["text"]) < 38 else banner["text"][:36] + "..."
            d.text((26, 74), text, font=F["bold"], fill="black")

        if v.last_tap:
            x0, y0, x1, y1 = v.last_tap
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
            short = {"New message": "+", "Add alarm": "+", "Add event": "+", "Brightness down": "-",
                     "Brightness up": "+"}.get(e.label, e.label)
            fill = "#007aff" if e.id in ("send", "open_chat", "save") else "#e5e5ea"
            d.rounded_rectangle(e.box, 12, fill=fill)
            color = "white" if fill == "#007aff" else "#007aff"
            d.text(((x0 + x1) // 2, (y0 + y1) // 2), short, font=F["bold"], fill=color, anchor="mm")
        elif e.kind in ("item", "toggle"):
            d.rectangle((x0, y0, x1, y1 - 1), fill="white")
            d.line((x0 + 16, y1 - 1, x1, y1 - 1), fill="#d1d1d6")
            d.text((x0 + 30, y0 + 12), e.label, font=F["bold"], fill="black")
            if e.kind == "item" and e.value:
                val = e.value if len(e.value) < 40 else e.value[:38] + "..."
                d.text((x0 + 30, y0 + 38), val, font=F["small"], fill="#6e6e73")
            if e.kind == "toggle":
                on = e.value == "on"
                tx, ty = x1 - 76, (y0 + y1) // 2 - 15
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


ROUTES = {
    "Messages": ("messages",), "Clock": ("clock",), "Calendar": ("calendar",),
    "Settings": ("settings",), "Contacts": ("contacts",), "Music": ("music",), "Maps": ("maps",),
}


def _search_places(places: list[dict], query: str) -> list[dict]:
    """Places whose name or kind matches any query word, nearest first."""

    words = [_PLACE_WORDS.get(w, w) for w in re.findall(r"[a-z]+", query.lower())]
    words = [w for w in words if len(w) > 2 and w not in {"the", "nearest", "closest", "near", "nearby", "best",
                                                           "open", "find", "me", "and", "for", "now"}]
    hits = [p for p in places if any(w in p["kind"].split() or w in p["name"].lower() for w in words)]
    return sorted(hits, key=lambda p: p["miles"])


class PhoneSession:
    """One task's display on the shared phone."""

    background = False  # never Rose's workspace (the real phone's RealSession may be)

    def __init__(self, phone: Phone, name: str):
        self.phone, self.name = phone, name

    @property
    def route(self) -> tuple:
        return self.phone.route_of(self.name)

    def ui_tree(self) -> str:
        return self.phone.ui_tree(self.name)

    def element(self, mark) -> Element | None:
        return self.phone.element(mark, self.name)

    def open_app(self, app: str) -> ActionResult:
        return self.phone.open_app(app, view=self.name)

    def tap(self, mark) -> ActionResult:
        return self.phone.tap(mark, view=self.name)

    def look(self) -> Image.Image | None:
        """This display as a screenshot (only with vision on)."""

        return self.phone.look(self.name) if self.phone.vision else None

    def no_image_reason(self) -> str:
        return "vision is off"

    def tap_point(self, x: float, y: float, target: str = "") -> ActionResult:
        """Tap a point of the screenshot, ``x`` and ``y`` from 0 to 1000 (left to right, top to bottom)."""

        if not (0 <= x <= 1000 and 0 <= y <= 1000):
            return ActionResult(False, "x and y go from 0 to 1000 across the screenshot.")
        px, py = min(W - 1, round(x / 1000 * W)), min(H - 1, round(y / 1000 * H))
        return self.phone.tap_point(px, py, view=self.name)

    def type_text(self, mark, text: str) -> ActionResult:
        return self.phone.type_text(mark, text, view=self.name)

    def back(self) -> ActionResult:
        return self.phone.back(view=self.name)

    def home(self) -> ActionResult:
        return self.phone.home(view=self.name)

    def close(self) -> None:
        self.phone.close_session(self.name)


def _parse_time(raw: str) -> str | None:
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m?\.?\s*", raw or "", re.I)
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


def _clock(minutes: int) -> str:
    h, m = divmod(minutes % (24 * 60), 60)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"
