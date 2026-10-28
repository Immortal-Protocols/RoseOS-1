"""OS-ALPHA-001: the Cuttlefish alpha product keeps the Rose integration and excludes the phone Updater.

Fixtures are pinned upstream files: build/make b0f697df (product makefiles, target/product/generic/Android.bp,
core/soong_extra_config.mk), build/soong 250710f7 (Android.bp, scripts/gen_build_prop.py), Seedvault 7cfffed7
(Android.bp, contactsbackup/Android.bp) and device/google/cuttlefish 283645aa vsoc_x86_64_only/phone/aosp_cf.mk. The test applies the real Rose build/make patches in patcher order (the 2804
integration), then os/patches/cuttlefish-alpha-no-updater.patch with git apply, and has GNU make evaluate the
aosp_cf.mk inheritance chain. Files outside the fixtures are treated as empty. This proves product-variable
contents, Soong deps declarations and build.prop generation from those variables, not a Soong system image, a
build or a boot."""
import importlib.util
import io
import hashlib
import json
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent/'fixtures/cuttlefish-alpha'
CONTRACT = json.loads((ROOT/'os/alpha/cuttlefish-product-contract.json').read_text())
ALPHA = ROOT/'os/patches/cuttlefish-alpha-no-updater.patch'
ROSE = ['agent-tray-build-make.patch', 'background-control-build-make.patch', 'updater-product-build-make.patch']
sys.path.insert(0, str(ROOT/'scripts'))
from background_apks import preinstall_make_block

def git(*args, cwd, check=True):
    return subprocess.run(['git', *args], cwd=cwd, check=check, capture_output=True, text=True)

class CuttlefishAlphaProductTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        # An independent root holding the 2804 build/make integration, as Infra will prepare it.
        self.make = self.root/'build/make'
        shutil.copytree(FIXTURES/'build-make', self.make)
        git('init', '-q', cwd=self.make)
        git('add', '.', cwd=self.make)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'pinned b0f697df', cwd=self.make)
        for patch in ROSE:
            git('apply', str(ROOT/'os/patches'/patch), cwd=self.make)
        product = self.make/'target/product/handheld_product.mk'
        product.write_text(product.read_text() + preinstall_make_block(
            ['RosePreinstall_dev_pixel_calculator_agent', 'RosePreinstall_dev_pixel_voice']))
        git('add', '.', cwd=self.make)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', '2804 integration', cwd=self.make)
        shutil.copytree(FIXTURES/'cuttlefish', self.root/'device/google/cuttlefish')

    def evaluate(self):
        """PRODUCT_PACKAGES and PRODUCT_SYSTEM_PROPERTIES of aosp_cf.mk, following inherit-product as include."""
        wrapper = self.root/'evaluate.mk'
        wrapper.write_text(textwrap.dedent(f'''\
            SRC_TARGET_DIR := {self.make}/target
            RELEASE_PACKAGE_COMPUTER_CONTROL := true
            TARGET_PRODUCT := aosp_cf_x86_64_only_phone
            inherit-product = $(if $(wildcard $(strip $(1))),$(eval include $(strip $(1))))
            inherit-product-if-exists = $(call inherit-product,$(1))
            include device/google/cuttlefish/vsoc_x86_64_only/phone/aosp_cf.mk
            $(info PACKAGES=$(sort $(PRODUCT_PACKAGES)))
            $(info PROPERTIES=$(sort $(PRODUCT_SYSTEM_PROPERTIES)))
            $(info SOONG_IMAGE=$(USE_SOONG_DEFINED_SYSTEM_IMAGE) $(PRODUCT_SOONG_DEFINED_SYSTEM_IMAGE))
            all: ;
            '''))
        env = {k: v for k, v in os.environ.items() if k != 'OFFICIAL_BUILD'}
        out = subprocess.run(['make', '-s', '-C', str(self.root), '-f', str(wrapper)], env=env,
            check=True, capture_output=True, text=True).stdout
        value = lambda name: re.search(rf'^{name}=(.*)$', out, re.M)[1].split()
        return set(value('PACKAGES')), set(value('PROPERTIES')), value('SOONG_IMAGE')

    def apply_alpha(self):
        check = git('apply', '--check', str(ALPHA), cwd=self.make, check=False)
        self.assertEqual(check.returncode, 0, check.stderr)
        git('apply', str(ALPHA), cwd=self.make)

    def test_the_2804_integration_puts_the_phone_updater_on_this_target(self):
        # The defect this patch prevents: the Rose Updater line reaches aosp_cf, whose system image is Soong-defined.
        packages, _, image = self.evaluate()
        self.assertIn('Updater', packages)
        self.assertEqual(image, ['true', 'aosp_shared_system_image'])

    def test_alpha_patch_excludes_only_the_updater(self):
        before, properties_before, _ = self.evaluate()
        self.apply_alpha()
        after, properties_after, _ = self.evaluate()
        self.assertNotIn('Updater', after)
        self.assertEqual(before - after, {'Updater'})
        self.assertEqual(properties_after, properties_before)
        # Intended Rose integration still reaches the target through its existing makefiles.
        for module in ('PixelBackgroundControl', 'com.android.extensions.computercontrol', 'VirtualDeviceManager',
                       'RosePreinstall_dev_pixel_voice', 'RosePreinstall_dev_pixel_calculator_agent', 'Seedvault'):
            self.assertIn(module, after, module)
        self.assertIn('persist.wm.debug.pixel_agent_tray=1', properties_after)
        # Only the contract's files changed, and the upstream OFFICIAL_BUILD block is intact.
        changed = git('diff', '--name-only', cwd=self.make).stdout.split()
        self.assertEqual(sorted(changed), sorted(f['path'] for f in CONTRACT['osDelta']['changedFiles']))
        media = (self.make/'target/product/media_system.mk').read_text()
        self.assertEqual(media, (FIXTURES/'build-make/target/product/media_system.mk').read_text())
        self.assertIn('ifeq ($(OFFICIAL_BUILD),true)\n    PRODUCT_PACKAGES += Updater\nendif\n', media)

    def test_refuses_a_root_without_the_2804_integration(self):
        # Pinned upstream (or any other base) does not take the patch: preparation must fail closed.
        git('checkout', '-q', 'HEAD~1', '--', '.', cwd=self.make)
        self.assertNotEqual(git('apply', '--check', str(ALPHA), cwd=self.make, check=False).returncode, 0)

    def test_contract_matches_the_delta_and_infra_interface(self):
        patch = CONTRACT['osDelta']['patches'][0]
        self.assertEqual(patch['sha256'], hashlib.sha256(ALPHA.read_bytes()).hexdigest())
        self.assertEqual(patch['project'], 'build/make')
        self.assertEqual(sorted(re.findall(r'^diff --git a/(\S+) ', ALPHA.read_text(), re.M)),
            sorted(f['path'] for f in CONTRACT['osDelta']['changedFiles']))
        self.assertEqual(CONTRACT['target']['lunch'], 'aosp_cf_x86_64_only_phone-cur-userdebug')
        self.assertEqual(CONTRACT['updaterMode'], 'excluded-no-compatible-feed')
        self.assertIsNone(CONTRACT['updaterEndpoint'])
        self.assertNotIn('http', json.dumps({k: v for k, v in CONTRACT.items() if k != 'basis'}))
        self.assertEqual(CONTRACT['base']['candidate'], 'ae9cfd4d9fafd2db4f2bc6aa76b7d025d6473fb9')
        self.assertEqual(CONTRACT['base']['reconcileSha256'], 'db1cc9a215f92a09d772c01efc15590b37fd6e748f97893908838340c9da9de0')
        self.assertEqual({a['package']: (a['version'], a['sha256']) for a in CONTRACT['apps']}, {
            'dev.pixel.voice': (302, '01b184b4a634f0a7843b6fb5ce30dff08f7267ec4e20b5013860b9439dd3a8c6'),
            'dev.pixel.calculator.agent': (4, '9f46c1a2068acdaacc7436fa3f3f29d08938f2896de18948bb10fae6343fd1a8'),
            'dev.pixel.backgroundcontrol': (28, 'f39936b1c217504151aa79fe756d2ce0eda58a6014a6950a8979aaaef91b827a')})
        self.assertEqual(CONTRACT['policySha256'], '044b99e57fa97d73b77fc46a349c47f7f6003fa9a202558ad577d9a83a471423')
        self.assertEqual(CONTRACT['pinned'], {'device/google/cuttlefish': '283645aacf6cdb56cc31a9362f54d412dbc132a1',
            'build/make': 'b0f697df8cc9519408ca0a404e918cd51bde0735',
            'build/soong': '250710f7f87acfbb8e3c517d90abde8b4a08fe1e',
            'packages/apps/Seedvault': '7cfffed7ceeda60a10bf2a6d9818d6fda81828ba'})

    def image_deps(self):
        """Quoted module names in aosp_shared_system_image's own deps list."""
        text = (self.make/'target/product/generic/Android.bp').read_text()
        block = text[text.index('name: "aosp_shared_system_image"'):]
        start = block.index('deps: [')
        deps = block[start:block.index('],', start)]
        return re.findall(r'"([^"]+)"', deps)

    def test_seedvault_and_its_required_closure_join_the_soong_system_image(self):
        # Seedvault's actual required closure, read from its pinned Android.bp files.
        modules = {}
        for bp in (FIXTURES/'seedvault/Android.bp', FIXTURES/'seedvault/contactsbackup/Android.bp'):
            for name, body in re.findall(r'\n\w+ \{\n\s*name: "([^"]+)",(.*?)\n\}', bp.read_text(), re.S):
                required = re.search(r'required: \[(.*?)\]', body, re.S)
                modules[name] = re.findall(r'"([^"]+)"', required[1]) if required else []
        closure, todo = set(), ['Seedvault']
        while todo:
            name = todo.pop()
            if name not in closure:closure.add(name); todo += modules.get(name, [])
        self.assertEqual(len(closure), 6)
        # Pinned image lists none of it (whole file, including system_image_defaults and selects) ...
        pinned = (FIXTURES/'build-make/target/product/generic/Android.bp').read_text()
        self.assertFalse(closure & set(re.findall(r'"([^"]+)"', pinned)))
        # ... while make installs Seedvault on system for this product: file_list_diff would fail.
        self.assertIn('Seedvault', self.evaluate()[0])
        self.apply_alpha()
        self.assertEqual(set(self.image_deps()) - {'logpersist.start'}, closure)
        # Baseline Seedvault stays in the product; nothing is removed, allowlisted or disabled.
        self.assertIn('Seedvault', self.evaluate()[0])
        bp_diff = git('diff', '--', 'target/product/generic/Android.bp', cwd=self.make).stdout
        self.assertFalse([l for l in bp_diff.splitlines() if l.startswith('-') and not l.startswith('---')])
        # Only the two contract files change; no added line touches the image switch or the diff allowlist.
        self.assertEqual(sorted(git('diff', '--name-only', cwd=self.make).stdout.split()),
                         ['target/product/generic/Android.bp', 'target/product/media_system.mk'])
        added = [l for l in git('diff', cwd=self.make).stdout.splitlines() if l.startswith('+') and not l.startswith('+++')]
        self.assertFalse([l for l in added if re.search(r'USE_SOONG_DEFINED_SYSTEM_IMAGE|system_image_diff_allowlist|filelistdiff', l)])

    def test_tray_property_reaches_the_soong_system_build_prop(self):
        self.apply_alpha()
        _, properties, _ = self.evaluate()
        # The image takes system-build.prop, which reads the lunched product's merged config ...
        generic = (self.make/'target/product/generic/Android.bp').read_text()
        defaults = generic[generic.index('system_image_defaults {\n    name: "system_image_defaults"'):generic.index('android_system_image {')]
        self.assertIn('"system-build.prop"', defaults)
        self.assertIn('defaults: ["system_image_defaults"]', generic[generic.index('name: "aosp_shared_system_image"') - 60:])
        soong = (FIXTURES/'build-soong/Android.bp').read_text()
        self.assertRegex(soong, r'build_prop \{\n    name: "system-build.prop",\n    stem: "build.prop",\n    product_config: ":product_config",')
        # ... where make exports that product's PRODUCT_SYSTEM_PROPERTIES (soong.<product>.extra.variables).
        extra = (FIXTURES/'build-make/core/soong_extra_config.mk').read_text()
        self.assertIn('$(call add_json_list, PRODUCT_SYSTEM_PROPERTIES,         $(call collapse-prop-pairs,PRODUCT_SYSTEM_PROPERTIES))', extra)
        # The pinned generator writes that list into the system build.prop.
        spec = importlib.util.spec_from_file_location('gen_build_prop', FIXTURES/'build-soong/scripts/gen_build_prop.py')
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        written = []
        gen.build_prop = lambda args, gen_build_info, gen_common_build_props, variables: written.extend(variables)
        args = type('Args', (), {'config': {'PropertySplitEnabled': True, 'PRODUCT_SYSTEM_PROPERTIES': sorted(properties)}})()
        gen.build_system_prop(args)
        self.assertIn('PRODUCT_SYSTEM_PROPERTIES', written)
        out = io.StringIO()
        with __import__('contextlib').redirect_stdout(out):
            gen.write_properties_from_variable('PRODUCT_SYSTEM_PROPERTIES', args.config['PRODUCT_SYSTEM_PROPERTIES'], False)
        self.assertIn('\npersist.wm.debug.pixel_agent_tray=1\n', out.getvalue())

    def test_pixel_recipe_is_unchanged(self):
        # The Frankel patcher still includes the Updater and never applies the alpha patch.
        patcher = (ROOT/'scripts/apply-os-changes.py').read_text()
        self.assertIn("projects.setdefault('build/make', []).append('updater-product-build-make.patch')", patcher)
        self.assertNotIn('cuttlefish-alpha', patcher)

if __name__ == '__main__':
    unittest.main()
