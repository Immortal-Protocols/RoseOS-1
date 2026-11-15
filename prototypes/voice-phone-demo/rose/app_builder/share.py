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
import string

SCHEMA_VERSION = 1
MAX_URL = 2048
MAX_CACHE_S = 600  # a 200 is re-checked at least this often: hosting cannot revoke a release yet, but may later
MAX_BACKOFF_S = 3600
HOSTNAME_LABEL = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?')
IPV4 = re.compile(r'(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})')
URL_CHARS = set(string.ascii_letters + string.digits + "-._~!$&'()*+,;=:@/?")  # RFC 3986, no brackets or '#'
HEX64 = re.compile(r'[0-9a-f]{64}')
SOON_S = 90  # a Retry-After up to this reads "try again in a minute"
REASONS = {
    'storage': 'the app\'s download storage is unavailable right now; try again in a minute',
    'storage_later': 'the app\'s download storage is unavailable right now; try again later',
    'unverifiable': 'this version can\'t be checked against the hosted copy yet',
    'unreachable': 'the app\'s download host could not be reached',
    'unreadable': 'the app\'s download host answered something this server can\'t read',
    'mismatch': 'the hosted copy doesn\'t match this version',
}


def _unavailable(key: str) -> dict:
    return {'schemaVersion': SCHEMA_VERSION, 'status': 'unavailable', 'reason': REASONS[key]}


def _host(host: str) -> bool:
    """A registry host name or dotted IPv4 address, as java.net.URI accepts for getHost(); IPv6 is refused."""
    match = IPV4.fullmatch(host)
    if match:
        return all(int(part) <= 255 for part in match.groups())
    labels = host[:-1].split('.') if host.endswith('.') else host.split('.')
    return all(HOSTNAME_LABEL.fullmatch(label) for label in labels) and labels[-1][:1].isalpha()


def link(value) -> str | None:
    """A plain absolute https URL, or None. At least as strict as the phone's ShareState.link (java.net.URI with a
    host, no credentials or fragment, ASCII, at most 2048 characters), so nothing the server calls published is one
    the phone refuses; test_app_share checks that against the phone's own code."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL or not value.startswith('https://'):
        return None
    authority, _, tail = value[len('https://'):].partition('/')
    if '?' in authority:
        authority, _, query = authority.partition('?')
        tail = '?' + query
    else:
        tail = '/' + tail if _ or tail else ''
    host, colon, port = authority.rpartition(':') if ':' in authority else (authority, '', '')
    if not host or '@' in authority or colon and not (port.isascii() and port.isdigit()) or not _host(host):
        return None
    i = 0
    while i < len(tail):  # path and query: RFC 3986 characters, '%' only as a valid escape
        if tail[i] == '%':
            if not all(c in string.hexdigits for c in tail[i + 1:i + 3]) or len(tail[i + 1:i + 3]) != 2:
                return None
            i += 3
        elif tail[i] in URL_CHARS:
            i += 1
        else:
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
    apk = record.get('apk') if isinstance(record.get('apk'), dict) else {}
    expected = (record['hostedAppId'], record.get('packageName'), record.get('versionCode'),
                record.get('signerSha256'), apk.get('sha256'), apk.get('size'))
    if not _identity(expected):  # an unsigned or incomplete record can never be matched, so never published
        return _unavailable('unverifiable')
    if answer is None:
        return _unavailable('unreachable')
    status = answer.get('status')  # None when no HTTP answer arrived: unreachable, below
    if status == 404:
        return {'schemaVersion': SCHEMA_VERSION, 'status': 'unpublished'}
    if status == 503:
        return _unavailable('storage' if backoff_seconds(answer) <= SOON_S else 'storage_later')
    if status != 200:
        return _unavailable('unreachable')
    body = answer.get('json')
    links = body.get('links') if isinstance(body, dict) else None
    if not isinstance(links, dict):
        return _unavailable('unreadable')
    release, latest = link(links.get('release')), link(links.get('latest'))
    fields = ('app_id', 'package_name', 'version_code', 'signer_sha256', 'sha256', 'size_bytes')
    hosted = tuple(body.get(k) for k in fields)
    if release is None or latest is None or not _identity(hosted):
        return _unavailable('unreadable')
    if hosted != expected:
        return _unavailable('mismatch')
    return {'schemaVersion': SCHEMA_VERSION, 'status': 'published', 'versionUrl': release, 'latestUrl': latest}


def _identity(values) -> bool:
    """(app id, package, versionCode, signer, sha256, size) each of its own kind, so nothing matches by both being
    missing: non-empty text, 64 lower-case hex digests, a positive versionCode and a size, never booleans."""
    app_id, package, code, signer, digest, size = values
    return (isinstance(app_id, str) and bool(app_id) and isinstance(package, str) and bool(package)
            and type(code) is int and code >= 1 and type(size) is int and size >= 0
            and all(isinstance(v, str) and HEX64.fullmatch(v) for v in (signer, digest)))


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
    return min(int(value), MAX_BACKOFF_S) if re.fullmatch(r'[0-9]{1,9}', value) else 60  # ASCII digits only
