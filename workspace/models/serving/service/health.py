#!/usr/bin/env python3
"""Loaded-model readiness and bounded recovery for the persistent service (MODELS-ALWAYSON-SERVICE-001).

`run` is started by models-health.timer about every 60 s.
1. Probe the loopback vLLM. Ready means /v1/models lists the served alias, AND a real chat completion from that model
   returns at least one generated token.
2. Apply `decide()`:
   - within the startup grace, a failure is `starting`;
   - after FAILURES_BEFORE_RESTART consecutive failures, restart the model unit, at most RESTARTS_PER_HOUR times;
   - after that, report `failed_needs_operator` and stop acting.
3. On the first ready result with no model receipt, record the receipt.
4. Write the status JSON for operators and monitoring: categories only, no prompt or reply text.
5. Publish an allowlisted receipt to the existing guest attribute rose-models/health: boot id, probe time, the
   completion's HTTP status, token count and latency (never its text), readiness, restarts, and the model receipt's
   start and full verification results, each qualified, plus the model unit's state, result and main exit code,
   start-model's last phase, selected container State fields, the startup guard's status for this attempt (its
   result; listen, exit and fixed output categories only for an explicitly supervised attempt) and how much of the
   model the weights cache holds. Publication runs after the decision and its failure
   only marks the status file: telemetry never restarts the model.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

FAILURES_BEFORE_RESTART = 3
RESTARTS_PER_HOUR = 3
STARTUP_GRACE_S = 3600  # the first start downloads about 60 GB; later starts load from the data disk
PROBE_TIMEOUT_S = 60
UNIT = 'muse-glimmer.service'
LOOPBACK = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route loopback probes via a proxy
GUEST = 'http://metadata.google.internal/computeMetadata/v1/instance/guest-attributes/rose-models/'
RECEIPT_SCHEMA, RECEIPT_MAX = 'rose-models-health/1', 4096
PHASES = ('verifying', 'launching_offline', 'launching_online', 'refused_storage')
RUN_STAGES = ('guard_refused', 'guard_passed', 'server_started', 'listening', 'exited')
GUARD_CODES = ('cuda_unavailable', 'driver_unknown', 'driver_too_old', 'gpu_count', 'gpu_total', 'gpu_free',
               'cache_free', 'shm', 'other')
OUTPUT_CATEGORIES = ('argument_rejected', 'tool_parser_invalid', 'reasoning_parser_invalid', 'image_missing',
                     'model_config_unavailable', 'device_unavailable', 'cuda_out_of_memory', 'kv_cache_insufficient',
                     'nccl_error', 'import_error', 'traceback')  # startup_guard.CATEGORIES
CONTAINER = 'muse-glimmer'
_spec = importlib.util.spec_from_file_location('unit_facts', Path(__file__).resolve().parent / 'unit_facts.py')
unit_facts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(unit_facts)
CATEGORY = re.compile(r'^[a-z][a-z0-9_]{0,39}$')
BOOT = re.compile(r'^[0-9a-f-]{36}$')
STATUSES = ('ready', 'starting', 'recovering', 'degraded', 'down', 'failed_needs_operator', 'unknown')
VERIFY_RESULTS = ('verified', 'no_receipt', 'mismatch')


def _json(url, body=None, timeout=PROBE_TIMEOUT_S):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'} if data else {})
    with LOOPBACK.open(req, timeout=timeout) as r:
        return r.status, json.loads(r.read(1 << 20))


def probe(base, model, opener=_json, facts=None):
    """(True, 'ready') or (False, category). Categories are fixed strings. `facts` receives listed, completionHttp,
    completionTokens and latencyMs: numbers and booleans only, never text."""
    facts = {} if facts is None else facts
    facts.update(listed=False, completionHttp=None, completionTokens=None, latencyMs=None)
    try:
        status, listing = opener(base + '/v1/models')
        if status != 200 or model not in [m.get('id') for m in listing.get('data', [])]:
            return False, 'model_not_listed'
        facts['listed'] = True
        started = time.monotonic()
        try:
            status, reply = opener(base + '/v1/chat/completions', {
                'model': model, 'max_tokens': 16,
                'messages': [{'role': 'user', 'content': 'Readiness check. Reply with one word.'}]})
        except urllib.error.HTTPError as e:
            facts['completionHttp'] = e.code
            raise
        finally:
            facts['latencyMs'] = int((time.monotonic() - started) * 1000)
        facts['completionHttp'] = status
        tokens = reply.get('usage', {}).get('completion_tokens') if isinstance(reply, dict) else None
        facts['completionTokens'] = tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None
        if status != 200 or reply.get('model') != model or not reply.get('choices'):
            return False, 'completion_invalid'
        if not isinstance(reply.get('usage', {}).get('completion_tokens'), int) or reply['usage']['completion_tokens'] < 1:
            return False, 'no_tokens_generated'
        return True, 'ready'
    except urllib.error.HTTPError as e:
        return False, f'http_{e.code}'
    except (urllib.error.URLError, OSError):
        return False, 'unreachable'
    except (ValueError, AttributeError, TypeError):
        return False, 'malformed_reply'


def decide(state, ok, category, now, active_since):
    """Pure transition: (new state, action) where action is None or 'restart'."""
    s = {'failures': 0, 'restarts': [], 'status': 'unknown', 'lastOk': None, 'lastCategory': None, **state}
    s['restarts'] = [t for t in s['restarts'] if now - t < 3600]
    s['lastCategory'] = category
    if ok:
        s.update(failures=0, status='ready', lastOk=now)
        return s, None
    if s['status'] == 'failed_needs_operator':
        return s, None  # stays failed until an operator runs `reset`
    if active_since is None:
        s.update(status='down', failures=s['failures'] + 1)
    elif now - active_since < 120 or (now - active_since < STARTUP_GRACE_S
                                       and (s['lastOk'] is None or s['lastOk'] < active_since)):
        s['status'] = 'starting'  # not yet ready since this activation
        return s, None
    else:
        s.update(status='degraded', failures=s['failures'] + 1)
    if s['failures'] < FAILURES_BEFORE_RESTART:
        return s, None
    if len(s['restarts']) >= RESTARTS_PER_HOUR:
        s['status'] = 'failed_needs_operator'
        return s, None
    s['restarts'].append(now)
    s.update(failures=0, status='recovering')
    return s, 'restart'


def active_since(unit=UNIT, run=subprocess.run):
    """Wall-clock seconds when the unit last became active, or None when it is not active."""
    out = run(['systemctl', 'show', unit, '-p', 'ActiveState', '-p', 'ActiveEnterTimestampMonotonic'],
              capture_output=True, text=True, timeout=30).stdout
    props = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
    if props.get('ActiveState') != 'active':
        return None
    mono = int(props.get('ActiveEnterTimestampMonotonic', '0')) / 1e6
    return time.time() - (time.monotonic() - mono)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(data, sort_keys=True) + '\n')
    os.replace(tmp, path)


def boot_id(path='/proc/sys/kernel/random/boot_id'):
    try:
        value = Path(path).read_text().strip()
    except OSError:
        return 'unknown'
    return value if BOOT.match(value) else 'unknown'


def _verify_fact(path):
    """The last verify_model result written to `path`: {result, full, at}, or None when it has not run."""
    try:
        raw = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {'result': 'unreadable'}
    if not isinstance(raw, dict):
        return {'result': 'unreadable'}
    at = raw.get('at')
    return {'result': raw.get('result') if raw.get('result') in VERIFY_RESULTS else 'unknown',
            'full': raw.get('full') is True, 'at': at if isinstance(at, int) and not isinstance(at, bool) else None}


def _recorded_at(path):
    """When the receipt was recorded (every file content-hashed at first load), or None."""
    try:
        value = json.loads(Path(path).read_text()).get('recordedAt')
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and re.match(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$', value) else None


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _startup_fact(path):
    """start-model's last recorded phase this boot (it lives in /run): phase, verifyExit, dataMounted, imagePresent,
    at. None when start-model has not run under this payload this boot."""
    try:
        raw = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {'phase': 'unreadable'}
    if not isinstance(raw, dict):
        return {'phase': 'unreadable'}
    flag = lambda v: v if isinstance(v, bool) else None  # noqa: E731
    exit_code = _int(raw.get('verifyExit'))
    return {'phase': raw.get('phase') if raw.get('phase') in PHASES else 'other',
            'verifyExit': exit_code if exit_code is not None and 0 <= exit_code <= 255 else None,
            'dataMounted': flag(raw.get('dataMounted')), 'imagePresent': flag(raw.get('imagePresent')),
            'at': _int(raw.get('at'))}


def _bounded(v, lo, hi):
    v = _int(v)
    return v if v is not None and lo <= v <= hi else None


def _container_run(path):
    """The startup guard's status for this attempt (start-model clears it before each launch): the guard result as
    codes; only when supervision was explicitly enabled for one attempt, also when the server first listened, how it
    exited and the fixed output categories seen within the capture bounds. None before any launch under this
    payload."""
    try:
        raw = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {'stage': 'unreadable'}
    if not isinstance(raw, dict):
        return {'stage': 'unreadable'}
    failures = raw.get('failures') if isinstance(raw.get('failures'), list) else []
    seen = raw.get('categories') if isinstance(raw.get('categories'), list) else []
    return {'stage': raw.get('stage') if raw.get('stage') in RUN_STAGES else 'other',
            'proceed': raw.get('proceed') if isinstance(raw.get('proceed'), bool) else None,
            'failures': [f if f in GUARD_CODES else 'other' for f in failures[:8]],
            'elapsedS': _bounded(raw.get('elapsedS'), 0, 1 << 31),
            'listenedAfterS': _bounded(raw.get('listenedAfterS'), 0, 1 << 31),
            'exitCode': _bounded(raw.get('exitCode'), 0, 255), 'signal': _bounded(raw.get('signal'), 1, 64),
            'firstCategory': raw.get('firstCategory') if raw.get('firstCategory') in OUTPUT_CATEGORIES else None,
            'categories': [c for c in OUTPUT_CATEGORIES if c in seen],
            'bytesInspected': _bounded(raw.get('bytesInspected'), 0, 2 << 20),
            'limitReached': raw.get('limitReached') if raw.get('limitReached') in ('bytes', 'time') else None,
            'at': _int(raw.get('at'))}


def _weights_cache(cache):
    """How much of the model the Hugging Face cache holds: complete and partial blob bytes, snapshot files, and the
    pinned repository size to compare with. Sizes only; no names."""
    out = {'blobBytes': 0, 'incompleteBytes': 0, 'incompleteFiles': 0, 'snapshotFiles': 0, 'expectedBytes': None}
    try:
        out['expectedBytes'] = _int(json.loads((Path(__file__).resolve().parents[1] / 'muse-glimmer-30b.json')
                                               .read_text())['model']['repo_bytes'])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        repos = [r for r in (Path(cache) / 'hub').iterdir() if r.name.startswith('models--')][:4]
    except OSError:
        return {**out, 'query': 'absent'}
    for repo in repos:
        try:
            for blob in os.scandir(repo / 'blobs'):
                size = blob.stat(follow_symlinks=False).st_size
                if blob.name.endswith('.incomplete'):
                    out['incompleteBytes'] += size
                    out['incompleteFiles'] += 1
                else:
                    out['blobBytes'] += size
            out['snapshotFiles'] += sum(len(files) for _, _, files in os.walk(repo / 'snapshots'))
        except OSError:
            continue
    return {**out, 'query': 'ok'}


def diagnose(a, query=subprocess.run):
    """Why the model may not answer: the unit's state, last result and main exit; start-model's phase; selected
    container State fields. Bounded queries; no journal, logs, environment or stderr."""
    return {'service': unit_facts.service(UNIT, query), 'startup': _startup_fact(a.start_phase),
            'container': unit_facts.container(CONTAINER, query), 'containerRun': _container_run(a.container_status),
            'weightsCache': _weights_cache(a.cache)}


def receipt(boot, now, ok, category, state, action, facts, receipt_present, start_check, full_check, recorded_at=None,
            diagnostics=None):
    """The published health receipt, built field by field from an allowlist. Model receipt facts are qualified:
    `present` is only the file and `recordedAt` when it hashed every file at first load; `startCheck` (this boot,
    blob ids and sizes; None until a start under this payload) and `fullCheck` (last content rehash) are separate
    verify_model results with their own times."""
    last_ok = state.get('lastOk') if state.get('lastOkBoot') == boot else None
    out = {'schema': RECEIPT_SCHEMA, 'bootId': boot if BOOT.match(boot) else 'unknown', 'checkedAt': int(now),
           'ready': ok is True, 'status': state['status'] if state['status'] in STATUSES else 'unknown',
           'category': category if CATEGORY.match(category) else 'other',
           'completion': {'listed': facts.get('listed') is True, 'http': _int(facts.get('completionHttp')),
                          'tokens': _int(facts.get('completionTokens')), 'latencyMs': _int(facts.get('latencyMs'))},
           'lastOkThisBoot': int(last_ok) if isinstance(last_ok, (int, float)) else None,
           'consecutiveFailures': int(state['failures']), 'restartsLastHour': len(state['restarts']),
           'restartLimitPerHour': RESTARTS_PER_HOUR, 'action': action if action in ('restart',) else None,
           'modelReceipt': {'present': receipt_present is True, 'recordedAt': recorded_at,
                            'startCheck': start_check, 'fullCheck': full_check}}
    for key in ('service', 'startup', 'container', 'containerRun', 'weightsCache'):  # allowlisted by diagnose()
        out[key] = (diagnostics or {}).get(key)
    line = json.dumps(out, sort_keys=True)
    if len(line) > RECEIPT_MAX:
        raise ValueError('receipt_too_large')
    return line


def publish(key, line, timeout=5):
    """PUT one value under the existing guest attributes namespace rose-models/ (enable-guest-attributes=TRUE)."""
    req = urllib.request.Request(GUEST + key, data=line.encode(), headers={'Metadata-Flavor': 'Google'}, method='PUT')
    LOOPBACK.open(req, timeout=timeout).close()


def run_once(a, now=None, prober=probe, since=active_since, run=subprocess.run, publisher=publish, boot=None,
             query=subprocess.run):
    now = time.time() if now is None else now
    boot = boot_id() if boot is None else boot
    state = json.loads(a.state.read_text()) if a.state.exists() else {}
    facts = {}
    ok, category = prober(a.base, a.model, facts=facts)
    state, action = decide(state, ok, category, now, since())
    if ok:
        state['lastOkBoot'] = boot
    if action == 'restart':
        run(['systemctl', 'restart', UNIT], timeout=120)
    if ok and a.record and not a.receipt.exists():
        run(a.record, timeout=3600)  # verify_model.py record (hashes the cache once)
    _write(a.state, state)
    status = {'status': state['status'], 'category': category, 'checkedAt': now, 'lastOk': state['lastOk'],
              'consecutiveFailures': state['failures'], 'restartsLastHour': len(state['restarts']),
              'modelReceipt': a.receipt.exists(), 'action': action}
    try:  # telemetry: after the decision, and its failure never changes it
        line = receipt(boot, now, ok, category, state, action, facts, a.receipt.exists(),
                       _verify_fact(a.verify_start), _verify_fact(a.verify_full), _recorded_at(a.receipt),
                       diagnose(a, query))
        publisher('health', line)
        status['published'] = True
    except Exception as e:  # noqa: BLE001 - any publication failure is reported, never raised
        status.update(published=False, publishError=type(e).__name__)
    _write(a.status, status)
    return state, action


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('run', 'probe', 'reset'))
    p.add_argument('--base', default='http://127.0.0.1:8000')
    p.add_argument('--model', default='muse-glimmer')
    p.add_argument('--state', type=Path, default=Path('/var/lib/rose-models/health-state.json'))
    p.add_argument('--status', type=Path, default=Path('/run/rose-models/health.json'))
    p.add_argument('--receipt', type=Path, default=Path('/mnt/models-data/receipt.json'))
    p.add_argument('--verify-start', type=Path, default=Path('/run/rose-models/verify-start.json'))
    p.add_argument('--verify-full', type=Path, default=Path('/var/lib/rose-models/verify-full.json'))
    p.add_argument('--start-phase', type=Path, default=Path('/run/rose-models/start-phase.json'))
    p.add_argument('--container-status', type=Path, default=Path('/run/rose-models/container/status.json'))
    p.add_argument('--cache', type=Path, default=Path('/mnt/models-data/hf'))
    p.add_argument('--record', nargs=argparse.REMAINDER, help='command that records the model receipt (last option)')
    a = p.parse_args(argv)
    if a.command == 'probe':
        ok, category = probe(a.base, a.model)
        print(json.dumps({'ready': ok, 'category': category}))
        return 0 if ok else 1
    if a.command == 'reset':  # operator acknowledged a failed_needs_operator state
        _write(a.state, {})
        return 0
    state, _ = run_once(a)
    print(json.dumps({'status': state['status']}))
    return 0 if state['status'] in ('ready', 'starting', 'recovering') else 1


if __name__ == '__main__':
    sys.exit(main())
