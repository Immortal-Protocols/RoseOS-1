#!/usr/bin/env python3
"""Render the Rose Models HTTPS edge: Caddy (public TLS :443) -> Nginx (127.0.0.1:8080) -> vLLM (127.0.0.1:8000).

Commands are offline. They write files and read the token file; nothing here contacts ACME, DNS,
GCP or any endpoint, and nothing starts a service.

  render     Write nginx.conf, caddy.json and manifest.json from closed, validated inputs.
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
import stat
import sys

SCHEMA = 'rose-models-https-edge/1'
HOST_RE = re.compile(r'^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')
# Paths are interpolated into config files, so they must be plain absolute paths.
PATH_RE = re.compile(r'^/[A-Za-z0-9._/-]+$')
TOKEN_RE = re.compile(r'^([A-Za-z0-9_-]{43}|[0-9a-f]{64})$')  # 256 bits, base64url or hex
ROUTES = {'/v1/chat/completions': 'POST', '/v1/models': 'GET'}
BODY_LIMIT = '256k'
RATE = '6r/m'
BURST = 2
DEFAULTS = {'hostname': 'models.a-list.com', 'nginx_port': 8080, 'upstream_port': 8000, 'timeout_s': 120}
LE_DIRECTORY = 'https://acme-v02.api.letsencrypt.org/directory'


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


def nginx_conf(runtime_dir, nginx_port, upstream_port, timeout_s):
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

    # The only line naming the credential lives in this 0600 file, written by `token-map`.
    map $http_authorization $auth_ok {{
        default 0;
        include {runtime_dir}/token.map;
    }}

    server {{
        listen 127.0.0.1:{nginx_port};
        server_name _;
        if ($args != "") {{ return 400; }}
{body}
        location / {{ return 404; }}
    }}
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


def render(out_dir, hostname, runtime_dir, nginx_port, upstream_port, timeout_s):
    check_inputs(hostname, runtime_dir, nginx_port, upstream_port, timeout_s)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        'nginx.conf': nginx_conf(runtime_dir, nginx_port, upstream_port, timeout_s).encode(),
        'caddy.json': (json.dumps(caddy_json(hostname, nginx_port), indent=2, sort_keys=True) + '\n').encode(),
    }
    for name, data in files.items():
        (out_dir/name).write_bytes(data)
    manifest = {
        'schema': SCHEMA,
        'inputs': {'hostname': hostname, 'runtime_dir': runtime_dir, 'nginx_listen': f'127.0.0.1:{nginx_port}',
                   'upstream': f'127.0.0.1:{upstream_port}', 'timeout_s': timeout_s, 'public_listen': ':443'},
        'policy': {'routes': ROUTES, 'body_limit': BODY_LIMIT, 'rate': RATE, 'burst': BURST, 'in_flight': 1,
                   'acme': {'directory': LE_DIRECTORY, 'challenge': 'tls-alpn-01', 'http_challenge': 'disabled'}},
        'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        'token_map': f'{runtime_dir}/token.map',
    }
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
    t = sub.add_parser('token-map')
    t.add_argument('--token-file', required=True)
    t.add_argument('--out', required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == 'render':
            manifest = render(a.out_dir, a.hostname, a.runtime_dir, a.nginx_port, a.upstream_port, a.timeout_seconds)
            print(json.dumps({'rendered': str(a.out_dir), 'files': manifest['files']}, sort_keys=True))
        else:
            token_map(a.token_file, a.out)
            print(json.dumps({'token_map': a.out, 'mode': '0600'}))
    except (Invalid, OSError) as e:
        print(f'refused: {e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
