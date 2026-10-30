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
"""
import argparse
import json
import os
from pathlib import Path
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


def _json(url, body=None, timeout=PROBE_TIMEOUT_S):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'} if data else {})
    with LOOPBACK.open(req, timeout=timeout) as r:
        return r.status, json.loads(r.read(1 << 20))


def probe(base, model, opener=_json):
    """(True, 'ready') or (False, category). Categories are fixed strings."""
    try:
        status, listing = opener(base + '/v1/models')
        if status != 200 or model not in [m.get('id') for m in listing.get('data', [])]:
            return False, 'model_not_listed'
        status, reply = opener(base + '/v1/chat/completions', {
            'model': model, 'max_tokens': 16,
            'messages': [{'role': 'user', 'content': 'Readiness check. Reply with one word.'}]})
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


def run_once(a, now=None, prober=probe, since=active_since, run=subprocess.run):
    now = time.time() if now is None else now
    state = json.loads(a.state.read_text()) if a.state.exists() else {}
    ok, category = prober(a.base, a.model)
    state, action = decide(state, ok, category, now, since())
    if action == 'restart':
        run(['systemctl', 'restart', UNIT], timeout=120)
    if ok and a.record and not a.receipt.exists():
        run(a.record, timeout=3600)  # verify_model.py record (hashes the cache once)
    _write(a.state, state)
    _write(a.status, {'status': state['status'], 'category': category, 'checkedAt': now, 'lastOk': state['lastOk'],
                      'consecutiveFailures': state['failures'], 'restartsLastHour': len(state['restarts']),
                      'modelReceipt': a.receipt.exists(), 'action': action})
    return state, action


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('run', 'probe', 'reset'))
    p.add_argument('--base', default='http://127.0.0.1:8000')
    p.add_argument('--model', default='muse-glimmer')
    p.add_argument('--state', type=Path, default=Path('/var/lib/rose-models/health-state.json'))
    p.add_argument('--status', type=Path, default=Path('/run/rose-models/health.json'))
    p.add_argument('--receipt', type=Path, default=Path('/mnt/models-data/receipt.json'))
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
