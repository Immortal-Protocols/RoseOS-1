#!/usr/bin/env python3
"""FAM-BLAZER-BUILD-004 / BLAZER-PREP: blazer (Pixel 10 Pro) preparation and unsigned build-only stages.

The recipe (os/build-only/blazer.json) binds only checked-in pinned sources: lunch blazer-cur-user, the indexed factory
image name, URL and sha256, the vendor/adevtool, vendor/state and laguna kernel pins of os/pinned-manifest.xml, adevtool
generation with an explicit -d blazer, and the proposal bounds of each preparation stage. It binds no host.

  blazer-build-only.py plan      --stage S ...   the rendered command and the preflight report; runs nothing
  blazer-build-only.py preflight --stage S ...   the report alone (exit 0 ready, 1 not ready)
  blazer-build-only.py run       --stage S ... --receipt-dir NEW   preflight, then the stage under the lock

Preparation stages (never compile, sign, publish or flash; never in the production source tree):
  source    --host --lock --source-root ISOLATED
            an ordinary in-process byte copy of the production source root (only read) into ISOLATED, which must not
            exist (one missing parent under the ancestor boundary is created owner-only): the emulator alpha's exclusion
            policy (repo metadata, keys, releases, outputs, .git, the adevtool unpack cache) plus exactly
            out_adevtool_deps/adevtool_revision and the complete out_adevtool_deps/host/linux-x86 (nothing else of the
            deps is walked), and only when those deps hold the pinned revision and the tools (never the vendor stage's
            other tree or kernel needs); the full selection is measured before anything is written and refused above
            its bound, the
            copy is staged beside ISOLATED and renamed into place only when complete, and a stop (timeout, SIGTERM,
            reserve) removes only this run's staging directory within cleanupSeconds (a remainder is left, named and
            blocks the next run); no subprocess, reflink, hardlink or mount. The result is a
            byte snapshot of the production working tree, Frankel integration included: its project revisions are
            not verified (see SOURCE_PROVENANCE). The per-entry selection record (path, type, mode, size and file
            sha256 or link text) is kept write-once beside the tree as ISOLATED.selection.tsv; its sha256 is
            selectionSha256, so later stages' changes to the tree never erase the original-byte evidence
  download  --host --lock --download-dir
            stream the pinned official URL to DIR/<file>.part (byte and time bound), rename to DIR/<file> only when its
            sha256 equals the pin; an existing archive is never replaced; a failed attempt removes only its own .part
  vendor    --host --lock --download-dir --source-root ISOLATED --node PATH
            `node --version` must meet the floor and `git -C vendor/adevtool rev-parse HEAD` must print exactly the
            pin (ISOLATED needs vendor/adevtool/.git; GIT_* stripped, GIT_CEILING_DIRECTORIES=ISOLATED), then
            `vendor/adevtool/bin/run generate-all -d blazer` with ADEVTOOL_SKIP_DEP_BUILD=1 (adevtool never rebuilds its
            deps) and ADEVTOOL_IMG_DOWNLOAD_DIR=DIR in ISOLATED, which must not be, contain or sit inside the production source root
            (os/release-pipeline/frankel.json host.sourceRoot); the archive's sha256, the adevtool deps revision marker, the
            host tools and the kernel directory must be present (project revisions are not checked) and every collision
            path absent; consumed bytes are measured afterwards against the bound
  lunch     --host --lock --source-root ISOLATED
            `lunch blazer-cur-user` and the TARGET_PRODUCT/RELEASE/BUILD_VARIANT/DEVICE checks with OUT_DIR=out-blazer-lunch;
            no make goal
Build stage (outside preparation, unchanged): compile --host --jobs --lock --source-root --integration-dir --inputs-receipt.

Every stage refuses on a symlink in an operator path, a held lock (probed non-blocking, held with LOCK_NB while
running), an existing output (nothing is deleted, reused or overwritten except the stage's own .part) or available
bytes minus the stage bound below capacity.reserveBytes. Bounds are labelled proposals, not measurements; the stage
timeouts total 6.5 h.

vendor and lunch run in their own session and process group with PATH limited to /usr/bin:/bin (vendor: first a private
directory in the receipt directory holding only a `node` link to the verified file, so adevtool resolves exactly that
file and no other tool beside it, e.g. the rest of /usr/local/bin; its identity is re-checked just before the run). The
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
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_receipts  # noqa: E402
import blazer_integration  # noqa: E402
import alpha_source  # noqa: E402  (the reviewed copy plan and per-file copy; only imported, never changed)

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT/'os/build-only/blazer.json'
DEVICE = 'blazer'
PREPARATION = ('source', 'download', 'vendor', 'lunch')
STAGES = PREPARATION + ('compile',)
BUILD_INDEX = 'os/tests/fixtures/adevtool-17df3b79/adevtool/config/build-index/build-index-main.yml.excerpt.json'
FACTORY_URL = 'https://dl.google.com/dl/android/aosp/'
TREE_NEEDS = ('vendor/adevtool/bin/run', 'build/envsetup.sh', 'prebuilts/build-tools/path/linux-x86',
    'prebuilts/jdk/jdk21/linux-x86/bin')
HOST_LABEL = re.compile(r'[a-z0-9][a-z0-9.-]{0,62}')
CHUNK = 1 << 20
LUNCH_VARS = ('TARGET_PRODUCT', 'TARGET_RELEASE', 'TARGET_BUILD_VARIANT', 'TARGET_DEVICE')
SAFE_PATH = '/usr/bin:/bin'
NODE_PATH_DIR = 'node-path'  # inside the vendor stage's own receipt directory

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
    source = prep.get('source') or {}
    if not (positive(source.get('cleanupSeconds')) and source['cleanupSeconds'] < 300):
        problems.append('preparation.source.cleanupSeconds must be positive and below the 300 s outer-timeout margin')
    if source.get('includePaths') != include_paths(recipe) or source.get('stagingPrefix') != '.source-staging-':
        problems.append(f'preparation.source must include exactly {include_paths(recipe)} and stage under .source-staging-')
    try:
        policy = pointer(root, source['exclusionsFrom'])
        if not ({'.repo', 'keys', 'releases', 'out'} <= set(policy.get('rootNames', ())) and {'out-', 'out_'} <= set(
                policy.get('rootPrefixes', ())) and {'.git', '.repo'} <= set(policy.get('anywhereNames', ()))):
            problems.append('preparation.source.exclusionsFrom must exclude repo metadata, keys, releases, outputs and .git')
    except (KeyError, OSError, ValueError, TypeError, AttributeError):
        problems.append('preparation.source.exclusionsFrom must resolve to the checked-in alpha exclusion policy')
    git = vendor.get('adevtoolGit') or {}
    if (git.get('metadataPath') != 'vendor/adevtool/.git' or git.get('argv') != ['git', '-C', 'vendor/adevtool', 'rev-parse', 'HEAD']
            or git.get('skipDepBuildEnv') != {'ADEVTOOL_SKIP_DEP_BUILD': '1'} or not positive(git.get('timeoutSeconds'))):
        problems.append('preparation.vendor.adevtoolGit must be vendor/adevtool/.git, `git -C vendor/adevtool rev-parse HEAD`, '
                        'ADEVTOOL_SKIP_DEP_BUILD=1 and a positive timeoutSeconds')
    node = vendor.get('node') or {}
    if not (positive(node.get('minMajor')) and positive(node.get('versionTimeoutSeconds'))):
        problems.append('preparation.vendor.node needs a positive minMajor and versionTimeoutSeconds')
    layout = prep.get('candidateLayout') or {}
    if layout.get('children') != {'sourceRoot': 'source', 'downloadDir': 'downloads', 'receiptsDir': 'receipts'}:
        problems.append('preparation.candidateLayout.children must be sourceRoot=source, downloadDir=downloads, receiptsDir=receipts')
    if not isinstance(production, str) or layout.get('ancestorBoundary') != str(Path(production).parent):
        problems.append('preparation.candidateLayout.ancestorBoundary must be the parent of the production source root')
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

def commands(recipe, op, root=ROOT):
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
        spec = prep['source']
        rendered['source'] = {'base': pointer(root, prep['productionSourceRootFrom']), 'target': src,
            'staging': str(op['sourceRoot'].parent/(spec['stagingPrefix'] + '<nonce>')),
            'exclusionsFrom': spec['exclusionsFrom'], 'includePaths': list(spec['includePaths']),
            'maxBytes': spec['boundBytes'], 'timeoutSeconds': spec['timeoutSeconds'], 'processes': 0, 'method': spec['method']}
        if op['downloadDir'] is not None:
            git = prep['vendor']['adevtoolGit']
            rendered['vendor'] = {'cwd': src, 'env': {recipe['generate']['downloadEnv']: str(op['downloadDir']),
                    'PATH': f'<receipt-dir>/{NODE_PATH_DIR}:{SAFE_PATH}' if op['node'] else SAFE_PATH,
                    'GIT_CEILING_DIRECTORIES': src, **git['skipDepBuildEnv']},
                'argv': list(recipe['generate']['argv']), 'timeoutSeconds': prep['vendor']['timeoutSeconds'],
                'nodeVersion': [str(op['node']) if op['node'] else 'node', '--version'],
                'adevtoolHead': list(git['argv']), 'adevtoolHeadTimeoutSeconds': git['timeoutSeconds']}
        checks = ' && '.join(f'echo {v}=${v}' for v in LUNCH_VARS[:3]) + ' && echo TARGET_DEVICE=$(get_build_var TARGET_DEVICE)'
        rendered['lunch'] = {'cwd': src, 'env': {'OUT_DIR': prep['lunch']['outDir'], 'PATH': SAFE_PATH,
                                                 'GIT_CEILING_DIRECTORIES': src},
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
    problems += deps_problems(recipe, src)  # the same exact, no-follow marker and tool checks as the source stage
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

def source_preflight(recipe, src, production, problems, facts):
    """Admission of the source stage: the target is new and under the ancestor boundary, its parent exists or is the one
    level the stage may create, no interrupted staging is left beside it and the production root is a real directory.
    Returns where the stage writes (its nearest existing ancestor) for the space check."""
    prep = recipe['preparation']
    boundary, parent = Path(prep['candidateLayout']['ancestorBoundary']), src.parent
    if os.path.lexists(src):
        problems.append(f'{src} already exists; an isolated source tree is never replaced or reused')
    if os.path.lexists(record_path(src)):
        problems.append(f'{record_path(src)} already exists; a selection record is never replaced')
    if boundary not in src.parents:
        problems.append(f'{src} must sit under the ancestor boundary {boundary}')
    facts['createsParent'] = not os.path.lexists(parent)
    if not facts['createsParent']:
        if parent.is_symlink() or not parent.is_dir():
            problems.append(f'{parent} is not a directory')
    elif not (parent.parent.is_dir() and boundary in parent.parents):
        problems.append(f'{parent} does not exist and is not the one level the stage may create under {boundary}')
    leftovers = sorted(p.name for p in parent.glob(prep['source']['stagingPrefix'] + '*')) if parent.is_dir() else []
    if leftovers:
        problems.append(f'an interrupted construction left {", ".join(leftovers)} in {parent}; inspect and remove it deliberately')
    if symlink_in(production) or not production.is_dir():
        problems.append(f'the production source root {production} is not an existing, link-free directory')
    return nearest_existing(src)

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
    needs = {'source': ('sourceRoot',), 'download': ('downloadDir',), 'vendor': ('downloadDir', 'sourceRoot', 'node'),
             'lunch': ('sourceRoot',), 'compile': ('sourceRoot',)}[stage]
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
    elif stage == 'source':
        where = source_preflight(recipe, src, Path(production), problems, facts)
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
        git = src/prep['vendor']['adevtoolGit']['metadataPath']
        facts['adevtoolGit'] = meta(git)
        if not os.path.lexists(git):
            problems.append(f'{prep["vendor"]["adevtoolGit"]["metadataPath"]} is missing: no git version data for adevtool '
                            '(its version check needs `git -C vendor/adevtool rev-parse HEAD`; a source-stage snapshot '
                            'has no .git, so vendor cannot run on it)')
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

# What a source copy is and is not: completed.json carries this verbatim so no consumer can read the copy as verified.
SOURCE_PROVENANCE = {
    'kind': 'production-working-tree-byte-snapshot',
    'projectRevisionsVerified': False,
    'pinnedManifestVerified': False,
    'resolvedManifest': None,
    'integration': 'included as found in the production working tree (its Frankel/Rose integration is not removed, '
                   're-applied or verified)',
    'versionControlMetadata': 'excluded (.git and .repo): project revisions cannot be re-derived from the copy, and a '
                              'manifest or resolved manifest read elsewhere is not evidence for these bytes',
    'selectionSha256': 'identifies the copied paths, modes, sizes, link texts and file sha256s at copy time only; later '
                       'stages change the tree by design (vendor output, OUT_DIR) and do not re-hash it',
}

class StageStopped(Exception):
    """The source stage stopped after it started writing (timeout, SIGTERM, reserve or a base change)."""

class Refused(Exception):
    """The source stage refused before writing anything."""

def deps_problems(recipe, base):
    """Only what the selected deps themselves must provide, as their pinned consumer reads them (GrapheneOS adevtool
    17df3b79 src/config/paths.ts getHostBinPath, sha256 418823cc..., read at that revision): adevtool_revision exactly
    the pinned hash plus one newline (paths.ts compares the file with `git -C vendor/adevtool rev-parse HEAD` stdout;
    read no-follow, at most 128 bytes) and each depsTool, the 14 programs every getHostBinPath call site names, a
    regular, unlinked, executable file in host/linux-x86/bin. The vendor stage's other tree needs (adevtool's run
    script, envsetup, prebuilts) and the kernel directory are its own admission, never a gate on the copy."""
    deps, problems = base/recipe['adevtool']['depsDir'], []
    want = (recipe['pins']['vendor/adevtool'] + '\n').encode()
    try:
        fd = os.open(deps/'adevtool_revision', os.O_RDONLY | os.O_NOFOLLOW)
        try:
            have = os.read(fd, 128)
        finally:
            os.close(fd)
    except OSError:
        have = None
    if have != want:
        problems.append(f'{recipe["adevtool"]["depsDir"]}/adevtool_revision is {have!r}, not exactly the pinned hash and '
                        'one newline')
    tools = deps/'host/linux-x86/bin'
    missing = [t for t in recipe['adevtool']['depsTools']
               if (tools/t).is_symlink() or not (tools/t).is_file() or not os.access(tools/t, os.X_OK)]
    if missing:
        problems.append(f'adevtool host tools missing, linked or not executable: {missing}')
    return problems

def include_paths(recipe):
    """adevtool's deps as the vendor stage consumes them: the adevtool_revision marker and the complete host/linux-x86
    distribution (bin/ tools with their lib64/ libraries and framework/ JARs). Nothing else of the deps directory, which
    can be a whole build OUT_DIR (FIFOs, device-path links, links back into the source), is selected or walked."""
    deps = recipe['adevtool']['depsDir']
    return [f'{deps}/adevtool_revision', f'{deps}/host/linux-x86']

def source_selection(recipe, base, root, planner=alpha_source.plan_copy):
    """The full copy selection of BASE (a metadata walk with no entry cap, plus alpha's reads of build-definition files
    for cache references): the alpha exclusion policy, then each include path: a regular file taken as is, or a
    directory as its own selection with only .git/.repo excluded, so none of its links may leave it. Their parent
    directories must be real directories and are created with their own modes; nothing else beside them is walked.
    The selected deps must also provide what their consumer needs (deps_problems: the pinned revision and the tools);
    the vendor stage's other admission (tree needs, kernel) is not a copy gate."""
    spec = recipe['preparation']['source']
    policy = pointer(root, spec['exclusionsFrom'])
    plan = planner(base, policy)
    entries, problems = list(plan['entries']), list(plan['problems'])
    excluded = len(plan['excluded'])
    inner = {'rootNames': [], 'rootPrefixes': [], 'anywhereNames': list(policy['anywhereNames']), 'externalLinks': [],
             'cacheBoundaries': []}
    parents, included = set(), True
    for rel in spec['includePaths']:
        unsafe = None
        for parent in reversed(Path(rel).parents[:-1]):  # out_adevtool_deps, then out_adevtool_deps/host
            info = os.lstat(base/parent) if os.path.lexists(base/parent) else None
            if info is None or not stat.S_ISDIR(info.st_mode):
                unsafe = f'{parent} is not a real directory in the production source root'
                break
            if str(parent) not in parents:
                parents.add(str(parent))
                entries.append({'path': str(parent), 'type': 'dir', 'mode': stat.S_IMODE(info.st_mode)})
        info = None if unsafe or not os.path.lexists(base/rel) else os.lstat(base/rel)
        if unsafe:
            problems.append(unsafe)
            included = False
        elif info is not None and stat.S_ISREG(info.st_mode):
            entries.append({'path': rel, 'type': 'file', 'mode': stat.S_IMODE(info.st_mode), 'size': info.st_size,
                            'ino': info.st_ino, 'mtime_ns': info.st_mtime_ns})
        elif info is not None and stat.S_ISDIR(info.st_mode):
            part = planner(base/rel, inner)
            entries.append({'path': rel, 'type': 'dir', 'mode': stat.S_IMODE(info.st_mode)})
            for entry in part['entries']:
                entry = dict(entry, path=f'{rel}/{entry["path"]}')
                if entry['type'] == 'link':
                    entry['target'] = os.path.normpath(f'{rel}/{entry["target"]}')
                entries.append(entry)
            problems += [f'{rel}: {problem}' for problem in part['problems']]
            excluded += len(part['excluded'])
        else:
            problems.append(f'{rel} is not a regular file or real directory in the production source root')
            included = False
    if included:  # deps_problems reads adevtool_revision: only once it is known to be a regular file, never a link
        problems += [f'deps: {problem}' for problem in deps_problems(recipe, base)]
    return {'entries': entries, 'problems': problems, 'excluded': excluded}

def projected_bytes(entries, block):
    """Bytes the copy allocates at most block-rounded: every file is written densely (a sparse base file grows to its
    apparent size), and the root, each directory and each link take one block."""
    return block + sum(-(-e['size'] // block) * block if e['type'] == 'file' else block for e in entries)

RECORD_SUFFIX = '.selection.tsv'
RECORD_ESCAPES = {ord('\\'): b'\\\\', ord('\t'): b'\\t', ord('\n'): b'\\n', ord('\r'): b'\\r'}

def record_field(text):
    """A path or link text as its raw filesystem bytes (non-UTF-8 names included), with backslash, tab, CR and LF
    escaped so that every selection-record line is unambiguous. Nothing is refused for its name."""
    return b''.join(RECORD_ESCAPES.get(byte, bytes((byte,))) for byte in os.fsencode(text))

def record_line(entry, digest_or_text):
    """One selection-record line for a copied entry: path, type, octal mode, then size and file sha256, or the link text written. Directories carry
    path, type and mode only."""
    head = record_field(entry['path']) + b'\t' + entry['type'].encode()
    if entry['type'] == 'dir':
        return head + f'\t{entry["mode"]:o}\n'.encode()
    if entry['type'] == 'file':
        return head + f'\t{entry["mode"]:o}\t{entry["size"]}\t{digest_or_text}\n'.encode()
    return head + b'\t' + record_field(digest_or_text) + b'\n'

def record_bound(entries, src, block):
    """Upper bound of the retained selection record's bytes, block-rounded: every path and link text fully escaped (twice its
    bytes), a rebased link prefix, and 100 bytes per line for type, mode, size, sha256 and separators."""
    prefix = len(os.fsencode(str(src))) + 1
    total = sum(2 * len(os.fsencode(e['path'])) + 100 + (2 * (len(os.fsencode(e['text'])) + prefix) if e['type'] == 'link' else 0)
                for e in entries)
    return -(-total // block) * block

def record_path(src):
    """Where the source stage keeps its selection record: beside the tree (never inside it), e.g. R/source.selection.tsv."""
    return src.parent/(src.name + RECORD_SUFFIX)

def remove_own(path, seconds, clock=time.monotonic):
    """Remove this run's own staging tree bottom-up within SECONDS: (removed, entries removed). Links are unlinked,
    never followed; each directory gets owner write first (copied modes may be read-only). When the budget runs out
    the rest is left in place, so a stop always finishes with a receipt instead of an unbounded removal."""
    deadline, removed = clock() + seconds, 0
    for base, dirs, files in os.walk(path, topdown=False):
        os.chmod(base, stat.S_IMODE(os.lstat(base).st_mode) | stat.S_IRWXU)
        for name in files + dirs:
            if clock() >= deadline:
                return False, removed
            target = os.path.join(base, name)
            if os.path.isdir(target) and not os.path.islink(target):
                os.rmdir(target)
            else:
                os.unlink(target)
            removed += 1
    os.rmdir(path)
    return True, removed + 1

def construct(recipe, op, production, root=ROOT, statvfs=os.statvfs, clock=time.monotonic, planner=alpha_source.plan_copy,
              copier=alpha_source.copy_file):
    """The source stage after admission, under the lock: (ok, outcome). The production root is only read. The whole
    selection is planned before anything is written; the copy is staged beside the source root and renamed into place
    only when complete. A wall-clock timer (timeoutSeconds) and SIGTERM stop planning or copying at any point, and
    available bytes are sampled against the reserve every sampleSeconds; a stop removes only this run's staging
    directory and staged selection record (and the parent it created, when empty) within cleanupSeconds, leaving and naming any
    remainder. The per-entry selection record is kept write-once beside the tree (record_path); its sha256 is
    selectionSha256.
    It identifies the copied bytes at copy time only and verifies no project revision. No process is started."""
    prep, src = recipe['preparation'], op['sourceRoot']
    spec, reserve = prep['source'], recipe['capacity']['reserveBytes']
    outcome = {'base': str(production), 'target': str(src), 'boundBytes': spec['boundBytes'], 'boundKind': prep['boundsKind'],
               'timeoutSeconds': spec['timeoutSeconds'], 'processesStarted': 0, 'written': False, 'complete': False,
               'selectionSha256': None, 'provenance': dict(SOURCE_PROVENANCE)}
    staging, staged_record, kept_record, record = None, None, None, None
    created_parent, done, final_record = False, {'entries': 0, 'fileBytes': 0}, record_path(src)
    def stop(signum, frame):
        raise StageStopped('SIGTERM' if signum == signal.SIGTERM else f'exceeded {spec["timeoutSeconds"]} s')
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGALRM, signal.SIGTERM)}
    signal.setitimer(signal.ITIMER_REAL, spec['timeoutSeconds'])
    try:
        selection = source_selection(recipe, production, root, planner)
        fs = statvfs(nearest_existing(src))
        bound = record_bound(selection['entries'], src, fs.f_frsize)
        projected = projected_bytes(selection['entries'], fs.f_frsize) + bound
        files = [e for e in selection['entries'] if e['type'] == 'file']
        outcome['selection'] = {'entries': len(selection['entries']), 'files': len(files), 'projectedBytes': projected,
            'selectionRecordBoundBytes': bound,
            'apparentBytes': sum(e['size'] for e in files), 'excluded': selection['excluded'],
            'problems': selection['problems'][:50]}
        outcome['availableBytesBefore'] = available = fs.f_bavail * fs.f_frsize
        if selection['problems']:
            raise Refused(f'{len(selection["problems"])} unsupported source dependencies, first: {selection["problems"][0]}')
        if projected > spec['boundBytes']:
            raise Refused(f'the selection projects {projected} bytes, above the {spec["boundBytes"]}-byte bound')
        if available - projected < reserve:
            raise Refused(f'available {available} minus the projected {projected} is below the reserve {reserve}')
        if not os.path.lexists(src.parent):
            src.parent.mkdir(mode=0o700)
            created_parent = True
        staging = src.parent/(spec['stagingPrefix'] + os.urandom(6).hex())
        staging.mkdir(mode=0o700)
        outcome['written'] = True
        staged_record = staging.with_name(staging.name + RECORD_SUFFIX)
        record = os.fdopen(os.open(staged_record, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb')
        digest, directories, sample = hashlib.sha256(), [], clock() + prep['limits']['sampleSeconds']
        for entry in selection['entries']:
            if clock() >= sample:
                fs = statvfs(staging)
                if fs.f_bavail * fs.f_frsize < reserve:
                    raise StageStopped(f'available {fs.f_bavail * fs.f_frsize} fell below the reserve {reserve}')
                sample = clock() + prep['limits']['sampleSeconds']
            destination = staging/entry['path']
            if entry['type'] == 'dir':
                destination.mkdir(mode=0o700)
                directories.append(entry)
                line = record_line(entry, None)
            elif entry['type'] == 'file':
                line = record_line(entry, copier(production/entry['path'], destination, entry))
                done['fileBytes'] += entry['size']
            else:
                text = entry['text'] if entry['kind'] == 'relative' else str(src/entry['target'])
                os.symlink(text, destination)
                line = record_line(entry, text)
            digest.update(line)
            record.write(line)
            done['entries'] += 1
        for entry in reversed(directories):  # copied modes last, deepest first
            os.chmod(staging/entry['path'], entry['mode'])
        record.close()
        record_bytes = staged_record.stat().st_size
        if os.path.lexists(src) or os.path.lexists(final_record):
            raise StageStopped(f'{src} or {final_record} appeared during the copy')
        signal.setitimer(signal.ITIMER_REAL, 0)
        os.chmod(staged_record, 0o400)  # write-once: read-only once complete
        os.rename(staged_record, final_record)
        staged_record, kept_record = None, final_record
        os.rename(staging, src)
        staging = None
        outcome.update(complete=True, sourceRoot=str(src), selectionSha256=digest.hexdigest(), copied=done,
                       createdParent=created_parent,
                       selectionRecord={'path': str(final_record), 'sha256': digest.hexdigest(), 'lines': done['entries'],
                                 'bytes': record_bytes,
                                 'format': 'path TAB type TAB octal mode [TAB size TAB sha256 | TAB link text]; raw '
                                           'filesystem bytes with backslash, tab, CR and LF escaped',
                                 'claims': 'the copied bytes at copy time only; no project revision is verified'})
        return True, outcome
    except BaseException as error:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)  # cleanup of this run's own staging is not interrupted
        outcome['stagingRemoved'] = None
        if record is not None and not record.closed:
            record.close()
        if staged_record is not None and os.path.lexists(staged_record):
            os.unlink(staged_record)
        # The kept record goes only while its tree is not in place: neither is ever left without the other.
        if kept_record is not None and os.path.lexists(kept_record) and not os.path.lexists(src):
            os.unlink(kept_record)
        if staging is not None and os.path.lexists(staging):
            outcome['cleanupSeconds'] = spec['cleanupSeconds']
            try:
                outcome['stagingRemoved'], outcome['removedEntries'] = remove_own(staging, spec['cleanupSeconds'], clock)
            except OSError as cleanup:
                outcome.update(stagingRemoved=False, cleanupError=type(cleanup).__name__)
            if not outcome['stagingRemoved']:
                outcome['stagingLeft'] = str(staging)  # blocks the next source run until it is removed deliberately
        if created_parent:
            try:
                src.parent.rmdir()
            except OSError:
                pass
        outcome['createdParentLeft'] = created_parent and os.path.lexists(src.parent)
        outcome['targetExists'] = os.path.lexists(src)
        outcome['selectionRecordExists'] = os.path.lexists(final_record)
        if not isinstance(error, (StageStopped, Refused, OSError, alpha_source.PreparationFailed)):
            raise
        kind = 'refused' if isinstance(error, Refused) else 'stopped'
        outcome[kind] = f'{type(error).__name__}: {error}' if isinstance(error, OSError) else str(error)
        if 'selection' in outcome:
            outcome['partial'] = dict(done, of=outcome['selection']['entries'])
        return False, outcome
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in previous.items():
            signal.signal(sig, handler)

INSPECTION = 'roseos.blazer-preparation-inspection.v2'
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

def nearest_existing(path):
    """The closest existing ancestor of PATH (PATH itself when it exists), never following a link."""
    probe = Path(path)
    while not os.path.lexists(probe) and probe != probe.parent:
        probe = probe.parent
    return probe

def filesystem(path, statvfs):
    probe = nearest_existing(path)
    if os.path.islink(probe) or not probe.is_dir():
        return {'queried': str(probe), 'usable': False}
    fs = statvfs(probe)
    return {'queried': str(probe), 'usable': True, 'availableBytes': fs.f_bavail * fs.f_frsize,
            'totalBytes': fs.f_blocks * fs.f_frsize, 'device': os.lstat(probe).st_dev}

def tree_facts(recipe, src, budget, clock):
    """Project presence, the adevtool revision marker (128 bytes) and bounded collision sizes of one tree."""
    prep = recipe['preparation']
    facts = {'projects': {rel: meta(src/rel) for rel in [*recipe['pins'], recipe['kernelDir'], *TREE_NEEDS,
                                                          '.repo', '.repo/manifest.xml']}}
    revision = src/recipe['adevtool']['depsDir']/'adevtool_revision'
    facts['adevtoolRevision'] = None
    if revision.is_file() and not revision.is_symlink():
        with open(revision, 'rb') as f:
            facts['adevtoolRevision'] = f.read(128).decode(errors='replace').strip()
    facts['adevtoolRevisionPinned'] = recipe['pins']['vendor/adevtool']
    paths = [*prep['vendor']['collisionPaths'], prep['lunch']['outDir']]
    facts['collisions'] = {rel: bounded_bytes(src/rel, budget, clock) for rel in paths}
    return facts

def candidate_paths(candidate_root=None, layout=None, **explicit):
    """Proposed paths only: --candidate-root R proposes the recipe's candidateLayout children of R (source, downloads,
    receipts); explicit candidate flags override. None of them is a prep binding, and none is assumed to exist."""
    layout = layout or load()['preparation']['candidateLayout']
    paths = {}
    if candidate_root is not None and (not Path(candidate_root).is_absolute() or '..' in Path(candidate_root).parts):
        raise InvalidInput('--candidate-root must be an absolute path without ..')
    for key, leaf in layout['children'].items():
        value = explicit.get(key) or (str(Path(candidate_root)/leaf) if candidate_root else None)
        if value is not None:
            path = Path(value)
            if not path.is_absolute() or '..' in path.parts:
                raise InvalidInput(f'candidate {key} must be an absolute path without ..')
            paths[key] = path
    node = explicit.get('node')
    if node is not None and explicit.get('nodeFromPath'):
        raise InvalidInput('--candidate-node and --candidate-node-from-path are mutually exclusive')
    if node is not None and (not Path(node).is_absolute() or '..' in Path(node).parts):
        raise InvalidInput('candidate node must be an absolute path without ..')
    return paths, ('PATH' if explicit.get('nodeFromPath') else Path(node) if node else None), \
        (Path(candidate_root) if candidate_root else None)

def ancestors(path, boundary):
    """lstat of PATH's existing ancestors from its parent up to and including BOUNDARY (never above it)."""
    chain, boundary = [], Path(boundary)
    for parent in Path(path).parents:
        if parent != boundary and boundary not in parent.parents:
            break
        if os.path.lexists(parent):
            chain.append(meta(parent))
    return chain

def node_facts(path, how):
    """Metadata of one node candidate: lstat of the path, its realpath and the realpath's stat. Never executed."""
    if path is None:
        return {'how': how, 'found': False}
    real = os.path.realpath(path)
    facts = {'how': how, 'found': True, 'path': str(path), 'pathMeta': meta(path), 'realpath': real,
             'realpathMeta': meta(real), 'nameIsNode': Path(path).name == 'node', 'executable': os.access(real, os.X_OK),
             'version': 'not executed (metadata only)'}
    return facts

def inspect(recipe, op, root=ROOT, statvfs=os.statvfs, max_entries=200000, max_seconds=900, clock=time.monotonic,
            candidates=None, reference=None, environ=os.environ):
    """Metadata-only observation for a later, separately approved run. It reports the bound inputs (normally null), the
    proposed candidate paths (never bindings), the reference source tree (default: the production source root, read-only
    metadata), and node as the runner's own PATH resolves it plus any candidate. Reads lstat/statvfs/realpath and at most
    128 bytes of each adevtool_revision; starts no process, opens no network connection, reads no secret and writes
    nothing. It cannot bind or enable a preparation stage: plan, preflight and run still need bound inputs."""
    if not 0 < max_seconds <= INSPECT_MAX_SECONDS or max_entries < 1:
        raise InvalidInput(f'--max-seconds must be 1..{INSPECT_MAX_SECONDS} and --max-entries positive')
    started = clock()
    budget = {'entries': max_entries, 'deadline': started + max_seconds}
    prep, factory = recipe['preparation'], recipe['factory']
    production = pointer(root, prep['productionSourceRootFrom'])
    candidate, candidate_node, candidate_root = candidates or ({}, None, None)
    layout = prep['candidateLayout']
    production_device = os.lstat(production).st_dev if os.path.isdir(production) and not os.path.islink(production) else None
    reference = Path(reference or production)
    report = {'schema': INSPECTION, 'device': DEVICE, 'host': op['host'], 'metadataOnly': True, 'processesStarted': 0,
              'network': False, 'writes': False, 'enablesPreparation': False, 'productionSourceRoot': production,
              'limits': {'maxEntries': max_entries, 'maxSeconds': max_seconds}, 'lock': meta(op['lock']),
              'bound': {name: (str(op[name]) if op[name] is not None else None) for name in ('sourceRoot', 'downloadDir', 'node')},
              'candidates': {'proposed': True, 'verified': False, 'binding': False,
                             'note': 'proposed paths, not known to exist and not approved bindings',
                             'root': (dict(meta(candidate_root), path=str(candidate_root),
                                           ancestors=ancestors(candidate_root, layout['ancestorBoundary']),
                                           isolation=isolation_problem(candidate_root, production)) if candidate_root else None),
                             'ancestorBoundary': layout['ancestorBoundary']},
              'unknowns': []}
    unknowns = report['unknowns']
    for name, value in report['bound'].items():
        if value is None:
            unknowns.append(f'bound {name} is null: preparation stays refused until the binding is supplied')
    report['reference'] = {'path': str(reference), 'meta': meta(reference), 'symlinkComponent': str(symlink_in(reference) or '') or None,
                           'isProduction': reference == Path(production), 'filesystem': filesystem(reference, statvfs)}
    if reference.is_dir() and not symlink_in(reference):
        report['reference'].update(tree_facts(recipe, reference, budget, clock))
        report['reference']['bytes'] = bounded_bytes(reference, budget, clock)
    else:
        unknowns.append(f'reference source {reference} is not an existing, link-free directory')
    for key, path in candidate.items():
        entry = {'path': str(path), 'meta': meta(path), 'symlinkComponent': str(symlink_in(path) or '') or None,
                 'isolation': isolation_problem(path, production), 'filesystem': filesystem(path, statvfs),
                 'ancestors': ancestors(path, layout['ancestorBoundary'])}
        if key == 'sourceRoot' and path.is_dir() and not symlink_in(path):
            entry.update(tree_facts(recipe, path, budget, clock))
        if key == 'downloadDir':
            entry['archive'] = meta(path/factory['file'])
            entry['part'] = meta(path/(factory['file'] + prep['download']['partSuffix']))
            entry['expectedSha256'], entry['hashed'] = factory['sha256'], False
        if not entry['meta'].get('exists'):
            unknowns.append(f'candidate {key} {path} does not exist (proposal only)')
        ref_fs = report['reference']['filesystem']
        entry['sameFilesystemAsReference'] = (entry['filesystem'].get('device') == ref_fs.get('device')
                                             if entry['filesystem'].get('usable') and ref_fs.get('usable') else None)
        entry['sameDeviceAsProduction'] = (entry['filesystem'].get('device') == production_device
                                          if entry['filesystem'].get('usable') and production_device is not None else None)
        report['candidates'][key] = entry
    runner_path = environ.get('PATH', '')
    resolved = shutil.which('node', path=runner_path)
    runner = node_facts(Path(resolved) if resolved else None, 'runner PATH')
    if resolved:
        runner['pathEntry'] = str(Path(resolved).parent)
    if candidate_node == 'PATH':
        candidate_facts = dict(runner, how='--candidate-node-from-path')
    elif candidate_node:
        candidate_facts = node_facts(candidate_node, '--candidate-node')
    else:
        candidate_facts = None
    report['node'] = {'runnerPath': runner_path, 'runner': runner, 'candidate': candidate_facts}
    if not resolved:
        unknowns.append('no node on the runner PATH')
    unknowns += ['Node version (metadata only: never executed)', 'reflink support (never assumed)',
                 'isolated source copy bytes (the source stage measures its full selection before writing; never this walk)',
                 'Pro factory archive size (8 GiB cap unverified)']
    report['elapsedSeconds'] = round(clock() - started, 3)
    report['truncated'] = budget['entries'] <= 0 or clock() > budget['deadline']
    if report['truncated']:
        unknowns.append('a bounded walk stopped at --max-entries or --max-seconds: sizes are lower bounds')
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
    command = commands(recipe, op, probes.get('root', ROOT))[stage]
    base = {'stage': stage, 'device': DEVICE, 'signed': False, 'releaseReady': False}
    with open(op['lock'], 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            release_receipts.write_once(receipt_dir/'completed.json', {**base, 'started': False, 'error': 'lock taken after preflight'})
            return 1
        if stage == 'vendor':  # a private PATH entry holding only the verified node, never the rest of its directory
            node_dir = receipt_dir/NODE_PATH_DIR
            node_dir.mkdir(mode=0o700)
            os.symlink(op['node'], node_dir/'node')
            command['env']['PATH'] = f'{node_dir}:{SAFE_PATH}'
        release_receipts.write_once(receipt_dir/'started.json', {**base, 'host': op['host'], 'command': command, 'at': stamp(now)})
        if stage == 'source':
            ok, outcome = construct(recipe, op, Path(report['facts']['productionSourceRoot']), probes.get('root', ROOT),
                                    statvfs=statvfs, clock=clock)
            release_receipts.write_once(receipt_dir/'completed.json', {**base, 'ok': ok, **outcome, 'at': stamp(now)})
            return 0 if ok else 1
        if stage == 'download':
            ok, outcome = download(recipe, command, fetcher, clock)
            release_receipts.write_once(receipt_dir/'completed.json', {**base, 'ok': ok, **outcome, 'at': stamp(now)})
            return 0 if ok else 1
        # Inherited GIT_* (e.g. a GIT_DIR or GIT_WORK_TREE elsewhere) never reaches a child: git answers only from
        # the tree itself, bounded by GIT_CEILING_DIRECTORIES at the source root.
        env = {**{k: v for k, v in os.environ.items() if k != 'PATH' and not k.startswith('GIT_')}, **command['env']}
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
            # ADEVTOOL_SKIP_DEP_BUILD=1 skips adevtool's own marker-vs-HEAD comparison; it is made here instead, before
            # any generation: the marker is the exact pin (preflight), and HEAD must print exactly the pin too.
            head = supervise(command['adevtoolHead'], command['cwd'], env, command['adevtoolHeadTimeoutSeconds'], limits,
                             capture=True, statvfs=statvfs, measure=measure, clock=clock, sleep=sleep)
            want = recipe['pins']['vendor/adevtool'] + '\n'
            if head['returncode'] or head['stopped'] or head.get('stdout') != want:
                release_receipts.write_once(receipt_dir/'completed.json', {**base, 'ok': False,
                    'adevtoolHead': {'returncode': head['returncode'], 'stopped': head['stopped'],
                                     'stdout': (head.get('stdout') or '')[:128]},
                    'error': 'git -C vendor/adevtool rev-parse HEAD did not print exactly the pinned revision; no genuine '
                             'adevtool version data, so nothing is generated', 'at': stamp(now)})
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
            outcome['adevtoolHead'] = want.strip()
            outcome['skipDepBuild'] = dict(prep['vendor']['adevtoolGit']['skipDepBuildEnv'])
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
    for flag in ('--candidate-root', '--candidate-source-root', '--candidate-download-dir', '--candidate-receipts-dir',
                 '--candidate-node', '--reference-source-root'):
        parser.add_argument(flag)
    parser.add_argument('--candidate-node-from-path', action='store_true')
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
        inspect_only = {k: v for k, v in vars(a).items() if (k.startswith('candidate_') or k == 'reference_source_root') and v}
        if a.action == 'inspect':
            candidates = candidate_paths(a.candidate_root, recipe['preparation']['candidateLayout'], sourceRoot=a.candidate_source_root,
                                         downloadDir=a.candidate_download_dir, receiptsDir=a.candidate_receipts_dir,
                                         node=a.candidate_node, nodeFromPath=a.candidate_node_from_path)
            if a.reference_source_root and (not Path(a.reference_source_root).is_absolute() or '..' in Path(a.reference_source_root).parts):
                raise InvalidInput('--reference-source-root must be an absolute path without ..')
            print(json.dumps(inspect(recipe, op, max_entries=a.max_entries, max_seconds=a.max_seconds, candidates=candidates,
                                     reference=a.reference_source_root), indent=2, default=str))
            return 0
        if inspect_only:
            raise InvalidInput(f'{", ".join("--" + k.replace("_", "-") for k in sorted(inspect_only))} are inspect-only: '
                               'candidates are never preparation bindings; supply --source-root/--download-dir/--node')
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
