"""Offline Yogi OTA route checks. Synthetic files, fake clients and fake gcloud only: no builder, key, network or phone."""
import base64
import copy
import datetime
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.parse
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yogi_ota as yogi

ORIGINAL_PROFILE_PROBLEM = yogi.profile_problem

BUILD, EPOCH, SHA = '2026110601', '1793900000', 'a'*40
BOOT_BUILD, BOOT_EPOCH = '2026110501', '1793850000'
CERT = b'synthetic Yogi release certificate DER'
PINS = {'otaCertificateSha256': hashlib.sha256(CERT).hexdigest(), 'avbPublicKeySha256': 'c'*64}
UTC = datetime.timezone.utc


def receipt(**changes):
    value = {'schema': 'roseos.signing-bootstrap-receipt.v1', 'profile': 'yogi-v1', 'device': 'yogi',
             'project': 'protocols-465723', 'reasons': [], 'cleanup': {'workspaceRemoved': True},
             'status': 'complete', 'recoveryNeeded': False, 'publicIdentity': dict(PINS),
             'secrets': {role: {'name': name, 'state': 'committed', 'committedVersion': '1'}
                         for role, name in yogi.SECRETS.items()}}
    value.update(changes)
    return value


def complete_evidence():
    """The committed receipt values plus synthetic stand-ins for every still-missing field (test only)."""
    evidence = copy.deepcopy(yogi.config()['builder']['evidence'])
    evidence['runner'].update(labels=['self-hosted', 'Linux', 'X64', yogi.RUNNER_LABEL],
                              organizationEligibility='synthetic: no other eligible runner carries the route label')
    evidence['storage']['mounts'] = {'guestDevice': '/dev/synthetic', 'filesystemUuid': 'synthetic', 'mountPoint': '/'}
    return evidence


class Fixture(unittest.TestCase):
    """A ready synthetic configuration whose bootstrap receipt lives in a temporary repository copy."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = copy.deepcopy(yogi.config())
        self.receipt = self.root/'yogi-bootstrap-receipt.json'
        self.receipt.write_text(json.dumps(receipt()))
        self.cfg['signing']['bootstrapReceipt'] = str(self.receipt)  # absolute: ROOT/abs == abs
        self.cfg['builder'].update(status='ready', attempts=str(self.root/'attempts'), evidence=complete_evidence())
        (self.root/'attempts').mkdir()
        self.record = self.root/'frankel-2026110403.json'
        self.record_value = {'schema': 'roseos.app-inputs-record.v1', 'signerSha256': self.cfg['appInputs']['appSignerSha256'],
            'sdk': 37, 'apps': [{'package': 'dev.pixel.voice', 'versionCode': 364},
                                {'package': 'dev.pixel.calculator.agent', 'versionCode': 5},
                                {'package': 'dev.pixel.backgroundcontrol', 'versionCode': 38}],
            'export': {'run': '41960000001', 'attempt': '1', 'receiptSha256': 'e'*64,
                       'artifact': {'name': 'app-inputs-frankel-2026110403-41960000001-1', 'id': 13400000001,
                                    'digest': 'sha256:' + 'd'*64}}}
        self.record.write_text(json.dumps(self.record_value))
        self.cfg['appInputs'].update(record=str(self.record))
        verifier = self.root/'app-inputs-verify.py'
        verifier.write_text('# synthetic stand-in for Release\'s verifier\n')
        bound = {'profiles': {'yogi-v1': {'device': 'yogi', 'status': 'resource-bound', 'missing': [], 'project': 'protocols-465723',
                                          'secrets': dict(yogi.SECRETS)}}}
        self.profiles = bound
        bound['profiles']['yogi-v1'].update(accessEvidence='os/signing-bootstrap/yogi-access-binding.json',
                                            access={'addVersionExpires': '2026-11-06T06:00:00Z', 'minimumRemainingSeconds': 1800})
        original, original_access = yogi.profile_problem, yogi.key_access_problem
        profiles = mock.patch.object(yogi, 'profile_problem', lambda profiles=None: original(self.profiles))
        profiles.start()
        self.addCleanup(profiles.stop)
        access = mock.patch.object(yogi, 'key_access_problem',
                                   lambda profiles=None, now=None: original_access(self.profiles, now or self.now))
        access.start()
        self.addCleanup(access.stop)
        patcher = mock.patch.object(yogi, 'VERIFIER', verifier)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cfg['signing'].update(status='approved', access={'approvalRef': 'synthetic', 'expiresAt': '2026-11-06T12:00:00Z'})
        self.cfg['bootstrap'].update(status='signed', build=BOOT_BUILD, timestamp=BOOT_EPOCH)
        self.cfg['publicationEnabled'] = True
        self.now = datetime.datetime(2026, 11, 6, 3, 0, tzinfo=UTC)


class ContractTest(unittest.TestCase):
    def test_committed_contract_is_yogi_only_and_inactive(self):
        cfg = yogi.config()
        self.assertFalse(cfg['publicationEnabled'])
        self.assertFalse(cfg['automationEnabled'])
        self.assertEqual((cfg['builder']['project'], cfg['builder']['zone'], cfg['builder']['instance'],
                          cfg['builder']['instanceId']),
                         ('protocols-465723', 'us-west2-b', 'roseos-yogi-builder-20261105-b', '2720796557371812468'))
        self.assertEqual(cfg['builder']['status'], 'runner-unregistered')
        self.assertEqual(cfg['signing']['secrets'], {'keys': 'PIXEL_OS_SIGNING_KEYS_YOGI_V1',
                                                     'passphrase': 'PIXEL_OS_SIGNING_PASSPHRASE_YOGI_V1'})
        lab = cfg['labBaseline']
        self.assertFalse(lab['artifactPackagesUpdater'])
        self.assertFalse(lab['installedBuildVerified'])  # unknowns stay recorded as unknown, not as impossibility
        self.assertFalse(lab['installedOtaTrustInspected'])
        self.assertIn('Proposed supported design', lab['reason'])
        self.assertIsNone(cfg['bootstrap']['build'])
        self.assertFalse((yogi.ROOT/cfg['signing']['bootstrapReceipt']).exists(),
                         'no Yogi pins may be claimed before the authorized bootstrap')

    def test_committed_contract_blocks_every_stage_by_name(self):
        cfg = yogi.config()
        found = {stage: yogi.blockers(cfg, stage, access=True) for stage in yogi.STAGES}
        joined = ' | '.join(found['bootstrap'])
        for expected in ('builder runner/controller not ready', 'signed app inputs are not bound',
                         'Yogi bootstrap receipt absent', 'signing read access is not approved'):
            self.assertIn(expected, joined)
        self.assertIn('Yogi bootstrap build A is not signed yet (bootstrap.status)', found['ota'])
        self.assertEqual(found['publish'], ['Yogi publication is not enabled (publicationEnabled)'])
        for stage in yogi.STAGES:
            with self.assertRaises(yogi.Refused):
                yogi.guard(cfg, stage, access=True)

    def test_foreign_builder_keys_feed_or_route_refuse(self):
        base = yogi.read(yogi.CONFIG)
        cases = [('builder', 'instance', 'immortal-os-builder'), ('builder', 'runnerLabel', 'roseos-frankel-builder'),
                 ('builder', 'lock', '/home/pixelbuild/rose.lock'),
                 ('signing', 'secrets', {'keys': 'PIXEL_OS_SIGNING_KEYS_MUSTANG_V1',
                                         'passphrase': 'PIXEL_OS_SIGNING_PASSPHRASE_MUSTANG_V1'}),
                 ('wire', 'tag', 'mustang-{build}'), ('wire', 'latest', True)]
        for section, key, value in cases:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temp:
                bad = copy.deepcopy(base)
                bad[section][key] = value
                path = Path(temp)/'yogi.json'
                path.write_text(json.dumps(bad))
                with self.assertRaises(yogi.Refused):
                    yogi.config(path)
        route = yogi.read(yogi.ROUTE)
        for key, value in [('updaterUrl', 'https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/'),
                           ('releaseIndex', 'https://api.github.com/repos/Other/repo/releases'),
                           ('device', 'mustang'), ('publishedChannel', 'prod')]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temp:
                path = Path(temp)/'route.json'
                path.write_text(json.dumps(dict(route, **{key: value})))
                with self.assertRaises(yogi.Refused):
                    yogi.check_route(path)

    def test_reconcile_template_carries_owned_updater_and_no_agent_tray(self):
        rendered = yogi.render_reconcile(yogi.config(), '/srv/rose-yogi/attempts/yogi-1/src', '/r', '4619827259835644672')
        args = rendered['host']['integrate']['reconcile']['applyArgs']
        self.assertNotIn('--agent-tray', args)
        self.assertNotIn('--keep-upstream-updater', args)
        self.assertEqual(args[args.index('--updater-url')+1], 'https://github.com/Immortal-Protocols/pixel-ota/releases/download/')
        self.assertEqual(args[args.index('--updater-release-index')+1],
                         'https://api.github.com/repos/Immortal-Protocols/pixel-ota/releases')
        self.assertEqual(args[args.index('--reference-display-address')+1], '4619827259835644672')
        self.assertIn('{policy}', args)
        self.assertIn('{signed_apps}/dev.pixel.voice.apk', args)
        self.assertEqual(rendered['host']['integrate']['reconcile']['receiptDir'], '/r')

    def test_workflow_is_manual_separate_queue_and_dedicated_runner(self):
        text = (yogi.ROOT/'.github/workflows/yogi-ota.yml').read_text()
        self.assertIn('workflow_dispatch:', text)
        for forbidden in ('schedule:', 'workflow_run:', 'push:', 'roseos-frankel-builder', 'os-release-frankel',
                          'make_latest: true', 'MUSTANG_V1'):
            self.assertNotIn(forbidden, text)
        self.assertIn('group: yogi-ota', text)
        self.assertEqual(text.count('runs-on: [self-hosted, roseos-yogi-builder-20261105-b]'), 4)
        self.assertNotIn('runs-on: [self-hosted, roseos-yogi-builder]', text)


class KeyBootstrapTest(Fixture):
    def setUp(self):
        super().setUp()
        self.receipt.unlink()  # before the one-time key bootstrap no Yogi identity exists

    def test_ready_key_bootstrap_needs_runner_and_resource_bound_yogi_profile_only(self):
        self.assertEqual(yogi.blockers(self.cfg, 'key-bootstrap'), [])
        self.cfg['builder']['status'] = 'runner-unregistered'
        self.assertTrue(any('builder runner' in b for b in yogi.blockers(self.cfg, 'key-bootstrap')))

    def test_existing_identity_unbound_or_foreign_profile_refuses(self):
        self.receipt.write_text(json.dumps(receipt()))
        self.assertIn('a Yogi bootstrap receipt is already committed: the key bootstrap is one-time',
                      yogi.blockers(self.cfg, 'key-bootstrap'))
        self.receipt.unlink()
        bound = self.profiles['profiles']['yogi-v1']
        for profile in ({}, {'yogi-v1': dict(bound, status='unbound')}, {'yogi-v1': dict(bound, device='mustang')}):
            with self.subTest(profile=sorted(profile)):
                self.profiles = {'profiles': profile}
                self.assertTrue(any('signing profile' in b for b in yogi.blockers(self.cfg, 'key-bootstrap')))

    def test_unbound_or_expiring_bootstrap_access_refuses(self):
        bound = self.profiles['profiles']['yogi-v1']
        for change in ({'accessEvidence': None}, {'access': {'addVersionExpires': '2026-11-06T03:20:00Z',
                                                             'minimumRemainingSeconds': 1800}}):
            with self.subTest(change=sorted(change)):
                self.profiles = {'profiles': {'yogi-v1': dict(bound, **change)}}
                self.assertTrue(any('bootstrap access' in b for b in yogi.blockers(self.cfg, 'key-bootstrap')))
        self.profiles = {'profiles': {'yogi-v1': dict(bound, access={'liveCheck': True})}}  # live check only: allowed
        self.assertEqual(yogi.blockers(self.cfg, 'key-bootstrap'), [])

    def test_signing_bootstrap_cli_takes_lock_on_prove_and_run_only(self):
        """The workflow never calls a flag the committed script lacks: --lock parses for prove and run (an unsafe path
        is refused before any work), and preflight has no --lock."""
        bootstrap = yogi.module('yogi_test_signing_bootstrap', 'signing-bootstrap.py')
        common = ['--profile', 'yogi-v1', '--recipe-sha', 'a'*40]
        with mock.patch.object(bootstrap.factory_signing, 'stop_on_signals', lambda handler: None), \
                mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(bootstrap.main(['prove', *common, '--proof-dir', str(self.root/'p'), '--lock', 'relative.lock'], env={}), 2)
            self.assertEqual(bootstrap.main(['run', *common, '--state-dir', str(self.root/'s'), '--receipt-dir',
                                             str(self.root/'r'), '--lock', '/srv/rose-yogi/../x.lock'], env={}), 2)
            with mock.patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
                bootstrap.main(['preflight', *common, '--lock', '/srv/rose-yogi/yogi-build.lock'], env={})
        self.assertIn('--lock must be an absolute path', out.getvalue())
        self.assertFalse((self.root/'p').exists() or (self.root/'s').exists() or (self.root/'r').exists())

    def test_committed_tree_refuses_key_bootstrap(self):
        found = yogi.blockers(yogi.config(), 'key-bootstrap')
        self.assertTrue(found)
        self.assertTrue(any('builder runner' in b for b in found))

    def test_workflow_stage_order_lock_and_public_receipt_only(self):
        text = (yogi.ROOT/'.github/workflows/yogi-ota.yml').read_text()
        job = text[text.index('  key-bootstrap:\n'):text.index('  build:\n')]
        self.assertIn("if: inputs.stage == 'key-bootstrap'", job)
        self.assertIn('runs-on: [self-hosted, roseos-yogi-builder-20261105-b]', job)
        order = ['yogi_ota.py guard --stage key-bootstrap', 'signing-bootstrap.py preflight --profile yogi-v1',
                 'signing-bootstrap.py prove --profile yogi-v1', 'actions-gcp-login.py login',
                 'signing-bootstrap.py run --profile yogi-v1', 'actions-gcp-login.py logout', 'upload-artifact']
        positions = [job.index(step) for step in order]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(job.count('--lock "$YOGI_LOCK"'), 2)
        self.assertIn('YOGI_LOCK: /srv/rose-yogi/yogi-build.lock', job)
        self.assertEqual(yogi.config()['builder']['lock'], '/srv/rose-yogi/yogi-build.lock')
        self.assertIn(f"--state-dir {yogi.config()['builder']['bootstrapState']} ", job)  # no drift from yogi.json
        self.assertEqual(job.count('path: '), 1)
        self.assertIn('path: ${{ runner.temp }}/yogi-bootstrap/signing-bootstrap-receipt.json', job)
        for forbidden in ('roseos-frankel-builder', 'pro-mustang-v1', 'MUSTANG', 'secrets.'):
            self.assertNotIn(forbidden, job)
        build = text[text.index('  build:\n'):text.index('  sign:\n')]
        self.assertIn("if: inputs.stage == 'bootstrap' || inputs.stage == 'ota'", build)
        self.assertIn('needs: [controller, readiness, key-bootstrap, build, sign, publisher]', text)


class ProviderTest(unittest.TestCase):
    """Hosted controller/cleanup use the Yogi controller provider; signer jobs keep the signer provider."""
    def jobs(self):
        text = (yogi.ROOT/'.github/workflows/yogi-ota.yml').read_text()
        body = text[text.index('\njobs:\n'):]
        names = [n for n in ('guard', 'controller', 'key-bootstrap', 'build', 'sign', 'publisher', 'cleanup')]
        starts = sorted((body.index(f'\n  {n}:\n'), n) for n in names)
        return {n: body[i:(starts[k+1][0] if k+1 < len(starts) else len(body))] for k, (i, n) in enumerate(starts)}

    def test_controller_and_cleanup_use_the_yogi_controller_provider_and_fail_closed_first(self):
        jobs = self.jobs()
        for name in ('controller', 'cleanup'):
            job = jobs[name]
            self.assertIn('runs-on: ubuntu-24.04', job)
            self.assertIn('GCP_WORKLOAD_IDENTITY_PROVIDER: ${{ vars.YOGI_CONTROLLER_WORKLOAD_IDENTITY_PROVIDER }}', job)
            self.assertIn('YOGI_CONTROLLER_SERVICE_ACCOUNT: ${{ vars.YOGI_CONTROLLER_SERVICE_ACCOUNT }}', job)
            self.assertNotIn('vars.GCP_WORKLOAD_IDENTITY_PROVIDER', job)
            check, login = job.index('[[ -n "$GCP_WORKLOAD_IDENTITY_PROVIDER" ]]'), job.index('actions-gcp-login.py login')
            self.assertLess(check, login)
            self.assertLess(job.index('[[ -n "$YOGI_CONTROLLER_SERVICE_ACCOUNT" ]]'), login)

    def test_signer_jobs_keep_the_signer_provider(self):
        jobs = self.jobs()
        for name in ('key-bootstrap', 'sign'):
            self.assertIn('GCP_WORKLOAD_IDENTITY_PROVIDER: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}', jobs[name])
            self.assertIn('GCP_SIGNER_SERVICE_ACCOUNT: ${{ vars.GCP_SIGNER_SERVICE_ACCOUNT }}', jobs[name])
            self.assertNotIn('YOGI_CONTROLLER', jobs[name])
        for name in ('guard', 'build', 'publisher'):
            self.assertNotIn('WORKLOAD_IDENTITY_PROVIDER', jobs[name])


class IdentityTest(Fixture):
    def test_ready_fixture_passes_every_guard(self):
        for stage in ('bootstrap', 'ota', 'publish'):
            self.assertEqual(yogi.blockers(self.cfg, stage, access=True, now=self.now), [], stage)
        # once the identity exists the one-time key bootstrap refuses
        self.assertEqual(yogi.blockers(self.cfg, 'key-bootstrap'),
                         ['a Yogi bootstrap receipt is already committed: the key bootstrap is one-time'])
        self.assertEqual(yogi.identities(self.cfg), (PINS, {'keys': '1', 'passphrase': '1'}))

    def test_lab_foreign_or_incomplete_bootstrap_receipts_refuse(self):
        cases = [receipt(publicIdentity=dict(PINS, avbPublicKeySha256=next(iter(sorted(yogi.LAB_PINS))))),
                 receipt(publicIdentity=dict(PINS, avbPublicKeySha256='7728e30f50bfa5cea165f473175a08803f6a8346642b5aa10913e9d9e6defef6')),
                 receipt(profile='pro-mustang-v1'), receipt(device='mustang'), receipt(status='recovery-needed'),
                 receipt(secrets={'keys': {'name': 'PIXEL_OS_SIGNING_KEYS_MUSTANG_V1', 'state': 'committed', 'committedVersion': '1'},
                                  'passphrase': {'name': yogi.SECRETS['passphrase'], 'state': 'committed', 'committedVersion': '1'}}),
                 receipt(publicIdentity={'otaCertificateSha256': 'short'})]
        for value in cases:
            with self.subTest(value=value.get('profile')):
                self.receipt.write_text(json.dumps(value))
                with self.assertRaises(yogi.Refused):
                    yogi.identities(self.cfg)

    def test_bootstrap_receipt_project_reasons_and_cleanup_are_required(self):
        for change in (dict(project='other-project'), dict(reasons=['partial import']),
                       dict(cleanup={'workspaceRemoved': False}), dict(cleanup=None)):
            with self.subTest(change=sorted(change)):
                self.receipt.write_text(json.dumps(receipt(**change)))
                with self.assertRaises(yogi.Refused):
                    yogi.identities(self.cfg)

    def test_unbound_or_foreign_signing_profile_blocks_before_pins_or_login(self):
        bound = self.profiles['profiles']['yogi-v1']
        cases = [{}, {'yogi-v1': dict(bound, status='unbound')}, {'yogi-v1': dict(bound, missing=['containers'])},
                 {'yogi-v1': dict(bound, project='other')},
                 {'yogi-v1': dict(bound, secrets={'keys': 'PIXEL_OS_SIGNING_KEYS_MUSTANG_V1',
                                                  'passphrase': 'PIXEL_OS_SIGNING_PASSPHRASE_MUSTANG_V1'})}]
        for profiles in cases:
            with self.subTest(profiles=sorted(profiles)):
                self.profiles = {'profiles': profiles}
                self.assertTrue(any('signing profile' in b for b in yogi.blockers(self.cfg, 'bootstrap', access=True, now=self.now)))
                with self.assertRaisesRegex(yogi.Refused, 'signing profile'):
                    yogi.sign(self.cfg, BUILD, 'bootstrap', env={'GCP_SIGNER_SERVICE_ACCOUNT': yogi.SIGNER})
        self.profiles = {'profiles': {'yogi-v1': bound}}
        self.assertEqual(yogi.blockers(self.cfg, 'bootstrap', access=True, now=self.now), [])

    def test_committed_tree_has_no_yogi_profile_yet(self):
        self.assertIsNotNone(ORIGINAL_PROFILE_PROBLEM())  # the real committed profiles: absent or unbound both refuse

    def test_signing_window_must_be_approved_and_current(self):
        self.cfg['signing']['access']['expiresAt'] = '2026-11-06T03:29:59Z'
        self.assertIn('less than 30 minutes', yogi.access_problem(self.cfg, self.now))
        self.cfg['signing']['status'] = 'unbound'
        self.assertIn('not approved', yogi.access_problem(self.cfg, self.now))

    def test_ota_needs_a_signed_bootstrap_and_publish_needs_enablement(self):
        self.cfg['bootstrap'].update(status='not-built', build=None)
        self.assertIn('Yogi bootstrap build A is not signed yet (bootstrap.status)', yogi.blockers(self.cfg, 'ota'))
        self.assertEqual(yogi.blockers(self.cfg, 'bootstrap', access=True, now=self.now), [])
        self.cfg['publicationEnabled'] = False
        self.assertTrue(yogi.blockers(self.cfg, 'publish'))


class AppInputTest(Fixture):
    SIGNER = '650d47f91b7f5af3c530fef53fe92018aa4521efba065bcb1d6fc9b699060bfb'

    def setUp(self):
        super().setUp()
        self.apps = self.root/'signed-apps'
        self.apps.mkdir()
        self.versions = {'dev.pixel.voice': 364, 'dev.pixel.calculator.agent': 5, 'dev.pixel.backgroundcontrol': 38}
        for package in self.versions:
            apk = self.apps/f'{package}.apk'
            apk.write_bytes(package.encode())
            (self.apps/f'{package}.receipt.json').write_text(json.dumps({'schemaVersion': 1, 'apk': apk.name,
                'sha256': yogi.digest(apk), 'certificateSha256': [self.SIGNER], 'signatureVerified': True}))
        row = lambda p: {'packageName': p, 'versionCode': self.versions[p], 'apkSha256': yogi.digest(self.apps/f'{p}.apk'),
                         'certificates': [self.SIGNER.upper()]}
        self.policy = self.root/'background-policy.json'
        self.policy.write_text(json.dumps({'schemaVersion': 1, 'sdk': 37, 'prototypeControl': True,
            'agent': row('dev.pixel.backgroundcontrol'), 'voice': row('dev.pixel.voice'),
            'targets': [row('dev.pixel.calculator.agent')]}))

    def identity(self, apk, aapt, apksigner, sdk):
        package = Path(apk).name[:-4]
        return {'packageName': package, 'versionCode': self.versions[package], 'certificates': [self.SIGNER.upper()]}

    def admit(self):
        return yogi.admit_apps(self.cfg, self.apps, self.policy, {'aapt': 'aapt', 'apksigner': 'apksigner'}, self.identity)

    def test_fleet_signed_bound_versions_and_matching_policy_admit(self):
        result = self.admit()
        self.assertEqual(sorted((a['package'], a['versionCode']) for a in result['apps']),
                         sorted(self.versions.items()))

    def test_other_signer_version_or_policy_refuses(self):
        self.versions['dev.pixel.voice'] = 311  # the lab's unallocated version
        with self.assertRaises(yogi.Refused):
            self.admit()
        self.versions['dev.pixel.voice'] = 364
        receipt = self.apps/'dev.pixel.voice.receipt.json'
        value = json.loads(receipt.read_text())
        receipt.write_text(json.dumps(dict(value, certificateSha256=['a40da80a59d170caa950cf15c18c454d47a39b26989d8b640ecd745ba71bf5dc'])))
        with self.assertRaisesRegex(yogi.Refused, 'signer'):
            self.admit()
        receipt.write_text(json.dumps(value))
        (self.apps/'dev.pixel.calculator.agent.apk').write_bytes(b'changed')
        with self.assertRaises(yogi.Refused):
            self.admit()


class RecordTest(Fixture):
    """Release's committed roseos.app-inputs-record.v1 (PR #565) is the one app-input record; its verifier admits bytes."""
    def test_committed_record_binds_and_verifier_is_called_with_yogi_tools(self):
        self.assertEqual(yogi.app_record(self.cfg)['export']['artifact']['id'], 13400000001)
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout='{"verdict": "ADMITTED"}', stderr='')
        result = yogi.verify_export(self.cfg, self.root/'download', {'aapt': '/t/aapt', 'apksigner': '/t/apksigner'}, run)
        self.assertEqual(result['verifier']['verdict'], 'ADMITTED')
        self.assertEqual(calls[0][1:], [str(yogi.VERIFIER), 'verify', '--record', str(self.record), '--dir',
                                        str(self.root/'download'), '--aapt', '/t/aapt', '--apksigner', '/t/apksigner'])
        refuse = lambda argv, **kwargs: SimpleNamespace(returncode=1, stdout='', stderr='inventory differs')
        with self.assertRaisesRegex(yogi.Refused, 'verifier refused'):
            yogi.verify_export(self.cfg, self.root/'download', {'aapt': 'a', 'apksigner': 'b'}, refuse)

    def test_missing_foreign_or_incomplete_records_block(self):
        cases = [dict(signerSha256='a40da80a59d170caa950cf15c18c454d47a39b26989d8b640ecd745ba71bf5dc'), dict(sdk=36),
                 dict(schema='roseos.latest-allocation.v1'), dict(apps=self.record_value['apps'][:2]),
                 dict(export=dict(self.record_value['export'], run=None))]
        for change in cases:
            with self.subTest(change=sorted(change)):
                self.record.write_text(json.dumps(dict(self.record_value, **change)))
                self.assertTrue(any('signed app inputs are not bound' in b for b in yogi.blockers(self.cfg, 'bootstrap')))
        self.record.unlink()
        self.assertTrue(any('not committed' in b for b in yogi.blockers(self.cfg, 'ota')))

    def test_committed_contract_points_at_the_release_record(self):
        self.assertEqual(yogi.config()['appInputs']['record'], 'os/app-inputs/frankel-2026110403.json')
        self.assertNotIn('status', yogi.config()['appInputs'])  # bound only by Release's committed record


class AllocationTest(Fixture):
    def test_strictly_newer_than_floor_bootstrap_and_attempts(self):
        record = yogi.allocate(self.cfg, '7', '1', SHA, self.now)
        self.assertEqual(record['build'], '2026110601')
        self.assertGreater(int(record['timestamp']), int(BOOT_EPOCH))
        second = yogi.allocate(self.cfg, '8', '1', SHA, self.now)
        self.assertEqual(second['build'], '2026110602')
        self.assertGreater(int(second['timestamp']), int(record['timestamp']))
        self.assertTrue((self.root/'attempts/yogi-2026110602/allocation.json').is_file())

    def test_clock_behind_recorded_builds_or_exhausted_day_refuses(self):
        self.cfg['bootstrap'].update(build='2026110799')
        with self.assertRaisesRegex(yogi.Refused, 'no build number left'):
            yogi.allocate(self.cfg, '7', '1', SHA, self.now)
        self.cfg['bootstrap'].update(build='2026110699')
        with self.assertRaisesRegex(yogi.Refused, 'no build number left'):
            yogi.allocate(self.cfg, '7', '1', SHA, self.now)

    def test_existing_attempt_is_never_reused(self):
        (self.root/'attempts/yogi-2026110601').mkdir()
        record = yogi.allocate(self.cfg, '7', '1', SHA, self.now)
        self.assertEqual(record['build'], '2026110602')
        with self.assertRaises(FileExistsError):
            (self.root/'attempts/yogi-2026110602').mkdir(mode=0o700)


class PrepareTest(Fixture):
    def test_copy_skips_output_trees_and_never_writes_the_base(self):
        base = self.root/'base'
        for name in ('build', 'out', 'out-old', 'out_yogi', 'outlier', '.repo', 'releases'):
            (base/name).mkdir(parents=True)
            (base/name/'file').write_text(name)
        yogi.copy_tree(base, self.root/'copy')
        self.assertEqual(sorted(p.name for p in (self.root/'copy').iterdir()), ['.repo', 'build', 'outlier'])
        self.assertEqual((base/'out/file').read_text(), 'out')

    def test_lock_held_by_another_attempt_refuses(self):
        lock = self.root/'yogi-build.lock'
        lock.write_text('')
        self.cfg['builder']['lock'] = str(lock)
        with yogi.builder_lock(self.cfg):
            with self.assertRaisesRegex(yogi.Refused, 'lock is held'):
                yogi.builder_lock(self.cfg)

    def test_reconcile_conflict_parsing(self):
        stderr = json.dumps({'verdict': 'FAIL', 'error': 'owner changes conflict with the candidate (nothing written): '
                             'frameworks/base/core/res/res/values/config.xml, build/make/target/product/handheld_product.mk'})
        self.assertEqual(yogi.conflicts_of('noise\n' + stderr),
                         ['build/make/target/product/handheld_product.mk', 'frameworks/base/core/res/res/values/config.xml'])
        self.assertIsNone(yogi.conflicts_of(json.dumps({'verdict': 'FAIL', 'error': 'git apply failed'})))
        allowed = yogi.read(yogi.TEMPLATE)['labAdoption']['paths']
        self.assertTrue(all(p.startswith(('frameworks/base/', 'build/make/')) for p in allowed))


def target_files(path, device='yogi', build=BUILD, epoch=EPOCH, trust=(CERT,), support=True, classes=True):
    store = io.BytesIO()
    with zipfile.ZipFile(store, 'w') as z:
        for i, der in enumerate(trust):
            z.writestr(f'cert{i}.x509.pem', b'-----BEGIN CERTIFICATE-----\n'+base64.b64encode(der)+b'\n-----END CERTIFICATE-----\n')
    apk = io.BytesIO()
    with zipfile.ZipFile(apk, 'w') as z:
        z.writestr('classes.dex', b'Lapp/seamlessupdate/client/RoseChannels; Lapp/seamlessupdate/client/RoseReleases;'
                   if classes else b'Lapp/seamlessupdate/client/Service;')
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('VENDOR/build.prop', f'ro.product.vendor.device={device}\nro.vendor.build.version.incremental={build}\n'
                   f'ro.vendor.build.date.utc={epoch}\n')
        z.writestr('SYSTEM/priv-app/Updater/Updater.apk', apk.getvalue())
        z.writestr('SYSTEM/etc/security/otacerts.zip', store.getvalue())
        if support:
            for kind in ('permissions', 'default-permissions', 'sysconfig'):
                z.writestr(f'SYSTEM/etc/{kind}/app.seamlessupdate.client.xml', '<config/>')


class ObserverTest(Fixture):
    def setUp(self):
        super().setUp()
        route = yogi.check_route()
        self.resources = {'url': route['updaterUrl'], 'release_index': route['releaseIndex'], 'channel_default': 'prod',
                          'channel_published': ['stable'], 'channel_values': ['prod', 'stable', 'alpha']}
        self.signer = PINS['otaCertificateSha256']
        self.path = self.root/'target.zip'
        target_files(self.path)

    def execute(self, argv, **kwargs):
        if argv[0] == 'apksigner':
            return SimpleNamespace(returncode=0, stdout='Signer #1 certificate SHA-256 digest: ' + self.signer)
        if 'badging' in argv:
            return SimpleNamespace(returncode=0, stdout="package: name='app.seamlessupdate.client'")
        lines = ['      config (default) flags=0x02 [offset16]:']
        for i, (name, value) in enumerate(self.resources.items()):
            if isinstance(value, list):
                lines += [f'        resource 0x7f0000{i:02x} app.seamlessupdate.client:array/{name}: <bag>',
                          f'          Parent=0x00000000(Resolved=0x00000000), Count={len(value)}']
                lines += [f'          #{j} (Key=0x01000001): (string8) "{v}"' for j, v in enumerate(value)]
            else:
                lines += [f'        resource 0x7f0000{i:02x} app.seamlessupdate.client:string/{name}: t=0x03 d=0x00000000',
                          f'          (string8) "{value}"']
        return SimpleNamespace(returncode=0, stdout='\n'.join(lines))

    def observe(self):
        return yogi.observe_target(self.path, BUILD, EPOCH, {'aapt': 'aapt', 'apksigner': 'apksigner'}, PINS, self.execute)

    def test_packaged_updater_route_signer_and_exact_trust(self):
        result = self.observe()
        self.assertTrue(result['packagedUpdaterVerified'])
        self.assertEqual(result['trustedOtaCertificateSha256'], [PINS['otaCertificateSha256']])
        self.assertEqual(result['updaterResources']['url'], 'https://github.com/Immortal-Protocols/pixel-ota/releases/download/')

    def test_lab_shaped_image_without_updater_or_with_other_trust_refuses(self):
        cases = [dict(support=False), dict(classes=False), dict(trust=(b'public test key',)),
                 dict(trust=(CERT, b'public test key')), dict(device='mustang'), dict(build=BOOT_BUILD)]
        for case in cases:
            with self.subTest(case=case):
                target_files(self.path, **case)
                with self.assertRaises(yogi.Refused):
                    self.observe()
        with zipfile.ZipFile(self.path, 'w') as z:
            z.writestr('VENDOR/build.prop', f'ro.product.vendor.device=yogi\nro.vendor.build.version.incremental={BUILD}\n'
                       f'ro.vendor.build.date.utc={EPOCH}\n')
        with self.assertRaises((yogi.Refused, ValueError)):  # the lab image: no Updater at all
            self.observe()

    def test_wrong_feed_channel_or_signer_refuses(self):
        target_files(self.path)
        for key, value in [('url', 'https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/'),
                           ('release_index', ''), ('channel_published', ['prod'])]:
            with self.subTest(key=key):
                old = self.resources[key]
                self.resources[key] = value
                with self.assertRaises(yogi.Refused):
                    self.observe()
                self.resources[key] = old
        self.signer = 'f'*64
        with self.assertRaisesRegex(yogi.Refused, 'signer'):
            self.observe()


class MetadataTest(Fixture):
    def write(self, **changes):
        meta = {'pre-device': 'yogi', 'post-build-incremental': BUILD, 'post-timestamp': EPOCH,
                'post-build': f'google/yogi/yogi:17/CD1A.260714.001.A9/{BUILD}:user/release-keys', 'ota-type': 'AB'}
        meta.update(changes)
        path = self.root/'ota.zip'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('META-INF/com/android/metadata', ''.join(f'{k}={v}\n' for k, v in meta.items()))
        return path

    def test_full_newer_yogi_ota(self):
        self.assertEqual(yogi.ota_metadata(self.write(), BUILD, EPOCH, self.cfg['bootstrap'])['pre-device'], 'yogi')

    def test_wrong_device_incremental_wipe_downgrade_or_not_newer_refuses(self):
        for key, value in [('pre-device', 'mustang'), ('ota-type', 'BLOCK'), ('ota-wipe', 'yes'), ('ota-downgrade', 'yes'),
                           ('pre-build', 'x'), ('post-timestamp', '1')]:
            with self.subTest(key=key), self.assertRaises(yogi.Refused):
                yogi.ota_metadata(self.write(**{key: value}), BUILD, EPOCH, self.cfg['bootstrap'])
        with self.assertRaisesRegex(yogi.Refused, 'newer than the Yogi bootstrap'):
            yogi.ota_metadata(self.write(), BUILD, EPOCH, {'build': BUILD, 'timestamp': EPOCH})


def staged(cfg, root):
    root.mkdir()
    name = f'yogi-ota_update-{BUILD}.zip'
    (root/name).write_bytes(b'synthetic OTA, not installable')
    (root/'yogi-stable').write_text(f'{BUILD} {EPOCH} yogi stable\n')
    value = {'schema': 'roseos.yogi-ota-stage.v1', 'device': 'yogi', 'build': BUILD, 'timestamp': EPOCH,
             'candidateSource': SHA, 'recipeSource': SHA, 'runId': '333', 'label': yogi.LABEL,
             'bootstrapBuild': cfg['bootstrap']['build'], 'publicIdentity': PINS, 'checks': dict(yogi.CHECKS),
             'ota': {'file': name, 'sizeBytes': (root/name).stat().st_size, 'sha256': yogi.digest(root/name)}}
    (root/'yogi-ota-receipt.json').write_text(json.dumps(value))
    (root/'SHA256SUMS').write_text(''.join(f'{yogi.digest(p)}  {p.name}\n' for p in sorted(root.iterdir())))
    return value


class Client:
    api, repository = 'https://api.github.com', yogi.REPOSITORY

    def __init__(self, fail=None):
        self.rows = [dict(id=1, tag_name='frankel-2026110403', draft=False, assets=[]),
                     dict(id=2, tag_name='mustang-2026110401', draft=False, assets=[])]
        self.before = copy.deepcopy(self.rows)
        self.log, self.blobs, self.fail = [], {}, fail

    def _request(self, *args):
        page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(args[1]).query)['page'][0])
        return 200, None, self.rows[(page-1)*100:page*100]

    def latest_tag(self):
        return 'frankel-2026110403'

    def create_draft(self, tag, name, body):
        self.log.append(('draft', tag))
        row = dict(id=3, tag_name=tag, draft=True, prerelease=False, assets=[])
        self.rows.append(row)
        return row

    def get_release(self, rid):
        return next(x for x in self.rows if x['id'] == rid)

    def upload(self, rid, path):
        self.log.append(('upload', path.name))
        aid = len(self.blobs)+10
        self.blobs[aid] = path.read_bytes()
        self.get_release(rid)['assets'].append(dict(id=aid, name=path.name, size=path.stat().st_size, state='uploaded'))

    def download(self, aid, path, size):
        self.log.append(('readback', path.name))
        path.write_bytes(b'corrupt' if self.fail == 'readback:'+path.name else self.blobs[aid])

    def finalize_public(self, rid):
        self.log.append(('finalize', rid))
        row = self.get_release(rid)
        row['draft'] = False
        return row

    def public_download(self, url, path, size):
        self.log.append(('public', url))
        asset = next(a for a in self.rows[-1]['assets'] if a['name'] == url.rsplit('/', 1)[1])
        path.write_bytes(self.blobs[asset['id']])


class PublicationTest(Fixture):
    def setUp(self):
        super().setUp()
        self.assets = self.root/'assets'
        self.r = staged(self.cfg, self.assets)

    def test_fresh_yogi_release_ota_first_metadata_last_never_latest(self):
        client = Client()
        result = yogi.publish(client, self.assets, self.cfg, self.root/'out')
        self.assertEqual(result['status'], 'ota-published')
        self.assertEqual(result['otaUrl'], f'https://github.com/Immortal-Protocols/pixel-ota/releases/download/yogi-{BUILD}/'
                                           f'yogi-ota_update-{BUILD}.zip')
        self.assertEqual(client.rows[:2], client.before)
        self.assertLess(client.log.index(('readback', self.r['ota']['file'])), client.log.index(('upload', 'yogi-stable')))
        self.assertLess(client.log.index(('readback', 'yogi-stable')), client.log.index(('finalize', 3)))
        self.assertEqual(result['latestBefore'], result['latestAfter'])

    def test_metadata_readback_failure_keeps_unadvertised_draft(self):
        client = Client('readback:yogi-stable')
        with self.assertRaises(yogi.Refused):
            yogi.publish(client, self.assets, self.cfg, self.root/'out')
        self.assertNotIn(('finalize', 3), client.log)
        self.assertFalse(yogi.read(self.root/'out/publication.json')['public'])

    def test_existing_or_newer_yogi_tag_or_disabled_publication_stops_before_api(self):
        client = Client()
        client.rows.append(dict(tag_name='yogi-'+BUILD, draft=True))
        with self.assertRaisesRegex(yogi.Refused, 'already consumes'):
            yogi.publish(client, self.assets, self.cfg, self.root/'out1')
        self.assertEqual(client.log, [])
        self.cfg['publicationEnabled'] = False
        with self.assertRaisesRegex(yogi.Refused, 'publication is not enabled'):
            yogi.publish(Client(), self.assets, self.cfg, self.root/'out2')

    def test_foreign_unproven_or_changed_assets_refuse(self):
        original = json.dumps(self.r)
        for field, value in [('device', 'mustang'), ('checks', {}), ('publicIdentity', {}), ('bootstrapBuild', '1'),
                             ('label', 'accepted on phone')]:
            with self.subTest(field=field):
                (self.assets/'yogi-ota-receipt.json').write_text(json.dumps(dict(self.r, **{field: value})))
                with self.assertRaises(yogi.Refused):
                    yogi.verify_staged(self.assets, self.cfg)
        (self.assets/'yogi-ota-receipt.json').write_text(original)
        (self.assets/'yogi-stable').write_text(f'{BUILD} {EPOCH} mustang stable\n')
        with self.assertRaises(yogi.Refused):
            yogi.verify_staged(self.assets, self.cfg)
        (self.assets/'yogi-stable').write_text(f'{BUILD} {EPOCH} yogi stable\n')
        (self.assets/self.r['ota']['file']).write_bytes(b'changed')
        with self.assertRaisesRegex(yogi.Refused, 'checksum'):
            yogi.verify_staged(self.assets, self.cfg)


RUN_ENV = {'GITHUB_SHA': SHA, 'GITHUB_WORKFLOW_REF': yogi.WORKFLOW + '@refs/heads/main',
           'GITHUB_RUN_ID': '777', 'GITHUB_RUN_ATTEMPT': '1'}


class BuilderControlTest(Fixture):
    """Exact-tuple controller: project, zone, name and numeric ID on every describe; v2 ownership before START."""
    def gcloud(self, states, log, identity=None, start_rc=0, stop_rc=0):
        seen = dict(id=yogi.INSTANCE_ID, name=yogi.INSTANCE, zone='https://compute.googleapis.com/compute/v1/projects/'
                    'protocols-465723/zones/us-west2-b', selfLink='https://compute.googleapis.com/compute/v1/projects/'
                    'protocols-465723/zones/us-west2-b/instances/' + yogi.INSTANCE)
        seen.update(identity or {})

        def run(argv, **kwargs):
            log.append(argv[3:])
            self.assertEqual(argv[4], yogi.INSTANCE)
            self.assertIn('--project=protocols-465723', argv)
            self.assertIn('--zone=us-west2-b', argv)
            if argv[3] == 'describe':
                status = states.pop(0) if len(states) > 1 else states[0]
                return SimpleNamespace(returncode=0, stdout=json.dumps(dict(seen, status=status)))
            return SimpleNamespace(returncode=start_rc if argv[3] == 'start' else stop_rc, stdout='')
        return run

    def control(self, action, states, log, own, env=RUN_ENV, **kwargs):
        with mock.patch.object(yogi.subprocess, 'run', self.gcloud(states, log, **kwargs)):
            return yogi.control_builder(self.cfg, action, own, sleep=lambda s: None, env=env)

    def requests(self, log):
        return [entry[0] for entry in log if entry[0] in ('start', 'stop')]

    def test_owned_start_then_exact_cleanup(self):
        log, own = [], self.root/'own.json'
        self.assertEqual(self.control('start', ['TERMINATED', 'STAGING', 'RUNNING'], log, own), 'RUNNING')
        record = yogi.read(own)
        self.assertEqual(record['builder'], {'project': 'protocols-465723', 'zone': 'us-west2-b',
                                             'instance': yogi.INSTANCE, 'instanceId': yogi.INSTANCE_ID})
        self.assertEqual(record['run'], {'source': SHA, 'workflowRef': RUN_ENV['GITHUB_WORKFLOW_REF'],
                                         'runId': '777', 'runAttempt': '1'})
        self.assertTrue(record['startedByThisOperation'])
        log2 = []
        self.assertEqual(self.control('stop', ['RUNNING', 'STOPPING', 'TERMINATED'], log2, own), 'TERMINATED')
        self.assertEqual(self.requests(log2), ['stop'])
        self.assertTrue(all(e[1] == yogi.INSTANCE for e in log2))

    def test_record_is_durable_before_the_start_request(self):
        own, log = self.root/'own.json', []
        fake = self.gcloud(['TERMINATED', 'RUNNING'], log)
        def run(argv, **kwargs):
            if argv[3] == 'start':
                self.assertTrue(own.exists(), 'START sent before the ownership record existed')
            return fake(argv, **kwargs)
        with mock.patch.object(yogi.subprocess, 'run', run):
            yogi.control_builder(self.cfg, 'start', own, sleep=lambda s: None, env=RUN_ENV)

    def test_same_name_other_zone_or_wrong_numeric_id_or_project_is_never_controlled(self):
        cases = [{'zone': 'https://compute.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-a',
                  'selfLink': 'https://compute.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-a/'
                              'instances/' + yogi.INSTANCE},
                 {'id': '1111111111111111111'},
                 {'selfLink': 'https://compute.googleapis.com/compute/v1/projects/other-project/zones/us-west2-b/'
                              'instances/' + yogi.INSTANCE},
                 {'name': 'roseos-yogi-builder-20261103-a'}]
        for identity in cases:
            with self.subTest(identity=sorted(identity)):
                own, log = self.root/f'own-{len(identity)}-{sorted(identity)[0]}.json', []
                with self.assertRaisesRegex(yogi.Refused, 'identity mismatch'):
                    self.control('start', ['TERMINATED'], log, own, identity=identity)
                self.assertFalse(own.exists())
                self.assertEqual(self.requests(log), [])
        # a valid record of this run still never stops a mismatched VM (same name, other zone or ID)
        own = self.root/'own-valid.json'
        self.control('start', ['TERMINATED', 'RUNNING'], [], own)
        for identity in cases[:2]:
            log = []
            with self.assertRaisesRegex(yogi.Refused, 'identity mismatch'):
                self.control('stop', ['RUNNING'], log, own, identity=identity)
            self.assertEqual(self.requests(log), [])

    def test_mismatch_appearing_during_a_poll_stops_polling_without_requests(self):
        own, log, calls = self.root/'own.json', [], {'n': 0}
        good = self.gcloud(['TERMINATED', 'STAGING'], log)
        bad = self.gcloud(['RUNNING'], log, identity={'id': '1111111111111111111'})
        def run(argv, **kwargs):
            if argv[3] == 'describe':
                calls['n'] += 1
                return (good if calls['n'] <= 2 else bad)(argv, **kwargs)
            return good(argv, **kwargs)
        with mock.patch.object(yogi.subprocess, 'run', run), self.assertRaisesRegex(yogi.Refused, 'identity mismatch'):
            yogi.control_builder(self.cfg, 'start', own, sleep=lambda s: None, env=RUN_ENV)
        self.assertEqual(self.requests(log), ['start'])

    def test_already_running_unowned_host_is_never_adopted_or_stopped(self):
        log, own = [], self.root/'own.json'
        self.assertIn('not started by this run', self.control('start', ['RUNNING'], log, own))
        self.assertFalse(yogi.read(own)['startedByThisOperation'])
        self.assertEqual(self.control('stop', ['RUNNING'], log, own), 'not-started-by-this-run')
        self.assertEqual(self.requests(log), [])
        with self.assertRaises(FileExistsError):
            self.control('start', ['RUNNING'], log, own)

    def test_uncertain_start_is_not_retried_and_its_record_still_lets_cleanup_stop_it(self):
        log, own = [], self.root/'own.json'
        with self.assertRaisesRegex(yogi.Refused, 'uncertain'):
            self.control('start', ['TERMINATED'], log, own, start_rc=1)
        self.assertEqual(self.requests(log), ['start'])  # exactly one request, no retry
        self.assertTrue(yogi.read(own)['startedByThisOperation'])
        log2 = []
        self.assertEqual(self.control('stop', ['STAGING', 'RUNNING', 'STOPPING', 'TERMINATED'], log2, own), 'TERMINATED')
        self.assertEqual(self.requests(log2), ['stop'])
        log3 = []  # the uncertain START never took effect: nothing to stop, nothing requested
        self.assertEqual(self.control('stop', ['TERMINATED'], log3, own), 'TERMINATED')
        self.assertEqual(self.requests(log3), [])

    def test_missing_legacy_stale_foreign_or_tampered_records_never_stop(self):
        own = self.root/'own.json'
        self.control('start', ['TERMINATED', 'RUNNING'], [], own)
        good = yogi.read(own)
        self.assertEqual(self.control('stop', ['RUNNING'], [], self.root/'absent.json'), 'no-ownership-record (nothing stopped)')
        variants = [{'schema': 'roseos.yogi-builder-ownership.v1', 'instance': 'roseos-yogi-builder-20261103-a',
                     'startedByThisOperation': True},
                    dict(good, schema='roseos.yogi-builder-ownership.v1'),
                    dict(good, builder=dict(good['builder'], zone='us-west2-a')),
                    dict(good, builder=dict(good['builder'], instanceId='1')),
                    dict(good, builder=dict(good['builder'], instance='immortal-os-builder')),
                    dict(good, run=dict(good['run'], runId='776')),
                    dict(good, run=dict(good['run'], runAttempt='2')),
                    dict(good, run=dict(good['run'], source='b'*40)),
                    dict(good, run=dict(good['run'], workflowRef='Immortal-Protocols/RoseOS-1/.github/workflows/os-release.yml@refs/heads/main')),
                    dict(good, observedBeforeStart='RUNNING'),
                    {k: v for k, v in good.items() if k != 'run'}]
        for index, record in enumerate(variants):
            with self.subTest(index=index):
                path = self.root/f'bad-{index}.json'
                path.write_text(json.dumps(record))
                log = []
                with self.assertRaisesRegex(yogi.Refused, 'legacy, malformed, stale'):
                    self.control('stop', ['RUNNING'], log, path)
                self.assertEqual(self.requests(log), [])
        log = []  # same record, another run's cleanup
        with self.assertRaises(yogi.Refused):
            self.control('stop', ['RUNNING'], log, own, env=dict(RUN_ENV, GITHUB_RUN_ID='778'))
        self.assertEqual(self.requests(log), [])

    def test_controller_needs_this_runs_identity(self):
        for env in ({}, dict(RUN_ENV, GITHUB_WORKFLOW_REF='Immortal-Protocols/RoseOS-1/.github/workflows/os-release.yml@x'),
                    dict(RUN_ENV, GITHUB_SHA='short')):
            with self.subTest(env=sorted(env)), self.assertRaises(yogi.Refused):
                self.control('start', ['TERMINATED'], [], self.root/f'own-{len(env)}.json', env=env)

    def test_legacy_or_foreign_builder_configs_refuse(self):
        base = yogi.read(yogi.CONFIG)
        for changes in ({'zone': 'us-west2-a'}, {'instance': 'roseos-yogi-builder-20261103-a', 'zone': 'us-west2-a'},
                        {'instanceId': '1'}, {'instance': 'immortal-os-builder'}, {'project': 'other-project'},
                        {'runnerLabel': 'roseos-yogi-builder'}, {'runnerLabel': 'roseos-frankel-builder'}):
            with self.subTest(changes=sorted(changes)):
                bad = copy.deepcopy(base)
                bad['builder'].update(changes)
                path = self.root/'yogi.json'
                path.write_text(json.dumps(bad))
                with self.assertRaises(yogi.Refused):
                    yogi.config(path)

    def test_cleanup_check_validates_the_record_without_gcloud(self):
        own = self.root/'own.json'
        self.control('start', ['TERMINATED', 'RUNNING'], [], own)
        def no_gcloud(*a, **k):
            raise AssertionError('check must not call gcloud')
        with mock.patch.object(yogi.subprocess, 'run', no_gcloud):
            self.assertEqual(yogi.control_builder(self.cfg, 'check', own, env=RUN_ENV), 'stop')
            self.assertEqual(yogi.control_builder(self.cfg, 'check', self.root/'absent.json', env=RUN_ENV), 'none')
            unowned = self.root/'unowned.json'
            unowned.write_text(json.dumps(dict(yogi.read(own), observedBeforeStart='RUNNING', startedByThisOperation=False)))
            self.assertEqual(yogi.control_builder(self.cfg, 'check', unowned, env=RUN_ENV), 'none')
            for env in (dict(RUN_ENV, GITHUB_RUN_ID='778'), dict(RUN_ENV, GITHUB_RUN_ATTEMPT='2')):
                with self.assertRaises(yogi.Refused):
                    yogi.control_builder(self.cfg, 'check', own, env=env)

    def test_workflow_exports_any_written_ownership_and_cleanup_uses_it(self):
        text = (yogi.ROOT/'.github/workflows/yogi-ota.yml').read_text()
        body = text[text.index('\njobs:\n'):]
        controller = body[body.index('\n  controller:\n'):body.index('\n  readiness:\n')]
        self.assertIn('ownership: ${{ steps.export.outputs.ownership }}', controller)
        export = controller[controller.index('      - id: export\n'):]
        self.assertIn('        if: always()\n', export[:60])
        self.assertLess(controller.index('builder start --ownership'), controller.index('      - id: export\n'))
        cleanup = body[body.index('\n  cleanup:\n'):]
        self.assertIn("if: always() && needs.controller.result != 'skipped'", cleanup)
        self.assertIn('OWNERSHIP: ${{ needs.controller.outputs.ownership }}', cleanup)
        self.assertIn('builder stop --ownership', cleanup)
        check, login = cleanup.index('builder check --ownership'), cleanup.index('actions-gcp-login.py login')
        self.assertLess(check, login)  # the record is validated before any credential exists
        for step in ('actions-gcp-login.py login', 'builder stop --ownership', 'actions-gcp-login.py logout'):
            before = cleanup[:cleanup.index(step)]
            self.assertIn("steps.ownership.outputs.action == 'stop'", before[before.rindex('      - '):])


class ReadinessTest(Fixture):
    """Readiness gathers host evidence only; it never needs or opens builder.status, keys, builds or publication."""
    def host(self):
        b = self.cfg['builder']
        root = self.root/'srv'
        source = root/'src'
        for d in (source/'build/make', source/'.repo', source/'script', source/'prebuilts/sdk/tools/linux/bin',
                  source/'prebuilts/jdk/jdk21/linux-x86/bin', root/'inputs', root/'attempts'):
            d.mkdir(parents=True, exist_ok=True)
        (source/'build/make/envsetup.sh').write_text('# synthetic')
        if not (source/'build/envsetup.sh').is_symlink():
            (source/'build/envsetup.sh').symlink_to('make/envsetup.sh')
        (source/'script/generate-release.sh').write_text('# synthetic')
        for tool in ('prebuilts/sdk/tools/linux/bin/aapt', 'prebuilts/sdk/tools/linux/bin/apksigner',
                     'prebuilts/jdk/jdk21/linux-x86/bin/java'):
            (source/tool).write_text('#!/bin/sh\n')
            (source/tool).chmod(0o755)
        (root/'yogi-build.lock').write_text('')
        (root/'inputs/cover-display-reference.json').write_text('{}')
        b.update(root=str(root), retainedSource=str(source), attempts=str(root/'attempts'), lock=str(root/'yogi-build.lock'),
                 coverDisplayInput=str(root/'inputs/cover-display-reference.json'),
                 bootstrapState=str(root/'signing-bootstrap-state'), status='runner-unregistered')
        return {'instance/id': yogi.INSTANCE_ID, 'instance/name': yogi.INSTANCE,
                'instance/zone': 'projects/123/zones/us-west2-b', 'project/project-id': 'protocols-465723'}

    def run_readiness(self, meta, out, free=10**13):
        return yogi.readiness(self.cfg, out, env={'RUNNER_NAME': yogi.INSTANCE, 'GITHUB_SHA': SHA},
                              metadata=lambda key: meta[key], which=lambda tool: '/usr/bin/' + tool,
                              free=lambda path: SimpleNamespace(free=free))

    def test_readiness_passes_on_the_exact_host_without_opening_any_other_stage(self):
        meta = self.host()
        record = self.run_readiness(meta, self.root/'out')
        self.assertTrue(record['passed'], [c for c in record['checks'] if not c['passed']])
        self.assertEqual(sorted(p.name for p in (self.root/'out').iterdir()), ['yogi-readiness.json'])
        self.assertEqual(yogi.blockers(self.cfg, 'readiness'), [])
        for stage in ('key-bootstrap', 'bootstrap', 'ota'):  # builder.status still closes every build/signing path
            self.assertTrue(any('builder runner/controller not ready' in b for b in yogi.blockers(self.cfg, stage)))

    def refused_without_inspection(self, meta, env, pattern, out):
        """Readiness refuses with `pattern` and touches nothing after the identity step: no path, tool or space probe."""
        def never(*args, **kwargs):
            raise AssertionError('inspected after an identity refusal')
        with mock.patch.object(yogi, 'regular_dir', never), mock.patch.object(yogi, 'builder_lock', never):
            with self.assertRaisesRegex(yogi.Refused, pattern):
                yogi.readiness(self.cfg, out, env=env, metadata=meta, which=never, free=never)
        self.assertFalse(Path(out).exists())

    def test_wrong_or_unavailable_host_identity_refuses_before_any_inspection_or_receipt(self):
        good = self.host()
        env = {'RUNNER_NAME': yogi.INSTANCE, 'GITHUB_SHA': SHA}
        for change in ({'instance/zone': 'projects/123/zones/us-west2-a'}, {'instance/id': '1'},
                       {'instance/name': 'roseos-yogi-builder-20261103-a'}, {'project/project-id': 'other-project'},
                       {'instance/name': 'immortal-os-builder'}):
            with self.subTest(change=change):
                meta = dict(good, **change)
                self.refused_without_inspection(lambda key: meta[key], env, 'not the exact replacement tuple',
                                                self.root/'out-wrong')
        for missing in good:
            with self.subTest(missing=missing):
                meta = {k: v for k, v in good.items() if k != missing}
                self.refused_without_inspection(lambda key: meta[key], env, r'host identity unavailable \(KeyError\)',
                                                self.root/'out-missing')

        def unreachable(key):
            raise OSError('metadata server unreachable')
        self.refused_without_inspection(unreachable, env, r'host identity unavailable \(OSError\)', self.root/'out-down')

    def test_space_or_tool_failures_after_a_good_identity_still_leave_a_failed_receipt(self):
        meta = self.host()
        with self.assertRaisesRegex(yogi.Refused, 'free space'):
            self.run_readiness(meta, self.root/'out-space', free=1)
        self.assertFalse(yogi.read(self.root/'out-space/yogi-readiness.json')['passed'])
        (Path(self.cfg['builder']['retainedSource'])/TOOLS_APKSIGNER).unlink()
        with self.assertRaisesRegex(yogi.Refused, 'apksigner'):
            self.run_readiness(meta, self.root/'out-tools')
        self.assertFalse(yogi.read(self.root/'out-tools/yogi-readiness.json')['passed'])

    def test_runner_name_must_be_the_exact_registered_replacement_runner(self):
        meta = self.host()

        def metadata(key):
            raise AssertionError('metadata read after a runner-name refusal')
        for name in (None, '', 'roseos-yogi-builder-20261103-a', 'roseos-frankel-builder', 'immortal-os-builder',
                     'roseos-yogi-builder', yogi.INSTANCE + ' ', yogi.INSTANCE.upper()):
            with self.subTest(name=name):
                env = {'GITHUB_SHA': SHA} if name is None else {'RUNNER_NAME': name, 'GITHUB_SHA': SHA}
                self.refused_without_inspection(metadata, env, 'is not the registered replacement runner',
                                                self.root/'out-name')
        self.assertTrue(self.run_readiness(meta, self.root/'out-ok')['passed'])
        self.cfg['builder']['evidence']['runner']['name'] = 'other-runner'
        self.assertTrue(any('runner.name is not the replacement' in b for b in yogi.blockers(self.cfg, 'readiness')))

    def test_organization_eligibility_exception_is_readiness_only(self):
        runner = self.cfg['builder']['evidence']['runner']
        runner['organizationEligibility'] = None
        self.assertEqual(yogi.blockers(self.cfg, 'readiness'), [])
        self.assertEqual(yogi.evidence_missing(self.cfg, 'readiness'), [])
        self.cfg['builder']['status'] = 'ready'
        for stage in ('key-bootstrap', 'bootstrap', 'ota'):
            with self.subTest(stage=stage):
                self.assertTrue(any('runner.organizationEligibility' in b for b in yogi.blockers(self.cfg, stage)))
                with self.assertRaisesRegex(yogi.Refused, 'runner.organizationEligibility'):
                    yogi.guard(self.cfg, stage)
        self.assertIn('runner.organizationEligibility', yogi.evidence_missing(self.cfg))
        self.assertEqual(yogi.READINESS_EXEMPT, frozenset({'runner.organizationEligibility'}))

    def test_routing_evidence_gates(self):
        runner = self.cfg['builder']['evidence']['runner']
        runner.update(labels=['self-hosted', 'roseos-yogi-builder'])
        self.assertTrue(any('is not in the read-back runner labels' in b for b in yogi.blockers(self.cfg, 'readiness')))
        runner['labels'] = ['self-hosted', yogi.RUNNER_LABEL, 'roseos-frankel-builder']
        self.assertTrue(any("another builder's label" in b for b in yogi.blockers(self.cfg, 'readiness')))
        runner['labels'] = ['self-hosted', yogi.RUNNER_LABEL]
        self.cfg['builder']['evidence']['storage']['mounts']['mountPoint'] = None
        self.assertTrue(any('storage.mounts' in b for b in yogi.blockers(self.cfg, 'readiness')))

    def test_committed_tree_keeps_readiness_closed_on_the_missing_mount_receipt(self):
        cfg = yogi.config()
        runner = cfg['builder']['evidence']['runner']
        self.assertTrue(runner['replacementLabelInstalled'])
        self.assertEqual(runner['labels'], ['self-hosted', 'Linux', 'X64', 'roseos-yogi-builder', yogi.RUNNER_LABEL])
        self.assertIsNone(runner['organizationEligibility'])  # unknown after the 403; never inferred
        self.assertIn('HTTP 403', runner['organizationEligibilityReadback'])
        self.assertEqual(cfg['builder']['status'], 'runner-unregistered')
        found = yogi.blockers(cfg, 'readiness')
        self.assertEqual(found, ['replacement builder receipt fields not recorded: '
                                 'storage.mounts (filesystemUuid, guestDevice, mountPoint)'])
        for stage in ('key-bootstrap', 'bootstrap', 'ota'):
            joined = ' '.join(yogi.blockers(cfg, stage))
            self.assertIn('runner.organizationEligibility', joined)
            self.assertIn('storage.mounts', joined)
        with self.assertRaises(yogi.Refused):
            yogi.readiness(cfg, self.root/'never')
        self.assertFalse((self.root/'never').exists())

    def readiness_job(self):
        text = (yogi.ROOT/'.github/workflows/yogi-ota.yml').read_text()
        body = text[text.index('\njobs:\n'):]
        return body, body[body.index('\n  readiness:\n'):body.index('\n  key-bootstrap:\n')]

    def test_readiness_job_is_isolated_from_build_keys_signing_and_publication(self):
        body, job = self.readiness_job()
        self.assertIn("if: inputs.stage == 'readiness'", job)
        self.assertIn('runs-on: [self-hosted, roseos-yogi-builder-20261105-b]', job)
        self.assertIn('    permissions:\n      contents: read\n    env:', job)
        for forbidden in ('id-token', 'write', 'secrets.', 'vars.', 'actions-gcp-login', 'signing-bootstrap', 'compile',
                          'integrate', 'download-artifact', 'publish', 'sign ', 'allocate', 'prepare',
                          'continue-on-error', 'WORKLOAD_IDENTITY'):
            self.assertNotIn(forbidden, job)
        self.assertEqual(job.count('python3 scripts/yogi_ota.py'), 2)
        for name, condition in (('key-bootstrap', "if: inputs.stage == 'key-bootstrap'"),
                                ('build', "if: inputs.stage == 'bootstrap' || inputs.stage == 'ota'")):
            block = body[body.index(f'\n  {name}:\n'):]
            self.assertIn(condition, block[:200])

    def test_identity_step_is_first_before_checkout_and_nothing_runs_after_a_refusal(self):
        import yaml
        workflow = yaml.safe_load((yogi.ROOT/'.github/workflows/yogi-ota.yml').read_text())
        job = workflow['jobs']['readiness']
        self.assertEqual(job['permissions'], {'contents': 'read'})
        steps = job['steps']
        self.assertEqual(steps[0]['id'], 'identity')
        self.assertNotIn('uses', steps[0])
        self.assertNotIn('if', steps[0])
        self.assertTrue(steps[1]['uses'].startswith('actions/checkout@'))
        self.assertTrue(all('uses' not in s or not s['uses'].startswith('actions/checkout@') for s in steps[2:]))
        for step in steps[1:]:
            self.assertNotIn('continue-on-error', step)
            condition = step.get('if')
            if condition is None:
                continue  # default success(): skipped after the identity step fails
            self.assertNotRegex(condition, r'\b(failure|cancelled)\(')
            if 'always()' in condition:
                self.assertIn("steps.identity.outcome == 'success'", condition)
                self.assertRegex(condition, r"^always\(\) && steps\.identity\.outcome == 'success'$")
        self.assertNotIn('continue-on-error', job)
        b = yogi.config()['builder']
        self.assertEqual(job['env'], {'YOGI_EXPECT_RUNNER': b['evidence']['runner']['name'],
                                      'YOGI_EXPECT_PROJECT': b['project'], 'YOGI_EXPECT_ZONE': b['zone'],
                                      'YOGI_EXPECT_INSTANCE': b['instance'], 'YOGI_EXPECT_INSTANCE_ID': b['instanceId']})
        self.assertEqual(b['evidence']['runner']['name'], yogi.INSTANCE)
        self.assertEqual(b['instanceId'], yogi.INSTANCE_ID)


class IdentityStepTest(unittest.TestCase):
    """Runs the workflow's own pre-checkout identity script against a local fake metadata server (test only)."""
    @classmethod
    def setUpClass(cls):
        import http.server
        import threading
        import yaml
        workflow = yaml.safe_load((yogi.ROOT/'.github/workflows/yogi-ota.yml').read_text())
        job = workflow['jobs']['readiness']
        run = job['steps'][0]['run']
        start, end = run.index("<<'PY'\n") + len("<<'PY'\n"), run.rindex('\nPY')
        cls.script, cls.expect = run[start:end], dict(job['env'])
        assert 'http://metadata.google.internal/computeMetadata/v1/' in cls.script
        cls.values = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                key = self.path.removeprefix('/computeMetadata/v1/')
                if self.headers.get('Metadata-Flavor') != 'Google' or key not in cls.values:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(cls.values[key].encode())

            def log_message(self, *args):
                pass
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        type(self).values = {'instance/id': yogi.INSTANCE_ID, 'instance/name': yogi.INSTANCE,
                             'instance/zone': 'projects/123/zones/us-west2-b', 'project/project-id': yogi.PROJECT}

    def step(self, runner=yogi.INSTANCE, url=None, proxy=False):
        url = url or f'http://127.0.0.1:{self.server.server_port}/computeMetadata/v1/'
        script = self.script.replace('http://metadata.google.internal/computeMetadata/v1/', url)
        env = {'PATH': '/usr/bin:/bin', **self.expect}
        if runner is not None:
            env['RUNNER_NAME'] = runner
        if proxy:  # a configured proxy must never answer for the metadata server
            env.update(http_proxy='http://127.0.0.1:9', HTTP_PROXY='http://127.0.0.1:9', no_proxy='', NO_PROXY='')
        return subprocess.run([sys.executable, '-I', '-'], input=script, env=env, capture_output=True, text=True,
                              timeout=60)

    def test_exact_runner_and_tuple_pass_even_with_a_proxy_configured(self):
        for proxy in (False, True):
            with self.subTest(proxy=proxy):
                result = self.step(proxy=proxy)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('identity matches the replacement builder', result.stdout)

    def test_wrong_or_missing_runner_name_refuses_without_reading_metadata(self):
        type(self).values = {}  # any metadata read would fail differently
        for runner in (None, '', 'roseos-yogi-builder-20261103-a', 'roseos-yogi-builder', 'roseos-frankel-builder'):
            with self.subTest(runner=runner):
                result = self.step(runner=runner)
                self.assertEqual(result.returncode, 1)
                self.assertIn('runner name is not the registered replacement runner', result.stdout)

    def test_wrong_metadata_refuses(self):
        good = dict(self.values)
        for key, value in (('instance/zone', 'projects/123/zones/us-west2-a'), ('instance/id', '1'),
                           ('instance/name', 'roseos-yogi-builder-20261103-a'), ('project/project-id', 'other'),
                           ('instance/name', 'immortal-os-builder')):
            with self.subTest(key=key, value=value):
                type(self).values = dict(good, **{key: value})
                result = self.step()
                self.assertEqual(result.returncode, 1)
                self.assertIn('not the exact replacement project/zone/name/numeric ID', result.stdout)

    def test_missing_or_unreachable_metadata_refuses(self):
        good = dict(self.values)
        for key in good:
            with self.subTest(missing=key):
                type(self).values = {k: v for k, v in good.items() if k != key}
                result = self.step()
                self.assertEqual(result.returncode, 1)
                self.assertIn('host identity unavailable (HTTPError)', result.stdout)
        result = self.step(url='http://127.0.0.1:9/computeMetadata/v1/')
        self.assertEqual(result.returncode, 1)
        self.assertIn('host identity unavailable', result.stdout)

TOOLS_APKSIGNER = 'prebuilts/sdk/tools/linux/bin/apksigner'


if __name__ == '__main__':
    unittest.main()
