#!/usr/bin/env python3
"""The hosted (off-builder) Agent route of scripts/host-app-release.py (--hosted, os/release-pipeline/agent-hosted.json).

Signing's #608 handoff: a hosted entry point admits one owned reservation and then calls the unchanged release(), with
none of main()'s Frankel wrapper (Frankel config, host lock, disk reserve, mustExceed/reserved floor, builder allocation
floor), keeps main()'s cleanup, and records the reservation instead of mustExceed. Fakes only, the stand-ins of
test_app_release.py: no secret, key, credential, network, lock, builder, signing identity or publication. A passing
mock is not a live signature, a hosted build or a release; the committed hosted config is unbound.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_app_release as base  # noqa: E402  (fixtures only; its own tests are not re-run here)

release, ROOT, PIN, OTHER = base.release, base.ROOT, base.PIN, base.OTHER
COMMITTED = json.loads((ROOT/'os/release-pipeline/agent-hosted.json').read_text())
FRANKEL = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
WRAPPER = ('disk_preflight', 'allocated_floor', 'requested_version', 'missing_inputs', 'main')
WORKFLOW_REF = 'Immortal-Protocols/RoseOS-1/.github/workflows/os-release.yml@refs/heads/main'
STORE = {'kind': 'synthetic-test-store', 'ref': 'synthetic only; no real ledger'}
UNBOUND = ['host.integrate.appSdkRoot', 'host.integrate.javaHome', 'host.integrate.publicAssetsDir',
           'host.tools.aapt', 'host.tools.apksigner', 'reservation.store']


def digest(record):
    body = {k: v for k, v in record.items() if k != 'reservationSha256'}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Committed(unittest.TestCase):
    def test_the_committed_hosted_config_is_unbound(self):
        self.assertEqual(release.hosted_missing_inputs(COMMITTED), UNBOUND)
        result = subprocess.run([sys.executable, str(ROOT/'scripts/host-app-release.py'), '--check', '--hosted'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(json.loads(result.stderr), {'verdict': 'SETUP_MISSING', 'missing': UNBOUND})
        result = subprocess.run([sys.executable, str(ROOT/'scripts/host-app-release.py'), '--hosted', '--artifact-dir',
                                 '/nonexistent/agent-hosted-artifact'], capture_output=True, text=True,
                                env=dict(os.environ, ROSE_CANDIDATE_SHA='f' * 40, ROSE_RELEASE_DIR='/nonexistent/work'))
        self.assertEqual(result.returncode, 4)  # refused before any worktree, build or secret
        self.assertFalse(Path('/nonexistent/work').exists())

    def test_it_keeps_the_committed_signing_values_and_has_no_frankel_wrapper_field(self):
        integrate, ours = FRANKEL['host']['integrate'], COMMITTED['host']['integrate']
        for key in ('project', 'secretVersion', 'appSecrets'):
            self.assertEqual(ours[key], integrate[key], key)
        self.assertEqual(COMMITTED['identities'], {'appSignerSha256': FRANKEL['identities']['appSignerSha256']})
        self.assertEqual(COMMITTED['appRelease'], {k: FRANKEL['appRelease'][k] for k in ('packageName', 'minimumTestedOs')})
        self.assertEqual(sorted(COMMITTED['host']), ['integrate', 'tools'])  # no lockPath, sourceRoot or OS commands
        self.assertEqual(sorted(ours), ['appSdkRoot', 'appSecrets', 'javaHome', 'project', 'publicAssetDigests',
                                        'publicAssetsDir', 'secretVersion'])
        # App's portable inputs and the shared ledger are never copied from the builder or guessed.
        for value in (ours['appSdkRoot'], ours['javaHome'], ours['publicAssetsDir'], ours['publicAssetDigests'],
                      COMMITTED['host']['tools']['aapt'], COMMITTED['host']['tools']['apksigner'],
                      COMMITTED['reservation']['store']):
            self.assertIsNone(value)
        self.assertNotIn('/home/pixelbuild', json.dumps(COMMITTED))

    def test_relative_builder_paths_never_count_as_portable(self):
        config = json.loads(json.dumps(COMMITTED))
        config['host']['integrate'].update(appSdkRoot='rose-actions/tools/android-sdk', javaHome='/opt/jdk',
                                           publicAssetsDir='/opt/assets')
        config['host']['tools'].update(aapt='prebuilts/sdk/tools/linux/bin/aapt', apksigner='/opt/sdk/apksigner')
        config['reservation']['store'] = STORE
        self.assertEqual(release.hosted_missing_inputs(config), ['host.integrate.appSdkRoot', 'host.tools.aapt'])


class Hosted(unittest.TestCase):
    setUp_base = base.AppReleaseTest.setUp
    build = base.AppReleaseTest.build
    fetched = base.AppReleaseTest.fetched
    assert_clean = base.AppReleaseTest.assert_clean

    def setUp(self):
        self.setUp_base()
        data = json.loads(json.dumps(self.config_data))
        host = data['host']
        for key in ('lockPath', 'sourceRoot', 'integrateCommand', 'makeCommand', 'signCommand', 'sign'):
            host.pop(key)
        for key in ('reconcileCommand', 'appBuildCommand', 'sdk'):
            host['integrate'].pop(key)
        data['appRelease'].pop('mustExceed')  # a hosted route has no floor to read
        data['reservation'] = {'store': STORE}
        self.hosted_config = self.root/'agent-hosted.json'
        self.hosted_config.write_text(json.dumps(data))
        self.receipt_path = self.root/'reservation.json'
        self.write_reservation()
        self.env.update(GITHUB_WORKFLOW_REF=WORKFLOW_REF, GITHUB_RUN_ID='42000000001', GITHUB_RUN_ATTEMPT='1',
                        ROSE_APP_RESERVATION=str(self.receipt_path))
        self.forbid_wrapper()

    def write_reservation(self, **change):
        record = {'schema': 'roseos.app-version-reservation.v1', 'packageName': 'dev.pixel.voice', 'versionCode': 306,
                  'sourceSha': 'd' * 40, 'candidateSha': self.head, 'allocationSha256': 'a' * 64,
                  'owner': {'workflowRef': WORKFLOW_REF, 'runId': '42000000001', 'runAttempt': '1'}, 'store': STORE}
        record.update(change)
        record['reservationSha256'] = digest(record)
        self.receipt_path.write_text(json.dumps(record))
        return record

    def forbid_wrapper(self):
        """Every Frankel-wrapper step and the lock primitive raise if the hosted route reaches them."""
        def trap(name):
            def refuse(*args, **kwargs):
                raise AssertionError(f'{name} reached on the hosted route')
            return refuse
        originals = {name: getattr(release, name) for name in WRAPPER}
        flock = release.fcntl.flock
        for name in WRAPPER:
            setattr(release, name, trap(name))
        release.fcntl.flock = trap('fcntl.flock')

        def restore():
            for name, value in originals.items():
                setattr(release, name, value)
            release.fcntl.flock = flock
        self.addCleanup(restore)

    def hosted(self):
        return release.hosted(self.artifact, self.hosted_config, self.env, self.gcloud, build=self.build)

    def test_an_owned_reservation_signs_with_the_unchanged_release_and_records_the_reservation(self):
        record = json.loads(self.receipt_path.read_text())
        receipt = self.hosted()
        self.assertEqual((receipt['packageName'], receipt['versionCode'], receipt['signerSha256']), ('dev.pixel.voice', 306, PIN))
        self.assertEqual(receipt['reservation'], {k: record[k] for k in ('reservationSha256', 'allocationSha256',
                                                                          'sourceSha', 'store')})
        self.assertIn(f'versionCode 306 is reservation {record["reservationSha256"]} owned by this run', receipt['checks'])
        self.assertFalse(any('versionCode above' in check for check in receipt['checks']))
        self.assertIn('independent apksigner verification: exactly the pinned signer', receipt['checks'])
        self.assertEqual(receipt, json.loads((self.artifact/'app-release-receipt.json').read_text()))
        self.assertEqual(sorted(p.name for p in self.artifact.iterdir()), ['SHA256SUMS', 'app-release-receipt.json',
                         'dev.pixel.voice-306.apk', 'dev.pixel.voice-306.apk.idsig'])
        self.assertEqual([c['voice_only'] for c in self.calls], [True])
        self.assertFalse(self.lock.exists(), 'no lock file was opened')
        self.assertEqual(self.statted, [], 'no disk reserve check')
        self.assert_clean()  # main()'s cleanup kept: candidate worktree, prune, ROSE_RELEASE_DIR, RAM secrets

    def test_a_reservation_this_run_does_not_own_exactly_is_refused_before_any_worktree_or_secret(self):
        cases = {
            'another run': lambda: self.write_reservation(owner={'workflowRef': WORKFLOW_REF, 'runId': '42000000002', 'runAttempt': '1'}),
            'another attempt': lambda: self.write_reservation(owner={'workflowRef': WORKFLOW_REF, 'runId': '42000000001', 'runAttempt': '2'}),
            'another workflow': lambda: self.write_reservation(owner={'workflowRef': WORKFLOW_REF.replace('os-release', 'agent-beta-auto'),
                                                                     'runId': '42000000001', 'runAttempt': '1'}),
            'another version': lambda: self.write_reservation(versionCode=307),
            'another package': lambda: self.write_reservation(packageName='dev.pixel.calculator.agent'),
            'another candidate': lambda: self.write_reservation(candidateSha='e' * 40),
            'another store': lambda: self.write_reservation(store={'kind': 'elsewhere'}),
            'text version': lambda: self.write_reservation(versionCode='306'),
            'short source': lambda: self.write_reservation(sourceSha='d' * 39),
            'no allocation digest': lambda: self.write_reservation(allocationSha256='x'),
            'tampered': lambda: self.receipt_path.write_text(self.receipt_path.read_text().replace('"versionCode": 306', '"versionCode": 305')),
            'extra field': lambda: self.receipt_path.write_text(json.dumps(dict(json.loads(self.receipt_path.read_text()), note='x'))),
            'other schema': lambda: self.write_reservation(schema='roseos.app-allocation.v1'),
            'a link': lambda: (self.root.joinpath('real.json').write_text(self.receipt_path.read_text()),
                               self.receipt_path.unlink(), self.receipt_path.symlink_to(self.root/'real.json')),
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.write_reservation()
                if self.receipt_path.is_symlink():
                    self.receipt_path.unlink()
                    self.write_reservation()
                change()
                with self.assertRaises(release.AdapterFailed):
                    self.hosted()
                self.assertEqual(self.fetched(), [], 'refused before any secret fetch')
                self.assertFalse(self.artifact.exists())
                self.assertFalse(self.work.exists(), 'refused before any worktree or build')
                self.assertEqual(self.calls, [])
        if self.receipt_path.is_symlink():
            self.receipt_path.unlink()
        self.write_reservation()
        for name, value in (('APPS', json.dumps([['dev.pixel.voice', 307]])), ('GITHUB_RUN_ID', '42000000009'),
                            ('ROSE_APP_RESERVATION', ''), ('GITHUB_WORKFLOW_REF', '')):
            with self.subTest(env=name):
                env = dict(self.env, **{name: value})
                with self.assertRaises(release.AdapterFailed):
                    release.hosted(self.artifact, self.hosted_config, env, self.gcloud, build=self.build)
                self.assertEqual(self.fetched(), [])

    def test_an_unbound_store_or_portable_input_stops_before_the_reservation_is_read(self):
        data = json.loads(self.hosted_config.read_text())
        data['reservation']['store'] = None
        self.hosted_config.write_text(json.dumps(data))
        self.receipt_path.unlink()  # not even read
        with self.assertRaises(release.AdapterSetupMissing) as caught:
            self.hosted()
        self.assertEqual(caught.exception.missing, ['reservation.store'])
        self.assertFalse(self.work.exists())

    def test_a_failed_signing_removes_the_partial_artifact_and_cleans_up(self):
        self.signer_cert.write_text(OTHER)  # sign-apk.py's own pin check fails
        with self.assertRaises(release.AdapterFailed):
            self.hosted()
        self.assertFalse(self.artifact.exists())
        self.assert_clean()

    def test_a_wrong_unsigned_identity_refuses_before_any_secret_and_cleans_up(self):
        self.built = 'dev.pixel.voice:307'
        with self.assertRaisesRegex(release.AdapterFailed, 'expected dev.pixel.voice 306'):
            self.hosted()
        self.assertEqual(self.fetched(), [])
        self.assertFalse(self.artifact.exists())
        self.assert_clean()

    def test_an_existing_or_nested_artifact_directory_is_refused(self):
        self.artifact.mkdir(parents=True)
        with self.assertRaisesRegex(release.AdapterFailed, 'already exists'):
            self.hosted()
        self.artifact.rmdir()
        nested = self.work/'artifact'
        with self.assertRaisesRegex(release.AdapterFailed, 'outside ROSE_RELEASE_DIR'):
            release.hosted(nested, self.hosted_config, self.env, self.gcloud, build=self.build)
        self.assertEqual(self.fetched(), [])


class BuilderRoute(unittest.TestCase):
    """The builder route keeps its mustExceed basis and records no reservation."""
    setUp = base.AppReleaseTest.setUp
    build = base.AppReleaseTest.build
    statvfs = base.AppReleaseTest.statvfs
    run_release = base.AppReleaseTest.run_release

    def test_main_keeps_its_floor_basis(self):
        receipt = self.run_release()
        self.assertIn('versionCode above 302 (newest built or published)', receipt['checks'])
        self.assertNotIn('reservation', receipt)


if __name__ == '__main__':
    unittest.main()
