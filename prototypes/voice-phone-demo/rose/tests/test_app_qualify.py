"""Host qualification for client-server builds (rose.app_builder.qualify; docs/APP-BUILDER.md §5.2): each check
names what a host lacks, nothing weaker stands in for the builder's own isolation, and the built-in test app is
exactly what the real code generator expects. The real end-to-end run happens only when an owner points
ROSE_QUALIFY_CONFIG at a real builder config on a candidate host; nothing here installs, changes or contacts
anything."""
import copy
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from rose.app_builder import codegen, contract as C, qualify, template
from rose.app_builder import host
from rose.tests.test_app_builder import HAS_SDK, JAVA_HOME, SDK


def lines(out):
    return [json.loads(line) for line in out.getvalue().splitlines()]


class Unconfined:
    """An isolation that confines nothing: what qualification must refuse."""

    def command(self, argv, env, *, writable, readonly):
        return list(argv), {**os.environ, **env}


class Refusing:
    def command(self, argv, env, *, writable, readonly):
        return ['/bin/sh', '-c', 'echo "bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted" >&2; exit 1'], env


class Unsupported:
    """As the real BubblewrapIsolation refuses: a BuildFailed naming why."""

    def command(self, argv, env, *, writable, readonly):
        from rose.app_builder.server_build import BuildFailed
        raise BuildFailed('toolchain', 'build_isolation_unavailable', 'requires unprivileged Linux x86-64')


class Checks(unittest.TestCase):
    def test_a_config_without_client_server_is_named_and_the_rest_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'builder.json'
            path.write_text(json.dumps({'schemaVersion': 1, 'root': f'{tmp}/b', 'sdk': str(SDK)}))
            out = io.StringIO()
            self.assertFalse(qualify.qualify(path, out))
        got = lines(out)
        self.assertEqual([(r.get('check'), r.get('ok')) for r in got],
                         [('config', False), ('platform', got[1]['ok']), ('facilities', None),
                          ('confinement', None), ('build', None), (None, None)])
        self.assertIn('no clientServer', got[0]['detail'])
        self.assertEqual(got[-1], {'qualified': False})

    def test_an_unreadable_config_and_main_exit_codes(self):
        out = io.StringIO()
        self.assertEqual(qualify.main(['--config', '/nonexistent/builder.json'], out), 1)
        self.assertIn('could not be read', lines(out)[0]['detail'])
        with self.assertRaises(SystemExit) as cm:
            qualify.main([], io.StringIO())
        self.assertEqual(cm.exception.code, 2)

    def test_the_platform_is_linux_x86_64_and_not_root(self):
        self.assertIn('uid 1000', qualify.check_platform(lambda: 'Linux', lambda: 'x86_64', lambda: 1000))
        for system, machine, uid, why in (('Darwin', 'arm64', 501, 'Linux x86-64'),
                                          ('Linux', 'aarch64', 1000, 'Linux x86-64'),
                                          ('Linux', 'x86_64', 0, 'unprivileged')):
            with self.subTest(system=system, machine=machine, uid=uid), \
                    self.assertRaisesRegex(qualify.Failed, why):
                qualify.check_platform(lambda: system, lambda: machine, lambda: uid)

    def test_confinement_that_confines_nothing_is_refused(self):
        with self.assertRaisesRegex(qualify.Failed, 'incomplete') as cm:
            qualify.check_confinement({'server_isolation': Unconfined()})
        self.assertIn('etc visible', str(cm.exception))  # the host's own /etc is in reach without confinement
        self.assertIn('interfaces', str(cm.exception))  # and its network interfaces
        self.assertIn('readonly writable', str(cm.exception))

    def test_only_every_confined_finding_passes(self):
        self.assertEqual(qualify.CONFINED, {'interfaces': 'loopback only', 'network': 'none', 'etc': 'hidden',
                                            'home': 'hidden', 'readonly': 'read-only', 'job': 'writable'})
        qualify.judge(dict(qualify.CONFINED))
        for key, value in (('interfaces', 'eth0,lo'), ('network', 'reachable'), ('etc', 'visible'),
                           ('home', 'visible'), ('readonly', 'writable'), ('job', 'not writable')):
            with self.subTest(key=key), self.assertRaisesRegex(qualify.Failed, f'{key} {value}'):
                qualify.judge({**qualify.CONFINED, key: value})
        for key in qualify.CONFINED:
            with self.subTest(missing=key), self.assertRaisesRegex(qualify.Failed, f'{key} not checked'):
                qualify.judge({k: v for k, v in qualify.CONFINED.items() if k != key})

    def test_home_counts_as_visible_only_with_contents_beyond_bubblewraps_mount_points(self):
        ns = {}
        exec(qualify.EXPOSED, ns)
        exposed = ns['exposed']
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'
            job = home / 'tmp/probe/job'
            job.mkdir(parents=True)  # what bwrap creates on the way to a mount under home: only that path
            self.assertFalse(exposed(str(home), (str(job), str(home / 'tmp/probe/shown'))))
            self.assertFalse(exposed('/', ()))  # a home of '/' is the /etc and /root findings' to judge
            self.assertFalse(exposed(str(Path(tmp) / 'absent'), ()))
            (home / 'secret').write_text('x')
            self.assertTrue(exposed(str(home), (str(job),)))
            self.assertTrue(exposed(str(home), ()))

    def test_a_refused_or_unsupported_confinement_names_the_cause(self):
        with self.assertRaisesRegex(qualify.Failed, 'Operation not permitted'):
            qualify.check_confinement({'server_isolation': Refusing()})
        with self.assertRaisesRegex(qualify.Failed, 'cannot be set up here: requires unprivileged'):
            qualify.check_confinement({'server_isolation': Unsupported()})

    def test_every_check_passing_qualifies_and_a_failure_does_not(self):
        passing = {'config': lambda p: object(), 'platform': lambda: 'Linux x86_64, uid 1000',
                   'facilities': lambda c: {}, 'confinement': lambda f: 'confined', 'build': lambda c, f: 'built'}
        out = io.StringIO()
        self.assertTrue(qualify.qualify(Path('/x'), out, checks=passing))
        self.assertEqual([r.get('ok') for r in lines(out)], [True] * 5 + [None])
        self.assertEqual(lines(out)[-1], {'qualified': True})

        def fails(*_):
            raise qualify.Failed('no musl C compiler')
        out = io.StringIO()
        self.assertFalse(qualify.qualify(Path('/x'), out, checks={**passing, 'confinement': fails}))
        got = lines(out)
        self.assertEqual((got[3]['ok'], got[3]['detail'], got[4]['ok']), (False, 'no musl C compiler', None))


class BuiltInApp(unittest.TestCase):
    """The test app is one the real builder accepts: a valid contract, and handlers for exactly its trait."""

    def test_the_contract_parses_and_the_handlers_implement_its_trait(self):
        files = codegen.server_files(C.parse(copy.deepcopy(qualify.CONTRACT)))
        self.assertIn('fn list_notes(ctx: &::rose_app_runtime::server::Ctx) -> ::std::result::Result<crate::api::'
                      'NoteList, Refusal>;', files['rose/api.rs'])
        self.assertIn('routes::routes::<handlers::App>()', files['rose/main.rs'])
        self.assertIn('impl crate::api::Handlers for App', qualify.HANDLERS)
        self.assertIn('fn list_notes(_ctx: &Ctx) -> Result<crate::api::NoteList, Refusal>', qualify.HANDLERS)

    def test_the_writer_produces_a_client_server_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'src'
            template.write_skeleton(src, 'com.example.notes', 'Notes')
            qualify.generate(src, 'create', 'x', lambda: False)
            self.assertIn(qualify.INTERNET, (src / 'AndroidManifest.xml').read_text())
            self.assertEqual(json.loads((src / 'rose-app.json').read_text())['profile'], 'client-server-v1')
            self.assertEqual(json.loads((src / 'contract/api.json').read_text()), qualify.CONTRACT)
            self.assertEqual((src / 'server/migrations/account/0001_notes.sql').read_text(), qualify.MIGRATION)


@unittest.skipUnless(HAS_SDK, 'needs the pinned Android SDK and a JDK')
class BuildCheck(unittest.TestCase):
    def config(self, root, **extra):
        return host.parse_builder_config({'schemaVersion': 1, 'root': str(root), 'sdk': str(SDK),
                                          **({'javaHome': str(JAVA_HOME)} if JAVA_HOME else {}), **extra})

    def test_the_real_builder_without_server_facilities_is_a_named_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'builder'
            seen = []
            real = qualify.Builder

            def builder(job_root, *args, **kw):
                seen.append(Path(job_root))
                return real(job_root, *args, **kw)
            with unittest.mock.patch.object(qualify, 'Builder', builder), \
                    self.assertRaisesRegex(qualify.Failed, 'the test app did not build: ') as cm:
                qualify.check_build(self.config(root), {}, apart_from=(Path(tmp) / 'config',))
            self.assertNotIn('unknown', str(cm.exception))
            self.assertEqual(seen[0].parent.parent, root)  # where real jobs run: inside the configured root
            self.assertEqual(list(root.iterdir()), [])  # and nothing is left there

    def test_the_root_and_library_checks_of_build_builder_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'config'
            for config, why in ((self.config(cfg / 'builder'), 'separate'),
                                (self.config(Path(tmp) / 'b', libraryStore=f'{tmp}/missing'), 'libraryStore')):
                with self.subTest(why=why), self.assertRaisesRegex(qualify.Failed, why):
                    qualify.check_build(config, {}, apart_from=(cfg,))
            (Path(tmp) / 'real').mkdir()
            (Path(tmp) / 'link').symlink_to(Path(tmp) / 'real')
            with self.assertRaisesRegex(qualify.Failed, 'symbolic link'):
                qualify.check_build(self.config(Path(tmp) / 'link'), {}, apart_from=(cfg,))


@unittest.skipUnless(os.environ.get('ROSE_QUALIFY_CONFIG'), 'an owner runs this against a real builder config')
class RealHost(unittest.TestCase):
    def test_this_host_qualifies(self):
        out = io.StringIO()
        ok = qualify.qualify(Path(os.environ['ROSE_QUALIFY_CONFIG']), out)
        sys.stdout.write(out.getvalue())
        self.assertTrue(ok, out.getvalue())


if __name__ == '__main__':
    unittest.main()
