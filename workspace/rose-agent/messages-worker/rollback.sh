#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
python3 - "$ROOT" <<'PY'
import sys
sys.path.insert(0,sys.argv[1]+'/rose-agent/phone')
import control
assert not control.call('/messages-worker/config',{'enabled':False})['enabled']
print('Message shortcuts disabled. The existing Messages app, send path, media shortcuts and Bluetooth settings are unchanged.')
PY
