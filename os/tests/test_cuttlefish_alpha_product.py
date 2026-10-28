"""OS-ALPHA-001: the Cuttlefish alpha product keeps the Rose integration and excludes the phone Updater.

Fixtures are pinned upstream files: build/make b0f697df product makefiles and device/google/cuttlefish 283645aa
vsoc_x86_64_only/phone/aosp_cf.mk. The test applies the real Rose build/make patches in patcher order (the 2804
integration), then os/patches/cuttlefish-alpha-no-updater.patch with git apply, and has GNU make evaluate the
aosp_cf.mk inheritance chain. Files outside the fixtures are treated as empty. This proves product-variable
contents only, not a Soong system image, a build or a boot."""
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
        # Only media_system.mk changed, and the upstream OFFICIAL_BUILD block is intact.
        changed = git('diff', '--name-only', cwd=self.make).stdout.split()
        self.assertEqual(changed, [f['path'] for f in CONTRACT['osDelta']['changedFiles']])
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
        self.assertEqual(re.findall(r'^diff --git a/(\S+) ', ALPHA.read_text(), re.M),
            [f['path'] for f in CONTRACT['osDelta']['changedFiles']])
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
            'build/make': 'b0f697df8cc9519408ca0a404e918cd51bde0735'})

    def test_pixel_recipe_is_unchanged(self):
        # The Frankel patcher still includes the Updater and never applies the alpha patch.
        patcher = (ROOT/'scripts/apply-os-changes.py').read_text()
        self.assertIn("projects.setdefault('build/make', []).append('updater-product-build-make.patch')", patcher)
        self.assertNotIn('cuttlefish-alpha', patcher)

if __name__ == '__main__':
    unittest.main()
