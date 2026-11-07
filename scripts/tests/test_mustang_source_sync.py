"""The source-sync recipe is pinned and does not run or authorize a build."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts')]
import mustang_source_sync as sync


class SourceSyncRecipeTest(unittest.TestCase):
    def test_committed_recipe_matches_the_pin_and_does_not_run(self):
        record = sync.load()
        self.assertFalse(record['syncAuthorized'])
        self.assertFalse(record['compileAuthorized'])
        self.assertFalse(record['signAuthorized'])
        self.assertFalse(record['runtimeBound'])
        self.assertEqual(record['roseRevision'], '2ac24c5475de121ebe4e9e6a5c1a404ef44aa9c8')
        self.assertEqual(record['sourceRoot'], '/mnt/disks/mustang-data/roseos/source')
        self.assertEqual(record['factory']['sha256'], '099acc44e2f87fe2735d92952081d64550ff08b4a6390bb6b001c8451695657e')
        self.assertFalse(record['factory']['extracted'])
        self.assertIn('immortal-os-builder', record['forbiddenHosts'])
        with self.assertRaisesRegex(ValueError, 'not authorized'):
            sync.execute(record)
        self.assertEqual(sync.main(), 4)

    def test_admitted_copy_returns_commands_without_executing(self):
        admitted = json.loads(json.dumps(sync.load()))
        admitted['syncAuthorized'] = True
        got = sync.execute(admitted)
        self.assertFalse(got['executed'])
        self.assertIn('repo sync -c -j4 --fail-fast', got['commands'])
        self.assertTrue(all('immortal-os-builder' not in command for command in got['commands']))

    def test_compile_or_other_host_is_rejected(self):
        record = json.loads(json.dumps(sync.load()))
        record['compileAuthorized'] = True
        with self.assertRaisesRegex(ValueError, 'cannot authorize'):
            sync.validate(record)
        moved = json.loads(json.dumps(sync.load()))
        moved['sourceRoot'] = '/home/pixelbuild/grapheneos-2026100600'
        with self.assertRaisesRegex(ValueError, 'Mustang data disk'):
            sync.validate(moved)
        other = json.loads(json.dumps(sync.load()))
        other['commands'] = list(other['commands']) + ['cd /home/pixelbuild && repo sync']
        with self.assertRaisesRegex(ValueError, 'leave the checkout'):
            sync.validate(other)


if __name__ == '__main__':
    unittest.main()
