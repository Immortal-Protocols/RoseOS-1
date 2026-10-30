"""service-auth-v1 through real Nginx in TLS mode (MODELS-ALWAYSON-SERVICE-001).

Real nginx and the real loopback auth service, with a disposable synthetic TLS chain, synthetic attestation roots and
a fake vLLM backend. Nothing here is a Google attestation, a public certificate or a live endpoint."""
import base64
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / 'tests'))
import test_edge  # noqa: E402
import test_tls  # noqa: E402
import test_service_auth as SA  # noqa: E402

render_proxy, A = test_tls.render_proxy, SA.A
NGINX = test_edge.NGINX


class ServiceEdge:
    def __init__(self, upstream_port, svc, mutate=None):
        self.dir = Path(tempfile.mkdtemp(prefix='rose-svc-edge-'))
        self.run = self.dir / 'run'
        for sub in ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'):
            (self.run / sub).mkdir(parents=True)
        os.chmod(self.run, 0o700)
        self.webroot = self.dir / 'acme'
        (self.webroot / '.well-known/acme-challenge').mkdir(parents=True)
        (self.webroot / '.well-known/acme-challenge/tok123').write_text('tok123.keyauth')
        for d in (self.dir, self.webroot, self.webroot / '.well-known', self.webroot / '.well-known/acme-challenge'):
            os.chmod(d, 0o755)  # nginx workers run unprivileged and only read the webroot
        self.auth = A.ThreadingHTTPServer(('127.0.0.1', 0), A.handler(svc))
        threading.Thread(target=self.auth.serve_forever, daemon=True).start()
        self.port, self.acme_port = test_edge.free_port(), test_edge.free_port()
        self.manifest = render_proxy.render(
            self.dir / 'out', test_tls.HOST, str(self.run), test_edge.free_port(), upstream_port, 120, front='tls',
            fullchain=str(test_tls.PKI / 'leaf-fullchain.pem'), key=str(test_tls.PKI / 'leaf.key'),
            public_address='127.0.0.1', public_port=self.port, auth='service', auth_port=self.auth.server_address[1],
            acme_webroot=str(self.webroot), acme_port=self.acme_port)
        self.conf = self.dir / 'out' / 'nginx.conf'
        if mutate:
            text = self.conf.read_text()
            changed = mutate(text)
            assert changed != text, 'negative control did not change the config'
            self.conf.write_text(changed)
        self.log = open(self.dir / 'stderr.log', 'wb')
        self.proc = subprocess.Popen([NGINX, '-p', str(self.run), '-c', str(self.conf), '-g', 'daemon off;'],
                                     stdout=self.log, stderr=self.log)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                socket.create_connection(('127.0.0.1', self.port), timeout=0.2).close()
                return
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError((self.dir / 'stderr.log').read_text())
                time.sleep(0.05)
        raise RuntimeError('nginx did not start')

    def request(self, method, path, headers=None, body=None):
        conn = test_tls.SNIConnection(self.port)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def post(self, path, body):
        status, data = self.request('POST', path, {'Content-Type': 'application/json'}, json.dumps(body).encode())
        return status, json.loads(data) if data.startswith(b'{') else None

    def stop(self):
        self.proc.terminate()
        self.proc.wait(10)
        self.log.close()
        self.auth.shutdown()
        self.auth.server_close()


def setUpModule():
    test_tls.setUpModule()


def tearDownModule():
    test_tls.tearDownModule()


@unittest.skipUnless(NGINX and test_tls.OPENSSL, 'needs a real nginx (ROSE_NGINX) and openssl')
class ServiceEdgeTest(SA.Fixture):
    def setUp(self):
        super().setUp()  # the service clock stays at the fixture's synthetic time
        self.policy['maxKeys'] = self.policy['enrollPerDay'] = 50
        self.backend = test_edge.Backend()
        self.addCleanup(self.backend.close)

    def edge(self, **kw):
        e = ServiceEdge(self.backend.port, self.svc, **kw)
        self.addCleanup(lambda: shutil.rmtree(e.dir, ignore_errors=True))
        self.addCleanup(e.stop)
        return e

    def token_over_edge(self, e):
        status, c = e.post('/v1/auth/challenge', {})
        self.assertEqual(status, 200)
        c = c['challenge']
        status, t = e.post('/v1/auth/token', {'challenge': c, 'attestation': self.chain(c), 'signature': self.signature(c),
                                              'invite': self.invite()})
        self.assertEqual(status, 200, t)
        return t

    def test_config_passes_real_nginx_test(self):
        e = self.edge()
        run = subprocess.run([NGINX, '-t', '-p', str(e.run), '-c', str(e.conf)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('token.map', e.conf.read_text())
        self.assertEqual(e.manifest['inputs']['auth'], 'service-auth-v1')

    def test_enroll_then_inference_and_refusals(self):
        e = self.edge()
        t = self.token_over_edge(e)
        bearer = {'Authorization': 'Bearer ' + t['access_token']}
        self.assertEqual(e.request('GET', '/v1/models', bearer)[0], 200)
        self.assertEqual(e.request('POST', '/v1/chat/completions', {**bearer, 'Content-Type': 'application/json'},
                                   test_edge.chat())[0], 200)
        self.assertTrue(all('Authorization' not in r['headers'] for r in self.backend.seen))
        self.assertEqual(len(self.backend.seen), 2)
        for headers in ({}, {'Authorization': 'Bearer ' + t['access_token'][:-2] + 'xx'}, {'Authorization': 'Bearer ' + test_edge.TOKEN}):
            self.assertEqual(e.request('GET', '/v1/models', headers)[0], 401)
        self.assertEqual(e.request('GET', '/v1/models?x=1', bearer)[0], 400)
        self.assertEqual(e.request('GET', '/v1//models', bearer)[0], 404)
        self.assertEqual(e.request('POST', '/v1/models', bearer)[0], 405)
        self.assertEqual(e.request('GET', '/_validate', bearer)[0], 404)  # internal only
        self.assertEqual(e.request('GET', '/v1/auth/challenge')[0], 405)
        self.store.update_registry(lambda reg: reg['keys'][t['key_id']].update(revoked=True))
        self.assertEqual(e.request('GET', '/v1/models', bearer)[0], 403)
        self.assertEqual(len(self.backend.seen), 2)  # nothing unauthenticated reached the model

    def test_unauthenticated_requests_do_not_consume_the_inference_budget(self):
        e = self.edge()
        t = self.token_over_edge(e)
        for _ in range(10):
            self.assertEqual(e.request('GET', '/v1/models')[0], 401)
        bearer = {'Authorization': 'Bearer ' + t['access_token']}
        statuses = [e.request('GET', '/v1/models', bearer)[0] for _ in range(4)]
        self.assertEqual(statuses, [200, 200, 200, 429])  # rate 6/min burst 2, counted after authentication

    def test_credential_routes_are_rate_limited_per_ip(self):
        e = self.edge()
        statuses = [e.post('/v1/auth/challenge', {})[0] for _ in range(9)]
        self.assertEqual(statuses.count(200), 6)  # 20/min with burst 5
        self.assertEqual(statuses[-1], 429)

    def test_acme_listener_serves_only_challenges(self):
        e = self.edge()
        conn = test_edge.http.client.HTTPConnection('127.0.0.1', e.acme_port, timeout=10)
        for path, want in (('/.well-known/acme-challenge/tok123', (200, b'tok123.keyauth')),
                           ('/.well-known/acme-challenge/missing', (404, None)), ('/v1/models', (404, None)),
                           ('/', (404, None))):
            conn.request('GET', path)
            r = conn.getresponse()
            body = r.read()
            self.assertEqual(r.status, want[0], path)
            if want[1]:
                self.assertEqual(body, want[1])
        conn.close()

    def test_negative_control_without_auth_request_lets_anyone_through(self):
        e = self.edge(mutate=lambda conf: conf.replace('            auth_request /_validate;\n', ''))
        self.assertEqual(e.request('GET', '/v1/models')[0], 200)


@unittest.skipUnless(NGINX, 'needs a real nginx (ROSE_NGINX)')
class AcmeOnlyTest(unittest.TestCase):
    """TLS-PREPARE: the ACME-only Nginx that runs before the first certificate exists."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='rose-acme-only-'))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.run_dir, self.webroot = self.dir / 'run', self.dir / 'acme'
        for sub in ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'):
            (self.run_dir / sub).mkdir(parents=True)
        (self.webroot / '.well-known/acme-challenge').mkdir(parents=True)
        for d in (self.dir, self.webroot, self.webroot / '.well-known', self.webroot / '.well-known/acme-challenge'):
            os.chmod(d, 0o755)
        (self.webroot / '.well-known/acme-challenge/tok-123').write_text('tok-123.keyauth')
        self.port = test_edge.free_port()
        self.conf = self.dir / 'acme-only.conf'
        self.conf.write_text(render_proxy.acme_only_conf(str(self.run_dir), '127.0.0.1', self.port, str(self.webroot)))

    def start(self):
        self.assertEqual(subprocess.run([NGINX, '-t', '-p', str(self.run_dir), '-c', str(self.conf)],
                                        capture_output=True).returncode, 0)
        proc = subprocess.Popen([NGINX, '-p', str(self.run_dir), '-c', str(self.conf), '-g', 'daemon off;'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait, 10)
        self.addCleanup(proc.terminate)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                socket.create_connection(('127.0.0.1', self.port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        self.fail('acme-only nginx did not start')

    def get(self, path, method='GET'):
        conn = test_edge.http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request(method, path, headers={'Host': 'models.a-list.com'})
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, body

    def test_serves_only_challenges(self):
        self.start()
        self.assertEqual(self.get('/.well-known/acme-challenge/tok-123'), (200, b'tok-123.keyauth'))
        for path in ('/', '/.well-known/acme-challenge/missing', '/v1/models', '/v1/chat/completions',
                     '/v1/auth/challenge', '/v1/auth/token', '/_validate'):
            with self.subTest(path):
                self.assertEqual(self.get(path)[0], 404)
        self.assertIn(self.get('/.well-known/acme-challenge/../../../etc/passwd')[0], (400, 404))  # nginx rejects
        self.assertEqual(self.get('/.well-known/acme-challenge/tok-123', 'POST')[0], 403)
        self.assertTrue((self.run_dir / 'nginx.pid').exists())

    def test_inputs_are_closed(self):
        for args in (('relative', '127.0.0.1', 80, '/w'), ('/r', 'not-an-ip', 80, '/w'), ('/r', '127.0.0.1', 22, '/w'),
                     ('/r', '127.0.0.1', 80, '/w/../x')):
            with self.subTest(args), self.assertRaises(render_proxy.Invalid):
                render_proxy.acme_only_conf(*args)


if __name__ == '__main__':
    unittest.main()
