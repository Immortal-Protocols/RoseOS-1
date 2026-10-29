"""systemui_identity (OS-COMPONENT-ID-002). Every tool output here is SYNTHETIC: it follows the line shapes of
`aapt dump xmltree` (as observed by the ALPHA-116 diagnostic, scripts/tests/test_alpha_image_package_scan.py) and
`apksigner verify --print-certs`, but the values, digests and APK hashes are invented. A pass proves the parser and
receipt rules only; it is not evidence of the identity of any shipped SystemUI."""
from pathlib import Path
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import systemui_identity as si  # noqa: E402

APK = 'a1' * 32          # synthetic APK sha256
OTHER_APK = 'b2' * 32
CERT = 'c3' * 32         # synthetic certificate digest; not a real platform certificate
PATH = 'SYSTEM_EXT/priv-app/SystemUI/SystemUI.apk'

def tree(*lines):
    return '\n'.join(lines) + '\n'

MANIFEST = tree(
    'N: android=http://schemas.android.com/apk/res/android',
    '  E: manifest (line=17)',
    '    A: android:versionCode(0x0101021b)=(type 0x10)0x25',
    '    A: android:versionName(0x0101021c)="17" (Raw: "17")',
    '    A: android:sharedUserId(0x0101000b)="android.uid.systemui" (Raw: "android.uid.systemui")',
    '    A: package="com.android.systemui" (Raw: "com.android.systemui")',
    '    A: coreApp=(type 0x12)0xffffffff',
    '    E: uses-permission (line=40)',
    '      A: android:name(0x01010003)="android.permission.STATUS_BAR" (Raw: "android.permission.STATUS_BAR")',
    '    E: application (line=300)',
    '      A: android:label(0x01010001)=@0x7f140000',
    '      A: android:persistent(0x0101000d)=(type 0x12)0xffffffff',
    '      E: service (line=310)',
    '        A: android:persistent(0x0101000d)=(type 0x12)0x0',
    '        A: android:name(0x01010003)="com.android.systemui.SystemUIService" (Raw: "com.android.systemui.SystemUIService")')

SIGNER = tree(
    'Signer #1 certificate DN: CN=Synthetic, OU=Test, O=Test',
    f'Signer #1 certificate SHA-256 digest: {CERT}',
    'Signer #1 certificate SHA-1 digest: ' + 'd4' * 20,
    'Signer #1 certificate MD5 digest: ' + 'e5' * 16)

def out(stdout, rc=0, apk=APK):
    return si.ToolOutput(rc, stdout, apk)

def inputs(**over):
    base = dict(apk_path=PATH, apk_sha256=APK, xmltree=out(MANIFEST), apksigner=out(SIGNER),
        artifact={'kind': 'target-files', 'name': 'frankel-target_files-SYNTHETIC.zip', 'sha256': 'f6' * 32},
        source={'revision': '0' * 39 + '1', 'buildId': 'SYNTHETIC'},
        tools={'aapt': {'path': 'bin/aapt', 'sha256': '17' * 32}, 'apksigner': {'path': 'bin/apksigner', 'sha256': '28' * 32}})
    base.update(over)
    return base

class ReceiptTest(unittest.TestCase):
    def test_a_complete_identity(self):
        r = si.build_receipt(**inputs())
        self.assertEqual(r['package'], 'com.android.systemui')
        self.assertEqual(r['versionCode'], 37)
        self.assertEqual(r['versionName'], {'state': 'present', 'value': '17'})
        self.assertEqual(r['sharedUserId'], {'state': 'present', 'value': 'android.uid.systemui'})
        self.assertEqual(r['persistent'], {'state': 'present', 'value': True}, 'the application flag, not the service one')
        self.assertEqual(r['signingCertificateSha256'], CERT)
        self.assertEqual(r['apk'], {'path': PATH, 'sha256': APK})
        self.assertEqual(r['artifact']['kind'], 'target-files')
        self.assertEqual(r['source'], {'buildId': 'SYNTHETIC', 'revision': '0' * 39 + '1'})
        self.assertIs(r['certificatePinned'], False)
        self.assertEqual(r['schema'], 'rose-systemui-identity/1')

    def test_absent_optional_attributes_are_absent_not_failures(self):
        bare = MANIFEST.replace('    A: android:sharedUserId(0x0101000b)="android.uid.systemui" (Raw: "android.uid.systemui")\n', '') \
            .replace('    A: android:versionName(0x0101021c)="17" (Raw: "17")\n', '') \
            .replace('      A: android:persistent(0x0101000d)=(type 0x12)0xffffffff\n', '')
        r = si.build_receipt(**inputs(xmltree=out(bare)))
        for key in ('versionName', 'sharedUserId', 'persistent'):
            self.assertEqual(r[key], {'state': 'absent'}, key)
        false = MANIFEST.replace('(0x0101000d)=(type 0x12)0xffffffff', '(0x0101000d)=(type 0x12)0x0', 1)
        self.assertEqual(si.build_receipt(**inputs(xmltree=out(false)))['persistent'], {'state': 'present', 'value': False})
        hexcode = MANIFEST.replace('(type 0x10)0x25', '(type 0x11)0x25')
        self.assertEqual(si.build_receipt(**inputs(xmltree=out(hexcode)))['versionCode'], 37)
        no_ids = MANIFEST.replace('(0x0101021b)', '').replace('(0x0101000d)', '')
        self.assertEqual(si.build_receipt(**inputs(xmltree=out(no_ids)))['versionCode'], 37)

    def test_serialization_is_deterministic(self):
        a = si.serialize(si.build_receipt(**inputs()))
        reordered = inputs(source={'buildId': 'SYNTHETIC', 'revision': '0' * 39 + '1'},
            tools={'apksigner': {'sha256': '28' * 32, 'path': 'bin/apksigner'}, 'aapt': {'sha256': '17' * 32, 'path': 'bin/aapt'}})
        self.assertEqual(a, si.serialize(si.build_receipt(**reordered)))
        self.assertTrue(a.endswith(b'\n') and a.count(b'\n') == 1 and b' ' not in a)
        self.assertEqual(json.loads(a), si.build_receipt(**inputs()))
        self.assertEqual(si.serialize(json.loads(a)), a)

class FailClosedTest(unittest.TestCase):
    def fails(self, expect, **over):
        with self.assertRaises(si.IdentityError) as caught:
            si.build_receipt(**inputs(**over))
        self.assertTrue(any(expect in p for p in caught.exception.problems), (expect, caught.exception.problems))

    def test_failed_or_partial_extraction(self):
        self.fails('aapt xmltree exited 1', xmltree=out(MANIFEST, rc=1))
        self.fails('apksigner exited 1', apksigner=out(SIGNER, rc=1))
        self.fails('no manifest element', xmltree=out(''))
        self.fails('0 <application>', xmltree=out(MANIFEST.split('    A: android:versionCode')[0]))  # cut short
        self.fails('no android:versionCode', xmltree=out(MANIFEST.replace('    A: android:versionCode(0x0101021b)=(type 0x10)0x25\n', '')))
        self.fails('0 <application>', xmltree=out(MANIFEST.split('    E: application')[0]))
        self.fails('exactly one (#1)', apksigner=out(''))

    def test_malformed_or_ambiguous_manifest(self):
        cases = [
            (MANIFEST.replace('E: manifest', 'E: application', 1), 'not <manifest>'),
            ('garbage\n' + MANIFEST, 'content before the manifest'),
            (MANIFEST + tree('  E: manifest (line=900)'), 'more than one top-level'),
            (MANIFEST.replace('    E: uses-permission', '    A: android:versionCode(0x0101021b)=(type 0x10)0x26\n    E: uses-permission', 1), 'repeated'),
            (MANIFEST + tree('    A: android:sharedUserId(0x0101000b)="android.uid.system" (Raw: "android.uid.system")'), 'after a child'),
            (MANIFEST + tree('    E: application (line=900)'), '2 <application>'),
            (MANIFEST.replace('"android.uid.systemui" (Raw: "android.uid.systemui")', '"android.uid.systemui" (Raw: "android.uid.system")'), 'Raw differ'),
            (MANIFEST.replace('(type 0x10)0x25', '"37" (Raw: "37")'), 'not an integer'),
            (MANIFEST.replace('(type 0x10)0x25', '@0x7f0b0001'), 'unresolved resource reference'),
            (MANIFEST.replace('0x0101000d)=(type 0x12)0xffffffff', '0x0101000d)=(type 0x12)0x1', 1), 'unsupported typed value'),
            (MANIFEST.replace('"android.uid.systemui" (Raw: "android.uid.systemui")', '(type 0x12)0xffffffff'), 'expected a string'),
            (MANIFEST.replace('android:persistent(0x0101000d)', 'android:persistent(0x0101000e)', 1), 'attribute id'),
            (MANIFEST.replace('"com.android.systemui" (Raw: "com.android.systemui")', '"systemui" (Raw: "systemui")'), 'not a valid package'),
            (MANIFEST.replace('    A: package="com.android.systemui" (Raw: "com.android.systemui")\n', ''), 'no package attribute'),
            (MANIFEST.replace('    A: coreApp=(type 0x12)0xffffffff', '    A: coreApp'), 'unreadable'),
        ]
        for manifest, expect in cases:
            with self.subTest(expect=expect):
                self.fails(expect, xmltree=out(manifest))

    def test_signer_must_be_single_and_recognised(self):
        two = SIGNER + tree(f'Signer #2 certificate SHA-256 digest: {"9a" * 32}')
        self.fails('2 signers', apksigner=out(two))
        self.fails('exactly one (#1)', apksigner=out(SIGNER.replace('Signer #1', 'Signer #2')))
        self.fails('unrecognised certificate digest', apksigner=out(SIGNER + tree(f'Source Stamp Signer certificate SHA-256 digest: {"9a" * 32}')))
        self.fails('unrecognised certificate digest', apksigner=out(SIGNER.replace(CERT, CERT.upper())))
        self.fails('unrecognised certificate digest', apksigner=out(SIGNER.replace(CERT, CERT[:-2])))

    def test_wrong_association_or_identity(self):
        self.fails(f'xmltree output is for APK {OTHER_APK}', xmltree=out(MANIFEST, apk=OTHER_APK))
        self.fails(f'apksigner output is for APK {OTHER_APK}', apksigner=out(SIGNER, apk=OTHER_APK))
        self.fails("not 'com.android.systemui'", xmltree=out(MANIFEST.replace('com.android.systemui"', 'com.android.shell"')))
        self.fails('relative artifact path', apk_path='/abs/SystemUI.apk')
        self.fails('relative artifact path', apk_path='SYSTEM_EXT/../SystemUI.apk')
        self.fails('apk sha256', apk_sha256=APK.upper())
        self.fails('artifact needs kind', artifact={'kind': 'ota', 'name': 'x', 'sha256': 'f6' * 32})
        self.fails('artifact needs kind', artifact={'kind': 'target-files', 'name': 'x'})
        self.fails('40-hex revision', source={'revision': 'abc'})
        self.fails('unexpected source fields', source={'revision': '0' * 40, 'signer': 'platform'})
        self.fails('tools.apksigner needs', tools={'aapt': {'path': 'a', 'sha256': '1' * 64}})

    def test_every_problem_is_reported_and_no_receipt_escapes(self):
        with self.assertRaises(si.IdentityError) as caught:
            si.build_receipt(**inputs(xmltree=out(MANIFEST, rc=2), apksigner=out('', rc=3)))
        self.assertEqual(len(caught.exception.problems), 2)

class PurityTest(unittest.TestCase):
    def test_the_module_runs_no_tool_and_opens_nothing(self):
        code = (Path(si.__file__)).read_text()
        for banned in ('subprocess', 'urllib', 'socket', 'requests', 'open(', 'os.system', 'zipfile', 'Popen'):
            self.assertNotIn(banned, code, banned)

if __name__ == '__main__':
    unittest.main()
