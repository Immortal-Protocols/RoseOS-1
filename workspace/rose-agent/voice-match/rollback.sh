#!/bin/bash
set -euo pipefail
# Disable without touching any microphone, Bluetooth, or installed-app configuration.
python3 - <<'PY'
from pathlib import Path
import json,os
p=Path.home()/'.config/pixel-voice/voice-match/config.json'
if p.exists():
 d=json.loads(p.read_text());d['enabled']=False
 temp=p.with_suffix('.tmp');temp.write_text(json.dumps(d));os.chmod(temp,0o600);temp.replace(p)
print('Voice match disabled. End the current voice session, then start normally.')
PY
