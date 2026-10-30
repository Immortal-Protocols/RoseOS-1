"""service-auth-v1 server against disposable synthetic attestation chains (MODELS-ALWAYSON-SERVICE-001).

A synthetic root → intermediate → leaf chain carries a hand-built Key Description extension. It stands in for a
Google-rooted Android attestation; it is not one. The HTTP test drives the real loopback server."""
import base64
import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock
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


BOOT_KEY = b'\x11' * 32  # an optionally pinned SelfSigned verified-boot key in these fixtures


def key_description(challenge, package=A.PACKAGE, signer=A.SIGNER_SHA256, level=1, boot=1, app_in_software=True,
                    locked=True, boot_key=BOOT_KEY):
    app = seq(setof(seq(octets(package.encode()), integer(1))), setof(octets(bytes.fromhex(signer))))
    rot = seq(octets(boot_key), der(0x01, b'\xff' if locked else b'\x00'), enum(boot), octets(b'\x22' * 32))
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
        self.revoked = set()
        self.policy = {'roots': {A.spki(self.root.public_key())}, 'revoked': lambda: self.revoked,
                       'selfSignedBootKeys': {BOOT_KEY.hex()}, 'maxKeys': 3, 'enrollPerDay': 2}
        self.svc = A.Service(self.store, self.policy, clock=lambda: self.now)
        self.device = ec.generate_private_key(ec.SECP256R1())

    def chain(self, challenge, key=None, **kw):
        key = key or self.device
        leaf = cert(key, self.mid_key, 'Android Keystore Key', 'Synthetic Intermediate',
                    ext=kw.pop('ext', None) or key_description(challenge.encode(), **kw), ca=False)
        return [b64(leaf), b64(self.mid), b64(self.root)]

    def signature(self, challenge, key=None):
        return base64.b64encode((key or self.device).sign(challenge.encode(), ec.ECDSA(hashes.SHA256()))).decode()

    def invite(self, label='test-device', ttl=3600):
        return self.store.create_invite(label, ttl, self.now)

    def enroll(self, key=None, invite=True, **kw):
        c = self.svc.challenge()['challenge']
        body = {'challenge': c, 'attestation': self.chain(c, key, **kw), 'signature': self.signature(c, key)}
        if invite:
            body['invite'] = self.invite() if invite is True else invite
        return self.svc.token(body)

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
            'self-signed with an unpinned boot key': (401, 'boot_state', {'boot_key': b'\x33' * 32}),
            'unlocked bootloader': (401, 'device_unlocked', {'locked': False}),
        }
        for label, (status, category, kw) in cases.items():
            with self.subTest(label):
                with self.assertRaises(A.Refused) as ctx:
                    self.enroll(**kw)
                self.assertEqual((ctx.exception.status, ctx.exception.category), (status, category))
        self.assertEqual(self.store.registry()['keys'], {})
        self.policy.update(maxKeys=10, enrollPerDay=10)
        self.enroll(app_in_software=False)  # attestationApplicationId accepted in either list
        self.policy['selfSignedBootKeys'] = set()  # no pin configured: any locked SelfSigned boot is accepted
        self.enroll(key=ec.generate_private_key(ec.SECP256R1()), boot_key=b'\x55' * 32)
        self.policy['selfSignedBootKeys'] = {BOOT_KEY.hex()}
        self.enroll(key=ec.generate_private_key(ec.SECP256R1()), boot=0, boot_key=b'\x44' * 32)  # Verified: any key

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
        foreign = self.chain('not-a-challenge-from-this-server')
        self.refused(401, 'challenge_mismatch', self.svc.token,
                     {'challenge': c2, 'attestation': foreign, 'signature': self.signature(c2)})
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
        self.revoked = {format(self.mid.serial_number, 'x')}
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


class ClosedContract(Fixture):
    """Manager contract closure 17:13: single-use challenges across restarts, root-side extension bound to proof of
    possession, revocation freshness, and revocation at renewal."""

    def test_challenge_is_single_use_even_after_a_restart(self):
        c = self.svc.challenge()['challenge']
        body = {'challenge': c, 'attestation': self.chain(c), 'signature': self.signature(c), 'invite': self.invite()}
        t = self.svc.token(body)
        self.refused(401, 'challenge_reused', self.svc.token, body)
        restarted = A.Service(A.Store(self.tmp), self.policy, clock=lambda: self.now)
        self.refused(401, 'challenge_reused', restarted.token, body)
        restarted.validate('Bearer ' + t['access_token'])  # live tokens survive a restart
        c2 = restarted.challenge()['challenge']
        renew = {'challenge': c2, 'key_id': t['key_id'], 'signature': self.signature(c2)}
        restarted.token(renew)
        self.refused(401, 'challenge_reused', self.svc.token, renew)
        self.now += 61  # consumed challenges are pruned once expired
        self.store.update_registry(lambda reg: self.svc._commit(reg, {'n': 'x', 'e': self.now + 60}, t['key_id'], None,
                                                                None, set(), self.now))
        self.assertEqual(list(self.store.registry()['usedChallenges']), ['x'])

    def test_the_extension_nearest_the_root_decides_and_its_key_must_prove_possession(self):
        c = self.svc.challenge()['challenge']
        # An attested key (extension for another challenge) issues a leaf that carries a forged, matching extension.
        attest_key = ec.generate_private_key(ec.SECP256R1())
        attest = cert(attest_key, self.mid_key, 'Attest Key', 'Synthetic Intermediate', ext=key_description(b'other'))
        forged = cert(self.device, attest_key, 'Forged Leaf', 'Attest Key', ext=key_description(c.encode()), ca=False)
        chain = [b64(forged), b64(attest), b64(self.mid), b64(self.root)]
        self.refused(401, 'challenge_mismatch', self.svc.token, {'challenge': c, 'attestation': chain, 'signature': self.signature(c)})
        # With a matching root-side extension, the leaf key cannot stand in for the attested key.
        c = self.svc.challenge()['challenge']
        attest = cert(attest_key, self.mid_key, 'Attest Key', 'Synthetic Intermediate', ext=key_description(c.encode()))
        forged = cert(self.device, attest_key, 'Forged Leaf', 'Attest Key', ca=False)
        chain = [b64(forged), b64(attest), b64(self.mid), b64(self.root)]
        self.refused(401, 'signature_invalid', self.svc.token, {'challenge': c, 'attestation': chain, 'signature': self.signature(c)})
        c = self.svc.challenge()['challenge']
        plain = cert(self.device, self.mid_key, 'k', 'Synthetic Intermediate', ca=False)
        self.refused(401, 'key_description_invalid', self.svc.token,
                     {'challenge': c, 'attestation': [b64(plain), b64(self.mid), b64(self.root)], 'signature': self.signature(c)})
        broken = cert(self.device, self.mid_key, 'k', 'Synthetic Intermediate', ext=b'\x30\x03\x02\x01', ca=False)
        self.refused(401, 'key_description_invalid', self.svc.token,
                     {'challenge': c, 'attestation': [b64(broken), b64(self.mid), b64(self.root)], 'signature': self.signature(c)})

    def test_revocation_status_freshness_and_revocation_at_renewal(self):
        status = self.tmp / 'status.json'
        clock = [NOW]
        rs = A.RevocationStatus(status, max_age_s=100, clock=lambda: clock[0])
        self.refused(503, 'revocation_status_missing', rs)
        status.write_text(json.dumps({'entries': {'00ABC': {'status': 'REVOKED'}}}))
        os.utime(status, (NOW, NOW))
        self.assertEqual(rs(), {'abc'})
        clock[0] += 101
        self.refused(503, 'revocation_status_stale', rs)
        os.utime(status, (clock[0], clock[0]))
        status.write_text('not json')
        os.utime(status, (clock[0] + 1, clock[0] + 1))
        self.refused(503, 'revocation_status_invalid', rs)
        self.policy['revoked'] = rs
        self.refused(503, 'revocation_status_invalid', self.enroll)  # enrollment fails closed
        self.policy['revoked'] = lambda: self.revoked
        t = self.enroll()
        self.revoked = {format(self.mid.serial_number, 'x')}  # Google revokes the intermediate after enrollment
        c = self.svc.challenge()['challenge']
        self.refused(403, 'key_revoked', self.svc.token, {'challenge': c, 'key_id': t['key_id'], 'signature': self.signature(c)})
        self.refused(403, 'key_revoked', self.svc.validate, 'Bearer ' + t['access_token'])  # persisted revocation


class Invites(Fixture):
    """Admin-issued, single-use QR enrollment codes (Nathan, 17:38): only a scanned code admits a new device."""

    def test_new_devices_need_a_fresh_unexpired_unused_code(self):
        self.refused(403, 'invite_required', self.enroll, None, False)
        self.refused(403, 'invite_invalid', self.enroll, None, 'rgi1.' + 'A' * 43)
        code = self.invite(label='nathan-pixel')
        t = self.enroll(invite=code)
        self.assertEqual(self.store.registry()['keys'][t['key_id']]['label'], 'nathan-pixel')
        self.enroll(invite=False)  # the same install enrolling again needs no second code
        other = ec.generate_private_key(ec.SECP256R1())
        self.refused(403, 'invite_invalid', self.enroll, other, code)  # single use
        stale = self.invite(ttl=60)
        self.now += 61
        self.refused(403, 'invite_invalid', self.enroll, other, stale)
        c = self.svc.challenge()['challenge']
        self.refused(400, 'malformed', self.svc.token, {'challenge': c, 'attestation': self.chain(c, other),
                                                        'signature': self.signature(c, other), 'invite': 7})
        self.assertEqual(len(self.store.registry()['keys']), 1)
        stored = json.dumps(self.store.registry())
        self.assertNotIn(code, stored)  # only the hash is kept
        self.assertIn(A.hashlib.sha256(code.encode()).hexdigest(), stored)

    def test_a_refused_attestation_does_not_spend_the_code(self):
        code = self.invite()
        c = self.svc.challenge()['challenge']
        self.refused(401, 'boot_state', self.svc.token, {'challenge': c, 'attestation': self.chain(c, boot=2),
                                                         'signature': self.signature(c), 'invite': code})
        self.enroll(invite=code)


class InviteCli(unittest.TestCase):
    def test_admin_issues_lists_and_revokes_by_label(self):
        tmp = Path(tempfile.mkdtemp(prefix='auth-invite-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        args = ['--state-dir', str(tmp / 'state')]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(A.main(['invite', '--label', 'nathan-pixel', '--ttl-hours', '2', *args]), 0)
            self.assertEqual(A.main(['invite', '--uri-only', *args]), 0)
            self.assertEqual(A.main(['invites', *args]), 0)
        lines = out.getvalue().splitlines()
        first = json.loads(lines[0])
        self.assertRegex(first['uri'], r'^dev\.pixel\.models://enroll\?invite=rgi1\.[A-Za-z0-9_-]{43}$')
        self.assertRegex(lines[1], r'^dev\.pixel\.models://enroll\?invite=rgi1\.[A-Za-z0-9_-]{43}$')
        listed = [json.loads(line) for line in lines[2:]]
        self.assertEqual(sorted(o['label'] for o in listed), ['', 'nathan-pixel'])
        self.assertNotIn('rgi1.', ''.join(lines[2:]))  # listings never show a code
        self.assertEqual(A.main(['invite', '--ttl-hours', '0', *args]), 1)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(A.main(['revoke', 'nathan-pixel', *args]), 0)  # unused code withdrawn
        store = A.Store(tmp / 'state')
        svc = A.Service(store, {'roots': set(), 'revoked': set, 'selfSignedBootKeys': set(), 'maxKeys': 5,
                                'enrollPerDay': 5})
        code = first['uri'].split('=', 1)[1]
        offer = store.registry()['invites'][A.hashlib.sha256(code.encode()).hexdigest()]
        self.assertLessEqual(offer['expiresAt'], 0)
        self.assertEqual(A.main(['revoke', 'nobody', *args]), 1)


class FixedAttestationChallenge(Fixture):
    """Android fixes the attestation challenge at key generation. The chain is built ONCE (as the Keystore would) and
    every retry reuses it, signing a fresh challenge. Models Product's uncommitted 5xx, lost response and restart."""

    def generate_key(self):
        """The app's first enrollment step: challenge c1 baked into the key's attestation, once."""
        c1 = self.svc.challenge()['challenge']
        return c1, self.chain(c1)

    def attempt(self, chain, invite=None, svc=None):
        svc = svc or self.svc
        c = svc.challenge()['challenge']  # fresh, signed: proof of possession now
        body = {'challenge': c, 'attestation': chain, 'signature': self.signature(c)}
        if invite:
            body['invite'] = invite
        return svc.token(body)

    def renew(self, key_id, svc=None):
        svc = svc or self.svc
        c = svc.challenge()['challenge']
        return svc.token({'challenge': c, 'key_id': key_id, 'signature': self.signature(c)})

    def test_uncommitted_5xx_then_retry_with_the_same_key_and_chain(self):
        code = self.invite()
        c1, chain = self.generate_key()
        key_id = A.hashlib.sha256(A.spki(self.device.public_key())).hexdigest()
        with mock.patch.object(self.store, '_write', side_effect=OSError('disk')):  # server fails before committing
            with self.assertRaises(OSError):
                self.svc.token({'challenge': c1, 'attestation': chain, 'signature': self.signature(c1), 'invite': code})
        self.refused(401, 'key_unknown', self.renew, key_id)  # not committed: the code is still unspent
        t = self.attempt(chain, code)  # same key, same chain (c1 inside), fresh signed challenge
        self.assertEqual(t['key_id'], key_id)
        self.assertIsNotNone(next(iter(self.store.registry()['invites'].values()))['usedBy'])

    def test_committed_but_response_lost_recovers_without_spending_again(self):
        code = self.invite()
        c1, chain = self.generate_key()
        first = self.svc.token({'challenge': c1, 'attestation': chain, 'signature': self.signature(c1), 'invite': code})
        # the response never arrives; the app kept its key and key_id
        self.assertEqual(self.renew(first['key_id'])['key_id'], first['key_id'])            # (a) renewal
        self.assertEqual(self.attempt(chain, code)['key_id'], first['key_id'])              # (b) same enrollment again
        self.assertEqual(self.attempt(chain)['key_id'], first['key_id'])                    # ...even without the code
        reg = self.store.registry()
        self.assertEqual((len(reg['keys']), sum(reg['days'].values())), (1, 1))  # one key, one enrollment counted

    def test_process_restart_keeps_working_with_the_stored_key_and_chain(self):
        code = self.invite()
        _, chain = self.generate_key()
        restarted = A.Service(A.Store(self.tmp), self.policy, clock=lambda: self.now)
        self.now += 1800  # half an hour later, after the app and server both restarted
        t = self.attempt(chain, code, svc=restarted)
        self.svc.validate('Bearer ' + t['access_token'])

    def test_attested_challenge_must_be_ours_and_recent_and_terminal_states_hold(self):
        code = self.invite()
        _, chain = self.generate_key()
        self.now += A.ATTESTED_CHALLENGE_MAX_AGE_S + 1
        self.refused(401, 'challenge_mismatch', self.attempt, chain, code)  # older than 24 h: a new key is needed
        self.now = NOW
        forged = self.chain('rgc1.e30.' + 'A' * 43)  # not MAC'd by this server
        self.refused(401, 'challenge_mismatch', self.attempt, forged, code)
        _, chain = self.generate_key()
        self.store.init_secret(rotate=True)
        self.store.init_secret(rotate=True)  # the issuing secret is gone
        self.refused(401, 'challenge_mismatch', self.attempt, chain, code)
        _, chain = self.generate_key()
        t = self.attempt(chain, code)
        self.store.update_registry(lambda reg: reg['keys'][t['key_id']].update(revoked=True))
        self.refused(403, 'key_revoked', self.attempt, chain, code)  # revoked stays terminal on retry
        self.refused(403, 'key_revoked', self.renew, t['key_id'])


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
                                            'signature': self.signature(c['challenge']), 'invite': self.invite()})
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
        args = ['--state-dir', str(tmp / 'state'), '--roots', str(tmp / 'roots'), '--revocation-status', str(tmp / 'none'),
                '--self-signed-boot-keys', str(tmp / 'none')]
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
