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
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from rose.app_builder import (appprofile, contract as C, crates, ledger as L, migrations, server_build, service,
                              template, toolchain)
from rose.app_builder.migrations import Migration
from rose.app_builder.service import Builder
from rose.tests.test_app_builder import HAS_SDK, JAVA_HOME, SDK, fake_backup

REPO = Path(__file__).resolve().parents[4]
RUNTIME = REPO / 'workspace/rose-app-runtime'
BUILDER_CRATES = {'rose-app-runtime': RUNTIME, 'rose-app-runtime-signin': RUNTIME}  # none get as far as cargo
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

    def __init__(self, outside=False, broken=False, writes=None, client=None, main_extra=''):
        self.outside, self.broken, self.writes = outside, broken, writes
        self.client, self.main_extra = client, main_extra

    def server_files(self, contract):
        if self.broken:
            raise RuntimeError('generator bug')
        files = {'rose/main.rs': 'mod meta;\nmod migrations;\n#[path = "../src/handlers/mod.rs"]\nmod handlers;\n\n'
                                 'fn main() {\n    handlers::hello();\n'
                                 '    let account: &[&str] = migrations::ACCOUNT;\n'
                                 '    let shared: Option<&[&str]> = migrations::SHARED;\n'
                                 '    let _ = (account.len(), shared, meta::APP, meta::ISSUER, meta::ADMISSION);\n'
                                 '    let _ = rose_app_runtime::json::parse(b"1");\n' + self.main_extra + '}\n'}
        if self.writes:
            files[self.writes] = '// not the generator\'s\n'
        return files

    def client_files(self, contract, package):
        if self.client is not None:
            return self.client(f'src/{package.replace(".", "/")}/')
        base = 'src/elsewhere/' if self.outside else f'src/{package.replace(".", "/")}/rose/'
        return {base + 'Api.java': f'package {package}.rose;\n\npublic final class Api {{}}\n',
                base + 'Core.java': 'package dev.pixel.rosesignin;\n\npublic final class Core {}\n'}


class Passthrough(server_build.Isolation):
    """Test-only: runs cargo as is, recording what it was asked to keep read-only. A builder host supplies real
    isolation (blocker 3)."""

    def __init__(self):
        self.readonly = set()

    def command(self, argv, env, *, writable, readonly):
        self.readonly.update(str(p) for p in readonly)
        return argv, env


def also(generate, path, text):
    """generate, then one more file."""
    def more(source, kind, request, cancelled):
        generate(source, kind, request, cancelled)
        (source / path).write_text(text, encoding='utf-8')
    return more


def package_folder(source: Path) -> Path:
    return next(source.glob('src/**/MainActivity.java')).parent


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

    def builder(self, generate, **kw):
        kw.setdefault('server_crates', BUILDER_CRATES)
        return super().builder(generate, **kw)

    def fresh(self, generate, **kw):
        shutil.rmtree(self.root, ignore_errors=True)
        return self.builder(generate, **kw)

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
            (also(client_server_app(), 'server/migrations/account/\u0660\u0660\u0660\u0662_more.sql', 'SELECT 1;\n'),
             'compile', '_more.sql'),  # digits are ASCII digits, not any Unicode digit
        ]
        for generate, category, words in cases:
            with self.subTest(words=words):
                shutil.rmtree(self.root, ignore_errors=True)
                b = self.builder(generate, codegen=Codegen())
                _, job = self.run_job(b)
                self.assertFailed(job, category, words)

    def test_a_generator_that_fails_or_strays_is_the_builders(self):
        def client(files):
            return Codegen(client=lambda pkg: {k.format(pkg=pkg): v for k, v in files.items()})
        good = 'package x;\n'
        for codegen, words in ((Codegen(broken=True), 'could not generate'), (Codegen(outside=True), 'outside'),
                               (Codegen(writes='rose/meta.rs'), 'writes itself'),
                               (Codegen(writes='rose/migrations.rs'), 'writes itself'),
                               (client({'{pkg}Main.java': good}), 'outside'),  # the writer's own package folder
                               (client({'{pkg}rose/../../AndroidManifest.xml': good}), 'outside'),
                               (client({'{pkg}rose/./Api.java': good}), 'outside'),
                               (client({'{pkg}rose//Api.java': good}), 'outside'),
                               (client({'{pkg}rose/': good}), 'outside'),
                               (client({'{pkg}rose/Api.java': b'bytes'}), 'outside'),
                               (Codegen(client=lambda pkg: {1: good}), 'outside'),
                               (client({'{pkg}rose/Api.java': good, '{pkg}rose/Api.java/Inner.java': good}),
                                'could not be written'),
                               (client({'{pkg}rose/A\x00.java': good}), 'could not be written'),
                               (client({'{pkg}rose/Api.java': 'package x;\ud800\n'}), 'could not be written'),
                               (client({'{pkg}rose/' + 'A' * 300 + '.java': good}), 'could not be written')):
            files = sorted(map(repr, codegen.client_files(None, 'p.q'))) if codegen.client or codegen.outside else []
            with self.subTest(words=words, files=files, broken=codegen.broken, writes=codegen.writes):
                b = self.fresh(client_server_app(), codegen=codegen)
                _, job = self.run_job(b)
                self.assertFailed(job, 'toolchain', words)

    def test_without_the_runtime_crate_the_server_is_not_built(self):
        # The builder's own generated code names the runtime and the adapter: without either, not the writer's fault.
        for crates, missing in (({}, 'rose-app-runtime or rose-app-runtime-signin'),
                                ({'rose-app-other': RUNTIME}, 'rose-app-runtime or rose-app-runtime-signin'),
                                ({'rose-app-runtime': RUNTIME}, 'no rose-app-runtime-signin crate')):
            with self.subTest(crates=crates):
                b = self.fresh(client_server_app(), codegen=Codegen(), server_crates=crates)
                _, job = self.run_job(b)
                self.assertFailed(job, 'toolchain', missing)

    def test_a_file_where_the_builders_folder_goes_is_the_writers(self):
        def writer(up):
            inner = client_server_app()

            def generate(source, kind, request, cancelled):
                inner(source, kind, request, cancelled)
                folder = package_folder(source)
                for java in folder.glob('*.java'):  # the package rule reads the declaration, not the folder
                    java.rename(source / 'src' / java.name)
                target = folder.parents[up] if up else folder
                shutil.rmtree(target)
                target.write_text('not a folder\n')
            return generate
        for up in (0, 1):
            with self.subTest(up=up):
                b = self.fresh(writer(up), codegen=Codegen())
                _, job = self.run_job(b)
                self.assertFailed(job, 'compile', 'is not a folder')

    def test_a_contract_too_large_to_keep_is_refused_before_anything_is_built(self):
        # Between the file's size (251 bytes) and its normalised form's (353): the limit is on what the ledger keeps.
        with mock.patch.object(service, 'MAX_CONTRACT', 300):
            b = self.builder(client_server_app(), codegen=Codegen())
            _, job = self.run_job(b)
        self.assertFailed(job, 'compile', 'too large')

    def test_a_revision_is_held_to_what_was_delivered(self):
        """The ledger's record of delivered versions, as each check reads it (no build: a delivered history is
        stood in for)."""
        def contract(fields):
            c = json.loads(json.dumps(CONTRACT))
            c['types']['Note']['fields'] = fields
            return c
        tag = lambda t: {'id': 'int', 'tag': {'type': t, 'optional': True}}
        for history in ((contract(tag('string')), contract({'id': 'int'})),  # v3 is fine after v2, breaks v1's phones
                        (contract({'id': 'int'}), contract(tag('string')))):  # ... breaks only v2's
            with self.subTest(history=[sorted(h['types']['Note']['fields']) for h in history]):
                texts = [C.canonical_bytes(C.parse(c).data).decode() for c in history]
                b = self.fresh(client_server_app(contract=contract(tag('int'))), codegen=Codegen())
                b.ledger.contracts = lambda project: list(texts)
                _, job = self.run_job(b)
                self.assertFailed(job, 'compile', 'string to int')

        # A delivered client-server app cannot become a plain one.
        def plain(source, kind, request, cancelled):
            pass
        b = self.fresh(plain, codegen=Codegen())
        b.ledger.profile = lambda project: L.CLIENT_SERVER
        _, job = self.run_job(b)
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'compile'), job)
        self.assertIn('rose-app.json', job['failure']['summary'])

        # A delivered migration cannot change.
        sql = 'CREATE TABLE notes (id INTEGER PRIMARY KEY);\n'
        b = self.fresh(client_server_app(), codegen=Codegen())
        b.ledger.migration_lock = lambda project: [
            Migration('account', 1, 'notes', hashlib.sha256(sql.encode()).hexdigest(), sql).lock()]
        _, job = self.run_job(b)
        self.assertFailed(job, 'compile', '0001_notes.sql')

        # The server build starts from the delivered Cargo.lock and is held to the delivered crates.
        b = self.fresh(client_server_app(), codegen=Codegen())
        b.ledger.cargo_lock = lambda project: '# delivered lock\n'
        b.ledger.crate_lock = lambda project: {'serde@1.0.0': 'a' * 64}
        seen = {}

        def build(*args, **kw):
            seen.update(kw)
            raise server_build.BuildFailed('toolchain', 'server_build', 'stopped here by the test')
        with mock.patch.object(server_build, 'build', build):
            _, job = self.run_job(b)
        self.assertFailed(job, 'toolchain', 'stopped here by the test')
        self.assertEqual((seen['previous_lock'], seen['locked']), ('# delivered lock\n', {'serde@1.0.0': 'a' * 64}))
        self.assertEqual(seen['path_crates'], BUILDER_CRATES)


class Pieces(unittest.TestCase):
    def test_embedded_migrations_are_the_adapters_shape(self):
        tricky = 'INSERT INTO t VALUES (\'a"#b\\"###c\');\n'
        m = lambda scope, n, sql='SELECT 1;': Migration(scope, n, f'm{n}', hashlib.sha256(sql.encode()).hexdigest(), sql)
        text = server_build.migrations_rs({'account': [m('account', 2), m('account', 1, tricky)], 'shared': []}, False)
        # Position is the number: index i takes version i to i + 1, as rose-app-runtime-signin's Migrations reads it.
        self.assertIn('pub const ACCOUNT: &[&str] = &[\n    // 0001_m1.sql\n    '
                      '"INSERT INTO t VALUES (\'a\\"#b\\\\\\"###c\');\\n",\n'
                      '    // 0002_m2.sql\n    "SELECT 1;",\n];', text)
        self.assertIn('pub const SHARED: Option<&[&str]> = None;', text)
        shared = server_build.migrations_rs({'account': [], 'shared': [m('shared', 1)]}, True)
        self.assertIn('pub const SHARED: Option<&[&str]> = Some(&[\n    // 0001_m1.sql\n    "SELECT 1;",\n]);',
                      shared)
        self.assertIn('pub const ACCOUNT: &[&str] = &[\n];', shared)
        with self.assertRaises(ValueError):  # a gap would shift every later version
            server_build.migrations_rs({'account': [m('account', 1), m('account', 3)]}, False)

    def test_names_take_ascii_digits_only(self):
        for name in ('\u0660\u0660\u0660\u0661_init.sql', '\uff10\uff10\uff10\uff11_init.sql'):
            self.assertIsNone(appprofile.MIGRATION.fullmatch(name))
            self.assertIsNone(migrations.NAME.fullmatch(name))
        self.assertTrue(appprofile.MIGRATION.fullmatch('0001_init.sql') and migrations.NAME.fullmatch('0001_init.sql'))
        for pattern in (appprofile.CRATE, crates.CRATE):
            self.assertIsNone(pattern.fullmatch('serde@1.\u0660.219'))
            self.assertTrue(pattern.fullmatch('serde@1.0.219'))

    def test_a_contract_file_too_large_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'contract').mkdir()
            (Path(tmp) / 'contract/api.json').write_text(' ' * (C.MAX_FILE + 1) + '{}')
            with self.assertRaises(C.ContractRefused) as caught:
                C.load(Path(tmp))
            self.assertIn('larger than', str(caught.exception))

    def test_meta_names_the_app_for_sign_in(self):
        text = server_build.meta_rs('app.rose.gen.p1', 'Notes: "mine"\x07', 'open')
        self.assertIn('pub const APP: &str = "app.rose.gen.p1";', text)
        self.assertIn('pub const ISSUER: &str = "Notes \\"mine\\"";', text)  # no ':' or control character
        self.assertIn('rose_app_runtime::signin::Admission::Open;', text)
        self.assertIn('Admission::Invite;', server_build.meta_rs('a.b', '', 'invite'))
        self.assertIn('"Rose app"', server_build.meta_rs('a.b', ':::', 'invite'))
        self.assertLessEqual(len(server_build._issuer('é' * 200).encode()), 128)
        with self.assertRaises(ValueError):
            server_build.meta_rs('a.b', 'x', 'anyone')

    @unittest.skipUnless(HAS_CARGO or os.environ.get('ROSE_REQUIRE_CARGO'), 'needs a Rust toolchain')
    def test_embedded_sql_is_byte_for_byte_the_checked_sql(self):
        # rustc rewrites a raw string's CRLF and refuses a bare CR; the embedded SQL must be the locked bytes.
        sqls = ['CREATE TABLE t (v TEXT);\r\nINSERT INTO t VALUES (\'a\r\nb\');\r\n',
                "INSERT INTO t VALUES ('a\rb\t\x07\u2028\\\n\"####é');\n"]
        current = {'account': [Migration('account', n, f'm{n}', hashlib.sha256(sql.encode()).hexdigest(), sql)
                               for n, sql in enumerate(sqls, 1)]}
        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp) / 'main.rs'
            meta = server_build.meta_rs('a.b', 'N', 'open').replace('rose_app_runtime::signin::Admission::Open', '()')
            meta = meta.replace('rose_app_runtime::signin::Admission', '()')
            body = server_build.migrations_rs(current, False) + meta
            main.write_text(body.replace('//!', '//') +
                            'fn main() { for s in ACCOUNT { for b in s.bytes() { print!("{:02x}", b); } println!(); } '
                            'let _ = (SHARED, APP, ISSUER, ADMISSION); }\n', newline='')
            subprocess.run(['rustc', '--edition', '2021', '-o', str(Path(tmp) / 'm'), str(main)], check=True,
                           capture_output=True)
            out = subprocess.run([str(Path(tmp) / 'm')], capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(out, [sql.encode().hex() for sql in sqls])

    def test_path_crates_in_the_cargo_toml(self):
        toml = server_build.cargo_toml(['serde@1.0.219'], {'rose-app-runtime': Path('/opt/rose/runtime')})
        self.assertIn('rose-app-runtime = { path = "/opt/rose/runtime" }\n', toml)
        odd = Path('/opt/a"b\\c d')
        parsed = tomllib.loads(server_build.cargo_toml([], {'rose-app-runtime': odd}))
        self.assertEqual(parsed['dependencies']['rose-app-runtime'], {'path': str(odd)})
        for bad in ('Bad Name', 'rose-app-server', 'x"y'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    server_build.cargo_toml([], {bad: Path('/x')})

    def test_builder_crates_depend_only_on_each_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def crate(folder, deps=''):
                (root / folder / 'src').mkdir(parents=True)
                (root / folder / 'Cargo.toml').write_text(f'[package]\nname = "{folder}"\nversion = "0.1.0"\n\n'
                                                          f'[dependencies]\n{deps}')
                return root / folder
            runtime = crate('rose-app-runtime')
            adapter = crate('rose-app-runtime-signin', 'rose-app-runtime = { path = "../rose-app-runtime" }\n'
                                                       'rose-signin = { path = "../rose-signin", features = ["sqlite"] }\n')
            signin = crate('rose-signin', 'sha2 = "0.10"\n')
            other = crate('elsewhere-runtime')
            # rose-signin uses a crates.io crate, so the set is needed; the runtime alone does not.
            self.assertTrue(server_build._check_path_crates({'rose-app-runtime': runtime,
                                                             'rose-app-runtime-signin': adapter,
                                                             'rose-signin': signin}))
            self.assertFalse(server_build._check_path_crates({'rose-app-runtime': runtime}))
            misnamed = crate('misnamed')
            (root / 'store').mkdir()
            moved = root / 'store/rose-signin'
            signin.rename(moved)
            signin.symlink_to(moved, target_is_directory=True)  # ../rose-signin is a link: cargo sees the link
            for crates in ({'rose-app-runtime': runtime, 'rose-app-runtime-signin': adapter, 'rose-signin': moved},
                           {'rose-app-runtime': misnamed},
                           {'rose-app-runtime': other, 'rose-app-runtime-signin': adapter, 'rose-signin': signin},
                           {'rose-app-runtime': runtime, 'rose-app-runtime-signin': adapter},
                           {'rose-app-runtime': root / 'missing'}):
                with self.subTest(crates=sorted(crates)):
                    with self.assertRaises(server_build.BuildFailed) as caught:
                        server_build._check_path_crates({k: Path(os.path.realpath(v)) for k, v in crates.items()})
                    self.assertEqual(caught.exception.category, 'toolchain')

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
            # Every other file still keeps the app's package: by its path, not its name or its folder.
            for other in ('src/com/example/app/Other.java', 'src/com/example/app/Core.java',
                          'src/com/example/app/rose/Other.java'):
                with self.subTest(other=other):
                    (src / other).write_text('package evil;\nclass X {}\n')
                    with self.assertRaises(template.TemplateRefused):
                        template.check(src, 'com.example.app',
                                       builder_files=frozenset({'src/com/example/app/rose/Core.java'}))
                    (src / other).unlink()

    def test_the_client_tree_leaves_out_only_the_servers_top_level_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap, out = Path(tmp) / 's', Path(tmp) / 'o'
            for path in ('server/src/main.rs', 'contract/api.json', 'rose-app.json', 'src/app/server/Helper.java',
                         'src/app/contract/Terms.java', 'res/raw/contract', 'AndroidManifest.xml'):
                (snap / path).parent.mkdir(parents=True, exist_ok=True)
                (snap / path).write_text('x')
            shutil.copytree(snap, out, symlinks=True, ignore=service.client_tree_ignore(snap))
            self.assertEqual(sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file()),
                             ['AndroidManifest.xml', 'res/raw/contract', 'src/app/contract/Terms.java',
                              'src/app/server/Helper.java'])

    def test_a_builder_crate_set_up_wrong_is_the_builders(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / 'snap'
            (snap / 'server/src').mkdir(parents=True)
            uses_registry = Path(tmp) / 'rose-app-runtime'
            (uses_registry / 'src').mkdir(parents=True)
            (uses_registry / 'Cargo.toml').write_text('[package]\nname = "rose-app-runtime"\nversion = "0.1.0"\n\n'
                                                      '[dependencies]\nsha2 = "0.10"\n')
            real = os.path.realpath
            at_root = lambda p: '/rose-app-runtime' if str(p).endswith('at-root') else real(p)
            for n, (crates, words, patch) in enumerate((
                    ({'rose-app-runtime': Path(tmp) / 'nowhere'}, 'missing', None),
                    ({'rose-app-runtime': uses_registry}, 'no crate set', None),  # the adapter's crates need one
                    ({'rose-app-runtime': Path(tmp) / 'at-root'}, 'own names for crates', at_root))):
                with self.subTest(words=words):
                    tools = mock.Mock(available=lambda: None)
                    with mock.patch.object(server_build.os.path, 'realpath', patch or real), \
                            self.assertRaises(server_build.BuildFailed) as caught:
                        server_build.build(snap, Path(tmp) / f'work{n}', [], None, tools, Passthrough(),
                                           cache=Path(tmp) / 'c', generated={'rose/main.rs': 'fn main() {}\n'},
                                           path_crates=crates)
                    self.assertEqual(caught.exception.category, 'toolchain')
                    self.assertIn(words, str(caught.exception))


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
            self.assertTrue(deliver(1, profile=L.CLIENT_SERVER, server=server, cargo_lock='v', contract='{"b":1}'))
            self.assertTrue(deliver(2, profile=L.CLIENT_SERVER, server=server, cargo_lock='v', contract='{"a":2}'))
            self.assertEqual(ledger.contracts(project), ['{"b":1}', '{"a":2}'])  # by version, not by text
            job = ledger.revise('d', project, 'r3', 'more')
            ledger.advance(job['jobId'], 'building', 'b', 'b')
            ledger.allocate_version(job['jobId'])
            for bad in (dict(profile=L.CLIENT_SERVER, server=server, cargo_lock='v', contract=''),
                        dict(profile=L.CLIENT_SERVER, server=server, cargo_lock='v', contract=b'{}'),
                        dict(profile=L.CLIENT_SERVER, server=server, cargo_lock='v',
                             contract='"' + 'x' * L.MAX_CONTRACT + '"'),
                        dict(contract='{}')):  # a plain app has no contract
                with self.subTest(bad=str(bad)[:60]):
                    with self.assertRaises(L.LedgerError):
                        ledger.deliver(job['jobId'], '3', 'rev', '3' * 64, 10, 'x', **bad)
            self.assertTrue(ledger.deliver(job['jobId'], '3', 'rev', '3' * 64, 10, 'x', profile=L.CLIENT_SERVER,
                                           server=server, cargo_lock='v', contract='"' + 'x' * 100 + '"'))


@unittest.skipUnless((HAS_SDK and HAS_CARGO) or (os.environ.get('ROSE_REQUIRE_APP_BUILD')
                                                and os.environ.get('ROSE_REQUIRE_CARGO')),
                     'needs the pinned Android SDK, a JDK and a Rust toolchain')
class EndToEnd(Base):
    def tools(self):
        sysroot = subprocess.run(['rustc', '--print', 'sysroot'], capture_output=True, text=True, check=True)
        host = subprocess.run(['rustc', '-vV'], capture_output=True, text=True, check=True).stdout
        target = next(line.split(': ', 1)[1] for line in host.splitlines() if line.startswith('host: '))
        return server_build.RustTools(Path(sysroot.stdout.strip()), target=target, timeout_s=600)

    def crates(self, embed=False):
        """The runtime by a symlink (what is mounted and remapped is its real folder), plus two test-only builder
        crates standing in for the adapter and the crate it depends on, each with a panic location in the binary
        (and, with ``embed``, one naming its own folder, which no remap covers)."""
        here = self.root.parent / 'crates'
        here.mkdir()
        (here / 'runtime').symlink_to(RUNTIME, target_is_directory=True)

        def crate(name, deps, body):
            (here / name / 'src').mkdir(parents=True)
            (here / name / 'Cargo.toml').write_text(f'[package]\nname = "{name}"\nversion = "0.1.0"\n'
                                                    f'edition = "2021"\n\n[dependencies]\n{deps}')
            (here / name / 'src/lib.rs').write_text(body)
            return here / name
        here_dir = 'pub fn here() -> &\'static str { env!("CARGO_MANIFEST_DIR") }\n' if embed else ''
        signin = crate('test-signin', '', '#[inline(never)]\npub fn boom(x: bool) { if x { panic!("boom") } }\n'
                       + here_dir)
        adapter = crate('rose-app-runtime-signin', 'test-signin = { path = "../test-signin" }\n',
                        '#[inline(never)]\npub fn touch(x: bool) { if x { panic!("touch") } test_signin::boom(x) }\n'
                        + ('pub fn here() -> &\'static str { test_signin::here() }\n' if embed else ''))
        return {'rose-app-runtime': here / 'runtime', 'rose-app-runtime-signin': adapter}, {'test-signin': signin}

    def test_a_client_server_app_is_built_and_delivered_as_one_version(self):
        crates, deps = self.crates()
        isolation = Passthrough()
        main = '    rose_app_runtime_signin::touch(std::env::args().count() > 99);\n'
        b = self.builder(client_server_app(), codegen=Codegen(main_extra=main),
                         server_isolation=isolation, rust_tools=self.tools(), server_crates=crates,
                         server_crate_deps=deps)
        out, job = self.run_job(b)
        self.assertEqual(job['state'], 'ready', job)
        project = out['projectId']
        # Every builder crate is read-only to the build and remapped out of the binary, by its real folder.
        folders = [os.path.realpath(p) for p in (*crates.values(), *deps.values())]
        self.assertLessEqual(set(folders), isolation.readonly)
        elf = (self.root / 'artifacts' / f'{b.ledger.server(project, 1)["sha256"]}.server').read_bytes()
        for folder in folders:
            self.assertNotIn(folder.encode(), elf)
            self.assertNotIn(folder, job['logTail'] or '')
        for name in ('rose-app-runtime', 'rose-app-runtime-signin', 'test-signin'):
            self.assertIn(f'/rose-crates/{name}/'.encode(), elf)
        # The APK holds the builder's generated client files, the sign-in core in its own package.
        package = b.projects()[0]['packageName'].replace('.', '/')
        apk = next(p for p in (self.root / 'artifacts').glob('*.apk'))
        with zipfile.ZipFile(apk) as z:
            dex = b''.join(z.read(n) for n in z.namelist() if n.startswith('classes') and n.endswith('.dex'))
        self.assertIn(f'L{package}/rose/Api;'.encode(), dex)
        self.assertIn(b'Ldev/pixel/rosesignin/Core;', dex)
        self.assertEqual(b.ledger.profile(project), L.CLIENT_SERVER)
        server = b.ledger.server(project, 1)
        self.assertEqual(server['size'], (self.root / 'artifacts' / f'{server["sha256"]}.server').stat().st_size)
        self.assertEqual([m['name'] for m in b.ledger.migration_lock(project)], ['notes'])
        self.assertEqual(json.loads(b.ledger.contracts(project)[0])['routes']['listNotes']['path'], '/app/notes')
        self.assertIn('rose-app-runtime', b.ledger.cargo_lock(project))
        self.assertIn('test-signin', b.ledger.cargo_lock(project))
        # A breaking contract change at the same api is refused, and nothing new is delivered.
        broken = json.loads(json.dumps(CONTRACT))
        broken['types']['Note']['fields']['id'] = 'string'  # a response field retyped, at the same api
        b.generate = client_server_app(contract=broken)
        _, job = self.run_job(b, revise=project)
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'compile'), job)
        self.assertIn('api', job['failure']['summary'])
        self.assertEqual(len(b.ledger.versions(project)), 1)

    def test_a_builder_crate_that_names_its_own_folder_is_not_delivered(self):
        crates, deps = self.crates(embed=True)
        main = '    println!("{}", rose_app_runtime_signin::here());\n'
        b = self.builder(client_server_app(), codegen=Codegen(main_extra=main), server_isolation=Passthrough(),
                         rust_tools=self.tools(), server_crates=crates, server_crate_deps=deps)
        out, job = self.run_job(b)
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'toolchain'), job)
        self.assertIn('builder\'s own crates', job['failure']['summary'])
        self.assertEqual(b.ledger.versions(out['projectId']), [])



@unittest.skipUnless(HAS_SDK or os.environ.get('ROSE_REQUIRE_APP_BUILD'), 'needs the pinned Android SDK and a JDK')
class CompiledClasses(unittest.TestCase):
    """The second line of defence: whatever a file declared, compiled classes are in the app's package or a builder
    file's own (and a package that only shares a builder package's prefix is neither)."""

    def test_only_the_app_and_builder_packages_compile(self):
        tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=300, key_creation=True)
        for declared, ok in (('dev.pixel.rosesignin', True), ('dev.pixel.rosesigninx', False),
                             ('com.example.appx', False)):
            with self.subTest(declared=declared), tempfile.TemporaryDirectory() as tmp:
                src, work = Path(tmp) / 'src', Path(tmp) / 'work'
                template.write_skeleton(src, 'com.example.app', 'App')
                core = src / 'src/com/example/app/rose/Core.java'
                core.parent.mkdir(parents=True)
                core.write_text(f'package {declared};\npublic final class Core {{}}\n')
                work.mkdir()
                build = lambda: toolchain.build(src, work, 'com.example.app', 1, '1', tools,
                                                builder_files=frozenset({'src/com/example/app/rose/Core.java'}),
                                                builder_packages=frozenset({'dev.pixel.rosesignin'}))
                if ok:
                    build()
                    continue
                with self.assertRaises(toolchain.StepFailed) as caught:
                    build()
                self.assertEqual(caught.exception.category, 'compile')
                self.assertIn('Classes must be in package', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
