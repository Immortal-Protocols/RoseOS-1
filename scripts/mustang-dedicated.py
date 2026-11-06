#!/usr/bin/env python3
"""Dedicated Mustang build/OTA steps. The committed lane and runtime stay unbound.

No provisioning, IAM, key bootstrap, global reclaim, automatic dispatch or phone
operation. Runtime actions require actual admitted bindings and this one fresh
manual workflow run. Public publication is separately default-off.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

import mustang_controller as controller
import mustang_lane
import mustang_paths
import mustang_ota as ota
import release_receipts

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'os/build-only/mustang-runtime.json'
WORKFLOW='.github/workflows/mustang-ota-dedicated.yml'
PRIVATE_RECORD='roseos.app-inputs-private-record.v1'
PRIVATE_PROVENANCE='roseos.app-inputs-private-provenance.v1'


def load(file):
    return json.loads(Path(file).read_text())


def reference(value, root=ROOT):
    path=Path(str(value))
    if not isinstance(value,str) or path.is_absolute() or '..' in path.parts or not path.parts \
            or path.parts[0]!='os' or (Path(root)/path).is_symlink() or not (Path(root)/path).is_file() \
            or not (Path(root)/path).resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('actual committed Mustang evidence under os/ is required')
    value=load(Path(root)/path)
    if not isinstance(value,dict): raise ValueError('committed Mustang evidence must be a JSON object')
    return value


def guard(lane, cfg, env, publish=False, root=ROOT, diff=None):
    run=controller.invocation(lane,env)
    paths=mustang_paths.layout(lane)
    if env.get('GITHUB_REPOSITORY_ID')!='1428996648':
        raise ValueError('dedicated workflow repository ID differs')
    if lane['workflow']!=WORKFLOW or lane['queue']!='mustang-ota' \
            or cfg.get('schema')!='roseos.mustang-dedicated-runtime.v1' or cfg.get('status')!='bound':
        raise ValueError('dedicated Mustang runtime inputs are unbound')
    controller.require_explicit_target(controller.common_controller())
    if type(cfg.get('jobs')) is not int or not 1<=cfg['jobs']<=256 \
            or set(cfg.get('tools',{}))!={'aapt','apksigner','node'}:
        raise ValueError('actual dedicated Make job count required')
    for key in ('aapt','apksigner','node'):
        value=cfg['tools'].get(key)
        if not isinstance(value,str) or not Path(value).is_absolute() or '..' in Path(value).parts:
            raise ValueError('actual dedicated tool paths required')
    if not isinstance(cfg.get('dataFilesystemUuid'),str) or not re.fullmatch(r'[A-Za-z0-9-]+',cfg['dataFilesystemUuid']):
        raise ValueError('actual dedicated data filesystem UUID required')
    network=reference(cfg.get('networkEvidence'),root)
    if cfg.get('automaticStopHours')!=12 or network.get('builder')!=lane['vm'] \
            or network.get('githubEgressVerified') is not True or not network.get('approvedRoute'):
        raise ValueError('actual isolated STOP and approved working GitHub egress evidence required')
    source=reference(cfg.get('sourceEvidence'),root)
    manifest=hashlib.sha256((Path(root)/'os/pinned-manifest.xml').read_bytes()).hexdigest()
    if source.get('builder')!=lane['vm'] or source.get('sourceRoot')!=lane['sourceRoot'] \
            or source.get('manifestSha256')!=manifest or source.get('pristine') is not True \
            or source.get('adevtoolRevision')!=load(Path(root)/'os/build-only/mustang.json')['pins']['vendor/adevtool']:
        raise ValueError('dedicated pristine pinned source/bootstrap evidence differs')
    runner=cfg['runner']; registration=reference(runner.get('registrationEvidence'),root)
    if not isinstance(runner.get('groupName'),str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _-]{0,99}',runner['groupName']) \
            or not re.fullmatch(r'[1-9][0-9]*',str(runner.get('groupId',''))) or runner.get('lifetime') not in ('persistent','ephemeral') \
            or registration.get('builder')!=lane['vm'] or registration.get('repositoryId')!='1428996648' \
            or registration.get('workflow')!=WORKFLOW or registration.get('groupId')!=runner['groupId'] \
            or registration.get('readyOnEveryStart') is not True or registration.get('publicRepositoryAdmitted') is not True:
        raise ValueError('dedicated protected runner registration-on-start evidence required')
    signing=reference(cfg.get('signingRouteEvidence'),root)
    if signing.get('builder')!=lane['vm'] or signing.get('workflow')!=WORKFLOW \
            or signing.get('identityVariable')!='GCP_SIGNER_SERVICE_ACCOUNT' \
            or signing.get('providerVariable')!='MUSTANG_SIGNER_WORKLOAD_IDENTITY_PROVIDER' \
            or signing.get('establishedMustangIdentityPreserved') is not True or signing.get('approved') is not True:
        raise ValueError('existing signer admission for the dedicated workflow is not established')
    state=reference(cfg.get('allocationState'),root)
    if str(state.get('failedRun'))!='41884368820' or state.get('unknown') is not False \
            or state.get('builder')!=lane['vm'] or state.get('dataFilesystemUuid')!=cfg['dataFilesystemUuid'] \
            or not isinstance(state.get('consumedBuilds'),list) or any(
                not isinstance(b,str) or not re.fullmatch(r'[1-9][0-9]{9}',b) for b in state['consumedBuilds']):
        raise ValueError('prior failed Mustang allocation state remains unresolved')
    record=reference(cfg.get('appInputsRecord'),root)
    if record.get('schema') not in ('roseos.app-inputs-record.v1',PRIVATE_RECORD) or record.get('producer',{}).get('device')!='frankel':
        raise ValueError('actual immutable signed-app export record required')
    if record['schema']==PRIVATE_RECORD: private_binding(record,lane,cfg,root)
    allocation=reference(record['producer']['allocation']['path'],root)
    if ota.digest(Path(root)/record['producer']['allocation']['path'])!=record['producer']['allocation']['sha256'] \
            or allocation.get('sourceSha')!=record['producer']['sourceSha']:
        raise ValueError('app producer allocation binding differs')
    packages=load(Path(root)/'os/latest-beta/allocation.json')['packages']
    for package in packages:
        changed=(diff or (lambda before,after,paths:subprocess.check_output(
            ['git','-C',str(root),'diff','--name-only',before,after,'--',*paths],text=True)))(
                record['producer']['sourceSha'],run['source'],package['sources'])
        if changed:
            raise ValueError('fresh app export omits current app source changes: '+package['packageName'])
    baseline=cfg.get('baselineTargetFiles')
    factory=cfg.get('baselineFactoryEvidence')
    if factory is not None:
        if baseline is not None: raise ValueError('select exactly one baseline evidence kind')
        ota.module('mustang_factory_baseline_guard','mustang-baseline-image.py').contract(reference(factory,root),lane,cfg,root)
    else:
        committed=load(Path(root)/load(Path(root)/'os/ota/mustang.json')['baseline']['signReceipt'])['outputs']['targetFiles']
        if not isinstance(baseline,dict) or any(baseline.get(k)!=committed[k] for k in ('sha256','sizeBytes')) \
                or paths['home'] not in Path(baseline.get('path','')).parents or '..' in Path(baseline.get('path','')).parts:
            raise ValueError('actual own-disk baseline archive binding required')
    if publish:
        if cfg['publication'].get('enabled') is not True:
            raise ValueError('dedicated public OTA publication remains disabled')
        authority=reference(cfg['publication'].get('authorityRecord'),root)
        if cfg['publication'].get('enabled') is not True or authority.get('approved') is not True \
                or authority.get('device')!='mustang' or not authority.get('approvalRef') \
                or authority.get('workflow')!=WORKFLOW or authority.get('automatic') is not False:
            raise ValueError('dedicated public OTA publication remains disabled')
    ota.guard(ota.config(),access=True,publication=publish)
    return dict(cfg,consumedBuilds=state['consumedBuilds'])


def emit(**values):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as out:
            for key,value in values.items():
                if '\n' in str(value) or '\r' in str(value): raise ValueError('multiline workflow output refused')
                out.write(f'{key}={value}\n')


def metadata_id():
    request=urllib.request.Request('http://metadata.google.internal/computeMetadata/v1/instance/id',
                                   headers={'Metadata-Flavor':'Google'})
    with urllib.request.urlopen(request,timeout=5) as response:
        return response.read(64).decode().strip()


def host_identity(lane,filesystem_uuid,observe_id=metadata_id,execute=subprocess.run):
    paths=mustang_paths.layout(lane)
    if observe_id()!=lane['vm']['instanceId']: raise ValueError('runner is not the admitted dedicated VM')
    uuid=execute(['findmnt','--noheadings','--output','UUID','--target',str(paths['home'])],capture_output=True,text=True)
    if not filesystem_uuid or uuid.returncode or uuid.stdout.strip()!=filesystem_uuid:
        raise ValueError('dedicated data filesystem is not the admitted mounted disk')
    for path in paths.values():
        if any(part.is_symlink() for part in (path,*path.parents)):
            raise ValueError('dedicated path traverses a link')
    return paths


def host_check(lane,cfg,observe_id=metadata_id,execute=subprocess.run):
    paths=host_identity(lane,cfg['dataFilesystemUuid'],observe_id,execute)
    space=os.statvfs(paths['home'])
    if space.f_bavail<=0 or space.f_files<=0 or space.f_favail<=0:
        raise ValueError('dedicated data filesystem has no available bytes/inodes')
    if not paths['sourceRoot'].is_dir() or not paths['storageRoot'].is_dir():
        raise ValueError('dedicated source/storage must already be prepared')
    if any(os.stat(paths[key]).st_dev!=os.stat(paths['home']).st_dev for key in ('sourceRoot','storageRoot')):
        raise ValueError('dedicated source/storage are not on the admitted data filesystem')
    for tool in cfg['tools'].values():
        if not Path(tool).is_file() or not os.access(tool,os.X_OK): raise ValueError('dedicated tool unavailable')
    if any(shutil.which(tool) is None for tool in ('gh','gcloud')):
        raise ValueError('artifact/signing CLI unavailable before compile')


def private_binding(record,lane,cfg,root=ROOT):
    """Admit only a separately approved, completed transfer to this exact lane.

    These committed attestations do not perform or authorize a transfer. Source
    receipt/identity/policy checks are still repeated on the imported bytes.
    """
    export=record['export']; archive=export['privateArchive']; producer=record['producer']
    if set(export)!={'receiptSha256','privateArchive'} or set(archive)!={'path','sha256','sizeBytes','provenance'}:
        raise ValueError('private transfer cannot contain Actions artifact metadata')
    build=producer.get('build','')
    if producer.get('device')!='frankel' or not re.fullmatch(r'[1-9][0-9]{9}',build) \
            or producer.get('tag')!=f'frankel-{build}':
        raise ValueError('private export producer identity differs')
    allocation_ref=producer['allocation']; allocation=reference(allocation_ref['path'],root)
    if ota.digest(Path(root)/allocation_ref['path'])!=allocation_ref['sha256'] or any(
            producer.get(key)!=allocation.get(key) for key in ('device','build','tag','candidateSha','sourceSha','run')):
        raise ValueError('private export producer differs from its committed allocation')
    home=mustang_paths.layout(lane)['home']
    expected=home/'inputs'/producer['tag']/'export.tar'
    if archive.get('path')!=str(expected) or type(archive.get('sizeBytes')) is not int or archive['sizeBytes']<=0 \
            or any(not re.fullmatch(r'[0-9a-f]{64}',str(value)) for value in
                   (archive.get('sha256'),export.get('receiptSha256'))):
        raise ValueError('private archive path/hash/size binding differs')
    ref=archive['provenance']
    if set(ref)!={'path','sha256'} or not re.fullmatch(r'[0-9a-f]{64}',str(ref['sha256'])):
        raise ValueError('private transfer provenance digest required')
    proof=reference(ref['path'],root)
    if ota.digest(Path(root)/ref['path'])!=ref['sha256']:
        raise ValueError('private transfer provenance bytes differ')
    source=proof.get('source',{}); transfer=proof.get('transfer',{})
    identity={k:archive[k] for k in ('sha256','sizeBytes')}
    refs=(source.get('evidenceRef'),transfer.get('approvalRef'),transfer.get('evidenceRef'))
    if proof.get('schema')!=PRIVATE_PROVENANCE or proof.get('producer')!=producer \
            or proof.get('receiptSha256')!=export['receiptSha256'] or proof.get('archive')!=identity \
            or source.get('originalAttemptChainVerified') is not True \
            or any(not isinstance(value,str) or not value.strip() for value in refs) \
            or source.get('exporter')!='scripts/host-app-inputs-export.py' \
            or not re.fullmatch(r'[0-9a-f]{40}',str(source.get('exporterSource'))) \
            or transfer.get('approved') is not True or transfer.get('completed') is not True \
            or transfer.get('received')!=identity or transfer.get('destination')!=dict(
                builder=lane['vm'],dataFilesystemUuid=cfg['dataFilesystemUuid'],path=str(expected)):
        raise ValueError('private export/transfer provenance is incomplete or belongs elsewhere')
    return archive


def import_private(record,out,lane,cfg,root=ROOT):
    """Copy and check one prepositioned plain tar; never extract its metadata.

    A failed copy/import is retained in the fresh output directory. Original
    inputs are never changed, and no downloader or credentials are used here.
    """
    archive=private_binding(record,lane,cfg,root); path=Path(archive['path']); out=Path(out)
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('private archive traverses a link')
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('private archive must be a regular file')
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as source:
        observed=os.fstat(source.fileno())
        if not stat.S_ISREG(observed.st_mode) or observed.st_size!=archive['sizeBytes'] \
                or observed.st_dev!=os.stat(mustang_paths.layout(lane)['home']).st_dev:
            raise ValueError('private archive is not the recorded regular file on Mustang data storage')
        out.mkdir(mode=0o700); copied=out/'input.tar'
        with open(copied,'xb') as destination:
            remaining=archive['sizeBytes']+1
            while remaining:
                chunk=source.read(min(1<<20,remaining))
                if not chunk: break
                destination.write(chunk); remaining-=len(chunk)
    if copied.stat().st_size!=archive['sizeBytes'] or ota.digest(copied)!=archive['sha256']:
        raise ValueError('private archive copy/hash differs; inputs retained')
    rows={row['path']:row for row in record['files']}
    packages=[app['package'] for app in load(Path(root)/'os/build-only/mustang-reconcile.json')['candidateInputs']['apps']]
    canonical={'background-policy.json'}|{f'signed-apps/{p}{suffix}' for p in packages
        for suffix in ('.apk','.receipt.json','.apk.idsig')}
    if len(rows)!=len(record['files']) or set(rows)!=canonical or any(
            type(row.get('sizeBytes')) is not int or row['sizeBytes']<0 for row in rows.values()):
        raise ValueError('private archive record does not name the canonical ten payload files')
    expected=canonical|{'app-inputs-export.json','SHA256SUMS'}
    extracted=out/'files'; extracted.mkdir(mode=0o700); seen=set(); found=set()
    with tarfile.open(copied,'r:') as stream:
        for member in stream:
            name=member.name
            if name.startswith('./'): name=name[2:]
            if name in ('.',''): name=''
            if name in seen: raise ValueError('private archive contains duplicate paths')
            seen.add(name)
            if len(seen)>len(expected)+2 or member.issparse():
                raise ValueError('private archive contains excess or sparse members')
            if member.isdir() and name in ('','signed-apps'): continue
            if not member.isreg() or name not in expected:
                raise ValueError('private archive contains an unsafe type or unexpected path')
            limit=rows[name]['sizeBytes'] if name in rows else 4<<20
            if member.size<0 or member.size>limit or (name in rows and member.size!=limit):
                raise ValueError('private archive member size differs')
            target=extracted/name; target.parent.mkdir(exist_ok=True)
            with stream.extractfile(member) as source,open(target,'xb') as destination:
                shutil.copyfileobj(source,destination)
            found.add(name)
    if found!=expected: raise ValueError('private archive inventory differs')
    return extracted


def fetch_inputs(record_path,out,execute=subprocess.run,lane=None,cfg=None,root=ROOT):
    rec=load(record_path)
    if rec.get('schema')==PRIVATE_RECORD:
        if lane is None or cfg is None: raise ValueError('private import requires the actual dedicated lane')
        return import_private(rec,out,lane,cfg,root)
    export=rec['export']; artifact=export['artifact']; out=Path(out)
    if not re.fullmatch(r'[1-9][0-9]*',str(artifact['id'])) or not re.fullmatch(r'sha256:[0-9a-f]{64}',artifact['digest']):
        raise ValueError('immutable artifact ID/digest required')
    endpoint=f'/repos/Immortal-Protocols/RoseOS-1/actions/artifacts/{artifact["id"]}'
    meta=execute(['gh','api',endpoint],capture_output=True,text=True)
    if meta.returncode: raise ValueError('app artifact metadata read failed; no retry')
    observed=json.loads(meta.stdout)
    if str(observed['workflow_run']['id'])!=str(export['run']) or observed.get('expired') is not False \
            or observed.get('digest')!=artifact['digest'] or observed.get('name')!=artifact['name']:
        raise ValueError('app artifact metadata differs from committed export')
    out.mkdir(mode=0o700); archive_path=out/'download.zip'; extracted=out/'files'; extracted.mkdir()
    with open(archive_path,'xb') as destination:
        downloaded=execute(['gh','api',endpoint+'/zip'],stdout=destination,stderr=subprocess.PIPE)
    if downloaded.returncode or 'sha256:'+ota.digest(archive_path)!=artifact['digest']:
        raise ValueError('app artifact download/digest failed; inputs retained, no retry')
    rows={f['path']:f for f in rec['files']}
    expected=set(rows)|{'app-inputs-export.json','SHA256SUMS'}
    with zipfile.ZipFile(archive_path) as archive:
        members=archive.infolist()
        files=[m for m in members if not m.is_dir()]
        if len(files)!=len(expected) or {m.filename for m in files}!=expected \
                or any(Path(m.filename).is_absolute() or '..' in Path(m.filename).parts
                       or ((m.external_attr>>16)&0o170000)==0o120000 for m in members):
            raise ValueError('app artifact ZIP inventory/paths differ')
        for member in files:
            limit=rows[member.filename]['sizeBytes'] if member.filename in rows else 4<<20
            if member.file_size>limit: raise ValueError('app artifact member exceeds recorded size')
            target=extracted/member.filename; target.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(member) as src,open(target,'xb') as dst: shutil.copyfileobj(src,dst)
    return extracted


def context(publish=False):
    lane=mustang_lane.configuration()
    cfg=guard(lane,load(CONFIG),os.environ,publish)
    if subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()!=os.environ['GITHUB_SHA'] \
            or subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip():
        raise ValueError('dedicated checkout differs from the captured clean source')
    return lane,cfg


def verify(directory,lane,cfg,publish=False):
    r=ota.verify_staged(directory,ota.config(),publication=publish)
    if r.get('builder')!=lane['vm'] or r.get('workflow')!=WORKFLOW \
            or r.get('runId')!=os.environ['GITHUB_RUN_ID'] or r.get('runAttempt')!=os.environ['GITHUB_RUN_ATTEMPT'] \
            or r.get('recipeSource')!=os.environ['GITHUB_SHA']:
        raise ValueError('staged OTA differs from this dedicated builder/run/source')
    return r


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('guard','host-check','fetch-inputs','baseline','produce','collect','sign',
        '_execute','stage','verify-staged','publish','report','ownership-output','cleanup-prepare','require-owned'))
    parser.add_argument('--dir'); parser.add_argument('--out'); parser.add_argument('--config')
    parser.add_argument('--ownership-record'); parser.add_argument('--publish',action='store_true')
    args=parser.parse_args()
    args.publish=args.publish or (args.action=='guard' and os.environ.get('MUSTANG_PUBLISH')=='true')
    if args.action in ('ownership-output','cleanup-prepare','require-owned','report'):
        lane=mustang_lane.validate(mustang_lane.configuration())
        controller.invocation(lane,os.environ)
        cfg=None
    else:
        lane,cfg=context(args.publish)
    run,attempt=os.environ['GITHUB_RUN_ID'],os.environ['GITHUB_RUN_ATTEMPT']
    prep=mustang_paths.attempt(lane,'prep',run,attempt)
    pipeline=ota.module('mustang_dedicated_pipeline','mustang-pipeline.py') if args.action in ('produce','report') else None
    signing=ota.module('mustang_dedicated_signing','mustang-sign-package.py') if args.action in ('collect','sign','_execute') else None
    if signing is not None:
        os.environ.update(EXPECTED_COMPILE_RUN=run,EXPECTED_COMPILE_SOURCE=os.environ['GITHUB_SHA'],
            EXPECTED_COMPILE_RECOVERY='false',EXPECTED_BOOTSTRAP_SOURCE=load(ROOT/'os/signing-bootstrap/mustang-bootstrap-receipt.json')['recipeSha'])
    if args.action=='guard':
        for name in ('MUSTANG_CONTROLLER_SERVICE_ACCOUNT','MUSTANG_CONTROLLER_WORKLOAD_IDENTITY_PROVIDER',
                     'MUSTANG_SIGNER_WORKLOAD_IDENTITY_PROVIDER','GCP_SIGNER_SERVICE_ACCOUNT'):
            if not os.environ.get(name): raise ValueError(name+' is unbound; no host start')
        if os.environ['GCP_SIGNER_SERVICE_ACCOUNT']!=ota.module('mustang_route_identity','mustang-sign-package.py').SIGNER:
            raise ValueError('existing Mustang signer identity differs; no host start')
        emit(runner_group=cfg['runner']['groupName'],runner_label=lane['runnerLabel'])
    elif args.action=='cleanup-prepare':
        encoded=os.environ.get('MUSTANG_OWNER_B64','')
        if len(encoded)>8192: raise ValueError('oversized controller ownership refused')
        raw=base64.b64decode(encoded,validate=True)
        if not raw: raise ValueError('controller ownership absent; no login or STOP')
        record=json.loads(raw); started=mustang_lane.owned_start(record['owner'],lane)
        if record.get('run')!=controller.invocation(lane,os.environ) or record.get('schema')!=controller.SCHEMA:
            raise ValueError('cleanup ownership belongs to another run')
        if not args.ownership_record: raise ValueError('ownership file required')
        release_receipts.write_once(args.ownership_record,record)
        emit(started='true' if started else 'false')
    elif args.action=='ownership-output':
        if args.ownership_record and Path(args.ownership_record).is_file():
            controller.read_ownership(args.ownership_record,lane,controller.invocation(lane,os.environ))
            emit(ownership=base64.b64encode(Path(args.ownership_record).read_bytes()).decode())
    elif args.action=='require-owned':
        if not args.ownership_record or not controller.read_ownership(args.ownership_record,lane,controller.invocation(lane,os.environ)):
            raise ValueError('dedicated VM was already running or owned elsewhere; reconcile before building')
    elif args.action in ('verify-staged','publish'):
        verify(args.dir,lane,cfg,args.publish)
        if args.action=='publish':
            if not args.publish: raise ValueError('explicit public publication input required')
            client=ota.publisher.GitHubReleases(ota.REPOSITORY,os.environ.get('PIXEL_OTA_TOKEN',''))
            ota.publish(client,args.dir,ota.config(),args.out)
    elif args.action=='report':
        # Failure reporting may ignore signing/input readiness, but never read
        # another guest or a fallback/unmounted filesystem's attempt paths.
        host_identity(lane,load(CONFIG).get('dataFilesystemUuid'))
        out=Path(args.out)
        pipeline.collect_reports(run,attempt,out,lane=lane)
        for kind,name in (('collect','compile-collection.json'),('sign','mustang-sign-receipt.json')):
            path=mustang_paths.attempt(lane,kind,run,attempt)/name
            if path.is_file() and not path.is_symlink(): shutil.copyfile(path,out/name)
        if args.dir and Path(args.dir).is_file(): shutil.copyfile(args.dir,out/'baseline.json')
    else:
        host_check(lane,cfg)
        if args.action=='host-check': return 0
        if args.action=='fetch-inputs':
            inputs=fetch_inputs(ROOT/cfg['appInputsRecord'],args.out,lane=lane,cfg=cfg,root=ROOT)
            ota.module('mustang_verify_apps','app-inputs-verify.py').verify(ROOT/cfg['appInputsRecord'],inputs,cfg['tools'])
            emit(inputs_dir=str(inputs))
        elif args.action=='baseline':
            factory=reference(cfg['baselineFactoryEvidence']) if cfg.get('baselineFactoryEvidence') is not None else None
            ota.observe_baseline(args.out,lane,cfg['tools'],cfg.get('baselineTargetFiles'),factory=factory,runtime=cfg)
        elif args.action=='produce':
            # This process has no further authenticated download/signing work.
            # Do not forward job tokens or auth/env-output capabilities to Make
            # or vendor tools. Later steps get their normal fresh job environment.
            for name in ('GH_TOKEN','GITHUB_TOKEN','ACTIONS_ID_TOKEN_REQUEST_TOKEN','ACTIONS_ID_TOKEN_REQUEST_URL',
                         'CLOUDSDK_CONFIG','GOOGLE_APPLICATION_CREDENTIALS','PIXEL_OTA_TOKEN','GITHUB_ENV','GITHUB_OUTPUT'):
                os.environ.pop(name,None)
            pipeline.run(run,attempt,lane,cfg,args.dir)
            pipeline.collect_reports(run,attempt,prep/'delivery-inputs',lane=lane)
        elif args.action=='collect': signing.collect(prep/'delivery-inputs/candidate-receipt.json',run,attempt,lane=lane)
        elif args.action=='sign': signing.sign(prep/'delivery-inputs/candidate-receipt.json',ROOT/'os/signing-bootstrap/mustang-bootstrap-receipt.json',run,attempt,lane=lane)
        elif args.action=='_execute':
            expected=mustang_paths.attempt(lane,'sign',run,attempt)/'sign-config.json'
            if Path(args.config or '')!=expected or expected.is_symlink(): raise ValueError('foreign signing config refused')
            config=load(expected); sign=config['host']['sign']
            if config['target'].get('device')!='mustang' or sign.get('project')!=signing.PROJECT \
                    or sign.get('keysSecret')!=signing.SECRETS['keys'] or sign.get('passphraseSecret')!=signing.SECRETS['passphrase'] \
                    or str(sign.get('secretVersion'))!='1' or str(sign.get('passphraseSecretVersion'))!='1':
                raise ValueError('dedicated signing executor must preserve Mustang version-1 scope')
            _,source,build,epoch,outputs=signing.compile_binding(prep/'delivery-inputs/candidate-receipt.json',lane=lane)
            if Path.cwd()!=source or Path(os.environ.get('ROSE_RELEASE_DIR',''))!=expected.parent/'release' \
                    or os.environ.get('BUILD_NUMBER')!=build or os.environ.get('BUILD_DATETIME')!=epoch \
                    or sign.get('otatools')!=str(Path(outputs['otatools']['path']).relative_to(source/f'out-mustang-build-{build}')):
                raise ValueError('dedicated signing executor output/source/tool binding differs')
            if signing.digest(expected.parent/'release/unsigned-target_files.zip')!=outputs['targetFiles']['sha256']:
                raise ValueError('signing input copy differs from the unsigned proof')
            signing.execute_sign(config,os.environ)
        elif args.action=='stage':
            ota.stage(run,attempt,args.dir,args.out,lane=lane,tools=cfg['tools'],publication=False)
            verify(args.out,lane,cfg)
            emit(directory=args.out)
    return 0


if __name__=='__main__':
    try:
        sys.exit(main())
    except (ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError,zipfile.BadZipFile,tarfile.TarError) as error:
        print('Dedicated Mustang stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__),file=sys.stderr)
        sys.exit(4)
