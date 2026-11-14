"""The Codex app writer for the builder (docs/APP-BUILDER.md §4A); source only and enabled nowhere.

``CodexGenerator`` is the Builder's ``generate(source, kind, request, cancelled)``. Each job opens one ``app_build``
session on its own bridge process (``codex-voice-bridge.mjs``, the existing and only Codex adapter):

- an ephemeral thread whose cwd is the project's ``source`` directory, with fixed instructions, no tools, web search
  or network, and writes limited to that directory by Codex's workspace-write sandbox;
- one turn, bounded by ``timeout_s`` and stopped on cancel;
- returned only after the bridge confirms the Codex process exited, so nothing can still be writing when the builder
  checks and commits the source. An unconfirmed stop is a failure, never a success.

What it does not do: it does not keep the writer from *reading* the server's files. That needs the writer to run as
a separate OS user or in a container that mounts only the project source (``command`` below), which belongs to the
Server Worker (APP-BUILDER.md §5, blocker 3). Until then no host should construct this.
"""
from __future__ import annotations

import queue
import time
import uuid
from pathlib import Path

from .toolchain import StepFailed

MODEL = 'gpt-6.1-terra'
NOTE_MAX = 1200
# Bridge open errors after which the bridge itself confirmed the Codex process stopped.
CLEAN_OPEN_ERRORS = {'codex_request_failed', 'chatgpt_login_required', 'model_unavailable', 'fast_unavailable',
                     'model_mode_mismatch', 'research_isolation_unavailable'}
OPEN_MESSAGES = {
    'chatgpt_login_required': 'The app writer needs the server signed in to ChatGPT.',
    'model_unavailable': 'The app writer model is not available to this account.',
    'fast_unavailable': 'The app writer model is not available to this account.',
    'model_mode_mismatch': 'The app writer model is not available to this account.',
    'research_isolation_unavailable': 'The app writer could not confirm it is isolated, so it did not start.',
}


def prompt(kind: str, request: str) -> str:
    """The owner's request as the turn's text. The policy itself is fixed by the bridge."""
    if kind == 'create':
        return 'Write this new app (the skeleton in the current directory is its starting point).\n\n' + request
    if kind == 'revise':
        return ('Change the existing app in the current directory as the owner asks, keeping their data.\n\n'
                + request)
    raise StepFailed('internal', 'Unknown build kind.')


class CodexGenerator:
    def __init__(self, timeout_s: float = 900.0, model: str = MODEL, command: list[str] | None = None, connect=None):
        """``command`` is the bridge command line (for example one that runs it as a separate OS user); ``connect``
        replaces the client for tests: connect(on_event) -> object with request(op, session, **params) and close()."""
        self.timeout_s, self.model = timeout_s, model
        if connect is None:
            from ..codex_client import CodexClient
            connect = lambda on_event: CodexClient(on_event, command)
        self.connect = connect
        self.last_note = ''

    def __call__(self, source: Path, kind: str, request: str, cancelled) -> str:
        text = prompt(kind, request)
        events: queue.Queue = queue.Queue()
        session = 'app-build-' + uuid.uuid4().hex
        client = self.connect(events.put)
        opened = False  # whether a Codex process may exist for this session
        failure = None
        note = ''
        try:
            try:
                opened = True
                reply = client.request('open', session, _timeout=60, cwd=str(Path(source).resolve()),
                                       model=self.model, tools=[], purpose='app_build')
            except RuntimeError as error:
                code = str(error)
                opened = code not in CLEAN_OPEN_ERRORS
                raise StepFailed('internal', OPEN_MESSAGES.get(code, 'The app writer could not start.'),
                                 retryable=True) from None
            if reply.get('model') != self.model:
                raise StepFailed('internal', 'The app writer started with the wrong model.', retryable=True)
            client.request('submit', session, _timeout=60, text=text)
            deadline = time.monotonic() + self.timeout_s
            while True:
                if cancelled():
                    raise StepFailed('cancelled', 'Cancelled.')
                if time.monotonic() > deadline:
                    raise StepFailed('timeout', f'Writing the app took longer than {int(self.timeout_s)} s.',
                                     retryable=True)
                try:
                    e = events.get(timeout=0.2)
                except queue.Empty:
                    continue
                if e.get('event') == 'bridge_failure':
                    raise StepFailed('internal', 'The app writer stopped unexpectedly.', retryable=True)
                if e.get('session') != session:
                    continue
                if e.get('event') == 'message' and isinstance(e.get('text'), str):
                    note = e['text'].strip()[:NOTE_MAX]
                elif e.get('event') == 'failure':
                    raise StepFailed('internal', 'The app writer stopped before finishing.', retryable=True)
                elif e.get('event') == 'done':
                    if (e.get('turn') or {}).get('status') != 'completed':
                        raise StepFailed('internal', 'The app writer stopped before finishing.', retryable=True)
                    self.last_note = note
                    return note
        except StepFailed as error:
            failure = error
            raise
        except Exception:
            failure = StepFailed('internal', 'The app writer failed.', retryable=True)
            raise failure from None
        finally:
            stopped = self._stop(client, session, opened)
            if not stopped:
                # Something may still be writing into the source: never let the builder use it.
                raise StepFailed('internal', 'The app writer did not confirm it stopped.') from failure

    @staticmethod
    def _stop(client, session: str, opened: bool) -> bool:
        stopped = not opened
        if opened:
            try:
                stopped = client.request('close', session, _timeout=15).get('stopped') is True
            except Exception:
                stopped = False
        try:
            exited = client.close()
        except Exception:
            exited = False
        # A bridge that exited cleanly confirmed every app_build session it held had stopped (exit code 0).
        return stopped or exited is True
