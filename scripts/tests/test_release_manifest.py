"""Release candidate manifest contract; synthetic files and test identities only, no network."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1]/'release-manifest.py'
spec = importlib.util.spec_from_file_location('release_manifest', SCRIPT)
release_manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_manifest)

SOURCE = '34fa48ff16d046e16c7d680731bf01058d794279'
# Obvious test pins; never production identities.
TEST_IDENTITIES = {
    'apps': [{'packageName': 'dev.example.voice', 'versionCode': 298, 'signerSha256': 'a'*64},
             {'packageName': 'dev.example.calculator', 'versionCode': 4, 'signerSha256': 'a'*64}],
    'otaCertificateSha256': 'b'*64,
    'avbPublicKeySha256': 'c'*64,
}

def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = release_manifest.main([str(a) for a in argv])
        except SystemExit as error:
            code = error.code
    text = out.getvalue() or err.getvalue()
    try:
        return code, json.loads(text)
    except ValueError:
        return code, text

class ReleaseManifestTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.candidate = self.root/'candidate'
        self.candidate.mkdir()
        (self.candidate/'testdev-ota_update-2026102801.zip').write_bytes(b'synthetic ota\n' * 100)
        (self.candidate/'testdev-stable').write_text('2026102801 1793200000 testdev stable\n')
        (self.candidate/'verification.json').write_text('{"published": false, "phoneAcceptance": false}\n')
        (self.candidate/'SHA256SUMS').write_text('synthetic\n')
        (self.root/'resolved-manifest.xml').write_text('<manifest/>\n')
        self.identities = self.write_json('identities.json', TEST_IDENTITIES)
        (self.root/'baseline').write_text('2026102701 1793129847 testdev stable\n')
        self.manifest = self.root/'manifest.json'

    def write_json(self, name, value):
        path = self.root/name
        path.write_text(json.dumps(value))
        return path

    def create(self, output=None, *extra):
        assets = []
        for path in sorted(self.candidate.iterdir()):
            assets += ['--asset', path]
        return run('create', '--source-revision', SOURCE, '--resolved-manifest', self.root/'resolved-manifest.xml',
            '--device', 'testdev', '--variant', 'user', '--channel', 'beta', '--build-number', 2026102801,
            '--build-timestamp', 1793200000, '--identities', self.identities,
            '--output', output or self.manifest, *assets, *extra)

    def verify(self, *extra, expected=None):
        return run('verify', self.manifest, '--dir', self.candidate, '--expected-device', 'testdev',
            '--expected-variant', 'user', '--expected-channel', 'beta',
            '--expected-identities', expected or self.identities, *(extra or ('--baseline-file', self.root/'baseline')))

    def test_create_records_contract_and_is_deterministic(self):
        code, report = self.create()
        self.assertEqual(code, 0, report)
        manifest = json.loads(self.manifest.read_text())
        self.assertEqual(manifest['receiptType'], 'pre-publication')
        self.assertEqual(manifest['source']['revision'], SOURCE)
        self.assertEqual(manifest['target'], {'device': 'testdev', 'variant': 'user'})
        self.assertEqual(manifest['channel'], 'beta')
        self.assertEqual(manifest['feed'], {'metadataAsset': 'testdev-stable', 'feedChannel': 'stable'})
        self.assertEqual([a['name'] for a in manifest['assets']], sorted(p.name for p in self.candidate.iterdir()))
        self.assertEqual(manifest['cryptographicVerification'], 'not-performed')
        self.assertNotIn('createdAt', manifest)
        second = self.root/'again.json'
        self.assertEqual(self.create(second)[0], 0)
        self.assertEqual(second.read_bytes(), self.manifest.read_bytes())

    def test_create_refuses_to_replace_an_existing_receipt(self):
        self.assertEqual(self.create()[0], 0)
        before = self.manifest.read_bytes()
        code, report = self.create()
        self.assertEqual(code, 2)
        self.assertIn('immutable', report['error'])
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith('.')], [])

    def test_create_rejects_symlinked_asset_and_inconsistent_feed_line(self):
        link = self.root/'link.zip'
        link.symlink_to(self.candidate/'testdev-ota_update-2026102801.zip')
        code, report = self.create(None, '--asset', link)
        self.assertEqual(code, 2)
        self.assertIn('regular file', report['error'])
        (self.candidate/'testdev-stable').write_text('2026102701 1793129847 testdev stable\n')
        code, report = self.create()
        self.assertEqual(code, 2)
        self.assertIn('does not describe', report['error'])

    def test_create_rejects_asset_over_release_limit(self):
        limit = release_manifest.MAX_ASSET_BYTES
        self.addCleanup(setattr, release_manifest, 'MAX_ASSET_BYTES', limit)
        release_manifest.MAX_ASSET_BYTES = 100
        code, report = self.create()
        self.assertEqual(code, 2)
        self.assertIn('2 GiB', report['error'])
        self.assertFalse(self.manifest.exists())

    def test_verify_passes_and_writes_immutable_receipt_without_claiming_signatures(self):
        self.create()
        receipt = self.root/'verify-receipt.json'
        code, report = self.verify('--baseline-file', self.root/'baseline', '--receipt', receipt)
        self.assertEqual(code, 0, report)
        self.assertEqual(report['verdict'], 'PASS')
        self.assertEqual(report['cryptographicVerification'], 'not-performed')
        self.assertIn('not cryptographically verified', report['identityStatus'])
        self.assertNotIn('signatureVerified', json.dumps(report))
        self.assertEqual(json.loads(receipt.read_text())['verdict'], 'PASS')

    def test_verify_rejects_extra_missing_mismatched_and_symlinked_assets(self):
        self.create()
        (self.candidate/'extra.txt').write_text('surprise\n')
        (self.candidate/'SHA256SUMS').write_text('changed\n')
        (self.candidate/'verification.json').unlink()
        ota = self.candidate/'testdev-ota_update-2026102801.zip'
        data = bytearray(ota.read_bytes())
        data[0] ^= 1
        ota.write_bytes(bytes(data))
        code, report = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(sorted(report['failures']), sorted([
            'unexpected entry: extra.txt', 'size mismatch: SHA256SUMS', 'missing asset: verification.json',
            'sha256 mismatch: testdev-ota_update-2026102801.zip']))
        (self.candidate/'extra.txt').unlink()
        (self.candidate/'verification.json').symlink_to(self.root/'baseline')
        code, report = self.verify()
        self.assertIn('not a regular file: verification.json', report['failures'])
        self.assertNotIn('missing asset: verification.json', report['failures'])

    def test_verify_rejects_wrong_target_channel_and_identities(self):
        self.create()
        code, report = run('verify', self.manifest, '--dir', self.candidate, '--expected-device', 'otherdev',
            '--expected-channel', 'prod', '--expected-identities', self.identities, '--no-baseline')
        self.assertEqual(code, 1)
        self.assertIn('wrong target device', report['failures'])
        self.assertIn('wrong channel', report['failures'])
        self.assertEqual(report['monotonicCheck'], 'not performed: --no-baseline')
        expected = json.loads(json.dumps(TEST_IDENTITIES))
        expected['apps'][0]['signerSha256'] = 'd'*64
        expected['apps'][1]['versionCode'] = 5
        expected['apps'].append({'packageName': 'dev.example.missing', 'versionCode': 1, 'signerSha256': 'a'*64})
        expected['avbPublicKeySha256'] = 'e'*64
        code, report = self.verify(expected=self.write_json('expected.json', expected))
        self.assertEqual(code, 1)
        self.assertEqual(sorted(report['failures']), sorted([
            'signerSha256 mismatch: dev.example.voice', 'versionCode mismatch: dev.example.calculator',
            'missing app identity: dev.example.missing', 'avbPublicKeySha256 mismatch']))

    def test_verify_reports_missing_claimed_identities_as_failure(self):
        self.create()
        manifest = json.loads(self.manifest.read_text())
        del manifest['identities']
        self.manifest.write_text(json.dumps(manifest))
        code, report = self.verify()
        self.assertEqual(code, 1)
        self.assertTrue(any(f.startswith('claimed identities invalid or missing') for f in report['failures']))

    def test_verify_rejects_non_monotonic_update(self):
        self.create()
        for line in ['2026102801 1793100000 testdev stable', '2026102701 1793300000 testdev stable',
                     '2026102701 1793129847 otherdev stable']:
            (self.root/'baseline').write_text(line+'\n')
            code, report = self.verify()
            self.assertEqual(code, 1, line)
        self.assertEqual(report['failures'], ['baseline device differs from manifest target'])
        (self.root/'baseline').write_text('not a feed line\n')
        self.assertEqual(self.verify()[0], 2)

    def test_verify_rejects_traversal_names_in_a_tampered_manifest(self):
        self.create()
        manifest = json.loads(self.manifest.read_text())
        manifest['assets'][0]['name'] = '../baseline'
        self.manifest.write_text(json.dumps(manifest))
        code, report = self.verify()
        self.assertEqual(code, 2)
        self.assertIn('asset name', report['error'])

    def test_both_verify_modes_reject_incomplete_or_malformed_provenance(self):
        self.create()
        original = json.loads(self.manifest.read_text())
        def without(*path):
            def change(m):
                for key in path[:-1]:
                    m = m[key]
                del m[path[-1]]
            return change
        def setting(value, *path):
            def change(m):
                for key in path[:-1]:
                    m = m[key]
                m[path[-1]] = value
            return change
        cases = {
            'no source': without('source'),
            'no revision': without('source', 'revision'),
            'short revision': setting('34fa48f', 'source', 'revision'),
            'no resolved manifest': without('source', 'resolvedManifestSha256'),
            'uppercase resolved manifest': setting('A'*64, 'source', 'resolvedManifestSha256'),
            'no variant': without('target', 'variant'),
            'bad variant': setting('debug', 'target', 'variant'),
            'no channel': without('channel'),
            'bad channel': setting('stable', 'channel'),
            'string build': setting('2026102801', 'build', 'number'),
            'boolean timestamp': setting(True, 'build', 'timestamp'),
            'empty assets': setting([], 'assets'),
            'asset not object': setting(['x'], 'assets'),
            'negative size': setting(-1, 'assets', 0, 'size'),
            'oversize': setting(2 * 1024**3 + 1, 'assets', 0, 'size'),
            'no feed': without('feed'),
            'foreign metadata asset': setting('otherdev-stable', 'feed', 'metadataAsset'),
            'unlisted metadata asset': setting('testdev-stable', 'feed', 'metadataAsset'),
            'metadata check skipped': setting(None, 'feed', 'metadataAsset'),
            'wrong receipt type': setting('delivery-verification', 'receiptType'),
            'not an object': lambda m: m.clear(),
        }
        downloaded = self.root/'downloaded'
        shutil.copytree(self.candidate, downloaded)
        for label, change in cases.items():
            manifest = json.loads(json.dumps(original))
            if label == 'unlisted metadata asset':
                manifest['assets'] = [a for a in manifest['assets'] if a['name'] != 'testdev-stable']
            change(manifest)
            self.manifest.write_text(json.dumps(manifest))
            code, report = self.verify()
            self.assertEqual(code, 2, label)
            self.assertEqual(report['verdict'], 'INVALID_INPUT', label)
            code, report = run('verify-published', self.manifest, '--dir', downloaded)
            self.assertEqual(code, 2, label)
        self.manifest.write_text('{not json')
        self.assertEqual(self.verify()[0], 2)

    def test_verify_published_accepts_byte_identical_delivery_with_pre_publication_flags(self):
        self.create()
        downloaded = self.root/'downloaded'
        shutil.copytree(self.candidate, downloaded)
        receipt = self.root/'delivery.json'
        code, report = run('verify-published', self.manifest, '--dir', downloaded, '--receipt', receipt,
            '--source', 'https://example.invalid/releases/latest/download/')
        self.assertEqual(code, 0, report)
        self.assertEqual(report['receiptType'], 'delivery-verification')
        self.assertEqual(json.loads(self.manifest.read_text())['receiptType'], 'pre-publication')
        self.assertTrue(receipt.exists())
        (downloaded/'testdev-stable').write_text('2026102801 1793200000 testdev stablX\n')
        code, report = run('verify-published', self.manifest, '--dir', downloaded)
        self.assertEqual(report['failures'], ['sha256 mismatch: testdev-stable'])
        self.assertEqual(run('verify-published', self.manifest, '--dir', downloaded, '--source', 'http://x')[0], 2)

    def test_identities_reject_private_or_malformed_fields(self):
        bad = dict(TEST_IDENTITIES, privateKey='x')
        with self.assertRaises(release_manifest.InputError):
            release_manifest.parse_identities(bad)
        bad = json.loads(json.dumps(TEST_IDENTITIES))
        bad['apps'][0]['signerSha256'] = 'abc'
        with self.assertRaises(release_manifest.InputError):
            release_manifest.parse_identities(bad)

if __name__ == '__main__':
    unittest.main()
