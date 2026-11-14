"""Yogi compiler supervision: live logs, retained failure evidence and a local cache.

No prior OUT_DIR is adopted. Cache objects live on the Yogi disk and use the
same pinned executable/configuration and wrapper as the Mustang pipeline.
"""
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

import mustang_compiler_cache as cache


def environment(inherited):
    prefixes = ('GIT_', 'GITHUB_', 'ACTIONS_', 'INPUT_', 'CCACHE_', 'GOOGLE_', 'GCP_', 'CLOUDSDK_', 'AWS_')
    excluded = {'GH_TOKEN', 'GITHUB_TOKEN', 'PIXEL_OTA_TOKEN', 'JAVA_HOME', 'JAVA_TOOL_OPTIONS',
                'JDK_JAVA_OPTIONS', '_JAVA_OPTIONS', 'CLASSPATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'LD_AUDIT'}
    return {k: v for k, v in inherited.items() if k not in excluded and not k.startswith(prefixes)}


def prepare_cache(cfg, root, out_dir):
    setting = cfg['builder'].get('compilerCache')
    if not cache.enabled({'compilerCache': setting}):
        return None
    return cache.prepare_at(Path(cfg['builder']['root'])/'compiler-cache-v1', root=root, out_dir=out_dir)


def stream(argv, cwd, env, log, timeout, reserve=64 << 30, sample=5, free=None):
    """Bound the whole compiler process group; preserve and stream both output channels.

    The reserve is a sampled stop rather than a quota. A normal exit also ends
    leftover descendants. Cancellation unwinds through the same group cleanup.
    """
    import shutil
    free = free or (lambda: shutil.disk_usage(cwd).free)
    started = time.monotonic()
    output = open(log, 'xb')  # refuse a repeated log before starting any compiler
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    except BaseException:
        output.close()
        raise
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    stopped = None
    lowest = None
    next_sample = started

    def end_group():
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        # Do not leave grandchildren alive merely because the shell exited.
        until = time.monotonic() + 5
        while time.monotonic() < until:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return
            time.sleep(.05)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    previous = signal.getsignal(signal.SIGTERM)
    def terminated(signum, frame):
        raise InterruptedError('compiler job terminated')
    signal.signal(signal.SIGTERM, terminated)
    try:
        with output:
            while selector.get_map():
                now = time.monotonic()
                if now >= next_sample:
                    available = free()
                    lowest = available if lowest is None else min(lowest, available)
                    next_sample = now + sample
                    if available < reserve:
                        stopped = 'filesystem-reserve'
                if now - started >= timeout:
                    stopped = 'timeout'
                if stopped:
                    end_group()
                    break
                for key, _ in selector.select(min(sample, .5)):
                    data = os.read(key.fd, 64 << 10)
                    if data:
                        output.write(data)
                        output.flush()
                        # Treat compiler output as text in both local and Actions logs.
                        sys.stdout.write(data.decode('utf-8', errors='replace'))
                        sys.stdout.flush()
                    else:
                        selector.unregister(key.fileobj)
            process.wait(timeout=10)
    finally:
        end_group()
        process.wait(timeout=10)
        process.stdout.close()
        selector.close()
        signal.signal(signal.SIGTERM, previous)
    return {'returncode': process.returncode, 'stopped': stopped,
            'lowestSampledAvailableBytes': lowest, 'seconds': round(time.monotonic()-started)}
