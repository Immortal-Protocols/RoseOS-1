"""Rose's workspace: the phone's background controller (``dev.pixel.backgroundcontrol``).

RoseOS has a second, separate screen for agent work: Android's ComputerControl runs one target
app on a virtual display, with a floating viewer and an ongoing notification whose Stop button
belongs to the owner (os/background-control/agent/src/dev/pixel/backgroundcontrol/Controller.java).
Rose's tasks run there, so the user's visible screen stays theirs. ``Workspace`` drives that
controller for one caller, Rose's task runner (through ``rose.realphone.RealPhone``). It is a
Python port of the old Node backend's ``BackgroundPhone``
(workspace/rose-agent/server/background-phone.mjs and its tests), with the same outcomes:

- It is reached with the ordinary phone command ``{"action": "app_command", "packageName":
  "dev.pixel.backgroundcontrol", "operation": OP, "params": {...}}`` over the existing link; the
  phone passes it through untouched. Operations: ``get_state``, ``start {packageName, taskId}``,
  ``command {sessionId, action, ...}``, ``stop``.
- A session is scoped to ONE target package. Another app means a new session (``start`` replaces
  the old one); ``home`` means ``stop``.
- Every command except screenshot/inspect_ui/app_context/home needs the ``snapshotId`` of the
  controller's LATEST observation (< 30 s old), and both ``screenshot`` and every accepted action
  clear the node map. So before each node action the target is refreshed: the screen is inspected
  again and the same node is found by its identity (class, view id, text, bounds, flags); if it is
  gone or ambiguous nothing runs ("stale_screen"). ``back`` gets a fresh snapshot the same way.
- Schema 3 controllers tag a session with the ``taskId`` given at ``start`` and refuse a
  ``command`` carrying another id (``stale_task``) or a ``start`` for a task the owner stopped
  (``user_stopped``). Rose uses one id per workspace generation (``rose-ws-<nonce>-<n>-<package>``,
  the nonce new per process, because the controller remembers owner-stopped ids across Rose
  restarts), so a follow-up task continues in the same workspace.
- The owner's Stop shows as ``status "stopped"`` with a ``stopReason`` other than ``"agent"`` for
  our session. The task is then over: the stop is latched and nothing reopens the workspace until
  a new task begins (``begin_task``). A Stop that happened before the current task began (the
  workspace sat open between tasks) only closes the workspace: it is never charged to the new task
  (BackgroundPhone.beginTask drops the old session too). A stop seen across a link reconnect is
  matched against the session held before it. The controller's own deadline ("deadline") and a
  session Android closed or failed just close the workspace (``workspace_closed``); a reused
  session is renewed before it nears the 200-operation / 600 s limits.
- While the owner is away Rose Agent refuses background work unless they armed app control while
  locked (``status "locked"``), and Android refuses or blocks sessions at owner gates. Neither is a
  retryable failure and neither is ever worked around.

Calls block on the phone (one round trip each; ``start`` polls ``get_state`` every 150 ms for up to
12 s), so call them off the MLX thread. They are serialised by one lock, like BackgroundPhone's
queue: an action waiting behind ``close`` is refused when its turn comes, never run on whatever
workspace exists then. Screenshots stay in memory: results are handed back, never logged.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Callable

CONTROLLER = "dev.pixel.backgroundcontrol"
START_POLL_S = 0.15     # get_state interval while the workspace starts (BackgroundPhone: 150 ms)
START_WAIT_S = 12.0     # how long a start may stay starting/launching/scheduled
WORKSPACE_IDLE_S = 180.0  # an unused workspace is closed after this long
MAX_OBSERVATIONS = 16   # remembered observations (for refreshing a node by its identity)
# A session has 200 operations and 600 s (Controller.java). The workspace is kept across tasks,
# so a reused session is renewed (stopped and started again) at open_app once it is this worn,
# rather than running into the limits halfway through a follow-up task.
ROTATE_OPS = 150
ROTATE_MS = 480_000

STARTING = ("starting", "launching", "scheduled")
# Statuses after which a session can never run another command.
ENDED_STATES = {"idle", "stopped", "closed", "failed", "rejected", "cleanup_failed", "consent_required",
                "target_not_allowed"}
# Every status the controller (or Rose Agent's lock gate in front of it) answers get_state with.
# Anything else ("unavailable": no controller installed, "offline", "timeout", ...) means there is
# no workspace on this phone.
CONTROLLER_STATES = {"idle", "starting", "launching", "scheduled", "ready", "blocked", "consent_required",
                     "target_not_allowed", "failed", "rejected", "closed", "stopped", "cleanup_failed", "locked"}
NODE_ACTIONS = {"tap", "long_press", "touch_point", "set_text", "ime_enter", "scroll", "swipe", "drag"}
LABEL_ACTIONS = {"tap_label", "touch_label"}
UI_ACTIONS = NODE_ACTIONS | LABEL_ACTIONS | {"screenshot", "inspect_ui", "app_context", "screen_gesture",
                                             "back", "home"}


def stop_kind(state: dict | None, session: dict | None) -> str | None:
    """How the controller's session ``session`` ended, from a get_state answer: "owner", "expired"
    or None (not stopped, stopped by us, or another session).

    Agent stop/home clears our session first, so it never matches. Schema 3 controllers report
    ``stopReason`` ("user", "agent" or "deadline") and ``stoppedSessionId``; a "deadline" stop is
    the controller's own 600 s limit, which closes the workspace without any owner intent. Schema 2
    keeps the last ``sessionId`` and no reason: its stops are taken as the owner's (the safe
    reading; Rose renews a workspace well before the deadline, see ROTATE_MS).
    """

    if not session or not isinstance(state, dict) or state.get("status") != "stopped" \
            or state.get("stopReason") == "agent":
        return None
    ended = state.get("stoppedSessionId") or state.get("sessionId")
    if ended and ended != session["sessionId"]:
        return None
    return "expired" if state.get("stopReason") == "deadline" else "owner"


def owner_stopped(state: dict | None, session: dict | None) -> bool:
    """The owner (not us, not the deadline) stopped the session we hold."""

    return stop_kind(state, session) == "owner"


def _gone_reason(error: str) -> str | None:
    """A command error meaning our session is no longer usable, as a short reason, or None.

    "Background session is not ready: <status>" with a terminal status (Android closed it, the
    launch failed, someone stopped it), "Session expired" (the 600 s deadline), "Session operation
    limit reached" (200 operations) or "Stale session ID" (the controller holds another session).
    "blocked" and the starting states are not terminal: they are handled where they occur."""

    low = (error or "").lower()
    m = re.search(r"not ready: ([a-z_]+)", low)
    if m:
        return m.group(1) if m.group(1) in ENDED_STATES else None
    if "session expired" in low:
        return "expired"
    if "operation limit" in low:
        return "limit"
    if "stale session id" in low:
        return "replaced"
    return None


def _worn(state: dict) -> bool:
    """A ready session near its operation or time limit (see ROTATE_OPS / ROTATE_MS)."""

    def num(key: str) -> float:
        try:
            return float(state.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0
    return num("operationCount") >= ROTATE_OPS or num("elapsedMs") >= ROTATE_MS


CLOSED_WHY = {"deadline": "its time limit ran out", "expired": "its time limit ran out",
              "limit": "it reached its action limit", "owner_stop": "the owner closed it before this task",
              "closed": "Android closed it", "failed": "the app failed to run there",
              "replaced": "it was replaced", "stopped": "it was stopped"}


def owner_unlock_required(s: dict | None) -> bool:
    """Rose Agent's lock gate refused, or Android blocked the session until the owner acts."""

    return isinstance(s, dict) and (s.get("status") == "locked" or s.get("reason") in
                                    ("owner_unlock_required", "device_locked"))


def locked_result(details: dict | None, **extra: Any) -> dict:
    details = details or {}
    control = details.get("lockedControl")
    if control is None:
        control = "os_refused" if details.get("reason") == "device_locked" else "owner_gate"
    return {"status": "locked", "actionExecuted": False, "backgroundExecution": True,
            "reason": details.get("message") or "The phone needs its owner: unlock it, or allow app control "
                                                "while locked, before this can continue.",
            "lockedControl": control, **extra, "details": details}


KEY_FIELDS = ("packageName", "class", "viewId", "text", "description", "bounds", "clickable", "editable",
              "scrollable", "password")


def node_key(n: dict) -> str:
    """A node's identity across observations (ids are per observation)."""

    return json.dumps([n.get("packageName") or "", n.get("className") or n.get("class") or "", n.get("viewId") or "",
                       n.get("text") or "", n.get("description") or "", list(n.get("bounds") or []),
                       bool(n.get("clickable")), bool(n.get("editable")), bool(n.get("scrollable")),
                       bool(n.get("password"))])


def _node_id(n: dict) -> Any:
    return n.get("nodeId", n.get("id"))


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


class Workspace:
    """One caller's background workspace (see the module docstring).

    ``send(operation, params) -> dict`` delivers one controller operation (RealPhone sends it as
    ``app_command`` over the link). ``in_use()`` says a task is using the workspace right now, so
    the idle timer leaves it open. ``on_change()`` is called after the workspace opens or closes.
    ``now``/``sleep`` and ``timers`` are for tests (a fake clock, no timer threads).
    """

    def __init__(self, send: Callable[[str, dict], dict], *, now: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, record: Callable[[dict], None] | None = None,
                 on_change: Callable[[], None] | None = None, in_use: Callable[[], bool] = lambda: False,
                 idle_s: float = WORKSPACE_IDLE_S, timers: bool = True):
        self.send, self.now, self.sleep = send, now, sleep
        self.record = record or (lambda event: None)
        self.on_change = on_change
        self.in_use = in_use
        self.idle_s, self.timers = idle_s, timers
        self._lock = threading.RLock()
        self.session: dict | None = None     # {"sessionId", "targetPackage"} of our live workspace
        self.task_id: str | None = None      # this workspace generation's id, sent as taskId
        self._generation = 0
        # New per process: a schema 3 controller remembers the last 32 owner-stopped task ids
        # across Rose restarts, so "rose-ws-1-<pkg>" from an earlier run must never come back.
        self._nonce = uuid.uuid4().hex[:8]
        self._new_task = False  # begin_task ran and the task has not used the workspace yet
        self._forgotten: dict | None = None  # the session held before a link reconnect
        self._close_generation = 0
        self._observations: OrderedDict[str, dict] = OrderedDict()
        self.stopped_by_owner: str | None = None  # latched: the owner ended the workspace for this task
        self.closed_reason = ""              # why the last workspace closed (status endpoint)
        self._used = now()
        self._timer: threading.Timer | None = None
        self._timer_lock = threading.Lock()  # guards _timer only; never held while calling the phone
        self._link_reset = False  # the link reconnected: drop local state before the next call

    # ----- public ------------------------------------------------------------------
    def begin_task(self) -> None:
        """A new user task may open the workspace again after an owner Stop ended the last one.

        Unlike BackgroundPhone the live workspace is kept: a follow-up task on the same app
        continues in it. The owner-stop latch is cleared, and until the task's first call a
        Stop found for the kept session belongs to an earlier task: it closes the workspace
        quietly instead of ending this task (``_check_stop``)."""

        # No lock: this is called on the MLX thread while a task thread may hold the lock for a
        # blocking call. Assigning the attributes is atomic.
        self.stopped_by_owner = None
        self._new_task = True

    def get_state(self) -> dict:
        """The controller's state (read-only; never changes the workspace)."""

        return self.call("get_state")

    def start(self, package: str) -> dict:
        """Open ``package`` in the workspace, or reuse the workspace that already has it."""

        with self._lock:
            self._after_reconnect()
            result = self._start(package)
            self._new_task = False
        self._touch()
        return result

    def command(self, action: str, **params: Any) -> dict:
        """One UI command in the workspace (BackgroundPhone.run for background UI actions)."""

        generation = self._close_generation
        with self._lock:
            self._after_reconnect()
            if generation != self._close_generation:
                result = self._superseded(action)
            else:
                result = self._run({"action": action, **params})
                self._new_task = False
        self._touch()
        return result

    def screenshot(self) -> dict:
        return self.command("screenshot")

    def inspect(self) -> dict:
        return self.command("inspect_ui")

    def observe(self) -> tuple[dict, dict]:
        """(screenshot, inspect_ui) of the workspace, in that order and under one lock hold.

        ``screenshot`` clears the controller's node map and issues a new snapshot id, so the tree
        is read after it: its ``snapshotId`` and node ids are the latest ones, and the image and
        tree show the same frame."""

        with self._lock:
            shot = self.command("screenshot")
            if shot.get("status") in ("locked", "stopped_by_owner", "no_background_app", "not_executed",
                                      "workspace_closed", "offline", "timeout", "disconnected"):
                return shot, shot
            return shot, self.command("inspect_ui")

    def close(self, reason: str = "closed") -> dict:
        """Close the workspace (``stop``). Actions already waiting are refused, not run."""

        self._close_generation += 1
        with self._lock:
            self._after_reconnect()
            result = self._close_now(reason)
        return result

    def close_if_idle(self) -> bool:
        """Close the workspace if nothing used it for ``idle_s`` and no task holds it."""

        with self._lock:
            self._after_reconnect()
            if self.session is None or self.in_use() or self.now() - self._used < self.idle_s:
                return False
            self._close_generation += 1
            self._close_now("idle")
            return True

    def forget(self) -> None:
        """Drop local state without touching the phone (the link reconnected).

        The controller's session may well still be live; the next command adopts it from
        ``get_state`` (with its taskId), so nothing is lost. Called on the server's event loop,
        so it only sets a flag: taking the lock there could deadlock with a task thread that holds
        it while waiting for that loop to deliver the phone's answer."""

        self._link_reset = True

    def _after_reconnect(self) -> None:
        if self._link_reset:
            self._link_reset = False
            # Kept so an owner Stop that happened around the reconnect is still recognised as a
            # stop of OUR session on the next get_state (else the task would reopen it).
            self._forgotten = self.session or self._forgotten
            self.session = None
            self._observations.clear()

    def state(self) -> dict:
        """For the status endpoint: no phone call and no lock (a task may hold it for a while)."""

        session = self.session
        idle_in = None if session is None else max(0.0, round(self.idle_s - (self.now() - self._used), 1))
        return {"session": dict(session) if session else None, "taskId": self.task_id,
                "stoppedByOwner": self.stopped_by_owner is not None, "closedReason": self.closed_reason,
                "idleCloseInS": idle_in}

    # ----- controller calls --------------------------------------------------------------
    def call(self, operation: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        # Sent on every start/command/stop so a schema 3 controller refuses work for another
        # workspace generation instead of driving whatever it holds now.
        if self.task_id and operation in ("start", "command", "stop"):
            params["taskId"] = self.task_id
        t0 = time.monotonic()
        r = self.send(operation, params)
        r = r if isinstance(r, dict) else {"status": "error", "error": "no answer"}
        # Every call's timing (operation, action and status only): slow or lost answers show up here.
        self.record({"stage": "background_call", "operation": operation, "action": params.get("action"),
                     "ms": round((time.monotonic() - t0) * 1000), "status": r.get("status")})
        return r

    # ----- internals (lock held) ----------------------------------------------------
    def _remember(self, observation: dict) -> None:
        if observation.get("status") != "ok" or not observation.get("snapshotId") \
                or not isinstance(observation.get("nodes"), list):
            return
        self._observations[observation["snapshotId"]] = {
            "sessionId": self.session and self.session["sessionId"],
            "nodes": [{**n, "bounds": list(n.get("bounds") or [])} for n in observation["nodes"]]}
        while len(self._observations) > MAX_OBSERVATIONS:
            self._observations.popitem(last=False)

    def _undetected_owner_stop(self) -> dict | None:
        """An owner Stop not yet seen by an action: check before acting or closing.

        Returns the stopped_by_owner result when it ends this task (see ``_check_stop``)."""

        if not self.session:
            return None
        return self._check_stop(self.call("get_state"))

    def _check_stop(self, state: dict | None) -> dict | None:
        """Reconcile a get_state answer with the session we hold (or held before a reconnect).

        Returns the stopped_by_owner result when the owner's Stop ends the current task. A stop
        by the controller's deadline, or an owner Stop found before the current task used the
        workspace (it was pressed while the workspace sat open between tasks), only drops the
        session (``workspace_closed`` from then on): an earlier task's Stop is never charged to a
        new task."""

        held = self.session or self._forgotten
        kind = stop_kind(state, held)
        if kind is None:
            if isinstance(state, dict) and state.get("status") in CONTROLLER_STATES:
                self._forgotten = None  # a definite answer about another (or no) session
            return None
        self._forgotten = None
        if kind == "owner" and not self._new_task:
            self.session = held
            return self._owner_stop(state)
        self._dropped("owner_stop" if kind == "owner" else "deadline", state)
        return None

    def _dropped(self, reason: str, state: dict | None = None) -> None:
        """Forget a session that ended without this task's owner Stop (closed, failed, deadline)."""

        had = self.session is not None
        self.session = None
        self._observations.clear()
        self.closed_reason = reason
        self.record({"stage": "background_closed", "reason": reason,
                     "status": (state or {}).get("status")})
        if had:
            self._changed()

    def _closed(self, reason: str) -> dict:
        return {"status": "workspace_closed", "actionExecuted": False, "backgroundExecution": True,
                "closedReason": reason,
                "reason": f"Rose's workspace closed before this action ran ({CLOSED_WHY.get(reason, reason)}); "
                          "open_app opens the app there again."}

    def _lost(self, result: dict) -> dict | None:
        """A command failed because our session ended: the result to hand back, or None.

        Reconciles with get_state first, so an owner Stop is still latched as one; any other
        end drops the session, so ``workspace_app`` never reports an app that is not open."""

        if result.get("status") != "error":
            return None
        reason = _gone_reason(str(result.get("error", "")))
        if reason is None:
            return None
        stop = self._check_stop(self.call("get_state"))
        if stop is not None:
            return stop
        if self.session is not None:
            if reason in ("limit", "expired"):
                self.call("stop")  # the controller still holds it; the next open_app starts afresh
            self._dropped(reason)
        return self._closed(self.closed_reason or reason)

    def _owner_stop(self, state: dict | None = None) -> dict:
        session_id = self.stopped_by_owner or (self.session and self.session["sessionId"]) \
            or (state or {}).get("stoppedSessionId")
        if not self.stopped_by_owner:
            self.stopped_by_owner = session_id or "unknown"
            self.session = None
            self._observations.clear()
            self.closed_reason = "owner_stop"
            self.record({"stage": "background_owner_stop", "sessionId": session_id,
                         "stopReason": (state or {}).get("stopReason") or "inferred"})
            self._changed()
        return {"status": "stopped_by_owner", "actionExecuted": False, "backgroundExecution": True,
                "sessionId": session_id,
                "reason": "The owner stopped the background workspace. Rose does not reopen it for this task."}

    def _adopt(self, state: dict) -> None:
        """Take over the controller's live session (after a reconnect or a restart)."""

        if (self.session or {}).get("sessionId") != state["sessionId"]:
            self._observations.clear()
            self.task_id = state.get("taskId") or self.task_id
        self.session = {"sessionId": state["sessionId"], "targetPackage": state["targetPackage"]}

    def _start(self, package: str) -> dict:
        if self.stopped_by_owner:
            return self._owner_stop()
        state = self.call("get_state")
        stop = self._check_stop(state)
        if stop is not None:
            return stop
        if state.get("status") == "ready" and state.get("targetPackage") == package and _worn(state):
            # Reused across tasks, this session nears its 200 operations / 600 s: renew it now
            # (a fresh app screen) rather than fail halfway through the task.
            self.record({"stage": "background_renew", "packageName": package,
                         "operationCount": state.get("operationCount"), "elapsedMs": state.get("elapsedMs")})
            self.call("stop")
            self.session = None
            self._observations.clear()
            state = {**state, "status": "stopped"}
        # Reopening the same app while Android blocks it (an installer, an owner gate) must not
        # destroy the live workspace: it resumes once the pending screen is done.
        if state.get("status") == "blocked" and state.get("targetPackage") == package and state.get("sessionId"):
            self._adopt(state)
            out = {"status": "background_blocked", "actionExecuted": False, "packageName": package,
                   "backgroundExecution": True, "sessionId": state["sessionId"], "sessionRetained": True,
                   "details": state}
            if owner_unlock_required(state):
                return {**out, "ownerUnlockRequired": True,
                        "reason": "The background app reached a screen that needs the owner while the phone is "
                                  "locked. The workspace has been retained; it continues after the owner unlocks "
                                  "and completes that screen."}
            return {**out, "reason": "The existing background app is blocked by Android. Resolve its pending "
                                     "operation before continuing; the workspace has been retained."}
        if state.get("status") != "ready" or state.get("targetPackage") != package:
            # A new workspace generation: its own id, so commands meant for the old one are refused.
            self._generation += 1
            previous, self.task_id = self.task_id, f"rose-ws-{self._nonce}-{self._generation}-{package}"[:128]
            state = self.call("start", {"packageName": package})
            if state.get("status") == "user_stopped":
                # Schema 3: the owner already stopped this task; nothing was opened.
                rejected = state.get("rejectedTaskId") or state.get("stoppedTaskId") or self.task_id
                if rejected != self.task_id:
                    self.task_id = previous
                    self.record({"stage": "background_stale_task_refused", "rejectedTaskId": rejected})
                    return {"status": "not_executed", "actionExecuted": False, "backgroundExecution": True,
                            "rejectedTaskId": rejected,
                            "reason": "The controller refused a start for an earlier, stopped task. "
                                      "The current task is unchanged."}
                return self._owner_stop(state)
            if state.get("status") not in CONTROLLER_STATES:
                # The reply was lost (timeout, "uncertain", "running"): the start may well be under
                # way on the phone, so read the controller's state instead of giving up.
                self.record({"stage": "background_start_unconfirmed", "status": state.get("status")})
                state = {"status": "starting"}
            deadline = self.now() + START_WAIT_S
            while state.get("status") in STARTING and self.now() < deadline:
                self.sleep(START_POLL_S)
                state = self.call("get_state")
        if state.get("status") != "ready" or not state.get("sessionId") or state.get("targetPackage") != package:
            had = self.session is not None
            self.session = None
            self._observations.clear()
            if had:
                self.closed_reason = "replaced"
                self._changed()
            if owner_unlock_required(state):
                return locked_result(state, packageName=package)
            if state.get("status") == "target_not_allowed":
                # Android's ComputerControl allowlist refused this app on this OS build.
                self.record({"stage": "background_target_not_allowed", "packageName": package})
                return {"status": "background_target_not_allowed", "actionExecuted": False, "packageName": package,
                        "backgroundExecution": True, "anyAppControl": state.get("anyAppControl") is True,
                        "reason": "This phone's OS does not let Rose use this app in its background workspace. "
                                  "Nothing was opened.", "details": state}
            return {"status": "background_consent_required" if state.get("status") == "consent_required"
                    else "background_unavailable", "actionExecuted": False, "packageName": package,
                    "backgroundExecution": True, "details": state}
        opened = self.session is None or self.session.get("sessionId") != state["sessionId"]
        self._adopt(state)
        observation = self.call("command", {**self.session, "action": "inspect_ui"})
        if opened:
            self.closed_reason = ""
            self._changed()
        if owner_unlock_required(observation):
            return locked_result(observation, packageName=package, sessionId=state["sessionId"])
        self._remember(observation)
        verified = observation.get("status") == "ok" and observation.get("packageName") == package
        self.record({"stage": "background_open", "packageName": package, "sessionId": state["sessionId"],
                     "verified": verified})
        return {"status": "opened" if verified else "launch_unverified", "packageName": package,
                "backgroundExecution": True, "backgroundVerified": verified, "foregroundVerified": False,
                "sessionId": state["sessionId"], "observation": observation}

    def _refresh_target(self, command: dict) -> dict:
        """Re-inspect and re-find the observed node by its identity (ids are per observation)."""

        prior = self._observations.get(str(command.get("snapshotId")))
        has_node = _is_int(command.get("nodeId"))
        if command["action"] in NODE_ACTIONS and not has_node:
            return {"error": "A previously observed target is required; inspect the current controls."}
        target = None
        if has_node and prior is not None and prior["sessionId"] == self.session["sessionId"]:
            target = next((n for n in prior["nodes"] if _node_id(n) == command["nodeId"]), None)
        if has_node and target is None:
            return {"error": "The observed target is no longer available; inspect the current controls."}
        current = self.call("command", {"action": "inspect_ui", "sessionId": self.session["sessionId"]})
        if owner_unlock_required(current):
            return {"locked": current}
        lost = self._lost(current)
        if lost is not None:
            return {"result": lost}
        self._remember(current)
        if current.get("status") != "ok" or not current.get("snapshotId") or \
                (current.get("sessionId") and current["sessionId"] != self.session["sessionId"]):
            return {"error": "Could not refresh controls in the existing background workspace."}
        node_id = command.get("nodeId")
        if target is not None:
            matches = [n for n in current.get("nodes") or [] if node_key(n) == node_key(target)]
            if len(matches) != 1:
                # Which identity fields moved on the node now at the same place (names only: no text).
                near = next((n for n in current.get("nodes") or [] if _node_id(n) == command.get("nodeId")), None)
                moved = [i for i, (a, b) in enumerate(zip(json.loads(node_key(target)), json.loads(node_key(near))))
                         if a != b] if near is not None else None
                self.record({"stage": "background_target_mismatch", "action": command["action"],
                             "matches": len(matches), "fields": [KEY_FIELDS[i] for i in moved] if moved else moved})
                return {"error": "The observed target changed or is ambiguous; inspect the current controls."}
            node_id = _node_id(matches[0])
        self.record({"stage": "background_target_refreshed", "action": command["action"],
                     "packageName": self.session["targetPackage"]})
        out = {**command, "snapshotId": current["snapshotId"]}
        if has_node:
            out["nodeId"] = node_id
        return {"command": out}

    def _run(self, command: dict) -> dict:
        action = command["action"]
        # After an owner Stop nothing runs in the workspace until the next task.
        if self.stopped_by_owner:
            return self._owner_stop()
        if action not in UI_ACTIONS:
            return {"status": "not_executed", "actionExecuted": False, "backgroundExecution": True,
                    "reason": "This operation is not verified for background control. Use background phone UI controls."}
        if action == "home":
            stop = self._undetected_owner_stop()
            if stop is not None:
                return stop
            self._close_now("home")
            return {"status": "action_accepted", "actionExecuted": True, "backgroundExecution": True,
                    "workspaceClosed": True}
        if self.session and self._new_task:
            # The first call of a new task on a workspace kept from an earlier one: a Stop
            # pressed in between closes it here, before this task acts (never charged to it).
            stop = self._check_stop(self.call("get_state"))
            if stop is not None:
                return stop
        if not self.session:
            state = self.call("get_state")
            stop = self._check_stop(state)
            if stop is not None:
                return stop
            if state.get("status") in ("offline", "timeout", "disconnected"):
                # No answer from the phone: say that, not "the workspace is closed".
                return {**state, "actionExecuted": False, "backgroundExecution": True}
            if state.get("status") == "ready" and state.get("sessionId") and state.get("targetPackage"):
                self._adopt(state)
        if not self.session:
            return {"status": "no_background_app", "actionExecuted": False, "backgroundExecution": True,
                    "reason": "Use open_app to select an app in the background workspace first."}
        request = {**command, "sessionId": self.session["sessionId"]}
        if action in NODE_ACTIONS or action in LABEL_ACTIONS:
            refreshed = self._refresh_target(command)
            if "locked" in refreshed:
                return locked_result(refreshed["locked"], sessionId=self.session["sessionId"])
            if "result" in refreshed:
                return refreshed["result"]
            if "error" in refreshed:
                return {"status": "stale_screen", "actionExecuted": False, "reason": refreshed["error"],
                        "backgroundExecution": True}
            request = {**refreshed["command"], "sessionId": self.session["sessionId"]}
        # Back takes no snapshot argument from callers, but the controller needs a current frame
        # before every navigation action.
        if action == "back" and not command.get("snapshotId"):
            observation = self.call("command", {"action": "inspect_ui", "sessionId": self.session["sessionId"]})
            if owner_unlock_required(observation):
                return locked_result(observation, sessionId=self.session["sessionId"])
            lost = self._lost(observation)
            if lost is not None:
                return lost
            if observation.get("status") != "ok" or not observation.get("snapshotId"):
                return {"status": "not_executed", "actionExecuted": False, "backgroundExecution": True,
                        "reason": "Could not observe the background app before navigating back.",
                        "details": observation}
            self._remember(observation)
            request["snapshotId"] = observation["snapshotId"]
        result = self.call("command", request)
        if result.get("status") == "stale_task":
            # Schema 3: the workspace belongs to another task; nothing ran.
            return {"status": "not_executed", "actionExecuted": False, "backgroundExecution": True,
                    "rejectedTaskId": result.get("rejectedTaskId"),
                    "reason": "The background workspace belongs to another task; this action did not run."}
        error = str(result.get("error", ""))
        # A blocked session answers "not ready"; its state says whether the owner must act.
        if result.get("status") == "error" and "not ready: blocked" in error:
            state = self.call("get_state")
            if owner_unlock_required(state):
                result = state
        if not owner_unlock_required(result):
            lost = self._lost(result)
            if lost is not None:
                return lost
        if owner_unlock_required(result):
            self.record({"stage": "background_locked", "action": action,
                         "packageName": self.session["targetPackage"],
                         "lockedControl": result.get("lockedControl") or result.get("reason")})
            return locked_result(result, sessionId=self.session["sessionId"])
        self._remember(result)
        self.record({"stage": "background_action", "action": action, "packageName": self.session["targetPackage"],
                     "sessionId": self.session["sessionId"], "status": result.get("status")})
        return {**result, "backgroundExecution": True, "sessionId": self.session["sessionId"]}

    def _close_now(self, reason: str) -> dict:
        """Stop the controller; "cleanup_failed" means the workspace may still be open."""

        try:
            # Clearing the session would otherwise forget an owner Stop nobody acted on (schema 2).
            self._undetected_owner_stop()
        except Exception:
            pass  # closing must still be attempted
        had = self.session is not None
        try:
            return self.call("stop")
        finally:
            self.session = None
            self._observations.clear()
            self._cancel_timer()
            if had:
                self.closed_reason = self.closed_reason if self.stopped_by_owner else reason
                self._changed()

    def _superseded(self, action: str) -> dict:
        if self.stopped_by_owner:
            return self._owner_stop()
        self.record({"stage": "background_action_superseded", "action": action})
        return {"status": "not_executed", "actionExecuted": False, "backgroundExecution": True,
                "reason": "The background workspace was closed before this action ran."}

    # ----- idle timer ------------------------------------------------------------------
    def _touch(self) -> None:
        """Note a use; arm the idle timer while a workspace is open (lock-free: any thread)."""

        self._used = self.now()
        if self.session is None:
            self._cancel_timer()
        elif self.timers:
            self._arm(self.idle_s, replace=False)

    def touch(self) -> None:
        """A task finished with the workspace: its idle time starts now."""

        self._touch()

    def _arm(self, delay: float, replace: bool = True) -> None:
        with self._timer_lock:
            if self._timer is not None and not replace:
                return  # the armed timer re-checks the idle time when it fires
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(max(1.0, delay), self._idle_fire)
            self._timer.daemon = True
            self._timer.start()

    def _cancel_timer(self) -> None:
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _idle_fire(self) -> None:
        with self._timer_lock:
            self._timer = None
        if self.session is None:
            return
        left = self.idle_s - (self.now() - self._used)
        if left > 0 or self.in_use():
            if self.timers:
                self._arm(left if left > 0 else self.idle_s)
            return
        try:
            self.close_if_idle()
        except Exception:
            pass  # the phone may be gone; the next start reconciles with get_state

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass
