#!/usr/bin/env python3
"""BLAZER-COMPILE workflow wiring (Release-owned) for os-release.yml mode blazer-compile: one first build of the
blazer target-files on the original builder, in the isolated blazer-prep work tree, by Flavors' helper.

  blazer-compile-wiring.py check
      hosted and on the builder: the recipe loads without drift (the helper's lexical load), compile carries its
      reviewed timeout and first-build allowance, and os/build-only/blazer-compile-host.json is complete and lexically
      isolated from the production source root. Exit 4 while jobs, integrationDir or inputsReceipt is null. Prints
      outer_seconds (compile.timeoutSeconds + 300 s for preflight and output collection) and job_minutes. Reads no host
      path.
  blazer-compile-wiring.py run --receipt-dir NEW
      the same check, then exec of exactly `blazer-build-only.py run --stage compile --host H --lock L --source-root S
      --jobs J --integration-dir I --inputs-receipt R --receipt-dir NEW`. The helper does every host check (isolation,
      the integration binding, lock, space, its own timeout) and writes preflight.json, started.json and completed.json
      (the collected target-files, otatools and boot image paths and hashes) write-once.

Compile neither signs nor packages. Exit status: 0 ok, 2 invalid, 4 unbound.
"""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BINDING = ROOT/'os/build-only/blazer-compile-host.json'
HELPER = ROOT/'scripts/blazer-build-only.py'
KEYS = {'schemaVersion', 'kind', 'basis', 'host', 'lockFrom', 'sourceRootFrom', 'jobs', 'integrationDir', 'inputsReceipt'}
OUTER_MARGIN_SECONDS, KILL_AFTER_SECONDS, JOB_MARGIN_MINUTES = 300, 30, 10
RECEIPTS = ('preflight.json', 'started.json', 'completed.json')


class Invalid(Exception):
    pass


class Unbound(Exception):
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
    unbound = [n for n in ('jobs', 'integrationDir', 'inputsReceipt') if binding[n] is None]
    if unbound:
        raise Unbound(f'compile needs inputs not yet bound in {Path(binding_path).name}: {", ".join(unbound)}')
    jobs = binding['jobs']
    if not isinstance(jobs, int) or isinstance(jobs, bool) or not 1 <= jobs <= compile_['maxJobs']:
        raise Invalid(f'jobs must be an integer from 1 to {compile_["maxJobs"]}')
    source = h.pointer(root, binding['sourceRootFrom'])
    production = Path(h.pointer(root, recipe['preparation']['productionSourceRootFrom']))
    values = {'sourceRoot': source, 'integrationDir': binding['integrationDir'], 'inputsReceipt': binding['inputsReceipt']}
    for name, value in values.items():
        if not isinstance(value, str) or not value.startswith('/') or os.path.normpath(value) != value or '..' in Path(value).parts:
            raise Invalid(f'{name} must be a normalized absolute path')
        problem = h.isolation_problem(Path(value), production, probe=False)
        if problem:
            raise Invalid(f'{name}: {problem}')
    if Path(binding['inputsReceipt']).name != 'local-inputs.json':
        raise Invalid('inputsReceipt must be blazer_integration.py admit\'s local-inputs.json')
    for name in ('integrationDir', 'inputsReceipt'):
        path = Path(values[name])
        if Path(source) in (path, *path.parents):
            raise Invalid(f'{name} must be outside the source tree')
    argv = ['--stage', 'compile', '--host', binding['host'], '--lock', h.pointer(root, binding['lockFrom']),
            '--source-root', source, '--jobs', str(jobs), '--integration-dir', values['integrationDir'],
            '--inputs-receipt', values['inputsReceipt']]
    outer = timeout + OUTER_MARGIN_SECONDS
    return argv, outer, math.ceil((outer + KILL_AFTER_SECONDS) / 60) + JOB_MARGIN_MINUTES


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
        command = [sys.executable, str(HELPER), 'run', *tail, '--receipt-dir', a.receipt_dir]
        print(json.dumps({'helper': command[1:]}), flush=True)
        os.execv(sys.executable, command)
    except Unbound as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'error': str(error)}), file=sys.stderr)
        return 4
    except (Invalid, ValueError, KeyError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
