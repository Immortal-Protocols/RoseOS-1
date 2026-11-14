"""Project 2: start a Rose task the owner confirmed on the workspace page (docs/APP-BUILDER.md §4.3).

The page's "Rose task · asks first" buttons come from OS Product's registry (`agent_task`, id ``task:<template>``).
After the owner confirms, the phone sends ``POST /workspace/v1/tasks``; this module checks it and hands the task to
an injected ``start(label, goal, request_id)`` that runs it as the owner's own request through the existing
conversation route and returns the task id (or None when the task id is known only once the task appears in the
task events). It never runs anything itself, and nothing is mounted until a host enables it.

- ``accepted`` means started, never done; progress and the result arrive as the existing task events.
- A repeated ``requestId`` from the same device returns the first reply and starts nothing (stored durably).
- Without ``confirmed: true`` the request is refused; the server never starts a page task on its own.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

ACTION_ID = re.compile(r'task:[A-Za-z0-9._-]{1,48}')
LIMITS = {'requestId': 64, 'label': 60, 'goal': 300}


class Unavailable(Exception):
    """The server cannot start tasks right now (no conversation running); nothing was started."""


def _text(value, limit):
    return (isinstance(value, str) and value.strip() and len(value) <= limit and '\x00' not in value
            and _utf8(value))


def _utf8(value: str) -> bool:
    try:
        value.encode('utf-8')
        return True
    except UnicodeEncodeError:
        return False


def check(body) -> str | None:
    """None when the body is a valid v1 request, else the refusal reason."""
    if not isinstance(body, dict) or type(body.get('schemaVersion')) is not int or body['schemaVersion'] != 1:
        return 'schemaVersion must be 1'
    extra = set(body) - {'schemaVersion', 'requestId', 'actionId', 'label', 'goal', 'confirmed'}
    if extra:
        return 'unknown field ' + sorted(extra)[0]
    for name, limit in LIMITS.items():
        if not _text(body.get(name), limit):
            return f'{name} must be 1 to {limit} characters'
    if not isinstance(body.get('actionId'), str) or not ACTION_ID.fullmatch(body['actionId']):
        return 'actionId must be task:<template id>'
    if body.get('confirmed') is not True:
        return 'the owner has not confirmed this task'
    return None


STATES = ('running', 'done', 'failed', 'cancelled', 'uncertain')
FINAL = ('done', 'failed', 'cancelled', 'uncertain')
PHONE_FINAL = ('done', 'failed', 'cancelled')
RECENT = 20
PHONE_TASKS_SHOWN = 8  # at most this many phoneTasks on the wire; every one still counts toward the state
DETAIL = {
    'done': 'Rose finished this.',
    'failed': 'Rose could not finish this.',
    'cancelled': 'This was stopped before it finished.',
    'uncertain': 'Rose restarted before this finished, so its outcome is unknown.',
}


def new_task_id() -> str:
    return 'wt-' + secrets.token_hex(8)


def _state(turn: str, phone: dict) -> str:
    """A page task's state from its conversation turn and the phone tasks that turn started or updated."""
    if turn == 'pending' or any(status == 'running' for status in phone.values()):
        return 'running'
    if turn == 'failed' or any(status == 'failed' for status in phone.values()):
        return 'failed'
    if turn == 'interrupted' or any(status == 'cancelled' for status in phone.values()):
        return 'cancelled'
    return 'done'


class WorkspaceTasks:
    """Page tasks: one durable, server-minted ``taskId`` per accepted request, and its authoritative state
    (docs/APP-BUILDER.md §4.3). ``emit(task)`` receives every change, for the ``workspace_task`` event."""

    def __init__(self, root: Path, start, emit=None, clock=time.time):
        """``emit(task, device)`` receives every change with the device that asked for the task."""
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.root / 'workspace-tasks.sqlite3'
        fresh = not path.exists()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        if fresh:
            os.chmod(path, 0o600)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY, reply TEXT NOT NULL, '
                        'created REAL NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, '
                        'action_id TEXT NOT NULL, turn TEXT NOT NULL, phone TEXT NOT NULL, state TEXT NOT NULL, '
                        'detail TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, device TEXT NOT NULL)')
        self.lock = threading.RLock()
        self.start, self.emit, self.clock = start, emit, clock
        self._recover()

    def close(self):
        self.db.close()

    # Requests.

    def submit(self, device: str, body) -> dict:
        """The v1 reply for one request (refusals included); never raises for a bad body."""
        reason = check(body)
        if reason:
            return {'schemaVersion': 1, 'status': 'refused', 'reason': reason}
        key = f'{device}\n{body["requestId"]}'
        with self.lock:  # one request at a time, so a retry never starts a second task
            row = self.db.execute('SELECT reply FROM requests WHERE key=?', (key,)).fetchone()
            if row:
                return {**json.loads(row[0]), 'status': 'duplicate'}
            task_id, now = new_task_id(), self.clock()
            reply = {'schemaVersion': 1, 'status': 'accepted', 'taskId': task_id}
            # Recorded before starting, so the turn's own events always find their task; removed again if nothing
            # could be started, so the phone may retry.
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('INSERT INTO requests VALUES (?,?,?)', (key, json.dumps(reply), now))
            self.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (task_id, body['requestId'], body['actionId'], 'pending', '{}', 'running', '', now, now,
                             device))
            self.db.execute('COMMIT')
            try:
                self.start(body['label'].strip(), body['goal'].strip(), body['requestId'], task_id)
            except BaseException as error:
                self.db.execute('DELETE FROM requests WHERE key=?', (key,))
                self.db.execute('DELETE FROM tasks WHERE task_id=?', (task_id,))
                if isinstance(error, Unavailable):
                    return {'schemaVersion': 1, 'status': 'unavailable'}  # nothing started; it may retry
                raise
            self._emit(task_id)
            return reply

    # State, reported by the conversation (conversation_starter wires these to the conductor).

    def attach(self, task_id: str, phone_task_id, status: str = 'running'):
        """A phone task this page task's turn started or updated."""
        self._change(task_id, phone={str(phone_task_id): status})

    def phone_task(self, phone_task_id, status: str):
        """Any phone task changed; only page tasks that attached it are affected."""
        if status not in ('running',) + PHONE_FINAL:
            return
        key = str(phone_task_id)
        with self.lock:
            rows = self.db.execute("SELECT task_id, phone FROM tasks WHERE state='running'").fetchall()
            for task_id, phone in rows:
                if key in json.loads(phone):
                    self._change(task_id, phone={key: status})

    def turn_ended(self, task_id: str, status: str):
        """The page task's conversation turn ended: completed, interrupted or failed."""
        turn = {'completed': 'completed', 'interrupted': 'interrupted'}.get(status, 'failed')
        self._change(task_id, turn=turn)

    def _change(self, task_id, turn=None, phone=None):
        with self.lock:
            row = self.db.execute('SELECT turn, phone, state, updated FROM tasks WHERE task_id=?',
                                  (task_id,)).fetchone()
            if row is None or row[2] in FINAL:  # a final state never changes
                return
            current_turn, current_phone = row[0], json.loads(row[1])
            if turn is not None and current_turn == 'pending':
                current_turn = turn
            if phone:
                for key, status in phone.items():
                    if current_phone.get(key) not in PHONE_FINAL:
                        current_phone[key] = status
            state = _state(current_turn, current_phone)
            self.db.execute('UPDATE tasks SET turn=?, phone=?, state=?, detail=?, updated=? WHERE task_id=?',
                            (current_turn, json.dumps(current_phone), state, DETAIL.get(state, ''),
                             max(row[3], self.clock()), task_id))  # updatedAt never goes back for a task
        self._emit(task_id)

    def _recover(self):
        """After a restart nothing is still being tracked: a page task that had not ended is uncertain."""
        with self.lock:
            rows = self.db.execute("SELECT task_id FROM tasks WHERE state='running'").fetchall()
            self.db.execute("UPDATE tasks SET state='uncertain', detail=?, updated=MAX(updated, ?) "
                            "WHERE state='running'", (DETAIL['uncertain'], self.clock()))
        for (task_id,) in rows:
            self._emit(task_id)

    # Reads.

    def get(self, task_id, device=None) -> dict | None:
        """The task's state; with ``device``, only if that device asked for it (another device's task is unknown)."""
        found = self._get(task_id)
        if found is None or (device is not None and found[1] != device):
            return None
        return found[0]

    def _get(self, task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r'wt-[0-9a-f]{16}', task_id):
            return None
        with self.lock:
            row = self.db.execute('SELECT task_id, request_id, action_id, phone, state, detail, created, updated, '
                                  'device FROM tasks WHERE task_id=?', (task_id,)).fetchone()
        if row is None:
            return None
        phone = json.loads(row[3])
        shown = sorted(phone.items(), key=lambda kv: (len(kv[0]), kv[0]))[-PHONE_TASKS_SHOWN:]  # the newest
        task = {'schemaVersion': 1, 'taskId': row[0], 'requestId': row[1], 'actionId': row[2], 'state': row[4],
                'detail': row[5][:200], 'phoneTasks': [{'id': int(k) if k.isdigit() else k[:128], 'status': v}
                                                      for k, v in shown],
                'createdAt': int(row[6]), 'updatedAt': int(row[7])}
        return task, row[8]

    def recent(self, device=None, limit: int = RECENT) -> list:
        """The most recent page tasks, oldest first; with ``device``, only that device's."""
        with self.lock:
            if device is None:
                rows = self.db.execute('SELECT task_id FROM tasks ORDER BY updated DESC, rowid DESC LIMIT ?',
                                       (limit,)).fetchall()
            else:
                rows = self.db.execute('SELECT task_id FROM tasks WHERE device=? ORDER BY updated DESC, rowid DESC '
                                       'LIMIT ?', (device, limit)).fetchall()
        return [t for t in (self.get(r[0]) for r in reversed(rows)) if t]

    def _emit(self, task_id):
        if self.emit is None:
            return
        found = self._get(task_id)
        if found is not None:
            try:
                self.emit(*found)
            except Exception:
                pass  # delivery is best effort; the GET route and reconnect snapshots are authoritative


PREFIX = '[A Rose task the owner confirmed on the workspace page] '


def conversation_starter(conductor):
    """start() for a CodexConductor: the confirmed task becomes the owner's own request in the conversation, so it
    runs with exactly the conversation's tools and policy (phone work through phone_task). The turn is bound to the
    page task's ``taskId``: the conductor reports the phone tasks that turn starts or updates, and how the turn ends
    (``connect``). While Rose is replying it is refused as unavailable (the phone retries after a 503) rather than
    cutting her off."""
    def start(label: str, goal: str, request_id: str, task_id: str):
        session = getattr(conductor, 'sessions', {}).get('conversation')
        stopped = getattr(conductor, '_stopped', None)
        if session is None or (stopped is not None and stopped.is_set()):
            raise Unavailable()
        if session.busy or session.utterance is not None:
            raise Unavailable()
        conductor.inject_text_turn(f'{PREFIX}{label}. {goal}', workspace_task=task_id)
    return start


def connect(conductor, tasks: WorkspaceTasks) -> WorkspaceTasks:
    """Wire page tasks to a conductor: requests start conversation turns, the conductor reports back to ``tasks``,
    and every change is a ``workspace_task`` event on the bus (sent to the phone with the other events)."""
    tasks.start = conversation_starter(conductor)
    tasks.emit = lambda task, device: conductor.bus.emit('workspace_task', task=task, device=device)
    conductor.workspace_tasks = tasks
    return tasks
