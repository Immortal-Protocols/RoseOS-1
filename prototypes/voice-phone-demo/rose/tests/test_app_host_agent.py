"""The agent-container setup (rose.app_builder.app_host; docs/APP-BUILDER.md §7.7a), run against a fake agent: the
real remote scripts and Chariot's own launcher, executed by the local shell in a temporary HOME, with the container's
tools linked into a stand-in for /chariot/bin and a fake HTTP server in place of the app's ELF. No SSH, Chariot,
network or real container: what this shows is the procedure's own behaviour (identity, upload, stop, backup, keep
the previous binary, health, publish, roll back), not that a real agent serves the app."""
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import tarfile
import io
import tempfile
import time
import unittest
from pathlib import Path

from rose.app_builder import app_host

AGENT_ID = '0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0'
AGENT_HEX = AGENT_ID.replace('-', '')
APP_URL = f'https://agent-{AGENT_HEX}.apps.example.org'

# The app server stand-in: /health is 200 with this binary's revision once ready; it records each start in its
# data folder, so a test can see which binary ran on which data.
FAKE_SERVER = '''#!/usr/bin/env python3
import http.server, json, os, sys
data = os.environ["DATA_DIR"]
os.makedirs(os.path.join(data, "accounts"), exist_ok=True)
with open(os.path.join(data, "accounts", "a.sqlite3"), "a") as f:
    f.write("@NAME@\\n")
if "@NAME@".startswith("broken"):
    sys.exit(1)
revision = os.environ["CHARIOT_REVISION_SHA256"]
host = os.environ["ROSE_LISTEN_HOST"]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok", "revision_sha256": revision}).encode()
        self.send_response(200 if self.path == "/health" else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
http.server.HTTPServer((host, int(os.environ["PORT"])), H).serve_forever()
'''


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class LocalAgent(app_host.Channel):
    """An agent container stand-in: the local shell, a temporary HOME and the contract's identity variables."""

    def __init__(self, root: Path, app_id=AGENT_ID, app_url=APP_URL, missing=()):
        self.home, self.bin = root / 'agent-data', root / 'chariot-bin'
        (self.home / 'workspace').mkdir(parents=True)
        self.bin.mkdir()
        for applet in app_host.APPLETS:
            if applet not in missing:
                (self.bin / applet).symlink_to(shutil.which(applet))
        self.env = {'HOME': str(self.home), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
                    'CHARIOT_APP_ID': app_id, 'CHARIOT_APP_URL': app_url}
        self.scripts = []

    def _run(self, argv, stdin):
        done = subprocess.run(argv, input=stdin, env=self.env, capture_output=True, timeout=120)
        return app_host.Result(done.returncode, done.stdout.decode(), done.stderr.decode())

    def script(self, text):
        self.scripts.append(text)
        return self._run([str(self.bin / 'sh'), '-s'], text.encode())

    def receive(self, tar, folder):
        return self._run([str(self.bin / 'sh'), '-c',
                          f'exec {self.bin}/tar -xf - -C "$HOME/workspace/.chariot-app/{folder}"'], tar)

    @property
    def app(self):
        return self.home / 'workspace/.chariot-app'

    def stop(self):
        pid = self.app / 'server.pid'
        if pid.exists():
            try:
                os.kill(int(pid.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass


class Setup(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.agent = LocalAgent(self.root)
        self.addCleanup(self.agent.stop)
        self.port = free_port()

    def plan(self, name, schema=1):
        elf = self.root / f'{name}.server'
        elf.write_text(FAKE_SERVER.replace('@NAME@', name))
        sha = hashlib.sha256(elf.read_bytes()).hexdigest()
        return app_host.Plan('p1', 1, AGENT_HEX, APP_URL, elf, sha, schema, self.port)

    def deploy(self, p, agent=None, **kw):
        return app_host.deploy(p, agent or self.agent, enabled=True, bin_dir=str((agent or self.agent).bin),
                               health_wait_s=kw.pop('health_wait_s', 20), stop_wait_s=5,
                               generate_keyring=kw.pop('generate_keyring', True), **kw)

    def get(self, path='/health'):
        with socket.create_connection(('127.0.0.1', self.port), timeout=5) as s:
            s.sendall(f'GET {path} HTTP/1.0\r\n\r\n'.encode())
            return s.makefile('rb').read().decode()

    def test_off_unless_enabled(self):
        with self.assertRaisesRegex(app_host.SetupFailed, 'disabled'):
            app_host.deploy(self.plan('v1'), self.agent)
        self.assertEqual(self.agent.scripts, [])  # nothing ran

    def test_first_setup_publishes_the_app_on_loopback_with_a_keyring(self):
        p = self.plan('v1')
        out = self.deploy(p)
        self.assertEqual((out['deployed'], out['previous'], out['appUrl']), (p.sha256, None, APP_URL))
        app = self.agent.app
        self.assertEqual(json.loads((app / 'app.json').read_text()),
                         {'format': 'chariot-agent-app/v1', 'port': self.port, 'health_path': '/health'})
        self.assertEqual(json.loads((app / 'deployed.json').read_text())['sha256'], p.sha256)
        self.assertIn(p.sha256, self.get())
        keyring = json.loads((app / 'keys/keyring.json').read_text())
        self.assertEqual((keyring['format'], keyring['current'], len(keyring['keys'])), ('rose-signin-keyring/v1', 1, 1))
        self.assertEqual(len(__import__('base64').b64decode(keyring['keys'][0]['key'])), 32)
        self.assertEqual((app / 'keys/keyring.json').stat().st_mode & 0o777, 0o400)
        start = (app / 'start').read_text()
        self.assertIn('export ROSE_LISTEN_HOST=127.0.0.1', start)  # never the pod's own address
        self.assertIn(f'export CHARIOT_REVISION_SHA256={p.sha256}', start)
        self.assertFalse(any(app.glob('incoming-*')))

    def test_an_update_backs_up_the_data_and_keeps_the_previous_binary(self):
        v1, v2 = self.plan('v1'), self.plan('v2', schema=2)
        self.deploy(v1)
        key = (self.agent.app / 'keys/keyring.json').read_bytes()
        out = self.deploy(v2)
        app = self.agent.app
        self.assertEqual((out['deployed'], out['previous']), (v2.sha256, v1.sha256))
        self.assertIn(v2.sha256, self.get())
        self.assertEqual((app / 'server.prev').read_bytes(), v1.elf.read_bytes())
        self.assertIn(f'CHARIOT_REVISION_SHA256={v1.sha256}', (app / 'start.prev').read_text())  # its own start
        with tarfile.open(app / 'backups/latest.tar') as tar:  # v1's data, taken while nothing ran
            backed = tar.extractfile('./accounts/a.sqlite3').read().decode()
        self.assertEqual(backed, 'v1\n')
        self.assertEqual((app / 'data/accounts/a.sqlite3').read_text(), 'v1\nv2\n')
        self.assertEqual((app / 'keys/keyring.json').read_bytes(), key)  # never replaced once there
        self.deploy(self.plan('v3', schema=2))
        self.assertTrue((app / 'backups/previous.tar').is_file())  # two backups kept, no more
        self.assertEqual(sorted(p.name for p in (app / 'backups').iterdir()), ['latest.tar', 'previous.tar'])

    def test_a_server_that_never_gets_healthy_is_rolled_back_to_the_last_good_state(self):
        v1 = self.plan('v1')
        self.deploy(v1)
        broken = self.plan('broken', schema=2)
        with self.assertRaisesRegex(app_host.SetupFailed, rf'switch: .*health.*restored: {v1.sha256}'):
            self.deploy(broken, health_wait_s=3)
        app = self.agent.app
        self.assertIn(v1.sha256, self.get())  # the previous binary serves again
        self.assertEqual((app / 'server').read_bytes(), v1.elf.read_bytes())
        # The data the broken binary touched is replaced by the backup, then the restored v1 opens it.
        self.assertEqual((app / 'data/accounts/a.sqlite3').read_text(), 'v1\nv1\n')
        self.assertTrue((app / 'app.json').is_file())
        self.assertEqual(json.loads((app / 'deployed.json').read_text())['sha256'], v1.sha256)

    def test_no_keyring_is_refused_before_anything_is_touched(self):
        v1 = self.plan('v1')
        self.deploy(v1)
        (self.agent.app / 'keys/keyring.json').unlink()
        with self.assertRaisesRegex(app_host.SetupFailed, 'keyring'):
            self.deploy(self.plan('v2', schema=2), generate_keyring=False)
        self.assertEqual((self.agent.app / 'server').read_bytes(), v1.elf.read_bytes())
        self.assertTrue((self.agent.app / 'app.json').is_file())  # still published, still v1
        self.assertIn(v1.sha256, self.get())

    def test_an_older_data_schema_or_another_agent_is_refused(self):
        self.deploy(self.plan('v2', schema=5))
        with self.assertRaisesRegex(app_host.SetupFailed, 'older than the deployed'):
            self.deploy(self.plan('v1', schema=4))
        other = LocalAgent(self.root / 'other', app_id='11111111-2222-3333-4444-555555555555')
        with self.assertRaisesRegex(app_host.SetupFailed, 'not the project'):
            self.deploy(self.plan('v3', schema=5), agent=other)
        bare = LocalAgent(self.root / 'bare', missing=('flock',))
        with self.assertRaisesRegex(app_host.SetupFailed, 'missing=flock'):
            self.deploy(self.plan('v3', schema=5), agent=bare)


class Contract(unittest.TestCase):
    def test_the_launcher_is_chariots_published_one(self):
        self.assertEqual(hashlib.sha256(app_host.LAUNCHER.encode()).hexdigest(), app_host.LAUNCHER_SHA256)
        self.assertIn('8dc39b86e20984e530fecadab36601c77e9cd467', app_host.LAUNCHER_SOURCE)

    def test_the_ssh_channel_runs_chariot_ssh_with_its_own_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'argv'
            fake = Path(tmp) / 'chariot'
            fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {log}\nprintf "HOME=%s\\n" "$HOME" >> {log}\ncat >> {log}\n')
            fake.chmod(0o700)
            ssh = app_host.ChariotSsh(fake, 'agent-000001', Path(tmp) / 'cli-home')
            self.assertEqual(ssh.script('echo hi\n').code, 0)
            self.assertEqual(log.read_text().splitlines(), ['ssh', 'agent-000001', '--', '/chariot/bin/sh', '-s',
                                                            f'HOME={tmp}/cli-home', 'echo hi'])
            ssh.receive(b'tar-bytes', 'incoming-' + 'a' * 64)
            self.assertEqual(log.read_text().splitlines()[:4], ['ssh', 'agent-000001', '--',
                             'exec /chariot/bin/tar -xf - -C "$HOME/workspace/.chariot-app/incoming-' + 'a' * 64 + '"'])
            with self.assertRaises(ValueError):
                ssh.receive(b'', '../x')

    def test_reserved_and_privileged_ports_are_refused(self):
        for port in (80, 8088, 42617, 70000):
            with self.subTest(port=port), self.assertRaisesRegex(app_host.SetupFailed, 'port'):
                app_host.plan(None, Path('/x'), 'p', port=port)


from unittest import mock  # noqa: E402

from rose.app_builder import ledger as L, server_build, server_manifest  # noqa: E402
from rose.tests.test_app_builder import HAS_SDK  # noqa: E402
from rose.tests.test_app_level2_pipeline import BUILDER_CRATES, Base, Codegen, client_server_app  # noqa: E402
from rose.tests.test_app_server_manifest import tiny_elf  # noqa: E402


class Scripted(app_host.Channel):
    """An agent that answers the probe with an identity and records what else it is asked."""

    def __init__(self, app_id, app_url):
        self.app_id, self.app_url, self.asked = app_id, app_url, []

    def script(self, text):
        self.asked.append(text)
        return app_host.Result(0, f'app_id={self.app_id}\napp_url={self.app_url}\n', '')


class Hosting(unittest.TestCase):
    """AgentHosting on its own: an agent per project, by the identity each agent reports, never derived."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.l = L.Ledger(Path(tmp.name) / 'b')
        self.addCleanup(self.l.close)
        self.agents = {'one': Scripted(AGENT_ID, APP_URL),
                       'two': Scripted('99999999-8888-7777-6666-555555555555',
                                       'https://agent-99999999888877776666555555555555.apps.example.org')}
        cfg = app_host.AgentHostConfig(Path('/usr/bin/chariot'), Path('/srv/cli'), ('one', 'two'))
        self.h = app_host.AgentHosting(cfg, self.l, channel=lambda name: self.agents[name])

    def test_each_project_gets_a_free_agent_by_its_own_identity(self):
        a = self.l.create('d', 'r1', 'x', 'X')['projectId']
        b = self.l.create('d', 'r2', 'y', 'Y')['projectId']
        self.h.ensure(a)
        self.h.ensure(a)  # recorded once
        self.h.ensure(b)
        self.assertEqual(self.l.agent_container(a), {'agentId': AGENT_HEX, 'appUrl': APP_URL})
        self.assertEqual(self.l.agent_container(b)['appUrl'], self.agents['two'].app_url)
        c = self.l.create('d', 'r3', 'z', 'Z')['projectId']
        with self.assertRaisesRegex(app_host.SetupFailed, 'no configured agent is free'):
            self.h.ensure(c)
        self.assertEqual(self.h.name_for(AGENT_HEX), 'one')


@unittest.skipUnless(HAS_SDK or os.environ.get('ROSE_REQUIRE_APP_BUILD'), 'needs the pinned Android SDK and a JDK')
class InTheJob(Base):
    """The Builder with hosting: the agent container is recorded before the client is generated (so its URL is
    baked), and the server is set up before the version is delivered; a failed setup delivers nothing."""

    class FakeHosting:
        def __init__(self, ledger, fail=None):
            self.ledger, self.fail, self.calls = ledger, fail, []

        def ensure(self, project):
            self.calls.append(('ensure', project))
            if self.ledger.agent_container(project) is None:
                self.ledger.record_agent_container(project, AGENT_HEX, APP_URL)

        def deploy(self, project, code, elf, manifest):
            self.calls.append(('deploy', project, code, Path(elf).is_file(), manifest['elf']['sha256'],
                               self.ledger.versions(project)))
            if self.fail:
                raise app_host.SetupFailed('switch', self.fail)
            return {'deployed': manifest['elf']['sha256']}

    def job(self, target=server_manifest.TARGET, fail=None):
        elf_bytes = tiny_elf()
        seen = {}

        def build(snapshot, work, *args, **kw):
            seen['url'] = next((Path(work).parent / 'client').glob('src/**/rose/RoseServer.java')).read_text()
            Path(work).mkdir(parents=True, exist_ok=True)
            (Path(work) / 'server').write_bytes(elf_bytes)
            return server_build.ServerBuild(Path(work) / 'server', hashlib.sha256(elf_bytes).hexdigest(),
                                            len(elf_bytes), target, '# Cargo.lock\nversion = 4\n')
        b = self.builder(client_server_app(), codegen=Codegen(), server_crates=BUILDER_CRATES)
        b.hosting = self.FakeHosting(b.ledger, fail)
        with mock.patch.object(server_build, 'build', build):
            out, job = self.run_job(b)
        return b, out['projectId'], job, seen

    def test_the_container_url_is_baked_and_the_server_is_up_before_delivery(self):
        b, project, job, seen = self.job()
        self.assertEqual(job['state'], 'ready', job)
        self.assertIn(f'static final String URL = "{APP_URL}";', seen['url'])
        ensure, deploy = b.hosting.calls
        self.assertEqual(ensure, ('ensure', project))
        self.assertEqual(deploy[:4], ('deploy', project, 1, True))
        self.assertEqual(deploy[4], b.ledger.server(project, 1)['sha256'])
        self.assertEqual(deploy[5], [])  # nothing delivered yet while its server was set up

    def test_a_failed_setup_delivers_nothing_and_can_be_retried(self):
        b, project, job, _ = self.job(fail='never healthy')
        self.assertEqual((job['state'], job['failure']['category'], job['failure']['retryable']),
                         ('failed', 'toolchain', True), job)
        self.assertIn('could not be set up in its Chariot agent container (switch)', job['failure']['summary'])
        self.assertEqual(b.ledger.versions(project), [])

    def test_a_server_for_another_target_is_not_delivered(self):
        b, project, job, _ = self.job(target='x86_64-unknown-linux-gnu')
        self.assertEqual(job['state'], 'failed', job)
        self.assertEqual(b.ledger.versions(project), [])


if __name__ == '__main__':
    unittest.main()
