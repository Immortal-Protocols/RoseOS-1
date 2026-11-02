"""The pinned v1 wire contract and selected models, and the smoke flow against the stand-in server.

``test_pr288_remote_matches`` imports PR288's own rose/remote.py (no MLX in it) from ROSE_PR288_DIR (the
prototypes/voice-phone-demo directory of a PR288 checkout at the pinned head) and runs a real pair + signed /hello
check through it on this Linux host with a software P-256 key standing in for the phone's Keystore key. It is
skipped, with the reason, when that checkout or PR288's dependencies (fastapi, cryptography, pillow) are not present.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

from fakes import CharTokenizer, FakeVllm  # noqa: E402
from rose_cloud import contract, smoke  # noqa: E402
from rose_cloud.vllm_brain import VllmBrain  # noqa: E402

PR288 = os.environ.get('ROSE_PR288_DIR')


class Pinned(unittest.TestCase):
    def test_selected_models_are_stated_not_substituted(self):
        self.assertEqual(contract.MAC_BRAIN, 'mlx-community/Qwen3.6-35B-A3B-4bit')
        self.assertEqual(contract.BRAIN_BASE[0], 'Qwen/Qwen3.6-35B-A3B')
        self.assertTrue(contract.CLOUD_BRAIN[0].startswith(contract.BRAIN_BASE[0]))  # same model, Qwen's FP8
        for rev in (contract.BRAIN_BASE[1], contract.CLOUD_BRAIN[1], contract.PR288_HEAD):
            self.assertRegex(rev, r'^[0-9a-f]{40}$')
        self.assertIn('fixture-tested only', contract.CAPABILITIES['brain_vision'])  # no vision proof claimed
        self.assertIn('fixture recognizers only', contract.CAPABILITIES['listener'])  # no recognition claimed
        self.assertIn('injected engine only', contract.CAPABILITIES['speaker'])  # no real synthesis claimed
        self.assertIn('>= 0.19.0', contract.BRAIN_PREREQUISITES['backend'])
        self.assertIn('unknown', contract.BRAIN_PREREQUISITES['backend'])

    @unittest.skipUnless(PR288, 'ROSE_PR288_DIR (PR288 prototypes/voice-phone-demo checkout) not set')
    def test_pr288_remote_matches(self):
        try:
            import fastapi  # noqa: F401
            import PIL  # noqa: F401  (PR288's QR png)
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError as e:
            self.skipTest(f'PR288 dependency missing: {e.name}')
        found = contract.check_remote(PR288)
        self.assertEqual((found['mismatches'], found['canonical']), ({}, True))
        sys.path.insert(0, PR288)
        try:
            from rose.remote import Remote, canonical_message
        finally:
            sys.path.remove(PR288)
        with tempfile.TemporaryDirectory() as d:
            remote = Remote(Path(d), name='cloud-test', hosts=lambda: ['10.0.0.5'])  # no host discovery
            invite = remote.new_invite()
            code = invite['uri'].split('code=')[1]
            key = ec.generate_private_key(ec.SECP256R1())
            spki = key.public_key().public_bytes(serialization.Encoding.DER,
                                                 serialization.PublicFormat.SubjectPublicKeyInfo)
            status, body = remote.pair(json.dumps({'code': code, 'publicKey': base64.b64encode(spki).decode(),
                                                   'name': 'Pixel', 'model': 'test'}).encode(), '127.0.0.1')
            self.assertEqual(status, 200)
            pin = invite['pin']
            t, nonce = str(int(remote.now() * 1000)), base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b'=')
            sig = key.sign(canonical_message('GET', '/hello', t, nonce.decode(), pin), ec.ECDSA(hashes.SHA256()))
            headers = {'x-rose-device': body['deviceId'], 'x-rose-time': t, 'x-rose-nonce': nonce.decode(),
                       'x-rose-signature': base64.b64encode(sig).decode()}
            self.assertIsNotNone(remote.authenticate('GET', '/hello', headers, '127.0.0.1').device)
            again = remote.authenticate('GET', '/hello', headers, '127.0.0.1')
            self.assertEqual((again.device, again.reason, again.final), (None, 'replayed nonce', False))


class Smoke(unittest.TestCase):
    def test_smoke_flow_on_the_stand_in(self):
        tok = CharTokenizer()
        fake = FakeVllm([tok.encode('Hi, I am Rose.<|im_end|>')])
        self.addCleanup(fake.close)
        result = smoke.run(VllmBrain(tok, fake.url, 'rose-brain'), max_tokens=48)
        self.assertEqual((result['reply'], result['endedWithImEnd'], result['rewindExact'], result['requests']),
                         ('Hi, I am Rose.', True, True, 1))
        self.assertTrue(fake.requests[0]['prompt'][-len(tok.encode(smoke.GEN_FAST)):] == tok.encode(smoke.GEN_FAST))


if __name__ == '__main__':
    unittest.main()
