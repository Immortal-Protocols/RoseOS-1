#!/usr/bin/env python3
"""Actual-model receipt check for the rose-chat two-turn demo (Models QA, advisory).

Two modes, standard library only:

  run    Probe the serving runtime, run rose-chat for two synthetic turns and write
         a receipt directory. The executor supplies the instance and server-start
         receipts it captured itself; this script never contacts a cloud API, starts
         a server or handles credentials beyond an optional env-var name.

    python3 qa/models/runtime_receipt.py run --tier runtime --bin target/debug/rose-chat \
        --base-url http://127.0.0.1:PORT/v1 --out receipt/ \
        --instance-receipt instance.json --server-receipt server.json \
        [--source-rev SHA] [--cargo-lock path/Cargo.lock] [--auth-env VAR]

  check  Judge a receipt directory against criteria R1-R7 and print one verdict per
         criterion, plus observed (ungated) facts and a failure class.

    python3 qa/models/runtime_receipt.py check receipt/

--tier fixture marks a run against qa/models/fixture_server.py; such a verdict
never counts as actual-model evidence. Expected pins come from Core's
workspace/models/serving/muse-glimmer-30b.json in this checkout.

Receipt inputs written by the executor (JSON, non-secret):
  instance.json  {"instance_id": "...", "zone": "...", "machine_type": "...", "gpu": "...", "captured_utc": "..."}
  server.json    {"image_digest": "sha256:...", "args": [... vllm serve args ...],
                  "startup_log_excerpt": "...", "captured_utc": "...",
                  "weights": {"repo": "...", "revision": "...", "files": {"name": bytes}}}
                 ("weights" is required only when args serve a local path instead of the repo id)
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CORE_PIN = os.path.join(HERE, '..', '..', 'workspace', 'models', 'serving', 'muse-glimmer-30b.json')
EXPECTED_INSTANCE = '5206786343410057895'
CODE_WORD = 'ORCHID-4417'
TURNS = [f'Remember the code word {CODE_WORD}. Reply with only OK.',
         'What code word did I ask you to remember? Reply with only the code word.']
# Turn 2 replaces nothing: with history its prompt adds user 1, reply 1 and user 2. Without
# history the prompt only swaps user 1 for a similar-length user 2 (a few tokens' change).
MIN_HISTORY_DELTA_TOKENS = 10
SECRET_PATTERNS = [re.compile(p) for p in (r'Bearer\s+[A-Za-z0-9._~+/-]{8,}', r'ya29\.[A-Za-z0-9._-]+',
                                           r'(?i)authorization:', r'X-Goog-Signature=', r'(?i)private_key')]


def pins():
    with open(CORE_PIN) as f:
        d = json.load(f)
    return {'repo': d['model']['repo'], 'revision': d['model']['revision'],
            'alias': next(iter(d['model']['served_alias'])),
            'image_digest': d['options'][0]['runtime']['image_digest'],
            'max_model_len': d['demo_assumptions']['max_model_len'],
            'files': d['model']['weights_fetch']['files']}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def probe(url, timeout):
    started = time.monotonic()
    req = urllib.request.Request(url, method='GET')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback tunnel: never a proxy
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read(1 << 20)
            return {'url': url, 'status': r.status, 'elapsed_s': round(time.monotonic() - started, 3),
                    'body': body.decode('utf-8', 'replace')}
    except urllib.error.HTTPError as e:
        return {'url': url, 'status': e.code, 'elapsed_s': round(time.monotonic() - started, 3), 'body': ''}
    except (urllib.error.URLError, OSError) as e:
        return {'url': url, 'status': None, 'elapsed_s': round(time.monotonic() - started, 3),
                'error': type(getattr(e, 'reason', e)).__name__}


def write_json(path, value):
    with open(path, 'w') as f:
        json.dump(value, f, indent=1)


def run(args):
    os.makedirs(args.out, exist_ok=True)
    p = pins()
    base = args.base_url.rstrip('/')
    root = base[:-3] if base.endswith('/v1') else base
    write_json(os.path.join(args.out, 'meta.json'), {'tier': args.tier, 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                                    'base_url': base, 'model_id': p['alias'], 'turns': TURNS})
    write_json(os.path.join(args.out, 'health.json'), probe(root + '/health', 10))
    write_json(os.path.join(args.out, 'models.json'), probe(base + '/models', 10))
    for name, src in (('instance.json', args.instance_receipt), ('server.json', args.server_receipt)):
        if src:
            shutil.copyfile(src, os.path.join(args.out, name))
    client = {'bin_sha256': sha256(args.bin), 'source_rev': args.source_rev,
              'cargo_lock_sha256': sha256(args.cargo_lock) if args.cargo_lock else None}
    auth = f'\n[auth]\nmode = "bearer-env"\nenv_var = "{args.auth_env}"\n' if args.auth_env else ''
    config = (f'[endpoint]\nbase_url = "{base}"\nprotocol = "openai-chat-candidate"\n\n'
              f'[model]\nid = "{p["alias"]}"\nexpected_revision = "{p["revision"]}"\n{auth}\n'
              f'[timeouts]\nconnect_ms = {min(10000, args.request_ms)}\nrequest_ms = {args.request_ms}\n')
    cfg = os.path.join(args.out, 'config.toml')
    with open(cfg, 'w') as f:
        f.write(config)
    script = os.path.join(args.out, 'turns.txt')
    with open(script, 'w') as f:
        f.write(''.join(t + '\n' for t in TURNS))
    started = time.monotonic()
    proc = subprocess.run([args.bin, 'chat', '--config', cfg, '--script', script,
                           '--transcript', os.path.join(args.out, 'transcript.jsonl'), '--label', f'qa-{args.tier}'],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=args.request_ms / 1000 * 2 + 60)
    client.update({'exit': proc.returncode, 'elapsed_s': round(time.monotonic() - started, 3)})
    write_json(os.path.join(args.out, 'client.json'), client)
    with open(os.path.join(args.out, 'stdout.txt'), 'w') as f:
        f.write(proc.stdout)
    with open(os.path.join(args.out, 'stderr.txt'), 'w') as f:
        f.write(proc.stderr)
    if args.auth_env and os.environ.get(args.auth_env):
        # Keep only a fingerprint so check can prove the value is absent without storing it.
        write_json(os.path.join(args.out, 'auth_fingerprint.json'),
                   {'env_var': args.auth_env, 'sha256': hashlib.sha256(os.environ[args.auth_env].encode()).hexdigest()})
    return check_dir(args.out, auth_value=os.environ.get(args.auth_env) if args.auth_env else None)


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def check_dir(directory, auth_value=None):
    p = pins()
    meta = load(os.path.join(directory, 'meta.json')) or {}
    tier = meta.get('tier', 'unknown')
    results = []

    def record(cid, state, observed):
        results.append({'id': cid, 'state': state, 'observed': observed})

    inst = load(os.path.join(directory, 'instance.json'))
    if inst is None:
        record('R1 H100 instance', 'UNAVAILABLE', 'no instance.json receipt')
    else:
        ok = str(inst.get('instance_id')) == EXPECTED_INSTANCE and 'H100' in str(inst.get('gpu', '')).upper()
        record('R1 H100 instance', 'PASS' if ok else 'FAIL',
               f'instance_id={inst.get("instance_id")}; gpu={inst.get("gpu")}; zone={inst.get("zone")}')

    srv = load(os.path.join(directory, 'server.json'))
    if srv is None:
        record('R2 pinned image and model at startup', 'UNAVAILABLE', 'no server.json receipt')
    else:
        a = [str(x) for x in srv.get('args', [])]

        def flag(name):
            return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else None
        problems = []
        if srv.get('image_digest') != p['image_digest']:
            problems.append(f'image {srv.get("image_digest")}')
        weights = srv.get('weights') or {}
        if p['repo'] in a:
            if flag('--revision') != p['revision']:
                problems.append(f'--revision {flag("--revision")}')
        elif weights:
            # Served from a local path: the executor's weights receipt must name the pin and match file sizes.
            if weights.get('repo') != p['repo'] or weights.get('revision') != p['revision']:
                problems.append(f'weights receipt {weights.get("repo")}@{weights.get("revision")}')
            got = {k: int(v) for k, v in (weights.get('files') or {}).items()}
            if got != {k: int(v) for k, v in p['files'].items()}:
                problems.append('weights file sizes differ from Core pin')
        else:
            problems.append('model repo absent from args and no weights receipt')
        if flag('--served-model-name') != p['alias']:
            problems.append(f'--served-model-name {flag("--served-model-name")}')
        if not str(srv.get('startup_log_excerpt', '')).strip():
            problems.append('no startup log excerpt')
        record('R2 pinned image and model at startup', 'FAIL' if problems else 'PASS',
               '; '.join(problems) or f'digest ok; revision ok; alias ok; tp={flag("--tensor-parallel-size")}; '
                                     f'max-model-len={flag("--max-model-len")}')

    health = load(os.path.join(directory, 'health.json')) or {}
    models = load(os.path.join(directory, 'models.json')) or {}
    ids, roots = [], []
    try:
        data = json.loads(models.get('body') or '{}').get('data', [])
        ids = [m.get('id') for m in data]
        roots = [m.get('root') for m in data]
    except ValueError:
        pass
    ready = health.get('status') == 200 and p['alias'] in ids
    record('R3 server ready, alias served', 'PASS' if ready else 'FAIL',
           f'/health={health.get("status") or health.get("error")}; /v1/models ids={ids}; roots={roots}')

    client = load(os.path.join(directory, 'client.json')) or {}
    record('R4 actual Rust client identity', 'PASS' if client.get('bin_sha256') and client.get('source_rev') else
           ('INCONCLUSIVE' if client.get('bin_sha256') else 'UNAVAILABLE'),
           f'source_rev={client.get("source_rev")}; bin={str(client.get("bin_sha256"))[:12]}; '
           f'Cargo.lock={str(client.get("cargo_lock_sha256"))[:12]}')

    records = []
    try:
        with open(os.path.join(directory, 'transcript.jsonl')) as f:
            records = [json.loads(line) for line in f if line.strip()]
    except (FileNotFoundError, ValueError):
        pass
    turns = [r for r in records if r.get('type') == 'turn']
    types = [r.get('type') for r in records]
    outcomes = [t.get('outcome') for t in turns]
    served = [t.get('served_model') for t in turns]
    two_ok = (client.get('exit') == 0 and types == ['run', 'turn', 'turn', 'summary'] and outcomes == ['ok', 'ok']
              and served == [p['alias'], p['alias']])
    record('R5 two ordered successful turns', 'PASS' if two_ok else 'FAIL',
           f'exit={client.get("exit")}; records={types}; outcomes={outcomes}; served_model={served}')

    if len(turns) == 2 and all(isinstance(t.get('prompt_tokens'), int) for t in turns):
        delta = turns[1]['prompt_tokens'] - turns[0]['prompt_tokens']
        record('R6 turn 2 carried earlier history', 'PASS' if delta >= MIN_HISTORY_DELTA_TOKENS else 'FAIL',
               f'prompt_tokens {turns[0]["prompt_tokens"]} -> {turns[1]["prompt_tokens"]} (delta {delta}, '
               f'needs >= {MIN_HISTORY_DELTA_TOKENS})')
    else:
        record('R6 turn 2 carried earlier history', 'INCONCLUSIVE', 'server did not report usage.prompt_tokens on both turns')

    leaks = []
    fingerprint = (load(os.path.join(directory, 'auth_fingerprint.json')) or {}).get('sha256')
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not os.path.isfile(path) or name == 'auth_fingerprint.json':
            continue
        with open(path, errors='replace') as f:
            text = f.read()
        if auth_value and auth_value in text:
            leaks.append(f'{name}: auth value')
        leaks += [f'{name}: {pat.pattern[:20]}' for pat in SECRET_PATTERNS if pat.search(text)]
    record('R7 no secret material in receipts', 'FAIL' if leaks else 'PASS',
           ', '.join(leaks) or ('checked patterns' + (' and auth value' if auth_value else
                                                      (' (auth fingerprint present; value not available to recheck)' if fingerprint else ''))))

    replies = []
    try:
        with open(os.path.join(directory, 'stdout.txt')) as f:
            replies = [line[len('assistant> '):].strip() for line in f if line.startswith('assistant> ')]
    except FileNotFoundError:
        pass
    observed = {
        'reply_2_recalls_code_word': (CODE_WORD in replies[1]) if len(replies) > 1 else None,
        'replies': replies,
        'turn_elapsed_ms': [t.get('elapsed_ms') for t in turns],
        'finish_reason': [t.get('finish_reason') for t in turns],
        'reasoning_chars': [t.get('reasoning_chars') for t in turns],
        'usage': [(t.get('prompt_tokens'), t.get('completion_tokens')) for t in turns],
    }

    if client.get('exit') in (2, 3):
        failure = 'client config (rose-chat exit %s; see stderr.txt)' % client.get('exit')
    elif health.get('status') is None:
        failure = 'transport'
    elif not ready:
        failure = 'server load'
    elif any(o and o.startswith('timeout') for o in outcomes):
        failure = 'timeout'
    elif 'model_identity_mismatch' in outcomes or any(r['id'].startswith(('R1', 'R2')) and r['state'] == 'FAIL'
                                                      for r in results):
        failure = 'model identity'
    elif any(o in ('connect',) for o in outcomes):
        failure = 'transport'
    elif any(o not in ('ok', None) for o in outcomes) or len(outcomes) < 2:
        failure = 'generation'
    else:
        failure = None

    gated = results
    actual = tier == 'runtime' and all(r['state'] == 'PASS' for r in gated)
    verdict = ('ACTUAL-MODEL TWO-TURN CHAT: PASS' if actual else
               f'{"FIXTURE" if tier == "fixture" else tier.upper()} RUN: '
               f'{"all criteria met (not actual-model evidence)" if all(r["state"] == "PASS" for r in gated) else "criteria not all met"}')
    width = max(len(r['id']) for r in results)
    print(f'tier={tier}')
    for r in results:
        print(f'{r["state"]:12} {r["id"]:{width}}  {r["observed"]}')
    print('observed (not gated):', json.dumps(observed))
    print('failure class:', failure or 'none')
    print(verdict)
    write_json(os.path.join(directory, 'verdict.json'), {'tier': tier, 'results': results, 'observed': observed,
                                                         'failure_class': failure, 'verdict': verdict})
    return 0 if all(r['state'] == 'PASS' for r in gated) else 1


def main():
    parser = argparse.ArgumentParser(description='rose-chat actual-model receipt check')
    sub = parser.add_subparsers(dest='mode', required=True)
    r = sub.add_parser('run')
    r.add_argument('--tier', choices=['fixture', 'runtime'], required=True)
    r.add_argument('--bin', required=True)
    r.add_argument('--base-url', required=True, help='OpenAI-style base, e.g. http://127.0.0.1:PORT/v1')
    r.add_argument('--out', required=True)
    r.add_argument('--instance-receipt')
    r.add_argument('--server-receipt')
    r.add_argument('--source-rev')
    r.add_argument('--cargo-lock')
    r.add_argument('--auth-env', help='env var NAME holding a bearer value, if the tunnel needs one')
    r.add_argument('--request-ms', type=int, default=120000)
    c = sub.add_parser('check')
    c.add_argument('directory')
    args = parser.parse_args()
    return run(args) if args.mode == 'run' else check_dir(args.directory)


if __name__ == '__main__':
    sys.exit(main())
