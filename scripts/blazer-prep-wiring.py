#!/usr/bin/env python3
"""BLAZER-PREP workflow wiring (Release-owned) for os-release.yml mode blazer-prep.

Binds the committed operator inputs of os/build-only/blazer-prep-host.json to the Flavors helper
scripts/blazer-build-only.py, one preparation stage (download, vendor or lunch) per dispatch:

  blazer-prep-wiring.py check --stage S
      hosted and on the builder: the recipe loads without drift (the helper's own load), the binding is complete and
      lexically isolated from the production source root, and the stage is a preparation stage. Prints the bounds as
      job outputs: outer_seconds (the helper's own stage timeout + 300 s for preflight and consumption measurement) and
      job_minutes. Reads no host path.
  blazer-prep-wiring.py run --stage S --receipt-dir NEW
      the same check, then exec of exactly `blazer-build-only.py run --stage S --host H --lock L <stage inputs>
      --receipt-dir NEW`. The helper does every host check (symlinks, same inode, collisions, lock, space, bounds).

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
FLAGS = {'downloadDir': '--download-dir', 'sourceRoot': '--source-root', 'node': '--node'}
KEYS = {'schemaVersion', 'kind', 'basis', 'host', 'lockFrom', 'sourceRoot', 'downloadDir', 'node'}


class Invalid(Exception):
    pass


class Unbound(Exception):
    pass


def helper():
    spec = importlib.util.spec_from_file_location('blazer_build_only', HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve(stage, binding_path=BINDING, root=ROOT):
    """(argv tail without --receipt-dir, outer_seconds, job_minutes) for one preparation stage."""
    h = helper()
    if stage not in h.PREPARATION:
        raise Invalid(f'{stage!r} is not a preparation stage {h.PREPARATION}')
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
    unbound = [name for name in STAGE_INPUTS[stage] if binding[name] is None]
    if unbound:
        raise Unbound(f'{stage} needs host facts not yet bound in {Path(binding_path).name}: {", ".join(unbound)}')
    argv = ['--stage', stage, '--host', binding['host'], '--lock', lock]
    for name in STAGE_INPUTS[stage]:
        value = binding[name]
        if not isinstance(value, str) or not value.startswith('/') or os.path.normpath(value) != value or '..' in Path(value).parts:
            raise Invalid(f'{name} must be a normalized absolute path')
        if name != 'node' and h.isolation_problem(Path(value), production):
            raise Invalid(f'{name}: {h.isolation_problem(Path(value), production)}')
        argv += [FLAGS[name], value]
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
    p.add_argument('--binding', default=str(BINDING), help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    try:
        tail, outer, minutes = resolve(a.stage, a.binding)
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
