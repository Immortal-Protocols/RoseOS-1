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


class WorkspaceTasks:
    def __init__(self, root: Path, start):
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
        self.lock = threading.Lock()
        self.start = start

    def close(self):
        self.db.close()

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
            try:
                task_id = self.start(body['label'].strip(), body['goal'].strip(), body['requestId'])
            except Unavailable:
                return {'schemaVersion': 1, 'status': 'unavailable'}  # nothing started; not recorded, so it may retry
            reply = {'schemaVersion': 1, 'status': 'accepted', 'taskId': task_id}
            # Recorded after starting: if this write fails the task still runs, and the phone sees it in the task
            # events; the request is never started twice from here because the lock is held throughout.
            self.db.execute('INSERT INTO requests VALUES (?,?,?)', (key, json.dumps(reply), time.time()))
            return reply


PREFIX = '[A Rose task the owner confirmed on the workspace page] '


def conversation_starter(conductor):
    """start() for a CodexConductor: the confirmed task becomes the owner's own request in the conversation, so it
    runs with exactly the conversation's tools and policy (phone work through phone_task) and its progress arrives as
    the usual task events. While Rose is replying, it is refused as unavailable (the phone retries after a 503)
    rather than cutting her off. The task id is not known yet: the conversation decides whether and how to start
    phone work, so the reply's taskId is None and the page follows the task events."""
    def start(label: str, goal: str, request_id: str):
        session = getattr(conductor, 'sessions', {}).get('conversation')
        stopped = getattr(conductor, '_stopped', None)
        if session is None or (stopped is not None and stopped.is_set()):
            raise Unavailable()
        if session.busy or session.utterance is not None:
            raise Unavailable()
        conductor.inject_text_turn(f'{PREFIX}{label}. {goal}')
        return None
    return start
