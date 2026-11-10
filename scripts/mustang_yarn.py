"""Install the pinned public Yarn CLI only inside a new preparation attempt."""
import hashlib
import json
import os
from pathlib import Path
import tarfile
import time
import urllib.request

import mustang_hostdeps as deps

ROOT = Path(__file__).resolve().parents[1]
PIN = 'os/build-only/mustang-yarn-1.22.22.json'
URL = 'https://registry.npmjs.org/yarn/-/yarn-1.22.22.tgz'
UNPACKED_BYTES = 8 << 20


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError('pinned Yarn archive redirect refused')


def pin(root=ROOT):
    record = json.loads((Path(root)/PIN).read_text())
    if record.get('version') != '1.22.22' or record.get('url') != URL \
            or type(record.get('sizeBytes')) is not int or not 0 < record['sizeBytes'] <= 2 << 20 \
            or len(bytes.fromhex(record.get('sha256',''))) != 32:
        raise ValueError('Yarn public archive pin differs')
    return record


def install(attempt, deadline, *, root=ROOT, opener=None):
    if time.monotonic() >= deadline:raise ValueError('Yarn bootstrap total timeout')
    record = pin(root)
    tools = attempt/'yarn-tools';tools.mkdir(mode=0o700);deps.plain_dir(tools)
    archive = tools/'yarn.tgz'
    connect = opener or urllib.request.build_opener(NoRedirect()).open
    request = urllib.request.Request(record['url'],headers={'Accept-Encoding':'identity'})
    digest = hashlib.sha256();size = 0
    with connect(request,timeout=60) as response:
        length = response.headers.get('Content-Length','')
        if response.status != 200 or response.geturl() != record['url'] \
                or response.headers.get('Content-Encoding','identity') != 'identity' \
                or not length.isascii() or not length.isdigit() or int(length) != record['sizeBytes']:
            raise ValueError('Yarn archive HTTP identity or length differs')
        with open(archive,'xb') as output:
            os.chmod(archive,0o600)
            while True:
                if time.monotonic() >= deadline:raise ValueError('Yarn bootstrap total timeout')
                block = response.read(min(1 << 20,record['sizeBytes']-size+1))
                if not block:break
                size += len(block)
                if size > record['sizeBytes']:raise ValueError('Yarn archive byte bound exceeded')
                output.write(block);digest.update(block)
            output.flush();os.fsync(output.fileno())
    if size != record['sizeBytes'] or digest.hexdigest() != record['sha256']:
        raise ValueError('Yarn public archive size or SHA-256 differs')
    total = 0;seen = set();catalog = []
    with tarfile.open(archive,'r:gz') as bundle:
        for entry in bundle:
            if time.monotonic() >= deadline:raise ValueError('Yarn bootstrap total timeout')
            parts = entry.name.split('/')
            if not parts or parts[0] != 'package' or any(p in ('','.','..') for p in parts) \
                    or '\\' in entry.name or entry.name in seen or len(seen) >= 32 \
                    or not (entry.isfile() or entry.isdir()) or entry.issparse():
                raise ValueError('Yarn archive member refused')
            seen.add(entry.name);target = tools.joinpath(*parts)
            if entry.isdir():
                target.mkdir(mode=0o700,parents=True,exist_ok=True);continue
            total += entry.size
            if entry.size < 0 or total > UNPACKED_BYTES:
                raise ValueError('Yarn unpacked byte bound exceeded')
            target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            with bundle.extractfile(entry) as source,open(target,'xb') as output:
                os.chmod(target,0o700 if entry.mode & 0o111 else 0o600)
                remaining = entry.size
                while remaining:
                    if time.monotonic() >= deadline:raise ValueError('Yarn bootstrap total timeout')
                    block = source.read(min(1 << 20,remaining))
                    if not block:raise ValueError('Yarn archive member is truncated')
                    output.write(block);remaining -= len(block)
                output.flush();os.fsync(output.fileno())
            catalog.append(dict(path=entry.name,**deps.fingerprint(attempt,target,deadline)))
    script = tools/'package/bin/yarn.js'
    if not script.is_file() or script.is_symlink() or not (tools/'package/lib/cli.js').is_file():
        raise ValueError('Yarn CLI package entrypoints are absent')
    return {'path':str(script),'version':record['version'],'archiveSha256':digest.hexdigest(),
            'archiveBytes':size,'unpackedBytes':total,'url':record['url'],
            'catalog':catalog,'credentialsUsed':False,'systemPackagesChanged':False}
