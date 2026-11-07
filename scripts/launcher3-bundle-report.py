#!/usr/bin/env python3
"""LAUNCHER3-BUNDLE-REPORT (Release-owned): the producer of the rose-integration-bundle-report v1 that the Launcher3
compile contract's verifyBaseApplyCandidate branch reads (os/compile-contracts/launcher3-quickstep-lib.json
base.bundleReport: "Release's producer script at a reviewed commit; never the adapter, the observer or this checker").
It reads the contract as data at its pin and never loads the adapter (scripts/launcher3-compile.py) or OS Product's
checker (scripts/launcher3-compile-contract.py).

  python3 -I scripts/launcher3-bundle-report.py produce --out NEW_FILE

It runs in isolated mode (-I), so nothing beside it on the path is imported, from a checkout whose HEAD is on
refs/remotes/origin/main (a reviewed commit) and whose run-association document, os/build-only/launcher3-bundle-run.json
(runId, runAttempt, headSha, label, createdAt, updatedAt: Release's file, Infra's values, entered by review from GitHub
run metadata), is exactly its committed blob at HEAD. Every value written comes from that document, the pinned
contract, this checkout's Git history at headSha (no replace objects or grafts, and every object of its store,
alternates included, first verified against its id by git fsck: git does not re-hash what it reads), or the reconcile
receipt directory headSha's own recipe names, never from expected values:
  - run: the document's fields, and bindingSha256, the sha256 of its committed bytes;
  - receipt: exactly one regular *-<label>.json in headSha:os/release-pipeline/frankel.json
    host.integrate.reconcile.receiptDir (absolute) with receiptType reconcile and the run's label, its at within
    [createdAt, updatedAt]; its path, sha256, type, label, at and trusted-patcher record as read, read through the
    opened directory (no link, FIFO or oversized file, no duplicate key). Its entries are association only: where they
    name an observed overlay, the bytes they record as installed must be the overlay's;
  - candidate: commit (headSha, exactly a commit of this checkout), onMain (headSha is an ancestor of this checkout's
    HEAD, producer.roseRevision: the reviewed commit that names it, itself on refs/remotes/origin/main, so the report
    records what onMain was read against; from whole history: a shallow checkout or an unreadable object refuses),
    frankelConfigSha256, applyArgs and patcherKind (the recipe's);
  - registration: source as base.bundleReport.values states it; patcherMatchesPin and applyArgsMatchPin, both of which
    must hold (the contract's names and order are used only then: contractDerived);
  - observed, from headSha's history only (base.bundleContent is never read): patches in base.registration.order, each
    with the sha256 of headSha:os/patches/<name>; overlays, each 100644 headSha:os/overlays file under a required
    prefix (overlayRoles.required.prefixes); notAnInputOverlays, present targets on the exact allowlist; unclassified,
    every other target under the three projects. Bytes a checkout would change (any .gitattributes at headSha's root,
    os/, os/patches or os/overlays, or this repository's own attribute or line-ending configuration) refuse;
  - producer: this script's path, the sha256 of its bytes, which must be its blob at HEAD, and HEAD.
NEW_FILE must not exist; it is written whole or not at all. Reading the receipt directory is a host read, so Infra
runs this, never CI. Nothing but NEW_FILE is written, nothing is fetched and no key or secret is read. Exit status:
0 written (the WRITTEN line names the file, its sha256 and the origin/main commit HEAD was found on), 2 invalid,
3 refused, 4 unbound (the run document is still null).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = 'os/compile-contracts/launcher3-quickstep-lib.json'
# The contract version this producer reads; the adapter pins the same bytes (MANIFEST_SHA256), and the tests hold the
# two pins equal.
CONTRACT_SHA256 = '44471fa60727ede4b0f068e2dc22708f60dbe12814a1620119ef3bc80b33d711'
RUN = 'os/build-only/launcher3-bundle-run.json'
PRODUCER = 'scripts/launcher3-bundle-report.py'
RECIPE = 'os/release-pipeline/frankel.json'
MAIN_REF = 'refs/remotes/origin/main'
REPORT_KIND, REPORT_VERSION = 'rose-integration-bundle-report', 1
RUN_KEYS = ('runId', 'runAttempt', 'headSha', 'label', 'createdAt', 'updatedAt')
RUN_DOCUMENT_KEYS = {'schemaVersion', 'kind', 'basis', *RUN_KEYS}
RECEIPT_BYTES = 16 << 20
TIME = '%Y-%m-%dT%H:%M:%SZ'
LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
COMMIT = re.compile(r'[0-9a-f]{40}')
SHA256 = re.compile(r'[0-9a-f]{64}')
FILE_MODES = ('100644', '100755')
# Attribute files that could make a checkout's bytes differ from the blobs this producer hashes: these, and any under
# ATTRIBUTE_TREES (every directory that holds or leads to a reported file).
ATTRIBUTE_FILES = ('.gitattributes', 'os/.gitattributes')
ATTRIBUTE_TREES = ('os/patches/', 'os/overlays/')
# History is read as committed: no replace objects or grafts, no system or global configuration, no prompt. The graft
# file is a path that cannot exist: git reads none, and prints no deprecation hint (it does for any graft file it reads,
# even an empty one, and a message from git refuses where history must be read whole).
GIT_ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_REPLACE_OBJECTS': '1',
           'GIT_GRAFT_FILE': os.path.join(os.devnull, 'grafts')}


class Invalid(Exception):
    pass


class Unbound(Exception):
    pass


class Refused(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(data):
    """JSON as RFC 8259 has it: no duplicate key (the last of two would win silently), no NaN or Infinity, and no
    nesting deep enough to raise."""
    def pairs(items):
        keys = [k for k, _ in items]
        if len(keys) != len(set(keys)):
            raise ValueError('a duplicate key')
        return dict(items)

    def constant(name):
        raise ValueError(f'{name}, which is not JSON')
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except RecursionError:
        raise ValueError('nested too deeply') from None


def utc(value):
    """Whether value is a UTC second exactly as GitHub run metadata and host-reconcile.py write it."""
    if not isinstance(value, str):
        return False
    try:
        return time.strftime(TIME, time.strptime(value, TIME)) == value
    except ValueError:
        return False


def run_git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, env=GIT_ENV)


def git_message(stderr):
    """git's own reason, for a refusal: its first fatal or error line, else its last line."""
    lines = stderr.decode(errors='replace').strip().splitlines()
    reasons = [line for line in lines if line.startswith(('fatal:', 'error:'))]
    return (reasons or lines[-1:] or [''])[0][:160]


def git(root, *args):
    result = run_git(root, *args)
    if result.returncode:
        raise Refused(f'git {" ".join(args[:2])} failed in {root}: {git_message(result.stderr)}')
    return result.stdout


def is_ancestor(root, commit, of):
    """Whether commit is of or an ancestor of it, from history read whole: any answer but yes or no, or any message
    on the way (git can answer no, or even yes, past an object it could not read), refuses."""
    result = run_git(root, 'merge-base', '--is-ancestor', commit, of)
    if result.returncode not in (0, 1) or result.stderr.strip():
        raise Refused(f'git merge-base could not tell whether {commit[:12]} is on {of[:12]}: '
                      f'{git_message(result.stderr)}')
    return result.returncode == 0


def commit_of(root, rev):
    """The full commit rev names in this checkout, or None (a tree, a blob, a prefix of another length or nothing)."""
    result = run_git(root, 'rev-parse', '--verify', '--quiet', '--end-of-options', f'{rev}^{{commit}}')
    return result.stdout.decode().strip() if result.returncode == 0 else None


def blob(root, rev, path, modes=FILE_MODES):
    """The bytes of rev:path, a regular file (of modes) in this checkout's history."""
    listed = git(root, 'ls-tree', '--full-tree', rev, '--', path)
    mode = listed.split(b' ', 1)[0].decode()
    if not listed or mode not in modes or listed.split(b'\t', 1)[1].rstrip(b'\n') != os.fsencode(path):
        raise Refused(f'{rev[:12]}:{path} is not a regular file in this checkout\'s history')
    return git(root, 'cat-file', 'blob', f'{rev}:{path}')


def tree(root, rev, path=None):
    """{path: (mode, type)} of every entry under path at rev (the whole tree without one)."""
    entries = {}
    for record in git(root, 'ls-tree', '-r', '-z', '--full-tree', rev, *(('--', path) if path else ())).split(b'\0'):
        if record:
            meta, name = record.split(b'\t', 1)
            mode, kind, _ = meta.decode().split(' ')
            entries[os.fsdecode(name)] = (mode, kind)
    return entries


def checked_out_as_committed(root, head):
    """Refuses when a checkout of head could hold other bytes than the blobs reported: a .gitattributes at head's root,
    os/, os/patches or os/overlays (eol, ident, filter and working-tree-encoding all live there), or this repository's
    own attribute file or line-ending configuration. The integrating checkout's own configuration is taken on trust."""
    files = [p for p in tree(root, head) if p.rsplit('/', 1)[-1] == '.gitattributes'
             and (p in ATTRIBUTE_FILES or p.startswith(ATTRIBUTE_TREES))]
    if files:
        raise Refused(f'{head[:12]} has attribute files that can change checked-out bytes: {", ".join(files[:5])}')
    configured = run_git(root, 'config', '--local', '--get-regexp', r'^core\.(autocrlf|eol|attributesfile)$')
    if configured.returncode == 0 and configured.stdout.strip():
        raise Refused('this repository configures line endings or attributes (core.autocrlf, eol or attributesFile)')
    info = Path(root)/git(root, 'rev-parse', '--git-path', 'info/attributes').decode().strip()  # relative to root
    if info.exists() and info.stat().st_size:
        raise Refused(f'this repository has its own {info.name} file')


def load_contract(root=ROOT, pinned=CONTRACT_SHA256):
    data = (Path(root)/CONTRACT).read_bytes()
    if sha256(data) != pinned:
        raise Invalid(f'{CONTRACT} is not the contract version this producer reads ({pinned[:12]})')
    contract = json.loads(data)
    check_contract(contract)
    return contract


def check_contract(contract):
    """The parts of the contract the producer reads, before anything else is read."""
    report = contract['base']['bundleReport']
    if (report['kind'], report['schemaVersion']) != (REPORT_KIND, REPORT_VERSION) \
            or type(report['schemaVersion']) is not int:
        raise Invalid('the contract\'s base.bundleReport is not the rose-integration-bundle-report v1 this producer '
                      'writes')
    prefixes = contract['overlayRoles']['required'].get('prefixes')
    if not (isinstance(prefixes, list) and prefixes and all(isinstance(p, str) and p.endswith('/') and p.strip('/')
                                                            for p in prefixes)):
        raise Invalid('the contract states no required overlay prefixes (overlayRoles.required.prefixes), so '
                      'observed.overlays cannot be computed from data')
    pins = contract['base']['registration']['patcher']
    if pins.get('patcher') != 'trusted' or not pins.get('sha256'):
        raise Invalid('the contract does not pin the trusted patcher')


def registration_source(contract):
    values = contract['base']['bundleReport'].get('values', {})
    source = values.get('registration.source', {}).get('equals', 'base.registration')
    if source != 'base.registration':
        raise Invalid('base.bundleReport.values states a registration.source this producer does not write')
    return source


def load_run(root, head):
    """(the run fields, the sha256 of the document's bytes). The document is exactly its committed blob at head (the
    reviewed one), an exact rose-launcher3-bundle-run v1; Unbound while any field is null."""
    path = Path(root)/RUN
    try:
        data = read_nofollow(path)
    except OSError as error:
        raise Invalid(f'{RUN} cannot be read ({type(error).__name__})') from None
    if data != blob(root, head, RUN, ('100644',)):
        raise Refused(f'{RUN} is not its committed blob at HEAD: only the reviewed document is read')
    try:
        document = strict_json(data)
    except ValueError as error:
        raise Invalid(f'{RUN} is not a JSON document ({error})') from None
    if not isinstance(document, dict) or set(document) != RUN_DOCUMENT_KEYS \
            or type(document['schemaVersion']) is not int \
            or (document['schemaVersion'], document['kind']) != (1, 'rose-launcher3-bundle-run'):
        raise Invalid(f'{RUN} is not an exact rose-launcher3-bundle-run v1 document')
    unbound = [k for k in RUN_KEYS if document[k] is None]
    if unbound:
        raise Unbound(f'the run association is not entered yet: {", ".join(unbound)}')
    run = {k: document[k] for k in RUN_KEYS}
    def count(v):
        return (type(v) is int and v > 0) or (isinstance(v, str) and re.fullmatch(r'[1-9][0-9]*', v))
    if not (count(run['runId']) and count(run['runAttempt']) and isinstance(run['headSha'], str)
            and COMMIT.fullmatch(run['headSha']) and isinstance(run['label'], str) and LABEL.fullmatch(run['label'])
            and utc(run['createdAt']) and utc(run['updatedAt']) and run['createdAt'] <= run['updatedAt']):
        raise Invalid('the run association has a value of the wrong form (run id, attempt, full commit, label, '
                      'UTC times in order)')
    return run, sha256(data)


def read_nofollow(path, dir_fd=None, limit=RECEIPT_BYTES):
    """A regular file's bytes, opened without following a link, without blocking (a FIFO) and through dir_fd when
    given; refused above limit."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise Refused(f'{path} is not a regular file')
        if info.st_size > limit:
            raise Refused(f'{path} is larger than {limit} bytes')
        chunks, total = [], 0
        while True:
            block = os.read(fd, 1 << 20)
            if not block:
                return b''.join(chunks)
            total += len(block)
            if total > limit:
                raise Refused(f'{path} grew past {limit} bytes while read')
            chunks.append(block)
    finally:
        os.close(fd)


def find_receipt(directory, label, created, updated, pinned_files):
    """The one reconcile receipt of label, read through the opened receipt directory, as read: ({path,
    receiptSha256, receiptType, label, at, patcher}, its entries)."""
    if not (isinstance(directory, str) and directory.startswith('/') and os.path.normpath(directory) == directory):
        raise Refused(f'the recipe\'s receiptDir {directory!r} is not a normalized absolute path')
    try:
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise Refused(f'the receipt directory {directory} does not exist') from None
    except OSError:
        raise Refused(f'the receipt directory {directory} is not a plain directory') from None
    try:
        matches = []
        with os.scandir(dir_fd) as entries:
            names = sorted(e.name for e in entries)
        for name in names:
            if not name.endswith(f'-{label}.json') or name == f'-{label}.json':
                continue
            try:
                data = read_nofollow(name, dir_fd)
            except OSError:
                raise Refused(f'{name} in the receipt directory is not a regular file') from None
            try:
                record = strict_json(data)
            except ValueError as error:
                raise Refused(f'{name} in the receipt directory is not a JSON document ({error})') from None
            if not isinstance(record, dict):
                raise Refused(f'{name} in the receipt directory is not a JSON object')
            if record.get('receiptType') == 'reconcile' and record.get('label') == label:
                matches.append((name, data, record))
    finally:
        os.close(dir_fd)
    if len(matches) != 1:
        raise Refused(f'{len(matches)} reconcile receipts of label {label} in {directory}; exactly one is required')
    name, data, record = matches[0]
    if not (utc(record.get('at')) and created <= record['at'] <= updated):
        raise Refused(f'{name}: its at is not within [createdAt, updatedAt] of the run')
    patcher = record.get('patcher')
    if not (isinstance(patcher, dict) and set(patcher) == {'patcher', 'sha256'} and patcher['patcher'] == 'trusted'
            and isinstance(patcher['sha256'], dict) and sorted(patcher['sha256']) == sorted(pinned_files)
            and all(isinstance(v, str) and SHA256.fullmatch(v) for v in patcher['sha256'].values())):
        raise Refused(f'{name} does not record the trusted patcher with its three files\' sha256')
    entries = record.get('entries', {})
    if not isinstance(entries, dict):
        raise Refused(f'{name}: its entries are not an object')
    return ({'path': f'{directory}/{name}', 'receiptSha256': sha256(data), 'receiptType': 'reconcile', 'label': label,
             'at': record['at'], 'patcher': {'patcher': 'trusted', 'sha256': dict(patcher['sha256'])}}, entries)


def observed(contract, root, rev):
    """base.bundleReport.observed from rev's history only; base.bundleContent is never read."""
    roles, registration = contract['overlayRoles'], contract['base']['registration']
    prefixes = tuple(roles['required']['prefixes'])
    patches = [{'project': o['project'], 'name': o['name'],
                'sha256': sha256(blob(root, rev, f'os/patches/{o["name"]}'))} for o in registration['order']]
    projects = tuple(p['path'] for p in contract['projects'])
    allowed = set(roles['notAnInput']['targets'])
    overlays, not_input, unclassified = [], [], []
    for path, (mode, kind) in sorted(tree(root, rev, 'os/overlays').items()):
        target = path[len('os/overlays/'):]
        if not any(target == p or target.startswith(p + '/') for p in projects):
            continue  # outside the three projects: neither observed nor unclassified
        if kind != 'blob' or mode != '100644' or target in projects:
            unclassified.append(target)  # a link, submodule, executable or a file at a project's own path
        elif target.startswith(prefixes):
            overlays.append({'target': target, 'sha256': sha256(blob(root, rev, path, ('100644',)))})
        elif target in allowed:
            not_input.append(target)
        else:
            unclassified.append(target)
    return {'patches': patches, 'overlays': overlays, 'notAnInputOverlays': not_input, 'unclassified': unclassified}


def produce(root=ROOT, contract=None):
    """(the report, the origin/main commit HEAD was found on), every value read as described above."""
    contract = contract or load_contract(root)
    check_contract(contract)
    registration = contract['base']['registration']
    source = registration_source(contract)
    top = git(root, 'rev-parse', '--show-toplevel').decode().strip()
    if os.path.realpath(top) != os.path.realpath(root):
        raise Refused(f'{root} is not the top of its own checkout')
    if git(root, 'rev-parse', '--is-shallow-repository').strip() != b'false':
        raise Refused(f'{root} is a shallow checkout: whether a commit is on {MAIN_REF} needs its whole history')
    verified = run_git(root, 'fsck', '--no-dangling', '--no-progress')
    if verified.returncode:  # git serves an object's bytes without re-hashing them: a damaged store could misstate any
        raise Refused(f'git fsck found this checkout\'s object store damaged: '
                      f'{git_message(verified.stderr + verified.stdout)}')
    revision = commit_of(root, 'HEAD')
    main = commit_of(root, MAIN_REF)
    if revision is None or main is None:
        raise Refused(f'this checkout has no HEAD commit or no {MAIN_REF}')
    own = (Path(root)/PRODUCER).read_bytes()
    if blob(root, revision, PRODUCER) != own:
        raise Refused(f'{PRODUCER} is not the committed producer at this checkout\'s HEAD')
    if not is_ancestor(root, revision, main):
        raise Refused(f'the producer\'s HEAD {revision[:12]} is not on {MAIN_REF}: not a reviewed commit')
    run, binding = load_run(root, revision)
    head = run['headSha']
    if commit_of(root, head) != head:
        raise Refused(f'headSha {head} is not exactly a commit of this checkout (a checkout with that history is '
                      'needed)')
    on_main = is_ancestor(root, head, revision)  # against HEAD, which the report records and which is on main
    checked_out_as_committed(root, head)
    recipe_bytes = blob(root, head, RECIPE)
    try:
        reconcile = strict_json(recipe_bytes)['host']['integrate']['reconcile']
        apply_args, patcher_kind, receipt_dir = reconcile['applyArgs'], reconcile['patcher'], reconcile['receiptDir']
    except (ValueError, KeyError, TypeError):
        raise Refused(f'{head[:12]}:{RECIPE} has no host.integrate.reconcile of the expected shape') from None
    pins = registration['patcher']['sha256']
    files = {name: sha256(blob(root, head, f'scripts/{name}')) for name in sorted(pins)}
    seen = observed(contract, root, head)  # from history, before anything of base.bundleContent (never read)
    receipt, entries = find_receipt(receipt_dir, run['label'], run['createdAt'], run['updatedAt'], pins)
    for overlay in seen['overlays']:  # entries are association only: where they name an overlay, they must agree
        entry = entries.get(overlay['target'])
        if isinstance(entry, dict) and entry.get('action') == 'installed' and entry.get('after') != overlay['sha256']:
            raise Refused(f'the receipt installed other bytes at {overlay["target"]} than headSha\'s overlay')
    patcher_matches = receipt['patcher'] == registration['patcher'] and files == pins
    apply_matches = apply_args == registration['applyArgs']
    if not (patcher_matches and apply_matches):  # contractDerived: the names and order are used only once both hold
        raise Refused('the receipt\'s patcher or the run\'s applyArgs are not the registration\'s pins, so the '
                      'contract\'s patch names and order cannot be used (contractDerived)')
    report = {'kind': REPORT_KIND, 'schemaVersion': REPORT_VERSION,
              'producer': {'path': PRODUCER, 'sha256': sha256(own), 'roseRevision': revision},
              'run': {**run, 'bindingSha256': binding},
              'receipt': receipt,
              'candidate': {'commit': head, 'onMain': on_main, 'frankelConfigSha256': sha256(recipe_bytes),
                            'applyArgs': apply_args, 'patcherKind': patcher_kind},
              'registration': {'source': source, 'patcherMatchesPin': True, 'applyArgsMatchPin': True},
              'observed': seen}
    return report, main


def write_once(path, document):
    """Writes document to the new path whole or not at all (a temporary file beside it, fsync, then a hard link that
    fails if the path exists); returns the sha256 of the bytes written."""
    data = (json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
    path = Path(path)
    staging = path.with_name(f'.{path.name}.{os.getpid()}.new')
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.link(staging, path, follow_symlinks=False)
    finally:
        os.unlink(staging)
    return sha256(data)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('produce',))
    p.add_argument('--out', required=True)
    a = p.parse_args(argv)
    try:
        if not sys.flags.isolated:
            raise Invalid('run the producer as python3 -I, so nothing beside it on the path is imported')
        if not os.path.isabs(a.out) or os.path.normpath(a.out) != a.out:
            raise Invalid('--out must be a normalized absolute path')
        report, main_commit = produce()
        print(json.dumps({'verdict': 'WRITTEN', 'path': a.out, 'sha256': write_once(a.out, report),
                          'originMain': main_commit}))
        return 0
    except Unbound as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'error': str(error)}), file=sys.stderr)
        return 4
    except Refused as error:
        print(json.dumps({'verdict': 'REFUSED', 'error': str(error)}), file=sys.stderr)
        return 3
    except (Invalid, OSError, KeyError, TypeError, ValueError, AttributeError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': f'{type(error).__name__}: {error}'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
