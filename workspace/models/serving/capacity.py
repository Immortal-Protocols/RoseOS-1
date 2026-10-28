#!/usr/bin/env python3
"""Derive per-GPU memory fit for each serving option in muse-glimmer-30b.json.

Arithmetic from the pinned config only; it is not a measurement. A live
deployment must confirm the numbers before any capacity claim.
"""
import argparse
import json
from pathlib import Path

CONFIG = Path(__file__).resolve().with_name('muse-glimmer-30b.json')
BF16 = 2
KV_BYTES = 2  # bf16/f16 cache


def kv_bytes_per_sequence(text, context, tp, sliding_aware):
    """K and V for one sequence on one GPU. KV heads shard across TP, never below one."""
    heads = max(1, text['num_key_value_heads'] // tp)
    per_token_layer = 2*heads*text['head_dim']*KV_BYTES
    context = min(context, text['max_position_embeddings'])
    if not sliding_aware:
        return text['num_hidden_layers']*context*per_token_layer
    window = min(context, text['sliding_window'])
    return (text['global_attention_layers']*context + text['sliding_attention_layers']*window)*per_token_layer


def weight_bytes_per_gpu(model, option):
    """Best case: everything shards. Worst case: the vision tower is replicated on every GPU."""
    if 'weights' in option:
        return option['weights']['bytes'], option['weights']['bytes']
    total = model['bf16_parameters']*BF16
    tp = option['machine_spec']['acceleratorCount']
    vision = model['vision_parameters_upper_bound']*BF16
    return total/tp, (total-vision)/tp + vision


def fit(config, option):
    model, text = config['model'], config['model']['text_config']
    tp = option['machine_spec']['acceleratorCount'] if 'weights' not in option else 1
    context = config['demo_assumptions']['max_model_len']
    usable = option['gpu_memory_bytes']*option['gpu_memory_utilization']
    best, worst = weight_bytes_per_gpu(model, option)
    kv_naive = kv_bytes_per_sequence(text, context, tp, sliding_aware=False)*option['max_num_seqs']
    reserve = option['runtime_reserve_bytes_per_gpu']
    headroom = usable - worst - kv_naive - reserve
    return {'option': option['id'], 'usable': usable, 'weights_best': best, 'weights_worst': worst,
            'kv_worst': kv_naive, 'reserve': reserve, 'headroom': headroom, 'fits': headroom > 0}


def load(path=CONFIG):
    return json.loads(Path(path).read_text())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=CONFIG)
    p.add_argument('--check', action='store_true', help='exit 1 if any option lacks headroom')
    a = p.parse_args()
    config = load(a.config)
    text = config['model']['text_config']
    gb = lambda b: f'{b/1e9:6.2f} GB'
    print(f"model {config['model']['repo']}@{config['model']['revision'][:12]}  "
          f"bf16 weights {gb(config['model']['bf16_parameters']*BF16)}")
    for context in (16384, 131072):
        print(f'  KV per sequence, unsharded, {context} tokens: sliding-aware '
              f'{gb(kv_bytes_per_sequence(text, context, 1, True))}, all layers {gb(kv_bytes_per_sequence(text, context, 1, False))}')
    results = [fit(config, option) for option in config['options']]
    for r in results:
        print(f"{r['option']}: usable {gb(r['usable'])}/GPU; weights {gb(r['weights_best'])}..{gb(r['weights_worst'])}; "
              f"KV(worst) {gb(r['kv_worst'])}; reserve {gb(r['reserve'])}; headroom {gb(r['headroom'])}; "
              f"{'fits' if r['fits'] else 'DOES NOT FIT'} (derived, not measured)")
    if a.check and not all(r['fits'] for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
