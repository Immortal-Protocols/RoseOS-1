"""service-auth-v1 server against disposable synthetic attestation chains (MODELS-ALWAYSON-SERVICE-001).

A synthetic root → intermediate → leaf chain carries a hand-built Key Description extension. It stands in for a
Google-rooted Android attestation; it is not one. The HTTP test drives the real loopback server."""
import base64
import datetime
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'service'))
import auth_service as A  # noqa: E402

NOW = 1_800_000_000


def der(tag, body, constructed=False, cls=0):
    first = (cls << 6) | (0x20 if constructed else 0)
    head = bytes([first | tag]) if tag < 31 else bytes([first | 0x1F]) + bytes(
        [0x80 | (tag >> 7), tag & 0x7F] if tag >= 128 else [tag])
    n = len(body)
    length = bytes([n]) if n < 128 else bytes([0x80 | ((n.bit_length() + 7) // 8)]) + n.to_bytes((n.bit_length() + 7) // 8, 'big')
    return head + length + body


def integer(v):
    return der(0x02, v.to_bytes(max(1, (v.bit_length() + 8) // 8), 'big'))


def enum(v):
    return der(0x0A, bytes([v]))


def octets(b):
    return der(0x04, b)


def seq(*items):
    return der(0x10, b''.join(items), True)


def setof(*items):
    return der(0x11, b''.join(items), True)


def explicit(tag, inner):
    return der(tag, inner, True, cls=2)


def key_description(challenge, package=A.PACKAGE, signer=A.SIGNER_SHA256, level=1, boot=1, app_in_software=True):
    app = seq(setof(seq(octets(package.encode()), integer(1))), setof(octets(bytes.fromhex(signer))))
    rot = seq(octets(b'\x11' * 32), der(0x01, b'\xff'), enum(boot), octets(b'\x22' * 32))
    software = seq(explicit(709, octets(app))) if app_in_software else seq()
    hardware = seq(explicit(704, rot), *([] if app_in_software else [explicit(709, octets(app))]))
    return seq(integer(300), enum(level), integer(300), enum(level), octets(challenge), octets(b''), software, hardware)


def name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def cert(subject_key, issuer_key, subject, issuer, ext=None, serial=None, days=(-1, 365), ca=True):
    b = (x509.CertificateBuilder().subject_name(name(subject)).issuer_name(name(issuer))
         .public_key(subject_key.public_key()).serial_number(serial or x509.random_serial_number())
         .not_valid_before(datetime.datetime.fromtimestamp(NOW + days[0] * 86400, datetime.timezone.utc))
         .not_valid_after(datetime.datetime.fromtimestamp(NOW + days[1] * 86400, datetime.timezone.utc))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if ext is not None:
        b = b.add_extension(x509.UnrecognizedExtension(A.KEY_DESCRIPTION_OID, ext), critical=False)
    return b.sign(issuer_key, hashes.SHA256())


def b64(c):
    return base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode()


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root_key = ec.generate_private_key(ec.SECP384R1())
        cls.root = cert(cls.root_key, cls.root_key, 'Synthetic Root Not Google', 'Synthetic Root Not Google')
        cls.mid_key = ec.generate_private_key(ec.SECP256R1())
        cls.mid = cert(cls.mid_key, cls.root_key, 'Synthetic Intermediate', 'Synthetic Root Not Google')

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='auth-test-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = A.Store(self.tmp)
        self.store.init_secret()
        self.now = NOW
        self.policy = {'roots': {A.spki(self.root.public_key())}, 'revokedSerials': set(),
                       'bootStates': {'Verified', 'SelfSigned'}, 'maxKeys': 3, 'enrollPerDay': 2}
        self.svc = A.Service(self.store, self.policy, clock=lambda: self.now)
        self.device = ec.generate_private_key(ec.SECP256R1())

    def chain(self, challenge, key=None, **kw):
        key = key or self.device
        leaf = cert(key, self.mid_key, 'Android Keystore Key', 'Synthetic Intermediate',
                    ext=kw.pop('ext', None) or key_description(challenge.encode(), **kw), ca=False)
        return [b64(leaf), b64(self.mid), b64(self.root)]

    def signature(self, challenge, key=None):
        return base64.b64encode((key or self.device).sign(challenge.encode(), ec.ECDSA(hashes.SHA256()))).decode()

    def enroll(self, key=None, **kw):
        c = self.svc.challenge()['challenge']
        return self.svc.token({'challenge': c, 'attestation': self.chain(c, key, **kw), 'signature': self.signature(c, key)})

    def refused(self, status, category, fn, *args):
        with self.assertRaises(A.Refused) as ctx:
            fn(*args)
        self.assertEqual((ctx.exception.status, ctx.exception.category), (status, category))


class Protocol(Fixture):
    def test_enroll_then_inference_validation_then_renewal(self):
        t = self.enroll()
        self.assertEqual((t['token_type'], t['expires_in']), ('Bearer', 3600))
        self.assertEqual(t['key_id'], __import__('hashlib').sha256(A.spki(self.device.public_key())).hexdigest())
        self.svc.validate('Bearer ' + t['access_token'])
        self.now += 3601
        self.refused(401, 'invalid_or_expired', self.svc.validate, 'Bearer ' + t['access_token'])
        c = self.svc.challenge()['challenge']
        r = self.svc.token({'challenge': c, 'key_id': t['key_id'], 'signature': self.signature(c)})
        self.svc.validate('Bearer ' + r['access_token'])
        self.assertEqual(len(self.store.registry()['keys']), 1)

    def test_attestation_policy_refusals(self):
        cases = {
            'wrong package': (401, 'application_mismatch', {'package': 'dev.pixel.voice'}),
            'wrong signer': (401, 'application_mismatch', {'signer': 'ab' * 32}),
            'software keystore': (401, 'security_level', {'level': 0}),
            'unverified boot': (401, 'boot_state', {'boot': 2}),
            'failed boot': (401, 'boot_state', {'boot': 3}),
        }
        for label, (status, category, kw) in cases.items():
            with self.subTest(label):
                with self.assertRaises(A.Refused) as ctx:
                    self.enroll(**kw)
                self.assertEqual((ctx.exception.status, ctx.exception.category), (status, category))
        self.assertEqual(self.store.registry()['keys'], {})
        self.enroll(app_in_software=False)  # attestationApplicationId accepted in either list

    def test_chain_challenge_and_signature_refusals(self):
        c = self.svc.challenge()['challenge']
        good = self.chain(c)
        other_root = ec.generate_private_key(ec.SECP384R1())
        rogue_mid = cert(self.mid_key, other_root, 'Synthetic Intermediate', 'Rogue Root')
        rogue_root = cert(other_root, other_root, 'Rogue Root', 'Rogue Root')
        cases = {
            'untrusted root': (401, 'attestation_root_untrusted', [good[0], b64(rogue_mid), b64(rogue_root)]),
            'broken chain': (401, 'attestation_chain_invalid', [good[0], b64(rogue_root)]),
            'leaf only': (400, 'attestation_malformed', good[:1]),
            'not base64': (400, 'attestation_malformed', ['***'] + good[1:]),
        }
        for label, (status, category, chain) in cases.items():
            with self.subTest(label):
                self.refused(status, category, self.svc.token,
                             {'challenge': c, 'attestation': chain, 'signature': self.signature(c)})
        c2 = self.svc.challenge()['challenge']
        self.refused(401, 'challenge_mismatch', self.svc.token,
                     {'challenge': c2, 'attestation': good, 'signature': self.signature(c2)})
        self.refused(401, 'signature_invalid', self.svc.token,
                     {'challenge': c, 'attestation': good, 'signature': self.signature(c2)})
        self.now += 61
        self.refused(401, 'challenge_invalid', self.svc.token,
                     {'challenge': c, 'attestation': good, 'signature': self.signature(c)})
        self.refused(401, 'challenge_invalid', self.svc.token,
                     {'challenge': c[:-2] + 'AA', 'attestation': good, 'signature': self.signature(c)})
        self.refused(400, 'malformed', self.svc.token, {'challenge': c})

    def test_expired_and_revoked_attestation_certificates(self):
        c = self.svc.challenge()['challenge']
        leaf = cert(self.device, self.mid_key, 'k', 'Synthetic Intermediate', ext=key_description(c.encode()),
                    days=(-10, -1), ca=False)
        self.refused(401, 'attestation_expired', self.svc.token,
                     {'challenge': c, 'attestation': [b64(leaf), b64(self.mid), b64(self.root)], 'signature': self.signature(c)})
        self.policy['revokedSerials'] = {format(self.mid.serial_number, 'x')}
        self.refused(403, 'attestation_revoked', self.enroll)

    def test_revocation_rotation_and_enrollment_caps(self):
        t = self.enroll()
        self.enroll()  # the same install enrolling again is idempotent
        self.assertEqual(len(self.store.registry()['keys']), 1)
        self.store.init_secret(rotate=True)  # previous secret still accepted for live tokens
        self.svc.validate('Bearer ' + t['access_token'])
        self.store.init_secret(rotate=True)
        self.refused(401, 'invalid_or_expired', self.svc.validate, 'Bearer ' + t['access_token'])
        t = self.enroll()
        self.store.update_registry(lambda reg: reg['keys'][t['key_id']].update(revoked=True))
        self.refused(403, 'key_revoked', self.svc.validate, 'Bearer ' + t['access_token'])
        c = self.svc.challenge()['challenge']
        self.refused(403, 'key_revoked', self.svc.token, {'challenge': c, 'key_id': t['key_id'], 'signature': self.signature(c)})
        self.refused(403, 'key_revoked', self.enroll)
        self.enroll(key=ec.generate_private_key(ec.SECP256R1()))  # second enrollment today
        self.refused(403, 'enrollment_closed', self.enroll, ec.generate_private_key(ec.SECP256R1()))  # 2/day
        self.now += 86400
        self.store.update_registry(lambda reg: reg.update(enrollmentOpen=False))
        self.refused(403, 'enrollment_closed', self.enroll, ec.generate_private_key(ec.SECP256R1()))
        self.store.update_registry(lambda reg: reg.update(enrollmentOpen=True))
        self.enroll(key=ec.generate_private_key(ec.SECP256R1()))  # third key
        self.refused(403, 'enrollment_closed', self.enroll, ec.generate_private_key(ec.SECP256R1()))  # maxKeys 3

    def test_tokens_and_validation_inputs(self):
        for header in (None, '', 'bearer x', 'Basic abc', 'Bearer ', 'Bearer rgm1.a.b', 'Bearer ' + 'x' * 5000):
            with self.subTest(header=header):
                self.refused(401, 'invalid_or_expired' if header and header.startswith('Bearer ') else 'missing',
                             self.svc.validate, header)
        c = self.svc.challenge()['challenge']
        self.refused(401, 'invalid_or_expired', self.svc.validate, 'Bearer ' + c)  # a challenge is not a token
        self.refused(401, 'key_unknown', self.svc.token, {'challenge': c, 'key_id': '00' * 32, 'signature': self.signature(c)})
        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        leaf = cert(rsa_key, self.mid_key, 'k', 'Synthetic Intermediate', ext=key_description(c.encode()), ca=False)
        self.refused(401, 'key_type', self.svc.token,
                     {'challenge': c, 'attestation': [b64(leaf), b64(self.mid), b64(self.root)], 'signature': 'AA=='})

    def test_uninitialized_secret_is_unavailable(self):
        svc = A.Service(A.Store(self.tmp / 'empty'), self.policy)
        (self.tmp / 'empty').mkdir()
        self.refused(503, 'not_initialized', svc.challenge)


class Http(Fixture):
    def test_loopback_server_endpoints(self):
        server = A.ThreadingHTTPServer(('127.0.0.1', 0), A.handler(self.svc))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_address[1]}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def call(path, body=None, headers=None, ctype='application/json'):
            data = None if body is None else json.dumps(body).encode()
            req = urllib.request.Request(base + path, data=data, headers={**({'Content-Type': ctype} if data else {}), **(headers or {})})
            try:
                with opener.open(req, timeout=10) as r:
                    raw = r.read()
                    return r.status, json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                raw = e.read()
                return e.code, json.loads(raw) if raw else None

        status, c = call('/v1/auth/challenge', {})
        self.assertEqual(status, 200)
        status, t = call('/v1/auth/token', {'challenge': c['challenge'], 'attestation': self.chain(c['challenge']),
                                            'signature': self.signature(c['challenge'])})
        self.assertEqual(status, 200)
        self.assertEqual(call('/_validate', headers={'Authorization': 'Bearer ' + t['access_token']}), (204, None))
        self.assertEqual(call('/_validate'), (401, None))
        self.assertEqual(call('/v1/auth/token', {'x': 1}), (400, {'error': 'malformed'}))
        self.assertEqual(call('/v1/auth/token', {'x': 1}, ctype='text/plain')[0], 400)
        self.assertEqual(call('/v1/other', {})[0], 404)
        self.assertEqual(call('/v1/auth/challenge')[0], 404)  # GET is not a route


class Cli(unittest.TestCase):
    def test_operator_commands_and_serve_refusal(self):
        tmp = Path(tempfile.mkdtemp(prefix='auth-cli-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        args = ['--state-dir', str(tmp / 'state'), '--roots', str(tmp / 'roots'), '--revoked-serials', str(tmp / 'none')]
        (tmp / 'roots').mkdir()
        self.assertEqual(A.main(['serve', *args]), 4)  # no trusted roots installed
        self.assertEqual(A.main(['secret-init', *args]), 0)
        self.assertEqual(A.main(['secret-init', *args]), 1)  # never silently replaced
        self.assertEqual(A.main(['rotate-secret', *args]), 0)
        self.assertEqual(oct((tmp / 'state/secret.json').stat().st_mode & 0o777), '0o600')
        self.assertEqual(A.main(['revoke', 'ab' * 32, *args]), 1)
        self.assertEqual(A.main(['close-enrollment', *args]), 0)
        self.assertFalse(json.loads((tmp / 'state/registry.json').read_text())['enrollmentOpen'])


if __name__ == '__main__':
    unittest.main()
