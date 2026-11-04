#!/usr/bin/env python3
"""FACTORY-DELIVERY: the offline delivery verifier, on synthetic files with an injected pin set and a mocked ssh-keygen.
Nothing here downloads an artifact, reads a host or a secret, or runs ssh-keygen."""
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('factory_delivery', ROOT/'scripts/factory-delivery.py')
FD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FD)
BLOB = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes(range(1, 33))
PUBLIC = 'ssh-ed25519 ' + base64.b64encode(BLOB).decode()
PIN = {'principal': 'roseos-factory-images-rango-v1', 'publicKey': PUBLIC}
SIG = b'-----BEGIN SSH SIGNATURE-----\nc3ludGhldGlj\n-----END SSH SIGNATURE-----\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Delivery(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='delivery-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.archive_bytes = b'synthetic retained installer bytes'
        self.installer, self.signature = self.tmp/'installer', self.tmp/'signature'
        (self.installer/'package').mkdir(parents=True)
        self.signature.mkdir()
        (self.installer/'package'/FD.INSTALL).write_bytes(self.archive_bytes)
        (self.installer/'SHA256SUMS').write_text(f'{sha(self.archive_bytes)}  package/{FD.INSTALL}\n')
        (self.signature/f'{FD.INSTALL}.sig').write_bytes(SIG)
        self.write_receipt()
        self.tool = self.tmp/'ssh-keygen'
        self.tool.write_bytes(b'stand-in verifier, never executed')
        self.calls = []
        self.retained = {'runId': '41515240855', 'attempt': '1', 'bytes': len(self.archive_bytes), 'sha256': sha(self.archive_bytes)}
        self.signature_pin = {'runId': None, 'attempt': None, 'sha256': None}

    def write_receipt(self, **change):
        receipt = {'schema': 'roseos.factory-archive-signature.v1', 'device': 'rango', 'build': '2026103001', 'status': 'signed',
                   'archive': {'path': '/home/pixelbuild/rose-actions/x/package/' + FD.INSTALL, 'sha256': sha(self.archive_bytes),
                               'size': len(self.archive_bytes)},
                   'signature': {'file': f'{FD.INSTALL}.sig', 'sha256': sha(SIG), 'size': len(SIG)},
                   'identity': {'principal': PIN['principal'], 'namespace': 'factory images', 'publicKey': PUBLIC}}
        for key, value in change.items():
            receipt[key] = value
        (self.signature/FD.SIGN_RECEIPT).write_text(json.dumps(receipt))

    def runner(self, verify='ok', negative='nonzero_exit', mutate=False):
        def run(argv, env, cwd, seconds, stdin_path=None):
            namespace = argv[argv.index('-n') + 1]
            self.calls.append({'argv': argv, 'stdin': stdin_path, 'namespace': namespace, 'env': env})
            if mutate and namespace == 'file':
                Path(stdin_path).write_bytes(b'changed')
            return {'exit': 0 if (verify if namespace == 'factory images' else negative) == 'ok' else 1,
                    'category': verify if namespace == 'factory images' else negative, 'outputBytes': 0}
        return run

    def verify(self, runner=None, pins=None, out=None):
        out = out or self.tmp/f'receipt-{len(list(self.tmp.glob("receipt-*")))}.json'
        receipt = FD.verify(self.installer, self.signature, out, str(self.tool), {'installerArtifact': 'synthetic'},
                            runner=runner or self.runner(), pins=pins or (self.retained, self.signature_pin, PIN))
        return receipt, json.loads(out.read_text())

    def refused(self, pattern, **kw):
        out = self.tmp/f'refused-{len(list(self.tmp.glob("refused-*")))}.json'
        with self.assertRaisesRegex(FD.Refused, pattern):
            self.verify(out=out, **kw)
        written = json.loads(out.read_text())
        self.assertFalse(written['verified'])
        self.assertTrue(written['problems'][0].startswith(pattern.lstrip('^')))
        return written

    def test_a_complete_delivery_verifies_and_writes_the_receipt(self):
        receipt, written = self.verify()
        self.assertEqual(receipt, written)
        self.assertTrue(written['verified'])
        self.assertEqual(written['archive'], {'file': FD.INSTALL, 'size': len(self.archive_bytes), 'sha256': sha(self.archive_bytes)})
        self.assertEqual(written['signature']['sha256'], sha(SIG))
        self.assertEqual(written['identity']['principal'], PIN['principal'])
        self.assertRegex(written['identity']['fingerprint'], r'^SHA256:[A-Za-z0-9+/]{43}$')
        self.assertEqual(written['verifier']['sha256'], sha(self.tool.read_bytes()))
        self.assertEqual((written['verifier']['verify'], written['verifier']['negativeControl']), ('ok', 'nonzero_exit'))
        self.assertEqual(written['pins'], {'retained': {'runId': '41515240855', 'attempt': '1'}, 'signature': None})
        self.assertIn('not installed, flashed, booted or phone-validated', written['nonClaims'])
        good, bad = self.calls
        self.assertEqual(good['argv'][1:3], ['-Y', 'verify'])
        self.assertEqual((good['namespace'], bad['namespace']), ('factory images', 'file'))
        self.assertEqual(good['stdin'], str(self.installer/'package'/FD.INSTALL))  # the exact archive hashed above
        self.assertEqual(good['env'], FD.factory_signing.CHILD_ENV)

    def test_the_archive_must_be_the_committed_retained_installer(self):
        (self.installer/'package'/FD.INSTALL).write_bytes(b'a different archive of other bytes')
        self.refused('^archive_mismatch')
        self.assertEqual(self.calls, [])

    def test_sums_contents_links_and_signature_shape_are_checked(self):
        (self.installer/'SHA256SUMS').write_text(f'{"0" * 64}  package/{FD.INSTALL}\n')
        self.refused('^sums_mismatch')
        self.setUp()
        (self.installer/'extra.txt').write_text('x')
        self.refused('^unexpected_contents')
        self.setUp()
        real = self.tmp/'elsewhere.zip'
        shutil.move(self.installer/'package'/FD.INSTALL, real)
        (self.installer/'package'/FD.INSTALL).symlink_to(real)
        self.refused('^not_regular')
        self.setUp()
        (self.signature/f'{FD.INSTALL}.sig').write_bytes(b'not a signature')
        self.refused('^signature_invalid')

    def test_a_bound_signature_pin_must_match(self):
        self.signature_pin = {'runId': '41700000003', 'attempt': '1', 'sha256': '0' * 64}
        self.refused('^signature_mismatch')
        self.signature_pin = {'runId': '41700000003', 'attempt': '1', 'sha256': sha(SIG)}
        receipt, _ = self.verify()
        self.assertEqual(receipt['pins']['signature'], {'runId': '41700000003', 'attempt': '1'})

    def test_the_sign_receipt_must_describe_this_archive_signature_and_identity(self):
        for name, change, pattern in (
                ('failed', {'status': 'failed'}, '^sign_receipt_mismatch'),
                ('other archive', {'archive': {'sha256': '0' * 64, 'size': len(self.archive_bytes)}}, '^sign_receipt_mismatch'),
                ('other signature', {'signature': {'file': f'{FD.INSTALL}.sig', 'sha256': '0' * 64}}, '^sign_receipt_mismatch'),
                ('other identity', {'identity': {'principal': 'roseos-factory-images-blazer-v1', 'namespace': 'factory images',
                                                 'publicKey': PUBLIC}}, '^identity_mismatch')):
            with self.subTest(name):
                self.setUp()
                self.write_receipt(**change)
                self.refused(pattern)

    def test_verification_needs_a_pass_and_a_refused_negative_control(self):
        self.refused('^verify_failed', runner=self.runner(verify='nonzero_exit'))
        self.setUp()
        self.refused('^verifier_untrusted', runner=self.runner(negative='ok'))
        self.setUp()
        self.refused('^input_changed', runner=self.runner(mutate=True))

    def test_the_cli_refuses_with_the_committed_pins_until_they_are_bound(self):
        out = self.tmp/'cli.json'
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = FD.main(['verify', '--installer', str(self.installer), '--signature', str(self.signature), '--out', str(out),
                            '--ssh-keygen', str(self.tool)])
        result = json.loads(stdout.getvalue())
        flavor = json.loads((ROOT/'os/flavors/rango.json').read_text())['factorySigning']
        if flavor.get('publicKey') is None:
            self.assertEqual((code, result['verified']), (3, False))
            self.assertIn('identity_unbound', result['refused'])
            self.assertFalse(json.loads(out.read_text())['verified'])
        else:  # once bound, the synthetic archive is never the committed retained installer
            self.assertEqual((code, result['verified']), (3, False))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(FD.main(['verify', '--installer', str(self.installer), '--signature', str(self.signature),
                                      '--out', str(out)]), 2)  # never overwrites a receipt


if __name__ == '__main__':
    unittest.main()
