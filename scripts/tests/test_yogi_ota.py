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
        self.cfg['builder'].update(status='ready', attempts=str(self.root/'attempts'))
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
        bound = {'profiles': {'yogi-v1': {'status': 'resource-bound', 'missing': [], 'project': 'protocols-465723',
                                          'secrets': dict(yogi.SECRETS)}}}
        self.profiles = bound
        original = yogi.profile_problem
        profiles = mock.patch.object(yogi, 'profile_problem', lambda profiles=None: original(self.profiles))
        profiles.start()
        self.addCleanup(profiles.stop)
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
        self.assertEqual(cfg['builder']['instance'], 'roseos-yogi-builder-20261103-a')
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
        self.assertEqual(text.count('runs-on: [self-hosted, roseos-yogi-builder]'), 2)


class IdentityTest(Fixture):
    def test_ready_fixture_passes_every_guard(self):
        for stage in yogi.STAGES:
            self.assertEqual(yogi.blockers(self.cfg, stage, access=True, now=self.now), [], stage)
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
        self.assertIn('yogi-v1 is not defined', ORIGINAL_PROFILE_PROBLEM())  # the real committed profiles

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


class BuilderControlTest(Fixture):
    def gcloud(self, states, log):
        def run(argv, **kwargs):
            log.append(argv[3])
            if argv[3] == 'describe':
                self.assertEqual(argv[4], 'roseos-yogi-builder-20261103-a')
                return SimpleNamespace(returncode=0, stdout=states.pop(0) if len(states) > 1 else states[0])
            return SimpleNamespace(returncode=0, stdout='')
        return run

    def test_start_records_ownership_and_stop_only_what_this_run_started(self):
        log, own = [], self.root/'own.json'
        with mock.patch.object(yogi.subprocess, 'run', self.gcloud(['TERMINATED', 'STAGING', 'RUNNING'], log)):
            self.assertEqual(yogi.control_builder(self.cfg, 'start', own, sleep=lambda s: None), 'RUNNING')
        self.assertTrue(yogi.read(own)['startedByThisOperation'])
        self.assertIn('start', log)
        log2 = []
        with mock.patch.object(yogi.subprocess, 'run', self.gcloud(['RUNNING', 'STOPPING', 'TERMINATED'], log2)):
            self.assertEqual(yogi.control_builder(self.cfg, 'stop', own, sleep=lambda s: None), 'TERMINATED')
        self.assertIn('stop', log2)

    def test_already_running_builder_is_never_stopped_and_record_is_never_reused(self):
        log, own = [], self.root/'own.json'
        with mock.patch.object(yogi.subprocess, 'run', self.gcloud(['RUNNING'], log)):
            yogi.control_builder(self.cfg, 'start', own, sleep=lambda s: None)
            self.assertEqual(yogi.control_builder(self.cfg, 'stop', own), 'not-started-by-this-run')
            self.assertEqual(yogi.control_builder(self.cfg, 'stop', self.root/'absent.json'), 'not-started-by-this-run')
            with self.assertRaises(FileExistsError):
                yogi.control_builder(self.cfg, 'start', own)
        self.assertNotIn('stop', log)
        self.assertNotIn('start', log)


if __name__ == '__main__':
    unittest.main()
