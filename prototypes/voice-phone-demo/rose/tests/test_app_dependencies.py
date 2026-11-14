"""Pinned pure-Java JAR libraries for generated apps (docs/APP-BUILDER.md §2, "Libraries").

Every library here is a tiny fake made by the test itself: hand-built JARs for the checks, and for the real build one
compiled with the pinned JDK. Nothing is downloaded and no real library store exists. The build tests need the pinned
SDK; the rest run anywhere."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from rose.app_builder import dependencies as D
from rose.app_builder import template, toolchain
from rose.app_builder.service import Builder
from rose.tests.test_app_builder import HAS_SDK, JAVA_HOME, SDK, fake_backup

P = 'app.rose.gen.p1'
CLASS = b'\xca\xfe\xba\xbe\x00\x00\x00\x34' + b'\x00' * 8  # magic, minor 0, major 52 (Java 8)


def jar(path: Path, entries: dict) -> bytes:
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return path.read_bytes()


class Store(D.RecordedStore):
    """A RecordedStore the test fills in: add(coordinate, bytes) records the bytes' digest."""

    def __init__(self, root: Path):
        super().__init__(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = {}
        self._write()

    def _write(self):
        (self.root / 'index.json').write_text(json.dumps({'schemaVersion': 1, 'artifacts': self.index}))

    def add(self, coordinate: str, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        (self.root / f'{digest}.jar').write_bytes(data)
        self.index[coordinate] = digest
        self._write()
        return digest


class Declared(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name)

    def declare(self, value):
        (self.source / D.FILE).write_text(value if isinstance(value, str) else json.dumps(value))

    def test_none_or_a_valid_list(self):
        self.assertEqual(D.declared(self.source), [])
        self.declare({'schemaVersion': 1, 'dependencies': ['com.example:lib:1.0', 'org.x:y:2.1-beta+3']})
        self.assertEqual(D.declared(self.source), ['com.example:lib:1.0', 'org.x:y:2.1-beta+3'])

    def test_malformed_declarations_are_compile_failures(self):
        for value in ('{', '[]', {'schemaVersion': 2, 'dependencies': []}, {'schemaVersion': 1},
                      {'schemaVersion': True, 'dependencies': []}, {'schemaVersion': 1, 'dependencies': 'a:b:c'},
                      {'schemaVersion': 1, 'dependencies': [], 'extra': 1},
                      {'schemaVersion': 1, 'dependencies': ['a:b']}, {'schemaVersion': 1, 'dependencies': [7]},
                      {'schemaVersion': 1, 'dependencies': ['a:b:1', 'a:b:2']},
                      {'schemaVersion': 1, 'dependencies': ['../x:y:1']},
                      {'schemaVersion': 1, 'dependencies': [f'g:a{i}:1' for i in range(D.MAX_DECLARED + 1)]}):
            with self.subTest(value=value):
                self.declare(value)
                with self.assertRaises(D.Refused) as refused:
                    D.declared(self.source)
                self.assertTrue(refused.exception.malformed)

    def test_an_aar_or_classifier_is_an_unsupported_library_not_a_typo(self):
        self.declare({'schemaVersion': 1, 'dependencies': ['androidx.core:core:1.12.0:aar']})
        with self.assertRaises(D.Refused) as refused:
            D.declared(self.source)
        self.assertFalse(refused.exception.malformed)

    def test_the_template_check(self):
        for rel, text in template.skeleton(P, 'x').items():
            (self.source / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.source / rel).write_text(text)
        os.chmod(self.source, 0o700)
        self.declare({'schemaVersion': 1, 'dependencies': ['com.example:lib:1.0']})
        template.check(self.source, P)  # a declaration is allowed
        self.declare('{')
        with self.assertRaises(template.TemplateRefused) as refused:
            template.check(self.source, P)
        self.assertFalse(refused.exception.dependency)
        (self.source / D.FILE).unlink()
        (self.source / 'src/lib.jar').write_bytes(b'PK')  # the writer still never ships a library
        with self.assertRaises(template.TemplateRefused) as refused:
            template.check(self.source, P)
        self.assertTrue(refused.exception.dependency)


class Verify(unittest.TestCase):
    """Resolution and verification with hand-built JARs (no SDK)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'store')
        self.n = 0

    def make(self, entries: dict) -> bytes:
        self.n += 1
        return jar(self.root / f'build{self.n}.jar', entries)

    def resolve(self, coordinates, resolver='store', locked=None, framework=frozenset({'javax/crypto/Cipher.class'})):
        self.n += 1
        return D.resolve(coordinates, self.store if resolver == 'store' else resolver, self.root / f'libs{self.n}', P,
                         framework, locked)

    def refused(self, entries, message):
        self.store.add('g:a:1', self.make(entries))
        with self.assertRaises(D.Refused) as refused:
            self.resolve(['g:a:1'])
        self.assertIn(message, str(refused.exception))
        self.assertFalse(refused.exception.malformed)

    def test_a_verified_library_is_a_job_local_jar_of_exactly_its_classes(self):
        data = self.make({'META-INF/MANIFEST.MF': 'Manifest-Version: 1.0\n', 'META-INF/LICENSE.txt': 'MIT',
                          'META-INF/maven/g/a/pom.xml': '<project/>', 'module-info.class': CLASS,
                          'com/example/lib/': '', 'com/example/lib/Greeter.class': CLASS,
                          'com/example/lib/Greeter$1.class': CLASS, 'com/example/lib/package-info.class': CLASS})
        digest = self.store.add('g:a:1', data)
        [lib] = self.resolve(['g:a:1'])
        self.assertEqual((lib.coordinate, lib.sha256, lib.size), ('g:a:1', digest, len(data)))
        self.assertTrue(lib.jar.is_relative_to(self.root / f'libs{self.n}'))
        with zipfile.ZipFile(lib.jar) as z:
            self.assertEqual(z.namelist(), ['com/example/lib/Greeter$1.class', 'com/example/lib/Greeter.class',
                                            'com/example/lib/package-info.class'])
        self.assertEqual(sorted(p.name for p in lib.jar.parent.iterdir()), ['0.jar'])  # no unchecked copy left

    def test_without_a_resolver_or_an_entry_it_is_an_honest_dependency_failure(self):
        for resolver, message in ((None, 'no library store'), ('store', 'not available on this builder')):
            with self.subTest(resolver=resolver), self.assertRaises(D.Refused) as refused:
                self.resolve(['g:missing:1'], resolver)
            self.assertIn(message, str(refused.exception))
            self.assertFalse(refused.exception.malformed)
        self.assertEqual(self.resolve([], None), [])  # nothing declared needs no store

    def test_tampered_or_relocked_bytes_are_refused(self):
        digest = self.store.add('g:a:1', self.make({'g/A.class': CLASS}))
        (self.store.root / f'{digest}.jar').write_bytes(self.make({'g/B.class': CLASS}))  # bytes changed in place
        with self.assertRaisesRegex(D.Refused, 'does not match its recorded digest'):
            self.resolve(['g:a:1'])
        self.store.add('g:a:1', self.make({'g/C.class': CLASS}))  # the store re-recorded the coordinate
        with self.assertRaisesRegex(D.Refused, 'not the one this app was last built with'):
            self.resolve(['g:a:1'], locked={'g:a:1': digest})
        self.assertEqual(len(self.resolve(['g:a:1'], locked={'g:a:2': digest})), 1)  # another coordinate's lock

    def test_classes_in_reserved_framework_or_app_namespaces_are_refused(self):
        for name in ('java/lang/Evil.class', 'android/app/Evil.class', 'dalvik/system/Evil.class',
                     'javax/crypto/Cipher.class', 'app/rose/gen/p1/MainActivity.class', 'app/rose/gen/p1/R.class'):
            with self.subTest(name=name):
                self.refused({name: CLASS}, 'which belongs to')
        self.assertEqual(len(self.resolve_one({'javax/inject/Inject.class': CLASS})), 1)  # not the framework's

    def resolve_one(self, entries):
        self.store.add('g:ok:1', self.make(entries))
        return self.resolve(['g:ok:1'])

    def test_two_libraries_defining_one_class_are_refused(self):
        self.store.add('g:a:1', self.make({'g/Same.class': CLASS}))
        self.store.add('g:b:1', self.make({'g/Same.class': CLASS}))
        with self.assertRaisesRegex(D.Refused, 'both define g.Same'):
            self.resolve(['g:a:1', 'g:b:1'])

    def test_malformed_archives_names_and_resources_are_refused(self):
        self.store.add('g:a:1', b'not a zip')
        with self.assertRaisesRegex(D.Refused, 'not a valid JAR'):
            self.resolve(['g:a:1'])
        for entries, message in (({'../evil.class': CLASS}, 'unsafe name'), ({'/abs/X.class': CLASS}, 'unsafe name'),
                                 ({'g\\X.class': CLASS}, 'unsafe name'), ({'g//X.class': CLASS}, 'unsafe name'),
                                 ({'g/./X.class': CLASS}, 'unsafe name'),
                                 ({'g/config.properties': 'x=1'}, 'only class files'),
                                 ({'lib/arm64-v8a/libx.so': b'\x7fELF'}, 'only class files'),
                                 ({'res/values/strings.xml': '<resources/>'}, 'only class files'),
                                 ({'AndroidManifest.xml': '<manifest/>'}, 'only class files'),
                                 ({'classes.dex': b'dex'}, 'only class files'),
                                 ({'g/my-class.class': CLASS}, 'only class files'),
                                 ({'META-INF/services/g.Spi': 'g.Impl'}, 'does not package'),
                                 ({'META-INF/versions/11/g/X.class': CLASS}, 'multi-release'),
                                 ({'g/X.class': b'\x00\x00\x00\x00' + CLASS[4:]}, 'cannot read'),
                                 ({'g/X.class': CLASS[:8] + b''}, 'cannot read'),
                                 ({'g/X.class': CLASS[:6] + (D.MAX_CLASS_MAJOR + 1).to_bytes(2, 'big') + CLASS[8:]},
                                  'cannot read')):
            with self.subTest(entries=list(entries)):
                self.refused(entries, message)

    def test_a_duplicate_or_encrypted_entry_is_refused(self):
        path = self.root / 'dup.jar'
        with zipfile.ZipFile(path, 'w') as z, mock.patch('warnings.warn'):
            z.writestr('g/X.class', CLASS)
            z.writestr('g/X.class', CLASS)
        self.store.add('g:a:1', path.read_bytes())
        with self.assertRaisesRegex(D.Refused, 'same entry twice'):
            self.resolve(['g:a:1'])
        data = bytearray(self.make({'g/X.class': CLASS}))  # zipfile never writes the flag: set it in the bytes
        central = data.index(b'PK\x01\x02')
        data[central + 8] |= 0x1  # the central directory's general purpose flags: encrypted
        self.store.add('g:a:1', bytes(data))
        with self.assertRaisesRegex(D.Refused, 'encrypted'):
            self.resolve(['g:a:1'])

    def test_size_and_entry_bounds(self):
        for bound, value, entries, message in (
                ('MAX_JAR', 200, {'g/X.class': CLASS * 40}, 'larger than this builder accepts'),
                ('MAX_ENTRIES', 2, {'g/A.class': CLASS, 'g/B.class': CLASS, 'g/C.class': CLASS}, 'more entries'),
                ('MAX_UNCOMPRESSED', 20, {'g/A.class': CLASS, 'g/B.class': CLASS}, 'once unpacked'),
                ('MAX_ENTRY', 10, {'g/A.class': CLASS}, 'an entry larger')):
            with self.subTest(bound=bound), mock.patch.object(D, bound, value):
                self.refused(entries, message)
        self.store.add('g:a:1', self.make({'g/A.class': CLASS * 30}))
        self.store.add('g:b:1', self.make({'g/B.class': CLASS * 30}))
        with mock.patch.object(D, 'MAX_TOTAL', 600), self.assertRaisesRegex(D.Refused, 'larger than'):
            self.resolve(['g:a:1', 'g:b:1'])  # each fits, together they do not

    def test_a_link_or_non_file_in_the_store_is_refused(self):
        digest = self.store.add('g:a:1', self.make({'g/A.class': CLASS}))
        real = self.store.root / f'{digest}.jar'
        moved = self.root / 'elsewhere.jar'
        real.rename(moved)
        real.symlink_to(moved)
        with self.assertRaisesRegex(D.Refused, 'missing from the library store'):
            self.resolve(['g:a:1'])
        real.unlink()
        real.mkdir()
        with self.assertRaisesRegex(D.Refused, 'not a regular file'):
            self.resolve(['g:a:1'])

    def test_a_resolver_that_fails_or_lies_is_refused(self):
        class Broken(D.Resolver):
            def resolve(self, coordinate):
                raise RuntimeError('store down')

        class Other(D.Resolver):
            def __init__(self, artifact):
                self.artifact = artifact

            def resolve(self, coordinate):
                return self.artifact
        with self.assertRaisesRegex(D.Refused, 'could not look up'):
            self.resolve(['g:a:1'], Broken())
        path = self.root / 'x.jar'
        data = jar(path, {'g/A.class': CLASS})
        with self.assertRaisesRegex(D.Refused, 'not available'):
            self.resolve(['g:a:1'], Other(D.Artifact('g:b:1', path, hashlib.sha256(data).hexdigest())))
        with self.assertRaisesRegex(D.Refused, 'no valid digest'):
            self.resolve(['g:a:1'], Other(D.Artifact('g:a:1', path, 'nope')))

    def test_a_recorded_store_ignores_a_malformed_index(self):
        self.store.add('g:a:1', self.make({'g/A.class': CLASS}))
        self.assertEqual(self.store.available(), ['g:a:1'])
        for text in ('{', '[]', json.dumps({'schemaVersion': 2, 'artifacts': {}}),
                     json.dumps({'schemaVersion': 1, 'artifacts': {'g:a:1': 'short', 'bad': 'a' * 64}})):
            (self.store.root / 'index.json').write_text(text)
            self.assertEqual((self.store.available(), self.store.resolve('g:a:1')), ([], None))


LIBRARY = '''package com.example.fakelib;

public final class Greeter {
    private Greeter() {}
    public static String greet(String who) { return "Hello, " + who; }
}
'''


@unittest.skipUnless(HAS_SDK or os.environ.get('ROSE_REQUIRE_APP_BUILD'), 'needs the pinned Android SDK (ANDROID_SDK_ROOT) and a JDK')
class LibraryBuild(unittest.TestCase):
    """The real build with a fake library compiled by the pinned JDK: compilation and signature verification only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'builder'
        self.tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=300, key_creation=True)
        self.store = Store(Path(self.tmp.name) / 'store')

    def library(self, greeting='Hello, ') -> bytes:
        src = Path(tempfile.mkdtemp(dir=self.tmp.name))
        (src / 'com/example/fakelib').mkdir(parents=True)
        (src / 'com/example/fakelib/Greeter.java').write_text(LIBRARY.replace('Hello, ', greeting))
        subprocess.run([self.tools.java(), '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '8', '-target', '8',
                        '-Xlint:-options', '-d', src / 'out', src / 'com/example/fakelib/Greeter.java'], check=True,
                       capture_output=True)
        return jar(src / 'lib.jar', {'META-INF/MANIFEST.MF': 'Manifest-Version: 1.0\n',
                                     'com/example/fakelib/Greeter.class':
                                         (src / 'out/com/example/fakelib/Greeter.class').read_bytes()})

    def builder(self, generate, resolver='store'):
        b = Builder(self.root, self.tools, generate, backup=fake_backup,
                    resolver=self.store if resolver == 'store' else resolver)
        self.addCleanup(b.ledger.close)
        return b

    @staticmethod
    def using(coordinate='com.example:fakelib:1.0', declare=True):
        def generate(source, kind, request, cancelled):
            java = next(source.glob('src/**/MainActivity.java'))
            java.write_text(java.read_text()
                            .replace('import android.os.Bundle;',
                                     'import android.os.Bundle;\nimport com.example.fakelib.Greeter;')
                            .replace('text.setText(R.string.app_name);', 'text.setText(Greeter.greet("owner"));'))
            if declare:
                (source / D.FILE).write_text(json.dumps({'schemaVersion': 1, 'dependencies': [coordinate]}))
        return generate

    def run_job(self, b, out):
        self.assertTrue(b.run_next())
        return b.job(out['jobId'])

    def test_a_declared_library_is_built_in_signed_and_recorded(self):
        data = self.library()
        digest = self.store.add('com.example:fakelib:1.0', data)
        b = self.builder(self.using())
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'Greeter app'})
        job = self.run_job(b, out)
        self.assertEqual(job['state'], 'ready', job)
        self.assertEqual(b.ledger.dependencies(out['projectId']),
                         [{'coordinate': 'com.example:fakelib:1.0', 'sha256': digest, 'size': len(data)}])
        apk = b.artifact(b.projects()[0]['versions'][0]['apk']['sha256'])
        with zipfile.ZipFile(apk) as z:
            dex = b''.join(z.read(n) for n in z.namelist() if n.startswith('classes') and n.endswith('.dex'))
        self.assertIn(b'Lcom/example/fakelib/Greeter;', dex)
        self.assertIn(b'Hello, ', dex)  # a string only the library's own code holds: its classes are in the app
        self.assertIn(b'Lapp/rose/gen/', dex)

    def test_without_a_resolver_or_a_declaration_it_is_an_honest_dependency_failure(self):
        self.store.add('com.example:fakelib:1.0', self.library())
        for resolver, declare in ((None, True), ('store', False)):
            with self.subTest(resolver=resolver, declare=declare):
                shutil.rmtree(self.root, ignore_errors=True)
                b = self.builder(self.using(declare=declare), resolver)
                out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'x'})
                job = self.run_job(b, out)
                self.assertEqual((job['state'], job['failure']['category']), ('failed', 'dependency'), job)
                self.assertEqual(b.projects()[0]['versions'], [])

    def test_a_revision_keeps_its_lock_and_refuses_changed_bytes(self):
        digest = self.store.add('com.example:fakelib:1.0', self.library())
        b = self.builder(self.using())
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'x'})
        self.assertEqual(self.run_job(b, out)['state'], 'ready')
        revise = lambda key: b.revise('d', out['projectId'], {'schemaVersion': 1, 'clientRequestId': key,
                                                              'request': 'again'})
        job = self.run_job(b, revise('r2'))  # same declaration, same bytes: reproducible
        self.assertEqual(job['state'], 'ready', job)
        self.assertEqual([d['sha256'] for d in b.ledger.dependencies(out['projectId'], 2)], [digest])
        self.store.add('com.example:fakelib:1.0', self.library('Hi, '))  # the store's bytes changed under the app
        job = self.run_job(b, revise('r3'))
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'dependency'), job)
        self.assertIn('last built with', job['failure']['summary'])
        newer = self.store.add('com.example:fakelib:1.1', self.library('Hi, '))
        b.generate = self.using('com.example:fakelib:1.1')  # a new version is a new coordinate: allowed
        job = self.run_job(b, revise('r4'))
        self.assertEqual(job['state'], 'ready', job)
        self.assertEqual(b.ledger.dependencies(out['projectId']),
                         [{'coordinate': 'com.example:fakelib:1.1', 'sha256': newer, 'size': mock.ANY}])
        self.assertEqual([v['versionCode'] for v in b.projects()[0]['versions']], [1, 2, 3])

    def test_a_library_defining_app_classes_is_refused(self):
        b = self.builder(self.using('com.example:evil:1.0'))
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'x'})
        package = b.projects()[0]['packageName']  # assigned at create; this library targets it
        src = Path(tempfile.mkdtemp(dir=self.tmp.name))
        self.store.add('com.example:evil:1.0', jar(src / 'evil.jar', {package.replace('.', '/') + '/R.class': CLASS}))
        job = self.run_job(b, out)
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'dependency'), job)
        self.assertIn('belongs to', job['failure']['summary'])


if __name__ == '__main__':
    unittest.main()
