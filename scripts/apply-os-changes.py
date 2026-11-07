#!/usr/bin/env python3
"""Preflight/apply captured changes to pinned GrapheneOS source; never build or flash."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from integration_copy import Conflict, install_bytes, install_copy
from background_apks import (BACKGROUND_FRAMEWORK_PATCHES, installed_precedence, preinstall_blueprint,
    preinstall_make_block, preinstall_plan, rose_home_projects, validate_policy)

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('source', help='Existing GrapheneOS checkout matching os/pinned-manifest.xml')
p.add_argument('--emulator', action='store_true', help='Also apply the optional Goldfish emulator change')
p.add_argument('--background-policy', type=Path, help='Local receipt from prepare-background-policy.py')
p.add_argument('--background-agent', type=Path, help='The exact signed Agent.apk verified in that receipt')
p.add_argument('--reference-display-address', help='Optional physical cover display address for a Fold build')
p.add_argument('--locked-control', action='store_true',
    help='Owner opt-in: let the pinned prototype controller start background sessions while locked. Product builds '
    'always carry the locked-session resources and owner-gated guards with this switched off')
p.add_argument('--agent-tray', action='store_true',
    help='Enable the Rose Agent status tray in the system image (persist.wm.debug.pixel_agent_tray=1)')
p.add_argument('--updater-url',
    help='Release channel base URL for the system Updater; must match the installed phone (https, ends with /)')
p.add_argument('--updater-published-channel', action='append', default=[],
    help='Wire channel with published DEVICE-channel metadata under --updater-url (repeatable; default: stable, '
    'the existing Pixel Beta feed). Other choices show as not available and are never fetched')
p.add_argument('--updater-release-index',
    help='GitHub release list of the --updater-url repository, for a device published as separate releases '
    '(Rango): each check resolves one eligible DEVICE-N release and reads its metadata and OTA from it')
p.add_argument('--keep-upstream-updater', action='store_true',
    help='Deliberately leave the Updater polling GrapheneOS (not for a build that updates an existing RoseOS phone)')
p.add_argument('--preinstall-apk', type=Path, action='append', default=[],
    help='Preinstall this exact signed Rose Agent or target APK from the policy receipt on the product partition (repeatable)')
p.add_argument('--installed-identities', type=Path,
    help='The phone\'s installed app identities (expected-identities.json shape); refuse bundled apps '
    'that would not replace them (versionCode not higher, or a different signer)')
p.add_argument('--check', action='store_true', help='Run all preflight checks without changing source')
a = p.parse_args()
if bool(a.background_policy) != bool(a.background_agent):
    p.error('--background-policy and --background-agent must be supplied together')
if a.preinstall_apk and not a.background_policy:
    p.error('--preinstall-apk needs --background-policy and --background-agent')
if a.installed_identities and not a.background_policy:
    p.error('--installed-identities needs --background-policy and --background-agent')
if a.locked_control and not a.background_policy:
    p.error('--locked-control needs --background-policy and --background-agent')
if a.reference_display_address and (not a.background_policy or not a.reference_display_address.isdecimal()
        or not 0 < int(a.reference_display_address) < 2**63):
    p.error('Reference display needs background policy and a positive signed-64-bit physical address')
if a.updater_url and (not a.updater_url.startswith('https://') or not a.updater_url.endswith('/')
        or any(c in a.updater_url for c in '<>&"\' \n')):
    p.error('--updater-url must be an https URL ending with / and without XML special characters')
if a.updater_published_channel and not a.updater_url:
    p.error('--updater-published-channel needs --updater-url')
if any(not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,31}', c) for c in a.updater_published_channel):
    p.error('--updater-published-channel must be a lowercase channel token such as stable')
published_channels = sorted(set(a.updater_published_channel or ['stable']))
# A per-release device: --updater-url is the repository's .../releases/download/ prefix and the index its release list.
RELEASE_REPO = re.compile(r'https://github\.com/([A-Za-z0-9-]+/[A-Za-z0-9._-]+)/releases/download/')
if a.updater_release_index:
    repo = RELEASE_REPO.fullmatch(a.updater_url or '')
    if not repo or a.updater_release_index != f'https://api.github.com/repos/{repo[1]}/releases':
        p.error('--updater-release-index must be https://api.github.com/repos/OWNER/REPO/releases with --updater-url '
            'https://github.com/OWNER/REPO/releases/download/ of the same repository')
if a.updater_url and a.keep_upstream_updater:
    p.error('--updater-url and --keep-upstream-updater are mutually exclusive')
# A product build that keeps the upstream URL moves the installed phone to GrapheneOS's channel,
# whose releases it cannot verify, and strands it until reflashed.
if (a.background_policy or a.preinstall_apk or a.agent_tray or a.locked_control) \
        and not a.updater_url and not a.keep_upstream_updater:
    p.error('A RoseOS product build needs --updater-url (the installed phone\'s channel); '
        'pass --keep-upstream-updater only for a build that will not update an existing phone')
source = Path(a.source).resolve()
bundle = Path(__file__).resolve().parents[1]/'os'
manifest = ET.parse(bundle/'pinned-manifest.xml')
revisions = {n.get('path', n.get('name')): n.get('revision') for n in manifest.findall('project')}
projects = {'frameworks/base': ['frameworks-base.patch', 'rose-agent-self-update-frameworks-base.patch']}
if a.agent_tray:projects['build/make'] = ['agent-tray-build-make.patch']
if a.emulator:projects['device/generic/goldfish'] = ['goldfish.patch']
# Upstream Updater polls GrapheneOS; a differently signed build must name its own channel.
UPDATER_CONFIG = 'packages/apps/Updater/res/values/config.xml'
UPDATER_MARKER = '    <string name="url" translatable="false">https://releases.grapheneos.org/</string>'
# RoseOS channel choices (Prod/Beta/Alpha, Beta = existing "stable" feed) ride on the same Updater change.
UPDATER_PUBLISHED = '    <string-array name="channel_published" translatable="false">\n        <item>stable</item>\n    </string-array>\n'
if a.updater_url:projects['packages/apps/Updater'] = ['updater-rose-channels.patch']
# Release discovery rides after the channels; without --updater-release-index the Updater is byte-identical.
if a.updater_release_index:projects['packages/apps/Updater'].append('updater-rose-releases.patch')
UPDATER_RELEASES = 'packages/apps/Updater/src/app/seamlessupdate/client/RoseReleases.java'
UPDATER_INDEX = '    <string name="release_index" translatable="false"></string>'
# Upstream lists the Updater module only for OFFICIAL_BUILD (media_system.mk), so a RoseOS build omitted it.
# Include it exactly when its channel is set; a --keep-upstream-updater build ships none.
if a.updater_url:projects.setdefault('build/make', []).append('updater-product-build-make.patch')
policy = None
if a.background_policy:
    policy = validate_policy(json.loads(a.background_policy.read_text()), a.background_agent)
    if a.locked_control and not policy.get('prototypeControl', False):
        p.error('--locked-control needs a policy prepared with --prototype-control')
    projects['frameworks/base'] += BACKGROUND_FRAMEWORK_PATCHES
    projects.setdefault('build/make', []).append('background-control-build-make.patch')
    projects['build/release'] = ['background-control-build-release.patch']
    # #156: Rose Agent's work row in Launcher3's fallback overview (Rose holds HOME); needs the controller above.
    # #410: the raised edge after it: E2a's short-swipe gesture, then E2c's shell hand-off and edge window.
    projects['packages/apps/Launcher3'] = ['rose-switcher-launcher3.patch', 'rose-edge-launcher3.patch',
        'rose-edge-resize-launcher3.patch']
    # #410: WMShell's real app resize and the edge's mic slot (OS Core, #516/#523/#528).
    projects['frameworks/base'].append('rose-edge-wmshell.patch')
    # 1G: the Rose notification shade's one SystemUI binding (OS Core): RoseShadeModule in ReferenceSystemUIModule.
    projects['frameworks/base'].append('rose-shade-systemui.patch')
    # #410: the edge's switch (persist.wm.debug.rose_edge); the mic slot is the tray's own control, so with the tray.
    if a.agent_tray:projects['build/make'].append('rose-edge-build-make.patch')
preinstalls = preinstall_plan(policy, a.preinstall_apk) if a.preinstall_apk else []
projects.update(rose_home_projects(preinstalls))
if a.installed_identities:
    bundled = [policy['agent']] + [row for row in [policy['voice'], *policy['targets']]
        if row['packageName'] in {p['packageName'] for p in preinstalls}]
    issues, notes = installed_precedence(bundled, json.loads(a.installed_identities.read_text()))
    for note in notes:print('Note: ' + note)
    if issues:raise SystemExit('Bundled apps would not replace the installed copies:\n  ' + '\n  '.join(issues))
PRODUCT_MAKEFILE = 'target/product/handheld_product.mk'

def is_updater_overlay(path):
    return str(path).startswith('packages/apps/Updater/')

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
if preinstalls:
    product = subprocess.check_output(['git', '-C', str(source/'build/make'), 'show', 'HEAD:'+PRODUCT_MAKEFILE], text=True)
    if 'RosePreinstall_' in product:raise SystemExit('Preinstall entries already present in '+PRODUCT_MAKEFILE)
if a.updater_url:
    config = subprocess.check_output(['git', '-C', str(source/'packages/apps/Updater'), 'show',
        'HEAD:res/values/config.xml'], text=True)
    if config.count(UPDATER_MARKER) != 1:raise SystemExit('Updater channel URL setting not found as expected')
copies = []
for file in (bundle/'overlays').rglob('*'):
    if not file.is_file():continue
    relative = file.relative_to(bundle/'overlays')
    if is_background_overlay(relative) and policy is None:continue
    if is_updater_overlay(relative) and not a.updater_url:continue
    if str(relative) == UPDATER_RELEASES and not a.updater_release_index:continue
    copies.append((file, source/relative))
if policy:copies.append((a.background_agent, source/'packages/apps/PixelBackgroundControl/Agent.apk'))
generated = []
for row in preinstalls:
    directory = source/'packages/apps/RosePreinstall'/row['module']
    copies.append((row['apk'], directory/'app.apk'))
    generated.append((directory/'Android.bp', preinstall_blueprint(row['module']).encode()))
for file, target in copies:
    if target.exists() and target.read_bytes() != file.read_bytes():
        raise SystemExit(f'Unexpected existing overlay target: {target}')
for target, data in generated:
    if target.exists() and target.read_bytes() != data:
        raise SystemExit(f'Unexpected existing overlay target: {target}')
if a.check:
    print('All revisions, patches, overlays and optional exact background pins passed preflight. No files changed.')
    raise SystemExit(0)
for project, patches in projects.items():
    for patch in patches:
        subprocess.run(['git', '-C', str(source/project), 'apply',
            str(bundle/'patches'/patch)], check=True)
# Fresh mtimes so incremental builds rebuild from replaced inputs; identical bytes stay untouched.
try:
    for file, target in copies:
        install_copy(file, target)
    for target, data in generated:
        install_bytes(target, data)
except Conflict as error:
    raise SystemExit(str(error))
if preinstalls:
    product = source/'build/make'/PRODUCT_MAKEFILE
    product.write_text(product.read_text() + preinstall_make_block([row['module'] for row in preinstalls]))
if a.updater_url:
    config = source/UPDATER_CONFIG
    text = config.read_text()
    if text.count(UPDATER_PUBLISHED) != 1:raise SystemExit('Updater published-channel list not found as expected')
    items = ''.join(f'        <item>{c}</item>\n' for c in published_channels)
    config.write_text(text.replace(UPDATER_MARKER,
        f'    <string name="url" translatable="false">{a.updater_url}</string>').replace(UPDATER_PUBLISHED,
        '    <string-array name="channel_published" translatable="false">\n' + items + '    </string-array>\n'))
    if a.updater_release_index:
        text = config.read_text()
        if text.count(UPDATER_INDEX) != 1:raise SystemExit('Updater release index setting not found as expected')
        config.write_text(text.replace(UPDATER_INDEX,
            f'    <string name="release_index" translatable="false">{a.updater_release_index}</string>'))
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
