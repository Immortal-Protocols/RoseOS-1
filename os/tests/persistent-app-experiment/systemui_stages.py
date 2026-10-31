"""SystemUI stages of the emulator experiment (OS-COMPONENT-UPDATES-001), run only by `experiment.py run --systemui`.

They act on the emulator image's own com.android.systemui and need no platform key:
  systemui-identity  pm path, pull the image APK, identity receipt (aapt xmltree + apksigner, scripts/systemui_identity.py)
  systemui-foreign   the same APK re-signed with the job-local test key: adb install --staged must exit nonzero with a
                     recognised refusal (pm's "Error [...]" verification verdict or a "Failure [...]" other than a
                     timeout) and no session it created may be ready (a different signer cannot join
                     android.uid.systemui). Exit 0 is always a failure, never acceptance or activation proof. Every
                     session it created that is not applied is abandoned, whatever its state, and checked gone
  systemui-staged    scripts/systemui-component-update.py apply with the image's own bytes (a same-version reinstall):
                     staged, reboot, /data codePath, UPDATED_SYSTEM_APP, a SystemUI process, the session applied
  systemui-restore   that tool's recovery: pm uninstall-system-updates, reboot, the image copy running again
A newer SystemUI would need the emulator's platform key and is not built here. This is the package-manager mechanism on
one emulator image, not GrapheneOS SystemUI compatibility, OTA, platform rollback or Pixel evidence.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'scripts'))
import systemui_identity  # noqa: E402

_spec = importlib.util.spec_from_file_location('systemui_component_update', ROOT/'scripts/systemui-component-update.py')
update = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(update)

SYSTEMUI = update.PACKAGE

def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def identity_receipt(apk, device_path, tools, run):
    """The rose-systemui-identity/1 receipt of a pulled APK; raises systemui_identity.IdentityError."""
    digest = _sha256(apk)
    outputs = {}
    for name, argv in (('xmltree', [tools['aapt'], 'dump', 'xmltree', apk, 'AndroidManifest.xml']),
            ('apksigner', [tools['apksigner'], 'verify', '--print-certs', apk])):
        result = run([str(a) for a in argv], capture_output=True, text=True, timeout=120)
        outputs[name] = systemui_identity.ToolOutput(result.returncode, result.stdout, digest)
    revision = os.environ.get('GITHUB_SHA') or subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
        capture_output=True, text=True).stdout.strip()
    return systemui_identity.build_receipt(apk_path=device_path.lstrip('/'), apk_sha256=digest,
        xmltree=outputs['xmltree'], apksigner=outputs['apksigner'],
        artifact={'kind': 'system_ext-image', 'name': 'emulator image (pulled with adb)', 'sha256': digest},
        source={'revision': revision},
        tools={k: {'path': str(tools[k]), 'sha256': _sha256(tools[k])} for k in ('aapt', 'apksigner')})

def install_result(text):
    """A fixed category for `adb install --staged` output (pm android15-release doWaitForStagedSessionReady):
    success-ready ("Success. Reboot device to apply staged session"), success, verification-error ("Error [..]"),
    timeout ("Failure [timed out ..]"), failure ("Failure [..]") or other. Never the text itself."""
    lines = [line.strip() for line in text.splitlines()]
    if any(line.startswith('Failure [timed out') for line in lines):
        return 'timeout'
    if any(line.startswith('Error [') for line in lines):
        return 'verification-error'
    if any(line.startswith('Failure [') for line in lines):
        return 'failure'
    if 'Success. Reboot device to apply staged session' in lines:
        return 'success-ready'
    if 'Success' in lines:
        return 'success'
    return 'other'

REFUSALS = ('verification-error', 'failure')

def stages(device, tools, work, evidence, serial, Failed, run=subprocess.run):
    """[(name, fn)] for experiment.run; each fn returns facts or raises Failed."""
    work, evidence = Path(work), Path(evidence)
    image_apk, receipt_path = work/'systemui-image.apk', work/'systemui-image.json'

    def identity():
        path = device.sh('shell', 'pm', 'path', SYSTEMUI).stdout.strip()
        if not path.startswith('package:/system_ext/priv-app/') or '\n' in path:
            raise Failed(f'SystemUI is not a single image APK under /system_ext/priv-app: {path[:200]}')
        device.sh('pull', path[len('package:'):], str(image_apk), check=True, timeout=180)
        try:
            receipt = identity_receipt(image_apk, path[len('package:'):], tools, run)
            update.decide(receipt, receipt)
        except (systemui_identity.IdentityError, update.InvalidInput) as error:
            raise Failed(f'image SystemUI identity: {error}')
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
        shutil.copyfile(receipt_path, evidence/'systemui-image.json')
        return {k: receipt[k] for k in ('versionCode', 'signingCertificateSha256')} | {'apkSha256': receipt['apk']['sha256']}

    def foreign():
        foreign_apk = work/'systemui-foreign.apk'
        result = run([str(tools['apksigner']), 'sign', '--ks', str(work/'test-only.keystore'), '--ks-pass',
            'pass:test-only', '--out', str(foreign_apk), str(image_apk)], capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise Failed('re-signing with the job-local test key failed: ' + result.stderr.strip()[-300:])
        def sessions(when):
            listed = device.sh('shell', 'pm', 'get-stagedsessions')
            try:
                if listed.returncode:
                    raise update.SessionStateError(f'pm get-stagedsessions exited {listed.returncode}')
                return update.staged_sessions(listed.stdout)
            except update.SessionStateError as error:
                failure = Failed(f'staged session state unreadable {when} the foreign install: {error}')
                failure.facts = {'foreignInstall': facts}
                raise failure
        facts = {'installExit': None, 'installResult': None, 'created': [], 'abandoned': [], 'remainingCreated': None}
        before = sessions('before')
        install = device.sh('install', '--staged', str(foreign_apk), timeout=300)
        text = install.stdout + install.stderr
        (evidence/'systemui-foreign.txt').write_text(text[-4000:])
        facts.update(installExit=install.returncode, installResult=install_result(text))
        # Only sessions this stage created, and every one of them that is not applied, whatever its state.
        created = update.created_unapplied(before, sessions('after'), SYSTEMUI)
        facts['created'] = [{k: s[k] for k in ('sessionId', 'ready', 'applied', 'failed')} for s in created]
        for session in created:
            device.sh('shell', 'pm', 'install-abandon', str(session['sessionId']))
        facts['abandoned'] = [s['sessionId'] for s in created]
        left = [s for s in update.created_unapplied(before, sessions('after abandoning'), SYSTEMUI)]
        facts['remainingCreated'] = len(left)
        problems = []
        if install.returncode == 0:
            problems.append(f'adb install --staged exited 0 ({facts["installResult"]}); not a refusal')
        elif facts['installResult'] not in REFUSALS:
            problems.append(f'adb install --staged exited {install.returncode} without a recognised refusal '
                f'({facts["installResult"]})')
        if any(s['ready'] and not s['failed'] for s in created):
            problems.append('a session it created was ready')
        if left:
            problems.append(f'{len(left)} created session(s) remain after abandoning')
        if problems:
            failure = Failed('a SystemUI signed by another key was not refused: ' + '; '.join(problems)
                + '. Neither acceptance nor activation is shown by this')
            failure.facts = {'foreignInstall': facts}
            raise failure
        return {'refusal': next((l for l in text.splitlines() if l.strip().startswith(('Failure [', 'Error ['))), ''),
            'foreignInstall': facts}

    def staged():
        receipt = json.loads(receipt_path.read_text())
        decision = update.decide(receipt, receipt)
        (evidence/'systemui').mkdir()
        outcome = update.apply(update.Device(device.adb, serial, run), image_apk, decision, evidence/'systemui')
        if outcome != 'verified':
            raise Failed(f'staged SystemUI outcome {outcome}; see systemui/result.json')
        return {'outcome': outcome, 'reinstall': decision['reinstall']}

    def restore():
        receipt = json.loads(receipt_path.read_text())
        decision = update.decide(receipt, receipt)
        (evidence/'systemui-restore').mkdir()
        recorded = {}
        outcome = update.recover(update.Device(device.adb, serial, run), decision, recorded, 600,
            lambda outcome: outcome)
        (evidence/'systemui-restore/result.json').write_text(json.dumps(recorded, indent=2, sort_keys=True) + '\n')
        if outcome != 'recovered':
            raise Failed(f'image SystemUI not restored: {recorded.get("recover")}')
        return {'outcome': outcome}

    return [('systemui-identity', identity), ('systemui-foreign', foreign), ('systemui-staged', staged),
        ('systemui-restore', restore)]
