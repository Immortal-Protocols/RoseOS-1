"""systemui-component-update.py against a scripted fake emulator: no adb, emulator, APK signing or phone."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('systemui_component_update', SCRIPTS/'systemui-component-update.py')
su = importlib.util.module_from_spec(spec)
spec.loader.exec_module(su)

PLATFORM = 'ab' * 32
APK = b'synthetic SystemUI v2 bytes'

def receipt(version, cert=PLATFORM, package='com.android.systemui', shared='android.uid.systemui', persistent=True,
        apk=APK):
    return {'schema': 'rose-systemui-identity/1', 'apk': {'path': 'SYSTEM_EXT/priv-app/SystemUI/SystemUI.apk',
        'sha256': hashlib.sha256(apk).hexdigest()}, 'package': package, 'versionCode': version,
        'sharedUserId': {'state': 'present', 'value': shared} if shared else {'state': 'absent'},
        'persistent': {'state': 'present', 'value': persistent}, 'signingCertificateSha256': cert}

def dumpsys(code_path, version, updated):
    flags = 'SYSTEM HAS_CODE PERSISTENT' + (' UPDATED_SYSTEM_APP' if updated else '')
    return (f'Packages:\n  Package [com.android.systemui] (abc):\n    codePath={code_path}\n'
        f'    versionCode={version} minSdk=35 targetSdk=36\n    pkgFlags=[ {flags} ]\n  Package [other] (d):\n'
        '    codePath=/data/app/other\n    versionCode=1\n')

class FakeEmulator:
    """A scripted emulator: staged installs apply at the next reboot unless told to fail."""
    def __init__(self, qemu='1', stage_ok=True, activation='applies', boot_after_update=True, recover_ok=True):
        self.qemu, self.stage_ok, self.activation = qemu, stage_ok, activation
        self.boot_after_update, self.recover_ok = boot_after_update, recover_ok
        self.code, self.version, self.updated, self.running, self.booted = '/system_ext/priv-app/SystemUI', 36, False, True, True
        self.sessions, self.calls = [], []

    def __call__(self, argv, capture_output, text, timeout):
        args = argv[3:]
        self.calls.append(args)
        out, code = '', 0
        if args[:2] == ['shell', 'getprop']:
            out = {'ro.kernel.qemu': self.qemu, 'ro.boot.qemu': '', 'sys.boot_completed': '1' if self.booted else ''}.get(args[2], '')
        elif args[:3] == ['shell', 'dumpsys', 'package']:
            out = dumpsys(self.code, self.version, self.updated)
        elif args[:2] == ['shell', 'pidof']:
            code = 0 if self.running else 1
            out = '1234\n' if self.running else ''
        elif args[:3] == ['shell', 'pm', 'get-stagedsessions']:
            out = ''.join(f'sessionId = {s["id"]}; appPackageName = com.android.systemui; isStagedSessionReady = '
                f'{str(s["ready"]).lower()}; isStagedSessionApplied = {str(s["applied"]).lower()}; '
                f'isStagedSessionFailed = false;\n' for s in self.sessions)
        elif args[0] == 'install':
            if self.stage_ok:
                self.sessions.append({'id': 77, 'ready': True, 'applied': False})
                out = 'Success. Reboot device to apply staged session\n'
            else:
                code, out = 1, 'Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match]\n'
        elif args[:3] == ['shell', 'pm', 'install-abandon']:
            self.sessions = [s for s in self.sessions if str(s['id']) != args[3]]
        elif args[0] == 'reboot':
            pending = [s for s in self.sessions if s['ready'] and not s['applied']]
            if pending:
                if not self.boot_after_update:
                    self.booted = False
                    self.boot_after_update = True  # the recovery boot works
                elif self.activation == 'applies':
                    self.code, self.version, self.updated = '/data/app/~~x/com.android.systemui-y', 37, True
                    pending[0]['applied'] = True
                elif self.activation == 'crashes':
                    self.code, self.version, self.updated, self.running = '/data/app/~~x/com.android.systemui-y', 37, True, False
                    pending[0]['applied'] = True
            else:
                self.booted = True
        elif args[:3] == ['shell', 'pm', 'uninstall-system-updates']:
            if self.recover_ok:
                self.code, self.version, self.updated, self.running = '/system_ext/priv-app/SystemUI', 36, False, True
                self.booted = True
            else:
                code, out = 1, 'Failure\n'
        return subprocess.CompletedProcess(argv, code, out, '')

class DecideTest(unittest.TestCase):
    def test_same_signer_same_shared_user_newer_or_equal_is_staged(self):
        decision = su.decide(receipt(37), receipt(36, apk=b'image'))
        self.assertEqual((decision['route'], decision['reinstall']), ('staged', False))
        self.assertTrue(su.decide(receipt(36), receipt(36, apk=b'image'))['reinstall'])

    def test_wrong_identity_signer_or_downgrade_is_refused(self):
        for candidate, expect in ((receipt(37, cert='cd' * 32), 'another certificate'),
                (receipt(35), 'lower than installed'),
                (receipt(37, package='dev.pixel.voice'), 'not com.android.systemui'),
                (receipt(37, shared=None), 'sharedUserId'),
                (receipt(37, shared='android.uid.system'), 'sharedUserId'),
                (receipt(37, persistent=False), 'not the persistent SystemUI'),
                ({'schema': 'other'}, 'not a rose-systemui-identity/1')):
            with self.subTest(expect=expect), self.assertRaises(su.InvalidInput) as caught:
                su.decide(candidate, receipt(36, apk=b'image'))
            self.assertIn(expect, str(caught.exception))

class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='systemui-update-'))
        self.addCleanup(__import__('shutil').rmtree, self.tmp, True)
        self.decision = su.decide(receipt(37), receipt(36, apk=b'image'))

    def run_apply(self, emulator):
        device = su.Device('adb', 'emulator-5580', emulator, sleep=lambda s: None, clock=iter(range(0, 10**6, 10)).__next__)
        outcome = su.apply(device, self.tmp/'v2.apk', self.decision, self.tmp, boot_budget=60)
        return outcome, json.loads((self.tmp/'result.json').read_text())

    def test_staged_replacement_activates_after_reboot(self):
        emulator = FakeEmulator()
        outcome, result = self.run_apply(emulator)
        self.assertEqual(outcome, 'verified')
        self.assertEqual(result['stages']['verify']['state']['versionCode'], 37)
        self.assertEqual(result['stages']['recover']['result'], 'not-run')
        self.assertIn(['install', '--staged', str(self.tmp/'v2.apk')], emulator.calls)
        self.assertNotIn(['install', str(self.tmp/'v2.apk')], emulator.calls, 'never an ordinary install')

    def test_a_crashing_replacement_is_recovered_to_the_image_copy(self):
        emulator = FakeEmulator(activation='crashes')
        outcome, result = self.run_apply(emulator)
        self.assertEqual(outcome, 'recovered')
        self.assertIn('no SystemUI process', result['stages']['verify']['reason'])
        self.assertEqual(result['stages']['recover']['state']['codePath'], '/system_ext/priv-app/SystemUI')
        self.assertIn(['shell', 'pm', 'uninstall-system-updates', 'com.android.systemui'], emulator.calls)

    def test_an_update_that_does_not_apply_or_does_not_boot_is_recovered(self):
        outcome, result = self.run_apply(FakeEmulator(activation='ignored'))
        self.assertEqual(outcome, 'recovered')
        self.assertIn('not an update under /data/app', result['stages']['verify']['reason'])
        outcome, result = self.run_apply(FakeEmulator(boot_after_update=False))
        self.assertEqual((outcome, result['stages']['activate']['result']), ('recovered', 'fail'))
        self.assertEqual(result['stages']['recover']['abandonedSessions'], [77], 'the unapplied session cannot apply later')

    def test_a_failed_recovery_is_reported(self):
        outcome, result = self.run_apply(FakeEmulator(activation='crashes', recover_ok=False))
        self.assertEqual(outcome, 'recovery-failed')
        self.assertEqual(result['stages']['recover']['result'], 'fail')

    def test_a_refused_stage_never_reboots(self):
        emulator = FakeEmulator(stage_ok=False)
        outcome, result = self.run_apply(emulator)
        self.assertEqual(outcome, 'refused')
        self.assertIn('signatures do not match', result['stages']['stage']['reason'])
        self.assertNotIn(['reboot'], emulator.calls)

    def test_a_phone_or_unprepared_device_is_refused_before_anything(self):
        emulator = FakeEmulator(qemu='')
        outcome, result = self.run_apply(emulator)
        self.assertEqual(outcome, 'refused')
        self.assertIn('never acts on a phone', result['stages']['preflight']['reason'])
        self.assertFalse(any(c[0] in ('install', 'reboot') for c in emulator.calls))
        busy = FakeEmulator()
        busy.sessions.append({'id': 5, 'ready': True, 'applied': False})
        self.assertIn('already pending', self.run_apply(busy)[1]['stages']['preflight']['reason'])
        other = FakeEmulator()
        other.version = 35
        self.assertIn('not the INST receipt version', self.run_apply(other)[1]['stages']['preflight']['reason'])

class CliTest(unittest.TestCase):
    def test_apply_needs_the_exact_candidate_bytes_and_a_new_evidence_dir(self):
        tmp = Path(tempfile.mkdtemp(prefix='systemui-cli-'))
        self.addCleanup(__import__('shutil').rmtree, tmp, True)
        (tmp/'c.json').write_text(json.dumps(receipt(37)))
        (tmp/'i.json').write_text(json.dumps(receipt(36, apk=b'image')))
        (tmp/'v2.apk').write_bytes(b'other bytes')
        args = ['apply', '--candidate', str(tmp/'c.json'), '--installed', str(tmp/'i.json'), '--serial', 'emulator-5580',
            '--apk', str(tmp/'v2.apk'), '--evidence', str(tmp/'ev')]
        self.assertEqual(su.main(args, runner=FakeEmulator()), 2)
        (tmp/'v2.apk').write_bytes(APK)
        self.assertEqual(su.main(args, runner=FakeEmulator()), 0)
        self.assertEqual(su.main(args, runner=FakeEmulator()), 2, 'evidence is never overwritten')
        self.assertEqual(su.main(['check', '--candidate', str(tmp/'c.json'), '--installed', str(tmp/'i.json')]), 0)

    def test_the_tool_has_no_root_remount_verity_signing_or_self_update_path(self):
        code = (SCRIPTS/'systemui-component-update.py').read_text()
        for banned in ("'root'", "'remount'", 'disable-verity', 'apksigner', 'sign(', 'dev.pixel.voice"', 'sudo'):
            self.assertNotIn(banned, code, banned)

if __name__ == '__main__':
    unittest.main()
