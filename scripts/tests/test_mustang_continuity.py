"""Candidate installer continuity matches the installed updater and v1 cert, and nothing else."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts')]
import mustang_ota as ota

RECORD = ROOT/'os/build-only/mustang-candidate-continuity-20261107.json'


class ContinuityTest(unittest.TestCase):
    def record(self, **change):
        value = json.loads(RECORD.read_text())
        value.update(change)
        return value

    def test_matching_candidate_is_not_the_current_phone_or_target_files(self):
        got = ota.candidate_continuity(self.record())
        self.assertEqual(got['evidenceKind'], 'candidate-installer-continuity')
        self.assertFalse(got['currentPhone'])
        self.assertFalse(got['phoneObserved'])
        self.assertFalse(got['admitsRuntime'])
        self.assertIsNone(got['targetFiles'])
        self.assertEqual(got['updaterBytes'], 5752573)
        self.assertEqual(got['updaterApkSha256'], '32d5f335098c14839641784552fbed513932921c26f4b83647967747198c2cd9')
        self.assertEqual(got['trustedOtaCertificateSha256'], ['93cc757e7f4e4e5c4ef7726fbeec9acc77b1f97d471c1cd0d62753cf94e471a8'])
        self.assertNotIn('updaterSignerSha256', got)
        self.assertFalse(got['apkSignerRemeasured'])
        self.assertEqual(got['historicalUpdaterSignerSha256'], '93cc757e7f4e4e5c4ef7726fbeec9acc77b1f97d471c1cd0d62753cf94e471a8')
        runtime = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
        self.assertEqual(runtime['status'], 'unbound')
        self.assertIsNone(runtime.get('baselineTargetFiles'))
        self.assertNotIn('candidateContinuity', runtime)

    def test_mismatched_bytes_or_a_phone_claim_fail_closed(self):
        bad = self.record()
        bad['observations'] = dict(bad['observations'], updaterSha256='0'*64)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            ota.candidate_continuity(bad)
        certs = json.loads(json.dumps(self.record()['observations']['otaCerts']))
        certs[0]['derSha256'] = '1'*64
        bad = self.record()
        bad['observations'] = dict(bad['observations'], otaCerts=certs)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            ota.candidate_continuity(bad)
        with self.assertRaisesRegex(ValueError, 'not a current phone'):
            ota.candidate_continuity(self.record(currentPhone=True))
        claimed = self.record(targetFiles={'sha256': 'baf24f4b433d66e7115e5fc5a15af1394482a59af10c08e222913a65e6bf7814'})
        with self.assertRaisesRegex(ValueError, 'not a current phone'):
            ota.candidate_continuity(claimed)
        measured = self.record()
        measured['observations'] = dict(measured['observations'], updaterSignerSha256='93cc757e7f4e4e5c4ef7726fbeec9acc77b1f97d471c1cd0d62753cf94e471a8')
        with self.assertRaisesRegex(ValueError, 'did not measure the APK signer'):
            ota.candidate_continuity(measured)

    def test_staged_baseline_must_equal_the_vetted_record_before_artifacts(self):
        vetted = ota.candidate_continuity(self.record())
        self.assertEqual(ota.require_staged_baseline(dict(vetted)), vetted)
        source = Path(ota.__file__).read_text().split('def stage(', 1)[1].split('\ndef verify_staged', 1)[0]
        self.assertLess(source.index('require_staged_baseline('), source.index('out.mkdir'))
        self.assertLess(source.index('require_staged_baseline('), source.index('observe_target('))
        self.assertLess(source.index('require_staged_baseline('), source.index('unzip_otatools'))
        for key, value in (
                ('build', '2026130001'),
                ('timestamp', '1793742436'),
                ('discoveryClassesPresent', False),
                ('installedEvidence', 'rewritten'),
                ('updaterSignerSha256', vetted['historicalUpdaterSignerSha256']),
                ('historicalUpdaterSignerSha256', '0'*64),
                ('apkSignerRemeasured', True),
                ('trustedOtaCertificateSha256', ['0'*64])):
            tampered = dict(vetted)
            tampered[key] = value
            with self.assertRaisesRegex(ValueError, 'differs from the vetted'):
                ota.require_staged_baseline(tampered)


if __name__ == '__main__':
    unittest.main()
