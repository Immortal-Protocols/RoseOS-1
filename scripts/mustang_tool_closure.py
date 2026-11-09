#!/usr/bin/env python3
"""Reviewed hosted-signer tooling closure. Production pins stay unbound.

The files under script/ and device/common/ are the upstream helpers the hosted
signer actually reads or executes. script/decrypt-keys is replaced before
execution, and device/common/generate-factory-images-common.sh is after the
img_from_target_files cut, so neither is imported. The JDK section records a
deterministic zip of the official prebuilts/jdk/jdk21 linux-x86 tree. That zip
is not committed, and jdkSha256 in the admission pin stays null.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = 'os/build-only/mustang-hosted-tool-closure.json'
PIN = 'os/build-only/mustang-hosted-sign-script.json'
CAPACITY = 'os/build-only/mustang-hosted-signer-capacity.json'
RECIPE = 'roseos.jdk-linux-x86-zip.v1'
EPOCH = (1980, 1, 1, 0, 0, 0)
PIN_KEYS = ('generateReleaseSha256', 'jdkSha256', 'otatoolsSha256', 'otatoolsExecutable')
OUTPUT_BOUNDS = (
    'signedTargetFilesBytes', 'otaBytes', 'signedTargetFilesCopyBytes',
    'otaCopyBytes', 'scratchBytes', 'reserveBytes')
KEPT_SOURCES = (
    '"$(dirname ${BASH_SOURCE[0]})/common.sh"',
    'device/common/clear-factory-images-variables.sh',
)
KEPT_COMMANDS = ('decrypt-keys', 'generate-metadata')
IMPORTED_COMMANDS = {'generate-metadata': 'script/generate-metadata'}


class ClosureError(ValueError):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(root, relative):
    path = Path(root) / relative
    if path.is_symlink() or not path.is_file():
        raise ClosureError(f'{relative} is not a regular checkout file')
    return json.loads(path.read_text())


def production_unbound(root=ROOT):
    """Admission pin config stays closed. A provenance receipt is not a pin."""
    pin = load_json(root, PIN)
    for key in PIN_KEYS:
        if pin.get(key) is not None:
            raise ClosureError(f'production pin {key} is filled')
    if pin.get('trust') != 'held' or pin.get('status') != 'unbound':
        raise ClosureError('production script trust is not held')
    capacity = load_json(root, CAPACITY)
    if capacity.get('admission') is not False or capacity.get('trust') != 'held':
        raise ClosureError('hosted signer admission is not held')
    bounds = capacity.get('outputBounds') or {}
    for key in OUTPUT_BOUNDS:
        if bounds.get(key) is not None:
            raise ClosureError(f'output bound {key} is filled')
    proposed = capacity.get('proposedRunner') or {}
    if any(proposed.get(key) is not False for key in ('purchased', 'configured', 'selected', 'approved', 'sizingProof')):
        raise ClosureError('paid runner is recorded as activated')
    return pin, capacity


def checkout_file(root, relative):
    path = Path(root) / relative
    if path.is_symlink():
        raise ClosureError(f'{relative} is a symlink')
    root_resolved = Path(root).resolve()
    if not path.is_file() or root_resolved not in path.resolve().parents:
        raise ClosureError(f'{relative} left the checkout')
    if '..' in Path(relative).parts or Path(relative).is_absolute():
        raise ClosureError(f'{relative} is not a checkout path')
    return path


def executed_closure(script_text):
    if script_text.count('\nimg_from_target_files') != 1:
        raise ClosureError('release script factory cut is not unique')
    kept, tail = script_text.split('\nimg_from_target_files', 1)
    if 'generate-factory-images-common.sh' not in tail:
        raise ClosureError('factory helper was not beyond the cut')
    sources, commands = [], []
    for line in kept.splitlines():
        if line.startswith('source '):
            sources.append(line[len('source '):])
        elif line.startswith('script/'):
            command = line.split()[0].split('/')[-1]
            commands.append(command)
    if tuple(sources) != KEPT_SOURCES or tuple(commands) != KEPT_COMMANDS:
        raise ClosureError('release script executed a helper outside the reviewed closure')
    if 'generate-factory-images-common.sh' in kept or 'optimize-factory-image' in kept:
        raise ClosureError('factory helper is inside the executed closure')
    return sources, commands


def further_sources(path):
    text = path.read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('source ') or stripped.startswith('. ') or stripped.startswith('script/'):
            raise ClosureError(f'{path.name} sources another helper')


def verify_closure(root=ROOT,*,require_unbound=True):
    root = Path(root)
    if require_unbound:production_unbound(root)
    receipt = load_json(root, RECEIPT)
    if receipt.get('schema') != 'roseos.mustang-hosted-tool-closure.v1':
        raise ClosureError('tool closure receipt schema mismatch')
    if receipt.get('admission') is not False or receipt.get('productionPins') != 'unbound' or receipt.get('trust') != 'held':
        raise ClosureError('tool closure receipt enables admission')
    otatools=receipt.get('otatools') or {}
    if any(otatools.get(key) is not None for key in ('sha256','executable')):
        raise ClosureError('otatools receipt was filled without the independent builder')
    if otatools.get('qualificationEvidence') is not None:
        import mustang_otatools_qualification
        mustang_otatools_qualification.binding(otatools['qualificationEvidence'],root=root)
    if otatools.get('inspectionEvidence') is not None:
        import mustang_otatools_qualification
        mustang_otatools_qualification.inspection_record(otatools['inspectionEvidence'],root=root)
    files = receipt.get('files')
    if not isinstance(files, list) or not files:
        raise ClosureError('tool closure has no files')
    seen = set()
    for row in files:
        relative = row.get('path')
        if not isinstance(relative, str) or relative in seen:
            raise ClosureError('tool closure path is duplicated')
        seen.add(relative)
        path = checkout_file(root, relative)
        data = path.read_bytes()
        if len(data) != row.get('sizeBytes') or sha256_bytes(data) != row.get('sha256'):
            raise ClosureError(f'{relative} does not match the provenance receipt')
        if not isinstance(row.get('revision'), str) or not isinstance(row.get('gitBlob'), str):
            raise ClosureError(f'{relative} has no upstream revision')
    script = checkout_file(root, 'script/generate-release.sh')
    executed_closure(script.read_text())
    for relative in ('script/common.sh', 'device/common/clear-factory-images-variables.sh'):
        further_sources(checkout_file(root, relative))
    metadata = checkout_file(root, 'script/generate-metadata').read_text()
    if 'import ' not in metadata or 'script/' in metadata or 'device/common' in metadata:
        raise ClosureError('generate-metadata is not the reviewed standalone helper')
    if (root / 'script/decrypt-keys').exists() or (root / 'device/common/generate-factory-images-common.sh').exists():
        raise ClosureError('a helper the signer does not execute was imported')
    jdk = receipt.get('jdk') or {}
    if jdk.get('recipe') != RECIPE or jdk.get('committed') is not False:
        raise ClosureError('jdk archive recipe is not the reviewed uncommitted zip')
    if jdk.get('revision') != 'ef5bcc92586b839ae3dbacc154127092fa4002ec':
        raise ClosureError('jdk revision is not the pinned prebuilt')
    if jdk.get('javaVersion') != '21.0.4' or jdk.get('osName') != 'Linux' or jdk.get('osArch') != 'x86_64':
        raise ClosureError('jdk identity is not the pinned linux-x86 21.0.4 prebuilt')
    for key in ('archiveSha256', 'downloadSha256', 'releaseSha256', 'binJavaSha256'):
        if not isinstance(jdk.get(key), str) or len(jdk[key]) != 64:
            raise ClosureError(f'jdk {key} has no byte proof')
    for key in ('archiveSizeBytes', 'downloadSizeBytes', 'entryCount', 'binJavaSizeBytes'):
        if not isinstance(jdk.get(key), int) or jdk[key] < 1:
            raise ClosureError(f'jdk {key} is missing')
    if jdk.get('binJavaMode') != '0755':
        raise ClosureError('jdk bin/java is not a regular executable in the archive')
    return receipt


def stored_mode(mode):
    if stat.S_ISLNK(mode) or stat.S_ISDIR(mode) or not stat.S_ISREG(mode):
        raise ClosureError('jdk member is not a regular file')
    if mode & 0o7000:
        raise ClosureError('jdk member has a privileged mode')
    return 0o755 if mode & 0o111 else 0o644


def _contained(root, path):
    """Containment without resolving symlinks or collapsing `..`."""
    path = Path(os.path.abspath(path))
    if path == root or root in path.parents:
        return path
    raise ClosureError('symlink escape')


def _components(raw):
    if os.path.isabs(raw) or '\\' in raw or raw.startswith('/'):
        raise ClosureError('symlink escape')
    return [part for part in raw.split('/') if part not in ('', '.')]


def resolve_from(root, directory, raw, seen):
    """Apply each path component after the previous symlink, matching the filesystem.

    Lexical normalization of `alias/../file` would drop `alias` before reading it.
    `..` is applied to the directory that alias actually reached.
    """
    current = _contained(root, directory)
    parts = _components(raw)
    for index, part in enumerate(parts):
        if part == '..':
            if current == root:
                raise ClosureError('symlink escape')
            current = _contained(root, current.parent)
        else:
            nxt = current / part
            if nxt.is_symlink():
                key = str(Path(os.path.abspath(nxt)))
                if key in seen or len(seen) >= 40:
                    raise ClosureError('symlink cycle')
                seen.add(key)
                nxt = resolve_from(root, current, os.readlink(nxt), seen)
            elif not nxt.exists():
                raise ClosureError('symlink target missing')
            else:
                nxt = _contained(root, nxt)
            if nxt.is_symlink() or not nxt.exists():
                raise ClosureError('symlink target missing')
            current = nxt
        if index + 1 < len(parts) and not current.is_dir():
            raise ClosureError('jdk member is not a directory')
    return current


def follow_symlink(root, link):
    link = Path(os.path.abspath(link))
    if not link.is_symlink():
        raise ClosureError('jdk member is not a symlink')
    seen = {str(link)}
    return resolve_from(root, link.parent, os.readlink(link), seen)


def collect_members(root):
    root = Path(os.path.abspath(root))
    if root.is_symlink() or not root.is_dir():
        raise ClosureError('jdk root is not a real directory')
    found = []

    def walk(directory, prefix, stack):
        directory = _contained(root, directory)
        if directory.is_symlink() or not directory.is_dir():
            raise ClosureError('jdk member is not a directory')
        key = str(directory)
        if key in stack:
            raise ClosureError('symlink cycle')
        stack = stack + (key,)
        try:
            names = sorted(os.listdir(directory))
        except NotADirectoryError as exc:
            raise ClosureError('jdk member is not a directory') from exc
        for name in names:
            if name in ('.', '..') or '/' in name or '\\' in name:
                raise ClosureError('jdk member name is unsafe')
            entry = directory / name
            logical = name if not prefix else prefix + '/' + name
            if entry.is_symlink():
                target = follow_symlink(root, entry)
                if target.is_dir():
                    walk(target, logical, stack)
                elif target.is_file():
                    found.append((logical, target))
                else:
                    raise ClosureError('jdk member is not a regular file')
            elif entry.is_dir():
                walk(entry, logical, stack)
            elif entry.is_file():
                found.append((logical, entry))
            else:
                raise ClosureError('jdk member is not a regular file')

    walk(root, '', ())
    found.sort(key=lambda item: item[0])
    if len({name for name, _ in found}) != len(found):
        raise ClosureError('jdk archive path is duplicated')
    return found


def pack_tree(root):
    """Deterministic zip. In-tree symlinks become the target's bytes and mode."""
    members = collect_members(root)
    buffer = __import__('io').BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=False) as archive:
        archive.comment = b''
        for name, source in members:
            info = os.lstat(source)
            mode = stored_mode(info.st_mode)
            data = Path(source).read_bytes()
            record = zipfile.ZipInfo(filename=name, date_time=EPOCH)
            record.compress_type = zipfile.ZIP_DEFLATED
            record.create_system = 3
            record.flag_bits = 0x800
            record.external_attr = mode << 16
            record.extra = b''
            archive.writestr(record, data)
    return buffer.getvalue()


def archive_member(blob, name):
    with zipfile.ZipFile(__import__('io').BytesIO(blob)) as archive:
        info = archive.getinfo(name)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or mode & 0o7000:
            raise ClosureError('archive member is unsafe')
        return {'mode': f'{mode & 0o777:04o}', 'sha256': sha256_bytes(archive.read(name)), 'sizeBytes': info.file_size}


def verify_archive_members(blob):
    with zipfile.ZipFile(__import__('io').BytesIO(blob)) as archive:
        for info in archive.infolist():
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or mode & 0o7000 or info.filename.startswith('/') or '..' in Path(info.filename).parts:
                raise ClosureError('archive member is unsafe')
            if (mode & 0o777) not in (0o755, 0o644):
                raise ClosureError('archive member mode is not the deterministic executable bit')


def main(argv):
    if argv == ['verify']:
        receipt = verify_closure(ROOT)
        print(json.dumps({'status': 'held', 'files': len(receipt['files']),
                          'jdkArchiveSha256': receipt['jdk']['archiveSha256']}, sort_keys=True))
        return 0
    print('usage: mustang_tool_closure.py verify', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
