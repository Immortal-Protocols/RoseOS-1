#!/usr/bin/env python3
"""QA-020 release acceptance (verifier-owned): Beta/Latest isolation, device and hash binding, fresh tags and
download checks for the Pixel (frankel) and Fold (rango) publication paths, as black-box fixtures over
release-pipeline.py, rango-retained.py and github_release_publisher.py. An in-memory GitHub stand-in only: no token,
network or release. Passing here is fixture coverage, never a published or installed release."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pipeline = load('release_pipeline_acceptance', SCRIPTS/'release-pipeline.py')
rango = load('rango_retained_release_acceptance', SCRIPTS/'rango-retained.py')
import github_release_publisher as publisher  # noqa: E402

FRANKEL = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
RANGO = json.loads((ROOT/'os/retained-rango/release.json').read_text())
DER = b'QA-020 synthetic release certificate'
CERT = '-----BEGIN CERTIFICATE-----\n' + base64.b64encode(DER).decode() + '\n-----END CERTIFICATE-----\n'
PKMD = b'QA-020 synthetic release AVB key'
SYNTHETIC = {'otaCertificateSha256': hashlib.sha256(DER).hexdigest(), 'avbPublicKeySha256': hashlib.sha256(PKMD).hexdigest()}


class FakeGitHub:
    """pixel-ota releases in memory: draft/public state, assets with digests, and the repository Latest."""

    def __init__(self, latest='frankel-2026102901'):
        self.releases, self.latest, self.next_id, self.calls = {}, latest, 1, []
        self.move_latest_on_finalize = False
        self.corrupt_download = False

    def _release(self, release_id):
        return next(r for r in self.releases.values() if r['id'] == release_id)

    def find_release(self, tag):
        self.calls.append(('find', tag))
        release = self.releases.get(tag)
        return json.loads(json.dumps(release)) if release else None

    def latest_tag(self):
        return self.latest

    def create_draft(self, tag, name, body):
        self.calls.append(('create_draft', tag))
        self.releases[tag] = {'id': self.next_id, 'tag_name': tag, 'draft': True, 'assets': []}
        self.next_id += 1
        return json.loads(json.dumps(self.releases[tag]))

    def get_release(self, release_id):
        return json.loads(json.dumps(self._release(release_id)))

    def delete_asset(self, asset_id):
        self.calls.append(('delete_asset', asset_id))
        for release in self.releases.values():
            release['assets'] = [a for a in release['assets'] if a['id'] != asset_id]

    def upload(self, release_id, path):
        self.calls.append(('upload', Path(path).name))
        data = Path(path).read_bytes()
        self._release(release_id)['assets'].append({'id': self.next_id, 'name': Path(path).name, 'size': len(data),
            'digest': 'sha256:' + hashlib.sha256(data).hexdigest(), 'state': 'uploaded', 'data': data.hex()})
        self.next_id += 1

    def download(self, asset_id, destination, size):
        asset = next(a for r in self.releases.values() for a in r['assets'] if a['id'] == asset_id)
        data = bytes.fromhex(asset['data'])
        Path(destination).write_bytes(data[:-1] + b'X' if self.corrupt_download else data)

    def finalize_public(self, release_id):
        self.calls.append(('finalize_public', release_id))
        release = self._release(release_id)
        release['draft'] = False
        if self.move_latest_on_finalize:
            self.latest = release['tag_name']
        return release

    def promote(self, release_id):
        self.calls.append(('promote', release_id))
        release = self._release(release_id)
        release['draft'] = False
        self.latest = release['tag_name']
        return release


def assets_dir(root, device, build):
    directory = Path(root)/'assets'
    directory.mkdir(parents=True)
    files = {f'{device}-ota_update-{build}.zip': b'QA-020 OTA', f'{device}-stable': f'{build} 1793400000 {device} stable\n'.encode(),
             'releasekey.x509.pem': CERT.encode(), 'avb_pkmd.bin': PKMD}
    for name, data in files.items():
        (directory/name).write_bytes(data)
    manifest = {'assets': [{'name': n, 'size': len(d), 'sha256': hashlib.sha256(d).hexdigest()} for n, d in sorted(files.items())]}
    return directory, manifest


class PublisherFixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='qa020-release-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.github = FakeGitHub()


class FreshTagAndDownloadCheck(PublisherFixture):
    def test_new_release_is_a_draft_until_its_downloaded_bytes_match(self):
        assets, manifest = assets_dir(self.root, 'frankel', '2026103101')
        release, download, already = publisher.publish_release(self.github, 'frankel-2026103101', 't', 'b', manifest, assets,
                                                               self.root/'download')
        self.assertTrue(self.github.releases['frankel-2026103101']['draft'])
        self.assertFalse(already)
        for row in manifest['assets']:
            self.assertEqual(hashlib.sha256((download/row['name']).read_bytes()).hexdigest(), row['sha256'])
        self.assertEqual(self.github.latest, 'frankel-2026102901')

    def test_corrupted_download_refuses_and_nothing_is_made_public(self):
        assets, manifest = assets_dir(self.root, 'frankel', '2026103101')
        self.github.corrupt_download = True
        with self.assertRaisesRegex(publisher.PublishError, 'does not match the candidate'):
            publisher.publish_release(self.github, 'frankel-2026103101', 't', 'b', manifest, assets, self.root/'download')
        self.assertTrue(self.github.releases['frankel-2026103101']['draft'])
        self.assertNotIn('promote', [c[0] for c in self.github.calls])

    def test_a_published_release_with_other_bytes_is_never_modified(self):
        assets, manifest = assets_dir(self.root, 'frankel', '2026103101')
        publisher.publish_release(self.github, 'frankel-2026103101', 't', 'b', manifest, assets, self.root/'d1')
        self.github.releases['frankel-2026103101']['draft'] = False
        changed = json.loads(json.dumps(manifest))
        changed['assets'][0]['sha256'] = '0' * 64
        before = len(self.github.calls)
        with self.assertRaisesRegex(publisher.PublishError, 'differs from this candidate'):
            publisher.publish_release(self.github, 'frankel-2026103101', 't', 'b', changed, assets, self.root/'d2')
        self.assertEqual([c[0] for c in self.github.calls[before:]], ['find'], 'no delete, upload or promotion')


class AdapterIsolation(PublisherFixture):
    """release-pipeline's Production adapter with the in-memory GitHub in place of the real client."""

    def adapter(self, config, cand):
        assets, manifest = assets_dir(self.root, cand['device'], cand['build'])
        receipt_dir = self.root/'artifact'
        receipt_dir.mkdir()
        (receipt_dir/'manifest.json').write_text(json.dumps(manifest))
        adapter = pipeline.Production()
        with mock.patch.dict(os.environ, {'PIXEL_OTA_TOKEN': 'synthetic'}), \
                mock.patch.object(pipeline.github_release_publisher, 'client_from_environment', return_value=self.github):
            draft = adapter.publish(config, cand, assets, receipt_dir, fresh=True)
        return adapter, draft

    def test_rango_finalizes_public_without_moving_latest(self):
        config = {'target': {'device': 'rango'}, 'publication': {'tag': 'rango-{build}', 'channels': {'beta': {'finalizePublic': True}}}}
        cand = {'device': 'rango', 'build': '2026103101', 'sha': 'a' * 40, 'channel': 'beta'}
        adapter, _ = self.adapter(config, cand)
        adapter.finalize_public(config, cand, None)
        self.assertFalse(self.github.releases['rango-2026103101']['draft'])
        self.assertEqual(self.github.latest, 'frankel-2026102901')

    def test_rango_refuses_if_latest_moves_while_finalizing(self):
        config = {'target': {'device': 'rango'}, 'publication': {'tag': 'rango-{build}', 'channels': {'beta': {'finalizePublic': True}}}}
        cand = {'device': 'rango', 'build': '2026103101', 'sha': 'a' * 40, 'channel': 'beta'}
        adapter, _ = self.adapter(config, cand)
        self.github.move_latest_on_finalize = True
        with self.assertRaisesRegex(pipeline.CheckFailed, 'Latest moved'):
            adapter.finalize_public(config, cand, None)

    def test_a_fresh_allocation_refuses_any_existing_tag_even_a_draft(self):
        self.github.create_draft('frankel-2026103101', 'old', 'old draft')
        config = {'target': {'device': 'frankel'}, 'publication': {'tag': 'frankel-{build}', 'channels': {'beta': {}}}}
        cand = {'device': 'frankel', 'build': '2026103101', 'sha': 'a' * 40, 'channel': 'beta'}
        with self.assertRaisesRegex(pipeline.CheckFailed, 'already exists'):
            self.adapter(config, cand)
        self.assertNotIn('upload', [c[0] for c in self.github.calls])


class PublishDecision(unittest.TestCase):
    """release-pipeline publish(): which device may become Latest, and refusal of unbound artifacts or keys."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='qa020-publish-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.tools = []
        patcher = mock.patch.object(pipeline, 'manifest_tool', side_effect=lambda *a: self.tools.append(a[0]) or {})
        patcher.start()
        self.addCleanup(patcher.stop)

    def frankel_config(self):
        config = json.loads(json.dumps(FRANKEL))
        config['identities'] = dict(config['identities'], **SYNTHETIC)
        return config

    def rango_config(self):
        binding = dict(SYNTHETIC)
        return rango.pipeline_config(json.loads(json.dumps(RANGO)), binding)

    def artifact(self, cand, simulated=False, cert=CERT, pkmd=PKMD):
        artifact = self.root/'artifact'
        shutil.rmtree(artifact, ignore_errors=True)
        (artifact/'assets').mkdir(parents=True)
        (artifact/'manifest.json').write_text('{"assets": []}')
        (artifact/'expected-identities.json').write_text('{}')
        (artifact/'assets'/'releasekey.x509.pem').write_text(cert)
        (artifact/'assets'/'avb_pkmd.bin').write_bytes(pkmd)
        (artifact/'pipeline-receipt.json').write_text(json.dumps({'candidate': cand, 'simulated': simulated,
            'manifestSha256': pipeline.sha256_file(artifact/'manifest.json')}))
        return artifact

    def run_publish(self, config, cand, promote, artifact=None, receipt_cand=None):
        artifact = artifact or self.artifact(receipt_cand or cand)
        adapter = mock.Mock(simulated=False)
        adapter.publish.return_value = self.root/'draft'
        args = types.SimpleNamespace(artifact_dir=str(artifact), promote=promote, allocation=None)
        return pipeline.publish(args, config, cand, adapter), adapter

    def frankel_cand(self, **changes):
        return dict({'sha': 'a' * 40, 'build': '2026103101', 'timestamp': '1793400000', 'channel': 'beta', 'device': 'frankel'}, **changes)

    def rango_cand(self):
        return {'sha': 'a' * 40, 'build': '2026103101', 'timestamp': '1793400000', 'channel': 'beta', 'device': 'rango'}

    def test_frankel_becomes_beta_latest_only_when_promoted_after_the_draft_check(self):
        report, adapter = self.run_publish(self.frankel_config(), self.frankel_cand(), promote=True)
        self.assertTrue(report['promoted'])
        adapter.promote.assert_called_once()
        adapter.finalize_public.assert_not_called()
        self.assertEqual(self.tools, ['verify-published', 'verify', 'verify-published'], 'download check before promotion')

    def test_frankel_without_promote_stays_a_verified_draft(self):
        report, adapter = self.run_publish(self.frankel_config(), self.frankel_cand(), promote=False)
        self.assertFalse(report['promoted'])
        adapter.promote.assert_not_called()
        adapter.finalize_public.assert_not_called()

    def test_rango_is_finalized_public_and_never_promoted(self):
        report, adapter = self.run_publish(self.rango_config(), self.rango_cand(), promote=False)
        self.assertEqual((report['promoted'], report.get('finalizedPublic')), (False, True))
        adapter.promote.assert_not_called()
        adapter.finalize_public.assert_called_once()

    def test_rango_promotion_is_refused_before_any_publication(self):
        with self.assertRaisesRegex(pipeline.CheckFailed, 'may never become the repository Latest'):
            self.run_publish(self.rango_config(), self.rango_cand(), promote=True)

    def test_rango_retained_refuses_promotion_and_unselected_publication(self):
        with self.assertRaisesRegex(rango.Refused, 'latest_refused'):
            rango.publish(self.root, promote=True, cfg=RANGO, selected=True)
        with self.assertRaisesRegex(rango.Refused, 'publication_unselected'):
            rango.publish(self.root, promote=False, cfg=RANGO, selected=False)

    def test_changed_device_or_tag_is_refused(self):
        with self.assertRaisesRegex(pipeline.CheckFailed, 'must name the dispatched device'):
            self.run_publish(self.frankel_config(), self.frankel_cand(device='rango'), promote=False)

    def test_artifact_bound_to_another_candidate_is_refused(self):
        with self.assertRaisesRegex(pipeline.CheckFailed, 'does not match this candidate'):
            self.run_publish(self.frankel_config(), self.frankel_cand(), promote=True,
                             receipt_cand=self.frankel_cand(sha='b' * 40))

    def test_simulated_artifact_is_never_published_for_real(self):
        cand = self.frankel_cand()
        with self.assertRaisesRegex(pipeline.CheckFailed, 'simulated'):
            self.run_publish(self.frankel_config(), cand, promote=True, artifact=self.artifact(cand, simulated=True))

    def test_transferred_keys_other_than_the_pins_are_refused_before_publication(self):
        cand = self.frankel_cand()
        with self.assertRaisesRegex(pipeline.CheckFailed, 'do not match the pins'):
            self.run_publish(self.frankel_config(), cand, promote=True, artifact=self.artifact(cand, pkmd=b'other key'))

    def test_real_frankel_pins_refuse_synthetic_keys(self):
        cand = self.frankel_cand()
        with self.assertRaisesRegex(pipeline.CheckFailed, 'do not match the pins'):
            self.run_publish(json.loads(json.dumps(FRANKEL)), cand, promote=True)


class CheckedInPublicationContract(unittest.TestCase):
    """The repository's own publication bindings keep Beta/Latest isolation (static; no release is read)."""

    def test_frankel_beta_is_the_only_latest_channel_and_prod_is_unconfigured(self):
        channels = FRANKEL['publication']['channels']
        self.assertEqual((channels['beta']['promoteLatest'], channels['prod']), (True, None))
        self.assertEqual((FRANKEL['publication']['tag'], pipeline.LATEST_DEVICES), ('frankel-{build}', ('frankel',)))

    def test_rango_is_a_separate_never_latest_release(self):
        self.assertEqual((RANGO['publication']['tag'], RANGO['publication']['latest']), ('rango-{build}', False))
        channel = rango.pipeline_config(json.loads(json.dumps(RANGO)), dict(SYNTHETIC))['publication']['channels']['beta']
        self.assertEqual((channel['promoteLatest'], channel['finalizePublic']), (False, True))

    def test_rango_identity_never_comes_from_the_frankel_pins(self):
        config = rango.pipeline_config(json.loads(json.dumps(RANGO)), dict(SYNTHETIC))
        self.assertEqual({k: config['identities'][k] for k in SYNTHETIC}, SYNTHETIC)
        self.assertEqual(config['identities']['appSignerSha256'], FRANKEL['identities']['appSignerSha256'])


if __name__ == '__main__':
    unittest.main()
