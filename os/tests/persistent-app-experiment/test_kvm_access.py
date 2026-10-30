"""kvm_access.py (EXP006-KVM-001) against a simulated device and ACL tools: never the executor's /dev/kvm or sudo.

The executor has no getfacl/setfacl and runs as root, so real ACL execution is not available here. FakeSystem models
the device's identity and POSIX access ACL, `setfacl --no-mask --modify` (a named entry; a mask equal to the owning
group's rights is created when none exists, as with --no-mask) and `--set-file` (exact replacement), plus the job
user's effective access. A pass shows these decisions and restorations, not a real runner's ACL behaviour."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kvm_access as ka  # noqa: E402

WORKFLOW = (HERE.parents[2]/'.github/workflows/systemui-persistent-experiment.yml').read_text()
UID = 1001
HOSTED = {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_OS': 'Linux', 'platform': 'Linux'}
BASE = [('user', '', 'rw-'), ('group', '', 'rw-'), ('other', '', '---')]

class FakeSystem:
    def __init__(self, acl=None, uid=UID, env=None, mode=stat.S_IFCHR, owner=0, tools=('getfacl', 'setfacl', 'sudo'),
                 sudo=True, api=12, gid=992):
        self.acl = list(acl or BASE)
        self._uid, self.env, self.mode, self.owner, self.tools, self.sudo, self.api, self.gid = \
            uid, dict(HOSTED if env is None else env), mode, owner, tools, sudo, api, gid
        self.inode, self.calls, self.setfacl_exit, self.setfacl_extra, self.restore_exit, self.exists = 7, [], 0, None, 0, True

    def uid(self):
        return self._uid
    def username(self, uid):
        return 'runner'
    def environment(self):
        return self.env
    def lstat(self, path):
        assert path == ka.DEVICE
        if not self.exists:
            raise FileNotFoundError(path)
        perms = {'r': 4, 'w': 2, 'x': 1, '-': 0}
        bits = lambda p: sum(perms[c] for c in p)
        owner = next(p for k, q, p in self.acl if k == 'user' and not q)
        group = next((p for k, q, p in self.acl if k == 'mask'), next(p for k, q, p in self.acl if k == 'group' and not q))
        other = next(p for k, q, p in self.acl if k == 'other')
        mode = self.mode | bits(owner) << 6 | bits(group) << 3 | bits(other)
        return os.stat_result((mode, self.inode, 5, 1, self.owner, self.gid, 0, 0, 0, 0))
    def which(self, tool):
        return f'/usr/bin/{tool}' if tool in self.tools else None
    def access(self, path):
        entry = ka.effective(self.acl).get(('user', str(self._uid)))
        return bool(entry) and ka.has_rw(entry)
    def kvm_api(self, path):
        if isinstance(self.api, Exception):
            raise self.api
        return self.api
    def run(self, argv):
        self.calls.append(argv)
        done = lambda code=0, out='': subprocess.CompletedProcess(argv, code, out, '')
        if argv == ['sudo', '-n', 'true']:
            return done(0 if self.sudo else 1)
        if argv[0] == 'getfacl' or argv[:3] == ['sudo', '-n', 'getfacl']:
            return done(0, ka.text(self.acl))
        if argv[:3] == ['sudo', '-n', 'setfacl']:
            assert argv[-2:] == ['--', ka.DEVICE], argv
            if argv[3] == '--no-mask':
                if self.setfacl_exit:
                    return done(self.setfacl_exit)
                kind, qualifier, perms = 'user', argv[5].split(':')[1], argv[5].split(':')[2] + '-'
                self.acl.append((kind, qualifier, perms))
                if not any(k == 'mask' for k, _, _ in self.acl):
                    self.acl.append(('mask', '', next(p for k, q, p in self.acl if k == 'group' and not q)))
                if self.setfacl_extra:
                    self.setfacl_extra(self)
                return done()
            if argv[3].startswith('--set-file='):
                if self.restore_exit:
                    return done(self.restore_exit)
                self.acl = ka.parse(Path(argv[3].split('=', 1)[1]).read_text())
                return done()
        raise AssertionError(f'unexpected command {argv}')

class KvmAccessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='kvm-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state, self.evidence = self.tmp/'state', self.tmp/'evidence'

    def grant(self, system):
        return ka.grant(self.state, self.evidence, system)

    def restore(self, system):
        return ka.restore(self.state, self.evidence, system)

    def recorded(self):
        return json.loads((self.evidence/ka.EVIDENCE).read_text())

    def setfacl_calls(self, system):
        return [c for c in system.calls if c[:3] == ['sudo', '-n', 'setfacl']]

    def test_grant_adds_only_the_job_user_and_restore_returns_the_exact_original(self):
        original = BASE + [('group', '5', 'r--'), ('user', '42', 'rw-'), ('mask', '', 'rw-')]
        system = FakeSystem(acl=original)
        before = system.lstat(ka.DEVICE)
        self.assertEqual(self.grant(system), 0, self.recorded())
        self.assertEqual(self.setfacl_calls(system), [['sudo', '-n', 'setfacl', '--no-mask', '--modify', f'u:{UID}:rw', '--', ka.DEVICE]])
        self.assertEqual(sorted(system.acl), sorted(original + [('user', str(UID), 'rw-')]))
        others = {k: v for k, v in ka.effective(system.acl).items() if k != ('user', str(UID))}
        self.assertEqual(others, ka.effective(original), 'every other principal keeps its effective rights')
        self.assertEqual(stat.S_IMODE(system.lstat(ka.DEVICE).st_mode), stat.S_IMODE(before.st_mode))
        grant = self.recorded()['grant']
        self.assertEqual((grant['result'], grant['uid'], grant['user'], grant['kvmApiVersion']), ('granted', UID, 'runner', 12))
        self.assertEqual(oct(os.stat(self.state).st_mode & 0o777), '0o700')
        self.assertEqual(oct(os.stat(self.state/ka.STATE).st_mode & 0o777), '0o600')
        self.assertEqual(self.restore(system), 0)
        self.assertEqual(system.acl, original, 'exactly the original access ACL')
        self.assertEqual(self.recorded()['restore']['result'], 'restored')
        self.assertFalse((self.state/'kvm-acl-restore.txt').exists(), 'the restore snapshot is removed')
        calls = len(system.calls)
        self.assertEqual(self.restore(system), 0, 'restore is idempotent (trap, then cleanup)')
        self.assertEqual(len(self.setfacl_calls(system)), 2, 'a second restore rewrites nothing')
        self.assertGreater(len(system.calls), calls)

    def test_a_minimal_acl_gains_a_mask_equal_to_the_group_rights_only(self):
        system = FakeSystem()
        self.assertEqual(self.grant(system), 0, self.recorded())
        self.assertIn(('mask', '', 'rw-'), system.acl)
        self.assertEqual(self.restore(system), 0)
        self.assertEqual(system.acl, BASE)

    def test_already_accessible_is_a_no_op(self):
        system = FakeSystem(acl=BASE + [('user', str(UID), 'rw-'), ('mask', '', 'rw-')])
        self.assertEqual(self.grant(system), 0)
        self.assertEqual(self.setfacl_calls(system), [])
        self.assertEqual(self.recorded()['grant']['result'], 'not-needed')
        self.assertEqual(self.restore(system), 0)
        self.assertEqual(self.setfacl_calls(system), [], 'nothing changed, nothing restored')

    def test_wrong_runner_or_root_user_is_refused_before_anything(self):
        for system, expect in ((FakeSystem(uid=0), 'is root'), (FakeSystem(env={**HOSTED, 'RUNNER_ENVIRONMENT': 'self-hosted'}), 'GitHub-hosted'),
                (FakeSystem(env={**HOSTED, 'platform': 'Darwin'}), 'GitHub-hosted'), (FakeSystem(env={}), 'GitHub-hosted')):
            with self.subTest(expect=expect):
                self.assertEqual(self.grant(system), 1)
                self.assertIn(expect, self.recorded()['grant']['reason'])
                self.assertEqual(system.calls, [])

    def test_missing_link_non_character_or_foreign_owned_device_is_refused(self):
        missing = FakeSystem()
        missing.exists = False
        for system, expect in ((missing, 'does not exist'), (FakeSystem(mode=stat.S_IFLNK), 'is a link'),
                (FakeSystem(mode=stat.S_IFREG), 'not a character device'), (FakeSystem(owner=1000), 'not owned by root')):
            with self.subTest(expect=expect):
                self.assertEqual(self.grant(system), 1)
                self.assertIn(expect, self.recorded()['grant']['reason'])
                self.assertEqual(self.setfacl_calls(system), [])

    def test_unavailable_tools_or_sudo_are_refused(self):
        system = FakeSystem(tools=('getfacl', 'sudo'))
        self.assertEqual(self.grant(system), 1)
        self.assertIn("tools unavailable: ['setfacl']", self.recorded()['grant']['reason'])
        system = FakeSystem(sudo=False)
        self.assertEqual(self.grant(system), 1)
        self.assertIn('non-interactive sudo is unavailable', self.recorded()['grant']['reason'])
        self.assertEqual(self.setfacl_calls(system), [])

    def test_a_restrictive_mask_or_group_unsupported_entries_or_an_existing_entry_refuse(self):
        for acl, expect in ((BASE + [('user', '42', 'rw-'), ('mask', '', 'r--')], 'mask rights r--'),
                ([('user', '', 'rw-'), ('group', '', 'r--'), ('other', '', '---')], 'group rights r--'),
                (BASE + [('user', str(UID), 'r--'), ('mask', '', 'rw-')], 'already names this user'),
                (BASE + [('default', '', 'rw-')], 'unsupported ACL entry')):
            with self.subTest(expect=expect):
                system = FakeSystem(acl=acl)
                self.assertEqual(self.grant(system), 1)
                self.assertIn(expect, self.recorded()['grant']['reason'])
                self.assertEqual(self.setfacl_calls(system), [])
                self.assertEqual(system.acl, acl)

    def test_apply_failure_and_unexpected_changes_restore_at_once(self):
        system = FakeSystem()
        system.setfacl_exit = 1
        self.assertEqual(self.grant(system), 1)
        self.assertIn('setfacl exited 1', self.recorded()['grant']['reason'])
        self.assertEqual(system.acl, BASE)
        # setfacl that also widened another principal (here: other gains rw): verification refuses and restores.
        system = FakeSystem(acl=BASE + [('mask', '', 'rw-')])
        system.setfacl_extra = lambda s: s.acl.__setitem__(2, ('other', '', 'rw-'))
        self.assertEqual(self.grant(system), 1)
        self.assertIn('unexpected ACL change', self.recorded()['grant']['reason'])
        self.assertEqual(system.acl, BASE + [('mask', '', 'rw-')])
        self.assertEqual(self.recorded()['restore']['result'], 'restored')

    def test_kvm_not_answering_refuses_and_restores(self):
        for api in (OSError(6, 'no device'), 11):
            with self.subTest(api=api):
                system = FakeSystem(api=api)
                self.assertEqual(self.grant(system), 1)
                self.assertIn('KVM', self.recorded()['grant']['reason'])
                self.assertEqual(system.acl, BASE)

    def test_experiment_failure_or_cancellation_still_restores_and_a_replaced_device_is_untouched(self):
        system = FakeSystem()
        self.assertEqual(self.grant(system), 0)
        # The experiment fails or the step is cancelled: the trap's restore runs; the cleanup step's runs again.
        self.assertEqual(self.restore(system), 0)
        self.assertEqual(self.restore(system), 0)
        self.assertEqual(system.acl, BASE)
        replaced = FakeSystem()
        self.assertEqual(ka.grant(self.tmp/'s2', self.tmp/'e2', replaced), 0)
        replaced.inode = 99
        granted = list(replaced.acl)
        self.assertEqual(ka.restore(self.tmp/'s2', self.tmp/'e2', replaced), 1)
        self.assertEqual(replaced.acl, granted, 'a replaced device is never written')
        self.assertIn('was replaced', json.loads((self.tmp/'e2'/ka.EVIDENCE).read_text())['restore']['reason'])

    def test_restore_failures_are_reported_and_nothing_recorded_is_not_needed(self):
        system = FakeSystem()
        self.assertEqual(self.grant(system), 0)
        system.restore_exit = 1
        self.assertEqual(self.restore(system), 1)
        self.assertEqual(self.recorded()['restore']['result'], 'restore-failed')
        self.assertEqual(ka.restore(self.tmp/'none', self.tmp/'e3', FakeSystem()), 0)
        self.assertEqual(json.loads((self.tmp/'e3'/ka.EVIDENCE).read_text())['restore']['result'], 'not-needed')

    def test_the_module_never_widens_beyond_one_acl_entry(self):
        code = (HERE/'kvm_access.py').read_text().split('"""', 2)[2]
        for banned in ('chmod', 'chown', 'usermod', 'gpasswd', 'udev', 'apt-get', 'os.chmod(DEVICE', "'666'", 'o+rw',
                'input(', 'argv[', 'sys.argv[1]'):
            self.assertNotIn(banned, code.replace('os.chmod(directory, 0o700)', ''), banned)
        self.assertEqual(code.count("['sudo', '-n', 'setfacl'"), 2, 'one modify and one set-file, nothing else')
        self.assertIn("['sudo', '-n', 'setfacl', '--no-mask', '--modify', f'u:{uid}:rw', '--', DEVICE]", code)
        self.assertIn("['sudo', '-n', 'setfacl', f'--set-file={snapshot}', '--', DEVICE]", code)
        self.assertIn("DEVICE = '/dev/kvm'", code)

class WorkflowTest(unittest.TestCase):
    def test_kvm_acl_is_manual_default_false_and_wraps_only_the_experiment(self):
        self.assertIn("      kvm_acl:\n        description: 'Grant this job user rw on the KVM device for the run and restore it "
            "(EXP006-KVM-001; approved runs only)'\n        type: boolean\n        default: false\n", WORKFLOW)
        run = WORKFLOW[WORKFLOW.index('      - name: Run the experiment'):WORKFLOW.index('      - name: Evidence')]
        self.assertIn('KVM_ACL: ${{ inputs.kvm_acl }}', run)
        grant = run.index('kvm_access.py grant')
        self.assertLess(run.index('if [ "$KVM_ACL" = "true" ]; then'), grant)
        self.assertLess(run.index("trap 'python3 os/tests/persistent-app-experiment/kvm_access.py restore"), grant,
            'the restore trap is armed before the grant')
        self.assertIn('EXIT INT TERM', run)
        self.assertIn('|| exit 1', run[grant:run.index('experiment.py run')], 'a refused grant runs no experiment')
        self.assertLess(grant, run.index('experiment.py run'))
        self.assertNotIn('sudo', run, 'the experiment itself never runs as root')
        cleanup = WORKFLOW[WORKFLOW.index('      - name: Remove the test key and AVD'):WORKFLOW.index('      - name: Stage and cleanup summary')]
        self.assertIn('kvm_access.py restore', cleanup)
        self.assertIn('if: always()', cleanup)
        self.assertIn('exit "$restored"', cleanup)
        self.assertIn('test_kvm_access.py -v', WORKFLOW)
        self.assertIn('KVM_ACL: ${{ inputs.kvm_acl }}', WORKFLOW[WORKFLOW.index('Stage and cleanup summary'):])
        for needle in ('timeout-minutes: 30', 'permissions:\n  contents: read', 'runs-on: ubuntu-24.04'):
            self.assertIn(needle, WORKFLOW)
        self.assertNotIn('${{ inputs.kvm_acl }}\n          then', WORKFLOW, 'the input reaches the shell only via env')

if __name__ == '__main__':
    unittest.main()
