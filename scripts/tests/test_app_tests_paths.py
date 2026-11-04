"""app-tests.yml runs scripts/test-local.sh, the full offline suite, only for the paths its two triggers list. A change to
a file a suite unit reads must re-run that unit somewhere, or a test it breaks stays unrun until an unrelated change
triggers the suite (#543: os/tests/test_rose_edge_shell.py reads scripts/host-reconcile.py, which no trigger listed, so
main's test-local.sh went red unseen).

This guard covers the reads it can see by name. A suite unit is a Python unit test-local.sh runs (`python3 [-flags]
<unit>.py`), or a node test or Java test class it names. Its reads are each repository path its text names, and each
path literal in it ('rose-verify.py', '../../../scripts/rose-verify.py') that resolves to a repository file from the
root, the unit's directory, its parent or scripts/. Each file test-local.sh names is read by the suite itself. A change
to any of these files has to re-run every unit that reads it. app-tests re-runs them all when its triggers list the
file. Another workflow re-runs only what it runs on every pull_request it triggers for (a step's own `python3
[-flags] <unit>` command, or the whole suite, in a job and step with no condition), and only when its pull_request
filter takes the file. A trigger or step it cannot read exactly earns no credit. Reads it cannot see (an import, a
glob, a path joined from several parts) are outside it, and a name that is not a read (prose, a synthetic file) costs
at most one unneeded trigger. It reads every workflow, so app-tests lists them all and a change to any re-runs it."""
from pathlib import Path
import posixpath
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT/'.github/workflows'
WORKFLOW = WORKFLOWS/'app-tests.yml'
SUITE = 'scripts/test-local.sh'
EXT = r'(?:py|mjs|cjs|js|java|sh|json|xml|mk|patch|yml|yaml)'
# A repository path as the suite and its units spell one: relative, with a directory and a file extension.
NAMED = re.compile(rf'(?<![\w$/.-])(\.?[\w-]+/[\w/.-]+\.{EXT})(?![\w/.-])')
# A string literal that is a whole path, bare or relative: how units name the module or script they load.
LITERAL = re.compile(rf'''(['"`])((?:\.\.?/)*(?:[\w.-]+/)*[\w.-]+\.{EXT})\1''')
PYTHON_UNIT = re.compile(r'(?m)^\s*python3(?:[ \t]+-[A-Za-z]+)*[ \t]+([\w/.-]+\.py)(?=\s|$)')
OTHER_UNIT = re.compile(r'(?:\.test\.[mc]?js|Test\.java)$')

def _item(raw):
    value = re.sub(r'\s+#.*$', '', raw).strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in '\'"':
        value = value[1:-1]
    if not value or re.search(r'[?+\[\]]', value) or '!' in value[1:]:
        raise AssertionError(f'a path pattern this guard does not evaluate: {raw.strip()!r}')
    return value

def trigger_paths(text, event):
    """The paths filter of `event` under the workflow's `on:`, in order; None when it has none (every change triggers
    it). Only what this reads exactly counts: a paths list (flow or block, any quoting, comments and blank lines
    anywhere) and a branches list that holds main. Anything else raises AssertionError: no such trigger, another key
    (types, paths-ignore, branches without main), a line it does not parse, or a pattern with ?, +, [ or ]."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if re.match(r'^on:[ \t]*(#.*)?$', line)), None)
    if start is None:
        raise AssertionError('no on: block')
    block = []
    for line in lines[start + 1:]:
        if line.strip() and not line.lstrip().startswith('#') and not line.startswith(' '):
            break
        block.append(line)
    head = next((i for i, line in enumerate(block) if re.match(rf'^  {event}:[ \t]*(#.*)?$', line)), None)
    if head is None:
        raise AssertionError(f'no {event} trigger')
    body = []
    for line in block[head + 1:]:
        if line.strip() and not line.lstrip().startswith('#') and not line.startswith('    '):
            break
        body.append(line)
    filters, key, flow = {}, None, None
    for line in body:
        content = line.strip()
        if flow is not None:
            flow += ' ' + re.sub(r'\s+#.*$', '', content)
        elif not content or content.startswith('#'):
            continue
        elif re.match(r'^    [\w-]+:', line):
            key, _, value = content.partition(':')
            value = re.sub(r'(^|\s+)#.*$', '', value).strip()
            if key in filters or key not in ('paths', 'branches'):
                raise AssertionError(f'{event}: {key} is not a filter this guard reads')
            filters[key] = []
            if value.startswith('['):
                flow = value
            elif value:
                raise AssertionError(f'{event}: {key} is not a list')
        elif key is not None and re.match(r'^      +- ', line):
            filters[key].append(_item(content[2:]))
        else:
            raise AssertionError(f'{event}: a line this guard does not read: {content!r}')
        if flow is not None and flow.rstrip().endswith(']'):
            filters[key] = [_item(raw) for raw in flow.strip()[1:-1].split(',') if raw.strip()]
            flow = None
    if flow is not None:
        raise AssertionError(f'{event}: an unterminated flow list')
    if 'branches' in filters and 'main' not in filters['branches']:
        raise AssertionError(f'{event}: branches without main')
    return filters.get('paths')

def glob_regex(pattern):
    """A path-filter glob as a regex: ** crosses directories (**/ also matches none), * stays within one."""
    return ''.join({'**/': '(?:.*/)?', '**': '.*', '*': '[^/]*'}.get(token) or re.escape(token)
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

def run_lines(text):
    """The command lines a workflow runs on every pull_request it triggers for: the run lines of steps with no if:,
    continue-on-error:, working-directory:, shell:, heredoc or `set +e`, in jobs with no if:, needs:, strategy:,
    continue-on-error:, container: or defaults:, in a workflow with no defaults:; comments dropped. A layout other
    than jobs at two spaces and steps at six yields nothing."""
    if re.search(r'^defaults:', text, re.M):
        return []
    jobs = re.search(r'^jobs:[ \t]*\n(.*?)(?=^[^\s#]|\Z)', text, re.M | re.S)
    lines = []
    for job in re.split(r'(?m)^  (?=[\w-]+:)', jobs.group(1) if jobs else '')[1:]:
        steps = re.search(r'^    steps:[ \t]*\n', job, re.M)
        if not steps or re.search(r'^    (?:if|needs|strategy|continue-on-error|container|defaults):',
                                  job[:steps.start()], re.M):
            continue
        for step in re.split(r'(?m)^      - ', job[steps.end():])[1:]:
            if re.search(r'(?:\A|^        )(?:if|continue-on-error|working-directory|shell):', step, re.M):
                continue
            run = re.search(r'(?:\A|^        )run:[ \t]*(.*?)(?=^        [\w-]+:|\Z)', step, re.M | re.S)
            if not run or re.search(r'(?<!<)<<(?!<)|^\s*set\s+\+e', run.group(1), re.M):
                continue
            for line in run.group(1).splitlines():
                line = line.strip()
                if line and line not in ('|', '>') and not line.startswith('#'):
                    lines.append(line)
    return lines

def runs(lines, unit):
    """Whether these command lines run this unit so that its failure fails the step (GitHub's bash runs with -e and
    without pipefail): its own `python3 [-flags] <unit>`, or the whole suite, at the start of a line, after && or ;,
    or as a `NAME="$(...)"` assignment; with no pipe, and no || unless what follows exits non-zero."""
    command = rf'(?:python3(?:[ \t]+-[A-Za-z]+)*[ \t]+{re.escape(unit)}|bash[ \t]+{re.escape(SUITE)})(?![\w/.-])'
    for line in lines:
        head, bar, rest = line.partition('||')
        if bar and not re.fullmatch(r'\s*(?:exit\s+[1-9]\d*|\{[^{}]*;\s*exit\s+[1-9]\d*\s*;?\s*\})\s*', rest):
            continue
        if '|' in head:
            continue
        if re.search(rf'(?:^|(?:&&|;)[ \t]*|^[A-Za-z_]\w*="?\$\(){command}', head):
            return True
    return False

def candidates(unit, literal):
    """The repository paths a path literal in `unit` can mean: from the root, the unit's directory, its parent or
    scripts/; never one outside the repository."""
    here = posixpath.dirname(unit)
    paths = (posixpath.normpath(posixpath.join(base, literal)) for base in ('', here, posixpath.dirname(here), 'scripts'))
    return {path for path in paths if path != '..' and not path.startswith('../')}

def resolve(unit, literal):
    """The repository files a path literal in `unit` names."""
    return {path for path in candidates(unit, literal) if (ROOT/path).is_file()}

def suite_units():
    """(Python units, other units) of test-local.sh: what it runs with python3, and the node tests and Java test
    classes it names."""
    text = (ROOT/SUITE).read_text()
    python = {u for u in PYTHON_UNIT.findall(text) if (ROOT/u).is_file()}
    other = {p for p in NAMED.findall(text) if OTHER_UNIT.search(p) and (ROOT/p).is_file()}
    return python, other

def names(unit):
    """The repository files `unit` reads by name."""
    text = (ROOT/unit).read_text(errors='replace')
    found = {path for path in NAMED.findall(text) if (ROOT/path).is_file()}
    for _, literal in LITERAL.findall(text):
        found |= resolve(unit, literal)
    return found

def suite_reads():
    """{path: readers}: each file test-local.sh names is read by SUITE, or by itself when it is a Python unit; each
    file a unit names is read by that unit, by SUITE for a node or Java unit (only the suite runs those)."""
    python, other = suite_units()
    reads = {}
    for path in NAMED.findall((ROOT/SUITE).read_text()):
        if (ROOT/path).is_file():
            reads.setdefault(path, set()).add(path if path in python else SUITE)
    for unit in sorted(python | other):
        for path in names(unit):
            reads.setdefault(path, set()).add(unit if unit in python else SUITE)
    return reads

def other_triggers():
    """(run lines, pull_request paths) of each other workflow with a pull_request trigger this guard reads."""
    found = []
    for workflow in sorted(WORKFLOWS.glob('*.y*ml')):
        if workflow != WORKFLOW:
            text = workflow.read_text()
            try:
                found.append((run_lines(text), trigger_paths(text, 'pull_request')))
            except AssertionError:
                pass  # no pull_request trigger it reads exactly: it re-runs nothing here
    return found

def unretested(reads, app, others):
    """The paths whose change does not re-run every unit that reads them."""
    def reruns(path, reader):
        return any(runs(lines, reader) and covered(path, paths) for lines, paths in others)
    return sorted(path for path, readers in reads.items()
                  if not covered(path, app) and not (readers and all(reruns(path, r) for r in readers)))

class AppTestsPathsTest(unittest.TestCase):
    def test_a_change_to_a_file_the_suite_names_reruns_every_unit_that_reads_it(self):
        reads = suite_reads()
        self.assertGreater(len(reads), 200, 'test-local.sh and its units name their files')
        self.assertIn('os/tests/test_rose_edge_shell.py', reads['scripts/host-reconcile.py'])  # #543's missed reader
        self.assertIn('scripts/tests/test_systemui_component_update.py', reads['scripts/systemui-component-update.py'])
        self.assertIn(SUITE, reads['scripts/rose-verify.py'])  # a node test's '../../../scripts/rose-verify.py'
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
        self.assertEqual([p for p in listed if not re.search(r'[*!]', p) and not (ROOT/p).exists()], [])
        workflows = [w.relative_to(ROOT).as_posix() for w in sorted(WORKFLOWS.glob('*.y*ml'))]
        self.assertGreater(len(workflows), 10)
        self.assertEqual([w for w in workflows if not covered(w, listed)], [])

    def test_filters_are_read_exactly_or_refused(self):
        flow = "on:\n  pull_request:\n    paths: ['os/**', \"scripts/host-*.py\"]\n  push:\n    branches: [main]\njobs:\n"
        self.assertEqual(trigger_paths(flow, 'pull_request'), ['os/**', 'scripts/host-*.py'])
        self.assertIsNone(trigger_paths(flow, 'push'))
        block = ("on:\n  # who\n  pull_request:\n  # what\n    paths:\n      - 'a/**'\n\n        # why\n      - b.py  # note\n"
                 "      - '!a/x.py'\n  push:\n    paths:\n      - 'c/**'\njobs:\n")
        patterns = trigger_paths(block, 'pull_request')
        self.assertEqual(patterns, ['a/**', 'b.py', '!a/x.py'])
        self.assertEqual(trigger_paths("on:\n  pull_request:\n    paths: [\n      'a/**',\n      b.py ]\njobs:\n",
                                       'pull_request'), ['a/**', 'b.py'])
        self.assertIsNone(trigger_paths("on:\n  pull_request:\n  push:\n    branches: [main]\njobs:\n", 'pull_request'))
        for refused in ("on:\n  push:\n    branches: [main]\njobs:\n",
                        "on:\n  pull_request:\n    paths-ignore: ['docs/**']\njobs:\n",
                        "on:\n  pull_request:\n    types: [labeled]\n    paths: ['a/**']\njobs:\n",
                        "on:\n  pull_request:\n    branches: [release]\n    paths: ['a/**']\njobs:\n",
                        "on:\n  pull_request:\n    paths: ['a/*.js?']\njobs:\n",
                        "on:\n  pull_request:\n    paths: ['a/[ab].py']\njobs:\n",
                        "on:\n  pull_request:\n    paths:\n      - 'a/**'\n    oops\njobs:\n",
                        "on:\n  pull_request:\n    paths: ['a/!b.py']\njobs:\n",
                        "on:\n  pull_request: {}\njobs:\n"):
            with self.assertRaises(AssertionError, msg=refused):
                trigger_paths(refused, 'pull_request')
        self.assertEqual([covered(p, patterns) for p in ('a/y/z.py', 'a/x.py', 'b.py', 'c/b.py')], [True, False, True, False])
        self.assertEqual([covered(p, ['scripts/host-*.py']) for p in
                          ('scripts/host-reconcile.py', 'scripts/host-a/b.py', 'scripts/x/host-a.py')], [True, False, False])
        self.assertTrue(covered('a/b.py', ['a/**/b.py']))
        self.assertTrue(covered('anything', None))

    def test_only_an_unconditional_run_counts(self):
        unit = 'scripts/tests/t.py'
        def workflow(job='', step='', command=f'python3 {unit}'):
            return (f"on:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n{job}    steps:\n"
                    f"      - uses: actions/checkout@v4\n      - name: unit\n{step}        run: |\n          {command}\n")
        self.assertTrue(runs(run_lines(workflow()), unit))
        self.assertTrue(runs(run_lines(workflow(command=f'python3 -I {unit} -v')), unit))
        self.assertTrue(runs(run_lines(workflow(command=f'python3 a.py && python3 {unit}')), unit))
        self.assertTrue(runs(run_lines(workflow(command='bash scripts/test-local.sh')), unit))
        self.assertTrue(runs(run_lines(f"jobs:\n  t:\n    steps:\n      - run: python3 {unit}\n"), unit))
        self.assertTrue(runs(run_lines(workflow(command=f'python3 {unit} || exit 1')), unit))
        strict = (f'out="$(python3 {unit} -v 2>&1)" || {{ printf \'%s\\n\' "$out"; exit 1; }}\n'
                  '          if grep -q skipped <<<"$out"; then exit 1; fi')
        self.assertTrue(runs(run_lines(workflow(command=strict)), unit))
        for lines in (run_lines(workflow(job="    if: github.event_name == 'workflow_dispatch'\n")),
                      run_lines(workflow(job='    needs: build\n')),
                      run_lines(workflow(job='    strategy:\n      matrix:\n        x: [1]\n')),
                      run_lines(workflow(step="        if: github.event_name == 'push'\n")),
                      run_lines(workflow(step='        continue-on-error: true\n')),
                      run_lines(workflow(step='        working-directory: os\n')),
                      run_lines(workflow(command=f'# python3 {unit}')),
                      run_lines(workflow(command=f'python3 {unit} || true')),
                      run_lines(workflow(command=f'python3 {unit} || echo failed; exit 1')),
                      run_lines(workflow(command=f'python3 {unit} | tee log')),
                      run_lines(workflow(command=f'echo "$(python3 {unit})"')),
                      run_lines(workflow(command=f'set +e\n          python3 {unit}')),
                      run_lines(workflow(command=f'cat > x.sh <<EOF\n          python3 {unit}\n          EOF')),
                      run_lines(workflow(command=f'echo python3 {unit}')),
                      run_lines(workflow(command=f'python3 {unit}.bak')),
                      run_lines("defaults:\n  run:\n    working-directory: os\n" + workflow()),
                      [f"- 'scripts/tests/t.py'"]):
            self.assertFalse(runs(lines, unit), lines)

    def test_every_reader_is_rerun_and_literals_resolve_as_units_load_them(self):
        others = [(['python3 u1.py'], ['f.json'])]
        self.assertEqual(unretested({'f.json': {'u1.py'}}, [], others), [])
        self.assertEqual(unretested({'f.json': {'u1.py', 'u2.py'}}, [], others), ['f.json'])  # every reader
        self.assertEqual(unretested({'f.json': {SUITE}}, [], others), ['f.json'])  # one unit's run skips the shell
        self.assertEqual(unretested({'f.json': set()}, [], others), ['f.json'])
        self.assertEqual(unretested({'f.json': {SUITE, 'u2.py'}}, [], [(['bash scripts/test-local.sh'], ['f.json'])]), [])
        self.assertEqual(unretested({'f.json': {SUITE, 'u2.py'}}, ['*.json'], others), [])
        self.assertEqual(resolve('scripts/tests/test_rose_verify.py', 'rose-verify.py'), {'scripts/rose-verify.py'})
        self.assertEqual(resolve('workspace/rose-agent/tests/x.test.mjs', '../../../scripts/rose-verify.py'),
                         {'scripts/rose-verify.py'})
        self.assertEqual(resolve('os/tests/test_x.py', 'scripts/host-reconcile.py'), {'scripts/host-reconcile.py'})
        self.assertEqual(candidates('os/tests/test_x.py', '../../../etc/passwd.py'), set())
        self.assertEqual(candidates('os/tests/test_x.py', '../x.py'), {'x.py', 'os/x.py'})
        self.assertEqual(LITERAL.findall("SCRIPTS/'a-b.py', \"../c/d.json\", `e.mjs`, 'not a path.py'"),
                         [("'", 'a-b.py'), ('"', '../c/d.json'), ('`', 'e.mjs')])
        self.assertEqual(PYTHON_UNIT.findall('python3 -I w/t.py\n  python3 s/u.py -v\npython3 -m unittest x\n'),
                         ['w/t.py', 's/u.py'])

if __name__ == '__main__':
    unittest.main()
