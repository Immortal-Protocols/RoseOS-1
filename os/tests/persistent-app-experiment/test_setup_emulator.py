"""setup_emulator.py (EXP005-F1) against synthetic SDK trees and a fake runner: nothing is installed or executed."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import setup_emulator as se  # noqa: E402

WORKFLOW = (HERE.parents[2]/'.github/workflows/systemui-persistent-experiment.yml').read_text()

def executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\n')
    path.chmod(0o755)

class Runner:
    def __init__(self, sdk, install_exit=0, creates=True, version_exit=0):
        self.sdk, self.install_exit, self.creates, self.version_exit, self.calls = sdk, install_exit, creates, version_exit, []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if argv[1:] == ['--install', 'emulator']:
            if self.install_exit == 0 and self.creates:
                executable(self.sdk/'emulator/emulator')
            out = 'Accept? (y/N): Skipping following packages as the license is not accepted:\n' if self.install_exit else ''
            return subprocess.CompletedProcess(argv, self.install_exit, out, '')
        if argv[1:] == ['-version']:
            return subprocess.CompletedProcess(argv, self.version_exit, 'Android emulator version 36.1.9.0 (build_id 1)\n', '')
        raise AssertionError(f'unexpected command {argv}')

QEMU_LIBPULSE = ('/usr/local/lib/android/sdk/emulator/qemu/linux-x86_64/qemu-system-x86_64: error while loading '
    'shared libraries: libpulse.so.0: cannot open shared object file: No such file or directory\n')

class LibraryRunner(Runner):
    """The owner-reported failure: -version exits 127 until libpulse0 (and here its libsndfile1) are on the path."""
    def __init__(self, sdk, needs=('libpulse.so.0', 'libsndfile.so.1'), download_exit=0, provides=True):
        super().__init__(sdk)
        self.needs, self.download_exit, self.provides = list(needs), download_exit, provides

    def __call__(self, argv, **kw):
        if argv[:2] == ['apt-get', 'download']:
            self.calls.append((argv, kw))
            if self.download_exit:
                return subprocess.CompletedProcess(argv, 100, '', 'E: Unable to locate package ' + argv[2] + '\n')
            Path(kw['cwd'], f'{argv[2]}_1.0-1ubuntu1_amd64.deb').write_bytes(argv[2].encode())
            return subprocess.CompletedProcess(argv, 0, '', '')
        if argv[:2] == ['dpkg-deb', '-x']:
            self.calls.append((argv, kw))
            package = Path(argv[2]).name.split('_')[0]
            libs = Path(argv[3])/se.LIB_SUBDIR
            libs.mkdir(parents=True, exist_ok=True)
            if self.provides:
                for soname, owner in se.SONAME_PACKAGES.items():
                    if owner == package:
                        (libs/soname).write_text('')
            return subprocess.CompletedProcess(argv, 0, '', '')
        if argv[1:] == ['-version']:
            self.calls.append((argv, kw))
            path = (kw.get('env') or {}).get('LD_LIBRARY_PATH', '').split(':')[0]
            for soname in self.needs:
                if not (path and (Path(path)/soname).exists()):
                    message = QEMU_LIBPULSE.replace('libpulse.so.0', soname)
                    return subprocess.CompletedProcess(argv, 127, '', message)
            return subprocess.CompletedProcess(argv, 0, 'Android emulator version 36.1.9.0 (build_id 1)\n', '')
        return super().__call__(argv, **kw)

class SetupTest(unittest.TestCase):
    def setUp(self):
        self.sdk = Path(tempfile.mkdtemp(prefix='sdk-'))
        self.addCleanup(__import__('shutil').rmtree, self.sdk, True)
        (self.sdk/'licenses').mkdir()
        (self.sdk/'licenses/android-sdk-license').write_text('hash\n')
        executable(self.sdk/'cmdline-tools/latest/bin/sdkmanager')

    def test_a_missing_emulator_is_installed_once_with_stdin_closed(self):
        runner = Runner(self.sdk)
        record = se.setup(self.sdk, runner)
        self.assertEqual((record['result'], record['version']), ('installed', 'Android emulator version 36.1.9.0 (build_id 1)'))
        install = [kw for argv, kw in runner.calls if argv[1:] == ['--install', 'emulator']]
        self.assertEqual(len(install), 1)
        self.assertIs(install[0]['stdin'], subprocess.DEVNULL, 'no licence prompt can be answered')
        self.assertEqual(record['licences'], ['android-sdk-license'])

    def test_a_pre_existing_emulator_is_used_without_sdkmanager(self):
        executable(self.sdk/'emulator/emulator')
        runner = Runner(self.sdk)
        record = se.setup(self.sdk, runner)
        self.assertEqual(record['result'], 'pre-existing')
        self.assertFalse(any(argv[1:] == ['--install', 'emulator'] for argv, _ in runner.calls))

    def test_refused_or_incomplete_installs_are_unavailable_not_bypassed(self):
        record = se.setup(self.sdk, Runner(self.sdk, install_exit=1))
        self.assertEqual(record['result'], 'unavailable')
        self.assertIn('no licence answered', record['reason'])
        self.assertIn('license is not accepted', record['sdkmanager']['outputTail'])
        self.assertEqual(se.setup(self.sdk, Runner(self.sdk, creates=False))['result'], 'unavailable')
        self.assertIn('-version exited', se.setup(self.sdk, Runner(self.sdk, version_exit=1))['reason'])
        (self.sdk/'cmdline-tools/latest/bin/sdkmanager').unlink()
        __import__('shutil').rmtree(self.sdk/'emulator', True)
        self.assertIn('is not installed', se.setup(self.sdk, Runner(self.sdk))['reason'])

    def test_main_writes_the_record_and_exits_by_usability(self):
        record = self.sdk/'evidence/emulator-setup.json'
        self.assertEqual(se.main(['--sdk', str(self.sdk), '--record', str(record)], Runner(self.sdk, install_exit=1)), 1)
        self.assertEqual(json.loads(record.read_text())['result'], 'unavailable')
        record.unlink()
        self.assertEqual(se.main(['--sdk', str(self.sdk), '--record', str(record)], Runner(self.sdk)), 0)

    def test_the_owner_reported_libpulse_failure_is_fixed_with_official_packages_without_root(self):
        libs, github_env = self.sdk/'emulator-libs', self.sdk/'github-env'
        runner = LibraryRunner(self.sdk)
        code = se.main(['--sdk', str(self.sdk), '--record', str(self.sdk/'setup.json'), '--library-dir', str(libs),
            '--github-env', str(github_env)], runner, base_env={'PATH': '/usr/bin'})
        record = json.loads((self.sdk/'setup.json').read_text())
        self.assertEqual((code, record['result']), (0, 'installed'))
        self.assertEqual([l['package'] for l in record['libraries']], ['libpulse0', 'libsndfile1'])
        self.assertTrue(all(len(l['sha256']) == 64 for l in record['libraries']))
        self.assertEqual(github_env.read_text(), f'LD_LIBRARY_PATH={libs/se.LIB_SUBDIR}\n')
        commands = [argv[:2] for argv, _ in runner.calls]
        self.assertNotIn(['apt-get', 'install'], commands)
        self.assertNotIn('sudo', [argv[0] for argv, _ in runner.calls])
        self.assertTrue(all(kw.get('stdin') is subprocess.DEVNULL for argv, kw in runner.calls if argv[0] == 'apt-get'))

    def test_without_a_library_dir_the_127_is_reported_truthfully(self):
        record = se.setup(self.sdk, LibraryRunner(self.sdk))
        self.assertEqual(record['result'], 'unavailable')
        self.assertIn('exited 127', record['reason'])
        self.assertIn('libpulse.so.0: cannot open shared object file', record['reason'])
        self.assertNotIn('libraries', record)

    def test_unknown_libraries_failed_downloads_or_wrong_packages_stay_unavailable(self):
        libs = self.sdk/'emulator-libs'
        record = se.setup(self.sdk, LibraryRunner(self.sdk, needs=('libmystery.so.3',)), libs)
        self.assertIn('no known Ubuntu package', record['reason'])
        record = se.setup(self.sdk, LibraryRunner(self.sdk, download_exit=100), self.sdk/'libs2')
        self.assertIn('apt-get download libpulse0 exited 100', record['reason'])
        record = se.setup(self.sdk, LibraryRunner(self.sdk, provides=False), self.sdk/'libs3')
        self.assertIn('did not provide', record['reason'])
        github_env = self.sdk/'github-env'
        self.assertEqual(se.main(['--sdk', str(self.sdk), '--record', str(self.sdk/'r.json'), '--library-dir',
            str(self.sdk/'libs4'), '--github-env', str(github_env)], LibraryRunner(self.sdk, download_exit=100)), 1)
        self.assertFalse(github_env.exists(), 'nothing is exported when the emulator is unusable')

    def test_the_missing_library_parser(self):
        self.assertEqual(se.missing_library(QEMU_LIBPULSE), 'libpulse.so.0')
        self.assertEqual(se.missing_library('qemu: error loading libpulse.so.0 (missing)'), 'libpulse.so.0')
        self.assertIsNone(se.missing_library('emulator -version exited 1: PANIC: unknown'))

    def test_the_workflow_installs_before_the_run_without_widening_anything(self):
        setup = WORKFLOW.index('setup_emulator.py --sdk "$ANDROID_HOME"')
        self.assertLess(WORKFLOW.index('experiment.py build'), setup)
        self.assertLess(setup, WORKFLOW.index('experiment.py run'))
        self.assertIn('test_setup_emulator.py -v', WORKFLOW)
        self.assertIn('--library-dir "$RUNNER_TEMP/emulator-libs" --github-env "$GITHUB_ENV"', WORKFLOW)
        code = (HERE/'setup_emulator.py').read_text().split('"""', 2)[2]  # the code, not its docstring
        for banned in ('sudo', '--licenses', 'yes |', "'y'", 'chmod', '/dev/kvm', 'udev', 'setenforce', 'input=',
                "'install', package", 'apt-get install', 'dpkg -i', 'ldconfig'):
            self.assertNotIn(banned, code + WORKFLOW[setup - 400:setup + 200], banned)

if __name__ == '__main__':
    unittest.main()
