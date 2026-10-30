#!/usr/bin/env python3
"""Actual-model receipt check for the rose-chat two-turn demo (Models QA, advisory).

Two modes, standard library only:

  run    Probe the serving runtime, run rose-chat for two synthetic turns with the
         agreed request envelope and write a receipt directory. The executor
         supplies the instance and server-start receipts, and optionally a request
         capture and weight-verification inputs. This script never contacts a cloud
         API, starts a server, downloads a model or handles credential values.

    python3 qa/models/runtime_receipt.py run --tier runtime --bin target/debug/rose-chat \
        --base-url http://127.0.0.1:PORT/v1 --out receipt/ \
        --instance-receipt instance.json --server-receipt server.json \
        [--capture capture.jsonl] [--weights-dir DIR --expected-manifest manifest.json] \
        [--source-rev SHA] [--cargo-lock path/Cargo.lock] [--request-ms N] [--auth-env VAR]

  check  Judge a receipt directory and print one verdict per criterion, plus
         observed (ungated) facts and a failure class.

    python3 qa/models/runtime_receipt.py check receipt/

What check proves: it validates the receipts and provenance it is given. It does not
observe a live machine; a receipt is only as good as its executor. --tier fixture
marks a run against qa/models/fixture_server.py and never yields an actual-model
verdict. Expected pins come from Core's workspace/models/serving/muse-glimmer-30b.json.

Request envelope written to the generated config (Product's real-run contract):
max_tokens 512, context_budget_chars 16000, system prompt "Reasoning strength: low",
connect_ms 5000, request_ms 180000 unless --request-ms overrides it, auth mode
"none" unless --auth-env selects bearer-env, expected_revision repo@revision.

Inputs (JSON, non-secret):
  instance.json  {"instance_id", "zone", "machine_type", "gpu", "captured_utc"}
  server.json    {"image_digest", "args": [serve args], "startup_log_excerpt", "captured_utc",
                  "weights": {"repo", "revision"}}  ("weights" only when args serve a local path;
                  it is a declaration, not verification)
  capture.jsonl  schema rose-models-qa-capture/1, one line per HTTP exchange the client made:
                 {"schema", "seq", "path", "request": <JSON body>, "response_status": int,
                  "response": <JSON body or null>}
  expected manifest  schema rose-models-qa-manifest/1, supplied independently of the observed files:
                 {"schema", "repo", "revision", "provenance": {"source": "...", "retrieved_utc": "..."},
                  "files": {name: {"bytes": int, "sha256": hex}}}
  weights_observed.json  written by run --weights-dir: sha256 and bytes of each local file.
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
SYSTEM_PROMPT = 'Reasoning strength: low'
MAX_TOKENS = 512
CONTEXT_BUDGET_CHARS = 16000
CONNECT_MS = 5000
REQUEST_MS = 180000
CAPTURE_SCHEMA = 'rose-models-qa-capture/1'
MANIFEST_SCHEMA = 'rose-models-qa-manifest/1'
OBSERVED_SCHEMA = 'rose-models-qa-weights-observed/1'
SECRET_PATTERNS = [re.compile(p) for p in (r'Bearer\s+[A-Za-z0-9._~+/-]{8,}', r'ya29\.[A-Za-z0-9._-]+',
                                           r'(?i)authorization:', r'X-Goog-Signature=', r'(?i)private_key')]


def pins():
    with open(CORE_PIN) as f:
        d = json.load(f)
    return {'repo': d['model']['repo'], 'revision': d['model']['revision'],
            'alias': next(iter(d['model']['served_alias'])),
            'image_digest': d['options'][0]['runtime']['image_digest'],
            'files': {k: int(v) for k, v in d['model']['weights_fetch']['files'].items()}}


def render_config(base_url, alias, canonical, request_ms=REQUEST_MS, auth_env=None):
    auth = f'mode = "bearer-env"\nenv_var = "{auth_env}"' if auth_env else 'mode = "none"'
    return (f'[endpoint]\nbase_url = "{base_url}"\nprotocol = "openai-chat-candidate"\n\n'
            f'[model]\nid = "{alias}"\nexpected_revision = "{canonical}"\n\n'
            f'[auth]\n{auth}\n\n'
            f'[timeouts]\nconnect_ms = {min(CONNECT_MS, request_ms)}\nrequest_ms = {request_ms}\n\n'
            f'[limits]\nmax_tokens = {MAX_TOKENS}\ncontext_budget_chars = {CONTEXT_BUDGET_CHARS}\n\n'
            f'[chat]\nsystem_prompt = "{SYSTEM_PROMPT}"\n')


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def get(url, timeout):
    started = time.monotonic()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback tunnel: never a proxy
    try:
        with opener.open(urllib.request.Request(url, method='GET'), timeout=timeout) as r:
            return {'url': url, 'status': r.status, 'elapsed_s': round(time.monotonic() - started, 3),
                    'body': r.read(1 << 20).decode('utf-8', 'replace')}
    except urllib.error.HTTPError as e:
        return {'url': url, 'status': e.code, 'elapsed_s': round(time.monotonic() - started, 3), 'body': ''}
    except (urllib.error.URLError, OSError) as e:
        return {'url': url, 'status': None, 'elapsed_s': round(time.monotonic() - started, 3),
                'error': type(getattr(e, 'reason', e)).__name__}


def write_json(path, value):
    with open(path, 'w') as f:
        json.dump(value, f, indent=1)


def observe_weights(directory):
    files = {}
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            files[name] = {'bytes': os.path.getsize(path), 'sha256': sha256_file(path)}
    return {'schema': OBSERVED_SCHEMA, 'files': files}


def capture_from_fixture(root, out_path):
    """Fixture tier only: turn the fixture's own request records into capture.jsonl."""
    records = json.loads(get(root + '/__qa/requests', 10)['body'])['requests']
    with open(out_path, 'w') as f:
        for r in records:
            f.write(json.dumps({'schema': CAPTURE_SCHEMA, 'seq': r['seq'], 'path': r['path'], 'request': r['body'],
                                'response_status': r.get('response_status'), 'response': r.get('response')}) + '\n')


def run(args):
    if args.capture_from_fixture and args.tier != 'fixture':
        raise SystemExit('--capture-from-fixture is only valid with --tier fixture')
    os.makedirs(args.out, exist_ok=True)
    p = pins()
    base = args.base_url.rstrip('/')
    root = base[:-3] if base.endswith('/v1') else base
    write_json(os.path.join(args.out, 'meta.json'), {
        'tier': args.tier, 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'base_url': base,
        'model_id': p['alias'], 'turns': TURNS, 'system_prompt': SYSTEM_PROMPT, 'max_tokens': MAX_TOKENS,
        'context_budget_chars': CONTEXT_BUDGET_CHARS, 'request_ms': args.request_ms})
    write_json(os.path.join(args.out, 'health.json'), get(root + '/health', 10))
    write_json(os.path.join(args.out, 'models.json'), get(base + '/models', 10))
    for name, src in (('instance.json', args.instance_receipt), ('server.json', args.server_receipt),
                      ('capture.jsonl', args.capture), ('expected_manifest.json', args.expected_manifest)):
        if src:
            shutil.copyfile(src, os.path.join(args.out, name))
    if args.weights_dir:
        write_json(os.path.join(args.out, 'weights_observed.json'), observe_weights(args.weights_dir))
    client = {'bin_sha256': sha256_file(args.bin), 'source_rev': args.source_rev,
              'cargo_lock_sha256': sha256_file(args.cargo_lock) if args.cargo_lock else None}
    cfg = os.path.join(args.out, 'config.toml')
    with open(cfg, 'w') as f:
        f.write(render_config(base, p['alias'], f'{p["repo"]}@{p["revision"]}', args.request_ms, args.auth_env))
    script = os.path.join(args.out, 'turns.txt')
    with open(script, 'w') as f:
        f.write(''.join(t + '\n' for t in TURNS))
    started = time.monotonic()
    proc = subprocess.run([args.bin, 'chat', '--config', cfg, '--script', script,
                           '--transcript', os.path.join(args.out, 'transcript.jsonl'), '--label', f'qa-{args.tier}'],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=args.request_ms / 1000 * 2 + 60)
    client.update({'exit': proc.returncode, 'elapsed_s': round(time.monotonic() - started, 3)})
    write_json(os.path.join(args.out, 'client.json'), client)
    for name, text in (('stdout.txt', proc.stdout), ('stderr.txt', proc.stderr)):
        with open(os.path.join(args.out, name), 'w') as f:
            f.write(text)
    if args.capture_from_fixture:
        capture_from_fixture(root.split('/s/')[0], os.path.join(args.out, 'capture.jsonl'))
    if args.auth_env and os.environ.get(args.auth_env):
        write_json(os.path.join(args.out, 'auth_fingerprint.json'),
                   {'env_var': args.auth_env, 'sha256': hashlib.sha256(os.environ[args.auth_env].encode()).hexdigest()})
    return check_dir(args.out, auth_value=os.environ.get(args.auth_env) if args.auth_env else None)


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def load_capture(path):
    """Return (records, error). records is None when the file is absent."""
    if not os.path.exists(path):
        return None, None
    out = []
    with open(path) as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                return [], f'line {n} is not JSON'
            if r.get('schema') != CAPTURE_SCHEMA or not isinstance(r.get('request'), dict):
                return [], f'line {n} is not a {CAPTURE_SCHEMA} record with a request object'
            out.append(r)
    return out, None


def messages_of(body):
    msgs = body.get('messages') if isinstance(body, dict) else None
    if not isinstance(msgs, list) or not all(isinstance(m, dict) for m in msgs):
        return None
    return [{'role': m.get('role'), 'content': m.get('content')} for m in msgs]


def judge_history(directory, replies):
    caps, err = load_capture(os.path.join(directory, 'capture.jsonl'))
    if caps is None:
        return 'INCONCLUSIVE', 'no direct request capture; token growth is only an observation'
    if err:
        return 'FAIL', f'capture malformed: {err}'
    chat = [c for c in caps if str(c.get('path', '')).endswith(('/chat/completions', ':rawPredict'))]
    if len(chat) != 2:
        return 'FAIL', f'{len(chat)} chat requests captured, expected exactly 2 (no hidden retries)'
    m1, m2 = messages_of(chat[0]['request']), messages_of(chat[1]['request'])
    if m1 is None or m2 is None:
        return 'FAIL', 'a captured request has no messages list'
    try:
        reply1 = chat[0]['response']['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        return 'INCONCLUSIVE', 'first response body not captured; cannot compare the replayed assistant text'
    if replies and replies[0] != reply1:
        return 'FAIL', 'captured first response contradicts the client output for turn 1'
    sys_msg = {'role': 'system', 'content': SYSTEM_PROMPT}
    want1 = [sys_msg, {'role': 'user', 'content': TURNS[0]}]
    want2 = want1 + [{'role': 'assistant', 'content': reply1}, {'role': 'user', 'content': TURNS[1]}]
    if m1 != want1:
        return 'FAIL', f'request 1 messages {[m["role"] for m in m1]} differ from [system, user1]'
    if m2 != want2:
        roles = [m['role'] for m in m2]
        return 'FAIL', f'request 2 roles {roles} or content differ from [system, user1, exact reply1, user2]'
    tokens = [c['request'].get('max_tokens') for c in chat]
    if tokens != [MAX_TOKENS, MAX_TOKENS]:
        return 'FAIL', f'captured max_tokens {tokens}, expected {MAX_TOKENS}'
    return 'PASS', 'request 2 = [system "Reasoning strength: low", user1, exact reply1, user2]; max_tokens 512 on both'


def judge_weights(directory, p):
    obs = load(os.path.join(directory, 'weights_observed.json'))
    man = load(os.path.join(directory, 'expected_manifest.json'))
    if obs is None or man is None:
        return 'INCONCLUSIVE', 'no observed file hashes and independent expected manifest'
    prov = man.get('provenance') if isinstance(man.get('provenance'), dict) else {}
    source = str(prov.get('source', '')).strip()
    if man.get('schema') != MANIFEST_SCHEMA or not source:
        return 'INCONCLUSIVE', 'manifest lacks schema or provenance.source'
    if 'weights_observed' in source or source.lower() in ('observed', 'self', 'local'):
        return 'INCONCLUSIVE', 'manifest provenance points at the observed files themselves'
    if man.get('repo') != p['repo'] or man.get('revision') != p['revision']:
        return 'FAIL', f'manifest pins {man.get("repo")}@{man.get("revision")}, Core pins {p["repo"]}@{p["revision"]}'
    expected = man.get('files') or {}
    observed = (obs.get('files') or {}) if obs.get('schema') == OBSERVED_SCHEMA else {}
    if any(not isinstance(v, dict) or not re.fullmatch(r'[0-9a-f]{64}', str(v.get('sha256', ''))) for v in expected.values()):
        return 'INCONCLUSIVE', 'manifest has files without sha256'
    if not observed or any(not re.fullmatch(r'[0-9a-f]{64}', str(v.get('sha256', ''))) for v in observed.values()):
        return 'INCONCLUSIVE', 'observed files lack sha256 (names/sizes alone do not prove identity)'
    if prov.get('copied_from_observed'):
        return 'INCONCLUSIVE', 'manifest declares itself copied from the observed list'
    missing_pin = sorted(set(p['files']) - set(expected))
    if missing_pin:
        return 'FAIL', f'manifest omits Core-pinned files {missing_pin}'
    if set(expected) != set(observed):
        return 'FAIL', f'missing {sorted(set(expected) - set(observed))}; extra {sorted(set(observed) - set(expected))}'
    bad = [n for n in expected if expected[n]['sha256'] != observed[n]['sha256']
           or int(expected[n].get('bytes', -1)) != int(observed[n].get('bytes', -2))]
    if bad:
        return 'FAIL', f'hash or size mismatch for {bad}'
    size_conflict = [n for n, b in p['files'].items() if int(observed[n]['bytes']) != b]
    if size_conflict:
        return 'FAIL', f'observed sizes differ from Core pin for {size_conflict}'
    return 'PASS', f'{len(observed)} files match the manifest sha256 and size; provenance: {source}'


def check_dir(directory, auth_value=None, pin=None):
    p = pin or pins()
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
        record('R2 declared image/model pin at startup', 'UNAVAILABLE', 'no server.json receipt')
    else:
        a = [str(x) for x in srv.get('args', [])]

        def flag(name):
            return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else None
        problems = []
        if srv.get('image_digest') != p['image_digest']:
            problems.append(f'image {srv.get("image_digest")}')
        declared = srv.get('weights') or {}
        if p['repo'] in a:
            if flag('--revision') != p['revision']:
                problems.append(f'--revision {flag("--revision")}')
        elif declared:
            if declared.get('repo') != p['repo'] or declared.get('revision') != p['revision']:
                problems.append(f'declared weights {declared.get("repo")}@{declared.get("revision")}')
        else:
            problems.append('model repo absent from args and no weights declaration')
        if flag('--served-model-name') != p['alias']:
            problems.append(f'--served-model-name {flag("--served-model-name")}')
        if not str(srv.get('startup_log_excerpt', '')).strip():
            problems.append('no startup log excerpt')
        record('R2 declared image/model pin at startup', 'FAIL' if problems else 'PASS',
               '; '.join(problems) or f'declared digest, repo@revision and alias match; tp={flag("--tensor-parallel-size")}')
    record('R2v verified weight identity', *judge_weights(directory, p))

    health = load(os.path.join(directory, 'health.json')) or {}
    models = load(os.path.join(directory, 'models.json')) or {}
    ids, roots = [], []
    try:
        data = json.loads(models.get('body') or '{}').get('data', [])
        ids, roots = [m.get('id') for m in data], [m.get('root') for m in data]
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

    replies = []
    try:
        with open(os.path.join(directory, 'stdout.txt')) as f:
            replies = [line[len('assistant> '):].rstrip('\n') for line in f if line.startswith('assistant> ')]
    except FileNotFoundError:
        pass
    record('R6 turn 2 replayed exact earlier history', *judge_history(directory, replies))

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
                                                      (' (auth fingerprint present)' if fingerprint else ''))))

    tokens = [t.get('prompt_tokens') for t in turns]
    observed = {
        'reply_2_recalls_code_word': (CODE_WORD in replies[1]) if len(replies) > 1 else None,
        'replies': replies,
        'prompt_tokens': tokens,
        'prompt_token_delta': (tokens[1] - tokens[0]) if len(tokens) == 2 and all(isinstance(t, int) for t in tokens) else None,
        'turn_elapsed_ms': [t.get('elapsed_ms') for t in turns],
        'finish_reason': [t.get('finish_reason') for t in turns],
        'reasoning_chars': [t.get('reasoning_chars') for t in turns],
    }

    if client.get('exit') in (2, 3):
        failure = f'client config (rose-chat exit {client.get("exit")}; see stderr.txt)'
    elif health.get('status') is None:
        failure = 'transport'
    elif not ready:
        failure = 'server load'
    elif any(o and o.startswith('timeout') for o in outcomes):
        failure = 'timeout'
    elif 'model_identity_mismatch' in outcomes or any(r['id'].startswith(('R1', 'R2')) and r['state'] == 'FAIL'
                                                      for r in results):
        failure = 'model identity'
    elif 'connect' in outcomes:
        failure = 'transport'
    elif any(o not in ('ok', None) for o in outcomes) or len(outcomes) < 2:
        failure = 'generation'
    else:
        failure = None

    all_pass = all(r['state'] == 'PASS' for r in results)
    if tier == 'runtime' and all_pass:
        verdict = 'ACTUAL-MODEL TWO-TURN CHAT: PASS (validated from supplied receipts)'
    elif tier == 'fixture':
        verdict = 'FIXTURE RUN: ' + ('all criteria met (not actual-model evidence)' if all_pass else 'criteria not all met')
    else:
        verdict = f'{tier.upper()} RUN: criteria not all met'
    width = max(len(r['id']) for r in results)
    print(f'tier={tier}')
    for r in results:
        print(f'{r["state"]:12} {r["id"]:{width}}  {r["observed"]}')
    print('observed (not gated):', json.dumps(observed))
    print('failure class:', failure or 'none')
    print(verdict)
    write_json(os.path.join(directory, 'verdict.json'), {'tier': tier, 'results': results, 'observed': observed,
                                                         'failure_class': failure, 'verdict': verdict})
    return 0 if all_pass else 1


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
    r.add_argument('--capture', help=f'{CAPTURE_SCHEMA} JSONL of the client\'s HTTP exchanges')
    r.add_argument('--capture-from-fixture', action='store_true', help='fixture tier: read capture from the fixture')
    r.add_argument('--weights-dir', help='local weight directory to hash (offline)')
    r.add_argument('--expected-manifest', help=f'{MANIFEST_SCHEMA} JSON supplied independently of the files')
    r.add_argument('--source-rev')
    r.add_argument('--cargo-lock')
    r.add_argument('--auth-env', help='env var NAME holding a bearer value, if the tunnel needs one')
    r.add_argument('--request-ms', type=int, default=REQUEST_MS)
    c = sub.add_parser('check')
    c.add_argument('directory')
    args = parser.parse_args()
    return run(args) if args.mode == 'run' else check_dir(args.directory)


if __name__ == '__main__':
    sys.exit(main())
