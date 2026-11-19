"""Legacy migration on scratch ledgers, source, and a local process stand-in. No owner infrastructure."""
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rose.app_builder import app_host, appprofile, codegen, host, legacy_backend, ledger as L, server_manifest, service, template, toolchain
from rose.tests import test_app_agent_backend as agent_tests
from rose.tests.test_app_builder import HAS_SDK, SDK, JAVA_HOME
from rose.tests.test_app_host_agent import AGENT_HEX, APP_URL, LocalAgent, free_port
from rose.tests.test_app_server_manifest import tiny_elf

SQL = 'CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT);\n'
LOCK = '# retained Cargo.lock\nversion = 4\n'
CONTRACT = {'schemaVersion': 1, 'api': 1, 'types': {'Note': {'fields': {'id': 'int', 'text': 'string'}}},
            'routes': {'listNotes': {'method': 'GET', 'path': '/app/notes', 'response': 'Note'}}}
MIGRATION = {'scope': 'account', 'number': 1, 'name': 'notes', 'sha256': hashlib.sha256(SQL.encode()).hexdigest()}


def seed(ledger, source, project, job):
    """An installed legacy version, represented by public metadata and synthetic source/artifacts only."""
    source.mkdir(parents=True, exist_ok=True)
    package = ledger.project(project)['packageName']
    template.write_skeleton(source, package, 'Notes')
    p = source / 'AndroidManifest.xml'
    p.write_text(p.read_text().replace('<application', '<uses-permission android:name="android.permission.INTERNET"/><application'))
    (source / 'rose-app.json').write_text(json.dumps({'schemaVersion': 1, 'profile': L.CLIENT_SERVER}))
    (source / 'contract').mkdir()
    (source / 'contract/api.json').write_text(json.dumps(CONTRACT))
    path = source / 'server/migrations/account/0001_notes.sql'
    path.parent.mkdir(parents=True)
    path.write_text(SQL)
    path = source / 'server/src/handlers/mod.rs'
    path.parent.mkdir(parents=True)
    path.write_text('pub struct App;\n')
    service._git(source, 'init', '-q')
    service._git(source, 'add', '-A')
    service._git(source, 'commit', '-qm', 'delivered legacy source')
    revision = service._git(source, 'rev-parse', 'HEAD')
    elf = tiny_elf()
    record = {'sha256': hashlib.sha256(elf).hexdigest(), 'size': len(elf), 'target': server_manifest.TARGET}
    artifacts = ledger.root / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    (artifacts / (record['sha256'] + '.server')).write_bytes(elf)
    ledger.advance(job, 'building', 'building', 'fixture')
    ledger.allocate_version(job)
    ledger.bind_signer(project, 'a' * 64)
    manifest = server_manifest.encode(server_manifest.manifest(project, 1, record, [MIGRATION]))
    ledger.deliver(job, '1', revision, 'b' * 64, 100, 'legacy fixture', profile=L.CLIENT_SERVER, server=record,
                   migrations=[MIGRATION], cargo_lock=LOCK, contract=json.dumps(CONTRACT), manifest=manifest)
    return record


class Migration(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'builder'
        self.ledger = L.Ledger(self.root)
        self.addCleanup(self.ledger.close)
        out = self.ledger.create('d', 'r1', 'notes', 'Notes')
        self.project = out['projectId']
        self.source = self.root / 'projects' / self.project / 'source'
        self.server = seed(self.ledger, self.source, self.project, out['jobId'])

    def bind(self):
        self.ledger.record_agent_container(self.project, AGENT_HEX, APP_URL)

    def test_read_only_inventory_preserves_records_and_reports_scope(self):
        reader = L.Ledger.reader(self.root)
        self.addCleanup(reader.close)
        result = legacy_backend.inventory(reader)
        self.assertIn('live data not observed', result['scope'])
        self.assertEqual(result['projects'][0]['server'], self.server)
        self.assertEqual(result['projects'][0]['sourceRevision'], self.ledger.versions(self.project)[0]['sourceRevision'])
        with self.assertRaises(Exception):
            reader.db.execute('DELETE FROM projects')
        missing = self.root / 'missing'
        with mock.patch('sys.stdout', new_callable=io.StringIO) as stdout:
            self.assertEqual(legacy_backend.main(['--ledger', str(missing)]), 4)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'unknown')
        self.assertFalse(missing.exists())

    def test_unreadable_or_old_schema_inventory_is_unknown_not_empty(self):
        import sqlite3
        folder = self.root / 'old-schema'
        folder.mkdir()
        sqlite3.connect(folder / 'builder.sqlite3').close()
        with mock.patch('sys.stdout', new_callable=io.StringIO) as stdout:
            self.assertEqual(legacy_backend.main(['--ledger', str(folder)]), 6)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'unknown')

    def test_no_native_host_or_unbound_app_is_silently_relocated(self):
        before = (self.source / 'rose-app.json').read_bytes()
        with self.assertRaisesRegex(toolchain.StepFailed, 'preserve its data and endpoint'):
            legacy_backend.prepare(self.source, self.ledger, self.project)
        self.assertEqual((self.source / 'rose-app.json').read_bytes(), before)
        self.assertFalse((self.source / 'backend').exists())

    def test_an_agent_record_cannot_silently_replace_a_native_client_endpoint(self):
        self.ledger.record_native_host(self.project, '11111111-1111-4111-8111-111111111111',
                                       'https://old.example.org')
        self.bind()
        with self.assertRaisesRegex(toolchain.StepFailed, 'different native-host and agent endpoints'):
            legacy_backend.prepare(self.source, self.ledger, self.project)
        self.assertEqual(appprofile.read(self.source).name, L.CLIENT_SERVER)

    def test_source_migration_keeps_history_and_is_checked_against_ledger(self):
        self.bind()
        before = self.ledger.versions(self.project)
        legacy_backend.prepare(self.source, self.ledger, self.project)
        self.assertEqual(appprofile.check(self.source, self.ledger.project(self.project)['packageName'], L.CLIENT_SERVER).name,
                         L.CLIENT_BACKEND)
        legacy_backend.check(self.source, self.ledger, self.project)
        self.assertEqual(self.ledger.versions(self.project), before)
        self.assertEqual(self.ledger.profile(self.project), L.CLIENT_SERVER)
        sql = self.source / 'backend/legacy/server/migrations/account/0001_notes.sql'
        self.assertEqual(sql.read_text(), SQL)
        sql.write_text('DROP TABLE notes;')
        with self.assertRaises(appprofile.ProfileRefused):
            legacy_backend.check(self.source, self.ledger, self.project)
        sql.write_text(SQL)
        (self.source / 'backend/legacy/Cargo.lock').write_text('changed')
        with self.assertRaises(appprofile.ProfileRefused):
            legacy_backend.check(self.source, self.ledger, self.project)
        self.assertEqual(self.ledger.cargo_lock(self.project), LOCK)
        self.assertTrue((self.root / 'artifacts' / (self.server['sha256'] + '.server')).exists())

    def test_profile_changes_only_in_same_delivery_transaction(self):
        self.bind()
        job = self.ledger.revise('d', self.project, 'r2', 'change')['jobId']
        self.ledger.advance(job, 'building', 'building', 'fixture')
        self.ledger.allocate_version(job)
        def deliver(**extra):
            return self.ledger.deliver(job, '2', 'source2', 'c' * 64, 100, 'migration', profile=L.CLIENT_BACKEND,
                                       server={'release': 'd' * 64}, contract=json.dumps(CONTRACT), **extra)
        for extra in ({}, {'migrated_from': 1}, {'migrated_from': 2, 'committed': True}):
            with self.assertRaises(L.LedgerError):
                deliver(**extra)
        self.assertEqual(self.ledger.profile(self.project), L.CLIENT_SERVER)
        import sqlite3
        self.ledger.db.execute("CREATE TEMP TRIGGER reject_migration BEFORE INSERT ON version_servers "
                               "BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            deliver(migrated_from=1, committed=True)
        self.assertEqual(self.ledger.profile(self.project), L.CLIENT_SERVER)
        self.assertEqual(len(self.ledger.versions(self.project)), 1)
        self.ledger.db.execute('DROP TRIGGER reject_migration')
        self.assertTrue(deliver(migrated_from=1, committed=True))
        self.assertEqual(self.ledger.profile(self.project), L.CLIENT_BACKEND)
        self.assertEqual(self.ledger.project(self.project)['signerSha256'], 'a' * 64)
        self.assertEqual(self.ledger.server(self.project, 1), self.server)
        self.assertEqual(self.ledger.migration_lock(self.project), [MIGRATION])
        self.assertEqual(self.ledger.cargo_lock(self.project), LOCK)
        self.assertEqual([v['versionCode'] for v in self.ledger.versions(self.project)], [1, 2])

    def test_failed_legacy_revision_restores_the_delivered_source_and_profile(self):
        self.bind()
        def fails(source, *args, **kw):
            self.assertTrue((source / 'backend/legacy/server').is_dir())
            raise toolchain.StepFailed('compile', 'fixture writer failure')
        builder = service.Builder(self.root, toolchain.Tools(Path('/no-sdk')), fails, ledger=self.ledger)
        original = self.ledger.versions(self.project)
        job = builder.revise('d', self.project, {'schemaVersion': 1, 'clientRequestId': 'r2', 'request': 'change'})
        builder.run_next()
        self.assertEqual(builder.job(job['jobId'])['state'], 'failed')
        self.assertEqual(appprofile.read(self.source).name, L.CLIENT_SERVER)
        self.assertTrue((self.source / 'server/src/handlers/mod.rs').is_file())
        self.assertFalse((self.source / 'backend').exists())
        self.assertEqual(self.ledger.profile(self.project), L.CLIENT_SERVER)
        self.assertEqual(self.ledger.versions(self.project), original)

    def test_legacy_runtime_must_match_before_any_writer_command(self):
        self.bind()
        agent = LocalAgent(self.root / 'agent')
        self.addCleanup(agent.stop)
        cfg = app_host.AgentHostConfig(Path('/unused'), Path('/unused'), ('one',))
        hosting = app_host.AgentHosting(cfg, self.ledger, channel=lambda name: agent)
        with mock.patch.object(app_host, 'BIN', str(agent.bin)):
            data = agent.app / 'data'
            if data.is_dir():
                data.rmdir()
            with self.assertRaisesRegex(app_host.SetupFailed, 'latest delivered'):
                hosting.check_legacy(self.project)
            self.assertFalse(data.exists())  # observing an empty/reset container must not initialize storage
            (agent.app / 'data').mkdir(parents=True, exist_ok=True)
            (agent.app / 'keys').mkdir(exist_ok=True)
            (agent.app / 'keys/keyring.json').write_text('private fixture key; never read by the preflight')
            state = {'project': self.project, 'release': self.server['sha256'], 'versionCode': 1,
                     'dataSchema': 200010001}
            (agent.app / 'deployed.json').write_text(json.dumps(state))
            with self.assertRaises(app_host.SetupFailed):
                hosting.check_legacy(self.project)
            (agent.app / 'current').write_text(self.server['sha256'] + '\n')
            hosting.check_legacy(self.project)
            data.rmdir()
            with self.assertRaises(app_host.SetupFailed):
                hosting.check_legacy(self.project)
            self.assertFalse(data.exists())
            data.mkdir()
            (agent.app / 'deployed.json').write_text(json.dumps({**state, 'release': 'e' * 64}))
            with self.assertRaisesRegex(app_host.SetupFailed, 'latest delivered'):
                hosting.check_legacy(self.project)

    def test_old_host_configuration_is_inert_without_rust_or_crates(self):
        cfg = host.parse_builder_config({'schemaVersion': 1, 'root': str(self.root / 'configured'),
            'sdk': '/not-installed', 'clientServer': {'rustToolchain': '/missing', 'crateStore': '/missing'}})
        with mock.patch('subprocess.run', side_effect=AssertionError('no tool may run')):
            builder = host.build_builder(cfg)
        self.addCleanup(builder.ledger.close)
        self.assertFalse(hasattr(builder, 'rust_tools'))
        self.assertFalse(hasattr(builder, '_client_server'))
        self.assertIsNone(builder.hosting)


class LegacyRuntimeUpdate(unittest.TestCase):
    """Run the legacy transition through the existing generic process harness."""
    setUp = agent_tests.AgentBuiltRelease.setUp
    build = agent_tests.AgentBuiltRelease.build
    deploy = agent_tests.AgentBuiltRelease.deploy
    get = agent_tests.AgentBuiltRelease.get
    def test_legacy_binary_to_agent_python_preserves_keyring_data_and_failed_update_rollback(self):
        import shlex
        import sqlite3
        import sys
        from rose.tests.test_app_host_agent import FAKE_SERVER
        elf = self.root / 'old-server'
        elf.write_text(FAKE_SERVER.replace('@PYTHON@', __import__('sys').executable).replace('@NAME@', 'old'))
        old = app_host.Plan('p1', 1, AGENT_HEX, APP_URL, elf, hashlib.sha256(elf.read_bytes()).hexdigest(), 200010001, self.port)
        self.deploy(old)
        key = (self.agent.app / 'keys/keyring.json').read_bytes()
        database = self.agent.app / 'data/notes.sqlite3'
        with sqlite3.connect(database) as db:
            db.executescript("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT); "
                             "INSERT INTO notes VALUES (1, 'retained'); PRAGMA user_version=1;")
        def migration(plan, version, sql):
            folder = self.agent.app / 'releases' / plan.sha256
            (folder / 'migrate.py').write_text(
                'import os, sqlite3\n'
                'with sqlite3.connect(os.path.join(os.environ["DATA_DIR"], "notes.sqlite3")) as db:\n'
                f'    if db.execute("PRAGMA user_version").fetchone()[0] < {version}:\n'
                f'        db.executescript({sql!r})\n')
            start = folder / 'start'
            start.write_text(start.read_text().replace('exec ', f'{shlex.quote(sys.executable)} ./migrate.py\nexec ', 1))
        new = self.build('c3')
        migration(new, 2, 'ALTER TABLE notes ADD COLUMN archived INTEGER DEFAULT 0; PRAGMA user_version=2;')
        self.deploy(new, generate_keyring=False)
        data = self.agent.app / 'data/accounts/a.sqlite3'
        self.assertEqual(data.read_text(), 'old\nc3\n')
        self.assertEqual((self.agent.app / 'keys/keyring.json').read_bytes(), key)
        with sqlite3.connect(database) as db:
            self.assertEqual(db.execute('SELECT * FROM notes').fetchall(), [(1, 'retained', 0)])
        broken = self.build('broken', broken=True)
        migration(broken, 3, "UPDATE notes SET text='changed'; PRAGMA user_version=3;")
        with self.assertRaises(app_host.SetupFailed):
            self.deploy(broken, health_wait_s=2)
        self.assertEqual(data.read_text(), 'old\nc3\nc3\n')
        self.assertEqual((self.agent.app / 'keys/keyring.json').read_bytes(), key)
        self.assertIn(new.sha256, self.get())
        with sqlite3.connect(database) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT * FROM notes').fetchall(), [(1, 'retained', 0)])


@unittest.skipUnless(HAS_SDK or os.environ.get('ROSE_REQUIRE_APP_BUILD'), 'needs Android SDK')
class MigrationJob(unittest.TestCase):
    setUp = Migration.setUp
    bind = Migration.bind
    def test_revise_delivers_same_identity_and_legacy_history_without_a_rust_builder(self):
        self.bind()
        writer = agent_tests.InTheJob.writer()
        builder = service.Builder(self.root, toolchain.Tools(SDK, JAVA_HOME), writer,
                                   ledger=self.ledger, codegen=codegen, require_key_backup=False)
        hosting = agent_tests.InTheJob.FakeHosting(self.ledger)
        hosting.check_legacy = lambda project: hosting.calls.append(('check_legacy', project))
        builder.hosting = hosting
        old_version = self.ledger.versions(self.project)[0]
        # Signing key generation is unrelated to migration; assert the bound signer is passed through unchanged.
        def sign(unsigned, signed, keys, pin, tools):
            self.assertEqual(pin, 'a' * 64)
            shutil.copyfile(unsigned, signed)
            return {'sha256': hashlib.sha256(signed.read_bytes()).hexdigest(), 'size': signed.stat().st_size,
                    'signerSha256': pin}
        job = builder.revise('d', self.project, {'schemaVersion': 1, 'clientRequestId': 'r2', 'request': 'change'})
        with mock.patch.object(toolchain, 'ensure_key'), mock.patch.object(toolchain, 'sign', side_effect=sign):
            builder.run_next()
        self.assertEqual(builder.job(job['jobId'])['state'], 'ready', builder.job(job['jobId']))
        self.assertEqual(self.ledger.profile(self.project), L.CLIENT_BACKEND)
        self.assertEqual(self.ledger.versions(self.project)[0], old_version)
        self.assertEqual(self.ledger.project(self.project)['signerSha256'], 'a' * 64)
        self.assertEqual(self.ledger.migration_lock(self.project), [MIGRATION])
        self.assertEqual(sum(c[0] == 'check_legacy' for c in hosting.calls), 2)
