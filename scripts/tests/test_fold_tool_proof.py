"""FOLD-TOOL-PROOF-001: scripts/fold_tool_proof.py on task-owned synthetic fixtures only.

A fake filesystem root holds synthetic pinned scripts, minimal ELF host files and synthetic vendor metadata: a
pure-Python RSA-2048 OpenPGP key, a clearsigned InRelease, a Packages index and .deb archives. The synthetic key and
metadata prove the test contract only, never production vendor trust; nothing is executed, downloaded or read from the
network, and the checked-in profile still refuses (vendor_trust_unbound)."""
import base64
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import lzma
import os
from pathlib import Path
import random
import shutil
import struct
import tarfile
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
spec_ = importlib.util.spec_from_file_location('fold_tool_proof', SCRIPTS / 'fold_tool_proof.py')
F = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(F)
REVISION = {'development': '1' * 40, 'external/avb': '2' * 40}


# ------------------------------------------------------------------ synthetic RSA / OpenPGP

def _prime(bits, rng):
    while True:
        n = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if all(pow(a, n - 1, n) == 1 for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)):
            d, r = n - 1, 0
            while d % 2 == 0:
                d, r = d // 2, r + 1
            if all(_mr(n, a, d, r) for a in (rng.randrange(2, n - 2) for _ in range(16))):
                return n


def _mr(n, a, d, r):
    x = pow(a, d, n)
    if x in (1, n - 1):
        return True
    for _ in range(r - 1):
        x = pow(x, 2, n)
        if x == n - 1:
            return True
    return False


def rsa_key(seed):
    rng = random.Random(seed)
    e = 65537
    while True:
        p, q = _prime(1024, rng), _prime(1024, rng)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e:
            return p * q, e, pow(e, -1, phi)


def mpi(x):
    return x.bit_length().to_bytes(2, 'big') + x.to_bytes((x.bit_length() + 7) // 8, 'big')


def packet(tag, body):
    return bytes([0xC0 | tag, 255]) + len(body).to_bytes(4, 'big') + body


def crc24(data):
    crc = 0xB704CE
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def armor(kind, data):
    b64 = base64.b64encode(data).decode()
    lines = '\n'.join(b64[i:i + 64] for i in range(0, len(b64), 64))
    return (f'-----BEGIN PGP {kind}-----\n\n{lines}\n=' + base64.b64encode(crc24(data).to_bytes(3, 'big')).decode()
            + f'\n-----END PGP {kind}-----\n')


class Key:
    def __init__(self, seed):
        self.n, self.e, self.d = rsa_key(seed)
        self.body = b'\x04' + (1700000000).to_bytes(4, 'big') + b'\x01' + mpi(self.n) + mpi(self.e)
        self.fingerprint = hashlib.sha1(b'\x99' + len(self.body).to_bytes(2, 'big') + self.body).hexdigest().upper()

    def public(self):
        return armor('PUBLIC KEY BLOCK', packet(6, self.body) + packet(13, b'Synthetic Test Archive Key'))

    def clearsign(self, text):
        canonical = '\r\n'.join(line.rstrip(' \t') for line in text.split('\n')).encode()
        sub = (bytes([5, 2]) + (1700000001).to_bytes(4, 'big')
               + bytes([22, 33, 4]) + bytes.fromhex(self.fingerprint))
        hashed = bytes([4, 0x01, 1, 8]) + len(sub).to_bytes(2, 'big') + sub
        digest = hashlib.sha256(canonical + hashed + b'\x04\xff' + len(hashed).to_bytes(4, 'big')).digest()
        k = (self.n.bit_length() + 7) // 8
        prefix = F.DIGEST_INFO[8][1]
        em = b'\x00\x01' + b'\xff' * (k - 3 - len(prefix) - len(digest)) + b'\x00' + prefix + digest
        s = pow(int.from_bytes(em, 'big'), self.d, self.n)
        unhashed = bytes([9, 16]) + bytes.fromhex(self.fingerprint[-16:])
        sig = hashed + len(unhashed).to_bytes(2, 'big') + unhashed + digest[:2] + mpi(s)
        escaped = '\n'.join('- ' + line if line.startswith('-') else line for line in text.split('\n'))
        return f'-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256\n\n{escaped}\n' + armor('SIGNATURE', packet(2, sig))


# ------------------------------------------------------------------ synthetic ELF / .deb

def elf(interp=None, needed=(), runpath=False, filler=b''):
    strtab = b'\0' + b''.join(n.encode() + b'\0' for n in needed)
    offsets = [1 + sum(len(n) + 1 for n in needed[:i]) for i in range(len(needed))]
    interp_b = (interp.encode() + b'\0') if interp else b''
    phnum = 3 if interp else 2
    data_off = 64 + 56 * phnum
    interp_off = data_off
    str_off = interp_off + len(interp_b)
    dyn_off = str_off + len(strtab)
    dyn_off += -dyn_off % 8
    dyn = b''.join(struct.pack('<qQ', 1, o) for o in offsets) + struct.pack('<qQ', 5, str_off)
    if runpath:
        dyn += struct.pack('<qQ', 29, 1)
    dyn += struct.pack('<qQ', 0, 0)
    total = dyn_off + len(dyn) + len(filler)
    header = b'\x7fELF' + bytes([2, 1, 1]) + b'\0' * 9 + struct.pack('<HHIQQQIHHHHHH', 3, 62, 1, 0, 64, 0, 0, 64, 56,
                                                                       phnum, 64, 0, 0)
    phdrs = struct.pack('<IIQQQQQQ', 1, 5, 0, 0, 0, total, total, 4096)
    if interp:
        phdrs += struct.pack('<IIQQQQQQ', 3, 4, interp_off, interp_off, 0, len(interp_b), len(interp_b), 1)
    phdrs += struct.pack('<IIQQQQQQ', 2, 6, dyn_off, dyn_off, 0, len(dyn), len(dyn), 8)
    body = header + phdrs + interp_b + strtab
    return body + b'\0' * (dyn_off - len(body)) + dyn + filler


def deb(files, compression='xz'):
    """files: {abs path: bytes | ('symlink', target)} -> .deb bytes (ar: debian-binary, control.tar.xz, data.tar.*)."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.GNU_FORMAT) as tar:
        for path, content in sorted(files.items()):
            info = tarfile.TarInfo('.' + path)
            if isinstance(content, tuple):
                info.type, info.linkname = tarfile.SYMTYPE, content[1]
                tar.addfile(info)
            else:
                info.size, info.mode = len(content), 0o755
                tar.addfile(info, io.BytesIO(content))
    data = {'xz': lzma.compress, 'gz': gzip.compress, 'zst': lambda b: b'(\xb5/\xfd' + b}[compression](raw.getvalue())
    control = lzma.compress(b'')

    def member(name, body):
        header = f'{name:<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(body):<10}`\n'.encode()
        return header + body + (b'\n' if len(body) & 1 else b'')
    return b'!<arch>\n' + member('debian-binary', b'2.0\n') + member('control.tar.xz', control) + member(
        f'data.tar.{compression}', data)


LIBDIR = '/usr/lib/x86_64-linux-gnu'
LOADER = LIBDIR + '/ld-linux-x86-64.so.2'


class Fixture:
    """A complete positive synthetic world under a task-owned root."""
    KEY = None

    def __init__(self, case):
        if Fixture.KEY is None:
            Fixture.KEY = Key(20261030)
        self.key = Fixture.KEY
        self.root = Path(tempfile.mkdtemp(prefix='fold-proof-'))
        case.addCleanup(shutil.rmtree, self.root, True)
        self.scripts = {'generator': ('/src/development', 'tools/make_key', b'#!/bin/bash\n# synthetic make_key\n'),
                        'decrypt-keys': ('/src/script', 'decrypt-keys', b'#!/bin/bash\n# synthetic decrypt-keys\n'),
                        'avbtool': ('/src/external/avb', 'avbtool.py', b'#!/usr/bin/env python3\n# synthetic avbtool\n')}
        self.packages = {
            'libc6': {LIBDIR + '/libc.so.6': elf(), LOADER: elf(), '/usr/lib64/ld-linux-x86-64.so.2':
                      ('symlink', '../lib/x86_64-linux-gnu/ld-linux-x86-64.so.2')},
            'libssl3t64': {LIBDIR + '/libssl.so.3': elf(needed=['libcrypto.so.3', 'libc.so.6']),
                           LIBDIR + '/libcrypto.so.3': elf(needed=['libc.so.6'])},
            'openssl': {'/usr/bin/openssl': elf('/lib64/ld-linux-x86-64.so.2', ['libssl.so.3', 'libcrypto.so.3', 'libc.so.6'])},
            'bash': {'/usr/bin/bash': elf('/lib64/ld-linux-x86-64.so.2', ['libc.so.6'])},
            'python3.12-minimal': {'/usr/bin/python3.12': elf('/lib64/ld-linux-x86-64.so.2', ['libc.so.6']),
                                   '/usr/bin/python3': ('symlink', 'python3.12')},
        }
        self.compression = {}
        self.build()

    def path(self, logical):
        return self.root / logical.lstrip('/')

    def write(self, logical, content):
        target = self.path(logical)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, tuple):
            if os.path.lexists(target):
                target.unlink()
            target.symlink_to(content[1])
        else:
            target.write_bytes(content)
            os.chmod(target, 0o755)

    def build(self, signer=None):
        for name, (root, rel, data) in self.scripts.items():
            self.write(f'{root}/{rel}', data)
        for alias, target in (('bin', 'usr/bin'), ('lib', 'usr/lib'), ('lib64', 'usr/lib64')):
            if not os.path.lexists(self.root / alias):
                (self.root / target).mkdir(parents=True, exist_ok=True)
                (self.root / alias).symlink_to(target)
        for files in self.packages.values():
            for logical, content in files.items():
                self.write(logical, content)
        self.write_metadata(signer)

    def write_metadata(self, signer=None, release_extra=''):
        stanzas, debs = [], {}
        for name, files in sorted(self.packages.items()):
            data = deb(files, self.compression.get(name, 'xz'))
            debs[name] = data
            self.write(f'/retained/{name}.deb', data)
            stanzas.append(f'Package: {name}\nArchitecture: amd64\nVersion: 1.0-{name}\nFilename: pool/{name}.deb\n'
                           f'Size: {len(data)}\nSHA256: {hashlib.sha256(data).hexdigest()}\n')
        index = lzma.compress('\n'.join(stanzas).encode())
        self.write('/retained/Packages.xz', index)
        release = (f'Origin: Synthetic\nSuite: noble\nCodename: noble\nSHA256:\n'
                   f' {hashlib.sha256(index).hexdigest()} {len(index)} main/binary-amd64/Packages.xz\n'
                   f' {"0" * 64} 1 main/binary-amd64/Release{release_extra}')
        self.write('/retained/InRelease', (signer or self.key).clearsign(release).encode())
        self.write('/retained/archive-key.asc', self.key.public().encode())

    def spec(self):
        return {'schema': F.SPEC_SCHEMA, 'profile': F.PROFILE,
                'scripts': {name: {'root': root} for name, (root, _, _) in self.scripts.items()},
                'hostFiles': {'openssl': {'path': '/usr/bin/openssl', 'package': 'openssl'},
                              'bash': {'path': '/usr/bin/bash', 'package': 'bash'},
                              'python3': {'path': '/usr/bin/python3', 'package': 'python3.12-minimal'}},
                'libraryDirectories': [LIBDIR, '/lib/x86_64-linux-gnu'],
                'vendor': {'publicKey': '/retained/archive-key.asc', 'release': '/retained/InRelease', 'suite': 'noble',
                           'index': {'path': '/retained/Packages.xz', 'releasePath': 'main/binary-amd64/Packages.xz'},
                           'packages': {name: {'version': f'1.0-{name}', 'architecture': 'amd64',
                                               'deb': f'/retained/{name}.deb'} for name in self.packages}}}

    def profile(self, fingerprint=None):
        profile = copy.deepcopy(F.default_profile())
        for name, (root, rel, data) in self.scripts.items():
            source = profile['tools'][name]['source']
            source['path'] = rel
            source['revision'] = REVISION.get(root.split('/src/', 1)[1], '3' * 40)
            source['gitBlob'] = hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()
            profile['tools'][name]['sourceSha256'] = hashlib.sha256(data).hexdigest()
        profile['tools']['openssl']['trust'] = {'basis': 'vendor-package', 'fingerprint': fingerprint or self.key.fingerprint}
        return profile

    def verify(self, spec=None, profile=None, manifest=None, **kw):
        return F.verify_local(spec or self.spec(), str(self.root), profile or self.profile(),
                              manifest if manifest is not None else dict(REVISION), **kw)


class Positive(unittest.TestCase):
    def test_complete_trusted_spec_resolves_all_four_tools(self):
        fx = Fixture(self)
        proof = fx.verify()
        self.assertEqual(set(proof['tools']), {'generator', 'decrypt-keys', 'avbtool', 'openssl'})
        self.assertEqual(proof['tools']['avbtool']['invocation'], ['/usr/bin/python3', '/src/external/avb/avbtool.py'])
        self.assertEqual(proof['tools']['generator']['interpreter'], '/usr/bin/bash')
        self.assertEqual(proof['tools']['openssl']['package']['name'], 'openssl')
        self.assertEqual({f['role'] for f in proof['hostFiles'].values()}, {'tool', 'interpreter', 'library', 'loader'})
        self.assertIn('/usr/bin/python3.12', proof['hostFiles'])  # the symlink was followed through its package member
        self.assertIn(LIBDIR + '/libcrypto.so.3', proof['hostFiles'])
        self.assertEqual(proof['vendor']['fingerprint'], fx.key.fingerprint)
        self.assertEqual(proof['vendor']['anchorSource'], 'caller-supplied profile (test only)')
        self.assertEqual(proof['environment'], {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        self.assertTrue(F.recheck(proof, str(fx.root)))
        text = json.dumps(proof)
        self.assertNotIn(str(fx.root), text)
        self.assertNotIn('synthetic make_key', text)

    def test_checked_in_profile_refuses_until_a_vendor_identity_is_approved(self):
        fx = Fixture(self)
        with self.assertRaisesRegex(F.Refused, '^vendor_trust_unbound$'):
            F.verify_local(fx.spec(), str(fx.root))
        self.assertEqual(F.default_profile()['tools']['openssl'].get('trust'), None)

    def test_validate_spec_is_pure_and_closed(self):
        fx = Fixture(self)
        self.assertEqual(F.validate_spec(fx.spec()), [])
        bad = fx.spec()
        bad['vendor']['packages']['openssl']['deb'] = 'relative.deb'
        self.assertIn('spec_packages', F.validate_spec(bad))
        bad = fx.spec()
        bad['extra'] = 1
        self.assertEqual(F.validate_spec(bad), ['spec_fields'])
        bad = fx.spec()
        bad['hostFiles']['bash']['package'] = 'unlisted'
        self.assertIn('spec_host_package_unlisted', F.validate_spec(bad))
        bad = fx.spec()
        del bad['scripts']['avbtool']
        self.assertIn('spec_scripts', F.validate_spec(bad))
        with self.assertRaisesRegex(F.Refused, '^spec_invalid$'):
            fx.verify(spec=bad)


class Negative(unittest.TestCase):
    def refuse(self, fx, code, **kw):
        with self.assertRaisesRegex(F.Refused, f'^{code}$'):
            fx.verify(**kw)

    def test_wrong_source_blob_or_revision(self):
        fx = Fixture(self)
        fx.write('/src/development/tools/make_key', b'#!/bin/bash\n# replaced\n')
        self.refuse(fx, 'source_mismatch')
        fx = Fixture(self)
        self.refuse(fx, 'source_revision_mismatch', manifest=dict(REVISION, development='9' * 40))
        profile = fx.profile()
        profile['tools']['avbtool']['source']['gitBlob'] = 'f' * 40
        self.refuse(fx, 'source_mismatch', profile=profile)
        spec = fx.spec()
        spec['scripts']['generator']['root'] = '/src/script'
        self.refuse(fx, 'source_root_mismatch', spec=spec)

    def test_replaced_executable_interpreter_or_library(self):
        for logical in ('/usr/bin/openssl', '/usr/bin/python3.12', '/usr/bin/bash', LIBDIR + '/libcrypto.so.3', LOADER):
            with self.subTest(logical):
                fx = Fixture(self)
                fx.write(logical, elf(needed=['libc.so.6'], filler=b'replaced'))
                self.refuse(fx, 'installed_mismatch')

    def test_untrusted_signature_index_or_package(self):
        fx = Fixture(self)
        fx.write_metadata(signer=Key(7))  # another key signs the same Release
        self.refuse(fx, 'signature_invalid')
        fx = Fixture(self)
        text = fx.path('/retained/InRelease').read_text().replace('Suite: noble', 'Suite: noble ')
        fx.path('/retained/InRelease').write_text(text.replace('Origin: Synthetic', 'Origin: Synthetix'))
        self.refuse(fx, 'signature_invalid')
        fx = Fixture(self)
        self.refuse(fx, 'vendor_key_mismatch', profile=fx.profile(fingerprint='A' * 40))
        fx = Fixture(self)
        fx.path('/retained/Packages.xz').write_bytes(lzma.compress(b'Package: openssl\n'))
        self.refuse(fx, 'index_mismatch')
        fx = Fixture(self)
        data = bytearray(fx.path('/retained/openssl.deb').read_bytes())
        data[-1] ^= 1
        fx.path('/retained/openssl.deb').write_bytes(bytes(data))
        self.refuse(fx, 'package_digest_mismatch')
        fx = Fixture(self)
        spec = fx.spec()
        spec['vendor']['packages']['openssl']['version'] = '9.9'
        self.refuse(fx, 'package_missing', spec=spec)
        spec = fx.spec()
        spec['vendor']['suite'] = 'jammy'
        self.refuse(fx, 'release_invalid', spec=spec)

    def test_path_hijack_cannot_substitute_a_tool(self):
        fx = Fixture(self)
        hijack = fx.root / 'hijack'
        hijack.mkdir()
        (hijack / 'openssl').write_bytes(b'#!/bin/sh\necho hijacked\n')
        os.chmod(hijack / 'openssl', 0o755)
        with mock.patch.dict(os.environ, {'PATH': f'{hijack}:{os.environ.get("PATH", "")}'}):
            proof = fx.verify()
        self.assertEqual(proof['tools']['openssl']['path'], '/usr/bin/openssl')
        self.assertFalse(proof['environment']['PATH'].startswith(str(hijack)))

    def test_symlink_escape(self):
        fx = Fixture(self)
        real = fx.root / 'elsewhere'
        shutil.move(fx.path('/src/development'), real)
        fx.path('/src/development').symlink_to(real)
        self.refuse(fx, 'symlink_ancestor')
        fx = Fixture(self)
        script = fx.path('/src/script/decrypt-keys')
        moved = fx.root / 'decrypt-keys'
        shutil.move(script, moved)
        script.symlink_to(moved)
        self.refuse(fx, 'file_missing')  # O_NOFOLLOW: the script itself may not be a symlink
        fx = Fixture(self)
        fx.write('/usr/bin/python3', ('symlink', '/hijack/python3'))
        self.refuse(fx, 'installed_mismatch')  # a symlink that is not the package's own symlink
        fx = Fixture(self)
        (fx.root / 'usr/lib/x86_64-linux-gnu').rename(fx.root / 'libs')
        (fx.root / 'usr/lib/x86_64-linux-gnu').symlink_to(fx.root / 'libs')
        self.refuse(fx, 'symlink_ancestor')

    def test_unsupported_format_and_interpreter(self):
        fx = Fixture(self)
        fx.compression['openssl'] = 'zst'
        fx.write_metadata()
        self.refuse(fx, 'format_unsupported')
        fx = Fixture(self)
        fx.packages['openssl']['/usr/bin/openssl'] = b'#!/bin/sh\nnot an ELF\n'
        fx.build()
        self.refuse(fx, 'elf_invalid')
        fx = Fixture(self)
        fx.scripts['avbtool'] = ('/src/external/avb', 'avbtool.py', b'#!/usr/bin/env python2\n')
        fx.build()
        self.refuse(fx, 'interpreter_unsupported')
        fx = Fixture(self)
        spec = fx.spec()
        spec['hostFiles']['bash']['path'] = '/usr/bin/python3.12'
        spec['hostFiles']['bash']['package'] = 'python3.12-minimal'
        self.refuse(fx, 'interpreter_mismatch', spec=spec)

    def test_dependency_ambiguity_or_absence(self):
        fx = Fixture(self)
        other = '/opt/lib'
        fx.write(other + '/libc.so.6', elf())
        spec = fx.spec()
        spec['libraryDirectories'] = [LIBDIR, other]
        self.refuse(fx, 'dependency_ambiguous', spec=spec)
        fx = Fixture(self)
        fx.packages['openssl']['/usr/bin/openssl'] = elf('/lib64/ld-linux-x86-64.so.2', ['libc.so.6'], runpath=True)
        fx.build()
        self.refuse(fx, 'dependency_ambiguous')
        fx = Fixture(self)
        fx.write('/src/external/avb/avb_openssl', b'#!/bin/sh\n')
        self.refuse(fx, 'dependency_ambiguous')
        fx = Fixture(self)
        fx.packages['bash']['/usr/bin/bash'] = elf('/lib64/ld-linux-x86-64.so.2', ['libtinfo.so.6', 'libc.so.6'])
        fx.build()
        self.refuse(fx, 'dependency_unresolved')
        fx = Fixture(self)
        fx.packages['bash'][LIBDIR + '/libc.so.6'] = elf(filler=b'other')  # two packages claim the same file
        fx.build()
        self.refuse(fx, 'dependency_ambiguous')

    def test_changed_after_proof(self):
        for logical in ('/usr/bin/openssl', '/src/external/avb/avbtool.py', LIBDIR + '/libssl.so.3'):
            with self.subTest(logical):
                fx = Fixture(self)
                proof = fx.verify()
                content = fx.path(logical).read_bytes() + b'\n'
                fx.path(logical).write_bytes(content)
                with self.assertRaisesRegex(F.Refused, '^changed_after_proof$'):
                    F.recheck(proof, str(fx.root))

    def test_bounds(self):
        fx = Fixture(self)
        ticks = iter(range(0, 10 ** 6, 1000))
        self.refuse(fx, 'bound_exceeded', clock=lambda: next(ticks))
        for limit, value in (('members', 1), ('debBytes', 100), ('elfBytes', 100), ('scriptBytes', 8), ('libraries', 1)):
            with self.subTest(limit):
                with mock.patch.dict(F.LIMITS, {limit: value}):
                    self.refuse(Fixture(self), 'bound_exceeded')

    def test_retained_sources_and_metadata_are_not_mutated(self):
        fx = Fixture(self)

        def snapshot():
            return {str(p): (p.read_bytes() if p.is_file() and not p.is_symlink() else os.readlink(p) if p.is_symlink()
                             else None, os.lstat(p).st_mode) for p in sorted(fx.root.rglob('*'))}
        before = snapshot()
        fx.verify()
        self.assertEqual(snapshot(), before)


if __name__ == '__main__':
    unittest.main()
