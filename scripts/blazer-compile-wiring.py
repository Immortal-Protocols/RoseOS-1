#!/usr/bin/env python3
"""BLAZER-COMPILE workflow wiring (Release-owned) for os-release.yml mode blazer-compile: one first build of the
blazer target-files on the original builder, in the isolated blazer-prep work tree, by Flavors' helper.

  blazer-compile-wiring.py check
      hosted and on the builder: the recipe loads without drift (the helper's lexical load), compile carries its
      reviewed timeout and first-build allowance, and os/build-only/blazer-compile-host.json is complete and lexically
      isolated from the production source root. Exit 4 while jobs is null. Prints outer_seconds
      (compile.timeoutSeconds + 300 s for preflight and output collection) and job_minutes. Reads no host path.
  blazer-compile-wiring.py run --receipt-dir NEW
      the same check, then selects the integration of THIS checkout (integrationFrom): among the blazer-allocate
      reservations H/rose-actions/blazer-allocations/<build>.json (read no-follow and validated by blazer-allocate),
      exactly one whose source is this checkout's HEAD and whose integration completed - the admitted
      blazer-integrate-<build>/inputs/local-inputs.json and a NNNN-blazer-<build>.json receipt in blazer-reconcile-<build>,
      both regular files with no link in the path. None or more than one refuses (exit 1) before the helper. Then exec
      of exactly `blazer-build-only.py run --stage compile --host H --lock L --source-root S --jobs J
      --integration-dir blazer-reconcile-<build> --inputs-receipt R --receipt-dir NEW`. Integrate and compile are
      therefore dispatched on the same commit: the helper's compile_problems also requires HEAD to be the admitted recipe. The helper does every host check (isolation,
      the integration binding, lock, space, its own timeout) and writes preflight.json, started.json and completed.json
      (the collected target-files, otatools and boot image paths and hashes) write-once.

Compile neither signs nor packages. Exit status: 0 ok, 1 refused, 2 invalid, 4 unbound.
"""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BINDING = ROOT/'os/build-only/blazer-compile-host.json'
HELPER = ROOT/'scripts/blazer-build-only.py'
KEYS = {'schemaVersion', 'kind', 'basis', 'host', 'lockFrom', 'sourceRootFrom', 'jobs', 'integrationFrom'}
INTEGRATION_FROM = 'scripts/blazer-allocate.py reservation whose source is this checkout HEAD, integration completed'
HOME = Path('/home/pixelbuild')
OUTER_MARGIN_SECONDS, KILL_AFTER_SECONDS, JOB_MARGIN_MINUTES = 300, 30, 10
RECEIPTS = ('preflight.json', 'started.json', 'completed.json')


class Invalid(Exception):
    pass


class Unbound(Exception):
    pass


class Refused(Exception):
    pass


def helper():
    spec = importlib.util.spec_from_file_location('blazer_build_only', HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve(binding_path=BINDING, root=ROOT):
    """(argv tail without --receipt-dir, outer_seconds, job_minutes)."""
    h = helper()
    try:
        recipe = h.load(root/'os/build-only/blazer.json', root, probe=False)
    except h.InvalidInput as error:
        raise Invalid(f'recipe: {error}')
    binding = json.loads(Path(binding_path).read_text())
    if set(binding) != KEYS or binding['schemaVersion'] != 1 or binding['kind'] != 'rose-blazer-compile-host':
        raise Invalid('binding is not an exact rose-blazer-compile-host v1 record')
    if binding['host'] != 'immortal-os-builder' or binding['lockFrom'] != recipe['preparation']['lockFrom']:
        raise Invalid('binding host and lock must be the original builder and the recipe lockFrom')
    if binding['sourceRootFrom'] != 'os/build-only/blazer-prep-host.json sourceRoot':
        raise Invalid('the source root must be the blazer-prep binding\'s isolated work tree')
    compile_ = recipe['compile']
    timeout = compile_.get('timeoutSeconds')
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 0 < timeout <= h.COMPILE_MAX_SECONDS:
        raise Invalid('recipe compile.timeoutSeconds must be a positive integer within the helper cap')
    allowance = recipe['capacity'].get('compileAllowanceBytes')
    if not isinstance(allowance, int) or isinstance(allowance, bool) or allowance <= 0:
        raise Invalid('recipe capacity.compileAllowanceBytes must be a reviewed finite allowance')
    if binding['integrationFrom'] != INTEGRATION_FROM:
        raise Invalid(f'integrationFrom must be {INTEGRATION_FROM!r}')
    if binding['jobs'] is None:
        raise Unbound(f'compile needs inputs not yet bound in {Path(binding_path).name}: jobs')
    jobs = binding['jobs']
    if not isinstance(jobs, int) or isinstance(jobs, bool) or not 1 <= jobs <= compile_['maxJobs']:
        raise Invalid(f'jobs must be an integer from 1 to {compile_["maxJobs"]}')
    source = h.pointer(root, binding['sourceRootFrom'])
    production = Path(h.pointer(root, recipe['preparation']['productionSourceRootFrom']))
    if not isinstance(source, str) or not source.startswith('/') or os.path.normpath(source) != source or '..' in Path(source).parts:
        raise Invalid('sourceRoot must be a normalized absolute path')
    problem = h.isolation_problem(Path(source), production, probe=False)
    if problem:
        raise Invalid(f'sourceRoot: {problem}')
    argv = ['--stage', 'compile', '--host', binding['host'], '--lock', h.pointer(root, binding['lockFrom']),
            '--source-root', source, '--jobs', str(jobs)]
    outer = timeout + OUTER_MARGIN_SECONDS
    return argv, outer, math.ceil((outer + KILL_AFTER_SECONDS) / 60) + JOB_MARGIN_MINUTES


def allocator(root=ROOT):
    spec = importlib.util.spec_from_file_location('blazer_allocate', root/'scripts/blazer-allocate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plain(path):
    """A regular file (or directory) with no link anywhere in its path."""
    path = Path(path)
    return not any(p.is_symlink() for p in (path, *path.parents)) and (path.is_file() or path.is_dir())


def integration(recipe, source, home=HOME, root=ROOT):
    """(--integration-dir, --inputs-receipt) of the one completed integration reserved for this recipe, or Refused."""
    allocate = allocator(root)
    base = Path(home)/'rose-actions'
    directory = base/allocate.RESERVATIONS
    if not plain(directory) or not directory.is_dir():
        raise Refused('no blazer reservation directory: integrate this commit first')
    found = []
    for path in sorted(directory.iterdir()):
        try:
            build = allocate.read_reservation(path)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as f:
                record = json.load(f)
        except (OSError, ValueError, KeyError, TypeError):
            raise Refused(f'unreadable blazer reservation {path.name[:40]}') from None
        if record['source'] != recipe:
            continue
        work, reconcile = base/f'blazer-integrate-{build}', base/f'blazer-reconcile-{build}'
        if (record['workDir'], record['integrationDir']) != (str(work), str(reconcile)):
            raise Refused(f'reservation {build} names other directories than its build derives')
        inputs = work/'inputs/local-inputs.json'
        if source in (work, reconcile, *work.parents, *reconcile.parents):
            raise Refused(f'reservation {build} directories contain the source tree')
        applied = plain(reconcile) and reconcile.is_dir() and any(
            plain(r) and r.is_file() for r in reconcile.glob(f'[0-9][0-9][0-9][0-9]-blazer-{build}.json'))
        if plain(inputs) and inputs.is_file() and applied:
            found.append((build, str(reconcile), str(inputs)))
    if len(found) != 1:
        raise Refused(f'{len(found)} completed blazer integrations of this commit {recipe[:12]}; exactly one is required')
    return found[0][1], found[0][2]


def head(root=ROOT):
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    value = result.stdout.strip()
    if result.returncode or not re.fullmatch(r'[0-9a-f]{40}', value):
        raise Invalid('this checkout has no readable HEAD')
    return value


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('check', 'run'))
    p.add_argument('--receipt-dir')
    p.add_argument('--binding', default=str(BINDING), help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    try:
        tail, outer, minutes = resolve(a.binding)
        if a.command == 'check':
            if a.receipt_dir:
                raise Invalid('check takes no --receipt-dir')
            print(f'outer_seconds={outer}\njob_minutes={minutes}')
            return 0
        if not a.receipt_dir or not a.receipt_dir.startswith('/'):
            raise Invalid('run needs an absolute --receipt-dir')
        integration_dir, inputs = integration(head(), Path(tail[tail.index('--source-root') + 1]))
        command = [sys.executable, str(HELPER), 'run', *tail, '--integration-dir', integration_dir,
                   '--inputs-receipt', inputs, '--receipt-dir', a.receipt_dir]
        print(json.dumps({'helper': command[1:]}), flush=True)
        os.execv(sys.executable, command)
    except Refused as error:
        print(json.dumps({'verdict': 'REFUSED', 'error': str(error)}), file=sys.stderr)
        return 1
    except Unbound as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'error': str(error)}), file=sys.stderr)
        return 4
    except (Invalid, ValueError, KeyError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
