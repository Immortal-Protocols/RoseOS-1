"""Preserve the exact failed lunch output before one fresh otatools proof.

No retained bytes are used as build input. The caller verifies guest identity
and prepared inputs and holds the canonical lock. All moves are no-replace.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

import mustang_hostdeps as deps
import mustang_lane
import mustang_preparation_inputs as inputs

ROOT=Path(__file__).resolve().parents[1]
BINDING='os/build-only/mustang-otatools-output-recovery.json'


def binding(root=ROOT):
    cfg=json.loads((Path(root)/BINDING).read_text())
    if cfg!={'schema':'roseos.mustang-otatools-output-recovery.v1',
             'failedEvidence':'os/build-only/mustang-otatools-42524960701.json',
             'observationEvidence':'os/build-only/mustang-otatools-observation-42527536373.json',
             'failedRunId':'42524960701','failedRunAttempt':'1','outDir':'out_mustang_otatools_proof',
             'oldOutputUsedForBuild':False,'method':'same-filesystem-renameat2-RENAME_NOREPLACE-with-durable-intent-and-result'}:
        raise ValueError('otatools output recovery binding differs')
    failed=json.loads((Path(root)/cfg['failedEvidence']).read_text())
    observed=inputs.evidence(cfg['observationEvidence'],'roseos.mustang-otatools-observation-evidence.v1',root)
    r=failed['receipt'];o=observed['receipt'];lane=mustang_lane.validate(mustang_lane.configuration())
    if failed.get('schema')!='roseos.mustang-otatools-evidence.v1' or failed.get('runId')!='42524960701' \
            or failed.get('source')!='6a532d4ff5cc6c55c338da36344b172c4fcb3cf1' \
            or failed.get('workflowConclusion')!='failure' \
            or failed.get('cleanupConclusion')!='success' or failed.get('stopObservation',{}).get('status')!='TERMINATED' \
            or failed['stopObservation'].get('instanceId')!=lane['vm']['instanceId'] \
            or failed.get('receiptSha256')!='853d64f90c93d0f57b330cd1c503a5c5a3b2894c6b7d8c6a655ad3610c5177ab' \
            or observed.get('runId')!='42527536373' or observed.get('source')!='a7956fa4f13520bb89d403fb42e41e243bf314c3' \
            or observed.get('receiptSha256')!='b81ab79abb2199d835862d1292c2da6315b800402395e8ac0a0ab367fc1aca6d' \
            or observed['stopObservation'].get('instanceId')!=lane['vm']['instanceId'] \
            or r.get('success') is not False or r.get('phase')!='mustang-otatools-make' \
            or r.get('buildScript',{}).get('sha256')!='788cdd06ffe5974ff3de3a7bdce016191f62004f1d3c0ec78ec6505db3cfef3f' \
            or o.get('failedReceiptSha256')!=failed['receiptSha256'] or o.get('failedRun')!=r['identity']['run'] \
            or o.get('identity',{}).get('builder')!=lane['vm'] or r['identity']['builder']!=lane['vm'] \
            or o['identity'].get('diskUuid')!=mustang_lane.CONFIRMED_UUID \
            or r['identity'].get('diskUuid')!=mustang_lane.CONFIRMED_UUID \
            or o.get('readOnly') is not True or o.get('makeStarted') is not False or o.get('outputMoved') is not False \
            or o.get('lunch',{}).get('fields')!={'TARGET_PRODUCT':'mustang','TARGET_RELEASE':'','TARGET_BUILD_VARIANT':'user','TARGET_DEVICE':'mustang'} \
            or o['lunch'].get('matchesExpected') is not False \
            or 'lunch-identity-assertion' not in o.get('privateDiagnostic',{}).get('categories',[]) \
            or o.get('output',{}).get('complete') is not True or o['output'].get('entryCount')!=732 \
            or o['output'].get('specialEntries')!=[]:
        raise ValueError('otatools recovery requires the exact observed pre-Make assertion failure')
    return cfg,failed,observed


def sync_directory(path):
    fd=deps.readers._walk_dir(deps.readers._body(str(path)))
    try:os.fsync(fd)
    finally:os.close(fd)


def stable_observation(value):
    return {key:item for key,item in value.items() if key!='elapsedSeconds'}


def retain(source,storage,identity,*,root=ROOT):
    cfg,failed,observed=binding(root);r=failed['receipt'];o=observed['receipt']
    output=source/cfg['outDir'];old=storage/'mustang-otatools-42524960701-1';target=old/'retained-output'
    if r['identity']['builder']!=identity['builder'] or r['identity']['diskUuid']!=identity['diskUuid'] \
            or o['identity']['diskUuid']!=identity['diskUuid'] or r['sourceRoot']!=str(source) \
            or r['outDir']!=str(output) or storage!=source.parent/'rose-actions':
        raise ValueError('otatools recovery belongs to another source, builder or disk')
    raw=deps.readers.read_regular(str(storage),str(old/'otatools-preparation-receipt.json'),1 << 20)
    if hashlib.sha256(raw).hexdigest()!=failed['receiptSha256'] or json.loads(raw)!=r:
        raise ValueError('failed otatools receipt changed before retention')
    intent=old/'output-retention-intent.json';completion=old/'output-retention-result.json'
    if any(os.path.lexists(path) for path in (intent,completion,target)):
        plan=json.loads(deps.readers.read_regular(str(storage),str(intent),65536))
        saved=json.loads(deps.readers.read_regular(str(storage),str(completion),65536))
        current=deps.plain_dir(target)
        if plan.get('schema')!='roseos.mustang-otatools-output-retention.v1' \
                or plan.get('failedReceiptSha256')!=failed['receiptSha256'] \
                or plan.get('observationReceiptSha256')!=observed['receiptSha256'] \
                or plan.get('completed') is not False or saved!=dict(plan,completed=True) \
                or plan.get('source')!=str(output) or plan.get('destination')!=str(target) \
                or plan.get('oldOutputUsedForBuild') is not False \
                or (current.st_dev,current.st_ino)!=(plan.get('device'),plan.get('inode')) \
                or os.path.lexists(output):
            raise ValueError('otatools output retention requires reconciliation')
        if stable_observation(deps.inspect_output_entries(target))!=plan.get('entryObservation'):
            raise ValueError('retained otatools output metadata changed')
        return saved
    before=deps.plain_dir(output);old_info=deps.plain_dir(old)
    if before.st_uid!=os.geteuid() or before.st_gid!=os.getegid() \
            or before.st_dev!=old_info.st_dev or stat.S_IMODE(before.st_mode)&0o022:
        raise ValueError('failed otatools output ownership or filesystem differs')
    entries=deps.inspect_output_entries(output)
    if stable_observation(entries)!=stable_observation(o['output']):
        raise ValueError('failed otatools output metadata differs from observation')
    for name,sha in (('lunch-identity.json',o['lunch']['sha256']),('make.log',o['privateDiagnostic']['logSha256'])):
        data=deps.readers.read_regular(str(storage),str(old/name),deps.LOG_BYTES)
        if hashlib.sha256(data).hexdigest()!=sha:raise ValueError('failed otatools diagnostic changed before retention')
    plan={'schema':'roseos.mustang-otatools-output-retention.v1','completed':False,
          'source':str(output),'destination':str(target),'device':before.st_dev,'inode':before.st_ino,
          'entryObservation':stable_observation(entries),'failedReceiptSha256':failed['receiptSha256'],
          'observationReceiptSha256':observed['receiptSha256'],'toRun':identity['run'],'oldOutputUsedForBuild':False}
    deps.receipt(intent,plan);sync_directory(old)
    deps.rename_noreplace(output,target)
    after=deps.plain_dir(target)
    if (after.st_dev,after.st_ino)!=(before.st_dev,before.st_ino) or os.path.lexists(output):
        raise ValueError('otatools output move outcome requires reconciliation')
    sync_directory(source);sync_directory(old)
    saved=dict(plan,completed=True);deps.receipt(completion,saved);sync_directory(old)
    return saved
