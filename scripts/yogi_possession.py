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
import signal
import subprocess
import sys
import tempfile
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
    'a5848d669170305750c61bc3effdec9f',
    '41e30832b719be117c73c13c0459cb44',
    'e0d23fb40aacb99f8278965a500ed5b8',
}
# Recorded rotate-publish id prefix. The full 32 hex id was not saved; no fresh binding may reuse it.
SPENT_BINDING_PREFIXES = ('ce79ffe4',)
OP_ID = re.compile(r'[0-9a-f]{32}')
OPERATION_NAME = re.compile(r'operation-[0-9]{10,16}-[0-9a-f]{8,20}-[0-9a-f]{6,20}-[0-9a-f]{6,20}')
SPENT_ZONE_OPERATIONS = frozenset({
    'operation-1794025967141-65fa8387d4384-e9bed427-7ed969ab',
    'operation-1794025969883-65fa838a71873-581e64cf-6e23ae4e',
    'operation-1794026872649-65fa86e763554-67c3c575-aa4d2c66',
    'operation-1794026874731-65fa86e95fb00-d547145e-45241669',
    'operation-1794027065214-65fa879f086ca-4cf61ef5-4480e200',
    'operation-1794027069962-65fa87a38f91f-6bc72969-2fdd5f07',
    'operation-1794066928521-65fb1c1fa4fa7-28daba8e-205f8105',
    'operation-1794066937204-65fb1c27ed0fd-78949f7d-9a3a6b1b',
    'operation-1794068119100-65fb208f11af8-c71e25d6-b4bccbbf',
    'operation-1794068240866-65fb210331ca7-8bb70fef-dd412780',
    'operation-1794069573860-65fb25fa6fb6b-2cdbb51b-2d04c90d',
    'operation-1794069640347-65fb2639d7df7-42a82e9e-f9247439',
    'operation-1794073461023-65fb347586360-98ed018a-b7696fb8',
    'operation-1794073473856-65fb3481c3563-3c8452c4-3d020ae2',
    'operation-1794073480074-65fb3487b162f-8c7d56fd-4f86d627',
    'operation-1794073528347-65fb34b5baa80-6178a243-76f2fb80',
    'operation-1794073566065-65fb34d9b3373-1e7494ff-6a693180',
    'operation-1794073568394-65fb34dbebee4-180141f9-b48db238',
    'operation-1794073760446-65fb3593139e7-3bb91b6c-f8cbdb4b',
    'operation-1794073765555-65fb3597f2ddd-7630e10f-130475d1',
    'operation-1794076322017-65fb3f1dfb219-b6636d67-10ace58b',
    'operation-1794076333827-65fb3f293e725-1ff9b97f-558b7ddc',
    'operation-1794076401065-65fb3f695dfd9-348dbe7e-219b3232',
    'operation-1794076449611-65fb3f97a9f1b-e11156b2-49f18218',
    'operation-1794076486246-65fb3fba99f04-a7ebd856-0dca3c32',
    'operation-1794076488270-65fb3fbc8843a-583c3cf8-47106870',
    'operation-1794076679125-65fb40728baa5-eeb25437-5fc4b997',
    'operation-1794076684226-65fb407768de8-0775bed1-3c864e58',
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


class DescribeRefused(Refused):
    """One instance describe did not return a usable view. The code is ours; it is not the response body."""

    def __init__(self, code, returncode=None, status='', stdout_class=''):
        super().__init__(code)
        self.code = code
        self.returncode = returncode
        self.status = status
        self.stdout_class = stdout_class
        self.network = None
        self.terminal = code not in ('describe-exit', 'describe-not-json', 'describe-timeout')


def _stdout_class(text):
    stripped = (text or '').lstrip()
    if not stripped:
        return 'empty'
    if stripped[0] == '{':
        return 'object'
    if stripped[0] == '[':
        return 'array'
    return 'other'


def _config_row(row, address_key):
    if not isinstance(row, dict):
        return None
    return {
        'name': row.get('name') if isinstance(row.get('name'), str) else '',
        'type': row.get('type') if isinstance(row.get('type'), str) else '',
        'networkTier': row.get('networkTier') if isinstance(row.get('networkTier'), str) else '',
        'addressPresent': bool(row.get(address_key)),
    }


def network_observation(seen):
    """Observed access configs only. An absent address is not recorded as an allocation."""
    nics = []
    for nic in (seen or {}).get('networkInterfaces') or []:
        if not isinstance(nic, dict):
            continue
        access = [row for row in (_config_row(item, 'natIP') for item in nic.get('accessConfigs') or []) if row]
        ipv6 = [row for row in (_config_row(item, 'externalIpv6') for item in nic.get('ipv6AccessConfigs') or []) if row]
        nics.append({
            'name': nic.get('name') if isinstance(nic.get('name'), str) else '',
            'stackType': nic.get('stackType') if isinstance(nic.get('stackType'), str) else '',
            'network': nic.get('network') if isinstance(nic.get('network'), str) else '',
            'subnetwork': nic.get('subnetwork') if isinstance(nic.get('subnetwork'), str) else '',
            'accessConfigs': access,
            'ipv6AccessConfigs': ipv6,
        })
    return {'observed': True, 'allocationInferred': False, 'nics': nics}


def external_exposure(network):
    """Observed config versus an address actually present in this read.

    `describe-external-nat` means a ONE_TO_ONE_NAT config was observed and no address was.
    It does not assert that an address is allocated. A present address is `describe-external-address`.
    """
    if not isinstance(network, dict):
        return None
    nat = ipv6 = address = False
    for nic in network.get('nics') or []:
        for row in nic.get('accessConfigs') or []:
            nat = True
            address = address or bool(row.get('addressPresent'))
        for row in nic.get('ipv6AccessConfigs') or []:
            ipv6 = True
            address = address or bool(row.get('addressPresent'))
    if address:
        return 'describe-external-address'
    if nat:
        return 'describe-external-nat'
    if ipv6:
        return 'describe-external-ipv6'
    return None


REVIEWED_TAGS = ('roseos-yogi-b-20261105', 'roseos-yogi-builder')
IAP_SOURCE = '35.235.240.0/20'
FIREWALL_PERMISSION = 'compute.instances.getEffectiveFirewalls'
_RULE_KEYS = frozenset({
    'allowed', 'denied', 'creationTimestamp', 'description', 'direction', 'disabled', 'id', 'kind',
    'logConfig', 'name', 'network', 'priority', 'selfLink', 'sourceRanges', 'sourceTags',
    'sourceServiceAccounts', 'targetTags', 'targetServiceAccounts', 'destinationRanges',
})


def network_drift(network, tags):
    """The reviewed nic0 External NAT shape. addressPresent is not part of identity."""
    if list(tags or []) != list(REVIEWED_TAGS) and set(tags or []) != set(REVIEWED_TAGS):
        return 'firewall-tag-drift'
    if set(tags or []) != set(REVIEWED_TAGS):
        return 'firewall-tag-drift'
    nics = (network or {}).get('nics') if isinstance(network, dict) else None
    if not isinstance(nics, list) or len(nics) != 1:
        return 'network-drift'
    nic = nics[0]
    if nic.get('name') != 'nic0' or nic.get('stackType') != 'IPV4_ONLY':
        return 'network-drift'
    if not str(nic.get('network') or '').endswith('/global/networks/default'):
        return 'network-drift'
    if not str(nic.get('subnetwork') or '').endswith('/regions/us-west2/subnetworks/default'):
        return 'network-drift'
    if nic.get('ipv6AccessConfigs'):
        return 'describe-external-ipv6'
    access = nic.get('accessConfigs') or []
    if len(access) != 1:
        return 'network-drift'
    row = access[0]
    if (row.get('name'), row.get('type'), row.get('networkTier')) != ('External NAT', 'ONE_TO_ONE_NAT', 'PREMIUM'):
        return 'network-drift'
    return None


def network_identity(view):
    """Comparable nic/tag shape. The ephemeral natIP flag is omitted."""
    network = view.get('network') if isinstance(view, dict) else None
    nics = []
    for nic in (network or {}).get('nics') or []:
        nics.append({
            'name': nic.get('name'), 'stackType': nic.get('stackType'),
            'network': nic.get('network'), 'subnetwork': nic.get('subnetwork'),
            'accessConfigs': [{key: row.get(key) for key in ('name', 'type', 'networkTier')}
                              for row in nic.get('accessConfigs') or []],
            'ipv6AccessConfigs': [{key: row.get(key) for key in ('name', 'type', 'networkTier')}
                                  for row in nic.get('ipv6AccessConfigs') or []],
        })
    return {'tags': sorted(view.get('tags') or []), 'nics': nics}


def _string_list(value, allow_missing=True):
    if value is None and allow_missing:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return tuple(value)


def _specs(value, allow_missing=True):
    if value is None and allow_missing:
        return ()
    if not isinstance(value, list):
        return None
    found = []
    for spec in value:
        if not isinstance(spec, dict) or set(spec) - {'IPProtocol', 'ports'}:
            return None
        proto = spec.get('IPProtocol')
        if not isinstance(proto, str) or not proto:
            return None
        ports = spec.get('ports')
        if ports is None:
            found.append((proto, None))
            continue
        if not isinstance(ports, list) or not all(isinstance(port, str) for port in ports):
            return None
        found.append((proto, tuple(ports)))
    return tuple(found)


def _covers_ssh(specs):
    for proto, ports in specs:
        if proto == 'all':
            return True
        if proto != 'tcp':
            continue
        if ports is None:
            return True
        for port in ports:
            if port == '22':
                return True
            if '-' in port:
                left, right = port.split('-', 1)
                if left.isdigit() and right.isdigit() and int(left) <= 22 <= int(right):
                    return True
    return False


def _parse_rule(rule):
    if not isinstance(rule, dict) or set(rule) - _RULE_KEYS:
        return None
    log = rule.get('logConfig')
    if log is not None and (not isinstance(log, dict) or set(log) != {'enable'} or not isinstance(log.get('enable'), bool)):
        return None
    direction = rule.get('direction')
    if direction not in ('INGRESS', 'EGRESS') or not isinstance(rule.get('name'), str):
        return None
    if not isinstance(rule.get('priority'), int):
        return None
    disabled = rule.get('disabled')
    if disabled is None:
        disabled = False
    if not isinstance(disabled, bool):
        return None
    allow, deny = _specs(rule.get('allowed')), _specs(rule.get('denied'))
    source_ranges = _string_list(rule.get('sourceRanges'))
    source_tags = _string_list(rule.get('sourceTags'))
    source_accounts = _string_list(rule.get('sourceServiceAccounts'))
    target_tags = _string_list(rule.get('targetTags'))
    if _string_list(rule.get('destinationRanges')) is None or _string_list(rule.get('targetServiceAccounts')) is None:
        return None
    if None in (allow, deny, source_ranges, source_tags, source_accounts, target_tags):
        return None
    network = rule.get('network')
    if network is not None and (not isinstance(network, str) or not network.endswith('/global/networks/default')):
        return None
    return {
        'name': rule['name'], 'direction': direction, 'priority': rule['priority'], 'disabled': disabled,
        'allow': allow, 'deny': deny, 'sourceRanges': source_ranges, 'sourceTags': source_tags,
        'sourceServiceAccounts': source_accounts, 'targetTags': target_tags,
        'ipv6': any(':' in item for item in source_ranges),
        'ssh': _covers_ssh(allow),
    }


def _baseline_iap(rule):
    return (rule['name'] == 'yogi-b-20261105-iap-ssh' and rule['direction'] == 'INGRESS' and not rule['disabled']
            and rule['priority'] == 900 and rule['sourceRanges'] == (IAP_SOURCE,) and not rule['sourceTags']
            and not rule['sourceServiceAccounts'] and rule['targetTags'] == ('roseos-yogi-b-20261105',)
            and rule['allow'] == (('tcp', ('22',)),) and not rule['deny'])


def _baseline_deny(rule):
    return (rule['name'] == 'yogi-b-20261105-deny-ingress' and rule['direction'] == 'INGRESS' and not rule['disabled']
            and rule['priority'] == 901 and rule['sourceRanges'] == ('0.0.0.0/0',) and not rule['sourceTags']
            and not rule['sourceServiceAccounts'] and rule['targetTags'] == ('roseos-yogi-b-20261105',)
            and rule['deny'] == (('all', None),) and not rule['allow'])


def evaluate_inbound(document):
    """IAP-only inbound under the reviewed nic0 effective-firewall read.

    Priority 901 deny-all from 0.0.0.0/0 shadows lower IPv4 allows, including 0.0.0.0/0 at 65534
    and the priority 1000 connector rules. It does not shadow IPv6. A nonempty or unexpected
    policy field fails closed. Egress rules are not an inbound decision.
    """
    if not isinstance(document, dict) or 'firewalls' not in document or not isinstance(document.get('firewalls'), list):
        return {'ok': False, 'code': 'firewall-unknown-shape', 'shadowed': [], 'ruleCount': 0}
    extra = set(document) - {'firewalls'}
    if extra:
        return {'ok': False, 'code': 'firewall-unreviewed-policy', 'shadowed': [], 'ruleCount': len(document['firewalls'])}
    rules = []
    for raw in document['firewalls']:
        parsed = _parse_rule(raw)
        if parsed is None:
            return {'ok': False, 'code': 'firewall-unknown-shape', 'shadowed': [], 'ruleCount': len(document['firewalls'])}
        rules.append(parsed)
    if not any(_baseline_iap(rule) for rule in rules) or not any(_baseline_deny(rule) for rule in rules):
        return {'ok': False, 'code': 'firewall-baseline', 'shadowed': [], 'ruleCount': len(rules)}
    shadowed = []
    for rule in rules:
        if rule['disabled'] or rule['direction'] == 'EGRESS' or _baseline_iap(rule) or _baseline_deny(rule):
            continue
        if rule['priority'] < 900 or rule['priority'] in (900, 901):
            return {'ok': False, 'code': 'firewall-unreviewed-priority', 'shadowed': shadowed, 'ruleCount': len(rules)}
        if rule['ipv6']:
            return {'ok': False, 'code': 'firewall-public-inbound', 'shadowed': shadowed, 'ruleCount': len(rules)}
        shadowed.append(rule['name'])
    return {'ok': True, 'code': None, 'shadowed': sorted(shadowed), 'ruleCount': len(rules),
            'permission': FIREWALL_PERMISSION, 'nic': 'nic0'}


def effective_firewall_argv():
    return ['gcloud', 'compute', 'instances', 'network-interfaces', 'get-effective-firewalls', INSTANCE,
            '--network-interface=nic0', f'--zone={ZONE}', f'--project={PROJECT}', '--format=json', '--quiet']


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
    if any(token in command for token in ('|', '||', '&&', ';')):
        raise Refused('possession ssh is not the strict read-only command')
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


def bind_execution_argv(argv, known_hosts, identity_path=None):
    """Replace plan placeholders with the files SSH will actually open.

    The plan may display KNOWN_HOSTS_FILE and IDENTITY_FILE. Execution must not.
    The caller unlinks the known-hosts file when the SSH attempt returns.
    """
    argv = list(argv or [])
    if identity_path:
        argv = [identity_path if arg == 'IDENTITY_FILE' else arg for arg in argv]
    path = None
    if any(isinstance(arg, str) and arg.endswith('=KNOWN_HOSTS_FILE') for arg in argv):
        text = known_hosts if isinstance(known_hosts, str) else ''
        if 'ssh-ed25519 ' not in text or '\n' not in text:
            raise Refused('known hosts for execution are not the API lines')
        fd, path = tempfile.mkstemp(prefix='yogi-known-')
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, text.encode())
        except OSError:
            os.close(fd)
            os.unlink(path)
            raise Refused('known hosts file was not written') from None
        os.close(fd)
        argv = [('UserKnownHostsFile=' + path) if isinstance(arg, str) and arg.endswith('=KNOWN_HOSTS_FILE') else arg
                for arg in argv]
    for arg in argv:
        if isinstance(arg, str) and ('KNOWN_HOSTS_FILE' in arg or arg == 'IDENTITY_FILE'):
            if path:
                os.unlink(path)
            raise Refused('ssh argv still has a display placeholder')
    return argv, path


_SECRET_TEXT = re.compile(
    r'ya29\.[A-Za-z0-9_\-]*|Bearer[ \t]+[A-Za-z0-9._\-+/=]*|'
    r'access_token[\'"\s:=]+[A-Za-z0-9._\-+/=]*|Authorization:[ \t]*\S+',
    re.I)
_HOST_KEY_UNKNOWN = re.compile(
    r"^No ED25519 host key is known for .+ and you have requested strict checking\.$")


def redact_text(text):
    """Remove credential material from a whole buffer, including a BEGIN block with no END line."""
    if isinstance(text, bytes):
        text = text.decode('utf-8', 'replace')
    if not isinstance(text, str):
        return ''
    text = re.sub(r'-----BEGIN [A-Z0-9 -]*-*[^\n]*\n?.*?(?:-----END [A-Z0-9 -]*-*[^\n]*|$)',
                  '[redacted]', text, flags=re.S)
    kept = []
    for line in text.splitlines():
        if '-----BEGIN' in line or 'PRIVATE KEY' in line or 'OPENSSH PRIVATE' in line:
            continue
        if len(line) >= 80 and re.fullmatch(r'[A-Za-z0-9+/=]+', line.strip()):
            continue
        kept.append(_SECRET_TEXT.sub('[redacted]', line))
    return '\n'.join(kept)


def cap_diagnostic(text, limit=2048):
    """Keep a prefix and the fatal tail. DEBUG setup lines must not push the last lines out."""
    if len(text) <= limit:
        return text
    tail_budget = max(1, limit // 2)
    head_budget = max(1, limit - tail_budget - 48)
    head = text[:head_budget]
    split = head.rfind('\n')
    if split >= 0:
        head = head[:split]
    tail = text[-tail_budget:]
    split = tail.find('\n')
    if split >= 0:
        tail = tail[split + 1:]
    omitted = max(0, len(text) - len(head) - len(tail))
    capped = head + f'\n[omitted {omitted} bytes]\n' + tail
    if len(capped) > limit:
        capped = capped[-limit:]
        split = capped.find('\n')
        if split >= 0:
            capped = capped[split + 1:]
    return capped


def sanitize_text(text, limit=2048):
    """Redact, then keep the start and the tail inside the cap."""
    return cap_diagnostic(redact_text(text), limit)


def sanitize_argv(argv):
    clean = []
    for arg in argv or []:
        if not isinstance(arg, str) or any(mark in arg for mark in (
                '-----BEGIN', 'PRIVATE KEY', 'ya29.', 'Bearer ', 'access_token')):
            clean.append('[redacted]')
        else:
            clean.append(arg)
    return clean


def _none():
    return {'serverKeyAccepted': False, 'loginAccepted': False, 'evidence': 'none'}


def _host_key_conflict(lines):
    """An observed different key. A missing known_hosts file is not this."""
    for line in lines:
        if 'REMOTE HOST IDENTIFICATION HAS CHANGED' in line or line.startswith('Offending '):
            return True
    return False


def _known_hosts_path(argv):
    for arg in argv or []:
        if isinstance(arg, str) and arg.startswith('UserKnownHostsFile='):
            return arg.split('=', 1)[1]
    return ''


def _known_hosts_problem(lines, known_path=''):
    """Only the executed UserKnownHostsFile, or OpenSSH's strict-checking fatal line.

    A debug fopen of some other missing file is not this failure.
    """
    path = known_path or ''
    fatal_missing = False
    for line in lines:
        if _HOST_KEY_UNKNOWN.fullmatch(line):
            fatal_missing = True
        if not path or path not in line:
            continue
        lowered = line.lower()
        if 'no such file' in lowered or 'not found' in lowered:
            return 'known-hosts-missing'
        if 'permission denied' in lowered or 'bad owner or permissions' in lowered:
            return 'known-hosts-unreadable'
    if fatal_missing:
        return 'known-hosts-missing'
    return None


def _identity_rejected(lines):
    """The client refused the identity file. This is not a known_hosts permission error."""
    for line in lines:
        if 'UNPROTECTED PRIVATE KEY' in line:
            return True
        if line.startswith('Load key ') and 'bad permissions' in line:
            return True
        if line.startswith('Warning: Identity file ') and 'not accessible' in line:
            return True
    return False


def _host_accepted(lines):
    return any('is known and matches the ED25519 host key.' in line for line in lines)


def _disqualified(lines):
    """Local and pre-auth client failures. They are not userauth proof."""
    for line in lines:
        if line.startswith('Warning: Identity file ') and 'not accessible' in line:
            return True
        if 'Could not resolve hostname' in line or 'no matching' in line or line.startswith('Unable to negotiate'):
            return True
    return False


def _client_authenticated(lines):
    """Lines OpenSSH 9.6 actually writes. VERBOSE does not write the debug1 host line."""
    for line in lines:
        if 'Authentication succeeded (publickey)' in line:
            return True
        if line.startswith('Authenticated to ') and 'using "publickey"' in line:
            return True
    return False


def _userauth_denied(lines, user, alias):
    """Exact denial after the DEBUG host-key line. VERBOSE on OpenSSH 9.6 does not emit that line."""
    if not user or not alias or _client_authenticated(lines):
        return False
    accepted = "debug1: Host '" + alias + "' is known and matches the ED25519 host key."
    denied = re.compile(r'^' + re.escape(user) + r'@' + re.escape(alias)
                        + r': Permission denied \(publickey(?:,[A-Za-z0-9-]+)*\)\.$')
    seen_host = False
    for line in lines:
        if line == accepted:
            seen_host = True
        elif seen_host and denied.fullmatch(line):
            return True
    return False


def classify_ssh(returncode, lines, user, alias, stderr_lines=None, known_path=''):
    """Evidence from the OpenSSH client log and the process exit.

    Mixed stderr cannot prove a session. It can only disqualify one: OpenSSH 9.6 writes
    `Warning: Identity file ... not accessible` on stderr, not in the -E log. Exit 0 is a
    completed session. Exit 1 through 254 is the remote command. Exit 255 is an
    authentication failure only when the DEBUG host-key line precedes the exact denial.
    A timeout is never upgraded by whatever the log already contains.
    """
    lines = [line for line in (lines or []) if isinstance(line, str)]
    extra = [line for line in (stderr_lines or []) if isinstance(line, str)]
    if returncode is None:
        return 'timeout', _none()
    if not isinstance(returncode, int) or returncode < 0:
        return 'unproven', _none()
    conflict = _host_key_conflict(lines)
    host_ok = _host_accepted(lines)
    known = None if host_ok else _known_hosts_problem(lines, known_path)
    identity = _identity_rejected(lines)
    succeeded = _client_authenticated(lines)
    blocked = _disqualified(lines) or _disqualified(extra)
    if conflict and succeeded:
        return 'unproven', _none()
    if conflict and returncode != 0:
        return 'host-key-mismatch', _none()
    if known and returncode != 0:
        return known, _none()
    if identity and returncode != 0 and not (isinstance(returncode, int) and 1 <= returncode <= 254):
        evidence = _none()
        if host_ok:
            evidence = {'serverKeyAccepted': True, 'loginAccepted': False, 'evidence': 'none'}
        return 'identity-unreadable', evidence
    if returncode == 0:
        if conflict or known or identity or blocked:
            return 'unproven', _none()
        return 'success', {'serverKeyAccepted': True, 'loginAccepted': True, 'evidence': 'openssh-session'}
    if 1 <= returncode <= 254:
        if blocked:
            return 'unproven', _none()
        return 'remote-command', {'serverKeyAccepted': True, 'loginAccepted': True,
                                  'evidence': 'openssh-remote-exit'}
    if returncode == 255 and not succeeded and not blocked and _userauth_denied(lines, user, alias):
        return 'auth-failed', {'serverKeyAccepted': True, 'loginAccepted': False,
                               'evidence': 'openssh-userauth-denied'}
    return 'unproven', _none()


def classify_tunnel(text):
    """Only IAP backend 4003 is retryable. IAM and every other tunnel error are fatal."""
    lowered = text.lower() if isinstance(text, str) else ''
    if '4003' in lowered and 'failed to connect to backend' in lowered:
        return 'backend-4003'
    if 'permission_denied' in lowered or 'permission denied' in lowered or 'iam_permission' in lowered:
        return 'iam'
    return 'fatal'


def ssh_evidence(kind):
    """Positive evidence is only a completed session, a non-255 remote exit, or the exact denial."""
    if kind in ('success', 'not-json'):
        return {'serverKeyAccepted': True, 'loginAccepted': True, 'evidence': 'openssh-session'}
    if kind == 'remote-command':
        return {'serverKeyAccepted': True, 'loginAccepted': True, 'evidence': 'openssh-remote-exit'}
    if kind == 'auth-failed':
        return {'serverKeyAccepted': True, 'loginAccepted': False, 'evidence': 'openssh-userauth-denied'}
    return _none()


def complete_log_text(raw, bound):
    """Complete lines from a client log. A log past the bound keeps a prefix and the tail."""
    if not isinstance(raw, (bytes, bytearray)):
        raw = b''
    if len(raw) <= bound:
        return raw.decode('utf-8', 'replace'), False
    head = raw[:bound // 2]
    tail = raw[-(bound // 2):]
    if b'\n' in head:
        head = head.rsplit(b'\n', 1)[0] + b'\n'
    else:
        head = b''
    if b'\n' in tail:
        tail = tail.split(b'\n', 1)[1]
    else:
        tail = b''
    return (head + tail).decode('utf-8', 'replace'), True


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


def spent_binding(value):
    return value in SPENT_INSPECT_IDS or any(str(value).startswith(prefix) for prefix in SPENT_BINDING_PREFIXES)


def login_material(line):
    """The public blob the guest agent keeps even if it rewrites the google-ssh JSON."""
    parts = str(line).split(' ')
    if len(parts) < 3 or not parts[0].endswith(':ssh-ed25519') or not parts[1]:
        raise Refused('login line is not the expiring pixelbuild key')
    return parts[1]


def installed_key_text(line):
    """Text the current guest agent writes: everything after the first colon, unchanged.

    utils.GetUserKey returns that remainder. CheckExpiredKey reads expireOn and does not
    rebuild the line, so the JSON comment stays. A rebuilt line that drops the comment
    still contains this blob.
    """
    user, sep, key = str(line).partition(':')
    if sep != ':' or user != POSSESSION_USER or 'ssh-ed25519 ' not in key:
        raise Refused('login line is not the expiring pixelbuild key')
    return key


def guest_key_removed(receipt, possession_id, material):
    """True only when a fresh inspect read both auth paths and neither the comment nor the blob remained.

    A missing field is not zero. An unreadable path is not proof. A spent inspect id is not proof.
    """
    if not isinstance(receipt, dict) or receipt.get('purpose') != 'inspect':
        return False
    if receipt.get('instanceId') != INSTANCE_ID or receipt.get('status') != 'complete':
        return False
    operation = receipt.get('operationId')
    if spent_binding(operation) or operation == possession_id or not OP_ID.fullmatch(operation or ''):
        return False
    cleanup = receipt.get('cleanup') if isinstance(receipt.get('cleanup'), dict) else {}
    comment = 'roseos-yogi-possess-' + possession_id
    if cleanup.get('accessCleanup') != 'ok' or cleanup.get('authPathsRead') != 2:
        return False
    if cleanup.get('possessComment') != comment or cleanup.get('possessMaterial') != material or not material:
        return False
    return cleanup.get('possessMarkerLines') == 0


def _tunnel_outcome(result):
    if isinstance(result, tuple) and len(result) == 4:
        return result
    ok, code, err = result
    text = err.decode('utf-8', 'replace') if isinstance(err, bytes) else (err or '')
    return bool(ok), code, text, ('ready' if ok else classify_tunnel(text))


def _process_exited(proc):
    poll = getattr(proc, 'poll', None)
    if not callable(poll):
        return False
    try:
        return poll() is not None
    except OSError:
        return True


def _process_code(proc):
    code = getattr(proc, 'returncode', None)
    if code is not None:
        return code
    poll = getattr(proc, 'poll', None)
    if not callable(poll):
        return None
    try:
        return poll()
    except OSError:
        return None


def _read_available(stream, limit):
    """Non-blocking read. An unknown stream is skipped so a held pipe cannot extend the wait."""
    if stream is None:
        return b''
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return b''
    try:
        os.set_blocking(fd, False)
    except (AttributeError, OSError):
        return b''
    try:
        data = stream.read(limit)
    except (OSError, ValueError):
        return b''
    if isinstance(data, str):
        data = data.encode()
    return data or b''


def _teardown_process(proc, timeout=2):
    """Reap within timeout. The caller caps timeout to the readiness budget still left."""
    timeout = 0.0 if timeout is None else max(0.0, min(2.0, float(timeout)))
    if isinstance(proc, subprocess.Popen):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        except OSError:
            pass
    else:
        kill = getattr(proc, 'kill', None)
        if callable(kill):
            try:
                kill()
            except OSError:
                pass
        wait = getattr(proc, 'wait', None)
        if callable(wait):
            try:
                wait(timeout=timeout)
            except (OSError, subprocess.TimeoutExpired):
                pass
    stream = getattr(proc, 'stderr', None)
    data = _read_available(stream, SSH_STDERR_CAP)
    close = getattr(stream, 'close', None)
    if callable(close):
        try:
            close()
        except OSError:
            pass
    return data


def _now(driver):
    clock = getattr(driver, 'clock', None)
    return clock() if callable(clock) else 0


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
    ssh_report = login_ready = inbound_before_login = metadata_add = metadata_remove = None
    try:
        view = driver.describe()
        bound = network_identity(view)
        _require_inbound(view, driver.effective_firewalls())
        if baseline is not None and (view.get('fingerprint') != baseline.get('fingerprint')
                                     or view.get('items') != baseline.get('items')):
            raise Refused('metadata changed since preflight')
        if view.get('status') != 'TERMINATED':
            raise Refused('replacement is not TERMINATED')
        _require_base(view['items'])
        start_state = driver.start()
        if start_state != 'started':
            ambiguous = True
            raise Refused(_phase_error('start', start_state, getattr(driver, 'start_report', None)))
        started = True
        view = driver.describe()
        if network_identity(view) != bound:
            raise Refused('network-drift')
        inbound_before_login = _require_inbound(view, driver.effective_firewalls())
        pre_add = {'fingerprint': view['fingerprint'], 'items': dict(view['items'])}
        added_items = add_login_line(view['items'], plan['loginLine'])
        add_started = _now(driver)
        add_state = driver.set_metadata(view['fingerprint'], added_items)
        metadata_add = {'result': add_state, 'elapsedSeconds': round(_now(driver) - add_started, 3),
                        'operation': getattr(driver, 'last_metadata', None)}
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
        login_ready = driver.wait_login_ready()
        if not login_ready.get('listenerReady'):
            ssh_report = {'class': login_ready.get('class') or 'unproven',
                          'returncode': login_ready.get('returncode'),
                          'stderr': login_ready.get('stderr') or '', 'clientLog': '', 'argv': [],
                          'attempts': 0, 'tunnelAttempts': login_ready.get('attempts') or [],
                          'serverKeyAccepted': False, 'loginAccepted': False,
                          'evidence': 'none', 'listenerReady': False,
                          'backendTcp22': False, 'possession': False}
            raise Refused('ssh ' + ssh_report['class'] + ' exit ' + str(login_ready.get('returncode')))
        argv, known_path = bind_execution_argv(plan['argv'], plan.get('knownHosts'), identity_path)
        try:
            ssh_report = driver.ssh(argv, 60)
        finally:
            if known_path:
                try:
                    os.unlink(known_path)
                except OSError:
                    pass
        kind = ssh_report.get('class')
        if kind in ('host-key-mismatch', 'known-hosts-missing', 'known-hosts-unreadable',
                    'identity-unreadable'):
            raise Refused('ssh ' + kind + ' exit ' + str(ssh_report.get('returncode')))
        if kind != 'success':
            raise Refused('ssh ' + str(kind) + ' exit ' + str(ssh_report.get('returncode')))
        if not _quiesced(ssh_report.get('probe') or {}):
            ssh_report['class'] = 'remote-command'
            raise Refused('ssh remote-command exit 0')
        probe_ok = True
    except (Refused, OSError, subprocess.SubprocessError) as exc:
        error = sanitize_text(type(exc).__name__ + ': ' + str(exc)) or type(exc).__name__
    meta_removed = False
    refreshed = False
    if added:
        try:
            view = driver.describe()
            kept = remove_login_line(view['items'], plan['loginLine'])
            instructions = {'loginLine': plan['loginLine'], 'items': kept, 'fingerprint': view.get('fingerprint'),
                            'doNotRetryBlindly': True}
            remove_started = _now(driver)
            remove_state = driver.set_metadata(view['fingerprint'], kept)
            metadata_remove = {'result': remove_state, 'elapsedSeconds': round(_now(driver) - remove_started, 3),
                               'operation': getattr(driver, 'last_metadata', None)}
            meta_removed = remove_state == 'ok'
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
                error = error or _phase_error('stop', 'timeout', getattr(driver, 'stop_report', None))
        except Refused as exc:
            error = error or str(exc)
            stopped = False
    guest_removed = False
    if meta_removed and stopped and refreshed:
        try:
            guest_removed = guest_key_removed(driver.inspect(), plan['operationId'], login_material(plan['loginLine']))
        except Refused as exc:
            error = error or str(exc)
    clean = bool(meta_removed and refreshed and guest_removed and stopped)
    return {
        'executed': True, 'hostKeyPinned': False, 'hostKeyHandshake': probe_ok,
        'guestAuthenticated': bool(probe_ok and clean), 'clean': clean,
        'probeOk': probe_ok, 'metadataLoginRemoved': meta_removed, 'guestKeyRemoved': guest_removed,
        'refreshed': refreshed, 'stopped': stopped, 'error': error, 'runnerEnabled': False,
        'startReport': getattr(driver, 'start_report', None),
        'stopReport': getattr(driver, 'stop_report', None),
        'cleanupInstructions': None if meta_removed else instructions,
        'ssh': ssh_report, 'loginReady': login_ready, 'inbound': inbound_before_login,
        'metadataAdd': metadata_add, 'metadataRemove': metadata_remove,
        'serverKeyAccepted': bool((ssh_report or {}).get('serverKeyAccepted')),
        'loginAccepted': bool((ssh_report or {}).get('loginAccepted')),
    }


def _phase_error(phase, state, report):
    """Name the last operation status and instance status. Do not collapse them into one timeout."""
    report = report or {}
    operation = report.get('operation') or {}
    observed = report.get('observed') or ('failed' if state == 'failed' else 'unresolved')
    detail = (' describe ' + report['describeError']) if report.get('describeError') else ''
    if report.get('returncode') is not None:
        detail += ' exit ' + str(report['returncode'])
    return (phase + ' ' + observed + ': operation ' + (operation.get('status') or 'none')
            + ' instance ' + (report.get('instanceStatus') or 'unknown')
            + ((' error ' + operation['error']) if operation.get('error') else '')
            + detail)


OPERATOR_ACCOUNT = 'grok-bots@protocols-465723.iam.gserviceaccount.com'
METADATA_URL = ('https://compute.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b/instances/'
                'roseos-yogi-builder-20261105-b/setMetadata')
DESCRIBE_TIMEOUT = 30
START_WAIT_SECONDS = 120
# The 2026-11-07 possession stop ran 138s and was still open when a 120s client wait gave up.
STOP_WAIT_SECONDS = 240
POLL_SECONDS = 2
TOKEN_TIMEOUT = 30
METADATA_TIMEOUT = 60
METADATA_WAIT_SECONDS = 180
SSH_TIMEOUT = 60
REFRESH_SECONDS = 45
# BatchMode rejects authentication immediately. The guest agent is not waited on inside ssh.
LOGIN_READY_SECONDS = 60
IAP_READY_SECONDS = 45
# One deadline for the guest-agent/sshd wait and recognized IAP 4003 retries. Not an auth retry.
TRANSPORT_BUDGET = 120
SSH_STDERR_CAP = 2048
# Client-log bound used for classification. Storage is capped separately, after classification.
SSH_LOG_BOUND = 65536
INSPECT_TIMEOUT = 600


class GcloudDriver:
    """Official gcloud for reads, start, and stop. setMetadata is one fingerprint write, same URL as recovery.

    Every subprocess has a timeout. This class performs no call until a method is used.
    """

    def __init__(self, run=subprocess.run, opener=urllib.request.urlopen, sleep=time.sleep,
                 clock=time.monotonic, possess_comment='', possess_material='', inspect_out=None,
                 popen=subprocess.Popen, connect=None, iap_check=None):
        self.run, self.opener, self.sleep, self.clock = run, opener, sleep, clock
        self.possess_comment = possess_comment
        self.possess_material = possess_material
        self.inspect_out = inspect_out
        self.popen = popen
        self.connect = connect
        self.iap_check = iap_check
        self.last_metadata = None

    def _cmd(self, argv, timeout):
        try:
            return self.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Refused('command timed out: ' + ' '.join(argv[:4])) from None

    def effective_firewalls(self):
        """Official nic0 effective-firewall read. SDK 592 prints an object with only `firewalls`."""
        argv = effective_firewall_argv()
        try:
            result = self.run(argv, capture_output=True, text=True, timeout=DESCRIBE_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise DescribeRefused('firewall-timeout') from None
        stdout = result.stdout if isinstance(result.stdout, str) else ''
        if result.returncode:
            raise DescribeRefused('firewall-unreadable', returncode=result.returncode, stdout_class=_stdout_class(stdout))
        try:
            parsed = json.loads(stdout)
        except ValueError:
            raise DescribeRefused('firewall-unreadable', returncode=result.returncode,
                                  stdout_class=_stdout_class(stdout)) from None
        return parsed

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
        """Read one instance. This is the only instance-describe used by preflight, start, and stop.

        gcloud's exit 0 is not enough. A policy refusal keeps the parsed status when the body was an object.
        The 2026-11-07 start polled this call 40 times, recorded only 'unreadable', then the same call
        accepted the terminated instance.
        """
        argv = ['gcloud', 'compute', 'instances', 'describe', INSTANCE, f'--project={PROJECT}',
                f'--zone={ZONE}', '--format=json', '--quiet']
        try:
            result = self.run(argv, capture_output=True, text=True, timeout=DESCRIBE_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise DescribeRefused('describe-timeout') from None
        stdout = result.stdout if isinstance(result.stdout, str) else ''
        if result.returncode:
            raise DescribeRefused('describe-exit', returncode=result.returncode, stdout_class=_stdout_class(stdout))
        try:
            seen = json.loads(stdout)
        except ValueError:
            raise DescribeRefused('describe-not-json', returncode=result.returncode,
                                  stdout_class=_stdout_class(stdout)) from None
        if not isinstance(seen, dict):
            raise DescribeRefused('describe-not-object', returncode=result.returncode, stdout_class='array')
        status = seen.get('status') if isinstance(seen.get('status'), str) else ''
        zone = str(seen.get('zone') or '').rstrip('/').rsplit('/', 1)[-1]
        link = f'/projects/{PROJECT}/zones/{ZONE}/instances/{INSTANCE}'
        if str(seen.get('id')) != INSTANCE_ID or seen.get('name') != INSTANCE or zone != ZONE \
                or not str(seen.get('selfLink') or '').endswith(link):
            raise DescribeRefused('describe-identity', status=status)
        items = {}
        meta = seen.get('metadata') if isinstance(seen.get('metadata'), dict) else {}
        for entry in meta.get('items') or []:
            if not isinstance(entry, dict) or not isinstance(entry.get('key'), str) or not isinstance(entry.get('value'), str):
                raise DescribeRefused('describe-metadata', status=status)
            if entry['key'] in items:
                raise DescribeRefused('describe-metadata', status=status)
            items[entry['key']] = entry['value']
        fingerprint = meta.get('fingerprint')
        if not isinstance(fingerprint, str) or not fingerprint:
            raise DescribeRefused('describe-fingerprint', status=status)
        for nic in seen.get('networkInterfaces') or []:
            if not isinstance(nic, dict):
                raise DescribeRefused('describe-network', status=status)
        tags = seen.get('tags') if isinstance(seen.get('tags'), dict) else {}
        tag_items = tags.get('items') if isinstance(tags, dict) else None
        if tag_items is None:
            tag_items = []
        if not isinstance(tag_items, list) or not all(isinstance(item, str) for item in tag_items):
            raise DescribeRefused('describe-tags', status=status)
        network = network_observation(seen)
        return {'status': status, 'fingerprint': fingerprint, 'items': items, 'id': str(seen.get('id')),
                'tags': sorted(tag_items), 'network': network}

    def _blank_report(self):
        return {'observed': 'unresolved', 'instanceStatus': '', 'operation': None, 'polls': 0}

    def _json_stdout(self, result):
        if result is None or result.returncode:
            return None
        try:
            parsed = json.loads(result.stdout)
        except (ValueError, AttributeError):
            return None
        if isinstance(parsed, list):
            if len(parsed) == 1 and isinstance(parsed[0], dict):
                return parsed[0]
            return None
        return parsed if isinstance(parsed, dict) else None

    def _public_operation(self, parsed, expected_type):
        """Whitelist fields only. No raw body, token, or selfLink is kept."""
        if not isinstance(parsed, dict):
            return None
        name = parsed.get('name')
        if not isinstance(name, str) or not OPERATION_NAME.fullmatch(name) or name in SPENT_ZONE_OPERATIONS:
            return None
        target = str(parsed.get('targetId') or '')
        if target and target != INSTANCE_ID:
            return None
        op_type = parsed.get('operationType')
        if op_type not in (None, '', expected_type):
            return None
        zone = str(parsed.get('zone') or '')
        if zone and not zone.rstrip('/').endswith('/' + ZONE):
            return None
        error = parsed.get('error')
        code = ''
        if isinstance(error, dict):
            errors = error.get('errors')
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                code = str(errors[0].get('code') or 'operation-error')[:40]
            else:
                code = str(error.get('code') or 'operation-error')[:40]
        elif error:
            code = 'operation-error'
        progress = parsed.get('progress')
        return {
            'name': name, 'id': str(parsed.get('id') or ''),
            'operationType': str(op_type or expected_type), 'status': str(parsed.get('status') or ''),
            'targetId': target, 'progress': progress if isinstance(progress, int) else None, 'error': code,
            'insertTime': parsed.get('insertTime') if isinstance(parsed.get('insertTime'), str) else '',
            'startTime': parsed.get('startTime') if isinstance(parsed.get('startTime'), str) else '',
            'endTime': parsed.get('endTime') if isinstance(parsed.get('endTime'), str) else '',
        }

    def _submit_instance(self, verb):
        try:
            return self.run(['gcloud', 'compute', 'instances', verb, INSTANCE, f'--project={PROJECT}',
                             f'--zone={ZONE}', '--async', '--format=json', '--quiet'],
                            capture_output=True, text=True, timeout=DESCRIBE_TIMEOUT)
        except subprocess.TimeoutExpired:
            return None

    def _describe_operation(self, name):
        try:
            result = self.run(['gcloud', 'compute', 'operations', 'describe', name, f'--project={PROJECT}',
                               f'--zone={ZONE}', '--format=json', '--quiet'],
                              capture_output=True, text=True, timeout=DESCRIBE_TIMEOUT)
        except subprocess.TimeoutExpired:
            return None
        return self._json_stdout(result)

    def _read_instance_status(self, report):
        try:
            view = self.describe()
        except DescribeRefused as exc:
            report['describeError'] = exc.code
            report['returncode'] = exc.returncode
            report['stdoutClass'] = exc.stdout_class or None
            report['instanceStatus'] = exc.status or ('mismatch' if exc.code == 'describe-identity' else 'unreadable')
            return None, exc.terminal
        report['describeError'] = None
        report['returncode'] = None
        report['stdoutClass'] = None
        report['instanceStatus'] = str(view.get('status') or '')
        return view, False

    def _poll_phase(self, report, expected_type, want_status, budget):
        """Poll the submitted operation and the instance until want_status, the budget, or a hard error.

        One non-matching describe is not terminal. A frozen clock stops after three polls.
        """
        operation = report.get('operation')
        deadline = self.clock() + budget
        stalls = 0
        while True:
            report['polls'] += 1
            if operation and operation.get('status') != 'DONE':
                nxt = self._public_operation(self._describe_operation(operation['name']), expected_type)
                if nxt and nxt['name'] == operation['name']:
                    operation = nxt
                    report['operation'] = operation
            if operation and operation.get('error'):
                report['observed'] = 'failed'
                return 'failed'
            view, terminal = self._read_instance_status(report)
            if terminal:
                report['observed'] = 'failed'
                return 'failed'
            if view and view.get('status') == want_status and (not operation or operation.get('status') == 'DONE'):
                report['observed'] = 'running' if want_status == 'RUNNING' else 'terminated'
                return 'ready'
            remaining = deadline - self.clock()
            if remaining <= 0:
                report['observed'] = 'unresolved'
                return 'timeout'
            before = remaining
            self.sleep(min(POLL_SECONDS, remaining))
            if deadline - self.clock() >= before - 0.001:
                stalls += 1
                if stalls >= 3:
                    report['observed'] = 'unresolved'
                    return 'timeout'

    def start(self):
        """Submit start asynchronously and poll until the instance is RUNNING.

        gcloud's return is not RUNNING. The 2026-11-07 start operation was DONE at 15:55:34Z
        and a single following describe was treated as unresolved at 15:55:37Z.
        """
        self.start_report = self._blank_report()
        result = self._submit_instance('start')
        if result is None:
            return 'timeout'
        operation = self._public_operation(self._json_stdout(result), 'start')
        if operation is None:
            self.start_report['observed'] = 'failed' if result.returncode else 'unresolved'
            return 'failed' if result.returncode else 'timeout'
        self.start_report['operation'] = operation
        polled = self._poll_phase(self.start_report, 'start', 'RUNNING', START_WAIT_SECONDS)
        if polled == 'ready':
            return 'started'
        return 'failed' if polled == 'failed' else 'timeout'

    def stop(self):
        """Submit stop asynchronously and poll until the instance is TERMINATED.

        A client timeout is not a finished stop. The 2026-11-07 stop was still running at 120s
        and reached DONE at 138s.
        """
        self.stop_report = self._blank_report()
        result = self._submit_instance('stop')
        if result is None:
            self.stop_report['observed'] = 'unresolved'
            return 'unresolved'
        operation = self._public_operation(self._json_stdout(result), 'stop')
        if operation is None:
            self.stop_report['observed'] = 'unresolved'
            return 'unresolved'
        self.stop_report['operation'] = operation
        polled = self._poll_phase(self.stop_report, 'stop', 'TERMINATED', STOP_WAIT_SECONDS)
        return 'stopped' if polled == 'ready' else 'unresolved'

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
            kind = self._wait_operation(name, token)
        self.last_metadata = {'result': kind,
                              'operation': self._public_operation(parsed, 'setMetadata') if isinstance(parsed, dict) else None}
        return kind

    def ssh(self, argv, timeout):
        """One strict attempt. Classification uses the OpenSSH -E log, not mixed stderr."""
        if timeout != SSH_TIMEOUT:
            raise Refused('ssh timeout is not 60 seconds')
        log_fd, log_path = tempfile.mkstemp(prefix='yogi-ssh-')
        os.close(log_fd)
        # DEBUG is the minimum OpenSSH 9.6 level that emits the host-key acceptance line.
        # VERBOSE writes the denial and nothing at debug1, so it cannot prove userauth.
        executed = [argv[0], '-E', log_path, '-o', 'LogLevel=DEBUG', *list(argv[1:])] if argv else []
        clean_argv = sanitize_argv(executed)
        user = POSSESSION_USER
        alias = ''
        for index, arg in enumerate(executed):
            if arg == '-l' and index + 1 < len(executed):
                user = executed[index + 1]
            elif isinstance(arg, str) and arg.startswith('HostKeyAlias='):
                alias = arg.split('=', 1)[1]
        try:
            try:
                result = self.run(executed, capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired as expired:
                text, truncated = self._load_client_log(log_path)
                return self._ssh_report('timeout', None, getattr(expired, 'stderr', b'') or b'',
                                        text, clean_argv, truncated)
            except (OSError, subprocess.SubprocessError) as exc:
                text, truncated = self._load_client_log(log_path)
                return self._ssh_report('unproven', None, type(exc).__name__, text, clean_argv, truncated)
            text, truncated = self._load_client_log(log_path)
            stderr = getattr(result, 'stderr', b'') or b''
            stderr_text = stderr.decode('utf-8', 'replace') if isinstance(stderr, bytes) else str(stderr)
            kind, evidence = classify_ssh(result.returncode, text.splitlines(), user, alias,
                                         stderr_lines=stderr_text.splitlines(),
                                         known_path=_known_hosts_path(executed))
            probe = None
            if result.returncode == 0 and kind == 'success':
                try:
                    probe = json.loads(result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout)
                except (ValueError, AttributeError):
                    kind = 'not-json'
            report = self._ssh_report(kind, result.returncode, stderr, text, clean_argv, truncated, probe)
            report.update(evidence)
            return report
        finally:
            try:
                os.unlink(log_path)
            except OSError:
                pass

    def _ssh_report(self, kind, returncode, stderr, client_log, argv, truncated, probe=None):
        report = {'class': kind, 'returncode': returncode, 'stderr': sanitize_text(stderr),
                  'clientLog': sanitize_text(client_log), 'argv': argv, 'attempts': 1,
                  'probe': probe, 'logTruncated': bool(truncated)}
        report.update(ssh_evidence(kind))
        return report

    def _load_client_log(self, path):
        try:
            size = os.path.getsize(path)
            with open(path, 'rb') as handle:
                if size <= SSH_LOG_BOUND:
                    raw = handle.read()
                    return complete_log_text(raw, SSH_LOG_BOUND)
                head_n = SSH_LOG_BOUND // 2
                tail_n = SSH_LOG_BOUND // 2
                head = handle.read(head_n)
                handle.seek(max(0, size - tail_n))
                tail = handle.read(tail_n)
        except OSError:
            return '', False
        if b'\n' in head:
            head = head.rsplit(b'\n', 1)[0] + b'\n'
        else:
            head = b''
        if b'\n' in tail:
            tail = tail.split(b'\n', 1)[1]
        else:
            tail = b''
        return (head + tail).decode('utf-8', 'replace'), True

    def wait_login_ready(self):
        """Wait for the guest agent and sshd, then retry only IAP backend 4003 until one deadline.

        A local accept after gcloud's own backend check is not an SSH session and not possession.
        """
        started = self.clock()
        deadline = started + TRANSPORT_BUDGET
        report = {'listenerReady': False, 'backendTcp22': False, 'backendEvidence': 'none',
                  'possession': False, 'class': 'unproven', 'deadlineSeconds': TRANSPORT_BUDGET,
                  'guestWaitSeconds': LOGIN_READY_SECONDS, 'delaySeconds': 0, 'elapsedSeconds': 0,
                  'attempts': [], 'returncode': None, 'stderr': ''}
        target = min(deadline, started + LOGIN_READY_SECONDS)
        stalls = 0
        while self.clock() < target:
            before = self.clock()
            self.sleep(min(POLL_SECONDS, max(0.0, target - before)))
            if self.clock() <= before:
                stalls += 1
                if stalls >= 3:
                    break
        report['delaySeconds'] = round(self.clock() - started, 3)
        while self.clock() < deadline and len(report['attempts']) < 40:
            try:
                if self.iap_check is not None:
                    outcome = self.iap_check()
                    ok, code, err, kind = _tunnel_outcome(outcome)
                else:
                    ok, code, err, kind = self._iap_once(deadline)
            except (OSError, subprocess.SubprocessError) as exc:
                ok, code, err, kind = False, None, type(exc).__name__, 'spawn'
            text = sanitize_text(err, 240)
            report['attempts'].append({'n': len(report['attempts']) + 1,
                                       'elapsedSeconds': round(self.clock() - started, 3),
                                       'returncode': code, 'class': kind, 'stderr': text})
            report['returncode'] = code
            report['stderr'] = text
            report['class'] = 'ready' if ok else kind
            if ok:
                report['listenerReady'] = True
                report['backendTcp22'] = True
                report['backendEvidence'] = 'gcloud-iap-check'
                break
            if kind != 'backend-4003':
                break
            remain = deadline - self.clock()
            if remain <= 0:
                break
            before = self.clock()
            self.sleep(min(POLL_SECONDS, remain))
            if self.clock() <= before:
                stalls += 1
                if stalls >= 3:
                    break
        report['elapsedSeconds'] = round(self.clock() - started, 3)
        return report

    def _iap_tcp(self):
        """One tunnel attempt. The readiness loop decides whether a 4003 is retried."""
        ok, code, err, _kind = self._iap_once(self.clock() + IAP_READY_SECONDS)
        return ok, code, err

    def _iap_once(self, deadline):
        """One tunnel check that stops at deadline, then reaps within the time still left."""
        import socket
        if self.clock() >= deadline:
            return False, None, '', 'deadline'
        try:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
        except OSError as exc:
            return False, None, type(exc).__name__, 'spawn'
        argv = ['gcloud', 'compute', 'start-iap-tunnel', INSTANCE, '22',
                '--local-host-port=127.0.0.1:' + str(port), '--zone', ZONE, '--project', PROJECT, '--quiet']
        try:
            try:
                proc = self.popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                  start_new_session=True)
            except TypeError:
                proc = self.popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except (OSError, subprocess.SubprocessError, TypeError) as exc:
            return False, None, type(exc).__name__, 'spawn'
        ok = False
        code = None
        err = b''
        try:
            attempt_end = min(deadline, self.clock() + IAP_READY_SECONDS)
            stalls = 0
            while self.clock() < attempt_end:
                if _process_exited(proc):
                    break
                remain = attempt_end - self.clock()
                try:
                    if self.connect is not None:
                        ok = bool(self.connect(('127.0.0.1', port)))
                    else:
                        try:
                            with socket.create_connection(('127.0.0.1', port), timeout=min(1.0, remain)):
                                ok = True
                        except OSError:
                            ok = False
                except OSError:
                    ok = False
                if ok or self.clock() >= attempt_end:
                    break
                pause = min(POLL_SECONDS, attempt_end - self.clock())
                if pause <= 0:
                    break
                before = self.clock()
                self.sleep(pause)
                if self.clock() <= before:
                    stalls += 1
                    if stalls >= 3:
                        break
        except (OSError, subprocess.SubprocessError) as exc:
            ok = False
            err = type(exc).__name__.encode()
        finally:
            code = _process_code(proc)
            captured = _teardown_process(proc, timeout=max(0.0, deadline - self.clock()))
        if not err:
            err = captured
        if isinstance(err, bytes):
            text = err.decode('utf-8', 'replace')
        else:
            text = err or ''
        kind = 'ready' if ok else classify_tunnel(text)
        return ok, code, text, kind

    def wait_refresh(self):
        self.sleep(REFRESH_SECONDS)
        return 'ok'

    def inspect(self):
        script = str(Path(__file__).resolve().parents[0] / 'yogi_replacement_recover.py')
        out = self.inspect_out or str(Path(os.getcwd()) / 'possess-inspect')
        plan = self._cmd([sys.executable, script, 'plan', '--mode', 'inspect', '--operator', 'possession',
                          '--out', out, '--possess-comment', self.possess_comment,
                          '--possess-material', self.possess_material], DESCRIBE_TIMEOUT)
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


def _require_inbound(view, document):
    """Fail closed before start and before the login line. The reviewed NAT may keep its address bit."""
    drift = network_drift(view.get('network') if isinstance(view, dict) else None,
                          (view or {}).get('tags'))
    if drift:
        raise DescribeRefused(drift, status=(view or {}).get('status') or '')
    decision = evaluate_inbound(document)
    if not decision.get('ok'):
        raise DescribeRefused(decision.get('code') or 'firewall-unknown-shape', status=(view or {}).get('status') or '')
    return decision


def possession_preflight(driver):
    """Account, replacement id, and the reviewed metadata baseline. No mutation."""
    driver.account()
    view = driver.describe()
    decision = _require_inbound(view, driver.effective_firewalls())
    if view.get('status') != 'TERMINATED' or view.get('id') != INSTANCE_ID:
        raise Refused('replacement is not the terminated saved instance')
    if view.get('fingerprint') != POSSESSION_FINGERPRINT or view.get('items') != POSSESSION_BASE:
        raise Refused('metadata does not match the reviewed baseline')
    _require_base(view['items'])
    return {'fingerprint': view['fingerprint'], 'items': dict(view['items']), 'status': view['status'],
            'inbound': decision, 'network': view.get('network')}


def execute_handshake(api_path, client_path, identity, out, driver=None, operation_id=None, environ=None, now=None):
    """Runnable operator entry. Tests pass a fake driver. The default driver is not used by tests."""
    identity = local_identity(identity)
    driver = driver or GcloudDriver()
    baseline = possession_preflight(driver)
    operation_id = operation_id or __import__('secrets').token_hex(16)
    api = json.loads(Path(api_path).read_text())
    plan = possession_plan(api, Path(client_path).read_text(), operation_id, current_items=baseline['items'],
                           environ=environ, now=now, identity_path=identity)
    destination = Path(out)
    destination.mkdir(mode=0o700, exist_ok=True)
    driver.possess_comment = 'roseos-yogi-possess-' + operation_id
    driver.possess_material = login_material(plan['loginLine'])
    driver.inspect_out = str(destination / 'inspect')
    result = possession_run(driver, plan, identity, baseline=baseline)
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


def describe_instance(driver=None):
    """Read the instance and nic0 effective firewalls. No start, stop, metadata write, or SSH."""
    driver = driver or GcloudDriver()
    try:
        view = driver.describe()
    except DescribeRefused as exc:
        return {'readOnly': True, 'mutation': False, 'describeError': exc.code,
                'returncode': exc.returncode, 'stdoutClass': exc.stdout_class or None,
                'status': exc.status or None, 'id': None, 'fingerprint': None, 'metadataKeys': [],
                'network': exc.network, 'firewall': None, 'allocationInferred': False}
    try:
        document = driver.effective_firewalls()
    except DescribeRefused as exc:
        return {'readOnly': True, 'mutation': False, 'describeError': None, 'firewallError': exc.code,
                'returncode': exc.returncode, 'stdoutClass': exc.stdout_class or None,
                'status': view['status'], 'id': view['id'], 'fingerprint': view['fingerprint'],
                'metadataKeys': sorted(view['items']), 'network': view.get('network'), 'firewall': None,
                'allocationInferred': False}
    try:
        decision = _require_inbound(view, document)
        code = None
    except DescribeRefused as exc:
        decision = None
        code = exc.code
    return {'readOnly': True, 'mutation': False, 'describeError': code, 'firewallError': None,
            'returncode': 0, 'stdoutClass': 'object', 'status': view['status'], 'id': view['id'],
            'fingerprint': view['fingerprint'], 'metadataKeys': sorted(view['items']),
            'network': view.get('network'), 'firewall': decision, 'allocationInferred': False,
            'permission': FIREWALL_PERMISSION}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Strict IAP possession plan. --execute is the operator run; tests inject a driver.')
    parser.add_argument('action', choices=('handshake-plan', 'handshake', 'describe-instance'))
    parser.add_argument('--hostkeys')
    parser.add_argument('--client-public')
    parser.add_argument('--out')
    parser.add_argument('--identity', help='local mode 0600 private key; required with handshake --execute')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'describe-instance':
            result = describe_instance()
            print(json.dumps(result, sort_keys=True))
            return 0 if result.get('describeError') is None and result.get('firewallError') is None else 1
        if not args.hostkeys or not args.client_public or not args.out:
            parser.error('handshake needs --hostkeys, --client-public and --out')
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
