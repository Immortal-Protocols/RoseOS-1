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
import subprocess
import sys
import tempfile
import unittest
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rr = load('rango_retained', SCRIPTS/'rango-retained.py')
sys.path.insert(0, str(SCRIPTS/'tests'))
import test_fold_bootstrap_proof as proof_fixtures  # noqa: E402  (the MOCK S016 helper and blob helper)
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


NOTES_FILE = SCRIPTS.parent/'os/release-notes/rango-2026103001.md'


def notes_history(test):
    """A SYNTHETIC git history: a product commit with no Rango notes, then a separate notes commit holding the real
    checked-in rango-2026103001.md bytes. Both modules read pinned notes from it. Returns (product, binding)."""
    repo = Path(tempfile.mkdtemp(prefix='notes-repo-'))
    test.addCleanup(shutil.rmtree, repo, True)
    git = ['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false']
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    (repo/'os/release-notes').mkdir(parents=True)
    (repo/'os/release-notes/frankel-2026102901.md').write_text('# frankel 2026102901\n')
    subprocess.run(git + ['add', '-A'], check=True)
    subprocess.run(git + ['commit', '-qm', 'product'], check=True)
    product = subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True).strip()
    shutil.copyfile(NOTES_FILE, repo/'os/release-notes/rango-2026103001.md')
    subprocess.run(git + ['add', '-A'], check=True)
    subprocess.run(git + ['commit', '-qm', 'notes'], check=True)
    commit = subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True).strip()
    for module in (rr, host_sign):
        saved = module.NOTES_REPO[0]
        module.NOTES_REPO[0] = repo
        test.addCleanup(module.NOTES_REPO.__setitem__, 0, saved)
    return product, {'commit': commit, 'path': 'os/release-notes/rango-2026103001.md',
        'sha256': hashlib.sha256(NOTES_FILE.read_bytes()).hexdigest(), 'build': '2026103001', 'device': 'rango'}


def bootstrap_mode(cfg):
    """The preserved bootstrap-receipt identity route (the checked-in config selects first-use since 13:07 UTC)."""
    cfg['signing']['identityPolicy']['mode'] = 'bootstrap-receipt'
    cfg['signing']['identityPolicy']['firstUse']['selected'] = False
    return cfg


def signed_target_files(out, target):
    """SYNTHETIC signed target-files: a real archive (so the real upstream Updater check reads it) without any Updater."""
    with zipfile.ZipFile(out/'signed-target_files.zip', 'w') as z:
        z.writestr('SYNTHETIC/signed-from-target-files', target.read_bytes())


class Config(unittest.TestCase):
    def test_checked_in_recipe_is_bound_and_passes_the_hosted_guard(self):
        # FINAL static data handoff: every static binding is in release.json. The hosted guard passes with nothing missing
        # and reads no input; only the builder (which holds the transferred directory) can check the real files.
        self.assertEqual(rr.preflight(local=False), [])
        self.assertEqual(rr.main(['preflight', '--job', 'guard']), 0)
        self.assertFalse(Path(CONFIG['input']['transport']['directory']).exists())  # not on this runner
        with self.assertRaisesRegex(rr.Refused, 'input_unsafe'):
            rr.preflight(local=True)
        self.assertEqual(rr.main(['preflight', '--job', 'builder']), 1)
        self.assertEqual((CONFIG['target']['device'], CONFIG['target']['product'], CONFIG['target']['build']),
            ('rango', '02b21bc69f9afd281422d42e3dcf8a456e471de1', '2026103001'))
        self.assertIsNone(CONFIG['signing']['bootstrapReceipt'])  # held; not used under the selected first-use policy

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

    def test_tools_come_only_from_the_admitted_local_proof(self):
        prof = copy.deepcopy(rr.profile())
        self.assertEqual(rr.tool_bindings(prof), [])  # admitted S016 helper and a valid spec
        self.assertTrue(all(tool['installed']['sha256'] is None for tool in prof['tools'].values()))  # never trusted statically
        unadmitted = copy.deepcopy(prof)
        unadmitted['localProof']['admittedBlob'] = None
        self.assertEqual(rr.tool_bindings(unadmitted), ['signing.localProof (proof-helper-unadmitted)'])
        unadmitted.pop('localProof')
        self.assertEqual(rr.tool_bindings(unadmitted), ['signing.localProof'])


class BoundSigning(unittest.TestCase):
    """host-sign.py's retained route: refuses before any secret while unbound; when bound, maps exactly the Rango
    secrets, per-secret versions and identities from the bootstrap receipt and the staged consumer inputs."""

    def bind_everything(self):
        cfg = bootstrap_mode(copy.deepcopy(CONFIG))
        cfg['input']['transport'] = {'kind': 'synthetic-local-directory', 'directory': '/synthetic/unused'}
        cfg['signing'].update(otatoolsProvenance={'revision': 'r'}, releaseScriptProvenance={'revision': 's'})
        self.product, notes = notes_history(self)
        cfg['publication'].update(feedChannel='stable', notes=notes)
        cfg['verification']['expected'] = {'apps': []}
        cfg['verification']['updater']['included'] = False
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
        # The checked-in recipe declares the real retained receipt (54d4cf66); a SYNTHETIC staged input is refused.
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'input_drift: the staged receipt is not the one input.transport declares'):
            host_sign.main(env=self.env(t, t.root/'consumer', t.root/'absent.json'))
        self.assertEqual(fetched, [])
        # The preserved bootstrap-receipt route refuses without its receipt.
        bootstrap_mode(self.bind_everything())  # host-sign's own seam with this module's (patched) config
        with self.assertRaises(host_sign.AdapterSetupMissing) as error:
            host_sign.retained_inputs(self.env(t, t.root/'consumer', t.root/'absent.json'), rr)
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
        bootstrap_mode(cfg)
        cfg['input']['transport'] = {'kind': 'synthetic-local-directory', 'directory': str(self.src)}
        cfg['signing'].update(bootstrapReceipt=bootstrap(publicIdentity={'otaCertificateSha256': self.ota_cert,
            'avbPublicKeySha256': self.avb, 'members': []}), otatoolsProvenance={'synthetic': True},
            releaseScriptProvenance={'synthetic': True})
        self.product, notes = notes_history(self)
        cfg['publication'].update(feedChannel='stable', notes=notes)
        # OCT30-INFRA-S018: the complete SYNTHETIC upstream contract (absence); the real check_updater runs on the signed zip.
        cfg['verification']['updater']['included'] = False
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
        for name in ('check_build_identity', 'check_apps', 'check_image_bytes', 'check_ota', 'check_payload',
                     'check_vbmeta'):
            self.mock_check(name, [])
        for name in ('check_permission_apex', 'check_systemui_tray'):
            self.mock_check(name, ([], {'synthetic': True}))
        self.consumer = self.t.root/'consumer'
        rr.stage(self.src, self.consumer)
        self.release, self.artifact = self.t.root/'release', self.t.root/'artifact'
        self.adapter = self.module.Fake(self.t.root/'fake')
        (self.t.root/'source-root').mkdir()
        self.env = {'HOME': str(self.t.root), 'ROSE_RETAINED_SOURCE_ROOT': str(self.t.root/'source-root'),
            'GITHUB_SHA': 'f' * 40, 'GITHUB_RUN_ID': '41200000000', 'GITHUB_RUN_ATTEMPT': '1',
            'ROSE_RETAINED_PROOF': str(self.t.root/'proof'/rr.PROOF_FILE)}
        # MOCK S016 helper (agreed interface only), admitted by its own blob in a run-scoped profile copy.
        self.helper_control = self.t.root/'helper-control.json'
        self.helper_control.write_text('{}')
        helper = self.t.root/'fold_tool_proof_mock.py'
        helper.write_text(proof_fixtures.MOCK_HELPER.format(control=str(self.helper_control), lock=str(self.t.root/'mock.lock')))
        prof = copy.deepcopy(rr.profile())
        prof['localProof'].update(helper=str(helper), admittedBlob=proof_fixtures.blob(helper),
            spec={'schema': 'mock.fold-tool-proof-spec', 'profile': 'fold-rango-v1'})
        saved_profile = rr.profile
        rr.profile = lambda cfg=None: prof
        self.addCleanup(setattr, rr, 'profile', saved_profile)
        self.prove()

    def prove(self):
        return rr.prove(self.env, self.t.root/'proof', lock=lambda path: contextlib.nullcontext())

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
        signed_target_files(out, target)
        (public/'rango-ota_update-2026103001.zip').write_bytes(b'SYNTHETIC rango OTA')
        (public/'rango-stable').write_text(f"2026103001 {env['BUILD_DATETIME']} rango {config['publication']['channels']['beta']['feedChannel']}\n")
        for name in ('releasekey.x509.pem', 'avb_pkmd.bin'):
            shutil.copyfile(self.ident/name, public/name)
        (public/'UPDATE.md').write_bytes(host_sign.release_notes(config['host']['sign'], CONFIG['target']['product'], 'rango',
            env['BUILD_NUMBER']))  # host-sign's own notes lookup, from the separately pinned commit
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
        report = rr.publish(self.artifact, adapter=self.adapter, selected=True)
        self.assertEqual((report['promoted'], report.get('finalizedPublic')), (False, True))
        store = self.t.root/'fake/releases/rango-2026103001'
        self.assertEqual(sorted(p.name for p in store.iterdir()), ['public'])
        self.assertIn('finalized public, not Latest', (self.t.root/'fake/events.log').read_text())
        self.assertNotIn('promoted latest', (self.t.root/'fake/events.log').read_text())
        manifest = json.loads((self.artifact/'manifest.json').read_text())
        self.assertEqual((manifest['target']['device'], manifest['identities']['otaCertificateSha256']), ('rango', self.ota_cert))

    def cli(self, *argv):
        """The real CLI, as the workflow runs it (os-release.yml rango-guard/rango-builder steps)."""
        return rr.main(list(argv))

    def test_cli_guard_checks_declarations_and_builder_checks_the_actual_files(self):
        """Consolidated audit defect 2: the hosted guard cannot read the builder's staging directory. --job guard checks
        the complete static config and the declared transport only; --job builder keeps the exact local file checks."""
        builder_path = self.t.root/'pixelbuild/rose-actions/rango-retained/2026103001-transfer007/input'  # never created
        self.cfg['input']['transport'] = {'kind': 'synthetic-local-directory', 'directory': str(builder_path)}
        reads, saved = [], rr.check_directory
        rr.check_directory = lambda *a, **k: reads.append(a) or (_ for _ in ()).throw(AssertionError('guard read the input'))
        try:
            self.assertEqual(self.cli('preflight', '--job', 'guard'), 0)
            self.assertEqual(self.cli('preflight', '--job', 'guard', '--input-dir', str(self.src)), 1)
        finally:
            rr.check_directory = saved
        self.assertEqual(reads, [])
        self.assertFalse(builder_path.exists())
        self.assertEqual(self.cli('preflight', '--job', 'builder'), 1)  # the same missing path refuses on the builder
        for transport, code in ((None, 4), ({'kind': 'synthetic-local-directory', 'directory': 'relative/input'}, 1),
                                ({'directory': str(builder_path)}, 1)):
            self.cfg['input']['transport'] = transport
            with self.subTest(transport=transport):
                self.assertEqual(self.cli('preflight', '--job', 'guard'), code)
                self.assertEqual(self.cli('preflight', '--job', 'builder'), code)
        self.cfg['input']['transport'] = {'kind': 'synthetic-local-directory', 'directory': str(self.src)}
        self.assertEqual(self.cli('preflight', '--job', 'guard'), 0)
        self.assertEqual(self.cli('preflight', '--job', 'builder'), 0)
        receipt_sha = hashlib.sha256((self.src/'rango-retained-input.json').read_bytes()).hexdigest()
        for declared, code in (('0' * 64, 1), (receipt_sha, 0)):  # the declared receipt hash binds the staged receipt
            self.cfg['input']['transport']['receiptSha256'] = declared
            self.assertEqual(self.cli('preflight', '--job', 'builder'), code)
        for name, damage in (('tampered', lambda d: (d/'rango-background-policy.json').write_bytes(b'{"tampered": true}')),
                             ('extra', lambda d: (d/'notes.txt').write_text('x')),
                             ('partial', lambda d: (d/'rango-otatools.zip').unlink()),
                             ('truncated', lambda d: (d/'rango-target_files.zip').write_bytes(b'short'))):
            bad = self.t.root/f'input-{name}'
            shutil.copytree(self.src, bad)
            damage(bad)
            self.cfg['input']['transport'] = {'kind': 'synthetic-local-directory', 'directory': str(bad)}
            with self.subTest(damage=name):
                self.assertEqual(self.cli('preflight', '--job', 'guard'), 0)  # declarations only; bytes are the builder's
                self.assertEqual(self.cli('preflight', '--job', 'builder'), 1)
        self.cfg['input']['transport'] = {'kind': 'synthetic-local-directory', 'directory': str(self.src)}
        consumer = self.t.root/'cli-consumer'
        self.assertEqual(self.cli('stage', '--consumer', str(consumer)), 0)
        self.assertEqual(self.cli('recheck', '--consumer', str(consumer)), 0)
        (consumer/'input/rango-background-policy.json').write_bytes(b'{"tampered": true}')
        self.assertEqual(self.cli('recheck', '--consumer', str(consumer)), 1)

    def test_cli_guard_builder_then_sign_verify_stage_and_hosted_publish(self):
        """The selected contract end to end on synthetic boundaries: CLI guard -> CLI builder preflight -> sign ->
        verify (real upstream Updater check) -> stage (pinned notes) -> hosted publish, never Latest."""
        self.assertEqual(self.cli('preflight', '--job', 'guard'), 0)
        self.assertEqual(self.cli('preflight', '--job', 'builder'), 0)
        self.sign()
        self.assertEqual(rr.verify(self.env, self.consumer, self.release, self.adapter)['verdict'], 'PASS')
        rr.stage_artifact(self.env, self.consumer, self.release, self.artifact, self.adapter)
        self.assertEqual((self.artifact/'assets/UPDATE.md').read_bytes(), NOTES_FILE.read_bytes())
        report = rr.publish(self.artifact, adapter=self.adapter, selected=True)
        self.assertEqual((report['promoted'], report.get('finalizedPublic')), (False, True))
        receipt = json.loads((self.artifact/'assets/verification.json').read_text())
        self.assertEqual((receipt['updater']['mode'], receipt['updater']['roseDiscovery']), ('upstream', False))

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
            rr.publish(self.artifact, promote=True, adapter=self.adapter, selected=True)
        bindings = json.loads((self.artifact/'rango-bindings.json').read_text())
        evil = copy.deepcopy(bindings)
        evil['bootstrapReceipt']['publicIdentity']['otaCertificateSha256'] = FRANKEL['identities']['otaCertificateSha256']
        (self.artifact/'rango-bindings.json').write_text(json.dumps(evil))
        with self.assertRaisesRegex(rr.Refused, 'signer_collision'):
            rr.publish(self.artifact, adapter=self.adapter, selected=True)
        evil = copy.deepcopy(bindings)
        evil['inputReceipt']['device'] = 'frankel'
        (self.artifact/'rango-bindings.json').write_text(json.dumps(evil))
        with self.assertRaisesRegex(rr.Refused, 'input_foreign'):
            rr.publish(self.artifact, adapter=self.adapter, selected=True)
        (self.artifact/'rango-bindings.json').write_text(json.dumps(bindings))
        (self.artifact/'assets/rango-ota_update-2026103001.zip').write_bytes(b'tampered in transfer')
        with self.assertRaises(self.module.CheckFailed):
            rr.publish(self.artifact, adapter=self.adapter, selected=True)
        self.assertFalse((self.t.root/'fake/releases').exists())

    def test_staged_and_published_notes_are_exactly_the_pinned_bytes(self):
        self.sign()
        rr.verify(self.env, self.consumer, self.release, self.adapter)
        notes = self.cfg['publication']['notes']
        good = (self.release/'public/UPDATE.md').read_bytes()
        (self.release/'public/UPDATE.md').write_bytes(good + b'edited\n')
        with self.assertRaisesRegex(rr.Refused, 'notes_drift'):
            rr.stage_artifact(self.env, self.consumer, self.release, self.artifact, self.adapter)
        (self.release/'public/UPDATE.md').write_bytes(good)
        rr.stage_artifact(self.env, self.consumer, self.release, self.artifact, self.adapter)
        self.assertEqual((self.artifact/'assets/UPDATE.md').read_bytes(), NOTES_FILE.read_bytes())
        bindings = json.loads((self.artifact/'rango-bindings.json').read_text())
        self.assertEqual(bindings['notesSource'], notes)
        self.assertNotEqual(bindings['notesSource']['commit'], CONFIG['target']['product'])
        (self.artifact/'rango-bindings.json').write_text(json.dumps(dict(bindings, notesSource=dict(notes, commit='e' * 40))))
        with self.assertRaisesRegex(rr.Refused, 'notes_foreign'):
            rr.publish(self.artifact, adapter=self.adapter, selected=True)
        (self.artifact/'rango-bindings.json').write_text(json.dumps(bindings))
        (self.artifact/'assets/UPDATE.md').write_bytes(b'SYNTHETIC swapped notes for 2026103001\n')
        with self.assertRaisesRegex(rr.Refused, 'notes_drift'):
            rr.publish(self.artifact, adapter=self.adapter, selected=True)
        self.assertFalse((self.t.root/'fake/releases').exists())

    def test_upstream_updater_is_verified_and_recorded_without_rose_discovery(self):
        """OCT30-INFRA-S018: the real check_updater runs on the signed target-files with the upstream contract."""
        self.sign()
        self.assertEqual(rr.verify(self.env, self.consumer, self.release, self.adapter)['verdict'], 'PASS')
        receipt = json.loads((self.release/'public/verification.json').read_text())
        self.assertEqual({k: receipt['updater'][k] for k in ('mode', 'roseDiscovery', 'included', 'recipe')},
            {'mode': 'upstream', 'roseDiscovery': False, 'included': False, 'recipe': '--keep-upstream-updater'})
        self.assertIn('upstream Updater absence (keep-upstream recipe, no Rose discovery)', receipt['checks'])

    def test_an_updater_in_the_image_fails_the_absence_contract(self):
        self.sign()
        with zipfile.ZipFile(self.release/'signed-target_files.zip', 'a') as z:
            z.writestr('SYSTEM/priv-app/Updater/Updater.apk', b'SYNTHETIC upstream updater')
        with self.assertRaisesRegex(self.module.CheckFailed, 'upstream Updater expected absent'):
            rr.verify(self.env, self.consumer, self.release, self.adapter)
        self.assertFalse((self.release/'public/verification.json').exists())

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


class RetainedNotes(unittest.TestCase):
    """Retained notes pin: the immutable product commit has no notes for rango 2026103001, so UPDATE.md is read from a
    separately pinned commit/path/sha256 for the exact build and device; Frankel's candidate-commit lookup is unchanged."""

    def setUp(self):
        self.product, self.pin = notes_history(self)

    def sign(self, **changes):
        return {'updateNotes': dict(self.pin, **changes)}

    def test_product_without_notes_and_separately_pinned_notes_give_the_exact_bytes(self):
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'has no os/release-notes/rango-2026103001.md'):
            host_sign.candidate_file(self.product, 'os/release-notes/rango-2026103001.md', rr.NOTES_REPO[0])  # the defect
        self.assertEqual(host_sign.release_notes(self.sign(), self.product, 'rango', '2026103001'), NOTES_FILE.read_bytes())
        self.assertEqual(rr.notes_bytes(self.pin), NOTES_FILE.read_bytes())

    def test_wrong_hash_build_device_path_or_source_refuses(self):
        for changes, message in (({'sha256': '0' * 64}, 'differ from their sha256'),
                                 ({'build': '2026103002'}, 'not rango 2026103001'),
                                 ({'device': 'frankel'}, 'not rango 2026103001'),
                                 ({'path': 'os/release-notes/frankel-2026102901.md'}, 'differ from their sha256'),
                                 ({'path': 'scripts/host-sign.py'}, 'os/release-notes'),
                                 ({'path': 'os/release-notes/absent.md'}, 'has no os/release-notes/absent.md'),
                                 ({'commit': self.product}, 'has no os/release-notes/rango-2026103001.md'),
                                 ({'commit': self.pin['commit'][:12]}, 'full commit')):
            with self.subTest(changes=changes), self.assertRaisesRegex(host_sign.AdapterFailed, re.escape(message)):
                host_sign.release_notes(self.sign(**changes), self.product, 'rango', '2026103001')
        cfg = copy.deepcopy(CONFIG)
        for changes, category in (({'sha256': '0' * 64}, 'notes_drift'), ({'commit': self.product}, 'notes_unavailable'),
                                  ({'path': 'os/release-notes/frankel-2026102901.md'}, 'notes_drift'),
                                  ({'build': '2026103002'}, 'notes_foreign'), ({'device': 'frankel'}, 'notes_foreign'),
                                  ({'commit': CONFIG['target']['product']}, 'notes_invalid'),
                                  ({'path': 'os/release-notes/../x.md'}, 'notes_invalid'), ({'extra': 1}, 'notes_invalid')):
            cfg['publication']['notes'] = dict(self.pin, **changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(rr.Refused, category):
                rr.notes_bytes(rr.notes_binding(cfg))
        cfg['publication']['notes'] = 'os/release-notes/{device}-{build}.md'  # the product-commit template is not admitted
        with self.assertRaisesRegex(rr.Refused, 'notes_invalid'):
            rr.notes_binding(cfg)

    def test_frankel_default_lookup_is_unchanged(self):
        head = subprocess.check_output(['git', '-C', str(SCRIPTS.parent), 'rev-parse', 'HEAD'], text=True).strip()
        template = {'updateNotes': 'os/release-notes/{device}-{build}.md'}
        self.assertEqual(host_sign.release_notes(template, head, 'frankel', '2026102901'),
            (SCRIPTS.parent/'os/release-notes/frankel-2026102901.md').read_bytes())
        with self.assertRaisesRegex(host_sign.AdapterFailed, f'candidate {head[:12]} has no os/release-notes/frankel-2026109999.md'):
            host_sign.release_notes(template, head, 'frankel', '2026109999')

class CheckedInNotes(unittest.TestCase):
    """The real pinned notes commit in this repository (release-verification checks out full history for it)."""

    def test_checked_in_pin_is_the_reviewed_rango_notes(self):
        pin = rr.notes_binding(CONFIG)
        self.assertEqual((pin['path'], pin['build'], pin['device']), ('os/release-notes/rango-2026103001.md', '2026103001', 'rango'))
        self.assertNotEqual(pin['commit'], CONFIG['target']['product'])
        self.assertEqual(hashlib.sha256((SCRIPTS.parent/pin['path']).read_bytes()).hexdigest(), pin['sha256'])
        # The real pinned commit, as the builder reads it (CI checks out full history for this).
        self.assertEqual(rr.notes_bytes(pin), (SCRIPTS.parent/pin['path']).read_bytes())
        text = NOTES_FILE.read_text()
        for phrase in ('rango 2026103001', 'Manual installation only', 'on-device update', 'has not been accepted on a phone',
                       'never the pixel-ota Latest'):
            self.assertIn(phrase, text)


class CheckedInRecipe(unittest.TestCase):
    """FINAL static data handoff: the bound release.json values, through release-pipeline's own static requirements."""

    def test_bound_expectations_satisfy_the_pipeline_without_frankel_identities(self):
        binding = {'otaCertificateSha256': 'a' * 64, 'avbPublicKeySha256': 'b' * 64}  # SYNTHETIC first-use runtime outputs
        config = rr.pipeline_config(CONFIG, binding)
        module = rr.pipeline()
        self.assertEqual(module.updater_missing(config), [])
        self.assertEqual(module.updater_record(config)['roseDiscovery'], False)
        self.assertEqual(module.permission_apex.missing(config['permissionApex']), [])
        self.assertEqual([k for k in module.SYSTEMUI_TRAY_REQUIRED if not config['systemUiTray'].get(k)], [])
        self.assertEqual([k for k, v in config['host']['tools'].items() if not v], [])
        self.assertEqual(config['target'], {'device': 'rango', 'variant': 'user'})
        self.assertEqual(config['identities'], dict(binding, appSignerSha256='650d47f91b7f5af3c530fef53fe92018aa4521efba065bcb1d6fc9b699060bfb'))
        frankel = json.loads((SCRIPTS.parent/'os/release-pipeline/frankel.json').read_text())['identities']
        self.assertNotIn(frankel['otaCertificateSha256'], json.dumps(CONFIG))
        self.assertNotIn(frankel['avbPublicKeySha256'], json.dumps(CONFIG))
        channel = config['publication']['channels']['beta']
        self.assertEqual((channel['promoteLatest'], channel['feedChannel']), (False, 'beta'))
        receipt = {'build': {'timestamp': 1793328547}}
        cand = rr.candidate(CONFIG, receipt)
        self.assertEqual(f"{cand['build']} {cand['timestamp']} {cand['device']} {channel['feedChannel']}", '2026103001 1793328547 rango beta')
        self.assertEqual(sorted(a[0] for a in config['apps']), ['dev.pixel.backgroundcontrol', 'dev.pixel.calculator.agent', 'dev.pixel.voice'])

    def test_bound_provenance_and_transport_are_exact(self):
        signing, transport = CONFIG['signing'], CONFIG['input']['transport']
        self.assertEqual(transport['directory'], '/home/pixelbuild/rose-actions/rango-retained/2026103001-transfer007/input')
        self.assertEqual(transport['receiptSha256'], '54d4cf665e9bc47d584abda8ab6c130687cc748b7752d156e81e94462bd2173e')
        self.assertEqual(signing['otatoolsProvenance']['sha256'], '22d62464ff3fa74fe3fa2b4c118bba116bc3a3aa504feddcdcf38fc3f3cbb664')
        self.assertEqual(signing['otatoolsProvenance']['resolvedManifestSha256'],
            hashlib.sha256((SCRIPTS.parent/'os/pinned-manifest.xml').read_bytes()).hexdigest())
        self.assertEqual(signing['releaseScriptProvenance']['files']['generate-release.sh'],
            hashlib.sha256((SCRIPTS/'tests/fixtures/permission-apex/script/generate-release.sh').read_bytes()).hexdigest())
        self.assertEqual(signing['releaseScriptProvenance']['files']['common.sh'],
            'd6ff8cd70b065af7e129a95d026ff5ddd8564a6e12fa340cd02fd93deba76262')


class UpdaterContract(unittest.TestCase):
    """OCT30-INFRA-S018: verification.updater is the upstream contract of the keep-upstream-updater recipe, bound to the
    pinned reconcile file by hash; unbound fields are named, contradictions refuse."""

    def cfg(self, **changes):
        cfg = copy.deepcopy(CONFIG)
        cfg['verification']['updater'].update(changes)
        return cfg

    def test_checked_in_contract_is_the_evidenced_absence_of_the_retained_recipe(self):
        spec = CONFIG['verification']['updater']
        self.assertEqual((spec['mode'], spec['included']), ('upstream', False))
        self.assertNotIn('signer', spec)
        self.assertNotIn('resources', spec)
        self.assertEqual(hashlib.sha256((SCRIPTS.parent/spec['recipe']['path']).read_bytes()).hexdigest(), spec['recipe']['sha256'])
        self.assertEqual(rr.updater_contract(CONFIG, check=True), [])
        self.assertEqual(rr.updater_contract(self.cfg(included=None), check=True), ['updater.included'])
        contract = rr.updater_contract(self.cfg(included=False))
        self.assertIn('--keep-upstream-updater', contract['recipeApplyArgs'])
        self.assertNotIn('--updater-url', contract['recipeApplyArgs'])
        self.assertEqual(rr.updater_contract(self.cfg(included=True), check=True), ['updater.signer', 'updater.resources'])
        self.assertEqual(rr.updater_contract(self.cfg(included=True, signer='a' * 64, resources={'string/channel_default': 'x'}),
            check=True), ['updater.resources.string/url'])

    def test_wrong_mode_recipe_or_contradictory_contract_refuses(self):
        pixel10 = hashlib.sha256((SCRIPTS.parent/'os/devices/pixel10.json').read_bytes()).hexdigest()
        for changes, message in (({'mode': 'rose'}, 'must be the upstream contract'), ({'mode': None}, 'must be the upstream contract'),
                                 ({'recipe': {'path': 'os/build-only/rango-reconcile.json', 'sha256': '0' * 64}}, 'differs from its pinned'),
                                 ({'recipe': {'path': '../etc/passwd', 'sha256': '0' * 64}}, 'must name a repository file'),
                                 ({'recipe': {'path': 'os/devices/pixel10.json', 'sha256': pixel10}}, 'not a readable reconcile config'),
                                 ({'included': 'yes'}, 'must be true or false'),
                                 ({'included': False, 'signer': 'a' * 64}, 'conflicting upstream contract'),
                                 ({'channelDefault': 'prod'}, 'hybrid')):
            with self.subTest(message=message), self.assertRaisesRegex(rr.Refused, 'config_invalid: .*' + re.escape(message)):
                rr.updater_contract(self.cfg(**changes))
        cfg = self.cfg(included=False)
        cfg['verification']['expected'] = {'variant': 'user', 'updater': {'mode': 'rose'}}
        with self.assertRaisesRegex(rr.Refused, 'config_invalid'):
            rr.pipeline_config(cfg, {'otaCertificateSha256': 'a' * 64, 'avbPublicKeySha256': 'b' * 64})


class UnboundRoute(unittest.TestCase):
    def test_checked_in_route_refuses_every_step_before_any_signer(self):
        t = Tmp(self)
        rr.stage(t.input(), t.root/'consumer')
        called = []
        # The checked-in recipe is bound to the real retained receipt; a SYNTHETIC staged input refuses before any signer.
        with self.assertRaisesRegex(rr.Refused, 'input_drift'):
            rr.sign({}, t.root/'consumer', t.root/'release', signer=lambda env: called.append(env),
                lock=lambda path: contextlib.nullcontext())
        for step in (lambda: rr.verify({}, t.root/'consumer', t.root/'release'),
                     lambda: rr.stage_artifact({}, t.root/'consumer', t.root/'release', t.root/'artifact')):
            with self.assertRaises(rr.Refused):
                step()
        self.assertEqual(called, [])
        self.assertEqual(rr.main(['stage', '--consumer', str(t.root/'c2')]), 1)  # the builder directory is not here


class SigningOnly(Route):
    """OCT30-INFRA-S017: signing never publishes; the same-run tool proof gates every secret."""

    def test_signing_only_route_completes_without_any_publisher(self):
        published = []
        saved = self.module.publish
        self.module.publish = lambda *a, **k: published.append(a)
        self.addCleanup(setattr, self.module, 'publish', saved)
        result = self.sign()
        self.assertEqual(set(result['tools']), {'generator', 'decrypt-keys', 'avbtool', 'openssl'})
        self.assertEqual(result['tools']['openssl']['trustBasis'], 'local-proof')
        self.assertEqual(rr.verify(self.env, self.consumer, self.release, self.adapter)['verdict'], 'PASS')
        rr.stage_artifact(self.env, self.consumer, self.release, self.artifact, self.adapter)
        self.assertEqual(published, [])
        self.assertFalse((self.t.root/'fake/releases').exists())
        for selected in (False, None, 'true', 1):
            with self.assertRaisesRegex(rr.Refused, 'publication_unselected'):
                rr.publish(self.artifact, adapter=self.adapter, selected=selected)
        self.assertEqual(published, [])
        saved_env = os.environ.get('ROSE_RANGO_PUBLISH')
        os.environ.pop('ROSE_RANGO_PUBLISH', None)
        self.addCleanup(lambda: os.environ.__setitem__('ROSE_RANGO_PUBLISH', saved_env) if saved_env is not None else None)
        self.assertEqual(rr.main(['publish', '--artifact-dir', str(self.artifact)]), 1)  # absent selection refuses

    def refused_before_signer(self, env, category):
        called = []
        with self.assertRaisesRegex(rr.Refused, category):
            rr.sign(env, self.consumer, self.release, signer=lambda env: called.append(env), lock=lambda path: contextlib.nullcontext())
        self.assertEqual(called, [])

    def test_missing_stale_or_drifted_proof_refuses_before_any_secret(self):
        self.refused_before_signer(dict(self.env, ROSE_RETAINED_PROOF=str(self.t.root/'absent.json')), 'tool_proof_missing')
        shutil.rmtree(self.release, ignore_errors=True)
        self.refused_before_signer(dict(self.env, GITHUB_RUN_ID='41200000001'), 'tool_proof_binding')
        shutil.rmtree(self.release, ignore_errors=True)
        self.refused_before_signer(dict(self.env, GITHUB_SHA='e' * 40), 'tool_proof_binding')
        shutil.rmtree(self.release, ignore_errors=True)
        binding = json.loads(Path(self.env['ROSE_RETAINED_PROOF']).read_text())
        binding['proof']['tools']['openssl']['path'] = '/tmp/other-openssl'  # substituted proof
        Path(self.env['ROSE_RETAINED_PROOF']).write_text(json.dumps(binding))
        self.refused_before_signer(self.env, 'tool_proof_binding')

    def test_tool_mutation_after_proof_refuses_before_any_secret(self):
        self.helper_control.write_text(json.dumps({'recheckRefuses': 'changed_after_proof'}))
        self.refused_before_signer(self.env, 'tool_proof_drift')

    def test_refused_proof_binds_nothing(self):
        Path(self.env['ROSE_RETAINED_PROOF']).unlink()
        self.helper_control.write_text(json.dumps({'verifyRefuses': 'package_unavailable'}))
        with self.assertRaisesRegex(rr.Refused, 'tool_proof_refused: package_unavailable'):
            self.prove()
        self.assertFalse(Path(self.env['ROSE_RETAINED_PROOF']).exists())


class FirstUseContract(unittest.TestCase):
    """The proposed first-use identity authority: tested here, refused until explicitly selected."""
    CONTRACT = CONFIG['signing']['identityPolicy']['firstUse']

    def metadata(self, **change):
        rows = {'keys': [{'name': 'projects/protocols-465723/secrets/PIXEL_OS_SIGNING_KEYS_RANGO_V1/versions/1',
                          'state': 'ENABLED', 'createTime': '2026-10-30T07:20:31.120Z'}],
                'passphrase': [{'name': 'projects/protocols-465723/secrets/PIXEL_OS_SIGNING_PASSPHRASE_RANGO_V1/versions/1',
                                'state': 'ENABLED', 'createTime': '2026-10-30T07:20:33Z'}]}
        rows.update(change)
        return rows

    def test_checked_in_policy_is_the_selected_first_use_contract(self):
        # Owner selected 13:07 UTC; the bootstrap-receipt route stays available and unselected first-use still refuses.
        self.assertEqual(CONFIG['signing']['identityPolicy']['mode'], 'first-use-containers')
        self.assertIs(self.CONTRACT['selected'], True)
        self.assertEqual(rr.identity_policy(CONFIG), 'first-use-containers')
        self.assertEqual((self.CONTRACT['bootstrapRun'], self.CONTRACT['bootstrapRecipe'], self.CONTRACT['windowStart'],
            self.CONTRACT['windowEnd']), ('41019390144', '99357a10a2e63021d5df76b962c5aee9e76216d1', '2026-10-30T07:20:15Z',
            '2026-10-30T07:20:44Z'))
        self.assertEqual(rr.identity_policy(bootstrap_mode(copy.deepcopy(CONFIG))), 'bootstrap-receipt')
        cfg = copy.deepcopy(CONFIG)
        cfg['signing']['identityPolicy']['firstUse']['selected'] = False
        with self.assertRaisesRegex(rr.Refused, 'identity_policy_unselected'):
            rr.preflight(cfg=cfg, local=False)
        cfg['signing']['identityPolicy']['firstUse']['selected'] = True
        # Selected: no bootstrap receipt and no identity is required before the protected signing derivation.
        missing = rr.preflight(cfg=cfg, local=False)
        self.assertNotIn('signing.bootstrapReceipt', missing)
        self.assertFalse([m for m in missing if m.startswith('signing.identityPolicy')])
        cfg['signing']['identityPolicy']['mode'] = 'latest'
        with self.assertRaisesRegex(rr.Refused, 'config_invalid'):
            rr.preflight(cfg=cfg, local=False)

    def test_exactly_one_enabled_timely_version_per_container_is_pinned(self):
        self.assertEqual(rr.first_use_admission(self.metadata(), self.CONTRACT), {'keys': '1', 'passphrase': '1'})
        second = self.metadata()['keys'] + [dict(self.metadata()['keys'][0], name=self.metadata()['keys'][0]['name'][:-1] + '2')]
        cases = [(self.metadata(keys=second), 'identity_versions_ambiguous'),
                 (self.metadata(keys=[]), 'identity_versions_ambiguous'),
                 (self.metadata(keys=[dict(self.metadata()['keys'][0], state='DISABLED')]), 'identity_versions_partial'),
                 (self.metadata(keys=[dict(self.metadata()['keys'][0], state='DESTROYED')]), 'identity_versions_partial'),
                 (self.metadata(keys=[dict(self.metadata()['keys'][0], createTime='2026-10-30T07:25:00Z')]), 'identity_versions_untimely'),
                 (self.metadata(keys=[dict(self.metadata()['keys'][0], name='projects/protocols-465723/secrets/PIXEL_OS_SIGNING_KEYS_RANGO_V1/versions/latest')]),
                  'identity_versions_ambiguous'),
                 (self.metadata(passphrase=[dict(self.metadata()['passphrase'][0],
                    name='projects/protocols-465723/secrets/PIXEL_OS_SIGNING_KEYS_V1/versions/1')]), 'identity_versions_ambiguous'),
                 ({'keys': self.metadata()['keys']}, 'identity_metadata_incomplete')]
        for metadata, category in cases:
            with self.assertRaisesRegex(rr.Refused, category):
                rr.first_use_admission(metadata, self.CONTRACT)

    def test_record_is_enrolled_once_atomically_and_matched_later(self):
        t = Tmp(self)
        binding = {'versions': {'keys': '1', 'passphrase': '1'}, 'otaCertificateSha256': 'a' * 64, 'avbPublicKeySha256': 'b' * 64,
            'policy': 'first-use-containers', 'bootstrapRun': '41019390144', 'bootstrapRecipe': 'r' * 40,
            'enrolledBy': {'runId': '1'}}
        directory = t.root/'identity'
        with self.assertRaisesRegex(rr.Refused, 'identity_record_missing'):
            rr.first_use_record(directory, binding, enroll=False)
        self.assertEqual(rr.first_use_record(directory, binding, enroll=True), 'enrolled')
        self.assertEqual(sorted(os.listdir(directory)), ['first-use.json'])  # no partial left behind
        # A later run's own audit fields never make an unchanged identity fail.
        self.assertEqual(rr.first_use_record(directory, dict(binding, enrolledBy={'runId': '2'}), enroll=False), 'matched')
        with self.assertRaisesRegex(rr.Refused, 'identity_already_enrolled'):
            rr.first_use_record(directory, binding, enroll=True)
        for change in ({'otaCertificateSha256': 'c' * 64}, {'versions': {'keys': '2', 'passphrase': '1'}}):
            with self.assertRaisesRegex(rr.Refused, 'identity_conflict'):
                rr.first_use_record(directory, dict(binding, **change), enroll=False)
        self.assertEqual(json.loads((directory/'first-use.json').read_text()), binding)  # never overwritten
        (directory/'first-use.json.partial').write_text('{')
        with self.assertRaisesRegex(rr.Refused, 'identity_record_partial'):
            rr.first_use_record(directory, binding, enroll=False)
        with self.assertRaisesRegex(rr.Refused, 'signer_collision'):
            rr.first_use_record(t.root/'other', dict(binding, otaCertificateSha256=FRANKEL['identities']['otaCertificateSha256']),
                enroll=True)
        (t.root/'torn').mkdir()
        (t.root/'torn/first-use.json').write_text('{"policy": "first-use-containers"')
        with self.assertRaisesRegex(rr.Refused, 'identity_record_partial'):
            rr.read_record(t.root/'torn')

    def test_guard_provenance_is_a_no_call_in_bootstrap_mode_and_checked_when_selected(self):
        calls = []
        saved = rr.RUN_READER[0]
        rr.RUN_READER[0] = lambda env, run_id: calls.append(run_id) or {'id': int(run_id), 'head_sha': self.CONTRACT['bootstrapRecipe'],
            'path': '.github/workflows/os-release.yml', 'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success',
            'repository': {'full_name': 'Immortal-Protocols/RoseOS-1'}}
        self.addCleanup(rr.RUN_READER.__setitem__, 0, saved)
        env = {'GITHUB_RUN_ID': '42', 'GITHUB_REPOSITORY': 'Immortal-Protocols/RoseOS-1'}
        self.assertEqual(rr.bootstrap_provenance(env, bootstrap_mode(copy.deepcopy(CONFIG))),
            {'policy': 'bootstrap-receipt', 'checked': False, 'consumerRunId': '42'})
        self.assertEqual(calls, [])
        cfg = copy.deepcopy(CONFIG)
        cfg['signing']['identityPolicy']['firstUse']['selected'] = False
        with self.assertRaisesRegex(rr.Refused, 'identity_policy_unselected'):
            rr.bootstrap_provenance(env, cfg)
        self.assertEqual(calls, [])
        cfg['signing']['identityPolicy']['firstUse']['selected'] = True
        receipt = rr.bootstrap_provenance(env, cfg)
        self.assertEqual(rr.provenance_receipt({'GITHUB_RUN_ID': '42', 'ROSE_FIRST_USE_PROVENANCE': self.write(receipt)},
            cfg['signing']['identityPolicy']['firstUse'])['checked'], True)
        with self.assertRaisesRegex(rr.Refused, 'identity_provenance'):
            rr.bootstrap_provenance(dict(env, GITHUB_REPOSITORY='fork/RoseOS-1'), cfg)

    def write(self, obj):
        fd, path = tempfile.mkstemp(suffix='.json')
        self.addCleanup(os.unlink, path)
        with os.fdopen(fd, 'w') as f:
            json.dump(obj, f)
        return path

    def test_bootstrap_run_provenance(self):
        run = {'id': 41019390144, 'head_sha': self.CONTRACT['bootstrapRecipe'], 'path': '.github/workflows/os-release.yml',
            'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success',
            'repository': {'full_name': 'Immortal-Protocols/RoseOS-1'}}
        self.assertEqual(rr.check_bootstrap_run(run, self.CONTRACT, 'Immortal-Protocols/RoseOS-1')['runId'], '41019390144')
        for change in ({'id': 1}, {'head_sha': 'f' * 40}, {'conclusion': 'failure'}, {'path': '.github/workflows/other.yml'},
                       {'repository': {'full_name': 'fork/RoseOS-1'}}):
            with self.assertRaisesRegex(rr.Refused, 'identity_provenance'):
                rr.check_bootstrap_run(dict(run, **change), self.CONTRACT, 'Immortal-Protocols/RoseOS-1')


class FirstUseRoute(Route):
    """An EXPLICITLY SELECTED synthetic first-use policy through the real seams: admission and race recheck in the
    retained signing route, key validation and identity derivation inside host-sign's hook, the durable record, then
    release-pipeline verify with that same binding. Metadata, keys and OpenSSL are synthetic; no secret is read."""

    def setUp(self):
        super().setUp()
        contract = self.cfg['signing']['identityPolicy']['firstUse']
        self.cfg['signing']['identityPolicy']['mode'] = 'first-use-containers'
        contract.update(selected=True, recordDir=str(self.t.root/'identity'))
        self.cfg['signing']['bootstrapReceipt'] = None
        self.metadata_calls, self.metadata_override, self.runner_override = [], None, None
        saved = (rr.METADATA[0], rr.KEY_RUNNER[0])
        rr.METADATA[0] = self.fake_metadata
        rr.KEY_RUNNER[0] = self.fake_openssl
        self.addCleanup(rr.METADATA.__setitem__, 0, saved[0])
        self.addCleanup(rr.KEY_RUNNER.__setitem__, 0, saved[1])
        (self.t.root/'provenance.json').write_text(json.dumps({'checked': True, 'consumerRunId': self.env['GITHUB_RUN_ID'],
            'runId': contract['bootstrapRun'], 'recipe': contract['bootstrapRecipe']}))
        self.env.update(ROSE_FIRST_USE_PROVENANCE=str(self.t.root/'provenance.json'), ROSE_FIRST_USE_ENROLL='true')
        self.payload = []

    def fake_metadata(self, project, secret):
        self.metadata_calls.append(secret)
        if self.metadata_override:
            return self.metadata_override(project, secret, len(self.metadata_calls))
        return [{'name': f'projects/{project}/secrets/{secret}/versions/1', 'state': 'ENABLED', 'createTime': '2026-10-30T07:20:30Z'}]

    def fake_openssl(self, argv):
        """MOCK OpenSSL: the 'public half' of a synthetic key or certificate is its file stem."""
        if self.runner_override:
            return self.runner_override(argv)
        return b'PUBLIC ' + Path(argv[argv.index('-in') + 1]).name.split('.')[0].encode()

    def signer(self, env):
        """host-sign's retained seam with its fetch and extraction mocked: the hook runs on a synthetic key set."""
        config, target, otatools = host_sign.retained_inputs(env, rr)
        self.signed_with = config['host']['sign']
        self.payload.append((self.signed_with['secretVersion'], self.signed_with['passphraseSecretVersion']))
        extracted = Path(tempfile.mkdtemp(prefix='keys-', dir=self.t.root))
        keys = extracted/'rango'
        keys.mkdir()
        for name in rr.profile()['keyInventory']['requiredByConsumer']:
            (keys/name).write_bytes(b'SYNTHETIC ' + name.encode())
        shutil.copyfile(self.ident/'releasekey.x509.pem', keys/'releasekey.x509.pem')
        shutil.copyfile(self.ident/'avb_pkmd.bin', keys/'avb_pkmd.bin')
        if getattr(self, 'drop_key', None):
            (keys/self.drop_key).unlink()
        passfile = self.t.root/'passphrase'
        passfile.write_text('synthetic\n')
        config['identities'] = config['deriveIdentities'](extracted, passfile)
        host_sign.host_secrets.discover_key_directory(extracted, config['identities'])  # the real pin check
        shutil.rmtree(extracted)
        out = Path(env['ROSE_RELEASE_DIR'])
        public = out/'public'
        public.mkdir()
        signed_target_files(out, target)
        (public/'rango-ota_update-2026103001.zip').write_bytes(b'SYNTHETIC rango OTA')
        (public/'rango-stable').write_text(f"2026103001 {env['BUILD_DATETIME']} rango stable\n")
        for name in ('releasekey.x509.pem', 'avb_pkmd.bin'):
            shutil.copyfile(self.ident/name, public/name)
        (public/'UPDATE.md').write_bytes(host_sign.release_notes(config['host']['sign'], CONFIG['target']['product'], 'rango',
            env['BUILD_NUMBER']))  # host-sign's own notes lookup, from the separately pinned commit
        return 0

    def record(self):
        return json.loads((self.t.root/'identity/first-use.json').read_text())

    def refuse(self, category):
        # host-sign reports a retained-route refusal raised in its seam as AdapterFailed carrying the same category.
        with self.assertRaisesRegex((rr.Refused, host_sign.host_secrets.AdapterFailed), category):
            self.sign()
        self.assertFalse((self.t.root/'identity/first-use.json').exists())

    def next_run(self, run_id):
        """A later run: fresh release dir and proof, the same durable record, no enrolment."""
        shutil.rmtree(self.release)
        Path(self.env['ROSE_RETAINED_PROOF']).unlink()
        self.env.update(GITHUB_RUN_ID=run_id, ROSE_FIRST_USE_ENROLL='false')
        (self.t.root/'provenance.json').write_text(json.dumps(dict(json.loads((self.t.root/'provenance.json').read_text()),
            consumerRunId=run_id)))
        self.prove()

    def test_bound_fixture_reaches_the_public_never_latest_release(self):
        pass  # the bootstrap-receipt fixture's publication case; first-use publication is covered below without publishing

    def refused(self, category):
        return self.assertRaisesRegex((rr.Refused, host_sign.host_secrets.AdapterFailed), category)

    def test_expected_config_cannot_carry_identities_or_publication(self):
        self.sign()
        binding = rr.resolve_binding(self.env, self.cfg)
        for key, value in (('publication', {}), ('target', {}), ('identities', {'otaCertificateSha256': 'f' * 64})):
            cfg = copy.deepcopy(self.cfg)
            cfg['verification']['expected'][key] = value
            with self.assertRaisesRegex(rr.Refused, 'config_invalid'):
                rr.pipeline_config(cfg, binding)

    def test_promote_tampering_and_foreign_bindings_never_publish(self):
        self.sign()
        rr.verify(self.env, self.consumer, self.release, self.adapter)
        rr.stage_artifact(self.env, self.consumer, self.release, self.artifact, self.adapter)
        bindings = json.loads((self.artifact/'rango-bindings.json').read_text())
        self.assertNotIn('bootstrapReceipt', bindings)
        self.assertEqual(bindings['firstUseBinding'], {k: self.record()[k] for k in rr.IDENTITY_FIELDS})
        with self.assertRaisesRegex(rr.Refused, 'latest_refused'):
            rr.publish(self.artifact, promote=True, adapter=self.adapter, selected=True)
        for field, value, category in (('otaCertificateSha256', FRANKEL['identities']['otaCertificateSha256'], 'signer_collision'),
                                       ('bootstrapRun', '1', 'identity'), ('policy', 'bootstrap-receipt', 'identity')):
            evil = copy.deepcopy(bindings)
            evil['firstUseBinding'][field] = value
            (self.artifact/'rango-bindings.json').write_text(json.dumps(evil))
            with self.assertRaisesRegex(rr.Refused, category):
                rr.publish(self.artifact, adapter=self.adapter, selected=True)
        evil = copy.deepcopy(bindings)
        del evil['firstUseBinding']['versions']
        (self.artifact/'rango-bindings.json').write_text(json.dumps(evil))
        with self.assertRaisesRegex(rr.Refused, 'identity_record_partial'):
            rr.publish(self.artifact, adapter=self.adapter, selected=True)
        self.assertFalse((self.t.root/'fake/releases').exists())

    def test_selected_first_use_enrols_then_signs_and_verifies_with_the_same_binding(self):
        published = []
        saved = self.module.publish
        self.module.publish = lambda *a, **k: published.append(a)
        self.addCleanup(setattr, self.module, 'publish', saved)
        self.sign()
        self.assertEqual(self.payload, [('1', '1')])  # pinned numeric versions, never latest
        record = self.record()
        self.assertEqual((record['policy'], record['versions'], record['otaCertificateSha256'], record['avbPublicKeySha256']),
            ('first-use-containers', {'keys': '1', 'passphrase': '1'}, self.ota_cert, self.avb))
        self.assertNotIn('bootstrapReceipt', record)
        self.assertEqual(rr.verify(self.env, self.consumer, self.release, self.adapter)['verdict'], 'PASS')
        rr.stage_artifact(self.env, self.consumer, self.release, self.artifact, self.adapter)
        bindings = json.loads((self.artifact/'rango-bindings.json').read_text())
        self.assertEqual(sorted(bindings), ['firstUseBinding', 'inputReceipt', 'notesSource'])
        manifest = json.loads((self.artifact/'manifest.json').read_text())
        self.assertEqual(manifest['identities']['otaCertificateSha256'], self.ota_cert)
        self.assertEqual(published, [])
        # A later run matches the record without enrolling again; the record is untouched.
        before = (self.t.root/'identity/first-use.json').read_bytes()
        self.next_run('41200000009')
        self.sign()
        self.assertEqual((self.t.root/'identity/first-use.json').read_bytes(), before)
        self.assertEqual(rr.verify(self.env, self.consumer, self.release, self.adapter)['verdict'], 'PASS')

    def test_unselected_policy_makes_no_metadata_or_secret_call(self):
        self.cfg['signing']['identityPolicy']['firstUse']['selected'] = False
        with self.assertRaisesRegex(rr.Refused, 'identity_policy_unselected'):
            self.sign()
        self.assertEqual((self.metadata_calls, self.payload), ([], []))

    def test_wrong_or_ambiguous_version_metadata_refuses_before_any_payload(self):
        def rows(*versions):
            return lambda project, secret, n: [dict({'name': f'projects/{project}/secrets/{secret}/versions/1', 'state': 'ENABLED',
                'createTime': '2026-10-30T07:20:30Z'}, **v) for v in versions]
        for override, category in ((rows(), 'identity_versions_ambiguous'), (rows({}, {}), 'identity_versions_ambiguous'),
                                   (rows({'state': 'DISABLED'}), 'identity_versions_partial'),
                                   (rows({'state': 'DESTROYED'}), 'identity_versions_partial'),
                                   (rows({'createTime': '2026-10-30T09:00:00Z'}), 'identity_versions_untimely'),
                                   (rows({'name': 'projects/other-project/secrets/X/versions/1'}), 'identity_versions_ambiguous'),
                                   (rows({'name': 'projects/protocols-465723/secrets/PIXEL_OS_SIGNING_KEYS_RANGO_V1/versions/latest'}),
                                    'identity_versions_ambiguous')):
            self.metadata_override = override
            shutil.rmtree(self.release, ignore_errors=True)
            self.refuse(category)
            self.assertEqual(self.payload, [])

    def test_metadata_race_refuses(self):
        def changing(project, secret, n):
            version = '1' if n <= 2 else '2'
            return [{'name': f'projects/{project}/secrets/{secret}/versions/{version}', 'state': 'ENABLED',
                'createTime': '2026-10-30T07:20:30Z'}]
        self.metadata_override = changing
        self.refuse('identity_versions_race')

    def test_missing_or_foreign_provenance_refuses(self):
        self.env['ROSE_FIRST_USE_PROVENANCE'] = str(self.t.root/'absent.json')
        self.refuse('identity_provenance')
        shutil.rmtree(self.release, ignore_errors=True)
        (self.t.root/'provenance.json').write_text(json.dumps({'checked': True, 'consumerRunId': '1', 'runId': '41019390144',
            'recipe': self.cfg['signing']['identityPolicy']['firstUse']['bootstrapRecipe']}))
        self.env['ROSE_FIRST_USE_PROVENANCE'] = str(self.t.root/'provenance.json')
        self.refuse('identity_provenance')

    def test_wrong_passphrase_keypair_or_inventory_refuses_before_any_record(self):
        def wrong_passphrase(argv):
            if 'pkey' in argv:
                raise rr.Refused('identity_keys_invalid: a key does not decrypt with the passphrase or is malformed')
            return b'PUBLIC x'
        def wrong_pair(argv):
            name = Path(argv[argv.index('-in') + 1]).name
            return b'PUBLIC other' if name == 'platform.pk8' else b'PUBLIC ' + name.split('.')[0].encode()
        for runner, category in ((wrong_passphrase, 'identity_keys_invalid'), (wrong_pair, 'identity_keys_pair')):
            self.runner_override = runner
            shutil.rmtree(self.release, ignore_errors=True)
            self.refuse(category)
        self.runner_override, self.drop_key = None, 'media.pk8'
        shutil.rmtree(self.release, ignore_errors=True)
        self.refuse('identity_keys_inventory')

    def test_frankel_collision_refuses(self):
        saved = rr.frankel_identities
        rr.frankel_identities = lambda: {'otaCertificateSha256': self.ota_cert, 'avbPublicKeySha256': 'f' * 64}
        self.addCleanup(setattr, rr, 'frankel_identities', saved)
        self.refuse('signer_collision')

    def test_later_use_refuses_missing_partial_or_conflicting_state(self):
        self.env['ROSE_FIRST_USE_ENROLL'] = 'false'
        self.refuse('identity_record_missing')
        shutil.rmtree(self.release, ignore_errors=True)
        self.env['ROSE_FIRST_USE_ENROLL'] = 'true'
        self.sign()
        self.next_run('41200000010')
        (self.t.root/'identity/first-use.json.partial').write_text('{')
        with self.refused('identity_record_partial'):
            self.sign()
        (self.t.root/'identity/first-use.json.partial').unlink()
        shutil.rmtree(self.release, ignore_errors=True)
        self.metadata_override = lambda project, secret, n: [{'name': f'projects/{project}/secrets/{secret}/versions/3',
            'state': 'ENABLED', 'createTime': '2026-10-30T07:20:30Z'}]
        with self.refused('identity_conflict'):
            self.sign()
        self.metadata_override = None
        shutil.rmtree(self.release, ignore_errors=True)
        record = self.record()
        (self.t.root/'identity/first-use.json').write_text(json.dumps(dict(record, otaCertificateSha256='c' * 64)))
        with self.refused('identity_conflict'):
            self.sign()
        shutil.rmtree(self.release, ignore_errors=True)
        (self.t.root/'identity/first-use.json').write_text(json.dumps(record))
        self.env['ROSE_FIRST_USE_ENROLL'] = 'true'
        with self.refused('identity_already_enrolled'):
            self.sign()


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
        # OCT30-INFRA-S017: publication only on the explicit default-false selection; signing-only stops at the builder.
        self.assertIn("    needs: rango-builder\n    if: inputs.mode == 'rango-retained' && inputs.rango_publish\n", publisher)
        self.assertIn('      rango_publish:\n', WORKFLOW)
        self.assertIn('        type: boolean\n        default: false\n', WORKFLOW.split('      rango_publish:\n', 1)[1][:400])
        self.assertIn('  ROSE_RANGO_PUBLISH: ${{ inputs.rango_publish }}\n', WORKFLOW)
        self.assertIn('[[ "$MODE" == rango-retained || "${ROSE_RANGO_PUBLISH:-false}" == false ]]', guard)
        self.assertNotIn('create-github-app-token', builder)  # no publisher token on the signing job
        self.assertNotIn('PIXEL_OTA', builder)
        order = [builder.index(s) for s in ('rango-retained.py stage --consumer', 'rango-retained.py prove --proof-dir',
            'actions-gcp-login.py login', '--proof-file "$RUNNER_TEMP/rango-proof/fold-tool-proof.json"')]
        self.assertEqual(order, sorted(order))
        self.assertIn('python3 scripts/rango-retained.py publish --artifact-dir "$RUNNER_TEMP/rango-artifact"', publisher)
        self.assertNotIn('--promote', publisher)
        for job in (builder, publisher):
            self.assertNotIn('actions: write', job)
            self.assertNotIn('sign-apk.py', job)
        # OCT30-INFRA-S017-FIX001: the guard checks bootstrap-run provenance and hands it over in-run; enrolment is a
        # default-false input refused outside this mode.
        self.assertIn('      rango_first_use_enroll:\n', WORKFLOW)
        self.assertIn('        type: boolean\n        default: false\n', WORKFLOW.split('      rango_first_use_enroll:\n', 1)[1][:400])
        self.assertIn('  ROSE_FIRST_USE_ENROLL: ${{ inputs.rango_first_use_enroll }}\n', WORKFLOW)
        self.assertIn('[[ "$MODE" == rango-retained || "${ROSE_FIRST_USE_ENROLL:-false}" == false ]]', guard)
        self.assertLess(guard.index('rango-retained.py preflight --job guard'), guard.index('rango-retained.py check-bootstrap-run --out'))
        self.assertIn('name: rango-first-use-provenance-${{ github.run_id }}', guard)
        order = [builder.index(s) for s in ('name: rango-first-use-provenance-${{ github.run_id }}', 'ROSE_FIRST_USE_PROVENANCE=',
            'actions-gcp-login.py login', 'rango-retained.py sign')]
        self.assertEqual(order, sorted(order))
        self.assertNotIn('run-id:', builder)  # same-run provenance only, never another run's artifact
        self.assertIn('          fetch-depth: 0  # the separately pinned release notes are read from their own commit\n', builder)
        self.assertNotIn('actions: read', builder)
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
