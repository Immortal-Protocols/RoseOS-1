#!/usr/bin/env python3
"""Opt-in guest writer for one temporary pixelbuild authorized_keys line.

This is not the read-only inspect guest. It does not enable ssh, sshd, the
guest agent, OS Login, or the runner, and it does not create an account.
``--root /`` is refused unless ROSEOS_POSSESS_ALLOW_ROOT=1. The possession
command refuses live execution separately. Tests call the functions with a
synthetic root.
"""
import argparse
import base64
import datetime
import errno
import json
import os
from pathlib import Path
import re
import stat
import sys

ACCOUNT = 'pixelbuild'
HOME_NAME = 'home'
USER_DIR = 'pixelbuild'
SSH_DIR = '.ssh'
AUTH_NAME = 'authorized_keys'
HOME_MODES = {0o755, 0o750, 0o700}
SSH_MODE = 0o700
FILE_MODE = 0o600
ANCESTOR_MODE = 0o755
MAX_EXPIRY_SECONDS = 20 * 60
MAX_DEADLINE_SECONDS = 45
MAX_AUTH_BYTES = 1_000_000
COMMENT_RE = re.compile(r'^roseos-yogi-possess-[0-9a-f]{32}$')
EXPIRY_RE = re.compile(r'^\d{14}Z$')
BLOB_RE = re.compile(r'^[A-Za-z0-9+/]+={0,2}$')
VERSION_RE = re.compile(r'^OpenSSH_9\.2p1(\s|$)')
BINDING_JSON = None
BACKWARD_CLOCK = (
    'sshd compares expiry-time to the guest clock. A guest clock stepped backward after '
    'install can accept the key until that clock passes the expiry. Nothing here prevents '
    'that step. The 20 minute value is the sshd UTC expiry, not a controller-enforced kill.'
)
QUALIFIED_PARSER = (
    'expiry-time grammar is the OpenSSH 9.2p1 parser: YYYYMMDDHHMMSSZ means UTC. '
    'This source did not run an OpenSSH 9.2 binary. A local sshd fixture reports its own version.'
)


class GuestRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def comment_for(operation_id):
    if not isinstance(operation_id, str) or not re.fullmatch(r'[0-9a-f]{32}', operation_id):
        raise GuestRefused('operation-id')
    return 'roseos-yogi-possess-' + operation_id


def version_allowed(text):
    first = (text or '').splitlines()[0].strip() if isinstance(text, str) else ''
    return VERSION_RE.match(first) is not None


def format_expiry(unix):
    return datetime.datetime.fromtimestamp(int(unix), datetime.timezone.utc).strftime('%Y%m%d%H%M%SZ')


def parse_expiry(text):
    if not isinstance(text, str) or not EXPIRY_RE.fullmatch(text):
        raise GuestRefused('expiry-format')
    parsed = datetime.datetime.strptime(text, '%Y%m%d%H%M%SZ').replace(tzinfo=datetime.timezone.utc)
    return int(parsed.timestamp())


def account_from_passwd(text):
    if not isinstance(text, str):
        raise GuestRefused('passwd')
    for line in text.splitlines():
        parts = line.split(':')
        if len(parts) < 7 or parts[0] != ACCOUNT:
            continue
        try:
            uid = int(parts[2])
            gid = int(parts[3])
        except ValueError:
            raise GuestRefused('passwd-uid') from None
        home, shell = parts[5], parts[6]
        if uid <= 0 or gid < 0 or home != '/home/pixelbuild':
            raise GuestRefused('passwd-account')
        if shell in {'/bin/false', '/usr/bin/false', '/sbin/nologin', '/usr/sbin/nologin'} or shell.endswith('/nologin'):
            raise GuestRefused('passwd-shell')
        if not shell.startswith('/') or shell.endswith('/false'):
            raise GuestRefused('passwd-shell')
        return uid, gid, shell
    raise GuestRefused('passwd-missing')


def sshd_config_decision(text):
    """Root sshd_config only. Include and Match make the effective path unproven."""
    if not isinstance(text, str) or not text.strip():
        return {'ok': False, 'code': 'sshd-config-unreadable'}
    authorized = None
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        if re.match(r'(?i)^(include|match)\b', line):
            return {'ok': False, 'code': 'sshd-config-unproven'}
        if re.match(r'(?i)^authorizedkeysfile\b', line):
            if authorized is not None:
                return {'ok': False, 'code': 'sshd-config-unproven'}
            authorized = line.split(None, 1)[1] if len(line.split(None, 1)) == 2 else ''
    if authorized is None:
        return {'ok': True, 'code': 'default-authorized-keys', 'path': '.ssh/authorized_keys'}
    tokens = authorized.split()
    allowed = {'.ssh/authorized_keys', '%h/.ssh/authorized_keys', '/home/pixelbuild/.ssh/authorized_keys'}
    if tokens != [tokens[0]] or tokens[0] not in allowed:
        return {'ok': False, 'code': 'authorized-keys-path', 'path': authorized}
    return {'ok': True, 'code': 'authorized-keys-path', 'path': tokens[0]}


def ancestor_allows(uid, mode, require_root, account_uid):
    """`/home` is root-owned on the guest. A synthetic root may be owned by the account."""
    if stat.S_IMODE(mode) != ANCESTOR_MODE:
        raise GuestRefused('ancestor-mode')
    if require_root:
        if uid != 0:
            raise GuestRefused('ancestor-owner')
        return
    if uid not in (0, account_uid):
        raise GuestRefused('ancestor-owner')


def evidence_from_outputs(*, version_text, config_text, passwd_text, now, sync_text):
    if not isinstance(sync_text, str) or sync_text.strip() != 'yes':
        raise GuestRefused('clock-unsynchronized')
    if not version_allowed(version_text):
        raise GuestRefused('sshd-version')
    decision = sshd_config_decision(config_text)
    if not decision['ok']:
        raise GuestRefused(decision['code'])
    uid, gid, shell = account_from_passwd(passwd_text)
    if not isinstance(now, (int, float)):
        raise GuestRefused('clock')
    return {
        'guestUnix': int(now), 'synchronized': True, 'sshdVersion': version_text.splitlines()[0].strip(),
        'sshdConfig': config_text, 'passwd': passwd_text, 'uid': uid, 'gid': gid, 'shell': shell,
        'authorizedKeys': decision['path'], 'qualifiedParser': QUALIFIED_PARSER,
        'backwardClock': BACKWARD_CLOCK,
    }


def watchdog_source(deadline, probe, unit):
    """Self-contained program. It does not import this module and does not read the client command."""
    if not isinstance(deadline, int) or not 1 <= deadline <= MAX_DEADLINE_SECONDS:
        raise GuestRefused('deadline')
    if not isinstance(probe, str) or not probe.strip() or '\x00' in probe:
        raise GuestRefused('probe')
    if not isinstance(unit, str) or not re.fullmatch(r'[A-Za-z0-9@._:-]+', unit):
        raise GuestRefused('unit')
    if 'SSH_ORIGINAL_COMMAND' in probe or 'shell=True' in probe or 'os.system' in probe:
        raise GuestRefused('probe')
    probe_b64 = base64.b64encode(probe.encode()).decode()
    return (
        'import base64, ctypes, errno, os, signal, sys\n'
        f'DEADLINE_SECONDS = {deadline}\n'
        f'PROBE_B64 = {json.dumps(probe_b64)}\n'
        f'UNIT = {json.dumps(unit)}\n'
        'def _kill_group(_signum=None, _frame=None):\n'
        '    try:\n'
        '        os.killpg(os.getpgrp(), 9)\n'
        '    except OSError:\n'
        '        pass\n'
        '    os._exit(124)\n'
        'try:\n'
        '    os.setpgrp()\n'
        'except OSError as error:\n'
        '    if error.errno != errno.EPERM or os.getpgrp() != os.getpid():\n'
        '        os._exit(125)\n'
        'libc = ctypes.CDLL(None, use_errno=True)\n'
        'if libc.prctl(1, signal.SIGTERM) != 0:\n'
        '    os._exit(125)\n'
        'if os.getppid() == 1:\n'
        '    _kill_group()\n'
        'signal.signal(signal.SIGTERM, _kill_group)\n'
        'signal.signal(signal.SIGHUP, _kill_group)\n'
        'signal.signal(signal.SIGALRM, _kill_group)\n'
        'signal.alarm(DEADLINE_SECONDS)\n'
        'pid = os.fork()\n'
        'if pid == 0:\n'
        '    if libc.prctl(1, signal.SIGTERM) != 0:\n'
        '        os._exit(125)\n'
        '    if os.getppid() == 1:\n'
        '        _kill_group()\n'
        '    signal.signal(signal.SIGTERM, _kill_group)\n'
        '    signal.signal(signal.SIGHUP, _kill_group)\n'
        '    sys.argv = ["probe", UNIT]\n'
        '    exec(compile(base64.b64decode(PROBE_B64).decode(), "<probe>", "exec"),\n'
        '         {"__name__": "probe", "__builtins__": __builtins__})\n'
        '    os._exit(0)\n'
        'while True:\n'
        '    try:\n'
        '        _pid, status = os.wait()\n'
        '        break\n'
        '    except InterruptedError:\n'
        '        continue\n'
        'os._exit(os.waitstatus_to_exitcode(status))\n'
    )


def forced_command(probe, unit, deadline=MAX_DEADLINE_SECONDS):
    """One python -I -c command. sshd runs it instead of the client command."""
    source = watchdog_source(deadline, probe, unit)
    blob = base64.b64encode(source.encode()).decode()
    if not BLOB_RE.fullmatch(blob):
        raise GuestRefused('command-encoding')
    return 'python3 -I -c "import base64; exec(base64.b64decode(\'' + blob + '\'))"'


def _command_payload(command):
    match = re.fullmatch(
        r'python3 -I -c "import base64; exec\(base64\.b64decode\(\'([A-Za-z0-9+/=]+)\'\)\)"',
        command or '',
    )
    if not match:
        raise GuestRefused('forced-command')
    try:
        source = base64.b64decode(match.group(1)).decode()
    except (ValueError, UnicodeError):
        raise GuestRefused('forced-command') from None
    deadline = re.search(r'(?m)^DEADLINE_SECONDS = ([1-9][0-9]*)$', source)
    probe_b64 = re.search(r'(?m)^PROBE_B64 = "([A-Za-z0-9+/=]+)"$', source)
    unit = re.search(r'(?m)^UNIT = ("(?:\\.|[^"\\])*")$', source)
    if not deadline or not probe_b64 or not unit:
        raise GuestRefused('forced-command')
    try:
        probe = base64.b64decode(probe_b64.group(1)).decode()
        unit_text = json.loads(unit.group(1))
        rebuilt = watchdog_source(int(deadline.group(1)), probe, unit_text)
    except (GuestRefused, ValueError, UnicodeError, json.JSONDecodeError):
        raise GuestRefused('forced-command') from None
    if rebuilt != source or forced_command(probe, unit_text, int(deadline.group(1))) != command:
        raise GuestRefused('forced-command')
    return {'deadline': int(deadline.group(1)), 'probe': probe, 'unit': unit_text}


def quote_command(command):
    if not isinstance(command, str) or '\n' in command or '\\' in command:
        raise GuestRefused('command-escape')
    _command_payload(command)
    return command.replace('"', '\\"')


def authorized_line(blob, comment, expiry, command):
    if not isinstance(blob, str) or not BLOB_RE.fullmatch(blob) or len(blob) < 32 or len(blob) > 200:
        raise GuestRefused('blob')
    if not isinstance(comment, str) or not COMMENT_RE.fullmatch(comment):
        raise GuestRefused('comment')
    parse_expiry(expiry)
    quoted = quote_command(command)
    return f'restrict,command="{quoted}",expiry-time="{expiry}" ssh-ed25519 {blob} {comment}'


def parse_key_line(line):
    """Blob and comment from the key at the end of a line. Options may contain spaces."""
    match = re.search(r'(?:^|\s)ssh-ed25519 ([A-Za-z0-9+/]+=*) (\S+)$', line or '')
    if not match:
        return None
    return match.group(1), match.group(2)


def _open_dir(parent, name, *, uid, modes):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise GuestRefused('symlink') from None
        raise GuestRefused('directory-open') from None
    st = os.fstat(fd)
    mode = stat.S_IMODE(st.st_mode)
    if not stat.S_ISDIR(st.st_mode) or mode not in modes or mode & 0o022 or st.st_uid != uid:
        os.close(fd)
        if mode == 0o600:
            raise GuestRefused('directory-mode')
        if st.st_uid != uid:
            raise GuestRefused('directory-owner')
        raise GuestRefused('directory-mode')
    return fd


def _read_auth(dirfd):
    try:
        fd = os.open(AUTH_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dirfd)
    except FileNotFoundError:
        raise GuestRefused('auth-absent') from None
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise GuestRefused('symlink') from None
        raise GuestRefused('auth-open') from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise GuestRefused('auth-type')
        if stat.S_IMODE(st.st_mode) != FILE_MODE:
            raise GuestRefused('auth-mode')
        if st.st_nlink != 1:
            raise GuestRefused('auth-hardlink')
        data = b''
        while len(data) <= MAX_AUTH_BYTES:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
        if len(data) > MAX_AUTH_BYTES:
            raise GuestRefused('auth-too-large')
        return st, data
    finally:
        os.close(fd)


def _split_lines(data):
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        raise GuestRefused('auth-encoding') from None
    if '\r' in text or '\x00' in text:
        raise GuestRefused('auth-encoding')
    if text == '':
        return [], False
    newline = text.endswith('\n')
    parts = text.split('\n')
    if newline:
        parts = parts[:-1]
    return parts, newline


def _join_lines(parts, newline):
    body = '\n'.join(parts)
    if newline:
        body += '\n'
    elif parts:
        body = '\n'.join(parts)
    return body.encode()


def _write_auth(dirfd, uid, gid, previous_inode, payload):
    tmp = f'.authorized_keys.{os.getpid()}.tmp'
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, FILE_MODE, dir_fd=dirfd)
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, FILE_MODE)
        view = payload
        while view:
            wrote = os.write(fd, view)
            if wrote <= 0:
                raise GuestRefused('auth-write')
            view = view[wrote:]
        os.fsync(fd)
        st = os.fstat(fd)
        if st.st_uid != uid or st.st_gid != gid or stat.S_IMODE(st.st_mode) != FILE_MODE or st.st_nlink != 1:
            raise GuestRefused('auth-write')
    except GuestRefused:
        os.close(fd)
        try:
            os.unlink(tmp, dir_fd=dirfd)
        except OSError:
            pass
        raise
    except OSError:
        os.close(fd)
        try:
            os.unlink(tmp, dir_fd=dirfd)
        except OSError:
            pass
        raise GuestRefused('auth-write') from None
    os.close(fd)
    current = os.fstat(dirfd)
    if current.st_ino != previous_inode:
        try:
            os.unlink(tmp, dir_fd=dirfd)
        except OSError:
            pass
        raise GuestRefused('directory-race')
    os.rename(f'/proc/self/fd/{dirfd}/{tmp}', f'/proc/self/fd/{dirfd}/{AUTH_NAME}')
    os.fsync(dirfd)
    checked, written = _read_auth(dirfd)
    if checked.st_uid != uid or written != payload:
        raise GuestRefused('auth-write')
    return written


def _walk(root, uid, gid, require_root_ancestor):
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    fds = [root_fd]
    try:
        home_fd = _open_dir(root_fd, HOME_NAME, uid=0 if require_root_ancestor else uid, modes={ANCESTOR_MODE})
        fds.append(home_fd)
        if require_root_ancestor:
            ancestor_allows(os.fstat(home_fd).st_uid, os.fstat(home_fd).st_mode, True, uid)
        user_fd = _open_dir(home_fd, USER_DIR, uid=uid, modes=HOME_MODES)
        fds.append(user_fd)
        ssh_fd = _open_dir(user_fd, SSH_DIR, uid=uid, modes={SSH_MODE})
        fds.append(ssh_fd)
        st, data = _read_auth(ssh_fd)
        if st.st_uid != uid or st.st_gid != gid:
            raise GuestRefused('auth-owner')
        return ssh_fd, fds, os.fstat(ssh_fd).st_ino, data
    except Exception:
        for fd in reversed(fds):
            os.close(fd)
        raise


def _close(fds):
    for fd in reversed(fds):
        try:
            os.close(fd)
        except OSError:
            pass


def _mutate(root, uid, gid, *, mode, blob, comment, line=None):
    require_root = os.path.abspath(root) == '/'
    ssh_fd, fds, inode, data = _walk(root, uid, gid, require_root)
    try:
        parts, newline = _split_lines(data)
        parsed = []
        for item in parts:
            found = parse_key_line(item)
            parsed.append(found)
        blob_hits = [item for item, found in zip(parts, parsed) if found and found[0] == blob]
        comment_hits = [item for item, found in zip(parts, parsed) if found and found[1] == comment]
        if mode == 'install':
            if line in parts:
                return {'rewritten': False, 'idempotent': True, 'line': line}
            if blob_hits or comment_hits:
                raise GuestRefused('duplicate-key')
            updated = list(parts)
            updated.append(line)
            payload = _join_lines(updated, True if newline or parts else True)
            # A non-empty original without a trailing newline keeps foreign bytes and gains a separator.
            if parts and not newline:
                payload = ('\n'.join(parts) + '\n' + line).encode()
            elif not parts:
                payload = (line + '\n').encode()
            else:
                payload = ('\n'.join(parts) + '\n' + line + '\n').encode()
            written = _write_auth(ssh_fd, uid, gid, inode, payload)
            if not written.startswith(data.splitlines()[0]) and data:
                raise GuestRefused('auth-write')
            return {'rewritten': True, 'idempotent': False, 'line': line}
        matches = [item for item in comment_hits if item in blob_hits]
        if len(matches) > 1:
            raise GuestRefused('revoke-ambiguous')
        if any(item not in matches for item in comment_hits) or any(item not in matches for item in blob_hits):
            raise GuestRefused('revoke-foreign')
        if not matches:
            return {'rewritten': False, 'removed': False, 'absent': True}
        if line is not None and matches != [line]:
            raise GuestRefused('revoke-not-exact')
        kept = [item for item in parts if item != matches[0]]
        if newline:
            payload = ('\n'.join(kept) + ('\n' if kept else '')).encode() if kept else b''
            if kept:
                payload = ('\n'.join(kept) + '\n').encode()
            else:
                payload = b''
        else:
            payload = '\n'.join(kept).encode()
        written = _write_auth(ssh_fd, uid, gid, inode, payload)
        if matches[0].encode() in written:
            raise GuestRefused('revoke-residue')
        return {'rewritten': True, 'removed': True, 'absent': False, 'line': matches[0]}
    finally:
        _close([ssh_fd] if ssh_fd not in fds else fds)


def install(root, *, blob, operation_id, probe, unit, now_unix, synchronized, sshd_version, sshd_config, passwd_text):
    if synchronized is not True:
        raise GuestRefused('clock-unsynchronized')
    if not version_allowed(sshd_version):
        raise GuestRefused('sshd-version')
    decision = sshd_config_decision(sshd_config)
    if not decision['ok']:
        raise GuestRefused(decision['code'])
    uid, gid, shell = account_from_passwd(passwd_text)
    if not isinstance(now_unix, (int, float)):
        raise GuestRefused('clock')
    expiry_unix = int(now_unix) + MAX_EXPIRY_SECONDS
    expiry = format_expiry(expiry_unix)
    if parse_expiry(expiry) != expiry_unix:
        raise GuestRefused('expiry-format')
    command = forced_command(probe, unit, MAX_DEADLINE_SECONDS)
    comment = comment_for(operation_id)
    line = authorized_line(blob, comment, expiry, command)
    result = _mutate(root, uid, gid, mode='install', blob=blob, comment=comment, line=line)
    result.update({
        'expiry': expiry, 'expiryUnix': expiry_unix, 'guestUnix': int(now_unix), 'synchronized': True,
        'sshdVersion': sshd_version.splitlines()[0].strip(), 'shell': shell, 'comment': comment,
        'backwardClock': BACKWARD_CLOCK, 'qualifiedParser': QUALIFIED_PARSER,
        'deadlineSeconds': MAX_DEADLINE_SECONDS, 'maxExpirySeconds': MAX_EXPIRY_SECONDS,
        'configPath': decision['path'],
    })
    return result


def revoke(root, *, blob, comment, passwd_text, exact_line=None):
    uid, gid, _shell = account_from_passwd(passwd_text)
    if not isinstance(comment, str) or not COMMENT_RE.fullmatch(comment):
        raise GuestRefused('comment')
    if not isinstance(blob, str) or not BLOB_RE.fullmatch(blob):
        raise GuestRefused('blob')
    return _mutate(root, uid, gid, mode='revoke', blob=blob, comment=comment, line=exact_line)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Opt-in possess guest install or revoke. Refuses --root / by default.')
    parser.add_argument('--root', required=True)
    parser.add_argument('--binding', help='JSON binding. Required when the source still has no embedded binding.')
    args = parser.parse_args(argv)
    if os.path.abspath(args.root) == '/' and os.environ.get('ROSEOS_POSSESS_ALLOW_ROOT') != '1':
        print('refusing --root /', file=sys.stderr)
        return 1
    if args.binding:
        binding = json.loads(Path(args.binding).read_text())
    elif isinstance(BINDING_JSON, str):
        binding = json.loads(BINDING_JSON)
    else:
        print('no binding', file=sys.stderr)
        return 1
    try:
        if os.path.abspath(args.root) == '/':
            import subprocess
            import time
            version = subprocess.run(['sshd', '-V'], capture_output=True, text=True, check=False)
            sync = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
                                  capture_output=True, text=True, check=False)
            evidence = evidence_from_outputs(
                version_text=(version.stderr or version.stdout),
                config_text=Path('/etc/ssh/sshd_config').read_text(),
                passwd_text=Path('/etc/passwd').read_text(),
                now=time.time(), sync_text=sync.stdout)
        else:
            evidence = binding['evidence']
        action = binding.get('action')
        if action == 'install':
            result = install(
                args.root, blob=binding['blob'], operation_id=binding['operationId'], probe=binding['probe'],
                unit=binding['unit'], now_unix=evidence['guestUnix'], synchronized=evidence['synchronized'],
                sshd_version=evidence['sshdVersion'], sshd_config=evidence['sshdConfig'],
                passwd_text=evidence['passwd'])
        elif action == 'revoke':
            result = revoke(args.root, blob=binding['blob'], comment=binding['comment'],
                            passwd_text=evidence['passwd'], exact_line=binding.get('exactLine'))
        else:
            raise GuestRefused('action')
    except GuestRefused as error:
        print(error.code, file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
