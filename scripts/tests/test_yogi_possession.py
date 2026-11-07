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

            def wait_refresh(self):
                self.calls.append('refresh')
                return 'ok'

            def inspect(self):
                self.calls.append('inspect')
                return {'purpose': 'inspect', 'status': 'complete', 'instanceId': yogi_possession.INSTANCE_ID,
                        'operationId': '12' * 16, 'cleanup': {
                            'possessMarkerLines': self.marker, 'authPathsRead': 2, 'accessCleanup': 'ok',
                            'possessComment': 'roseos-yogi-possess-' + operation}}

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
        self.assertEqual(good.calls[-3:], ['refresh', 'stop', 'inspect'])
        self.assertTrue(done['hostKeyHandshake'])
        self.assertFalse(yogi_possession.guest_key_removed(
            {'purpose': 'inspect', 'status': 'complete', 'instanceId': yogi_possession.INSTANCE_ID,
             'operationId': '12' * 16, 'cleanup': {'possessMarkerLines': 0}}, operation))

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
                            'possessComment': 'roseos-yogi-possess-' + operation}}

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
                self.assertEqual(timeout, yogi_possession.START_TIMEOUT)
                raise subprocess.TimeoutExpired(argv, timeout)
            if argv[1:4] == ['compute', 'instances', 'stop']:
                self.assertEqual(timeout, yogi_possession.STOP_TIMEOUT)
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
                        'fingerprint': self.fingerprint, 'items': self.items}

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


if __name__ == '__main__':
    unittest.main()

