#!/usr/bin/env python3
"""Mustang source/preparation/integration/unsigned-compile route on the original builder.

No signing or credential path is selected by this unsigned route. Reuses the
shared lock, allocator, production-copy mechanism, host-reconcile and compiler
without consuming Blazer/Rango artifacts.
A failed command is never retried or reset by this unsigned route. The delivery
route separately admits one recorded make=0 output-lookup correction, preserving
the original failed receipts and requiring fresh complete output collection.
"""
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import mustang_integration as integration
import release_receipts

PLAN = 'https://app.notion.com/p/40d7c646c81b81198e60cb76ca2ea603'
SCRUBBED = ('JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS', '_JAVA_OPTIONS', 'CLASSPATH', 'JAVA_HOME', 'LD_PRELOAD',
            'LD_LIBRARY_PATH', 'LD_AUDIT')


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def head():
    return subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()


def config():
    # The hosted describe guard checks the same static route before starting the
    # builder. Keep this in the runtime path too, before creating a fresh attempt.
    integration.check_route(ROOT/'os/build-only/mustang-updater-route.json')
    integration.other_builder_paths()
    return json.loads((ROOT/'os/build-only/mustang-host.json').read_text())


def omissions():
    return json.loads((ROOT/'os/build-only/mustang-sprint.json').read_text())['omittedChecks']


def call(engine, argv, seconds, cwd, env):
    result = engine.supervise([str(a) for a in argv], str(cwd), env, seconds,
        {'sampleSeconds':5, 'killGraceSeconds':30, 'boundBytes':1 << 60,
         'reserveBytes':0, 'checkReserve':False}, capture=True)
    output = result.pop('stdout', '')
    if result['returncode'] != 0 or result['stopped']:
        # Child output may contain operator paths; preserve locally, never dump credentials or raw failures.
        raise RuntimeError(f'{Path(str(argv[0])).name} failed: exit {result["returncode"]}, stopped={result["stopped"]}')
    return result, output


def run(run_id, attempt):
    if not all(re.fullmatch(r'[1-9][0-9]{0,19}', x) for x in (run_id, attempt)):
        raise ValueError('run id and attempt must be positive numeric identifiers')
    cfg, source = config(), head()
    engine = module('mustang_adapter', 'mustang-build-only.py').engine()
    recipe = engine.load(ROOT/'os/build-only/mustang.json')
    pipeline = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
    lock = pipeline['host']['lockPath']
    home = Path(pipeline['host']['sourceRoot']).parent
    base = home/'rose-actions'
    run_root = base/f'mustang-prep-{run_id}-{attempt}'
    # Basic overwrite/access protections remain; no independent safe-path test suite is run.
    if not base.is_dir() or engine.symlink_in(run_root) or os.path.lexists(run_root):
        raise ValueError('fresh Mustang attempt directory unavailable; no previous attempt is reused')
    run_root.mkdir(mode=0o700)
    receipts, src, downloads = run_root/'receipts', run_root/'source', run_root/'downloads'
    receipts.mkdir()
    report = {'schema':'roseos.mustang-candidate-run.v1', 'device':'mustang', 'model':'Pixel 10 Pro XL',
        'source':source, 'runId':run_id, 'runAttempt':attempt, 'plan':PLAN,
        'root':str(run_root), 'label':recipe['sprint']['candidateLabel'], 'stages':{},
        'omittedChecks':omissions(), 'signed':False, 'published':False, 'phoneValidated':False,
        'signing':{'status':'separate-bootstrap-and-delivery-required', 'reason':cfg['signingBlocker']}}
    env = {k:v for k,v in os.environ.items() if k not in SCRUBBED and not k.startswith('GIT_')}
    current = 'source'

    def stage(name, allocation=None, inputs=None):
        nonlocal current
        current = name
        build = allocation['build'] if allocation else None
        epoch = str(int(datetime.datetime.strptime(allocation['reservedAt'], '%Y-%m-%dT%H:%M:%SZ')
            .replace(tzinfo=datetime.timezone.utc).timestamp())) if allocation else None
        op = engine.operator(cfg['host'], str(src), cfg['jobs'] if name=='compile' else None,
            lock, str(downloads), allocation['integrationDir'] if allocation else None,
            recipe['compile']['maxJobs'], str(inputs) if inputs else None,
            cfg['node'], build, epoch)
        status = engine.run(recipe, name, op, receipts/name)
        path = receipts/name/'completed.json'
        result = json.loads(path.read_text()) if path.is_file() else {'ok':False, 'preflight':str(receipts/name/'preflight.json')}
        report['stages'][name] = {'exitCode':status, 'receipt':str(path), 'result':result}
        if status:
            raise RuntimeError(f'{name} failed; inspect its recorded receipt; no retry')
        return result

    try:
        for name in ('source', 'git-meta'):
            stage(name)
        current = 'vendor-sprint-patch'
        patch = ROOT/'os/build-only/mustang-inputs/adevtool-no-input-hash.patch'
        # The pinned adevtool also verifies the factory hash inside unpackFactoryImage.
        # Apply the deliberate omission only to this new Mustang tree, never production.
        outcome, _ = call(engine, ['git','-C',str(src/'vendor/adevtool'),'apply',str(patch)],120,ROOT,env)
        report['stages'][current] = {'result':outcome, 'patch':str(patch),
            'downloadedInputHashVerification':'omitted in helper and pinned adevtool unpacker'}
        for name in ('download', 'vendor', 'lunch'):
            stage(name)
        current = 'allocate'
        alloc = module('mustang_allocate', 'mustang-allocate.py')
        allocation = alloc.reserve(str(home), run_id, attempt, source)
        release_receipts.write_once(run_root/'allocation.json', allocation)
        report['allocation'] = allocation
        work, reconcile = Path(allocation['workDir']), Path(allocation['integrationDir'])
        work.mkdir(mode=0o700)
        current = 'seed'
        copy = report['stages']['source']['result']['selectionRecord']
        seed = module('mustang_seed', 'mustang-reconcile-seed.py')
        # Only the established Frankel receipt chain may explain the production snapshot.
        # Prior Mustang integrations are eligible on future explicit attempts; Blazer/Rango never are.
        with alloc.allocator.shared_lock(lock):
            seed.seed(pipeline['host']['integrate']['reconcile']['receiptDir'], str(src), run_id,
                str(reconcile), copy['path'], copy['sha256'], str(base))
        current = 'assemble'
        tools = {k:cfg[k] for k in ('aapt','apksigner')}
        bundle = module('mustang_bundle', 'mustang-candidate-bundle.py')
        bundle.assemble(cfg['appsDir'], cfg['policy'], allocation['build'], source, tools, work/'bundle')
        current = 'admit'
        integration.admit(work/'bundle', ROOT/'os/build-only/mustang-updater-route.json', work/'inputs', tools)
        inputs = work/'inputs/local-inputs.json'
        current = 'render'
        _, command = integration.render(inputs, src, lock, reconcile, work, work/'reconcile-config.json')
        current = 'candidate'
        call(engine, ['git','-C',str(ROOT),'worktree','add','--detach',str(work/f'product-{source[:12]}'),source],300,ROOT,env)
        current = 'integrate'
        outcome, _ = call(engine, command, 3600, ROOT, env)
        report['stages']['integrate'] = {'source':source, 'result':outcome, 'inputs':str(inputs),
            'receiptDir':str(reconcile), 'compiledAppAndPolicyVerification':'omitted'}
        stage('compile', allocation, inputs)
        report['unsignedCompileComplete'] = True
        report['nextStage'] = 'separate mustang-delivery run after successful Mustang bootstrap; this unsigned run performs no signing'
        return 0
    except BaseException as error:
        report['failedStage'] = current
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        release_receipts.write_once(run_root/'candidate-receipt.json', report)
        print(json.dumps({'device':'mustang', 'source':source, 'receipt':str(run_root/'candidate-receipt.json'),
            'failedStage':report.get('failedStage'), 'unsignedCompileComplete':report.get('unsignedCompileComplete',False),
            'signed':False, 'signingBlocked':True}))


def collect_reports(run_id, attempt, out):
    pipeline = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
    base = Path(pipeline['host']['sourceRoot']).parent/'rose-actions'/f'mustang-prep-{run_id}-{attempt}'
    out = Path(out)
    out.mkdir(mode=0o700)
    # Fixed receipt names only: no source, keys, APK payloads or factory inputs are uploaded.
    for name in ('candidate-receipt.json','allocation.json'):
        path = base/name
        if path.is_file() and not path.is_symlink():
            shutil.copyfile(path, out/name)
    for name in ('source','git-meta','download','vendor','lunch','compile'):
        for filename in ('preflight.json','started.json','completed.json'):
            path = base/'receipts'/name/filename
            if path.is_file() and not path.is_symlink():
                shutil.copyfile(path, out/f'{name}-{filename}')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('describe','run','report'))
    p.add_argument('--run-id')
    p.add_argument('--run-attempt')
    p.add_argument('--out')
    a = p.parse_args()
    if a.action=='describe':
        print(json.dumps({'device':'mustang', 'source':head(), 'host':config(), 'omittedChecks':omissions(),
            'stages':['source','git-meta','download','vendor','lunch','allocate','seed','integrate','compile'],
            'signing':config()['signingBlocker']},indent=2))
        return 0
    if not a.run_id or not a.run_attempt:
        p.error('run/report require --run-id and --run-attempt')
    if a.action=='report':
        if not a.out:
            p.error('report requires --out')
        collect_reports(a.run_id, a.run_attempt, a.out)
        return 0
    return run(a.run_id, a.run_attempt)


if __name__=='__main__':
    sys.exit(main())
