"""Bundled apps must replace the phone's installed copies; pure checks, no device or private APK."""
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
from background_apks import identity, installed_precedence

SIGNER = 'A' * 64
ROWS = [
    {'packageName': 'dev.pixel.backgroundcontrol', 'certificates': [SIGNER], 'versionCode': 28},
    {'packageName': 'dev.pixel.voice', 'certificates': [SIGNER], 'versionCode': 299},
]
# Same shape as os/release-evidence/*/expected-identities.json (signer in lowercase there).
INSTALLED = {'apps': [
    {'packageName': 'dev.pixel.voice', 'versionCode': 298, 'signerSha256': SIGNER.lower()},
    {'packageName': 'dev.pixel.backgroundcontrol', 'versionCode': 27, 'signerSha256': SIGNER.lower()},
    {'packageName': 'dev.pixel.calculator.agent', 'versionCode': 4, 'signerSha256': SIGNER.lower()},
]}

def issues(rows, installed=INSTALLED):
    return installed_precedence(rows, installed)[0]

class InstalledPrecedenceTest(unittest.TestCase):
    def test_higher_versions_with_the_same_signer_pass(self):
        self.assertEqual(installed_precedence(ROWS, INSTALLED), ([], []))

    def test_lower_version_is_refused(self):
        found = issues([dict(ROWS[1], versionCode=297)])
        self.assertEqual(len(found), 1)
        self.assertIn('would keep its installed copy', found[0])

    def test_unchanged_app_at_equal_version_is_a_note_not_an_issue(self):
        # The P1 shape: Rose Agent 299 over 298, controller unchanged at 27.
        rows = [dict(ROWS[0], versionCode=27), ROWS[1]]
        found, notes = installed_precedence(rows, INSTALLED)
        self.assertEqual(found, [])
        self.assertEqual(len(notes), 1)
        self.assertIn('dev.pixel.backgroundcontrol', notes[0])

    def test_equal_version_with_different_pinned_bytes_is_refused(self):
        pinned = {'apps': [dict(INSTALLED['apps'][0], apkSha256='c' * 64)]}
        row = dict(ROWS[1], versionCode=298, apkSha256='d' * 64)
        self.assertIn('differs from the installed one', issues([row], pinned)[0])
        self.assertEqual(issues([dict(row, apkSha256='c' * 64)], pinned), [])

    def test_different_signer_is_refused(self):
        found = issues([dict(ROWS[1], certificates=['B' * 64])])
        self.assertEqual(found, ['dev.pixel.voice: bundled signer differs from the installed copy'])

    def test_receipt_without_version_is_refused(self):
        row = {k: v for k, v in ROWS[1].items() if k != 'versionCode'}
        self.assertIn('regenerate', issues([row])[0])

    def test_app_not_installed_is_not_compared(self):
        self.assertEqual(installed_precedence(
            [{'packageName': 'dev.pixel.new', 'certificates': [SIGNER], 'versionCode': 1}], INSTALLED), ([], []))

    def test_published_release_evidence_is_accepted_as_input(self):
        root = Path(__file__).resolve().parents[2]/'os/release-evidence'
        for path in root.glob('*/expected-identities.json'):
            installed_precedence(ROWS, json.loads(path.read_text()))

    def test_malformed_installed_identities_are_rejected(self):
        for bad in ({}, {'apps': [{'packageName': 'dev.pixel.voice', 'versionCode': '298',
                'signerSha256': SIGNER}]}, {'apps': [{'packageName': 'dev.pixel.voice', 'versionCode': 298}]}):
            with self.assertRaises(ValueError):
                installed_precedence(ROWS, bad)

class IdentityVersionTest(unittest.TestCase):
    def test_identity_records_version_code_from_aapt(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory)
            apk = tools/'app.apk'
            apk.write_bytes(b'not inspected by the fake tools')
            aapt, apksigner = tools/'aapt', tools/'apksigner'
            aapt.write_text("#!/bin/sh\necho \"package: name='dev.pixel.voice' versionCode='299' versionName='299'\"\n")
            apksigner.write_text(f"#!/bin/sh\necho 'Signer #1 certificate SHA-256 digest: {SIGNER.lower()}'\n")
            for tool in (aapt, apksigner):
                tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
            row = identity(apk, aapt, apksigner, 35)
        self.assertEqual((row['packageName'], row['versionCode'], row['certificates']),
            ('dev.pixel.voice', 299, [SIGNER]))

if __name__ == '__main__':
    unittest.main()
