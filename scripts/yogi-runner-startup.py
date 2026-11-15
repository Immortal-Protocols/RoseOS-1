#!/usr/bin/python3
"""Approved deployment payload only; not installed or executed by source tests.

Start only the existing replacement Yogi runner service after verifying its
instance and filesystem. No SSH credential, registration, service enablement,
IAM, existing-directory ownership change or build operation.
"""
import os
from pathlib import Path
import pwd
import subprocess
import urllib.request

INSTANCE = 'roseos-yogi-builder-20261105-b'
UNIT = 'actions.runner.Immortal-Protocols-RoseOS-1.'+INSTANCE+'.service'
ROOT = Path('/srv/rose-yogi')


def main():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def meta(key):
        request = urllib.request.Request('http://metadata.google.internal/computeMetadata/v1/'+key,
                                         headers={'Metadata-Flavor': 'Google'})
        with opener.open(request, timeout=5) as response:
            return response.read(4096).decode().strip()
    actual = (meta('project/project-id'), meta('instance/zone').rsplit('/', 1)[-1], meta('instance/name'), meta('instance/id'))
    if actual != ('protocols-465723', 'us-west2-b', INSTANCE, '2720796557371812468') or os.geteuid() != 0:
        raise SystemExit('Yogi runner startup refused: wrong instance or execution identity')
    if any(part.is_symlink() for part in (ROOT, *ROOT.parents)) or not ROOT.is_dir():
        raise SystemExit('Yogi runner startup refused: root path differs')
    observed = subprocess.check_output(['findmnt', '--noheadings', '--output', 'UUID', '--target', str(ROOT)], text=True).strip()
    if observed != '64959ab3-d595-45a4-9cb8-da4af456efdd':
        raise SystemExit('Yogi runner startup refused: restored filesystem differs')
    user = subprocess.check_output(['systemctl', 'show', UNIT, '--property=User', '--value'], text=True).strip()
    if user != 'pixelbuild':
        raise SystemExit('Yogi runner startup refused: existing service user differs')
    account = pwd.getpwnam(user)
    for name in ('signing-bootstrap-state', 'compiler-cache-v1'):
        path = ROOT/name
        if path.is_symlink():
            raise SystemExit('Yogi runner startup refused: state directory is a link')
        if not path.exists():
            path.mkdir(mode=0o700)
            os.chown(path, account.pw_uid, account.pw_gid)
        if not path.is_dir() or path.stat().st_uid != account.pw_uid or path.stat().st_dev != ROOT.stat().st_dev:
            raise SystemExit('Yogi runner startup refused: state owner/filesystem differs')
    subprocess.run(['systemctl', 'start', UNIT], check=True, timeout=60)
    print('Exact replacement Yogi runner started; service enablement unchanged')


if __name__ == '__main__':
    main()
