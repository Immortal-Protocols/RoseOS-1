"""Persistent, bounded C/C++ compiler cache on the dedicated Mustang data disk.

No previous OUT_DIR is adopted. Fresh per-run source/output and normal Make,
allocation, collection, signing and publication checks remain in effect.
"""
import hashlib
import io
import json
import os
import re
from pathlib import Path
import shlex
import stat
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = 'out-mustang-cache'
VERSION = '4.12.2'
MAX_BYTES = 20 << 30
ARCHIVE = 'ccache-4.12.2-linux-x86_64.tar.xz'
URL = 'https://github.com/ccache/ccache/releases/download/v4.12.2/' + ARCHIVE
SIZE = 1289864
SHA = '630c34ec94d451b200f5b14a6a25580d6a45bc80c394b7e0b93e33556eee5d32'
BINARY_SHA = '4fdc966c46448960f3f9d85cf4430d818c1f4b4618ba0b745d000a396d6bc041'
SETTING = {'enabled': True, 'kind': 'ccache', 'maxBytes': MAX_BYTES}


def enabled(runtime):
    value = runtime.get('compilerCache')
    if value is None:
        return False
    if value != SETTING:
        raise ValueError('Mustang compiler-cache configuration differs')
    return True


def output_name(candidate, build):
    """Select only a fixed output name bound to the completed compile receipt."""
    cached = candidate.get('compilerCache') or {}
    if cached.get('enabled') is True:
        if cached.get('kind') != 'ccache' or cached.get('outDir') != OUT:
            raise ValueError('compiled cache output-directory binding differs')
        name = OUT
    else:
        if not re.fullmatch(r'[1-9][0-9]{0,19}', str(build)):
            raise ValueError('compiled build identity differs')
        name = f'out-mustang-build-{build}'
    recorded = candidate.get('stages', {}).get('compile', {}).get('result', {}).get('outDir')
    if recorded is not None and recorded != name:
        raise ValueError('compiled output-directory binding differs')
    return name


def plain(path, directory=False):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('compiler cache path traverses a link')
    st = path.stat()
    if not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)):
        raise ValueError('compiler cache path type differs')
    return path


def location(lane):
    import mustang_paths
    # The runner owns rose-actions, where it already creates attempt receipts.
    # Its parent is administrator-owned; never mkdir there or change its access.
    return mustang_paths.layout(lane)['storageRoot'] / 'compiler-cache-v1'


def binary(cache):
    return Path(cache) / ('ccache-' + VERSION)


def check_binary(cache):
    path = plain(binary(cache))
    if path.stat().st_size != 3788240 or hashlib.sha256(path.read_bytes()).hexdigest() != BINARY_SHA:
        raise ValueError('compiler-cache executable differs from its pin')
    return path


def cache_environment(cache, source=None, root=ROOT, inherited=None):
    cache = Path(cache)
    env = {k: v for k, v in (os.environ if inherited is None else inherited).items()
           if not k.startswith('CCACHE_')}
    env.update(CCACHE_CONFIGPATH=str(cache / 'ccache.conf'), CCACHE_DIR=str(cache / 'objects'),
               CCACHE_COMPILERCHECK='content', CCACHE_NAMESPACE=hashlib.sha256(
                   (Path(root) / 'os/pinned-manifest.xml').read_bytes()).hexdigest())
    if source is not None:
        env.update(CCACHE_BASEDIR=str(source))
    return env


def statistics(cache, root=ROOT):
    result = subprocess.run([str(binary(cache)), '--print-stats'],
                            env=cache_environment(cache, root=root), capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError('compiler-cache statistics failed')
    return {key: int(value) for key, value in (line.split() for line in result.stdout.splitlines())}


def prepare(lane, runtime, root=ROOT, fetch=None):
    if not enabled(runtime):
        return None
    return prepare_at(location(lane), root=root, fetch=fetch)


def prepare_at(cache, root=ROOT, fetch=None, out_dir=OUT):
    """The same pinned local cache on a caller's dedicated filesystem."""
    cache = Path(cache)
    plain(cache.parent, directory=True)
    cache.mkdir(mode=0o700, exist_ok=True)
    plain(cache, directory=True)
    if cache.stat().st_uid != os.geteuid() or cache.stat().st_dev != cache.parent.stat().st_dev:
        raise ValueError('compiler cache owner/filesystem differs')
    for name in ('objects', 'tmp'):
        child = cache / name
        child.mkdir(mode=0o700, exist_ok=True)
        plain(child, directory=True)
    config = (f'cache_dir = {cache / "objects"}\nmax_size = 20GiB\ncompiler_check = content\n'
              'compression = true\nhard_link = false\nfile_clone = false\nsloppiness =\nremote_storage =\n')
    path = cache / 'ccache.conf'
    if os.path.lexists(path):
        if plain(path).read_text() != config:
            raise ValueError('compiler-cache settings differ; not overwritten')
    else:
        with path.open('x') as output:
            output.write(config)
    executable = binary(cache)
    if not os.path.lexists(executable):
        if fetch is None:
            with urllib.request.urlopen(URL, timeout=60) as response:
                raw = response.read(SIZE + 1)
        else:
            raw = fetch()
        if len(raw) != SIZE or hashlib.sha256(raw).hexdigest() != SHA:
            raise ValueError('compiler-cache download differs from its pin')
        # Read only the named regular executable; never extract archive paths/links.
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:xz') as archive:
            member = archive.getmember('ccache-4.12.2-linux-x86_64/ccache')
            if not member.isfile() or member.size != 3788240:
                raise ValueError('compiler-cache executable member differs')
            data = archive.extractfile(member).read()
        if hashlib.sha256(data).hexdigest() != BINARY_SHA:
            raise ValueError('compiler-cache executable digest differs')
        fd, temporary = tempfile.mkstemp(prefix='.ccache-', dir=cache)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(data)
                os.fchmod(output.fileno(), 0o755)
            os.rename(temporary, executable)
        finally:
            Path(temporary).unlink(missing_ok=True)
    check_binary(cache)
    before = statistics(cache, root)
    return {'enabled': True, 'kind': 'ccache', 'version': VERSION, 'maxBytes': MAX_BYTES,
            'path': str(cache), 'outDir': out_dir, 'before': before,
            'namespace': cache_environment(cache, root=root)['CCACHE_NAMESPACE'],
            'priorObjectImport': False, 'scope': 'C/C++ compiler results; no Java or whole-OUT_DIR reuse'}


def finish(state, root=ROOT):
    after = statistics(state['path'], root)
    state.update(after=after, delta={key: after.get(key, 0) - state['before'].get(key, 0)
                                  for key in set(after) | set(state['before'])})
    print(json.dumps({'stage': 'compiler-cache', **state}), flush=True)
    return state


def bind_engine(engine, state, root=ROOT):
    """Private Mustang engine only. Stable relative paths allow cross-run cache hits."""
    if state is None:
        return
    original = engine.commands
    engine.compile_out_dir = lambda recipe, op: OUT

    def commands(recipe, op, command_root=ROOT):
        result = original(recipe, op, command_root)
        if 'compile' in result:
            result['compile']['env'].update(
                CC_WRAPPER=shlex.join(wrapper_argv(state, root)),
                USE_CCACHE='1')
        return result
    engine.commands = commands


def wrapper_argv(state, root=ROOT):
    return ['/bin/bash', str(Path(root) / 'scripts/mustang-ccache-wrapper.sh'),
            state['path'], state['namespace']]
