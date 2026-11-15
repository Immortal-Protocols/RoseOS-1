"""The delivered server's deploy plan (rose.app_builder.server_delivery; docs/APP-BUILDER.md §7.7): read from the
builder's own records, shaped for Chariot's agreed owner calls (ProtocolsBackend #2564: PUT artifacts, POST deploy).
Synthetic values only; nothing is uploaded, deployed or contacted."""
import hashlib
import io
import itertools
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import ledger as L, server_delivery as D, server_manifest as SM
from rose.tests.test_app_server_manifest import tiny_elf

HOST = '4a4a4a4a-4444-4444-8444-44444444444b'
ENDPOINT = 'https://notes.example.org'
M1 = {'scope': 'account', 'number': 1, 'name': 'notes', 'sha256': '1' * 64}
M2 = {'scope': 'account', 'number': 2, 'name': 'pins', 'sha256': '2' * 64}
ROOT = Path(__file__).resolve().parents[2]


def chariot_accepts(body: dict) -> bool:
    """Chariot's DeployNative bounds at #2564 (hosting/app_http.py, vm_types.NativeRevision), restated."""
    return (set(body) == {'sha256', 'args', 'port', 'health_path', 'data_schema'}
            and re.fullmatch(r'[0-9a-f]{64}', body['sha256']) is not None
            and isinstance(body['args'], list) and len(body['args']) <= 32
            and type(body['port']) is int and 1024 <= body['port'] <= 65535
            and re.fullmatch(r'/[A-Za-z0-9/_-]{0,127}', body['health_path']) is not None
            and '..' not in body['health_path']
            and type(body['data_schema']) is int and body['data_schema'] >= 1)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'builder'
        self.l = L.Ledger(self.root)
        self.addCleanup(lambda: self.l.close())
        self.project = self.l.create('d', 'r0', 'x', 'X')['projectId']
        self.n = 0

    def deliver(self, migrations=(M1,), target=SM.TARGET, elf=None, manifest=True):
        self.n += 1
        job = (self.l.create('d', 'r0', 'x', 'X') if self.n == 1
               else self.l.revise('d', self.project, f'r{self.n}', 'more'))
        self.l.advance(job['jobId'], 'building', 'b', 'b')
        self.assertEqual(self.l.allocate_version(job['jobId']), self.n)
        self.l.bind_signer(self.project, 'a' * 64)
        elf = tiny_elf() if elf is None else elf
        server = {'sha256': hashlib.sha256(elf).hexdigest(), 'size': len(elf), 'target': target}
        (self.root / 'artifacts').mkdir(exist_ok=True)
        (self.root / 'artifacts' / f'{server["sha256"]}.server').write_bytes(elf)
        extra = {}
        if manifest and target == SM.TARGET:
            extra['manifest'] = SM.encode(SM.manifest(self.project, self.n, server, list(migrations)))
        self.assertTrue(self.l.deliver(job['jobId'], str(self.n), 'rev', f'{self.n:x}' * 64, 10, 'x',
                                       profile=L.CLIENT_SERVER, server=server, migrations=list(migrations),
                                       cargo_lock='v', **extra))
        return server

    def host(self):
        self.l.record_native_host(self.project, HOST, ENDPOINT)


class Plan(Base):
    def test_a_delivered_version_is_planned_for_its_native_host(self):
        server = self.deliver()
        self.host()
        p = D.plan(self.l, self.root, self.project)
        self.assertEqual((p['projectId'], p['versionCode'], p['hostId'], p['endpoint']),
                         (self.project, 1, HOST, ENDPOINT))
        self.assertEqual(p['artifact']['sha256'], server['sha256'])
        self.assertEqual(Path(p['artifact']['path']).read_bytes(),
                         (self.root / 'artifacts' / f'{server["sha256"]}.server').read_bytes())
        self.assertEqual(p['deploy'], {'sha256': server['sha256'], 'args': [], 'port': 8080, 'health_path': '/health',
                                       'data_schema': 1 + 1 * 10000})
        self.assertTrue(chariot_accepts(p['deploy']))
        self.assertEqual(p['manifestSha256'], hashlib.sha256(self.l.server_manifest(self.project, 1)).hexdigest())

    def test_the_newest_version_unless_one_is_named(self):
        self.deliver()
        second = self.deliver(migrations=(M1, M2), elf=tiny_elf(extra=b'2'))
        self.host()
        self.assertEqual(D.plan(self.l, self.root, self.project)['versionCode'], 2)
        self.assertEqual(D.plan(self.l, self.root, self.project)['artifact']['sha256'], second['sha256'])
        self.assertEqual(D.plan(self.l, self.root, self.project, 1)['versionCode'], 1)
        # A new migration changes the data schema, which Chariot v1 refuses to deploy over the old one.
        self.assertNotEqual(D.plan(self.l, self.root, self.project, 1)['deploy']['data_schema'],
                            D.plan(self.l, self.root, self.project, 2)['deploy']['data_schema'])

    def test_what_is_not_deployable_is_refused(self):
        self.deliver(target='x86_64-unknown-linux-gnu')  # the build machine's own target: no manifest
        self.host()
        with self.assertRaisesRegex(D.Refused, 'target'):
            D.plan(self.l, self.root, self.project)

    def test_no_native_host_is_refused(self):
        self.deliver()
        with self.assertRaisesRegex(D.Refused, 'native host'):
            D.plan(self.l, self.root, self.project)

    def test_a_stored_binary_that_is_not_the_recorded_one_is_refused(self):
        server = self.deliver()
        self.host()
        path = self.root / 'artifacts' / f'{server["sha256"]}.server'
        path.write_bytes(path.read_bytes() + b'x')
        with self.assertRaisesRegex(D.Refused, 'not the one'):
            D.plan(self.l, self.root, self.project)
        path.unlink()
        with self.assertRaisesRegex(D.Refused, 'not in the builder'):
            D.plan(self.l, self.root, self.project)

    def test_a_server_over_the_hosts_limit_is_refused(self):
        self.deliver()
        self.host()
        real = self.l.server_manifest
        big = json.loads(real(self.project, 1))
        big['elf']['size'] = D.MAX_HOSTED_ELF + 1
        self.l.server_manifest = lambda project, code: SM.encode(big)
        with self.assertRaisesRegex(D.Refused, '64 MiB'):
            D.plan(self.l, self.root, self.project)

    def test_plain_projects_and_missing_versions(self):
        with self.assertRaises(LookupError):
            D.plan(self.l, self.root, self.project)  # nothing delivered
        with self.assertRaises(LookupError):
            D.plan(self.l, self.root, '0' * 16)
        self.deliver()
        with self.assertRaises(LookupError):
            D.plan(self.l, self.root, self.project, 7)

    def test_data_schema_is_one_integer_per_schema(self):
        seen = {}
        for account, shared in itertools.product((0, 1, 2, 9998, 9999), (None, 0, 1, 9999)):
            schema = {'account': account} | ({} if shared is None else {'shared': shared})
            value = D.data_schema(schema)
            self.assertGreaterEqual(value, 1)
            key = (account, shared or 0)  # an empty shared database is the same schema as none
            self.assertEqual(seen.setdefault(value, key), key)


class Cli(Base):
    def run_tool(self, *args):
        out = io.StringIO()
        code = D.main(list(args), out=out)
        return code, json.loads(out.getvalue())

    def test_exit_codes(self):
        args = ('plan', '--ledger', str(self.root), '--project', self.project)
        self.assertEqual(self.run_tool(*args)[0], 4)  # nothing delivered yet
        self.deliver()
        code, out = self.run_tool(*args)
        self.assertEqual((code, out['status']), (3, 'refused'))  # no native host yet
        self.host()
        code, out = self.run_tool(*args)
        self.assertEqual((code, out['status'], out['plan']['versionCode']), (0, 'ok', 1))
        self.assertEqual(self.run_tool('plan', '--ledger', str(self.root))[0], 2)
        self.assertEqual(self.run_tool(*args, '--version', '0')[0], 2)
        missing = self.root.parent / 'nowhere'
        self.assertEqual(self.run_tool('plan', '--ledger', str(missing), '--project', self.project)[0], 4)
        self.assertFalse(missing.exists())

    def test_it_runs_as_a_module_and_changes_nothing(self):
        self.deliver()
        self.host()
        self.l.close()
        self.root.chmod(0o750)  # a mode the builder's own open would reset to 0700
        schema = lambda: sqlite3.connect(self.root / 'builder.sqlite3').execute(
            'SELECT name, sql FROM sqlite_master ORDER BY name').fetchall()
        before = (schema(), self.root.stat().st_mode)
        with sqlite3.connect(self.root / 'builder.sqlite3') as db:
            db.execute('DROP TABLE installs')  # a table an older ledger lacks: the plan must not create it
        dropped = schema()
        result = subprocess.run([sys.executable, '-m', 'rose.app_builder.server_delivery', 'plan', '--ledger',
                                 str(self.root), '--project', self.project], cwd=ROOT, capture_output=True, text=True,
                                timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(chariot_accepts(json.loads(result.stdout)['plan']['deploy']))
        self.assertEqual(schema(), dropped)  # nothing created
        self.assertEqual(self.root.stat().st_mode, before[1])  # directory mode untouched
        self.assertNotEqual(dropped, before[0])

    def test_the_reader_cannot_write(self):
        self.deliver()
        reader = L.Ledger.reader(self.root)
        self.addCleanup(reader.close)
        self.assertEqual(len(reader.versions(self.project)), 1)
        with self.assertRaises(sqlite3.OperationalError):
            reader.db.execute("UPDATE projects SET name='y'")
        with self.assertRaises(sqlite3.OperationalError):
            L.Ledger.reader(self.root.parent / 'nowhere')
        self.assertFalse((self.root.parent / 'nowhere').exists())


if __name__ == '__main__':
    unittest.main()
