"""The Codex writer's fixed policy (workspace/rose-agent/server/app-build.mjs) against the builder it describes: every
rule and name its client-server section gives is the one the builder checks or its generated code has, read here
from the builder itself, so the two cannot drift apart unnoticed (docs/APP-BUILDER.md §7.6)."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from rose.app_builder import appprofile, codegen, contract, generator, migrations, server_build, service, template

REPO = Path(__file__).resolve().parents[4]
POLICY_MODULE = REPO / 'workspace/rose-agent/server/app-build.mjs'
ADAPTER = REPO / 'workspace/rose-app-runtime-signin/src'
RUNTIME = REPO / 'workspace/rose-app-runtime/src'
NODE = shutil.which('node')

# A contract using each shape the policy names a mapping for.
SAMPLE = {'schemaVersion': 1, 'api': 1,
          'types': {'Note': {'fields': {'noteId': 'int', 'text': {'type': 'string', 'max': 100},
                                        'parent': {'type': 'Note', 'optional': True},
                                        'tags': {'type': 'list', 'of': 'string'},
                                        'type': {'type': 'string', 'optional': True}}},
                    'NoteList': {'fields': {'notes': {'type': 'list', 'of': 'Note'}}},
                    'Done': {'fields': {}}},
          'routes': {'listNotes': {'method': 'GET', 'path': '/app/notes', 'response': 'NoteList'},
                     'addNote': {'method': 'POST', 'path': '/app/notes', 'request': 'Note', 'response': 'Note'},
                     'ping': {'method': 'GET', 'path': '/app/ping', 'auth': 'public', 'response': 'Done'}}}


def flat(text: str) -> str:
    return re.sub(r'\s+', ' ', text)


@unittest.skipUnless(NODE, 'needs node to read the policy module')
class WriterPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, '--input-type=module', '-e',
                              f'import {{appBuildPolicy}} from {json.dumps(POLICY_MODULE.as_uri())};'
                              'process.stdout.write(appBuildPolicy)'], capture_output=True, text=True, check=True)
        cls.policy = flat(out.stdout)
        api = contract.parse(SAMPLE)
        cls.server = flat('\n'.join(codegen.server_files(api).values()))
        cls.client = flat('\n'.join(codegen.client_files(api, 'p.q').values()))

    def says(self, *phrases):
        for phrase in phrases:
            self.assertIn(flat(phrase), self.policy)

    def test_it_is_offered_only_when_the_turn_says_so(self):
        self.says('A client-server app, only when this turn says the builder builds them')
        self.assertIn('This builder builds client-server apps', generator.prompt('create', 'x', client_server=True))
        for kind in ('create', 'revise'):
            self.assertNotIn('client-server', generator.prompt(kind, 'x'))  # a host without the parts says nothing

    def test_the_layout_is_the_profiles(self):
        self.assertEqual(appprofile.SERVER_TOP, {'rose-app.json', 'contract', 'server'})
        self.says('also writes rose-app.json, contract/ and server/', '"profile": "client-server-v1"',
                  '"admission" is "invite" or "open"', 'server/src/handlers/mod.rs', 'server/src holds only .rs files',
                  f'files up to {template.MAX_FILE // (1024 * 1024)} MB')
        self.assertEqual(str(appprofile.HANDLERS), 'server/src/handlers/mod.rs')
        self.assertEqual(set(appprofile.ADMISSIONS), {'invite', 'open'})
        self.assertTrue(appprofile.CRATE.fullmatch('serde@1.0.219'))
        self.assertIsNone(appprofile.CRATE.fullmatch('serde:1.0.219'))

    def test_the_contract_rules_are_the_contracts(self):
        self.assertEqual(contract.METHODS, ('GET', 'POST', 'PUT', 'DELETE'))
        self.assertEqual(contract.BODYLESS, ('GET', 'DELETE'))
        self.assertEqual(contract.BOUNDED, ('string', 'bytes', 'list', 'map'))
        self.assertEqual(contract.AUTH, ('account', 'public'))
        self.says('method GET, POST, PUT or DELETE', 'GET and DELETE take no request', '"account" (signed in, the default)',
                  'max (string characters, bytes, list or map entries)', "1 to 8 segments of a-z, 0-9 and '-'")
        for path, ok in (('/app/notes', True), ('/app/a-b/c9', True), ('/app/a_b', False), ('/app/A', False),
                         ('/app/' + '/'.join('x' * 9), False)):
            self.assertEqual(bool(contract.PATH.fullmatch(path)), ok, path)

    def test_names_map_as_it_says(self):
        self.says("each capital letter turned into '_' and its lower case (listNotes is list_notes, noteId is "
                  "note_id)", 'a Rust keyword gets r# (r#type)', 'self, super, crate and Self get a trailing _')
        self.assertEqual((codegen.snake('listNotes'), codegen.snake('noteId'), codegen.snake('noteID')),
                         ('list_notes', 'note_id', 'note_i_d'))
        self.assertEqual(codegen.rust_ident('type'), 'r#type')
        for name in ('self', 'super', 'crate', 'Self'):
            self.assertEqual(codegen.rust_ident(name), name + '_')
        self.assertIn('fn list_notes(ctx: &::rose_app_runtime::server::Ctx)', self.server)
        self.assertIn('fn add_note(ctx: &::rose_app_runtime::server::Ctx, request: crate::api::Note)', self.server)
        self.assertIn('pub note_id: i64', self.server)
        self.assertIn('pub r#type: ::std::option::Option<::std::string::String>', self.server)
        # An optional field of a contract type is boxed, as the policy says.
        self.says('Option<Box<T>> for a contract type')
        self.assertIn('pub parent: ::std::option::Option<::std::boxed::Box<crate::api::Note>>', self.server)

    def test_the_refused_names_are_the_scans(self):
        names = sorted([*appprofile.HOST_READS, *appprofile.NATIVE_MACROS])
        listed = re.search(r'The names (.*?) are refused anywhere in server/src', self.policy).group(1)
        self.assertEqual(sorted(re.split(r', | and ', listed)), names)
        # The same names as contract members: each maps to one of them.
        members = re.search(r'are none of (.*?)\. A field is', self.policy).group(1)
        self.assertEqual(sorted(codegen.snake(m) for m in re.split(r', | or ', members)), names)

    def test_the_migration_rules_are_the_sandboxs(self):
        listed = re.search(r'The PRAGMAs a migration may use are (.*?)\. A new migration', self.policy).group(1)
        self.assertEqual(set(re.split(r', | and ', listed)), migrations.PRAGMAS)
        self.says('names of a-z, 0-9 and \'_\'', 'numbered with no gap',
                  'all in one transaction with foreign keys off; then every reference is checked',
                  'ON DELETE actions do not fire there: delete child rows yourself', 'ON UPDATE does not act either')
        self.assertTrue(migrations.NAME.fullmatch('0001_add_notes.sql'))
        for bad in ('0001_add-notes.sql', '0001_Notes.sql', '1_notes.sql'):
            self.assertIsNone(migrations.NAME.fullmatch(bad), bad)
        # The builder's sandbox and the adapter both apply pending migrations that way (SQLite's procedure).
        import inspect
        sandbox = inspect.getsource(migrations._Sandbox.apply)
        data = (ADAPTER / 'data.rs').read_text()
        for source in (sandbox, data):
            self.assertRegex(source, r'foreign_keys\s*=\s*OFF')
            self.assertIn('foreign_key_check', source)
            self.assertIn('BEGIN IMMEDIATE', source)

    def test_handlers_reach_data_as_it_says(self):
        self.says('ctx.data.downcast_ref::<rose_app_runtime_signin::AppData>()', '.account(principal)', '.shared()',
                  'rose_app_runtime_signin::rusqlite::Connection', 'rose_app_runtime_signin::rusqlite::params',
                  'crate::assets::get("data/x.json") gives an Option<&\'static [u8]>')
        lib, data = (ADAPTER / 'lib.rs').read_text(), (ADAPTER / 'data.rs').read_text()
        self.assertIn('pub use rusqlite;', lib)
        self.assertRegex(lib, r'pub use data::\{[^}]*AppData')
        self.assertRegex(data, r'pub fn account\(&self, principal: &Principal\) -> Result<Connection, DataError>')
        self.assertRegex(data, r'pub fn shared\(&self\) -> Result<Connection, DataError>')
        self.assertIn("pub fn get(path: &str) -> Option<&'static [u8]>", server_build.assets_rs(Path('/nonexistent')))
        self.assertIn('pub principal: Option<&', (RUNTIME / 'server.rs').read_text())

    def test_refusals_are_the_runtimes_and_clients(self):
        self.says("a 4xx status and a word of a-z and '_' starting with a letter, at most 64 characters")
        self.assertIn('pub const fn new(status: u16, word: &\'static str)', self.server)
        self.assertIn("b.is_ascii_lowercase() || b == b'_'", (RUNTIME / 'server.rs').read_text())
        self.assertIn('matches("[a-z][a-z_]{0,63}")', self.client)

    def test_the_client_api_is_the_generated_one(self):
        self.says('RoseApi.serverUrl()', 'new RoseApi.Client(new RoseApi.HttpsTransport(url), new RoseSession(context))',
                  'new RoseSignin(transport, session)', 'enroll(account, passphrase, invite, null)',
                  'confirm(account, enrollment.handle, code)', 'signIn(account, code, passphrase)',
                  'RoseSignin.passphraseProblem(passphrase)', 'signOut(false)',
                  'RoseSession.openAuthenticator(activity, uri)', 'RoseSession.qr(uri, 8)',
                  '401 signed_out or session_invalid',
                  'int is long, float double, bytes byte[]')
        for generated in ('public static String serverUrl()', 'public HttpsTransport(String baseUrl)',
                          'public Client(Transport transport, Tokens tokens)', 'public RoseSession(android.content.Context',
                          'public RoseSignin(RoseApi.Transport transport, RoseApi.Tokens tokens)',
                          'public Enrollment enroll(String account, String passphrase, String invite, String handle)',
                          'public void confirm(String account, String handle, String code)',
                          'public Session signIn(String account, String code, String passphrase)',
                          'public static String passphraseProblem(String passphrase)', 'public void signOut(boolean',
                          'static boolean openAuthenticator(android.app.Activity', 'otpauthUri', 'recoveryCodes',
                          'public static android.graphics.Bitmap qr(String otpauthUri, int pixelsPerModule)',
                          '"signed_out"', 'public p.q.rose.RoseTypes.NoteList listNotes() throws Error', 'public long noteId;'):
            self.assertIn(generated, self.client)
        self.assertIn('static final String URL = null;', service.rose_server_java('p.q', None))

    def test_revisions_are_the_checks(self):
        def check(old, new):
            return contract.check_update(contract.parse(old), contract.parse(new))
        def edited(change):
            c = json.loads(json.dumps(SAMPLE))
            change(c)
            return c
        self.says('no new or newly required field in any type a request carries',
                  'renamed routes and types, routes made public', 'raise "api" by one')
        # Allowed at the same api, as the policy lists them.
        for change in (lambda c: c['routes'].update(rename=c['routes'].pop('ping')),
                       lambda c: c['routes']['listNotes'].update(auth='public'),
                       lambda c: c['types']['Note']['fields'].pop('type'),  # an optional response field dropped
                       lambda c: c['types']['NoteList']['fields'].update(more='int')):  # in no request
            check(SAMPLE, edited(change))
        # Breaking at the same api, as the policy lists them.
        for change in (lambda c: c['types']['Note']['fields'].update(color='string'),  # Note is a request too
                       lambda c: c['routes'].pop('listNotes'),
                       lambda c: c['types']['Note']['fields'].update(noteId='string')):
            with self.assertRaises(contract.ContractRefused):
                check(SAMPLE, edited(change))

    def test_the_server_is_said_not_to_be_hosted(self):
        self.says('The server is not hosted yet: RoseApi.serverUrl() is null in every build today',
                  'never say the server runs, is reachable or syncs')

    def test_it_sets_no_rule_on_what_an_app_does(self):
        # The owner's rule: the policy describes shapes and isolation, never what an app may store or do.
        for limit in (r'\bnever store\b', r'\bmust not (store|collect|show)\b', r'\bonly (for|if) (the owner|allowed)\b',
                      r'\bnot allowed to (build|make)\b'):
            self.assertIsNone(re.search(limit, self.policy, re.I), limit)


if __name__ == '__main__':
    unittest.main()
