"""Set up a delivered version's server in its Chariot agent container over SSH (docs/APP-BUILDER.md §7.7a), as
Chariot's contract describes it (ProtocolsBackend ``chariot/docs/agent-app-hosting.md`` at 8dc39b86). Source only:
nothing here runs unless an owner enables it (``enabled`` below) and hands it a channel to an authorized agent;
whether the builder or a Codex turn calls it is the owner's capability decision, not made here.

The procedure, all over SSH exec with stdin (no SFTP, SCP or forwarding):

1. ``probe``: the agent's own identity (``CHARIOT_APP_ID``, ``CHARIOT_APP_URL``) must be the project's recorded agent
   container (Ledger.agent_container), and every tool the setup uses must be in ``/chariot/bin``.
2. ``state``: the deployed record left by the last setup (``deployed.json``: ELF sha256, data_schema, version). A
   lower ``data_schema`` than the deployed one is refused (the server refuses newer data anyway).
3. ``upload``: the server ELF and its ``start`` script, as a tar on stdin, into a fresh ``incoming`` folder.
4. ``switch``, one script: first the sealing keyring (refused before anything is touched when it is absent, unless
   the owner enables ``generate_keyring``, which writes it once, 0400, into the persistent area); then unpublish (remove ``app.json``); stop the running server and wait for its ``run.lock``;
   back up the whole data folder (every SQLite database with its journal files) into ``backups/latest.tar``
   while nothing runs, the backup before it kept as ``backups/previous.tar``; keep the previous binary and its start script as ``server.prev`` and ``start.prev``; move the new
   one in; start it with Chariot's published launcher; wait for ``/health`` on loopback to answer 200
   with this binary's revision; then publish ``app.json`` and write ``deployed.json``. If health does not come,
   the new server is stopped and the previous binary and the backed-up data are restored and started again, so the
   app returns to its last good state; the step then fails.

The server listens on 127.0.0.1 only (``ROSE_LISTEN_HOST``), never on the pod's address. Nothing here allocates,
wakes or pays for a container, logs in, or reads Chariot credentials; ``ChariotSsh`` runs the operator's
already-authorized ``chariot ssh`` with its own HOME.
"""
from __future__ import annotations

import io
import json
import re
import unicodedata
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

from . import server_delivery, server_manifest
from .ledger import Ledger

BIN = '/chariot/bin'
APP_PORT = 8090  # the contract's default; 8088 and 42617 are Chariot's own
RESERVED_PORTS = (8088, 42617)
# Chariot's published launcher (chariot/scripts/launch_agent_app.sh at ProtocolsBackend 8dc39b86), verbatim.
LAUNCHER_SOURCE = 'ProtocolsBackend 8dc39b86e20984e530fecadab36601c77e9cd467 chariot/scripts/launch_agent_app.sh'
LAUNCHER = '''#!/chariot/bin/sh
# Run with: chariot ssh AGENT -- /chariot/bin/sh -s < launch_agent_app.sh
# The same source runs as the optional pod postStart hook. No image rebuild.
set -eu
umask 077
chariot_app_dir="${HOME:?}/workspace/.chariot-app"
test -f "$chariot_app_dir/start" && test ! -L "$chariot_app_dir/start" || exit 0
(
  trap '' HUP
  exec 9>"$chariot_app_dir/run.lock"
  /chariot/bin/flock -n 9 || exit 0
  cd "$chariot_app_dir"
  /chariot/bin/sh ./start &
  chariot_app_child=$!
  wait "$chariot_app_child"
) </dev/null >>"$chariot_app_dir/app.log" 2>&1 &
'''
# Every tool the remote scripts call; the probe refuses an agent missing one. (test, printf, kill, wait and trap
# are the shell's own.)
APPLETS = ('sh', 'tar', 'nc', 'base64', 'head', 'timeout', 'flock', 'mkdir', 'mv', 'rm', 'cat', 'chmod', 'sleep')
LAUNCHER_SHA256 = '584d15de31c63cdd94ddec3bf73d654f0052fdface011fec33d5d3d7934f322a'  # of that file, at that commit
HEALTH_WAIT_S = 60
STOP_WAIT_S = 30


class SetupFailed(Exception):
    """The setup refused or failed; ``step`` names where. The app is left as the last good state."""

    def __init__(self, step: str, message: str):
        super().__init__(f'{step}: {message}')
        self.step, self.message = step, message


@dataclass(frozen=True)
class Result:
    code: int
    stdout: str
    stderr: str


class Channel:
    """SSH exec with stdin to one agent container: ``script`` runs as ``sh -s``; ``receive`` unpacks a tar from
    stdin into a folder under the app directory."""

    def script(self, text: str) -> Result:
        raise NotImplementedError

    def receive(self, tar: bytes, folder: str) -> Result:
        raise NotImplementedError


@dataclass
class ChariotSsh(Channel):
    """``chariot ssh AGENT -- …`` with the operator's own, already-authorized CLI and a dedicated HOME (its
    ``~/.chariot``). It reads no credential and prints nothing of the CLI's config."""
    chariot: Path
    agent: str
    home: Path
    timeout_s: float = 300.0

    def _run(self, remote: list[str], stdin: bytes) -> Result:
        env = {'HOME': str(self.home), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
        try:
            done = subprocess.run([str(self.chariot), 'ssh', self.agent, '--', *remote], input=stdin, env=env,
                                  capture_output=True, timeout=self.timeout_s)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SetupFailed('ssh', f'chariot ssh did not run ({type(exc).__name__})') from None
        return Result(done.returncode, done.stdout.decode('utf-8', 'replace'), done.stderr.decode('utf-8', 'replace'))

    def script(self, text: str) -> Result:
        return self._run([f'{BIN}/sh', '-s'], text.encode())

    def receive(self, tar: bytes, folder: str) -> Result:
        if not re.fullmatch(r'incoming-[0-9a-f]{64}', folder):
            raise ValueError('folder')
        return self._run([f'exec {BIN}/tar -xf - -C "$HOME/workspace/.chariot-app/{folder}"'], tar)


@dataclass(frozen=True)
class Plan:
    project: str
    version_code: int
    agent_id: str
    app_url: str
    elf: Path
    sha256: str
    data_schema: int
    port: int = APP_PORT


def plan(ledger: Ledger, root: Path, project: str, version_code: int | None = None, port: int = APP_PORT) -> Plan:
    """What to set up: a delivered client-server version's server (its manifest and the stored ELF, re-hashed) and
    the project's recorded agent container."""
    if not (1024 <= port <= 65535) or port in RESERVED_PORTS:
        raise SetupFailed('plan', f'port {port} is not an unprivileged app port')
    versions = [v['versionCode'] for v in ledger.versions(project)]
    if not versions:
        raise SetupFailed('plan', 'this project has no delivered version')
    code = max(versions) if version_code is None else version_code
    data = ledger.server_manifest(project, code) if code in versions else None
    if data is None:
        raise SetupFailed('plan', 'this version has no hosted server (no manifest for the static musl target)')
    m = server_manifest.decode(data)
    elf = Path(root) / 'artifacts' / f'{m["elf"]["sha256"]}.server'
    try:
        server_manifest.check(m, elf, project_id=project, version_code=code)
    except server_manifest.Refused as exc:
        raise SetupFailed('plan', f'the stored server is not the recorded one: {exc}') from None
    container = ledger.agent_container(project)
    if container is None:
        raise SetupFailed('plan', 'no agent container is recorded for this project')
    try:
        schema = server_delivery.data_schema(m['schema'], m['signin']['revision'])
    except server_delivery.Refused as exc:
        raise SetupFailed('plan', str(exc)) from None
    return Plan(project, code, container['agentId'], container['appUrl'], elf, m['elf']['sha256'], schema, port)


def start_script(p: Plan) -> str:
    """The foreground ``start`` the launcher runs: loopback only, the contract's data folder, the keyring outside
    it, this binary's identity for /health, and the pid the next setup stops."""
    return f'''#!{BIN}/sh
set -eu
export PORT={p.port}
export ROSE_LISTEN_HOST=127.0.0.1
export DATA_DIR="$HOME/workspace/.chariot-app/data"
export CHARIOT_SEALING_KEY_FILE="$HOME/workspace/.chariot-app/keys/keyring.json"
export CHARIOT_REVISION_SHA256={p.sha256}
export CHARIOT_DEPLOYMENT_SHA256={p.sha256}
printf '%s\\n' "$$" > server.pid
exec ./server
'''


def bundle(p: Plan) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for name, data in (('server', p.elf.read_bytes()), ('start', start_script(p).encode())):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o700, 0
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def _sh(text: str, bin_dir: str) -> str:
    return text.replace('@BIN@', bin_dir)


PROBE = '''set -eu
for applet in @APPLETS@; do test -x "@BIN@/$applet" || { echo "missing=$applet"; exit 3; }; done
test -d "$HOME/workspace" && test -w "$HOME/workspace" || { echo "missing=workspace"; exit 3; }
test -n "${CHARIOT_APP_ID:-}" && test -n "${CHARIOT_APP_URL:-}" || { echo "missing=identity"; exit 3; }
printf 'app_id=%s\\napp_url=%s\\n' "$CHARIOT_APP_ID" "$CHARIOT_APP_URL"
'''
STATE = '''set -eu
app="$HOME/workspace/.chariot-app"
umask 077
@BIN@/mkdir -p "$app/data" "$app/backups" "$app/keys"
if test -f "$app/deployed.json"; then @BIN@/cat "$app/deployed.json"; fi
'''
PREPARE = '''set -eu
app="$HOME/workspace/.chariot-app"
umask 077
@BIN@/rm -rf "$app/@INCOMING@"
@BIN@/mkdir "$app/@INCOMING@"
'''
SWITCH = r'''set -eu
app="$HOME/workspace/.chariot-app"
umask 077
cd "$app"
stop() {
  if test -f server.pid; then kill -TERM "$(@BIN@/cat server.pid)" 2>/dev/null || true; fi
  if ! @BIN@/flock -w @STOP_WAIT@ run.lock true; then
    if test -f server.pid; then kill -KILL "$(@BIN@/cat server.pid)" 2>/dev/null || true; fi
    @BIN@/flock -w 10 run.lock true || { echo "failed=stop"; exit 5; }
  fi
  @BIN@/rm -f server.pid
}
healthy() {
  i=0
  while test "$i" -lt @HEALTH_WAIT@; do
    reply=$(printf 'GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n' |
      @BIN@/timeout 5 @BIN@/nc -w 3 127.0.0.1 @PORT@ 2>/dev/null || true)
    case "$reply" in "HTTP/1."?" 200 "*"$1"*) return 0 ;; esac
    @BIN@/sleep 1
    i=$((i + 1))
  done
  return 1
}
launch() { @BIN@/sh -s <<'LAUNCH'
@LAUNCHER@
LAUNCH
}
publish() {
  printf '%s' '@DESCRIPTOR@' > app.json.tmp
  @BIN@/mv -f app.json.tmp app.json
}
# Before anything is touched: the keyring the server needs (D9), never replaced once there.
if test ! -f keys/keyring.json; then
  test "@GENERATE_KEYRING@" = yes || { echo "failed=keyring"; exit 6; }
  key=$(@BIN@/head -c 32 /dev/urandom | @BIN@/base64)
  printf '{"format":"rose-signin-keyring/v1","current":1,"keys":[{"version":1,"key":"%s"}]}' "$key" > keys/keyring.tmp
  @BIN@/chmod 0400 keys/keyring.tmp
  @BIN@/mv -f keys/keyring.tmp keys/keyring.json
fi
# Unpublish, stop, then back up the data and keep the previous binary while nothing runs.
@BIN@/rm -f app.json app.json.tmp
stop
if test -f backups/latest.tar; then @BIN@/mv -f backups/latest.tar backups/previous.tar; fi
@BIN@/tar -C data -cf backups/latest.tar .
had_prev=no
if test -f server && test -f start; then
  @BIN@/mv -f server server.prev
  @BIN@/mv -f start start.prev
  had_prev=yes
fi
@BIN@/mv -f "@INCOMING@/server" server
@BIN@/mv -f "@INCOMING@/start" start
@BIN@/chmod 0700 server start
@BIN@/rm -rf "@INCOMING@"
launch
if healthy "@SHA@"; then
  publish
  printf '%s' '@DEPLOYED@' > deployed.json.tmp
  @BIN@/mv -f deployed.json.tmp deployed.json
  echo "deployed=@SHA@"
  exit 0
fi
# The new server never became healthy: back to the last good state, binary and data both.
echo "failed=health"
stop
@BIN@/rm -rf data
@BIN@/mkdir data
@BIN@/tar -C data -xf backups/latest.tar
if test "$had_prev" = yes; then
  @BIN@/mv -f server.prev server
  @BIN@/mv -f start.prev start
  launch
  if healthy "@PREV@"; then publish; echo "restored=@PREV@"; else echo "restored=binary-and-data-not-healthy"; fi
else
  @BIN@/rm -f server start
  echo "restored=data-no-previous-server"
fi
exit 4
'''


def _check(result: Result, step: str) -> str:
    if result.code != 0:
        detail = next((line for line in result.stdout.splitlines() if '=' in line), '') or result.stderr.strip()[-200:]
        raise SetupFailed(step, f'exit {result.code}: {detail}')
    return result.stdout


def deploy(p: Plan, channel: Channel, *, enabled: bool = False, generate_keyring: bool = False,
           bin_dir: str = BIN, health_wait_s: int = HEALTH_WAIT_S, stop_wait_s: int = STOP_WAIT_S) -> dict:
    """Run the setup for ``p`` on ``channel``. Off unless ``enabled``: the owner's decision (§7.7a)."""
    if not enabled:
        raise SetupFailed('disabled', 'agent-container setup is not enabled on this builder')
    out = _check(channel.script(_sh(PROBE.replace('@APPLETS@', ' '.join(APPLETS)), bin_dir)), 'probe')
    seen = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
    if seen.get('app_url') != p.app_url or seen.get('app_id', '').replace('-', '').lower() != p.agent_id:
        raise SetupFailed('probe', 'this agent is not the project\'s recorded agent container')
    state = _check(channel.script(_sh(STATE, bin_dir)), 'state').strip()
    previous = None
    if state:
        try:
            previous = json.loads(state)
            if not (isinstance(previous, dict) and isinstance(previous.get('dataSchema'), int)
                    and re.fullmatch(r'[0-9a-f]{64}', str(previous.get('sha256', '')))):
                raise ValueError
        except ValueError:
            raise SetupFailed('state', 'the deployed record on the agent is not readable') from None
        if previous['dataSchema'] > p.data_schema:
            raise SetupFailed('state', 'this version\'s data schema is older than the deployed one')
    incoming = f'incoming-{p.sha256}'
    _check(channel.script(_sh(PREPARE.replace('@INCOMING@', incoming), bin_dir)), 'upload')
    _check(channel.receive(bundle(p), incoming), 'upload')
    descriptor = json.dumps({'format': 'chariot-agent-app/v1', 'port': p.port, 'health_path': '/health'},
                            separators=(',', ':'))
    deployed = json.dumps({'sha256': p.sha256, 'dataSchema': p.data_schema, 'versionCode': p.version_code,
                           'project': p.project}, separators=(',', ':'), sort_keys=True)
    launcher = LAUNCHER.replace('/chariot/bin', bin_dir)
    script = (SWITCH.replace('@LAUNCHER@', launcher).replace('@INCOMING@', incoming).replace('@SHA@', p.sha256)
              .replace('@PREV@', (previous or {}).get('sha256', 'none')).replace('@PORT@', str(p.port))
              .replace('@DESCRIPTOR@', descriptor).replace('@DEPLOYED@', deployed)
              .replace('@GENERATE_KEYRING@', 'yes' if generate_keyring else 'no')
              .replace('@HEALTH_WAIT@', str(int(health_wait_s))).replace('@STOP_WAIT@', str(int(stop_wait_s))))
    result = channel.script(_sh(script, bin_dir))
    lines = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.code != 0 or lines.get('deployed') != p.sha256:
        why = lines.get('failed', 'unknown')
        raise SetupFailed('switch', f'the new server did not become healthy ({why}); restored: '
                                    f'{lines.get("restored", "nothing to restore")}')
    return {'deployed': p.sha256, 'appUrl': p.app_url, 'dataSchema': p.data_schema,
            'previous': (previous or {}).get('sha256')}



AGENT_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')


@dataclass(frozen=True)
class AgentHostConfig:
    """The owner's opt-in (builder config ``agentHost``): the already-authorized ``chariot`` CLI, its own HOME (where
    ``chariot login`` was done), the agents this builder may set apps up in (one app each), and whether the setup may
    write a sealing keyring into an agent that has none (D9)."""
    chariot: Path
    home: Path
    agents: tuple
    generate_keyring: bool = False
    port: int = APP_PORT


class AgentHosting:
    """The Builder's ``hosting``: before a client-server job generates its client, ``ensure`` records the project's
    agent container (an agent from the config not already holding another project's app, by the identity it
    reports itself: never derived), so its URL is baked; before the version is delivered, ``deploy`` sets its server
    up there. ``channel(agent)`` is ``ChariotSsh`` unless a test passes its own."""

    def __init__(self, config: AgentHostConfig, ledger: Ledger, channel=None):
        self.config, self.ledger = config, ledger
        self.channel = channel or (lambda agent: ChariotSsh(config.chariot, agent, config.home))
        self._names = {}  # agent id (hex) -> configured agent name, as each agent reported itself

    def _identity(self, name: str) -> tuple[str, str]:
        out = _check(self.channel(name).script(_sh(PROBE.replace('@APPLETS@', ' '.join(APPLETS)), BIN)), 'probe')
        seen = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
        agent_id = seen.get('app_id', '').replace('-', '').lower()
        if not re.fullmatch(r'[0-9a-f]{32}', agent_id) or not seen.get('app_url'):
            raise SetupFailed('probe', f'agent {name} reported no app identity')
        self._names[agent_id] = name
        return agent_id, seen['app_url']

    def ensure(self, project: str) -> None:
        if self.ledger.agent_container(project) is not None:
            return
        last = None
        for name in self.config.agents:
            agent_id, url = self._identity(name)
            try:
                self.ledger.record_agent_container(project, agent_id, url)
                return
            except Exception as exc:  # held by another project's app, or a URL the client cannot use: the next
                last = exc
        raise SetupFailed('ensure', f'no configured agent is free for this app ({last})')

    def name_for(self, agent_id: str) -> str:
        if agent_id not in self._names:
            for name in self.config.agents:
                if self._identity(name)[0] == agent_id:
                    break
        if agent_id not in self._names:
            raise SetupFailed('probe', 'the recorded agent container is not one of the configured agents')
        return self._names[agent_id]

    def deploy(self, project: str, version_code: int, elf: Path, manifest: dict) -> dict:
        container = self.ledger.agent_container(project)
        if container is None:
            raise SetupFailed('plan', 'no agent container is recorded for this project')
        schema = server_delivery.data_schema(manifest['schema'], manifest['signin']['revision'])
        p = Plan(project, version_code, container['agentId'], container['appUrl'], Path(elf),
                 manifest['elf']['sha256'], schema, self.config.port)
        return deploy(p, self.channel(self.name_for(container['agentId'])), enabled=True,
                      generate_keyring=self.config.generate_keyring)

    def invite(self, project: str, sha256: str, account: str) -> str:
        """An invite from the project's server in its recorded agent container (see ``invite``)."""
        container = self.ledger.agent_container(project)
        if container is None:
            raise InviteRefused('not_hosted')
        try:
            name = self.name_for(container['agentId'])
        except SetupFailed:
            raise InviteRefused('unreachable') from None
        return invite(self.channel(name), sha256, account)


INVITE = '''set -eu
app="$HOME/workspace/.chariot-app"
cd "$app" 2>/dev/null && test -x ./server || { echo "failed=not_set_up" >&2; exit 5; }
export DATA_DIR="$app/data"
export CHARIOT_DEPLOYMENT_SHA256=@SHA@
export CHARIOT_REVISION_SHA256=@SHA@
exec ./server invite @ACCOUNT@
'''
INVITE_TOKEN = re.compile(r'[A-Z2-7]{52}')  # rose-signin base32, no padding
INVITE_REFUSAL = re.compile(r'(?:no invite: |failed=)([a-z_]+)')


class InviteRefused(Exception):
    """The agent's server issued no invite: ``word`` is its one-word reason (never the account or a token)."""

    def __init__(self, word: str):
        super().__init__(word)
        self.word = word


def valid_account(account) -> bool:
    """rose-signin's ``valid_name``: 1 to 128 UTF-8 bytes, no ``:`` and no control character."""
    return (isinstance(account, str) and 0 < len(account.encode('utf-8', 'surrogatepass')) <= 128
            and not any(c == ':' or unicodedata.category(c) == 'Cc' for c in account)
            and not any(0xD800 <= ord(c) <= 0xDFFF for c in account))


def invite(channel: Channel, sha256: str, account: str, *, bin_dir: str = BIN) -> str:
    """One single-use invite for ``account`` from the server set up in the agent (its ``invite <account>``,
    §7.4): the token, returned once. Its stdout is the token, so it is read only on success and never put in an
    error, a log or a record."""
    if not valid_account(account) or not re.fullmatch(r'[0-9a-f]{64}', sha256):
        raise InviteRefused('invalid_name')
    quoted = "'" + account.replace("'", "'\\''") + "'"
    script = _sh(INVITE, bin_dir).replace('@SHA@', sha256).replace('@ACCOUNT@', quoted)
    try:
        done = channel.script(script)
    except SetupFailed:
        raise InviteRefused('unreachable') from None
    if done.code == 0:
        token = done.stdout.strip()
        if INVITE_TOKEN.fullmatch(token):
            return token
        raise InviteRefused('bad_output')
    found = INVITE_REFUSAL.search(done.stderr)
    raise InviteRefused(found.group(1) if found else 'failed')
