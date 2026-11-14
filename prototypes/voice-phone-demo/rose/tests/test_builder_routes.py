"""Builder API v1 on the remote listener: mounted only with a builder, and only for signed requests from a paired
device. No network, port or phone: FastAPI's test client against build_remote_app."""
import base64
import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from rose.tests.policy_fixtures import approving_policy
from rose.app_builder import toolchain
from rose.app_builder.service import Builder
from rose.remote import Remote, build_remote_app
from rose.tests.test_app_builder import HAS_SDK, JAVA_HOME, SDK


class Device:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.spki = self.key.public_key().public_bytes(serialization.Encoding.DER,
                                                       serialization.PublicFormat.SubjectPublicKeyInfo)
        self.id = hashlib.sha256(self.spki).hexdigest()

    def headers(self, method, path, pin):
        t = str(int(time.time() * 1000))
        nonce = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b'=').decode()
        msg = ('rose-auth-v1\n' + method + '\n' + path + '\n' + t + '\n' + nonce + '\n' + pin).encode()
        sig = self.key.sign(msg, ec.ECDSA(hashes.SHA256()))
        return {'X-Rose-Device': self.id, 'X-Rose-Time': t, 'X-Rose-Nonce': nonce,
                'X-Rose-Signature': base64.b64encode(sig).decode()}


def label(source, kind, request, cancelled):
    (source / 'res/values/notes.xml').write_text('<resources><string name="note">x</string></resources>')


async def _ws(ws, **kw):
    await ws.close()


def server(**extra):
    return SimpleNamespace(phone=None, serve_audio=_ws, serve_events=_ws, serve_phone=_ws, **extra)


class Routes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / 'remote').mkdir(mode=0o700)
        self.remote = Remote(directory=root / 'remote', name='test', hosts=lambda: [])
        self.device = Device()
        self.remote.devices.enroll(self.device.spki, 'phone', 'Pixel', '100.64.0.2')
        tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=300, key_creation=True)
        self.builder = Builder(root / 'builder', tools, label, policy=approving_policy())
        self.addCleanup(self.builder.ledger.close)
        self.server = server(app_builder=self.builder)
        self.client = TestClient(build_remote_app(self.server, self.remote))

    def tearDown(self):
        self.tmp.cleanup()

    def signed(self, method, path, json=None):
        h = self.device.headers(method, path, self.remote.identity.pin)
        return self.client.request(method, path, json=json, headers=h)

    def test_screening_refusal_and_notes(self):
        r = self.signed('POST', '/builder/v1/projects', {'schemaVersion': 1, 'clientRequestId': 's1',
                                                         'description': 'an iPhone app for my lists'})
        self.assertEqual(r.status_code, 400)
        body = r.json()
        self.assertEqual((body['schemaVersion'], body['code'], body['category']), (1, 'out_of_scope', 'platform'))
        self.assertTrue(body['error'].endswith('Nothing was started.'))
        self.assertEqual(self.signed('GET', '/builder/v1/projects').json()['projects'], [])
        r = self.signed('POST', '/builder/v1/projects', {'schemaVersion': 1, 'clientRequestId': 's2',
                                                         'description': 'chess with a leaderboard'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()['notes']), 1)

    def test_the_default_policy_answers_review_required(self):
        from rose.app_builder import policy
        self.builder.policy = policy.default_policy()
        r = self.signed('POST', '/builder/v1/projects', {'schemaVersion': 1, 'clientRequestId': 'p1',
                                                         'description': 'a stopwatch'})
        self.assertEqual(r.status_code, 400)
        body = r.json()
        self.assertEqual((body['schemaVersion'], body['code'], body['category']), (1, 'review_required', 'unavailable'))
        self.assertTrue(body['error'].endswith('Nothing was started.'))
        self.assertEqual(self.signed('GET', '/builder/v1/projects').json()['projects'], [])

    def test_not_mounted_without_a_builder(self):
        bare = TestClient(build_remote_app(server(), self.remote))
        self.assertEqual(bare.get('/builder/v1/projects').status_code, 404)

    def test_unsigned_and_unknown_devices_are_refused(self):
        self.assertEqual(self.client.get('/builder/v1/projects').status_code, 401)
        stranger = Device()
        h = stranger.headers('GET', '/builder/v1/projects', self.remote.identity.pin)
        r = self.client.get('/builder/v1/projects', headers=h)
        self.assertEqual((r.status_code, r.json()['final']), (401, True))
        h = self.device.headers('GET', '/builder/v1/jobs/x', self.remote.identity.pin)  # signed for another path
        self.assertEqual(self.client.get('/builder/v1/projects', headers=h).status_code, 401)

    def test_create_job_cancel_and_library(self):
        r = self.signed('POST', '/builder/v1/projects',
                        {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'A habit tracker'})
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertEqual(out['schemaVersion'], 1)
        again = self.signed('POST', '/builder/v1/projects',
                            {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'A habit tracker'}).json()
        self.assertEqual(again['projectId'], out['projectId'])
        job = self.signed('GET', f'/builder/v1/jobs/{out["jobId"]}').json()
        self.assertEqual((job['state'], job['attempt']), ('queued', 1))
        lib = self.signed('GET', '/builder/v1/projects').json()
        self.assertEqual(lib['projects'][0]['activeJob'], {'jobId': out['jobId'], 'state': 'queued'})
        r = self.signed('POST', f'/builder/v1/jobs/{out["jobId"]}/cancel', {'schemaVersion': 1})
        self.assertEqual(r.json()['status'], 'accepted')
        self.assertEqual(self.signed('GET', '/builder/v1/jobs/nope').status_code, 404)
        bad = self.signed('POST', '/builder/v1/projects', {'schemaVersion': 1, 'description': 'x'})
        self.assertEqual(bad.status_code, 400)
        h = self.device.headers('POST', '/builder/v1/projects', self.remote.identity.pin)
        big = self.client.post('/builder/v1/projects', content=b'{' + b' ' * 20000 + b'}', headers=h)
        self.assertEqual(big.status_code, 400)
        h = self.device.headers('POST', '/builder/v1/projects', self.remote.identity.pin)
        deep = self.client.post('/builder/v1/projects', content=b'[' * 16000, headers=h)
        self.assertEqual(deep.status_code, 400)
        r = self.client.get('/builder/v1/projects')
        self.assertEqual((r.status_code, r.json()['schemaVersion']), (401, 1))

    def test_artifact_paths_are_not_files(self):
        for sha in ('0' * 64, '..%2F..%2Fetc%2Fpasswd', 'A' * 64):
            self.assertEqual(self.signed('GET', f'/builder/v1/artifacts/{sha}').status_code, 404, sha)

    @unittest.skipUnless(HAS_SDK or os.environ.get('ROSE_REQUIRE_APP_BUILD'), 'needs the pinned Android SDK')
    def test_signed_download_of_a_built_version(self):
        out = self.signed('POST', '/builder/v1/projects',
                          {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'Notes'}).json()
        self.builder.run_next()
        version = self.signed('GET', '/builder/v1/projects').json()['projects'][0]['versions'][0]
        r = self.signed('GET', f'/builder/v1/artifacts/{version["apk"]["sha256"]}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.content), version['apk']['size'])
        self.assertEqual(hashlib.sha256(r.content).hexdigest(), version['apk']['sha256'])
        self.assertEqual(self.client.get(f'/builder/v1/artifacts/{version["apk"]["sha256"]}').status_code, 401)
        receipt = {'schemaVersion': 1, 'projectId': out['projectId'],
                   'packageName': 'app.rose.gen.p' + out['projectId'], 'versionCode': 1,
                   'signerSha256': self.signed('GET', '/builder/v1/projects').json()['projects'][0]['signerSha256'],
                   'status': 'installed', 'detail': ''}
        self.assertEqual(self.signed('POST', '/builder/v1/installs', receipt).json(),
                         {'schemaVersion': 1, 'recorded': True})


if __name__ == '__main__':
    unittest.main()
