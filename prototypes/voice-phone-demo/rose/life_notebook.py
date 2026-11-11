"""Rosie's immutable server notes. No owner notes, paths, or network access."""
from __future__ import annotations

import contextlib
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

NAMESPACE = 'rosie-server-life-v1'


class Notebook:
    def __init__(self, directory, *, clock=time.time):
        self.directory, self.clock = Path(directory), clock
        if self.directory.is_symlink():
            raise OSError('Notebook directory must not be a symlink')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.stat().st_uid != os.getuid():
            raise OSError('Notebook directory has another owner')
        self.directory.chmod(0o700)
        self.path = self.directory / 'notebook.sqlite3'
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise OSError('Notebook must be an owned regular file')
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS notes (task_id TEXT PRIMARY KEY, title TEXT NOT NULL, '
                       'body TEXT NOT NULL, sha256 TEXT NOT NULL, saved_at REAL NOT NULL)')

    @contextlib.contextmanager
    def _db(self):
        if self.path.is_symlink():
            raise OSError('Notebook must not be a symlink')
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _id(task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', task_id):
            raise ValueError('Invalid note task identity')

    def read(self, task_id):
        self._id(task_id)
        with self._db() as db:
            row = db.execute('SELECT * FROM notes WHERE task_id=?', (task_id,)).fetchone()
        if row is None:
            return None
        note = dict(row)
        if (not isinstance(note['body'], str) or not 0 < len(note['body']) <= 16000
                or not isinstance(note['title'], str) or not 0 < len(note['title']) <= 300
                or not isinstance(note['saved_at'], (int, float)) or not math.isfinite(note['saved_at'])
                or hashlib.sha256(note['body'].encode('utf-8')).hexdigest() != note['sha256']):
            raise ValueError('Notebook readback is corrupt')
        return dict(note, version=1, namespace=NAMESPACE, storage='server', verified=True,
                    note_id='life-'+task_id, content_is_untrusted=True)

    def put(self, task_id, title, body, check=lambda: None):
        self._id(task_id)
        for value, maximum in [(title, 300), (body, 16000)]:
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError('Invalid note text')
            value.encode('utf-8', errors='strict')
        digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
        check()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM notes WHERE task_id=?', (task_id,)).fetchone()
            if old is not None:
                if (old['title'], old['body'], old['sha256']) != (title, body, digest):
                    raise ValueError('Immutable note conflict')
            else:
                if db.execute('SELECT COUNT(*) FROM notes').fetchone()[0] >= 256:
                    raise ValueError('Notebook is full')
                check()
                db.execute('INSERT INTO notes VALUES (?,?,?,?,?)', (task_id, title, body, digest, self.clock()))
            check()  # a cancellation before commit rolls the transaction back
        return self.read(task_id)
