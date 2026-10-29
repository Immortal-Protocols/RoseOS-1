"""Nginx TLS front: real nginx terminating TLS with a disposable synthetic CA chain.

Every certificate here is generated per run with openssl into a temporary directory and deleted
after. The client trusts only that synthetic root. Nothing is issued by a real CA, and nothing
touches ACME, DNS, cloud or a live endpoint. Passing these tests is not evidence of production
certificate issuance.
"""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
spec = importlib.util.spec_from_file_location('render_proxy', HERE/'render_proxy.py')
render_proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_proxy)
import test_edge  # noqa: E402  (shared fixture backend, token and helpers)

NGINX = test_edge.NGINX
OPENSSL = shutil.which('openssl')
HOST = 'models.a-list.com'
TOKEN = test_edge.TOKEN
AUTH = test_edge.AUTH


def openssl(*args, cwd):
    subprocess.run([OPENSSL, *args], cwd=cwd, check=True, capture_output=True)


def make_pki(d):
    """Root -> intermediate -> leaf for HOST, plus a wrong-host leaf and a self-signed leaf."""
    ec = ['-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes']
    (d/'ca.ext').write_text('basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n')
    openssl('req', '-x509', *ec, '-keyout', 'root.key', '-out', 'root.pem', '-days', '2', '-subj', '/CN=Rose synthetic test root',
            '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign', cwd=d)
    openssl('req', *ec, '-keyout', 'int.key', '-out', 'int.csr', '-subj', '/CN=Rose synthetic test intermediate', cwd=d)
    openssl('x509', '-req', '-in', 'int.csr', '-CA', 'root.pem', '-CAkey', 'root.key', '-CAcreateserial', '-days', '2',
            '-out', 'int.pem', '-extfile', 'ca.ext', cwd=d)
    for name, san in (('leaf', HOST), ('wrong', 'other.example')):
        (d/f'{name}.ext').write_text(f'subjectAltName=DNS:{san}\nextendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\n')
        openssl('req', *ec, '-keyout', f'{name}.key', '-out', f'{name}.csr', '-subj', f'/CN={san}', cwd=d)
        openssl('x509', '-req', '-in', f'{name}.csr', '-CA', 'int.pem', '-CAkey', 'int.key', '-CAcreateserial', '-days', '1',
                '-out', f'{name}.pem', '-extfile', f'{name}.ext', cwd=d)
        (d/f'{name}-fullchain.pem').write_text((d/f'{name}.pem').read_text() + (d/'int.pem').read_text())
    openssl('req', '-x509', *ec, '-keyout', 'self.key', '-out', 'self.pem', '-days', '1', '-subj', f'/CN={HOST}',
            '-addext', f'subjectAltName=DNS:{HOST}', cwd=d)
    (d/'self-fullchain.pem').write_text((d/'self.pem').read_text() + (d/'int.pem').read_text())
    for key in d.glob('*.key'):
        os.chmod(key, 0o600)


def client_context(trust_test_root=True, max_version=None, verify=True):
    ctx = ssl.create_default_context(cafile=str(PKI/'root.pem')) if trust_test_root else ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if max_version:
        ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
        ctx.maximum_version = max_version
    return ctx


class SNIConnection(http.client.HTTPSConnection):
    """Connect to 127.0.0.1 while presenting `sni` (and Host) like a real client of HOST would."""

    def __init__(self, port, sni=HOST, context=None, host_header=None):
        super().__init__(host_header or sni or HOST, port, timeout=30, context=context or client_context())
        self.sni, self.real_port = sni, port

    def connect(self):
        sock = socket.create_connection(('127.0.0.1', self.real_port), timeout=30)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.sni)


class TLSEdge(test_edge.Edge):
    """Same runtime layout as the loopback edge, rendered in TLS mode with the synthetic chain."""

    def __init__(self, upstream_port, chain='leaf', mutate=None):
        self.dir = Path(tempfile.mkdtemp(prefix='rose-tls-'))
        self.run = self.dir/'run'
        for sub in ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'):
            (self.run/sub).mkdir(parents=True)
        os.chmod(self.run, 0o700)
        self.port = test_edge.free_port()
        render_proxy.render(self.dir/'out', HOST, str(self.run), test_edge.free_port(), upstream_port, 120,
                            front='tls', fullchain=str(PKI/f'{chain}-fullchain.pem'), key=str(PKI/f'{chain}.key'),
                            public_address='127.0.0.1', public_port=self.port)
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

    def request(self, method, path, headers=None, body=None, sni=HOST, context=None, host_header=None):
        conn = SNIConnection(self.port, sni, context, host_header)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data, dict(resp.getheaders())


PKI = None


def setUpModule():
    global PKI
    if NGINX and OPENSSL:
        PKI = Path(tempfile.mkdtemp(prefix='rose-pki-'))
        make_pki(PKI)


def tearDownModule():
    if PKI:
        shutil.rmtree(PKI, ignore_errors=True)


@unittest.skipUnless(NGINX and OPENSSL, 'needs a real nginx (ROSE_NGINX) and openssl')
class TLSEdgeTest(unittest.TestCase):
    def setUp(self):
        self.backend = test_edge.Backend()
        self.addCleanup(self.backend.close)

    def edge(self, **kw):
        edge = TLSEdge(self.backend.port, **kw)
        self.addCleanup(lambda: shutil.rmtree(edge.dir, ignore_errors=True))
        self.addCleanup(edge.stop)
        return edge

    def test_config_passes_real_nginx_test(self):
        e = self.edge()
        run = subprocess.run([NGINX, '-t', '-p', str(e.run), '-c', str(e.dir/'out'/'nginx.conf')],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        conf = (e.dir/'out'/'nginx.conf').read_text()
        self.assertIn('ssl_protocols TLSv1.2 TLSv1.3;', conf)
        self.assertNotIn('listen 127.0.0.1:8080', conf)
        self.assertFalse((e.dir/'out'/'caddy.json').exists())

    def test_trusted_handshake_and_allowed_routes(self):
        e = self.edge()
        status, data, headers = e.request('GET', '/v1/models', AUTH)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['data'][0]['id'], 'muse-glimmer')
        status = e.request('POST', '/v1/chat/completions', {**AUTH, 'Content-Type': 'application/json'}, test_edge.chat())[0]
        self.assertEqual(status, 200)
        for r in self.backend.seen:
            self.assertNotIn('authorization', {k.lower() for k in r['headers']})

    def test_pr132_controls_hold_over_tls(self):
        e = self.edge()
        h = {**AUTH, 'Content-Type': 'application/json'}
        self.assertEqual(e.request('GET', '/v1/models')[0], 401)
        self.assertEqual(e.request('GET', '/v1/models', {'Authorization': f'Bearer {TOKEN.swapcase()}'})[0], 401)
        self.assertEqual(e.request('GET', f'/v1/models?access_token={TOKEN}')[0], 400)
        self.assertEqual(e.request('GET', '/v1/%6dodels', AUTH)[0], 404)
        self.assertEqual(e.request('GET', '//v1/models', AUTH)[0], 404)
        self.assertEqual(e.request('GET', '/metrics', AUTH)[0], 404)
        self.assertEqual(e.request('GET', '/v1/chat/completions', AUTH)[0], 405)
        self.assertEqual(e.request('POST', '/v1/chat/completions', h, b' ' * (256 * 1024 + 1))[0], 413)
        self.assertEqual(self.backend.seen, [])
        statuses = [e.request('GET', '/v1/models', AUTH)[0] for _ in range(4)]
        self.assertEqual(statuses, [200, 200, 200, 429])

    def test_handshake_refusals(self):
        e = self.edge()
        # Wrong or missing SNI: the server itself rejects the handshake, even for a non-verifying client.
        for sni in ('other.example', None):
            with self.assertRaises(ssl.SSLError, msg=sni):
                e.request('GET', '/v1/models', AUTH, sni=sni, context=client_context(verify=False))
        # A client that does not trust the synthetic root refuses the certificate.
        with self.assertRaises(ssl.SSLCertVerificationError):
            e.request('GET', '/v1/models', AUTH, context=client_context(trust_test_root=False))
        # TLS 1.1 and below are not offered.
        with self.assertRaises(ssl.SSLError):
            e.request('GET', '/v1/models', AUTH, context=client_context(max_version=ssl.TLSVersion.TLSv1_1))
        self.assertEqual(self.backend.seen, [])

    def test_host_must_match_sni(self):
        # nginx routes the request by Host; another name lands on the default server, which returns 421.
        e = self.edge()
        self.assertEqual(e.request('GET', '/v1/models', AUTH, host_header='other.example')[0], 421)
        self.assertEqual(self.backend.seen, [])

    def test_plain_http_to_tls_port_is_refused_without_redirect(self):
        e = self.edge()
        conn = http.client.HTTPConnection('127.0.0.1', e.port, timeout=10)
        conn.request('GET', '/v1/models', headers=AUTH)
        resp = conn.getresponse()
        resp.read()
        self.assertEqual(resp.status, 400)
        self.assertIsNone(resp.getheader('Location'))
        self.assertEqual(self.backend.seen, [])

    def test_only_the_tls_listener_is_bound(self):
        e = self.edge()
        ports = {int(l.split()[1].split(':')[1], 16) for l in Path('/proc/net/tcp').read_text().splitlines()[1:]
                 if l.split()[3] == '0A' and l.split()[1].split(':')[0] == '0100007F'}
        self.assertIn(e.port, ports)
        conf = (e.dir/'out'/'nginx.conf').read_text()
        listens = sorted({l.strip() for l in conf.splitlines() if l.strip().startswith('listen ')})
        self.assertEqual(listens, [f'listen 127.0.0.1:{e.port} ssl default_server;', f'listen 127.0.0.1:{e.port} ssl;'])

    def test_nothing_leaks_to_logs_or_disk(self):
        e = self.edge()
        h = {**AUTH, 'Content-Type': 'application/json'}
        e.request('POST', '/v1/chat/completions', h, test_edge.chat(test_edge.PROMPT_MARKER))
        e.request('GET', f'/v1/models?access_token={TOKEN}')
        with self.assertRaises(ssl.SSLError):
            e.request('GET', '/v1/models', AUTH, sni='other.example', context=client_context(verify=False))
        e.stop()
        leaked = e.artifacts()
        self.assertNotIn(TOKEN.encode(), leaked)
        self.assertNotIn(test_edge.PROMPT_MARKER.encode(), leaked)

    def test_negative_control_without_sni_guard_wrong_names_handshake(self):
        def drop_default(conf):
            start = conf.index('    # Any SNI other than')
            end = conf.index('    server {', conf.index('ssl_reject_handshake on;'))
            return conf[:start] + conf[end:]
        e = self.edge(mutate=drop_default)
        status = e.request('GET', '/v1/models', AUTH, sni='other.example', context=client_context(verify=False))[0]
        # Without the rejecting default server, the wrong name both handshakes and is served.
        self.assertEqual(status, 200)


@unittest.skipUnless(OPENSSL, 'needs openssl')
class CheckTLSTest(unittest.TestCase):
    def setUp(self):
        global PKI
        if PKI is None:
            PKI = Path(tempfile.mkdtemp(prefix='rose-pki-'))
            make_pki(PKI)

    def test_accepts_the_synthetic_chain(self):
        facts = render_proxy.check_tls(str(PKI/'leaf-fullchain.pem'), str(PKI/'leaf.key'), HOST)
        self.assertEqual(facts['san'], [HOST])
        self.assertEqual(facts['chain_certificates'], 2)
        self.assertEqual(facts['key_mode'], '0o600')
        self.assertNotIn('PRIVATE', json.dumps(facts))

    def test_refusals(self):
        cases = {
            'does not name': ('wrong-fullchain.pem', 'wrong.key', HOST),
            'self-signed': ('self-fullchain.pem', 'self.key', HOST),
            'does not match': ('leaf-fullchain.pem', 'wrong.key', HOST),
            'issuer certificate': ('leaf.pem', 'leaf.key', HOST),
        }
        for expected, (chain, key, host) in cases.items():
            with self.assertRaises(render_proxy.Invalid) as err:
                render_proxy.check_tls(str(PKI/chain), str(PKI/key), host)
            self.assertIn(expected, str(err.exception))
        open_key = PKI/'open.key'
        shutil.copy(PKI/'leaf.key', open_key)
        os.chmod(open_key, 0o644)
        with self.assertRaises(render_proxy.Invalid) as err:
            render_proxy.check_tls(str(PKI/'leaf-fullchain.pem'), str(open_key), HOST)
        self.assertIn('chmod 600', str(err.exception))
        with self.assertRaises(render_proxy.Invalid) as err:
            render_proxy.check_tls(str(PKI/'leaf-fullchain.pem'), str(PKI/'leaf.key'), HOST, now=time.time() + 3 * 86400)
        self.assertIn('expired', str(err.exception))

    def test_cli_prints_no_key_material(self):
        run = subprocess.run([sys.executable, '-I', str(HERE/'render_proxy.py'), 'check-tls', '--fullchain',
                              str(PKI/'leaf-fullchain.pem'), '--key', str(PKI/'leaf.key')], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('PRIVATE', run.stdout + run.stderr)
        self.assertNotIn((PKI/'leaf.key').read_text().splitlines()[1], run.stdout + run.stderr)


class TLSRenderTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='rose-render-tls-'))
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))

    def render(self, **kw):
        args = dict(out_dir=self.dir/'out', hostname=HOST, runtime_dir='/run/rose-edge', nginx_port=8080,
                    upstream_port=8000, timeout_s=120, front='tls', fullchain='/run/rose-edge/tls/fullchain.pem',
                    key='/run/rose-edge/tls/privkey.pem')
        args.update(kw)
        return render_proxy.render(**args)

    def test_deployment_render(self):
        manifest = self.render()
        conf = (self.dir/'out'/'nginx.conf').read_text()
        for line in ('listen 0.0.0.0:443 ssl default_server;', 'listen 0.0.0.0:443 ssl;', f'server_name {HOST};',
                     'ssl_reject_handshake on;', 'ssl_certificate /run/rose-edge/tls/fullchain.pem;',
                     'ssl_certificate_key /run/rose-edge/tls/privkey.pem;', 'ssl_protocols TLSv1.2 TLSv1.3;',
                     'client_max_body_size 256k;', 'rate=6r/m', 'burst=2 nodelay', 'limit_conn inflight 1;',
                     'proxy_read_timeout 120s;', 'proxy_set_header Authorization "";',
                     'proxy_pass http://127.0.0.1:8000;', 'access_log off;'):
            self.assertIn(line, conf)
        self.assertNotIn(':80', conf.replace(':8000', ''))
        self.assertNotIn('Bearer', conf)
        self.assertEqual(sorted(manifest['files']), ['nginx.conf'])
        self.assertEqual(manifest['inputs']['public_listen'], '0.0.0.0:443')
        self.assertEqual(manifest['policy']['tls']['terminator'], 'nginx')
        self.assertNotIn('acme', manifest['policy'])
        self.assertEqual(self.render(), manifest)

    def test_closed_tls_inputs(self):
        for kw in ({'fullchain': None}, {'key': None}, {'key': 'relative.pem'}, {'fullchain': '/x/../etc/cert'},
                   {'key': '/run/a b.pem'}, {'public_address': '::'}, {'public_address': '999.1.1.1'},
                   {'public_address': 'models.a-list.com'}, {'public_port': 80}, {'public_port': 8000},
                   {'front': 'caddy'}):
            with self.assertRaises(render_proxy.Invalid, msg=kw):
                self.render(**kw)

    def test_loopback_mode_unchanged(self):
        self.render(front='loopback')
        files = render_proxy.render(self.dir/'lo', HOST, '/run/rose-edge', 8080, 8000, 120)['files']
        self.assertEqual(files['nginx.conf'], 'ba592d8fc16022b464bcccd6ff0169ec10678dbe4d9317a7b3aa9d26d3140294')
        self.assertEqual(files['caddy.json'], 'cc6c26fb4a5418cc9303960bc0d3661056a3ebebc7e941d644bea19da7f97d54')


if __name__ == '__main__':
    unittest.main()
