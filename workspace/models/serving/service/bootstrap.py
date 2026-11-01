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
  diagnostic only with metadata rose-models-diagnostic=offline-validate|gpu-start-once|hf-probe-once|
             cloud-preflight-once|cloud-stage-once|cloud-redirect-probe-once:<payload sha256>:<id>, once per id (see
             gpu_start_once, hf_probe_once, cloud_preflight_once, cloud_stage_once, cloud_redirect_probe_once), and then
             INSTEAD of every stage above and below: nothing is installed, pulled, reconfigured or rebooted. The model
             is held (MODEL_HOLD), stopped and its container positively absent; read-only prerequisites (docker, runc,
             the exact local image) or a refusal; one offline CLI check of the exact serve command (--pull=never,
             runc, no network, GPU or mounts, 120 s); verified removal; fixed categories published as
             rose-models/diagnostic. The model stays held for this boot only
  auth       secret-init once; the auth service only once attestation roots are present
  tls        an installed certificate; else an issued-but-uninstalled lineage is installed (never reissued); else, with
             rose-models-acme set and DNS here, an ACME-only Nginx is started and proven (challenge 200, all else 404);
             that proof is published, then the stage waits (bounded) for metadata rose-models-acme-ack equal to
             <payload sha256>:<boot id>. Only then does certbot issue once via its webroot, reusing the single existing
             account (ambiguity fails). A timeout issues nothing. The ACME-only Nginx is stopped either way
  edge       the Nginx edge once TLS and auth are up; a standalone-issued lineage is switched to the webroot;
             certbot.timer and the renewal-evidence timer are enabled and the evidence is published once

Every command has a time bound. Output: one nonsecret JSON line (stdout, so it reaches the serial console), the status
file, and the guest attribute rose-models/bootstrap. Exit 0 when ready, waiting or rebooting; exit 1 when a stage failed.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import signal
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
RENEWAL_UNITS = ('certbot.timer', 'models-renewal-status.timer')  # renewal, and its published evidence
# Diagnostic branch (explicit, one attempt per id): metadata rose-models-diagnostic=<mode>:<payload sha256>:<id>
DIAG_KEY = 'attributes/rose-models-diagnostic'
DIAG_VALUE = re.compile(r'(offline-validate|gpu-start-once|hf-probe-once|cloud-preflight-once|cloud-stage-once|'
                        r'cloud-redirect-probe-once):([0-9a-f]{64}):([a-z0-9][a-z0-9-]{0,31})')
# gpu-start-once: one production GPU start, at most 900 s, stopped as soon as the server listens; inference stays held
GPU_SECONDS, GPU_PROBE_S, MODEL_PORT, DATA = 900, 5, 8000, '/mnt/models-data'
# hf-probe-once: lib/service/hf_probe.py in the pinned image, offline, no GPU, cache read-only, at most 120 s
HF_PROBE_SECONDS, HF_PROBE_CONTAINER, HF_PROBE_LINE = 120, 'muse-glimmer-hfprobe', 4096
# cloud-preflight-once: lib/rose_cloud/preflight.py in the pinned image, offline, no GPU, data disk read-only, 120 s
CLOUD_SECONDS, CLOUD_CONTAINER, CLOUD_RECEIPT = 120, 'rose-cloud-preflight', 4096
# cloud-stage-once (runs only when a separate runtime decision sets it): lib/rose_cloud/stage.py fetches the manifest
# into STAGING (only that directory mounted, read-write), allowlisted https only, at most STAGE_SECONDS and
# STAGE_MAX_BYTES inbound; the manifest must be the exact pinned one.
STAGING, STAGE_CONTAINER = '/mnt/models-data/rose-cloud', 'rose-cloud-stage'
STAGE_SECONDS, STAGE_MAX_BYTES, STAGE_GRACE_S = 3600, 45_000_000_000, 120
STAGE_MANIFEST_SHA256 = 'ebcfb082a94bcae9c950eefab4b34a8fc736bfeb934c84a96360398afd09f3c3'
# cloud-redirect-probe-once (CLOUD-REDIRECT-HEAD-001; runs only when a separate runtime decision sets it): the same
# stage.py in HEAD-only mode names the refused redirect destination without staging. One HEAD per pinned manifest file,
# the same allowlist and redirect checks; no body read, nothing mounted, read-only root filesystem. The container is
# killed at PROBE_SECONDS (the diagnostic cap, no grace); the helper stops itself earlier, at PROBE_HELPER_S.
PROBE_CONTAINER, PROBE_SECONDS, PROBE_HELPER_S, PROBE_READ_TIMEOUT_S = 'rose-cloud-redirect-probe', 120, 100, 10
MODEL_HOLD = '/run/rose-models/model-hold'  # muse-glimmer.service's ExecCondition: no model start while it exists
DIAG_CONTAINER, DIAG_SECONDS, DIAG_BYTES, DIAG_STOP_S = 'muse-glimmer-validate', 120, 2 << 20, 120
MODEL_UNITS = ('muse-glimmer.service', 'models-health.timer', 'models-verify-full.timer',
               'models-attestation-status.timer')
METADATA = 'http://metadata.google.internal/computeMetadata/v1/instance/'
CAPTURE_BYTES = 1 << 20  # stdout kept per command, enforced while reading
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
        """(exit code, stdout) of one bounded command. stdout is read while the command runs; past CAPTURE_BYTES it is
        drained and discarded, never buffered. stderr goes to /dev/null, and reading stops once the command has exited,
        so a daemon it leaves holding a pipe cannot hold the call (the 60 s ACME start failure). Deadline: killed, 124.
        Not startable: 127."""
        try:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     env=None if env is None else {**os.environ, **env})
        except OSError:
            return 127, ''
        out, deadline, code, exited = bytearray(), time.monotonic() + timeout, None, False
        try:
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    code = 124
                    break
                if select.select([child.stdout], [], [], 0 if exited else min(left, 0.2))[0]:
                    chunk = os.read(child.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    out += chunk[:max(0, CAPTURE_BYTES - len(out))]
                elif exited:
                    break  # its output is drained; anything still holding stdout is not this command
                else:
                    exited = child.poll() is not None
            if code is None:
                code = child.wait(max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            code = 124
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            child.stdout.close()
        return code, out.decode('utf-8', 'replace')

    def start_daemon(self, argv, timeout=30):
        """Start a self-daemonizing server with every stdio on /dev/null. The foreground parent must exit 0 within
        `timeout`. Returns None, or 'exit', 'signal', 'deadline' or 'not_started'. No output is captured."""
        try:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            return 'not_started'
        try:
            rc = child.wait(timeout)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
            return 'deadline'
        return None if rc == 0 else 'signal' if rc < 0 else 'exit'

    def validate(self, argv, max_seconds, capture, probe=None, probe_every=GPU_PROBE_S):
        """Run one bounded check and inspect its merged output with `capture` (startup_guard.Capture: fixed categories,
        bounded bytes and time). Output is never stored or forwarded. At the deadline, or as soon as `probe()` is true
        (checked every `probe_every` s), the process group is killed. Returns (exit code or None when killed, elapsed
        seconds)."""
        started = last_probe = time.monotonic()
        try:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        except OSError:
            return 127, 0
        deadline, fd, code = started + max_seconds, child.stdout.fileno(), None
        try:
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                if probe and time.monotonic() - last_probe >= probe_every:
                    last_probe = time.monotonic()
                    if probe():
                        break
                if select.select([fd], [], [], min(left, 0.5))[0]:
                    chunk = os.read(fd, 1 << 16)
                    if not chunk:
                        code = child.wait(max(0.1, deadline - time.monotonic()))
                        break
                    capture.feed(chunk)
                else:
                    capture.feed(b'')
        except subprocess.TimeoutExpired:
            pass
        finally:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except OSError:
                    pass
                child.wait()
            child.stdout.close()
        return code, round(time.monotonic() - started, 1)

    def listening(self, port):
        """Whether something accepts connections on 127.0.0.1:port."""
        try:
            socket.create_connection(('127.0.0.1', port), timeout=1).close()
            return True
        except OSError:
            return False

    def kill(self, pid, sig):
        os.kill(pid, sig)

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
    if host.path(MODEL_HOLD).exists():  # diagnostic branch: enable for later boots, start nothing that runs the model
        must(host, ['systemctl', 'enable', *MODEL_UNITS], 'service_enable_failed', 300)
        must(host, ['systemctl', 'start', 'models-verify-full.timer', 'models-attestation-status.timer'],
             'service_enable_failed', 300)
    else:
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
ACME_PORT, ACME_START_S = 80, 15
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


def running(host, pid):
    """The process exists and has not exited (an exited daemon stays a zombie until its new parent reaps it)."""
    try:
        return host.path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except (OSError, IndexError):
        return False


def pid_of(host, run_dir):
    """The running pid named by the task's pid file, or None (missing, malformed or exited)."""
    try:
        pid = int(host.path(f'{run_dir}/nginx.pid').read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 and running(host, pid) else None


def listener_inodes(host, port):
    """Socket inodes listening on TCP `port` (IPv4 or IPv6)."""
    inodes = set()
    for table in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            lines = host.path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if len(f) > 9 and f[3] == '0A' and int(f[1].rsplit(':', 1)[1], 16) == port:
                inodes.add(f[9])
    return inodes


def owned(host, pid, port=None):
    """What ties `pid` to this task. exe: it runs the pinned nginx binary. config: its command line (the master keeps
    its argv in the process title) loads ACME_NGINX's config. listener: it holds the socket listening on `port`."""
    try:
        exe = os.readlink(host.path(f'/proc/{pid}/exe')) == str(host.path(ACME_NGINX[0]).resolve())
    except OSError:
        exe = False
    try:
        args = host.path(f'/proc/{pid}/cmdline').read_bytes()[:4096].decode('utf-8', 'replace').replace('\0', ' ')
    except OSError:
        args = ''
    config = f' -c {ACME_NGINX[-1]} ' in args + ' '
    listener = False
    if port is not None:
        inodes = listener_inodes(host, port)
        try:
            fds = list(host.path(f'/proc/{pid}/fd').iterdir())
        except OSError:
            fds = []
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith('socket:[') and target[8:-1] in inodes:
                listener = True
                break
    return {'exe': exe, 'config': config, 'listener': listener}


def prepare_acme_http(host):
    """Start the ACME-only Nginx and prove it before anything else:
    - `nginx -t` passes;
    - the task pid file names a live process running the pinned nginx binary, loaded with that config, holding the
      :ACME_PORT listener;
    - a synthetic challenge returns 200 with its exact content, and every other path returns 404.
    An ACME-only Nginx left by an earlier attempt is stopped and started afresh, so the loaded config is this file.
    A stale pid file is removed; a live foreign pid or another listener on the port is refused, never killed."""
    for sub in ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'):
        host.path(f'{ACME_RUN}/{sub}').mkdir(parents=True, exist_ok=True)
    challenges = host.path(f'{ACME_WEBROOT}/.well-known/acme-challenge')
    challenges.mkdir(parents=True, exist_ok=True)
    for d in (host.path(ACME_WEBROOT), challenges.parent, challenges):
        os.chmod(d, 0o755)  # nginx workers read the webroot unprivileged
    must(host, [*ACME_NGINX, '-t'], 'acme_http_config_invalid', 60)
    pid = pid_of(host, ACME_RUN)
    if pid is not None and not owned(host, pid)['exe']:
        raise Failed('acme_http_pid_foreign', [{'reason': 'pid file names a live non-nginx process'}])
    stop_acme_http(host)
    pidfile = host.path(f'{ACME_RUN}/nginx.pid')
    if pidfile.exists():
        pidfile.unlink()  # stale: its process is gone
    if listener_inodes(host, ACME_PORT):
        raise Failed('acme_http_port_occupied', [{'port': ACME_PORT}])
    reason = host.start_daemon(ACME_NGINX, ACME_START_S)
    if reason:
        raise Failed('acme_http_start_failed', [{'reason': reason}])
    pid, check = None, {'exe': False, 'config': False, 'listener': False}
    for _ in range(int(ACME_START_S / 0.2)):  # the daemon writes its pid file just after the parent exits
        pid = pid_of(host, ACME_RUN)
        if pid is not None:
            check = owned(host, pid, ACME_PORT)
            if all(check.values()):
                break
        host.sleep(0.2)
    if not all(check.values()):
        raise Failed('acme_http_pid_unverified', [{'pid': pid is not None, **check}])
    token, content = 'rose-probe-' + os.urandom(8).hex(), os.urandom(16).hex()
    probe = challenges / token
    probe.write_text(content)
    os.chmod(probe, 0o644)
    base = f'http://127.0.0.1:{ACME_PORT}'
    try:
        got = host.http_get(f'{base}/.well-known/acme-challenge/{token}', HOSTNAME)
        codes = {'challenge': got[0], **{p: host.http_get(f'{base}{p}', HOSTNAME)[0] for p in ACME_404}}
    finally:
        probe.unlink()
    if got != (200, content.encode()) or any(codes[p] != 404 for p in ACME_404):
        raise Failed('acme_http_not_ready', [codes])
    return {'pid': check, 'acmeHttp': codes}


def stop_acme_http(host):
    """Stop only the task's own ACME-only Nginx (pinned binary, task pid file). Bounded: quit, then TERM. Returns once
    the process has exited: nginx removes its pid file before it closes its listener, and the edge takes :80 next."""
    pid = pid_of(host, ACME_RUN)
    if pid is None or not owned(host, pid)['exe']:
        return
    host.run([*ACME_NGINX, '-s', 'quit'], 30)
    for attempt in range(40):
        if not running(host, pid):
            return
        if attempt == 20:
            try:
                host.kill(pid, signal.SIGTERM)
            except OSError:
                pass  # already gone
        host.sleep(0.25)
    raise Failed('acme_http_stop_failed')


DEPLOY_FACTS = ('result', 'stage', 'reason', 'current', 'notAfter')


def install_lineage(host, identity):
    """Install the retained lineage's exact certificate and key with tls-deploy (check, switch, nginx -t). Never
    contacts the CA. A failure reports tls-deploy's nonsecret JSON result and exit code."""
    live = host.path(f'{LE}/live/{HOSTNAME}')
    if not (live / 'fullchain.pem').exists() or not (live / 'privkey.pem').exists():
        raise Failed('certificate_lineage_incomplete', [identity])
    rc, out = host.run([f'{ROOT}/bin/tls-deploy'], 120, {'RENEWED_LINEAGE': f'{LE}/live/{HOSTNAME}'})
    if rc != 0 or not host.path(f'{ROOT}/tls/current/fullchain.pem').exists():
        try:
            result = json.loads(out.strip().splitlines()[-1])
            facts = {k: str(result[k])[:200] for k in DEPLOY_FACTS if k in result}
        except (ValueError, IndexError, AttributeError):
            facts = {}
        raise Failed('certificate_install_failed', [{**identity, 'deployExit': rc, 'deploy': facts}])


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
        install_lineage(host, identity)
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
        ready = prepare_acme_http(host)
        codes = ready['acmeHttp']
        expected_ack = f"{ctx['payloadSha256']}:{boot}"
        conf = host.path(f'{ROOT}/edge/acme-only.conf')
        proof = {'schema': 'rose-models-acme-proof/1', 'payloadSha256': ctx['payloadSha256'], 'bootId': boot,
                 'config': f'{ROOT}/edge/acme-only.conf', 'configSha256': hashlib.sha256(conf.read_bytes()).hexdigest(),
                 'pidVerified': ready['pid'], 'acmeHttp': codes, 'accounts': len(accounts), 'lineages': lineages,
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
    how = 'issued (account ' + ('reused' if accounts else 'registered') + ')'
    if current.exists():
        return how
    # certbot exits 0 even when its deploy hook fails: install the lineage it just kept, never reissue.
    after, lineages_after = acme_identity(host)
    if lineages_after != [HOSTNAME]:
        raise Failed('certificate_not_installed', [{'accounts': len(after), 'lineages': lineages_after}])
    install_lineage(host, {'accounts': len(after), 'lineages': lineages_after})
    return how + ', installed after the deploy hook failed'


def stage_edge(host, ctx):
    if ctx['results'].get('tls', ('',))[0] != 'ok' or ctx['results'].get('auth', ('',))[0] != 'ok':
        raise Waiting('needs_tls_and_auth')
    must(host, ['systemctl', 'enable', '--now', 'models-edge.service'], 'edge_enable_failed', 120)
    renewal = host.path(f'{LE}/renewal/{HOSTNAME}.conf')
    if renewal.exists() and 'authenticator = standalone' in renewal.read_text():
        # A lineage issued standalone cannot renew while the edge holds :80; renew through the edge's webroot.
        must(host, ['certbot', 'reconfigure', '-n', '--cert-name', HOSTNAME, '--webroot', '-w', ACME_WEBROOT],
             'renewal_reconfigure_failed', 300)
    must(host, ['systemctl', 'enable', '--now', *RENEWAL_UNITS], 'renewal_timer_enable_failed', 120)
    host.run(['systemctl', 'start', '--no-block', 'models-renewal-status.service'], 30)  # evidence now, not in 5 min
    return 'active'


def diagnostic_request(host, ctx):
    """None, ('requested', id) or ('ignored', reason). The value must name this exact payload; each id runs once."""
    raw = (host.metadata(DIAG_KEY) or '').strip()
    if not raw:
        return None
    m = DIAG_VALUE.fullmatch(raw)
    if not m:
        return ('ignored', 'malformed')
    if m.group(2) != ctx['payloadSha256']:
        return ('ignored', 'other_payload')
    if (host.path(STATE) / f'diagnostic-{m.group(3)}').exists():
        return ('ignored', 'attempt_already_used')
    return ('requested', m.group(3), m.group(1))


def container_names(host, name):
    """Containers named exactly `name` (running or not), or None when docker did not answer (error, timeout, access):
    absence must be positive."""
    rc, out = host.run(['docker', 'ps', '--all', '--filter', f'name={name}', '--format', '{{.Names}}'], 30)
    return None if rc != 0 else [n for n in out.split() if n == name]


def diagnostic_prerequisites(host, image):
    """Read-only: None, or the category that refuses the check. Nothing is pulled, installed or reconfigured."""
    rc, out = host.run(['docker', 'info', '--format', '{{json .Runtimes}}'], 60)
    if rc != 0:
        return 'docker_unavailable'
    try:
        if 'runc' not in json.loads(out):
            return 'runc_unavailable'
    except (ValueError, TypeError):
        return 'runc_unavailable'
    rc, out = host.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], 60)
    if rc != 0:
        return 'image_missing'
    return None if out.strip() == image else 'image_mismatch'


def stage_diagnostic(host, ctx):
    """The offline CLI check of the exact serve command: the pinned image already in the local store (--pull=never),
    `vllm serve` with the deployed arguments under runc, no network, no GPU, no mounts, HF_HUB_OFFLINE=1, at most 120 s
    and 2 MiB inspected. A diagnostic boot runs only this stage: missing prerequisites are refused, never provisioned.
    It runs only after the model service is stopped and its container is positively absent, and the model stays held
    for the rest of this boot. Its container's removal is verified. It proves only where the CLI stopped (fixed
    categories), not that serving works."""
    request = ctx.get('diagnostic')
    if request is None:
        return 'not requested'
    if request[0] == 'ignored':
        return f'ignored: {request[1]}'
    attempt = request[1]
    (host.path(STATE) / f'diagnostic-{attempt}').write_text(ctx['payloadSha256'] + '\n')  # used even if this fails
    must(host, ['systemctl', 'stop', 'models-health.timer', 'models-health.service', 'muse-glimmer.service'],
         'diagnostic_stop_failed', 180)
    deadline = host.clock() + DIAG_STOP_S
    while True:
        _, out = host.run(['systemctl', 'is-active', 'muse-glimmer.service'], 30)
        stopped = out.strip() in ('inactive', 'failed')
        names = container_names(host, 'muse-glimmer')
        if names is None:
            raise Failed('diagnostic_container_state_unknown')
        if stopped and not names:
            break
        if host.clock() >= deadline:
            raise Failed('diagnostic_model_not_stopped', [{'stopped': stopped, 'containerGone': not names}])
        host.sleep(2)
    image = ctx['runtime']['image_id']
    if request[2] == 'gpu-start-once':
        return gpu_start_once(host, ctx, attempt, image)
    if request[2] == 'hf-probe-once':
        return hf_probe_once(host, ctx, attempt, image)
    if request[2] == 'cloud-preflight-once':
        return cloud_preflight_once(host, ctx, attempt, image)
    if request[2] == 'cloud-stage-once':
        return cloud_stage_once(host, ctx, attempt, image)
    if request[2] == 'cloud-redirect-probe-once':
        return cloud_redirect_probe_once(host, ctx, attempt, image)
    result = {'schema': 'rose-models-diagnostic/1', 'payloadSha256': ctx['payloadSha256'], 'bootId': host.boot_id(),
              'attemptId': attempt, 'check': 'vllm-serve-cli-offline', 'modelHeld': True}
    refused = diagnostic_prerequisites(host, image)
    if refused:
        result.update(prerequisite=refused, imagePresent=refused not in ('image_missing', 'image_mismatch'),
                      exitCode=None, elapsedS=0, firstCategory=None, categories=[], bytesInspected=0,
                      limitReached=None, cleanup=None)
    else:
        guard = load_guard(ctx['payload'])
        capture = guard.Capture(DIAG_BYTES, DIAG_SECONDS)
        argv = ['docker', 'run', '--rm', '--pull=never', '--runtime=runc', '--network', 'none', '--name', DIAG_CONTAINER,
                '-e', 'HF_HUB_OFFLINE=1', '--entrypoint', 'vllm', image, 'serve', *ctx['serveArgs']]
        try:
            code, elapsed = host.validate(argv, DIAG_SECONDS, capture)
        finally:
            removed = host.run(['docker', 'rm', '-f', DIAG_CONTAINER], 30)[0]  # a killed CLI leaves its container
            left = container_names(host, DIAG_CONTAINER)
        cleanup = 'unknown' if left is None else 'failed' if left else 'removed'
        result.update(prerequisite=None, imagePresent=True, exitCode=code, elapsedS=elapsed, cleanup=cleanup,
                      cleanupRemoveExit=removed, **capture.summary())
    line = json.dumps(result, sort_keys=True)
    host.put_guest_attribute('diagnostic', line)
    host.publish('ROSE-MODELS-DIAGNOSTIC ' + line)
    if refused:
        raise Failed('diagnostic_prerequisite_missing', [{'prerequisite': refused}])
    if result['cleanup'] != 'removed':
        raise Failed('diagnostic_cleanup_failed', [{'cleanup': result['cleanup']}])
    return f"model held; offline CLI check {result['firstCategory'] or 'no_category'} (exit {result['exitCode']})"


def launch_argv(payload, branch):
    """The exact `docker run` argv of the payload's launch-<branch> script, with its inlined guard source, or None
    when the script does not have the rendered shape."""
    try:
        text = (Path(payload) / f'etc-rose-models/bin/launch-{branch}').read_text()
        guard = text.split("GUARD=\"$(cat <<'GUARD_SOURCE_END'\n", 1)[1].split('GUARD_SOURCE_END\n)"\n', 1)[0]
        argv = shlex.split(text.rsplit('\nexec docker run ', 1)[1])
    except (OSError, IndexError, ValueError):
        return None
    if argv.count('$GUARD') != 1 or '--rm' not in argv or '--name' not in argv:
        return None
    argv[argv.index('$GUARD')] = guard.rstrip('\n')  # what bash's "$(cat <<...)" yields
    return ['docker', 'run', '--pull=never', *argv]


def gpu_start_once(host, ctx, attempt, image):
    """One production GPU start of the exact payload launch command, chosen by verify_model as start-model chooses it
    (0 offline, 3 online first load over the production Hugging Face network and cache; anything else refused). At
    most GPU_SECONDS, DIAG_BYTES inspected by Capture (fixed categories only), stopped as soon as the server listens on
    127.0.0.1:MODEL_PORT. The container is always removed and its absence verified; inference stays held."""
    result = {'schema': 'rose-models-diagnostic/1', 'payloadSha256': ctx['payloadSha256'], 'bootId': host.boot_id(),
              'attemptId': attempt, 'check': 'gpu-start-once', 'modelHeld': True, 'branch': None, 'verifyExit': None,
              'exitCode': None, 'elapsedS': 0, 'listenedAfterS': None, 'stoppedOn': None, 'firstCategory': None,
              'categories': [], 'bytesInspected': 0, 'limitReached': None, 'cleanup': None}
    refused = diagnostic_prerequisites(host, image)
    if not refused and host.run(['mountpoint', '-q', DATA], 30)[0] != 0:
        refused = 'data_not_mounted'
    argv = None
    if not refused:
        verify = Path(ctx['payload']) / 'etc-rose-models/lib/service/verify_model.py'
        rc = host.run(['python3', str(verify), 'check', '--cache', f'{DATA}/hf', '--receipt', f'{DATA}/receipt.json'],
                      300)[0]
        result['verifyExit'] = rc
        result['branch'] = {0: 'offline', 3: 'online'}.get(rc)
        if result['branch'] is None:
            refused = 'verify_refused'
        elif host.listening(MODEL_PORT):
            refused = 'port_in_use'
        else:
            argv = launch_argv(ctx['payload'], result['branch'])
            refused = None if argv else 'launch_unreadable'
    result['prerequisite'] = refused
    if not refused:
        capture = load_guard(ctx['payload']).Capture(DIAG_BYTES, GPU_SECONDS)
        heard = {}
        def probe():
            heard['listened'] = host.listening(MODEL_PORT)
            return heard['listened']
        try:
            code, elapsed = host.validate(argv, GPU_SECONDS, capture, probe=probe)
        finally:
            removed = host.run(['docker', 'rm', '-f', 'muse-glimmer'], 30)[0]
            left = container_names(host, 'muse-glimmer')
        stopped = 'listen' if heard.get('listened') else 'deadline' if code is None else None
        result.update(exitCode=code, elapsedS=elapsed, stoppedOn=stopped,
                      listenedAfterS=elapsed if stopped == 'listen' else None,
                      cleanup='unknown' if left is None else 'failed' if left else 'removed',
                      cleanupRemoveExit=removed, **capture.summary())
    line = json.dumps(result, sort_keys=True)
    host.put_guest_attribute('diagnostic', line)
    host.publish('ROSE-MODELS-DIAGNOSTIC ' + line)
    if refused:
        raise Failed('diagnostic_prerequisite_missing', [{'prerequisite': refused}])
    if result['cleanup'] != 'removed':
        raise Failed('diagnostic_cleanup_failed', [{'cleanup': result['cleanup']}])
    return (f"inference held; GPU start {result['stoppedOn'] or 'exited'} after {result['elapsedS']} s "
            f"({result['firstCategory'] or 'no_category'})")


def load_probe(payload):
    spec = importlib.util.spec_from_file_location('hf_probe', Path(payload) / 'etc-rose-models/lib/service/hf_probe.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, spec.origin


def probe_result(probe, out):
    """The probe's fixed tokens from its MARKER line, re-checked against its own allowlists (the line comes from inside
    a container, so nothing in it is trusted), or None when there is no such line."""
    line = next((l for l in reversed(out.splitlines()) if l.startswith(probe.MARKER)), None)
    if line is None or len(line) > HF_PROBE_LINE:
        return None
    try:
        raw = json.loads(line[len(probe.MARKER):])
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    loaders = raw.get('loaders') if isinstance(raw.get('loaders'), dict) else {}
    files = raw.get('files') if isinstance(raw.get('files'), dict) else {}
    version = raw.get('transformersVersion')
    flag = lambda v: v if isinstance(v, bool) else None  # noqa: E731
    result = {'loaders': {n: loaders.get(n) if loaders.get(n) in probe.CATEGORIES else 'other' for n in probe.LOADERS},
              'transformersVersion': version if version is None or (isinstance(version, str)
                                                                    and probe.VERSION.match(version)) else 'other',
              'registeredTransformers': flag(raw.get('registeredTransformers')),
              'registeredVllm': flag(raw.get('registeredVllm')),
              'files': {n: files.get(n) if files.get(n) in probe.FILE_STATES else 'unknown'
                        for n in probe.FILES}}
    failed = [n for n in probe.LOADERS if result['loaders'][n] != 'ok']
    result.update(firstFailure=failed[0] if failed else None, allOk=not failed)
    return result


def hf_probe_once(host, ctx, attempt, image):
    """One offline loader probe of the pinned model in the exact cached image: --pull=never, runc, no network, no GPU,
    the Hugging Face cache read-only, HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE, at most HF_PROBE_SECONDS. Four loaders are
    tried independently (hf_probe.py); only fixed tokens are published. The container is always removed and its
    absence verified; inference stays held for the boot. It shows which loader fails, not why."""
    result = {'schema': 'rose-models-diagnostic/1', 'payloadSha256': ctx['payloadSha256'], 'bootId': host.boot_id(),
              'attemptId': attempt, 'check': 'hf-probe-once', 'modelHeld': True, 'exitCode': None, 'timedOut': False,
              'probe': None, 'cleanup': None}
    refused = diagnostic_prerequisites(host, image)
    if not refused and host.run(['mountpoint', '-q', DATA], 30)[0] != 0:
        refused = 'data_not_mounted'
    result['prerequisite'] = refused
    if not refused:
        probe, origin = load_probe(ctx['payload'])
        source = Path(origin).read_text()
        model = ctx['model']
        argv = ['docker', 'run', '--rm', '--pull=never', '--runtime=runc', '--network', 'none',
                '--name', HF_PROBE_CONTAINER, '-e', 'HF_HUB_OFFLINE=1', '-e', 'TRANSFORMERS_OFFLINE=1',
                '-v', f'{DATA}/hf:/root/.cache/huggingface:ro', '--entrypoint', 'python3', image,
                '-c', source, model['repo'], model['revision']]
        try:
            code, out = host.run(argv, HF_PROBE_SECONDS)
        finally:
            removed = host.run(['docker', 'rm', '-f', HF_PROBE_CONTAINER], 30)[0]  # a killed CLI leaves it running
            left = container_names(host, HF_PROBE_CONTAINER)
        result.update(exitCode=code if code != 124 else None, timedOut=code == 124,
                      probe=probe_result(probe, out) if code != 124 else None,
                      cleanup='unknown' if left is None else 'failed' if left else 'removed', cleanupRemoveExit=removed)
    line = json.dumps(result, sort_keys=True)
    host.put_guest_attribute('diagnostic', line)
    host.publish('ROSE-MODELS-DIAGNOSTIC ' + line)
    if refused:
        raise Failed('diagnostic_prerequisite_missing', [{'prerequisite': refused}])
    if result['cleanup'] != 'removed':
        raise Failed('diagnostic_cleanup_failed', [{'cleanup': result['cleanup']}])
    if result['probe'] is None:
        return 'inference held; loader probe ' + ('timed out' if result['timedOut'] else 'gave no result')
    return f"inference held; loader probe first failure {result['probe']['firstFailure'] or 'none'}"


def load_preflight(payload):
    lib = Path(payload) / 'etc-rose-models/lib/rose_cloud'
    spec = importlib.util.spec_from_file_location('rose_cloud_preflight', lib / 'preflight.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, Path(spec.origin).read_text(), (lib / 'manifest.json').read_text()


def preflight_result(module, manifest, out):
    """The preflight's fixed tokens from its MARKER line, re-checked by its own allowlist, or None."""
    line = next((l for l in reversed(out.splitlines()) if l.startswith(module.MARKER)), None)
    if line is None or len(line) > CLOUD_RECEIPT:
        return None
    try:
        return module.sanitize(json.loads(line[len(module.MARKER):]), manifest)
    except (ValueError, TypeError, AttributeError, KeyError):
        return None


def cloud_preflight_once(host, ctx, attempt, image):
    """MODELS-ROSE-CLOUD-003: what the exact cached image and the data disk already hold for the proposed Rose cloud
    bring-up (rose_cloud/preflight.py and its pinned manifest). Same hold, stop, positive absence and read-only
    prerequisites as hf-probe-once, plus the image's pinned repo digest and platform; then one container: --pull=never,
    runc, --network none, no GPU, no credentials, /mnt/models-data read-only, at most CLOUD_SECONDS, stdout kept to
    CAPTURE_BYTES. Nothing is loaded, installed or downloaded. The container is always removed and its absence
    verified; the receipt is at most CLOUD_RECEIPT bytes and inference stays held for the boot."""
    result = {'schema': 'rose-models-diagnostic/1', 'payloadSha256': ctx['payloadSha256'], 'bootId': host.boot_id(),
              'attemptId': attempt, 'check': 'cloud-preflight-once', 'modelHeld': True, 'exitCode': None,
              'timedOut': False, 'probe': None, 'cleanup': None}
    refused = diagnostic_prerequisites(host, image)
    if not refused:
        ref = f"vllm/vllm-openai@{ctx['runtime']['image_digest']}"
        facts, _ = image_identity(host, image)
        if facts is None or facts['id'] != image or ref not in facts['repoDigests'] \
                or facts['platform'] != 'linux/amd64':
            refused = 'image_digest_mismatch'
    if not refused and host.run(['mountpoint', '-q', DATA], 30)[0] != 0:
        refused = 'data_not_mounted'
    result['prerequisite'] = refused
    if not refused:
        module, source, manifest_text = load_preflight(ctx['payload'])
        manifest = json.loads(manifest_text)
        argv = ['docker', 'run', '--rm', '--pull=never', '--runtime=runc', '--network', 'none',
                '--name', CLOUD_CONTAINER, '-v', f'{DATA}:{DATA}:ro', '--entrypoint', 'python3', image,
                '-c', source, manifest_text]
        try:
            code, out = host.run(argv, CLOUD_SECONDS)
        finally:
            removed = host.run(['docker', 'rm', '-f', CLOUD_CONTAINER], 30)[0]  # a killed CLI leaves it running
            left = container_names(host, CLOUD_CONTAINER)
        result.update(exitCode=code if code != 124 else None, timedOut=code == 124,
                      probe=preflight_result(module, manifest, out) if code != 124 else None,
                      cleanup='unknown' if left is None else 'failed' if left else 'removed', cleanupRemoveExit=removed)
    line = json.dumps(result, sort_keys=True, separators=(',', ':'))
    if len(line) > CLOUD_RECEIPT:
        result.update(probe=None, receiptTooLarge=True)
        line = json.dumps(result, sort_keys=True, separators=(',', ':'))
    host.put_guest_attribute('diagnostic', line)
    host.publish('ROSE-MODELS-DIAGNOSTIC ' + line)
    if refused:
        raise Failed('diagnostic_prerequisite_missing', [{'prerequisite': refused}])
    if result['cleanup'] != 'removed':
        raise Failed('diagnostic_cleanup_failed', [{'cleanup': result['cleanup']}])
    if result['probe'] is None:
        return 'inference held; cloud preflight ' + ('timed out' if result['timedOut'] else 'gave no result')
    complete = sorted(g for g, ok in result['probe']['filesComplete'].items() if ok)
    return f"inference held; cloud preflight done (complete groups: {', '.join(complete) or 'none'})"


def stage_limits() -> str:
    """The only limits cloud-stage-once passes: no test hooks (hubBase, sources, allowHttp, allowHosts)."""
    return json.dumps({'deadlineS': STAGE_SECONDS, 'maxBytes': STAGE_MAX_BYTES, 'readTimeoutS': 30}, sort_keys=True)


def cloud_stage_once(host, ctx, attempt, image):
    """CLOUD-STAGING-SOURCE-001: fetch and verify the pinned manifest's files into STAGING, once. Same hold,
    stop, positive absence, read-only prerequisites, image digest and data-disk checks as cloud-preflight-once; the
    payload's manifest must hash to STAGE_MANIFEST_SHA256. Then one container: --pull=never, runc, the default bridge
    network (outbound only; the script accepts https to its allowlisted hosts), no GPU, no credentials, all
    capabilities dropped, only STAGING mounted read-write, at most STAGE_SECONDS (+grace) and STAGE_MAX_BYTES.
    Existing exact files are kept, differing ones refuse the run, partials are kept for the next run. The container
    is always removed and its absence verified; the receipt is at most CLOUD_RECEIPT bytes; inference stays held."""
    result = {'schema': 'rose-models-diagnostic/1', 'payloadSha256': ctx['payloadSha256'], 'bootId': host.boot_id(),
              'attemptId': attempt, 'check': 'cloud-stage-once', 'modelHeld': True, 'exitCode': None,
              'timedOut': False, 'stage': None, 'cleanup': None}
    refused = diagnostic_prerequisites(host, image)
    if not refused:
        ref = f"vllm/vllm-openai@{ctx['runtime']['image_digest']}"
        facts, _ = image_identity(host, image)
        if facts is None or facts['id'] != image or ref not in facts['repoDigests'] \
                or facts['platform'] != 'linux/amd64':
            refused = 'image_digest_mismatch'
    if not refused and host.run(['mountpoint', '-q', DATA], 30)[0] != 0:
        refused = 'data_not_mounted'
    lib = Path(ctx['payload']) / 'etc-rose-models/lib/rose_cloud'
    manifest_text = (lib / 'manifest.json').read_text() if not refused else ''
    if not refused and hashlib.sha256(manifest_text.encode()).hexdigest() != STAGE_MANIFEST_SHA256:
        refused = 'manifest_mismatch'
    result['prerequisite'] = refused
    if not refused:
        spec = importlib.util.spec_from_file_location('rose_cloud_stage', lib / 'stage.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source, manifest = (lib / 'stage.py').read_text(), json.loads(manifest_text)
        host.path(STAGING).mkdir(parents=True, exist_ok=True)
        argv = ['docker', 'run', '--rm', '--pull=never', '--runtime=runc', '--network', 'bridge',
                '--name', STAGE_CONTAINER, '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '-v', f'{STAGING}:{STAGING}:rw', '--entrypoint', 'python3', image,
                '-c', source, manifest_text, stage_limits()]
        try:
            code, out = host.run(argv, STAGE_SECONDS + STAGE_GRACE_S)
        finally:
            removed = host.run(['docker', 'rm', '-f', STAGE_CONTAINER], 30)[0]
            left = container_names(host, STAGE_CONTAINER)
        line = next((l for l in reversed(out.splitlines()) if l.startswith(module.MARKER)), None) \
            if code != 124 else None
        try:
            stage = module.sanitize(json.loads(line[len(module.MARKER):]), manifest) \
                if line and len(line) <= CLOUD_RECEIPT else None
        except (ValueError, TypeError, AttributeError):
            stage = None
        result.update(exitCode=code if code != 124 else None, timedOut=code == 124, stage=stage,
                      cleanup='unknown' if left is None else 'failed' if left else 'removed', cleanupRemoveExit=removed)
    line = json.dumps(result, sort_keys=True, separators=(',', ':'))
    if len(line) > CLOUD_RECEIPT:
        result.update(stage=None, receiptTooLarge=True)
        line = json.dumps(result, sort_keys=True, separators=(',', ':'))
    host.put_guest_attribute('diagnostic', line)
    host.publish('ROSE-MODELS-DIAGNOSTIC ' + line)
    if refused:
        raise Failed('diagnostic_prerequisite_missing', [{'prerequisite': refused}])
    if result['cleanup'] != 'removed':
        raise Failed('diagnostic_cleanup_failed', [{'cleanup': result['cleanup']}])
    if result['stage'] is None:
        return 'inference held; cloud staging ' + ('timed out' if result['timedOut'] else 'gave no result')
    return f"inference held; cloud staging stopped: {result['stage']['stop']}"


def probe_limits() -> str:
    """The only limits cloud-redirect-probe-once passes: HEAD mode, no test hooks, no byte budget (no body is read)."""
    return json.dumps({'mode': 'head', 'deadlineS': PROBE_HELPER_S, 'maxBytes': 0,
                       'readTimeoutS': PROBE_READ_TIMEOUT_S}, sort_keys=True)


def cloud_redirect_probe_once(host, ctx, attempt, image):
    """CLOUD-REDIRECT-HEAD-001: name the destination cloud-stage-once refused, without staging. Same hold, stop,
    positive absence, read-only prerequisites, image digest and pinned-manifest checks as cloud-stage-once (no data
    disk is needed). Then one container: --pull=never, runc, the default bridge network (outbound only), no GPU, no
    credentials, all capabilities dropped, a read-only root filesystem and NOTHING mounted, running stage.py with
    probe_limits(): one HEAD per manifest file, redirects checked against the same allowlist, refused destinations
    never requested, no body read. Killed at PROBE_SECONDS. The container is always removed and its absence verified;
    the receipt is at most CLOUD_RECEIPT bytes; inference stays held."""
    result = {'schema': 'rose-models-diagnostic/1', 'payloadSha256': ctx['payloadSha256'], 'bootId': host.boot_id(),
              'attemptId': attempt, 'check': 'cloud-redirect-probe-once', 'modelHeld': True, 'exitCode': None,
              'timedOut': False, 'probe': None, 'cleanup': None}
    refused = diagnostic_prerequisites(host, image)
    if not refused:
        ref = f"vllm/vllm-openai@{ctx['runtime']['image_digest']}"
        facts, _ = image_identity(host, image)
        if facts is None or facts['id'] != image or ref not in facts['repoDigests'] \
                or facts['platform'] != 'linux/amd64':
            refused = 'image_digest_mismatch'
    lib = Path(ctx['payload']) / 'etc-rose-models/lib/rose_cloud'
    manifest_text = (lib / 'manifest.json').read_text() if not refused else ''
    if not refused and hashlib.sha256(manifest_text.encode()).hexdigest() != STAGE_MANIFEST_SHA256:
        refused = 'manifest_mismatch'
    result['prerequisite'] = refused
    if not refused:
        spec = importlib.util.spec_from_file_location('rose_cloud_stage', lib / 'stage.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source, manifest = (lib / 'stage.py').read_text(), json.loads(manifest_text)
        argv = ['docker', 'run', '--rm', '--pull=never', '--runtime=runc', '--network', 'bridge',
                '--name', PROBE_CONTAINER, '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--read-only',
                '--entrypoint', 'python3', image, '-c', source, manifest_text, probe_limits()]
        try:
            code, out = host.run(argv, PROBE_SECONDS)
        finally:
            removed = host.run(['docker', 'rm', '-f', PROBE_CONTAINER], 30)[0]
            left = container_names(host, PROBE_CONTAINER)
        line = next((l for l in reversed(out.splitlines()) if l.startswith(module.HEAD_MARKER)), None) \
            if code != 124 else None
        try:
            probe = module.sanitize_head(json.loads(line[len(module.HEAD_MARKER):]), manifest) \
                if line and len(line) <= CLOUD_RECEIPT else None
        except (ValueError, TypeError, AttributeError):
            probe = None
        result.update(exitCode=code if code != 124 else None, timedOut=code == 124, probe=probe,
                      cleanup='unknown' if left is None else 'failed' if left else 'removed', cleanupRemoveExit=removed)
    line = json.dumps(result, sort_keys=True, separators=(',', ':'))
    if len(line) > CLOUD_RECEIPT:
        result.update(probe=None, receiptTooLarge=True)
        line = json.dumps(result, sort_keys=True, separators=(',', ':'))
    host.put_guest_attribute('diagnostic', line)
    host.publish('ROSE-MODELS-DIAGNOSTIC ' + line)
    if refused:
        raise Failed('diagnostic_prerequisite_missing', [{'prerequisite': refused}])
    if result['cleanup'] != 'removed':
        raise Failed('diagnostic_cleanup_failed', [{'cleanup': result['cleanup']}])
    if result['probe'] is None:
        return 'inference held; redirect probe ' + ('timed out' if result['timedOut'] else 'gave no result')
    return (f"inference held; redirect probe stopped: {result['probe']['stop']} "
            f"({len(result['probe']['refusedHosts'])} refused destination(s) in rose-models/diagnostic)")


def load_guard(payload):
    spec = importlib.util.spec_from_file_location('startup_guard', Path(payload) / 'etc-rose-models/lib/startup_guard.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STAGES = (('disk', stage_disk), ('packages', stage_packages), ('driver', stage_driver),
          ('container', stage_container), ('image', stage_image), ('nginx', stage_nginx),
          ('service', stage_service), ('diagnostic', stage_diagnostic), ('auth', stage_auth), ('tls', stage_tls),
          ('edge', stage_edge))
DIAGNOSTIC_STAGES = (('diagnostic', stage_diagnostic),)  # a diagnostic boot provisions nothing
INDEPENDENT = {'tls'}  # may still run while auth waits


def boot_id_or_unknown(host):
    try:
        return host.boot_id()
    except OSError:
        return 'unknown'


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
        option = next(o for o in config['options'] if o['id'] == 'a100x2-bf16-vllm-persistent')
        ctx = {'payload': payload, 'runtime': option['runtime'], 'serveArgs': option['container_spec']['args'],
               'model': config['model'], 'results': {}, 'payloadSha256': payload_sha256}
        ctx['diagnostic'] = diagnostic_request(host, ctx)
        if ctx['diagnostic'] and ctx['diagnostic'][0] == 'requested':  # before any stage can start the model
            host.path(MODEL_HOLD).parent.mkdir(parents=True, exist_ok=True)
            host.path(MODEL_HOLD).write_text(ctx['diagnostic'][1] + '\n')
        stopped = None
        requested = ctx['diagnostic'] and ctx['diagnostic'][0] == 'requested'
        for name, fn in DIAGNOSTIC_STAGES if requested else STAGES:
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
                  'bootId': boot_id_or_unknown(host), 'at': int(now()), 'stages': {k: {'state': v[0], 'detail': v[1], 'seconds': v[2],
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
