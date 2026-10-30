"""background_apks.verified_signers/identity (OS-RANGO-SIGNER-004). The V3.0 line is the actual operator-observed SDK 37
output (scripts/tests/fixtures/apksigner-sdk37-v30); every other line here is SYNTHETIC, in the existing apksigner
shapes. identity() is exercised with subprocess.check_output mocked: no real aapt, apksigner or APK."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import background_apks as ba  # noqa: E402

OBSERVED = (SCRIPTS/'tests/fixtures/apksigner-sdk37-v30/observed-line.txt').read_text()
PIN = '650D47F91B7F5AF3C530FEF53FE92018AA4521EFBA065BCB1D6FC9B699060BFB'
OTHER = 'CD' * 32

class VerifiedSignersTest(unittest.TestCase):
    def test_the_observed_v3_0_label(self):
        self.assertEqual(ba.verified_signers(OBSERVED, 37), {PIN})

    def test_existing_numbered_and_ranged_formats_still_work(self):
        self.assertEqual(ba.verified_signers(f'Signer #1 certificate SHA-256 digest: {PIN.lower()}\n', 37), {PIN})
        ranged = f'Signer (minSdkVersion=33, maxSdkVersion=2147483647) certificate SHA-256 digest: {PIN.lower()}\n'
        self.assertEqual(ba.verified_signers(ranged, 37), {PIN})

    def test_a_range_excluding_the_sdk_is_ignored(self):
        excluded = f'Signer (minSdkVersion=24, maxSdkVersion=32) certificate SHA-256 digest: {OTHER.lower()}\n'
        self.assertEqual(ba.verified_signers(excluded, 37), set())
        self.assertEqual(ba.verified_signers(excluded + OBSERVED, 37), {PIN})

    def test_malformed_truncated_or_unrelated_lines_are_not_signers(self):
        for line in (OBSERVED.strip()[:-2],                                   # truncated digest
                'V3.0 Signer: certificate SHA-256 digest: ' + 'zz' * 32,      # not hex
                'V3.0 Signer: certificate SHA-1 digest: ' + 'ab' * 20,        # another digest kind
                'V3.1 Signer: certificate SHA-256 digest: ' + PIN.lower(),    # a label not observed
                'Source Stamp Signer: certificate SHA-256 digest: ' + PIN.lower(),
                'note: V3.0 Signer: certificate SHA-256 digest: ' + PIN.lower(),  # not the whole line
                OBSERVED.strip() + ' trailing',
                'Verifies', ''):
            with self.subTest(line=line):
                self.assertEqual(ba.verified_signers(line + '\n', 37), set())

    def test_two_distinct_signers_are_both_reported(self):
        self.assertEqual(ba.verified_signers(OBSERVED + f'V3.0 Signer: certificate SHA-256 digest: {OTHER}\n', 37),
            {PIN, OTHER})

class IdentityTest(unittest.TestCase):
    def setUp(self):
        self.apk = Path(tempfile.mkstemp(suffix='.apk')[1])
        self.apk.write_bytes(b'synthetic apk bytes')
        self.addCleanup(self.apk.unlink)

    def identity(self, signer_output=None, signer_error=None):
        def fake(argv, text):
            if argv[1] == 'dump':
                return "package: name='dev.pixel.backgroundcontrol' versionCode='28' versionName='x'\n"
            self.assertEqual(argv[1:7], ['verify', '--print-certs', '--min-sdk-version', '37', '--max-sdk-version', '37'])
            if signer_error:
                raise signer_error
            return signer_output
        with mock.patch.object(ba.subprocess, 'check_output', side_effect=fake):
            return ba.identity(self.apk, 'aapt', 'apksigner', 37)

    def test_observed_output_gives_the_exact_identity(self):
        found = self.identity(OBSERVED)
        self.assertEqual((found['packageName'], found['versionCode'], found['certificates']),
            ('dev.pixel.backgroundcontrol', 28, [PIN]))

    def test_failed_verification_is_refused_despite_a_plausible_digest(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.identity(signer_error=subprocess.CalledProcessError(1, 'apksigner', output=OBSERVED))

    def test_missing_signer_output_is_refused(self):
        for output in ('', 'Verifies\n', 'V3.1 Signer: certificate SHA-256 digest: ' + PIN.lower() + '\n'):
            with self.subTest(output=output), self.assertRaises(ValueError) as caught:
                self.identity(output)
            self.assertIn('No verified signer for target SDK 37', str(caught.exception))

    def test_a_wrong_or_extra_certificate_is_visible_to_the_pin_check(self):
        self.assertEqual(self.identity(f'V3.0 Signer: certificate SHA-256 digest: {OTHER}\n')['certificates'], [OTHER])
        both = self.identity(OBSERVED + f'V3.0 Signer: certificate SHA-256 digest: {OTHER}\n')['certificates']
        self.assertEqual(both, sorted([PIN, OTHER]), 'callers pinning a singleton signer see both and refuse')

if __name__ == '__main__':
    unittest.main()
