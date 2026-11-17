"""Preparing a client-server builder's inputs (rose.app_builder.prepare; docs/APP-BUILDER.md §5.2): the toolchain pin,
a crate store holding exactly a lock's crates.io packages by their recorded checksums, and the runtime root from a
clean checkout at the pinned sign-in revision. Synthetic files and a throwaway git repository only; offline."""
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rose.app_builder import crates, prepare, server_manifest
from rose.app_builder.providers.host import toolchain_digest

REG = prepare.REGISTRY


def lock(*packages):
    text = 'version = 4\n'
    for name, version, data in packages:
        text += f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n'
        if data is not None:
            text += f'source = "{REG}"\nchecksum = "{hashlib.sha256(data).hexdigest()}"\n'
    return text


class Digest(unittest.TestCase):
    def test_it_is_the_toolchain_pin_and_refuses_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / 'rust'
            (tree / 'bin').mkdir(parents=True)
            (tree / 'bin/rustc').write_bytes(b'x')
            self.assertEqual(prepare.digest(tree), toolchain_digest(tree))
            (tree / 'bin/link').symlink_to(tree / 'bin/rustc')
            with self.assertRaises(prepare.Refused):
                prepare.digest(tree)


class CrateStore(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.cache = self.tmp / 'cache'
        self.cache.mkdir()
        self.a, self.b = b'crate a bytes', b'crate b bytes'
        (self.cache / 'aa-1.0.0.crate').write_bytes(self.a)
        (self.cache / 'bb-2.1.0.crate').write_bytes(self.b)
        self.lock = self.tmp / 'Cargo.lock'
        self.lock.write_text(lock(('aa', '1.0.0', self.a), ('bb', '2.1.0', self.b), ('rose-app-runtime', '0.1.0', None)))

    def test_exactly_the_locks_registry_crates_by_their_checksums(self):
        out = self.tmp / 'store'
        result = prepare.crate_store([self.lock], self.cache, out)
        self.assertEqual(result['crates'], 2)  # the path crate comes from the runtime root, not the store
        index = json.loads((out / 'index.json').read_text())
        self.assertEqual(index, {'schemaVersion': 1, 'crates': {'aa@1.0.0': hashlib.sha256(self.a).hexdigest(),
                                                                'bb@2.1.0': hashlib.sha256(self.b).hexdigest()}})
        got = {(c.name, c.version) for c in crates.RecordedCrates(out).crates()}
        self.assertEqual(got, {('aa', '1.0.0'), ('bb', '2.1.0')})

    def test_a_missing_or_altered_crate_or_a_used_store_is_refused(self):
        (self.cache / 'bb-2.1.0.crate').write_bytes(b'tampered')
        with self.assertRaisesRegex(prepare.Refused, 'SHA-256 differs'):
            prepare.crate_store([self.lock], self.cache, self.tmp / 's1')
        (self.cache / 'bb-2.1.0.crate').unlink()
        with self.assertRaisesRegex(prepare.Refused, 'not in'):
            prepare.crate_store([self.lock], self.cache, self.tmp / 's2')
        self.assertFalse((self.tmp / 's1').exists() or (self.tmp / 's2').exists())  # nothing written on refusal
        used = self.tmp / 'used'
        used.mkdir()
        (used / 'x').write_text('x')
        with self.assertRaisesRegex(prepare.Refused, 'not empty'):
            prepare.crate_store([self.lock], self.cache, used)

    def test_a_failed_write_leaves_nothing_and_an_out_file_is_refused(self):
        out = self.tmp / 'store'
        with mock.patch.object(Path, 'write_text', side_effect=OSError(28, 'No space left on device')), \
                self.assertRaises(OSError):
            prepare.crate_store([self.lock], self.cache, out)
        self.assertFalse(out.exists())
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith('.store')], [])  # no stage left
        target = self.tmp / 'a-file'
        target.write_text('x')
        with self.assertRaisesRegex(prepare.Refused, 'not a directory'):
            prepare.crate_store([self.lock], self.cache, target)
        report = io.StringIO()
        with mock.patch.object(Path, 'write_text', side_effect=OSError(28, 'No space left on device')):
            code = prepare.main(['crate-store', '--lock', str(self.lock), '--from', str(self.cache), '--out',
                                 str(self.tmp / 'store2')], report)
        self.assertEqual((code, json.loads(report.getvalue())['status']), (1, 'refused'))  # one JSON line

    def test_locks_that_disagree_or_name_nothing_are_refused(self):
        other = self.tmp / 'Other.lock'
        other.write_text(lock(('aa', '1.0.0', b'different')))
        with self.assertRaisesRegex(prepare.Refused, 'disagree'):
            prepare.registry_packages([self.lock, other])
        other.write_text(lock(('rose-app-runtime', '0.1.0', None)))
        with self.assertRaisesRegex(prepare.Refused, 'no crates.io package'):
            prepare.registry_packages([other])


class RuntimeRoot(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / 'repo'
        for name in prepare.PATH_CRATES:
            (self.repo / 'workspace' / name / 'src').mkdir(parents=True)
            (self.repo / 'workspace' / name / 'Cargo.toml').write_text(f'[package]\nname = "{name}"\n')
            (self.repo / 'workspace' / name / 'src/lib.rs').write_text('// lib\n')
            (self.repo / 'workspace' / name / 'tests').mkdir()
            (self.repo / 'workspace' / name / 'tests/t.rs').write_text('// not copied\n')
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.email=t@example.org', '-c', 'user.name=t', 'commit', '-qm', 'pinned')
        self.pin = self.git('rev-parse', 'HEAD')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True, text=True,
                              check=True).stdout.strip()

    def test_the_path_crates_from_a_clean_checkout_at_the_pinned_signin(self):
        out = self.repo.parent / 'root'
        with mock.patch.object(server_manifest, 'SIGNIN_REVISION', self.pin):
            result = prepare.runtime_root(self.repo, out)
        self.assertEqual((result['revision'], result['signin']), (self.pin, self.pin))
        for name in prepare.PATH_CRATES:
            self.assertTrue((out / name / 'Cargo.toml').is_file())
            self.assertTrue((out / name / 'src/lib.rs').is_file())
            self.assertFalse((out / name / 'tests').exists())

    def test_a_missing_crate_copies_nothing(self):
        (self.repo / 'workspace/rose-signin/src/lib.rs').unlink()
        self.git('-c', 'user.email=t@example.org', '-c', 'user.name=t', 'commit', '-qam', 'drop lib')
        out = self.repo.parent / 'root'
        with mock.patch.object(server_manifest, 'SIGNIN_REVISION', self.pin), \
                self.assertRaisesRegex(prepare.Refused, 'rose-signin has no src/lib.rs'):
            prepare.runtime_root(self.repo, out)
        self.assertFalse(out.exists())

    def test_a_checkout_without_the_pinned_revision_says_how_to_get_it(self):
        with mock.patch.object(server_manifest, 'SIGNIN_REVISION', 'f' * 40), \
                self.assertRaisesRegex(prepare.Refused, 'does not hold the pinned sign-in revision .*full clone'):
            prepare.runtime_root(self.repo, self.repo.parent / 'root')

    def test_uncommitted_or_moved_signin_is_refused(self):
        with mock.patch.object(server_manifest, 'SIGNIN_REVISION', self.pin):
            (self.repo / 'workspace/rose-app-runtime/src/lib.rs').write_text('// edited\n')
            with self.assertRaisesRegex(prepare.Refused, 'uncommitted'):
                prepare.runtime_root(self.repo, self.repo.parent / 'r1')
            self.git('checkout', '--', '.')
            (self.repo / 'workspace/rose-signin/src/lib.rs').write_text('// a later rose-signin\n')
            self.git('-c', 'user.email=t@example.org', '-c', 'user.name=t', 'commit', '-qam', 'move signin')
            with self.assertRaisesRegex(prepare.Refused, 'pinned sign-in revision'):
                prepare.runtime_root(self.repo, self.repo.parent / 'r2')


class Cli(unittest.TestCase):
    def test_statuses(self):
        out = io.StringIO()
        self.assertEqual(prepare.main(['digest', '/nonexistent/tree'], out), 1)
        self.assertEqual(json.loads(out.getvalue())['status'], 'refused')
        with self.assertRaises(SystemExit) as cm:
            prepare.main([], io.StringIO())
        self.assertEqual(cm.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
