#!/usr/bin/env python3
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mustang_tool_closure as closure

_spec = importlib.util.spec_from_file_location('host_sign_for_closure', Path(__file__).resolve().parents[1] / 'host-sign.py')
host_sign = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(host_sign)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / 'scripts/tests/fixtures/mustang-jdk-pack/tree'
FIXTURE_SHA = '8a88df24adfb652c11ab62fc5c9bc7098f27d33c1dc2cf7a0183e1226f2bb55f'


class ToolClosureTest(unittest.TestCase):
    def test_checkout_matches_the_receipt_and_production_pins_stay_null(self):
        receipt = closure.verify_closure(ROOT)
        self.assertEqual(receipt['files'][1]['sha256'],
                         '01f7847ecdc6600bd2e3ffebe45cf79cee4fead1d6b61686c4d3c70e9c548311')
        self.assertIsNone(receipt['otatools']['sha256'])
        self.assertEqual(receipt['otatools']['qualificationEvidence'],'os/build-only/mustang-otatools-42531345133.json')
        self.assertFalse(receipt['jdk']['committed'])
        pin = json.loads((ROOT / closure.PIN).read_text())
        for key in closure.PIN_KEYS:
            self.assertIsNone(pin[key])
        capacity = json.loads((ROOT / closure.CAPACITY).read_text())
        self.assertFalse(capacity['admission'])
        self.assertEqual(capacity['trust'], 'held')
        self.assertTrue(all(value is None for value in capacity['outputBounds'].values()))
        self.assertFalse(any(capacity['proposedRunner'][key] for key in
                             ('purchased', 'configured', 'selected', 'approved', 'sizingProof')))
        self.assertFalse((ROOT / 'script/decrypt-keys').exists())
        self.assertFalse((ROOT / 'device/common/generate-factory-images-common.sh').exists())

    def test_a_changed_helper_or_filled_pin_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            self.copy_checkout(base)
            target = base / 'script/common.sh'
            target.write_bytes(target.read_bytes() + b'\n')
            with self.assertRaisesRegex(closure.ClosureError, 'does not match the provenance receipt'):
                closure.verify_closure(base)
            self.copy_checkout(base)
            helper = base / 'script/generate-metadata'
            helper.unlink()
            helper.symlink_to('common.sh')
            with self.assertRaisesRegex(closure.ClosureError, 'symlink'):
                closure.verify_closure(base)
            self.copy_checkout(base)
            pin = json.loads((base / closure.PIN).read_text())
            pin['jdkSha256'] = 'a' * 64
            (base / closure.PIN).write_text(json.dumps(pin))
            with self.assertRaisesRegex(closure.ClosureError, 'production pin jdkSha256 is filled'):
                closure.verify_closure(base)
            self.copy_checkout(base)
            capacity = json.loads((base / closure.CAPACITY).read_text())
            capacity['admission'] = True
            (base / closure.CAPACITY).write_text(json.dumps(capacity))
            with self.assertRaisesRegex(closure.ClosureError, 'admission is not held'):
                closure.verify_closure(base)

    def test_qualification_is_provenance_and_cannot_silently_fill_an_executable_pin(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);self.copy_checkout(base)
            receipt=json.loads((base/closure.RECEIPT).read_text())
            receipt['otatools']['executable']='bin/ota_from_target_files'
            (base/closure.RECEIPT).write_text(json.dumps(receipt))
            with self.assertRaisesRegex(closure.ClosureError,'otatools receipt was filled'):
                closure.verify_closure(base)
            self.copy_checkout(base)
            evidence=base/'os/build-only/mustang-otatools-42531345133.json'
            value=json.loads(evidence.read_text());value['independentReadback']['receipt']['byteReadbackMatches']=False
            evidence.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'independent same-run'):
                closure.verify_closure(base)

    def test_release_script_closure_is_only_the_imported_helpers(self):
        text = (ROOT / 'script/generate-release.sh').read_text()
        closure.executed_closure(text)
        extra = text.replace('source device/common/clear-factory-images-variables.sh',
                             'source device/common/clear-factory-images-variables.sh\nsource device/common/extra.sh', 1)
        with self.assertRaisesRegex(closure.ClosureError, 'outside the reviewed closure'):
            closure.executed_closure(extra)

    def test_fixture_pack_is_deterministic_and_resolves_the_in_tree_link(self):
        first = closure.pack_tree(FIXTURE)
        os.utime(FIXTURE / 'bin/java', (10, 20))
        second = closure.pack_tree(FIXTURE)
        self.assertEqual(first, second)
        self.assertEqual(hashlib.sha256(first).hexdigest(), FIXTURE_SHA)
        java = closure.archive_member(first, 'bin/java')
        linked = closure.archive_member(first, 'legal/java.compiler/LICENSE')
        target = closure.archive_member(first, 'legal/java.base/LICENSE')
        self.assertEqual(java['mode'], '0755')
        self.assertEqual(linked, target)
        closure.verify_archive_members(first)
        self.assertEqual(java['sha256'], hashlib.sha256((FIXTURE / 'bin/java').read_bytes()).hexdigest())

    def test_symlink_escape_cycle_and_privileged_mode_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            tree = base / 'tree'
            (tree / 'bin').mkdir(parents=True)
            (tree / 'bin/java').write_bytes(b'java')
            (tree / 'bin/java').chmod(0o755)
            outside = base / 'outside'
            outside.write_bytes(b'secret')
            (tree / 'bin/escaped').symlink_to('../../outside')
            with self.assertRaisesRegex(closure.ClosureError, 'symlink escape'):
                closure.pack_tree(tree)
            (tree / 'bin/escaped').unlink()
            (tree / 'bin/absolute').symlink_to(outside)
            with self.assertRaisesRegex(closure.ClosureError, 'symlink escape'):
                closure.pack_tree(tree)
            (tree / 'bin/absolute').unlink()
            (tree / 'loop').symlink_to('loop')
            with self.assertRaisesRegex(closure.ClosureError, 'symlink cycle'):
                closure.pack_tree(tree)
            (tree / 'loop').unlink()
            (tree / 'bin/java').chmod(0o755 | stat.S_ISUID)
            with self.assertRaisesRegex(closure.ClosureError, 'privileged mode'):
                closure.pack_tree(tree)

    def test_directory_alias_parent_is_not_collapsed_and_intermediate_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            tree = base / 'tree'
            (tree / 'x' / 'y').mkdir(parents=True)
            (tree / 'x' / 'file').write_bytes(b'after-alias')
            (tree / 'file').write_bytes(b'lexical-wrong')
            (tree / 'a').symlink_to('x/y')
            (tree / 'link').symlink_to('a/../file')
            packed = closure.pack_tree(tree)
            member = closure.archive_member(packed, 'link')
            self.assertEqual(member['sha256'], hashlib.sha256(b'after-alias').hexdigest())
            self.assertNotEqual(member['sha256'], hashlib.sha256(b'lexical-wrong').hexdigest())
            self.assertNotIn(b'lexical-wrong', packed)
            (tree / 'inside').write_bytes(b'inside')
            outside = base / 'outside'
            outside.mkdir()
            (outside / 'secret').write_bytes(b'secret-bytes')
            (tree / 'alias').symlink_to('../outside')
            (tree / 'returned').symlink_to('alias/../inside')
            with self.assertRaisesRegex(closure.ClosureError, 'symlink escape'):
                closure.follow_symlink(tree, tree / 'returned')
            with self.assertRaisesRegex(closure.ClosureError, 'symlink escape'):
                closure.pack_tree(tree)

    def test_derived_helpers_execute_the_checkout_after_the_otatools_directory_change(self):
        original = (ROOT / 'script/generate-release.sh').read_text()
        self.assertLess(original.index('cd $RELEASE_OUT'), original.index('source device/common/clear-factory-images-variables.sh'))
        self.assertLess(original.index('cd $RELEASE_OUT'), original.index('script/generate-metadata'))
        self.assertEqual((ROOT / 'script/generate-metadata').stat().st_mode & 0o111, 0)
        derived = host_sign.derive_release_script(
            original, str(ROOT / 'script/common.sh'), '/dev/shm/k', '/dev/shm/p', '/tmp/release-keys', '/tmp/wrappers')
        source_line = next(line for line in derived.splitlines() if 'clear-factory-images-variables.sh' in line)
        metadata_line = next(line for line in derived.splitlines() if 'generate-metadata' in line)
        self.assertEqual(source_line, 'source ' + shlex.quote(str(ROOT / 'device/common/clear-factory-images-variables.sh')))
        self.assertEqual(metadata_line, shlex.quote(sys.executable) + ' ' + shlex.quote(str(ROOT / 'script/generate-metadata'))
                         + ' $DEVICE-ota_update-$BUILD_NUMBER.zip')
        self.assertLess(derived.index('cd $RELEASE_OUT'), derived.index(source_line))
        self.assertNotRegex(derived, r'(?m)^source device/common/clear-factory-images-variables\.sh$')
        self.assertNotRegex(derived, r'(?m)^script/generate-metadata\b')
        for line in ('BUILD=$BUILD_NUMBER', 'VERSION=$BUILD_NUMBER', 'DEVICE=$1'):
            self.assertIn(line, derived)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('META-INF/com/android/metadata',
                            'pre-device=mustang\npost-build=a/b/c/d/999:user/release-keys\npost-timestamp=1\n')
        payload = archive.getvalue()
        script = '\n'.join(['set -euo pipefail', 'AVB_PKMD=payload-value', 'BUILD_NUMBER=2026110701',
                            source_line, 'BUILD=$BUILD_NUMBER', 'VERSION=$BUILD_NUMBER', 'DEVICE=$1',
                            metadata_line, 'if [[ -n ${AVB_PKMD+x} ]]; then echo still-set >&2; exit 4; fi', ''])
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            hostile = base / 'hostile'
            (hostile / 'device/common').mkdir(parents=True)
            (hostile / 'script').mkdir()
            (hostile / 'device/common/clear-factory-images-variables.sh').write_text('echo HOSTILE > hostile-clear\nexit 9\n')
            (hostile / 'script/generate-metadata').write_text('#!/bin/sh\necho HOSTILE > hostile-meta\nexit 9\n')
            (hostile / 'script/generate-metadata').chmod(0o755)
            absent = base / 'absent'
            absent.mkdir()
            elsewhere = base / 'elsewhere'
            elsewhere.mkdir()
            for cwd in (hostile, absent, elsewhere):
                (cwd / 'mustang-ota_update-2026110701.zip').write_bytes(payload)
                result = subprocess.run(['bash', '-c', script, 'release', 'mustang', '2026110701'],
                                        cwd=cwd, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((cwd / 'hostile-clear').exists())
                self.assertFalse((cwd / 'hostile-meta').exists())
                self.assertEqual((cwd / 'mustang-beta').read_text(), '999 1 mustang beta\n')
                self.assertNotIn('HOSTILE', result.stdout)
                self.assertNotIn('HOSTILE', result.stderr)

    def copy_checkout(self, base):
        if base.exists():
            shutil.rmtree(base)
        for relative in (closure.RECEIPT, closure.PIN, closure.CAPACITY,
                         'os/build-only/mustang-otatools-42531345133.json','os/build-only/mustang-lane.json',
                         'os/build-only/mustang-otatools-inspection-42531345133.json',
                         'script/LICENSE', 'script/generate-release.sh', 'script/common.sh',
                         'script/generate-metadata', 'device/common/clear-factory-images-variables.sh'):
            source = ROOT / relative
            target = base / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


if __name__ == '__main__':
    unittest.main()
