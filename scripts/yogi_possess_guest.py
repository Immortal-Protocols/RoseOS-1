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
import ctypes
import datetime
import errno
import glob
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
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
# OpenSSH 9.2p1 `sshd -V` prints SSH_VERSION, which is "OpenSSH_9.2" with no portable suffix.
# `ssh -V` and the protocol banner include "p1". A 9.6 binary is a different string.
VERSION_RE = re.compile(r'^OpenSSH_9\.2(,|$)')
KEY_RE = re.compile(r'(?:^|\s)ssh-ed25519\s+([A-Za-z0-9+/]+=*)(?=\s|$)')
TEMP_RE = re.compile(r'^\.authorized_keys\.[0-9a-f]{16}\.tmp$')
PRIMARY_KEY_FILES = {'.ssh/authorized_keys', '%h/.ssh/authorized_keys', '/home/pixelbuild/.ssh/authorized_keys'}
SECONDARY_KEY_FILES = {'.ssh/authorized_keys2', '%h/.ssh/authorized_keys2'}
ALLOWED_KEY_FILES = PRIMARY_KEY_FILES | SECONDARY_KEY_FILES
BINDING_JSON = None
BACKWARD_CLOCK = (
    'sshd compares expiry-time to the guest clock. A guest clock stepped backward after '
    'install can accept the key until that clock passes the expiry. Nothing here prevents '
    'that step. The 20 minute value is the sshd UTC expiry, not a controller-enforced kill.'
)
QUALIFIED_PARSER = (
    'OpenSSH 9.2p1 sshd -V prints OpenSSH_9.2 with no p1 suffix. '
    'That was read from a portable 9.2p1 build and from the Debian 9.2p1-2+deb12u10 sshd binary. '
    'The protocol banner is OpenSSH_9.2p1 and is not the sshd -V gate. '
    'YYYYMMDDHHMMSSZ is UTC. A forced sleep is signal 9, status 137, because the watchdog is in the group it kills. '
    'OpenSSH 9.6 is a separate local fixture and is not this gate.'
)
WATCHDOG_STATUS = 137


class GuestRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code
        self.detail = {}


def comment_for(operation_id):
    if not isinstance(operation_id, str) or not re.fullmatch(r'[0-9a-f]{32}', operation_id):
        raise GuestRefused('operation-id')
    return 'roseos-yogi-possess-' + operation_id


def version_allowed(text):
    """True only for the sshd -V line verified on OpenSSH 9.2p1."""
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


def _config_value(line):
    """Return (keyword, value). A bare keyword or `Keyword=` has an empty value and does not index it."""
    text = line.split('#', 1)[0].strip()
    if not text:
        return None
    head = text.split(None, 1)[0]
    if '=' in head:
        keyword, _, value = head.partition('=')
        extra = text[len(head):].strip()
        if extra:
            value = (value + ' ' + extra).strip()
        return keyword.lower(), value.strip()
    parts = text.split(None, 1)
    if len(parts) == 1:
        return parts[0].lower(), ''
    return parts[0].lower(), parts[1].strip()


def _key_file_decision(value):
    tokens = [item for item in (value or '').split() if item]
    if not tokens:
        return {'ok': False, 'code': 'sshd-config-malformed', 'path': value or ''}
    if any(token not in ALLOWED_KEY_FILES for token in tokens):
        return {'ok': False, 'code': 'authorized-keys-path', 'path': value}
    if not any(token in PRIMARY_KEY_FILES for token in tokens):
        return {'ok': False, 'code': 'authorized-keys-path', 'path': value}
    return {'ok': True, 'code': 'authorized-keys-path', 'path': tokens[0], 'tokens': tokens}


def decision_from_effective(text):
    """Parse `sshd -T` output for pixelbuild. This is the effective config, after Include and Match."""
    if not isinstance(text, str) or not text.strip():
        return {'ok': False, 'code': 'sshd-config-unreadable'}
    values = {}
    guarded = {'pubkeyauthentication', 'authorizedkeyscommand', 'permituserenvironment', 'allowusers',
               'denyusers', 'authorizedkeysfile'}
    for raw in text.splitlines():
        parsed = _config_value(raw)
        if parsed is None:
            continue
        keyword, value = parsed
        if keyword in guarded and keyword in values:
            return {'ok': False, 'code': 'sshd-config-malformed'}
        values.setdefault(keyword, value)
    if values.get('pubkeyauthentication', 'yes') != 'yes':
        return {'ok': False, 'code': 'pubkey-authentication'}
    if values.get('forcecommand', 'none') not in ('none', ''):
        return {'ok': False, 'code': 'force-command'}
    methods = values.get('authenticationmethods', 'any').strip()
    if methods == 'any':
        for key in ('passwordauthentication', 'kbdinteractiveauthentication', 'challengeresponseauthentication'):
            if values.get(key, 'no') not in ('no', ''):
                return {'ok': False, 'code': 'authentication-methods'}
    elif methods != 'publickey':
        return {'ok': False, 'code': 'authentication-methods'}
    if values.get('authorizedkeyscommand', 'none') not in ('none', ''):
        return {'ok': False, 'code': 'authorized-keys-command'}
    if values.get('permituserenvironment', 'no') != 'no':
        return {'ok': False, 'code': 'permit-user-environment'}
    allow = values.get('allowusers')
    if allow is not None and 'pixelbuild' not in allow.split():
        return {'ok': False, 'code': 'allow-users'}
    if 'pixelbuild' in values.get('denyusers', '').split():
        return {'ok': False, 'code': 'deny-users'}
    if 'authorizedkeysfile' not in values:
        return {'ok': False, 'code': 'sshd-config-malformed'}
    return _key_file_decision(values.get('authorizedkeysfile', ''))


def sshd_config_decision(text, effective=None):
    """Raw files with Include or Match need an sshd -T dump. A bare keyword cannot raise IndexError.

    An empty effective dump is a failed `sshd -T`, not permission to read the raw file instead.
    """
    if effective is not None:
        if not str(effective).strip():
            return {'ok': False, 'code': 'sshd-config-unproven'}
        return decision_from_effective(effective)
    if not isinstance(text, str) or not text.strip():
        return {'ok': False, 'code': 'sshd-config-unreadable'}
    authorized = None
    for raw in text.splitlines():
        parsed = _config_value(raw)
        if parsed is None:
            continue
        keyword, value = parsed
        if keyword in ('include', 'match'):
            return {'ok': False, 'code': 'sshd-config-needs-effective'}
        if keyword == 'pubkeyauthentication' and value not in ('yes', ''):
            return {'ok': False, 'code': 'pubkey-authentication'}
        if keyword == 'authorizedkeyscommand' and value not in ('none', ''):
            return {'ok': False, 'code': 'authorized-keys-command'}
        if keyword == 'permituserenvironment' and value not in ('no', ''):
            return {'ok': False, 'code': 'permit-user-environment'}
        if keyword == 'allowusers' and 'pixelbuild' not in value.split():
            return {'ok': False, 'code': 'allow-users'}
        if keyword == 'denyusers' and 'pixelbuild' in value.split():
            return {'ok': False, 'code': 'deny-users'}
        if keyword == 'authorizedkeysfile':
            if authorized is not None:
                return {'ok': False, 'code': 'sshd-config-malformed'}
            authorized = value
    if authorized is None:
        return {'ok': True, 'code': 'default-authorized-keys', 'path': '.ssh/authorized_keys',
                'tokens': ['.ssh/authorized_keys']}
    return _key_file_decision(authorized)


def filesystem_includes(pattern):
    """Read Include targets. A missing explicit path is unproven. A glob may match nothing."""
    if not isinstance(pattern, str) or not pattern.strip():
        raise OSError('empty include')
    explicit = not any(mark in pattern for mark in '*?[]')
    paths = [pattern] if explicit else sorted(glob.glob(pattern))
    if explicit and not os.path.isfile(pattern):
        raise OSError(pattern)
    found = []
    for path in paths:
        with open(path, 'r', encoding='utf-8') as handle:
            found.append((path, handle.read()))
    return found


def match_graph_problem(text, read_include=None):
    """Any Match in the root file or its Include graph is unproven for an IAP connection.

    One `sshd -T -C addr=127.0.0.1` does not cover Match Address/Host/LocalAddress inside
    35.235.240.0/20 or a narrower address. Refuse until the real connection context is known.
    """
    seen = set()

    def walk(body):
        includes = []
        for parsed in (_config_value(raw) for raw in body.splitlines()):
            if not parsed:
                continue
            keyword, value = parsed
            if keyword == 'match':
                return 'sshd-match-unproven'
            if keyword == 'include':
                includes.append(value)
        for pattern in includes:
            if read_include is None:
                return 'sshd-config-unproven'
            try:
                files = read_include(pattern)
            except OSError:
                return 'sshd-config-unproven'
            if not isinstance(files, list):
                return 'sshd-config-unproven'
            for item in files:
                if not isinstance(item, tuple) or len(item) != 2:
                    return 'sshd-config-unproven'
                path, content = item
                if path in seen:
                    continue
                seen.add(path)
                if not isinstance(content, str):
                    return 'sshd-config-unproven'
                code = walk(content)
                if code:
                    return code
        return None

    if not isinstance(text, str):
        return 'sshd-config-unreadable'
    return walk(text)


def sshd_effective_text(sshd, config_path):
    """Run sshd -T. Nonzero status is a refusal. This does not invent an IAP peer address."""
    try:
        completed = subprocess.run(
            [sshd, '-T', '-f', str(config_path), '-C', 'user=pixelbuild'],
            capture_output=True, text=True, check=False)
    except OSError:
        raise GuestRefused('sshd-config-unproven') from None
    if completed.returncode != 0 or not completed.stdout.strip():
        raise GuestRefused('sshd-config-unproven')
    return completed.stdout


def ancestor_allows(uid, gid, mode, require_root, account_uid, account_gid):
    """`/home` is root-owned mode 0755 on the guest. A synthetic root may be owned by the account."""
    if stat.S_IMODE(mode) != ANCESTOR_MODE:
        raise GuestRefused('ancestor-mode')
    if require_root:
        if uid != 0 or gid != 0:
            raise GuestRefused('ancestor-owner')
        return
    if uid not in (0, account_uid) or gid not in (0, account_gid):
        raise GuestRefused('ancestor-owner')


def evidence_from_outputs(*, version_text, config_text, passwd_text, now, sync_text, effective_text=None):
    if not isinstance(sync_text, str) or sync_text.strip() != 'yes':
        raise GuestRefused('clock-unsynchronized')
    if not version_allowed(version_text):
        raise GuestRefused('sshd-version')
    decision = sshd_config_decision(config_text, effective_text)
    if not decision['ok']:
        raise GuestRefused(decision['code'])
    uid, gid, shell = account_from_passwd(passwd_text)
    if not isinstance(now, (int, float)):
        raise GuestRefused('clock')
    return {
        'guestUnix': int(now), 'synchronized': True, 'sshdVersion': version_text.splitlines()[0].strip(),
        'sshdConfig': config_text, 'passwd': passwd_text, 'uid': uid, 'gid': gid, 'shell': shell,
        'authorizedKeys': decision['path'], 'qualifiedParser': QUALIFIED_PARSER,
        'backwardClock': BACKWARD_CLOCK, 'sshdEffective': effective_text,
    }


def watchdog_source(deadline, probe, unit):
    """Self-contained program. It does not import this module and does not read the client command."""
    if not isinstance(deadline, int) or not 1 <= deadline <= MAX_DEADLINE_SECONDS:
        raise GuestRefused('deadline')
    if not isinstance(probe, str) or not probe.strip() or '\x00' in probe:
        raise GuestRefused('probe')
    if not isinstance(unit, str) or not re.fullmatch(r'[A-Za-z0-9@._:-]+', unit):
        raise GuestRefused('unit')
    if any(token in probe for token in (
            'SSH_ORIGINAL_COMMAND', 'shell=True', 'os.system', 'setsid', 'start_new_session', 'setpgrp')):
        raise GuestRefused('probe')
    probe_b64 = base64.b64encode(probe.encode()).decode()
    return (
        'import base64, ctypes, errno, os, signal, sys\n'
        f'DEADLINE_SECONDS = {deadline}\n'
        f'PROBE_B64 = {json.dumps(probe_b64)}\n'
        f'UNIT = {json.dumps(unit)}\n'
        'def _kill_group(_signum=None, _frame=None):\n'
        '    # The watchdog is in this process group, so SIGKILL applies to it too.\n'
        '    # The status a waiter observes is 137. os._exit here runs only if this process survives.\n'
        '    try:\n'
        '        os.killpg(os.getpgrp(), 9)\n'
        '    except OSError:\n'
        '        pass\n'
        '    os._exit(125)\n'
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


def parse_key_line(line, our_blob=None):
    """One ssh-ed25519 blob and the rest of the line as its comment.

    A google-ssh JSON comment contains spaces and stays one foreign line. Ambiguous means this
    line has more than one ssh-ed25519 blob and one of them is ours. Other lines are preserved.
    """
    if not isinstance(line, str) or line.strip() == '' or line.strip().startswith('#'):
        return None
    matches = list(KEY_RE.finditer(line.strip()))
    if not matches:
        return None
    blobs = [match.group(1) for match in matches]
    if len(matches) != 1:
        if our_blob is not None and our_blob in blobs:
            raise GuestRefused('key-ambiguous')
        return None
    blob = blobs[0]
    comment = line.strip()[matches[0].end():].strip()
    return blob, comment


def _temp_token():
    return secrets.token_hex(8)


def _list_names(dirfd):
    """Names in an already-open directory. Does not use a /proc path."""
    libc = ctypes.CDLL(None, use_errno=True)

    class Dirent(ctypes.Structure):
        _fields_ = [
            ('d_ino', ctypes.c_uint64), ('d_off', ctypes.c_int64), ('d_reclen', ctypes.c_ushort),
            ('d_type', ctypes.c_ubyte), ('d_name', ctypes.c_char * 256),
        ]

    duped = os.dup(dirfd)
    libc.fdopendir.restype = ctypes.c_void_p
    handle = libc.fdopendir(duped)
    if not handle:
        os.close(duped)
        raise GuestRefused('directory-open')
    libc.readdir.argtypes = [ctypes.c_void_p]
    libc.readdir.restype = ctypes.POINTER(Dirent)
    libc.closedir.argtypes = [ctypes.c_void_p]
    names = []
    try:
        while True:
            entry = libc.readdir(handle)
            if not entry:
                break
            name = entry.contents.d_name.split(b'\x00', 1)[0].decode('utf-8', 'replace')
            if name not in ('.', '..'):
                names.append(name)
    finally:
        libc.closedir(handle)
    return names


def _stat_name(parent, name, *, directory):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    if directory:
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(name, flags, dir_fd=parent)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise GuestRefused('symlink') from None
        raise GuestRefused('directory-race') from None
    try:
        return os.fstat(fd)
    finally:
        os.close(fd)


def recover_owned_leftovers(dirfd, uid, gid):
    """Remove only our regular temp files. Anything else with that shape stays visible and blocks."""
    removed, blocked = [], []
    for name in _list_names(dirfd):
        if not TEMP_RE.fullmatch(name):
            continue
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dirfd)
        except OSError:
            blocked.append(name)
            continue
        try:
            st = os.fstat(fd)
        finally:
            os.close(fd)
        safe = stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_uid == uid and st.st_gid == gid
        if not safe:
            blocked.append(name)
            continue
        try:
            os.unlink(name, dir_fd=dirfd)
        except OSError:
            blocked.append(name)
            continue
        removed.append(name)
    if blocked:
        error = GuestRefused('temp-leftover')
        error.detail = {'blockedLeftovers': blocked, 'removedTemps': removed}
        raise error
    return removed


def _open_dir(parent, name, *, uid, gid, modes):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise GuestRefused('symlink') from None
        raise GuestRefused('directory-open') from None
    st = os.fstat(fd)
    mode = stat.S_IMODE(st.st_mode)
    if not stat.S_ISDIR(st.st_mode) or mode not in modes or mode & 0o022 or st.st_uid != uid or st.st_gid != gid:
        os.close(fd)
        if st.st_uid != uid or st.st_gid != gid:
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


def _write_all(fd, payload):
    view = payload
    while view:
        wrote = os.write(fd, view)
        if wrote <= 0:
            raise GuestRefused('auth-write')
        view = view[wrote:]
    os.fsync(fd)


def _write_auth(dirfd, parent_fd, uid, gid, read_stat, payload):
    removed = recover_owned_leftovers(dirfd, uid, gid)
    tmp = f'.authorized_keys.{_temp_token()}.tmp'
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, FILE_MODE, dir_fd=dirfd)
    except FileExistsError:
        raise GuestRefused('auth-temp') from None
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, FILE_MODE)
        _write_all(fd, payload)
        st = os.fstat(fd)
        if st.st_uid != uid or st.st_gid != gid or stat.S_IMODE(st.st_mode) != FILE_MODE or st.st_nlink != 1:
            raise GuestRefused('auth-write')
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp, dir_fd=dirfd)
        except OSError:
            pass
        raise
    os.close(fd)
    directory_now = _stat_name(parent_fd, SSH_DIR, directory=True)
    held = os.fstat(dirfd)
    if (directory_now.st_dev, directory_now.st_ino) != (held.st_dev, held.st_ino):
        try:
            os.unlink(tmp, dir_fd=dirfd)
        except OSError:
            pass
        raise GuestRefused('directory-race')
    file_now = _stat_name(dirfd, AUTH_NAME, directory=False)
    if (file_now.st_dev, file_now.st_ino) != (read_stat.st_dev, read_stat.st_ino):
        try:
            os.unlink(tmp, dir_fd=dirfd)
        except OSError:
            pass
        raise GuestRefused('directory-race')
    os.rename(tmp, AUTH_NAME, src_dir_fd=dirfd, dst_dir_fd=dirfd)
    os.fsync(dirfd)
    checked, written = _read_auth(dirfd)
    if checked.st_uid != uid or written != payload:
        raise GuestRefused('auth-write')
    return written, removed


def _walk(root, uid, gid, require_root_ancestor):
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    fds = [root_fd]
    try:
        home_uid, home_gid = (0, 0) if require_root_ancestor else (uid, gid)
        home_fd = _open_dir(root_fd, HOME_NAME, uid=home_uid, gid=home_gid, modes={ANCESTOR_MODE})
        fds.append(home_fd)
        home_stat = os.fstat(home_fd)
        ancestor_allows(home_stat.st_uid, home_stat.st_gid, home_stat.st_mode, require_root_ancestor, uid, gid)
        user_fd = _open_dir(home_fd, USER_DIR, uid=uid, gid=gid, modes=HOME_MODES)
        fds.append(user_fd)
        ssh_fd = _open_dir(user_fd, SSH_DIR, uid=uid, gid=gid, modes={SSH_MODE})
        fds.append(ssh_fd)
        st, data = _read_auth(ssh_fd)
        if st.st_uid != uid or st.st_gid != gid:
            raise GuestRefused('auth-owner')
        return ssh_fd, user_fd, fds, st, data
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


def _foreign_payload(parts, newline, line=None):
    kept = list(parts) if line is None else [item for item in parts if item != line]
    if not kept:
        return b''
    body = '\n'.join(kept)
    if newline:
        body += '\n'
    return body.encode()


def _mutate(root, uid, gid, *, mode, blob, comment, line=None):
    require_root = os.path.abspath(root) == '/'
    ssh_fd, user_fd, fds, read_stat, data = _walk(root, uid, gid, require_root)
    try:
        removed = recover_owned_leftovers(ssh_fd, uid, gid)
        parts, newline = _split_lines(data)
        parsed = []
        for item in parts:
            found = parse_key_line(item, our_blob=blob)
            parsed.append(found)
        blob_hits = [item for item, found in zip(parts, parsed) if found and found[0] == blob]
        comment_hits = [item for item, found in zip(parts, parsed) if found and found[1] == comment and comment]
        if mode == 'install':
            if line in parts:
                return {'rewritten': False, 'idempotent': True, 'line': line, 'tempFilesRemoved': len(removed),
                        'removedTemps': removed}
            if blob_hits or comment_hits:
                raise GuestRefused('duplicate-key')
            if parts and not newline:
                payload = ('\n'.join(parts) + '\n' + line).encode()
            elif not parts:
                payload = (line + '\n').encode()
            else:
                payload = ('\n'.join(parts) + '\n' + line + '\n').encode()
            _written, more = _write_auth(ssh_fd, user_fd, uid, gid, read_stat, payload)
            removed.extend(more)
            return {'rewritten': True, 'idempotent': False, 'line': line, 'tempFilesRemoved': len(removed),
                    'removedTemps': removed}
        matches = [item for item, found in zip(parts, parsed)
                   if found and found[0] == blob and found[1] == comment]
        # A blob with no comment, or a different comment, is not an exact revoke and is not rewritten.
        if any(found and found[0] == blob and found[1] != comment for found in parsed):
            raise GuestRefused('revoke-foreign')
        if any(found and found[1] == comment and found[0] != blob for found in parsed if comment):
            raise GuestRefused('revoke-foreign')
        if len(matches) > 1:
            raise GuestRefused('revoke-ambiguous')
        if not matches:
            return {'rewritten': False, 'removed': False, 'absent': True, 'tempFilesRemoved': len(removed),
                    'removedTemps': removed}
        if line is not None and matches != [line]:
            raise GuestRefused('revoke-not-exact')
        payload = _foreign_payload(parts, newline, matches[0])
        if data.startswith(matches[0].encode()):
            prefix = data.split(matches[0].encode(), 1)[0]
        else:
            prefix = b''
        written, more = _write_auth(ssh_fd, user_fd, uid, gid, read_stat, payload)
        removed.extend(more)
        if matches[0].encode() in written:
            raise GuestRefused('revoke-residue')
        if prefix and not written.startswith(prefix) and prefix.strip():
            raise GuestRefused('revoke-residue')
        return {'rewritten': True, 'removed': True, 'absent': False, 'line': matches[0],
                'tempFilesRemoved': len(removed), 'removedTemps': removed}
    finally:
        _close(fds)


def install(root, *, blob, operation_id, probe, unit, now_unix, synchronized, sshd_version, sshd_config,
            passwd_text, sshd_effective=None, include_read=None):
    if synchronized is not True:
        raise GuestRefused('clock-unsynchronized')
    if not version_allowed(sshd_version):
        raise GuestRefused('sshd-version')
    problem = match_graph_problem(sshd_config, include_read)
    if problem:
        raise GuestRefused(problem)
    if not isinstance(sshd_effective, str) or not sshd_effective.strip():
        raise GuestRefused('sshd-config-unproven')
    decision = decision_from_effective(sshd_effective)
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
            import time
            version = subprocess.run(['sshd', '-V'], capture_output=True, text=True, check=False)
            sync = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
                                  capture_output=True, text=True, check=False)
            config_text = Path('/etc/ssh/sshd_config').read_text()
            problem = match_graph_problem(config_text, filesystem_includes)
            if problem:
                raise GuestRefused(problem)
            effective = sshd_effective_text('sshd', '/etc/ssh/sshd_config')
            evidence = evidence_from_outputs(
                version_text=(version.stderr or version.stdout),
                config_text=config_text,
                passwd_text=Path('/etc/passwd').read_text(),
                now=time.time(), sync_text=sync.stdout, effective_text=effective)
        else:
            evidence = binding['evidence']
        action = binding.get('action')
        if action == 'install':
            result = install(
                args.root, blob=binding['blob'], operation_id=binding['operationId'], probe=binding['probe'],
                unit=binding['unit'], now_unix=evidence['guestUnix'], synchronized=evidence['synchronized'],
                sshd_version=evidence['sshdVersion'], sshd_config=evidence['sshdConfig'],
                passwd_text=evidence['passwd'], sshd_effective=evidence.get('sshdEffective'))
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
