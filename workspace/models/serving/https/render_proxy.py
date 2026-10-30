#!/usr/bin/env python3
"""Render the Rose Models HTTPS edge: Caddy (public TLS :443) -> Nginx (127.0.0.1:8080) -> vLLM (127.0.0.1:8000).

Commands are offline. They write files and read the token file; nothing here contacts ACME, DNS,
GCP or any endpoint, and nothing starts a service.

  render     Write nginx.conf (plus caddy.json in loopback mode) and manifest.json from closed,
             validated inputs. Two front modes:
               --front tls       Nginx terminates TLS itself on :443 with an explicit fullchain and key.
                                 This is the deployment mode while Caddy acquisition is held.
               --front loopback  Nginx on 127.0.0.1:8080 behind Caddy. caddy.json is unvalidated.
  check-tls  Check a certificate/key pair before starting: the key is 0600 and owned by the caller,
             the leaf's SAN names the hostname exactly, the leaf is unexpired, CA-issued and matches
             the key, and the fullchain carries at least one issuer certificate.
  token-map  Turn a protected token file into the 0600 Nginx include that holds the bearer match.
             The token never appears in argv, stdout, the rendered configs or the manifest.

Nginx enforces everything that matters for access:
- exactly POST /v1/chat/completions and GET /v1/models;
- an exact bearer match, with no query string accepted;
- Authorization stripped before the backend;
- 256 KiB body cap;
- a global 6 r/m limit with burst 2, plus one in-flight request, both returning 429;
- 120 s upstream timeout;
- no access log and no disk buffering.

Caddy only terminates TLS for the one hostname.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import stat
import sys
import time

SCHEMA = 'rose-models-https-edge/1'
HOST_RE = re.compile(r'^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')
# Paths are interpolated into config files, so they must be plain absolute paths.
PATH_RE = re.compile(r'^/[A-Za-z0-9._/-]+$')
IPV4_RE = re.compile(r'^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$')
TOKEN_RE = re.compile(r'^([A-Za-z0-9_-]{43}|[0-9a-f]{64})$')  # 256 bits, base64url or hex
ROUTES = {'/v1/chat/completions': 'POST', '/v1/models': 'GET'}
BODY_LIMIT = '256k'
RATE = '6r/m'
BURST = 2
DEFAULTS = {'hostname': 'models.a-list.com', 'nginx_port': 8080, 'upstream_port': 8000, 'timeout_s': 120}
LE_DIRECTORY = 'https://acme-v02.api.letsencrypt.org/directory'
# service-auth-v1 (workspace/models/contracts/service-auth-v1.md): per-IP limits on the credential routes.
AUTH_ROUTES = {'/v1/auth/challenge': 'authchallenge', '/v1/auth/token': 'authtoken'}
AUTH_RATES = {'authchallenge': '20r/m', 'authtoken': '10r/m'}
AUTH_BODY_LIMIT = '64k'


class Invalid(Exception):
    pass


def check_inputs(hostname, runtime_dir, nginx_port, upstream_port, timeout_s):
    if not HOST_RE.match(hostname):
        raise Invalid(f'hostname {hostname!r} is not a lowercase DNS name')
    if not PATH_RE.match(runtime_dir) or '/../' in runtime_dir + '/' or runtime_dir.endswith('/'):
        raise Invalid('runtime dir must be a plain absolute path without .. or a trailing slash')
    for name, port in (('nginx port', nginx_port), ('upstream port', upstream_port)):
        if not 1024 <= port <= 65535:
            raise Invalid(f'{name} {port} must be 1024-65535')
    if nginx_port == upstream_port:
        raise Invalid('nginx and upstream ports must differ')
    if not 1 <= timeout_s <= 120:
        raise Invalid('timeout must be 1-120 seconds (120 for deployment)')


def service_servers(nginx_port, upstream_port, tls, auth_port, acme):
    """service-auth-v1 mode (TLS only). The public server authenticates every inference request with auth_request,
    then forwards it to an internal loopback server that applies the global rate and in-flight limits, so only
    authenticated requests consume them. /v1/auth/* go to the loopback auth service with per-IP limits."""
    public, internal = [], []
    for path, method in ROUTES.items():
        public.append(f'''        location = {path} {{
            if ($request_uri != "{path}") {{ return 404; }}
            if ($request_method != {method}) {{ return 405; }}
            auth_request /_validate;
            proxy_set_header Authorization "";
            proxy_pass http://127.0.0.1:{nginx_port};
        }}''')
        internal.append(f'''        location = {path} {{
            limit_req zone=global burst={BURST} nodelay;
            limit_conn inflight 1;
            proxy_pass http://127.0.0.1:{upstream_port};
        }}''')
    for path, zone in AUTH_ROUTES.items():
        public.append(f'''        location = {path} {{
            if ($request_uri != "{path}") {{ return 404; }}
            if ($request_method != POST) {{ return 405; }}
            limit_req zone={zone} burst=5 nodelay;
            client_max_body_size {AUTH_BODY_LIMIT};
            proxy_set_header Authorization "";
            proxy_pass http://127.0.0.1:{auth_port};
        }}''')
    public.append(f'''        location = /_validate {{
            internal;
            proxy_method GET;
            proxy_pass_request_body off;
            proxy_set_header Content-Length "";
            proxy_pass http://127.0.0.1:{auth_port}/_validate;
        }}''')
    listen = f"{tls['address']}:{tls['port']} ssl"
    acme_server = '' if acme is None else f'''
    # ACME HTTP-01 for renewal only: the challenge path is served from the webroot, everything else is 404.
    server {{
        listen {tls['address']}:{acme['port']};
        server_name _;
        location ^~ /.well-known/acme-challenge/ {{ root {acme['webroot']}; default_type text/plain; }}
        location / {{ return 404; }}
    }}
'''
    nl = '\n'
    return f'''    server {{
        listen {listen} default_server;
        server_name _;
        ssl_reject_handshake on;
        return 421;
    }}

    server {{
        listen {listen};
        server_name {tls['hostname']};
        ssl_certificate {tls['fullchain']};
        ssl_certificate_key {tls['key']};
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_session_tickets off;
        if ($args != "") {{ return 400; }}
{nl.join(public)}
        location / {{ return 404; }}
    }}

    # Internal hop: reachable on loopback only, after authentication.
    server {{
        listen 127.0.0.1:{nginx_port};
        server_name _;
{nl.join(internal)}
        location / {{ return 404; }}
    }}
{acme_server}'''


def nginx_conf(runtime_dir, nginx_port, upstream_port, timeout_s, tls=None, auth='token-map', auth_port=None, acme=None):
    locations = []
    for path, method in ROUTES.items():
        locations.append(f'''        location = {path} {{
            # Exact raw path only: rejects encoded or doubled-slash forms that normalize to it.
            if ($request_uri != "{path}") {{ return 404; }}
            if ($request_method != {method}) {{ return 405; }}
            if ($auth_ok != 1) {{ return 401; }}
            limit_req zone=global burst={BURST} nodelay;
            limit_conn inflight 1;
            proxy_set_header Authorization "";
            proxy_pass http://127.0.0.1:{upstream_port};
        }}''')
    body = '\n'.join(locations)
    if auth == 'service':
        servers = service_servers(nginx_port, upstream_port, tls, auth_port, acme)
    elif tls is None:
        servers = f'''    server {{
        listen 127.0.0.1:{nginx_port};
        server_name _;
        if ($args != "") {{ return 400; }}
{body}
        location / {{ return 404; }}
    }}'''
    else:
        listen = f"{tls['address']}:{tls['port']} ssl"
        servers = f'''    # Any SNI other than the one hostname (or none) fails the TLS handshake. A request whose
    # Host names another server is routed here too and gets 421.
    server {{
        listen {listen} default_server;
        server_name _;
        ssl_reject_handshake on;
        return 421;
    }}

    server {{
        listen {listen};
        server_name {tls['hostname']};
        ssl_certificate {tls['fullchain']};
        ssl_certificate_key {tls['key']};
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_session_tickets off;
        if ($args != "") {{ return 400; }}
{body}
        location / {{ return 404; }}
    }}'''
    if auth == 'service':
        credentials = '\n'.join(f'    limit_req_zone $binary_remote_addr zone={zone}:1m rate={rate};'
                                 for zone, rate in AUTH_RATES.items())
    else:
        credentials = f'''    # The only line naming the credential lives in this 0600 file, written by `token-map`.
    map $http_authorization $auth_ok {{
        default 0;
        include {runtime_dir}/token.map;
    }}'''
    return f'''# Rendered by workspace/models/serving/https/render_proxy.py ({SCHEMA}). Do not edit by hand.
worker_processes 1;
pid {runtime_dir}/nginx.pid;
# crit only: lower levels can echo request lines, which must not reach logs.
error_log stderr crit;

events {{ worker_connections 64; }}

http {{
    access_log off;
    log_not_found off;
    server_tokens off;
    client_body_temp_path {runtime_dir}/client_body;
    proxy_temp_path {runtime_dir}/proxy;
    fastcgi_temp_path {runtime_dir}/fastcgi;
    uwsgi_temp_path {runtime_dir}/uwsgi;
    scgi_temp_path {runtime_dir}/scgi;

    client_max_body_size {BODY_LIMIT};
    client_body_buffer_size {BODY_LIMIT};
    proxy_buffering off;
    proxy_max_temp_file_size 0;
    proxy_redirect off;
    proxy_http_version 1.1;
    proxy_connect_timeout 5s;
    proxy_send_timeout {timeout_s}s;
    proxy_read_timeout {timeout_s}s;

    # The bearer key plus a 64-character token exceeds the default 64-byte map bucket.
    map_hash_bucket_size 128;
    map $host $edge_key {{ default edge; }}
    limit_req_zone $edge_key zone=global:1m rate={RATE};
    limit_req_status 429;
    limit_req_log_level warn;
    limit_conn_zone $edge_key zone=inflight:1m;
    limit_conn_status 429;
    limit_conn_log_level warn;

{credentials}

{servers}
}}
'''


def caddy_json(hostname, nginx_port):
    return {
        'admin': {'disabled': True},
        'logging': {'logs': {'default': {'level': 'ERROR'}}},
        'apps': {
            'http': {
                'servers': {
                    'models': {
                        'listen': [':443'],
                        'automatic_https': {'disable_redirects': True},
                        'routes': [
                            {'match': [{'host': [hostname]}],
                             'handle': [{'handler': 'reverse_proxy', 'upstreams': [{'dial': f'127.0.0.1:{nginx_port}'}]}],
                             'terminal': True},
                            {'handle': [{'handler': 'static_response', 'status_code': 421}], 'terminal': True},
                        ],
                    },
                },
            },
            'tls': {
                'automation': {
                    'policies': [{
                        'subjects': [hostname],
                        'issuers': [{
                            'module': 'acme',
                            'ca': LE_DIRECTORY,
                            'challenges': {'http': {'disabled': True}, 'tls-alpn': {'disabled': False}},
                        }],
                    }],
                },
            },
        },
    }


def check_tls_inputs(fullchain, key, address, port):
    for name, path in (('fullchain', fullchain), ('key', key)):
        if not path or not PATH_RE.match(path) or '/../' in path + '/':
            raise Invalid(f'{name} path must be a plain absolute path')
    if address not in ('0.0.0.0', '127.0.0.1') and not IPV4_RE.match(address):
        raise Invalid('public address must be an IPv4 address')
    if not (port == 443 or 1024 <= port <= 65535):
        raise Invalid('public port must be 443 or 1024-65535')


def render(out_dir, hostname, runtime_dir, nginx_port, upstream_port, timeout_s, front='loopback',
           fullchain=None, key=None, public_address='0.0.0.0', public_port=443, auth='token-map', auth_port=8090,
           acme_webroot=None, acme_port=80):
    check_inputs(hostname, runtime_dir, nginx_port, upstream_port, timeout_s)
    if front not in ('loopback', 'tls'):
        raise Invalid('front must be tls or loopback')
    if auth not in ('token-map', 'service') or (auth == 'service' and front != 'tls'):
        raise Invalid('auth must be token-map, or service with the tls front')
    acme = None
    if auth == 'service':
        if not 1024 <= auth_port <= 65535 or auth_port in (nginx_port, upstream_port, public_port):
            raise Invalid('auth port must be 1024-65535 and distinct')
        if acme_webroot is not None:
            if not PATH_RE.match(acme_webroot) or '/../' in acme_webroot + '/':
                raise Invalid('acme webroot must be a plain absolute path')
            if not (acme_port == 80 or 1024 <= acme_port <= 65535) or acme_port == public_port:
                raise Invalid('acme port must be 80 or 1024-65535 and differ from the public port')
            acme = {'webroot': acme_webroot, 'port': acme_port}
    elif acme_webroot is not None:
        raise Invalid('acme webroot needs auth=service')
    out_dir.mkdir(parents=True, exist_ok=True)
    if front == 'tls':
        check_tls_inputs(fullchain, key, public_address, public_port)
        if public_port == upstream_port:
            raise Invalid('public and upstream ports must differ')
        tls = {'address': public_address, 'port': public_port, 'hostname': hostname, 'fullchain': fullchain, 'key': key}
        files = {'nginx.conf': nginx_conf(runtime_dir, nginx_port, upstream_port, timeout_s, tls,
                                          auth, auth_port, acme).encode()}
    else:
        files = {
            'nginx.conf': nginx_conf(runtime_dir, nginx_port, upstream_port, timeout_s).encode(),
            'caddy.json': (json.dumps(caddy_json(hostname, nginx_port), indent=2, sort_keys=True) + '\n').encode(),
        }
    for stale in ('nginx.conf', 'caddy.json'):
        if stale not in files and (out_dir/stale).exists():
            (out_dir/stale).unlink()
    for name, data in files.items():
        (out_dir/name).write_bytes(data)
    if front == 'tls':
        inputs = {'front': 'tls', 'hostname': hostname, 'runtime_dir': runtime_dir,
                  'public_listen': f'{public_address}:{public_port}', 'fullchain': fullchain, 'key': key,
                  'upstream': f'127.0.0.1:{upstream_port}', 'timeout_s': timeout_s}
        tls_policy = {'terminator': 'nginx', 'protocols': ['TLSv1.2', 'TLSv1.3'], 'unknown_sni': 'handshake rejected',
                      'certificate': 'provisioned separately; Certbot manual DNS-01 is the selected candidate'}
    else:
        inputs = {'hostname': hostname, 'runtime_dir': runtime_dir, 'nginx_listen': f'127.0.0.1:{nginx_port}',
                  'upstream': f'127.0.0.1:{upstream_port}', 'timeout_s': timeout_s, 'public_listen': ':443'}
        tls_policy = None
    manifest = {
        'schema': SCHEMA,
        'inputs': inputs,
        'policy': {'routes': ROUTES, 'body_limit': BODY_LIMIT, 'rate': RATE, 'burst': BURST, 'in_flight': 1},
        'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        'token_map': f'{runtime_dir}/token.map',
    }
    if auth == 'service':
        manifest.pop('token_map')
        manifest['inputs'].update(auth='service-auth-v1', auth_upstream=f'127.0.0.1:{auth_port}',
                                  acme=None if acme is None else f"{public_address}:{acme['port']} {acme['webroot']}")
        manifest['policy']['auth_routes'] = {path: AUTH_RATES[zone] + ' per IP' for path, zone in AUTH_ROUTES.items()}
    if tls_policy:
        manifest['policy']['tls'] = tls_policy
    else:
        manifest['policy']['acme'] = {'directory': LE_DIRECTORY, 'challenge': 'tls-alpn-01', 'http_challenge': 'disabled'}
    (out_dir/'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


def token_map(token_file, out):
    st = os.stat(token_file)
    if not stat.S_ISREG(st.st_mode):
        raise Invalid('token file is not a regular file')
    if st.st_mode & 0o077:
        raise Invalid('token file must not be readable by group or others (chmod 600)')
    if st.st_uid != os.getuid():
        raise Invalid('token file must be owned by the user running this command')
    token = Path(token_file).read_text().strip()
    if not TOKEN_RE.match(token):
        raise Invalid('token must be 256 random bits: 43 base64url characters or 64 lowercase hex')
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        # Plain map keys compare case-insensitively; an anchored `~` regex is case-sensitive and exact.
        f.write(f'"~^Bearer {token}$" 1;\n')
    os.chmod(out, 0o600)


def check_tls(fullchain, key, hostname, now=None):
    """Refuse a certificate/key pair that cannot serve `hostname` safely. Returns nonsecret facts."""
    st = os.stat(key)
    if not stat.S_ISREG(st.st_mode):
        raise Invalid('key is not a regular file')
    if st.st_mode & 0o077:
        raise Invalid('key must not be readable by group or others (chmod 600)')
    if st.st_uid != os.getuid():
        raise Invalid('key must be owned by the user running this command')
    chain = Path(fullchain).read_text()
    count = chain.count('-----BEGIN CERTIFICATE-----')
    if count < 2:
        raise Invalid('fullchain must hold the leaf followed by its issuer certificate(s)')
    try:
        leaf = ssl._ssl._test_decode_cert(fullchain)
    except Exception as e:
        raise Invalid(f'fullchain leaf does not parse: {type(e).__name__}') from None
    names = [v for k, v in leaf.get('subjectAltName', ()) if k == 'DNS']
    if hostname not in names:
        raise Invalid(f'leaf SAN {names} does not name {hostname} exactly')
    if leaf.get('issuer') == leaf.get('subject'):
        raise Invalid('leaf is self-signed')
    not_after = ssl.cert_time_to_seconds(leaf['notAfter'])
    if not_after <= (now if now is not None else time.time()):
        raise Invalid('leaf has expired')
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        ctx.load_cert_chain(fullchain, key)
    except ssl.SSLError:
        raise Invalid('key does not match the leaf certificate') from None
    return {'hostname': hostname, 'san': names, 'chain_certificates': count, 'not_after': leaf['notAfter'],
            'key_mode': oct(st.st_mode & 0o777)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('render')
    r.add_argument('--out-dir', type=Path, required=True)
    r.add_argument('--runtime-dir', required=True, help='task-private dir: pid, temp paths and token.map')
    r.add_argument('--hostname', default=DEFAULTS['hostname'])
    r.add_argument('--nginx-port', type=int, default=DEFAULTS['nginx_port'])
    r.add_argument('--upstream-port', type=int, default=DEFAULTS['upstream_port'])
    r.add_argument('--timeout-seconds', type=int, default=DEFAULTS['timeout_s'])
    r.add_argument('--front', choices=('tls', 'loopback'), default='loopback')
    r.add_argument('--fullchain', help='tls: publicly trusted leaf + issuer chain (PEM)')
    r.add_argument('--key', help='tls: private key path (0600); never read by render')
    r.add_argument('--public-address', default='0.0.0.0')
    r.add_argument('--public-port', type=int, default=443)
    r.add_argument('--auth', choices=('token-map', 'service'), default='token-map',
                   help='service: service-auth-v1 via the loopback auth service (tls front only)')
    r.add_argument('--auth-port', type=int, default=8090)
    r.add_argument('--acme-webroot', help='service: serve ACME HTTP-01 challenges from this webroot')
    r.add_argument('--acme-port', type=int, default=80)
    c = sub.add_parser('check-tls')
    c.add_argument('--fullchain', required=True)
    c.add_argument('--key', required=True)
    c.add_argument('--hostname', default=DEFAULTS['hostname'])
    t = sub.add_parser('token-map')
    t.add_argument('--token-file', required=True)
    t.add_argument('--out', required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == 'render':
            manifest = render(a.out_dir, a.hostname, a.runtime_dir, a.nginx_port, a.upstream_port, a.timeout_seconds,
                              a.front, a.fullchain, a.key, a.public_address, a.public_port,
                              a.auth, a.auth_port, a.acme_webroot, a.acme_port)
            print(json.dumps({'rendered': str(a.out_dir), 'files': manifest['files']}, sort_keys=True))
        elif a.cmd == 'check-tls':
            print(json.dumps(check_tls(a.fullchain, a.key, a.hostname), sort_keys=True))
        else:
            token_map(a.token_file, a.out)
            print(json.dumps({'token_map': a.out, 'mode': '0600'}))
    except (Invalid, OSError) as e:
        print(f'refused: {e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
