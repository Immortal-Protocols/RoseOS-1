"""The standalone app-builder host (docs/APP-BUILDER.md §6): the builder on its own machine, without the Rose
conversation server. Source only; nothing here is deployed, configured or enabled.

It serves, from one JSON config file:

- a signed TLS listener with ``/pair``, ``/hello`` and the builder API v1 routes only, with no voice or phone
  websockets (``build_remote_app(..., voice=False)``). The builder client pairs with it as its own device.
- a loopback-only admin listener for pairing management (``/api/pair/*``), so codes are minted on the host itself.
- the single build worker.

The writer is ``CodexGenerator`` launched with the configured command (on a host, one that runs it as a separate OS
user). With no writer configured, jobs fail honestly. Each project's signing key is backed up by the configured
backup command before its first delivery (``CommandBackup``), unless the owner explicitly sets
``requireKeyBackup:false`` to accept local key persistence only. Key creation stays
off unless the config turns it on. Usage: ``python -m rose.app_builder.host --config /path/to/host.json``.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from . import dependencies, toolchain
from .ledger import Ledger
from .service import Builder

SCHEMA_VERSION = 1
# The builder's own settings: the same in the standalone host's config and in the builder config the Rose server
# reads with ``--app-builder`` (docs/APP-BUILDER.md §6), which has no listener of its own.
BUILDER_KEYS = {'schemaVersion', 'root', 'sdk', 'javaHome', 'buildTimeoutS', 'keyCreation', 'writer', 'backup', 'requireKeyBackup',
                'libraryStore', 'clientServer', 'agentHost', 'gradle'}
BUILDER_REQUIRED = {'schemaVersion', 'root', 'sdk'}
KEYS = BUILDER_KEYS | {'remoteDir', 'name', 'port', 'adminPort', 'hosts'}
REQUIRED = BUILDER_REQUIRED | {'remoteDir', 'name', 'port', 'adminPort', 'hosts'}
RECEIPT_MAX = 200
NO_WRITER = 'No app writer is set up on this builder host, so nothing was written.'


class ConfigError(ValueError):
    """A host config this module will not run with; the message names the field."""


@dataclass(frozen=True)
class Command:
    argv: tuple
    timeout_s: float


@dataclass(frozen=True)
class GradleConfig:
    """``gradle`` (docs/APP-BUILDER.md §2.4): the pinned stack rose.app_builder.gradle_prepare made (its ``jdk``,
    ``distribution``, ``sdk`` and read-only ``dependencyCache``) and the build isolation ordinary Gradle projects run
    in (``bubblewrap``, Linux); there is no unisolated Gradle build. ``seatbelt`` (macOS) is named so a config that
    asks for it is refused with the reason: it is not qualified (rose.app_builder.isolation.SeatbeltIsolation)."""
    jdk: Path
    distribution: Path
    sdk: Path
    dependency_cache: Path
    isolation: str
    bubblewrap: Path = Path('/usr/bin/bwrap')
    build_timeout_s: float = 1800.0


@dataclass(frozen=True)
class BuilderConfig:
    """The builder alone, served by the Rose server's own signed listener (``rose.app --app-builder``)."""
    root: Path
    sdk: Path
    java_home: Path | None = None
    build_timeout_s: float = 600.0
    key_creation: bool = False
    writer: Command | None = None
    backup: Command | None = None
    require_key_backup: bool = True
    library_store: Path | None = None
    agent_host: object | None = None  # app_host.AgentHostConfig (§7.7a)
    gradle: GradleConfig | None = None


@dataclass(frozen=True)
class HostConfig:
    root: Path
    remote_dir: Path
    name: str
    port: int
    admin_port: int
    hosts: tuple
    sdk: Path
    java_home: Path | None = None
    build_timeout_s: float = 600.0
    key_creation: bool = False
    writer: Command | None = None
    backup: Command | None = None
    require_key_backup: bool = True
    library_store: Path | None = None
    agent_host: object | None = None
    gradle: GradleConfig | None = None


def _path(value, name: str) -> Path:
    if not isinstance(value, str) or not value.startswith('/') or '\0' in value or len(value) > 4096:
        raise ConfigError(f'{name} must be an absolute path')
    return Path(os.path.normpath('/' + value.lstrip('/')))  # '//x' and '/a/../x' name the directory they mean


def _port(value, name: str) -> int:
    if type(value) is not int or not 1024 <= value <= 65535:
        raise ConfigError(f'{name} must be a port from 1024 to 65535')
    return value


def _seconds(value, name: str, default: float) -> float:
    if value is None:
        return default
    if type(value) not in (int, float) or not 1 <= value <= 24 * 3600:
        raise ConfigError(f'{name} must be 1 to 86400 seconds')
    return float(value)


def _command(value, name: str, default_timeout: float) -> Command | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {'argv', 'timeoutS'} or 'argv' not in value:
        raise ConfigError(f'{name} must be {{argv, timeoutS?}}')
    argv = value['argv']
    if (not isinstance(argv, list) or not argv or len(argv) > 32
            or not all(isinstance(a, str) and a and '\0' not in a for a in argv) or not argv[0].startswith('/')):
        raise ConfigError(f'{name}.argv must be a list of strings starting with an absolute program path')
    return Command(tuple(argv), _seconds(value.get('timeoutS'), f'{name}.timeoutS', default_timeout))


def _checked(data, keys: set, required: set) -> dict:
    if not isinstance(data, dict):
        raise ConfigError('the config must be a JSON object')
    if type(data.get('schemaVersion')) is not int or data['schemaVersion'] != SCHEMA_VERSION:
        raise ConfigError('schemaVersion must be 1')
    unknown = set(data) - keys
    if unknown:
        raise ConfigError('unknown field ' + sorted(unknown)[0])
    missing = required - set(data)
    if missing:
        raise ConfigError('missing field ' + sorted(missing)[0])
    return data


def _builder_fields(data: dict) -> dict:
    """The builder's settings, parsed by the one rule both configs share."""
    key_creation = data.get('keyCreation', False)
    if type(key_creation) is not bool:
        raise ConfigError('keyCreation must be true or false')
    require_key_backup = data.get('requireKeyBackup', True)
    if type(require_key_backup) is not bool:
        raise ConfigError('requireKeyBackup must be true or false')
    # Kept as an inert config key so an existing server can start and preserve its ledger.
    # No legacy paths are opened, checked or executed. Remove clientServer after migration.
    return dict(
        root=_path(data['root'], 'root'), sdk=_path(data['sdk'], 'sdk'),
        java_home=None if data.get('javaHome') is None else _path(data['javaHome'], 'javaHome'),
        build_timeout_s=_seconds(data.get('buildTimeoutS'), 'buildTimeoutS', 600.0), key_creation=key_creation,
        writer=_command(data.get('writer'), 'writer', 900.0), backup=_command(data.get('backup'), 'backup', 120.0),
        require_key_backup=require_key_backup,
        library_store=None if data.get('libraryStore') is None else _path(data['libraryStore'], 'libraryStore'),
        agent_host=_agent_host_config(data.get('agentHost')),
        gradle=_gradle_config(data.get('gradle')))


def parse_builder_config(data) -> BuilderConfig:
    """The builder config ``rose.app --app-builder`` reads: the standalone host's builder fields, with no listener
    fields (the Rose server's own listener, pairing and devices serve it)."""
    return BuilderConfig(**_builder_fields(_checked(data, BUILDER_KEYS, BUILDER_REQUIRED)))


def parse_config(data) -> HostConfig:
    _checked(data, KEYS, REQUIRED)
    if not isinstance(data['name'], str) or not 0 < len(data['name'].strip()) <= 64:
        raise ConfigError('name must be 1 to 64 characters')
    hosts = data['hosts']
    if not isinstance(hosts, list) or not hosts or len(hosts) > 8 or not all(isinstance(h, str) for h in hosts):
        raise ConfigError('hosts must be 1 to 8 host names or addresses')
    from ..remote import configured_hosts
    try:  # the same rule pairing applies, so a host that can never pair is refused at load
        hosts = configured_hosts(hosts)
    except ValueError as exc:
        raise ConfigError(f'hosts: {exc}') from None
    builder = _builder_fields(data)
    port, admin_port = _port(data['port'], 'port'), _port(data['adminPort'], 'adminPort')
    if port == admin_port:
        raise ConfigError('port and adminPort must differ')
    remote_dir = _path(data['remoteDir'], 'remoteDir')
    if not separate(builder['root'], remote_dir):
        raise ConfigError('root and remoteDir must be separate directories')
    return HostConfig(remote_dir=remote_dir, name=data['name'].strip(), port=port, admin_port=admin_port,
                      hosts=tuple(hosts), **builder)


def separate(a: Path, b: Path) -> bool:
    """Neither directory is, or is inside, the other."""
    return not (a == b or a in b.parents or b in a.parents)


def _agent_host_config(value):
    """The existing Chariot agent connection; the building agent chooses and builds its backend there."""
    if value is None:
        return None
    from .app_host import AGENT_NAME, APP_PORT, RESERVED_PORTS, AgentHostConfig
    if not isinstance(value, dict) or set(value) - {'chariot', 'home', 'agents', 'generateKeyring', 'port'} \
            or not {'chariot', 'home', 'agents'} <= set(value):
        raise ConfigError('agentHost must contain chariot, home and agents (generateKeyring and port optional)')
    agents = value['agents']
    if (not isinstance(agents, list) or not 1 <= len(agents) <= 16 or len(set(agents)) != len(agents)
            or not all(isinstance(a, str) and AGENT_NAME.fullmatch(a) for a in agents)):
        raise ConfigError('agentHost.agents must be 1 to 16 distinct agent names')
    generate = value.get('generateKeyring', False)
    if type(generate) is not bool:
        raise ConfigError('agentHost.generateKeyring must be true or false')
    port = value.get('port', APP_PORT)
    if type(port) is not int or not 1024 <= port <= 65535 or port in RESERVED_PORTS:
        raise ConfigError('agentHost.port must be an unprivileged port other than 8088 and 42617')
    return AgentHostConfig(_path(value['chariot'], 'agentHost.chariot'), _path(value['home'], 'agentHost.home'),
                           tuple(agents), generate, port)


GRADLE_KEYS = {'jdk', 'distribution', 'sdk', 'dependencyCache', 'isolation', 'bubblewrap', 'buildTimeoutS'}
GRADLE_ISOLATIONS = ('bubblewrap', 'seatbelt')


def _gradle_config(value) -> GradleConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - GRADLE_KEYS \
            or not {'jdk', 'distribution', 'sdk', 'dependencyCache', 'isolation'} <= set(value):
        raise ConfigError('gradle must be {jdk, distribution, sdk, dependencyCache, isolation, bubblewrap?, '
                          'buildTimeoutS?}')
    if value['isolation'] not in GRADLE_ISOLATIONS:
        raise ConfigError('gradle.isolation must be bubblewrap')
    if value['isolation'] == 'seatbelt':  # allow-by-default: loopback (Rose's own pairing) and launchd stay reachable
        raise ConfigError('gradle.isolation seatbelt (macOS) is not qualified: it leaves localhost, launchd and '
                          'other services reachable to the build\'s code. Gradle builds need bubblewrap on Linux.')
    if 'bubblewrap' in value and value['isolation'] != 'bubblewrap':
        raise ConfigError('gradle.bubblewrap is only for the bubblewrap isolation')
    paths = {}
    for key in ('jdk', 'distribution', 'sdk', 'dependencyCache'):
        paths[key] = _facility_path(_path(value[key], f'gradle.{key}'), key, section='gradle')
    bwrap = (_facility_path(_path(value['bubblewrap'], 'gradle.bubblewrap'), 'bubblewrap', directory=False,
                            section='gradle') if 'bubblewrap' in value else Path('/usr/bin/bwrap'))
    return GradleConfig(paths['jdk'], paths['distribution'], paths['sdk'], paths['dependencyCache'],
                        value['isolation'], bwrap, _seconds(value.get('buildTimeoutS'), 'gradle.buildTimeoutS', 1800.0))


def gradle_tools(config: GradleConfig | None):
    """The Gradle toolchain and its isolation, checked now: a configured stack that is not usable fails the host at
    startup instead of every job. The isolation shows the build nothing that is not mounted."""
    if config is None:
        return None
    from .gradle import GradleTools
    from .isolation import BubblewrapIsolation
    isolation = BubblewrapIsolation(config.bubblewrap)  # the only qualified isolation (_gradle_config)
    tools = GradleTools(config.jdk, config.distribution, config.sdk, config.dependency_cache, isolation,
                        timeout_s=config.build_timeout_s)
    missing = tools.available()
    if missing:
        raise ConfigError(f'gradle: {missing}')
    return tools


def _facility_path(path: Path, name: str, *, directory: bool = True, section: str = 'gradle') -> Path:
    """Owner-supplied facilities use the canonical absolute paths isolation preserves."""
    try:
        if (not path.is_absolute() or path.resolve(strict=True) != path or
                not (path.is_dir() if directory else path.is_file())):
            raise ValueError
    except (OSError, ValueError, RuntimeError):
        raise ConfigError(f'{section}.{name} must be an existing canonical absolute '
                          + ('directory' if directory else 'file')) from None
    return path


def _read(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, RecursionError) as exc:
        raise ConfigError(f'the config could not be read ({type(exc).__name__})') from None


def load_config(path: Path) -> HostConfig:
    return parse_config(_read(path))


def load_builder_config(path: Path) -> BuilderConfig:
    return parse_builder_config(_read(path))


def no_writer(source, kind, request, cancelled):
    raise toolchain.StepFailed('internal', NO_WRITER, retryable=True)


def library_store(path: Path | None) -> dependencies.RecordedStore | None:
    """Validate a configured offline catalog before constructing the writer or listener.

    Artifact digests and JAR contents are still verified for each job by the existing resolver.
    An omitted store preserves framework-only builds; a broken configured store is never silently disabled.
    """
    if path is None:
        return None
    try:
        index = path / 'index.json'
        if path.is_symlink() or not path.is_dir() or index.is_symlink() or not index.is_file():
            raise ValueError
        data = json.loads(index.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or type(data.get('schemaVersion')) is not int
                or data['schemaVersion'] != 1 or not isinstance(data.get('artifacts'), dict)):
            raise ValueError
        for coordinate, digest in data['artifacts'].items():
            if (not dependencies.COORDINATE.fullmatch(coordinate) or not isinstance(digest, str)
                    or not dependencies.SHA256.fullmatch(digest)):
                raise ValueError
            jar = path / f'{digest}.jar'
            if jar.is_symlink() or not jar.is_file():
                raise ValueError
    except (OSError, ValueError, RecursionError):
        raise ConfigError('libraryStore must contain a readable schemaVersion 1 index with valid coordinates, '
                          'SHA-256 digests and available plain JAR files') from None
    return dependencies.RecordedStore(path)


def valid_receipt(receipt) -> bool:
    """The one rule for a backup receipt, used by CommandBackup and the Builder alike."""
    return isinstance(receipt, str) and 0 < len(receipt) <= RECEIPT_MAX and receipt.isprintable()


class CommandBackup:
    """backup(keys_dir, project_id, package) by running ``argv + [keys_dir, project_id, package]``. The command copies
    the key bundle off the host (for example encrypted to the owner's offline key) and prints one receipt line, such as
    the hash of what it stored (at most 200 printable characters; tabs become spaces). Its output beyond that line, and
    anything it writes to stderr, is never logged or shown.

    It runs in its own session with its own empty HOME and working directory, outside the key directory, so a tool's
    own state never lands next to the key. When the command finishes, fails or times out, its whole process group is
    killed, so nothing it started keeps running with access to the key. A failure, a timeout or an unusable receipt
    raises, and the builder then delivers nothing."""

    def __init__(self, command: Command):
        self.command = command

    @staticmethod
    def _failed(why: str = 'could not be backed up') -> toolchain.StepFailed:
        return toolchain.StepFailed('signing', f'This app\'s signing key {why}, so the app was not delivered.',
                                    retryable=True)

    def __call__(self, keys: Path, project_id: str, package: str) -> str:
        scratch = Path(tempfile.mkdtemp(prefix='rose-key-backup-'))
        os.chmod(scratch, 0o700)
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': str(scratch), 'TMPDIR': str(scratch)}
        proc = None
        try:
            with open(scratch / '.stdout', 'w+b') as out:  # a file, so a leftover helper holding it changes nothing
                try:
                    proc = subprocess.Popen([*self.command.argv, str(keys), project_id, package], env=env,
                                            cwd=str(scratch), stdin=subprocess.DEVNULL, stdout=out,
                                            stderr=subprocess.DEVNULL, start_new_session=True)
                    code = proc.wait(timeout=self.command.timeout_s)
                except (OSError, subprocess.TimeoutExpired):
                    raise self._failed() from None
                finally:
                    if proc is not None:
                        with contextlib.suppress(ProcessLookupError, PermissionError):
                            os.killpg(proc.pid, signal.SIGKILL)  # the whole group, finished or not
                        with contextlib.suppress(Exception):
                            proc.wait(timeout=5)
                out.seek(0)
                first = out.read(4096).decode('utf-8', 'replace').splitlines()
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        receipt = (first[0] if first else '').replace('\t', ' ').strip()
        if code != 0 or not receipt:
            raise self._failed()
        if not valid_receipt(receipt):
            raise self._failed('was backed up with an unusable receipt (over 200 characters or not printable)')
        return receipt


@dataclass
class Host:
    config: HostConfig
    builder: Builder
    remote: object
    remote_app: object
    admin_app: object
    worker: threading.Thread | None = field(default=None)

    def start_worker(self) -> threading.Thread:
        self.worker = start_worker(self.builder)
        return self.worker

    def stop(self, timeout_s: float = 30.0) -> None:
        stop_worker(self.builder, self.worker, timeout_s)


def start_worker(builder: Builder) -> threading.Thread:
    """The single build worker, on its own thread."""
    worker = threading.Thread(target=builder.serve_forever, name='app-builder', daemon=True)
    worker.start()
    return worker


def stop_worker(builder: Builder, worker: threading.Thread | None, timeout_s: float = 30.0) -> None:
    builder.stopping.set()
    builder.wake.set()
    if worker is not None:
        worker.join(timeout_s)


def build_builder(config: BuilderConfig | HostConfig, generate=None, backup=None, *,
                  private: tuple = (), apart_from: tuple = ()) -> Builder:
    """The builder, its writer, signing-key backup and Level 2 facilities from one config, wired but not started.
    Shared by the standalone host and the Rose server (``rose.app --app-builder``), so both run the same builder on
    the same ledger root. Everything configured is checked before any directory is created. The root and each of
    ``private`` (the standalone host's pairing directory) are then created 0700, refusing a symbolic link, and the
    root must be separate from each of them and from ``apart_from`` (the Rose server's own configuration)."""
    resolver = library_store(config.library_store)
    client_codegen = None
    gradle = gradle_tools(config.gradle)
    for other in apart_from:
        if not separate(config.root.resolve(), Path(other).resolve()):
            raise ConfigError("the builder root must be separate from the server's own configuration")
    for directory in (config.root, *private):
        if directory.is_symlink():
            raise ConfigError(f'{directory} must not be a symbolic link')
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    root = config.root.resolve()
    if not all(separate(root, Path(d).resolve()) for d in (*private, *apart_from)):
        raise ConfigError('root and remoteDir must be separate directories')
    if generate is None:
        if config.writer is not None:
            from .generator import CodexGenerator
            generate = CodexGenerator(timeout_s=config.writer.timeout_s, command=list(config.writer.argv),
                                      libraries=resolver.available if resolver is not None else None,
                                      backend=config.agent_host is not None, gradle=gradle is not None)
        else:
            generate = no_writer
    if backup is None and config.backup is not None:
        backup = CommandBackup(config.backup)
    tools = toolchain.Tools(config.sdk, config.java_home, timeout_s=config.build_timeout_s,
                            key_creation=config.key_creation)
    ledger = Ledger(config.root)
    hosting = None
    if config.agent_host is not None:
        from . import codegen
        from .app_host import AgentHosting
        hosting = AgentHosting(config.agent_host, ledger)
        client_codegen = codegen  # the API client an agent-built backend's app is built with
    return Builder(config.root, tools, generate, ledger=ledger, backup=backup, resolver=resolver,
                   require_key_backup=config.require_key_backup,
                   server_url=ledger.backend_endpoint, hosting=hosting, codegen=client_codegen, gradle=gradle)


def build_host(config: HostConfig, generate=None, backup=None) -> Host:
    """Everything the standalone host runs, wired but not started: tests drive it with fakes, ``main`` serves it."""
    from fastapi import FastAPI

    from ..remote import Remote, add_pairing_api, build_remote_app

    builder = build_builder(config, generate, backup, private=(config.remote_dir,))
    remote = Remote(directory=config.remote_dir, port=config.port, name=config.name,
                    hosts=lambda: list(config.hosts))
    remote_app = build_remote_app(SimpleNamespace(app_builder=builder, phone=None), remote, voice=False)
    admin_app = FastAPI(title='Rose app builder admin', docs_url=None, redoc_url=None, openapi_url=None)
    add_pairing_api(admin_app, remote, config.admin_port)
    return Host(config, builder, remote, remote_app, admin_app)


def servers(host: Host):
    """The two uvicorn servers: the signed TLS listener on every interface and the admin listener on loopback."""
    import uvicorn

    from ..remote import REMOTE_CONCURRENCY, bind_socket, remote_http_protocol

    class Server(uvicorn.Server):  # signals are handled once, in _serve, for both servers
        @contextlib.contextmanager
        def capture_signals(self):
            yield

    sock = bind_socket(host.config.port)
    remote_cfg = uvicorn.Config(host.remote_app, host='0.0.0.0', port=host.config.port,
                                ssl_keyfile=str(host.remote.identity.key_path),
                                ssl_certfile=str(host.remote.identity.cert_path), lifespan='off',
                                proxy_headers=False, server_header=False, access_log=False,
                                http=remote_http_protocol(), limit_concurrency=REMOTE_CONCURRENCY)
    admin_cfg = uvicorn.Config(host.admin_app, host='127.0.0.1', port=host.config.admin_port, lifespan='off',
                               server_header=False, access_log=False)
    host.remote.listening = True
    return [(Server(remote_cfg), [sock]), (Server(admin_cfg), None)]


async def _serve(pairs) -> None:
    """Serve until a signal or until a server stops; a second signal forces exit. A server that failed is re-raised,
    so the host exits non-zero and a supervisor sees it."""
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def on_signal():
        if stop.is_set():
            for server, _ in pairs:
                server.force_exit = True
        stop.set()

    sigs = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    for sig in sigs:
        with contextlib.suppress(ValueError, RuntimeError, NotImplementedError):
            loop.add_signal_handler(sig, on_signal)
    tasks = [asyncio.create_task(s.serve(sockets=socks)) for s, socks in pairs]
    waiter = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait([*tasks, waiter], return_when=asyncio.FIRST_COMPLETED)
    finally:
        for server, _ in pairs:
            server.should_exit = True
        waiter.cancel()
        results = await asyncio.gather(*tasks, waiter, return_exceptions=True)
        for sig in sigs:
            with contextlib.suppress(ValueError, RuntimeError, NotImplementedError):
                loop.remove_signal_handler(sig)
    for result in results:
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
            raise result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Rose standalone app-builder host (source; see APP-BUILDER.md §6)')
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f'[rose-builder] config refused: {exc}', flush=True)
        return 2
    try:
        host = build_host(config)
        pairs = servers(host)
    except (ConfigError, OSError) as exc:
        print(f'[rose-builder] could not start: {type(exc).__name__}: {exc}', flush=True)
        return 2
    host.start_worker()
    try:
        asyncio.run(_serve(pairs))
    except Exception as exc:  # a listener failed: exit non-zero, naming only the error type
        print(f'[rose-builder] a listener stopped: {type(exc).__name__}', flush=True)
        return 1
    finally:
        host.stop()
        host.remote.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
