#!/usr/bin/env python3
"""service-auth-v1 server (workspace/models/contracts/service-auth-v1.md), loopback only.

Nginx proxies POST /v1/auth/challenge and /v1/auth/token here with per-IP limits, and asks GET /_validate
(auth_request) before every inference request. 204 = valid, 401 = missing/expired/invalid, 403 = key revoked.

  serve | secret-init | rotate-secret | revoke KEY_ID | close-enrollment | open-enrollment | status

State (owner-only, under --state-dir): secret.json {current, previous}, registry.json {keys, enrollmentOpen, days}.
Trusted roots: every *.pem/*.der in --roots (Google hardware attestation roots, installed by the operator).
Revocation: Google's attestation status JSON at --revocation-status, refreshed by the operator's timer; enrollment
refuses (503) while it is missing or stale. SelfSigned boots need a pinned verifiedBootKey (--self-signed-boot-keys).
Nothing secret is logged; the request log is disabled.
"""
import argparse
import base64
import contextlib
import datetime
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import time

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

KEY_DESCRIPTION_OID = x509.ObjectIdentifier('1.3.6.1.4.1.11129.2.1.17')
PACKAGE = 'dev.pixel.models'
SIGNER_SHA256 = '066c66d62518d1db6c8a7d40795fcca580412049d2c5a5cce89b7673540108da'
BOOT_STATES = {0: 'Verified', 1: 'SelfSigned', 2: 'Unverified', 3: 'Failed'}
TEE_LEVELS = {1, 2}  # TrustedEnvironment, StrongBox
TOKEN_TTL_S, CHALLENGE_TTL_S = 3600, 60
MAX_BODY = 64 * 1024


class Refused(Exception):
    def __init__(self, status, category):
        super().__init__(category)
        self.status, self.category = status, category


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def unb64u(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


# ---- minimal DER reader for the Key Description extension ----------------------------------------------------------

def tlv(data, i):
    """(class, constructed, tag number, value start, value end) of the element at data[i]."""
    first = data[i]
    cls, constructed, tag = first >> 6, bool(first & 0x20), first & 0x1F
    i += 1
    if tag == 0x1F:
        tag = 0
        while True:
            b = data[i]
            i += 1
            tag = (tag << 7) | (b & 0x7F)
            if not b & 0x80:
                break
    length = data[i]
    i += 1
    if length & 0x80:
        n = length & 0x7F
        if not 1 <= n <= 4:
            raise ValueError('der length')
        length = int.from_bytes(data[i:i + n], 'big')
        i += n
    if i + length > len(data):
        raise ValueError('der overrun')
    return cls, constructed, tag, i, i + length


def children(data, start, end):
    out, i = [], start
    while i < end:
        el = tlv(data, i)
        out.append(el)
        i = el[4]
    return out


def integer(data, el):
    return int.from_bytes(data[el[3]:el[4]], 'big', signed=True)


def key_description(der):
    """The fields of an attestation Key Description that this policy reads."""
    top = tlv(der, 0)
    f = children(der, top[3], top[4])
    if len(f) < 8:
        raise ValueError('key description')
    lists = {}
    for name, el in (('software', f[6]), ('hardware', f[7])):
        lists[name] = {tag: (s, e) for _, _, tag, s, e in children(der, el[3], el[4])}
    out = {'attestationSecurityLevel': integer(der, f[1]), 'keyMintSecurityLevel': integer(der, f[3]),
           'challenge': der[f[4][3]:f[4][4]], 'packages': set(), 'signers': set(), 'bootState': None}
    app = lists['software'].get(709) or lists['hardware'].get(709)
    if app:
        octet = tlv(der, app[0])
        inner = der[octet[3]:octet[4]]
        seq = tlv(inner, 0)
        infos, digests = children(inner, seq[3], seq[4])[:2]
        for info in children(inner, infos[3], infos[4]):
            name = children(inner, info[3], info[4])[0]
            out['packages'].add(inner[name[3]:name[4]].decode('utf-8', 'replace'))
        out['signers'] = {inner[d[3]:d[4]].hex() for d in children(inner, digests[3], digests[4])}
    out.update(bootKey=None, deviceLocked=False)
    rot = lists['hardware'].get(704)
    if rot:
        seq = tlv(der, rot[0])
        fields = children(der, seq[3], seq[4])
        out['bootKey'] = der[fields[0][3]:fields[0][4]].hex()
        out['deviceLocked'] = der[fields[1][3]:fields[1][4]] not in (b'', b'\x00')
        out['bootState'] = BOOT_STATES.get(integer(der, fields[2]))
    return out


# ---- policy --------------------------------------------------------------------------------------------------------

def spki(public_key):
    return public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def load_roots(directory):
    roots = set()
    for p in sorted(Path(directory).iterdir()):
        data = p.read_bytes()
        if p.suffix == '.pem':
            cert = x509.load_pem_x509_certificate(data)
        elif p.suffix == '.der':
            cert = x509.load_der_x509_certificate(data)
        else:
            continue
        roots.add(spki(cert.public_key()))
    return roots


def validity(cert, edge):
    """not_valid_before/after as a timestamp (cryptography 41 on Ubuntu 24.04 has only the naive UTC accessors)."""
    value = getattr(cert, f'not_valid_{edge}_utc', None)
    if value is None:
        value = getattr(cert, f'not_valid_{edge}').replace(tzinfo=datetime.timezone.utc)
    return value.timestamp()


def verify_attestation(chain_b64, challenge, policy, now):
    """The leaf public key of an acceptable attestation chain, else Refused(401|403)."""
    try:
        chain = [x509.load_der_x509_certificate(base64.b64decode(c, validate=True)) for c in chain_b64]
    except Exception:
        raise Refused(400, 'attestation_malformed') from None
    if not 2 <= len(chain) <= 8:
        raise Refused(400, 'attestation_malformed')
    for cert, issuer in zip(chain, chain[1:]):
        try:
            cert.verify_directly_issued_by(issuer)
        except (ValueError, TypeError, InvalidSignature):
            raise Refused(401, 'attestation_chain_invalid') from None
    if spki(chain[-1].public_key()) not in policy['roots']:
        raise Refused(401, 'attestation_root_untrusted')
    revoked = policy['revoked']()  # refuses when the revocation status is missing or stale
    for cert in chain:
        if not validity(cert, 'before') <= now <= validity(cert, 'after'):
            raise Refused(401, 'attestation_expired')
        if format(cert.serial_number, 'x') in revoked:
            raise Refused(403, 'attestation_revoked')
    # Android's guidance: trust the attestation extension nearest the root. Certificates below it could have been
    # issued by an attested key, so their extensions prove nothing. Its certified key must do proof of possession.
    attested = next((c for c in reversed(chain) if KEY_DESCRIPTION_OID in [e.oid for e in c.extensions]), None)
    if attested is None:
        raise Refused(401, 'key_description_invalid')
    key = attested.public_key()
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != 'secp256r1':
        raise Refused(401, 'key_type')
    try:
        desc = key_description(attested.extensions.get_extension_for_oid(KEY_DESCRIPTION_OID).value.value)
    except (ValueError, IndexError):
        raise Refused(401, 'key_description_invalid') from None
    if desc['attestationSecurityLevel'] not in TEE_LEVELS or desc['keyMintSecurityLevel'] not in TEE_LEVELS:
        raise Refused(401, 'security_level')
    if not hmac.compare_digest(desc['challenge'], challenge.encode()):
        raise Refused(401, 'challenge_mismatch')
    if desc['packages'] != {PACKAGE} or desc['signers'] != {SIGNER_SHA256}:
        raise Refused(401, 'application_mismatch')
    if not desc['deviceLocked']:
        raise Refused(401, 'device_unlocked')
    if desc['bootState'] == 'Verified':
        pass
    elif desc['bootState'] == 'SelfSigned' and desc['bootKey'] in policy['selfSignedBootKeys']:
        pass  # a custom OS (RoseOS) only with its approved, pinned verified-boot key
    else:
        raise Refused(401, 'boot_state')
    return key, [format(c.serial_number, 'x') for c in chain]


# ---- state ---------------------------------------------------------------------------------------------------------

class Store:
    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self.secret_path, self.registry_path = self.dir / 'secret.json', self.dir / 'registry.json'
        self.lock_path = self.dir / '.lock'

    @contextlib.contextmanager
    def locked(self):
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _read(self, path, default):
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            return default

    def _write(self, path, data):
        tmp = path.with_name(path.name + '.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, sort_keys=True)
        os.replace(tmp, path)

    def secrets(self):
        s = self._read(self.secret_path, None)
        if not s or not s.get('current'):
            raise Refused(503, 'not_initialized')
        return [bytes.fromhex(s['current'])] + ([bytes.fromhex(s['previous'])] if s.get('previous') else [])

    def registry(self):
        return self._read(self.registry_path, {'keys': {}, 'enrollmentOpen': True, 'days': {}})

    def update_registry(self, fn):
        """fn's changes are written even when it refuses: a consumed challenge or an automatic revocation stays."""
        with self.locked():
            reg = self.registry()
            try:
                return fn(reg)
            finally:
                self._write(self.registry_path, reg)

    def init_secret(self, rotate=False):
        with self.locked():
            old = self._read(self.secret_path, None)
            if old and not rotate:
                raise Refused(409, 'already_initialized')
            if rotate and not old:
                raise Refused(409, 'not_initialized')
            self._write(self.secret_path, {'current': secrets.token_hex(32), 'previous': old['current'] if old else None})


def sign(secret, kind, payload):
    body = b64u(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode())
    return f'{kind}.{body}.' + b64u(hmac.new(secret, f'{kind}.{body}'.encode(), hashlib.sha256).digest())


def opened(secrets_, kind, text, now):
    """The payload of a token/challenge MAC'd by any accepted secret and not expired, else None."""
    parts = text.split('.') if isinstance(text, str) else []
    if len(parts) != 3 or parts[0] != kind or len(text) > 2048:
        return None
    for secret in secrets_:
        mac = b64u(hmac.new(secret, f'{kind}.{parts[1]}'.encode(), hashlib.sha256).digest())
        if hmac.compare_digest(mac, parts[2]):
            try:
                payload = json.loads(unb64u(parts[1]))
            except ValueError:
                return None
            return payload if isinstance(payload, dict) and payload.get('e', 0) > now else None
    return None


# ---- protocol ------------------------------------------------------------------------------------------------------

class Service:
    def __init__(self, store, policy, clock=time.time):
        self.store, self.policy, self.clock = store, policy, clock

    def challenge(self):
        now = self.clock()
        return {'challenge': sign(self.store.secrets()[0], 'rgc1',
                                  {'n': b64u(os.urandom(16)), 'e': int(now) + CHALLENGE_TTL_S}),
                'expires_in': CHALLENGE_TTL_S}

    def token(self, body):
        now = self.clock()
        if not isinstance(body, dict) or not isinstance(body.get('challenge'), str) \
                or not isinstance(body.get('signature'), str):
            raise Refused(400, 'malformed')
        challenge = body['challenge']
        opened_challenge = opened(self.store.secrets(), 'rgc1', challenge, now)
        if opened_challenge is None:
            raise Refused(401, 'challenge_invalid')
        serials = None
        if 'attestation' in body:
            if not isinstance(body['attestation'], list):
                raise Refused(400, 'malformed')
            public_key, serials = verify_attestation(body['attestation'], challenge, self.policy, now)
            der = spki(public_key)
            key_id = hashlib.sha256(der).hexdigest()
        elif isinstance(body.get('key_id'), str):
            key_id = body['key_id']
            entry = self.store.registry()['keys'].get(key_id)
            if entry is None:
                raise Refused(401, 'key_unknown')
            der = base64.b64decode(entry['spki'])
            public_key = serialization.load_der_public_key(der)
        else:
            raise Refused(400, 'malformed')
        try:
            public_key.verify(base64.b64decode(body['signature'], validate=True), challenge.encode(),
                              ec.ECDSA(hashes.SHA256()))
        except (InvalidSignature, ValueError):
            raise Refused(401, 'signature_invalid') from None
        revoked = None if serials is not None else self.policy['revoked']()
        self.store.update_registry(lambda reg: self._commit(reg, opened_challenge, key_id, der, serials, revoked, now))
        token = sign(self.store.secrets()[0], 'rgm1', {'k': key_id, 'e': int(now) + TOKEN_TTL_S})
        return {'access_token': token, 'token_type': 'Bearer', 'expires_in': TOKEN_TTL_S, 'key_id': key_id}

    def _commit(self, reg, challenge, key_id, der, serials, revoked, now):
        """Under the registry lock: consume the challenge once (persisted, so a restart cannot replay it), then
        enroll or check the key."""
        used = {n: e for n, e in reg.setdefault('usedChallenges', {}).items() if e > now}
        if challenge['n'] in used:
            raise Refused(401, 'challenge_reused')
        used[challenge['n']] = challenge['e']
        reg['usedChallenges'] = used
        entry = reg['keys'].get(key_id)
        if serials is None:  # renewal
            if entry['revoked'] or set(entry.get('serials', [])) & revoked:
                entry['revoked'] = True
                raise Refused(403, 'key_revoked')
            return
        if entry is not None:
            if entry['revoked']:
                raise Refused(403, 'key_revoked')
            return  # the same install enrolling again
        day = time.strftime('%Y-%m-%d', time.gmtime(now))
        reg['days'] = {day: reg['days'].get(day, 0)}
        if (not reg['enrollmentOpen'] or len(reg['keys']) >= self.policy['maxKeys']
                or reg['days'][day] >= self.policy['enrollPerDay']):
            raise Refused(403, 'enrollment_closed')
        reg['days'][day] += 1
        reg['keys'][key_id] = {'spki': base64.b64encode(der).decode(), 'enrolledAt': int(now), 'revoked': False,
                               'serials': serials}

    def validate(self, authorization):
        now = self.clock()
        if not isinstance(authorization, str) or not authorization.startswith('Bearer '):
            raise Refused(401, 'missing')
        payload = opened(self.store.secrets(), 'rgm1', authorization[7:], now)
        if payload is None:
            raise Refused(401, 'invalid_or_expired')
        entry = self.store.registry()['keys'].get(payload.get('k'))
        if entry is None:
            raise Refused(401, 'key_unknown')
        if entry['revoked']:
            raise Refused(403, 'key_revoked')


def handler(service):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'models-auth'
        sys_version = ''

        def log_message(self, *args):  # no request log: paths and addresses stay out of logs
            pass

        def reply(self, status, body=None):
            data = b'' if body is None else json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Cache-Control', 'no-store')
            if body is not None:
                self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path != '/_validate':
                return self.reply(404, {'error': 'not_found'})
            try:
                service.validate(self.headers.get('Authorization'))
                self.reply(204)
            except Refused as e:
                self.reply(e.status)

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', ''))
                if not 0 <= length <= MAX_BODY or self.headers.get_content_type() != 'application/json':
                    raise Refused(400, 'malformed')
                body = json.loads(self.rfile.read(length) or b'{}')
                if self.path == '/v1/auth/challenge':
                    return self.reply(200, service.challenge())
                if self.path == '/v1/auth/token':
                    return self.reply(200, service.token(body))
                raise Refused(404, 'not_found')
            except Refused as e:
                self.reply(e.status, {'error': e.category})
            except ValueError:
                self.reply(400, {'error': 'malformed'})
    return Handler


class RevocationStatus:
    """Google's attestation status list (JSON {"entries": {"<serial hex>": {...}}}), refreshed by the operator's
    timer. Enrollment refuses while it is missing or older than max_age_s; any listed serial counts as revoked."""

    def __init__(self, path, max_age_s=48 * 3600, clock=time.time):
        self.path, self.max_age_s, self.clock = Path(path), max_age_s, clock
        self.cached = (None, frozenset())

    def __call__(self):
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            raise Refused(503, 'revocation_status_missing') from None
        if self.clock() - mtime > self.max_age_s:
            raise Refused(503, 'revocation_status_stale')
        if self.cached[0] != mtime:
            try:
                entries = json.loads(self.path.read_text())['entries']
                self.cached = (mtime, frozenset(k.lower().lstrip('0') or '0' for k in entries))
            except (OSError, ValueError, KeyError, AttributeError):
                raise Refused(503, 'revocation_status_invalid') from None
        return self.cached[1]


def policy_from(a):
    boot_keys = set()
    if a.self_signed_boot_keys and Path(a.self_signed_boot_keys).exists():
        boot_keys = {line.strip().lower() for line in Path(a.self_signed_boot_keys).read_text().split() if line.strip()}
    return {'roots': load_roots(a.roots) if a.roots else set(), 'revoked': RevocationStatus(a.revocation_status),
            'selfSignedBootKeys': boot_keys, 'maxKeys': a.max_keys, 'enrollPerDay': a.enroll_per_day}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('serve', 'secret-init', 'rotate-secret', 'revoke', 'close-enrollment',
                                       'open-enrollment', 'status'))
    p.add_argument('key_id', nargs='?')
    p.add_argument('--state-dir', type=Path, default=Path('/var/lib/rose-models/auth'))
    p.add_argument('--roots', type=Path, default=Path('/etc/rose-models/attestation-roots'))
    p.add_argument('--revocation-status', type=Path, default=Path('/var/lib/rose-models/attestation-status.json'))
    p.add_argument('--self-signed-boot-keys', type=Path, default=Path('/etc/rose-models/self-signed-boot-keys.txt'),
                   help='approved verifiedBootKey values (hex), one per line; SelfSigned boots need one')
    p.add_argument('--max-keys', type=int, default=200)
    p.add_argument('--enroll-per-day', type=int, default=50)
    p.add_argument('--port', type=int, default=8090)
    a = p.parse_args(argv)
    os.umask(0o077)
    a.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    store = Store(a.state_dir)
    try:
        if a.command == 'serve':
            policy = policy_from(a)
            if not policy['roots']:
                print('refused: no trusted attestation roots installed', file=sys.stderr)
                return 4
            store.secrets()
            ThreadingHTTPServer(('127.0.0.1', a.port), handler(Service(store, policy))).serve_forever()
        elif a.command in ('secret-init', 'rotate-secret'):
            store.init_secret(rotate=a.command == 'rotate-secret')
        elif a.command == 'revoke':
            def revoke(reg):
                if a.key_id not in reg['keys']:
                    raise Refused(404, 'key_unknown')
                reg['keys'][a.key_id]['revoked'] = True
            store.update_registry(revoke)
        elif a.command in ('close-enrollment', 'open-enrollment'):
            store.update_registry(lambda reg: reg.update(enrollmentOpen=a.command == 'open-enrollment'))
        reg = store.registry()
        print(json.dumps({'command': a.command, 'keys': len(reg['keys']),
                          'revoked': sum(k['revoked'] for k in reg['keys'].values()),
                          'enrollmentOpen': reg['enrollmentOpen']}))
        return 0
    except Refused as e:
        print(f'refused: {e.category}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
