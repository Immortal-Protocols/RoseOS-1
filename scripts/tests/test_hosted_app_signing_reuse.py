#!/usr/bin/env python3
"""Signing-owned offline check of the existing app-signing reuse path for an off-builder (hosted) Agent route.

host-app-release.py main() is the Frankel app-only wrapper: it loads the Frankel host config, holds the builder's
source/output lock, requires the 300 GiB builder reserve plus the app workspace allowance, applies the
mustExceed/reserved version floor and reads the builder-local latest-beta floor. A hosted route must not run those.
This file shows, against the unchanged code, that release() (worktree, build, the four pinned app secrets in owner-only
RAM, sign-apk.py, independent verification, receipt) needs none of them, and records exactly what a hosted entry point
must still provide. Fakes only, the same stand-ins as test_app_release.py: no secret, key, credential, network, lock,
builder, signing identity or publication. A passing mock is not a live signature or a release.
"""
import json
from pathlib import Path
import subprocess
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_app_release as base  # noqa: E402  (fixtures only; its own tests are not re-run here)

release, ROOT, PIN, OTHER = base.release, base.ROOT, base.PIN, base.OTHER
FORBIDDEN = ('disk_preflight', 'allocated_floor', 'requested_version', 'main')


class Recording(dict):
    """A config dict that records every key path release() reads (item access and get)."""

    def __init__(self, data, seen, prefix=()):
        super().__init__({k: Recording(v, seen, prefix + (k,)) if isinstance(v, dict) else v for k, v in data.items()})
        self._seen, self._prefix = seen, prefix

    def __getitem__(self, key):
        self._seen.add('.'.join(self._prefix + (key,)))
        return super().__getitem__(key)

    def get(self, key, default=None):
        self._seen.add('.'.join(self._prefix + (key,)))
        return super().get(key, default)


class HostedReuse(unittest.TestCase):
    setUp = base.AppReleaseTest.setUp
    build = base.AppReleaseTest.build
    fetched = base.AppReleaseTest.fetched

    def hosted_config(self):
        """The fixture config with every Frankel-builder-only field removed: no lock path, source root, OS-signing,
        reconcile, make or command entries, and no mustExceed/reserved floor beyond what release() itself reads."""
        data = json.loads(json.dumps(self.config_data))
        host = data['host']
        for key in ('lockPath', 'sourceRoot', 'integrateCommand', 'makeCommand', 'signCommand', 'sign'):
            host.pop(key)
        for key in ('reconcileCommand', 'appBuildCommand', 'sdk'):
            host['integrate'].pop(key)
        return data

    def forbid_wrapper(self):
        """Every Frankel-wrapper step raises if reached; the lock primitive too."""
        def trap(name):
            def refuse(*args, **kwargs):
                raise AssertionError(f'{name} reached on the hosted reuse path')
            return refuse
        originals = {name: getattr(release, name) for name in FORBIDDEN}
        flock = release.fcntl.flock
        for name in FORBIDDEN:
            setattr(release, name, trap(name))
        release.fcntl.flock = trap('fcntl.flock')

        def restore():
            for name, value in originals.items():
                setattr(release, name, value)
            release.fcntl.flock = flock
        self.addCleanup(restore)

    def cleanup_like_main(self):
        """What main()'s finally does and a hosted entry point must do too: release() itself never removes them."""
        subprocess.run(['git', '-C', str(ROOT), 'worktree', 'remove', '--force', str(self.work/'candidate')], capture_output=True)
        subprocess.run(['git', '-C', str(ROOT), 'worktree', 'prune'], capture_output=True)

    def sign(self, config, build=None, version=306):
        self.addCleanup(self.cleanup_like_main)
        tools = config['host']['tools']
        return release.release(config, self.head, self.work, version, tools['aapt'], tools['apksigner'], self.artifact,
                               self.env, self.gcloud, build or self.build)

    def assert_secrets_gone(self):
        self.assertEqual(sorted(c[c.index('--secret') + 1] for c in self.fetched()), ['CONFIG', 'KEY', 'KEYSTORE', 'STORE'])
        for call in self.fetched():
            self.assertEqual(call[3], '1', 'the pinned numeric version, never latest')
            self.assertFalse(Path(call[call.index('--out-file') + 1]).exists(), 'RAM secret copy removed')

    def test_release_signs_and_verifies_without_lock_disk_reserve_or_version_floors(self):
        self.forbid_wrapper()
        receipt = self.sign(self.hosted_config())
        self.assertEqual((receipt['packageName'], receipt['versionCode'], receipt['signerSha256']), ('dev.pixel.voice', 306, PIN))
        self.assertIn('independent apksigner verification: exactly the pinned signer', receipt['checks'])
        self.assertEqual(sorted(p.name for p in self.artifact.iterdir()), ['SHA256SUMS', 'app-release-receipt.json',
                         'dev.pixel.voice-306.apk', 'dev.pixel.voice-306.apk.idsig'])
        artifact_bytes = b''.join(p.read_bytes() for p in self.artifact.iterdir())
        for secret in self.secrets.iterdir():
            self.assertNotIn(secret.read_bytes(), artifact_bytes)
        self.assertFalse(self.lock.exists(), 'no lock file was opened')
        self.assertEqual(self.statted, [], 'no disk reserve check')
        self.assert_secrets_gone()

    def test_exact_config_fields_release_reads(self):
        """The hosted config contract: exactly these fields. appRelease.mustExceed is read only for the receipt's check
        text, which a hosted entry point must replace with its owned reservation (handoff, not changed here)."""
        self.forbid_wrapper()
        seen = set()
        self.sign(Recording(self.hosted_config(), seen))
        self.assertEqual(seen, {
            'host', 'host.integrate', 'host.integrate.project', 'host.integrate.secretVersion', 'host.integrate.appSecrets',
            'host.integrate.appSecrets.keystore', 'host.integrate.appSecrets.config',
            'host.integrate.appSecrets.storePassword', 'host.integrate.appSecrets.keyPassword',
            'host.integrate.appSdkRoot', 'host.integrate.javaHome', 'host.integrate.publicAssetsDir',
            'host.integrate.publicAssetDigests', 'host.tools', 'host.tools.aapt', 'host.tools.apksigner',
            'identities', 'identities.appSignerSha256',
            'appRelease', 'appRelease.mustExceed', 'appRelease.minimumTestedOs'})

    def test_committed_signing_inputs_a_hosted_config_must_copy(self):
        """The signing values a hosted config must carry unchanged from the committed Frankel route."""
        frankel = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
        integrate = frankel['host']['integrate']
        self.assertEqual((integrate['project'], integrate['secretVersion'], integrate['appSecrets']),
                         ('protocols-465723', '1', {'keystore': 'PIXEL_APP_SIGNING_KEYSTORE', 'config': 'PIXEL_APP_SIGNING_CONFIG',
                          'storePassword': 'PIXEL_APP_SIGNING_STORE_PASSWORD', 'keyPassword': 'PIXEL_APP_SIGNING_KEY_PASSWORD'}))
        self.assertRegex(frankel['identities']['appSignerSha256'], r'^[0-9a-fA-F]{64}$')

    def test_prestaged_unsigned_apk_signs_with_a_no_op_build(self):
        """A build job can hand the sign step its unsigned APK: release() accepts an injected build, so a no-op build
        over a pre-staged work/unsigned/voice.apk signs it without building with the signer login active."""
        self.forbid_wrapper()
        (self.work/'unsigned').mkdir(parents=True)
        with zipfile.ZipFile(self.work/'unsigned/voice.apk', 'w') as apk:
            apk.writestr('identity', 'dev.pixel.voice:306')
        built = []
        receipt = self.sign(self.hosted_config(), build=lambda *a, **k: built.append(a))
        self.assertEqual(len(built), 1)
        self.assertEqual(receipt['versionCode'], 306)
        self.assert_secrets_gone()

    def test_wrong_unsigned_identity_refuses_before_any_secret(self):
        for identity, version in (('dev.pixel.voice:307', 306), ('dev.pixel.other:306', 306)):
            with self.subTest(identity):
                self.built = identity
                with self.assertRaisesRegex(release.AdapterFailed, 'expected dev.pixel.voice 306'):
                    self.sign(self.hosted_config(), version=version)
                self.assertEqual(self.fetched(), [], 'refused before any secret fetch')
                self.assertFalse(self.artifact.exists())
                self.cleanup_like_main()
                subprocess.run(['rm', '-rf', str(self.work)])

    def test_signer_mismatch_refuses_and_removes_secrets_and_artifact(self):
        self.signer_cert.write_text(OTHER)  # sign-apk.py's own pin check fails
        with self.assertRaises(release.AdapterFailed):
            self.sign(self.hosted_config())
        self.assertFalse(self.artifact.exists())
        self.assert_secrets_gone()

    def test_release_leaves_worktree_and_work_dir_for_the_caller(self):
        """release() does not clean up its candidate worktree or work dir; main()'s finally does. A hosted entry point
        must keep that cleanup (and remove a partial artifact on failure)."""
        self.sign(self.hosted_config())
        self.assertTrue((self.work/'candidate').is_dir())
        listed = subprocess.run(['git', '-C', str(ROOT), 'worktree', 'list'], capture_output=True, text=True).stdout
        self.assertIn(str(self.work/'candidate'), listed)


if __name__ == '__main__':
    unittest.main()
