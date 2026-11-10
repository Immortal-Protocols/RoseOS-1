#!/usr/bin/env python3
"""#410: the fail-closed compile contract for OS Core's Shell patch, os/compile-contracts/frameworks-base-wmshell.json.

These checks cover:
- the manifest recomputes from the committed fixtures and patches;
- its candidate is the patch on main;
- the contract refuses when changed;
- it carries OS Core's recorded confirmation;
- each Shell and SystemUI file is placed in the module that compiles it;
- it never registers the patch, because registration waits for a real compile (#410).
"""
import copy
import gzip
import importlib.util
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT/'scripts/frameworks-base-wmshell-contract.py'
MANIFEST = ROOT/'os/compile-contracts/frameworks-base-wmshell.json'
LISTING = ROOT/'os/tests/fixtures/launcher3-compile-contract/trees/frameworks__base.ls-tree.txt.gz'
SHELL = 'libs/WindowManager/Shell/src/com/android/wm/shell/'


def load():
    spec = importlib.util.spec_from_file_location('frameworks_base_wmshell_contract', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


W = load()


class FrameworksBaseWmshellContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text())

    def test_the_manifest_recomputes_from_the_committed_fixtures(self):
        result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('confirmation confirmed', result.stdout)

    def test_the_candidate_is_os_cores_patch_on_main(self):
        delta = self.manifest['candidateDelta']
        self.assertEqual((delta['pr'], delta['commit']), (744, '8ed43f1762e690b5db1ff9f9226d9ff9fa760a3f'))
        patch = (ROOT/'os/patches/rose-edge-wmshell.patch').read_bytes()
        self.assertEqual([p['name'] for p in delta['patches']], ['rose-edge-wmshell.patch'])
        self.assertEqual(delta['patches'][0]['sha256'], W.C.sha256(patch))
        self.assertEqual(delta['patches'][0]['project'], 'frameworks/base')
        bundle = ROOT/delta['patches'][0]['bundlePath']
        self.assertEqual(bundle.read_bytes(), patch, 'the inert bundle copy is the committed patch')

    def test_preimages_are_the_pinned_tree_and_after_images_are_the_patchs_own_index(self):
        listing = {}
        for line in gzip.decompress(LISTING.read_bytes()).decode().splitlines():
            meta, path = line.split('\t', 1)
            listing[path] = meta.split(' ')[2]
        index = {path: lines for path, _, lines in W.C.file_blocks('rose-edge-wmshell.patch')}
        files = {f['path'][len('frameworks/base/'):]: f for f in self.manifest['candidateDelta']['files']}
        self.assertEqual(set(files), set(index))
        for path, row in files.items():
            if row['before'] is None:
                self.assertNotIn(path, listing, f'{path}: new, so absent from the pinned tree')
            else:
                self.assertEqual(row['before']['gitBlob'], listing[path], f'{path}: pre is the pinned blob')
                self.assertTrue(row['before']['gitBlob'].startswith(index[path][0]))
            self.assertTrue(row['after']['gitBlob'].startswith(index[path][1]), f'{path}: after is the patch index')
        self.assertEqual(files[SHELL + 'dagger/WMShellModule.java']['before']['gitBlob'],
            '237f47124837e3f939172f25f18a2d3444423641')
        others = [p for p in sorted((ROOT/'os/patches').glob('*.patch')) if p.name != 'rose-edge-wmshell.patch']
        for other in others:
            self.assertNotIn('WMShellModule.java', other.read_text(errors='replace'),
                f'{other.name}: only the candidate touches WMShellModule, so its pre is the pinned blob')

    def test_the_base_excludes_the_candidate_and_the_goals_are_wmshell_and_systemui(self):
        base = [p['name'] for p in self.manifest['base']['bundleContent']['patches']]
        self.assertNotIn('rose-edge-wmshell.patch', base)
        self.assertEqual(self.manifest['projects'][0]['patches'][-1]['name'], 'rose-edge-wmshell.patch')
        self.assertEqual(self.manifest['overlayRoles']['compileGoals'], ['WindowManager-Shell', 'SystemUI-core'])
        self.assertEqual(self.manifest['targets'], ['WindowManager-Shell', 'SystemUI-core'])

    def test_every_frameworks_base_overlay_is_a_required_input(self):
        committed = sorted(str(f.relative_to(ROOT/'os/overlays')) for f in (ROOT/'os/overlays/frameworks/base').rglob('*')
            if f.is_file())
        required = self.manifest['overlayRoles']['required']['targets']
        self.assertTrue(committed)
        for target in committed:
            self.assertIn(target, required)
        self.assertIn('frameworks/base/' + SHELL + 'AgentTrayLink.java', required)
        self.assertEqual(self.manifest['overlayRoles']['notAnInput']['targets'], [])
        for row in self.manifest['overlays']:
            self.assertEqual(W.C.sha256((ROOT/'os/overlays'/row['target']).read_bytes()), row['sha256'])

    def test_it_carries_os_cores_confirmation_and_never_registers(self):
        self.assertEqual(self.manifest['confirmation']['state'], 'confirmed')
        self.assertEqual(self.manifest['confirmation'], W.CONFIRMATION)
        self.assertIn('refuses this contract unless state is "confirmed"', self.manifest['confirmation']['rule'])
        self.assertEqual(self.manifest['registration']['state'], 'unregistered')
        # The live patcher now registers it for the normal OS build, whose compile is the proof (#410, Chief); this
        # inert contract keeps describing the pre-registration candidate.
        patcher = (ROOT/'scripts/apply-os-changes.py').read_text()
        self.assertIn("projects['frameworks/base'].append('rose-edge-wmshell.patch')", patcher)

    def test_each_shell_and_systemui_file_is_proved_in_the_module_that_compiles_it(self):
        roles = self.manifest['overlayRoles']
        self.assertEqual(roles['goalDirs'], {'WindowManager-Shell': 'frameworks/base/libs/WindowManager/Shell',
            'SystemUI-core': 'frameworks/base/packages/SystemUI'})
        listing = {}
        for line in gzip.decompress(LISTING.read_bytes()).decode().splitlines():
            meta, path = line.split('\t', 1)
            listing['frameworks/base/' + path] = meta.split(' ')[2]
        for bp in roles['buildGraph']:
            self.assertEqual(bp['gitBlob'], listing[bp['path']], f'{bp["path"]}: the pinned Android.bp')
        modules = {r['file'].rsplit('/', 1)[1]: r['module'] for r in roles['sourceProof']['files']}
        self.assertEqual(modules['RoseEdgeController.java'], 'wm_shell_protolog_src')
        self.assertEqual(modules['RoseEdgeMicSlot.java'], 'wm_shell_protolog_src')
        self.assertEqual(modules['AgentTrayLink.java'], 'wm_shell_protolog_src')
        self.assertEqual(modules['IRoseEdge.aidl'], 'WindowManager-Shell-aidls')
        self.assertEqual(modules['PixelStatusBarFilter.java'], 'SystemUI-core')
        proved = {r['file'] for r in roles['sourceProof']['files']}
        for f in self.manifest['candidateDelta']['files']:
            self.assertIn(f['path'], proved, 'every candidate file is source-proved')
        for path in roles['sourceProof']['notSourceProved']:
            self.assertFalse(any(path.startswith(d + '/') for d in roles['goalDirs'].values()))
        with self.assertRaises(ValueError):
            W.source_proof([{'path': 'frameworks/base', 'files': [{'path': 'libs/WindowManager/Shell/res/x.xml',
                'post': {}}]}], [])

    def test_any_change_refuses(self):
        def mutated(change):
            manifest = copy.deepcopy(self.manifest)
            change(manifest)
            return W.check(manifest)
        self.assertEqual(W.check(copy.deepcopy(self.manifest)), [])
        cases = {
            'patch bytes': lambda m: m['candidateDelta']['patches'][0].update(sha256='0' * 64),
            'after image': lambda m: m['candidateDelta']['files'][0]['after'].update(sha256='0' * 64),
            'goal': lambda m: m['overlayRoles']['compileGoals'].pop(),
            'tray overlay': lambda m: m['overlays'][-1].update(sha256='0' * 64),
            'candidate commit': lambda m: m['candidateDelta'].update(commit='0' * 40),
            'confirmation': lambda m: m['confirmation'].update(state='assumed'),
            'registration': lambda m: m['registration'].update(state='registered'),
            'unconfirmed': lambda m: m['confirmation'].update(state='pending'),
            'goal dir': lambda m: m['overlayRoles']['goalDirs'].update({'SystemUI-core': 'frameworks/base'}),
            'source module': lambda m: m['overlayRoles']['sourceProof']['files'][0].update(module='WindowManager-Shell'),
            'pre-image': lambda m: next(f for f in m['projects'][0]['files']
                if f['path'] == SHELL + 'dagger/WMShellModule.java')['pre'].update(gitBlob='0' * 40),
        }
        for name, change in cases.items():
            with self.subTest(name):
                self.assertTrue(mutated(change), f'{name}: a changed contract must refuse')

    def test_the_launcher3_contract_is_untouched_by_the_sibling(self):
        result = subprocess.run([sys.executable, str(ROOT/'scripts/launcher3-compile-contract.py')], capture_output=True,
            text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # The shared generator moves the Launcher3 contract to schema 3; this one stays at schema 2. Its bytes change
        # only with a rebind OS Core reconfirms (#540: 2373307d... -> ae6e5f39...; #551: -> 55bfb937...; #563: -> c5dccd7d...).
        self.assertEqual(self.manifest['schemaVersion'], 2)
        import hashlib
        self.assertEqual(hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
            'b800b8840775524951ba430f24a542676a7c5374229eaf5ffc2e00aa69a468bd')
        self.assertTrue(re.search(r'"target": "Launcher3QuickStepLib"',
            (ROOT/'os/compile-contracts/launcher3-quickstep-lib.json').read_text()))


if __name__ == '__main__':
    unittest.main()
