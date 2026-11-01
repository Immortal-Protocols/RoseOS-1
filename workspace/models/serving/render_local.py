#!/usr/bin/env python3
"""Render the loopback-only launch script for the retained H100 guest (profile h100x1-bf16-vllm-local).

Offline: it reads the pinned config and writes a bash script. Nothing runs here. The script, run on
the guest by the Mac/Chief executor, refuses unless the scratch mount and the exact image ID are
already present. It then starts the pinned image with the startup guard inline: the guard checks
driver, GPU count, device-reported memory, cache disk and /dev/shm, and only then execs vllm. vLLM
binds 127.0.0.1:8000 on the host network, so nothing listens beyond guest loopback. The script never
installs drivers, pulls images, changes the network or creates credentials.

  python3 workspace/models/serving/render_local.py --out launch-muse-glimmer.sh [--offline]

--offline adds HF_HUB_OFFLINE=1, for weights already staged into the cache at the pinned revision.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import capacity  # noqa: E402
import render_upload  # noqa: E402

PROFILE = 'h100x1-bf16-vllm-local'
EXIT_PRECONDITION = 4


def check_local(config, option):
    render_upload.check_context(config, option)
    args = option['container_spec']['args']
    if args[args.index('--host')+1] != '127.0.0.1':
        raise render_upload.Rejected('local profile must bind 127.0.0.1 only')
    launch = option['local_launch']
    if launch['network'] != 'host' or launch['bind'] != f"127.0.0.1:{args[args.index('--port')+1]}":
        raise render_upload.Rejected('local launch must use host networking with a loopback bind')
    if not launch['host_cache_dir'].startswith(launch['scratch_mount'].rstrip('/') + '/'):
        raise render_upload.Rejected('the model cache must live on the scratch mount')


def render(config, offline=False, profile=PROFILE, status=None):
    """status: optional (host dir, container dir) for the guard's status file (the persistent service)."""
    option = render_upload.profile(config, profile)
    check_local(config, option)
    launch, runtime = option['local_launch'], option['runtime']
    limits = render_upload.guard_limits(config, option)
    mounts = []
    if status:
        limits['status_path'] = f'{status[1]}/status.json'
        mounts = ['-v', f'{status[0]}:{status[1]}']
    guard = (HERE/'startup_guard.py').read_text()
    if 'GUARD_SOURCE_END' in guard:
        raise render_upload.Rejected('guard source collides with the heredoc marker')
    env = ['-e', 'HF_HUB_OFFLINE=1'] if offline else []
    run = ['docker', 'run', '--rm', '--name', launch['container_name'], '--gpus', 'all', '--network', 'host',
           '--shm-size', f"{launch['shm_size_mb']}m", '-v', f"{launch['host_cache_dir']}:{launch['container_cache_dir']}",
           *mounts, *env, '--entrypoint', 'python3', runtime['image_id'], '-c']
    tail = [json.dumps(limits, sort_keys=True), '--', 'vllm', 'serve', *option['container_spec']['args']]
    q = shlex.quote
    return f"""#!/usr/bin/env bash
# Muse Glimmer {profile}: {config['model']['repo']}@{config['model']['revision']} as {config['wire_contract']['model_field']}.
# Rendered by workspace/models/serving/render_local.py. Loopback only: http://{launch['bind']}/v1
set -euo pipefail
IMAGE_ID={q(runtime['image_id'])}
SCRATCH={q(launch['scratch_mount'])}
CACHE={q(launch['host_cache_dir'])}
if ! mountpoint -q "$SCRATCH"; then echo "refused: $SCRATCH is not a mounted scratch filesystem" >&2; exit {EXIT_PRECONDITION}; fi
actual="$(docker image inspect --format '{{{{.Id}}}}' "$IMAGE_ID" 2>/dev/null || true)"
if [ "$actual" != "$IMAGE_ID" ]; then echo "refused: image $IMAGE_ID is not present locally (stage it first; this script never pulls)" >&2; exit {EXIT_PRECONDITION}; fi
mkdir -p "$CACHE"
GUARD="$(cat <<'GUARD_SOURCE_END'
{guard}GUARD_SOURCE_END
)"
exec {' '.join(q(x) for x in run)} "$GUARD" {' '.join(q(x) for x in tail)}
""", limits


def qwen_args(qwen, settings):
    """The vllm serve flags for the selectable Qwen profile: the runtime-proven set, plus the proposed overrides and
    additions only when ``settings`` is 'proposed'. Returns (args, the flags that are proposed, not runtime-proven)."""
    if settings not in ('proven', 'proposed'):
        raise render_upload.Rejected('qwen settings must be proven or proposed')
    args = list(qwen['settings']['proven']['args'])
    proposed = []
    if settings == 'proposed':
        p = qwen['settings']['proposed']
        for flag, value in p['overrides'].items():
            args[args.index(flag) + 1] = value
            proposed.append(flag)
        args += p['add']
        proposed += [a for a in p['add'] if a.startswith('--')]
    return args, proposed


def render_qwen(qwen, runtime, settings='proven', status=None):
    """The persistent service's launch script for the staged Qwen model (MODELS-QWEN-SERVE-001): the same image, guard
    and loopback-only host networking as the Muse launch, the staged directory mounted read-only, offline. Nothing is
    downloaded; no cache is written. Returns (script, guard limits)."""
    launch, model = qwen['launch'], qwen['model']
    if launch['network'] != 'host' or launch['host'] != '127.0.0.1':
        raise render_upload.Rejected('the qwen profile must bind 127.0.0.1 on the host network only')
    args, proposed = qwen_args(qwen, settings)
    staged = model['staged_dir']
    limits = {'driver_major_min': launch['driver_major_min'], 'gpu_count': launch['gpu_count'],
              'gpu_total_min_bytes': launch['gpu_total_min_bytes'],
              'gpu_memory_utilization': launch['gpu_memory_utilization'],
              'cache_dir': staged, 'cache_free_min_bytes': 0,  # read-only staged weights: nothing is written
              'shm_min_bytes': int(launch['shm_size_mb'] * 2**20 * 0.9)}
    mounts = []
    if status:
        limits['status_path'] = f'{status[1]}/status.json'
        mounts = ['-v', f'{status[0]}:{status[1]}']
    guard = (HERE/'startup_guard.py').read_text()
    if 'GUARD_SOURCE_END' in guard:
        raise render_upload.Rejected('guard source collides with the heredoc marker')
    run = ['docker', 'run', '--rm', '--name', launch['container_name'], '--gpus', 'all', '--network', 'host',
           '--shm-size', f"{launch['shm_size_mb']}m", '-v', f'{staged}:{staged}:ro', *mounts,
           '-e', 'HF_HUB_OFFLINE=1', '-e', 'TRANSFORMERS_OFFLINE=1', '-e', 'VLLM_NO_USAGE_STATS=1',
           '-e', 'DO_NOT_TRACK=1', '--entrypoint', 'python3', runtime['image_id'], '-c']
    tail = [json.dumps(limits, sort_keys=True), '--', 'vllm', 'serve', staged,
            '--served-model-name', qwen['served_model_name'], '--host', launch['host'], '--port', str(launch['port']),
            *args]
    q = shlex.quote
    return f"""#!/usr/bin/env bash
# Qwen {model['repo']}@{model['revision']} as {qwen['served_model_name']} ({qwen['id']}, settings: {settings}).
# Runtime-proven flags are the PR349/PR350 vllm serve set; NOT runtime-proven here: {' '.join(proposed) or 'none'}.
# Rendered by workspace/models/serving/render_local.py (render_qwen). Loopback only: http://{launch['host']}:{launch['port']}/v1
set -euo pipefail
IMAGE_ID={q(runtime['image_id'])}
SCRATCH={q(staged.rsplit('/', 2)[0])}
MODEL_DIR={q(staged)}
if ! mountpoint -q "$SCRATCH"; then echo "refused: $SCRATCH is not a mounted scratch filesystem" >&2; exit {EXIT_PRECONDITION}; fi
actual="$(docker image inspect --format '{{{{.Id}}}}' "$IMAGE_ID" 2>/dev/null || true)"
if [ "$actual" != "$IMAGE_ID" ]; then echo "refused: image $IMAGE_ID is not present locally (this script never pulls)" >&2; exit {EXIT_PRECONDITION}; fi
if [ ! -d "$MODEL_DIR" ]; then echo "refused: $MODEL_DIR is not staged (this script never downloads)" >&2; exit {EXIT_PRECONDITION}; fi
GUARD="$(cat <<'GUARD_SOURCE_END'
{guard}GUARD_SOURCE_END
)"
exec {' '.join(q(x) for x in run)} "$GUARD" {' '.join(q(x) for x in tail)}
""", limits


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--offline', action='store_true')
    p.add_argument('--config', type=Path, default=capacity.CONFIG)
    a = p.parse_args(argv)
    try:
        script, limits = render(capacity.load(a.config), a.offline)
    except render_upload.Rejected as e:
        print(f'rejected: {e}', file=sys.stderr)
        return 2
    data = script.encode()
    tmp = a.out.with_name(a.out.name + '.tmp')
    tmp.write_bytes(data)
    os.chmod(tmp, 0o755)
    os.replace(tmp, a.out)
    print(json.dumps({'out': str(a.out), 'sha256': hashlib.sha256(data).hexdigest(), 'profile': PROFILE,
                      'offline': a.offline, 'guard_limits': limits}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
