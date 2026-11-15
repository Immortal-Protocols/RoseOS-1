"""The Level 2 profile declaration and its checks (docs/APP-BUILDER.md §7): a project without rose-app.json is
checked exactly as before, and a client-server project keeps the builder's files, its users' data and the builder's
machine out of the writer's reach. Every case is a tree the test writes; nothing is compiled or run."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import appprofile as P
from rose.app_builder import template

PKG = 'app.rose.gen.p0123456789abcdef'
INTERNET = '  <uses-permission android:name="android.permission.INTERNET"/>\n  <application'
DECL = {'schemaVersion': 1, 'profile': 'client-server-v1'}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'source'
        self.root.mkdir(mode=0o700)
        template.write_skeleton(self.root, PKG, 'Notes')

    def put(self, rel, text=''):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def client_server(self, decl=None, internet=True):
        self.put(P.FILE, json.dumps(DECL if decl is None else decl))
        self.put('contract/api.json', '{}')
        self.put('server/src/handlers/mod.rs', 'pub struct App;\n')
        if internet:
            manifest = self.root / 'AndroidManifest.xml'
            manifest.write_text(manifest.read_text().replace('  <application', INTERNET, 1))

    def refused(self, reason, bound=None):
        with self.assertRaises(P.ProfileRefused) as caught:
            P.check(self.root, PKG, bound)
        self.assertEqual(caught.exception.reason, reason, str(caught.exception))
        return caught.exception


class Level1(Base):
    def test_level1_is_unchanged(self):
        self.assertEqual(P.check(self.root, PKG), P.Profile(P.APP_V1))
        template.check(self.root, PKG)  # the same tree, the same answer
        self.put('server/src/handlers/mod.rs', 'pub struct App;\n')
        for check in (lambda: template.check(self.root, PKG), lambda: P.check(self.root, PKG)):
            with self.assertRaises(template.TemplateRefused):  # no declaration: server/ is still unknown
                check()

    def test_a_delivered_server_cannot_be_dropped(self):
        self.refused('profile_dropped', bound=P.CLIENT_SERVER_V1)
        self.assertEqual(P.check(self.root, PKG, bound=P.APP_V1).name, P.APP_V1)


class Declaration(Base):
    def test_a_good_tree_passes(self):
        self.client_server()
        self.assertEqual(P.check(self.root, PKG), P.Profile(P.CLIENT_SERVER_V1, P.ServerDecl((), False, 'invite')))
        self.client_server({**DECL, 'server': {'crates': ['serde@1.0.219', 'base64@0.22.1'], 'shared': True,
                                               'admission': 'open'}})
        self.put('server/migrations/shared/0001_notes.sql', 'CREATE TABLE n (x);')
        self.put('server/migrations/account/0001_items.sql', 'CREATE TABLE i (x);')
        self.put('server/assets/words.txt', 'a b c')
        self.put('server/src/handlers/build.rs', '// a module named build is fine')
        self.assertEqual(P.check(self.root, PKG).server,
                         P.ServerDecl(('serde@1.0.219', 'base64@0.22.1'), True, 'open'))

    def test_the_declaration_is_strict(self):
        self.client_server()
        bad = {
            'unknown key': {**DECL, 'extra': 1},
            'schema 2': {**DECL, 'schemaVersion': 2},
            'schema true': {**DECL, 'schemaVersion': True},
            'unknown profile': {**DECL, 'profile': 'app-v2'},
            'app-v1 declared': {**DECL, 'profile': 'app-v1'},
            'not an object': [],
            'unknown server key': {**DECL, 'server': {'port': 1}},
            'too many crates': {**DECL, 'server': {'crates': [f'c{i}@1.0.0' for i in range(33)]}},
            'a duplicate crate': {**DECL, 'server': {'crates': ['serde@1.0.0', 'serde@1.0.1']}},
            'a range': {**DECL, 'server': {'crates': ['serde@^1']}},
            'no version': {**DECL, 'server': {'crates': ['serde']}},
            'an upper-case name': {**DECL, 'server': {'crates': ['Serde@1.0.0']}},
            'a leading zero': {**DECL, 'server': {'crates': ['serde@01.0.0']}},
            'shared as text': {**DECL, 'server': {'shared': 'yes'}},
            'unknown admission': {**DECL, 'server': {'admission': 'anyone'}},
        }
        for name, decl in bad.items():
            with self.subTest(name):
                self.put(P.FILE, json.dumps(decl))
                self.refused('profile_invalid')
        for name, raw in {'repeated key': '{"schemaVersion":1,"schemaVersion":1,"profile":"client-server-v1"}',
                          'not JSON': '{', 'not UTF-8': None}.items():
            with self.subTest(name):
                if raw is None:
                    (self.root / P.FILE).write_bytes(b'\xff')
                else:
                    self.put(P.FILE, raw)
                self.refused('profile_invalid')
        (self.root / P.FILE).unlink()
        (self.root / P.FILE).mkdir()
        self.refused('profile_invalid')

    def test_a_linked_declaration_is_refused(self):
        self.client_server()
        (self.root / P.FILE).unlink()
        os.symlink('/etc/passwd', self.root / P.FILE)
        self.refused('profile_invalid')


class Layout(Base):
    def setUp(self):
        super().setUp()
        self.client_server()

    def test_the_builder_writes_the_cargo_files(self):
        for rel in ('server/Cargo.toml', 'server/cargo.lock', 'server/build.rs', 'server/.cargo/config.toml',
                    'server/rust-toolchain.toml', 'server/rust-toolchain'):
            with self.subTest(rel):
                path = self.put(rel)
                self.assertTrue(self.refused('server_build_file').dependency)
                path.unlink()
                if path.parent.name == '.cargo':
                    path.parent.rmdir()

    def test_only_source_assets_and_migrations(self):
        cases = ['server/notes.txt', 'server/src/data.json', 'server/migrations/account/1_x.sql',
                 'server/migrations/account/0001_X.sql', 'server/migrations/other/0001_x.sql',
                 'server/migrations/account/sub/0001_x.sql', 'server/migrations/shared/0001_x.sql']
        for rel in cases:
            with self.subTest(rel):
                path = self.put(rel)
                self.refused('layout_invalid')
                path.unlink()
                shutil.rmtree(self.root / 'server/migrations', ignore_errors=True)  # each case on its own
                P.check(self.root, PKG)
        (self.root / P.HANDLERS).unlink()
        self.refused('layout_invalid')

    def test_native_and_script_files_stay_refused_everywhere(self):
        for rel in ('server/src/x.c', 'server/assets/lib.so', 'server/assets/run.sh', 'contract/gen.py'):
            with self.subTest(rel):
                path = self.put(rel)
                with self.assertRaises(template.TemplateRefused):
                    P.check(self.root, PKG)
                path.unlink()

    def test_links_under_server_are_refused(self):
        os.symlink('/etc', self.root / 'server/assets')
        with self.assertRaises(template.TemplateRefused):
            P.check(self.root, PKG)

    def test_the_builders_client_package_is_reserved(self):
        path = self.put(f'src/{PKG.replace(".", "/")}/rose/Api.java', f'package {PKG}.rose;\nclass Api {{}}\n')
        self.refused('reserved_path')
        path.unlink()
        path.parent.rmdir()
        self.put(f'src/{PKG.replace(".", "/")}/Other.java', f'/* elsewhere */ package {PKG}.rose.api;\nclass O {{}}\n')
        self.refused('reserved_path')

    def test_internet_is_needed_to_reach_the_server(self):
        manifest = self.root / 'AndroidManifest.xml'
        manifest.write_text(manifest.read_text().replace(INTERNET, '  <application'))
        self.refused('missing_internet')

    def test_server_source_is_scanned(self):
        self.put('server/src/handlers/mod.rs', 'pub struct App;\nconst K: &str = include_str!("/etc/hostname");\n')
        error = self.refused('forbidden_construct')
        self.assertIn('line 2', str(error))
        self.put('server/src/handlers/mod.rs', 'pub struct App; /* unterminated')
        self.refused('source_unreadable')
        (self.root / P.HANDLERS).write_bytes(b'\xff')
        self.refused('source_unreadable')


class RustScan(unittest.TestCase):
    REFUSED = [
        'include_str!("/etc/passwd")', 'std::include_bytes!("x")', '::core::include!("x")', 'r#include_str!("x")',
        'env!("HOME")', 'option_env!("X")', 'core::env!("X")', 'use std::include_str as s;',
        'use std::env as e;', 'use std::{include_bytes};', 'use std::env::{self as e};',
        'fn include() {}',  # disclosed: the name itself is refused
        '#[path = "/etc/x.rs"] mod m;', '#[cfg_attr(unix, path = "x.rs")] mod m;',
        '#[cfg_attr(a, cfg_attr(b, path = "x.rs"))] mod m;', '#[link(name = "z")] extern "C" {}',
        '#![cfg_attr(unix, link(name = "z"))]', 'global_asm!(".incbin \\"/etc/passwd\\"")',
        'core::arch::asm!("nop")', 'naked_asm!("ret")',
        'macro_rules! m { ($i:ident) => { $i!("x") } } m!(include_str);',
    ]
    ALLOWED = [
        'std::env::var("X")', 'use std::env;', 'let a = env::args();', 'use std::env::{var, args};',
        'let path = 1; let link = 2;', '#[derive(Debug)] struct S;', '#![allow(dead_code)]', 'extern crate core;',
        'unsafe {}', '#[no_mangle] pub extern "C" fn f() {}', '#[serde(path = "x", link = "y")] struct S;',
        '// include_str!("x")', '/* /* env!() */ include!() */', 'let s = "include_str!(\\"x\\")";',
        'let s = r##"env!("X") "# ok"##;', 'let b = b"include!";', 'let c = c"asm!";', "'a: loop { break 'a; }",
        "fn f<'a>(x: &'a str) -> &'a str { x }", "let c = '!'; let q = '\\''; let b = b'\"';",
        'break; crate::x(); let brand = 1; let cr = 2;', 'let x = 1..2; let y = 1.5e3; let z = 0x1F_u8;',
        'let e = my.env; let s = std::fs::read("data");',
    ]

    def test_host_reads_and_native_code_are_refused(self):
        for text in self.REFUSED:
            with self.subTest(text):
                self.assertTrue(P.rust_refusals(text), text)

    def test_ordinary_rust_is_allowed(self):
        for text in self.ALLOWED:
            with self.subTest(text):
                self.assertEqual(P.rust_refusals(text), [], text)

    def test_hits_name_their_line(self):
        [hit] = P.rust_refusals('fn a() {}\n/* a\nb */\nconst S: &str = "x\ny";\nfn b() { env!("X"); }')
        self.assertIn('line 6', hit)  # the comment and the string each span a line break

    def test_unterminated_code_is_unreadable(self):
        for text in ('/* a /* b */', '"open', "r#\"open\"", "let c = '"):
            with self.subTest(text):
                with self.assertRaises(SyntaxError):
                    P.rust_refusals(text)


if __name__ == '__main__':
    unittest.main()
