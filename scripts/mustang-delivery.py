#!/usr/bin/env python3
"""Hosted Mustang provenance guard and private delivered-download checksum receipt."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
REPOSITORY='Immortal-Protocols/RoseOS-1'
API='https://api.github.com/repos/'+REPOSITORY


def request(path):
    token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:raise ValueError('workflow token required for private run metadata')
    req=urllib.request.Request(API+path,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(req,timeout=60) as response:return json.load(response)


def number(value):
    if not re.fullmatch(r'[1-9][0-9]{0,19}',str(value or '')):raise ValueError('positive run id required')
    return str(value)


def pin_run(run_id,path,artifact,title_prefix):
    run=request('/actions/runs/'+number(run_id))
    if run.get('path')!=path or run.get('head_branch')!='main' or run.get('event')!='workflow_dispatch' \
            or run.get('status')!='completed' or run.get('conclusion')!='success' or run.get('run_attempt')!=1 or not run.get('display_title','').startswith(title_prefix):
        raise ValueError('source run is not the completed single-attempt main workflow')
    sha=run.get('head_sha','')
    if not re.fullmatch(r'[0-9a-f]{40}',sha):raise ValueError('source SHA absent')
    subprocess.run(['git','-C',str(ROOT),'merge-base','--is-ancestor',sha,'origin/main'],check=True)
    artifacts=request('/actions/runs/'+number(run_id)+'/artifacts?per_page=100')
    if artifacts['total_count']>100:raise ValueError('artifact listing incomplete')
    matching=[x for x in artifacts['artifacts'] if x['name']==artifact and not x['expired']]
    if len(matching)!=1:raise ValueError('one expected unexpired artifact required')
    row=matching[0]
    return {'runId':number(run_id),'source':sha,'artifactId':row['id'],'artifactName':artifact,
            'artifactDigest':row.get('digest'),'expiresAt':row['expires_at']}


def guard(mode,compile_run,bootstrap_run,out):
    if os.environ.get('GITHUB_REF')!='refs/heads/main' or os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch':
        raise ValueError('manual main workflow required')
    spec=importlib.util.spec_from_file_location('mustang_bootstrap',ROOT/'scripts/signing-bootstrap.py')
    sb=importlib.util.module_from_spec(spec);spec.loader.exec_module(sb)
    problems=sb.preflight('pro-mustang-v1',sb.load_profiles(),os.environ,os.environ['GITHUB_SHA'],
                         sb.datetime.now(sb.timezone.utc))
    if problems:raise ValueError('; '.join(problems))
    binding={'mode':mode,'recipeSource':os.environ['GITHUB_SHA']}
    if mode=='bootstrap':
        if compile_run or bootstrap_run:raise ValueError('bootstrap accepts no prior-run pins')
    else:
        binding['compile']=pin_run(compile_run,'.github/workflows/os-release.yml',f'mustang-receipts-{number(compile_run)}-1','mustang-build ')
        binding['bootstrap']=pin_run(bootstrap_run,'.github/workflows/os-release.yml',f'mustang-bootstrap-{number(bootstrap_run)}-1','mustang-bootstrap ')
    with Path(out).open('x') as f:json.dump(binding,f,indent=2)
    if mode=='sign-package' and os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:
            f.write(f'compile_source={binding["compile"]["source"]}\n')
            f.write(f'bootstrap_source={binding["bootstrap"]["source"]}\n')
    print(json.dumps(binding))


def verify_download(directory,artifact_id,out):
    directory=Path(directory)
    metadata=request('/actions/artifacts/'+number(artifact_id))
    if metadata.get('expired'):raise ValueError('delivery artifact expired')
    checked=[]
    for line in (directory/'SHA256SUMS').read_text().splitlines():
        digest,name=line.split('  ',1)
        if not re.fullmatch(r'[0-9a-f]{64}',digest) or Path(name).name!=name:raise ValueError('invalid checksum entry')
        with (directory/name).open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
        if actual!=digest:raise ValueError('downloaded output checksum differs')
        checked.append({'file':name,'sizeBytes':(directory/name).stat().st_size,'sha256':actual})
    receipt=json.loads((directory/'candidate-receipt.json').read_text())
    installer=receipt['output']
    if receipt.get('device')!='mustang' or receipt.get('status')!='packaged-candidate' \
            or not any(x['file']==installer['file'] and x['sha256']==installer['sha256'] for x in checked):
        raise ValueError('delivered installer identity differs')
    result={'schema':'roseos.mustang-delivery.v1','device':'mustang','build':receipt['build'],
        'candidateSource':receipt['candidateSource'],'label':receipt['label'],'artifactId':metadata['id'],
        'privateUrl':f'https://github.com/{REPOSITORY}/actions/runs/{metadata["workflow_run"]["id"]}/artifacts/{metadata["id"]}',
        'expiresAt':metadata['expires_at'],'artifactSizeBytes':metadata['size_in_bytes'],
        'downloadChecksumsVerified':True,'files':checked,'omittedChecks':receipt['omittedChecks'],'phoneValidated':False}
    with Path(out).open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps(result))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('guard','verify-download'))
    p.add_argument('--mode',choices=('bootstrap','sign-package'))
    for flag in ('--compile-run','--bootstrap-run','--out','--dir','--artifact-id'):p.add_argument(flag)
    a=p.parse_args()
    if a.action=='guard':guard(a.mode,a.compile_run,a.bootstrap_run,a.out)
    else:verify_download(a.dir,a.artifact_id,a.out)


if __name__=='__main__':main()
