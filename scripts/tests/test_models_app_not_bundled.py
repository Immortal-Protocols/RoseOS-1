"""1I guard: Rose Models (dev.pixel.models) is retired, with no successor (#651, docs/MODELS-RETIREMENT.md).

OS images and OTAs never carried it: their app inputs are Rose Agent, the calculator agent and background control.
These tests keep it out of:
- every image's and OTA's app inputs and build integration;
- Rose Agent's delivery catalog and publisher;
- every Release route: no workflow, mode, job or script signs or publishes it.

Retired, not erased: the models-v0 signing-bootstrap profile and its signer identity, key and secrets stay (no key or
secret is deleted), and so does the already-published rose-models-2 prerelease in pixel-ota (never Latest). The app's
unsigned CI build (models-android.yml) and its source go next, on Home's side: that artifact only fed the removed
models-sign route. Core's cloud Models serving, its auth and the Rose Agent backend are untouched."""
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
# Release's slice of 1I: the Models sign and publish routes it removed.
RETIRED_ROUTES = ('.github/workflows/models-publish.yml', '.github/workflows/models-signing-verification.yml',
                  'scripts/models-publication.py', 'scripts/host-models-sign.py', 'scripts/models-input-ingress.py',
                  'scripts/models-sdk-prepare.py', 'os/models-signing', 'workspace/models/signing')


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

    def test_the_os_release_workflow_has_no_models_route(self):
        workflow = (ROOT/'.github/workflows/os-release.yml').read_text()
        self.assertNotIn(PACKAGE, workflow)
        for retired in ('models-sign', 'models-signer', 'models_bootstrap_run', 'MODELS_BOOTSTRAP_RUN', 'models-v0',
                        'models-input', 'models-bootstrap'):
            self.assertNotIn(retired, workflow, retired)
        self.assertNotIn('models-sign', (ROOT/'os/release-pipeline/lanes/capacity.json').read_text())

    def test_no_route_signs_or_publishes_it(self):
        for path in RETIRED_ROUTES:
            self.assertFalse((ROOT/path).exists(), path)
        workflows = sorted((ROOT/'.github/workflows').glob('*.yml'))
        self.assertGreater(len(workflows), 10)
        for path in workflows:
            if path.name == 'models-android.yml':  # the app's unsigned CI build, removed next on Home's side
                continue
            text = path.read_text()
            self.assertNotIn(PACKAGE, text, path.name)
            self.assertNotRegex(text, r'models-(sign|signer|signing|publish|publication)\b', path.name)
        for path in sorted((ROOT/'scripts').glob('*.py')) + sorted((ROOT/'scripts').glob('*.sh')):
            self.assertNotIn(PACKAGE, path.read_text(errors='ignore'), path.name)
        self.assertNotIn('models', (ROOT/'scripts/sign-apk.py').read_text().lower())  # no Models diagnostic hook

    def test_the_signer_record_stays_without_a_route(self):
        # Kept: the retired signer's identity record. No key or secret is deleted; nothing dispatches it any more.
        profiles = json.loads((ROOT/'os/signing-bootstrap/profiles.json').read_text())['profiles']
        self.assertIn('models-v0', profiles)


if __name__ == '__main__':
    unittest.main()
