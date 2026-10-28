#!/usr/bin/env python3
"""Render the Agent Platform Model upload request for the Muse Glimmer first run.

Offline only: it reads the pinned config and the regional image URI you supply, and writes the
request body for POST https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}/models:upload.
It makes no network call and handles no credentials. The startup guard goes inline into
containerSpec.command with its numeric limits computed here, so the container needs nothing from
this repository.

  python3 workspace/models/serving/render_upload.py \
    --image-uri us-central1-docker.pkg.dev/PROJECT/REPO/vllm-openai@sha256:<pinned linux/amd64 digest> \
    --out upload-request.json

On success it writes the body to --out and prints one JSON summary line (output sha256, image,
limits). On any rejection it writes nothing and exits 2.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import capacity  # noqa: E402
GUARD = HERE/'startup_guard.py'
# ModelContainerSpec fields this request may carry (Agent Platform REST v1 reference).
CONTAINER_FIELDS = {'imageUri', 'command', 'args', 'env', 'ports', 'predictRoute', 'healthRoute',
                    'sharedMemorySizeMb', 'startupProbe', 'healthProbe', 'livenessProbe', 'deploymentTimeout'}
PROJECT = r'[a-z][a-z0-9-]{4,28}[a-z0-9]'


class Rejected(Exception):
    pass


def guard_limits(config, option):
    """Numeric guard limits for the configured context: worst-case weights, all-layer KV, runtime reserve."""
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


def check_image(uri, option):
    runtime = option['runtime']
    region = runtime['artifact_registry_region']
    digest = runtime['image_digest']
    pattern = rf'^{re.escape(region)}-docker\.pkg\.dev/{PROJECT}/[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._/-]*@{re.escape(digest)}$'
    if not re.fullmatch(pattern, uri):
        raise Rejected(f'image URI must be a {region} Artifact Registry reference pinned to {digest} (no tag, no other digest)')


def check_context(config, option):
    args = option['container_spec']['args']
    context = int(args[args.index('--max-model-len')+1])
    env = config['prompt_envelope']
    if not (context == config['demo_assumptions']['max_model_len'] == env['max_model_len']):
        raise Rejected(f'context {context} disagrees with demo/envelope settings')
    if context > config['model']['text_config']['max_position_embeddings']:
        raise Rejected(f'context {context} exceeds the model maximum')
    if env['acceptance_prompt_tokens_max'] + env['acceptance_max_tokens'] + env['slack_tokens'] > context:
        raise Rejected('prompt + output + slack exceeds the context')
    if int(args[args.index('--max-num-seqs')+1]) != option['max_num_seqs']:
        raise Rejected('--max-num-seqs disagrees with max_num_seqs')
    if int(args[args.index('--tensor-parallel-size')+1]) != option['machine_spec']['acceleratorCount']:
        raise Rejected('tensor parallel size disagrees with the GPU count')


def render(config, image_uri, display_name):
    option = config['options'][0]
    check_image(image_uri, option)
    check_context(config, option)
    limits = guard_limits(config, option)
    spec = {k: v for k, v in option['container_spec'].items() if k in CONTAINER_FIELDS}
    spec['imageUri'] = image_uri
    spec['command'] = ['python3', '-c', GUARD.read_text(), json.dumps(limits, sort_keys=True), '--', 'vllm', 'serve']
    model = config['model']
    return {'model': {'displayName': display_name,
                      'description': f"{model['repo']}@{model['revision']} served as {config['wire_contract']['model_field']}; "
                                     f"{option['id']}, context {config['demo_assumptions']['max_model_len']}",
                      'containerSpec': spec}}, limits


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--image-uri', required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--display-name', default='muse-glimmer-30b-8k')
    p.add_argument('--config', type=Path, default=capacity.CONFIG)
    a = p.parse_args(argv)
    try:
        body, limits = render(capacity.load(a.config), a.image_uri, a.display_name)
    except Rejected as e:
        print(f'rejected: {e}', file=sys.stderr)
        return 2
    data = (json.dumps(body, indent=2, sort_keys=True) + '\n').encode()
    tmp = a.out.with_name(a.out.name + '.tmp')
    tmp.write_bytes(data)
    os.replace(tmp, a.out)
    print(json.dumps({'out': str(a.out), 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                      'imageUri': a.image_uri, 'guard_limits': limits}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
