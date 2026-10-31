"""SystemUI stages of the emulator experiment against a SYNTHETIC scripted emulator and synthetic aapt/apksigner
output (shapes from scripts/tests/test_systemui_identity.py). Tests the stage logic only; no emulator, adb or key."""
import importlib.util
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import systemui_stages as st  # noqa: E402
import experiment as ex  # noqa: E402

_spec = importlib.util.spec_from_file_location('fake_emulator', ROOT/'scripts/tests/test_systemui_component_update.py')
fake = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake)

CERT = 'c3' * 32
XMLTREE = '\n'.join([
    'N: android=http://schemas.android.com/apk/res/android',
    '  E: manifest (line=17)',
    '    A: android:versionCode(0x0101021b)=(type 0x10)0x24',
    '    A: android:versionName(0x0101021c)="15" (Raw: "15")',
    '    A: android:sharedUserId(0x0101000b)="android.uid.systemui" (Raw: "android.uid.systemui")',
    '    A: package="com.android.systemui" (Raw: "com.android.systemui")',
    '    E: application (line=300)',
    '      A: android:persistent(0x0101000d)=(type 0x12)0xffffffff']) + '\n'
SIGNER = f'Signer #1 certificate DN: CN=Synthetic\nSigner #1 certificate SHA-256 digest: {CERT}\n'

class Emulator(fake.FakeEmulator):
    """The scripted emulator plus pm path, pull, the host tools and a refusal for a foreign signer."""
    def __init__(self, foreign_accepted=False, signer=SIGNER, foreign=None, **kw):
        super().__init__(**kw)
        self.foreign_accepted, self.signer, self.foreign = foreign_accepted, signer, foreign

    def __call__(self, argv, capture_output=True, text=True, timeout=None):
        if Path(argv[0]).name in ('aapt', 'apksigner'):
            if argv[1] == 'sign':
                Path(argv[argv.index('--out') + 1]).write_bytes(b'foreign')
                return subprocess.CompletedProcess(argv, 0, '', '')
            return subprocess.CompletedProcess(argv, 0, XMLTREE if argv[1] == 'dump' else self.signer, '')
        args = argv[3:]
        if args[:3] == ['shell', 'pm', 'path']:
            return subprocess.CompletedProcess(argv, 0, 'package:/system_ext/priv-app/SystemUI/SystemUI.apk\n', '')
        if args[0] == 'pull':
            Path(args[2]).write_bytes(b'image SystemUI')
            return subprocess.CompletedProcess(argv, 0, '', '')
        if args[0] == 'install' and args[-1].endswith('systemui-foreign.apk') and self.foreign:
            # SYNTHETIC outcomes in the android15-release pm shell layouts: (exit, printed line, created session state)
            code, line, state = self.foreign
            self.calls.append(args)
            if state is not None:
                self.sessions.append({'id': 88, 'applied': False, **state})
            return subprocess.CompletedProcess(argv, code, line + '\n', '')
        if args[0] == 'install' and args[-1].endswith('systemui-foreign.apk') and not self.foreign_accepted:
            self.calls.append(args)
            return subprocess.CompletedProcess(argv, 1, '', 'Failure [INSTALL_FAILED_SHARED_USER_INCOMPATIBLE: '
                'Package com.android.systemui has no signatures that match those in shared user android.uid.systemui]\n')
        return super().__call__(argv, capture_output, text, timeout)

class FakeDevice:
    """experiment.Device's interface over the scripted emulator."""
    def __init__(self, emulator):
        self.adb, self.emulator = 'adb', emulator

    def sh(self, *args, timeout=120, check=False):
        result = self.emulator(['adb', '-s', 'emulator-5580', *args], timeout=timeout)
        if check and result.returncode:
            raise ex.Failed('adb failed')
        return result

class SystemUiStagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='systemui-stages-'))
        self.addCleanup(__import__('shutil').rmtree, self.tmp, True)
        (self.tmp/'work').mkdir()
        (self.tmp/'evidence').mkdir()
        (self.tmp/'work/test-only.keystore').write_bytes(b'job-local')

    def run_stages(self, emulator):
        tools = {'aapt': self.tmp/'aapt', 'apksigner': self.tmp/'apksigner'}
        for tool in tools.values():
            tool.write_text('#!/bin/sh\n')
        results = {}
        for name, fn in st.stages(FakeDevice(emulator), tools, self.tmp/'work', self.tmp/'evidence', 'emulator-5580',
                ex.Failed, run=emulator):
            if any(r['result'] != 'pass' for r in results.values()):
                results[name] = {'result': 'not-run'}
                continue
            try:
                results[name] = {'result': 'pass', **(fn() or {})}
            except ex.Failed as error:
                results[name] = {'result': 'fail', 'reason': str(error)}
        return results

    def test_identity_foreign_refusal_staged_reinstall_and_restore(self):
        emulator = Emulator()
        emulator.version = emulator.applied_version = 36  # the receipt says 0x24 = 36; a same-version reinstall
        results = self.run_stages(emulator)
        self.assertEqual({k: v['result'] for k, v in results.items()}, {'systemui-identity': 'pass',
            'systemui-foreign': 'pass', 'systemui-staged': 'pass', 'systemui-restore': 'pass'}, results)
        self.assertEqual(results['systemui-identity']['signingCertificateSha256'], CERT)
        self.assertIn('INSTALL_FAILED_SHARED_USER_INCOMPATIBLE', results['systemui-foreign']['refusal'])
        self.assertTrue(results['systemui-staged']['reinstall'])
        staged = json.loads((self.tmp/'evidence/systemui/result.json').read_text())
        self.assertEqual(staged['outcome'], 'verified')
        self.assertIn(['shell', 'pm', 'uninstall-system-updates', 'com.android.systemui'], emulator.calls)

    def test_the_v3_0_signer_layout_passes_identity_and_keeps_the_foreign_refusal(self):
        # EXP006-SIGNER-006: run 41158887101's apksigner printed "V3.0 Signer: ..." (layout runtime-derived; the
        # certificate here is SYNTHETIC). Identity now passes and the later stages run as for the numbered layout.
        emulator = Emulator(signer=f'V3.0 Signer: certificate DN: CN=Synthetic\n'
            f'V3.0 Signer: certificate SHA-256 digest: {CERT}\n')
        emulator.version = emulator.applied_version = 36
        results = self.run_stages(emulator)
        self.assertEqual({k: v['result'] for k, v in results.items()}, {'systemui-identity': 'pass',
            'systemui-foreign': 'pass', 'systemui-staged': 'pass', 'systemui-restore': 'pass'}, results)
        self.assertEqual(results['systemui-identity']['signingCertificateSha256'], CERT)
        self.assertIn('INSTALL_FAILED_SHARED_USER_INCOMPATIBLE', results['systemui-foreign']['refusal'])
        two = Emulator(signer=f'V3.0 Signer: certificate SHA-256 digest: {CERT}\nSigner #1 certificate SHA-256 digest: {CERT}\n')
        results = self.run_stages(two)
        self.assertEqual((results['systemui-identity']['result'], results['systemui-foreign']['result']),
            ('fail', 'not-run'), 'two signer records still fail identity and stop the later stages')

    def test_an_accepted_foreign_signer_fails_and_stops_the_later_stages(self):
        emulator = Emulator(foreign_accepted=True)
        emulator.version = emulator.applied_version = 36
        results = self.run_stages(emulator)
        self.assertEqual(results['systemui-foreign']['result'], 'fail')
        self.assertIn(['shell', 'pm', 'install-abandon', '77'], emulator.calls, 'the foreign session is abandoned')
        self.assertEqual(results['systemui-staged']['result'], 'not-run')

    def foreign_run(self, foreign, **kw):
        emulator = Emulator(foreign=foreign, **kw)
        emulator.version = emulator.applied_version = 36
        return emulator, self.run_stages(emulator)

    def test_run_41161860755_shape_exit_0_is_a_failure_and_its_session_is_abandoned(self):
        # Exit 0 with pm's ready line and a ready session: the parser now sees it; the stage fails, abandons it, and
        # claims neither acceptance nor activation (no reboot happens).
        emulator, results = self.foreign_run((0, 'Success. Reboot device to apply staged session', {'ready': True}))
        foreign = results['systemui-foreign']
        self.assertEqual(foreign['result'], 'fail')
        self.assertIn('exited 0 (success-ready); not a refusal', foreign['reason'])
        self.assertIn('a session it created was ready', foreign['reason'])
        self.assertIn('Neither acceptance nor activation', foreign['reason'])
        self.assertIn(['shell', 'pm', 'install-abandon', '88'], emulator.calls)
        self.assertNotIn(['reboot'], emulator.calls)
        self.assertEqual(results['systemui-staged']['result'], 'not-run')

    def test_every_outcome_category_and_cleanup(self):
        cases = (
            ('plain success, pending session', (0, 'Success', {'ready': False}), 'fail', 'success', [88]),
            ('verification verdict', (1, 'Error [-110] [INSTALL_FAILED_SHARED_USER_INCOMPATIBLE: x]',
                {'ready': False, 'failed': True}), 'pass', 'verification-error', [88]),
            ('timeout is not a refusal', (1, 'Failure [timed out after 60000 ms]', {'ready': False}), 'fail',
                'timeout', [88]),
            ('refused before a session', (1, 'Failure [INSTALL_FAILED_SHARED_USER_INCOMPATIBLE: x]', None), 'pass',
                'failure', []),
            ('nonzero without a refusal', (1, 'adb: failed to install', None), 'fail', 'other', []),
        )
        for label, foreign, result, category, abandoned in cases:
            with self.subTest(label):
                shutil.rmtree(self.tmp/'evidence')
                (self.tmp/'evidence').mkdir()
                emulator, results = self.foreign_run(foreign)
                stage = results['systemui-foreign']
                self.assertEqual(stage['result'], result, stage)
                image = next((i for i, c in enumerate(emulator.calls) if c[0] == 'install'
                    and c[-1].endswith('systemui-image.apk')), len(emulator.calls))
                foreign_calls = emulator.calls[:image]  # the later stages run only after a pass
                aborted = [c[3] for c in foreign_calls if c[:3] == ['shell', 'pm', 'install-abandon']]
                self.assertEqual(aborted, [str(i) for i in abandoned])
                self.assertNotIn(['reboot'], foreign_calls, 'the foreign stage never reboots')
                self.assertEqual(results['systemui-staged']['result'], 'pass' if result == 'pass' else 'not-run')
                if result == 'pass':
                    self.assertEqual((stage['foreignInstall']['installResult'], stage['foreignInstall']['abandoned'],
                        stage['foreignInstall']['remainingCreated']), (category, abandoned, 0))
                else:
                    self.assertIn(f'({category})', stage['reason'])

    def test_pre_existing_sessions_are_never_abandoned_and_unreadable_state_fails_closed(self):
        emulator = Emulator(foreign=(1, 'Error [-110] [x]', {'ready': False, 'failed': True}))
        emulator.version = emulator.applied_version = 36
        emulator.sessions.append({'id': 5, 'ready': False, 'applied': False, 'failed': True})
        results = self.run_stages(emulator)
        self.assertEqual(results['systemui-foreign']['result'], 'pass')
        aborted = [c[3] for c in emulator.calls if c[:3] == ['shell', 'pm', 'install-abandon']]
        self.assertEqual(aborted, ['88'], 'the pre-existing failed session 5 is not this test\'s')
        self.assertNotIn('5', [c[3] for c in emulator.calls if c[:3] == ['shell', 'pm', 'install-abandon']],
            'nor by the later stages')
        for after in (0, 1):
            with self.subTest(garbled_after=after):
                shutil.rmtree(self.tmp/'evidence')
                (self.tmp/'evidence').mkdir()
                emulator = Emulator(foreign=(0, 'Success. Reboot device to apply staged session', {'ready': True}))
                emulator.version = emulator.applied_version = 36
                emulator.garble_sessions_after = after
                results = self.run_stages(emulator)
                self.assertEqual(results['systemui-foreign']['result'], 'fail')
                self.assertIn('staged session state unreadable', results['systemui-foreign']['reason'])
                self.assertFalse(any(c[:3] == ['shell', 'pm', 'install-abandon'] for c in emulator.calls),
                    'nothing is abandoned blindly')
                if after == 0:
                    self.assertFalse(any(c[0] == 'install' for c in emulator.calls), 'no install without a snapshot')

    def test_the_systemui_stages_are_opt_in_and_not_gated_on_fixture_seeding(self):
        code = (HERE/'experiment.py').read_text()
        self.assertIn("p.add_argument('--systemui', action='store_true'", code)
        self.assertIn("needs = ['environment', 'image', 'device']", code)
        self.assertNotIn('com.android.systemui', code, 'the synthetic harness itself still never names SystemUI')
        stages = (HERE/'systemui_stages.py').read_text()
        for banned in ("'root'", "'remount'", 'disable-verity', 'setenforce', 'platform.pk8', 'dev.pixel.voice'):
            self.assertNotIn(banned, stages, banned)

if __name__ == '__main__':
    unittest.main()
