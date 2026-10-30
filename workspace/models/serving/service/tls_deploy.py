#!/usr/bin/env python3
"""Install a renewed certificate with rollback (MODELS-ALWAYSON-SERVICE-001). It is certbot's --deploy-hook.

1. Copy the lineage's fullchain and key into a new generation under --tls-dir (key 0600).
2. Check that generation with render_proxy.check_tls: chain, SAN, expiry, key match and key mode.
3. Switch `current` to it atomically.
4. Run `nginx -t`, then `nginx -s reload` when the edge is running (first issuance happens before it starts).
On any failure `current` returns to the previous generation and Nginx keeps serving the previous chain. The current
and previous generations are kept; older ones are removed. Output: one JSON line, no key material.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('render_proxy', HERE.parent / 'https' / 'render_proxy.py')
render_proxy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render_proxy)


def _copy(src, dst, mode):
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'wb') as f:
        f.write(Path(src).read_bytes())


def _point(tls_dir, target):
    tmp = tls_dir / 'current.tmp'
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(target, tmp)
    os.replace(tmp, tls_dir / 'current')


def running(pidfile):
    """True when the edge's pid file names a live process (first issuance happens before the edge starts)."""
    try:
        os.kill(int(Path(pidfile).read_text().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


def deploy(lineage, tls_dir, hostname, nginx_cmd, run=subprocess.run, now=None, edge_running=True):
    tls_dir = Path(tls_dir)
    tls_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    previous = os.readlink(tls_dir / 'current') if (tls_dir / 'current').is_symlink() else None
    gen = tls_dir / time.strftime('gen-%Y%m%dT%H%M%SZ', time.gmtime(now))
    gen.mkdir(mode=0o755)
    try:
        _copy(Path(lineage) / 'fullchain.pem', gen / 'fullchain.pem', 0o644)
        _copy(Path(lineage) / 'privkey.pem', gen / 'privkey.pem', 0o600)
        facts = render_proxy.check_tls(str(gen / 'fullchain.pem'), str(gen / 'privkey.pem'), hostname, now)
    except (render_proxy.Invalid, OSError, ValueError) as e:
        shutil.rmtree(gen)
        return {'result': 'refused', 'stage': 'check', 'reason': str(e) if isinstance(e, render_proxy.Invalid) else type(e).__name__}
    _point(tls_dir, gen.name)
    for stage, args in (('nginx_test', ['-t']), ('reload', ['-s', 'reload']))[:2 if edge_running else 1]:
        if run([*nginx_cmd, *args], capture_output=True, timeout=60).returncode != 0:
            if previous:
                _point(tls_dir, previous)
                run([*nginx_cmd, '-s', 'reload'], capture_output=True, timeout=60)
            else:
                (tls_dir / 'current').unlink()
            shutil.rmtree(gen)
            return {'result': 'rolled_back', 'stage': stage, 'current': previous}
    for old in tls_dir.glob('gen-*'):
        if old.name not in (gen.name, previous):
            shutil.rmtree(old)
    return {'result': 'deployed', 'current': gen.name, 'previous': previous, 'reloaded': edge_running,
            'notAfter': facts['not_after']}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--lineage', default=os.environ.get('RENEWED_LINEAGE'), help='certbot live dir (default $RENEWED_LINEAGE)')
    p.add_argument('--tls-dir', type=Path, default=Path('/etc/rose-models/tls'))
    p.add_argument('--hostname', default='models.a-list.com')
    p.add_argument('--pidfile', default='/run/rose-edge/nginx.pid')
    # Separate options: values starting with '-' cannot follow a multi-value option (the rendered hook exited 2).
    p.add_argument('--nginx', default='/usr/sbin/nginx', help='nginx binary')
    p.add_argument('--nginx-prefix', default='/run/rose-edge')
    p.add_argument('--nginx-conf', default='/etc/rose-models/edge/nginx.conf')
    a = p.parse_args(argv)
    if not a.lineage:
        print(json.dumps({'result': 'refused', 'stage': 'input', 'reason': 'no lineage'}))
        return 2
    nginx = [a.nginx, '-p', a.nginx_prefix, '-c', a.nginx_conf]
    out = deploy(a.lineage, a.tls_dir, a.hostname, nginx, edge_running=running(a.pidfile))
    print(json.dumps(out, sort_keys=True))
    return 0 if out['result'] == 'deployed' else 1


if __name__ == '__main__':
    sys.exit(main())
