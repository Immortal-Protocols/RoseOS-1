#!/usr/bin/env python3
"""OS-COMPONENT-EXP-004: replacing a persistent system app on a disposable emulator, observed stage by stage.

  build  --sdk SDK --work DIR   build v1 (versionCode 1) and v2 (versionCode 2) of dev.roseos.test.persistentfixture
                                from fixture/, signed with ONE job-local test key created in DIR (never a production
                                identity); writes apks.json with APK sha256s and the public test-certificate digest
  run    --sdk SDK --work DIR --evidence EDIR
                                the hosted experiment; every stage is pass, fail or unavailable with a reason:
           environment  SDK tools, /dev/kvm usable as-is (no ACL change), licences only as already present
           image        sdkmanager --install of the API 35 default x86_64 image with stdin closed (no licence prompt
                        answered); package revision and image file hashes
           device       fresh AVD in DIR (no snapshots), explicit serial, qemu=1, debuggable, sole device
           seed         debug root + writable-system remount (no disable-verity) to place v1 in /system/app,
                        reboot, unroot; SYSTEM and PERSISTENT flags, image codePath, v1 and a live v1 marker
           ordinary     adb install of v2: expected "Persistent apps are not updateable"
           staged       adb install --staged of the same v2, reboot: /data codePath, v2, same signer, live v2 marker
           restore      pm uninstall-system-updates, reboot: image codePath, v1, live v1 marker (manual restoration
                        to the image copy, not platform rollback)
         EDIR receives results.json and small text excerpts only. The emulator is always stopped and the key and AVD
         removed. A stage that cannot run stops the later ones; nothing is forced.
Synthetic fixture only: no conclusion about SystemUI, platform signatures, privileged installers, OTA or Pixels.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
PACKAGE = 'dev.roseos.test.persistentfixture'
IMAGE = 'system-images;android-35;default;x86_64'
SERIAL_PORT = 5580
SERIAL = f'emulator-{SERIAL_PORT}'
SYSTEM_DIR = '/system/app/PersistentFixture'
VERITY_ENFORCED = ('enforcing', 'eio', 'restart_on_corruption')
PERSISTENT_REJECTION = 'Persistent apps are not updateable'
MARKER = re.compile(r'PersistFixture: marker version=(\d+) pid=(\d+) sentinel=(\S+)')

class Unavailable(Exception):
    pass

class Failed(Exception):
    pass

# ------------------------------------------------------------------ pure classifiers (unit-tested)

def package_state(dumpsys):
    """codePath, versionCode, flags and pkgFlags of the fixture from `dumpsys package <pkg>`; the first package block
    is the active one, a later 'Hidden system packages' block is the image copy."""
    active = dumpsys.split('Hidden system packages:')[0]
    code = re.search(r'codePath=(\S+)', active)
    version = re.search(r'versionCode=(\d+)', active)
    flags = re.search(r'\bflags=\[\s*([^\]]*)\]', active)
    return {'codePath': code[1] if code else None, 'versionCode': int(version[1]) if version else None,
        'flags': flags[1].split() if flags else [], 'hiddenSystemCopy': 'Hidden system packages:' in dumpsys}

def classify_ordinary(output, returncode):
    if returncode == 0 and 'Success' in output:
        return 'fail', 'ordinary install of a persistent system app was accepted'
    if PERSISTENT_REJECTION in output:
        return 'pass', 'rejected as persistent: ' + _first(output, PERSISTENT_REJECTION)
    return 'fail', 'rejected for a different reason, which does not test the hypothesis: ' + _first(output, 'Failure')

def classify_remount(output, returncode):
    """'ok', 'reboot' (one reboot activates the writable-system overlay) or why more would be needed. Disabling
    verity is never performed here."""
    text = output.lower()
    if 'disable-verity' in text or 'disable verity' in text or 'disabled verity' in text:
        return 'requires-disable-verity'
    if returncode != 0:
        return 'reboot' if 'reboot' in text else 'remount-failed'
    if 'now reboot' in text or 'reboot your device' in text:
        return 'reboot'
    return 'ok'

def latest_marker(logcat):
    found = MARKER.findall(logcat)
    return {'version': int(found[-1][0]), 'pid': int(found[-1][1]), 'sentinel': found[-1][2]} if found else None

def cert_digests(apksigner_output):
    return re.findall(r'certificate SHA-256 digest: ([0-9a-f]{64})', apksigner_output)

def _first(text, needle):
    for line in text.splitlines():
        if needle in line:
            return line.strip()[:300]
    return text.strip().splitlines()[-1][:300] if text.strip() else ''

# ------------------------------------------------------------------ build

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def sdk_tools(sdk):
    sdk = Path(sdk)
    build_tools = sorted((sdk/'build-tools').iterdir())[-1]
    platform = sdk/'platforms/android-35/android.jar'
    if not platform.is_file():
        raise Unavailable(f'{platform} is not installed')
    return {'aapt': build_tools/'aapt', 'aapt2': build_tools/'aapt2', 'd8': build_tools/'d8', 'zipalign': build_tools/'zipalign',
        'apksigner': build_tools/'apksigner', 'androidJar': platform, 'buildTools': build_tools.name}

def run_ok(argv, **kw):
    result = subprocess.run([str(a) for a in argv], capture_output=True, text=True, **kw)
    if result.returncode:
        raise Failed(f'{Path(str(argv[0])).name} exited {result.returncode}: {result.stderr.strip()[-400:]}')
    return result.stdout

def build(sdk, work):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    tools = sdk_tools(sdk)
    key = work/'test-only.keystore'
    if not key.exists():
        # One disposable, job-local identity for both APKs; never a production key; deleted by run's cleanup.
        run_ok(['keytool', '-genkeypair', '-keystore', key, '-storepass', 'test-only', '-keypass', 'test-only',
            '-alias', 'fixture', '-keyalg', 'RSA', '-keysize', '2048', '-validity', '2', '-dname', 'CN=RoseOS synthetic test fixture'])
    result = {'package': PACKAGE, 'buildTools': tools['buildTools'], 'apks': {}}
    for version in (1, 2):
        out = work/f'v{version}'
        shutil.rmtree(out, ignore_errors=True)
        (out/'src/dev/roseos/test/persistentfixture').mkdir(parents=True)
        (out/'src/dev/roseos/test/persistentfixture/FixtureVersion.java').write_text(
            'package dev.roseos.test.persistentfixture;\nfinal class FixtureVersion {\n'
            f'    static final int CODE = {version};\n    private FixtureVersion() {{}}\n}}\n')
        sources = [str(p) for p in (HERE/'fixture/src').rglob('*.java')] + [str(p) for p in (out/'src').rglob('*.java')]
        run_ok(['javac', '--release', '17', '-cp', tools['androidJar'], '-d', out/'classes', *sources])
        run_ok([tools['d8'], '--min-api', '29', '--lib', tools['androidJar'], '--output', out,
            *[str(p) for p in (out/'classes').rglob('*.class')]])
        run_ok([tools['aapt2'], 'link', '--manifest', HERE/'fixture/AndroidManifest.xml', '-I', tools['androidJar'],
            '--version-code', str(version), '--version-name', f'v{version}', '-o', out/'unsigned.apk'])
        run_ok(['zip', '-j', '-q', out/'unsigned.apk', out/'classes.dex'])
        run_ok([tools['zipalign'], '-f', '-p', '4', out/'unsigned.apk', out/'aligned.apk'])
        apk = work/f'fixture-v{version}.apk'
        run_ok([tools['apksigner'], 'sign', '--ks', key, '--ks-pass', 'pass:test-only', '--out', apk, out/'aligned.apk'])
        certs = cert_digests(run_ok([tools['apksigner'], 'verify', '--print-certs', apk]))
        result['apks'][f'v{version}'] = {'file': apk.name, 'sha256': sha256(apk), 'versionCode': version,
            'testCertificateSha256': certs}
    if result['apks']['v1']['testCertificateSha256'] != result['apks']['v2']['testCertificateSha256'] \
            or len(result['apks']['v1']['testCertificateSha256']) != 1:
        raise Failed('v1 and v2 must carry exactly the same single test certificate')
    (work/'apks.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result

# ------------------------------------------------------------------ run

class Device:
    def __init__(self, sdk, evidence):
        self.adb = str(Path(sdk)/'platform-tools/adb')
        self.evidence = Path(evidence)

    def sh(self, *args, timeout=120, check=False):
        result = subprocess.run([self.adb, '-s', SERIAL, *args], capture_output=True, text=True, timeout=timeout)
        if check and result.returncode:
            raise Failed(f'adb {" ".join(args)[:120]} exited {result.returncode}: {(result.stdout + result.stderr).strip()[-300:]}')
        return result

    def prop(self, name):
        return self.sh('shell', 'getprop', name).stdout.strip()

    def wait_boot(self, deadline):
        subprocess.run([self.adb, '-s', SERIAL, 'wait-for-device'], timeout=max(1, deadline - time.time()))
        while time.time() < deadline:
            if self.prop('sys.boot_completed') == '1':
                time.sleep(5)
                return
            time.sleep(5)
        raise Unavailable('boot did not complete within the stage budget')

    def reboot(self, deadline):
        self.sh('reboot', timeout=60)
        time.sleep(10)
        self.wait_boot(deadline)

    def save(self, name, text):
        (self.evidence/name).write_text(text[-20000:])

    def observe(self, label):
        dump = self.sh('shell', 'dumpsys', 'package', PACKAGE).stdout
        log = self.sh('shell', 'logcat', '-d', '-s', 'PersistFixture:I').stdout
        pid = self.sh('shell', 'pidof', PACKAGE).stdout.strip()
        self.save(f'{label}-dumpsys-package.txt', dump)
        self.save(f'{label}-logcat.txt', log)
        return {'package': package_state(dump), 'marker': latest_marker(log), 'pidof': pid or None}

def run(sdk, work, evidence, systemui=False):
    work, evidence = Path(work), Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    results = {'experiment': 'OS-COMPONENT-EXP-004', 'source': os.environ.get('GITHUB_SHA'),
        'run': os.environ.get('GITHUB_RUN_ID'), 'stages': {}, 'scope': 'synthetic persistent fixture on an AOSP emulator only'}
    stages = results['stages']
    start = time.time()
    budget = lambda minutes: min(time.time() + minutes * 60, start + 24 * 60)
    emulator_proc = None
    device = Device(sdk, evidence)

    def stage(name, fn, needs=None):
        if any(s['result'] != 'pass' for n, s in stages.items() if needs is None or n in needs):
            stages[name] = {'result': 'not-run', 'reason': 'an earlier stage did not pass'}
            return
        try:
            stages[name] = {'result': 'pass', **(fn() or {})}
        except Unavailable as e:
            stages[name] = {'result': 'unavailable', 'reason': str(e)}
        except (Failed, subprocess.TimeoutExpired) as e:
            stages[name] = {'result': 'fail', 'reason': str(e)[:600]}

    def environment():
        sdkmanager = Path(sdk)/'cmdline-tools/latest/bin/sdkmanager'
        emulator = Path(sdk)/'emulator/emulator'
        missing = [str(p) for p in (sdkmanager, emulator, Path(device.adb)) if not p.exists()]
        if missing:
            raise Unavailable(f'SDK tools missing: {missing}')
        kvm = os.access('/dev/kvm', os.R_OK | os.W_OK)
        facts = {'kvmUsableAsIs': kvm, 'licences': sorted(p.name for p in (Path(sdk)/'licenses').glob('*')),
            'emulatorVersion': run_ok([emulator, '-version']).splitlines()[0],
            'build': json.loads((work/'apks.json').read_text())}
        if not kvm:
            raise Unavailable('/dev/kvm is not readable and writable by the runner user; changing its ACL is out of scope')
        return facts

    def image():
        sdkmanager = Path(sdk)/'cmdline-tools/latest/bin/sdkmanager'
        result = subprocess.run([sdkmanager, '--install', IMAGE], stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=600)
        device.save('sdkmanager-install.txt', result.stdout + result.stderr)
        if result.returncode:
            raise Unavailable(f'sdkmanager --install {IMAGE} exited {result.returncode} with no licence answered: '
                + _first(result.stdout + result.stderr, 'icense'))
        listing = run_ok([sdkmanager, '--list_installed'])
        line = next((l for l in listing.splitlines() if l.strip().startswith(IMAGE)), '')
        root = Path(sdk)/'system-images/android-35/default/x86_64'
        return {'package': line.strip(), 'files': {p.name: sha256(p) for p in sorted(root.glob('*.img'))}}

    def boot():
        nonlocal emulator_proc
        avd = work/'avd'
        os.environ['ANDROID_AVD_HOME'] = str(avd)
        os.environ['ANDROID_EMULATOR_HOME'] = str(work/'emulator-home')
        run_ok([Path(sdk)/'cmdline-tools/latest/bin/avdmanager', 'create', 'avd', '-n', 'fixture', '-k', IMAGE,
            '-d', 'pixel', '-p', avd/'fixture.avd'], input='no\n')
        emulator_proc = subprocess.Popen([str(Path(sdk)/'emulator/emulator'), '-avd', 'fixture', '-port', str(SERIAL_PORT),
            '-no-window', '-no-snapshot', '-no-boot-anim', '-no-audio', '-writable-system', '-gpu', 'swiftshader_indirect'],
            stdout=open(work/'emulator.log', 'w'), stderr=subprocess.STDOUT)
        device.wait_boot(budget(8))
        devices = [l.split()[0] for l in run_ok([device.adb, 'devices']).splitlines()[1:] if l.strip()]
        if devices != [SERIAL]:
            raise Failed(f'unexpected devices {devices}')
        facts = {'qemu': device.prop('ro.kernel.qemu') or device.prop('ro.boot.qemu'), 'debuggable': device.prop('ro.debuggable'),
            'fingerprint': device.prop('ro.build.fingerprint'), 'sdk': device.prop('ro.build.version.sdk')}
        if facts['qemu'] != '1' or facts['debuggable'] != '1':
            raise Unavailable(f'not a debuggable emulator image: {facts}')
        return facts

    def seed():
        verity = device.prop('ro.boot.veritymode')
        if verity in VERITY_ENFORCED:
            # adb remount would disable verity itself on this image; that is outside this experiment.
            raise Unavailable(f'ro.boot.veritymode={verity}: seeding /system would need verity disabled')
        root = device.sh('root', timeout=60)
        time.sleep(5)
        device.sh('wait-for-device', timeout=60)
        transcript = f'ro.boot.veritymode={verity!r}\n'
        for attempt in (1, 2):
            remount = device.sh('remount', timeout=120)
            text = remount.stdout + remount.stderr
            transcript += f'--- remount attempt {attempt} (exit {remount.returncode})\n{text}\n'
            verdict = classify_remount(text, remount.returncode)
            if verdict != 'reboot':
                break
            # The writable-system overlay is set up on first remount and takes effect after one reboot; no verity change.
            device.reboot(budget(8))
            device.sh('root', timeout=60)
            time.sleep(5)
            device.sh('wait-for-device', timeout=60)
        device.save('remount.txt', transcript)
        if verdict != 'ok':
            raise Unavailable('remount needs an operation outside this experiment (' + verdict + '): '
                + _first(transcript, 'erity'))
        device.sh('shell', 'mkdir', '-p', SYSTEM_DIR, check=True)
        device.sh('push', str(work/'fixture-v1.apk'), f'{SYSTEM_DIR}/PersistentFixture.apk', check=True, timeout=120)
        device.sh('shell', 'chmod', '644', f'{SYSTEM_DIR}/PersistentFixture.apk', check=True)
        device.reboot(budget(8))
        device.sh('unroot', timeout=60)
        time.sleep(5)
        device.wait_boot(budget(3))
        if device.sh('shell', 'id', '-u').stdout.strip() == '0':
            raise Failed('adbd is still root after unroot')
        seen = device.observe('seed')
        p = seen['package']
        if not (p['codePath'] == SYSTEM_DIR and p['versionCode'] == 1 and 'SYSTEM' in p['flags']
                and 'PERSISTENT' in p['flags'] and seen['marker'] and seen['marker']['version'] == 1 and seen['pidof']):
            raise Failed(f'v1 is not a running persistent system app from the image: {seen}')
        return {'rootUsed': root.stdout.strip()[:120], **seen}

    def ordinary():
        result = device.sh('install', str(work/'fixture-v2.apk'), timeout=180)
        device.save('ordinary-install.txt', result.stdout + result.stderr)
        verdict, reason = classify_ordinary(result.stdout + result.stderr, result.returncode)
        after = device.observe('ordinary')
        if verdict != 'pass':
            raise Failed(reason)
        if after['package']['versionCode'] != 1:
            raise Failed(f'version changed despite rejection: {after["package"]}')
        return {'reason': reason, **after}

    def staged():
        device.sh('logcat', '-c')
        result = device.sh('install', '--staged', str(work/'fixture-v2.apk'), timeout=180)
        sessions = device.sh('shell', 'pm', 'get-stagedsessions').stdout
        device.save('staged-install.txt', result.stdout + result.stderr + '\n--- staged sessions\n' + sessions)
        if result.returncode:
            raise Failed('staged install refused: ' + _first(result.stdout + result.stderr, 'Failure'))
        device.reboot(budget(8))
        seen = device.observe('staged')
        p = seen['package']
        if not (p['codePath'] and p['codePath'].startswith('/data/app') and p['versionCode'] == 2
                and seen['marker'] and seen['marker']['version'] == 2 and seen['pidof']):
            raise Failed(f'staged v2 is not the running copy after reboot: {seen}')
        return {'sessionsBeforeReboot': sessions.strip()[:600], **seen}

    def restore():
        device.sh('logcat', '-c')
        result = device.sh('shell', 'pm', 'uninstall-system-updates', PACKAGE, timeout=180)
        device.save('restore.txt', result.stdout + result.stderr)
        device.reboot(budget(8))
        seen = device.observe('restore')
        rollback = device.sh('shell', 'dumpsys', 'rollback').stdout
        device.save('dumpsys-rollback.txt', rollback)
        p = seen['package']
        if not (p['codePath'] == SYSTEM_DIR and p['versionCode'] == 1 and seen['marker']
                and seen['marker']['version'] == 1 and seen['pidof']):
            raise Failed(f'the image copy is not the running copy after restoration: {seen}')
        return {'command': result.stdout.strip()[:300], **seen}

    try:
        for name, fn in (('environment', environment), ('image', image), ('device', boot), ('seed', seed),
                ('ordinary', ordinary), ('staged', staged), ('restore', restore)):
            stage(name, fn)
        if systemui:
            # Opt-in (OS-COMPONENT-UPDATES-001): the image's own SystemUI, after the fixture stages but not gated on
            # their seeding (which may need verity disabled); each SystemUI stage needs the boot and its predecessors.
            import systemui_stages
            needs = ['environment', 'image', 'device']
            for name, fn in systemui_stages.stages(device, sdk_tools(sdk), work, evidence, SERIAL, Failed):
                stage(name, fn, needs)
                needs = needs + [name]
    finally:
        if emulator_proc:
            subprocess.run([device.adb, '-s', SERIAL, 'emu', 'kill'], capture_output=True, timeout=60)
            try:
                emulator_proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                emulator_proc.kill()
        for private in ('test-only.keystore', 'avd', 'emulator-home'):
            target = work/private
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink()
        results['elapsedSeconds'] = round(time.time() - start)
        (evidence/'results.json').write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')
    return results

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['build', 'run'])
    p.add_argument('--sdk', required=True)
    p.add_argument('--work', required=True)
    p.add_argument('--evidence')
    p.add_argument('--systemui', action='store_true', help='run: also the SystemUI stages (systemui_stages.py)')
    a = p.parse_args(argv)
    try:
        if a.command == 'build':
            print(json.dumps(build(a.sdk, a.work), indent=2, sort_keys=True))
            return 0
        if not a.evidence:
            p.error('run needs --evidence')
        results = run(a.sdk, a.work, a.evidence, a.systemui)
        # Results and reasons in the job log too, so the outcome is readable without downloading the artifact.
        print(json.dumps({k: {'result': v['result'], 'reason': v.get('reason')} for k, v in results['stages'].items()},
            indent=2, sort_keys=True))
        return 0
    except (Unavailable, Failed) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
