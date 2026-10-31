#!/usr/bin/env python3
"""os-release host.integrate.reconcileCommand: bring the retained, owner-modified OS tree to the candidate state.

The candidate's own scripts/apply-os-changes.py runs against a shadow source root made of shared, sparse clones
of the touched projects at their current HEAD (the retained repositories are only read). Every file that run
changes is then classified against the retained tree:
  already identical                      -> nothing to do
  pristine (== HEAD, or absent like HEAD) -> install the candidate bytes (fresh mtime, backup, receipt)
  equal to the previous task receipt     -> task-owned, replace it
  under an integration-owned root        -> Rose-only directory, replace it
  equal to an earlier Rose patch output  -> integration-reproduced, replace it (see below)
  anything else                          -> owner change: CONFLICT, nothing is written
A receipt records only files a run changed, so a frameworks/base file the retained tree already held in Rose's patched
form before reconcile existed (and that every candidate since reproduced byte for byte) is in no receipt; the first
candidate that changes it would otherwise read as an owner change. For such a file only, the bytes Rose's own
frameworks/base patch sequence produced at each earlier version of the candidate repository's os/patches (its git
history, at most HISTORY_LIMIT versions) are recomputed from the project HEAD copy; when the retained bytes equal one,
the file is replaced like a task-owned one and the receipt names the baseline commit. Anything else still conflicts.
Files a previous receipt installed that the candidate no longer changes are restored to HEAD on the same terms.
Owner changes are never reset or wiped and candidate changes are never skipped. `adopt` records the current bytes of
conflicting files as a task-owned baseline once, explicitly and with a reason (e.g. the hand-made P1 integration);
`--path` limits that to the named conflicting files (e.g. a makefile that differs only in package order).
Receipts are immutable and sequenced in the receipt directory. Exit status: 0 ok, 1 conflict or failure, 2 usage.

The patcher is the candidate's own by default. `patcher: trusted` is a recipe fix for an already-pinned candidate: this
checkout's apply-os-changes.py (and its two helpers) runs against the candidate's own os/ bundle (pinned manifest,
patches, overlays), so the product content stays the candidate's; the patcher hashes are recorded in the receipt.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_receipts  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PATCHER = ('apply-os-changes.py', 'background_apks.py', 'integration_copy.py')

class Conflict(Exception):
    pass

def sha(data):
    return None if data is None else hashlib.sha256(data).hexdigest()

def read(path):
    return Path(path).read_bytes() if Path(path).is_file() and not Path(path).is_symlink() else None

def git(*args, **kwargs):
    return subprocess.run(['git', *map(str, args)], check=True, capture_output=True, **kwargs).stdout

def head_bytes(source, project, relative):
    result = subprocess.run(['git', '-C', str(Path(source)/project), 'show', f'HEAD:{relative}'], capture_output=True)
    return result.stdout if result.returncode == 0 else None

# apply-os-changes.py's frameworks/base order for a product build with a background policy (its initial list, then
# background_apks.BACKGROUND_FRAMEWORK_PATCHES); scripts/tests/test_host_build_reconcile.py keeps the two in step.
FRAMEWORKS_BASE_SEQUENCE = ('frameworks-base.patch', 'rose-agent-self-update-frameworks-base.patch',
    'background-control-frameworks-base.patch', 'background-control-modern-frameworks-base.patch',
    'background-control-resource-import-fix.patch', 'locked-control-frameworks-base.patch')
HISTORY_LIMIT = 64

def historical_outputs(source, candidate, project, relative, limit=HISTORY_LIMIT):
    """{sha256: commit} of the bytes `relative` had after Rose's frameworks/base patch sequence at each of the last
    `limit` commits of the candidate repository that changed os/patches, recomputed from the project's HEAD copy with
    only that file's hunks (git apply --include). Another project, a file HEAD lacks, a patch outside the sequence
    that touches the file, or a version whose patches do not apply yields nothing for it."""
    base = head_bytes(source, project, relative) if project == 'frameworks/base' else None
    if base is None:
        return {}
    log = subprocess.run(['git', '-C', str(candidate), 'log', '--format=%H', f'-n{limit}', '--', 'os/patches'],
        capture_output=True, text=True)
    outputs = {}
    for commit in log.stdout.split() if log.returncode == 0 else []:
        listed = subprocess.run(['git', '-C', str(candidate), 'ls-tree', '--name-only', f'{commit}:os/patches'],
            capture_output=True, text=True)
        names = [n for n in listed.stdout.split() if n.endswith('.patch')] if listed.returncode == 0 else []
        patches = {}
        for name in names:
            text = subprocess.run(['git', '-C', str(candidate), 'show', f'{commit}:os/patches/{name}'],
                capture_output=True).stdout
            if f'+++ b/{relative}'.encode() in text:
                patches[name] = text
        if not patches or set(patches) - set(FRAMEWORKS_BASE_SEQUENCE):
            continue
        with tempfile.TemporaryDirectory(prefix='reconcile-history-') as directory:
            tree = Path(directory)
            subprocess.run(['git', 'init', '-q', str(tree)], check=True)
            (tree/relative).parent.mkdir(parents=True, exist_ok=True)
            (tree/relative).write_bytes(base)
            applied = True
            for name in FRAMEWORKS_BASE_SEQUENCE:
                if name in patches:
                    patch = tree.parent/f'{tree.name}.{name}'
                    patch.write_bytes(patches[name])
                    try:
                        result = subprocess.run(['git', '-C', str(tree), 'apply', f'--include={relative}', str(patch)],
                            capture_output=True)
                    finally:
                        patch.unlink()
                    if result.returncode:
                        applied = False
                        break
            if applied:
                outputs.setdefault(sha((tree/relative).read_bytes()), commit)
    return outputs

def sparse_paths(candidate, project, extra):
    paths = set(extra.get(project, []))
    for patch in sorted((Path(candidate)/'os/patches').glob('*.patch')):
        for line in patch.read_text(errors='replace').splitlines():
            if line.startswith('--- a/') or line.startswith('+++ b/'):
                path = line[6:].split('\t')[0]
                # Patches name project-relative paths; keep those that exist in this project at HEAD.
                paths.add(path)
    return sorted(paths)

def build_shadow(source, candidate, shadow, projects, extra):
    for project in projects:
        origin, clone = Path(source)/project, Path(shadow)/project
        head = git('-C', origin, 'rev-parse', 'HEAD', text=True).strip()
        clone.parent.mkdir(parents=True, exist_ok=True)
        git('clone', '--quiet', '--shared', '--no-checkout', origin, clone)
        git('-C', clone, 'sparse-checkout', 'set', '--no-cone', *sparse_paths(candidate, project, extra))
        git('-C', clone, 'checkout', '--quiet', '--detach', head)

def desired_changes(shadow, projects):
    """Relative paths (to the source root) the candidate run changed, with their new bytes and mode (None = deleted)."""
    changes = {}
    for project in projects:
        clone = Path(shadow)/project
        status = git('-C', clone, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
        for entry in filter(None, status.split(b'\0')):
            code, relative = entry[:2].decode(), entry[3:].decode()
            path = clone/relative
            changes[f'{project}/{relative}'] = (None, None) if 'D' in code else (path.read_bytes(), path.stat().st_mode & 0o777)
    project_roots = {Path(shadow)/p for p in projects}
    for path in Path(shadow).rglob('*'):
        if path.is_file() and not any(r == path or r in path.parents for r in project_roots):
            changes[str(path.relative_to(shadow))] = (path.read_bytes(), path.stat().st_mode & 0o777)
    return changes

def project_of(relative, projects):
    return next((p for p in projects if relative.startswith(p + '/')), None)

def latest_receipt(receipt_dir):
    receipts = sorted(Path(receipt_dir).glob('[0-9][0-9][0-9][0-9]-*.json'))
    return (json.loads(receipts[-1].read_text()), len(receipts)) if receipts else ({'entries': {}}, 0)

def plan(source, projects, owned_roots, changes, previous, reproduced=None):
    """(actions, conflicts). `reproduced` maps a path to {sha256: commit} of earlier Rose patch outputs for it
    (historical_outputs); current bytes equal to one of them are integration output, not an owner change."""
    owned = {path: entry['after'] for path, entry in previous.get('entries', {}).items()}
    reproduced = reproduced or {}
    actions, conflicts = {}, []
    targets = dict(changes)
    for path in owned:
        if path not in targets:
            project = project_of(path, projects)
            data = head_bytes(source, project, path[len(project) + 1:]) if project else None
            targets[path] = (data, None)  # restore HEAD (or remove) what only this task had installed
    for path, (desired, mode) in sorted(targets.items()):
        current = read(Path(source)/path)
        if sha(current) == sha(desired):
            continue
        project = project_of(path, projects)
        if project is None:
            pristine = current is None  # nothing there yet outside any git project
        else:
            pristine = sha(current) == sha(head_bytes(source, project, path[len(project) + 1:]))
        task_owned = path in owned and owned[path] == sha(current)
        integration_owned = any(path.startswith(r.rstrip('/') + '/') for r in owned_roots)
        integration_reproduced = sha(current) in reproduced.get(path, {})
        if pristine or task_owned or integration_owned or integration_reproduced:
            actions[path] = (current, desired, mode)
        else:
            conflicts.append(path)
    return actions, conflicts

def write_atomic(target, data, mode):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + target.name + '.', dir=target.parent)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    os.chmod(temporary, mode or 0o644)
    os.replace(temporary, target)
    os.utime(target)

def prepare_patcher(candidate, choice, workdir):
    """(apply-os-changes.py to run, receipt entry). 'trusted' copies this checkout's patcher into workdir beside a link
    to the candidate's os/ bundle, which the patcher reads relative to its own path."""
    if choice == 'candidate':
        script = Path(candidate)/'scripts/apply-os-changes.py'
        return script, {'patcher': 'candidate', 'sha256': {script.name: sha(script.read_bytes())}}
    if choice != 'trusted':
        raise Conflict(f'unknown patcher {choice!r}; expected candidate or trusted')
    (Path(workdir)/'scripts').mkdir(parents=True)
    for name in PATCHER:
        shutil.copyfile(ROOT/'scripts'/name, Path(workdir)/'scripts'/name)
    os.symlink(Path(candidate).resolve()/'os', Path(workdir)/'os')
    return Path(workdir)/'scripts/apply-os-changes.py', {'patcher': 'trusted',
        'sha256': {name: sha((ROOT/'scripts'/name).read_bytes()) for name in PATCHER}}

def reconcile(source, candidate, release_dir, apply_args, receipt_dir, projects, owned_roots, extra, label, mode='apply',
              reason=None, run=subprocess.run, patcher='candidate', only=None):
    source, receipt_dir = Path(source), Path(receipt_dir)
    receipt_dir.mkdir(parents=True, exist_ok=True)
    shadow, workdir = Path(release_dir)/'reconcile-shadow', Path(release_dir)/'reconcile-patcher'
    for leftover in (shadow, workdir):
        if leftover.exists():
            raise Conflict(f'{leftover} exists from an earlier run; inspect and remove it deliberately')
    try:
        script, patcher_receipt = prepare_patcher(candidate, patcher, workdir)
        build_shadow(source, candidate, shadow, projects, extra)
        result = run([sys.executable, str(script), str(shadow), *apply_args],
            cwd=str(candidate), capture_output=True, text=True)
        if result.returncode:
            raise Conflict('candidate apply-os-changes failed on the shadow tree: ' + (result.stdout + result.stderr).strip()[-600:])
        previous, count = latest_receipt(receipt_dir)
        changes = desired_changes(shadow, projects)
        actions, conflicts = plan(source, projects, owned_roots, changes, previous)
        reproduced = {}
        if conflicts and mode == 'apply':
            for path in conflicts:
                project = project_of(path, projects)
                found = historical_outputs(source, candidate, project, path[len(project) + 1:]) if project else {}
                if found:
                    reproduced[path] = found
            if reproduced:
                actions, conflicts = plan(source, projects, owned_roots, changes, previous, reproduced)
    finally:
        shutil.rmtree(shadow, ignore_errors=True)
        shutil.rmtree(workdir, ignore_errors=True)
    entries = dict(previous.get('entries', {}))
    if mode == 'adopt':
        if not reason:
            raise Conflict('adopt needs an explicit reason')
        if only:
            unknown = sorted(set(only) - set(conflicts))
            if unknown:
                raise Conflict('not current conflicts, nothing adopted: ' + ', '.join(unknown))
            conflicts = sorted(only)
        for path in conflicts:
            entries[path] = {'after': sha(read(source/path)), 'action': 'adopted'}
        record = {'receiptType': 'reconcile-adopt', 'label': label, 'reason': reason, 'adopted': sorted(conflicts), 'entries': entries}
    else:
        if conflicts:
            raise Conflict('owner changes conflict with the candidate (nothing written): ' + ', '.join(conflicts))
        backup = receipt_dir/f'{count + 1:04d}-{label}-backup'
        for path, (current, desired, file_mode) in sorted(actions.items()):
            target = source/path
            if current is not None:
                (backup/path).parent.mkdir(parents=True, exist_ok=True)
                (backup/path).write_bytes(current)
            if desired is None:
                target.unlink()
            else:
                write_atomic(target, desired, file_mode)
            entries[path] = {'before': sha(current), 'after': sha(desired), 'action': 'removed' if desired is None else 'installed'}
            baseline = reproduced.get(path, {}).get(sha(current))
            if baseline:
                entries[path]['reproducedFrom'] = baseline
        record = {'receiptType': 'reconcile', 'label': label, 'changed': sorted(actions), 'entries': entries}
    record['patcher'] = patcher_receipt
    record['at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    release_receipts.write_once(receipt_dir/f'{count + 1:04d}-{label}.json', record)
    return record

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('mode', choices=['apply', 'adopt'])
    p.add_argument('candidate', type=Path)
    p.add_argument('release_dir', type=Path)
    p.add_argument('policy', type=Path)
    p.add_argument('signed_apps', type=Path)
    p.add_argument('--config', type=Path, default=ROOT/'os/release-pipeline/frankel.json')
    p.add_argument('--label', default=os.environ.get('BUILD_NUMBER', 'manual'))
    p.add_argument('--reason')
    p.add_argument('--path', action='append', dest='only', help='adopt only this conflicting path (repeatable)')
    a = p.parse_args(argv)
    config = json.loads(a.config.read_text())
    settings = config['host']['integrate']['reconcile']
    values = {'policy': str(a.policy), 'signed_apps': str(a.signed_apps)}
    if settings.get('installedIdentities'):
        # The delivered identities baseline comes from the selected candidate commit, not the trusted checkout.
        installed = a.candidate/settings['installedIdentities']
        if not installed.is_file():
            print(json.dumps({'verdict': 'FAIL', 'error': f'candidate has no {settings["installedIdentities"]}'}), file=sys.stderr)
            return 1
        values['installed_identities'] = str(installed)
    apply_args = [part.format(**values) for part in settings['applyArgs']]
    try:
        record = reconcile(config['host']['sourceRoot'], a.candidate, a.release_dir, apply_args, settings['receiptDir'],
            settings['projects'], settings['integrationOwnedRoots'], settings.get('sparsePaths', {}), a.label, a.mode, a.reason,
            patcher=settings.get('patcher', 'candidate'), only=a.only)
    except (Conflict, subprocess.CalledProcessError, release_receipts.ReceiptExists) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error) if not isinstance(error, subprocess.CalledProcessError)
            else f'git {error.cmd[1]} failed'}), file=sys.stderr)
        return 1
    print(json.dumps({'verdict': 'OK', 'mode': a.mode, 'label': a.label,
        'changed': record.get('changed', record.get('adopted'))}, indent=2))
    return 0

if __name__ == '__main__':
    sys.exit(main())
