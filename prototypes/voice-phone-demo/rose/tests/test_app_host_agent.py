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
import sys
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
FAKE_SERVER = '''#!@PYTHON@
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


BUSYBOX_FLOCK = '''#!/bin/sh
for a in "$@"; do
  case "$a" in -[sxnu]) ;; -*) echo "flock: unrecognized option" >&2; exit 1 ;; *) break ;; esac
done
exec @FLOCK@ "$@"
'''


def running(app: Path) -> Path:
    """The server binary of the release the shim runs (``current``)."""
    return app / 'releases' / (app / 'current').read_text() / 'server'


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
        if 'flock' not in missing:  # BusyBox's flock: -s -x -u -n and nothing else (flock.c's "sxnu")
            (self.bin / 'flock').unlink()
            (self.bin / 'flock').write_text(BUSYBOX_FLOCK.replace('@FLOCK@', shutil.which('flock')))
            (self.bin / 'flock').chmod(0o755)
        # Like the agent: /chariot/bin is not on PATH, and nothing else is either.
        self.env = {'HOME': str(self.home), 'PATH': '/nonexistent', 'LANG': 'C.UTF-8',
                    'CHARIOT_APP_ID': app_id, 'CHARIOT_APP_URL': app_url}
        self.scripts = []

    def _run(self, argv, stdin):
        done = subprocess.run(argv, input=stdin, env=self.env, capture_output=True, timeout=120)
        return app_host.Result(done.returncode, done.stdout.decode(), done.stderr.decode())

    def script(self, text):
        self.scripts.append(text)
        return self._run([str(self.bin / 'sh'), '-s'], text.encode())

    def run(self, command, stdin=b'', timeout_s=None, stopped=lambda: False):
        return self._run([str(self.bin / 'sh'), '-c', command], stdin)

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
        elf.write_text(FAKE_SERVER.replace('@NAME@', name).replace('@PYTHON@', sys.executable))
        sha = hashlib.sha256(elf.read_bytes()).hexdigest()
        return app_host.Plan('p1', 1, AGENT_HEX, APP_URL, elf, sha, schema, self.port)

    def deploy(self, p, agent=None, **kw):
        return app_host.deploy(p, agent or self.agent, enabled=True, bin_dir=str((agent or self.agent).bin),
                               health_wait_s=kw.pop('health_wait_s', 20), stop_wait_s=kw.pop('stop_wait_s', 5),
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
        self.assertEqual(json.loads((app / 'deployed.json').read_text())['release'], p.sha256)
        self.assertIn(p.sha256, self.get())
        keyring = json.loads((app / 'keys/keyring.json').read_text())
        self.assertEqual((keyring['format'], keyring['current'], len(keyring['keys'])), ('rose-signin-keyring/v1', 1, 1))
        self.assertEqual(len(__import__('base64').b64decode(keyring['keys'][0]['key'])), 32)
        self.assertEqual((app / 'keys/keyring.json').stat().st_mode & 0o777, 0o400)
        start = (app / 'start').read_text()  # the shim, the same for every release
        self.assertIn('export ROSE_LISTEN_HOST=127.0.0.1', start)  # never the pod's own address
        self.assertIn('export CHARIOT_DEPLOYMENT_SHA256="$release"', start)
        self.assertEqual((app / 'current').read_text(), p.sha256)
        self.assertIn(f'export CHARIOT_REVISION_SHA256={p.sha256}', (app / 'releases' / p.sha256 / 'start').read_text())
        self.assertFalse(any(app.glob('incoming-*')))

    def test_an_update_backs_up_the_data_and_keeps_the_previous_binary(self):
        v1, v2 = self.plan('v1'), self.plan('v2', schema=2)
        self.deploy(v1)
        key = (self.agent.app / 'keys/keyring.json').read_bytes()
        out = self.deploy(v2)
        app = self.agent.app
        self.assertEqual((out['deployed'], out['previous']), (v2.sha256, v1.sha256))
        self.assertIn(v2.sha256, self.get())
        self.assertEqual((app / 'releases' / v1.sha256 / 'server').read_bytes(), v1.elf.read_bytes())
        self.assertIn(f'CHARIOT_REVISION_SHA256={v1.sha256}',  # its own start
                      (app / 'releases' / v1.sha256 / 'start').read_text())
        self.assertEqual(json.loads((app / 'deployed.prev.json').read_text())['release'], v1.sha256)
        with tarfile.open(app / 'backups/latest.tar') as tar:  # v1's data, taken while nothing ran
            backed = tar.extractfile('./accounts/a.sqlite3').read().decode()
        self.assertEqual(backed, 'v1\n')
        self.assertEqual((app / 'data/accounts/a.sqlite3').read_text(), 'v1\nv2\n')
        self.assertEqual((app / 'keys/keyring.json').read_bytes(), key)  # never replaced once there
        v3 = self.plan('v3', schema=2)
        self.deploy(v3)
        self.assertTrue((app / 'backups/previous.tar').is_file())  # two backups kept, no more
        self.assertEqual(sorted(p.name for p in (app / 'backups').iterdir()), ['latest.tar', 'previous.tar'])
        self.assertEqual(sorted(p.name for p in (app / 'releases').iterdir()), sorted([v2.sha256, v3.sha256]))
        self.assertFalse((app / 'switching').exists())

    def test_a_server_that_never_gets_healthy_is_rolled_back_to_the_last_good_state(self):
        v1 = self.plan('v1')
        self.deploy(v1)
        broken = self.plan('broken', schema=2)
        with self.assertRaisesRegex(app_host.SetupFailed, rf'switch: .*health.*restored: {v1.sha256}'):
            self.deploy(broken, health_wait_s=3)
        app = self.agent.app
        self.assertIn(v1.sha256, self.get())  # the previous binary serves again
        self.assertEqual(running(app).read_bytes(), v1.elf.read_bytes())
        # The data the broken binary touched is replaced by the backup, then the restored v1 opens it.
        self.assertEqual((app / 'data/accounts/a.sqlite3').read_text(), 'v1\nv1\n')
        self.assertTrue((app / 'app.json').is_file())
        self.assertEqual(json.loads((app / 'deployed.json').read_text())['release'], v1.sha256)

    def test_no_keyring_is_refused_before_anything_is_touched(self):
        v1 = self.plan('v1')
        self.deploy(v1)
        (self.agent.app / 'keys/keyring.json').unlink()
        with self.assertRaisesRegex(app_host.SetupFailed, 'keyring'):
            self.deploy(self.plan('v2', schema=2), generate_keyring=False)
        self.assertEqual(running(self.agent.app).read_bytes(), v1.elf.read_bytes())
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


    def test_an_interrupted_switch_is_undone_before_anything_else(self):
        """The review's case: a switch cut off after the new binary started (SSH dropped, builder stopped), then a
        failing attempt. The last good binary and its data come back; nothing the unverified binary did stays."""
        v1 = self.plan('v1')
        self.deploy(v1)
        cut = self.agent.script

        def dropped(text):
            if "failed=health\nhealthy" in text:  # the switch: killed outright once its binary started
                self.agent.script = cut
                text = text.replace("failed=health\nhealthy", "kill -KILL $$\nhealthy", 1)
            return cut(text)
        self.agent.script = dropped
        with self.assertRaises(app_host.SetupFailed):
            self.deploy(self.plan('v2', schema=2))
        app = self.agent.app
        self.assertTrue((app / 'switching').exists())
        self.assertFalse((app / 'app.json').exists())
        with self.assertRaisesRegex(app_host.SetupFailed, rf'restored: {v1.sha256}'):
            self.deploy(self.plan('broken', schema=2), health_wait_s=3)
        self.assertIn(v1.sha256, self.get())
        self.assertEqual(running(app).read_bytes(), v1.elf.read_bytes())
        self.assertNotIn('v2', (app / 'data/accounts/a.sqlite3').read_text())
        self.assertTrue((app / 'app.json').is_file())
        self.assertFalse((app / 'switching').exists())
        self.assertEqual(self.deploy(self.plan('v2', schema=2))['previous'], v1.sha256)  # and on it goes

    def test_a_failed_backup_leaves_the_app_serving_and_both_backups_whole(self):
        v1 = self.plan('v1')
        self.deploy(v1)
        self.deploy(self.plan('v1b'))
        real, tar = shutil.which('tar'), self.agent.bin / 'tar'
        tar.unlink()
        tar.write_text(f'#!/bin/sh\ncase "$*" in *-cf*) exit 1 ;; esac\nexec {real} "$@"\n')
        tar.chmod(0o755)
        for _ in range(2):
            with self.assertRaisesRegex(app_host.SetupFailed, r'\(backup\)'):
                self.deploy(self.plan('v2', schema=2))
            self.assertIn(self.plan('v1b').sha256, self.get())
            self.assertTrue((self.agent.app / 'app.json').is_file())
        for name in ('latest.tar', 'previous.tar'):
            with tarfile.open(self.agent.app / 'backups' / name) as archive:
                archive.getmembers()

    def test_another_apps_agent_is_never_set_up_over(self):
        self.deploy(self.plan('v1'))
        theirs = app_host.Plan('p2', 1, AGENT_HEX, APP_URL, self.plan('x').elf, self.plan('x').sha256, 9, self.port)
        with self.assertRaisesRegex(app_host.SetupFailed, 'another app'):
            self.deploy(theirs)
        for leftover in ('server', 'data/notes.sqlite3'):
            with self.subTest(leftover=leftover):
                agent = LocalAgent(self.root / leftover.replace('/', '-'))
                (agent.app / 'data').mkdir(parents=True)
                (agent.app / leftover).write_text('not ours')
                with self.assertRaisesRegex(app_host.SetupFailed, 'did not set up'):
                    self.deploy(self.plan('v1'), agent=agent)
                self.assertEqual((agent.app / leftover).read_text(), 'not ours')

    def test_an_undelivered_release_is_reverted_before_the_next_setup(self):
        v1, v2, v3 = self.plan('v1'), self.plan('v2', schema=2), self.plan('v3', schema=2)
        self.deploy(v1)
        self.deploy(v2)  # set up, but (say) its delivery never happened
        out = self.deploy(v3, undelivered=lambda sha: sha == v2.sha256)
        app = self.agent.app
        self.assertEqual(out['previous'], v1.sha256)
        self.assertEqual((app / 'data/accounts/a.sqlite3').read_text(), 'v1\nv1\nv3\n')  # v2's writes undone
        self.assertIn('v2', (app / 'backups/reverted/accounts/a.sqlite3').read_text())  # and kept aside
        self.assertEqual(sorted(p.name for p in (app / 'releases').iterdir()), sorted([v1.sha256, v3.sha256]))
        self.assertIn(v3.sha256, self.get())
        with self.assertRaises(app_host.SetupFailed):  # a later failed setup restores into its own slot, not this one
            self.deploy(self.plan('broken', schema=2), health_wait_s=3)
        self.assertIn('v2', (app / 'backups/reverted/accounts/a.sqlite3').read_text())
        self.assertIn(v3.sha256, self.get())

    def wrap_tar(self, fails_on):
        """The agent's tar, failing whenever its arguments contain ``fails_on`` (while the flag file exists)."""
        real, tar, flag = shutil.which('tar'), self.agent.bin / 'tar', self.root / 'tar-fails'
        tar.unlink()
        tar.write_text(f'#!/bin/sh\nif test -f {flag}; then case "$*" in *"{fails_on}"*) exit 1 ;; esac; fi\n'
                       f'exec {real} "$@"\n')
        tar.chmod(0o755)
        flag.touch()
        return flag

    def test_data_that_cannot_be_put_back_is_never_replaced_or_served(self):
        v1 = self.plan('v1')
        self.deploy(v1)
        flag = self.wrap_tar('-xf backups/latest.tar')
        with self.assertRaisesRegex(app_host.SetupFailed, 'restored: data-not-restored'):
            self.deploy(self.plan('broken', schema=2), health_wait_s=3)
        app = self.agent.app
        self.assertFalse((app / 'app.json').exists())  # nothing is served on data it could not vouch for
        self.assertTrue((app / 'switching').read_text().endswith(' installing'))  # so the next run tries again
        self.assertFalse((app / 'data.restoring').exists())
        flag.unlink()
        out = self.deploy(self.plan('v2', schema=2))
        self.assertEqual(out['previous'], v1.sha256)
        self.assertNotIn('broken', (app / 'data/accounts/a.sqlite3').read_text())
        self.assertIn('broken', (app / 'backups/replaced/accounts/a.sqlite3').read_text())  # kept aside, not lost

    def test_the_same_server_again_changes_nothing_and_is_never_undone(self):
        v1, v2 = self.plan('v1'), self.plan('v2', schema=2)
        self.deploy(v1)
        self.deploy(v2)
        backup = (self.agent.app / 'backups/latest.tar').read_bytes()
        again = self.deploy(v2)  # a version that changed only its client
        app = self.agent.app
        self.assertTrue(again['unchanged'])
        self.assertEqual(json.loads((app / 'deployed.prev.json').read_text())['release'], v1.sha256)
        self.assertEqual(sorted(p.name for p in (app / 'releases').iterdir()), sorted([v1.sha256, v2.sha256]))
        self.assertEqual((app / 'backups/latest.tar').read_bytes(), backup)
        self.assertIn(v2.sha256, self.get())
        self.assertEqual(app_host.revert(v2, self.agent, v1.sha256, bin_dir=str(self.agent.bin)), 'not-current')
        (app / 'deployed.prev.json').write_text((app / 'deployed.json').read_text())  # as an older build left it
        self.assertEqual(app_host.revert(v2, self.agent, v2.sha256, bin_dir=str(self.agent.bin)), 'same')
        self.assertIn(v2.sha256, self.get())

    def test_a_revert_that_fails_starts_the_deployed_release_again(self):
        v1, v2 = self.plan('v1'), self.plan('v2', schema=2)
        self.deploy(v1)
        self.deploy(v2)
        self.wrap_tar('-xf backups/latest.tar')
        with self.assertRaisesRegex(app_host.SetupFailed, 'revert'):
            self.deploy(self.plan('v3', schema=2), undelivered=lambda sha: sha == v2.sha256)
        self.assertIn(v2.sha256, self.get())  # stopped for the revert, then started again
        self.assertTrue((self.agent.app / 'app.json').is_file())

    def test_a_switch_cut_off_during_the_backup_only_restarts_the_app(self):
        v1 = self.plan('v1')
        self.deploy(v1)
        real, tar = shutil.which('tar'), self.agent.bin / 'tar'
        tar.unlink()  # the SSH session dies while the backup runs: the shell is killed, no trap runs
        tar.write_text(f'#!/bin/sh\ncase "$*" in *-cf*) kill -KILL $PPID; exit 1 ;; esac\nexec {real} "$@"\n')
        tar.chmod(0o755)
        with self.assertRaises(app_host.SetupFailed):
            self.deploy(self.plan('v2', schema=2))
        app = self.agent.app
        self.assertTrue((app / 'switching').read_text().endswith(' stopped'))
        tar.unlink()
        tar.symlink_to(real)
        self.deploy(self.plan('v2', schema=2))
        self.assertFalse((app / 'backups/replaced').exists())  # its data was never touched, so never put back
        self.assertEqual((app / 'data/accounts/a.sqlite3').read_text(), 'v1\nv1\nv2\n')

    def test_a_stale_pid_file_is_never_signalled(self):
        bystander = subprocess.Popen(['sleep', '60'])
        self.addCleanup(bystander.kill)
        self.agent.app.mkdir(parents=True)
        (self.agent.app / 'server.pid').write_text(f'{bystander.pid}\n')
        self.deploy(self.plan('v1'))
        self.assertIsNone(bystander.poll())  # no server held the lock, so nothing was killed


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


TOKEN = 'A' * 26 + '2' * 26
# The current release's `invite <account>` stand-in: the token for the expected environment, the account recorded.
FAKE_INVITE = '''set -eu
test "$DATA_DIR" = "$HOME/workspace/.chariot-app/data" && test "$CHARIOT_DEPLOYMENT_SHA256" = "@SHA@" || exit 9
printf '%s' "$1" > "$HOME/asked"
case "$1" in
  enrolled) echo "rose server: no invite: already_enrolled" >&2; exit 1 ;;
  other) echo "rose server: no invite: invalid_name" >&2; exit 1 ;;
  *) echo @TOKEN@ ;;
esac
'''


class Invite(unittest.TestCase):
    """The creator's invite from the server set up in the agent: the account passed exactly, the token only from a
    success, a refusal as its one word, and nothing run for an account rose-signin would refuse."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.agent = LocalAgent(Path(tmp.name))
        self.sha = 'ab' * 32
        release = self.agent.app / 'releases' / self.sha
        release.mkdir(parents=True)
        (self.agent.app / 'current').write_text(self.sha)
        (release / 'invite').write_text(FAKE_INVITE.replace('@SHA@', self.sha).replace('@TOKEN@', TOKEN))

    def invite(self, account):
        return app_host.invite(self.agent, self.sha, account, bin_dir=str(self.agent.bin))

    def test_the_account_reaches_the_server_exactly_and_the_token_comes_back_once(self):
        for account in ('o\'neil $(x) "q" `y` \\ ;&|*', 'me', '\u00e9t\u00e9 \U0001F600'):
            with self.subTest(account=account):
                self.assertEqual(self.invite(account), TOKEN)
                self.assertEqual((self.agent.home / 'asked').read_text(encoding='utf-8'), account)

    def test_refusals_are_one_word_and_carry_no_output(self):
        for account, word in (('enrolled', 'already_enrolled'), ('other', 'invalid_name')):
            with self.subTest(account=account), self.assertRaises(app_host.InviteRefused) as caught:
                self.invite(account)
            self.assertEqual(caught.exception.word, word)
        (self.agent.app / 'releases' / self.sha / 'invite').unlink()
        with self.assertRaises(app_host.InviteRefused) as caught:
            self.invite('me')
        self.assertEqual(caught.exception.word, 'unsupported')  # a backend with no creator invites
        (self.agent.app / 'current').write_text('cd' * 32)
        with self.assertRaises(app_host.InviteRefused) as caught:
            self.invite('me')
        self.assertEqual(caught.exception.word, 'not_set_up')  # another release runs there now

    def test_an_account_rose_signin_refuses_never_reaches_the_agent(self):
        for account in ('', 'a:b', 'x' * 129, 'a\nb', '\u00e9' * 65, None, 7):
            with self.subTest(account=account), self.assertRaises(app_host.InviteRefused):
                self.invite(account)
        self.assertEqual(self.agent.scripts, [])

    def test_output_that_is_not_a_token_is_not_returned(self):
        class Noisy(app_host.Channel):
            def script(self, text):
                return app_host.Result(0, 'not a token\n', '')
        with self.assertRaises(app_host.InviteRefused) as caught:
            app_host.invite(Noisy(), self.sha, 'me')
        self.assertEqual(caught.exception.word, 'bad_output')


from unittest import mock  # noqa: E402

from rose.app_builder import ledger as L, server_manifest, service  # noqa: E402
from rose.tests.test_app_builder import HAS_SDK  # noqa: E402
from rose.tests.test_app_builder_one_server import shape  # noqa: E402

# The hosting additions to builder API v1 (§7.7a) for the Agent's tests: a hosted version's library entry, the invite
# answer, and the invite refusals (test_builder_routes). Regenerate with ROSE_WRITE_WIRE_FIXTURE=1 after an agreed change.
HOSTING_WIRE = Path(__file__).resolve().parent / 'fixtures/builder-hosting-v1.json'
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

    def test_an_invite_never_runs_during_a_setup(self):
        a = self.l.create('d', 'r1', 'x', 'X')['projectId']
        self.h.ensure(a)
        with self.h._busy:
            with self.assertRaises(app_host.InviteRefused) as caught:
                self.h.invite(a, 'ab' * 32, 'me')
        self.assertEqual(caught.exception.word, 'busy')


if __name__ == '__main__':
    unittest.main()
