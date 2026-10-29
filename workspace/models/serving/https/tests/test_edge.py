"""HTTPS edge: real Nginx against a loopback fixture backend with obviously fake credentials.

Run with a real nginx binary (env ROSE_NGINX, else `nginx` on PATH). Every request stays on
127.0.0.1; nothing contacts ACME, DNS, GCP or a live endpoint. Caddy is not exercised here.
"""
import hashlib
import http.client
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('render_proxy', HERE/'render_proxy.py')
render_proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_proxy)

NGINX = os.environ.get('ROSE_NGINX') or shutil.which('nginx')
TOKEN = 'synthetic-fixture-token-not-a-secret-000000'  # 43 chars; never a real credential
PROMPT_MARKER = 'synthetic-prompt-marker-7f3a'
AUTH = {'Authorization': f'Bearer {TOKEN}'}


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Backend:
    """Loopback stand-in for vLLM: records every request it receives."""

    def __init__(self, delay=0.0):
        self.seen = []
        self.delay = delay
        backend = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, payload):
                data = json.dumps(payload).encode()
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # nginx gave up first (timeout test)

            def do_GET(self):
                backend.seen.append({'method': 'GET', 'path': self.path, 'headers': dict(self.headers)})
                self._reply({'object': 'list', 'data': [{'id': 'muse-glimmer'}]})

            def do_POST(self):
                length = int(self.headers.get('Content-Length', 0))
                self.rfile.read(length)
                backend.seen.append({'method': 'POST', 'path': self.path, 'headers': dict(self.headers)})
                time.sleep(backend.delay)
                self._reply({'model': 'muse-glimmer', 'choices': [{'message': {'content': 'ok'}}]})

        self.port = free_port()
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', self.port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Edge:
    """Renders the config with render_proxy.py and runs real nginx in the foreground."""

    def __init__(self, upstream_port, timeout_s=120, mutate=None):
        self.dir = Path(tempfile.mkdtemp(prefix='rose-edge-'))
        self.run = self.dir/'run'
        for sub in ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'):
            (self.run/sub).mkdir(parents=True)
        os.chmod(self.run, 0o700)
        self.port = free_port()
        render_proxy.render(self.dir/'out', 'models.a-list.com', str(self.run), self.port, upstream_port, timeout_s)
        token_file = self.dir/'token'
        token_file.write_text(TOKEN)
        os.chmod(token_file, 0o600)
        render_proxy.token_map(str(token_file), str(self.run/'token.map'))
        conf = self.dir/'out'/'nginx.conf'
        if mutate:
            text = conf.read_text()
            changed = mutate(text)
            assert changed != text, 'negative control did not change the config'
            conf.write_text(changed)
        self.log = open(self.dir/'stderr.log', 'wb')
        self.proc = subprocess.Popen([NGINX, '-p', str(self.run), '-c', str(conf), '-g', 'daemon off;'],
                                     stdout=self.log, stderr=self.log)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                socket.create_connection(('127.0.0.1', self.port), timeout=0.2).close()
                return
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError((self.dir/'stderr.log').read_text())
                time.sleep(0.05)
        raise RuntimeError('nginx did not start')

    def request(self, method, path, headers=None, body=None, raw_headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=30)
        if raw_headers is not None:
            conn.putrequest(method, path, skip_accept_encoding=True)
            for k, v in raw_headers:
                conn.putheader(k, v)
            conn.endheaders(body)
        else:
            conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data, dict(resp.getheaders())

    def stop(self):
        self.proc.terminate()
        self.proc.wait(10)
        self.log.close()

    def artifacts(self):
        """Every byte nginx could have written: logs and the task-private runtime tree."""
        out = (self.dir/'stderr.log').read_bytes()
        for path in self.run.rglob('*'):
            if path.is_file() and path.name != 'token.map':
                out += path.read_bytes()
        return out


def chat(prompt='hello'):
    return json.dumps({'model': 'muse-glimmer', 'messages': [{'role': 'user', 'content': prompt}],
                       'max_tokens': 512, 'stream': False}).encode()


@unittest.skipUnless(NGINX, 'set ROSE_NGINX or put nginx on PATH')
class EdgeTest(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.addCleanup(self.backend.close)

    def edge(self, **kw):
        edge = Edge(self.backend.port, **kw)
        self.addCleanup(lambda: shutil.rmtree(edge.dir, ignore_errors=True))
        self.addCleanup(edge.stop)
        return edge

    def test_allowed_routes_reach_backend_without_authorization(self):
        e = self.edge()
        status, data, _ = e.request('POST', '/v1/chat/completions', {**AUTH, 'Content-Type': 'application/json'}, chat())
        self.assertEqual(status, 200, data)
        status, data, _ = e.request('GET', '/v1/models', AUTH)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['data'][0]['id'], 'muse-glimmer')
        self.assertEqual([r['path'] for r in self.backend.seen], ['/v1/chat/completions', '/v1/models'])
        for r in self.backend.seen:
            self.assertNotIn('authorization', {k.lower() for k in r['headers']})
            self.assertNotIn(TOKEN, json.dumps(r['headers']))

    def test_auth_is_exact_and_header_only(self):
        e = self.edge()
        cases = [
            ({}, 401), ({'Authorization': 'Bearer wrong'}, 401), ({'Authorization': f'bearer {TOKEN}'}, 401),
            # Trailing optional whitespace (RFC 9110 OWS) is stripped by nginx: same credential.
            ({'Authorization': f'Bearer {TOKEN} '}, 200), ({'Authorization': f'Bearer  {TOKEN}'}, 401),
            ({'Authorization': f'Basic {TOKEN}'}, 401), ({'Authorization': TOKEN}, 401),
            ({'X-Authorization': f'Bearer {TOKEN}'}, 401),
            ({'Authorization': f'BEARER {TOKEN}'}, 401), ({'Authorization': f'Bearer {TOKEN.upper()}'}, 401),
            ({'Authorization': f'Bearer {TOKEN.swapcase()}'}, 401), ({'Authorization': f'Bearer {TOKEN}x'}, 401),
            ({'Authorization': f'Bearer {TOKEN[:-1]}'}, 401), ({'Authorization': f'xBearer {TOKEN}'}, 401),
        ]
        for headers, expected in cases:
            self.assertEqual(e.request('GET', '/v1/models', headers)[0], expected, headers)
        # A token in the query string is never accepted, with or without a header.
        self.assertEqual(e.request('GET', f'/v1/models?access_token={TOKEN}')[0], 400)
        self.assertEqual(e.request('GET', f'/v1/models?token={TOKEN}', AUTH)[0], 400)
        # Two Authorization headers: nginx refuses the request outright.
        status = e.request('GET', '/v1/models', raw_headers=[('Host', 'x'), ('Authorization', f'Bearer {TOKEN}'),
                                                           ('Authorization', f'Bearer {TOKEN}')])[0]
        self.assertIn(status, (400, 401))
        self.assertEqual(len(self.backend.seen), 1)  # only the trailing-OWS form of the real token

    def test_only_two_exact_routes_and_methods(self):
        e = self.edge()
        for path in ['/', '/v1', '/v1/', '/v1/models/', '/v1/completions', '/v1/embeddings', '/docs', '/openapi.json',
                     '/metrics', '/health', '/admin', '/config/', '/v1/chat/completions/', '/V1/models',
                     '/v1/%63hat/completions', '/v1/chat%2Fcompletions', '//v1/models', '/v1//models',
                     '/v1/./models', '/v1/x/../models', '/v1/models%00', '/v1/models;x']:
            status = e.request('GET', path, AUTH)[0]
            self.assertIn(status, (400, 404), path)
            status = e.request('POST', path, {**AUTH, 'Content-Type': 'application/json'}, chat())[0]
            self.assertIn(status, (400, 404), path)
        self.assertEqual(e.request('GET', '/v1/chat/completions', AUTH)[0], 405)
        self.assertEqual(e.request('POST', '/v1/models', AUTH, b'{}')[0], 405)
        for method in ('PUT', 'DELETE', 'PATCH', 'OPTIONS'):
            self.assertEqual(e.request(method, '/v1/chat/completions', AUTH)[0], 405, method)
        self.assertEqual(self.backend.seen, [])

    def test_body_cap(self):
        e = self.edge()
        h = {**AUTH, 'Content-Type': 'application/json'}
        self.assertEqual(e.request('POST', '/v1/chat/completions', h, b' ' * (256 * 1024))[0], 200)
        self.assertEqual(e.request('POST', '/v1/chat/completions', h, b' ' * (256 * 1024 + 1))[0], 413)
        self.assertEqual(len(self.backend.seen), 1)

    def test_global_rate_six_per_minute_burst_two(self):
        e = self.edge()
        statuses = [e.request('GET', '/v1/models', AUTH)[0] for _ in range(5)]
        self.assertEqual(statuses[:3], [200, 200, 200])
        self.assertEqual(statuses[3:], [429, 429])
        self.assertEqual(len(self.backend.seen), 3)

    def test_one_request_in_flight(self):
        self.backend.delay = 1.5
        e = self.edge()
        results = []

        def call():
            results.append(e.request('POST', '/v1/chat/completions', {**AUTH, 'Content-Type': 'application/json'}, chat())[0])
        threads = [threading.Thread(target=call) for _ in range(2)]
        threads[0].start()
        time.sleep(0.3)
        threads[1].start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [200, 429])
        self.assertEqual(len(self.backend.seen), 1)

    def test_upstream_timeout(self):
        self.backend.delay = 4
        e = self.edge(timeout_s=2)
        started = time.time()
        status = e.request('POST', '/v1/chat/completions', {**AUTH, 'Content-Type': 'application/json'}, chat())[0]
        self.assertEqual(status, 504)
        self.assertLess(time.time() - started, 3.8)

    def test_no_redirects_and_nothing_leaks_to_logs_or_disk(self):
        e = self.edge()
        h = {**AUTH, 'Content-Type': 'application/json'}
        for _ in range(3):
            status, data, headers = e.request('POST', '/v1/chat/completions', h, chat(PROMPT_MARKER))
            self.assertNotIn('Location', headers)
        e.request('GET', f'/v1/models?access_token={TOKEN}')
        e.request('GET', '/v1/models', {'Authorization': f'Bearer {TOKEN}x'})
        e.request('POST', '/v1/chat/completions', h, chat(PROMPT_MARKER) + b' ' * (300 * 1024))
        e.stop()
        leaked = e.artifacts()
        self.assertNotIn(TOKEN.encode(), leaked)
        self.assertNotIn(PROMPT_MARKER.encode(), leaked)
        self.assertEqual([p for p in (e.run/'client_body').rglob('*')], [])
        self.assertEqual([p for p in (e.run/'proxy').rglob('*')], [])

    def test_listens_on_loopback_only(self):
        e = self.edge()
        listening = [l for l in Path('/proc/net/tcp').read_text().splitlines()[1:]
                     if l.split()[1].endswith(f':{e.port:04X}') and l.split()[3] == '0A']
        self.assertTrue(listening)
        for line in listening:
            self.assertEqual(line.split()[1].split(':')[0], '0100007F', line)  # 127.0.0.1


@unittest.skipUnless(NGINX, 'set ROSE_NGINX or put nginx on PATH')
class NegativeControlTest(unittest.TestCase):
    """Each guard is essential: removing it from the rendered config breaks exactly its check."""

    def setUp(self):
        self.backend = Backend()
        self.addCleanup(self.backend.close)

    def edge(self, mutate):
        edge = Edge(self.backend.port, mutate=mutate)
        self.addCleanup(lambda: shutil.rmtree(edge.dir, ignore_errors=True))
        self.addCleanup(edge.stop)
        return edge

    def test_without_auth_check_anyone_gets_in(self):
        e = self.edge(lambda c: c.replace('if ($auth_ok != 1) { return 401; }', ''))
        self.assertEqual(e.request('GET', '/v1/models')[0], 200)

    def test_without_raw_path_check_encoded_paths_reach_backend(self):
        e = self.edge(lambda c: c.replace('if ($request_uri != "/v1/models") { return 404; }', ''))
        self.assertEqual(e.request('GET', '/v1/%6dodels', AUTH)[0], 200)

    def test_without_body_cap_large_bodies_pass(self):
        e = self.edge(lambda c: c.replace('client_max_body_size 256k;', 'client_max_body_size 1m;'))
        self.assertEqual(e.request('POST', '/v1/chat/completions', {**AUTH, 'Content-Type': 'application/json'},
                                   b' ' * (300 * 1024))[0], 200)

    def test_without_rate_limit_fourth_request_passes(self):
        e = self.edge(lambda c: c.replace('limit_req zone=global burst=2 nodelay;', ''))
        self.assertEqual([e.request('GET', '/v1/models', AUTH)[0] for _ in range(5)], [200] * 5)

    def test_without_header_strip_backend_sees_the_token(self):
        e = self.edge(lambda c: c.replace('proxy_set_header Authorization "";', ''))
        e.request('GET', '/v1/models', AUTH)
        self.assertIn(TOKEN, json.dumps(self.backend.seen[0]['headers']))


class RendererTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='rose-render-'))
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))

    def render(self, **kw):
        args = dict(out_dir=self.dir/'out', hostname='models.a-list.com', runtime_dir='/run/rose-edge',
                    nginx_port=8080, upstream_port=8000, timeout_s=120)
        args.update(kw)
        return render_proxy.render(**args)

    def test_deployment_defaults_and_deterministic_output(self):
        first = self.render()
        conf = (self.dir/'out'/'nginx.conf').read_text()
        for line in ('listen 127.0.0.1:8080;', 'proxy_pass http://127.0.0.1:8000;', 'client_max_body_size 256k;',
                     'rate=6r/m', 'burst=2 nodelay', 'limit_conn inflight 1;', 'proxy_read_timeout 120s;',
                     'access_log off;', 'proxy_buffering off;', 'include /run/rose-edge/token.map;'):
            self.assertIn(line, conf)
        self.assertNotIn('Bearer', conf)
        self.assertEqual(self.render(), first)
        caddy = json.loads((self.dir/'out'/'caddy.json').read_text())
        self.assertEqual(caddy['admin'], {'disabled': True})
        server = caddy['apps']['http']['servers']['models']
        self.assertEqual(server['listen'], [':443'])
        self.assertEqual(server['automatic_https'], {'disable_redirects': True})
        self.assertEqual(server['routes'][0]['match'], [{'host': ['models.a-list.com']}])
        self.assertEqual(server['routes'][0]['handle'][0]['upstreams'], [{'dial': '127.0.0.1:8080'}])
        policy = caddy['apps']['tls']['automation']['policies'][0]
        self.assertEqual(policy['subjects'], ['models.a-list.com'])
        self.assertEqual(len(policy['issuers']), 1)
        self.assertEqual(policy['issuers'][0]['challenges'], {'http': {'disabled': True}, 'tls-alpn': {'disabled': False}})
        self.assertNotIn('logs', server)  # no access log
        for name, digest in first['files'].items():
            self.assertEqual(hashlib.sha256((self.dir/'out'/name).read_bytes()).hexdigest(), digest)

    def test_closed_inputs(self):
        bad = [
            {'hostname': 'Models.a-list.com'}, {'hostname': '*.a-list.com'}, {'hostname': 'a-list'},
            {'hostname': 'x.com; include /etc/passwd'}, {'hostname': 'models.a-list.com.'},
            {'runtime_dir': 'relative/dir'}, {'runtime_dir': '/run/a b'}, {'runtime_dir': '/run/$x'},
            {'runtime_dir': '/run/../etc'}, {'runtime_dir': '/run/x;'}, {'runtime_dir': '/run/x/'},
            {'nginx_port': 80}, {'upstream_port': 70000}, {'nginx_port': 8000}, {'timeout_s': 0}, {'timeout_s': 121},
        ]
        for kw in bad:
            with self.assertRaises(render_proxy.Invalid, msg=kw):
                self.render(**kw)

    def test_token_map_guards_and_never_echoes(self):
        token = self.dir/'tok'
        out = self.dir/'map'
        token.write_text(TOKEN)
        os.chmod(token, 0o644)
        with self.assertRaises(render_proxy.Invalid):
            render_proxy.token_map(str(token), str(out))
        for value in ('short', 'x' * 44, TOKEN + '!', 'A' * 64):
            token.write_text(value)
            os.chmod(token, 0o600)
            with self.assertRaises(render_proxy.Invalid, msg=value):
                render_proxy.token_map(str(token), str(out))
        token.write_text(TOKEN + '\n')
        run = subprocess.run([sys.executable, '-I', str(HERE/'render_proxy.py'), 'token-map', '--token-file', str(token),
                              '--out', str(out)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn(TOKEN, run.stdout + run.stderr)
        self.assertEqual(oct(out.stat().st_mode & 0o777), '0o600')
        self.assertEqual(out.read_text(), f'"~^Bearer {TOKEN}$" 1;\n')


if __name__ == '__main__':
    unittest.main()
