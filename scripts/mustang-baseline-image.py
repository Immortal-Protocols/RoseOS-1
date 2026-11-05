#!/usr/bin/env python3
"""Focused baseline evidence from one admitted factory artifact, never a downloader.

No flash/install scripts, signing, keys, VM/controller, mount or network operation.
The input stays unbound until an actual approved local transfer and tools exist.
This proves only baseline identity, AVB image integrity, Updater and system trust.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import sys
import time
from types import SimpleNamespace
import zipfile

import mustang_ota as ota
import mustang_paths
import release_receipts

ROOT=Path(__file__).resolve().parents[1]
SCHEMA='roseos.mustang-factory-baseline-input.v1'
RECEIPT='roseos.mustang-factory-baseline-observation.v1'
KIND='factory-images'
BASE='os/mustang-delivery/41738359336/'
ARTIFACT_ID=13280698927
CORE={'simg2img','lpunpack','avbtool','aapt','apksigner'}
READERS={'debugfs','fsckErofs'}
CHECKS={'archiveProvenance','archiveSafety','avbIntegrity','baselineIdentity','packagedUpdater','baselineTrust'}
SPARSE=b'\x3a\xff\x26\xed'
LIMITATIONS=dict(factoryArchiveSignature='not-recorded; original omission preserved',
    factoryContentComparison='not-performed; original target-files unavailable',firmwareCoverage='not-verified')


def referenced(ref,root):
    if not isinstance(ref,dict) or set(ref)!={'path','sha256'}:
        raise ValueError('hash-bound committed recovery evidence required')
    path=Path(ref['path'])
    if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!='os' \
            or (Path(root)/path).is_symlink() or not (Path(root)/path).resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('recovery evidence must stay under committed os/')
    if ota.digest(Path(root)/path)!=ref['sha256']: raise ValueError('recovery evidence hash differs')
    return ota.read(Path(root)/path)


def source_pins(root):
    delivery=ota.read(Path(root)/BASE/'delivery.json'); package=ota.read(Path(root)/BASE/'package.json')
    signed=ota.read(Path(root)/BASE/'sign.json')
    metadata=ota.read(Path(root)/BASE/'artifacts.json')
    rows=[r for r in metadata['artifacts'] if r['id']==ARTIFACT_ID]
    if len(rows)!=1 or delivery['artifactId']!=ARTIFACT_ID or metadata['runId']!='41738359336' \
            or package['build']!='2026110302' or package['device']!='mustang' \
            or package.get('status')!='packaged-candidate' or delivery.get('downloadChecksumsVerified') is not True \
            or signed.get('status')!='signed-command-complete' \
            or any(signed.get(k)!=package.get(k) for k in ('device','build','candidateSource')) \
            or package['output']['file']!='mustang-install-2026110302.zip':
        raise ValueError('committed factory source identities differ')
    inner={k:package['output'][k] for k in ('file','sha256','sizeBytes')}
    bound=[r for r in delivery['files'] if r['file']==inner['file']]
    if bound!=[inner] or delivery['build']!=package['build'] or delivery['candidateSource']!=package['candidateSource']:
        raise ValueError('committed delivery/package binding differs')
    row=rows[0]
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',row['digest']) or row['sizeBytes']!=delivery['artifactSizeBytes']:
        raise ValueError('committed artifact digest/size differs')
    baseline=ota.read(Path(root)/'os/mustang-ota/41761758623/baseline.json')
    stage=ota.read(Path(root)/'os/mustang-ota/41761758623/mustang-ota-receipt.json')
    publication=ota.read(Path(root)/'os/mustang-ota/41761758623/publication.json')
    if ota.digest(Path(root)/'os/mustang-ota/41761758623/baseline.json')!=stage['baselineObservationSha256'] \
            or ota.digest(Path(root)/'os/mustang-ota/41761758623/mustang-ota-receipt.json')!=publication['stageReceiptSha256'] \
            or baseline['targetFiles']!=signed['outputs']['targetFiles'] \
            or publication.get('status')!='ota-published' or publication.get('publicDownloadVerified') is not True:
        raise ValueError('historical baseline observation is not bound by its stage')
    return dict(artifact=dict(id=ARTIFACT_ID,runId='41738359336',sha256=row['digest'][7:],sizeBytes=row['sizeBytes']),
        installer=inner,files=delivery['files'],baseline=baseline,baselineSource=package['candidateSource'],
        sourceRecords={name:ota.digest(Path(root)/name) for name in
            (BASE+'delivery.json',BASE+'package.json',BASE+'sign.json',BASE+'artifacts.json',
             'os/mustang-ota/41761758623/baseline.json','os/mustang-ota/41761758623/mustang-ota-receipt.json',
             'os/mustang-ota/41761758623/publication.json')})


def contract(value,lane,runtime,root=ROOT):
    """Hosted/source-only admission. Does not inspect a guest or authorize a transfer."""
    if value.get('schema')!=SCHEMA or value.get('status')!='bound' \
            or runtime.get('schema')!='roseos.mustang-dedicated-runtime.v1' or runtime.get('status')!='bound':
        raise ValueError('factory baseline recovery is unbound')
    pins=source_pins(root); home=mustang_paths.layout(lane)['home']
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+',str(home)):
        raise ValueError('baseline data home must be a plain tool-command path')
    archive=dict(pins['artifact'],path=str(home/'inputs/baseline/13280698927.zip'))
    if value.get('archive')!=archive or value.get('installer')!=pins['installer'] \
            or value.get('sourceRecords')!=pins['sourceRecords']:
        raise ValueError('factory recovery archive/source provenance differs')
    transfer=referenced(value.get('transferEvidence'),root)
    if transfer.get('schema')!='roseos.mustang-baseline-transfer.v1' or transfer.get('approved') is not True \
            or transfer.get('completed') is not True or transfer.get('archive')!=archive \
            or transfer.get('builder')!=lane['vm'] or transfer.get('dataFilesystemUuid')!=runtime['dataFilesystemUuid'] \
            or any(not isinstance(transfer.get(k),str) or not transfer[k].strip()
                   for k in ('approvalRef','retrievalApprovalRef','evidenceRef')):
        raise ValueError('factory baseline transfer approval/readback differs')
    tools=value.get('tools',{})
    if not CORE<=set(tools) or set(tools)-CORE-READERS or not set(tools)&READERS:
        raise ValueError('verified recovery tools and filesystem reader required')
    for row in tools.values():
        if set(row)!={'path','sha256'} or not isinstance(row['path'],str) or not re.fullmatch(r'/[\w./-]+',row['path']) \
                or '..' in Path(row['path']).parts or not re.fullmatch(r'[0-9a-f]{64}',str(row['sha256'])):
            raise ValueError('recovery tool path/hash is unbound')
    if any(tools[k]['path']!=runtime['tools'][k] for k in ('aapt','apksigner')):
        raise ValueError('baseline SDK paths differ from admitted Mustang tools')
    limits=value.get('limits',{})
    if set(limits)!={'seconds','workBytes','reserveBytes'} or any(type(x) is not int or x<=0 for x in limits.values()):
        raise ValueError('actual recovery time/work/reserve limits required')
    cfg=ota.read(Path(root)/'os/ota/mustang.json')['baseline']
    if any(pins['baseline'][k]!=cfg[k] for k in ('build','timestamp')) or cfg['source']!=pins['baselineSource']:
        raise ValueError('factory recovery is not the configured baseline')
    return pins


def plain(path,home=None):
    path=Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)) or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('recovery input is not a regular no-link file')
    if home is not None and path.stat().st_dev!=Path(home).stat().st_dev:
        raise ValueError('recovery archive is not on the admitted data filesystem')
    return path


def copy_stream(source,target,limit,check=None):
    count=0
    with open(target,'xb') as dest:
        while True:
            block=source.read(min(1<<20,limit-count+1))
            if not block: break
            count+=len(block)
            if count>limit: raise ValueError('recovery copy exceeds its size bound')
            if check: check(len(block))
            dest.write(block)
    return count


def index(archive,top=None):
    rows={}; seen=set()
    for row in archive.infolist():
        parts=row.filename.split('/')
        if row.filename in seen or row.filename.startswith('/') or '\\' in row.filename or '..' in parts or '\0' in row.filename \
                or stat.S_ISLNK(row.external_attr>>16) or row.flag_bits&1:
            raise ValueError('unsafe factory archive entry')
        seen.add(row.filename)
        if top is not None:
            if parts[0]!=top or len(parts) not in (1,2): raise ValueError('factory archive directory differs')
            name=parts[1] if len(parts)==2 else ''
        else: name=row.filename
        if row.is_dir():
            if top is None or row.filename!=top+'/': raise ValueError('unexpected factory directory')
            continue
        mode=stat.S_IFMT(row.external_attr>>16)
        if mode not in (0,stat.S_IFREG) or not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or name in rows:
            raise ValueError('factory archive entry type/name/uniqueness differs')
        rows[name]=row
    return rows


class Tools:
    def __init__(self,value,work,supervise):
        self.value=value; self.work=work; self.supervise=supervise; self.used={}; self.runs=[]
        self.deadline=time.monotonic()+value['limits']['seconds']

    def check(self,extra=0):
        limits=self.value['limits']; fs=os.statvfs(self.work)
        used=sum(p.stat().st_size for p in self.work.rglob('*') if p.is_file() and not p.is_symlink())
        if time.monotonic()>=self.deadline or used+extra>limits['workBytes'] \
                or fs.f_bavail*fs.f_frsize-extra<limits['reserveBytes'] or fs.f_favail<=0:
            raise ValueError('factory verification time/work/space bound reached')

    def copy(self,source,target,limit):
        return copy_stream(source,target,limit,self.check)

    def verified(self,name):
        row=self.value['tools'].get(name)
        if row is None: raise ValueError('required filesystem tool unavailable: '+name)
        path=plain(row['path'])
        if ota.digest(path)!=row['sha256'] or (path.suffix!='.py' and not os.access(path,os.X_OK)):
            raise ValueError('recovery tool bytes/executable differ: '+name)
        return path,row

    def run(self,name,args):
        self.check()
        path,row=self.verified(name)
        self.used[name]=dict(row)
        prefix=[sys.executable,str(path)] if path.suffix=='.py' else [str(path)]
        limits=self.value['limits']; left=self.deadline-time.monotonic()
        if left<=0: raise ValueError('factory baseline verification timed out')
        result=self.supervise(prefix+list(map(str,args)),str(self.work),
            {'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C','HOME':str(self.work)},left,
            dict(sampleSeconds=1,killGraceSeconds=2,boundBytes=limits['workBytes'],
                 reserveBytes=limits['reserveBytes'],checkReserve=True),watch=(str(self.work),),capture=True)
        self.runs.append(dict(tool=name,returncode=result['returncode'],stopped=result.get('stopped'),
                              enforcement=result.get('enforcement','unreported')))
        if result['returncode']!=0 or result.get('stopped') or result.get('consumedBytes',0)>limits['workBytes']:
            raise ValueError('recovery tool failed or exceeded its bound: '+name)
        self.check()
        return result.get('stdout','')

    def execute(self,argv,**kwargs):
        name=next((k for k in ('aapt','apksigner') if self.value['tools'][k]['path']==argv[0]),None)
        if name is None: raise ValueError('unexpected baseline SDK command')
        return SimpleNamespace(returncode=0,stdout=self.run(name,argv[1:]))


def sparse_size(path,limit):
    with open(path,'rb') as f: head=f.read(28)
    if head[:4]!=SPARSE: return None
    if len(head)!=28: raise ValueError('truncated sparse image')
    _,major,minor,fh,ch,block,blocks,chunks,checksum=struct.unpack('<I4H4I',head)
    size=block*blocks
    if major!=1 or fh<28 or ch<12 or not block or block%4 or not size or size>limit:
        raise ValueError('sparse image header/bound differs')
    return size


def unpack(installer,work,runner):
    images=work/'images'; images.mkdir(); segments=[]; limit=runner.value['limits']['workBytes']
    with zipfile.ZipFile(installer) as archive:
        rows=index(archive,'mustang-install-2026110302')
        if not {'script.txt','flash-all.sh','android-info.txt','avb_pkmd.bin','vbmeta.img'}<=set(rows):
            raise ValueError('factory installer required members missing')
        board=ota.member(archive,rows['android-info.txt'].filename,1<<20).decode().splitlines()
        if [line.strip() for line in board if line.strip().startswith('require board=')]!=['require board=mustang']:
            raise ValueError('factory installer board differs')
        numbered=sorted((int(m[1]),name) for name in rows if (m:=re.fullmatch(r'super_([1-9][0-9]*)\.img',name)))
        if not numbered or [n for n,_ in numbered]!=list(range(1,len(numbered)+1)):
            raise ValueError('factory super segments are missing or noncontiguous')
        selected={name for name in rows if re.fullmatch(r'[a-z][a-z0-9_]*\.img',name)}|{'avb_pkmd.bin'}
        if sum(rows[n].file_size for n in selected)>limit: raise ValueError('factory image set exceeds work bound')
        for name in sorted(selected):
            target=images/name
            with archive.open(rows[name]) as source: runner.copy(source,target,min(limit,rows[name].file_size))
        segments=[images/name for _,name in numbered]
    sizes=[sparse_size(p,limit) for p in segments]
    if any(x is None for x in sizes) or len(set(sizes))!=1: raise ValueError('factory super sparse geometry differs')
    raw=work/'super.raw'; runner.run('simg2img',[*segments,raw])
    if plain(raw).stat().st_size!=sizes[0]: raise ValueError('reconstructed super size differs')
    partitions=work/'partitions'; partitions.mkdir(); runner.run('lpunpack',[raw,partitions])
    raw.unlink()
    for file in sorted(partitions.iterdir()):
        plain(file); match=re.fullmatch(r'([a-z][a-z0-9_]*?)(_[ab])?\.img',file.name)
        if not match: raise ValueError('unrecognized logical partition')
        if match[2]=='_b':
            if file.stat().st_size: raise ValueError('nonempty inactive logical partition')
            continue
        target=images/(match[1]+'.img')
        if target.exists(): raise ValueError('duplicate logical/physical partition')
        shutil.move(file,target)
    for file in sorted(images.glob('*.img')):
        if re.fullmatch(r'super_[0-9]+\.img',file.name): continue
        size=sparse_size(file,limit)
        if size is not None:
            flat=file.with_suffix('.raw'); runner.run('simg2img',[file,flat])
            if plain(flat).stat().st_size!=size: raise ValueError('flattened image size differs')
            os.replace(flat,file)
    if not all((images/(p+'.img')).is_file() for p in ('system','vendor','vbmeta')):
        raise ValueError('baseline system/vendor/vbmeta images missing')
    return images


def avb_descriptors(data):
    """Bounds-check the AVB descriptor area; signatures/hashes remain avbtool's job.

    AVB v1 header and hash/hashtree/chain descriptor layouts from upstream
    libavb/avb_{vbmeta_image,hash,hashtree,chain_partition}_descriptor.h.
    """
    auth,aux=struct.unpack('>QQ',data[12:28]); offset,size=struct.unpack('>QQ',data[96:112])
    if offset+size>aux or 256+auth+aux>len(data): raise ValueError('vbmeta descriptor area escapes auxiliary block')
    area=data[256+auth+offset:256+auth+offset+size]; cursor=0
    while cursor<len(area):
        if cursor+16>len(area): raise ValueError('truncated AVB descriptor')
        tag,following=struct.unpack('>QQ',area[cursor:cursor+16]); end=cursor+16+following
        if following%8 or end>len(area): raise ValueError('AVB descriptor length differs')
        row=area[cursor:end]; cursor=end
        if tag not in (1,2,4): continue
        fixed,length_offset={1:(180,104),2:(132,56),4:(92,20)}[tag]
        if len(row)<fixed: raise ValueError('truncated typed AVB descriptor')
        if tag==4: name_size,key_size=struct.unpack('>II',row[length_offset:length_offset+8]); rest=key_size
        else:
            name_size,salt,digest=struct.unpack('>III',row[length_offset:length_offset+12]); rest=salt+digest
            if digest==0: raise ValueError('persistent AVB digest cannot be verified offline')
        used=fixed+name_size+rest
        if used>len(row) or ((used+7)//8)*8!=len(row): raise ValueError('AVB descriptor data length differs')
        name=row[fixed:fixed+name_size].decode('ascii')
        if not re.fullmatch(r'[a-z][a-z0-9_]*',name): raise ValueError('unsafe AVB partition name')
        yield tag,name,row[fixed+name_size:used] if tag==4 else None


def verify_avb(images,runner,pins):
    hp=ota.module('mustang_baseline_public_key','host-factory-package.py')
    public_file=plain(images/'avb_pkmd.bin')
    if public_file.stat().st_size>hp.LIMITS['vbmetaBytes']: raise ValueError('factory AVB key exceeds metadata bound')
    public=public_file.read_bytes()
    if hashlib.sha256(public).hexdigest()!=pins['avbPublicKeySha256']:
        raise ValueError('factory public AVB identity differs')
    pending=[('vbmeta.img',public)]; seen=set(); covered=set()
    while pending:
        name,key=pending.pop()
        if name in seen: raise ValueError('duplicate/cyclic AVB chain')
        seen.add(name); file=plain(images/name)
        if file.stat().st_size>hp.LIMITS['vbmetaBytes']: raise ValueError('factory vbmeta exceeds metadata bound')
        data=file.read_bytes()
        if len(data)<256 or data[:4]!=b'AVB0' or struct.unpack('>I',data[28:32])[0]==0 \
                or struct.unpack('>I',data[120:124])[0]!=0:
            raise ValueError('unsigned or verification-disabled vbmeta')
        if hp.avb_public_key(data)!=key:
            raise ValueError('vbmeta embedded public key differs')
        for tag,partition,child_key in avb_descriptors(data):
            plain(images/(partition+'.img'))
            if tag==4: pending.append((partition+'.img',child_key))
            elif partition in covered: raise ValueError('duplicate authenticated AVB partition')
            else: covered.add(partition)
    if not {'system','vendor'}<=covered:
        raise ValueError('AVB descriptors do not authenticate baseline system and vendor')
    runner.run('avbtool',['verify_image','--image',images/'vbmeta.img','--follow_chain_partitions'])
    return seen|{name+'.img' for name in covered}


def component(image,inside,out,runner,limit):
    with open(image,'rb') as f: header=f.read(4096)
    if header[1080:1082]==b'\x53\xef':
        runner.run('debugfs',['-R',f'dump -p {inside} {out}',image])
    elif header[1024:1028]==struct.pack('<I',0xE0F5E1E2):
        # Same unprivileged extraction mechanism as alpha_image, after AVB.
        directory=image.with_suffix('.extract')
        if not directory.exists():
            directory.mkdir(); runner.run('fsckErofs',[f'--extract={directory}',image])
        source=plain(directory/inside.lstrip('/'))
        if not source.resolve().is_relative_to(directory.resolve()): raise ValueError('extracted component escapes image')
        with source.open('rb') as src: runner.copy(src,out,limit)
    else: raise ValueError('unsupported baseline filesystem')
    plain(out)
    if out.stat().st_size>limit: raise ValueError('baseline component exceeds size bound')
    return out.read_bytes()


def observe(value,lane,runtime,out,root=ROOT,supervise=None):
    pins=contract(value,lane,runtime,root); paths=mustang_paths.layout(lane)
    run={k:os.environ.get(k,'') for k in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_SHA')}
    if not re.fullmatch(r'[1-9][0-9]*',run['GITHUB_RUN_ID']) or run['GITHUB_RUN_ATTEMPT']!='1' \
            or not re.fullmatch(r'[0-9a-f]{40}',run['GITHUB_SHA']):
        raise ValueError('fresh baseline observation run/source required')
    if os.path.lexists(out): raise ValueError('baseline observation already exists; no overwrite')
    work=paths['storageRoot']/f'mustang-baseline-{run["GITHUB_RUN_ID"]}-1'
    if any(p.is_symlink() for p in (work,*work.parents)):
        raise ValueError('baseline scratch traverses a link')
    work.mkdir(mode=0o700)
    record=dict(schema=RECEIPT,evidenceKind=KIND,status='failed',device='mustang',build='2026110302',
        timestamp=pins['baseline']['timestamp'],builder=lane['vm'],dataFilesystemUuid=runtime['dataFilesystemUuid'],
        baselineSource=pins['baselineSource'],
        runId=run['GITHUB_RUN_ID'],runAttempt='1',recipeSource=run['GITHUB_SHA'],archive=value['archive'],
        installer=pins['installer'],sourceRecords=pins['sourceRecords'],transferEvidence=value['transferEvidence'],
        **LIMITATIONS,phoneObserved=False)
    runner=None
    try:
        if supervise is None: supervise=ota.module('mustang_baseline_bounds','mustang-build-only.py').engine().supervise
        runner=Tools(value,work,supervise)
        for name in value['tools']: runner.verified(name)
        record['phase']='archive-provenance'
        source=plain(value['archive']['path'],paths['home'])
        if source.stat().st_size!=value['archive']['sizeBytes']: raise ValueError('factory outer size differs')
        outer=work/'artifact.zip'
        with os.fdopen(os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as src:
            if not stat.S_ISREG(os.fstat(src.fileno()).st_mode): raise ValueError('factory archive type changed')
            runner.copy(src,outer,value['archive']['sizeBytes'])
        if ota.digest(outer)!=value['archive']['sha256']: raise ValueError('factory outer digest differs')
        installer=work/pins['installer']['file']
        with zipfile.ZipFile(outer) as archive:
            rows=index(archive)
            expected={r['file']:r for r in pins['files']}
            if set(rows)!=set(expected)|{'SHA256SUMS'}: raise ValueError('factory artifact inventory differs')
            for name,row in expected.items():
                if rows[name].file_size!=row['sizeBytes']: raise ValueError('factory artifact member size differs')
                path=work/name
                with archive.open(rows[name]) as src: runner.copy(src,path,row['sizeBytes'])
                if ota.digest(path)!=row['sha256']: raise ValueError('factory artifact member digest differs')
            sums=ota.member(archive,'SHA256SUMS',1<<20).decode().splitlines()
            if set(sums)!={f'{r["sha256"]}  {n}' for n,r in expected.items()} or len(sums)!=len(expected):
                raise ValueError('factory artifact checksum manifest differs')
        record['phase']='unpack-images'; images=unpack(installer,work,runner)
        record['phase']='verify-avb'
        public=ota.identities(); authenticated=verify_avb(images,runner,public)
        record['phase']='extract-components'
        vendor=component(images/'vendor.img','/build.prop',work/'vendor.prop',runner,4<<20)
        apk=component(images/'system.img','/system/priv-app/Updater/Updater.apk',work/'Updater.apk',runner,128<<20)
        trust=component(images/'system.img','/system/etc/security/otacerts.zip',work/'otacerts.zip',runner,4<<20)
        if hashlib.sha256(apk).hexdigest()!=pins['baseline']['updaterApkSha256']:
            raise ValueError('baseline Updater bytes differ from historical observation')
        record['phase']='verify-components'
        observed=ota.observe_components(vendor,apk,trust,'2026110302',pins['baseline']['timestamp'],
            {k:value['tools'][k]['path'] for k in ('aapt','apksigner')},public,runner.execute,work,origin='factory-image')
        if observed['trustedOtaCertificateSha256']!=pins['baseline']['trustedOtaCertificateSha256']:
            raise ValueError('baseline trust set differs from historical observation')
        record.update(observed,status='passed',phase='complete',checks={k:True for k in sorted(CHECKS)},
            checkerSha256=ota.digest(Path(__file__)),avbPublicKeySha256=hashlib.sha256((images/'avb_pkmd.bin').read_bytes()).hexdigest(),
            images={name:ota.digest(images/name) for name in sorted(authenticated)},
            components={name:hashlib.sha256(data).hexdigest() for name,data in
                        (('vendorProperties',vendor),('updater',apk),('otaTrustStore',trust))})
        return record
    except Exception as error:
        record['errorCategory']=type(error).__name__
        if isinstance(error,ValueError): record['reason']=str(error)
        raise
    finally:
        if runner is not None: record.update(tools=runner.used,toolRuns=runner.runs)
        if record['status']=='passed':
            try: shutil.rmtree(work)
            except OSError:
                record.update(status='failed',errorCategory='CleanupFailure')
        record['workRetained']=work.exists()
        release_receipts.write_once(out,record)
        if record.get('errorCategory')=='CleanupFailure': raise ValueError('factory baseline scratch cleanup failed')


def require_receipt(record,value,lane,runtime,root=ROOT):
    """Validate the embedded baseline proof again before staging/public credentials."""
    pins=contract(value,lane,runtime,root)
    if record.get('schema')!=RECEIPT or record.get('evidenceKind')!=KIND or record.get('status')!='passed' \
            or record.get('phase')!='complete' \
            or record.get('archive')!=value['archive'] or record.get('installer')!=pins['installer'] \
            or record.get('sourceRecords')!=pins['sourceRecords'] or record.get('transferEvidence')!=value['transferEvidence'] \
            or record.get('baselineSource')!=pins['baselineSource'] \
            or record.get('builder')!=lane['vm'] or record.get('dataFilesystemUuid')!=runtime['dataFilesystemUuid'] \
            or record.get('checks')!={k:True for k in CHECKS} or any(v is not True for v in record['checks'].values()) \
            or record.get('checkerSha256')!=ota.digest(Path(__file__)) \
            or record.get('avbPublicKeySha256')!=ota.identities()['avbPublicKeySha256'] \
            or record.get('phoneObserved') is not False \
            or record.get('workRetained') is not False or any(record.get(k)!=v for k,v in LIMITATIONS.items()) \
            or any(record.get(k)!=pins['baseline'][k] for k in
                   ('device','build','timestamp','updaterApkSha256','updaterResources','updaterSignerSha256',
                    'trustedOtaCertificateSha256','discoveryClassesPresent','packagedUpdaterVerified')):
        raise ValueError('complete hash-bound factory-image baseline evidence required')
    if any(record.get(k)!=os.environ.get(env) for k,env in
           (('runId','GITHUB_RUN_ID'),('runAttempt','GITHUB_RUN_ATTEMPT'),('recipeSource','GITHUB_SHA'))):
        raise ValueError('factory baseline proof belongs to another run/source')
    if not CORE<=set(record.get('tools',{})) or not set(record['tools'])<=set(value['tools']) \
            or any(row!=value['tools'].get(name) for name,row in record['tools'].items()) \
            or not set(record['tools'])&READERS or not {'system.img','vendor.img','vbmeta.img'}<=set(record.get('images',{})) \
            or set(record.get('components',{}))!={'vendorProperties','updater','otaTrustStore'} \
            or record['components']['updater']!=record['updaterApkSha256'] \
            or any(not re.fullmatch(r'[0-9a-f]{64}',str(v)) for v in
                   [*record['images'].values(),*record['components'].values()]):
        raise ValueError('factory baseline image/component/tool proof incomplete')
    runs=record.get('toolRuns')
    if not isinstance(runs,list) or any(not isinstance(r,dict) for r in runs) \
            or not CORE<={r.get('tool') for r in runs} or any(
            set(r)!={'tool','returncode','stopped','enforcement'} or r.get('tool') not in record['tools'] \
            or type(r.get('returncode')) is not int or r['returncode']!=0 \
            or r.get('stopped') is not None or r.get('enforcement')!='sampled-stop' for r in runs):
        raise ValueError('factory baseline bounded tool outcomes incomplete')
    return record
