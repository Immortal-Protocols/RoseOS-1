"""Offline adversarial checks for the bounded generic dependency adapter."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
sys.path.insert(0, str(ROOT/'scripts/tests'))
import mustang_hostdeps as deps
import mustang_ephemeral as ephemeral
import mustang_lane
import mustang_route
import os_release_shape as workflow_shape


class HostdepsTest(unittest.TestCase):
    def env(self):
        return {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_WORKFLOW_REF': 'Immortal-Protocols/RoseOS-1/.github/workflows/mustang-ota-dedicated.yml@refs/heads/main',
                'GITHUB_REPOSITORY_ID': '1428996648', 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
                'GITHUB_SHA': 'a'*40, 'GITHUB_JOB': 'hostdeps'}

    def test_scope_is_exclusive_and_fresh_main_only(self):
        event = {'inputs': {'hostdeps_only': 'true'}}
        lane, run = deps.guard(self.env(), event)
        self.assertEqual(lane['vm']['instanceId'], '5585425566393384049')
        self.assertEqual(run['runAttempt'], '1')
        for key in ('source_only', 'dry_run', 'publish'):
            for value in (True, 'true', 'yes', 1):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    deps.guard(self.env(), {'inputs': dict(event['inputs'], **{key: value})})
        for key, value in [('GITHUB_RUN_ATTEMPT', '2'), ('GITHUB_REF', 'refs/heads/other'),
                           ('GITHUB_REPOSITORY_ID', '1'), ('GITHUB_EVENT_NAME', 'pull_request')]:
            with self.assertRaises(ValueError):
                deps.guard(dict(self.env(), **{key: value}), event)
        with self.assertRaises(ValueError):
            deps.require_selection({'inputs': {'hostdeps_only': 'false'}})

    def test_plan_and_refused_execute_spawn_nothing(self):
        with patch.object(deps.subprocess, 'Popen', side_effect=AssertionError('spawned')), \
                patch.object(deps.subprocess, 'run', side_effect=AssertionError('spawned')), \
                patch.dict(os.environ, {}, clear=True):
            self.assertEqual(deps.main([]), 4)
            self.assertEqual(deps.main(['run']), 4)
            self.assertEqual(deps.main(['run', '--execute']), 4)
        recipe = deps.recipe()
        self.assertEqual(recipe['jobs'], 24)
        self.assertLess(recipe['timeoutSeconds']+2*deps.KILL_GRACE, 155*60)
        self.assertEqual(len(recipe['tools']), 14)
        self.assertNotIn('mustang-cur-user', deps.argv(recipe))
        self.assertNotIn('otatools-package', deps.argv(recipe))
        self.assertIn('-j24', deps.argv(recipe))
        self.assertEqual(deps.argv(recipe)[2:4], ['sdk_phone64_x86_64', 'out_adevtool_deps'])

    def test_child_does_not_inherit_auth_or_shell_injection(self):
        with patch.dict(os.environ, {'GCP_SERVICE_ACCOUNT_JSON': 'secret', 'GH_TOKEN': 'token',
                                     'BASH_ENV': '/evil', 'OUT_DIR': '/other-host', 'GITHUB_OUTPUT': '/evil'}):
            clean = deps.child_env('/runner', '/attempt/tmp')
        for key in ('GCP_SERVICE_ACCOUNT_JSON', 'GH_TOKEN', 'BASH_ENV', 'OUT_DIR', 'GITHUB_OUTPUT', 'GITHUB_ENV',
                    'ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'GOOGLE_APPLICATION_CREDENTIALS'):
            self.assertNotIn(key, clean)
        self.assertEqual(clean['TMPDIR'], '/attempt/tmp')
        self.assertEqual(clean['GIT_CONFIG_GLOBAL'], '/dev/null')

    def failure_environment(self, directory):
        event = Path(directory)/'event.json'
        event.write_text(json.dumps({'inputs': {'hostdeps_only': True}}))
        return dict(self.env(), RUNNER_TEMP=directory, GITHUB_EVENT_PATH=str(event))

    def test_pre_attempt_failure_gets_a_minimal_unattested_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self.failure_environment(tmp)
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(deps, 'execute', side_effect=ValueError('Mustang builder lock is absent; host preparation required')):
                self.assertEqual(deps.main(['run', '--execute']), 4)
            summary = json.loads((Path(tmp)/'hostdeps-failure.json').read_text())
            self.assertEqual(summary['failureCode'], 'lock-absent')
            self.assertEqual(summary['run']['source'], 'a'*40)
            self.assertEqual(summary['expectedBuilder']['instanceId'], '5585425566393384049')
            for key in ('success', 'guestIdentityVerified', 'makeSuccessAttested',
                        'distributionAttested', 'authenticatedStop', 'otaAuthorized'):
                self.assertFalse(summary[key])
            self.assertNotIn('distribution', summary)

    def test_failure_summary_never_serializes_untrusted_exception_text_or_inherited_secrets(self):
        for error, code in [(PermissionError('secret access token'), 'permission-denied'),
                            (ValueError('secret private path'), 'admission-or-build-refused'),
                            (subprocess.CalledProcessError(1, ['secret-command']), 'process-error')]:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                env = dict(self.failure_environment(tmp), GH_TOKEN='secret-inherited')
                deps.report_failure(error, env)
                content = (Path(tmp)/'hostdeps-failure.json').read_text()
                self.assertNotIn('secret', content)
                self.assertEqual(json.loads(content)['failureCode'], code)

    def test_failure_summary_is_write_once_and_refuses_foreign_runs_or_linked_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self.failure_environment(tmp)
            for change in ({'GITHUB_RUN_ATTEMPT': '2'}, {'GITHUB_JOB': 'builder'},
                           {'GITHUB_REPOSITORY_ID': 'other'}):
                with self.assertRaises(ValueError):
                    deps.report_failure(ValueError('hostdeps Make failed'), dict(env, **change))
                self.assertFalse((Path(tmp)/'hostdeps-failure.json').exists())
            linked = Path(tmp)/'linked'
            linked.symlink_to(tmp, target_is_directory=True)
            with self.assertRaises(deps.readers._Refused):
                deps.report_failure(ValueError('hostdeps Make failed'), dict(env, RUNNER_TEMP=str(linked)))
            deps.report_failure(ValueError('hostdeps Make failed'), env)
            original = (Path(tmp)/'hostdeps-failure.json').read_bytes()
            with self.assertRaises(FileExistsError):
                deps.report_failure(ValueError('hostdeps cancelled'), env)
            self.assertEqual((Path(tmp)/'hostdeps-failure.json').read_bytes(), original)

    def test_failure_summary_write_error_preserves_the_failed_cli_exit(self):
        with patch.dict(os.environ, self.env(), clear=True), \
                patch.object(deps, 'execute', side_effect=PermissionError('private-path')), \
                patch.object(deps, 'report_failure', side_effect=OSError('cannot write')):
            self.assertEqual(deps.main(['run', '--execute']), 4)

    def test_recipe_cannot_become_a_foreign_output_path_or_arbitrary_make_option(self):
        original = deps.readers.recipe()
        for value in ('../other-output', '/home/other-builder/out'):
            changed = json.loads(json.dumps(original))
            changed['adevtool']['depsDir'] = value
            with patch.object(deps.readers, 'recipe', return_value=changed), self.assertRaisesRegex(ValueError, 'recipe'):
                deps.recipe()
        for value in ('--eval=bad', '../../target', 'aapt2;bad', 'apksigner', 'droid', 'otatools-package', 'vendorbootimage'):
            changed = json.loads(json.dumps(original))
            changed['adevtool']['depsTools'][0] = value
            with patch.object(deps.readers, 'recipe', return_value=changed), self.assertRaisesRegex(ValueError, 'recipe'):
                deps.recipe()
            with patch.object(deps.readers, 'recipe', return_value=changed), self.assertRaisesRegex(ValueError, 'recipe'):
                deps.guard(self.env(), {'inputs': {'hostdeps_only': True}})

    def test_dedicated_guard_rejects_mixed_scope_before_any_process(self):
        spec = importlib.util.spec_from_file_location('hostdeps_guard_test', ROOT/'scripts/mustang-dedicated.py')
        dedicated = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dedicated)
        with tempfile.TemporaryDirectory() as tmp:
            event = Path(tmp)/'event.json'
            event.write_text(json.dumps({'inputs': {'hostdeps_only': True, 'publish': True}}))
            env = dict(self.env(), GITHUB_EVENT_PATH=str(event), MUSTANG_HOSTDEPS_ONLY='true', MUSTANG_DRY_RUN='true')
            with patch.dict(os.environ, env, clear=True), patch.object(sys, 'argv', ['dedicated', 'guard']), \
                    patch.object(subprocess, 'run', side_effect=AssertionError('process started')):
                with self.assertRaisesRegex(ValueError, 'sole selected'):
                    dedicated.main()

    def test_guest_refuses_the_wrong_job_or_user_before_host_inspection(self):
        event = {'inputs': {'hostdeps_only': True}}
        with patch.object(deps, 'read_event', return_value=event), \
                patch.object(deps.pwd, 'getpwuid', return_value=SimpleNamespace(pw_name='other-user')):
            with self.assertRaisesRegex(ValueError, 'dedicated guest job'):
                deps.guest_context(self.env())
        with patch.object(deps, 'read_event', return_value=event):
            with self.assertRaisesRegex(ValueError, 'dedicated guest job'):
                deps.guest_context(dict(self.env(), GITHUB_JOB='builder'))

    def test_lock_is_nonblocking_and_refuses_links_and_special_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root/'mustang.lock'
            with self.assertRaisesRegex(ValueError, 'absent'):
                with deps.lock(lock):
                    self.fail('missing lock created')
            self.assertFalse(lock.exists())
            lock.write_text('')
            with deps.lock(lock):
                with self.assertRaisesRegex(ValueError, 'busy'):
                    with deps.lock(lock):
                        self.fail('second writer accepted')
            target = root/'other'
            target.write_text('')
            lock.unlink()
            lock.symlink_to(target)
            with self.assertRaises(OSError):
                with deps.lock(lock):
                    self.fail('symlink accepted')
            lock.unlink()
            os.mkfifo(lock)
            with self.assertRaisesRegex(ValueError, 'plain file'):
                with deps.lock(lock):
                    self.fail('FIFO accepted')

    def fixtures(self, tmp):
        source = Path(tmp)/'source'
        source.mkdir()
        wrapper = source/'vendor/adevtool/scripts/run-build.sh'
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text('#!/bin/bash\nexit 0\n')
        wrapper.chmod(0o755)
        attempt = Path(tmp)/'attempt'
        attempt.mkdir()
        return source, attempt

    def emit_outputs(self, source, spec):
        output = source/spec['outDir']
        bindir = output/'host/linux-x86/bin'
        bindir.mkdir(parents=True)
        for name in spec['tools']:
            path = bindir/name
            path.write_text('tool\n')
            path.chmod(0o755)
        supporting = output/'host/linux-x86/framework/support.jar'
        supporting.parent.mkdir()
        supporting.write_bytes(b'jar')
        return output

    def test_success_records_supporting_files_and_marks_only_after_make_and_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, attempt = self.fixtures(tmp)
            spec = deps.recipe()
            def build(*_args):
                self.assertFalse((source/spec['outDir']/'adevtool_revision').exists())
                self.emit_outputs(source, spec)
                return 0
            with patch.object(deps, 'source_check', return_value={'projectCount': 1057}), \
                    patch.object(deps, 'checked', return_value=spec['adevtoolRevision']), \
                    patch.object(deps, 'sample', return_value=123), patch.object(deps, 'supervise', side_effect=build):
                result = deps.build(source, attempt, spec, {}, identity={'run': 'fixture'})
            self.assertTrue(result['success'])
            self.assertEqual(result['identity'], {'run': 'fixture'})
            self.assertIn('host/linux-x86/framework/support.jar', result['distribution'])
            self.assertFalse(result['otaAuthorized'])
            self.assertFalse(result['authenticatedStop'])
            self.assertEqual((source/spec['outDir']/'adevtool_revision').read_text(), spec['adevtoolRevision']+'\n')
            self.assertEqual(json.loads((attempt/'hostdeps-receipt.json').read_text()), result)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                deps.build(source, attempt, spec, {})

    def test_revision_marker_does_not_extend_an_expired_stage_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, attempt = self.fixtures(tmp)
            output = self.emit_outputs(source, deps.recipe())
            with self.assertRaisesRegex(ValueError, 'total timeout'):
                deps.mark_revision(source, output, attempt, deps.recipe()['adevtoolRevision'], time.monotonic()-1)
            self.assertFalse((output/'adevtool_revision').exists())

    def test_failure_retains_output_writes_failed_receipt_and_no_revision_marker(self):
        for failure in ('make', 'missing-tool', 'inventory-link'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                source, attempt = self.fixtures(tmp)
                spec = deps.recipe()
                def build(*_args):
                    output = self.emit_outputs(source, spec)
                    if failure == 'make':
                        raise ValueError('hostdeps Make failed')
                    if failure == 'missing-tool':
                        (output/'host/linux-x86/bin/aapt2').unlink()
                    if failure == 'inventory-link':
                        (output/'host/linux-x86/outside').symlink_to('/etc/passwd')
                with patch.object(deps, 'source_check', return_value={'projectCount': 1057}), \
                        patch.object(deps, 'checked', return_value=spec['adevtoolRevision']), \
                        patch.object(deps, 'sample', return_value=123), patch.object(deps, 'supervise', side_effect=build):
                    with self.assertRaises(ValueError):
                        deps.build(source, attempt, spec, {})
                result = json.loads((attempt/'hostdeps-receipt.json').read_text())
                self.assertFalse(result['success'])
                self.assertFalse((source/spec['outDir']/'adevtool_revision').exists())
                self.assertTrue((source/spec['outDir']).exists())

    def test_source_mismatch_never_starts_make(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, attempt = self.fixtures(tmp)
            with patch.object(deps, 'source_check', side_effect=ValueError('pinned project HEAD or worktree differs')), \
                    patch.object(deps, 'sample', return_value=123), \
                    patch.object(deps, 'supervise', side_effect=AssertionError('Make started')):
                with self.assertRaisesRegex(ValueError, 'worktree'):
                    deps.build(source, attempt, deps.recipe(), {})
            self.assertFalse(json.loads((attempt/'hostdeps-receipt.json').read_text())['success'])

    def test_real_project_check_rejects_dirty_wrong_revision_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root/'project'
            subprocess.run(['git', 'init', '-q', str(project)], check=True)
            file = project/'a'
            file.write_text('clean')
            git = ['git', '-C', str(project)]
            subprocess.run(git+['add', 'a'], check=True)
            subprocess.run(git+['-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-qm', 'init'], check=True)
            revision = subprocess.check_output(git+['rev-parse', 'HEAD'], text=True).strip()
            with patch.object(deps.sync, 'project_identities', return_value=[('p', 'project', 'r', revision)]):
                self.assertEqual(deps.source_check(root, deps.child_env(root, root), time.monotonic()+20)['projectCount'], 1)
                file.write_text('dirty')
                with self.assertRaisesRegex(ValueError, 'worktree'):
                    deps.source_check(root, deps.child_env(root, root), time.monotonic()+20)
                subprocess.run(git+['checkout', '--', 'a'], check=True)
                (project/'untracked').write_text('new')
                with self.assertRaisesRegex(ValueError, 'worktree'):
                    deps.source_check(root, deps.child_env(root, root), time.monotonic()+20)
                (project/'untracked').unlink()
                project.rename(root/'real')
                project.symlink_to(root/'real')
                with self.assertRaises(deps.readers._Refused):
                    deps.source_check(root, deps.child_env(root, root), time.monotonic()+20)
            with patch.object(deps.sync, 'project_identities', return_value=[('p', 'real', 'r', '0'*40)]):
                with self.assertRaisesRegex(ValueError, 'worktree'):
                    deps.source_check(root, deps.child_env(root, root), time.monotonic()+20)

    def test_inventory_rejects_linked_entrypoint_fifo_and_output_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, attempt = self.fixtures(tmp)
            output = self.emit_outputs(source, deps.recipe())
            binary = output/'host/linux-x86/bin/aapt2'
            binary.unlink()
            os.mkfifo(binary)
            with self.assertRaisesRegex(ValueError, 'special'):
                deps.inventory(source, output, time.monotonic()+20)
            binary.unlink()
            binary.symlink_to('/etc/passwd')
            with self.assertRaisesRegex(ValueError, 'escapes'):
                deps.inventory(source, output, time.monotonic()+20)

    def test_live_sampler_refuses_low_space_exhausted_inodes_and_log_growth(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, attempt = self.fixtures(tmp)
            output = source/'out_adevtool_deps'
            log = attempt/'log'
            ample = SimpleNamespace(f_bavail=1 << 40, f_frsize=4096, f_favail=100)
            for available in (SimpleNamespace(f_bavail=1, f_frsize=4096, f_favail=100),
                              SimpleNamespace(f_bavail=1 << 40, f_frsize=4096, f_favail=0)):
                with patch.object(deps.os, 'statvfs', return_value=available):
                    with self.assertRaisesRegex(ValueError, 'space or inode'):
                        deps.sample(source, output, attempt, log)
            log.write_text('0123456789')
            with patch.object(deps.os, 'statvfs', return_value=ample), patch.object(deps, 'LOG_BYTES', 4):
                with self.assertRaisesRegex(ValueError, 'log bound'):
                    deps.sample(source, output, attempt, log)

    def test_live_sampler_tolerates_a_file_unlinked_between_readdir_and_stat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'stable').write_bytes(b'1234')
            (root/'transient').write_bytes(b'56789')
            original_stat = os.stat
            removed = []
            def stat_during_build(path, *args, **kwargs):
                if path == 'transient' and kwargs.get('dir_fd') is not None and not removed:
                    os.unlink(path, dir_fd=kwargs['dir_fd'])
                    removed.append(True)
                return original_stat(path, *args, **kwargs)
            with patch.object(deps.os, 'stat', side_effect=stat_during_build):
                self.assertEqual(deps.tree_bytes(root), 4)
            self.assertEqual(removed, [True])
            (root/'outside-link').symlink_to('/etc')
            self.assertEqual(deps.tree_bytes(root), 4+len('/etc'))

    def test_space_observations_record_actual_free_space_and_watched_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, attempt = self.fixtures(tmp)
            output = source/'out_adevtool_deps'
            log = attempt/'log'
            observations = {}
            initial = SimpleNamespace(f_bavail=1000, f_frsize=4096, f_favail=100)
            later = SimpleNamespace(f_bavail=990, f_frsize=4096, f_favail=97)
            with patch.object(deps, 'RESERVE_BYTES', 1), patch.object(deps.os, 'statvfs', side_effect=[initial, later]):
                deps.sample(source, output, attempt, log, observations=observations)
                log.write_bytes(b'1234')
                deps.sample(source, output, attempt, log, observations=observations)
            self.assertEqual(observations['samples'], 2)
            self.assertEqual(observations['filesystemAvailableDropBytes'], 10*4096)
            self.assertEqual(observations['minimumAvailableInodes'], 97)
            self.assertEqual(observations['peakWatchedBytes'], 4)

    def test_inventory_refuses_an_unreadable_supporting_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, _attempt = self.fixtures(tmp)
            output = self.emit_outputs(source, deps.recipe())
            blocked = output/'host/linux-x86/framework'
            original_scandir = os.scandir
            def denied(path):
                if os.fspath(path) == str(blocked):
                    raise PermissionError('fixture unreadable supporting directory')
                return original_scandir(path)
            with patch.object(deps.os, 'scandir', side_effect=denied):
                with self.assertRaisesRegex(ValueError, 'inventory is incomplete'):
                    deps.inventory(source, output, time.monotonic()+20)

    def test_report_rejects_foreign_identity_and_never_copies_raw_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            attempt = storage/'mustang-hostdeps-123-1'
            attempt.mkdir()
            (attempt/'build.log').write_text('raw-log-not-for-upload')
            lane, run = deps.guard(self.env(), {'inputs': {'hostdeps_only': True}})
            cfg = {'dataFilesystemUuid': 'fixture-uuid'}
            paths = {'storageRoot': storage}
            raw = attempt/'hostdeps-receipt.json'
            raw.write_text(json.dumps({'schema': 'roseos.mustang-hostdeps-run.v1', 'identity': {'foreign': True}}))
            target = storage/'report.json'
            with patch.object(deps, 'guest_context', return_value=(lane, run, cfg, paths)):
                with self.assertRaisesRegex(ValueError, 'another run'):
                    deps.report(self.env(), target)
                self.assertFalse(target.exists())
                raw.write_text(json.dumps({'schema': 'roseos.mustang-hostdeps-run.v1', 'identity': {
                    'run': run, 'builder': lane['vm'], 'diskUuid': cfg['dataFilesystemUuid']}, 'success': False}))
                deps.report(self.env(), target)
                self.assertNotIn('raw-log-not-for-upload', target.read_text())
    def test_supervisor_kills_real_process_group_on_timeout_and_monitor_failure(self):
        for failure in ('timeout', 'monitor'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                code = 'import time;time.sleep(30)'
                calls = []
                processes = []
                real_popen = subprocess.Popen
                def spawn(*args, **kwargs):
                    proc = real_popen(*args, **kwargs)
                    processes.append(proc)
                    return proc
                def monitor():
                    calls.append(1)
                    if failure == 'monitor' and len(calls) > 2:
                        raise ValueError('hostdeps space or inode bound exceeded')
                with patch.object(deps, 'SAMPLE_SECONDS', 0.01), patch.object(deps.subprocess, 'Popen', side_effect=spawn):
                    with self.assertRaises(ValueError):
                        deps.supervise([sys.executable, '-c', code], root, os.environ.copy(), root/'log',
                                       time.monotonic()+(0.25 if failure == 'timeout' else 10), monitor)
                self.assertEqual(len(processes), 1)
                for proc in processes:
                    with self.assertRaises(ProcessLookupError):
                        os.kill(proc.pid, 0)

    def test_workflow_wires_exclusive_guest_job_into_queue_watch_and_cleanup(self):
        jobs = workflow_shape.jobs((ROOT/'.github/workflows/mustang-ota-dedicated.yml').read_text())
        host = jobs['hostdeps']
        self.assertNotIn('id-token:', host)
        self.assertNotIn('actions-gcp-login', host)
        self.assertNotIn('sudo', host)
        self.assertIn('timeout-minutes: 165', host)
        self.assertIn('timeout-minutes: 155', host)
        self.assertIn('mustang_hostdeps.py run --execute', host)
        self.assertIn('mustang_hostdeps.py report', host)
        self.assertNotIn('build.log', host)
        self.assertIn('- hostdeps', jobs['cleanup'].split('steps:', 1)[0])
        self.assertIn('inputs.hostdeps_only == false', jobs['builder'])
        self.assertIn('inputs.hostdeps_only == false', jobs['signer'])
        self.assertEqual(ephemeral.queue_decision([{'name': 'hostdeps', 'status': 'in_progress'}], 800), 'ready')
        self.assertEqual(ephemeral.queue_decision([{'name': 'hostdeps', 'status': 'queued'},
                            {'name': 'builder', 'status': 'completed', 'conclusion': 'skipped'}], 800), 'stop')

    def test_workflow_trust_allows_only_boolean_scopes_and_never_shell_interpolation(self):
        workflow = (ROOT/'.github/workflows/mustang-ota-dedicated.yml').read_text()
        self.assertEqual(mustang_route.workflow_trust(workflow)['problems'], [])
        for expression in ('inputs.command', 'inputs.dry_run || inputs.command', 'github.event.issue.title'):
            poisoned = workflow+'\n  BAD: ${{ '+expression+' }}\n'
            self.assertIn('untrusted expression reaches the workflow', mustang_route.workflow_trust(poisoned)['problems'])
        poisoned = workflow+'\n      run: echo ${{ inputs.hostdeps_only }}\n'
        self.assertIn('workflow input is interpolated into a shell script', mustang_route.workflow_trust(poisoned)['problems'])


if __name__ == '__main__':
    unittest.main()
