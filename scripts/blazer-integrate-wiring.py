#!/usr/bin/env python3
"""BLAZER-INTEGRATE workflow wiring (Release-owned) for os-release.yml mode blazer-integrate: the Rose integration of
the isolated blazer work tree, by the existing owners' commands only, in order, each bounded, stopping at the first
failure.

  blazer-integrate-wiring.py check
      hosted and on the builder: os/build-only/blazer-integrate-host.json is exact, its paths are normalized absolute,
      isolated from the production source root and outside the source tree, and every host fact is bound. Exit 4 while
      any is null. Prints outer_seconds and job_minutes. Reads no host path.
  blazer-integrate-wiring.py run --run-dir NEW
      the same check, then exactly:
        1. seed       blazer-reconcile-seed.py seed --frankel-receipts F --source-root S --source-run RUN
                      --source-inventory INV --source-inventory-sha256 H --receipt-dir INTEGRATION_DIR
        2. assemble   blazer-candidate-bundle.py assemble --apps-dir A --policy P --build B --product HEAD --aapt T
                      --apksigner U --out WORK/bundle
        3. admit      blazer_integration.py admit --bundle WORK/bundle --updater-route ROUTE --out WORK/inputs
                      --aapt T --apksigner U
        4. render     blazer_integration.py render --inputs WORK/inputs/local-inputs.json --source-root S --lock L
                      --receipt-dir INTEGRATION_DIR --work-dir WORK --out WORK/reconcile-config.json
        5. candidate  git worktree add --detach WORK/product-<HEAD[:12]> HEAD (this checkout, no network), HEAD checked
        6. reconcile  exactly the command render printed (flock --nonblock L host-reconcile.py apply ...)
      HEAD is this checkout, the recipe: assemble names it as the product, and admit requires the checkout to be the
      recipe. Each step's exit and its last JSON line go to NEW/integrate.json; nothing is retried, reset or adopted.
  blazer-integrate-wiring.py report --run-dir NEW --upload-dir NEW_UPLOAD
      always: copies only integrate.json, the seed provenance, the admitted local-inputs.json, the rendered config and
      the newest reconcile receipt (regular files, never a link) into a new upload directory.

The tree is changed only by step 6, host-reconcile.py under the shared lock with its own owner-conflict checks.
Exit status: 0 integrated, 1 a step refused or failed, 2 invalid, 4 unbound.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BINDING = ROOT/'os/build-only/blazer-integrate-host.json'
KEYS = {'schemaVersion', 'kind', 'basis', 'host', 'lockFrom', 'sourceRootFrom', 'frankelReceiptsFrom', 'updaterRoute',
        'sourceRun', 'sourceInventory', 'appsDir', 'policy', 'aapt', 'apksigner', 'build', 'integrationDir', 'workDir'}
FACTS = ('sourceRun', 'sourceInventory.path', 'sourceInventory.sha256', 'appsDir', 'policy', 'aapt', 'apksigner', 'build',
         'integrationDir', 'workDir')
# Wrapper ceilings (seconds) per step, sized to the work, not measurements.
STEPS = {'seed': 600, 'assemble': 300, 'admit': 600, 'render': 120, 'candidate': 300, 'reconcile': 3600}
KILL_GRACE_SECONDS, OUTER_MARGIN_SECONDS, JOB_MARGIN_MINUTES = 30, 300, 10
PRODUCTION = 'os/release-pipeline/frankel.json host.sourceRoot'


class Invalid(Exception):
    pass


class Unbound(Exception):
    pass


def pointer(root, source):
    file, field = source.split(' ', 1)
    value = json.loads((root/file).read_text())
    for key in field.split('.'):
        value = value[key]
    return value


def absolute(name, value):
    if not isinstance(value, str) or not value.startswith('/') or os.path.normpath(value) != value or '..' in Path(value).parts:
        raise Invalid(f'{name} must be a normalized absolute path')
    return Path(value)


def resolve(binding_path=BINDING, root=ROOT):
    binding = json.loads(Path(binding_path).read_text())
    if set(binding) != KEYS or binding['schemaVersion'] != 1 or binding['kind'] != 'rose-blazer-integrate-host':
        raise Invalid('binding is not an exact rose-blazer-integrate-host v1 record')
    fixed = {'host': 'immortal-os-builder', 'lockFrom': 'os/release-pipeline/frankel.json host.lockPath',
             'sourceRootFrom': 'os/build-only/blazer-prep-host.json sourceRoot',
             'frankelReceiptsFrom': 'os/release-pipeline/frankel.json host.integrate.reconcile.receiptDir',
             'updaterRoute': 'os/build-only/blazer-updater-route.json'}
    for key, value in fixed.items():
        if binding[key] != value:
            raise Invalid(f'{key} must be {value}')
    inventory = binding['sourceInventory']
    if not isinstance(inventory, dict) or set(inventory) != {'path', 'sha256'}:
        raise Invalid('sourceInventory must be exactly {path, sha256}')
    values = {**{k: binding[k] for k in FACTS if '.' not in k}, 'sourceInventory.path': inventory['path'],
              'sourceInventory.sha256': inventory['sha256']}
    unbound = [k for k in FACTS if values[k] is None]
    if unbound:
        raise Unbound(f'integration needs host facts not yet bound in {Path(binding_path).name}: {", ".join(unbound)}')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', str(values['sourceRun'])):
        raise Invalid('sourceRun must be the numeric run that made the copy')
    if not re.fullmatch(r'[0-9a-f]{64}', str(values['sourceInventory.sha256'])):
        raise Invalid('sourceInventory.sha256 must be a lowercase sha256')
    if not re.fullmatch(r'[1-9][0-9]{9}', str(values['build'])):
        raise Invalid('build must be the ten-digit allocated build number')
    source = absolute('sourceRoot', pointer(root, binding['sourceRootFrom']))
    production = absolute('production', pointer(root, PRODUCTION))
    paths = {name: absolute(name, values[name]) for name in
             ('sourceInventory.path', 'appsDir', 'policy', 'aapt', 'apksigner', 'integrationDir', 'workDir')}
    for name, path in {**paths, 'sourceRoot': source}.items():
        if path == production or production in path.parents or path in production.parents:
            raise Invalid(f'{name} {path} is, contains or sits inside the production source root')
    for name in ('integrationDir', 'workDir'):
        if source in (paths[name], *paths[name].parents) or paths[name] in source.parents:
            raise Invalid(f'{name} must be outside the source tree')
    if paths['integrationDir'] == paths['workDir'] or paths['workDir'] in paths['integrationDir'].parents \
            or paths['integrationDir'] in paths['workDir'].parents:
        raise Invalid('integrationDir and workDir must be separate')
    plan = {'source': source, 'lock': pointer(root, binding['lockFrom']), 'route': root/binding['updaterRoute'],
            'frankel': absolute('frankelReceipts', pointer(root, binding['frankelReceiptsFrom'])),
            'run': str(values['sourceRun']), 'inventory': paths['sourceInventory.path'],
            'inventorySha256': values['sourceInventory.sha256'], 'build': str(values['build']), **paths}
    outer = sum(STEPS.values()) + KILL_GRACE_SECONDS * len(STEPS) + OUTER_MARGIN_SECONDS
    return plan, outer, math.ceil((outer + KILL_GRACE_SECONDS) / 60) + JOB_MARGIN_MINUTES


def head(root=ROOT, run=subprocess.run):
    result = run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    value = result.stdout.strip()
    if result.returncode or not re.fullmatch(r'[0-9a-f]{40}', value):
        raise Invalid('this checkout has no readable HEAD')
    return value


def commands(plan, recipe, root=ROOT):
    """The five argv before reconcile; reconcile is the command render prints."""
    py, work = sys.executable, plan['workDir']
    return [
        ('seed', [py, str(root/'scripts/blazer-reconcile-seed.py'), 'seed', '--frankel-receipts', str(plan['frankel']),
                  '--source-root', str(plan['source']), '--source-run', plan['run'], '--source-inventory',
                  str(plan['inventory']), '--source-inventory-sha256', plan['inventorySha256'], '--receipt-dir',
                  str(plan['integrationDir'])]),
        ('assemble', [py, str(root/'scripts/blazer-candidate-bundle.py'), 'assemble', '--apps-dir', str(plan['appsDir']),
                      '--policy', str(plan['policy']), '--build', plan['build'], '--product', recipe,
                      '--aapt', str(plan['aapt']), '--apksigner', str(plan['apksigner']), '--out', str(work/'bundle')]),
        ('admit', [py, str(root/'scripts/blazer_integration.py'), 'admit', '--bundle', str(work/'bundle'),
                   '--updater-route', str(plan['route']), '--out', str(work/'inputs'), '--aapt', str(plan['aapt']),
                   '--apksigner', str(plan['apksigner'])]),
        ('render', [py, str(root/'scripts/blazer_integration.py'), 'render', '--inputs', str(work/'inputs/local-inputs.json'),
                    '--source-root', str(plan['source']), '--lock', str(plan['lock']), '--receipt-dir',
                    str(plan['integrationDir']), '--work-dir', str(work), '--out', str(work/'reconcile-config.json')]),
        ('candidate', ['git', '-C', str(root), 'worktree', 'add', '--detach', str(work/f'product-{recipe[:12]}'), recipe]),
    ]


def last_json(text):
    for line in reversed((text or '').strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def expected_reconcile(plan, recipe):
    """The only reconcile command accepted from render: its fixed shape for these inputs."""
    work = plan['workDir']
    inputs = json.loads((work/'inputs/local-inputs.json').read_text())
    return ['flock', '--nonblock', '--conflict-exit-code', '75', str(plan['lock']), sys.executable,
            str(ROOT/'scripts/host-reconcile.py'), 'apply', f'{work}/product-{recipe[:12]}', f'{work}/reconcile-{plan["build"]}',
            inputs['policy']['path'], inputs['signedApps'], '--config', str(work/'reconcile-config.json'),
            '--label', f'blazer-{plan["build"]}']


def run(plan, run_dir, root=ROOT, runner=subprocess.run, recipe=None):
    run_dir = Path(run_dir)
    run_dir.mkdir(mode=0o700)
    recipe = recipe or head(root, runner)
    work = plan['workDir']
    if os.path.lexists(work):
        raise Invalid(f'{work} exists; integration needs a new work directory')
    work.mkdir(mode=0o700)
    record = {'schema': 'roseos.blazer-integrate.v1', 'recipe': recipe, 'build': plan['build'], 'steps': []}
    status = 1
    try:
        steps = commands(plan, recipe, root)
        for name, argv in steps:
            result = runner(argv, capture_output=True, text=True, timeout=STEPS[name])
            record['steps'].append({'step': name, 'exit': result.returncode, 'result': last_json(result.stdout or result.stderr)})
            if result.returncode:
                return status
            if name == 'candidate':
                checked = runner(['git', '-C', str(work/f'product-{recipe[:12]}'), 'rev-parse', 'HEAD'],
                                 capture_output=True, text=True, timeout=60)
                if checked.returncode or checked.stdout.strip() != recipe:
                    record['steps'][-1]['problem'] = 'the candidate checkout is not the recipe HEAD'
                    return status
            if name == 'render':
                printed = (record['steps'][-1]['result'] or {}).get('command')
                if printed != expected_reconcile(plan, recipe):
                    record['steps'][-1]['problem'] = 'render printed another reconcile command than the fixed shape'
                    return status
                command = printed
        result = runner(command, capture_output=True, text=True, timeout=STEPS['reconcile'])
        record['steps'].append({'step': 'reconcile', 'exit': result.returncode,
                                'result': last_json(result.stdout or result.stderr)})
        status = 0 if result.returncode == 0 else 1
        return status
    except subprocess.TimeoutExpired as error:
        record['steps'].append({'step': 'timeout', 'argv0': str(error.cmd[0]), 'seconds': error.timeout})
        return status
    finally:
        record['result'] = 'integrated' if status == 0 else 'stopped'
        fd = os.open(run_dir/'integrate.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(record, indent=2, sort_keys=True) + '\n')


def uploads(plan, run_dir):
    receipts = sorted(Path(plan['integrationDir']).glob('[0-9][0-9][0-9][0-9]-*.json')) \
        if Path(plan['integrationDir']).is_dir() and not Path(plan['integrationDir']).is_symlink() else []
    files = {'integrate.json': Path(run_dir)/'integrate.json',
             'seed-provenance.json': Path(plan['integrationDir'])/'seed-provenance.json',
             'local-inputs.json': Path(plan['workDir'])/'inputs/local-inputs.json',
             'reconcile-config.json': Path(plan['workDir'])/'reconcile-config.json'}
    if receipts:
        files['newest-reconcile-receipt.json'] = receipts[-1]
    return files


def report(plan, run_dir, upload_dir):
    upload_dir = Path(upload_dir)
    upload_dir.mkdir(mode=0o700)
    copied = []
    for name, source in uploads(plan, run_dir).items():
        if source.is_file() and not source.is_symlink() and not any(p.is_symlink() for p in source.parents):
            data = source.read_bytes()
            fd = os.open(upload_dir/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            copied.append(name)
    return copied


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('check', 'run', 'report'))
    p.add_argument('--run-dir')
    p.add_argument('--upload-dir')
    p.add_argument('--binding', default=str(BINDING), help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    try:
        plan, outer, minutes = resolve(a.binding)
        if a.command == 'check':
            if a.run_dir or a.upload_dir:
                raise Invalid('check takes no other option')
            print(f'outer_seconds={outer}\njob_minutes={minutes}')
            return 0
        if not a.run_dir or not os.path.isabs(a.run_dir):
            raise Invalid(f'{a.command} needs an absolute --run-dir')
        if a.command == 'report':
            if not a.upload_dir or not os.path.isabs(a.upload_dir):
                raise Invalid('report needs an absolute --upload-dir')
            print(json.dumps({'uploaded': report(plan, a.run_dir, a.upload_dir)}))
            return 0
        if a.upload_dir:
            raise Invalid('run takes no --upload-dir')
        return run(plan, a.run_dir)
    except Unbound as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'error': str(error)}), file=sys.stderr)
        return 4
    except (Invalid, ValueError, KeyError, FileExistsError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
