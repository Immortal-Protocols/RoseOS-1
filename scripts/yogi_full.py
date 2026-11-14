#!/usr/bin/env python3
"""One automatic Yogi identity -> private baseline A -> newer public OTA B.

All native steps run on the existing dedicated Yogi builder. Existing Yogi
admission, allocation, integration, signing, verification and publication are
reused. Public same-run bindings travel separately from the four OTA assets;
private keys, fastboot images, compiler output and caches stay on the Yogi disk.
"""
import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import mustang_route
import release_receipts
import yogi_ota as yogi

SCHEMA = 'roseos.yogi-full-run.v1'
PROVIDER = ('projects/1072799993443/locations/global/workloadIdentityPools/'
            'roseos-yogi/providers/github-signer')
NATIVE_MINUTES = 660
STOP_SECONDS = 12 * 3600
BINDINGS = {'runtime.json', 'signing-bootstrap-receipt.json', 'bootstrap-sign-receipt.json',
            'bootstrap-install-receipt.json'}


def current(env=os.environ):
    identity = yogi.run_identity(env)
    if env.get('GITHUB_REF') != 'refs/heads/main' or identity['runAttempt'] != '1' \
            or identity['workflowRef'] != yogi.WORKFLOW+'@refs/heads/main':
        raise yogi.Refused('full Yogi delivery runs once from main')
    return identity


def queue_watch(env=os.environ, opener=urllib.request.urlopen, sleep=time.sleep, clock=time.monotonic):
    """Cancel only this run if its paid VM starts but its native job cannot start.

    Successful metadata reads can be polled. Authentication/transport failures
    are surfaced immediately, without retry or a different access route.
    """
    identity = current(env)
    base = 'https://api.github.com/repos/Immortal-Protocols/RoseOS-1/actions/runs/'+identity['runId']
    headers = {'Authorization': 'Bearer '+env['GH_TOKEN'], 'Accept': 'application/vnd.github+json'}
    deadline = clock()+300
    while True:
        request = urllib.request.Request(base+'/attempts/1/jobs?per_page=100', headers=headers)
        with opener(request, timeout=30) as response:
            value = json.load(response)
        if value.get('total_count', 101) > 100:
            raise yogi.Refused('Yogi job index incomplete; no cancellation guessed')
        jobs = [job for job in value.get('jobs', []) if job.get('name') == 'full']
        if len(jobs) > 1:
            raise yogi.Refused('ambiguous native Yogi job; no cancellation guessed')
        if jobs and jobs[0].get('status') in ('in_progress', 'completed'):
            print('Yogi native job started; queue watchdog complete')
            return
        if clock() >= deadline:
            with opener(urllib.request.Request(base+'/cancel', data=b'', headers=headers, method='POST'), timeout=30):
                pass
            raise yogi.Refused('Yogi native job did not start within 5 minutes; this run cancellation requested once')
        sleep(10)


def guard(cfg, env=os.environ):
    current(env)
    full = cfg.get('fullRun') or {}
    if full.get('enabled') is not True or full.get('nativeMinutes') != NATIVE_MINUTES \
            or full.get('stopSeconds') != STOP_SECONDS or not full.get('setupEvidence'):
        raise yogi.Refused('full Yogi setup is not bound (runner, STOP limit and signer access)')
    if cfg['builder'].get('status') != 'ready' or yogi.evidence_missing(cfg):
        raise yogi.Refused('exclusive replacement Yogi runner evidence is incomplete')
    runner = cfg['builder']['evidence']['runner']
    if runner.get('protectedWorkflow') != yogi.WORKFLOW+'@refs/heads/main':
        raise yogi.Refused('Yogi signer runner must be restricted to its main workflow')
    if cfg['builder'].get('compilerCache') != yogi.yogi_compile.cache.SETTING:
        raise yogi.Refused('full Yogi run requires the bounded local compiler cache')
    if cfg.get('publicationEnabled') is not True or env.get('PUBLISH') != 'true':
        raise yogi.Refused('full Yogi attempt must retain its publication path')
    if env.get('GCP_WORKLOAD_IDENTITY_PROVIDER') != PROVIDER \
            or env.get('GCP_SIGNER_SERVICE_ACCOUNT') != yogi.SIGNER:
        raise yogi.Refused('full Yogi signer/provider binding differs')
    for problem in (yogi.profile_problem(), yogi.access_problem(cfg)):
        if problem:
            raise yogi.Refused(problem)
    access_expiry = datetime.datetime.fromisoformat(cfg['signing']['access']['expiresAt'].replace('Z', '+00:00'))
    if (access_expiry-datetime.datetime.now(datetime.timezone.utc)).total_seconds() < STOP_SECONDS:
        raise yogi.Refused('Yogi signing access has less than a full run remaining')
    record = yogi.app_record(cfg)
    if mustang_route.bound_app_freshness(record, yogi.ROOT) != []:
        raise yogi.Refused('Yogi app export is stale or its source history is unavailable')
    expires = datetime.datetime.fromisoformat(cfg['appInputs']['artifactExpiresAt'].replace('Z', '+00:00'))
    if (expires-datetime.datetime.now(datetime.timezone.utc)).total_seconds() < STOP_SECONDS:
        raise yogi.Refused('app export has less than one full Yogi run of lifetime remaining')


def write(path, value):
    """Replace only this run's private state, atomically; receipts remain write-once."""
    path = Path(path)
    if path.is_symlink():
        raise yogi.Refused('Yogi full-run state is a link')
    fd, name = tempfile.mkstemp(prefix='.yogi-state-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def load_state(path, env=os.environ):
    path = Path(path)
    yogi.regular_dir(path.parent, 'full-run state directory')
    if path.is_symlink():
        raise yogi.Refused('full-run state is a link')
    state = yogi.read(path)
    if state.get('schema') != SCHEMA or state.get('run') != current(env):
        raise yogi.Refused('full-run state is not this run/source/attempt')
    return state


def identity_config(cfg, receipt):
    bound = copy.deepcopy(cfg)
    bound['signing']['bootstrapReceipt'] = str(Path(receipt).resolve())
    yogi.identities(bound)
    return bound


def initialize(cfg, state_path, env=os.environ):
    guard(cfg, env)
    yogi.identity_refusal(cfg, env)
    yogi.preflight(cfg)
    mount = subprocess.run(['findmnt', '--json', '--target', cfg['builder']['root'], '--output', 'UUID,SOURCE,TARGET'],
                           capture_output=True, text=True, timeout=30, check=True)
    rows = json.loads(mount.stdout)['filesystems']
    expected = cfg['builder']['evidence']['storage']['mounts']
    if len(rows) != 1 or rows[0]['uuid'] != expected['filesystemUuid'] \
            or rows[0]['target'] != expected['mountPoint'] \
            or os.stat(cfg['builder']['root']).st_dev != os.stat(cfg['builder']['attempts']).st_dev:
        raise yogi.Refused('Yogi filesystem UUID/mount differs from the restored disk')
    state_path = Path(state_path)
    if state_path.exists() or state_path.is_symlink():
        raise yogi.Refused('full run already initialized; no duplicate attempt')
    state = {'schema': SCHEMA, 'run': current(env), 'phase': 'initialized', 'needsBootstrap': True}
    persisted = Path(cfg['builder']['bootstrapState'])/'signing-bootstrap-receipt.json'
    committed = yogi.ROOT/cfg['signing']['bootstrapReceipt']
    journal = Path(cfg['builder']['bootstrapState'])/'yogi-v1.json'
    if persisted.exists():
        yogi.regular_dir(persisted.parent, 'bootstrap state directory')
        if persisted.is_symlink() or journal.is_symlink():
            raise yogi.Refused('bootstrap receipt/journal is a link')
        bound = identity_config(cfg, persisted)
        pins, versions = yogi.identities(bound)
        previous = yogi.read(journal)
        if previous.get('status') != 'complete' or previous.get('profile') != yogi.PROFILE \
                or previous.get('publicIdentity') != pins or previous.get('committed') != versions:
            raise yogi.Refused('persisted identity does not match the completed bootstrap journal')
        state.update(needsBootstrap=False, keyReceipt=str(persisted))
    elif committed.is_file():
        identity_config(cfg, committed)
        state.update(needsBootstrap=False, keyReceipt=str(committed))
    elif journal.exists():
        raise yogi.Refused('bootstrap journal exists without a complete receipt; recovery required, no regeneration')
    else:
        problem = yogi.key_access_problem()
        if problem:
            raise yogi.Refused(problem)
    release_receipts.write_once(state_path, state)
    output(env, needs_bootstrap=str(state['needsBootstrap']).lower())
    return state


def output(env, **values):
    if env.get('GITHUB_OUTPUT'):
        with open(env['GITHUB_OUTPUT'], 'a') as out:
            for key, value in values.items():
                if '\n' in str(value):
                    raise yogi.Refused('invalid step output')
                out.write(f'{key}={value}\n')


def accept_keys(cfg, state, receipt):
    if state['needsBootstrap']:
        bound = identity_config(cfg, receipt)
        value = yogi.read(receipt)
        if value.get('recipeSha') != state['run']['source']:
            raise yogi.Refused('new key receipt is not this source')
        destination = Path(cfg['builder']['bootstrapState'])/'signing-bootstrap-receipt.json'
        release_receipts.write_once(destination, value)
        state['keyReceipt'] = str(destination)
        bound = identity_config(cfg, destination)
    else:
        bound = identity_config(cfg, state['keyReceipt'])
    state['keyReceiptSha256'] = yogi.digest(bound['signing']['bootstrapReceipt'])
    return bound


def runtime_config(cfg, state):
    bound = identity_config(cfg, state['keyReceipt'])
    if yogi.digest(state['keyReceipt']) != state['keyReceiptSha256']:
        raise yogi.Refused('key receipt changed within this run')
    if state.get('baseline'):
        bound['bootstrap'] = state['baseline']
    return bound


def compile_one(cfg, state, role, apps, env=os.environ):
    # Recheck space before each fresh source/OUT_DIR; never delete the previous
    # candidate or the lab tree to make a new one fit.
    yogi.preflight(cfg)
    with yogi.builder_lock(cfg):
        allocation = yogi.allocate(cfg, state['run']['runId'], state['run']['runAttempt'], state['run']['source'])
    build = allocation['build']
    state[role] = allocation
    base = state['a']['build'] if role == 'b' else None
    yogi.prepare(cfg, build, base_attempt=base)
    yogi.integrate(cfg, build, apps)
    yogi.compile_build(cfg, build)
    output(env, build=build)


def baseline(cfg, state):
    build = state['a']['build']
    install = yogi.install_image(cfg, build)
    state['baseline'] = {'status': 'signed', 'build': build, 'timestamp': state['a']['timestamp'],
                         'source': state['run']['source'], 'installEvidence': None,
                         'basis': 'Signed private baseline A in this run; no phone installation is claimed.'}
    state['baselineInstallSha256'] = yogi.digest(yogi.attempt_dir(cfg, build)/'install/install-receipt.json')
    return install


def export_bindings(cfg, state, destination):
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    a = yogi.attempt_dir(cfg, state['a']['build'])
    sources = {'signing-bootstrap-receipt.json': Path(state['keyReceipt']),
               'bootstrap-sign-receipt.json': a/'sign/yogi-sign-receipt.json',
               'bootstrap-install-receipt.json': a/'install/install-receipt.json'}
    for name, source in sources.items():
        shutil.copyfile(source, destination/name)
    record = {'schema': SCHEMA, 'run': state['run'], 'baseline': state['baseline'],
              'a': state['a'], 'b': state['b'],
              'appRecord': cfg['appInputs']['record'], 'appRecordSha256': yogi.digest(yogi.ROOT/cfg['appInputs']['record']),
              'files': {name: yogi.digest(destination/name) for name in sources}}
    release_receipts.write_once(destination/'runtime.json', record)


def publisher_config(cfg, directory, env=os.environ):
    """Consume only this workflow's binding artifact, never arbitrary config overrides."""
    directory = Path(directory)
    yogi.regular_dir(directory, 'public Yogi bindings')
    if {p.name for p in directory.iterdir()} != BINDINGS or any(p.is_symlink() or not p.is_file() for p in directory.iterdir()):
        raise yogi.Refused('public binding inventory differs')
    record = yogi.read(directory/'runtime.json')
    if record.get('schema') != SCHEMA or record.get('run') != current(env) \
            or set(record.get('files', {})) != BINDINGS-{'runtime.json'} \
            or any(yogi.digest(directory/name) != sha for name, sha in record['files'].items()):
        raise yogi.Refused('public binding source/run/hash differs')
    if record.get('appRecord') != cfg['appInputs']['record'] \
            or record.get('appRecordSha256') != yogi.digest(yogi.ROOT/cfg['appInputs']['record']):
        raise yogi.Refused('public binding app export differs')
    bound = identity_config(cfg, directory/'signing-bootstrap-receipt.json')
    pins, versions = yogi.identities(bound)
    signed = yogi.read(directory/'bootstrap-sign-receipt.json')
    install = yogi.read(directory/'bootstrap-install-receipt.json')
    for role in ('a', 'b'):
        allocation = record[role]
        if allocation.get('schema') != 'roseos.yogi-allocation.v1' or allocation.get('device') != yogi.DEVICE \
                or allocation.get('source') != record['run']['source'] \
                or allocation.get('runId') != record['run']['runId'] or allocation.get('runAttempt') != '1' \
                or not yogi.BUILD.fullmatch(str(allocation.get('build', ''))) \
                or not str(allocation.get('timestamp', '')).isdecimal():
            raise yogi.Refused('baseline/OTA allocation binding differs')
    a, b = record['a'], record['b']
    baseline_value = record.get('baseline', {})
    if baseline_value.get('status') != 'signed' or baseline_value.get('build') != a['build'] \
            or baseline_value.get('timestamp') != a['timestamp'] or baseline_value.get('source') != a['source'] \
            or int(b['build']) <= int(a['build']) or int(b['timestamp']) <= int(a['timestamp']):
        raise yogi.Refused('OTA B must be strictly newer than authenticated baseline A')
    if signed.get('schema') != 'roseos.yogi-sign.v1' or signed.get('device') != yogi.DEVICE \
            or signed.get('status') != 'signed' or signed.get('stage') != 'bootstrap' \
            or signed.get('build') != a['build'] or signed.get('timestamp') != a['timestamp'] \
            or signed.get('candidateSource') != a['source'] or signed.get('recipeSource') != a['source'] \
            or signed.get('secrets') != {k: {'name': yogi.SECRETS[k], 'version': v} for k, v in versions.items()}:
        raise yogi.Refused('baseline signing receipt differs')
    if install.get('schema') != 'roseos.yogi-install-image.v1' or install.get('build') != a['build'] \
            or install.get('avbPublicKeySha256') != pins['avbPublicKeySha256']:
        raise yogi.Refused('baseline install receipt differs')
    bound['bootstrap'] = baseline_value
    return bound, record


TRANSITIONS = {'accept-keys': ('initialized', 'keys'), 'compile-a': ('keys', 'a-compiled'),
               'sign-a': ('a-compiled', 'a-signed'), 'baseline': ('a-signed', 'baseline'),
               'compile-b': ('baseline', 'b-compiled'), 'sign-b': ('b-compiled', 'b-signed'),
               'stage': ('b-signed', 'staged')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('guard', 'queue-watch', 'initialize', *TRANSITIONS, 'verify', 'publish', 'report'))
    parser.add_argument('--state', type=Path)
    parser.add_argument('--apps', type=Path)
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--bindings', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    try:
        cfg = yogi.config()
        if args.action == 'guard':
            guard(cfg)
        elif args.action == 'queue-watch':
            queue_watch()
        elif args.action == 'initialize':
            initialize(cfg, args.state)
        elif args.action in ('verify', 'publish'):
            cfg, record = publisher_config(cfg, args.bindings)
            staged = yogi.verify_staged(args.out, cfg)
            if staged['recipeSource'] != record['run']['source'] or staged['candidateSource'] != record['run']['source'] \
                    or staged['runId'] != record['run']['runId'] or staged['build'] != record['b']['build'] \
                    or staged['timestamp'] != record['b']['timestamp']:
                raise yogi.Refused('staged OTA is not this full run B')
            if args.action == 'publish':
                client = yogi.publisher.GitHubReleases(yogi.REPOSITORY, os.environ.get('PIXEL_OTA_TOKEN', ''))
                print(json.dumps(yogi.publish(client, args.out, cfg, args.out.parent/'yogi-publication')))
        else:
            state = load_state(args.state)
            if args.action == 'report':
                args.out.mkdir(mode=0o700, exist_ok=True)
                shutil.copyfile(args.state, args.out/'full-run.json')
                key_receipt = (Path(state['keyReceipt']) if state.get('keyReceipt') else
                               Path(os.environ['RUNNER_TEMP'])/'yogi-bootstrap/signing-bootstrap-receipt.json')
                if key_receipt.is_file() and not key_receipt.is_symlink():
                    shutil.copyfile(key_receipt, args.out/'signing-bootstrap-receipt.json')
                # Small receipts only. Never upload native output, private install
                # images, compile.log, key workspace or source trees as diagnostics.
                for role in ('a', 'b'):
                    if state.get(role):
                        native = yogi.attempt_dir(cfg, state[role]['build'])
                        for name in ('allocation.json', 'compile.json', 'sign/yogi-sign-receipt.json'):
                            path = native/name
                            if path.is_file() and not path.is_symlink():
                                shutil.copyfile(path, args.out/(role+'-'+path.name))
                return 0
            before, after = TRANSITIONS[args.action]
            if state['phase'] != before:
                raise yogi.Refused(f'{args.action} is invalid after {state["phase"]}; no repeated operation')
            if args.action == 'accept-keys':
                accept_keys(cfg, state, args.receipt)
            else:
                cfg = runtime_config(cfg, state)
                if args.action.startswith('compile-'):
                    # Persist the allocation immediately, even when prepare/Make fails.
                    try:
                        compile_one(cfg, state, args.action[-1], args.apps)
                    finally:
                        write(args.state, state)
                elif args.action.startswith('sign-'):
                    role = args.action[-1]
                    yogi.sign(cfg, state[role]['build'], 'bootstrap' if role == 'a' else 'ota')
                elif args.action == 'baseline':
                    baseline(cfg, state)
                elif args.action == 'stage':
                    yogi.stage(cfg, state['b']['build'], args.out)
                    export_bindings(cfg, state, args.bindings)
            state['phase'] = after
            write(args.state, state)
    except (yogi.Refused, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError,
            release_receipts.ReceiptExists) as error:
        print(f'Yogi full run stopped: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
