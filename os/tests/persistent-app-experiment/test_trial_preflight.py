"""trial_preflight.py and its workflow wiring (EXP006-ISOLATED-TRIAL-001), over SYNTHETIC environments and files only:
no runner, VM, KVM device change or dispatch."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import trial_preflight as tp  # noqa: E402

WORKFLOW = (HERE.parents[2]/'.github/workflows/systemui-persistent-experiment.yml').read_text()
CODE = (HERE/'trial_preflight.py').read_text()
NOW = 1_793_420_000

def trial_env(**over):
    env = {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
        'GITHUB_REPOSITORY_ID': tp.REPOSITORY_ID, 'GITHUB_WORKFLOW_REF': tp.WORKFLOW_REF,
        'RUNNER_ENVIRONMENT': 'self-hosted', 'KVM_ACL': 'false', 'GITHUB_SHA': 'ab' * 20,
        'GITHUB_WORKSPACE': '/work'}
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}

def no_credential(argv, **kw):
    return subprocess.CompletedProcess(argv, 1, '', '')

class JobAndUserTest(unittest.TestCase):
    def test_only_this_workflow_on_main_by_dispatch_without_kvm_acl(self):
        self.assertEqual(tp.job_problems(trial_env()), [])
        for key, value in (('GITHUB_EVENT_NAME', 'push'), ('GITHUB_REF', 'refs/heads/feature'),
                ('GITHUB_REPOSITORY_ID', '1'), ('GITHUB_WORKFLOW_REF', tp.WORKFLOW_REF.replace('main', 'other')),
                ('RUNNER_ENVIRONMENT', 'github-hosted'), ('KVM_ACL', 'true'), ('KVM_ACL', None),
                ('GITHUB_SHA', 'main')):
            with self.subTest(key=key, value=value):
                self.assertTrue(tp.job_problems(trial_env(**{key: value})))

    def test_root_sudo_or_privileged_groups_are_refused(self):
        self.assertEqual(tp.user_problems(1001, 1001, ['rose-test', 'kvm'], False), [])
        for args, expect in (((0, 0, ['root'], False), 'root'), ((1001, 0, [], False), 'root'),
                ((1001, 1001, ['kvm'], True), 'sudo'), ((1001, 1001, ['kvm', 'docker'], False), 'docker'),
                ((1001, 1001, ['sudo'], False), 'sudo')):
            with self.subTest(args=args):
                self.assertTrue(any(expect in p for p in tp.user_problems(*args)))

class HostFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='trial-preflight-'))
        self.addCleanup(__import__('shutil').rmtree, self.tmp, True)

    def deadline(self, text, mode=0o444, dir_mode=0o755, owner=0):
        directory = Path(tempfile.mkdtemp(dir=self.tmp))/'etc'
        directory.mkdir()
        os.chmod(directory, dir_mode)
        path = directory/'deadline'
        path.write_text(text)
        os.chmod(path, mode)
        real = os.lstat
        def fake_lstat(p):
            info = real(p)
            if Path(p) in (path, directory):  # ownership simulated (directory root, file `owner`); real mode bits
                uid = owner if Path(p) == path else 0
                return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink, uid, 0,
                    *tuple(info)[6:10]))
            return info
        patch = mock.patch.object(tp.os, 'lstat', fake_lstat)
        patch.start()
        self.addCleanup(patch.stop)
        return path

    def test_the_deadline_needs_35_minutes_and_at_most_the_trial(self):
        path = self.deadline(f'{NOW + 36 * 60}\n')
        self.assertEqual(tp.deadline_facts(NOW, path), ([], {'deadline': 'present', 'remainingSeconds': 36 * 60}))
        for seconds, expect in ((35 * 60 - 1, 'at least 2100'), (-60, 'at least 2100'), (3 * 3600, 'beyond')):
            with self.subTest(seconds=seconds):
                path.chmod(0o644)
                path.write_text(f'{NOW + seconds}\n')
                path.chmod(0o444)
                self.assertTrue(any(expect in p for p in tp.deadline_facts(NOW, path)[0]))
        self.assertEqual(tp.deadline_facts(NOW, path.with_name('missing'))[1], {'deadline': 'absent'})

    def test_an_unsafe_or_malformed_deadline_is_refused(self):
        for text, mode, dir_mode, owner, expect in (
                (f'{NOW + 3600}', 0o464, 0o755, 0, 'unsafe-file'),
                (f'{NOW + 3600}', 0o446, 0o755, 0, 'unsafe-file'),
                (f'{NOW + 3600}', 0o444, 0o755, 1001, 'unsafe-file'),
                (f'{NOW + 3600}', 0o444, 0o775, 0, 'unsafe-directory'),
                ('soon', 0o444, 0o755, 0, 'malformed'),
                (f'{NOW + 3600} extra', 0o444, 0o755, 0, 'malformed')):
            with self.subTest(expect=expect, mode=oct(mode), owner=owner):
                path = self.deadline(text, mode, dir_mode, owner)
                self.assertEqual(tp.deadline_facts(NOW, path)[1]['deadline'], expect)
        link = path.with_name('link')
        link.symlink_to(path)
        self.assertEqual(tp.deadline_facts(NOW, link)[1]['deadline'], 'unsafe-file')

    def test_kvm_must_already_be_a_usable_character_device_and_is_never_changed(self):
        self.assertEqual(tp.kvm_facts(self.tmp/'absent')[1], {'kvm': 'absent'})
        regular = self.tmp/'kvm'
        regular.write_text('')
        self.assertIn('not a character device', tp.kvm_facts(regular)[0][0])
        null = Path('/dev/null')  # a real character device; world-writable, so refused as the trial expects 0660
        problems, facts = tp.kvm_facts(null)
        self.assertIn('world-writable', problems[0])
        before = os.stat(null)
        self.assertEqual((os.stat(null).st_mode, os.stat(null).st_uid), (before.st_mode, before.st_uid))
        code = CODE.split('"""', 2)[2]  # the code after the module docstring
        for banned in ('os.chmod', 'os.chown', 'setfacl', "'chmod'", 'usermod', 'gpasswd', 'udevadm', 'O_CREAT', "'w'"):
            self.assertNotIn(banned, code, banned)
        self.assertEqual(code.count("run(['sudo'"), 1, 'sudo is only probed')
        self.assertIn("run(['sudo', '-n', 'true']", code)

    def test_paths_are_constants_never_environment_or_arguments(self):
        self.assertEqual((tp.DEADLINE_FILE, tp.KVM), (Path('/etc/roseos-systemui-test/deadline'), Path('/dev/kvm')))
        self.assertNotIn('os.environ.get', CODE)
        self.assertEqual(CODE.count("add_argument("), 1, 'only --evidence')

    def test_a_persisted_checkout_credential_is_refused(self):
        found = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, 'http.https://github.com/.extraheader x\n', '')
        self.assertEqual(tp.credential_problems('/work', found), ['the checkout persisted a git credential'])
        self.assertEqual(tp.credential_problems('/work', no_credential), [])
        self.assertTrue(tp.credential_problems(None, no_credential))

    def test_check_combines_everything_into_fixed_facts(self):
        path = self.deadline(f'{NOW + 3600}\n')
        problems, record = tp.check(trial_env(KVM_ACL='true'), 1001, 1001, ['kvm'], False, NOW, no_credential,
            kvm=self.tmp/'absent', deadline=path)
        self.assertEqual(record['result'], 'refused')
        self.assertTrue(any('KVM_ACL' in p for p in problems) and any('/dev/kvm is absent' in p for p in problems))
        self.assertNotIn(str(self.tmp), json.dumps(record), 'no host path in the record')

class WorkflowTest(unittest.TestCase):
    def test_hosted_is_the_default_and_the_trial_is_an_exact_group_and_label(self):
        self.assertIn("      runner:\n        description: 'hosted (ubuntu-24.04) or the isolated self-hosted trial runner "
            "(EXP006-ISOLATED-TRIAL-001; approved trial only)'\n        type: choice\n        options:\n          - hosted\n"
            "          - isolated-trial\n        default: hosted\n", WORKFLOW)
        self.assertIn("    runs-on: ${{ inputs.runner == 'isolated-trial' && fromJSON('{\"group\":\"roseos-systemui-test\","
            "\"labels\":[\"self-hosted\",\"roseos-systemui-test\"]}') || 'ubuntu-24.04' }}\n", WORKFLOW)
        self.assertEqual(WORKFLOW.count('self-hosted'), 3, 'only that runs-on, the header comment and the input text')

    def test_the_guard_runs_before_checkout_and_the_preflight_before_anything_else(self):
        steps = WORKFLOW[WORKFLOW.index('    steps:'):]
        guard, checkout = steps.index('Isolated trial guard (before checkout)'), steps.index('actions/checkout@')
        preflight, static = steps.index('Isolated trial preflight'), steps.index('Static and unit checks')
        self.assertLess(guard, checkout)
        self.assertLess(checkout, preflight)
        self.assertLess(preflight, static)
        block = steps[guard:checkout]
        for needle in ("if: inputs.runner == 'isolated-trial'", '"$GITHUB_EVENT_NAME" != workflow_dispatch',
                '"$GITHUB_REF" != refs/heads/main', '"$GITHUB_REPOSITORY_ID" != 1428996648',
                f'"$GITHUB_WORKFLOW_REF" != "{tp.WORKFLOW_REF}"', '"$KVM_ACL" != false'):
            self.assertIn(needle, block, needle)
        self.assertIn("if: inputs.runner == 'isolated-trial'", steps[preflight:static])
        self.assertIn('persist-credentials: false', steps[checkout:preflight])

    def test_the_kvm_grant_cannot_run_on_the_trial(self):
        run = WORKFLOW[WORKFLOW.index('- name: Run the experiment'):WORKFLOW.index('- name: Evidence')]
        self.assertIn('if [ "$KVM_ACL" = "true" ]; then', run, 'the grant stays behind kvm_acl, which the guard refuses')
        for needle in ('timeout-minutes: 30', 'cancel-in-progress: false', 'permissions:\n  contents: read'):
            self.assertIn(needle, WORKFLOW)

if __name__ == '__main__':
    unittest.main()
