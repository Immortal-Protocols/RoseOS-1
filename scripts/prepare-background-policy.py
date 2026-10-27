#!/usr/bin/env python3
"""Verify the recipient's signed APKs and write exact local policy; never contact a device."""
import argparse
import json
from pathlib import Path
from background_apks import AGENT, VOICE, identity, validate_policy

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--agent-apk', type=Path, required=True)
p.add_argument('--voice-apk', type=Path, required=True)
p.add_argument('--target-apk', type=Path, action='append', default=[])
p.add_argument('--prototype-control', action='store_true',
    help='Authorize this signed controller to follow the task across apps, including installer screens')
p.add_argument('--aapt', type=Path, required=True)
p.add_argument('--apksigner', type=Path, required=True)
p.add_argument('--sdk', type=int, required=True, help='ro.build.version.sdk of the OS being built')
p.add_argument('--output', type=Path, required=True, help='Private local receipt; do not commit')
a = p.parse_args()
if a.output.exists():
    p.error('Output exists; choose a new receipt path')
policy = {'schemaVersion': 1, 'sdk': a.sdk,
    'prototypeControl': a.prototype_control,
    'agent': identity(a.agent_apk, a.aapt, a.apksigner, a.sdk),
    'voice': identity(a.voice_apk, a.aapt, a.apksigner, a.sdk),
    'targets': sorted([identity(apk, a.aapt, a.apksigner, a.sdk) for apk in a.target_apk], key=lambda t: t['packageName'])}
validate_policy(policy, a.agent_apk)
a.output.parent.mkdir(parents=True, exist_ok=True)
with a.output.open('x') as f:
    a.output.chmod(0o600)
    json.dump(policy, f, indent=2)
    f.write('\n')
print(f'Verified {len(policy["targets"])} exact targets; wrote {a.output}. No device or OS source changed.')
