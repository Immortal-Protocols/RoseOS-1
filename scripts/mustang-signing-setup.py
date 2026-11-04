#!/usr/bin/env python3
"""Exact Mustang container metadata/creation through the caller's existing gcloud identity.

Creates empty containers only. Never generates credentials, reads payloads,
adds versions, changes IAM, repeats an uncertain create or modifies other devices.
"""
import argparse
import datetime
import json
import subprocess
import sys

PROJECT = 'protocols-465723'
SECRETS = ('PIXEL_OS_SIGNING_KEYS_MUSTANG_V1', 'PIXEL_OS_SIGNING_PASSPHRASE_MUSTANG_V1')
SIGNER = 'roseos-build-signer@protocols-465723.iam.gserviceaccount.com'
BOOTSTRAP_ROLE = 'projects/protocols-465723/roles/roseosModelsBootstrapV1'


def call(sdk, args):
    result = subprocess.run([sdk,*args,'--project',PROJECT,'--quiet','--format=json'],
                            capture_output=True,text=True,timeout=120)
    if result.returncode:
        category = next((c for c in ('PERMISSION_DENIED','NOT_FOUND','UNAUTHENTICATED','ALREADY_EXISTS')
                         if c in result.stderr),'unclassified-error')
        return {'status':category,'exitCode':result.returncode}
    return {'status':'ok','metadata':json.loads(result.stdout or 'null')}


def inspect(sdk):
    rows = {}
    for secret in SECRETS:
        row = call(sdk,['secrets','describe',secret])
        if row['status']=='ok':
            versions = call(sdk,['secrets','versions','list',secret,'--filter=state!=DESTROYED'])
            row['versions'] = versions
        rows[secret] = row
    return rows


def create(sdk):
    before = inspect(sdk)
    results = {}
    for secret in SECRETS:
        state = before[secret]
        if state['status']=='ok':
            versions = state.get('versions',{})
            if versions.get('status')!='ok' or versions.get('metadata'):
                raise RuntimeError(f'{secret}: occupied or version metadata unavailable; not changed')
            results[secret] = {'status':'existing-empty; not changed'}
        elif state['status']=='NOT_FOUND':
            # Exactly one create. A timeout/unknown result is observed once, never retried.
            try:
                result = call(sdk,['secrets','create',secret,'--replication-policy=automatic'])
            except subprocess.TimeoutExpired:
                result = {'status':'uncertain-create; no retry'}
            results[secret] = result
            if result['status']!='ok':
                print(json.dumps({'create':results,'after':inspect(sdk)}))
                raise RuntimeError(f'{secret}: create failed or uncertain; stop, no retry')
        else:
            raise RuntimeError(f'{secret}: {state["status"]}; create not attempted')
    after = inspect(sdk)
    print(json.dumps({'create':results,'after':after}))
    for secret,row in after.items():
        if row['status']!='ok' or row.get('versions',{}).get('status')!='ok' or row['versions']['metadata']:
            raise RuntimeError(f'{secret}: creation readback not empty/confirmed')


def binding_plan(expires_at):
    """Concrete four-hour, two-container proposal only; it never executes IAM commands."""
    expiry=datetime.datetime.strptime(expires_at,'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)
    remaining=(expiry-datetime.datetime.now(datetime.timezone.utc)).total_seconds()
    if not 1800<=remaining<=4*3600:
        raise ValueError('expiry must leave 30 minutes and be at most four hours from action time')
    commands=[]
    for secret in SECRETS:
        for title,role in [('mustang-bootstrap',BOOTSTRAP_ROLE),('mustang-signing','roles/secretmanager.secretAccessor')]:
            commands.append(['gcloud','secrets','add-iam-policy-binding',secret,'--project',PROJECT,
                '--member','serviceAccount:'+SIGNER,'--role',role,'--condition',
                f'expression=request.time < timestamp("{expires_at}"),title={title}'])
    return {'status':'requires explicit action-time approval; not executed','signer':SIGNER,'expiresAt':expires_at,
            'targets':list(SECRETS),'commands':commands,
            'bootstrapNext':'One journaled pro-mustang-v1 key generation/import after controller access is resolved and containers are rechecked empty by the signer.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('plan','inspect','create-containers','binding-plan'))
    p.add_argument('--gcloud',default='gcloud')
    p.add_argument('--expires-at')
    a = p.parse_args()
    if a.action=='plan':
        print(json.dumps({'project':PROJECT,'secrets':SECRETS,'signer':SIGNER,
            'createCommands':[['gcloud','secrets','create',s,'--project',PROJECT,'--replication-policy=automatic'] for s in SECRETS],
            'requiredBootstrapPermissions':['secretmanager.secrets.get','secretmanager.versions.list','secretmanager.versions.add'],
            'requiredSigningPermission':'secretmanager.versions.access',
            'actionTimeConfirmation':'Any new signer IAM binding and generation/import of a persistent new Mustang OS key set must be concretely authorized; this program does neither.'},indent=2))
    elif a.action=='binding-plan':
        print(json.dumps(binding_plan(a.expires_at),indent=2))
    elif a.action=='inspect':
        print(json.dumps(inspect(a.gcloud)))
    else:
        create(a.gcloud)


if __name__=='__main__':
    try:
        main()
    except (RuntimeError,subprocess.TimeoutExpired) as error:
        print(json.dumps({'status':'stopped','reason':str(error)}),file=sys.stderr)
        sys.exit(1)
