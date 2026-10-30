#!/usr/bin/env python3
"""EXP005-F1: make the official SDK emulator package present before the experiment, without accepting anything.

  setup_emulator.py --sdk SDK --record FILE

If SDK/emulator/emulator is already an executable file it is used as is. Otherwise the installed
SDK/cmdline-tools/latest/bin/sdkmanager runs `--install emulator` once, with stdin closed so no licence prompt can be
answered: only licences already present in SDK/licenses apply. FILE (JSON) records `pre-existing`, `installed` or
`unavailable` with the reason, the sdkmanager exit status and output tail, and `emulator -version`. No sudo, KVM ACL,
SELinux or verity change. An unavailable result is not hidden: the experiment's environment stage still reports the
missing tool. Exit status 0 when the emulator is usable, 1 when it is unavailable.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

def emulator_version(emulator, run):
    try:
        result = run([str(emulator), '-version'], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f'emulator -version failed: {error}'
    lines = (result.stdout or '').strip().splitlines()
    if result.returncode or not lines:
        return None, f'emulator -version exited {result.returncode}'
    return lines[0], None

def setup(sdk, run=subprocess.run):
    sdk = Path(sdk)
    emulator = sdk/'emulator/emulator'
    sdkmanager = sdk/'cmdline-tools/latest/bin/sdkmanager'
    record = {'emulator': str(emulator), 'licences': sorted(p.name for p in (sdk/'licenses').glob('*'))}
    if emulator.is_file() and os.access(emulator, os.X_OK):
        record['result'] = 'pre-existing'
    elif not (sdkmanager.is_file() and os.access(sdkmanager, os.X_OK)):
        return {**record, 'result': 'unavailable', 'reason': f'{sdkmanager} is not installed'}
    else:
        try:
            result = run([str(sdkmanager), '--install', 'emulator'], stdin=subprocess.DEVNULL, capture_output=True,
                text=True, timeout=600)
            code, output = result.returncode, (result.stdout or '') + (result.stderr or '')
        except subprocess.TimeoutExpired:
            code, output = 124, 'sdkmanager timed out after 600s'
        record['sdkmanager'] = {'exit': code, 'outputTail': output[-1500:]}
        if code != 0:
            return {**record, 'result': 'unavailable',
                'reason': f'sdkmanager --install emulator exited {code} with no licence answered'}
        if not (emulator.is_file() and os.access(emulator, os.X_OK)):
            return {**record, 'result': 'unavailable', 'reason': 'sdkmanager succeeded but the emulator is not executable'}
        record['result'] = 'installed'
    version, problem = emulator_version(emulator, run)
    if problem:
        return {**record, 'result': 'unavailable', 'reason': problem}
    return {**record, 'version': version}

def main(argv=None, run=subprocess.run):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--sdk', required=True)
    p.add_argument('--record', required=True)
    a = p.parse_args(argv)
    record = setup(a.sdk, run)
    Path(a.record).parent.mkdir(parents=True, exist_ok=True)
    Path(a.record).write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    print(json.dumps({k: record.get(k) for k in ('result', 'reason', 'version')}, sort_keys=True))
    return 0 if record['result'] in ('pre-existing', 'installed') else 1

if __name__ == '__main__':
    sys.exit(main())
