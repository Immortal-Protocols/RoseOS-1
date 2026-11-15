"""The owner records' writer (rose.app_builder.owner_records; docs/APP-BUILDER.md §7.7): what owner tooling calls to
record a project's native host and hosted APK app id in a builder's ledger. Synthetic values only: reserved example
names (RFC 2606) and made-up UUIDs, never a live host."""
import io
import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import sqlite3
from pathlib import Path

from rose.app_builder import ledger as L, owner_records as O

HOST = '4a4a4a4a-4444-4444-8444-44444444444b'
HOST2 = '5c5c5c5c-5555-4555-8555-55555555555d'
APK = '1e1e1e1e-1111-4111-8111-11111111111f'
ENDPOINT = 'https://notes.example.org'
ENDPOINT2 = 'https://notes.example.net/app'
_connect = sqlite3.connect
ROOT = Path(__file__).resolve().parents[2]  # prototypes/voice-phone-demo


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / 'builder'
        ledger = L.Ledger(self.dir)
        self.project = ledger.create('d', 'r0', 'x', 'X')['projectId']
        ledger.close()

    def run_tool(self, *args):
        out = io.StringIO()
        code = O.main(list(args), out=out)
        return code, json.loads(out.getvalue())

    def native(self, *extra, host=HOST, endpoint=ENDPOINT, project=None):
        return self.run_tool('native-host', '--ledger', str(self.dir), '--project', project or self.project,
                             '--host-id', host, '--endpoint', endpoint, *extra)


class Records(Base):
    def test_show_then_record_then_the_same_again(self):
        code, out = self.run_tool('show', '--ledger', str(self.dir), '--project', self.project)
        self.assertEqual((code, out), (0, {'status': 'ok', 'projectId': self.project, 'nativeHost': None,
                                           'hostedAppId': None}))
        for _ in range(2):  # the same record again is still ok
            code, out = self.native()
            self.assertEqual(code, 0)
            self.assertEqual(out['nativeHost'], {'hostId': HOST, 'endpoint': ENDPOINT})
        code, out = self.run_tool('hosted-app', '--ledger', str(self.dir), '--project', self.project,
                                  '--hosted-app-id', APK)
        self.assertEqual((code, out['hostedAppId'], out['nativeHost']['hostId']), (0, APK, HOST))
        ledger = L.Ledger(self.dir)
        self.addCleanup(ledger.close)
        self.assertEqual(ledger.native_host_endpoint(self.project), ENDPOINT)  # what a build bakes

    def test_a_replacement_names_the_record_it_replaces(self):
        self.native()
        code, out = self.native(host=HOST2, endpoint=ENDPOINT2)
        self.assertEqual((code, out['status'], out['nativeHost']['hostId']), (3, 'refused', HOST))
        code, out = self.native('--replaces-host-id', HOST2, '--replaces-endpoint', ENDPOINT, host=HOST2,
                                endpoint=ENDPOINT2)
        self.assertEqual((code, out['nativeHost']['hostId']), (3, HOST))  # not the current record
        code, out = self.native('--replaces-host-id', HOST, '--replaces-endpoint', ENDPOINT, host=HOST2,
                                endpoint=ENDPOINT2)
        self.assertEqual((code, out['nativeHost']), (0, {'hostId': HOST2, 'endpoint': ENDPOINT2}))

    def test_what_the_ledger_refuses_is_refused(self):
        for host, endpoint in (('not-a-uuid', ENDPOINT), (HOST, 'http://notes.example.org'),
                               (HOST, ENDPOINT + '/'), (HOST, ENDPOINT + ':443')):
            with self.subTest(host=host, endpoint=endpoint):
                code, out = self.native(host=host, endpoint=endpoint)
                self.assertEqual((code, out['status'], out['nativeHost']), (3, 'refused', None))
        code, out = self.run_tool('hosted-app', '--ledger', str(self.dir), '--project', self.project,
                                  '--hosted-app-id', APK.upper())
        self.assertEqual((code, out['hostedAppId']), (3, None))

    def test_usage_and_missing_ledgers_or_projects(self):
        for args in ((), ('show',), ('native-host', '--ledger', str(self.dir), '--project', self.project),
                     ('nope', '--ledger', str(self.dir), '--project', self.project)):
            with self.subTest(args=args):
                self.assertEqual(self.run_tool(*args)[0], 2)
        self.assertEqual(self.native('--replaces-host-id', HOST)[0], 2)  # half a replacement
        missing = self.dir.parent / 'nowhere'
        code, out = self.run_tool('show', '--ledger', str(missing), '--project', self.project)
        self.assertEqual((code, out['status']), (4, 'not_found'))
        self.assertFalse(missing.exists())  # no ledger is created
        self.assertEqual(self.native(project='0' * 16)[0], 4)

    def test_it_runs_as_a_module(self):
        result = subprocess.run([sys.executable, '-m', 'rose.app_builder.owner_records', 'native-host', '--ledger',
                                 str(self.dir), '--project', self.project, '--host-id', HOST, '--endpoint', ENDPOINT],
                                cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['nativeHost'], {'hostId': HOST, 'endpoint': ENDPOINT})


class AnotherProcess(Base):
    """The tool's ledger and the builder's are two connections to one file: each record is one transaction."""

    def test_a_replacement_read_before_another_writer_changed_it_is_refused(self):
        builder, tool = L.Ledger(self.dir), L.Ledger(self.dir)
        self.addCleanup(builder.close)
        self.addCleanup(tool.close)
        tool.record_native_host(self.project, HOST, ENDPOINT)
        self.assertEqual(builder.native_host_endpoint(self.project), ENDPOINT)
        builder.record_native_host(self.project, HOST, ENDPOINT2, replaces={'hostId': HOST, 'endpoint': ENDPOINT})
        with self.assertRaises(L.LedgerError):  # the tool's view is stale
            tool.record_native_host(self.project, HOST2, ENDPOINT, replaces={'hostId': HOST, 'endpoint': ENDPOINT})
        self.assertEqual(tool.native_host(self.project), {'hostId': HOST, 'endpoint': ENDPOINT2})

    def test_a_refused_record_leaves_no_transaction_open(self):
        builder, tool = L.Ledger(self.dir), L.Ledger(self.dir)
        self.addCleanup(builder.close)
        self.addCleanup(tool.close)
        with self.assertRaises(L.LedgerError):
            tool.record_native_host(self.project, HOST, ENDPOINT, replaces={'hostId': HOST2, 'endpoint': ENDPOINT2})
        self.assertFalse(tool.db.in_transaction)
        builder.record_native_host(self.project, HOST, ENDPOINT)  # the file is not left locked
        builder.record_hosted_app(self.project, APK)
        self.assertEqual(tool.hosted_app(self.project), APK)

    def test_each_record_reads_inside_its_own_write_transaction(self):
        # So no other writer can change the record between the check and the write.
        ledger = L.Ledger(self.dir)
        self.addCleanup(ledger.close)
        seen = []
        for reader, record in (('native_host', lambda: ledger.record_native_host(self.project, HOST, ENDPOINT)),
                               ('hosted_app', lambda: ledger.record_hosted_app(self.project, APK))):
            real = getattr(ledger, reader)

            def spy(project, real=real, reader=reader):
                seen.append((reader, ledger.db.in_transaction))
                return real(project)
            setattr(ledger, reader, spy)
            record()
            delattr(ledger, reader)
            self.assertFalse(ledger.db.in_transaction)
        self.assertEqual(seen, [('native_host', True), ('hosted_app', True)])

    def test_a_ledger_another_process_is_writing_is_busy_and_nothing_is_recorded(self):
        holder = L.Ledger(self.dir)
        self.addCleanup(holder.close)
        holder.db.execute('BEGIN IMMEDIATE')
        try:
            with unittest.mock.patch('sqlite3.connect', lambda *a, **k: _connect(*a, **{**k, 'timeout': 0.1})):
                out = io.StringIO()
                code = O.main(['native-host', '--ledger', str(self.dir), '--project', self.project, '--host-id', HOST,
                               '--endpoint', ENDPOINT], out=out)
        finally:
            holder.db.execute('ROLLBACK')
        self.assertEqual((code, json.loads(out.getvalue())['status']), (5, 'busy'))
        self.assertIsNone(holder.native_host(self.project))

    def test_a_ledger_that_cannot_be_written_is_an_error_not_busy(self):
        # SQLite's own read-only refusal (mode=ro), not lock contention: nothing recorded, and no retry advised.
        uri = (self.dir / 'builder.sqlite3').as_uri() + '?mode=ro'
        with unittest.mock.patch('sqlite3.connect', lambda *a, **k: _connect(uri, uri=True, **{
                n: v for n, v in k.items() if n != 'uri'})):
            code, out = self.native()
        self.assertEqual((code, out['status']), (6, 'error'))
        self.assertIn('readonly', out['message'])
        self.assertIsNone(self.run_tool('show', '--ledger', str(self.dir), '--project', self.project)[1]['nativeHost'])

    def test_a_ledger_that_cannot_be_opened_is_an_error(self):
        def refuse(*a, **k):
            raise sqlite3.OperationalError('unable to open database file')
        with unittest.mock.patch('sqlite3.connect', refuse):
            code, out = self.native()
        self.assertEqual((code, out['status']), (6, 'error'))

    def test_a_failed_commit_leaves_no_transaction_open(self):
        ledger = L.Ledger(self.dir)
        self.addCleanup(ledger.close)
        real = ledger.db

        class FailingCommit:
            in_transaction = property(lambda self: real.in_transaction)

            def execute(self, sql, *args):
                if sql == 'COMMIT':
                    raise sqlite3.OperationalError('disk I/O error')
                return real.execute(sql, *args)
        ledger.db = FailingCommit()
        with self.assertRaises(sqlite3.OperationalError):
            ledger.record_hosted_app(self.project, APK)
        ledger.db = real
        self.assertFalse(real.in_transaction)
        self.assertIsNone(ledger.hosted_app(self.project))


if __name__ == '__main__':
    unittest.main()
