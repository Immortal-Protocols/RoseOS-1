"""Rose server's local layout model, owned child process, no extra listener or conversation-server restart."""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import subprocess
import threading
import time

MAX_BODY = 64 * 1024
MAX_REPLY = 32 * 1024

def failed(request, category):
    rid = request.get('requestId') if isinstance(request, dict) else None
    return {'contract': 'rose-layout-v1', 'requestId': rid if isinstance(rid, str) else None,
            'status': 'failed', 'failure': category}

class WorkspaceModel:
    """One warm local worker. Calls serialize without an unbounded queue. Only its own child may be stopped."""
    def __init__(self, config: Path):
        data = json.loads(Path(config).read_text())
        self.command = data['command']
        if not isinstance(self.command, list) or not self.command or not all(isinstance(s, str) and s for s in self.command):
            raise ValueError('Invalid workspace worker command')
        self.lock = threading.Lock()
        self.process = None
        self.buffer = b''
        self.closed = False
        self.ready = threading.Event()
        self._loading_lock = threading.Lock()
        self._loader = None

    @classmethod
    def configured(cls):
        configured = os.environ.get('ROSE_WORKSPACE_MODEL_CONFIG')
        path = Path(configured) if configured else Path(__file__).resolve().parents[3] / '.tools/local-composer/worker.json'
        if not configured and not path.is_file(): return None
        try: return cls(path)
        except (OSError, ValueError, KeyError, TypeError): return UnavailableWorkspaceModel()

    def _stop(self):
        self.ready.clear()
        child, self.process = self.process, None
        self.buffer = b''
        if child is None: return
        if child.poll() is None:
            child.terminate()
            try: child.wait(timeout=2)
            except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=2)
        for pipe in (child.stdin, child.stdout):
            if pipe: pipe.close()

    def _line(self, deadline, cancelled):
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while b'\n' not in self.buffer:
                if cancelled(): raise InterruptedError()
                left = deadline - time.monotonic()
                if left <= 0: raise TimeoutError()
                if not selector.select(min(left, 0.05)): continue
                chunk = os.read(self.process.stdout.fileno(), 4096)
                if not chunk: raise OSError('worker exited')
                self.buffer += chunk
                if len(self.buffer) > MAX_REPLY: raise ValueError('worker reply too large')
        line, self.buffer = self.buffer.split(b'\n', 1)
        return json.loads(line)

    def _start(self, cancelled):
        if self.process is not None and self.process.poll() is None: return
        self._stop()
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, bufsize=0, start_new_session=True)
        os.set_blocking(self.process.stdin.fileno(), False)
        # Load once, independently of the per-composition deadline. No prompt is sent before ready.
        ready = self._line(time.monotonic() + 60, cancelled)
        if not isinstance(ready, dict) or ready.get('status') != 'ready': raise ValueError('worker unavailable')

    def preload(self):
        """Start loading once, independently of any HTTP request or disconnect."""
        with self._loading_lock:
            if self.closed or self.ready.is_set() or (self._loader and self._loader.is_alive()): return
            self._loader = threading.Thread(target=self._load, name='workspace-model-load', daemon=True)
            self._loader.start()

    def _load(self):
        loaded = False
        with self.lock:
            if self.closed: return
            try:
                self._start(lambda: self.closed)
                if self.closed: self._stop()
                else: loaded = True
            except Exception:
                self._stop()
        with self._loading_lock:
            self._loader = None
            if loaded and not self.closed: self.ready.set()

    def wait_ready(self, timeout_s):
        """For setup/tests that explicitly wait for preload; compose itself never waits for a cold load."""
        return self.ready.wait(timeout_s)

    def compose(self, request, cancelled=lambda: False):
        if cancelled(): return failed(request, 'cancelled')
        if self.closed: return failed(request, 'model_unavailable')
        try: raw = (json.dumps(request, ensure_ascii=True, separators=(',', ':')) + '\n').encode()
        except (ValueError, TypeError, RecursionError): return failed(request, 'bad_request')
        if len(raw) > MAX_BODY: return failed(request, 'bad_request')
        if not self.ready.is_set():
            self.preload()
            return failed(request, 'model_unavailable')
        if not self.lock.acquire(blocking=False): return failed(request, 'model_unavailable')
        sent = False
        try:
            cancel_requested = cancelled
            cancelled = lambda: self.closed or cancel_requested()
            if self.process is None or self.process.poll() is not None:
                self._stop()
                return failed(request, 'model_unavailable')
            # The worker validates requested limits (cat-1 <= 4 seconds); leave transport headroom.
            deadline = time.monotonic() + 5
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdin, selectors.EVENT_WRITE)
                remaining = memoryview(raw)
                while remaining:
                    if cancelled(): raise InterruptedError()
                    left = deadline - time.monotonic()
                    if left <= 0: raise TimeoutError()
                    if selector.select(min(left, 0.05)):
                        try:
                            count = os.write(self.process.stdin.fileno(), remaining)
                            sent = sent or count > 0
                            remaining = remaining[count:]
                        except BlockingIOError: continue
            reply = self._line(deadline, cancelled)
            if (not isinstance(reply, dict) or reply.get('contract') != 'rose-layout-v1'
                    or reply.get('requestId') != (request.get('requestId') if isinstance(request, dict) else None)
                    or reply.get('status') not in ('ok', 'failed')): raise ValueError('worker protocol')
            return reply
        except InterruptedError:
            if sent: self._stop()
            return failed(request, 'cancelled')
        except TimeoutError:
            self._stop(); return failed(request, 'timeout')
        except Exception:
            self._stop(); return failed(request, 'model_unavailable')
        finally:
            self.lock.release()
            if not self.closed and not self.ready.is_set(): self.preload()

    def close(self):
        self.closed = True
        with self.lock: self._stop()


class UnavailableWorkspaceModel:
    """Bad optional model configuration cannot prevent Rose's conversation server from starting."""
    def compose(self, request, cancelled=lambda: False): return failed(request, 'model_unavailable')
    def preload(self): pass
    def close(self): pass
