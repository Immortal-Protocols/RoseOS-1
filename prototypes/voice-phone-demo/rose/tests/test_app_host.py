"""The standalone app-builder host (docs/APP-BUILDER.md §6) with fakes: config validation, the builder-only signed
listener and loopback pairing admin, no-writer behaviour, and the key backup that must succeed before a project's
first delivery. No socket is bound, no key leaves the test, nothing calls a model."""
import base64
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from rose.app_builder import host, toolchain
from rose.tests.test_app_builder import HAS_SDK, JAVA_HOME, SDK, fake_backup
from rose.tests.test_builder_routes import Device, label


def config_data(tmp, **extra):
    return {'schemaVersion': 1, 'root': f'{tmp}/builder', 'remoteDir': f'{tmp}/remote', 'name': 'Rose app builder',
            'port': 8766, 'adminPort': 8767, 'hosts': ['100.64.0.9'], 'sdk': str(SDK), **extra}


class Config(unittest.TestCase):
    def test_a_minimal_config_is_safe_by_default(self):
        c = host.parse_config(config_data('/srv/x'))
        self.assertEqual((c.key_creation, c.writer, c.backup, c.build_timeout_s), (False, None, None, 600.0))
        self.assertEqual((c.port, c.admin_port, c.hosts), (8766, 8767, ('100.64.0.9',)))

    def test_refusals(self):
        good = config_data('/srv/x')
        bad = [None, [], {**good, 'schemaVersion': 2}, {**good, 'extra': 1},
               {k: v for k, v in good.items() if k != 'sdk'}, {**good, 'root': 'relative'},
               {**good, 'remoteDir': good['root']}, {**good, 'remoteDir': good['root'] + '/remote'},
               {**good, 'port': 80}, {**good, 'adminPort': good['port']}, {**good, 'port': True},
               {**good, 'hosts': []}, {**good, 'hosts': ['a b']}, {**good, 'name': ''},
               {**good, 'keyCreation': 'yes'}, {**good, 'buildTimeoutS': 0},
               {**good, 'writer': {'argv': ['node', 'bridge.mjs']}}, {**good, 'writer': {'argv': []}},
               {**good, 'backup': {'argv': ['/bin/backup'], 'extra': 1}}, {**good, 'backup': ['/bin/backup']}]
        for data in bad:
            with self.assertRaises(host.ConfigError, msg=data):
                host.parse_config(data)

    def test_load_config_and_main_refuse_a_bad_file_without_serving(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'host.json'
            path.write_text('{not json')
            with self.assertRaises(host.ConfigError):
                host.load_config(path)
            self.assertEqual(host.main(['--config', str(path)]), 2)
            path.write_text(json.dumps(config_data(tmp, writer={'argv': ['/usr/bin/node', 'b.mjs'], 'timeoutS': 60})))
            c = host.load_config(path)
            self.assertEqual((c.writer.argv, c.writer.timeout_s), (('/usr/bin/node', 'b.mjs'), 60.0))


class Wired(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = host.parse_config(config_data(self.tmp.name))
        self.host = host.build_host(self.config, generate=label, backup=fake_backup)
        self.addCleanup(self.host.builder.ledger.close)
        self.host.remote.listening = True  # main() sets this once the TLS socket is bound
        self.remote = TestClient(self.host.remote_app)
        self.admin = TestClient(self.host.admin_app, base_url='http://127.0.0.1:8767')

    def tearDown(self):
        self.tmp.cleanup()

    def pair(self):
        invite = self.admin.post('/api/pair/new').json()
        self.assertIn('100.64.0.9', invite['hosts'])
        code = invite['uri'].split('code=')[1].split('&')[0]
        device = Device()
        r = self.remote.post('/pair', json={'code': code, 'publicKey': base64.b64encode(device.spki).decode(),
                                            'name': 'Rose App Builder on Pixel', 'model': 'Pixel'})
        self.assertEqual(r.status_code, 200, r.text)
        return device

    def signed(self, device, method, path, json=None):
        h = device.headers(method, path, self.host.remote.identity.pin)
        return self.remote.request(method, path, json=json, headers=h)

    def test_directories_are_private(self):
        for d in (self.config.root, self.config.remote_dir):
            self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)

    def test_pairing_is_loopback_only_and_the_listener_serves_only_the_builder(self):
        refused = TestClient(self.host.admin_app, base_url='http://100.64.0.9:8767').post('/api/pair/new')
        self.assertEqual(refused.status_code, 403)
        device = self.pair()
        hello = self.signed(device, 'GET', '/hello').json()
        self.assertEqual((hello['serverName'], hello['phone']), ('Rose app builder', 'none'))
        for path in ('/ws/audio', '/ws/events', '/ws/phone'):
            with self.assertRaises(WebSocketDisconnect):
                with self.remote.websocket_connect(path, headers=device.headers('GET', path,
                                                                               self.host.remote.identity.pin)):
                    pass
        self.assertEqual(self.remote.get('/builder/v1/projects').status_code, 401)  # unsigned
        r = self.signed(device, 'POST', '/builder/v1/projects', {'schemaVersion': 1, 'clientRequestId': 'a',
                                                                 'description': 'a stopwatch'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.signed(device, 'GET', '/builder/v1/projects').json()['projects']), 1)

    def test_no_writer_fails_jobs_honestly(self):
        bare = host.build_host(host.parse_config(config_data(self.tmp.name + '/bare')))
        self.addCleanup(bare.builder.ledger.close)
        out = bare.builder.create('d', {'schemaVersion': 1, 'clientRequestId': 'a', 'description': 'a stopwatch'})
        self.assertTrue(bare.builder.run_next())
        job = bare.builder.job(out['jobId'])
        self.assertEqual((job['state'], job['failure']['summary']), ('failed', host.NO_WRITER))
        self.assertIsNone(bare.builder.backup)  # nothing configured means nothing will be delivered
        self.assertFalse(bare.builder.tools.key_creation)

    def test_worker_starts_and_stops(self):
        self.host.start_worker()
        self.assertTrue(self.host.worker.is_alive())
        self.host.stop(timeout_s=10)
        self.assertFalse(self.host.worker.is_alive())


def script(tmp, body):
    path = Path(tmp) / 'backup.sh'
    path.write_text('#!/bin/sh\n' + body)
    path.chmod(0o700)
    return path


class Backup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.keys = Path(self.tmp.name) / 'keys'
        self.keys.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_backup(self, body, timeout_s=10.0):
        return host.CommandBackup(host.Command((str(script(self.tmp.name, body)),), timeout_s))(
            self.keys, 'p1', 'app.rose.gen.pp1')

    def test_receipt_and_arguments(self):
        receipt = self.run_backup('echo "stored:$2:$3:$(basename "$1"):$HOME"\necho second line\n')
        self.assertEqual(receipt, f'stored:p1:app.rose.gen.pp1:keys:{self.keys}')

    def test_failures_raise_signing_without_echoing_output(self):
        for body, timeout in (('echo secret-material >&2\nexit 3\n', 10.0), ('exit 0\n', 10.0),
                              ('sleep 5\necho late\n', 0.5)):
            with self.assertRaises(toolchain.StepFailed) as cm:
                self.run_backup(body, timeout)
            self.assertEqual(cm.exception.category, 'signing')
            self.assertNotIn('secret', cm.exception.summary + cm.exception.log)


@unittest.skipUnless(HAS_SDK or os.environ.get('ROSE_REQUIRE_APP_BUILD'), 'needs the pinned Android SDK and a JDK')
class BackupBeforeFirstDelivery(unittest.TestCase):
    """Compilation and signing with throwaway keys; nothing is installed and no key leaves the test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def builder(self, backup):
        from rose.app_builder.service import Builder
        tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=300, key_creation=True)
        b = Builder(Path(self.tmp.name) / 'b', tools, label, backup=backup)
        self.addCleanup(b.ledger.close)
        return b

    def test_no_backup_configured_delivers_nothing(self):
        b = self.builder(None)
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'a', 'description': 'a stopwatch'})
        b.run_next()
        job = b.job(out['jobId'])
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'signing'), job)
        project = b.projects()[0]
        self.assertEqual((project['versions'], project['signerSha256']), ([], None))

    def test_a_failed_backup_blocks_delivery_until_it_succeeds_once(self):
        state = {'ok': False}

        def backup(keys, project_id, package):
            self.calls.append(project_id)
            if not state['ok']:
                raise OSError('bucket unreachable')
            return 'receipt-1'
        b = self.builder(backup)
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'a', 'description': 'a stopwatch'})
        b.run_next()
        job = b.job(out['jobId'])
        self.assertEqual((job['state'], job['failure']['category'], job['failure']['retryable']),
                         ('failed', 'signing', True), job)
        self.assertEqual((b.projects()[0]['versions'], b.projects()[0]['signerSha256']), ([], None))
        state['ok'] = True
        b.retry(out['jobId'], {'schemaVersion': 1})
        b.run_next()
        self.assertEqual(b.job(out['jobId'])['state'], 'ready')
        self.assertEqual(b.ledger.key_backup(out['projectId']), 'receipt-1')
        rev = b.revise('d', out['projectId'], {'schemaVersion': 1, 'clientRequestId': 'b', 'request': 'add laps'})
        b.run_next()
        self.assertEqual(b.job(rev['jobId'])['state'], 'ready')
        self.assertEqual(len(self.calls), 2)  # one failure, one success; the update needs no new backup

    def test_a_bad_receipt_is_a_failed_backup(self):
        for receipt in ('', None, 'x' * 201, 'line\nbreak'):
            with self.subTest(receipt=receipt):
                b = self.builder(lambda *a, r=receipt: r)
                out = b.create('d', {'schemaVersion': 1, 'clientRequestId': f'a{len(str(receipt))}',
                                     'description': 'a stopwatch'})
                b.run_next()
                self.assertEqual(b.job(out['jobId'])['failure']['category'], 'signing')
                self.assertIsNone(b.ledger.key_backup(out['projectId']))


if __name__ == '__main__':
    unittest.main()
