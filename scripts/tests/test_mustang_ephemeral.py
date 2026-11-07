"""Offline checks for one-job Mustang enrollment. No token mint, metadata write, or VM call."""
import hashlib
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

    def runner(self, run='12', **extra):
        value = {'id': 1144, 'name': 'mustang-'+run, 'runner_group_id': 5, 'os': 'linux',
                 'status': 'offline', 'busy': False, 'labels': ['roseos-mustang-builder']}
        value.update(extra)
        return value

    def test_response_must_be_ephemeral_and_the_summary_omits_the_config(self):
        secret = 'A'*40
        body = {'encoded_jit_config': secret, 'runner': self.runner(ephemeral=True)}
        summary = enroll.accept_jit(body, '12')
        self.assertNotIn(secret, json.dumps(summary))
        self.assertTrue(summary['ephemeral'])
        self.assertEqual(summary['oneJobProof'], 'POST '+enroll.API)
        body['runner']['ephemeral'] = False
        refused = enroll.accept_jit(body, '12')
        self.assertIs(refused['ephemeral'], False)
        self.assertFalse(refused['oneJob'])
        body['runner'].update(ephemeral=True, runner_group_id=4)
        with self.assertRaisesRegex(ValueError, 'not an ephemeral'):
            enroll.accept_jit(body, '12')
        bare = self.runner()
        del bare['runner_group_id']
        omitted = {'encoded_jit_config': secret, 'runner': bare}
        self.assertNotIn('ephemeral', omitted['runner'])
        self.assertNotIn('runner_group_id', omitted['runner'])
        accepted = enroll.accept_jit(omitted, '12')
        self.assertIsNone(accepted['ephemeral'])
        self.assertIsNone(accepted['responseGroupId'])
        self.assertNotIn('groupId', accepted)
        self.assertTrue(accepted['oneJob'])
        self.assertNotEqual(accepted['ephemeral'], True)
        nulled = {'encoded_jit_config': secret, 'runner': self.runner(ephemeral=None)}
        self.assertIsNone(enroll.accept_jit(nulled, '12')['ephemeral'])
        with self.assertRaisesRegex(ValueError, 'registration token'):
            enroll.accept_jit({'token': secret, 'expires_at': '2026-11-07T00:00:00Z'}, '12')
        readback = {'busy': False, 'ephemeral': None, 'id': 1144, 'labels': ['roseos-mustang-builder'],
                    'name': 'mustang-42387816754', 'status': 'offline'}
        checked = enroll.confirm_readback(readback, '42387816754', 1144)
        self.assertIsNone(checked['ephemeral'])
        self.assertTrue(checked['omitted'])
        readback['ephemeral'] = False
        with self.assertRaisesRegex(ValueError, 'not an ephemeral'):
            enroll.confirm_readback(readback, '42387816754', 1144)

    def test_issue_writes_a_private_file_and_does_not_return_the_config(self):
        secret = 'B'*48
        minted = self.runner('9')
        del minted['runner_group_id']
        def post(path, body, token):
            self.assertEqual(path, enroll.API)
            self.assertEqual(body['runner_group_id'], 5)
            self.assertEqual(token, 'x'*24)
            return {'encoded_jit_config': secret, 'runner': minted}
        def group(page, token):
            self.assertEqual(page, 1)
            return {'total_count': 1, 'runners': [dict(minted)]}
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'jit'
            summary = enroll.issue('9', 'x'*24, post, out, group)
            self.assertNotIn(secret, json.dumps(summary))
            self.assertIsNone(summary['responseGroupId'])
            self.assertEqual(summary['groupId'], 5)
            self.assertEqual(summary['groupProof'], 'GET '+enroll.GROUP_RUNNERS)
            self.assertEqual(out.read_text(), secret)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)

    def pages(self, runners):
        def group(page, token):
            self.assertEqual(page, 1)
            return {'total_count': len(runners), 'runners': list(runners)}
        return group

    def test_group_membership_is_the_list_and_a_failed_mint_deletes_only_after_that_proof(self):
        secret = 'C'*40
        deleted = []
        bare = self.runner(ephemeral=False)
        member = dict(bare)
        def post(path, body, token):
            return {'encoded_jit_config': secret, 'runner': bare}
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'jit'
            with self.assertRaisesRegex(ValueError, 'not an ephemeral'):
                enroll.issue('12', 'x'*24, post, out, self.pages([member]), lambda path, token: deleted.append(path))
            self.assertFalse(out.exists())
        self.assertEqual(deleted, ['/orgs/Immortal-Protocols/actions/runners/1144'])
        deleted.clear()
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'jit'
            with self.assertRaisesRegex(ValueError, 'not a member'):
                enroll.issue('12', 'x'*24, post, out, self.pages([]), lambda path, token: deleted.append(path))
            self.assertFalse(out.exists())
        self.assertEqual(deleted, [])
        def other_group(path, body, token):
            return {'encoded_jit_config': secret, 'runner': self.runner(ephemeral=False, runner_group_id=4)}
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'jit'
            with self.assertRaises(ValueError):
                enroll.issue('12', 'x'*24, other_group, out, self.pages([member]), lambda path, token: deleted.append(path))
            self.assertFalse(out.exists())
        self.assertEqual(deleted, [])
        missing = self.runner('42387816754')
        del missing['runner_group_id']
        missing.pop('ephemeral', None)
        def mint(path, body, token):
            return {'encoded_jit_config': secret, 'runner': missing}
        listed = {'busy': False, 'ephemeral': None, 'id': 1144, 'labels': ['roseos-mustang-builder'],
                  'name': 'mustang-42387816754', 'status': 'offline'}
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'jit'
            summary = enroll.issue('42387816754', 'x'*24, mint, out, self.pages([listed]),
                                   lambda path, token: deleted.append(path))
            self.assertIsNone(summary['ephemeral'])
            self.assertIsNone(summary['responseGroupId'])
            self.assertEqual(summary['groupId'], 5)
            self.assertTrue(summary['readbackEphemeralOmitted'])
            self.assertTrue(summary['oneJob'])
            self.assertEqual(out.read_text(), secret)
        self.assertEqual(deleted, [])
        def short(page, token):
            if page == 1:
                return {'total_count': 2, 'runners': [listed]}
            return {'total_count': 2, 'runners': []}
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'jit'
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                enroll.issue('42387816754', 'x'*24, mint, out, short, lambda path, token: deleted.append(path))
            self.assertFalse(out.exists())
        self.assertEqual(deleted, [])

    def test_sweep_deletes_only_an_offline_exact_name_leftover(self):
        leftover = {'busy': False, 'ephemeral': None, 'id': 1144, 'labels': ['roseos-mustang-builder'],
                    'name': 'mustang-42387816754', 'status': 'offline'}
        others = [
            leftover,
            {'busy': False, 'ephemeral': None, 'id': 9, 'labels': ['roseos-mustang-builder'],
             'name': 'mustang-1', 'status': 'offline'},
            {'busy': True, 'ephemeral': None, 'id': 8, 'labels': ['other'], 'name': 'kept', 'status': 'online',
             'runner_group_id': 4},
        ]
        self.assertEqual(enroll.orphan_ids(others, '42387816754'), [1144])
        busy = [dict(leftover, busy=True, status='online')]
        self.assertEqual(enroll.orphan_ids(busy, '42387816754'), [])
        deleted = []
        def group(page, token):
            self.assertEqual(page, 1)
            return {'total_count': 3, 'runners': others}
        enroll.sweep('42387816754', 'y'*24, group, lambda path, token: deleted.append(path))
        self.assertEqual(deleted, ['/orgs/Immortal-Protocols/actions/runners/1144'])
        paged = []
        def two_pages(page, token):
            if page == 1:
                return {'total_count': 2, 'runners': [others[1]]}
            if page == 2:
                return {'total_count': 2, 'runners': [leftover]}
            raise AssertionError(page)
        enroll.sweep('42387816754', 'y'*24, two_pages, lambda path, token: paged.append(path))
        self.assertEqual(paged, ['/orgs/Immortal-Protocols/actions/runners/1144'])
        blocked = []
        def short(page, token):
            if page == 1:
                return {'total_count': 2, 'runners': [leftover]}
            return {'total_count': 2, 'runners': []}
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            enroll.sweep('42387816754', 'y'*24, short, lambda path, token: blocked.append(path))
        self.assertEqual(blocked, [])
        with self.assertRaisesRegex(ValueError, 'leaves the Mustang'):
            enroll.allowed_path('DELETE', '/orgs/Immortal-Protocols/actions/runners/1144'.replace('1144', '0'))
        with self.assertRaisesRegex(ValueError, 'registration token'):
            enroll.allowed_path('POST', '/orgs/Immortal-Protocols/actions/runners/registration-token')

    def test_metadata_and_cancel_stay_on_this_vm_and_run(self):
        add = enroll.metadata_argv('add-metadata')
        self.assertIn('roseos-mustang-builder-20261107-a', add)
        self.assertIn('--metadata-from-file=mustang-jitconfig', add)
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
        skipped_builder = {'name': 'builder', 'status': 'completed', 'conclusion': 'skipped'}
        queued_source = {'name': 'source', 'status': 'queued'}
        self.assertEqual(enroll.queue_decision([skipped_builder, queued_source], 30), 'wait')
        self.assertEqual(enroll.queue_decision([skipped_builder, queued_source], enroll.QUEUE_LIMIT_SECONDS), 'stop')
        skipped_source = {'name': 'source', 'status': 'completed', 'conclusion': 'skipped'}
        queued_builder = {'name': 'builder', 'status': 'queued'}
        self.assertEqual(enroll.queue_decision([skipped_source, queued_builder], 30), 'wait')
        self.assertEqual(enroll.queue_decision([skipped_source, queued_builder], enroll.QUEUE_LIMIT_SECONDS), 'stop')
        self.assertEqual(enroll.queue_decision([skipped_builder, {'name': 'source', 'status': 'in_progress'}],
                                               enroll.QUEUE_LIMIT_SECONDS), 'ready')
        self.assertEqual(enroll.queue_decision([skipped_source, {'name': 'builder', 'status': 'in_progress'}],
                                               enroll.QUEUE_LIMIT_SECONDS), 'ready')
        self.assertEqual(enroll.watch_decision('failure', [{'name': 'builder', 'status': 'in_progress'}], 0), 'stop')
        self.assertNotIn('value', ' '.join(enroll.metadata_keys_argv()))
        self.assertIn('--format=json(metadata.items[].key)', enroll.metadata_keys_argv())
        self.assertNotIn('metadata.items.key)', ' '.join(enroll.metadata_keys_argv()))
        clock = {'now': 0}
        def fetch():
            return queued if clock['now'] < enroll.QUEUE_LIMIT_SECONDS else [{'name': 'builder', 'status': 'in_progress'}]
        def sleep(seconds):
            clock['now'] += seconds
        self.assertEqual(enroll.watch(fetch, sleep, lambda: clock['now']), 'ready')
        clock['now'] = enroll.QUEUE_LIMIT_SECONDS
        self.assertEqual(enroll.watch(lambda: queued, sleep, lambda: clock['now']), 'stop')

    def test_metadata_clear_rejects_a_null_projection_and_requires_readback(self):
        post_remove = {'metadata': {'items': [{'key': 'block-project-ssh-keys'}, {'key': 'enable-oslogin'}]},
                       'status': 'TERMINATED'}
        self.assertEqual(enroll.metadata_key_names(post_remove), ['block-project-ssh-keys', 'enable-oslogin'])
        self.assertFalse(enroll.metadata_is_present(post_remove))
        self.assertEqual(enroll.metadata_key_names(['block-project-ssh-keys', 'mustang-jitconfig']),
                         ['block-project-ssh-keys', 'mustang-jitconfig'])
        for raw in ('null', '', '   ', '{"metadata": null}', '{"metadata": {"items": null}}',
                    '{"metadata": {"items": {"key": null}}}', '{}', '["mustang-jitconfig", null]'):
            with self.assertRaisesRegex(ValueError, 'not a key list'):
                enroll.parse_metadata_document(raw)
        reads = {'n': 0}

        def transport(argv):
            if 'describe' not in argv:
                return subprocess.CompletedProcess(argv, 0, '', '')
            reads['n'] += 1
            if reads['n'] == 1:
                body = '["enable-oslogin", "mustang-jitconfig"]\n'
            else:
                body = json.dumps(post_remove)
            return subprocess.CompletedProcess(argv, 0, body, '')

        code, out, err = self.invoke_clear(transport)
        self.assertEqual(code, 0, err)
        self.assertIn('"state": "removed"', out)
        stuck = {'n': 0}

        def still_there(argv):
            if 'describe' not in argv:
                return subprocess.CompletedProcess(argv, 0, '', '')
            stuck['n'] += 1
            return subprocess.CompletedProcess(argv, 0, '["mustang-jitconfig"]\n', '')

        code, out, err = self.invoke_clear(still_there)
        self.assertEqual(code, 4)
        self.assertIn('remained after removal', err)

    def invoke_clear(self, transport):
        from contextlib import redirect_stderr, redirect_stdout
        import io
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = enroll.main(['clear-metadata', '--execute'], transport=transport)
        return code, out.getvalue(), err.getvalue()

    def test_infrastructure_host_check_starts_without_fabricated_inputs(self):
        runtime = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
        self.assertEqual(runtime['status'], 'unbound')
        self.assertEqual(runtime['appInputsRecord'], 'os/app-inputs/frankel-2026110704.json')
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
        self.assertEqual(cfg['appInputsRecord'], 'os/app-inputs/frankel-2026110704.json')
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
        self.assertIn('ACTIONS_RUNNER_INPUT_JITCONFIG=$jit_config', script)
        self.assertIn('export ACTIONS_RUNNER_INPUT_JITCONFIG', script)
        self.assertIn('./run.sh || status=$?', script)
        self.assertNotIn('--jitconfig', script)
        self.assertNotIn('exec ./run.sh', script)
        self.assertNotIn('eval ', script)
        self.assertNotIn('set -x', script)
        self.assertIn('trap cleanup EXIT', script)
        self.assertIn('-o "$outfile"', script)
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
        self.assertLess(enroll_job.index('sweep --execute'), enroll_job.index('clear-metadata --execute'))
        self.assertIn("failure() && steps.app.outcome == 'success'", enroll_job)
        cleanup = workflow.split('\n  cleanup:', 1)[1]
        self.assertLess(cleanup.index('mustang_controller.py stop'), cleanup.index('id: app'))
        self.assertLess(cleanup.index('clear-metadata --execute'), cleanup.index('sweep --execute'))
        app = cleanup[cleanup.index('create-github-app-token'):cleanup.index('sweep --execute')]
        self.assertNotIn('repositories:', app)
        self.assertNotIn('permission-contents:', app)
        self.assertEqual(self.cleanup_outcomes(cleanup, fail='app')['stop'], 'success')
        self.assertEqual(self.cleanup_outcomes(cleanup, fail='app')['owned'], 'success')
        self.assertEqual(self.cleanup_outcomes(cleanup, fail='app')['login'], 'success')
        self.assertEqual(self.cleanup_outcomes(cleanup, fail='app')['clear-metadata'], 'success')
        self.assertEqual(self.cleanup_outcomes(cleanup, fail='app')['sweep'], 'skipped')
        self.assertEqual(self.cleanup_outcomes(cleanup, fail='sweep')['stop'], 'success')
        self.assertEqual(self.cleanup_outcomes(cleanup, fail='sweep')['clear-metadata'], 'success')
        blocked = [{'id': 'app', 'if': None, 'run': '', 'uses': 'create-github-app-token'},
                   {'id': 'owned', 'if': None, 'run': 'cleanup-prepare', 'uses': ''},
                   {'id': None, 'if': "steps.owned.outputs.started == 'true'", 'run': 'mustang_controller.py stop', 'uses': ''}]
        self.assertEqual(self.step_outcomes(blocked, fail='app')['owned'], 'skipped')
        self.assertEqual(self.step_outcomes(blocked, fail='app')['stop'], 'skipped')
        self.assertNotIn('permission-contents:', enroll_job)
        self.assertEqual(enroll.APP_PERMISSION['access'], 'write')
        self.assertIn('generate-jitconfig', enroll.APP_PERMISSION['endpoint'])
        env = dict(os.environ, MUSTANG_EPHEMERAL_CHECK='1')
        self.assertEqual(__import__('subprocess').run(['bash', str(ROOT/'scripts/mustang-ephemeral-guest.sh')], env=env).returncode, 0)
        unit = (ROOT/'os/build-only/mustang-ephemeral-runner.service').read_text()
        self.assertIn('User=mustang-runner', unit)
        self.assertIn('Type=simple', unit)
        self.assertIn('RuntimeMaxSec=14400', unit)
        self.assertIn('NoNewPrivileges=true', unit)
        self.assertIn('PrivateTmp=true', unit)
        self.assertIn('RequiresMountsFor=/mnt/disks/mustang-data', unit)
        self.assertIn('Wants=network-online.target', unit)
        self.assertIn('After=network-online.target', unit)
        self.assertIn('ConditionPathIsMountPoint=/mnt/disks/mustang-data', unit)
        self.assertNotIn('local-fs.target', unit)
        self.assertNotIn('jitconfig', unit.lower())
        self.bootstrap_refuses_wrong_disk_and_keeps_a_verified_download()

    def cleanup_steps(self, job):
        steps = []
        current = None
        body = job.split('\n    steps:\n', 1)[1]
        for line in body.splitlines():
            if line.startswith('    - '):
                if current:
                    steps.append(current)
                current = {'if': None, 'id': None, 'run': '', 'uses': ''}
                key, _, value = line[6:].partition(':')
                if key in ('if', 'id', 'run', 'uses'):
                    current[key] = value.strip()
            elif current is not None and line.startswith('      ') and not line.startswith('        '):
                key, _, value = line.strip().partition(':')
                if key in ('if', 'id', 'run', 'uses'):
                    current[key] = value.strip()
        if current:
            steps.append(current)
        return steps

    def step_key(self, step):
        if step.get('id'):
            return step['id']
        blob = (step.get('run') or '') + (step.get('uses') or '')
        for name in ('stop', 'clear-metadata', 'logout', 'sweep', 'checkout'):
            if name in blob:
                return name
        return blob or 'step'

    def step_runs(self, condition, failed, outcomes):
        text = (condition or '').strip().strip('"').strip("'")
        status = 'success'
        rest = text
        for function, name in (('always()', 'always'), ('success()', 'success'), ('failure()', 'failure')):
            if text.startswith(function):
                status = name
                rest = text[len(function):].strip()
                if rest.startswith('&&'):
                    rest = rest[2:].strip()
                break
        else:
            if failed:
                return False
        if status == 'success' and failed:
            return False
        if status == 'failure' and not failed:
            return False
        if 'steps.owned.outputs.started' in rest:
            return outcomes.get('owned') == 'success'
        if 'steps.login.outcome' in rest:
            return outcomes.get('login') == 'success'
        if 'steps.app.outcome' in rest:
            return outcomes.get('app') == 'success'
        return True

    def step_outcomes(self, steps, fail):
        failed = False
        outcomes = {}
        for step in steps:
            key = self.step_key(step)
            if not self.step_runs(step.get('if'), failed, outcomes):
                outcomes[key] = 'skipped'
                continue
            if key == fail:
                outcomes[key] = 'failure'
                failed = True
                continue
            outcomes[key] = 'success'
        return outcomes

    def cleanup_outcomes(self, job, fail):
        return self.step_outcomes(self.cleanup_steps(job), fail)

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

    def test_source_dirs_are_runner_writable_under_a_root_owned_parent(self):
        import os
        import shlex
        import subprocess
        script = ROOT/'scripts/mustang-guest-bootstrap.sh'
        helper = ROOT/'scripts/mustang-source-dirs.sh'
        user = subprocess.check_output(['id', '-un'], text=True).strip()
        text = script.read_text()
        self.assertNotIn('--prepare-source-dirs', text)
        self.assertLess(text.index("refuse 'disk mount differs'"), text.index('\nprepare_source_dirs\n'))

        def run(home):
            command = '\n'.join([
                'set -euo pipefail',
                'source '+shlex.quote(str(helper)),
                'home='+shlex.quote(str(home)),
                'user='+shlex.quote(user),
                'prepare_source_dirs',
            ])
            return subprocess.run(['bash', '-c', command], capture_output=True, text=True)

        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)/'roseos'
            home.mkdir()
            (home/'source').mkdir(mode=0o750)
            (home/'runner').mkdir(mode=0o750)
            subprocess.run(['sudo', '-n', 'chown', 'root:root', str(home)], check=True)
            subprocess.run(['sudo', '-n', 'chmod', '755', str(home)], check=True)
            try:
                before = subprocess.check_output(['stat', '-c', '%u:%a', str(home)], text=True).strip()
                created = run(home)
                self.assertEqual(created.returncode, 0, created.stderr)
                self.assertEqual(created.stdout, '')
                self.assertEqual(subprocess.check_output(['stat', '-c', '%u:%a', str(home)], text=True).strip(), '0:755')
                self.assertEqual(before, '0:755')
                self.assertEqual(subprocess.check_output(['stat', '-c', '%a', str(home/'source')], text=True).strip(), '750')
                self.assertEqual(subprocess.check_output(['stat', '-c', '%a', str(home/'runner')], text=True).strip(), '750')
                for name, mode in (('sync-logs', '700'), ('roseos-pin', '750')):
                    path = home/name
                    self.assertEqual(subprocess.check_output(['stat', '-c', '%U:%a', str(path)], text=True).strip(), user+':'+mode)
                    self.assertFalse(path.is_symlink())
                    (path/'probe').write_text('runner-write')
                    blocked = subprocess.run(['sudo', '-n', '-u', 'nobody', 'touch', str(path/'denied')],
                                             capture_output=True, text=True)
                    self.assertNotEqual(blocked.returncode, 0)
                    self.assertFalse((path/'denied').exists())
                sibling = subprocess.run(['mkdir', str(home/'extra')], capture_output=True, text=True)
                self.assertNotEqual(sibling.returncode, 0)
                self.assertFalse((home/'extra').exists())
                again = run(home)
                self.assertEqual(again.returncode, 0, again.stderr)
            finally:
                subprocess.run(['sudo', '-n', 'chown', '-R', user, str(home)], check=False)
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)/'roseos'
            home.mkdir(mode=0o755)
            (home/'source').mkdir()
            (home/'runner').mkdir()
            (home/'sync-logs').symlink_to('/tmp')
            linked = run(home)
            self.assertEqual(linked.returncode, 4)
            self.assertEqual(linked.stderr.strip(), 'sync-logs')
            self.assertTrue((home/'sync-logs').is_symlink())
            self.assertFalse((home/'roseos-pin').exists())
            (home/'sync-logs').unlink()
            (home/'sync-logs').mkdir(mode=0o755)
            unexpected = run(home)
            self.assertEqual(unexpected.returncode, 4)
            self.assertEqual(unexpected.stderr.strip(), 'sync-logs')
            self.assertEqual(subprocess.check_output(['stat', '-c', '%a', str(home/'sync-logs')], text=True).strip(), '755')
            (home/'roseos-pin').write_text('not-a-directory')
            (home/'sync-logs').chmod(0o700)
            # owner is the test user and mode is now correct, so the next refusal is the file
            refused_file = run(home)
            self.assertEqual(refused_file.returncode, 4)
            self.assertEqual(refused_file.stderr.strip(), 'roseos-pin')
            self.assertTrue((home/'roseos-pin').is_file())


class WorkflowCliContractTest(unittest.TestCase):
    """Every dedicated-workflow ephemeral command is parsed by main, not a helper."""

    def workflow_commands(self):
        import shlex
        marker = 'python3 scripts/mustang_ephemeral.py '
        found = []
        for line in (ROOT/'.github/workflows/mustang-ota-dedicated.yml').read_text().splitlines():
            if marker not in line:
                continue
            found.append(shlex.split(line.split(marker, 1)[1].strip()))
        self.assertGreaterEqual(len(found), 8)
        self.assertIn(['put-metadata', '--execute', '--file', '$RUNNER_TEMP/mustang-jit'], found)
        return found

    def invoke(self, argv, transport, extra=None):
        from contextlib import redirect_stderr, redirect_stdout
        import io
        out, err = io.StringIO(), io.StringIO()
        env = {'GH_TOKEN': '', 'GITHUB_TOKEN': '', 'ENROLL_RESULT': 'failure', 'GITHUB_RUN_ID': '42391012772'}
        if extra:
            env.update(extra)
        with patch.dict(os.environ, env, clear=False):
            with redirect_stdout(out), redirect_stderr(err):
                code = enroll.main(list(argv), transport=transport)
        return code, out.getvalue(), err.getvalue()

    def test_workflow_commands_parse_and_deliver_only_a_private_file(self):
        secret = 'encoded-jit-config-secret-value-not-for-logs'
        calls = []

        def transport(argv):
            calls.append(list(argv))
            text = ' '.join(argv)
            if 'get-guest-attributes' in argv:
                digest = hashlib.sha256(secret.encode()).hexdigest()
                body = json.dumps([{'key': 'jit-fetched', 'namespace': 'mustang', 'value': digest}])
                return subprocess.CompletedProcess(argv, 0, body+'\n', '')
            if 'describe' in argv:
                seen_describe = sum(1 for call in calls if 'describe' in call)
                if seen_describe % 2 == 1:
                    body = '{"metadata":{"items":[{"key":"mustang-jitconfig"}]}}\n'
                else:
                    body = ('{"metadata":{"items":[{"key":"block-project-ssh-keys"},'
                            '{"key":"enable-oslogin"}]},"status":"TERMINATED"}\n')
                return subprocess.CompletedProcess(argv, 0, body, '')
            self.assertNotIn(secret, text)
            return subprocess.CompletedProcess(argv, 0, '', '')

        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary)/'mustang-jit'
            private.write_text(secret)
            private.chmod(0o600)
            public = Path(temporary)/'public'
            public.write_text(secret)
            public.chmod(0o644)
            link = Path(temporary)/'link'
            link.symlink_to(private)
            values = {'$GITHUB_RUN_ID': '42391012772', '$RUNNER_TEMP/mustang-jit': str(private)}
            seen = set()
            for raw in self.workflow_commands():
                argv = [values.get(part, part) for part in raw]
                seen.add(argv[0])
                code, out, err = self.invoke(argv, transport)
                self.assertNotIn('unrecognized arguments', err)
                self.assertNotIn(secret, out)
                self.assertNotIn(secret, err)
                if argv[0] in ('issue', 'sweep', 'cancel'):
                    self.assertEqual(code, 4)
                    self.assertIn('token', err)
                elif argv[0] == 'put-metadata':
                    self.assertEqual(code, 0, err)
                elif argv[0] == 'wait-fetched':
                    self.assertEqual(code, 0, err)
                elif argv[0] == 'clear-metadata':
                    self.assertEqual(code, 0, err)
                    self.assertIn('"state": "removed"', out)
                elif argv[0] == 'queue-watch':
                    self.assertEqual(code, 0, err)
                    self.assertIn('"decision": "stop"', out)
            self.assertEqual(seen, {'issue', 'put-metadata', 'wait-fetched', 'sweep', 'clear-metadata', 'queue-watch', 'cancel'})
            delivered = [argv for argv in calls if 'add-metadata' in argv]
            self.assertEqual(len(delivered), 1)
            self.assertEqual(delivered[0][-1], '--metadata-from-file=mustang-jitconfig='+str(private))
            self.assertIn('roseos-mustang-builder-20261107-a', delivered[0])
            self.assertTrue(all('immortal-os-builder' not in ' '.join(argv) and 'frankel' not in ' '.join(argv) for argv in calls))
            removed = [argv for argv in calls if 'remove-metadata' in argv]
            self.assertTrue(removed)
            self.assertTrue(all('--keys=mustang-jitconfig' in argv for argv in removed))
            fetched = [argv for argv in calls if 'get-guest-attributes' in argv]
            self.assertEqual(len(fetched), 1)
            self.assertIn('--query-path=mustang/jit-fetched', fetched[0])

            for bad in (str(Path(temporary)/'missing'), str(public), str(link), 'relative-jit', str(Path(temporary)/'..'/'passwd')):
                code, out, err = self.invoke(['put-metadata', '--execute', '--file', bad], transport)
                self.assertEqual(code, 4)
                self.assertIn('private regular file', err)
                self.assertNotIn(secret, err)
            code, out, err = self.invoke(['issue', '--execute', '--run-id', '42391012772', '--out', str(private), '--file', str(private)], transport)
            self.assertEqual(code, 4)
            self.assertIn('only accepted by put-metadata', err)
            self.assertNotIn(secret, err)
            code, out, err = self.invoke(['put-metadata', '--file', str(private)], transport)
            self.assertEqual(code, 4)
            self.assertIn('--execute', err)

        script = [sys.executable, str(ROOT/'scripts/mustang_ephemeral.py')]
        env = dict(os.environ)
        env.pop('GH_TOKEN', None)
        env.pop('GITHUB_TOKEN', None)
        missing = subprocess.run(script+['put-metadata', '--execute', '--file', '/tmp/mustang-jit-missing-not-a-secret'],
                                 capture_output=True, text=True, env=env)
        self.assertEqual(missing.returncode, 4)
        self.assertNotIn('unrecognized arguments', missing.stderr)
        self.assertIn('private regular file', missing.stderr)
        refused = subprocess.run(script+['sweep', '--execute', '--run-id', '42391012772', '--file', '/tmp/not-used'],
                                 capture_output=True, text=True, env=env)
        self.assertEqual(refused.returncode, 4)
        self.assertNotIn('unrecognized arguments', refused.stderr)
        self.assertIn('only accepted by put-metadata', refused.stderr)
        guest = (ROOT/'scripts/mustang-ephemeral-guest.sh').read_text()
        self.assertIn('instance/attributes/'+enroll.METADATA_KEY, guest)
        self.assertIn('guest-attributes/mustang/jit-fetched', guest)
        self.assertIn('ACTIONS_RUNNER_INPUT_JITCONFIG', guest)
        self.assertNotIn('--jitconfig', guest)


class GuestAttributeReadbackTest(unittest.TestCase):
    def real_list(self, value):
        return [{'key': 'jit-fetched', 'namespace': 'mustang', 'value': value}]

    def test_current_config_matches_the_gcloud_list_and_stale_values_do_not(self):
        current = 'a'*64
        prior = 'b'*64
        self.assertTrue(enroll.acknowledgement(self.real_list(current), current))
        self.assertFalse(enroll.acknowledgement(self.real_list('1'), current))
        self.assertFalse(enroll.acknowledgement(self.real_list(prior), current))
        self.assertFalse(enroll.acknowledgement([], current))
        for payload in ({'value': '1'}, {'variableValue': '1'}, '1', None,
                        [{'key': 'jit-fetched', 'namespace': 'mustang'}],
                        [{'key': 'jit-fetched', 'namespace': 'other', 'value': current}],
                        [{'key': 'other', 'namespace': 'mustang', 'value': current}],
                        self.real_list(current)+self.real_list(current)):
            with self.assertRaises(ValueError):
                enroll.acknowledgement(payload, current)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'mustang-jit'
            path.write_text('current-config')
            path.chmod(0o600)
            expected = hashlib.sha256(b'current-config').hexdigest()
            state = {'t': 0, 'calls': 0}

            def transport(argv):
                self.assertIn('get-guest-attributes', argv)
                self.assertIn('--query-path=mustang/jit-fetched', argv)
                self.assertIn('--format=json', argv)
                state['calls'] += 1
                if state['calls'] == 1:
                    body = json.dumps(self.real_list('1'))
                else:
                    body = json.dumps(self.real_list(expected))
                return subprocess.CompletedProcess(argv, 0, body, '')

            def pause(seconds):
                state['t'] += seconds

            code, out, err = self.invoke(['wait-fetched', '--execute', '--file', str(path)], transport, pause,
                                         clock=lambda: state['t'])
            self.assertEqual(code, 0, err)
            self.assertIn('"fetched": true', out)
            self.assertNotIn('current-config', out+err)
            self.assertNotIn(expected, out+err)
            failed = subprocess.CompletedProcess(['gcloud'], 1, '', 'denied')
            code, out, err = self.invoke(['wait-fetched', '--execute', '--file', str(path)],
                                         lambda argv: failed, pause)
            self.assertEqual(code, 4)
            self.assertIn('guest attribute query failed: exit 1', err)
            self.assertNotIn('denied', err)
            stale = {'t': 0}

            def only_legacy(argv):
                return subprocess.CompletedProcess(argv, 0, json.dumps(self.real_list('1')), '')

            def advance(seconds):
                stale['t'] += 200

            code, out, err = self.invoke(['wait-fetched', '--execute', '--file', str(path)], only_legacy, advance,
                                         clock=lambda: stale['t'])
            self.assertEqual(code, 4)
            self.assertIn('did not match this config', err)
            self.assertNotIn('1', err)

    def test_verified_missing_key_404_waits_and_permission_errors_stop(self):
        # Readonly capture, 10:24, of get-guest-attributes for a missing probe key.
        # The jit-fetched line keeps this header and account sentence and changes only the resource.
        captured = (
            'ERROR: (gcloud.compute.instances.get-guest-attributes) HTTPError 404: '
            "The resource 'mustang/nonexistent-probe' of type 'Guest Attribute' was not found. "
            'This command is authenticated as grok-bots@protocols-465723.iam.gserviceaccount.com '
            'which is the active account specified by the [core/account] property.')
        operator = captured.replace("'mustang/nonexistent-probe'", "'mustang/jit-fetched'", 1)
        controller = operator.replace(
            'grok-bots@protocols-465723.iam.gserviceaccount.com',
            'mustang-controller@protocols-465723.iam.gserviceaccount.com', 1)
        self.assertEqual(operator, enroll.GUEST_ATTRIBUTE_404_HEADER % 'mustang/jit-fetched'
                         + enroll.GUEST_ATTRIBUTE_ACCOUNTS[0] + enroll.GUEST_ATTRIBUTE_404_SUFFIX)
        self.assertEqual(controller, enroll.GUEST_ATTRIBUTE_404_HEADER % 'mustang/jit-fetched'
                         + enroll.GUEST_ATTRIBUTE_ACCOUNTS[1] + enroll.GUEST_ATTRIBUTE_404_SUFFIX)
        for line in (operator, controller):
            self.assertTrue(enroll.missing_guest_attribute(subprocess.CompletedProcess(['gcloud'], 1, '', line)))
            self.assertTrue(enroll.missing_guest_attribute(subprocess.CompletedProcess(['gcloud'], 1, ' \n', line+'\n')))
        self.assertFalse(enroll.missing_guest_attribute(subprocess.CompletedProcess(['gcloud'], 1, '', captured)))
        handwritten = (
            'ERROR: (gcloud.compute.instances.get-guest-attributes) HTTPError 404: '
            "The resource 'projects/protocols-465723/zones/us-west2-a/instances/"
            "roseos-mustang-builder-20261107-a/guestAttributes/mustang/jit-fetched' was not found")
        self.assertFalse(enroll.missing_guest_attribute(subprocess.CompletedProcess(['gcloud'], 1, '', handwritten)))
        unknown = operator.replace(
            'grok-bots@protocols-465723.iam.gserviceaccount.com',
            'other@protocols-465723.iam.gserviceaccount.com', 1)
        self.assertFalse(enroll.missing_guest_attribute(subprocess.CompletedProcess(['gcloud'], 1, '', unknown)))
        self.assertFalse(enroll.missing_guest_attribute(
            subprocess.CompletedProcess(['gcloud'], 1, '', operator+' ya29.extra-secret')))
        self.assertFalse(enroll.missing_guest_attribute(subprocess.CompletedProcess(['gcloud'], 2, '', operator)))
        self.assertFalse(enroll.missing_guest_attribute(subprocess.CompletedProcess(['gcloud'], 1, '[]', operator)))
        permission = (
            'ERROR: (gcloud.compute.instances.get-guest-attributes) HTTPError 403: '
            "Required 'compute.instances.getGuestAttributes' permission for "
            "'projects/protocols-465723/zones/us-west2-a/instances/roseos-mustang-builder-20261107-a' "
            'ya29.permission-sentinel-not-a-token')
        self.assertFalse(enroll.missing_guest_attribute(subprocess.CompletedProcess(['gcloud'], 1, '', permission)))
        literal = controller
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'mustang-jit'
            path.write_text('current-config')
            path.chmod(0o600)
            expected = hashlib.sha256(b'current-config').hexdigest()
            state = {'t': 0, 'calls': 0}

            def appear(argv):
                self.assertIn('--query-path=mustang/jit-fetched', argv)
                state['calls'] += 1
                if state['calls'] < 3:
                    return subprocess.CompletedProcess(argv, 1, '', literal+'\n')
                return subprocess.CompletedProcess(argv, 0, json.dumps(self.real_list(expected)), '')

            def step(seconds):
                self.assertEqual(seconds, 5)
                state['t'] += seconds

            code, out, err = self.invoke(['wait-fetched', '--execute', '--file', str(path)], appear, step,
                                         clock=lambda: state['t'])
            self.assertEqual(code, 0, err)
            self.assertEqual(state['calls'], 3)
            self.assertNotIn('HTTPError', err)
            self.assertNotIn('guestAttributes', err)
            self.assertNotIn('grok-bots', err)
            self.assertNotIn('mustang-controller', err)
            self.assertNotIn('serviceaccount', err.lower())
            self.assertNotIn(expected, out+err)
            calls = {'n': 0}

            def forbidden(argv):
                calls['n'] += 1
                return subprocess.CompletedProcess(argv, 1, '', permission)

            code, out, err = self.invoke(['wait-fetched', '--execute', '--file', str(path)], forbidden, step,
                                         clock=lambda: 0)
            self.assertEqual(code, 4)
            self.assertEqual(calls['n'], 1)
            self.assertIn('guest attribute query failed: exit 1', err)
            self.assertNotIn('ya29.permission-sentinel-not-a-token', err)
            self.assertNotIn('HTTPError', err)
            self.assertNotIn('getGuestAttributes', err)
            self.assertNotIn('grok-bots', err)
            self.assertNotIn('ya29', err)
            bound = {'t': 0, 'calls': 0}

            def stay_missing(argv):
                bound['calls'] += 1
                return subprocess.CompletedProcess(argv, 1, '', literal)

            def tick(seconds):
                bound['t'] += seconds

            code, out, err = self.invoke(['wait-fetched', '--execute', '--file', str(path)], stay_missing, tick,
                                         clock=lambda: bound['t'])
            self.assertEqual(code, 4)
            self.assertEqual(bound['calls'], 37)
            self.assertEqual(bound['t'], 180)
            self.assertIn('did not match this config before the bound', err)
            self.assertNotIn('query failed', err)
            self.assertNotIn('HTTPError', err)
            self.assertNotIn('protocols-465723', err)
            self.assertNotIn('guestAttributes', err)
            self.assertNotIn('mustang-controller', err)
            self.assertNotIn('grok-bots', err)
            self.assertNotIn('core/account', err)

    def invoke(self, argv, transport, pause, clock=None):
        from contextlib import redirect_stderr, redirect_stdout
        import io
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = enroll.main(argv, transport=transport, sleep=pause, clock=clock)
        return code, out.getvalue(), err.getvalue()


class GuestMetadataOrderTest(unittest.TestCase):
    """The config read and the guest-attribute acknowledgement are different requests."""

    def run_guest(self, root, plan, body='jit-body-must-not-be-logged',
                  run_exit='0', run_signal=''):
        binary = Path(root)/'bin'
        runner = Path(root)/'roseos/runner'
        binary.mkdir(parents=True)
        runner.mkdir(parents=True)
        (binary/'curl').write_text(
            '#!/usr/bin/env python3\n'
            'import os, sys\n'
            'args=sys.argv[1:]; method, outfile, url, data="GET", None, None, ""\n'
            'i=0\n'
            'while i < len(args):\n'
            '    if args[i]=="-X" and i+1<len(args): method=args[i+1]; i+=2; continue\n'
            '    if args[i]=="-o" and i+1<len(args): outfile=args[i+1]; i+=2; continue\n'
            '    if args[i]=="--data" and i+1<len(args): data=args[i+1]; i+=2; continue\n'
            '    if args[i]=="-w": i+=2; continue\n'
            '    if args[i].startswith("http"): url=args[i]\n'
            '    i+=1\n'
            'plan=os.environ["MUSTANG_CURL_PLAN"]\n'
            'lines=open(plan).read().splitlines()\n'
            'op, status=lines[0].split(); open(plan,"w").write("\\n".join(lines[1:]))\n'
            'open(os.environ["MUSTANG_CURL_LOG"],"a").write(method+" "+url+" "+data+"\\n")\n'
            'if method!=op: sys.stderr.write("unexpected "+method+"\\n"); sys.exit(3)\n'
            'if status=="network": sys.exit(7)\n'
            'if outfile and status=="200" and method=="GET":\n'
            '    open(outfile,"w").write(os.environ["MUSTANG_CURL_BODY"])\n'
            'elif outfile: open(outfile,"w").write("")\n'
            'sys.stdout.write(status)\n')
        (binary/'curl').chmod(0o755)
        (runner/'run.sh').write_text(
            '#!/usr/bin/env python3\n'
            'import hashlib, os, pathlib, signal, sys, time\n'
            'argv = pathlib.Path(os.environ["MUSTANG_ARGV_LOG"])\n'
            'argv.write_text("".join(arg + "\\n" for arg in sys.argv[1:]))\n'
            'value = os.environ.get("ACTIONS_RUNNER_INPUT_JITCONFIG", "")\n'
            'pathlib.Path(os.environ["MUSTANG_ENV_HASH_LOG"]).write_text(\n'
            '    hashlib.sha256(value.encode()).hexdigest() + "\\n")\n'
            'command = pathlib.Path("/proc/self/cmdline").read_bytes()\n'
            'mark = "argv-has-secret\\n" if value.encode() in command else "argv-clean\\n"\n'
            'pathlib.Path(os.environ["MUSTANG_ARGV_MARK"]).write_text(mark)\n'
            'if os.environ.get("MUSTANG_RUN_SIGNAL") == "TERM":\n'
            '    pid = os.getppid()\n'
            '    for _ in range(4):\n'
            '        comm = pathlib.Path("/proc/%s/comm" % pid).read_text().strip()\n'
            '        if comm == "bash":\n'
            '            break\n'
            '        status = pathlib.Path("/proc/%s/status" % pid).read_text().splitlines()\n'
            '        pid = int(next(line.split()[1] for line in status if line.startswith("PPid:")))\n'
            '    os.kill(pid, signal.SIGTERM)\n'
            '    time.sleep(2)\n'
            'code = int(os.environ.get("MUSTANG_RUN_EXIT", "0"))\n'
            'if code == 0 and os.environ.get("MUSTANG_RUN_SIGNAL") != "TERM":\n'
            '    with open(os.environ["MUSTANG_RUN_LOG"], "a") as handle:\n'
            '        handle.write("ran\\n")\n'
            'sys.exit(code)\n')
        (runner/'run.sh').chmod(0o755)
        plan_path = Path(root)/'plan'
        plan_path.write_text('\n'.join(plan)+'\n')
        log = Path(root)/'curl.log'
        ran = Path(root)/'ran'
        argv_log = Path(root)/'argv.log'
        env_hash = Path(root)/'env.sha'
        argv_mark = Path(root)/'argv.mark'
        cleanup_log = Path(root)/'cleanup.log'
        env = dict(os.environ, PATH=str(binary)+':'+os.environ['PATH'],
                   MUSTANG_GUEST_ROOT=str(Path(root)/'roseos'),
                   MUSTANG_METADATA_URL='http://metadata.test/computeMetadata/v1',
                   MUSTANG_GUEST_WAIT_SECONDS='5', MUSTANG_GUEST_POLL_SECONDS='0',
                   MUSTANG_CURL_PLAN=str(plan_path), MUSTANG_CURL_LOG=str(log),
                   MUSTANG_CURL_BODY=body, MUSTANG_RUN_LOG=str(ran),
                   MUSTANG_ARGV_LOG=str(argv_log), MUSTANG_ENV_HASH_LOG=str(env_hash),
                   MUSTANG_ARGV_MARK=str(argv_mark), MUSTANG_CLEANUP_LOG=str(cleanup_log),
                   MUSTANG_RUN_EXIT=run_exit, MUSTANG_RUN_SIGNAL=run_signal)
        result = subprocess.run(['bash', str(ROOT/'scripts/mustang-ephemeral-guest.sh')],
                                env=env, capture_output=True, text=True)
        self.assertNotIn(body, result.stdout)
        self.assertNotIn(body, result.stderr)
        cleanup = cleanup_log.read_text() if cleanup_log.exists() else ''
        self.assertNotIn(body, cleanup)
        argv = argv_log.read_text() if argv_log.exists() else ''
        self.assertNotIn(body, argv)
        self.assertNotIn('--jitconfig', argv)
        if env_hash.exists():
            self.assertEqual(env_hash.read_text().strip(),
                             hashlib.sha256(body.encode()).hexdigest())
            self.assertEqual(argv_mark.read_text(), 'argv-clean\n')
            self.assertNotIn(body, argv_mark.read_text())
        leftover = list(runner.glob('jit.*'))
        return result, log.read_text() if log.exists() else '', ran.exists(), leftover, cleanup

    def test_not_ready_is_a_get_and_a_forbidden_ack_does_not_start_the_runner(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            ready, log, ran, leftover, cleanup = self.run_guest(base/'ready', ['GET 404', 'GET 200', 'PUT 200'])
            self.assertEqual(ready.returncode, 0, ready.stderr)
            self.assertIn('metadata GET failed: HTTP 404', ready.stderr)
            self.assertNotIn('ack failed', ready.stderr)
            self.assertTrue(ran)
            self.assertEqual(leftover, [])
            self.assertTrue(cleanup)
            self.assertTrue(all(line == 'unset' for line in cleanup.splitlines()))
            self.assertIn('GET http://metadata.test/computeMetadata/v1/instance/attributes/mustang-jitconfig', log)
            digest = hashlib.sha256(b'jit-body-must-not-be-logged').hexdigest()
            self.assertIn('PUT http://metadata.test/computeMetadata/v1/instance/guest-attributes/mustang/jit-fetched '+digest, log)
            self.assertNotIn('jit-body-must-not-be-logged', log)
            denied, log, ran, leftover, _ = self.run_guest(base/'denied', ['GET 200', 'PUT 403'])
            self.assertEqual(denied.returncode, 4, denied.stderr)
            self.assertIn('guest attribute ack failed: HTTP 403', denied.stderr)
            self.assertNotIn('metadata GET failed: HTTP 403', denied.stderr)
            self.assertFalse(ran)
            self.assertEqual(leftover, [])
            self.assertFalse((base/'denied/roseos/runner/.runner').exists())
            get_denied, _, ran, leftover, _ = self.run_guest(base/'get-denied', ['GET 403', 'PUT 200'])
            self.assertEqual(get_denied.returncode, 4, get_denied.stderr)
            self.assertIn('metadata GET failed: HTTP 403', get_denied.stderr)
            self.assertNotIn('ack failed', get_denied.stderr)
            self.assertFalse(ran)
            self.assertEqual(leftover, [])
            networked, _, ran, _, _ = self.run_guest(base/'network', ['GET network', 'GET 200', 'PUT 200'])
            self.assertEqual(networked.returncode, 0, networked.stderr)
            self.assertIn('metadata GET failed: network', networked.stderr)
            self.assertTrue(ran)

    def test_runner_input_is_the_exact_config_and_leaves_no_env_on_failure_or_signal(self):
        body = 'exact-config-bytes=\nnot-a-path\n'
        sentinel = 'SENTINEL-NOT-A-PATH'
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            exact, _, ran, leftover, cleanup = self.run_guest(
                base/'exact', ['GET 200', 'PUT 200'], body=body)
            self.assertEqual(exact.returncode, 0, exact.stderr)
            self.assertTrue(ran)
            self.assertEqual(leftover, [])
            self.assertNotIn(body, exact.stdout)
            self.assertNotIn(body, exact.stderr)
            self.assertNotIn(body, cleanup)
            self.assertTrue(all(line == 'unset' for line in cleanup.splitlines()))
            failed, _, ran, leftover, cleanup = self.run_guest(
                base/'failed', ['GET 200', 'PUT 200'], body=sentinel, run_exit='9')
            self.assertEqual(failed.returncode, 9, failed.stderr)
            self.assertFalse(ran)
            self.assertEqual(leftover, [])
            self.assertNotIn(sentinel, failed.stdout)
            self.assertNotIn(sentinel, failed.stderr)
            self.assertNotIn(sentinel, cleanup)
            self.assertTrue(all(line == 'unset' for line in cleanup.splitlines()))
            signalled, _, ran, leftover, cleanup = self.run_guest(
                base/'signal', ['GET 200', 'PUT 200'], body=sentinel, run_signal='TERM')
            self.assertEqual(signalled.returncode, 143, signalled.stderr)
            self.assertFalse(ran)
            self.assertEqual(leftover, [])
            self.assertNotIn(sentinel, signalled.stdout)
            self.assertNotIn(sentinel, signalled.stderr)
            self.assertNotIn(sentinel, cleanup)
            self.assertTrue(all(line == 'unset' for line in cleanup.splitlines()))


if __name__ == '__main__':
    unittest.main()
