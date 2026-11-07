#!/usr/bin/env python3
"""One strict IAP possession check for the Yogi replacement.

`handshake-plan` does not call GCP or SSH. `handshake --execute` is the operator run:
one metadata append, one read-only probe, one exact-line removal, a guest refresh, STOP,
then a fresh inspect. It does not record builder.direct.hostKey. hostKeyPinned stays
false; this tool does not write a config pin. The private login key stays on the
operator machine. pixelbuild is not given sudo or a new role.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

PROJECT = 'protocols-465723'
ZONE = 'us-west2-b'
INSTANCE = 'roseos-yogi-builder-20261105-b'
INSTANCE_ID = '2720796557371812468'
POSSESSION_FINGERPRINTS = {
    'ssh-ed25519': 'SHA256:n4xqiwBsyYqkQ/aF19AXkVr9fqAZJmJLWPqfZwhUweE',
    'ecdsa-sha2-nistp256': 'SHA256:TCnFbeIJ1450hxNHg99j4b9NpdzV7W5DmPNyho5YAnQ',
    'ssh-rsa': 'SHA256:c5YklLIyN4gjq16yBM+dYMVq3VpAodoA0TbseKXDT/c',
}
POSSESSION_USER = 'pixelbuild'
POSSESSION_UNIT = 'actions.runner.Immortal-Protocols-RoseOS-1.roseos-yogi-builder-20261105-b.service'
POSSESSION_BASE = {'enable-oslogin': 'FALSE', 'block-project-ssh-keys': 'TRUE'}
# Reviewed terminated read at 2026-11-07T15:01:24Z. A different map is an unexplained change.
POSSESSION_FINGERPRINT = 'pSAPI4eI6q4='
SPENT_INSPECT_IDS = {
    'e75b0fd782e43f585c621c74f95bfa90',
    '5cf6fe9a62880b89ee6e7e822a0379d2',
    '20ef6008b1ef78137d201de13176af42',
    '638e6e9039e41b7531697bb45066a616',
}
OP_ID = re.compile(r'[0-9a-f]{32}')
OPERATION_NAME = re.compile(r'operation-[0-9]{10,16}-[0-9a-f]{8,20}-[0-9a-f]{6,20}-[0-9a-f]{6,20}')
SPENT_ZONE_OPERATIONS = frozenset({
    'operation-1794025967141-65fa8387d4384-e9bed427-7ed969ab',
    'operation-1794025969883-65fa838a71873-581e64cf-6e23ae4e',
    'operation-1794026872649-65fa86e763554-67c3c575-aa4d2c66',
    'operation-1794026874731-65fa86e95fb00-d547145e-45241669',
    'operation-1794027065214-65fa879f086ca-4cf61ef5-4480e200',
    'operation-1794027069962-65fa87a38f91f-6bc72969-2fdd5f07',
})
GITHUB_CONTEXT = ('GITHUB_ACTIONS', 'ACTIONS_ID_TOKEN_REQUEST_URL', 'ACTIONS_ID_TOKEN_REQUEST_TOKEN',
                  'ACTIONS_RUNTIME_TOKEN', 'ACTIONS_RUNTIME_URL', 'ACTIONS_CACHE_URL', 'GITHUB_TOKEN')
# Read-only. No sudo, no account change, no runner start.
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
    return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else 'unknown'
print(json.dumps({'builder': seen, 'runnerProcess': runner, 'active': systemctl('is-active'),
                  'enabled': systemctl('is-enabled')}))
'''


class Refused(Exception):
    pass


def manual_context(env):
    found = sorted(key for key in GITHUB_CONTEXT if key in env)
    if found:
        raise Refused('possession planning refuses a GitHub Actions environment')


def _fingerprint(blob):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')


def _public_blob(value):
    if not isinstance(value, str) or any(token in value for token in ('BEGIN', 'PRIVATE', 'OPENSSH', ' ', '\n', '\r')):
        raise Refused('host key material is not a public blob')
    try:
        blob = base64.b64decode(value, validate=True)
        length = int.from_bytes(blob[:4], 'big')
        embedded = blob[4:4 + length].decode()
    except (ValueError, UnicodeError, IndexError):
        raise Refused('host key material is not a public blob') from None
    if not 8 <= len(blob) <= 4096 or embedded not in POSSESSION_FINGERPRINTS:
        raise Refused('host key material is not a public blob')
    return embedded, blob, _fingerprint(blob)


def hostkeys_from_api(document, expected=None):
    expected = POSSESSION_FINGERPRINTS if expected is None else expected
    if isinstance(document, dict) and document.get('absent') is True:
        raise Refused('guest-attribute host keys are absent')
    if isinstance(document, list):
        items = document
    elif isinstance(document, dict) and isinstance((document.get('queryValue') or {}).get('items'), list):
        items = document['queryValue']['items']
    else:
        raise Refused('guest-attribute host keys are not the hostkeys response')
    found = {}
    for item in items:
        if not isinstance(item, dict) or item.get('namespace') not in (None, 'hostkeys'):
            raise Refused('host key material is not a public blob')
        name, blob, fingerprint = _public_blob(item.get('value'))
        if item.get('key') not in (None, name) or name in found:
            raise Refused('host key material is not a public blob')
        if expected.get(name) != fingerprint:
            raise Refused('guest-attribute host key fingerprint does not match the saved API read')
        found[name] = base64.b64encode(blob).decode()
    if set(found) != set(expected):
        raise Refused('guest-attribute host keys are not the three saved public keys')
    return found


def client_login_line(text, operation_id, now):
    """One expiring pixelbuild login line. The private half is not read."""
    if not OP_ID.fullmatch(operation_id or ''):
        raise Refused('possession operation id is not a fresh 32 hex characters')
    if not isinstance(text, str) or any(token in text for token in ('BEGIN', 'PRIVATE', 'OPENSSH')):
        raise Refused('client login key is not a public line')
    parts = text.strip().split()
    if len(parts) < 2 or parts[0] != 'ssh-ed25519':
        raise Refused('client login key must be one ssh-ed25519 public line')
    name, _blob, _fp = _public_blob(parts[1])
    if name != 'ssh-ed25519':
        raise Refused('client login key must be one ssh-ed25519 public line')
    expire = (now + datetime.timedelta(minutes=20)).strftime('%Y-%m-%dT%H:%M:%S+0000')
    marker = 'roseos-yogi-possess-' + operation_id
    payload = json.dumps({'userName': POSSESSION_USER, 'expireOn': expire, 'comment': marker},
                         separators=(',', ':'), sort_keys=True)
    return f'{POSSESSION_USER}:ssh-ed25519 {parts[1]} google-ssh {payload}'


def _require_base(items):
    if not isinstance(items, dict) or any(not isinstance(value, str) for value in items.values()):
        raise Refused('metadata is not a string map')
    for key, value in POSSESSION_BASE.items():
        if items.get(key) != value:
            raise Refused('metadata base keys are not enable-oslogin FALSE and block-project-ssh-keys TRUE')


def ssh_key_lines(value):
    if value is None:
        return []
    if not isinstance(value, str) or 'BEGIN' in value or 'PRIVATE' in value:
        raise Refused('ssh-keys is not a public list')
    return [line for line in value.split('\n') if line]


def add_login_line(items, line):
    """Copy every metadata key and every existing ssh-keys line. Append only this line."""
    _require_base(items)
    lines = ssh_key_lines(items.get('ssh-keys'))
    if line in lines:
        raise Refused('login line is already present')
    blob = line.split(' ', 2)[1]
    if any(blob in existing for existing in lines):
        raise Refused('login public key is already present')
    kept = dict(items)
    kept['ssh-keys'] = '\n'.join(lines + [line])
    return kept


def remove_login_line(items, line):
    """Remove only the exact login line. Every other ssh-keys line and metadata key stays."""
    _require_base(items)
    lines = ssh_key_lines(items.get('ssh-keys'))
    kept = dict(items)
    if line not in lines:
        return kept
    remaining = [existing for existing in lines if existing != line]
    if remaining:
        kept['ssh-keys'] = '\n'.join(remaining)
    else:
        kept.pop('ssh-keys', None)
    return kept


def local_identity(path):
    """The private key file must be a regular mode 0600 file. Its bytes are not read."""
    info = os.lstat(path)
    if not os.path.isfile(path) or info.st_mode & 0o170000 == 0o120000:
        raise Refused('identity file is not a regular file')
    if info.st_mode & 0o777 != 0o600:
        raise Refused('identity file must be mode 0600')
    return os.path.abspath(path)


def gcloud_environment(environ):
    config = environ.get('CLOUDSDK_CONFIG') or (
        os.path.join(environ['HOME'], '.config', 'gcloud') if environ.get('HOME') else '')
    found = [('CLOUDSDK_CONFIG', config)]
    if environ.get('CLOUDSDK_PYTHON'):
        found.append(('CLOUDSDK_PYTHON', environ['CLOUDSDK_PYTHON']))
    for name, value in found:
        if not os.path.isabs(value) or any(char in value for char in '\n\r\0'):
            raise Refused('CLOUDSDK_CONFIG for the IAP tunnel must be an absolute path')
    return [f'{name}={value}' for name, value in found]


def possession_ssh(hostkeys, identity_path, environ):
    alias = f'yogi-{INSTANCE_ID}'
    known = ''.join(f'{alias} {name} {hostkeys[name]}\n' for name in (
        'ssh-ed25519', 'ecdsa-sha2-nistp256', 'ssh-rsa'))
    proxy = shlex.join(['/usr/bin/env', *gcloud_environment(environ), 'gcloud', 'compute', 'start-iap-tunnel',
                        INSTANCE, '22', '--listen-on-stdin', f'--project={PROJECT}', f'--zone={ZONE}',
                        '--verbosity=warning']).replace('%', '%%')
    command = f'python3 -I -c {shlex.quote(PROBE)} {shlex.quote(POSSESSION_UNIT)}'
    argv = ['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'RequestTTY=no',
            '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
            '-o', 'PreferredAuthentications=publickey', '-o', 'NumberOfPasswordPrompts=0',
            '-o', 'PubkeyAuthentication=yes', '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none',
            '-i', identity_path, '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile=KNOWN_HOSTS_FILE', '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', f'HostKeyAlias={alias}', '-o', 'HostKeyAlgorithms=ssh-ed25519',
            '-o', 'CheckHostIP=no', '-o', 'UpdateHostKeys=no', '-o', 'ForwardAgent=no',
            '-o', 'ForwardX11=no', '-o', 'ClearAllForwardings=yes', '-o', 'PermitLocalCommand=no',
            '-o', 'ConnectTimeout=20', '-l', POSSESSION_USER, '-o', 'ProxyCommand=' + proxy,
            alias, '--', command]
    joined = ' '.join(argv)
    if 'gcloud compute ssh' in joined or 'sudo' in command or 'TOFU' in joined:
        raise Refused('possession ssh is not the strict read-only command')
    return {'knownHosts': known, 'argv': argv, 'timeoutSeconds': 60}


def possession_plan(api, client_public, operation_id, current_items=None, expected=None, environ=None, now=None,
                   identity_path='IDENTITY_FILE'):
    manual_context(os.environ if environ is None else environ)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    current_items = POSSESSION_BASE if current_items is None else current_items
    hostkeys = hostkeys_from_api(api, expected)
    line = client_login_line(client_public, operation_id, now)
    added = add_login_line(current_items, line)
    ssh = possession_ssh(hostkeys, identity_path, os.environ if environ is None else environ)
    return {
        'schema': 'roseos.yogi-possession-plan.v1', 'executed': False, 'operationId': operation_id,
        'instanceId': INSTANCE_ID, 'route': 'iap', 'sshUser': POSSESSION_USER,
        'observedVia': 'guest-attributes-hostkeys', 'serialTrustedForPin': False,
        'distinctFromOriginal': False, 'iapAuthenticatesHostKey': False,
        'hostKeyPinned': False, 'guestAuthenticated': False, 'accountChanges': False,
        'gcloudComputeSsh': 'refused', 'osLogin': 'FALSE', 'projectSshKeys': 'blocked',
        'loginLine': line, 'knownHosts': ssh['knownHosts'], 'argv': ssh['argv'],
        'commandTimeoutSeconds': 60, 'metadataAdd': added,
        'cleanup': 'remove only this ssh-keys line, STOP, then a fresh inspect must show the exact comment absent',
        'fingerprint': 'one write, 412 is not retried',
        'steps': [
            'describe TERMINATED; keep every metadata key; base values must stay',
            'start once; a start timeout still attempts STOP and adds no login line',
            'setMetadata the current fingerprint with only loginLine appended; do not retry 412',
            'one read-only ssh probe, timeout 60, identity mode 0600, no sudo',
            'setMetadata the new fingerprint with only that exact line removed; 412 keeps cleanup instructions',
            'wait for the guest agent to refresh authorized_keys',
            'STOP if start was accepted or the start call timed out',
            'fresh inspect boot, not a spent operation; both auth paths readable and the exact comment absent',
        ],
    }


def guest_key_removed(receipt, possession_id):
    """True only when a fresh inspect read both auth paths and the exact comment was absent.

    A missing field is not zero. An unreadable path is not proof.
    """
    if not isinstance(receipt, dict) or receipt.get('purpose') != 'inspect':
        return False
    if receipt.get('instanceId') != INSTANCE_ID or receipt.get('status') != 'complete':
        return False
    operation = receipt.get('operationId')
    if operation in SPENT_INSPECT_IDS or operation == possession_id or not OP_ID.fullmatch(operation or ''):
        return False
    cleanup = receipt.get('cleanup') if isinstance(receipt.get('cleanup'), dict) else {}
    comment = 'roseos-yogi-possess-' + possession_id
    if cleanup.get('accessCleanup') != 'ok' or cleanup.get('authPathsRead') != 2:
        return False
    if cleanup.get('possessComment') != comment:
        return False
    return cleanup.get('possessMarkerLines') == 0


def _quiesced(probe):
    return probe.get('runnerProcess') is False and probe.get('active') in ('inactive', 'failed') \
        and probe.get('enabled') in ('disabled', 'masked') \
        and (probe.get('builder') or {}).get('instanceId') == INSTANCE_ID


def possession_run(driver, plan, identity_path, baseline=None):
    """One login, one probe, then remove only our line, wait, inspect, and STOP.

    hostKeyHandshake is the strict SSH result alone. guestAuthenticated also needs the fresh
    inspect and a confirmed stop. hostKeyPinned stays false even when the handshake succeeds:
    this tool does not record a config pin. A 412 is not retried. Start timeout and SSH failure
    still attempt STOP.
    """
    local_identity(identity_path)
    started = added = ambiguous = False
    probe_ok = False
    error = None
    instructions = None
    try:
        view = driver.describe()
        if baseline is not None and (view.get('fingerprint') != baseline.get('fingerprint')
                                     or view.get('items') != baseline.get('items')):
            raise Refused('metadata changed since preflight')
        if view.get('status') != 'TERMINATED':
            raise Refused('replacement is not TERMINATED')
        _require_base(view['items'])
        start_state = driver.start()
        if start_state == 'timeout':
            ambiguous = True
            raise Refused('start timed out and the instance state is unresolved')
        if start_state != 'started':
            raise Refused('start failed')
        started = True
        view = driver.describe()
        pre_add = {'fingerprint': view['fingerprint'], 'items': dict(view['items'])}
        added_items = add_login_line(view['items'], plan['loginLine'])
        add_state = driver.set_metadata(view['fingerprint'], added_items)
        if add_state == 'ok':
            added = True
        elif add_state == 'conflict':
            raise Refused('metadata add was not accepted')
        else:
            try:
                seen = driver.describe()
            except Refused:
                instructions = {'loginLine': plan['loginLine'],
                                'items': remove_login_line(added_items, plan['loginLine']),
                                'fingerprint': 'read the live fingerprint; do not reuse a stale value',
                                'doNotRetryBlindly': True}
                raise Refused('metadata add is unresolved')
            if plan['loginLine'] in ssh_key_lines(seen['items'].get('ssh-keys')):
                added = True
            elif seen['items'] == pre_add['items'] and seen['fingerprint'] == pre_add['fingerprint']:
                raise Refused('metadata add was not accepted')
            else:
                instructions = {'loginLine': plan['loginLine'],
                                'items': remove_login_line(seen['items'], plan['loginLine']),
                                'fingerprint': seen.get('fingerprint'), 'doNotRetryBlindly': True}
                raise Refused('metadata add is unresolved')
        probe = driver.ssh(plan['argv'], 60)
        if not _quiesced(probe):
            raise Refused('read-only probe did not show the quiesced replacement')
        probe_ok = True
    except Refused as exc:
        error = str(exc)
    meta_removed = False
    refreshed = False
    if added:
        try:
            view = driver.describe()
            kept = remove_login_line(view['items'], plan['loginLine'])
            instructions = {'loginLine': plan['loginLine'], 'items': kept, 'fingerprint': view.get('fingerprint'),
                            'doNotRetryBlindly': True}
            meta_removed = driver.set_metadata(view['fingerprint'], kept) == 'ok'
            if not meta_removed:
                error = error or 'metadata removal was not accepted; cleanup instructions kept; not retried'
        except Refused as exc:
            error = error or str(exc)
        if meta_removed:
            try:
                refreshed = driver.wait_refresh() == 'ok'
            except Refused as exc:
                error = error or str(exc)
    stopped = not (started or ambiguous)
    if started or ambiguous:
        try:
            stopped = driver.stop() == 'stopped'
            if not stopped:
                error = error or 'stop is unresolved'
        except Refused as exc:
            error = error or str(exc)
            stopped = False
    guest_removed = False
    if meta_removed and stopped and refreshed:
        try:
            guest_removed = guest_key_removed(driver.inspect(), plan['operationId'])
        except Refused as exc:
            error = error or str(exc)
    clean = bool(meta_removed and refreshed and guest_removed and stopped)
    return {
        'executed': True, 'hostKeyPinned': False, 'hostKeyHandshake': probe_ok,
        'guestAuthenticated': bool(probe_ok and clean), 'clean': clean,
        'probeOk': probe_ok, 'metadataLoginRemoved': meta_removed, 'guestKeyRemoved': guest_removed,
        'refreshed': refreshed, 'stopped': stopped, 'error': error, 'runnerEnabled': False,
        'cleanupInstructions': None if meta_removed else instructions,
    }


OPERATOR_ACCOUNT = 'grok-bots@protocols-465723.iam.gserviceaccount.com'
METADATA_URL = ('https://compute.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b/instances/'
                'roseos-yogi-builder-20261105-b/setMetadata')
DESCRIBE_TIMEOUT = 30
START_TIMEOUT = 120
STOP_TIMEOUT = 120
TOKEN_TIMEOUT = 30
METADATA_TIMEOUT = 60
METADATA_WAIT_SECONDS = 180
SSH_TIMEOUT = 60
REFRESH_SECONDS = 45
INSPECT_TIMEOUT = 600


class GcloudDriver:
    """Official gcloud for reads, start, and stop. setMetadata is one fingerprint write, same URL as recovery.

    Every subprocess has a timeout. This class performs no call until a method is used.
    """

    def __init__(self, run=subprocess.run, opener=urllib.request.urlopen, sleep=time.sleep,
                 clock=time.monotonic, possess_comment='', inspect_out=None):
        self.run, self.opener, self.sleep, self.clock = run, opener, sleep, clock
        self.possess_comment = possess_comment
        self.inspect_out = inspect_out

    def _cmd(self, argv, timeout):
        try:
            return self.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Refused('command timed out: ' + ' '.join(argv[:4])) from None

    def account(self):
        result = self._cmd(['gcloud', 'auth', 'list', '--format=json', '--quiet'], DESCRIBE_TIMEOUT)
        if result.returncode:
            raise Refused('gcloud auth list failed')
        try:
            listing = json.loads(result.stdout)
        except ValueError:
            raise Refused('gcloud auth list was not JSON') from None
        active = [row.get('account') for row in listing if isinstance(row, dict) and row.get('status') == 'ACTIVE']
        if active != [OPERATOR_ACCOUNT]:
            raise Refused('the active gcloud account is not the replacement operator account')
        return active[0]

    def describe(self):
        result = self._cmd(['gcloud', 'compute', 'instances', 'describe', INSTANCE, f'--project={PROJECT}',
                            f'--zone={ZONE}', '--format=json', '--quiet'], DESCRIBE_TIMEOUT)
        if result.returncode:
            raise Refused('describe of the replacement failed')
        try:
            seen = json.loads(result.stdout)
        except ValueError:
            raise Refused('describe was not JSON') from None
        zone = str(seen.get('zone') or '').rstrip('/').rsplit('/', 1)[-1]
        link = f'/projects/{PROJECT}/zones/{ZONE}/instances/{INSTANCE}'
        if str(seen.get('id')) != INSTANCE_ID or seen.get('name') != INSTANCE or zone != ZONE \
                or not str(seen.get('selfLink') or '').endswith(link):
            raise Refused('replacement identity does not match the saved instance id')
        items = {}
        meta = seen.get('metadata') if isinstance(seen.get('metadata'), dict) else {}
        for entry in meta.get('items') or []:
            if not isinstance(entry, dict) or not isinstance(entry.get('key'), str) or not isinstance(entry.get('value'), str):
                raise Refused('instance metadata is not a string map')
            if entry['key'] in items:
                raise Refused('instance metadata has a duplicate key')
            items[entry['key']] = entry['value']
        fingerprint = meta.get('fingerprint')
        if not isinstance(fingerprint, str) or not fingerprint:
            raise Refused('instance metadata has no fingerprint')
        address = None
        for nic in seen.get('networkInterfaces') or []:
            for access in (nic.get('accessConfigs') or []) if isinstance(nic, dict) else []:
                address = address or access.get('natIP')
        if address:
            raise Refused('replacement has an external address')
        return {'status': seen.get('status'), 'fingerprint': fingerprint, 'items': items, 'id': str(seen.get('id'))}

    def start(self):
        try:
            result = self.run(['gcloud', 'compute', 'instances', 'start', INSTANCE, f'--project={PROJECT}',
                               f'--zone={ZONE}', '--quiet'], capture_output=True, text=True, timeout=START_TIMEOUT)
        except subprocess.TimeoutExpired:
            return 'timeout'
        if result.returncode:
            return 'failed'
        try:
            view = self.describe()
        except Refused:
            return 'timeout'
        return 'started' if view.get('status') == 'RUNNING' else 'timeout'

    def stop(self):
        try:
            result = self.run(['gcloud', 'compute', 'instances', 'stop', INSTANCE, f'--project={PROJECT}',
                               f'--zone={ZONE}', '--quiet'], capture_output=True, text=True, timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            return 'unresolved'
        if result.returncode:
            return 'unresolved'
        try:
            view = self.describe()
        except Refused:
            return 'unresolved'
        return 'stopped' if view.get('status') == 'TERMINATED' else 'unresolved'

    def _post(self, url, body, token, timeout):
        request = urllib.request.Request(url, data=body, method='POST', headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        try:
            with self.opener(request, timeout=timeout) as response:
                raw, status = response.read(1 << 20), getattr(response, 'status', 200)
        except urllib.error.HTTPError as error:
            raw, status = error.read(1 << 20), error.code
        except Exception:
            raise Refused('setMetadata timed out or failed') from None
        try:
            parsed = json.loads(raw.decode()) if raw else None
        except ValueError:
            parsed = None
        return status, parsed

    def _classify_operation(self, status, parsed, expected_name=None):
        if status == 412 and expected_name is None:
            return 'conflict', None
        if status != 200 or not isinstance(parsed, dict) or parsed.get('error'):
            return ('failed' if status not in (200, None) else 'unresolved'), None
        name = parsed.get('name')
        if not isinstance(name, str) or not OPERATION_NAME.fullmatch(name) or name in SPENT_ZONE_OPERATIONS:
            return 'unresolved', None
        if expected_name is not None and name != expected_name:
            return 'failed', name
        if str(parsed.get('targetId') or '') != INSTANCE_ID:
            return 'failed', name
        if parsed.get('operationType') not in (None, '', 'setMetadata'):
            return 'failed', name
        zone = str(parsed.get('zone') or '')
        if zone and not zone.rstrip('/').endswith('/' + ZONE):
            return 'failed', name
        state = parsed.get('status')
        if state == 'DONE':
            return 'ok', name
        if state in ('PENDING', 'RUNNING'):
            return 'pending', name
        return 'failed', name

    def _wait_operation(self, name, token):
        """One zoneOperations.wait loop for the accepted handle. Not another setMetadata."""
        url = ('https://compute.googleapis.com/compute/v1/projects/' + PROJECT + '/zones/' + ZONE
               + '/operations/' + name + '/wait')
        deadline = self.clock() + METADATA_WAIT_SECONDS
        stalls = 0
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                return 'unresolved'
            timeout = min(150, max(1, int(remaining)))
            try:
                status, parsed = self._post(url, b'', token, timeout)
            except Refused:
                return 'unresolved'
            kind, _seen = self._classify_operation(status, parsed, expected_name=name)
            if kind == 'ok':
                return 'ok'
            if kind != 'pending':
                return 'unresolved'
            before = remaining
            pause = min(2.0, max(0.0, deadline - self.clock() - 1))
            if pause:
                self.sleep(pause)
            if deadline - self.clock() >= before - 0.001:
                stalls += 1
                if stalls >= 3:
                    return 'unresolved'

    def set_metadata(self, fingerprint, items):
        token_result = self._cmd(['gcloud', 'auth', 'print-access-token'], TOKEN_TIMEOUT)
        token = token_result.stdout.strip() if token_result.returncode == 0 else ''
        if not token or any(char in token for char in '\n\r\t '):
            raise Refused('access token was not usable')
        body = json.dumps({'fingerprint': fingerprint,
                           'items': [{'key': key, 'value': items[key]} for key in sorted(items)]}).encode()
        try:
            status, parsed = self._post(METADATA_URL, body, token, METADATA_TIMEOUT)
        except Refused:
            return 'unresolved'
        kind, name = self._classify_operation(status, parsed)
        if kind == 'pending':
            return self._wait_operation(name, token)
        if kind == 'ok':
            return 'ok'
        if kind == 'conflict':
            return 'conflict'
        if kind == 'failed':
            return 'failed'
        return 'unresolved'

    def ssh(self, argv, timeout):
        if timeout != SSH_TIMEOUT:
            raise Refused('ssh timeout is not 60 seconds')
        try:
            result = self.run(argv, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Refused('ssh probe timed out') from None
        if result.returncode:
            raise Refused('ssh probe failed')
        try:
            return json.loads(result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout)
        except ValueError:
            raise Refused('ssh probe was not JSON') from None

    def wait_refresh(self):
        self.sleep(REFRESH_SECONDS)
        return 'ok'

    def inspect(self):
        script = str(Path(__file__).resolve().parents[0] / 'yogi_replacement_recover.py')
        out = self.inspect_out or str(Path(os.getcwd()) / 'possess-inspect')
        plan = self._cmd([sys.executable, script, 'plan', '--mode', 'inspect', '--operator', 'possession',
                          '--out', out, '--possess-comment', self.possess_comment], DESCRIBE_TIMEOUT)
        if plan.returncode:
            raise Refused('fresh inspect plan failed')
        pre = self._cmd([sys.executable, script, 'preflight', '--op', out, '--confirm-instance', INSTANCE_ID,
                         '--execute'], INSPECT_TIMEOUT)
        if pre.returncode:
            raise Refused('fresh inspect preflight failed')
        apply = self._cmd([sys.executable, script, 'apply', '--op', out, '--confirm-instance', INSTANCE_ID,
                           '--execute'], INSPECT_TIMEOUT)
        if apply.returncode and not (Path(out) / 'outcome.json').is_file():
            raise Refused('fresh inspect did not finish')
        try:
            outcome = json.loads((Path(out) / 'outcome.json').read_text())
        except (OSError, ValueError):
            raise Refused('fresh inspect outcome is unreadable') from None
        receipt = outcome.get('guestReceipt')
        if not isinstance(receipt, dict):
            raise Refused('fresh inspect has no receipt')
        return receipt


def possession_preflight(driver):
    """Account, replacement id, and the reviewed metadata baseline. No mutation."""
    driver.account()
    view = driver.describe()
    if view.get('status') != 'TERMINATED' or view.get('id') != INSTANCE_ID:
        raise Refused('replacement is not the terminated saved instance')
    if view.get('fingerprint') != POSSESSION_FINGERPRINT or view.get('items') != POSSESSION_BASE:
        raise Refused('metadata does not match the reviewed baseline')
    _require_base(view['items'])
    return {'fingerprint': view['fingerprint'], 'items': dict(view['items']), 'status': view['status']}


def execute_handshake(api_path, client_path, identity, out, driver=None, operation_id=None, environ=None, now=None):
    """Runnable operator entry. Tests pass a fake driver. The default driver is not used by tests."""
    identity = local_identity(identity)
    driver = driver or GcloudDriver()
    baseline = possession_preflight(driver)
    operation_id = operation_id or __import__('secrets').token_hex(16)
    api = json.loads(Path(api_path).read_text())
    plan = possession_plan(api, Path(client_path).read_text(), operation_id, current_items=baseline['items'],
                           environ=environ, now=now, identity_path=identity)
    driver.possess_comment = 'roseos-yogi-possess-' + operation_id
    driver.inspect_out = str(Path(out) / 'inspect')
    result = possession_run(driver, plan, identity, baseline=baseline)
    destination = Path(out)
    destination.mkdir(mode=0o700, exist_ok=True)
    (destination / 'possession-plan.json').write_text(json.dumps(plan, indent=2, sort_keys=True) + '\n')
    (destination / 'possession-outcome.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


def write_possession_plan(api_path, client_path, out, operation_id=None, environ=None, now=None):
    operation_id = operation_id or __import__('secrets').token_hex(16)
    api = json.loads(Path(api_path).read_text())
    plan = possession_plan(api, Path(client_path).read_text(), operation_id, environ=environ, now=now)
    destination = Path(out)
    destination.mkdir(mode=0o700)
    (destination / 'possession-plan.json').write_text(json.dumps(plan, indent=2, sort_keys=True) + '\n')
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description='Strict IAP possession plan. --execute is the operator run; tests inject a driver.')
    parser.add_argument('action', choices=('handshake-plan', 'handshake'))
    parser.add_argument('--hostkeys', required=True)
    parser.add_argument('--client-public', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--identity', help='local mode 0600 private key; required with handshake --execute')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'handshake' and args.execute:
            if not args.identity:
                parser.error('handshake --execute needs --identity')
            result = execute_handshake(args.hostkeys, args.client_public, args.identity, args.out)
            print(json.dumps(result, sort_keys=True))
            return 0 if result.get('guestAuthenticated') else 1
        print(json.dumps(write_possession_plan(args.hostkeys, args.client_public, args.out), sort_keys=True))
    except (Refused, OSError, ValueError) as error:
        print(f'possession plan stopped: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
