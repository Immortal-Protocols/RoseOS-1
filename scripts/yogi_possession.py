#!/usr/bin/env python3
"""Offline plan and cleanup rules for one strict IAP possession check.

Does not call GCP or SSH. Does not record builder.direct.hostKey. The private login key
stays on the operator machine. pixelbuild is not given sudo or a new role.
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
import sys

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
SPENT_INSPECT_IDS = {
    'e75b0fd782e43f585c621c74f95bfa90',
    '5cf6fe9a62880b89ee6e7e822a0379d2',
    '20ef6008b1ef78137d201de13176af42',
    '638e6e9039e41b7531697bb45066a616',
}
OP_ID = re.compile(r'[0-9a-f]{32}')
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
        'cleanup': 'remove only this ssh-keys line, fresh inspect must show possessMarkerLines 0, then STOP',
        'fingerprint': 'one write, 412 is not retried',
        'steps': [
            'describe TERMINATED; keep every metadata key; base values must stay',
            'start once',
            'setMetadata the current fingerprint with only loginLine appended; do not retry 412',
            'one read-only ssh probe, timeout 60, identity mode 0600, no sudo',
            'setMetadata the new fingerprint with only that exact line removed',
            'fresh inspect, not a spent operation; possessMarkerLines must be 0',
            'stop whenever the VM was started',
        ],
    }


def guest_key_removed(receipt, possession_id):
    """True only for a fresh inspect receipt that counted zero possession markers."""
    if not isinstance(receipt, dict) or receipt.get('purpose') != 'inspect':
        return False
    if receipt.get('instanceId') != INSTANCE_ID:
        return False
    operation = receipt.get('operationId')
    if operation in SPENT_INSPECT_IDS or operation == possession_id or not OP_ID.fullmatch(operation or ''):
        return False
    cleanup = receipt.get('cleanup') if isinstance(receipt.get('cleanup'), dict) else {}
    return cleanup.get('possessMarkerLines') == 0


def _quiesced(probe):
    return probe.get('runnerProcess') is False and probe.get('active') in ('inactive', 'failed') \
        and probe.get('enabled') in ('disabled', 'masked') \
        and (probe.get('builder') or {}).get('instanceId') == INSTANCE_ID


def possession_run(driver, plan, identity_path):
    """One login, one probe, then remove only our line, inspect, and STOP.

    A metadata write uses the fingerprint from the describe just before it. A 412 is not retried.
    guestAuthenticated is true only when the probe and a fresh inspect both succeed. hostKeyPinned
    stays false. The VM is stopped whenever it was started.
    """
    local_identity(identity_path)
    started = added = False
    probe_ok = False
    error = None
    try:
        view = driver.describe()
        if view.get('status') != 'TERMINATED':
            raise Refused('replacement is not TERMINATED')
        _require_base(view['items'])
        if driver.start() != 'started':
            raise Refused('start failed')
        started = True
        view = driver.describe()
        added_items = add_login_line(view['items'], plan['loginLine'])
        if driver.set_metadata(view['fingerprint'], added_items) != 'ok':
            raise Refused('metadata add was not accepted')
        added = True
        probe = driver.ssh(plan['argv'], 60)
        if not _quiesced(probe):
            raise Refused('read-only probe did not show the quiesced replacement')
        probe_ok = True
    except Refused as exc:
        error = str(exc)
    meta_removed = False
    stopped = not started
    guest_removed = False
    if added:
        try:
            view = driver.describe()
            kept = remove_login_line(view['items'], plan['loginLine'])
            meta_removed = driver.set_metadata(view['fingerprint'], kept) == 'ok'
        except Refused as exc:
            error = error or str(exc)
    if started:
        try:
            stopped = driver.stop() == 'stopped'
        except Refused as exc:
            error = error or str(exc)
            stopped = False
    if meta_removed and stopped:
        try:
            guest_removed = guest_key_removed(driver.inspect(), plan['operationId'])
        except Refused as exc:
            error = error or str(exc)
    return {
        'executed': True, 'hostKeyPinned': False,
        'guestAuthenticated': bool(probe_ok and meta_removed and guest_removed and stopped),
        'probeOk': probe_ok, 'metadataLoginRemoved': meta_removed, 'guestKeyRemoved': guest_removed,
        'stopped': stopped, 'error': error, 'runnerEnabled': False,
    }


def write_possession_plan(api_path, client_path, out, operation_id=None, environ=None, now=None):
    operation_id = operation_id or __import__('secrets').token_hex(16)
    api = json.loads(Path(api_path).read_text())
    plan = possession_plan(api, Path(client_path).read_text(), operation_id, environ=environ, now=now)
    destination = Path(out)
    destination.mkdir(mode=0o700)
    (destination / 'possession-plan.json').write_text(json.dumps(plan, indent=2, sort_keys=True) + '\n')
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description='Write a strict IAP possession plan. Does not call GCP or SSH.')
    parser.add_argument('action', choices=('handshake-plan',))
    parser.add_argument('--hostkeys', required=True)
    parser.add_argument('--client-public', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(write_possession_plan(args.hostkeys, args.client_public, args.out), sort_keys=True))
    except (Refused, OSError, ValueError) as error:
        print(f'possession plan stopped: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
