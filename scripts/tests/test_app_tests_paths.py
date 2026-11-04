"""app-tests.yml runs scripts/test-local.sh, the full offline suite, only for the paths its two triggers list. A change to
a file a suite unit reads must re-run that unit somewhere, or a test it breaks stays unrun until an unrelated change
triggers the suite (#543: os/tests/test_rose_edge_shell.py reads scripts/host-reconcile.py, which no trigger listed, so
main's test-local.sh went red unseen).

This guard covers the reads it can see by name: each repository file test-local.sh names (its units, the sources it
compiles) and each one a Python unit of it names in its text. A change to such a file has to re-run every unit that
reads it. app-tests re-runs them all when its triggers list the file. Another workflow re-runs what it runs itself (a
Python unit by a literal `python3 <unit>`, not a mere listing; everything by running the suite), and only when its
pull_request filter takes the file. Reads it cannot see (an import, a glob, a path built from parts) are outside it,
and a name in prose costs at most one unneeded trigger. It reads every workflow's triggers, so app-tests lists them all
and a change to any of them re-runs it."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT/'.github/workflows'
WORKFLOW = WORKFLOWS/'app-tests.yml'
SUITE = 'scripts/test-local.sh'
# A repository path as the suite and its units spell one: relative, with a directory and a file extension.
NAMED = re.compile(r'(?<![\w$/.-])(\.?[\w-]+/[\w/.-]+\.(?:py|mjs|cjs|js|java|sh|json|xml|mk|patch|yml|yaml))(?![\w/.-])')

def trigger_paths(text, event):
    """The paths filter of `event` under the workflow's `on:`, in order (a block or flow list, any quoting); None when
    `event` has no paths filter, so every change triggers it. AssertionError when the workflow has no such trigger or a
    filter this reader does not parse."""
    on = re.search(r'^on:[ \t]*\n(.*?)(?=^\S|\Z)', text, re.M | re.S)
    block = re.search(rf'^  {event}:[ \t]*\n(.*?)(?=^  \S|\Z)', on.group(1) if on else '', re.M | re.S)
    if not block:
        raise AssertionError(f'no {event} trigger')
    body = block.group(1)
    flow = re.search(r'^    paths:[ \t]*\[(.*?)\]', body, re.M | re.S)
    if flow:
        items = flow.group(1).split(',')
    else:
        listed = re.search(r'^    paths:[ \t]*\n((?:      (?:- .*|#.*)\n?)+)', body, re.M)
        if not listed:
            if re.search(r'^    paths', body, re.M):
                raise AssertionError(f'a {event} paths filter this guard does not parse')
            return None
        items = re.findall(r'^      - (.*)$', listed.group(1), re.M)
    return [re.sub(r'\s+#.*', '', item).strip().strip('\'"') for item in items if item.strip()]

def glob_regex(pattern):
    """A path-filter glob as a regex: ** crosses directories (**/ also matches none), * and ? stay within one."""
    return ''.join({'**/': '(?:.*/)?', '**': '.*', '*': '[^/]*', '?': '[^/]'}.get(token) or re.escape(token)
                   for token in re.findall(r'\*\*/|\*\*|.', pattern, re.S))

def covered(path, patterns):
    """Whether a change to `path` triggers this filter (None: every change does). A later `!` pattern excludes."""
    if patterns is None:
        return True
    hit = False
    for pattern in patterns:
        if pattern.startswith('!'):
            hit = hit and not re.fullmatch(glob_regex(pattern[1:]), path)
        else:
            hit = hit or re.fullmatch(glob_regex(pattern), path) is not None
    return hit

def runs(text, unit):
    """Whether a workflow runs this unit, not merely lists it: the unit's own `python3 <unit>`, or the whole suite."""
    return re.search(rf'(?:python3 {re.escape(unit)}|bash {re.escape(SUITE)})(?![\w/.-])', text) is not None

def suite_reads():
    """{path: readers} for each existing file test-local.sh or one of its Python units names. A unit reads itself and
    each file its text names; a file only the shell names (a compiled source, an argument) is read by SUITE."""
    text = (ROOT/SUITE).read_text()
    units = {u for u in re.findall(r'(?m)^\s*python3 ([\w/.-]+\.py)(?=\s|$)', text) if (ROOT/u).is_file()}
    reads = {}
    for path in NAMED.findall(text):
        if (ROOT/path).is_file():
            reads.setdefault(path, set()).add(path if path in units else SUITE)
    for unit in sorted(units):
        for path in NAMED.findall((ROOT/unit).read_text()):
            if (ROOT/path).is_file():
                reads.setdefault(path, set()).add(unit)
    return reads

def other_triggers():
    """(text, pull_request paths) of each other workflow with a pull_request trigger this guard parses."""
    found = []
    for workflow in sorted(WORKFLOWS.glob('*.y*ml')):
        if workflow != WORKFLOW:
            text = workflow.read_text()
            try:
                found.append((text, trigger_paths(text, 'pull_request')))
            except AssertionError:
                pass  # no pull_request trigger it reads: it re-runs nothing here
    return found

def unretested(reads, app, others):
    """The paths whose change does not re-run every unit that reads them."""
    def reruns(path, reader):
        return any(runs(text, reader) and covered(path, paths) for text, paths in others)
    return sorted(path for path, readers in reads.items()
                  if not covered(path, app) and not (readers and all(reruns(path, r) for r in readers)))

class AppTestsPathsTest(unittest.TestCase):
    def test_a_change_to_a_file_the_suite_names_reruns_every_unit_that_reads_it(self):
        reads = suite_reads()
        self.assertGreater(len(reads), 200, 'test-local.sh and its units name their files')
        self.assertIn('os/tests/test_rose_edge_shell.py', reads['scripts/host-reconcile.py'])  # #543's missed reader
        self.assertTrue(any(p.startswith('.github/workflows/') for p in reads), 'units name workflow files too')
        self.assertTrue(any(SUITE in readers for readers in reads.values()), 'the shell names files too')
        app = trigger_paths(WORKFLOW.read_text(), 'pull_request')
        self.assertEqual(unretested(reads, app, other_triggers()), [],
            'list these in both app-tests.yml triggers, or run their readers where a trigger takes them')

    def test_the_two_triggers_list_the_same_paths(self):
        text = WORKFLOW.read_text()
        self.assertEqual(trigger_paths(text, 'pull_request'), trigger_paths(text, 'push'))

    def test_each_listed_path_exists_and_every_workflow_change_reruns_the_suite(self):
        listed = trigger_paths(WORKFLOW.read_text(), 'pull_request')
        self.assertEqual([p for p in listed if not re.search(r'[*?!]', p) and not (ROOT/p).exists()], [])
        workflows = [w.relative_to(ROOT).as_posix() for w in sorted(WORKFLOWS.glob('*.y*ml'))]
        self.assertGreater(len(workflows), 10)
        self.assertEqual([w for w in workflows if not covered(w, listed)], [])

    def test_filters_are_read_as_github_applies_them_and_only_a_run_counts(self):
        flow = "on:\n  pull_request:\n    paths: ['os/**', \"scripts/host-*.py\"]\n  push:\n    branches: [main]\njobs:\n"
        self.assertEqual(trigger_paths(flow, 'pull_request'), ['os/**', 'scripts/host-*.py'])
        block = ("on:\n  pull_request:\n    paths:\n      - 'a/**'\n      # why\n      - b.py  # note\n"
                 "      - '!a/x.py'\n  push:\n    paths:\n      - 'c/**'\njobs:\n")
        patterns = trigger_paths(block, 'pull_request')
        self.assertEqual(patterns, ['a/**', 'b.py', '!a/x.py'])
        self.assertIsNone(trigger_paths("on:\n  pull_request:\n  push:\n    branches: [main]\njobs:\n", 'pull_request'))
        with self.assertRaises(AssertionError):
            trigger_paths("on:\n  push:\n    branches: [main]\njobs:\n", 'pull_request')
        with self.assertRaises(AssertionError):
            trigger_paths("on:\n  pull_request:\n    paths-ignore: ['docs/**']\njobs:\n", 'pull_request')
        self.assertEqual([covered(p, patterns) for p in ('a/y/z.py', 'a/x.py', 'b.py', 'c/b.py')], [True, False, True, False])
        self.assertEqual([covered(p, ['scripts/host-*.py']) for p in
                          ('scripts/host-reconcile.py', 'scripts/host-a/b.py', 'scripts/x/host-a.py')], [True, False, False])
        self.assertTrue(covered('a/b.py', ['a/**/b.py']))
        self.assertTrue(covered('anything', None))
        unit = 'scripts/tests/t.py'
        self.assertFalse(runs("    paths:\n      - 'scripts/tests/t.py'\n", unit))
        self.assertTrue(runs("      - run: python3 scripts/tests/t.py -v\n", unit))
        self.assertFalse(runs("        run: python3 scripts/tests/t.py.bak\n", unit))
        others = [('run: python3 u1.py', ['f.json'])]
        self.assertEqual(unretested({'f.json': {'u1.py'}}, [], others), [])
        self.assertEqual(unretested({'f.json': {'u1.py', 'u2.py'}}, [], others), ['f.json'])  # every reader
        self.assertEqual(unretested({'f.json': {SUITE}}, [], others), ['f.json'])  # one unit's run skips the shell
        self.assertTrue(runs("        run: bash scripts/test-local.sh\n", unit))
        self.assertEqual(unretested({'f.json': {SUITE, 'u2.py'}}, [], [('run: bash scripts/test-local.sh', ['f.json'])]), [])
        self.assertEqual(unretested({'f.json': set()}, [], others), ['f.json'])
        self.assertEqual(unretested({'f.json': {SUITE, 'u2.py'}}, ['*.json'], others), [])

if __name__ == '__main__':
    unittest.main()
