"""Durable, redacted conversational events; never copy Codex's native rollouts.

SQLite commits each event before the caller continues. No automatic retention.
The CLI and loopback API export the same records, including interrupted runs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

PRIVATE = re.compile(r'reasoning|thought|audio|image|screenshot|recording|authorization|cookie|password|secret|token|pairing|private.?key', re.I)
SECRET = re.compile(r'''(?ix)\b(?:bearer\s+[^\s"']+|sk-[\w-]{8,}|(?:api[_ -]?key|password|secret|token|pairing[_ -]?code)["']?\s*[=:]\s*(?:"[^"]*"|'[^']*'|[^\s,}\]&]+))''')
PAIRING = re.compile(r'roseagent://[^\s"\'<>]+', re.I)
MEDIA = re.compile(r'data:(?:image|audio|video)/[^\s"\']+', re.I)
PEM = re.compile(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', re.S)
THINK = re.compile(r'<think>.*?(?:</think>|$)', re.S | re.I)


def redact(value):
    if isinstance(value, dict):
        return {str(k): redact(v) for k, v in value.items() if not PRIVATE.search(str(k))}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return SECRET.sub('[redacted]', PAIRING.sub('[pairing omitted]', MEDIA.sub('[media omitted]', PEM.sub('[redacted]', THINK.sub('', value)))))
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return '[unsupported content omitted]'


class Transcripts:
    def __init__(self, directory=None):
        self.directory = Path(directory or os.environ.get('ROSE_TRANSCRIPT_DIR') or
                              Path.home() / '.local/share/rose/transcripts').expanduser()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'conversations.sqlite3'
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, created TEXT, model TEXT);
          CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, conversation TEXT, session TEXT,
            thread TEXT, turn TEXT, timestamp TEXT, model TEXT, kind TEXT, data TEXT);
          CREATE INDEX IF NOT EXISTS events_conversation ON events(conversation, seq);
        ''')

    def new(self, model):
        cid = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute('INSERT INTO conversations VALUES(?,?,?)', (cid, self.now(), model))
        return cid

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def append(self, conversation, session, kind, data, *, thread=None, turn=None, model='gpt-6.1-terra'):
        with self.lock, self.db:
            self.db.execute('INSERT INTO events(conversation,session,thread,turn,timestamp,model,kind,data) '
                            'VALUES(?,?,?,?,?,?,?,?)',
                            (conversation, session, thread, turn, self.now(), model, kind,
                             json.dumps(redact(data), ensure_ascii=False)))

    def conversations(self):
        with self.lock:
            rows = self.db.execute('SELECT id,created,model FROM conversations ORDER BY created DESC').fetchall()
        return [dict(zip(('id', 'created', 'model'), row)) for row in rows]

    def export(self, cid):
        with self.lock:
            rows = self.db.execute('SELECT seq,session,thread,turn,timestamp,model,kind,data '
                                   'FROM events WHERE conversation=? ORDER BY seq', (cid,)).fetchall()
        return [dict(zip(('seq', 'session_id', 'thread_id', 'turn_id', 'timestamp', 'model', 'kind'), row[:7]),
                     conversation_id=cid, data=json.loads(row[7])) for row in rows]

    def close(self):
        with self.lock:
            self.db.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory')
    p.add_argument('--conversation', help='export this conversation as JSONL; otherwise list conversations')
    a = p.parse_args()
    store = Transcripts(a.directory)
    try:
        for row in store.export(a.conversation) if a.conversation else store.conversations():
            print(json.dumps(row, ensure_ascii=False))
    finally:
        store.close()


if __name__ == '__main__':
    main()
