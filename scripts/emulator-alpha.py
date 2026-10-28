#!/usr/bin/env python3
"""os-release emulator-alpha mode: an unsigned, test-only Cuttlefish build of the retained builder tree.

Target and policy are in os/release-pipeline/cuttlefish-alpha.json; the builder, source tree and lock are shared
with the Pixel config it names (sharedHost). The alpha never reconciles, adopts, signs, publishes or cleans: it
builds the tree exactly as the last Pixel integration reconciled it, into its own OUT_DIR, and binds that tree to a
candidate only through the recorded reconcile receipt (sourceBinding). Every host fact the checked-in config cannot
prove is null there, and every command except plan fails closed (exit 4) naming it.

  plan       print the commands a build would run, marked executed: false; runs nothing
  preflight  --job guard: the checked-in config alone (before the builder starts);
             --job builder: also the retained tree, read-only (no lunch, no make)
  probe      on the builder, under the shared lock: the read-only tree checks plus lunch resolution (which writes
             build-system state only inside the alpha OUT_DIR); writes a probe receipt whose values the lead records in hostEvidence
  build      on the builder, under the shared lock: every check, lunch resolution equal to hostEvidence, make of
             the configured goals, fresh required outputs, build identity in the partition build.props, then a
             receipt hashing every top-level image and the host package. Unsigned, test-only, boot not attempted
Exit status: 0 ok, 1 check failed, 2 invalid input, 4 setup missing.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_receipts  # noqa: E402
from release_receipts import sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'os/release-pipeline/cuttlefish-alpha.json'
CUTTLEFISH = 'device/google/cuttlefish'
LUNCH_VARS = ('TARGET_PRODUCT', 'TARGET_RELEASE', 'TARGET_BUILD_VARIANT', 'TARGET_BOARD_PLATFORM', 'PRODUCT_DEVICE',
    'PRODUCT_OUT', 'HOST_OUT')
# Read from the shell after lunch, not get_build_var: the retained tree's lunch exports TARGET_RELEASE
# (build/envsetup.sh) while get_build_var TARGET_RELEASE returns empty there.
SHELL_VARS = ('TARGET_RELEASE',)
PROPS = ('system/build.prop', 'vendor/build.prop', 'product/etc/build.prop', 'system_ext/etc/build.prop')
NOT_PROVEN = ['signature or verified boot (AOSP test keys, userdebug)', 'guest boot (sys.boot_completed)',
    'Rose UI, viewer, input or Stop behaviour', 'Pixel hardware, firmware, radio or flashing',
    'that every Rose change applies to this product (Pixel-specific overlays may not)']

class CheckFailed(Exception):
    pass

class InvalidInput(Exception):
    pass

class SetupMissing(Exception):
    def __init__(self, missing):
        super().__init__('setup missing: ' + ', '.join(missing))
        self.missing = missing

def load(path=CONFIG):
    config = json.loads(Path(path).read_text())
    if config.get('schemaVersion') != 1 or config.get('kind') != 'emulator-alpha':
        raise InvalidInput('not an emulator-alpha config')
    shared = json.loads((ROOT/config['sharedHost']).read_text())
    config['host'] = {k: shared['host'][k] for k in ('sourceRoot', 'lockPath', 'outDir')}
    return config

def token(value):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', str(value)):
        raise InvalidInput(f'unsafe build token: {value!r}')
    return value

def config_failures(config):
    """Static checks of the checked-in config: target consistency and disjoint, contained outputs."""
    target, build, host = config['target'], config['build'], config['host']
    failures = []
    if target['lunch'] != f'{target["product"]}-{target["release"]}-{target["variant"]}':
        failures.append('target.lunch does not match product, release and variant')
    if target['variant'] == 'user' or target['keys'] != 'test-keys':
        failures.append('the alpha is a userdebug test-keys build')
    out, pixel = build['outDir'], host['outDir']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', out) or out in ('out', '.', '..'):
        failures.append('build.outDir must be one plain directory name under the source root')
    if out == pixel or pixel.startswith(out + '/') or out.startswith(pixel + '/'):
        failures.append('build.outDir must be disjoint from the Pixel outDir')
    for goal in build['makeGoals']:
        token(goal)
    package = config['hostPackage']
    if package['output'] != package['module'] + '.tar.gz':
        failures.append('hostPackage.output must be the module name plus .tar.gz (what the plugin installs)')
    if package['goal'] not in build['makeGoals']:
        failures.append('build.makeGoals must include the hostPackage goal')
    return failures

def missing_evidence(config):
    binding, evidence = config['sourceBinding'], config['hostEvidence']
    missing = [f'sourceBinding.{k}' for k in ('candidate', 'reconcileReceipt', 'reconcileReceiptSha256', 'apps') if not binding.get(k)]
    missing += [f'hostEvidence.{k}' for k in ('probeReceiptSha256', 'lunch') if not evidence.get(k)]
    lunch = evidence.get('lunch') or {}
    if lunch:
        missing += [f'hostEvidence.lunch.{k}' for k in LUNCH_VARS if not lunch.get(k)]
    return missing

def check_dispatch(config, sha, apps):
    binding = config['sourceBinding']
    if sha != binding['candidate']:
        raise CheckFailed(f'the retained tree is bound to {binding["candidate"]}, not {sha}')
    if apps != binding['apps']:
        raise CheckFailed(f'dispatched apps {apps} differ from the bound integration apps {binding["apps"]}')

def pinned_revision(project):
    for node in ET.parse(ROOT/'os/pinned-manifest.xml').getroot().iter('project'):
        if node.get('name') == project or node.get('path') == project:
            return node.get('revision')
    return None

def tree_failures(config):
    """Read-only facts of the retained tree; each failure names what is absent or different."""
    source = Path(config['host']['sourceRoot'])
    if not source.is_dir():
        return [f'host.sourceRoot {source} not present on this runner']
    failures = []
    cf = source/CUTTLEFISH
    head = subprocess.run(['git', '-C', str(cf), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    pinned = pinned_revision(CUTTLEFISH)
    if head.returncode or head.stdout.strip() != pinned:
        failures.append(f'{CUTTLEFISH} is not checked out at the pinned {pinned}')
    products = cf/'AndroidProducts.mk'
    product = config['target']['product']
    mapping = re.search(rf'^\s*{re.escape(product)}:\$\(LOCAL_DIR\)/(\S+?)\s*\\?$', products.read_text(), re.M) if products.is_file() else None
    if not mapping:
        failures.append(f'{CUTTLEFISH}/AndroidProducts.mk does not map {product}')
    elif not (cf/mapping[1]).is_file():
        failures.append(f'{product} makefile {CUTTLEFISH}/{mapping[1]} is absent')
    failures += host_package_failures(config, cf)
    failures += binding_failures(config, source)
    free = shutil.disk_usage(source).free // 2**30
    if free < config['build']['minFreeGiB']:
        failures.append(f'{free} GiB free under the source root, below build.minFreeGiB {config["build"]["minFreeGiB"]}')
    return failures

def host_package_failures(config, cf):
    """The Soong mechanism behind the host package goal, as pinned: the cvd_host_package plugin (by sha256 and by the
    declarations that make the goal and the output), the module declaration and the board platform the goal needs."""
    package = config['hostPackage']
    plugin, blueprint = cf/package['plugin'], cf/package['definedIn']
    if not plugin.is_file():
        return [f'{CUTTLEFISH}/{package["plugin"]} is absent; the {package["goal"]} goal is unverified']
    failures = []
    if sha256_file(plugin) != package['pluginSha256']:
        failures.append(f'{CUTTLEFISH}/{package["plugin"]} differs from the pinned plugin; its goal and output are unverified')
    text = plugin.read_text(errors='replace')
    for what, needle in (('module type', f'android.RegisterModuleType("{package["moduleType"]}"'),
                         ('goal', f'ctx.Phony("{package["goal"]}", info.TarballMetadata)'),
                         ('installed output', 'ctx.InstallFile(android.PathForModuleInstall(ctx), c.BaseModuleName()+".tar.gz", tarball)')):
        if needle not in text:
            failures.append(f'{CUTTLEFISH}/{package["plugin"]} does not declare the {what}')
    if package['boardPlatform'] not in re.findall(r'board_platform == "([\w-]+)"', text):
        failures.append(f'{CUTTLEFISH}/{package["plugin"]} declares {package["goal"]} only for other board platforms than {package["boardPlatform"]}')
    modules = re.findall(rf'^{re.escape(package["moduleType"])}\s*\{{\s*name:\s*"([^"]+)"', blueprint.read_text(errors='replace'), re.M) \
        if blueprint.is_file() else []
    if package['module'] not in modules:
        failures.append(f'{CUTTLEFISH}/{package["definedIn"]} does not declare {package["moduleType"]} {package["module"]}')
    return failures

def binding_failures(config, source):
    """The bound reconcile receipt is still the tree's latest, unchanged, and nothing it installed has drifted."""
    binding = config['sourceBinding']
    if not binding.get('reconcileReceipt'):
        return []
    shared = json.loads((ROOT/config['sharedHost']).read_text())
    directory = Path(shared['host']['integrate']['reconcile']['receiptDir'])
    receipts = sorted(directory.glob('[0-9][0-9][0-9][0-9]-*.json'))
    if not receipts or receipts[-1].name != binding['reconcileReceipt']:
        return [f'the latest reconcile receipt is {receipts[-1].name if receipts else None}, not the bound {binding["reconcileReceipt"]}']
    if sha256_file(receipts[-1]) != binding['reconcileReceiptSha256']:
        return [f'{receipts[-1].name} differs from the bound sha256']
    drifted = []
    for path, entry in json.loads(receipts[-1].read_text()).get('entries', {}).items():
        current = source/path
        actual = sha256_file(current) if current.is_file() and not current.is_symlink() else None
        if actual != entry.get('after'):
            drifted.append(path)
    return [f'tree drifted from the bound receipt: {", ".join(sorted(drifted))}'] if drifted else []

def locked(config):
    lock = open(config['host']['lockPath'], 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise CheckFailed('the shared builder lock is held by another job')
    return lock

def build_env(config, build_id, timestamp):
    return dict(os.environ, OUT_DIR=config['build']['outDir'], BUILD_NUMBER=build_id, BUILD_DATETIME=timestamp)

def steps(config):
    lunch = token(config['target']['lunch'])
    resolve = ['source build/envsetup.sh', f'lunch {lunch}'] + [
        f'echo {v}=${{{v}:-}}' if v in SHELL_VARS else f'echo {v}=$(get_build_var {v})' for v in LUNCH_VARS]
    make = ['source build/envsetup.sh', f'lunch {lunch}',
        f'm -j{int(config["build"]["makeJobs"])} ' + ' '.join(token(g) for g in config['build']['makeGoals'])]
    return ' && '.join(resolve), ' && '.join(make)

def resolve(config, env):
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', steps(config)[0]], cwd=config['host']['sourceRoot'],
        env=env, capture_output=True, text=True)
    if result.returncode:
        raise CheckFailed(f'lunch {config["target"]["lunch"]} did not resolve (exit {result.returncode})')
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line and line.split('=', 1)[0] in LUNCH_VARS)
    source = str(Path(config['host']['sourceRoot']).resolve()) + '/'
    for key in ('PRODUCT_OUT', 'HOST_OUT'):
        if (values.get(key) or '').startswith(source):  # recorded relative to the source root either way
            values[key] = values[key][len(source):]
    target = config['target']
    expected = {'TARGET_PRODUCT': target['product'], 'TARGET_RELEASE': target['release'], 'TARGET_BUILD_VARIANT': target['variant'],
        'TARGET_BOARD_PLATFORM': config['hostPackage']['boardPlatform']}
    wrong = [f'{k}={values.get(k) or "(empty)"}' for k, v in expected.items() if values.get(k) != v]
    out = config['build']['outDir']
    wrong += [f'{k}={values.get(k)} (outside {out})' for k in ('PRODUCT_OUT', 'HOST_OUT')
        if not (values.get(k) or '').startswith(out + '/')]
    if wrong:
        raise CheckFailed('lunch resolved ' + ', '.join(wrong))
    return values

def build_id(config, run_id):
    if not re.fullmatch(r'[1-9][0-9]*', str(run_id)):
        raise InvalidInput('--run-id must be the numeric Actions run id')
    return config['build']['buildIdPrefix'] + str(run_id)

def plan(config, sha, run_id, timestamp):
    resolve_steps, make_steps = steps(config)
    return {'receiptType': 'emulator-alpha-plan', 'executed': False, 'candidate': sha, 'emulatorBuildId': build_id(config, run_id),
        'environment': {'OUT_DIR': config['build']['outDir'], 'BUILD_NUMBER': build_id(config, run_id), 'BUILD_DATETIME': timestamp},
        'cwd': config['host']['sourceRoot'], 'lock': config['host']['lockPath'],
        'commands': [['bash', '-euo', 'pipefail', '-c', resolve_steps], ['bash', '-euo', 'pipefail', '-c', make_steps]],
        'missingEvidence': missing_evidence(config), 'configFailures': config_failures(config),
        'note': 'planned only; nothing was run, compiled or verified'}

def guard(config):
    failures = config_failures(config)
    if failures:
        raise CheckFailed('; '.join(failures))
    missing = missing_evidence(config)
    if missing:
        raise SetupMissing(missing)

def probe(config, run_id, timestamp, receipt_path):
    failures = config_failures(config)
    if failures:
        raise CheckFailed('; '.join(failures))
    lock = locked(config)
    try:
        failures = tree_failures(config)
        values = resolve(config, build_env(config, build_id(config, run_id), timestamp))
    finally:
        lock.close()
    receipt = {'receiptType': 'emulator-alpha-probe', 'executedMake': False, 'treeFailures': failures, 'lunch': values,
        'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    release_receipts.write_once(receipt_path, receipt)
    return receipt

def read_props(product_out):
    props = {}
    for name in PROPS:
        path = product_out/name
        if path.is_file():
            props[name] = dict(line.split('=', 1) for line in path.read_text(errors='replace').splitlines()
                if '=' in line and not line.startswith('#'))
    if 'system/build.prop' not in props:
        raise CheckFailed('PRODUCT_OUT has no system/build.prop')
    return props

def identity_failures(props, config, build, timestamp, device):
    marker = f'/{build}:{config["target"]["variant"]}/{config["target"]["keys"]}'
    failures = []
    for name, values in props.items():
        for key, value in values.items():
            if key.endswith('build.version.incremental') and value != build:
                failures.append(f'{name}: {key}={value}')
            if key.endswith('build.date.utc') and value != timestamp:
                failures.append(f'{name}: {key}={value}')
            if key.endswith('build.fingerprint') and marker not in value:
                failures.append(f'{name}: {key} does not name {build} with {config["target"]["variant"]}/{config["target"]["keys"]}')
            if key.endswith('product.device') and value != device:
                failures.append(f'{name}: {key}={value}')
    if props['system/build.prop'].get('ro.build.type') != config['target']['variant']:
        failures.append('ro.build.type is not ' + config['target']['variant'])
    return failures

def build(config, sha, apps, run_id, timestamp, receipt_dir, make=None):
    if not re.fullmatch(r'[0-9a-f]{40}', sha or '') or not re.fullmatch(r'\d{10}', timestamp or ''):
        raise InvalidInput('a full candidate SHA and a ten-digit BUILD_DATETIME are required')
    guard(config)
    check_dispatch(config, sha, apps)
    receipt_dir = Path(receipt_dir)
    if receipt_dir.exists():
        raise CheckFailed(f'{receipt_dir} already exists; use a new receipt directory')
    identity = build_id(config, run_id)
    lock = locked(config)
    try:
        failures = tree_failures(config)
        if failures:
            raise CheckFailed('; '.join(failures))
        env = build_env(config, identity, timestamp)
        values = resolve(config, env)
        recorded = config['hostEvidence']['lunch']
        if values != recorded:
            raise CheckFailed(f'lunch now resolves {values}, not the recorded hostEvidence {recorded}')
        started = time.time()
        source = Path(config['host']['sourceRoot'])
        (make or (lambda: subprocess.run(['bash', '-euo', 'pipefail', '-c', steps(config)[1]], cwd=source, env=env, check=True)))()
        product_out, host_out = source/values['PRODUCT_OUT'], source/values['HOST_OUT']
        stale = [n for n in config['build']['requiredImages'] if not (product_out/n).is_file() or (product_out/n).stat().st_mtime < started - 1]
        stale += [n for n in [config['hostPackage']['output']] if not (host_out/n).is_file() or (host_out/n).stat().st_mtime < started - 1]
        if stale:
            raise CheckFailed('not written by this build: ' + ', '.join(stale))
        failures = identity_failures(read_props(product_out), config, identity, timestamp, values['PRODUCT_DEVICE'])
        if failures:
            raise CheckFailed('; '.join(failures))
        images = {p.name: {'size': p.stat().st_size, 'sha256': sha256_file(p)} for p in sorted(product_out.glob('*.img'))}
        hosts = {n: {'size': (host_out/n).stat().st_size, 'sha256': sha256_file(host_out/n)} for n in [config['hostPackage']['output']]}
    finally:
        lock.close()
    props = read_props(product_out)['system/build.prop']
    receipt = {'receiptType': 'emulator-alpha-build', 'simulated': make is not None, 'testOnly': True, 'signed': False,
        'keys': config['target']['keys'], 'published': False, 'bootAttempted': False, 'bootVerified': False,
        'candidate': sha, 'sourceBinding': {k: config['sourceBinding'][k] for k in ('candidate', 'reconcileReceipt', 'reconcileReceiptSha256', 'apps')},
        'emulatorBuildId': identity, 'buildDatetime': timestamp, 'lunch': values, 'outDir': config['build']['outDir'],
        'makeGoals': config['build']['makeGoals'], 'images': images, 'hostOutputs': hosts,
        'buildProps': {k: props.get(k) for k in ('ro.build.fingerprint', 'ro.build.type', 'ro.build.version.incremental', 'ro.build.date.utc')},
        'notProven': NOT_PROVEN, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    receipt_dir.mkdir(parents=True)
    release_receipts.write_once(receipt_dir/'emulator-alpha-receipt.json', receipt)
    digest = sha256_file(receipt_dir/'emulator-alpha-receipt.json')
    (receipt_dir/'SHA256SUMS').write_text(f'{digest}  emulator-alpha-receipt.json\n')
    return receipt

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['plan', 'preflight', 'probe', 'build'])
    p.add_argument('--job', choices=['guard', 'builder'], default='guard')
    p.add_argument('--config', default=str(CONFIG))
    p.add_argument('--candidate-sha')
    p.add_argument('--apps', default='[]')
    p.add_argument('--run-id')
    p.add_argument('--build-timestamp')
    p.add_argument('--receipt-dir')
    p.add_argument('--receipt')
    a = p.parse_args(argv)
    try:
        config = load(a.config)
        if a.command == 'plan':
            result = plan(config, a.candidate_sha, a.run_id, a.build_timestamp)
        elif a.command == 'preflight':
            guard(config)
            if a.job == 'builder':
                failures = tree_failures(config)
                if failures:
                    raise CheckFailed('; '.join(failures))
            result = {'verdict': 'OK', 'job': a.job}
        elif a.command == 'probe':
            if not a.receipt:
                raise InvalidInput('--receipt is required')
            result = probe(config, a.run_id, a.build_timestamp or str(int(time.time())), a.receipt)
        else:
            result = build(config, a.candidate_sha, json.loads(a.apps), a.run_id, a.build_timestamp, a.receipt_dir)
    except SetupMissing as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
        return 4
    except InvalidInput as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2
    except (CheckFailed, release_receipts.ReceiptExists) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error)}), file=sys.stderr)
        return 1
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': error.__class__.__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0

if __name__ == '__main__':
    sys.exit(main())
