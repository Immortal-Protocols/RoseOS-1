"""Standalone checks for the IAP possession plan. No VM, SSH, or cloud call."""
import base64
import datetime
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yogi_possession  # noqa: E402

def _wire(kind, payload):
    return _u32(len(kind)) + kind + _u32(len(payload)) + payload


def _u32(value):
    return value.to_bytes(4, 'big')


class PossessionTest(unittest.TestCase):
    def _api(self):
        keys = {
            'ssh-ed25519': _wire(b'ssh-ed25519', bytes(range(32))),
            'ecdsa-sha2-nistp256': _wire(b'ecdsa-sha2-nistp256', bytes(range(40))),
            'ssh-rsa': _wire(b'ssh-rsa', bytes(range(64))),
        }
        expected = {name: 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
                    for name, blob in keys.items()}
        api = {'queryValue': {'items': [
            {'namespace': 'hostkeys', 'key': name, 'value': base64.b64encode(blob).decode()}
            for name, blob in keys.items()]}}
        return keys, expected, api

    def test_saved_api_blobs_keep_foreign_metadata_and_use_strict_ssh(self):
        keys, expected, api = self._api()
        client = 'ssh-ed25519 ' + base64.b64encode(_wire(b'ssh-ed25519', bytes(range(32, 64)))).decode() + ' comment'
        operation = 'ab' * 16
        current = dict(yogi_possession.POSSESSION_BASE, **{'ssh-keys': 'other:ssh-ed25519 AAAA foreign', 'fleet': 'west'})
        now = datetime.datetime(2026, 11, 7, 18, 0, tzinfo=datetime.timezone.utc)
        plan = yogi_possession.possession_plan(api, client, operation, current_items=current, expected=expected,
                                      environ={'HOME': '/home/operator'}, now=now)
        self.assertFalse(plan['hostKeyPinned'] or plan['guestAuthenticated'] or plan['accountChanges'])
        self.assertFalse(plan['iapAuthenticatesHostKey'])
        lines = plan['metadataAdd']['ssh-keys'].split('\n')
        self.assertEqual(lines[0], 'other:ssh-ed25519 AAAA foreign')
        self.assertIn('roseos-yogi-possess-' + operation, lines[1])
        self.assertIn('expireOn', lines[1])
        self.assertEqual(plan['metadataAdd']['fleet'], 'west')
        removed = yogi_possession.remove_login_line(plan['metadataAdd'], plan['loginLine'])
        self.assertEqual(removed['ssh-keys'], 'other:ssh-ed25519 AAAA foreign')
        self.assertEqual(removed['fleet'], 'west')
        self.assertNotIn('gcloud compute ssh', ' '.join(plan['argv']))
        self.assertIn('start-iap-tunnel', ' '.join(plan['argv']))
        for flag in ('StrictHostKeyChecking=yes', 'PasswordAuthentication=no', 'IdentityAgent=none',
                     'NumberOfPasswordPrompts=0', 'UpdateHostKeys=no', 'RequestTTY=no',
                     'HostKeyAlias=yogi-' + yogi_possession.INSTANCE_ID, 'ConnectTimeout=20'):
            self.assertIn(flag, plan['argv'])
        self.assertEqual(plan['commandTimeoutSeconds'], 60)
        self.assertNotIn('sudo', plan['argv'][-1])
        self.assertEqual(plan['knownHosts'].count('yogi-' + yogi_possession.INSTANCE_ID), 3)
        self.assertNotIn('PRIVATE', json.dumps(plan))
        with self.assertRaises(yogi_possession.Refused):
            yogi_possession.possession_plan(api, client, operation, environ={'HOME': '/home/operator'}, now=now)
        self.assertEqual(yogi_possession.POSSESSION_FINGERPRINTS['ssh-ed25519'],
                         'SHA256:n4xqiwBsyYqkQ/aF19AXkVr9fqAZJmJLWPqfZwhUweE')

    def test_failures_stop_the_vm_and_require_a_fresh_inspect(self):
        _keys, expected, api = self._api()
        client = 'ssh-ed25519 ' + base64.b64encode(_wire(b'ssh-ed25519', bytes(range(32, 64)))).decode()
        operation = 'cd' * 16
        now = datetime.datetime(2026, 11, 7, 18, 0, tzinfo=datetime.timezone.utc)
        plan = yogi_possession.possession_plan(api, client, operation, expected=expected,
                                              environ={'HOME': '/home/operator'}, now=now)
        identity = Path(tempfile.mkdtemp())/'id_ed25519'
        identity.write_text('not-read')
        identity.chmod(0o600)
        self.assertEqual(yogi_possession.local_identity(str(identity)), str(identity.resolve()))
        identity.chmod(0o644)
        with self.assertRaises(yogi_possession.Refused):
            yogi_possession.local_identity(str(identity))
        identity.chmod(0o600)

        class Driver:
            def __init__(self, ssh_ok=True, marker=0, conflict=False):
                self.ssh_ok, self.marker, self.conflict = ssh_ok, marker, conflict
                self.items = dict(yogi_possession.POSSESSION_BASE, **{'fleet': 'west', 'ssh-keys': 'other:ssh-ed25519 AAAA foreign'})
                self.fingerprint = 'fp1'
                self.calls = []
                self.status = 'TERMINATED'

            def describe(self):
                return {'status': self.status, 'fingerprint': self.fingerprint, 'items': dict(self.items)}

            def start(self):
                self.calls.append('start')
                self.status = 'RUNNING'
                return 'started'

            def stop(self):
                self.calls.append('stop')
                self.status = 'TERMINATED'
                return 'stopped'

            def set_metadata(self, fingerprint, items):
                self.calls.append(('metadata', fingerprint, sorted(items)))
                if self.conflict and fingerprint == 'fp1':
                    return 'conflict'
                if fingerprint != self.fingerprint:
                    return 'conflict'
                self.items = dict(items)
                self.fingerprint += 'x'
                return 'ok'

            def ssh(self, argv, timeout):
                self.calls.append(('ssh', timeout))
                self.assert_timeout = timeout
                if not self.ssh_ok:
                    return {'runnerProcess': True, 'active': 'active', 'enabled': 'enabled',
                            'builder': {'instanceId': yogi_possession.INSTANCE_ID}}
                return {'runnerProcess': False, 'active': 'inactive', 'enabled': 'disabled',
                        'builder': {'instanceId': yogi_possession.INSTANCE_ID}}

            def inspect(self):
                self.calls.append('inspect')
                return {'purpose': 'inspect', 'instanceId': yogi_possession.INSTANCE_ID,
                        'operationId': '12' * 16, 'cleanup': {'possessMarkerLines': self.marker}}

        bad = Driver(ssh_ok=False, marker=1)
        result = yogi_possession.possession_run(bad, plan, str(identity))
        self.assertFalse(result['guestAuthenticated'] or result['hostKeyPinned'] or result['guestKeyRemoved'])
        self.assertTrue(result['stopped'] and result['metadataLoginRemoved'])
        self.assertEqual(bad.items['ssh-keys'], 'other:ssh-ed25519 AAAA foreign')
        self.assertEqual(bad.items['fleet'], 'west')
        self.assertEqual(bad.calls.count('stop'), 1)
        self.assertEqual(sum(1 for call in bad.calls if call[0] == 'metadata'), 2)

        stale = Driver(conflict=True)
        refused = yogi_possession.possession_run(stale, plan, str(identity))
        self.assertFalse(refused['guestAuthenticated'])
        self.assertTrue(refused['stopped'])
        self.assertEqual(sum(1 for call in stale.calls if call[0] == 'metadata'), 1)
        self.assertEqual(stale.items['ssh-keys'], 'other:ssh-ed25519 AAAA foreign')

        spent = {'purpose': 'inspect', 'instanceId': yogi_possession.INSTANCE_ID,
                 'operationId': '638e6e9039e41b7531697bb45066a616', 'cleanup': {'possessMarkerLines': 0}}
        self.assertFalse(yogi_possession.guest_key_removed(spent, operation))

        good = Driver(marker=0)
        done = yogi_possession.possession_run(good, plan, str(identity))
        self.assertTrue(done['guestAuthenticated'] and done['guestKeyRemoved'] and done['stopped'])
        self.assertFalse(done['hostKeyPinned'] or done['runnerEnabled'])
        self.assertEqual(good.calls[-2:], ['stop', 'inspect'])



