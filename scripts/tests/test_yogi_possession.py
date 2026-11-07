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

            def wait_login_ready(self):
                self.calls.append('login-ready')
                return {'listenerReady': True, 'backendTcp22': False, 'possession': False,
                        'returncode': 0, 'stderr': '', 'delaySeconds': yogi_possession.LOGIN_READY_SECONDS}

            def ssh(self, argv, timeout):
                self.calls.append(('ssh', timeout))
                self.assert_timeout = timeout
                probe = {'runnerProcess': False, 'active': 'inactive', 'enabled': 'disabled',
                         'builder': {'instanceId': yogi_possession.INSTANCE_ID}}
                if not self.ssh_ok:
                    probe = {'runnerProcess': True, 'active': 'active', 'enabled': 'enabled',
                             'builder': {'instanceId': yogi_possession.INSTANCE_ID}}
                report = {'class': 'success', 'returncode': 0, 'stderr': '', 'argv': list(argv),
                          'attempts': 1, 'probe': probe}
                report.update(yogi_possession.ssh_evidence('success'))
                return report

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
        replayed = dict(spent, operationId='a5848d669170305750c61bc3effdec9f')
        self.assertFalse(yogi_possession.guest_key_removed(replayed, operation, material))
        fresh = dict(spent, operationId='41e30832b719be117c73c13c0459cb44')
        self.assertFalse(yogi_possession.guest_key_removed(fresh, operation, material))

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
                report = {'class': 'auth-failed', 'returncode': 255, 'stderr': 'Permission denied (publickey).',
                          'argv': list(argv), 'attempts': 1, 'probe': None}
                report.update(yogi_possession.ssh_evidence('auth-failed'))
                return report

        failed_ssh = SshFails()
        ssh_result = yogi_possession.possession_run(failed_ssh, plan, str(identity))
        self.assertFalse(ssh_result['hostKeyHandshake'] or ssh_result['guestAuthenticated'] or ssh_result['hostKeyPinned'])
        self.assertTrue(ssh_result['stopped'] and ssh_result['metadataLoginRemoved'])
        self.assertTrue(ssh_result['serverKeyAccepted'] and not ssh_result['loginAccepted'])
        self.assertEqual(ssh_result['ssh']['returncode'], 255)
        self.assertIn('Permission denied', ssh_result['ssh']['stderr'])
        self.assertEqual(failed_ssh.calls.count(('ssh', 60)), 1)
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

        def case(start='ok', stop='ok', metadata='done', ssh_code=0, ssh_stderr=b'', ssh_log='',
                 ssh_raise=None, iap=(True, 0, ''), iap_script=None, clock=None, receipt=None,
                 popen=None, use_listener=False):
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
                    self.assertIn('LogLevel=DEBUG', argv)
                    self.assertEqual(argv[1], '-E')
                    if ssh_raise == 'oserror':
                        raise OSError('ssh spawn')
                    Path(argv[2]).write_text(ssh_log)
                    Result.returncode = ssh_code
                    Result.stderr = ssh_stderr
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

            script_at = {'n': 0}

            def check():
                if iap_script is not None:
                    item = iap_script[min(script_at['n'], len(iap_script) - 1)]
                    script_at['n'] += 1
                    return item
                return iap

            if clock is None:
                sleep, now = (lambda _seconds: None), (lambda: 0)
            else:
                sleep, now = clock.sleep, clock
            driver_args = {'sleep': sleep, 'clock': now}
            if use_listener:
                driver_args['popen'] = popen
            else:
                driver_args['iap_check'] = check
            driver = yogi_possession.GcloudDriver(run=run, opener=opener,
                                                 inspect_out=str(Path(tempfile.mkdtemp()) / 'inspect'),
                                                 **driver_args)
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

        nonzero, nonzero_commands, nonzero_posts = case(ssh_code=1, ssh_stderr=b'probe exited 1\n')
        self.assertFalse(nonzero['hostKeyHandshake'] or nonzero['guestAuthenticated'] or nonzero['hostKeyPinned'])
        self.assertEqual(metadata_posts(nonzero_posts), 2)
        self.assertTrue(nonzero['stopped'])
        self.assertEqual(nonzero['ssh']['class'], 'remote-command')
        self.assertEqual(nonzero['ssh']['evidence'], 'openssh-remote-exit')
        self.assertTrue(nonzero['serverKeyAccepted'] and nonzero['loginAccepted'])
        self.assertEqual(nonzero['ssh']['returncode'], 1)
        self.assertIn('probe exited 1', nonzero['ssh']['stderr'])
        self.assertEqual(sum(argv[0] == 'ssh' for argv, _timeout in nonzero_commands), 1)
        forged_exit, _forged_exit_commands, _posts = case(
            ssh_code=1, ssh_stderr=b'Host key verification failed.\nPermission denied (publickey).\n')
        self.assertEqual(forged_exit['ssh']['class'], 'remote-command')
        self.assertTrue(forged_exit['serverKeyAccepted'] and forged_exit['loginAccepted'])
        logged_fail, _logged_commands, _posts = case(ssh_code=1, ssh_log='Host key verification failed.\n')
        self.assertEqual(logged_fail['ssh']['class'], 'host-key-mismatch')
        self.assertFalse(logged_fail['serverKeyAccepted'] or logged_fail['loginAccepted'])

        alias = 'yogi-' + yogi_possession.INSTANCE_ID
        user = yogi_possession.POSSESSION_USER
        host_ok = "debug1: Host '" + alias + "' is known and matches the ED25519 host key.\n"
        denied_line = user + '@' + alias + ': Permission denied (publickey).\n'
        exact_denial = host_ok + 'debug1: Authentications that can continue: publickey\n' + denied_line

        denied, denied_commands, _denied_posts = case(ssh_code=255, ssh_log=exact_denial,
                                                      ssh_stderr=b'ERROR: (gcloud) Permission denied\n')
        self.assertEqual(denied['ssh']['class'], 'auth-failed')
        self.assertEqual(denied['ssh']['evidence'], 'openssh-userauth-denied')
        self.assertTrue(denied['serverKeyAccepted'] and not denied['loginAccepted'])
        self.assertFalse(denied['hostKeyHandshake'] or denied['hostKeyPinned'])
        self.assertIn('auth-failed', denied['error'])
        self.assertIn(denied_line.strip(), denied['ssh']['clientLog'])
        self.assertEqual(sum(argv[0] == 'ssh' for argv, _timeout in denied_commands), 1)
        identity_arg = denied['ssh']['argv'][denied['ssh']['argv'].index('-i') + 1]
        self.assertEqual(identity_arg, 'IDENTITY_FILE')
        self.assertNotIn('PRIVATE', identity_arg)

        mismatch, mismatch_commands, _posts = case(
            ssh_code=255, ssh_log='Host key verification failed.\n',
            ssh_stderr=b'debug1: Authentication succeeded (publickey).\n')
        self.assertEqual(mismatch['ssh']['class'], 'host-key-mismatch')
        self.assertFalse(mismatch['serverKeyAccepted'] or mismatch['loginAccepted'] or mismatch['hostKeyPinned'])
        self.assertEqual(mismatch['ssh']['evidence'], 'none')
        self.assertIn('host-key-mismatch', mismatch['error'])
        self.assertEqual(sum(argv[0] == 'ssh' for argv, _timeout in mismatch_commands), 1)

        forged, _posts_commands, _posts = case(
            ssh_code=255,
            ssh_stderr=b'debug1: Authentication succeeded (publickey).\nHost key verification failed.\n'
                       b'Permission denied (publickey).\n')
        self.assertEqual(forged['ssh']['class'], 'unproven')
        self.assertFalse(forged['serverKeyAccepted'] or forged['loginAccepted'])

        empty, _empty_commands, _posts = case(ssh_code=255)
        self.assertEqual(empty['ssh']['class'], 'unproven')
        self.assertFalse(empty['serverKeyAccepted'] or empty['loginAccepted'])

        iam, _iam_commands, _posts = case(
            ssh_code=255,
            ssh_log='ERROR: (gcloud.compute.start-iap-tunnel) [PERMISSION_DENIED] Permission denied.\n',
            ssh_stderr=b'Permission denied\n')
        self.assertEqual(iam['ssh']['class'], 'unproven')
        self.assertFalse(iam['serverKeyAccepted'] or iam['loginAccepted'])

        missing_key, _missing_commands, _posts = case(
            ssh_code=255,
            ssh_log='Warning: Identity file IDENTITY_FILE not accessible: No such file or directory.\n'
                    + exact_denial)
        self.assertEqual(missing_key['ssh']['class'], 'unproven')
        self.assertFalse(missing_key['serverKeyAccepted'] or missing_key['loginAccepted'])

        generic, _generic_commands, _posts = case(
            ssh_code=255, ssh_log='Permission denied (publickey).\n')
        self.assertEqual(generic['ssh']['class'], 'unproven')
        self.assertFalse(generic['serverKeyAccepted'] or generic['loginAccepted'])

        resolve, _resolve_commands, _posts = case(
            ssh_code=255, ssh_log='ssh: Could not resolve hostname ' + alias + ': Name or service not known\n')
        self.assertEqual(resolve['ssh']['class'], 'unproven')
        self.assertFalse(resolve['serverKeyAccepted'] or resolve['loginAccepted'])

        kex, _kex_commands, _posts = case(
            ssh_code=255,
            ssh_log='Unable to negotiate with 127.0.0.1 port 22: no matching host key type found. Their offer: ssh-rsa\n')
        self.assertEqual(kex['ssh']['class'], 'unproven')
        self.assertFalse(kex['serverKeyAccepted'] or kex['loginAccepted'])

        killed, _killed_commands, _posts = case(ssh_code=-9, ssh_log=exact_denial)
        self.assertEqual(killed['ssh']['class'], 'unproven')
        self.assertFalse(killed['serverKeyAccepted'] or killed['loginAccepted'])

        remote_255, _remote_commands, _posts = case(
            ssh_code=255,
            ssh_log='debug1: Authentication succeeded (publickey).\nprobe failed inside the guest\n')
        self.assertEqual(remote_255['ssh']['class'], 'unproven')
        self.assertFalse(remote_255['serverKeyAccepted'] or remote_255['loginAccepted'])

        leaked, _leaked_commands, _posts = case(
            ssh_code=255,
            ssh_stderr=b'-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAAB3NzaC1yc2EAAAADAQAB\n'
                       b'ya29.super-secret-token\n')
        leaked_text = json.dumps(leaked)
        self.assertNotIn('b3BlbnNza', leaked_text)
        self.assertNotIn('ya29.', leaked_text)
        self.assertNotIn('BEGIN', leaked_text)
        self.assertEqual(leaked['ssh']['class'], 'unproven')

        prefix = 'debug1: note\n' * 400
        capped, _capped_commands, _posts = case(ssh_code=255, ssh_log=prefix + exact_denial)
        self.assertEqual(capped['ssh']['class'], 'auth-failed')
        self.assertTrue(capped['serverKeyAccepted'] and not capped['loginAccepted'])
        self.assertIn('debug1: note', capped['ssh']['clientLog'])
        self.assertIn('Permission denied (publickey).', capped['ssh']['clientLog'])
        self.assertIn('[omitted', capped['ssh']['clientLog'])
        self.assertLessEqual(len(capped['ssh']['clientLog']), 2048)

        unready, unready_commands, _posts = case(iap=(False, 1, 'ERROR: failed to start the tunnel ya29.tunnel-token'))
        self.assertEqual(unready['ssh']['class'], 'fatal')
        self.assertEqual(unready['ssh']['attempts'], 0)
        self.assertEqual(len(unready['loginReady']['attempts']), 1)
        self.assertFalse(unready['serverKeyAccepted'] or unready['loginAccepted'] or unready['loginReady']['backendTcp22']
                         or unready['loginReady']['possession'] or unready['loginReady']['listenerReady'])
        self.assertFalse(any(argv[0] == 'ssh' for argv, _timeout in unready_commands))
        self.assertIn('failed to start the tunnel', unready['ssh']['stderr'])
        self.assertNotIn('ya29.', json.dumps(unready))
        self.assertTrue(unready['stopped'] and unready['metadataLoginRemoved'])
        self.assertIsNotNone(unready['inbound'])
        self.assertEqual(unready['inbound']['ok'], True)
        self.assertEqual(unready['metadataAdd']['result'], 'ok')

        def spawn_fails(*_args, **_kwargs):
            raise OSError('spawn failed')

        spawned, spawned_commands, _posts = case(use_listener=True, popen=spawn_fails)
        self.assertEqual(spawned['ssh']['class'], 'spawn')
        self.assertEqual(len(spawned['loginReady']['attempts']), 1)
        self.assertFalse(any(argv[0] == 'ssh' for argv, _timeout in spawned_commands))
        self.assertTrue(spawned['stopped'] and spawned['metadataLoginRemoved'])
        self.assertIn('OSError', spawned['loginReady']['stderr'])
        self.assertFalse(spawned['loginReady']['listenerReady'] or spawned['loginReady']['backendTcp22'])

        class Clock:
            def __init__(self):
                self.t = 0.0

            def __call__(self):
                return self.t

            def sleep(self, seconds):
                self.t += seconds

        refused = ("ERROR: (gcloud.compute.start-iap-tunnel) While checking if a connection can be made: "
                   "Error while connecting [4003: 'failed to connect to backend']. "
                   "(Failed to connect to port 22)")
        healthy, healthy_commands, _posts = case(
            iap_script=[(False, 1, refused), (True, 0, 'Listening on port [127.0.0.1].')], clock=Clock())
        self.assertTrue(healthy['loginReady']['listenerReady'] and healthy['loginReady']['backendTcp22'])
        self.assertFalse(healthy['loginReady']['possession'])
        self.assertGreaterEqual(healthy['loginReady']['attempts'][0]['elapsedSeconds'],
                                yogi_possession.LOGIN_READY_SECONDS)
        self.assertEqual([item['class'] for item in healthy['loginReady']['attempts']], ['backend-4003', 'ready'])
        self.assertEqual(sum(argv[0] == 'ssh' for argv, _timeout in healthy_commands), 1)
        self.assertLess(healthy['loginReady']['elapsedSeconds'], yogi_possession.TRANSPORT_BUDGET + 5)

        exhausted, exhausted_commands, _posts = case(iap_script=[(False, 1, refused)], clock=Clock())
        self.assertEqual(exhausted['ssh']['class'], 'backend-4003')
        self.assertFalse(exhausted['loginReady']['listenerReady'] or exhausted['loginReady']['backendTcp22'])
        self.assertGreaterEqual(len(exhausted['loginReady']['attempts']), 2)
        self.assertTrue(all(item['class'] == 'backend-4003' for item in exhausted['loginReady']['attempts']))
        self.assertGreaterEqual(exhausted['loginReady']['elapsedSeconds'], yogi_possession.TRANSPORT_BUDGET)
        self.assertFalse(any(argv[0] == 'ssh' for argv, _timeout in exhausted_commands))
        self.assertTrue(exhausted['stopped'] and exhausted['metadataLoginRemoved'])

        denied_iam, iam_commands, _posts = case(
            iap_script=[(False, 1, 'ERROR: (gcloud.compute.start-iap-tunnel) [PERMISSION_DENIED]')], clock=Clock())
        self.assertEqual(denied_iam['ssh']['class'], 'iam')
        self.assertEqual(len(denied_iam['loginReady']['attempts']), 1)
        self.assertFalse(any(argv[0] == 'ssh' for argv, _timeout in iam_commands))
        self.assertLess(denied_iam['loginReady']['elapsedSeconds'], yogi_possession.TRANSPORT_BUDGET)

        crashed, crashed_commands, _posts = case(ssh_raise='oserror')
        self.assertEqual(crashed['ssh']['class'], 'unproven')
        self.assertTrue(crashed['stopped'] and crashed['metadataLoginRemoved'])
        self.assertEqual(sum(argv[0] == 'ssh' for argv, _timeout in crashed_commands), 1)

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
        self.assertEqual(done['ssh']['class'], 'success')
        self.assertEqual(done['loginReady']['guestWaitSeconds'], yogi_possession.LOGIN_READY_SECONDS)
        self.assertTrue(done['loginReady']['listenerReady'] and done['loginReady']['backendTcp22'])
        self.assertEqual(done['loginReady']['backendEvidence'], 'gcloud-iap-check')
        self.assertFalse(done['loginReady']['possession'])
        self.assertEqual(done['inbound']['ruleCount'], 9)
        self.assertTrue(done['serverKeyAccepted'] and done['loginAccepted'])
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

    def test_listener_teardown_and_ssh_evidence_are_bounded(self):
        import os
        import subprocess
        import time
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b'Listening on port\n')
        os.close(write_fd)
        stream = os.fdopen(read_fd, 'rb')
        killed = []

        class Proc:
            returncode = None
            pid = None
            stderr = stream

            def poll(self):
                return None

            def kill(self):
                killed.append(True)

            def wait(self, timeout=None):
                self.returncode = 0
                return 0

        seen = []

        def popen(argv, stdout=None, stderr=None, start_new_session=False):
            seen.append(list(argv))
            return Proc()

        attempts = {'n': 0}

        def connect(_address):
            attempts['n'] += 1
            return attempts['n'] >= 2

        clock = {'t': 0.0}

        def sleep(seconds):
            clock['t'] += seconds

        driver = yogi_possession.GcloudDriver(popen=popen, connect=connect, sleep=sleep, clock=lambda: clock['t'])
        ok, _code, err = driver._iap_tcp()
        self.assertTrue(ok and killed)
        self.assertIn('start-iap-tunnel', seen[0])
        self.assertIn('Listening', err.decode() if isinstance(err, bytes) else err)
        stream.close()
        ready = driver.wait_login_ready()
        self.assertTrue(ready['listenerReady'])
        self.assertEqual(ready['backendEvidence'], 'gcloud-iap-check')
        self.assertTrue(ready['backendTcp22'])
        self.assertFalse(ready['possession'])

        class Dead:
            returncode = 1
            pid = None
            stderr = None

            def poll(self):
                return 1

            def kill(self):
                self.returncode = 1

            def wait(self, timeout=None):
                return 1

        def dead_popen(*_args, **_kwargs):
            return Dead()

        def fail_connect(_address):
            raise AssertionError('connect after exit')

        early = yogi_possession.GcloudDriver(popen=dead_popen, connect=fail_connect, sleep=sleep,
                                            clock=lambda: clock['t'])
        before = clock['t']
        early_ok, early_code, _early_err = early._iap_tcp()
        self.assertFalse(early_ok)
        self.assertEqual(early_code, 1)
        self.assertEqual(clock['t'], before)

        def spawn(*_args, **_kwargs):
            raise OSError('spawn failed')

        failed = yogi_possession.GcloudDriver(popen=spawn, sleep=sleep, clock=lambda: clock['t']).wait_login_ready()
        self.assertFalse(failed['listenerReady'] or failed['backendTcp22'] or failed['possession'])
        self.assertIn('OSError', failed['stderr'])

        class Blocking:
            def read(self, _n):
                raise AssertionError('held pipe was read without a bound')

        class Held:
            returncode = None
            pid = None
            stderr = Blocking()

            def poll(self):
                return None

            def kill(self):
                return None

            def wait(self, timeout=None):
                return 0

        held = yogi_possession.GcloudDriver(popen=lambda *_a, **_k: Held(), connect=lambda _a: True,
                                           sleep=sleep, clock=lambda: clock['t'])
        held_ok, _held_code, held_err = held._iap_tcp()
        self.assertTrue(held_ok)
        self.assertEqual(held_err, '')

        directory = Path(tempfile.mkdtemp())
        child_file = directory / 'child'
        script = directory / 'hold.py'
        script.write_text(
            'import os, sys, time, pathlib\n'
            'pid = os.fork()\n'
            'if pid == 0:\n'
            '    time.sleep(60)\n'
            '    os._exit(0)\n'
            'pathlib.Path(sys.argv[1]).write_text(str(pid))\n'
            'time.sleep(60)\n')
        started = time.monotonic()

        def real_popen(argv, stdout=None, stderr=None, start_new_session=False):
            return subprocess.Popen([sys.executable, str(script), str(child_file)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)

        def wait_child(_address):
            for _ in range(50):
                if child_file.exists():
                    return True
                time.sleep(0.02)
            return False

        real = yogi_possession.GcloudDriver(popen=real_popen, connect=wait_child)
        real_ok, _real_code, _real_err = real._iap_tcp()
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(child_file.exists())
        grandchild = int(child_file.read_text())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail('grandchild still alive')
        self.assertTrue(real_ok)
        user = yogi_possession.POSSESSION_USER
        alias = 'yogi-' + yogi_possession.INSTANCE_ID
        denial = (user + '@' + alias + ': Permission denied (publickey).\n').encode()
        raw = (b'debug1: filler\n' * 5000) + denial
        text, truncated = yogi_possession.complete_log_text(raw, yogi_possession.SSH_LOG_BOUND)
        kind, evidence = yogi_possession.classify_ssh(255, text.splitlines(), user, alias)
        self.assertTrue(truncated)
        self.assertEqual(kind, 'unproven')
        self.assertFalse(evidence['serverKeyAccepted'] or evidence['loginAccepted'])
        host_line = ("debug1: Host '" + alias + "' is known and matches the ED25519 host key.\n").encode()
        wide = host_line + (b'debug1: filler\n' * 5000) + denial
        text, truncated = yogi_possession.complete_log_text(wide, yogi_possession.SSH_LOG_BOUND)
        kind, evidence = yogi_possession.classify_ssh(255, text.splitlines(), user, alias)
        self.assertTrue(truncated)
        self.assertEqual(kind, 'auth-failed')
        self.assertTrue(evidence['serverKeyAccepted'] and not evidence['loginAccepted'])
        kind, evidence = yogi_possession.classify_ssh(
            1, ['Warning: Identity file IDENTITY_FILE not accessible: No such file or directory.'], user, alias)
        self.assertEqual(kind, 'unproven')
        self.assertFalse(evidence['serverKeyAccepted'] or evidence['loginAccepted'])
        kind, evidence = yogi_possession.classify_ssh(0, [], user, alias)
        self.assertEqual((kind, evidence['evidence']), ('success', 'openssh-session'))

        def expire(argv, capture_output=True, timeout=0):
            raise subprocess.TimeoutExpired(argv, timeout, stderr=b'banner exchange ya29.timeout-token\n')

        report = yogi_possession.GcloudDriver(run=expire).ssh(['ssh', '-i', '/tmp/id_ed25519'], 60)
        self.assertEqual(report['class'], 'timeout')
        self.assertIsNone(report['returncode'])
        self.assertFalse(report['serverKeyAccepted'] or report['loginAccepted'])
        self.assertIn('banner exchange', report['stderr'])
        self.assertNotIn('ya29.', report['stderr'])
        self.assertIn('/tmp/id_ed25519', report['argv'])
        self.assertIn('LogLevel=DEBUG', report['argv'])
        with self.assertRaises(yogi_possession.Refused):
            yogi_possession.GcloudDriver(run=expire).ssh(['ssh'], 10)

        alias = 'yogi-' + yogi_possession.INSTANCE_ID
        user = yogi_possession.POSSESSION_USER
        denial = user + '@' + alias + ': Permission denied (publickey).\n'
        host_line = "debug1: Host '" + alias + "' is known and matches the ED25519 host key.\n"

        def write_then(exc_factory):
            def run(argv, capture_output=True, timeout=0):
                log = argv[argv.index('-E') + 1]
                Path(log).write_text('debug1: Connecting to example.\n' + host_line + denial)
                raise exc_factory(argv, timeout)
            return run

        timed = yogi_possession.GcloudDriver(run=write_then(
            lambda argv, timeout: subprocess.TimeoutExpired(argv, timeout, stderr=b'hung ya29.timeout-token\n'))).ssh(
                ['ssh', '-i', '/tmp/id_ed25519', '-l', user, '-o', 'HostKeyAlias=' + alias, alias], 60)
        self.assertEqual(timed['class'], 'timeout')
        self.assertEqual(timed['evidence'], 'none')
        self.assertFalse(timed['serverKeyAccepted'] or timed['loginAccepted'])
        self.assertIn('Connecting to example.', timed['clientLog'])
        self.assertIn('Permission denied (publickey).', timed['clientLog'])
        self.assertNotIn('ya29.', timed['stderr'] + timed['clientLog'])
        self.assertFalse(Path(timed['argv'][timed['argv'].index('-E') + 1]).exists())

        broken = yogi_possession.GcloudDriver(run=write_then(
            lambda argv, timeout: OSError('ssh spawn'))).ssh(
                ['ssh', '-i', '/tmp/id_ed25519', '-l', user, '-o', 'HostKeyAlias=' + alias, alias], 60)
        self.assertEqual(broken['class'], 'unproven')
        self.assertIn('Connecting to example.', broken['clientLog'])
        self.assertFalse(broken['serverKeyAccepted'] or broken['loginAccepted'])

    def test_openssh_9_6_debug_log_matches_a_real_sshd(self):
        import os
        import socket
        import subprocess
        import time
        sshd = '/usr/sbin/sshd'
        self.assertTrue(os.path.isfile(sshd), 'openssh-server is required for the localhost fixture')
        base = Path(tempfile.mkdtemp())
        host = base / 'host_ed25519'
        good = base / 'client_ok'
        bad = base / 'client_bad'
        subprocess.check_call(['ssh-keygen', '-q', '-t', 'ed25519', '-f', str(host), '-N', '', '-C', 'host'])
        subprocess.check_call(['ssh-keygen', '-q', '-t', 'ed25519', '-f', str(good), '-N', '', '-C', 'ok'])
        subprocess.check_call(['ssh-keygen', '-q', '-t', 'ed25519', '-f', str(bad), '-N', '', '-C', 'bad'])
        os.chmod(base, 0o700)
        for path in (host, good, bad):
            os.chmod(path, 0o600)
        auth = base / 'authkeys'
        auth.write_text((base / 'client_ok.pub').read_text())
        os.chmod(auth, 0o600)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        user = subprocess.check_output(['id', '-un'], text=True).strip()
        alias = 'yogi-localtest'
        host_pub = (base / 'host_ed25519.pub').read_text().split()
        known = base / 'known'
        known.write_text(alias + ' ' + host_pub[0] + ' ' + host_pub[1] + '\n')
        wrong = base / 'known_wrong'
        wrong.write_text(alias + ' ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIN2pFrUsLlgUbqItVqVwLLqnC+cpPPv29xXhHWYvqGCI\n')
        config = base / 'sshd_config'
        config.write_text(
            f'Port {port}\nListenAddress 127.0.0.1\nHostKey {host}\nPidFile {base / "sshd.pid"}\n'
            f'AuthorizedKeysFile {auth}\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n'
            'ChallengeResponseAuthentication no\nPubkeyAuthentication yes\nUsePAM no\n'
            'PermitRootLogin no\nStrictModes no\nMaxAuthTries 6\n')
        proxy = base / 'proxy.py'
        proxy.write_text(
            'import os, select, socket, sys\n'
            's = socket.create_connection(("127.0.0.1", int(sys.argv[1])))\n'
            'while True:\n'
            '    ready, _, _ = select.select([0, s], [], [], 30)\n'
            '    if not ready:\n'
            '        break\n'
            '    if 0 in ready:\n'
            '        data = os.read(0, 65536)\n'
            '        if not data:\n'
            '            break\n'
            '        s.sendall(data)\n'
            '    if s in ready:\n'
            '        data = s.recv(65536)\n'
            '        if not data:\n'
            '            break\n'
            '        os.write(1, data)\n')
        log = base / 'sshd.log'
        proc = subprocess.Popen([sshd, '-f', str(config), '-E', str(log), '-D'])
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                        break
                except OSError:
                    if proc.poll() is not None:
                        self.fail(log.read_text(errors='replace')[-500:])
                    time.sleep(0.05)
            else:
                self.fail('sshd did not accept')

            def argv(identity, known_file, command):
                return [
                    'ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'RequestTTY=no',
                    '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
                    '-o', 'PreferredAuthentications=publickey', '-o', 'NumberOfPasswordPrompts=0',
                    '-o', 'PubkeyAuthentication=yes', '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none',
                    '-i', str(identity), '-o', 'StrictHostKeyChecking=yes',
                    '-o', 'UserKnownHostsFile=' + str(known_file), '-o', 'GlobalKnownHostsFile=/dev/null',
                    '-o', 'HostKeyAlias=' + alias, '-o', 'HostKeyAlgorithms=ssh-ed25519',
                    '-o', 'CheckHostIP=no', '-o', 'UpdateHostKeys=no', '-o', 'ForwardAgent=no',
                    '-o', 'ForwardX11=no', '-o', 'ClearAllForwardings=yes', '-o', 'PermitLocalCommand=no',
                    '-o', 'ConnectTimeout=5', '-l', user,
                    '-o', 'ProxyCommand=python3 ' + str(proxy) + ' ' + str(port),
                    alias, '--', command]

            denied = yogi_possession.GcloudDriver().ssh(argv(bad, known, 'echo no'), 60)
            self.assertEqual(denied['returncode'], 255)
            self.assertEqual(denied['class'], 'auth-failed')
            self.assertEqual(denied['evidence'], 'openssh-userauth-denied')
            self.assertTrue(denied['serverKeyAccepted'] and not denied['loginAccepted'])
            self.assertIn(user + '@' + alias + ': Permission denied (publickey).', denied['clientLog'])
            self.assertIn('debug1:', denied['clientLog'])
            self.assertIn('[omitted', denied['clientLog'])
            self.assertIn('LogLevel=DEBUG', denied['argv'])
            self.assertLessEqual(len(denied['clientLog']), 2048)
            self.assertFalse(Path(denied['argv'][denied['argv'].index('-E') + 1]).exists())

            verbose = base / 'verbose.log'
            completed = subprocess.run(
                ['ssh', '-F', '/dev/null', '-E', str(verbose), '-o', 'LogLevel=VERBOSE', *argv(bad, known, 'echo no')[1:]],
                capture_output=True, timeout=20)
            verbose_text = verbose.read_text(errors='replace')
            self.assertEqual(completed.returncode, 255)
            self.assertNotIn('debug1:', verbose_text)
            self.assertIn(user + '@' + alias + ': Permission denied (publickey).', verbose_text)
            kind, evidence = yogi_possession.classify_ssh(255, verbose_text.splitlines(), user, alias)
            self.assertEqual(kind, 'unproven')
            self.assertFalse(evidence['serverKeyAccepted'] or evidence['loginAccepted'])

            mismatch = yogi_possession.GcloudDriver().ssh(argv(good, wrong, 'echo no'), 60)
            self.assertEqual(mismatch['class'], 'host-key-mismatch')
            self.assertIn('Host key verification failed.', mismatch['clientLog'])
            self.assertFalse(mismatch['serverKeyAccepted'] or mismatch['loginAccepted'])

            missing = yogi_possession.GcloudDriver().ssh(argv(base / 'missing', known, 'echo no'), 60)
            self.assertEqual(missing['class'], 'unproven')
            self.assertIn('not accessible', missing['stderr'])
            self.assertFalse(missing['serverKeyAccepted'] or missing['loginAccepted'])

            remote = yogi_possession.GcloudDriver().ssh(argv(good, known, 'exit 7'), 60)
            self.assertEqual(remote['class'], 'remote-command')
            self.assertEqual(remote['returncode'], 7)
            self.assertTrue(remote['serverKeyAccepted'] and remote['loginAccepted'])
            self.assertIn('Authenticated to ' + alias + ' (via proxy) using "publickey".', remote['clientLog'])

            collided = yogi_possession.GcloudDriver().ssh(argv(good, known, 'exit 255'), 60)
            self.assertEqual(collided['returncode'], 255)
            self.assertEqual(collided['class'], 'unproven')
            self.assertIn('Exit status 255', collided['clientLog'])
            self.assertFalse(collided['serverKeyAccepted'] or collided['loginAccepted'])

            proved = yogi_possession.GcloudDriver().ssh(argv(good, known, 'echo {}'), 60)
            self.assertEqual(proved['returncode'], 0)
            self.assertEqual(proved['class'], 'success')
            self.assertEqual(proved['evidence'], 'openssh-session')
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)


if __name__ == '__main__':
    unittest.main()

