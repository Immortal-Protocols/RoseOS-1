#!/usr/bin/env python3
"""OS Product (#410): the input contract for a compile-only Launcher3QuickStepLib proof of RoseOS's Launcher3 changes.

Release owns the adapter that uses it; this file only states the inputs. The manifest,
os/compile-contracts/launcher3-quickstep-lib.json, names every affected file in the three projects that change what
Launcher3QuickStepLib compiles against (frameworks/base, packages/apps/Launcher3, build/release). For each it gives the
pinned pre-image (sha256 and git blob id, or absent for a new file) and the post-image after the ordered patches, and it
lists the overlays copied into those projects. The adapter takes exactly one of two branches and refuses anything else:

- verifyIntegrated: every affected file already equals its post-image (config.xml after reversing the integration
  rewrites below), every required overlay is present with its hash, and no patch is applied again.
- reconstruct: every affected file equals its pinned pre-image (and its pinned git blob), new files are absent, and
  the patches are applied in order to give exactly the post-images.
- Any other state (some files pre, some post, an unknown hash, a missing input) is refused. Nothing is reset.

Usage:
  scripts/launcher3-compile-contract.py          offline: check the committed manifest against the committed os/
                                                 bundle (patch and overlay bytes, Launcher3 fixtures, pins)
  scripts/launcher3-compile-contract.py --write  regenerate it: fetches the pinned pre-images read-only from
                                                 GrapheneOS's public GitHub at the pinned SHAs (no host, no
                                                 credentials), applies the patches in a scratch git tree
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT/'os/compile-contracts/launcher3-quickstep-lib.json'
PINNED = ROOT/'os/pinned-manifest.xml'
FIXTURES = ROOT/'os/tests/fixtures/rose-switcher'
# The order apply-os-changes.py applies them with --background-policy (scripts/background_apks.py).
PROJECTS = [
    ('frameworks/base', 'platform_frameworks_base', ['frameworks-base.patch',
        'rose-agent-self-update-frameworks-base.patch', 'background-control-frameworks-base.patch',
        'background-control-modern-frameworks-base.patch', 'background-control-resource-import-fix.patch',
        'locked-control-frameworks-base.patch']),
    ('packages/apps/Launcher3', 'platform_packages_apps_Launcher3', ['rose-switcher-launcher3.patch',
        'rose-edge-launcher3.patch']),
    ('build/release', 'platform_build_release', ['background-control-build-release.patch']),
]
REQUIRED_OVERLAYS = ('packages/apps/Launcher3/', 'build/release/')
# apply-os-changes.py rewrites these five marker elements of frameworks/base config.xml from the background policy
# after patching. The contract compares an integrated config.xml after putting each element back to its marker.
CONFIG = 'core/res/res/values/config.xml'
CONFIG_MARKERS = [
    ('string-array', 'config_pixelComputerControlAgents',
        '    <string-array name="config_pixelComputerControlAgents" translatable="false" />'),
    ('string-array', 'config_pixelComputerControlTargets',
        '    <string-array name="config_pixelComputerControlTargets" translatable="false" />'),
    ('string-array', 'config_pixelComputerControlPrototypeAgents',
        '    <string-array name="config_pixelComputerControlPrototypeAgents" translatable="false" />'),
    ('string', 'config_pixelComputerControlReferenceDisplayAddress',
        '    <string name="config_pixelComputerControlReferenceDisplayAddress" translatable="false"></string>'),
    ('bool', 'config_pixelComputerControlLockedSessions',
        '    <bool name="config_pixelComputerControlLockedSessions">false</bool>'),
]


def normalize_config(text):
    """An integrated config.xml with apply-os-changes.py's five policy rewrites put back to their markers."""
    for kind, name, marker in CONFIG_MARKERS:
        if kind == 'string-array':
            pattern = r'    <string-array name="%s" translatable="false"(?: />|>\n(?:        <item>[^<\n]*</item>\n)*    </string-array>)' % name
        elif kind == 'string':
            pattern = r'    <string name="%s" translatable="false">[^<\n]*</string>' % name
        else:
            pattern = r'    <bool name="%s">(?:true|false)</bool>' % name
        found = re.findall(pattern, text)
        if len(found) != 1:
            raise ValueError(f'{name}: expected exactly one element, found {len(found)}')
        text = re.sub(pattern, lambda m: marker, text)
    return text


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git_blob(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def pins():
    xml = PINNED.read_text()
    out = {}
    for path, _, _ in PROJECTS:
        m = re.search(r'<project name="[^"]+" path="%s" [^>]*revision="([0-9a-f]{40})"' % re.escape(path), xml)
        if not m:
            raise SystemExit(f'{path}: no pinned revision in {PINNED}')
        out[path] = m.group(1)
    return out


def file_blocks(patch):
    """[(path, new, index)] for every file a patch changes, from its `--- a/` / `+++ b/` pairs (some patches here use
    plain unified headers without `diff --git`), with the `index a..b` line of the same block when there is one."""
    lines = (ROOT/'os/patches'/patch).read_text().split('\n')
    out, index = [], None
    for i, line in enumerate(lines):
        if line.startswith('diff --git '):
            index = None
        elif line.startswith('index ') and re.match(r'index [0-9a-f]+\.\.[0-9a-f]+', line):
            index = list(re.match(r'index ([0-9a-f]+)\.\.([0-9a-f]+)', line).groups())
        elif line.startswith('--- ') and i + 1 < len(lines) and lines[i + 1].startswith('+++ '):
            old, new = line[4:].split('\t')[0], lines[i + 1][4:].split('\t')[0]
            if new == '/dev/null':
                raise SystemExit(f'{patch}: deletes {old}; not expected here')
            out.append((new[2:], old == '/dev/null', index))
            index = None
    return out


def touched(patches):
    """Paths in first-touch order, each with whether the patch sequence creates it."""
    files = {}
    for patch in patches:
        for path, new, _ in file_blocks(patch):
            files.setdefault(path, {'new': new})
    return files


def patch_index(patch):
    """{path: [pre blob prefix, post blob prefix] or None} from one patch's own `index a..b` lines."""
    return {path: index for path, _, index in file_blocks(patch)}


def overlays():
    rows = []
    for file in sorted((ROOT/'os/overlays').rglob('*')):
        if not file.is_file():
            continue
        target = str(file.relative_to(ROOT/'os/overlays'))
        project = next((p for p, _, _ in PROJECTS if target.startswith(p + '/')), None)
        if project is None:
            continue
        rows.append({'target': target, 'sha256': sha256(file.read_bytes()),
            'role': 'required' if target.startswith(REQUIRED_OVERLAYS) else 'notAnInput'})
    return rows


def apply_in_scratch(pre, patches):
    """{path: bytes or None} -> {path: bytes} after applying the patches in order with git apply."""
    tree = Path(tempfile.mkdtemp(prefix='l3-contract-'))
    try:
        for path, data in pre.items():
            if data is not None:
                (tree/path).parent.mkdir(parents=True, exist_ok=True)
                (tree/path).write_bytes(data)
        subprocess.run(['git', 'init', '-q', str(tree)], check=True)
        chain = {path: [] for path in pre}
        for patch in patches:
            index = patch_index(patch)
            before = {f: git_blob((tree/f).read_bytes()) if (tree/f).exists() else None for f in index}
            subprocess.run(['git', '-C', str(tree), 'apply', str(ROOT/'os/patches'/patch)], check=True)
            for f, lines in index.items():
                after = git_blob((tree/f).read_bytes())
                if lines is None:
                    base = 'noIndex'
                elif before[f] is None and set(lines[0]) == {'0'} or before[f] and before[f].startswith(lines[0]):
                    base = 'exact'
                    if not after.startswith(lines[1]):
                        raise SystemExit(f'{patch} {f}: authored on this base but produces {after}, not {lines[1]}')
                else:
                    base = 'different'
                chain[f].append({'patch': patch, 'before': before[f], 'after': after, 'index': lines, 'base': base})
        apply_in_scratch.chain = chain
        return {path: (tree/path).read_bytes() for path in pre}
    finally:
        shutil.rmtree(tree, True)


def fetch(repo, revision, path):
    url = f'https://raw.githubusercontent.com/GrapheneOS/{repo}/{revision}/{path}'
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def build():
    revisions = pins()
    projects = []
    for path, repo, patches in PROJECTS:
        files = touched(patches)
        pre = {f: None if meta['new'] else fetch(repo, revisions[path], f) for f, meta in files.items()}
        post = apply_in_scratch(pre, patches)
        rows = []
        for f, meta in files.items():
            row = {'path': f, 'pre': None if pre[f] is None else {'sha256': sha256(pre[f]), 'gitBlob': git_blob(pre[f])},
                'post': {'sha256': sha256(post[f]), 'gitBlob': git_blob(post[f])},
                'chain': apply_in_scratch.chain[f]}
            if path == 'frameworks/base' and f == CONFIG:
                row['integrationRewrite'] = {'by': 'scripts/apply-os-changes.py (background policy)',
                    'compare': 'normalize_config(integrated bytes) must equal post',
                    'elements': [name for _, name, _ in CONFIG_MARKERS]}
            rows.append(row)
        projects.append({'path': path, 'repo': f'GrapheneOS/{repo}', 'revision': revisions[path],
            'patches': [{'name': p, 'sha256': sha256((ROOT/'os/patches'/p).read_bytes())} for p in patches],
            'files': rows})
    return {
        'schemaVersion': 1,
        'kind': 'rose-launcher3-compile-inputs',
        'owner': 'OS Product (#410). Release owns the adapter; this states inputs only.',
        'target': 'Launcher3QuickStepLib',
        'proves': 'the patched handlers and overlays type-check and compile on the real classpath, plus the '
            'generated aconfig values. Not R8, manifest merge, APK resources, packaging, signing or runtime.',
        'branches': {
            'verifyIntegrated': 'every affected file equals post (config.xml via integrationRewrite), every required '
                'overlay is present with its sha256, no patch is applied again',
            'reconstruct': 'every affected file equals pre (sha256 and gitBlob at revision), new files are absent, '
                'patches are applied in order and must give exactly post; required overlays are then copied',
            'otherwise': 'refuse: mixed pre/post, unknown hash, missing file or overlay. Never reset or trust a snapshot.',
        },
        'projects': projects,
        'overlays': overlays(),
        'unaffected': 'every other project the library or lunch needs must be byte-identical to its pinned revision; '
            'that is Infra provenance, not this contract',
        'notInputs': ['packages/apps/PixelBackgroundControl and Agent.apk', 'policy receipt', 'build/make patches',
            'packages/apps/Updater', 'packages/modules/Permission', 'preinstall APKs',
            'frameworks/base overlays (WindowManager-Shell, SystemUI-core: not on the library classpath)'],
        'outputsToBind': [
            'Soong source and resource lists of Launcher3QuickStepLib contain every Launcher3 post-image and '
                'required overlay at these hashes',
            'its javac/kotlinc classpath contains the patched framework (AutomatedPackageListener declares '
                'onAutomatedDevicesChanged)',
            'm Launcher3QuickStepLib succeeds',
            'aconfig cache: com.android.launcher3.enable_fallback_overview_in_window DISABLED/READ_ONLY',
            'aconfig cache: android.security.content_uri_permission_apis ENABLED/READ_ONLY',
            'lunch product, variant, BUILD_ID and release-config maps in effect',
        ],
        'identitySharing': {
            'sites': ['quickstep/src/com/android/quickstep/FallbackSwipeHandler.java startHomeIntent',
                'quickstep/src/com/android/quickstep/window/RecentsWindowSwipeHandler.java (inactive here)'],
            'change': 'ActivityOptions.makeCustomAnimation(mContext, 0, 0).setShareIdentityEnabled(true)',
            'appliesWithEdgeOff': True,
            'consumer': "Home replies to gesture_nav_contract_v1 only through its fail-closed caller gate "
                "(#410 6594409930 section 4); no dedicated exported entry",
        },
    }


def check(manifest):
    """Everything that can be checked offline against the committed os/ bundle."""
    errors = []
    bound = [0, 0]
    revisions = pins()
    if [p['path'] for p in manifest['projects']] != [p for p, _, _ in PROJECTS]:
        errors.append('projects differ from PROJECTS')
    for project, (path, repo, patches) in zip(manifest['projects'], PROJECTS):
        if project['revision'] != revisions[path]:
            errors.append(f'{path}: revision {project["revision"]} is not the pinned {revisions[path]}')
        expected = [{'name': p, 'sha256': sha256((ROOT/'os/patches'/p).read_bytes())} for p in patches]
        if project['patches'] != expected:
            errors.append(f'{path}: patch list or bytes changed; regenerate with --write')
        files = touched(patches)
        if [r['path'] for r in project['files']] != list(files):
            errors.append(f'{path}: affected files differ from the patch headers')
        parsed = {p: patch_index(p) for p in patches}
        for row in project['files']:
            if (row['pre'] is None) != files.get(row['path'], {}).get('new', False):
                errors.append(f'{path}/{row["path"]}: new-file state disagrees with the patch')
            chain = row['chain']
            steps = [p for p in patches if row['path'] in parsed[p]]
            if [c['patch'] for c in chain] != steps:
                errors.append(f'{path}/{row["path"]}: chain does not follow the patch order')
                continue
            if chain[0]['before'] != (row['pre'] and row['pre']['gitBlob']) or chain[-1]['after'] != row['post']['gitBlob']:
                errors.append(f'{path}/{row["path"]}: chain ends are not the pre- and post-images')
            for c, nxt in zip(chain, chain[1:]):
                if c['after'] != nxt['before']:
                    errors.append(f'{path}/{row["path"]}: chain is broken after {c["patch"]}')
            for c in chain:
                lines = parsed[c['patch']][row['path']]
                if c['index'] != lines:
                    errors.append(f'{path}/{row["path"]}: {c["patch"]} index lines changed')
                elif c['base'] == 'exact':
                    bound[0] += 1
                    pre_ok = c['before'] is None and set(lines[0]) == {'0'} or c['before'] and c['before'].startswith(lines[0])
                    if not (pre_ok and c['after'].startswith(lines[1])):
                        errors.append(f'{path}/{row["path"]}: {c["patch"]} does not produce the blob its index records')
                elif c['base'] == 'different':
                    bound[1] += 1
                    if c['before'] and c['before'].startswith(lines[0]):
                        errors.append(f'{path}/{row["path"]}: {c["patch"]} is marked different-base but matches')
                elif lines is not None:
                    errors.append(f'{path}/{row["path"]}: {c["patch"]} has index lines but is marked noIndex')
    launcher = manifest['projects'][1]
    pre = {}
    for row in launcher['files']:
        fixture = FIXTURES/Path(row['path']).name
        if row['pre'] is None:
            pre[row['path']] = None
        elif fixture.exists() and sha256(fixture.read_bytes()) == row['pre']['sha256']:
            pre[row['path']] = fixture.read_bytes()
        else:
            errors.append(f'Launcher3 {row["path"]}: no pinned fixture with the contract pre-image')
    if len(pre) == len(launcher['files']):
        post = apply_in_scratch(pre, PROJECTS[1][2])
        for row in launcher['files']:
            if sha256(post[row['path']]) != row['post']['sha256']:
                errors.append(f'Launcher3 {row["path"]}: post-image differs from the contract')
            if row['pre'] is not None and git_blob(pre[row['path']]) != row['pre']['gitBlob']:
                errors.append(f'Launcher3 {row["path"]}: git blob differs')
    if manifest['overlays'] != overlays():
        errors.append('overlays changed; regenerate with --write')
    check.bound = tuple(bound)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--write', action='store_true', help='regenerate from pinned public sources')
    a = parser.parse_args()
    if a.write:
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(build(), indent=1) + '\n')
    errors = check(json.loads(MANIFEST.read_text()))
    for error in errors:
        print('FAIL:', error)
    if errors:
        raise SystemExit(1)
    total = sum(len(p['files']) for p in json.loads(MANIFEST.read_text())['projects'])
    print(f'{MANIFEST.relative_to(ROOT)} matches the committed os/ bundle (sha256 {sha256(MANIFEST.read_bytes())}); '
        f'{check.bound[0]} patch steps match the blobs their own index lines record; {check.bound[1]} were authored on a '
        f'different base and apply by context (recorded per file)')


if __name__ == '__main__':
    main()
