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

    def test_the_workflow_installs_before_the_run_without_widening_anything(self):
        setup = WORKFLOW.index('setup_emulator.py --sdk "$ANDROID_HOME"')
        self.assertLess(WORKFLOW.index('experiment.py build'), setup)
        self.assertLess(setup, WORKFLOW.index('experiment.py run'))
        self.assertIn('test_setup_emulator.py -v', WORKFLOW)
        code = (HERE/'setup_emulator.py').read_text().split('"""', 2)[2]  # the code, not its docstring
        for banned in ('sudo', '--licenses', 'yes |', "'y'", 'chmod', '/dev/kvm', 'udev', 'setenforce', 'input='):
            self.assertNotIn(banned, code + WORKFLOW[setup - 400:setup + 200], banned)

if __name__ == '__main__':
    unittest.main()
