#!/usr/bin/env python3
"""Observe only failed otatools attempt 42524960701; never rebuild or read denied logs.

Raw private log text stays on the guest. Only fixed categories, bounded public
source locations, structured lunch fields and output metadata leave it. No
output is moved, deleted, resumed or admitted by this observation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import sys

import mustang_controller as controller
import mustang_hostdeps as deps
import mustang_lane
import mustang_ota
import mustang_otatools_prep as prep

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE='os/build-only/mustang-otatools-42524960701.json'
RESULT='otatools-failure-observation.json'
EXPECTED={'TARGET_PRODUCT':'mustang','TARGET_RELEASE':'cur','TARGET_BUILD_VARIANT':'user','TARGET_DEVICE':'mustang'}
MARKERS={
    'product-config-missing':('cannot find config makefile for product',),
    'invalid-release':('invalid release', 'unknown release config', 'no release config found'),
    'lunch-identity-assertion':('assertionerror: mustang lunch identity differs',),
    'missing-command':('command not found',),
    'permission-error':('permission denied',),
    'missing-make-target':('no rule to make target',),
    'unknown-ninja-target':('ninja: error: unknown target',),
    'soong-bootstrap-failed':('soong bootstrap failed', 'bootstrap failed with'),
    'ninja-stopped':('ninja: build stopped',),
    'read-only-variable':('cannot assign to readonly variable', 'read-only variable'),
    'missing-file':('no such file or directory',),
    'make-error':('error:', '***'),
}
LOCATION=re.compile(r'^((?:build|device|vendor|prebuilts)/[A-Za-z0-9_./+\-]{1,240}):([0-9]{1,8}):')


def binding(root=ROOT):
    record=json.loads((Path(root)/EVIDENCE).read_text());r=record['receipt']
    lane=mustang_lane.validate(mustang_lane.configuration())
    old=Path(lane['storageRoot'])/'mustang-otatools-42524960701-1'
    if record.get('schema')!='roseos.mustang-otatools-evidence.v1' \
            or record.get('runId')!='42524960701' or record.get('source')!='6a532d4ff5cc6c55c338da36344b172c4fcb3cf1' \
            or record.get('receiptSha256')!='853d64f90c93d0f57b330cd1c503a5c5a3b2894c6b7d8c6a655ad3610c5177ab' \
            or record.get('workflowConclusion')!='failure' or record.get('cleanupConclusion')!='success' \
            or record.get('stopObservation',{}).get('status')!='TERMINATED' \
            or record['stopObservation'].get('instanceId')!=lane['vm']['instanceId'] \
            or r.get('success') is not False or r.get('phase')!='mustang-otatools-make' \
            or r.get('sourceRoot')!=lane['sourceRoot'] or r.get('outDir')!=str(Path(lane['sourceRoot'])/prep.OUT) \
            or r.get('identity',{}).get('builder')!=lane['vm'] \
            or r['identity']['run'].get('runId')!='42524960701' or r['identity']['run'].get('runAttempt')!='1' \
            or r['identity']['run'].get('source')!=record['source'] \
            or r.get('commands')!=[{'argv':['/bin/bash',str(old/'build-otatools.sh'),prep.OUT,str(old/'lunch-identity.json')],
                                   'exitCode':1,'processStarted':True}] \
            or any(r.get(k) is not False for k in ('allocated','signingStarted','otaAdmitted','signerPinsAdmitted')) \
            or r.get('buildScript',{}).get('sha256')!='788cdd06ffe5974ff3de3a7bdce016191f62004f1d3c0ec78ec6505db3cfef3f':
        raise ValueError('otatools observation is bound only to the exact failed wrapper attempt')
    return record


def classify(data):
    if len(data)>deps.LOG_BYTES:raise ValueError('otatools private diagnostic exceeds its bound')
    categories=set();locations=set();long_lines=0
    for raw in data.splitlines():
        if len(raw)>4096:long_lines+=1;continue
        line=raw.decode('utf-8',errors='replace');lower=line.lower()
        categories.update(name for name,terms in MARKERS.items() if any(term in lower for term in terms))
        if ('error' in lower or '***' in line) and (match:=LOCATION.match(line)):
            path,number=match.groups()
            if not any(p in ('','.','..') for p in path.split('/')) and len(locations)<32:
                locations.add((path,int(number)))
    return {'logBytes':len(data),'logSha256':hashlib.sha256(data).hexdigest(),
            'categories':sorted(categories),'sourceLocations':[{'path':p,'line':n} for p,n in sorted(locations)],
            'longLinesOmitted':long_lines,'rawTextExported':False}


def observe(source,storage,record,identity):
    r=record['receipt'];old=storage/'mustang-otatools-42524960701-1'
    if r['identity']['builder']!=identity['builder'] or r['identity']['diskUuid']!=identity['diskUuid'] \
            or r['sourceRoot']!=str(source) or storage!=source.parent/'rose-actions':
        raise ValueError('otatools observation belongs to another source, builder or disk')
    raw=deps.readers.read_regular(str(storage),str(old/prep.RECEIPT),1 << 20)
    if hashlib.sha256(raw).hexdigest()!=record['receiptSha256'] or json.loads(raw)!=r:
        raise ValueError('failed otatools receipt differs from uploaded evidence')
    result={'schema':'roseos.mustang-otatools-failure-observation.v1','identity':identity,
            'failedRun':r['identity']['run'],'failedReceiptSha256':record['receiptSha256'],
            'readOnly':True,'makeStarted':False,'outputMoved':False,'otaAdmitted':False,'authenticatedStop':False}
    path=old/'lunch-identity.json'
    if os.path.lexists(path):
        data=deps.readers.read_regular(str(storage),str(path),65536);actual=json.loads(data)
        if not isinstance(actual,dict) or set(actual)!=set(EXPECTED):raise ValueError('lunch observation schema differs')
        allowed={'mustang','frankel','blazer','yogi','cur','trunk_staging','user','userdebug','eng',''}
        result['lunch']={'state':'present','sha256':hashlib.sha256(data).hexdigest(),
                         'matchesExpected':actual==EXPECTED,
                         'fields':{key:value if type(value) is str and value in allowed else 'unexpected' for key,value in actual.items()}}
    else:result['lunch']={'state':'absent','meaning':'the wrapper did not write its post-lunch identity record'}
    path=old/'make.log'
    if os.path.lexists(path):result['privateDiagnostic']=classify(deps.readers.read_regular(str(storage),str(path),deps.LOG_BYTES))
    else:result['privateDiagnostic']={'state':'absent','rawTextExported':False}
    output=source/prep.OUT
    if os.path.lexists(output):result['output']=deps.inspect_output_entries(output)
    else:result['output']={'state':'absent','metadataOnly':True}
    return result


def execute(env,root=ROOT):
    lane=mustang_lane.validate(mustang_lane.configuration());run=controller.invocation(lane,env)
    selected=deps.read_event(env).get('inputs',{})
    disabled=lambda v:v is None or v is False or (type(v) is str and v=='false')
    if not isinstance(selected,dict) or set(selected)-prep.FLAGS \
            or not (selected.get('dry_run') is True or selected.get('dry_run')=='true') \
            or any(not disabled(selected.get(k)) for k in prep.FLAGS-{'dry_run'}) \
            or env.get('GITHUB_REPOSITORY_ID')!='1428996648' or env.get('GITHUB_JOB')!='builder' \
            or env.get('MUSTANG_DRY_RUN')!='true' or pwd.getpwuid(os.geteuid()).pw_name!='mustang-runner':
        raise ValueError('otatools observation requires the exclusive unprivileged infrastructure dry run')
    record=binding(root)
    cfg=json.loads((Path(root)/'os/build-only/mustang-runtime.json').read_text())
    dedicated=mustang_ota.module('mustang_otatools_observation_identity','mustang-dedicated.py')
    paths=dedicated.host_identity(lane,cfg['dataFilesystemUuid'],mount=cfg['dataMount'])
    source=Path(lane['sourceRoot']);directory=Path(env['RUNNER_TEMP'])
    for path in (source,paths['storageRoot'],directory):
        if deps.plain_dir(path).st_dev!=deps.plain_dir(paths['home']).st_dev:
            raise ValueError('otatools observation leaves the dedicated data filesystem')
    clean=deps.child_env(paths['home']/'runner',directory)
    git=['/usr/bin/git','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null','-C',str(root)]
    import time
    deadline=time.monotonic()+60
    if deps.checked(git+['rev-parse','HEAD'],root,clean,deadline)!=run['source'] \
            or deps.checked(git+['status','--porcelain'],root,clean,deadline):
        raise ValueError('observation checkout differs from captured clean source')
    with deps.lock(Path(lane['lockPath'])):
        result=observe(source,paths['storageRoot'],record,dict(run=run,builder=lane['vm'],diskUuid=cfg['dataFilesystemUuid']))
    deps.receipt(directory/RESULT,result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--execute',action='store_true');mode.add_argument('--check-binding',action='store_true')
    args=parser.parse_args()
    if args.check_binding:
        binding();print(json.dumps({'bindingVerified':True,'execute':False}));return 0
    if not args.execute:
        print(json.dumps({'execute':False,'scope':'fixed-failed-otatools-observation','evidence':EVIDENCE}));return 4
    execute(os.environ);return 0


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,OSError,deps.readers._Refused):
        print(json.dumps({'success':False,'failure':'otatools-observation-refused','authenticatedStop':False}));sys.exit(4)
