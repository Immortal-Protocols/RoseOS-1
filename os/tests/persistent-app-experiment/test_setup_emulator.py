"""setup_emulator.py (EXP005-F1) against synthetic SDK trees and a fake runner: nothing is installed or executed."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import setup_emulator as se  # noqa: E402

WORKFLOW = (HERE.parents[2]/'.github/workflows/systemui-persistent-experiment.yml').read_text()

def executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\n')
    path.chmod(0o755)

class Runner:
    def __init__(self, sdk, install_exit=0, creates=True, version_exit=0):
        self.sdk, self.install_exit, self.creates, self.version_exit, self.calls = sdk, install_exit, creates, version_exit, []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if argv[1:] == ['--install', 'emulator']:
            if self.install_exit == 0 and self.creates:
                executable(self.sdk/'emulator/emulator')
            out = 'Accept? (y/N): Skipping following packages as the license is not accepted:\n' if self.install_exit else ''
            return subprocess.CompletedProcess(argv, self.install_exit, out, '')
        if argv[1:] == ['-version']:
            return subprocess.CompletedProcess(argv, self.version_exit, 'Android emulator version 36.1.9.0 (build_id 1)\n', '')
        raise AssertionError(f'unexpected command {argv}')

QEMU_LIBPULSE = ('/usr/local/lib/android/sdk/emulator/qemu/linux-x86_64/qemu-system-x86_64: error while loading '
    'shared libraries: libpulse.so.0: cannot open shared object file: No such file or directory\n')

TOOLS = all(shutil.which(t) for t in ('gcc', 'ldd', 'dpkg-deb'))
LIBDIR = 'usr/lib/x86_64-linux-gnu'

def compile_library(out, soname, needs=(), source='int f(void){return 1;}', rpath=None, libdirs=()):
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = ['gcc', '-shared', '-fPIC', '-o', str(out), '-x', 'c', '-', f'-Wl,-soname,{soname}', '-Wl,--no-as-needed']
    argv += [f'-L{d}' for d in libdirs] + [f'-l:{n}' for n in needs]
    if rpath:
        argv.append(f'-Wl,-rpath,{rpath}')
    subprocess.run(argv, input=source, text=True, check=True, capture_output=True)

def build_deb(mirror, staging, package, version='1.0-1ubuntu1'):
    (staging/'DEBIAN').mkdir(parents=True, exist_ok=True)
    (staging/'DEBIAN/control').write_text(f'Package: {package}\nVersion: {version}\nArchitecture: amd64\n'
        'Maintainer: test <t@t>\nDescription: synthetic\n')
    deb = mirror/f'{package}_{version}_amd64.deb'
    subprocess.run(['dpkg-deb', '--root-owner-group', '-b', str(staging), str(deb)], check=True, capture_output=True)
    return deb

class Archive:
    """A synthetic official archive for the real-loader tests: real .deb packages holding real ELF libraries in the
    real Ubuntu 24.04 libpulse0 layout (public lib flat, private lib nested in pulseaudio/, RUNPATH to the absent host
    path), plus two further transitive packages. Only apt-get download, apt-cache depends and dpkg-query are
    simulated (they need the runner's archive); dpkg-deb, ldd and the emulator stub's loading are real."""
    def __init__(self, tmp):
        self.mirror, build = tmp/'mirror', tmp/'build'
        self.mirror.mkdir()
        ogg, snd, pulse = build/'fakeogg0', build/'fakesndfile1', build/'fakepulse0'
        compile_library(ogg/LIBDIR/'libfakeogg.so.0', 'libfakeogg.so.0')
        compile_library(snd/LIBDIR/'libfakesnd.so.1', 'libfakesnd.so.1', ['libfakeogg.so.0'], libdirs=[ogg/LIBDIR])
        nested = pulse/LIBDIR/'pulseaudio'
        compile_library(nested/'libfakepulsecommon-16.1.so', 'libfakepulsecommon-16.1.so', ['libfakesnd.so.1'],
            libdirs=[snd/LIBDIR, ogg/LIBDIR])
        compile_library(pulse/LIBDIR/'libfakepulse.so.0.24.2', 'libfakepulse.so.0', ['libfakepulsecommon-16.1.so'],
            rpath='/usr/lib/x86_64-linux-gnu/pulseaudio', libdirs=[nested, snd/LIBDIR, ogg/LIBDIR])
        (pulse/LIBDIR/'libfakepulse.so.0').symlink_to('libfakepulse.so.0.24.2')
        self.debs = {name: build_deb(self.mirror, build/name, name) for name in ('fakeogg0', 'fakesndfile1', 'fakepulse0')}
        # The emulator: a real ELF linked against the public library only, as qemu is against libpulse.so.0.
        self.emulator_dir = tmp/'sdk/emulator'
        self.emulator_dir.mkdir(parents=True)
        subprocess.run(['gcc', '-o', str(self.emulator_dir/'emulator'), '-x', 'c', '-', '-Wl,--no-as-needed',
            f'-L{pulse/LIBDIR}', f'-L{nested}', f'-L{snd/LIBDIR}', f'-L{ogg/LIBDIR}', '-l:libfakepulse.so.0'],
            input='#include <stdio.h>\nint f(void);int main(){printf("Android emulator version 36.1.9.0 (build_id 1)\\n");'
            'return f()-1;}', text=True, check=True, capture_output=True)
        self.depends = {'fakepulse0': ['libc6', 'fakesndfile1'], 'fakesndfile1': ['libc6', 'fakeogg0'], 'fakeogg0': ['libc6']}
        self.installed = {'libc6'}
        self.unavailable = set()

    def __call__(self, argv, **kw):
        if argv[:2] == ['apt-get', 'download']:
            assert kw.get('stdin') is subprocess.DEVNULL
            self.downloads.append(argv[2])
            if argv[2] in self.unavailable or argv[2] not in self.debs:
                return subprocess.CompletedProcess(argv, 100, '', f'E: Unable to locate package {argv[2]}\n')
            shutil.copy(self.debs[argv[2]], kw['cwd'])
            return subprocess.CompletedProcess(argv, 0, '', '')
        if argv[:2] == ['apt-cache', 'depends']:
            package = argv[-1]
            lines = [package] + [f'  Depends: {d}' for d in self.depends.get(package, [])]
            return subprocess.CompletedProcess(argv, 0, '\n'.join(lines) + '\n', '')
        if argv[0] == 'dpkg-query':
            return subprocess.CompletedProcess(argv, 0 if argv[-1] in self.installed else 1,
                'installed' if argv[-1] in self.installed else '', '')
        return subprocess.run(argv, **kw)

class SetupTest(unittest.TestCase):
    def setUp(self):
        self.sdk = Path(tempfile.mkdtemp(prefix='sdk-'))
        self.addCleanup(__import__('shutil').rmtree, self.sdk, True)
        (self.sdk/'licenses').mkdir()
        (self.sdk/'licenses/android-sdk-license').write_text('hash\n')
        executable(self.sdk/'cmdline-tools/latest/bin/sdkmanager')

    def test_a_missing_emulator_is_installed_once_with_stdin_closed(self):
        runner = Runner(self.sdk)
        record = se.setup(self.sdk, runner)
        self.assertEqual((record['result'], record['version']), ('installed', 'Android emulator version 36.1.9.0 (build_id 1)'))
        install = [kw for argv, kw in runner.calls if argv[1:] == ['--install', 'emulator']]
        self.assertEqual(len(install), 1)
        self.assertIs(install[0]['stdin'], subprocess.DEVNULL, 'no licence prompt can be answered')
        self.assertEqual(record['licences'], ['android-sdk-license'])

    def test_a_pre_existing_emulator_is_used_without_sdkmanager(self):
        executable(self.sdk/'emulator/emulator')
        runner = Runner(self.sdk)
        record = se.setup(self.sdk, runner)
        self.assertEqual(record['result'], 'pre-existing')
        self.assertFalse(any(argv[1:] == ['--install', 'emulator'] for argv, _ in runner.calls))

    def test_refused_or_incomplete_installs_are_unavailable_not_bypassed(self):
        record = se.setup(self.sdk, Runner(self.sdk, install_exit=1))
        self.assertEqual(record['result'], 'unavailable')
        self.assertIn('no licence answered', record['reason'])
        self.assertIn('license is not accepted', record['sdkmanager']['outputTail'])
        self.assertEqual(se.setup(self.sdk, Runner(self.sdk, creates=False))['result'], 'unavailable')
        self.assertIn('-version exited', se.setup(self.sdk, Runner(self.sdk, version_exit=1))['reason'])
        (self.sdk/'cmdline-tools/latest/bin/sdkmanager').unlink()
        __import__('shutil').rmtree(self.sdk/'emulator', True)
        self.assertIn('is not installed', se.setup(self.sdk, Runner(self.sdk))['reason'])

    def test_main_writes_the_record_and_exits_by_usability(self):
        record = self.sdk/'evidence/emulator-setup.json'
        self.assertEqual(se.main(['--sdk', str(self.sdk), '--record', str(record)], Runner(self.sdk, install_exit=1)), 1)
        self.assertEqual(json.loads(record.read_text())['result'], 'unavailable')
        record.unlink()
        self.assertEqual(se.main(['--sdk', str(self.sdk), '--record', str(record)], Runner(self.sdk)), 0)

    def test_the_missing_library_parser(self):
        self.assertEqual(se.missing_library(QEMU_LIBPULSE), 'libpulse.so.0')
        self.assertEqual(se.missing_library('qemu: error loading libpulse.so.0 (missing)'), 'libpulse.so.0')
        self.assertIsNone(se.missing_library('emulator -version exited 1: PANIC: unknown'))

    def test_the_workflow_installs_before_the_run_without_widening_anything(self):
        setup = WORKFLOW.index('setup_emulator.py --sdk "$ANDROID_HOME"')
        self.assertLess(WORKFLOW.index('experiment.py build'), setup)
        self.assertLess(setup, WORKFLOW.index('experiment.py run'))
        self.assertIn('test_setup_emulator.py -v', WORKFLOW)
        self.assertIn('--library-dir "$RUNNER_TEMP/emulator-libs" --github-env "$GITHUB_ENV"', WORKFLOW)
        code = (HERE/'setup_emulator.py').read_text().split('"""', 2)[2]  # the code, not its docstring
        for banned in ('sudo', '--licenses', 'yes |', "'y'", 'chmod', '/dev/kvm', 'udev', 'setenforce', 'input=',
                "'install', package", 'apt-get install', 'dpkg -i', 'ldconfig'):
            self.assertNotIn(banned, code + WORKFLOW[setup - 400:setup + 200], banned)

@unittest.skipUnless(TOOLS, 'needs gcc, ldd and dpkg-deb to build and load real libraries')
class NestedLibraryTest(unittest.TestCase):
    """EXP006-F3: the run 41122307845 failure (libpulse.so.0 provided flat, its private nested libpulsecommon not
    found) and a further transitive dependency, through the real loader."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='nested-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.archive = Archive(self.tmp)
        self.archive.downloads = []
        self.sdk = self.tmp/'sdk'
        self.libs = self.tmp/'emulator-libs'
        roots = dict(se.ROOT_PACKAGES)
        se.ROOT_PACKAGES.clear()
        se.ROOT_PACKAGES['libfakepulse.so.0'] = 'fakepulse0'
        self.addCleanup(lambda: (se.ROOT_PACKAGES.clear(), se.ROOT_PACKAGES.update(roots)))
        self.base = {'PATH': '/usr/bin:/bin'}

    def setup(self, **kw):
        return se.setup(self.sdk, self.archive, kw.get('libs', self.libs), self.base)

    def version(self, ld_path):
        env = dict(self.base, **({'LD_LIBRARY_PATH': ld_path} if ld_path else {}))
        return subprocess.run([str(self.sdk/'emulator/emulator'), '-version'], capture_output=True, text=True, env=env)

    def test_the_real_loader_fails_first_then_succeeds_with_the_nested_closure_exported(self):
        before = self.version(None)
        self.assertEqual(before.returncode, 127, 'the real loader reproduces the missing public library')
        self.assertIn('libfakepulse.so.0: cannot open shared object file', before.stderr)
        github_env = self.tmp/'github-env'
        code = se.main(['--sdk', str(self.sdk), '--record', str(self.tmp/'setup.json'), '--library-dir', str(self.libs),
            '--github-env', str(github_env)], self.archive, base_env=self.base)
        record = json.loads((self.tmp/'setup.json').read_text())
        self.assertEqual((code, record['result']), (0, 'pre-existing'), record.get('reason'))
        self.assertEqual([(l['package'], l['version'], l['reused']) for l in record['libraries']],
            [('fakepulse0', '1.0-1ubuntu1', False), ('fakesndfile1', '1.0-1ubuntu1', False), ('fakeogg0', '1.0-1ubuntu1', False)])
        self.assertTrue(all(len(l['sha256']) == 64 for l in record['libraries']))
        self.assertNotIn('libc6', self.archive.downloads, 'a host-installed dependency is used, not fetched')
        self.assertEqual(record['searchPaths'], [LIBDIR, LIBDIR + '/pulseaudio'])
        # The exported environment is exactly the one emulator -version succeeded with, and it works.
        self.assertEqual(github_env.read_text(), f'LD_LIBRARY_PATH={record["ldLibraryPath"]}\n')
        self.assertTrue(all(p.startswith(str(self.libs.resolve())) for p in record['ldLibraryPath'].split(':')))
        self.assertEqual(self.version(record['ldLibraryPath']).returncode, 0)
        # The old flat-only path fails exactly as run 41122307845 did: the nested private library is not found.
        flat = self.version(str(self.libs.resolve()/'root'/LIBDIR))
        self.assertEqual(flat.returncode, 127)
        self.assertIn('libfakepulsecommon-16.1.so: cannot open shared object file', flat.stderr)

    def test_an_already_downloaded_package_is_reused_and_each_package_is_fetched_once(self):
        (self.libs/'debs').mkdir(parents=True)
        shutil.copy(self.archive.debs['fakesndfile1'], self.libs/'debs')
        self.archive.depends['fakeogg0'] = ['libc6', 'fakesndfile1']  # a cycle back up the closure
        record = self.setup()
        self.assertEqual(record['result'], 'pre-existing', record.get('reason'))
        self.assertEqual({l['package']: l['reused'] for l in record['libraries']},
            {'fakepulse0': False, 'fakesndfile1': True, 'fakeogg0': False})
        self.assertEqual(sorted(self.archive.downloads), ['fakeogg0', 'fakepulse0'])

    def test_a_missing_package_or_library_stays_unavailable_and_exports_nothing(self):
        self.archive.unavailable.add('fakeogg0')
        github_env = self.tmp/'github-env'
        code = se.main(['--sdk', str(self.sdk), '--record', str(self.tmp/'r.json'), '--library-dir', str(self.libs),
            '--github-env', str(github_env)], self.archive, base_env=self.base)
        record = json.loads((self.tmp/'r.json').read_text())
        self.assertEqual((code, record['result']), (1, 'unavailable'))
        self.assertIn('apt-get download fakeogg0 exited 100', record['reason'])
        self.assertFalse(github_env.exists())
        # A dependency the archive metadata does not name: the real loader check names the unresolved library.
        self.archive.unavailable.clear()
        self.archive.depends['fakesndfile1'] = ['libc6']
        record = self.setup(libs=self.tmp/'libs2')
        self.assertEqual(record['result'], 'unavailable')
        self.assertIn("the loader cannot resolve ['libfakeogg.so.0']", record['reason'])

    def test_closure_bounds_unknown_roots_and_virtual_only_dependencies(self):
        bound = se.MAX_PACKAGES
        se.MAX_PACKAGES = 2
        self.addCleanup(setattr, se, 'MAX_PACKAGES', bound)
        self.assertIn('exceeds 2 packages', self.setup()['reason'])
        se.MAX_PACKAGES = bound
        se.ROOT_PACKAGES.clear()
        self.assertIn('not a known root library; nothing is guessed', self.setup(libs=self.tmp/'l2')['reason'])
        self.assertEqual(self.archive.downloads, [], 'nothing is fetched for an unknown library')

    def test_escaping_or_ambiguous_libraries_are_refused(self):
        # A package whose library links outside the unpack root.
        evil = self.tmp/'build/fakeogg0'
        (evil/LIBDIR/'libfakeogg.so.0').unlink()
        (evil/LIBDIR/'libfakeogg.so.0').symlink_to('/usr/lib/x86_64-linux-gnu/libc.so.6')
        self.archive.debs['fakeogg0'] = build_deb(self.archive.mirror, evil, 'fakeogg0', '1.0-2')
        (self.archive.mirror/'fakeogg0_1.0-1ubuntu1_amd64.deb').unlink()
        self.assertIn('members leaving the unpack root', self.setup()['reason'])
        # Two packages shipping the same library name in different directories.
        dup = self.tmp/'build/fakeogg0b'
        compile_library(dup/LIBDIR/'pulseaudio/libfakesnd.so.1', 'libfakesnd.so.1')
        self.archive.debs['fakeogg0'] = build_deb(self.archive.mirror, dup, 'fakeogg0', '1.0-3')
        self.assertIn('libfakesnd.so.1 is ambiguous', self.setup(libs=self.tmp/'l3')['reason'])

    def test_member_and_link_confinement(self):
        listing = ('-rw-r--r-- root/root 1 2024-04-08 15:47 ./usr/lib/x/pulseaudio/libp.so\n'
            'lrwxrwxrwx root/root 0 2024-04-08 15:47 ./usr/lib/x/libp.so.0 -> libp.so.0.1\n'
            'lrwxrwxrwx root/root 0 2024-04-08 15:47 ./usr/lib/a -> ../../../../etc/passwd\n'
            'lrwxrwxrwx root/root 0 2024-04-08 15:47 ./usr/lib/b -> /etc/passwd\n'
            '-rw-r--r-- root/root 1 2024-04-08 15:47 ./../evil\n')
        self.assertEqual(se.unsafe_members(listing),
            ['./usr/lib/a -> ../../../../etc/passwd', './usr/lib/b -> /etc/passwd', './../evil'])

if __name__ == '__main__':
    unittest.main()
