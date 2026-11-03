#!/usr/bin/env python3
"""BLAZER-INTEGRATE workflow wiring (Release-owned) for os-release.yml mode blazer-integrate: the Rose integration of
the isolated blazer work tree, by the existing owners' commands only, in order, each bounded, stopping at the first
failure.

  blazer-integrate-wiring.py check
      hosted and on the builder: os/build-only/blazer-integrate-host.json is exact, its paths are normalized absolute,
      isolated from the production source root and outside the source tree, and every host fact is bound. Exit 4 while
      any is null. Prints outer_seconds and job_minutes. Reads no host path.
  blazer-integrate-wiring.py run --run-dir NEW --run-id N --run-attempt N
      the same check, then exactly:
        0. tools      aapt, apksigner and apksigner's toolDependencies (its lib/apksigner.jar and the JDK java it execs)
                      re-hashed against their expected sha256 (regular files, no link in the path)
        0. inputs     the policy and exactly the six pinned app files of appsDir (<package>.apk, <package>.receipt.json)
                      re-hashed against inputDigests the same way
        0. allocate   blazer-allocate.py reserve --home H --run-id N --run-attempt N --source HEAD --out NEW/allocation.json
                      (a fresh collision-checked blazer build under the shared lock); WORK and INTEGRATION_DIR are the
                      reservation's derived H/rose-actions/blazer-integrate-<build> and blazer-reconcile-<build>
        1. seed       blazer-reconcile-seed.py seed --frankel-receipts F --source-root S --source-run RUN
                      --source-inventory INV --source-inventory-sha256 H --receipt-dir INTEGRATION_DIR
        2. assemble   blazer-candidate-bundle.py assemble --apps-dir A --policy P --build B --product HEAD --aapt T
                      --apksigner U --out WORK/bundle
                      then the bundle's copied policy and six app files re-hashed against inputDigests
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

Every step runs with the inherited environment minus SCRUBBED: the apksigner wrapper execs the bound java by absolute
path (its bound bytes are host-sdk-layout.py's wrapper for these paths), so PATH and JAVA_HOME never select it, and the
variables a JVM or the dynamic loader would honour to inject code or options are removed.
The tree is changed only by step 6, host-reconcile.py under the shared lock with its own owner-conflict checks.
Exit status: 0 integrated, 1 a step refused or failed, 2 invalid, 4 unbound.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BINDING = ROOT/'os/build-only/blazer-integrate-host.json'
KEYS = {'schemaVersion', 'kind', 'basis', 'host', 'lockFrom', 'sourceRootFrom', 'frankelReceiptsFrom', 'updaterRoute',
        'sourceRun', 'sourceInventory', 'appsDir', 'policy', 'aapt', 'apksigner', 'inputDigests', 'toolDependencies',
        'observation'}
FACTS = ('sourceRun', 'sourceInventory.path', 'sourceInventory.sha256', 'appsDir', 'policy', 'aapt.path', 'aapt.sha256',
         'apksigner.path', 'apksigner.sha256')
TOOLS = ('aapt', 'apksigner')
# What the apksigner wrapper executes (host-sdk-layout.py): fixed relative to the wrapper and the production root.
DEPENDENCIES = {'apksigner.jar': lambda tools, production: tools['apksigner']['path'].parent/'lib/apksigner.jar',
                'java': lambda tools, production: production/'prebuilts/jdk/jdk21/linux-x86/bin/java'}
OBSERVATION = {'run', 'runAttempt', 'source', 'artifact', 'artifactZipSha256', 'report', 'reportSha256'}
RECONCILE = ROOT/'os/build-only/blazer-reconcile.json'
SCRUBBED = ('JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS', '_JAVA_OPTIONS', 'CLASSPATH', 'JAVA_HOME', 'LD_PRELOAD',
            'LD_LIBRARY_PATH', 'LD_AUDIT')
SHA256 = re.compile(r'[0-9a-f]{64}')
HOME = '/home/pixelbuild'
# Read-only SDK tools may sit inside the production source root only below this exact prefix: they are executed and
# hashed, never written. Every data path keeps the full production-root rejection.
TOOL_PREFIX = 'prebuilts/sdk/'
# Wrapper ceilings (seconds) per step, sized to the work, not measurements.
STEPS = {'tools': 300, 'inputs': 120, 'allocate': 120, 'seed': 600, 'assemble': 300, 'admit': 600, 'render': 120, 'candidate': 300,
         'reconcile': 3600}
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
    for name in ('sourceInventory', *TOOLS):
        if not isinstance(binding[name], dict) or set(binding[name]) != {'path', 'sha256'}:
            raise Invalid(f'{name} must be exactly {{path, sha256}}')
    values = {k: (binding[k.split('.')[0]][k.split('.')[1]] if '.' in k else binding[k]) for k in FACTS}
    unbound = [k for k in FACTS if values[k] is None]
    if unbound:
        raise Unbound(f'integration needs host facts not yet bound in {Path(binding_path).name}: {", ".join(unbound)}')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', str(values['sourceRun'])):
        raise Invalid('sourceRun must be the numeric run that made the copy')
    for name in ('sourceInventory.sha256', 'aapt.sha256', 'apksigner.sha256'):
        if not SHA256.fullmatch(str(values[name])):
            raise Invalid(f'{name} must be a lowercase sha256')
    source = absolute('sourceRoot', pointer(root, binding['sourceRootFrom']))
    production = absolute('production', pointer(root, PRODUCTION))
    if Path(HOME)/'rose-actions' not in source.parents:
        raise Invalid(f'the source root must sit under {HOME}/rose-actions')
    paths = {name: absolute(name, values[name]) for name in ('sourceInventory.path', 'appsDir', 'policy')}
    for name, path in {**paths, 'sourceRoot': source}.items():
        if path == production or production in path.parents or path in production.parents:
            raise Invalid(f'{name} {path} is, contains or sits inside the production source root')
    tools = {}
    for name in TOOLS:
        path = absolute(f'{name}.path', values[f'{name}.path'])
        if path in production.parents or path == production:
            raise Invalid(f'{name}.path {path} is or contains the production source root')
        if production in path.parents and not str(path).startswith(f'{production}/{TOOL_PREFIX}'):
            raise Invalid(f'{name}.path inside the production source root must be below {TOOL_PREFIX} (read-only SDK tools)')
        tools[name] = {'path': path, 'sha256': values[f'{name}.sha256']}
    tools.update(dependencies(binding['toolDependencies'], tools, production))
    digests = input_digests(binding['inputDigests'], paths['appsDir'], paths['policy'], root)
    observed = binding['observation']
    if not isinstance(observed, dict) or set(observed) != OBSERVATION \
            or not all(re.fullmatch(r'[1-9][0-9]{0,19}', str(observed[k])) for k in ('run', 'runAttempt', 'artifact')) \
            or not re.fullmatch(r'[0-9a-f]{40}', str(observed['source'])) or observed['report'] != 'binding-observation.json' \
            or not all(SHA256.fullmatch(str(observed[k])) for k in ('artifactZipSha256', 'reportSha256')):
        raise Invalid('observation must name the binding-observe run, attempt, source, artifact and the report and ZIP sha256')
    plan = {'source': source, 'lock': pointer(root, binding['lockFrom']), 'route': root/binding['updaterRoute'],
            'frankel': absolute('frankelReceipts', pointer(root, binding['frankelReceiptsFrom'])),
            'run': str(values['sourceRun']), 'inventory': paths['sourceInventory.path'],
            'inventorySha256': values['sourceInventory.sha256'], 'appsDir': paths['appsDir'], 'policy': paths['policy'],
            'tools': tools, 'inputs': digests, 'aapt': tools['aapt']['path'], 'apksigner': tools['apksigner']['path'], 'home': Path(HOME)}
    outer = sum(STEPS.values()) + KILL_GRACE_SECONDS * len(STEPS) + OUTER_MARGIN_SECONDS
    return plan, outer, math.ceil((outer + KILL_GRACE_SECONDS) / 60) + JOB_MARGIN_MINUTES


def dependencies(record, tools, production):
    """apksigner's executed dependencies: exactly the fixed jar and java paths, each with a sha256. Unbound if absent."""
    if record is None:
        raise Unbound('integration needs host facts not yet bound: toolDependencies')
    if not isinstance(record, dict) or set(record) != set(DEPENDENCIES):
        raise Invalid(f'toolDependencies must be exactly {sorted(DEPENDENCIES)}')
    found = {}
    for name, derive in DEPENDENCIES.items():
        spec = record[name]
        if not isinstance(spec, dict) or set(spec) != {'path', 'sha256'} or None in spec.values():
            raise Unbound(f'integration needs host facts not yet bound: toolDependencies.{name}')
        path = absolute(f'toolDependencies.{name}.path', spec['path'])
        if path != derive(tools, production):
            raise Invalid(f'toolDependencies.{name}.path must be {derive(tools, production)}')
        if not SHA256.fullmatch(str(spec['sha256'])):
            raise Invalid(f'toolDependencies.{name}.sha256 must be a lowercase sha256')
        found[name] = {'path': path, 'sha256': spec['sha256']}
    return found


def input_digests(record, apps_dir, policy, root=ROOT):
    """{path: sha256} for the policy and exactly the six pinned app files of appsDir. Unbound if absent."""
    if record is None:
        raise Unbound('integration needs host facts not yet bound: inputDigests')
    packages = [app['package'] for app in json.loads(Path(root/RECONCILE.relative_to(ROOT)).read_text())['candidateInputs']['apps']]
    names = {f'{p}.apk' for p in packages} | {f'{p}.receipt.json' for p in packages}
    if not isinstance(record, dict) or set(record) != {'policy', 'apps'} or not isinstance(record['apps'], dict) \
            or set(record['apps']) != names:
        raise Invalid(f'inputDigests must be exactly the policy and {sorted(names)}')
    found = {policy: record['policy'], **{apps_dir/name: record['apps'][name] for name in sorted(names)}}
    if not all(SHA256.fullmatch(str(v)) for v in found.values()):
        raise Invalid('every inputDigests value must be a lowercase sha256')
    return found


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
    """A step's result: one complete JSON object, or None. The whole output is tried first, because the owners' helpers
    print multi-line records (blazer_integration.py admit and render use json.dumps(..., indent=2)); only when it is not
    one document is the last line tried, for a one-line record after other output. Anything that is not an object - a
    scalar, a list, one element of a pretty-printed record, a truncated document - is None, never a result."""
    text = (text or '').strip() if isinstance(text, str) else ''
    try:
        value = json.loads(text)
    except ValueError:
        lines = text.splitlines()
        try:
            value = json.loads(lines[-1]) if lines else None
        except ValueError:
            value = None
    return value if isinstance(value, dict) else None


RENDER_KEYS = {'verdict', 'config', 'command'}


def render_problem(result, plan, recipe):
    """None when render printed exactly its OK record for this run: {verdict OK, config, command} with config the
    --out this wiring passed and command exactly the fixed reconcile shape; otherwise the fixed reason."""
    if not isinstance(result, dict) or set(result) != RENDER_KEYS or result['verdict'] != 'OK' \
            or result['config'] != str(plan['workDir']/'reconcile-config.json'):
        return 'render printed no complete OK record {verdict, config, command} for this run'
    if result['command'] != expected_reconcile(plan, recipe):
        return 'render printed another reconcile command than the fixed shape'
    return None


def expected_reconcile(plan, recipe):
    """The only reconcile command accepted from render: its fixed shape for these inputs."""
    work = plan['workDir']
    inputs = json.loads((work/'inputs/local-inputs.json').read_text())
    return ['flock', '--nonblock', '--conflict-exit-code', '75', str(plan['lock']), sys.executable,
            str(ROOT/'scripts/host-reconcile.py'), 'apply', f'{work}/product-{recipe[:12]}', f'{work}/reconcile-{plan["build"]}',
            inputs['policy']['path'], inputs['signedApps'], '--config', str(work/'reconcile-config.json'),
            '--label', f'blazer-{plan["build"]}']


def tool_sha256(path):
    """sha256 of a regular file reached with no link anywhere in its path; None otherwise."""
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        digest = hashlib.sha256()
        while True:
            block = os.read(fd, 8 << 20)
            if not block:
                return digest.hexdigest()
            digest.update(block)
    finally:
        os.close(fd)


def read_allocation(path, home, recipe):
    """The reservation the allocator wrote: its build and exactly the derived directories, or Invalid."""
    record = json.loads(Path(path).read_text())
    build = str(record.get('build'))
    if not re.fullmatch(r'[1-9][0-9]{9}', build) or record.get('device') != 'blazer' or record.get('source') != recipe:
        raise Invalid('the allocation is not a blazer reservation for this recipe')
    work, integration = Path(home)/'rose-actions'/f'blazer-integrate-{build}', Path(home)/'rose-actions'/f'blazer-reconcile-{build}'
    if (record.get('workDir'), record.get('integrationDir')) != (str(work), str(integration)):
        raise Invalid('the allocation names other directories than its build derives')
    return build, work, integration


def step_env():
    return {k: v for k, v in os.environ.items() if k not in SCRUBBED}


def run(plan, run_dir, run_id, attempt, root=ROOT, runner=subprocess.run, recipe=None, hasher=tool_sha256):
    run_dir = Path(run_dir)
    run_dir.mkdir(mode=0o700)
    recipe = recipe or head(root, runner)
    record = {'schema': 'roseos.blazer-integrate.v1', 'recipe': recipe, 'build': None, 'steps': []}
    status = 1
    try:
        tools = {name: hasher(spec['path']) == spec['sha256'] for name, spec in plan['tools'].items()}
        record['steps'].append({'step': 'tools', 'exit': 0 if all(tools.values()) else 1, 'result': tools})
        if not all(tools.values()):
            return status
        inputs = {path.name: hasher(path) == sha256 for path, sha256 in plan['inputs'].items()}
        record['steps'].append({'step': 'inputs', 'exit': 0 if all(inputs.values()) else 1, 'result': inputs})
        if not all(inputs.values()):
            return status
        argv = [sys.executable, str(root/'scripts/blazer-allocate.py'), 'reserve', '--home', str(plan['home']),
                '--run-id', str(run_id), '--run-attempt', str(attempt), '--source', recipe, '--out', str(run_dir/'allocation.json')]
        result = runner(argv, capture_output=True, text=True, timeout=STEPS['allocate'], env=step_env())
        record['steps'].append({'step': 'allocate', 'exit': result.returncode, 'result': last_json(result.stdout or result.stderr)})
        if result.returncode:
            return status
        build, work, integration = read_allocation(run_dir/'allocation.json', plan['home'], recipe)
        plan = {**plan, 'build': build, 'workDir': work, 'integrationDir': integration}
        record['build'] = build
        if os.path.lexists(work) or os.path.lexists(integration):
            record['steps'][-1]['problem'] = 'a derived directory already exists'
            return status
        work.mkdir(mode=0o700)
        for name, argv in commands(plan, recipe, root):
            result = runner(argv, capture_output=True, text=True, timeout=STEPS[name], env=step_env())
            record['steps'].append({'step': name, 'exit': result.returncode, 'result': last_json(result.stdout or result.stderr)})
            if result.returncode:
                return status
            if name == 'assemble':
                bundle = work/'bundle'
                copies = {(bundle/'background-policy.json' if path == plan['policy'] else bundle/'signed-apps'/path.name): sha256
                          for path, sha256 in plan['inputs'].items()}
                if not all(hasher(path) == sha256 for path, sha256 in copies.items()):
                    record['steps'][-1]['problem'] = 'the bundle copies are not the bound input bytes'
                    return status
            if name == 'candidate':
                checked = runner(['git', '-C', str(work/f'product-{recipe[:12]}'), 'rev-parse', 'HEAD'],
                                 capture_output=True, text=True, timeout=60, env=step_env())
                if checked.returncode or checked.stdout.strip() != recipe:
                    record['steps'][-1]['problem'] = 'the candidate checkout is not the recipe HEAD'
                    return status
            if name == 'render':
                problem = render_problem(record['steps'][-1]['result'], plan, recipe)
                if problem:
                    record['steps'][-1]['problem'] = problem
                    return status
                command = expected_reconcile(plan, recipe)
        result = runner(command, capture_output=True, text=True, timeout=STEPS['reconcile'], env=step_env())
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
    files = {'integrate.json': Path(run_dir)/'integrate.json', 'allocation.json': Path(run_dir)/'allocation.json'}
    try:
        recipe = json.loads((Path(run_dir)/'integrate.json').read_text())['recipe']
        _, work, integration = read_allocation(Path(run_dir)/'allocation.json', plan['home'], recipe)
    except (OSError, ValueError, KeyError, Invalid):
        return files
    receipts = sorted(integration.glob('[0-9][0-9][0-9][0-9]-*.json')) \
        if integration.is_dir() and not integration.is_symlink() else []
    files.update({'seed-provenance.json': integration/'seed-provenance.json',
                  'local-inputs.json': work/'inputs/local-inputs.json',
                  'reconcile-config.json': work/'reconcile-config.json'})
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
    p.add_argument('--run-id')
    p.add_argument('--run-attempt')
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
        if not (a.run_id and a.run_attempt):
            raise Invalid('run needs --run-id and --run-attempt')
        return run(plan, a.run_dir, a.run_id, a.run_attempt)
    except Unbound as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'error': str(error)}), file=sys.stderr)
        return 4
    except (Invalid, ValueError, KeyError, FileExistsError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
