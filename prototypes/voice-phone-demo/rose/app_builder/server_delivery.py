"""The delivered server's deploy plan (docs/APP-BUILDER.md §7.7): what Chariot's owner tooling sends to deploy one
delivered client-server version on its recorded native host, read from the builder's own records.

    python -m rose.app_builder.server_delivery plan --ledger DIR --project ID [--version N]

The plan maps App Core's manifest (rose.app_builder.server_manifest) onto Chariot's agreed owner calls
(ProtocolsBackend #2564, chariot/docs/native-app-hosts.md):

1. ``PUT /v1/app-hosts/{hostId}/artifacts/{sha256}`` with the bytes at ``artifact.path`` (static x86-64 musl, at
   most 64 MiB), then
2. ``POST /v1/app-hosts/{hostId}/deploy`` with ``deploy`` as its body: ``{sha256, args: [], port, health_path,
   data_schema}``. The generated server serves only with no arguments (``invite <account>`` is its one other
   command), so ``args`` is always empty. ``data_schema`` is ``1 + account * 10000 + shared`` over the manifest's
   highest migration numbers: one integer per schema, at least 1, changed by any new migration.

This module only reads: it opens the ledger read-only (Ledger.reader) and records, uploads, deploys and contacts
nothing, and holds no credential. Chariot's owner
tooling makes the calls with Chariot's own creator authentication, an Idempotency-Key of its own, and then reads the
host's status; a running or allocated host is not a ready app. Chariot v1 refuses a deploy whose ``data_schema``
differs from the active one (no schema-changing deploys yet, §7.7 item 4).

Output is one line of JSON with a ``status``; ``ok`` carries the plan, every other status a ``message``:
- ``ok``, 0;
- ``usage``, 2;
- ``refused``, 3: not a client-server version; no manifest (its server is not for the host's target); a server over
  Chariot's 64 MiB; no native host recorded; or the stored ELF missing or not the recorded one;
- ``not_found``, 4: no such ledger, project or delivered version;
- ``error``, 6: the ledger could not be opened or read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from . import server_manifest
from .ledger import CLIENT_SERVER, Ledger

MAX_HOSTED_ELF = 64 * 1024 * 1024  # Chariot's artifact limit (NativeArtifact.size_bytes)
SCHEMA_SCALE = 10000  # server_manifest.MAX_SCHEMA is 9999, so (account, shared) maps one to one


class Refused(Exception):
    pass


def data_schema(schema: dict) -> int:
    """Chariot's one ``data_schema`` integer for the manifest's ``{account, shared?}``: 1 + account*10000 + shared."""
    account, shared = schema['account'], schema.get('shared', 0)
    if not (0 <= account <= server_manifest.MAX_SCHEMA and 0 <= shared <= server_manifest.MAX_SCHEMA):
        raise Refused('the manifest schema is out of range')
    return 1 + account * SCHEMA_SCALE + shared


def plan(ledger: Ledger, root: Path, project: str, version_code: int | None = None) -> dict:
    """The deploy plan for one delivered version (the newest when ``version_code`` is None); raises Refused, or
    LookupError when the project or version is not there."""
    if ledger.project(project) is None:
        raise LookupError('no such project in this ledger')
    versions = [v['versionCode'] for v in ledger.versions(project)]
    if not versions:
        raise LookupError('this project has no delivered version')
    code = max(versions) if version_code is None else version_code
    if code not in versions:
        raise LookupError('no such delivered version')
    if ledger.profile(project) != CLIENT_SERVER or ledger.server(project, code) is None:
        raise Refused('this version has no server')
    data = ledger.server_manifest(project, code)
    if data is None:
        raise Refused("this version's server is not built for the host's target, so it has no manifest")
    m = server_manifest.decode(data)
    elf = m['elf']
    if elf['size'] > MAX_HOSTED_ELF:
        raise Refused('this server is larger than the 64 MiB the host accepts')
    host = ledger.native_host(project)
    if host is None:
        raise Refused('no native host is recorded for this project')
    path = Path(root) / 'artifacts' / f'{elf["sha256"]}.server'
    try:
        digest, size = hashlib.sha256(), 0
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                digest.update(chunk)
                size += len(chunk)
    except OSError:
        raise Refused("this version's server binary is not in the builder's artifacts") from None
    if (digest.hexdigest(), size) != (elf['sha256'], elf['size']):
        raise Refused("the stored server binary is not the one this version recorded")
    return {'projectId': project, 'versionCode': code, 'hostId': host['hostId'], 'endpoint': host['endpoint'],
            'artifact': {'path': str(path.resolve()), 'sha256': elf['sha256'], 'size': elf['size'],
                         'target': elf['target']},
            'deploy': {'sha256': elf['sha256'], 'args': [], 'port': m['listen']['default'],
                       'health_path': m['health'], 'data_schema': data_schema(m['schema'])},
            'manifestSha256': hashlib.sha256(data).hexdigest()}


class _Usage(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _Usage(message)


def main(argv=None, out=sys.stdout) -> int:
    def say(status, **fields):
        print(json.dumps({'status': status, **fields}, sort_keys=True), file=out)
    p = _Parser(prog='python -m rose.app_builder.server_delivery', add_help=False, allow_abbrev=False)
    sub = p.add_subparsers(dest='command', required=True, parser_class=_Parser)
    s = sub.add_parser('plan', add_help=False, allow_abbrev=False)
    s.add_argument('--ledger', required=True)
    s.add_argument('--project', required=True)
    s.add_argument('--version', type=int)
    try:
        a = p.parse_args(argv)
        if a.version is not None and a.version < 1:
            raise _Usage('--version is a delivered versionCode')
    except _Usage as e:
        say('usage', message=str(e))
        return 2
    root = Path(a.ledger)
    if not (root / 'builder.sqlite3').is_file():
        say('not_found', message='no builder ledger there')
        return 4
    try:
        ledger = Ledger.reader(root)  # read-only: the plan changes nothing in the builder's records
    except (OSError, sqlite3.Error) as e:
        say('error', message=f'the ledger could not be opened: {e}')
        return 6
    try:
        result = plan(ledger, root, a.project, a.version)
    except LookupError as e:
        say('not_found', message=str(e))
        return 4
    except (Refused, server_manifest.Refused) as e:
        say('refused', message=str(e))
        return 3
    except sqlite3.Error as e:
        say('error', message=f'the ledger could not be read: {e}')
        return 6
    finally:
        ledger.close()
    say('ok', plan=result)
    return 0


if __name__ == '__main__':
    sys.exit(main())
