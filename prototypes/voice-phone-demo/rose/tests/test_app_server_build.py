"""The server half's offline build plumbing (docs/APP-BUILDER.md §7.6): the injected crate set and how it is checked
and unpacked (rose.app_builder.crates), and the cargo runs around it (rose.app_builder.server_build).

A fake cargo shows exactly what the builder runs and in what environment, and how cargo's failures map to the job's
categories. Real cargo then builds a dependency-free fixture and one against a small crate set made here. Those use
the toolchain already on the machine, for its own target, and never fetch anything: the product target is static
musl (§7.7), and pinning that toolchain on the builder host is the Local Server Worker's (blocker 6). ROSE_REQUIRE_CARGO
makes a missing cargo a failure rather than a skip (CI sets it).
"""
import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from rose.app_builder import crates as C
from rose.app_builder import server_build as S


def crate_bytes(name, version, deps=None, lib='pub fn v() -> u32 { 1 }\n', extra=(), manifest=None, top=None,
                compress=True):
    """A .crate as cargo packages one: a gzip tar of ``<name>-<version>/``. ``extra`` adds raw TarInfo entries."""
    top = top or f'{name}-{version}'
    if manifest is None:
        manifest = (f'[package]\nname = "{name}"\nversion = "{version}"\nedition = "2021"\n\n[dependencies]\n'
                    + ''.join(f'{k} = "{v}"\n' for k, v in (deps or {}).items()))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        files = [('src/lib.rs', lib)] + ([('Cargo.toml', manifest)] if manifest is not False else [])
        for path, text in files:
            data = text.encode()
            info = tarfile.TarInfo(f'{top}/{path}')
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
        for info, data in extra:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    raw = buf.getvalue()
    return gzip.compress(raw, mtime=0) if compress else raw


def entry(name, kind=tarfile.REGTYPE, data=b'x', link=''):
    info = tarfile.TarInfo(name)
    info.type, info.linkname = kind, link
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    return info, data if kind == tarfile.REGTYPE else None


def store(root: Path, crates: dict) -> C.RecordedCrates:
    """A RecordedCrates directory holding ``{"name@version": bytes}``."""
    root.mkdir(parents=True, exist_ok=True)
    index = {}
    for key, data in crates.items():
        digest = hashlib.sha256(data).hexdigest()
        (root / f'{digest}.crate').write_bytes(data)
        index[key] = digest
    (root / 'index.json').write_text(json.dumps({'schemaVersion': 1, 'crates': index}))
    return C.RecordedCrates(root)


class Tmp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._cleanup, tmp)
        self.tmp = Path(tmp.name)

    @staticmethod
    def _cleanup(tmp):
        for here, dirs, _ in os.walk(tmp.name):  # the vendored set is read-only
            os.chmod(here, 0o700)
        tmp.cleanup()


class CrateSet(Tmp):
    def test_the_index_is_read_strictly(self):
        good = crate_bytes('aa', '1.0.0')
        s = store(self.tmp / 's', {'aa@1.0.0': good})
        self.assertEqual([c.key for c in s.crates()], ['aa@1.0.0'])
        (self.tmp / 's/index.json').write_text(json.dumps({'schemaVersion': 1, 'crates': {
            'aa@1.0.0': hashlib.sha256(good).hexdigest(), 'aa@^1': '0' * 64, 'Bad@1.0.0': '0' * 64,
            'bb@1.0.0': 'not-a-digest', 'cc@1.0.0': 7}}))
        self.assertEqual([c.key for c in s.crates()], ['aa@1.0.0'])  # only well-formed entries
        for text in ('', 'nope', json.dumps({'schemaVersion': 2, 'crates': {}}), json.dumps([1])):
            (self.tmp / 's/index.json').write_text(text)
            self.assertEqual(s.crates(), [])
        self.assertEqual(C.RecordedCrates(self.tmp / 'missing').crates(), [])

    def test_declared_crates_must_be_in_the_set(self):
        with self.assertRaises(C.Refused) as caught:
            C.check_declared(['aa@1.0.0'], None)
        self.assertEqual(caught.exception.reason, 'no_crate_source')
        self.assertEqual(C.check_declared([], None), {})
        s = store(self.tmp / 's', {'aa@1.0.0': crate_bytes('aa', '1.0.0')})
        self.assertEqual(list(C.check_declared(['aa@1.0.0'], s)), ['aa@1.0.0'])
        with self.assertRaises(C.Refused) as caught:
            C.check_declared(['aa@1.0.1'], s)
        self.assertEqual(caught.exception.reason, 'crate_unavailable')

    def test_a_source_that_misbehaves_is_an_invalid_set(self):
        class Broken(C.CrateSource):
            def __init__(self, result):
                self.result = result

            def crates(self):
                if isinstance(self.result, Exception):
                    raise self.result
                return self.result
        dup = C.Crate('aa', '1.0.0', Path('/x'), '0' * 64)
        for result in (OSError('disk'), [dup, dup], [C.Crate('aa', '^1', Path('/x'), '0' * 64)],
                       [C.Crate('aa', '1.0.0', Path('/x'), 'E' * 64)], ['aa@1.0.0']):
            with self.subTest(result=result):
                with self.assertRaises(C.Refused) as caught:
                    C.check_declared([], Broken(result))
                self.assertEqual(caught.exception.reason, 'crate_set_invalid')
        with mock.patch.object(C, 'MAX_SET', 1):
            with self.assertRaises(C.Refused):
                C.check_declared([], Broken([dup, C.Crate('bb', '1.0.0', Path('/x'), '0' * 64)]))


class Vendor(Tmp):
    def vendor(self, crates):
        return C.vendor(store(self.tmp / 's', crates), self.tmp / 'cache')

    def test_every_crate_is_unpacked_with_cargo_checksums_and_left_read_only(self):
        data = crate_bytes('aa', '1.0.0', extra=[entry('aa-1.0.0/src/deep/mod.rs', data=b'// deep\n')])
        vendor = self.vendor({'aa@1.0.0': data, 'bb@0.1.0': crate_bytes('bb', '0.1.0')})
        crate = vendor / 'aa-1.0.0'
        checks = json.loads((crate / C.CHECKSUM_FILE).read_text())
        self.assertEqual(checks['package'], hashlib.sha256(data).hexdigest())
        self.assertEqual(set(checks['files']), {'Cargo.toml', 'src/lib.rs', 'src/deep/mod.rs'})
        self.assertEqual(checks['files']['src/deep/mod.rs'], hashlib.sha256(b'// deep\n').hexdigest())
        self.assertTrue((vendor / 'bb-0.1.0/src/lib.rs').is_file())
        for path in [vendor, *vendor.rglob('*')]:
            self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode) & 0o222, 0, path)

    def test_the_set_is_unpacked_once_and_reused(self):
        crates = {'aa@1.0.0': crate_bytes('aa', '1.0.0')}
        first = self.vendor(crates)
        with mock.patch.object(C, '_unpack', side_effect=AssertionError('unpacked again')):
            self.assertEqual(C.vendor(C.RecordedCrates(self.tmp / 's'), self.tmp / 'cache'), first)
        # A different set is a different folder; a damaged marker is rebuilt, not trusted.
        os.chmod(first, 0o700)
        (first / '.rose-vendor').chmod(0o600)
        (first / '.rose-vendor').write_text('other')
        self.assertEqual(C.vendor(C.RecordedCrates(self.tmp / 's'), self.tmp / 'cache'), first)
        self.assertTrue((first / 'aa-1.0.0/src/lib.rs').is_file())
        second = C.vendor(store(self.tmp / 's2', {'aa@1.0.1': crate_bytes('aa', '1.0.1')}), self.tmp / 'cache')
        self.assertNotEqual(second, first)

    def refused(self, data, key='aa@1.0.0', **patches):
        with mock.patch.multiple(C, **patches) if patches else mock.patch.object(C, 'MAX_SET', C.MAX_SET):
            with self.assertRaises(C.Refused) as caught:
                self.vendor({key: data})
        self.assertEqual(caught.exception.reason, 'crate_set_invalid')
        self.assertEqual([p.name for p in (self.tmp / 'cache').iterdir()], [])  # nothing half unpacked is kept
        shutil.rmtree(self.tmp / 's')
        return str(caught.exception)

    def test_damaged_or_hostile_crates_refuse_the_set(self):
        cases = {
            'a link': crate_bytes('aa', '1.0.0', extra=[entry('aa-1.0.0/x', tarfile.SYMTYPE, link='/etc/passwd')]),
            'a hard link': crate_bytes('aa', '1.0.0', extra=[entry('aa-1.0.0/x', tarfile.LNKTYPE,
                                                                   link='aa-1.0.0/src/lib.rs')]),
            'a device': crate_bytes('aa', '1.0.0', extra=[entry('aa-1.0.0/x', tarfile.CHRTYPE)]),
            'a fifo': crate_bytes('aa', '1.0.0', extra=[entry('aa-1.0.0/x', tarfile.FIFOTYPE)]),
            'a parent segment': crate_bytes('aa', '1.0.0', extra=[entry('aa-1.0.0/../escape.rs')]),
            'an absolute path': crate_bytes('aa', '1.0.0', extra=[entry('/tmp/escape.rs')]),
            'another folder': crate_bytes('aa', '1.0.0', extra=[entry('bb-1.0.0/src/lib.rs')]),
            'a dot segment': crate_bytes('aa', '1.0.0', extra=[entry('aa-1.0.0/./src/x.rs')]),
            'an empty segment': crate_bytes('aa', '1.0.0', extra=[entry('aa-1.0.0//x.rs')]),
            'a control character': crate_bytes('aa', '1.0.0', extra=[entry('aa-1.0.0/x\n.rs')]),
            'a backslash': crate_bytes('aa', '1.0.0', extra=[entry('aa-1.0.0/..\\x.rs')]),
            'an entry twice': crate_bytes('aa', '1.0.0', extra=[entry('aa-1.0.0/src/lib.rs')]),
            'its own checksums': crate_bytes('aa', '1.0.0', extra=[entry('aa-1.0.0/.cargo-checksum.json')]),
            'a file for its folder': crate_bytes('aa', '1.0.0', extra=[entry('aa-1.0.0')]),
            'no Cargo.toml': crate_bytes('aa', '1.0.0', manifest=False),
            'an unreadable Cargo.toml': crate_bytes('aa', '1.0.0', manifest='[package\n'),
            'another crate\'s Cargo.toml': crate_bytes('aa', '1.0.0', manifest='[package]\nname = "bb"\n'
                                                                                'version = "1.0.0"\n'),
            'another version\'s Cargo.toml': crate_bytes('aa', '1.0.0', manifest='[package]\nname = "aa"\n'
                                                                                  'version = "9.0.0"\n'),
            'under another name': crate_bytes('aa', '1.0.0', top='aa-9.9.9'),
            'not gzip': crate_bytes('aa', '1.0.0', compress=False),
            'not a tar': gzip.compress(b'just bytes' * 100),
            'truncated': crate_bytes('aa', '1.0.0')[:40],
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.refused(data)

    def test_bounds(self):
        bomb = crate_bytes('aa', '1.0.0', extra=[entry('aa-1.0.0/zeros', data=b'\0' * (3 * 1024 * 1024))])
        self.assertLess(len(bomb), 64 * 1024)
        self.assertIn('unpacks to more than', self.refused(bomb, MAX_UNPACKED=1024 * 1024))
        many = crate_bytes('aa', '1.0.0', extra=[entry(f'aa-1.0.0/f{i}') for i in range(30)])
        self.assertIn('entries', self.refused(many, MAX_ENTRIES=10))
        self.assertIn('larger', self.refused(crate_bytes('aa', '1.0.0'), MAX_CRATE=100))
        long = crate_bytes('aa', '1.0.0', extra=[entry('aa-1.0.0/' + 'x' * 600)])
        self.refused(long)

    def test_the_file_must_be_a_plain_file_with_the_recorded_digest(self):
        s = store(self.tmp / 's', {'aa@1.0.0': crate_bytes('aa', '1.0.0')})
        [crate] = s.crates()
        crate.path.write_bytes(crate.path.read_bytes() + b'\0')
        with self.assertRaises(C.Refused) as caught:
            C.vendor(s, self.tmp / 'cache')
        self.assertIn('digest', str(caught.exception))
        real = self.tmp / 'real.crate'
        real.write_bytes(crate_bytes('aa', '1.0.0'))
        crate.path.unlink()
        crate.path.symlink_to(real)  # never followed
        with self.assertRaises(C.Refused):
            C.vendor(s, self.tmp / 'cache')
        crate.path.unlink()
        with self.assertRaises(C.Refused) as caught:
            C.vendor(s, self.tmp / 'cache')
        self.assertIn('missing', str(caught.exception))
        crate.path.mkdir()
        with self.assertRaises(C.Refused):
            C.vendor(s, self.tmp / 'cache')


LOCK = textwrap.dedent('''\
    version = 4

    [[package]]
    name = "aa"
    version = "1.0.0"
    source = "registry+https://github.com/rust-lang/crates.io-index"
    checksum = "{aa}"

    [[package]]
    name = "rose-app-server"
    version = "0.0.0"
    dependencies = ["aa"]
    ''')


class Lock(unittest.TestCase):
    def setUp(self):
        self.digest = 'a' * 64
        self.by_key = {'aa@1.0.0': C.Crate('aa', '1.0.0', Path('/x'), self.digest)}

    def test_the_lock_is_held_to_the_set_and_to_delivered_versions(self):
        self.assertEqual(C.check_lock(LOCK.format(aa=self.digest), self.by_key),
                         [C.Locked('aa', '1.0.0', self.digest)])
        self.assertEqual(C.check_lock(LOCK.format(aa=self.digest), self.by_key, {'aa@1.0.0': self.digest}),
                         [C.Locked('aa', '1.0.0', self.digest)])
        cases = {
            'crate_changed': [(LOCK.format(aa='b' * 64), None), (LOCK.format(aa=self.digest),
                                                                 {'aa@1.0.0': 'c' * 64})],
            'crate_unavailable': [(LOCK.format(aa=self.digest).replace('1.0.0', '1.0.1', 1), None),
                                  (LOCK.format(aa=self.digest).replace('registry+https://github.com/rust-lang/'
                                                                       'crates.io-index', 'git+https://x/y'), None)],
            'crate_set_invalid': [('[[package', None), ('package = 3', None), ('[[package]]\nname = 3\n', None)],
        }
        for reason, texts in cases.items():
            for text, locked in texts:
                with self.subTest(reason=reason, text=text[:40]):
                    with self.assertRaises(C.Refused) as caught:
                        C.check_lock(text, self.by_key, locked)
                    self.assertEqual(caught.exception.reason, reason)


FAKE_CARGO = '''\
import json, os, sys
from pathlib import Path
record = Path(os.environ['FAKE_RECORD'])
calls = json.loads(record.read_text()) if record.exists() else []
manifest = Path(sys.argv[sys.argv.index('--manifest-path') + 1])
calls.append({'argv': sys.argv[1:], 'env': dict(os.environ), 'cwd': os.getcwd(),
              'lockBefore': (manifest.parent / 'Cargo.lock').read_text() if (manifest.parent / 'Cargo.lock').exists()
              else None, 'config': (Path(os.environ['CARGO_HOME']) / 'config.toml').read_text(),
              'cargoToml': manifest.read_text()})
record.write_text(json.dumps(calls))
step = sys.argv[1]
fail = os.environ.get('FAKE_FAIL_' + step.upper())
if fail:
    sys.stderr.write(fail.replace('@WORK@', str(manifest.parent.parent)))
    sys.exit(101)
if step == 'metadata':
    (manifest.parent / 'Cargo.lock').write_text(os.environ.get('FAKE_LOCK', 'version = 4\\n'))
    print('{}')
elif step == 'build':
    target = sys.argv[sys.argv.index('--target') + 1]
    out = Path(os.environ['CARGO_TARGET_DIR']) / target / 'release' / 'rose-app-server'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b'\\x7fELF fake server')
'''


class Passthrough(S.Isolation):
    """Test-only: runs the command as is and records what the builder asked it to confine. A real builder host
    supplies real isolation (blocker 3); nothing in rose.app_builder provides this."""

    def __init__(self, extra_env=None):
        self.calls, self.extra_env = [], extra_env or {}

    def command(self, argv, env, *, writable, readonly):
        self.calls.append({'argv': argv, 'writable': writable, 'readonly': readonly})
        return argv, {**env, **self.extra_env}


HANDLERS = 'pub fn hello() -> &\'static str { "hello" }\n'
MAIN = '#[path = "../src/handlers/mod.rs"]\nmod handlers;\n\nfn main() { println!("{}", handlers::hello()); }\n'


def snapshot(root: Path, handlers=HANDLERS) -> Path:
    (root / 'server/src/handlers').mkdir(parents=True)
    (root / 'server/src/handlers/mod.rs').write_text(handlers)
    return root


class FakeCargo(Tmp):
    def setUp(self):
        super().setUp()
        self.toolchain = self.tmp / 'toolchain'
        (self.toolchain / 'bin').mkdir(parents=True)
        (self.toolchain / 'lib/rustlib' / S.TARGET / 'lib').mkdir(parents=True)
        cargo = self.toolchain / 'bin/cargo'
        cargo.write_text(f'#!{sys.executable}\n' + FAKE_CARGO)
        cargo.chmod(0o755)
        (self.toolchain / 'bin/rustc').write_text('')
        self.tools = S.RustTools(self.toolchain, timeout_s=60)
        self.record = self.tmp / 'record.json'
        self.snap = snapshot(self.tmp / 'snap')
        self.set = store(self.tmp / 'set', {'aa@1.0.0': crate_bytes('aa', '1.0.0')})
        self.digest = self.set.crates()[0].sha256
        self.n = 0

    def build(self, isolation=None, fail=None, lock=None, **kw):
        self.n += 1
        env = {'FAKE_RECORD': str(self.record), 'FAKE_LOCK': lock or LOCK.format(aa=self.digest)}
        for step, text in (fail or {}).items():
            env['FAKE_FAIL_' + step.upper()] = text
        isolation = isolation or Passthrough(env)
        kw.setdefault('source', self.set)
        kw.setdefault('declared', ['aa@1.0.0'])
        return S.build(self.snap, self.tmp / f'work{self.n}', kw.pop('declared'), kw.pop('source'),
                       kw.pop('tools', self.tools), isolation, cache=self.tmp / 'cache', generated={S.ENTRY: MAIN},
                       **kw), isolation

    def calls(self):
        return json.loads(self.record.read_text())

    def test_without_isolation_or_a_toolchain_nothing_runs(self):
        with self.assertRaises(S.BuildFailed) as caught:
            S.build(self.snap, self.tmp / 'w', [], None, self.tools, None, cache=self.tmp / 'c',
                    generated={S.ENTRY: MAIN})
        self.assertEqual((caught.exception.category, caught.exception.reason),
                         ('toolchain', 'build_isolation_unavailable'))
        for tools in (None, S.RustTools(self.tmp / 'none'), S.RustTools(self.toolchain, target='x86_64-apple-darwin'),
                      S.RustTools(self.toolchain, target='$(evil)')):
            with self.subTest(tools=tools):
                with self.assertRaises(S.BuildFailed) as caught:
                    self.build(tools=tools)
                self.assertEqual((caught.exception.category, caught.exception.reason),
                                 ('toolchain', 'rust_toolchain_unavailable'))
        self.assertFalse(self.record.exists())

    def test_what_cargo_is_run_with(self):
        os.environ['ROSE_TEST_SECRET'] = 'do-not-pass'
        self.addCleanup(os.environ.pop, 'ROSE_TEST_SECRET')
        result, isolation = self.build()
        metadata, build = self.calls()
        work = self.tmp / 'work1'
        self.assertEqual(metadata['argv'], ['metadata', '--offline', '--format-version', '1', '--manifest-path',
                                            str(work / 'server/Cargo.toml')])
        self.assertEqual(build['argv'], ['build', '--offline', '--frozen', '--release', '--target', S.TARGET,
                                         '--manifest-path', str(work / 'server/Cargo.toml')])
        env = {k: v for k, v in build['env'].items() if not k.startswith('FAKE_')}
        vendor = isolation.calls[0]['readonly'][1]
        self.assertEqual(env, {
            'PATH': f'{self.toolchain}/bin:/usr/bin:/bin', 'HOME': str(work / 'home'),
            'CARGO_HOME': str(work / 'cargo-home'), 'CARGO_TARGET_DIR': str(work / 'target'),
            'TMPDIR': str(work / 'tmp'), 'LANG': 'C.UTF-8', 'RUSTC': str(self.toolchain / 'bin/rustc'),
            'CARGO_NET_OFFLINE': 'true', 'CARGO_INCREMENTAL': '0', 'CARGO_TERM_COLOR': 'never',
            'CARGO_ENCODED_RUSTFLAGS': f'--remap-path-prefix={work}=/rose-build\x1f'
                                       f'--remap-path-prefix={vendor}=/rose-crates'})
        self.assertEqual([(c['writable'], c['readonly']) for c in isolation.calls],
                         [(work, (self.toolchain, vendor))] * 2)
        self.assertTrue((vendor / 'aa-1.0.0/.cargo-checksum.json').is_file())
        self.assertIn('[net]\noffline = true', build['config'])
        self.assertIn(f'replace-with = "rose-crates"', build['config'])
        self.assertIn(f'directory = "{vendor}"', build['config'])
        toml = build['cargoToml']
        for line in ('name = "rose-app-server"', 'build = false', 'autolib = false', 'autobins = false',
                     f'path = "{S.ENTRY}"', 'aa = "=1.0.0"', '[workspace]'):
            self.assertIn(line + '\n', toml)
        self.assertEqual(build['cwd'], str(work / 'server'))
        self.assertEqual((work / 'server/src/handlers/mod.rs').read_text(), HANDLERS)
        self.assertEqual((work / 'server/rose/main.rs').read_text(), MAIN)
        self.assertEqual(result.target, S.TARGET)
        self.assertEqual(result.sha256, hashlib.sha256(b'\x7fELF fake server').hexdigest())
        self.assertEqual(result.record(), {'sha256': result.sha256, 'size': 16, 'target': S.TARGET})
        self.assertEqual(result.crates, [C.Locked('aa', '1.0.0', self.digest)])
        self.assertEqual(result.cargo_lock, LOCK.format(aa=self.digest))

    def test_the_last_delivered_lock_is_where_resolution_starts(self):
        self.build(previous_lock='# the last version\'s lock\n')
        self.assertEqual(self.calls()[0]['lockBefore'], '# the last version\'s lock\n')

    def test_no_crates_needs_no_set(self):
        result, isolation = self.build(declared=[], source=None, lock='version = 4\n')
        self.assertEqual(isolation.calls[0]['readonly'], (self.toolchain,))
        self.assertNotIn('[source', self.calls()[0]['config'])
        self.assertEqual(result.crates, [])

    def test_crate_set_refusals_stop_before_cargo(self):
        for declared, source, reason in ((['aa@1.0.0'], None, 'no_crate_source'),
                                         (['aa@2.0.0'], self.set, 'crate_unavailable')):
            with self.subTest(reason=reason):
                with self.assertRaises(S.BuildFailed) as caught:
                    self.build(declared=declared, source=source)
                self.assertEqual((caught.exception.category, caught.exception.reason), ('dependency', reason))
        self.assertFalse(self.record.exists())

    def test_the_resolved_lock_is_checked_before_building(self):
        with self.assertRaises(S.BuildFailed) as caught:
            self.build(locked={'aa@1.0.0': 'f' * 64})
        self.assertEqual((caught.exception.category, caught.exception.reason), ('dependency', 'crate_changed'))
        self.assertEqual([c['argv'][0] for c in self.calls()], ['metadata'])  # never built

    def test_cargo_failures_map_to_categories(self):
        cases = [
            ('metadata', 'error: no matching package named `dd` found\nlocation searched: directory source',
             'dependency', 'crate_unavailable', 'dd'),
            ('metadata', 'error: failed to select a version for the requirement `aa = "^2"`\ncandidate versions found',
             'dependency', 'crate_unavailable', 'aa'),
            ('build', 'error: checksum for `aa v1.0.0` changed between lock files', 'dependency', 'crate_changed', ''),
            ('build', 'error: the listed checksum of `@WORK@/x/src/lib.rs` has changed:', 'dependency',
             'crate_changed', ''),
            ('build', 'error[E0463]: can\'t find crate for `std`\n  = note: the `x86_64-unknown-linux-musl` target '
                      'may not be installed', 'toolchain', 'rust_toolchain_unavailable', ''),
            ('build', 'error: linker `cc` not found', 'toolchain', 'rust_toolchain_unavailable', ''),
            ('build', 'error[E0308]: mismatched types\n --> @WORK@/server/src/handlers/mod.rs:1:2\n'
                      'error: could not compile `rose-app-server` (bin "rose-app-server") due to 1 previous error',
             'compile', 'server_compile', ''),
            ('build', 'error: could not compile `serde` (lib) due to 3 previous errors', 'dependency', 'crate_compile',
             'serde'),
            ('build', 'Segmentation fault', 'compile', 'server_build', ''),
        ]
        for step, text, category, reason, named in cases:
            with self.subTest(text=text[:50]):
                with self.assertRaises(S.BuildFailed) as caught:
                    self.build(fail={step: text})
                failed = caught.exception
                self.assertEqual((failed.category, failed.reason), (category, reason))
                self.assertIn(named, failed.summary)
                self.assertNotIn(str(self.tmp), failed.log)  # the job's paths are redacted
                self.assertNotIn(str(self.tmp), failed.summary)

    def test_timeouts_and_cancellation_are_the_jobs_own(self):
        slow = self.toolchain / 'bin/cargo'
        slow.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(30)\n')
        with self.assertRaises(S.StepFailed) as caught:
            self.build(tools=S.RustTools(self.toolchain, timeout_s=0.5))
        self.assertEqual(caught.exception.category, 'timeout')
        with self.assertRaises(S.StepFailed) as caught:
            self.build(cancelled=lambda: True, tools=S.RustTools(self.toolchain, timeout_s=60))
        self.assertEqual(caught.exception.category, 'cancelled')

    def test_a_missing_binary_or_lock_is_the_toolchains_fault(self):
        cargo = self.toolchain / 'bin/cargo'
        cargo.write_text(f'#!{sys.executable}\nimport sys\nsys.exit(0)\n')  # succeeds and writes nothing
        with self.assertRaises(S.BuildFailed) as caught:
            self.build()
        self.assertEqual(caught.exception.reason, 'rust_toolchain_unavailable')

    def test_generated_files_stay_the_builders(self):
        for generated in ({}, {'src/main.rs': MAIN}, {S.ENTRY: MAIN, 'Cargo.toml': ''},
                          {S.ENTRY: MAIN, 'rose/../x.rs': ''}):
            with self.subTest(generated=list(generated)):
                with self.assertRaises(ValueError):
                    S.build(self.snap, self.tmp / 'w', [], None, self.tools, Passthrough(), cache=self.tmp / 'c',
                            generated=generated)

    def test_the_writers_tree_is_copied_without_following_links(self):
        (self.snap / 'server/src/handlers/link.rs').symlink_to(self.snap / 'server/src/handlers/mod.rs')
        with self.assertRaises(S.BuildFailed) as caught:
            self.build()
        self.assertEqual(caught.exception.reason, 'layout_invalid')
        (self.snap / 'server/src/handlers/link.rs').unlink()
        (self.snap / 'server/src/dirlink').symlink_to(self.snap / 'server/src/handlers')
        with self.assertRaises(S.BuildFailed) as caught:
            self.build()
        self.assertEqual(caught.exception.reason, 'layout_invalid')

    def test_cargo_toml_rejects_anything_but_exact_versions(self):
        self.assertIn('serde = "=1.0.219"\n', S.cargo_toml(['serde@1.0.219']))
        for bad in ('serde@^1', 'serde = "*"\nx@1.0.0', 'Serde@1.0.0'):
            with self.subTest(bad=bad):
                with self.assertRaises(S.BuildFailed):
                    S.cargo_toml([bad])


HAS_CARGO = shutil.which('cargo') is not None and shutil.which('rustc') is not None


@unittest.skipUnless(HAS_CARGO or os.environ.get('ROSE_REQUIRE_CARGO'), 'needs a Rust toolchain (cargo and rustc)')
class RealCargo(Tmp):
    """Real cargo, offline, for the machine's own target (module docstring)."""

    def setUp(self):
        super().setUp()
        sysroot = subprocess.run(['rustc', '--print', 'sysroot'], capture_output=True, text=True, check=True)
        host = subprocess.run(['rustc', '-vV'], capture_output=True, text=True, check=True).stdout
        target = next(line.split(': ', 1)[1] for line in host.splitlines() if line.startswith('host: '))
        self.tools = S.RustTools(Path(sysroot.stdout.strip()), target=target, timeout_s=300)
        self.assertIsNone(self.tools.available())
        self.n = 0

    def build(self, snap, declared=(), source=None, main=MAIN, **kw):
        self.n += 1
        return S.build(snap, self.tmp / f'work{self.n}', list(declared), source, self.tools, Passthrough(),
                       cache=self.tmp / 'cache', generated={S.ENTRY: main}, **kw)

    def test_a_dependency_free_server_builds(self):
        snap = snapshot(self.tmp / 'snap')
        result = self.build(snap)
        data = result.elf.read_bytes()
        self.assertEqual(data[:4], b'\x7fELF')
        self.assertEqual((result.sha256, result.size), (hashlib.sha256(data).hexdigest(), len(data)))
        self.assertEqual(result.crates, [])
        self.assertNotIn(str(self.tmp).encode(), data)  # the job's paths are remapped
        # The writer's code failing to compile is the writer's: category compile.
        broken = snapshot(self.tmp / 'broken', handlers='pub fn hello() -> &\'static str { 1 }\n')
        with self.assertRaises(S.BuildFailed) as caught:
            self.build(broken)
        self.assertEqual((caught.exception.category, caught.exception.reason), ('compile', 'server_compile'))
        self.assertIn('mismatched types', caught.exception.log)
        self.assertNotIn(str(self.tmp), caught.exception.log)

    def test_crates_resolve_from_the_set_and_a_rebuild_keeps_its_lock(self):
        source = store(self.tmp / 'set', {
            'aa@1.0.0': crate_bytes('aa', '1.0.0', lib='pub fn v() -> u32 { 100 }\n'),
            'aa@1.0.1': crate_bytes('aa', '1.0.1', lib='pub fn v() -> u32 { 101 }\n'),
            'bb@1.0.0': crate_bytes('bb', '1.0.0', deps={'aa': '1'}, lib='pub fn v() -> u32 { aa::v() }\n'),
            'cc@0.1.0': crate_bytes('cc', '0.1.0'),
        })
        digests = {c.key: c.sha256 for c in source.crates()}
        main = 'fn main() { println!("{}", bb::v()); }\n'
        snap = snapshot(self.tmp / 'snap')
        first = self.build(snap, ['bb@1.0.0'], source, main=main)
        self.assertEqual(first.crates, [C.Locked('aa', '1.0.1', digests['aa@1.0.1']),
                                        C.Locked('bb', '1.0.0', digests['bb@1.0.0'])])
        self.assertEqual(subprocess.run([first.elf], capture_output=True, text=True).stdout, '101\n')
        # A version that used aa 1.0.0: its lock is kept when the next one adds a crate.
        pinned = first.cargo_lock.replace('version = "1.0.1"', 'version = "1.0.0"').replace(
            digests['aa@1.0.1'], digests['aa@1.0.0'])
        second = self.build(snap, ['bb@1.0.0', 'cc@0.1.0'], source, main=main, previous_lock=pinned)
        self.assertEqual([c.key for c in second.crates], ['aa@1.0.0', 'bb@1.0.0', 'cc@0.1.0'])
        self.assertEqual(subprocess.run([second.elf], capture_output=True, text=True).stdout, '100\n')
        # A crate a delivered version used, with other contents, is refused; so is one the set lacks.
        with self.assertRaises(S.BuildFailed) as caught:
            self.build(snap, ['bb@1.0.0'], source, main=main, locked={'aa@1.0.1': '0' * 64})
        self.assertEqual(caught.exception.reason, 'crate_changed')
        short = store(self.tmp / 'short', {'bb@1.0.0': crate_bytes('bb', '1.0.0', deps={'aa': '1'})})
        with self.assertRaises(S.BuildFailed) as caught:
            self.build(snap, ['bb@1.0.0'], short, main=main)
        self.assertEqual((caught.exception.category, caught.exception.reason), ('dependency', 'crate_unavailable'))
        self.assertIn('aa', caught.exception.summary)


if __name__ == '__main__':
    unittest.main()
