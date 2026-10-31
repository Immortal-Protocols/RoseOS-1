#!/usr/bin/env python3
"""scripts/test-select.py: unit parsing keeps every test-local.sh command exactly once, and selection is targeted only
when every changed code file is claimed by some unit's inputs; otherwise it runs the full suite."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('test_select', SCRIPTS/'test-select.py')
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)

SUITE = '''#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# a comment
node --test workspace/a/one.test.mjs workspace/a/two.test.mjs
node --test \\
 workspace/b/three.test.mjs \\
 workspace/b/four.test.cjs
python3 scripts/tests/test_alpha.py
python3 os/tests/test_beta.py
JAVA_TESTS="$(mktemp -d)"
trap 'rm -rf "$JAVA_TESTS"' EXIT
for pair in A:x B:y; do
 name="${pair%%:*}"
 java -m jdk.compiler/com.sun.tools.javac.Main -d "$JAVA_TESTS/$name" "os/c/$name.java"
done
java -m jdk.compiler/com.sun.tools.javac.Main -d "$JAVA_TESTS/D" \\
 os/d/D.java os/tests/DTest.java
java -cp "$JAVA_TESTS/D" dev.DTest
'''


class Parse(unittest.TestCase):
    def test_units_follow_test_local_order_and_grouping(self):
        u = ts.units(SUITE)
        self.assertEqual([x['commands'][0].split()[0:2] for x in u],
                         [['node', '--test'], ['node', '--test'], ['python3', 'scripts/tests/test_alpha.py'],
                          ['python3', 'os/tests/test_beta.py'], ['for', 'pair'], ['java', '-m']])
        self.assertEqual(u[1]['paths'], ['workspace/b/four.test.cjs', 'workspace/b/three.test.mjs'])
        self.assertEqual(len(u[5]['commands']), 2, 'javac and its java run are one unit')
        self.assertEqual(u[4]['commands'][-1], 'done')
        self.assertTrue(u[5]['java'] and not u[2]['java'])

    def test_the_real_suite_keeps_every_command_exactly_once(self):
        text = ts.SUITE.read_text()
        lines = text.splitlines()
        start = next(i for i, l in enumerate(lines) if l.strip() == 'cd "$ROOT"') + 1
        body, buf = [], ''
        for line in lines[start:]:
            if line.rstrip().endswith('\\'):
                buf += line.rstrip()[:-1].rstrip() + ' '
                continue
            body.append((buf + line).strip())
            buf = ''
        expected = [l for l in body if l and not l.startswith('#') and not l.startswith(('JAVA_TESTS=', 'trap '))]
        got = [c for u in ts.units() for c in u['commands']]
        self.assertEqual(got, expected)
        self.assertEqual(len({u['key'] for u in ts.units()}), len(ts.units()), 'unit keys are unique')


class Select(unittest.TestCase):
    def setUp(self):
        self.units = ts.units(SUITE)
        self.map = {'units': {u['key']: {'inputs': ins} for u, ins in zip(self.units, [
            ['workspace/a/one.test.mjs', 'workspace/a/lib.mjs'],
            ['workspace/b/three.test.mjs', 'workspace/b/four.test.cjs', 'workspace/b/shared.json'],
            ['scripts/tests/test_alpha.py', 'scripts/alpha.py', 'scripts/receipts.py'],
            ['os/tests/test_beta.py', 'os/beta.py', 'scripts/receipts.py'],
            ['os/c/A.java', 'os/c/B.java'],
            ['os/d/D.java', 'os/tests/DTest.java']])}}

    def pick(self, *files):
        return ts.select(files, self.units, self.map)

    def test_a_claimed_change_selects_only_its_units(self):
        r = self.pick('scripts/alpha.py')
        self.assertEqual((r['mode'], r['units']), ('targeted', [2]))

    def test_a_shared_dependency_selects_every_user(self):
        self.assertEqual(self.pick('scripts/receipts.py')['units'], [2, 3])

    def test_command_named_paths_count_even_if_not_traced(self):
        self.assertEqual(self.pick('workspace/a/two.test.mjs')['units'], [0])

    def test_an_unclaimed_code_file_runs_the_full_suite(self):
        r = self.pick('scripts/alpha.py', 'scripts/new_helper.py')
        self.assertEqual((r['mode'], r['reason']['unclaimedCode']), ('full', ['scripts/new_helper.py']))
        self.assertEqual(r['units'], list(range(6)))

    def test_suite_tool_workflow_and_package_changes_run_the_full_suite(self):
        for f in ('scripts/test-local.sh', 'scripts/test-select.py', 'scripts/test-select-deps.json',
                  '.github/workflows/app-tests.yml', 'workspace/rose-agent/server/package-lock.json'):
            with self.subTest(f):
                self.assertEqual(self.pick(f)['mode'], 'full')

    def test_unclaimed_documents_select_nothing(self):
        r = self.pick('docs/VERIFY.md', 'README.md')
        self.assertEqual((r['mode'], r['units'], r['ignored']), ('targeted', [], ['README.md', 'docs/VERIFY.md']))

    def test_a_unit_missing_from_the_map_is_always_selected(self):
        del self.map['units'][self.units[3]['key']]
        self.assertEqual(self.pick('scripts/alpha.py')['units'], [2, 3])


class RealMap(unittest.TestCase):
    def test_checked_in_map_names_tracked_files_only(self):
        dep_map = json.loads(ts.MAP.read_text())
        files = ts.tracked()
        for key, entry in dep_map['units'].items():
            with self.subTest(entry['command'][:60]):
                self.assertTrue(entry['inputs'])
                self.assertLessEqual(set(entry['inputs']), files)

    def test_every_test_file_in_the_suite_is_an_input_of_its_unit(self):
        dep_map = json.loads(ts.MAP.read_text())['units']
        for unit in ts.units():
            entry = dep_map.get(unit['key'])
            if entry is None:
                continue   # newer than the map: always selected
            tests = [p for p in unit['paths'] if '/tests/' in p or p.endswith(('.test.mjs', '.test.cjs'))]
            with self.subTest(unit['commands'][0][:60]):
                self.assertLessEqual(set(tests) & ts.tracked(), set(entry['inputs']) | set(unit['paths']))


class Run(unittest.TestCase):
    def test_run_executes_only_chosen_units_in_order_and_stops_at_failure(self):
        units = [{'id': 0, 'commands': ['echo a >> "$OUT"'], 'java': False},
                 {'id': 1, 'commands': ['echo b >> "$OUT"; false'], 'java': False},
                 {'id': 2, 'commands': ['echo c >> "$OUT"'], 'java': False}]
        with tempfile.NamedTemporaryFile('r') as out:
            import os
            os.environ['OUT'] = out.name
            try:
                status, record = ts.run_units({0, 1, 2}, units)
            finally:
                del os.environ['OUT']
            self.assertEqual((status, [r['id'] for r in record], Path(out.name).read_text()), (1, [0, 1], 'a\nb\n'))


if __name__ == '__main__':
    unittest.main()
