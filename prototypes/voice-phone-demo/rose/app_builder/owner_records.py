"""The owner records' writer (docs/APP-BUILDER.md §7.7 and §3.3 `share`): the one source path by which owner tooling
records, in a builder's ledger, what only owner tooling may record, and reads it back.

    python -m rose.app_builder.owner_records show --ledger DIR --project ID
    python -m rose.app_builder.owner_records native-host --ledger DIR --project ID --host-id UUID --endpoint URL
        [--replaces-host-id UUID --replaces-endpoint URL]
    python -m rose.app_builder.owner_records hosted-app --ledger DIR --project ID --hosted-app-id UUID

- `native-host`: the native host (Chariot's HostView: the host's UUID and its endpoint) whose endpoint client-server
  builds bake into RoseServer.URL. A replaced host or a changed domain names the record it replaces.
- `hosted-app`: the hosted APK app id the library's `share` reads; recorded once.

Every value comes from the caller, as the owner's own systems returned it: this tool derives, guesses, fetches and
provisions nothing, opens no connection and reads no credential. DIR must already hold a builder ledger
(builder.sqlite3); none is created. The ledger checks every value (Ledger.record_native_host, record_hosted_app), in
one transaction, so it is safe while the builder runs; a build already running keeps the endpoint it read.

Output is one line of JSON on stdout. Exit status: 0 recorded (or already so), 2 usage, 3 refused by the ledger,
4 no such ledger or project, 5 the ledger was busy (nothing recorded; try again).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .ledger import Ledger, LedgerError


class _Usage(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _Usage(message)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog='python -m rose.app_builder.owner_records', add_help=False)
    sub = p.add_subparsers(dest='command', required=True, parser_class=_Parser)
    for name in ('show', 'native-host', 'hosted-app'):
        s = sub.add_parser(name, add_help=False)
        s.add_argument('--ledger', required=True)
        s.add_argument('--project', required=True)
        if name == 'native-host':
            s.add_argument('--host-id', required=True)
            s.add_argument('--endpoint', required=True)
            s.add_argument('--replaces-host-id')
            s.add_argument('--replaces-endpoint')
        if name == 'hosted-app':
            s.add_argument('--hosted-app-id', required=True)
    return p


def _state(ledger: Ledger, project: str) -> dict:
    return {'projectId': project, 'nativeHost': ledger.native_host(project), 'hostedAppId': ledger.hosted_app(project)}


def main(argv=None, out=sys.stdout) -> int:
    def say(status, **fields):
        print(json.dumps({'status': status, **fields}, sort_keys=True), file=out)
    try:
        a = _parser().parse_args(argv)
        if a.command == 'native-host' and (a.replaces_host_id is None) != (a.replaces_endpoint is None):
            raise _Usage('--replaces-host-id and --replaces-endpoint go together')
    except _Usage as e:
        say('usage', message=str(e))
        return 2
    if not (Path(a.ledger) / 'builder.sqlite3').is_file():
        say('not_found', message='no builder ledger there')
        return 4
    ledger = Ledger(Path(a.ledger))
    try:
        if ledger.project(a.project) is None:
            say('not_found', message='no such project in this ledger')
            return 4
        try:
            if a.command == 'native-host':
                replaces = None if a.replaces_host_id is None else {'hostId': a.replaces_host_id,
                                                                    'endpoint': a.replaces_endpoint}
                ledger.record_native_host(a.project, a.host_id, a.endpoint, replaces=replaces)
            elif a.command == 'hosted-app':
                ledger.record_hosted_app(a.project, a.hosted_app_id)
        except LedgerError as e:
            say('refused', message=str(e), **_state(ledger, a.project))
            return 3
        except sqlite3.OperationalError:  # the builder holds the ledger's write lock: nothing was recorded
            say('busy', message='the ledger is busy; nothing was recorded; try again')
            return 5
        say('ok', **_state(ledger, a.project))
        return 0
    finally:
        ledger.close()


if __name__ == '__main__':
    sys.exit(main())
