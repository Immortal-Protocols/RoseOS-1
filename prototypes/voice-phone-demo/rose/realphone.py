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

Rose's workspace (background mode, on by default where the phone has it): RoseOS phones carry a
background controller (``dev.pixel.backgroundcontrol``) that runs one app on a separate virtual
display, so a task never drives the screen in the user's hand. ``RealPhone.background_available``
probes it once (``get_state``; again after a reconnect) and a task's ``RealSession`` then runs in
the workspace through ``rose.background.Workspace``: ``open_app`` starts (or reuses) a workspace
for that app, taps/typing/scrolls/back/``tap_point`` become controller commands, the UI tree and
screenshot come from the workspace (same rendered format, ids per observation), and ``home``
closes it. The workspace stays open after a task, so a follow-up on the same app continues there;
it closes on home, on another app, on the owner's Stop, after ``WORKSPACE_IDLE_S`` unused, and on
reset. If Android refuses an app there, the action says so and nothing happens on the visible
screen. A task runs on the visible screen only when started ``on_screen`` or when the phone has no
controller (older phones, the Fold): then everything is exactly as above. Alarms, timers, media
keys, music (audio is the user's) and directions (navigation is for the user) stay direct
intents; ``search_places`` opens Maps in the workspace and types the query there.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import concurrent.futures
import io
import json
import os
import re
import threading
import time
import uuid
from typing import Any, Callable

from PIL import Image, ImageDraw

from .background import CONTROLLER, CONTROLLER_STATES, WORKSPACE_IDLE_S, Workspace, Workspaces
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
            "offline": "the phone is not connected", "unsupported": "this Android version cannot take one",
            "no_background_app": "Rose's workspace is closed", "stopped_by_owner": "the owner stopped Rose's workspace",
            "workspace_closed": "Rose's workspace closed", "timeout": "the phone did not answer in time"}
# One controller operation. Rose Agent waits up to 20 s for an app_command result
# (LocalRosePhone.RUNNING_WAIT_MS) and the controller's node checks are accessibility calls of up
# to 5 s each (refresh, then the click), so wait longer than the phone does: a shorter wait loses
# answers the phone still sends (seen on a Pixel 10: start and taps over 12 s). Workspace._start
# also polls get_state if a start's reply is lost anyway.
WS_CALL_TIMEOUT_S = 23.0
BG_RETRY_S = 60.0   # a phone without the controller is probed again after this long
MAPS = "com.google.android.apps.maps"
LOCKED_SAY = "The phone is locked; unlock it and I'll continue."
WS_TITLES = {"no_background_app": "Rose's workspace is closed (open_app opens an app there)",
             "stopped_by_owner": "Rose's workspace was stopped by the owner",
             "workspace_closed": "Rose's workspace closed (open_app opens the app there again)",
             "locked": "Locked: the phone needs the user to unlock it"}


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
    """One task's view of the phone: its last observation and the actions that change it.

    ``background`` (decided at the first blocking call, then fixed): the task runs in Rose's
    workspace (``RealPhone.workspace``) instead of on the visible screen. ``on_screen=True``
    sessions always use the visible screen."""

    blocking = True  # actions wait on the phone: run them off the MLX thread

    def __init__(self, phone: "RealPhone", name: str, on_screen: bool = False, workspace=None):
        self.phone, self.name = phone, name
        self.scoped = workspace  # an independent session's own workspace; else the shared pool
        self.on_screen = bool(on_screen)
        self._background: bool | None = None
        self.obs: dict = {}
        self.screen = Screen("phone", "Phone (not read yet)", [])
        self._nodes: dict[int, dict] = {}   # mark -> node
        self._by_mark: dict[int, Element] = {}
        self.image: Image.Image | None = None  # the screenshot taken with obs (vision on)
        self.shot: dict = {}  # its status and sizes: width, height (image), displayWidth, displayHeight
        self._stale = False  # obs changed since the screenshot: ensure_image takes a new one

    @property
    def background(self) -> bool:
        """This task works in Rose's workspace, not on the user's screen.

        The first read may probe the phone (blocking: the task runner reads it in ``refresh``,
        off the MLX thread); a definite answer is then fixed for the session. While the probe has
        no definite answer (the phone is offline, ``get_state`` timed out, a transient error) this
        is True and not kept: an unanswered probe never moves a task onto the user's screen.
        The workspace calls then fail plainly ("not connected", "did not answer in time") and the
        next read probes again."""

        if self._background is None:
            if self.on_screen:
                self._background = False
            else:
                probe = self.phone.background_probe()
                if probe is None:
                    return True
                self._background = probe
        return self._background

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

    def control_labels(self, mark) -> list[str]:
        """Every name of the control ``mark`` taps: its node's and its click target's text,
        description and view id ("ic_send" -> "ic send"). For the send guard, which judges the
        real node rather than the label the agent sees (an icon-only Send has no text)."""

        node = self._nodes.get(_int(mark))
        if node is None:
            return []
        return _names_of(self._obs_nodes(), {node["id"], node["target"]})

    def labels_at(self, x: float, y: float) -> list[str]:
        """The names of what ``tap_point(x, y)`` would touch: the smallest node at the point and
        every clickable node containing it (the control it belongs to). Never waits; [] when
        there is no screenshot to map the point through."""

        shot = self.shot
        if self.image is None or not shot.get("width") or not shot.get("displayWidth"):
            return []
        try:
            px, py = display_point(float(x), float(y), shot)
        except (TypeError, ValueError):
            return []
        nodes = self._obs_nodes()
        hit = self._node_at(px, py)
        ids = {hit["id"]} if hit is not None else set()
        for n in nodes.values():
            b = n.get("bounds") or ()
            if n.get("clickable") and len(b) == 4 and b[0] <= px < b[2] and b[1] <= py < b[3]:
                ids.add(n["id"])
        return _names_of(nodes, ids)

    def _obs_nodes(self) -> dict:
        return {n.get("id"): n for n in self.obs.get("nodes", [])} if self.obs.get("status") == "ok" else {}

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
        if self.background:
            r = self._unit().inspect()
        else:
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
        screen readable only; otherwise ``shot`` says why there is none. In the workspace the
        screenshot is taken first and the tree read again right after it (``Workspace.observe``):
        the controller's screenshot clears its node map, and the agent then acts on that tree's
        snapshot."""

        if not self._stale:
            return self.image
        self._stale = False
        if self.obs.get("status") != "ok":
            self.shot = {"status": self.obs.get("status") or "unreadable"}
            return None
        if self.background:
            shot, tree = self._unit().observe()
            self._observe(tree)
            self.image, self.shot = self.phone._decode(shot)
            return self.image
        self.image, self.shot = self.phone.capture()
        return self.image

    @property
    def workspace(self):
        """The workspace this task works in: its own (independent sessions) or its pool unit."""

        return self.scoped or self.phone.workspaces.for_view(self.name)

    _unit = workspace.fget

    def _start_app(self, pkg: str) -> dict:
        return self.scoped.start(pkg) if self.scoped else self.phone.workspaces.start(self.name, pkg)

    def _cmd(self, action: str, **args: Any) -> dict:
        """One screen command, in the workspace or on the visible screen."""

        if self.background:
            return self._unit().command(action, **args)
        return self.phone.link.call(action, **args)

    def open_app(self, app: str) -> ActionResult:
        pkg, label = self.phone.resolve_app(app)
        if pkg is None:
            return ActionResult(False, f"No app called {app} on this phone.")
        if self.background:
            return self._open_in_workspace(pkg, label)
        r = self.phone.link.call("open_app", packageName=pkg)
        self._observed(r)
        ok = r.get("status") in ("opened", "launch_unverified")
        return ActionResult(ok, f"Opened {label}." if ok else _why(r), changed=ok)

    def _open_in_workspace(self, pkg: str, label: str) -> ActionResult:
        """Start (or reuse) Rose's workspace for ``pkg``; a refusal is said plainly, never worked around."""

        r = self._start_app(pkg)
        ok = r.get("status") in ("opened", "launch_unverified")
        if ok:
            self._observed(r)
            return ActionResult(True, f"Opened {label} in Rose's workspace.", changed=True)
        self.refresh()
        return ActionResult(False, _ws_why(r, label), changed=False)

    def tap(self, mark) -> ActionResult:
        el, node = self.element(mark), self._nodes.get(_int(mark))
        if el is None or node is None:
            return ActionResult(False, f"There is no [{mark}] on this screen.")
        label = node.get("label")
        if self.background:
            # The controller clicks the observed node itself (its clickable target); the workspace
            # re-finds it on a fresh inspection first.
            r = self._cmd("tap", snapshotId=self.obs.get("snapshotId"), nodeId=node["target"])
        elif label:
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
        r = self._cmd("set_text", snapshotId=self.obs.get("snapshotId"), nodeId=node["id"], text=text)
        return self._after(r, f"Typed into {el.label or 'the field'}.")

    def scroll(self, mark, direction: str = "forward") -> ActionResult:
        node = self._nodes.get(_int(mark))
        if node is None:
            return ActionResult(False, f"There is no [{mark}] on this screen.")
        direction = "backward" if str(direction).lower() in ("backward", "up", "back") else "forward"
        r = self._cmd("scroll", snapshotId=self.obs.get("snapshotId"), nodeId=node["id"], direction=direction)
        return self._after(r, f"Scrolled {direction}.")

    def back(self) -> ActionResult:
        if self.background:
            return self._after(self._cmd("back"), "Went back.")
        return self._after(self.phone.link.call("back"), "Went back.")

    def home(self) -> ActionResult:
        if self.background:
            # The workspace has no home screen: going home ends it (BackgroundPhone: home == stop).
            r = self._cmd("home")
            return self._after(r, "Closed Rose's workspace.", settle=False)
        return self._after(self.phone.link.call("home"), "Went to the home screen.")

    def direct(self, action: str, **args: Any) -> ActionResult:
        """Alarms, timers, directions, places and music by Android intent (no tapping).

        In the workspace, ``search_places`` opens Maps there and types the query (it only shows
        results, so it belongs on Rose's screen). The rest stay intents on the phone itself:
        alarms and timers skip the UI, music and media keys are audio the user hears, and
        directions start navigation, which is for the user. Their result screen is not the
        workspace's, so it is not taken as this session's observation."""

        if self.background and action == "search_places":
            return self._search_in_workspace(str(args.get("query", "")))
        r = self.phone.link.call(action, **args)
        ok = r.get("status") in ("started", "action_accepted")
        if self.background:
            return ActionResult(ok, "Done on the phone itself (outside Rose's workspace)." if ok else _why(r),
                                changed=ok)
        if isinstance(r.get("observation"), dict):
            self._observed(r)
        return ActionResult(ok, "Done; the screen now shows the result." if ok else _why(r), changed=ok)

    def _search_in_workspace(self, query: str) -> ActionResult:
        """Open Google Maps in the workspace, put ``query`` in its search box and submit it."""

        label = self.phone.app_label(MAPS)
        label = "Maps" if label == MAPS else label
        r = self._start_app(MAPS)
        ws = self._unit()
        if r.get("status") not in ("opened", "launch_unverified"):
            self.refresh()
            return ActionResult(False, _ws_why(r, label))
        obs = r.get("observation") or ws.inspect()
        field = _search_field(obs)
        if field is None:
            box = next((n for n in obs.get("nodes") or [] if not n.get("password") and n.get("clickable")
                        and "search" in " ".join(str(n.get(k) or "") for k in ("text", "description", "viewId")).lower()),
                       None)
            if box is not None:
                ws.command("tap", snapshotId=obs.get("snapshotId"), nodeId=box["id"])
                time.sleep(SETTLE_S)
                obs = ws.inspect()
                field = _search_field(obs)
        if field is None:
            self._observe(obs)
            self._capture()
            return ActionResult(False, f"Opened {label} in Rose's workspace, but found no search box there; "
                                       "search on its screen.", changed=True)
        typed = ws.command("set_text", snapshotId=obs.get("snapshotId"), nodeId=field["id"], text=query)
        if typed.get("status") != "action_accepted":
            self.refresh()
            return ActionResult(False, f"Opened {label} in Rose's workspace, but typing the search failed: "
                                       f"{_why(typed)}", changed=True)
        time.sleep(SETTLE_S)
        obs = ws.inspect()
        field = _search_field(obs, query)
        sent = field is not None and field.get("imeEnter") is not False and ws.command(
            "ime_enter", snapshotId=obs.get("snapshotId"), nodeId=field["id"]).get("status") == "action_accepted"
        res = self._after({"status": "action_accepted"}, f"Searched {label} for {query} in Rose's workspace; "
                                                         "the screen now shows the results." if sent else
                          f"Typed {query} into {label}'s search box in Rose's workspace; pick a suggestion "
                          "or submit the search.")
        return res

    def tap_point(self, x: float, y: float, target: str = "") -> ActionResult:
        """Tap a point of the screenshot: ``x``, ``y`` from 0 to 1000 (left to right, top to bottom).

        For something the screenshot shows but the tree lacks. The point is mapped from the
        screenshot (``width`` x ``height``) to display pixels (``displayWidth`` x
        ``displayHeight``; in the workspace, its virtual display), and sent as ``touch_point``
        with the smallest enabled node of this snapshot that contains it (Rose Agent and the
        workspace controller only tap inside an observed node's bounds, and never on a password
        field). Android only queues the touch, so the result is unverified (``changed`` False):
        the next screenshot shows what it did.
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
        r = self._cmd("touch_point", snapshotId=self.obs.get("snapshotId"), nodeId=node["id"], x=px[0], y=px[1])
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

    def _after(self, r: dict, done: str, settle: bool = True) -> ActionResult:
        ok = r.get("status") in ("action_accepted", "gesture_dispatched")
        if settle:
            time.sleep(SETTLE_S)
        self.refresh()
        return ActionResult(ok, done if ok else (_ws_why(r) if self.background else _why(r)), changed=ok)

    # ----- observation -> elements -------------------------------------
    def _observe(self, r: dict) -> None:
        self.obs = r if isinstance(r, dict) else {}
        status = self.obs.get("status")
        if status != "ok":
            title = {"locked": "Locked: the phone needs the user to unlock it",
                     "accessibility_disabled": "Rose Agent phone controls are off (Settings > Accessibility)",
                     "offline": "Not connected",
                     **(WS_TITLES if self._background else {})}.get(status, f"Unreadable ({status or 'no answer'})")
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
        # Rose's workspace (rose/background.py). ROSE_WORKSPACE=off keeps every task on the
        # visible screen, as on phones without the controller.
        self.use_workspace = os.environ.get("ROSE_WORKSPACE", "").strip().lower() not in ("0", "off", "no", "false")
        # Its stage records go to the bus (dashboard, event log): ids and statuses only, no screen text.
        self.workspace = Workspace(self._controller, in_use=self._workspace_in_use,
                                   record=lambda rec: bus.emit("workspace", **rec) if bus is not None else None,
                                   on_change=lambda: self._changed("workspace", {"app": self.workspace_app()}))
        # On a two-slot controller each task view drives its own session (Workspaces).
        self.workspaces = Workspaces(self.workspace, self._new_unit, self._scoped)
        self._bg: tuple[bool, float] | None = None  # (controller answered, when probed)
        self._bg_lock = threading.Lock()
        self.link = PhoneLink(on_change=self._link_changed)
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
    def session(self, name: str, on_screen: bool = False, independent: bool = False) -> RealSession:
        """A task's view (made on first use; ``on_screen`` is fixed then).

        ``on_screen`` False (the default): the task works in Rose's workspace when the phone has
        one (``RealSession.background``); True: on the user's visible screen. Never blocks: a new
        session only clears the workspace's owner-stop latch (a new task may reopen it)."""

        with self._lock:
            s = self._sessions.get(name)
            if s is None:
                workspace = None
                if independent:
                    from .scoped_workspace import ScopedWorkspace
                    workspace = ScopedWorkspace(self._controller)
                s = self._sessions[name] = RealSession(self, name, on_screen=on_screen, workspace=workspace)
                if independent:
                    s._background = True  # never fall back to the owner's screen
                if not on_screen:
                    (s.scoped or self.workspaces).begin_task()
            return s

    def close_session(self, name: str) -> None:
        with self._lock:
            s = self._sessions.pop(name, None)
        if s is not None and s._background:
            if s.scoped is not None:
                s.scoped.touch()  # the workspace stays open for a follow-up; idle time starts now
            else:
                self.workspaces.release(name)  # it stays open for a follow-up; idle time starts now

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
                "workspace": self.workspace_app(),
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
        """A fresh screenshot of the user's visible screen, or None (locked, secure screen, offline).

        Blocking (it waits on the phone): call it off the MLX thread. ``view`` is accepted for
        the fake Phone's surface; this is always the visible screen (Rose's workspace is
        ``look_workspace``). ``look_status`` says why the
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

    def copy_to_clipboard(self, text: str) -> dict:
        """Put exactly ``text`` on the phone's clipboard (rose.clipboard; blocking: call it off the server's loop)."""
        from .clipboard import copy
        return copy(self.link.call, text)

    def reset(self) -> None:
        for session in list(self._sessions.values()):
            if session.scoped is not None:
                threading.Thread(target=session.scoped.close, args=("reset",), daemon=True).start()
        with self._lock:
            self._sessions.clear()
            self._shown = None
        self._close_workspace_soon("reset")
        self._changed("reset", {})

    def close(self) -> None:
        """Rose is shutting down: close her workspace (blocking; call it off the server's loop)."""

        if self.link.connected:
            for unit in {*self.workspaces.open_units(), *self._scoped()}:
                if unit.session is not None:
                    unit.close("shutdown")

    def _close_workspace_soon(self, reason: str) -> None:
        """Close the workspace on a worker thread: reset runs on threads that must not wait on the phone."""

        if self.link.connected:
            for unit in self.workspaces.open_units():
                threading.Thread(target=unit.close, args=(reason,), name="rose-workspace-close",
                                 daemon=True).start()

    # ----- Rose's workspace (rose/background.py) ----------------------------------------
    def background_available(self) -> bool:
        """The phone has Rose's workspace (its background controller answers ``get_state``).

        Probed once and cached (a "no" is probed again after ``BG_RETRY_S``; everything again
        after a reconnect). Blocking only while unknown: call it off the MLX thread then.
        False while the phone is offline or when ROSE_WORKSPACE=off."""

        return self.background_probe() is True

    def background_probe(self) -> bool | None:
        """``background_available`` with "don't know": True (the controller answered), False
        (definitely absent: Rose Agent says "unavailable", or is too old to know app_command),
        None (no definite answer: offline, a timeout, a transient error). Only a definite answer
        is cached; "don't know" is asked again on the next call."""

        if not self.use_workspace:
            return False
        if not self.link.connected:
            return None
        cached = self._bg
        if cached is not None and (cached[0] or time.monotonic() - cached[1] < BG_RETRY_S):
            return cached[0]
        with self._bg_lock:
            cached = self._bg
            if cached is not None and (cached[0] or time.monotonic() - cached[1] < BG_RETRY_S):
                return cached[0]
            r = self.workspace.get_state()
            status = r.get("status")
            if status in CONTROLLER_STATES:
                answer = True
                # How many sessions it runs side by side (schema 4); older ones refuse the op: one.
                try:
                    self.workspaces.configure(self.workspace._send("capabilities", {}))
                except Exception:
                    self.workspaces.configure(None)
            elif status == "unavailable" or "unsupported phone action" in str(r.get("message") or "").lower():
                answer = False  # no controller (older phones, the Fold) or an old Rose Agent
            else:
                return None  # offline, timeout, an error: unknown, ask again next time
            self._bg = (answer, time.monotonic())
            return answer

    def _scoped(self) -> list[Workspace]:
        """The independent sessions' own workspaces (Codex mode), outside the pool."""

        return [s.scoped for s in list(self._sessions.values()) if s.scoped is not None]

    def _new_unit(self) -> Workspace:
        """Another of Rose's workspaces (a second controller session), set up like the first."""

        first = self.workspace
        return Workspace(self._controller, now=first.now, sleep=first.sleep, record=first.record,
                         on_change=first.on_change, idle_s=first.idle_s, timers=first.timers)

    def _view_unit(self, view: str | None) -> Workspace | None:
        """The workspace ``view``'s task uses, else the one used last (None: all closed). Never waits."""

        task = self._sessions.get(view) if view is not None else None
        if task is not None and task._background:
            unit = self.workspaces.for_view(view)
            if unit.session is not None or not self.workspaces.multi:
                return unit
        units = self.workspaces.open_units()
        return units[0] if units else None

    def workspace_app(self, view: str | None = None) -> str | None:
        """The label of the app open in Rose's workspace (``view``'s, else the one used last), or
        None when it is closed (never waits)."""

        unit = self._view_unit(view)
        s = unit.session if unit is not None else None
        if s is None:
            return None
        return self._label_of(s.get("targetPackage") or "")

    def workspace_apps(self) -> list[str]:
        """Every app open in Rose's workspaces, the one used last first (never waits)."""

        return [self._label_of(u.session.get("targetPackage") or "") for u in self.workspaces.open_units()
                if u.session is not None]

    def _label_of(self, pkg: str) -> str | None:
        return next((str(a.get("label")) for a in self._apps if a.get("packageName") == pkg), pkg or None)

    def look_workspace(self) -> Image.Image | None:
        """A fresh screenshot of Rose's workspace, or None; ``look_status`` says why.

        Blocking: call it off the MLX thread. Only the workspace's app is in it (the controller
        is scoped to that one package). Kept in memory only."""

        if not self.background_available():
            self.look_status = "Rose's workspace is not available on this phone"
            return None
        unit = self._view_unit(None) or self.workspace
        r = unit.screenshot()
        image, info = self._decode(r)
        if image is None:
            status = info.get("status") or "error"
            self.look_status = {"no_background_app": "Rose's workspace is closed",
                                "workspace_closed": "Rose's workspace is closed",
                                "stopped_by_owner": "the owner stopped Rose's workspace",
                                "locked": "the phone is locked"}.get(status, f"Rose's workspace could not be "
                                                                             f"captured ({status})")
            return None
        self.look_status = "ok"
        return image

    def background_status(self) -> dict:
        """Read-only workspace state for /api/realphone (no phone call)."""

        cached = self._bg
        out = {"enabled": self.use_workspace, "available": None if cached is None else cached[0],
               "app": self.workspace_app(), "idleCloseS": self.workspace.idle_s, **self.workspace.state()}
        if self.workspaces.multi:
            out["slots"] = self.workspaces.max
            out["workspaces"] = [{"app": self._label_of((u.session or {}).get("targetPackage") or "")
                                  if u.session else None, **u.state()} for u in self.workspaces.units]
        return out

    def _controller(self, operation: str, params: dict) -> dict:
        """One background-controller operation, as ``app_command`` over the link (the phone passes it on)."""

        return self.link.call("app_command", timeout=WS_CALL_TIMEOUT_S, packageName=CONTROLLER,
                              operation=operation, params=params)

    def _workspace_in_use(self) -> bool:
        with self._lock:
            return any(s._background for s in self._sessions.values())

    def _link_changed(self) -> None:
        """Connected, disconnected or a new hello: probe the controller again, re-adopt its session.

        Runs on the server's event loop, so nothing here waits on the phone."""

        self._bg = None
        for unit in [*self.workspaces.units, *self._scoped()]:
            unit.forget()
        self._changed("link", {"connected": self.link.connected})

    # ----- apps --------------------------------------------------------
    def apps(self) -> list[dict]:
        if not self._apps or time.monotonic() - self._apps_at > 300:
            r = self.link.call("list_apps")
            if r.get("status") == "ok" or r.get("apps"):
                self._apps, self._apps_at = list(r.get("apps") or []), time.monotonic()
        return self._apps

    def app_labels(self) -> list[str]:
        """The labels of the apps already loaded from the phone (never waits: the talker
        matches app names on its own thread). Empty until a task or resolve_app loads them."""

        return [str(a.get("label")) for a in list(self._apps) if a.get("label")]

    def cached_label(self, pkg: str) -> str | None:
        """The label of ``pkg`` from the app list already loaded, or None (never waits: for the
        task runner's guards, which run on the MLX thread)."""

        return next((str(a.get("label")) for a in list(self._apps) if a.get("packageName") == pkg), None)

    def workspace_session_id(self, view: str | None = None) -> str | None:
        """The controller session ``view``'s workspace holds now, or None (never waits)."""

        unit = self._view_unit(view)
        s = unit.session if unit is not None else None
        return s.get("sessionId") if s else None

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


def _ws_why(r: dict, label: str | None = None) -> str:
    """A workspace result the agent reads (and Rose repeats): what happened, plainly."""

    status = r.get("status") or "error"
    app = label or "that app"
    details = r.get("details") if isinstance(r.get("details"), dict) else {}
    if r.get("reason") == "target_in_foreground" or details.get("reason") == "target_in_foreground":
        # A limit of this RoseOS build's platform patch (it refuses any app visible on the main display), not a rule
        # Rose follows: F06's platform change replaces it with a takeover on the owner's request.
        return (f"{app} is open on your phone screen, and this RoseOS build can't take over an app you can see yet; "
                "nothing was opened. If you're done with it there, switch to another app and ask again.")
    if "target_not_standard" in " ".join(str(x.get(k) or "") for x in (r, details) for k in ("reason", "error")):
        # The controller's refusal (reason) or the platform's backstop (error) for the owner's Home or recents task.
        return f"Rose can't take over the Home screen or the recents app ({app}); nothing was opened."
    if "target_in_other_session" in " ".join(str(x.get(k) or "") for x in (r, details) for k in ("reason", "error")):
        # The platform's backstop for an app already in Rose's other workspace session: the controller's own
        # app_in_other_session refusal, reported the same way.
        status = "app_in_other_session"
    if status == "locked":
        return LOCKED_SAY
    if status == "background_target_not_allowed":
        return f"Android doesn't let Rose open {app} in her workspace; nothing was opened."
    if status == "background_consent_required":
        return (f"Android needs the owner's consent on the phone before Rose can use {app} in her workspace; "
                "nothing was opened.")
    if status == "background_blocked":
        return (f"{app} in Rose's workspace is waiting for the owner on the phone (Android blocked it); "
                "the workspace is kept and continues once that is done.")
    if status == "background_unavailable":
        if details.get("status") in ("offline", "timeout", "disconnected"):
            return _why(details)
        return f"Rose's workspace could not open {app} ({details.get('status') or 'unavailable'}); nothing was opened."
    if status == "app_in_other_session":
        return (f"{app} is open in Rose's workspace for another of her tasks right now; nothing was opened. "
                "Let that task finish first.")
    if status in ("session_limit", "multi_session_unavailable"):
        return (f"Rose's workspace is busy with her other tasks' apps; {app} was not opened. "
                "Let one of them finish first.")
    if status == "stopped_by_owner":
        return "The owner stopped Rose's workspace; it stays closed for this task."
    if status == "no_background_app":
        return "Rose's workspace has no app open; use open_app first."
    if status == "workspace_closed":
        return r.get("reason") or "Rose's workspace closed; use open_app to open the app there again."
    if status == "not_executed":
        return f"Not done: {r.get('reason') or 'the workspace refused it'}"
    if status == "stale_screen":
        return f"The screen changed before this action; check it again. {r.get('reason') or ''}".strip()
    if status == "error" and r.get("error"):
        # Controller exceptions ("java.lang.IllegalStateException: Background click rejected").
        return f"Failed: {str(r['error']).rsplit(': ', 1)[-1]}"
    return _why(r)


def _search_field(obs: dict, text: str | None = None) -> dict | None:
    """The search box of a Maps observation: an editable, non-password node (holding ``text``)."""

    fields = [n for n in obs.get("nodes") or [] if n.get("editable") and not n.get("password")
              and n.get("visible") is not False] if obs.get("status") == "ok" else []
    if text is not None:
        fields = [n for n in fields if _clean(n.get("text") or "") == _clean(text)] or \
                 [n for n in fields if n.get("focused")] or fields
    return fields[0] if fields else None


def _names_of(nodes: dict, ids: set) -> list[str]:
    """Text, description and view-id hint (as "id:<words>") of the nodes ``ids`` (no blanks, no
    repeats)."""

    out: list[str] = []
    for i in ids:
        n = nodes.get(i) or {}
        if n.get("password"):
            continue
        hint = _hint(n.get("viewId"))
        for v in (n.get("text"), n.get("description"), "id:" + hint if hint else ""):
            v = _clean(v or "")
            if v and v not in out:
                out.append(v)
    return out


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
