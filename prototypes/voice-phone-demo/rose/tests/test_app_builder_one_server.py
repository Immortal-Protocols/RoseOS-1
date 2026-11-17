"""The app builder on the one Rose server (docs/APP-BUILDER.md §6): ``rose.app --app-builder CONFIG`` serves
builder API v1 on the same signed listener and pairing the Agent already uses, from the builder config the
standalone host's builder fields share. No socket, model, writer or phone: the real Server in --ui-only mode,
FastAPI's test client against build_remote_app, and a fake writer and key backup."""
import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from rose.app_builder import host
from rose.remote import Remote, build_remote_app
from rose.tests.test_app_builder import SDK, fake_backup
from rose.tests.test_app_host import config_data
from rose.tests.test_builder_routes import Device, label

REQUEST = {'schemaVersion': 1, 'clientRequestId': 'a', 'description': 'a stopwatch'}


def builder_data(tmp, **extra):
    return {'schemaVersion': 1, 'root': f'{tmp}/builder', 'sdk': str(SDK), **extra}


class BuilderConfig(unittest.TestCase):
    def test_a_minimal_builder_config_is_safe_by_default(self):
        c = host.parse_builder_config(builder_data('/srv/x'))
        self.assertEqual((c.root, c.sdk), (Path('/srv/x/builder'), SDK))
        self.assertEqual((c.key_creation, c.writer, c.backup, c.build_timeout_s, c.library_store, c.client_server),
                         (False, None, None, 600.0, None, None))

    def test_the_listener_fields_belong_to_the_server_not_the_builder(self):
        for field, value in (('port', 8766), ('adminPort', 8767), ('remoteDir', '/srv/r'), ('hosts', ['100.64.0.9']),
                             ('name', 'Rose app builder')):
            with self.assertRaisesRegex(host.ConfigError, 'unknown field ' + field):
                host.parse_builder_config(builder_data('/srv/x', **{field: value}))

    def test_the_same_rules_as_the_standalone_host(self):
        good = builder_data('/srv/x')
        bad = [None, [], {**good, 'schemaVersion': 2}, {**good, 'extra': 1}, {'schemaVersion': 1, 'root': '/srv/x'},
               {**good, 'root': 'relative'}, {**good, 'keyCreation': 'yes'}, {**good, 'buildTimeoutS': 0},
               {**good, 'writer': {'argv': ['node', 'bridge.mjs']}}, {**good, 'backup': ['/bin/backup']},
               {**good, 'libraryStore': 'relative'}, {**good, 'clientServer': {}}]
        for data in bad:
            with self.assertRaises(host.ConfigError, msg=data):
                host.parse_builder_config(data)

    def test_a_standalone_host_config_reads_the_same_builder(self):
        """Moving to the one server keeps the same ledger root and settings: no project, version or key moves."""
        extra = {'keyCreation': True, 'buildTimeoutS': 300, 'writer': {'argv': ['/usr/bin/node', 'b.mjs']}}
        hosted = host.parse_config(config_data('/srv/x', **extra))
        alone = host.parse_builder_config(builder_data('/srv/x', **extra))
        for name in ('root', 'sdk', 'java_home', 'build_timeout_s', 'key_creation', 'writer', 'backup',
                     'library_store', 'client_server'):
            self.assertEqual(getattr(hosted, name), getattr(alone, name), name)


class BuildBuilder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_the_root_is_private_and_never_the_servers_configuration(self):
        config = host.parse_builder_config(builder_data(self.tmp.name))
        b = host.build_builder(config, generate=label, backup=fake_backup, apart_from=(self.root / 'config',))
        self.addCleanup(b.ledger.close)
        self.assertEqual(stat.S_IMODE(os.stat(config.root).st_mode), 0o700)
        for root in (self.root / 'config', self.root / 'config/builder', self.root):
            with self.assertRaisesRegex(host.ConfigError, 'separate'):
                host.build_builder(host.parse_builder_config({**builder_data('/'), 'root': str(root)}),
                                   apart_from=(self.root / 'config',))
        self.assertFalse((self.root / 'config').exists())  # refused before anything was created

    def test_a_symlinked_root_is_refused(self):
        (self.root / 'real').mkdir()
        (self.root / 'link').symlink_to(self.root / 'real')
        with self.assertRaisesRegex(host.ConfigError, 'symbolic link'):
            host.build_builder(host.parse_builder_config({**builder_data('/'), 'root': str(self.root / 'link')}))


class OneServer(unittest.TestCase):
    """The real rose.app Server and argument parser, with ROSE_CONFIG_DIR in a temporary directory."""

    def setUp(self):
        from rose import app
        self.app = app
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.enterContext(mock.patch.dict(os.environ, {'ROSE_CONFIG_DIR': str(self.root / 'config')}))
        self.config = self.root / 'builder.json'
        self.config.write_text(json.dumps(builder_data(self.tmp.name)))

    def parse(self, *argv):
        return self.app.parse_args(['--ui-only', *argv])

    def refused(self, *argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            self.parse(*argv)
        self.assertEqual(cm.exception.code, 2)
        return err.getvalue()

    def make_server(self, *argv):
        server = self.app.Server(self.parse('--app-builder', str(self.config), *argv))
        self.addCleanup(server.shutdown)
        return server

    def client(self, server):
        (self.root / 'remote').mkdir(mode=0o700, exist_ok=True)
        self.remote = Remote(directory=self.root / 'remote', name='Test Mac', hosts=lambda: [])
        self.device = Device()
        self.remote.devices.enroll(self.device.spki, 'Rose Agent', 'Pixel', '100.64.0.2')
        return TestClient(build_remote_app(server, self.remote))

    def signed(self, client, method, path, body=None):
        return client.request(method, path, json=body, headers=self.device.headers(method, path,
                                                                                    self.remote.identity.pin))

    def test_off_by_default(self):
        args = self.parse()
        self.assertIsNone(args.app_builder_config)
        server = self.app.Server(args)
        self.addCleanup(server.shutdown)
        self.assertIsNone(server.app_builder)
        self.assertEqual(self.signed(self.client(server), 'GET', '/builder/v1/projects').status_code, 404)
        self.assertFalse((self.root / 'builder').exists())

    def test_a_bad_or_unusable_config_is_refused_before_anything_is_served(self):
        self.assertIn('could not be read', self.refused('--app-builder', str(self.root / 'missing.json')))
        self.config.write_text(json.dumps({**builder_data(self.tmp.name), 'port': 8766}))
        self.assertIn('unknown field port', self.refused('--app-builder', str(self.config)))
        self.config.write_text(json.dumps(builder_data(self.tmp.name)))
        self.assertIn('--no-remote', self.refused('--app-builder', str(self.config), '--no-remote'))
        self.config.write_text(json.dumps({**builder_data('/'), 'root': str(self.root / 'config/builder')}))
        with self.assertRaisesRegex(host.ConfigError, 'separate'):
            self.app.Server(self.parse('--app-builder', str(self.config)))

    def test_main_exits_2_on_a_builder_it_cannot_construct(self):
        self.config.write_text(json.dumps({**builder_data('/'), 'root': str(self.root / 'config/builder')}))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            self.app.main(['--ui-only', '--app-builder', str(self.config)])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn('app builder refused', out.getvalue())

    def test_the_agents_pairing_reaches_builder_api_v1_beside_voice(self):
        server = self.make_server()
        self.assertIsNotNone(server.app_builder)
        client = self.client(server)
        paths = {getattr(r, 'path', '') for r in client.app.routes}
        self.assertTrue({'/hello', '/ws/audio', '/ws/events', '/ws/phone', '/builder/v1/projects'} <= paths)
        self.assertEqual(client.get('/builder/v1/projects').status_code, 401)  # unsigned
        r = self.signed(client, 'POST', '/builder/v1/projects', REQUEST)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self.signed(client, 'GET', '/builder/v1/projects').json()['projects']), 1)
        self.assertEqual(self.signed(client, 'GET', '/hello').status_code, 200)  # the same device, the same key

    def test_the_worker_runs_with_the_server_and_stops_with_it(self):
        server = self.make_server()
        server.start(mock.MagicMock())
        worker = server._builder_worker
        self.assertTrue(worker.is_alive())
        server.shutdown()
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertTrue(server.app_builder.stopping.is_set())

    def test_the_standalone_hosts_ledger_root_is_served_unchanged(self):
        """A root first used by the standalone host keeps its projects when the one server serves it."""
        standalone = host.build_host(host.parse_config(config_data(self.tmp.name)), generate=label,
                                     backup=fake_backup)
        made = standalone.builder.create('d', REQUEST)
        standalone.builder.ledger.close()
        server = self.make_server()
        self.assertIsNotNone(server.app_builder.job(made['jobId']))
        projects = self.signed(self.client(server), 'GET', '/builder/v1/projects').json()['projects']
        self.assertEqual([p['projectId'] for p in projects], [made['projectId']])


if __name__ == '__main__':
    unittest.main()
