#!/usr/bin/env python3
"""Inactive controlled collector integration; CLI prints a plan only.

The library combines the guest probe, captured checkout, prepared source
candidate and a fresh attempt receipt under one existing read-only lock.
Offline fixtures are explicitly identified. No workflow invokes it. Workflow
context checks are not independent run authentication or bootstrap admission.
"""
from dataclasses import asdict,dataclass
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys
import time

import mustang_bootstrap_git as git
import mustang_bootstrap_probe as probe_module
import mustang_bootstrap_proof as proof
import mustang_controller as controller
import mustang_existing_lock as locks
import mustang_hosttools as readers
import mustang_paths

ROOT=Path(__file__).resolve().parents[1]
SCHEMA='roseos.mustang-bootstrap-collector.v1'
RESULT='bootstrap-observation-receipt.json'
JOB='bootstrap-observe'
SCOPE='bootstrap_observe_only'
FLAGS=frozenset(('publish','source_only','dry_run','lock_admin_only','factory_only',
                 'hostdeps_only','vendor_only','otatools_only',SCOPE))
RESERVE_BYTES=64 << 20
RESERVE_INODES=8


@dataclass(frozen=True)
class Limits:
    seconds:int=7200
    checkout_entries:int=100_000
    checkout_bytes:int=1 << 30
    receipt_bytes:int=1 << 20

    def validate(self):
        if any(type(v) is not int or not 1<=v<=asdict(Limits())[k] for k,v in asdict(self).items()):
            raise git.Refused('collector-bounds-differ')
        return self


def check(deadline):
    if time.monotonic()>=deadline:raise git.Refused('collector-deadline')


def lock_signature(path):
    with locks.open_stream(path,expected=path) as stream:
        return proof.signature(os.fstat(stream.fileno()))


def runner_identity():
    uid=os.geteuid()
    return uid,pwd.getpwuid(uid).pw_name


def invocation(lane,env,event):
    run=controller.invocation(lane,env)
    selected=event.get('inputs') if isinstance(event,dict) else None
    enabled=lambda value:value is True or (type(value) is str and value=='true')
    disabled=lambda value:value is None or value is False or (type(value) is str and value=='false')
    if not isinstance(selected,dict) or set(selected)-FLAGS or not enabled(selected.get(SCOPE)) \
            or any(not disabled(selected.get(k)) for k in FLAGS-{SCOPE}):
        raise git.Refused('collector-exclusive-scope-required')
    uid,user=runner_identity()
    if env.get('GITHUB_REPOSITORY')!='Immortal-Protocols/RoseOS-1' \
            or env.get('GITHUB_REPOSITORY_ID')!='1428996648' or env.get('GITHUB_JOB')!=JOB \
            or env.get('GITHUB_WORKFLOW_SHA')!=run['source'] \
            or env.get('MUSTANG_BOOTSTRAP_OBSERVE_ONLY')!='true' \
            or any(env.get(k) not in (None,'false') for k in ('MUSTANG_PUBLISH','MUSTANG_DRY_RUN')) \
            or uid<=0 or user!='mustang-runner' or not sys.dont_write_bytecode:
        raise git.Refused('collector-workflow-context-differs')
    git.oid(run['source'])
    return run


def guest(lane,cfg,paths,root,probe,deadline):
    check(deadline)
    observed=probe.observe(paths['home'],deadline)
    mount=observed.get('mount',{})
    if observed.get('instanceId')!=lane['vm']['instanceId']:
        raise git.Refused('collector-guest-identity-differs')
    if not isinstance(mount,dict) or set(mount)!={'uuid','target','fstype','options','maj:min','fsroot'} \
            or not isinstance(cfg.get('dataFilesystemUuid'),str) or not cfg['dataFilesystemUuid'] \
            or mount.get('uuid')!=cfg['dataFilesystemUuid'] or mount.get('target')!=cfg.get('dataMount') \
            or mount.get('fstype')!='ext4' or mount.get('fsroot')!='/' \
            or not isinstance(mount.get('options'),str) or 'rw' not in mount['options'].split(',') \
            or 'ro' in mount['options'].split(','):
        raise git.Refused('collector-data-mount-differs')
    mount_path=Path(mount['target'])
    if not mount_path.is_absolute() or str(mount_path)!=mount['target'] or mount_path not in paths['home'].parents:
        raise git.Refused('collector-data-mount-differs')
    home=proof.deps.plain_dir(paths['home'])
    device=f'{os.major(home.st_dev)}:{os.minor(home.st_dev)}'
    if mount['maj:min']!=device:raise git.Refused('collector-data-device-differs')
    identities={}
    for name,path in {**paths,'checkout':root,'mount':mount_path}.items():
        if name=='lockPath':continue
        info=proof.deps.plain_dir(path)
        if info.st_dev!=home.st_dev:raise git.Refused('collector-path-crosses-filesystem')
        identities[name]=[info.st_dev,info.st_ino]
    fd=readers._walk_dir(readers._body(str(paths['home'])))
    try:space=os.fstatvfs(fd)
    finally:os.close(fd)
    free=space.f_bavail*(space.f_frsize or space.f_bsize)
    if free<RESERVE_BYTES or space.f_favail<RESERVE_INODES:
        raise git.Refused('collector-receipt-reserve-unavailable')
    check(deadline)
    return dict(builder=dict(lane['vm']),diskUuid=mount['uuid'],mount=mount['target'],
                filesystem=mount['fstype'],device=device,pathIdentities=identities),dict(
                availableBytes=free,availableInodes=space.f_favail,
                receiptReserveBytes=RESERVE_BYTES,receiptReserveInodes=RESERVE_INODES,
                runtimeReserveVerified=False)


def checkout(root,revision,limits,deadline):
    """Read all tracked bytes/modes and reject untracked (including ignored) paths.

    Only the root .git metadata entry is excluded. No status/index, filters,
    hooks, source scripts or archive programs are used.
    """
    bounds=proof.Limits(entries=limits.checkout_entries,bytes=limits.checkout_bytes)
    observation=proof.Observation(root,bounds,deadline=deadline)
    reader=git.LocalGit(deadline)
    expected=reader.project(root,revision,max_entries=limits.checkout_entries)
    seen=set();inventory=[]
    rootfd=readers._walk_dir(readers._body(str(root)))
    def failed(_error):raise git.Refused('collector-checkout-walk-incomplete')
    try:
        for directory,dirs,files,parent in os.fwalk('.',topdown=True,dir_fd=rootfd,follow_symlinks=False,onerror=failed):
            observation.check();prefix='' if directory=='.' else directory[2:]
            observation.signatures[prefix or '.']=proof.signature(os.fstat(parent))
            for name in sorted(dirs+files):
                if not prefix and name=='.git':
                    if name in dirs:dirs.remove(name)
                    continue
                observation.count();relative=prefix+'/'+name if prefix else name
                row=expected.get(relative)
                if row is None:raise git.Refused('collector-checkout-untracked-path')
                info=os.stat(name,dir_fd=parent,follow_symlinks=False);mode=row['mode']
                if info.st_dev!=observation.device or stat.S_IMODE(info.st_mode)&0o7000:
                    raise git.Refused('collector-checkout-entry-differs')
                if mode=='040000':
                    if not stat.S_ISDIR(info.st_mode):raise git.Refused('collector-checkout-entry-differs')
                elif mode=='120000':
                    if not stat.S_ISLNK(info.st_mode):raise git.Refused('collector-checkout-entry-differs')
                    raw=os.fsencode(os.readlink(name,dir_fd=parent))
                    observation.bytes+=len(raw)
                    if observation.bytes>limits.checkout_bytes:raise git.Refused('collector-checkout-byte-bound')
                    if git.object_id('blob',raw)!=row['oid']:raise git.Refused('collector-checkout-blob-differs')
                else:
                    if not stat.S_ISREG(info.st_mode):raise git.Refused('collector-checkout-entry-differs')
                    measured=observation.measure(root/relative,info)
                    if measured['blob']!=row['oid'] or bool(info.st_mode&stat.S_IXUSR)!=(mode=='100755'):
                        raise git.Refused('collector-checkout-blob-differs')
                if proof.signature(info)!=proof.signature(os.stat(name,dir_fd=parent,follow_symlinks=False)):
                    raise git.Refused('collector-checkout-entry-changed')
                observation.signatures[relative]=proof.signature(info);seen.add(relative)
                inventory.append(dict(path=relative,mode=mode,oid=row['oid']))
    finally:os.close(rootfd)
    if seen!=set(expected):raise git.Refused('collector-checkout-tracked-path-missing')
    reader.check_head(root,revision);observation.verify_namespace()
    inventory.sort(key=lambda row:row['path'])
    return dict(source=revision,trackedEntries=len(inventory),bytesRead=observation.bytes,
                gitOutputBytesRead=reader.bytes_read,gitIndexTrusted=False,
                inventorySha256=hashlib.sha256(proof.canonical(inventory)).hexdigest()),observation,reader


class Attempt:
    """Private, exclusive attempt; immutable receipt, no adoption or cleanup of old data."""
    def __init__(self,storage,run):
        if not isinstance(run,dict) or run.get('runAttempt')!='1' \
                or not isinstance(run.get('runId'),str) or not re.fullmatch(r'[1-9][0-9]{0,19}',run['runId']):
            raise git.Refused('collector-attempt-run-differs')
        self.storage=Path(storage)
        self.name=f"mustang-bootstrap-observation-{run['runId']}-{run['runAttempt']}"
        self.path=self.storage/self.name;self.parent=None;self.fd=None

    def __enter__(self):
        self.parent=readers._walk_dir(readers._body(str(self.storage)))
        try:
            os.mkdir(self.name,mode=0o700,dir_fd=self.parent)
            self.fd=os.open(self.name,readers._DIR_FLAGS,dir_fd=self.parent)
            self.identity=os.fstat(self.fd)
            if self.identity.st_dev!=os.fstat(self.parent).st_dev:
                raise git.Refused('collector-attempt-crosses-filesystem')
            os.fsync(self.parent)
            return self
        except BaseException:
            self.__exit__(None,None,None)
            raise

    def verify(self):
        fd=readers._walk_dir(readers._body(str(self.storage)))
        try:
            original=os.fstat(self.parent);current=os.fstat(fd)
            info=os.stat(self.name,dir_fd=fd,follow_symlinks=False)
            if (original.st_dev,original.st_ino)!=(current.st_dev,current.st_ino) \
                    or not stat.S_ISDIR(info.st_mode) \
                    or (info.st_dev,info.st_ino)!=(self.identity.st_dev,self.identity.st_ino):
                raise git.Refused('collector-attempt-path-changed')
        finally:os.close(fd)

    def write(self,document,maximum,*,deadline=None):
        if deadline is not None:check(deadline)
        self.verify();raw=proof.canonical(document)
        if len(raw)>maximum:raise git.Refused('collector-receipt-byte-bound')
        temporary='.receipt.pending'
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.fd)
        with os.fdopen(fd,'wb') as output:
            output.write(raw);output.flush();os.fsync(output.fileno())
        self.verify()
        if deadline is not None:check(deadline)
        os.link(temporary,RESULT,src_dir_fd=self.fd,dst_dir_fd=self.fd,follow_symlinks=False)
        os.unlink(temporary,dir_fd=self.fd);os.fsync(self.fd);self.verify()
        actual=readers.read_regular(str(self.storage),str(self.path/RESULT),maximum)
        if actual!=raw:raise git.Refused('collector-receipt-readback-differs')
        if deadline is not None:check(deadline)
        return dict(path=str(self.path/RESULT),sha256=hashlib.sha256(raw).hexdigest(),sizeBytes=len(raw))

    def __exit__(self,*_args):
        for fd in (self.fd,self.parent):
            if fd is not None:os.close(fd)
        self.fd=self.parent=None


def collect(lane,cfg,env,event,*,root=ROOT,limits=None,source_limits=None,probe=None):
    """Unwired library entry point. Future activation requires separate authorization.

    A refusal before a safe fresh attempt exists is returned without a receipt.
    After claim, failures are sanitized into the same immutable receipt. Claimed
    attempts are never resumed or removed, including after interruption.
    """
    started=time.monotonic();stage='scope';completed=[];record=None
    document=dict(schema=SCHEMA,scope='selected-source-candidate-observation-only',success=False,
        runtimeWired=False,fullBootstrapVerified=False,pristine=False,pristineRuntimeAttestation=False,
        readyForControlledStart=False,otaAdmitted=False,signingStarted=False,publicationStarted=False,
        runIdentityObserved=False,workflowContextValidated=False,guestIdentityObserved=False,
        dataFilesystemIdentityObserved=False,authenticatedStop=False,stopOwnedByCollector=False,
        hostedLifecyclePending=True)
    try:
        run=invocation(lane,env,event);limits=(limits or Limits()).validate()
        source_limits=(source_limits or proof.Limits()).validate()
        deadline=started+limits.seconds;paths=mustang_paths.layout(lane);root=Path(root)
        if env.get('GITHUB_WORKSPACE')!=str(root) or root==paths['sourceRoot'] \
                or any(p==root or p in root.parents for p in (paths['sourceRoot'],paths['storageRoot'])):
            raise git.Refused('collector-checkout-location-differs')
        probe=probe or probe_module.LocalGuestProbe()
        if probe.kind not in ('offline-fixture','native-read-only'):raise git.Refused('collector-probe-kind-differs')
        document.update(run=run,workflowContextValidated=True,observationKind=probe.kind,
                        limits=asdict(limits),sourceLimits=asdict(source_limits),
                        boundsKind='proposal-ceilings-not-measurements')
        stage='lock'
        with locks.shared(paths['lockPath'],expected=paths['lockPath']):
            canonical_lock=lock_signature(paths['lockPath'])
            stage='guest-before'
            identity,space=guest(lane,cfg,paths,root,probe,deadline)
            document.update(identity=identity,spaceBefore=space)
            completed.append(stage);stage='claim'
            with Attempt(paths['storageRoot'],run) as attempt:
                try:
                    stage='checkout'
                    measured,snapshot,reader=checkout(root,run['source'],limits,deadline)
                    document['checkout']=measured;completed.append(stage)
                    stage='bindings'
                    for relative,value in (('os/build-only/mustang-lane.json',lane),('os/build-only/mustang-runtime.json',cfg)):
                        if json.loads(readers.read_regular(str(root),str(root/relative),4 << 20))!=value:
                            raise git.Refused('collector-captured-binding-differs')
                    check(deadline);completed.append(stage);stage='source'
                    candidate=proof._observe_prepared_locked(
                        lane,cfg,run,root=root,limits=source_limits,deadline=deadline)
                    if candidate.get('schema')!='roseos.mustang-bootstrap-source-candidate.v1' \
                            or any(candidate.get(k) is not True for k in ('success','selectedPinnedBytesVerified',
                                'preparedPayloadsVerified','preparedReceiptProvenanceVerified','selectedNamespaceReadback')) \
                            or candidate.get('observationRun')!=run or candidate.get('sourceRoot')!=str(paths['sourceRoot']) \
                            or any(candidate.get(k) is not False for k in ('runtimeWired','fullBootstrapVerified',
                                'pristine','pristineRuntimeAttestation','readyForControlledStart','otaAdmitted','signingStarted')):
                        raise git.Refused('collector-source-candidate-differs')
                    document['sourceCandidate']=candidate
                    completed.append(stage);stage='readback'
                    reader.check_head(root,run['source']);snapshot.verify_namespace()
                    after,space=guest(lane,cfg,paths,root,probe,deadline)
                    if after!=identity:raise git.Refused('collector-identity-changed')
                    document['spaceAfter']=space;completed.append(stage);check(deadline)
                    native=probe.kind=='native-read-only'
                    document.update(success=True,guestChecksPassed=True,checkoutBytesVerified=True,
                        guestIdentityObserved=native,dataFilesystemIdentityObserved=native)
                except Exception as error:
                    document['failure']=error.code if isinstance(error,git.Refused) else 'collector-check-failed'
                document.update(stage=stage,completedChecks=completed,elapsedSeconds=round(time.monotonic()-started,3))
                stage='receipt'
                if lock_signature(paths['lockPath'])!=canonical_lock:
                    raise git.Refused('collector-canonical-lock-changed')
                record=attempt.write(document,limits.receipt_bytes,deadline=deadline if document['success'] else None)
    except Exception as error:
        # Do not include arbitrary exception text, subprocess stderr, payloads,
        # tokens or provider URLs. A receipt-write refusal never claims success.
        document.update(success=False,failure=error.code if isinstance(error,git.Refused) else 'collector-refused',
                        stage=stage,completedChecks=completed,elapsedSeconds=round(time.monotonic()-started,3))
    return dict(receipt=document,receiptWritten=record is not None,receiptFile=record)


def main():
    import argparse
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(dict(schema=SCHEMA,execute=False,runtimeWired=False,liveObservationEnabled=False,
        scopeInput=SCOPE,job=JOB,limits=asdict(Limits()),sourceLimits=asdict(proof.Limits()),
        readyForControlledStart=False,fullBootstrapVerified=False,otaAdmitted=False),sort_keys=True))


if __name__=='__main__':main()
