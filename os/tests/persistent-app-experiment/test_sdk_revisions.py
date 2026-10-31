"""sdk_revisions.py over SYNTHETIC `sdkmanager --list_installed` tables in sdkmanager's layout and the real QA
manifest; nothing is installed or run."""
import io
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sdk_revisions as sr  # noqa: E402

MANIFEST = json.loads(sr.MANIFEST.read_text())

def table(rows):
    lines = ['Installed packages:', '  Path                 | Version | Description | Location',
        '  -------              | ------- | -------     | -------']
    lines += [f'  {path} | {version} | {path} description | {location}' for path, version, location in rows]
    return '\n'.join(lines) + '\n'

EXACT = [('build-tools;35.0.0', '35.0.0', 'build-tools/35.0.0'), ('cmdline-tools;latest', '22.0', 'cmdline-tools/latest/'),
    ('emulator', '37.2.13', 'emulator'), ('platform-tools', '37.0.1', 'platform-tools'),
    ('platforms;android-35', '2', 'platforms/android-35'),
    ('system-images;android-35;default;x86_64', '2', 'system-images/android-35/default/x86_64/')]

class RevisionTest(unittest.TestCase):
    def check(self, rows):
        return sr.check(table(rows), MANIFEST)

    def test_the_pins_come_from_the_qa_manifest(self):
        self.assertEqual(sr.pinned(MANIFEST), {
            'cmdline-tools;22.0': ('22.0', 'cmdline-tools/latest'), 'platform-tools': ('37.0.1', 'platform-tools'),
            'emulator': ('37.2.13', 'emulator'),
            'system-images;android-35;default;x86_64': ('2', 'system-images/android-35/default/x86_64'),
            'platforms;android-35': ('2', 'platforms/android-35'), 'build-tools;35.0.0': ('35.0.0', 'build-tools/35.0.0')})
        self.assertEqual(MANIFEST['runner']['sha256'], 'missing', 'the runner pin stays explicitly missing')

    def test_exactly_the_pinned_set_passes(self):
        self.assertEqual(self.check(EXACT), [])
        alias = [r if r[0] != 'cmdline-tools;latest' else ('cmdline-tools;22.0', '22.0', 'cmdline-tools/latest') for r in EXACT]
        self.assertEqual(self.check(alias), [])

    def test_drift_missing_extra_or_duplicates_refuse(self):
        def edit(path, version=None, location=None):
            return [(p, version or v, location or l) if p == path else (p, v, l) for p, v, l in EXACT]
        cases = {
            'newer emulator': (edit('emulator', '37.3.6'), 'emulator is revision 37.3.6, pinned 37.2.13'),
            'cmdline-tools 23 shim': (edit('cmdline-tools;latest', '23.0'), 'cmdline-tools;22.0 is revision 23.0'),
            'image r03': (edit('system-images;android-35;default;x86_64', '3'), 'is revision 3, pinned 2'),
            'moved': (edit('platform-tools', location='platform-tools-old'), 'installed at platform-tools-old'),
            'missing emulator (direct unzip, no package.xml)': ([r for r in EXACT if r[0] != 'emulator'],
                'emulator is not installed exactly once'),
            'extra package': (EXACT + [('platforms;android-36', '1', 'platforms/android-36')],
                'platforms;android-36 is installed but not pinned'),
            'both cmdline-tools paths': (EXACT + [('cmdline-tools;22.0', '22.0', 'cmdline-tools/22.0')],
                'listed under two paths'),
            'duplicate row': (EXACT + [EXACT[0]], 'listed twice'),
        }
        for label, (rows, expect) in cases.items():
            with self.subTest(label):
                problems = self.check(rows)
                self.assertTrue(any(expect in p for p in problems), problems)

    def test_unparseable_or_absent_tables_refuse(self):
        for text in ('', 'Warning: something\n', 'Installed packages:\n  build-tools;35.0.0 35.0.0\n',
                table(EXACT).replace('| 35.0.0  |', '| latest |').replace('35.0.0 | build', 'x y | build')):
            with self.subTest(text=text[:30]):
                self.assertTrue(sr.check(text, MANIFEST))
        self.assertTrue(any('does not parse' in p for p in self.check(EXACT + [('Bad Path!', '1', 'x')])))

    def test_an_available_packages_section_after_the_table_is_ignored(self):
        text = table(EXACT) + '\nAvailable Packages:\n  Path | Version | Description\n  emulator | 37.3.6 | Android Emulator\n'
        self.assertEqual(sr.check(text, MANIFEST), [])

    def test_cli_reads_stdin_and_exits_by_result(self):
        out = io.StringIO()
        sys.stdout, saved = out, sys.stdout
        try:
            self.assertEqual(sr.main([], io.StringIO(table(EXACT))), 0)
            self.assertEqual(sr.main([], io.StringIO(table(EXACT[1:]))), 1)
        finally:
            sys.stdout = saved
        results = [json.loads(chunk)['result'] for chunk in out.getvalue().replace('}\n{', '}\n\x00{').split('\x00')]
        self.assertEqual(results, ['exact', 'refused'])

    def test_it_installs_downloads_or_accepts_nothing(self):
        code = (HERE/'sdk_revisions.py').read_text().split('"""', 2)[2]
        for banned in ('subprocess', 'urllib', 'requests', '--install', '--licenses', 'open(', "'w'"):
            self.assertNotIn(banned, code, banned)

class SetupDocTest(unittest.TestCase):
    def test_the_setup_doc_names_the_manifest_pins_and_keeps_the_runner_pin_missing(self):
        doc = (HERE/'isolated-trial-setup.md').read_text()
        for package in MANIFEST['sdk']['packages']:
            if package['path'] not in ('platforms;android-35', 'build-tools;35.0.0'):  # pinned by sha256 in the table
                self.assertIn(package['archive'], doc, package['path'])
        self.assertIn('**22.0**', doc)
        self.assertNotIn('no reviewed pin in this repository', doc)
        runner = MANIFEST['runner']
        self.assertIn(runner['version'], doc)
        self.assertIn(runner['tagCommit'], doc)
        self.assertIn('**sha256 still missing**', doc)
        self.assertIn('sdkmanager --list_installed | python3 os/tests/persistent-app-experiment/sdk_revisions.py', doc)
        self.assertIn('Install with that `sdkmanager`, as root', doc)
        self.assertIn('Accept only `android-sdk-license`', doc)
        self.assertNotIn('SDK licences: decision needed', doc)

if __name__ == '__main__':
    unittest.main()
