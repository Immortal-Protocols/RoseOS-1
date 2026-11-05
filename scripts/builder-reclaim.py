#!/usr/bin/env python3
"""BUILDER-RECLAIM (Release): reclaim safely regenerable build data from the shared builder's attempt storage.

Nothing else prunes that storage (/home/pixelbuild/rose-actions); every run's directories stay, and the builder ran
out of disk on 2026-11-04 (os/mustang-ota/41884368820). The policy, os/release-pipeline/storage/builder-reclaim.json, names
the data a later run regenerates by itself and when it may go:

  plan  [--policy P] [--root R] [--current-run ID] [--measure]
                    what apply would reclaim now, as JSON; changes nothing and takes no lock
  apply [--policy P] [--root R] [--current-run ID] --label L
                    reclaim it under the shared builder lock, with a receipt

What is reclaimed (default deny):
- Only entries a policy class matches, in the class's parent directory, are candidates. Within each, only the paths
  the class lists go (`.` is the whole entry); a listed directory's `keep` children stay (mustang-prep's
  source/releases, which holds signed copies).
- An entry is kept whole when any tracked file under the policy's pinSources names it (pinned inputs, recovery records,
  delivery evidence), when its run is the current run, or when it is one of its class's keepNewest newest (by run id,
  then attempt). Receipts, ledgers and anything not listed always stay.

Safety:
- apply holds the shared builder lock (the same file every builder job takes, flock exclusive and non-blocking) from
  before it reads the storage until its receipt is written. A busy lock reclaims nothing and exits 0.
- The storage root must be a real directory whose path has no symlink. Every other path is opened relative to it
  without following symlinks: a symlink is never followed, moved or removed, a non-directory where the class expects a
  directory is left, and nothing on another filesystem is touched. Each such case is reported.
- A listed path is first renamed, whole and atomically, into the root's trash directory (`.reclaim-trash`, same
  filesystem), then removed there. A killed apply leaves each path either untouched or wholly in the trash, never half
  removed where a build reads it, and every apply first empties the trash. A directory whose mode forbids removing its
  entries (read-only caches) is made writable by its owner first.
- Idempotent: a second apply finds nothing more to reclaim, and names never change, so the allocators, which read
  `frankel-*` and `mustang-integrate-*`/`mustang-reconcile-*` names as consumed builds, see the same entries.

Receipts (apply): <root>/reclaim/NNNN-<label>.plan.json before anything moves (the plan), and NNNN-<label>.json after
(each item's outcome, the trash it emptied, the filesystem's available bytes before and after). Both are write-once.
apply prints the result receipt.

Exit status: 0 reclaimed, nothing to reclaim, no storage root, or the lock was busy (the output says which); 2 invalid
policy or usage; 3 refused (an unsafe storage root, trash or receipt directory; nothing was moved).
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_receipts  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT/'os/release-pipeline/storage/builder-reclaim.json'
SCHEMA = 'roseos.builder-reclaim-policy.v1'
RECEIPT_SCHEMA = 'roseos.builder-reclaim.v1'
COMPONENT = re.compile(r'[A-Za-z0-9._-]{1,200}')
LABEL = re.compile(r'[A-Za-z0-9._-]{1,80}')
WORD = re.compile(r'[A-Za-z0-9._-]+')
PIN_FILE_LIMIT = 8 << 20


class Invalid(Exception):
    pass


class Refused(Exception):
    pass


class Left(Exception):
    """A path this step will not touch, with the reason."""


def component(value, what):
    if not (isinstance(value, str) and COMPONENT.fullmatch(value) and value not in ('.', '..')):
        raise Invalid(f'{what} must be one plain path component, not {str(value)[:40]!r}')
    return value


def lock_path(policy, repo):
    source, _, key = str(policy.get('lockFrom', '')).partition(' ')
    try:
        value = json.loads((Path(repo)/source).read_text())
        for part in key.split('.'):
            value = value[part]
    except (OSError, ValueError, KeyError, TypeError):
        raise Invalid('lockFrom must name a JSON file and key that hold the builder lock path') from None
    if not (isinstance(value, str) and os.path.isabs(value)):
        raise Invalid('the builder lock path must be absolute')
    return value


def load_policy(path=POLICY, repo=ROOT):
    """The policy, checked: classes with anchored entry patterns, single-component paths, and keepNewest only where
    entries carry a run id."""
    try:
        policy = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise Invalid(f'unreadable policy ({type(error).__name__})') from None
    if not isinstance(policy, dict) or policy.get('schema') != SCHEMA:
        raise Invalid('not a builder-reclaim policy')
    root = policy.get('root')
    if not (isinstance(root, str) and os.path.isabs(root) and os.path.normpath(root) == root and root != '/'):
        raise Invalid('root must be a normalized absolute path')
    checked = {'root': root, 'lock': lock_path(policy, repo), 'receiptDir': component(policy.get('receiptDir'), 'receiptDir'),
               'trashDir': component(policy.get('trashDir'), 'trashDir'), 'classes': []}
    if checked['receiptDir'] == checked['trashDir']:
        raise Invalid('receiptDir and trashDir must differ')
    sources = policy.get('pinSources')
    if not (isinstance(sources, list) and sources and all(isinstance(s, str) and s and not os.path.isabs(s)
                                                           and '..' not in Path(s).parts for s in sources)):
        raise Invalid('pinSources must list repository-relative paths')
    checked['pinSources'] = sources
    names = set()
    for spec in policy.get('classes') or []:
        if not isinstance(spec, dict) or spec.get('name') in names or not COMPONENT.fullmatch(str(spec.get('name'))):
            raise Invalid('every class needs a unique plain name')
        names.add(spec['name'])
        parent = spec.get('parent', '.')
        if parent != '.':
            component(parent, f'{spec["name"]} parent')
        try:
            entry = re.compile(spec['entry'])
        except (KeyError, TypeError, re.error):
            raise Invalid(f'{spec["name"]} needs a valid entry pattern') from None
        keep_newest = spec.get('keepNewest')
        if not (isinstance(keep_newest, int) and not isinstance(keep_newest, bool) and keep_newest >= 0):
            raise Invalid(f'{spec["name"]} keepNewest must be a whole number')
        if keep_newest and 'run' not in entry.groupindex:
            raise Invalid(f'{spec["name"]} can keep its newest only when its entries carry a run id')
        reclaim = []
        for item in spec.get('reclaim') or []:
            path = item.get('path') if isinstance(item, dict) else None
            keep = item.get('keep', []) if isinstance(item, dict) else None
            if path != '.':
                component(path, f'{spec["name"]} path')
            if not isinstance(keep, list) or (path == '.' and keep):
                raise Invalid(f'{spec["name"]} keep must be a list, and a whole entry keeps nothing')
            reclaim.append({'path': path, 'keep': [component(k, f'{spec["name"]} keep') for k in keep]})
        if not reclaim or (any(r['path'] == '.' for r in reclaim) and len(reclaim) > 1):
            raise Invalid(f'{spec["name"]} must list its paths, or only the whole entry')
        checked['classes'].append({'name': spec['name'], 'parent': parent, 'entry': entry, 'keepNewest': keep_newest,
                                   'reclaim': reclaim})
    if not checked['classes']:
        raise Invalid('the policy has no classes')
    return checked


def pinned_names(policy, repo=ROOT):
    """Every word of the tracked text under pinSources that a class's entry pattern matches whole. An entry named
    anywhere (a bound input, a recovery record, delivery evidence) is kept whole; naming more than needed only keeps."""
    words = set()
    for source in policy['pinSources']:
        base = Path(repo)/source
        if not base.exists():
            raise Invalid(f'pin source {source} is missing')
        for path in sorted(base.rglob('*')) if base.is_dir() else [base]:
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= PIN_FILE_LIMIT:
                words.update(WORD.findall(path.read_text(errors='ignore')))
    return {w for w in words if any(c['entry'].fullmatch(w) for c in policy['classes'])}


def open_root(root):
    """An fd for the storage root, None if it does not exist; Refused if it is anything but a real directory reached
    without any symlink."""
    if os.path.realpath(root) != root:
        raise Refused(f'the storage root path {root} passes through a symlink')
    try:
        return os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise Refused(f'the storage root is not a plain directory ({type(error).__name__})') from None


def open_dir(parent_fd, name, create=False):
    """An fd for parent/name, a real directory on parent's filesystem, or None if absent; Left otherwise."""
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode):
        raise Left('a symlink')
    if not stat.S_ISDIR(st.st_mode):
        raise Left('not a directory')
    if st.st_dev != os.fstat(parent_fd).st_dev:
        raise Left('on another filesystem')
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    if os.fstat(fd).st_ino != st.st_ino:
        os.close(fd)
        raise Left('replaced while it was opened')
    return fd


def candidates(policy, root_fd, current_run, pins):
    """Every entry a class matches, newest first per class, each with the reason it is kept whole or None."""
    rows = []
    for cls in policy['classes']:
        try:
            parent_fd = os.dup(root_fd) if cls['parent'] == '.' else open_dir(root_fd, cls['parent'])
        except Left as left:
            rows.append({'class': cls['name'], 'parent': cls['parent'], 'entry': None, 'kept': f'parent {left}'})
            continue
        if parent_fd is None:
            continue
        try:
            matched = []
            for name in os.listdir(parent_fd):
                match = cls['entry'].fullmatch(name)
                if match:
                    groups = match.groupdict()
                    matched.append((int(groups.get('run') or 0), int(groups.get('attempt') or 0), name))
        finally:
            os.close(parent_fd)
        matched.sort(reverse=True)
        for index, (run, _, name) in enumerate(matched):
            kept = ('pinned by a tracked file' if name in pins
                    else 'the current run' if current_run and run == current_run
                    else f'one of the {cls["keepNewest"]} newest' if index < cls['keepNewest'] else None)
            rows.append({'class': cls['name'], 'parent': cls['parent'], 'entry': name, 'kept': kept})
    return rows


def targets(cls, parent_fd, name):
    """(directory fd, name, path label) for each path of a reclaimable entry, and [(path, reason)] for what is left.
    The caller closes the fds."""
    moves, left, fds = [], [], []
    for item in cls['reclaim']:
        if item['path'] == '.':
            try:
                fd = open_dir(parent_fd, name)
            except Left as reason:
                left.append((name, str(reason)))
                continue
            if fd is not None:
                os.close(fd)
                moves.append((parent_fd, name, name))
            continue
        try:
            entry_fd = open_dir(parent_fd, name)
        except Left as reason:
            left.append((name, str(reason)))
            break
        if entry_fd is None:
            break
        fds.append(entry_fd)
        if not item['keep']:
            present(entry_fd, item['path'], f'{name}/{item["path"]}', moves, left)
            continue
        try:
            sub_fd = open_dir(entry_fd, item['path'])
        except Left as reason:
            left.append((f'{name}/{item["path"]}', str(reason)))
            continue
        if sub_fd is None:
            continue
        fds.append(sub_fd)
        for child in sorted(os.listdir(sub_fd)):
            if child not in item['keep']:
                present(sub_fd, child, f'{name}/{item["path"]}/{child}', moves, left)
    return moves, left, fds


def present(dir_fd, name, label, moves, left):
    """Add dir_fd/name to moves if it exists and is not a symlink (a symlink is left and reported)."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode):
        left.append((label, 'a symlink'))
    else:
        moves.append((dir_fd, name, label))


def move_to_trash(dir_fd, name, trash_fd, trash_name):
    """Rename dir_fd/name, whole, to trash/trash_name: 'moved', 'absent', or Left. A symlink is never moved."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return 'absent'
    if stat.S_ISLNK(st.st_mode):
        raise Left('a symlink')
    if st.st_dev != os.fstat(trash_fd).st_dev:
        raise Left('on another filesystem')
    os.rename(name, trash_name, src_dir_fd=dir_fd, dst_dir_fd=trash_fd)
    return 'moved'


def remove_tree(parent_fd, name, dev):
    """Remove parent/name and everything under it, never following a symlink and never leaving the filesystem dev.
    A directory whose mode forbids removing its entries is made writable by its owner first."""
    st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(st.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        return
    if st.st_dev != dev:
        raise Left('on another filesystem')
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        if os.fstat(fd).st_ino != st.st_ino:
            raise Left('replaced while it was removed')
        if stat.S_IMODE(st.st_mode) & 0o700 != 0o700:
            os.fchmod(fd, stat.S_IMODE(st.st_mode) | 0o700)
        with os.scandir(fd) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    remove_tree(fd, entry.name, dev)
                else:
                    os.unlink(entry.name, dir_fd=fd)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent_fd)


def empty_trash(trash_fd):
    done, left = [], []
    dev = os.fstat(trash_fd).st_dev
    for name in sorted(os.listdir(trash_fd)):
        try:
            remove_tree(trash_fd, name, dev)
            done.append(name)
        except (OSError, Left) as error:
            left.append({'item': name, 'reason': str(error) if isinstance(error, Left) else
                         f'{type(error).__name__} (errno {error.errno})'})
    return done, left


def size_of(dir_fd, name, dev):
    st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    total = st.st_blocks * 512
    if stat.S_ISDIR(st.st_mode) and st.st_dev == dev:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
        try:
            total += sum(size_of(fd, child, dev) for child in os.listdir(fd))
        finally:
            os.close(fd)
    return total


def available(fd):
    s = os.fstatvfs(fd)
    return s.f_bavail * s.f_frsize


def survey(policy, root_fd, current_run, repo, measure=False):
    """The plan: kept entries with their reasons, the paths to move, and what is left alone and why."""
    pins = pinned_names(policy, repo)
    by_name = {c['name']: c for c in policy['classes']}
    plan = {'kept': [], 'reclaim': [], 'left': []}
    for row in candidates(policy, root_fd, current_run, pins):
        if row['kept']:
            plan['kept'].append(row)
            continue
        cls = by_name[row['class']]
        parent_fd = os.dup(root_fd) if cls['parent'] == '.' else open_dir(root_fd, cls['parent'])
        try:
            moves, left, fds = targets(cls, parent_fd, row['entry'])
            try:
                for dir_fd, name, label in moves:
                    path = f'{cls["parent"]}/{label}' if cls['parent'] != '.' else label
                    item = {'class': cls['name'], 'path': path}
                    if measure:
                        item['bytes'] = size_of(dir_fd, name, os.fstat(root_fd).st_dev)
                    plan['reclaim'].append(item)
                plan['left'] += [{'class': cls['name'], 'path': p, 'reason': r} for p, r in left]
            finally:
                for fd in fds:
                    os.close(fd)
        finally:
            os.close(parent_fd)
    return plan


def plan(policy, current_run=None, repo=ROOT, measure=False):
    root_fd = open_root(policy['root'])
    if root_fd is None:
        return {'schema': RECEIPT_SCHEMA, 'mode': 'plan', 'verdict': 'no-storage-root', 'root': policy['root']}
    try:
        result = survey(policy, root_fd, current_run, repo, measure)
        return {'schema': RECEIPT_SCHEMA, 'mode': 'plan', 'verdict': 'planned', 'root': policy['root'],
                'availableBytes': available(root_fd), **result}
    finally:
        os.close(root_fd)


def take_lock(path):
    """An fd holding the builder lock exclusively, or None when another process holds it."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    except OSError as error:
        raise Refused(f'the builder lock {path} cannot be opened ({type(error).__name__})') from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def next_sequence(receipt_fd):
    numbers = [int(n[:4]) for n in os.listdir(receipt_fd) if re.fullmatch(r'\d{4}-.*\.json', n)]
    return max(numbers, default=0) + 1


def apply(policy, label, current_run=None, repo=ROOT, clock=time.time):
    if not LABEL.fullmatch(str(label)):
        raise Invalid('--label must be letters, digits, dot, underscore or hyphen')
    stamp = lambda: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(clock()))
    lock_fd = take_lock(policy['lock'])
    if lock_fd is None:
        return {'schema': RECEIPT_SCHEMA, 'mode': 'apply', 'verdict': 'busy', 'lock': policy['lock'],
                'reason': 'another process holds the builder lock; nothing was reclaimed'}
    try:
        root_fd = open_root(policy['root'])
        if root_fd is None:
            return {'schema': RECEIPT_SCHEMA, 'mode': 'apply', 'verdict': 'no-storage-root', 'root': policy['root']}
        try:
            try:
                trash_fd = open_dir(root_fd, policy['trashDir'], create=True)
                receipt_fd = open_dir(root_fd, policy['receiptDir'], create=True)
            except Left as left:
                raise Refused(f'the trash or receipt directory is {left}') from None
            try:
                return reclaim(policy, label, current_run, repo, root_fd, trash_fd, receipt_fd, stamp)
            finally:
                os.close(trash_fd)
                os.close(receipt_fd)
        finally:
            os.close(root_fd)
    finally:
        os.close(lock_fd)


def reclaim(policy, label, current_run, repo, root_fd, trash_fd, receipt_fd, stamp):
    receipts = Path(policy['root'])/policy['receiptDir']
    sequence = next_sequence(receipt_fd)
    base = f'{sequence:04d}-{label}'
    before = available(root_fd)
    planned = survey(policy, root_fd, current_run, repo)
    record = {'schema': RECEIPT_SCHEMA, 'mode': 'apply', 'label': label, 'root': policy['root'],
              'lock': policy['lock'], 'currentRun': current_run, 'startedAt': stamp(), 'availableBytesBefore': before,
              **planned}
    release_receipts.write_once(receipts/f'{base}.plan.json', record)
    finished, unfinished = empty_trash(trash_fd)
    outcomes, by_name = [], {c['name']: c for c in policy['classes']}
    for row in candidates(policy, root_fd, current_run, pinned_names(policy, repo)):
        if row['kept']:
            continue
        cls = by_name[row['class']]
        parent_fd = os.dup(root_fd) if cls['parent'] == '.' else open_dir(root_fd, cls['parent'])
        try:
            moves, left, fds = targets(cls, parent_fd, row['entry'])
            try:
                for index, (dir_fd, name, path) in enumerate(moves):
                    trash_name = f'{base}-{len(outcomes):05d}'
                    try:
                        outcome = move_to_trash(dir_fd, name, trash_fd, trash_name)
                    except (OSError, Left) as error:
                        outcome = f'left: {error}' if isinstance(error, Left) else \
                            f'left: {type(error).__name__} (errno {error.errno})'
                    outcomes.append({'class': cls['name'], 'path': path, 'outcome': outcome,
                                     'trash': trash_name if outcome == 'moved' else None})
                outcomes += [{'class': cls['name'], 'path': p, 'outcome': f'left: {r}', 'trash': None} for p, r in left]
            finally:
                for fd in fds:
                    os.close(fd)
        finally:
            os.close(parent_fd)
    removed, still = empty_trash(trash_fd)
    gone = set(removed)
    for item in outcomes:
        if item['outcome'] == 'moved':
            item['outcome'] = 'removed' if item['trash'] in gone else 'in trash, not yet removed'
    record.update({'finishedAt': stamp(), 'earlierTrashRemoved': finished, 'items': outcomes,
                   'trashLeft': unfinished + still, 'availableBytesAfter': available(root_fd),
                   'verdict': 'reclaimed' if any(i['outcome'] == 'removed' for i in outcomes) or finished
                   else 'nothing-to-reclaim',
                   'complete': not (unfinished or still or record['left'] or any(i['outcome'] not in ('removed', 'absent')
                                                                             for i in outcomes))})
    release_receipts.write_once(receipts/f'{base}.json', record)
    return record


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['plan', 'apply'])
    p.add_argument('--policy', type=Path, default=POLICY)
    p.add_argument('--root', help='the storage root instead of the policy\'s (tests and inspection only)')
    p.add_argument('--current-run', help='the running workflow run id, whose entries are kept whole')
    p.add_argument('--label', help='apply: the receipt label, e.g. <run id>-<attempt>-<job>')
    p.add_argument('--measure', action='store_true', help='plan: measure each path\'s allocated bytes')
    a = p.parse_args(argv)
    try:
        policy = load_policy(a.policy)
        if a.root:
            if not (os.path.isabs(a.root) and os.path.normpath(a.root) == a.root):
                raise Invalid('--root must be a normalized absolute path')
            policy['root'] = a.root
        current = None
        if a.current_run is not None:
            if not re.fullmatch(r'[1-9][0-9]{0,19}', a.current_run):
                raise Invalid('--current-run must be a run id')
            current = int(a.current_run)
        result = plan(policy, current, measure=a.measure) if a.command == 'plan' \
            else apply(policy, a.label, current)
    except Invalid as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2
    except (Refused, Left, release_receipts.ReceiptExists) as error:
        print(json.dumps({'verdict': 'REFUSED', 'error': str(error)}), file=sys.stderr)
        return 3
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
