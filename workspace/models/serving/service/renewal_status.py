#!/usr/bin/env python3
"""Renewal evidence for the persistent service's certificate (MODEL-STATUS-RENEWAL-001). Read-only, except for one
random probe file in the ACME webroot that it removes again. It never runs certbot or contacts the CA.

  publish   collect the evidence, write /run/rose-models/renewal.json and the guest attribute rose-models/renewal

Evidence (nonsecret; no key material, account ids or certificate contents):
- certbot.timer: enabled, active, next and last run (unix seconds), one `schedule` category (query_failed, not_found,
  disabled, inactive, timestamp_format_unsupported, unscheduled, out_of_range, scheduled) and the triggered service's
  state and last result;
- renewal config for models.a-list.com: webroot authenticator, webroot path, deploy hook, and that its account is the
  single existing account;
- certificate: the installed chain passes check_tls and equals the lineage's chain; notAfter and days left;
- edge ACME proof, CURRENT: the edge's pid file names a running process of the pinned nginx binary, loaded with the
  edge config, holding the :80 listener; a fresh challenge returns 200 with its exact content and every other path
  returns 404.
`renewalReady` is true only when all of these hold; reopening the approved :80 ingress is conditional on it.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('render_proxy', HERE.parent / 'https' / 'render_proxy.py')
render_proxy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render_proxy)
_spec = importlib.util.spec_from_file_location('unit_facts', HERE / 'unit_facts.py')
unit_facts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(unit_facts)

SCHEMA = 'rose-models-renewal/1'
HOSTNAME = 'models.a-list.com'
ROOT = '/etc/rose-models'
LE = '/etc/letsencrypt'
WEBROOT = '/var/lib/rose-models/acme'
DEPLOY_HOOK = f'{ROOT}/bin/tls-deploy'
EDGE = {'nginx': '/usr/sbin/nginx', 'run': '/run/rose-edge', 'conf': f'{ROOT}/edge/nginx.conf', 'port': 80}
NOT_FOUND = ('/', '/v1/models', '/v1/chat/completions', '/v1/auth/challenge', '/v1/auth/token')
GUEST = 'http://metadata.google.internal/computeMetadata/v1/instance/guest-attributes/rose-models/'
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def timer(run=subprocess.run, now=None, unit='certbot.timer', scope=()):
    """certbot.timer and the service it triggers, with one `schedule` category (unit_facts.timer)."""
    return unit_facts.timer(unit, run, scope, now)


def renewal_config(prefix='/'):
    """What certbot renew will do for HOSTNAME, read from its renewal file. Facts are booleans and counts only."""
    root = Path(prefix)
    acct_dir = root / LE.lstrip('/') / 'accounts/acme-v02.api.letsencrypt.org/directory'
    accounts = sorted(p.name for p in acct_dir.iterdir() if p.is_dir()) if acct_dir.is_dir() else []
    conf = root / LE.lstrip('/') / f'renewal/{HOSTNAME}.conf'
    try:
        text = conf.read_text()
    except OSError:
        return {'present': False, 'accounts': len(accounts)}
    values, section = {}, ''
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('['):
            section = line.strip('[]')
        elif '=' in line and not line.startswith('#'):
            k, v = (x.strip() for x in line.split('=', 1))
            values[(section, k)] = v
    webroot_map = values.get(('webroot_map', HOSTNAME))
    webroot_path = values.get(('renewalparams', 'webroot_path'), '').rstrip(',').strip()
    hook = values.get(('renewalparams', 'renew_hook')) or values.get(('renewalparams', 'deploy_hook'))
    account = values.get(('renewalparams', 'account'))
    return {'present': True, 'authenticatorWebroot': values.get(('renewalparams', 'authenticator')) == 'webroot',
            'webroot': (webroot_map or webroot_path) == WEBROOT, 'deployHook': hook == DEPLOY_HOOK,
            'accounts': len(accounts), 'accountIsTheSingleAccount': len(accounts) == 1 and account == accounts[0]}


def certificate(prefix='/', now=None):
    root = Path(prefix)
    current = root / ROOT.lstrip('/') / 'tls/current'
    live = root / LE.lstrip('/') / f'live/{HOSTNAME}'
    out = {'installed': False, 'check': None, 'notAfter': None, 'daysLeft': None, 'matchesLineage': False}
    if not (current / 'fullchain.pem').exists():
        return out
    out['installed'] = True
    try:
        facts = render_proxy.check_tls(str(current / 'fullchain.pem'), str(current / 'privkey.pem'), HOSTNAME, now)
        out['check'] = 'ok'
        not_after = int(render_proxy.ssl.cert_time_to_seconds(facts['not_after']))
        out.update(notAfter=not_after, daysLeft=int((not_after - (time.time() if now is None else now)) // 86400))
    except render_proxy.Invalid:
        out['check'] = 'refused'
    except (OSError, ValueError):
        out['check'] = 'unreadable'
    try:
        digest = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (current / 'fullchain.pem', live / 'fullchain.pem')]
        out['matchesLineage'] = digest[0] == digest[1]
    except OSError:
        pass
    return out


def _running(prefix, pid):
    try:
        return (Path(prefix) / f'proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except (OSError, IndexError):
        return False


def _listener_inodes(prefix, port):
    inodes = set()
    for table in ('proc/net/tcp', 'proc/net/tcp6'):
        try:
            lines = (Path(prefix) / table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if len(f) > 9 and f[3] == '0A' and int(f[1].rsplit(':', 1)[1], 16) == port:
                inodes.add(f[9])
    return inodes


def edge_process(edge=EDGE, prefix='/'):
    """{pid, exe, config, listener}: the edge's pid file names a running process of the pinned binary, loaded with
    the edge config, that holds the :port listener."""
    root = Path(prefix)
    out = {'pid': False, 'exe': False, 'config': False, 'listener': False}
    try:
        pid = int((root / edge['run'].lstrip('/') / 'nginx.pid').read_text().strip())
    except (OSError, ValueError):
        return out
    if pid <= 0 or not _running(prefix, pid):
        return out
    out['pid'] = True
    proc = root / f'proc/{pid}'
    try:
        out['exe'] = os.readlink(proc / 'exe') == str((root / edge['nginx'].lstrip('/')).resolve())
    except OSError:
        pass
    try:
        args = (proc / 'cmdline').read_bytes()[:4096].decode('utf-8', 'replace').replace('\0', ' ')
        out['config'] = f" -c {edge['conf']} " in args + ' '
    except OSError:
        pass
    inodes = _listener_inodes(prefix, edge['port'])
    try:
        fds = list((proc / 'fd').iterdir())
    except OSError:
        fds = []
    for fd in fds:
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith('socket:[') and target[8:-1] in inodes:
            out['listener'] = True
            break
    return out


def http_get(url):
    req = urllib.request.Request(url, headers={'Host': HOSTNAME})
    try:
        with NO_PROXY.open(req, timeout=10) as r:
            return r.status, r.read(256)
    except urllib.error.HTTPError as e:
        return e.code, b''
    except OSError:
        return 0, b''


def edge_acme(edge=EDGE, webroot=WEBROOT, prefix='/', get=http_get):
    """A fresh challenge through the RUNNING edge's :80 server: 200 with exact content, every other path 404."""
    challenges = Path(prefix) / webroot.lstrip('/') / '.well-known/acme-challenge'
    token, content = 'rose-renewal-probe-' + os.urandom(8).hex(), os.urandom(16).hex()
    base = f"http://127.0.0.1:{edge['port']}"
    try:
        challenges.mkdir(parents=True, exist_ok=True)
        probe = challenges / token
        probe.write_text(content)
        os.chmod(probe, 0o644)
    except OSError:
        return {'challenge': None, 'notFound': None, 'ok': False}
    try:
        got = get(f'{base}/.well-known/acme-challenge/{token}')
        codes = {p: get(base + p)[0] for p in NOT_FOUND}
    finally:
        probe.unlink(missing_ok=True)
    ok = got == (200, content.encode()) and all(c == 404 for c in codes.values())
    return {'challenge': got[0], 'notFound': codes, 'ok': ok}


def boot_id(prefix='/'):
    try:
        value = (Path(prefix) / 'proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        return 'unknown'
    return value if re.match(r'^[0-9a-f-]{36}$', value) else 'unknown'


def collect(prefix='/', now=None, run=subprocess.run, get=http_get, edge=EDGE, webroot=WEBROOT, proc_prefix='/'):
    now = time.time() if now is None else now
    t, conf, cert = timer(run, now), renewal_config(prefix), certificate(prefix, now)
    proc = edge_process(edge, proc_prefix)
    acme = edge_acme(edge, webroot, prefix, get) if all(proc.values()) else {'challenge': None, 'notFound': None,
                                                                              'ok': False}
    ready = (t['schedule'] == 'scheduled' and conf.get('present') is True
             and all(conf.get(k) is True for k in ('authenticatorWebroot', 'webroot', 'deployHook',
                                                     'accountIsTheSingleAccount'))
             and cert['check'] == 'ok' and cert['matchesLineage'] and all(proc.values()) and acme['ok'])
    return {'schema': SCHEMA, 'bootId': boot_id(proc_prefix), 'checkedAt': int(now), 'certbotTimer': t,
            'renewalConfig': conf, 'certificate': cert, 'edgeProcess': proc, 'edgeAcme': acme, 'renewalReady': ready}


def publish(key, line, timeout=5):
    req = urllib.request.Request(GUEST + key, data=line.encode(), headers={'Metadata-Flavor': 'Google'}, method='PUT')
    NO_PROXY.open(req, timeout=timeout).close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('publish',))
    p.add_argument('--out', type=Path, default=Path('/run/rose-models/renewal.json'))
    a = p.parse_args(argv)
    evidence = collect()
    line = json.dumps(evidence, sort_keys=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(a.out) + '.tmp')
    tmp.write_text(line + '\n')
    os.replace(tmp, a.out)
    try:
        publish('renewal', line)
        published = True
    except OSError:
        published = False
    print(json.dumps({'renewalReady': evidence['renewalReady'], 'published': published}))
    return 0 if evidence['renewalReady'] else 1


if __name__ == '__main__':
    sys.exit(main())
