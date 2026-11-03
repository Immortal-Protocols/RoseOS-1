#!/usr/bin/env python3
"""Mustang reconcile seed: start the mustang reconcile receipt chain from the one authentic Frankel
reconcile receipt that explains the isolated copy's integrated bytes, so host-reconcile.py can tell Rose-installed
files from owner changes without restoring anything.

  seed --frankel-receipts DIR --source-root SRC --source-run RUN --source-inventory FILE --source-inventory-sha256 H
       --receipt-dir NEW [--prior-integrations ACTIONS]

The isolated mustang source tree is a byte copy of the Frankel-integrated production tree. host-reconcile.py replaces a
file only when it is pristine (== HEAD), integration-owned, integration-reproduced or task-owned, where task-owned
means equal to the newest receipt's `after` for that path (latest_receipt: the newest NNNN-*.json, whatever its label).
Files Frankel's reconcile installed outside the reproduced frameworks/base and overlay history are owned only through
such a receipt.

A copy that a mustang integration already reconciled no longer matches any Frankel receipt (the reconcile replaced
Rose files with mustang bytes), so a later integration of the same copy - required by any recompile, because compile
admits only an integration of its own commit - must start from that integration's receipt instead. With
--prior-integrations ACTIONS (the rose-actions directory) seed first considers, newest build first, every plain
ACTIONS/mustang-reconcile-<build> directory's own receipt NNNN-mustang-<build>.json (the newest such sequence; directories
that hold only a seeded copy are skipped) and takes the first whose every entry matches SRC exactly; only when none
matches does it fall back to DIR. A link in place of such a directory or receipt refuses.

seed reads DIR (Frankel's own receipt directory, never written) and SRC (read only) and selects, newest sequence first,
the first receipt whose every entry matches SRC exactly: `after` sha256 equal to the regular file there (never a link),
or the path absent where `after` is null. There is no closest match: if no receipt matches entirely, seed refuses and
writes nothing. NEW must not exist; it gets exactly two files:
  0001-<that receipt's label>.json   the receipt's bytes unchanged (renumbered so later mustang receipts sort after it)
  seed-provenance.json               which file it was (kind frankel or mustang, path, original name, sequence, sha256), the copy's source run
                                     and its inventory (path and sha256, which must equal H), how many entries were
                                     checked; a name latest_receipt never reads
It claims only that those bytes were installed by that receipt's reconcile. It decides no ownership itself: host-reconcile.py
still refuses any byte no receipt or rule explains. Nothing is restored, reset, written to SRC or fetched.

Bounds: at most MAX_RECEIPTS receipts of at most MAX_RECEIPT_BYTES each and MAX_ENTRIES entries; files hashed in
streaming blocks. Exit status: 0 seeded, 1 refused, 2 invalid usage.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

RECEIPT = re.compile(r'([0-9]{4})-([A-Za-z0-9._-]{1,64})\.json')
RECEIPT_TYPES = ('reconcile', 'reconcile-adopt')
SHA = re.compile(r'[0-9a-f]{64}')
MAX_RECEIPTS, MAX_RECEIPT_BYTES, MAX_ENTRIES = 512, 2 << 20, 20000
PROVENANCE = 'seed-provenance.json'
PRIOR = re.compile(r'mustang-reconcile-([0-9]{10})')
MAX_PRIOR = 64


class Refused(Exception):
    pass


def plain_dir(path, label):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or path.is_symlink() or not path.is_dir():
        raise Refused(f'{label} must be an existing absolute directory, not a link')
    return path


def read_regular(path, limit=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (limit is not None and info.st_size > limit):
            raise Refused(f'{Path(path).name} is not a regular file within {limit} bytes')
        chunks, size = [], 0
        while True:
            block = os.read(fd, 1 << 20)
            if not block:
                return b''.join(chunks)
            size += len(block)
            if limit is not None and size > limit:
                raise Refused(f'{Path(path).name} grew beyond {limit} bytes')
            chunks.append(block)
    finally:
        os.close(fd)


def file_sha256(path):
    """sha256 of a regular file reached without links below SRC; None when absent; Refused for a link or special."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:
        raise Refused(f'{path} is a link or unreadable')
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Refused(f'{path} is not a regular file')
        digest = hashlib.sha256()
        while True:
            block = os.read(fd, 8 << 20)
            if not block:
                return digest.hexdigest()
            digest.update(block)
    finally:
        os.close(fd)


def no_link_below(root, relative):
    current = root
    for part in PurePosixPath(relative).parts[:-1]:
        current = current/part
        if current.is_symlink():
            return False
    return True


def matches(receipt, source):
    """(True, checked) when every entry matches SOURCE exactly, else (False, first mismatching path)."""
    entries = receipt.get('entries')
    if not isinstance(entries, dict) or len(entries) > MAX_ENTRIES:
        raise Refused('a receipt has no entries object or too many entries')
    for relative, entry in sorted(entries.items()):
        pure = PurePosixPath(relative)
        if pure.is_absolute() or '..' in pure.parts or not pure.parts or not isinstance(entry, dict):
            raise Refused(f'a receipt entry {relative!r} is not a relative source path')
        after = entry.get('after')
        if after is not None and not (isinstance(after, str) and SHA.fullmatch(after)):
            raise Refused(f'a receipt entry {relative!r} has no sha256 after')
        if not no_link_below(source, relative):
            return False, relative
        if file_sha256(source/relative) != after:
            return False, relative
    return True, len(entries)


def prior_receipts(prior_root, out):
    """[(build, receipt path)] of completed mustang integrations' own receipts, newest build first: in each plain
    ACTIONS/mustang-reconcile-<build> directory the newest NNNN-mustang-<build>.json. A directory holding only a seeded copy
    has none and is skipped; a link in place of a directory or receipt refuses."""
    root = plain_dir(prior_root, '--prior-integrations')
    found = []
    entries = [e for e in os.scandir(root) if PRIOR.fullmatch(e.name) and root/e.name != out]
    if len(entries) > MAX_PRIOR:
        raise Refused(f'more than {MAX_PRIOR} prior mustang integrations; refusing an unbounded search')
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            raise Refused(f'{entry.name} is not a plain directory (a link or another file type)')
        build = PRIOR.fullmatch(entry.name).group(1)
        own = [e for e in os.scandir(root/entry.name) if RECEIPT.fullmatch(e.name)
               and RECEIPT.fullmatch(e.name).group(2) == f'mustang-{build}']
        if any(e.is_symlink() or not e.is_file(follow_symlinks=False) for e in own):
            raise Refused(f'{entry.name} holds a receipt that is not a regular file (a link or another file type)')
        names = [e.name for e in own]
        if len(names) > MAX_RECEIPTS:
            raise Refused(f'{entry.name} holds more than {MAX_RECEIPTS} receipts')
        if names:
            found.append((build, root/entry.name/max(names)))
    return sorted(found, reverse=True)


def load_receipt(path):
    data = read_regular(path, MAX_RECEIPT_BYTES)
    try:
        receipt = json.loads(data)
    except ValueError:
        raise Refused(f'{path.name} is not JSON')
    if not isinstance(receipt, dict) or receipt.get('receiptType') not in RECEIPT_TYPES:
        raise Refused(f'{path.name} is not a reconcile receipt')
    sequence, label = RECEIPT.fullmatch(path.name).groups()
    if receipt.get('label') != label:
        raise Refused(f'{path.name} names label {receipt.get("label")!r}, not its file label')
    return data, receipt, int(sequence), label


def commit(out, data, label, provenance):
    """Write NEW/0001-<label>.json (the receipt's bytes unchanged) and the provenance, or nothing."""
    digest = hashlib.sha256(data).hexdigest()
    out.mkdir(mode=0o700)
    try:
        target = out/f'0001-{label}.json'
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        if hashlib.sha256(read_regular(target)).hexdigest() != digest:
            raise Refused('the seeded receipt changed while being written')
        provenance = {**provenance, 'seededAs': target.name}
        provenance['source']['sha256'] = digest
        fd = os.open(out/PROVENANCE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(provenance, indent=2, sort_keys=True) + '\n')
    except BaseException:
        for child in out.iterdir():
            child.unlink()
        out.rmdir()
        raise
    return provenance


def seed(frankel_receipts, source_root, source_run, receipt_dir, inventory, inventory_sha256, prior_integrations=None):
    receipts_dir = plain_dir(frankel_receipts, '--frankel-receipts')
    source = plain_dir(source_root, '--source-root')
    if not (isinstance(source_run, str) and source_run.isdigit() and source_run[0] != '0' and len(source_run) <= 20):
        raise Refused('--source-run must be the numeric run that made the copy')
    if not (isinstance(inventory_sha256, str) and SHA.fullmatch(inventory_sha256)):
        raise Refused('--source-inventory-sha256 must be a lowercase sha256')
    inventory = Path(inventory)
    if not inventory.is_absolute() or '..' in inventory.parts:
        raise Refused('--source-inventory must be an absolute path')
    if file_sha256(inventory) != inventory_sha256:  # streamed (the copy inventory is ~300 MB), no link followed
        raise Refused('--source-inventory is not the named copy inventory (missing, a link, or sha256 differs)')
    out = Path(receipt_dir)
    if not out.is_absolute() or '..' in out.parts or os.path.lexists(out):
        raise Refused('--receipt-dir must be a new absolute path')
    if not out.parent.is_dir() or out.parent.is_symlink():
        raise Refused(f'{out.parent} must be an existing directory, not a link')
    if source in (out, *out.parents) or receipts_dir in (out, *out.parents):
        raise Refused('--receipt-dir must be outside the source tree and the Frankel receipt directory')
    base = {'schema': 'roseos.mustang-reconcile-seed.v1', 'sourceRun': source_run, 'sourceRoot': str(source),
            'sourceInventory': {'path': str(inventory), 'sha256': inventory_sha256}}
    misses = []
    for build, path in (prior_receipts(prior_integrations, out) if prior_integrations is not None else []):
        data, receipt, sequence, label = load_receipt(path)
        ok, detail = matches(receipt, source)
        if not ok:
            misses.append({'kind': 'mustang', 'receipt': str(path), 'firstMismatch': detail})
            continue
        return commit(out, data, label, {**base, 'entriesChecked': detail, 'newerReceiptsNotMatching': misses,
            'source': {'kind': 'mustang', 'build': build, 'path': str(path), 'name': path.name, 'sequence': sequence,
                       'label': label},
            'claim': 'every entry of this mustang reconcile receipt (a completed integration of this copy) matches the '
                     'isolated copy exactly; nothing else is claimed'})
    names = sorted(e.name for e in os.scandir(receipts_dir) if RECEIPT.fullmatch(e.name))
    if not names:
        raise Refused('the Frankel receipt directory holds no NNNN-label.json receipt')
    if len(names) > MAX_RECEIPTS:
        raise Refused(f'more than {MAX_RECEIPTS} receipts; refusing an unbounded search')
    for name in reversed(names):  # newest sequence first, as latest_receipt orders them
        data, receipt, sequence, label = load_receipt(receipts_dir/name)
        ok, detail = matches(receipt, source)
        if not ok:
            misses.append({'receipt': name, 'firstMismatch': detail})
            continue
        return commit(out, data, label, {**base, 'entriesChecked': detail, 'newerReceiptsNotMatching': misses,
            'source': {'kind': 'frankel', 'path': str(receipts_dir/name), 'name': name, 'sequence': sequence,
                       'label': label},
            'claim': 'every entry of this Frankel reconcile receipt matches the isolated copy exactly; '
                     'nothing else is claimed'})
    raise Refused('no mustang integration receipt and no Frankel reconcile receipt matches the copy entirely: ' +
                  ', '.join(f'{m["receipt"]} at {m["firstMismatch"]}' for m in misses[:5]))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('seed',))
    for flag in ('--frankel-receipts', '--source-root', '--source-run', '--source-inventory', '--source-inventory-sha256',
                 '--receipt-dir'):
        p.add_argument(flag, required=True)
    p.add_argument('--prior-integrations', help='the rose-actions directory whose completed mustang integrations come first')
    a = p.parse_args(argv)
    try:
        record = seed(a.frankel_receipts, a.source_root, a.source_run, a.receipt_dir, a.source_inventory,
                      a.source_inventory_sha256, a.prior_integrations)
    except Refused as error:
        print(json.dumps({'verdict': 'REFUSED', 'error': str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({'verdict': 'SEEDED', **record}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
