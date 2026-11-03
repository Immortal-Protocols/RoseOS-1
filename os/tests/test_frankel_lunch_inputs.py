"""#410: os/compile-contracts/frankel-lunch-inputs.json, OS Product's data for what `lunch frankel-cur-user` needs
beyond the Launcher3 contract's three projects. Every value is recomputed offline from committed pinned fixtures; the
git and make behaviour it states is shown with the installed git and GNU make (standing in for kati for $(shell),
ifneq and $(error)) in temporary directories only. Source checks only: no lunch, build, device or host."""
from pathlib import Path
import copy
import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT/'scripts/frankel-lunch-inputs.py'
MANIFEST = ROOT/'os/compile-contracts/frankel-lunch-inputs.json'
FIXTURES = ROOT/'os/tests/fixtures/frankel-lunch-inputs'
PIN = '17df3b79a73e67ad68387b35c7d41c85e1d8103c'
# `git rev-parse <pin>^{tree}` in public clones of each project, recorded independently of the committed listings.
ROOT_TREES = {'vendor/adevtool': 'f0863541755769c12b01f5e1a429bdc6cb704f07',
    'build/make': '8cba4028c35f5cc4e4ebab97b01b6ca93700623f',
    'device/google/laguna-kernels/6.6': '50a4ff771115c0cf74c49595d2730af299b5e201'}
CHECK = 'vendor/google_devices/frankel/adevtool-version-check.mk'


def load():
    spec = importlib.util.spec_from_file_location('frankel_lunch_inputs', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blob(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


class FrankelLunchInputsTest(unittest.TestCase):
    # OS Product's proposed version (#410), not yet agreed with Release; once agreed, any change needs re-agreement.
    PROPOSED_SHA256 = 'fabb9292186800a8d41974491d2d320d790c7a22e9200941464b235b3c4218df'

    def manifest(self):
        return json.loads(MANIFEST.read_text())

    def env(self, root, ceiling=True, **extra):
        """An allowlisted environment: nothing of the caller's git, make or XDG configuration reaches the demos."""
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LC_ALL': 'C', 'HOME': str(root),
            'XDG_CONFIG_HOME': str(root), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, **extra}
        if ceiling:
            env['GIT_CEILING_DIRECTORIES'] = str(root)
        return env

    def git(self, root, *args, cwd=None, check=True):
        return subprocess.run(['git', *args], cwd=cwd or root, env=self.env(root), capture_output=True, text=True,
            check=check)

    def commit(self, root, repo):
        repo.mkdir(parents=True, exist_ok=True)
        self.git(root, 'init', '-q', str(repo))
        (repo/'README').write_text('x\n')
        self.git(root, '-C', str(repo), 'add', 'README')
        self.git(root, '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'x')
        return self.git(root, '-C', str(repo), 'rev-parse', 'HEAD').stdout.strip()

    def lunch(self, root, check_bytes, ceiling=True):
        """Product configuration's view of the version check: frankel.mk's include, evaluated by make at root."""
        (root/CHECK).parent.mkdir(parents=True, exist_ok=True)
        (root/CHECK).write_bytes(check_bytes)
        (root/'Makefile').write_text(f'include {CHECK}\nall:\n\t@echo configured\n')
        return subprocess.run(['make', '-s', '-f', 'Makefile'], cwd=root, env=self.env(root, ceiling),
            capture_output=True, text=True)

    def test_the_manifest_is_the_pinned_version_and_recomputes_offline(self):
        self.assertEqual(hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), self.PROPOSED_SHA256)
        manifest = self.manifest()
        self.assertEqual((manifest['kind'], manifest['schemaVersion']), ('rose-frankel-lunch-inputs', 1))
        self.assertEqual(manifest['lunch'], {'target': 'frankel-cur-user', 'product': 'frankel', 'release': 'cur',
            'variant': 'user'})
        self.assertIn('never copies them into any receipt, bundle report or binding', manifest['provenance'])
        result = subprocess.run(['python3', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_fixture_is_its_pinned_trees_blob(self):
        module = load()
        manifest = self.manifest()
        xml = (ROOT/'os/pinned-manifest.xml').read_text()
        for source in manifest['sources']:
            self.assertIn(f'path="{source["path"]}"', xml)
            self.assertIn(f'revision="{source["revision"]}"', xml)
            listing = module.listing(source['path'])
            self.assertEqual(module.root_tree(source['path']), source['rootTree'])
            raw = gzip.decompress((ROOT/source['listing']['path']).read_bytes())
            self.assertEqual(hashlib.sha256(raw).hexdigest(), source['listing']['sha256'])
            self.assertEqual(len(listing), source['listing']['paths'])
        self.assertEqual({s['path']: s['rootTree'] for s in manifest['sources']}, ROOT_TREES)
        self.assertEqual(manifest['gitRequirement']['rootTree'], ROOT_TREES['vendor/adevtool'])
        for row in manifest['fixtures']:
            tree = module.listing(row['project'])
            self.assertEqual(tree[row['path']][1], row['gitBlob'], row['path'])
            data = (FIXTURES/row['fixture']).read_bytes()
            self.assertEqual(blob(gzip.decompress(data) if row['kind'] == 'gzip' else data), row['gitBlob'], row['path'])
            self.assertEqual(row['kind'] == 'gzip', row['fixture'].endswith('.gz'))
        self.assertTrue((FIXTURES/'vendor/adevtool/LICENSE').read_text().startswith('The MIT License'))

    def test_the_version_check_is_the_pinned_templates_exact_bytes(self):
        check = self.manifest()['versionCheck']
        # Written out by hand from adevtool 17df3b79 src/blobs/build.ts:43-46, independently of the script's interpreter.
        for form in ('error', 'warning'):
            text = (f'ifneq ($(shell git -C vendor/adevtool rev-parse HEAD),{PIN})\n'
                f'  $({form} frankel vendor module is outdated. Run `adevtool generate-all -d frankel` to update it)\n'
                'endif').encode()
            self.assertEqual(check['expected'][form], {'sha256': hashlib.sha256(text).hexdigest(), 'gitBlob': blob(text),
                'bytes': len(text)})
        self.assertEqual(check['expected']['form'], 'error')
        self.assertIn('the error form is necessary, not sufficient', check['formRule'])
        self.assertNotIn('vendor-skels', check['formRule'])
        self.assertIn('generatedVendor.observable must still be compared', check['formRule'])
        self.assertEqual([c['text'] for c in check['formSources']], [
            'await writeVersionCheckFile(config, vendorDirs, flags.noVerify)', 'if (flags.updateSpec) {',
            "return path.join(VENDOR_MODULE_SPECS_DIR, config.device.vendor, `${config.device.name}.yml`)",
            'await writeVersionCheckFile(config, dirs)'])
        self.assertEqual((check['path'], check['includedFrom']), (CHECK, {'path': 'vendor/google_devices/frankel/frankel.mk',
            'line': 4, 'text': f'include {CHECK}'}))
        self.assertEqual(check['selectedBy']['text'], 'PRODUCT_MAKEFILES += $(LOCAL_DIR)/frankel.mk')
        self.assertEqual(check['executes']['argv'], ['git', '-C', 'vendor/adevtool', 'rev-parse', 'HEAD'])
        self.assertIsNone(check['skipSwitch'])
        self.assertFalse(check['inFileTreeSpec'])

    @unittest.skipUnless(shutil.which('make'), 'needs GNU make')
    def test_the_version_check_behaves_as_stated(self):
        module = load()
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)/'copy'
            root.mkdir()
            # A copy without vendor/adevtool/.git, git bounded at the copy root: the error form stops product config.
            (root/'vendor/adevtool').mkdir(parents=True)
            stopped = self.lunch(root, module.version_check_bytes(PIN, False))
            self.assertNotEqual(stopped.returncode, 0)
            self.assertIn('frankel vendor module is outdated', stopped.stderr)
            # The warning form lets the same copy through: that is why only the error form is accepted.
            warned = self.lunch(root, module.version_check_bytes(PIN, True))
            self.assertEqual((warned.returncode, warned.stdout.strip()), (0, 'configured'))
            self.assertIn('frankel vendor module is outdated', warned.stderr)
            # A genuine repository at vendor/adevtool whose HEAD is the embedded revision passes.
            genuine = Path(scratch)/'genuine'
            head = self.commit(genuine, genuine/'vendor/adevtool')
            self.assertEqual(self.lunch(genuine, module.version_check_bytes(head, False)).returncode, 0)
            # Without a ceiling, a parent repository answers for the missing .git with its own (foreign) HEAD.
            parent = Path(scratch)/'parent'
            foreign = self.commit(parent, parent)
            (parent/'vendor/adevtool').mkdir(parents=True)
            seen = subprocess.run(['git', '-C', 'vendor/adevtool', 'rev-parse', 'HEAD'], cwd=parent,
                env=self.env(parent, ceiling=False), capture_output=True, text=True).stdout.strip()
            self.assertEqual(seen, foreign)
            self.assertNotEqual(self.lunch(parent, module.version_check_bytes(PIN, False), ceiling=False).returncode, 0)
            # A fabricated, objectless gitdir prints the pin and passes product config, so a passing lunch proves
            # nothing; the stated acceptance (the commit and its tree must exist) refuses it.
            fake = Path(scratch)/'fake'
            gitdir = fake/'vendor/adevtool/.git'
            for sub in ('objects', 'refs'):
                (gitdir/sub).mkdir(parents=True)
            (gitdir/'HEAD').write_text(PIN + '\n')
            self.assertEqual(self.git(fake, '-C', 'vendor/adevtool', 'rev-parse', 'HEAD').stdout, PIN + '\n')
            self.assertEqual(self.lunch(fake, module.version_check_bytes(PIN, False)).returncode, 0)
            self.assertNotEqual(self.git(fake, '-C', 'vendor/adevtool', 'cat-file', '-t', PIN, check=False).returncode, 0)
            self.assertNotEqual(self.git(fake, '-C', 'vendor/adevtool', 'rev-parse', '--verify', '-q', PIN + '^{tree}',
                check=False).returncode, 0)

    def test_the_git_requirement_admits_only_a_genuine_copy(self):
        git = self.manifest()['gitRequirement']
        self.assertEqual((git['project'], git['revision'], git['path']), ('vendor/adevtool', PIN, 'vendor/adevtool/.git'))
        self.assertEqual({o['class']: o['status'].split(' ')[0] for o in git['options']},
            {'genuine-copy': 'allowed', 'redirect': 'refused', 'fabrication': 'refused', 'relaxation': 'refused'})
        self.assertIn('separate approval with exact paths and bounds', git['options'][0]['status'])
        self.assertEqual(git['genuineSource']['linkText'], '../../.repo/projects/vendor/adevtool.git')
        self.assertIn('Historical context, not an expected value and not current state', git['genuineSource']['basis'])
        self.assertEqual(git['environment']['GIT_CEILING_DIRECTORIES'], 'the copy root')
        self.assertEqual(len(git['acceptance']), 4)
        self.assertIn('rootTree', git['acceptance'][1])
        self.assertIn('nothing redirects outside the copy', git['acceptance'][2])
        self.assertIn('none is a mount point; no regular file in it has more than one hard link', git['acceptance'][2])
        self.assertIn('none of these checks can tell a fabricated store', git['acceptance'][3])
        self.assertEqual(git['genuineSource']['kind'], 'historical')
        self.assertIn('except gitRequirement.genuineSource', self.manifest()['provenance'])
        self.assertIn('--updateSpec', git['options'][3]['means'])

    def test_the_generated_vendor_observables_are_the_reference_spec(self):
        vendor = self.manifest()['generatedVendor']
        spec = gzip.decompress((FIXTURES/'vendor/adevtool/vendor-specs/google_devices/frankel.yml.gz').read_bytes())
        entries = dict(line.split(': ', 1) for line in spec.decode().splitlines())
        self.assertNotIn('adevtool-version-check.mk', entries)
        self.assertEqual(entries['frankel.mk'], 'e12dadf4bb07bebb73b3f66431dfc09f2fb9d6cf177479f243aae6d338873736')
        self.assertEqual([r['path'] for r in vendor['observable']], [f'vendor/google_devices/frankel/{n}' for n in
            ('frankel.mk', 'AndroidProducts.mk', 'BoardConfig.mk', 'Android.mk', 'cmds-for-envsetup.sh')])
        for row in vendor['observable']:
            name = row['path'].rsplit('/', 1)[1]
            self.assertEqual(row['sha256'], entries[name])
            skeleton = FIXTURES/'vendor/adevtool/vendor-skels/google_devices/frankel'/name
            data = skeleton.read_bytes() if skeleton.exists() else gzip.decompress(Path(str(skeleton) + '.gz').read_bytes())
            self.assertEqual(hashlib.sha256(data).hexdigest(), row['sha256'])
        self.assertEqual(vendor['generator'], {'project': 'vendor/adevtool', 'revision': PIN,
            'command': 'generate-all -d frankel'})
        self.assertIn("host's generated bytes, which flags generated them", vendor['notEstablished'])
        self.assertIn('only without --noVerify and without --updateSpec', vendor['verification'])

    def test_build_id_kernel_lists_finder_markers_and_manifest_files(self):
        manifest = self.manifest()
        build_id = manifest['buildId']
        self.assertEqual(build_id['expected'], 'CP3A.261005.005')
        self.assertEqual(build_id['setBy']['text'], 'export BUILD_ID_frankel="CP3A.261005.005"')
        self.assertEqual(build_id['readBy']['text'], 'BUILD_ID := $(BUILD_ID_$(TARGET_PRODUCT))')
        self.assertNotEqual(build_id['defaultWithout']['text'], 'BUILD_ID=CP3A.261005.005')
        kernel = manifest['kernelModuleLists']
        self.assertEqual(kernel['dir'], 'device/google/laguna-kernels/6.6/grapheneos/muzel')
        gs201 = (FIXTURES/'vendor/adevtool/config/mk/google_devices/common/BoardConfig-common-gs201-plus.mk').read_text()
        guards = {}
        for row in kernel['files']:
            name = row['path'].rsplit('/', 1)[1]
            self.assertTrue(row['path'].startswith(kernel['dir'] + '/'))
            self.assertNotEqual(row['gitBlob'], blob(b''))
            self.assertIn('$(KERNEL_MODULE_DIR)/' + name, row['readBy']['text'])
            guards[name] = row['lunchGuard'] is not None
            self.assertEqual(guards[name], f'$(error {name} not found or empty)' in gs201)
            self.assertIn('stops lunch' if guards[name] else 'no guard', row['rule'])
        self.assertEqual(guards, {'vendor_kernel_boot.modules.load': True, 'vendor_dlkm.modules.load': True,
            'system_dlkm.modules.load': False})
        self.assertEqual(manifest['environment']['unset'], ['GIT_*', 'BUILD_ID_frankel', 'TARGET_KERNEL_DIR',
            'USE_STOCK_KERNEL'])
        self.assertIn('before envsetup and lunch', manifest['environment']['when'])
        envsetup = (FIXTURES/'vendor/adevtool/vendor-skels/google_devices/frankel/cmds-for-envsetup.sh').read_text()
        self.assertIn('unset PLATFORM_SECURITY_PATCH_frankel', envsetup, 'the sourced script clears it itself')
        self.assertEqual(kernel['switchedBy']['text'], 'USE_STOCK_KERNEL ?= false')
        module = load()
        tree = module.listing('vendor/adevtool')
        for marker in manifest['finderPrune']['markers']:
            self.assertIn(marker['path'][len('vendor/adevtool/'):], tree)
        self.assertIn('vendor-skels/google_devices/frankel/AndroidProducts.mk', tree)
        expected = sorted(({'kind': c.tag, 'project': p.get('path') or p.get('name'), 'src': c.get('src'),
            'dest': c.get('dest')} for p in ET.parse(ROOT/'os/pinned-manifest.xml').getroot().iter('project')
            for c in p if c.tag in ('linkfile', 'copyfile')), key=lambda r: r['dest'])
        self.assertEqual(manifest['manifestFiles'], expected)
        self.assertIn({'kind': 'linkfile', 'project': 'build/soong', 'src': 'root.bp', 'dest': 'Android.bp'},
            manifest['manifestFiles'])

    def accepted(self, copy):
        """A reference reading of gitRequirement.acceptance items 1-3 for a copy root (item 4 is provenance only)."""
        work, root = copy/'vendor/adevtool', copy.resolve()
        dot = work/'.git'
        inside = lambda p: Path(os.path.realpath(p)) == root or root in Path(os.path.realpath(p)).parents
        if not os.path.lexists(dot):
            return 'no gitdir'
        if dot.is_symlink() and not inside(dot):
            return 'link outside the copy'
        gitdir = Path(os.path.realpath(dot))
        if gitdir.is_file():
            return 'gitfile'
        if not gitdir.is_dir():
            return 'no gitdir'
        for name in ('objects/info/alternates', 'commondir', 'gitdir'):
            if (gitdir/name).exists():
                return name
        device = os.stat(root).st_dev
        for path in [gitdir, *gitdir.rglob('*')]:
            if path.is_symlink() and not inside(path):
                return 'link outside the copy'
            if os.path.ismount(path) or os.stat(path).st_dev != device:
                return 'another filesystem'
            if path.is_file() and not path.is_symlink() and os.stat(path).st_nlink > 1:
                return 'hard link'
        if (gitdir/'HEAD').read_text() != self.head + '\n':
            return 'HEAD'
        for argv in (['--absolute-git-dir'], ['--git-common-dir']):
            out = self.git(copy, '-C', 'vendor/adevtool', 'rev-parse', '--path-format=absolute', *argv).stdout.strip()
            if not inside(out):
                return 'git dir outside the copy'
        if self.git(copy, '-C', 'vendor/adevtool', 'cat-file', '-t', self.head, check=False).stdout.strip() != 'commit':
            return 'commit'
        return None

    def test_the_acceptance_checks_refuse_redirects_that_pass_the_basic_checks(self):
        with tempfile.TemporaryDirectory() as scratch:
            scratch = Path(scratch)
            production = scratch/'production'
            self.head = self.commit(production, production/'vendor/adevtool')
            self.git(production, '-C', str(production/'vendor/adevtool'), 'checkout', '-q', '--detach')
            gitdir = production/'vendor/adevtool/.git'
            # A genuine byte copy of the gitdir into the copy is accepted.
            genuine = scratch/'genuine'
            (genuine/'vendor/adevtool').mkdir(parents=True)
            shutil.copytree(gitdir, genuine/'vendor/adevtool/.git', symlinks=True)
            self.assertIsNone(self.accepted(genuine))
            redirects = {}
            alternates = scratch/'alternates'
            (alternates/'vendor/adevtool/.git/objects/info').mkdir(parents=True)
            (alternates/'vendor/adevtool/.git/refs').mkdir()
            (alternates/'vendor/adevtool/.git/HEAD').write_text(self.head + '\n')
            (alternates/'vendor/adevtool/.git/objects/info/alternates').write_text(str(gitdir/'objects') + '\n')
            redirects['objects/info/alternates'] = alternates
            gitfile = scratch/'gitfile'
            (gitfile/'vendor/adevtool').mkdir(parents=True)
            (gitfile/'vendor/adevtool/.git').write_text(f'gitdir: {gitdir}\n')
            redirects['gitfile'] = gitfile
            linked = scratch/'linked'
            (linked/'vendor/adevtool').mkdir(parents=True)
            os.symlink(gitdir, linked/'vendor/adevtool/.git')
            redirects['link outside the copy'] = linked
            to_gitfile = scratch/'to-gitfile'
            (to_gitfile/'vendor/adevtool').mkdir(parents=True)
            (to_gitfile/'gl').write_text(f'gitdir: {gitdir}\n')
            os.symlink(to_gitfile/'gl', to_gitfile/'vendor/adevtool/.git')
            redirects['gitfile'] = gitfile
            hardlinked = scratch/'hardlinked'
            (hardlinked/'vendor/adevtool').mkdir(parents=True)
            shutil.copytree(gitdir, hardlinked/'vendor/adevtool/.git', symlinks=True, copy_function=os.link)
            redirects['hard link'] = hardlinked
            # A link to a gitfile inside the copy is a gitfile redirect too, and a copy without .git has no gitdir.
            self.assertEqual(self.git(to_gitfile, '-C', 'vendor/adevtool', 'rev-parse', 'HEAD').stdout.strip(), self.head)
            self.assertEqual(self.accepted(to_gitfile), 'gitfile')
            missing = scratch/'missing'
            (missing/'vendor/adevtool').mkdir(parents=True)
            self.assertEqual(self.accepted(missing), 'no gitdir')
            for reason, copy in redirects.items():
                # Every redirect passes the basic checks (acceptance items 1 and 2)...
                self.assertEqual(self.git(copy, '-C', 'vendor/adevtool', 'rev-parse', 'HEAD').stdout.strip(), self.head)
                self.assertEqual(self.git(copy, '-C', 'vendor/adevtool', 'cat-file', '-t', self.head).stdout.strip(),
                    'commit')
                # ...and the structural checks (item 3) refuse it.
                self.assertEqual(self.accepted(copy), reason)

    @unittest.skipUnless(shutil.which('make'), 'needs GNU make')
    def test_an_inherited_build_id_hides_a_missing_envsetup_script(self):
        """Why environment.unset lists BUILD_ID_frankel: version_util.mk takes BUILD_ID from it, so an inherited value
        passes frankel.mk's check although no cmds-for-envsetup.sh was sourced."""
        frankel = gzip.decompress((FIXTURES/'vendor/adevtool/vendor-skels/google_devices/frankel/frankel.mk.gz')
            .read_bytes()).decode().split('\n')
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root/'Makefile').write_text('TARGET_PRODUCT := frankel\nBUILD_ID := $(BUILD_ID_$(TARGET_PRODUCT))\n'
                'ifeq ($(BUILD_ID),)\nBUILD_ID := CP2A.260605.016\nendif\n' + '\n'.join(frankel[5:8]) +
                '\nall:\n\t@echo configured\n')
            run = lambda **extra: subprocess.run(['make', '-s'], cwd=root, env=self.env(root, **extra),
                capture_output=True, text=True)
            self.assertNotEqual(run().returncode, 0, 'no script, clean environment: lunch stops')
            self.assertEqual(run(BUILD_ID_frankel='CP3A.261005.005').returncode, 0, 'an inherited value hides it')

    def test_a_tampered_manifest_or_fixture_is_refused(self):
        module = load()
        manifest = self.manifest()
        for edit in (lambda m: m['versionCheck']['expected'].__setitem__('form', 'warning'),
                lambda m: m['gitRequirement']['options'][1].__setitem__('status', 'allowed'),
                lambda m: m['generatedVendor']['observable'][0].__setitem__('sha256', '0' * 64),
                lambda m: m['buildId'].__setitem__('expected', 'X'),
                lambda m: m.__setitem__('sources', None)):
            changed = copy.deepcopy(manifest)
            edit(changed)
            self.assertTrue(module.check(changed))
        def tampered(change):
            with tempfile.TemporaryDirectory() as scratch:
                fixtures = Path(scratch)/'fixtures'
                shutil.copytree(FIXTURES, fixtures)
                change(fixtures)
                module = load()
                module.FIXTURES, module.TREES, module.PROVENANCE = fixtures, fixtures/'trees', fixtures/'PROVENANCE.json'
                return module.check(manifest)
        skel = 'vendor/adevtool/vendor-skels/google_devices/frankel'

        def edit(path, change):
            return lambda root: (root/path).write_bytes(change((root/path).read_bytes()))
        cases = {
            'bytes differ from the pinned blob': edit(f'{skel}/cmds-for-envsetup.sh', lambda b: b.replace(b'CP3A', b'CP9A')),
            'the compressed bytes are not the recorded ones': edit(f'{skel}/frankel.mk.gz', lambda b: b[:4] + b'\x01' + b[5:]),
            'fixtures: ': edit('trees/vendor__adevtool.ls-tree.txt.gz', lambda b: b[:len(b) // 2]),
            'holds files other than its listed fixtures': lambda root: (root/'vendor/adevtool/extra.mk').write_text('x'),
            'canonical': edit('PROVENANCE.json', lambda b: b + b'\n'),
        }
        for expected, change in cases.items():
            errors = tampered(change)
            self.assertTrue(any(expected in e for e in errors), (expected, errors))
        corrupt = edit('vendor/adevtool/vendor-specs/google_devices/frankel.yml.gz', lambda b: b[:40] + b'\xff' * 20 + b[60:])
        self.assertTrue(tampered(corrupt), 'corrupt deflate data is reported, not raised')


if __name__ == '__main__':
    unittest.main()
