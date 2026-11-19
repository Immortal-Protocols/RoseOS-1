"""Retained backend evidence and source migration; never compiles, connects, or changes live data.

``python -m rose.app_builder.legacy_backend --ledger DIR`` is read-only. A missing ledger is unknown usage,
not an empty inventory. The normal revise job stages legacy source and changes the bound profile only when the
new agent release and APK are delivered together. Original commits, binaries, locks and manifests stay intact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from . import appprofile
from .ledger import CLIENT_SERVER, Ledger
from .toolchain import StepFailed

LEGACY = Path('backend/legacy')


def base_version(ledger: Ledger, project: str) -> int:
    return max(v['versionCode'] for v in ledger.versions(project))


def evidence(ledger: Ledger, project: str) -> dict:
    code = base_version(ledger, project)
    version = next(v for v in ledger.versions(project) if v['versionCode'] == code)
    identity = ledger.project(project)
    return {'projectId': project, 'versionCode': code, 'sourceRevision': version['sourceRevision'],
            'packageName': identity['packageName'], 'signerSha256': identity['signerSha256'],
            'server': ledger.server(project, code), 'agentContainer': ledger.agent_container(project),
            'nativeHost': ledger.native_host(project), 'migrations': ledger.migration_lock(project),
            'crates': ledger.crates(project, code)}


def inventory(ledger: Ledger) -> dict:
    rows = [evidence(ledger, p['projectId']) for p in ledger.projects()
            if ledger.profile(p['projectId']) == CLIENT_SERVER]
    return {'schemaVersion': 1, 'scope': 'this ledger only; deployment and live data not observed', 'projects': rows}


def prepare(source: Path, ledger: Ledger, project: str) -> None:
    """Called only after checkout of the last delivered source. No guessing a new container or endpoint."""
    record = evidence(ledger, project)
    if record['agentContainer'] is None:
        raise StepFailed('toolchain', 'This app uses the retired Rust backend path. The Local Server Worker must '
                         'preserve its data and endpoint in an existing Chariot agent container before revision; '
                         'see docs/APP-BACKEND-MIGRATION.md. Nothing was moved or deployed.')
    native, agent = record['nativeHost'], record['agentContainer']
    setup = ledger.server_setup(project)
    if native is not None and native['endpoint'] != agent['appUrl'] and not (
            setup and setup['versionCode'] == record['versionCode'] and setup['appUrl'] == agent['appUrl']):
        raise StepFailed('toolchain', 'This legacy app has different native-host and agent endpoints. The Local '
                         'Server Worker must establish which endpoint its delivered clients use and preserve it '
                         'before revision; no endpoint was changed.')
    if appprofile.read(source).name != CLIENT_SERVER or not (source / 'server').is_dir():
        raise StepFailed('toolchain', 'The delivered legacy backend source is incomplete; restore that source '
                         'before migrating this app.')
    target = source / LEGACY
    target.mkdir(parents=True, exist_ok=False)
    (source / 'server').rename(target / 'server')
    (source / appprofile.FILE).rename(target / appprofile.FILE)
    (target / 'record.json').write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
    lock = ledger.cargo_lock(project)
    if lock is not None:
        (target / 'Cargo.lock').write_text(lock)
    manifest = ledger.server_manifest(project, record['versionCode'])
    if manifest is not None:
        (target / 'manifest.json').write_bytes(manifest)
    (source / appprofile.FILE).write_text(json.dumps({'schemaVersion': 1, 'profile': appprofile.CLIENT_BACKEND_V1}))
    (target / 'README.md').write_text(
        'Migrated from the delivered builder-managed Rust backend. This is retained source and evidence, not a '
        'new toolchain. Keep record.json, the original SQL migrations and Cargo.lock unchanged. The original '
        'commits and binary remain in the builder ledger/artifacts. Choose and install a stack inside the existing '
        'agent container using agent_container. For a client-only revision, copy the current release\'s server '
        'and invite into the new release and write start to export CHARIOT_REVISION_SHA256="$ROSE_RELEASE" and '
        'exec ./server. Do not reference an old release by path: old releases are eventually pruned. For a backend '
        'change, own its build files and dependencies and preserve its API, auth identities, sealing keyring, '
        'database paths and all data; implement and test forward migrations on synthetic data. Do not touch '
        'live data or keys during the writer turn. The normal switch keeps DATA_DIR and the existing keyring, '
        'backs up data and rolls back on health failure. Runtime seeded-data and update acceptance remain required.\n')


def check(snapshot: Path, ledger: Ledger, project: str) -> None:
    """The retained migration history must still match the delivered records, including across repair turns."""
    def refused():
        return appprofile.ProfileRefused('Keep the legacy backend record, manifest, Cargo.lock and delivered SQL migrations '
                                         'unchanged under backend/legacy; add forward migrations separately.',
                                         'legacy_changed')
    try:
        root = snapshot / LEGACY
        if json.loads((root / 'record.json').read_text()) != evidence(ledger, project):
            raise refused()
        lock = ledger.cargo_lock(project)
        if lock is not None and (root / 'Cargo.lock').read_text() != lock:
            raise refused()
        manifest = ledger.server_manifest(project, base_version(ledger, project))
        if manifest is not None and (root / 'manifest.json').read_bytes() != manifest:
            raise refused()
        for migration in ledger.migration_lock(project):
            path = root / 'server/migrations' / migration['scope'] / f'{migration["number"]:04d}_{migration["name"]}.sql'
            if hashlib.sha256(path.read_bytes()).hexdigest() != migration['sha256']:
                raise refused()
    except (OSError, ValueError):
        raise refused() from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, required=True)
    args = parser.parse_args(argv)
    if not (args.ledger / 'builder.sqlite3').is_file():
        print(json.dumps({'status': 'unknown', 'message': 'No ledger at this path; deployed usage is unknown.'}))
        return 4
    try:
        ledger = Ledger.reader(args.ledger)
        try:
            result = inventory(ledger)
        finally:
            ledger.close()
    except (OSError, sqlite3.Error, ValueError, KeyError):
        print(json.dumps({'status': 'unknown', 'message': 'The ledger could not be inventoried; preserve it and '
                          'have the Local Server Worker check its schema and readability.'}))
        return 6
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
