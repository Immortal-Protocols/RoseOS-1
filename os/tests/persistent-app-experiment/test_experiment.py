"""Static and unit checks for the persistent-app experiment (OS-COMPONENT-EXP-004). Outputs below are SYNTHETIC
shapes of adb/dumpsys/logcat/apksigner; they test the classifiers, not any device."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import experiment as ex  # noqa: E402

WORKFLOW = (ROOT/'.github/workflows/systemui-persistent-experiment.yml').read_text()
CODE = (HERE/'experiment.py').read_text()

DUMP_SYSTEM = '''Packages:
  Package [dev.roseos.test.persistentfixture] (1a2b):
    codePath=/system/app/PersistentFixture
    versionCode=1 minSdk=29 targetSdk=35
    flags=[ SYSTEM HAS_CODE PERSISTENT ALLOW_CLEAR_USER_DATA ]
'''
DUMP_UPDATED = '''Packages:
  Package [dev.roseos.test.persistentfixture] (3c4d):
    codePath=/data/app/~~abc==/dev.roseos.test.persistentfixture-xyz==
    versionCode=2 minSdk=29 targetSdk=35
    flags=[ SYSTEM HAS_CODE PERSISTENT UPDATED_SYSTEM_APP ]

Hidden system packages:
  Package [dev.roseos.test.persistentfixture] (1a2b):
    codePath=/system/app/PersistentFixture
    versionCode=1 minSdk=29 targetSdk=35
'''

class ClassifierTest(unittest.TestCase):
    def test_package_state_reads_the_active_block(self):
        self.assertEqual(ex.package_state(DUMP_SYSTEM), {'codePath': '/system/app/PersistentFixture', 'versionCode': 1,
            'flags': ['SYSTEM', 'HAS_CODE', 'PERSISTENT', 'ALLOW_CLEAR_USER_DATA'], 'hiddenSystemCopy': False})
        updated = ex.package_state(DUMP_UPDATED)
        self.assertEqual((updated['versionCode'], updated['hiddenSystemCopy']), (2, True))
        self.assertTrue(updated['codePath'].startswith('/data/app'))
        self.assertEqual(ex.package_state(''), {'codePath': None, 'versionCode': None, 'flags': [], 'hiddenSystemCopy': False})

    def test_ordinary_install_verdicts(self):
        rejected = ('Performing Streamed Install\nadb: failed to install fixture-v2.apk: Failure [INSTALL_FAILED_INVALID_APK: '
            'Package dev.roseos.test.persistentfixture is a persistent app. Persistent apps are not updateable.]')
        self.assertEqual(ex.classify_ordinary(rejected, 1)[0], 'pass')
        other = 'adb: failed to install fixture-v2.apk: Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match]'
        verdict, reason = ex.classify_ordinary(other, 1)
        self.assertEqual(verdict, 'fail')
        self.assertIn('different reason', reason)
        self.assertEqual(ex.classify_ordinary('Performing Streamed Install\nSuccess', 0)[0], 'fail')

    def test_remount_verdicts(self):
        self.assertEqual(ex.classify_remount('Remount succeeded', 0), 'ok')
        self.assertEqual(ex.classify_remount('Successfully disabled verity\nNow reboot your device for settings to take effect', 0), 'requires-disable-verity')
        self.assertEqual(ex.classify_remount('Using overlayfs for /system\nNow reboot your device for settings to take effect', 0), 'reboot')
        self.assertEqual(ex.classify_remount('verity is enabled; use adb disable-verity', 1), 'requires-disable-verity')
        self.assertEqual(ex.classify_remount('remount failed', 1), 'remount-failed')

    def test_markers_and_certificates(self):
        log = ('10-30 01:00:00.000  901  901 I PersistFixture: marker version=1 pid=901 sentinel=created\n'
            '10-30 01:05:00.000 1204 1204 I PersistFixture: marker version=2 pid=1204 sentinel=existing:created-by-v1\n')
        self.assertEqual(ex.latest_marker(log), {'version': 2, 'pid': 1204, 'sentinel': 'existing:created-by-v1'})
        self.assertIsNone(ex.latest_marker('nothing'))
        self.assertEqual(ex.cert_digests('Signer #1 certificate SHA-256 digest: ' + 'ab' * 32), ['ab' * 32])

class BoundaryTest(unittest.TestCase):
    def test_workflow_is_manual_read_only_bounded_and_uploads_an_allowlist(self):
        on = WORKFLOW[WORKFLOW.index('\non:'):WORKFLOW.index('\npermissions:')]
        # Manual only; EXP006 adds one boolean input, default false (the synthetic-only run).
        self.assertEqual(on.strip(), "on:\n  workflow_dispatch:\n    inputs:\n      systemui:\n        description: "
            "'Also run the opt-in SystemUI stages on the emulator image (EXP006)'\n        type: boolean\n        default: false"
            "\n      kvm_acl:\n        description: 'Grant this job user rw on the KVM device for the run and restore it "
            "(EXP006-KVM-001; approved runs only)'\n        type: boolean\n        default: false")
        for needle in ('permissions:\n  contents: read', 'cancel-in-progress: false', 'runs-on: ubuntu-24.04',
                'timeout-minutes: 30', 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262',
                'persist-credentials: false', 'retention-days: 7', 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'):
            self.assertIn(needle, WORKFLOW)
        for banned in ('secrets.', 'id-token', 'environment:', 'self-hosted', 'pull_request', 'push:', 'schedule',
                'emulator-runner', 'reactivecircus'):
            self.assertNotIn(banned, WORKFLOW)
        upload = WORKFLOW[WORKFLOW.index('Evidence (allowlist only)'):WORKFLOW.index('Remove the test key')]
        self.assertNotIn('keystore', upload)
        self.assertNotIn('avd', upload)
        self.assertNotIn('.apk\n', upload)

    def test_the_harness_never_widens_security_or_accepts_licences(self):
        # Detecting verity text is allowed; invoking it, licence acceptance, ACL or SELinux changes are not.
        for banned in ('--licenses', 'yes |', "sh('disable-verity'", "'disable-verity',", 'setenforce', 'chmod 666',
                'chown', 'udev', 'kvm.rules', 'com.android.systemui', 'sharedUserId', 'INSTALL_PACKAGES', 'MANAGE_ROLLBACKS'):
            self.assertNotIn(banned, CODE, banned)
        self.assertIn("os.access('/dev/kvm', os.R_OK | os.W_OK)", CODE)  # probed, never changed
        seed = CODE[CODE.index('    def seed():'):CODE.index('    def ordinary():')]
        self.assertLess(seed.index('VERITY_ENFORCED'), seed.index("sh('remount'"), 'verity is checked before any remount')
        self.assertLess(seed.index("sh('unroot'"), seed.index("observe('seed')"), 'installation trials start unrooted')
        self.assertIn('stdin=subprocess.DEVNULL', CODE)
        self.assertIn("'unroot'", CODE)
        self.assertIn("'reason': v.get('reason')", CODE, 'stage reasons reach the job log, not only the artifact')
        manifest = (HERE/'fixture/AndroidManifest.xml').read_text()
        self.assertIn('android:persistent="true"', manifest)
        for banned in ('sharedUserId', 'uses-permission'):
            self.assertNotIn(banned, manifest)

class FakeClock:
    def __init__(self):
        self.now = 1000.0
    def time(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds

class FakeChild:
    """The owned emulator process: running until `exits_at` (fake clock), then its exit status."""
    def __init__(self, clock, exits_at=None, code=1):
        self.clock, self.exits_at, self.code = clock, exits_at, code
    def poll(self):
        return self.code if self.exits_at is not None and self.clock.now >= self.exits_at else None

class FakeAdb:
    """`adb devices`, `wait-for-device` and getprop over a fake clock; `states` maps a start time to the serial's
    state (None: absent), `booted_at` is when sys.boot_completed becomes 1."""
    def __init__(self, clock, states, booted_at=None):
        self.clock, self.states, self.booted_at, self.calls = clock, states, booted_at, []
    def state(self):
        current = None
        for at, value in sorted(self.states.items()):
            if self.clock.now >= at:
                current = value
        return current
    def run(self, argv, timeout=None, **kwargs):
        args = [str(a) for a in argv[1:]]
        self.calls.append(args)
        if args == ['devices']:
            line = f'{ex.SERIAL}\t{self.state()}\n' if self.state() else ''
            return ex.subprocess.CompletedProcess(argv, 0, 'List of devices attached\n' + line, '')
        if args[-1:] == ['wait-for-device']:
            while self.state() != 'device':
                if timeout is not None and self.clock.now + 1 > self.start + timeout:
                    self.clock.now = self.start + timeout
                    raise ex.subprocess.TimeoutExpired(argv, timeout)
                self.clock.now += 1
            return ex.subprocess.CompletedProcess(argv, 0, '', '')
        if args[-2:] == ['getprop', 'sys.boot_completed']:
            up = self.state() == 'device' and self.booted_at is not None and self.clock.now >= self.booted_at
            return ex.subprocess.CompletedProcess(argv, 0 if self.state() == 'device' else 1, '1\n' if up else '\n', '')
        raise AssertionError(f'unexpected adb call {args}')

class BootWaitTest(unittest.TestCase):
    """EXP006-BOOT-001: the boot wait over a fake owned child, fake adb and a fake clock; budgets unchanged."""
    BUDGET = 480

    def wait(self, states, booted_at=None, exits_at=None, code=1):
        clock = FakeClock()
        adb = FakeAdb(clock, states, booted_at)
        adb.start = clock.now
        child = FakeChild(clock, exits_at, code)
        patches = [mock.patch.object(ex.subprocess, 'run', adb.run), mock.patch.object(ex.time, 'time', clock.time),
            mock.patch.object(ex.time, 'sleep', clock.sleep)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        device = ex.Device('/sdk', tempfile.mkdtemp(prefix='pfx-ev-'))
        device.child = child
        deadline = clock.now + self.BUDGET
        try:
            facts = device.wait_boot(deadline)
            return 'pass', facts, clock.now - adb.start, deadline - adb.start
        except (ex.Failed, ex.Unavailable, ex.subprocess.TimeoutExpired) as e:
            return ex.outcome(lambda: (_ for _ in ()).throw(e)), getattr(e, 'facts', None), clock.now - adb.start, \
                deadline - adb.start

    def test_an_exited_child_ends_the_wait_with_its_status(self):
        record, facts, elapsed, _ = self.wait({}, exits_at=1000.0, code=1)
        self.assertLess(elapsed, 30, 'a dead emulator must not consume the whole boot budget')
        self.assertEqual(record['result'], 'fail')
        self.assertEqual(facts['bootWait'], {'child': 'exited', 'childExitStatus': 1, 'serial': 'absent',
            'serialReachedDevice': False, 'wait': 'child-exited', 'bootCompleted': False, 'seconds': 0})
        self.assertIn('exited with status 1', record['reason'])

    def test_a_child_that_exits_later_is_seen_on_the_next_round(self):
        record, facts, elapsed, _ = self.wait({1000.0: 'offline'}, exits_at=1100.0, code=-9)
        self.assertLess(elapsed, 110)
        self.assertEqual((facts['bootWait']['wait'], facts['bootWait']['childExitStatus'], facts['bootWait']['serial']),
            ('child-exited', -9, 'offline'))
        self.assertEqual(record['result'], 'fail')

    def test_serial_never_device_is_a_failure_at_the_same_budget(self):
        for state, name in ((None, 'absent'), ('offline', 'offline'), ('unauthorized', 'unauthorized')):
            with self.subTest(state=name):
                record, facts, elapsed, budget = self.wait({1000.0: state} if state else {})
                self.assertEqual(record['result'], 'fail', 'as before: the device never appeared')
                self.assertGreaterEqual(elapsed, budget)
                self.assertLess(elapsed, budget + 40, 'no extension of the stage budget')
                self.assertEqual(facts['bootWait'], {'child': 'running', 'childExitStatus': None, 'serial': name,
                    'serialReachedDevice': False, 'wait': 'timeout', 'bootCompleted': False, 'seconds': elapsed})
                self.assertIn(f'serial {name}', record['reason'])
                for raw in ('/sdk', 'wait-for-device', "['"):
                    self.assertNotIn(raw, record['reason'], 'no argv in the reason')

    def test_device_without_the_boot_property_stays_unavailable(self):
        record, facts, elapsed, budget = self.wait({1000.0: 'offline', 1060.0: 'device'})
        self.assertEqual(record['result'], 'unavailable')
        self.assertEqual(record['reason'], 'boot did not complete within the stage budget')
        self.assertEqual((facts['bootWait']['serial'], facts['bootWait']['serialReachedDevice'],
            facts['bootWait']['bootCompleted'], facts['bootWait']['wait']), ('device', True, False, 'timeout'))
        self.assertLess(elapsed, budget + 40)

    def test_a_normal_boot_returns_the_facts(self):
        record, facts, elapsed, _ = self.wait({1000.0: 'offline', 1030.0: 'device'}, booted_at=1090.0)
        self.assertEqual(record, 'pass')
        self.assertEqual(facts, {'child': 'running', 'childExitStatus': None, 'serial': 'device',
            'serialReachedDevice': True, 'wait': 'boot-completed', 'bootCompleted': True, 'seconds': facts['seconds']})
        self.assertTrue(90 <= facts['seconds'] <= 100 and elapsed <= facts['seconds'] + 5)

    def test_serial_states_from_adb_devices(self):
        head = 'List of devices attached\n'
        self.assertEqual(ex.serial_state(head), 'absent')
        self.assertEqual(ex.serial_state(head + 'emulator-5554\tdevice\n'), 'absent')
        for state in ('device', 'offline', 'unauthorized', 'recovery'):
            self.assertEqual(ex.serial_state(head + f'{ex.SERIAL}\t{state}\n'), state)
        self.assertEqual(ex.serial_state(head + f'{ex.SERIAL}\tno permissions; see [http://x]\n'), 'other')

    def test_outcome_keeps_stage_semantics_and_carries_facts(self):
        def raises(e):
            def fn():
                raise e
            return fn
        self.assertEqual(ex.outcome(lambda: {'a': 1}), {'result': 'pass', 'a': 1})
        self.assertEqual(ex.outcome(raises(ex.Unavailable('u'))), {'result': 'unavailable', 'reason': 'u'})
        self.assertEqual(ex.outcome(raises(ex.Failed('f' * 700))), {'result': 'fail', 'reason': 'f' * 600})
        self.assertEqual(ex.outcome(raises(ex.subprocess.TimeoutExpired(['x'], 5)))['result'], 'fail')
        e = ex.Failed('f')
        e.facts = {'bootWait': {'wait': 'timeout'}}
        self.assertEqual(ex.outcome(raises(e)), {'result': 'fail', 'reason': 'f', 'bootWait': {'wait': 'timeout'}})

@unittest.skipUnless(os.environ.get('ANDROID_HOME') or Path('/opt/roseos/android-sdk/platforms/android-35').is_dir(),
    'no Android SDK with platform 35')
class BuildTest(unittest.TestCase):
    def test_v1_and_v2_share_one_test_certificate_and_differ_in_version(self):
        sdk = os.environ.get('ANDROID_HOME') or '/opt/roseos/android-sdk'
        work = Path(tempfile.mkdtemp(prefix='pfx-'))
        self.addCleanup(shutil.rmtree, work, True)
        result = ex.build(sdk, work)
        v1, v2 = result['apks']['v1'], result['apks']['v2']
        self.assertEqual((v1['versionCode'], v2['versionCode']), (1, 2))
        self.assertEqual(v1['testCertificateSha256'], v2['testCertificateSha256'])
        self.assertNotEqual(v1['sha256'], v2['sha256'])

if __name__ == '__main__':
    unittest.main()
