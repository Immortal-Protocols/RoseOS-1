"""Rose's workspace without a phone: a fake background controller with Controller.java's rules.

    .venv/bin/python -m rose.tests.test_background

``FakeController`` enforces what the real controller does (os/background-control/agent/src/dev/
pixel/backgroundcontrol/Controller.java, schema 3 as installed on RoseOS): one target package per
session; every action except screenshot/inspect_ui/app_context/home needs the latest snapshot id,
less than 30 s old; screenshot and every accepted action clear the node map; 200 operations per
session; a 600 s deadline; taskId tagging (``stale_task``, ``user_stopped``); the OS allowlist
(``target_not_allowed``); the owner's Stop; Rose Agent's lock gate in front of start/command.
Errors come back the way AgentProvider shapes them (``stale_screen`` for snapshot problems,
``error`` otherwise).

The checks port every case of workspace/rose-agent/server/background-phone.test.mjs that applies
to one caller, then run ``RealSession`` in background mode end to end: open_app, tree, tap, type,
a follow-up task reusing the workspace, the idle close and the owner's Stop. No model, no phone.
"""

from __future__ import annotations

import base64
import io
import threading
import time
import uuid

from PIL import Image

import rose.realphone as realphone
from rose.background import Workspace, node_key
from rose.phone import Phone
from rose.realphone import RealPhone

CONTROLLER = "dev.pixel.backgroundcontrol"
CALC, NOTES, MAPS, AMAZON = ("com.google.android.calculator", "com.google.android.keep",
                             "com.google.android.apps.maps", "com.amazon.mShop.android.shopping")
APPS = [{"label": "Calculator", "packageName": CALC}, {"label": "Keep Notes", "packageName": NOTES},
        {"label": "Maps", "packageName": MAPS}, {"label": "Amazon Shopping", "packageName": AMAZON},
        {"label": "Messages", "packageName": "com.google.android.apps.messaging"}]
DISPLAY = (1080, 2400)
SHOT = (540, 1200)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def _jpeg() -> str:
    buf = io.BytesIO()
    Image.new("RGB", SHOT, "#223344").save(buf, format="JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode()


class FakeController:
    """The background controller, in memory (see the module docstring)."""

    def __init__(self, clock: Clock, allowed: set[str] | None = None, start_polls: int = 2):
        self.clock = clock
        self.allowed = allowed  # None: anyAppControl (every installed app)
        self.start_polls = start_polls  # get_state calls that still say "starting"
        self.calls: list[tuple[str, dict]] = []
        self.locked = False  # Rose Agent's gate: owner away, locked control off
        self.status, self.id, self.target, self.task = "idle", None, None, None
        self.stop_reason = self.stopped_session = self.stopped_task = None
        self.snapshot, self.snap_time, self.nodes = None, 0.0, {}
        self.ops, self.started, self.pending = 0, 0.0, 0
        self.blocked = False
        self.owner_stopped_tasks: set[str] = set()
        self.apps: dict[str, dict] = {}  # per-package app state

    # ----- the provider ------------------------------------------------------------
    def __call__(self, operation: str, params: dict) -> dict:
        self.calls.append((operation, dict(params)))
        if self.locked and operation in ("start", "command"):
            return {"status": "locked", "reason": "owner_unlock_required", "lockedControl": "not_enabled",
                    "message": "App control while locked is off.", "actionExecuted": False}
        try:
            return getattr(self, "op_" + operation)(params)
        except (ValueError, LookupError, PermissionError, RuntimeError) as e:  # AgentProvider's shaping
            msg = f"java.lang.{type(e).__name__}: {e}"
            stale = any(k in msg for k in ("snapshot", "Snapshot", "stale", "changed; inspect"))
            return {"status": "stale_screen" if stale else "error", "error": msg, "foregroundFallback": False}

    def state(self) -> dict:
        return {"status": self.status, "sessionId": self.id, "targetPackage": self.target, "taskId": self.task,
                "error": "", "reason": "", "blocked": self.blocked, "width": DISPLAY[0], "height": DISPLAY[1],
                "operationCount": self.ops,
                "elapsedMs": round((self.clock.now() - self.started) * 1000) if self.started else 0,
                "stopReason": self.stop_reason, "stoppedSessionId": self.stopped_session,
                "stoppedTaskId": self.stopped_task, "anyAppControl": self.allowed is None, "foregroundFallback": False}

    def op_capabilities(self, p: dict) -> dict:
        return {"status": "ok", "schemaVersion": 3, "backgroundOnly": True,
                "operations": ["list_apps", "start", "get_state", "command", "stop"]}

    def op_get_state(self, p: dict) -> dict:
        if self.status == "starting":
            if self.pending > 0:
                self.pending -= 1
            else:
                self.status = "ready"
        return self.state()

    def op_start(self, p: dict) -> dict:
        pkg = p["packageName"]
        if pkg not in {a["packageName"] for a in APPS}:
            raise ValueError("Target must be an installed launchable app")
        if p.get("taskId") in self.owner_stopped_tasks:
            return {**self.state(), "status": "user_stopped", "rejectedTaskId": p.get("taskId")}
        if self.allowed is not None and pkg not in self.allowed:
            return {**self.state(), "status": "target_not_allowed", "targetPackage": pkg}
        if self.id and pkg == self.target and self.status == "ready":
            return self.state()
        if self.id and self.status in ("ready", "starting", "blocked"):
            # As on the Pixel 10 (2026-11-01): a second live session is refused, not replaced.
            self._stop("agent")
            self.status = "failed"
            return self.state()
        self._stop("agent")
        self.id, self.target, self.task = str(uuid.uuid4()), pkg, p.get("taskId")
        self.status, self.ops, self.started, self.pending = "starting", 0, self.clock.now(), self.start_polls
        self.stop_reason = self.stopped_session = self.stopped_task = None
        self.apps.setdefault(pkg, {"display": "", "note": "", "query": "", "searching": False})
        return self.state()

    def op_stop(self, p: dict) -> dict:
        self._stop("agent")
        return self.state()

    def _stop(self, reason: str) -> None:
        if self.id:
            self.stopped_session, self.stopped_task, self.stop_reason = self.id, self.task, reason
        self.id, self.blocked, self.status = None, False, "stopped"
        self._clear()

    def owner_stop(self) -> None:
        """The owner pressed Stop on the notification."""

        self.owner_stopped_tasks.add(self.task)
        self._stop("owner")

    def deadline_stop(self) -> None:
        """The controller's own 600 s deadline (StopLedger.DEADLINE)."""

        self._stop("deadline")

    def _clear(self) -> None:
        self.nodes, self.snapshot = {}, None

    def op_command(self, p: dict) -> dict:
        if not self.id or self.status != "ready" or self.blocked:
            raise RuntimeError(f"Background session is not ready: {self.status}")
        if p.get("sessionId") != self.id:
            raise ValueError("Stale session ID")
        if p.get("taskId") != self.task:
            return {"status": "stale_task", "rejectedTaskId": p.get("taskId"), "taskId": self.task}
        if self.clock.now() - self.started > 600:
            raise RuntimeError("Session expired")
        action = p["action"]
        if action == "screenshot":
            self._clear()
            self.snapshot, self.snap_time = str(uuid.uuid4()), self.clock.now()
            return {**self._base(), "width": SHOT[0], "height": SHOT[1], "screenshotId": self.snapshot,
                    "mimeType": "image/jpeg", "imageBase64": _jpeg()}
        if action == "inspect_ui":
            self._clear()
            self.snapshot, self.snap_time = str(uuid.uuid4()), self.clock.now()
            rows = self._screen()
            self.nodes = {r["id"]: r for r in rows}
            return {**self._base(), "nodes": [dict(r) for r in rows], "truncated": False}
        if action == "app_context":
            return self._base()
        if action == "home":
            self._stop("agent")
            return self.state()
        # fresh(): the latest observation's id, under 30 s old.
        if self.snapshot is None or self.clock.now() - self.snap_time > 30 or \
                self.snapshot != p.get("snapshotId", p.get("screenshotId")):
            raise ValueError("Fresh background snapshot required")
        if self.ops >= 200:
            raise RuntimeError("Session operation limit reached")
        app = self.apps[self.target]
        if action in ("tap", "touch_point", "long_press"):
            n = self._node(p)
            if n.get("password"):
                raise PermissionError("Password field requires user")
            if action == "touch_point":
                b = n["bounds"]
                if not (b[0] <= p["x"] < b[2] and b[1] <= p["y"] < b[3]):
                    raise ValueError("Point outside observed node")
            self._tapped(app, n)
        elif action == "set_text":
            n = self._node(p)
            if not n.get("editable"):
                raise ValueError("Node is not an editable visible control")
            if n.get("password"):
                raise PermissionError("Password fields require user interaction")
            app["note" if self.target == NOTES else "query"] = p["text"]
        elif action == "ime_enter":
            n = self._node(p)
            if not n.get("editable"):
                raise RuntimeError("Background editor submit rejected")
            app["searching"] = True
        elif action == "scroll":
            if not self._node(p).get("scrollable"):
                raise RuntimeError("Background scroll rejected")
        elif action == "back":
            app["display"] = app["display"][:-1]
        else:
            raise ValueError("Unsupported background action")
        self.ops += 1
        self._clear()
        return {**self.state(), "status": "action_accepted", "action": action}

    def _node(self, p: dict) -> dict:
        n = self.nodes.get(p.get("nodeId"))
        if n is None:
            raise ValueError("Unknown target node; inspect_ui first")
        return n

    def _tapped(self, app: dict, n: dict) -> None:
        if self.target == CALC:
            key = n.get("text") or ""
            if key == "=":
                try:
                    app["display"] = str(eval(app["display"], {"__builtins__": {}}))  # digits and + only
                except SyntaxError:
                    pass
            elif key == "AC":
                app["display"] = ""
            else:
                app["display"] += key
        elif self.target == MAPS and n.get("description") == "Search here":
            app["searching"] = False

    def _base(self) -> dict:
        return {"status": "ok", "sessionId": self.id, "targetPackage": self.target, "snapshotId": self.snapshot,
                "width": DISPLAY[0], "height": DISPLAY[1], "scope": "background_target", "packageName": self.target,
                "validForMs": 30000, "displayWidth": DISPLAY[0], "displayHeight": DISPLAY[1]}

    def _screen(self) -> list[dict]:
        """The target app's visible nodes (fresh ids per observation, as the controller numbers them)."""

        app = self.apps[self.target]
        rows: list[dict] = []

        def add(cls, text="", desc="", bounds=(0, 0, 10, 10), **flags):
            rows.append({"id": len(rows), "nodeId": len(rows), "parentId": 0 if rows else -1,
                         "class": cls, "className": cls, "viewId": "", "text": text, "description": desc,
                         "bounds": list(bounds), "visible": True, "enabled": True, "clickable": False,
                         "editable": False, "scrollable": False, "password": False, "checked": False,
                         "selected": False, "focused": False, **flags})

        add("android.widget.FrameLayout", bounds=(0, 0, *DISPLAY))
        if self.target == CALC:
            add("android.widget.TextView", text=app["display"] or "0", bounds=(0, 300, 1080, 500))
            for i, key in enumerate(["1", "2", "3", "+", "=", "AC"]):
                add("android.widget.Button", text=key, bounds=(i * 180, 1800, i * 180 + 180, 1980), clickable=True)
        elif self.target == NOTES:
            add("android.widget.EditText", text=app["note"], desc="Note", bounds=(0, 300, 1080, 900),
                editable=True, focused=True, imeEnter=False)
            add("android.widget.EditText", text="", desc="PIN", bounds=(0, 1000, 1080, 1100), editable=True,
                password=True)
        elif self.target == MAPS:
            if app["searching"]:
                add("android.widget.TextView", text=f"Results for {app['query']}", bounds=(0, 300, 1080, 400))
            else:
                add("android.widget.EditText", text=app["query"], desc="Search here", bounds=(0, 100, 1080, 200),
                    editable=True, clickable=True, focused=bool(app["query"]), imeEnter=True)
        else:
            add("android.widget.TextView", text="Welcome", bounds=(0, 300, 1080, 400))
        return rows


def make(clock: Clock | None = None, **kw) -> tuple[Workspace, FakeController, Clock]:
    clock = clock or Clock()
    fc = FakeController(clock, **kw)
    events: list = []
    ws = Workspace(fc, now=clock.now, sleep=clock.sleep, record=events.append, timers=False)
    ws.events = events  # type: ignore[attr-defined]
    return ws, fc, clock


def scripted(responses: dict) -> tuple[Workspace, list]:
    """A workspace whose controller answers from a table (for BackgroundPhone's scripted cases)."""

    calls: list = []

    def send(op, params):
        calls.append((op, params))
        handler = responses.get(op)
        if handler is None:
            raise AssertionError(f"unexpected operation {op}")
        return handler(op, params) if callable(handler) else dict(handler)
    clock = Clock()
    return Workspace(send, now=clock.now, sleep=clock.sleep, timers=False), calls


# ------------------------------------------------------------- BackgroundPhone's cases
def ported_checks(bad: list) -> None:
    # open and subsequent UI operations use the background provider (never the physical UI)
    ws, fc, _ = make()
    opened = ws.start(MAPS)
    if opened["status"] != "opened" or opened["backgroundVerified"] is not True or opened["foregroundVerified"]:
        bad.append(f"open: {opened['status']}")
    if [c[0] for c in fc.calls[:2]] != ["get_state", "start"] or fc.calls[1][1].get("taskId") != ws.task_id:
        bad.append(f"start was not tagged with the workspace id: {fc.calls[:2]}")
    field = next(n for n in opened["observation"]["nodes"] if n.get("editable"))
    r = ws.command("set_text", snapshotId=opened["observation"]["snapshotId"], nodeId=field["id"], text="coffee")
    if r["status"] != "action_accepted" or fc.calls[-1][1].get("sessionId") != ws.session["sessionId"]:
        bad.append(f"set_text in the workspace: {r}")

    # bare back observes the background app before scoped navigation
    fc.calls.clear()
    r = ws.command("back")
    if [c[1].get("action") for c in fc.calls] != ["inspect_ui", "back"] or r["status"] != "action_accepted":
        bad.append(f"bare back sent {[c[1].get('action') for c in fc.calls]} ({r['status']})")

    # bare back never navigates when the background observation fails
    w2, calls = scripted({"get_state": {"status": "ready", "targetPackage": "maps", "sessionId": "session-a"},
                          "command": {"status": "unavailable"}})
    r = w2.command("back")
    if r["actionExecuted"] is not False or any(c[1].get("action") == "back" for c in calls):
        bad.append("back navigated without an observation")

    # missing session and unsupported commands never fall back to the visible screen
    ws, fc, _ = make()
    if ws.command("screenshot")["status"] != "no_background_app":
        bad.append("a screenshot without a workspace")
    if ws.command("apple_music_run", title="a")["status"] != "not_executed":
        bad.append("an unverified operation was not refused")

    # cleanup closes orphaned sessions (after a restart)
    ws.close()
    if fc.calls[-1][0] != "stop":
        bad.append("close did not stop the controller")

    # reopening the same installer-blocked app preserves its session until approval completes
    blocked = [True]
    w3, calls = scripted({
        "get_state": lambda op, p: {"status": "blocked" if blocked[0] else "ready", "blocked": blocked[0],
                                    "targetPackage": "com.android.vending", "sessionId": "retained"},
        "command": {"status": "ok", "packageName": "com.android.vending", "snapshotId": "after", "nodes": []}})
    for _ in range(2):
        r = w3.start("com.android.vending")
        if r["status"] != "background_blocked" or not r["sessionRetained"] or r["sessionId"] != "retained":
            bad.append(f"blocked reopen: {r}")
    if [c[0] for c in calls] != ["get_state", "get_state"] or w3.session["sessionId"] != "retained":
        bad.append(f"blocked reopen replaced the session: {calls}")
    blocked[0] = False
    r = w3.start("com.android.vending")
    if r["status"] != "opened" or any(c[0] in ("start", "stop") for c in calls):
        bad.append(f"resumed after approval: {r['status']}")

    # an installer window in the same workspace accepts its freshly observed control
    node = {"id": 1, "nodeId": 1, "packageName": "com.android.packageinstaller", "className": "Button",
            "text": "Install", "bounds": [10, 10, 80, 40], "clickable": True}
    w4, calls = scripted({
        "get_state": {"status": "ready", "targetPackage": "com.android.vending", "sessionId": "workspace"},
        "command": lambda op, p: {"status": "ok", "packageName": "com.android.packageinstaller",
                                  "sessionId": "workspace", "snapshotId": "installer", "nodes": [node]}
        if p["action"] == "inspect_ui" else {"status": "action_accepted", "sessionId": "workspace"}})
    w4.command("inspect_ui")
    r = w4.command("tap", snapshotId="installer", nodeId=1)
    if r["status"] != "action_accepted" or calls[-1][1]["nodeId"] != 1 or calls[-1][1]["sessionId"] != "workspace":
        bad.append(f"installer tap: {r}")

    # consent requirement is explicit and no app navigation follows it
    w5, calls = scripted({"get_state": {"status": "stopped"}, "start": {"status": "consent_required"}})
    r = w5.start("maps")
    if r["status"] != "background_consent_required" or r["actionExecuted"] or any(c[0] == "command" for c in calls):
        bad.append(f"consent: {r}")

    # home closes only the background session
    ws, fc, _ = make()
    ws.start(MAPS)
    r = ws.command("home")
    if not r.get("workspaceClosed") or fc.calls[-1][0] != "stop" or ws.session is not None:
        bad.append(f"home: {r}")

    locked_checks(bad)


def locked_checks(bad: list) -> None:
    # a locked start refused by Rose Agent reports locked, without retrying
    refusal = {"status": "locked", "reason": "owner_unlock_required", "lockedControl": "authentication_expired",
               "message": "Unlock the phone with your PIN or strong biometric to renew app control while locked.",
               "actionExecuted": False}
    w, calls = scripted({"get_state": {"status": "stopped"}, "start": refusal})
    r = w.start("com.example.notes")
    if (r["status"], r["lockedControl"], r["reason"], r["packageName"]) != \
            ("locked", "authentication_expired", refusal["message"], "com.example.notes") or w.session is not None:
        bad.append(f"locked start: {r}")
    if [c[0] for c in calls] != ["get_state", "start"]:
        bad.append(f"locked start retried: {calls}")

    # Android refusing a locked session is an OS refusal
    w, _ = scripted({"get_state": {"status": "stopped"},
                     "start": {"status": "rejected", "reason": "device_locked", "error": "Android session error 2"}})
    r = w.start("com.example.notes")
    if r["status"] != "locked" or r["lockedControl"] != "os_refused" or r["details"]["error"] != "Android session error 2":
        bad.append(f"device_locked: {r}")

    # a session blocked at an owner gate while locked is retained and explained
    w, calls = scripted({"get_state": {"status": "blocked", "blocked": True, "reason": "owner_unlock_required",
                                       "targetPackage": "com.android.vending", "sessionId": "kept"}})
    r = w.start("com.android.vending")
    if r["status"] != "background_blocked" or not r.get("ownerUnlockRequired") or [c[0] for c in calls] != ["get_state"]:
        bad.append(f"owner gate while locked: {r}")

    # locked results during a session keep the workspace (FakeController's gate)
    ws, fc, _ = make()
    opened = ws.start(CALC)
    one = next(n for n in opened["observation"]["nodes"] if n["text"] == "1")
    fc.locked = True
    tapped = ws.command("tap", snapshotId=opened["observation"]["snapshotId"], nodeId=one["id"])
    shot = ws.command("screenshot")
    back = ws.command("back")
    if (tapped["status"], shot["status"], back["status"]) != ("locked", "locked", "locked") or \
            tapped.get("sessionId") != ws.session["sessionId"] or tapped["lockedControl"] != "not_enabled":
        bad.append(f"locked during a session: {tapped['status']} {shot['status']} {back['status']}")
    if ws.session is None:
        bad.append("a locked result dropped the workspace")
    fc.locked = False

    # a blocked command is locked only when the controller says the owner is needed
    gate = [True]
    w, _ = scripted({
        "get_state": lambda op, p: {"status": "blocked", "blocked": True, "sessionId": "s1", "targetPackage": "notes",
                                    "reason": "owner_unlock_required" if gate[0] else ""},
        "command": {"status": "error", "error": "java.lang.IllegalStateException: Background session is not ready: blocked"}})
    w.session = {"sessionId": "s1", "targetPackage": "notes"}
    if (r := w.command("screenshot"))["status"] != "locked" or r["lockedControl"] != "owner_gate":
        bad.append(f"blocked owner gate: {r}")
    gate[0] = False
    if w.command("screenshot")["status"] != "error":
        bad.append("a blocked (not owner-gated) command was reported as locked")

    # reusing a ready session while locked reports locked instead of an unverified launch
    w, calls = scripted({"get_state": {"status": "ready", "targetPackage": "notes", "sessionId": "s1"},
                         "command": {"status": "locked", "reason": "owner_unlock_required",
                                     "lockedControl": "authentication_expired", "message": "Unlock the phone."}})
    r = w.start("notes")
    if r["status"] != "locked" or r.get("sessionId") != "s1" or [c[0] for c in calls] != ["get_state", "command"]:
        bad.append(f"locked reuse: {r} {calls}")


# --------------------------------------------------------- Controller.java's rules
def controller_checks(bad: list) -> None:
    ws, fc, clock = make()
    opened = ws.start(CALC)
    obs = opened["observation"]
    if [c[0] for c in fc.calls].count("get_state") != 1 + fc.start_polls + 1:
        bad.append(f"start did not poll get_state while starting: {[c[0] for c in fc.calls]}")

    # screenshot clears the node map and issues a new snapshot: the observed node is re-found
    shot = ws.screenshot()
    if shot.get("screenshotId") != fc.snapshot or fc.nodes:
        bad.append("the fake screenshot did not clear nodes")
    one = next(n for n in obs["nodes"] if n["text"] == "1")
    r = ws.command("tap", snapshotId=obs["snapshotId"], nodeId=one["id"])
    if r["status"] != "action_accepted" or fc.apps[CALC]["display"] != "1":
        bad.append(f"a tap after a screenshot was not refreshed onto the latest snapshot: {r}")

    # observe(): screenshot first, then inspect, whose snapshot is the latest
    fc.calls.clear()
    shot, tree = ws.observe()
    if [c[1].get("action") for c in fc.calls] != ["screenshot", "inspect_ui"] or tree["snapshotId"] != fc.snapshot:
        bad.append(f"observe order {[c[1].get('action') for c in fc.calls]}")

    # snapshots older than 30 s: the refresh makes a new one before acting
    clock.t += 45
    plus = next(n for n in tree["nodes"] if n["text"] == "+")
    if ws.command("tap", snapshotId=tree["snapshotId"], nodeId=plus["id"])["status"] != "action_accepted":
        bad.append("an old snapshot was not refreshed")
    # sent directly with an old snapshot, the controller refuses it (AgentProvider says stale_screen)
    raw = fc("command", {"sessionId": fc.id, "taskId": fc.task, "action": "tap", "snapshotId": "old", "nodeId": 1})
    if raw["status"] != "stale_screen":
        bad.append(f"the fake controller accepted a stale snapshot: {raw}")

    # a node gone from the screen, or ambiguous, is refused without acting
    tree = ws.inspect()
    ops = fc.ops
    fake_prior = dict(tree, snapshotId="gone", nodes=[{**tree["nodes"][1], "text": "999"}])
    ws._remember(fake_prior)
    r = ws.command("tap", snapshotId="gone", nodeId=tree["nodes"][1]["id"])
    if r["status"] != "stale_screen" or fc.ops != ops:
        bad.append(f"a vanished node was tapped: {r}")
    if ws.command("tap", snapshotId="never-seen", nodeId=3)["status"] != "stale_screen":
        bad.append("an unknown snapshot's node was tapped")
    if ws.command("tap", snapshotId=tree["snapshotId"])["status"] != "stale_screen":
        bad.append("a node action without a node ran")
    twin = {"a": 1}
    if node_key({**twin, "bounds": [1, 2, 3, 4]}) != node_key({**twin, "bounds": (1, 2, 3, 4)}):
        bad.append("node_key depends on the bounds' type")

    # target scoping: another app is a new session; the old session id no longer works
    old_session, old_task = ws.session["sessionId"], ws.task_id
    r = ws.start(NOTES)
    if r["status"] != "opened" or ws.session["sessionId"] == old_session or ws.task_id == old_task:
        bad.append(f"another app did not get a new workspace generation: {r['status']}")
    raw = fc("command", {"sessionId": old_session, "taskId": old_task, "action": "inspect_ui"})
    if raw["status"] != "error":
        bad.append("the old session still answered")

    # stale_task: the workspace belongs to another generation; nothing ran
    ws.task_id = "rose-ws-0-someone-else"
    r = ws.command("inspect_ui")
    if r["status"] != "not_executed" or "another task" not in r["reason"]:
        bad.append(f"stale_task: {r}")
    ws.task_id = fc.task

    # the operation limit closes the workspace (no app reported open) and says open_app again
    fc.ops = 200
    tree = ws.inspect()
    note = next(n for n in tree["nodes"] if n["editable"] and not n["password"])
    r = ws.command("set_text", snapshotId=tree["snapshotId"], nodeId=note["id"], text="x")
    if r["status"] != "workspace_closed" or r["closedReason"] != "limit" or ws.session is not None \
            or "open_app" not in r["reason"]:
        bad.append(f"op limit: {r}")
    fc.ops = 0
    tree = ws.start(NOTES)["observation"]  # open_app again: a fresh session

    # password fields: the controller refuses, nothing is typed
    pin = next(n for n in tree["nodes"] if n["password"])
    r = ws.command("set_text", snapshotId=tree["snapshotId"], nodeId=pin["id"], text="1234")
    if r["status"] == "action_accepted":
        bad.append("typed into a password field")

    # target_not_allowed: nothing opened, plainly reported
    ws, fc, _ = make(allowed={CALC})
    r = ws.start(AMAZON)
    if r["status"] != "background_target_not_allowed" or r["anyAppControl"] or ws.session is not None:
        bad.append(f"target_not_allowed: {r}")
    if any(c[0] == "command" for c in fc.calls):
        bad.append("an action followed target_not_allowed")

    # a start that never becomes ready gives up after 20 s (past the controller's 15 s frame wait) and stops
    # exactly that session, so it cannot land later and hold the app unseen
    ws, fc, clock = make(start_polls=10_000)
    t0 = clock.t
    r = ws.start(CALC)
    if r["status"] != "background_unavailable" or not 20 <= clock.t - t0 < 20.5:
        bad.append(f"start timeout: {r['status']} after {clock.t - t0:.2f} s")
    stops = [c[1] for c in fc.calls if c[0] == "stop"]
    if not stops or stops[-1].get("sessionId") != fc.stopped_session or fc.status != "stopped":
        bad.append(f"an abandoned start was not stopped by its own session id: {stops} status={fc.status}")
    if not r.get("details", {}).get("startAbandoned") or r["details"].get("stopStatus") != "stopped":
        bad.append(f"abandoned start not reported: {r.get('details')}")
    from rose.realphone import _ws_why
    why = _ws_why(r, "Calculator")
    if "could not open Calculator in time, so Rose stopped that start" not in why or "nothing was opened" in why:
        bad.append(f"abandoned start wording: {why}")
    # a slow start that becomes ready after 14 s (the controller still within its 15 s wait) now opens
    ws, fc, clock = make(start_polls=round(14 / 0.15))
    t0 = clock.t
    r = ws.start(CALC)
    if r["status"] != "opened" or not 13.5 <= clock.t - t0 < 15 or any(c[0] == "stop" for c in fc.calls[1:]):
        bad.append(f"slow start: {r['status']} after {clock.t - t0:.2f} s")

    owner_stop_checks(bad)
    idle_checks(bad)
    lifetime_checks(bad)


def owner_stop_checks(bad: list) -> None:
    ws, fc, _ = make()
    opened = ws.start(CALC)
    task = ws.task_id
    fc.owner_stop()
    one = next(n for n in opened["observation"]["nodes"] if n["text"] == "1")
    r = ws.command("tap", snapshotId=opened["observation"]["snapshotId"], nodeId=one["id"])
    if r["status"] != "stopped_by_owner" or ws.session is not None or not ws.stopped_by_owner:
        bad.append(f"owner stop during an action: {r}")
    # latched: nothing reopens the workspace for this task, not even open_app
    n = len(fc.calls)
    if ws.start(CALC)["status"] != "stopped_by_owner" or ws.command("screenshot")["status"] != "stopped_by_owner":
        bad.append("the owner stop was not latched")
    if len(fc.calls) != n:
        bad.append("the latched workspace still called the controller")
    # a start for the stopped task id is refused by a schema 3 controller (user_stopped)
    ws.begin_task()
    ws._generation -= 1  # make the next generation reuse the stopped task's id
    r = ws.start(CALC)
    if r["status"] != "stopped_by_owner" or fc.calls[-1][1].get("taskId") != task:
        bad.append(f"user_stopped for the stopped task: {r}")
    # a new task opens it again with a new generation
    ws.begin_task()
    r = ws.start(CALC)
    if r["status"] != "opened" or ws.task_id == task:
        bad.append(f"a new task could not reopen after an owner stop: {r['status']}")
    # user_stopped naming an older task: the current one is unchanged
    w, calls = scripted({"get_state": {"status": "stopped"},
                         "start": {"status": "user_stopped", "rejectedTaskId": "rose-ws-0-old"}})
    r = w.start(CALC)
    if r["status"] != "not_executed" or w.stopped_by_owner:
        bad.append(f"user_stopped for an older task: {r}")
    # the owner stop is noticed before home closes anything (schema 2: no stopReason)
    ws, fc, _ = make()
    ws.start(CALC)
    fc.owner_stop()
    fc.stop_reason = None
    if ws.command("home")["status"] != "stopped_by_owner":
        bad.append("home did not notice the owner stop")
    # an agent stop is never mistaken for the owner's
    ws, fc, _ = make()
    ws.start(CALC)
    ws.close()
    ws.begin_task()
    if ws.start(CALC)["status"] != "opened":
        bad.append("our own stop looked like the owner's")


def lifetime_checks(bad: list) -> None:
    """A workspace kept across tasks: stops between tasks, restarts, limits, reconnects, closes."""

    # an owner Stop pressed after a task finished is not charged to the next task
    for first in ("inspect", "start"):
        ws, fc, _ = make()
        ws.start(CALC)
        fc.owner_stop()
        ws.begin_task()
        r = ws.inspect() if first == "inspect" else ws.start(CALC)
        if r["status"] == "stopped_by_owner" or ws.stopped_by_owner:
            bad.append(f"an earlier task's owner stop ended the new task ({first}): {r['status']}")
        if ws.start(CALC)["status"] != "opened":
            bad.append(f"the new task could not open the workspace after an earlier stop ({first})")
    # ...but a Stop during the task, after its first call, still ends it
    ws, fc, _ = make()
    ws.start(CALC)
    ws.begin_task()
    ws.inspect()
    fc.owner_stop()
    if ws.inspect()["status"] != "stopped_by_owner" or ws.start(CALC)["status"] != "stopped_by_owner":
        bad.append("an owner stop during the task was not latched")

    # workspace ids differ per process: a restarted Rose never reuses an owner-stopped id
    clock = Clock()
    fc = FakeController(clock)
    a = Workspace(fc, now=clock.now, sleep=clock.sleep, timers=False)
    a.start(CALC)
    fc.owner_stop()
    b = Workspace(fc, now=clock.now, sleep=clock.sleep, timers=False)  # a new server process
    r = b.start(CALC)
    if r["status"] != "opened" or b.task_id == a.task_id or not b.task_id.startswith("rose-ws-"):
        bad.append(f"a restarted workspace reused the stopped id: {b.task_id} ({r['status']})")

    # a reused session near its operation limit is renewed at open_app
    ws, fc, _ = make()
    ws.start(CALC)
    old = ws.session["sessionId"]
    fc.ops = 160
    r = ws.start(CALC)
    if r["status"] != "opened" or ws.session["sessionId"] == old or fc.ops != 0:
        bad.append(f"a worn session was not renewed: {r['status']}")
    # ...and near its deadline
    ws, fc, clock = make()
    ws.start(CALC)
    old = ws.session["sessionId"]
    clock.t += 500
    if ws.start(CALC)["status"] != "opened" or ws.session["sessionId"] == old:
        bad.append("a session near its deadline was not renewed")

    # the controller's deadline closes the workspace; it is not the owner's Stop
    ws, fc, _ = make()
    ws.start(CALC)
    fc.deadline_stop()
    r = ws.inspect()
    if r["status"] != "workspace_closed" or ws.stopped_by_owner or ws.session is not None:
        bad.append(f"a deadline stop read as an owner stop: {r['status']}")
    if ws.start(CALC)["status"] != "opened":
        bad.append("the workspace did not reopen after its deadline")
    # between tasks too
    ws, fc, _ = make()
    ws.start(CALC)
    fc.deadline_stop()
    ws.begin_task()
    if ws.start(CALC)["status"] != "opened":
        bad.append("a deadline between tasks refused the next task")

    # an owner Stop around a link reconnect is still the owner's: the task never reopens it
    ws, fc, _ = make()
    ws.start(CALC)
    ws.forget()
    fc.owner_stop()
    r = ws.inspect()
    if r["status"] != "stopped_by_owner" or ws.start(CALC)["status"] != "stopped_by_owner":
        bad.append(f"an owner stop across a reconnect was lost: {r['status']}")
    # ...while a live session is adopted again after a reconnect
    ws, fc, _ = make()
    ws.start(CALC)
    live = ws.session["sessionId"]
    ws.forget()
    if ws.inspect()["status"] != "ok" or ws.session["sessionId"] != live:
        bad.append("the live session was not re-adopted after a reconnect")

    # Android closed the session: the workspace no longer reports the app open
    ws, fc, _ = make()
    ws.start(CALC)
    fc.id, fc.status = None, "closed"
    r = ws.inspect()
    if r["status"] != "workspace_closed" or ws.session is not None or ws.closed_reason != "closed":
        bad.append(f"a closed session stayed open: {r['status']} {ws.session}")
    tree = None
    ws, fc, _ = make()
    tree = ws.start(CALC)["observation"]
    fc.id, fc.status = None, "failed"
    one = next(n for n in tree["nodes"] if n["text"] == "1")
    r = ws.command("tap", snapshotId=tree["snapshotId"], nodeId=one["id"])
    if r["status"] != "workspace_closed" or ws.session is not None:
        bad.append(f"a failed session stayed open after a tap: {r['status']}")


def idle_checks(bad: list) -> None:
    ws, fc, clock = make()
    busy = [True]
    ws.in_use = lambda: busy[0]
    ws.start(CALC)
    clock.t += 500
    if ws.close_if_idle() or ws.session is None:
        bad.append("the idle close closed a workspace a task holds")
    busy[0] = False
    ws.touch()
    clock.t += 179
    if ws.close_if_idle():
        bad.append("closed before WORKSPACE_IDLE_S")
    clock.t += 2
    if not ws.close_if_idle() or ws.session is not None or fc.calls[-1][0] != "stop" or ws.closed_reason != "idle":
        bad.append("not closed after WORKSPACE_IDLE_S idle")
    # an action waiting behind close is refused, not run on whatever exists then
    ws, fc, _ = make()
    ws.start(CALC)
    out: list = []
    t = threading.Thread(target=lambda: out.append(ws.command("screenshot")))
    with ws._lock:  # an action in flight; another one queues behind it, then close() is called
        t.start()
        time.sleep(0.05)
        ws._close_generation += 1
    t.join()
    if not out or out[0]["status"] != "not_executed":
        bad.append(f"a superseded action ran: {out}")


# ------------------------------------------------------------ RealSession end to end
class Link:
    """Rose Agent's link: visible-screen commands are recorded; app_command goes to the controller."""

    connected, model = True, "Pixel 10"

    def __init__(self, controller):
        self.controller = controller
        self.visible: list = []

    def call(self, action, timeout=25.0, **args):
        if action == "list_apps":
            return {"status": "ok", "apps": APPS}
        if action == "app_command":
            if self.controller is None:
                return {"status": "unavailable", "reason": "App has no agent command provider"}
            if args.get("packageName") != CONTROLLER:
                raise AssertionError(args)
            return self.controller(args["operation"], args.get("params") or {})
        self.visible.append((action, args))
        if action == "inspect_ui":
            return {"status": "ok", "packageName": "com.google.android.apps.nexuslauncher", "snapshotId": "v1",
                    "nodes": [{"id": 0, "class": "android.widget.TextView", "text": "Home", "visible": True,
                               "bounds": [0, 0, 100, 100]}]}
        if action == "screenshot":
            return {"status": "ok", "width": SHOT[0], "height": SHOT[1], "displayWidth": DISPLAY[0],
                    "displayHeight": DISPLAY[1], "imageBase64": _jpeg()}
        if action in ("set_alarm", "set_timer", "play_music", "directions", "media_key"):
            return {"status": "started"}
        return {"status": "action_accepted"}


def real(controller=None) -> tuple[RealPhone, Link]:
    phone = RealPhone()
    link = Link(controller)
    phone.link = link  # type: ignore[assignment]
    if controller is not None:
        phone.workspace.now, phone.workspace.sleep = controller.clock.now, controller.clock.sleep
        phone.workspace.timers = False
    return phone, link


def mark_of(s, label: str) -> str:
    return str(next(e.mark for e in s.screen.elements if e.mark and e.label == label))


def session_checks(bad: list) -> None:
    old = realphone.SETTLE_S
    realphone.SETTLE_S = 0.0
    try:
        _session_checks(bad)
    finally:
        realphone.SETTLE_S = old


def _session_checks(bad: list) -> None:
    clock = Clock()
    fc = FakeController(clock)
    phone, link = real(fc)
    if phone.workspace_app() is not None:
        bad.append("a workspace app before anything opened")
    s = phone.session("task1")
    s.refresh()
    if not s.background or not phone.background_available():
        bad.append("background mode is not on with a controller")
    if "workspace is closed" not in s.ui_tree():
        bad.append(f"the closed workspace's tree: {s.ui_tree()}")
    r = s.open_app("calculator")
    if not r.ok or "Rose's workspace" not in r.message or phone.workspace_app() != "Calculator":
        bad.append(f"open_app in the workspace: {r} / {phone.workspace_app()}")
    s.ensure_image()
    tree = s.ui_tree()
    if not tree.startswith("Screen: Calculator") or 'button "1"' not in tree:
        bad.append(f"the workspace tree: {tree}")
    if s.look() is None or s.shot.get("displayWidth") != DISPLAY[0] or s.shot.get("width") != SHOT[0]:
        bad.append(f"the workspace screenshot: {s.shot}")
    for key in ("1", "+", "2", "="):
        r = s.tap(mark_of(s, key))
        if not r.ok:
            bad.append(f"tap {key}: {r}")
        s.ensure_image()
    if fc.apps[CALC]["display"] != "3" or 'text "3"' not in s.ui_tree():
        bad.append(f"the calculator shows {fc.apps[CALC]['display']!r}")
    # tap_point maps through the workspace screenshot onto its display
    one = next(n for n in fc.nodes.values() if n["text"] == "1")
    b = one["bounds"]
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    r = s.tap_point(cx / DISPLAY[0] * 1000, cy / DISPLAY[1] * 1000, "1")
    if not r.ok or fc.apps[CALC]["display"] != "31":
        bad.append(f"tap_point in the workspace: {r} {fc.apps[CALC]['display']!r}")
    if link.visible:
        bad.append(f"the visible screen was touched: {link.visible}")

    # a follow-up task continues in the same workspace
    session_id = fc.id
    phone.close_session("task1")
    s2 = phone.session("task2")
    s2.refresh()
    if fc.id != session_id or not s2.ui_tree().startswith("Screen: Calculator") or 'text "31"' not in s2.ui_tree():
        bad.append(f"the follow-up did not continue in the workspace: {s2.ui_tree()}")
    r = s2.open_app("Calculator")
    if not r.ok or fc.id != session_id:
        bad.append("reopening the same app replaced the workspace")
    # another app: a new workspace; typing goes to set_text
    r = s2.open_app("keep notes")
    if not r.ok or phone.workspace_app() != "Keep Notes":
        bad.append(f"switching apps: {r}")
    r = s2.type_text(mark_of(s2, "Note"), "milk")
    if not r.ok or fc.apps[NOTES]["note"] != "milk":
        bad.append(f"type_text in the workspace: {r}")
    if "PIN" in s2.ui_tree():
        bad.append("a password field was shown")
    # direct intents stay on the phone; search_places goes through the workspace
    n = len(link.visible)
    r = s2.direct("set_alarm", hour=6, minute=30, label="")
    if not r.ok or link.visible[n:] != [("set_alarm", {"hour": 6, "minute": 30, "label": ""})]:
        bad.append(f"set_alarm: {r} {link.visible[n:]}")
    r = s2.direct("search_places", query="coffee")
    if not r.ok or fc.target != MAPS or not fc.apps[MAPS]["searching"] or fc.apps[MAPS]["query"] != "coffee":
        bad.append(f"search_places in the workspace: {r}")
    if any(c[0] == "search_places" for c in link.visible):
        bad.append("search_places used the visible-screen intent")
    # home closes the workspace
    r = s2.home()
    if not r.ok or r.message != "Closed Rose's workspace." or fc.id is not None or phone.workspace_app() is not None:
        bad.append(f"home: {r}")
    if "workspace is closed" not in s2.ui_tree():
        bad.append("the tree after home")
    if phone.look_workspace() is not None or "closed" not in phone.look_status:
        bad.append(f"look_workspace on a closed workspace: {phone.look_status}")
    phone.close_session("task2")

    # idle close: the timer's check, once no task holds it
    s3 = phone.session("task3")
    s3.open_app("Calculator")
    clock.t += 500
    if phone.workspace.close_if_idle():
        bad.append("idle-closed while a task holds the workspace")
    img = phone.look_workspace()
    if img is None or img.size != SHOT or phone.look_status != "ok":
        bad.append(f"look_workspace: {phone.look_status}")
    phone.close_session("task3")
    clock.t += realphone.WORKSPACE_IDLE_S + 1
    if not phone.workspace.close_if_idle() or phone.workspace_app() is not None:
        bad.append("the idle workspace was not closed")

    # the owner's Stop mid-task: said plainly, and not reopened for that task
    s4 = phone.session("task4")
    s4.open_app("Calculator")
    fc.owner_stop()
    r = s4.tap(mark_of(s4, "1"))
    if r.ok or "owner stopped" not in r.message:
        bad.append(f"owner stop mid-task: {r}")
    r = s4.open_app("Calculator")
    if r.ok or fc.id is not None:
        bad.append("reopened after the owner's Stop in the same task")
    phone.close_session("task4")
    s5 = phone.session("task5")
    if not s5.open_app("Calculator").ok:
        bad.append("a new task could not reopen after the owner's Stop")
    status = phone.background_status()
    if status["app"] != "Calculator" or status["available"] is not True or status["session"] is None:
        bad.append(f"background_status: {status}")
    phone.close_session("task5")

    # Android refuses an app: plainly reported, nothing on the visible screen
    phone, link = real(FakeController(Clock(), allowed={CALC}))
    s = phone.session("t")
    r = s.open_app("amazon")
    if r.ok or r.message != "Android doesn't let Rose open Amazon Shopping in her workspace; nothing was opened.":
        bad.append(f"target_not_allowed message: {r.message}")
    if [c for c in link.visible if c[0] != "inspect_ui"] or link.visible:
        bad.append(f"a refused app fell back to the visible screen: {link.visible}")

    # locked: the agent is told plainly
    fc = FakeController(Clock())
    phone, link = real(fc)
    fc.locked = True
    r = phone.session("t").open_app("Calculator")
    if r.ok or r.message != "The phone is locked; unlock it and I'll continue.":
        bad.append(f"locked message: {r.message}")

    # on_screen sessions and phones without the controller use the visible screen exactly as before
    phone, link = real(FakeController(Clock()))
    s = phone.session("seen", on_screen=True)
    s.refresh()
    s.open_app("Calculator")
    if s.background or [c[0] for c in link.visible][:2] != ["inspect_ui", "open_app"]:
        bad.append(f"on_screen session: {link.visible}")
    phone, link = real(None)
    s = phone.session("old")
    s.refresh()
    if s.background or phone.background_available() or link.visible[:1] != [("inspect_ui", {})]:
        bad.append("a phone without the controller is not in visible mode")
    if phone.look_workspace() is not None or "not available" not in phone.look_status:
        bad.append("look_workspace without a controller")

    # reset closes the workspace (on a worker thread)
    fc = FakeController(Clock())
    phone, _ = real(fc)
    phone.session("t").open_app("Calculator")
    phone.close_session("t")
    phone.reset()
    for t in threading.enumerate():
        if t.name == "rose-workspace-close":
            t.join(5)
    if fc.id is not None or phone.workspace_app() is not None:
        bad.append("reset left the workspace open")

    # a reconnect drops local state; the next command adopts the live session
    fc = FakeController(Clock())
    phone, _ = real(fc)
    s = phone.session("t")
    s.open_app("Calculator")
    live = fc.id
    phone._link_changed()
    s.refresh()
    if phone.workspace.session is None or phone.workspace.session["sessionId"] != live or phone.workspace.task_id != fc.task:
        bad.append("the workspace was not re-adopted after a reconnect")


def probe_checks(bad: list) -> None:
    """An unanswered probe never moves a task onto the user's screen, and is not cached."""

    answers = [{"status": "timeout", "message": "The phone did not answer app_command in time."},
               {"status": "error", "message": "IllegalStateException: provider busy"}]
    for answer in answers:
        phone = RealPhone()
        link = Link(lambda op, p, a=answer: dict(a))
        phone.link = link  # type: ignore[assignment]
        s = phone.session("t")
        s.refresh()
        if not s.background or link.visible or phone._bg is not None or s._background is not None:
            bad.append(f"an unanswered probe ({answer['status']}) used the visible screen: {link.visible} {phone._bg}")
        r = s.tap(1)
        if link.visible or r.ok:
            bad.append(f"an action after an unanswered probe ran on the visible screen: {link.visible}")
    # a definite "unavailable" (no controller) is the visible screen, as before
    phone, link = real(None)
    s = phone.session("t")
    s.refresh()
    if s.background or phone._bg is None or phone._bg[0] or link.visible[:1] != [("inspect_ui", {})]:
        bad.append("a phone without the controller did not use the visible screen")


def switch_checks(bad: list) -> None:
    # Calculator, then Keep: the live Calculator session is closed before Keep starts (the
    # controller refuses a second start while one is live).
    ws, fc, _ = make()
    ws.start(CALC)
    fc.calls.clear()
    r = ws.start(NOTES)
    ops = [c[0] for c in fc.calls]
    if r["status"] != "opened" or "stop" not in ops or ops.index("stop") > ops.index("start"):
        bad.append(f"switching apps: {r['status']} via {ops}")


# ------------------------------------------------------------ two slots (provider schema 4)
class MultiController:
    """The two-slot controller (MultiSession.java): two ``FakeController`` slots, routed per request.

    A start without ``mode`` is slot 0's (refused if slot 1 holds the app); ``mode "alongside"``
    takes the slot already targeting the app, else a free one (``session_limit`` when none, and
    ``multi_session_unavailable`` when the platform allows one session). get_state and stop name a
    session (an unknown one is ``stale_session``; an unnamed stop with two live is
    ``session_required``); a command goes to its session's slot. Replies carry ``sessions``."""

    OCCUPIED = ("starting", "launching", "scheduled", "ready", "blocked", "cleanup_failed", "consent_required")

    def __init__(self, clock: Clock, limit: int = 2, **kw):
        self.clock, self.limit = clock, limit
        self.slots = [FakeController(clock, **kw), FakeController(clock, **kw)]
        self.calls: list[tuple[str, dict]] = []
        self.locked = False

    def __call__(self, operation: str, params: dict) -> dict:
        self.calls.append((operation, dict(params)))
        for slot in self.slots:
            slot.locked = self.locked
        if operation == "capabilities":
            return {"status": "ok", "schemaVersion": 4, "multiSession": self.limit >= 2, "maxSessions": self.limit}
        sid = params.get("sessionId")
        if operation == "start":
            i = self._start(params["packageName"], params.get("mode") or "", params.get("taskId"))
            if isinstance(i, str):
                return {"status": i, "actionExecuted": False, "sessions": self.summary()}
            return {**self.slots[i](operation, params), "sessions": self.summary()}
        if operation == "get_state":
            i = self._by(sid) if sid else 0
            if i is None:
                return {"status": "stale_session", "sessionId": sid, "sessions": self.summary()}
            return {**self.slots[i](operation, params), "sessions": self.summary()}
        if operation == "stop":
            if sid:
                i = self._by(sid)
                if i is None:
                    return {"status": "stale_session", "actionExecuted": False, "sessions": self.summary()}
            else:
                live = [j for j in range(2) if self._occupied(j)]
                if len(live) > 1:
                    return {"status": "session_required", "actionExecuted": False, "sessions": self.summary()}
                i = live[0] if live else 0
            return {**self.slots[i](operation, params), "sessions": self.summary()}
        i = self._by(sid)
        return self.slots[0 if i is None else i](operation, params)

    def _occupied(self, i: int) -> bool:
        s = self.slots[i]
        return s.id is not None and s.status in self.OCCUPIED

    def _holds(self, i: int, pkg: str) -> bool:
        return self._occupied(i) and self.slots[i].target == pkg

    def _by(self, sid) -> int | None:
        # The real slot keeps an ended session's id until its next start.
        return next((i for i, s in enumerate(self.slots) if sid and sid in (s.id, s.stopped_session)), None)

    def _start(self, pkg: str, mode: str, task) -> int | str:
        if any(task in s.owner_stopped_tasks for s in self.slots):
            return next(i for i, s in enumerate(self.slots) if task in s.owner_stopped_tasks)
        if not mode:
            return "app_in_other_session" if self._holds(1, pkg) else 0
        if mode != "alongside":
            return "invalid_mode"
        if self.limit < 2:
            return "multi_session_unavailable"
        for i in range(2):
            if self._holds(i, pkg):
                return i
        free = [i for i in range(2) if not self._occupied(i)]
        return free[0] if free else "session_limit"

    def summary(self) -> list[dict]:
        return [{"slot": i, "sessionId": s.id or s.stopped_session, "targetPackage": s.target, "taskId": s.task,
                 "status": s.status, "stopReason": s.stop_reason, "stoppedSessionId": s.stopped_session,
                 "stoppedTaskId": s.stopped_task} for i, s in enumerate(self.slots)]

    def slot_of(self, pkg: str) -> FakeController | None:
        return next((s for s in self.slots if s.id and s.target == pkg), None)


def multi_checks(bad: list) -> None:
    old = realphone.SETTLE_S
    realphone.SETTLE_S = 0.0
    try:
        _multi_checks(bad)
    finally:
        realphone.SETTLE_S = old


def _multi_checks(bad: list) -> None:
    clock = Clock()
    mc = MultiController(clock)
    phone, link = real(mc)
    a, b = phone.session("a"), phone.session("b")
    a.refresh()
    if not phone.workspaces.multi or len(phone.workspaces.units) != 2:
        bad.append(f"two slots not used: {phone.workspaces.max}")
    # two tasks at once, each in its own app and session
    if not a.open_app("calculator").ok or not b.open_app("keep notes").ok:
        bad.append("two tasks could not open two apps")
    calc, notes = mc.slot_of(CALC), mc.slot_of(NOTES)
    if calc is None or notes is None or calc is notes:
        bad.append(f"the two apps are not in two sessions: {mc.summary()}")
        return
    a.ensure_image()
    b.ensure_image()
    for key in ("1", "+", "2"):
        a.tap(mark_of(a, key))
        a.ensure_image()
    if not b.type_text(mark_of(b, "Note"), "milk").ok:
        bad.append("typing in the second task's app failed")
    a.tap(mark_of(a, "="))
    if calc.apps[CALC]["display"] != "3" or notes.apps[NOTES]["note"] != "milk":
        bad.append(f"interleaved work went wrong: {calc.apps[CALC]['display']!r} {notes.apps[NOTES]['note']!r}")
    if any(op == "stop" for op, _ in mc.calls):
        bad.append("a session was stopped while two tasks worked side by side")
    if phone.workspace_app("a") != "Calculator" or phone.workspace_app("b") != "Keep Notes":
        bad.append(f"each task's app: {phone.workspace_app('a')} / {phone.workspace_app('b')}")
    if sorted(phone.workspace_apps()) != ["Calculator", "Keep Notes"]:
        bad.append(f"workspace_apps: {phone.workspace_apps()}")
    if phone.workspace_session_id("a") != calc.id or phone.workspace_session_id("b") != notes.id:
        bad.append("workspace_session_id is not per task")
    # an app another running task has open is refused, never taken from it
    r = b.open_app("calculator")
    if r.ok or "another of her tasks" not in r.message or mc.slot_of(CALC) is not calc:
        bad.append(f"a running task's app was taken: {r}")
    # both slots held by running tasks: a third task is refused plainly
    c = phone.session("c")
    r = c.open_app("maps")
    if r.ok or "busy" not in r.message or mc.slot_of(CALC) is not calc or mc.slot_of(NOTES) is not notes:
        bad.append(f"a third task: {r}")
    phone.close_session("c")

    # the owner's Stop of one session ends that task only
    calc_task = calc.task
    calc.owner_stop()
    r = a.tap(mark_of(a, "1"))
    if r.ok or "owner stopped" not in r.message:
        bad.append(f"owner stop of one session: {r}")
    if mc.slot_of(NOTES) is not notes or not b.type_text(mark_of(b, "Note"), "eggs").ok:
        bad.append("the other task's session was affected by the owner's Stop")
    if a.open_app("calculator").ok:
        bad.append("reopened after the owner's Stop in the same task")
    phone.close_session("a")

    # one task keeps two apps and flips between them: Keep Notes stays open while it uses Maps
    phone.close_session("b")
    d = phone.session("d")
    d.refresh()
    if phone.workspace_app("d") != "Keep Notes":
        bad.append(f"a follow-up does not see the app left open: {phone.workspace_app('d')}")
    keep_id = notes.id
    if not d.open_app("maps").ok or mc.slot_of(NOTES) is None or mc.slot_of(NOTES).id != keep_id:
        bad.append(f"switching apps closed the other one: {mc.summary()}")
    if not d.open_app("keep notes").ok or mc.slot_of(NOTES).id != keep_id or notes.apps[NOTES]["note"] != "eggs":
        bad.append("flipping back did not return to the same session")
    if mc.slot_of(MAPS) is None:
        bad.append("flipping back closed Maps")
    # a third app with both slots held by this one task: its least recently used app is replaced
    if not d.open_app("calculator").ok or mc.slot_of(MAPS) is not None or mc.slot_of(NOTES) is None:
        bad.append(f"a third app in one task: {mc.summary()}")
    phone.close_session("d")
    status = phone.background_status()
    if status.get("slots") != 2 or len(status.get("workspaces") or []) != 2:
        bad.append(f"background_status: {status}")

    # idle close: each workspace on its own
    clock.t += realphone.WORKSPACE_IDLE_S + 1
    for unit in phone.workspaces.units:
        unit.close_if_idle()
    if phone.workspace_apps() or any(s.id and s.status == "ready" for s in mc.slots):
        bad.append(f"idle workspaces were not closed: {mc.summary()}")

    # a reconnect: each unit re-adopts its own session, never the other's
    mc = MultiController(Clock())
    phone, _ = real(mc)
    a, b = phone.session("a"), phone.session("b")
    a.open_app("calculator")
    b.open_app("keep notes")
    phone._link_changed()
    a.refresh()
    b.refresh()
    if not a.ui_tree().startswith("Screen: Calculator") or not b.ui_tree().startswith("Screen: Keep Notes"):
        bad.append(f"after a reconnect: {a.ui_tree()[:40]} / {b.ui_tree()[:40]}")

    # sessions Rose opened before a restart are stopped when they block a start; Rose Agent's are not
    mc = MultiController(Clock())
    for slot, task in zip(mc.slots, ("rose-ws-deadbeef-1-x", "rose-ws-deadbeef-2-y")):
        slot.op_start({"packageName": AMAZON if slot is mc.slots[0] else MAPS, "taskId": task})
        slot.status = "ready"
    phone, _ = real(mc)
    if not phone.session("t").open_app("calculator").ok or mc.slot_of(CALC) is None:
        bad.append(f"stray sessions blocked a start: {mc.summary()}")
    mc = MultiController(Clock())
    for slot, task in zip(mc.slots, ("pip-1", "pip-2")):
        slot.op_start({"packageName": AMAZON if slot is mc.slots[0] else MAPS, "taskId": task})
        slot.status = "ready"
    phone, _ = real(mc)
    r = phone.session("t").open_app("calculator")
    if r.ok or any(s.status != "ready" for s in mc.slots):
        bad.append(f"Rose Agent's own sessions were stopped: {r} {mc.summary()}")

    # reset closes every workspace
    mc = MultiController(Clock())
    phone, _ = real(mc)
    phone.session("a").open_app("calculator")
    phone.session("b").open_app("keep notes")
    phone.reset()
    for t in threading.enumerate():
        if t.name == "rose-workspace-close":
            t.join(5)
    if any(s.id for s in mc.slots):
        bad.append(f"reset left a workspace open: {mc.summary()}")

    # a platform with one session (schema 4, maxSessions 1): one shared workspace, as before
    mc = MultiController(Clock(), limit=1)
    phone, _ = real(mc)
    s = phone.session("t")
    s.refresh()
    if phone.workspaces.multi or not s.open_app("calculator").ok or any(p.get("mode") for _, p in mc.calls):
        bad.append(f"one-slot platform: {[c for c in mc.calls if c[0] == 'start']}")


def fake_phone_checks(bad: list) -> None:
    p = Phone()
    s = p.session("x", on_screen=True)
    if p.background_available() or p.workspace_app() is not None or p.look_workspace() is not None or s.background:
        bad.append("the fake phone claims a workspace")


def main() -> None:
    bad: list = []
    ported_checks(bad)
    controller_checks(bad)
    session_checks(bad)
    probe_checks(bad)
    switch_checks(bad)
    multi_checks(bad)
    fake_phone_checks(bad)
    if bad:
        raise SystemExit("FAILED:\n  " + "\n  ".join(bad))
    print("background checks passed")


if __name__ == "__main__":
    main()
