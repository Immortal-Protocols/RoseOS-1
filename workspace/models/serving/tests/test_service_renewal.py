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


def systemd_255(at):
    """A timestamp exactly as systemctl 255 prints NextElapseUSecRealtime under TZ=UTC (the unix style is ignored)."""
    return time.strftime('%a %Y-%m-%d %H:%M:%S UTC', time.gmtime(at))


def systemctl(timer_props, service_props=None, rc=0):
    """systemctl show, answering per unit with systemd 255's output shape."""
    service_props = service_props or {'LoadState': 'loaded', 'ActiveState': 'inactive', 'SubState': 'dead',
                                      'Result': 'success', 'ExecMainCode': '1', 'ExecMainStatus': '0', 'NRestarts': '0',
                                      'ActiveEnterTimestamp': '', 'ExecMainExitTimestamp': '@1793405489'}
    def run(argv, **kw):
        assert argv[:2] == ['systemctl', 'show'] and kw['env']['TZ'] == 'UTC', argv
        props = timer_props if argv[2].endswith('.timer') else service_props
        wanted = [argv[i + 1] for i, a in enumerate(argv) if a == '-p']
        return types.SimpleNamespace(stdout=''.join(f'{k}={props[k]}\n' for k in wanted if k in props), returncode=rc)
    return run


def certbot_timer(**over):
    return {'LoadState': 'loaded', 'UnitFileState': 'enabled', 'ActiveState': 'active', 'SubState': 'waiting',
            'NextElapseUSecRealtime': systemd_255(time.time() + 6 * 3600), 'LastTriggerUSec': '',
            'Triggers': 'certbot.service', **over}


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
    def test_timestamps_as_systemd_255_prints_them(self):
        self.assertEqual(R.unit_facts.timestamp('Sat 2026-10-31 13:23:03 UTC'), (1793452983, 'utc'))
        self.assertEqual(R.unit_facts.timestamp('@1793452983'), (1793452983, 'unix'))
        for empty in ('', 'n/a', 'infinity'):
            self.assertEqual(R.unit_facts.timestamp(empty), (None, 'empty'))
        self.assertEqual(R.unit_facts.timestamp('Sat 2026-10-31 09:23:03 EDT'), (None, 'unsupported'))

    def test_the_old_integer_only_parser_loses_a_scheduled_timer(self):
        printed = systemd_255(1793452983)  # what `systemctl show --timestamp=unix` printed on systemd 255.4
        old = (lambda t: int(t) if t.isdigit() else None)(printed.lstrip('@'))
        self.assertIsNone(old)
        t = R.timer(systemctl(certbot_timer(NextElapseUSecRealtime=printed)), now=1793452983 - 3600)
        self.assertEqual((t['schedule'], t['nextRun'], t['nextRunFormat']), ('scheduled', 1793452983, 'utc'))

    def test_every_schedule_category(self):
        now = time.time()
        for label, props, rc, schedule in (
                ('scheduled', certbot_timer(), 0, 'scheduled'),
                ('query failed', certbot_timer(), 1, 'query_failed'),
                ('not found', certbot_timer(LoadState='not-found', UnitFileState=''), 0, 'not_found'),
                ('disabled', certbot_timer(UnitFileState='disabled'), 0, 'disabled'),
                ('inactive', certbot_timer(ActiveState='inactive', SubState='dead', NextElapseUSecRealtime=''), 0,
                 'inactive'),
                ('other zone', certbot_timer(NextElapseUSecRealtime='Sat 2026-10-31 09:23:03 EDT'), 0,
                 'timestamp_format_unsupported'),
                ('unscheduled', certbot_timer(SubState='elapsed', NextElapseUSecRealtime=''), 0, 'unscheduled'),
                ('far future', certbot_timer(NextElapseUSecRealtime=systemd_255(now + 500 * 86400)), 0, 'out_of_range')):
            with self.subTest(label):
                t = R.timer(systemctl(props, rc=rc), now=now)
                self.assertEqual(t['schedule'], schedule)
        t = R.timer(systemctl(certbot_timer()), now=now)
        self.assertEqual((t['triggers'], t['service']['result'], t['service']['mainStatus'], t['service']['lastExit']),
                         ('certbot.service', 'success', 0, 1793405489))
        missing = R.timer(lambda argv, **kw: (_ for _ in ()).throw(FileNotFoundError('systemctl')), now=now)
        self.assertEqual(missing['schedule'], 'query_failed')

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


SYSTEMD = '/usr/lib/systemd/systemd'


def systemd_backend():
    """'system' (systemd is PID 1 and we are root or have passwordless sudo: the VM's own scope), 'user' (root without
    a running systemd: a private user manager), or None."""
    if not shutil.which('systemctl'):
        return None
    state = subprocess.run(['systemctl', 'is-system-running'], capture_output=True, text=True).stdout.strip()
    root = os.geteuid() == 0
    if state not in ('', 'offline', 'unknown') and (root or subprocess.run(['sudo', '-n', 'true'],
                                                                             capture_output=True).returncode == 0):
        return 'system'
    if root and os.path.exists(SYSTEMD):
        return 'user'
    return None


BACKEND = systemd_backend()
if os.environ.get('ROSE_REQUIRE_SYSTEMD') and not BACKEND:
    raise SystemExit('ROSE_REQUIRE_SYSTEMD is set but no systemd manager can be used')


@unittest.skipUnless(BACKEND, 'needs systemd: PID 1 with root or sudo, or root for a private user manager')
class RealSystemdTimer(unittest.TestCase):
    """The exact `systemctl show` query against a real systemd manager and real timers (no CA, no certbot)."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix='rose-systemd-'))
        os.chmod(cls.dir, 0o755)
        cls.tag = f'rose-test-{os.getpid()}'
        cls.created, cls.manager = [], None
        if BACKEND == 'system':
            cls.scope, cls.units = (), Path('/etc/systemd/system')
            cls.sudo = [] if os.geteuid() == 0 else ['sudo', '-n']
        else:
            cls.scope, cls.sudo = ('--user',), []
            cls.saved = {k: os.environ.get(k) for k in ('XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME')}
            os.environ.update(XDG_RUNTIME_DIR=str(cls.dir / 'run'), XDG_CONFIG_HOME=str(cls.dir / 'config'))
            (cls.dir / 'run').mkdir(mode=0o700)
            cls.units = cls.dir / 'config/systemd/user'
            cls.units.mkdir(parents=True)
            cls.manager = subprocess.Popen([SYSTEMD, '--user'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           start_new_session=True)
            deadline = time.time() + 20
            while not (cls.dir / 'run/systemd/private').exists():
                if time.time() > deadline or cls.manager.poll() is not None:
                    cls.tearDownClass()
                    raise RuntimeError('the private systemd user manager did not start')
                time.sleep(0.1)
        version = subprocess.run(['systemctl', '--version'], capture_output=True, text=True).stdout.splitlines()[0]
        print(f'{BACKEND} manager, {version}', file=sys.stderr)

    @classmethod
    def tearDownClass(cls):
        for name in reversed(cls.created):
            subprocess.run([*cls.sudo, 'systemctl', *cls.scope, 'disable', '--now', name], capture_output=True)
            subprocess.run([*cls.sudo, 'rm', '-f', str(cls.units / name)], capture_output=True)
        if cls.created:
            subprocess.run([*cls.sudo, 'systemctl', *cls.scope, 'daemon-reload'], capture_output=True)
        if cls.manager:
            cls.manager.terminate()
            try:
                cls.manager.wait(10)
            except subprocess.TimeoutExpired:
                cls.manager.kill()
            for k, v in cls.saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def ctl(self, *args):
        r = subprocess.run([*self.sudo, 'systemctl', *self.scope, *args], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def unit(self, suffix, text):
        name = f'{self.tag}-{suffix}'
        src = self.dir / name
        src.write_text(text)
        r = subprocess.run([*self.sudo, 'install', '-m', '644', str(src), str(self.units / name)], capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.created.append(name)
        self.ctl('daemon-reload')
        return name

    def timer(self, name):
        return R.timer(now=time.time(), unit=name, scope=self.scope)

    def test_certbot_shaped_timer_through_every_state(self):
        service = self.unit('cert.service', '[Service]\nType=oneshot\nExecStart=/bin/true\n')
        name = self.unit('cert.timer', f'[Timer]\nOnCalendar=*-*-* 00,12:00:00\nRandomizedDelaySec=43200\n'
                                       f'Persistent=true\nUnit={service}\n[Install]\nWantedBy=timers.target\n')
        t = self.timer(name)
        self.assertEqual((t['query'], t['schedule'], t['unitFileState'], t['nextRun']), ('ok', 'disabled', 'disabled', None))
        self.ctl('start', name)  # active but still not enabled
        self.assertEqual(self.timer(name)['schedule'], 'disabled')
        self.ctl('enable', name)
        t = self.timer(name)
        self.assertEqual((t['schedule'], t['enabled'], t['active'], t['nextRunFormat']), ('scheduled', True, True, 'utc'))
        self.assertTrue(time.time() - 60 < t['nextRun'] < time.time() + 2 * 86400, t)
        self.assertEqual((t['triggers'], t['service']['query'], t['service']['load']), (service, 'ok', 'loaded'))
        raw = subprocess.run(['systemctl', *self.scope, 'show', '--timestamp=unix', name, '-p', 'NextElapseUSecRealtime'],
                             capture_output=True, text=True, env={**os.environ, 'TZ': 'UTC'})
        printed = raw.stdout.strip().split('=', 1)[1]
        print(f'systemctl show --timestamp=unix printed NextElapseUSecRealtime={printed}', file=sys.stderr)
        self.assertEqual(R.unit_facts.timestamp(printed)[0], t['nextRun'])  # whatever this systemd prints, it parses
        self.ctl('stop', name)
        self.assertEqual(self.timer(name)['schedule'], 'inactive')

    def test_unscheduled_and_missing_timers(self):
        service = self.unit('past.service', '[Service]\nType=oneshot\nExecStart=/bin/true\n')
        name = self.unit('past.timer', f'[Timer]\nOnCalendar=2020-01-01 00:00:00\nUnit={service}\n'
                                       f'[Install]\nWantedBy=timers.target\n')
        self.ctl('enable', '--now', name)
        t = self.timer(name)
        self.assertEqual((t['schedule'], t['nextRun'], t['active']), ('unscheduled', None, True))
        self.assertEqual(self.timer(f'{self.tag}-no-such.timer')['schedule'], 'not_found')


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
        self.timer = systemctl(certbot_timer())

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
                ('timer disabled', lambda: setattr(self, 'timer', systemctl(certbot_timer(UnitFileState='disabled')))),
                ('timer unscheduled', lambda: setattr(self, 'timer', systemctl(certbot_timer(NextElapseUSecRealtime='')))),
                ('standalone config', lambda: self.r.renewal(RENEWAL_CONF.replace('= webroot', '= standalone'))),
                ('renewed, not installed', lambda: self.r.certs(lineage='wrong'))):
            with self.subTest(label):
                setup()
                self.assertFalse(self.collect()['renewalReady'])


if __name__ == '__main__':
    unittest.main()
