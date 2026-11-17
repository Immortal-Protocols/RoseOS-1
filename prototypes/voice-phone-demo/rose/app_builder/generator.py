"""The Codex app writer for the builder (docs/APP-BUILDER.md §4A); source only and enabled nowhere.

``CodexGenerator`` is the Builder's ``generate(source, kind, request, cancelled)``. Each job opens one ``app_build``
session on its own bridge process (``codex-voice-bridge.mjs``, the existing and only Codex adapter):

- an ephemeral thread whose cwd is the project's ``source`` directory, with fixed instructions, no tools, web search
  or network, and writes limited to that directory by Codex's workspace-write sandbox;
- one turn, bounded by ``timeout_s`` and stopped on cancel;
- returned only after the bridge confirms the writer's whole process group is gone (an unconfirmed stop is a failure,
  never a success). A process that left the group can still change the source afterwards, so the Builder checks,
  records and builds only a private snapshot taken after this returns, and resets the source before the next job.

What it does not do: it does not keep the writer from *reading* the server's files. That needs the writer to run as
a separate OS user or in a container that mounts only the project source (``command`` below), which belongs to the
Server Worker (APP-BUILDER.md §5, blocker 3). Until then no host should construct this.
"""
from __future__ import annotations

import json
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


CLIENT_SERVER = ('\n\nThis builder builds client-server apps (rose-app.json, contract/ and server/), as your '
                 'instructions describe.')
BACKEND = ('\n\nThis builder sets up the app\'s backend in its own Chariot agent container (client-backend-v1), as '
           'your instructions describe, with the agent_container tool; the release folder for this build is {folder}.')
TOOL_OUTPUT_MAX = 64 * 1024  # each of stdout and stderr, its last bytes, back to the writer
TOOL = 'agent_container'


def prompt(kind: str, request: str, libraries=(), client_server: bool = False, backend_folder: str | None = None) -> str:
    """The owner's request as the turn's text, with the libraries this builder provides (declared in
    dependencies.json, never shipped) and, when it can build them, that it builds client-server apps: the policy
    (fixed by the bridge, the same for every job) offers one only when the turn says so."""
    provided = ('\n\nPure-Java libraries this builder provides (declare in dependencies.json only if needed): '
                + ', '.join(libraries) + '.') if libraries else ''
    if client_server:
        provided += CLIENT_SERVER
    if backend_folder is not None:
        provided += BACKEND.format(folder=backend_folder)
    if kind == 'create':
        return ('Write this new app (the skeleton in the current directory is its starting point).' + provided
                + '\n\n' + request)
    if kind == 'revise':
        return ('Change the existing app in the current directory as the owner asks, keeping their data.' + provided
                + '\n\n' + request)
    raise StepFailed('internal', 'Unknown build kind.')


class CodexGenerator:
    def __init__(self, timeout_s: float = 900.0, model: str = MODEL, command: list[str] | None = None, connect=None,
                 libraries=None, client_server: bool = False, backend: bool = False, tool_timeout_s: int = 1800):
        """``command`` is the bridge command line (for example one that runs it as a separate OS user); ``connect``
        replaces the client for tests: connect(on_event) -> object with request(op, session, **params) and close().
        ``libraries`` lists the coordinates the builder's library store provides (its ``available``), if any.
        ``client_server`` says the builder can build a client-server app (a host sets it only when it injects
        the Builder's code generator, server isolation, Rust toolchain and builder crates); the turn then says so."""
        self.timeout_s, self.model = timeout_s, model
        self.libraries = libraries
        self.client_server = client_server
        # §7.7a: the writer also sets the app's backend up in its agent container, through one host-run tool the
        # Builder answers (BackendSession); the writer itself still has no network, credentials or other tools.
        self.backend, self.tool_timeout_s = backend, int(tool_timeout_s)
        if connect is None:
            from ..codex_client import CodexClient
            connect = lambda on_event: CodexClient(on_event, command)
        self.connect = connect
        self.last_note = ''

    def __call__(self, source: Path, kind: str, request: str, cancelled, backend=None) -> str:
        available = self.libraries() if callable(self.libraries) else self.libraries
        text = prompt(kind, request, sorted(available or ()), self.client_server,
                      backend.folder if backend is not None else None)
        container = {'containerTool': True, 'toolTimeoutS': self.tool_timeout_s} if backend is not None else {}
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
                                       model=self.model, tools=[], purpose='app_build', **container)
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
                if e.get('event') == 'tool':
                    client.send({'op': 'tool_result', 'callId': e.get('callId'),
                                 'result': self._tool(backend, e.get('params'))})
                elif e.get('event') == 'message' and isinstance(e.get('text'), str):
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
                # The writer's processes may still be running: fail the job rather than build next to them.
                raise StepFailed('internal', 'The app writer did not confirm it stopped.') from failure

    def _tool(self, backend, params) -> dict:
        """One ``agent_container`` call: run in the job's own container (BackendSession), its exit code and output
        back to the writer only (never logged, never in the job). Anything else is refused."""
        def answer(text: str, ok: bool = True) -> dict:
            return {'success': ok, 'contentItems': [{'type': 'inputText', 'text': text}]}
        if backend is None or not isinstance(params, dict) or params.get('tool') != TOOL:
            return answer('Tool unavailable.', False)
        args = params.get('arguments')
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = None
        if not isinstance(args, dict) or set(args) - {'command', 'stdin'} or not isinstance(args.get('command'), str) \
                or not args['command'] or not isinstance(args.get('stdin', ''), str):
            return answer('agent_container takes {command, stdin?}, both text.', False)
        from .app_host import SetupFailed
        try:
            done = backend.exec(args['command'], args.get('stdin', '').encode('utf-8'), self.tool_timeout_s)
        except SetupFailed as failed:
            return answer(f'The agent container could not be reached ({failed.step}).', False)
        out = done.stdout[-TOOL_OUTPUT_MAX:]
        err = done.stderr[-TOOL_OUTPUT_MAX:]
        return answer(f'exit {done.code}\n--- stdout ---\n{out}\n--- stderr ---\n{err}')

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
