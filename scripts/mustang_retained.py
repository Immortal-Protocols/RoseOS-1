"""One exact dedicated producer, authenticated separately from its new delivery.

No Make, allocation, journal repair, login or provisioning. Network callers use
the existing fixed artifact reader; a denial exits without retry or fallback.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
import zipfile

import mustang_bootstrap_collector as collector
import mustang_clock
import mustang_controller as controller
import mustang_existing_lock as locks
import mustang_hosttools as readers
import mustang_paths
import mustang_route

ROOT=Path(__file__).resolve().parents[1]
RUN='43486889593'
SOURCE='8103ca2fbd29a1eaca9998be53219c0f42c93020'
ARTIFACT=dict(id=14077115670,name='mustang-dedicated-receipts-43486889593-1',sizeBytes=25576,
    sha256='58ac9c8dbf8b8c70ad79d2fe516078e1b9ed1bb3c31c8a09344fc77f437451d0')
RUN_NUMBER=51
CREATED_AT='2026-11-13T18:53:00Z'
SCHEMA='roseos.mustang-retained-producer.v1'
FILES=('candidate-receipt.json','compile-completed.json','allocation.json')
WORKFLOW='.github/workflows/mustang-ota-dedicated.yml'
ALLOWED=frozenset(('scripts/mustang_retained.py','scripts/mustang-dedicated.py',
    'scripts/mustang-sign-package.py','scripts/mustang_collection.py','scripts/mustang_hosted_sign.py',
    'scripts/mustang_clock.py','scripts/mustang_ota.py','scripts/mustang_bootstrap_collector.py','scripts/mustang_route.py',
    WORKFLOW,'os/build-only/mustang-full-run-42664204892.json'))


def sha(raw):return hashlib.sha256(raw).hexdigest()


def selected(env):
    value=env.get('MUSTANG_RETAINED_COMPILE_RUN','')
    if value not in ('',RUN):raise ValueError('only the exact retained Mustang producer is supported')
    return value==RUN


def compatible(source,root=ROOT,git=None):
    if not re.fullmatch(r'[0-9a-f]{40}',str(source)) or source==SOURCE:
        raise ValueError('retained delivery needs a distinct reviewed source')
    git=git or (lambda args:mustang_route.git_local(root,args))
    if git(['merge-base','--is-ancestor',SOURCE,source]).returncode:
        raise ValueError('retained producer is not an ancestor of this delivery')
    diff=git(['diff','--name-only',SOURCE,source,'--'])
    if diff.returncode or len(diff.stdout)>128 << 10:
        raise ValueError('retained source compatibility unavailable')
    for path in diff.stdout.splitlines():
        if path not in ALLOWED and not path.startswith(('scripts/tests/','docs/')):
            raise ValueError('retained delivery changes OS, app, input, allocation or tool source: '+path)


def proof_identity(value):
    if not isinstance(value,dict) or set(value)!={'schema','runId','runAttempt','source','artifact','receiptSha256'} \
            or value.get('schema')!=SCHEMA or value.get('runId')!=RUN or value.get('runAttempt')!='1' \
            or value.get('source')!=SOURCE or value.get('artifact')!=ARTIFACT \
            or set(value.get('receiptSha256',{}))!=set(FILES) \
            or any(not re.fullmatch(r'[0-9a-f]{64}',str(v)) for v in value['receiptSha256'].values()):
        raise ValueError('retained producer proof differs')
    return value


def compile_context(bundle):
    if bundle.get('artifactProducer') is not None:
        import mustang_artifact_delivery
        result=mustang_artifact_delivery.proof_identity(bundle['artifactProducer'])
        if str(bundle.get('runId'))==result[0] or str(bundle.get('runAttempt'))!='1' or bundle.get('sourceSha')==result[2]:
            raise ValueError('artifact producer was relabelled as delivery')
        return result
    proof=bundle.get('retainedProducer')
    if proof is None:return str(bundle['runId']),str(bundle['runAttempt']),bundle['sourceSha']
    proof_identity(proof)
    if str(bundle['runId'])==RUN or str(bundle['runAttempt'])!='1' or bundle.get('sourceSha')==SOURCE:
        raise ValueError('retained producer was relabelled as the delivery')
    return RUN,'1',SOURCE


def archive(raw):
    if len(raw)!=ARTIFACT['sizeBytes'] or sha(raw)!=ARTIFACT['sha256']:
        raise ValueError('retained receipt archive digest differs; no retry')
    with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
        rows=zipped.infolist();names=[r.filename for r in rows]
        if not 3<=len(rows)<=64 or len(set(names))!=len(names) or any(
                Path(n).name!=n or n in ('','.','..') or '\\' in n for n in names) \
                or any(r.is_dir() or r.flag_bits&1 or (r.external_attr>>16)&0o170000 not in (0,0o100000)
                    or r.file_size>4 << 20 for r in rows) or sum(r.file_size for r in rows)>16 << 20:
            raise ValueError('retained receipt ZIP inventory differs')
        if not set(FILES)<=set(names) or 'compile-collection.json' in names:
            raise ValueError('retained receipt cohort incomplete or already collected')
        raw_files={name:zipped.read(name) for name in FILES}
    documents={name:json.loads(data) for name,data in raw_files.items()}
    candidate=documents[FILES[0]];completed=documents[FILES[1]];allocation=documents[FILES[2]]
    if candidate.get('schema')!='roseos.mustang-candidate-run.v1' or candidate.get('device')!='mustang' \
            or candidate.get('source')!=SOURCE or str(candidate.get('runId'))!=RUN or str(candidate.get('runAttempt'))!='1' \
            or candidate.get('unsignedCompileComplete') is not True or candidate.get('failedStage') \
            or candidate.get('allocation')!=allocation or candidate['stages']['compile']['result']!=completed \
            or completed.get('returncode')!=0 or completed.get('stopped') or completed.get('ok') is not True \
            or completed.get('outputs',{}).get('verdict')!='complete':
        raise ValueError('not the exact successful-compile/precollection capacity failure')
    ctx=allocation.get('observed',{}).get('clock',{}).get('context',{})
    if ctx.get('createdAt')!=CREATED_AT or ctx.get('runNumber')!=RUN_NUMBER \
            or ctx.get('workflowId')!=408460629 or ctx.get('runId')!=RUN or ctx.get('source')!=SOURCE:
        raise ValueError('retained allocation differs from the authenticated producer clock')
    proof=dict(schema=SCHEMA,runId=RUN,runAttempt='1',source=SOURCE,artifact=dict(ARTIFACT),
        receiptSha256={name:sha(data) for name,data in raw_files.items()})
    return proof,documents,raw_files


def authenticate(env,root=ROOT,*,api=None,read=None,git=None):
    """Read and authenticate the pinned original run plus exact immutable archive."""
    if not selected(env):raise ValueError('retained delivery was not selected')
    compatible(env['GITHUB_SHA'],root,git)
    api=api or collector.github_reader(dict(runId=RUN),env.get('GH_TOKEN',''))
    base='/repos/Immortal-Protocols/RoseOS-1/actions/runs/'+RUN
    actual=api(base)
    if actual.get('id')!=int(RUN) or actual.get('run_attempt')!=1 or actual.get('run_number')!=RUN_NUMBER \
            or actual.get('head_sha')!=SOURCE or actual.get('head_branch')!='main' or actual.get('path')!=WORKFLOW \
            or actual.get('workflow_id')!=408460629 or actual.get('event')!='workflow_dispatch' \
            or actual.get('created_at')!=CREATED_AT \
            or (actual.get('status'),actual.get('conclusion'))!=('completed','failure') \
            or any((actual.get(k) or {}).get('id')!=1428996648 for k in ('repository','head_repository')):
        raise ValueError('retained original Actions identity differs')
    jobs=collector.listing(api(base+'/attempts/1/jobs?per_page=100'),'jobs')
    for name,conclusion in (('guard','success'),('controller','success'),('enroll','success'),
                            ('builder','failure'),('cleanup','success'),('signer','skipped'),('publisher','skipped')):
        rows=[j for j in jobs if j.get('name')==name]
        if len(rows)!=1 or (rows[0].get('status'),rows[0].get('conclusion'))!=('completed',conclusion) \
                or rows[0].get('head_sha')!=SOURCE or rows[0].get('run_id')!=int(RUN) or rows[0].get('run_attempt',1)!=1:
            raise ValueError('retained original Actions jobs differ')
        if name=='builder':
            job=rows[0];steps=job.get('steps',[])
            if job.get('id')!=132098482375 or job.get('runner_group_id')!=5 \
                    or any(len([s for s in steps if s.get('name')==step and s.get('conclusion')==result])!=1
                    for step,result in (('Prepare, allocate, integrate and compile once on Mustang disk','success'),
                        ('Require unsigned raised-edge proof before the hosted signing bundle','failure'))):
                raise ValueError('retained compile/collection step identity differs')
    if read is None:
        import mustang_ota
        read=mustang_ota.module('mustang_retained_artifact_reader','mustang-dedicated.py').app_artifact_reader(
            ARTIFACT['id'],env.get('GH_TOKEN',''))
    meta=json.loads(read('',2 << 20))
    if meta.get('id')!=ARTIFACT['id'] or meta.get('name')!=ARTIFACT['name'] \
            or meta.get('size_in_bytes')!=ARTIFACT['sizeBytes'] or meta.get('digest')!='sha256:'+ARTIFACT['sha256'] \
            or meta.get('expired') is not False or (meta.get('workflow_run') or {}).get('id')!=int(RUN) \
            or (meta.get('workflow_run') or {}).get('head_sha')!=SOURCE:
        raise ValueError('retained artifact metadata differs')
    result=archive(read('/zip',ARTIFACT['sizeBytes']))
    cfg=json.loads((Path(root)/'os/build-only/mustang-runtime.json').read_text())
    lane=json.loads((Path(root)/'os/build-only/mustang-lane.json').read_text())
    original_builder=next(j for j in jobs if j.get('name')=='builder')
    if original_builder.get('runner_name')!='mustang-'+RUN \
            or lane['runnerLabel'] not in original_builder.get('labels',[]) \
            or any(j.get('conclusion')!='skipped' for j in jobs
                if j.get('name') in ('source','hostdeps','lock-admin','factory','vendor','otatools')):
        raise ValueError('retained original runner/preparation scope differs')
    record=json.loads((Path(root)/cfg['appInputsRecord']).read_text())
    candidate=result[1]['candidate-receipt.json'];completed=result[1]['compile-completed.json']
    admitted=candidate.get('appInputAdmission',{})
    if candidate.get('builder')!=lane['vm'] or candidate.get('workflow')!=WORKFLOW:
        raise ValueError('retained original builder/workflow differs')
    stamp=str(int(mustang_clock.utc(candidate['allocation']['reservedAt']).timestamp()))
    mustang_clock.require_allocation(candidate['allocation'],lane,cfg,root=root,
        env=dict(GITHUB_RUN_ID=RUN,GITHUB_RUN_ATTEMPT='1',GITHUB_SHA=SOURCE),timestamp=stamp)
    if completed.get('buildIdentity')!={'buildNumber':candidate['allocation']['build'],'buildDatetime':stamp} \
            or completed['outputs'].get('actualBuildIdentity')!={'device':'mustang','build':candidate['allocation']['build'],'timestamp':stamp}:
        raise ValueError('retained compiled device/build/timestamp differs')
    if admitted.get('verdict')!='ADMITTED' or admitted.get('producer')!=record['producer']['tag'] \
            or admitted.get('export')!=record['export'] or admitted.get('apps')!=record['apps'] \
            or admitted.get('toolsRechecked') is not True:
        raise ValueError('retained signed-app export binding differs from current inputs')
    return result


def guest_receipt(lane,cfg,env,event,facts,root=ROOT):
    run=collector.full_invocation(lane,env,event,retained=True)
    paths=mustang_paths.layout(lane)
    if env.get('GITHUB_WORKSPACE')!=str(root) or any(Path(root)==p or p in Path(root).parents
            for p in (paths['sourceRoot'],paths['storageRoot'])):
        raise ValueError('retained collection checkout location differs')
    if facts.get('instanceId')!=lane['vm']['instanceId'] or facts.get('filesystemUuid')!=cfg['dataFilesystemUuid'] \
            or facts.get('mount')!=cfg.get('dataMount') or facts.get('euid',0)<=0 \
            or facts.get('availableBytes',0)<=0 or facts.get('availableInodes',0)<=0:
        raise ValueError('retained current guest identity differs')
    value=dict(schema='roseos.mustang-retained-guest.v1',run=run,builder=lane['vm'],guest=facts,
        controllerOwnershipSha256=sha(base64.b64decode(env['MUSTANG_OWNER_B64'],validate=True)),
        observationKind='native-read-only',checkoutReobserved=False,makeRerun=False,allocationIssued=False)
    require_guest(value,lane,run,cfg,root)
    return value


def journal(candidate,lane,cfg,root=ROOT):
    a=candidate['allocation'];context=a['observed']['clock']['context']
    env=dict(GITHUB_RUN_ID=RUN,GITHUB_RUN_ATTEMPT='1',GITHUB_SHA=SOURCE,
        MUSTANG_CLOCK_CONTEXT=base64.b64encode(mustang_clock.canonical(context)).decode())
    timestamp=str(int(mustang_clock.utc(a['reservedAt']).timestamp()))
    paths=mustang_paths.layout(lane);source=mustang_paths.attempt(lane,'prep',RUN,'1')/'source'
    op=dict(buildNumber=a['build'],buildDatetime=timestamp,sourceRoot=source,
        integrationDir=a['integrationDir'],inputsReceipt=Path(a['workDir'])/'inputs/local-inputs.json')
    problems,_facts=mustang_clock.compile_identity(op,time.time,lane=lane,cfg=cfg,root=root,env=env)
    if problems:raise ValueError('retained current journal/inputs binding refused')
    for path in (source,Path(a['integrationDir']),Path(a['workDir'])):
        if paths['storageRoot'] not in path.parents or any(p.is_symlink() for p in (path,*path.parents)):
            raise ValueError('retained source or allocation path traverses a link')


def collect(lane,cfg,env,event,facts,root=ROOT):
    """Fresh delivery directories only; no original file or durable state writes."""
    proof,documents,raw_files=authenticate(env,root)
    observation=guest_receipt(lane,cfg,env,event,facts,root)
    candidate=documents['candidate-receipt.json'];journal(candidate,lane,cfg,root)
    import mustang_ota
    signing=mustang_ota.module('mustang_retained_collection','mustang-sign-package.py')
    current=observation['run'];paths=mustang_paths.layout(lane)
    destination=paths['storageRoot']/('mustang-retained-'+current['runId']+'-1')
    if os.path.lexists(destination):raise ValueError('retained delivery attempt exists; no resume')
    with locks.shared(paths['lockPath'],expected=paths['lockPath']):
        # Compare the real retained original receipts with the authenticated archive.
        original=mustang_paths.attempt(lane,'prep',RUN,'1')/'delivery-inputs'
        for name in ('candidate-receipt.json','compile-completed.json','allocation.json'):
            if readers.read_regular(str(paths['storageRoot']),str(original/name),4 << 20)!=raw_files[name]:
                raise ValueError('retained original receipt bytes changed')
        destination.mkdir(mode=0o700)
        for name,data in raw_files.items():
            with (destination/name).open('xb') as out:out.write(data);out.flush();os.fsync(out.fileno())
    old={k:os.environ.get(k) for k in ('EXPECTED_COMPILE_RUN','EXPECTED_COMPILE_SOURCE','EXPECTED_COMPILE_RECOVERY')}
    try:
        os.environ.update(EXPECTED_COMPILE_RUN=RUN,EXPECTED_COMPILE_SOURCE=SOURCE,EXPECTED_COMPILE_RECOVERY='false')
        return signing.collect(destination/'candidate-receipt.json',current['runId'],'1',lane=lane,
            retained=dict(producer=proof,guest=observation))
    finally:
        for k,v in old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v


def require_guest(value,lane,run,cfg,root=ROOT):
    if value.get('schema')!='roseos.mustang-retained-guest.v1' or value.get('run')!=run \
            or value.get('builder')!=lane['vm'] or value.get('observationKind')!='native-read-only' \
            or any(value.get(k) is not False for k in ('checkoutReobserved','makeRerun','allocationIssued')):
        raise ValueError('retained current guest proof differs')
    if not re.fullmatch(r'[0-9a-f]{64}',str(value.get('controllerOwnershipSha256',''))):
        raise ValueError('retained current ownership digest differs')
    facts=value.get('guest',{})
    if set(facts)!={'instanceId','filesystemUuid','mount','availableBytes','availableInodes','totalInodes','euid'} \
            or any(type(facts.get(k)) is not int for k in ('euid','availableBytes','availableInodes','totalInodes')) \
            or facts.get('instanceId')!=lane['vm']['instanceId'] or facts.get('filesystemUuid')!=cfg['dataFilesystemUuid'] \
            or facts.get('mount')!=cfg.get('dataMount') or facts.get('euid',0)<=0 \
            or facts.get('availableBytes',0)<=0 or facts.get('availableInodes',0)<=0:
        raise ValueError('retained current guest facts differ')
    mustang_route.evaluate_guest(facts,lane,cfg,root,phase="collection")
    return value
