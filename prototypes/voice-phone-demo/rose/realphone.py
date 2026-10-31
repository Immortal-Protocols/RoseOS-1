"""The real phone: Rose Agent on an Android phone, reached over USB (``adb reverse tcp:8091 tcp:8091``)
or, once paired by QR code, over Wi-Fi or Tailscale through the TLS listener (rose/remote.py).

Rose Agent dials ``/ws/phone``. Commands go down as ``{"id", "action", ...args}`` and results
come back as ``{"type": "result", "id", "result"}`` (the phone side is
workspace/rose-agent/app/src/dev/pixel/voice/LocalRosePhone.java). Screen commands
(inspect_ui, tap_label, tap, set_text, scroll, open_app, list_apps, back, home) run through Rose
Agent's own accessibility path, with its stale-screen, password and lock-screen guards; a few
direct intents (set_alarm, set_timer, directions, search_places, play_music, media_key) skip
tapping.

``RealPhone`` has the surface of ``rose.phone.Phone`` that the task agent, the conductor and the
dashboard use. Reads (``ui_tree``, ``element``, ``screen``, ``RealSession.look``) come from each
session's last observation and never wait; actions block on the phone, so the task agent runs them
off the MLX thread (``RealSession.blocking``).

Vision (``RealPhone.vision``, on by default): every observation is the UI tree plus Rose Agent's
``screenshot`` of the same screen, so a step's image and tree match. Each action reads the tree
again, but the screenshot is taken lazily, once, when the next observation is built
(``RealSession.ensure_image``): calls written in one reply do not each pay for a screenshot the
model never sees. ``RealPhone.look()`` takes a fresh screenshot (blocking: call it off the MLX
thread). All screenshots go through ``RealPhone.capture``, which serialises them and keeps them
``SHOT_GAP_S`` apart: Android refuses a second accessibility screenshot within ~333 ms
(``ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT``), so the talker's look and an agent step would
otherwise collide; a refusal for that reason is retried once.
``RealSession.tap_point`` taps a point the model picked in the screenshot (0-1000 relative
coordinates) through Rose Agent's ``touch_point``, for controls the tree lacks. Screenshots stay
in memory: they are decoded here, given only to the local model, and never written or logged.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import concurrent.futures
import io
import json
import re
import threading
import time
import uuid
from typing import Any, Callable

from PIL import Image, ImageDraw

from .phone import ActionResult, Element, Screen, W, H, _parse_time

MAX_ELEMENTS = 70   # rows shown to the agent per screen
SETTLE_S = 0.35     # after an action, before reading the screen again
CALL_TIMEOUT_S = 25.0
POINT_SCALE = 1000  # tap_point coordinates: 0..1000 across the screenshot (Qwen's grounding scale)
SHOT_GAP_S = 0.35   # between screenshots: Android throttles takeScreenshot to one per 333 ms
SHOT_REUSE_S = 0.3  # look() reuses a screenshot this fresh instead of taking another
ERROR_INTERVAL_TIME_SHORT = 3  # AccessibilityService.ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT
SHOT_WHY = {"locked": "the phone is locked", "secure_window": "this screen blocks screenshots",
            "accessibility_disabled": "Rose Agent phone controls are off",
            "offline": "the phone is not connected", "unsupported": "this Android version cannot take one"}


class PhoneLink:
    """The /ws/phone websocket. ``call`` may be used from any thread except the server's loop."""

    def __init__(self, on_change: Callable[[], None] | None = None):
        self._ws: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: dict[str, concurrent.futures.Future] = {}
        self._lock = threading.Lock()
        self._authenticated = False  # the current link came through the remote signature check
        self.model = ""
        self.on_change = on_change

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def serve(self, ws: Any, authenticated: bool = False) -> None:
        """Serve one phone connection; the newest wins, except that an unauthenticated (USB)
        connection never replaces an authenticated (paired, remote) one."""

        await ws.accept()
        with self._lock:
            refused = not authenticated and self._ws is not None and self._authenticated
            if not refused:
                old = self._ws
                self._ws, self._loop, self._authenticated = ws, asyncio.get_running_loop(), authenticated
        if refused:
            with _quiet():
                await ws.close(code=4009, reason="a paired phone is connected")
            return
        if old is not None:
            with _quiet():
                await old.close()
        self._changed()
        try:
            while True:
                m = json.loads(await ws.receive_text())
                if m.get("type") == "hello":
                    self.model = str(m.get("model") or "")
                    self._changed()
                elif m.get("type") == "result":
                    with self._lock:
                        fut = self._pending.pop(str(m.get("id")), None)
                    if fut is not None and not fut.done():
                        fut.set_result(m.get("result") or {})
        except Exception:
            pass  # the phone went away
        finally:
            with self._lock:
                if self._ws is ws:
                    self._ws, self._authenticated = None, False
                    lost, self._pending = self._pending, {}
                else:
                    lost = {}
            for fut in lost.values():
                if not fut.done():
                    fut.set_result({"status": "disconnected", "message": "The phone disconnected."})
            self._changed()

    def call(self, action: str, timeout: float = CALL_TIMEOUT_S, **args: Any) -> dict:
        with self._lock:
            ws, loop = self._ws, self._loop
            if ws is None or loop is None:
                return {"status": "offline",
                        "message": "Rose Agent is not connected. Open it on the phone "
                                   "(paired over Wi-Fi or Tailscale, or on USB)."}
            cid = str(uuid.uuid4())
            fut: concurrent.futures.Future = concurrent.futures.Future()
            self._pending[cid] = fut
        asyncio.run_coroutine_threadsafe(ws.send_text(json.dumps({"id": cid, "action": action, **args})), loop)
        try:
            return fut.result(timeout)
        except concurrent.futures.TimeoutError:
            with self._lock:
                self._pending.pop(cid, None)
            return {"status": "timeout", "message": f"The phone did not answer {action} in time."}

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()


class RealSession:
    """One task's view of the phone: its last observation and the actions that change it."""

    blocking = True  # actions wait on the phone: run them off the MLX thread

    def __init__(self, phone: "RealPhone", name: str):
        self.phone, self.name = phone, name
        self.obs: dict = {}
        self.screen = Screen("phone", "Phone (not read yet)", [])
        self._nodes: dict[int, dict] = {}   # mark -> node
        self._by_mark: dict[int, Element] = {}
        self.image: Image.Image | None = None  # the screenshot taken with obs (vision on)
        self.shot: dict = {}  # its status and sizes: width, height (image), displayWidth, displayHeight
        self._stale = False  # obs changed since the screenshot: ensure_image takes a new one

    # ----- reads (never wait) ------------------------------------------
    @property
    def route(self) -> tuple:
        return (self.obs.get("packageName"), self.obs.get("windowId"))

    def ui_tree(self) -> str:
        lines = [f"Screen: {self.screen.title}"]
        lines += ["  " + e.describe() for e in self.screen.elements]
        if self.obs.get("truncated"):
            lines.append("  (more on screen than shown; scroll to see the rest)")
        return "\n".join(lines)

    def element(self, mark) -> Element | None:
        try:
            return self._by_mark.get(int(mark))
        except (TypeError, ValueError):
            return None

    def look(self) -> Image.Image | None:
        """The screenshot that goes with the current UI tree (None without vision or when refused).

        Never waits: call ``ensure_image`` (off the MLX thread) first to take it."""

        return None if self._stale else self.image

    def no_image_reason(self) -> str:
        """Why there is no screenshot of this screen, for the agent's prompt."""

        status = self.shot.get("status") or "not taken"
        return SHOT_WHY.get(status, f"the capture failed ({status})")

    # ----- actions (wait on the phone) ---------------------------------
    def refresh(self) -> dict:
        r = self.phone.link.call("inspect_ui")
        self._observe(r)
        self._capture()
        return r

    def _observed(self, r: dict) -> None:
        """Take an action's returned observation, or read the screen again."""

        if isinstance(r.get("observation"), dict):
            self._observe(r["observation"])
            self._capture()
        else:
            self.refresh()

    def _capture(self) -> None:
        """The screen was just inspected: its screenshot is now due (taken by ``ensure_image``)."""

        self.image, self.shot = None, {}
        self._stale = bool(self.phone.vision)

    def ensure_image(self) -> Image.Image | None:
        """Take the screenshot of the screen last inspected, if it is due (blocking).

        Called once per observation, right before the model reads it, so a step's image and
        tree match and earlier calls of the same reply cost no screenshot. Vision on and the
        screen readable only; otherwise ``shot`` says why there is none."""

        if not self._stale:
            return self.image
        self._stale = False
        if self.obs.get("status") != "ok":
            self.shot = {"status": self.obs.get("status") or "unreadable"}
            return None
        self.image, self.shot = self.phone.capture()
        return self.image

    def open_app(self, app: str) -> ActionResult:
        pkg, label = self.phone.resolve_app(app)
        if pkg is None:
            return ActionResult(False, f"No app called {app} on this phone.")
        r = self.phone.link.call("open_app", packageName=pkg)
        self._observed(r)
        ok = r.get("status") in ("opened", "launch_unverified")
        return ActionResult(ok, f"Opened {label}." if ok else _why(r), changed=ok)

    def tap(self, mark) -> ActionResult:
        el, node = self.element(mark), self._nodes.get(_int(mark))
        if el is None or node is None:
            return ActionResult(False, f"There is no [{mark}] on this screen.")
        label = node.get("label")
        if label:
            r = self.phone.link.call("tap_label", snapshotId=self.obs.get("snapshotId"), label=label,
                                     nodeId=node["id"])
            if r.get("status") in ("ambiguous_label", "label_target_mismatch", "label_not_found"):
                r = self.phone.link.call("tap", snapshotId=self.obs.get("snapshotId"), nodeId=node["target"])
        else:
            r = self.phone.link.call("tap", snapshotId=self.obs.get("snapshotId"), nodeId=node["target"])
        res = self._after(r, f"Tapped {el.label or '[' + str(mark) + ']'}.")
        if self.phone.vision:
            # Android accepting a click is not the outcome: with screenshots, the agent checks
            # the next one before it may finish (tasks._defer_finish looks at ``changed``).
            res.changed = False
        return res

    def type_text(self, mark, text: str) -> ActionResult:
        el, node = self.element(mark), self._nodes.get(_int(mark))
        if el is None or node is None:
            return ActionResult(False, f"There is no [{mark}] on this screen.")
        if el.kind != "field":
            return ActionResult(False, f"[{mark}] is not a text field.")
        r = self.phone.link.call("set_text", snapshotId=self.obs.get("snapshotId"), nodeId=node["id"], text=text)
        return self._after(r, f"Typed into {el.label or 'the field'}.")

    def scroll(self, mark, direction: str = "forward") -> ActionResult:
        node = self._nodes.get(_int(mark))
        if node is None:
            return ActionResult(False, f"There is no [{mark}] on this screen.")
        direction = "backward" if str(direction).lower() in ("backward", "up", "back") else "forward"
        r = self.phone.link.call("scroll", snapshotId=self.obs.get("snapshotId"), nodeId=node["id"],
                                 direction=direction)
        return self._after(r, f"Scrolled {direction}.")

    def back(self) -> ActionResult:
        return self._after(self.phone.link.call("back"), "Went back.")

    def home(self) -> ActionResult:
        return self._after(self.phone.link.call("home"), "Went to the home screen.")

    def direct(self, action: str, **args: Any) -> ActionResult:
        """Alarms, timers, directions, places and music by Android intent (no tapping)."""

        r = self.phone.link.call(action, **args)
        if isinstance(r.get("observation"), dict):
            self._observed(r)
        ok = r.get("status") in ("started", "action_accepted")
        return ActionResult(ok, "Done; the screen now shows the result." if ok else _why(r), changed=ok)

    def tap_point(self, x: float, y: float, target: str = "") -> ActionResult:
        """Tap a point of the screenshot: ``x``, ``y`` from 0 to 1000 (left to right, top to bottom).

        For something the screenshot shows but the tree lacks. The point is mapped from the
        screenshot (``width`` x ``height``) to display pixels (``displayWidth`` x
        ``displayHeight``), and sent as Rose Agent's ``touch_point`` with the smallest enabled
        node of this snapshot that contains it (Rose Agent only taps inside an observed node's
        visible bounds, and never on a password field). Android only queues the touch, so the
        result is unverified (``changed`` False): the next screenshot shows what it did.
        """

        self.ensure_image()  # an earlier call of this reply changed the screen: map through the new one
        shot = self.shot
        if self.image is None or not shot.get("width") or not shot.get("displayWidth"):
            return ActionResult(False, "There is no screenshot of this screen to point at; use an [id].")
        if not (0 <= x <= POINT_SCALE and 0 <= y <= POINT_SCALE):
            return ActionResult(False, f"x and y go from 0 to {POINT_SCALE} across the screenshot.")
        px = display_point(x, y, shot)
        node = self._node_at(*px)
        if node is None:
            return ActionResult(False, "Nothing of this app is at that point; tap something inside the app.")
        if node.get("password"):
            return ActionResult(False, "That is a password field; the user types passwords directly on the phone.")
        r = self.phone.link.call("touch_point", snapshotId=self.obs.get("snapshotId"), nodeId=node["id"],
                                 x=px[0], y=px[1])
        what = f"Touched {target.strip() or 'the point'}"
        res = self._after(r, f"{what}; check the new screenshot to see whether it worked.")
        return ActionResult(res.ok, res.message, changed=False)

    def _node_at(self, x: int, y: int) -> dict | None:
        """The smallest enabled node of the last observation whose bounds contain (x, y)."""

        best, area = None, None
        for n in self.obs.get("nodes", []) if self.obs.get("status") == "ok" else []:
            b = n.get("bounds") or ()
            if len(b) != 4 or n.get("visible") is False or n.get("enabled") is False:
                continue
            if not (b[0] <= x < b[2] and b[1] <= y < b[3]):
                continue
            if n.get("password"):
                return n  # refused by the caller: never touch inside a password field
            a = (b[2] - b[0]) * (b[3] - b[1])
            if area is None or a < area:
                best, area = n, a
        return best

    def _after(self, r: dict, done: str) -> ActionResult:
        ok = r.get("status") in ("action_accepted", "gesture_dispatched")
        time.sleep(SETTLE_S)
        self.refresh()
        return ActionResult(ok, done if ok else _why(r), changed=ok)

    # ----- observation -> elements -------------------------------------
    def _observe(self, r: dict) -> None:
        self.obs = r if isinstance(r, dict) else {}
        status = self.obs.get("status")
        if status != "ok":
            title = {"locked": "Locked: the phone needs the user to unlock it",
                     "accessibility_disabled": "Rose Agent phone controls are off (Settings > Accessibility)",
                     "offline": "Not connected"}.get(status, f"Unreadable ({status or 'no answer'})")
            self.screen, self._nodes, self._by_mark = Screen("phone", title, []), {}, {}
            self.phone.touched(self)
            return
        nodes = {n["id"]: n for n in self.obs.get("nodes", [])}
        targeted = {n.get("clickTarget") for n in nodes.values() if n.get("clickTarget") is not None}
        elements, marks = [], {}
        for n in nodes.values():
            if n.get("visible") is False or n.get("password"):
                continue
            label = _clean(n.get("text") or n.get("description") or "")
            target = n.get("clickTarget")
            if n.get("editable"):
                kind, value = "field", _clean(n.get("text") or "")
                label = _clean(n.get("description") or _hint(n.get("viewId")) or "Text field")
                tgt = n["id"]
            elif label and (n.get("clickable") or target is not None):
                kind, value, tgt = ("toggle", "on" if n.get("checked") else "off", n["id"]) \
                    if n.get("checked") is not None and _toggle(n) else ("button", None, n["id"] if n.get("clickable") else target)
            elif n.get("clickable") and n["id"] not in targeted:
                kind, value, tgt = "button", None, n["id"]
                label = _hint(n.get("viewId")) or n.get("class", "").rsplit(".", 1)[-1] or "unlabelled"
            elif n.get("scrollable"):
                kind, value, tgt = "list", None, n["id"]
                label = label or "scrollable list"
            elif label:
                elements.append(Element(str(n["id"]), "text", label[:120], tuple(n.get("bounds") or (0, 0, 0, 0))))
                continue
            else:
                continue
            mark = len(marks) + 1
            el = Element(str(n["id"]), kind, label[:80], tuple(n.get("bounds") or (0, 0, 0, 0)), value, mark=mark)
            marks[mark] = {"id": n["id"], "target": tgt, "label": _clean(n.get("text") or n.get("description") or "")}
            elements.append(el)
            if len(marks) >= MAX_ELEMENTS:
                break
        app = self.phone.app_label(self.obs.get("packageName", ""))
        self.screen = Screen("phone", app, elements)
        self._nodes = marks
        self._by_mark = {e.mark: e for e in elements if e.mark}
        self.phone.touched(self)


class RealPhone:
    """The real phone behind Rose Agent, with the fake ``Phone``'s surface."""

    real = True
    vision = True  # observations carry a screenshot; the agent may tap points in it

    def __init__(self, bus=None):
        self.bus = bus
        self.listeners: list[Callable[[str, dict], None]] = []
        self.version = 0
        self.data: dict = {"contacts": [], "calendar": []}  # not read from the phone yet
        self.link = PhoneLink(on_change=lambda: self._changed("link", {"connected": self.link.connected}))
        self._sessions: dict[str, RealSession] = {}
        self._shown: RealSession | None = None
        self._apps: list[dict] = []
        self._apps_at = 0.0
        self._lock = threading.RLock()
        self.look_status = ""
        self._shot_lock = threading.Lock()  # one screenshot at a time, SHOT_GAP_S apart
        self._shot_at = float("-inf")  # monotonic time the last screenshot call returned
        self._last_shot: tuple[float, Image.Image | None, dict] = (float("-inf"), None, {})

    # ----- the fake Phone's surface ------------------------------------
    def session(self, name: str) -> RealSession:
        with self._lock:
            if name not in self._sessions:
                self._sessions[name] = RealSession(self, name)
            return self._sessions[name]

    def close_session(self, name: str) -> None:
        with self._lock:
            self._sessions.pop(name, None)

    def screen(self, view: str | None = None) -> Screen:
        s = self._session_for(view)
        return s.screen if s else Screen("phone", "Phone", [])

    def ui_tree(self, view: str | None = None) -> str:
        s = self._session_for(view)
        return s.ui_tree() if s else "Screen: (not read yet)"

    def element(self, mark, view: str | None = None) -> Element | None:
        s = self._session_for(view)
        return s.element(mark) if s else None

    def summary(self) -> str:
        where = f"the user's real {self.link.model or 'Android'} phone" + ("" if self.link.connected else
                                                                           " (not connected right now)")
        return (f"Phone: {where}, controlled through Rose Agent. Rose cannot read its contacts, "
                "calendar or messages yet; for those, start a phone task. To see what is on its "
                "screen, use look_at_screen.")

    def snapshot(self) -> dict:
        s = self._shown
        return {"version": self.version, "real": True, "connected": self.link.connected, "model": self.link.model,
                "view": s.name if s else "main", "route": [str(r) for r in (s.route if s else ())],
                "screen": "phone", "title": s.screen.title if s else "Real phone", "toast": None, "banner": None,
                "last_tap": None, "size": [W, H], "elements": [],
                "status": {"time": time.strftime("%-I:%M"), "dnd": False, "wifi": True, "airplane": False}}

    def screenshot_png(self, marks: bool = False, view: str | None = None) -> bytes:
        """A placeholder for the dashboard: the real screen is on the phone in your hand."""

        img = Image.new("RGB", (W, H), "#1b1418")
        d = ImageDraw.Draw(img)
        s = self._session_for(view) or self._shown
        lines = ["Real phone", self.link.model or "", "connected" if self.link.connected else "not connected", ""]
        if s is not None:
            lines.append(s.screen.title)
            lines += [e.label[:34] for e in s.screen.elements[:24]]
        y = 60
        for line in lines:
            d.text((28, y), line, fill="#eadde2")
            y += 26
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    png = screenshot_png

    def look(self, view: str | None = None) -> Image.Image | None:
        """A fresh screenshot of the phone's screen, or None (locked, secure screen, offline).

        Blocking (it waits on the phone): call it off the MLX thread. ``view`` is accepted for
        the fake Phone's surface; the real phone has one screen. ``look_status`` says why the
        last call returned None. A screenshot another caller (a task step) took within
        ``SHOT_REUSE_S`` is reused rather than asking Android for another one. The image is only
        kept in memory.
        """

        with self._shot_lock:
            at, image, shot = self._last_shot
            if image is not None and time.monotonic() - at <= SHOT_REUSE_S:
                self.look_status = shot.get("status", "")
                return image
        image, shot = self.capture()
        self.look_status = shot.get("status", "")
        return image

    def capture(self) -> tuple[Image.Image | None, dict]:
        """(image, info) from Rose Agent's ``screenshot``; info has the status and sizes, never the bytes.

        Blocking. Calls are serialised and spaced ``SHOT_GAP_S`` apart, because Android refuses an
        accessibility screenshot within ~333 ms of the last one; if it refuses anyway (another
        screenshot on the phone, e.g. Rose Agent's own), the call waits out the interval and
        retries once."""

        with self._shot_lock:
            r = self._screenshot()
            if r.get("status") == "capture_failed" and r.get("errorCode") == ERROR_INTERVAL_TIME_SHORT:
                r = self._screenshot()
            image, info = self._decode(r)
            self._last_shot = (self._shot_at, image, info)
            return image, info

    def _screenshot(self) -> dict:
        """One ``screenshot`` call, at least SHOT_GAP_S after the last one returned (lock held)."""

        wait = SHOT_GAP_S - (time.monotonic() - self._shot_at)
        if wait > 0:
            time.sleep(wait)
        try:
            return self.link.call("screenshot")
        finally:
            self._shot_at = time.monotonic()

    @staticmethod
    def _decode(r: dict) -> tuple[Image.Image | None, dict]:
        info = {k: r.get(k) for k in ("status", "errorCode", "width", "height", "displayWidth",
                                       "displayHeight", "packageName") if k in r}
        if r.get("status") != "ok":
            return None, info
        try:
            with Image.open(io.BytesIO(base64.b64decode(r.get("imageBase64") or "", validate=True))) as img:
                image = img.convert("RGB")
        except (binascii.Error, OSError, ValueError):
            return None, {**info, "status": "undecodable"}
        info.setdefault("width", image.width)
        info.setdefault("height", image.height)
        if image.size != (info["width"], info["height"]):
            info["width"], info["height"] = image.size
        return image, info

    def notify(self, app: str, title: str, text: str) -> None:
        pass  # real notifications come from the phone (not read yet)

    def reset(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._shown = None
        self._changed("reset", {})

    # ----- apps --------------------------------------------------------
    def apps(self) -> list[dict]:
        if not self._apps or time.monotonic() - self._apps_at > 300:
            r = self.link.call("list_apps")
            if r.get("status") == "ok" or r.get("apps"):
                self._apps, self._apps_at = list(r.get("apps") or []), time.monotonic()
        return self._apps

    def app_label(self, pkg: str) -> str:
        """Called on an action's worker thread, so loading the app list here may wait."""

        for a in self._apps or self.apps():
            if a.get("packageName") == pkg:
                return str(a.get("label"))
        return pkg or "Phone"

    def resolve_app(self, name: str) -> tuple[str | None, str]:
        want = _clean(name).lower()
        apps = self.apps()
        if re.fullmatch(r"[a-z][\w]*(\.[\w]+)+", want):
            return want, name
        for test in (lambda l: l == want, lambda l: l.startswith(want), lambda l: want in l,
                     lambda l: any(w and w in l for w in want.split())):
            hits = [a for a in apps if test(str(a.get("label", "")).lower())]
            if hits:
                return str(hits[0]["packageName"]), str(hits[0]["label"])
        return None, name

    # ----- internals ---------------------------------------------------
    def touched(self, s: RealSession) -> None:
        self._shown = s
        self._changed("screen", {"view": s.name, "title": s.screen.title})

    def _session_for(self, view: str | None) -> RealSession | None:
        with self._lock:
            return self._sessions.get(view) if view else self._shown

    def _changed(self, what: str, info: dict) -> None:
        self.version += 1
        for fn in list(self.listeners):
            try:
                fn(what, info)
            except Exception:
                pass


def alarm_time(raw: str) -> tuple[int, int] | None:
    """"6:30 AM" -> (6, 30); "18:05" -> (18, 5)."""

    t = _parse_time(raw)
    if t is not None:
        m = re.match(r"(\d+):(\d+) ([AP])M", t)
        if m:
            return int(m.group(1)) % 12 + (12 if m.group(3) == "P" else 0), int(m.group(2))
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", raw or "")
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return int(m.group(1)), int(m.group(2))
    return None


def display_point(x: float, y: float, shot: dict) -> tuple[int, int]:
    """0-1000 screenshot coordinates -> display pixels.

    The point is first placed on the screenshot (``width`` x ``height``, which Rose Agent may have
    scaled down to 1600 px), then scaled by ``displayWidth / width`` and ``displayHeight / height``
    to the display, and kept inside it.
    """

    w, h = shot["width"], shot["height"]
    dw, dh = shot.get("displayWidth") or w, shot.get("displayHeight") or h
    ix, iy = x / POINT_SCALE * w, y / POINT_SCALE * h
    return (min(dw - 1, max(0, round(ix * dw / w))), min(dh - 1, max(0, round(iy * dh / h))))


def _why(r: dict) -> str:
    status = r.get("status") or "error"
    message = r.get("message") or r.get("reason") or ""
    return {"locked": "The phone is locked: the user needs to unlock it.",
            "accessibility_disabled": "Rose Agent phone controls are off on the phone.",
            "stale_screen": "The screen changed before this action; check it again.",
            "label_not_found": "That control is not on the screen any more.",
            "target_not_visible": "Nothing visible is at that spot.",
            "password_field": "That is a password field; the user types passwords directly on the phone.",
            "offline": message or "The phone is not connected."}.get(status, f"Failed ({status}). {message}".strip())


def _clean(s: str) -> str:
    return " ".join(str(s).split())


def _hint(view_id: str | None) -> str:
    """'com.app:id/send_button' -> 'send button'."""

    if not view_id:
        return ""
    return view_id.rsplit("/", 1)[-1].replace("_", " ").strip()


def _toggle(n: dict) -> bool:
    cls = str(n.get("class", ""))
    return any(k in cls for k in ("Switch", "CheckBox", "Toggle", "RadioButton"))


def _int(mark) -> int:
    try:
        return int(mark)
    except (TypeError, ValueError):
        return -1


class _quiet:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True
