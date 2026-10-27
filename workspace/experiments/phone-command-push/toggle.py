#!/usr/bin/env python3
"""Switch command push on/off; off restores polling without an app update."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'rose-agent/phone'))
from control import call
if len(sys.argv) != 2 or sys.argv[1] not in ('on', 'off', 'status'):
    raise SystemExit('Usage: toggle.py on|off|status')
print(call('/phone/push/config', None if sys.argv[1] == 'status' else {'enabled': sys.argv[1] == 'on'}))
