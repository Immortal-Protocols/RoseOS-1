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
        self.install_versions = {}  # simulate the archive installing a different version than pinned
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
                return 0, '{"nvidia":{},"runc":{}}' if self.loaded['nvidia'] else '{"runc":{}}'
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
                return 0, f'{i}|{json.dumps(digests)}|{os_}|{arch}\n'
        if cmd == 'nvidia-ctk':
            daemon = self.path(B.DAEMON_JSON)
            daemon.parent.mkdir(parents=True, exist_ok=True)
            config = json.loads(daemon.read_text()) if daemon.exists() else {}
            config.setdefault('runtimes', {})['nvidia'] = {'path': 'nvidia-container-runtime'}
            daemon.write_text(json.dumps(config))
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
