#!/usr/bin/env python3
"""Targeted local test selection over scripts/test-local.sh, which stays the full suite and CI's suite.

  test-select.py list                         the units parsed from test-local.sh
  test-select.py select [--base REF] [FILE..] which units a change needs, and why (JSON)
  test-select.py run    [--base REF] [FILE..] run those units in test-local order; stop at the first failure
  test-select.py run --all                    run every unit, timed (equivalent to test-local.sh)
  test-select.py trace                        rebuild scripts/test-select-deps.json: run every unit under strace and
                                              record the tracked files it opens, plus the repo paths its text names

A unit's inputs are its recorded files and the paths its command names. A change selects every unit whose inputs it
touches. An edit to test-local.sh selects the units it adds or changes; an edit to its header or Java prelude runs the
FULL suite, as does a change to this tool or its map, a package manifest, or any code file no unit claims. GitHub
configuration (.github/) and other files no unit reads select nothing: the offline suite never reads them. A unit
missing from the map (new or edited in test-local.sh) is always selected.

Map drift: the map is a trace at one commit (its generatedAt). A unit whose recorded or named code inputs have changed
since that commit may now read other files (an added import, a new fixture path), so it is STALE and always selected
until the map is retraced. If the map's commit is not in this checkout, the FULL suite runs. Changed files default to
the working tree against --base (origin/main) plus uncommitted changes. Exit: 0 pass, 1 a unit failed, 2 invalid input.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT/'scripts/test-local.sh'
MAP = ROOT/'scripts/test-select-deps.json'
FULL_TRIGGERS = ('scripts/test-select.py', 'scripts/test-select-deps.json')
OUTSIDE_SUITE = ('.github/',)   # read by GitHub, not by the offline suite; a unit that reads one still claims it
FULL_NAMES = ('package.json', 'package-lock.json')
CODE = re.compile(r'\.(py|mjs|cjs|js|ts|java|kt|sh|json|xml|patch|yml|yaml|toml|cfg|properties|bp|mk|c|cc|h|rs|go)$')
PATH_TOKEN = re.compile(r'(?<![\w./-])((?:workspace|os|scripts|qa|prototypes|infra)/[\w./@+-]+)')


def suite_prelude(text):
    """The lines that apply to every unit: the header through cd "$ROOT", and the Java directory/trap setup."""
    lines = text.splitlines()
    end = next(i for i, l in enumerate(lines) if l.strip() == 'cd "$ROOT"') + 1
    return lines[:end] + [l.strip() for l in lines[end:] if l.strip().startswith(('JAVA_TESTS=', 'trap '))]


def suite_at(ref):
    out = subprocess.run(['git', '-C', str(ROOT), 'show', f'{ref}:scripts/test-local.sh'], capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else None


def units(text=None):
    """test-local.sh as an ordered list of units: one command, one for-loop, or one javac + java pair."""
    lines = (text if text is not None else SUITE.read_text()).splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == 'cd "$ROOT"') + 1
    joined, buf = [], ''
    for line in lines[start:]:
        if line.rstrip().endswith('\\'):
            buf += line.rstrip()[:-1].rstrip() + ' '
            continue
        joined.append((buf + line).strip())
        buf = ''
    out, i = [], 0
    while i < len(joined):
        line = joined[i]
        if not line or line.startswith('#') or line.startswith('JAVA_TESTS=') or line.startswith('trap '):
            i += 1
            continue
        if line.startswith('for '):
            block = [line]
            while not joined[i].startswith('done'):
                i += 1
                block.append(joined[i])
            commands = block
        elif line.startswith('java -m jdk.compiler') and i + 1 < len(joined) and joined[i + 1].startswith('java '):
            commands = [line, joined[i + 1]]
            i += 1
        else:
            commands = [line]
        i += 1
        text = '\n'.join(commands)
        out.append({'id': len(out), 'key': hashlib.sha256(text.encode()).hexdigest()[:16], 'commands': commands,
                    'java': 'JAVA_TESTS' in text, 'paths': sorted(set(PATH_TOKEN.findall(text)))})
    return out


def tracked():
    return set(subprocess.run(['git', '-C', str(ROOT), 'ls-files'], capture_output=True, text=True, check=True).stdout.split())


def changed_files(base):
    run = lambda *a: subprocess.run(['git', '-C', str(ROOT), *a], capture_output=True, text=True).stdout.split()
    committed = run('diff', '--name-only', f'{base}...HEAD')
    working = run('diff', '--name-only', 'HEAD')
    untracked = run('ls-files', '--others', '--exclude-standard')
    return sorted(set(committed + working + untracked))


def drifted_since(ref):
    """Files that differ between the map's traced commit and the working tree (incl. untracked); None if unknown."""
    git = lambda *a: subprocess.run(['git', '-C', str(ROOT), *a], capture_output=True, text=True)
    if not ref or git('cat-file', '-e', f'{ref}^{{commit}}').returncode:
        return None
    diff = git('diff', '--name-only', '--no-renames', ref)
    if diff.returncode:
        return None
    return set(diff.stdout.split()) | set(git('ls-files', '--others', '--exclude-standard').stdout.split())


def load_map():
    try:
        return json.loads(MAP.read_text())
    except (OSError, ValueError):
        return {'units': {}}


def unit_inputs(unit, dep_map):
    inputs = set(unit['paths'])
    entry = dep_map.get('units', {}).get(unit['key'])
    if entry:
        inputs |= set(entry['inputs'])
    return inputs, entry is not None


def select(files, suite_units=None, dep_map=None, base_suite=None, suite_text=None, drifted=...):
    """base_suite: test-local.sh at the base, so an edit to it selects only the units it adds or changes.
    drifted: files changed since the map's trace (drifted_since); None means unknown, which runs the full suite. By
    default it is computed from the map's generatedAt; a map without one (a synthetic map) has no drift."""
    suite_text = suite_text if suite_text is not None else SUITE.read_text()
    suite_units = suite_units if suite_units is not None else units(suite_text)
    dep_map = dep_map if dep_map is not None else load_map()
    if drifted is ...:
        drifted = drifted_since(dep_map['generatedAt']) if dep_map.get('generatedAt') else frozenset()
    files = sorted(set(files))
    full = [f for f in files if f in FULL_TRIGGERS or Path(f).name in FULL_NAMES]
    if drifted is None:
        full.append('(map commit not in this checkout: retrace)')
        drifted = frozenset()
    new_keys = set()
    if 'scripts/test-local.sh' in files:
        if base_suite is None or suite_prelude(base_suite) != suite_prelude(suite_text):
            full.append('scripts/test-local.sh')
        else:
            new_keys = {u['key'] for u in suite_units} - {u['key'] for u in units(base_suite)}
    selected, claimed, stale_units = [], {'scripts/test-local.sh'}, []
    for unit in suite_units:
        inputs, mapped = unit_inputs(unit, dep_map)
        hit = sorted(inputs & set(files)) if mapped else ['(not in map)']
        if unit['key'] in new_keys:
            hit = ['(added or changed in test-local.sh)'] + hit
        stale = sorted(f for f in inputs & drifted if CODE.search(f)) if mapped else []
        if stale and not hit:
            hit = ['(stale: inputs changed since the trace)'] + stale
            stale_units.append(unit['id'])
        claimed |= inputs
        if hit:
            selected.append({'id': unit['id'], 'because': hit[:5]})
    unclaimed = [f for f in files if f not in claimed and f not in full]
    unclaimed_code = [f for f in unclaimed if CODE.search(f) and not f.startswith(OUTSIDE_SUITE)]
    if full or unclaimed_code:
        return {'mode': 'full', 'reason': {'triggers': full, 'unclaimedCode': unclaimed_code},
                'units': [u['id'] for u in suite_units], 'changed': files}
    return {'mode': 'targeted', 'units': [s['id'] for s in selected], 'because': selected,
            'stale': stale_units, 'ignored': unclaimed, 'changed': files, 'of': len(suite_units)}


def run_units(chosen, suite_units, timings=None, wrap=None):
    """Each unit in its own bash with test-local's settings; JAVA_TESTS shared per run, removed after."""
    java_dir = tempfile.mkdtemp(prefix='test-select-java-')
    record, status = [], 0
    try:
        for unit in suite_units:
            if unit['id'] not in chosen:
                continue
            script = 'set -euo pipefail\n' + '\n'.join(unit['commands']) + '\n'
            argv = (wrap(unit) if wrap else []) + ['bash', '-c', script]
            start = time.monotonic()
            result = subprocess.run(argv, cwd=ROOT, env=dict(os.environ, JAVA_TESTS=java_dir))
            record.append({'id': unit['id'], 'seconds': round(time.monotonic() - start, 2), 'exit': result.returncode,
                           'command': unit['commands'][0][:120]})
            if result.returncode:
                status = 1
                break
    finally:
        shutil.rmtree(java_dir, ignore_errors=True)
    if timings:
        Path(timings).write_text(json.dumps(record, indent=1) + '\n')
    return status, record


def trace(suite_units):
    """Record, per unit, the tracked repository files it opens (strace), and write the map."""
    if not shutil.which('strace'):
        raise SystemExit('trace needs strace')
    files, root = tracked(), str(ROOT) + '/'
    result, tmp = {}, Path(tempfile.mkdtemp(prefix='test-select-trace-'))
    try:
        for unit in suite_units:
            log = tmp/f'{unit["id"]}.strace'
            wrap = lambda u, log=log: ['strace', '-f', '-qq', '-e', 'trace=open,openat,execve,stat,newfstatat', '-o', str(log)]
            status, _ = run_units({unit['id']}, suite_units, wrap=wrap)
            if status:
                raise SystemExit(f'unit {unit["id"]} failed while tracing; fix the suite first')
            opened = set()
            for path in re.findall(r'"([^"]+)"', log.read_text(errors='replace')):
                rel = path[len(root):] if path.startswith(root) else path
                rel = os.path.normpath(rel)
                if rel in files:
                    opened.add(rel)
            result[unit['key']] = {'id': unit['id'], 'command': unit['commands'][0][:120], 'inputs': sorted(opened)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    MAP.write_text(json.dumps({'schemaVersion': 1, 'generatedAt': head, 'units': result}, indent=1, sort_keys=True) + '\n')
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    sub.add_parser('trace')
    for name in ('select', 'run'):
        p = sub.add_parser(name)
        p.add_argument('files', nargs='*')
        p.add_argument('--base', default='origin/main')
        if name == 'run':
            p.add_argument('--all', action='store_true')
            p.add_argument('--timings')
    a = ap.parse_args(argv)
    suite_units = units()
    if a.command == 'list':
        for u in suite_units:
            print(u['id'], u['commands'][0][:140])
        return 0
    if a.command == 'trace':
        print(json.dumps({'units': len(trace(suite_units)), 'map': str(MAP.relative_to(ROOT))}))
        return 0
    if a.command == 'run' and a.all:
        chosen = {'mode': 'full', 'units': [u['id'] for u in suite_units]}
    else:
        chosen = select(a.files or changed_files(a.base), suite_units, base_suite=suite_at(a.base))
    if a.command == 'select':
        print(json.dumps(chosen, indent=1))
        return 0
    start = time.monotonic()
    status, record = run_units(set(chosen['units']), suite_units, a.timings)
    print(json.dumps({'mode': chosen['mode'], 'ran': len(record), 'of': len(suite_units), 'result': 'fail' if status else 'pass',
                      'seconds': round(time.monotonic() - start, 1)}))
    return status


if __name__ == '__main__':
    sys.exit(main())
