"""Declarations are not a host-tools proof. Readers measure fixtures only."""
import json
import os
import sys
import tempfile
import unittest
import hashlib
import tracemalloc
from unittest.mock import patch
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
        import mustang_tool_closure
        for name, value in mustang_tool_closure.qualified_pins(ROOT).items():
            self.assertEqual(pins[name], value)
        self.assertFalse(json.loads((ROOT/'os/build-only/mustang-hosted-signer-capacity.json').read_text())['admission'])
        runtime = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
        self.assertEqual(runtime['status'], 'unbound')
        self.assertTrue(runtime['publication']['enabled'])
        self.assertEqual(runtime['jobs'], 24)
        self.assertTrue(all(Path(value).is_absolute() for value in runtime['tools'].values()))
        self.assertEqual(runtime['runner']['lifetime'], 'ephemeral')
        self.assertIsNone(runtime['runner']['registrationEvidence'])
        self.assertEqual(runtime['runner']['groupId'], '5')
        self.assertEqual(runtime['automaticStopHours'], 4)
        self.assertTrue(route.accepted_source_sync(
            json.loads((ROOT/route.ACCEPTED_SOURCE_SYNC['path']).read_text()), route.ACCEPTED_SOURCE_SYNC['path']))

    def test_declarations_cannot_clear_the_proposal(self):
        decision = host.decide()
        self.assertFalse(decision['readyForControlledStart'])
        self.assertIsNone(decision['argv'])
        self.assertNotIn('m ', json.dumps(decision))


class ReaderTest(unittest.TestCase):
    def test_factory_hash_streams_with_bounded_memory_and_still_checks_the_digest(self):
        size = 12 << 20
        chunk = b'x'*(1 << 20)
        digest = hashlib.sha256()
        with tempfile.TemporaryDirectory() as tmp:
            mount = Path(tmp)
            archive = mount/'factory.zip'
            with archive.open('wb') as out:
                for _ in range(12):
                    out.write(chunk)
                    digest.update(chunk)
            pin = {'factory': {'sizeBytes': size, 'sha256': digest.hexdigest()}}
            with patch.object(host, 'recipe', return_value=pin):
                tracemalloc.start()
                try:
                    measured = host.measure_factory(mount, archive)
                    _current, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                self.assertTrue(measured['matchesPinnedFactory'])
                self.assertFalse(measured['admitsOtatools'])
                self.assertLess(peak, 4 << 20)
                pin['factory']['sha256'] = '0'*64
                self.assertIn('factory-hash-differs', host.measure_factory(mount, archive)['problems'])

    def test_factory_cannot_match_when_bytes_change_after_their_read(self):
        for mutation in ('append', 'already-read-prefix'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                mount = Path(tmp)
                archive = mount/'factory.zip'
                data = b'x'*(2 << 20)
                archive.write_bytes(data)
                pin = {'factory': {'sizeBytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}}
                original_read = os.read
                changed = []
                def read_then_change(fd, count):
                    block = original_read(fd, count)
                    if not changed:
                        changed.append(True)
                        if mutation == 'append':
                            with archive.open('ab') as out:
                                out.write(b'y')
                        else:
                            with archive.open('r+b') as out:
                                out.write(b'y')
                    return block
                with patch.object(host, 'recipe', return_value=pin), patch.object(host.os, 'read', side_effect=read_then_change):
                    measured = host.measure_factory(mount, archive)
                self.assertFalse(measured['matchesPinnedFactory'])
                self.assertFalse(measured['admitsOtatools'])

    def bounded(self, call):
        import signal
        def handler(signum, frame):
            raise TimeoutError('read blocked')
        previous = signal.signal(signal.SIGALRM, handler)
        signal.alarm(2)
        try:
            return call()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
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
            outside = Path(tmp)/'outside'
            (outside/'sub').mkdir(parents=True)
            (outside/'factory.zip').write_bytes(b'OUTSIDE')
            (mount/'a').symlink_to(outside/'sub')
            (mount/'link').symlink_to(outside)
            traversed = host.measure_factory(mount, str(mount) + '/a/../factory.zip')
            self.assertFalse(traversed['matchesPinnedFactory'])
            self.assertIn('factory-not-contained', traversed['problems'])
            self.assertNotIn('factory-size-differs', traversed['problems'])
            followed = host.measure_factory(mount, str(mount) + '/link/factory.zip')
            self.assertFalse(followed['matchesPinnedFactory'])
            self.assertNotIn('factory-size-differs', followed['problems'])
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
            opened = host.measure_deps(source)
            self.assertEqual(opened['problems'], [])
            self.assertFalse(opened['provenance'])
            self.assertFalse(opened['admitsOtatools'])
            (bindir/names[0]).unlink()
            (bindir/names[0]).symlink_to(bindir/names[1])
            self.assertIn('adevtool-host-tools-unmeasured', host.measure_deps(source)['problems'])
            (deps/'adevtool_revision').unlink()
            os.mkfifo(deps/'adevtool_revision')
            self.assertIn('adevtool-revision-differs', self.bounded(lambda: host.measure_deps(source))['problems'])
            (deps/'adevtool_revision').unlink()
            outside = Path(tmp)/'outside-deps'
            outside.mkdir()
            (outside/'adevtool_revision').write_text(PIN + '\n')
            (source/'out_adevtool_deps').rename(source/'deps-real')
            (source/'out_adevtool_deps').symlink_to(outside)
            linked = self.bounded(lambda: host.measure_deps(source))
            self.assertIn('adevtool-revision-differs', linked['problems'])
            self.assertFalse(linked['provenance'])
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
            self.assertTrue(measured['includeSeen'])
            self.assertFalse(measured['provenance'])
            self.assertFalse(measured['admitsVendorTree'])
            self.assertFalse(measured['admitsOtatools'])
            product.unlink()
            product.symlink_to(note)
            self.assertFalse(host.measure_vendor(source)['includeSeen'])
            outside = Path(tmp)/'outside-vendor'/'google_devices'/'mustang'
            outside.mkdir(parents=True)
            (outside/'mustang.mk').write_bytes(INCLUDE + b'\n')
            import shutil
            shutil.rmtree(source/'vendor')
            (source/'vendor').symlink_to(Path(tmp)/'outside-vendor')
            stolen = host.measure_vendor(source)
            self.assertFalse(stolen['includeSeen'])
            self.assertFalse(stolen['provenance'])
            self.assertIn('mustang-product-config-absent', stolen['problems'])
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
