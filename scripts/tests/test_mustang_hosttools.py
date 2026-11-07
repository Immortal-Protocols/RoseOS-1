"""Host-tools contract refusals. No VM, Make, download, or pin write."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts')]
import mustang_hosttools as host
import mustang_route as route


def sha(name):
    return format(abs(hash(name)) % (16 ** 16), '016x').ljust(64, 'a')[:64]


class HosttoolsContractTest(unittest.TestCase):
    def current(self):
        return host.decide(host.current_observation())

    def filled(self, **change):
        obs = host.current_observation()
        obs.update(outDir='out-mustang-hosttools-42409634329', outExists=False, outEmpty=True,
                   priorProductOutputs=False, factoryOnDedicatedDisk=True, factoryExtracted=True,
                   factoryPath='/mnt/disks/mustang-data/roseos/factory/mustang-cp3a.261005.005-factory-099acc44.zip',
                   vendorGenerated=True, startAcknowledged=True,
                   generated=[{'path': 'vendor/google_devices/mustang/device.mk', 'sha256': sha('device')}],
                   dependencies=[{'name': name, 'sha256': sha(name)} for name in host.deps_required()])
        obs.update(change)
        return obs

    def test_current_records_keep_the_compile_gap_and_the_pins(self):
        decision = self.current()
        self.assertFalse(decision['execute'])
        self.assertFalse(decision['startAuthorized'])
        self.assertFalse(decision['readyForControlledStart'])
        self.assertIsNone(decision['argv'])
        self.assertEqual(decision['target'], 'otatools-package')
        self.assertIn('START', decision['lifecycle'])
        self.assertIn('stopped', decision['lifecycle'])
        for code in ('factory-not-on-dedicated-disk', 'factory-not-extracted', 'vendor-not-generated',
                     'mustang-product-config-absent', 'host-dependency-closure-absent', 'out-dir-unspecified'):
            self.assertIn(code, decision['problems'])
        for absent in ('wrong-vm', 'wrong-disk', 'wrong-tree', 'source-changed', 'symlink',
                       'held-otatools-substitute', 'circular-pin', 'production-pin-changed', 'start-not-acknowledged'):
            self.assertNotIn(absent, decision['problems'])
        pins = json.loads((ROOT/'os/build-only/mustang-hosted-sign-script.json').read_text())
        for name in ('generateReleaseSha256', 'jdkSha256', 'otatoolsSha256', 'otatoolsExecutable'):
            self.assertIsNone(pins[name])
        runtime = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
        self.assertEqual(runtime['status'], 'unbound')
        self.assertFalse(runtime['publication']['enabled'])
        self.assertTrue(route.accepted_source_sync(
            json.loads((ROOT/route.ACCEPTED_SOURCE_SYNC['path']).read_text()), route.ACCEPTED_SOURCE_SYNC['path']))

    def test_identity_tree_symlink_output_and_target_refusals(self):
        cases = (
            ({'instanceId': '1'}, 'wrong-vm'),
            ({'diskUuid': 'other'}, 'wrong-disk'),
            ({'sourceRoot': '/home/pixelbuild/grapheneos-2026100600'}, 'wrong-tree'),
            ({'manifestSha256': 'b'*64}, 'source-changed'),
            ({'symlinks': ['source']}, 'symlink'),
            ({'symlinks': ['out']}, 'symlink'),
            ({'outExists': True}, 'reused-out-dir'),
            ({'priorProductOutputs': True}, 'reused-out-dir'),
            ({'outDir': 'out-mustang-build-2026110302'}, 'out-dir-unspecified'),
            ({'targets': ['otatools-package', 'target-files-package']}, 'untrusted-target'),
            ({'targets': ['droid']}, 'untrusted-target'),
            ({'argv': ['m', 'vendorbootimage']}, 'untrusted-target'),
            ({'argv': ['bash', '-c', 'sign publish /keys']}, 'forbidden-path'),
            ({'jobs': 48}, 'bounds'),
            ({'timeoutSeconds': 28800}, 'bounds'),
            ({'integration': {'paths': ['vendor/google_devices/mustang/device.mk']}}, 'unrecorded-mutation'),
            ({'otatoolsSha256': '4e13f55b' + 'ab'*28}, 'held-otatools-substitute'),
            ({'otatoolsSha256': 'f8fc065e87083a51f64c74be57f4f06611721645273b9ae45081148203cebc22'}, 'held-otatools-substitute'),
            ({'requireExistingOtatoolsPin': True}, 'circular-pin'),
            ({'generated': []}, 'mustang-product-config-absent'),
            ({'dependencies': []}, 'host-dependency-closure-absent'),
            ({'factoryOnDedicatedDisk': False}, 'factory-not-on-dedicated-disk'),
            ({'factoryPath': '/workspace/mustang-ota/source-inputs/factory.zip'}, 'factory-not-on-dedicated-disk'),
            ({'factoryExtracted': False}, 'factory-not-extracted'),
        )
        for change, code in cases:
            with self.subTest(code=code):
                decision = host.decide(self.filled(**change))
                self.assertIn(code, decision['problems'])
                self.assertFalse(decision['readyForControlledStart'])
                self.assertFalse(decision['execute'])
                self.assertIsNone(decision['argv'])

    def test_complete_proof_still_does_not_start_or_pin(self):
        decision = host.decide(self.filled())
        self.assertEqual(decision['problems'], [])
        self.assertTrue(decision['readyForControlledStart'])
        self.assertFalse(decision['execute'])
        self.assertFalse(decision['startAuthorized'])
        argv = ' '.join(decision['argv']['argv'])
        self.assertIn('m -j4 otatools-package', argv)
        self.assertIn('lunch mustang-cur-user', argv)
        for goal in host.PRODUCT_GOALS:
            self.assertNotIn(goal, argv)
        self.assertEqual(decision['argv']['env']['OUT_DIR'], 'out-mustang-hosttools-42409634329')
        self.assertIn('delete only this OUT_DIR', decision['argv']['cleanup'])
        self.assertIn('START', decision['lifecycle'])
        pins = json.loads((ROOT/'os/build-only/mustang-hosted-sign-script.json').read_text())
        self.assertIsNone(pins['otatoolsSha256'])
        self.assertIsNone(pins['otatoolsExecutable'])

    def test_cli_reports_the_gap_and_does_not_execute(self):
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()) as out:
            code = host.main([])
        self.assertEqual(code, 4)
        self.assertIn('factory-not-on-dedicated-disk', out.getvalue())
        self.assertIn('"execute": false', out.getvalue())


if __name__ == '__main__':
    unittest.main()
