"""The retired Mustang epoch recommends a range and does not allocate one."""
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts')]
import mustang_epoch as epoch


class EpochTest(unittest.TestCase):
    def proposal(self, build='2026130001', **change):
        value = {'previous': epoch.genesis(), 'build': build, 'epoch': epoch.EPOCH,
                 'timestamp': 1793755348, 'rollbackIndex': 2026100500,
                 'securityPatchLevel': '2026-10-05', 'observed': []}
        value.update(change)
        return value

    def test_range_is_ten_digits_above_evidence_and_not_a_legacy_date(self):
        described = epoch.describe()
        self.assertEqual(described['recommendedNext'], '2026130001')
        self.assertFalse(described['issued'])
        self.assertFalse(described['selectsBuildNumber'])
        self.assertIsNone(described['consumedBuilds'])
        self.assertFalse(described['quarantine']['consumed'])
        self.assertGreater(int(described['rangeStart']), 2026110501)
        self.assertFalse(epoch.legacy_date('2026130001'))
        self.assertTrue(epoch.legacy_date('2026110402'))
        for bad, message in (
                ('2026110502', 'legacy date allocator'),
                ('2026110402', 'quarantined'),
                ('2026110401', 'collides'),
                ('9999123200', 'outside'),
                ('123', 'ten digits')):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, message):
                    epoch.in_range(bad)
        self.assertLessEqual(epoch.RANGE_END, epoch.INT32_MAX)
        self.assertNotRegex(Path(epoch.__file__).read_text(), r'next_build\(')

    def test_timestamp_and_rollback_stay_separate_from_the_build(self):
        epoch.check_candidate('2026130001', 1793755348, 2026100500, '2026-10-05')
        for change, message in (
                ({'timestamp': 1793755347}, 'published'),
                ({'timestamp': True}, 'int64'),
                ({'rollbackIndex': 2026130001}, 'must not be the build number'),
                ({'rollbackIndex': True}, 'int64'),
                ({'securityPatchLevel': '2026-10-04'}, 'older'),
                ({'observed': ['2026130001']}, 'collides')):
            with self.subTest(change=change):
                fields = dict(build='2026130001', timestamp=1793755348, rollback_index=2026100500,
                              security_patch='2026-10-05', observed=())
                fields.update(change if 'observed' in change or 'timestamp' in change or 'rollbackIndex' in change
                              or 'securityPatchLevel' in change else {})
                kwargs = {'build': '2026130001', 'timestamp': 1793755348, 'rollback_index': 2026100500,
                          'security_patch': '2026-10-05', 'observed': ()}
                if 'timestamp' in change:
                    kwargs['timestamp'] = change['timestamp']
                if 'rollbackIndex' in change:
                    kwargs['rollback_index'] = change['rollbackIndex']
                if 'securityPatchLevel' in change:
                    kwargs['security_patch'] = change['securityPatchLevel']
                if 'observed' in change:
                    kwargs['observed'] = change['observed']
                with self.assertRaisesRegex(ValueError, message):
                    epoch.check_candidate(**kwargs)

    def test_journal_is_in_memory_and_the_cli_does_not_allocate(self):
        first = epoch.append([], self.proposal())
        self.assertEqual(first[0]['build'], '2026130001')
        self.assertFalse(first[0]['issued'])
        with self.assertRaisesRegex(ValueError, 'next unissued'):
            epoch.append(first, self.proposal())
        second = self.proposal(build='2026130002', previous=epoch.digest(first[0]))
        self.assertEqual(epoch.append(first, second)[1]['build'], '2026130002')
        result = subprocess.run([sys.executable, str(ROOT/'scripts/mustang_epoch.py'), 'allocate'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 4)
        self.assertIn('not enabled', result.stderr)
        described = subprocess.run([sys.executable, str(ROOT/'scripts/mustang_epoch.py'), 'describe'],
                                   capture_output=True, text=True, check=True)
        self.assertFalse(json.loads(described.stdout)['issued'])


if __name__ == '__main__':
    unittest.main()
