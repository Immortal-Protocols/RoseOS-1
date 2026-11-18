"""The Codex writer's fixed policy (workspace/rose-agent/server/app-build.mjs) against the builder it describes: every
rule and name its client-server section gives is the one the builder checks or its generated code has, read here
from the builder itself, so the two cannot drift apart unnoticed (docs/APP-BUILDER.md §7.6)."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from rose.app_builder import appprofile, codegen, contract, generator, service, template

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



    def test_only_the_agent_path_is_offered(self):
        self.says('Python and other stacks require no Rust', 'A client-only app has no backend',
                  'existing Chariot agent container', 'backend/legacy', 'preserve every existing user')
        self.assertNotIn('server/src/handlers/mod.rs', self.policy)
        self.assertNotIn('client-server-v1', generator.prompt('create', 'x'))
        self.assertIn('agent_container', generator.prompt('create', 'x', backend_folder='/release'))

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
        self.says('when it is null, show that the server is not set up yet',
                  'Never say in your reply that the backend is deployed')

    def test_it_sets_no_rule_on_what_an_app_does(self):
        # The owner's rule: the policy describes shapes and isolation, never what an app may store or do.
        for limit in (r'\bnever store\b', r'\bmust not (store|collect|show)\b', r'\bonly (for|if) (the owner|allowed)\b',
                      r'\bnot allowed to (build|make)\b'):
            self.assertIsNone(re.search(limit, self.policy, re.I), limit)


if __name__ == '__main__':
    unittest.main()
