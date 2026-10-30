"""Renewal evidence (MODEL-STATUS-RENEWAL-001): certbot.timer facts, the renewal config certbot will use, the installed
certificate against its lineage, and a CURRENT ACME proof through the running edge.

The edge is the rendered service edge config started like models-edge.service (`daemon off;`), under a disposable
root with loopback ports and a synthetic chain. Nothing contacts a CA, DNS, GCP or a live endpoint."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'service'))
sys.path.insert(0, str(HERE.parent / 'https' / 'tests'))
import render_service  # noqa: E402
import renewal_status as R  # noqa: E402
import test_edge  # noqa: E402
import test_tls  # noqa: E402

NGINX = test_tls.NGINX
ACCOUNT = '0123456789abcdef0123456789abcdef'
RENEWAL_CONF = f'''# renew_before_expiry = 30 days
version = 2.9.0
archive_dir = /etc/letsencrypt/archive/models.a-list.com
cert = /etc/letsencrypt/live/models.a-list.com/cert.pem
privkey = /etc/letsencrypt/live/models.a-list.com/privkey.pem
chain = /etc/letsencrypt/live/models.a-list.com/chain.pem
fullchain = /etc/letsencrypt/live/models.a-list.com/fullchain.pem

# Options used in the renewal process
[renewalparams]
account = {ACCOUNT}
authenticator = webroot
webroot_path = /var/lib/rose-models/acme,
server = https://acme-v02.api.letsencrypt.org/directory
key_type = ecdsa
renew_hook = /etc/rose-models/bin/tls-deploy
[[webroot_map]]
models.a-list.com = /var/lib/rose-models/acme
'''


def setUpModule():
    test_tls.setUpModule()


def tearDownModule():
    test_tls.tearDownModule()


def systemctl(output):
    return lambda argv, **kw: types.SimpleNamespace(stdout=output, returncode=0)


class Root:
    def __init__(self, test):
        self.root = Path(tempfile.mkdtemp(prefix='rose-renewal-'))
        test.addCleanup(shutil.rmtree, self.root, True)
        os.chmod(self.root, 0o755)

    def path(self, p):
        return self.root / p.lstrip('/')

    def account(self, name=ACCOUNT):
        self.path(f'{R.LE}/accounts/acme-v02.api.letsencrypt.org/directory/{name}').mkdir(parents=True)

    def renewal(self, text=RENEWAL_CONF):
        conf = self.path(f'{R.LE}/renewal/{R.HOSTNAME}.conf')
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(text)

    def certs(self, installed='leaf', lineage='leaf'):
        for d, name in ((self.path(f'{R.ROOT}/tls/current'), installed), (self.path(f'{R.LE}/live/{R.HOSTNAME}'), lineage)):
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy(test_tls.PKI / f'{name}-fullchain.pem', d / 'fullchain.pem')
            shutil.copy(test_tls.PKI / f'{name}.key', d / 'privkey.pem')
            os.chmod(d / 'privkey.pem', 0o600)


class TimerAndConfig(unittest.TestCase):
    def test_timer_facts(self):
        on = 'UnitFileState=enabled\nActiveState=active\nNextElapseUSecRealtime=@1793500000\nLastTriggerUSec=@1793450000\n'
        self.assertEqual(R.timer(systemctl(on)), {'enabled': True, 'active': True, 'nextRun': 1793500000,
                                                   'lastRun': 1793450000})
        off = 'UnitFileState=disabled\nActiveState=inactive\nNextElapseUSecRealtime=\nLastTriggerUSec=n/a\n'
        self.assertEqual(R.timer(systemctl(off)), {'enabled': False, 'active': False, 'nextRun': None, 'lastRun': None})

    def test_renewal_config_matches_the_webroot_hook_and_single_account(self):
        root = Root(self)
        self.assertEqual(R.renewal_config(root.root), {'present': False, 'accounts': 0})
        root.account()
        root.renewal()
        good = R.renewal_config(root.root)
        self.assertEqual(good, {'present': True, 'authenticatorWebroot': True, 'webroot': True, 'deployHook': True,
                                'accounts': 1, 'accountIsTheSingleAccount': True})
        self.assertNotIn(ACCOUNT, json.dumps(good))
        for label, edit, key in (
                ('standalone', ('authenticator = webroot', 'authenticator = standalone'), 'authenticatorWebroot'),
                ('other webroot', ('models.a-list.com = /var/lib/rose-models/acme', 'models.a-list.com = /srv/www'),
                 'webroot'),
                ('no deploy hook', ('renew_hook = /etc/rose-models/bin/tls-deploy\n', ''), 'deployHook'),
                ('other account', (f'account = {ACCOUNT}', 'account = ffff'), 'accountIsTheSingleAccount')):
            with self.subTest(label):
                root.renewal(RENEWAL_CONF.replace(*edit))
                self.assertFalse(R.renewal_config(root.root)[key])
        root.renewal()
        root.account('second-account')
        self.assertEqual(R.renewal_config(root.root)['accountIsTheSingleAccount'], False)


@unittest.skipUnless(test_tls.OPENSSL and NGINX, 'needs openssl (synthetic chain) and nginx (ROSE_NGINX)')
class Certificate(unittest.TestCase):
    def test_installed_chain_checked_against_its_lineage(self):
        root = Root(self)
        self.assertEqual(R.certificate(root.root)['installed'], False)
        root.certs()
        cert = R.certificate(root.root)
        self.assertEqual((cert['installed'], cert['check'], cert['matchesLineage']), (True, 'ok', True))
        self.assertGreater(cert['notAfter'], time.time())
        self.assertIn(cert['daysLeft'], (0, 1))
        root.certs(lineage='wrong')  # renewed but not installed
        self.assertFalse(R.certificate(root.root)['matchesLineage'])
        shutil.copy(test_tls.PKI / 'wrong.key', root.path(f'{R.ROOT}/tls/current/privkey.pem'))
        self.assertEqual(R.certificate(root.root)['check'], 'refused')


@unittest.skipUnless(NGINX and test_tls.OPENSSL, 'needs a real nginx (ROSE_NGINX) and openssl')
class RunningEdge(unittest.TestCase):
    """The rendered edge, started as models-edge.service starts it, proven through its own :80 server."""

    def setUp(self):
        self.r = Root(self)
        self.r.account()
        self.r.renewal()
        self.r.certs()
        etc = self.r.path(R.ROOT)
        render_service.render(etc, root=str(etc))
        self.run_dir, self.webroot = self.r.path('/run/rose-edge'), self.r.path(R.WEBROOT)
        for sub in ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'):
            (self.run_dir / sub).mkdir(parents=True)
        self.webroot.mkdir(parents=True)
        for d in (self.r.path('/var'), self.r.path('/var/lib'), self.r.path('/var/lib/rose-models'), self.webroot):
            os.chmod(d, 0o755)
        self.acme_port = test_edge.free_port()
        self.conf = etc / 'edge/nginx.conf'
        text = self.conf.read_text()
        for old, new in ((render_service.RUN_EDGE, str(self.run_dir)), (R.WEBROOT, str(self.webroot)),
                         ('0.0.0.0:443', f'127.0.0.1:{test_edge.free_port()}'),
                         ('127.0.0.1:8080', f'127.0.0.1:{test_edge.free_port()}'),
                         ('0.0.0.0:80;', f'127.0.0.1:{self.acme_port};')):
            self.assertIn(old, text)
            text = text.replace(old, new)
        self.conf.write_text(text)
        self.edge = {'nginx': NGINX, 'run': str(self.run_dir), 'conf': str(self.conf), 'port': self.acme_port}
        self.timer = systemctl('UnitFileState=enabled\nActiveState=active\nNextElapseUSecRealtime=@1793500000\n'
                               'LastTriggerUSec=@1793450000\n')

    def start(self, conf=None):
        proc = subprocess.Popen([NGINX, '-p', str(self.run_dir), '-c', str(conf or self.conf), '-g', 'daemon off;'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait, 10)
        self.addCleanup(proc.terminate)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                socket.create_connection(('127.0.0.1', self.acme_port), timeout=0.2).close()
                if (self.run_dir / 'nginx.pid').exists():
                    return proc
            except OSError:
                pass
            time.sleep(0.05)
        self.fail('edge did not start')

    def collect(self):
        return R.collect(self.r.root, run=self.timer, edge=self.edge)

    def test_running_edge_proves_current_acme_http_and_renewal_is_ready(self):
        self.start()
        e = self.collect()
        self.assertEqual(e['edgeProcess'], {'pid': True, 'exe': True, 'config': True, 'listener': True})
        self.assertEqual((e['edgeAcme']['challenge'], e['edgeAcme']['ok']), (200, True))
        self.assertEqual(e['edgeAcme']['notFound'], {p: 404 for p in R.NOT_FOUND})
        self.assertEqual(list((self.webroot / '.well-known/acme-challenge').iterdir()), [])  # probe removed
        self.assertTrue(e['renewalReady'])
        line = json.dumps(e)
        for secret in ('PRIVATE KEY', ACCOUNT, 'BEGIN CERTIFICATE'):
            self.assertNotIn(secret, line)

    def test_no_edge_or_another_config_is_not_ready(self):
        e = self.collect()
        self.assertEqual((e['edgeProcess']['pid'], e['edgeAcme']['challenge'], e['renewalReady']), (False, None, False))
        other = self.conf.with_name('copy.conf')
        other.write_text(self.conf.read_text())
        self.start(other)
        e = self.collect()
        self.assertEqual((e['edgeProcess']['config'], e['edgeAcme']['challenge'], e['renewalReady']),
                         (False, None, False))

    def test_each_missing_fact_withholds_renewal_ready(self):
        self.start()
        for label, setup in (
                ('timer disabled', lambda: setattr(self, 'timer', systemctl('UnitFileState=disabled\nActiveState=inactive\n'))),
                ('standalone config', lambda: self.r.renewal(RENEWAL_CONF.replace('= webroot', '= standalone'))),
                ('renewed, not installed', lambda: self.r.certs(lineage='wrong'))):
            with self.subTest(label):
                setup()
                self.assertFalse(self.collect()['renewalReady'])


if __name__ == '__main__':
    unittest.main()
