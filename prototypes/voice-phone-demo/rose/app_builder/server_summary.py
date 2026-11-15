"""The library's server summary for a client-server version (docs/APP-BUILDER.md §3.3, slice 9): which contract
`api` the version's server speaks, and the oldest installed version whose client that server still serves.

A revision that breaks what installed clients rely on must raise `api` (rose.app_builder.contract.check_update),
and the server keeps serving every client since `api` last rose. So for a version at `api` n, the oldest client it
serves is the first delivered version at n: an installed version older than that must update before it uses the
server. Plain (app-v1) versions have no summary; the library leaves the field out for them.

Source only: the ledger serves it inside the library's `versions[]`; the phone's parsing and display are Home/App's.
"""
from __future__ import annotations

import json

SCHEMA_VERSION = 1


def summaries(versions: list[tuple[int, bool, str | None]]) -> dict[int, dict]:
    """``versions`` is every delivered version of one project, oldest first, as ``(versionCode, hasServer,
    contract)``: whether the version was delivered with a server, and the canonical contract JSON the ledger recorded
    for it (None when none was). Returns each client-server version's summary by versionCode."""
    out, first, api_of_first = {}, None, None
    for version_code, has_server, text in versions:
        if not has_server:  # a plain version: its client never talked to a server
            first = api_of_first = None
            continue
        api = None if text is None else _api(text)
        if api is None:  # no record, or one this server cannot read: nothing about it, or after it, is claimed
            out[version_code] = {'schemaVersion': SCHEMA_VERSION, 'status': 'unavailable'}
            first = api_of_first = None
            continue
        if first is None or api != api_of_first:
            first, api_of_first = version_code, api
        out[version_code] = {'schemaVersion': SCHEMA_VERSION, 'status': 'ok', 'api': api,
                             'minClientVersionCode': first}
    return out


def _api(text: str) -> int | None:
    try:  # a record nested too deep to parse is as unreadable as any other: never the whole library's failure
        api = json.loads(text).get('api')
    except (ValueError, AttributeError, TypeError, RecursionError):
        return None
    return api if type(api) is int and 1 <= api <= 2 ** 31 - 1 else None


def readable(summary, version_code: int) -> bool:
    """Whether a served summary is one a client may act on (the rules §3.3 gives a phone): anything else is read
    as `unavailable`, which never blocks an install and never says an older client still works. Numbers are JSON
    integers (as the phone's parser types them, `Long`): `1.0` and `true` are refused, though Python compares them
    equal to 1."""
    if not isinstance(summary, dict) or type(summary.get('schemaVersion')) is not int \
            or summary['schemaVersion'] != SCHEMA_VERSION:
        return False
    if summary.get('status') == 'unavailable':
        return set(summary) == {'schemaVersion', 'status'}
    if summary.get('status') != 'ok' or set(summary) != {'schemaVersion', 'status', 'api', 'minClientVersionCode'}:
        return False
    api, first = summary['api'], summary['minClientVersionCode']
    return (type(api) is int and 1 <= api <= 2 ** 31 - 1 and type(first) is int
            and 1 <= first <= min(version_code, 2 ** 31 - 1))
