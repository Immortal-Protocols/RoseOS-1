"""Independent source preparation for the Cuttlefish emulator alpha (INFRA-ALPHA-085): an ordinary copy of the
retained Pixel tree's source inputs into the reserved root, plus the OS delta, recorded by a write-once receipt.

The retained tree is only read. Every regular file is copied byte by byte into a new inode (no reflink, hardlink,
symlink forest, mount, or patch-and-restore), keeping its mode and mtime; directories keep their modes. What is not
copied is decided by an exact policy and listed in the receipt:
  * root names (.repo, keys, releases, out) and root prefixes (out-, out_): repo metadata, Pixel release keys,
    release outputs and build outputs;
  * names anywhere (.git, .repo): version-control metadata; the base revisions are snapshotted before;
  * listed external links, each matched by path and exact target;
  * cache boundaries: an exact directory proven not to be a build input, here adevtool's extracted factory-image
    cache vendor/adevtool/dl/unpacked (INFRA-ALPHA-090). Its links point at device paths (/system/bin, /vendor/...,
    /sys/kernel/debug) that are image-internal, not host inputs. The boundary holds only while its prune marker
    (vendor/adevtool/dl/.find-ignore, which the build's file finder honours) is an empty regular file in the base
    and no build-definition file (.mk, .bp, .go) in the copied selection names the cache; otherwise preparation
    stops and names the cause. The cache itself stays untouched in the base.
Symlinks are resolved hop by hop inside the tree before anything is written: relative links that stay inside are
kept as they are, absolute links into the tree are rebased onto the prepared tree, links whose target is absent in
the base (dangling) are kept only if their target is contained in the tree, and any other escape, a link into an
excluded path that exists, a loop or an unsupported file type stops the preparation and is named.
git apply rewrites each patched file, so under a restrictive umask it would lose the copied mode; the mode recorded
for exactly each declared changed file is restored afterwards (and listed in the receipt), never any other file.
Staging happens in <root>/.staging-<id>-<nonce>/, renamed to <root>/<id>/ (tree/ and preparation.json) only after
the delta applied with the expected hashes and the base was seen unchanged. On failure this run removes only the
staging directory it created (it carries this run's marker); a leftover from an interrupted run is never removed
automatically and blocks a new preparation of the same id.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time

MARKER = '.rose-alpha-staging'
MAX_HOPS = 40

class PreparationFailed(Exception):
    pass

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()

def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()

def sha256_path(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()

BUILD_FILE_SUFFIXES = ('.mk', '.bp', '.go')

def excluded_reason(rel, policy):
    for boundary in policy.get('cacheBoundaries', []):
        if rel == boundary['path']:
            return 'cache boundary: ' + boundary['reason']
    parts = rel.split('/')
    if len(parts) == 1 and (parts[0] in policy['rootNames'] or any(parts[0].startswith(p) for p in policy['rootPrefixes'])):
        return 'excluded root'
    if parts[-1] in policy['anywhereNames']:
        return 'version-control metadata'
    return None

def inside_excluded(rel, policy):
    parts = rel.split('/')
    return any(excluded_reason('/'.join(parts[:i + 1]), policy) for i in range(len(parts)))

def boundary_problems(base, boundary, policy):
    """A cache boundary is admitted only as the exact real directory with its build-finder prune marker present."""
    problems, path, marker = [], base/boundary['path'], base/boundary['pruneMarker']
    if os.path.islink(path) or not path.is_dir():
        problems.append(f'cache boundary {boundary["path"]} is not a real directory in the base')
    if os.path.islink(marker) or not marker.is_file() or marker.stat().st_size:
        problems.append(f'prune marker {boundary["pruneMarker"]} is not an empty regular file; the cache may be visible to the build')
    elif inside_excluded(boundary['pruneMarker'], policy):
        problems.append(f'prune marker {boundary["pruneMarker"]} would not be copied')
    return problems

def resolve_link(base, rel, hops=0):
    """Follow the link at base/rel hop by hop without leaving base. Returns (kind, target_rel, exists) with kind
    'relative' or 'absolute' for the link's own text, or raises PreparationFailed naming the escape."""
    text = os.readlink(base/rel)
    if os.path.isabs(text):
        prefix = str(base) + '/'
        if not (text + '/').startswith(prefix):
            raise PreparationFailed(f'{rel} -> {text} leaves the source tree')
        kind, lexical = 'absolute', os.path.normpath(text[len(prefix):] or '.')
    else:
        kind, lexical = 'relative', os.path.normpath(os.path.join(os.path.dirname(rel), text))
        if lexical == '..' or lexical.startswith('../'):
            raise PreparationFailed(f'{rel} -> {text} leaves the source tree')
    target, exists = walk_contained(base, lexical, hops + 1, rel)
    return kind, target, exists

def walk_contained(base, lexical, hops, origin):
    if hops > MAX_HOPS:
        raise PreparationFailed(f'{origin}: symlink chain longer than {MAX_HOPS} or a loop')
    if lexical in ('.', ''):
        return '.', True
    parts, current = lexical.split('/'), []
    for index, part in enumerate(parts):
        candidate = '/'.join(current + [part])
        try:
            info = os.lstat(base/candidate)
        except (FileNotFoundError, NotADirectoryError):
            return os.path.normpath('/'.join([candidate] + parts[index + 1:])), False
        if stat.S_ISLNK(info.st_mode):
            _, resolved, exists = resolve_link(base, candidate, hops)
            if not exists:
                return os.path.normpath('/'.join([resolved] + parts[index + 1:])), False
            rest = '/'.join(parts[index + 1:])
            return walk_contained(base, os.path.normpath(f'{resolved}/{rest}' if rest else resolved), hops + 1, origin)
        current.append(part)
    return '/'.join(current), True

def plan_copy(base, policy):
    """Metadata walk only: what is copied, what is excluded and why, and every problem that stops the run."""
    base = Path(base)
    entries, excluded, problems = [], [], []
    external = {e['path']: e['target'] for e in policy['externalLinks']}
    counts = {'files': 0, 'dirs': 0, 'linksInternal': 0, 'linksRebased': 0, 'linksDangling': 0, 'allocatedBytes': 0, 'apparentBytes': 0}
    dangling = []
    boundaries = policy.get('cacheBoundaries', [])
    for boundary in boundaries:
        problems += boundary_problems(base, boundary, policy)
    stack = ['']
    while stack:
        directory = stack.pop()
        with os.scandir(base/directory if directory else base) as it:
            names = sorted(e.name for e in it)
        for name in names:
            rel = f'{directory}/{name}' if directory else name
            reason = excluded_reason(rel, policy)
            if reason:
                excluded.append({'path': rel, 'reason': reason})
                continue
            info = os.lstat(base/rel)
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISDIR(info.st_mode):
                entries.append({'path': rel, 'type': 'dir', 'mode': mode})
                counts['dirs'] += 1
                stack.append(rel)
            elif stat.S_ISREG(info.st_mode):
                entries.append({'path': rel, 'type': 'file', 'mode': mode, 'size': info.st_size,
                    'ino': info.st_ino, 'mtime_ns': info.st_mtime_ns})
                counts['files'] += 1
                counts['allocatedBytes'] += info.st_blocks * 512
                counts['apparentBytes'] += info.st_size
                if boundaries and name.endswith(BUILD_FILE_SUFFIXES):
                    data = (base/rel).read_bytes()
                    problems += [f'{rel} references the excluded cache ({m}); the boundary is not proven for this tree'
                        for b in boundaries for m in b['referenceMarkers'] if m.encode() in data]
            elif stat.S_ISLNK(info.st_mode):
                text = os.readlink(base/rel)
                if rel in external:
                    if text != external[rel]:
                        problems.append(f'{rel} -> {text} is not the listed {external[rel]}')
                    else:
                        excluded.append({'path': rel, 'reason': f'listed non-input link to {text}'})
                    continue
                try:
                    kind, target, exists = resolve_link(base, rel)
                except PreparationFailed as error:
                    problems.append(str(error))
                    continue
                if exists and inside_excluded(target, policy):
                    problems.append(f'{rel} -> {text} reaches excluded {target}, which would dangle in the prepared tree')
                    continue
                entry = {'path': rel, 'type': 'link', 'text': text, 'kind': kind, 'target': target, 'exists': exists}
                entries.append(entry)
                if not exists:
                    counts['linksDangling'] += 1
                    dangling.append({'path': rel, 'target': target})
                counts['linksRebased' if kind == 'absolute' else 'linksInternal'] += 1
            else:
                problems.append(f'{rel}: unsupported file type')
    unlisted = sorted(set(external) - {e['path'] for e in excluded})
    problems += [f'listed external link {p} not found as listed' for p in unlisted]
    return {'entries': entries, 'excluded': excluded, 'problems': problems, 'counts': counts, 'dangling': dangling}

def apply_patch(directory, patch, ceiling):
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(ceiling))
    for args in (['apply', '--check', str(patch)], ['apply', str(patch)]):
        result = subprocess.run(['git', '-C', str(directory), *args], env=env, capture_output=True, text=True)
        if result.returncode:
            raise PreparationFailed(f'git {" ".join(args[:-1])} of {Path(patch).name} failed in {Path(directory).name}: '
                + result.stderr.strip()[-300:])

def predict_changes(base, delta, scratch):
    """Before/after hashes of the delta's changed files, from a scratch copy of only those files."""
    changes = []
    for item in delta['changedFiles']:
        source = Path(base)/item['project']/item['path']
        if not source.is_file() or source.is_symlink():
            raise PreparationFailed(f'{item["project"]}/{item["path"]} is not a regular file in the base')
        project = Path(scratch)/item['project']
        (project/item['path']).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, project/item['path'])
        changes.append({'project': item['project'], 'path': item['path'], 'beforeSha256': sha256_path(source)})
    for patch in delta['patches']:
        apply_patch(Path(scratch)/patch['project'], patch['file'], scratch)
    for change in changes:
        change['afterSha256'] = sha256_path(Path(scratch)/change['project']/change['path'])
        if change['afterSha256'] == change['beforeSha256']:
            raise PreparationFailed(f'{change["project"]}/{change["path"]} is unchanged by the delta')
    return changes

def restore_changed_modes(tree, changes, plan):
    """git apply writes each patched file anew, so under a restrictive umask (the builder runs with 0077) it loses the
    copied mode. Restore the mode the copy recorded, on exactly the delta's declared changed files and nothing else."""
    recorded = {e['path']: e['mode'] for e in plan['entries'] if e['type'] == 'file'}
    modes = []
    for change in changes:
        rel = f'{change["project"]}/{change["path"]}'
        if rel not in recorded:
            raise PreparationFailed(f'{rel} is not a copied regular file; its mode is not recorded')
        info = os.lstat(Path(tree)/rel)
        if not stat.S_ISREG(info.st_mode):
            raise PreparationFailed(f'{rel} is not a regular file after the delta')
        after = stat.S_IMODE(info.st_mode)
        if after != recorded[rel]:
            os.chmod(Path(tree)/rel, recorded[rel])
        modes.append({'path': rel, 'recordedMode': f'{recorded[rel]:o}', 'modeAfterDelta': f'{after:o}', 'restored': after != recorded[rel]})
    return modes

def preparation_id(binding, delta, changes):
    return sha256_bytes(canonical({'base': binding, 'osDelta': {'commit': delta['commit'],
        'patchDigests': [{'path': p['path'], 'project': p['project'], 'sha256': p['sha256']} for p in delta['patches']]},
        'changedFiles': changes}))

def copy_file(source, destination, entry):
    digest = hashlib.sha256()
    fd_in = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fd_out = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            while True:
                block = os.read(fd_in, 1 << 20)
                if not block:
                    break
                digest.update(block)
                view = memoryview(block)
                while view:
                    view = view[os.write(fd_out, view):]
        finally:
            os.close(fd_out)
        after = os.fstat(fd_in)
    finally:
        os.close(fd_in)
    if (after.st_ino, after.st_size, after.st_mtime_ns) != (entry['ino'], entry['size'], entry['mtime_ns']):
        raise PreparationFailed(f'{entry["path"]} changed in the base during the copy')
    os.chmod(destination, entry['mode'])
    os.utime(destination, ns=(entry['mtime_ns'], entry['mtime_ns']))
    return digest.hexdigest()

def copy_tree(base, tree, final_tree, plan):
    """Ordinary copy into tree; returns the manifest lines (path, type, mode, size or link text, sha256 or target)."""
    base, tree = Path(base), Path(tree)
    tree.mkdir()
    lines, directories = [], []
    for entry in plan['entries']:
        destination = tree/entry['path']
        if entry['type'] == 'dir':
            destination.mkdir(mode=0o700)
            directories.append(entry)
            lines.append(f'{entry["path"]}\tdir\t{entry["mode"]:o}\t-\t-')
        elif entry['type'] == 'file':
            digest = copy_file(base/entry['path'], destination, entry)
            lines.append(f'{entry["path"]}\tfile\t{entry["mode"]:o}\t{entry["size"]}\t{digest}')
        else:
            text = entry['text'] if entry['kind'] == 'relative' else str(Path(final_tree)/entry['target'])
            os.symlink(text, destination)
            lines.append(f'{entry["path"]}\tlink\t-\t{text}\t{entry["target"]}')
    for entry in reversed(directories):  # modes last, deepest first, so read-only directories did not block copying
        os.chmod(tree/entry['path'], entry['mode'])
    return lines

def preserved_hashes(base, paths):
    return {p: (sha256_path(Path(base)/p) if (Path(base)/p).is_file() and not (Path(base)/p).is_symlink() else None) for p in sorted(paths)}

def prepare(base, reserved_root, binding, delta, policy, extra, free_needed_bytes, budget_bytes, write_receipt=True, now=time.time):
    """Plan, check capacity, copy, apply the delta and publish <reserved_root>/<id>. extra: receipt fields that the
    caller verified (target, apps, policy, updater, base evidence, git heads, preservation paths)."""
    base, reserved_root = Path(base).resolve(), Path(reserved_root)
    with tempfile.TemporaryDirectory(prefix='rose-alpha-delta-') as scratch:
        changes = predict_changes(base, delta, scratch)
    identity = preparation_id(binding, delta, changes)
    final = reserved_root/identity
    if final.exists() or final.is_symlink():
        raise PreparationFailed(f'{final} already exists; preparations are immutable (verify it, or choose a new delta)')
    leftovers = sorted(p.name for p in reserved_root.glob(f'.staging-{identity}-*')) if reserved_root.is_dir() else []
    if leftovers:
        raise PreparationFailed(f'an interrupted preparation left {", ".join(leftovers)} in {reserved_root}; inspect and remove it deliberately')
    plan = plan_copy(base, policy)
    if plan['problems']:
        raise PreparationFailed('unsupported source dependencies: ' + '; '.join(plan['problems'][:50]))
    reserved_root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(reserved_root).free
    if plan['counts']['allocatedBytes'] > budget_bytes:
        raise PreparationFailed(f'the selection allocates {plan["counts"]["allocatedBytes"]} bytes, above the copy budget {budget_bytes}')
    if free < free_needed_bytes:
        raise PreparationFailed(f'{free} bytes free, below the {free_needed_bytes} needed for the copy budget plus the post-copy build minimum')
    preserved = extra['preservationPaths']
    before = preserved_hashes(base, preserved)
    nonce = sha256_bytes(os.urandom(16))[:12]
    staging = reserved_root/f'.staging-{identity}-{nonce}'
    staging.mkdir(mode=0o700)
    (staging/MARKER).write_text(json.dumps({'nonce': nonce, 'pid': os.getpid(), 'started': now()}))
    try:
        lines = copy_tree(base, staging/'tree', final/'tree', plan)
        manifest = ('\n'.join(lines) + '\n').encode()
        with open(staging/'manifest.tsv', 'xb') as f:
            f.write(manifest)
        for patch in delta['patches']:
            apply_patch(staging/'tree'/patch['project'], patch['file'], staging)
        modes = restore_changed_modes(staging/'tree', changes, plan)
        for change in changes:
            actual = sha256_path(staging/'tree'/change['project']/change['path'])
            if actual != change['afterSha256']:
                raise PreparationFailed(f'{change["project"]}/{change["path"]} has {actual} after the delta, not {change["afterSha256"]}')
        after = preserved_hashes(base, preserved)
        if after != before:
            raise PreparationFailed('the base changed during preparation: ' + ', '.join(p for p in before if before[p] != after[p]))
        selection = sha256_bytes(manifest)
        receipt = {'schemaVersion': 1, 'kind': 'rose-cuttlefish-source-preparation', 'preparationId': identity,
            'preparedRoot': str(final/'tree'), 'baseRoot': str(base), **binding,
            'osDeltaCommit': delta['commit'],
            'patchDigests': [{'path': p['path'], 'project': p['project'], 'sha256': p['sha256']} for p in delta['patches']],
            'changedFiles': changes, 'changedFileModes': modes, 'selectionSha256': selection, 'manifest': 'manifest.tsv',
            'effectiveSourceIdentity': sha256_bytes(canonical({'base': binding, 'selectionSha256': selection,
                'osDeltaCommit': delta['commit'], 'changedFiles': changes})),
            'sourceCopyMethod': 'ordinary byte copy into new inodes (no reflink, hardlink, symlink forest or mount); modes and mtimes kept',
            'exclusionPolicy': policy, 'excluded': plan['excluded'], 'counts': plan['counts'], 'danglingLinks': plan['dangling'],
            'preservationChecks': {'paths': before, 'unchangedAfterCopy': True},
            'freeBytesBefore': free, 'freeBytesAfter': shutil.disk_usage(reserved_root).free,
            'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now())), **{k: v for k, v in extra.items() if k != 'preservationPaths'}}
        if write_receipt:
            with open(staging/'preparation.json', 'x') as f:
                f.write(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
        os.unlink(staging/MARKER)
        os.rename(staging, final)
    except BaseException:
        marker = staging/MARKER
        if marker.is_file() and json.loads(marker.read_text()).get('nonce') == nonce:
            make_removable(staging)
            shutil.rmtree(staging)
        raise
    return receipt

def make_removable(path):
    """Restore owner write on this run's own staging directories (copied modes may be read-only) before removing it."""
    for directory, dirs, _ in os.walk(path):
        for name in dirs:
            child = Path(directory)/name
            if not child.is_symlink():
                os.chmod(child, stat.S_IMODE(os.lstat(child).st_mode) | stat.S_IRWXU)

def verify_tree(tree, manifest_text, changes, skip=()):
    """Full re-hash of a prepared tree against its manifest (the delta's changed files carry their after hashes).
    Returns the differences; build outputs named in skip are ignored."""
    tree = Path(tree)
    after = {f'{c["project"]}/{c["path"]}': c['afterSha256'] for c in changes}
    expected, problems = {}, []
    for line in manifest_text.splitlines():
        path, kind, mode, size, value = line.split('\t')
        expected[path] = (kind, mode, size, value)
    seen = set()
    for directory, dirs, files in os.walk(tree):
        rel_dir = os.path.relpath(directory, tree)
        dirs[:] = [d for d in dirs if os.path.normpath(os.path.join(rel_dir, d)) not in skip]
        for name in dirs + files:
            rel = os.path.normpath(os.path.join(rel_dir, name))
            seen.add(rel)
            if rel not in expected:
                problems.append(f'{rel}: not in the preparation manifest')
                continue
            kind, mode, size, value = expected[rel]
            info = os.lstat(tree/rel)
            if kind == 'link':
                if not stat.S_ISLNK(info.st_mode) or os.readlink(tree/rel) != size:
                    problems.append(f'{rel}: link differs')
            elif kind == 'dir':
                if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != int(mode, 8):
                    problems.append(f'{rel}: directory differs')
            elif not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != int(mode, 8) \
                    or sha256_path(tree/rel) != after.get(rel, value):
                problems.append(f'{rel}: file differs')
    problems += [f'{p}: missing' for p in sorted(set(expected) - seen)]
    return problems

# ---------------------------------------------------------------- successor transition (INFRA-ALPHA-104)

SUCCESSORS = 'successors'
STARTED, RECORD = 'transition-started.json', 'successor.json'

def successor_id(predecessor, delta, incremental, changes):
    return sha256_bytes(canonical({'predecessor': {'preparationId': predecessor['preparationId'], 'receiptSha256': predecessor['receiptSha256']},
        'osDelta': {'commit': delta['commit'], 'contractSha256': delta['contractSha256'],
            'patchDigests': [{'path': p['path'], 'project': p['project'], 'sha256': p['sha256']} for p in delta['patches']]},
        'incremental': {k: incremental[k] for k in ('path', 'sha256', 'project', 'file', 'beforeSha256', 'afterSha256')},
        'changedFiles': changes}))

def contained_regular(tree, rel):
    """tree/rel as a regular file reached without any symlink component, or a PreparationFailed naming why not."""
    current = Path(tree)
    for part in rel.split('/'):
        current = current/part
        info = os.lstat(current) if os.path.lexists(current) else None
        if info is None:
            raise PreparationFailed(f'{rel} is absent from the prepared tree')
        if stat.S_ISLNK(info.st_mode):
            raise PreparationFailed(f'{rel} reaches a symlink at {current.relative_to(tree)}; the transition never follows links')
    if not stat.S_ISREG(info.st_mode):
        raise PreparationFailed(f'{rel} is not a regular file')
    return current

def out_dir_state(tree, out_dir):
    path = Path(tree)/out_dir
    if not os.path.lexists(path):
        return {'path': out_dir, 'present': False}
    info = os.lstat(path)
    return {'path': out_dir, 'present': True, 'directory': stat.S_ISDIR(info.st_mode), 'inode': info.st_ino, 'mtimeNs': info.st_mtime_ns}

def transition(directory, predecessor, delta, incremental, extra, recover=False, now=time.time):
    """Move an immutable preparation's tree in place to a successor delta by exactly one declared file change, recorded by
    a write-once successor receipt beside the predecessor (directory/successors/<id>/). The predecessor's
    preparation.json, manifest and every other byte of the tree, and its retained OUT_DIR, stay as they are.

    predecessor: the predecessor receipt plus its receiptSha256 (already checked by the caller). delta: the successor's
    commit, contractSha256 and patches (digests only). incremental: the contract's one-file delta with its patch file
    (path, sha256, file on disk, project, file, beforeSha256, afterSha256). extra: receipt fields the caller verified.
    Order: full re-hash of the predecessor tree against its manifest (anything but the declared file at its preimage is
    drift and stops before any write), a write-once journal, git apply --check and apply of only the incremental patch,
    the recorded mode restored, the postimage and a refused second --check, then the write-once receipt. An existing
    receipt makes the call a verified no-op; a journal without a receipt is an interrupted transition that only
    recover=True continues, from exactly the preimage or the postimage of that file."""
    directory = Path(directory)
    tree = directory/'tree'
    rel = f'{incremental["project"]}/{incremental["file"]}'
    changes, found = [], False
    for change in predecessor['changedFiles']:
        if (change['project'], change['path']) == (incremental['project'], incremental['file']):
            if change['afterSha256'] != incremental['beforeSha256']:
                raise PreparationFailed(f'the predecessor recorded {rel} as {change["afterSha256"]}, not the incremental preimage {incremental["beforeSha256"]}')
            change, found = {**change, 'afterSha256': incremental['afterSha256']}, True
        changes.append(change)
    if not found:
        raise PreparationFailed(f'{rel} is not one of the predecessor\'s changed files; the incremental delta has no ancestry there')
    identity = successor_id(predecessor, delta, incremental, changes)
    record_dir = directory/SUCCESSORS/identity
    record, started = record_dir/RECORD, record_dir/STARTED
    if record.exists():
        receipt = json.loads(record.read_text())
        current = sha256_path(contained_regular(tree, rel))
        if receipt.get('successorId') != identity or current != incremental['afterSha256']:
            raise PreparationFailed(f'{record} exists but the tree or receipt no longer matches successor {identity}')
        return {**receipt, 'alreadyTransitioned': True}
    if started.exists() and not recover:
        raise PreparationFailed(f'an interrupted transition left {started}; inspect the tree, then rerun with --recover')
    if recover and not started.exists():
        raise PreparationFailed(f'nothing to recover: {started} is absent')
    target = contained_regular(tree, rel)
    recorded = {e['path']: e for e in predecessor.get('changedFileModes', [])}
    manifest_text = (directory/predecessor['manifest']).read_text()
    if sha256_bytes(manifest_text.encode()) != predecessor['selectionSha256']:
        raise PreparationFailed('the predecessor manifest differs from its selectionSha256')
    mode = next((int(line.split('\t')[2], 8) for line in manifest_text.splitlines() if line.split('\t')[0] == rel), None)
    if mode is None:
        raise PreparationFailed(f'{rel} has no recorded mode in the predecessor manifest')
    current = sha256_path(target)
    allowed = {incremental['beforeSha256']} | ({incremental['afterSha256']} if recover else set())
    if current not in allowed:
        raise PreparationFailed(f'{rel} is {current}, not the incremental preimage {incremental["beforeSha256"]}'
            + (' or postimage' if recover else ''))
    # Full re-hash: every entry but rel must be exactly the predecessor's; rel was checked above.
    problems = [p for p in verify_tree(tree, manifest_text, predecessor['changedFiles'], skip={extra['outDir']}) if p != f'{rel}: file differs']
    if stat.S_IMODE(os.lstat(target).st_mode) != mode and current == incremental['beforeSha256']:
        problems.append(f'{rel}: mode {stat.S_IMODE(os.lstat(target).st_mode):o}, not the recorded {mode:o}')
    if problems:
        raise PreparationFailed('the prepared tree drifted from its predecessor receipt: ' + '; '.join(problems[:50]))
    out_before = out_dir_state(tree, extra['outDir'])
    entries = len(manifest_text.splitlines())
    if recover:
        journal = json.loads(started.read_text())
        if journal.get('successorId') != identity:
            raise PreparationFailed(f'{started} names another successor')
        nonce = journal['nonce']
    else:
        record_dir.mkdir(parents=True, exist_ok=True)
        nonce = sha256_bytes(os.urandom(16))[:12]
        with open(started, 'x') as f:
            f.write(json.dumps({'successorId': identity, 'nonce': nonce, 'pid': os.getpid(), 'started': now(),
                'preimageSha256': incremental['beforeSha256']}, sort_keys=True) + '\n')
    recovered = None
    if current == incremental['beforeSha256']:
        apply_patch(tree/incremental['project'], incremental['patchFile'], directory)
        recovered = 'preimage' if recover else None
    else:
        recovered = 'postimage'
    after_mode = stat.S_IMODE(os.lstat(target).st_mode)
    if after_mode != mode:
        os.chmod(target, mode)
    target = contained_regular(tree, rel)
    if sha256_path(target) != incremental['afterSha256'] or stat.S_IMODE(os.lstat(target).st_mode) != mode:
        raise PreparationFailed(f'{rel} is not the incremental postimage {incremental["afterSha256"]} with mode {mode:o} after the transition')
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(directory))
    second = subprocess.run(['git', '-C', str(tree/incremental['project']), 'apply', '--check', str(incremental['patchFile'])],
        env=env, capture_output=True, text=True)
    if second.returncode == 0:
        raise PreparationFailed(f'the incremental patch would apply again to {rel}; the postimage is not a one-time transition')
    out_after = out_dir_state(tree, extra['outDir'])
    if out_after != out_before:
        raise PreparationFailed(f'{extra["outDir"]} changed during the transition')
    selection = predecessor['selectionSha256']
    binding = {k: predecessor[k] for k in ('baseCandidate', 'baseResolvedManifestSha256', 'baseReconcileReceipt', 'baseReconcileSha256')}
    receipt = {'schemaVersion': 1, 'kind': 'rose-cuttlefish-source-successor', 'successorId': identity,
        'predecessor': {'preparationId': predecessor['preparationId'], 'receiptSha256': predecessor['receiptSha256'],
            'selectionSha256': selection, 'effectiveSourceIdentity': predecessor['effectiveSourceIdentity'],
            'osDeltaCommit': predecessor['osDeltaCommit'], 'patchDigests': predecessor['patchDigests'],
            'contractSha256': predecessor.get('contractSha256')},
        'preparedRoot': str(tree), **binding, 'osDeltaCommit': delta['commit'], 'contractSha256': delta['contractSha256'],
        'patchDigests': [{'path': p['path'], 'project': p['project'], 'sha256': p['sha256']} for p in delta['patches']],
        'incremental': {**{k: incremental[k] for k in ('path', 'sha256', 'project', 'file', 'beforeSha256', 'afterSha256')},
            'recordedMode': f'{mode:o}', 'modeAfterApply': f'{after_mode:o}', 'modeRestored': after_mode != mode, 'secondCheckRefused': True},
        'changedFiles': changes, 'selectionSha256': selection, 'manifest': f'../../{predecessor["manifest"]}',
        'effectiveSourceIdentity': sha256_bytes(canonical({'base': binding, 'selectionSha256': selection,
            'osDeltaCommit': delta['commit'], 'changedFiles': changes})),
        'transitionMethod': 'in place: one git apply of the incremental patch to one regular file of the predecessor tree; no copy, reset or clean',
        'verification': {'predecessorFullRehash': True, 'manifestEntries': entries, 'differencesOutsideTheDelta': 0},
        'outDirPreserved': {'before': out_before, 'after': out_after}, 'recoveredFrom': recovered, 'journalNonce': nonce,
        'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now())), **extra}
    with open(record, 'x') as f:
        f.write(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt
