"""Declarations are not a host-tools proof. Readers measure fixtures only."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts')]
import mustang_hosttools as host
import mustang_route as route

PIN = '17df3b79a73e67ad68387b35c7d41c85e1d8103c'
INCLUDE = b'include vendor/google_devices/mustang/adevtool-version-check.mk\n'


class ProposalTest(unittest.TestCase):
    def test_committed_records_are_not_ready_and_emit_no_make(self):
        decision = host.decide()
        self.assertEqual(decision['kind'], 'non-executing-proposal')
        self.assertFalse(decision['readyForControlledStart'])
        self.assertFalse(decision['execute'])
        self.assertFalse(decision['startAuthorized'])
        self.assertIsNone(decision['argv'])
        self.assertIn('START', decision['lifecycle'])
        self.assertIn('stopped', decision['lifecycle'])
        for code in ('factory-not-on-dedicated-disk', 'factory-not-extracted', 'vendor-not-generated',
                     'mustang-product-config-absent', 'host-dependency-closure-absent', 'out-dir-unspecified'):
            self.assertIn(code, decision['problems'])
        pins = json.loads((ROOT/'os/build-only/mustang-hosted-sign-script.json').read_text())
        for name in ('generateReleaseSha256', 'jdkSha256', 'otatoolsSha256', 'otatoolsExecutable'):
            self.assertIsNone(pins[name])
        runtime = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
        self.assertEqual(runtime['status'], 'unbound')
        self.assertFalse(runtime['publication']['enabled'])
        self.assertTrue(route.accepted_source_sync(
            json.loads((ROOT/route.ACCEPTED_SOURCE_SYNC['path']).read_text()), route.ACCEPTED_SOURCE_SYNC['path']))

    def test_declarations_cannot_clear_the_proposal(self):
        decision = host.decide()
        self.assertFalse(decision['readyForControlledStart'])
        self.assertIsNone(decision['argv'])
        self.assertNotIn('m ', json.dumps(decision))


class ReaderTest(unittest.TestCase):
    def test_factory_reader_rejects_escape_symlink_and_wrong_bytes(self):
        pin = host.recipe()['factory']
        with tempfile.TemporaryDirectory() as tmp:
            mount = Path(tmp)/'disk'
            mount.mkdir()
            outside = Path(tmp)/'factory.zip'
            outside.write_bytes(b'not-the-factory')
            escaped = host.measure_factory(mount, outside)
            self.assertIn('factory-not-contained', escaped['problems'])
            self.assertFalse(escaped['matchesPinnedFactory'])
            self.assertFalse(escaped['admitsOtatools'])
            workspace = Path('/workspace/factory.zip')
            self.assertIn('factory-not-contained', host.measure_factory(mount, workspace)['problems'])
            inside = mount/'roseos'/'factory.zip'
            inside.parent.mkdir()
            inside.write_bytes(b'short')
            short = host.measure_factory(mount, inside)
            self.assertIn('factory-size-differs', short['problems'])
            self.assertFalse(short['matchesPinnedFactory'])
            self.assertNotEqual(inside.stat().st_size, pin['sizeBytes'])
            link = mount/'roseos'/'linked.zip'
            link.symlink_to(inside)
            self.assertIn('factory-not-contained', host.measure_factory(mount, link)['problems'])
        self.assertFalse(host.decide()['readyForControlledStart'])
        self.assertIsNone(host.decide()['argv'])

    def test_deps_reader_requires_the_pin_marker_and_regular_executables(self):
        names = host.recipe()['adevtool']['depsTools']
        self.assertEqual(len(names), 14)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            self.assertIn('adevtool-revision-differs', host.measure_deps(source)['problems'])
            deps = source/'out_adevtool_deps'
            bindir = deps/'host/linux-x86/bin'
            bindir.mkdir(parents=True)
            (deps/'adevtool_revision').write_text('0'*40 + '\n')
            self.assertIn('adevtool-revision-differs', host.measure_deps(source)['problems'])
            (deps/'adevtool_revision').write_text(PIN + '\n')
            for name in names:
                (bindir/name).write_text('x')
            unmeasured = host.measure_deps(source)
            self.assertIn('adevtool-host-tools-unmeasured', unmeasured['problems'])
            self.assertFalse(unmeasured['measured'])
            for name in names:
                os.chmod(bindir/name, 0o755)
            (bindir/names[0]).unlink()
            (bindir/names[0]).symlink_to(bindir/names[1])
            self.assertIn('adevtool-host-tools-unmeasured', host.measure_deps(source)['problems'])
            (bindir/names[0]).unlink()
            (bindir/names[0]).write_text('tool')
            os.chmod(bindir/names[0], 0o755)
            measured = host.measure_deps(source)
            self.assertEqual(measured['problems'], [])
            self.assertTrue(measured['measured'])
            self.assertFalse(measured['admitsOtatools'])
            self.assertEqual((deps/'adevtool_revision').read_bytes(), (PIN + '\n').encode())
        self.assertFalse(host.decide()['readyForControlledStart'])

    def test_vendor_reader_reads_the_product_makefile_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            self.assertIn('mustang-product-config-absent', host.measure_vendor(source)['problems'])
            note = source/'vendor/google_devices/mustang/note.txt'
            note.parent.mkdir(parents=True)
            note.write_text('sha ' + ('ab'*32))
            self.assertIn('mustang-product-config-absent', host.measure_vendor(source)['problems'])
            product = source/'vendor/google_devices/mustang/mustang.mk'
            product.write_text('PRODUCT_NAME := mustang\n')
            self.assertIn('mustang-product-config-absent', host.measure_vendor(source)['problems'])
            product.write_bytes(b'# generated\n' + INCLUDE + b'\n')
            measured = host.measure_vendor(source)
            self.assertEqual(measured['problems'], [])
            self.assertTrue(measured['measured'])
            self.assertFalse(measured['admitsVendorTree'])
            self.assertFalse(measured['admitsOtatools'])
            product.unlink()
            product.symlink_to(note)
            self.assertIn('mustang-product-config-absent', host.measure_vendor(source)['problems'])
        self.assertIsNone(host.decide()['argv'])

    def test_cli_stays_a_refusal(self):
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()) as out:
            code = host.main([])
        body = out.getvalue()
        self.assertEqual(code, 4)
        self.assertIn('"readyForControlledStart": false', body)
        self.assertIn('"argv": null', body)
        self.assertNotIn('lunch mustang-cur-user', body)


if __name__ == '__main__':
    unittest.main()
