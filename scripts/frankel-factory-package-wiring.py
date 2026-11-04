#!/usr/bin/env python3
"""FRANKEL-FACTORY workflow wiring (Release-owned) for os-release.yml modes frankel-factory-observe and
frankel-factory-package: the Frankel (Pixel 10) initial factory installer frankel-install-<build>.zip, built from the
exact signed target-files of one published Frankel OTA build.

It runs the shared machinery in scripts/factory_package_route.py. That is the same orchestration the Rango route uses;
no Rango or Blazer pin, image or receipt is used here. The helpers are unchanged:
- scripts/host-factory-package.py (Signing; it holds the shared builder lock for its whole run);
- scripts/factory-package.py observe and classify (Flavors);
- scripts/rango-firmware-observe.py --device frankel (Signing; device-generic).

Every path and pin comes from committed sources; no argument chooses one:
- binding: os/factory-package/frankel.json {build, buildTimestamp, candidate, sourceRun,
  signedTargetFilesSha256, otatoolsSha256, fastbootSha256}, all null until their records are supplied;
- signed target-files: /home/pixelbuild/rose-actions/frankel-<build>/release/signed-target_files.zip, the original
  attempt's own signed output;
- otatools: <host.sourceRoot>/releases/<build>/frankel-otatools.zip, the copy scripts/host-sign.py made of the build's
  own otatools when it signed that build (never the shared OUT_DIR copy, which a later compile replaces); the owner and
  signed markers host-sign.py wrote in the same directory must name the bound candidate, build and signed target-files;
- release script: host.sourceRoot/script/generate-release.sh, pinned by os/flavors/frankel.json
  provenance.releaseScript;
- AVB public key: derived from the signed target-files' own vbmeta by the helper and pinned through the flavor's
  avbPublicKeySha256From (os/release-pipeline/frankel.json identities.avbPublicKeySha256).

Commands:
  check --stage observe|package      hosted and on the builder: the binding is exact and every derived pin agrees
                                     with its committed source; exit 4 while a value that stage needs is null; prints
                                     the stage's step and job bounds. Reads no host path.
  observe-inputs --run-id --attempt --out NEW_DIR
                                     mode frankel-factory-observe; read-only, under the shared builder lock (an existing
                                     regular file: never created, never waited on). Records, without changing
                                     anything:
                                     - the original attempt: its receipt chain through transfer (not a resume), its
                                       sign receipt and its passing, non-simulated verification receipt, each naming
                                       the bound candidate, build, timestamp and signed target-files sha256;
                                     - that file's own sha256, and the AVB key the package helper derives from
                                       it (its own derive_avb_pkmd) against the pin;
                                     - the build's release directory markers (owner and signed) against the binding;
                                     - the otatools ZIP's sha256 and size, its bin/fastboot member's sha256, its
                                       device/common generator members against the flavor's generatorFixture pins,
                                       and the helper's extraction refusals (member count, unsafe names, links);
                                     - the release script, and the one the sign receipt records, against its pin;
                                     - the free bytes the helper's 128 GiB precondition needs.
                                     Only NEW_DIR/frankel-factory-inputs.json is written. Exit 0 when every check holds,
                                     3 when the report lists a problem.
  package | observe | firmware-observe | classify | stage-installer | report
                                     mode frankel-factory-package: the Rango route's steps on this route (see
                                     scripts/factory_package_route.py and scripts/rango-factory-package-wiring.py).

Unsigned intermediate: no step adds an archive signature. The package and firmware attempt records, the staged
record, the cleanup report and the observation receipt carry LABEL; the private installer artifact's name says it too
(the ZIP inside keeps the flavor's install-archive name). os/flavors/frankel.json factorySigning stays unbound, and
classify reports signatureVerified missing: the archive is not factory-ready and is not the delivered installer.

RETAINED and SIGNATURE are the Frankel installer pins for Signing's delivery verification. On main nothing reads them
yet: Signing's open #515 adds scripts/factory_targets.py with frankel installerPins null, and its follow-up points
those at this module once this is merged. They stay null until a package run's receipts, and a signature from a
separately approved Frankel archive identity, are bound by reviewed commits. An unsigned or unpinned archive is never
verified as delivered.

observe-inputs also does not verify NOT_VERIFIED (sourceRun and publication), and says so in its receipt.

Bounds are wrapper ceilings, not measurements. Exit status: 0 ok, 2 invalid, 3 refused or incomplete, 4 unbound.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import factory_package_route as route_lib  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEVICE = 'frankel'
BINDING = 'os/factory-package/frankel.json'
BINDING_KIND = 'roseos.frankel-factory-package'
KEYS = {'schemaVersion', 'kind', 'basis', 'build', 'buildTimestamp', 'candidate', 'sourceRun', 'signedTargetFilesSha256',
        'otatoolsSha256', 'fastbootSha256'}
SHAPES = {'build': re.compile(r'[0-9]{10}'), 'buildTimestamp': re.compile(r'[1-9][0-9]{9}'),
          'candidate': re.compile(r'[0-9a-f]{40}'), 'sourceRun': re.compile(r'[1-9][0-9]{0,19}'),
          'signedTargetFilesSha256': re.compile(r'[0-9a-f]{64}'), 'otatoolsSha256': re.compile(r'[0-9a-f]{64}'),
          'fastbootSha256': re.compile(r'[0-9a-f]{64}')}
NEEDS = {'observe': ('build', 'buildTimestamp', 'candidate', 'sourceRun', 'signedTargetFilesSha256'),
         'package': ('build', 'buildTimestamp', 'candidate', 'sourceRun', 'signedTargetFilesSha256', 'otatoolsSha256',
                     'fastbootSha256')}
ATTEMPT_PARENT = '/home/pixelbuild/rose-actions'  # the os-release builder's $HOME/rose-actions/<device>-<build>/
LABEL = ('unsigned intermediate: this route adds no archive signature (signatureVerified missing); not factory-ready and '
         'not the delivered installer')
OBSERVE_SCHEMA = 'roseos.frankel-factory-inputs.v1'
# What observe-inputs does not establish; the reviewer of the binding commit checks these against the published release
# (pixel-ota tag frankel-<build>, whose body names the run that verified and published it).
NOT_VERIFIED = ('sourceRun: recorded provenance; no run is read here',
                'publication: the public release is not read here; the attempt proves signing, verification and transfer')
OBSERVE_FILE = 'frankel-factory-inputs.json'
GENERATOR_MEMBERS = 'device/common/'
# scripts/host-sign.py prepare_release_dir/main: <sourceRoot>/releases/<build> is made when that build is signed; it holds
# the otatools copy the signing used, an owner marker {build, candidate, device} and, after success, the signed marker.
OTATOOLS_COPY = '{device}-otatools.zip'
OWNER_MARKER = '.rose-actions-owner.json'
SIGNED_MARKER = '.rose-actions-signed.json'
FASTBOOT_MEMBER = 'bin/fastboot'
# otatoolsEntries is scripts/host-factory-package.py LIMITS['otatoolsMembers'] (a test keeps them equal).
LIMITS = {'seconds': 900, 'otatoolsEntries': 50000, 'fastbootBytes': 128 << 20, 'generatorBytes': 1 << 20,
          'receiptBytes': 1 << 20}
FREE_BYTES = 128 << 30  # scripts/host-factory-package.py LIMITS['freeBytes'], recorded here before any package run
# Wrapper ceilings (seconds), not measurements: the Rango route's approved per-step ceilings for the same helpers, and
# the observation's own bound (one target-files and one otatools hash, plus small members, under 900 s).
PACKAGE_BOUNDS = {'packageSeconds': 5100, 'observeSeconds': 600, 'firmwareSeconds': 570, 'classifySeconds': 7200,
                  'stageSeconds': 900}
OBSERVE_BOUNDS = {'observeInputsSeconds': LIMITS['seconds'] + 120}
INSTALLER = {'capBytes': 8 << 30, 'retentionDays': 3, 'uploadMinutes': 45}
KILL_AFTER_SECONDS = 30
JOB_MARGIN_MINUTES = 10
# The Frankel installer pins for Signing's delivery verification (#515's scripts/factory_targets.py 'frankel'
# installerPins will point here): the exact key sets of scripts/rango-factory-inspect-wiring.py, all null until a
# package run's receipts (and, for the signature, a separately approved Frankel archive identity) are bound here by a
# reviewed commit. Never guessed.
RETAINED = {'runId': None, 'attempt': None, 'bytes': None, 'sha256': None}
SIGNATURE = {'runId': None, 'attempt': None, 'sha256': None}

Invalid = route_lib.Invalid


class Unbound(Exception):
    pass


def _json(root, relative):
    value = json.loads((Path(root)/relative).read_text())
    if not isinstance(value, dict):
        raise Invalid(f'{relative} is not a JSON object')
    return value


def load_binding(root=ROOT):
    """The committed binding, exact: every value null or of its shape (a null is unbound, never a guess)."""
    def pairs(items):
        if len({key for key, _ in items}) != len(items):
            raise Invalid(f'{BINDING} repeats a key')
        return dict(items)
    binding = json.loads((Path(root)/BINDING).read_text(), object_pairs_hook=pairs)
    if (not isinstance(binding, dict) or set(binding) != KEYS or type(binding['schemaVersion']) is not int
            or binding['schemaVersion'] != 1 or binding['kind'] != BINDING_KIND):
        raise Invalid(f'{BINDING} is not exactly a {BINDING_KIND} binding')
    for key, shape in SHAPES.items():
        value = binding[key]
        if value is not None and not (isinstance(value, str) and shape.fullmatch(value)):
            raise Invalid(f'{BINDING} {key} must be null or {shape.pattern}')
    return binding


def sources(root=ROOT):
    """The committed Frankel sources every derived pin comes from (none is a binding value)."""
    pipeline, flavor = _json(root, 'os/release-pipeline/frankel.json'), _json(root, 'os/flavors/frankel.json')
    host = pipeline['host']
    production = host['sourceRoot']
    if not (isinstance(production, str) and production.startswith('/') and os.path.normpath(production) == production):
        raise Invalid('os/release-pipeline/frankel.json host.sourceRoot must be a normalized absolute path')
    script = flavor['provenance']['releaseScript']['generate-release.sh']
    generator = dict(flavor['inventory']['generatorFixture']['files'])
    for name, value in (('releaseScript', script), *((f'generator {n}', v) for n, v in generator.items())):
        if not (isinstance(value, str) and SHAPES['otatoolsSha256'].fullmatch(value)):
            raise Invalid(f'os/flavors/frankel.json {name} pin must be a sha256')
    if Path(route_lib.TASK_PARENT) in Path(production).parents or Path(production) in Path(route_lib.TASK_PARENT).parents \
            or Path(production) == Path(route_lib.TASK_PARENT):
        raise Invalid('the task parent must not overlap the production tree')
    return {'production': production, 'releaseScript': {'path': f'{production}/script/generate-release.sh', 'sha256': script},
            'generator': generator, 'lock': host['lockPath'], 'avbPublicKeySha256': pipeline['identities']['avbPublicKeySha256']}


def attempt_dir(build):
    return Path(ATTEMPT_PARENT)/f'{DEVICE}-{build}'


def release_dir(production, build):
    """The build's own signing release directory (scripts/host-sign.py prepare_release_dir)."""
    return Path(production)/'releases'/build


def otatools_path(production, build):
    return release_dir(production, build)/OTATOOLS_COPY.format(device=DEVICE)


def unbound(binding, stage):
    return [key for key in NEEDS[stage] if binding[key] is None]


def route(root=ROOT, binding=None):
    """The bound package route (every NEEDS['package'] value bound, else Unbound)."""
    binding = binding or load_binding(root)
    missing = unbound(binding, 'package')
    if missing:
        raise Unbound(f'{BINDING} is unbound: {", ".join(missing)}')
    src = sources(root)
    pins = {'targetFiles': {'path': str(attempt_dir(binding['build'])/'release/signed-target_files.zip'),
                            'sha256': binding['signedTargetFilesSha256']},
            'otatools': {'path': str(otatools_path(src['production'], binding['build'])), 'sha256': binding['otatoolsSha256']},
            'releaseScript': dict(src['releaseScript']), 'fastbootSha256': binding['fastbootSha256']}
    return route_lib.Route(DEVICE, binding['build'], pins, installer=INSTALLER, label=LABEL)


def bounds(stage):
    if stage == 'observe':
        total = OBSERVE_BOUNDS['observeInputsSeconds'] + KILL_AFTER_SECONDS
        return dict(OBSERVE_BOUNDS), math.ceil(total / 60) + JOB_MARGIN_MINUTES
    total = sum(PACKAGE_BOUNDS.values()) + KILL_AFTER_SECONDS * len(PACKAGE_BOUNDS) + INSTALLER['uploadMinutes'] * 60
    return dict(PACKAGE_BOUNDS), math.ceil(total / 60) + JOB_MARGIN_MINUTES


def check(stage, root=ROOT):
    """(bounds, job_minutes) when the binding and derived pins agree and every value the stage needs is bound."""
    if stage not in NEEDS:
        raise Invalid('--stage must be observe or package')
    binding = load_binding(root)
    sources(root)
    missing = unbound(binding, stage)
    if missing:
        raise Unbound(f'{BINDING} is unbound for {stage}: {", ".join(missing)}')
    if stage == 'package':
        route(root, binding)
    return bounds(stage)


# ---- frankel-factory-observe: read-only, under the shared builder lock

@contextmanager
def held_lock(path):
    """The shared builder lock: an existing regular file (never created), taken non-blocking (never waited on)."""
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        raise Invalid('lock_absent: the shared builder lock does not exist') from None
    if not stat.S_ISREG(before.st_mode):
        raise Invalid('lock_unusable: the shared builder lock is not a regular file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != (before.st_dev, before.st_ino):
            raise Invalid('lock_unusable: the shared builder lock changed while it was opened')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Invalid('lock_held: the shared builder lock is held by another job') from None
        yield
    finally:
        os.close(fd)


class Expired(Exception):
    """The observation's own time bound passed. Not an OSError (TimeoutError is one), so no section handler can take it
    for a read failure and carry on reading."""


class Deadline:
    def __init__(self, seconds, clock=time.monotonic):
        self.clock, self.end = clock, clock() + seconds

    def check(self):
        if self.clock() > self.end:
            raise Expired('the observation exceeded its time bound')


def regular(path):
    """lstat of a regular file reached with no link anywhere in its path; ValueError otherwise."""
    path = Path(path)
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f'{path} is reached through a link')
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f'{path} is not a regular file')
    return info


def opened(path):
    """(binary file object, lstat) of the regular file PATH names, reached with no link and opened without following
    one; ValueError when the opened file is not the one lstat saw."""
    before = regular(path)
    f = os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb')
    now = os.fstat(f.fileno())
    if (now.st_dev, now.st_ino) != (before.st_dev, before.st_ino):
        f.close()
        raise ValueError(f'{path} changed while it was opened')
    return f, before


def unchanged(f, before, path):
    """ValueError unless PATH still names the open file and that file kept its size, mtime and ctime. The name is
    checked first: replacing a file also changes the old file's ctime, and is reported as the replacement it is."""
    after = os.fstat(f.fileno())
    try:
        now = os.lstat(path)
    except FileNotFoundError:
        raise ValueError(f'{path} was removed while it was read') from None
    if (now.st_dev, now.st_ino) != (after.st_dev, after.st_ino):
        raise ValueError(f'{path} was replaced while it was read')
    if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (before.st_size, before.st_mtime_ns, before.st_ctime_ns):
        raise ValueError(f'{path} changed while it was read')


def hashed(f, deadline):
    digest = hashlib.sha256()
    while True:
        deadline.check()
        block = f.read(8 << 20)
        if not block:
            return digest.hexdigest()
        digest.update(block)


def file_sha256(path, deadline):
    f, before = opened(path)
    with f:
        digest = hashed(f, deadline)
        unchanged(f, before, path)
    return digest, before.st_size


def small_json(path):
    f, before = opened(path)
    with f:
        data = f.read(LIMITS['receiptBytes'] + 1)
        unchanged(f, before, path)
    if len(data) > LIMITS['receiptBytes']:
        raise ValueError(f'{Path(path).name} is larger than {LIMITS["receiptBytes"]} bytes')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError(f'{Path(path).name} is not a JSON object')
    return value


def otatools_observation(path, generator_pins, deadline):
    """The otatools ZIP's sha256 and size and its members (otatools_members), all read through one descriptor of one
    regular file that kept its size, mtime and ctime and was not replaced, so the member hashes are of the hashed bytes."""
    f, before = opened(path)
    with f:
        digest = hashed(f, deadline)
        f.seek(0)
        members, problems = otatools_members(f, generator_pins, deadline)
        unchanged(f, before, path)
    return digest, before.st_size, members, problems


def target_files_observation(path, helper, avb_pin, deadline):
    """The signed target-files' sha256 and size, and the helper's own AVB derivation (scripts/host-factory-package.py
    derive_avb_pkmd: exactly IMAGES/vbmeta.img, bounded) against the pin, read through one descriptor as above."""
    f, before = opened(path)
    with f:
        digest = hashed(f, deadline)
        f.seek(0)
        try:
            public, provenance = helper.derive_avb_pkmd(f, avb_pin)
            avb = dict(provenance, publicKeySha256=hashlib.sha256(public).hexdigest(), matchesPin=True)
        except helper.Refused as error:
            avb = {'matchesPin': False, 'refused': str(error)[:300]}
        unchanged(f, before, path)
    return digest, before.st_size, avb


def otatools_members(source, generator_pins, deadline):
    """sha256 and size of bin/fastboot and of each pinned device/common generator member, read from the ZIP's own
    central directory: stored or deflated, unencrypted, unique names, each within its cap. ValueError otherwise."""
    found, problems = {}, []
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > LIMITS['otatoolsEntries']:
            raise ValueError(f'otatools has more than {LIMITS["otatoolsEntries"]} entries')
        names = [info.filename for info in infos]
        unsafe = [info.filename for info in infos if info.filename.startswith('/') or '\\' in info.filename
                  or '..' in info.filename.split('/') or stat.S_ISLNK(info.external_attr >> 16)]
        if unsafe:  # scripts/host-factory-package.py unzip_otatools refuses these (otatools_unsafe)
            problems.append(f'otatools has {len(unsafe)} member(s) the package helper refuses to extract, first '
                            f'{unsafe[0][:120]!r}')
        wanted = {FASTBOOT_MEMBER: LIMITS['fastbootBytes'],
                  **{GENERATOR_MEMBERS + name: LIMITS['generatorBytes'] for name in generator_pins}}
        for name, cap in wanted.items():
            matches = [info for info in infos if info.filename == name]
            if len(matches) != 1:
                problems.append(f'otatools {name}: {len(matches)} entries (exactly one is required)')
                continue
            info = matches[0]
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or info.flag_bits & 0x1 \
                    or stat.S_ISLNK(info.external_attr >> 16) or info.file_size > cap:
                problems.append(f'otatools {name}: not a plain stored or deflated member within {cap} bytes')
                continue
            digest, size = hashlib.sha256(), 0
            with archive.open(info) as member:
                while True:
                    deadline.check()
                    block = member.read(1 << 20)
                    if not block:
                        break
                    size += len(block)
                    if size > cap:
                        raise ValueError(f'otatools {name} exceeds {cap} bytes')
                    digest.update(block)
            found[name] = {'sha256': digest.hexdigest(), 'size': size}
        if len(set(names)) != len(names):
            problems.append('otatools has duplicate member names')
    for name, pin in generator_pins.items():
        row = found.get(GENERATOR_MEMBERS + name)
        if row is not None and row['sha256'] != pin:
            problems.append(f'otatools {GENERATOR_MEMBERS}{name} is not the pinned generator fixture ({pin[:12]}...)')
    return found, problems


def observe_inputs(out_dir, run_id, attempt, root=ROOT, attempt_parent=ATTEMPT_PARENT, lock=None, clock=time.monotonic,
                   free=None):
    """Write NEW/frankel-factory-inputs.json (write-once) and return its exit status (0 every check holds, 3 not)."""
    numeric(run_id, attempt)
    binding = load_binding(root)
    missing = unbound(binding, 'observe')
    if missing:
        raise Unbound(f'{BINDING} is unbound for observe: {", ".join(missing)}')
    src = sources(root)
    out_dir = Path(out_dir)
    route_lib.new_dir(out_dir)
    build, sha = binding['build'], binding['candidate']
    base = Path(attempt_parent)/f'{DEVICE}-{build}'
    report = {'schema': OBSERVE_SCHEMA, 'device': DEVICE, 'build': build, 'candidate': sha, 'sourceRun': binding['sourceRun'],
              'buildTimestamp': binding['buildTimestamp'], 'label': LABEL, 'limits': dict(LIMITS), 'problems': [],
              'source': f'os-release frankel-factory-observe run {run_id} attempt {attempt}',
              'claims': 'read-only measurements of the inputs a frankel-factory-package run would use; nothing packaged',
              'notVerified': list(NOT_VERIFIED),
              'binding': {'path': BINDING, 'sha256': hashlib.sha256((Path(root)/BINDING).read_bytes()).hexdigest()}}
    deadline = Deadline(LIMITS['seconds'], clock)
    helper = _module('host_factory_package', ROOT/'scripts/host-factory-package.py')  # its own AVB derivation
    try:
        with held_lock(lock or src['lock']):
            report['lock'] = {'held': True}
            observe_attempt(report, base, binding, src, deadline, helper)
            observe_release(report, src, binding)
            observe_tools(report, src, binding, deadline)
            stats = (free or os.statvfs)(route_lib.TASK_PARENT)
            available = stats.f_bavail * stats.f_frsize
            report['freeBytes'] = {'path': route_lib.TASK_PARENT, 'available': available, 'required': FREE_BYTES}
            if available < FREE_BYTES:
                report['problems'].append(f'capacity: {available} bytes free under {route_lib.TASK_PARENT}, below {FREE_BYTES}')
    except Invalid as error:  # the lock was absent, unusable or held: nothing was read
        report['lock'] = {'held': False, 'reason': str(error)}
        report['problems'].append(str(error))
    except Expired as error:  # the observation stops here; what it measured so far stays in the report
        report['problems'].append(f'deadline: {error}')
    report['complete'] = not report['problems']
    fd = os.open(out_dir/OBSERVE_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w') as f:
        f.write(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return 0 if report['complete'] else 3


def observe_attempt(report, base, binding, src, deadline, helper):
    """The original attempt's chain, receipts and signed target-files (with its AVB key), each against the binding."""
    build, sha, stamp, pin = binding['build'], binding['candidate'], binding['buildTimestamp'], binding['signedTargetFilesSha256']
    problems = report['problems']
    reuse = _module('host_app_reuse', ROOT/'scripts/host-app-reuse.py')  # code from this checkout; root is data only
    try:
        reuse.attempt_chain(base, sha, build, DEVICE)
        report['attempt'] = {'path': str(base), 'chain': 'original attempt, complete through transfer'}
    except (reuse.AdapterFailed, OSError, ValueError, KeyError, TypeError) as error:
        report['attempt'] = {'path': str(base), 'chain': None}
        problems.append(f'attempt: {str(error)[:300]}')
    release = base/'release'
    try:
        signing = small_json(release/'sign-receipt.json')
        named = (signing.get('receiptType'), signing.get('candidate'), signing.get('build'), signing.get('signedTargetFilesSha256'))
        script = signing.get('releaseScriptSha256')
        report['signReceipt'] = {'matches': named == ('os-signing', sha, build, pin),
                                 'releaseScriptMatchesPin': script == src['releaseScript']['sha256']}
        if not report['signReceipt']['matches']:
            problems.append('signReceipt: sign-receipt.json does not name this candidate, build and signed target-files')
        if not report['signReceipt']['releaseScriptMatchesPin']:
            problems.append('signReceipt: the build was not signed with the os/flavors/frankel.json provenance release script')
    except (OSError, ValueError) as error:
        report['signReceipt'] = {'matches': False}
        problems.append(f'signReceipt: {str(error)[:300]}')
    try:
        verification = small_json(release/'public/verification.json')
        candidate = verification.get('candidate') or {}
        named = (verification.get('receiptType'), verification.get('simulated'), verification.get('signedTargetFilesSha256'),
                 candidate.get('sha'), candidate.get('build'), str(candidate.get('timestamp')))
        report['verification'] = {'matches': named == ('candidate-verification', False, pin, sha, build, stamp)}
        if not report['verification']['matches']:
            problems.append('verification: no passing, non-simulated verification receipt for this candidate, build, '
                            'timestamp and signed target-files')
    except (OSError, ValueError) as error:
        report['verification'] = {'matches': False}
        problems.append(f'verification: {str(error)[:300]}')
    target = release/'signed-target_files.zip'
    try:
        digest, size, avb = target_files_observation(target, helper, src['avbPublicKeySha256'], deadline)
        report['signedTargetFiles'] = {'path': str(target), 'sha256': digest, 'bytes': size, 'matchesBinding': digest == pin}
        report['avbKey'] = dict(avb, pin=src['avbPublicKeySha256'])
        if digest != pin:
            problems.append('signedTargetFiles: signed-target_files.zip is not the bound signedTargetFilesSha256')
        if not avb['matchesPin']:
            problems.append(f'avbKey: {avb["refused"]}')
    except (OSError, ValueError) as error:
        report['signedTargetFiles'] = {'path': str(target), 'sha256': None}
        problems.append(f'signedTargetFiles: {str(error)[:300]}')


def observe_release(report, src, binding):
    """The build's signing release directory: host-sign.py's owner marker and its signed marker, against the binding."""
    build, sha, pin = binding['build'], binding['candidate'], binding['signedTargetFilesSha256']
    directory = release_dir(src['production'], build)
    report['releaseDir'] = {'path': str(directory)}
    for key, name, check in (
            ('owner', OWNER_MARKER, lambda m: m == {'build': build, 'candidate': sha, 'device': DEVICE}),
            ('signed', SIGNED_MARKER, lambda m: (m.get('candidate'), m.get('build'), m.get('signedTargetFilesSha256'))
             == (sha, build, pin))):
        try:
            report['releaseDir'][key] = {'matches': check(small_json(directory/name))}
            if not report['releaseDir'][key]['matches']:
                report['problems'].append(f'releaseDir: {name} does not name this candidate, build and signed target-files')
        except (OSError, ValueError) as error:
            report['releaseDir'][key] = {'matches': False}
            report['problems'].append(f'releaseDir: {str(error)[:300]}')


def observe_tools(report, src, binding, deadline):
    """The build's otatools copy (sha256, size, fastboot and generator members) and the release script, against their
    pins."""
    problems = report['problems']
    path = str(otatools_path(src['production'], binding['build']))
    try:
        digest, size, members, member_problems = otatools_observation(path, src['generator'], deadline)
        fastboot = members.get(FASTBOOT_MEMBER)
        measured = {'otatoolsSha256': digest, 'fastbootSha256': fastboot['sha256'] if fastboot else None}
        bound = {key: binding[key] for key in measured}
        report['otatools'] = {'path': path, 'sha256': digest, 'bytes': size, 'members': members,
                              'fastbootSha256': measured['fastbootSha256'], 'bound': bound,
                              # null: nothing bound yet, so nothing was compared (never reported as a match)
                              'matchesBinding': {key: None if bound[key] is None else bound[key] == measured[key]
                                                 for key in measured}}
        problems.extend(member_problems)
        for key, ok in report['otatools']['matchesBinding'].items():
            if ok is False:
                problems.append(f'otatools: the measured value differs from the bound {key}')
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        report['otatools'] = {'path': path, 'sha256': None}
        problems.append(f'otatools: {str(error)[:300]}')
    try:
        digest, size = file_sha256(src['releaseScript']['path'], deadline)
        report['releaseScript'] = {'path': src['releaseScript']['path'], 'sha256': digest, 'bytes': size,
                                   'matchesPin': digest == src['releaseScript']['sha256']}
        if digest != src['releaseScript']['sha256']:
            problems.append('releaseScript: generate-release.sh is not the os/flavors/frankel.json provenance pin')
    except (OSError, ValueError) as error:
        report['releaseScript'] = {'path': src['releaseScript']['path'], 'sha256': None}
        problems.append(f'releaseScript: {str(error)[:300]}')


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def numeric(run_id, attempt):
    for label, value in (('run id', run_id), ('attempt', attempt)):
        if not (isinstance(value, str) and value.isdigit() and value[0] != '0' and len(value) <= 20):
            raise Invalid(f'a positive numeric {label} is required')


# ---- frankel-factory-package: the shared route's steps on the bound Frankel route

def task_root(run_id, attempt, root=ROOT):
    return route_lib.task_root(route(root), run_id, attempt)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('check', 'observe-inputs', 'package', 'observe', 'firmware-observe', 'classify',
                                       'stage-installer', 'report'))
    p.add_argument('--stage', choices=('observe', 'package'))
    p.add_argument('--run-id')
    p.add_argument('--attempt')
    p.add_argument('--out')
    p.add_argument('--upload-dir')
    p.add_argument('--installer-upload', choices=('success', 'failure', 'cancelled', 'skipped'))
    p.add_argument('--attempt-record')
    a = p.parse_args(argv)
    try:
        if a.command == 'check':
            if not a.stage or a.run_id or a.attempt or a.out or a.upload_dir or a.attempt_record:
                raise Invalid('check takes exactly --stage observe|package')
            stage_bounds, minutes = check(a.stage)
            names = {'observeInputsSeconds': 'observe_inputs_seconds', 'packageSeconds': 'package_seconds',
                     'observeSeconds': 'observe_seconds', 'firmwareSeconds': 'firmware_seconds',
                     'classifySeconds': 'classify_seconds', 'stageSeconds': 'stage_seconds'}
            print('\n'.join([*(f'{names[k]}={v}' for k, v in stage_bounds.items()), f'job_minutes={minutes}']))
            return 0
        if a.stage:
            raise Invalid('--stage is for check only')
        if a.command == 'observe-inputs':
            if not a.out or a.upload_dir or a.attempt_record or a.installer_upload:
                raise Invalid('observe-inputs takes --run-id, --attempt and --out only')
            return observe_inputs(a.out, a.run_id, a.attempt)
        bound = route()
        root_dir = route_lib.task_root(bound, a.run_id, a.attempt)
        if a.out:
            raise Invalid('--out is for observe-inputs only')
        if (a.command == 'report') != bool(a.upload_dir) or (a.installer_upload and a.command != 'report'):
            raise Invalid('--upload-dir and --installer-upload are for report only, and report needs --upload-dir')
        if a.attempt_record and a.command not in ('package', 'report') or (a.command == 'package' and not a.attempt_record):
            raise Invalid('--attempt-record is for package (required) and report only')
        record = route_lib.attempt_record_path(bound, a.attempt_record, a.run_id, a.attempt) if a.attempt_record else None
        if a.command == 'package':
            return route_lib.package(bound, root_dir, record)
        if a.command == 'observe':
            route_lib.packaged(bound, root_dir)
            route_lib.new_dir(root_dir/'inspection')
            return subprocess.run(route_lib.observe_argv(bound, root_dir, a.run_id, a.attempt)).returncode
        if a.command == 'firmware-observe':
            return route_lib.firmware_observe(bound, root_dir, a.run_id, a.attempt)
        if a.command == 'classify':
            return route_lib.classify(bound, root_dir)
        if a.command == 'stage-installer':
            staged = route_lib.stage_installer(bound, root_dir)
            print(json.dumps(staged, sort_keys=True), file=sys.stderr)
            print(f'root={root_dir}\nfile={staged["file"]}')  # the ZIP's name carries the bound build
            return 0
        print(json.dumps(route_lib.report(bound, root_dir, a.upload_dir, a.installer_upload, record), sort_keys=True))
        return 0
    except Unbound as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'error': str(error)}), file=sys.stderr)
        return 4
    except (Invalid, FileExistsError, KeyError, ValueError, TypeError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)[:500]}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
