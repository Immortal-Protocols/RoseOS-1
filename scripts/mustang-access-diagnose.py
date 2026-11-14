#!/usr/bin/env python3
"""Classify the same controller's token exchange; no token output, compute request or access mutation."""
import json
import os
import subprocess
import sys

result=subprocess.run(['gcloud','auth','print-access-token','--quiet'],capture_output=True,text=True,timeout=120)
flags={
    'permissionDenied':'PERMISSION_DENIED',
    'impersonationDenied':'Failed to impersonate',
    'tokenCreatorMissing':'iam.serviceAccounts.getAccessToken',
    'attributeConditionDenied':'attribute condition',
    'invalidGrant':'invalid_grant',
    'unauthenticated':'UNAUTHENTICATED'
}
# Never print stdout: it holds the access token on success.
print(json.dumps({'schema':'roseos.mustang-controller-auth-diagnostic.v1',
    'account':os.environ.get('GCP_CONTROLLER_SERVICE_ACCOUNT'),'workflowRef':os.environ.get('GITHUB_WORKFLOW_REF'),
    'source':os.environ.get('GITHUB_SHA'),'tokenExchange':'ok' if result.returncode==0 else 'failed',
    'exitCode':result.returncode,'categories':[k for k,v in flags.items() if v.lower() in result.stderr.lower()],
    'computeRequests':0,'iamChanges':0}))
sys.exit(0 if result.returncode==0 else 1)
