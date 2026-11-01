#!/usr/bin/env python3
"""Stage the pinned bring-up files (manifest.json) into the staging root, verified (CLOUD-STAGING-SOURCE-001).

  python3 -c <this source> <manifest JSON> <limits JSON>

bootstrap's cloud-stage-once diagnostic would run it once in the exact cached image, with only the staging root
mounted read-write and no credentials, when a separate runtime decision sets that mode. It has not run on a VM.

For every manifest file, in manifest order:
- an existing final file is verified (size, then sha256 or git blob sha1): if it matches it is kept; if it differs the
  whole run is refused BEFORE any download, and nothing is changed;
- otherwise it is fetched from its one pinned source (SOURCES / the Hub resolve URL at the pinned revision) into
  ``<name>.partial``, resuming an existing partial with an HTTP Range request. Only https to ALLOW_HOSTS is accepted,
  for the first request and every redirect; any other host or scheme, a 401/403 (no credentials are ever sent), a
  Content-Length or body larger than the pinned size, or a server that ignores the Range stops that file;
- complete files are hashed and only an exact match is renamed into place. A mismatch, an interruption, the
  deadline or the byte cap leaves ``.partial`` where it is (partials and caches are never deleted).

The run stops at ``deadlineS`` or once ``maxBytes`` have been read. It prints one MARKER line of fixed tokens
(sanitize() is the allowlist bootstrap re-applies): counts per group, bytes in, elapsed seconds and the stop reason.
Never a URL, query, header, path outside the manifest's names, or exception text.

Unverified until a separately approved run: the image's Python ssl/CA store, the Hub's redirect hosts from the VM's
region (only the three hosts seen from a development sandbox are allowed), its Range support on those hosts, and
the throughput needed to finish 37.6 GB inside the deadline.
"""
import hashlib
import json
import os
import sys
import time

MARKER = 'ROSE-CLOUD-STAGE '
HUB = 'https://huggingface.co'
# The exact PyPI file for the pinned wheel (PyPI JSON for ptts 0.6.0); its sha256 is the manifest's.
SOURCES = {('ptts', 'ptts-0.6.0-cp39-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl'):
           'https://files.pythonhosted.org/packages/62/5f/2c68115af9499a1c8d4395850eb758efd95aca20bc351c8d969962a39c53/'
           'ptts-0.6.0-cp39-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl'}
ALLOW_HOSTS = ('huggingface.co', 'us.aws.cdn.hf.co', 'files.pythonhosted.org')
MAX_REDIRECTS = 5
CHUNK = 1 << 20
STOPS = ('done', 'refused_existing', 'insufficient_space', 'deadline', 'byte_cap', 'file_failures', 'bad_manifest')
FAILURES = ('existing_differs', 'redirect_refused', 'auth_required', 'http_error', 'size_mismatch', 'range_refused',
            'hash_mismatch', 'network')
GROUP_KEYS = ('expected', 'present', 'downloaded', 'resumed', 'failed', 'bytesIn')


class Stop(Exception):
    pass


class FileFailure(Exception):
    pass


def url_for(name, group, rel, limits):
    """The one source of a file. ``hubBase``/``sources`` in limits exist for offline tests; bootstrap never sets them
    (its tests assert the exact limits it passes)."""
    if 'repo' in group:
        return '%s/%s/resolve/%s/%s' % (limits.get('hubBase', HUB), group['repo'], group['revision'], rel)
    sources = dict(SOURCES)
    sources.update({tuple(k.split('/', 1)): v for k, v in limits.get('sources', {}).items()})
    return sources[(name, rel)]


def hasher(kind, size):
    h = hashlib.sha256() if kind == 'sha256' else hashlib.sha1()
    if kind == 'gitsha1':
        h.update(b'blob %d\0' % size)
    return h


def file_matches(path, size, kind, digest):
    if os.path.getsize(path) != size:
        return False
    h = hasher(kind, size)
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(CHUNK), b''):
            h.update(block)
    return h.hexdigest() == digest


class Fetcher:
    """Bounded HTTP(S) GET with every hop checked against the allowlist; no credentials, no cookies."""

    def __init__(self, limits, clock=time.monotonic):
        import urllib.request
        self.allow = tuple(limits.get('allowHosts', ALLOW_HOSTS))
        self.schemes = ('https', 'http') if limits.get('allowHttp') else ('https',)
        self.timeout = float(limits.get('readTimeoutS', 30))
        self.deadline = clock() + float(limits['deadlineS'])
        self.max_bytes = int(limits['maxBytes'])
        self.clock, self.bytes_in = clock, 0
        fetcher = self

        class Hop(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                fetcher.check(newurl)
                new = super().redirect_request(req, fp, code, msg, headers, newurl)
                if new is not None and req.get_header('Range'):
                    new.add_unredirected_header('Range', req.get_header('Range'))
                return new

        Hop.max_redirections = MAX_REDIRECTS
        self.opener = urllib.request.build_opener(Hop)
        self.opener.addheaders = [('User-Agent', 'rose-cloud-stage/1')]

    def check(self, url):
        import urllib.parse
        u = urllib.parse.urlsplit(url)
        if u.scheme not in self.schemes or u.hostname not in self.allow or u.username or u.password:
            raise FileFailure('redirect_refused')

    def get(self, url, offset):
        import urllib.error
        import urllib.request
        self.check(url)
        req = urllib.request.Request(url, headers={'Range': 'bytes=%d-' % offset} if offset else {})
        try:
            return self.opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            e.close()
            raise FileFailure('auth_required' if e.code in (401, 403) else 'http_error') from None
        except FileFailure:
            raise
        except (OSError, ValueError):
            raise FileFailure('network') from None

    def pull(self, url, part, size, kind, digest):
        """Fetch into ``part`` (resuming it), then True if the whole file hashes to ``digest``."""
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if have > size:
            raise FileFailure('size_mismatch')
        h = hasher(kind, size)
        if have:
            with open(part, 'rb') as f:
                for block in iter(lambda: f.read(CHUNK), b''):
                    h.update(block)
        if have < size:
            resp = self.get(url, have)
            try:
                status = getattr(resp, 'status', 200)
                if have and status != 206:
                    raise FileFailure('range_refused')  # the partial stays exactly as it was
                length = resp.headers.get('Content-Length')
                if length is not None and (not length.isdigit() or int(length) != size - have):
                    raise FileFailure('size_mismatch')
                read = getattr(resp, 'read1', resp.read)  # what has arrived, so the deadline is checked often
                with open(part, 'ab') as out:
                    while True:
                        if self.clock() >= self.deadline:
                            raise Stop('deadline')
                        if self.bytes_in >= self.max_bytes:
                            raise Stop('byte_cap')
                        try:
                            block = read(min(CHUNK, self.max_bytes - self.bytes_in))
                        except OSError:
                            raise FileFailure('network') from None
                        if not block:
                            break
                        self.bytes_in += len(block)
                        have += len(block)
                        if have > size:
                            raise FileFailure('size_mismatch')
                        out.write(block)
                        h.update(block)
            finally:
                resp.close()
        if have != size:
            raise FileFailure('size_mismatch')
        return h.hexdigest() == digest


def stage(manifest, limits, clock=time.monotonic):
    t0 = clock()
    root = manifest['stagingRoot']
    groups = manifest['groups']
    counts = {g: dict.fromkeys(GROUP_KEYS, 0) for g in groups}
    failures = {g: None for g in groups}
    result = {'stop': None, 'bytesIn': 0, 'elapsedS': 0, 'groups': counts, 'firstFailure': failures,
              'freeBytes': None, 'needBytes': None}
    plan = []
    for name, group in groups.items():
        for rel, size, kind, digest in group['files']:
            if '..' in rel.split('/') or rel.startswith('/'):
                result['stop'] = 'bad_manifest'
                return result
            counts[name]['expected'] += 1
            plan.append((name, group, rel, size, kind, digest, os.path.join(root, group['dir'], rel)))
    # 1. existing files: keep exact ones, refuse the run if any differs (nothing is changed)
    todo, need = [], 0
    for item in plan:
        name, group, rel, size, kind, digest, path = item
        if os.path.exists(path):
            if not file_matches(path, size, kind, digest):
                result['stop'] = 'refused_existing'
                failures[name] = 'existing_differs'
                return result
            counts[name]['present'] += 1
        else:
            part = path + '.partial'
            need += size - (os.path.getsize(part) if os.path.exists(part) else 0)
            todo.append(item)
    os.makedirs(root, exist_ok=True)
    stat = os.statvfs(root)
    result['freeBytes'], result['needBytes'] = stat.f_bavail * stat.f_frsize, need
    if result['freeBytes'] < need * 1.1:
        result['stop'] = 'insufficient_space'
        return result
    # 2. fetch what is missing
    fetcher = Fetcher(limits, clock)
    try:
        for name, group, rel, size, kind, digest, path in todo:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            part = path + '.partial'
            before, resumed = fetcher.bytes_in, os.path.exists(part)
            try:
                ok = fetcher.pull(url_for(name, group, rel, limits), part, size, kind, digest)
            except FileFailure as e:
                counts[name]['failed'] += 1
                failures[name] = failures[name] or str(e)
                continue
            finally:
                counts[name]['bytesIn'] += fetcher.bytes_in - before
            if not ok:
                counts[name]['failed'] += 1
                failures[name] = failures[name] or 'hash_mismatch'
                continue
            os.rename(part, path)
            counts[name]['resumed' if resumed else 'downloaded'] += 1
        result['stop'] = 'file_failures' if any(c['failed'] for c in counts.values()) else 'done'
    except Stop as s:
        result['stop'] = str(s)
    result['bytesIn'] = fetcher.bytes_in
    result['elapsedS'] = round(clock() - t0)
    return result


# ----------------------------------------------------------------- the allowlist bootstrap re-applies
def _count(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 1 << 50 else None


def sanitize(raw, manifest):
    if not isinstance(raw, dict):
        return None
    g = raw.get('groups') if isinstance(raw.get('groups'), dict) else {}
    f = raw.get('firstFailure') if isinstance(raw.get('firstFailure'), dict) else {}
    return {
        'stop': raw.get('stop') if raw.get('stop') in STOPS else None,
        'bytesIn': _count(raw.get('bytesIn')), 'elapsedS': _count(raw.get('elapsedS')),
        'freeBytes': _count(raw.get('freeBytes')), 'needBytes': _count(raw.get('needBytes')),
        'groups': {n: {k: _count((g.get(n) if isinstance(g.get(n), dict) else {}).get(k)) for k in GROUP_KEYS}
                   for n in manifest['groups']},
        'firstFailure': {n: f.get(n) if f.get(n) in FAILURES else None for n in manifest['groups']},
    }


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    manifest, limits = json.loads(argv[0]), json.loads(argv[1])
    try:
        result = stage(manifest, limits)
    except OSError:
        result = {'stop': 'file_failures'}
    print(MARKER + json.dumps(result, sort_keys=True, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main(sys.argv[1:])
