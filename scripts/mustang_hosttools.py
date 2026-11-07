#!/usr/bin/env python3
"""Non-executing Mustang host-tools proposal.

readyForControlledStart stays false and no Make argv is emitted. Caller
declarations are not measurements. The prerequisite readers below hash or
stat a tree; a fixture that passes a reader is not an otatools proof and
does not authorize START.

Factory transfer has to be a regular file contained in the dedicated mount,
with the pinned size and sha256. Adevtool's host tools follow
blazer-build-only.deps_problems: the revision marker is the pin plus one
newline, and each depsTool is a regular executable. Vendor generation is
not shown by an arbitrary file hash; the product makefile must be the
regular file vendor/google_devices/mustang/mustang.mk and include the
adevtool version check. None of those readers consumes or sets the
otatools pin.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

import mustang_route as route

ROOT = Path(__file__).resolve().parents[1]
TARGET = 'otatools-package'
MOUNT = '/mnt/disks/mustang-data'
PRODUCT_MK = Path('vendor/google_devices/mustang/mustang.mk')
VERSION_INCLUDE = b'include vendor/google_devices/mustang/adevtool-version-check.mk'
PRODUCT_MK_BOUND = 1 << 20
LIFECYCLE = (
    'The dedicated VM is stopped. Measuring these prerequisites does not start it. '
    'A later run, after the readers match on that disk, would still need '
    'scripts/mustang_controller.py START, the existing group-5 enroll, and the '
    'existing cleanup STOP. This module does not start, enroll, compile, or stop.'
)


def pins_null(root=ROOT):
    pin = json.loads((Path(root)/'os/build-only/mustang-hosted-sign-script.json').read_text())
    return all(pin.get(name) is None for name in (
        'generateReleaseSha256', 'jdkSha256', 'otatoolsSha256', 'otatoolsExecutable'))


def recipe(root=ROOT):
    return json.loads((Path(root)/'os/build-only/mustang.json').read_text())


def current_gaps(root=ROOT):
    """Gaps in the committed records. There is no caller observation to satisfy them."""
    problems = []
    identity = route.ACCEPTED_SOURCE_SYNC
    record = json.loads((Path(root)/identity['path']).read_text())
    if not route.accepted_source_sync(record, identity['path'], root):
        problems.append('source-receipt-unbound')
    factory = json.loads((Path(root)/'os/build-only/mustang-factory-box-20261107.json').read_text())
    if factory.get('location') == 'box-only' or factory.get('transferred') is not True:
        problems.append('factory-not-on-dedicated-disk')
    if factory.get('extracted') is not True:
        problems.append('factory-not-extracted')
    if factory.get('vendorGenerated') is not True:
        problems.append('vendor-not-generated')
    problems.append('mustang-product-config-absent')
    problems.append('host-dependency-closure-absent')
    problems.append('out-dir-unspecified')
    if not pins_null(root):
        problems.append('production-pin-changed')
    return problems


def decide(root=ROOT):
    """The proposal. Arguments other than the checkout are ignored; none can make it ready."""
    return {
        'schema': 'roseos.mustang-hosttools-contract.v1',
        'kind': 'non-executing-proposal',
        'target': TARGET,
        'execute': False,
        'startAuthorized': False,
        'readyForControlledStart': False,
        'problems': current_gaps(root),
        'argv': None,
        'lifecycle': LIFECYCLE,
        'productionPins': 'null',
        'heldOtatools': '4e13f55b',
    }


def _contained(mount, path):
    """Lexical containment plus a no-symlink walk. abspath drops .. without following links."""
    base = Path(os.path.abspath(mount))
    candidate = Path(os.path.abspath(path))
    if candidate == base or base.parts != candidate.parts[:len(base.parts)]:
        return False
    if base.is_symlink():
        return False
    cursor = base
    for part in candidate.relative_to(base).parts:
        cursor = cursor/part
        if cursor.is_symlink():
            return False
    return True


def measure_factory(mount, path, root=ROOT):
    """Read a factory archive. The expected hash and size come from mustang.json, not the caller."""
    pin = recipe(root)['factory']
    problems = []
    if not _contained(mount, path):
        problems.append('factory-not-contained')
        return {'measured': False, 'matchesPinnedFactory': False, 'admitsOtatools': False, 'problems': problems}
    candidate = Path(path)
    try:
        info = os.lstat(candidate)
    except OSError:
        problems.append('factory-unreadable')
        return {'measured': False, 'matchesPinnedFactory': False, 'admitsOtatools': False, 'problems': problems}
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        problems.append('factory-not-regular')
        return {'measured': False, 'matchesPinnedFactory': False, 'admitsOtatools': False, 'problems': problems}
    if info.st_size != pin['sizeBytes']:
        problems.append('factory-size-differs')
        return {'measured': True, 'matchesPinnedFactory': False, 'admitsOtatools': False, 'problems': problems}
    digest = hashlib.sha256()
    with open(candidate, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    if digest.hexdigest() != pin['sha256']:
        problems.append('factory-hash-differs')
    return {'measured': True, 'matchesPinnedFactory': not problems, 'admitsOtatools': False, 'problems': problems}


def measure_deps(source, root=ROOT):
    """The deps_problems read: pin newline, then each host tool as a regular executable."""
    spec = recipe(root)
    deps = Path(source)/spec['adevtool']['depsDir']
    problems = []
    marker = deps/'adevtool_revision'
    want = (spec['pins']['vendor/adevtool'] + '\n').encode()
    try:
        fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            have = os.read(fd, 128)
        finally:
            os.close(fd)
    except OSError:
        have = None
    if have != want:
        problems.append('adevtool-revision-differs')
    tools = deps/'host/linux-x86/bin'
    missing = []
    for name in spec['adevtool']['depsTools']:
        tool = tools/name
        try:
            info = os.lstat(tool)
        except OSError:
            missing.append(name)
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or not os.access(tool, os.X_OK):
            missing.append(name)
    if missing:
        problems.append('adevtool-host-tools-unmeasured')
    return {'measured': not problems, 'admitsOtatools': False, 'problems': problems,
            'tools': list(spec['adevtool']['depsTools'])}


def measure_vendor(source):
    """Read the product makefile. A hash supplied beside an arbitrary vendor path is not this check."""
    path = Path(source)/PRODUCT_MK
    source = Path(source)
    problems = []
    if not _contained(source, path):
        problems.append('mustang-product-config-absent')
        return {'measured': False, 'admitsVendorTree': False, 'admitsOtatools': False, 'problems': problems}
    try:
        info = os.lstat(path)
    except OSError:
        problems.append('mustang-product-config-absent')
        return {'measured': False, 'admitsVendorTree': False, 'admitsOtatools': False, 'problems': problems}
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > PRODUCT_MK_BOUND or info.st_size <= 0:
        problems.append('mustang-product-config-absent')
        return {'measured': False, 'admitsVendorTree': False, 'admitsOtatools': False, 'problems': problems}
    data = path.read_bytes()
    if VERSION_INCLUDE not in data:
        problems.append('mustang-product-config-absent')
    return {'measured': not problems, 'admitsVendorTree': False, 'admitsOtatools': False, 'problems': problems}


def main(argv=None):
    json.dump(decide(), sys.stdout, sort_keys=True)
    sys.stdout.write('\n')
    return 4


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
