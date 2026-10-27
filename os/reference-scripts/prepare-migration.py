#!/usr/bin/env python3
"""Snapshot local reinstall artifacts; never connect to or modify a phone.

Input launcher-apps.json is a separately captured phone inventory. APKs contain
private connection configuration. Keep output outside the source tree.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(sys.argv[1]).expanduser().resolve()
if ROOT in OUT.parents:
    raise SystemExit('Output must be outside the source tree (private APKs).')
os.umask(0o077)
OUT.mkdir(parents=True, exist_ok=True)
inventory = json.loads((OUT / 'launcher-apps.json').read_text())['apps']
wanted = {a['packageName']: a['label'] for a in inventory
          if a['packageName'].startswith('dev.pixel.')}
tools = ROOT / 'tools/android-sdk/android-15'
env = dict(os.environ, JAVA_HOME=str(ROOT / 'tools/jdk/Contents/Home'))

def digest(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def metadata(p):
    try:
        text = subprocess.check_output([str(tools / 'aapt'), 'dump', 'badging', str(p)],
                                       text=True, stderr=subprocess.DEVNULL)
        package = re.search(r"package: name='([^']+)'", text).group(1)
        version = int(re.search(r"versionCode='(\d+)'", text).group(1))
        return package, version
    except (subprocess.CalledProcessError, AttributeError):
        return None

candidates = {}
sky_apk = ROOT / 'pixel-sky/.worktrees/stardroid-alpha/stardroid-v2/app/build/outputs/apk/fdroid/debug/app-fdroid-debug.apk'
if sky_apk.is_file():
    candidates.setdefault('dev.pixel.sky', []).append(sky_apk)
delivery = Path.home() / '.config/pixel-voice/delivery'
for p in delivery.glob('*.json'):
    try:
        entry = json.loads(p.read_text())
        apk = delivery / (entry['id'] + '.apk')
        package = entry['packageName']
        if package in wanted and apk.is_file():
            candidates.setdefault(package, []).append(apk)
    except (ValueError, KeyError):
        continue

pruned = {'.git', '.worktrees', 'node_modules', 'tools', 'backups', 'backup',
          'rollback', 'baseline', 'artifacts', '.gradle', 'classes', 'pixel-backups'}
for directory, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in pruned]
    for f in files:
        if f.endswith('.apk') and 'unsigned' not in f and 'aligned' not in f:
            p = Path(directory) / f
            m = metadata(p)
            if m and m[0] in wanted:
                candidates.setdefault(m[0], []).append(p)

apks = OUT / 'apks'
apks.mkdir(exist_ok=True)
records = []
for package, label in wanted.items():
    options = []
    for p in set(candidates.get(package, [])):
        m = metadata(p)
        if m and m[0] == package:
            options.append((m[1], p.stat().st_mtime_ns, str(p), p))
    if not options:
        records.append(dict(package=package, label=label, status='missing-local-apk'))
        continue
    version, _, _, p = max(options)
    subprocess.run([str(tools / 'apksigner'), 'verify', str(p)], env=env,
                   check=True, stdout=subprocess.DEVNULL)
    target = apks / f'{package}-v{version}.apk'
    shutil.copyfile(p, target)
    if digest(p) != digest(target):
        raise RuntimeError('Copy hash mismatch: ' + package)
    records.append(dict(package=package, label=label, versionCode=version,
                        source=str(p), file=str(target.relative_to(OUT)),
                        sha256=digest(target), status='signature-and-copy-verified',
                        installedVersionMatch='not-yet-compared'))

# The phone wipe does not erase the Mac. These copies preserve the small
# configuration and bootstrap dependencies independently of working files.
config = OUT / 'mac-configuration'
config.mkdir(exist_ok=True)
sources = [ROOT / 'mono-reminders/development.keystore',
           ROOT / 'rose-agent/app/assets/connection.json',
           ROOT / 'rose-agent/app/assets/clipboard-connection.json']
for folder, names in {
    'pixel-voice': ['pair-token', 'server-cert.pem', 'server-key.pem',
                    'task-model.json', 'decisions-routing.json', 'phone-push.json',
                    'media-worker.json', 'messages-worker.json', 'clock-worker.json'],
    'pixel-clipboard': ['config.json', 'Send to Pixel.shortcut'],
}.items():
    sources.extend(Path.home() / '.config' / folder / name for name in names)
sources.extend((Path.home() / 'Library/LaunchAgents').glob('local.pixel*.plist'))
config_records = []
for p in sources:
    if not p.is_file():
        continue
    name = hashlib.sha256(str(p).encode()).hexdigest()[:10] + '-' + p.name
    target = config / name
    shutil.copyfile(p, target)
    config_records.append(dict(source=str(p), file=str(target.relative_to(OUT)),
                               sha256=digest(target)))

manifest = dict(scope='Local reinstall artifacts, not a phone-data backup',
                apps=records, configuration=config_records,
                gaps=['No fresh phone shared-storage or private-app-data backup',
                      'No verified SMS, contacts, eSIM or authenticator recovery',
                      'No clean-device restore test',
                      'Local APK versions not compared with all installed versions'])
(OUT / 'reinstall-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
files = [p for p in OUT.rglob('*') if p.is_file() and p.name != 'SHA256SUMS']
(OUT / 'SHA256SUMS').write_text(''.join(f'{digest(p)}  {p.relative_to(OUT)}\n'
                                      for p in sorted(files)))
for p in OUT.rglob('*'):
    p.chmod(0o700 if p.is_dir() else 0o600)
print(json.dumps({'appsSaved': sum(r['status'] != 'missing-local-apk' for r in records),
                  'missing': [r['package'] for r in records if r['status'] == 'missing-local-apk'],
                  'configurationFiles': len(config_records),
                  'output': str(OUT)}))
