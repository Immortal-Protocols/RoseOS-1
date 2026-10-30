#!/usr/bin/env python3
"""OS-COMPONENT-UPDATES-001: replace the persistent SystemUI on a disposable emulator by a staged install, verify its
activation after reboot, and recover the image copy when activation fails.

  check  --candidate CAND.json --installed INST.json
         the compatibility decision only (no device): both are rose-systemui-identity/1 receipts
         (scripts/systemui_identity.py), CAND for the replacement APK, INST for the APK the device runs now
  apply  --serial S --apk APK --candidate CAND.json --installed INST.json --evidence NEW_DIR [--adb PATH]
         check, then on that emulator: stage, reboot, verify; on any activation failure recover and verify that

Pinned frameworks/base 84183aff decides the route:
  * com.android.systemui is persistent (android:persistent="true"), and InstallPackageHelper refuses an update of a
    persistent package unless the session is staged ("Persistent apps are not updateable"); so only a staged install;
  * a staged APK session needs INSTALL_PACKAGES (PackageInstallerService), and GrapheneOS refuses system-app updates
    from any installer but its first-party store or the shell (PackageInstallerSession); adb's shell uid is the
    supported installer here. The Rose Agent self-update exception (#202) is limited to dev.pixel.voice and is not
    used: it never applies to SystemUI;
  * SystemUI runs under sharedUserId android.uid.systemui, so the replacement must keep that shared user and the same
    signing certificate, and must not lower the version.

Stages (each recorded in NEW_DIR/result.json as pass, fail or not-run with a reason):
  preflight  emulator only (ro.kernel.qemu or ro.boot.qemu = 1), booted, no staged session pending, the installed
             SystemUI matches INST (versionCode) and runs
  stage      adb install --staged APK; exactly one ready staged session for com.android.systemui
  activate   reboot, boot completed within the budget
  verify     /data codePath, UPDATED_SYSTEM_APP, versionCode of CAND, a SystemUI process, the session applied
  recover    only after a failed activate or verify: pm uninstall-system-updates com.android.systemui, reboot, then the
             image codePath, versionCode of INST and a SystemUI process again (a manual return to the image copy, not
             platform rollback)
A refused stage stops before any reboot. Nothing here disables verity, roots, remounts, signs, reads a key or touches
a phone: a device that is not an emulator is refused. A pass shows this mechanism on that emulator image only.
Exit status: 0 verified, 1 not applied (recovered or refused), 2 invalid input, 3 recovery failed.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

PACKAGE = 'com.android.systemui'
SHARED_USER = 'android.uid.systemui'
SCHEMA = 'rose-systemui-identity/1'

class InvalidInput(Exception):
    pass

def _present(receipt, field):
    value = receipt.get(field)
    return value.get('value') if isinstance(value, dict) and value.get('state') == 'present' else None

def decide(candidate, installed):
    """{'route': 'staged', ...} when CAND may replace INST on the device, else InvalidInput with every reason."""
    problems = []
    for name, receipt in (('candidate', candidate), ('installed', installed)):
        if not isinstance(receipt, dict) or receipt.get('schema') != SCHEMA:
            raise InvalidInput(f'{name} is not a {SCHEMA} receipt')
        if receipt.get('package') != PACKAGE:
            problems.append(f'{name} package is {receipt.get("package")!r}, not {PACKAGE}')
        if _present(receipt, 'sharedUserId') != SHARED_USER:
            problems.append(f'{name} sharedUserId is not {SHARED_USER}')
        if _present(receipt, 'persistent') is not True:
            problems.append(f'{name} is not the persistent SystemUI')
        if not re.fullmatch(r'[0-9a-f]{64}', str(receipt.get('signingCertificateSha256', ''))):
            problems.append(f'{name} has no signing certificate digest')
    if problems:
        raise InvalidInput('; '.join(problems))
    if candidate['signingCertificateSha256'] != installed['signingCertificateSha256']:
        problems.append('the candidate is signed by another certificate than the installed SystemUI')
    if not isinstance(candidate.get('versionCode'), int) or not isinstance(installed.get('versionCode'), int):
        problems.append('versionCode missing')
    elif candidate['versionCode'] < installed['versionCode']:
        problems.append(f'candidate versionCode {candidate["versionCode"]} is lower than installed {installed["versionCode"]}')
    if problems:
        raise InvalidInput('; '.join(problems))
    return {'route': 'staged', 'reinstall': candidate['versionCode'] == installed['versionCode'],
        'candidate': {k: candidate[k] for k in ('versionCode', 'signingCertificateSha256')} | {'apkSha256': candidate['apk']['sha256']},
        'installed': {k: installed[k] for k in ('versionCode', 'signingCertificateSha256')} | {'apkSha256': installed['apk']['sha256']}}

def package_state(dumpsys):
    """codePath, versionCode and pkgFlags of the first `Package [com.android.systemui]` block of dumpsys package."""
    block = dumpsys.split(f'Package [{PACKAGE}]', 1)
    if len(block) < 2:
        return None
    text = block[1].split('\n  Package [', 1)[0]
    field = lambda name: (re.search(rf'\b{name}=(\S+)', text) or [None, None])[1]
    flags = re.search(r'pkgFlags=\[([^\]]*)\]', text)
    code = field('versionCode')
    return {'codePath': field('codePath'), 'versionCode': int(code) if code and code.isdigit() else None,
        'flags': flags[1].split() if flags else []}

def staged_sessions(output):
    """[{sessionId, package, ready, applied, failed}] from `pm get-stagedsessions`."""
    sessions = []
    for chunk in re.split(r'(?=sessionId = )', output):
        found = re.match(r'sessionId = (\d+)', chunk)
        if not found:
            continue
        get = lambda name: (re.search(rf'{name} = ([^;\s]+)', chunk) or [None, None])[1]
        sessions.append({'sessionId': int(found[1]), 'package': get('appPackageName'),
            'ready': get('isStagedSessionReady') == 'true', 'applied': get('isStagedSessionApplied') == 'true',
            'failed': get('isStagedSessionFailed') == 'true'})
    return sessions

class Device:
    def __init__(self, adb, serial, runner=subprocess.run, sleep=time.sleep, clock=time.monotonic):
        self.adb, self.serial, self.runner, self.sleep, self.clock = adb, serial, runner, sleep, clock
        self.log = []

    def run(self, *args, timeout=120):
        argv = [self.adb, '-s', self.serial, *args]
        try:
            result = self.runner(argv, capture_output=True, text=True, timeout=timeout)
            out = (result.stdout or '') + (result.stderr or '')
            code = result.returncode
        except subprocess.TimeoutExpired:
            out, code = 'timeout', 124
        self.log.append({'argv': args, 'returncode': code, 'output': out[-2000:]})
        return code, out

    def shell(self, *args, timeout=120):
        return self.run('shell', *args, timeout=timeout)

    def prop(self, name):
        return self.shell('getprop', name, timeout=30)[1].strip()

    def booted(self, budget):
        deadline = self.clock() + budget
        self.run('wait-for-device', timeout=budget)
        while self.clock() < deadline:
            if self.prop('sys.boot_completed') == '1':
                return True
            self.sleep(5)
        return False

    def reboot(self, budget):
        self.run('reboot', timeout=60)
        return self.booted(budget)

    def state(self):
        return package_state(self.shell('dumpsys', 'package', PACKAGE)[1])

    def running(self):
        code, out = self.shell('pidof', PACKAGE, timeout=30)
        return code == 0 and bool(out.strip())

def apply(device, apk, decision, evidence, boot_budget=600):
    """The stage results; writes evidence/result.json. Recovery runs only after a failed activate or verify."""
    stages = {name: {'result': 'not-run'} for name in ('preflight', 'stage', 'activate', 'verify', 'recover')}
    def finish(outcome):
        document = {'package': PACKAGE, 'serial': device.serial, 'decision': decision, 'stages': stages,
            'outcome': outcome, 'adb': device.log,
            'scope': 'staged SystemUI replacement on one emulator image; not platform rollback, OTA or Pixel evidence'}
        Path(evidence, 'result.json').write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')
        return outcome
    if '1' not in (device.prop('ro.kernel.qemu'), device.prop('ro.boot.qemu')):
        stages['preflight'] = {'result': 'fail', 'reason': 'not an emulator; this tool never acts on a phone'}
        return finish('refused')
    before = device.state()
    pending = [s for s in staged_sessions(device.shell('pm', 'get-stagedsessions')[1])
        if not s['applied'] and not s['failed']]
    problems = []
    if device.prop('sys.boot_completed') != '1':
        problems.append('device not booted')
    if pending:
        problems.append(f'staged sessions already pending: {[s["sessionId"] for s in pending]}')
    if not before or before['versionCode'] != decision['installed']['versionCode']:
        problems.append(f'installed SystemUI {before} is not the INST receipt version')
    if not device.running():
        problems.append('SystemUI is not running before the update')
    if problems:
        stages['preflight'] = {'result': 'fail', 'reason': '; '.join(problems)}
        return finish('refused')
    stages['preflight'] = {'result': 'pass', 'state': before}
    code, out = device.run('install', '--staged', str(apk), timeout=300)
    ready = [s for s in staged_sessions(device.shell('pm', 'get-stagedsessions')[1])
        if s['package'] == PACKAGE and s['ready'] and not s['applied'] and not s['failed']]
    if code != 0 or 'Success' not in out or len(ready) != 1:
        stages['stage'] = {'result': 'fail', 'reason': f'staged install refused or not ready (exit {code}): '
            + out.strip()[-300:]}
        for session in ready:
            device.shell('pm', 'install-abandon', str(session['sessionId']))
        return finish('refused')
    session = ready[0]['sessionId']
    stages['stage'] = {'result': 'pass', 'sessionId': session}
    if not device.reboot(boot_budget):
        stages['activate'] = {'result': 'fail', 'reason': f'boot not completed within {boot_budget}s'}
        return recover(device, decision, stages, boot_budget, finish)
    stages['activate'] = {'result': 'pass'}
    after = device.state()
    applied = [s for s in staged_sessions(device.shell('pm', 'get-stagedsessions')[1]) if s['sessionId'] == session]
    problems = []
    if not after or not str(after['codePath']).startswith('/data/app/'):
        problems.append(f'codePath {after and after["codePath"]} is not an update under /data/app')
    if after and after['versionCode'] != decision['candidate']['versionCode']:
        problems.append(f'versionCode {after["versionCode"]}, expected {decision["candidate"]["versionCode"]}')
    if after and 'UPDATED_SYSTEM_APP' not in after['flags']:
        problems.append('not flagged UPDATED_SYSTEM_APP')
    if not device.running():
        problems.append('no SystemUI process after activation')
    if not applied or not applied[0]['applied']:
        problems.append(f'staged session {session} not applied')
    if problems:
        stages['verify'] = {'result': 'fail', 'reason': '; '.join(problems), 'state': after}
        return recover(device, decision, stages, boot_budget, finish)
    stages['verify'] = {'result': 'pass', 'state': after}
    return finish('verified')

def recover(device, decision, stages, boot_budget, finish):
    # A session that has not applied yet would otherwise apply at the recovery reboot.
    abandoned = [s['sessionId'] for s in staged_sessions(device.shell('pm', 'get-stagedsessions')[1])
        if s['package'] == PACKAGE and not s['applied'] and not s['failed']]
    for session in abandoned:
        device.shell('pm', 'install-abandon', str(session))
    code, out = device.shell('pm', 'uninstall-system-updates', PACKAGE, timeout=120)
    booted = device.reboot(boot_budget)
    state = device.state() if booted else None
    problems = []
    if code != 0:
        problems.append(f'uninstall-system-updates exit {code}: {out.strip()[-200:]}')
    if not booted:
        problems.append('boot not completed after recovery')
    elif not state or str(state['codePath']).startswith('/data/') \
            or state['versionCode'] != decision['installed']['versionCode'] or not device.running():
        problems.append(f'image SystemUI not restored: {state}')
    stages['recover'] = {'result': 'fail' if problems else 'pass', 'state': state, 'abandonedSessions': abandoned,
        **({'reason': '; '.join(problems)} if problems else {})}
    return finish('recovery-failed' if problems else 'recovered')

def main(argv=None, runner=subprocess.run):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['check', 'apply'])
    p.add_argument('--candidate', required=True)
    p.add_argument('--installed', required=True)
    p.add_argument('--serial')
    p.add_argument('--apk')
    p.add_argument('--evidence')
    p.add_argument('--adb', default='adb')
    p.add_argument('--boot-budget', type=int, default=600)
    a = p.parse_args(argv)
    try:
        decision = decide(json.loads(Path(a.candidate).read_text()), json.loads(Path(a.installed).read_text()))
        if a.command == 'check':
            print(json.dumps(decision, indent=2, sort_keys=True))
            return 0
        if not (a.serial and a.apk and a.evidence):
            raise InvalidInput('apply needs --serial, --apk and --evidence')
        evidence = Path(a.evidence)
        if evidence.exists():
            raise InvalidInput(f'{evidence} exists; use a new evidence directory')
        apk_sha = __import__('hashlib').sha256(Path(a.apk).read_bytes()).hexdigest()
        if apk_sha != decision['candidate']['apkSha256']:
            raise InvalidInput('the APK bytes are not the ones the candidate receipt describes')
        evidence.mkdir(parents=True)
    except (InvalidInput, OSError, ValueError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2
    outcome = apply(Device(a.adb, a.serial, runner), a.apk, decision, evidence, a.boot_budget)
    print(json.dumps({'outcome': outcome, 'result': str(evidence/'result.json')}))
    return {'verified': 0, 'recovered': 1, 'refused': 1}.get(outcome, 3)

if __name__ == '__main__':
    sys.exit(main())
