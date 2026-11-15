"""Level 2 codegen (docs/APP-BUILDER.md §7.3–7.5, slice 8): the server's Rust handlers and routes and the client's
Java API and sign-in overlay, from one contract.

The generated Rust is compiled and run against App Core's runtime crate (workspace/rose-app-runtime) with the
machine's own cargo, offline. The generated Java is compiled with javac and run against a fake transport, and the
Android parts against the SDK's android.jar. A cross-language check sends a Java-built request through the generated
Rust handlers and reads the reply in Java. ROSE_REQUIRE_CARGO and ROSE_REQUIRE_APP_BUILD make a missing tool a failure
rather than a skip (CI sets both). Every contract here is the test's own; no URL, key or account is real."""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from rose.app_builder import codegen as G
from rose.app_builder import contract as C
from rose.app_builder import server_build as S

REPO = Path(__file__).resolve().parents[4]
RUNTIME = Path(os.environ.get('ROSE_APP_RUNTIME', REPO / 'workspace/rose-app-runtime'))
PACKAGE = 'com.example.notes'

CONTRACT = {
    'schemaVersion': 1, 'api': 1,
    'types': {
        'Note': {'fields': {
            'id': 'int', 'text': {'type': 'string', 'max': 10}, 'score': 'float',
            'tags': {'type': 'list', 'of': 'string', 'max': 3},
            'meta': {'type': 'map', 'of': {'type': 'list', 'of': 'int'}},
            'blob': {'type': 'bytes', 'optional': True, 'max': 8},
            'parent': {'type': 'Note', 'optional': True},
            'type': {'type': 'string', 'optional': True},
            'done': {'type': 'bool', 'optional': True}}},
        'NoteList': {'fields': {'notes': {'type': 'list', 'of': 'Note'}}},
        'Self': {'fields': {'self': 'bool', 'class': {'type': 'int', 'optional': True}}},
        'RoseTypes': {'fields': {}},
    },
    'routes': {
        'listNotes': {'method': 'GET', 'path': '/app/notes', 'response': 'NoteList'},
        'addNote': {'method': 'POST', 'path': '/app/notes', 'request': 'Note', 'response': 'Note'},
        'about': {'method': 'GET', 'path': '/app/about', 'auth': 'public', 'response': 'RoseTypes'},
        'self': {'method': 'PUT', 'path': '/app/self', 'request': 'Self', 'response': 'Self'},
        'toString': {'method': 'DELETE', 'path': '/app/all', 'response': 'RoseTypes'},
    },
}


def require(env: str, tool: str):
    if not shutil.which(tool):
        if os.environ.get(env):
            raise AssertionError(f'{tool} is required ({env} is set)')
        raise unittest.SkipTest(f'{tool} is not installed')


def android_jar() -> Path | None:
    for root in (os.environ.get('ANDROID_SDK_ROOT'), REPO / '.tools/android-sdk', '/opt/roseos/android-sdk'):
        if root and (Path(root) / 'platforms/android-35/android.jar').is_file():
            return Path(root) / 'platforms/android-35/android.jar'
    return None


def write_tree(root: Path, files: dict[str, str]):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')


class Server(unittest.TestCase):
    def setUp(self):
        self.contract = C.parse(copy.deepcopy(CONTRACT))
        self.files = G.server_files(self.contract)

    def test_files_are_the_builders_generated_modules(self):
        self.assertEqual(sorted(self.files), ['rose/api.rs', 'rose/handlers.rs', 'rose/routes.rs', 'rose/wire.rs'])
        self.assertTrue(all(S.GENERATED.fullmatch(k) for k in self.files))
        # The crate root binds OS Core's authenticator and the host's identity: not generated until that is agreed.
        self.assertNotIn(S.ENTRY, self.files)

    def test_same_contract_same_bytes(self):
        reordered = json.loads(json.dumps(CONTRACT), object_pairs_hook=lambda kv: dict(reversed(kv)))
        self.assertEqual(G.server_files(C.parse(reordered)), self.files)
        self.assertIn(self.contract.sha256, self.files['rose/api.rs'])

    def test_names_are_escaped(self):
        api, handlers = self.files['rose/api.rs'], self.files['rose/handlers.rs']
        self.assertIn('pub struct Self_ {', api)
        self.assertIn('pub self_: bool,', api)
        self.assertIn('pub r#type: ::std::option::Option<::std::string::String>,', api)
        self.assertIn('pub parent: ::std::option::Option<::std::boxed::Box<crate::api::Note>>,', api)
        self.assertIn('fn to_string(ctx: &Ctx)', handlers)
        self.assertIn('fn self_(ctx: &Ctx, request: crate::api::Self_)', handlers)

    def test_compiles_and_behaves_against_the_runtime(self):
        require('ROSE_REQUIRE_CARGO', 'cargo')
        if not (RUNTIME / 'Cargo.toml').is_file():
            if os.environ.get('ROSE_REQUIRE_CARGO'):
                self.fail(f'the runtime crate is missing at {RUNTIME}')
            self.skipTest('the runtime crate is not in this checkout')
        with tempfile.TemporaryDirectory() as tmp:
            crate = Path(tmp)
            write_tree(crate, self.files)
            write_tree(crate, {'Cargo.toml': RUST_CARGO.replace('@RUNTIME@', json.dumps(str(RUNTIME))),
                               'rose/main.rs': RUST_HARNESS})
            env = {**os.environ, 'CARGO_TARGET_DIR': str(crate / 'target'), 'CARGO_NET_OFFLINE': 'true'}
            built = subprocess.run(['cargo', 'build', '--offline', '--quiet'], cwd=crate, env=env,
                                   capture_output=True, text=True, timeout=600)
            self.assertEqual(built.returncode, 0, built.stderr[-4000:])
            self.assertNotIn('warning', built.stderr, built.stderr[-4000:])
            run = subprocess.run([str(crate / 'target/debug/codegen-check'), 'checks'], capture_output=True,
                                 text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout[-2000:] + run.stderr[-4000:])
            self.assertIn('server checks passed', run.stdout)


class Client(unittest.TestCase):
    def setUp(self):
        self.contract = C.parse(copy.deepcopy(CONTRACT))
        self.files = G.client_files(self.contract, PACKAGE)
        self.base = 'src/com/example/notes/rose/'

    def test_files_are_in_the_reserved_folder(self):
        self.assertEqual(sorted(self.files), [self.base + n for n in (
            'RoseApi.java', 'RoseJson.java', 'RoseSession.java', 'RoseSignin.java', 'RoseSigninCore.java',
            'RoseTypes.java')])
        self.assertTrue(all(f'package {PACKAGE}.rose;' in v for k, v in self.files.items()
                            if not k.endswith('RoseSigninCore.java')))

    def test_signin_core_is_byte_for_byte(self):
        core = self.files[self.base + 'RoseSigninCore.java'].encode('utf-8')
        self.assertEqual(hashlib.sha256(core).hexdigest(), G.SIGNIN_CORE_SHA256)
        self.assertEqual(core, (REPO / 'workspace/rose-signin/java/src/dev/pixel/rosesignin/RoseSigninCore.java').read_bytes())

    def test_packages_are_checked(self):
        for bad in ('notes', 'com.Example.notes', 'com.example.class', '', 'com..x', None):
            with self.assertRaises(ValueError, msg=bad):
                G.client_files(self.contract, bad)

    def test_no_url_is_invented(self):
        text = '\n'.join(v for k, v in self.files.items() if not k.endswith('RoseSigninCore.java'))
        self.assertNotIn('https://', text)
        self.assertNotIn('http://', text)

    def test_same_contract_same_bytes(self):
        reordered = json.loads(json.dumps(CONTRACT), object_pairs_hook=lambda kv: dict(reversed(kv)))
        self.assertEqual(G.client_files(C.parse(reordered), PACKAGE), self.files)

    def compile(self, tmp: Path, extra: dict[str, str], android: bool) -> Path:
        write_tree(tmp, self.files)
        write_tree(tmp, extra)
        sources = [str(p) for p in tmp.rglob('*.java') if android or p.name != 'RoseSession.java']
        out = tmp / 'classes'
        cmd = ['javac', '--release', '17', '-Xlint:all', '-Werror', '-d', str(out)]
        if android:
            cmd = ['javac', '--release', '17', '-d', str(out), '-cp', str(android_jar())]
        done = subprocess.run(cmd + sources, capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return out

    def test_compiles_and_behaves_on_the_jvm(self):
        require('ROSE_REQUIRE_APP_BUILD', 'javac')
        with tempfile.TemporaryDirectory() as tmp:
            out = self.compile(Path(tmp), {'test/Check.java': JAVA_HARNESS}, android=False)
            run = subprocess.run(['java', '-cp', str(out), 'Check', 'checks'], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('client checks passed', run.stdout)

    def test_android_parts_compile_against_the_platform(self):
        require('ROSE_REQUIRE_APP_BUILD', 'javac')
        if android_jar() is None:
            if os.environ.get('ROSE_REQUIRE_APP_BUILD'):
                self.fail('android-35 android.jar is required')
            self.skipTest('no Android SDK')
        with tempfile.TemporaryDirectory() as tmp:
            self.compile(Path(tmp), {}, android=True)


class Parity(unittest.TestCase):
    """A request the Java client builds is parsed by the generated Rust handler, whose reply the Java client reads."""

    def test_java_request_through_rust_and_back(self):
        require('ROSE_REQUIRE_CARGO', 'cargo')
        require('ROSE_REQUIRE_APP_BUILD', 'javac')
        if not (RUNTIME / 'Cargo.toml').is_file():
            if os.environ.get('ROSE_REQUIRE_CARGO'):
                self.fail(f'the runtime crate is missing at {RUNTIME}')
            self.skipTest('the runtime crate is not in this checkout')
        contract = C.parse(copy.deepcopy(CONTRACT))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crate, java = root / 'server', root / 'client'
            write_tree(crate, G.server_files(contract))
            write_tree(crate, {'Cargo.toml': RUST_CARGO.replace('@RUNTIME@', json.dumps(str(RUNTIME))),
                               'rose/main.rs': RUST_HARNESS})
            env = {**os.environ, 'CARGO_TARGET_DIR': str(crate / 'target'), 'CARGO_NET_OFFLINE': 'true'}
            built = subprocess.run(['cargo', 'build', '--offline', '--quiet'], cwd=crate, env=env,
                                   capture_output=True, text=True, timeout=600)
            self.assertEqual(built.returncode, 0, built.stderr[-4000:])
            write_tree(java, G.client_files(contract, PACKAGE))
            write_tree(java, {'test/Check.java': JAVA_HARNESS})
            out = java / 'classes'
            javac = subprocess.run(['javac', '--release', '17', '-d', str(out)] +
                                   [str(p) for p in java.rglob('*.java') if p.name != 'RoseSession.java'],
                                   capture_output=True, text=True, timeout=300)
            self.assertEqual(javac.returncode, 0, javac.stderr)
            request = subprocess.run(['java', '-cp', str(out), 'Check', 'request'], capture_output=True, text=True,
                                     timeout=60, check=True).stdout
            reply = subprocess.run([str(crate / 'target/debug/codegen-check'), 'echo'], input=request,
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(reply.returncode, 0, reply.stderr)
            read = subprocess.run(['java', '-cp', str(out), 'Check', 'reply'], input=reply.stdout,
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(read.returncode, 0, read.stdout + read.stderr)
            self.assertEqual(read.stdout.strip(), 'note 7 tést 2 [a, b] {k=[1, 2]} 3 parent 8 x')


RUST_CARGO = '''[package]
name = "codegen-check"
version = "0.0.0"
edition = "2021"
publish = false

[[bin]]
name = "codegen-check"
path = "rose/main.rs"

[dependencies]
rose-app-runtime = { path = @RUNTIME@ }

[workspace]
'''

RUST_HARNESS = textwrap.dedent('''\
    mod api;
    mod handlers;
    mod routes;
    mod wire;

    use rose_app_runtime::json::{self, Value};
    use rose_app_runtime::server::{Ctx, Reply, RouteAuth};

    struct App;

    impl handlers::Handlers for App {
        fn add_note(_ctx: &Ctx, r: api::Note) -> Result<api::Note, handlers::Refusal> {
            if r.id < 0 {
                Err(handlers::Refusal::new(409, "negative"))
            } else {
                Ok(r)
            }
        }
        fn list_notes(_ctx: &Ctx) -> Result<api::NoteList, handlers::Refusal> {
            Ok(api::NoteList { notes: vec![] })
        }
        fn about(_ctx: &Ctx) -> Result<api::RoseTypes, handlers::Refusal> {
            Ok(api::RoseTypes {})
        }
        fn self_(_ctx: &Ctx, r: api::Self_) -> Result<api::Self_, handlers::Refusal> {
            Ok(r)
        }
        fn to_string(_ctx: &Ctx) -> Result<api::RoseTypes, handlers::Refusal> {
            Ok(api::RoseTypes {})
        }
    }

    fn call(method: &str, path: &str, body: &str) -> Reply {
        let routes = routes::routes::<App>();
        let route = routes.iter().find(|r| r.method == method && r.path == path).expect("route");
        let value = if body.is_empty() { None } else { Some(json::parse(body.as_bytes()).expect("json")) };
        (route.handler)(&Ctx { principal: None, now: 0, data: &() }, value.as_ref())
    }

    fn checks() {
        let routes = routes::routes::<App>();
        assert_eq!(routes.len(), 5);
        let add = routes.iter().find(|r| r.path == "/app/notes" && r.method == "POST").unwrap();
        assert!(add.body && add.auth == RouteAuth::Account);
        let about = routes.iter().find(|r| r.path == "/app/about").unwrap();
        assert!(!about.body && about.auth == RouteAuth::Public);

        let ok = r#"{"id":1,"text":"hi","score":2,"tags":["a"],"meta":{"k":[1,2]},"blob":"aGk=","done":true,
                    "parent":{"id":2,"text":"p","score":1.5,"tags":[],"meta":{}},"unknown":{"x":[1]}}"#;
        match call("POST", "/app/notes", ok) {
            Reply::Ok(v) => {
                let back = api::Note::from_value(&v).expect("round trip");
                assert_eq!(back.blob.as_deref(), Some(&b"hi"[..]));
                assert_eq!(back.score, 2.0);
                assert_eq!(back.parent.expect("parent").id, 2);
                assert!(v.get("unknown").is_none() && v.get("type").is_none());
            }
            other => panic!("{:?}", other),
        }
        let refused = [
            r#"{"id":1,"text":"01234567890","score":1,"tags":[],"meta":{}}"#,
            r#"{"id":1,"text":"x","score":1,"tags":["a","b","c","d"],"meta":{}}"#,
            r#"{"id":1,"text":"x","score":1,"tags":[],"meta":{},"blob":"MDEyMzQ1Njc4"}"#,
            r#"{"id":1,"text":"x","score":1,"tags":[],"meta":{},"blob":"aGk"}"#,
            r#"{"id":1.5,"text":"x","score":1,"tags":[],"meta":{}}"#,
            r#"{"id":"1","text":"x","score":1,"tags":[],"meta":{}}"#,
            r#"{"text":"x","score":1,"tags":[],"meta":{}}"#,
            r#"{"id":null,"text":"x","score":1,"tags":[],"meta":{}}"#,
            r#"{"id":1,"text":"x","score":1,"tags":[null],"meta":{}}"#,
            r#"{"id":1,"text":"x","score":1,"tags":[],"meta":{"k":["1"]}}"#,
            r#"{"id":1,"text":"x","score":1,"tags":[],"meta":{},"parent":{"id":2}}"#,
            r#"[]"#,
            "",
        ];
        for body in refused {
            assert_eq!(call("POST", "/app/notes", body), Reply::Refused(400, "invalid_request"), "{}", body);
        }
        let negative = r#"{"id":-1,"text":"x","score":1,"tags":[],"meta":{}}"#;
        assert_eq!(call("POST", "/app/notes", negative), Reply::Refused(409, "negative"));
        assert_eq!(call("GET", "/app/notes", ""), Reply::Ok(Value::Obj(vec![("notes".into(), Value::Arr(vec![]))])));
        assert_eq!(call("PUT", "/app/self", r#"{"self":true,"class":3}"#),
                   Reply::Ok(Value::Obj(vec![("class".into(), Value::Int(3)), ("self".into(), Value::Bool(true))])));
        let b64 = [("", &b""[..]), ("Zg==", b"f"), ("Zm8=", b"fo"), ("Zm9v", b"foo"), ("/+8=", &[0xff, 0xef][..])];
        for (text, bytes) in b64 {
            assert_eq!(wire::base64_decode(text).as_deref(), Some(bytes), "{}", text);
            assert_eq!(wire::base64_encode(bytes), text);
        }
        for text in ["Zg=", "Zg==Zg==", "Zh==", "Z===", "Zm9v!", "Zm9-"] {
            assert_eq!(wire::base64_decode(text), None, "{}", text);
        }
        println!("server checks passed");
    }

    fn main() {
        match std::env::args().nth(1).as_deref() {
            Some("checks") => checks(),
            Some("echo") => {
                let mut body = String::new();
                std::io::Read::read_to_string(&mut std::io::stdin(), &mut body).unwrap();
                match call("POST", "/app/notes", body.trim()) {
                    Reply::Ok(v) => println!("{}", json::to_string(&v).unwrap()),
                    other => panic!("{:?}", other),
                }
            }
            _ => panic!("checks or echo"),
        }
    }
''')

JAVA_HARNESS = textwrap.dedent('''\
    import com.example.notes.rose.*;
    import java.nio.charset.StandardCharsets;
    import java.util.*;

    public final class Check {
        static int checks;
        static void expect(boolean ok, String what) { checks++; if (!ok) throw new AssertionError(what); }

        static final class Fake implements RoseApi.Transport {
            final List<String> sent = new ArrayList<>();
            final Deque<RoseApi.Response> replies = new ArrayDeque<>();
            boolean down;
            public RoseApi.Response send(String m, String p, String bearer, byte[] body) throws java.io.IOException {
                if (down) throw new java.io.IOException("down");
                sent.add(m + " " + p + " " + bearer + " " + (body == null ? "-" : new String(body, StandardCharsets.UTF_8)));
                return replies.removeFirst();
            }
            void reply(int status, String body) { replies.add(new RoseApi.Response(status, body.getBytes(StandardCharsets.UTF_8), status == 429 ? 30 : -1)); }
        }

        static final class Store implements RoseApi.Tokens {
            String token;
            public String bearer() { return token; }
            public void keep(String t) { token = t; }
            public void forget() { token = null; }
        }

        static RoseTypes.Note note() {
            RoseTypes.Note n = new RoseTypes.Note();
            n.id = 7; n.text = "tést"; n.score = 2; n.tags = Arrays.asList("a", "b");
            n.meta = new LinkedHashMap<>(); n.meta.put("k", Arrays.asList(1L, 2L));
            n.blob = new byte[] {1, 2, 3};
            RoseTypes.Note p = new RoseTypes.Note();
            p.id = 8; p.text = "x"; p.score = 0.5; p.tags = new ArrayList<>(); p.meta = new LinkedHashMap<>();
            n.parent = p;
            return n;
        }

        static String show(RoseTypes.Note n) {
            return "note " + n.id + " " + n.text + " " + (long) n.score + " " + n.tags + " " + n.meta + " "
                + n.blob.length + " parent " + n.parent.id + " " + n.parent.text;
        }

        static void fails(String word, Runnable r) {
            try { r.run(); expect(false, "expected " + word); }
            catch (RuntimeException e) {
                RoseApi.Error err = (RoseApi.Error) e.getCause();
                expect(err.word.equals(word), "expected " + word + ", got " + err.word);
            }
        }

        interface Call { void run() throws Exception; }
        static Runnable wrap(Call c) {
            return () -> { try { c.run(); } catch (RoseApi.Error e) { throw new RuntimeException(e); } catch (Exception e) { throw new AssertionError(e); } };
        }

        static void checks() throws Exception {
            Fake t = new Fake();
            Store s = new Store();
            RoseApi.Client c = new RoseApi.Client(t, s);

            // Account routes need a session; public ones do not.
            fails("signed_out", wrap(() -> c.listNotes()));
            t.reply(200, "{}");
            c.about();
            expect(t.sent.get(0).equals("GET /app/about null -"), t.sent.get(0));

            // A request goes out as the contract says; the reply is read, unknown fields ignored.
            s.token = "tok";
            t.reply(200, "{\\"id\\":7,\\"text\\":\\"t\\",\\"score\\":1,\\"tags\\":[],\\"meta\\":{},\\"later\\":[1]}");
            RoseTypes.Note r = c.addNote(note());
            expect(r.id == 7 && r.score == 1.0 && r.parent == null && r.blob == null && r.done == null, "reply read");
            String sent = t.sent.get(1);
            expect(sent.startsWith("POST /app/notes tok {\\"blob\\":\\"AQID\\",\\"id\\":7,\\"meta\\":{\\"k\\":[1,2]},"), sent);
            expect(sent.contains("\\"score\\":2.0,\\"tags\\":[\\"a\\",\\"b\\"],\\"text\\":\\"tést\\"}"), sent);
            expect(sent.contains("\\"blob\\":\\"AQID\\"") && !sent.contains("\\"type\\"") && !sent.contains("\\"done\\""), sent);

            // Bad replies are bad_response; refusals carry the server's word; a dead session is forgotten.
            t.reply(200, "{\\"notes\\":[{\\"id\\":\\"1\\"}]}");
            fails("bad_response", wrap(() -> c.listNotes()));
            t.reply(200, "{\\"notes\\":[] ,}");
            fails("bad_response", wrap(() -> c.listNotes()));
            t.reply(409, "{\\"error\\":\\"negative\\"}");
            fails("negative", wrap(() -> c.addNote(note())));
            t.reply(500, "oops");
            fails("http_500", wrap(() -> c.listNotes()));
            t.reply(401, "{\\"error\\":\\"session_invalid\\"}");
            fails("session_invalid", wrap(() -> c.listNotes()));
            expect(s.token == null, "a dead session is forgotten");
            t.down = true;
            s.token = "tok";
            fails("unreachable", wrap(() -> c.listNotes()));
            t.down = false;

            // Keyword names: the route named toString is toString_(), the field class is class_.
            t.reply(200, "{\\"self\\":true,\\"class\\":3}");
            RoseTypes.Self in = new RoseTypes.Self(); in.self = true; in.class_ = 3L;
            RoseTypes.Self out = c.self(in);
            expect(out.self && out.class_ == 3L, "keyword fields");
            t.reply(200, "{}");
            c.toString_();

            // Sign-in: enrollment, sign-in keeps only the token, sign-out forgets it.
            RoseSignin si = new RoseSignin(t, s);
            String uri = "otpauth://totp/Notes:ann?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=Notes";
            t.reply(200, "{\\"otpauth_uri\\":\\"" + uri + "\\",\\"handle\\":\\"h1\\",\\"recovery_codes\\":[\\"r1\\",\\"r2\\"]}");
            RoseSignin.Enrollment e = si.enroll("ann", "passphrase", "inv", null);
            expect(e.handle.equals("h1") && e.recoveryCodes.size() == 2 && e.otpauthUri.equals(uri), "enrollment");
            expect(t.sent.get(t.sent.size() - 1).endsWith("{\\"account\\":\\"ann\\",\\"passphrase\\":\\"passphrase\\",\\"invite\\":\\"inv\\"}"), "enroll body");
            t.reply(200, "{\\"otpauth_uri\\":\\"otpauth://totp/x?secret=AAAA\\",\\"handle\\":\\"h\\",\\"recovery_codes\\":[]}");
            fails("bad_response", wrap(() -> si.enroll("ann", "passphrase", null, null)));
            t.reply(204, "");
            si.confirm("ann", "h1", "123456");
            s.token = null;
            t.reply(200, "{\\"token\\":\\"T\\",\\"account\\":\\"ann\\"}");
            RoseSignin.Session ses = si.signIn("ann", "123456", "passphrase");
            expect("T".equals(s.token) && ses.account.equals("ann") && ses.recoveryCodes.isEmpty(), "signed in");
            t.reply(429, "{\\"error\\":\\"try_later\\"}");
            try { si.signIn("ann", "1", "p"); expect(false, "limited"); }
            catch (RoseApi.Error err) { expect(err.word.equals("try_later") && err.retryAfterSeconds == 30, "retry-after"); }
            t.reply(204, "");
            si.signOut(false);
            expect(s.token == null && t.sent.get(t.sent.size() - 1).equals("DELETE /signin/v1/session T -"), "signed out");
            expect(RoseSignin.passphraseProblem("short") != null && RoseSignin.passphraseProblem("long enough") == null, "passphrase check");

            // The transport takes only plain https base URLs.
            for (String bad : new String[] {"http://x", "https://u:p@x", "https://x/?q", "https://x/#f", "ftp://x"}) {
                try { new RoseApi.HttpsTransport(bad); expect(false, bad); } catch (IllegalArgumentException ok) { checks++; }
            }
            new RoseApi.HttpsTransport("https://app.example.invalid/");

            // Strict JSON.
            for (String bad : new String[] {"{\\"a\\":1,\\"a\\":2}", "[1,]", "01", "\\"\\\\ud800\\"", "{} x"}) {
                try { RoseJson.parse(bad); expect(false, bad); } catch (RoseJson.Bad ok) { checks++; }
            }
            System.out.println("client checks passed: " + checks);
        }

        public static void main(String[] args) throws Exception {
            switch (args[0]) {
                case "checks": checks(); break;
                case "request": System.out.println(RoseJson.write(note().toJson())); break;
                case "reply": {
                    String text = new String(System.in.readAllBytes(), StandardCharsets.UTF_8).trim();
                    System.out.println(show(RoseTypes.Note.fromJson(RoseJson.parse(text))));
                    break;
                }
                default: throw new IllegalArgumentException(args[0]);
            }
        }
    }
''')


if __name__ == '__main__':
    unittest.main()
