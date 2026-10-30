"""Closed, nonsecret signing diagnostic for the Rose Models signing path (MODELS-SIGN-DIAG-001).

Producer side (inside the protected signing process, scripts/sign-apk.py with --diagnostic-out):
  run_bounded()  starts one tool and drains its stdout/stderr pipes concurrently in fixed-size reads, holding at most
                 CAPTURE_BYTES per stream in process memory. The cap is enforced while the tool runs: the read that
                 would pass it ends collection, the excess is discarded unstored and the tool is stopped (TERM, then
                 KILL) and reaped within a finite bound. Output is never written to disk; the buffers are only used
                 for classification inside the signing process and are dropped with the Result.
  classify()     maps tool output to ONE fixed category by fixed patterns, else 'unclassified'. A match is an
                 observation of the tool's message, not proof of a key, password or certificate defect.
  write()        writes the closed record (owner-only, never overwrites).

Consumer side (workspace/models/signing/models_signing.py):
  load()         accepts only the closed schema with consistent fields and the bound apksigner identity; anything
                 else is reported as invalid without echoing a single foreign value.
  summary()      fixed tokens only: stage, outcome, child exit or signal, category, wrapper exit.

No raw stdout/stderr line, snippet, argv, environment value, path or exception text is ever part of a record.
"""
import hashlib
import json
import os
import re
import selectors
import subprocess
import time

SCHEMA = 'rose-models-sign-diagnostic/1'
STAGES = ('invocation', 'alignment', 'sign', 'verify', 'complete')
OUTCOMES = ('ok', 'exit', 'signal', 'timeout', 'oversized', 'not_started', 'rejected')
CATEGORIES = (
    'none', 'invocation_rejected', 'tool_missing', 'timeout', 'output_oversized',
    'keystore_password_incorrect', 'key_password_incorrect', 'alias_not_found', 'keystore_unreadable',
    'password_file_unreadable', 'java_runtime_error', 'apk_input_rejected', 'output_write_failed',
    'signature_not_verified', 'certificate_pin_mismatch', 'unclassified',
)
FIELDS = ('schema', 'stage', 'outcome', 'childExit', 'childSignal', 'category', 'apksignerJarSha256')
TIMEOUT_SECONDS = 300
CAPTURE_BYTES = 256 * 1024  # per stream; both streams together retain at most 2 * CAPTURE_BYTES
CHUNK_BYTES = 64 * 1024  # largest single read; a read never asks for more than one byte past the cap
POLL_SECONDS = 0.05  # how often a quiet, still-running tool is checked for exit
TERM_GRACE_SECONDS = 1  # after SIGTERM, before SIGKILL
REAP_SECONDS = 5  # after SIGKILL; total stop/reap bound is TERM_GRACE_SECONDS + REAP_SECONDS
RECORD_BYTES = 4096
HEX64 = re.compile(r'[0-9a-f]{64}')

# Fixed patterns, checked in order against bounded tool output inside the protected process only.
PATTERNS = (
    ('keystore_password_incorrect', re.compile(r'Keystore was tampered with, or password was incorrect|'
                                               r'keystore password was incorrect', re.I)),
    ('key_password_incorrect', re.compile(r'Cannot recover key|UnrecoverableKeyException|'
                                          r'Failed to obtain key with alias', re.I)),
    ('alias_not_found', re.compile(r'does not contain (a )?key (entry )?(with|named) alias|No key with alias|'
                                   r'Alias .* does not exist', re.I)),
    ('password_file_unreadable', re.compile(r'Failed to read password from file|NoSuchFileException', re.I)),
    ('keystore_unreadable', re.compile(r'Failed to load keystore|Invalid keystore format|DerInputStream|'
                                       r'KeyStoreException', re.I)),
    ('java_runtime_error', re.compile(r'UnsupportedClassVersionError|Could not find or load main class|'
                                      r'NoClassDefFoundError|JAVA_HOME|java: (command )?not found', re.I)),
    ('apk_input_rejected', re.compile(r'MinSdkVersionException|ZipFormatException|Malformed APK|'
                                      r'Failed to determine APK', re.I)),
    ('output_write_failed', re.compile(r'AccessDeniedException|Permission denied|Failed to write|'
                                       r'No space left on device', re.I)),
    ('signature_not_verified', re.compile(r'DOES NOT VERIFY', re.I)),
)


class Result:
    """Bounded outcome of one tool run. stdout/stderr are truncated text for in-process parsing only."""

    def __init__(self, outcome, returncode=None, signum=None, stdout='', stderr=''):
        self.outcome, self.returncode, self.signum = outcome, returncode, signum
        self.stdout, self.stderr = stdout, stderr


def _stop(child):
    """Stop and reap the child within TERM_GRACE_SECONDS + REAP_SECONDS. True once reaped."""
    for send, wait in ((child.terminate, TERM_GRACE_SECONDS), (child.kill, REAP_SECONDS)):
        if child.poll() is not None:
            return True
        try:
            send()
        except OSError:
            pass
        try:
            child.wait(timeout=wait)
            return True
        except subprocess.TimeoutExpired:
            pass
    return child.poll() is not None


def run_bounded(command, timeout=TIMEOUT_SECONDS, popen=subprocess.Popen):
    """Exactly one start of `command`. Its stdout and stderr are pipes drained together, CHUNK_BYTES at most per
    read and never more than one byte past CAPTURE_BYTES, so each stream retains at most CAPTURE_BYTES and the
    excess is never stored. A stream passing the cap ('oversized'), the deadline ('timeout') or any exception here
    stops the tool and closes both pipes; a writer left holding them then gets EPIPE instead of filling anything."""
    try:
        child = popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        return Result('not_started')
    deadline = time.monotonic() + timeout
    buffers = {child.stdout: bytearray(), child.stderr: bytearray()}
    outcome = None
    try:
        with selectors.DefaultSelector() as selector:
            for pipe in buffers:
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ)
            while outcome is None and selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    outcome = 'timeout'
                    break
                events = selector.select(min(remaining, POLL_SECONDS))
                for key, _ in events:
                    buffer = buffers[key.fileobj]
                    try:
                        data = os.read(key.fd, min(CHUNK_BYTES, CAPTURE_BYTES - len(buffer) + 1))
                    except BlockingIOError:
                        continue
                    if not data:
                        selector.unregister(key.fileobj)
                    elif len(buffer) + len(data) > CAPTURE_BYTES:
                        outcome = 'oversized'
                        break
                    else:
                        buffer += data
                if not events and child.poll() is not None:
                    break  # the tool has exited and nothing it wrote is pending; another holder of the pipe is not it
        if outcome is None:
            try:
                code = child.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                outcome = 'timeout'
    finally:
        for pipe in buffers:
            pipe.close()
        if child.returncode is None:
            _stop(child)
    if outcome is not None:
        return Result(outcome)
    stdout, stderr = (bytes(b).decode('utf-8', 'replace') for b in buffers.values())
    if code < 0:
        return Result('signal', None, -code)
    return Result('ok' if code == 0 else 'exit', code, None, stdout, stderr)


def classify(result):
    if result.outcome == 'not_started':
        return 'tool_missing'
    if result.outcome == 'timeout':
        return 'timeout'
    if result.outcome == 'oversized':
        return 'output_oversized'
    if result.outcome == 'ok':
        return 'none'
    text = result.stderr + '\n' + result.stdout
    for category, pattern in PATTERNS:
        if pattern.search(text):
            return category
    return 'unclassified'


def record(stage, result=None, category=None, apksigner_jar_sha256=None):
    """A closed record. With no result (invocation/verify checks) the outcome is 'rejected' or 'ok'."""
    if result is None:
        outcome, child_exit, child_signal = ('ok' if category == 'none' else 'rejected'), None, None
    else:
        outcome = result.outcome
        child_exit = result.returncode if outcome in ('ok', 'exit') or (outcome == 'oversized' and result.returncode is not None) else None
        child_signal = result.signum if outcome in ('signal', 'oversized') else None
        category = category or classify(result)
    diag = {'schema': SCHEMA, 'stage': stage, 'outcome': outcome, 'childExit': child_exit,
            'childSignal': child_signal, 'category': category, 'apksignerJarSha256': apksigner_jar_sha256}
    problem = validate(diag)
    if problem:
        raise ValueError('diagnostic_record_invalid')
    return diag


def validate(diag):
    """None when `diag` is a consistent closed record, else a fixed problem token (never a value from it)."""
    if not isinstance(diag, dict) or list(diag) != list(FIELDS):
        return 'fields'
    if diag['schema'] != SCHEMA:
        return 'schema'
    if diag['stage'] not in STAGES or diag['outcome'] not in OUTCOMES or diag['category'] not in CATEGORIES:
        return 'enum'
    exit_, sig = diag['childExit'], diag['childSignal']
    if exit_ is not None and (type(exit_) is not int or not 0 <= exit_ <= 255):
        return 'child_exit'
    if sig is not None and (type(sig) is not int or not 1 <= sig <= 64):
        return 'child_signal'
    jar = diag['apksignerJarSha256']
    if jar is not None and (not isinstance(jar, str) or not HEX64.fullmatch(jar)):
        return 'tool_identity'
    outcome, category, stage = diag['outcome'], diag['category'], diag['stage']
    consistent = {
        'ok': exit_ in (0, None) and sig is None and category == 'none',
        'exit': isinstance(exit_, int) and exit_ != 0 and sig is None and category not in ('none', 'timeout', 'tool_missing', 'output_oversized'),
        'signal': exit_ is None and sig is not None and category == 'unclassified',
        'timeout': exit_ is None and sig is None and category == 'timeout',
        'oversized': category == 'output_oversized',
        'not_started': exit_ is None and sig is None and category == 'tool_missing',
        'rejected': exit_ is None and sig is None and category in ('invocation_rejected', 'certificate_pin_mismatch',
                                                                    'signature_not_verified'),
    }[outcome]
    if not consistent:
        return 'inconsistent'
    if (stage == 'complete') != (category == 'none'):
        return 'inconsistent'
    return None


def write(path, diag):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(diag, f)


def load(path, expected_apksigner_jar_sha256):
    """(record, None) for a valid record whose tool identity is the bound one, else (None, fixed problem token)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None, 'missing'
    with os.fdopen(fd, 'rb') as f:
        raw = f.read(RECORD_BYTES + 1)
    if len(raw) > RECORD_BYTES:
        return None, 'oversized'
    try:
        diag = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return None, 'malformed'
    problem = validate(diag)
    if problem:
        return None, problem
    if diag['apksignerJarSha256'] is not None and diag['apksignerJarSha256'] != expected_apksigner_jar_sha256:
        return None, 'tool_identity_mismatch'
    return diag, None


def summary(diag, problem, wrapper_exit):
    """Fixed tokens only. The inner tool exit and the wrapper (sign-apk.py) exit are reported separately."""
    wrapper = f'wrapper_exit={wrapper_exit}' if type(wrapper_exit) is int else 'wrapper_exit=unknown'
    if diag is None:
        return f'diagnostic=invalid:{problem} {wrapper}'
    parts = [f"stage={diag['stage']}", f"outcome={diag['outcome']}"]
    if diag['childExit'] is not None:
        parts.append(f"child_exit={diag['childExit']}")
    if diag['childSignal'] is not None:
        parts.append(f"child_signal={diag['childSignal']}")
    parts += [f"category={diag['category']}", wrapper]
    return ' '.join(parts)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


SIGN_OPTIONS = ('--keystore', '--config', '--output', '--expected-cert-sha256', '--diagnostic-out')


def validate_invocation(argv, sign_apk):
    """The adapter's exact sign-apk.py call shape: interpreter, the fixed script, one positional APK, each fixed
    option exactly once with one value, no inline password option. Returns a fixed problem token or None."""
    if len(argv) != 3 + 2 * len(SIGN_OPTIONS) or argv[1] != str(sign_apk):
        return 'shape'
    options = argv[3:]
    names = options[0::2]
    if sorted(names) != sorted(SIGN_OPTIONS) or any(v.startswith('-') for v in options[1::2]):
        return 'options'
    if any(word in a for a in argv for word in ('--ks-pass', '--key-pass', 'pass:', 'env:')):
        return 'inline_secret'
    return None
