"""The shared shape of .github/workflows/os-release.yml, read from the file itself (Release-owned test helper).

Each workflow test still pins its own mode's steps exactly. What every mode shares - the mode choice list, the
dispatch inputs and the environment GitHub derives from them, the controller's and cleanup's builder-ownership
conditions and cleanup's needs - is parsed here instead, so a mode, input or job added for another device never breaks
a test that only checks its own mode's place in them. Every parser refuses (AssertionError) a shape it does not
recognise, so a test never passes on a misread workflow.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT/'.github/workflows/os-release.yml'
MODE_EQ = re.compile(r"inputs\.mode == '([\w-]+)'")
MODE_NE = re.compile(r"inputs\.mode != '([\w-]+)'")
START = 'python3 scripts/release-pipeline.py start-builder'
STOP = 'python3 scripts/release-pipeline.py stop-builder'


def text():
    return PATH.read_text()


def jobs(workflow=None):
    body = (workflow or text()).split('\njobs:\n', 1)[1]
    return dict(re.findall(r'(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)', body))


def inputs(workflow=None):
    """{name: {'type', 'default', 'options'}} of the workflow_dispatch inputs, in declared order."""
    section = (workflow or text()).split('\n    inputs:\n', 1)[1].split('\npermissions:', 1)[0]
    found = {}
    for name, block in re.findall(r'(?m)^      (\w+):\n((?:        .*\n?)*)', section):
        kind = re.search(r'(?m)^        type: (\w+)$', block)
        default = re.search(r'(?m)^        default: (.*)$', block)
        options = re.search(r'(?m)^        options: \[(.*)\]$', block)
        value = default[1].strip() if default else ''
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '\'"':
            value = value[1:-1]
        found[name] = {'type': kind[1] if kind else 'string', 'default': value,
                       'options': [o.strip() for o in options[1].split(',')] if options else None}
    assert found and 'mode' in found, 'no workflow_dispatch inputs found'
    return found


def modes(workflow=None):
    options = inputs(workflow)['mode']['options']
    assert options, 'the mode input has no options list'
    return options


CONCURRENCY = re.compile(r'(?ms)^concurrency:\n(.*?)(?=^\S|\Z)')
KEYED_GROUP = re.compile(r"\$\{\{ \((.+)\) && '([\w-]+)' \|\| '([\w-]+)' \}\}")


def concurrency(workflow=None):
    """The workflow-level concurrency settings {key: value}, comment lines skipped."""
    head = (workflow or text()).split('\njobs:\n', 1)[0]
    block = CONCURRENCY.search(head)
    assert block, 'no workflow-level concurrency'
    return dict(re.findall(r'(?m)^  ([\w-]+): (.+)$', block[1]))


def queues(workflow=None):
    """(default queue, {mode: queue}) of the workflow-level concurrency group: one literal group, or exactly the
    mode-keyed shape `${{ (inputs.mode == 'a' || inputs.mode == 'b') && 'own' || 'default' }}` (os-release.yml's
    hosted Agent modes). Any other expression refuses, so no run's queue is guessed."""
    group = concurrency(workflow).get('group')
    assert group, 'no concurrency group'
    if re.fullmatch(r'[\w-]+', group):
        return group, {}
    keyed = KEYED_GROUP.fullmatch(group)
    assert keyed, f'a concurrency group this reader does not evaluate: {group}'
    modes = _modes_of(keyed[1], MODE_EQ, ' || ')
    assert keyed[2] != keyed[3], 'a mode-keyed group must name two queues'
    return keyed[3], {mode: keyed[2] for mode in sorted(modes)}


def queue(mode, workflow=None):
    """The concurrency group a dispatch of MODE joins."""
    default, keyed = queues(workflow)
    return keyed.get(mode, default)


def dispatch_env(workflow=None, **change):
    """The workflow-level env exactly as GitHub derives it for a dispatch that leaves every input at its declared
    default (a boolean renders as 'true'/'false'; `inputs.x || 'v'` falls back on an empty or false value), then CHANGE.
    Step-level env (for example the guard's PROMOTE) is the caller's to add."""
    workflow = workflow or text()
    declared = inputs(workflow)
    block = workflow.split('\nenv:\n', 1)[1].split('\njobs:\n', 1)[0]
    env = {}
    for name, expression in re.findall(r'(?m)^  ([A-Z][A-Z0-9_]*): (.+)$', block):
        # latest-draft is an input alias for the normal allocator, with promotion disabled.
        if name == 'DRAFT_ONLY' and expression == "${{ inputs.mode == 'latest-draft' }}":
            env[name] = 'true' if declared['mode']['default'] == 'latest-draft' else 'false'
            continue
        if name == 'MODE' and expression == "${{ inputs.mode == 'latest-draft' && 'latest-beta' || inputs.mode }}":
            env[name] = 'latest-beta' if declared['mode']['default'] == 'latest-draft' else declared['mode']['default']
            continue
        match = re.fullmatch(r"\$\{\{ inputs\.(\w+)(?: \|\| '([^']*)')? \}\}", expression.strip())
        assert match and match[1] in declared, f'unrecognised workflow env {name}: {expression}'
        value = declared[match[1]]['default']
        if declared[match[1]]['type'] == 'boolean':
            value = 'true' if value == 'true' else 'false'
        if match[2] is not None and value in ('', 'false'):
            value = match[2]
        env[name] = value
    env.update(change)
    return env


def _condition(job, command, owned):
    """The if: of the one step whose run is exactly COMMAND (owned=False) or COMMAND with ownership arguments."""
    steps = re.findall(r'(?m)^      - if: (.+)\n(?:        env:\n(?:          .*\n)+)?        run: (.+)$', job)
    picked = [cond for cond, run in steps if (run.startswith(command + ' --') if owned else run == command)]
    assert len(picked) == 1, f'expected one {"owned" if owned else "plain"} step running {command}, found {len(picked)}'
    return picked[0]


def _modes_of(condition, pattern, joiner):
    """The modes of a condition that is exactly `inputs.mode OP 'm'` terms joined by JOINER, and nothing else."""
    names = pattern.findall(condition)
    op = '==' if pattern is MODE_EQ else '!='
    assert names and condition.strip() == joiner.join(f"inputs.mode {op} '{n}'" for n in names), \
        f'unrecognised condition {condition}'
    return set(names)


def owned_modes(workflow=None):
    """The modes whose controller records builder ownership and whose cleanup stops only a builder it started. The
    five places that name them (plain start and stop exclusions, owned start, ownership record, owned stop) must agree."""
    all_jobs = jobs(workflow)
    controller, cleanup = all_jobs['controller'], all_jobs['cleanup']
    record = re.search(r"(?m)^      - name: Builder ownership\n        id: ownership\n        if: always\(\) && \((.+)\)$", controller)
    assert record, 'no Builder ownership step in the controller'
    places = {
        'controller plain start': _modes_of(_condition(controller, START, False), MODE_NE, ' && '),
        'controller owned start': _modes_of(_condition(controller, START, True), MODE_EQ, ' || '),
        'controller ownership record': _modes_of(record[1], MODE_EQ, ' || '),
        'cleanup plain stop': _modes_of(_condition(cleanup, STOP, False), MODE_NE, ' && '),
        'cleanup owned stop': _modes_of(_condition(cleanup, STOP, True), MODE_EQ, ' || '),
    }
    first = places['controller owned start']
    assert all(value == first for value in places.values()), f'ownership conditions disagree: {places}'
    return first


def cleanup_needs(workflow=None):
    match = re.search(r'(?m)^    needs: \[(.+)\]$', jobs(workflow)['cleanup'])
    assert match, 'cleanup has no needs list'
    return [name.strip() for name in match[1].split(',')]


def main_check_exempt_modes(workflow=None):
    """The modes the guard's exact-commit-on-main check skips: its one `[[ "$MODE" == a || ... ]] || git merge-base
    --is-ancestor` line (a bare dispatch of these modes carries no candidate)."""
    guard = jobs(workflow)['guard']
    found = re.findall(r'(?m)^ +\[\[ ((?:"\$MODE" == [\w-]+)(?: \|\| "\$MODE" == [\w-]+)*) \]\] \|\| git merge-base --is-ancestor', guard)
    assert len(found) == 1, f'expected one exempt-mode line before the exact-commit check, found {len(found)}'
    return set(re.findall(r'"\$MODE" == ([\w-]+)', found[0]))


def owned_runs(workflow=None):
    """The run lines of the three owned steps: the controller's owned start and ownership record, cleanup's owned stop
    (their conditions are checked by owned_modes())."""
    all_jobs = jobs(workflow)
    owned_modes(workflow)
    controller, cleanup = all_jobs['controller'], all_jobs['cleanup']
    steps = re.findall(r'(?m)^      - if: (.+)\n(?:        env:\n(?:          .*\n)+)?        run: (.+)$', controller + cleanup)
    start = [run for _, run in steps if run.startswith(START + ' --')]
    stop = [run for _, run in steps if run.startswith(STOP + ' --')]
    record = re.findall(r"(?m)^      - name: Builder ownership\n        id: ownership\n        if: always\(\) && \(.+\)\n"
                        r"        run: (.+)$", controller)
    assert len(start) == len(stop) == len(record) == 1, 'expected one owned start, ownership record and owned stop'
    return {'start': start[0], 'record': record[0], 'stop': stop[0]}
