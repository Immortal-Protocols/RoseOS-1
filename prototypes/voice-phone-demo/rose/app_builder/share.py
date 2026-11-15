"""Level 3 share state for one delivered version (docs/APP-BUILDER.md §3.3, `versions[].share`). Source only and
called nowhere yet: no hosting is configured and nothing is published.

The Chariot owner hosts published APKs and serves public metadata per release (`app_id` + `version_code`). The
builder's server reads that metadata and tells the phone what it may share; the phone never contacts hosting,
holds no Chariot credential, and shares only the links recorded here, exactly as hosting returned them.

`published` needs every one of these at once: a 200 for this version's hosted app id, hosted bytes identical to
what the builder delivered (sha256 and size), the same package, versionCode and signer, and `release` and `latest`
links that are plain absolute https URLs. Anything else is `unpublished` (hosting has no such release) or
`unavailable` with a reason, never `published` by default.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
MAX_URL = 2048
MAX_CACHE_S = 600  # a 200 is re-checked at least this often: hosting cannot revoke a release yet, but may later
MAX_BACKOFF_S = 3600
REASONS = {
    'storage': 'the app\'s download storage is unavailable right now',
    'unreachable': 'the app\'s download host could not be reached',
    'unreadable': 'the app\'s download host answered something this server can\'t read',
    'mismatch': 'the hosted copy doesn\'t match this version',
}


def _unavailable(key: str) -> dict:
    return {'schemaVersion': SCHEMA_VERSION, 'status': 'unavailable', 'reason': REASONS[key]}


def link(value) -> str | None:
    """A plain absolute https URL (no credentials, fragment, spaces or non-ASCII; at most 2048 characters), or None.
    The same rule the phone applies (ShareState.link), so nothing the server sends is refused there."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL or not value.startswith('https://'):
        return None
    if any(ord(c) <= 0x20 or ord(c) >= 0x7f for c in value):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme != 'https' or not parts.hostname or '@' in parts.netloc or parts.fragment or '#' in value:
        return None
    return value


def summary(record: dict, answer: dict | None) -> dict:
    """The `share` value for one version.

    record: what the builder delivered and recorded, {hostedAppId (None until owner tooling records the mapping),
    packageName, versionCode, signerSha256, apk: {sha256, size}}.
    answer: hosting's reply to GET /apps/<hostedAppId>/releases/<versionCode>/metadata, as {status (None when no
    answer arrived), headers, json}; None when no lookup was made.
    """
    if record.get('hostedAppId') is None:
        return {'schemaVersion': SCHEMA_VERSION, 'status': 'unpublished'}  # never mapped, so never published
    if answer is None:
        return _unavailable('unreachable')
    status = answer.get('status')  # None when no HTTP answer arrived: unreachable, below
    if status == 404:
        return {'schemaVersion': SCHEMA_VERSION, 'status': 'unpublished'}
    if status == 503:
        return _unavailable('storage')
    if status != 200:
        return _unavailable('unreachable')
    body = answer.get('json')
    links = body.get('links') if isinstance(body, dict) else None
    if not isinstance(links, dict):
        return _unavailable('unreadable')
    release, latest = link(links.get('release')), link(links.get('latest'))
    fields = ('app_id', 'package_name', 'version_code', 'signer_sha256', 'sha256', 'size_bytes')
    if release is None or latest is None or any(k not in body for k in fields) \
            or type(body['version_code']) is not int or type(body['size_bytes']) is not int:
        return _unavailable('unreadable')
    apk = record.get('apk') or {}
    expected = (record['hostedAppId'], record.get('packageName'), record.get('versionCode'),
                record.get('signerSha256'), apk.get('sha256'), apk.get('size'))
    hosted = tuple(body[k] for k in fields)
    if hosted != expected:
        return _unavailable('mismatch')
    return {'schemaVersion': SCHEMA_VERSION, 'status': 'published', 'versionUrl': release, 'latestUrl': latest}


def cache_seconds(answer: dict | None) -> int:
    """How long this answer may stand before hosting is asked again: a 200 for its max-age, at most MAX_CACHE_S;
    nothing else is kept."""
    if not answer or answer.get('status') != 200:
        return 0
    control = str((answer.get('headers') or {}).get('cache-control', '')).lower()
    if 'no-store' in control or 'no-cache' in control:
        return 0
    match = re.search(r'(?:^|[,\s])max-age=(\d{1,9})(?:$|[,\s])', control)
    return min(int(match.group(1)), MAX_CACHE_S) if match else 0


def backoff_seconds(answer: dict | None) -> int:
    """After a 503, how long to wait before asking this host again (its Retry-After seconds, bounded)."""
    if not answer or answer.get('status') != 503:
        return 0
    value = str((answer.get('headers') or {}).get('retry-after', '')).strip()
    return min(int(value), MAX_BACKOFF_S) if value.isdigit() else 60
