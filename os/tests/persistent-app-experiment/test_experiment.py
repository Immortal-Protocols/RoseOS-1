"""Static and unit checks for the persistent-app experiment (OS-COMPONENT-EXP-004). Outputs below are SYNTHETIC
shapes of adb/dumpsys/logcat/apksigner; they test the classifiers, not any device."""
import contextlib
import io
import json
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
            "(EXP006-KVM-001; approved runs only)'\n        type: boolean\n        default: false"
            "\n      startup_diagnostic:\n        description: 'Stop after the first emulator launch and report a fixed "
            "startup category (EXP006-STARTUP-003; not with systemui)'\n        type: boolean\n        default: false"
            "\n      runner:\n        description: 'hosted (ubuntu-24.04) or the isolated self-hosted trial runner "
            "(EXP006-ISOLATED-TRIAL-001; approved trial only)'\n        type: choice\n        options:\n          - hosted"
            "\n          - isolated-trial\n        default: hosted")
        for needle in ('permissions:\n  contents: read', 'cancel-in-progress: false', "|| 'ubuntu-24.04' }}",
                'timeout-minutes: 30', 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262',
                'persist-credentials: false', 'retention-days: 7', 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'):
            self.assertIn(needle, WORKFLOW)
        trial = ("fromJSON('{\"group\":\"roseos-systemui-test\",\"labels\":[\"self-hosted\",\"roseos-systemui-test\"]}')")
        self.assertEqual(WORKFLOW.count(trial), 1, 'self-hosted only as the exact trial group and label')
        steps = WORKFLOW[WORKFLOW.index('    steps:'):]
        for banned in ('secrets.', 'id-token', 'environment:', 'pull_request', 'push:', 'schedule',
                'emulator-runner', 'reactivecircus'):
            self.assertNotIn(banned, WORKFLOW)
        self.assertNotIn('self-hosted', steps, 'no step names a self-hosted runner')
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

def official_avdmanager_create(argv, env):
    """A MODEL (not the real tool) of `avdmanager create avd -n NAME ... -p DATA`, transcribed from the official
    source, platform/tools/base mirror-goog-studio-main: AbstractAndroidLocations.kt avdLocation =
    singlePathOf(ANDROID_AVD_HOME) ?: prefsLocation/avd, where PathLocator.handlePath drops a value that is not an
    existing directory (mustExist); prefs falls back to user.home/HOME + /.android (ANDROID_USER_HOME,
    ANDROID_PREFS_ROOT, ANDROID_SDK_HOME unset here); ANDROID_EMULATOR_HOME is not read. AvdInfo.getDefaultIniFile =
    baseAvdFolder/NAME.ini whatever -p is; createAvdIniFile writes path=<absolute -p> (MetadataKey.ABS_PATH = "path")
    and target=; buildAvdFilesList creates a missing base folder. Returns the base folder it used."""
    name, data = argv[argv.index('-n') + 1], Path(argv[argv.index('-p') + 1])
    home = env.get('ANDROID_AVD_HOME')
    base = Path(home) if home and Path(home).is_dir() else Path(env['HOME'])/'.android/avd'
    base.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    (data/'config.ini').write_text('image.sysdir.1=system-images/android-35/default/x86_64/\n')
    (base/f'{name}.ini').write_text(f'avd.ini.encoding=UTF-8\npath={data.absolute()}\ntarget=android-35\n')
    return base

class AvdPreparationTest(unittest.TestCase):
    """EXP006-AVD-004. No official avdmanager or emulator is installed here, so the real-tool reproduction is not
    possible; these use official_avdmanager_create (a source-transcribed model) and synthetic files, labelled so."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='pfx-avd-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work, self.home = self.tmp/'work', self.tmp/'home'
        self.work.mkdir()
        self.home.mkdir()
        self.env = {'HOME': str(self.home), 'ANDROID_AVD_HOME': str(self.work/'avd'),
            'ANDROID_EMULATOR_HOME': str(self.work/'emulator-home')}
        self.argv = ['avdmanager', 'create', 'avd', '-n', 'fixture', '-k', ex.IMAGE, '-d', 'pixel', '-p',
            str(self.work/'avd/fixture.avd')]

    def test_model_reproduces_the_run_when_the_avd_home_is_absent(self):
        # Run 41154781046: avdmanager exited 0, yet fixture.ini was absent from the AVD home the emulator was given.
        base = official_avdmanager_create(self.argv, self.env)
        self.assertEqual(base, self.home/'.android/avd', 'the absent ANDROID_AVD_HOME is dropped for HOME/.android/avd')
        self.assertTrue((self.work/'avd/fixture.avd/config.ini').is_file(), '-p still creates the data folder')
        self.assertTrue((self.work/'avd').is_dir(), '...so the AVD home exists afterwards, without the descriptor')
        self.assertFalse((self.work/'avd/fixture.ini').exists())
        self.assertEqual(ex.avd_binding(self.work/'avd'), (False, {'descriptor': 'absent', 'binding': 'unchecked',
            'data': 'unchecked'}), 'the old order is now refused before launch')

    def test_prepared_homes_bind_the_descriptor_to_the_task_data(self):
        ex.prepare_homes(self.work)
        self.assertEqual(official_avdmanager_create(self.argv, self.env), self.work/'avd')
        self.assertEqual(ex.avd_binding(self.work/'avd'), (True, {'descriptor': 'present', 'binding': 'match',
            'data': 'present'}))
        self.assertFalse((self.home/'.android').exists(), 'nothing is written outside the task directories')
        self.assertTrue((self.work/'emulator-home').is_dir())

    def test_prepare_refuses_any_existing_path_or_link(self):
        for make in (lambda p: p.mkdir(), lambda p: p.write_text('x'), lambda p: p.symlink_to(self.tmp/'elsewhere'),
                lambda p: p.symlink_to(self.home)):
            work = Path(tempfile.mkdtemp(dir=self.tmp))
            make(work/'avd')
            with self.assertRaises(ex.Failed):
                ex.prepare_homes(work)
        self.assertEqual(list(self.home.iterdir()), [], 'a linked target is never written')
        self.assertFalse((self.tmp/'elsewhere').exists())

    def descriptor(self, text, data=True, config=True):
        ex.prepare_homes(self.work)
        avd = self.work/'avd'
        if data:
            (avd/'fixture.avd').mkdir()
            if config:
                (avd/'fixture.avd/config.ini').write_text('x=1\n')
        if text is not None:
            (avd/'fixture.ini').write_bytes(text if isinstance(text, bytes) else text.encode())
        return ex.avd_binding(avd)

    def test_binding_refusals(self):
        good = f'path={self.work.absolute()}/avd/fixture.avd\n'
        cases = {
            'no path': ('target=android-35\n', {'descriptor': 'present', 'binding': 'missing', 'data': 'unchecked'}),
            'two paths': (good + good, {'descriptor': 'present', 'binding': 'ambiguous', 'data': 'unchecked'}),
            'relative': ('path=avd/fixture.avd\n', {'descriptor': 'present', 'binding': 'mismatch', 'data': 'unchecked'}),
            'elsewhere': (f'path={self.home}/.android/avd/fixture.avd\n',
                {'descriptor': 'present', 'binding': 'mismatch', 'data': 'unchecked'}),
            'escape': (f'path={self.work.absolute()}/avd/fixture.avd/../../other\n',
                {'descriptor': 'present', 'binding': 'mismatch', 'data': 'unchecked'}),
            'not utf-8': (b'path=\xff\xfe\n', {'descriptor': 'unreadable', 'binding': 'unchecked', 'data': 'unchecked'}),
            'oversized': (good + 'x' * ex.DESCRIPTOR_CAP, {'descriptor': 'oversized', 'binding': 'unchecked',
                'data': 'unchecked'}),
        }
        for label, (text, facts) in cases.items():
            with self.subTest(label):
                shutil.rmtree(self.work)
                self.work.mkdir()
                self.assertEqual(self.descriptor(text), (False, facts))
        for label, kw, data in (('no data folder', {'data': False}, 'absent'), ('no config.ini', {'config': False},
                'config-missing')):
            with self.subTest(label):
                shutil.rmtree(self.work)
                self.work.mkdir()
                self.assertEqual(self.descriptor(good, **kw), (False, {'descriptor': 'present', 'binding': 'match',
                    'data': data}))
        shutil.rmtree(self.work)
        self.work.mkdir()
        self.assertEqual(self.descriptor(good)[0], True)

    def test_linked_or_non_regular_descriptor_and_data_are_refused(self):
        outside = self.tmp/'outside.ini'
        outside.write_text(f'path={self.work.absolute()}/avd/fixture.avd\n')
        ex.prepare_homes(self.work)
        avd = self.work/'avd'
        (avd/'fixture.avd').mkdir()
        (avd/'fixture.avd/config.ini').write_text('x=1\n')
        (avd/'fixture.ini').symlink_to(outside)
        self.assertEqual(ex.avd_binding(avd)[1]['descriptor'], 'symlink')
        (avd/'fixture.ini').unlink()
        (avd/'fixture.ini').mkdir()
        self.assertEqual(ex.avd_binding(avd)[1]['descriptor'], 'not-regular')
        (avd/'fixture.ini').rmdir()
        (avd/'fixture.ini').write_text(f'path={avd.absolute()}/fixture.avd\n')
        real = self.tmp/'real-data'
        shutil.move(str(avd/'fixture.avd'), real)
        (avd/'fixture.avd').symlink_to(real)
        self.assertEqual(ex.avd_binding(avd), (False, {'descriptor': 'present', 'binding': 'match',
            'data': 'symlink'}), 'a linked data folder is refused')
        (avd/'fixture.avd').unlink()
        (avd/'fixture.avd').write_text('not a folder')
        self.assertEqual(ex.avd_binding(avd)[1]['data'], 'not-directory')
        facts = json.dumps(ex.avd_binding(avd))
        self.assertNotIn(str(self.tmp), facts, 'no path value in the facts')

class StartupDiagnosticTest(unittest.TestCase):
    """EXP006-STARTUP-003 over synthetic files only: the cap, ambiguity, path safety, no raw data, stage exclusion
    and cleanup. Nothing is launched."""
    SECRET = 'TOKEN-abc123 /home/runner/private ANDROID_SDK_ROOT=/x --flag'

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='pfx-startup-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def owned(self, data, name='emulator.log'):
        """The run's own descriptor for a new task-local file, as boot() creates it, with data written through it."""
        fd = os.open(self.tmp/name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.addCleanup(os.close, fd)
        os.write(fd, data)
        return fd

    def test_each_fixed_category_and_unknown(self):
        samples = {'avd_missing': b'PANIC: Unknown AVD name [fixture], use -list-avds to see valid list.',
            'image_missing': b'PANIC: Cannot find AVD system path. Please define ANDROID_SDK_ROOT',
            'acceleration_refused': b'ERROR: x86_64 emulation currently requires hardware acceleration!',
            'rejected_option': b'emulator: ERROR: unknown option: -bogus',
            'missing_library': b'qemu-system-x86_64: error while loading shared libraries: libfoo.so.1: cannot open '
                b'shared object file: No such file or directory'}
        self.assertEqual(set(samples), set(ex.STARTUP_PATTERNS))
        for category, text in samples.items():
            self.assertEqual(ex.classify_startup(b'INFO | starting\n' + text + b'\n'), (category, False), category)
        self.assertEqual(ex.classify_startup(b'INFO | Android emulator version 36\nINFO | boot'), ('unknown', False))
        self.assertEqual(ex.STARTUP_CATEGORIES[-1], 'unknown')

    def test_two_categories_or_malformed_input_stay_unknown(self):
        both = b'PANIC: Unknown AVD name [fixture]\nerror while loading shared libraries: libx.so'
        self.assertEqual(ex.classify_startup(both), ('unknown', True))
        for junk in (b'', b'\x00' * 100, bytes(range(256)) * 8, b'\xff\xfe unknown \xc3( avd'):
            self.assertEqual(ex.classify_startup(junk), ('unknown', False))
        # A pattern split by invalid bytes is not a match.
        self.assertEqual(ex.classify_startup(b'unknown \xff avd name')[0], 'unknown')

    def test_only_the_first_cap_bytes_are_read_and_truncation_is_reported(self):
        cap = ex.STARTUP_CAP
        self.assertEqual(cap, 16384)
        facts = ex.startup_output(self.owned(b'x' * cap + b'PANIC: Unknown AVD name [fixture]'))
        self.assertEqual((facts['log'], facts['bytesRead'], facts['truncated'], facts['category']),
            ('read', cap, True, 'unknown'), 'a match after the cap is never seen')
        facts = ex.startup_output(self.owned(b'y' * (cap - 33) + b'PANIC: Unknown AVD name [fixture]', 'exact.log'))
        self.assertEqual((facts['bytesRead'], facts['truncated'], facts['category']), (cap, False, 'avd_missing'))
        self.assertEqual(ex.startup_output(self.owned(b'ERROR: unknown option: -x', 'short.log')), {'log': 'read',
            'capBytes': cap, 'bytesRead': 25, 'truncated': False, 'category': 'rejected_option', 'ambiguous': False})
        self.assertEqual(ex.startup_output(self.owned(b'', 'empty.log'))['bytesRead'], 0)

    def test_only_the_owned_new_regular_file_is_read_never_the_path_again(self):
        fd = self.owned(b'ERROR: unknown option: -x')
        path = self.tmp/'emulator.log'
        path.unlink()
        path.write_bytes(b'PANIC: Unknown AVD name [fixture]')  # a replacement at the path, possibly the same inode
        self.assertEqual(ex.startup_output(fd)['category'], 'rejected_option', 'read through the owned descriptor only')
        # Creation refuses any existing path: a file, a link to elsewhere, a dangling link.
        target = self.tmp/'elsewhere.txt'
        target.write_text('PANIC: Unknown AVD name')
        for name, make in (('file.log', lambda p: p.write_text('x')), ('link.log', lambda p: p.symlink_to(target)),
                ('dangling.log', lambda p: p.symlink_to(self.tmp/'missing'))):
            make(self.tmp/name)
            with self.assertRaises(OSError):
                self.owned(b'', name)
        self.assertEqual(target.read_text(), 'PANIC: Unknown AVD name', 'a linked target is never written')
        self.assertFalse((self.tmp/'missing').exists())
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        self.addCleanup(os.close, w)
        self.assertEqual(ex.startup_output(r)['log'], 'not-regular')
        self.assertEqual(ex.startup_output(None)['log'], 'not-created', 'never read without an owned launch')
        closed = os.dup(r)
        os.close(closed)
        self.assertEqual(ex.startup_output(closed)['log'], 'unreadable')

    def test_the_avd_descriptor_fact(self):
        ini = self.tmp/'fixture.ini'
        self.assertEqual(ex.avd_descriptor(ini), 'absent')
        ini.write_text('path=/x\n')
        self.assertEqual(ex.avd_descriptor(ini), 'present')
        link = self.tmp/'link.ini'
        link.symlink_to(ini)
        self.assertEqual(ex.avd_descriptor(link), 'unknown')
        (self.tmp/'dir.ini').mkdir()
        self.assertEqual(ex.avd_descriptor(self.tmp/'dir.ini'), 'unknown')

    def test_no_raw_text_path_or_value_leaves_the_facts(self):
        facts = ex.startup_output(self.owned(('PANIC: Unknown AVD name ' + self.SECRET).encode()))
        text = json.dumps(facts)
        for fragment in ('TOKEN', '/home/runner', 'ANDROID_SDK_ROOT', '--flag', str(self.tmp), 'PANIC', 'fixture'):
            self.assertNotIn(fragment, text)
        self.assertEqual(set(facts), {'log', 'capBytes', 'bytesRead', 'truncated', 'category', 'ambiguous'})

    # ---- run() end to end over a fake SDK, child and adb

    def fake_sdk(self):
        sdk = self.tmp/'sdk'
        for tool in ('cmdline-tools/latest/bin/sdkmanager', 'cmdline-tools/latest/bin/avdmanager', 'emulator/emulator',
                'platform-tools/adb'):
            (sdk/tool).parent.mkdir(parents=True, exist_ok=True)
            (sdk/tool).write_text('')
        (sdk/'licenses').mkdir()
        work = self.tmp/'work'
        work.mkdir()
        (work/'apks.json').write_text('{}')
        (work/'test-only.keystore').write_text('k')
        return sdk, work

    def run_fake(self, startup_text, create_ini=True, pre_existing_log=False, **kw):
        sdk, work = self.fake_sdk()
        if pre_existing_log:
            (work/'emulator.log').write_text('older output')
        clock, calls, launched = FakeClock(), [], []

        class Child:
            def __init__(self, argv, stdout, stderr):
                launched.append([str(a) for a in argv])
                os.write(stdout, startup_text.encode())
            def poll(self):
                return 1
            def wait(self, timeout=None):
                return 1
            def kill(self):
                pass

        def run(argv, **kwargs):
            argv = [str(a) for a in argv]
            calls.append(argv)
            name = Path(argv[0]).name
            out = ''
            if name == 'emulator':
                out = 'Android emulator version 36.0\n'
            elif name == 'sdkmanager' and '--list_installed' in argv:
                out = f'  {ex.IMAGE} | 1 | image\n'
            elif name == 'avdmanager':
                env = dict(ex.os.environ)
                if not create_ini:
                    env['ANDROID_AVD_HOME'] = str(self.tmp/'not-there')  # the old effect: the descriptor lands elsewhere
                official_avdmanager_create(argv, env)
            elif name == 'adb' and argv[1:] == ['devices']:
                out = 'List of devices attached\n'
            return ex.subprocess.CompletedProcess(argv, 0, out, '')

        evidence = self.tmp/'evidence'
        (self.tmp/'home').mkdir()
        env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(self.tmp/'home')}
        with mock.patch.object(ex.subprocess, 'run', run), mock.patch.object(ex.subprocess, 'Popen', Child), \
                mock.patch.object(ex.time, 'time', clock.time), mock.patch.object(ex.time, 'sleep', clock.sleep), \
                mock.patch.object(ex.os, 'access', lambda p, m: True), mock.patch.dict(ex.os.environ, env, clear=True):
            results = ex.run(sdk, work, evidence, startup_diagnostic=True, **kw)
        return results, work, evidence, calls, launched

    def test_the_mode_runs_only_the_startup_stages_and_cleans_up(self):
        results, work, evidence, calls, launched = self.run_fake('PANIC: Unknown AVD name [fixture] ' + self.SECRET)
        stages = results['stages']
        self.assertEqual((stages['environment']['result'], stages['image']['result'], stages['device']['result']),
            ('pass', 'pass', 'fail'))
        self.assertEqual(stages['device']['bootWait']['wait'], 'child-exited')
        for name in ('seed', 'ordinary', 'staged', 'restore'):
            self.assertEqual(stages[name], {'result': 'not-run', 'reason': 'excluded: startup diagnostic only'})
        self.assertFalse([n for n in stages if n.startswith('systemui')])
        self.assertEqual(len(launched), 1, 'one initial launch only')
        self.assertEqual(launched[0][1:], ['-avd', 'fixture', '-port', '5580', '-no-window', '-no-snapshot',
            '-no-boot-anim', '-no-audio', '-writable-system', '-gpu', 'swiftshader_indirect'], 'launch flags unchanged')
        for banned in ('install', 'push', 'remount', 'root', 'reboot'):
            self.assertFalse([c for c in calls if Path(c[0]).name == 'adb' and banned in c[1:]], banned)
        self.assertEqual(results['mode'], 'startup-diagnostic')
        self.assertEqual(results['startupDiagnostic'], {'log': 'read', 'capBytes': 16384,
            'bytesRead': len(('PANIC: Unknown AVD name [fixture] ' + self.SECRET).encode()), 'truncated': False,
            'category': 'avd_missing', 'ambiguous': False, 'avdDescriptor': 'present'},
            'the descriptor is checked before cleanup removes it')
        for private in ('test-only.keystore', 'avd', 'emulator-home'):
            self.assertFalse((work/private).exists(), private)
        written = (evidence/'results.json').read_text()
        for fragment in ('TOKEN', '/home/runner', 'ANDROID_SDK_ROOT', 'PANIC', str(work)):
            self.assertNotIn(fragment, written)
        self.assertEqual(sorted(p.name for p in evidence.iterdir()), ['results.json', 'sdkmanager-install.txt'])

    def test_a_descriptor_absent_from_the_task_avd_home_refuses_before_launch(self):
        results, work, evidence, calls, launched = self.run_fake('PANIC: Unknown AVD name [fixture]', create_ini=False)
        self.assertEqual(launched, [], 'no doomed emulator is spawned')
        device = results['stages']['device']
        self.assertEqual(device['result'], 'fail')
        self.assertIn('no usable fixture descriptor in the task AVD home (descriptor absent', device['reason'])
        self.assertEqual(device['avdPrepare'], {'descriptor': 'absent', 'binding': 'unchecked', 'data': 'unchecked'})
        self.assertNotIn('bootWait', device)
        self.assertEqual(results['startupDiagnostic']['log'], 'not-created')
        for name in ('seed', 'ordinary', 'staged', 'restore'):
            self.assertEqual(results['stages'][name]['result'], 'not-run')
        for private in ('test-only.keystore', 'avd', 'emulator-home'):
            self.assertFalse((work/private).exists(), private)
        self.assertNotIn(str(work), (evidence/'results.json').read_text())

    def test_an_existing_output_file_stops_before_launch_and_is_never_read(self):
        results, work, _, _, launched = self.run_fake('PANIC: Unknown AVD name', pre_existing_log=True)
        self.assertEqual(launched, [], 'nothing launches over an existing output file')
        self.assertEqual(results['stages']['device']['result'], 'fail')
        self.assertEqual(results['startupDiagnostic'], {'log': 'not-created', 'capBytes': 16384, 'bytesRead': 0,
            'truncated': None, 'category': 'unknown', 'ambiguous': False, 'avdDescriptor': 'unknown'})
        self.assertFalse((work/'avd').exists())
        self.assertEqual((work/'emulator.log').read_text(), 'older output', 'left as it was')

    def test_never_with_systemui_and_off_by_default(self):
        with self.assertRaises(ex.Unavailable):
            ex.run(self.tmp/'sdk', self.tmp/'work', self.tmp/'evidence', systemui=True, startup_diagnostic=True)
        self.assertFalse((self.tmp/'evidence').exists(), 'refused before anything is written')
        with mock.patch.object(ex, 'run') as run, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                ex.main(['run', '--sdk', 's', '--work', 'w', '--evidence', 'e', '--systemui', '--startup-diagnostic'])
            run.assert_not_called()
        self.assertIn("p.add_argument('--startup-diagnostic', action='store_true'", CODE)
        step = WORKFLOW[WORKFLOW.index('- name: Run the experiment'):WORKFLOW.index('- name: Evidence')]
        self.assertIn('STARTUP_DIAGNOSTIC: ${{ inputs.startup_diagnostic }}', step)
        self.assertLess(step.index('not with systemui'), step.index('kvm_access.py grant'),
            'the combination is refused before any KVM grant')
        self.assertIn('flag=(--startup-diagnostic)', step)
        self.assertNotIn('emulator.log', WORKFLOW[WORKFLOW.index('Evidence (allowlist only)'):WORKFLOW.index('Remove the test key')])

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
