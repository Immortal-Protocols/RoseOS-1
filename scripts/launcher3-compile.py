#!/usr/bin/env python3
"""LAUNCHER3-COMPILE adapter (Release-owned): a compile-only proof of RoseOS's Launcher3 changes against OS Product's
input contract os/compile-contracts/launcher3-quickstep-lib.json (scripts/launcher3-compile-contract.py, #410/#440).

  check                       hosted and on the builder: the contract is the pinned version (MANIFEST_SHA256), the
                              binding os/build-only/launcher3-compile-host.json is exact, and every input the selected
                              branch needs is bound. Exit 4 while any is null. Reads no host path.
  run --run-id --attempt      under the nonblocking shared builder lock, into a new per-run root
                              <taskParent>/launcher3-compile-<run>-<attempt>/{tree,out,home,tmp,receipts}:
                              1. byte-copy the bound source tree (read only; alpha exclusion policy: outputs, .repo,
                                 .git, keys, releases), refused before writing when the selection projects above
                                 SOURCE_BOUND_BYTES or available minus the projection is below RESERVE_BYTES;
                              2. classify every contract file in the copy and take exactly one branch:
                                 verifyIntegrated - every affected file equals its post-image (config.xml only through
                                 OS Product's verify_integrated_config, with the bound policy receipt and frankel's
                                 applyArgs) and every required overlay is present at its sha256; nothing is applied;
                                 reconstruct - needs an approved genuine-Git-object source, none is bound: refused;
                                 anything else (mixed, pre-images, unknown hashes, missing files) - refused, nothing
                                 is reset or patched;
                              3. lunch and `m -j8 Launcher3QuickStepLib <flag caches>` with OUT_DIR, HOME, TMPDIR and
                                 XDG_* inside the run root, no ccache and no GIT_*; sampled stops (every SAMPLE_SECONDS)
                                 on the output filesystem's growth (OUTPUT_STOP_BYTES) and the process group's resident
                                 memory (MEMORY_STOP_BYTES), and a wall-clock COMPILE_SECONDS; TERM, then KILL;
                              4. a sanitized report.json: the branch, the classification, the bound inputs, the compile
                                 outcome, the source-list and classpath proof and both aconfig flag values. Never a raw
                                 log, never current bytes as provenance.

What a passing report shows: the patched handlers and overlays type-check and compile on the real classpath, and the
generated aconfig values. Not R8, manifest merge, APK resources, packaging, signing or runtime behaviour. The sampled
stops can overshoot and are not hard caps; containment is by paths and environment only (no cgroup or quota exists).
Bounds are first-measurement ceilings proposed by Infra, not measurements. Exit status: 0 report written, 2 invalid,
3 refused, 4 unbound.
"""
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import alpha_source  # noqa: E402  (the reviewed copy plan and per-file copy)

MANIFEST = ROOT/'os/compile-contracts/launcher3-quickstep-lib.json'
# OS Product's reviewed contract version (#440 e361d1c). A different manifest is refused until this pin is updated.
MANIFEST_SHA256 = '2c556473e0d1848a46660c98b710d2fe2161c814fb2784d557fb895f3ec68d13'
BINDING = ROOT/'os/build-only/launcher3-compile-host.json'
BINDING_KEYS = {'schemaVersion', 'kind', 'basis', 'host', 'lockFrom', 'taskParent', 'branch', 'sourceTree', 'lunch',
                'policyReceipt', 'reconstructSource'}
TASK_NAME = 'launcher3-compile-{run_id}-{attempt}'
TARGET = 'Launcher3QuickStepLib'
JOBS = 8
GIB = 1 << 30
# Infra's first-measurement ceilings (2026-11-03), not measured requirements.
SOURCE_BOUND_BYTES, RESERVE_BYTES = 150 * GIB, 300 * GIB
PREPARE_SECONDS, COMPILE_SECONDS = 120 * 60, 120 * 60
OUTPUT_STOP_BYTES, MEMORY_STOP_BYTES = 150 * GIB, 64 * GIB
SAMPLE_SECONDS, KILL_GRACE_SECONDS = 5, 30
# The aconfig flags the report binds come from the contract's flagEvidence: each flag's own aconfig_declarations module,
# its expectedOutputDir (out/... relative to OUT_DIR's parent naming) and its exact expectedLine
# ("<package>.<name>:<permission>=<state:bool>", the format of the module's aconfig-flags.txt).
FLAG_TEXT, FLAG_CACHE = 'aconfig-flags.txt', 'aconfig-cache.pb'
# build/make tools/aconfig/aconfig_protos/protos/aconfig.proto at b0f697df: parsed_flags.parsed_flag = 1;
# parsed_flag: package = 1, name = 2, state = 6, permission = 7; ENABLED = 1, DISABLED = 2; READ_ONLY = 1, READ_WRITE = 2.
STATE, PERMISSION = {1: 'ENABLED', 2: 'DISABLED'}, {1: 'READ_ONLY', 2: 'READ_WRITE'}
PROOF_ENTRIES = 20000
CLASSPATH_SYMBOL = (b'AutomatedPackageListener', b'onAutomatedDevicesChanged')
REPORT_ERRORS = 40


class Invalid(Exception):
    pass


class Unbound(Exception):
    pass


class Refused(Exception):
    pass


def contract_module(root=ROOT):
    spec = importlib.util.spec_from_file_location('launcher3_compile_contract', root/'scripts/launcher3-compile-contract.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def git_blob(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def load_manifest(path=MANIFEST, pinned=MANIFEST_SHA256):
    data = Path(path).read_bytes()
    if sha256_bytes(data) != pinned:
        raise Invalid(f'{Path(path).name} is not the reviewed contract version {pinned[:12]}')
    manifest = json.loads(data)
    if (manifest.get('schemaVersion'), manifest.get('kind'), manifest.get('target')) != (1, 'rose-launcher3-compile-inputs', TARGET):
        raise Invalid('the contract is not a rose-launcher3-compile-inputs v1 for Launcher3QuickStepLib')
    flags = (manifest.get('flagEvidence') or {}).get('flags') or []
    if {f.get('flag') for f in flags} != {'android.security.content_uri_permission_apis',
                                          'com.android.launcher3.enable_fallback_overview_in_window'}:
        raise Invalid('the contract must bind exactly the identity and fallback flags in flagEvidence')
    return manifest


def frankel_policy_args(root=ROOT):
    """(reference display address, locked control) exactly as frankel's committed applyArgs give them."""
    config = json.loads((root/'os/release-pipeline/frankel.json').read_text())
    args = config['host']['integrate']['reconcile']['applyArgs']
    if '--background-policy' not in args:
        raise Invalid('frankel applyArgs do not pass --background-policy: the contract post-images need it')
    reference = args[args.index('--reference-display-address') + 1] if '--reference-display-address' in args else None
    return reference, '--locked-control' in args


def lock_path(root=ROOT):
    return json.loads((root/'os/release-pipeline/frankel.json').read_text())['host']['lockPath']


def check(binding_path=BINDING, root=ROOT, manifest_path=MANIFEST):
    """(manifest, binding). Invalid on a wrong shape, Unbound while the selected branch lacks an input."""
    manifest = load_manifest(manifest_path)
    binding = json.loads(Path(binding_path).read_text())
    if set(binding) != BINDING_KEYS or binding['schemaVersion'] != 1 or binding['kind'] != 'rose-launcher3-compile-host':
        raise Invalid('the binding is not an exact rose-launcher3-compile-host v1 record')
    if binding['host'] != 'immortal-os-builder' or binding['lockFrom'] != 'os/release-pipeline/frankel.json host.lockPath':
        raise Invalid('the binding must name the existing builder and its shared lock')
    if binding['taskParent'] != '/home/pixelbuild/rose-actions':
        raise Invalid('the run root parent must be /home/pixelbuild/rose-actions')
    if binding['branch'] not in ('verifyIntegrated', 'reconstruct'):
        raise Invalid('branch must be verifyIntegrated or reconstruct')
    receipt = binding['policyReceipt']
    if not isinstance(receipt, dict) or set(receipt) != {'path', 'sha256'}:
        raise Invalid('policyReceipt must be exactly {path, sha256}')
    for name, value in (('sourceTree', binding['sourceTree']), ('policyReceipt.path', receipt['path'])):
        if value is not None and (not isinstance(value, str) or not value.startswith('/') or os.path.normpath(value) != value):
            raise Invalid(f'{name} must be a normalized absolute path')
    if binding['sourceTree'] is not None:
        source, parent = Path(binding['sourceTree']), Path(binding['taskParent'])
        if parent in [source, *source.parents]:
            raise Invalid('the source tree must not be under the run root parent (no Pro snapshot or earlier run root)')
        if source in parent.parents:
            raise Invalid('the run root parent must not be inside the source tree')
    if receipt['sha256'] is not None and not re.fullmatch(r'[0-9a-f]{64}', str(receipt['sha256'])):
        raise Invalid('policyReceipt.sha256 must be a lowercase sha256')
    if binding['lunch'] is not None and not re.fullmatch(r'[a-z0-9_]+-[a-z0-9]+-(user|userdebug|eng)', str(binding['lunch'])):
        raise Invalid('lunch must be <product>-<release>-<variant>')
    frankel_policy_args(root)
    needed = ['sourceTree', 'lunch']
    if binding['branch'] == 'verifyIntegrated':
        needed += ['policyReceipt.path', 'policyReceipt.sha256']
    else:
        needed += ['reconstructSource']
    values = {'sourceTree': binding['sourceTree'], 'lunch': binding['lunch'], 'policyReceipt.path': receipt['path'],
              'policyReceipt.sha256': receipt['sha256'], 'reconstructSource': binding['reconstructSource']}
    unbound = [n for n in needed if values[n] is None]
    if unbound:
        raise Unbound(f'{binding["branch"]} needs inputs not yet bound in {Path(binding_path).name}: {", ".join(unbound)}')
    if binding['branch'] == 'reconstruct':
        raise Unbound('reconstruct needs an approved genuine-Git-object source; this adapter has none wired')
    return manifest, binding


def read_bytes(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return None
    return path.read_bytes()


def classify(tree, manifest, policy=None, policy_sha256=None, bound_policy_sha256=None, reference=None, locked=False,
             contract=None):
    """{'branch', 'files', 'overlays', 'problems'}: verifyIntegrated only when every contract file is at its post-image
    and every required overlay is present at its sha256; otherwise None with every reason. Reads only; never applies."""
    contract = contract or contract_module()
    tree, files, problems = Path(tree), [], []
    for project in manifest['projects']:
        for entry in project['files']:
            relative = f'{project["path"]}/{entry["path"]}'
            data = read_bytes(tree/relative)
            row = {'path': relative}
            if data is None:
                row['state'] = 'missing'
            elif 'integrationRewrite' in entry:
                errors = contract.verify_integrated_config(data, entry['post']['sha256'], policy, policy_sha256,
                                                           bound_policy_sha256, reference, locked)
                row['state'] = 'post' if not errors else 'other'
                if errors:
                    row['errors'] = errors[:5]
            elif sha256_bytes(data) == entry['post']['sha256'] and git_blob(data) == entry['post']['gitBlob']:
                row['state'] = 'post'
            elif entry['pre'] and sha256_bytes(data) == entry['pre']['sha256'] and git_blob(data) == entry['pre']['gitBlob']:
                row['state'] = 'pre'
            else:
                row['state'] = 'other'
            files.append(row)
    overlays = []
    for overlay in manifest['overlays']:
        if overlay['role'] != 'required':
            continue
        data = read_bytes(tree/overlay['target'])
        state = 'missing' if data is None else 'present' if sha256_bytes(data) == overlay['sha256'] else 'other'
        overlays.append({'path': overlay['target'], 'state': state})
    off = [f for f in files if f['state'] != 'post'] + [o for o in overlays if o['state'] != 'present']
    if not files or not overlays:
        problems.append('the contract names no files or no required overlays')
    for row in off:
        problems.append(f'{row["path"]}: {row["state"]}')
    if off and {f['state'] for f in files} >= {'post', 'pre'}:
        problems.insert(0, 'mixed state: some files are pre-images and some post-images; nothing is reset')
    return {'branch': None if problems else 'verifyIntegrated', 'files': files, 'overlays': overlays,
            'problems': problems}


# ---------------------------------------------------------------- aconfig cache (protobuf, read only)

def varint(data, i):
    value, shift = 0, 0
    while True:
        if i >= len(data) or shift > 63:
            raise ValueError('truncated varint')
        byte = data[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7


def fields(data):
    """[(number, wire type, value)] of one protobuf message (varint, 64-bit, length-delimited, 32-bit)."""
    out, i = [], 0
    while i < len(data):
        key, i = varint(data, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = varint(data, i)
        elif wire == 1:
            value, i = data[i:i + 8], i + 8
        elif wire == 2:
            length, i = varint(data, i)
            if i + length > len(data):
                raise ValueError('truncated field')
            value, i = data[i:i + length], i + length
        elif wire == 5:
            value, i = data[i:i + 4], i + 4
        else:
            raise ValueError(f'unsupported wire type {wire}')
        out.append((number, wire, value))
    return out


def flag_values(cache):
    """{(package, name): (state, permission)} of an aconfig parsed_flags cache."""
    values = {}
    for number, wire, flag in fields(cache):
        if number != 1 or wire != 2:
            continue
        record = {n: v for n, _, v in fields(flag)}
        key = (record.get(1, b'').decode(), record.get(2, b'').decode())
        values[key] = (STATE.get(record.get(6)), PERMISSION.get(record.get(7)))
    return values


def bounded_files(directory, suffixes, limit=PROOF_ENTRIES):
    found, seen = [], 0
    for base, dirs, names in os.walk(directory, followlinks=False):
        for name in sorted(names):
            seen += 1
            if seen > limit:
                return found, True
            if name.endswith(suffixes):
                found.append(Path(base)/name)
    return found, False


def flag_dirs(manifest, out):
    """[(flag evidence row, its module's output directory under this run's OUT_DIR)] from the contract."""
    rows = []
    for row in manifest['flagEvidence']['flags']:
        relative = row['expectedOutputDir']
        if not relative.startswith('out/soong/.intermediates/') or '..' in Path(relative).parts:
            raise Invalid(f'{row["flag"]}: expectedOutputDir must be under out/soong/.intermediates')
        rows.append((row, Path(out)/relative[len('out/'):]))
    return rows


def flag_line(package, name, value):
    state, permission = value
    return f'{package}.{name}:{permission}={"true" if state == "ENABLED" else "false" if state == "DISABLED" else state}'


def flag_report(out, manifest):
    """Each flag: exactly one line in its own module's aconfig-flags.txt equal to the expected line, and the same value
    decoded independently from that module's aconfig-cache.pb. Absent, duplicated, disagreeing or other: not ok."""
    rows = []
    for evidence, directory in flag_dirs(manifest, out):
        row = {'flag': evidence['flag'], 'module': evidence['module'], 'expected': evidence['expectedLine']}
        text_path, cache_path = directory/FLAG_TEXT, directory/FLAG_CACHE
        lines = []
        if text_path.is_file() and not text_path.is_symlink():
            lines = [l for l in text_path.read_text(errors='replace').splitlines() if l.startswith(evidence['flag'] + ':')]
        decoded = None
        if cache_path.is_file() and not cache_path.is_symlink():
            with contextlib.suppress(ValueError, OSError, UnicodeDecodeError):
                value = flag_values(cache_path.read_bytes()).get((evidence['package'], evidence['flag'][len(evidence['package']) + 1:]))
                decoded = flag_line(evidence['package'], evidence['flag'][len(evidence['package']) + 1:], value) if value else None
        row.update(textLines=lines[:3], decodedCache=decoded)
        if len(lines) != 1:
            row.update(ok=False, reason=f'{len(lines)} lines for the flag in {FLAG_TEXT}, expected exactly one')
        elif lines[0] != evidence['expectedLine']:
            row.update(ok=False, reason='the module text differs from the expected line')
        elif decoded != lines[0]:
            row.update(ok=False, reason='the decoded cache does not agree with the module text')
        else:
            row['ok'] = True
        rows.append(row)
    return rows


def source_proof(out, manifest):
    """Every Launcher3 post-image and required Launcher3 overlay appears in the library's Soong file lists (rsp)."""
    launcher = next(p for p in manifest['projects'] if p['path'] == 'packages/apps/Launcher3')
    wanted = [f'{launcher["path"]}/{f["path"]}' for f in launcher['files']]
    wanted += [o['target'] for o in manifest['overlays'] if o['role'] == 'required' and o['target'].startswith(launcher['path'] + '/')]
    lists, truncated = bounded_files(Path(out)/'soong/.intermediates/packages/apps/Launcher3'/TARGET, ('.rsp', '.list'))
    text = ''
    for item in lists:
        with contextlib.suppress(OSError):
            text += item.read_text(errors='replace') + '\n'
    missing = [w for w in wanted if w not in text]
    return {'lists': len(lists), 'expected': len(wanted), 'missing': missing[:REPORT_ERRORS], 'truncated': truncated,
            'ok': bool(lists) and not missing and not truncated}


def classpath_proof(out):
    """A framework jar on the library's classpath holds AutomatedPackageListener declaring onAutomatedDevicesChanged."""
    lists, truncated = bounded_files(Path(out)/'soong/.intermediates/packages/apps/Launcher3'/TARGET, ('.rsp',))
    jars = set()
    for item in lists:
        with contextlib.suppress(OSError):
            jars.update(re.findall(r'(\S*framework\S*\.jar)', item.read_text(errors='replace')))
    for jar in sorted(jars):
        path = Path(jar) if jar.startswith('/') else Path(out).parent/jar
        with contextlib.suppress(OSError, zipfile.BadZipFile):
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if name.endswith(CLASSPATH_SYMBOL[0].decode() + '.class'):
                        if CLASSPATH_SYMBOL[1] in archive.read(name):
                            return {'ok': True, 'jar': jar, 'class': name}
    return {'ok': False, 'jars': len(jars), 'truncated': truncated,
            'reason': 'no classpath jar holds AutomatedPackageListener with onAutomatedDevicesChanged'}


# ---------------------------------------------------------------- bounded processes

def group_rss(pgid, proc=Path('/proc')):
    """Resident bytes of every process in a process group (sampled; may miss short-lived processes)."""
    total, page = 0, os.sysconf('SC_PAGE_SIZE')
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        with contextlib.suppress(OSError, ValueError, IndexError):
            stat = (entry/'stat').read_text()
            if int(stat[stat.rindex(')') + 2:].split()[2]) == pgid:
                total += int((entry/'statm').read_text().split()[1]) * page
    return total


def used_bytes(path):
    fs = os.statvfs(path)
    return (fs.f_blocks - fs.f_bfree) * fs.f_frsize


def supervised(argv, env, cwd, seconds, out_dir, popen=subprocess.Popen, clock=time.monotonic, sleep=time.sleep,
               rss=group_rss, used=used_bytes):
    """(exit, stoppedBy or None). Own session; every SAMPLE_SECONDS the output filesystem's growth and the group's RSS
    are compared with their stop thresholds; a stop or the timeout ends the group with TERM, then KILL."""
    proc = popen(argv, env=env, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    start_used, deadline, stopped = used(out_dir), clock() + seconds, None
    while proc.poll() is None:
        if clock() > deadline:
            stopped = 'timeout'
        elif used(out_dir) - start_used > OUTPUT_STOP_BYTES:
            stopped = 'output'
        elif rss(proc.pid) > MEMORY_STOP_BYTES:
            stopped = 'memory'
        if stopped:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, sig)
                end = clock() + KILL_GRACE_SECONDS
                while proc.poll() is None and clock() < end:
                    sleep(1)
                if proc.poll() is not None:
                    break
            break
        sleep(SAMPLE_SECONDS)
    return proc.wait(), stopped


def contained_env(run_root, path=os.environ.get('PATH', '/usr/bin:/bin')):
    env = {'PATH': path, 'HOME': str(run_root/'home'), 'TMPDIR': str(run_root/'tmp'), 'OUT_DIR': str(run_root/'out'),
           'XDG_CACHE_HOME': str(run_root/'home/.cache'), 'XDG_CONFIG_HOME': str(run_root/'home/.config'),
           'XDG_DATA_HOME': str(run_root/'home/.local/share'), 'GRADLE_USER_HOME': str(run_root/'home/.gradle'),
           'USE_CCACHE': '0', 'LANG': 'C.UTF-8'}
    return env


def compile_argv(lunch, targets):
    script = ('set -eo pipefail; source build/envsetup.sh >/dev/null; lunch "$1" >/dev/null; shift; '
              f'm -j{JOBS} "$@"')
    return ['bash', '-c', script, 'launcher3-compile', lunch, *targets]


def compile_targets(manifest, out):
    """The library plus each flag module's aconfig-flags.txt as an explicit output goal (the tree's own pinned rule
    produces the text; a module invocation alone is not assumed to)."""
    return [TARGET, *sorted(str(directory/FLAG_TEXT) for _, directory in flag_dirs(manifest, out))]


# ---------------------------------------------------------------- run

@contextlib.contextmanager
def shared_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('lock_held: the shared builder lock is held by another job') from None
        yield
    finally:
        os.close(fd)


def write_once(path, document):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w') as f:
        f.write(json.dumps(document, indent=2, sort_keys=True) + '\n')


def task_root(binding, run_id, attempt):
    for label, value in (('run id', run_id), ('attempt', attempt)):
        if not (isinstance(value, str) and value.isdigit() and value[0] != '0' and len(value) <= 20):
            raise Invalid(f'a positive numeric {label} is required')
    return Path(binding['taskParent'])/TASK_NAME.format(run_id=run_id, attempt=attempt)


def copy_tree(source, destination, root=ROOT, statvfs=os.statvfs, planner=alpha_source.plan_copy,
              copier=alpha_source.copy_tree, seconds=PREPARE_SECONDS):
    """The reviewed alpha copy (plan, then alpha_source.copy_tree, which also rebases links) of the selection.
    Refused before writing on plan problems, the source bound or the reserve; a wall-clock timer stops the copy at
    `seconds` (the partial tree stays inside the run root and is reported, never used)."""
    policy = json.loads((root/'os/release-pipeline/cuttlefish-alpha.json').read_text())['preparation']['exclusions']
    plan = planner(source, policy)
    if plan['problems']:
        raise Refused(f'copy_refused: {len(plan["problems"])} problems, first: {plan["problems"][0]}')
    fs = statvfs(destination.parent)
    projected = sum(e.get('size', 0) for e in plan['entries'] if e['type'] == 'file')
    available = fs.f_bavail * fs.f_frsize
    if projected > SOURCE_BOUND_BYTES:
        raise Refused(f'copy_refused: the selection projects {projected} bytes, above {SOURCE_BOUND_BYTES}')
    if available - projected < RESERVE_BYTES:
        raise Refused(f'copy_refused: available {available} minus {projected} is below the {RESERVE_BYTES} reserve')

    def expired(signum, frame):
        raise Refused(f'copy_timeout: the copy exceeded {seconds} s; the partial tree is not used')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        copier(Path(source), destination, destination, plan)
    except alpha_source.PreparationFailed as error:
        raise Refused(f'copy_failed: {error}') from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    return {'entries': len(plan['entries']), 'excluded': len(plan['excluded']), 'projectedBytes': projected,
            'availableBytesBefore': available}


def run(binding, manifest, run_id, attempt, root=ROOT, lock=None, copier=copy_tree, compiler=supervised,
        policy_loader=None):
    """The adapter run; returns its exit status and leaves receipts/report.json naming each step's outcome."""
    run_root = task_root(binding, run_id, attempt)
    if Path(binding['taskParent']).is_symlink() or not Path(binding['taskParent']).is_dir():
        raise Refused('the run root parent must be an existing plain directory')
    run_root.mkdir(mode=0o700)
    receipts = run_root/'receipts'
    receipts.mkdir(mode=0o700)
    reference, locked = frankel_policy_args(root)
    report = {'schema': 'roseos.launcher3-compile-report.v1', 'target': TARGET, 'manifestSha256': MANIFEST_SHA256,
              'candidateSha': os.environ.get('GITHUB_SHA'), 'branchRequested': binding['branch'],
              'lunch': binding['lunch'], 'sourceTree': binding['sourceTree'],
              'policyInputs': {'receiptSha256': binding['policyReceipt']['sha256'], 'referenceDisplayAddress': reference,
                               'lockedControl': locked},
              'bounds': {'sourceBytes': SOURCE_BOUND_BYTES, 'reserveBytes': RESERVE_BYTES, 'prepareSeconds': PREPARE_SECONDS,
                         'compileSeconds': COMPILE_SECONDS, 'outputStopBytes': OUTPUT_STOP_BYTES,
                         'memoryStopBytes': MEMORY_STOP_BYTES, 'sampleSeconds': SAMPLE_SECONDS, 'jobs': JOBS,
                         'kind': 'first-measurement ceilings; sampled stops can overshoot; no hard cap'},
              'proves': manifest['proves'], 'unaffectedProjects': 'not verified by this adapter (Infra provenance)',
              'identitySharing': manifest['identitySharing']}
    status = 3
    try:
        with (lock or shared_lock)(lock_path(root)):
            for name in ('home', 'tmp', 'out'):
                (run_root/name).mkdir(mode=0o700)
            report['copy'] = copier(Path(binding['sourceTree']), run_root/'tree')
            loader = policy_loader or (lambda path: (json.loads(Path(path).read_bytes()), sha256_bytes(Path(path).read_bytes())))
            try:
                policy, policy_sha = loader(binding['policyReceipt']['path'])
            except (OSError, ValueError):
                raise Refused('policy_missing: the bound policy receipt cannot be read') from None
            result = classify(run_root/'tree', manifest, policy, policy_sha, binding['policyReceipt']['sha256'], reference, locked)
            report['classification'] = {'branch': result['branch'], 'problems': result['problems'][:REPORT_ERRORS],
                                        'files': len(result['files']), 'requiredOverlays': len(result['overlays'])}
            write_once(receipts/'classification.json', result)
            if result['branch'] != 'verifyIntegrated':
                raise Refused('state_refused: the copied tree is not exactly the contract post-state; nothing applied')
            out = run_root/'out'
            targets = compile_targets(manifest, out)
            code, stopped = compiler(compile_argv(binding['lunch'], targets), contained_env(run_root),
                                     str(run_root/'tree'), COMPILE_SECONDS, str(out))
            report['compile'] = {'exit': code, 'stoppedBy': stopped, 'targets': [TARGET, *[
                str(Path(t).relative_to(out.parent)) for t in targets[1:]]]}
            report['sourceProof'] = source_proof(out, manifest)
            report['classpathProof'] = classpath_proof(out)
            report['flags'] = flag_report(out, manifest)
            passed = (code == 0 and not stopped and report['sourceProof']['ok'] and report['classpathProof']['ok']
                      and all(f['ok'] for f in report['flags']))
            report['result'] = 'compiled-and-proven' if passed else 'incomplete'
            status = 0
    except Refused as error:
        report['refused'] = str(error)
        report['result'] = 'refused'
    finally:
        write_once(receipts/'report.json', report)
    return status


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('check', 'run'))
    p.add_argument('--run-id')
    p.add_argument('--attempt')
    p.add_argument('--binding', default=str(BINDING), help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    try:
        manifest, binding = check(a.binding)
        if a.command == 'check':
            if a.run_id or a.attempt:
                raise Invalid('check takes no other option')
            prepare = PREPARE_SECONDS + KILL_GRACE_SECONDS
            outer = prepare + COMPILE_SECONDS + KILL_GRACE_SECONDS + 600
            print(f'outer_seconds={outer}\njob_minutes={-(-outer // 60) + 10}')
            return 0
        return run(binding, manifest, a.run_id, a.attempt)
    except Unbound as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'error': str(error)}), file=sys.stderr)
        return 4
    except (Invalid, Refused, FileExistsError, KeyError, ValueError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
