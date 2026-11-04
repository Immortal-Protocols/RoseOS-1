#!/usr/bin/env python3
"""Check or apply the experimental Pixel 11 Pro Fold patches to a pinned source tree.

This prepares source only. It never downloads vendor files, builds, signs, accesses
ADB, starts infrastructure or flashes a phone. Apply requires an existing builder
lock. Run it in an isolated tree, after device-scoped vendor generation and before
the normal Rose integration.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


class Refused(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular_file(source, relative):
    path = source / relative
    for part in (path, *path.parents):
        if part == source:
            break
        if part.is_symlink():
            raise Refused('Symlink in source path: ' + relative)
    if not path.is_file():
        raise Refused('Missing source file: ' + relative)
    return path


def git(project, *args):
    result = subprocess.run(['git', '-C', str(project), *args], capture_output=True, text=True)
    if result.returncode:
        raise Refused('Cannot inspect Git project: ' + str(project))
    return result.stdout.strip()


def recipe(root):
    config = json.loads((root / 'os/fold-port/source.json').read_text())
    if config['device'] != 'yogi' or config['build']['lunch'] != 'yogi-cur-user':
        raise Refused('This tool prepares yogi only')
    pins = {p.get('path', p.get('name')): p.get('revision')
            for p in ET.parse(root / config['manifest']).findall('project')}
    return config, pins


def prepared_files(source, root=ROOT):
    """Check every input and the entire patch sequence before returning any new bytes."""
    config, pins = recipe(root)
    if not (source / '.repo').is_dir():
        raise Refused('Expected an existing repo-managed Android source tree')
    revisions = {}
    for project in config['projects']:
        directory = source / project
        if directory.is_symlink() or not directory.is_dir():
            raise Refused('Missing or linked project: ' + project)
        if Path(git(directory, 'rev-parse', '--show-toplevel')).resolve() != directory.resolve():
            raise Refused('Not an independent Git project: ' + project)
        head = git(directory, 'rev-parse', 'HEAD')
        if head != pins[project]:
            raise Refused('Wrong upstream revision: ' + project)
        if git(directory, 'status', '--porcelain', '--untracked-files=all'):
            raise Refused('Local changes present: ' + project)
        revisions[project] = head
    before = {}
    for relative, expected in config['preimages'].items():
        data = regular_file(source, relative).read_bytes()
        if digest(data) != expected:
            raise Refused('Source bytes differ from the pinned preimage: ' + relative)
        before[relative] = data
    with tempfile.TemporaryDirectory(prefix='rose-yogi-preflight-') as temporary:
        scratch = Path(temporary)
        for relative, data in before.items():
            target = scratch / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        patches = []
        for name in config['patches']:
            patch = root / 'os/fold-port' / name
            # These are a deliberately separate patch set; never extend another device's route.
            paths = {line[6:] for line in patch.read_text().splitlines()
                     if line.startswith(('--- a/', '+++ b/'))}
            if not paths or not paths <= before.keys():
                raise Refused('Patch touches an undeclared file: ' + name)
            result = subprocess.run(['git', 'apply', '--check', str(patch)], cwd=scratch,
                                    capture_output=True, text=True)
            if result.returncode:
                raise Refused('Patch does not apply: ' + name)
            subprocess.run(['git', 'apply', str(patch)], cwd=scratch, check=True,
                           capture_output=True)
            patches.append({'file': name, 'sha256': digest(patch.read_bytes())})
        after = {relative: (scratch / relative).read_bytes() for relative in before}
    return config, revisions, patches, before, after


@contextlib.contextmanager
def build_lock(path):
    if path.is_symlink() or not path.is_file():
        raise Refused('--lock-file must be the existing builder lock, not a symlink')
    with path.open('r+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('Builder lock is held; no source changes made')
        yield


def prepare(source, apply=False, lock_file=None, root=ROOT):
    source = Path(source).resolve()
    if apply and lock_file is None:
        raise Refused('apply requires --lock-file for the existing builder lock')
    lock = build_lock(Path(lock_file)) if apply else contextlib.nullcontext()
    with lock:
        config, revisions, patches, before, after = prepared_files(source, root)
        if apply:
            # Recheck all bytes under the same lock before changing the first file.
            for relative, data in before.items():
                if regular_file(source, relative).read_bytes() != data:
                    raise Refused('Source changed during preflight: ' + relative)
            for row in patches:
                subprocess.run(['git', 'apply', str(root / 'os/fold-port' / row['file'])],
                               cwd=source, check=True, capture_output=True)
        return {'device': 'yogi', 'method': 'source-preparation' if apply else 'source-inspection',
                'applied': apply, 'projects': revisions, 'patches': patches,
                'files': {p: {'beforeSha256': digest(before[p]), 'afterSha256': digest(after[p])}
                          for p in before},
                'build': config['build'],
                'limits': ['Vendor archive and stock-kernel bytes are not verified by this tool.',
                           'Rose app, policy, signing and update compatibility are separate checks.',
                           'No compilation, installation, phone acceptance or publication.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'apply'])
    parser.add_argument('source', type=Path)
    parser.add_argument('--lock-file', type=Path)
    args = parser.parse_args()
    try:
        result = prepare(args.source, args.action == 'apply', args.lock_file)
    except (Refused, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({'device': 'yogi', 'ready': False, 'reason': str(error)}))
        return 1
    print(json.dumps(dict(result, ready=True), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
