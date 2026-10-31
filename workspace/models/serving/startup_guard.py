#!/usr/bin/env python3
"""Pre-download startup guard for the Muse Glimmer serving container.

Runs before `vllm serve`, inside the container. It reads only what the node reports: driver
version, the GPUs CUDA sees with their total and free memory, free disk at the weights cache, and
/dev/shm. It prints one JSON evidence line, then either execs the server or exits 3, so a node
that cannot fit the model fails before it downloads 59.6 GB. It never reads environment values
or credentials.

Usage: startup_guard.py '<limits json>' -- vllm serve <args...>

With "status_path" in the limits (the persistent service only), the guard also writes a small nonsecret status file
there and supervises the server instead of exec-ing it: output passes through unchanged, signals are forwarded, and
the guard exits with the server's own status (128+N for signal N). The file records the guard's result as failure
codes, when the server's port first accepted a connection, the exit status, the elapsed time and the class name of
the last exception the server printed. It never records message text, arguments or environment.
"""
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

EXIT_REFUSED = 3


def evaluate(facts, limits):
    """Pure check of reported facts against limits; returns failure strings (empty means proceed)."""
    failures = []
    if facts.get('cuda_error'):
        failures.append(f"CUDA unavailable: {facts['cuda_error']}")
    driver = facts.get('driver_version')
    if driver is None:
        failures.append('driver version not reported')
    elif int(driver.split('.')[0]) < limits['driver_major_min']:
        failures.append(f"driver {driver} below {limits['driver_major_min']}")
    gpus = facts.get('gpus') or []
    if len(gpus) != limits['gpu_count']:
        failures.append(f"{len(gpus)} GPUs visible, need {limits['gpu_count']}")
    for i, gpu in enumerate(gpus):
        if gpu['total'] < limits['gpu_total_min_bytes']:
            failures.append(f"GPU{i} reports {gpu['total']} total bytes, need {limits['gpu_total_min_bytes']}")
        if gpu['free'] < gpu['total']*limits['gpu_memory_utilization']:
            failures.append(f"GPU{i} has {gpu['free']} free bytes, below utilization {limits['gpu_memory_utilization']} of total")
    if facts.get('cache_free') is None or facts['cache_free'] < limits['cache_free_min_bytes']:
        failures.append(f"cache free {facts.get('cache_free')} bytes, need {limits['cache_free_min_bytes']}")
    if facts.get('shm_total') is None or facts['shm_total'] < limits['shm_min_bytes']:
        failures.append(f"/dev/shm {facts.get('shm_total')} bytes, need {limits['shm_min_bytes']}")
    return failures


def _driver_version(path='/proc/driver/nvidia/version'):
    try:
        with open(path) as f:
            match = re.search(r'Kernel Module\s+(?:for \S+\s+)?(\d+\.\d+(?:\.\d+)?)', f.read())
            return match.group(1) if match else None
    except OSError:
        return None


def _existing(path):
    while not os.path.exists(path):
        path = os.path.dirname(path)
    return path


def collect(cache_dir):
    facts = {'driver_version': _driver_version(), 'gpus': [], 'cache_dir': cache_dir}
    try:
        import torch
        for i in range(torch.cuda.device_count()):
            free, total = torch.cuda.mem_get_info(i)
            facts['gpus'].append({'name': torch.cuda.get_device_name(i), 'total': total, 'free': free})
    except Exception as e:  # an old driver surfaces here as a CUDA initialisation error
        facts['cuda_error'] = f'{type(e).__name__}: {e}'[:300]
    facts['cache_free'] = shutil.disk_usage(_existing(cache_dir)).free
    try:
        facts['shm_total'] = shutil.disk_usage('/dev/shm').total
    except OSError:
        facts['shm_total'] = None
    return facts


CODES = (('CUDA unavailable', 'cuda_unavailable'), ('driver version not reported', 'driver_unknown'),
         ('driver ', 'driver_too_old'), ('GPUs visible', 'gpu_count'), ('total bytes', 'gpu_total'),
         ('free bytes', 'gpu_free'), ('cache free', 'cache_free'), ('/dev/shm', 'shm'))
EXCEPTION = re.compile(r'(?:^|[\s(\[])((?:[A-Za-z_][A-Za-z0-9_]*\.){0,5}[A-Z][A-Za-z0-9_]*(?:Error|Exception|Interrupt))(?::|$)')


def codes(failures):
    return [next((c for marker, c in CODES if marker in f), 'other') for f in failures]


def write_status(path, **fields):
    """Atomic and best effort: the status file is telemetry and never changes what the guard does."""
    try:
        tmp = f'{path}.tmp'
        with open(tmp, 'w') as f:
            json.dump({**fields, 'at': int(time.time())}, f, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        pass


def supervise(command, status_path):
    """Run the server as a child, pass its output through, and return the exit status the container should have."""
    started, seen = time.monotonic(), {'listenedAfterS': None, 'lastException': None}
    args = command[1:]
    host = args[args.index('--host') + 1] if '--host' in args else '127.0.0.1'
    port = int(args[args.index('--port') + 1]) if '--port' in args else 8000
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda s, _: child.poll() is None and child.send_signal(s))
    status = lambda stage, **extra: write_status(status_path, stage=stage, proceed=True, failures=[],  # noqa: E731
                                                 elapsedS=int(time.monotonic() - started), **seen, **extra)
    status('server_started')

    def relay(src, dst):
        for raw in iter(src.readline, b''):
            dst.write(raw)
            dst.flush()
            m = EXCEPTION.search(raw[:2000].decode('utf-8', 'replace').rstrip())
            if m:
                seen['lastException'] = m.group(1)[:120]

    def listen():
        while child.poll() is None and seen['listenedAfterS'] is None:
            try:
                socket.create_connection((host, port), timeout=2).close()
                seen['listenedAfterS'] = int(time.monotonic() - started)
                status('listening')
            except OSError:
                time.sleep(5)

    threads = [threading.Thread(target=relay, args=(child.stdout, sys.stdout.buffer), daemon=True),
               threading.Thread(target=relay, args=(child.stderr, sys.stderr.buffer), daemon=True),
               threading.Thread(target=listen, daemon=True)]
    for t in threads:
        t.start()
    rc = child.wait()
    for t in threads:  # the listener too, so `exited` is the last status written
        t.join(10)
    code = 128 - rc if rc < 0 else rc
    status('exited', exitCode=rc if rc >= 0 else None, signal=-rc if rc < 0 else None)
    return code


def main(argv):
    if '--' not in argv or argv.index('--') != 1:
        raise SystemExit(__doc__)
    limits, command = json.loads(argv[0]), argv[2:]
    facts = collect(os.path.expanduser(limits['cache_dir']))
    failures = evaluate(facts, limits)
    print(json.dumps({'guard': 'muse-glimmer-startup', 'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                      'facts': facts, 'limits': limits, 'failures': failures, 'proceed': not failures}), flush=True)
    status_path = limits.get('status_path')
    if status_path:
        write_status(status_path, stage='guard_refused' if failures else 'guard_passed', proceed=not failures,
                     failures=codes(failures))
    if failures:
        sys.exit(EXIT_REFUSED)
    if status_path:
        sys.exit(supervise(command, status_path))
    os.execvp(command[0], command)


if __name__ == '__main__':
    main(sys.argv[1:])
