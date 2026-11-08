"""Qualify one completed dependency install and preserve the failed extra build.

No failed generated JavaScript is used. The exact failed receipt must match
its uploaded evidence, the canonical lock must be held by the caller, and the
next job independently catalogs and checks the installed dependencies.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

import mustang_hostdeps as deps
import mustang_lane
import mustang_ephemeral as ephemeral
import mustang_yarn

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE='os/build-only/mustang-vendor-42516700216.json'


def evidence(root=ROOT):
    record=json.loads((Path(root)/EVIDENCE).read_text());r=record['receipt'];run=r['identity']['run']
    lane=mustang_lane.validate(mustang_lane.configuration())
    commands=r.get('commands',[])
    expected_yarn=str(Path(lane['storageRoot'])/'mustang-vendor-42516700216-1/yarn-tools/package/bin/yarn.js')
    if record.get('schema')!='roseos.mustang-vendor-evidence.v1' or record.get('workflowConclusion')!='failure' \
            or record.get('cleanupConclusion')!='success' or record.get('stopObservation',{}).get('status')!='TERMINATED' \
            or r.get('success') is not False or r.get('phase')!='adevtool-typescript-build' \
            or run.get('runId')!='42516700216' or run.get('runAttempt')!='1' \
            or r['identity']['builder']!=lane['vm'] or r.get('sourceRoot')!=lane['sourceRoot'] \
            or len(commands)!=2 or any(c.get('processStarted') is not True for c in commands) \
            or commands[0].get('exitCode')!=0 or commands[1].get('exitCode')!=1 \
            or any(c.get('argv',[])[:2]!=[ephemeral.NODE['install'],expected_yarn] for c in commands) \
            or commands[0].get('argv',[])[2:]!=['install','--cwd','vendor/adevtool','--frozen-lockfile','--non-interactive'] \
            or commands[1].get('argv',[])[2:]!=['--cwd','vendor/adevtool','run','build'] \
            or r.get('makeStarted') is not False or r.get('nodeVersion')!=ephemeral.NODE['version'] \
            or r.get('yarnVersion')!=mustang_yarn.pin(root)['version'] \
            or r.get('yarnDistribution',{}).get('archiveSha256')!=mustang_yarn.pin(root)['sha256']:
        raise ValueError('completed dependency-install recovery evidence differs')
    return record


def qualify(source,storage,identity,*,root=ROOT,catalog,deadline,host_sha,factory_sha):
    record=evidence(root);r=record['receipt'];run=r['identity']['run']
    if r['identity']['builder']!=identity['builder'] or r['identity']['diskUuid']!=identity['diskUuid']:
        raise ValueError('bootstrap recovery belongs to another builder or disk')
    if str(source)!=r['sourceRoot'] or storage!=source.parent/'rose-actions':
        raise ValueError('bootstrap recovery source or storage path differs')
    if r['hostDependencies']['receiptSha256']!=host_sha or r['factoryInput']['receiptSha256']!=factory_sha:
        raise ValueError('bootstrap recovery belongs to different prepared inputs')
    old=storage/'mustang-vendor-42516700216-1'
    raw=deps.readers.read_regular(str(storage),str(old/'vendor-preparation-receipt.json'),1 << 20)
    if hashlib.sha256(raw).hexdigest()!=record['receiptSha256'] or json.loads(raw)!=r:
        raise ValueError('bootstrap recovery receipt differs from uploaded evidence')
    adev=source/'vendor/adevtool';modules=adev/'node_modules';lib=adev/'lib'
    info=deps.plain_dir(modules)
    if info.st_uid!=os.geteuid() or info.st_gid!=os.getegid() or stat.S_IMODE(info.st_mode)&0o022 \
            or info.st_dev!=deps.plain_dir(old).st_dev:
        raise ValueError('completed dependency-install ownership or filesystem differs')
    result={'fromRun':run,'installExitCode':0,'installRepeated':False,
            'nodeModulesBefore':catalog(source,modules,deadline),'compiledJavascriptUsed':False}
    intent=old/'lib-retention-intent.json';completion=old/'lib-retention-result.json';target=old/'retained-tsc-lib'
    existing=any(os.path.lexists(p) for p in (intent,completion,target))
    if existing:
        if not os.path.lexists(intent):raise ValueError('compiled-output retention intent requires reconciliation')
        preserved=json.loads(deps.readers.read_regular(str(storage),str(completion),65536))
        observed=deps.plain_dir(target)
        if preserved.get('schema')!='roseos.mustang-vendor-lib-retention.v1' \
                or preserved.get('failedReceiptSha256')!=record['receiptSha256'] \
                or preserved.get('completed') is not True or preserved.get('source')!=str(lib) \
                or preserved.get('destination')!=str(target) \
                or (observed.st_dev,observed.st_ino)!=(preserved.get('device'),preserved.get('inode')) \
                or os.path.lexists(lib):
            raise ValueError('compiled-output retention requires reconciliation')
        if catalog(storage,target,deadline)!=preserved.get('catalog'):
            raise ValueError('retained compiled output changed; reconciliation required')
        result['libRetention']=preserved
    elif os.path.lexists(lib):
        before=deps.plain_dir(lib)
        if before.st_uid!=os.geteuid() or before.st_gid!=os.getegid() or before.st_dev!=info.st_dev \
                or stat.S_IMODE(before.st_mode)&0o022:
            raise ValueError('failed compiled-output ownership or filesystem differs')
        planned={'schema':'roseos.mustang-vendor-lib-retention.v1','completed':False,'source':str(lib),
                 'destination':str(target),'device':before.st_dev,'inode':before.st_ino,
                 'catalog':catalog(source,lib,deadline),'failedReceiptSha256':record['receiptSha256'],
                 'toRun':identity['run']}
        deps.receipt(intent,planned)
        fd=deps.readers._walk_dir(deps.readers._body(str(old)))
        try:os.fsync(fd)
        finally:os.close(fd)
        deps.rename_noreplace(lib,target)
        after=deps.plain_dir(target)
        if (after.st_dev,after.st_ino)!=(before.st_dev,before.st_ino) or os.path.lexists(lib):
            raise ValueError('compiled-output retention outcome requires reconciliation')
        for parent in (adev,old):
            fd=deps.readers._walk_dir(deps.readers._body(str(parent)))
            try:os.fsync(fd)
            finally:os.close(fd)
        result['libRetention']=dict(planned,completed=True);deps.receipt(completion,result['libRetention'])
        fd=deps.readers._walk_dir(deps.readers._body(str(old)))
        try:os.fsync(fd)
        finally:os.close(fd)
    else:result['libRetention']={'state':'absent','compiledJavascriptUsed':False}
    return result
