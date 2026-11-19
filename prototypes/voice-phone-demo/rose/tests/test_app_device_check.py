"""rose.app_builder.device_check against a scripted adb: what it refuses before touching a device, and what each step
needs from the device. No device or emulator runs here."""
import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import device_check, starter_pilots
from rose.app_builder.device_check import Adb, Refused

PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 32

# A fake adb: reads its behaviour from $FAKE_ADB_STATE (JSON) and appends each call to $FAKE_ADB_LOG.
FAKE_ADB = r'''#!/usr/bin/env python3
import json, os, re, sys
state = json.load(open(os.environ['FAKE_ADB_STATE']))
args = sys.argv[1:]
assert args[:2] == ['-s', state['serial']], args
args = args[2:]
with open(os.environ['FAKE_ADB_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\n')
def out(text, code=0):
    sys.stdout.write(text); sys.exit(code)
installed = state.setdefault('installed', {})
def save():
    json.dump(state, open(os.environ['FAKE_ADB_STATE'], 'w'))
if args == ['get-state']:
    out(state.get('deviceState', 'device') + '\n')
if args[0] == 'install':
    pkg = state['apkPackages'][os.path.basename(args[-1])]
    if pkg in state.get('installFails', []):
        out('Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]\n', 1)
    installed[pkg] = state.get('installedVersion', {}).get(pkg, state['versions'][pkg]); save()
    out('Performing Streamed Install\nSuccess\n')
if args[0] == 'uninstall':
    installed.pop(args[1], None); save(); out('Success\n')
if args[:3] == ['exec-out', 'screencap', '-p']:
    bad = state.get('top', '').split('/')[0] in state.get('pngFails', [])
    sys.stdout.buffer.write(state.get('screencapStderr', '').encode())  # exec-out merges stderr into the stream
    sys.stdout.buffer.write(b'' if bad else bytes.fromhex(state.get('png', ''))); sys.exit(0)
assert args[0] == 'shell', args
import shlex
cmd = ' '.join(shlex.split(args[1]))
if cmd.startswith('getprop '):
    out(state.get('props', {}).get(cmd.split()[1], '') + '\n')
if cmd.startswith('dumpsys package '):
    pkg = cmd.split()[2]
    out(f'Packages:\n  Package [{pkg}]\n    versionCode={installed[pkg]} minSdk=26 targetSdk=37\n' if pkg in installed else '')
if cmd.startswith('am start -W -n '):
    comp = cmd.split()[-1]; pkg = comp.split('/')[0]
    if pkg in state.get('launchFails', []):
        out(f'Starting: Intent {{ cmp={comp} }}\nError: Activity class {{{comp}}} does not exist.\n', 1)
    state['top'] = comp; save()
    out(f'Starting: Intent {{ cmp={comp} }}\nStatus: ok\nLaunchState: COLD\nActivity: {comp}\nTotalTime: 412\nWaitTime: 420\nComplete\n')
if cmd.startswith('pidof '):
    pkg = cmd.split()[1]
    out('' if pkg in state.get('dies', []) else '4242\n')
if cmd == 'dumpsys activity activities':
    top = state.get('top', 'com.android.launcher3/.Launcher')
    if top.split('/')[0] in state.get('notResumed', []):
        top = 'com.android.launcher3/.Launcher'
    out(f'ACTIVITY MANAGER ACTIVITIES\n  topResumedActivity=ActivityRecord{{1a2b u0 {top} t7}}\n'
        f'  ResumedActivity: ActivityRecord{{1a2b u0 {top} t7}}\n')
if cmd == 'logcat -b crash -c':
    out('')
if cmd == 'logcat -b crash -d':
    pkg = state.get('top', '').split('/')[0]
    out(f'--------- beginning of crash\nE AndroidRuntime: FATAL EXCEPTION: main\nE AndroidRuntime: Process: {pkg}, PID: 4242\n'
        if pkg in state.get('crashes', []) else '')
if cmd.startswith('am force-stop '):
    out('')
out('unknown\n', 1)
'''


class DeviceCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='rose-device-check-'))
        self.addCleanup(__import__('shutil').rmtree, self.tmp)
        self.adb = self.tmp / 'adb'
        self.adb.write_text(FAKE_ADB.replace('/usr/bin/env python3', sys.executable, 1))
        self.adb.chmod(self.adb.stat().st_mode | stat.S_IXUSR)
        self.state_file, self.log_file = self.tmp / 'state.json', self.tmp / 'calls.log'
        os.environ['FAKE_ADB_STATE'], os.environ['FAKE_ADB_LOG'] = str(self.state_file), str(self.log_file)
        self.addCleanup(os.environ.pop, 'FAKE_ADB_STATE', None)
        self.addCleanup(os.environ.pop, 'FAKE_ADB_LOG', None)
        self.pilots = self.tmp / 'pilots'
        self.records = self.write_pilots([('feed', 'minimal'), ('player', 'material3')])
        self.state({})

    def write_pilots(self, pairs, label=starter_pilots.LABEL):
        (self.pilots / 'apks').mkdir(parents=True)
        records, sums = [], []
        for i, (template, theme) in enumerate(pairs):
            apk = self.pilots / 'apks' / f'{template}-{theme}.apk'
            apk.write_bytes(f'apk {template} {theme}'.encode())
            digest = hashlib.sha256(apk.read_bytes()).hexdigest()
            records.append({'template': template, 'theme': theme, 'label': label, 'state': 'ready',
                            'apk': f'apks/{apk.name}', 'apkSha256': digest, 'packageName': f'app.rose.p{i}',
                            'versionCode': 1, 'signerSha256': f'{i:064x}', 'launchableActivity': f'app.rose.p{i}.MainActivity',
                            'minSdk': 26, 'targetSdk': 37})
            sums.append(f'{digest}  apks/{apk.name}\n')
        manifest = {'schemaVersion': starter_pilots.SCHEMA_VERSION, 'label': starter_pilots.LABEL, 'pilots': records}
        self.write_manifest(manifest, sums)
        return records

    def write_manifest(self, manifest, sums):
        data = (json.dumps(manifest, indent=2) + '\n').encode()
        (self.pilots / 'pilots.json').write_bytes(data)
        (self.pilots / 'SHA256SUMS').write_text(''.join(sums) + f'{hashlib.sha256(data).hexdigest()}  pilots.json\n')

    def state(self, extra):
        base = {'serial': 'emulator-5580', 'png': PNG.hex(),
                'apkPackages': {Path(r['apk']).name: r['packageName'] for r in self.records},
                'versions': {r['packageName']: r['versionCode'] for r in self.records},
                'props': {'ro.build.version.sdk': '35', 'ro.product.cpu.abi': 'x86_64'}}
        self.state_file.write_text(json.dumps(base | extra))

    def check(self, **kwargs):
        return device_check.check(self.pilots, self.tmp / 'evidence', Adb(str(self.adb), 'emulator-5580'),
                                  settle_s=0, **kwargs)

    def calls(self):
        return [json.loads(l) for l in self.log_file.read_text().splitlines()] if self.log_file.exists() else []

    def test_every_pilot_installs_launches_runs_is_resumed_and_is_captured(self):
        evidence = self.check()
        self.assertTrue(evidence['passed'])
        self.assertEqual(evidence['label'], starter_pilots.LABEL)
        self.assertEqual(evidence['device']['sdk'], '35')
        for pilot in evidence['pilots']:
            self.assertTrue(pilot['passed'], pilot)
            self.assertEqual(set(pilot['steps']), {'install', 'launch', 'running', 'resumed', 'screenshot', 'crash'})
            self.assertEqual((self.tmp / 'evidence' / pilot['steps']['screenshot']['file']).read_bytes(), PNG)
        saved = json.loads((self.tmp / 'evidence' / 'evidence.json').read_text())
        self.assertEqual(saved['passed'], True)
        self.assertEqual(json.loads(self.state_file.read_text())['installed'], {})  # uninstalled afterwards
        self.assertIn(['shell', 'am start -W -n app.rose.p0/app.rose.p0.MainActivity'], self.calls())

    def test_a_multi_display_warning_before_the_image_is_kept_as_a_warning(self):
        warning = ('[Warning] Multiple displays were found, but no display id was specified! '
                   'Defaulting to the first display found.\n')
        self.state({'screencapStderr': warning})
        evidence = self.check()
        self.assertTrue(evidence['passed'], evidence)
        shot = evidence['pilots'][0]['steps']['screenshot']
        self.assertEqual((self.tmp / 'evidence' / shot['file']).read_bytes(), PNG)
        self.assertIn('Multiple displays', shot['warning'])

    def test_keep_leaves_the_pilots_installed(self):
        self.check(keep=True)
        self.assertEqual(set(json.loads(self.state_file.read_text())['installed']), {'app.rose.p0', 'app.rose.p1'})

    def test_each_failing_step_fails_only_that_pilot_and_says_why(self):
        for extra, step in (({'installFails': ['app.rose.p0']}, 'install'),
                            ({'installedVersion': {'app.rose.p0': 7}}, 'install'),
                            ({'launchFails': ['app.rose.p0']}, 'launch'),
                            ({'dies': ['app.rose.p0']}, 'running'),
                            ({'notResumed': ['app.rose.p0']}, 'resumed'),
                            ({'pngFails': ['app.rose.p0']}, 'screenshot'),
                            ({'crashes': ['app.rose.p0']}, 'crash')):
            with self.subTest(step=step, extra=extra):
                self.state(extra)
                evidence = device_check.check(self.pilots, self.tmp / f'evidence-{len(self.calls())}',
                                              Adb(str(self.adb), 'emulator-5580'), settle_s=0)
                self.assertFalse(evidence['passed'])
                first, second = evidence['pilots']
                self.assertFalse(first['passed'])
                self.assertFalse(first['steps'][step]['ok'])
                self.assertTrue(second['passed'], second)
                if step != 'install':
                    self.assertIn(['shell', 'am force-stop app.rose.p0'], self.calls())
                self.assertNotIn('app.rose.p0', json.loads(self.state_file.read_text()).get('installed', {}))

    def test_a_device_that_is_not_ready_is_refused_before_anything_is_installed(self):
        self.state({'deviceState': 'offline'})
        with self.assertRaises(Refused):
            self.check()
        self.assertEqual(self.calls(), [['get-state']])

    def test_pilots_that_do_not_match_their_digests_or_label_are_refused_before_adb_runs(self):
        cases = []
        cases.append(lambda: (self.pilots / 'apks' / 'feed-minimal.apk').write_bytes(b'tampered'))
        cases.append(lambda: (self.pilots / 'pilots.json').write_text('{}'))
        cases.append(lambda: (self.pilots / 'SHA256SUMS').unlink())

        def relabel():
            manifest = json.loads((self.pilots / 'pilots.json').read_text())
            manifest['pilots'][0]['label'] = 'writer-generated'
            sums = [l + '\n' for l in (self.pilots / 'SHA256SUMS').read_text().splitlines() if 'pilots.json' not in l]
            self.write_manifest(manifest, sums)
        cases.append(relabel)

        def escape():
            manifest = json.loads((self.pilots / 'pilots.json').read_text())
            manifest['pilots'][0]['apk'] = '../outside.apk'
            sums = [l + '\n' for l in (self.pilots / 'SHA256SUMS').read_text().splitlines() if 'pilots.json' not in l]
            self.write_manifest(manifest, sums)
        cases.append(escape)

        def inject():
            manifest = json.loads((self.pilots / 'pilots.json').read_text())
            manifest['pilots'][0]['launchableActivity'] = 'x; reboot'
            sums = [l + '\n' for l in (self.pilots / 'SHA256SUMS').read_text().splitlines() if 'pilots.json' not in l]
            self.write_manifest(manifest, sums)
        cases.append(inject)
        for i, spoil in enumerate(cases):
            with self.subTest(case=i):
                __import__('shutil').rmtree(self.pilots)
                self.records = self.write_pilots([('feed', 'minimal'), ('player', 'material3')])
                spoil()
                with self.assertRaises(Refused):
                    self.check()
                self.assertEqual(self.calls(), [])

    def test_pilots_that_were_not_ready_are_skipped(self):
        manifest = json.loads((self.pilots / 'pilots.json').read_text())
        manifest['pilots'].append({'template': 'sprite-game', 'theme': 'minimal', 'label': starter_pilots.LABEL,
                                   'state': 'failed', 'failure': {'message': 'no'}})
        sums = [l + '\n' for l in (self.pilots / 'SHA256SUMS').read_text().splitlines() if 'pilots.json' not in l]
        self.write_manifest(manifest, sums)
        evidence = self.check()
        self.assertEqual(len(evidence['pilots']), 2)

    def test_the_command_line_reports_and_exits_by_outcome(self):
        out = self.tmp / 'cli-evidence'
        self.assertEqual(device_check.main(['--pilots', str(self.pilots), '--serial', 'emulator-5580',
                                            '--evidence', str(out), '--adb', str(self.adb)]), 0)
        self.assertEqual(device_check.main(['--pilots', str(self.tmp / 'none'), '--serial', 'emulator-5580',
                                            '--evidence', str(self.tmp / 'e2'), '--adb', str(self.adb)]), 2)


class StarterPilotsUnitTest(unittest.TestCase):
    BADGING = ("package: name='app.rose.p0' versionCode='1' versionName='1.0'\nsdkVersion:'26'\n"
               "targetSdkVersion:'37'\nlaunchable-activity: name='app.rose.p0.MainActivity'  label='Feed' icon=''\n")

    def test_badging_facts(self):
        self.assertEqual(starter_pilots.launchable(self.BADGING), 'app.rose.p0.MainActivity')
        self.assertEqual(starter_pilots.sdk_levels(self.BADGING), (26, 37))
        self.assertIsNone(starter_pilots.launchable('package: name=x'))

    def test_a_pilot_named_twice_is_refused_before_anything_is_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'out'
            with self.assertRaises(ValueError):
                starter_pilots.build(out, [('feed', 'minimal'), ('feed', 'minimal')], None, None, None)
            self.assertFalse(out.exists())

    def test_pilot_names_are_checked_against_the_catalog(self):
        from rose.app_builder import starters
        if not starters.available():
            self.skipTest('this checkout has no workspace/rose-starters')
        catalog = starters.Starters.load()
        self.assertEqual(len(starter_pilots.all_pilots(catalog)), 9)
        self.assertEqual(starter_pilots.parse_pilot('feed:minimal', catalog), ('feed', 'minimal'))
        for bad in ('feed', 'feed:nothing', 'nothing:minimal'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                starter_pilots.parse_pilot(bad, catalog)


if __name__ == '__main__':
    unittest.main()
