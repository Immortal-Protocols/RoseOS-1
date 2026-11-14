"""Offline integrated Yogi delivery checks; no cloud, signing, or native build."""
import contextlib
import copy
import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yogi_compile
import yogi_full as full
import yogi_ota as yogi

SOURCE = 'a'*40
ENV = {'GITHUB_REF': 'refs/heads/main', 'GITHUB_SHA': SOURCE,
       'GITHUB_WORKFLOW_REF': yogi.WORKFLOW+'@refs/heads/main', 'GITHUB_RUN_ID': '123',
       'GITHUB_RUN_ATTEMPT': '1', 'PUBLISH': 'true', 'GCP_SIGNER_SERVICE_ACCOUNT': yogi.SIGNER,
       'GCP_WORKLOAD_IDENTITY_PROVIDER': full.PROVIDER}
PINS = {'otaCertificateSha256': 'b'*64, 'avbPublicKeySha256': 'c'*64}


def keys():
    return {'schema': 'roseos.signing-bootstrap-receipt.v1', 'profile': yogi.PROFILE, 'device': yogi.DEVICE,
            'project': yogi.PROJECT, 'status': 'complete', 'reasons': [], 'recoveryNeeded': False,
            'recipeSha': SOURCE, 'cleanup': {'workspaceRemoved': True}, 'publicIdentity': PINS,
            'secrets': {k: {'name': v, 'state': 'committed', 'committedVersion': '1'} for k, v in yogi.SECRETS.items()}}


def allocation(build, epoch):
    return dict(schema='roseos.yogi-allocation.v1', device='yogi', build=build, timestamp=epoch,
                source=SOURCE, runId='123', runAttempt='1')


class FullTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = copy.deepcopy(yogi.config())
        self.cfg['fullRun'].update(enabled=True, setupEvidence='synthetic setup receipt')
        self.cfg['publicationEnabled'] = True
        self.cfg['builder']['status'] = 'ready'
        self.cfg['builder']['root'] = str(self.root)
        self.cfg['builder']['bootstrapState'] = str(self.root/'bootstrap')
        (self.root/'bootstrap').mkdir()
        self.cfg['builder']['evidence']['runner']['organizationEligibility'] = 'synthetic exclusive route'
        self.cfg['builder']['evidence']['runner']['protectedWorkflow'] = yogi.WORKFLOW+'@refs/heads/main'
        self.cfg['signing'].update(status='approved', access={'approvalRef': 'synthetic',
            'expiresAt': (datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=1)).isoformat()})
        self.cfg['signing']['bootstrapReceipt'] = str(self.root/'not-committed.json')
        self.state = {'schema': full.SCHEMA, 'run': full.current(ENV), 'phase': 'initialized', 'needsBootstrap': True}
        self.receipt = self.root/'keys.json'
        self.receipt.write_text(json.dumps(keys()))

    def test_guard_is_main_attempt_one_only_and_source_setup_stays_closed(self):
        self.assertFalse(yogi.config()['fullRun']['enabled'])
        for changes in ({'GITHUB_REF': 'refs/heads/topic'}, {'GITHUB_RUN_ATTEMPT': '2'},
                        {'GITHUB_WORKFLOW_REF': yogi.WORKFLOW+'@refs/heads/topic'}):
            with self.subTest(changes=changes), self.assertRaises(yogi.Refused):
                full.current(dict(ENV, **changes))
        with self.assertRaisesRegex(yogi.Refused, 'setup is not bound'):
            full.guard(yogi.config(), ENV)

    def test_guard_accepts_only_the_exact_verified_export_before_identity_or_allocation(self):
        with mock.patch.object(yogi, 'profile_problem', return_value=None), \
                mock.patch.object(full.mustang_route, 'bound_app_freshness', return_value=[]):
            full.guard(self.cfg, ENV)
            for changes in ({'PUBLISH': 'false'}, {'GCP_WORKLOAD_IDENTITY_PROVIDER': 'foreign'}):
                with self.subTest(changes=changes), self.assertRaises(yogi.Refused):
                    full.guard(self.cfg, dict(ENV, **changes))
        # An immutable published app set is an explicit input, not a claim to
        # contain every later application source change in the OS checkout.
        for drift in (None, ['dev.pixel.voice']):
            with mock.patch.object(yogi, 'profile_problem', return_value=None), \
                    mock.patch.object(full.mustang_route, 'bound_app_freshness', return_value=drift):
                full.guard(self.cfg, ENV)
        self.cfg['appInputs']['selection']['recordSha256'] = '0'*64
        with mock.patch.object(yogi, 'profile_problem', return_value=None), \
                self.assertRaisesRegex(yogi.Refused, 'export selection differs'):
            full.guard(self.cfg, ENV)

    def test_complete_public_receipt_is_retained_and_changed_receipt_refuses(self):
        bound = full.accept_keys(self.cfg, self.state, self.receipt)
        self.assertEqual(yogi.identities(bound), (PINS, {'keys': '1', 'passphrase': '1'}))
        saved = Path(self.state['keyReceipt'])
        self.assertEqual(saved.parent, self.root/'bootstrap')
        full.runtime_config(self.cfg, self.state)
        value = keys(); value['secrets']['keys']['committedVersion'] = '2'
        saved.write_text(json.dumps(value))
        with self.assertRaisesRegex(yogi.Refused, 'changed within this run'):
            full.runtime_config(self.cfg, self.state)

    def test_bad_or_foreign_new_key_receipt_is_not_adopted(self):
        for changes in ({'status': 'blocked'}, {'device': 'mustang'}, {'recipeSha': 'd'*40},
                        {'publicIdentity': {'otaCertificateSha256': next(iter(yogi.LAB_PINS)), 'avbPublicKeySha256': 'c'*64}}):
            value = keys(); value.update(changes); self.receipt.write_text(json.dumps(value))
            with self.subTest(changes=changes), self.assertRaises(yogi.Refused):
                full.accept_keys(self.cfg, self.state, self.receipt)

    def test_state_is_bound_to_run_source_and_attempt(self):
        path = self.root/'state.json'; path.write_text(json.dumps(self.state))
        self.assertEqual(full.load_state(path, ENV), self.state)
        for changes in ({'GITHUB_RUN_ID': '124'}, {'GITHUB_SHA': 'f'*40}):
            with self.subTest(changes=changes), self.assertRaises(yogi.Refused):
                full.load_state(path, dict(ENV, **changes))

    def test_initializer_reuses_only_complete_journal_bound_identity(self):
        full.accept_keys(self.cfg, self.state, self.receipt)
        journal = self.root/'bootstrap/yogi-v1.json'
        journal.write_text(json.dumps(dict(profile=yogi.PROFILE, status='complete', publicIdentity=PINS,
                                          committed={'keys': '1', 'passphrase': '1'})))
        self.cfg['builder']['attempts'] = str(self.root)
        mounts = self.cfg['builder']['evidence']['storage']['mounts']
        findmnt = type('Result', (), {'stdout': json.dumps({'filesystems': [
            {'uuid': mounts['filesystemUuid'], 'target': mounts['mountPoint']} ]})})()
        with mock.patch.object(full, 'guard'), mock.patch.object(yogi, 'identity_refusal'), \
                mock.patch.object(yogi, 'preflight'), mock.patch.object(full.subprocess, 'run', return_value=findmnt), \
                mock.patch.object(full.mustang_route, 'bound_app_freshness', return_value=['dev.pixel.voice']):
            reused = full.initialize(self.cfg, self.root/'new-run.json', ENV)
            self.assertFalse(reused['needsBootstrap'])
            self.assertEqual(reused['appSourceDrift'], ['dev.pixel.voice'])
            for changed in ('importing', 'recovery-needed'):
                value = yogi.read(journal); value['status'] = changed; journal.write_text(json.dumps(value))
                with self.subTest(status=changed), self.assertRaisesRegex(yogi.Refused, 'completed bootstrap journal'):
                    full.initialize(self.cfg, self.root/'failed-run.json', ENV)

    def test_complete_automatic_phase_sequence_and_same_run_publisher_binding(self):
        """Exercise the orchestrator across both allocations without native/cloud actions."""
        self.cfg['builder']['attempts'] = str(self.root/'attempts')
        (self.root/'attempts').mkdir()
        state_path = self.root/'run.json'
        self.state['run'] = full.current(ENV)
        state_path.write_text(json.dumps(self.state))
        calls = []
        def compile_build(cfg, build):
            calls.append(('compile', build))
        def signed(cfg, build, stage):
            calls.append(('sign', build, stage))
            directory = yogi.attempt_dir(cfg, build); (directory/'sign').mkdir()
            a = yogi.read(directory/'allocation.json')
            value = dict(schema='roseos.yogi-sign.v1', device='yogi', status='signed', stage=stage,
                         build=build, timestamp=a['timestamp'], candidateSource=SOURCE, recipeSource=SOURCE,
                         secrets={k: {'name': v, 'version': '1'} for k, v in yogi.SECRETS.items()})
            (directory/'sign/yogi-sign-receipt.json').write_text(json.dumps(value))
        def install(cfg, build):
            calls.append(('install', build)); directory = yogi.attempt_dir(cfg, build)/'install'; directory.mkdir()
            value = dict(schema='roseos.yogi-install-image.v1', build=build, avbPublicKeySha256=PINS['avbPublicKeySha256'])
            (directory/'install-receipt.json').write_text(json.dumps(value)); return value
        def staged(cfg, build, out):
            calls.append(('stage', build)); out.mkdir()
        with mock.patch.dict(os.environ, ENV), mock.patch.object(yogi, 'config', return_value=self.cfg), \
                mock.patch.object(yogi, 'preflight'), mock.patch.object(yogi, 'builder_lock', side_effect=contextlib.nullcontext), \
                mock.patch.object(yogi, 'prepare'), mock.patch.object(yogi, 'integrate'), \
                mock.patch.object(yogi, 'compile_build', side_effect=compile_build), \
                mock.patch.object(yogi, 'sign', side_effect=signed), \
                mock.patch.object(yogi, 'install_image', side_effect=install), mock.patch.object(yogi, 'stage', side_effect=staged):
            for action in full.TRANSITIONS:
                args = [action, '--state', str(state_path), '--receipt', str(self.receipt),
                        '--apps', str(self.root/'apps'), '--out', str(self.root/'public'),
                        '--bindings', str(self.root/'bindings')]
                self.assertEqual(full.main(args), 0, action)
            final = full.load_state(state_path, ENV)
            self.assertEqual(final['phase'], 'staged')
            cfg, binding = full.publisher_config(self.cfg, self.root/'bindings', ENV)
            self.assertEqual(cfg['bootstrap']['build'], final['a']['build'])
            self.assertEqual(binding['b']['build'], final['b']['build'])
            self.assertEqual([c[0] for c in calls], ['compile', 'sign', 'install', 'compile', 'sign', 'stage'])
            self.assertGreater(int(final['b']['build']), int(final['a']['build']))
            self.assertGreater(int(final['b']['timestamp']), int(final['a']['timestamp']))
            self.assertEqual(full.main(['compile-b', '--state', str(state_path)]), 1)

    def bindings(self):
        directory = self.root/'bindings'; directory.mkdir()
        a, b = allocation('2026111401', '1794600001'), allocation('2026111402', '1794600002')
        baseline = dict(status='signed', build=a['build'], timestamp=a['timestamp'], source=SOURCE)
        sign = dict(schema='roseos.yogi-sign.v1', device='yogi', status='signed', stage='bootstrap',
                    build=a['build'], timestamp=a['timestamp'], candidateSource=SOURCE, recipeSource=SOURCE,
                    secrets={k: {'name': v, 'version': '1'} for k, v in yogi.SECRETS.items()})
        install = dict(schema='roseos.yogi-install-image.v1', build=a['build'], avbPublicKeySha256=PINS['avbPublicKeySha256'])
        for name, value in [('signing-bootstrap-receipt.json', keys()), ('bootstrap-sign-receipt.json', sign),
                            ('bootstrap-install-receipt.json', install)]:
            (directory/name).write_text(json.dumps(value))
        record = dict(schema=full.SCHEMA, run=full.current(ENV), a=a, b=b, baseline=baseline,
                      appRecord=self.cfg['appInputs']['record'], appRecordSha256=yogi.digest(yogi.ROOT/self.cfg['appInputs']['record']),
                      files={name: yogi.digest(directory/name) for name in full.BINDINGS-{'runtime.json'}})
        (directory/'runtime.json').write_text(json.dumps(record))
        return directory, record

    def test_hosted_consumer_accepts_same_run_baseline_without_native_paths_or_phone_claim(self):
        directory, record = self.bindings()
        bound, observed = full.publisher_config(self.cfg, directory, ENV)
        self.assertEqual(bound['bootstrap']['build'], record['a']['build'])
        self.assertEqual(observed, record)
        self.assertEqual(yogi.identities(bound)[0], PINS)

    def test_hosted_consumer_refuses_old_run_nonmonotonic_build_and_foreign_source(self):
        directory, record = self.bindings()
        for section, key, value in [('run', 'runId', '124'), ('b', 'build', record['a']['build']),
                                    ('b', 'timestamp', record['a']['timestamp']), ('a', 'source', 'e'*40),
                                    ('baseline', 'source', 'f'*40)]:
            bad = copy.deepcopy(record); bad[section][key] = value
            (directory/'runtime.json').write_text(json.dumps(bad))
            with self.subTest(section=section, key=key), self.assertRaises(yogi.Refused):
                full.publisher_config(self.cfg, directory, ENV)

    def test_hosted_consumer_refuses_rehashed_but_invalid_baseline_signature_binding(self):
        directory, record = self.bindings()
        name = 'bootstrap-sign-receipt.json'
        original = yogi.read(directory/name)
        for field, value in [('status', 'failed'), ('stage', 'ota'), ('recipeSource', 'f'*40), ('secrets', {})]:
            bad = dict(original, **{field: value}); (directory/name).write_text(json.dumps(bad))
            record['files'][name] = yogi.digest(directory/name)
            (directory/'runtime.json').write_text(json.dumps(record))
            with self.subTest(field=field), self.assertRaisesRegex(yogi.Refused, 'baseline signing receipt'):
                full.publisher_config(self.cfg, directory, ENV)


class CompilerTest(unittest.TestCase):
    def test_compiler_env_excludes_signer_and_workflow_credentials(self):
        inherited = dict(PATH='/usr/bin:/bin', HOME='/tmp', ACTIONS_ID_TOKEN_REQUEST_TOKEN='private',
                         GOOGLE_APPLICATION_CREDENTIALS='private', GCP_SERVICE_ACCOUNT_JSON='private',
                         CLOUDSDK_CONFIG='private', GH_TOKEN='private', GITHUB_TOKEN='private', CCACHE_REMOTE_STORAGE='remote')
        self.assertEqual(yogi_compile.environment(inherited), {'PATH': '/usr/bin:/bin', 'HOME': '/tmp'})

    def test_cache_uses_yogi_disk_and_never_imports_an_old_out_dir(self):
        cfg = yogi.config()
        for build in ('2026111401', '2026111402'):
            self.assertEqual(yogi_compile.output_dir(cfg, build), 'out-yogi-cache')
        uncached = copy.deepcopy(cfg); uncached['builder'].pop('compilerCache')
        self.assertEqual(yogi_compile.output_dir(uncached, '2026111401'), 'out/yogi-2026111401')
        with mock.patch.object(yogi_compile.cache, 'prepare_at', return_value={'enabled': True}) as prepare:
            yogi_compile.prepare_cache(cfg, yogi.ROOT, yogi_compile.output_dir(cfg, '2026111401'))
        self.assertEqual(prepare.call_args.args, (Path('/srv/rose-yogi/compiler-cache-v1'),))
        self.assertEqual(prepare.call_args.kwargs['out_dir'], 'out-yogi-cache')

    def test_failed_compiler_streams_both_channels_and_retains_exact_log(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()) as output:
            log = Path(temp)/'compile.log'
            result = yogi_compile.stream([sys.executable, '-c',
                "import sys;print('compiler stdout',flush=True);print('compiler stderr',file=sys.stderr);sys.exit(7)"],
                temp, {'PATH': '/usr/bin:/bin'}, log, timeout=10, reserve=0)
            self.assertEqual(result['returncode'], 7)
            self.assertIsNone(result['stopped'])
            self.assertEqual(log.read_text(), output.getvalue())
            self.assertIn('compiler stdout', output.getvalue()); self.assertIn('compiler stderr', output.getvalue())

    def test_reserve_and_timeout_stop_the_compiler_group(self):
        for timeout, free, reason in [(10, lambda: 0, 'filesystem-reserve'), (.05, lambda: 100, 'timeout')]:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temp:
                result = yogi_compile.stream([sys.executable, '-c', 'import time;time.sleep(60)'], temp,
                    {'PATH': '/usr/bin:/bin'}, Path(temp)/'compile.log', timeout=timeout, reserve=1,
                    sample=.02, free=free)
                self.assertEqual(result['stopped'], reason)
                self.assertNotEqual(result['returncode'], 0)

    def test_failed_parent_cannot_leave_a_child_holding_the_log_pipe(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            command = ('import subprocess,sys; subprocess.Popen([sys.executable,"-c",'
                       '"import time; time.sleep(60)"]); print("parent failed",flush=True); sys.exit(7)')
            result = yogi_compile.stream([sys.executable, '-c', command], temp, {'PATH': '/usr/bin:/bin'},
                                        Path(temp)/'compile.log', timeout=15, reserve=0, sample=.05)
            self.assertEqual(result['returncode'], 7)
            self.assertIsNone(result['stopped'])


class QueueTest(unittest.TestCase):
    def test_started_job_never_cancels_or_restarts_native_work(self):
        requests = []
        def opened(request, **kwargs):
            requests.append(request)
            return io.BytesIO(json.dumps({'total_count': 1, 'jobs': [{'name': 'full', 'status': 'in_progress'}]}).encode())
        full.queue_watch(dict(ENV, GH_TOKEN='synthetic'), opener=opened)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].get_method(), 'GET')

    def test_queue_timeout_cancels_exact_same_run_once(self):
        requests = []
        def opened(request, **kwargs):
            requests.append(request)
            return io.BytesIO(json.dumps({'total_count': 1, 'jobs': [{'name': 'full', 'status': 'queued'}]}).encode())
        with self.assertRaisesRegex(yogi.Refused, 'cancellation requested once'):
            full.queue_watch(dict(ENV, GH_TOKEN='synthetic'), opener=opened, clock=iter([0, 301]).__next__)
        self.assertEqual([r.get_method() for r in requests], ['GET', 'POST'])
        self.assertEqual(requests[1].full_url, 'https://api.github.com/repos/Immortal-Protocols/RoseOS-1/actions/runs/123/cancel')

    def test_denied_metadata_is_not_retried_and_does_not_trigger_cancellation(self):
        opened = mock.Mock(side_effect=urllib.error.HTTPError('https://api.github.com/example', 403, 'Forbidden', {}, None))
        with self.assertRaises(urllib.error.HTTPError):
            full.queue_watch(dict(ENV, GH_TOKEN='synthetic'), opener=opened)
        self.assertEqual(opened.call_count, 1)


if __name__ == '__main__':
    unittest.main()
