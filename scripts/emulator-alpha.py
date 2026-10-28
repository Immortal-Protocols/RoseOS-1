#!/usr/bin/env python3
"""os-release emulator-alpha mode: an unsigned, test-only Cuttlefish build of an independent, prepared source tree.

Target and policy are in os/release-pipeline/cuttlefish-alpha.json; the builder, retained Pixel source tree and lock
are shared with the Pixel config it names (sharedHost). The retained tree is only read: prepare copies its source
inputs (scripts/alpha_source.py) into alpha-source/<preparationId>/tree, applies OS's alpha delta there and writes
an immutable preparation.json bound to the base (sourceBinding, resolved manifest, reconcile receipt, apps, policy)
and the delta. Probe and build use only such a prepared tree, never the retained one, and never reconcile, adopt,
sign, publish or clean. Every host fact the checked-in config cannot prove is null there, and every command except
plan fails closed (exit 4) naming it.

  plan             print the commands a build would run, marked executed: false; runs nothing
  preflight        --job guard: the checked-in config alone (before the builder starts);
                   --job builder: also the pinned preparation, read-only (no lunch, no make)
  prepare          on the builder, under the shared lock: base and capacity checks, then the independent copy plus
                   delta; prints the preparation receipt
  verify-prepared  --preparation-id ID: full re-hash of a prepared tree against its manifest
  probe            --preparation-id ID, under the shared lock: the prepared tree's checks plus lunch resolution (which
                   writes build-system state only inside the alpha OUT_DIR of that tree); writes a probe receipt whose
                   values the lead records in hostEvidence and prepared
  build            under the shared lock: every check against the pinned preparation, lunch resolution equal to
                   hostEvidence, make of the configured goals, fresh required outputs, build identity in the partition
                   build.props, then a receipt hashing every top-level image and the host package. Unsigned, test-only,
                   boot not attempted
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
import alpha_source  # noqa: E402
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
    config['host']['reconcile'] = {k: shared['host']['integrate']['reconcile'][k] for k in ('receiptDir', 'integrationOwnedRoots')}
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
        failures.append('build.outDir must be one plain directory name under the build root')
    if out == pixel or pixel.startswith(out + '/') or out.startswith(pixel + '/'):
        failures.append('build.outDir must be disjoint from the Pixel outDir')
    for goal in build['makeGoals']:
        token(goal)
    package = config['hostPackage']
    if package['output'] != package['module'] + '.tar.gz':
        failures.append('hostPackage.output must be the module name plus .tar.gz (what the plugin installs)')
    if package['goal'] not in build['makeGoals']:
        failures.append('build.makeGoals must include the hostPackage goal')
    preparation = config['preparation']
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', preparation['root']) or '..' in preparation['root'].split('/'):
        failures.append('preparation.root must be a plain absolute path')
    elif Path(preparation['root']) == Path(host['sourceRoot']) or Path(preparation['root']).is_relative_to(host['sourceRoot']) \
            or Path(host['sourceRoot']).is_relative_to(preparation['root']):
        failures.append('preparation.root must be disjoint from the retained source tree')
    if out in preparation['exclusions']['rootNames'] or not any(out.startswith(p) for p in preparation['exclusions']['rootPrefixes']):
        failures.append('build.outDir must be an excluded output name, so a preparation never copies build output')
    return failures

def missing_evidence(config):
    binding, evidence, prepared = config['sourceBinding'], config['hostEvidence'], config['prepared']
    missing = [f'sourceBinding.{k}' for k in ('candidate', 'reconcileReceipt', 'reconcileReceiptSha256', 'apps') if not binding.get(k)]
    missing += [f'hostEvidence.{k}' for k in ('probeReceiptSha256', 'lunch') if not evidence.get(k)]
    lunch = evidence.get('lunch') or {}
    if lunch:
        missing += [f'hostEvidence.lunch.{k}' for k in LUNCH_VARS if not lunch.get(k)]
    missing += [f'prepared.{k}' for k in ('preparationId', 'receiptSha256') if not prepared.get(k)]
    return missing

def check_dispatch(config, sha, apps):
    binding = config['sourceBinding']
    if sha != binding['candidate']:
        raise CheckFailed(f'the prepared source is based on {binding["candidate"]}, not {sha}')
    if apps != binding['apps']:
        raise CheckFailed(f'dispatched apps {apps} differ from the bound integration apps {binding["apps"]}')

def pinned_revision(project):
    for node in ET.parse(ROOT/'os/pinned-manifest.xml').getroot().iter('project'):
        if node.get('name') == project or node.get('path') == project:
            return node.get('revision')
    return None

def git_head(root, project):
    result = subprocess.run(['git', '-C', str(Path(root)/project), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None

def source_failures(config, root, cuttlefish_head):
    """Cuttlefish facts of a source tree (the retained base before preparation, or a prepared tree, whose revision comes
    from its receipt because the copy carries no .git): pinned revision, product mapping, host package, disk."""
    root = Path(root)
    if not root.is_dir():
        return [f'{root} not present on this runner']
    failures = []
    cf = root/CUTTLEFISH
    pinned = pinned_revision(CUTTLEFISH)
    if cuttlefish_head != pinned:
        failures.append(f'{CUTTLEFISH} is not at the pinned {pinned}')
    products = cf/'AndroidProducts.mk'
    product = config['target']['product']
    mapping = re.search(rf'^\s*{re.escape(product)}:\$\(LOCAL_DIR\)/(\S+?)\s*\\?$', products.read_text(), re.M) if products.is_file() else None
    if not mapping:
        failures.append(f'{CUTTLEFISH}/AndroidProducts.mk does not map {product}')
    elif not (cf/mapping[1]).is_file():
        failures.append(f'{product} makefile {CUTTLEFISH}/{mapping[1]} is absent')
    failures += host_package_failures(config, cf)
    free = shutil.disk_usage(root).free // 2**30
    if free < config['build']['minFreeGiB']:
        failures.append(f'{free} GiB free under {root}, below build.minFreeGiB {config["build"]["minFreeGiB"]}')
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

def reconcile_receipt(config):
    directory = Path(config['host']['reconcile']['receiptDir'])
    receipts = sorted(directory.glob('[0-9][0-9][0-9][0-9]-*.json'))
    return receipts[-1] if receipts else None

def binding_failures(config, source):
    """The bound reconcile receipt is still the retained tree's latest, unchanged, and nothing it installed has drifted."""
    binding = config['sourceBinding']
    latest = reconcile_receipt(config)
    if latest is None or latest.name != binding['reconcileReceipt']:
        return [f'the latest reconcile receipt is {latest.name if latest else None}, not the bound {binding["reconcileReceipt"]}']
    if sha256_file(latest) != binding['reconcileReceiptSha256']:
        return [f'{latest.name} differs from the bound sha256']
    return drift_failures(source, json.loads(latest.read_text()).get('entries', {}), 'the bound receipt')

def drift_failures(root, entries, what):
    drifted = []
    for path, entry in entries.items():
        current = Path(root)/path
        actual = sha256_file(current) if current.is_file() and not current.is_symlink() else None
        if actual != entry.get('after'):
            drifted.append(path)
    return [f'tree drifted from {what}: {", ".join(sorted(drifted))}'] if drifted else []

def locked(config):
    lock = open(config['host']['lockPath'], 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise CheckFailed('the shared builder lock is held by another job')
    return lock

# ---------------------------------------------------------------- preparation

def contract(config):
    """OS's alpha contract at its pinned digest, consistent with this config; the delta patches with their digests."""
    delta = config['preparation']['osDelta']
    path = ROOT/delta['contract']
    if sha256_file(path) != delta['contractSha256']:
        raise CheckFailed(f'{delta["contract"]} differs from preparation.osDelta.contractSha256')
    document = json.loads(path.read_text())
    binding, problems = config['sourceBinding'], []
    base = document['base']
    if (base['candidate'], base['reconcileReceipt'], base['reconcileSha256']) != \
            (binding['candidate'], binding['reconcileReceipt'], binding['reconcileReceiptSha256']):
        problems.append('contract base differs from sourceBinding')
    if [[a['package'], a['version']] for a in document['apps']] != binding['apps']:
        problems.append('contract apps differ from sourceBinding.apps')
    if document['target']['lunch'] != config['target']['lunch']:
        problems.append('contract target differs from target.lunch')
    if document['updaterMode'] != 'excluded-no-compatible-feed' or document['updaterEndpoint'] is not None:
        problems.append('contract updater routing is not excluded-no-compatible-feed with no endpoint')
    if document['pinned'].get(CUTTLEFISH) != pinned_revision(CUTTLEFISH):
        problems.append('contract Cuttlefish pin differs from os/pinned-manifest.xml')
    patches = []
    for patch in document['osDelta']['patches']:
        file = ROOT/patch['path']
        if not file.is_file() or sha256_file(file) != patch['sha256']:
            problems.append(f'{patch["path"]} is absent or differs from its contract digest')
        patches.append({**patch, 'file': str(file)})
    touched = sorted({(p['project'], line[6:].split('\t')[0]) for p in patches if Path(p['file']).is_file()
        for line in Path(p['file']).read_text().splitlines() if line.startswith(('--- a/', '+++ b/'))})
    declared = sorted((c['project'], c['path']) for c in document['osDelta']['changedFiles'])
    if touched != declared:
        problems.append(f'the delta patches touch {touched}, not the declared changed files {declared}')
    if problems:
        raise CheckFailed('; '.join(problems))
    return document, {'commit': delta['commit'], 'patches': patches,
        'changedFiles': [{'project': c['project'], 'path': c['path']} for c in document['osDelta']['changedFiles']]}

def base_binding(config):
    evidence = config['preparation']['baseEvidence']
    binding = config['sourceBinding']
    return {'baseCandidate': binding['candidate'], 'baseResolvedManifestSha256': evidence['resolvedManifest']['sha256'],
        'baseReconcileReceipt': binding['reconcileReceipt'], 'baseReconcileSha256': binding['reconcileReceiptSha256']}

def prepare_missing(config):
    missing = [f'sourceBinding.{k}' for k in ('candidate', 'reconcileReceipt', 'reconcileReceiptSha256', 'apps') if not config['sourceBinding'].get(k)]
    evidence = config['preparation']['baseEvidence']
    missing += [f'preparation.baseEvidence.{k}.sha256' for k in ('resolvedManifest', 'policy') if not evidence[k].get('sha256')]
    return missing

def base_failures(config, document):
    """Read-only facts of the retained base before copying: binding and drift, revisions, evidence files, apps."""
    source = Path(config['host']['sourceRoot'])
    failures = source_failures(config, source, git_head(source, CUTTLEFISH))
    failures = [f for f in failures if 'below build.minFreeGiB' not in f]  # capacity is checked against the copy budget
    failures += binding_failures(config, source)
    for project, pin in document['pinned'].items():
        if git_head(source, project) != pin:
            failures.append(f'{project} is not at the contract pin {pin}')
    for name, item in config['preparation']['baseEvidence'].items():
        path = Path(item['path'])
        if not path.is_file() or sha256_file(path) != item['sha256']:
            failures.append(f'base evidence {name} ({path}) is absent or differs from its recorded sha256')
    return failures

def app_files(root, owned_roots, apps):
    """Each contract app APK by digest among the integration-owned roots of a tree."""
    found = {}
    for owned in owned_roots:
        for path in sorted((Path(root)/owned).rglob('*.apk')) if (Path(root)/owned).is_dir() else []:
            if path.is_file() and not path.is_symlink():
                found.setdefault(sha256_file(path), str(path.relative_to(root)))
    missing = [f'{a["package"]} {a["version"]} ({a["sha256"][:12]})' for a in apps if a['sha256'] not in found]
    return [{**a, 'path': found.get(a['sha256'])} for a in apps], missing

def prepare(config, now=time.time):
    failures = config_failures(config)
    if failures:
        raise CheckFailed('; '.join(failures))
    missing = prepare_missing(config)
    if missing:
        raise SetupMissing(missing)
    document, delta = contract(config)
    preparation = config['preparation']
    lock = locked(config)
    try:
        failures = base_failures(config, document)
        if failures:
            raise CheckFailed('; '.join(failures))
        source = Path(config['host']['sourceRoot'])
        apps, absent = app_files(source, config['host']['reconcile']['integrationOwnedRoots'], document['apps'])
        if absent:
            raise CheckFailed('apps not found in the base integration: ' + ', '.join(absent))
        entries = json.loads(reconcile_receipt(config).read_text()).get('entries', {})
        heads = {p: git_head(source, p) for p in preparation['gitHeadProjects']}
        extra = {'target': config['target']['lunch'], 'apps': apps, 'policySha256': preparation['baseEvidence']['policy']['sha256'],
            'updaterMode': document['updaterMode'], 'updaterEndpoint': document['updaterEndpoint'], 'baseGitHeads': heads,
            'outDir': config['build']['outDir'], 'contractSha256': preparation['osDelta']['contractSha256'],
            'baseReconcileEntries': entries,
            'preservationPaths': sorted(set(entries) | {a['path'] for a in apps} |
                {f'{c["project"]}/{c["path"]}' for c in delta['changedFiles']})}
        gib = 2**30
        return alpha_source.prepare(source, preparation['root'], base_binding(config), delta, preparation['exclusions'], extra,
            (preparation['copyBudgetGiB'] + config['build']['minFreeGiB']) * gib, preparation['copyBudgetGiB'] * gib, now=now)
    except alpha_source.PreparationFailed as error:
        raise CheckFailed(str(error))
    finally:
        lock.close()

def preparation_dir(config, preparation_id):
    if not re.fullmatch(r'[0-9a-f]{64}', preparation_id or ''):
        raise InvalidInput('--preparation-id must be the 64-hex preparation id')
    return Path(config['preparation']['root'])/preparation_id

def prepared_failures(config, preparation_id, receipt_sha256=None):
    """A prepared tree against its receipt and this config: identity, base binding, delta, apps, policy, routing,
    changed files, manifest digest and the Cuttlefish checks (revision from the receipt). Returns (receipt, failures)."""
    directory = preparation_dir(config, preparation_id)
    path = directory/'preparation.json'
    if not path.is_file():
        return None, [f'{path} is absent: no preparation with this id']
    digest = sha256_file(path)
    if receipt_sha256 and digest != receipt_sha256:
        return None, [f'{path} differs from prepared.receiptSha256']
    receipt = json.loads(path.read_text())
    tree = directory/'tree'
    try:
        _, delta = contract(config)
    except CheckFailed as error:
        return receipt, [str(error)]
    failures = []
    expected = {'schemaVersion': 1, 'kind': 'rose-cuttlefish-source-preparation', 'preparationId': preparation_id,
        'preparedRoot': str(tree), **base_binding(config), 'osDeltaCommit': delta['commit'],
        'patchDigests': [{'path': p['path'], 'project': p['project'], 'sha256': p['sha256']} for p in delta['patches']],
        'target': config['target']['lunch'], 'updaterMode': 'excluded-no-compatible-feed', 'updaterEndpoint': None,
        'policySha256': config['preparation']['baseEvidence']['policy']['sha256'], 'outDir': config['build']['outDir']}
    failures += [f'receipt {k} is {receipt.get(k)!r}, not {v!r}' for k, v in expected.items() if receipt.get(k) != v]
    if alpha_source.preparation_id(base_binding(config), delta, receipt.get('changedFiles')) != preparation_id:
        failures.append('the preparation id does not match its base, delta and changed files')
    if [[a['package'], a['version']] for a in receipt.get('apps', [])] != config['sourceBinding']['apps']:
        failures.append('receipt apps differ from sourceBinding.apps')
    for app in receipt.get('apps', []):
        file = tree/(app.get('path') or '-')
        if not file.is_file() or sha256_file(file) != app['sha256']:
            failures.append(f'{app["package"]} APK in the prepared tree differs from {app["sha256"][:12]}')
    for change in receipt.get('changedFiles', []):
        file = tree/change['project']/change['path']
        if not file.is_file() or sha256_file(file) != change['afterSha256']:
            failures.append(f'{change["project"]}/{change["path"]} differs from its prepared afterSha256')
    changed = {f'{c["project"]}/{c["path"]}' for c in receipt.get('changedFiles', [])}
    failures += drift_failures(tree, {p: e for p, e in receipt.get('baseReconcileEntries', {}).items() if p not in changed},
        'the base reconcile entries')
    manifest = directory/receipt.get('manifest', 'manifest.tsv')
    if not manifest.is_file() or sha256_file(manifest) != receipt.get('selectionSha256'):
        failures.append('the preparation manifest differs from selectionSha256')
    failures += source_failures(config, tree, (receipt.get('baseGitHeads') or {}).get(CUTTLEFISH))
    return receipt, failures

def verify_prepared(config, preparation_id):
    receipt, failures = prepared_failures(config, preparation_id)
    if receipt is None:
        raise CheckFailed('; '.join(failures))
    directory = preparation_dir(config, preparation_id)
    differences = alpha_source.verify_tree(directory/'tree', (directory/receipt['manifest']).read_text(), receipt['changedFiles'],
        skip={config['build']['outDir']})
    failures += differences
    return {'verdict': 'FAIL' if failures else 'OK', 'preparationId': preparation_id, 'failures': failures[:200],
        'failureCount': len(failures), 'fullRehash': True}

# ---------------------------------------------------------------- probe and build

def build_env(config, build_id, timestamp):
    return dict(os.environ, OUT_DIR=config['build']['outDir'], BUILD_NUMBER=build_id, BUILD_DATETIME=timestamp)

def steps(config):
    lunch = token(config['target']['lunch'])
    resolve = ['source build/envsetup.sh', f'lunch {lunch}'] + [
        f'echo {v}=${{{v}:-}}' if v in SHELL_VARS else f'echo {v}=$(get_build_var {v})' for v in LUNCH_VARS]
    make = ['source build/envsetup.sh', f'lunch {lunch}',
        f'm -j{int(config["build"]["makeJobs"])} ' + ' '.join(token(g) for g in config['build']['makeGoals'])]
    return ' && '.join(resolve), ' && '.join(make)

def resolve(config, env, root):
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', steps(config)[0]], cwd=root, env=env, capture_output=True, text=True)
    if result.returncode:
        raise CheckFailed(f'lunch {config["target"]["lunch"]} did not resolve (exit {result.returncode})')
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line and line.split('=', 1)[0] in LUNCH_VARS)
    source = str(Path(root).resolve()) + '/'
    for key in ('PRODUCT_OUT', 'HOST_OUT'):
        if (values.get(key) or '').startswith(source):  # recorded relative to the build root either way
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
    prepared = config['prepared'].get('preparationId')
    return {'receiptType': 'emulator-alpha-plan', 'executed': False, 'candidate': sha, 'emulatorBuildId': build_id(config, run_id),
        'environment': {'OUT_DIR': config['build']['outDir'], 'BUILD_NUMBER': build_id(config, run_id), 'BUILD_DATETIME': timestamp},
        'cwd': str(Path(config['preparation']['root'])/prepared/'tree') if prepared else None, 'lock': config['host']['lockPath'],
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

def probe(config, preparation_id, run_id, timestamp, receipt_path):
    failures = config_failures(config)
    if failures:
        raise CheckFailed('; '.join(failures))
    lock = locked(config)
    try:
        receipt, failures = prepared_failures(config, preparation_id)
        if receipt is None:
            raise CheckFailed('; '.join(failures))
        root = preparation_dir(config, preparation_id)/'tree'
        values = resolve(config, build_env(config, build_id(config, run_id), timestamp), root)
    finally:
        lock.close()
    result = {'receiptType': 'emulator-alpha-probe', 'executedMake': False, 'preparationId': preparation_id,
        'preparationReceiptSha256': sha256_file(preparation_dir(config, preparation_id)/'preparation.json'),
        'treeFailures': failures, 'lunch': values, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    release_receipts.write_once(receipt_path, result)
    return result

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
    prepared = config['prepared']
    lock = locked(config)
    try:
        preparation, failures = prepared_failures(config, prepared['preparationId'], prepared['receiptSha256'])
        if failures:
            raise CheckFailed('; '.join(failures))
        root = preparation_dir(config, prepared['preparationId'])/'tree'
        env = build_env(config, identity, timestamp)
        values = resolve(config, env, root)
        recorded = config['hostEvidence']['lunch']
        if values != recorded:
            raise CheckFailed(f'lunch now resolves {values}, not the recorded hostEvidence {recorded}')
        started = time.time()
        (make or (lambda: subprocess.run(['bash', '-euo', 'pipefail', '-c', steps(config)[1]], cwd=root, env=env, check=True)))()
        product_out, host_out = root/values['PRODUCT_OUT'], root/values['HOST_OUT']
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
        'preparation': {'preparationId': prepared['preparationId'], 'receiptSha256': prepared['receiptSha256'],
            'effectiveSourceIdentity': preparation['effectiveSourceIdentity'], 'osDeltaCommit': preparation['osDeltaCommit'],
            'updaterMode': preparation['updaterMode'], 'preparedRoot': preparation['preparedRoot']},
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
    p.add_argument('command', choices=['plan', 'preflight', 'prepare', 'verify-prepared', 'probe', 'build'])
    p.add_argument('--job', choices=['guard', 'builder'], default='guard')
    p.add_argument('--config', default=str(CONFIG))
    p.add_argument('--candidate-sha')
    p.add_argument('--apps', default='[]')
    p.add_argument('--run-id')
    p.add_argument('--build-timestamp')
    p.add_argument('--receipt-dir')
    p.add_argument('--receipt')
    p.add_argument('--preparation-id')
    a = p.parse_args(argv)
    try:
        config = load(a.config)
        if a.command == 'plan':
            result = plan(config, a.candidate_sha, a.run_id, a.build_timestamp)
        elif a.command == 'preflight':
            guard(config)
            if a.job == 'builder':
                _, failures = prepared_failures(config, config['prepared']['preparationId'], config['prepared']['receiptSha256'])
                if failures:
                    raise CheckFailed('; '.join(failures))
            result = {'verdict': 'OK', 'job': a.job}
        elif a.command == 'prepare':
            result = prepare(config)
        elif a.command == 'verify-prepared':
            result = verify_prepared(config, a.preparation_id)
            if result['verdict'] != 'OK':
                print(json.dumps(result, indent=2, sort_keys=True))
                return 1
        elif a.command == 'probe':
            if not a.receipt:
                raise InvalidInput('--receipt is required')
            result = probe(config, a.preparation_id, a.run_id, a.build_timestamp or str(int(time.time())), a.receipt)
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
