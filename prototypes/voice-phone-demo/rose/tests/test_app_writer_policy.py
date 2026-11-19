"""The Codex writer's fixed policy (workspace/rose-agent/server/app-build.mjs) against the builder it describes: every
rule and name its Gradle project and client-server sections give is the one the builder checks, its pinned stack
(gradle_stack) or skeleton has, or its generated code has, read here from the builder itself, so the two cannot drift
apart unnoticed (docs/APP-BUILDER.md §7.6)."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from rose.app_builder import appprofile, codegen, contract, generator, gradle, gradle_stack, service, template

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


    def test_the_gradle_project_is_the_skeletons_and_the_stacks(self):
        self.says('The app is an ordinary Android Gradle project', 'settings.gradle.kts, build.gradle.kts and '
                  'gradle.properties at the top', 'app/build.gradle.kts and app/src/main/ with AndroidManifest.xml, '
                  'java/ or kotlin/, res/ and assets/', 'Write Kotlin with Jetpack Compose, and Material 3 where it fits',
                  f'Keep compileSdk and targetSdk at the turn\'s compileSdk ({gradle_stack.COMPILE_SDK}); minSdk is at '
                  f'most {gradle_stack.MAX_MIN_SDK} (the skeleton\'s {gradle_stack.MIN_SDK} suits most apps)',
                  'Keep namespace and applicationId exactly as the skeleton has them',
                  'google() and mavenCentral(), and for plugins also gradlePluginPortal()',
                  f'Every file is at most {template.MAX_FILE // (1024 * 1024)} MB')
        self.assertEqual(gradle_stack.TARGET_SDK, gradle_stack.COMPILE_SDK)
        self.assertEqual(gradle_stack.APP_MODULE, 'app')
        package = 'app.rose.gen.p7'
        files = gradle.skeleton(package, 'Notes')
        for name in ('settings.gradle.kts', 'build.gradle.kts', 'gradle.properties', 'app/build.gradle.kts',
                     'app/src/main/AndroidManifest.xml', 'app/src/main/java/app/rose/gen/p7/MainActivity.kt'):
            self.assertIn(name, files)
        app = files['app/build.gradle.kts']
        for line in (f'namespace = "{package}"', f'applicationId = "{package}"', f'compileSdk = {gradle_stack.COMPILE_SDK}',
                     f'minSdk = {gradle_stack.MIN_SDK}', f'targetSdk = {gradle_stack.TARGET_SDK}'):
            self.assertIn(line, app)
        self.assertNotIn('signingConfig', app)
        self.assertNotIn('package=', files['app/src/main/AndroidManifest.xml'])  # the namespace is the package
        self.assertIn('google(); mavenCentral(); gradlePluginPortal()', files['settings.gradle.kts'])
        self.assertIn('repositories { google(); mavenCentral() }', files['settings.gradle.kts'])
        # The Android Gradle Plugin compiles Kotlin itself: the policy and the stack say so, and no plugin offers it.
        self.says('apply no Kotlin Android plugin')
        self.assertNotIn('org.jetbrains.kotlin.android', gradle_stack.PLUGINS)

    def test_the_gradle_refusals_are_the_checks(self):
        listed = re.search(r'prebuilt files \((.*?)\)', self.policy).group(1)
        self.assertEqual(set(re.split(r', | or ', listed)), {*gradle.LIBRARY_SUFFIXES, *gradle.BINARY_SUFFIXES})
        state = re.search(r'build output \((.*?)\)', self.policy).group(1)
        self.assertEqual(set(re.split(r', ', state)), {*(d + '/' for d in gradle.BUILD_STATE), '*.iml'})
        self.says('Never add the Gradle wrapper (gradlew, gradlew.bat, gradle/wrapper/)', 'key or keystore files',
                  'native code (C or C++, CMake or ndk-build: the stack has no NDK)')
        self.assertTrue(gradle.WRAPPER_JAR.startswith('gradle/wrapper/'))  # tolerated by the check, never written
        self.assertTrue({'.jks', '.keystore', '.p12'} <= set(gradle.KEY_SUFFIXES))
        self.assertTrue({'.c', '.cpp', '.h'} <= set(gradle.NATIVE_SUFFIXES))
        self.assertIn('cmakelists.txt', gradle.NATIVE_NAMES)

    def test_identity_is_the_hosts(self):
        self.says('The host owns the app\'s identity and signs the built APK itself, outside the build',
                  'Never set or change applicationId, applicationIdSuffix, versionCode, versionName, signingConfigs, '
                  'sharedUserId, splits or bundles, ABI filters (abiFilters) or native-library excludes, and never '
                  'make a build debuggable or testOnly',
                  'Android library modules, com.android.library; the application stays app/',
                  'set android { compileOptions { sourceCompatibility = JavaVersion.VERSION_17;',
                  'host owns the <package>.rose package: never create that folder or package',
                  f'it writes it as {appprofile.client_root(True)}<package path>/rose/')
        self.assertEqual(appprofile.client_root(True), 'app/src/main/java/')
        self.assertEqual(appprofile.client_root(False), 'src/')
        self.says('or src/<package>/rose/ in the older layout')

    def test_the_older_layout_keeps_its_rules_and_may_convert(self):
        self.says('An app in the older flat layout (AndroidManifest.xml, src/ and res/ at the top, and no '
                  'settings.gradle.kts) keeps that layout\'s rules and may stay that way',
                  'In a revise, when this turn names the Gradle stack, you may instead convert it into a Gradle project',
                  'set namespace and applicationId to its manifest package',
                  'Its package, the owner\'s data and its signing stay the same, so phones update it in place',
                  'The older layout\'s rules: write only AndroidManifest.xml, src/, res/, assets/, README.md and '
                  'dependencies.json', 'Java 17 source level', 'Resources follow aapt v1 rules')
        self.assertEqual(template.ALLOWED_TOP, {'AndroidManifest.xml', 'src', 'res', 'assets', 'README.md',
                                                'dependencies.json'})
        self.assertFalse(gradle.is_gradle_project(Path('/nonexistent')))

    def test_the_turn_names_the_stack(self):
        stack = gradle_stack.summary()
        self.says('exactly the stack this turn names', 'request no Java toolchain but the turn\'s JDK')
        for kind in ('create', 'revise', 'repair'):
            text = generator.prompt(kind, 'x', stack=stack)
            self.assertIn(stack, text)
            for named in (f'Gradle {gradle_stack.GRADLE}', f'compileSdk {gradle_stack.COMPILE_SDK}',
                          f'JDK {gradle_stack.JDK_FEATURE}', *gradle_stack.PLUGINS):
                self.assertIn(named, text)
            self.assertNotIn('Gradle', generator.prompt(kind, 'x'))  # a host without the stack names none

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
