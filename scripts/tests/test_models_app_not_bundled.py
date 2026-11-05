"""F02 guard: the standalone Rose Models app (dev.pixel.models) is not part of any OS image or Rose Agent delivery.

OS images and OTAs never carried it: their app inputs are Rose Agent, the calculator agent and background control.
These tests keep it out of:
- every image's and OTA's app inputs and build integration;
- Rose Agent's delivery catalog and publisher;
- the OS release workflow's publication.

The app's own routes are deliberately untouched and stay as they are: models-android.yml (CI), the private models-sign
mode, and the one-off models-publish.yml prerelease route (rose-models-2, never Latest). The same holds for its source,
signing identity, secrets and installed copies. Owner hold, 2026-11-05: the app stays installable and enrollable, as
the Models phone client for the Qwen proof, until Chief/Nathan decide on migration. Core's cloud Models serving, its
auth and the Rose Agent backend are untouched."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = 'dev.pixel.models'
# The app inputs of every OS image and OTA: the release configurations, flavor and build-only bindings, OTA pins.
IMAGE_INPUTS = ('os/release-pipeline', 'os/flavors', 'os/build-only', 'os/ota')
# How a package would be preinstalled: the build patches, overlays and the background-control integration.
BUILD_INTEGRATION = ('os/patches', 'os/overlays', 'os/background-control')
# What Rose Agent delivers to a phone: its delivery catalog and publisher.
AGENT_DELIVERY = ('workspace/rose-agent/delivery/catalog.mjs', 'workspace/rose-agent/delivery/publish.mjs')
OS_APPS = {'dev.pixel.voice', 'dev.pixel.calculator.agent', 'dev.pixel.backgroundcontrol'}


def text_files(relative):
    base = ROOT/relative
    return sorted(p for p in (base.rglob('*') if base.is_dir() else [base]) if p.is_file())


class NotBundled(unittest.TestCase):
    def test_no_image_or_ota_app_input_names_the_models_app(self):
        seen = 0
        for relative in IMAGE_INPUTS:
            for path in text_files(relative):
                seen += 1
                self.assertNotIn(PACKAGE, path.read_text(errors='ignore'), path.relative_to(ROOT))
        self.assertGreater(seen, 20)
        allocations = sorted((ROOT/'os/ota').glob('*-allocation.json'))  # the app sets OTAs bind from a Frankel build
        self.assertTrue(allocations)
        for path in allocations:
            self.assertLessEqual({package for package, _ in json.loads(path.read_text())['apps']}, OS_APPS, path.name)

    def test_no_build_patch_or_overlay_preinstalls_it(self):
        for relative in BUILD_INTEGRATION:
            for path in text_files(relative):
                self.assertNotIn(PACKAGE, path.read_text(errors='ignore'), path.relative_to(ROOT))

    def test_rose_agent_never_delivers_it(self):
        for relative in AGENT_DELIVERY:
            self.assertNotIn(PACKAGE, (ROOT/relative).read_text(), relative)

    def test_the_os_release_workflow_never_publishes_it(self):
        workflow = (ROOT/'.github/workflows/os-release.yml').read_text()
        self.assertNotIn(PACKAGE, workflow)
        # Its one Models job re-signs into a private 7-day artifact and publishes nothing.
        job = re.search(r'^  models-signer:\n(.*?)(?=^  [a-z0-9-]+:\n)', workflow, re.M | re.S).group(1)
        self.assertIn('actions/upload-artifact@', job)
        self.assertIn('retention-days: 7', job)
        for publishing in ('create-github-app-token', 'PIXEL_OTA', 'publish'):
            self.assertNotIn(publishing, job)

    def test_the_models_routes_stay_as_held(self):
        # The hold is part of the boundary: this guard keeps the app out of OS images without retiring its own routes.
        for path in ('.github/workflows/models-android.yml', '.github/workflows/models-publish.yml',
                     'scripts/models-publication.py', 'scripts/host-models-sign.py', 'os/models-signing/models-sign.json'):
            self.assertTrue((ROOT/path).is_file(), path)


if __name__ == '__main__':
    unittest.main()
