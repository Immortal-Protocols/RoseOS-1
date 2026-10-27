#!/usr/bin/env python3
"""Preflight/apply captured changes to pinned GrapheneOS source; never build or flash."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from background_apks import validate_policy

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('source', help='Existing GrapheneOS checkout matching os/pinned-manifest.xml')
p.add_argument('--emulator', action='store_true', help='Also apply the optional Goldfish emulator change')
p.add_argument('--background-policy', type=Path, help='Local receipt from prepare-background-policy.py')
p.add_argument('--background-agent', type=Path, help='The exact signed Agent.apk verified in that receipt')
p.add_argument('--reference-display-address', help='Optional physical cover display address for a Fold build')
p.add_argument('--locked-control', action='store_true',
    help='Owner opt-in: let the pinned prototype controller start background sessions while locked')
p.add_argument('--agent-tray', action='store_true',
    help='Enable the Rose Agent status tray in the system image (persist.wm.debug.pixel_agent_tray=1)')
p.add_argument('--check', action='store_true', help='Run all preflight checks without changing source')
a = p.parse_args()
if bool(a.background_policy) != bool(a.background_agent):
    p.error('--background-policy and --background-agent must be supplied together')
if a.locked_control and not a.background_policy:
    p.error('--locked-control needs --background-policy and --background-agent')
if a.reference_display_address and (not a.background_policy or not a.reference_display_address.isdecimal()
        or not 0 < int(a.reference_display_address) < 2**63):
    p.error('Reference display needs background policy and a positive signed-64-bit physical address')
source = Path(a.source).resolve()
bundle = Path(__file__).resolve().parents[1]/'os'
manifest = ET.parse(bundle/'pinned-manifest.xml')
revisions = {n.get('path', n.get('name')): n.get('revision') for n in manifest.findall('project')}
projects = {'frameworks/base': ['frameworks-base.patch']}
if a.agent_tray:projects['build/make'] = ['agent-tray-build-make.patch']
if a.emulator:projects['device/generic/goldfish'] = ['goldfish.patch']
policy = None
if a.background_policy:
    policy = validate_policy(json.loads(a.background_policy.read_text()), a.background_agent)
    if a.locked_control and not policy.get('prototypeControl', False):
        p.error('--locked-control needs a policy prepared with --prototype-control')
    projects['frameworks/base'].append('background-control-frameworks-base.patch')
    projects['frameworks/base'].append('background-control-modern-frameworks-base.patch')
    projects['frameworks/base'].append('background-control-resource-import-fix.patch')
    if a.locked_control:projects['frameworks/base'].append('locked-control-frameworks-base.patch')
    projects.setdefault('build/make', []).append('background-control-build-make.patch')
    projects['build/release'] = ['background-control-build-release.patch']

def is_background_overlay(path):
    return str(path).startswith(('packages/apps/PixelBackgroundControl/',
        'build/release/aconfig/pixel_background_control/')) or str(path) == 'build/release/release_configs/pixel_background_control.textproto'

def check_patch_sequence(dest, patches):
    # Later patches intentionally build on earlier changes to the same file.
    # git apply --check p1 p2 tests both against HEAD, not the intermediate tree.
    # Preflight the sequence in a small temporary tree, without touching source.
    with tempfile.TemporaryDirectory(prefix='pixel-os-preflight-') as directory:
        temporary = Path(directory)
        subprocess.run(['git', '-C', directory, 'init', '-q'], check=True)
        paths = set()
        for patch in patches:
            for line in patch.read_text().splitlines():
                if line.startswith('--- a/'):
                    paths.add(line[6:].split('\t')[0])
        for relative in paths:
            data = subprocess.check_output(['git', '-C', str(dest), 'show', 'HEAD:'+relative])
            target = temporary/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for patch in patches:
            subprocess.run(['git', '-C', directory, 'apply', '--check', str(patch)], check=True)
            subprocess.run(['git', '-C', directory, 'apply', str(patch)], check=True)

# Check every destination before applying any patch or copying any file.
for project, patches in projects.items():
    dest = source/project
    head = subprocess.check_output(['git', '-C', str(dest), 'rev-parse', 'HEAD'], text=True).strip()
    if head != revisions[project]:raise SystemExit(f'Wrong upstream revision for {project}')
    if subprocess.check_output(['git', '-C', str(dest), 'status', '--porcelain']):
        raise SystemExit(f'Local changes present in {project}; refusing to overwrite them')
    check_patch_sequence(dest, [(bundle/'patches'/patch).resolve() for patch in patches])
copies = []
for file in (bundle/'overlays').rglob('*'):
    if not file.is_file():continue
    relative = file.relative_to(bundle/'overlays')
    if is_background_overlay(relative) and policy is None:continue
    copies.append((file, source/relative))
if policy:copies.append((a.background_agent, source/'packages/apps/PixelBackgroundControl/Agent.apk'))
for file, target in copies:
    if target.exists() and target.read_bytes() != file.read_bytes():
        raise SystemExit(f'Unexpected existing overlay target: {target}')
if a.check:
    print('All revisions, patches, overlays and optional exact background pins passed preflight. No files changed.')
    raise SystemExit(0)
for project, patches in projects.items():
    for patch in patches:
        subprocess.run(['git', '-C', str(source/project), 'apply',
            str(bundle/'patches'/patch)], check=True)
for file, target in copies:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(file, target)
if policy:
    config = source/'frameworks/base/core/res/res/values/config.xml'
    text = config.read_text()
    for name, rows in [('Agents', [policy['agent']]), ('Targets', policy['targets'])]:
        marker = f'    <string-array name="config_pixelComputerControl{name}" translatable="false" />'
        assert text.count(marker) == 1
        entries = ''.join(f'        <item>{row["packageName"]}:{cert}</item>\n'
            for row in rows for cert in row['certificates'])
        text = text.replace(marker, f'    <string-array name="config_pixelComputerControl{name}" translatable="false">\n{entries}    </string-array>')
    if policy.get('prototypeControl', False):
        marker = '    <string-array name="config_pixelComputerControlPrototypeAgents" translatable="false" />'
        entries = ''.join(f'        <item>{policy["agent"]["packageName"]}:{cert}</item>\n'
            for cert in policy['agent']['certificates'])
        assert text.count(marker) == 1
        text = text.replace(marker, '    <string-array name="config_pixelComputerControlPrototypeAgents" translatable="false">\n'+entries+'    </string-array>')
    if a.reference_display_address:
        marker = '    <string name="config_pixelComputerControlReferenceDisplayAddress" translatable="false"></string>'
        assert text.count(marker) == 1
        text = text.replace(marker, '    <string name="config_pixelComputerControlReferenceDisplayAddress" translatable="false">'+a.reference_display_address+'</string>')
    if a.locked_control:
        marker = '    <bool name="config_pixelComputerControlLockedSessions">false</bool>'
        assert text.count(marker) == 1
        text = text.replace(marker, '    <bool name="config_pixelComputerControlLockedSessions">true</bool>')
    config.write_text(text)
print('OS changes applied. Nothing was built, signed, or flashed.')
