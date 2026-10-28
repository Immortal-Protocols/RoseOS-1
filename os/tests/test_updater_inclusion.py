"""The Updater is built into RoseOS products: run the real apply-os-changes.py on small fixture git trees, then
let GNU make evaluate the patched media_system.mk with OFFICIAL_BUILD unset. Fixtures are the pinned upstream
files (build/make b0f697df media_system.mk, Updater 52c34a55 config.xml); unrelated patches are replaced by
trivial ones so only the Updater integration is exercised. No Android build: proves product inclusion and the
compiled config inputs, not an APK in target-files."""
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
FIXTURES = Path(__file__).resolve().parent/'fixtures/updater-inclusion'
URL = 'https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/'
PROJECTS = {'frameworks/base': {'stub.txt': 'upstream\n'},
            'build/make': {'target/product/media_system.mk': (FIXTURES/'media_system.mk').read_text()},
            'packages/apps/Updater': {'res/values/config.xml': (FIXTURES/'config.xml').read_text()}}

def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

def product_packages(makefile, official=None):
    """PRODUCT_PACKAGES as GNU make evaluates the product file; inherit-product calls expand to nothing."""
    wrapper = makefile.parent/'evaluate.mk'
    wrapper.write_text(textwrap.dedent(f'''\
        SRC_TARGET_DIR := /nonexistent
        include {makefile}
        $(info PACKAGES=$(sort $(PRODUCT_PACKAGES)))
        all: ;
        '''))
    env = {k: v for k, v in os.environ.items() if k != 'OFFICIAL_BUILD'}
    if official is not None:env['OFFICIAL_BUILD'] = official
    out = subprocess.run(['make', '-s', '-f', str(wrapper)], env=env, check=True, capture_output=True, text=True).stdout
    wrapper.unlink()
    return set(re.search(r'^PACKAGES=(.*)$', out, re.M)[1].split())

class UpdaterInclusionTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        # A private bundle: the real patcher and os/ tree, with fixture revisions and trivial unrelated patches.
        (root/'scripts').mkdir()
        for name in ('apply-os-changes.py', 'background_apks.py', 'integration_copy.py'):
            shutil.copy(ROOT/'scripts'/name, root/'scripts'/name)
        shutil.copytree(ROOT/'os', root/'os', ignore=shutil.ignore_patterns('tests', '__pycache__'))
        self.source = root/'source'
        manifest = (root/'os/pinned-manifest.xml').read_text()
        for project, files in PROJECTS.items():
            repo = self.source/project
            for relative, text in files.items():
                (repo/relative).parent.mkdir(parents=True, exist_ok=True)
                (repo/relative).write_text(text)
            git('init', '-q', cwd=repo)
            git('add', '.', cwd=repo)
            git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'pinned', cwd=repo)
            manifest, count = re.subn(rf'(path="{re.escape(project)}"[^>]*revision=")[0-9a-f]+', rf'\g<1>{git("rev-parse", "HEAD", cwd=repo)}', manifest)
            self.assertEqual(count, 1, project)
        (root/'os/pinned-manifest.xml').write_text(manifest)
        (root/'os/patches/frameworks-base.patch').write_text(
            'diff --git a/stub.txt b/stub.txt\n--- a/stub.txt\n+++ b/stub.txt\n@@ -1 +1 @@\n-upstream\n+patched\n')
        # Keep only the real channel patch's config.xml hunk; its Java and other resources are covered elsewhere.
        channels = (ROOT/'os/patches/updater-rose-channels.patch').read_text()
        config = 'diff --git a/res/values/config.xml' + channels.split('diff --git a/res/values/config.xml', 1)[1].split('\ndiff --git ', 1)[0] + '\n'
        (root/'os/patches/updater-rose-channels.patch').write_text(config)
        self.script = root/'scripts/apply-os-changes.py'
        self.media = self.source/'build/make/target/product/media_system.mk'

    def apply(self, *args):
        return subprocess.run([sys.executable, str(self.script), str(self.source), *args], capture_output=True, text=True)

    def test_pinned_upstream_omits_the_updater_unless_official(self):
        # The candidate077 defect: without OFFICIAL_BUILD the product never lists Updater.
        self.assertNotIn('Updater', product_packages(self.media))
        self.assertIn('Updater', product_packages(self.media, official='true'))
        self.assertIn('Seedvault', product_packages(self.media))

    def test_a_channel_build_includes_the_existing_updater_with_official_build_unset(self):
        result = self.apply('--updater-url', URL)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        packages = product_packages(self.media)
        self.assertIn('Updater', packages)
        self.assertIn('Seedvault', packages)
        # The upstream OFFICIAL_BUILD block itself is untouched, and nothing sets the flag.
        text = self.media.read_text()
        self.assertIn('ifeq ($(OFFICIAL_BUILD),true)\n    PRODUCT_PACKAGES += Updater\nendif\n', text)
        self.assertNotRegex(text, r'OFFICIAL_BUILD\s*:?=')
        # The same run configured that Updater: channel base, Prod default for new installs, only stable published.
        config = (self.source/'packages/apps/Updater/res/values/config.xml').read_text()
        self.assertIn(f'<string name="url" translatable="false">{URL}</string>', config)
        self.assertIn('<string name="channel_default" translatable="false">prod</string>', config)
        self.assertRegex(config, r'<string-array name="channel_published" translatable="false">\s*<item>stable</item>\s*</string-array>')
        self.assertTrue((self.source/'packages/apps/Updater/src/app/seamlessupdate/client/RoseChannels.java').is_file())

    def test_no_channel_means_no_updater(self):
        # A build that never updates a phone keeps upstream behaviour: no Updater polling GrapheneOS.
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('Updater', product_packages(self.media))
        self.assertEqual(git('status', '--porcelain', cwd=self.source/'build/make'), '')

if __name__ == '__main__':
    unittest.main()
