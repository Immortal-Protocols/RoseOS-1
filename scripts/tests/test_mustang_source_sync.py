"""Source sync uses exact argv, the pinned checkout, and a bounded stop."""
import json
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts')]
import mustang_source_sync as sync


class SourceSyncRecipeTest(unittest.TestCase):
    def approval(self):
        record = sync.load()
        return {'schema': 'roseos.mustang-source-sync-approval.v1', 'scope': 'source-sync-only',
                'recipeSha256': sync.recipe_sha256(),
                **{flag: record[flag] for flag in sync.FLAGS}}

    def test_committed_recipe_is_not_admitted_and_pins_the_literal_revision(self):
        record = sync.load()
        self.assertIsNone(record['approval'])
        self.assertFalse(record['executed'])
        self.assertFalse(record['compileAuthorized'])
        self.assertFalse(record['enrollmentClaimed'])
        self.assertFalse(record['oidcTested'])
        self.assertEqual(record['argv'], sync.canonical_argv())
        self.assertEqual(record['argv'], sync.canonical_argv({'ROSE_REVISION': ''}))
        self.assertEqual(record['argv'], sync.canonical_argv({'ROSE_REVISION': 'deadbeef'}))
        flat = [part for command in record['argv'] for part in command]
        self.assertIn(sync.REVISION, flat)
        self.assertNotIn('deadbeef', ''.join(flat))
        self.assertNotIn('$ROSE_REVISION', ''.join(flat))
        init = [command for command in record['argv'] if 'repo' in command and 'init' in command][0]
        self.assertEqual(init[init.index('-b')+1], sync.REVISION)
        self.assertEqual(sync.main(), 4)
        with self.assertRaisesRegex(ValueError, 'fields differ'):
            sync.admit(None)

    def test_repo_commands_run_as_mustang_runner_in_the_source_directory(self):
        for command in sync.canonical_argv():
            if 'repo' not in command:
                continue
            self.assertEqual(command[:3], ['/usr/bin/env', '--chdir', sync.SOURCE])
            self.assertNotIn('sudo', command)
            self.assertNotIn('root', command)

    def test_parent_hash_admits_source_sync_only(self):
        got = sync.admit(self.approval())
        self.assertEqual(got['admitted'], 'source-sync-only')
        self.assertFalse(got['executed'])
        self.assertEqual(got['argv'], sync.canonical_argv())
        stamped = self.approval()
        stamped['approvedAt'] = '2026-11-07T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'fields differ'):
            sync.admit(stamped)
        wrong = self.approval()
        wrong['recipeSha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            sync.admit(wrong)
        broader = self.approval()
        broader['compileAuthorized'] = True
        with self.assertRaisesRegex(ValueError, 'more than source sync'):
            sync.admit(broader)

    def test_argv_equality_rejects_a_changed_revision_or_an_extra_command(self):
        record = json.loads(json.dumps(sync.load()))
        init = [command for command in record['argv'] if 'repo' in command and 'init' in command][0]
        init[init.index('-b')+1] = 'deadbeef'
        with self.assertRaisesRegex(ValueError, 'argv differs'):
            sync.validate(record)
        extra = json.loads(json.dumps(sync.load()))
        extra['argv'].append(['repo', 'sync', '-c'])
        with self.assertRaisesRegex(ValueError, 'argv differs'):
            sync.validate(extra)

    def test_host_head_and_manifest_fail_closed(self):
        sync.require_before_sync(
            '5585425566393384049\n', '4183bff8-6be4-46bb-a641-2193d19813ee',
            'mustang-runner 750\n', sync.REVISION+'\n',
            sync.MANIFEST_SHA+'  os/pinned-manifest.xml\n')
        with self.assertRaisesRegex(ValueError, 'instance differs'):
            sync.require_host('0\n', '4183bff8-6be4-46bb-a641-2193d19813ee')
        with self.assertRaisesRegex(ValueError, 'disk uuid differs'):
            sync.require_host('5585425566393384049', 'other')
        with self.assertRaisesRegex(ValueError, 'git HEAD'):
            sync.require_before_sync('5585425566393384049', '4183bff8-6be4-46bb-a641-2193d19813ee',
                                     'mustang-runner 750', 'deadbeef', sync.MANIFEST_SHA+'  os/pinned-manifest.xml')
        with self.assertRaisesRegex(ValueError, 'manifest hash'):
            sync.require_before_sync('5585425566393384049', '4183bff8-6be4-46bb-a641-2193d19813ee',
                                     'mustang-runner 750', sync.REVISION, '0'*64+'  os/pinned-manifest.xml')

    def test_resolved_manifest_must_match_the_pinned_project_set(self):
        exact = sync.normalized_resolved()
        evidence = sync.require_resolved(exact)
        self.assertGreater(evidence['projectCount'], 1000)
        self.assertFalse(evidence['compileAuthorized'])
        changed_commit = sync.normalized_resolved(mutate=lambda projects: [projects[0][:3]+('b'*40,)]+projects[1:])
        with self.assertRaisesRegex(ValueError, 'pinned project set'):
            sync.require_resolved(changed_commit)
        changed_path = sync.normalized_resolved(mutate=lambda projects: [(projects[0][0], 'other/path', projects[0][2], projects[0][3])]+projects[1:])
        with self.assertRaisesRegex(ValueError, 'pinned project set'):
            sync.require_resolved(changed_path)
        fake = ('<manifest>'+''.join(
            f'<project name="p{i}" path="p{i}" remote="aosp" revision="{"a"*40}"/>' for i in range(evidence['projectCount']))
            +'</manifest>').encode()
        with self.assertRaisesRegex(ValueError, 'pinned project set'):
            sync.require_resolved(fake)

    def test_failure_and_timeout_request_vm_stop(self):
        stops = []
        sync.watch_sync(0, 10, stops.append)
        self.assertEqual(stops, [])
        with self.assertRaisesRegex(ValueError, 'did not stop the VM'):
            sync.watch_sync(1, 10, stops.append)
        self.assertEqual(stops[-1]['reason'], 'source-sync-failed')
        self.assertFalse(stops[-1]['authenticatedStop'])
        self.assertFalse(stops[-1]['localControllerStop'])
        self.assertNotIn('argv', stops[-1])
        with self.assertRaisesRegex(ValueError, 'bounded timeout'):
            sync.watch_sync(None, sync.SYNC_TIMEOUT_SECONDS, stops.append)
        self.assertEqual(stops[-1]['reason'], 'source-sync-timeout')
        self.assertFalse(stops[-1]['authenticatedStop'])
        with self.assertRaisesRegex(ValueError, 'crash stop'):
            sync.watch_sync(None, 0, stops.append, timeout=14400)
        with self.assertRaisesRegex(ValueError, 'did not stop the VM'):
            sync.on_command('git rev-parse', 1, stops.append)
        self.assertEqual(stops[-1]['command'], 'git rev-parse')
        self.assertLess(sync.SYNC_TIMEOUT_SECONDS, 14400)
        names = [step['name'] for step in sync.steps()]
        self.assertLess(names.index('git-init'), names.index('repo-init'))
        self.assertLess(names.index('manifest-head'), names.index('repo-sync'))
        self.assertIn('.repo/manifests', ' '.join(sync.canonical_argv()[names.index('manifest-head')]))

    def answer(self, argv):
        if 'instance/id' in ' '.join(argv):
            return '5585425566393384049\n'
        if 'findmnt' in argv:
            return '4183bff8-6be4-46bb-a641-2193d19813ee\n'
        if 'rev-parse' in argv:
            return sync.REVISION+'\n'
        if 'sha256sum' in argv and sync.PIN in argv[-1]:
            return sync.MANIFEST_SHA+'  '+sync.PIN+'/os/pinned-manifest.xml\n'
        if 'sha256sum' in argv:
            return sync.MANIFEST_SHA+'  .repo/manifests/os/pinned-manifest.xml\n'
        if argv[-2:] == ['cat', 'resolved-manifest.xml']:
            return sync.normalized_resolved().decode()
        return ''

    def test_orchestration_stops_on_sync_failure_and_timeout_and_records_observations(self):
        stops = []
        spawned = []

        class Proc:
            def __init__(self, argv, code):
                self.argv = argv
                self.returncode = code
                self.killed = False
            def poll(self):
                return self.returncode
            def kill(self):
                self.killed = True
                self.returncode = -9
            def output(self):
                return answer(self.argv)

        answer = self.answer
        def spawn(argv):
            spawned.append(argv)
            return Proc(argv, 0)

        receipt = sync.orchestrate(self.approval(), 'owner.json', spawn, stops.append, lambda: 0, lambda _seconds: None)
        self.assertTrue(receipt['executed'])
        self.assertFalse(receipt['historical']['oidcExercised'])
        self.assertEqual(receipt['observed']['manifest-head'], sync.REVISION)
        self.assertEqual(receipt['observed']['projectCount'], sync.require_resolved(sync.normalized_resolved())['projectCount'])
        self.assertFalse(receipt['authenticatedStop'])
        self.assertNotIn('gcloud', receipt['cancellation'])
        self.assertEqual(stops, [])
        self.assertIn('git', spawned[4])
        self.assertIn(sync.PIN, spawned[4])
        failed = []
        ran = []

        def failing(argv):
            ran.append(list(argv))
            return Proc(argv, 1 if 'sync' in argv else 0)

        with self.assertRaisesRegex(ValueError, 'did not stop the VM'):
            sync.orchestrate(self.approval(), 'owner.json', failing, failed.append, lambda: 0, lambda _seconds: None)
        self.assertEqual(failed[-1]['reason'], 'source-sync-failed')
        self.assertFalse(failed[-1]['authenticatedStop'])
        self.assertNotIn('argv', failed[-1])
        self.assertTrue(any('sync' in command for command in ran))
        self.assertFalse(any(command[-2:] == ['cat', 'resolved-manifest.xml'] for command in ran))
        clock = {'t': 0}
        killed = {}

        class Hung:
            def poll(self):
                return None
            def kill(self):
                killed['yes'] = True
            def output(self):
                return ''

        def hang(argv):
            return Hung()
        def tick(seconds):
            clock['t'] += seconds
        timed = []
        with self.assertRaisesRegex(ValueError, 'bounded timeout'):
            sync.run_bounded(['repo', 'sync'], 30, hang, timed.append, lambda: clock['t'], tick)
        self.assertTrue(killed.get('yes'))
        self.assertEqual(timed[-1]['reason'], 'source-sync-timeout')
        denied = []
        with self.assertRaises(ValueError):
            sync.orchestrate({'schema': 'roseos.mustang-source-sync-approval.v1'}, 'owner.json',
                             lambda argv: (_ for _ in ()).throw(AssertionError('spawned')),
                             denied.append, lambda: 0, lambda _seconds: None)
        self.assertEqual(denied[-1]['reason'], 'source-sync-not-admitted')
        self.assertFalse(denied[-1]['authenticatedStop'])
        self.assertNotIn('argv', denied[-1])

    def test_real_poll_reads_more_than_a_pipe_without_deadlocking(self):
        size = 256 * 1024
        argv = [sys.executable, '-c',
                'import sys; sys.stdout.buffer.write(b"o"*%d); sys.stderr.buffer.write(b"e"*%d)' % (size, size)]
        with tempfile.TemporaryDirectory() as temporary:
            seen = []

            def spawn(command):
                proc = sync.spawn_subprocess(command, temporary)
                seen.append(proc)
                return proc

            started = time.monotonic()
            text = sync.run_bounded(argv, 20, spawn, lambda _detail: None, time.monotonic, time.sleep)
            elapsed = time.monotonic() - started
            self.assertEqual(len(text), size)
            self.assertLess(elapsed, 15)
            self.assertEqual(len(Path(seen[-1].stderr_path).read_bytes()), size)
            self.assertLessEqual(len(text.encode()), sync.MAX_STDOUT_RECEIPT)

    def test_sigterm_kills_the_child_and_requests_vm_stop(self):
        argv = [sys.executable, '-c', 'import time; time.sleep(30)']
        with tempfile.TemporaryDirectory() as temporary:
            proc = sync.spawn_subprocess(argv, temporary)
            handler = sync.cancellation_handler({'proc': proc})
            try:
                handler(signal.SIGTERM, None)
            except SystemExit as error:
                self.assertEqual(error.code, 4)
            deadline = time.monotonic() + 8
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertIsNotNone(proc.poll())
            source = Path(sync.__file__).read_text()
            self.assertNotIn('mustang_controller.py', source)
            self.assertNotIn('gcloud', source)
            self.assertEqual(sync.mustang_lane.CONFIRMED_STOP['seconds'], 14400)

    def test_execute_refuses_a_local_stop_and_a_hosted_runner_outside_the_source_job(self):
        called = []

        def refuse(argv, *args, **kwargs):
            called.append(argv)
            raise AssertionError('guest executed a process during a refused run')

        with mock.patch('mustang_source_sync.subprocess.run', refuse), \
                mock.patch('mustang_source_sync.subprocess.Popen', refuse):
            closed = sync.main(['run', '--execute', '--ownership-record', 'owner.json'])
            self.assertEqual(closed, 4)
            outside = sync.main(['run', '--execute'])
            self.assertEqual(outside, 4)
        self.assertEqual(called, [])
        workflow = (ROOT/'.github/workflows/mustang-ota-dedicated.yml').read_text()
        source = workflow.split('\n  source:\n', 1)[1].split('\n  cleanup:\n', 1)[0]
        self.assertIn('timeout-minutes: 185', source)
        self.assertIn('timeout-minutes: 180', source)
        self.assertIn('python3 scripts/mustang_source_sync.py run --execute', source)
        self.assertNotIn('mustang_controller.py', source)
        self.assertNotIn('id-token:', source)
        self.assertNotIn('sudo', source)
        cleanup = workflow.split('\n  cleanup:\n', 1)[1]
        self.assertIn('- source', cleanup)
        self.assertIn('always() && needs.controller.result != \'skipped\'', cleanup)
        self.assertIn('python3 scripts/mustang_controller.py stop --ownership-record', cleanup)
        self.assertIn('if: inputs.source_only == false', workflow.split('\n  builder:\n', 1)[1].split('\n    runs-on:', 1)[0])
        unit = (ROOT/'os/build-only/mustang-ephemeral-runner.service').read_text()
        self.assertIn('NoNewPrivileges=true', unit)
        self.assertIn('User=mustang-runner', unit)

    def test_pin_fetch_is_idempotent_and_repo_init_checks_out_the_sha(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            origin = root/'origin'
            pin = root/'pin'
            source = root/'source'
            subprocess.run(['git', 'init', '-q', '-b', 'main', str(origin)], check=True)
            subprocess.run(['git', '-C', str(origin), 'config', 'user.email', 't@example.com'], check=True)
            subprocess.run(['git', '-C', str(origin), 'config', 'user.name', 't'], check=True)
            subprocess.run(['git', '-C', str(origin), 'config', 'uploadpack.allowReachableSHA1InWant', 'true'], check=True)
            manifest = origin/'pinned-manifest.xml'
            manifest.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<manifest>\n'
                                '  <remote name="aosp" fetch="."/>\n'
                                '  <default remote="aosp" revision="refs/heads/main"/>\n'
                                '  <project name="toy" path="toy" revision="0123456789abcdef0123456789abcdef01234567"/>\n'
                                '</manifest>\n')
            subprocess.run(['git', '-C', str(origin), 'add', 'pinned-manifest.xml'], check=True)
            subprocess.run(['git', '-C', str(origin), 'commit', '-q', '-m', 'init'], check=True)
            revision = subprocess.check_output(['git', '-C', str(origin), 'rev-parse', 'HEAD'], text=True).strip()
            commands = sync.pin_commands(str(pin), str(origin), revision)
            commands[0] = ['git', 'init', '-q', '-b', 'main', str(pin)]
            for _attempt in (1, 2):
                for command in commands:
                    subprocess.run(command, check=True)
            self.assertEqual(subprocess.check_output(['git', '-C', str(pin), 'rev-parse', 'HEAD'], text=True).strip(), revision)
            partial = root/'partial'
            subprocess.run(['git', 'init', '-q', '-b', 'main', str(partial)], check=True)
            subprocess.run(['git', '-C', str(partial), 'config', 'remote.origin.url', str(origin)], check=True)
            for command in sync.pin_commands(str(partial), str(origin), revision)[1:]:
                subprocess.run(command, check=True)
            self.assertEqual(subprocess.check_output(['git', '-C', str(partial), 'rev-parse', 'HEAD'], text=True).strip(), revision)
            repo = shutil.which('repo')
            if repo is None:
                return
            source.mkdir()
            init = sync.repo_init_command(str(pin), revision, 'pinned-manifest.xml')
            init[0] = repo
            subprocess.run(init, cwd=source, check=True)
            head = subprocess.check_output(['git', '-C', str(source/'.repo/manifests'), 'rev-parse', 'HEAD'], text=True).strip()
            self.assertEqual(head, revision)


if __name__ == '__main__':
    unittest.main()
