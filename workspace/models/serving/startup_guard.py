#!/usr/bin/env python3
"""Pre-download startup guard for the Muse Glimmer serving container.

Runs before `vllm serve`, inside the container. It reads only what the node reports: driver
version, the GPUs CUDA sees with their total and free memory, free disk at the weights cache, and
/dev/shm. It prints one JSON evidence line, then either execs the server or exits 3, so a node
that cannot fit the model fails before it downloads 59.6 GB. It never reads environment values
or credentials.

Usage: startup_guard.py '<limits json>' -- vllm serve <args...>
"""
import json
import os
import re
import shutil
import sys
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


def main(argv):
    if '--' not in argv or argv.index('--') != 1:
        raise SystemExit(__doc__)
    limits, command = json.loads(argv[0]), argv[2:]
    facts = collect(os.path.expanduser(limits['cache_dir']))
    failures = evaluate(facts, limits)
    print(json.dumps({'guard': 'muse-glimmer-startup', 'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                      'facts': facts, 'limits': limits, 'failures': failures, 'proceed': not failures}), flush=True)
    if failures:
        sys.exit(EXIT_REFUSED)
    os.execvp(command[0], command)


def limits_for(config, option):
    """Thresholds from the pinned config: weights and KV at the configured context, 3 GiB reserve, one sequence."""
    import capacity
    text = config['model']['text_config']
    _, weights = capacity.weight_bytes_per_gpu(config['model'], option)
    tp = option['machine_spec']['acceleratorCount']
    kv = capacity.kv_bytes_per_sequence(text, config['demo_assumptions']['max_model_len'], tp, sliding_aware=False)*option['max_num_seqs']
    util = option['gpu_memory_utilization']
    fetch = config['model']['weights_fetch']['total_bytes_including_small_files']
    return {'driver_major_min': option['runtime']['driver_major_min'], 'gpu_count': tp,
            'gpu_total_min_bytes': int((weights + kv + option['runtime_reserve_bytes_per_gpu'])/util),
            'gpu_memory_utilization': util, 'cache_dir': option['runtime']['weights_cache_dir'],
            'cache_free_min_bytes': int(fetch*1.05) + 2**30,
            'shm_min_bytes': int(int(option['container_spec']['sharedMemorySizeMb'])*2**20*0.9)}


def container_command(config, option):
    """containerSpec.command: this guard inline, then the image's own `vllm serve`; containerSpec.args follow."""
    with open(__file__) as f:
        source = f.read()
    return ['python3', '-c', source, json.dumps(limits_for(config, option), sort_keys=True), '--', 'vllm', 'serve']


if __name__ == '__main__':
    main(sys.argv[1:])
