"""Read-only qualification provenance and ZIP inspection; no executable or admission path."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
import zipfile

import mustang_hosttools as readers
import mustang_lane as lane
import mustang_preparation_inputs as inputs
import mustang_otatools_prep as prep

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE='os/build-only/mustang-otatools-42531345133.json'
INSPECTION='os/build-only/mustang-otatools-inspection-42531345133.json'
REQUIRED=('bin/ota_from_target_files','bin/sign_target_files_apks')
MAX_ENTRIES=20000
MAX_EXPANDED=4 << 30


def canonical_sha(value):
    # These two receipt schemas use mustang_hostdeps.receipt's exact encoding.
    return hashlib.sha256((json.dumps(value,sort_keys=True)+'\n').encode()).hexdigest()


def digest(value):
    return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) is not None


def binding(reference=EVIDENCE,*,root=ROOT):
    """Bind committed native and independent readback evidence, without reading package bytes."""
    record=inputs.evidence(reference,'roseos.mustang-otatools-evidence.v1',root)
    native=record.get('receipt',{});identity=native.get('identity',{});run=identity.get('run',{})
    approved=json.loads(readers.read_regular(str(root),str(Path(root)/'os/build-only/mustang-lane.json'),1 << 20))
    vm=approved['vm'];source=Path(approved['sourceRoot'])
    workflow='Immortal-Protocols/RoseOS-1/.github/workflows/mustang-ota-dedicated.yml@refs/heads/main'
    if vm.get('instance')!=lane.CONFIRMED_INSTANCE or vm.get('instanceId')!=lane.CONFIRMED_INSTANCE_ID \
            or vm.get('project')!='protocols-465723' or vm.get('zone')!='us-west2-a' \
            or str(source)!=str(Path(lane.CONFIRMED_MOUNT)/'roseos/source') \
            or approved.get('workflow')!='.github/workflows/mustang-ota-dedicated.yml' \
            or approved.get('queue')!='mustang-ota' \
            or identity.get('builder')!=vm or identity.get('diskUuid')!=lane.CONFIRMED_UUID \
            or run.get('workflow')!=workflow or run.get('runId')!=record.get('runId') \
            or not re.fullmatch(r'[1-9][0-9]{0,19}',str(run.get('runId',''))) \
            or run.get('source')!=record.get('source') or not re.fullmatch(r'[0-9a-f]{40}',str(run.get('source',''))) \
            or run.get('runAttempt')!='1' or record.get('runAttempt')!='1' \
            or record['stopObservation'].get('instanceId')!=vm['instanceId'] \
            or record['stopObservation'].get('maxRunDurationSeconds')!=14400 \
            or record['stopObservation'].get('terminationAction')!='STOP':
        raise ValueError('tool qualification belongs to another builder, disk, run or workflow')
    if native.get('schema')!='roseos.mustang-otatools-preparation-run.v1' \
            or native.get('success') is not True or native.get('phase')!='complete' \
            or native.get('makeStarted') is not True or type(native.get('makeExitCode')) is not int \
            or native['makeExitCode']!=0 or native.get('goals')!=['otatools-package'] \
            or native.get('jobs')!=24 or native.get('product')!='mustang-cur-user' \
            or native.get('actualLunchIdentity')!=dict(TARGET_PRODUCT='mustang',TARGET_DEVICE='mustang',
                                                       TARGET_RELEASE='cur',TARGET_BUILD_VARIANT='user') \
            or native.get('sourceRoot')!=str(source) or native.get('outDir')!=str(source/prep.OUT) \
            or any(native.get(k) is not False for k in ('allocated','signingStarted','otaAdmitted','signerPinsAdmitted')) \
            or record.get('otaAdmitted') is not False or record.get('signerPinsAdmitted') is not False:
        raise ValueError('tool qualification is not a successful tool-only Mustang build')
    for field in ('source','sourceReadback'):
        check=native.get(field,{})
        if check.get('projectCount')!=1057 or check.get('check')!='project-heads-and-git-status' \
                or check.get('pristineRuntimeAttestation') is not False:
            raise ValueError('tool qualification source checks differ')
    package=native.get('package',{});relative=Path(package.get('relativePath',''))
    if not digest(package.get('sha256')) or type(package.get('sizeBytes')) is not int \
            or not 0<package['sizeBytes']<=prep.PACKAGE_BYTES \
            or relative.is_absolute() or '..' in relative.parts or not relative.match(prep.PATTERN) \
            or package.get('modulePattern')!=prep.PATTERN or package.get('path')!=str(source/prep.OUT/relative):
        raise ValueError('qualified package identity or Soong module differs')
    independent=record.get('independentReadback',{});readback=independent.get('receipt',{})
    if canonical_sha(native)!=record['receiptSha256'] \
            or canonical_sha(readback)!=independent.get('receiptSha256') \
            or readback.get('schema')!='roseos.mustang-otatools-independent-readback.v1' \
            or readback.get('nativeReceiptSha256')!=record['receiptSha256'] or readback.get('run')!=run \
            or readback.get('byteReadbackMatches') is not True \
            or any(readback.get(k) is not False for k in ('packageExecuted','otaAdmitted','signerPinsAdmitted')) \
            or readback.get('packageSha256')!=package['sha256'] \
            or type(readback.get('packageBytes')) is not int or readback['packageBytes']!=package['sizeBytes']:
        raise ValueError('independent same-run package readback or receipt digest differs')
    artifacts=(record.get('artifact',{}),record.get('packageArtifact',{}),independent.get('artifact',{}))
    for artifact,prefix in zip(artifacts,('receipt','package','independent-readback')):
        if type(artifact.get('id')) is not int or artifact['id']<=0 \
                or artifact.get('name')!=f'mustang-otatools-{prefix}-{run["runId"]}-1' \
                or not digest(artifact.get('sha256')) or type(artifact.get('sizeBytes')) is not int \
                or artifact['sizeBytes']<=0:
            raise ValueError('qualification artifact provenance differs')
    if len({a['id'] for a in artifacts})!=3:
        raise ValueError('qualification artifact identities are duplicated')
    return dict(schema='roseos.mustang-otatools-qualification-binding.v1',evidence=reference,run=run,
        nativeReceiptSha256=record['receiptSha256'],independentReceiptSha256=independent['receiptSha256'],
        packageSha256=package['sha256'],packageBytes=package['sizeBytes'],packageArtifact=artifacts[1],
        packageBytesReadThisInvocation=False,packageExecuted=False,pristineRuntimeAttestation=False,
        signerPinsAdmitted=False,otaAdmitted=False)


def inspect_package(archive_path,reference=EVIDENCE,*,root=ROOT):
    """Hash all supplied ZIP bytes and members, without extracting or running anything."""
    proof=binding(reference,root=root);path=Path(os.path.abspath(archive_path))
    fd,before=readers._open_regular(str(path.parent),str(path))
    deadline=time.monotonic()+900
    with os.fdopen(fd,'rb') as handle:
        if before.st_size!=proof['packageBytes']:
            raise ValueError('supplied tool package size differs from qualification')
        h=hashlib.sha256()
        while chunk:=handle.read(1 << 20):
            if time.monotonic()>deadline:raise ValueError('tool inspection deadline exceeded')
            h.update(chunk)
        if h.hexdigest()!=proof['packageSha256']:
            raise ValueError('supplied tool package digest differs from qualification')
        handle.seek(0);inventory=[];seen=set();expanded=0
        with zipfile.ZipFile(handle) as archive:
            members=archive.infolist()
            if not members or len(members)>MAX_ENTRIES:raise ValueError('tool package entry bound exceeded')
            if sum(i.file_size for i in members)>MAX_EXPANDED:raise ValueError('tool package expansion bound exceeded')
            for info in members:
                if time.monotonic()>deadline:raise ValueError('tool inspection deadline exceeded')
                name=info.filename;key=name.rstrip('/');parts=PurePosixPath(key).parts;mode=info.external_attr>>16
                if not parts or '\\' in name or '\x00' in name or ':' in name or name.startswith('/') \
                        or '..' in parts or str(PurePosixPath(key))!=key or key in seen \
                        or info.flag_bits&1 or info.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED) \
                        or mode&0o7000 or stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR) \
                        or info.is_dir()!=(stat.S_ISDIR(mode) or (stat.S_IFMT(mode)==0 and name.endswith('/'))):
                    raise ValueError('unsafe, duplicate or unsupported tool package member')
                seen.add(key)
                if info.is_dir():
                    if info.file_size:raise ValueError('tool package directory contains bytes')
                    inventory.append(dict(path=key,type='directory',mode=mode&0o777));continue
                h=hashlib.sha256();size=0
                with archive.open(info) as member:
                    while chunk:=member.read(1 << 20):
                        if time.monotonic()>deadline:raise ValueError('tool inspection deadline exceeded')
                        size+=len(chunk);expanded+=len(chunk)
                        if size>info.file_size or expanded>MAX_EXPANDED:raise ValueError('tool package expansion bound exceeded')
                        h.update(chunk)
                if size!=info.file_size:raise ValueError('tool package member length differs')
                inventory.append(dict(path=key,type='file',mode=mode&0o777,sizeBytes=size,sha256=h.hexdigest()))
        after=os.fstat(handle.fileno())
        if any(getattr(before,k)!=getattr(after,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')):
            raise ValueError('tool package changed during inspection')
    rows={r['path']:r for r in inventory}
    for name in rows:
        parts=PurePosixPath(name).parts
        if any(rows.get('/'.join(parts[:i]),{}).get('type')=='file' for i in range(1,len(parts))):
            raise ValueError('tool package file conflicts with a directory prefix')
    if any(name not in rows or rows[name]['type']!='file' or not rows[name]['sizeBytes'] \
           or not rows[name]['mode']&0o111 for name in REQUIRED):
        raise ValueError('qualified tool package lacks a required executable entry')
    inventory.sort(key=lambda r:r['path'])
    return dict(proof,schema='roseos.mustang-otatools-package-inspection.v1',packageBytesReadThisInvocation=True,
        entryCount=len(inventory),expandedBytes=expanded,inventorySha256=canonical_sha(inventory),
        requiredExecutables={name:rows[name] for name in REQUIRED},inventory=inventory)


def inspection_record(reference=INSPECTION,*,root=ROOT):
    """Validate a committed inspection inventory; this does not reread or admit a package."""
    parts=str(reference).split('/')
    if parts[:2]!=['os','build-only'] or any(p in ('','.','..') for p in parts):
        raise ValueError('tool inspection must be committed build-only evidence')
    record=json.loads(readers.read_regular(str(root),str(Path(root)/reference),12 << 20))
    proof=binding(record.get('evidence'),root=root)
    if record.get('schema')!='roseos.mustang-otatools-package-inspection.v1' \
            or record.get('packageBytesReadThisInvocation') is not True \
            or any(record.get(k) is not False for k in ('packageExecuted','pristineRuntimeAttestation','signerPinsAdmitted','otaAdmitted')) \
            or any(record.get(k)!=v for k,v in proof.items() if k not in ('schema','packageBytesReadThisInvocation')):
        raise ValueError('tool inspection differs from the bound qualification')
    metadata={'inspectorSource','inspectorScriptSha256','inspectionReceiptSha256','recordedAt','outerArtifactReadback'}
    outer=record.get('outerArtifactReadback',{});artifact=proof['packageArtifact']
    if canonical_sha({k:v for k,v in record.items() if k not in metadata})!=record.get('inspectionReceiptSha256') \
            or not re.fullmatch(r'[0-9a-f]{40}',str(record.get('inspectorSource',''))) \
            or not digest(record.get('inspectorScriptSha256')) \
            or outer.get('complete') is not True or outer.get('packageExecuted') is not False \
            or outer.get('artifactId')!=artifact['id'] or outer.get('artifactBytes')!=artifact['sizeBytes'] \
            or outer.get('artifactSha256')!=artifact['sha256'] \
            or outer.get('nativeReceiptSha256')!=proof['nativeReceiptSha256'] \
            or outer.get('packageSha256')!=proof['packageSha256'] or outer.get('packageBytes')!=proof['packageBytes']:
        raise ValueError('tool inspection receipt or artifact readback differs')
    inventory=record.get('inventory');count=record.get('entryCount');expanded=record.get('expandedBytes')
    if not isinstance(inventory,list) or type(count) is not int or not 0<count<=MAX_ENTRIES \
            or count!=len(inventory) or type(expanded) is not int or not 0<expanded<=MAX_EXPANDED \
            or canonical_sha(inventory)!=record.get('inventorySha256'):
        raise ValueError('tool inspection inventory identity or bounds differ')
    names=[row.get('path') for row in inventory]
    if any(not isinstance(n,str) for n in names) or names!=sorted(set(names)):
        raise ValueError('tool inspection inventory paths differ')
    if any(row.get('type') not in ('file','directory') or type(row.get('mode')) is not int \
           or not 0<=row['mode']<=0o777 for row in inventory):
        raise ValueError('tool inspection inventory type or mode differs')
    files=[row for row in inventory if row.get('type')=='file']
    if any(type(row.get('sizeBytes')) is not int or row['sizeBytes']<0 or not digest(row.get('sha256')) for row in files) \
            or sum(row['sizeBytes'] for row in files)!=expanded:
        raise ValueError('tool inspection file inventory differs')
    rows={row['path']:row for row in inventory}
    if record.get('requiredExecutables')!={name:rows.get(name) for name in REQUIRED} \
            or any(name not in rows or rows[name].get('type')!='file' or not rows[name]['sizeBytes'] \
                   or type(rows[name].get('mode')) is not int or not rows[name]['mode']&0o111 for name in REQUIRED):
        raise ValueError('tool inspection required executables differ')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path)
    args=parser.parse_args()
    result=inspect_package(args.archive) if args.archive is not None else binding()
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
