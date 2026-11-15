"""The ledger's Level 2 and 3 records (docs/APP-BUILDER.md §7.6, §3.3 share): a client-server version pairs its APK
with one server ELF and its migration lock in one transaction, its profile is bound for good, and a project's hosted
app id is recorded once, explicitly, never derived. Level 1 projects and their library entries are unchanged."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import ledger as L

SERVER = {'sha256': 'e' * 64, 'size': 1234, 'target': 'x86_64-unknown-linux-musl'}
M1 = {'scope': 'account', 'number': 1, 'name': 'notes', 'sha256': '1' * 64}
M2 = {'scope': 'account', 'number': 2, 'name': 'pinned', 'sha256': '2' * 64}
HOSTED = '11111111-1111-4111-8111-111111111111'


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'builder'
        self.l = L.Ledger(self.root)
        self.addCleanup(self.l.close)
        self.project = self.l.create('d', 'r0', 'x', 'X')['projectId']
        self.first = True

    def job(self, n):
        job = (self.l.create('d', 'r0', 'x', 'X') if self.first else
               self.l.revise('d', self.project, f'r{n}', 'more'))
        self.first = False
        self.l.advance(job['jobId'], 'building', 'b', 'b')
        self.assertEqual(self.l.allocate_version(job['jobId']), n)
        self.l.bind_signer(self.project, 'a' * 64)
        return job['jobId']

    def deliver(self, n, **kw):
        return self.l.deliver(self.job(n), str(n), f'rev{n}', f'{n:x}' * 64, 10, 'x', **kw)


class Pairing(Base):
    def test_a_client_server_version_records_its_server_and_lock(self):
        self.assertTrue(self.deliver(1, profile=L.CLIENT_SERVER, server=SERVER, migrations=[M1]))
        self.assertEqual(self.l.profile(self.project), L.CLIENT_SERVER)
        self.assertEqual(self.l.server(self.project, 1), SERVER)
        self.assertTrue(self.deliver(2, profile=L.CLIENT_SERVER, server=SERVER, migrations=[M1, M2]))
        self.assertEqual(self.l.migration_lock(self.project), [M1, M2])

    def test_nothing_is_half_recorded(self):
        job = self.job(1)
        # A stray row makes the server insert collide after the version row went in: all of it must roll back.
        self.l.db.execute('INSERT INTO version_servers VALUES (?,?,?,?,?)', (self.project, 1, 'f' * 64, 1, 't'))
        with self.assertRaises(sqlite3.IntegrityError):
            self.l.deliver(job, '1', 'rev1', '1' * 64, 10, 'x', profile=L.CLIENT_SERVER, server=SERVER,
                           migrations=[M1])
        self.l.db.execute('DELETE FROM version_servers')
        self.assertEqual((self.l.versions(self.project), self.l.server(self.project, 1),
                          self.l.migration_lock(self.project), self.l.profile(self.project)), ([], None, [], 'app-v1'))
        self.assertNotEqual(self.l.job(job)['state'], 'ready')

    def test_a_delivered_migration_cannot_change_or_go(self):
        self.deliver(1, profile=L.CLIENT_SERVER, server=SERVER, migrations=[M1, M2])
        for migrations in ([{**M1, 'sha256': '3' * 64}, M2], [{**M1, 'name': 'other'}, M2], [M1], []):
            with self.subTest(migrations=migrations):
                with self.assertRaises(L.LedgerError):
                    self.deliver(2, profile=L.CLIENT_SERVER, server=SERVER, migrations=migrations)
                self.first = False
        self.assertEqual([v['versionCode'] for v in self.l.versions(self.project)], [1])
        self.assertEqual(self.l.migration_lock(self.project), [M1, M2])  # still readable, unchanged
        M3 = {'scope': 'shared', 'number': 1, 'name': 'tags', 'sha256': '4' * 64}
        self.assertTrue(self.deliver(2, profile=L.CLIENT_SERVER, server=SERVER, migrations=[M1, M2, M3]))
        self.assertEqual(self.l.migration_lock(self.project), [M1, M2, M3])

    def test_a_bound_server_cannot_be_dropped(self):
        self.deliver(1, profile=L.CLIENT_SERVER, server=SERVER, migrations=[M1])
        with self.assertRaises(L.LedgerError):
            self.deliver(2)  # a version without its server
        self.assertEqual([v['versionCode'] for v in self.l.versions(self.project)], [1])

    def test_inputs_are_checked_before_anything_is_written(self):
        bad = [
            dict(profile=L.CLIENT_SERVER, server=None),
            dict(profile=L.CLIENT_SERVER, server={**SERVER, 'sha256': 'E' * 64}),
            dict(profile=L.CLIENT_SERVER, server={**SERVER, 'size': True}),
            dict(profile=L.CLIENT_SERVER, server={**SERVER, 'extra': 1}),
            dict(profile=L.CLIENT_SERVER, server=SERVER, migrations=[{**M1, 'scope': 'other'}]),
            dict(profile=L.CLIENT_SERVER, server=SERVER, migrations=[{**M1, 'name': 'Bad Name'}]),
            dict(profile=L.CLIENT_SERVER, server=SERVER, migrations=[M1, M1]),
            dict(profile='app-v2'),
            dict(server=SERVER),  # a plain app has no server
            dict(migrations=[M1]),
        ]
        for kw in bad:
            with self.subTest(kw=kw):
                with self.assertRaises(L.LedgerError):
                    self.l.deliver('nope', '1', 'r', '1' * 64, 10, 'x', **kw)

    def test_level1_is_unchanged(self):
        self.assertTrue(self.deliver(1))
        self.assertEqual(self.l.profile(self.project), 'app-v1')
        self.assertIsNone(self.l.server(self.project, 1))
        [version] = self.l.versions(self.project)
        self.assertEqual(set(version), {'versionCode', 'versionName', 'createdAt', 'jobId', 'sourceRevision', 'apk',
                                        'notes', 'apiReport'})
        self.assertTrue(self.deliver(2, profile=L.CLIENT_SERVER, server=SERVER))  # Level 1 to 2 is allowed, once


class Hosting(Base):
    def test_the_mapping_is_recorded_once_and_never_moves(self):
        self.assertIsNone(self.l.hosted_app(self.project))
        self.l.record_hosted_app(self.project, HOSTED)
        self.l.record_hosted_app(self.project, HOSTED)  # the same again changes nothing
        self.assertEqual(self.l.hosted_app(self.project), HOSTED)
        with self.assertRaises(L.LedgerError):
            self.l.record_hosted_app(self.project, '22222222-2222-4222-8222-222222222222')
        other = self.l.create('d', 'other', 'y', 'Y')['projectId']
        with self.assertRaises(L.LedgerError):
            self.l.record_hosted_app(other, HOSTED)  # another project's id
        for bad in ('', HOSTED.upper(), 'not-a-uuid', None, 7, self.project):
            with self.subTest(bad=bad):
                with self.assertRaises(L.LedgerError):
                    self.l.record_hosted_app(other, bad)
        with self.assertRaises(L.LedgerError):
            self.l.record_hosted_app('nope', '33333333-3333-4333-8333-333333333333')
        self.assertIsNone(self.l.hosted_app(other))


class Upgrade(unittest.TestCase):
    def test_a_ledger_from_before_level2_opens_and_keeps_its_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'builder'
            old = L.Ledger(root)
            project = old.create('d', 'r', 'x', 'X')['projectId']
            for table in ('project_profiles', 'version_servers', 'version_migrations', 'project_hosting'):
                old.db.execute(f'DROP TABLE {table}')  # as a ledger written before these existed
            old.close()
            new = L.Ledger(root)
            self.addCleanup(new.close)
            self.assertEqual(new.profile(project), 'app-v1')
            self.assertEqual(new.migration_lock(project), [])
            self.assertIsNone(new.hosted_app(project))


if __name__ == '__main__':
    unittest.main()
