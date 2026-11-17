"""The ledger's Level 2 and 3 records (docs/APP-BUILDER.md §7.6, §3.3 share): a client-server version pairs its APK
with one server ELF and its migration lock in one transaction, its profile is bound for good, and a project's hosted
app id is recorded once, explicitly, never derived. Level 1 projects and their library entries are unchanged."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import crates, ledger as L, server_manifest as SM

SERVER = {'sha256': 'e' * 64, 'size': 1234, 'target': 'x86_64-unknown-linux-musl'}
M1 = {'scope': 'account', 'number': 1, 'name': 'notes', 'sha256': '1' * 64}
M2 = {'scope': 'account', 'number': 2, 'name': 'pinned', 'sha256': '2' * 64}
HOSTED = '11111111-1111-4111-8111-111111111111'
LOCK = '# Cargo.lock\nversion = 4\n'
SERDE = crates.Locked('serde', '1.0.219', '5' * 64)


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
        if kw.get('profile') == L.CLIENT_SERVER:
            kw.setdefault('cargo_lock', LOCK)
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
                           migrations=[M1], crates=[SERDE], cargo_lock=LOCK)
        self.l.db.execute('DELETE FROM version_servers')
        self.assertEqual((self.l.versions(self.project), self.l.server(self.project, 1),
                          self.l.migration_lock(self.project), self.l.profile(self.project),
                          self.l.crate_lock(self.project), self.l.cargo_lock(self.project)),
                         ([], None, [], 'app-v1', {}, None))
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
            dict(profile=L.CLIENT_SERVER, server=SERVER, crates=[SERDE]),  # no Cargo.lock
            dict(profile=L.CLIENT_SERVER, server=SERVER, cargo_lock=''),
            dict(profile=L.CLIENT_SERVER, server=SERVER, cargo_lock=b'x'),
            dict(profile=L.CLIENT_SERVER, server=SERVER, cargo_lock='x' * (L.MAX_CARGO_LOCK + 1)),
            dict(profile=L.CLIENT_SERVER, server=SERVER, cargo_lock=LOCK, crates=[SERDE, SERDE]),
            dict(profile=L.CLIENT_SERVER, server=SERVER, cargo_lock=LOCK, crates=[{'name': 'serde'}]),
            dict(profile=L.CLIENT_SERVER, server=SERVER, cargo_lock=LOCK,
                 crates=[crates.Locked('serde', '1.0.219', 'E' * 64)]),
            dict(profile=L.CLIENT_SERVER, server=SERVER, cargo_lock=LOCK,
                 crates=[crates.Locked('ser de', '1.0.219', '5' * 64)]),
            dict(profile='app-v2'),
            dict(server=SERVER),  # a plain app has no server
            dict(migrations=[M1]),
            dict(crates=[SERDE]),
            dict(cargo_lock=LOCK),
        ]
        for kw in bad:
            with self.subTest(kw=kw):
                with self.assertRaises(L.LedgerError):
                    self.l.deliver('nope', '1', 'r', '1' * 64, 10, 'x', **kw)

    def test_crates_and_the_cargo_lock_are_recorded_and_a_crate_never_changes(self):
        other = crates.Locked('itoa', '1.0.15', '6' * 64)
        self.assertTrue(self.deliver(1, profile=L.CLIENT_SERVER, server=SERVER, crates=[SERDE, other],
                                     cargo_lock=LOCK))
        self.assertEqual(self.l.crates(self.project, 1), [
            {'name': 'itoa', 'version': '1.0.15', 'sha256': '6' * 64},
            {'name': 'serde', 'version': '1.0.219', 'sha256': '5' * 64}])
        self.assertEqual(self.l.crate_lock(self.project), {'serde@1.0.219': '5' * 64, 'itoa@1.0.15': '6' * 64})
        self.assertEqual(self.l.cargo_lock(self.project), LOCK)
        with self.assertRaises(L.LedgerError):  # the same crate version with other contents
            self.deliver(2, profile=L.CLIENT_SERVER, server=SERVER,
                         crates=[crates.Locked('serde', '1.0.219', '7' * 64)])
        self.assertEqual([v['versionCode'] for v in self.l.versions(self.project)], [1])
        # A crate may be dropped, and a new version of it may come in; the next build starts from the newest lock.
        newer = '# Cargo.lock\nversion = 4\n# 2\n'
        self.assertTrue(self.deliver(2, profile=L.CLIENT_SERVER, server=SERVER,
                                     crates=[crates.Locked('serde', '1.0.220', '8' * 64)], cargo_lock=newer))
        self.assertEqual(self.l.cargo_lock(self.project), newer)
        self.assertEqual(self.l.crate_lock(self.project)['serde@1.0.219'], '5' * 64)  # still held, if it comes back
        with self.assertRaises(L.LedgerError):
            self.deliver(3, profile=L.CLIENT_SERVER, server=SERVER,
                         crates=[crates.Locked('itoa', '1.0.15', '9' * 64)])

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



class Manifests(Base):
    """§7.7: a client-server version's manifest (App Core's own description of its server) is recorded with the
    version, in the same transaction, only when it names that version, its project and its server."""
    MUSL = {**SERVER, 'target': SM.TARGET}

    def manifest(self, version_code=1, server=None, project=None):
        return SM.encode(SM.manifest(project or self.project, version_code, server or self.MUSL, [M1]))

    def test_recorded_with_its_version(self):
        self.assertIsNone(self.l.server_manifest(self.project, 1))
        data = self.manifest()
        self.assertTrue(self.deliver(1, profile=L.CLIENT_SERVER, server=self.MUSL, migrations=[M1], manifest=data))
        self.assertEqual(self.l.server_manifest(self.project, 1), data)
        self.assertEqual(SM.decode(self.l.server_manifest(self.project, 1))['schema'], {'account': 1})
        # A version whose server the host does not run (another target) has none, and is still delivered.
        self.assertTrue(self.deliver(2, profile=L.CLIENT_SERVER, server=SERVER, migrations=[M1]))
        self.assertIsNone(self.l.server_manifest(self.project, 2))

    def test_a_manifest_for_anything_else_is_refused_and_nothing_is_recorded(self):
        other = SM.encode(SM.manifest('0123456789abcdef', 1, self.MUSL, [M1]))
        for kw in (dict(manifest=self.manifest(version_code=2)),  # another version
                   dict(manifest=other),  # another project
                   dict(manifest=self.manifest(server={**self.MUSL, 'size': 9})),  # another server
                   dict(manifest=self.manifest() + b' '),  # not canonical
                   dict(manifest=b'{}')):
            with self.subTest(kw=kw), self.assertRaises(L.LedgerError):
                self.first = True
                self.deliver(1, profile=L.CLIENT_SERVER, server=self.MUSL, migrations=[M1], **kw)
            self.assertEqual(self.l.versions(self.project), [])
            self.assertIsNone(self.l.server_manifest(self.project, 1))
        with self.assertRaises(L.LedgerError):  # a plain version has no server, so no manifest
            self.deliver(1, manifest=self.manifest())


# Synthetic fixtures only: reserved example names (RFC 2606) and made-up UUIDs, never a live host.
HOST = '4a4a4a4a-4444-4444-8444-44444444444b'
HOST2 = '55555555-5555-4555-8555-555555555555'
ENDPOINT = 'https://notes.example.org'
ENDPOINT2 = 'https://notes.example.net/app'


class NativeHost(Base):
    """§7.7: the native host (Chariot's HostView host id and endpoint), recorded only from owner tooling, kept apart
    from the hosted APK app id, and the endpoint a client-server build bakes into RoseServer.URL."""

    def test_recorded_once_then_replaced_only_by_naming_the_current_record(self):
        self.assertIsNone(self.l.native_host(self.project))
        self.assertIsNone(self.l.native_host_endpoint(self.project))
        self.l.record_native_host(self.project, HOST, ENDPOINT)
        self.l.record_native_host(self.project, HOST, ENDPOINT)  # the same again changes nothing
        self.assertEqual(self.l.native_host(self.project), {'hostId': HOST, 'endpoint': ENDPOINT})
        self.assertEqual(self.l.native_host_endpoint(self.project), ENDPOINT)
        for host, endpoint in ((HOST2, ENDPOINT), (HOST, ENDPOINT2), (HOST2, ENDPOINT2)):
            with self.subTest(host=host, endpoint=endpoint), self.assertRaises(L.LedgerError):
                self.l.record_native_host(self.project, host, endpoint)  # never a silent move
        for stale in ({'hostId': HOST2, 'endpoint': ENDPOINT}, {'hostId': HOST, 'endpoint': ENDPOINT2},
                      {'hostId': HOST}, {}):
            with self.subTest(replaces=stale), self.assertRaises(L.LedgerError):
                self.l.record_native_host(self.project, HOST2, ENDPOINT2, replaces=stale)
        self.assertEqual(self.l.native_host_endpoint(self.project), ENDPOINT)
        # A domain change on the same host, then a replaced host: each names the record it replaces.
        self.l.record_native_host(self.project, HOST, ENDPOINT2, replaces={'hostId': HOST, 'endpoint': ENDPOINT})
        self.assertEqual(self.l.native_host(self.project), {'hostId': HOST, 'endpoint': ENDPOINT2})
        self.l.record_native_host(self.project, HOST2, ENDPOINT2, replaces={'hostId': HOST, 'endpoint': ENDPOINT2})
        self.assertEqual(self.l.native_host(self.project), {'hostId': HOST2, 'endpoint': ENDPOINT2})

    def test_nothing_to_replace_is_refused(self):
        with self.assertRaises(L.LedgerError):
            self.l.record_native_host(self.project, HOST, ENDPOINT, replaces={'hostId': HOST2, 'endpoint': ENDPOINT2})
        self.assertIsNone(self.l.native_host(self.project))

    def test_another_projects_host_or_endpoint_is_refused(self):
        self.l.record_native_host(self.project, HOST, ENDPOINT)
        other = self.l.create('d', 'other', 'y', 'Y')['projectId']
        for host, endpoint in ((HOST, ENDPOINT2), (HOST2, ENDPOINT)):
            with self.subTest(host=host, endpoint=endpoint), self.assertRaises(L.LedgerError):
                self.l.record_native_host(other, host, endpoint)
        self.assertIsNone(self.l.native_host(other))
        self.l.record_native_host(other, HOST2, ENDPOINT2)
        with self.assertRaises(L.LedgerError):  # nor by a replacement
            self.l.record_native_host(other, HOST, ENDPOINT2, replaces={'hostId': HOST2, 'endpoint': ENDPOINT2})
        self.assertEqual(self.l.native_host(other), {'hostId': HOST2, 'endpoint': ENDPOINT2})
        with self.assertRaises(L.LedgerError):
            self.l.record_native_host('nope', '66666666-6666-4666-8666-666666666666', 'https://x.example.org')

    def test_the_host_id_and_endpoint_are_checked(self):
        for bad in ('', HOST.upper(), 'not-a-uuid', None, 7, self.project, HOST + '0', 'x' + HOST):
            with self.subTest(host=bad), self.assertRaises(L.LedgerError):
                self.l.record_native_host(self.project, bad, ENDPOINT)
        for bad in ('http://notes.example.org', 'https://Notes.example.org', 'https://notes.example.org/#x',
                    'https://notes.example.org?q=1', 'https://user@notes.example.org', 'https://10.0.0.1',
                    'https://notes.example.org:0', 'https://' + 'a' * 2050 + '.org', '', None, b'https://x.org',
                    ' https://notes.example.org', 'https://notes.example.org\n',
                    'https://notes.example.org/', 'https://notes.example.org/app/', 'https://notes.example.org:443',
                    'https://notes.example.org:443/app', 'https://notes.example.org/' + 'a' * 2048):
            with self.subTest(endpoint=bad), self.assertRaises(L.LedgerError):
                self.l.record_native_host(self.project, HOST, bad)
        self.assertIsNone(self.l.native_host(self.project))
        longest = 'https://notes.example.org/' + 'a' * (L.MAX_ENDPOINT - len('https://notes.example.org/'))
        self.l.record_native_host(self.project, HOST, longest)  # at the bound; one more is refused above
        other = self.l.create('d', 'o', 'y', 'Y')['projectId']
        self.l.record_native_host(other, HOST2, 'https://notes.example.org:8443/app')  # another port is kept

    def test_another_spelling_of_a_recorded_endpoint_is_refused(self):
        self.l.record_native_host(self.project, HOST, ENDPOINT)
        other = self.l.create('d', 'other', 'y', 'Y')['projectId']
        for spelling in (ENDPOINT + '/', ENDPOINT + ':443', ENDPOINT + ':443/'):
            with self.subTest(spelling=spelling), self.assertRaises(L.LedgerError):
                self.l.record_native_host(other, HOST2, spelling)
        self.assertIsNone(self.l.native_host(other))

    def test_it_is_not_the_hosted_apk_app_id(self):
        self.l.record_hosted_app(self.project, HOSTED)
        self.assertIsNone(self.l.native_host(self.project))  # neither record stands in for the other
        self.l.record_native_host(self.project, HOST, ENDPOINT)
        self.assertEqual(self.l.hosted_app(self.project), HOSTED)
        self.assertEqual(self.l.native_host(self.project)['hostId'], HOST)


class Upgrade(unittest.TestCase):
    def test_a_ledger_from_before_level2_opens_and_keeps_its_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'builder'
            old = L.Ledger(root)
            project = old.create('d', 'r', 'x', 'X')['projectId']
            for table in ('project_profiles', 'version_servers', 'version_migrations', 'project_hosting',
                          'version_crates', 'version_cargo_locks'):
                old.db.execute(f'DROP TABLE {table}')  # as a ledger written before these existed
            old.close()
            new = L.Ledger(root)
            self.addCleanup(new.close)
            self.assertEqual(new.profile(project), 'app-v1')
            self.assertEqual(new.migration_lock(project), [])
            self.assertIsNone(new.hosted_app(project))
            self.assertEqual((new.crate_lock(project), new.cargo_lock(project)), ({}, None))


AGENT = 'a1' * 16
AGENT2 = 'b2' * 16
APP_URL = f'https://agent-{AGENT}.apps.example.org'
APP_URL2 = f'https://agent-{AGENT2}.apps.example.org'


class AgentContainer(Base):
    """§7.7a: the Chariot agent container (its agent id and CHARIOT_APP_URL), recorded only from owner tooling under
    the native host's rules, and ahead of a native host in what a client-server build bakes into RoseServer.URL.
    Synthetic reserved names only (RFC 2606), never a live agent."""

    def test_recorded_once_then_replaced_only_by_naming_the_current_record(self):
        self.assertIsNone(self.l.agent_container(self.project))
        self.assertIsNone(self.l.backend_endpoint(self.project))
        self.l.record_agent_container(self.project, AGENT, APP_URL)
        self.l.record_agent_container(self.project, AGENT, APP_URL)  # the same again changes nothing
        self.assertEqual(self.l.agent_container(self.project), {'agentId': AGENT, 'appUrl': APP_URL})
        for agent, url in ((AGENT2, APP_URL), (AGENT, APP_URL2), (AGENT2, APP_URL2)):
            with self.subTest(agent=agent, url=url), self.assertRaises(L.LedgerError):
                self.l.record_agent_container(self.project, agent, url)  # never a silent move
        with self.assertRaises(L.LedgerError):
            self.l.record_agent_container(self.project, AGENT2, APP_URL2, replaces={'agentId': AGENT2,
                                                                                   'appUrl': APP_URL})
        self.l.record_agent_container(self.project, AGENT2, APP_URL2, replaces={'agentId': AGENT, 'appUrl': APP_URL})
        self.assertEqual(self.l.backend_endpoint(self.project), APP_URL2)

    def test_the_container_is_baked_ahead_of_a_native_host(self):
        self.l.record_native_host(self.project, HOST, ENDPOINT)
        self.assertEqual(self.l.backend_endpoint(self.project), ENDPOINT)  # until a container is recorded
        self.l.record_agent_container(self.project, AGENT, APP_URL)
        self.assertEqual(self.l.backend_endpoint(self.project), APP_URL)
        self.assertEqual(self.l.native_host(self.project), {'hostId': HOST, 'endpoint': ENDPOINT})  # kept

    def test_another_projects_agent_or_url_is_refused(self):
        self.l.record_agent_container(self.project, AGENT, APP_URL)
        other = self.l.create('d', 'other', 'y', 'Y')['projectId']
        for agent, url in ((AGENT, APP_URL2), (AGENT2, APP_URL)):
            with self.subTest(agent=agent, url=url), self.assertRaises(L.LedgerError):
                self.l.record_agent_container(other, agent, url)
        self.assertIsNone(self.l.agent_container(other))
        with self.assertRaises(L.LedgerError):
            self.l.record_agent_container('nope', AGENT2, APP_URL2)

    def test_the_agent_id_and_url_are_checked(self):
        for bad in ('', AGENT.upper(), AGENT[:-1], AGENT + '0', 'g' * 32, None, 7, HOST):
            with self.subTest(agent=bad), self.assertRaises(L.LedgerError):
                self.l.record_agent_container(self.project, bad, APP_URL)
        for bad in ('http://agent.example.org', APP_URL + '/', APP_URL + ':443', APP_URL + '?q=1', 'https://10.0.0.1',
                    '', None, ' ' + APP_URL):
            with self.subTest(url=bad), self.assertRaises(L.LedgerError):
                self.l.record_agent_container(self.project, AGENT, bad)
        self.assertIsNone(self.l.agent_container(self.project))


if __name__ == '__main__':
    unittest.main()
