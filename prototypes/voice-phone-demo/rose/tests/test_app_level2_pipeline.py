"""A client-server app through the builder's job (docs/APP-BUILDER.md §7.6): the profile, contract and migration
checks run on the snapshot, the server half is built only with the host's injected code generator, isolation,
toolchain and crate set, and nothing is half delivered. Without any of them a job fails with an honest category,
before anything is built. With the pinned Android SDK and a Rust toolchain (CI), one app is built end to end: the
APK from the client tree with the builder's generated files, and the server with real cargo against the runtime
crate, offline, for the machine's own target."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import ledger as L, server_build, template, toolchain
from rose.app_builder.migrations import Migration
from rose.app_builder.service import Builder
from rose.tests.test_app_builder import HAS_SDK, JAVA_HOME, SDK, fake_backup

REPO = Path(__file__).resolve().parents[4]
RUNTIME = REPO / 'workspace/rose-app-runtime'
HAS_CARGO = shutil.which('cargo') is not None and shutil.which('rustc') is not None

CONTRACT = {'schemaVersion': 1, 'api': 1,
            'types': {'Note': {'fields': {'id': 'int', 'text': 'string'}},
                      'NoteList': {'fields': {'notes': {'type': 'list', 'of': 'Note'}}}},
            'routes': {'listNotes': {'method': 'GET', 'path': '/app/notes', 'response': 'NoteList'}}}
INTERNET = '<uses-permission android:name="android.permission.INTERNET"/>'


def client_server_app(contract=CONTRACT, migration='CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT);\n',
                      profile=None, handlers='pub fn hello() {}\n'):
    """A generator that writes a minimal client-server app over the skeleton."""
    def generate(source, kind, request, cancelled):
        manifest = source / 'AndroidManifest.xml'
        text = manifest.read_text()
        if INTERNET not in text:
            manifest.write_text(text.replace('  <application', f'  {INTERNET}\n  <application', 1))
        (source / 'rose-app.json').write_text(json.dumps(profile or {'schemaVersion': 1,
                                                                     'profile': 'client-server-v1'}))
        (source / 'contract').mkdir(exist_ok=True)
        (source / 'contract/api.json').write_text(json.dumps(contract))
        (source / 'server/src/handlers').mkdir(parents=True, exist_ok=True)
        (source / 'server/src/handlers/mod.rs').write_text(handlers)
        (source / 'server/migrations/account').mkdir(parents=True, exist_ok=True)
        (source / 'server/migrations/account/0001_notes.sql').write_text(migration)
    return generate


class Codegen:
    """Test-only stand-in for slice 8's generator: a main that uses the runtime, the embedded migrations and the
    writer's handlers, and two client files, one keeping a package of its own (as the sign-in core does)."""

    def __init__(self, outside=False, broken=False, writes=None):
        self.outside, self.broken, self.writes = outside, broken, writes

    def server_files(self, contract):
        if self.broken:
            raise RuntimeError('generator bug')
        files = {'rose/main.rs': 'mod meta;\nmod migrations;\n#[path = "../src/handlers/mod.rs"]\nmod handlers;\n\n'
                                 'fn main() {\n    handlers::hello();\n'
                                 '    let account: &[&str] = migrations::ACCOUNT;\n'
                                 '    let shared: Option<&[&str]> = migrations::SHARED;\n'
                                 '    let _ = (account.len(), shared, meta::APP, meta::ISSUER, meta::ADMISSION);\n'
                                 '    let _ = rose_app_runtime::json::parse(b"1");\n}\n'}
        if self.writes:
            files[self.writes] = '// not the generator\'s\n'
        return files

    def client_files(self, contract, package):
        base = 'src/elsewhere/' if self.outside else f'src/{package.replace(".", "/")}/rose/'
        return {base + 'Api.java': f'package {package}.rose;\n\npublic final class Api {{}}\n',
                base + 'Core.java': 'package dev.pixel.rosesignin;\n\npublic final class Core {}\n'}


class Passthrough(server_build.Isolation):
    """Test-only: runs cargo as is. A builder host supplies real isolation (blocker 3)."""

    def command(self, argv, env, *, writable, readonly):
        return argv, env


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._cleanup, tmp)
        self.root = Path(tmp.name) / 'builder'

    @staticmethod
    def _cleanup(tmp):
        for here, dirs, _ in os.walk(tmp.name):  # the vendored crate set is read-only
            os.chmod(here, 0o700)
        tmp.cleanup()

    def builder(self, generate, **kw):
        b = Builder(self.root, toolchain.Tools(SDK, JAVA_HOME, timeout_s=300, key_creation=True), generate,
                    backup=fake_backup, **kw)
        self.addCleanup(b.ledger.close)
        return b

    def run_job(self, b, revise=None):
        if revise is None:
            out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'Notes'})
        else:
            out = b.revise('d', revise, {'schemaVersion': 1, 'clientRequestId': f'r{len(b.projects()[0]["versions"]) + 2}',
                                         'request': 'more'})
        b.run_next()
        return out, b.job(out['jobId'])


class HonestFailures(Base):
    """Each stops before anything is built or delivered (no SDK needed: nothing gets as far as the APK)."""

    def assertFailed(self, job, category, words):
        self.assertEqual((job['state'], job['failure']['category']), ('failed', category), job)
        self.assertIn(words, job['failure']['summary'])

    def test_without_a_code_generator_the_app_is_not_half_built(self):
        b = self.builder(client_server_app())
        out, job = self.run_job(b)
        self.assertFailed(job, 'toolchain', 'no code generator')
        self.assertEqual((b.ledger.profile(out['projectId']), b.ledger.versions(out['projectId'])), ('app-v1', []))

    def test_the_writers_mistakes_are_the_writers(self):
        cases = [
            (client_server_app(contract={'schemaVersion': 1}), 'compile', 'contract'),
            (client_server_app(migration='CREATE TABLE ('), 'compile', '0001_notes.sql'),
            (client_server_app(profile={'schemaVersion': 1, 'profile': 'client-server-v9'}), 'compile', 'profile'),
            (client_server_app(handlers='pub fn f() { let _ = include_str!("/etc/passwd"); }\n'), 'compile',
             'include_str'),
        ]
        for generate, category, words in cases:
            with self.subTest(words=words):
                shutil.rmtree(self.root, ignore_errors=True)
                b = self.builder(generate, codegen=Codegen())
                _, job = self.run_job(b)
                self.assertFailed(job, category, words)

    def test_a_generator_that_fails_or_strays_is_the_builders(self):
        for codegen, words in ((Codegen(broken=True), 'could not generate'), (Codegen(outside=True), 'outside'),
                               (Codegen(writes='rose/meta.rs'), 'writes itself'),
                               (Codegen(writes='rose/migrations.rs'), 'writes itself')):
            with self.subTest(words=words):
                shutil.rmtree(self.root, ignore_errors=True)
                b = self.builder(client_server_app(), codegen=codegen)
                _, job = self.run_job(b)
                self.assertFailed(job, 'toolchain', words)


class Pieces(unittest.TestCase):
    def test_embedded_migrations_are_the_adapters_shape(self):
        tricky = 'INSERT INTO t VALUES (\'a"#b"##c\');\n'
        m = lambda scope, n, sql='SELECT 1;': Migration(scope, n, f'm{n}', hashlib.sha256(sql.encode()).hexdigest(), sql)
        text = server_build.migrations_rs({'account': [m('account', 2), m('account', 1, tricky)], 'shared': []}, False)
        # Position is the number: index i takes version i to i + 1, as rose-app-runtime-signin's Migrations reads it.
        self.assertIn('pub const ACCOUNT: &[&str] = &[\n    // 0001_m1.sql\n    r###"' + tricky + '"###,\n'
                      '    // 0002_m2.sql\n    r#"SELECT 1;"#,\n];', text)
        self.assertIn('pub const SHARED: Option<&[&str]> = None;', text)
        shared = server_build.migrations_rs({'account': [], 'shared': [m('shared', 1)]}, True)
        self.assertIn('pub const SHARED: Option<&[&str]> = Some(&[\n    // 0001_m1.sql\n    r#"SELECT 1;"#,\n]);',
                      shared)
        self.assertIn('pub const ACCOUNT: &[&str] = &[\n];', shared)
        with self.assertRaises(ValueError):  # a gap would shift every later version
            server_build.migrations_rs({'account': [m('account', 1), m('account', 3)]}, False)

    def test_meta_names_the_app_for_sign_in(self):
        text = server_build.meta_rs('app.rose.gen.p1', 'Notes: "mine"\x07', 'open')
        self.assertIn('pub const APP: &str = r#"app.rose.gen.p1"#;', text)
        self.assertIn('pub const ISSUER: &str = r#"Notes "mine""#;', text)  # no ':' or control character
        self.assertIn('rose_app_runtime::signin::Admission::Open;', text)
        self.assertIn('Admission::Invite;', server_build.meta_rs('a.b', '', 'invite'))
        self.assertIn('r#"Rose app"#', server_build.meta_rs('a.b', ':::', 'invite'))
        self.assertLessEqual(len(server_build._issuer('é' * 200).encode()), 128)
        with self.assertRaises(ValueError):
            server_build.meta_rs('a.b', 'x', 'anyone')

    def test_path_crates_in_the_cargo_toml(self):
        toml = server_build.cargo_toml(['serde@1.0.219'], {'rose-app-runtime': Path('/opt/rose/runtime')})
        self.assertIn('rose-app-runtime = { path = "/opt/rose/runtime" }\n', toml)
        for bad in ('Bad Name', 'rose-app-server', 'x"y'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    server_build.cargo_toml([], {bad: Path('/x')})

    def test_only_the_builders_own_files_keep_another_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            template.write_skeleton(src, 'com.example.app', 'App')
            core = src / 'src/com/example/app/rose/Core.java'
            core.parent.mkdir(parents=True)
            core.write_text('package dev.pixel.rosesignin;\nclass Core {}\n')
            with self.assertRaises(template.TemplateRefused):
                template.check(src, 'com.example.app')
            template.check(src, 'com.example.app', builder_files=frozenset({'src/com/example/app/rose/Core.java'}))
            other = src / 'src/com/example/app/Other.java'
            other.write_text('package evil;\nclass Other {}\n')
            with self.assertRaises(template.TemplateRefused):  # every other file still keeps the app's package
                template.check(src, 'com.example.app', builder_files=frozenset({'src/com/example/app/rose/Core.java'}))


class LedgerContracts(unittest.TestCase):
    def test_each_client_server_version_records_its_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = L.Ledger(Path(tmp) / 'b')
            self.addCleanup(ledger.close)
            project = ledger.create('d', 'r0', 'x', 'X')['projectId']
            server = {'sha256': 'e' * 64, 'size': 1, 'target': 'x86_64-unknown-linux-musl'}

            def deliver(n, **kw):
                job = ledger.create('d', 'r0', 'x', 'X') if n == 1 else ledger.revise('d', project, f'r{n}', 'more')
                ledger.advance(job['jobId'], 'building', 'b', 'b')
                ledger.allocate_version(job['jobId'])
                ledger.bind_signer(project, 'a' * 64)
                return ledger.deliver(job['jobId'], str(n), 'rev', f'{n}' * 64, 10, 'x', **kw)
            self.assertTrue(deliver(1, profile=L.CLIENT_SERVER, server=server, cargo_lock='v', contract='{"a":1}'))
            self.assertTrue(deliver(2, profile=L.CLIENT_SERVER, server=server, cargo_lock='v', contract='{"a":2}'))
            self.assertEqual(ledger.contracts(project), ['{"a":1}', '{"a":2}'])
            for bad in (dict(profile=L.CLIENT_SERVER, server=server, cargo_lock='v', contract=''),
                        dict(profile=L.CLIENT_SERVER, server=server, cargo_lock='v', contract=b'{}'),
                        dict(contract='{}')):  # a plain app has no contract
                with self.assertRaises(L.LedgerError):
                    ledger.deliver('nope', '1', 'r', '1' * 64, 10, 'x', **bad)


@unittest.skipUnless((HAS_SDK and HAS_CARGO) or (os.environ.get('ROSE_REQUIRE_APP_BUILD')
                                                and os.environ.get('ROSE_REQUIRE_CARGO')),
                     'needs the pinned Android SDK, a JDK and a Rust toolchain')
class EndToEnd(Base):
    def tools(self):
        sysroot = subprocess.run(['rustc', '--print', 'sysroot'], capture_output=True, text=True, check=True)
        host = subprocess.run(['rustc', '-vV'], capture_output=True, text=True, check=True).stdout
        target = next(line.split(': ', 1)[1] for line in host.splitlines() if line.startswith('host: '))
        return server_build.RustTools(Path(sysroot.stdout.strip()), target=target, timeout_s=600)

    def test_a_client_server_app_is_built_and_delivered_as_one_version(self):
        b = self.builder(client_server_app(), codegen=Codegen(), server_isolation=Passthrough(),
                         rust_tools=self.tools(), server_crates={'rose-app-runtime': RUNTIME})
        out, job = self.run_job(b)
        self.assertEqual(job['state'], 'ready', job)
        project = out['projectId']
        self.assertEqual(b.ledger.profile(project), L.CLIENT_SERVER)
        server = b.ledger.server(project, 1)
        self.assertEqual(server['size'], (self.root / 'artifacts' / f'{server["sha256"]}.server').stat().st_size)
        self.assertEqual([m['name'] for m in b.ledger.migration_lock(project)], ['notes'])
        self.assertEqual(json.loads(b.ledger.contracts(project)[0])['routes']['listNotes']['path'], '/app/notes')
        self.assertIn('rose-app-runtime', b.ledger.cargo_lock(project))
        # A breaking contract change at the same api is refused, and nothing new is delivered.
        broken = json.loads(json.dumps(CONTRACT))
        broken['types']['Note']['fields']['id'] = 'string'  # a response field retyped, at the same api
        b.generate = client_server_app(contract=broken)
        _, job = self.run_job(b, revise=project)
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'compile'), job)
        self.assertIn('api', job['failure']['summary'])
        self.assertEqual(len(b.ledger.versions(project)), 1)


if __name__ == '__main__':
    unittest.main()
