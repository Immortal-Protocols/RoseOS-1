"""The building agent's own backend (client-backend-v1; docs/APP-BUILDER.md §7.7a): the agent chooses the stack and
builds the release itself in the app's agent container, through the one ``agent_container`` tool the Builder
answers; the builder compiles nothing, switches to that release with the same backup, health and rollback, and
delivers the version with its release. Here the container is the local stand-in (LocalAgent) and the agent a fake
writer: no Chariot, SSH or model."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import app_host, generator, ledger as L, template
from rose.tests.test_app_generator import FakeClient
from rose.tests.test_app_host_agent import AGENT_HEX, APP_URL, FAKE_SERVER, LocalAgent, TOKEN, free_port

INTERNET = '<uses-permission android:name="android.permission.INTERNET"/>'
CONTRACT = {'schemaVersion': 1, 'api': 1, 'types': {'Note': {'fields': {'id': 'int', 'text': 'string'}}},
            'routes': {'listNotes': {'method': 'GET', 'path': '/app/notes', 'response': 'Note'}}}


def release_start(name: str) -> str:
    """An agent-built release's start: a Python backend in its own folder, by relative path."""
    return f'set -eu\nexport CHARIOT_REVISION_SHA256={name}\nexec {sys.executable} ./server.py\n'


class AgentBuiltRelease(unittest.TestCase):
    """The switch over a release the agent built in place: never moved, health only, rollback, no invites."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.agent = LocalAgent(self.root)
        self.addCleanup(self.agent.stop)
        self.port = free_port()

    def build(self, name, broken=False):
        """What the building agent does with its tool: write the release into releases/<id>, in place."""
        release = (name * 64)[:64] if all(c in '0123456789abcdef' for c in name) else os.urandom(32).hex()
        folder = self.agent.app / 'releases' / release
        folder.mkdir(parents=True)
        (folder / 'server.py').write_text(FAKE_SERVER.replace('#!@PYTHON@\n', '').replace(
            '@NAME@', 'broken' if broken else name))
        (folder / 'start').write_text(release_start(name))
        return app_host.Plan('p1', 1, AGENT_HEX, APP_URL, None, release, None, self.port)

    def deploy(self, p, **kw):
        return app_host.deploy(p, self.agent, enabled=True, bin_dir=str(self.agent.bin), stop_wait_s=5,
                               health_wait_s=kw.pop('health_wait_s', 20), generate_keyring=True, **kw)

    def get(self):
        import socket
        with socket.create_connection(('127.0.0.1', self.port), timeout=5) as s:
            s.sendall(b'GET /health HTTP/1.0\r\n\r\n')
            return s.makefile('rb').read().decode()

    def test_the_agents_release_runs_where_it_was_built_and_updates_safely(self):
        v1 = self.build('a1')
        self.assertEqual(self.deploy(v1)['deployed'], v1.sha256)
        app = self.agent.app
        self.assertEqual((app / 'current').read_text(), v1.sha256)
        self.assertTrue((app / 'releases' / v1.sha256 / 'server.py').is_file())  # in place: never moved
        self.assertIn('a1', self.get())
        self.assertIsNone(json.loads((app / 'deployed.json').read_text())['dataSchema'])
        broken = self.build('b2', broken=True)
        with self.assertRaisesRegex(app_host.SetupFailed, f'restored: {v1.sha256}'):
            self.deploy(broken, health_wait_s=3)
        self.assertIn('a1', self.get())  # rolled back to the agent's previous release, on its own data
        v2 = self.build('c3')
        self.assertEqual(self.deploy(v2)['previous'], v1.sha256)
        self.assertEqual(sorted(p.name for p in (app / 'releases').iterdir()), sorted([v1.sha256, v2.sha256]))
        with tempfile.TemporaryDirectory():
            with self.assertRaises(app_host.InviteRefused) as caught:  # its backend ships no invite
                app_host.invite(self.agent, v2.sha256, 'me', bin_dir=str(self.agent.bin))
        self.assertEqual(caught.exception.word, 'unsupported')

    def test_after_a_pod_replacement_chariots_own_hook_runs_the_current_release_again(self):
        """Chariot reruns its launcher (the postStart hook) against .chariot-app/start after a pod replacement or
        restart, with the PVC kept: the shim, current, the releases and the data are all on it, so the release that
        was current serves again on its data, with no setup and nothing from the builder."""
        v1 = self.build('a1')
        self.deploy(v1)
        v2 = self.build('c3')
        self.deploy(v2)
        self.agent.stop()  # the pod goes away: the process dies, the PVC stays
        import time
        time.sleep(0.5)
        launcher = app_host.LAUNCHER.replace('/chariot/bin', str(self.agent.bin))
        self.assertEqual(self.agent.script(launcher).code, 0)  # the hook, exactly Chariot's launcher
        for _ in range(100):
            try:
                reply = self.get()
                break
            except OSError:
                time.sleep(0.1)
        self.assertIn('c3', reply)  # the current release, not the previous one
        self.assertEqual((self.agent.app / 'data/accounts/a.sqlite3').read_text(), 'a1\nc3\nc3\n')  # its data kept

    def test_a_release_without_its_start_touches_nothing(self):
        v1 = self.build('a1')
        self.deploy(v1)
        empty = app_host.Plan('p1', 2, AGENT_HEX, APP_URL, None, 'e' * 64, None, self.port)
        (self.agent.app / 'releases' / empty.sha256).mkdir()
        with self.assertRaisesRegex(app_host.SetupFailed, r'\(release\)'):
            self.deploy(empty)
        self.assertIn('a1', self.get())
        self.assertTrue((self.agent.app / 'app.json').is_file())

    def test_the_agents_invite_is_its_own_and_opaque(self):
        v1 = self.build('a1')
        self.deploy(v1)
        token = 'opaque-Token_123~!'
        (self.agent.app / 'releases' / v1.sha256 / 'invite').write_text(f'set -eu\necho "{token}"\n')
        self.assertEqual(app_host.invite(self.agent, v1.sha256, 'me', bin_dir=str(self.agent.bin)), token)
        (self.agent.app / 'releases' / v1.sha256 / 'invite').write_text('echo "two words"\n')
        with self.assertRaises(app_host.InviteRefused) as caught:
            app_host.invite(self.agent, v1.sha256, 'me', bin_dir=str(self.agent.bin))
        self.assertEqual(caught.exception.word, 'bad_output')


class WriterTool(unittest.TestCase):
    """The writer's one tool: answered from the job's own backend session, nothing else."""

    class Session:
        folder = '$HOME/workspace/.chariot-app/releases/' + 'f' * 64

        def __init__(self):
            self.calls = []

        def exec(self, command, stdin=b'', timeout_s=1800):
            self.calls.append((command, stdin, timeout_s))
            return app_host.Result(0, 'built\n', 'warn\n')

    def run_writer(self, events, backend):
        holder = {}

        class Client(FakeClient):
            def __init__(self, on_event):
                super().__init__(on_event, after_submit=events)
                self.sent = []
                holder['client'] = self

            def send(self, message):
                self.sent.append(message)

        gen = generator.CodexGenerator(connect=Client, backend=True, tool_timeout_s=600)
        with tempfile.TemporaryDirectory() as tmp:
            gen(Path(tmp), 'create', 'notes', lambda: False, backend=backend)
        return holder['client']

    def test_the_tool_runs_in_the_jobs_container_and_answers_the_writer(self):
        call = {'event': 'tool', 'callId': '7', 'params': {'tool': 'agent_container', 'callId': 'c1',
                                                           'arguments': {'command': 'echo hi', 'stdin': 'x'}}}
        wrong = {'event': 'tool', 'callId': '8', 'params': {'tool': 'other', 'arguments': {}}}
        done = ({'event': 'message', 'text': 'Notes.'}, {'event': 'done', 'turn': {'status': 'completed'}})
        session = self.Session()
        client = self.run_writer((call, wrong, *done), session)
        opened = next(p for op, _, p in client.calls if op == 'open')
        self.assertEqual((opened['containerTool'], opened['toolTimeoutS'], opened['tools']), (True, 600, []))
        submitted = next(p for op, _, p in client.calls if op == 'submit')
        self.assertIn(session.folder, submitted['text'])
        self.assertEqual(session.calls, [('echo hi', b'x', 600)])
        first, second = client.sent
        self.assertEqual((first['op'], first['callId'], first['result']['success']), ('tool_result', '7', True))
        self.assertIn('exit 0\n--- stdout ---\nbuilt', first['result']['contentItems'][0]['text'])
        self.assertEqual((second['callId'], second['result']['success']), ('8', False))

    def test_without_a_backend_session_the_writer_has_no_tool(self):
        done = ({'event': 'message', 'text': 'Notes.'}, {'event': 'done', 'turn': {'status': 'completed'}})
        client = self.run_writer(done, None)
        opened = next(p for op, _, p in client.calls if op == 'open')
        self.assertNotIn('containerTool', opened)


@unittest.skipUnless(os.environ.get('ROSE_REQUIRE_APP_BUILD') or __import__(
    'rose.tests.test_app_builder', fromlist=['HAS_SDK']).HAS_SDK, 'needs the pinned Android SDK and a JDK')
class InTheJob(unittest.TestCase):
    """A real Builder job for a client-backend-v1 app: the writer uses the tool, the builder compiles no server,
    switches to the agent's release before delivery, and records the release, the contract and the hosting."""

    class FakeHosting:
        def __init__(self, ledger, fail=None):
            self.ledger, self.fail, self.calls = ledger, fail, []

        def release_folder(self, release):
            return f'$HOME/workspace/.chariot-app/releases/{release}'

        def ensure(self, project):
            self.calls.append(('ensure', project))
            if self.ledger.agent_container(project) is None:
                self.ledger.record_agent_container(project, AGENT_HEX, APP_URL)

        def prepare(self, project, release):
            self.calls.append(('prepare', project, release))

        def exec(self, project, command, stdin=b'', timeout_s=1800):
            self.calls.append(('exec', project, command))
            return app_host.Result(0, '', '')

        def deploy_release(self, project, code, release, url):
            self.calls.append(('deploy_release', project, code, release, url, self.ledger.versions(project)))
            if self.fail:
                raise app_host.SetupFailed('switch', self.fail)
            return {'deployed': release}

        def revert_release(self, project, code, release):
            self.calls.append(('revert_release', project, code, release))

        def invite(self, project, release, account):
            self.calls.append(('invite', project, release, account))
            return TOKEN

    @staticmethod
    def writer(uses_tool=True):
        def generate(source, kind, request, cancelled, backend=None):
            manifest = source / 'AndroidManifest.xml'
            text = manifest.read_text()
            if INTERNET not in text:
                manifest.write_text(text.replace('  <application', f'  {INTERNET}\n  <application', 1))
            (source / 'rose-app.json').write_text(json.dumps({'schemaVersion': 1, 'profile': 'client-backend-v1'}))
            (source / 'contract').mkdir(exist_ok=True)
            (source / 'contract/api.json').write_text(json.dumps(CONTRACT))
            (source / 'backend').mkdir(exist_ok=True)
            (source / 'backend/app.py').write_text('print("a Python backend")\n')  # a .py the APK rules refuse
            (source / 'backend/start.sh').write_text('exec python3 app.py\n')
            if uses_tool and backend is not None:
                backend.exec('mkdir -p "$PWD" && cat > app.py', b'print(1)\n')
            return 'Notes, with a Python backend.'
        generate.backend = True
        return generate

    def job(self, fail=None, uses_tool=True):
        from rose.app_builder import codegen
        from rose.tests.test_app_level2_pipeline import Base
        base = Base('run_job')
        base.setUp()
        self.addCleanup(base.doCleanups)
        b = base.builder(self.writer(uses_tool), codegen=codegen)
        b.hosting = self.FakeHosting(b.ledger, fail)
        out, job = base.run_job(b)
        return b, out['projectId'], job

    def test_the_agents_backend_is_switched_to_before_delivery_and_recorded(self):
        b, project, job = self.job()
        self.assertEqual(job['state'], 'ready', job)
        names = [c[0] for c in b.hosting.calls]
        self.assertEqual(names[:3], ['ensure', 'prepare', 'exec'])  # the container first, on the tool's first use
        deploy = next(c for c in b.hosting.calls if c[0] == 'deploy_release')
        release = deploy[3]
        self.assertEqual((deploy[2], deploy[4], deploy[5]), (1, APP_URL, []))  # before anything was delivered
        self.assertEqual(b.ledger.server(project, 1), {'sha256': release, 'size': 0, 'target': 'chariot-agent'})
        self.assertEqual(b.ledger.profile(project), L.CLIENT_BACKEND)
        (version,) = b.ledger.versions(project)
        self.assertEqual(version['server']['status'], 'ok')  # the summary from its contract, exact keys
        self.assertEqual(set(version['server']), {'schemaVersion', 'status', 'api', 'minClientVersionCode'})
        self.assertEqual((version['hosting']['state'], version['hosting']['appUrl']), ('set_up', APP_URL))
        self.assertIsNone(b.ledger.server_manifest(project, 1))
        out = b.invite('d', project, {'schemaVersion': 1, 'account': 'me'})
        self.assertEqual(out['invite'], TOKEN)
        self.assertEqual(b.hosting.calls[-1], ('invite', project, release, 'me'))

    def test_a_failed_switch_delivers_nothing(self):
        b, project, job = self.job(fail='never healthy')
        self.assertEqual((job['state'], job['failure']['category'], job['failure']['retryable']),
                         ('failed', 'toolchain', True), job)
        self.assertEqual(b.ledger.versions(project), [])

    def test_without_an_agent_container_no_backend_app_is_built(self):
        from rose.app_builder import codegen
        from rose.tests.test_app_level2_pipeline import Base
        base = Base('run_job')
        base.setUp()
        self.addCleanup(base.doCleanups)
        b = base.builder(self.writer(), codegen=codegen)  # no hosting configured
        out, job = base.run_job(b)
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'toolchain'), job)
        self.assertIn('no agent container', job['failure']['summary'])


if __name__ == '__main__':
    unittest.main()
