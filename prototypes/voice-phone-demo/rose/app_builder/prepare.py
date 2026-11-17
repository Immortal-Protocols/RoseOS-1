"""Prepare a client-server builder's inputs exactly (docs/APP-BUILDER.md §5.2 recipe): offline, from files the
operator already fetched, checking each against its own record. It downloads, installs and runs nothing but ``git``
on the given checkout.

    python -m rose.app_builder.prepare digest TREE
    python -m rose.app_builder.prepare crate-store --lock CARGO_LOCK --from CRATE_DIR --out STORE
    python -m rose.app_builder.prepare runtime-root --repo CHECKOUT --out DIR

- ``digest``: the ``rustToolchainSha256`` pin of an unpacked Rust toolchain tree (``toolchain_digest``, pin format
  v1). Compute it on the tree at its final path: rust's installer writes that path into the tree.
- ``crate-store``: a ``RecordedCrates`` store (``index.json`` and ``<sha256>.crate``) holding exactly the registry
  packages of the given Cargo.lock(s), each copied from ``CRATE_DIR`` (for example a dedicated ``CARGO_HOME``'s
  ``registry/cache/<index>/``, after ``cargo fetch --locked``) only if its SHA-256 is the lock's own checksum. A
  missing or mismatched crate, or a non-empty ``STORE``, is refused.
- ``runtime-root``: the builder's path crates (``rose-app-runtime``, ``rose-app-runtime-signin``, ``rose-signin``:
  ``Cargo.toml`` and ``src/``) copied from a git checkout. Refused unless those folders have no uncommitted change
  and ``rose-signin`` is exactly its tree at the pinned sign-in revision (``server_manifest.SIGNIN_REVISION``).

Each prints one JSON line; exit 0 ok, 1 refused, 2 usage.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from . import crates, server_build, server_manifest
from .providers.host import toolchain_digest

PATH_CRATES = (*server_build.BUILDER_CRATES, 'rose-signin')
REGISTRY = 'registry+https://github.com/rust-lang/crates.io-index'


class Refused(Exception):
    pass


def digest(tree: Path) -> str:
    try:
        return toolchain_digest(tree)
    except (OSError, ValueError, RuntimeError) as exc:
        raise Refused(f'the toolchain tree cannot be pinned: {exc}') from None


def registry_packages(locks: list[Path]) -> dict[str, str]:
    """``name@version`` -> checksum for every crates.io package the locks name; one checksum per package."""
    out = {}
    for lock in locks:
        try:
            data = tomllib.loads(Path(lock).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise Refused(f'{lock} could not be read ({type(exc).__name__})') from None
        for package in data.get('package', []):
            if package.get('source') != REGISTRY:
                continue  # path crates come from the runtime root
            key, checksum = f'{package["name"]}@{package["version"]}', package.get('checksum')
            if not isinstance(checksum, str) or len(checksum) != 64:
                raise Refused(f'{lock} gives {key} no checksum')
            if out.setdefault(key, checksum) != checksum:
                raise Refused(f'the locks disagree on the checksum of {key}')
    if not out:
        raise Refused('the locks name no crates.io package')
    return out


def _empty_target(out: Path) -> None:
    """OUT must not exist, or be an empty directory, so nothing already there is mixed in or replaced."""
    if out.is_symlink() or (out.exists() and not out.is_dir()):
        raise Refused(f'{out} is not a directory')
    if out.exists() and any(out.iterdir()):
        raise Refused(f'{out} is not empty')


@contextlib.contextmanager
def _staged(out: Path):
    """Write into a fresh sibling folder, and move it to OUT only once it is complete: a refusal or an I/O error
    leaves OUT exactly as it was."""
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(os.path.realpath(out.parent)) / f'.{out.name}.partial-{os.getpid()}'
    if os.path.lexists(stage):
        raise Refused(f'{stage} is left from an earlier run; remove it')
    stage.mkdir(mode=0o755)
    try:
        yield stage
        if out.exists():
            out.rmdir()  # empty, checked before anything was written
        stage.rename(out)
    finally:
        if os.path.lexists(stage):
            shutil.rmtree(stage, ignore_errors=True)


def crate_store(locks: list[Path], source: Path, out: Path) -> dict:
    wanted = registry_packages(locks)
    _empty_target(out)
    files = {}
    for key, checksum in sorted(wanted.items()):
        name, version = key.split('@', 1)
        path = Path(source) / f'{name}-{version}.crate'
        try:
            data = path.read_bytes()
        except OSError:
            raise Refused(f'{path.name} is not in {source}') from None
        if hashlib.sha256(data).hexdigest() != checksum:
            raise Refused(f'{path.name} is not the crate the lock records (SHA-256 differs)')
        files[checksum] = data
    with _staged(out) as stage:
        for checksum, data in files.items():
            (stage / f'{checksum}.crate').write_bytes(data)
        (stage / 'index.json').write_text(json.dumps({'schemaVersion': crates.SCHEMA_VERSION, 'crates': wanted},
                                                     indent=1, sort_keys=True) + '\n')
        try:
            loaded = crates.RecordedCrates(stage).crates()  # read back exactly as the builder will
        except ValueError as exc:
            raise Refused(f'the written store does not read back: {exc}') from None
        if len(loaded) != len(wanted):
            raise Refused('the written store does not read back whole')
    return {'crates': len(loaded), 'store': str(out)}


def _git(repo: Path, *args: str) -> str:
    try:
        done = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refused(f'git could not run ({type(exc).__name__})') from None
    if done.returncode != 0:
        raise Refused(f'git {" ".join(args)} failed: {done.stderr.strip()[:200]}')
    return done.stdout.strip()


def runtime_root(repo: Path, out: Path) -> dict:
    _empty_target(out)
    folders = [f'workspace/{name}' for name in PATH_CRATES]
    for name in PATH_CRATES:  # all present before anything is copied
        for required in ('Cargo.toml', 'src/lib.rs'):
            if not (Path(repo) / 'workspace' / name / required).is_file():
                raise Refused(f'{name} has no {required}')
    if _git(repo, 'status', '--porcelain', '--', *folders):
        raise Refused('the checkout has uncommitted changes in ' + ', '.join(folders))
    pinned = server_manifest.SIGNIN_REVISION
    try:
        pinned_tree = _git(repo, 'rev-parse', f'{pinned}:workspace/rose-signin')
    except Refused:
        raise Refused(f'the checkout does not hold the pinned sign-in revision {pinned} (a shallow clone?): use a '
                      f'full clone, or fetch that commit into it, then run this again') from None
    if _git(repo, 'rev-parse', 'HEAD:workspace/rose-signin') != pinned_tree:
        raise Refused(f'workspace/rose-signin is not the tree at the pinned sign-in revision {pinned}')
    head = _git(repo, 'rev-parse', 'HEAD')
    with _staged(out) as stage:
        for name in PATH_CRATES:
            src = Path(repo) / 'workspace' / name
            (stage / name).mkdir()
            shutil.copy2(src / 'Cargo.toml', stage / name / 'Cargo.toml')
            shutil.copytree(src / 'src', stage / name / 'src', symlinks=False)
    return {'revision': head, 'signin': pinned, 'crates': list(PATH_CRATES), 'root': str(out)}


def main(argv=None, out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(prog='python -m rose.app_builder.prepare')
    sub = parser.add_subparsers(dest='command', required=True)
    d = sub.add_parser('digest')
    d.add_argument('tree', type=Path)
    c = sub.add_parser('crate-store')
    c.add_argument('--lock', type=Path, action='append', required=True)
    c.add_argument('--from', dest='source', type=Path, required=True)
    c.add_argument('--out', type=Path, required=True)
    r = sub.add_parser('runtime-root')
    r.add_argument('--repo', type=Path, required=True)
    r.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'digest':
            result = {'rustToolchainSha256': digest(args.tree)}
        elif args.command == 'crate-store':
            result = crate_store(args.lock, args.source, args.out)
        else:
            result = runtime_root(args.repo, args.out)
    except Refused as exc:
        print(json.dumps({'status': 'refused', 'message': str(exc)}), file=out)
        return 1
    except OSError as exc:  # a disk, permission or path error: nothing was moved into place
        print(json.dumps({'status': 'refused', 'message': f'{type(exc).__name__}: {exc}'}), file=out)
        return 1
    print(json.dumps({'status': 'ok', **result}, sort_keys=True), file=out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
