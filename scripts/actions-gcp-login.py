#!/usr/bin/env python3
"""Per-job gcloud login through GitHub OIDC workload identity federation; no key files, no token in argv.

login SERVICE_ACCOUNT  writes an owner-only gcloud config directory under RUNNER_TEMP holding a documented
                       external_account credential whose subject token gcloud fetches from the job's OIDC
                       endpoint (the request token never appears in a command line or log), points later
                       steps at it through CLOUDSDK_CONFIG, and activates it.
logout                 deletes that directory; run it with if: always().
"""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import urllib.parse

STS = 'https://sts.googleapis.com/v1/token'
IMPERSONATE = 'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{}:generateAccessToken'

def config_dir(env):
    return Path(env['RUNNER_TEMP'])/'gcloud-job-config'

def credential(env, account):
    provider = env.get('GCP_WORKLOAD_IDENTITY_PROVIDER')
    if not provider or not provider.startswith('projects/') or '/providers/' not in provider:
        raise SystemExit('GCP_WORKLOAD_IDENTITY_PROVIDER must be the full provider resource name')
    if not account.endswith('.iam.gserviceaccount.com'):
        raise SystemExit('service account must be a full service-account email')
    url, token = env.get('ACTIONS_ID_TOKEN_REQUEST_URL'), env.get('ACTIONS_ID_TOKEN_REQUEST_TOKEN')
    if not url or not token:
        raise SystemExit('the job needs permissions: id-token: write')
    audience = 'https://iam.googleapis.com/' + provider
    separator = '&' if urllib.parse.urlsplit(url).query else '?'
    return {
        'type': 'external_account',
        'audience': '//iam.googleapis.com/' + provider,
        'subject_token_type': 'urn:ietf:params:oauth:token-type:jwt',
        'token_url': STS,
        'service_account_impersonation_url': IMPERSONATE.format(account),
        'credential_source': {
            'url': url + separator + 'audience=' + urllib.parse.quote(audience, safe=''),
            'headers': {'Authorization': 'Bearer ' + token},
            'format': {'type': 'json', 'subject_token_field_name': 'value'},
        },
    }

def login(env, account, run=subprocess.run):
    directory = config_dir(env)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(mode=0o700)
    path = directory/'credential.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, 'w') as f:
        json.dump(credential(env, account), f)
    child = dict(env, CLOUDSDK_CONFIG=str(directory))
    run(['gcloud', 'auth', 'login', '--cred-file', str(path), '--quiet'], env=child, check=True,
        stdout=subprocess.DEVNULL)
    if env.get('GITHUB_ENV'):
        with open(env['GITHUB_ENV'], 'a') as f:
            f.write(f'CLOUDSDK_CONFIG={directory}\n')
    return directory

def logout(env):
    directory = config_dir(env)
    if directory.exists():
        shutil.rmtree(directory)

if __name__ == '__main__':
    if sys.argv[1:2] == ['login'] and len(sys.argv) == 3:
        login(os.environ, sys.argv[2])
    elif sys.argv[1:] == ['logout']:
        logout(os.environ)
    else:
        raise SystemExit('usage: actions-gcp-login.py login SERVICE_ACCOUNT | logout')
