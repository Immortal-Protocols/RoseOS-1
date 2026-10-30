#!/usr/bin/env python3
"""EXP005-F1/EXP006: make the official SDK emulator package usable before the experiment, without accepting anything
or gaining a privilege.

  setup_emulator.py --sdk SDK --record FILE [--library-dir DIR --github-env FILE]

If SDK/emulator/emulator is already an executable file it is used as is. Otherwise the installed
SDK/cmdline-tools/latest/bin/sdkmanager runs `--install emulator` once, with stdin closed so no licence prompt can be
answered: only licences already present in SDK/licenses apply.

`emulator -version` must then succeed. On ubuntu-24.04 it exited 127 because qemu-system-x86_64 could not load
libpulse.so.0 (owner-supplied summary of run 41109233214). With --library-dir, a missing shared library named in that
error is provided without root: its official Ubuntu package (a fixed soname -> package table) is fetched with
`apt-get download` from the runner's configured archive, unpacked with `dpkg-deb -x` into DIR, and the version check
is retried with DIR's library path on LD_LIBRARY_PATH, at most 8 libraries. On success that LD_LIBRARY_PATH is
appended to --github-env so the experiment step sees it. Nothing is installed system-wide.

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
import re
import subprocess
import sys

# Official Ubuntu 24.04 packages for the libraries the emulator's qemu and libpulse0 load.
SONAME_PACKAGES = {
    'libpulse.so.0': 'libpulse0', 'libpulse-simple.so.0': 'libpulse0',
    'libsndfile.so.1': 'libsndfile1', 'libasyncns.so.0': 'libasyncns0',
    'libFLAC.so.12': 'libflac12t64', 'libvorbisenc.so.2': 'libvorbisenc2', 'libvorbis.so.0': 'libvorbis0a',
    'libogg.so.0': 'libogg0', 'libopus.so.0': 'libopus0', 'libmpg123.so.0': 'libmpg123-0t64',
    'libmp3lame.so.0': 'libmp3lame0',
}
MISSING = re.compile(r'(lib[\w.+-]*\.so(?:\.\d+)*)\b[^\n]*?(?:cannot open shared object file|No such file)'
    r'|error (?:while )?loading (?:shared libraries: )?(lib[\w.+-]*\.so(?:\.\d+)*)')
MAX_LIBRARIES = 8
LIB_SUBDIR = 'usr/lib/x86_64-linux-gnu'

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

def provide(soname, library_dir, run):
    """{'package', 'deb', 'sha256'} after unpacking soname's package into library_dir; raises ValueError why not."""
    package = SONAME_PACKAGES.get(soname)
    if not package:
        raise ValueError(f'{soname} is missing and has no known Ubuntu package here')
    downloads = Path(library_dir)/'debs'
    downloads.mkdir(parents=True, exist_ok=True)
    before = set(downloads.glob('*.deb'))
    result = run(['apt-get', 'download', package], cwd=str(downloads), capture_output=True, text=True, timeout=180,
        stdin=subprocess.DEVNULL)
    debs = sorted(set(downloads.glob(f'{package}_*.deb')) - before)
    if result.returncode or len(debs) != 1:
        raise ValueError(f'apt-get download {package} exited {result.returncode}: '
            + ' '.join(((result.stdout or '') + (result.stderr or '')).split())[-200:])
    unpack = run(['dpkg-deb', '-x', str(debs[0]), str(library_dir)], capture_output=True, text=True, timeout=120)
    if unpack.returncode:
        raise ValueError(f'dpkg-deb -x {debs[0].name} exited {unpack.returncode}')
    if not (Path(library_dir)/LIB_SUBDIR/soname).exists():
        raise ValueError(f'{package} did not provide {LIB_SUBDIR}/{soname}')
    return {'soname': soname, 'package': package, 'deb': debs[0].name,
        'sha256': hashlib.sha256(debs[0].read_bytes()).hexdigest()}

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
    provided, env = [], None
    while problem and library_dir and len(provided) < MAX_LIBRARIES:
        soname = missing_library(problem)
        if not soname or any(p['soname'] == soname for p in provided):
            break
        try:
            provided.append(provide(soname, library_dir, run))
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            record['libraries'] = provided
            return {**record, 'result': 'unavailable', 'reason': f'{problem}; library not provided: {error}'}
        path = str(Path(library_dir)/LIB_SUBDIR)
        current = (base_env if base_env is not None else os.environ).get('LD_LIBRARY_PATH')
        env = {**(base_env if base_env is not None else os.environ),
            'LD_LIBRARY_PATH': path + (os.pathsep + current if current else '')}
        version, problem = emulator_version(emulator, run, env)
    if provided:
        record['libraries'] = provided
        record['libraryPath'] = str(Path(library_dir)/LIB_SUBDIR)
    if problem:
        return {**record, 'result': 'unavailable', 'reason': problem}
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
    if usable and record.get('libraryPath') and a.github_env:
        current = (base_env if base_env is not None else os.environ).get('LD_LIBRARY_PATH')
        with open(a.github_env, 'a') as env:
            env.write(f'LD_LIBRARY_PATH={record["libraryPath"]}' + (os.pathsep + current if current else '') + '\n')
    print(json.dumps({k: record.get(k) for k in ('result', 'reason', 'version')}, sort_keys=True))
    return 0 if usable else 1

if __name__ == '__main__':
    sys.exit(main())
