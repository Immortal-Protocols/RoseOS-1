"""Set up a delivered version's server in its Chariot agent container over SSH (docs/APP-BUILDER.md §7.7a), as
Chariot's contract describes it (ProtocolsBackend ``chariot/docs/agent-app-hosting.md`` at 8dc39b86). Source only:
nothing here runs unless an owner configures ``agentHost`` (``enabled`` below) with an authorized agent; the
builder's own job runs it, no model turn.

The procedure, all over SSH exec with stdin (no SFTP, SCP or forwarding):

1. ``probe``: the agent's own identity (``CHARIOT_APP_ID``, ``CHARIOT_APP_URL``) must be the project's recorded agent
   container (Ledger.agent_container), and every tool the setup uses must be in ``/chariot/bin``.
2. ``state``: the deployed record left by the last setup (``deployed.json``: ELF sha256, data_schema, version). A
   lower ``data_schema`` than the deployed one is refused (the server refuses newer data anyway).
3. ``upload``: the server ELF and its ``start`` script, as a tar on stdin, into a fresh ``incoming`` folder.
4. ``switch``, one script (SWITCH below; docs/APP-BUILDER.md §7.7a): the keyring first; an earlier switch that was
   cut off undone; unpublish; stop (only a server holding ``run.lock``); back up the data whole into
   ``backups/latest.tar`` (the one before kept as ``previous.tar``); keep the release as ``releases/<sha256>``;
   start it with Chariot's launcher; health; publish and record. Any failure after the stop goes back to the
   deployed release (and, once another binary ran, its backed-up data).
5. ``revert``: a release whose version was never delivered goes back to the one before it, with the data from just
   before its switch.

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
import threading
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
    timeout_s: float = 1200.0  # above the switch's own worst case (two stops and two health waits)

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
STATE = r'''set -eu
app="$HOME/workspace/.chariot-app"
umask 077
@BIN@/mkdir -p "$app/data" "$app/backups" "$app/keys" "$app/releases"
cd "$app"
occupied=no
if test -e server || test -e start; then occupied=yes; fi
for f in data/* data/.[!.]*; do if test -e "$f"; then occupied=yes; fi; done
echo "occupied=$occupied"
if test -f switching; then printf 'switching=%s\n' "$(@BIN@/cat switching)"; fi
if test -f deployed.json; then printf 'deployed=%s\n' "$(@BIN@/cat deployed.json)"; fi
'''
PREPARE = '''set -eu
app="$HOME/workspace/.chariot-app"
umask 077
@BIN@/rm -rf "$app/@INCOMING@"
@BIN@/mkdir "$app/@INCOMING@"
'''
# Shared by SWITCH and REVERT. Every tool is called by its /chariot/bin path (that folder is not on PATH), and only
# with options BusyBox's applets take (its flock: -s -x -u -n only). The launcher holds run.lock for as long as the
# server runs (its fd 9 is inherited through ./start's exec), so "locked" means a server is running.
COMMON = r'''set -eu
app="$HOME/workspace/.chariot-app"
umask 077
cd "$app"
B=@BIN@
locked() { ! $B/flock -n run.lock $B/sh -c : ; }
stop() {
  if locked; then
    if test -f server.pid; then kill -TERM "$($B/cat server.pid)" 2>/dev/null || true; fi
    i=0; while locked && test "$i" -lt @STOP_WAIT@; do $B/sleep 1; i=$((i + 1)); done
    if locked; then
      if test -f server.pid; then kill -KILL "$($B/cat server.pid)" 2>/dev/null || true; fi
      i=0; while locked && test "$i" -lt 10; do $B/sleep 1; i=$((i + 1)); done
      if locked; then failed=stop; return 1; fi
    fi
  fi
  $B/rm -f server.pid
}
healthy() {
  i=0
  while test "$i" -lt @HEALTH_WAIT@; do
    reply=$(printf 'GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n' |
      $B/timeout 3 $B/nc -w 2 127.0.0.1 @PORT@ 2>/dev/null || true)
    case "$reply" in "HTTP/1."?" 200 "*"$1"*) return 0 ;; esac
    $B/sleep 1
    i=$((i + 1))
  done
  return 1
}
launch() { $B/sh -s <<'LAUNCH'
@LAUNCHER@
LAUNCH
}
publish() {
  printf '%s' '@DESCRIPTOR@' > app.json.tmp
  $B/mv -f app.json.tmp app.json
}
# The ELF sha256 a deployed record names (deployed.json by default), or nothing.
sha_of() {
  test -f "${1:-deployed.json}" || return 0
  s=$($B/cat "${1:-deployed.json}"); s=${s#*'"sha256":"'}; s=${s%%'"'*}
  case "$s" in *[!0-9a-f]* | '') return 0 ;; esac
  if test "${#s}" -eq 64; then printf '%s' "$s"; fi
}
# A release as the running pair, written beside and moved into place.
activate() {
  $B/cat "releases/$1/server" > server.tmp
  $B/cat "releases/$1/start" > start.tmp
  $B/chmod 0700 server.tmp start.tmp
  $B/mv -f server.tmp server
  $B/mv -f start.tmp start
}
# The data as backups/latest.tar holds it. Each step fails explicitly, whatever errexit says: the data folder is
# replaced only once the archive unpacked whole, and the data it replaces is moved (not copied) to the folder $1
# (recover: backups/replaced; REVERT: backups/reverted, which nothing else ever replaces).
restore_data() {
  $B/rm -rf data.restoring "$1.tmp" || return 1
  $B/mkdir data.restoring || return 1
  if ! $B/tar -C data.restoring -xf backups/latest.tar; then $B/rm -rf data.restoring; return 1; fi
  $B/mv data "$1.tmp" || { $B/rm -rf data.restoring; return 1; }
  if ! $B/mv data.restoring data; then $B/mv "$1.tmp" data; return 1; fi
  $B/rm -rf "$1"
  $B/mv "$1.tmp" "$1" || true
}
# The marker a switch leaves while it runs: "<project> <sha> <stage>", stage "stopped" (the deployed release was
# stopped; the data is untouched) or "installing" (another binary may have run on the data since latest.tar).
stage_of() {
  test -f switching || return 0
  m=$($B/cat switching); printf '%s' "${m##* }"
}
# Back to the deployed release (deployed.json), on its data from before the switch when another binary may have
# touched it, then started and published again. If the data cannot be put back, nothing is started and the marker
# stays, so the next run tries again.
recover() {
  stop || true
  if test "$(stage_of)" = installing; then
    if ! restore_data backups/replaced; then echo "restored=data-not-restored"; return 0; fi
  fi
  good=$(sha_of)
  if test -n "$good" && test -f "releases/$good/server" && test -f "releases/$good/start"; then
    activate "$good" || { echo "restored=not-restored"; return 0; }
    $B/rm -f switching
    launch
    if healthy "$good"; then publish; echo "restored=$good"; else echo "restored=not-healthy"; fi
  else
    $B/rm -f server start switching
    echo "restored=nothing-deployed"
  fi
}
'''
SWITCH = COMMON + r'''phase=start
failed=interrupted
on_exit() {
  code=$?
  trap - EXIT
  set +e
  if test "$phase" = done; then exit "$code"; fi
  echo "failed=$failed"
  if test "$phase" != start; then recover; fi
  exit 4
}
trap on_exit EXIT
# Before anything is touched: the keyring the server needs (D9), never replaced once there.
if test ! -f keys/keyring.json; then
  if test "@GENERATE_KEYRING@" != yes; then echo "failed=keyring"; phase=done; exit 6; fi
  key=$($B/head -c 32 /dev/urandom | $B/base64)
  printf '{"format":"rose-signin-keyring/v1","current":1,"keys":[{"version":1,"key":"%s"}]}' "$key" > keys/keyring.tmp
  $B/chmod 0400 keys/keyring.tmp
  $B/mv -f keys/keyring.tmp keys/keyring.json
fi
phase=switch
# An earlier switch was cut off (SSH dropped, the builder stopped): back to the deployed release and its data first.
if test -f switching; then
  echo "recovered=interrupted"
  recover
  if test -f switching; then failed=recover; exit 4; fi
fi
# From the stop on, a crash leaves the marker, so the next run starts the deployed release again (stage "stopped":
# its data untouched) or also puts latest.tar back (stage "installing").
printf '%s %s stopped' '@PROJECT@' '@SHA@' > switching
$B/rm -f app.json app.json.tmp
failed=stop
stop
# Back up the data while nothing runs: the new archive becomes latest.tar only once it is whole, and the one before
# it is kept as previous.tar.
failed=backup
$B/rm -f backups/taking.tar
$B/tar -C data -cf backups/taking.tar .
if test -f backups/latest.tar; then $B/mv -f backups/latest.tar backups/previous.tar; fi
$B/mv -f backups/taking.tar backups/latest.tar
printf '%s %s installing' '@PROJECT@' '@SHA@' > switching
failed=install
$B/rm -rf "releases/@SHA@"
$B/mv "@INCOMING@" "releases/@SHA@"
activate '@SHA@'
launch
failed=health
healthy '@SHA@'
# Recorded before it is published: a crash from here leaves it running and set up, never undone.
if test -f deployed.json; then
  $B/cat deployed.json > deployed.prev.tmp
  $B/mv -f deployed.prev.tmp deployed.prev.json
fi
printf '%s' '@DEPLOYED@' > deployed.tmp
$B/mv -f deployed.tmp deployed.json
$B/rm -f switching
phase=done
publish
prev=$(sha_of deployed.prev.json)
for d in releases/*; do
  n=${d#releases/}
  if test "$n" != '@SHA@' && test "$n" != "$prev"; then $B/rm -rf "$d"; fi
done
echo "deployed=@SHA@"
'''
# The deployed release is already @SHA@ (a version whose server is byte-identical to the one set up): it is started
# and published if it is not running, and nothing else changes: no backup, no new previous release.
ENSURE = COMMON + r'''if test "$(sha_of)" != '@SHA@' || test -f switching; then echo "failed=not-current"; exit 4; fi
if ! locked; then launch; fi
if healthy '@SHA@'; then publish; echo "deployed=@SHA@"; else echo "failed=health"; exit 4; fi
'''
# Undo a setup whose version was never delivered (the job failed or stopped after it): only while @SHA@ is the
# deployed release and the one before it is another. Its data goes back to latest.tar (taken just before it was
# switched in; what it wrote since is kept as backups/reverted), and the release before it, if any, is started and
# published again. A failure after the stop starts the deployed release again on whatever data is in place.
REVERT = COMMON + r'''phase=start
failed=revert
on_exit() {
  code=$?
  trap - EXIT
  set +e
  if test "$phase" = done; then exit "$code"; fi
  echo "failed=$failed"
  if test "$phase" = stopped; then
    cur=$(sha_of)
    if test -n "$cur" && test -f "releases/$cur/server" && activate "$cur"; then
      launch
      if healthy "$cur"; then publish; echo "restored=$cur"; fi
    fi
  fi
  exit 4
}
trap on_exit EXIT
if test "$(sha_of)" != '@SHA@'; then echo "reverted=not-current"; phase=done; exit 0; fi
prev=$(sha_of deployed.prev.json)
if test "$prev" = '@SHA@'; then echo "reverted=same"; phase=done; exit 0; fi
$B/rm -f app.json app.json.tmp
failed=stop
stop
phase=stopped
failed=restore
restore_data backups/reverted
if test -n "$prev" && test -f "releases/$prev/server"; then
  activate "$prev"
  $B/mv -f deployed.prev.json deployed.json
  phase=done
  launch
  if healthy "$prev"; then publish; echo "reverted=$prev"; else echo "reverted=not-healthy"; fi
else
  phase=done
  $B/rm -f server start deployed.json deployed.prev.json
  echo "reverted=nothing"
fi
$B/rm -rf "releases/@SHA@"
'''


def _check(result: Result, step: str) -> str:
    if result.code != 0:
        detail = next((line for line in result.stdout.splitlines() if '=' in line), '') or result.stderr.strip()[-200:]
        raise SetupFailed(step, f'exit {result.code}: {detail}')
    return result.stdout


def _state(channel: Channel, bin_dir: str) -> dict:
    out = _check(channel.script(_sh(STATE, bin_dir)), 'state')
    lines = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
    deployed = None
    if 'deployed' in lines:
        try:
            deployed = json.loads(lines['deployed'])
            if not (isinstance(deployed, dict) and isinstance(deployed.get('dataSchema'), int)
                    and isinstance(deployed.get('project'), str)
                    and re.fullmatch(r'[0-9a-f]{64}', str(deployed.get('sha256', '')))):
                raise ValueError
        except ValueError:
            raise SetupFailed('state', 'the deployed record on the agent is not readable') from None
    switching = lines.get('switching', '').split(' ')[0] or None
    return {'deployed': deployed, 'occupied': lines.get('occupied') == 'yes', 'switching': switching}


def _check_owner(state: dict, project: str) -> None:
    """The agent holds this project's app, or nothing: never another app's server or data."""
    deployed = state['deployed']
    if deployed is not None and deployed['project'] != project:
        raise SetupFailed('state', 'this agent holds another app; it is not set up over')
    if state['switching'] is not None and state['switching'] != project:
        raise SetupFailed('state', 'this agent was being set up for another app')
    if deployed is None and state['switching'] is None and state['occupied']:
        raise SetupFailed('state', 'this agent already has an app server or data the builder did not set up; '
                                   'it is not set up over')


def _script(text: str, p: Plan, bin_dir: str, *, sha: str, health_wait_s: int, stop_wait_s: int, **extra) -> str:
    descriptor = json.dumps({'format': 'chariot-agent-app/v1', 'port': p.port, 'health_path': '/health'},
                            separators=(',', ':'))
    out = (text.replace('@LAUNCHER@', LAUNCHER.replace('/chariot/bin', bin_dir)).replace('@SHA@', sha)
           .replace('@PORT@', str(p.port)).replace('@DESCRIPTOR@', descriptor)
           .replace('@HEALTH_WAIT@', str(int(health_wait_s))).replace('@STOP_WAIT@', str(int(stop_wait_s))))
    for key, value in extra.items():
        out = out.replace(f'@{key}@', value)
    return _sh(out, bin_dir)


def _identity_matches(channel: Channel, p: Plan, bin_dir: str) -> None:
    out = _check(channel.script(_sh(PROBE.replace('@APPLETS@', ' '.join(APPLETS)), bin_dir)), 'probe')
    seen = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
    if seen.get('app_url') != p.app_url or seen.get('app_id', '').replace('-', '').lower() != p.agent_id:
        raise SetupFailed('probe', 'this agent is not the project\'s recorded agent container')


def deploy(p: Plan, channel: Channel, *, enabled: bool = False, generate_keyring: bool = False,
           bin_dir: str = BIN, health_wait_s: int = HEALTH_WAIT_S, stop_wait_s: int = STOP_WAIT_S,
           undelivered=lambda sha: False) -> dict:
    """Run the setup for ``p`` on ``channel``. Off unless ``enabled``: the owner's decision (§7.7a). When the agent's
    deployed release is one ``undelivered(sha)`` says was never delivered, it is reverted first."""
    if not enabled:
        raise SetupFailed('disabled', 'agent-container setup is not enabled on this builder')
    if not re.fullmatch(r'[A-Za-z0-9]{1,64}', p.project) or not re.fullmatch(r'[0-9a-f]{64}', p.sha256):
        raise SetupFailed('plan', 'the project or server digest is not in its recorded form')
    _identity_matches(channel, p, bin_dir)
    state = _state(channel, bin_dir)
    _check_owner(state, p.project)
    previous = state['deployed']
    if previous is not None and previous['sha256'] != p.sha256 and undelivered(previous['sha256']):
        revert(p, channel, previous['sha256'], bin_dir=bin_dir, health_wait_s=health_wait_s, stop_wait_s=stop_wait_s)
        state = _state(channel, bin_dir)
        _check_owner(state, p.project)
        previous = state['deployed']
    if previous is not None and previous['dataSchema'] > p.data_schema:
        raise SetupFailed('state', 'this version\'s data schema is older than the deployed one')
    if previous is not None and previous['sha256'] == p.sha256 and state['switching'] is None:
        # The same server (a version that changed only its client): nothing to back up, switch or ever undo.
        result = channel.script(_script(ENSURE, p, bin_dir, sha=p.sha256, health_wait_s=health_wait_s,
                                        stop_wait_s=stop_wait_s))
        lines = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if result.code != 0 or lines.get('deployed') != p.sha256:
            raise SetupFailed('switch', f'the set-up server did not answer ({lines.get("failed", "unknown")})')
        return {'deployed': p.sha256, 'appUrl': p.app_url, 'dataSchema': p.data_schema,
                'previous': p.sha256, 'unchanged': True}
    incoming = f'incoming-{p.sha256}'
    _check(channel.script(_sh(PREPARE.replace('@INCOMING@', incoming), bin_dir)), 'upload')
    try:
        tar = bundle(p)
    except OSError as exc:
        raise SetupFailed('upload', f'the server could not be read ({type(exc).__name__})') from None
    _check(channel.receive(tar, incoming), 'upload')
    deployed = json.dumps({'sha256': p.sha256, 'dataSchema': p.data_schema, 'versionCode': p.version_code,
                           'project': p.project}, separators=(',', ':'), sort_keys=True)
    result = channel.script(_script(SWITCH, p, bin_dir, sha=p.sha256, health_wait_s=health_wait_s,
                                    stop_wait_s=stop_wait_s, INCOMING=incoming, DEPLOYED=deployed, PROJECT=p.project,
                                    GENERATE_KEYRING='yes' if generate_keyring else 'no'))
    lines = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.code != 0 or lines.get('deployed') != p.sha256:
        raise SetupFailed('switch', f'the new server was not set up ({lines.get("failed", "unknown")}); restored: '
                                    f'{lines.get("restored", "nothing was changed")}')
    return {'deployed': p.sha256, 'appUrl': p.app_url, 'dataSchema': p.data_schema,
            'previous': (previous or {}).get('sha256'), 'unchanged': False}


def revert(p: Plan, channel: Channel, sha: str, *, bin_dir: str = BIN, health_wait_s: int = HEALTH_WAIT_S,
           stop_wait_s: int = STOP_WAIT_S) -> str:
    """Undo the setup of ``sha`` (a version never delivered): see REVERT. Returns what is running afterwards."""
    if not re.fullmatch(r'[0-9a-f]{64}', sha):
        raise SetupFailed('revert', 'the server digest is not in its recorded form')
    _identity_matches(channel, p, bin_dir)
    _check_owner(_state(channel, bin_dir), p.project)
    result = channel.script(_script(REVERT, p, bin_dir, sha=sha, health_wait_s=health_wait_s,
                                    stop_wait_s=stop_wait_s))
    lines = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.code != 0 or 'reverted' not in lines:
        raise SetupFailed('revert', f'the undelivered server could not be undone ({lines.get("failed", "unknown")})')
    return lines['reverted']


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
        # One setup, revert or invite at a time: an invite never runs while the data it would touch is being backed
        # up, restored or replaced (it is refused as busy instead, so a phone never waits on a setup).
        self._busy = threading.Lock()

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

    def _plan(self, project: str, version_code: int, elf: Path, manifest: dict, app_url: str | None) -> Plan:
        container = self.ledger.agent_container(project)
        if container is None:
            raise SetupFailed('plan', 'no agent container is recorded for this project')
        if app_url is not None and container['appUrl'] != app_url:
            raise SetupFailed('plan', 'the agent container changed after the app\'s client was built')
        try:
            schema = server_delivery.data_schema(manifest['schema'], manifest['signin']['revision'])
            sha = manifest['elf']['sha256']
        except (server_delivery.Refused, KeyError, TypeError) as exc:
            raise SetupFailed('plan', f'the server manifest cannot be used ({type(exc).__name__})') from None
        return Plan(project, version_code, container['agentId'], container['appUrl'], Path(elf), sha, schema,
                    self.config.port)

    def _undelivered(self, project: str):
        delivered = set()
        for v in self.ledger.versions(project):
            server = self.ledger.server(project, v['versionCode'])
            if server is not None:
                delivered.add(server['sha256'])
        return lambda sha: sha not in delivered

    def deploy(self, project: str, version_code: int, elf: Path, manifest: dict, app_url: str | None = None) -> dict:
        """Set the version's server up; a release the agent runs that was never delivered is reverted first."""
        p = self._plan(project, version_code, elf, manifest, app_url)
        with self._busy:
            return deploy(p, self.channel(self.name_for(p.agent_id)), enabled=True,
                          generate_keyring=self.config.generate_keyring, undelivered=self._undelivered(project))

    def revert(self, project: str, version_code: int, elf: Path, manifest: dict) -> str:
        """Undo this version's setup after its delivery did not happen (see ``revert``)."""
        p = self._plan(project, version_code, elf, manifest, None)
        with self._busy:
            return revert(p, self.channel(self.name_for(p.agent_id)), p.sha256)

    def invite(self, project: str, sha256: str, account: str) -> str:
        """An invite from the project's server in its recorded agent container (see ``invite``)."""
        container = self.ledger.agent_container(project)
        if container is None:
            raise InviteRefused('not_hosted')
        if not self._busy.acquire(blocking=False):
            raise InviteRefused('busy')  # its server is being set up: ask again in a moment
        try:
            try:
                name = self.name_for(container['agentId'])
            except SetupFailed:
                raise InviteRefused('unreachable') from None
            return invite(self.channel(name), sha256, account)
        finally:
            self._busy.release()


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
