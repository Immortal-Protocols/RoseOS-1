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
# `git rev-parse 17df3b79^{tree}` in a public clone of GrapheneOS/adevtool, recorded independently of the listing.
ADEVTOOL_ROOT_TREE = 'f0863541755769c12b01f5e1a429bdc6cb704f07'
CHECK = 'vendor/google_devices/frankel/adevtool-version-check.mk'


def load():
    spec = importlib.util.spec_from_file_location('frankel_lunch_inputs', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blob(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


class FrankelLunchInputsTest(unittest.TestCase):
    # Pinned once agreed with Release (#410); a change needs re-agreement.
    AGREED_SHA256 = '5026361d7e7f67032aef23490309f7a0d36f385f7dfeb2ed6802baac51373061'

    def manifest(self):
        return json.loads(MANIFEST.read_text())

    def env(self, root, ceiling=True):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('GIT_', 'MAKE'))}
        env.update(HOME=str(root), GIT_CONFIG_NOSYSTEM='1', LC_ALL='C')
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
        self.assertEqual(hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), self.AGREED_SHA256)
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
        self.assertEqual(manifest['sources'][0]['rootTree'], ADEVTOOL_ROOT_TREE)
        self.assertEqual(manifest['gitRequirement']['rootTree'], ADEVTOOL_ROOT_TREE)
        for row in manifest['fixtures']:
            tree = module.listing(row['project'])
            self.assertEqual(tree[row['path']][1], row['gitBlob'], row['path'])
            if row['kind'] == 'full':
                self.assertEqual(blob((FIXTURES/row['fixture']).read_bytes()), row['gitBlob'])
            else:
                self.assertEqual(json.loads((FIXTURES/row['fixture']).read_text())['upstreamGitBlob'], row['gitBlob'])
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
        self.assertIn('a warning-form module is unverified vendor provenance and is refused', check['formRule'])
        self.assertEqual(check['formSource']['text'], 'await writeVersionCheckFile(config, vendorDirs, flags.noVerify)')
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
        self.assertIn('historical evidence, not current state', git['genuineSource']['basis'])
        self.assertEqual(git['environment']['GIT_CEILING_DIRECTORIES'], 'the copy root')
        self.assertEqual(len(git['acceptance']), 3)
        self.assertIn('do not prove a complete one', git['acceptance'][2])
        self.assertIn('rootTree', git['acceptance'][1])

    def test_the_generated_vendor_observables_are_the_reference_spec(self):
        vendor = self.manifest()['generatedVendor']
        spec = json.loads((FIXTURES/'vendor/adevtool/vendor-specs/google_devices/frankel.yml.excerpt.json').read_text())
        entries = dict(line.split(': ', 1) for line in spec['lines'].values())
        self.assertEqual(spec['absent'], ['adevtool-version-check.mk'])
        self.assertEqual([r['path'] for r in vendor['observable']], [f'vendor/google_devices/frankel/{n}' for n in
            ('frankel.mk', 'AndroidProducts.mk', 'BoardConfig.mk', 'Android.mk', 'cmds-for-envsetup.sh')])
        for row in vendor['observable']:
            name = row['path'].rsplit('/', 1)[1]
            self.assertEqual(row['sha256'], entries[name])
            skeleton = FIXTURES/'vendor/adevtool/vendor-skels/google_devices/frankel'/name
            if skeleton.exists():
                self.assertEqual(hashlib.sha256(skeleton.read_bytes()).hexdigest(), row['sha256'])
        self.assertEqual(vendor['generator'], {'project': 'vendor/adevtool', 'revision': PIN,
            'command': 'generate-all -d frankel'})
        self.assertIn("host's generated bytes", vendor['notEstablished'])

    def test_build_id_kernel_lists_finder_markers_and_manifest_files(self):
        manifest = self.manifest()
        build_id = manifest['buildId']
        self.assertEqual(build_id['expected'], 'CP3A.261005.005')
        self.assertEqual(build_id['setBy']['text'], 'export BUILD_ID_frankel="CP3A.261005.005"')
        self.assertEqual(build_id['readBy']['text'], 'BUILD_ID := $(BUILD_ID_$(TARGET_PRODUCT))')
        self.assertNotEqual(build_id['defaultWithout']['text'], 'BUILD_ID=CP3A.261005.005')
        kernel = manifest['kernelModuleLists']
        self.assertEqual(kernel['dir'], 'device/google/laguna-kernels/6.6/grapheneos/muzel')
        for row in kernel['files']:
            self.assertTrue(row['path'].startswith(kernel['dir'] + '/'))
            self.assertNotEqual(row['gitBlob'], blob(b''), 'an empty list stops lunch')
            self.assertIn('$(KERNEL_MODULE_DIR)/' + row['path'].rsplit('/', 1)[1], row['readBy']['text'])
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
        with tempfile.TemporaryDirectory() as scratch:
            fixtures = Path(scratch)/'fixtures'
            shutil.copytree(FIXTURES, fixtures)
            target = fixtures/'vendor/adevtool/vendor-skels/google_devices/frankel/cmds-for-envsetup.sh'
            target.write_text(target.read_text().replace('CP3A', 'CP9A'))
            module.FIXTURES, module.TREES, module.PROVENANCE = fixtures, fixtures/'trees', fixtures/'PROVENANCE.json'
            errors = module.check(manifest)
        self.assertTrue(any('bytes differ from the pinned blob' in e for e in errors), errors)


if __name__ == '__main__':
    unittest.main()
