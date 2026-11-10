"""Verify the prepared Mustang vendor in a new dedicated source copy.

No generation, download, patch, allocation, signing or lifecycle action exists
here. Caller verifies its guest; consumption holds the existing canonical lock. Full OTA admission
remains separate; these are input checks, not a pristine-source attestation.
"""
from pathlib import Path
import hashlib
import re
import time

import mustang_hostdeps as deps
import mustang_lane
import mustang_paths
import mustang_preparation_inputs as inputs
import mustang_otatools_prep as preparation
import mustang_existing_lock as locks

ROOT=Path(__file__).resolve().parents[1]


def binding(lane,cfg,root=ROOT):
    mustang_lane.validate(lane)
    if not isinstance(cfg.get('vendorEvidence'),str) or not isinstance(cfg.get('hostDependenciesEvidence'),str):
        raise ValueError('successful dedicated vendor and host-dependency evidence is required')
    vendor=inputs.evidence(cfg['vendorEvidence'],'roseos.mustang-vendor-evidence.v1',root)
    host=inputs.evidence(cfg['hostDependenciesEvidence'],'roseos.mustang-hostdeps-evidence.v1',root)
    for record in (vendor,host):
        r=record['receipt'];identity=r.get('identity',{});run=identity.get('run',{})
        if identity.get('builder')!=lane['vm'] or identity.get('diskUuid')!=cfg.get('dataFilesystemUuid') \
                or record['stopObservation'].get('instanceId')!=lane['vm']['instanceId'] \
                or r.get('sourceRoot')!=lane['sourceRoot'] or r.get('success') is not True \
                or run.get('runId')!=record.get('runId') or run.get('source')!=record.get('source') \
                or run.get('workflow')!='Immortal-Protocols/RoseOS-1/'+lane['workflow']+'@refs/heads/main' \
                or run.get('runAttempt')!='1' or not re.fullmatch(r'[1-9][0-9]{0,19}',str(run.get('runId',''))) \
                or not re.fullmatch(r'[0-9a-f]{40}',str(run.get('source',''))):
            raise ValueError('prepared input belongs to another source, builder, disk or successful run')
    v=vendor['receipt'];h=host['receipt'];recipe=deps.recipe(root)
    if v.get('schema')!='roseos.mustang-vendor-preparation-run.v1' or v.get('phase')!='complete' \
            or v.get('executionMode')!='pinned-typescript-source-loader' \
            or v.get('adevtoolTrackedWorktreeUnchanged') is not True \
            or any(v.get(k) is not False for k in ('makeStarted','allocated','signingStarted','otaAdmitted')) \
            or v.get('hostDependencies',{}).get('receiptSha256')!=host['receiptSha256'] \
            or v.get('factoryInput',{}).get('archiveRehashed') is not True \
            or v['factoryInput'].get('sha256')!=deps.readers.recipe(root)['factory']['sha256'] \
            or h.get('schema')!='roseos.mustang-hostdeps-run.v1' or type(h.get('makeExitCode')) is not int \
            or h['makeExitCode']!=0 or h.get('recipe')!=recipe \
            or h.get('outDir')!=str(Path(lane['sourceRoot'])/recipe['outDir']) \
            or h.get('manifestSha256')!=hashlib.sha256((Path(root)/'os/pinned-manifest.xml').read_bytes()).hexdigest() \
            or h.get('source',{}).get('projectCount')!=1057 \
            or h['source'].get('check')!='project-heads-and-git-status' \
            or len(v.get('commands',[]))!=3 \
            or any(c.get('processStarted') is not True or type(c.get('exitCode')) is not int \
                   or c['exitCode']!=0 for c in v['commands']) \
            or v['commands'][-1].get('argv')!=['vendor/adevtool/bin/run','generate-all','-d','mustang']:
        raise ValueError('prepared vendor is not the completed pinned Mustang generation')
    return vendor,host


def consume(lane,cfg,candidate,run,*,root=ROOT):
    """Rehash both the original prepared input and its isolated copy, never regenerate."""
    paths=mustang_paths.layout(lane);candidate=Path(candidate)
    expected=mustang_paths.attempt(lane,'prep',run['runId'],run['runAttempt'])/'source'
    if candidate!=expected or not re.fullmatch(r'[0-9a-f]{40}',str(run.get('source',''))):
        raise ValueError('vendor consumption requires this fresh dedicated source copy')
    source=paths['sourceRoot'];storage=paths['storageRoot']
    for path in (source,storage,candidate):
        if deps.plain_dir(path).st_dev!=deps.plain_dir(paths['home']).st_dev:
            raise ValueError('vendor consumption leaves the dedicated data filesystem')
    with locks.shared(paths['lockPath'],expected=paths['lockPath']):
        vendor,host=binding(lane,cfg,root);deadline=time.monotonic()+900
        identity={'builder':lane['vm'],'diskUuid':cfg['dataFilesystemUuid'],'run':run}
        original=preparation.vendor_input(source,storage,vendor,identity,deadline)
        original_host=inputs.host_dependencies(source,storage,host,identity,deadline=deadline,root=root)
        copied=preparation.verify_vendor_payload(candidate,vendor['receipt'],deadline)
        copied_host=inputs.verify_host_distribution(candidate,host['receipt'],deadline=deadline,root=root)
    return {'schema':'roseos.mustang-vendor-consumption.v1','run':run,'builder':lane['vm'],
            'sourceRoot':str(source),'candidateSourceRoot':str(candidate),'original':original,
            'originalHostDependencies':original_host,'copy':copied,'copiedHostDependencies':copied_host,
            'vendorProducer':vendor['receipt']['identity']['run'],'vendorReceiptSha256':vendor['receiptSha256'],
            'generationStarted':False,'downloadStarted':False,'hashBypassPatchApplied':False,
            'pristineRuntimeAttestation':False,'otaAdmitted':False}
