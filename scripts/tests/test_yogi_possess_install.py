"""Opt-in possess install. No GCP, no guest boot, no live SSH to Yogi."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import yogi_possess_guest as guest  # noqa: E402
import yogi_possess_install as install  # noqa: E402
import yogi_possession  # noqa: E402

BLOB = 'B' * 68
FOREIGN = 'ssh-ed25519 ' + ('A' * 40) + ' foreign'
OP = 'ab' * 16
VERSION = 'OpenSSH_9.2p1 Debian-2+deb12u10'
CONFIG = 'Port 22\n# AuthorizedKeysFile is the default\n'
NOW = 1760000000
UNIT = yogi_possession.POSSESSION_UNIT
PROBE = yogi_possession.PROBE
HUNG = 'import subprocess, time\nsubprocess.Popen(["sleep", "30"])\ntime.sleep(30)\n'


def passwd():
    return f'pixelbuild:x:{os.getuid()}:{os.getgid()}::/home/pixelbuild:/bin/bash\n'


def evidence(now=NOW, version=VERSION, config=CONFIG, synchronized=True):
    return {
        'guestUnix': now, 'synchronized': synchronized, 'sshdVersion': version,
        'sshdConfig': config, 'passwd': passwd(), 'probe': PROBE, 'unit': UNIT,
    }


def seed(root):
    home = Path(root) / 'home'
    user = home / 'pixelbuild'
    ssh = user / '.ssh'
    ssh.mkdir(parents=True)
    os.chmod(home, 0o755)
    os.chmod(user, 0o755)
    os.chmod(ssh, 0o700)
    auth = ssh / 'authorized_keys'
    auth.write_text(FOREIGN + '\n')
    os.chmod(auth, 0o600)
    root_ssh = Path(root) / 'root' / '.ssh'
    root_ssh.mkdir(parents=True)
    (root_ssh / 'authorized_keys').write_bytes(b'ROOT KEY\n')
    return auth


class PossessInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'root'
        self.root.mkdir()
        self.auth = seed(self.root)
        self.out = Path(self.tmp.name) / 'out'
        self.out.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_directories_reject_0600_symlink_hardlink_and_foreign_mode(self):
        original = self.auth.read_bytes()
        held = os.open(self.auth, os.O_RDONLY)
        os.chmod(self.auth.parent, 0o600)
        with self.assertRaises(guest.GuestRefused) as caught:
            guest.install(self.root, blob=BLOB, operation_id=OP, probe=PROBE, unit=UNIT, now_unix=NOW,
                          synchronized=True, sshd_version=VERSION, sshd_config=CONFIG, passwd_text=passwd())
        self.assertEqual(caught.exception.code, 'directory-mode')
        os.lseek(held, 0, os.SEEK_SET)
        self.assertEqual(os.read(held, len(original) + 10), original)
        os.close(held)
        os.chmod(self.auth.parent, 0o700)
        os.chmod(self.auth.parent.parent, 0o777)
        with self.assertRaises(guest.GuestRefused) as caught:
            guest.install(self.root, blob=BLOB, operation_id=OP, probe=PROBE, unit=UNIT, now_unix=NOW,
                          synchronized=True, sshd_version=VERSION, sshd_config=CONFIG, passwd_text=passwd())
        self.assertEqual(caught.exception.code, 'directory-mode')
        os.chmod(self.auth.parent.parent, 0o755)
        link = self.auth.with_name('linked')
        os.link(self.auth, link)
        with self.assertRaises(guest.GuestRefused) as caught:
            guest.install(self.root, blob=BLOB, operation_id=OP, probe=PROBE, unit=UNIT, now_unix=NOW,
                          synchronized=True, sshd_version=VERSION, sshd_config=CONFIG, passwd_text=passwd())
        self.assertEqual(caught.exception.code, 'auth-hardlink')
        self.assertEqual(self.auth.read_bytes(), original)
        link.unlink()
        target = self.auth.read_bytes()
        self.auth.unlink()
        self.auth.symlink_to(link)
        link.write_bytes(target)
        with self.assertRaises(guest.GuestRefused) as caught:
            guest.install(self.root, blob=BLOB, operation_id=OP, probe=PROBE, unit=UNIT, now_unix=NOW,
                          synchronized=True, sshd_version=VERSION, sshd_config=CONFIG, passwd_text=passwd())
        self.assertEqual(caught.exception.code, 'symlink')
        self.assertEqual(link.read_bytes(), target)
        self.assertEqual((self.root / 'root' / '.ssh' / 'authorized_keys').read_bytes(), b'ROOT KEY\n')

    def test_ancestor_root_policy_and_shell_and_config(self):
        with self.assertRaises(guest.GuestRefused) as caught:
            guest.ancestor_allows(os.getuid(), 0o755, True, os.getuid())
        self.assertEqual(caught.exception.code, 'ancestor-owner')
        guest.ancestor_allows(0, 0o755, True, os.getuid())
        with self.assertRaises(guest.GuestRefused):
            guest.ancestor_allows(0, 0o600, True, os.getuid())
        with self.assertRaises(guest.GuestRefused) as caught:
            guest.account_from_passwd(f'pixelbuild:x:{os.getuid()}:{os.getgid()}::/home/pixelbuild:/usr/sbin/nologin\n')
        self.assertEqual(caught.exception.code, 'passwd-shell')
        with self.assertRaises(guest.GuestRefused) as caught:
            guest.account_from_passwd('pixelbuild:x:0:0::/home/pixelbuild:/bin/bash\n')
        self.assertEqual(caught.exception.code, 'passwd-account')
        self.assertFalse(guest.sshd_config_decision('Include /etc/ssh/sshd_config.d/*.conf\n')['ok'])
        self.assertFalse(guest.sshd_config_decision('Match User pixelbuild\nAuthorizedKeysFile /tmp/keys\n')['ok'])
        self.assertFalse(guest.sshd_config_decision('AuthorizedKeysFile /root/.ssh/authorized_keys\n')['ok'])
        self.assertTrue(guest.sshd_config_decision(CONFIG)['ok'])
        self.assertFalse(guest.version_allowed('OpenSSH_9.6p1 Ubuntu-3ubuntu13.20'))
        self.assertTrue(guest.version_allowed(VERSION))
        with self.assertRaises(guest.GuestRefused) as caught:
            guest.evidence_from_outputs(version_text=VERSION, config_text=CONFIG, passwd_text=passwd(),
                                        now=NOW, sync_text='no')
        self.assertEqual(caught.exception.code, 'clock-unsynchronized')

    def test_install_preserves_foreign_bytes_and_refuses_a_different_line(self):
        original = self.auth.read_bytes()
        receipt = guest.install(self.root, blob=BLOB, operation_id=OP, probe=PROBE, unit=UNIT, now_unix=NOW,
                                synchronized=True, sshd_version=VERSION, sshd_config=CONFIG, passwd_text=passwd())
        written = self.auth.read_bytes()
        self.assertTrue(written.startswith(original))
        self.assertEqual(stat.S_IMODE(self.auth.stat().st_mode), 0o600)
        self.assertEqual(self.auth.stat().st_uid, os.getuid())
        self.assertIn('expiry-time="', receipt['line'])
        self.assertTrue(receipt['expiry'].endswith('Z'))
        self.assertNotIn('-', receipt['expiry'])
        self.assertEqual(receipt['deadlineSeconds'], 45)
        self.assertIn('backward', receipt['backwardClock'])
        again = guest.install(self.root, blob=BLOB, operation_id=OP, probe=PROBE, unit=UNIT, now_unix=NOW,
                              synchronized=True, sshd_version=VERSION, sshd_config=CONFIG, passwd_text=passwd())
        self.assertTrue(again['idempotent'])
        self.assertEqual(self.auth.read_bytes(), written)
        with self.assertRaises(guest.GuestRefused) as caught:
            guest.install(self.root, blob=BLOB, operation_id=OP, probe=PROBE, unit=UNIT, now_unix=NOW + 60,
                          synchronized=True, sshd_version=VERSION, sshd_config=CONFIG, passwd_text=passwd())
        self.assertEqual(caught.exception.code, 'duplicate-key')
        self.assertEqual(self.auth.read_bytes(), written)
        with self.assertRaises(guest.GuestRefused) as caught:
            guest.install(self.root, blob=BLOB, operation_id='cd' * 16, probe=PROBE, unit=UNIT, now_unix=NOW,
                          synchronized=True, sshd_version=VERSION, sshd_config=CONFIG, passwd_text=passwd())
        self.assertEqual(caught.exception.code, 'duplicate-key')
        self.assertEqual((self.root / 'root' / '.ssh' / 'authorized_keys').read_bytes(), b'ROOT KEY\n')

    def test_clock_version_and_unsync_do_not_write(self):
        original = self.auth.read_bytes()
        for kwargs in (
            {'synchronized': False, 'sshd_version': VERSION},
            {'synchronized': True, 'sshd_version': 'OpenSSH_9.6p1 Ubuntu-3ubuntu13.20'},
        ):
            with self.assertRaises(guest.GuestRefused):
                guest.install(self.root, blob=BLOB, operation_id=OP, probe=PROBE, unit=UNIT, now_unix=NOW,
                              sshd_config=CONFIG, passwd_text=passwd(), **kwargs)
            self.assertEqual(self.auth.read_bytes(), original)

    def test_revoke_keeps_concurrent_bytes_and_ignores_a_stale_backup(self):
        receipt = guest.install(self.root, blob=BLOB, operation_id=OP, probe=PROBE, unit=UNIT, now_unix=NOW,
                                synchronized=True, sshd_version=VERSION, sshd_config=CONFIG, passwd_text=passwd())
        extra = b'ssh-ed25519 ' + b'C' * 40 + b' concurrent\n'
        with self.auth.open('ab') as handle:
            handle.write(extra)
        backup = self.auth.with_name('authorized_keys.roseos-prev')
        backup.write_bytes(b'STALE\n')
        result = guest.revoke(self.root, blob=BLOB, comment=receipt['comment'], passwd_text=passwd(),
                              exact_line=receipt['line'])
        self.assertTrue(result['removed'])
        self.assertEqual(self.auth.read_bytes(), (FOREIGN + '\n').encode() + extra)
        self.assertEqual(backup.read_bytes(), b'STALE\n')
        absent = guest.revoke(self.root, blob=BLOB, comment=receipt['comment'], passwd_text=passwd(),
                              exact_line=receipt['line'])
        self.assertTrue(absent['absent'])
        self.assertFalse(absent['rewritten'])
        self.assertEqual(self.auth.read_bytes(), (FOREIGN + '\n').encode() + extra)

    def test_revoke_refuses_a_line_that_is_not_the_recorded_one(self):
        receipt = guest.install(self.root, blob=BLOB, operation_id=OP, probe=PROBE, unit=UNIT, now_unix=NOW,
                                synchronized=True, sshd_version=VERSION, sshd_config=CONFIG, passwd_text=passwd())
        current = self.auth.read_bytes()
        with self.assertRaises(guest.GuestRefused) as caught:
            guest.revoke(self.root, blob=BLOB, comment=receipt['comment'], passwd_text=passwd(),
                         exact_line=receipt['line'] + '-nope')
        self.assertEqual(caught.exception.code, 'revoke-not-exact')
        self.assertEqual(self.auth.read_bytes(), current)

    def test_journal_before_install_and_every_failure_stage(self):
        original = self.auth.read_bytes()
        record = install.begin(self.out, OP, BLOB)
        self.assertEqual(record['state'], 'revoke-required')
        self.assertIsNone(record['exactLine'])
        self.assertFalse(record['guestAuthenticated'])
        self.assertEqual(self.auth.read_bytes(), original)
        with self.assertRaises(install.Refused) as caught:
            install.begin(self.out, 'cd' * 16, 'D' * 68)
        self.assertEqual(caught.exception.code, 'unrevoked-journal')
        stale = evidence(now=NOW - 600)
        with self.assertRaises(install.Refused) as caught:
            install.apply_install(self.out, self.root, stale, NOW)
        self.assertEqual(caught.exception.code, 'clock-skew')
        self.assertEqual(self.auth.read_bytes(), original)
        unsync = evidence()
        unsync['synchronized'] = False
        with self.assertRaises(install.Refused) as caught:
            install.apply_install(self.out, self.root, unsync, NOW)
        self.assertEqual(caught.exception.code, 'clock-unsynchronized')
        wrong = evidence(version='OpenSSH_9.6p1 Ubuntu-3ubuntu13.20')
        with self.assertRaises(install.Refused) as caught:
            install.apply_install(self.out, self.root, wrong, NOW)
        self.assertEqual(caught.exception.code, 'sshd-version')
        self.assertEqual(self.auth.read_bytes(), original)
        self.assertEqual(install.journal_status(self.out), 'revoke-required')

    def test_receipt_loss_restart_and_inspect_gate(self):
        install.begin(self.out, OP, BLOB)
        # Guest write succeeds and the controller dies before recording the line.
        guest.install(self.root, blob=BLOB, operation_id=OP, probe=PROBE, unit=UNIT, now_unix=NOW,
                      synchronized=True, sshd_version=VERSION, sshd_config=CONFIG, passwd_text=passwd())
        self.assertIn(BLOB, self.auth.read_text())
        with self.assertRaises(install.Refused):
            install.begin(self.out, OP, BLOB)
        revoked = install.resume_revoke(self.out, self.root, passwd())
        self.assertTrue(revoked['removed'])
        self.assertNotIn(BLOB, self.auth.read_text())
        self.assertFalse(revoked['guestAuthenticated'])
        self.assertFalse(revoked['clean'])
        with self.assertRaises(install.Refused):
            install.accept_inspect(self.out, {'authPathsRead': 2, 'possessMarkerLines': 1, 'blobPresent': False})
        done = install.accept_inspect(self.out, {'authPathsRead': 2, 'possessMarkerLines': 0, 'blobPresent': False})
        self.assertEqual(done['state'], 'revoked')
        self.assertTrue(done['clean'])
        self.assertFalse(done['guestAuthenticated'])
        self.assertEqual(install.journal_status(self.out), 'revoked')

    def test_recorded_install_then_revoke_preserves_a_later_foreign_line(self):
        install.begin(self.out, OP, BLOB)
        receipt = install.apply_install(self.out, self.root, evidence(), NOW)
        self.assertFalse(receipt['guestAuthenticated'])
        with self.auth.open('ab') as handle:
            handle.write(b'ssh-ed25519 ' + b'E' * 40 + b' later\n')
        result = install.resume_revoke(self.out, self.root, passwd())
        self.assertTrue(result['removed'])
        text = self.auth.read_text()
        self.assertNotIn(BLOB, text)
        self.assertIn('later', text)
        self.assertIn('foreign', text)
        with self.assertRaises(install.Refused) as caught:
            install.apply_install(self.out, self.root, evidence(), NOW)
        self.assertEqual(caught.exception.code, 'install-already-recorded')

    def test_same_bootstrap_retry_is_idempotent_and_a_new_expiry_refuses(self):
        binding = {'action': 'install', 'blob': BLOB, 'operationId': OP, 'probe': PROBE, 'unit': UNIT,
                   'evidence': evidence()}
        script = install.render_bootstrap(binding)
        path = self.out / 'bootstrap.py'
        path.write_text(script)
        first = subprocess.run([sys.executable, '-B', str(path), '--root', str(self.root)],
                               capture_output=True, text=True, check=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        count = self.auth.read_text().count(BLOB)
        second = subprocess.run([sys.executable, '-B', str(path), '--root', str(self.root)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.auth.read_text().count(BLOB), count)
        later = dict(binding)
        later['evidence'] = evidence(now=NOW + 90)
        again = self.out / 'later.py'
        again.write_text(install.render_bootstrap(later))
        third = subprocess.run([sys.executable, '-B', str(again), '--root', str(self.root)],
                               capture_output=True, text=True, check=False)
        self.assertNotEqual(third.returncode, 0)
        self.assertIn('duplicate-key', third.stderr)
        self.assertEqual(self.auth.read_text().count(BLOB), 1)
        self.assertIn('ROOT KEY', (self.root / 'root' / '.ssh' / 'authorized_keys').read_text())

    def test_watchdog_kills_a_hung_child_without_the_client(self):
        command = guest.forced_command(HUNG, UNIT, 1)
        started = time.monotonic()
        completed = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=8)
        self.assertLess(time.monotonic() - started, 6)
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(self._sleep30())

    def test_plan_and_execute_cli_do_not_touch_the_guest(self):
        completed = subprocess.run(
            [sys.executable, '-B', str(HERE.parent / 'yogi_possession.py'), 'guest-install-execute'],
            capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 1)
        self.assertIn('not authorized', completed.stderr)
        plan = subprocess.run(
            [sys.executable, '-B', str(HERE.parent / 'yogi_possession.py'), 'guest-install-plan', '--out', str(self.out)],
            capture_output=True, text=True, check=False)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        body = json.loads((self.out / 'guest-install-plan.json').read_text())
        self.assertFalse(body['liveExecute'])
        self.assertFalse(body['guestAuthenticated'])
        self.assertIn('9.2p1', body['qualifiedParser'])
        self.assertNotIn('ran an OpenSSH 9.2', body['qualifiedParser'])
        source = (HERE.parent / 'yogi_possess_guest.py').read_text()
        self.assertNotIn('systemctl enable', source)
        self.assertNotIn('google-guest-agent', source)
        self.assertNotIn('gcloud', (HERE.parent / 'yogi_possess_install.py').read_text())

    def _sleep30(self):
        return bool(subprocess.run(['pgrep', '-f', 'sleep 30'], capture_output=True).stdout.strip())


def _sshd_available():
    return shutil.which('ssh') and Path('/usr/sbin/sshd').is_file()


@unittest.skipUnless(_sshd_available(), '/usr/sbin/sshd is not installed')
class PossessSshdTest(unittest.TestCase):
    """Local sshd only. The binary's version is recorded and is not claimed to be 9.2."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(Path.home()))
        self.base = Path(self.tmp.name)
        os.chmod(self.base, 0o755)
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-f', str(self.base / 'host'), '-N', ''], check=True)
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-f', str(self.base / 'client'), '-N', ''], check=True)
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-f', str(self.base / 'other'), '-N', ''], check=True)
        self.keys = self.base / 'keys'
        self.keys.mkdir()
        os.chmod(self.keys, 0o700)
        self.auth = self.keys / 'authorized_keys'
        self.port = self._port()
        self.pub = (self.base / 'client.pub').read_text().split()[1]
        config = self.base / 'sshd_config'
        config.write_text(
            f'Port {self.port}\nListenAddress 127.0.0.1\nHostKey {self.base / "host"}\n'
            f'PidFile {self.base / "sshd.pid"}\nAuthorizedKeysFile {self.auth}\n'
            'PasswordAuthentication no\nKbdInteractiveAuthentication no\nUsePAM no\n'
            'StrictModes yes\nPubkeyAuthentication yes\n')
        known = (self.base / 'host.pub').read_text().strip()
        (self.base / 'known').write_text(f'[127.0.0.1]:{self.port} {known}\n')
        wrong = (self.base / 'other.pub').read_text().strip()
        (self.base / 'wrong').write_text(f'[127.0.0.1]:{self.port} {wrong}\n')
        self.proc = subprocess.Popen(
            ['/usr/sbin/sshd', '-f', str(config), '-E', str(self.base / 'sshd.log'), '-D'])
        self.version = subprocess.run(['ssh', '-V'], capture_output=True, text=True).stderr.strip()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not (self.base / 'sshd.pid').exists():
            time.sleep(0.05)

    def tearDown(self):
        if (self.base / 'sshd.pid').exists():
            os.kill(int((self.base / 'sshd.pid').read_text()), 15)
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        subprocess.run(['pkill', '-f', 'sleep 30'], check=False)
        self.tmp.cleanup()

    def _port(self):
        import socket
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    def _write(self, options, blob=None):
        material = blob or self.pub
        self.auth.write_text(f'{options} ssh-ed25519 {material} client\n')
        os.chmod(self.auth, 0o600)

    def _ssh(self, known, extra=None, remote='echo CLIENT'):
        command = [
            'ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', f'UserKnownHostsFile={known}', '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'IdentitiesOnly=yes', '-i', str(self.base / 'client'),
            '-o', 'PreferredAuthentications=publickey', '-o', 'PasswordAuthentication=no',
            '-o', 'RequestTTY=no', '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no',
            '-p', str(self.port), '-l', os.environ.get('USER') or 'ubuntu',
        ]
        command.extend(extra or [])
        command.extend(['127.0.0.1', remote])
        return subprocess.run(command, capture_output=True, text=True, timeout=8)

    def test_fixture_records_the_local_binary_separately_from_the_9_2_qualification(self):
        self.assertTrue(self.version.startswith('OpenSSH_'))
        self.assertIn('did not run an OpenSSH 9.2 binary', guest.QUALIFIED_PARSER)

    def test_restricted_expiry_forced_command_known_hosts_and_malformed_keys(self):
        future = guest.format_expiry(int(time.time()) + 600)
        past = guest.format_expiry(int(time.time()) - 60)
        forced = 'echo FORCED'
        # A plain echo is not our wrapper, so build the option text directly for the fixture.
        self._write(f'restrict,command="{forced}",expiry-time="{future}"')
        good = self._ssh(self.base / 'known')
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(good.stdout.strip(), 'FORCED')
        self.assertNotIn('CLIENT', good.stdout)
        missing = self._ssh(self.base / 'absent')
        self.assertNotEqual(missing.returncode, 0)
        self.assertNotIn('FORCED', missing.stdout)
        mismatch = self._ssh(self.base / 'wrong')
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertIn('Host key verification failed', mismatch.stderr)
        self.assertNotIn('FORCED', mismatch.stdout)
        self._write(f'restrict,command="{forced}",expiry-time="{past}"')
        expired = self._ssh(self.base / 'known')
        self.assertNotEqual(expired.returncode, 0)
        self.assertIn('Permission denied', expired.stderr)
        self.assertNotIn('FORCED', expired.stdout)
        self._write('restrict,command="echo FORCED",expiry-time="2026-11-07T00:00:00Z"')
        malformed = self._ssh(self.base / 'known')
        self.assertNotEqual(malformed.returncode, 0)
        self.assertNotIn('FORCED', malformed.stdout)
        self._write('not-an-option,restrict,command="echo FORCED"')
        unknown = self._ssh(self.base / 'known')
        self.assertNotEqual(unknown.returncode, 0)
        self.assertNotIn('FORCED', unknown.stdout)

    def test_restrict_blocks_pty_forwarding_and_user_rc(self):
        future = guest.format_expiry(int(time.time()) + 600)
        self._write(f'restrict,command="python3 -c \'import os; print(os.isatty(0))\'",expiry-time="{future}"')
        pty = self._ssh(self.base / 'known', extra=['-tt'], remote='echo CLIENT')
        self.assertIn('PTY allocation request failed', pty.stderr)
        self.assertNotIn('CLIENT', pty.stdout)
        self.assertNotIn('False', pty.stdout)
        self._write(f'restrict,command="sleep 2",expiry-time="{future}"')
        command = self._ssh_argv(self.base / 'known')
        command[-2:-2] = ['-v', '-L127.0.0.1:34567:127.0.0.1:9']
        forwarded = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.4)
        import socket
        try:
            probe = socket.create_connection(('127.0.0.1', 34567), timeout=1)
            probe.close()
        except OSError:
            pass
        try:
            out, err = forwarded.communicate(timeout=4)
        except subprocess.TimeoutExpired:
            forwarded.kill()
            out, err = forwarded.communicate()
        self.assertIn('administratively prohibited', err, err)
        self.assertNotIn('CLIENT', out or '')
        rc = Path.home() / '.ssh' / 'rc'
        ssh_dir = rc.parent
        created_dir = not ssh_dir.exists()
        previous = rc.read_bytes() if rc.exists() else None
        ssh_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(ssh_dir, 0o700)
        marker = self.base / 'rc-ran'
        try:
            rc.write_text(f'#!/bin/sh\ntouch {marker}\n')
            os.chmod(rc, 0o700)
            self._ssh(self.base / 'known')
            self.assertFalse(marker.exists())
        finally:
            if previous is None:
                rc.unlink(missing_ok=True)
            else:
                rc.write_bytes(previous)
            if created_dir:
                try:
                    ssh_dir.rmdir()
                except OSError:
                    pass

    def test_disconnect_and_hung_probe_do_not_leave_a_child(self):
        command = guest.forced_command(HUNG, UNIT, 30).replace('"', '\\"')
        future = guest.format_expiry(int(time.time()) + 600)
        self._write(f'restrict,command="{command}",expiry-time="{future}"')
        proc = subprocess.Popen(self._ssh_argv(self.base / 'known'), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(0.6)
        self.assertTrue(self._sleep30_count())
        proc.kill()
        proc.wait(timeout=3)
        proc.stdout.close()
        proc.stderr.close()
        time.sleep(1.2)
        self.assertFalse(self._sleep30_count())
        short = guest.forced_command(HUNG, UNIT, 1).replace('"', '\\"')
        self._write(f'restrict,command="{short}",expiry-time="{future}"')
        completed = self._ssh(self.base / 'known')
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(self._sleep30_count())

    def _ssh_argv(self, known):
        return [
            'ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', f'UserKnownHostsFile={known}', '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'IdentitiesOnly=yes', '-i', str(self.base / 'client'),
            '-o', 'RequestTTY=no', '-p', str(self.port), '-l', os.environ.get('USER') or 'ubuntu',
            '127.0.0.1', 'echo CLIENT',
        ]

    def _sleep30_count(self):
        return bool(subprocess.run(['pgrep', '-f', 'sleep 30'], capture_output=True).stdout.strip())


if __name__ == '__main__':
    unittest.main()
