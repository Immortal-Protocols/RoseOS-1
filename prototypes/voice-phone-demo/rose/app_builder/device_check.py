"""Install and launch starter pilots (rose.app_builder.starter_pilots) on one Android device or emulator over adb,
and record what happened.

    python -m rose.app_builder.device_check --pilots DIR --serial SERIAL --evidence OUT [--adb ADB] [--keep]

DIR is starter_pilots' output: pilots.json, SHA256SUMS and apks/. OUT must not exist. It receives evidence.json and
one screenshot per launched pilot. For each ready pilot this:

1. checks the APK against SHA256SUMS and pilots.json, and refuses any record whose label is not starter_pilots'
   (nothing here can turn a compiled starter tree into a writer-generated app);
2. installs it (``adb install -r``) and reads the installed version back from ``dumpsys package``;
3. launches its launchable activity with ``am start -W`` and needs Status ok;
4. needs its process running (``pidof``) and its activity resumed (``dumpsys activity activities``), after a settle;
5. takes a screenshot (``exec-out screencap -p``) and reads the crash buffer for its package;
6. force-stops it and, unless --keep, uninstalls it.

This module only talks to the device adb already reaches. It boots nothing, changes no device or host permission
and installs nothing but the pilots. Whoever runs it owns that device (for the hosted emulator in
systemui-persistent-experiment.yml that is an owner-approved dispatch, see docs/APP-BUILDER.md §5). Exit status: 0
only when every pilot passed every step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from .starter_pilots import LABEL, SCHEMA_VERSION

MAX_TEXT = 2000  # bounded tool output kept in the evidence
SETTLE_S = 3.0
PACKAGE = re.compile(r'[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+')
ACTIVITY = re.compile(r'\.?[A-Za-z][A-Za-z0-9_$]*(\.[A-Za-z][A-Za-z0-9_$]*)*')


class Refused(Exception):
    """The pilots directory is not starter_pilots' output, or does not match its own digests."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _bounded(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + f'... ({len(text) - MAX_TEXT} more characters)'


def load(pilots_dir: Path) -> list[dict]:
    """The ready pilots, each checked against SHA256SUMS and its own record. Raises Refused."""
    pilots_dir = Path(pilots_dir)
    try:
        sums = {}
        for line in (pilots_dir / 'SHA256SUMS').read_text().splitlines():
            digest, _, path = line.partition('  ')
            if not re.fullmatch(r'[0-9a-f]{64}', digest) or not path or path in sums:
                raise Refused(f'SHA256SUMS has a malformed line: {line[:80]!r}')
            sums[path] = digest
        manifest_bytes = (pilots_dir / 'pilots.json').read_bytes()
    except OSError as error:
        raise Refused(f'{pilots_dir} is not starter_pilots output: {error}') from None
    if hashlib.sha256(manifest_bytes).hexdigest() != sums.get('pilots.json'):
        raise Refused('pilots.json does not match SHA256SUMS')
    manifest = json.loads(manifest_bytes)
    if manifest.get('schemaVersion') != SCHEMA_VERSION or manifest.get('label') != LABEL:
        raise Refused('pilots.json is not a starter_pilots manifest of this version')
    ready = []
    for record in manifest.get('pilots', []):
        if record.get('label') != LABEL:
            raise Refused(f'a pilot is labelled {record.get("label")!r}, not {LABEL!r}')
        if record.get('state') != 'ready':
            continue
        apk = record.get('apk', '')
        if not re.fullmatch(r'apks/[a-z0-9-]+\.apk', apk):
            raise Refused(f'a pilot names an unexpected APK path {apk!r}')
        if not PACKAGE.fullmatch(record.get('packageName', '')) or not isinstance(record.get('versionCode'), int):
            raise Refused(f'{apk} has no valid package or versionCode')
        activity = record.get('launchableActivity')
        if not isinstance(activity, str) or not ACTIVITY.fullmatch(activity):
            raise Refused(f'{apk} has no valid launchable activity')
        if sums.get(apk) != record.get('apkSha256') or _sha256(pilots_dir / apk) != record['apkSha256']:
            raise Refused(f'{apk} does not match its digest')
        ready.append(record | {'path': pilots_dir / apk})
    return ready


class Adb:
    def __init__(self, adb: str, serial: str, timeout_s: float = 120.0):
        self.base, self.timeout_s = [adb, '-s', serial], timeout_s

    def run(self, *args, binary=False, timeout_s=None) -> tuple[int, str | bytes]:
        try:
            done = subprocess.run([*self.base, *args], capture_output=True, timeout=timeout_s or self.timeout_s,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return 124, b'' if binary else f'timed out: adb {" ".join(args)}'
        if binary:
            return done.returncode, done.stdout
        return done.returncode, (done.stdout + done.stderr).decode('utf-8', 'replace')

    def shell(self, command: str, timeout_s=None) -> tuple[int, str]:
        return self.run('shell', command, timeout_s=timeout_s)


def _component(package: str, activity: str) -> str:
    return f'{package}/{activity}'


def check_one(adb: Adb, pilot: dict, evidence_dir: Path, keep: bool, settle_s: float) -> dict:
    package, activity = pilot['packageName'], pilot['launchableActivity']
    name = Path(pilot['apk']).stem
    result = {k: pilot[k] for k in ('template', 'theme', 'label', 'packageName', 'versionCode', 'apkSha256',
                                    'signerSha256', 'launchableActivity')}
    steps = result['steps'] = {}

    def failed(step, detail):
        steps[step] = {'ok': False, 'detail': _bounded(detail)}
        result['passed'] = False
        return result

    code, out = adb.run('install', '-r', str(pilot['path']), timeout_s=300)
    if code != 0 or 'Success' not in out:
        return failed('install', out)
    try:
        _, dump = adb.shell(f'dumpsys package {package}')
        found = re.search(r'versionCode=(\d+)', dump)
        if not found or int(found.group(1)) != pilot['versionCode']:
            return failed('install', f'installed versionCode is {found.group(1) if found else "missing"}, '
                                     f'expected {pilot["versionCode"]}')
        steps['install'] = {'ok': True}
        adb.shell('logcat -b crash -c')
        code, out = adb.shell(f'am start -W -n {_component(package, activity)}', timeout_s=120)
        status = re.search(r'^Status: (\S+)', out, re.M)
        total = re.search(r'^TotalTime: (\d+)', out, re.M)
        if code != 0 or not status or status.group(1) != 'ok' or 'Error' in out:
            return failed('launch', out)
        steps['launch'] = {'ok': True, 'totalTimeMs': int(total.group(1)) if total else None}
        time.sleep(settle_s)
        _, pid = adb.shell(f'pidof {package}')
        if not re.fullmatch(r'\d+( \d+)*', pid.strip()):
            return failed('running', f'no process for {package}: {pid}')
        steps['running'] = {'ok': True, 'pid': int(pid.split()[0])}
        _, activities = adb.shell('dumpsys activity activities')
        resumed = re.findall(r'(?:topResumedActivity|mResumedActivity|ResumedActivity)[:=].*?\s(\S+/\S+)', activities)
        if not any(r.split('/')[0] == package for r in resumed):
            return failed('resumed', 'resumed: ' + (', '.join(resumed) or 'none'))
        steps['resumed'] = {'ok': True}
        code, png = adb.run('exec-out', 'screencap', '-p', binary=True)
        if code != 0 or not png.startswith(b'\x89PNG\r\n\x1a\n'):
            return failed('screenshot', f'screencap exited {code} with {len(png)} bytes')
        shot = evidence_dir / f'{name}.png'
        shot.write_bytes(png)
        steps['screenshot'] = {'ok': True, 'file': shot.name, 'sha256': hashlib.sha256(png).hexdigest()}
        _, crashes = adb.shell('logcat -b crash -d')
        if package in crashes:
            return failed('crash', crashes)
        steps['crash'] = {'ok': True}
        result['passed'] = True
        return result
    finally:
        adb.shell(f'am force-stop {package}')
        if not keep:
            adb.run('uninstall', package)


def device(adb: Adb) -> dict:
    facts = {}
    for key, prop in (('sdk', 'ro.build.version.sdk'), ('release', 'ro.build.version.release'),
                      ('abi', 'ro.product.cpu.abi'), ('fingerprint', 'ro.build.fingerprint')):
        code, out = adb.shell(f'getprop {prop}')
        facts[key] = _bounded(out.strip()) if code == 0 else None
    return facts


def check(pilots_dir: Path, evidence_dir: Path, adb: Adb, *, keep=False, settle_s=SETTLE_S) -> dict:
    pilots = load(pilots_dir)
    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir()
    code, state = adb.run('get-state', timeout_s=30)
    if code != 0 or state.strip() != 'device':
        raise Refused(f'adb does not reach a ready device: {_bounded(state.strip())}')
    evidence = {'schemaVersion': 1, 'label': LABEL, 'device': device(adb), 'pilots': []}
    for pilot in pilots:
        evidence['pilots'].append(check_one(adb, pilot, evidence_dir, keep, settle_s))
    evidence['passed'] = bool(evidence['pilots']) and all(p['passed'] for p in evidence['pilots'])
    (evidence_dir / 'evidence.json').write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n')
    return evidence


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='python -m rose.app_builder.device_check',
                                     description=__doc__.split('\n\n')[0])
    parser.add_argument('--pilots', required=True, type=Path)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--adb', default='adb')
    parser.add_argument('--keep', action='store_true', help='leave the pilots installed')
    args = parser.parse_args(argv)
    if args.evidence.exists():
        parser.error(f'{args.evidence} exists')
    try:
        evidence = check(args.pilots, args.evidence, Adb(args.adb, args.serial), keep=args.keep)
    except Refused as error:
        print(f'device_check: refused: {error}', file=sys.stderr)
        return 2
    print(json.dumps({'passed': evidence['passed'], 'device': evidence['device'].get('sdk'),
                      'pilots': [{'pilot': f'{p["template"]}-{p["theme"]}', 'passed': p['passed']}
                                 for p in evidence['pilots']], 'label': LABEL}))
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
