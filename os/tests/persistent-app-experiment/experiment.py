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
         EDIR receives results.json and small text excerpts only. With --startup-diagnostic (EXP006-STARTUP-003, never
         with --systemui) only environment, image and device run; after the owned emulator exits, at most the first
         STARTUP_CAP bytes of its own newly created log are classified into one fixed category (STARTUP_PATTERNS),
         recorded with the byte-cap facts and whether the AVD descriptor existed; no text, path, argv or environment.
         The emulator is always stopped and the key and AVD removed. A stage that cannot run stops the later ones; nothing is forced.
Synthetic fixture only: no conclusion about SystemUI, platform signatures, privileged installers, OTA or Pixels.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
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

def serial_state(devices_output, serial=SERIAL):
    """The adb state of `serial` in `adb devices` output (device, offline, unauthorized, ...), 'absent' when it is not
    listed, 'other' for a state that is not a plain word; never the rest of the line."""
    for line in devices_output.splitlines()[1:]:
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0] == serial:
            state = parts[1].strip()
            return state if re.fullmatch(r'[a-z-]{1,20}', state) else 'other'
    return 'absent'

def outcome(fn):
    """One stage's record: pass with its facts, unavailable or fail with a reason, plus any structured facts the
    exception carries (the boot wait's); a stage's result is decided only here."""
    try:
        return {'result': 'pass', **(fn() or {})}
    except Unavailable as e:
        return {'result': 'unavailable', 'reason': str(e), **getattr(e, 'facts', {})}
    except (Failed, subprocess.TimeoutExpired) as e:
        return {'result': 'fail', 'reason': str(e)[:600], **getattr(e, 'facts', {})}

# EXP006-STARTUP-003: fixed startup categories, matched case-insensitively in the first STARTUP_CAP bytes of the
# owned emulator log. One category or none; two or more is ambiguous and stays unknown. A match is evidence, never a
# repair.
STARTUP_CAP = 16 * 1024
STARTUP_PATTERNS = {
    'avd_missing': (r'unknown avd name', r'no avd (name )?specified', r'avd\b[^\n]{0,80}\bdoes not exist',
        r'cannot find avd\b(?! system path)'),
    'image_missing': (r'cannot find avd system path', r'no initial system image', r'system image[^\n]{0,80}'
        r'\b(not found|missing|does not exist)'),
    'acceleration_refused': (r'requires hardware acceleration', r'/dev/kvm[^\n]{0,80}\b(permission denied|not found|'
        r'no such file|is not accessible)', r'\bkvm\b[^\n]{0,40}\b(is required|not available|permission denied)'),
    'rejected_option': (r'\b(unknown|invalid|unrecognized) option\b', r'option[^\n]{0,60}requires an argument'),
    'missing_library': (r'error while loading shared libraries', r'cannot open shared object file',
        r'could not load[^\n]{0,80}\.so\b'),
}
STARTUP_CATEGORIES = (*STARTUP_PATTERNS, 'unknown')

def classify_startup(data):
    """(category, ambiguous) for bytes of startup output; no match or more than one category is 'unknown'."""
    text = data.decode('utf-8', 'replace')
    found = [name for name, patterns in STARTUP_PATTERNS.items()
        if any(re.search(p, text, re.IGNORECASE) for p in patterns)]
    return (found[0] if len(found) == 1 else 'unknown'), len(found) > 1

def startup_output(fd, cap=STARTUP_CAP):
    """Facts about the owned log through `fd`, the descriptor this run created it with (O_EXCL, O_NOFOLLOW) and gave
    the child as its output; the path is never opened again, so a replaced or linked path cannot be read. Only a
    regular file is read, at most its first cap bytes; never its text or path."""
    facts = {'log': 'not-created', 'capBytes': cap, 'bytesRead': 0, 'truncated': None, 'category': 'unknown',
        'ambiguous': False}
    if fd is None:
        return facts
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return {**facts, 'log': 'not-regular'}
        data = b''
        while len(data) < cap:
            chunk = os.pread(fd, cap - len(data), len(data))
            if not chunk:
                break
            data += chunk
        category, ambiguous = classify_startup(data)
        return {**facts, 'log': 'read', 'bytesRead': len(data), 'truncated': os.fstat(fd).st_size > cap,
            'category': category, 'ambiguous': ambiguous}
    except OSError:
        return {**facts, 'log': 'unreadable'}

def avd_descriptor(path):
    """present (a regular file), absent, or unknown (anything else, or it could not be checked)."""
    try:
        return 'present' if stat.S_ISREG(os.lstat(path).st_mode) else 'unknown'
    except FileNotFoundError:
        return 'absent'
    except OSError:
        return 'unknown'

# EXP006-AVD-004: the AVD home and emulator home are new directories of this run, created before avdmanager, and the
# descriptor avdmanager writes is checked before any emulator is spawned. Nothing here writes a descriptor.
AVD_NAME = 'fixture'
DESCRIPTOR_CAP = 4096

def prepare_homes(work):
    """Create WORK/avd and WORK/emulator-home as new real directories (never an existing path or a link)."""
    for name in ('avd', 'emulator-home'):
        try:
            os.mkdir(Path(work)/name, 0o700)
        except OSError:
            raise Failed(f'the task-local {name} directory already exists or cannot be created; nothing was launched')

def avd_binding(avd_home, name=AVD_NAME):
    """(ready, facts) for the descriptor AVD_HOME/<name>.ini: a regular non-symlink file of at most DESCRIPTOR_CAP
    bytes whose absolute path= is this AVD home's own <name>.avd, a real directory holding a regular config.ini.
    The facts are fixed words only, never a path or a value from the file."""
    avd_home = Path(os.path.abspath(avd_home))
    facts = {'descriptor': 'absent', 'binding': 'unchecked', 'data': 'unchecked'}
    ini = avd_home/f'{name}.ini'
    try:
        info = os.lstat(ini)
    except FileNotFoundError:
        return False, facts
    except OSError:
        return False, {**facts, 'descriptor': 'unreadable'}
    if stat.S_ISLNK(info.st_mode):
        return False, {**facts, 'descriptor': 'symlink'}
    if not stat.S_ISREG(info.st_mode):
        return False, {**facts, 'descriptor': 'not-regular'}
    if info.st_size > DESCRIPTOR_CAP:
        return False, {**facts, 'descriptor': 'oversized'}
    try:
        fd = os.open(ini, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            text = os.read(fd, DESCRIPTOR_CAP + 1).decode('utf-8')
        finally:
            os.close(fd)
    except (OSError, UnicodeDecodeError):
        return False, {**facts, 'descriptor': 'unreadable'}
    facts['descriptor'] = 'present'
    paths = [line.split('=', 1)[1].strip() for line in text.splitlines() if line.split('=', 1)[0].strip() == 'path']
    expected = avd_home/f'{name}.avd'
    if len(paths) != 1:
        facts['binding'] = 'missing' if not paths else 'ambiguous'
        return False, facts
    if not os.path.isabs(paths[0]) or os.path.realpath(paths[0]) != os.path.realpath(expected):
        facts['binding'] = 'mismatch'
        return False, facts
    facts['binding'] = 'match'
    try:
        data = os.lstat(expected)
    except OSError:
        return False, {**facts, 'data': 'absent'}
    if stat.S_ISLNK(data.st_mode) or not stat.S_ISDIR(data.st_mode):
        return False, {**facts, 'data': 'symlink' if stat.S_ISLNK(data.st_mode) else 'not-directory'}
    try:
        config = os.lstat(expected/'config.ini')
    except OSError:
        return False, {**facts, 'data': 'config-missing'}
    if not stat.S_ISREG(config.st_mode):
        return False, {**facts, 'data': 'config-not-regular'}
    return True, {**facts, 'data': 'present'}

# ------------------------------------------------------------------ run

class Device:
    def __init__(self, sdk, evidence):
        self.adb = str(Path(sdk)/'platform-tools/adb')
        self.evidence = Path(evidence)
        self.child = None  # the owned emulator process, once launched

    def sh(self, *args, timeout=120, check=False):
        result = subprocess.run([self.adb, '-s', SERIAL, *args], capture_output=True, text=True, timeout=timeout)
        if check and result.returncode:
            raise Failed(f'adb {" ".join(args)[:120]} exited {result.returncode}: {(result.stdout + result.stderr).strip()[-300:]}')
        return result

    def prop(self, name):
        return self.sh('shell', 'getprop', name).stdout.strip()

    def serial(self):
        try:
            listed = subprocess.run([self.adb, 'devices'], capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return 'adb-timeout'
        return serial_state(listed.stdout) if listed.returncode == 0 else 'adb-error'

    def wait_boot(self, deadline):
        """Every 5 s until the same deadline: the owned emulator child (an exit ends the wait at once with its
        status), the serial's adb state, and once it is a device, sys.boot_completed. Never reaching device fails as
        the old wait-for-device timeout did; device without the boot property is unavailable. The structured facts
        (no log, argv or environment) are returned, or carried by the exception for the stage record."""
        started = time.time()
        facts = {'child': 'not-owned', 'childExitStatus': None, 'serial': 'absent', 'serialReachedDevice': False,
            'wait': 'timeout', 'bootCompleted': False, 'seconds': 0}
        def end(error, wait):
            facts.update(wait=wait, seconds=round(time.time() - started))
            error.facts = {'bootWait': facts}
            return error
        while True:
            code = self.child.poll() if self.child else None
            if self.child:
                facts.update(child='running' if code is None else 'exited', childExitStatus=code)
            facts['serial'] = self.serial()
            if code is not None:
                raise end(Failed(f'the emulator exited with status {code} before boot completed (serial '
                    f'{facts["serial"]}, boot property not reached)'), 'child-exited')
            if facts['serial'] == 'device':
                facts['serialReachedDevice'] = True
                if self.prop('sys.boot_completed') == '1':
                    facts['bootCompleted'] = True
                    time.sleep(5)
                    facts.update(wait='boot-completed', seconds=round(time.time() - started))
                    return facts
            if time.time() >= deadline:
                break
            time.sleep(5)
        if not facts['serialReachedDevice']:
            raise end(Failed(f'{SERIAL} never reached device state within the stage budget (child {facts["child"]}, '
                f'serial {facts["serial"]}, boot property not reached)'), 'timeout')
        raise end(Unavailable('boot did not complete within the stage budget'), 'timeout')

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

def run(sdk, work, evidence, systemui=False, startup_diagnostic=False):
    if startup_diagnostic and systemui:
        raise Unavailable('the startup diagnostic runs without the SystemUI stages')
    work, evidence = Path(work), Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    results = {'experiment': 'OS-COMPONENT-EXP-004', 'source': os.environ.get('GITHUB_SHA'),
        'run': os.environ.get('GITHUB_RUN_ID'), 'stages': {}, 'scope': 'synthetic persistent fixture on an AOSP emulator only'}
    stages = results['stages']
    start = time.time()
    budget = lambda minutes: min(time.time() + minutes * 60, start + 24 * 60)
    emulator_proc = owned_log = None
    device = Device(sdk, evidence)
    if startup_diagnostic:
        results['mode'] = 'startup-diagnostic'

    def stage(name, fn, needs=None):
        if any(s['result'] != 'pass' for n, s in stages.items() if needs is None or n in needs):
            stages[name] = {'result': 'not-run', 'reason': 'an earlier stage did not pass'}
            return
        stages[name] = outcome(fn)

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
        nonlocal emulator_proc, owned_log
        avd = work/'avd'
        prepare_homes(work)
        os.environ['ANDROID_AVD_HOME'] = str(avd)
        os.environ['ANDROID_EMULATOR_HOME'] = str(work/'emulator-home')
        run_ok([Path(sdk)/'cmdline-tools/latest/bin/avdmanager', 'create', 'avd', '-n', AVD_NAME, '-k', IMAGE,
            '-d', 'pixel', '-p', avd/f'{AVD_NAME}.avd'], input='no\n')
        ready, prepared = avd_binding(avd)
        if not ready:
            error = Failed(f'avdmanager left no usable {AVD_NAME} descriptor in the task AVD home (descriptor '
                f'{prepared["descriptor"]}, binding {prepared["binding"]}, data {prepared["data"]}); nothing was launched')
            error.facts = {'avdPrepare': prepared}
            raise error
        if startup_diagnostic:
            # A new file of this run only (never an existing file or a link); its descriptor is kept for the read.
            try:
                owned_log = os.open(work/'emulator.log', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except OSError:
                raise Failed('the task-local emulator output already exists; the startup diagnostic needs a new one')
            log = owned_log
        else:
            log = open(work/'emulator.log', 'w')
        emulator_proc = subprocess.Popen([str(Path(sdk)/'emulator/emulator'), '-avd', 'fixture', '-port', str(SERIAL_PORT),
            '-no-window', '-no-snapshot', '-no-boot-anim', '-no-audio', '-writable-system', '-gpu', 'swiftshader_indirect'],
            stdout=log, stderr=subprocess.STDOUT)
        device.child = emulator_proc
        boot_wait = device.wait_boot(budget(8))
        devices = [l.split()[0] for l in run_ok([device.adb, 'devices']).splitlines()[1:] if l.strip()]
        if devices != [SERIAL]:
            raise Failed(f'unexpected devices {devices}')
        facts = {'qemu': device.prop('ro.kernel.qemu') or device.prop('ro.boot.qemu'), 'debuggable': device.prop('ro.debuggable'),
            'fingerprint': device.prop('ro.build.fingerprint'), 'sdk': device.prop('ro.build.version.sdk')}
        if facts['qemu'] != '1' or facts['debuggable'] != '1':
            raise Unavailable(f'not a debuggable emulator image: {facts}')
        return {**facts, 'avdPrepare': prepared, 'bootWait': boot_wait}

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
            if startup_diagnostic and name not in ('environment', 'image', 'device'):
                stages[name] = {'result': 'not-run', 'reason': 'excluded: startup diagnostic only'}
                continue
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
        if startup_diagnostic:
            # After the owned child exits and before the AVD is removed; any failure is a recorded fact, not a raise.
            try:
                try:
                    if emulator_proc:
                        emulator_proc.wait(timeout=10)
                    diagnostic = startup_output(owned_log)
                except subprocess.TimeoutExpired:
                    diagnostic = {**startup_output(None), 'log': 'child-running'}
                diagnostic['avdDescriptor'] = avd_descriptor(work/'avd/fixture.ini') if owned_log is not None else 'unknown'
            except Exception:
                diagnostic = {**startup_output(None), 'log': 'error', 'avdDescriptor': 'unknown'}
            finally:
                if owned_log is not None:
                    os.close(owned_log)
            results['startupDiagnostic'] = diagnostic
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
    p.add_argument('--startup-diagnostic', action='store_true',
        help='run: environment, image and device only, then classify the owned emulator startup output')
    a = p.parse_args(argv)
    if a.startup_diagnostic and a.systemui:
        p.error('--startup-diagnostic runs without --systemui')
    try:
        if a.command == 'build':
            print(json.dumps(build(a.sdk, a.work), indent=2, sort_keys=True))
            return 0
        if not a.evidence:
            p.error('run needs --evidence')
        results = run(a.sdk, a.work, a.evidence, a.systemui, a.startup_diagnostic)
        # Results and reasons in the job log too, so the outcome is readable without downloading the artifact.
        print(json.dumps({k: {'result': v['result'], 'reason': v.get('reason')} for k, v in results['stages'].items()},
            indent=2, sort_keys=True))
        return 0
    except (Unavailable, Failed) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
