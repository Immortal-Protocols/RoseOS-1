#!/usr/bin/env python3
"""os-release device-readiness mode (FAM-HOST-READINESS-S002, FIX001): one bounded, metadata-only inspection of the
retained Pixel source tree on the existing builder for the fixed Pixel 10 targets of os/devices/pixel10.json. It admits
nothing to delivery (release-pipeline.py SUPPORTED_DEVICES stays frankel only), never compiles, signs, downloads, syncs,
generates vendor trees, cleans or deletes outputs, and reads no secret, key or credential.

It never executes anything from the tree: no envsetup.sh, lunch, soong_ui, vendorsetup.sh or cmds-for-envsetup.sh.
Those scripts run as ordinary host bash with the runner's full access, and OUT_DIR, HOME and TMPDIR only redirect
conventional paths; no existing supported mechanism confines them. Lunch and target results are therefore unknown, and
every finished inspection is PARTIAL (metadata only); none is complete or target-ready.

Every tree file and directory is opened one component at a time with O_NOFOLLOW (open_under): a symlink anywhere on
the path, or a secret-named component, is refused before anything behind it is opened, read, hashed or listed. Listings
check their entry and time caps before each entry and report a stop as incomplete, never as a total or zero.

  check    the checked-in contract and this script's pins agree (before the builder starts); reads only the checkout
  plan     print what run would inspect, marked executed: false; runs nothing
  run      --run-id, --run-attempt, --recipe-sha, --evidence-dir: on the builder, under the shared lock. Refuses foreign
           work, a wrong instance or recipe, and an existing task root. Measures filesystem bytes/type and bounded named
           usage; reads vendor/adevtool and vendor/state HEADs against the contract pins; hashes the contract-required
           product files, generated vendor/google_devices/<device> files and the lunch-chain files (compared, never run;
           presence is not completeness); records factory-image presence separately from a checksum validated within
           budget. Writes one sanitized receipt and SHA256SUMS to --evidence-dir
  cleanup  --run-id, --run-attempt: removes only this attempt's task root, and only when its ownership marker matches
Exit status: 0 inspected (PARTIAL, metadata only), 1 check failed or refused, 2 invalid input, 4 setup missing.
"""
import argparse
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'os/release-pipeline/frankel.json'
CONTRACT = ROOT/'os/devices/pixel10.json'
PROVENANCE = ROOT/'os/tests/fixtures/adevtool-17df3b79/PROVENANCE.json'
# Inspected targets only, from the OS contract; never the release allowlist.
READINESS_DEVICES = ('frankel', 'blazer', 'rango')
EXPECTED_PINS = {'vendor/adevtool': '17df3b79a73e67ad68387b35c7d41c85e1d8103c',
    'vendor/state': '1469180711293a5852bfda9a5f67b8f53d9828ad'}
# The lunch call chain as read at the pinned build/make b0f697df and build/soong 250710f7. Compared as metadata only:
# matching bytes are not a confinement mechanism, so nothing here is ever executed.
LUNCH_CHAIN = {
    'build/make/envsetup.sh': '0edc666156d0690ae762b8958efaca109c3b012e88471c317794c63679f978ba',
    'build/make/shell_utils.sh': 'b78e69ad82924546bfd74154ef8e95442bff51131564b66c4620c9155dcd8487',
    'build/soong/soong_ui.bash': '3b50c94ed3e209d573b1e34f1fc2ea1778bca049bec922292ddd3548e0a68675',
    'build/soong/scripts/microfactory.bash': '29d650997a0bdfbd9f3be7f424bb5e2106a80859c7c12f01065613ee77b165f4',
}
CONFINEMENT_UNAVAILABLE = ('no supported mechanism confines tree scripts: lunch sources build/envsetup.sh, every '
    'vendorsetup.sh under device/, vendor/ and product/, and vendor/*/<product>/cmds-for-envsetup.sh as ordinary host bash '
    'with the runner\'s access; OUT_DIR, HOME and TMPDIR only redirect conventional paths, and a before/after scan is not '
    'preventive confinement')
GENERATED_FILES = ('AndroidProducts.mk', '{device}.mk', 'cmds-for-envsetup.sh', 'firmware/android-info.txt')
USAGE = {'generatedVendor': 'vendor/google_devices', 'adevtoolDownloads': 'vendor/adevtool/dl', 'defaultOutput': 'out',
    'repoMetadata': '.repo'}  # plus retainedOutput, frankel.json host.outDir
FOREIGN = frozenset({'soong_ui', 'soong_build', 'ninja', 'n2', 'ckati', 'kati', 'launch_cvd', 'run_cvd', 'crosvm',
    'qemu-system-x86', 'qemu-system-aarch64', 'repo', 'adevtool'})
SECRET_NAMES = frozenset({'keys', '.ssh', '.gnupg', '.android', 'secrets', '.config', 'credentials'})
MARKER = '.rose-device-readiness-owner'
LIMITS = {'deadlineSeconds': 900, 'gitSeconds': 30, 'usageSeconds': 45, 'usageEntries': 2_000_000,
    'hashSeconds': 90, 'smallFileBytes': 4 << 20, 'listedNames': 50, 'listingEntries': 10_000, 'listingSeconds': 10}
NOT_PROVEN = ['lunch, product configuration or any target variable (not executed)', 'source-tree integrity',
    'completeness of any generated vendor tree or factory download', 'that any target compiles, signs or boots',
    'artifact, signature, runtime or phone acceptance', 'release admission (frankel only, unchanged)']
SAFE_VALUE = re.compile(r'[A-Za-z0-9_.,:/+=@-]{0,256}')
SECRETISH = re.compile(r'(?i)(token|secret|passw|private|credential|authoriz|bearer|api[_-]?key|BEGIN [A-Z ]*KEY)')

class CheckFailed(Exception):
    pass

class InvalidInput(Exception):
    pass

class SetupMissing(Exception):
    pass

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()

def git_blob(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()

def load_pipeline():
    spec = importlib.util.spec_from_file_location('release_pipeline_readiness', ROOT/'scripts/release-pipeline.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class Deadline:
    def __init__(self, seconds, clock=time.monotonic):
        self.clock, self.end = clock, clock() + seconds

    def budget(self, limit):
        return max(0.0, min(limit, self.end - self.clock()))

# ---------------------------------------------------------------- contract

def contract_targets(contract):
    """The fixed inspected targets, their lunch combos and required inputs; refuses a contract this script does not pin."""
    failures = []
    devices = contract.get('devices') or {}
    if tuple(devices) != READINESS_DEVICES:
        failures.append(f'contract devices {sorted(devices)} are not {list(READINESS_DEVICES)}')
    projects = (contract.get('source') or {}).get('projects') or {}
    failures += [f'contract pins {p} at {projects.get(p)}, not {rev}' for p, rev in EXPECTED_PINS.items() if projects.get(p) != rev]
    shared = contract.get('shared') or {}
    targets = {}
    for device in READINESS_DEVICES:
        entry = devices.get(device) or {}
        lunch = (entry.get('lunch') or {}).get('expected')
        if entry.get('product') != device or lunch != f'{device}-cur-user':
            failures.append(f'{device}: product/lunch is not {device}-cur-user')
            continue
        config = entry.get('adevtoolConfig') or ''
        factory = ((entry.get('vendorInputs') or {}).get('factoryImage')) or {}
        if not re.fullmatch(r'config/device/[a-z]+\.yml', config) or not re.fullmatch(r'[a-z0-9.-]+\.zip', factory.get('file') or '') \
                or not re.fullmatch(r'[0-9a-f]{64}', factory.get('sha256') or ''):
            failures.append(f'{device}: adevtool config or factory image entry is malformed')
            continue
        required = ['vendor/adevtool/' + config, 'vendor/adevtool/' + shared.get('adevtoolDeviceList', ''),
            'vendor/adevtool/config/device/' + shared.get('include', '')]
        required += [p.replace('<device>', device) for p in shared.get('inheritance', [])]
        required += ['build/make/' + p for p in (contract.get('roseInclusion') or {}).get('products', {})]
        if any('..' in p.split('/') or not re.fullmatch(r'(vendor/adevtool|build/make)/[\w./-]+', p) for p in required):
            failures.append(f'{device}: a required product path is unsafe')
            continue
        targets[device] = {'lunch': lunch, 'required': required, 'factory': factory['file'], 'factorySha256': factory['sha256'],
            'unpacked': f'{device}-{shared.get("vendorBuildId", "")}', 'kernelDir': entry.get('kernelDir')}
    if failures:
        raise CheckFailed('; '.join(failures))
    return targets

def check():
    contract_targets(json.loads(CONTRACT.read_text()))
    config = json.loads(CONFIG.read_text())
    pipeline = load_pipeline()
    if pipeline.SUPPORTED_DEVICES != ('frankel',) or config['target']['device'] != 'frankel':
        raise CheckFailed('release admission is not frankel only')
    return {'contract': str(CONTRACT.relative_to(ROOT)), 'contractSha256': sha256_file(CONTRACT), 'devices': list(READINESS_DEVICES),
        'lock': config['host']['lockPath'], 'sourceRoot': config['host']['sourceRoot'], 'instance': pipeline.BUILDER['instance']}

# ---------------------------------------------------------------- bounded reads

DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC

def open_under(root, rel, directory=False):
    """An fd for root/rel, opened one component at a time with O_NOFOLLOW relative to its parent's fd. A secret-named or
    unsafe component is refused before anything is opened, and a symlink anywhere on the path - the final component or
    any ancestor, pointing inside or outside the root - is refused without opening what it points at. Nothing behind a
    refused component is read, hashed or enumerated. Returns (fd, 'present') or (None, reason)."""
    parts = Path(rel).parts
    if not parts or Path(rel).is_absolute() or any(p in ('.', '..') for p in parts):
        return None, 'unsafe-path'
    if any(p in SECRET_NAMES for p in parts):
        return None, 'secret-path-refused'
    fd = os.open(os.path.realpath(root), DIRECTORY)
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        try:
            child = os.open(part, FILE if last and not directory else DIRECTORY, dir_fd=fd)
        except OSError as error:
            reason = 'missing' if error.errno == errno.ENOENT else 'symlink-refused' if error.errno == errno.ELOOP else None
            if reason is None and error.errno == errno.ENOTDIR:
                try:
                    reason = 'symlink-refused' if stat.S_ISLNK(os.lstat(part, dir_fd=fd).st_mode) else 'not-a-directory'
                except OSError:
                    reason = 'missing'
            os.close(fd)
            return None, reason or f'unreadable ({errno.errorcode.get(error.errno, error.errno)})'
        os.close(fd)
        fd = child
    if not directory and not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None, 'not-regular'
    return fd, 'present'

def dir_status(root, rel):
    fd, status = open_under(root, rel, directory=True)
    if fd is not None:
        os.close(fd)
    return status

def read_fd(fd, size):
    """Every content read of a tree file goes through here, after open_under has validated its whole path."""
    with os.fdopen(os.dup(fd), 'rb') as f:
        return f.read(size)

def file_record(root, rel, blobs=None, limit=LIMITS['smallFileBytes']):
    fd, status = open_under(root, rel)
    record = {'path': rel, 'status': status}
    if fd is None:
        return record
    try:
        size = os.fstat(fd).st_size
        record['sizeBytes'] = size
        if size > limit:
            record['status'] = 'too-large-not-hashed'
            return record
        data = read_fd(fd, limit + 1)
    finally:
        os.close(fd)
    record.update(sha256=hashlib.sha256(data).hexdigest(), gitBlob=git_blob(data))
    if blobs and rel in blobs:
        record['matchesPinnedFixture'] = record['gitBlob'] == blobs[rel]
    return record

def bounded_entries(fd, max_entries, end, clock):
    """Directory entries of fd, checking the entry and time caps before each one is handled. Stops early (closing the
    listing) and reports why; never yields more than max_entries."""
    seen = 0
    with os.scandir(fd) as items:
        for item in items:
            if seen >= max_entries:
                return f'entry cap ({max_entries})'
            if clock() > end:
                return 'time cap'
            seen += 1
            yield item
    return None

def walk_entries(fd, max_entries, end, clock):
    """(entries, stop reason) as a list; the generator's return value carries the reason."""
    entries, generator = [], bounded_entries(fd, max_entries, end, clock)
    while True:
        try:
            entries.append(next(generator))
        except StopIteration as stop:
            return entries, stop.value

def usage(root, rel, deadline, clock=time.monotonic):
    """Bytes and entries under root/rel by lstat only. Every directory is opened through open_under (no symlink on its
    path is followed and no secret-named directory is entered), other filesystems are not entered, and the entry and
    time caps are checked before each entry: a cap or an unreadable directory yields incomplete with its reason and no
    total, never a partial number or zero."""
    fd, status = open_under(root, rel, directory=True)
    if fd is None:
        return {'path': rel, 'status': 'absent' if status == 'missing' else 'refused', 'reason': status}
    device = os.fstat(fd).st_dev
    os.close(fd)
    end = clock() + deadline.budget(LIMITS['usageSeconds'])
    total, entries, skipped, stack = 0, 0, 0, [rel]
    while stack:
        current = stack.pop()
        fd, status = open_under(root, current, directory=True)
        if fd is None:
            return {'path': rel, 'status': 'incomplete', 'reason': f'{current}: {status}', 'entriesExamined': entries}
        try:
            generator = bounded_entries(fd, LIMITS['usageEntries'] - entries, end, clock)
            while True:
                try:
                    item = next(generator)
                except StopIteration as stop:
                    if stop.value:
                        return {'path': rel, 'status': 'incomplete', 'reason': stop.value, 'entriesExamined': entries}
                    break
                entries += 1
                st = item.stat(follow_symlinks=False)
                total += st.st_blocks * 512
                if stat.S_ISDIR(st.st_mode):
                    if item.name in SECRET_NAMES or st.st_dev != device:
                        skipped += 1
                    else:
                        stack.append(f'{current}/{item.name}')
        except OSError as error:
            return {'path': rel, 'status': 'incomplete', 'reason': f'unreadable ({errno.errorcode.get(error.errno, error.errno)})',
                'entriesExamined': entries}
        finally:
            os.close(fd)
    return {'path': rel, 'status': 'complete', 'allocatedBytes': total, 'entries': entries, 'skippedDirectories': skipped}

def state_entries(root, devices, deadline, clock=time.monotonic):
    """vendor/state names mentioning each device, from one bounded listing: the directory is opened through open_under,
    caps are checked per entry, and only the bounded collection is sorted. A truncated or incomplete result says so; it
    is never presented as the first names of the whole directory."""
    fd, status = open_under(root, 'vendor/state', directory=True)
    if fd is None:
        return {'status': 'absent' if status == 'missing' else 'refused', 'reason': status}
    end = clock() + deadline.budget(LIMITS['listingSeconds'])
    found, truncated = {d: [] for d in devices}, set()
    try:
        entries, stopped = walk_entries(fd, LIMITS['listingEntries'], end, clock)
    finally:
        os.close(fd)
    for item in entries:
        for device in devices:
            if device in item.name:
                if len(found[device]) < LIMITS['listedNames']:
                    found[device].append(sanitize(item.name))
                else:
                    truncated.add(device)
    result = {'status': 'incomplete' if stopped else 'complete', 'scanned': len(entries),
        'devices': {d: {'names': sorted(found[d]), 'truncated': d in truncated} for d in devices}}
    if stopped:
        result['reason'] = stopped
    if stopped or truncated:
        result['note'] = 'a bounded, unordered subset of the directory; not its first names'
    return result

def filesystem(root, mounts='/proc/mounts'):
    st = os.statvfs(root)
    real, best = os.path.realpath(root), ('', None)
    try:
        for line in Path(mounts).read_text().splitlines():
            fields = line.split()
            if len(fields) > 2 and (real == fields[1] or real.startswith(fields[1].rstrip('/') + '/')) and len(fields[1]) >= len(best[0]):
                best = (fields[1], fields[2])
    except OSError:
        pass
    return {'availableBytes': st.f_bavail * st.f_frsize, 'totalBytes': st.f_blocks * st.f_frsize,
        'freeBytes': st.f_bfree * st.f_frsize, 'mountPoint': best[0] or None, 'type': best[1]}

def hash_within(root, rel, seconds, clock=time.monotonic):
    fd, status = open_under(root, rel)
    if fd is None:
        return None
    digest, end = hashlib.sha256(), clock() + seconds
    with os.fdopen(fd, 'rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            if clock() > end:
                return None
            digest.update(block)
    return digest.hexdigest()

def foreign_work(proc='/proc'):
    names = set()
    for entry in Path(proc).iterdir():
        if entry.name.isdigit():
            try:
                comm = (entry/'comm').read_text().strip()
            except OSError:
                continue
            if comm in FOREIGN:
                names.add(comm)
    return sorted(names)

def sanitize(value):
    return value if SAFE_VALUE.fullmatch(value) and not SECRETISH.search(value) else '(withheld)'

# ---------------------------------------------------------------- host steps

def git_head(root, project, runner, timeout):
    status = dir_status(root, project)
    if status != 'present':
        return {'status': status}
    path = Path(os.path.realpath(root))/project
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0', 'LANG': 'C'}
    try:
        result = runner(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-C', str(path), 'rev-parse', '--verify', 'HEAD^{commit}'],
            env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'status': 'timeout'}
    head = result.stdout.strip()
    if result.returncode or not re.fullmatch(r'[0-9a-f]{40}', head):
        return {'status': 'unreadable', 'exit': result.returncode}
    return {'status': 'read', 'observed': head}

def lunch_chain(root):
    """The chain files' bytes against the inspected revision: metadata, never a reason to run them."""
    files = {}
    for rel, expected in LUNCH_CHAIN.items():
        record = file_record(root, rel)
        record['matchesInspectedRevision'] = record.get('sha256') == expected
        files[rel] = record
    fd, _ = open_under(root, 'build', directory=True)
    try:
        linked = fd is not None and os.readlink('envsetup.sh', dir_fd=fd) == 'make/envsetup.sh'
    except OSError:
        linked = False
    finally:
        if fd is not None:
            os.close(fd)
    return {'files': files, 'envsetupEntryResolvesToBuildMake': linked}

# ---------------------------------------------------------------- run

class Host:
    """Everything run touches outside its own arguments, so tests can substitute fixtures."""
    def __init__(self, source_root, lock_path, retained_out, instance, home):
        self.source_root, self.lock_path, self.retained_out = Path(source_root), Path(lock_path), retained_out
        self.instance, self.home = instance, Path(home)
        self.hostname = lambda: socket.gethostname().split('.')[0]
        self.foreign = foreign_work
        self.runner = subprocess.run
        self.clock = time.monotonic
        self.mounts = '/proc/mounts'
        self.head = lambda: subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()

    @classmethod
    def production(cls):
        config, pipeline = json.loads(CONFIG.read_text()), load_pipeline()
        return cls(config['host']['sourceRoot'], config['host']['lockPath'], config['host']['outDir'],
            pipeline.BUILDER['instance'], os.environ['HOME'])

def attempt_id(run_id, run_attempt):
    if not re.fullmatch(r'[1-9][0-9]{0,19}', str(run_id)) or not re.fullmatch(r'[1-9][0-9]{0,3}', str(run_attempt)):
        raise InvalidInput('--run-id and --run-attempt must be the numeric Actions run id and attempt')
    return f'{run_id}-{run_attempt}'

def task_root(host, attempt):
    return host.home/'rose-actions'/f'device-readiness-{attempt}'

def inspect(host, receipt, deadline, targets):
    root = host.source_root
    receipt['filesystem'] = filesystem(root, host.mounts)
    receipt['pins'] = {}
    for project, expected in EXPECTED_PINS.items():
        head = git_head(root, project, host.runner, deadline.budget(LIMITS['gitSeconds']))
        head.update(expected=expected, matches=head.get('observed') == expected)
        receipt['pins'][project] = head
    receipt['manifest'] = file_record(root, '.repo/manifest.xml')
    pinned = sha256_file(ROOT/'os/pinned-manifest.xml')
    receipt['manifest']['equalsPinnedManifest'] = receipt['manifest'].get('sha256') == pinned
    blobs = {'vendor/adevtool/' + r['path']: r['gitBlob'] for r in json.loads(PROVENANCE.read_text())['files']
        if r['kind'] == 'full' and r['repository'].endswith('/adevtool')}
    blobs.update({'build/make/' + r['path']: r['gitBlob'] for r in json.loads(PROVENANCE.read_text())['files']
        if r['kind'] == 'full' and r['repository'].endswith('/platform_build')})
    receipt['products'], receipt['generatedVendor'], receipt['factory'] = {}, {}, {}
    for device, target in targets.items():
        records = [file_record(root, rel, blobs) for rel in target['required']]
        receipt['products'][device] = {'files': records,
            'kernelDir': dir_status(root, target['kernelDir']) if target['kernelDir'] else 'missing'}
        base, skel = f'vendor/google_devices/{device}', f'vendor/adevtool/vendor-skels/google_devices/{device}'
        names = [name.format(device=device) for name in GENERATED_FILES]
        files = [file_record(root, f'{base}/{n}', {f'{base}/{n}': blobs[f'{skel}/{n}']} if f'{skel}/{n}' in blobs else None)
            for n in names]
        directory = dir_status(root, base)
        state = directory if directory != 'present' else \
            'present-unverified' if all(f['status'] == 'present' for f in files) else 'incomplete'
        receipt['generatedVendor'][device] = {'status': state, 'files': files}
        dl = f'vendor/adevtool/dl/{target["factory"]}'
        fd, status = open_under(root, dl)
        size = None
        if fd is not None:
            size = os.fstat(fd).st_size
            os.close(fd)
        receipt['factory'][device] = {'path': dl, 'status': status, 'sizeBytes': size,
            'checksum': 'notValidated', 'expectedSha256': target['factorySha256'],
            'unpacked': dir_status(root, f'vendor/adevtool/dl/unpacked/{target["unpacked"]}')}
    receipt['vendorState'] = state_entries(root, list(targets), deadline, host.clock)
    # Never executed: no supported mechanism confines tree scripts. The inputs a later confined route would need are
    # reported per device; lunch and target results stay unknown.
    receipt['lunch'] = {'executed': False, 'status': 'NOT_EXECUTED', 'reason': CONFINEMENT_UNAVAILABLE,
        'chain': lunch_chain(root), 'devices': {}}
    for device, target in targets.items():
        missing = [f'{r["path"]} {r["status"]}' for r in receipt['products'][device]['files'] if r['status'] != 'present']
        if receipt['generatedVendor'][device]['status'] != 'present-unverified':
            missing.append(f'vendor/google_devices/{device} {receipt["generatedVendor"][device]["status"]}')
        receipt['lunch']['devices'][device] = {'lunch': 'unknown', 'target': 'unknown', 'combo': target['lunch'],
            'missingInputs': missing}
    # Bounded usage last: an unavailable category is reported as such, never as zero.
    names = {**USAGE, 'retainedOutput': host.retained_out}
    receipt['usage'] = {name: usage(root, rel, deadline, host.clock) for name, rel in names.items()}
    for device, record in receipt['factory'].items():
        if record['status'] == 'present':
            seconds = deadline.budget(LIMITS['hashSeconds'])
            digest = hash_within(root, record['path'], seconds, host.clock) if seconds >= 1 else None
            record['checksum'] = 'unavailable' if digest is None else 'validated' if digest == record['expectedSha256'] else 'mismatch'

def run(host, run_id, run_attempt, recipe_sha, evidence_dir):
    attempt = attempt_id(run_id, run_attempt)
    if not re.fullmatch(r'[0-9a-f]{40}', recipe_sha or ''):
        raise InvalidInput('--recipe-sha must be a full commit')
    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(parents=True)  # a fresh, job-owned directory: refuses one that exists
    deadline = Deadline(LIMITS['deadlineSeconds'], host.clock)
    targets = contract_targets(json.loads(CONTRACT.read_text()))
    root_dir = task_root(host, attempt)
    receipt = {'receiptType': 'device-readiness', 'schemaVersion': 1, 'mode': 'device-readiness', 'attempt': attempt,
        'recipeSha': recipe_sha, 'contract': {'path': str(CONTRACT.relative_to(ROOT)), 'sha256': sha256_file(CONTRACT)},
        'devices': list(targets), 'sourceRoot': str(host.source_root), 'lock': {'path': str(host.lock_path), 'acquired': False},
        'instance': {'expected': host.instance}, 'taskRoot': str(root_dir), 'limits': LIMITS, 'executedMake': False,
        'status': 'refused', 'refusal': None, 'notProven': NOT_PROVEN}
    lock = None
    try:
        if host.head() != recipe_sha:
            raise CheckFailed('the checked-out workflow recipe is not --recipe-sha')
        receipt['instance']['observed'] = sanitize(host.hostname())
        if receipt['instance']['observed'] != host.instance:
            raise CheckFailed(f'this runner is not instance {host.instance}')
        if not host.source_root.is_dir():
            raise SetupMissing(f'{host.source_root} is not present on this runner')
        lock = open(host.lock_path, 'a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CheckFailed('the shared builder lock is held by another job')
        receipt['lock']['acquired'] = True
        foreign = host.foreign()
        receipt['foreignWork'] = foreign
        if foreign:
            raise CheckFailed('foreign build or guest work is running: ' + ', '.join(foreign))
        root_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            root_dir.mkdir()
        except FileExistsError:
            raise CheckFailed(f'task root {root_dir} already exists; it is not this attempt\'s and is left untouched')
        (root_dir/MARKER).write_text(attempt + '\n')
        inspect(host, receipt, deadline, targets)
        # Metadata only: never complete or target-ready, whatever the metadata says.
        receipt['status'] = 'partial'
        receipt['partialReasons'] = [CONFINEMENT_UNAVAILABLE] + ([] if deadline.budget(1) > 0 else ['inspection deadline reached'])
    except (CheckFailed, SetupMissing) as error:
        receipt['refusal'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        if lock:
            lock.close()
        receipt['at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        with open(evidence_dir/'device-readiness.json', 'x') as f:
            json.dump(receipt, f, indent=2, sort_keys=True)
            f.write('\n')
        (evidence_dir/'SHA256SUMS').write_text(f'{sha256_file(evidence_dir/"device-readiness.json")}  device-readiness.json\n')
    return receipt

def cleanup(host, run_id, run_attempt):
    """Removes this attempt's task root (its marker and scratch), never a root it did not create."""
    attempt = attempt_id(run_id, run_attempt)
    root_dir = task_root(host, attempt)
    if not os.path.lexists(root_dir):
        return {'taskRoot': str(root_dir), 'removed': False, 'reason': 'absent'}
    marker = root_dir/MARKER
    if root_dir.is_symlink() or not root_dir.is_dir() or marker.is_symlink() or not marker.is_file() \
            or marker.read_text() != attempt + '\n':
        raise CheckFailed(f'{root_dir} is not owned by attempt {attempt}; left untouched')
    shutil.rmtree(root_dir)
    return {'taskRoot': str(root_dir), 'removed': True}

def plan():
    summary = check()
    targets = contract_targets(json.loads(CONTRACT.read_text()))
    return {'receiptType': 'device-readiness-plan', 'executed': False, **summary, 'lunchChain': LUNCH_CHAIN,
        'lunch': {'executed': False, 'reason': CONFINEMENT_UNAVAILABLE}, 'commands': {},
        'requiredFiles': {d: t['required'] for d, t in targets.items()}, 'limits': LIMITS,
        'note': 'planned only; nothing was run or inspected'}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    sub.add_parser('plan')
    for name in ('run', 'cleanup'):
        p = sub.add_parser(name)
        p.add_argument('--run-id', required=True)
        p.add_argument('--run-attempt', required=True)
        if name == 'run':
            p.add_argument('--recipe-sha', required=True)
            p.add_argument('--evidence-dir', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'check':
            result = check()
        elif args.command == 'plan':
            result = plan()
        elif args.command == 'run':
            result = run(Host.production(), args.run_id, args.run_attempt, args.recipe_sha, args.evidence_dir)
            result = {k: result[k] for k in ('status', 'partialReasons', 'attempt', 'devices', 'filesystem') if k in result}
        else:
            result = cleanup(Host.production(), args.run_id, args.run_attempt)
    except InvalidInput as error:
        print(f'invalid input: {error}', file=sys.stderr)
        return 2
    except SetupMissing as error:
        print(f'SETUP_MISSING: {error}', file=sys.stderr)
        return 4
    except (CheckFailed, OSError) as error:
        print(f'check failed: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0

if __name__ == '__main__':
    sys.exit(main())
