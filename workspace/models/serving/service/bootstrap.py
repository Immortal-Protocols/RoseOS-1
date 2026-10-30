#!/usr/bin/env python3
"""Startup-metadata bootstrap of the persistent Muse Glimmer service (MODELS-ALWAYSON-SERVICE-001).

Runs as root from the VM's startup-script on every boot, after that script has verified the payload's sha256. Stages
run in order and are rerun-safe: each first checks whether its result is already in place.

  disk       the blank data disk gets ext4 labelled models-data. Any other existing content is refused, never formatted.
             Then it is mounted at /mnt/models-data
  packages   pinned Ubuntu and NVIDIA-repository versions (NVIDIA key fingerprint checked), then apt-mark hold
  driver     nvidia-smi reports DRIVER_VERSION on GPU_COUNT GPUs; otherwise at most ONE reboot, then failure
  container  the docker nvidia runtime (nvidia-ctk) and Docker's classic overlay2 image store (containerd-snapshotter
             off), where .Id is the config digest
  image      pull by the pinned manifest digest; .Id must equal the pinned config ID, RepoDigests must hold the
             pinned reference, and the platform must be linux/amd64
  nginx      the installed binary's sha256 equals the pin; the distro nginx unit is disabled
  service    the rendered /etc/rose-models tree and systemd units; enables vLLM, health and verification timers
  auth       secret-init once; the auth service only once attestation roots are present
  tls        an installed certificate; else an issued-but-uninstalled lineage is installed (never reissued); else, with
             rose-models-acme set and DNS here, an ACME-only Nginx is started and proven (challenge 200, all else 404);
             that proof is published, then the stage waits (bounded) for metadata rose-models-acme-ack equal to
             <payload sha256>:<boot id>. Only then does certbot issue once via its webroot, reusing the single existing
             account (ambiguity fails). A timeout issues nothing. The ACME-only Nginx is stopped either way
  edge       the Nginx edge once TLS and auth are up; a standalone-issued lineage is switched to the webroot

Every command has a time bound. Output: one nonsecret JSON line (stdout, so it reaches the serial console), the status
file, and the guest attribute rose-models/bootstrap. Exit 0 when ready, waiting or rebooting; exit 1 when a stage failed.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

DRIVER_VERSION = '580.178.04'
GPU_COUNT = 2
APT_PINS = {  # read from the Ubuntu noble archive and NVIDIA's stable index on 2026-10-30
    'nvidia-headless-no-dkms-580-server': '580.178.04-0ubuntu0.24.04.1',
    'nvidia-utils-580-server': '580.178.04-0ubuntu0.24.04.1',
    'docker.io': '29.1.3-0ubuntu3~24.04.2',
    'nginx': '1.24.0-2ubuntu7.19',
    'certbot': '2.9.0-1',
    'python3-cryptography': '41.0.7-4ubuntu0.4',
    'qrencode': '4.1.1-1build2',
    'nvidia-container-toolkit': '1.20.1-1',
    'nvidia-container-toolkit-base': '1.20.1-1',
    'libnvidia-container-tools': '1.20.1-1',
    'libnvidia-container1': '1.20.1-1',
}
KERNEL_MODULES = 'linux-modules-nvidia-580-server-gcp'  # follows the gcp kernel; checked by the loaded driver version
NVIDIA_KEY_FPR = 'C95B321B61E88C1809C4F759DDCAE044F796ECB0'
NVIDIA_REPO = ('deb [signed-by=/etc/apt/keyrings/nvidia-container-toolkit.asc] '
               'https://nvidia.github.io/libnvidia-container/stable/deb/amd64 /')
NGINX_SHA256 = '5bfff15e0a9e0bce5c71be8179d77880f9b17c3f575194ff93c912a415756eed'
DISK = '/dev/disk/by-id/google-models-data'
DISK_LABEL, MOUNT, DISK_MIN_BYTES = 'models-data', '/mnt/models-data', 200 * 10**9
FSTAB_LINE = f'LABEL={DISK_LABEL} {MOUNT} ext4 defaults,nofail 0 2'
HOSTNAME = 'models.a-list.com'
ACME_FLAG = 'agree-tos-no-email'  # metadata rose-models-acme: the operator's explicit ACME authorization
ROOT = '/etc/rose-models'
STATE = '/var/lib/rose-models/bootstrap'
MODEL_UNITS = ('muse-glimmer.service', 'models-health.timer', 'models-verify-full.timer',
               'models-attestation-status.timer')
METADATA = 'http://metadata.google.internal/computeMetadata/v1/instance/'
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Failed(Exception):
    def __init__(self, category, observed=None):
        super().__init__(category)
        self.observed = observed  # bounded, nonsecret facts for the status line (package versions only)


class Waiting(Exception):
    pass


class Reboot(Exception):
    pass


class Host:
    """Every side effect of the bootstrap. Tests replace it; `prefix` relocates file paths."""

    def __init__(self, prefix='/'):
        self.prefix = Path(prefix)

    def path(self, p):
        return self.prefix / p.lstrip('/')

    def run(self, argv, timeout=300, env=None):
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                               env=None if env is None else {**os.environ, **env})
            return r.returncode, r.stdout
        except subprocess.TimeoutExpired:
            return 124, ''
        except OSError:
            return 127, ''

    def metadata(self, path):
        req = urllib.request.Request(METADATA + path, headers={'Metadata-Flavor': 'Google'})
        try:
            with NO_PROXY.open(req, timeout=10) as r:
                return r.read(4096).decode()
        except OSError:
            return None

    def put_guest_attribute(self, key, value):
        req = urllib.request.Request(METADATA + 'guest-attributes/rose-models/' + key, data=value.encode(),
                                     headers={'Metadata-Flavor': 'Google'}, method='PUT')
        try:
            NO_PROXY.open(req, timeout=10).close()
        except OSError:
            pass  # guest attributes are optional (enable-guest-attributes=TRUE)

    def resolve(self, name):
        try:
            return sorted({a[4][0] for a in socket.getaddrinfo(name, 443, socket.AF_INET)})
        except OSError:
            return []

    def http_get(self, url, host_header):
        """(status, first 256 body bytes) of one bounded loopback GET; (0, b'') when unreachable."""
        req = urllib.request.Request(url, headers={'Host': host_header})
        try:
            with NO_PROXY.open(req, timeout=10) as r:
                return r.status, r.read(256)
        except urllib.error.HTTPError as e:
            return e.code, b''
        except OSError:
            return 0, b''

    def sleep(self, seconds):
        time.sleep(seconds)

    def clock(self):
        return time.monotonic()

    def publish(self, line):
        """An interim nonsecret line on the serial console (the startup script's stdout)."""
        print(line, flush=True)

    def read_head(self, device, n):
        with open(self.path(device), 'rb') as f:
            return f.read(n)

    def boot_id(self):
        return self.path('/proc/sys/kernel/random/boot_id').read_text().strip()


def must(host, argv, category, timeout=300):
    rc, out = host.run(argv, timeout)
    if rc != 0:
        raise Failed(category)
    return out


# ---- stages --------------------------------------------------------------------------------------------------------

def stage_disk(host, ctx):
    if not host.path(DISK).exists():
        raise Failed('data_disk_missing')
    rc, fs = host.run(['findmnt', '-n', '-o', 'SOURCE,FSTYPE', MOUNT], 30)
    if rc != 0:
        rc, sig = host.run(['blkid', '-p', '-o', 'export', DISK], 30)
        fields = dict(line.split('=', 1) for line in sig.splitlines() if '=' in line)
        if rc == 0 and (fields.get('TYPE'), fields.get('LABEL')) == ('ext4', DISK_LABEL):
            pass  # formatted by an earlier run
        elif rc == 2:  # blkid found no signature: prove the disk is blank before formatting it
            _, size = host.run(['blockdev', '--getsize64', DISK], 30)
            if not size.strip().isdigit() or int(size) < DISK_MIN_BYTES:
                raise Failed('data_disk_too_small')
            rc, wipe = host.run(['wipefs', '--no-act', '--noheadings', DISK], 30)
            if rc != 0 or wipe.strip() or any(host.read_head(DISK, 1 << 20)):
                raise Failed('data_disk_not_blank')
            must(host, ['mkfs.ext4', '-q', '-L', DISK_LABEL, DISK], 'mkfs_failed', 600)
        else:
            raise Failed('data_disk_not_blank')  # some other filesystem or partition table: never formatted
        fstab = host.path('/etc/fstab')
        lines = fstab.read_text().splitlines() if fstab.exists() else []
        if FSTAB_LINE not in lines:
            fstab.write_text('\n'.join(lines + [FSTAB_LINE]) + '\n')
        host.path(MOUNT).mkdir(parents=True, exist_ok=True)
        must(host, ['mount', MOUNT], 'mount_failed', 60)
    rc, fs = host.run(['findmnt', '-n', '-o', 'LABEL,FSTYPE', MOUNT], 30)
    if rc != 0 or fs.split() != [DISK_LABEL, 'ext4']:
        raise Failed('data_disk_wrong_mount')
    return 'mounted'


PKG_FORMAT = '${Package}\\t${Version}\\t${db:Status-Abbrev}\\n'


def installed(host):
    """{package: (version, status abbrev)} for the pins, from one dpkg-query. Exit 1 only means some package is unknown
    to dpkg; any other failure, an unknown or duplicate package, or a malformed line refuses."""
    rc, out = host.run(['dpkg-query', '-W', '-f', PKG_FORMAT, *APT_PINS], 60)
    if rc not in (0, 1):
        raise Failed('package_query_failed')
    seen = {}
    for line in out.splitlines():
        fields = line.split('\t')
        if len(fields) != 3 or fields[0] not in APT_PINS or fields[0] in seen or len(fields[2]) not in (2, 3):
            raise Failed('package_query_malformed')
        seen[fields[0]] = (fields[1], fields[2])
    return seen


def satisfied(entry, version):
    """Exactly the pinned version, fully installed with no error flag; held (h) or plain install (i) selection."""
    if entry is None:
        return False
    got, abbrev = entry
    return got == version and abbrev[0] in 'ih' and abbrev[1] == 'i' and abbrev[2:].strip() == ''


def package_report(host, seen, candidates=False):
    """Bounded nonsecret expected/observed facts for every pin that is not satisfied."""
    report = [{'package': p, 'expected': v, 'observed': (seen.get(p) or (None,))[0],
               'status': (seen.get(p) or (None, None))[1]}
              for p, v in APT_PINS.items() if not satisfied(seen.get(p), v)]
    if candidates and report:
        rc, out = host.run(['apt-cache', 'policy', *[r['package'] for r in report]], 60)
        current = None
        for line in out.splitlines() if rc == 0 else []:
            if line and not line.startswith(' ') and line.endswith(':'):
                current = line[:-1]
            elif line.strip().startswith('Candidate:') and current:
                for r in report:
                    if r['package'] == current:
                        r['candidate'] = line.split(':', 1)[1].strip()[:64]
    return report[:len(APT_PINS)]


def stage_packages(host, ctx):
    if not package_report(host, installed(host)):
        return 'pinned'
    key = host.path('/etc/apt/keyrings/nvidia-container-toolkit.asc')
    key.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ctx['payload'] / 'apt' / 'nvidia-container-toolkit.asc', key)
    out = must(host, ['gpg', '--show-keys', '--with-colons', str(key)], 'nvidia_key_unreadable', 60)
    fprs = [line.split(':')[9] for line in out.splitlines() if line.startswith('fpr:')]
    if not fprs or fprs[0] != NVIDIA_KEY_FPR:
        raise Failed('nvidia_key_fingerprint_mismatch')
    sources = host.path('/etc/apt/sources.list.d/nvidia-container-toolkit.list')
    sources.parent.mkdir(parents=True, exist_ok=True)
    sources.write_text(NVIDIA_REPO + '\n')
    env = {'DEBIAN_FRONTEND': 'noninteractive'}
    if host.run(['apt-get', 'update'], 900, env)[0] != 0:
        raise Failed('apt_update_failed')
    pins = [f'{p}={v}' for p, v in APT_PINS.items()]
    if host.run(['apt-get', 'install', '-y', '--no-install-recommends', *pins, KERNEL_MODULES], 3600, env)[0] != 0:
        raise Failed('package_version_unavailable_or_install_failed', package_report(host, installed(host), True))
    must(host, ['apt-mark', 'hold', *APT_PINS], 'apt_hold_failed', 60)
    mismatched = package_report(host, installed(host))  # after the hold: held packages report 'hi'
    if mismatched:
        raise Failed('package_versions_differ', package_report(host, installed(host), True))
    return 'installed'


def stage_driver(host, ctx):
    rc, out = host.run(['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader'], 60)
    versions = [v.strip() for v in out.splitlines() if v.strip()]
    marker = host.path(STATE) / 'reboot-for-driver'
    if rc == 0 and versions:
        if set(versions) != {DRIVER_VERSION}:
            raise Failed('driver_version_mismatch')
        if len(versions) != GPU_COUNT:
            raise Failed('gpu_count_mismatch')
        return f'{DRIVER_VERSION} x{GPU_COUNT}'
    boot = host.boot_id()
    if marker.exists():
        if marker.read_text().strip() != boot:
            raise Failed('driver_not_loaded_after_reboot')
        raise Reboot('reboot_pending')
    marker.write_text(boot + '\n')  # at most one reboot: the next boot sees a different boot id
    host.run(['systemctl', '--no-block', 'reboot'], 60)
    raise Reboot('reboot_requested_for_driver')


DAEMON_JSON = '/etc/docker/daemon.json'
HEX64 = __import__('re').compile(r'^sha256:[0-9a-f]{64}$')


def docker_store(host):
    """{'driver', 'snapshotter', 'version'} from three named docker info fields (no full info dump), or None."""
    rc, out = host.run(['docker', 'info', '--format', '{{.Driver}}|{{json .DriverStatus}}|{{.ServerVersion}}'], 60)
    parts = out.strip().split('|') if rc == 0 else []
    if len(parts) != 3:
        return None
    return {'driver': parts[0][:32], 'snapshotter': 'io.containerd.snapshotter' in parts[1], 'version': parts[2][:32]}


def classic(store):
    """Docker's classic overlay2 store, where an image's .Id is its config digest (the pinned image_id). Docker 29
    defaults new installs to the containerd image store, where .Id is the manifest or index digest instead."""
    return bool(store) and store['driver'] == 'overlay2' and not store['snapshotter']


def stage_container(host, ctx):
    def has_runtime():
        rc, out = host.run(['docker', 'info', '--format', '{{json .Runtimes}}'], 60)
        return rc == 0 and '"nvidia"' in out
    changed = False
    if not has_runtime():
        must(host, ['nvidia-ctk', 'runtime', 'configure', '--runtime=docker'], 'nvidia_ctk_failed', 120)
        changed = True
    if not classic(docker_store(host)) or changed:
        daemon = host.path(DAEMON_JSON)
        try:
            config = json.loads(daemon.read_text()) if daemon.exists() else {}
        except ValueError:
            raise Failed('docker_daemon_json_invalid') from None
        if not isinstance(config, dict):
            raise Failed('docker_daemon_json_invalid')
        if config.get('features', {}).get('containerd-snapshotter') is not False:
            config.setdefault('features', {})['containerd-snapshotter'] = False
            daemon.parent.mkdir(parents=True, exist_ok=True)
            tmp = daemon.with_name(daemon.name + '.tmp')
            tmp.write_text(json.dumps(config, indent=2, sort_keys=True) + '\n')
            os.replace(tmp, daemon)
            changed = True
        if changed:
            must(host, ['systemctl', 'restart', 'docker'], 'docker_restart_failed', 300)
    if not has_runtime():
        raise Failed('nvidia_runtime_missing')
    store = docker_store(host)
    if not classic(store):
        raise Failed('docker_image_store_not_classic', [{'store': store}])
    return f"nvidia runtime; {store['driver']} store" + ('; configured' if changed else '')


def image_identity(host, ref):
    """(facts, None) from one bounded docker image inspect, or (None, failure category)."""
    rc, out = host.run(['docker', 'image', 'inspect', '--format',
                        '{{.Id}}|{{json .RepoDigests}}|{{.Os}}|{{.Architecture}}', ref], 60)
    if rc != 0:
        return None, 'image_not_present' if rc == 1 else 'image_inspect_failed'
    parts = out.strip().split('|')
    try:
        if len(parts) != 4 or not HEX64.match(parts[0]):
            raise ValueError
        digests = json.loads(parts[1])
        if not isinstance(digests, list) or not all(isinstance(d, str) for d in digests):
            raise ValueError
    except ValueError:
        return None, 'image_inspect_malformed'
    return {'id': parts[0], 'repoDigests': sorted(d[:160] for d in digests)[:4],
            'platform': f'{parts[2][:16]}/{parts[3][:16]}'}, None


def stage_image(host, ctx):
    runtime = ctx['runtime']
    ref = f"vllm/vllm-openai@{runtime['image_digest']}"

    def verified(facts):
        return (facts is not None and facts['id'] == runtime['image_id'] and ref in facts['repoDigests']
                and facts['platform'] == 'linux/amd64')
    facts, _ = image_identity(host, ref)
    if verified(facts):
        return 'present'
    must(host, ['docker', 'pull', ref], 'image_pull_failed', 3600)
    facts, failure = image_identity(host, ref)
    if verified(facts):
        return 'pulled'
    observed = {'expectedId': runtime['image_id'], 'expectedRepoDigest': ref, 'expectedPlatform': 'linux/amd64',
                'observed': facts, 'store': docker_store(host)}
    raise Failed(failure or 'image_id_mismatch', [observed])


def stage_nginx(host, ctx):
    binary = host.path('/usr/sbin/nginx')
    if not binary.exists() or hashlib.sha256(binary.read_bytes()).hexdigest() != NGINX_SHA256:
        raise Failed('nginx_binary_hash_mismatch')
    if host.run(['systemctl', 'is-enabled', 'nginx.service'], 30)[0] == 0:
        must(host, ['systemctl', 'disable', '--now', 'nginx.service'], 'distro_nginx_disable_failed', 120)
    return 'pinned'


def stage_service(host, ctx):
    tree = ctx['payload'] / 'etc-rose-models'
    changed = False
    for src in sorted(p for p in tree.rglob('*') if p.is_file()):
        rel = src.relative_to(tree).as_posix()
        dst = host.path(ROOT) / rel
        data = src.read_bytes()
        if not dst.exists() or dst.read_bytes() != data:
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + '.tmp')
            tmp.write_bytes(data)
            os.chmod(tmp, 0o755 if rel.startswith('bin/') else 0o644)
            os.replace(tmp, dst)
            changed = True
        if rel.startswith('systemd/'):
            unit = host.path('/etc/systemd/system') / src.name
            if not unit.exists() or unit.read_bytes() != data:
                unit.parent.mkdir(parents=True, exist_ok=True)
                unit.write_bytes(data)
                changed = True
    for d in ('/var/lib/rose-models/acme', '/var/lib/rose-models/auth'):
        host.path(d).mkdir(parents=True, exist_ok=True)
    os.chmod(host.path('/var/lib/rose-models/acme'), 0o755)  # nginx workers read the webroot unprivileged
    if changed:
        must(host, ['systemctl', 'daemon-reload'], 'daemon_reload_failed', 120)
    must(host, ['systemctl', 'enable', '--now', *MODEL_UNITS], 'service_enable_failed', 300)
    health = host.path('/run/rose-models/health.json')
    try:
        model = json.loads(health.read_text()).get('status', 'unknown')
    except (OSError, ValueError):
        model = 'not_reported_yet'
    return f'enabled; model {model}'


def stage_auth(host, ctx):
    roots_src = ctx['payload'] / 'attestation-roots'
    roots = host.path(ROOT) / 'attestation-roots'
    roots.mkdir(parents=True, exist_ok=True)
    for src in sorted(roots_src.glob('*')) if roots_src.is_dir() else []:
        if not (roots / src.name).exists() or (roots / src.name).read_bytes() != src.read_bytes():
            (roots / src.name).write_bytes(src.read_bytes())
    if not host.path('/var/lib/rose-models/auth/secret.json').exists():
        must(host, ['python3', f'{ROOT}/lib/service/auth_service.py', 'secret-init'], 'auth_secret_init_failed', 60)
    if not any(roots.glob('*.pem')) and not any(roots.glob('*.der')):
        raise Waiting('attestation_roots_missing')
    must(host, ['systemctl', 'enable', '--now', 'models-auth.service'], 'auth_enable_failed', 120)
    return 'active'


LE = '/etc/letsencrypt'
ACME_WEBROOT = '/var/lib/rose-models/acme'
ACME_RUN = '/run/rose-acme'
ACME_NGINX = ['/usr/sbin/nginx', '-p', ACME_RUN, '-c', f'{ROOT}/edge/acme-only.conf']
ACME_404 = ('/', '/v1/models', '/v1/chat/completions', '/v1/auth/challenge', '/v1/auth/token')
ACK_KEY = 'attributes/rose-models-acme-ack'  # existing instance metadata; the operator sets it with add-metadata
ACK_TIMEOUT_S, ACK_POLL_S = 1800, 10


def acme_identity(host):
    """(account ids, lineage names for HOSTNAME) under /etc/letsencrypt: what issuance must reuse, never replace."""
    acct_dir = host.path(f'{LE}/accounts/acme-v02.api.letsencrypt.org/directory')
    accounts = sorted(p.name for p in acct_dir.iterdir() if p.is_dir()) if acct_dir.is_dir() else []
    renewal = host.path(f'{LE}/renewal')
    lineages = sorted(p.name[:-len('.conf')] for p in renewal.glob('*.conf')
                      if p.name == f'{HOSTNAME}.conf' or p.name.startswith(f'{HOSTNAME}-')) if renewal.is_dir() else []
    return accounts, lineages


def pid_of(host, run_dir):
    try:
        pid = int(host.path(f'{run_dir}/nginx.pid').read_text().strip())
        cmdline = host.path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode('utf-8', 'replace')
    except (OSError, ValueError):
        return None, ''
    return pid, cmdline


def prepare_acme_http(host):
    """Start the ACME-only Nginx and prove it: the config tests, the pid file names a live process loaded with that
    config, a synthetic challenge returns 200 with its exact content, and every other path returns 404."""
    for sub in ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'):
        host.path(f'{ACME_RUN}/{sub}').mkdir(parents=True, exist_ok=True)
    challenges = host.path(f'{ACME_WEBROOT}/.well-known/acme-challenge')
    challenges.mkdir(parents=True, exist_ok=True)
    for d in (host.path(ACME_WEBROOT), challenges.parent, challenges):
        os.chmod(d, 0o755)  # nginx workers read the webroot unprivileged
    must(host, [*ACME_NGINX, '-t'], 'acme_http_config_invalid', 60)
    pid, cmdline = pid_of(host, ACME_RUN)
    if pid is None:
        must(host, ACME_NGINX, 'acme_http_start_failed', 60)
        pid, cmdline = pid_of(host, ACME_RUN)
    if pid is None or f'{ROOT}/edge/acme-only.conf' not in cmdline:
        raise Failed('acme_http_pid_unverified')
    token, content = 'rose-probe-' + os.urandom(8).hex(), os.urandom(16).hex()
    probe = challenges / token
    probe.write_text(content)
    os.chmod(probe, 0o644)
    try:
        got = host.http_get(f'http://127.0.0.1/.well-known/acme-challenge/{token}', HOSTNAME)
        codes = {'challenge': got[0], **{p: host.http_get(f'http://127.0.0.1{p}', HOSTNAME)[0] for p in ACME_404}}
    finally:
        probe.unlink()
    if got != (200, content.encode()) or any(codes[p] != 404 for p in ACME_404):
        raise Failed('acme_http_not_ready', [codes])
    return codes


def stop_acme_http(host):
    pid, _ = pid_of(host, ACME_RUN)
    if pid is None:
        return
    host.run([*ACME_NGINX, '-s', 'quit'], 60)
    for _ in range(20):  # the edge takes :80 next
        if pid_of(host, ACME_RUN)[0] is None:
            return
        host.sleep(0.5)
    raise Failed('acme_http_stop_failed')


def stage_tls(host, ctx):
    current = host.path(ROOT) / 'tls/current/fullchain.pem'
    if current.exists():
        return 'certificate present'
    accounts, lineages = acme_identity(host)
    identity = {'accounts': len(accounts), 'lineages': lineages}
    if len(accounts) > 1:
        raise Failed('acme_account_ambiguous', [identity])
    if len(lineages) > 1 or lineages not in ([], [HOSTNAME]):
        raise Failed('certificate_lineage_ambiguous', [identity])
    host.path('/run/rose-edge').mkdir(parents=True, exist_ok=True)
    deploy = f'{ROOT}/bin/tls-deploy'
    if lineages:  # issued earlier but never installed: install that exact certificate and key, never reissue
        live = host.path(f'{LE}/live/{HOSTNAME}')
        if not (live / 'fullchain.pem').exists() or not (live / 'privkey.pem').exists():
            raise Failed('certificate_lineage_incomplete', [identity])
        if host.run([deploy], 120, {'RENEWED_LINEAGE': f'{LE}/live/{HOSTNAME}'})[0] != 0 or not current.exists():
            raise Failed('certificate_install_failed', [identity])
        return 'installed existing certificate'
    if (host.metadata('attributes/rose-models-acme') or '').strip() != ACME_FLAG:
        raise Waiting('acme_not_authorized')
    external = (host.metadata('network-interfaces/0/access-configs/0/external-ip') or '').strip()
    if not external or host.resolve(HOSTNAME) != [external]:
        raise Waiting('dns_not_pointing_here')
    boot = host.boot_id()
    attempted = host.path(STATE) / f'acme-issue-attempted-{boot}'
    if attempted.exists():  # at most one issuance per acknowledged boot; a retry needs a new reset and acknowledgement
        raise Failed('acme_issuance_already_attempted_this_boot', [identity])
    try:
        codes = prepare_acme_http(host)
        expected_ack = f"{ctx['payloadSha256']}:{boot}"
        conf = host.path(f'{ROOT}/edge/acme-only.conf')
        proof = {'schema': 'rose-models-acme-proof/1', 'payloadSha256': ctx['payloadSha256'], 'bootId': boot,
                 'config': f'{ROOT}/edge/acme-only.conf', 'configSha256': hashlib.sha256(conf.read_bytes()).hexdigest(),
                 'pidVerified': True, 'acmeHttp': codes, 'accounts': len(accounts), 'lineages': lineages,
                 'awaiting': 'metadata rose-models-acme-ack=' + expected_ack, 'timeoutSeconds': ACK_TIMEOUT_S}
        line = json.dumps(proof, sort_keys=True)
        host.put_guest_attribute('acme-proof', line)  # published BEFORE any issuance
        host.publish('ROSE-MODELS-ACME-PROOF ' + line)
        deadline, seen = host.clock() + ACK_TIMEOUT_S, 'absent'
        while True:
            ack = (host.metadata(ACK_KEY) or '').strip()
            if ack == expected_ack:
                break
            seen = 'mismatch' if ack else 'absent'  # a stale boot or payload never issues
            if host.clock() >= deadline:
                raise Failed('acme_ack_timeout', [{'expectedAck': expected_ack, 'seen': seen, 'acmeHttp': codes}])
            host.sleep(ACK_POLL_S)
        attempted.write_text(expected_ack + '\n')
        argv = ['certbot', 'certonly', '--webroot', '-w', ACME_WEBROOT, '-n', '-d', HOSTNAME, '--deploy-hook', deploy]
        # Exactly one existing account is reused; none means the approved first registration (no email).
        argv += ['--account', accounts[0]] if accounts else ['--agree-tos', '--register-unsafely-without-email']
        if host.run(argv, 600)[0] != 0:
            after, lineages_after = acme_identity(host)
            raise Failed('certificate_issuance_failed',
                         [{'accounts': len(after), 'lineages': lineages_after, 'acmeHttp': codes}])
    finally:
        stop_acme_http(host)
    if not current.exists():
        raise Failed('certificate_not_installed')
    return 'issued (account ' + ('reused' if accounts else 'registered') + ')'


def stage_edge(host, ctx):
    if ctx['results'].get('tls', ('',))[0] != 'ok' or ctx['results'].get('auth', ('',))[0] != 'ok':
        raise Waiting('needs_tls_and_auth')
    must(host, ['systemctl', 'enable', '--now', 'models-edge.service'], 'edge_enable_failed', 120)
    renewal = host.path(f'{LE}/renewal/{HOSTNAME}.conf')
    if renewal.exists() and 'authenticator = standalone' in renewal.read_text():
        # A lineage issued standalone cannot renew while the edge holds :80; renew through the edge's webroot.
        must(host, ['certbot', 'reconfigure', '-n', '--cert-name', HOSTNAME, '--webroot', '-w', ACME_WEBROOT],
             'renewal_reconfigure_failed', 300)
    return 'active'


STAGES = (('disk', stage_disk), ('packages', stage_packages), ('driver', stage_driver),
          ('container', stage_container), ('image', stage_image), ('nginx', stage_nginx),
          ('service', stage_service), ('auth', stage_auth), ('tls', stage_tls), ('edge', stage_edge))
INDEPENDENT = {'tls'}  # may still run while auth waits


def run(host, payload, payload_sha256, now=time.time):
    state = host.path(STATE)
    state.mkdir(parents=True, exist_ok=True)
    lock = os.open(state / '.lock', os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock)
        return {'result': 'already_running'}, 0
    try:
        config = json.loads((payload / 'etc-rose-models/lib/muse-glimmer-30b.json').read_text())
        runtime = next(o for o in config['options'] if o['id'] == 'a100x2-bf16-vllm-persistent')['runtime']
        ctx = {'payload': payload, 'runtime': runtime, 'results': {}, 'payloadSha256': payload_sha256}
        stopped = None
        for name, fn in STAGES:
            if stopped and not (stopped == 'waiting' and name in INDEPENDENT | {'edge'}):
                ctx['results'][name] = ('skipped', stopped, 0.0)
                continue
            started = now()
            try:
                ctx['results'][name] = ('ok', fn(host, ctx))
            except Waiting as w:
                ctx['results'][name] = ('waiting', str(w))
                stopped = stopped or 'waiting'
            except Reboot as r:
                ctx['results'][name] = ('reboot', str(r))
                stopped = 'reboot'
            except Failed as f:
                ctx['results'][name] = ('failed', str(f))
                if f.observed:
                    ctx.setdefault('observed', {})[name] = f.observed
                stopped = 'failed'
            except Exception as e:  # unexpected: category only, never a message that might hold paths or output
                ctx['results'][name] = ('failed', f'internal_{type(e).__name__}')
                stopped = 'failed'
            ctx['results'][name] += (round(now() - started, 1),)
        states = {r[0] for r in ctx['results'].values()}
        result = ('failed' if 'failed' in states else 'rebooting' if 'reboot' in states
                  else 'waiting' if states - {'ok'} else 'ready')
        status = {'schema': 'rose-models-bootstrap/1', 'payloadSha256': payload_sha256, 'result': result,
                  'at': int(now()), 'stages': {k: {'state': v[0], 'detail': v[1], 'seconds': v[2],
                                                   **({'observed': ctx['observed'][k]} if k in ctx.get('observed', {})
                                                      else {})}
                                               for k, v in ctx['results'].items()}}
        line = json.dumps(status, sort_keys=True)
        (state / 'status.json').write_text(line + '\n')
        host.put_guest_attribute('bootstrap', line)
        return status, 1 if result == 'failed' else 0
    finally:
        os.close(lock)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('run',))
    p.add_argument('--payload', type=Path, required=True)
    p.add_argument('--payload-sha256', required=True)
    a = p.parse_args(argv)
    status, code = run(Host(), a.payload, a.payload_sha256)
    print('ROSE-MODELS-BOOTSTRAP ' + json.dumps(status, sort_keys=True), flush=True)
    return code


if __name__ == '__main__':
    sys.exit(main())
