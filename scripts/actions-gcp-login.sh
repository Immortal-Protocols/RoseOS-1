#!/usr/bin/env bash
# Log gcloud in as SERVICE_ACCOUNT through GitHub OIDC workload identity federation; no key files.
# Needs id-token: write, GCP_WORKLOAD_IDENTITY_PROVIDER (full provider resource name) and gcloud.
set -euo pipefail
account="${1:?usage: actions-gcp-login.sh SERVICE_ACCOUNT}"
: "${GCP_WORKLOAD_IDENTITY_PROVIDER:?not configured}" "${ACTIONS_ID_TOKEN_REQUEST_URL:?id-token: write is required}"
umask 077
token_file="$RUNNER_TEMP/gcp-oidc-token"
curl --fail --silent --show-error -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
  "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=https://iam.googleapis.com/$GCP_WORKLOAD_IDENTITY_PROVIDER" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["value"], end="")' > "$token_file"
echo "::add-mask::$(cat "$token_file")"
gcloud iam workload-identity-pools create-cred-config "$GCP_WORKLOAD_IDENTITY_PROVIDER" \
  --service-account="$account" --credential-source-file="$token_file" \
  --output-file="$RUNNER_TEMP/gcp-credentials.json" --quiet
gcloud auth login --cred-file="$RUNNER_TEMP/gcp-credentials.json" --quiet
