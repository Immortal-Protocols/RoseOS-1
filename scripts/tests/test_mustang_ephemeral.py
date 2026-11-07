"""Offline checks for one-job Mustang enrollment. No token mint, metadata write, or VM call."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts')]
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
        clock = {'now': 0}
        def fetch():
            return queued if clock['now'] < enroll.QUEUE_LIMIT_SECONDS else [{'name': 'builder', 'status': 'in_progress'}]
        def sleep(seconds):
            clock['now'] += seconds
        self.assertEqual(enroll.watch(fetch, sleep, lambda: clock['now']), 'ready')
        clock['now'] = enroll.QUEUE_LIMIT_SECONDS
        self.assertEqual(enroll.watch(lambda: queued, sleep, lambda: clock['now']), 'stop')

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
        self.assertNotIn('config.sh', script)
        self.assertNotIn('--token', script)
        env = dict(os.environ, MUSTANG_EPHEMERAL_CHECK='1')
        self.assertEqual(__import__('subprocess').run(['bash', str(ROOT/'scripts/mustang-ephemeral-guest.sh')], env=env).returncode, 0)
        unit = (ROOT/'os/build-only/mustang-ephemeral-runner.service').read_text()
        self.assertIn('User=mustang-runner', unit)
        self.assertNotIn('jitconfig', unit.lower())


if __name__ == '__main__':
    unittest.main()
