"""Offline checks for one-job Mustang enrollment. No token mint, metadata write, or VM call."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts')]
import mustang_controller as controller
import mustang_ephemeral as enroll
import mustang_lane as lane

spec = importlib.util.spec_from_file_location('mustang_dedicated_ephemeral', ROOT/'scripts/mustang-dedicated.py')
dedicated = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dedicated)


class EnrollmentTest(unittest.TestCase):
    def test_jit_request_is_only_group_5_for_this_run_and_outside_source(self):
        body = enroll.jit_body('41884368820')
        self.assertEqual(body, {'name': 'mustang-41884368820', 'runner_group_id': 5,
                                 'labels': ['roseos-mustang-builder'], 'work_folder': '_work'})
        self.assertFalse(str(enroll.SOURCE_ROOT).startswith(str(enroll.paths()['work'])))
        for group in (3, 4, 1):
            with self.assertRaisesRegex(ValueError, 'group 5'):
                enroll.jit_body('12', group)
        with self.assertRaises(ValueError):
            enroll.jit_body('0')

    def test_base64_jit_payload_never_reaches_the_summary_or_stderr(self):
        import base64
        raw = b'jit-secret-not-for-logs'
        secret = base64.b64encode(raw).decode()
        body = {'encoded_jit_config': secret, 'runner': {'name': 'mustang-12', 'runner_group_id': 4, 'ephemeral': True}}
        try:
            enroll.accept_jit(body, '12')
        except ValueError as error:
            message = str(error)
        else:
            self.fail('wrong group was accepted')
        self.assertFalse(enroll.contains_secret(message, secret))
        self.assertNotIn(raw.decode(), message)
        self.assertTrue(enroll.contains_secret('runner started with '+raw.decode(), secret))
        self.assertTrue(enroll.contains_secret('config '+secret, secret))
        self.assertNotIn(secret, enroll.public_error(ValueError('refused '+secret)))
        self.assertNotIn(raw.decode(), enroll.public_error(ValueError('refused '+raw.decode())))
        plan = __import__('subprocess').run([sys.executable, str(ROOT/'scripts/mustang_ephemeral.py'), 'plan', '--run-id', '12'],
                                             capture_output=True, text=True)
        self.assertFalse(enroll.contains_secret(plan.stdout + plan.stderr, secret))

    def test_response_must_be_ephemeral_and_the_summary_omits_the_config(self):
        secret = 'A'*40
        body = {'encoded_jit_config': secret, 'runner': {'name': 'mustang-12', 'runner_group_id': 5, 'ephemeral': True}}
        summary = enroll.accept_jit(body, '12')
        self.assertNotIn(secret, json.dumps(summary))
        self.assertTrue(summary['ephemeral'])
        body['runner']['ephemeral'] = False
        with self.assertRaisesRegex(ValueError, 'not an ephemeral'):
            enroll.accept_jit(body, '12')
        body['runner'].update(ephemeral=True, runner_group_id=4)
        with self.assertRaisesRegex(ValueError, 'not an ephemeral'):
            enroll.accept_jit(body, '12')

    def test_issue_writes_a_private_file_and_does_not_return_the_config(self):
        secret = 'B'*48
        def post(path, body, token):
            self.assertEqual(path, enroll.API)
            self.assertEqual(body['runner_group_id'], 5)
            self.assertEqual(token, 'x'*24)
            return {'encoded_jit_config': secret, 'runner': {'name': 'mustang-9', 'runner_group_id': 5, 'ephemeral': True}}
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'jit'
            summary = enroll.issue('9', 'x'*24, post, out)
            self.assertNotIn(secret, json.dumps(summary))
            self.assertEqual(out.read_text(), secret)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)

    def test_metadata_and_cancel_stay_on_this_vm_and_run(self):
        add = enroll.metadata_argv('add-metadata')
        self.assertIn('roseos-mustang-builder-20261107-a', add)
        self.assertIn('--metadata-from-file=mustang-jitconfig', add)
        secret = 'encoded-jit-config-secret-value-not-for-logs'
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary)/'mustang-jit'
            private.write_text(secret)
            private.chmod(0o600)
            seen = []
            def transport(argv):
                seen.append(list(argv))
                return subprocess.CompletedProcess(argv, 0, '', '')
            code = enroll.main(['put-metadata', '--execute', '--file', str(private)], transport=transport)
            self.assertEqual(code, 0)
            self.assertEqual(seen[0][-1], '--metadata-from-file=mustang-jitconfig='+str(private))
            self.assertNotIn(secret, ' '.join(seen[0]))
            refused = enroll.main(['issue', '--execute', '--run-id', '12', '--out', str(private), '--file', str(private)])
            self.assertEqual(refused, 4)
        missing = subprocess.run([sys.executable, str(ROOT/'scripts/mustang_ephemeral.py'),
                                  'put-metadata', '--execute', '--file', '/tmp/mustang-jit-missing'],
                                 capture_output=True, text=True)
        self.assertEqual(missing.returncode, 4)
        self.assertNotIn('unrecognized arguments', missing.stderr)
        self.assertIn('private regular file', missing.stderr)
        self.assertNotIn('immortal-os-builder', add)
        self.assertIn('--keys=mustang-jitconfig', enroll.metadata_argv('remove-metadata'))
        with self.assertRaises(ValueError):
            enroll.metadata_argv('add-metadata', 'immortal-os-builder')
        self.assertTrue(enroll.cancel_path('44', '44').endswith('/actions/runs/44/cancel'))
        with self.assertRaisesRegex(ValueError, 'different workflow run'):
            enroll.cancel_path('44', '45')

    def test_missing_runner_stops_after_the_bound_and_an_accepted_job_does_not(self):
        queued = [{'name': 'builder', 'status': 'queued'}]
        self.assertEqual(enroll.queue_decision(queued, 30), 'wait')
        self.assertEqual(enroll.queue_decision(queued, enroll.QUEUE_LIMIT_SECONDS), 'stop')
        self.assertEqual(enroll.queue_decision([{'name': 'builder', 'status': 'in_progress'}], enroll.QUEUE_LIMIT_SECONDS), 'ready')
        self.assertEqual(enroll.queue_decision([{'name': 'builder', 'status': 'skipped'},
                                                {'name': 'source', 'status': 'in_progress'}], enroll.QUEUE_LIMIT_SECONDS), 'ready')
        self.assertEqual(enroll.watch_decision('failure', [{'name': 'builder', 'status': 'in_progress'}], 0), 'stop')
        self.assertNotIn('value', ' '.join(enroll.metadata_keys_argv()))
        clock = {'now': 0}
        def fetch():
            return queued if clock['now'] < enroll.QUEUE_LIMIT_SECONDS else [{'name': 'builder', 'status': 'in_progress'}]
        def sleep(seconds):
            clock['now'] += seconds
        self.assertEqual(enroll.watch(fetch, sleep, lambda: clock['now']), 'ready')
        clock['now'] = enroll.QUEUE_LIMIT_SECONDS
        self.assertEqual(enroll.watch(lambda: queued, sleep, lambda: clock['now']), 'stop')

    def test_infrastructure_host_check_starts_without_fabricated_inputs(self):
        runtime = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
        self.assertEqual(runtime['status'], 'unbound')
        self.assertIsNone(runtime['appInputsRecord'])
        self.assertIsNone(runtime['signingRouteEvidence'])
        self.assertIsNone(runtime['sourceEvidence'])
        self.assertIsNone(runtime['baselineTargetFiles'])
        self.assertEqual(controller.start_gate(runtime, {'MUSTANG_DRY_RUN': 'true'}), 'infrastructure-host-check')
        with self.assertRaisesRegex(ValueError, 'not admitted'):
            controller.start_gate(runtime, {})
        with self.assertRaisesRegex(ValueError, 'cannot publish'):
            controller.start_gate(runtime, {'MUSTANG_DRY_RUN': 'true', 'MUSTANG_PUBLISH': 'true'})
        for action in dedicated.DRY_RUN_BLOCKED:
            with patch.dict(os.environ, {'MUSTANG_DRY_RUN': 'true'}):
                with self.assertRaisesRegex(ValueError, 'cannot compile, sign, or publish'):
                    dedicated.refuse_dry_run(action)
        with patch.dict(os.environ, {'MUSTANG_DRY_RUN': 'true'}):
            dedicated.refuse_dry_run('host-check')
        env = dict(PATH=os.environ['PATH'], MUSTANG_DRY_RUN='true')
        started = subprocess.run([sys.executable, str(ROOT/'scripts/mustang_controller.py'), 'start',
                                  '--ownership-record', '/tmp/mustang-owner-absent.json'],
                                 env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(started.returncode, 4, started.stderr)
        self.assertIn('exact main workflow', started.stderr)
        self.assertNotIn('gcloud', started.stderr)
        published = subprocess.run([sys.executable, str(ROOT/'scripts/mustang_controller.py'), 'start'],
                                   env=dict(env, MUSTANG_PUBLISH='true'), capture_output=True, text=True, timeout=20)
        self.assertEqual(published.returncode, 4)
        self.assertIn('cannot publish', published.stderr)
        self.assertNotIn('gcloud', published.stderr)
        workflow = (ROOT/'.github/workflows/mustang-ota-dedicated.yml').read_text()
        builder = workflow.split('builder:', 1)[1].split('publisher:', 1)[0]
        self.assertIn('if: inputs.dry_run == false\n      run: python3 scripts/mustang-dedicated.py baseline', builder)
        self.assertIn('if: inputs.dry_run == false\n      env:\n        APP_INPUTS: ${{ steps.apps.outputs.inputs_dir }}\n      run: python3 scripts/mustang-dedicated.py produce', builder)
        self.assertIn("if: always() && inputs.dry_run == false\n      run: python3 scripts/mustang-dedicated.py report", builder)
        self.assertNotIn('mustang-dedicated.py baseline', builder.split('if: inputs.dry_run == false', 1)[0])

    def test_dry_run_host_check_uses_pinned_tools_not_null_runtime_records(self):
        cfg = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
        bound = lane.configuration()
        seen = []

        def is_file(path):
            seen.append(str(path))
            return True

        space = type('Space', (), {'f_bavail': 10**9, 'f_frsize': 4096, 'f_bsize': 4096,
                                    'f_files': lane.CONFIRMED_TOTAL_INODES, 'f_favail': 1000})()

        def execute(argv, **kwargs):
            self.assertEqual(argv[0], 'findmnt')
            return type('Result', (), {'returncode': 0, 'stdout': lane.CONFIRMED_UUID+' '+lane.CONFIRMED_MOUNT+'\n'})()

        with patch.dict(os.environ, {'MUSTANG_DRY_RUN': 'true'}), \
                patch.object(Path, 'is_file', is_file), patch.object(Path, 'is_dir', lambda path: True), \
                patch.object(Path, 'is_symlink', lambda path: False), \
                patch.object(dedicated.os, 'statvfs', return_value=space), \
                patch.object(dedicated.os, 'stat', return_value=type('Stat', (), {'st_dev': 1})()), \
                patch.object(dedicated.os, 'access', return_value=True), \
                patch.object(dedicated.os, 'geteuid', return_value=1000), \
                patch.object(dedicated.shutil, 'which', side_effect=AssertionError('signing CLI is not an infrastructure check')):
            dedicated.host_check(bound, cfg, observe_id=lambda: bound['vm']['instanceId'], execute=execute)
        self.assertIn(enroll.NODE['install'], seen)
        self.assertIn(enroll.SDK['aapt'], seen)
        self.assertIn(enroll.SDK['apksigner'], seen)
        self.assertNotIn(None, seen)

    def test_dry_run_uses_group_5_without_admitting_inputs_or_publishing(self):
        env = dict(GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main', GITHUB_SHA='a'*40,
                   GITHUB_REPOSITORY_ID='1428996648', GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1',
                   GITHUB_WORKFLOW_REF='Immortal-Protocols/RoseOS-1/.github/workflows/mustang-ota-dedicated.yml@refs/heads/main')
        cfg = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
        bound = lane.configuration()
        self.assertIsNone(cfg['runner']['lifetime'])
        self.assertIsNone(cfg['appInputsRecord'])
        got = dedicated.guard(bound, cfg, env, dry_run=True)
        self.assertIsNone(got['baselineTargetFiles'])
        with self.assertRaisesRegex(ValueError, 'cannot publish'):
            dedicated.guard(bound, cfg, env, publish=True, dry_run=True)
        forged = dict(cfg, runner=dict(cfg['runner'], lifetime='persistent'))
        with self.assertRaisesRegex(ValueError, 'persistent'):
            dedicated.guard(bound, forged, env, dry_run=True)
        other = dict(cfg, runner=dict(cfg['runner'], groupId='4'))
        with self.assertRaisesRegex(ValueError, 'group 5'):
            dedicated.guard(bound, other, env, dry_run=True)

    def test_pinned_tools_and_guest_script_do_not_register_a_persistent_runner(self):
        plan = json.loads(__import__('subprocess').check_output(
            [sys.executable, str(ROOT/'scripts/mustang_ephemeral.py'), 'plan', '--run-id', '12'], text=True))
        self.assertEqual(plan['tools']['node']['sha256'], enroll.NODE['sha256'])
        self.assertEqual(plan['tools']['sdk']['sha256'], 'bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88')
        self.assertEqual(plan['tools']['runner']['sha256'], enroll.RUNNER_APP['sha256'])
        self.assertNotIn('encoded_jit_config', plan)
        script = (ROOT/'scripts/mustang-ephemeral-guest.sh').read_text()
        self.assertIn('./run.sh --jitconfig', script)
        self.assertNotIn('exec ./run.sh', script)
        self.assertIn('trap cleanup EXIT', script)
        self.assertIn('--output "$file"', script)
        self.assertIn('guest-attributes/mustang/jit-fetched', script)
        self.assertNotIn('config=$(curl', script)
        self.assertNotIn('config.sh', script)
        self.assertNotIn('--token', script)
        workflow = (ROOT/'.github/workflows/mustang-ota-dedicated.yml').read_text()
        watch = workflow.split('queue-watch:', 1)[1].split('builder:', 1)[0]
        self.assertIn("needs.controller.result == 'success'", watch)
        self.assertNotIn("needs.enroll.result == 'success'", watch)
        self.assertIn('mustang_ephemeral.py cancel --execute', watch)
        self.assertNotIn('gh api --method POST', workflow)
        enroll_job = workflow.split('enroll:', 1)[1].split('queue-watch:', 1)[0]
        self.assertIn('clear-metadata --execute', enroll_job)
        self.assertNotIn('permission-contents:', enroll_job)
        self.assertEqual(enroll.APP_PERMISSION['access'], 'write')
        self.assertIn('generate-jitconfig', enroll.APP_PERMISSION['endpoint'])
        env = dict(os.environ, MUSTANG_EPHEMERAL_CHECK='1')
        self.assertEqual(__import__('subprocess').run(['bash', str(ROOT/'scripts/mustang-ephemeral-guest.sh')], env=env).returncode, 0)
        unit = (ROOT/'os/build-only/mustang-ephemeral-runner.service').read_text()
        self.assertIn('User=mustang-runner', unit)
        self.assertIn('Type=simple', unit)
        self.assertIn('RuntimeMaxSec=14400', unit)
        self.assertNotIn('jitconfig', unit.lower())
        self.bootstrap_refuses_wrong_disk_and_keeps_a_verified_download()

    def bootstrap_refuses_wrong_disk_and_keeps_a_verified_download(self):
        import hashlib
        import subprocess
        script = ROOT/'scripts/mustang-guest-bootstrap.sh'
        text = script.read_text()
        self.assertLess(text.index('instance id differs'), text.index('sudo tee'))
        self.assertNotIn('rm -rf', text)
        self.assertNotIn('"$home/source"', text.split('tar ', 1)[-1])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bindir = root/'bin'
            bindir.mkdir()
            log = root/'sudo.log'
            (bindir/'curl').write_text('#!/bin/sh\necho wrong-instance\n')
            (bindir/'sudo').write_text('#!/bin/sh\necho "$@" >>"$MUSTANG_SUDO_LOG"\nexit 99\n')
            for path in (bindir/'curl', bindir/'sudo'):
                path.chmod(0o755)
            env = dict(os.environ, PATH=str(bindir)+os.pathsep+os.environ['PATH'], MUSTANG_SUDO_LOG=str(log))
            refused = subprocess.run(['bash', str(script)], env=env, capture_output=True, text=True)
            self.assertEqual(refused.returncode, 4, refused.stderr)
            self.assertFalse(log.exists())
            payload = b'official-bytes'
            digest = hashlib.sha256(payload).hexdigest()
            archive = root/'node.tar.xz'
            archive.write_bytes(payload)
            (bindir/'curl').write_text('#!/bin/sh\necho curl-called >>"$MUSTANG_CURL_LOG"\nexit 99\n')
            marker = root/'curl.log'
            checked = subprocess.run(['bash', str(script), '--check-fetch', 'https://example.invalid/node', digest, str(archive)],
                                     env=dict(env, MUSTANG_BOOTSTRAP_DIRECT='1', MUSTANG_CURL_LOG=str(marker)),
                                     capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertFalse(marker.exists())
            self.assertEqual(archive.read_bytes(), payload)
        self.assertNotIn('test -x "$tools', text)
        self.assertIn('sudo -H -u "$user" -- /usr/bin/env --chdir "$runner_home"', text)
        self.assertIn('-m 0750', text)

    def test_health_check_uses_the_runner_when_parents_are_not_searchable(self):
        import subprocess
        script = ROOT/'scripts/mustang-guest-bootstrap.sh'
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)/'roseos'
            binaries = {
                home/'tools/node/node-v24.22.2-linux-x64/bin/node': 'echo v24.22.2\n',
                home/'tools/android-sdk/build-tools/android-15/aapt': 'echo "Android Asset Packaging Tool, v0.2-11948202"\n',
                home/'tools/android-sdk/build-tools/android-15/apksigner': 'echo 0.9\n',
                home/'runner/bin/Runner.Listener': 'mkdir -p "$(dirname "$0")/../_diag" && echo 2.338.2\n',
                home/'runner/run.sh': 'echo ok\n',
            }
            for path, body in binaries.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('#!/bin/sh\n'+body)
                path.chmod(0o755)
            for directory in (home, home/'tools', home/'tools/node', home/'tools/android-sdk',
                              home/'tools/android-sdk/build-tools', home/'tools/android-sdk/build-tools/android-15',
                              home/'runner', home/'runner/bin'):
                directory.chmod(0o750)
            node = home/'tools/node/node-v24.22.2-linux-x64/bin/node'
            blocked = subprocess.run(['sudo', '-n', '-u', 'nobody', '--', 'test', '-x', str(node)],
                                     capture_output=True, text=True)
            self.assertNotEqual(blocked.returncode, 0)
            env = dict(os.environ, MUSTANG_BOOTSTRAP_HOME=str(home), MUSTANG_BOOTSTRAP_USER='ubuntu')
            passed = subprocess.run(['bash', str(script), '--check-health'], env=env, capture_output=True, text=True)
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertTrue((home/'runner/_diag').is_dir())
            denied = subprocess.run(['bash', str(script), '--check-health'],
                                    env=dict(env, MUSTANG_BOOTSTRAP_USER='nobody'), capture_output=True, text=True)
            self.assertEqual(denied.returncode, 4, denied.stderr)
            self.assertIn('not executable by nobody', denied.stderr)
            node.write_text('#!/bin/sh\necho v0.0.0\n')
            node.chmod(0o755)
            wrong = subprocess.run(['bash', str(script), '--check-health'], env=env, capture_output=True, text=True)
            self.assertEqual(wrong.returncode, 4, wrong.stderr)
            self.assertIn("node version is 'v0.0.0'", wrong.stderr)
            self.assertIn('expected v24.22.2', wrong.stderr)


if __name__ == '__main__':
    unittest.main()
