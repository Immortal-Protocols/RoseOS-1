"""OCT30-INFRA-S015: scripts/rango-retained.py, the retained Rango signing and separate-release route.

Synthetic receipts, files and a fake release client only: no host, secret, key, bootstrap, network or publication."""
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rr = load('rango_retained', SCRIPTS/'rango-retained.py')
host_sign = load('host_sign_for_rango', SCRIPTS/'host-sign.py')
pipeline = load('release_pipeline_for_rango', SCRIPTS/'release-pipeline.py')
CONFIG = rr.config()
FRANKEL = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
TARGET, OTATOOLS = b'synthetic rango target-files', b'synthetic rango otatools'
MANIFEST, POLICY = b'<manifest><!-- synthetic rango resolved manifest --></manifest>\n', b'{"synthetic": "rango policy"}\n'


def h(data):
    return hashlib.sha256(data).hexdigest()


def receipt(**change):
    data = {'schema': 'roseos.rango-retained-input.v1', 'device': 'rango', 'product': CONFIG['target']['product'],
        'build': {'number': '2026103001', 'timestamp': 1793330000},
        'source': {'manifestSha256': h(MANIFEST), 'configSha256': '2' * 64, 'compileReceiptSha256': '3' * 64, 'compileExit': 0,
                   'overlays': [{'path': 'scripts/background_apks.py', 'sha256': '4' * 64}]},
        'artifacts': {'targetFiles': {'name': 'rango-target_files.zip', 'size': len(TARGET), 'sha256': h(TARGET)},
                      'otatools': {'name': 'rango-otatools.zip', 'size': len(OTATOOLS), 'sha256': h(OTATOOLS)}},
        'apps': {'roseApk': {'packageName': 'dev.pixel.voice', 'versionCode': 311, 'sha256': '5' * 64, 'signerSha256': '6' * 64},
                 'policySha256': h(POLICY)},
        'producer': {'route': 'rango-build-only', 'operator': 'mac-operator', 'host': 'mac-builder'}}
    data.update(change)
    return data


def bootstrap(**change):
    data = {'schema': 'roseos.signing-bootstrap-receipt.v1', 'profile': 'fold-rango-v1', 'device': 'rango', 'status': 'complete',
        'recoveryNeeded': False,
        'secrets': {'keys': {'name': 'PIXEL_OS_SIGNING_KEYS_RANGO_V1', 'committedVersion': '1', 'state': 'committed'},
                    'passphrase': {'name': 'PIXEL_OS_SIGNING_PASSPHRASE_RANGO_V1', 'committedVersion': '2', 'state': 'committed'}},
        'publicIdentity': {'otaCertificateSha256': 'a' * 64, 'avbPublicKeySha256': 'b' * 64, 'members': []},
        'cleanup': {'workspaceCreated': True, 'workspaceRemoved': True}}
    data.update(change)
    return data


class Tmp:
    def __init__(self, test):
        self.root = Path(tempfile.mkdtemp(prefix='rango-retained-'))
        test.addCleanup(shutil.rmtree, self.root, True)

    def input(self, data=None, target=TARGET, otatools=OTATOOLS, name='in'):
        d = self.root/name
        d.mkdir()
        (d/'rango-retained-input.json').write_text(json.dumps(data or receipt()))
        (d/'rango-target_files.zip').write_bytes(target)
        (d/'rango-otatools.zip').write_bytes(otatools)
        (d/'rango-resolved-manifest.xml').write_bytes(MANIFEST)
        (d/'rango-background-policy.json').write_bytes(POLICY)
        return d


class Config(unittest.TestCase):
    def test_checked_in_route_is_unbound_and_refuses_before_any_host(self):
        self.assertEqual(rr.preflight(), ['input.transport', 'input.directory', 'signing.bootstrapReceipt',
            'signing.tools.generator.installed', 'signing.tools.decrypt-keys.installed', 'signing.tools.avbtool.installed',
            'signing.tools.openssl.installed', 'signing.otatoolsProvenance', 'signing.releaseScriptProvenance',
            'publication.feedChannel', 'publication.notes', 'verification.expected'])
        self.assertEqual(rr.main(['preflight']), 4)
        self.assertEqual((CONFIG['target']['device'], CONFIG['target']['product'], CONFIG['target']['build']),
            ('rango', '02b21bc69f9afd281422d42e3dcf8a456e471de1', '2026103001'))

    def test_mutated_config_cannot_reach_latest_or_another_device(self):
        for path, value in ((('publication', 'latest'), True), (('publication', 'tag'), 'frankel-{build}'),
                            (('target', 'device'), 'frankel'), (('signing', 'bootstrapProfile'), 'models-v0'),
                            (('target', 'product'), 'main')):
            cfg = copy.deepcopy(CONFIG)
            cfg[path[0]][path[1]] = value
            with self.assertRaisesRegex(rr.Refused, 'config_invalid'):
                rr.preflight(cfg=cfg)

    def test_frankel_admission_and_latest_stay_frankel_only(self):
        self.assertEqual((pipeline.SUPPORTED_DEVICES, pipeline.LATEST_DEVICES), (('frankel',), ('frankel',)))
        self.assertEqual(sorted(p.name for p in (ROOT/'os/release-pipeline').glob('*.json')), ['alpha-runtime.json', 'cuttlefish-alpha.json', 'frankel.json'])


class Receipt(unittest.TestCase):
    def test_complete_receipt_has_nothing_unbound(self):
        self.assertEqual(rr.check_receipt(receipt()), [])

    def test_missing_values_stay_explicitly_unbound(self):
        data = receipt(build={'number': '2026103001', 'timestamp': None}, producer=None,
            apps={'roseApk': None, 'policySha256': None})
        data['source'].update(manifestSha256=None, overlays=None)
        data['artifacts']['otatools'] = None
        self.assertEqual(rr.check_receipt(data), ['input.build.timestamp', 'input.source.manifestSha256', 'input.source.overlays',
            'input.artifacts.otatools', 'input.apps.roseApk', 'input.apps.policySha256', 'input.producer'])

    def test_wrong_or_foreign_receipts_refuse(self):
        cases = [(dict(schema='other'), 'input_invalid'), (dict(device='frankel'), 'input_foreign'),
            (dict(product='f' * 40), 'input_foreign'), (dict(build={'number': '2026102806', 'timestamp': 1793330000}), 'input_foreign'),
            (dict(build={'number': '2026103001', 'timestamp': 17}), 'input_invalid'),
            (dict(producer={'route': 'frankel-os-release', 'operator': 'x', 'host': 'y'}), 'input_foreign'),
            (dict(apps={'roseApk': {'packageName': 'dev.pixel.other', 'versionCode': 1, 'sha256': '5' * 64,
                                    'signerSha256': '6' * 64}, 'policySha256': '7' * 64}), 'input_invalid')]
        for change, category in cases:
            with self.assertRaisesRegex(rr.Refused, category, msg=change):
                rr.check_receipt(receipt(**change))
        for source, category in (({'compileExit': 1}, 'compile'), ({'overlays': [{'path': '../x', 'sha256': '4' * 64}]}, 'overlays'),
                                 ({'overlays': [{'path': 'a', 'sha256': '4' * 64}] * 2}, 'overlays'),
                                 ({'manifestSha256': 'short'}, 'manifestSha256')):
            data = receipt()
            data['source'].update(source)
            with self.assertRaisesRegex(rr.Refused, category):
                rr.check_receipt(data)
        data = receipt()
        data['artifacts']['targetFiles']['name'] = '../rango-target_files.zip'
        with self.assertRaisesRegex(rr.Refused, 'input_invalid'):
            rr.check_receipt(data)


class Staging(unittest.TestCase):
    def test_stage_copies_into_a_fresh_consumer_and_revalidates(self):
        t = Tmp(self)
        src = t.input()
        record = rr.stage(src, t.root/'consumer')
        self.assertEqual(record['files'], {'rango-target_files.zip': h(TARGET), 'rango-otatools.zip': h(OTATOOLS),
            'rango-resolved-manifest.xml': h(MANIFEST), 'rango-background-policy.json': h(POLICY)})
        self.assertEqual(record['unbound'], [])
        (src/'rango-target_files.zip').write_bytes(b'source changed after staging')  # the copy is what is used
        self.assertEqual(rr.recheck(t.root/'consumer')[2]['files'], record['files'])
        (t.root/'consumer/input/rango-otatools.zip').write_bytes(b'tampered')
        with self.assertRaisesRegex(rr.Refused, 'input_drift'):
            rr.recheck(t.root/'consumer')
        with self.assertRaisesRegex(rr.Refused, 'consumer_exists'):
            rr.stage(t.input(name='in2'), t.root/'consumer')

    def test_unsafe_or_drifting_inputs_refuse(self):
        t = Tmp(self)
        d = t.input(name='extra')
        (d/'notes.txt').write_text('x')
        with self.assertRaisesRegex(rr.Refused, 'input_inventory'):
            rr.check_directory(d)
        d = t.input(name='missing')
        (d/'rango-otatools.zip').unlink()
        with self.assertRaisesRegex(rr.Refused, 'input_inventory'):
            rr.check_directory(d)
        d = t.input(name='link')
        (d/'rango-otatools.zip').unlink()
        os.symlink(d/'rango-target_files.zip', d/'rango-otatools.zip')
        with self.assertRaisesRegex(rr.Refused, 'input_unsafe'):
            rr.check_directory(d)
        with self.assertRaisesRegex(rr.Refused, 'input_drift'):
            rr.check_directory(t.input(name='drift', target=b'X' * len(TARGET)))
        with self.assertRaisesRegex(rr.Refused, 'input_drift'):
            rr.check_directory(t.input(name='grown', target=TARGET + b'more'))
        alias = t.root/'alias'
        os.symlink(t.input(name='real'), alias)
        with self.assertRaisesRegex(rr.Refused, 'input_unsafe'):
            rr.check_directory(alias)
        data = receipt()
        data['artifacts']['targetFiles'] = None
        with self.assertRaises(rr.SetupMissing):
            rr.check_directory(t.input(data, name='unbound'))


class Signer(unittest.TestCase):
    def test_genuine_receipt_binds_distinct_rango_identities(self):
        binding = rr.signer_binding(bootstrap())
        self.assertEqual(binding['secrets'], {'keys': 'PIXEL_OS_SIGNING_KEYS_RANGO_V1', 'passphrase': 'PIXEL_OS_SIGNING_PASSPHRASE_RANGO_V1'})
        self.assertEqual(binding['versions'], {'keys': '1', 'passphrase': '2'})
        frankel = FRANKEL['host']['sign']
        self.assertNotIn(frankel['keysSecret'], binding['secrets'].values())
        self.assertNotIn(frankel['passphraseSecret'], binding['secrets'].values())

    def test_placeholder_incomplete_or_frankel_identities_refuse(self):
        ident = FRANKEL['identities']
        cases = [(dict(profile='models-v0'), 'signer_unbound'), (dict(status='failed'), 'signer_unbound'),
            (dict(recoveryNeeded=True), 'signer_unbound'),
            (dict(secrets={'keys': {'name': 'PIXEL_OS_SIGNING_KEYS_RANGO_V1', 'committedVersion': None, 'state': 'untouched'},
                           'passphrase': {'name': 'PIXEL_OS_SIGNING_PASSPHRASE_RANGO_V1', 'committedVersion': '1', 'state': 'committed'}}),
             'no committed exact version'),
            (dict(publicIdentity={'otaCertificateSha256': None, 'avbPublicKeySha256': 'b' * 64}), 'publicIdentity'),
            (dict(publicIdentity={'otaCertificateSha256': ident['otaCertificateSha256'], 'avbPublicKeySha256': 'b' * 64}), 'signer_collision'),
            (dict(publicIdentity={'otaCertificateSha256': 'a' * 64, 'avbPublicKeySha256': ident['avbPublicKeySha256']}), 'signer_collision'),
            (dict(cleanup={'workspaceRemoved': None}), 'cleanup')]
        for change, category in cases:
            with self.assertRaisesRegex(rr.Refused, category, msg=change):
                rr.signer_binding(bootstrap(**change))

    def test_installed_tool_identities_stay_unbound_until_observed(self):
        prof = copy.deepcopy(rr.profile())
        self.assertEqual(len(rr.tool_bindings(prof)), 4)
        for name in rr.TOOLS:
            prof['tools'][name]['installed'] = {'path': f'/opt/{name}', 'sha256': 'c' * 64}
        self.assertEqual(rr.tool_bindings(prof), [])
        prof['tools']['openssl']['installed']['sha256'] = prof['tools']['openssl'].get('sourceSha256')
        self.assertEqual(rr.tool_bindings(prof), ['signing.tools.openssl.installed'])


class BoundSigning(unittest.TestCase):
    """host-sign.py's retained route: refuses before any secret while unbound; when bound, maps exactly the Rango
    secrets, per-secret versions and identities from the bootstrap receipt and the staged consumer inputs."""

    def bind_everything(self):
        cfg = copy.deepcopy(CONFIG)
        cfg['input']['transport'] = {'kind': 'synthetic-local-directory', 'directory': '/synthetic/unused'}
        cfg['signing'].update(otatoolsProvenance={'revision': 'r'}, releaseScriptProvenance={'revision': 's'})
        cfg['publication'].update(feedChannel='stable', notes='os/release-notes/{device}-{build}.md')
        cfg['verification']['expected'] = {'apps': []}
        saved = (rr.config, rr.tool_bindings)
        rr.config = lambda: cfg
        rr.tool_bindings = lambda prof=None: []
        self.addCleanup(setattr, rr, 'config', saved[0])
        self.addCleanup(setattr, rr, 'tool_bindings', saved[1])
        return cfg

    def env(self, t, consumer, receipt_path):
        return {'ROSE_RETAINED_INPUT': str(consumer), 'ROSE_RETAINED_BOOTSTRAP_RECEIPT': str(receipt_path),
            'BUILD_NUMBER': '2026103001', 'BUILD_DATETIME': '1793330000', 'ROSE_CANDIDATE_SHA': 'a' * 40,
            'ROSE_RELEASE_DIR': str(t.root/'release')}

    def test_unbound_route_refuses_before_any_secret(self):
        t = Tmp(self)
        rr.stage(t.input(), t.root/'consumer')
        fetched = []
        saved = host_sign.host_secrets.fetch_secret
        host_sign.host_secrets.fetch_secret = lambda *a, **k: fetched.append(a)
        self.addCleanup(setattr, host_sign.host_secrets, 'fetch_secret', saved)
        with self.assertRaises(host_sign.AdapterSetupMissing) as error:
            host_sign.main(env=self.env(t, t.root/'consumer', t.root/'absent.json'))
        self.assertIn('signing.bootstrapReceipt', error.exception.missing)
        self.assertEqual(fetched, [])

    def test_bound_route_maps_exact_rango_bindings(self):
        self.bind_everything()
        t = Tmp(self)
        rr.stage(t.input(), t.root/'consumer')
        (t.root/'bootstrap.json').write_text(json.dumps(bootstrap()))
        config, target, otatools = rr.sign_inputs(self.env(t, t.root/'consumer', t.root/'bootstrap.json'))
        self.assertEqual(config['host']['sign']['keysSecret'], 'PIXEL_OS_SIGNING_KEYS_RANGO_V1')
        self.assertEqual(config['host']['sign']['passphraseSecret'], 'PIXEL_OS_SIGNING_PASSPHRASE_RANGO_V1')
        self.assertEqual((config['host']['sign']['secretVersion'], config['host']['sign']['passphraseSecretVersion']), ('1', '2'))
        self.assertEqual(config['identities'], {'otaCertificateSha256': 'a' * 64, 'avbPublicKeySha256': 'b' * 64})
        self.assertNotEqual(config['identities']['otaCertificateSha256'], FRANKEL['identities']['otaCertificateSha256'])
        self.assertEqual((target.name, otatools.name), ('rango-target_files.zip', 'rango-otatools.zip'))
        self.assertEqual(host_sign.missing_inputs(config), [])
        with self.assertRaisesRegex(rr.Refused, 'input_foreign'):
            rr.sign_inputs(dict(self.env(t, t.root/'consumer', t.root/'bootstrap.json'), BUILD_NUMBER='2026103002'))
        (t.root/'consumer/input/rango-target_files.zip').write_bytes(b'tampered after staging')
        with self.assertRaisesRegex(rr.Refused, 'input_drift'):
            rr.sign_inputs(self.env(t, t.root/'consumer', t.root/'bootstrap.json'))

    def test_frankel_signing_route_is_unchanged(self):
        self.assertIsNone(None if 'ROSE_RETAINED_INPUT' in os.environ else None)
        self.assertEqual(host_sign.missing_inputs(FRANKEL), [])
        text = (SCRIPTS/'host-sign.py').read_text()
        self.assertIn("retained = retained_inputs(env) if env.get('ROSE_RETAINED_INPUT') else None", text)
        self.assertIn("config = retained[0] if retained else json.loads(Path(config_path).read_text())", text)


def synthetic_identity(root):
    """SYNTHETIC public identity for the fixture route: a throwaway certificate and AVB blob (never a real key)."""
    import subprocess
    key, pem = root/'synthetic.key', root/'releasekey.x509.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-subj', '/CN=synthetic-rango',
        '-days', '1', '-out', str(pem)], check=True, capture_output=True)
    key.unlink()
    (root/'avb_pkmd.bin').write_bytes(b'synthetic rango avb key blob')
    return pipeline.cert_sha256(pem.read_text()), h((root/'avb_pkmd.bin').read_bytes())


class Route(unittest.TestCase):
    """The connected fixture route: stage -> host-sign retained seam (signing mocked) -> release-pipeline verify_signed
    (image/OTA/AVB tool checks mocked) -> stage_artifact -> publish (draft, download check) -> public, never Latest."""

    def setUp(self):
        self.t = Tmp(self)
        self.ident = self.t.root/'identity'
        self.ident.mkdir()
        self.ota_cert, self.avb = synthetic_identity(self.ident)
        self.src = self.t.input()
        cfg = copy.deepcopy(CONFIG)
        cfg['input']['transport'] = {'kind': 'synthetic-local-directory', 'directory': str(self.src)}
        cfg['signing'].update(bootstrapReceipt=bootstrap(publicIdentity={'otaCertificateSha256': self.ota_cert,
            'avbPublicKeySha256': self.avb, 'members': []}), otatoolsProvenance={'synthetic': True},
            releaseScriptProvenance={'synthetic': True})
        cfg['publication'].update(feedChannel='stable', notes='os/release-notes/{device}-{build}.md')
        cfg['verification']['expected'] = {'variant': 'user', 'apps': [['dev.pixel.voice', 311]],
            'identities': {'appSignerSha256': '6' * 64}, 'host': {'tools': {}}}
        for name in ('apksigner', 'aapt'):  # SYNTHETIC tool paths; the tool-running checks are mocked
            (self.t.root/name).write_text('#!/bin/sh\nexit 1\n')
            cfg['verification']['expected']['host']['tools'][name] = str(self.t.root/name)
        self.cfg = cfg
        saved = (rr.config, rr.tool_bindings)
        rr.config = lambda: cfg
        rr.tool_bindings = lambda prof=None: []
        self.addCleanup(setattr, rr, 'config', saved[0])
        self.addCleanup(setattr, rr, 'tool_bindings', saved[1])
        self.module = rr.pipeline()
        self.checks = []
        for name in ('check_build_identity', 'check_apps', 'check_updater', 'check_image_bytes', 'check_ota', 'check_payload',
                     'check_vbmeta'):
            self.mock_check(name, [])
        for name in ('check_permission_apex', 'check_systemui_tray'):
            self.mock_check(name, ([], {'synthetic': True}))
        self.consumer = self.t.root/'consumer'
        rr.stage(self.src, self.consumer)
        self.release, self.artifact = self.t.root/'release', self.t.root/'artifact'
        self.adapter = self.module.Fake(self.t.root/'fake')
        (self.t.root/'source-root').mkdir()
        self.env = {'HOME': str(self.t.root), 'ROSE_RETAINED_SOURCE_ROOT': str(self.t.root/'source-root')}

    def mock_check(self, name, result):
        saved = getattr(self.module, name)

        def check(*args, **kwargs):
            config = next(a for a in args if isinstance(a, dict) and 'target' in a) if any(
                isinstance(a, dict) and 'target' in a for a in args) else None
            self.checks.append((name, config and config['target']['device']))
            return result
        setattr(self.module, name, check)
        self.addCleanup(setattr, self.module, name, saved)

    def signer(self, env):
        """Mocked cryptographic signing behind host-sign.py's real retained seam (bound config, revalidated input)."""
        config, target, otatools = host_sign.retained_inputs(env, rr)
        self.signed_with = config['host']['sign']
        out = Path(env['ROSE_RELEASE_DIR'])
        public = out/'public'
        public.mkdir()
        (out/'signed-target_files.zip').write_bytes(b'SYNTHETIC signed ' + target.read_bytes())
        (public/'rango-ota_update-2026103001.zip').write_bytes(b'SYNTHETIC rango OTA')
        (public/'rango-stable').write_text(f"2026103001 {env['BUILD_DATETIME']} rango {config['publication']['channels']['beta']['feedChannel']}\n")
        for name in ('releasekey.x509.pem', 'avb_pkmd.bin'):
            shutil.copyfile(self.ident/name, public/name)
        (public/'UPDATE.md').write_text('SYNTHETIC notes for rango 2026103001\n')
        return 0

    def sign(self):
        return rr.sign(self.env, self.consumer, self.release, signer=self.signer, lock=lambda path: contextlib.nullcontext())

    def test_bound_fixture_reaches_the_public_never_latest_release(self):
        self.assertEqual(self.sign()['candidate'], {'sha': CONFIG['target']['product'], 'build': '2026103001',
            'timestamp': '1793330000', 'channel': 'beta', 'device': 'rango'})
        self.assertEqual((self.signed_with['keysSecret'], self.signed_with['passphraseSecret']),
            ('PIXEL_OS_SIGNING_KEYS_RANGO_V1', 'PIXEL_OS_SIGNING_PASSPHRASE_RANGO_V1'))
        self.assertEqual(rr.verify(self.env, self.consumer, self.release, self.adapter)['verdict'], 'PASS')
        self.assertTrue({('check_ota', 'rango'), ('check_vbmeta', 'rango')} <= set(self.checks))
        rr.stage_artifact(self.env, self.consumer, self.release, self.artifact, self.adapter)
        self.assertEqual(sorted(p.name for p in (self.artifact/'assets').iterdir()), sorted(self.module.public_assets('rango', '2026103001')))
        report = rr.publish(self.artifact, adapter=self.adapter)
        self.assertEqual((report['promoted'], report.get('finalizedPublic')), (False, True))
        store = self.t.root/'fake/releases/rango-2026103001'
        self.assertEqual(sorted(p.name for p in store.iterdir()), ['public'])
        self.assertIn('finalized public, not Latest', (self.t.root/'fake/events.log').read_text())
        self.assertNotIn('promoted latest', (self.t.root/'fake/events.log').read_text())
        manifest = json.loads((self.artifact/'manifest.json').read_text())
        self.assertEqual((manifest['target']['device'], manifest['identities']['otaCertificateSha256']), ('rango', self.ota_cert))

    def test_verification_failure_prevents_publication(self):
        self.sign()
        self.mock_check('check_ota', ['OTA package signature does not verify'])
        with self.assertRaisesRegex(self.module.CheckFailed, 'OTA package signature'):
            rr.verify(self.env, self.consumer, self.release, self.adapter)
        with self.assertRaises(Exception):
            rr.stage_artifact(self.env, self.consumer, self.release, self.artifact, self.adapter)
        self.assertFalse((self.t.root/'fake/releases').exists())

    def test_promote_tampering_and_foreign_bindings_never_publish(self):
        self.sign()
        rr.verify(self.env, self.consumer, self.release, self.adapter)
        rr.stage_artifact(self.env, self.consumer, self.release, self.artifact, self.adapter)
        with self.assertRaisesRegex(rr.Refused, 'latest_refused'):
            rr.publish(self.artifact, promote=True, adapter=self.adapter)
        bindings = json.loads((self.artifact/'rango-bindings.json').read_text())
        evil = copy.deepcopy(bindings)
        evil['bootstrapReceipt']['publicIdentity']['otaCertificateSha256'] = FRANKEL['identities']['otaCertificateSha256']
        (self.artifact/'rango-bindings.json').write_text(json.dumps(evil))
        with self.assertRaisesRegex(rr.Refused, 'signer_collision'):
            rr.publish(self.artifact, adapter=self.adapter)
        evil = copy.deepcopy(bindings)
        evil['inputReceipt']['device'] = 'frankel'
        (self.artifact/'rango-bindings.json').write_text(json.dumps(evil))
        with self.assertRaisesRegex(rr.Refused, 'input_foreign'):
            rr.publish(self.artifact, adapter=self.adapter)
        (self.artifact/'rango-bindings.json').write_text(json.dumps(bindings))
        (self.artifact/'assets/rango-ota_update-2026103001.zip').write_bytes(b'tampered in transfer')
        with self.assertRaises(self.module.CheckFailed):
            rr.publish(self.artifact, adapter=self.adapter)
        self.assertFalse((self.t.root/'fake/releases').exists())

    def test_drift_after_staging_refuses_before_signing(self):
        (self.consumer/'input/rango-background-policy.json').write_bytes(b'{"tampered": true}')
        called = []
        with self.assertRaisesRegex(rr.Refused, 'input_drift'):
            rr.sign(self.env, self.consumer, self.release, signer=lambda env: called.append(env),
                lock=lambda path: contextlib.nullcontext())
        self.assertEqual(called, [])

    def test_expected_config_cannot_carry_identities_or_publication(self):
        for key, value in (('publication', {}), ('target', {}), ('identities', {'otaCertificateSha256': 'f' * 64})):
            cfg = copy.deepcopy(self.cfg)
            cfg['verification']['expected'][key] = value
            with self.assertRaisesRegex(rr.Refused, 'config_invalid'):
                rr.pipeline_config(cfg, rr.signer_binding(cfg['signing']['bootstrapReceipt']))

    def test_held_lock_refuses_before_signing(self):
        import fcntl
        lock = self.t.root/'shared.lock'
        self.cfg['signing']['lockPath'] = str(lock)
        fd = os.open(lock, os.O_RDWR | os.O_CREAT)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        called = []
        with self.assertRaisesRegex(rr.Refused, 'lock_held'):
            rr.sign(self.env, self.consumer, self.release, signer=lambda env: called.append(env))
        self.assertEqual(called, [])


class UnboundRoute(unittest.TestCase):
    def test_checked_in_route_refuses_every_step_before_any_signer(self):
        t = Tmp(self)
        rr.stage(t.input(), t.root/'consumer')
        called = []
        with self.assertRaises(rr.SetupMissing):
            rr.sign({}, t.root/'consumer', t.root/'release', signer=lambda env: called.append(env),
                lock=lambda path: contextlib.nullcontext())
        for step in (lambda: rr.verify({}, t.root/'consumer', t.root/'release'),
                     lambda: rr.stage_artifact({}, t.root/'consumer', t.root/'release', t.root/'artifact')):
            with self.assertRaises(rr.SetupMissing):
                step()
        self.assertEqual(called, [])
        self.assertEqual(rr.main(['stage', '--consumer', str(t.root/'c2')]), 4)


class FakeClient:
    def __init__(self, latest='frankel-2026102806', move=False):
        self.latest, self.move, self.calls = latest, move, []

    def latest_tag(self):
        return self.latest

    def finalize_public(self, release_id):
        self.calls.append(('finalize_public', release_id))
        if self.move:
            self.latest = 'rango-2026103001'

    def promote(self, release_id):
        raise AssertionError('never promoted')


class ProductionFinalize(unittest.TestCase):
    def adapter(self, client, already=False):
        prod = pipeline.Production()
        prod.client, prod.release, prod.already_published, prod.latest_before = client, {'id': 7}, already, client.latest
        return prod

    def test_public_finalization_keeps_latest(self):
        client = FakeClient()
        self.adapter(client).finalize_public({}, {}, None)
        self.assertEqual((client.calls, client.latest), ([('finalize_public', 7)], 'frankel-2026102806'))

    def test_latest_move_or_existing_release_refuses(self):
        with self.assertRaisesRegex(pipeline.CheckFailed, 'Latest moved'):
            self.adapter(FakeClient(move=True)).finalize_public({}, {}, None)
        with self.assertRaisesRegex(pipeline.CheckFailed, 'must be fresh'):
            self.adapter(FakeClient(), already=True).finalize_public({}, {}, None)

    def test_public_finalization_never_requests_latest(self):
        text = (SCRIPTS/'github_release_publisher.py').read_text()
        body = text.split('def finalize_public', 1)[1].split('\n    def ', 1)[0]
        self.assertIn("body={'draft': False, 'prerelease': False, 'make_latest': 'false'}", body)
        source = (SCRIPTS/'release-pipeline.py').read_text()
        self.assertIn("finalized = bool(channel.get('finalizePublic')) and device not in LATEST_DEVICES and not promoted", source)


def jobs(text):
    body = text.split('\njobs:\n', 1)[1]
    return {m[1]: m[2] for m in re.finditer(r'^  ([\w-]+):\n((?:(?:    .*)?\n)*)', body, re.M)}


class Workflow(unittest.TestCase):
    def test_mode_is_gated_by_the_guard_and_reuses_the_existing_trust(self):
        j = jobs(WORKFLOW)
        guard, builder, publisher = j['guard'], j['rango-builder'], j['rango-publisher']
        self.assertIn('models-sign, rango-retained, ', WORKFLOW)
        self.assertIn('        default: latest-beta\n', WORKFLOW)
        self.assertNotIn('runtime is not selected', WORKFLOW)  # bindings gate the route, not an unconditional refusal
        self.assertLess(guard.index('python3 scripts/rango-retained.py preflight --job guard'), guard.index('name: Bootstrap preflight'))
        self.assertIn("    needs: controller\n    if: inputs.mode == 'rango-retained'\n    runs-on: [self-hosted, roseos-frankel-builder]\n", builder)
        order = [builder.index(s) for s in ('rango-retained.py preflight --job builder', 'rango-retained.py stage --consumer',
            'actions-gcp-login.py login', 'rango-retained.py sign', 'actions-gcp-login.py logout', 'rango-retained.py verify',
            'rango-retained.py stage-artifact', 'name: rango-release-${{ github.run_id }}')]
        self.assertEqual(order, sorted(order))
        self.assertIn("      - name: Drop signer access\n        if: always()\n", builder)
        self.assertIn("    needs: rango-builder\n    if: inputs.mode == 'rango-retained'\n", publisher)
        self.assertIn('python3 scripts/rango-retained.py publish --artifact-dir "$RUNNER_TEMP/rango-artifact"', publisher)
        self.assertNotIn('--promote', publisher)
        for job in (builder, publisher):
            self.assertNotIn('actions: write', job)
            self.assertNotIn('sign-apk.py', job)
        cleanup = re.search(r'    needs: \[([^\]]*)\]', j['cleanup'])[1].split(', ')
        self.assertTrue({'controller', 'rango-builder', 'rango-publisher'} <= set(cleanup))
        self.assertIn("    if: always() && needs.controller.result != 'skipped'\n", j['cleanup'])
        for name, body in j.items():
            if name not in ('guard', 'rango-builder', 'rango-publisher', 'cleanup'):
                self.assertNotIn('rango-retained', body, name)

    def test_actual_ci_runs_this_suite(self):
        ci = (ROOT/'.github/workflows/release-verification.yml').read_text()
        self.assertIn('          python3 scripts/tests/test_rango_retained.py\n', ci)
        self.assertIn('python3 scripts/tests/test_rango_retained.py\n', (ROOT/'scripts/test-local.sh').read_text())
        for path in ('scripts/rango-retained.py', 'os/retained-rango/**'):
            self.assertEqual(ci.count(f"      - '{path}'\n"), 2, path)


if __name__ == '__main__':
    unittest.main()
