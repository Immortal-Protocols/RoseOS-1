"""The server binary check and its manifest (docs/APP-BUILDER.md §7.7): only a static x86-64 Linux executable is a
server, read from its own headers within the file's bounds, and a manifest is exactly schema version 1, tied to its
binary by sha256 and size, with no host identity or secret in it. Real binaries come from the machine's own Rust
toolchain (a static-pie and a dynamically linked one); the rest are made here byte by byte."""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rose.app_builder import server_manifest as M

EHDR = struct.Struct('<16sHHIQQQIHHHHHH')
PHDR = struct.Struct('<IIQQQQQQ')


def tiny_elf(*, e_type=M.ET_EXEC, machine=M.EM_X86_64, cls=M.ELFCLASS64, data=M.ELFDATA2LSB, osabi=0, entry=0x401000,
             interp=False, needed=False, soname=False, dynamic=None, executable=True, phnum=None, phoff=None,
             filesz_over_memsz=False, segment_past_end=False, ehsize=64, phentsize=56, magic=b'\x7fELF',
             extra=b'') -> bytes:
    """A minimal ELF: one PT_LOAD over the whole file, and optionally PT_INTERP and PT_DYNAMIC. ``dynamic`` defaults to
    on for ET_DYN (a static-pie executable has one)."""
    if dynamic is None:
        dynamic = e_type == M.ET_DYN or needed or soname
    entries = []
    if needed:
        entries.append((M.DT_NEEDED, 1))
    if soname:
        entries.append((M.DT_SONAME, 1))
    entries += [(0x6ffffffb, 0x08000001), (M.DT_NULL, 0)]
    dyn = b''.join(struct.pack('<qQ', t, v) for t, v in entries) if dynamic else b''
    interp_bytes = b'/lib/ld-musl-x86_64.so.1\0' if interp else b''
    count = 1 + bool(interp) + bool(dynamic)
    start = 64 + count * 56
    body = dyn + interp_bytes + b'\xc3' * 16 + extra
    size = start + len(body)
    headers = []
    headers.append((M.PT_LOAD, 5 if executable else 4, 0, 0x400000, 0x400000, size + (1 << 30 if segment_past_end
                                                                                       else 0),
                    size - 1 if filesz_over_memsz else size, 0x1000))
    if dynamic:
        headers.append((M.PT_DYNAMIC, 6, start, 0x400000 + start, 0x400000 + start, len(dyn), len(dyn), 8))
    if interp:
        headers.append((M.PT_INTERP, 4, start + len(dyn), 0, 0, len(interp_bytes), len(interp_bytes), 1))
    ident = magic + bytes([cls, data, 1, osabi]) + bytes(8)
    out = EHDR.pack(ident, e_type, machine, 1, entry, 64 if phoff is None else phoff, 0, 0, ehsize, phentsize,
                    count if phnum is None else phnum, 0, 0, 0)
    out += b''.join(PHDR.pack(*h) for h in headers)
    return out + body


class Tmp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.n = 0

    def file(self, data: bytes) -> Path:
        self.n += 1
        path = self.tmp / f'elf{self.n}'
        path.write_bytes(data)
        return path


class Elf(Tmp):
    def test_static_executables_pass(self):
        for label, data in (('static', tiny_elf()), ('static-pie', tiny_elf(e_type=M.ET_DYN)),
                            ('GNU/Linux ABI', tiny_elf(osabi=3))):
            with self.subTest(label):
                info = M.check_elf(self.file(data))
                self.assertEqual((info.sha256, info.size), (hashlib.sha256(data).hexdigest(), len(data)))
                self.assertEqual(info.pie, label == 'static-pie')

    def test_anything_else_is_refused(self):
        not_static = {
            'an interpreter': tiny_elf(interp=True),
            'a shared library needed': tiny_elf(e_type=M.ET_DYN, needed=True),
            'a needed library in a non-PIE executable': tiny_elf(needed=True),
        }
        invalid = {
            'empty': b'',
            'short': tiny_elf()[:40],
            'not an ELF': b'MZ' + tiny_elf()[2:],
            '32-bit': tiny_elf(cls=1),
            'big-endian': tiny_elf(data=2),
            'another OS ABI': tiny_elf(osabi=9),
            'arm64': tiny_elf(machine=183),
            'an object file': tiny_elf(e_type=1),
            'a core file': tiny_elf(e_type=4),
            'a shared object': tiny_elf(e_type=M.ET_DYN, soname=True),
            'PIE without a dynamic section': tiny_elf(e_type=M.ET_DYN, dynamic=False),
            'no entry point': tiny_elf(entry=0),
            'no executable segment': tiny_elf(executable=False),
            'no program headers': tiny_elf(phnum=0),
            'too many program headers': tiny_elf(phnum=M.MAX_PHNUM + 1),
            'program headers past the end': tiny_elf(phnum=40),
            'program headers inside the ELF header': tiny_elf(phoff=8),
            'a wrong header size': tiny_elf(ehsize=52),
            'a wrong program header size': tiny_elf(phentsize=32),
            'a segment past the end': tiny_elf(segment_past_end=True),
            'more file than memory': tiny_elf(filesz_over_memsz=True),
        }
        for reason, cases in (('server_not_static', not_static), ('server_binary_invalid', invalid)):
            for label, data in cases.items():
                with self.subTest(label):
                    with self.assertRaises(M.Refused) as caught:
                        M.check_elf(self.file(data))
                    self.assertEqual(caught.exception.reason, reason)

    def test_dynamic_entries_are_bounded(self):
        many = tiny_elf(e_type=M.ET_DYN)
        with mock.patch.object(M, 'MAX_DYNAMIC', 1):  # two entries before DT_NULL
            with self.assertRaises(M.Refused):
                M.check_elf(self.file(many))

    def test_only_a_plain_file_is_read(self):
        real = self.file(tiny_elf())
        link = self.tmp / 'link'
        link.symlink_to(real)
        for path in (link, self.tmp, self.tmp / 'missing'):
            with self.subTest(path=path.name):
                with self.assertRaises(M.Refused) as caught:
                    M.check_elf(path)
                self.assertEqual(caught.exception.reason, 'server_binary_invalid')
        with mock.patch.object(M, 'MAX_ELF', 100):
            with self.assertRaises(M.Refused):
                M.check_elf(real)


HAS_RUST = shutil.which('cargo') is not None and shutil.which('rustc') is not None


@unittest.skipUnless(HAS_RUST or os.environ.get('ROSE_REQUIRE_CARGO'), 'needs a Rust toolchain (cargo and rustc)')
class RealBinaries(Tmp):
    """The machine's own toolchain, offline, no crates: the same program linked statically and dynamically."""

    def build(self, rustflags):
        project = self.tmp / f'p{self.n}'
        self.n += 1
        (project / 'src').mkdir(parents=True)
        (project / 'Cargo.toml').write_text('[package]\nname = "t"\nversion = "0.0.0"\nedition = "2021"\n[workspace]\n')
        (project / 'src/main.rs').write_text('fn main() { println!("hi"); }\n')
        host = subprocess.run(['rustc', '-vV'], capture_output=True, text=True, check=True).stdout
        target = next(line.split(': ', 1)[1] for line in host.splitlines() if line.startswith('host: '))
        env = {**os.environ, 'CARGO_HOME': str(self.tmp / 'cargo-home'), 'RUSTFLAGS': rustflags}
        subprocess.run(['cargo', 'build', '-q', '--offline', '--release', '--target', target], cwd=project, env=env,
                       check=True, timeout=300)
        return project / 'target' / target / 'release/t'

    def test_a_static_build_passes_and_a_dynamic_one_does_not(self):
        info = M.check_elf(self.build('-Ctarget-feature=+crt-static'))
        self.assertGreater(info.size, 1000)
        with self.assertRaises(M.Refused) as caught:
            M.check_elf(self.build(''))
        self.assertEqual(caught.exception.reason, 'server_not_static')


ELF = {'sha256': 'e' * 64, 'size': 1234, 'target': M.TARGET}
LOCK = [{'scope': 'account', 'number': 1, 'name': 'notes', 'sha256': '1' * 64},
        {'scope': 'account', 'number': 2, 'name': 'tags', 'sha256': '2' * 64},
        {'scope': 'shared', 'number': 1, 'name': 'board', 'sha256': '3' * 64}]


class Manifest(Tmp):
    def test_the_manifest_describes_one_version_and_nothing_of_the_host(self):
        m = M.manifest('0123456789abcdef', 7, ELF, LOCK, shared=True)
        self.assertEqual(m, {
            'schema_version': 1, 'project_id': '0123456789abcdef', 'version_code': 7, 'elf': ELF,
            'listen': {'env': 'PORT', 'default': 8080}, 'health': '/health', 'data_dir': {'env': 'DATA_DIR'},
            'signin': {'revision': '23062a04'}, 'schema': {'account': 2, 'shared': 1}})
        for host_owned in ('app_id', 'deployment_sha256', 'revision_sha256', 'key', 'secret', 'sealing'):
            self.assertNotIn(host_owned, json.dumps(m))
        self.assertEqual(M.manifest('0123456789abcdef', 1, ELF)['schema'], {'account': 0})
        with self.assertRaises(M.Refused):
            M.manifest('0123456789abcdef', 7, ELF, LOCK)  # shared migrations, no shared database

    def test_canonical_bytes_round_trip(self):
        m = M.manifest('0123456789abcdef', 7, ELF, LOCK, shared=True)
        data = M.encode(m)
        self.assertEqual(data, (json.dumps(m, sort_keys=True, separators=(',', ':')) + '\n').encode())
        self.assertEqual(M.decode(data), m)
        self.assertEqual(M.encode(M.decode(data)), data)

    def test_decoding_is_strict(self):
        good = M.encode(M.manifest('0123456789abcdef', 7, ELF))
        text = good.decode()
        for label, data in {
            'not UTF-8': b'\xff' + good,
            'not JSON': b'{',
            'a repeated key': text.replace('{"data_dir"', '{"health":"/health","data_dir"', 1).encode(),
            'a float': text.replace('"version_code":7', '"version_code":7.0').encode(),
            'NaN': text.replace('"version_code":7', '"version_code":NaN').encode(),
            'too large': good + b' ' * M.MAX_MANIFEST,
            'deep': b'[' * 100000,
            'not bytes': text,
        }.items():
            with self.subTest(label):
                with self.assertRaises(M.Refused) as caught:
                    M.decode(data)
                self.assertEqual(caught.exception.reason, 'manifest_invalid')

    def test_every_field_is_exactly_the_schema(self):
        good = M.manifest('0123456789abcdef', 7, ELF, LOCK, shared=True)
        changes = {
            'an extra field': {'app_id': '11111111-1111-4111-8111-111111111111'},
            'schema version 2': {'schema_version': 2},
            'schema version true': {'schema_version': True},
            'a project id of another shape': {'project_id': 'Project-1'},
            'version 0': {'version_code': 0},
            'a version past Android\'s': {'version_code': M.MAX_VERSION_CODE + 1},
            'a version as text': {'version_code': '7'},
            'another target': {'elf': {**ELF, 'target': 'x86_64-unknown-linux-gnu'}},
            'no digest': {'elf': {'size': 1, 'target': M.TARGET}},
            'an upper-case digest': {'elf': {**ELF, 'sha256': 'E' * 64}},
            'size 0': {'elf': {**ELF, 'size': 0}},
            'size true': {'elf': {**ELF, 'size': True}},
            'another port variable': {'listen': {'env': 'HTTP_PORT', 'default': 8080}},
            'another default port': {'listen': {'env': 'PORT', 'default': 80}},
            'a float port': {'listen': {'env': 'PORT', 'default': 8080.0}},
            'another health path': {'health': '/healthz'},
            'another data folder': {'data_dir': {'env': 'HOME'}},
            'another rose-signin': {'signin': {'revision': 'ee1e554f'}},
            'no account schema': {'schema': {'shared': 1}},
            'an unknown scope': {'schema': {'account': 1, 'other': 1}},
            'a negative schema': {'schema': {'account': -1}},
        }
        for label, change in changes.items():
            with self.subTest(label):
                with self.assertRaises(M.Refused) as caught:
                    M.check({**good, **change})
                self.assertEqual(caught.exception.reason, 'manifest_invalid')
        for missing in good:
            with self.subTest(missing=missing):
                with self.assertRaises(M.Refused):
                    M.check({k: v for k, v in good.items() if k != missing})
        for bad in (None, [], 'x'):
            with self.assertRaises(M.Refused):
                M.check(bad)

    def test_a_manifest_is_held_to_its_project_version_and_binary(self):
        data = tiny_elf()
        elf = self.file(data)
        record = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'target': M.TARGET}
        m = M.manifest('0123456789abcdef', 7, record)
        self.assertEqual(M.check(m, elf, project_id='0123456789abcdef', version_code=7), m)
        for kw in (dict(project_id='fedcba9876543210'), dict(version_code=8)):
            with self.subTest(kw=kw):
                with self.assertRaises(M.Refused):
                    M.check(m, **kw)
        other = self.file(data + b'\0')
        with self.assertRaises(M.Refused) as caught:
            M.check(m, other)
        self.assertEqual(caught.exception.reason, 'server_binary_invalid')
        dynamic = tiny_elf(interp=True)
        m2 = M.manifest('0123456789abcdef', 7, {**record, 'sha256': hashlib.sha256(dynamic).hexdigest(),
                                                'size': len(dynamic)})
        with self.assertRaises(M.Refused) as caught:
            M.check(m2, self.file(dynamic))
        self.assertEqual(caught.exception.reason, 'server_not_static')


if __name__ == '__main__':
    unittest.main()
