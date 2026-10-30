"""OCT30-INFRA-S015: scripts/rango-retained.py, the retained Rango signing and separate-release route.

Synthetic receipts, files and a fake release client only: no host, secret, key, bootstrap, network or publication."""
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


def h(data):
    return hashlib.sha256(data).hexdigest()


def receipt(**change):
    data = {'schema': 'roseos.rango-retained-input.v1', 'device': 'rango', 'product': CONFIG['target']['product'],
        'build': {'number': '2026103001', 'timestamp': 1793330000},
        'source': {'manifestSha256': '1' * 64, 'configSha256': '2' * 64, 'compileReceiptSha256': '3' * 64, 'compileExit': 0,
                   'overlays': [{'path': 'scripts/background_apks.py', 'sha256': '4' * 64}]},
        'artifacts': {'targetFiles': {'name': 'rango-target_files.zip', 'size': len(TARGET), 'sha256': h(TARGET)},
                      'otatools': {'name': 'rango-otatools.zip', 'size': len(OTATOOLS), 'sha256': h(OTATOOLS)}},
        'apps': {'roseApk': {'packageName': 'dev.pixel.voice', 'versionCode': 311, 'sha256': '5' * 64, 'signerSha256': '6' * 64},
                 'policySha256': '7' * 64},
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
        self.assertEqual(record['files'], {'rango-target_files.zip': h(TARGET), 'rango-otatools.zip': h(OTATOOLS)})
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
        cfg['input']['transport'] = 'reviewed-local-directory'
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


class FakeClient:
    """Release API stand-in: assets uploaded, downloaded back byte for byte; Latest is a release attribute."""

    def __init__(self, latest='frankel-2026102806', existing=None, move_latest=False):
        self.releases, self.latest, self.calls, self.move_latest = {}, latest, [], move_latest
        if existing:
            self.releases[existing] = {'id': 1, 'tag_name': existing, 'draft': False, 'assets': []}

    def find_release(self, tag):
        return self.releases.get(tag)

    def create_draft(self, tag, name, body):
        self.calls.append(('create_draft', tag))
        self.releases[tag] = {'id': 7, 'tag_name': tag, 'draft': True, 'assets': [], 'body': body}
        return self.releases[tag]

    def get_release(self, release_id):
        return next(r for r in self.releases.values() if r['id'] == release_id)

    def upload(self, release_id, path):
        release = self.get_release(release_id)
        data = Path(path).read_bytes()
        release['assets'].append({'id': len(release['assets']) + 10, 'name': Path(path).name, 'size': len(data),
            'digest': 'sha256:' + h(data), 'state': 'uploaded', 'data': data})

    def delete_asset(self, asset_id):
        raise AssertionError('nothing to reconcile')

    def download(self, asset_id, destination, size):
        asset = next(a for r in self.releases.values() for a in r['assets'] if a['id'] == asset_id)
        Path(destination).write_bytes(asset['data'])

    def latest_tag(self):
        return self.latest

    def finalize_public(self, release_id):
        self.calls.append(('finalize_public', release_id))
        release = self.get_release(release_id)
        release['draft'] = False
        if self.move_latest:
            self.latest = release['tag_name']
        return release

    def promote(self, release_id):
        raise AssertionError('a Rango release is never promoted')


class SeparateRelease(unittest.TestCase):
    def assets(self, t, names=('rango-ota_update-2026103001.zip', 'rango-stable', 'releasekey.x509.pem', 'avb_pkmd.bin',
                               'SHA256SUMS', 'UPDATE.md', 'verification.json')):
        d = t.root/'assets'
        d.mkdir()
        rows = []
        for name in names:
            (d/name).write_bytes(f'synthetic {name}'.encode())
            rows.append({'name': name, 'size': len(f'synthetic {name}'), 'sha256': h(f'synthetic {name}'.encode())})
        return d, {'assets': rows}

    def test_verified_draft_becomes_public_without_changing_latest(self):
        t = Tmp(self)
        d, manifest = self.assets(t)
        client = FakeClient()
        result = rr.publish_separate(client, CONFIG, '2026103001', 'notes', manifest, d, t.root/'download')
        self.assertEqual(result['tag'], 'rango-2026103001')
        self.assertEqual((result['latestBefore'], result['latestAfter']), ('frankel-2026102806', 'frankel-2026102806'))
        self.assertEqual(client.calls, [('create_draft', 'rango-2026103001'), ('finalize_public', 7)])
        self.assertEqual(sorted(p.name for p in (t.root/'download').iterdir()), sorted(r['name'] for r in manifest['assets']))

    def test_latest_is_never_changed_even_under_promote_or_config_mutation(self):
        t = Tmp(self)
        d, manifest = self.assets(t)
        with self.assertRaisesRegex(rr.Refused, 'latest_refused'):
            rr.publish_separate(FakeClient(), CONFIG, '2026103001', 'n', manifest, d, t.root/'x', promote=True)
        evil = copy.deepcopy(CONFIG)
        evil['publication']['latest'] = True
        with self.assertRaisesRegex(rr.Refused, 'config_invalid'):
            rr.publish_separate(FakeClient(), evil, '2026103001', 'n', manifest, d, t.root/'y')
        with self.assertRaisesRegex(rr.Refused, 'latest_changed'):
            rr.publish_separate(FakeClient(move_latest=True), CONFIG, '2026103001', 'n', manifest, d, t.root/'z')
        with self.assertRaisesRegex(rr.Refused, 'release_exists'):
            rr.publish_separate(FakeClient(existing='rango-2026103001'), CONFIG, '2026103001', 'n', manifest, d, t.root/'w')
        with self.assertRaisesRegex(rr.Refused, 'input_foreign'):
            rr.publish_separate(FakeClient(), CONFIG, '2026103002', 'n', manifest, d, t.root/'v')

    def test_assets_must_be_rango_prefixed(self):
        t = Tmp(self)
        d, manifest = self.assets(t, names=('frankel-ota_update-2026103001.zip', 'SHA256SUMS'))
        with self.assertRaisesRegex(rr.Refused, 'asset_foreign'):
            rr.publish_separate(FakeClient(), CONFIG, '2026103001', 'n', manifest, d, t.root/'download')

    def test_download_mismatch_stops_before_public_finalization(self):
        t = Tmp(self)
        d, manifest = self.assets(t)
        client = FakeClient()
        original = client.download
        client.download = lambda asset_id, destination, size: (original(asset_id, destination, size),
            Path(destination).write_bytes(b'corrupted'))
        with self.assertRaises(Exception):
            rr.publish_separate(client, CONFIG, '2026103001', 'n', manifest, d, t.root/'download')
        self.assertNotIn(('finalize_public', 7), client.calls)
        self.assertTrue(client.releases['rango-2026103001']['draft'])

    def test_public_finalization_never_requests_latest(self):
        text = (SCRIPTS/'github_release_publisher.py').read_text()
        body = text.split('def finalize_public', 1)[1].split('\n    def ', 1)[0]
        self.assertIn("body={'draft': False, 'prerelease': False, 'make_latest': 'false'}", body)


def jobs(text):
    body = text.split('\njobs:\n', 1)[1]
    return {m[1]: m[2] for m in re.finditer(r'^  ([\w-]+):\n((?:(?:    .*)?\n)*)', body, re.M)}


class Workflow(unittest.TestCase):
    def test_mode_refuses_on_the_hosted_guard_before_any_controller(self):
        j = jobs(WORKFLOW)
        guard = j['guard']
        self.assertIn('models-sign, rango-retained, ', WORKFLOW)
        self.assertIn('        default: latest-beta\n', WORKFLOW)
        order = [guard.index(s) for s in ('name: Candidate is an exact commit on main', 'python3 scripts/rango-retained.py preflight --job guard',
            'name: Retained Rango runtime is not selected', 'name: Bootstrap preflight')]
        self.assertEqual(order, sorted(order))
        self.assertIn('    needs: guard\n', j['controller'])
        for name, body in j.items():
            if name == 'guard':
                continue
            self.assertNotIn('rango-retained', body, name)  # no controller, builder, signer or publisher job in this mode
        self.assertIn('elif [[ "$MODE" == rango-retained ]]; then', guard)

    def test_actual_ci_runs_this_suite(self):
        ci = (ROOT/'.github/workflows/release-verification.yml').read_text()
        self.assertIn('          python3 scripts/tests/test_rango_retained.py\n', ci)
        self.assertIn('python3 scripts/tests/test_rango_retained.py\n', (ROOT/'scripts/test-local.sh').read_text())
        for path in ('scripts/rango-retained.py', 'os/retained-rango/**'):
            self.assertEqual(ci.count(f"      - '{path}'\n"), 2, path)


if __name__ == '__main__':
    unittest.main()
