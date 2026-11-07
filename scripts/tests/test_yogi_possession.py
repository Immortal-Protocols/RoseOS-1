"""Standalone checks for the IAP possession plan. No VM, SSH, or cloud call."""
import base64
import datetime
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

def _possession_root():
    """The directory that contains yogi_possession.py: beside this test, or its parent in the repo."""
    here = Path(__file__).resolve().parent
    for candidate in (here, here.parent):
        if (candidate / 'yogi_possession.py').is_file():
            return candidate
    raise AssertionError('yogi_possession.py is not beside this test or in its parent directory')


sys.path.insert(0, str(_possession_root()))
import yogi_possession  # noqa: E402

EFFECTIVE_FIREWALLS = '{\n  "firewalls": [\n    {\n      "allowed": [\n        {\n          "IPProtocol": "tcp",\n          "ports": [\n            "22"\n          ]\n        }\n      ],\n      "creationTimestamp": "2026-11-05T15:12:13.037-08:00",\n      "description": "Approved replacement-only Yogi readiness ingress; no existing VM or rule modified.",\n      "direction": "INGRESS",\n      "disabled": false,\n      "id": "5330425627088565155",\n      "kind": "compute#firewall",\n      "logConfig": {\n        "enable": false\n      },\n      "name": "yogi-b-20261105-iap-ssh",\n      "network": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default",\n      "priority": 900,\n      "selfLink": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/firewalls/yogi-b-20261105-iap-ssh",\n      "sourceRanges": [\n        "35.235.240.0/20"\n      ],\n      "targetTags": [\n        "roseos-yogi-b-20261105"\n      ]\n    },\n    {\n      "creationTimestamp": "2026-11-05T15:12:21.093-08:00",\n      "denied": [\n        {\n          "IPProtocol": "all"\n        }\n      ],\n      "description": "Approved replacement-only Yogi readiness ingress; no existing VM or rule modified.",\n      "direction": "INGRESS",\n      "disabled": false,\n      "id": "2143899672614319035",\n      "kind": "compute#firewall",\n      "logConfig": {\n        "enable": false\n      },\n      "name": "yogi-b-20261105-deny-ingress",\n      "network": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default",\n      "priority": 901,\n      "selfLink": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/firewalls/yogi-b-20261105-deny-ingress",\n      "sourceRanges": [\n        "0.0.0.0/0"\n      ],\n      "targetTags": [\n        "roseos-yogi-b-20261105"\n      ]\n    },\n    {\n      "allowed": [\n        {\n          "IPProtocol": "tcp",\n          "ports": [\n            "22"\n          ]\n        }\n      ],\n      "creationTimestamp": "2026-11-03T21:54:02.749-08:00",\n      "description": "IAP SSH to isolated Yogi build VMs only",\n      "direction": "INGRESS",\n      "disabled": false,\n      "id": "6201501568027031189",\n      "kind": "compute#firewall",\n      "logConfig": {\n        "enable": false\n      },\n      "name": "roseos-yogi-iap-ssh",\n      "network": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default",\n      "priority": 1000,\n      "selfLink": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/firewalls/roseos-yogi-iap-ssh",\n      "sourceRanges": [\n        "35.235.240.0/20"\n      ],\n      "targetTags": [\n        "roseos-yogi-builder"\n      ]\n    },\n    {\n      "allowed": [\n        {\n          "IPProtocol": "tcp"\n        },\n        {\n          "IPProtocol": "udp"\n        },\n        {\n          "IPProtocol": "icmp"\n        }\n      ],\n      "creationTimestamp": "2026-01-08T13:50:56.382-08:00",\n      "description": "",\n      "direction": "INGRESS",\n      "disabled": false,\n      "id": "1842537047467595823",\n      "kind": "compute#firewall",\n      "logConfig": {\n        "enable": false\n      },\n      "name": "aet-uswest2-a--list--connector-tagfw",\n      "network": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default",\n      "priority": 1000,\n      "selfLink": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/firewalls/aet-uswest2-a--list--connector-tagfw",\n      "sourceTags": [\n        "aet-uswest2-a--list--connector"\n      ]\n    },\n    {\n      "allowed": [\n        {\n          "IPProtocol": "tcp"\n        },\n        {\n          "IPProtocol": "udp"\n        },\n        {\n          "IPProtocol": "icmp"\n        }\n      ],\n      "creationTimestamp": "2026-01-08T13:50:56.383-08:00",\n      "description": "",\n      "direction": "INGRESS",\n      "disabled": false,\n      "id": "319457170889218095",\n      "kind": "compute#firewall",\n      "logConfig": {\n        "enable": false\n      },\n      "name": "aet-uswest2-a--list--connector-sbntfw",\n      "network": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default",\n      "priority": 1000,\n      "selfLink": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/firewalls/aet-uswest2-a--list--connector-sbntfw",\n      "sourceRanges": [\n        "10.9.0.0/28"\n      ]\n    },\n    {\n      "allowed": [\n        {\n          "IPProtocol": "tcp",\n          "ports": [\n            "0-65535"\n          ]\n        },\n        {\n          "IPProtocol": "udp",\n          "ports": [\n            "0-65535"\n          ]\n        },\n        {\n          "IPProtocol": "icmp"\n        }\n      ],\n      "creationTimestamp": "2026-01-05T14:30:33.660-08:00",\n      "description": "Allow internal traffic on the default network",\n      "direction": "INGRESS",\n      "disabled": false,\n      "id": "8583724390203622758",\n      "kind": "compute#firewall",\n      "logConfig": {\n        "enable": false\n      },\n      "name": "default-allow-internal",\n      "network": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default",\n      "priority": 65534,\n      "selfLink": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/firewalls/default-allow-internal",\n      "sourceRanges": [\n        "10.128.0.0/9"\n      ]\n    },\n    {\n      "allowed": [\n        {\n          "IPProtocol": "tcp",\n          "ports": [\n            "3389"\n          ]\n        }\n      ],\n      "creationTimestamp": "2026-01-05T14:30:33.822-08:00",\n      "description": "Allow RDP from anywhere",\n      "direction": "INGRESS",\n      "disabled": false,\n      "id": "5089601416384841062",\n      "kind": "compute#firewall",\n      "logConfig": {\n        "enable": false\n      },\n      "name": "default-allow-rdp",\n      "network": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default",\n      "priority": 65534,\n      "selfLink": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/firewalls/default-allow-rdp",\n      "sourceRanges": [\n        "0.0.0.0/0"\n      ]\n    },\n    {\n      "allowed": [\n        {\n          "IPProtocol": "icmp"\n        }\n      ],\n      "creationTimestamp": "2026-01-05T14:30:33.903-08:00",\n      "description": "Allow ICMP from anywhere",\n      "direction": "INGRESS",\n      "disabled": false,\n      "id": "5305455786803168614",\n      "kind": "compute#firewall",\n      "logConfig": {\n        "enable": false\n      },\n      "name": "default-allow-icmp",\n      "network": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default",\n      "priority": 65534,\n      "selfLink": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/firewalls/default-allow-icmp",\n      "sourceRanges": [\n        "0.0.0.0/0"\n      ]\n    },\n    {\n      "allowed": [\n        {\n          "IPProtocol": "tcp",\n          "ports": [\n            "22"\n          ]\n        }\n      ],\n      "creationTimestamp": "2026-01-05T14:30:33.741-08:00",\n      "description": "Allow SSH from anywhere",\n      "direction": "INGRESS",\n      "disabled": false,\n      "id": "5910327979697866086",\n      "kind": "compute#firewall",\n      "logConfig": {\n        "enable": false\n      },\n      "name": "default-allow-ssh",\n      "network": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default",\n      "priority": 65534,\n      "selfLink": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/firewalls/default-allow-ssh",\n      "sourceRanges": [\n        "0.0.0.0/0"\n      ]\n    }\n  ]\n}\n'

REAL_HOSTKEYS = '[\n  {\n    "key": "ecdsa-sha2-nistp256",\n    "namespace": "hostkeys",\n    "value": "AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBP5yomg6p17Uoyq81/3JX15Z05YxRDE0gtPOoIUp03o/KqmWLf8IVIFe7kFFaO0lb7kxVGxbhZ/gYMjsDbxgl+8="\n  },\n  {\n    "key": "ssh-ed25519",\n    "namespace": "hostkeys",\n    "value": "AAAAC3NzaC1lZDI1NTE5AAAAIN2pFrUsLlgUbqItVqVwLLqnC+cpPPv29xXhHWYvqGCI"\n  },\n  {\n    "key": "ssh-rsa",\n    "namespace": "hostkeys",\n    "value": "AAAAB3NzaC1yc2EAAAADAQABAAABgQC63HzpRewfI4E6MsoAuQPo/KBN/SpCNkEn4bi2SYpGlxRdMfWiQgwZph4i3VP3NBcrlyRNRjk7k0IvesEcrCDvks1sD8EKSFYpdagg1MGWCpahpUSta216AIaldbm5folmFM3bGob+EtZY3o31s85PsEAvkLQpfmDaX+vgY3MRsqxuVQqOJcLAC7z6jAD5bzivcy5rFWqhFVj/+E74u7hMM5M0uii6QBoDQ11Or4iAWyGyCFFb+c0TB16BNJqmnf5bmz+Fpu+QrwELcClsYh1NJB6Gb7s7GHTUeqjKJJePs8uic8+MLGSyXBDES/NzfsjgayvDyJfphNJL+luKG9flP5udesBwwXEECOkoaboc+orCGBk6J3Iaf8oAZs+zFBLcUePl9XWIHEYdaIbBBPKB3ZkrxBaOoDu0vnjvmN88CGRQo6jVikXp+wkDhSATGUtGZC/9KDBY5eeLs7G6vRkkGJ6olrTAk1Qy4gXe4eVrtfgXPLDYJ0PR+nrWW2cNV1U="\n  }\n]\n'

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
                return {'status': self.status, 'fingerprint': self.fingerprint, 'items': dict(self.items),
                        'id': yogi_possession.INSTANCE_ID, 'tags': list(yogi_possession.REVIEWED_TAGS),
                        'network': self.network()}

            def network(self):
                return {'observed': True, 'allocationInferred': False, 'nics': [{
                    'name': 'nic0', 'stackType': 'IPV4_ONLY',
                    'network': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default',
                    'subnetwork': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/regions/us-west2/subnetworks/default',
                    'accessConfigs': [{'name': 'External NAT', 'type': 'ONE_TO_ONE_NAT', 'networkTier': 'PREMIUM',
                                       'addressPresent': False}],
                    'ipv6AccessConfigs': []}]}

            def effective_firewalls(self):
                return json.loads(EFFECTIVE_FIREWALLS)

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

            def wait_refresh(self):
                self.calls.append('refresh')
                return 'ok'

            def inspect(self):
                self.calls.append('inspect')
                return {'purpose': 'inspect', 'status': 'complete', 'instanceId': yogi_possession.INSTANCE_ID,
                        'operationId': '12' * 16, 'cleanup': {
                            'possessMarkerLines': self.marker, 'authPathsRead': 2, 'accessCleanup': 'ok',
                            'possessComment': 'roseos-yogi-possess-' + operation,
                            'possessMaterial': yogi_possession.login_material(plan['loginLine'])}}

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
        material = yogi_possession.login_material(plan['loginLine'])
        self.assertFalse(yogi_possession.guest_key_removed(spent, operation, material))

        good = Driver(marker=0)
        done = yogi_possession.possession_run(good, plan, str(identity))
        self.assertTrue(done['guestAuthenticated'] and done['guestKeyRemoved'] and done['stopped'])
        self.assertFalse(done['hostKeyPinned'] or done['runnerEnabled'])
        self.assertEqual(good.calls[-3:], ['refresh', 'stop', 'inspect'])
        self.assertTrue(done['hostKeyHandshake'])
        self.assertFalse(yogi_possession.guest_key_removed(
            {'purpose': 'inspect', 'status': 'complete', 'instanceId': yogi_possession.INSTANCE_ID,
             'operationId': '12' * 16, 'cleanup': {'possessMarkerLines': 0}}, operation, material))

        class TimeoutStart(Driver):
            def start(self):
                self.calls.append('start-timeout')
                return 'timeout'

        timed = TimeoutStart()
        unresolved = yogi_possession.possession_run(timed, plan, str(identity))
        self.assertIn('stop', timed.calls)
        self.assertFalse(unresolved['guestAuthenticated'] or unresolved['hostKeyHandshake'])
        self.assertFalse(unresolved['stopped'] is True and unresolved['guestAuthenticated'])

        class StopFails(Driver):
            def stop(self):
                self.calls.append('stop')
                return 'unresolved'

        stopped = StopFails()
        outcome = yogi_possession.possession_run(stopped, plan, str(identity))
        self.assertTrue(outcome['hostKeyHandshake'])
        self.assertFalse(outcome['guestAuthenticated'] or outcome['stopped'] or outcome['clean'])

        class SshFails(Driver):
            def ssh(self, argv, timeout):
                self.calls.append(('ssh', timeout))
                raise yogi_possession.Refused('ssh probe failed')

        failed_ssh = SshFails()
        ssh_result = yogi_possession.possession_run(failed_ssh, plan, str(identity))
        self.assertFalse(ssh_result['hostKeyHandshake'] or ssh_result['guestAuthenticated'] or ssh_result['hostKeyPinned'])
        self.assertTrue(ssh_result['stopped'] and ssh_result['metadataLoginRemoved'])
        self.assertEqual(failed_ssh.items['ssh-keys'], 'other:ssh-ed25519 AAAA foreign')
        self.assertEqual(sum(1 for call in failed_ssh.calls if call[0] == 'metadata'), 2)

        class RemoveConflict(Driver):
            def set_metadata(self, fingerprint, items):
                self.calls.append(('metadata', fingerprint, sorted(items)))
                if sum(1 for call in self.calls if call[0] == 'metadata') > 1:
                    return 'conflict'
                self.items = dict(items)
                self.fingerprint += 'x'
                return 'ok'

        removal = RemoveConflict()
        removal_result = yogi_possession.possession_run(removal, plan, str(identity))
        self.assertFalse(removal_result['guestAuthenticated'] or removal_result['metadataLoginRemoved']
                         or removal_result['hostKeyPinned'])
        self.assertTrue(removal_result['stopped'] and removal_result['hostKeyHandshake'])
        self.assertEqual(sum(1 for call in removal.calls if call[0] == 'metadata'), 2)
        self.assertEqual(removal_result['cleanupInstructions']['loginLine'], plan['loginLine'])
        self.assertTrue(removal_result['cleanupInstructions']['doNotRetryBlindly'])
        self.assertNotIn(plan['loginLine'], removal_result['cleanupInstructions']['items'].get('ssh-keys', ''))

        class Unreadable(Driver):
            def inspect(self):
                self.calls.append('inspect')
                return {'purpose': 'inspect', 'status': 'complete', 'instanceId': yogi_possession.INSTANCE_ID,
                        'operationId': '12' * 16, 'cleanup': {
                            'possessMarkerLines': -1, 'authPathsRead': 1, 'accessCleanup': 'failed',
                            'possessComment': 'roseos-yogi-possess-' + operation,
                            'possessMaterial': yogi_possession.login_material(plan['loginLine'])}}

        unread = Unreadable()
        unread_result = yogi_possession.possession_run(unread, plan, str(identity))
        self.assertTrue(unread_result['hostKeyHandshake'] and unread_result['stopped'])
        self.assertFalse(unread_result['guestAuthenticated'] or unread_result['guestKeyRemoved']
                         or unread_result['clean'] or unread_result['hostKeyPinned'])

        class AddLanded(Driver):
            def set_metadata(self, fingerprint, items):
                self.calls.append(('metadata', fingerprint, sorted(items)))
                if fingerprint != self.fingerprint:
                    return 'conflict'
                self.items = dict(items)
                self.fingerprint += 'x'
                if sum(1 for call in self.calls if call[0] == 'metadata') == 1:
                    return 'unresolved'
                return 'ok'

        landed = AddLanded()
        landed_result = yogi_possession.possession_run(landed, plan, str(identity))
        self.assertTrue(landed_result['guestAuthenticated'] and landed_result['metadataLoginRemoved'])
        self.assertFalse(landed_result['hostKeyPinned'])
        self.assertEqual(sum(1 for call in landed.calls if call[0] == 'metadata'), 2)
        self.assertNotIn(plan['loginLine'], landed.items.get('ssh-keys', ''))

        class AddMissed(Driver):
            def set_metadata(self, fingerprint, items):
                self.calls.append(('metadata', fingerprint, sorted(items)))
                return 'unresolved'

        missed = AddMissed()
        missed_result = yogi_possession.possession_run(missed, plan, str(identity))
        self.assertFalse(missed_result['guestAuthenticated'] or missed_result['hostKeyHandshake'])
        self.assertTrue(missed_result['stopped'])
        self.assertEqual(sum(1 for call in missed.calls if call[0] == 'metadata'), 1)
        self.assertNotIn('ssh', [call[0] if isinstance(call, tuple) else call for call in missed.calls])

        class AddBlind(Driver):
            def describe(self):
                if any(isinstance(call, tuple) and call[0] == 'metadata' for call in self.calls):
                    raise yogi_possession.Refused('describe failed')
                return super().describe()

            def set_metadata(self, fingerprint, items):
                self.calls.append(('metadata', fingerprint, sorted(items)))
                return 'unresolved'

        blind = AddBlind()
        blind_result = yogi_possession.possession_run(blind, plan, str(identity))
        self.assertFalse(blind_result['guestAuthenticated'] or blind_result['metadataLoginRemoved'])
        self.assertTrue(blind_result['stopped'])
        self.assertEqual(blind_result['cleanupInstructions']['loginLine'], plan['loginLine'])
        self.assertTrue(blind_result['cleanupInstructions']['doNotRetryBlindly'])
        self.assertEqual(sum(1 for call in blind.calls if call[0] == 'metadata'), 1)

    def test_real_gcloud_list_matches_ce79_fingerprints(self):
        self.assertEqual(len(REAL_HOSTKEYS.encode()), 999)
        self.assertEqual(hashlib.sha256(REAL_HOSTKEYS.encode()).hexdigest(),
                         '85b3581216fbf8fff2a45edf74db937eb63afd75ef406540e797c3c65af81aab')
        observed = json.loads(REAL_HOSTKEYS)
        self.assertIsInstance(observed, list)
        self.assertEqual(len(observed), 3)
        found = yogi_possession.hostkeys_from_api(observed)
        self.assertEqual(set(found), {'ssh-ed25519', 'ecdsa-sha2-nistp256', 'ssh-rsa'})
        with self.assertRaises(yogi_possession.Refused):
            yogi_possession.hostkeys_from_api({'response': observed, 'provenance': {}})

    def test_metadata_post_conflict_is_a_single_attempt(self):
        import io
        import urllib.error
        calls = []

        def run(argv, capture_output=True, text=True, timeout=0):
            calls.append(timeout)
            class Result:
                returncode = 0
                stdout = 'token\n'
            return Result()

        def opener(request, timeout=0):
            calls.append(('post', timeout))
            raise urllib.error.HTTPError(request.full_url, 412, 'conflict', {}, io.BytesIO(b'{}'))

        driver = yogi_possession.GcloudDriver(run=run, opener=opener)
        self.assertEqual(driver.set_metadata('pSAPI4eI6q4=', yogi_possession.POSSESSION_BASE), 'conflict')
        self.assertEqual([item for item in calls if isinstance(item, tuple)], [('post', yogi_possession.METADATA_TIMEOUT)])

    def test_pending_metadata_waits_once_and_does_not_resubmit(self):
        import subprocess
        name = 'operation-1794028000000-65fa900000000-aaaaaaaa-bbbbbbbb'
        posts = []
        secret = 'supersecret-token-value'

        def run(argv, capture_output=True, text=True, timeout=0):
            if argv[1:3] == ['auth', 'print-access-token']:
                class Result:
                    returncode = 0
                    stdout = secret + '\n'
                return Result()
            if argv[1:4] == ['compute', 'instances', 'start']:
                self.assertIn('--async', argv)
                self.assertEqual(timeout, yogi_possession.DESCRIBE_TIMEOUT)
                raise subprocess.TimeoutExpired(argv, timeout)
            if argv[1:4] == ['compute', 'instances', 'stop']:
                self.assertIn('--async', argv)
                self.assertEqual(timeout, yogi_possession.DESCRIBE_TIMEOUT)
                raise subprocess.TimeoutExpired(argv, timeout)
            raise AssertionError(argv)

        class Response:
            def __init__(self, payload):
                self.payload, self.status = payload, 200
            def read(self, _n):
                return self.payload
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False

        def body(status):
            return json.dumps({
                'kind': 'compute#operation', 'name': name, 'status': status,
                'targetId': yogi_possession.INSTANCE_ID, 'operationType': 'setMetadata',
                'zone': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b',
            }).encode()

        def opener(request, timeout=0):
            posts.append((request.full_url, timeout, request.data))
            self.assertNotIn(secret.encode(), request.full_url.encode())
            if request.full_url == yogi_possession.METADATA_URL:
                return Response(body('PENDING'))
            self.assertTrue(request.full_url.endswith('/operations/' + name + '/wait'))
            return Response(body('DONE'))

        driver = yogi_possession.GcloudDriver(run=run, opener=opener, sleep=lambda _seconds: None, clock=lambda: 0)
        self.assertEqual(driver.set_metadata('pSAPI4eI6q4=', yogi_possession.POSSESSION_BASE), 'ok')
        self.assertEqual(sum(1 for url, _timeout, _data in posts if url == yogi_possession.METADATA_URL), 1)
        self.assertEqual(sum(1 for url, _timeout, _data in posts if url.endswith('/wait')), 1)
        self.assertNotIn(secret, json.dumps(posts, default=str))

        def explode(request, timeout=0):
            raise RuntimeError('Bearer ' + secret)

        leaking = yogi_possession.GcloudDriver(run=run, opener=explode)
        try:
            leaked = leaking.set_metadata('pSAPI4eI6q4=', yogi_possession.POSSESSION_BASE)
        except yogi_possession.Refused as error:
            self.assertNotIn(secret, str(error))
            leaked = 'raised'
        else:
            self.assertEqual(leaked, 'unresolved')
        self.assertNotIn(secret, str(leaked))
        self.assertEqual(driver.start(), 'timeout')
        self.assertEqual(driver.stop(), 'unresolved')

        stalls = []

        def pending_forever(request, timeout=0):
            stalls.append(request.full_url)
            return Response(body('PENDING'))

        stuck = yogi_possession.GcloudDriver(run=run, opener=pending_forever, sleep=lambda _seconds: None, clock=lambda: 0)
        self.assertEqual(stuck.set_metadata('pSAPI4eI6q4=', yogi_possession.POSSESSION_BASE), 'unresolved')
        self.assertEqual(sum(1 for url in stalls if url == yogi_possession.METADATA_URL), 1)
        self.assertGreaterEqual(sum(1 for url in stalls if url.endswith('/wait')), 1)

    def test_preflight_requires_the_reviewed_baseline(self):
        class Gate:
            def __init__(self, fingerprint, items):
                self.fingerprint, self.items = fingerprint, items
            def account(self):
                return yogi_possession.OPERATOR_ACCOUNT
            def describe(self):
                return {'status': 'TERMINATED', 'id': yogi_possession.INSTANCE_ID,
                        'fingerprint': self.fingerprint, 'items': self.items,
                        'tags': list(yogi_possession.REVIEWED_TAGS),
                        'network': {'observed': True, 'allocationInferred': False, 'nics': [{
                            'name': 'nic0', 'stackType': 'IPV4_ONLY',
                            'network': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default',
                            'subnetwork': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/regions/us-west2/subnetworks/default',
                            'accessConfigs': [{'name': 'External NAT', 'type': 'ONE_TO_ONE_NAT',
                                               'networkTier': 'PREMIUM', 'addressPresent': False}],
                            'ipv6AccessConfigs': []}]}}

            def effective_firewalls(self):
                return json.loads(EFFECTIVE_FIREWALLS)

        baseline = yogi_possession.possession_preflight(Gate(
            yogi_possession.POSSESSION_FINGERPRINT, dict(yogi_possession.POSSESSION_BASE)))
        self.assertEqual(baseline['fingerprint'], 'pSAPI4eI6q4=')
        drifted = dict(yogi_possession.POSSESSION_BASE, **{'ssh-keys': 'other:ssh-ed25519 AAAA foreign'})
        with self.assertRaises(yogi_possession.Refused):
            yogi_possession.possession_preflight(Gate(yogi_possession.POSSESSION_FINGERPRINT, drifted))
        with self.assertRaises(yogi_possession.Refused):
            yogi_possession.possession_preflight(Gate('other-fingerprint', dict(yogi_possession.POSSESSION_BASE)))

        class NoStart(Gate):
            def start(self):
                raise AssertionError('started')

        directory = Path(tempfile.mkdtemp())
        hostkeys = directory / 'hostkeys.json'
        hostkeys.write_text(REAL_HOSTKEYS)
        client = directory / 'client.pub'
        client.write_text('ssh-ed25519 ' + base64.b64encode(_wire(b'ssh-ed25519', bytes(range(32, 64)))).decode() + '\n')
        identity = directory / 'id_ed25519'
        identity.write_text('not-read')
        identity.chmod(0o600)
        with self.assertRaises(yogi_possession.Refused):
            yogi_possession.execute_handshake(str(hostkeys), str(client), str(identity), str(directory / 'out'),
                                             driver=NoStart('other-fingerprint', dict(yogi_possession.POSSESSION_BASE)))


    def test_installed_line_keeps_the_comment_and_the_blob_still_counts(self):
        _keys, expected, api = self._api()
        client = 'ssh-ed25519 ' + base64.b64encode(_wire(b'ssh-ed25519', bytes(range(32, 64)))).decode()
        operation = 'ef' * 16
        now = datetime.datetime(2026, 11, 7, 18, 0, tzinfo=datetime.timezone.utc)
        plan = yogi_possession.possession_plan(api, client, operation, expected=expected,
                                              environ={'HOME': '/home/operator'}, now=now)
        installed = yogi_possession.installed_key_text(plan['loginLine'])
        material = yogi_possession.login_material(plan['loginLine'])
        self.assertTrue(installed.startswith('ssh-ed25519 ' + material + ' google-ssh '))
        self.assertIn('roseos-yogi-possess-' + operation, installed)
        self.assertIn('"expireOn":"', installed)
        rebuilt = 'ssh-ed25519 ' + material + ' google-ssh {"expireOn":"2026-11-07T18:20:00+0000","userName":"pixelbuild"}'
        self.assertNotIn('roseos-yogi-possess-', rebuilt)
        self.assertIn(material, rebuilt)
        self.assertNotIn('|', plan['argv'][-1])
        self.assertNotIn('||', plan['argv'][-1])
        self.assertEqual(plan['argv'][0], 'ssh')

    def test_gcloud_driver_failures_stop_and_do_not_count_a_nonzero_ssh(self):
        import io
        import subprocess
        import sys
        import urllib.error
        keys, expected, api = self._api()
        client = 'ssh-ed25519 ' + base64.b64encode(_wire(b'ssh-ed25519', bytes(range(32, 64)))).decode()
        operation = 'cd' * 16
        now = datetime.datetime(2026, 11, 7, 18, 0, tzinfo=datetime.timezone.utc)
        plan = yogi_possession.possession_plan(api, client, operation, expected=expected,
                                              environ={'HOME': '/home/operator'}, now=now)
        identity = Path(tempfile.mkdtemp()) / 'id_ed25519'
        identity.write_text('not-read')
        identity.chmod(0o600)
        material = yogi_possession.login_material(plan['loginLine'])
        op_name = 'operation-1794029000000-65fa910000000-cccccccc-dddddddd'

        class Response:
            def __init__(self, payload):
                self.payload, self.status = payload, 200
            def read(self, _n):
                return self.payload
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False

        def operation_body(status):
            return json.dumps({
                'kind': 'compute#operation', 'name': op_name, 'status': status,
                'targetId': yogi_possession.INSTANCE_ID, 'operationType': 'setMetadata',
                'zone': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b',
            }).encode()

        def case(start='ok', stop='ok', metadata='done', ssh_code=0, receipt=None):
            state = {'status': 'TERMINATED', 'items': dict(yogi_possession.POSSESSION_BASE),
                     'fingerprint': yogi_possession.POSSESSION_FINGERPRINT}
            commands, posts = [], []

            def instance():
                return json.dumps({
                    'id': yogi_possession.INSTANCE_ID, 'name': yogi_possession.INSTANCE,
                    'status': state['status'],
                    'zone': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b',
                    'selfLink': ('https://www.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b'
                                 '/instances/' + yogi_possession.INSTANCE),
                    'networkInterfaces': [{
                        'name': 'nic0', 'stackType': 'IPV4_ONLY',
                        'network': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default',
                        'subnetwork': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/regions/us-west2/subnetworks/default',
                        'accessConfigs': [{'name': 'External NAT', 'type': 'ONE_TO_ONE_NAT', 'networkTier': 'PREMIUM'}],
                    }],
                    'tags': {'items': list(yogi_possession.REVIEWED_TAGS)},
                    'metadata': {'fingerprint': state['fingerprint'],
                                 'items': [{'key': key, 'value': value} for key, value in state['items'].items()]},
                })

            def run(argv, capture_output=True, text=True, timeout=0):
                commands.append((list(argv), timeout))
                class Result:
                    returncode = 0
                    stdout = ''
                if argv[:3] == ['gcloud', 'auth', 'list']:
                    self.assertEqual(timeout, yogi_possession.DESCRIBE_TIMEOUT)
                    Result.stdout = json.dumps([{'account': yogi_possession.OPERATOR_ACCOUNT, 'status': 'ACTIVE'}])
                    return Result()
                if argv[:3] == ['gcloud', 'auth', 'print-access-token']:
                    self.assertEqual(timeout, yogi_possession.TOKEN_TIMEOUT)
                    Result.stdout = 'ya29.not-a-real-token\n'
                    return Result()
                if argv[1:5] == ['compute', 'instances', 'network-interfaces', 'get-effective-firewalls']:
                    self.assertIn('--network-interface=nic0', argv)
                    self.assertIn('--format=json', argv)
                    Result.stdout = EFFECTIVE_FIREWALLS
                    return Result()
                if argv[:4] == ['gcloud', 'compute', 'instances', 'describe']:
                    self.assertEqual(timeout, yogi_possession.DESCRIBE_TIMEOUT)
                    Result.stdout = instance()
                    return Result()
                if argv[:4] == ['gcloud', 'compute', 'instances', 'start']:
                    self.assertIn('--async', argv)
                    self.assertEqual(timeout, yogi_possession.DESCRIBE_TIMEOUT)
                    if start == 'timeout':
                        raise subprocess.TimeoutExpired(argv, timeout)
                    if start == 'failed':
                        Result.returncode = 1
                        return Result()
                    state['status'] = 'STAGING' if start == 'staging' else 'RUNNING'
                    Result.stdout = json.dumps({'name': 'operation-1794068000000-65fb200000000-aaaaaaaa-bbbbbbbb',
                                                'operationType': 'start', 'status': 'PENDING',
                                                'targetId': yogi_possession.INSTANCE_ID})
                    return Result()
                if argv[:4] == ['gcloud', 'compute', 'operations', 'describe']:
                    self.assertEqual(timeout, yogi_possession.DESCRIBE_TIMEOUT)
                    kind = 'stop' if 'cccccccc' in argv[4] else 'start'
                    Result.stdout = json.dumps({'name': argv[4], 'operationType': kind, 'status': 'DONE',
                                                'targetId': yogi_possession.INSTANCE_ID, 'progress': 100})
                    return Result()
                if argv[:4] == ['gcloud', 'compute', 'instances', 'stop']:
                    self.assertIn('--async', argv)
                    self.assertEqual(timeout, yogi_possession.DESCRIBE_TIMEOUT)
                    if stop == 'timeout':
                        raise subprocess.TimeoutExpired(argv, timeout)
                    state['status'] = 'RUNNING' if stop == 'running' else 'TERMINATED'
                    Result.stdout = json.dumps({'name': 'operation-1794068001000-65fb200000100-cccccccc-dddddddd',
                                                'operationType': 'stop', 'status': 'PENDING',
                                                'targetId': yogi_possession.INSTANCE_ID})
                    return Result()
                if argv and argv[0] == 'ssh':
                    self.assertEqual(timeout, yogi_possession.SSH_TIMEOUT)
                    self.assertNotIn('|', ' '.join(argv))
                    Result.returncode = ssh_code
                    Result.stdout = json.dumps({
                        'runnerProcess': False, 'active': 'inactive', 'enabled': 'disabled',
                        'builder': {'instanceId': yogi_possession.INSTANCE_ID}})
                    return Result()
                if argv and argv[0] == sys.executable and 'apply' in argv:
                    out = Path(argv[argv.index('--op') + 1])
                    out.mkdir(parents=True, exist_ok=True)
                    (out / 'outcome.json').write_text(json.dumps({'guestReceipt': receipt}))
                    return Result()
                if argv and argv[0] == sys.executable:
                    return Result()
                raise AssertionError(argv)

            def opener(request, timeout=0):
                posts.append(request.full_url)
                if request.full_url == yogi_possession.METADATA_URL:
                    self.assertEqual(timeout, yogi_possession.METADATA_TIMEOUT)
                    nth = posts.count(yogi_possession.METADATA_URL)
                    if metadata == 'timeout' and nth == 1:
                        raise TimeoutError('metadata timed out')
                    if metadata == 'conflict-add' or (metadata == 'conflict-remove' and nth == 2):
                        raise urllib.error.HTTPError(request.full_url, 412, 'conflict', {}, io.BytesIO(b'{}'))
                    if metadata == 'pending' and nth == 1:
                        return Response(operation_body('PENDING'))
                    body = json.loads(request.data)
                    state['items'] = {item['key']: item['value'] for item in body['items']}
                    state['fingerprint'] += 'x'
                    return Response(operation_body('DONE'))
                self.assertTrue(request.full_url.endswith('/operations/' + op_name + '/wait'))
                return Response(operation_body('PENDING'))

            driver = yogi_possession.GcloudDriver(run=run, opener=opener, sleep=lambda _seconds: None, clock=lambda: 0,
                                                 inspect_out=str(Path(tempfile.mkdtemp()) / 'inspect'))
            driver.possess_comment = 'roseos-yogi-possess-' + operation
            driver.possess_material = material
            result = yogi_possession.possession_run(driver, plan, str(identity), baseline={
                'fingerprint': yogi_possession.POSSESSION_FINGERPRINT, 'items': dict(yogi_possession.POSSESSION_BASE)})
            return result, commands, posts

        def stopped(commands):
            return any(argv[:4] == ['gcloud', 'compute', 'instances', 'stop'] for argv, _timeout in commands)

        def metadata_posts(posts):
            return sum(1 for url in posts if url == yogi_possession.METADATA_URL)

        timed, timed_commands, timed_posts = case(start='timeout')
        self.assertTrue(stopped(timed_commands))
        self.assertEqual(metadata_posts(timed_posts), 0)
        self.assertFalse(timed['guestAuthenticated'] or timed['hostKeyHandshake'] or timed['hostKeyPinned'])

        failed, failed_commands, failed_posts = case(start='failed')
        self.assertTrue(stopped(failed_commands))
        self.assertEqual(metadata_posts(failed_posts), 0)
        self.assertFalse(failed['guestAuthenticated'])

        staging, staging_commands, _posts = case(start='staging')
        self.assertTrue(stopped(staging_commands))
        self.assertFalse(staging['guestAuthenticated'] or staging['hostKeyHandshake'])

        pending, pending_commands, pending_posts = case(metadata='pending')
        self.assertTrue(stopped(pending_commands))
        self.assertEqual(metadata_posts(pending_posts), 1)
        self.assertGreaterEqual(sum(1 for url in pending_posts if url.endswith('/wait')), 1)
        self.assertFalse(any(argv[0] == 'ssh' for argv, _timeout in pending_commands))
        self.assertFalse(pending['guestAuthenticated'])

        conflict, conflict_commands, conflict_posts = case(metadata='conflict-remove')
        self.assertTrue(conflict['hostKeyHandshake'] and conflict['stopped'])
        self.assertFalse(conflict['guestAuthenticated'] or conflict['metadataLoginRemoved'] or conflict['hostKeyPinned'])
        self.assertEqual(metadata_posts(conflict_posts), 2)
        self.assertEqual(conflict['cleanupInstructions']['loginLine'], plan['loginLine'])
        self.assertNotIn(plan['loginLine'], conflict['cleanupInstructions']['items'].get('ssh-keys', ''))
        self.assertFalse(any(argv[0] == sys.executable for argv, _timeout in conflict_commands))

        nonzero, _nonzero_commands, nonzero_posts = case(ssh_code=1)
        self.assertFalse(nonzero['hostKeyHandshake'] or nonzero['guestAuthenticated'] or nonzero['hostKeyPinned'])
        self.assertEqual(metadata_posts(nonzero_posts), 2)
        self.assertTrue(nonzero['stopped'])

        halted, halted_commands, _halted_posts = case(stop='timeout')
        self.assertTrue(halted['hostKeyHandshake'])
        self.assertFalse(halted['guestAuthenticated'] or halted['stopped'] or halted['clean'] or halted['hostKeyPinned'])
        self.assertFalse(any('apply' in argv for argv, _timeout in halted_commands))

        missing = {'purpose': 'inspect', 'status': 'complete', 'instanceId': yogi_possession.INSTANCE_ID,
                   'operationId': '12' * 16, 'cleanup': {'accessCleanup': 'ok', 'authPathsRead': 2,
                   'possessComment': 'roseos-yogi-possess-' + operation, 'possessMaterial': material}}
        unread = dict(missing, cleanup={'possessMarkerLines': -1, 'authPathsRead': 1, 'accessCleanup': 'failed',
                      'possessComment': 'roseos-yogi-possess-' + operation, 'possessMaterial': material})
        good_receipt = {'purpose': 'inspect', 'status': 'complete', 'instanceId': yogi_possession.INSTANCE_ID,
                        'operationId': '12' * 16, 'cleanup': {
                            'possessMarkerLines': 0, 'authPathsRead': 2, 'accessCleanup': 'ok',
                            'possessComment': 'roseos-yogi-possess-' + operation, 'possessMaterial': material}}
        for receipt in (missing, unread):
            result, commands, _posts = case(receipt=receipt)
            self.assertTrue(result['hostKeyHandshake'] and result['stopped'])
            self.assertFalse(result['guestAuthenticated'] or result['guestKeyRemoved'] or result['hostKeyPinned'])
            self.assertTrue(any('apply' in argv for argv, _timeout in commands))
        done, done_commands, _done_posts = case(receipt=good_receipt)
        self.assertTrue(done['guestAuthenticated'] and done['guestKeyRemoved'] and done['clean'])
        self.assertFalse(done['hostKeyPinned'])
        self.assertTrue(any('plan' in argv and '--possess-material' in argv for argv, _timeout in done_commands))
        self.assertTrue(any('preflight' in argv and '--execute' in argv for argv, _timeout in done_commands))
        self.assertTrue(any('apply' in argv and '--execute' in argv for argv, _timeout in done_commands))
        self.assertNotIn('ya29.not-a-real-token', json.dumps(done))

    def test_one_execute_invocation_plans_a_fresh_inspect(self):
        import yogi_replacement_recover as recover
        directory = Path(tempfile.mkdtemp())
        def pair(first, second):
            values = [first, second]
            return lambda: values.pop(0)
        with self.assertRaises(recover.Refused):
            recover.plan('possession', str(directory / 'spent'), mode='inspect', revision='a' * 40, env={},
                         token=pair('638e6e9039e41b7531697bb45066a616', 'ab' * 16))
        with self.assertRaises(recover.Refused):
            recover.plan('possession', str(directory / 'ce79'), mode='inspect', revision='a' * 40, env={},
                         token=pair('ce79ffe4' + 'cd' * 12, 'ab' * 16))
        fresh = recover.plan('possession', str(directory / 'fresh'), mode='inspect', revision='a' * 40, env={},
                             token=pair('12' * 16, '34' * 16), possess_comment='roseos-yogi-possess-' + 'ef' * 16,
                             possess_material='BLOB')
        self.assertEqual((fresh['operationId'], fresh['nonce']), ('12' * 16, '34' * 16))
        guest = (directory / 'fresh' / 'guest.py').read_text()
        self.assertNotIn('@@', guest)
        self.assertIn('BLOB', guest)
        self.assertNotIn('638e6e9039e41b7531697bb45066a616', guest)
        self.assertNotIn('ce79ffe4', guest)


    def _clock(self):
        class Clock:
            def __init__(self):
                self.now = 0.0
            def __call__(self):
                return self.now
            def sleep(self, seconds):
                self.now += seconds
        return Clock()

    def _instance_json(self, status):
        return json.dumps({
            'id': yogi_possession.INSTANCE_ID, 'name': yogi_possession.INSTANCE, 'status': status,
            'zone': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b',
            'selfLink': ('https://www.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b/instances/'
                         + yogi_possession.INSTANCE),
            'networkInterfaces': [{}],
            'metadata': {'fingerprint': yogi_possession.POSSESSION_FINGERPRINT,
                         'items': [{'key': key, 'value': value}
                                   for key, value in yogi_possession.POSSESSION_BASE.items()]},
        })

    def test_a_done_start_is_polled_until_running_and_stop_waits_past_120_seconds(self):
        start_name = 'operation-1794068100000-65fb210000000-11111111-22222222'
        stop_name = 'operation-1794068200000-65fb220000000-33333333-44444444'
        clock = self._clock()
        commands = []

        def operation(name, kind, status, error=None):
            body = {'name': name, 'operationType': kind, 'status': status, 'targetId': yogi_possession.INSTANCE_ID,
                    'progress': 100 if status == 'DONE' else 0,
                    'zone': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b'}
            if error:
                body['error'] = {'errors': [{'code': error}]}
            if status == 'DONE':
                body['endTime'] = '2026-11-07T07:57:55.387-08:00'
            return json.dumps(body)

        def run_start(argv, capture_output=True, text=True, timeout=0):
            commands.append(list(argv))
            class Result:
                returncode = 0
                stdout = ''
            if argv[1:4] == ['compute', 'instances', 'describe']:
                Result.stdout = self._instance_json('STAGING' if clock.now < 8 else 'RUNNING')
                return Result()
            if argv[1:4] == ['compute', 'instances', 'start']:
                Result.stdout = operation(start_name, 'start', 'PENDING')
                return Result()
            if argv[1:4] == ['compute', 'operations', 'describe']:
                Result.stdout = operation(start_name, 'start', 'DONE' if clock.now >= 6 else 'RUNNING')
                return Result()
            raise AssertionError(argv)

        driver = yogi_possession.GcloudDriver(run=run_start, sleep=clock.sleep, clock=clock)
        self.assertEqual(driver.start(), 'started')
        self.assertEqual(driver.start_report['observed'], 'running')
        self.assertEqual(driver.start_report['operation']['status'], 'DONE')
        self.assertEqual(driver.start_report['operation']['targetId'], yogi_possession.INSTANCE_ID)
        self.assertGreaterEqual(clock.now, 8)
        self.assertLess(clock.now, 120)
        self.assertFalse(any(argv[1:4] == ['compute', 'instances', 'stop'] for argv in commands))
        self.assertNotIn('selfLink', json.dumps(driver.start_report))

        clock.now = 0
        def run_stop(argv, capture_output=True, text=True, timeout=0):
            commands.append(list(argv))
            class Result:
                returncode = 0
                stdout = ''
            if argv[1:4] == ['compute', 'instances', 'stop']:
                Result.stdout = operation(stop_name, 'stop', 'RUNNING')
                return Result()
            if argv[1:4] == ['compute', 'operations', 'describe']:
                Result.stdout = operation(stop_name, 'stop', 'DONE' if clock.now >= 138 else 'RUNNING')
                return Result()
            if argv[1:4] == ['compute', 'instances', 'describe']:
                Result.stdout = self._instance_json('TERMINATED' if clock.now >= 138 else 'STOPPING')
                return Result()
            raise AssertionError(argv)
        driver = yogi_possession.GcloudDriver(run=run_stop, sleep=clock.sleep, clock=clock)
        self.assertEqual(driver.stop(), 'stopped')
        self.assertEqual(driver.stop_report['observed'], 'terminated')
        self.assertEqual(driver.stop_report['instanceStatus'], 'TERMINATED')
        self.assertEqual(driver.stop_report['operation']['status'], 'DONE')
        self.assertEqual(driver.stop_report['operation']['endTime'], '2026-11-07T07:57:55.387-08:00')
        # The instance stays STOPPING and the operation stays RUNNING until 138s.
        # The old 120s client wait would already have aborted. The 240s budget has not.
        self.assertGreater(clock.now, 120)
        self.assertGreaterEqual(clock.now, 138)
        self.assertLess(clock.now, yogi_possession.STOP_WAIT_SECONDS)
        self.assertGreater(yogi_possession.STOP_WAIT_SECONDS, 120)

        clock.now = 0
        short = yogi_possession.GcloudDriver(run=run_stop, sleep=clock.sleep, clock=clock)
        original = yogi_possession.STOP_WAIT_SECONDS
        yogi_possession.STOP_WAIT_SECONDS = 120
        try:
            self.assertEqual(short.stop(), 'unresolved')
        finally:
            yogi_possession.STOP_WAIT_SECONDS = original
        self.assertEqual(short.stop_report['observed'], 'unresolved')
        self.assertEqual(short.stop_report['instanceStatus'], 'STOPPING')
        self.assertEqual(short.stop_report['operation']['status'], 'RUNNING')
        self.assertGreaterEqual(clock.now, 120)
        self.assertLess(clock.now, 138)

    def test_operation_error_drift_and_a_pending_budget_stay_unresolved(self):
        clock = self._clock()
        name = 'operation-1794068300000-65fb230000000-55555555-66666666'

        def run(argv, capture_output=True, text=True, timeout=0):
            class Result:
                returncode = 0
                stdout = ''
            if argv[1:4] == ['compute', 'instances', 'start']:
                Result.stdout = json.dumps({'name': name, 'operationType': 'start', 'status': 'PENDING',
                                            'targetId': yogi_possession.INSTANCE_ID})
                return Result()
            if argv[1:4] == ['compute', 'operations', 'describe']:
                Result.stdout = json.dumps({'name': name, 'operationType': 'start', 'status': 'DONE',
                                            'targetId': yogi_possession.INSTANCE_ID,
                                            'error': {'errors': [{'code': 'ZONE_RESOURCE_POOL_EXHAUSTED'}]}})
                return Result()
            if argv[1:4] == ['compute', 'instances', 'describe']:
                Result.stdout = self._instance_json('TERMINATED')
                return Result()
            raise AssertionError(argv)

        driver = yogi_possession.GcloudDriver(run=run, sleep=clock.sleep, clock=clock)
        self.assertEqual(driver.start(), 'failed')
        self.assertEqual(driver.start_report['operation']['error'], 'ZONE_RESOURCE_POOL_EXHAUSTED')
        self.assertNotIn('Bearer', json.dumps(driver.start_report))

        def drifted(argv, capture_output=True, text=True, timeout=0):
            class Result:
                returncode = 0
                stdout = ''
            if argv[1:4] == ['compute', 'instances', 'start']:
                Result.stdout = json.dumps({'name': name, 'operationType': 'start', 'status': 'DONE',
                                            'targetId': yogi_possession.INSTANCE_ID})
                return Result()
            if argv[1:4] == ['compute', 'instances', 'describe']:
                body = json.loads(self._instance_json('RUNNING'))
                body['id'] = '1'
                Result.stdout = json.dumps(body)
                return Result()
            raise AssertionError(argv)

        drifted_driver = yogi_possession.GcloudDriver(run=drifted, sleep=clock.sleep, clock=clock)
        self.assertEqual(drifted_driver.start(), 'failed')
        self.assertEqual(drifted_driver.start_report['describeError'], 'describe-identity')
        self.assertEqual(drifted_driver.start_report['instanceStatus'], 'RUNNING')

        def pending(argv, capture_output=True, text=True, timeout=0):
            class Result:
                returncode = 0
                stdout = ''
            if argv[1:4] == ['compute', 'instances', 'start']:
                Result.stdout = json.dumps({'name': name, 'operationType': 'start', 'status': 'PENDING',
                                            'targetId': yogi_possession.INSTANCE_ID})
                return Result()
            if argv[1:4] == ['compute', 'operations', 'describe']:
                Result.stdout = json.dumps({'name': name, 'operationType': 'start', 'status': 'PENDING',
                                            'targetId': yogi_possession.INSTANCE_ID})
                return Result()
            if argv[1:4] == ['compute', 'instances', 'describe']:
                Result.stdout = self._instance_json('STAGING')
                return Result()
            raise AssertionError(argv)

        waiting = yogi_possession.GcloudDriver(run=pending, sleep=clock.sleep, clock=clock)
        self.assertEqual(waiting.start(), 'timeout')
        self.assertEqual(waiting.start_report['observed'], 'unresolved')
        self.assertEqual(waiting.start_report['operation']['status'], 'PENDING')
        self.assertEqual(waiting.start_report['instanceStatus'], 'STAGING')
        self.assertGreaterEqual(clock.now, yogi_possession.START_WAIT_SECONDS)

    def test_cli_exit_is_1_when_the_guest_is_not_authenticated(self):
        import os
        import subprocess
        import sys
        directory = Path(tempfile.mkdtemp())
        hostkeys = directory / 'hostkeys.json'
        hostkeys.write_text(REAL_HOSTKEYS)
        client = directory / 'client.pub'
        client.write_text('ssh-ed25519 ' + base64.b64encode(_wire(b'ssh-ed25519', bytes(range(32, 64)))).decode() + '\n')
        identity = directory / 'id_ed25519'
        identity.write_text('not-read')
        identity.chmod(0o600)
        out = directory / 'out'
        bin_dir = directory / 'bin'
        bin_dir.mkdir()
        state = directory / 'describes'
        state.write_text('0')
        (directory / 'firewalls.json').write_text(EFFECTIVE_FIREWALLS)
        (bin_dir / 'gcloud').write_text(
            '#!/usr/bin/env python3\n'
            'import json, sys\n'
            'from pathlib import Path\n'
            'args = sys.argv[1:]\n'
            f'state = Path({str(state)!r})\n'
            'instance = ' + json.dumps(yogi_possession.INSTANCE_ID) + '\n'
            'name = ' + json.dumps(yogi_possession.INSTANCE) + '\n'
            'zone = "https://www.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b"\n'
            'def emit(body, code=0):\n'
            '    print(json.dumps(body))\n'
            '    raise SystemExit(code)\n'
            'if args[:2] == ["auth", "list"]:\n'
            '    emit([{"account": "grok-bots@protocols-465723.iam.gserviceaccount.com", "status": "ACTIVE"}])\n'
            'if args[:2] == ["auth", "print-access-token"]:\n'
            '    raise SystemExit(1)\n'
            'if args[:3] == ["compute", "instances", "describe"]:\n'
            '    n = int(state.read_text()); state.write_text(str(n + 1))\n'
            '    status = {0: "TERMINATED", 1: "TERMINATED", 2: "STAGING", 3: "RUNNING"}.get(n, "TERMINATED")\n'
            '    emit({"id": instance, "name": name, "status": status, "zone": zone,\n'
            '          "selfLink": zone + "/instances/" + name,\n'
            '          "tags": {"items": ["roseos-yogi-b-20261105", "roseos-yogi-builder"]},\n'
            '          "networkInterfaces": [{"name": "nic0", "stackType": "IPV4_ONLY",\n'
            '            "network": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default",\n'
            '            "subnetwork": "https://www.googleapis.com/compute/v1/projects/protocols-465723/regions/us-west2/subnetworks/default",\n'
            '            "accessConfigs": [{"name": "External NAT", "type": "ONE_TO_ONE_NAT", "networkTier": "PREMIUM"}]}],\n'
            '          "metadata": {"fingerprint": "pSAPI4eI6q4=", "items": [\n'
            '              {"key": "block-project-ssh-keys", "value": "TRUE"},\n'
            '              {"key": "enable-oslogin", "value": "FALSE"}]}})\n'
            'if args[:4] == ["compute", "instances", "network-interfaces", "get-effective-firewalls"]:\n'
            f'    print(Path({str(directory / "firewalls.json")!r}).read_text())\n'
            '    raise SystemExit(0)\n'
            'if args[:3] == ["compute", "instances", "start"]:\n'
            '    emit({"name": "operation-1794068400000-65fb240000000-77777777-88888888",\n'
            '          "operationType": "start", "status": "PENDING", "targetId": instance})\n'
            'if args[:3] == ["compute", "instances", "stop"]:\n'
            '    emit({"name": "operation-1794068500000-65fb250000000-99999999-aaaaaaa0",\n'
            '          "operationType": "stop", "status": "RUNNING", "targetId": instance})\n'
            'if args[:3] == ["compute", "operations", "describe"]:\n'
            '    kind = "start" if args[3].endswith("77777777-88888888") else "stop"\n'
            '    emit({"name": args[3], "operationType": kind, "status": "DONE", "targetId": instance,\n'
            '          "progress": 100})\n'
            'raise SystemExit("unexpected " + " ".join(args))\n')
        (bin_dir / 'gcloud').chmod(0o755)
        env = os.environ.copy()
        for key in ('GOOGLE_APPLICATION_CREDENTIALS', 'CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT',
                    'GITHUB_ACTIONS', 'ACTIONS_ID_TOKEN_REQUEST_URL', 'ACTIONS_ID_TOKEN_REQUEST_TOKEN',
                    'ACTIONS_RUNTIME_TOKEN', 'ACTIONS_RUNTIME_URL', 'ACTIONS_CACHE_URL', 'GITHUB_TOKEN'):
            env.pop(key, None)
        env['PATH'] = str(bin_dir) + os.pathsep + env.get('PATH', '')
        cli = _possession_root() / 'yogi_possession.py'
        plan = subprocess.run([sys.executable, '-B', str(cli), 'handshake-plan',
                               '--hostkeys', str(hostkeys), '--client-public', str(client),
                               '--out', str(directory / 'plan')], cwd=str(cli.parent), env=env,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertFalse(json.loads(plan.stdout)['executed'])
        proc = subprocess.run([sys.executable, '-B', str(cli), 'handshake', '--execute',
                               '--hostkeys', str(hostkeys), '--client-public', str(client),
                               '--identity', str(identity), '--out', str(out)], cwd=str(cli.parent), env=env,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(proc.stderr, '')
        outcome = json.loads((out / 'possession-outcome.json').read_text())
        self.assertFalse(outcome['guestAuthenticated'] or outcome['hostKeyPinned'] or outcome['hostKeyHandshake'])
        self.assertEqual(outcome['startReport']['observed'], 'running')
        self.assertEqual(outcome['startReport']['instanceStatus'], 'RUNNING')
        self.assertEqual(outcome['stopReport']['observed'], 'terminated')
        self.assertEqual(outcome['stopReport']['instanceStatus'], 'TERMINATED')
        self.assertNotIn('PRIVATE', json.dumps(outcome))
        self.assertNotIn('ya29', proc.stdout)


    def _gcloud_instance(self, status, nat=None, identity='2720796557371812468', items=None, raw_id=None):
        """The compute instances describe --format=json contract: one instance resource, not a status summary."""
        item_rows = [{'key': key, 'value': value} for key, value in (items or yogi_possession.POSSESSION_BASE).items()]
        access = {'kind': 'compute#accessConfig', 'name': 'External NAT', 'type': 'ONE_TO_ONE_NAT',
                  'networkTier': 'PREMIUM'}
        if nat is not None:
            access['natIP'] = nat
        nic = {
            'kind': 'compute#networkInterface', 'name': 'nic0',
            'network': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default',
            'subnetwork': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/regions/us-west2/subnetworks/default',
            'networkIP': '10.168.0.4', 'stackType': 'IPV4_ONLY', 'fingerprint': 'nic-fingerprint=',
            'accessConfigs': [access],
        }
        body = {
            'kind': 'compute#instance',
            'id': yogi_possession.INSTANCE_ID if raw_id is None else raw_id,
            'name': yogi_possession.INSTANCE,
            'status': status,
            'zone': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b',
            'machineType': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b/machineTypes/n2-standard-16',
            'cpuPlatform': 'Intel Cascade Lake' if status == 'RUNNING' else None,
            'networkInterfaces': [nic],
            'metadata': {'kind': 'compute#metadata', 'fingerprint': 'pSAPI4eI6q4=', 'items': item_rows},
            'selfLink': ('https://www.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b/instances/'
                         + yogi_possession.INSTANCE),
            'tags': {'items': list(yogi_possession.REVIEWED_TAGS)},
            'scheduling': {'automaticRestart': True, 'onHostMaintenance': 'MIGRATE'},
            'disks': [{'boot': True, 'source': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b/disks/'
                       + yogi_possession.INSTANCE}],
        }
        if identity != yogi_possession.INSTANCE_ID and raw_id is None:
            body['id'] = identity
        if body['cpuPlatform'] is None:
            del body['cpuPlatform']
        return json.dumps(body)

    def test_describe_uses_the_gcloud_instance_document(self):
        expected = ['gcloud', 'compute', 'instances', 'describe', yogi_possession.INSTANCE,
                    '--project=' + yogi_possession.PROJECT, '--zone=' + yogi_possession.ZONE,
                    '--format=json', '--quiet']

        def run(argv, capture_output=True, text=True, timeout=0):
            self.assertEqual(list(argv), expected)
            self.assertEqual(timeout, yogi_possession.DESCRIBE_TIMEOUT)
            class Result:
                returncode = 0
                stdout = run.stdout
            return Result()
        run.stdout = self._gcloud_instance('RUNNING')
        view = yogi_possession.GcloudDriver(run=run).describe()
        self.assertEqual(view['status'], 'RUNNING')
        self.assertEqual(view['fingerprint'], 'pSAPI4eI6q4=')
        self.assertEqual(view['items'], yogi_possession.POSSESSION_BASE)

        run.stdout = self._gcloud_instance('TERMINATED')
        self.assertEqual(yogi_possession.GcloudDriver(run=run).describe()['status'], 'TERMINATED')

        run.stdout = self._gcloud_instance('RUNNING', raw_id=int(yogi_possession.INSTANCE_ID))
        self.assertEqual(yogi_possession.GcloudDriver(run=run).describe()['id'], yogi_possession.INSTANCE_ID)

        run.stdout = self._gcloud_instance('RUNNING', nat='203.0.113.9')
        addressed = yogi_possession.GcloudDriver(run=run).describe()
        self.assertEqual(addressed['status'], 'RUNNING')
        self.assertTrue(addressed['network']['nics'][0]['accessConfigs'][0]['addressPresent'])
        run.stdout = self._gcloud_instance('TERMINATED')
        stopped = yogi_possession.GcloudDriver(run=run).describe()
        self.assertFalse(stopped['network']['nics'][0]['accessConfigs'][0]['addressPresent'])
        self.assertEqual(yogi_possession.network_identity(addressed), yogi_possession.network_identity(stopped))
        self.assertNotIn('203.0.113.9', json.dumps(addressed['network']))

        def fail(argv, capture_output=True, text=True, timeout=0):
            self.assertEqual(list(argv), expected)
            class Result:
                returncode = 1
                stdout = 'ERROR: not the instance body\n'
            return Result()
        with self.assertRaises(yogi_possession.DescribeRefused) as failed:
            yogi_possession.GcloudDriver(run=fail).describe()
        self.assertEqual(failed.exception.code, 'describe-exit')
        self.assertEqual(failed.exception.returncode, 1)
        self.assertEqual(failed.exception.stdout_class, 'other')
        self.assertNotIn('ERROR', str(failed.exception))

        clock = self._clock()
        calls = []

        def running_with_address(argv, capture_output=True, text=True, timeout=0):
            calls.append(list(argv))
            class Result:
                returncode = 0
                stdout = ''
            if argv[1:4] == ['compute', 'instances', 'start']:
                Result.stdout = json.dumps({'name': 'operation-1794068600000-65fb260000000-bbbbbbb1-cccccccc',
                                            'operationType': 'start', 'status': 'DONE',
                                            'targetId': yogi_possession.INSTANCE_ID, 'progress': 100})
                return Result()
            if argv[1:4] == ['compute', 'instances', 'describe']:
                self.assertEqual(list(argv), expected)
                Result.stdout = self._gcloud_instance('RUNNING', nat='203.0.113.9')
                return Result()
            raise AssertionError(argv)

        driver = yogi_possession.GcloudDriver(run=running_with_address, sleep=clock.sleep, clock=clock)
        self.assertEqual(driver.start(), 'started')
        self.assertEqual(driver.start_report['instanceStatus'], 'RUNNING')
        self.assertIsNone(driver.start_report['describeError'])
        self.assertNotIn('203.0.113.9', json.dumps(driver.start_report))

        import os
        import subprocess
        import sys
        directory = Path(tempfile.mkdtemp())
        bin_dir = directory / 'bin'
        bin_dir.mkdir()
        (directory / 'firewalls.json').write_text(EFFECTIVE_FIREWALLS)
        document = self._gcloud_instance('TERMINATED')
        (bin_dir / 'gcloud').write_text(
            '#!/usr/bin/env python3\n'
            'import json, sys\n'
            'from pathlib import Path\n'
            'args = sys.argv[1:]\n'
            'if args[:4] == ["compute", "instances", "network-interfaces", "get-effective-firewalls"]:\n'
            f'    print(Path({str(directory / "firewalls.json")!r}).read_text())\n'
            '    raise SystemExit(0)\n'
            'if args[:3] != ["compute", "instances", "describe"] or "--format=json" not in args:\n'
            '    raise SystemExit("refusing " + " ".join(args))\n'
            'if any(word in args for word in ("start", "stop", "set-metadata")):\n'
            '    raise SystemExit("mutation")\n'
            'print(' + json.dumps(document) + ')\n')
        (bin_dir / 'gcloud').chmod(0o755)
        env = os.environ.copy()
        env['PATH'] = str(bin_dir) + os.pathsep + env.get('PATH', '')
        for key in ('GOOGLE_APPLICATION_CREDENTIALS', 'CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT'):
            env.pop(key, None)
        cli = _possession_root() / 'yogi_possession.py'
        proc = subprocess.run([sys.executable, '-B', str(cli), 'describe-instance'], cwd=str(cli.parent),
                              env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        reported = json.loads(proc.stdout)
        self.assertTrue(reported['readOnly'])
        self.assertFalse(reported['mutation'])
        self.assertEqual(reported['status'], 'TERMINATED')
        self.assertEqual(reported['fingerprint'], 'pSAPI4eI6q4=')
        self.assertEqual(reported['metadataKeys'], ['block-project-ssh-keys', 'enable-oslogin'])
        self.assertIsNone(reported['describeError'])


    def test_effective_firewall_fixture_shadows_lower_allows_and_rejects_drift(self):
        import copy
        raw = EFFECTIVE_FIREWALLS.encode()
        self.assertEqual(len(raw), 7560)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         '64fa8369d59c8e9337518d0e78cd92080575402fc8be6bcd6167460e8e00bd2e')
        document = json.loads(EFFECTIVE_FIREWALLS)
        self.assertEqual(set(document), {'firewalls'})
        self.assertEqual(len(document['firewalls']), 9)
        decision = yogi_possession.evaluate_inbound(document)
        self.assertTrue(decision['ok'])
        self.assertIsNone(decision['code'])
        for name in ('default-allow-ssh', 'default-allow-rdp', 'default-allow-icmp', 'default-allow-internal',
                     'aet-uswest2-a--list--connector-tagfw', 'aet-uswest2-a--list--connector-sbntfw',
                     'roseos-yogi-iap-ssh'):
            self.assertIn(name, decision['shadowed'])
        self.assertEqual(yogi_possession.effective_firewall_argv()[1:6],
                         ['compute', 'instances', 'network-interfaces', 'get-effective-firewalls',
                          yogi_possession.INSTANCE])
        self.assertIn('--network-interface=nic0', yogi_possession.effective_firewall_argv())

        def rejected(mutate):
            body = json.loads(EFFECTIVE_FIREWALLS)
            mutate(body)
            found = yogi_possession.evaluate_inbound(body)
            self.assertFalse(found['ok'])
            return found['code']

        def add_rule(rule):
            def mutate(body):
                body['firewalls'].append(rule)
            return mutate

        public = {'name': 'public-ssh', 'direction': 'INGRESS', 'priority': 100, 'disabled': False,
                  'sourceRanges': ['0.0.0.0/0'], 'allowed': [{'IPProtocol': 'tcp', 'ports': ['22']}],
                  'network': document['firewalls'][0]['network']}
        self.assertEqual(rejected(add_rule(public)), 'firewall-unreviewed-priority')
        tied = dict(public, priority=901, name='tied-ssh')
        self.assertEqual(rejected(add_rule(tied)), 'firewall-unreviewed-priority')
        ipv6 = {'name': 'ipv6-ssh', 'direction': 'INGRESS', 'priority': 1000, 'disabled': False,
                'sourceRanges': ['::/0'], 'allowed': [{'IPProtocol': 'tcp', 'ports': ['22']}],
                'network': document['firewalls'][0]['network']}
        self.assertEqual(rejected(add_rule(ipv6)), 'firewall-public-inbound')
        self.assertEqual(rejected(lambda body: body.__setitem__('firewallPolicys', [{'name': 'org'}])),
                         'firewall-unreviewed-policy')
        self.assertEqual(rejected(lambda body: body.__setitem__('firewallPolicys', [])),
                         'firewall-unreviewed-policy')
        self.assertEqual(rejected(lambda body: body['firewalls'][0].__setitem__('unexpected', True)),
                         'firewall-unknown-shape')

        def drop_deny(body):
            body['firewalls'] = [rule for rule in body['firewalls'] if rule['name'] != 'yogi-b-20261105-deny-ingress']
        self.assertEqual(rejected(drop_deny), 'firewall-baseline')

        disabled = dict(public, disabled=True, name='disabled-public')
        kept = json.loads(EFFECTIVE_FIREWALLS)
        kept['firewalls'].append(disabled)
        self.assertTrue(yogi_possession.evaluate_inbound(kept)['ok'])
        egress = {'name': 'egress-web', 'direction': 'EGRESS', 'priority': 1000, 'disabled': False,
                  'destinationRanges': ['0.0.0.0/0'], 'allowed': [{'IPProtocol': 'tcp', 'ports': ['443']}],
                  'network': document['firewalls'][0]['network']}
        kept = json.loads(EFFECTIVE_FIREWALLS)
        kept['firewalls'].append(egress)
        self.assertTrue(yogi_possession.evaluate_inbound(kept)['ok'])

        class ApiDown:
            def account(self):
                return yogi_possession.OPERATOR_ACCOUNT
            def describe(self):
                return {'status': 'TERMINATED', 'id': yogi_possession.INSTANCE_ID,
                        'fingerprint': yogi_possession.POSSESSION_FINGERPRINT,
                        'items': dict(yogi_possession.POSSESSION_BASE),
                        'tags': list(yogi_possession.REVIEWED_TAGS),
                        'network': {'observed': True, 'allocationInferred': False, 'nics': [{
                            'name': 'nic0', 'stackType': 'IPV4_ONLY',
                            'network': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default',
                            'subnetwork': 'https://www.googleapis.com/compute/v1/projects/protocols-465723/regions/us-west2/subnetworks/default',
                            'accessConfigs': [{'name': 'External NAT', 'type': 'ONE_TO_ONE_NAT',
                                               'networkTier': 'PREMIUM', 'addressPresent': False}],
                            'ipv6AccessConfigs': []}]}}
            def effective_firewalls(self):
                raise yogi_possession.DescribeRefused('firewall-unreadable', returncode=1)
            def start(self):
                raise AssertionError('started')
        with self.assertRaises(yogi_possession.DescribeRefused) as down:
            yogi_possession.possession_preflight(ApiDown())
        self.assertEqual(down.exception.code, 'firewall-unreadable')

        class Untagged(ApiDown):
            def describe(self):
                view = ApiDown.describe(self)
                view['tags'] = ['roseos-yogi-builder']
                return view
            def effective_firewalls(self):
                return json.loads(EFFECTIVE_FIREWALLS)
            def start(self):
                raise AssertionError('started')
        with self.assertRaises(yogi_possession.DescribeRefused) as drift:
            yogi_possession.possession_preflight(Untagged())
        self.assertEqual(drift.exception.code, 'firewall-tag-drift')


if __name__ == '__main__':
    unittest.main()

