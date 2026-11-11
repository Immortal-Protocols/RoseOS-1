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
import mustang_lane
import mustang_paths
import mustang_vendor_consumer

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
    mustang_lane.reject_legacy()
    # The hosted describe guard checks the same static route before starting the
    # builder. Keep this in the runtime path too, before creating a fresh attempt.
    integration.check_route(ROOT/'os/build-only/mustang-updater-route.json')
    integration.other_builder_paths()
    cfg = json.loads((ROOT/'os/build-only/mustang-host.json').read_text())
    app_input_allocation(cfg)
    return cfg


def app_input_allocation(cfg):
    """Check the committed producer binding on the hosted guard; this reads no host input."""
    ref = cfg.get('appInputAllocation')
    if ref is None:
        return None
    path = Path(ref)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('app input allocation must be a repository-relative record')
    allocation = json.loads((ROOT/path).read_text())
    build = allocation.get('build', '')
    if (allocation.get('schema') != 'roseos.latest-allocation.v1' or allocation.get('device') != 'frankel'
            or not re.fullmatch(r'[1-9][0-9]{9}', str(build))
            or allocation.get('tag') != f'frankel-{build}'
            or not re.fullmatch(r'[0-9a-f]{40}', str(allocation.get('sourceSha', '')))):
        raise ValueError('Frankel app-input producer identity differs')
    base = f'/home/pixelbuild/rose-actions/frankel-{build}/release'
    if cfg['appsDir'] != base+'/signed-apps' or cfg['policy'] != base+'/background-policy.json':
        raise ValueError('signed apps and policy must bind the same recorded Frankel producer')
    return allocation


def app_input_admission(cfg, candidate, directory):
    """Record actual copies only after the existing APK/receipt/signer/policy admission passed."""
    allocation = app_input_allocation(cfg)
    if allocation is None:
        return None
    if sorted(candidate['apps']) != sorted(allocation['apps']):
        raise ValueError('admitted app versions differ from the bound Frankel allocation')
    directory = Path(directory)
    apps = []
    for package, version in candidate['apps']:
        apk = directory/'signed-apps'/f'{package}.apk'
        receipt = directory/'signed-apps'/f'{package}.receipt.json'
        apps.append({'package':package, 'versionCode':version, 'sha256':integration.sha256_file(apk),
            'receiptSha256':integration.sha256_file(receipt)})
    return {'schema':'roseos.mustang-app-input-admission.v1', 'status':'verified-by-signed-input-admission',
        'observedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'producerRun':allocation['run'], 'producerBuild':allocation['build'],
        'producerSource':allocation['sourceSha'], 'producerCandidate':allocation['candidateSha'],
        'allocationRecordSha256':integration.sha256_file(ROOT/cfg['appInputAllocation']),
        'appsDir':cfg['appsDir'], 'policy':cfg['policy'], 'apps':apps,
        'policySha256':integration.sha256_file(directory/'background-policy.json'),
        'apkSignaturesAndReceiptsVerified':True, 'policyMatchesActualApps':True,
        'compiledAppAndPolicyVerification':'omitted per original sprint; this records input admission only'}


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


def require_bootstrap_observation(result):
    """Surface only the collector's bounded fixed codes, including pre-claim failures."""
    receipt=result.get('receipt',{})
    if result.get('receiptWritten') is True and receipt.get('success') is True:
        return
    safe=lambda value,fallback:value if isinstance(value,str) and re.fullmatch(r'[a-z][a-z0-9-]{0,95}',value) else fallback
    failure=safe(receipt.get('failure'),'collector-refused')
    stage=safe(receipt.get('stage'),'unknown')
    report=dict(stage='bootstrap-source',status='refused',collectorStage=stage,
        failureCode=failure,receiptWritten=result.get('receiptWritten') is True)
    path=receipt.get('failurePath')
    if isinstance(path,str) and re.fullmatch(r'[A-Za-z0-9_+./-]{1,256}',path) and not path.startswith('/') \
            and not any(p in ('','.','..') for p in path.split('/')):report['failurePath']=path
    print(json.dumps(report),flush=True)
    raise ValueError('full-run bootstrap source observation refused ('+stage+': '+failure+'); no source copy, allocation or Make')


def run(run_id, attempt, lane=None, runtime=None, inputs_dir=None):
    if not all(re.fullmatch(r'[1-9][0-9]{0,19}', x) for x in (run_id, attempt)):
        raise ValueError('run id and attempt must be positive numeric identifiers')
    source = head()
    if lane is None:
        cfg = config()
    else:
        paths = mustang_paths.layout(lane)
        if runtime is None or inputs_dir is None:
            raise ValueError('dedicated Mustang needs admitted runtime bindings and app inputs')
        consumed = runtime.get('consumedBuilds')
        if runtime.get('clockNumbering') is not None:
            import mustang_clock
            mustang_clock.policy(lane,runtime,ROOT)
        elif not isinstance(consumed, list) or not consumed or any(
                not isinstance(build, str) or not re.fullmatch(r'[1-9][0-9]{9}', build) for build in consumed):
            raise ValueError('prior failed Mustang allocation state remains unresolved; no new build number')
        admission = None  # verified after native source/tool bytes are observed
        cfg = dict(runtime['tools'],host=lane['vm']['instance'],jobs=runtime['jobs'],
                   appsDir=str(Path(inputs_dir)/'signed-apps'),policy=str(Path(inputs_dir)/'background-policy.json'),
                   signingBlocker='separate same-run signing stage after unsigned proof')
    engine = module('mustang_adapter', 'mustang-build-only.py').engine()
    if lane is None:
        recipe = engine.load(ROOT/'os/build-only/mustang.json')
        pipeline = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
        lock = pipeline['host']['lockPath']
        home = Path(pipeline['host']['sourceRoot']).parent
        base = home/'rose-actions'
    else:
        recipe = mustang_paths.compiler_recipe(engine,lane,ROOT)
        if runtime.get('clockNumbering') is not None:
            mustang_clock.bind_compiler(engine,lane,runtime,root=ROOT)
        engine.SOURCE_PROVENANCE=dict(engine.SOURCE_PROVENANCE,
            integration='copy of the admitted pristine pinned Mustang base on its dedicated VM; no other-device seed')
        lock, home, base = str(paths['lockPath']), paths['home'], paths['storageRoot']
    bootstrap=None
    if lane is not None:
        import mustang_bootstrap_collector as bootstrap_collector
        print(json.dumps({'stage':'bootstrap-source','status':'entered','runId':run_id}),flush=True)
        bootstrap=bootstrap_collector.collect(lane,runtime,os.environ,
            bootstrap_collector.proof.deps.read_event(os.environ),root=ROOT,full_run=True)
        require_bootstrap_observation(bootstrap)
        bootstrap_collector.require_full_result(bootstrap,lane,os.environ,
            bootstrap_collector.proof.deps.read_event(os.environ))
        verifier = module('mustang_app_input_verify','app-inputs-verify.py')
        admission = verifier.verify(ROOT/runtime['appInputsRecord'], inputs_dir, runtime['tools'])
        if runtime.get('frozenAppInputs') and os.environ.get('GITHUB_RUN_NUMBER') == str(runtime['frozenAppInputs']['workflowRunNumber']):
            admission['sourceChoice'] = runtime['frozenAppInputs']
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
    if lane is not None:
        report.update(builder=dict(lane['vm']),workflow=lane['workflow'],appInputAdmission=admission,
                      sourceEvidence=runtime['sourceEvidence'],allocationState=runtime['allocationState'])
        report['signing']={'status':'same-run-signing-pending-unsigned-proof','reason':cfg['signingBlocker']}
        report['stages']['bootstrap-source']={'receipt':bootstrap['receiptFile'],'result':bootstrap['receipt']}
    env = {k:v for k,v in os.environ.items() if k not in SCRUBBED and not k.startswith('GIT_')}
    current = 'source'
    cache_state = None

    def progress(name,status):
        event={'device':'mustang','runId':run_id,'runAttempt':attempt,'source':source,'stage':name,'status':status,
               'at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        print(json.dumps(event),flush=True)
        try:
            with open(run_root/'progress.jsonl','a') as log:
                log.write(json.dumps(event)+'\n')
        except OSError:
            pass  # stdout still reports the phase when the data disk cannot write.

    def stage(name, allocation=None, inputs=None):
        nonlocal current
        current = name
        progress(name,'entered')
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
        progress(name,'complete')
        return result

    try:
        if lane is not None:
            import mustang_compiler_cache as compiler_cache
            import mustang_existing_lock
            current = 'compiler-cache'
            with mustang_existing_lock.shared(paths['lockPath'], expected=paths['lockPath']):
                cache_state = compiler_cache.prepare(lane, runtime, root=ROOT)
            compiler_cache.bind_engine(engine, cache_state, root=ROOT)
            if cache_state is not None:
                report['compilerCache'] = cache_state
                print(json.dumps({'stage': current, 'status': 'enabled', **cache_state}), flush=True)
        for name in ('source', 'git-meta'):
            stage(name)
        if lane is not None:
            current='source-copy'
            progress(current,'entered')
            import mustang_bootstrap_proof as source_proof
            import mustang_existing_lock as source_lock
            with source_lock.shared(paths['lockPath'],expected=paths['lockPath']):
                verified=source_proof.verify_copy(paths['sourceRoot'],src,
                    report['stages']['source']['result']['selectionRecord'],bootstrap['receipt']['sourceCandidate'])
            verified.update(run=bootstrap['receipt']['run'],builder=lane['vm'],
                nativeReceiptSha256=bootstrap['receiptFile']['sha256'],sourceRoot=str(src))
            directory=receipts/current;directory.mkdir(mode=0o700)
            release_receipts.write_once(directory/'completed.json',verified)
            report['stages'][current]={'receipt':str(directory/'completed.json'),'result':verified}
            progress(current,'complete')
            current = 'vendor-inputs'
            progress(current,'entered')
            reused=mustang_vendor_consumer.consume(lane,runtime,src,
                dict(runId=run_id,runAttempt=attempt,source=source,
                     workflow='Immortal-Protocols/RoseOS-1/'+lane['workflow']+'@refs/heads/main'),root=ROOT)
            directory=receipts/current;directory.mkdir(mode=0o700)
            release_receipts.write_once(directory/'completed.json',reused)
            report['stages'][current]={'receipt':str(directory/'completed.json'),'result':reused}
            progress(current,'complete')
        else:
            current = 'vendor-sprint-patch'
            patch = ROOT/'os/build-only/mustang-inputs/adevtool-no-input-hash.patch'
            outcome, _ = call(engine, ['git','-C',str(src/'vendor/adevtool'),'apply',str(patch)],120,ROOT,env)
            report['stages'][current] = {'result':outcome, 'patch':str(patch),
                'downloadedInputHashVerification':'omitted in helper and pinned adevtool unpacker'}
            for name in ('download', 'vendor'):
                stage(name)
        stage('lunch')
        current = 'allocate'
        progress(current,'entered')
        alloc = module('mustang_allocate', 'mustang-allocate.py')
        if lane is not None and runtime.get('clockNumbering') is not None:
            allocation = mustang_clock.reserve(lane,runtime,os.environ,root=ROOT)
        else:
            allocation = alloc.reserve(str(home), run_id, attempt, source, **(
                {'lane':lane,'consumed':runtime['consumedBuilds']} if lane is not None else {}))
        release_receipts.write_once(run_root/'allocation.json', allocation)
        report['allocation'] = allocation
        work, reconcile = Path(allocation['workDir']), Path(allocation['integrationDir'])
        work.mkdir(mode=0o700)
        current = 'seed'
        copy = report['stages']['source']['result']['selectionRecord']
        # Only the established Frankel receipt chain may explain the production snapshot.
        # Prior Mustang integrations are eligible on future explicit attempts; Blazer/Rango never are.
        if lane is None:
            seed = module('mustang_seed', 'mustang-reconcile-seed.py')
            with alloc.allocator.shared_lock(lock):
                seed.seed(pipeline['host']['integrate']['reconcile']['receiptDir'], str(src), run_id,
                    str(reconcile), copy['path'], copy['sha256'], str(base))
        else:
            # The admitted base is a pristine pinned checkout on this VM. The
            # existing host-reconcile HEAD path handles its first integration;
            # no Frankel receipt/tree is copied or adopted as a Mustang seed.
            report['stages']['seed']={'pristineBase':runtime['sourceEvidence'],
                                     'selectionRecord':copy,'priorReconcileSeed':False}
        current = 'assemble'
        progress(current,'entered')
        tools = {k:cfg[k] for k in ('aapt','apksigner')}
        bundle = module('mustang_bundle', 'mustang-candidate-bundle.py')
        candidate = bundle.assemble(cfg['appsDir'], cfg['policy'], allocation['build'], source, tools, work/'bundle')
        if lane is None:
            admitted = app_input_admission(cfg, candidate, work/'bundle')
        else:
            record = json.loads((ROOT/runtime['appInputsRecord']).read_text())
            if sorted(candidate['apps']) != sorted([a['package'],a['versionCode']] for a in record['apps']):
                raise ValueError('dedicated app bundle differs from admitted producer versions')
            admitted = dict(admission,builder=dict(lane['vm']),policySha256=integration.sha256_file(work/'bundle/background-policy.json'))
        if admitted is not None:
            release_receipts.write_once(run_root/'app-input-admission.json', admitted)
            report['stages']['assemble'] = admitted
        current = 'admit'
        integration.admit(work/'bundle', ROOT/'os/build-only/mustang-updater-route.json', work/'inputs', tools)
        inputs = work/'inputs/local-inputs.json'
        current = 'render'
        _, command = integration.render(inputs, src, lock, reconcile, work, work/'reconcile-config.json')
        current = 'candidate'
        call(engine, ['git','-C',str(ROOT),'worktree','add','--detach',str(work/f'product-{source[:12]}'),source],300,ROOT,env)
        current = 'integrate'
        progress(current,'entered')
        outcome, _ = call(engine, command, 3600, ROOT, env)
        report['stages']['integrate'] = {'source':source, 'result':outcome, 'inputs':str(inputs),
            'receiptDir':str(reconcile), 'compiledAppAndPolicyVerification':'omitted'}
        stage('compile', allocation, inputs)
        report['unsignedCompileComplete'] = True
        report['nextStage'] = ('same-run collection proof and signing with the established identity; no bootstrap' if lane is not None
            else 'separate mustang-delivery run after successful Mustang bootstrap; this unsigned run performs no signing')
        return 0
    except BaseException as error:
        report['failedStage'] = current
        progress(current,'failed')
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        if cache_state is not None:
            try:
                compiler_cache.finish(cache_state, root=ROOT)
                release_receipts.write_once(run_root/'compiler-cache.json', cache_state)
            except Exception as error:
                # Reporting must not hide Make's result or interrupt owned cleanup.
                report['compilerCache']['statisticsError'] = type(error).__name__
        release_receipts.write_once(run_root/'candidate-receipt.json', report)
        print(json.dumps({'device':'mustang', 'source':source, 'receipt':str(run_root/'candidate-receipt.json'),
            'failedStage':report.get('failedStage'), 'unsignedCompileComplete':report.get('unsignedCompileComplete',False),
            'signed':False, **({'signingBlocked':True} if lane is None else {'sameRunSigningPending':True})}))


def collect_reports(run_id, attempt, out, lane=None):
    if lane is None:
        pipeline = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
        base = Path(pipeline['host']['sourceRoot']).parent/'rose-actions'/f'mustang-prep-{run_id}-{attempt}'
    else:
        base = mustang_paths.attempt(lane,'prep',run_id,attempt)
    out = Path(out)
    out.mkdir(mode=0o700)
    # Fixed receipt names only: no source, keys, APK payloads or factory inputs are uploaded.
    for name in ('candidate-receipt.json','allocation.json','app-input-admission.json','progress.jsonl','compiler-cache.json'):
        path = base/name
        if path.is_file() and not path.is_symlink():
            shutil.copyfile(path, out/name)
    if lane is not None:
        import mustang_bootstrap_collector as collector
        import mustang_hosttools as readers
        storage=mustang_paths.layout(lane)['storageRoot']
        path=storage/f'mustang-bootstrap-observation-{run_id}-{attempt}'/collector.RESULT
        if path.exists() and not path.is_symlink():
            raw=readers.read_regular(str(storage),str(path),1 << 20)
            document=json.loads(raw);observed_run=document.get('run',{})
            if document.get('schema')!=collector.SCHEMA or observed_run.get('runId')!=run_id \
                    or observed_run.get('runAttempt')!=attempt or document.get('workflowJob')!='builder':
                raise ValueError('bootstrap report identity differs')
            with (out/collector.RESULT).open('xb') as destination:destination.write(raw)
    for name in ('source','git-meta','download','vendor','vendor-inputs','lunch','compile'):
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
    mustang_lane.reject_legacy()
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
