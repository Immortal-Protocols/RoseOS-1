#!/usr/bin/env python3
"""Mustang-only signing and reduced factory packaging; no IAM, key generation or phone actions.

Consumes the completed Mustang compile receipt and a genuine pro-mustang-v1
bootstrap public receipt supplied by the workflow. Fixed target/container names,
source/build bindings, shared locks and transient secret cleanup remain enforced.
Signing-result/public-identity, APK/policy, firmware coverage and installer content
verification are deliberately omitted under Nathan's sprint plan.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import host_secrets
import release_receipts

PROFILE = 'pro-mustang-v1'
PROJECT = 'protocols-465723'
SIGNER = 'roseos-build-signer@protocols-465723.iam.gserviceaccount.com'
SECRETS = {'keys':'PIXEL_OS_SIGNING_KEYS_MUSTANG_V1','passphrase':'PIXEL_OS_SIGNING_PASSPHRASE_MUSTANG_V1'}
LABEL = 'build-and-package candidate; omitted verification remains undone; physical-device acceptance unproven'
LOCK = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())['host']['lockPath']
BASE = Path('/home/pixelbuild/rose-actions')


def module(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def number(value):
    if not re.fullmatch(r'[1-9][0-9]{0,19}',str(value or '')):raise ValueError('positive numeric run/attempt required')
    return str(value)


def run_root(kind,run_id,attempt):
    return BASE/f'mustang-{kind}-{number(run_id)}-{number(attempt)}'


def source_sha():
    return subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()


def compile_binding(path):
    value=read(path)
    if value.get('schema')!='roseos.mustang-candidate-run.v1' or value.get('device')!='mustang' \
            or value.get('unsignedCompileComplete') is not True or value.get('failedStage'):
        raise ValueError('completed Mustang compile receipt required')
    if not re.fullmatch(r'[0-9a-f]{40}',str(value.get('source'))):raise ValueError('compile source SHA absent')
    if value['source']!=os.environ.get('EXPECTED_COMPILE_SOURCE') \
            or str(value.get('runId'))!=os.environ.get('EXPECTED_COMPILE_RUN') or str(value.get('runAttempt'))!='1':
        raise ValueError('compile receipt differs from the hosted producer-run binding')
    allocation=value['allocation'];build=allocation['build']
    if not re.fullmatch(r'[1-9][0-9]{9}',str(build)) or allocation.get('device')!='mustang' \
            or allocation.get('source')!=value['source']:
        raise ValueError('allocation/source/device binding differs')
    source=run_root('prep',value['runId'],value['runAttempt'])/'source'
    if value['root']!=str(source.parent):raise ValueError('foreign source root')
    result=value['stages']['compile']['result']
    if result.get('ok') is not True or result.get('device')!='mustang' or result['outputs']['verdict']!='complete':
        raise ValueError('actual compile output receipt incomplete')
    identity=result['outputs']['actualBuildIdentity']
    epoch=str(int(datetime.datetime.strptime(allocation['reservedAt'],'%Y-%m-%dT%H:%M:%SZ')
                  .replace(tzinfo=datetime.timezone.utc).timestamp()))
    if identity!={'device':'mustang','build':build,'timestamp':epoch}:raise ValueError('compiled build identity differs')
    for role,relative in [('targetFiles',None),('otatools','host/linux-x86/otatools.zip')]:
        row=result['outputs']['artifacts'][role]
        path=Path(row['path'])
        out=source/f'out-mustang-build-{build}'
        if out not in path.parents or path.is_symlink() or not path.is_file():raise ValueError('compiled output unavailable')
        if relative and path!=out/relative:raise ValueError('foreign otatools path')
        # These are produced outputs, whose hashes remain required in the reduced plan.
        if path.stat().st_size!=row['sizeBytes'] or digest(path)!=row['sha256']:
            raise ValueError('compiled output identity changed')
    return value,source,build,epoch,result['outputs']['artifacts']


def bootstrap_binding(path):
    value=read(path)
    if value.get('schema')!='roseos.signing-bootstrap-receipt.v1' or value.get('profile')!=PROFILE \
            or value.get('device')!='mustang' or value.get('project')!=PROJECT or value.get('status')!='complete' \
            or value.get('recoveryNeeded') is not False or value.get('cleanup',{}).get('workspaceRemoved') is not True:
        raise ValueError('complete Mustang bootstrap receipt required; no foreign identity')
    if value.get('recipeSha')!=os.environ.get('EXPECTED_BOOTSTRAP_SOURCE'):
        raise ValueError('bootstrap receipt differs from the hosted producer-run binding')
    versions={}
    for role,name in SECRETS.items():
        row=value['secrets'][role]
        if row.get('name')!=name or row.get('state')!='committed':raise ValueError('foreign/uncommitted secret binding')
        versions[role]=number(row.get('committedVersion'))
    return value,versions


def structural_keys(root,_identities):
    # This is not intended-public-identity verification. The authority is the
    # Mustang-only container name + numeric version from the real bootstrap.
    certs=list(Path(root).rglob('releasekey.x509.pem'));avbs=list(Path(root).rglob('avb_pkmd.bin'))
    if len(certs)!=1 or len(avbs)!=1 or certs[0].parent!=avbs[0].parent \
            or any(p.is_symlink() or not p.is_file() for p in certs+avbs):
        raise ValueError('key archive must contain one usable key directory')
    return certs[0].parent


def execute_sign(config,env):
    # A signal unwinds host_secrets.private_directory, including RAM key cleanup.
    def stopped(signum,_frame):raise InterruptedError(f'signing stopped by signal {signum}')
    signal.signal(signal.SIGTERM,stopped)
    if env.get('ROSE_RETAINED_INPUT') or env.get('ROSE_SIGN_RESUME_ARCHIVE'):
        raise ValueError('foreign retained-input/resume selector is not a Mustang signing route')
    hs=module('mustang_host_sign','host-sign.py')
    return hs.main(config_path=config,env=env,discover_keys=structural_keys)


def sign(compile_receipt,bootstrap_receipt,run_id,attempt):
    sb=module('mustang_bootstrap','signing-bootstrap.py')
    profile=sb.load_profiles()['profiles'][PROFILE]
    if profile.get('status')!='resource-bound' or profile.get('missing'):
        raise ValueError('Mustang signing resources are unbound; no login/key access authorized by a source definition')
    if profile['project']!=PROJECT or profile['secrets']!=SECRETS \
            or os.environ.get('GCP_SIGNER_SERVICE_ACCOUNT')!=SIGNER:
        raise ValueError('signer/project/container binding differs')
    compiled,source,build,epoch,outputs=compile_binding(compile_receipt)
    bootstrap,versions=bootstrap_binding(bootstrap_receipt)
    out=run_root('sign',run_id,attempt)
    if out.exists() or out.is_symlink():raise ValueError('sign attempt exists; no automatic retry')
    out.mkdir(mode=0o700)
    record={'schema':'roseos.mustang-sign-run.v1','device':'mustang','build':build,'candidateSource':compiled['source'],
        'recipeSource':source_sha(),'runId':run_id,'runAttempt':attempt,'label':LABEL,'status':'failed',
        'compileReceiptSha256':digest(compile_receipt),'bootstrapReceiptSha256':digest(bootstrap_receipt),
        'secrets':{k:{'name':SECRETS[k],'version':v} for k,v in versions.items()},
        'omittedChecks':compiled['omittedChecks'],'signingResultVerified':False,'intendedPublicIdentityVerified':False}
    try:
        notes=out/'CANDIDATE.md'
        notes.write_text(f'# Mustang {build}\n\n{LABEL}\n\nSource: {compiled["source"]}\n\n'
            +'Omitted checks:\n'+''.join(f'- {x}\n' for x in compiled['omittedChecks']))
        release=out/'release';release.mkdir(mode=0o700)
        shutil.copyfile(outputs['targetFiles']['path'],release/'unsigned-target_files.zip')
        config={'target':{'device':'mustang'},'host':{'sign':{
            'project':PROJECT,'keysSecret':SECRETS['keys'],'passphraseSecret':SECRETS['passphrase'],
            'secretVersion':versions['keys'],'passphraseSecretVersion':versions['passphrase'],
            'otatools':'host/linux-x86/otatools.zip','javaHome':str(source/'prebuilts/jdk/jdk21/linux-x86'),
            'updateNotes':{'file':str(notes),'sha256':digest(notes),'device':'mustang','build':build}}},
            'identities':bootstrap['publicIdentity'],'publication':{'channels':{'beta':{'feedChannel':'stable'}}}}
        config_path=out/'sign-config.json';release_receipts.write_once(config_path,config)
        env={k:v for k,v in os.environ.items() if k not in ('JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS',
            'CLASSPATH','JAVA_HOME','LD_PRELOAD','LD_LIBRARY_PATH','LD_AUDIT') and not k.startswith('GIT_')}
        env.update(BUILD_NUMBER=build,BUILD_DATETIME=epoch,ROSE_CANDIDATE_SHA=compiled['source'],
            ROSE_RELEASE_DIR=str(release),ROSE_SIGN_OTATOOLS=outputs['otatools']['path'],OUT_DIR=f'out-mustang-build-{build}')
        bounded=module('mustang_build','mustang-build-only.py').engine()
        hp=module('mustang_factory_host','host-factory-package.py')
        with hp.shared_lock(LOCK):
            result=bounded.supervise([sys.executable,str(Path(__file__).resolve()),'_execute','--config',str(config_path)],
                str(source),env,10800,{'sampleSeconds':5,'killGraceSeconds':30,'boundBytes':1<<60,
                                     'reserveBytes':0,'checkReserve':False})
        record['commandOutcome']=result
        if result['returncode']!=0 or result['stopped']:raise RuntimeError('signing command failed; no retry')
        tf=release/'signed-target_files.zip';ota=release/'public'/f'mustang-ota_update-{build}.zip'
        record['outputs']={name:{'path':str(p),'sizeBytes':p.stat().st_size,'sha256':digest(p)}
                           for name,p in [('targetFiles',tf),('ota',ota)]}
        record.update(status='signed-command-complete',sourceRoot=str(source),compileArtifacts=outputs,
                      publicAvbPath=str(release/'public/avb_pkmd.bin'))
        return record
    finally:
        release_receipts.write_once(out/'mustang-sign-receipt.json',record)
        print(json.dumps({'signReceipt':str(out/'mustang-sign-receipt.json'),'status':record['status']}))


def package(sign_run,sign_attempt,run_id,attempt):
    signed=read(run_root('sign',sign_run,sign_attempt)/'mustang-sign-receipt.json')
    if signed.get('schema')!='roseos.mustang-sign-run.v1' or signed.get('device')!='mustang' \
            or signed.get('status')!='signed-command-complete':raise ValueError('completed Mustang signing command required')
    build=signed['build'];source=Path(signed['sourceRoot'])
    out=run_root('package',run_id,attempt)
    if out.exists() or out.is_symlink():raise ValueError('package attempt exists; no automatic retry')
    out.mkdir(mode=0o700);work=out/'work';assets=out/'assets';work.mkdir();assets.mkdir()
    hp=module('mustang_factory_host','host-factory-package.py');hs=module('mustang_host_sign','host-sign.py')
    receipt={'schema':'roseos.mustang-package-run.v1','device':'mustang','build':build,'label':LABEL,
        'candidateSource':signed['candidateSource'],'recipeSource':source_sha(),'status':'failed',
        'omittedChecks':signed['omittedChecks'],'phoneValidated':False}
    try:
        with hp.shared_lock(LOCK):
            tf=Path(signed['outputs']['targetFiles']['path']);ota=Path(signed['compileArtifacts']['otatools']['path'])
            for p,expected in [(tf,signed['outputs']['targetFiles']['sha256']),
                               (ota,signed['compileArtifacts']['otatools']['sha256'])]:
                if not p.is_file() or digest(p)!=expected:raise ValueError('produced signing/compile output changed')
            avb=Path(signed['publicAvbPath'])
            if not avb.is_file():raise ValueError('signing command produced no public AVB metadata for generator')
            shutil.copyfile(avb,out/'avb_pkmd.bin')
            # Retain extraction's basic mutation protections, not a separate safe-file verification suite.
            hp.unzip_otatools(ota,work)
            fastboot=work/'bin/fastboot'
            fastboot_sha=digest(fastboot)
            derived=hs.derive_factory_only_script((source/'script/generate-release.sh').read_text(),
                'mustang',build,out/'avb_pkmd.bin',fastboot_sha)
            shutil.copyfile(tf,work/'mustang-target_files.zip')
            script=work/'mustang-factory-only.sh';script.write_text(derived)
            category,code=hp.run_script(script,work,out/'factory-package.log')
            receipt['commandOutcome']={'category':category,'exitCode':code}
            if category!='ok' or code!=0:raise RuntimeError('factory generator failed; outputs kept; no retry')
            produced=work/f'mustang-install-{build}.zip'
            with zipfile.ZipFile(produced) as archive:
                names={Path(n).name for n in archive.namelist()}
                if not {'flash-all.sh','script.txt'}<=names:raise ValueError('output not identified as optimized factory installer')
            dest=assets/produced.name;shutil.copyfile(produced,dest)
            receipt.update(status='packaged-candidate',format='optimized-factory-installer',
                output={'file':dest.name,'path':str(dest),'sizeBytes':dest.stat().st_size,'sha256':digest(dest)})
            (assets/'CANDIDATE.md').write_text(f'# Mustang {build}\n\n{LABEL}\n\nSource: {signed["candidateSource"]}\n\n'
                +'Omitted checks:\n'+''.join(f'- {x}\n' for x in signed['omittedChecks']))
            release_receipts.write_once(assets/'candidate-receipt.json',receipt)
            checksums=''.join(f'{digest(p)}  {p.name}\n' for p in sorted(assets.iterdir()))
            (assets/'SHA256SUMS').write_text(checksums)
            shutil.rmtree(work)
            receipt['workRemoved']=True
            print(f'artifact_dir={assets}')
            if os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write(f'artifact_dir={assets}\n')
            return receipt
    finally:
        receipt['workRetained']=work.exists()
        release_receipts.write_once(out/'mustang-package-receipt.json',receipt)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('sign','package','_execute'))
    for flag in ('--compile-receipt','--bootstrap-receipt','--run-id','--run-attempt','--sign-run','--sign-attempt','--config'):
        p.add_argument(flag)
    a=p.parse_args()
    if a.action=='_execute':return execute_sign(a.config,dict(os.environ))
    if a.action=='sign':sign(a.compile_receipt,a.bootstrap_receipt,number(a.run_id),number(a.run_attempt))
    else:package(number(a.sign_run),number(a.sign_attempt),number(a.run_id),number(a.run_attempt))
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,RuntimeError,host_secrets.AdapterFailed,host_secrets.AdapterSetupMissing) as error:
        print(json.dumps({'status':'stopped','reason':str(error)}),file=sys.stderr);sys.exit(1)
