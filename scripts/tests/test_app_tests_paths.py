"""app-tests.yml runs scripts/test-local.sh only for the paths its two triggers list. An os/tests unit of that suite that
names a script under scripts/ reads it (the reconcile guard in test_rose_edge_shell.py reads host-reconcile.py), so a
change to that script must trigger the suite, or a test it breaks stays unrun until an unrelated change does (#543:
host-reconcile.py was missing, and main's test-local.sh went red unseen)."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT/'.github/workflows/app-tests.yml'

def trigger_paths(text, event):
    block = re.search(rf'^  {event}:\n(.*?)(?=^  \S|^\S)', text, re.M | re.S)
    if not block:
        raise AssertionError(f'app-tests.yml has no {event} trigger')
    return re.findall(r"^\s+- '([^']+)'$", block.group(1), re.M)

def covered(path, patterns):
    return any(path == p or (p.endswith('/**') and path.startswith(p[:-2])) for p in patterns)

def suite_os_tests():
    lines = (ROOT/'scripts/test-local.sh').read_text().splitlines()
    return sorted({m.group(1) for line in lines for m in [re.match(r'python3 (os/tests/[\w-]+\.py)\b', line)] if m})

class AppTestsPathsTest(unittest.TestCase):
    def test_every_script_an_os_test_names_triggers_the_suite(self):
        text = WORKFLOW.read_text()
        triggers = {event: trigger_paths(text, event) for event in ('pull_request', 'push')}
        units = suite_os_tests()
        self.assertGreater(len(units), 10, 'test-local.sh lists its os/tests units')
        missing = []
        for unit in units:
            for script in sorted(set(re.findall(r'scripts/[\w.-]+\.py', (ROOT/unit).read_text()))):
                if not (ROOT/script).is_file():
                    continue  # a name in prose or a fixture path, not this repository's script
                for event, patterns in triggers.items():
                    if not covered(script, patterns):
                        missing.append(f'{script} ({unit}, {event})')
        self.assertEqual(missing, [], 'add these to app-tests.yml so a change to them runs the suite')

    def test_the_two_triggers_list_the_same_paths(self):
        text = WORKFLOW.read_text()
        self.assertEqual(trigger_paths(text, 'pull_request'), trigger_paths(text, 'push'))

    def test_the_guard_sees_the_reconcile_reader_that_was_missed(self):
        units = suite_os_tests()
        self.assertIn('os/tests/test_rose_edge_shell.py', units)
        self.assertIn('scripts/host-reconcile.py', (ROOT/'os/tests/test_rose_edge_shell.py').read_text())

if __name__ == '__main__':
    unittest.main()
