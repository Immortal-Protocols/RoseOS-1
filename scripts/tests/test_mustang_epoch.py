"""Proposal checks for the retired Mustang epoch. They do not allocate a build."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts')]
import mustang_epoch as epoch

PATCH = '2026-10-05'
STAMP = epoch.security_patch_timestamp(PATCH)


class EpochTest(unittest.TestCase):
    def proposal(self, build='2026130001', **change):
        value = {'previous': epoch.genesis(), 'build': build, 'epoch': epoch.EPOCH,
                 'timestamp': 1793755348, 'rollbackIndex': STAMP,
                 'securityPatchLevel': PATCH, 'observed': []}
        value.update(change)
        return value

    def test_range_is_a_proposal_above_evidence_and_not_a_legacy_date(self):
        described = epoch.describe()
        self.assertEqual(described['proposalStart'], '2026130001')
        self.assertFalse(described['reserved'])
        self.assertIsNone(described['authority'])
        self.assertFalse(described['issued'])
        self.assertFalse(described['inMemoryChainIsAuthority'])
        self.assertIsNone(described['consumedBuilds'])
        self.assertFalse(epoch.legacy_date('2026130001'))
        self.assertTrue(epoch.legacy_date('2026110402'))
        self.assertLessEqual(epoch.RANGE_END, epoch.INT32_MAX)
        for bad, message in (
                ('2026110502', 'legacy date allocator'),
                ('2026110402', 'quarantined'),
                ('2026110401', 'collides'),
                ('9999123200', 'outside'),
                ('123', 'ten-digit string'),
                (2026130001, 'ten-digit string'),
                (True, 'ten-digit string')):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, message):
                    epoch.in_range(bad)
        self.assertNotRegex(Path(epoch.__file__).read_text(), r'next_build\(')

    def test_observed_higher_id_and_bad_types_reject_a_lower_candidate(self):
        epoch.check_candidate('2026130001', 1793755348, STAMP, PATCH, [])
        with self.assertRaisesRegex(ValueError, 'not strictly newer'):
            epoch.check_candidate('2026130001', 1793755348, STAMP, PATCH, ['2027010101'])
        for observed in ([2027010101], [True], ['2026130001abc'], '2027010101'):
            with self.subTest(observed=observed):
                with self.assertRaisesRegex(ValueError, 'ten-digit string|list of ten-digit'):
                    epoch.check_candidate('2026130001', 1793755348, STAMP, PATCH, observed)
        for level in ('2026-99-99', '2026-02-31', '2026-10-5', 20261005):
            with self.subTest(level=level):
                with self.assertRaisesRegex(ValueError, 'real date|YYYY-MM-DD'):
                    epoch.check_candidate('2026130001', 1793755348, STAMP, level, [])
        with self.assertRaisesRegex(ValueError, 'PLATFORM_SECURITY_PATCH_TIMESTAMP'):
            epoch.check_candidate('2026130001', 1793755348, STAMP + 1, PATCH, [])
        with self.assertRaisesRegex(ValueError, 'older'):
            epoch.check_candidate('2026130001', 1793755348, epoch.security_patch_timestamp('2026-10-04'), '2026-10-04', [])
        with self.assertRaisesRegex(ValueError, 'int64'):
            epoch.check_candidate('2026130001', True, STAMP, PATCH, [])

    def test_malformed_prefix_cannot_authorize_the_next_proposal(self):
        first = epoch.append([], self.proposal())
        self.assertEqual(first[0]['status'], 'proposal')
        self.assertFalse(first[0]['issued'])
        tampered = dict(first[0], build='2026130002')
        follower = self.proposal(build='2026130002', previous=epoch.digest(tampered), timestamp=1793755349)
        with self.assertRaisesRegex(ValueError, 'sequence or predecessor'):
            epoch.append([tampered], follower)
        missing = dict(first[0])
        del missing['schema']
        with self.assertRaisesRegex(ValueError, 'fields differ'):
            epoch.append([missing], follower)
        flagged = dict(first[0], sequence=True)
        with self.assertRaisesRegex(ValueError, 'sequence differs'):
            epoch.append([flagged], self.proposal(build='2026130002', previous=epoch.digest(flagged), timestamp=1793755349))
        second = self.proposal(build='2026130002', previous=epoch.digest(first[0]), timestamp=1793755349)
        self.assertEqual(epoch.append(first, second)[1]['build'], '2026130002')
        with self.assertRaisesRegex(ValueError, 'not strictly newer'):
            epoch.append(first, dict(second, observed=['2027010101']))
        result = subprocess.run([sys.executable, str(ROOT/'scripts/mustang_epoch.py'), 'allocate'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 4)
        described = json.loads(subprocess.run([sys.executable, str(ROOT/'scripts/mustang_epoch.py'), 'describe'],
                                              capture_output=True, text=True, check=True).stdout)
        self.assertFalse(described['reserved'])
        self.assertFalse(described['issued'])
        self.assertIsNone(described['authority'])


if __name__ == '__main__':
    unittest.main()
