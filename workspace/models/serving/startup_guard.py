#!/usr/bin/env python3
"""Pre-download startup guard for the Muse Glimmer serving container.

Runs before `vllm serve`, inside the container. It reads only what the node reports: driver
version, the GPUs CUDA sees with their total and free memory, free disk at the weights cache, and
/dev/shm. It prints one JSON evidence line, then either execs the server or exits 3, so a node
that cannot fit the model fails before it downloads 59.6 GB. It never reads environment values
or credentials.

Usage: startup_guard.py '<limits json>' -- vllm serve <args...>

With "status_path" in the limits (the persistent service), the guard writes its own result there as failure codes,
then execs the server exactly as without it: the server's output goes straight to the container log.

Supervision is explicit, default OFF and one attempt only: it needs both "supervise" bounds in the limits and a
one-shot token file "supervise-once" next to the status file, which the guard consumes (renames) before it starts.
Supervised, the guard reads the server's output in chunks of at most 64 KiB, passes every byte through, and inspects
at most "max_bytes" (2 MiB) within "max_seconds" (10 minutes); after either bound it only passes output through.
Inspection keeps fixed category names (CATEGORIES), never text. Signals are forwarded and the guard exits with the
server's own status (128+N for signal N).
"""
import json
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

EXIT_REFUSED = 3


def evaluate(facts, limits):
    """Pure check of reported facts against limits; returns failure strings (empty means proceed)."""
    failures = []
    if facts.get('cuda_error'):
        failures.append(f"CUDA unavailable: {facts['cuda_error']}")
    driver = facts.get('driver_version')
    if driver is None:
        failures.append('driver version not reported')
    elif int(driver.split('.')[0]) < limits['driver_major_min']:
        failures.append(f"driver {driver} below {limits['driver_major_min']}")
    gpus = facts.get('gpus') or []
    if len(gpus) != limits['gpu_count']:
        failures.append(f"{len(gpus)} GPUs visible, need {limits['gpu_count']}")
    for i, gpu in enumerate(gpus):
        if gpu['total'] < limits['gpu_total_min_bytes']:
            failures.append(f"GPU{i} reports {gpu['total']} total bytes, need {limits['gpu_total_min_bytes']}")
        if gpu['free'] < gpu['total']*limits['gpu_memory_utilization']:
            failures.append(f"GPU{i} has {gpu['free']} free bytes, below utilization {limits['gpu_memory_utilization']} of total")
    if facts.get('cache_free') is None or facts['cache_free'] < limits['cache_free_min_bytes']:
        failures.append(f"cache free {facts.get('cache_free')} bytes, need {limits['cache_free_min_bytes']}")
    if facts.get('shm_total') is None or facts['shm_total'] < limits['shm_min_bytes']:
        failures.append(f"/dev/shm {facts.get('shm_total')} bytes, need {limits['shm_min_bytes']}")
    return failures


def _driver_version(path='/proc/driver/nvidia/version'):
    try:
        with open(path) as f:
            match = re.search(r'Kernel Module\s+(?:for \S+\s+)?(\d+\.\d+(?:\.\d+)?)', f.read())
            return match.group(1) if match else None
    except OSError:
        return None


def _existing(path):
    while not os.path.exists(path):
        path = os.path.dirname(path)
    return path


def collect(cache_dir):
    facts = {'driver_version': _driver_version(), 'gpus': [], 'cache_dir': cache_dir}
    try:
        import torch
        for i in range(torch.cuda.device_count()):
            free, total = torch.cuda.mem_get_info(i)
            facts['gpus'].append({'name': torch.cuda.get_device_name(i), 'total': total, 'free': free})
    except Exception as e:  # an old driver surfaces here as a CUDA initialisation error
        facts['cuda_error'] = f'{type(e).__name__}: {e}'[:300]
    facts['cache_free'] = shutil.disk_usage(_existing(cache_dir)).free
    try:
        facts['shm_total'] = shutil.disk_usage('/dev/shm').total
    except OSError:
        facts['shm_total'] = None
    return facts


CODES = (('CUDA unavailable', 'cuda_unavailable'), ('driver version not reported', 'driver_unknown'),
         ('driver ', 'driver_too_old'), ('GPUs visible', 'gpu_count'), ('total bytes', 'gpu_total'),
         ('free bytes', 'gpu_free'), ('cache free', 'cache_free'), ('/dev/shm', 'shm'))
CHUNK, WINDOW = 1 << 16, 1024
# (category, pattern): fixed names for where a vLLM start or CLI run stopped. Matching runs over a bounded window and
# keeps only the category name, never the matched text.
CATEGORIES = (
    ('argument_rejected', re.compile(r'error: (argument |unrecognized arguments|the following arguments are required)'
                                     r'|invalid choice')),
    ('tool_parser_invalid', re.compile(r'(?i)invalid tool[ _-]call parser|tool[ _-]call[ _-]parser.{0,40}not (found|registered|supported)')),
    ('reasoning_parser_invalid', re.compile(r'(?i)invalid reasoning parser|reasoning[ _-]parser.{0,40}not (found|registered|supported)')),
    ('image_missing', re.compile(r'No such image|Unable to find image|pull access denied')),
    ('model_config_unavailable', re.compile(r'LocalEntryNotFoundError|OfflineModeIsEnabled|HFValidationError|'
                                            r'RepositoryNotFoundError|couldn.t connect to|Can.t load the configuration|'
                                            r'NameResolutionError|MaxRetryError')),
    ('device_unavailable', re.compile(r'Failed to infer device type|No CUDA GPUs are available|'
                                      r'CUDA driver initialization failed|Found no NVIDIA driver')),
    ('cuda_out_of_memory', re.compile(r'OutOfMemoryError|CUDA out of memory')),
    ('kv_cache_insufficient', re.compile(r'No available memory for the cache blocks|larger than the maximum number of tokens')),
    ('nccl_error', re.compile(r'NCCL error|ncclInternalError|ncclSystemError|ncclUnhandledCudaError')),
    ('import_error', re.compile(r'ModuleNotFoundError|ImportError')),
    ('traceback', re.compile(r'Traceback \(most recent call last\)')),
)
CATEGORY_NAMES = tuple(name for name, _ in CATEGORIES)

# Fixed vocabularies for the last traceback. Only these tokens can leave Capture: never a message, path, line number,
# stack or class string from the output. Anything not listed is `other`.
EXCEPTION_TYPES = (
    'RuntimeError', 'ValueError', 'TypeError', 'KeyError', 'IndexError', 'AttributeError', 'AssertionError',
    'NotImplementedError', 'OSError', 'FileNotFoundError', 'PermissionError', 'TimeoutError', 'ConnectionError',
    'ImportError', 'ModuleNotFoundError', 'MemoryError', 'SystemExit', 'KeyboardInterrupt', 'EOFError',
    'JSONDecodeError', 'OutOfMemoryError', 'DistBackendError', 'LocalEntryNotFoundError', 'EntryNotFoundError',
    'HfHubHTTPError', 'RepositoryNotFoundError', 'RevisionNotFoundError', 'GatedRepoError', 'HTTPError',
    'ReadTimeout', 'ConnectTimeout', 'ChunkedEncodingError', 'MaxRetryError', 'SafetensorError', 'ValidationError',
    'CalledProcessError', 'EngineDeadError', 'EngineGenerateError')
LOCATIONS = (  # (path fragment, component): the innermost frame of the last traceback, first match wins
    ('<string>', 'guard'),  # the guard runs as `python3 -c <source>`
    ('/vllm/model_executor/model_loader/', 'model_loader'), ('/vllm/model_executor/', 'model_executor'),
    ('/vllm/distributed/', 'distributed'), ('/vllm/v1/worker/', 'worker'), ('/vllm/worker/', 'worker'),
    ('/vllm/v1/engine/', 'engine_core'), ('/vllm/engine/', 'engine'), ('/vllm/v1/executor/', 'executor'),
    ('/vllm/executor/', 'executor'), ('/vllm/config', 'config'), ('/vllm/platforms/', 'platform'),
    ('/vllm/v1/core/', 'kv_cache'), ('/vllm/compilation/', 'compile'), ('/vllm/entrypoints/', 'entrypoint'),
    ('/vllm/transformers_utils/', 'hf_config'), ('/vllm/', 'vllm_other'), ('/huggingface_hub/', 'huggingface_hub'),
    ('/transformers/', 'transformers'), ('/torch/cuda/', 'torch_cuda'), ('/torch/distributed/', 'torch_distributed'),
    ('/torch/', 'torch'), ('/safetensors/', 'safetensors'), ('/requests/', 'requests'), ('/urllib3/', 'urllib3'),
    ('/multiprocessing/', 'multiprocessing'), ('/asyncio/', 'asyncio'))
LOCATION_NAMES = tuple(dict.fromkeys(c for _, c in LOCATIONS)) + ('other',)
PROCESSES = (('EngineCore', 'engine_core'), ('VllmWorker', 'worker'), ('Worker', 'worker'), ('APIServer', 'api_server'))
PROCESS_NAMES = ('main', 'engine_core', 'worker', 'api_server', 'other')
PREFIX = re.compile(r'^\(([A-Za-z_]{1,40})[^)\n]{0,80}\)\s+')  # vLLM's "(EngineCore_DP0 pid=7) " line prefix
# The rest of vLLM's log header (vllm/logger.py _FORMAT: "%(levelname)s %(asctime)s [%(fileinfo)s:%(lineno)d] "), which
# its NewLineFormatter repeats on every line of a multi-line record, tracebacks from logger.exception included.
HEADER = re.compile(r'^(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+\d\d-\d\d \d\d:\d\d:\d\d(?:[.,]\d+)?\s+\[[^\]\n]{1,200}\]\s?')
ANSI = re.compile(r'\x1b\[[0-9;]*m')  # ColoredFormatter's header colors
MAX_PROCESSES = 16  # traceback state is kept per line-prefix process, so interleaved processes do not mix
FRAME = re.compile(r'File "([^"\n]{1,600})", line \d+, in ')
EXCEPTION_LINE = re.compile(r'^([A-Za-z_][\w.]{0,100}(?:Error|Exception|Exit|Interrupt))(?::|$)')
GUARD_MARK, TAIL = '"guard": "muse-glimmer-startup"', 64
MAX_TRACEBACKS = 1000


class Capture:
    """Bounded inspection: at most max_bytes within max_seconds, matched over a sliding window of WINDOW bytes, so
    a line without newlines or an endless stream never grows memory. Keeps fixed category names only.

    The same bounded bytes are also read as lines, keeping at most WINDOW characters of a line's head and TAIL of its
    end (lines end at \\n or \\r), to classify with fixed vocabularies: whether the guard passed or refused, and for
    the first and the last classified traceback its exception type, innermost frame component and vLLM process. A
    vLLM process prefix and log header (repeated per line by its NewLineFormatter) are skipped, and traceback state
    is kept per process. The first exception is only the earliest one classified, not a proven root cause."""

    def __init__(self, max_bytes=2 << 20, max_seconds=600, clock=time.monotonic):
        self.max_bytes, self.deadline, self.clock = max_bytes, clock() + max_seconds, clock
        self.inspected, self.limit, self.first, self.seen, self.tail = 0, None, None, [], ''
        self.head, self.end, self.guard, self.tracebacks = '', '', None, 0
        self.first_exception, self.exception, self.state = None, None, {}  # state: process -> [in traceback, frame]

    def feed(self, chunk):
        """Inspect what fits in the bounds; returns False once inspection has stopped."""
        if self.limit:
            return False
        if self.clock() >= self.deadline:
            self.limit = 'time'
            return False
        room = self.max_bytes - self.inspected
        part = chunk[:room]
        self.inspected += len(part)
        decoded = part.decode('utf-8', 'replace')
        text = self.tail + decoded
        for name, pattern in CATEGORIES:
            if name not in self.seen and pattern.search(text):
                self.seen.append(name)
                if self.first is None and name != 'traceback':
                    self.first = name
        self.tail = text[-WINDOW:]
        self.lines(decoded)
        if len(chunk) > room:
            self.limit = 'bytes'
            return False
        return True

    def lines(self, text):
        pieces = re.split(r'[\r\n]', text)
        for i, piece in enumerate(pieces):
            if len(self.head) < WINDOW:
                self.head += piece[:WINDOW - len(self.head)]
            self.end = (self.end + piece)[-TAIL:]
            if i < len(pieces) - 1:  # a line ended
                self.line(self.head, self.end)
                self.head = self.end = ''

    def line(self, head, end):
        head = ANSI.sub('', head)
        if GUARD_MARK in head:
            self.guard = 'passed' if '"proceed": true' in end else 'refused' if '"proceed": false' in end else self.guard
            return
        prefix = PREFIX.match(head)
        body = head[prefix.end():] if prefix else head
        header = HEADER.match(body)
        body = body[header.end():] if header else body
        key = prefix.group(1) if prefix else ''
        if key not in self.state and len(self.state) >= MAX_PROCESSES:
            key = '*'
        state = self.state.setdefault(key, [False, None])
        if body.startswith('Traceback (most recent call last)'):
            self.tracebacks = min(self.tracebacks + 1, MAX_TRACEBACKS)
            state[:] = [True, None]
            return
        if not state[0]:
            return
        frame = FRAME.search(body)
        if frame:
            state[1] = next((c for fragment, c in LOCATIONS if fragment in frame.group(1)), 'other')
            return
        found = EXCEPTION_LINE.match(body)
        if found:
            name = found.group(1).rsplit('.', 1)[-1]
            process = 'main' if not prefix else next(
                (token for marker, token in PROCESSES if prefix.group(1).startswith(marker)), 'other')
            self.exception = (name if name in EXCEPTION_TYPES else 'other', state[1] or 'other', process)
            self.first_exception = self.first_exception or self.exception
            state[0] = False  # a chained traceback starts again with its own header

    def summary(self):
        return {'firstCategory': self.first, 'categories': [n for n in CATEGORY_NAMES if n in self.seen],
                'bytesInspected': self.inspected, 'limitReached': self.limit, 'guard': self.guard,
                'tracebacks': self.tracebacks, **self.tokens('exception', self.exception),
                **self.tokens('firstException', self.first_exception)}

    @staticmethod
    def tokens(name, found):
        kind, location, process = found or (None, None, None)
        return {f'{name}Type': kind, f'{name}Location': location, f'{name}Process': process}


def codes(failures):
    return [next((c for marker, c in CODES if marker in f), 'other') for f in failures]


def write_status(path, **fields):
    """Atomic and best effort: the status file is telemetry and never changes what the guard does."""
    try:
        tmp = f'{path}.tmp'
        with open(tmp, 'w') as f:
            json.dump({**fields, 'at': int(time.time())}, f, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        pass


def claim_once(status_path):
    """Consume the one-shot supervision token next to the status file. True only for the attempt that renamed it."""
    token = os.path.join(os.path.dirname(status_path), 'supervise-once')
    try:
        os.rename(token, token + '.used')
        return True
    except OSError:
        return False


def supervise(command, status_path, max_bytes=2 << 20, max_seconds=600):
    """Run the server as a child, pass all of its output through, inspect a bounded prefix of it, and return the exit
    status the container should have."""
    started, seen = time.monotonic(), {'listenedAfterS': None}
    capture = Capture(max_bytes, max_seconds)
    args = command[1:]
    host = args[args.index('--host') + 1] if '--host' in args else '127.0.0.1'
    port = int(args[args.index('--port') + 1]) if '--port' in args else 8000
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda s, _: child.poll() is None and child.send_signal(s))

    def status(stage, **extra):
        write_status(status_path, stage=stage, proceed=True, failures=[], elapsedS=int(time.monotonic() - started),
                     **seen, **capture.summary(), **extra)
    status('server_started')

    def listen():
        while child.poll() is None and seen['listenedAfterS'] is None:
            try:
                socket.create_connection((host, port), timeout=2).close()
                seen['listenedAfterS'] = int(time.monotonic() - started)
                status('listening')
            except OSError:
                time.sleep(5)
    listener = threading.Thread(target=listen, daemon=True)
    listener.start()
    outputs = {child.stdout.fileno(): sys.stdout.buffer, child.stderr.fileno(): sys.stderr.buffer}
    while outputs:
        try:
            ready, _, _ = select.select(list(outputs), [], [], 1.0)
        except InterruptedError:
            continue
        for fd in ready:
            chunk = os.read(fd, CHUNK)
            if not chunk:
                del outputs[fd]
                continue
            outputs[fd].write(chunk)
            outputs[fd].flush()
            capture.feed(chunk)
        capture.feed(b'')  # notices the deadline even when the server is quiet
    rc = child.wait()
    listener.join(10)  # so `exited` is the last status written
    status('exited', exitCode=rc if rc >= 0 else None, signal=-rc if rc < 0 else None)
    return 128 - rc if rc < 0 else rc


def main(argv):
    if '--' not in argv or argv.index('--') != 1:
        raise SystemExit(__doc__)
    limits, command = json.loads(argv[0]), argv[2:]
    facts = collect(os.path.expanduser(limits['cache_dir']))
    failures = evaluate(facts, limits)
    print(json.dumps({'guard': 'muse-glimmer-startup', 'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                      'facts': facts, 'limits': limits, 'failures': failures, 'proceed': not failures}), flush=True)
    status_path = limits.get('status_path')
    if status_path:
        write_status(status_path, stage='guard_refused' if failures else 'guard_passed', proceed=not failures,
                     failures=codes(failures))
    if failures:
        sys.exit(EXIT_REFUSED)
    bounds = limits.get('supervise')
    if status_path and isinstance(bounds, dict) and claim_once(status_path):
        sys.exit(supervise(command, status_path, int(bounds.get('max_bytes', 2 << 20)),
                           int(bounds.get('max_seconds', 600))))
    os.execvp(command[0], command)


if __name__ == '__main__':
    main(sys.argv[1:])
