"""Provisioning the pinned Gradle stack (rose.app_builder.gradle_prepare; docs/APP-BUILDER.md §2.4): archives are
checked against their pins before they are opened and unpacked as untrusted data, SDK packages get the package.xml
AGP needs, the catalog project covers every pinned plugin and library, and a stack is made atomically, kept when
current and never put over a folder it did not make. Synthetic archives and a fake Gradle step only; offline.

With ROSE_GRADLE_STACK pointing at a provisioned stack (CI's gradle job), it is also checked to be the stack these
pins make for this host, its cache digest included."""
import hashlib
import io
import json
import os
import shutil
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

from rose.app_builder import gradle_prepare as gp
from rose.app_builder import gradle_stack
from rose.app_builder.gradle_stack import Archive


def make_zip(path: Path, entries: dict, modes: dict | None = None, links: dict | None = None) -> Path:
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = ((modes or {}).get(name, 0o100644)) << 16
            z.writestr(info, data)
        for name, target in (links or {}).items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, target)
    return path


def make_tar(path: Path, entries: dict, links: dict | None = None) -> Path:
    with tarfile.open(path, 'w:gz') as t:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755 if name.endswith('/java') else 0o644
            t.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            t.addfile(info)
    return path


def pinned(path: Path, url: str = 'https://example.invalid/x') -> Archive:
    return Archive(path.name, url, hashlib.sha256(path.read_bytes()).hexdigest())


def synthetic_archives(folder: Path) -> dict:
    """Small archives shaped like the pinned ones, pinned by their own digests."""
    feature = gradle_stack.JDK_FEATURE
    jdk = make_tar(folder / 'jdk.tar.gz', {
        'jdk-x/release': f'JAVA_VERSION="{feature}.0.13"\n'.encode(), 'jdk-x/bin/java': b'#!/bin/sh\n',
        'jdk-x/legal/java.base/LICENSE': b'l'}, links={'jdk-x/legal/jdk.jlink/LICENSE': '../java.base/LICENSE'})
    gradle = make_zip(folder / f'gradle-{gradle_stack.GRADLE}-bin.zip', {
        f'gradle-{gradle_stack.GRADLE}/bin/gradle': b'#!/bin/sh\n', f'gradle-{gradle_stack.GRADLE}/lib/a.jar': b'j'},
        modes={f'gradle-{gradle_stack.GRADLE}/bin/gradle': 0o100755})
    out = {'jdk': pinned(jdk), 'gradle': pinned(gradle)}
    for n, (path, _target, _per_os) in enumerate(gradle_stack.SDK_PACKAGES):
        source = ''.join(f'{k}={v}\n' for k, v in gp.SDK_DETAILS[path]['source'].items())
        out[path] = pinned(make_zip(folder / f'sdk{n}.zip', {f'top{n}/source.properties': source.encode(),
                                                            f'top{n}/aapt2': b'\x7fELF'},
                                    modes={f'top{n}/aapt2': 0o100755}))
    return out


class Archives(unittest.TestCase):
    def test_this_hosts_pins_and_an_unpinned_host_is_refused(self):
        got = gp.archives('linux', 'x86_64')
        self.assertEqual(got['jdk'], gradle_stack.JDKS[('linux', 'x86_64')])
        self.assertEqual(got['gradle'], gradle_stack.GRADLE_DISTRIBUTION)
        self.assertEqual(set(got) - {'jdk', 'gradle'}, {p for p, _t, _a in gradle_stack.SDK_PACKAGES})
        with self.assertRaisesRegex(gp.Refused, 'no JDK'):
            gp.archives('linux', 'riscv64')
        with self.assertRaisesRegex(gp.Refused, 'measure'):  # build-tools has no macOS SHA-256 pin yet
            gp.archives('darwin', 'arm64')

    def test_every_sdk_package_has_details_and_every_unpinned_one_a_manifest_record(self):
        for path, target, per_os in gradle_stack.SDK_PACKAGES:
            self.assertIn(path, gp.SDK_DETAILS)
            self.assertEqual(target, path.replace(';', '/', 1))
            if per_os.get('macosx') is None:
                self.assertIn(path, gp.MEASURE)

    def test_fetch_checks_the_pin_and_never_keeps_a_wrong_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            downloads, data = Path(tmp) / 'dl', b'archive bytes'
            archive = Archive('a.zip', 'https://example.invalid/a.zip', hashlib.sha256(data).hexdigest())
            with mock.patch.object(gp, '_download', side_effect=lambda url, t: Path(t).write_bytes(b'other')):
                with self.assertRaisesRegex(gp.Refused, 'not the pinned archive'):
                    gp.fetch(archive, downloads, log=io.StringIO())
            self.assertEqual(list(downloads.iterdir()), [])
            with mock.patch.object(gp, '_download', side_effect=lambda url, t: Path(t).write_bytes(data)) as dl:
                self.assertEqual(gp.fetch(archive, downloads, log=io.StringIO()).read_bytes(), data)
                gp.fetch(archive, downloads, log=io.StringIO())  # present: only checked
            self.assertEqual(dl.call_count, 1)
            (downloads / 'a.zip').write_bytes(b'tampered')
            with self.assertRaisesRegex(gp.Refused, 'delete it'):
                gp.fetch(archive, downloads, log=io.StringIO())
            self.assertEqual((downloads / 'a.zip').read_bytes(), b'tampered')  # the operator's file stays


class Unpack(unittest.TestCase):
    def test_zip_keeps_execute_bits_but_no_set_id_and_returns_the_one_top_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = make_zip(Path(tmp) / 'a.zip', {'top/bin/tool': b'x', 'top/doc.txt': b'y'},
                         modes={'top/bin/tool': 0o104755, 'top/doc.txt': 0o100666})
            top = gp.unpack(z, Path(tmp) / 'u')
            self.assertEqual(top, Path(tmp) / 'u' / 'top')
            self.assertEqual(stat.S_IMODE((top / 'bin/tool').stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((top / 'doc.txt').stat().st_mode), 0o644)

    def test_unsafe_zip_entries_are_refused_before_anything_is_written(self):
        for entries, links in (({'top/../../evil': b'x'}, None), ({'/etc/evil': b'x'}, None),
                               ({'top\\evil': b'x'}, None), ({'top/a': b'x'}, {'top/link': '/etc/passwd'})):
            with self.subTest(entries=entries, links=links), tempfile.TemporaryDirectory() as tmp:
                z = make_zip(Path(tmp) / 'a.zip', entries, links=links)
                with self.assertRaises(gp.Refused):
                    gp.unpack(z, Path(tmp) / 'u')
                self.assertEqual(list((Path(tmp) / 'u').iterdir()), [])
                self.assertFalse((Path(tmp).parent / 'evil').exists())

    def test_archives_need_exactly_one_top_folder_and_a_fresh_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = make_zip(Path(tmp) / 'a.zip', {'one/a': b'x', 'two/b': b'y'})
            with self.assertRaisesRegex(gp.Refused, 'one top-level'):
                gp.unpack(z, Path(tmp) / 'u')
            with self.assertRaises(FileExistsError):
                gp.unpack(z, Path(tmp) / 'u')
            with self.assertRaisesRegex(gp.Refused, 'not an archive'):
                gp.unpack(Path(tmp) / 'a.rar', Path(tmp) / 'v')

    def test_tar_keeps_links_inside_the_tree_and_refuses_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_tar(Path(tmp) / 'a.tar.gz', {'jdk/legal/java.base/LICENSE': b'l'},
                         links={'jdk/legal/x/LICENSE': '../java.base/LICENSE'})
            top = gp.unpack(t, Path(tmp) / 'u')
            self.assertTrue((top / 'legal/x/LICENSE').is_symlink())
            self.assertEqual((top / 'legal/x/LICENSE').read_bytes(), b'l')
            for links in ({'jdk/l': '/etc/passwd'}, {'jdk/l': '../../outside'}, {'jdk/a/l': '../../../x'}):
                with self.subTest(links=links):
                    bad = make_tar(Path(tmp) / f'b{len(os.listdir(tmp))}.tar.gz', {'jdk/a/f': b'x'}, links=links)
                    with self.assertRaisesRegex(gp.Refused, 'link out'):
                        gp.unpack(bad, Path(tmp) / f'v{len(os.listdir(tmp))}')
            evil = make_tar(Path(tmp) / 'e.tar.gz', {'jdk/../../evil': b'x'})
            with self.assertRaisesRegex(gp.Refused, 'leaves'):
                gp.unpack(evil, Path(tmp) / 'w')
            with tarfile.open(Path(tmp) / 'f.tar.gz', 'w:gz') as tar:
                fifo = tarfile.TarInfo('jdk/fifo')
                fifo.type = tarfile.FIFOTYPE
                tar.addfile(fifo)
            with self.assertRaisesRegex(gp.Refused, 'special'):
                gp.unpack(Path(tmp) / 'f.tar.gz', Path(tmp) / 'x')


class SdkPackages(unittest.TestCase):
    def test_package_xml_is_what_sdkmanager_writes_for_each_pinned_package(self):
        ns = {'xsi': 'http://www.w3.org/2001/XMLSchema-instance'}
        for path, _target, _a in gradle_stack.SDK_PACKAGES:
            with self.subTest(path=path):
                root = ET.fromstring(gp.package_xml(path).split('\n', 1)[1])
                self.assertEqual(root.tag, '{http://schemas.android.com/repository/android/common/02}repository')
                package = root.find('localPackage')
                self.assertEqual(package.get('path'), path)
                self.assertEqual(package.get('obsolete'), 'false')
                kind = package.find('type-details').get(f'{{{ns["xsi"]}}}type')
                revision = [int(package.find(f'revision/{k}').text) for k in ('major', 'minor', 'micro')
                            if package.find(f'revision/{k}') is not None]
                if path.startswith('platforms;'):
                    self.assertEqual(kind, 'sdk:platformDetailsType')
                    self.assertEqual(package.find('type-details/api-level').text,
                                     gradle_stack.PLATFORM.split('-', 1)[1])
                else:
                    self.assertEqual(kind, 'generic:genericDetailsType')
                    self.assertEqual('.'.join(map(str, revision)), gradle_stack.BUILD_TOOLS)
        with self.assertRaisesRegex(gp.Refused, 'no package details'):
            gp.package_xml('platforms;android-99')

    def test_a_package_is_installed_only_when_its_source_properties_agree(self):
        path, target, _a = gradle_stack.SDK_PACKAGES[0]
        with tempfile.TemporaryDirectory() as tmp:
            top = Path(tmp) / 'top'
            top.mkdir()
            (top / 'source.properties').write_text('Pkg.Revision=1\n')
            with self.assertRaisesRegex(gp.Refused, 'Pkg.Revision'):
                gp.install_sdk_package(path, top, Path(tmp) / 'sdk', target)
            (top / 'source.properties').write_text(
                ''.join(f'{k}={v}\n' for k, v in gp.SDK_DETAILS[path]['source'].items()))
            installed = gp.install_sdk_package(path, top, Path(tmp) / 'sdk', target)
            self.assertEqual(installed, Path(tmp) / 'sdk' / target)
            self.assertEqual((installed / 'package.xml').read_text(), gp.package_xml(path))


class Catalog(unittest.TestCase):
    def test_the_catalog_applies_every_plugin_and_depends_on_every_library(self):
        files = gp.catalog()
        app = files['app/build.gradle.kts']
        root = files['build.gradle.kts']
        for plugin, version in gradle_stack.PLUGINS.items():
            self.assertIn(f'id("{plugin}") version "{version}" apply false', root)
            self.assertIn(f'id("{plugin}")\n', files['lib/build.gradle.kts'] if plugin == 'com.android.library' else app)
        # A project's own library module, and an application that uses it, are built too.
        self.assertIn('implementation(project(":lib"))', files['multi/build.gradle.kts'])
        self.assertIn(':multi:assembleRelease', gp.TASKS)
        bom = 'androidx.compose:compose-bom'
        self.assertIn(f'implementation(platform("{bom}:{gradle_stack.LIBRARIES[bom]}"))', app)
        for coordinate, version in gradle_stack.LIBRARIES.items():
            if coordinate != bom:
                self.assertIn(f'implementation("{coordinate}")' if version is None
                              else f'implementation("{coordinate}:{version}")', app)
        self.assertIn('isMinifyEnabled = true', app)
        self.assertNotIn('kotlin.android', ''.join(files.values()))  # AGP 9 compiles Kotlin itself

    def test_one_module_per_library_alone_and_next_to_the_skeleton(self):
        files, libs = gp.catalog(), gradle_stack.LIBRARIES
        each = [c for c in libs if not c.endswith(':compose-bom')]
        settings = files['settings.gradle.kts']
        for n, coordinate in enumerate(each):
            alone, beside = files[f'x{n}/build.gradle.kts'], files[f's{n}/build.gradle.kts']
            self.assertIn(f'include(":x{n}")', settings)
            self.assertIn(f'include(":s{n}")', settings)
            for text in (alone, beside):
                self.assertIn(f'"{coordinate}' + ('")' if libs[coordinate] is None else f':{libs[coordinate]}")'),
                              text)
                self.assertIn('tasks.register("roseResolve")', text)
                self.assertEqual('plugin.serialization' in text, coordinate == gp.SERIALIZATION)
            self.assertEqual(alone.count('implementation('), 2 if libs[coordinate] is None else 1)
            for skeleton_library in gp.BASE[1:]:
                self.assertIn(f'"{skeleton_library}', beside)

    def test_the_catalog_declares_the_skeletons_repositories(self):
        """The cache answers per repository: the catalog must declare exactly what a new project declares."""
        from rose.app_builder import gradle
        skeleton = gradle.skeleton('app.rose.gen.p1', 'App')
        catalog = gp.catalog()

        def blocks(text):
            return [line for line in text.splitlines() if 'repositories' in line or 'RepositoriesMode' in line]

        self.assertEqual(blocks(skeleton['settings.gradle.kts']), blocks(catalog['settings.gradle.kts']))
        app = skeleton[f'{gradle_stack.APP_MODULE}/build.gradle.kts']
        for library in gp.BASE[1:]:
            self.assertIn(f'"{library}', app)
        self.assertIn(f'compose-bom:{gradle_stack.COMPOSE_BOM}', app)

    def test_the_seed_uses_the_builders_gradle_settings(self):
        from rose.app_builder import gradle
        jdk, home, tmp = Path('/s/jdk'), Path('/w/home'), Path('/w/tmp')
        ours = dict(line.split('=', 1) for line in gp._user_properties(jdk, home, tmp).splitlines())
        builder = dict(line.split('=', 1) for line in gradle._properties(jdk, home, tmp).splitlines())
        self.assertEqual({k: v for k, v in ours.items() if k != 'org.gradle.jvmargs'},
                         {k: v for k, v in builder.items() if k != 'org.gradle.jvmargs'})


class CacheAndDigest(unittest.TestCase):
    def test_copy_drops_locks_and_gc_state_and_refuses_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'modules-2'
            (src / 'files-2.1/g/a/1/sha').mkdir(parents=True)
            (src / 'files-2.1/g/a/1/sha/a.jar').write_bytes(b'j')
            (src / 'modules-2.lock').write_bytes(b'')
            (src / 'gc.properties').write_bytes(b'')
            (src / 'metadata-2.107').mkdir()
            (src / 'metadata-2.107/x.lock').write_bytes(b'')
            self.assertEqual(gp.copy_cache(src, Path(tmp) / 'c' / 'modules-2'), 1)
            copied = sorted(p.relative_to(Path(tmp) / 'c').as_posix() for p in (Path(tmp) / 'c').rglob('*'))
            self.assertEqual(copied, ['modules-2', 'modules-2/files-2.1', 'modules-2/files-2.1/g',
                                      'modules-2/files-2.1/g/a', 'modules-2/files-2.1/g/a/1',
                                      'modules-2/files-2.1/g/a/1/sha', 'modules-2/files-2.1/g/a/1/sha/a.jar',
                                      'modules-2/metadata-2.107'])
            (src / 'files-2.1/link').symlink_to(src / 'gc.properties')
            with self.assertRaisesRegex(gp.Refused, 'link'):
                gp.copy_cache(src, Path(tmp) / 'd' / 'modules-2')
            with self.assertRaisesRegex(gp.Refused, 'no dependency cache'):
                gp.copy_cache(Path(tmp) / 'missing', Path(tmp) / 'e')

    def test_digest_covers_names_contents_and_modes_and_refuses_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'cache'
            (root / 'a').mkdir(parents=True)
            (root / 'a/f').write_bytes(b'1')
            first = gp.tree_digest(root)
            self.assertEqual(first, gp.tree_digest(root))
            (root / 'a/f').write_bytes(b'2')
            second = gp.tree_digest(root)
            self.assertNotEqual(first, second)
            os.chmod(root / 'a/f', 0o444)
            self.assertNotEqual(second, gp.tree_digest(root))
            (root / 'a/g').mkdir()
            self.assertNotEqual(second, gp.tree_digest(root))
            (root / 'a/dirlink').symlink_to(root / 'a/g')
            with self.assertRaisesRegex(gp.Refused, 'link'):
                gp.tree_digest(root)

    def test_config_is_the_hosts_gradle_object(self):
        from rose.app_builder import host
        with tempfile.TemporaryDirectory() as tmp:
            got = gp.config(Path(tmp), 'linux')
            real = os.path.realpath(tmp)
            self.assertEqual(got, {'jdk': f'{real}/jdk', 'distribution': f'{real}/gradle', 'sdk': f'{real}/sdk',
                                   'dependencyCache': f'{real}/cache', 'isolation': 'bubblewrap'})
            self.assertEqual(gp.config(Path(tmp), 'darwin')['isolation'], 'seatbelt')
            self.assertLessEqual(set(got), host.GRADLE_KEYS)


class Stack(unittest.TestCase):
    """stack() end to end with synthetic archives; the Gradle runs are replaced by a fake that fills a cache."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: gp._remove(self.tmp))
        (self.tmp / 'archives').mkdir()
        self.pins = synthetic_archives(self.tmp / 'archives')
        self.downloads = self.tmp / 'dl'
        self.downloads.mkdir()
        for archive in self.pins.values():
            shutil.copy(self.tmp / 'archives' / archive.name, self.downloads / archive.name)
        self.runs = []
        patches = [mock.patch.object(gp, 'archives', lambda s, m: self.pins),
                   mock.patch.object(gp, 'host', lambda: ('linux', 'x86_64')),
                   mock.patch.object(gp, 'run_catalog', self.fake_run),
                   mock.patch.object(gp, '_download', side_effect=AssertionError('no network'))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.out = self.tmp / 'stack'

    def fake_run(self, stage, work, *, online, log=None):
        self.runs.append(online)
        for part in ('jdk/bin/java', 'gradle/bin/gradle', 'sdk/platforms', 'sdk/build-tools'):
            self.assertTrue((stage / part).exists(), part)
        user = work / 'gradle-home'
        if online:
            jar = user / 'caches/modules-2/files-2.1/androidx.core/core/1/abc/core-1.aar'
            jar.parent.mkdir(parents=True)
            jar.write_bytes(b'aar')
            (user / 'caches/modules-2/modules-2.lock').write_bytes(b'')
        else:
            self.assertTrue((stage / 'stack.json').is_file())
            self.assertEqual(stat.S_IMODE((stage / 'cache' / 'modules-2').stat().st_mode), 0o555)
        return user

    def test_a_stack_is_made_proven_offline_kept_when_current_and_read_only(self):
        result = gp.stack(self.out, self.downloads, log=io.StringIO())
        self.assertTrue(result['prepared'])
        self.assertEqual(self.runs, [True, False])  # seeded online, then proven offline
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ['cache', 'gradle', 'jdk', 'sdk', 'stack.json'])
        data = json.loads((self.out / 'stack.json').read_text())
        self.assertEqual(data, result['stack'])
        self.assertEqual({k: v for k, v in data.items() if k != 'cacheSha256'}, gp.record('linux', 'x86_64'))
        self.assertEqual(data['cacheSha256'], gp.tree_digest(self.out / 'cache'))
        self.assertEqual(result['gradle'], gp.config(self.out, 'linux'))
        for path, target, _a in gradle_stack.SDK_PACKAGES:
            self.assertEqual((self.out / 'sdk' / target / 'package.xml').read_text(), gp.package_xml(path))
        self.assertTrue((self.out / 'jdk/legal/jdk.jlink/LICENSE').is_symlink())
        self.assertFalse((self.out / 'cache/modules-2/modules-2.lock').exists())
        for path in [self.out, *self.out.rglob('*')]:
            if path.is_symlink():
                continue
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode & 0o222, 0, path)
            self.assertEqual(mode & 0o444, 0o444, path)
            if path.is_dir():
                self.assertEqual(mode & 0o111, 0o111, path)
        self.assertEqual(stat.S_IMODE((self.out / 'gradle/bin/gradle').stat().st_mode), 0o555)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ['archives', 'dl', 'stack'])  # no leftovers
        again = gp.stack(self.out, self.downloads, log=io.StringIO())
        self.assertFalse(again['prepared'])
        self.assertEqual(self.runs, [True, False])
        self.assertEqual(again['stack'], data)

    def test_a_failure_leaves_the_target_as_it_was(self):
        def fail(stage, work, *, online, log=None):
            raise gp.Refused('the catalog build failed')
        with mock.patch.object(gp, 'run_catalog', fail):
            with self.assertRaisesRegex(gp.Refused, 'catalog build failed'):
                gp.stack(self.out, self.downloads, log=io.StringIO())
        self.assertFalse(self.out.exists())
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ['archives', 'dl'])

    def test_a_stale_or_damaged_stack_is_replaced_and_a_foreign_folder_never(self):
        gp.stack(self.out, self.downloads, log=io.StringIO())
        os.chmod(self.out / 'cache/modules-2', 0o755)
        (self.out / 'cache/modules-2/planted').write_bytes(b'x')
        result = gp.stack(self.out, self.downloads, log=io.StringIO())
        self.assertTrue(result['prepared'])
        self.assertFalse((self.out / 'cache/modules-2/planted').exists())
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ['archives', 'dl', 'stack'])
        foreign = self.tmp / 'foreign'
        foreign.mkdir()
        (foreign / 'notes.txt').write_text('mine')
        with self.assertRaisesRegex(gp.Refused, 'not a Gradle stack'):
            gp.stack(foreign, self.downloads, log=io.StringIO())
        self.assertEqual((foreign / 'notes.txt').read_text(), 'mine')
        empty = self.tmp / 'empty'
        empty.mkdir()
        self.assertTrue(gp.stack(empty, self.downloads, log=io.StringIO())['prepared'])

    def test_verify_refuses_other_pins(self):
        gp.stack(self.out, self.downloads, log=io.StringIO())
        with mock.patch.object(gradle_stack, 'COMPOSE_BOM', '2099.01.00'):
            with self.assertRaisesRegex(gp.Refused, 'other pins'):
                gp.verify(self.out, 'linux', 'x86_64')
        with self.assertRaisesRegex(gp.Refused, 'stack.json'):
            gp.verify(self.tmp / 'dl', 'linux', 'x86_64')

    def test_main_prints_one_json_line(self):
        out = io.StringIO()
        with mock.patch('sys.stderr', io.StringIO()):
            self.assertEqual(gp.main(['stack', '--out', str(self.out), '--downloads', str(self.downloads)], out), 0)
        line = json.loads(out.getvalue())
        self.assertEqual(line['status'], 'ok')
        self.assertEqual(line['gradle']['dependencyCache'], str(Path(os.path.realpath(self.out)) / 'cache'))
        out = io.StringIO()
        with mock.patch('sys.stderr', io.StringIO()):
            self.assertEqual(gp.main(['stack', '--out', str(self.tmp / 'archives')], out), 1)
        self.assertEqual(json.loads(out.getvalue())['status'], 'refused')


class Measure(unittest.TestCase):
    def test_it_checks_googles_record_and_prints_the_sha256(self):
        unpinned = [p for p, _t, a in gradle_stack.SDK_PACKAGES if a.get('macosx') is None]
        with tempfile.TemporaryDirectory() as tmp:
            data = b'mac build tools'
            record = {p: (gp.MEASURE[p][0], len(data), hashlib.sha1(data).hexdigest()) for p in unpinned}
            with mock.patch.dict(gp.MEASURE, record), \
                    mock.patch.object(gp, '_download', side_effect=lambda u, t: Path(t).write_bytes(data)):
                got = gp.measure(Path(tmp), log=io.StringIO())['measured']
            self.assertEqual(set(got), set(unpinned))
            for path in unpinned:
                self.assertEqual(got[path]['sha256'], hashlib.sha256(data).hexdigest())
            bad = {p: (gp.MEASURE[p][0], len(data), '0' * 40) for p in unpinned}
            with mock.patch.dict(gp.MEASURE, bad):
                if unpinned:
                    with self.assertRaisesRegex(gp.Refused, 'SHA-1'):
                        gp.measure(Path(tmp), log=io.StringIO())


@unittest.skipUnless(os.environ.get('ROSE_GRADLE_STACK'), 'ROSE_GRADLE_STACK is not set')
class ProvisionedStack(unittest.TestCase):
    """The stack setup-android-gradle.sh made is the one these pins make for this host, digest included."""

    def test_the_provisioned_stack_is_current(self):
        out = Path(os.environ['ROSE_GRADLE_STACK'])
        data = gp.verify(out)
        self.assertEqual(data['gradle'], gradle_stack.GRADLE)
        for path, target, _a in gradle_stack.SDK_PACKAGES:
            self.assertEqual((out / 'sdk' / target / 'package.xml').read_text(), gp.package_xml(path))
        self.assertTrue((out / 'jdk/bin/java').is_file())
        self.assertTrue((out / 'gradle/bin/gradle').is_file())


if __name__ == '__main__':
    unittest.main()
