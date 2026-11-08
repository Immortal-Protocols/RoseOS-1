#!/usr/bin/env python3
"""Non-executing Mustang host-tools proposal.

readyForControlledStart stays false and no Make argv is emitted. Caller
declarations are not measurements. A fixture that passes a reader is not an
otatools proof and does not authorize START.

Every read rejects '.' and '..' before any normalization, then opens that
same path from directory descriptors with O_NOFOLLOW. The final file is
O_NONBLOCK and must be a regular file; a FIFO cannot block the read. The
bytes come from that descriptor only.

Factory size and sha256 come from mustang.json. The adevtool revision marker
must be the pin plus one newline. Opening the fourteen host-tool paths as
regular files is not provenance of those binaries, and the product makefile's
version-check include is not a vendor-tree proof. None of this consumes or
sets the otatools pin.

The fourteen tools are built by adevtool scripts/run-build.sh as
sdk_phone64_x86_64 into out_adevtool_deps, before generate-all writes
vendor/google_devices/mustang. Mustang's vendor stage sets
ADEVTOOL_SKIP_DEP_BUILD=1 and does not run that build.
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
PRODUCT_MK = Path('vendor/google_devices/mustang/mustang.mk')
VERSION_INCLUDE = b'include vendor/google_devices/mustang/adevtool-version-check.mk'
PRODUCT_MK_BOUND = 1 << 20
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
LIFECYCLE = (
    'The dedicated VM is stopped. Measuring these prerequisites does not start it. '
    'A later run, after the readers match on that disk, would still need '
    'scripts/mustang_controller.py START, the existing group-5 enroll, and the '
    'existing cleanup STOP. This module does not start, enroll, compile, or stop.'
)


class _Refused(Exception):
    def __init__(self, code='not-contained'):
        self.code = code


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


def _body(path):
    """Absolute components. '.' and '..' are rejected before normalization."""
    text = os.fspath(path)
    if not isinstance(text, str) or not text.startswith('/') or '\x00' in text:
        return None
    parts = text.split('/')
    if parts[0] != '' or any(part in ('', '.', '..') for part in parts[1:]):
        return None
    return parts[1:]


def _relative(root, path):
    root_body, path_body = _body(root), _body(path)
    if not root_body or not path_body or path_body[:len(root_body)] != root_body or len(path_body) == len(root_body):
        return None
    return path_body[len(root_body):]


def _close(fd):
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def _walk_dir(parts):
    fd = None
    try:
        fd = os.open('/', _DIR_FLAGS)
        for part in parts:
            nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise _Refused()
        owned, fd = fd, None
        return owned
    except OSError as error:
        raise _Refused() from error
    finally:
        _close(fd)


def _open_regular(root, path):
    """Fd of one regular file reached with no symlink component. Caller closes it."""
    rel = _relative(root, path)
    root_body = _body(root)
    if rel is None or not root_body:
        raise _Refused()
    dir_fd = file_fd = None
    try:
        dir_fd = _walk_dir(root_body)
        for part in rel[:-1]:
            nxt = os.open(part, _DIR_FLAGS, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = nxt
        file_fd = os.open(rel[-1], _FILE_FLAGS, dir_fd=dir_fd)
        os.close(dir_fd)
        dir_fd = None
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size < 0:
            raise _Refused('not-regular')
        owned, file_fd = file_fd, None
        return owned, info
    except OSError as error:
        raise _Refused() from error
    finally:
        _close(dir_fd)
        _close(file_fd)


def read_regular(root, path, limit):
    """Bytes of one regular file. This descriptor is the only object read."""
    fd = None
    try:
        fd, info = _open_regular(root, path)
        if info.st_size > limit:
            raise _Refused('over-bound')
        remaining, chunks = info.st_size, []
        while remaining:
            block = os.read(fd, min(1 << 20, remaining))
            if not block or len(block) > remaining:
                raise _Refused('not-regular')
            chunks.append(block)
            remaining -= len(block)
        if remaining:
            raise _Refused('not-regular')
        return b''.join(chunks)
    finally:
        _close(fd)


def measure_factory(mount, path, root=ROOT):
    """Stream one pinned factory archive through its contained regular descriptor."""
    pin = recipe(root)['factory']
    fd = None
    try:
        fd, before = _open_regular(mount, path)
        if before.st_size != pin['sizeBytes']:
            raise _Refused('factory-size-differs')
        remaining = before.st_size
        digest = hashlib.sha256()
        while remaining:
            block = os.read(fd, min(1 << 20, remaining))
            if not block:
                raise _Refused('factory-size-differs')
            digest.update(block)
            remaining -= len(block)
        if os.read(fd, 1):
            raise _Refused('factory-size-differs')
        after = os.fstat(fd)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if identity(before) != identity(after):
            raise _Refused('factory-changed-during-read')
    except _Refused as refused:
        code = refused.code if refused.code in ('factory-size-differs', 'factory-changed-during-read') else 'factory-not-contained'
        return {'matchesPinnedFactory': False, 'admitsOtatools': False, 'problems': [code]}
    except OSError:
        return {'matchesPinnedFactory': False, 'admitsOtatools': False, 'problems': ['factory-read-failed']}
    finally:
        _close(fd)
    problems = []
    if digest.hexdigest() != pin['sha256']:
        problems.append('factory-hash-differs')
    return {'matchesPinnedFactory': not problems, 'admitsOtatools': False, 'problems': problems}


def _under(source, relative, limit):
    return read_regular(source, str(Path(source)/relative), limit)


def measure_deps(source, root=ROOT):
    """Revision marker and host-tool paths as regular files. Executability is not provenance."""
    spec = recipe(root)
    problems = []
    want = (spec['pins']['vendor/adevtool'] + '\n').encode()
    try:
        have = _under(source, spec['adevtool']['depsDir'] + '/adevtool_revision', 128)
    except _Refused:
        have = None
    if have != want:
        problems.append('adevtool-revision-differs')
    deps = spec['adevtool']['depsDir']
    for name in spec['adevtool']['depsTools']:
        if not isinstance(name, str) or '/' in name or name in ('', '.', '..'):
            problems.append('adevtool-host-tools-unmeasured')
            break
        try:
            fd, _info = _open_regular(source, str(Path(source)/('%s/host/linux-x86/bin/%s' % (deps, name))))
            _close(fd)
        except _Refused:
            problems.append('adevtool-host-tools-unmeasured')
            break
    return {'provenance': False, 'admitsOtatools': False, 'problems': problems,
            'tools': list(spec['adevtool']['depsTools'])}


def measure_vendor(source):
    """The version-check include is visible or not. That include is not a vendor-tree proof."""
    try:
        data = read_regular(source, str(Path(source)/PRODUCT_MK), PRODUCT_MK_BOUND)
    except _Refused:
        data = b''
    include_seen = bool(data) and VERSION_INCLUDE in data
    return {'includeSeen': include_seen, 'provenance': False, 'admitsVendorTree': False,
            'admitsOtatools': False,
            'problems': [] if include_seen else ['mustang-product-config-absent']}


def main(argv=None):
    json.dump(decide(), sys.stdout, sort_keys=True)
    sys.stdout.write('\n')
    return 4


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
