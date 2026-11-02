#!/usr/bin/env python3
"""FAM-BLAZER-BUILD-004 / BLAZER-PREP: blazer (Pixel 10 Pro) preparation and unsigned build-only stages.

The recipe (os/build-only/blazer.json) binds only checked-in pinned sources: lunch blazer-cur-user, the indexed factory
image name, URL and sha256, the vendor/adevtool, vendor/state and laguna kernel pins of os/pinned-manifest.xml, adevtool
generation with an explicit -d blazer, and the proposal bounds of each preparation stage. It binds no host.

  blazer-build-only.py plan      --stage S ...   the rendered command and the preflight report; runs nothing
  blazer-build-only.py preflight --stage S ...   the report alone (exit 0 ready, 1 not ready)
  blazer-build-only.py run       --stage S ... --receipt-dir NEW   preflight, then the stage under the lock

Preparation stages (never compile, sign, publish or flash; never in the production source tree):
  download  --host --lock --download-dir
            stream the pinned official URL to DIR/<file>.part (byte and time bound), rename to DIR/<file> only when its
            sha256 equals the pin; an existing archive is never replaced; a failed attempt removes only its own .part
  vendor    --host --lock --download-dir --source-root ISOLATED --node PATH
            `node --version` must meet the floor, then `vendor/adevtool/bin/run generate-all -d blazer` with
            ADEVTOOL_IMG_DOWNLOAD_DIR=DIR in ISOLATED, which must not be, contain or sit inside the production source root
            (os/release-pipeline/frankel.json host.sourceRoot); the verified archive, pins, host tools and kernel must be
            present and every collision path absent; consumed bytes are measured afterwards against the bound
  lunch     --host --lock --source-root ISOLATED
            `lunch blazer-cur-user` and the TARGET_PRODUCT/RELEASE/BUILD_VARIANT/DEVICE checks with OUT_DIR=out-blazer-lunch;
            no make goal
Build stage (outside preparation, unchanged): compile --host --jobs --lock --source-root --integration-dir --inputs-receipt.

Every stage refuses on a symlink in an operator path, a held lock (probed non-blocking, held with LOCK_NB while
running), an existing output (nothing is deleted, reused or overwritten except the stage's own .part) or available
bytes minus the stage bound below capacity.reserveBytes. Bounds are labelled proposals, not measurements; the stage
timeouts total 4.5 h, excluding construction of the isolated tree.

vendor and lunch run in their own session and process group with PATH limited to /usr/bin:/bin (vendor: the verified
node's directory first, so adevtool resolves exactly that file; its identity is re-checked just before the run). The
byte limit is a sampled stop, not a filesystem quota: every preparation.limits.sampleSeconds the watched outputs are
compared with the bound and the filesystem's available bytes with the reserve, and an overrun or the timeout ends the
group (SIGTERM, then SIGKILL after killGraceSeconds); live members left after a normal exit are ended the same way.
Writes between samples can overshoot, and descendants that start their own session are not reached: an enforced quota
and full containment need host-side cgroup/quota configuration that this tool does not have.

  blazer-build-only.py inspect --host --lock [--source-root] [--download-dir] [--node] [--max-entries] [--max-seconds]
            metadata only, for a later approved observation (at most 20 minutes): lstat identities, isolation, statvfs,
            pinned project presence, adevtool_revision (128 bytes), bounded collision/download sizes and the node file
            identity and PATH resolution; starts no process, opens no connection and writes nothing
Exit status: 0 ready/success, 1 not ready or the stage failed, 2 invalid input. Source tests use synthetic trees, a fake
fetcher and a fake runner only.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_receipts  # noqa: E402
import blazer_integration  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT/'os/build-only/blazer.json'
DEVICE = 'blazer'
PREPARATION = ('download', 'vendor', 'lunch')
STAGES = PREPARATION + ('compile',)
BUILD_INDEX = 'os/tests/fixtures/adevtool-17df3b79/adevtool/config/build-index/build-index-main.yml.excerpt.json'
FACTORY_URL = 'https://dl.google.com/dl/android/aosp/'
TREE_NEEDS = ('vendor/adevtool/bin/run', 'build/envsetup.sh', 'prebuilts/build-tools/path/linux-x86',
    'prebuilts/jdk/jdk21/linux-x86/bin')
HOST_LABEL = re.compile(r'[a-z0-9][a-z0-9.-]{0,62}')
CHUNK = 1 << 20
LUNCH_VARS = ('TARGET_PRODUCT', 'TARGET_RELEASE', 'TARGET_BUILD_VARIANT', 'TARGET_DEVICE')
SAFE_PATH = '/usr/bin:/bin'

class InvalidInput(Exception):
    pass

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()

def index_block(root, build_id):
    """The indented entries under 'blazer BUILD_ID:' in the pinned adevtool build index excerpt."""
    lines = json.loads((root/BUILD_INDEX).read_text())['lines']
    block, inside = [], False
    for _, text in sorted(lines.items(), key=lambda item: int(item[0])):
        if not text.startswith(' '):
            inside = text.strip() == f'{DEVICE} {build_id}:'
        elif inside:
            block.append(text.strip())
    return block

def pointer(root, source):
    """'FILE dotted.field' read from a checked-in JSON source."""
    file, field = source.split(' ', 1)
    value = json.loads((root/file).read_text())
    for key in field.split('.'):
        value = value[key]
    return value

def load(path=RECIPE, root=ROOT):
    """The recipe, checked against its checked-in sources; InvalidInput on any drift."""
    recipe = json.loads(Path(path).read_text())
    problems = []
    if recipe.get('kind') != 'rose-build-only-recipe' or recipe.get('device') != DEVICE:
        raise InvalidInput(f'not a {DEVICE} build-only recipe; this route builds {DEVICE} only')
    contract = json.loads((root/recipe['deviceContract']).read_text())
    entry = contract['devices'].get(DEVICE) or {}
    if recipe['lunch'] != entry.get('lunch', {}).get('expected'):
        problems.append(f'lunch {recipe["lunch"]!r} is not the contract\'s')
    image = entry.get('vendorInputs', {}).get('factoryImage', {})
    factory = recipe['factory']
    if (factory['file'], factory['sha256']) != (image.get('file'), image.get('sha256')):
        problems.append('factory image name or sha256 differs from the device contract')
    if f'factory: {factory["sha256"]} {factory["file"]}' not in index_block(root, contract['shared']['vendorBuildId']):
        problems.append('factory image is not the pinned adevtool build index entry')
    if factory['url'] != FACTORY_URL + factory['file']:
        problems.append('factory URL is not the adevtool index URL for that file')
    manifest = {p.get('path'): p.get('revision') for p in ElementTree.parse(root/recipe['manifest']).getroot().iter('project')}
    for path, revision in recipe['pins'].items():
        if revision != manifest.get(path) or revision != contract['source']['projects'].get(path):
            problems.append(f'pin {path} differs from the pinned manifest or device contract')
    if set(recipe['pins']) != {'vendor/adevtool', 'vendor/state', 'device/google/laguna-kernels/6.6'}:
        problems.append('pins must be exactly vendor/adevtool, vendor/state and the laguna kernel')
    if recipe['kernelDir'] != entry.get('kernelDir') or not recipe['kernelDir'].startswith('device/google/laguna-kernels/6.6/'):
        problems.append('kernelDir differs from the device contract')
    if recipe['generate']['argv'] != ['vendor/adevtool/bin/run', 'generate-all', '-d', DEVICE]:
        problems.append('generation must name -d blazer explicitly (never the all-device default)')
    if recipe['compile']['outDir'] != 'out-blazer-build':
        problems.append('compile OUT_DIR must be out-blazer-build')
    flavor = json.loads((root/recipe['flavor']).read_text())
    if flavor['device']['lunch'] != recipe['lunch'] or flavor['provenance']['adevtool'] != recipe['pins']['vendor/adevtool']:
        problems.append('recipe differs from the blazer flavor contract')
    for forbidden in entry.get('forbiddenPatchSets', []):
        if forbidden in json.dumps(recipe):
            problems.append(f'recipe references {forbidden}, which never applies to {DEVICE}')
    capacity = recipe['capacity']
    if capacity.get('allowanceRequired') is not True:
        problems.append('capacity.allowanceRequired must be true: there is no isolated-host allowance exception')
    if not isinstance(capacity.get('reserveBytes'), int) or capacity['reserveBytes'] < 322122547200:
        problems.append('capacity.reserveBytes must be at least the 300 GiB reserve')
    problems += preparation_problems(recipe, root, contract)
    if problems:
        raise InvalidInput('; '.join(problems))
    return recipe

def positive(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0

def preparation_problems(recipe, root, contract):
    prep, problems = recipe.get('preparation') or {}, []
    if prep.get('stages') != list(PREPARATION):
        return [f'preparation.stages must be exactly {list(PREPARATION)}']
    limits = prep.get('limits') or {}
    if limits.get('enforcement') != 'sampled-stop' or not (positive(limits.get('sampleSeconds')) and positive(limits.get('killGraceSeconds'))):
        problems.append('preparation.limits must be the sampled-stop enforcement with positive sampleSeconds and killGraceSeconds')
    total = sum(((prep.get(stage) or {}).get('timeoutSeconds') or 0) for stage in PREPARATION)
    if prep.get('totalTimeoutSeconds') != total:
        problems.append(f'preparation.totalTimeoutSeconds must be the sum of the stage timeouts ({total})')
    if prep.get('boundsKind') != 'proposal':
        problems.append('preparation bounds must be labelled proposal: no Blazer preparation has been measured')
    for stage in PREPARATION:
        spec = prep.get(stage) or {}
        if not (positive(spec.get('boundBytes')) and positive(spec.get('timeoutSeconds'))):
            problems.append(f'preparation.{stage} needs a positive boundBytes and timeoutSeconds')
    try:
        production = pointer(root, prep['productionSourceRootFrom'])
        lock = pointer(root, prep['lockFrom'])
    except (KeyError, OSError, ValueError, TypeError):
        return problems + ['preparation.productionSourceRootFrom and lockFrom must resolve in checked-in sources']
    if not (isinstance(production, str) and production.startswith('/') and isinstance(lock, str) and lock.startswith('/')):
        problems.append('the production source root and lock must be absolute paths')
    vendor = prep.get('vendor') or {}
    unpacked = f'vendor/adevtool/dl/unpacked/{DEVICE}-{contract["shared"]["vendorBuildId"]}'
    if vendor.get('collisionPaths') != [f'vendor/google_devices/{DEVICE}', unpacked]:
        problems.append(f'preparation.vendor.collisionPaths must be the generated vendor tree and {unpacked}')
    node = vendor.get('node') or {}
    if not (positive(node.get('minMajor')) and positive(node.get('versionTimeoutSeconds'))):
        problems.append('preparation.vendor.node needs a positive minMajor and versionTimeoutSeconds')
    lunch = prep.get('lunch') or {}
    product, release, variant = recipe['lunch'].split('-')
    if lunch.get('expect') != {'TARGET_PRODUCT': product, 'TARGET_RELEASE': release, 'TARGET_BUILD_VARIANT': variant,
                               'TARGET_DEVICE': DEVICE}:
        problems.append(f'preparation.lunch.expect must follow lunch {recipe["lunch"]}')
    if lunch.get('outDir') in (None, recipe['compile']['outDir']) or '/' in str(lunch.get('outDir')):
        problems.append('preparation.lunch.outDir must be its own top-level OUT_DIR, not the compile OUT_DIR')
    return problems

def operator(host, source_root, jobs, lock, download_dir=None, integration_dir=None, max_jobs=256, inputs_receipt=None,
             node=None):
    """The operator's host inputs, validated for shape only. Nothing is invented: every value is required."""
    if not HOST_LABEL.fullmatch(str(host or '')):
        raise InvalidInput('--host must be the operator\'s builder label (lowercase letters, digits, . and -)')
    if jobs is not None and (not isinstance(jobs, int) or isinstance(jobs, bool) or not 1 <= jobs <= max_jobs):
        raise InvalidInput(f'--jobs must be an integer from 1 to {max_jobs}')
    paths = {'sourceRoot': source_root, 'lock': lock, 'downloadDir': download_dir, 'integrationDir': integration_dir,
        'inputsReceipt': inputs_receipt, 'node': node}
    for name, value in paths.items():
        if value is None:
            continue
        path = Path(value)
        if not path.is_absolute() or '..' in path.parts:
            raise InvalidInput(f'{name} must be an absolute path without ..')
    return {'host': host, 'jobs': jobs, **{k: (Path(v) if v is not None else None) for k, v in paths.items()}}

def symlink_in(path):
    """The first existing component of path that is a symlink, or None."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current/part
        if current.is_symlink():
            return current
        if not os.path.lexists(current):
            return None
    return None

def commands(recipe, op):
    """The exact argv, cwd and extra environment of each stage, rendered from the recipe and the operator's inputs."""
    rendered = {}
    prep = recipe['preparation']
    if op['downloadDir'] is not None:
        factory = recipe['factory']
        rendered['download'] = {'url': factory['url'], 'part': str(op['downloadDir']/(factory['file'] + prep['download']['partSuffix'])),
            'final': str(op['downloadDir']/factory['file']), 'sha256': factory['sha256'],
            'maxBytes': prep['download']['boundBytes'], 'timeoutSeconds': prep['download']['timeoutSeconds']}
    if op['sourceRoot'] is not None:
        src = str(op['sourceRoot'])
        if op['downloadDir'] is not None:
            node_dir = str(op['node'].parent) if op['node'] else None
            rendered['vendor'] = {'cwd': src, 'env': {recipe['generate']['downloadEnv']: str(op['downloadDir']),
                    'PATH': f'{node_dir}:{SAFE_PATH}' if node_dir else SAFE_PATH},
                'argv': list(recipe['generate']['argv']), 'timeoutSeconds': prep['vendor']['timeoutSeconds'],
                'nodeVersion': [str(op['node']) if op['node'] else 'node', '--version']}
        checks = ' && '.join(f'echo {v}=${v}' for v in LUNCH_VARS[:3]) + ' && echo TARGET_DEVICE=$(get_build_var TARGET_DEVICE)'
        rendered['lunch'] = {'cwd': src, 'env': {'OUT_DIR': prep['lunch']['outDir'], 'PATH': SAFE_PATH},
            'argv': ['bash', '-c', f'source build/envsetup.sh && lunch {recipe["lunch"]} && {checks}'],
            'timeoutSeconds': prep['lunch']['timeoutSeconds']}
        if op['jobs'] is not None:
            make = 'm -j{} {}'.format(op['jobs'], ' '.join(recipe['compile']['makeGoals']))
            rendered['compile'] = {'cwd': src, 'env': {'OUT_DIR': recipe['compile']['outDir']},
                'argv': ['bash', '-c', f'source build/envsetup.sh && lunch {recipe["lunch"]} && {make}']}
    return rendered

def lock_free(path):
    """True when nobody holds the builder lock (probed without keeping it)."""
    try:
        with open(path, 'a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle, fcntl.LOCK_UN)
        return True
    except BlockingIOError:
        return False

def integration(recipe, src, directory, inputs_receipt=None):
    """(problems, facts): the newest reconcile receipt in the operator's integration dir must be a blazer-labelled
    receipt from this checkout's trusted patcher whose recorded entries still match the tree, and (FAM-BLAZER-
    INTEGRATE-005) bind to the admitted inputs: build label, recipe, installed APK bytes, Launcher3, the routed
    Updater and the policy pins (blazer_integration.compile_problems)."""
    spec = recipe['integration']
    if directory is None:
        return ['compile needs --integration-dir: the Rose reconcile receipts for this tree'], {}
    if not directory.is_dir() or directory.is_symlink():
        return [f'integration dir {directory} is not an existing, non-link directory'], {}
    receipts = sorted(directory.glob('[0-9][0-9][0-9][0-9]-*.json'))
    if not receipts:
        return ['no Rose integration (reconcile) receipt for this tree'], {}
    latest = receipts[-1]
    facts = {'receipt': latest.name}
    if latest.is_symlink() or latest.resolve().parent != directory.resolve():
        return [f'{latest.name} is a link out of {directory}, not a receipt of this builder'], facts
    facts['sha256'] = sha256_file(latest)
    record = json.loads(latest.read_text())
    problems = []
    if record.get('receiptType') != spec['receiptType']:
        problems.append(f'{latest.name} is a {record.get("receiptType")!r} receipt, not a completed reconcile')
    if not str(record.get('label', '')).startswith(spec['labelPrefix']):
        problems.append(f'{latest.name} is labelled {record.get("label")!r}: foreign provenance, not a {DEVICE} integration')
    ours = {name: sha256_file(ROOT/'scripts'/name) for name in spec['patcher']}
    patcher = record.get('patcher') or {}
    if patcher.get('patcher') != 'trusted' or patcher.get('sha256') != ours:
        problems.append('the reconcile patcher is not this checkout\'s trusted patcher (foreign recipe)')
    entries = record.get('entries') or {}
    if not entries:
        problems.append(f'{latest.name} records no installed entries')
    drifted = []
    for relative, entry in sorted(entries.items()):
        path = src/relative
        have = sha256_file(path) if path.is_file() and not path.is_symlink() else None
        if have != entry.get('after'):
            drifted.append(relative)
    if drifted:
        problems.append(f'the source tree no longer matches {latest.name}: {drifted[:5]}')
    problems += blazer_integration.compile_problems(record, src, inputs_receipt)
    return problems, facts

def isolation_problem(path, production):
    """Why PATH is not isolated from the production source root, or None. Equal, inside, containing or the same inode
    as the production tree all refuse; the production tree is never a preparation workspace."""
    production = Path(production)
    if path == production or production in path.parents or path in production.parents:
        return f'{path} is, contains or sits inside the production source root {production}'
    try:
        if path.exists() and production.exists() and os.path.samefile(path, production):
            return f'{path} is the production source root {production}'
    except OSError:
        pass
    return None

def space(stage, recipe, where, statvfs, facts, problems):
    """available - bound >= reserve on the filesystem the stage writes."""
    bound = recipe['preparation'][stage]['boundBytes'] if stage in PREPARATION else recipe['capacity']['compileAllowanceBytes']
    fs = statvfs(where)
    available = fs.f_bavail * fs.f_frsize
    reserve = recipe['capacity']['reserveBytes']
    facts['capacity'] = {'availableBytes': available, 'reserveBytes': reserve, 'boundBytes': bound,
        'boundKind': recipe['preparation']['boundsKind'] if stage in PREPARATION else 'measured-allowance'}
    if bound is None:
        problems.append(f'capacity.{stage}AllowanceBytes is unmeasured; compile needs a measured allowance')
    elif available - bound < reserve:
        problems.append(f'available {available} minus bound {bound} is below the reserve {reserve}')

def tree_problems(recipe, src):
    problems = []
    for needed in TREE_NEEDS:
        if not (src/needed).exists():
            problems.append(f'{needed} is missing from the source tree')
    deps = src/recipe['adevtool']['depsDir']
    revision = (deps/'adevtool_revision').read_text().strip() if (deps/'adevtool_revision').is_file() else None
    if revision != recipe['pins']['vendor/adevtool']:
        problems.append(f'{recipe["adevtool"]["depsDir"]}/adevtool_revision is {revision!r}, not the pinned revision')
    tools = deps/'host/linux-x86/bin'
    missing = [t for t in recipe['adevtool']['depsTools']
        if not (tools/t).is_file() or (tools/t).is_symlink() or not os.access(tools/t, os.X_OK)]
    if missing:
        problems.append(f'adevtool host tools missing or not executable: {missing}')
    if not (src/recipe['kernelDir']).is_dir():
        problems.append(f'{recipe["kernelDir"]} (pinned laguna kernel) is missing from the source tree')
    return problems

def vendor_tree_problems(recipe, src):
    vendor = src/'vendor/google_devices'/DEVICE
    check = vendor/'adevtool-version-check.mk'
    problems = []
    if vendor.is_symlink() or not (vendor/f'{DEVICE}.mk').is_file():
        problems.append(f'{vendor}/{DEVICE}.mk is missing: run the vendor stage first')
    if not check.is_file() or recipe['pins']['vendor/adevtool'] not in check.read_text():
        problems.append('the generated vendor tree is not from the pinned adevtool revision (foreign provenance)')
    return problems

def preflight(recipe, stage, op, statvfs=os.statvfs, lock_probe=lock_free, root=ROOT):
    """{'ready': bool, 'problems': [...], 'facts': {...}} for one stage. Reads only; writes and runs nothing."""
    if stage not in STAGES:
        raise InvalidInput(f'stage must be one of {STAGES}')
    prep = recipe['preparation']
    production = pointer(root, prep['productionSourceRootFrom'])
    src, download = op['sourceRoot'], op['downloadDir']
    problems, facts = [], {'stage': stage, 'device': DEVICE, 'host': op['host'], 'signed': False,
        'preparation': stage in PREPARATION, 'productionSourceRoot': production}
    for name in ('sourceRoot', 'lock', 'downloadDir', 'integrationDir', 'inputsReceipt', 'node'):
        if op[name] is not None and symlink_in(op[name]):
            problems.append(f'{name} passes through a symlink ({symlink_in(op[name])})')
    needs = {'download': ('downloadDir',), 'vendor': ('downloadDir', 'sourceRoot', 'node'), 'lunch': ('sourceRoot',),
             'compile': ('sourceRoot',)}[stage]
    absent = [n for n in needs if op[n] is None]
    if stage == 'compile' and op['jobs'] is None:
        absent.append('jobs')
    if absent:
        problems.append(f'{stage} needs {", ".join("--" + re.sub(r"([A-Z])", lambda m: "-" + m[1].lower(), n) for n in absent)}')
        return {'ready': False, 'problems': problems, 'facts': facts}
    for name in ('sourceRoot', 'downloadDir'):
        if stage in PREPARATION and op[name] is not None:
            reason = isolation_problem(op[name], production)
            if reason:
                problems.append(f'{name}: {reason}')
    factory = recipe['factory']
    if stage == 'download':
        final, part = download/factory['file'], download/(factory['file'] + prep['download']['partSuffix'])
        if download.exists() and not download.is_dir():
            problems.append(f'download dir {download} is not a directory')
        if not (download if download.exists() else download.parent).is_dir():
            problems.append(f'download dir parent {download.parent} does not exist')
        for path in (final, part):
            if os.path.lexists(path):
                problems.append(f'{path} already exists; an existing archive or partial download is never replaced')
        where = download if download.exists() else download.parent
    else:
        if not src.is_dir():
            problems.append(f'source root {src} is not an existing directory')
            return {'ready': False, 'problems': problems, 'facts': facts}
        where = src
    if stage == 'vendor':
        problems += tree_problems(recipe, src)
        if download == src or src in download.parents:
            problems.append('the download dir must be outside the source tree')
        archive = download/factory['file']
        if archive.is_symlink() or not archive.is_file():
            problems.append(f'{archive} is missing: run the download stage first')
        else:
            facts['factorySha256'] = sha256_file(archive)
            if facts['factorySha256'] != factory['sha256']:
                problems.append(f'{archive} sha256 {facts["factorySha256"]} is not the pinned {factory["sha256"]}')
        for rel in prep['vendor']['collisionPaths']:
            if os.path.lexists(src/rel):
                problems.append(f'{rel} already exists; generate-all would replace it (nothing is deleted or reused)')
        node = op['node']
        if node.is_symlink() or not node.is_file() or not os.access(node, os.X_OK):
            problems.append(f'node {node} is not an executable regular file')
        elif node.name != 'node':
            problems.append(f'node {node} must be named node so that PATH resolution reaches exactly that file')
        else:
            # The child PATH starts with node's own directory, so it resolves there; run() re-checks identity and PATH.
            facts['node'] = file_identity(node)
    elif stage == 'lunch':
        if not (src/'build/envsetup.sh').is_file():
            problems.append('build/envsetup.sh is missing from the source tree')
        problems += vendor_tree_problems(recipe, src)
        if os.path.lexists(src/prep['lunch']['outDir']):
            problems.append(f'{prep["lunch"]["outDir"]} already exists; refusing to reuse or overwrite an OUT_DIR')
    elif stage == 'compile':
        problems += tree_problems(recipe, src) + vendor_tree_problems(recipe, src)
        integration_problems, facts['roseIntegration'] = integration(recipe, src, op['integrationDir'], op['inputsReceipt'])
        problems += integration_problems
        if os.path.lexists(src/recipe['compile']['outDir']):
            problems.append(f'{recipe["compile"]["outDir"]} already exists; refusing to reuse or overwrite an OUT_DIR')
    if not lock_probe(op['lock']):
        problems.append('the builder lock is held by another job')
    space(stage, recipe, where, statvfs, facts, problems)
    return {'ready': not problems, 'problems': problems, 'facts': facts}

def stamp(now):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now()))

def tree_bytes(path):
    """Allocated bytes under PATH without following links (0 when absent)."""
    if not os.path.lexists(path):
        return 0
    total = 0
    for base, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            try:
                total += os.lstat(os.path.join(base, name)).st_blocks * 512
            except OSError:
                pass
    return total + os.lstat(path).st_blocks * 512

def file_identity(path):
    """Metadata identity of a regular file (no content read)."""
    st = os.stat(path, follow_symlinks=False)
    return {'path': str(path), 'device': st.st_dev, 'inode': st.st_ino, 'size': st.st_size, 'mode': oct(st.st_mode),
            'mtimeNs': st.st_mtime_ns}

def node_binding_problem(node, path_env):
    """adevtool reaches node through PATH; the child PATH must resolve `node` to exactly the verified file."""
    resolved = shutil.which('node', path=path_env)
    try:
        if resolved is None or not os.path.samefile(resolved, node):
            return f'PATH {path_env!r} resolves node to {resolved!r}, not the verified {node}'
    except OSError:
        return f'PATH {path_env!r} resolves node to {resolved!r}, which cannot be compared with {node}'
    return None

def group_alive(pgid):
    """True while a non-zombie process is in group PGID. Linux /proc is read so that exited members awaiting reaping by
    init are not counted; without /proc the signal-0 probe is used (zombies then count as alive)."""
    proc = Path('/proc')
    if proc.is_dir():
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry/'stat').read_text().rsplit(')', 1)[1].split()
            except (OSError, IndexError):
                continue
            if fields[0] != 'Z' and int(fields[2]) == pgid:
                return True
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

def end_group(pgid, grace, clock, sleep):
    """SIGTERM the stage's process group, then SIGKILL whatever is left after the grace period. Returns what was done.
    Descendants that started their own session are outside the group and are not reached (needs cgroup containment)."""
    if not group_alive(pgid):
        return None
    os.killpg(pgid, signal.SIGTERM)
    deadline = clock() + grace
    while clock() < deadline and group_alive(pgid):
        sleep(0.05)
    if group_alive(pgid):
        os.killpg(pgid, signal.SIGKILL)
        return 'SIGKILL'
    return 'SIGTERM'

def supervise(argv, cwd, env, timeout, limits, watch=(), statvfs=os.statvfs, measure=None, capture=False,
              clock=time.monotonic, sleep=time.sleep):
    """Run ARGV in its own session and process group. Every limits['sampleSeconds'] the watched paths' bytes are compared
    with limits['boundBytes'] and the filesystem's available bytes with limits['reserveBytes']; an overrun or the timeout
    stops the whole group (SIGTERM, then SIGKILL after killGraceSeconds), and members left after a normal exit are ended
    the same way. This is a sampled stop, not a filesystem quota: writes between samples can overshoot the bound."""
    measure = measure or tree_bytes
    out = tempfile.TemporaryFile() if capture else subprocess.DEVNULL
    started = clock()
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, start_new_session=True)
    pgid, stopped, peak, low = proc.pid, None, 0, None
    while True:
        try:
            proc.wait(timeout=limits['sampleSeconds'])
            break
        except subprocess.TimeoutExpired:
            pass
        if clock() - started > timeout:
            stopped = f'exceeded {timeout} s'
        else:
            used = sum(measure(Path(path)) for path in watch)
            peak = max(peak, used)
            fs = statvfs(cwd)
            available = fs.f_bavail * fs.f_frsize
            low = available if low is None else min(low, available)
            if used > limits['boundBytes']:
                stopped = f'watched paths reached {used} bytes, above the {limits["boundBytes"]}-byte bound'
            elif available < limits['reserveBytes']:
                stopped = f'available {available} fell below the reserve {limits["reserveBytes"]}'
        if stopped:
            end_group(pgid, limits['killGraceSeconds'], clock, sleep)
            proc.wait()
            break
    leftover = end_group(pgid, limits['killGraceSeconds'], clock, sleep)
    used = sum(measure(Path(path)) for path in watch)
    result = {'returncode': proc.returncode, 'stopped': stopped, 'leftoverGroupEnded': leftover,
              'consumedBytes': used, 'peakSampledBytes': max(peak, used), 'lowestSampledAvailableBytes': low,
              'enforcement': 'sampled-stop', 'sampleSeconds': limits['sampleSeconds']}
    if capture:
        out.seek(0)
        result['stdout'] = out.read().decode(errors='replace')
        out.close()
    return result

INSPECTION = 'roseos.blazer-preparation-inspection.v1'
INSPECT_MAX_SECONDS = 1200

def meta(path):
    """lstat metadata of one path; never follows a link and never reads content."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return {'path': str(path), 'exists': False}
    except OSError as error:
        return {'path': str(path), 'exists': None, 'error': type(error).__name__}
    kind = 'link' if os.path.islink(path) else 'dir' if os.path.isdir(path) else 'file' if os.path.isfile(path) else 'other'
    return {'path': str(path), 'exists': True, 'type': kind, 'mode': oct(st.st_mode), 'uid': st.st_uid, 'device': st.st_dev,
            'inode': st.st_ino, 'size': st.st_size}

def bounded_bytes(path, budget, clock):
    """Allocated bytes under PATH (no links followed) within the shared entry and time budget."""
    if not os.path.lexists(path):
        return {'exists': False, 'bytes': 0, 'entries': 0, 'truncated': False}
    total, entries = os.lstat(path).st_blocks * 512, 0
    for base, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            if budget['entries'] <= 0 or clock() > budget['deadline']:
                return {'exists': True, 'bytes': total, 'entries': entries, 'truncated': True}
            budget['entries'] -= 1
            entries += 1
            try:
                total += os.lstat(os.path.join(base, name)).st_blocks * 512
            except OSError:
                pass
    return {'exists': True, 'bytes': total, 'entries': entries, 'truncated': False}

def inspect(recipe, op, root=ROOT, statvfs=os.statvfs, max_entries=200000, max_seconds=900, clock=time.monotonic):
    """Metadata-only observation for a later, separately approved run: path identities, isolation, filesystem space,
    pinned project presence, bounded sizes of collision and download paths, and the --node file identity and its PATH
    resolution. Reads lstat/statvfs and at most 128 bytes of out_adevtool_deps/adevtool_revision; starts no process,
    opens no network connection and writes nothing."""
    if not 0 < max_seconds <= INSPECT_MAX_SECONDS or max_entries < 1:
        raise InvalidInput(f'--max-seconds must be 1..{INSPECT_MAX_SECONDS} and --max-entries positive')
    started = clock()
    budget = {'entries': max_entries, 'deadline': started + max_seconds}
    prep, factory = recipe['preparation'], recipe['factory']
    production = pointer(root, prep['productionSourceRootFrom'])
    report = {'schema': INSPECTION, 'device': DEVICE, 'host': op['host'], 'metadataOnly': True, 'processesStarted': 0,
              'network': False, 'writes': False, 'productionSourceRoot': production, 'paths': {}, 'isolation': {},
              'filesystems': {}, 'limits': {'maxEntries': max_entries, 'maxSeconds': max_seconds}}
    for name in ('sourceRoot', 'downloadDir', 'lock', 'node'):
        if op[name] is None:
            continue
        report['paths'][name] = dict(meta(op[name]), symlinkComponent=str(symlink_in(op[name]) or '') or None)
        if name in ('sourceRoot', 'downloadDir'):
            report['isolation'][name] = isolation_problem(op[name], production)
            probe = op[name] if op[name].exists() else op[name].parent
            if probe.is_dir():
                fs = statvfs(probe)
                report['filesystems'][name] = {'queried': str(probe), 'availableBytes': fs.f_bavail * fs.f_frsize,
                    'totalBytes': fs.f_blocks * fs.f_frsize, 'device': os.stat(probe).st_dev}
    src = op['sourceRoot']
    if src is not None and src.is_dir() and not symlink_in(src):
        report['projects'] = {rel: meta(src/rel) for rel in [*recipe['pins'], recipe['kernelDir'], *TREE_NEEDS, '.repo/manifest.xml']}
        revision = src/recipe['adevtool']['depsDir']/'adevtool_revision'
        if revision.is_file() and not revision.is_symlink():
            with open(revision, 'rb') as f:
                report['adevtoolRevision'] = f.read(128).decode(errors='replace').strip()
        report['adevtoolRevisionPinned'] = recipe['pins']['vendor/adevtool']
        report['collisions'] = {rel: bounded_bytes(src/rel, budget, clock) for rel in prep['vendor']['collisionPaths']}
        report['collisions'][prep['lunch']['outDir']] = bounded_bytes(src/prep['lunch']['outDir'], budget, clock)
    if op['downloadDir'] is not None:
        report['download'] = {'archive': meta(op['downloadDir']/factory['file']),
            'part': meta(op['downloadDir']/(factory['file'] + prep['download']['partSuffix'])),
            'expectedSha256': factory['sha256'], 'hashed': False}
    if op['node'] is not None:
        node_path = f'{op["node"].parent}:{SAFE_PATH}'
        resolved = shutil.which('node', path=node_path)
        report['node'] = {'given': str(op['node']), 'realpath': os.path.realpath(op['node']),
            'resolvedThroughStagePath': resolved, 'nameIsNode': op['node'].name == 'node',
            'sameFile': bool(resolved) and os.path.exists(op['node']) and os.path.samefile(resolved, op['node']),
            'version': 'not executed (metadata only)'}
    report['elapsedSeconds'] = round(clock() - started, 3)
    report['truncated'] = budget['entries'] <= 0 or clock() > budget['deadline']
    return report

def fetch(url, timeout):
    return urllib.request.urlopen(url, timeout=timeout)  # noqa: S310 - the pinned https URL only

def download(recipe, command, fetcher, clock):
    """Stream the pinned URL to the .part file within the byte and time bounds; rename only on a sha256 match. A failed
    attempt removes only the .part this call created; an existing archive or .part is never touched."""
    part, final = Path(command['part']), Path(command['final'])
    digest, size, started, created = hashlib.sha256(), 0, clock(), False
    outcome = {'url': command['url'], 'expectedSha256': command['sha256'], 'maxBytes': command['maxBytes']}
    try:
        part.parent.mkdir(exist_ok=True)  # only the leaf download dir; preflight required its parent
        with open(part, 'xb') as out:
            created = True
            with fetcher(command['url'], command['timeoutSeconds']) as response:
                for block in iter(lambda: response.read(CHUNK), b''):
                    size += len(block)
                    if size > command['maxBytes']:
                        raise OverflowError(f'download exceeded the {command["maxBytes"]}-byte bound')
                    if clock() - started > command['timeoutSeconds']:
                        raise TimeoutError(f'download exceeded {command["timeoutSeconds"]} s')
                    digest.update(block)
                    out.write(block)
        outcome.update(bytes=size, sha256=digest.hexdigest())
        if outcome['sha256'] != command['sha256']:
            raise ValueError(f'sha256 {outcome["sha256"]} is not the pinned {command["sha256"]}')
        if os.path.lexists(final):
            raise FileExistsError(f'{final} appeared during the download')
        os.rename(part, final)
        return True, outcome
    except (OSError, OverflowError, TimeoutError, ValueError) as error:
        outcome['error'] = f'{type(error).__name__}: {error}'
        if created:
            part.unlink(missing_ok=True)
        return False, outcome

def node_major(text):
    match = re.fullmatch(r'v?(\d+)\.\d+\.\d+', (text or '').strip())
    return int(match[1]) if match else None

def run(recipe, stage, op, receipt_dir, now=time.time, fetcher=fetch, clock=time.monotonic, measure=tree_bytes,
        sleep=time.sleep, statvfs=os.statvfs, **probes):
    """Preflight, then the stage under the operator's lock (LOCK_NB), with write-once receipts."""
    receipt_dir = Path(receipt_dir)
    if os.path.lexists(receipt_dir) or symlink_in(receipt_dir):
        raise InvalidInput(f'{receipt_dir} already exists or passes through a symlink; use a new receipt directory')
    report = preflight(recipe, stage, op, statvfs=statvfs, **probes)
    receipt_dir.mkdir(parents=True)
    release_receipts.write_once(receipt_dir/'preflight.json', {**report, 'at': stamp(now)})
    if not report['ready']:
        return 1
    command = commands(recipe, op)[stage]
    base = {'stage': stage, 'device': DEVICE, 'signed': False, 'releaseReady': False}
    with open(op['lock'], 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            release_receipts.write_once(receipt_dir/'completed.json', {**base, 'started': False, 'error': 'lock taken after preflight'})
            return 1
        release_receipts.write_once(receipt_dir/'started.json', {**base, 'host': op['host'], 'command': command, 'at': stamp(now)})
        if stage == 'download':
            ok, outcome = download(recipe, command, fetcher, clock)
            release_receipts.write_once(receipt_dir/'completed.json', {**base, 'ok': ok, **outcome, 'at': stamp(now)})
            return 0 if ok else 1
        env = {**{k: v for k, v in os.environ.items() if k not in ('PATH',)}, **command['env']}
        prep, capacity = recipe['preparation'], recipe['capacity']
        limits = {**prep['limits'], 'boundBytes': prep[stage]['boundBytes'], 'reserveBytes': capacity['reserveBytes']}
        if stage == 'vendor':
            if file_identity(op['node']) != report['facts'].get('node') or node_binding_problem(op['node'], env['PATH']):
                release_receipts.write_once(receipt_dir/'completed.json', {**base, 'ok': False,
                    'error': 'node changed or no longer resolves through PATH after preflight', 'at': stamp(now)})
                return 1
            node = supervise(command['nodeVersion'], command['cwd'], env, prep['vendor']['node']['versionTimeoutSeconds'],
                             limits, capture=True, statvfs=statvfs, measure=measure, clock=clock, sleep=sleep)
            major = node_major(node.get('stdout'))
            floor = prep['vendor']['node']['minMajor']
            if node['returncode'] or node['stopped'] or major is None or major < floor:
                release_receipts.write_once(receipt_dir/'completed.json', {**base, 'ok': False, 'node': report['facts']['node'],
                    'nodeVersion': (node.get('stdout') or '').strip(), 'error': f'node must be >= {floor}', 'at': stamp(now)})
                return 1
        paths = prep['vendor']['collisionPaths'] if stage == 'vendor' else [prep['lunch']['outDir']]
        watch = [str(op['sourceRoot']/rel) for rel in paths]
        outcome = supervise(command['argv'], command['cwd'], env, command['timeoutSeconds'], limits, watch=watch,
                            capture=stage == 'lunch', statvfs=statvfs, measure=measure, clock=clock, sleep=sleep)
        stdout = outcome.pop('stdout', '')
        outcome.update(boundBytes=limits['boundBytes'], boundKind=prep['boundsKind'])
        ok = outcome['returncode'] == 0 and not outcome['stopped']
        if outcome['stopped']:
            outcome['error'] = f'stopped: {outcome["stopped"]} (outputs are kept, not deleted)'
        elif outcome['consumedBytes'] > limits['boundBytes']:
            ok, outcome['error'] = False, (f'consumed {outcome["consumedBytes"]} bytes, above the {limits["boundBytes"]}-byte '
                                           'bound between samples (outputs are kept, not deleted)')
        if stage == 'vendor':
            outcome['node'] = report['facts']['node']
        if stage == 'lunch' and outcome['returncode'] == 0 and not outcome['stopped']:
            seen = dict(line.split('=', 1) for line in stdout.splitlines() if '=' in line)
            outcome['targets'] = {k: seen.get(k) for k in LUNCH_VARS}
            if outcome['targets'] != prep['lunch']['expect']:
                ok, outcome['error'] = False, f'lunch targets {outcome["targets"]} are not {prep["lunch"]["expect"]}'
        release_receipts.write_once(receipt_dir/'completed.json', {**base, 'ok': ok, **outcome, 'at': stamp(now)})
    return 0 if ok else 1

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=('plan', 'preflight', 'run', 'inspect'))
    parser.add_argument('--stage', choices=STAGES)
    parser.add_argument('--max-entries', type=int, default=200000)
    parser.add_argument('--max-seconds', type=int, default=900)
    parser.add_argument('--recipe', default=str(RECIPE))
    parser.add_argument('--host', required=True)
    parser.add_argument('--lock', required=True)
    parser.add_argument('--source-root')
    parser.add_argument('--jobs', type=int)
    parser.add_argument('--download-dir')
    parser.add_argument('--node')
    parser.add_argument('--integration-dir')
    parser.add_argument('--inputs-receipt')
    parser.add_argument('--receipt-dir')
    a = parser.parse_args(argv)
    try:
        recipe = load(a.recipe)
        op = operator(a.host, a.source_root, a.jobs, a.lock, a.download_dir, a.integration_dir,
            recipe['compile']['maxJobs'], a.inputs_receipt, a.node)
        if a.action == 'inspect':
            print(json.dumps(inspect(recipe, op, max_entries=a.max_entries, max_seconds=a.max_seconds), indent=2, default=str))
            return 0
        if a.stage is None:
            raise InvalidInput(f'{a.action} needs --stage')
        if a.action == 'run':
            if not a.receipt_dir:
                raise InvalidInput('run needs --receipt-dir')
            return run(recipe, a.stage, op, a.receipt_dir)
        report = preflight(recipe, a.stage, op)
        if a.action == 'plan':
            report = {**report, 'command': commands(recipe, op).get(a.stage)}
        print(json.dumps(report, indent=2, default=str))
        return 0 if report['ready'] else 1
    except InvalidInput as error:
        print(f'invalid input: {error}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())
