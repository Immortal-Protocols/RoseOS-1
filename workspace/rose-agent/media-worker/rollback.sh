#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
python3 - "$ROOT" <<'PY'
import sys
sys.path.insert(0,sys.argv[1]+'/rose-agent/phone')
import control
assert control.call('/media-worker/config',{'enabled':False})['enabled'] is False
print('Media shortcuts disabled. Existing song search, timers and Bluetooth settings are unchanged. An already-dispatched control may finish; no new trial requests are accepted.')
PY
