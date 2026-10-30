#!/usr/bin/env python3
"""EXP005-F1/EXP006: make the official SDK emulator package usable before the experiment, without accepting anything
or gaining a privilege.

  setup_emulator.py --sdk SDK --record FILE [--library-dir DIR --github-env FILE]

If SDK/emulator/emulator is already an executable file it is used as is. Otherwise the installed
SDK/cmdline-tools/latest/bin/sdkmanager runs `--install emulator` once, with stdin closed so no licence prompt can be
answered: only licences already present in SDK/licenses apply.

`emulator -version` must then succeed. On ubuntu-24.04 it exited 127 because qemu-system-x86_64 could not load
libpulse.so.0 (run 41109233214), and after that library was supplied flat, because libpulse.so.0 loads the private
libpulsecommon-16.1.so that libpulse0 ships nested in usr/lib/x86_64-linux-gnu/pulseaudio (run 41122307845). With
--library-dir, a missing root library is provided without root (EXP006-F3):
  * its official package (only the roots in ROOT_PACKAGES) and that package's Depends closure from the runner's
    configured archive (apt-cache depends), skipping packages already installed on the host, at most MAX_PACKAGES;
  * each fetched once with `apt-get download` (stdin closed; an already downloaded deb of that package is reused),
    checked with dpkg-deb (package name, version, sha256, no member or link that escapes) and unpacked under
    DIR/root;
  * every directory under DIR/root that holds shared objects, the nested private ones included, goes on
    LD_LIBRARY_PATH; a library name found in two of them, or one resolving outside DIR/root, refuses;
  * the real loader (ldd) must then resolve every unpacked shared object with that environment, and `emulator
    -version` must succeed with the SAME environment, which alone is appended to --github-env.
No host directory and nothing outside DIR/root is ever added to the search path.

FILE (JSON) records `pre-existing`, `installed` or `unavailable` with the reason, the sdkmanager exit status and
output tail, each provided package (name, deb file, sha256) and `emulator -version`. No sudo, apt-get install, KVM
ACL, SELinux or verity change. An unavailable result is not hidden: the experiment's environment stage still reports
it. Exit status 0 when the emulator is usable, 1 when it is unavailable.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys

# The root libraries the emulator's qemu is known to load, mapped to their official Ubuntu 24.04 package. Every
# further library comes from that package's own Depends closure, never from a guess.
ROOT_PACKAGES = {'libpulse.so.0': 'libpulse0', 'libpulse-simple.so.0': 'libpulse0'}
MISSING = re.compile(r'(lib[\w.+-]*\.so(?:\.\d+)*)\b[^\n]*?(?:cannot open shared object file|No such file)'
    r'|error (?:while )?loading (?:shared libraries: )?(lib[\w.+-]*\.so(?:\.\d+)*)')
MAX_PACKAGES = 24
MAX_ROOTS = 4
SHARED = re.compile(r'lib[\w.+-]*\.so(?:\.[\w.]+)?$')
NOT_FOUND = re.compile(r'^\s*(\S+) => not found', re.M)

def emulator_version(emulator, run, env=None):
    """(first version line, None) or (None, problem); problem keeps a bounded tail of the tool's own message."""
    try:
        result = run([str(emulator), '-version'], capture_output=True, text=True, timeout=60,
            **({'env': env} if env else {}))
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f'emulator -version failed: {error}'
    lines = (result.stdout or '').strip().splitlines()
    if result.returncode or not lines:
        text = ' '.join(((result.stdout or '') + ' ' + (result.stderr or '')).split())
        return None, f'emulator -version exited {result.returncode}: {text[-300:]}'
    return lines[0], None

def missing_library(problem):
    found = MISSING.search(problem or '')
    return (found[1] or found[2]) if found else None

def tool(run, argv, **kw):
    try:
        return run(argv, capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL, **kw)
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(argv, 127, '', str(error))

def installed(package, run):
    return tool(run, ['dpkg-query', '-W', '-f=${db:Status-Status}', package]).stdout.strip() == 'installed'

def depends(package, run):
    """The package's Depends/Pre-Depends from the configured archive, as alternative groups of real package names."""
    result = tool(run, ['apt-cache', 'depends', '--no-recommends', '--no-suggests', '--no-conflicts', '--no-breaks',
        '--no-replaces', '--no-enhances', package])
    if result.returncode:
        raise ValueError(f'apt-cache depends {package} exited {result.returncode}')
    groups, alternative = [], False
    for line in result.stdout.splitlines():
        found = re.match(r'\s*(\|)?(?:Pre)?Depends: (<)?([a-z0-9][a-z0-9+.-]*)', line)
        if not found:
            continue
        name = None if found[2] else found[3]  # a virtual <name> is never fetched by name
        if alternative and groups:
            groups[-1].append(name)
        else:
            groups.append([name])
        alternative = bool(found[1])
    return groups

def closure(root, run):
    """Packages to fetch for root: root and its Depends closure, skipping any already installed on the host."""
    needed, queue, seen = [], [root], set()
    while queue:
        package = queue.pop(0)
        if package in seen:
            continue
        seen.add(package)
        if package != root and installed(package, run):
            continue
        needed.append(package)
        if len(needed) > MAX_PACKAGES:
            raise ValueError(f'the dependency closure of {root} exceeds {MAX_PACKAGES} packages')
        for group in depends(package, run):
            real = [name for name in group if name]
            if not real:
                raise ValueError(f'{package} depends only on a virtual package; nothing to fetch by name')
            if any(name in seen or installed(name, run) for name in real):
                continue
            queue.append(real[0])
    return needed

def escapes(path):
    """Whether a member path, relative to the unpack root, would leave it."""
    normal = posixpath.normpath(path)
    return path.startswith('/') or normal == '..' or normal.startswith('../')

def unsafe_members(listing):
    """Members of a `dpkg-deb -c` listing whose path or link target would leave the unpack root."""
    bad = []
    for line in listing.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        name, _, target = parts[5].partition(' -> ')
        if escapes(name):
            bad.append(name)
        elif target and (target.startswith('/') or escapes(posixpath.join(posixpath.dirname(name), target))):
            bad.append(f'{name} -> {target}')
    return bad

def fetch(package, downloads, run):
    """{'package', 'version', 'deb', 'sha256', 'reused'} for package's one official deb in downloads."""
    existing = sorted(downloads.glob(f'{package}_*.deb'))
    reused = len(existing) == 1
    if not reused:
        if existing:
            raise ValueError(f'{len(existing)} {package} debs in {downloads}; refusing to choose one')
        result = tool(run, ['apt-get', 'download', package], cwd=str(downloads))
        existing = sorted(downloads.glob(f'{package}_*.deb'))
        if result.returncode or len(existing) != 1:
            raise ValueError(f'apt-get download {package} exited {result.returncode}: '
                + ' '.join(((result.stdout or '') + (result.stderr or '')).split())[-200:])
    deb = existing[0]
    fields = tool(run, ['dpkg-deb', '-f', str(deb), 'Package', 'Version'])
    found = dict(line.split(': ', 1) for line in fields.stdout.splitlines() if ': ' in line)
    if fields.returncode or found.get('Package') != package or not found.get('Version'):
        raise ValueError(f'{deb.name} is not a readable {package} package')
    listing = tool(run, ['dpkg-deb', '-c', str(deb)])
    bad = unsafe_members(listing.stdout) if not listing.returncode else ['unlistable']
    if bad:
        raise ValueError(f'{deb.name} has members leaving the unpack root: {bad[:3]}')
    return {'package': package, 'version': found['Version'], 'deb': deb.name,
        'sha256': hashlib.sha256(deb.read_bytes()).hexdigest(), 'reused': reused}

def search_paths(root):
    """Directories under root holding shared objects, shortest first; refuses a library that resolves outside root
    or a library name present in two directories (the loader would pick one silently)."""
    root, owners = Path(root).resolve(), {}
    for path in sorted(root.rglob('*')):
        if not SHARED.match(path.name) or not (path.is_file() or path.is_symlink()):
            continue
        real = path.resolve()
        if root not in real.parents:
            raise ValueError(f'{path.relative_to(root)} resolves outside the unpacked packages')
        if not real.is_file():
            continue
        owner = owners.setdefault(path.name, path.parent)
        if owner != path.parent:
            raise ValueError(f'{path.name} is ambiguous: {owner.relative_to(root)} and {path.parent.relative_to(root)}')
    return sorted({str(d) for d in owners.values()}, key=lambda d: (d.count('/'), d))

def loader_check(root, env, run):
    """Sonames the real loader still cannot resolve for the unpacked shared objects with env (empty when complete)."""
    missing = set()
    for path in sorted(Path(root).rglob('*')):
        if SHARED.match(path.name) and path.is_file() and not path.is_symlink():
            result = tool(run, ['ldd', str(path)], env=env)
            missing |= set(NOT_FOUND.findall(result.stdout or ''))
    return sorted(missing)

def provide(soname, library_dir, base_env, state, run):
    """Fetch soname's root package closure into library_dir/root; returns the environment for the next check."""
    package = ROOT_PACKAGES.get(soname)
    if not package:
        raise ValueError(f'{soname} is missing and is not a known root library; nothing is guessed')
    if package in state['roots'] or len(state['roots']) >= MAX_ROOTS:
        raise ValueError(f'{soname} is still missing after {package} was provided')
    state['roots'].append(package)
    downloads, root = Path(library_dir)/'debs', Path(library_dir)/'root'
    downloads.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    for name in closure(package, run):
        if name in state['packages']:
            continue
        record = fetch(name, downloads, run)
        unpack = tool(run, ['dpkg-deb', '-x', str(downloads/record['deb']), str(root)])
        if unpack.returncode:
            raise ValueError(f'dpkg-deb -x {record["deb"]} exited {unpack.returncode}')
        state['packages'][name] = record
    paths = search_paths(root)
    if not paths:
        raise ValueError(f'{package} provided no shared library')
    current = base_env.get('LD_LIBRARY_PATH')
    env = {**base_env, 'LD_LIBRARY_PATH': os.pathsep.join(paths) + (os.pathsep + current if current else '')}
    unresolved = loader_check(root, env, run)
    if unresolved:
        raise ValueError(f'the loader cannot resolve {unresolved} for the provided libraries')
    state['searchPaths'] = [str(Path(p).relative_to(root.resolve())) for p in paths]
    return env

def setup(sdk, run=subprocess.run, library_dir=None, base_env=None):
    sdk = Path(sdk)
    emulator = sdk/'emulator/emulator'
    sdkmanager = sdk/'cmdline-tools/latest/bin/sdkmanager'
    record = {'emulator': str(emulator), 'licences': sorted(p.name for p in (sdk/'licenses').glob('*'))}
    if emulator.is_file() and os.access(emulator, os.X_OK):
        record['result'] = 'pre-existing'
    elif not (sdkmanager.is_file() and os.access(sdkmanager, os.X_OK)):
        return {**record, 'result': 'unavailable', 'reason': f'{sdkmanager} is not installed'}
    else:
        try:
            result = run([str(sdkmanager), '--install', 'emulator'], stdin=subprocess.DEVNULL, capture_output=True,
                text=True, timeout=600)
            code, output = result.returncode, (result.stdout or '') + (result.stderr or '')
        except subprocess.TimeoutExpired:
            code, output = 124, 'sdkmanager timed out after 600s'
        record['sdkmanager'] = {'exit': code, 'outputTail': output[-1500:]}
        if code != 0:
            return {**record, 'result': 'unavailable',
                'reason': f'sdkmanager --install emulator exited {code} with no licence answered'}
        if not (emulator.is_file() and os.access(emulator, os.X_OK)):
            return {**record, 'result': 'unavailable', 'reason': 'sdkmanager succeeded but the emulator is not executable'}
        record['result'] = 'installed'
    version, problem = emulator_version(emulator, run)
    base = dict(base_env if base_env is not None else os.environ)
    state, env = {'roots': [], 'packages': {}}, None
    while problem and library_dir:
        soname = missing_library(problem)
        if not soname:
            break
        try:
            env = provide(soname, library_dir, base, state, run)
        except (ValueError, OSError) as error:
            record['libraries'] = list(state['packages'].values())
            return {**record, 'result': 'unavailable', 'reason': f'{problem}; library not provided: {error}'}
        version, problem = emulator_version(emulator, run, env)
    if state['packages']:
        record['libraries'] = list(state['packages'].values())
        record['searchPaths'] = state.get('searchPaths', [])
    if problem:
        return {**record, 'result': 'unavailable', 'reason': problem}
    if env:
        record['ldLibraryPath'] = env['LD_LIBRARY_PATH']
    return {**record, 'version': version}

def main(argv=None, run=subprocess.run, base_env=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--sdk', required=True)
    p.add_argument('--record', required=True)
    p.add_argument('--library-dir', help='where official Ubuntu packages for missing emulator libraries are unpacked')
    p.add_argument('--github-env', help='file to append LD_LIBRARY_PATH to when libraries were provided')
    a = p.parse_args(argv)
    record = setup(a.sdk, run, a.library_dir, base_env)
    Path(a.record).parent.mkdir(parents=True, exist_ok=True)
    Path(a.record).write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    usable = record['result'] in ('pre-existing', 'installed')
    if usable and record.get('ldLibraryPath') and a.github_env:
        # Exactly the environment emulator -version succeeded with; nothing is exported for an unusable emulator.
        with open(a.github_env, 'a') as env:
            env.write(f'LD_LIBRARY_PATH={record["ldLibraryPath"]}\n')
    print(json.dumps({k: record.get(k) for k in ('result', 'reason', 'version')}, sort_keys=True))
    return 0 if usable else 1

if __name__ == '__main__':
    sys.exit(main())
