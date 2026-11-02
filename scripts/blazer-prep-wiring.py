#!/usr/bin/env python3
"""BLAZER-PREP workflow wiring (Release-owned) for os-release.yml mode blazer-prep.

Binds the committed operator inputs of os/build-only/blazer-prep-host.json to the Flavors helper
scripts/blazer-build-only.py, one preparation stage (download, vendor or lunch) or the metadata-only inspect per
dispatch:

  blazer-prep-wiring.py check --stage S
      hosted and on the builder: the recipe loads without drift (the helper's own load), the binding is complete and
      lexically isolated from the production source root, and the stage is a preparation stage. Prints the bounds as
      job outputs: outer_seconds (the helper's own stage timeout + 300 s for preflight and consumption measurement) and
      job_minutes. Reads no host path.
  blazer-prep-wiring.py run --stage S --receipt-dir NEW
      the same check, then exec of exactly `blazer-build-only.py run --stage S --host H --lock L <stage inputs>
      --receipt-dir NEW`. The helper does every host check (symlinks, same inode, collisions, lock, space, bounds).
      For inspect: `blazer-build-only.py inspect --host H --lock L [only the bound sourceRoot/downloadDir/node]
      --candidate-root R --max-entries 200000 --max-seconds 900`, its stdout captured write-once as
      NEW/inspection.json (the helper takes no lock and no receipt directory there, starts no process and writes
      nothing). R is the committed inspectCandidates.rootTemplate with this run's id and attempt: a proposed path, not
      known to exist, never a preparation binding. The runner's own PATH node is always reported by the helper. Without
      committed candidates inspect is unbound (exit 4): there is no lock-only inspection.

Never compile: compile is not a preparation stage and is refused here. Exit status: 0 ok, 2 invalid, 4 unbound (a host
fact is still null).
"""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BINDING = ROOT/'os/build-only/blazer-prep-host.json'
HELPER = ROOT/'scripts/blazer-build-only.py'
OUTER_MARGIN_SECONDS = 300
KILL_AFTER_SECONDS = 30
JOB_MARGIN_MINUTES = 10
STAGE_INPUTS = {'download': ('downloadDir',), 'vendor': ('downloadDir', 'sourceRoot', 'node'), 'lunch': ('sourceRoot',)}
INSPECT_INPUTS = ('sourceRoot', 'downloadDir', 'node')  # each passed only when bound
# inspect: the helper's own bound (its cap is INSPECT_MAX_SECONDS), then an outer timeout and a 20-minute job.
INSPECT = {'maxEntries': 200000, 'maxSeconds': 900, 'outerSeconds': 960, 'jobMinutes': 20}
FLAGS = {'downloadDir': '--download-dir', 'sourceRoot': '--source-root', 'node': '--node'}
KEYS = {'schemaVersion', 'kind', 'basis', 'host', 'lockFrom', 'sourceRoot', 'downloadDir', 'node', 'inspectCandidates'}
CANDIDATE_KEYS = {'rootTemplate', 'verified', 'binding', 'basis'}


class Invalid(Exception):
    pass


class Unbound(Exception):
    pass


def helper():
    spec = importlib.util.spec_from_file_location('blazer_build_only', HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate_root(binding, recipe, production, run_id, attempt):
    """The proposed inspection root for this run: a committed template plus the run id and attempt, never a binding."""
    record = binding['inspectCandidates']
    if record is None:
        raise Unbound('inspect needs committed inspectCandidates (a lock-only inspection is not offered)')
    if (not isinstance(record, dict) or set(record) != CANDIDATE_KEYS or record['verified'] is not False
            or record['binding'] is not False):
        raise Invalid('inspectCandidates must be an exact proposal record with verified=false and binding=false')
    template = record['rootTemplate']
    if (not isinstance(template, str) or template.count('{run_id}') != 1 or template.count('{attempt}') != 1
            or '{' in template.replace('{run_id}', '').replace('{attempt}', '')):
        raise Invalid('inspectCandidates.rootTemplate must hold {run_id} and {attempt} exactly once')
    for name, value in (('run id', run_id), ('attempt', attempt)):
        if not (isinstance(value, str) and value.isdigit() and value[0] != '0' and len(value) <= 20):
            raise Invalid(f'inspect needs a positive numeric {name}')
    path = template.format(run_id=run_id, attempt=attempt)
    boundary = Path(recipe['preparation']['candidateLayout']['ancestorBoundary'])
    if (not path.startswith('/') or os.path.normpath(path) != path or '..' in Path(path).parts
            or boundary not in Path(path).parents):
        raise Invalid(f'the candidate root must be a normalized absolute path under {boundary}')
    from_template = Path(template).parent
    if '{' in str(from_template):
        raise Invalid('only the last component of the candidate root may vary per run')
    return path


def resolve(stage, binding_path=BINDING, root=ROOT, run_id=None, attempt=None):
    """(argv tail without --receipt-dir, outer_seconds, job_minutes) for one preparation stage or inspect. The hosted
    check of inspect validates the candidate template with a sample run (1, 1); run passes the real run id and attempt."""
    h = helper()
    if stage not in (*h.PREPARATION, 'inspect'):
        raise Invalid(f'{stage!r} is not a preparation stage {h.PREPARATION} or inspect')
    try:
        recipe = h.load(root/'os/build-only/blazer.json', root)
    except h.InvalidInput as error:
        raise Invalid(f'recipe: {error}')
    binding = json.loads(Path(binding_path).read_text())
    if set(binding) != KEYS or binding['schemaVersion'] != 1 or binding['kind'] != 'rose-blazer-prep-host':
        raise Invalid('binding is not an exact rose-blazer-prep-host v1 record')
    if binding['lockFrom'] != recipe['preparation']['lockFrom']:
        raise Invalid('binding lock source differs from the recipe lockFrom')
    if binding['host'] != 'immortal-os-builder' or not h.HOST_LABEL.fullmatch(binding['host']):
        raise Invalid('binding host must be the existing original builder immortal-os-builder')
    lock = h.pointer(root, binding['lockFrom'])
    production = Path(h.pointer(root, recipe['preparation']['productionSourceRootFrom']))
    names = [n for n in INSPECT_INPUTS if binding[n] is not None] if stage == 'inspect' else STAGE_INPUTS[stage]
    unbound = [name for name in names if binding[name] is None]
    if unbound:
        raise Unbound(f'{stage} needs host facts not yet bound in {Path(binding_path).name}: {", ".join(unbound)}')
    argv = ['--host', binding['host'], '--lock', lock] if stage == 'inspect' else ['--stage', stage, '--host', binding['host'], '--lock', lock]
    for name in names:
        value = binding[name]
        if not isinstance(value, str) or not value.startswith('/') or os.path.normpath(value) != value or '..' in Path(value).parts:
            raise Invalid(f'{name} must be a normalized absolute path')
        if name == 'node' and Path(value).name != 'node':
            raise Invalid('node must be the path of a file named node (the vendor stage resolves exactly that file)')
        if name != 'node' and h.isolation_problem(Path(value), production):
            raise Invalid(f'{name}: {h.isolation_problem(Path(value), production)}')
        argv += [FLAGS[name], value]
    if stage == 'inspect':
        if not 0 < INSPECT['maxSeconds'] <= h.INSPECT_MAX_SECONDS or INSPECT['outerSeconds'] <= INSPECT['maxSeconds']:
            raise Invalid('inspect bounds exceed the helper cap')
        proposed = candidate_root(binding, recipe, production, run_id or '1', attempt or '1')
        if h.isolation_problem(Path(proposed), production):
            raise Invalid(f'candidate root: {h.isolation_problem(Path(proposed), production)}')
        argv += ['--candidate-root', proposed]
        argv += ['--max-entries', str(INSPECT['maxEntries']), '--max-seconds', str(INSPECT['maxSeconds'])]
        return argv, INSPECT['outerSeconds'], INSPECT['jobMinutes']
    if stage == 'vendor' and Path(binding['downloadDir']) in (Path(binding['sourceRoot']), *Path(binding['sourceRoot']).parents):
        raise Invalid('downloadDir must not be or contain the isolated source root')
    timeout = recipe['preparation'][stage]['timeoutSeconds']
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise Invalid(f'recipe preparation.{stage}.timeoutSeconds must be a positive integer')
    outer = timeout + OUTER_MARGIN_SECONDS
    return argv, outer, math.ceil((outer + KILL_AFTER_SECONDS) / 60) + JOB_MARGIN_MINUTES


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('check', 'run'))
    p.add_argument('--stage', required=True)
    p.add_argument('--receipt-dir')
    p.add_argument('--run-id', help='inspect only: this run id, for the proposed candidate root')
    p.add_argument('--attempt', help='inspect only: this run attempt, for the proposed candidate root')
    p.add_argument('--binding', default=str(BINDING), help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    try:
        if a.command == 'check' and (a.run_id or a.attempt):
            raise Invalid('check takes no --run-id/--attempt')
        if a.command == 'run' and a.stage == 'inspect' and not (a.run_id and a.attempt):
            raise Invalid('run --stage inspect needs --run-id and --attempt')
        tail, outer, minutes = resolve(a.stage, a.binding, run_id=a.run_id, attempt=a.attempt)
        if a.command == 'check':
            if a.receipt_dir:
                raise Invalid('check takes no --receipt-dir')
            print(f'outer_seconds={outer}\njob_minutes={minutes}')
            return 0
        if not a.receipt_dir or not a.receipt_dir.startswith('/'):
            raise Invalid('run needs an absolute --receipt-dir')
        if a.stage == 'inspect':
            command = [sys.executable, str(HELPER), 'inspect', *tail]
            print(json.dumps({'helper': command[1:]}), flush=True)
            receipts = Path(a.receipt_dir)
            receipts.mkdir(mode=0o700)  # fresh: an existing directory refuses
            out = os.open(receipts/'inspection.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.dup2(out, 1)
            os.close(out)
        else:
            command = [sys.executable, str(HELPER), 'run', *tail, '--receipt-dir', a.receipt_dir]
            print(json.dumps({'helper': command[1:]}), flush=True)
        os.execv(sys.executable, command)
    except Unbound as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'error': str(error)}), file=sys.stderr)
        return 4
    except (Invalid, ValueError, KeyError, FileExistsError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
