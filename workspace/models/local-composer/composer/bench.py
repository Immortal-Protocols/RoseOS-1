"""Benchmark and validity harness: JSONL records plus a summary.

Every record carries its environment and says what was and was not measured. Composer-side time is never labelled
request-to-render: that needs OS Product's renderer timestamp. Memory and energy stay null until measured on the
device by an admitted route. Usefulness here is recall and precision against each synthetic case's `expect` IDs, a
proxy that a human review of real layouts replaces (a layout that shows everything scores full recall and poor
precision).

    python3 -m composer.bench --cases contract/examples/cases.json --backend heuristic --repeats 3
"""

from __future__ import annotations

import argparse
import copy
import json
import platform
import statistics
import sys
import time

from .backends import HeuristicBackend, LlamaServerBackend
from .compose import compose

ENV_KEYS = ('device', 'os', 'runtime', 'runtimeVersion', 'backend', 'model', 'revision', 'quantization',
            'contextTokens', 'threads')


def host_env(backend) -> dict:
    """The environment record for a run on this machine (not a phone)."""
    info = backend.info
    return {'device': f'host:{platform.machine()}', 'os': f'{platform.system()} {platform.release()}',
            'runtime': info.get('runtime'), 'runtimeVersion': info.get('runtimeVersion'),
            'backend': type(backend).__name__, 'model': info.get('id'), 'revision': info.get('revision'),
            'quantization': info.get('quantization'), 'contextTokens': info.get('contextTokens'),
            'threads': info.get('threads'), 'python': platform.python_version()}


def _ids(layout) -> set:
    return {i for s in layout['sections'] for i in s['items'] + s['actions']}


def run(cases: list, backend, env: dict, repeats: int = 3, clock=time.perf_counter) -> list:
    missing = [k for k in ENV_KEYS if k not in env]
    if missing:
        raise ValueError(f'env record lacks {missing}')
    records = []
    for case in cases:
        for i in range(repeats):
            res = compose(copy.deepcopy(case['request']), backend, clock=clock)
            got = _ids(res['layout']) if res['status'] == 'ok' else set()
            expect = set(case.get('expect', []))
            records.append({
                'case': case['id'], 'trigger': case['request']['trigger'], 'run': i,
                # the first run in this process; a true cold start (fresh process, model load) is measured on device
                'phase': 'first' if i == 0 else 'warm',
                'status': res['status'], 'failure': res.get('failure'),
                'timing': res.get('timing', {}), 'timingScope': 'composer-only',
                'requestToRenderMs': None, 'peakRssMb': None, 'energyMj': None,
                'recall': round(len(got & expect) / len(expect), 3) if expect else None,
                'precision': round(len(got & expect) / len(got), 3) if expect and got else None,
                'items': len(got), 'context': res.get('context'), 'runtimeStats': res.get('runtimeStats'),
                'env': env})
    return records


def _pct(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, round(q * (len(values) - 1)))]


def summarize(records: list) -> dict:
    out = {'runs': len(records), 'ok': sum(r['status'] == 'ok' for r in records), 'failures': {}}
    for r in records:
        if r['failure']:
            out['failures'][r['failure']] = out['failures'].get(r['failure'], 0) + 1
    for phase in ('first', 'warm'):
        for key in ('composeMs', 'generationMs'):
            vals = [r['timing'][key] for r in records if r['phase'] == phase and key in r['timing']]
            out[f'{phase}.{key}'] = {'p50': _pct(vals, 0.5), 'p95': _pct(vals, 0.95), 'n': len(vals)}
    recalls = [r['recall'] for r in records if r['recall'] is not None]
    out['meanRecall'] = round(statistics.mean(recalls), 3) if recalls else None
    precisions = [r['precision'] for r in records if r['precision'] is not None]
    out['meanPrecision'] = round(statistics.mean(precisions), 3) if precisions else None
    out['timingScope'] = 'composer-only (not request-to-render)'
    out['unmeasured'] = ['requestToRenderMs', 'peakRssMb', 'energyMj']
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--cases', required=True)
    ap.add_argument('--backend', choices=('heuristic', 'llama-server'), default='heuristic')
    ap.add_argument('--url', default='http://127.0.0.1:8080', help='llama-server on this device (loopback only)')
    ap.add_argument('--model-info', help='JSON file: id, revision, quantization, runtime, runtimeVersion, ...')
    ap.add_argument('--repeats', type=int, default=3)
    ap.add_argument('--out', help='JSONL output path (default: none, summary only)')
    args = ap.parse_args(argv)
    with open(args.cases) as f:
        cases = json.load(f)['cases']
    if args.backend == 'heuristic':
        backend = HeuristicBackend()
    else:
        if not args.model_info:
            ap.error('--model-info is required for a model backend: results must name model, revision and quant')
        with open(args.model_info) as f:
            backend = LlamaServerBackend(args.url, json.load(f))
    records = run(cases, backend, host_env(backend), args.repeats)
    if args.out:
        with open(args.out, 'w') as f:
            for r in records:
                f.write(json.dumps(r, sort_keys=True) + '\n')
    json.dump(summarize(records), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
