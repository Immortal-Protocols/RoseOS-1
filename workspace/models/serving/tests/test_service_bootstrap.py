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
import subprocess
import sys
import tarfile
import tempfile
import threading
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
        self.disk = {'exists': True, 'sig': None, 'size': 500 * 10**9, 'wipefs': '', 'head': bytes(1 << 20)}
        self.mounted = False
        self.packages = {}
        self.driver_loaded_on_boot = 2  # the driver loads after this boot number
        self.boot = 1
        self.gpus, self.driver = 2, B.DRIVER_VERSION
        self.runtimes, self.image = '{"runc":{}}', None
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
            return 0, ''.join(f'{p} {v} ii \n' for p, v in self.packages.items())
        if cmd == 'gpg':
            fpr = hashlib.sha1(Path(argv[-1]).read_bytes()).hexdigest().upper()  # stand-in fingerprint
            return 0, f"pub:-:4096:1:X:::\nfpr:::::::::{self.key_fpr(fpr)}:\n"
        if cmd == 'apt-get' and argv[1] == 'install':
            self.packages = dict(B.APT_PINS)
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
            if argv[1] == 'info':
                return 0, self.runtimes
            if argv[1] == 'pull':
                self.image = self.pulled_id
                return 0, ''
            if argv[1:3] == ['image', 'inspect']:
                return (0, self.image + '\n') if self.image else (1, '')
        if cmd == 'nvidia-ctk':
            self.runtimes = '{"nvidia":{},"runc":{}}'
            return 0, ''
        if cmd == 'python3' and argv[-1] == 'secret-init':
            p = self.path('/var/lib/rose-models/auth/secret.json')
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('{"current": "SECRET-NEVER-PRINTED"}')
            return 0, ''
        if cmd == 'certbot':
            if argv[1] == 'certonly':
                cur = self.path('/etc/rose-models/tls/current')
                cur.mkdir(parents=True, exist_ok=True)
                (cur / 'fullchain.pem').write_text('cert')
            return 0, ''
        raise AssertionError(f'unexpected command {argv}')

    def key_fpr(self, fpr):
        return B.NVIDIA_KEY_FPR if fpr == self.expected_key_sha1 else fpr

    def systemctl(self, args):
        if args[0] == 'is-enabled':
            return (0, 'enabled') if args[1] in self.enabled else (1, 'disabled')
        if args[0] == 'disable':
            self.enabled -= set(args[2:])
            return 0, ''
        if args[0] == 'enable':
            self.enabled |= set(args[2:])
            return 0, ''
        if args[:2] == ['--no-block', 'reboot']:
            self.reboot_requested = True
            return 0, ''
        return 0, ''

    def metadata(self, path):
        return self.md.get(path)

    def put_guest_attribute(self, key, value):
        self.guest[key] = value

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
            (self.payload / 'keys/nvidia-container-toolkit.asc').read_bytes()).hexdigest().upper()
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
                          self.host.count('certbot', 'reconfigure'), self.host.count('docker', 'pull')), (1, 1, 1, 1))

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
            (('certbot', 'certonly'), 'tls', 'certificate_issuance_failed'))
        self.host.md['attributes/rose-models-acme'] = B.ACME_FLAG
        self.host.dns = ['203.0.113.7']
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
            (self.payload / 'keys/nvidia-container-toolkit.asc').read_bytes()).hexdigest().upper()
        self.host.pulled_id = 'sha256:' + '0' * 64
        self.assertStopsAt('image', 'image_id_mismatch')
        self.host.pulled_id = RUNTIME['image_id']
        self.host.image = None
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


class Build(Fixture):
    def test_deterministic_bounded_and_complete(self):
        again = BB.build(self.tmp / 'again', source='test')
        self.assertEqual(again['payloadTarSha256'], self.build['payloadTarSha256'])
        self.assertLessEqual(self.build['metadataValueBytes'], BB.METADATA_VALUE_LIMIT)
        names = set(self.build['files'])
        for required in ('bootstrap.py', 'keys/nvidia-container-toolkit.asc', 'etc-rose-models/bin/start-model',
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
        out = subprocess.run(['gpg', '--show-keys', '--with-colons', str(HERE.parent / 'service/keys/nvidia-container-toolkit.asc')],
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
        for label, body, reason in (('another payload', other, 'payload_digest_mismatch'),
                                    ('not base64', b'!!!!', 'payload_decode_failed')):
            with self.subTest(label):
                url, _ = self.serve(body)
                opt = self.vm / f'opt-{label.replace(" ", "-")}'
                r = self.script(url, opt)
                self.assertEqual(r.returncode, 1)
                self.assertIn(f'"reason": "{reason}"', r.stdout)
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
