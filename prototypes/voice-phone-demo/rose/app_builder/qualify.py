"""Qualify a host for client-server builds (docs/APP-BUILDER.md §5.2, §7.7a): one command that says whether this
machine, with this builder config, can build a generated app's server as the builder will, and if not, what it lacks.

    python -m rose.app_builder.qualify --config builder.json

The config is the builder config ``rose.app --app-builder`` reads (§6.1), with ``clientServer`` set. Each check runs
the builder's own code, never a weaker stand-in, and a failure names the missing capability; nothing here retries
with less isolation, installs anything, changes a setting or contacts a network. The checks, in order:

1. ``config``: the builder config parses and sets ``clientServer``.
2. ``platform``: Linux x86-64, run as a user other than root (bubblewrap's unprivileged namespaces).
3. ``facilities``: the same checks the server runs at start (``host.client_server_facilities``): the Rust toolchain
   matches its owner-supplied pin, bubblewrap is executable, the crate store and runtime root are readable.
4. ``confinement``: a probe run through the configured bubblewrap exactly as builds run (``BubblewrapIsolation``),
   which must find only the loopback interface and no network, no ``/etc`` or home, a read-only folder read-only (a
   throwaway one of its own, never the pinned toolchain), and its own job folder writable.
5. ``build``: one throwaway client-server job (a built-in one-route notes app) through the real ``Builder``, in a
   fresh folder inside the configured root (where real jobs run; it is removed afterwards) with throwaway signing
   keys, after the root and library checks ``build_builder`` makes: it must end ``ready`` with a static musl server ELF whose manifest
   ``server_manifest.check`` accepts. The temporary root, its keys and the APK are removed afterwards.

A check that cannot run because an earlier one failed is reported ``skipped``. Output is one JSON line per check
(``{"check", "ok", "detail"}``) and a final ``{"qualified": bool}``. Exit 0 when every check passed, 1 when one
failed, 2 on usage.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from . import host, server_manifest, toolchain
from .ledger import Ledger
from .service import Builder

# The built-in app: one authenticated route over one account table, and handlers for exactly that contract.
CONTRACT = {'schemaVersion': 1, 'api': 1,
            'types': {'Note': {'fields': {'id': 'int', 'text': 'string'}},
                      'NoteList': {'fields': {'notes': {'type': 'list', 'of': 'Note'}}}},
            'routes': {'listNotes': {'method': 'GET', 'path': '/app/notes', 'response': 'NoteList'}}}
HANDLERS = '''use crate::api::Refusal;
use rose_app_runtime::server::Ctx;

pub struct App;

impl crate::api::Handlers for App {
    fn list_notes(_ctx: &Ctx) -> Result<crate::api::NoteList, Refusal> {
        Ok(crate::api::NoteList { notes: vec![] })
    }
}
'''
MIGRATION = 'CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT);\n'
INTERNET = '<uses-permission android:name="android.permission.INTERNET"/>'
REQUEST = {'schemaVersion': 1, 'clientRequestId': 'qualify', 'description': 'a notes app (host qualification)'}
PROBE_TIMEOUT_S = 60
# Run inside the confinement: every line is what it found. Nothing it tries leaves the job folder.
# Shared by the probe and its tests: a folder's visible contents, apart from the empty folders bubblewrap creates
# on the way to its own mounts (job, shown). A home of '/' is covered by the /etc and /root findings.
EXPOSED = r'''
import os


def exposed(folder, mounts):
    folder = folder.rstrip("/")
    if not folder or not os.path.isdir(folder):
        return False
    leads = {m[len(folder) + 1:].split("/")[0] for m in mounts if m.startswith(folder + "/")}
    return any(entry not in leads for entry in os.listdir(folder))
'''
PROBE = EXPOSED + r'''
import json, socket, sys
job, shown, home = sys.argv[1], sys.argv[2], sys.argv[3]
found = {}

# A network namespace has only loopback; any other interface means the build could reach a network.
names = sorted(name for _, name in socket.if_nameindex())
found["interfaces"] = "loopback only" if names in ([], ["lo"]) else ",".join(names)
try:
    socket.create_connection(("1.1.1.1", 443), timeout=3).close(); found["network"] = "reachable"
except OSError:
    found["network"] = "none"
found["etc"] = "visible" if os.path.exists("/etc/passwd") else "hidden"
found["home"] = "visible" if exposed(home, (job, shown)) or exposed("/root", (job, shown)) else "hidden"
try:
    open(os.path.join(shown, ".qualify-write"), "w").close(); found["readonly"] = "writable"
except OSError:
    found["readonly"] = "read-only"
try:
    with open(os.path.join(job, "ok"), "w") as f: f.write("ok")
    found["job"] = "writable"
except OSError:
    found["job"] = "not writable"
print(json.dumps(found))
'''
CONFINED = {'interfaces': 'loopback only', 'network': 'none', 'etc': 'hidden', 'home': 'hidden', 'readonly': 'read-only', 'job': 'writable'}


def generate(source, kind, request, cancelled):
    """The built-in writer: the one-route notes app over the builder's skeleton."""
    manifest = source / 'AndroidManifest.xml'
    text = manifest.read_text()
    if INTERNET not in text:
        manifest.write_text(text.replace('  <application', f'  {INTERNET}\n  <application', 1))
    (source / 'rose-app.json').write_text(json.dumps({'schemaVersion': 1, 'profile': 'client-server-v1'}))
    (source / 'contract').mkdir(exist_ok=True)
    (source / 'contract/api.json').write_text(json.dumps(CONTRACT))
    (source / 'server/src/handlers').mkdir(parents=True, exist_ok=True)
    (source / 'server/src/handlers/mod.rs').write_text(HANDLERS)
    (source / 'server/migrations/account').mkdir(parents=True, exist_ok=True)
    (source / 'server/migrations/account/0001_notes.sql').write_text(MIGRATION)


class Failed(Exception):
    """A check that did not pass; the message names what is missing."""


def check_config(path: Path):
    try:
        config = host.load_builder_config(path)
    except host.ConfigError as exc:
        raise Failed(f'the builder config was refused: {exc}') from None
    if config.client_server is None:
        raise Failed('the builder config sets no clientServer, so this builder builds no servers')
    return config


def check_platform(system=platform.system, machine=platform.machine, euid=os.geteuid):
    if (system(), machine()) != ('Linux', 'x86_64'):
        raise Failed(f'client-server builds need Linux x86-64, not {system()} {machine()}')
    if euid() == 0:
        raise Failed('run as an unprivileged user: bubblewrap confinement is not checked as root')
    return f'{system()} {machine()}, uid {euid()}'


def check_facilities(config) -> dict:
    try:
        return host.client_server_facilities(config.client_server, config.build_timeout_s)
    except host.ConfigError as exc:
        raise Failed(str(exc)) from None


def check_confinement(facilities: dict, run=subprocess.run) -> str:
    isolation = facilities['server_isolation']
    with tempfile.TemporaryDirectory(prefix='rose-qualify-probe-') as tmp:
        job, shown = Path(tmp) / 'job', Path(tmp) / 'shown'
        (job / 'home').mkdir(parents=True)
        shown.mkdir()
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(job / 'home'), 'LANG': 'C.UTF-8'}
        try:
            argv, run_env = isolation.command(['/usr/bin/python3', '-c', PROBE, str(job), str(shown),
                                               str(Path.home())], env, writable=job, readonly=(shown,))
        except toolchain.StepFailed as exc:  # the real provider's refusal (BuildFailed), named
            raise Failed(f'the build confinement cannot be set up here: {exc.summary}') from None
        except ValueError as exc:
            raise Failed(f'the build confinement cannot be set up here: {exc}') from None
        try:
            done = run(argv, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S, env=run_env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Failed(f'the confinement probe did not run ({type(exc).__name__})') from None
        if done.returncode != 0:
            raise Failed('bubblewrap refused the build confinement on this host: '
                         + (done.stderr.strip().splitlines() or ['no message'])[-1][:300])
        try:
            found = json.loads(done.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise Failed('the confinement probe gave no result') from None
    judge(found)
    return 'loopback only, no network, /etc and home hidden, read-only folder read-only, job writable'


def judge(found: dict) -> None:
    """The probe's findings must be exactly the confined ones, every key present."""
    wrong = {k: found.get(k, 'not checked') for k in sorted(set(found) | set(CONFINED)) if found.get(k) != CONFINED.get(k)}
    if wrong:
        raise Failed('the build confinement is incomplete here: '
                     + ', '.join(f'{k} {v}' for k, v in wrong.items()))


def check_build(config, facilities: dict, apart_from=None) -> str:
    """One throwaway job where real jobs run: in a fresh folder inside the configured root (so its parents, volume
    and mount options are the real jobs'), after the same root and library checks ``build_builder`` makes."""
    if apart_from is None:
        from ..remote import config_dir
        apart_from = (config_dir(),)
    try:
        host.library_store(config.library_store)
        if config.root.is_symlink():
            raise host.ConfigError(f'{config.root} must not be a symbolic link')
        if not all(host.separate(config.root.resolve(), Path(d).resolve()) for d in apart_from):
            raise host.ConfigError("the builder root must be separate from the server's own configuration")
        config.root.mkdir(mode=0o700, parents=True, exist_ok=True)  # as build_builder does at start
    except (host.ConfigError, OSError) as exc:
        raise Failed(f'the builder root is not usable: {exc}') from None
    with tempfile.TemporaryDirectory(prefix='.qualify-', dir=config.root) as tmp:
        root = Path(tmp) / 'builder'
        tools = toolchain.Tools(config.sdk, config.java_home, timeout_s=config.build_timeout_s, key_creation=True)
        if missing := tools.available():
            raise Failed(f'the Android toolchain is not usable: {missing}')
        ledger = Ledger(root)
        try:
            builder = Builder(root, tools, generate, ledger=ledger, backup=lambda *_: 'qualify-throwaway-key',
                              **facilities)
            started = builder.create('qualify', REQUEST)
            builder.run_next()
            job = builder.job(started['jobId'])
            if job is None or job['state'] != 'ready':
                failure = (job or {}).get('failure') or {}
                raise Failed(f'the test app did not build: {failure.get("category", "unknown")}: '
                             f'{failure.get("summary", (job or {}).get("message", ""))}'[:400])
            project = started['projectId']
            data = ledger.server_manifest(project, 1)
            server = ledger.server(project, 1)
            if data is None:
                raise Failed(f'the server was built for {(server or {}).get("target")}, not the hosted static '
                             f'{server_manifest.TARGET} target, so it has no manifest')
            manifest = server_manifest.decode(data)
            elf = root / 'artifacts' / f'{manifest["elf"]["sha256"]}.server'
            try:
                server_manifest.check(manifest, elf, project_id=project, version_code=1)
            except server_manifest.Refused as exc:
                raise Failed(f'the built server is not one the host accepts: {exc}') from None
            return f'a static {server_manifest.TARGET} server, {manifest["elf"]["size"]} bytes, manifest accepted'
        finally:
            ledger.close()


def qualify(config_path: Path, out=sys.stdout, *, checks=None) -> bool:
    """Run the checks in order, printing one JSON line each; True when all passed. ``checks`` replaces the
    individual checks (tests only)."""
    c = {'config': check_config, 'platform': check_platform, 'facilities': check_facilities,
         'confinement': check_confinement, 'build': check_build, **(checks or {})}
    state, ok = {}, True

    def report(name, passed, detail):
        print(json.dumps({'check': name, 'ok': passed, 'detail': detail}), file=out)

    steps = (('config', lambda: c['config'](config_path)), ('platform', lambda: c['platform']()),
             ('facilities', lambda: c['facilities'](state['config'])),
             ('confinement', lambda: c['confinement'](state['facilities'])),
             ('build', lambda: c['build'](state['config'], state['facilities'])))
    needs = {'facilities': ('config',), 'confinement': ('facilities',), 'build': ('config', 'facilities',
                                                                                  'confinement')}
    for name, step in steps:
        if any(n not in state for n in needs.get(name, ())):
            report(name, None, 'skipped: an earlier check failed')
            ok = False
            continue
        try:
            value = step()
        except Failed as exc:
            report(name, False, str(exc))
            ok = False
            continue
        state[name] = value
        detail = value if isinstance(value, str) else ('passed' if name != 'config' else 'clientServer is set')
        report(name, True, detail)
    print(json.dumps({'qualified': ok}), file=out)
    return ok


def main(argv=None, out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(prog='python -m rose.app_builder.qualify',
                                     description='Check that this host can build client-server apps (§7.7a).')
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args(argv)
    return 0 if qualify(args.config, out) else 1


if __name__ == '__main__':
    raise SystemExit(main())
