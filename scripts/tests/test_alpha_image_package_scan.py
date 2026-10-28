"""alpha_image's package-only reader for the Updater-absence scan (INFRA-ALPHA-117). The fake aapt answers with the
output shapes observed by the ALPHA-116 diagnostic (run 40771878744) on the retained alpha image: badging exits 1 on
unresolvable framework references for Shell and Settings and prints an empty versionCode for IntentResolver, while
xmltree reads all three compiled manifests. Labels are synthetic; the shapes are the observed ones."""
from pathlib import Path
import stat
import sys
import tempfile
import textwrap
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import alpha_image  # noqa: E402

# Answers from files beside the APK: <apk>.<operation>.out / .err / .rc
FAKE_AAPT = textwrap.dedent('''\
    #!/usr/bin/env python3
    import pathlib, sys
    a = sys.argv[1:]
    apk = pathlib.Path(a[2])
    def part(suffix, default):
        p = apk.with_name(apk.name + '.' + a[1] + suffix)
        return p.read_text() if p.exists() else default
    sys.stdout.write(part('.out', ''))
    sys.stderr.write(part('.err', ''))
    sys.exit(int(part('.rc', '1')))
''')

def tree(*lines):
    return '\n'.join(lines) + '\n'

SHELL_TREE = tree('N: android=http://schemas.android.com/apk/res/android',
    '  E: manifest (line=17)',
    '    A: android:versionCode(0x0101021b)=(type 0x10)0x25',
    '    A: android:sharedUserId(0x0101000b)="android.uid.shell" (Raw: "android.uid.shell")',
    '    A: package="com.android.shell" (Raw: "com.android.shell")',
    '    E: uses-permission (line=24)',
    '      A: android:name(0x01010003)="android.permission.SEND_SMS" (Raw: "android.permission.SEND_SMS")',
    '    E: application (line=700)',
    '      A: android:label(0x01010001)=@0x010403e2')
INTENT_TREE = tree('N: android=http://schemas.android.com/apk/res/android',
    '  E: manifest (line=18)',
    '    A: android:versionCode(0x0101021b)=(type 0x10)0x0',
    '    A: package="com.android.intentresolver" (Raw: "com.android.intentresolver")',
    '    E: application (line=30)')
SETTINGS_TREE = SHELL_TREE.replace('com.android.shell', 'com.android.settings')

class Fake(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='alpha-scan-'))
        self.aapt = self.tmp/'aapt'
        self.aapt.write_text(FAKE_AAPT)
        self.aapt.chmod(self.aapt.stat().st_mode | stat.S_IXUSR)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def apk(self, name, badging=(1, '', ''), xmltree=(1, '', '')):
        path = self.tmp/name
        path.write_bytes(b'PK synthetic ' + name.encode())
        for op, (rc, out, err) in (('badging', badging), ('xmltree', xmltree)):
            path.with_name(path.name + f'.{op}.rc').write_text(str(rc))
            path.with_name(path.name + f'.{op}.out').write_text(out)
            path.with_name(path.name + f'.{op}.err').write_text(err)
        return path

    def package(self, out, rc=0, err=''):
        return alpha_image.manifest_package(str(self.aapt), self.apk('X.apk', xmltree=(rc, out, err)))

class ObservedFailuresTest(Fake):
    def test_the_three_observed_apks_have_a_package_through_the_manifest_but_not_through_badging(self):
        shell = self.apk('Shell.apk',
            badging=(1, "package: name='com.android.shell' versionCode='37' versionName='17'\n",
                "ERROR getting 'android:label' attribute: attribute value reference does not exist\n"
                "W/ResourceType: resource 0x010403e2 (android:label) reference does not exist\n"),
            xmltree=(0, SHELL_TREE, ''))
        intent = self.apk('IntentResolver.apk',
            badging=(0, "package: name='com.android.intentresolver' versionCode='' versionName=''\nsdkVersion:'37'\n", ''),
            xmltree=(0, INTENT_TREE, ''))
        settings = self.apk('Settings.apk',
            badging=(1, "package: name='com.android.settings' versionCode='37' versionName='17'\n",
                "W/ResourceType: resource 0x01080665 (android:icon) reference does not exist\n"),
            xmltree=(0, SETTINGS_TREE, ''))
        expected = {shell: 'com.android.shell', intent: 'com.android.intentresolver', settings: 'com.android.settings'}
        for apk, package in expected.items():
            # Negative control: the gate's original package+version reader still rejects each of them...
            self.assertIsNone(alpha_image.badging(str(self.aapt), apk), apk.name)
            # ...while the strict compiled-manifest reader establishes the package identity.
            self.assertEqual(alpha_image.manifest_package(str(self.aapt), apk), package, apk.name)

class StrictReaderTest(Fake):
    def test_aapt_failure_or_partial_output_is_not_a_package(self):
        self.assertIsNone(self.package(SHELL_TREE, rc=1, err='ERROR: dump failed'))  # nonzero exit, even with stdout
        self.assertIsNone(self.package(''))
        self.assertIsNone(self.package(SHELL_TREE.split('    A: package')[0]))  # cut before the package attribute

    def test_the_root_must_be_the_only_top_level_manifest_element(self):
        self.assertIsNone(self.package(SHELL_TREE.replace('E: manifest', 'E: application', 1)))
        self.assertIsNone(self.package(SHELL_TREE + tree('  E: manifest (line=900)', '    A: package="app.seamlessupdate.client" (Raw: "app.seamlessupdate.client")')))
        self.assertIsNone(self.package('garbage before the tree\n' + SHELL_TREE))

    def test_missing_duplicate_or_invalid_root_packages_fail(self):
        self.assertIsNone(self.package(SHELL_TREE.replace('    A: package="com.android.shell" (Raw: "com.android.shell")\n', '')))
        self.assertIsNone(self.package(SHELL_TREE.replace('    E: uses-permission', '    A: package="com.android.other" (Raw: "com.android.other")\n    E: uses-permission', 1)))
        for bad in ('', '1com.android', 'com..android', 'com.android shell', 'com/android'):
            self.assertIsNone(self.package(SHELL_TREE.replace('"com.android.shell"', f'"{bad}"')), repr(bad))
        self.assertIsNone(self.package(SHELL_TREE.replace('(Raw: "com.android.shell")', '(Raw: "app.seamlessupdate.client")')))
        self.assertEqual(self.package(tree('  E: manifest (line=1)', '    A: package="android" (Raw: "android")')), 'android')

    def test_nested_package_attributes_never_stand_in_for_the_root(self):
        no_root_package = SHELL_TREE.replace('    A: package="com.android.shell" (Raw: "com.android.shell")\n', '')
        nested = no_root_package + tree('    E: queries (line=800)', '      A: package="app.seamlessupdate.client" (Raw: "app.seamlessupdate.client")')
        self.assertIsNone(self.package(nested))
        spoof = SHELL_TREE + tree('    E: queries (line=800)', '      A: package="app.seamlessupdate.client" (Raw: "app.seamlessupdate.client")')
        self.assertEqual(self.package(spoof), 'com.android.shell')
        # A root-level attribute after a child element is malformed, so it cannot add or replace the package either.
        late = no_root_package + tree('    A: package="com.android.shell" (Raw: "com.android.shell")')
        self.assertIsNone(self.package(late))

class GateSourceTest(unittest.TestCase):
    def test_only_the_absence_scan_uses_the_package_reader(self):
        code = (SCRIPTS/'alpha_image.py').read_text()
        self.assertEqual(code.count('manifest_package(aapt, path)'), 1)
        scan = code[code.index("updater, inspected = spec['updater'], 0"):]
        self.assertIn('package = manifest_package(aapt, path)', scan)
        self.assertNotIn('badging(', scan)
        # Rose apps keep the exact package + numeric version, bytes and signer checks; Seedvault keeps badging.
        self.assertIn("row.update(sha256=sha256_path(apk), identity=badging(aapt, apk), signers=signers(apksigner, apk))", code)
        self.assertIn("if row['identity'] != (package, want['version']):", code)
        self.assertIn("if row['signers'] != [signer]:", code)
        self.assertIn("identity = badging(aapt, path)\n        row.update(present=True", code)
        self.assertIn('''match = re.search(r"^package: name='([^']+)' versionCode='(\\d+)'", out.stdout, re.M)''', code)

if __name__ == '__main__':
    unittest.main()
