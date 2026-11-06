#!/usr/bin/env python3
"""Yogi direct operator route: an explicit, operator-driven operation on the exact replacement Yogi VM over SSH that is
authenticated against a committed host-key pin. GitHub self-hosted scheduling is not used, so runner eligibility is
not consulted here; that is a different route, not an exemption. The runner route in yogi_ota.py / yogi-ota.yml keeps
every gate it has, organization eligibility included.

Operator machine (never inside a GitHub Actions job):
  plan --stage check|compile --source SHA --operator NAME --out DIR [--inputs ZIP]
  start  --op DIR     write the ownership record (fsync) before START; START only from TERMINATED
  run    --op DIR     verify host, then send the verified bundle and launch the detached agent
  status --op DIR     host-verified read of the agent's status
  cancel --op DIR     host-verified SIGTERM to the agent's process group
  cleanup --op DIR [--abandon]  STOP only with this operation's own record and a terminal (or abandoned) agent
Guest (started by `run` from the transferred, hash-verified source):
  agent --op-dir DIR --manifest-sha SHA

Stages: check (identity, quiesced runner, the existing readiness checks) and compile (bootstrap build A up to an
unsigned compile). Everything that needs a signer credential, a phone or publication is refused here. Nothing is
signed, published or installed. No secret, GitHub token or OIDC request credential is used, sent or forwarded.
Exit status: 0 ok, 1 refused or failed, 2 usage.
"""
import argparse
import base64
import datetime
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import yogi_ota as yogi  # noqa: E402

Refused = yogi.Refused
SCHEMA = 'roseos.yogi-direct-operation.v1'
OWNERSHIP = 'roseos.yogi-direct-ownership.v1'
STATUS = 'roseos.yogi-direct-status.v1'
STAGES = ('check', 'compile')
# What this route refuses, and the exact decision each one still needs.
NOT_DIRECT = {
    'key-bootstrap': 'needs a Signing-approved signer credential path for a direct operation (GitHub OIDC is never '
                     'forwarded to the guest); not decided',
    'sign': 'needs a Signing-approved signer credential path for a direct operation (GitHub OIDC is never forwarded '
            'to the guest); not decided',
    'install-image': 'follows a signed build A; signing is not available on this route',
    'stage': 'follows a signed OTA; signing is not available on this route',
    'ota': 'needs a signed bootstrap A and signing; not available on this route',
    'publish': 'publication is not part of the direct route (publicationEnabled stays false)',
}
# A process environment that is a GitHub Actions job: the direct route refuses to run there at all, so no workflow
# identity is borrowed and no job or OIDC request credential can reach ssh, gcloud or the guest.
GITHUB_CONTEXT = ('GITHUB_ACTIONS', 'ACTIONS_ID_TOKEN_REQUEST_URL', 'ACTIONS_ID_TOKEN_REQUEST_TOKEN',
                  'ACTIONS_RUNTIME_TOKEN', 'ACTIONS_RUNTIME_URL', 'ACTIONS_CACHE_URL', 'GITHUB_TOKEN')
OP_ID = re.compile(r'[0-9a-f]{32}')
SHA256 = re.compile(r'[0-9a-f]{64}')
USER = re.compile(r'[a-z_][a-z0-9_-]{0,31}')
UNIT = re.compile(r'[A-Za-z0-9@._-]{1,200}\.service')
OPERATOR = re.compile(r'[A-Za-z0-9 ._@-]{1,80}')
# Channels that bind a host key to the numeric instance through the authenticated Compute API, not a network address.
HOST_KEY_CHANNELS = ('serial-console-output', 'guest-attributes-hostkeys')
TERMINAL = ('done', 'failed', 'cancelled', 'expired', 'refused', 'not-installed', 'lost')
MAX_SECONDS = 12 * 3600  # 660 build minutes plus margin; the operator holds no session for it
RUNNER_PROCESSES = ('Runner.Listener', 'Runner.Worker')


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def stamp(moment):
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_stamp(value):
    return datetime.datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)


def sha_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def manual_context(env=os.environ):
    found = sorted(k for k in GITHUB_CONTEXT if k in env)
    if found:
        raise Refused('the direct route is manual operator only and refuses inside a GitHub Actions job '
                      f'({", ".join(found)} present); no workflow identity or job/OIDC credential is used or forwarded')


# ------------------------------------------------------------------------------------------------ contract

def host_key_problem(key):
    if not isinstance(key, dict):
        return 'builder.direct.hostKey is not recorded (a public host key independently bound to the numeric instance)'
    try:
        blob = base64.b64decode(key['key'], validate=True)
        length = int.from_bytes(blob[:4], 'big')
        embedded = blob[4:4 + length].decode()
    except (KeyError, TypeError, ValueError):
        return 'builder.direct.hostKey.key is not a valid OpenSSH public key blob'
    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
    if key.get('type') != 'ssh-ed25519' or embedded != 'ssh-ed25519':
        return 'builder.direct.hostKey must be the ssh-ed25519 host key'
    if key.get('sha256') != fingerprint:
        return 'builder.direct.hostKey.sha256 does not match direct.hostKey.key'
    if key.get('instanceId') != yogi.INSTANCE_ID:
        return 'builder.direct.hostKey is not bound to the replacement numeric instance ID'
    if key.get('observedVia') not in HOST_KEY_CHANNELS:
        return f'builder.direct.hostKey.observedVia must be one of {HOST_KEY_CHANNELS} (never first use, keyscan or a guest name)'
    if key.get('distinctFromOriginal') is not True:
        return ('builder.direct.hostKey.distinctFromOriginal is not true: the replacement disk was restored, so its host key '
                'may equal the original VM\'s until shown otherwise')
    if not key.get('provenance'):
        return 'builder.direct.hostKey.provenance is not recorded'
    return None


def direct_problems(cfg):
    """Every unrecorded dependency of the direct route itself, by name. Runner fields are not among them: this route
    schedules nothing on the runner; it instead requires the runner to be proven quiesced on every step."""
    d, found = cfg['builder'].get('direct') or {}, []
    if d.get('status') != 'ready':
        found.append(f'builder.direct.status is {d.get("status")!r}, not ready')
    if d.get('route') not in ('external-ip', 'iap'):
        found.append('builder.direct.route (external-ip or iap) is not recorded')
    if not isinstance(d.get('sshUser'), str) or not USER.fullmatch(d['sshUser']):
        found.append('builder.direct.sshUser is not recorded')
    problem = host_key_problem(d.get('hostKey'))
    if problem:
        found.append(problem)
    if not isinstance(d.get('runnerService'), str) or not UNIT.fullmatch(d['runnerService']):
        found.append('builder.direct.runnerService (the runner systemd unit, kept inactive and disabled) is not recorded')
    access = d.get('operatorAccess')
    if not isinstance(access, dict) or any(not access.get(k) for k in ('compute', 'ssh', 'paths', 'provenance')):
        found.append('builder.direct.operatorAccess (verified compute get/start/stop, SSH login and Yogi path access for the '
                     'operator) is not recorded')
    if not re.fullmatch(r'[1-9][0-9]{5,19}', str(cfg['builder'].get('retired', {}).get('instanceId', ''))):
        found.append('builder.retired.instanceId (the original VM, checked TERMINATED on every operation) is not recorded')
    root, path = Path(cfg['builder']['root']), Path(d.get('operationRoot', ''))
    if not path.is_absolute() or '..' in path.parts or root not in path.parents:
        found.append(f'builder.direct.operationRoot must be an absolute path under {root}')
    lock = Path(d.get('lock', ''))
    if not lock.is_absolute() or '..' in lock.parts or root not in lock.parents or lock == Path(cfg['builder']['lock']):
        found.append(f'builder.direct.lock must be its own absolute path under {root}')
    missing = [f for f in yogi.evidence_missing(cfg) if f.startswith('storage.')]
    if missing:
        found.append('replacement builder storage receipt fields not recorded: ' + ', '.join(missing))
    return found


def blockers(cfg, stage):
    if stage in NOT_DIRECT:
        return [f'{stage} is not available on the direct route: {NOT_DIRECT[stage]}']
    if stage not in STAGES:
        raise Refused(f'direct stage must be one of {STAGES}')
    found = direct_problems(cfg)
    if stage == 'compile':
        try:
            yogi.app_record(cfg)
        except (Refused, OSError, ValueError, KeyError) as error:
            found.append(f'signed app inputs are not bound: {error}')
        if cfg['bootstrap'].get('status') != 'not-built' or cfg['bootstrap'].get('build'):
            found.append('compile builds bootstrap A only, and bootstrap A is already recorded')
    return found


def guard(cfg, stage):
    found = blockers(cfg, stage)
    if found:
        raise Refused(f'Yogi direct {stage} blocked: ' + '; '.join(found))


# ------------------------------------------------------------------------------------------------ operation

def plan(cfg, stage, source, operator, out, inputs=None, repo=ROOT, run=subprocess.run, now=None, env=os.environ,
         token=lambda: secrets.token_hex(16)):
    """One operation: a random id, the exact builder, the reviewed source (a commit already on origin/main, from a
    clean checkout at that commit) as a hashed archive, the hashed inputs, an expiry. Written once."""
    manual_context(env)
    guard(cfg, stage)
    now = now or now_utc()
    if not yogi.COMMIT.fullmatch(source or ''):
        raise Refused('--source must be a full 40-hex commit')
    if not isinstance(operator, str) or not OPERATOR.fullmatch(operator):
        raise Refused('--operator must name the human operator (letters, digits, space, ._@-)')

    def git(*args):
        return run(['git', '-C', str(repo), *args], capture_output=True, text=True)
    if git('rev-parse', 'HEAD').stdout.strip() != source:
        raise Refused('the local checkout is not at --source')
    if git('status', '--porcelain', '--untracked-files=no').stdout.strip():
        raise Refused('the local checkout has uncommitted changes')
    if git('merge-base', '--is-ancestor', source, 'origin/main').returncode:
        raise Refused('--source is not on origin/main (only reviewed, merged source is executed); fetch main first')
    if stage == 'compile':
        record = yogi.app_record(cfg)
        if not inputs or not Path(inputs).is_file():
            raise Refused('compile needs --inputs: the zip of exactly the committed app-inputs record artifact')
        expected = record['export']['artifact']['digest']
        if 'sha256:' + sha_file(inputs) != expected:
            raise Refused('--inputs does not match the committed app-inputs artifact digest')
    elif inputs:
        raise Refused('only compile takes --inputs')
    out = Path(out)
    out.mkdir(mode=0o700)  # exclusive: one directory per operation
    archive = out/'source.tar'
    result = run(['git', '-C', str(repo), 'archive', '--format=tar', '-o', str(archive), source],
                 capture_output=True, text=True)
    if result.returncode or not archive.is_file():
        raise Refused('git archive of --source failed')
    manifest = {'schema': SCHEMA, 'operationId': token(), 'stage': stage, 'builder': yogi.tuple_of(cfg),
                'source': source, 'sourceArchiveSha256': sha_file(archive),
                'inputs': None, 'operator': {'declared': operator, 'note': 'declared by the operator; not a GitHub identity'},
                'createdAt': stamp(now), 'expiresAt': stamp(now + datetime.timedelta(seconds=MAX_SECONDS))}
    if not OP_ID.fullmatch(manifest['operationId']):
        raise Refused('operation id generation failed')
    if stage == 'compile':
        target = out/'app-inputs.zip'
        target.write_bytes(Path(inputs).read_bytes())
        manifest['inputs'] = {'record': cfg['appInputs']['record'], 'sha256': sha_file(target)}
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


def load(cfg, op, now=None, allow_expired=False):
    """The operation as planned, its files unchanged; refused when expired unless only cleaning up."""
    op = Path(op)
    raw = (op/'manifest.json').read_bytes()
    manifest = json.loads(raw)
    if manifest.get('schema') != SCHEMA or not OP_ID.fullmatch(str(manifest.get('operationId'))) \
            or manifest.get('stage') not in STAGES or manifest.get('builder') != yogi.tuple_of(cfg) \
            or not yogi.COMMIT.fullmatch(str(manifest.get('source'))):
        raise Refused('operation manifest is not a Yogi direct operation for the exact replacement builder')
    if sha_file(op/'source.tar') != manifest['sourceArchiveSha256']:
        raise Refused('source archive changed since the operation was planned')
    if manifest['stage'] == 'compile' and sha_file(op/'app-inputs.zip') != (manifest.get('inputs') or {}).get('sha256'):
        raise Refused('app inputs changed since the operation was planned')
    if not allow_expired and (now or now_utc()) >= parse_stamp(manifest['expiresAt']):
        raise Refused('operation expired; plan a new one (never reuse an operation)')
    return manifest, hashlib.sha256(raw).hexdigest()


def ownership_record(cfg, manifest, manifest_sha, state):
    return {'schema': OWNERSHIP, 'builder': yogi.tuple_of(cfg),
            'operation': {'id': manifest['operationId'], 'stage': manifest['stage'], 'source': manifest['source'],
                          'manifestSha256': manifest_sha},
            'observedBeforeStart': state, 'startedByThisOperation': state == 'TERMINATED'}


def read_ownership(cfg, op, manifest, manifest_sha):
    path = Path(op)/'ownership.json'
    if not path.is_file():
        return None
    record = yogi.read(path)
    if record != ownership_record(cfg, manifest, manifest_sha, record.get('observedBeforeStart')):
        raise Refused('ownership record is not this operation\'s exact record; nothing stopped')
    return record


# ------------------------------------------------------------------------------------------------ control plane

def describe(cfg, run=subprocess.run, gcloud='gcloud', original=False):
    """(status, external IP) of the exact replacement VM, or the exact original VM's status. Project, zone, name and
    numeric ID are checked on every call."""
    b = cfg['builder']
    name, zone, number = ((b['retired']['instance'], b['retired']['zone'], str(b['retired']['instanceId'])) if original
                          else (b['instance'], b['zone'], b['instanceId']))
    result = run([gcloud, 'compute', 'instances', 'describe', name, f'--project={b["project"]}', f'--zone={zone}',
                  '--format=json(id,name,zone,status,selfLink,networkInterfaces)'], capture_output=True, text=True)
    if result.returncode:
        raise Refused(f'describe of {"the original" if original else "the replacement"} Yogi VM failed '
                      f'(exit {result.returncode})')
    try:
        seen = json.loads(result.stdout)
        link = f'/projects/{b["project"]}/zones/{zone}/instances/{name}'
        if str(seen['id']) != number or seen['name'] != name or str(seen['zone']).rsplit('/', 1)[-1] != zone \
                or not str(seen['selfLink']).endswith(link) or not isinstance(seen['status'], str):
            raise ValueError
        address = None
        for nic in seen.get('networkInterfaces') or []:
            for access in nic.get('accessConfigs') or []:
                address = address or access.get('natIP')
    except (KeyError, TypeError, ValueError, AttributeError):
        raise Refused('Yogi VM identity mismatch (project, zone, name or numeric ID); nothing requested') from None
    return seen['status'], address


def wait_for(cfg, target, passing, run, gcloud, sleep, clock, timeout=420, interval=10):
    deadline = clock() + timeout
    state = describe(cfg, run, gcloud)[0]
    while state != target:
        if state not in passing:
            raise Refused(f'Yogi builder moved to {state} while waiting for {target}')
        if clock() >= deadline:
            raise Refused(f'Yogi builder did not reach {target} within {timeout}s; last observed {state}')
        sleep(interval)
        state = describe(cfg, run, gcloud)[0]
    return state


def original_is_stopped(cfg, run, gcloud):
    if not re.fullmatch(r'[1-9][0-9]{5,19}', str(cfg['builder'].get('retired', {}).get('instanceId', ''))):
        raise Refused('builder.retired.instanceId is not recorded; the original VM cannot be checked; nothing requested')
    state = describe(cfg, run, gcloud, original=True)[0]
    if state != 'TERMINATED':
        raise Refused(f'the original Yogi VM is {state}; the direct route runs only while it is TERMINATED')


def start(cfg, op, run=subprocess.run, gcloud='gcloud', sleep=time.sleep, clock=time.monotonic, env=os.environ,
          now=None):
    """The ownership record is written and fsynced before START. Only a TERMINATED VM is started: a running VM may be
    another operation's, so it is never adopted and this operation refuses. START is never retried."""
    manual_context(env)
    manifest, manifest_sha = load(cfg, op, now)
    guard(cfg, manifest['stage'])
    original_is_stopped(cfg, run, gcloud)
    state = describe(cfg, run, gcloud)[0]
    with open(Path(op)/'ownership.json', 'x') as f:  # one operation, one record, durable before any request
        json.dump(ownership_record(cfg, manifest, manifest_sha, state), f)
        f.flush()
        os.fsync(f.fileno())
    if state != 'TERMINATED':
        raise Refused(f'Yogi builder is {state}, not TERMINATED; not started and not adopted by this operation')
    b = cfg['builder']
    result = run([gcloud, 'compute', 'instances', 'start', b['instance'], f'--project={b["project"]}',
                  f'--zone={b["zone"]}', '--async', '--quiet'], capture_output=True, text=True)
    if result.returncode:
        raise Refused(f'Yogi builder START request uncertain (exit {result.returncode}); not retried, record kept')
    return wait_for(cfg, 'RUNNING', {'TERMINATED', 'STAGING', 'PROVISIONING'}, run, gcloud, sleep, clock)


# ------------------------------------------------------------------------------------------------ SSH

# Run on the guest only after ssh has authenticated the pinned host key: the GCE metadata tuple, whether a runner
# process exists and the runner unit's state. Read-only.
PROBE = r'''
import json, os, subprocess, sys, urllib.request
def meta(key):
    request = urllib.request.Request('http://metadata.google.internal/computeMetadata/v1/' + key,
                                     headers={'Metadata-Flavor': 'Google'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
        return response.read(4096).decode().strip()
seen = {}
try:
    seen = {'project': meta('project/project-id'), 'zone': meta('instance/zone').rsplit('/', 1)[-1],
            'instance': meta('instance/name'), 'instanceId': meta('instance/id')}
except Exception as error:
    seen = {'error': type(error).__name__}
runner = False
for pid in os.listdir('/proc'):
    try:
        cmd = open(f'/proc/{pid}/cmdline', 'rb').read().decode(errors='replace')
    except OSError:
        continue
    runner = runner or any(p in cmd for p in ('Runner.Listener', 'Runner.Worker'))
unit = sys.argv[1]
def systemctl(verb):
    r = subprocess.run(['systemctl', verb, unit], capture_output=True, text=True)
    return r.stdout.strip() or r.stderr.strip()[:80]
print(json.dumps({'builder': seen, 'runnerProcess': runner, 'active': systemctl('is-active'),
                  'enabled': systemctl('is-enabled')}))
'''


def quiesced(probe):
    return probe.get('runnerProcess') is False and probe.get('active') in ('inactive', 'failed') \
        and probe.get('enabled') in ('disabled', 'masked')


class Session:
    """ssh to the exact VM with the committed host-key pin as the only trusted key: no user ssh config, no agent,
    forwarding or environment, no first-use acceptance and no host-key updates."""
    def __init__(self, cfg, address, identity_file, run=subprocess.run, gcloud='gcloud'):
        d, b = cfg['builder']['direct'], cfg['builder']
        self.cfg, self.run, self.alias = cfg, run, f'yogi-{b["instanceId"]}'
        self.temp = tempfile.TemporaryDirectory()
        self.known = Path(self.temp.name)/'known_hosts'
        self.known.write_text(f'{self.alias} {d["hostKey"]["type"]} {d["hostKey"]["key"]}\n')
        if not identity_file or not Path(identity_file).is_file():
            raise Refused('--identity must be the operator\'s own SSH private key file (it never leaves this machine)')
        options = ['-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-i', str(identity_file),
                   '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={self.known}',
                   '-o', 'GlobalKnownHostsFile=/dev/null', '-o', f'HostKeyAlias={self.alias}',
                   '-o', 'HostKeyAlgorithms=ssh-ed25519', '-o', 'CheckHostIP=no', '-o', 'UpdateHostKeys=no',
                   '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'ClearAllForwardings=yes',
                   '-o', 'PermitLocalCommand=no', '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=30',
                   '-o', 'ServerAliveCountMax=4', '-l', d['sshUser']]
        if d['route'] == 'iap':
            options += ['-o', 'ProxyCommand=' + shlex.join([
                gcloud, 'compute', 'start-iap-tunnel', b['instance'], '22', '--listen-on-stdin',
                f'--project={b["project"]}', f'--zone={b["zone"]}', '--verbosity=warning'])]
            self.target = self.alias
        else:
            if not address or not re.fullmatch(r'[0-9]{1,3}(\.[0-9]{1,3}){3}', address):
                raise Refused('the replacement VM has no external address in its exact-identity describe')
            self.target = address
        self.argv = ['ssh', *options, self.target, '--']
        # ssh gets a minimal environment: nothing from a job, token or the operator's shell reaches it or the guest.
        self.env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': self.temp.name}

    def __call__(self, command, data=None, timeout=600):
        result = self.run([*self.argv, command], input=data, capture_output=True, env=self.env, timeout=timeout)
        if result.returncode == 255:
            raise Refused('SSH to the Yogi builder failed or its host key did not match the pin; nothing sent')
        return result

    def close(self):
        self.temp.cleanup()


def verified(cfg, session):
    """The first command on every connection: the guest's metadata tuple and a quiesced runner. ssh has already
    refused any host key but the pin; this adds the instance's own view and the runner state before anything else."""
    result = session(f'python3 -I -c {shlex.quote(PROBE)} {shlex.quote(cfg["builder"]["direct"]["runnerService"])}', timeout=60)
    try:
        probe = json.loads(result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout)
    except (ValueError, AttributeError):
        raise Refused('the Yogi builder identity probe returned no result; nothing sent') from None
    if result.returncode or probe.get('builder') != yogi.tuple_of(cfg):
        raise Refused('the authenticated host is not the exact replacement tuple; nothing sent')
    if not quiesced(probe):
        raise Refused('the Yogi runner is not quiesced (process, or unit not inactive and disabled); nothing sent')
    return probe


def connect(cfg, op, identity_file, run=subprocess.run, gcloud='gcloud', now=None, allow_expired=False):
    manifest, manifest_sha = load(cfg, op, now, allow_expired)
    original_is_stopped(cfg, run, gcloud)
    state, address = describe(cfg, run, gcloud)
    if state != 'RUNNING':
        raise Refused(f'Yogi builder is {state}; nothing sent')
    session = Session(cfg, address, identity_file, run, gcloud)
    try:
        verified(cfg, session)
    except BaseException:
        session.close()
        raise
    return manifest, manifest_sha, session


def bundle(op, manifest):
    """One tar of exactly the planned files: manifest.json, source.tar and, for compile, app-inputs.zip."""
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w', format=tarfile.PAX_FORMAT) as tar:
        for name in ('manifest.json', 'source.tar') + (('app-inputs.zip',) if manifest['stage'] == 'compile' else ()):
            info = tar.gettarinfo(str(Path(op)/name), arcname=name)
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mtime = 0
            with open(Path(op)/name, 'rb') as f:
                tar.addfile(info, f)
    return data.getvalue()


def install_script(cfg, manifest, manifest_sha, bundle_sha):
    """The guest command that receives the bundle: exclusive operation directory (a replay or duplicate fails),
    hashes checked before anything is unpacked or executed, then the agent started detached from this session."""
    d = Path(cfg['builder']['direct']['operationRoot'])/manifest['operationId']
    q = shlex.quote
    return '\n'.join([
        'set -eu', 'umask 077',
        f'mkdir -p {q(str(d.parent))}',
        f'mkdir {q(str(d))}',
        f'cat > {q(str(d/"bundle.tar"))}',
        f'printf "%s  %s\\n" {q(bundle_sha)} {q(str(d/"bundle.tar"))} | sha256sum -c --quiet -',
        f'mkdir {q(str(d/"in"))}',
        f'tar -x --no-same-owner --no-same-permissions -C {q(str(d/"in"))} -f {q(str(d/"bundle.tar"))} '
        'manifest.json source.tar' + (' app-inputs.zip' if manifest['stage'] == 'compile' else ''),
        f'printf "%s  %s\\n" {q(manifest_sha)} {q(str(d/"in/manifest.json"))} | sha256sum -c --quiet -',
        f'printf "%s  %s\\n" {q(manifest["sourceArchiveSha256"])} {q(str(d/"in/source.tar"))} | sha256sum -c --quiet -',
        f'mkdir {q(str(d/"src"))}',
        f'tar -x --no-same-owner --no-same-permissions -C {q(str(d/"src"))} -f {q(str(d/"in/source.tar"))}',
        f'cd {q(str(d))}',
        f'setsid python3 -I {q(str(d/"src/scripts/yogi_direct.py"))} agent --op-dir {q(str(d))} '
        f'--manifest-sha {q(manifest_sha)} </dev/null >{q(str(d/"agent.log"))} 2>&1 &',
        'echo launched'])


def launch(cfg, op, identity_file, run=subprocess.run, gcloud='gcloud', now=None, env=os.environ):
    manual_context(env)
    manifest, manifest_sha = load(cfg, op, now)
    guard(cfg, manifest['stage'])
    record = read_ownership(cfg, op, manifest, manifest_sha)
    if not record or not record['startedByThisOperation']:
        raise Refused('this operation did not start the Yogi builder; run `start` first (a running VM is never adopted)')
    marker = Path(op)/'launched.json'
    if marker.exists():
        raise Refused('this operation was already sent once; it is never re-sent (plan a new operation)')
    manifest, manifest_sha, session = connect(cfg, op, identity_file, run, gcloud, now)
    try:
        data = bundle(op, manifest)
        digest = hashlib.sha256(data).hexdigest()
        with open(marker, 'x') as f:  # before sending: a lost connection never leads to a second send
            json.dump({'operationId': manifest['operationId'], 'bundleSha256': digest, 'sentAt': stamp(now or now_utc())}, f)
        result = session(install_script(cfg, manifest, manifest_sha, digest), data=data, timeout=1800)
        if result.returncode:
            raise Refused(f'the guest refused the operation bundle (exit {result.returncode}); nothing re-sent')
        return {'operationId': manifest['operationId'], 'bundleSha256': digest, 'launched': True}
    finally:
        session.close()


# Read-only on the guest: this operation's status file (if any) and whether its recorded agent process is alive.
STATUS_PROBE = r'''
import json, os, sys
d = sys.argv[1]
if not os.path.isdir(d):
    print(json.dumps({'status': {'state': 'not-installed'}, 'alive': False})); sys.exit()
try:
    status = json.load(open(os.path.join(d, 'status.json')))
except FileNotFoundError:
    print(json.dumps({'status': {'state': 'installed'}, 'alive': False})); sys.exit()
alive = False
if isinstance(status.get('pid'), int) and status['pid'] > 1:
    try:
        os.kill(status['pid'], 0)
        alive = True
    except OSError:
        alive = False
print(json.dumps({'status': status, 'alive': alive}))
'''


def remote_status(cfg, manifest, session):
    d = Path(cfg['builder']['direct']['operationRoot'])/manifest['operationId']
    result = session(f'python3 -I -c {shlex.quote(STATUS_PROBE)} {shlex.quote(str(d))}', timeout=60)
    try:
        answer = json.loads(result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout)
        status, alive = answer['status'], answer['alive'] is True
    except (ValueError, KeyError, TypeError, AttributeError):
        raise Refused('the Yogi builder returned no readable operation status') from None
    if status.get('state') not in ('not-installed', 'installed') and \
            (status.get('schema') != STATUS or status.get('operationId') != manifest['operationId']):
        raise Refused('remote status is not this operation\'s')
    if status.get('state') in ('running', 'installed') and not alive:
        status = dict(status, state='lost' if status.get('state') == 'running' else 'not-installed')
    return status, alive


def status(cfg, op, identity_file, run=subprocess.run, gcloud='gcloud', env=os.environ):
    manual_context(env)
    manifest, _, session = connect(cfg, op, identity_file, run, gcloud, allow_expired=True)
    try:
        return remote_status(cfg, manifest, session)[0]
    finally:
        session.close()


def cancel(cfg, op, identity_file, run=subprocess.run, gcloud='gcloud', env=os.environ):
    manual_context(env)
    manifest, _, session = connect(cfg, op, identity_file, run, gcloud, allow_expired=True)
    try:
        state, alive = remote_status(cfg, manifest, session)
        if state.get('state') != 'running' or not alive or not isinstance(state.get('pgid'), int) or state['pgid'] < 2:
            raise Refused(f'nothing to cancel: the operation is {state.get("state")}')
        result = session(f'kill -TERM -- -{state["pgid"]}', timeout=60)
        if result.returncode:
            raise Refused(f'cancel signal failed (exit {result.returncode})')
        return 'cancel requested (the agent records cancelled when it stops)'
    finally:
        session.close()


def cleanup(cfg, op, identity_file, abandon=False, run=subprocess.run, gcloud='gcloud', sleep=time.sleep,
            clock=time.monotonic, env=os.environ, now=None):
    """STOP the exact VM only with this operation's own record that started it, and only once its agent is terminal,
    or the operator abandons an unreachable operation explicitly (recorded). A running agent is never stopped under."""
    manual_context(env)
    manifest, manifest_sha = load(cfg, op, now, allow_expired=True)
    record = read_ownership(cfg, op, manifest, manifest_sha)
    if not record or not record['startedByThisOperation']:
        return 'nothing to stop: this operation did not start the Yogi builder'
    state = describe(cfg, run, gcloud)[0]
    if state == 'TERMINATED':
        outcome = 'TERMINATED'
    else:
        remote = None
        if state == 'RUNNING':
            try:
                manifest, _, session = connect(cfg, op, identity_file, run, gcloud, allow_expired=True)
                try:
                    remote = remote_status(cfg, manifest, session)[0]['state']
                finally:
                    session.close()
            except Refused:
                remote = None
        if remote == 'running' or remote == 'installed':
            raise Refused('the operation is still running on the Yogi builder; cancel it first; nothing stopped')
        if remote not in TERMINAL:
            if not abandon:
                raise Refused('the operation state cannot be verified; pass --abandon to stop anyway (recorded)')
        if state in ('STAGING', 'PROVISIONING'):
            wait_for(cfg, 'RUNNING', {'STAGING', 'PROVISIONING'}, run, gcloud, sleep, clock)
            state = 'RUNNING'
        if state == 'RUNNING':
            b = cfg['builder']
            result = run([gcloud, 'compute', 'instances', 'stop', b['instance'], f'--project={b["project"]}',
                          f'--zone={b["zone"]}', '--async', '--quiet'], capture_output=True, text=True)
            if result.returncode and describe(cfg, run, gcloud)[0] not in ('STOPPING', 'TERMINATED'):
                raise Refused(f'Yogi builder stop request failed (exit {result.returncode})')
        elif state != 'STOPPING':
            raise Refused(f'Yogi builder is {state}; refusing to stop it')
        outcome = wait_for(cfg, 'TERMINATED', {'RUNNING', 'STOPPING'}, run, gcloud, sleep, clock)
        if remote not in TERMINAL:
            remote = 'abandoned (unverified)'
        record = {'operationId': manifest['operationId'], 'remote': remote, 'stopped': outcome,
                  'at': stamp(now or now_utc())}
        release_receipt(Path(op)/'cleanup.json', record)
    return outcome


def release_receipt(path, record):
    with open(path, 'x') as f:
        json.dump(record, f, indent=2, sort_keys=True)


# ------------------------------------------------------------------------------------------------ guest agent

class Cancelled(Exception):
    pass


def runner_state(unit, proc='/proc', run=subprocess.run):
    running = False
    for pid in os.listdir(proc):
        try:
            cmd = Path(proc, pid, 'cmdline').read_bytes().decode(errors='replace')
        except OSError:
            continue
        running = running or any(p in cmd for p in RUNNER_PROCESSES)

    def systemctl(verb):
        r = run(['systemctl', verb, unit], capture_output=True, text=True)
        return r.stdout.strip()
    return {'runnerProcess': running, 'active': systemctl('is-active'), 'enabled': systemctl('is-enabled')}


def direct_identity(cfg, metadata=yogi.metadata_read, runner=None):
    """The direct route's identity refusal (no runner name exists here): the exact metadata tuple and a quiesced
    runner, or Refused before anything is inspected."""
    try:
        seen = {'project': metadata('project/project-id'), 'zone': metadata('instance/zone').rsplit('/', 1)[-1],
                'instance': metadata('instance/name'), 'instanceId': metadata('instance/id')}
    except Exception as error:  # noqa: BLE001 - unavailable metadata is a refusal, never a pass
        raise Refused(f'direct operation refused: host identity unavailable ({type(error).__name__})') from None
    if seen != yogi.tuple_of(cfg):
        raise Refused('direct operation refused: host identity is not the exact replacement tuple')
    state = (runner or (lambda: runner_state(cfg['builder']['direct']['runnerService'])))()
    if not quiesced(state):
        raise Refused('direct operation refused: the Yogi runner is not quiesced')
    return [('host identity is the exact replacement tuple', json.dumps(seen, sort_keys=True)),
            ('direct operation: runner quiesced (no runner process; unit inactive and disabled)', json.dumps(state))]


def safe_unzip(archive, target):
    """Release's artifact zip: regular files and directories only, all inside target."""
    target.mkdir(mode=0o700)
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            name = Path(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if name.is_absolute() or '..' in name.parts or mode not in (0, 0o100000, 0o040000):
                raise Refused('app inputs archive has an unsafe entry')
        z.extractall(target)
    return target


def agent(op_dir, manifest_sha, cfg=None, env=os.environ, now=now_utc, metadata=yogi.metadata_read, runner=None,
          steps=None, lock_open=os.open):
    """The guest side of one operation, detached from the SSH session: verify, take the direct lock, run each step
    with identity and runner re-checked before it, and persist status so a lost connection loses nothing. Never
    resumed or re-run: a new operation is planned instead."""
    op_dir = Path(op_dir)
    manual_context(env)
    cfg = cfg or yogi.config()
    path = op_dir/'status.json'
    if path.exists():
        raise Refused('this operation already ran; never re-run')
    state = {'schema': STATUS, 'operationId': op_dir.name, 'state': 'refused', 'pid': os.getpid(),
             'pgid': os.getpgid(0), 'steps': [], 'startedAt': stamp(now())}

    def write():
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(state, indent=2, sort_keys=True))
        os.replace(temp, path)
    try:
        raw = (op_dir/'in/manifest.json').read_bytes()
        manifest = json.loads(raw)
        if hashlib.sha256(raw).hexdigest() != manifest_sha or manifest.get('operationId') != op_dir.name \
                or manifest.get('builder') != yogi.tuple_of(cfg) or manifest.get('schema') != SCHEMA:
            raise Refused('operation manifest differs from the one sent')
        if sha_file(op_dir/'in/source.tar') != manifest['sourceArchiveSha256']:
            raise Refused('source archive differs from the manifest')
        guard(cfg, manifest['stage'])
        if now() >= parse_stamp(manifest['expiresAt']):
            raise Refused('operation expired before it started')
        handle = lock_open(cfg['builder']['direct']['lock'], os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(handle)
            raise Refused('another Yogi direct operation holds the direct lock') from None
    except (Refused, OSError, ValueError, KeyError) as error:
        state['error'] = str(error) if isinstance(error, Refused) else type(error).__name__
        write()
        raise Refused(state['error']) from None

    def on_term(signum, frame):
        raise Cancelled()
    previous = signal.signal(signal.SIGTERM, on_term)
    state['state'] = 'running'
    write()
    identity = (lambda c: direct_identity(c, metadata, runner))
    try:
        for name, step in (steps or stage_steps(cfg, manifest, op_dir, identity)):
            if now() >= parse_stamp(manifest['expiresAt']):
                state['state'] = 'expired'
                break
            identity(cfg)  # every step: still the exact host, runner still quiesced
            started = stamp(now())
            result = step()
            state['steps'].append({'step': name, 'startedAt': started, 'finishedAt': stamp(now()), 'result': result})
            write()
        else:
            state['state'] = 'done'
    except Cancelled:
        state['state'] = 'cancelled'
    except (Refused, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        state['state'], state['error'] = 'failed', str(error) if isinstance(error, Refused) else type(error).__name__
    finally:
        signal.signal(signal.SIGTERM, previous)
        state['finishedAt'] = stamp(now())
        write()
        os.close(handle)
    return state


def stage_steps(cfg, manifest, op_dir, identity):
    if manifest['stage'] == 'check':
        return [('readiness', lambda: yogi.readiness(cfg, op_dir/'readiness', identity=identity)['passed'])]
    built = {}

    def allocate():
        built.update(yogi.allocate(cfg, None, None, manifest['source'], operation=manifest['operationId']))
        return built['build']

    def inputs():
        if sha_file(op_dir/'in/app-inputs.zip') != manifest['inputs']['sha256']:
            raise Refused('app inputs differ from the manifest')
        return str(safe_unzip(op_dir/'in/app-inputs.zip', op_dir/'app-inputs'))
    return [('preflight', lambda: yogi.preflight(cfg)), ('allocate', allocate),
            ('prepare', lambda: yogi.prepare(cfg, built['build'])['build']), ('inputs', inputs),
            ('integrate', lambda: bool(yogi.integrate(cfg, built['build'], op_dir/'app-inputs'))),
            ('compile', lambda: bool(yogi.compile_build(cfg, built['build'])))]


# ------------------------------------------------------------------------------------------------ CLI

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('action', choices=('describe', 'plan', 'start', 'run', 'status', 'cancel', 'cleanup', 'agent'))
    p.add_argument('--stage', choices=STAGES + tuple(NOT_DIRECT))
    p.add_argument('--source')
    p.add_argument('--operator')
    p.add_argument('--inputs')
    p.add_argument('--out')
    p.add_argument('--op')
    p.add_argument('--op-dir')
    p.add_argument('--manifest-sha')
    p.add_argument('--identity', help='the operator\'s SSH private key file (stays on this machine)')
    p.add_argument('--abandon', action='store_true')
    a = p.parse_args(argv)
    try:
        if a.action == 'agent':
            print(json.dumps(agent(a.op_dir, a.manifest_sha)))
            return 0
        cfg = yogi.config()
        if a.action == 'describe':
            print(json.dumps({s: blockers(cfg, s) for s in STAGES + tuple(NOT_DIRECT)}, indent=2))
        elif a.action == 'plan':
            print(json.dumps(plan(cfg, a.stage, a.source, a.operator, a.out or p.error('plan needs --out'), a.inputs)))
        elif a.action == 'start':
            print(start(cfg, a.op))
        elif a.action == 'run':
            print(json.dumps(launch(cfg, a.op, a.identity)))
        elif a.action == 'status':
            print(json.dumps(status(cfg, a.op, a.identity)))
        elif a.action == 'cancel':
            print(cancel(cfg, a.op, a.identity))
        else:
            print(cleanup(cfg, a.op, a.identity, a.abandon))
    except (Refused, ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        shown = error if isinstance(error, (Refused, ValueError)) else 'operation failed; inspect the operation directory'
        print(f'Yogi direct stopped: {type(error).__name__}: {shown}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
