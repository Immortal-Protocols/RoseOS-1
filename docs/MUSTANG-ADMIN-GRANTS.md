# Apply the approved Mustang signer grants

Nathan approved these four-hour grants and one journaled Mustang key bootstrap.
The worker’s configured `dev-infra` account can read these policies but lacks
`secretmanager.secrets.setIamPolicy`. Its first grant was not applied, confirmed
by policy readback. The remaining three grants were not attempted. An existing
GCP IAM admin must apply the approved bindings; no further approval is needed.

Run the following once in **GCP Cloud Shell as that existing IAM admin**. It adds
two conditional roles on each of the two Mustang containers to the existing
RoseOS signer. Both roles expire at the same time, four hours from execution.
The existing bootstrap role has exactly get/list/add permissions. The commands
use Google’s documented [conditional binding interface](https://docs.cloud.google.com/sdk/gcloud/reference/secrets/add-iam-policy-binding)
and preserve other policy entries. No key or secret payload is created or read.

```bash
(
  set -euo pipefail
  mustang_expiry="$(date -u -d '+4 hours' +'%Y-%m-%dT%H:%M:%SZ')"
  mustang_condition="$(mktemp)"
  trap 'rm -f "$mustang_condition"' EXIT
  python3 - "$mustang_expiry" "$mustang_condition" <<'PY'
import json, sys
with open(sys.argv[2], 'w') as f:
    json.dump({
        'title': 'mustang-four-hour',
        'expression': 'request.time < timestamp("' + sys.argv[1] + '")'
    }, f)
PY
  for mustang_secret in PIXEL_OS_SIGNING_KEYS_MUSTANG_V1 PIXEL_OS_SIGNING_PASSPHRASE_MUSTANG_V1; do
    for mustang_role in projects/protocols-465723/roles/roseosModelsBootstrapV1 roles/secretmanager.secretAccessor; do
      gcloud secrets add-iam-policy-binding "$mustang_secret" \
        --project=protocols-465723 \
        --member=serviceAccount:roseos-build-signer@protocols-465723.iam.gserviceaccount.com \
        --role="$mustang_role" \
        --condition-from-file="$mustang_condition" \
        --quiet --format='value(etag)'
    done
  done
  printf 'All four Mustang updates succeeded; expires at %s\n' "$mustang_expiry"
)
```

If a command fails, the block stops. Report which command failed and its error;
do not rerun the block blindly. On success, send the confirmation line and expiry.
The worker will read back the exact public IAM bindings, bind the profile from
that evidence, and run the already-approved single bootstrap through the
established workflow. It will recheck container occupancy as the signer before
key generation. The bootstrap remains unexecuted until those checks pass.

The independent unsigned workflow is
[41714573501](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41714573501),
source `216184cba518c3d2f5e86aedfdddfa3b1fa27834`. Its controller succeeded;
the preparation/build step started at 20:40:43 UTC on 2026-11-03. No successful
compile, signing, installer or phone acceptance is claimed by this handoff.
