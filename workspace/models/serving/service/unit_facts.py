"""Bounded, allowlisted systemd and container facts for the published receipts (MODEL-START-DIAG-001).

systemd 255 prints NextElapseUSecRealtime and LastTriggerUSec in its local calendar format even with
`--timestamp=unix` (that option reaches only *Timestamp properties), so every `systemctl show` here runs with TZ=UTC
and LC_ALL=C and timestamps are parsed from either `@<seconds>` or `<Day> YYYY-MM-DD HH:MM:SS UTC`. Values are never
published raw: states must look like systemd's lower-case words, numbers must be integers, and anything else becomes
a category. No journal, environment, command line or stderr is read or kept."""
import calendar
import json
import os
import re
import subprocess
import time

ENV = {'TZ': 'UTC', 'LC_ALL': 'C'}
WORD = re.compile(r'^[a-z][a-z0-9-]{0,31}$')
UNIT = re.compile(r'^[A-Za-z0-9@_.-]{1,64}\.(service|timer)$')
CONTAINER_STATUS = ('created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead')


def word(value):
    return value if isinstance(value, str) and WORD.match(value) else ('' if value in (None, '') else 'other')


def integer(value, lo=-(1 << 31), hi=(1 << 63)):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def show(unit, props, run=subprocess.run, scope=()):
    """The unit's properties, or None when the query itself failed (no systemctl, timeout or nonzero exit)."""
    argv = ['systemctl', *scope, 'show', unit, *[x for p in props for x in ('-p', p)]]
    try:
        r = run(argv, capture_output=True, text=True, timeout=30, env={**os.environ, **ENV})
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return dict(line.split('=', 1) for line in r.stdout.splitlines() if '=' in line)


def timestamp(value):
    """(unix seconds or None, format): format is unix, utc, empty or unsupported."""
    value = (value or '').strip()
    if value in ('', 'n/a', '0', 'infinity'):
        return None, 'empty'
    if re.fullmatch(r'@\d{1,12}', value):
        return int(value[1:]), 'unix'
    try:
        return calendar.timegm(time.strptime(value, '%a %Y-%m-%d %H:%M:%S UTC')), 'utc'
    except ValueError:
        return None, 'unsupported'


def service(unit, run=subprocess.run, scope=()):
    """A service's state, last result and main process exit, as published."""
    p = show(unit, ('LoadState', 'ActiveState', 'SubState', 'Result', 'ExecMainCode', 'ExecMainStatus', 'NRestarts',
                    'ActiveEnterTimestamp', 'ExecMainExitTimestamp'), run, scope)
    if p is None:
        return {'query': 'failed'}
    return {'query': 'ok', 'load': word(p.get('LoadState')), 'active': word(p.get('ActiveState')),
            'sub': word(p.get('SubState')), 'result': word(p.get('Result')),
            'mainCode': integer(p.get('ExecMainCode'), 0, 255), 'mainStatus': integer(p.get('ExecMainStatus'), -255, 255),
            'restarts': integer(p.get('NRestarts'), 0, 1 << 31), 'activeSince': timestamp(p.get('ActiveEnterTimestamp'))[0],
            'lastExit': timestamp(p.get('ExecMainExitTimestamp'))[0]}


def timer(unit, run=subprocess.run, scope=(), now=None):
    """A timer's facts and one `schedule` category: query_failed, not_found, disabled, inactive,
    timestamp_format_unsupported, unscheduled, out_of_range or scheduled. The service it triggers is included."""
    now = time.time() if now is None else now
    p = show(unit, ('LoadState', 'UnitFileState', 'ActiveState', 'SubState', 'NextElapseUSecRealtime',
                    'LastTriggerUSec', 'Triggers'), run, scope)
    if p is None:
        return {'query': 'failed', 'schedule': 'query_failed', 'enabled': False, 'active': False, 'nextRun': None,
                'lastRun': None}
    next_run, next_format = timestamp(p.get('NextElapseUSecRealtime'))
    last_run, last_format = timestamp(p.get('LastTriggerUSec'))
    out = {'query': 'ok', 'load': word(p.get('LoadState')), 'unitFileState': word(p.get('UnitFileState')),
           'enabled': p.get('UnitFileState') == 'enabled', 'active': p.get('ActiveState') == 'active',
           'sub': word(p.get('SubState')), 'nextRun': next_run, 'nextRunFormat': next_format, 'lastRun': last_run,
           'lastRunFormat': last_format}
    if p.get('LoadState') != 'loaded':
        schedule = 'not_found'
    elif not out['enabled']:
        schedule = 'disabled'
    elif not out['active']:
        schedule = 'inactive'
    elif next_format == 'unsupported':
        schedule = 'timestamp_format_unsupported'
    elif next_run is None:
        schedule = 'unscheduled'
    elif not now - 3600 <= next_run <= now + 400 * 86400:
        schedule = 'out_of_range'
    else:
        schedule = 'scheduled'
    out['schedule'] = schedule
    triggers = (p.get('Triggers') or '').split()
    if len(triggers) == 1 and UNIT.match(triggers[0]):
        out['triggers'] = triggers[0]
        out['service'] = service(triggers[0], run, scope)
    else:
        out['triggers'] = None if not triggers else 'other'
    return out


def _rfc3339(value):
    m = re.fullmatch(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?Z', value or '')
    if not m or m.group(1).startswith('0001-'):
        return None
    return calendar.timegm(time.strptime(m.group(1), '%Y-%m-%dT%H:%M:%S'))


def container(name, run=subprocess.run):
    """Selected docker State fields of one container: present, status, exitCode, oomKilled, startedAt, finishedAt."""
    try:
        r = run(['docker', 'inspect', '--type', 'container', '--format', '{{json .State}}', name],
                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return {'query': 'failed'}
    if r.returncode != 0:
        if 'No such' in (r.stderr or ''):  # matched, never kept
            return {'query': 'ok', 'present': False}
        return {'query': 'failed'}
    try:
        state = json.loads(r.stdout)
    except ValueError:
        return {'query': 'unreadable'}
    if not isinstance(state, dict):
        return {'query': 'unreadable'}
    status = state.get('Status')
    return {'query': 'ok', 'present': True, 'status': status if status in CONTAINER_STATUS else 'other',
            'exitCode': integer(state.get('ExitCode'), -255, 255), 'oomKilled': state.get('OOMKilled') is True,
            'startedAt': _rfc3339(state.get('StartedAt')), 'finishedAt': _rfc3339(state.get('FinishedAt'))}
