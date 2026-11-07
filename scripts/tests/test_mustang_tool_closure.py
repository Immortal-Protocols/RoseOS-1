#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mustang_tool_closure as closure

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / 'scripts/tests/fixtures/mustang-jdk-pack/tree'
FIXTURE_SHA = '8a88df24adfb652c11ab62fc5c9bc7098f27d33c1dc2cf7a0183e1226f2bb55f'


class ToolClosureTest(unittest.TestCase):
    def test_checkout_matches_the_receipt_and_production_pins_stay_null(self):
        receipt = closure.verify_closure(ROOT)
        self.assertEqual(receipt['files'][1]['sha256'],
                         '01f7847ecdc6600bd2e3ffebe45cf79cee4fead1d6b61686c4d3c70e9c548311')
        self.assertIsNone(receipt['otatools']['sha256'])
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

    def copy_checkout(self, base):
        if base.exists():
            shutil.rmtree(base)
        for relative in (closure.RECEIPT, closure.PIN, closure.CAPACITY,
                         'script/LICENSE', 'script/generate-release.sh', 'script/common.sh',
                         'script/generate-metadata', 'device/common/clear-factory-images-variables.sh'):
            source = ROOT / relative
            target = base / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


if __name__ == '__main__':
    unittest.main()
