#!/usr/bin/env python3
"""Deliver the exact completed run-46 bundle without a VM, Make or allocation.

Only existing finalized artifacts are consumed. Original compile source/build
remain explicit; this delivery has a separate run/source and the same signer.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
RUN='43074045726'
SOURCE='091ea6c486f1421bf06fde571fa4c5a1feda02b6'
WORKFLOW='.github/workflows/mustang-ota-dedicated.yml'
RECORD='os/build-only/mustang-artifact-bundle-43074045726.json'
DOCUMENT_SHA='939882456f53de5991fdbbce3529181927b675e54d337658cd4496ecfbdb647a'
ARTIFACTS={
    'bundle':dict(id=13890272339,name='mustang-signing-bundle-43074045726-1',sizeBytes=4169,
                  sha256='a1de048194f78cc4e53b8fa8affa467af60eda3859723a35bce3801e88470842'),
    'inputs':dict(id=13889918815,name='mustang-signing-inputs-43074045726-1',sizeBytes=5067975918,
                  sha256='f7a17ed88a5e5846ccd4452f411e3b36a110981c63bd7305e9bb66147fb5afe0')}
PROOF=dict(schema='roseos.mustang-artifact-producer.v1',runId=RUN,runAttempt='1',source=SOURCE,
           bundleDocumentSha256=DOCUMENT_SHA,artifacts=ARTIFACTS)
# These are executed or interpreted during delivery, unlike newer app/server code.
IMMUTABLE=('os/pinned-manifest.xml','script/generate-release.sh','scripts/host-sign.py',
    'scripts/host_secrets.py','scripts/host-decrypt-keys.py','scripts/edge_artifact.py',
    'os/release-pipeline/frankel.json','os/ota/mustang.json','os/signing-bootstrap/',
    'os/build-only/mustang-hosted-tool-closure.json','os/build-only/mustang-hosted-sign-script.json',
    'os/build-only/mustang-otatools-42531345133.json','os/build-only/mustang-otatools-inspection-42531345133.json')


def canonical(value):return (json.dumps(value,indent=2,sort_keys=True)+'\n').encode()


def proof_identity(value):
    if value!=PROOF:raise ValueError('artifact producer proof differs')
    return RUN,'1',SOURCE


def original(root=ROOT):
    path=Path(root)/RECORD
    if path.is_symlink():raise ValueError('artifact bundle record is linked')
    value=json.loads(path.read_text())
    if hashlib.sha256(canonical(value)).hexdigest()!=DOCUMENT_SHA:
        raise ValueError('pinned artifact bundle document differs')
    if (value.get('runId'),str(value.get('runAttempt')),value.get('sourceSha'),value.get('build'))!=(
            RUN,'1',SOURCE,'2611111800'):
        raise ValueError('artifact compile identity differs')
    return value


def compatible(source,root=ROOT,git=None):
    if not re.fullmatch(r'[0-9a-f]{40}',str(source)) or source==SOURCE:
        raise ValueError('artifact recovery needs its reviewed delivery source')
    def execute(args):return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True)
    git=git or execute
    if git(['merge-base','--is-ancestor',SOURCE,source]).returncode:
        raise ValueError('artifact producer is not an ancestor of delivery')
    diff=git(['diff','--name-only',SOURCE,source,'--',*IMMUTABLE])
    if diff.returncode or diff.stdout.strip():
        raise ValueError('artifact signing/tool/identity inputs changed; no recovery')


def rebound(run,source,root=ROOT):
    value=original(root)
    if not re.fullmatch(r'[1-9][0-9]{0,19}',str(run)) or str(run)==RUN:
        raise ValueError('distinct artifact delivery run required')
    import mustang_edge
    old=mustang_edge.binding(SOURCE,SOURCE,value['build'],value['timestamp'],RUN,'1',RUN,'1')
    mustang_edge.require(value['unsignedRaisedEdge'],'unsigned',old,value['unsigned'])
    value.update(runId=str(run),runAttempt='1',sourceSha=source,artifactProducer=copy.deepcopy(PROOF))
    # Rebind the authenticated hash-bound scan; this does not claim a new scan.
    value['unsignedRaisedEdge']['binding']=mustang_edge.binding(SOURCE,source,value['build'],value['timestamp'],
                                                              str(run),'1',RUN,'1')
    return value


def require_bundle(bundle,root=ROOT):
    proof_identity(bundle.get('artifactProducer'))
    if bundle!=rebound(bundle.get('runId'),bundle.get('sourceSha'),root):
        raise ValueError('artifact delivery bundle differs from the pinned producer')
    return True


def scope(env,event):
    inputs=event.get('inputs') if isinstance(event,dict) else None
    disabled=lambda v:v is None or v is False or v=='false'
    flags={'publish','dry_run','source_only','hostdeps_only','lock_admin_only','factory_only','vendor_only','otatools_only'}
    if not isinstance(inputs,dict) or set(inputs)-(flags|{'retained_compile_run'}) \
            or inputs.get('retained_compile_run')!=RUN or env.get('MUSTANG_RETAINED_COMPILE_RUN')!=RUN \
            or any(not disabled(inputs.get(k)) for k in flags-{'publish'}) \
            or inputs.get('publish') not in (True,'true'):
        raise ValueError('artifact recovery must retain the complete signing/publication scope')
    if env.get('GITHUB_EVENT_NAME')!='workflow_dispatch' or env.get('GITHUB_REF')!='refs/heads/main' \
            or env.get('GITHUB_REPOSITORY')!='Immortal-Protocols/RoseOS-1' \
            or env.get('GITHUB_REPOSITORY_ID')!='1428996648' or env.get('GITHUB_RUN_ATTEMPT')!='1' \
            or env.get('GITHUB_WORKFLOW_REF')!='Immortal-Protocols/RoseOS-1/'+WORKFLOW+'@refs/heads/main' \
            or env.get('GITHUB_WORKFLOW_SHA')!=env.get('GITHUB_SHA'):
        raise ValueError('artifact recovery workflow context differs')
    return env['GITHUB_RUN_ID'],env['GITHUB_SHA']


def authenticate(env,event,*,api=None,root=ROOT,git=None,cleanup_ready=True):
    """Authenticate both runs using metadata only; no payload or signer admission."""
    import mustang_bootstrap_collector as collector
    import mustang_hosted_sign as hosted
    run,source=scope(env,event);compatible(source,root,git)
    value=rebound(run,source,root);hosted.require_bundle_identity(value,env,root)
    phase=env.get('GITHUB_JOB')
    if phase not in ('guard','artifact-admission','signer'):raise ValueError('artifact recovery job differs')
    hosted.require_delivery_capacity(root=root)
    if phase in ('guard','signer') and env.get('GCP_SIGNER_SERVICE_ACCOUNT')!=hosted.SIGNER:
        raise ValueError('artifact recovery signer identity differs')
    producer_api=api or collector.github_reader(dict(runId=RUN),env.get('GH_TOKEN',''),include_artifacts=True)
    base='/repos/Immortal-Protocols/RoseOS-1/actions/runs/'
    producer=producer_api(base+RUN)
    if (producer.get('id'),producer.get('run_attempt'),producer.get('run_number'),producer.get('head_sha'),
            producer.get('path'),producer.get('workflow_id'),producer.get('created_at'),producer.get('status'),
            producer.get('conclusion'))!=(int(RUN),1,46,SOURCE,WORKFLOW,408460629,
                                        '2026-11-11T18:03:16Z','completed','failure') \
            or producer.get('head_branch')!='main' or producer.get('event')!='workflow_dispatch' \
            or any((producer.get(k) or {}).get('id')!=1428996648 for k in ('repository','head_repository')):
        raise ValueError('artifact producer is not the exact completed failed upload run')
    jobs=collector.listing(producer_api(base+RUN+'/attempts/1/jobs?per_page=100'),'jobs')
    for name,conclusion in (('guard','success'),('controller','success'),('enroll','success'),
            ('builder','failure'),('cleanup','success'),('signer','skipped'),('publisher','skipped')):
        rows=[j for j in jobs if j.get('name')==name]
        if len(rows)!=1:raise ValueError('artifact original job missing or ambiguous: '+name)
        job=rows[0]
        pending_cleanup=name=='cleanup' and (job.get('status'),job.get('conclusion'))==('queued',None)
        if (job.get('run_id'),job.get('head_sha'),job.get('run_attempt',1))!=(int(RUN),SOURCE,1) \
                or not pending_cleanup and (job.get('status'),job.get('conclusion'))!=('completed',conclusion):
            raise ValueError('artifact original job outcome differs: '+name)
        if name=='cleanup':original_cleanup_pending=pending_cleanup
        if name=='builder':
            steps=job.get('steps',[])
            if job.get('id')!=130709837750 or job.get('runner_group_id')!=5 \
                    or job.get('runner_name')!='mustang-'+RUN:
                raise ValueError('artifact original builder identity differs')
            for step in ('Prepare, allocate, integrate and compile once on Mustang disk',
                         'Require unsigned raised-edge proof before the hosted signing bundle',
                         'Bundle the same-run unsigned target-files, otatools, and JDK bytes',
                         'Metadata gate only; no payload byte verification'):
                if len([s for s in steps if s.get('name')==step and s.get('conclusion')=='success'])!=1:
                    raise ValueError('artifact original native compile/collection did not pass')
            failed=[s['name'] for s in steps if s.get('conclusion')=='failure']
            if 'Upload the bundled inputs only after the metadata gate' not in failed \
                    or set(failed)-{'Upload the bundled inputs only after the metadata gate',
                                   'Run actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'}:
                raise ValueError('artifact producer failed outside the upload boundary')
    rows=collector.listing(producer_api(base+RUN+'/artifacts?per_page=100'),'artifacts')
    for pin in ARTIFACTS.values():
        found=[r for r in rows if r.get('id')==pin['id'] or r.get('name')==pin['name']]
        if len(found)!=1:raise ValueError('artifact recovery input missing or ambiguous')
        row=found[0];owner=row.get('workflow_run') or {}
        if (row.get('id'),row.get('name'),row.get('size_in_bytes'),row.get('digest'),row.get('expired'))!=(
                pin['id'],pin['name'],pin['sizeBytes'],'sha256:'+pin['sha256'],False) \
                or (owner.get('id'),owner.get('head_sha'),owner.get('repository_id'),owner.get('head_repository_id'))!=(
                    int(RUN),SOURCE,1428996648,1428996648):
            raise ValueError('artifact recovery immutable input identity differs')
    current_api=api or collector.github_reader(dict(runId=run),env.get('GH_TOKEN',''))
    current=current_api(base+run)
    if (current.get('id'),current.get('run_attempt'),current.get('head_sha'),current.get('path'),
            current.get('workflow_id'),current.get('status'),current.get('conclusion'))!=(
                int(run),1,source,WORKFLOW,408460629,'in_progress',None) \
            or current.get('head_branch')!='main' or current.get('event')!='workflow_dispatch' \
            or type(current.get('run_number')) is not int or current['run_number']<=46 \
            or current.get('created_at','')<=producer['created_at'] \
            or any((current.get(k) or {}).get('id')!=1428996648 for k in ('repository','head_repository')):
        raise ValueError('artifact delivery Actions run differs')
    current_jobs=collector.listing(current_api(base+run+'/attempts/1/jobs?per_page=100'),'jobs')
    expected={'guard':('in_progress',None)} if phase=='guard' else {
        'guard':('completed','success'),'artifact-admission':('in_progress',None)}
    if phase=='signer':expected.update({'artifact-admission':('completed','success'),'signer':('in_progress',None)})
    for name,outcome in expected.items():
        found=[j for j in current_jobs if j.get('name')==name]
        if len(found)!=1 or (found[0].get('status'),found[0].get('conclusion'))!=outcome \
                or found[0].get('run_id')!=int(run) or found[0].get('head_sha')!=source \
                or found[0].get('run_attempt',1)!=1:
            raise ValueError('artifact delivery job identity differs')
        if name=='signer':
            route=hosted.larger_route(hosted.load_capacity(root=root))
            if route is None or found[0].get('runner_group_id')!=route['groupId'] \
                    or route['label'] not in found[0].get('labels',[]):
                raise ValueError('artifact delivery signer route differs')
    if original_cleanup_pending and cleanup_ready:
        admission=[j for j in current_jobs if j.get('name')=='artifact-admission']
        required=('Stop only the authenticated original Mustang owner',
                  'Clear original JIT metadata after owned STOP','Remove only the original offline group5 runner')
        if len(admission)!=1 or any(len([s for s in admission[0].get('steps',[])
                if s.get('name')==name and s.get('status')=='completed' and s.get('conclusion')=='success'])!=1
                for name in required):
            raise ValueError('original queued cleanup must complete through this authenticated recovery')
    native={'controller','enroll','builder','queue-watch','source','hostdeps','lock-admin','factory','vendor','otatools'}
    if any(j.get('name') in native and j.get('conclusion')!='skipped' for j in current_jobs):
        raise ValueError('artifact recovery cannot start a native builder or preparation')
    return dict(artifactProducerVerified=True,actionsRunIdentityVerified=True,producerRunId=RUN,
                payloadBytesVerified=False,signerAdmitted=False)


def publisher_context(lane,cfg,env,event,*,root=ROOT,publish=False):
    """Artifact publication consumes verified same-delivery bytes, not native inputs."""
    import mustang_controller, mustang_lane, mustang_ota
    run,source=scope(env,event)
    mustang_lane.validate(lane,root)
    mustang_controller.invocation(lane,env)
    compatible(source,root)
    if env.get('GITHUB_JOB')!='publisher' or env.get('MUSTANG_PUBLISH')!='true' or not publish \
            or cfg.get('schema')!='roseos.mustang-dedicated-runtime.v1' or cfg.get('status')!='bound' \
            or cfg.get('publication',{}).get('enabled') is not True:
        raise ValueError('artifact publisher scope or runtime differs')
    path=Path(str(cfg['publication'].get('authorityRecord','')))
    if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!='os' \
            or (Path(root)/path).is_symlink() or not (Path(root)/path).is_file() \
            or not (Path(root)/path).resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('artifact publication authority record differs')
    authority=json.loads((Path(root)/path).read_text())
    if authority.get('approved') is not True or authority.get('device')!='mustang' \
            or authority.get('workflow')!=WORKFLOW or not authority.get('approvalRef') \
            or authority.get('automatic') is not False:
        raise ValueError('artifact public publication remains disabled')
    mustang_ota.guard(mustang_ota.config(),access=True,publication=True)
    return cfg


def recover_owner(env,event,out,*,api=None,root=ROOT,git=None):
    authenticate(env,event,api=api,root=root,git=git,cleanup_ready=False)
    import mustang_controller, mustang_lane
    value=original(root);native=value['fullRunSource']['nativeReceipt']
    owner=dict(schema=mustang_controller.SCHEMA,run=native['run'],
               owner=dict(vm=mustang_lane.configuration()['vm'],startedByThisOperation=True))
    raw=(json.dumps(owner,sort_keys=True)+'\n').encode()
    if hashlib.sha256(raw).hexdigest()!=native['controllerOwnershipSha256']:
        raise ValueError('original controller ownership bytes differ')
    with Path(out).open('xb') as f:f.write(raw)
    return owner


def stop_original(owner_path,out,*,root=ROOT,execute=subprocess.run,adapter=None):
    import datetime
    import mustang_controller, mustang_lane
    lane=mustang_lane.configuration();native=original(root)['fullRunSource']['nativeReceipt']
    raw=Path(owner_path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=native['controllerOwnershipSha256']:
        raise ValueError('original controller ownership bytes differ')
    mustang_controller.read_ownership(owner_path,lane,native['run'])
    vm=lane['vm']
    result=execute(['gcloud','compute','instances','describe',vm['instance'],'--project='+vm['project'],
        '--zone='+vm['zone'],'--format=json(id,name,status,lastStartTimestamp)'],capture_output=True,text=True,timeout=30)
    if result.returncode:raise ValueError('original VM state unavailable; no stop')
    body=json.loads(result.stdout)
    if (body.get('id'),body.get('name'))!=(vm['instanceId'],vm['instance']) \
            or datetime.datetime.fromisoformat(body['lastStartTimestamp'])!=datetime.datetime.fromisoformat(
                '2026-11-11T18:03:51.948+00:00'):
        raise ValueError('original VM numeric identity or start changed; no stop')
    return mustang_controller.stop_with_receipt(adapter or mustang_controller.common_controller(),lane,
        native['run'],owner_path,out,execute=execute)


def prepare(env,event,out,*,api=None,root=ROOT,git=None):
    authenticate(env,event,api=api,root=root,git=git)
    import mustang_hosted_sign as hosted
    folder=Path(out);folder.mkdir(mode=0o700,parents=True,exist_ok=False)
    value=rebound(env['GITHUB_RUN_ID'],env['GITHUB_SHA'],root)
    target=folder/'mustang-signing-bundle.json';hosted.write_json(target,value)
    digest=hosted.sha256_file(target)
    if env['GITHUB_JOB']=='signer' and digest!=env.get('EXPECTED_FULL_BUNDLE_SHA256'):
        raise ValueError('artifact admission and signer bundle hashes differ')
    hosted.step_output('bundle_sha256',digest)
    return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('guard','prepare','owner','stop'))
    parser.add_argument('--out');parser.add_argument('--owner');args=parser.parse_args(argv)
    try:
        env=os.environ;event=json.loads(Path(env['GITHUB_EVENT_PATH']).read_text())
        if args.action=='guard':print(json.dumps(authenticate(env,event,cleanup_ready=False)))
        else:
            if not args.out:raise ValueError('artifact recovery destination required')
            if args.action=='owner':recover_owner(env,event,args.out)
            elif args.action=='stop':
                if not args.owner:raise ValueError('authenticated original owner required')
                print(json.dumps(stop_original(args.owner,args.out)))
            else:prepare(env,event,args.out)
        return 0
    except (ValueError,OSError,KeyError,TypeError) as error:
        print('Mustang artifact recovery stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__),file=sys.stderr)
        return 4


if __name__=='__main__':sys.exit(main())
