#!/usr/bin/env python3
"""LAUNCHER3-BUNDLE-REPORT (Release-owned): the producer of the rose-integration-bundle-report v1 that the Launcher3
compile contract's verifyBaseApplyCandidate branch reads (os/compile-contracts/launcher3-quickstep-lib.json
base.bundleReport: "Release's producer script at a reviewed commit; never the adapter, the observer or this checker").
It reads the contract as data at its pin and never loads the adapter (scripts/launcher3-compile.py) or OS Product's
checker (scripts/launcher3-compile-contract.py).

  produce --run FILE --out NEW_FILE [--main-ref REF]

FILE is the reviewed run-association document (os/build-only/launcher3-bundle-run.json: runId, runAttempt, headSha,
label, createdAt, updatedAt), entered by review from GitHub run metadata with no live API and no actions:read. Every
value written comes from that document, the pinned contract, this checkout's Git history at headSha, or the reconcile
receipt directory headSha's own recipe names, never from expected values:
  - run: the document's fields, and bindingSha256, the sha256 of its bytes;
  - receipt: exactly one *-<label>.json in headSha:os/release-pipeline/frankel.json host.integrate.reconcile.receiptDir
    with receiptType reconcile and the run's label, its at within [createdAt, updatedAt]; its path, sha256, type,
    label, at and trusted-patcher record as read (its changed and entries are association only and never copied);
  - candidate: commit (headSha, a commit of this checkout), onMain (headSha is an ancestor of REF, origin/main by
    default), frankelConfigSha256 (headSha's frankel.json bytes), applyArgs and patcherKind (that file's
    host.integrate.reconcile.applyArgs and patcher);
  - registration: source "base.registration"; patcherMatchesPin when the receipt's patcher, headSha's three patcher
    files and base.registration.patcher are the same trusted patcher; applyArgsMatchPin when the applyArgs equal
    base.registration.applyArgs;
  - observed, from headSha's history only (base.bundleContent is never read): patches in base.registration.order, each
    with the sha256 of headSha:os/patches/<name>; overlays, each headSha:os/overlays file whose target is under a
    required prefix (overlayRoles.required.prefixes), with its sha256; notAnInputOverlays, the present targets on the
    exact allowlist (overlayRoles.notAnInput.targets); unclassified, every other target under the three projects;
  - producer: this script's path, the sha256 of its bytes, which must be its blob at this checkout's HEAD, and HEAD.
NEW_FILE must not exist and is written once. Reading the receipt directory is a host read, so Infra runs this, never
CI. Nothing but NEW_FILE is written, nothing is fetched and no key or secret is read. Exit status: 0 written,
2 invalid, 3 refused, 4 unbound (the run document is still null).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = 'os/compile-contracts/launcher3-quickstep-lib.json'
# The contract version this producer reads; the adapter pins the same bytes (MANIFEST_SHA256), and the tests hold the
# two pins equal.
CONTRACT_SHA256 = '466f5ab88e4c110318e6e2533635db9bfcbf6ff73fc40dc54fbcc4c72f0593b4'
RUN = 'os/build-only/launcher3-bundle-run.json'
PRODUCER = 'scripts/launcher3-bundle-report.py'
RECIPE = 'os/release-pipeline/frankel.json'
REPORT_KIND, REPORT_VERSION = 'rose-integration-bundle-report', 1
RUN_KEYS = ('runId', 'runAttempt', 'headSha', 'label', 'createdAt', 'updatedAt')
RUN_DOCUMENT_KEYS = {'schemaVersion', 'kind', 'basis', *RUN_KEYS}
TIME = '%Y-%m-%dT%H:%M:%SZ'
LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
COMMIT = re.compile(r'[0-9a-f]{40}')
SHA256 = re.compile(r'[0-9a-f]{64}')
OVERLAY_MODES = ('100644', '100755')
GIT_ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}


class Invalid(Exception):
    pass


class Unbound(Exception):
    pass


class Refused(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_nofollow(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        chunks = []
        while True:
            block = os.read(fd, 1 << 20)
            if not block:
                return b''.join(chunks)
            chunks.append(block)
    finally:
        os.close(fd)


def utc(value):
    """Whether value is a UTC second exactly as GitHub run metadata and host-reconcile.py write it."""
    if not isinstance(value, str):
        return False
    try:
        return time.strftime(TIME, time.strptime(value, TIME)) == value
    except ValueError:
        return False


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, env=GIT_ENV)
    if result.returncode:
        raise Refused(f'git {" ".join(args[:2])} failed in {root}')
    return result.stdout


def blob(root, rev, path):
    """The bytes of rev:path, a regular file in this checkout's history."""
    mode = git(root, 'ls-tree', '--full-tree', rev, '--', path).split(b' ', 1)[0].decode()
    if mode not in OVERLAY_MODES:
        raise Refused(f'{rev[:12]}:{path} is not a regular file in this checkout\'s history')
    return git(root, 'cat-file', 'blob', f'{rev}:{path}')


def tree(root, rev, path):
    """{path: (mode, type)} of every entry under path at rev."""
    entries = {}
    for record in git(root, 'ls-tree', '-r', '-z', '--full-tree', rev, '--', path).split(b'\0'):
        if record:
            meta, name = record.split(b'\t', 1)
            mode, kind, _ = meta.decode().split(' ')
            entries[os.fsdecode(name)] = (mode, kind)
    return entries


def load_contract(root=ROOT, pinned=CONTRACT_SHA256):
    data = (Path(root)/CONTRACT).read_bytes()
    if sha256(data) != pinned:
        raise Invalid(f'{CONTRACT} is not the contract version this producer reads ({pinned[:12]})')
    contract = json.loads(data)
    report = contract['base']['bundleReport']
    if (report['kind'], report['schemaVersion']) != (REPORT_KIND, REPORT_VERSION):
        raise Invalid('the contract\'s base.bundleReport is not the rose-integration-bundle-report v1 this producer writes')
    return contract


def load_run(path):
    """(the run fields, the sha256 of the document's bytes). Unbound while any field is null."""
    data = read_nofollow(path)
    document = json.loads(data)
    if not isinstance(document, dict) or set(document) != RUN_DOCUMENT_KEYS or \
            (document['schemaVersion'], document['kind']) != (1, 'rose-launcher3-bundle-run'):
        raise Invalid(f'{path} is not an exact rose-launcher3-bundle-run v1 document')
    unbound = [k for k in RUN_KEYS if document[k] is None]
    if unbound:
        raise Unbound(f'the run association is not entered yet: {", ".join(unbound)}')
    run = {k: document[k] for k in RUN_KEYS}
    count = lambda v: (type(v) is int and v > 0) or (isinstance(v, str) and re.fullmatch(r'[1-9][0-9]*', v))  # noqa: E731
    if not (count(run['runId']) and count(run['runAttempt']) and isinstance(run['headSha'], str)
            and COMMIT.fullmatch(run['headSha']) and isinstance(run['label'], str) and LABEL.fullmatch(run['label'])
            and utc(run['createdAt']) and utc(run['updatedAt']) and run['createdAt'] <= run['updatedAt']):
        raise Invalid('the run association has a value of the wrong form (run id, attempt, full commit, label, '
                      'UTC times in order)')
    return run, sha256(data)


def find_receipt(directory, label, created, updated):
    """The one reconcile receipt of label, as read: {path, receiptSha256, receiptType, label, at, patcher}."""
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise Refused(f'the receipt directory {directory} is not a plain directory')
    matches = []
    for entry in sorted(os.scandir(directory), key=lambda e: e.name):
        if not entry.name.endswith(f'-{label}.json') or entry.name == f'-{label}.json':
            continue
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
            raise Refused(f'{entry.name} in the receipt directory is not a regular file')
        data = read_nofollow(entry.path)
        try:
            record = json.loads(data)
        except ValueError:
            raise Refused(f'{entry.name} in the receipt directory is not JSON') from None
        if isinstance(record, dict) and record.get('receiptType') == 'reconcile' and record.get('label') == label:
            matches.append((entry.path, data, record))
    if len(matches) != 1:
        raise Refused(f'{len(matches)} reconcile receipts of label {label} in {directory}; exactly one is required')
    path, data, record = matches[0]
    if not (utc(record.get('at')) and created <= record['at'] <= updated):
        raise Refused(f'{Path(path).name}: its at is not within [createdAt, updatedAt] of the run')
    patcher = record.get('patcher')
    if not (isinstance(patcher, dict) and set(patcher) == {'patcher', 'sha256'} and patcher['patcher'] == 'trusted'
            and isinstance(patcher['sha256'], dict)
            and all(isinstance(v, str) and SHA256.fullmatch(v) for v in patcher['sha256'].values())):
        raise Refused(f'{Path(path).name} does not record the trusted patcher with its files\' sha256')
    return {'path': os.path.normpath(os.path.abspath(path)), 'receiptSha256': sha256(data), 'receiptType': 'reconcile',
            'label': label, 'at': record['at'], 'patcher': {'patcher': 'trusted', 'sha256': dict(patcher['sha256'])}}


def observed(contract, root, rev):
    """base.bundleReport.observed from rev's history only; base.bundleContent is never read."""
    roles, registration = contract['overlayRoles'], contract['base']['registration']
    prefixes = roles['required'].get('prefixes')
    if not (isinstance(prefixes, list) and prefixes and all(isinstance(p, str) and p.endswith('/') for p in prefixes)):
        raise Invalid('the contract states no required overlay prefixes (overlayRoles.required.prefixes), so '
                      'observed.overlays cannot be computed from data')
    patches = [{'project': o['project'], 'name': o['name'], 'sha256': sha256(blob(root, rev, f'os/patches/{o["name"]}'))}
               for o in registration['order']]
    projects = tuple(p['path'] + '/' for p in contract['projects'])
    allowed = set(roles['notAnInput']['targets'])
    overlays, not_input, unclassified = [], [], []
    for path, (mode, kind) in sorted(tree(root, rev, 'os/overlays').items()):
        target = path[len('os/overlays/'):]
        if not target.startswith(projects):
            continue  # outside the three projects: neither observed nor unclassified
        if kind != 'blob' or mode not in OVERLAY_MODES:
            unclassified.append(target)  # a link or a submodule is never an overlay of these projects
        elif target.startswith(tuple(prefixes)):
            overlays.append({'target': target, 'sha256': sha256(blob(root, rev, path))})
        elif target in allowed:
            not_input.append(target)
        else:
            unclassified.append(target)
    return {'patches': patches, 'overlays': overlays, 'notAnInputOverlays': not_input, 'unclassified': unclassified}


def produce(run_path, root=ROOT, main_ref='origin/main', contract=None, receipt_dir=None):
    """The report, every value read as described above. receipt_dir is only for tests: it otherwise comes from
    headSha's recipe."""
    contract = contract or load_contract(root)
    registration = contract['base']['registration']
    run, binding = load_run(run_path)
    head = run['headSha']
    kind = subprocess.run(['git', '-C', str(root), 'cat-file', '-t', head], env=GIT_ENV, capture_output=True)
    if kind.returncode or kind.stdout.strip() != b'commit':
        raise Refused(f'headSha {head} is not a commit of this checkout (a checkout with that history is needed)')
    ancestor = subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', head, main_ref], env=GIT_ENV,
                              capture_output=True).returncode
    if ancestor not in (0, 1):
        raise Refused(f'{main_ref} is not a ref of this checkout')
    recipe_bytes = blob(root, head, RECIPE)
    reconcile = json.loads(recipe_bytes)['host']['integrate']['reconcile']
    receipt = find_receipt(receipt_dir or reconcile['receiptDir'], run['label'], run['createdAt'], run['updatedAt'])
    pins = registration['patcher']['sha256']
    files = {name: sha256(blob(root, head, f'scripts/{name}')) for name in sorted(pins)}
    seen = observed(contract, root, head)  # from history, before anything of base.bundleContent (never read)
    own = (Path(root)/PRODUCER).read_bytes()
    if blob(root, 'HEAD', PRODUCER) != own:
        raise Refused(f'{PRODUCER} is not the committed producer at this checkout\'s HEAD')
    return {'kind': REPORT_KIND, 'schemaVersion': REPORT_VERSION,
            'producer': {'path': PRODUCER, 'sha256': sha256(own), 'roseRevision': git(root, 'rev-parse', 'HEAD').decode().strip()},
            'run': {**run, 'bindingSha256': binding},
            'receipt': receipt,
            'candidate': {'commit': head, 'onMain': ancestor == 0, 'frankelConfigSha256': sha256(recipe_bytes),
                          'applyArgs': reconcile['applyArgs'], 'patcherKind': reconcile['patcher']},
            'registration': {'source': 'base.registration',
                             'patcherMatchesPin': receipt['patcher'] == registration['patcher'] and files == pins,
                             'applyArgsMatchPin': reconcile['applyArgs'] == registration['applyArgs']},
            'observed': seen}


def write_once(path, document):
    data = (json.dumps(document, indent=2, sort_keys=True) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    return sha256(data)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('produce',))
    p.add_argument('--run', default=str(ROOT/RUN))
    p.add_argument('--out', required=True)
    p.add_argument('--main-ref', default='origin/main')
    a = p.parse_args(argv)
    try:
        if not os.path.isabs(a.out):
            raise Invalid('--out must be an absolute path')
        report = produce(a.run, main_ref=a.main_ref)
        print(json.dumps({'verdict': 'WRITTEN', 'path': a.out, 'sha256': write_once(a.out, report)}))
        return 0
    except Unbound as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'error': str(error)}), file=sys.stderr)
        return 4
    except Refused as error:
        print(json.dumps({'verdict': 'REFUSED', 'error': str(error)}), file=sys.stderr)
        return 3
    except (Invalid, FileExistsError, KeyError, ValueError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': f'{type(error).__name__}: {error}'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
