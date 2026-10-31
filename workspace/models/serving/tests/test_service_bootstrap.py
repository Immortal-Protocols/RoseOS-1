"""Startup-metadata bootstrap (MODELS-ALWAYSON-SERVICE-001).

A simulated VM (FakeHost) drives the real bootstrap stages: first boot, the one driver reboot, waiting for operator
inputs, partial failures and reruns. The real startup-script.sh runs against a loopback stand-in for the metadata
server. No cloud, GPU, apt, Docker or disk is touched."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'service'))
sys.path.insert(0, str(HERE.parent / 'https' / 'tests'))
import bootstrap as B  # noqa: E402
import build_bootstrap as BB  # noqa: E402

NGINX_BYTES = b'pinned nginx stand-in'
CONFIG = json.loads((HERE.parent / 'muse-glimmer-30b.json').read_text())
RUNTIME = next(o for o in CONFIG['options'] if o['id'] == 'a100x2-bf16-vllm-persistent')['runtime']


class FakeHost(B.Host):
    """A VM's observable behavior. `fail` maps a command prefix to an exit code for injected failures."""

    def __init__(self, prefix):
        super().__init__(prefix)
        self.calls, self.fail, self.guest = [], {}, {}
        self.install_versions = {}  # simulate the archive installing a different version than pinned
        self.probes, self.http_override = [], {}
        self.now, self.sleeps, self.on_sleep, self.events, self.md_reads = 0.0, 0, None, [], set()
        self.certbot_fail = self.acme_config_bad = False
        self.model_active = self.model_container = self.model_stuck = False
        self.validate_output, self.validate_rc = b'', 1
        self.validate_container = self.leave_container = self.rm_fails = self.docker_down = self.no_runc = False
        self.ps_results = []  # queued (exit code, output) for `docker ps --all` (daemon errors, timeouts)
        self.verify_rc, self.data_mounted, self.port_open = 3, True, False
        self.gpu_listens = self.gpu_deadline = False
        self.deploy_results = []
        self.acme_cmdline = self.acme_exe = self.acme_start = None
        self.acme_listens = True
        self.disk = {'exists': True, 'sig': None, 'size': 500 * 10**9, 'wipefs': '', 'head': bytes(1 << 20)}
        self.mounted = False
        self.packages = {}  # package -> (version, dpkg Status field); served by the REAL dpkg-query below
        self.dpkg_admin = Path(prefix).parent / 'dpkg-admin'
        for sub in ('info', 'updates'):
            (self.dpkg_admin / sub).mkdir(parents=True, exist_ok=True)
        self.query_override = None
        self.candidates = {}
        self.driver_loaded_on_boot = 2  # the driver loads after this boot number
        self.boot = 1
        self.gpus, self.driver = 2, B.DRIVER_VERSION
        # Docker 29 as installed: containerd image store by default; daemon.json is read on (re)start.
        self.loaded = {'nvidia': False, 'snapshotter': True}
        self.images = {}  # store -> (id, repo digests, os, arch)
        self.wrong_platform = False
        self.inspect_override = None
        self.enabled = {'nginx.service'}
        self.md = {'network-interfaces/0/access-configs/0/external-ip': '203.0.113.7'}
        self.dns = []
        self.pulled_id = RUNTIME['image_id']
        self.path('/usr/sbin').mkdir(parents=True, exist_ok=True)
        self.path('/usr/sbin/nginx').write_bytes(NGINX_BYTES)
        self.path('/etc').mkdir(parents=True, exist_ok=True)
        self.path(B.DISK).parent.mkdir(parents=True, exist_ok=True)
        self.path(B.DISK).write_bytes(b'')

    def run(self, argv, timeout=300, env=None):
        self.calls.append(argv)
        for prefix, rc in self.fail.items():
            if tuple(argv[:len(prefix)]) == prefix:
                return rc, ''
        cmd = argv[0]
        if cmd == 'findmnt':
            if not self.mounted:
                return 1, ''
            return 0, 'models-data ext4\n' if 'LABEL,FSTYPE' in argv else '/dev/sdb ext4\n'
        if cmd == 'blkid':
            return (2, '') if self.disk['sig'] is None else (0, self.disk['sig'])
        if cmd == 'blockdev':
            return 0, f"{self.disk['size']}\n"
        if cmd == 'wipefs':
            return 0, self.disk['wipefs']
        if cmd == 'mkfs.ext4':
            self.disk['sig'] = 'TYPE=ext4\nLABEL=models-data\n'
            return 0, ''
        if cmd == 'mount':
            self.mounted = True
            return 0, ''
        if cmd == 'dpkg-query':
            if self.query_override:
                return self.query_override
            (self.dpkg_admin / 'status').write_text(''.join(
                f'Package: {p}\nStatus: {st}\nVersion: {v}\nArchitecture: amd64\nMaintainer: x\nDescription: x\n\n'
                for p, (v, st) in self.packages.items()))
            r = subprocess.run(['dpkg-query', f'--admindir={self.dpkg_admin}', *argv[1:]], capture_output=True,
                               text=True, timeout=60)
            return r.returncode, r.stdout
        if cmd == 'apt-cache':
            return 0, ''.join(f'{p}:\n  Installed: (none)\n  Candidate: {self.candidates.get(p, B.APT_PINS[p])}\n'
                              for p in argv[2:])
        if cmd == 'gpg':
            fpr = hashlib.sha1(Path(argv[-1]).read_bytes()).hexdigest().upper()  # stand-in fingerprint
            return 0, f"pub:-:4096:1:X:::\nfpr:::::::::{self.key_fpr(fpr)}:\n"
        if cmd == 'apt-get' and argv[1] == 'install':
            for pin in argv:
                if '=' in pin:
                    name, version = pin.split('=', 1)
                    self.packages[name] = (self.install_versions.get(name, version), 'install ok installed')
            return 0, ''
        if cmd == 'apt-mark' and argv[1] == 'hold':
            for name in argv[2:]:
                if name in self.packages:
                    v, st = self.packages[name]
                    self.packages[name] = (v, st.replace('install ', 'hold ', 1))
            return 0, ''
        if cmd in ('apt-get', 'apt-mark'):
            return 0, ''
        if cmd == 'nvidia-smi':
            if self.boot < self.driver_loaded_on_boot:
                return 9, ''
            return 0, ''.join(f'{self.driver}\n' for _ in range(self.gpus))
        if cmd == 'systemctl':
            return self.systemctl(argv[1:])
        if cmd == 'docker':
            store = 'containerd' if self.loaded['snapshotter'] else 'classic'
            if argv[1] == 'info' and '{{json .Runtimes}}' in argv:
                if self.docker_down:
                    return 1, ''
                runtimes = {**({'nvidia': {}} if self.loaded['nvidia'] else {}), **({} if self.no_runc else {'runc': {}})}
                return 0, json.dumps(runtimes)
            if argv[1] == 'info':
                if self.loaded['snapshotter']:
                    return 0, 'overlayfs|[["driver-type","io.containerd.snapshotter.v1"]]|29.1.3\n'
                return 0, 'overlay2|[["Backing Filesystem","extfs"],["Supports d_type","true"]]|29.1.3\n'
            if argv[1] == 'pull':
                ref = argv[2]
                # containerd store: .Id is the pulled manifest digest; classic store: .Id is the config digest
                image_id = ref.split('@')[1] if store == 'containerd' else self.pulled_id
                self.images[store] = (image_id, [ref], 'linux', 'arm64' if self.wrong_platform else 'amd64')
                return 0, ''
            if argv[1:3] == ['image', 'inspect']:
                if self.inspect_override:
                    return self.inspect_override
                if store not in self.images:
                    return 1, ''
                i, digests, os_, arch = self.images[store]
                if argv[-2] == '{{.Id}}':
                    return 0, i
                return 0, f'{i}|{json.dumps(digests)}|{os_}|{arch}\n'
            if argv[1:3] == ['ps', '--all']:
                if self.ps_results:
                    return self.ps_results.pop(0)
                names = [n for n, alive in (('muse-glimmer', self.model_container),
                                            ('muse-glimmer-validate', self.validate_container)) if alive]
                wanted = argv[argv.index('--filter') + 1].split('=', 1)[1]
                return 0, ''.join(n + '\n' for n in names if wanted in n)  # docker's name filter is a substring match
            if argv[1:3] == ['rm', '-f']:
                self.events.append(('rm', argv[3]))
                if argv[3] == 'muse-glimmer-validate' and not self.rm_fails:
                    self.validate_container = False
                if argv[3] == 'muse-glimmer' and not self.rm_fails:
                    self.model_container = self.port_open = False
                return (1, '') if self.rm_fails else (0, '')
        if cmd == 'nvidia-ctk':
            daemon = self.path(B.DAEMON_JSON)
            daemon.parent.mkdir(parents=True, exist_ok=True)
            config = json.loads(daemon.read_text()) if daemon.exists() else {}
            config.setdefault('runtimes', {})['nvidia'] = {'path': 'nvidia-container-runtime'}
            daemon.write_text(json.dumps(config))
            return 0, ''
        if cmd == 'python3' and argv[1].endswith('etc-rose-models/lib/service/verify_model.py'):
            self.events.append(('verify', argv[2:]))
            return self.verify_rc, ''
        if cmd == 'mountpoint':
            return (0 if self.data_mounted else 1), ''
        if cmd == 'python3' and argv[-1] == 'secret-init':
            p = self.path('/var/lib/rose-models/auth/secret.json')
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('{"current": "SECRET-NEVER-PRINTED"}')
            return 0, ''
        if cmd == 'certbot':
            if argv[1] == 'certonly':
                assert self.acme_running(), 'issuance needs the proven ACME-only nginx'
                self.events.append(('certonly', argv))
                if self.certbot_fail:
                    if '--account' not in argv:  # a failed first attempt can still leave a registered account
                        self.add_account('acct-from-failed-attempt')
                    return 1, ''
                if '--account' not in argv:
                    self.add_account('acct-new')
                self.add_lineage(issued_by='webroot')
                self.tls_deploy(argv[argv.index('--deploy-hook') + 1])  # certbot exits 0 even if its hook fails
            return 0, ''
        if cmd == '/usr/sbin/nginx' and '/run/rose-acme' in argv:
            if argv[-1] == '-t':
                return (1, '') if self.acme_config_bad else (0, '')
            if argv[-2:] == ['-s', 'quit']:
                self.acme_exit(4242)
                return 0, ''
        if cmd.endswith('/bin/tls-deploy'):
            return self.tls_deploy(cmd)
        raise AssertionError(f'unexpected command {argv}')

    def validate(self, argv, max_seconds, capture, probe=None, probe_every=5):
        self.events.append(('validate', list(argv), self.model_active, self.model_container,
                            self.path(B.MODEL_HOLD).exists()))
        for i in range(0, len(self.validate_output), 65536):
            capture.feed(self.validate_output[i:i + 65536])
        if '--gpus' in argv:  # the production container, named muse-glimmer, without --rm cleanup when killed
            self.model_container = True
            if probe and self.gpu_listens:
                self.port_open = True
                assert probe()
                return None, 42.0
            if self.gpu_deadline:
                return None, float(max_seconds)
            self.model_container = self.leave_container
            return self.validate_rc, 3.5
        self.validate_container = self.leave_container  # e.g. a CLI killed at the deadline
        return self.validate_rc, 3.5

    def listening(self, port):
        assert port == 8000
        return self.port_open

    def start_daemon(self, argv, timeout=30):
        """The daemonizing nginx: pid file, /proc identity (exe, process title, listening socket) like the real one."""
        self.calls.append(argv)
        assert argv == B.ACME_NGINX, argv
        if self.acme_start:
            return self.acme_start
        self.path('/run/rose-acme/nginx.pid').write_text('4242\n')
        proc = self.path('/proc/4242')
        (proc / 'fd').mkdir(parents=True, exist_ok=True)
        (proc / 'exe').symlink_to(self.acme_exe or self.path('/usr/sbin/nginx').resolve())
        (proc / 'stat').write_text('4242 (nginx) S 1 4242 4242 0 -1\n')
        (proc / 'cmdline').write_bytes(' '.join(self.acme_cmdline or ['nginx: master process', *argv]).encode() + b'\0')
        if self.acme_listens:
            os.symlink('socket:[777]', proc / 'fd/6')
            self.listen(80, 777)
        return None

    def listen(self, port, inode):
        tcp = self.path('/proc/net/tcp')
        tcp.parent.mkdir(parents=True, exist_ok=True)
        lines = tcp.read_text() if tcp.exists() else '  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n'
        tcp.write_text(lines + f'   0: 00000000:{port:04X} 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 {inode} 1\n')

    def acme_exit(self, pid):
        self.path('/run/rose-acme/nginx.pid').unlink(missing_ok=True)
        shutil.rmtree(self.path(f'/proc/{pid}'), ignore_errors=True)
        tcp = self.path('/proc/net/tcp')
        if tcp.exists():
            tcp.write_text(''.join(l for l in tcp.read_text().splitlines(True) if ' 777 ' not in l))  # its listener

    def kill(self, pid, sig):
        self.events.append(('kill', pid, sig))
        self.acme_exit(pid)

    def key_fpr(self, fpr):
        return B.NVIDIA_KEY_FPR if fpr == self.expected_key_sha1 else fpr

    def systemctl(self, args):
        if args[0] == 'is-enabled':
            return (0, 'enabled') if args[1] in self.enabled else (1, 'disabled')
        if args[0] == 'disable':
            self.enabled -= set(args[2:])
            return 0, ''
        if args[0] == 'enable':
            self.enabled |= {a for a in args[1:] if not a.startswith('--')}
            if '--now' in args and 'muse-glimmer.service' in args:
                self.model_active = self.model_container = True
            return 0, ''
        if args[0] == 'stop':
            self.events.append(('stop', tuple(args[1:])))
            if 'muse-glimmer.service' in args and not self.model_stuck:
                self.model_active = self.model_container = False
            return 0, ''
        if args[:2] == ['is-active', 'muse-glimmer.service']:
            return (0, 'active\n') if self.model_active else (3, 'inactive\n')
        if args == ['restart', 'docker']:
            daemon = self.path(B.DAEMON_JSON)
            config = json.loads(daemon.read_text()) if daemon.exists() else {}
            self.loaded = {'nvidia': 'nvidia' in config.get('runtimes', {}),
                           'snapshotter': config.get('features', {}).get('containerd-snapshotter', True) is not False}
            return 0, ''
        if args[:2] == ['--no-block', 'reboot']:
            self.reboot_requested = True
            return 0, ''
        return 0, ''

    def metadata(self, path):
        self.md_reads.add(path)
        return self.md.get(path)

    def clock(self):
        return self.now

    def publish(self, line):
        self.events.append(('publish', line))

    def ack(self, boot=None, payload='sha'):
        """What the operator sets with `gcloud compute instances add-metadata` after reading the proof."""
        self.md[B.ACK_KEY] = f'{payload}:boot-{self.boot if boot is None else boot}'

    def put_guest_attribute(self, key, value):
        self.guest[key] = value

    def acme_running(self):
        return self.path('/run/rose-acme/nginx.pid').exists()

    def http_get(self, url, host_header):
        self.probes.append(url)
        if not self.acme_running():
            return 0, b''
        assert url.startswith('http://127.0.0.1:80/'), url
        path = url.split('127.0.0.1:80', 1)[1]
        if path in self.http_override:
            return self.http_override[path]
        if path.startswith('/.well-known/acme-challenge/'):
            f = self.path('/var/lib/rose-models/acme') / path.lstrip('/')
            return (200, f.read_bytes()[:256]) if f.exists() else (404, b'')
        return 404, b''

    def sleep(self, seconds):
        self.now += seconds
        self.sleeps += 1
        if self.on_sleep:
            self.on_sleep(self)

    def add_account(self, name):
        self.path(f'/etc/letsencrypt/accounts/acme-v02.api.letsencrypt.org/directory/{name}').mkdir(parents=True, exist_ok=True)

    def add_lineage(self, name='models.a-list.com', issued_by='webroot', complete=True):
        renewal = self.path(f'/etc/letsencrypt/renewal/{name}.conf')
        renewal.parent.mkdir(parents=True, exist_ok=True)
        renewal.write_text(f'[renewalparams]\nauthenticator = {issued_by}\n')
        live = self.path(f'/etc/letsencrypt/live/{name}')
        live.mkdir(parents=True, exist_ok=True)
        if complete:
            (live / 'fullchain.pem').write_text('chain')
            (live / 'privkey.pem').write_text('KEY-NEVER-PRINTED')

    def tls_deploy(self, cmd):
        """The rendered hook: `deploy_results` queues (exit, stdout) failures, e.g. the argparse exit 2."""
        assert cmd == '/etc/rose-models/bin/tls-deploy', cmd
        self.events.append(('tls-deploy',))
        if self.deploy_results:
            return self.deploy_results.pop(0)
        self.deploy_current()
        return 0, '{"result": "deployed"}\n'

    def deploy_current(self):
        cur = self.path('/etc/rose-models/tls/current')
        cur.mkdir(parents=True, exist_ok=True)
        (cur / 'fullchain.pem').write_text('cert')

    def resolve(self, name):
        return list(self.dns)

    def read_head(self, device, n):
        return self.disk['head'][:n]

    def boot_id(self):
        return f'boot-{self.boot}'

    def reboot(self):
        self.boot += 1
        self.mounted = self.disk['sig'] is not None  # fstab mounts it on boot

    def count(self, *prefix):
        return sum(1 for c in self.calls if tuple(c[:len(prefix)]) == prefix)


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='bootstrap-test-'))
        cls.roots = cls.tmp / 'roots'
        cls.roots.mkdir()
        shutil.copy(cls.make_root(), cls.roots / 'synthetic-root.pem')
        cls.build = BB.build(cls.tmp / 'build', source='test')
        cls.build_roots = BB.build(cls.tmp / 'build-roots', attestation_roots=cls.roots, source='test')

    @classmethod
    def make_root(cls):
        import datetime
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic Root Not Google')])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
                .sign(key, hashes.SHA256()))
        path = cls.tmp / 'root.pem'
        path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return path

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.vm = Path(tempfile.mkdtemp(prefix='vm-'))
        self.addCleanup(shutil.rmtree, self.vm, True)
        self.payload = self.extract(self.tmp / 'build')
        self.host = FakeHost(self.vm / 'root')
        self.host.expected_key_sha1 = hashlib.sha1(
            (self.payload / 'apt/nvidia-container-toolkit.asc').read_bytes()).hexdigest().upper()
        B.NGINX_SHA256_SAVED = B.NGINX_SHA256
        B.NGINX_SHA256 = hashlib.sha256(NGINX_BYTES).hexdigest()
        self.addCleanup(setattr, B, 'NGINX_SHA256', B.NGINX_SHA256_SAVED)

    def extract(self, build):
        out = Path(tempfile.mkdtemp(prefix='payload-', dir=self.vm))
        import gzip
        raw = gzip.decompress(base64.b64decode((build / 'rose-models-payload.b64').read_bytes()))
        with tarfile.open(fileobj=__import__('io').BytesIO(raw)) as tar:
            tar.extractall(out, filter='data') if hasattr(tarfile, 'data_filter') else tar.extractall(out)
        return out

    def boot(self, payload=None):
        status, code = B.run(self.host, payload or self.payload, 'sha', now=lambda: 1000.0)
        return status, code

    def states(self, status):
        return {k: (v['state'], v['detail']) for k, v in status['stages'].items()}


class FirstBootToReady(Fixture):
    def test_fresh_vm_reboots_once_waits_for_operator_inputs_then_becomes_ready_and_reruns_idempotently(self):
        status, code = self.boot()
        self.assertEqual((status['result'], code), ('rebooting', 0))
        s = self.states(status)
        self.assertEqual((s['disk'][0], s['packages'], s['driver']),
                         ('ok', ('ok', 'installed'), ('reboot', 'reboot_requested_for_driver')))
        self.assertEqual(self.host.count('mkfs.ext4'), 1)
        self.assertIn(B.FSTAB_LINE, self.host.path('/etc/fstab').read_text())
        self.assertTrue(all(v[0] == 'skipped' for k, v in s.items() if k not in ('disk', 'packages', 'driver')))

        self.host.reboot()
        status, code = self.boot()
        s = self.states(status)
        self.assertEqual((status['result'], code), ('waiting', 0))
        self.assertEqual(s['auth'], ('waiting', 'attestation_roots_missing'))
        self.assertEqual(s['tls'], ('waiting', 'acme_not_authorized'))
        self.assertEqual(s['edge'], ('waiting', 'needs_tls_and_auth'))
        self.assertTrue(s['service'][1].startswith('enabled'))
        self.assertTrue(set(B.MODEL_UNITS) <= self.host.enabled)
        self.assertNotIn('nginx.service', self.host.enabled)
        self.assertTrue((self.host.path('/etc/rose-models') / 'bin/start-model').stat().st_mode & 0o111)
        self.assertTrue(self.host.path('/etc/systemd/system/muse-glimmer.service').exists())

        self.host.md['attributes/rose-models-acme'] = B.ACME_FLAG
        self.host.dns = ['198.51.100.9']
        status, _ = self.boot()
        self.assertEqual(self.states(status)['tls'], ('waiting', 'dns_not_pointing_here'))
        self.host.dns = ['203.0.113.7']
        self.host.ack()
        payload = self.extract(self.tmp / 'build-roots')
        status, code = self.boot(payload)
        self.assertEqual((status['result'], code), ('ready', 0), status)
        self.assertTrue({'models-auth.service', 'models-edge.service'} <= self.host.enabled)

        before = list(self.host.calls)
        status, code = self.boot(payload)
        self.assertEqual((status['result'], code), ('ready', 0))
        new = self.host.calls[len(before):]
        for heavy in (('mkfs.ext4',), ('apt-get', 'install'), ('docker', 'pull'), ('certbot',), ('nvidia-ctk',),
                      ('systemctl', '--no-block', 'reboot'), ('python3',)):
            self.assertFalse([c for c in new if tuple(c[:len(heavy)]) == heavy], heavy)
        self.assertEqual((self.host.count('mkfs.ext4'), self.host.count('certbot', 'certonly'),
                          self.host.count('certbot', 'reconfigure'), self.host.count('docker', 'pull')), (1, 1, 0, 1))
        self.assertFalse(self.host.acme_running())  # the edge holds :80 now

    def test_status_is_nonsecret_and_published(self):
        self.host.driver_loaded_on_boot = 1
        status, _ = self.boot()
        line = self.host.guest['bootstrap']
        self.assertEqual(json.loads(line), status)
        self.assertEqual(json.loads(self.host.path(B.STATE + '/status.json').read_text()), status)
        for text in (line, json.dumps(status)):
            self.assertNotIn('SECRET-NEVER-PRINTED', text)
            self.assertNotIn(str(self.vm), text)


class Refusals(Fixture):
    def setUp(self):
        super().setUp()
        self.host.driver_loaded_on_boot = 1

    def assertStopsAt(self, stage, category):
        status, code = self.boot()
        s = self.states(status)
        self.assertEqual((status['result'], code, s[stage]), ('failed', 1, ('failed', category)))
        later = [k for k, _ in B.STAGES][[k for k, _ in B.STAGES].index(stage) + 1:]
        self.assertTrue(all(s[k][0] == 'skipped' for k in later))
        return s

    def test_data_disk_is_formatted_only_when_provably_blank(self):
        for label, change, category in (
                ('other filesystem', {'sig': 'TYPE=xfs\n'}, 'data_disk_not_blank'),
                ('ext4 with another label', {'sig': 'TYPE=ext4\nLABEL=scratch\n'}, 'data_disk_not_blank'),
                ('signature only wipefs sees', {'wipefs': 'gpt partition table\n'}, 'data_disk_not_blank'),
                ('nonzero first MiB', {'head': b'\x00' * 100 + b'\x01'}, 'data_disk_not_blank'),
                ('too small', {'size': 100 * 10**9}, 'data_disk_too_small'),
                ('missing', {'exists': False}, 'data_disk_missing')):
            with self.subTest(label):
                self.host.disk.update({'sig': None, 'size': 500 * 10**9, 'wipefs': '', 'head': bytes(1 << 20)})
                self.host.disk.update(change)
                if not self.host.disk['exists']:
                    self.host.path(B.DISK).unlink()
                self.assertStopsAt('disk', category)
                self.assertEqual(self.host.count('mkfs.ext4'), 0)
                if not self.host.disk['exists']:
                    self.host.path(B.DISK).write_bytes(b'')
                    self.host.disk['exists'] = True

    def test_partial_failures_stop_cleanly_and_a_rerun_resumes(self):
        cases = (
            (('apt-get', 'install'), 'packages', 'package_version_unavailable_or_install_failed'),
            (('apt-get', 'update'), 'packages', 'apt_update_failed'),
            (('nvidia-ctk',), 'container', 'nvidia_ctk_failed'),
            (('docker', 'pull'), 'image', 'image_pull_failed'),
            (('systemctl', 'enable', '--now', B.MODEL_UNITS[0]), 'service', 'service_enable_failed'),
            (('/usr/sbin/nginx', '-p', '/run/rose-acme', '-c', '/etc/rose-models/edge/acme-only.conf'), 'tls',
             'acme_http_config_invalid'))
        self.host.md['attributes/rose-models-acme'] = B.ACME_FLAG
        self.host.dns = ['203.0.113.7']
        self.host.ack()
        for prefix, stage, category in cases:
            with self.subTest(stage=stage, prefix=prefix):
                self.host.fail = {prefix: 100}
                status, code = self.boot()
                self.assertEqual((code, self.states(status)[stage]), (1, ('failed', category)))
                self.host.fail = {}
        status, code = self.boot()  # every earlier failure left the host resumable
        self.assertEqual(status['result'], 'waiting')  # only the attestation roots remain
        self.assertEqual(self.host.count('mkfs.ext4'), 1)

    def test_pins_and_identities_are_enforced(self):
        self.host.expected_key_sha1 = 'tampered'
        self.assertStopsAt('packages', 'nvidia_key_fingerprint_mismatch')
        self.assertEqual(self.host.count('apt-get', 'update'), 0)  # nothing installed from an unverified repository
        self.host.expected_key_sha1 = hashlib.sha1(
            (self.payload / 'apt/nvidia-container-toolkit.asc').read_bytes()).hexdigest().upper()
        self.host.pulled_id = 'sha256:' + '0' * 64
        self.assertStopsAt('image', 'image_id_mismatch')
        self.host.pulled_id = RUNTIME['image_id']
        self.host.images = {}
        self.host.path('/usr/sbin/nginx').write_bytes(b'other build')
        self.assertStopsAt('nginx', 'nginx_binary_hash_mismatch')
        self.host.path('/usr/sbin/nginx').write_bytes(NGINX_BYTES)
        self.host.driver = '575.00.00'
        self.assertStopsAt('driver', 'driver_version_mismatch')
        self.host.driver, self.host.gpus = B.DRIVER_VERSION, 1
        self.assertStopsAt('driver', 'gpu_count_mismatch')

    def test_at_most_one_driver_reboot(self):
        self.host.driver_loaded_on_boot = 99
        status, _ = self.boot()
        self.assertEqual(self.states(status)['driver'], ('reboot', 'reboot_requested_for_driver'))
        status, _ = self.boot()  # the same boot again (reboot still pending): no second request
        self.assertEqual(self.states(status)['driver'], ('reboot', 'reboot_pending'))
        self.host.reboot()
        self.assertStopsAt('driver', 'driver_not_loaded_after_reboot')
        self.assertEqual(self.host.count('systemctl', '--no-block', 'reboot'), 1)

    def test_a_second_concurrent_run_does_not_interleave(self):
        state = self.host.path(B.STATE)
        state.mkdir(parents=True)
        import fcntl
        fd = os.open(state / '.lock', os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.addCleanup(os.close, fd)
        self.assertEqual(self.boot(), ({'result': 'already_running'}, 0))
        self.assertEqual(self.host.calls, [])


class Packages(Fixture):
    """MODELS-BOOTSTRAP-PACKAGES-FIX-001: the real dpkg-query over a fixture admindir, through install, hold and recheck."""

    def setUp(self):
        super().setUp()
        self.host.driver_loaded_on_boot = 1

    def packages_stage(self):
        status, code = self.boot()
        return self.states(status)['packages'], status['stages']['packages'].get('observed'), code

    def test_install_then_hold_then_recheck_passes_and_a_rerun_installs_nothing(self):
        (state, detail), observed, _ = self.packages_stage()
        self.assertEqual((state, detail, observed), ('ok', 'installed', None))
        self.assertTrue(all(st == 'hold ok installed' for _, st in self.host.packages.values()))  # really 'hi'
        installs = self.host.count('apt-get', 'install')
        (state, detail), _, _ = self.packages_stage()
        self.assertEqual((state, detail, self.host.count('apt-get', 'install')), ('ok', 'pinned', installs))

    def test_held_package_reports_hi_and_the_old_ii_only_check_would_have_refused_it(self):
        self.packages_stage()
        rc, out = self.host.run(['dpkg-query', '-W', '-f', B.PKG_FORMAT, *B.APT_PINS], 60)
        self.assertEqual({line.split('\t')[2] for line in out.splitlines()}, {'hi '})
        old_check = {l.split()[0]: l.split()[1] for l in out.replace('\t', ' ').splitlines() if l.split()[2].startswith('ii')}
        self.assertNotEqual(old_check, B.APT_PINS)  # the 8183dc55 defect, reproduced with the real dpkg-query

    def test_rejects_wrong_or_incomplete_package_states_with_bounded_observed_facts(self):
        pin = 'nvidia-utils-580-server'
        for label, version, dpkg_status, abbrev in (
                ('wrong version', '580.000.00-0ubuntu1', 'hold ok installed', 'hi '),
                ('half-installed, reinstall required', B.APT_PINS[pin], 'install reinstreq half-installed', 'iHR'),
                ('unpacked only', B.APT_PINS[pin], 'install ok unpacked', 'iU '),
                ('removed, config files left', B.APT_PINS[pin], 'deinstall ok config-files', 'rc ')):
            with self.subTest(label):
                self.host.packages = {p: (v, 'hold ok installed') for p, v in B.APT_PINS.items()}
                self.host.packages[pin] = (version, dpkg_status)
                self.host.install_versions = {pin: version}
                self.host.candidates = {pin: '580.999.99-0ubuntu0.24.04.1'}
                if dpkg_status != 'hold ok installed':
                    self.host.install_versions = {}
                    self.host.fail = {('apt-get', 'install'): 100}  # the archive cannot repair it this run
                (state, category), observed, code = self.packages_stage()
                self.host.fail = {}
                self.assertEqual((state, code), ('failed', 1))
                self.assertIn(category, ('package_versions_differ', 'package_version_unavailable_or_install_failed'))
                self.assertEqual(observed, [{'package': pin, 'expected': B.APT_PINS[pin], 'observed': version,
                                             'status': abbrev, 'candidate': '580.999.99-0ubuntu0.24.04.1'}])

    def test_missing_packages_and_failed_or_malformed_queries_refuse(self):
        self.host.fail = {('apt-get', 'install'): 100}
        (state, category), observed, _ = self.packages_stage()
        self.assertEqual(category, 'package_version_unavailable_or_install_failed')
        self.assertEqual(len(observed), len(B.APT_PINS))  # every pin reported missing
        self.assertTrue(all(o['observed'] is None and o['status'] is None for o in observed))
        self.host.fail = {}
        for label, override, category in (
                ('query failed', (2, ''), 'package_query_failed'),
                ('malformed line', (0, 'docker.io 29.1.3 hi\n'), 'package_query_malformed'),
                ('duplicate package', (0, 'nginx\t1.24.0-2ubuntu7.19\thi \nnginx\t1.24.0-2ubuntu7.19\thi \n'),
                 'package_query_malformed'),
                ('unexpected package', (0, 'evil\t1\thi \n'), 'package_query_malformed'),
                ('bad status abbrev', (0, 'nginx\t1.24.0-2ubuntu7.19\th\n'), 'package_query_malformed')):
            with self.subTest(label):
                self.host.query_override = override
                (state, got), _, code = self.packages_stage()
                self.assertEqual((state, got, code), ('failed', category, 1))
        self.host.query_override = None

    def test_observed_facts_are_nonsecret_and_bounded(self):
        self.host.install_versions = {'docker.io': '1:bad'}
        status, _ = self.boot()
        observed = status['stages']['packages']['observed']
        self.assertEqual([o['package'] for o in observed], ['docker.io'])
        self.assertLessEqual(len(json.dumps(status)), 8192)
        self.assertEqual(json.loads(self.host.guest['bootstrap']), status)


class Image(Fixture):
    """IMAGE-FIX-001: Docker 29's default containerd image store reports the manifest digest as .Id. The bootstrap pins the
    classic store, then requires exactly the pinned config ID, the pinned repo digest and linux/amd64."""

    def setUp(self):
        super().setUp()
        self.host.driver_loaded_on_boot = 1

    def image_stage(self):
        status, code = self.boot()
        st = status['stages']
        return self.states(status)['container'], self.states(status)['image'], st['image'].get('observed'), code

    def test_containerd_default_is_switched_to_classic_then_the_pinned_config_id_matches(self):
        container, image, observed, _ = self.image_stage()
        self.assertEqual((container[0], image, observed), ('ok', ('ok', 'pulled'), None))
        self.assertIn('overlay2 store; configured', container[1])
        daemon = json.loads(self.host.path(B.DAEMON_JSON).read_text())
        self.assertEqual((daemon['features'], 'nvidia' in daemon['runtimes']), ({'containerd-snapshotter': False}, True))
        restarts = self.host.count('systemctl', 'restart', 'docker')
        _, image, _, _ = self.image_stage()  # rerun: nothing restarted, nothing pulled again
        self.assertEqual((image, self.host.count('systemctl', 'restart', 'docker'), self.host.count('docker', 'pull')),
                         (('ok', 'present'), restarts, 1))

    def test_model_of_the_hypothesis_containerd_store_reports_the_manifest_digest_as_id(self):
        self.host.loaded = {'nvidia': True, 'snapshotter': True}
        ref = f"vllm/vllm-openai@{RUNTIME['image_digest']}"
        self.host.run(['docker', 'pull', ref])
        rc, out = self.host.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', ref])
        self.assertNotEqual(out.split('|')[0], RUNTIME['image_id'])  # .Id is the manifest digest there
        self.assertEqual(out.split('|')[0], RUNTIME['image_digest'])

    def test_store_that_stays_containerd_is_refused_with_observed_store(self):
        def restart_ignores_features(args, original=self.host.systemctl):
            return (0, '') if args == ['restart', 'docker'] else original(args)
        self.host.systemctl = restart_ignores_features
        self.host.loaded['nvidia'] = True
        container, _, _, code = self.image_stage()
        self.assertEqual((container[0], container[1], code), ('failed', 'docker_image_store_not_classic', 1))

    def test_wrong_image_platform_or_missing_digest_is_refused_with_bounded_observed_identity(self):
        for label, setup, category in (
                ('genuinely different image', lambda h: setattr(h, 'pulled_id', 'sha256:' + 'ab' * 32), 'image_id_mismatch'),
                ('wrong platform', lambda h: setattr(h, 'wrong_platform', True), 'image_id_mismatch'),
                ('pinned repo digest missing', lambda h: setattr(h, 'inspect_override',
                    (0, f"{RUNTIME['image_id']}|[]|linux|amd64\n")), 'image_id_mismatch'),
                ('malformed inspect output', lambda h: setattr(h, 'inspect_override', (0, 'not-an-id\n')),
                 'image_inspect_malformed'),
                ('inspect fails', lambda h: setattr(h, 'inspect_override', (125, '')), 'image_inspect_failed')):
            with self.subTest(label):
                self.host.images, self.host.wrong_platform, self.host.inspect_override = {}, False, None
                self.host.pulled_id = RUNTIME['image_id']
                setup(self.host)
                _, (state, got), observed, code = self.image_stage()
                self.assertEqual((state, got, code), ('failed', category, 1))
                o = observed[0]
                self.assertEqual((o['expectedId'], o['expectedPlatform']), (RUNTIME['image_id'], 'linux/amd64'))
                self.assertEqual(o['store']['driver'], 'overlay2')
                self.assertLessEqual(len(json.dumps(o)), 2048)
                if category == 'image_id_mismatch':
                    self.assertTrue(o['observed']['id'].startswith('sha256:'))
                else:
                    self.assertIsNone(o['observed'])

    def test_invalid_daemon_json_is_refused_not_overwritten(self):
        self.host.path(B.DAEMON_JSON).parent.mkdir(parents=True, exist_ok=True)
        self.host.path(B.DAEMON_JSON).write_text('{not json')
        self.host.loaded['nvidia'] = True
        container, _, _, _ = self.image_stage()
        self.assertEqual(container, ('failed', 'docker_daemon_json_invalid'))
        self.assertEqual(self.host.path(B.DAEMON_JSON).read_text(), '{not json')


class Tls(Fixture):
    """TLS-PREPARE: prepare (ACME-only nginx proven) -> issue via webroot -> install -> stop; identity reused, never
    replaced; ambiguity refused."""

    def setUp(self):
        super().setUp()
        self.host.driver_loaded_on_boot = 1
        self.host.md['attributes/rose-models-acme'] = B.ACME_FLAG
        self.host.dns = ['203.0.113.7']
        self.host.ack()
        self.payload = self.extract(self.tmp / 'build-roots')

    def tls(self):
        status, code = self.boot()
        st = status['stages']['tls']
        return (st['state'], st['detail']), st.get('observed'), code

    def certonly(self):
        return [c for c in self.host.calls if c[:2] == ['certbot', 'certonly']]

    def test_prepare_proves_acme_http_then_issues_via_webroot_registering_once(self):
        (state, detail), _, _ = self.tls()
        self.assertEqual((state, detail), ('ok', 'issued (account registered)'))
        argv = self.certonly()[0]
        self.assertEqual(argv[argv.index('-w') + 1], B.ACME_WEBROOT)
        self.assertIn('--webroot', argv)
        self.assertNotIn('--standalone', argv)
        self.assertIn('--register-unsafely-without-email', argv)
        probed = [u.split('127.0.0.1:80', 1)[1] for u in self.host.probes]
        self.assertTrue(probed[0].startswith('/.well-known/acme-challenge/rose-probe-'))
        self.assertEqual(probed[1:], list(B.ACME_404))
        self.assertEqual(list(self.host.path(B.ACME_WEBROOT + '/.well-known/acme-challenge').iterdir()), [])
        self.assertFalse(self.host.acme_running())
        status = self.boot()[0]
        self.assertEqual(self.states(status)['edge'], ('ok', 'active'))
        self.assertEqual(self.host.count('certbot', 'reconfigure'), 0)  # issued via webroot: renewal already works
        self.assertTrue({'certbot.timer', 'models-renewal-status.timer'} <= self.host.enabled)
        self.assertEqual(self.host.count('systemctl', 'start', '--no-block', 'models-renewal-status.service'), 2)  # per boot
        self.assertEqual(status['bootId'], self.host.boot_id())  # status and health receipts name their boot

    def test_existing_single_account_is_reused_never_replaced(self):
        self.host.add_account('acct-existing')
        (state, detail), _, _ = self.tls()
        self.assertEqual((state, detail), ('ok', 'issued (account reused)'))
        argv = self.certonly()[0]
        self.assertEqual(argv[argv.index('--account') + 1], 'acct-existing')
        self.assertNotIn('--register-unsafely-without-email', argv)

    def test_failed_first_issuance_keeps_its_account_and_the_retry_reuses_it(self):
        self.host.certbot_fail = True
        (state, detail), observed, code = self.tls()
        self.assertEqual((state, detail, code), ('failed', 'certificate_issuance_failed', 1))
        self.assertEqual((observed[0]['accounts'], observed[0]['lineages']), (1, []))
        self.assertEqual(observed[0]['acmeHttp']['challenge'], 200)
        self.assertFalse(self.host.acme_running())  # stopped on failure too
        self.host.certbot_fail = False
        (state, detail), _, _ = self.tls()  # same boot: issuance is not attempted twice
        self.assertEqual((state, detail, len(self.certonly())), ('failed', 'acme_issuance_already_attempted_this_boot', 1))
        self.host.reboot()  # the operator resets once more and acknowledges the new boot
        self.host.ack()
        (state, detail), _, _ = self.tls()
        self.assertEqual((state, detail), ('ok', 'issued (account reused)'))
        argv = self.certonly()[-1]
        self.assertEqual(argv[argv.index('--account') + 1], 'acct-from-failed-attempt')

    def test_issued_but_uninstalled_lineage_is_installed_not_reissued(self):
        self.host.add_account('acct-existing')
        self.host.add_lineage()
        (state, detail), _, _ = self.tls()
        self.assertEqual((state, detail, self.certonly()), ('ok', 'installed existing certificate', []))
        self.assertFalse(self.host.probes)  # no ACME HTTP needed

    def test_hook_failure_after_issuance_installs_the_retained_lineage_without_reissuing(self):
        self.host.deploy_results = [(2, '')]  # the 275d6e2 hook: argparse exit 2, nothing on stdout
        (state, detail), _, _ = self.tls()
        self.assertEqual((state, detail), ('ok', 'issued (account registered), installed after the deploy hook failed'))
        self.assertEqual(len(self.certonly()), 1)
        self.assertEqual([e for e in self.host.events if e[0] in ('certonly', 'tls-deploy')],
                         [('certonly', self.certonly()[0]), ('tls-deploy',), ('tls-deploy',)])
        self.assertEqual(B.acme_identity(self.host), (['acct-new'], ['models.a-list.com']))

    def test_retained_lineage_from_the_failed_live_hook_installs_on_the_next_reset_without_acme(self):
        self.host.add_account('acct-live')  # the live state after certificate_not_installed: same account, one lineage
        self.host.add_lineage(issued_by='webroot')
        del self.host.md[B.ACK_KEY]  # no new acknowledgement and no :80 needed
        (state, detail), _, code = self.tls()
        self.assertEqual((state, detail, code), ('ok', 'installed existing certificate', 0))
        self.assertEqual((self.certonly(), self.host.probes), ([], []))
        self.assertEqual(self.host.count(*B.ACME_NGINX), 0)
        self.assertNotIn(B.ACK_KEY, self.host.md_reads)
        self.assertEqual(B.acme_identity(self.host), (['acct-live'], ['models.a-list.com']))
        self.assertEqual(self.states(self.boot()[0])['edge'], ('ok', 'active'))

    def test_install_failures_report_the_deploy_result_and_never_reissue(self):
        refused = '{"reason": "certificate does not match key", "result": "refused", "stage": "check"}\n'
        for label, results, facts in (('argparse', [(2, '')] * 2, {}),
                                      ('refused', [(1, refused)] * 2,
                                       {'reason': 'certificate does not match key', 'result': 'refused', 'stage': 'check'})):
            with self.subTest(label):
                shutil.rmtree(self.host.path('/etc/letsencrypt'), ignore_errors=True)
                shutil.rmtree(self.host.path(B.STATE), ignore_errors=True)
                self.host.deploy_results = list(results)
                self.host.ack()  # this boot's acknowledgement
                (state, detail), observed, code = self.tls()
                self.assertEqual((state, detail, code), ('failed', 'certificate_install_failed', 1))
                self.assertEqual((observed[0]['deployExit'], observed[0]['deploy']), (results[0][0], facts))
                self.assertEqual(observed[0]['lineages'], ['models.a-list.com'])
                certonly = len(self.certonly())
                self.host.reboot()  # next reset: the kept lineage is installed, nothing is reissued
                (state, detail), _, _ = self.tls()
                self.assertEqual((state, detail, len(self.certonly())), ('ok', 'installed existing certificate', certonly))
                shutil.rmtree(self.host.path('/etc/rose-models/tls'), ignore_errors=True)

    def test_ambiguous_or_incomplete_identity_is_refused_without_issuance(self):
        for label, setup, category in (
                ('two accounts', lambda h: (h.add_account('a1'), h.add_account('a2')), 'acme_account_ambiguous'),
                ('two lineages', lambda h: (h.add_lineage(), h.add_lineage('models.a-list.com-0001')),
                 'certificate_lineage_ambiguous'),
                ('only a -0001 lineage', lambda h: h.add_lineage('models.a-list.com-0001'), 'certificate_lineage_ambiguous'),
                ('lineage without key', lambda h: h.add_lineage(complete=False), 'certificate_lineage_incomplete')):
            with self.subTest(label):
                shutil.rmtree(self.host.path('/etc/letsencrypt'), ignore_errors=True)
                setup(self.host)
                (state, detail), observed, code = self.tls()
                self.assertEqual((state, detail, code), ('failed', category, 1))
                self.assertEqual(self.certonly(), [])
                self.assertNotIn('KEY-NEVER-PRINTED', json.dumps(observed))

    def reset_acme(self):
        h = self.host
        h.acme_config_bad, h.http_override, h.acme_listens = False, {}, True
        h.acme_cmdline = h.acme_exe = h.acme_start = None
        h.acme_exit(4242)
        h.path('/proc/net/tcp').unlink(missing_ok=True)
        shutil.rmtree(h.path('/proc/5555'), ignore_errors=True)

    def test_acme_http_must_be_proven_before_any_issuance(self):
        for label, setup, category in (
                ('config invalid', lambda h: setattr(h, 'acme_config_bad', True), 'acme_http_config_invalid'),
                ('parent exits nonzero', lambda h: setattr(h, 'acme_start', 'exit'), 'acme_http_start_failed'),
                ('parent past deadline', lambda h: setattr(h, 'acme_start', 'deadline'), 'acme_http_start_failed'),
                ('pid loaded another config',
                 lambda h: setattr(h, 'acme_cmdline', ['nginx: master process /usr/sbin/nginx -c /etc/other.conf']),
                 'acme_http_pid_unverified'),
                ('pid holds no :80 listener', lambda h: setattr(h, 'acme_listens', False), 'acme_http_pid_unverified'),
                ('pid runs another binary', lambda h: setattr(h, 'acme_exe', '/usr/bin/python3'), 'acme_http_pid_unverified'),
                ('port already taken', lambda h: h.listen(80, 999), 'acme_http_port_occupied'),
                ('root path answers', lambda h: h.http_override.update({'/': (200, b'')}), 'acme_http_not_ready'),
                ('auth path answers', lambda h: h.http_override.update({'/v1/auth/token': (405, b'')}), 'acme_http_not_ready')):
            with self.subTest(label):
                self.reset_acme()
                setup(self.host)
                (state, detail), observed, code = self.tls()
                self.assertEqual((state, detail, code), ('failed', category, 1))
                self.assertEqual(self.certonly(), [])
                if category == 'acme_http_start_failed':
                    self.assertEqual(observed, [{'reason': self.host.acme_start}])  # a category, never raw stderr

    def test_stale_foreign_and_leftover_pids(self):
        pidfile = self.host.path('/run/rose-acme/nginx.pid')
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text('9999\n')  # stale: no such process
        self.host.ack(payload='other')  # stop after the proof
        self.assertEqual(self.tls()[0], ('failed', 'acme_ack_timeout'))
        self.assertEqual(self.host.count('/usr/sbin/nginx', '-p', '/run/rose-acme', '-c',
                                         '/etc/rose-models/edge/acme-only.conf', '-s', 'quit'), 1)  # only its own
        self.assertFalse(self.host.acme_running())
        foreign = self.host.path('/proc/5555')
        foreign.mkdir(parents=True)
        (foreign / 'exe').symlink_to('/usr/bin/python3')
        (foreign / 'stat').write_text('5555 (python3) S 1 5555 5555 0 -1\n')
        pidfile.write_text('5555\n')
        before = len(self.host.calls)
        self.assertEqual(self.tls()[0], ('failed', 'acme_http_pid_foreign'))
        self.assertEqual((pidfile.read_text(), foreign.exists()), ('5555\n', True))  # neither touched nor killed
        self.assertFalse([c for c in self.host.calls[before:] if 'quit' in c or c == B.ACME_NGINX])
        self.assertFalse([e for e in self.host.events if e[0] == 'kill'])
        self.reset_acme()
        self.host.start_daemon(B.ACME_NGINX)  # left by an earlier attempt: replaced, so the loaded config is current
        self.host.reboot()
        self.host.ack()
        self.assertEqual(self.tls()[0], ('ok', 'issued (account registered)'))
        self.assertEqual(self.host.count('/usr/sbin/nginx', '-p', '/run/rose-acme', '-c',
                                         '/etc/rose-models/edge/acme-only.conf', '-s', 'quit'), 3)

    def test_cleanup_escalates_to_term_only_for_its_own_nginx(self):
        self.host.ack()
        self.host.fail = {}
        original = self.host.run
        def quit_ignored(argv, timeout=300, env=None):
            if argv[-2:] == ['-s', 'quit']:
                self.host.calls.append(argv)
                return 0, ''
            return original(argv, timeout, env)
        self.host.run = quit_ignored
        (state, detail), _, _ = self.tls()
        self.assertEqual((state, detail), ('ok', 'issued (account registered)'))
        self.assertEqual([e for e in self.host.events if e[0] == 'kill'], [('kill', 4242, B.signal.SIGTERM)])
        self.assertFalse(self.host.acme_running())

    def test_no_acme_listener_without_authorization_or_dns(self):
        del self.host.md['attributes/rose-models-acme']
        self.assertEqual(self.tls()[0], ('waiting', 'acme_not_authorized'))
        self.host.md['attributes/rose-models-acme'] = B.ACME_FLAG
        self.host.dns = ['198.51.100.1']
        self.assertEqual(self.tls()[0], ('waiting', 'dns_not_pointing_here'))
        self.assertFalse(self.host.acme_running())
        self.assertEqual(self.host.count('/usr/sbin/nginx'), 0)

    def test_standalone_issued_lineage_is_switched_to_webroot_at_the_edge(self):
        self.host.add_account('acct-existing')
        self.host.add_lineage(issued_by='standalone')
        status, _ = self.boot()
        self.assertEqual(self.states(status)['edge'], ('ok', 'active'))
        self.assertEqual(self.host.count('certbot', 'reconfigure'), 1)


class AcmeHandshake(Fixture):
    """TLS-HANDSHAKE: the ACME-only proof is published BEFORE issuance; issuance waits (bounded) for the operator's
    metadata acknowledgement bound to this exact payload and boot; a timeout issues nothing; one issuance per boot."""

    def setUp(self):
        super().setUp()
        self.host.driver_loaded_on_boot = 1
        self.host.md['attributes/rose-models-acme'] = B.ACME_FLAG
        self.host.dns = ['203.0.113.7']
        self.payload = self.extract(self.tmp / 'build-roots')

    def tls(self):
        status, code = self.boot()
        st = status['stages']['tls']
        return (st['state'], st['detail']), st.get('observed'), code

    def certonly(self):
        return [e for e in self.host.events if e[0] == 'certonly']

    def test_proof_is_published_before_waiting_and_issuance_follows_the_bound_ack(self):
        def operator(host):  # reads the proof, restores :80 ingress, then acknowledges this payload and boot
            if host.now >= 60 and B.ACK_KEY not in host.md:
                proof = json.loads(host.guest['acme-proof'])
                host.md[B.ACK_KEY] = proof['awaiting'].split('=', 1)[1]
        self.host.on_sleep = operator
        (state, detail), _, code = self.tls()
        self.assertEqual((state, detail, code), ('ok', 'issued (account registered)', 0))
        kinds = [e[0] for e in self.host.events]
        self.assertEqual(kinds, ['publish', 'certonly', 'tls-deploy'])  # proof strictly before the single issuance
        proof = json.loads(self.host.events[0][1].split(' ', 1)[1])
        conf = self.host.path('/etc/rose-models/edge/acme-only.conf').read_bytes()
        self.assertEqual((proof['payloadSha256'], proof['bootId']), ('sha', 'boot-1'))
        self.assertEqual(proof['pidVerified'], {'exe': True, 'config': True, 'listener': True})
        self.assertEqual(proof['configSha256'], __import__('hashlib').sha256(conf).hexdigest())
        self.assertEqual(proof['acmeHttp'], {'challenge': 200, **{p: 404 for p in B.ACME_404}})
        self.assertEqual(proof['awaiting'], 'metadata rose-models-acme-ack=sha:boot-1')
        self.assertEqual(json.loads(self.host.guest['acme-proof']), proof)
        self.assertEqual(self.host.sleeps, 6)  # waited ~60 s for the operator, bounded polling

    def test_no_ack_times_out_without_issuance(self):
        (state, detail), observed, code = self.tls()
        self.assertEqual((state, detail, code), ('failed', 'acme_ack_timeout', 1))
        self.assertEqual((observed[0]['seen'], observed[0]['expectedAck']), ('absent', 'sha:boot-1'))
        self.assertEqual(self.certonly(), [])
        self.assertFalse(self.host.acme_running())
        self.assertEqual(self.host.sleeps, B.ACK_TIMEOUT_S // B.ACK_POLL_S)  # bounded

    def test_stale_boot_or_wrong_payload_ack_never_issues(self):
        for label, value in (('previous boot', 'sha:boot-0'), ('other payload', 'f599a34b:boot-1'),
                             ('bare payload', 'sha'), ('padded', 'sha:boot-1 extra')):
            with self.subTest(label):
                self.host.md[B.ACK_KEY] = value
                self.host.path(B.STATE).mkdir(parents=True, exist_ok=True)
                (state, detail), observed, _ = self.tls()
                self.assertEqual((state, detail, observed[0]['seen']), ('failed', 'acme_ack_timeout', 'mismatch'))
                self.assertEqual(self.certonly(), [])

    def test_issuance_happens_at_most_once_per_acknowledged_boot(self):
        self.host.ack()
        self.host.certbot_fail = True
        self.assertEqual(self.tls()[0], ('failed', 'certificate_issuance_failed'))
        self.host.certbot_fail = False
        self.assertEqual(self.tls()[0], ('failed', 'acme_issuance_already_attempted_this_boot'))
        self.assertEqual(len(self.certonly()), 1)
        self.host.reboot()
        self.assertEqual(self.tls()[0], ('failed', 'acme_ack_timeout'))  # the old boot's ack is stale now
        self.host.ack()
        self.assertEqual(self.tls()[0], ('ok', 'issued (account reused)'))
        self.assertEqual(len(self.certonly()), 2)

    def test_the_only_operator_channel_is_existing_instance_metadata_and_reset(self):
        self.host.ack()
        self.boot()
        self.assertTrue(self.host.md_reads <= {'attributes/rose-models-acme', B.ACK_KEY, B.DIAG_KEY,
                                               'network-interfaces/0/access-configs/0/external-ip'}, self.host.md_reads)
        readme = (HERE.parent / 'service/README.md').read_text()
        section = readme[readme.index('**Operator (Mac)'):readme.index('**Impact of that one reset')]
        commands = [block.split('\n', 1)[1].replace('\\\n', ' ') for block in section.split('```bash')[1:]]
        commands = [c.split('```', 1)[0].strip() for c in commands]
        self.assertTrue(commands)
        for command in commands:
            words = command.split()
            self.assertEqual(words[:3], ['gcloud', 'compute', 'instances'], command)
            self.assertIn(words[3], ('add-metadata', 'reset', 'get-serial-port-output', 'get-guest-attributes'), command)
            self.assertNotIn('ssh', command)
        self.assertIn('add-metadata', section)
        self.assertIn('rose-models-acme-ack', section)
        self.assertIn('reset', section)
        for word in ('80', 'closed'):
            self.assertIn(word, section)


SHA = 'ab' * 32


class DiagnosticFixture(Fixture):
    """A deployed host after a reboot: the enabled model started by systemd, /run empty."""

    def setUp(self):
        super().setUp()
        self.host.driver_loaded_on_boot = 1
        self.payload = self.extract(self.tmp / 'build-roots')
        B.run(self.host, self.payload, SHA)  # a normal first boot: model, TLS and edge as deployed
        self.host.deploy_current()
        self.host.events.clear()
        self.host.reboot()
        self.host.model_active = self.host.model_container = True  # systemd starts the enabled model at boot
        self.host.path(B.MODEL_HOLD).unlink(missing_ok=True)  # /run is empty after a reboot
        self.expected = ['docker', 'run', '--rm', '--pull=never', '--runtime=runc', '--network', 'none',
                         '--name', 'muse-glimmer-validate',
                         '-e', 'HF_HUB_OFFLINE=1', '--entrypoint', 'vllm', RUNTIME['image_id'], 'serve',
                         *next(o for o in CONFIG['options'] if o['id'] == 'a100x2-bf16-vllm-persistent')
                         ['container_spec']['args']]

    def boot(self, value=None):
        if value is not None:
            self.host.md[B.DIAG_KEY] = value
        self.host.calls.clear()
        status, code = B.run(self.host, self.payload, SHA)
        return status, code

    def published(self):
        return json.loads(self.host.guest['diagnostic'])

    PROVISIONING = (('apt-get',), ('apt-mark',), ('gpg',), ('docker', 'pull'), ('nvidia-ctk',), ('mkfs.ext4',),
                    ('mount',), ('certbot',), ('systemctl', 'restart'), ('systemctl', 'enable'),
                    ('systemctl', 'disable'), ('systemctl', 'daemon-reload'), ('systemctl', '--no-block', 'reboot'))

    def assertNoProvisioning(self):
        made = [c for c in self.host.calls for p in self.PROVISIONING if tuple(c[:len(p)]) == p]
        self.assertEqual(made, [])
        self.assertFalse(getattr(self.host, 'reboot_requested', False))



class Diagnostic(DiagnosticFixture):
    """The explicit, one-shot offline CLI check through existing metadata, with the model held and stopped first."""

    def test_requested_check_holds_and_stops_the_model_first_then_runs_once(self):
        self.host.validate_output = (b'INFO starting\nusage: vllm serve\nvllm serve: error: argument --tool-call-parser:'
                                     b' invalid choice: SECRET-TEXT\n')
        self.host.validate_rc = 2
        status, code = self.boot(f'offline-validate:{SHA}:first')
        self.assertEqual(self.states(status)['diagnostic'][0], 'ok')
        self.assertEqual(list(status['stages']), ['diagnostic'])  # no ordinary stage runs on a diagnostic boot
        self.assertNoProvisioning()
        kinds = [e[0] for e in self.host.events if e[0] in ('stop', 'validate', 'rm')]
        self.assertEqual(kinds, ['stop', 'validate', 'rm'])
        _, argv, active, container, held = next(e for e in self.host.events if e[0] == 'validate')
        self.assertEqual(argv, self.expected)
        self.assertEqual((active, container, held), (False, False, True))  # never alongside the model service
        for forbidden in ('--gpus', '-v', '--volume', '--mount', '--privileged'):
            self.assertNotIn(forbidden, argv)
        result = self.published()
        self.assertEqual({k: result[k] for k in ('payloadSha256', 'attemptId', 'check', 'imagePresent', 'exitCode',
                                                  'firstCategory', 'categories', 'limitReached', 'modelHeld')},
                         {'payloadSha256': SHA, 'attemptId': 'first', 'check': 'vllm-serve-cli-offline',
                          'imagePresent': True, 'exitCode': 2, 'firstCategory': 'argument_rejected',
                          'categories': ['argument_rejected'], 'limitReached': None, 'modelHeld': True})
        self.assertNotIn('SECRET', self.host.guest['diagnostic'])
        self.assertEqual((result['cleanup'], result['prerequisite']), ('removed', None))
        self.assertFalse([c for c in self.host.calls if 'models-edge.service' in c or c[0] == 'certbot'])  # HTTPS untouched
        self.assertTrue(self.host.path('/etc/rose-models/tls/current/fullchain.pem').exists())  # certificate kept
        self.assertTrue(self.host.path(B.MODEL_HOLD).exists())
        # the same value never runs again
        self.host.reboot()
        self.host.path(B.MODEL_HOLD).unlink()
        self.host.events.clear()
        status, _ = self.boot()
        self.assertEqual(self.states(status)['diagnostic'], ('ok', 'ignored: attempt_already_used'))
        self.assertFalse([e for e in self.host.events if e[0] in ('stop', 'validate')])
        self.assertIn(['systemctl', 'enable', '--now', *B.MODEL_UNITS], self.host.calls)  # normal service again
        self.assertFalse(self.host.path(B.MODEL_HOLD).exists())

    def test_other_payload_or_malformed_values_never_hold_or_run(self):
        for value, reason in ((f'offline-validate:{"cd" * 32}:x', 'other_payload'),
                              (f'offline-validate:{SHA}:Bad_Id', 'malformed'), ('yes please', 'malformed')):
            with self.subTest(value):
                self.host.events.clear()
                status, _ = self.boot(value)
                self.assertEqual(self.states(status)['diagnostic'], ('ok', f'ignored: {reason}'))
                self.assertFalse(self.host.path(B.MODEL_HOLD).exists())
                self.assertFalse([e for e in self.host.events if e[0] in ('stop', 'validate')])
        del self.host.md[B.DIAG_KEY]
        self.assertEqual(self.states(self.boot()[0])['diagnostic'], ('ok', 'not requested'))

    def test_a_model_that_does_not_stop_blocks_the_check(self):
        self.host.model_stuck = True
        status, code = self.boot(f'offline-validate:{SHA}:stuck')
        self.assertEqual((self.states(status)['diagnostic'], code), (('failed', 'diagnostic_model_not_stopped'), 1))
        self.assertFalse([e for e in self.host.events if e[0] == 'validate'])
        self.assertLessEqual(self.host.sleeps, B.DIAG_STOP_S // 2 + 1)  # bounded wait
        self.assertTrue((self.host.path(B.STATE) / 'diagnostic-stuck').exists())  # the attempt is spent

    def test_absent_or_mismatched_prerequisites_are_refused_without_provisioning(self):
        daemon = self.host.path(B.DAEMON_JSON)
        before = daemon.read_bytes() if daemon.exists() else None
        cases = (
            ('image genuinely absent', lambda h: h.images.clear(), 'image_missing', False),
            ('another store reports another ID', lambda h: h.images.update(
                {k: ('sha256:' + 'e' * 64, *v[1:]) for k, v in h.images.items()}), 'image_mismatch', False),
            ('no runc runtime', lambda h: setattr(h, 'no_runc', True), 'runc_unavailable', True),
            ('docker daemon down', lambda h: setattr(h, 'docker_down', True), 'docker_unavailable', True))
        for i, (label, setup, category, image_present) in enumerate(cases):
            with self.subTest(label):
                saved = dict(self.host.images)
                setup(self.host)
                self.host.events.clear()
                status, code = self.boot(f'offline-validate:{SHA}:pre{i}')
                self.assertEqual((self.states(status)['diagnostic'], code),
                                 (('failed', 'diagnostic_prerequisite_missing'), 1))
                result = self.published()
                self.assertEqual((result['prerequisite'], result['imagePresent'], result['exitCode']),
                                 (category, image_present, None))
                self.assertNotIn('validate', [e[0] for e in self.host.events])
                self.assertNoProvisioning()
                self.assertEqual(daemon.read_bytes() if daemon.exists() else None, before)
                self.host.images.clear()
                self.host.images.update(saved)
                self.host.no_runc = self.host.docker_down = False
                self.host.model_active = self.host.model_container = True

    def test_container_state_must_be_positively_absent(self):
        for i, (label, answer) in enumerate((('daemon error', (1, '')), ('query timed out', (124, '')))):
            with self.subTest(label):
                self.host.ps_results = [answer]
                self.host.events.clear()
                status, code = self.boot(f'offline-validate:{SHA}:state{i}')
                self.assertEqual(self.states(status)['diagnostic'], ('failed', 'diagnostic_container_state_unknown'))
                self.assertNotIn('validate', [e[0] for e in self.host.events])
                self.host.model_active = self.host.model_container = True
        self.host.model_container = True
        self.host.model_stuck = True  # the unit stops but its container survives
        status, _ = self.boot(f'offline-validate:{SHA}:survivor')
        self.assertEqual(self.states(status)['diagnostic'], ('failed', 'diagnostic_model_not_stopped'))

    def test_cleanup_is_verified(self):
        self.host.leave_container, self.host.rm_fails = True, True  # killed CLI; removal fails; container survives
        status, code = self.boot(f'offline-validate:{SHA}:rmfail')
        self.assertEqual((self.states(status)['diagnostic'], code), (('failed', 'diagnostic_cleanup_failed'), 1))
        self.assertEqual((self.published()['cleanup'], self.published()['cleanupRemoveExit']), ('failed', 1))
        self.host.rm_fails, self.host.validate_container = False, False
        self.host.model_active = self.host.model_container = True
        self.host.ps_results = [(0, ''), (124, '')]  # absent before the check; the post-removal query times out
        status, _ = self.boot(f'offline-validate:{SHA}:rmunknown')
        self.assertEqual(self.states(status)['diagnostic'], ('failed', 'diagnostic_cleanup_failed'))
        self.assertEqual(self.published()['cleanup'], 'unknown')
        self.host.leave_container = False
        self.host.model_active = self.host.model_container = True
        self.boot(f'offline-validate:{SHA}:rmok')
        self.assertEqual(self.published()['cleanup'], 'removed')

    def test_output_is_inspected_only_to_the_bound(self):
        self.host.validate_output = b'z' * (3 << 20) + b' OutOfMemoryError'
        self.boot(f'offline-validate:{SHA}:flood')
        result = self.published()
        self.assertEqual((result['bytesInspected'], result['limitReached'], result['firstCategory']),
                         (2 << 20, 'bytes', None))


class GpuStartOnce(DiagnosticFixture):
    """gpu-start-once: one production GPU start, stopped on listen or at 900 s, always cleaned up, inference held."""

    def gpu(self, attempt, **flags):
        for k, v in flags.items():
            setattr(self.host, k, v)
        self.host.events.clear()
        return self.boot(f'gpu-start-once:{SHA}:{attempt}')

    def model_started(self):
        return [c for c in self.host.calls if c[:1] == ['systemctl'] and c[1] in ('start', 'restart', 'enable')
                and 'muse-glimmer.service' in c]

    def test_online_first_load_stops_on_listen_cleans_up_and_keeps_inference_held(self):
        status, code = self.gpu('listen', gpu_listens=True, verify_rc=3)
        self.assertEqual((self.states(status)['diagnostic'][0], code, list(status['stages'])), ('ok', 0, ['diagnostic']))
        r = self.published()
        self.assertEqual({k: r[k] for k in ('check', 'branch', 'verifyExit', 'stoppedOn', 'listenedAfterS', 'exitCode',
                                             'cleanup', 'prerequisite', 'modelHeld')},
                         {'check': 'gpu-start-once', 'branch': 'online', 'verifyExit': 3, 'stoppedOn': 'listen',
                          'listenedAfterS': 42.0, 'exitCode': None, 'cleanup': 'removed', 'prerequisite': None,
                          'modelHeld': True})
        _, argv, active, container, held = next(e for e in self.host.events if e[0] == 'validate')
        self.assertEqual(argv, B.launch_argv(self.payload, 'online'))  # the payload's exact launch-online command
        self.assertEqual(argv[:3], ['docker', 'run', '--pull=never'])
        self.assertNotIn('HF_HUB_OFFLINE=1', argv)  # online first load: production's Hugging Face path
        self.assertEqual((active, container, held), (False, False, True))
        self.assertEqual([e[0] for e in self.host.events if e[0] in ('stop', 'verify', 'validate', 'rm')],
                         ['stop', 'verify', 'validate', 'rm'])
        self.assertEqual((self.host.model_container, self.host.port_open), (False, False))  # nothing left serving
        self.assertTrue(self.host.path(B.MODEL_HOLD).exists())  # inference stays held for this boot
        self.assertEqual(self.model_started(), [])
        self.assertNoProvisioning()

    def test_offline_branch_failure_reports_fixed_categories_only(self):
        status, code = self.gpu('oom', verify_rc=0, validate_rc=1,
                                validate_output=b'Traceback (most recent call last):\ntorch.OutOfMemoryError: CUDA out of'
                                                b' memory. SECRET-TEXT\n')
        r = self.published()
        self.assertEqual((r['branch'], r['exitCode'], r['stoppedOn'], r['firstCategory'], r['categories'], r['cleanup']),
                         ('offline', 1, None, 'cuda_out_of_memory', ['cuda_out_of_memory', 'traceback'], 'removed'))
        argv = next(e for e in self.host.events if e[0] == 'validate')[1]
        self.assertEqual(argv, B.launch_argv(self.payload, 'offline'))
        self.assertIn('HF_HUB_OFFLINE=1', argv)
        self.assertNotIn('SECRET', self.host.guest['diagnostic'])
        self.assertEqual(r['exceptionType'], 'OutOfMemoryError')  # an allowlisted token; never the message

    def test_deadline_kills_and_cleans_up(self):
        self.gpu('slow', gpu_deadline=True)
        r = self.published()
        self.assertEqual((r['stoppedOn'], r['exitCode'], r['elapsedS'], r['listenedAfterS'], r['cleanup']),
                         ('deadline', None, 900.0, None, 'removed'))
        self.assertEqual(B.GPU_SECONDS, 900)

    def test_other_verifier_status_and_missing_prerequisites_are_refused_without_running(self):
        cases = (('mismatch', dict(verify_rc=4), 'verify_refused'), ('error', dict(verify_rc=1), 'verify_refused'),
                 ('unmounted', dict(data_mounted=False), 'data_not_mounted'),
                 ('busy', dict(port_open=True), 'port_in_use'))
        for attempt, flags, category in cases:
            with self.subTest(attempt):
                status, code = self.gpu(attempt, **flags)
                self.assertEqual((self.states(status)['diagnostic'], code),
                                 (('failed', 'diagnostic_prerequisite_missing'), 1))
                self.assertEqual(self.published()['prerequisite'], category)
                self.assertNotIn('validate', [e[0] for e in self.host.events])
                self.assertEqual(self.model_started(), [])
                self.assertNoProvisioning()
                self.host.verify_rc, self.host.data_mounted, self.host.port_open = 3, True, False
                self.host.model_active = self.host.model_container = True
        saved = dict(self.host.images)
        self.host.images.clear()  # the exact image is not cached: refused, never pulled
        self.gpu('noimage')
        self.assertEqual(self.published()['prerequisite'], 'image_missing')
        self.assertNoProvisioning()
        self.host.images.update(saved)

    def test_cleanup_failure_is_reported(self):
        status, code = self.gpu('rmfail', gpu_listens=True, rm_fails=True)
        self.assertEqual((self.states(status)['diagnostic'], code), (('failed', 'diagnostic_cleanup_failed'), 1))
        self.assertEqual((self.published()['cleanup'], self.published()['stoppedOn']), ('failed', 'listen'))
        self.assertTrue(self.host.path(B.MODEL_HOLD).exists())

    def test_once_per_id_and_grammar(self):
        self.gpu('once', gpu_listens=True)
        self.host.reboot()
        self.host.path(B.MODEL_HOLD).unlink()
        self.host.model_active = self.host.model_container = True
        status, _ = self.boot()
        self.assertEqual(self.states(status)['diagnostic'], ('ok', 'ignored: attempt_already_used'))
        for value in (f'gpu-start-twice:{SHA}:x', f'gpu-start-once:{SHA}:', f'gpu-start-once:{SHA[:-2]}:x'):
            with self.subTest(value):
                self.assertEqual(self.states(self.boot(value)[0])['diagnostic'], ('ok', 'ignored: malformed'))


class HostValidate(unittest.TestCase):
    """The real Host.validate with a stand-in docker: bounded bytes and time, process group killed, nothing kept."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='host-validate-'))
        self.addCleanup(shutil.rmtree, self.dir, True)
        guard_spec = __import__('importlib.util').util.spec_from_file_location('startup_guard', HERE.parent / 'startup_guard.py')
        self.guard = __import__('importlib.util').util.module_from_spec(guard_spec)
        guard_spec.loader.exec_module(self.guard)

    def script(self, body):
        path = self.dir / 'docker'
        path.write_text('#!/bin/sh\n' + body)
        os.chmod(path, 0o755)
        return [str(path), 'run']

    def test_categories_exit_and_bounds(self):
        cap = self.guard.Capture(2 << 20, 10)
        code, elapsed = B.Host().validate(self.script("echo 'error: unrecognized arguments: --x'; exit 2\n"), 10, cap)
        self.assertEqual((code, cap.summary()['firstCategory']), (2, 'argument_rejected'))
        cap = self.guard.Capture(2 << 20, 10)
        code, _ = B.Host().validate(self.script("head -c 3145728 /dev/zero | tr '\\0' y; exit 1\n"), 20, cap)
        self.assertEqual((code, cap.summary()['bytesInspected'], cap.summary()['limitReached']), (1, 2 << 20, 'bytes'))

    def test_probe_stops_a_server_as_soon_as_it_listens(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        listener = self.dir / 'listener.py'
        listener.write_text('import socket, sys, time\ns = socket.socket()\ns.bind(("127.0.0.1", int(sys.argv[1])))\n'
                            's.listen()\ntime.sleep(60)\n')
        server = f'{sys.executable} {listener} {port} & sleep 60\n'
        host, cap = B.Host(), self.guard.Capture(2 << 20, 60)
        t = time.monotonic()
        code, elapsed = host.validate(self.script(server), 60, cap, probe=lambda: host.listening(port), probe_every=0.5)
        self.assertIsNone(code)
        self.assertLess(time.monotonic() - t, 15)
        time.sleep(0.5)
        self.assertFalse(host.listening(port))  # the whole process group, listener included, was killed

    def test_deadline_kills_the_whole_process_group(self):
        marker = self.dir / 'grandchild-alive'
        cap = self.guard.Capture(2 << 20, 1)
        t = time.monotonic()
        code, elapsed = B.Host().validate(self.script(f"(sleep 5; touch {marker}) & sleep 30\n"), 1, cap)
        self.assertIsNone(code)
        self.assertLess(time.monotonic() - t, 5)
        time.sleep(5.5)
        self.assertFalse(marker.exists())
        self.assertEqual(cap.summary()['limitReached'], 'time')


class Build(Fixture):
    def test_deterministic_bounded_and_complete(self):
        again = BB.build(self.tmp / 'again', source='test')
        self.assertEqual(again['payloadTarSha256'], self.build['payloadTarSha256'])
        self.assertLessEqual(self.build['metadataValueBytes'], BB.METADATA_VALUE_LIMIT)
        names = set(self.build['files'])
        for required in ('bootstrap.py', 'apt/nvidia-container-toolkit.asc', 'etc-rose-models/bin/start-model',
                         'etc-rose-models/systemd/muse-glimmer.service', 'etc-rose-models/edge/nginx.conf',
                         'etc-rose-models/lib/service/auth_service.py', 'etc-rose-models/lib/muse-glimmer-30b.json'):
            self.assertIn(required, names)
        self.assertNotIn('attestation-roots/synthetic-root.pem', names)
        self.assertIn('attestation-roots/synthetic-root.pem', self.build_roots['files'])
        self.assertNotEqual(self.build_roots['payloadTarSha256'], self.build['payloadTarSha256'])
        bad = self.tmp / 'bad-roots'
        bad.mkdir(exist_ok=True)
        (bad / 'x.pem').write_text('not a certificate')
        with self.assertRaises(BB.Invalid):
            BB.build(self.tmp / 'bad', attestation_roots=bad, source='test')

    def test_pinned_nvidia_key_is_the_vendored_file(self):
        out = subprocess.run(['gpg', '--show-keys', '--with-colons', str(HERE.parent / 'service/apt/nvidia-container-toolkit.asc')],
                             capture_output=True, text=True, env={**os.environ, 'GNUPGHOME': str(self.vm)})
        if out.returncode != 0:
            self.skipTest('gpg not available')
        fprs = [line.split(':')[9] for line in out.stdout.splitlines() if line.startswith('fpr:')]
        self.assertEqual(fprs[0], B.NVIDIA_KEY_FPR)


class StartupScript(Fixture):
    def serve(self, body):
        state = {'hits': 0}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                state['hits'] += 1
                ok = self.path.endswith('/rose-models-payload') and self.headers.get('Metadata-Flavor') == 'Google'
                data = body if ok else b''
                self.send_response(200 if ok else 404)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_address[1]}', state

    def script(self, url, opt):
        stub = self.vm / 'python-stub'
        stub.write_text('#!/bin/sh\necho "STUB $*"\n')
        os.chmod(stub, 0o755)
        env = {'PATH': os.environ['PATH'], 'ROSE_MODELS_METADATA_URL': url, 'ROSE_MODELS_OPT': str(opt),
               'ROSE_MODELS_PYTHON': str(stub)}
        return subprocess.run(['bash', str(self.tmp / 'build/startup-script.sh')], capture_output=True, text=True,
                              env=env, timeout=120)

    def test_verified_payload_runs_the_bootstrap_and_is_reused(self):
        url, state = self.serve((self.tmp / 'build/rose-models-payload.b64').read_bytes())
        opt = self.vm / 'opt'
        r = self.script(url, opt)
        sha = self.build['payloadTarSha256']
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f'run --payload {opt}/payload-{sha} --payload-sha256 {sha}', r.stdout)
        self.assertTrue((opt / f'payload-{sha}/bootstrap.py').exists())
        self.assertEqual(sorted(p.name for p in opt.iterdir()), [f'payload-{sha}'])  # no fetch leftovers
        hits = state['hits']
        r = self.script('http://127.0.0.1:9', opt)  # next boot: metadata unreachable, the verified copy is reused
        self.assertEqual((r.returncode, state['hits']), (0, hits))

    def test_tampered_or_missing_payload_is_refused_without_running(self):
        good = (self.tmp / 'build/rose-models-payload.b64').read_bytes()
        other = (self.tmp / 'build-roots/rose-models-payload.b64').read_bytes()
        for label, body, reasons in (('another payload', other, ('payload_size_mismatch', 'payload_digest_mismatch')),
                                     ('not base64', b'!!!!', ('payload_decode_failed',))):
            with self.subTest(label):
                url, _ = self.serve(body)
                opt = self.vm / f'opt-{label.replace(" ", "-")}'
                r = self.script(url, opt)
                self.assertEqual(r.returncode, 1)
                self.assertTrue(any(f'"reason": "{reason}"' in r.stdout for reason in reasons), r.stdout)
                self.assertNotIn('STUB', r.stdout)
                self.assertEqual([p.name for p in opt.iterdir()], [])
        # same size, different content
        tar = __import__('gzip').decompress(base64.b64decode(good))
        flipped = bytearray(tar)
        flipped[600] ^= 1
        body = base64.b64encode(__import__('gzip').compress(bytes(flipped)))
        url, _ = self.serve(body)
        r = self.script(url, self.vm / 'opt-flipped')
        self.assertIn('"reason": "payload_digest_mismatch"', r.stdout)
        r = self.script('http://127.0.0.1:9', self.vm / 'opt-down')
        self.assertIn('"reason": "metadata_fetch_failed"', r.stdout)


if __name__ == '__main__':
    unittest.main()
