#!/usr/bin/env python3
"""OS Product (#410): the input contract for a compile-only Launcher3QuickStepLib proof of RoseOS's Launcher3 changes.

Release owns the adapter that uses it; this file only states the inputs. The manifest,
os/compile-contracts/launcher3-quickstep-lib.json, names every affected file in the three projects that change what
Launcher3QuickStepLib compiles against (frameworks/base, packages/apps/Launcher3, build/release). For each it gives the
pinned pre-image (sha256 and git blob id, or absent for a new file), the post-image after the ordered patches and the
blob chain between them, and it lists the overlays copied into those projects. Its hashes are expected values derived
from pinned public sources and the committed patches; an adapter compares current bytes with them and never records
the current bytes' hashes as provenance.

The patches are those of this candidate: apply-os-changes.py's --background-policy order for frameworks/base and
build/release, and for Launcher3 rose-switcher-launcher3.patch then rose-edge-launcher3.patch. apply-os-changes.py
does not register rose-edge-launcher3.patch yet, so a tree it integrates today is a mixed state for this contract.

The adapter takes exactly one of two branches and refuses anything else:
- verifyIntegrated: every affected file already equals its post-image, every required overlay is present with its
  hash, and no patch is applied again. config.xml is the one file the patcher rewrites afterwards: it is accepted only
  through verify_integrated_config, which needs the separately bound generating inputs.
- reconstruct: every affected file equals its pinned pre-image (and its pinned git blob), new files are absent, and
  the patches are applied in order to give exactly the post-images.
- Any other state (some files pre and some post, an unknown hash, a missing input or binding) is refused. Nothing is
  reset; no snapshot is trusted.

Usage:
  scripts/launcher3-compile-contract.py          offline: recompute every pre-image, post-image and chain from the
                                                 committed fixtures and patches, and compare with the manifest
  scripts/launcher3-compile-contract.py --write  regenerate it: fetches the pinned pre-images read-only from
                                                 GrapheneOS's public GitHub at the pinned SHAs (no host, no
                                                 credentials) into the fixtures, applies the patches in scratch
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT/'os/compile-contracts/launcher3-quickstep-lib.json'
PINNED = ROOT/'os/pinned-manifest.xml'
# Pinned pre-images, byte for byte: Launcher3's are the switcher fixtures (by file name); the others are stored
# under their project paths.
LAUNCHER3_FIXTURES = ROOT/'os/tests/fixtures/rose-switcher'
FIXTURES = ROOT/'os/tests/fixtures/launcher3-compile-contract'
# frameworks/base and build/release: apply-os-changes.py's --background-policy order (scripts/background_apks.py).
# Launcher3: this candidate's two patches (apply-os-changes.py:106 lists only the first so far).
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

# apply-os-changes.py rewrites exactly these five elements of frameworks/base config.xml after patching, from the
# background policy receipt and two flags. Each is either its marker (left alone) or the exact text written below.
CONFIG = 'core/res/res/values/config.xml'
ARRAY = '    <string-array name="config_pixelComputerControl{name}" translatable="false" />'
CONFIG_MARKERS = {
    'config_pixelComputerControlAgents': ARRAY.format(name='Agents'),
    'config_pixelComputerControlTargets': ARRAY.format(name='Targets'),
    'config_pixelComputerControlPrototypeAgents': ARRAY.format(name='PrototypeAgents'),
    'config_pixelComputerControlReferenceDisplayAddress':
        '    <string name="config_pixelComputerControlReferenceDisplayAddress" translatable="false"></string>',
    'config_pixelComputerControlLockedSessions': '    <bool name="config_pixelComputerControlLockedSessions">false</bool>',
}
CERT = re.compile(r'[0-9A-F]{64}')
PACKAGE = re.compile(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+')


def expected_config_elements(policy, reference_display_address=None, locked_control=False):
    """{element name: exact text} that apply-os-changes.py writes for these inputs (scripts/apply-os-changes.py, the
    `if policy:` block). policy is the background policy receipt; it must be the separately bound receipt."""
    agent, targets = policy['agent'], policy['targets']
    rows = [agent] + list(targets)
    for row in rows:
        if not PACKAGE.fullmatch(row['packageName']) or not row['certificates'] or not all(
                isinstance(c, str) and CERT.fullmatch(c) for c in row['certificates']):
            raise ValueError(f'policy row {row.get("packageName")!r}: not a package with uppercase SHA-256 certificates')
    if reference_display_address is not None and not (str(reference_display_address).isdecimal()
            and 0 < int(reference_display_address) < 2**63):
        raise ValueError('reference display address: not a positive signed-64-bit number')

    def array(name, items):
        entries = ''.join(f'        <item>{row["packageName"]}:{cert}</item>\n' for row in items for cert in row['certificates'])
        return f'    <string-array name="config_pixelComputerControl{name}" translatable="false">\n{entries}    </string-array>'

    out = dict(CONFIG_MARKERS)
    out['config_pixelComputerControlAgents'] = array('Agents', [agent])
    out['config_pixelComputerControlTargets'] = array('Targets', targets)
    if policy.get('prototypeControl', False):
        out['config_pixelComputerControlPrototypeAgents'] = array('PrototypeAgents', [agent])
    if reference_display_address is not None:
        out['config_pixelComputerControlReferenceDisplayAddress'] = ('    <string name="config_pixelComputerControl'
            f'ReferenceDisplayAddress" translatable="false">{reference_display_address}</string>')
    if locked_control:
        out['config_pixelComputerControlLockedSessions'] = \
            '    <bool name="config_pixelComputerControlLockedSessions">true</bool>'
    return out


def verify_integrated_config(integrated, post_sha256, policy, policy_sha256, bound_policy_sha256,
        reference_display_address=None, locked_control=False):
    """Errors (empty when accepted) for an integrated config.xml. Accepted only when the policy receipt's sha256 equals
    the one bound for this tree outside this contract, each of the five elements is exactly the text apply-os-changes.py
    writes from those inputs, and putting exactly those texts back to their markers gives the post-image. Nothing else
    is normalised: any other difference, a missing input or a missing binding refuses."""
    errors = []
    if not bound_policy_sha256 or policy is None or policy_sha256 != bound_policy_sha256:
        return ['the policy receipt is missing or not the one bound for this tree']
    try:
        expected = expected_config_elements(policy, reference_display_address, locked_control)
    except (KeyError, TypeError, ValueError) as error:
        return [f'policy inputs refused: {error}']
    try:
        text = integrated.decode('utf-8')
    except UnicodeDecodeError:
        return ['config.xml is not UTF-8']
    for name, wanted in expected.items():
        # The element's opening must appear exactly once, and exactly as the expected text.
        opening = re.findall(r'<(?:string-array|string|bool) name="%s"' % name, text)
        if len(opening) != 1:
            errors.append(f'{name}: {len(opening)} elements, expected exactly one')
        elif text.count(wanted) != 1:
            errors.append(f'{name}: not the exact text apply-os-changes.py writes from the bound inputs')
        else:
            text = text.replace(wanted, CONFIG_MARKERS[name])
    if not errors and sha256(text.encode('utf-8')) != post_sha256:
        errors.append('config.xml differs from the post-image outside the five policy elements')
    return errors


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
    """({path: bytes}, {path: chain}) after applying the patches in order with git apply to {path: bytes or None}."""
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
        return {path: (tree/path).read_bytes() for path in pre}, chain
    finally:
        shutil.rmtree(tree, True)


def fixture_path(project, path):
    if project == 'packages/apps/Launcher3':
        return LAUNCHER3_FIXTURES/Path(path).name
    return FIXTURES/project/path


def fetch(repo, revision, path):
    url = f'https://raw.githubusercontent.com/GrapheneOS/{repo}/{revision}/{path}'
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def build():
    revisions = pins()
    projects = []
    for path, repo, patches in PROJECTS:
        files = touched(patches)
        pre = {}
        for f, meta in files.items():
            if meta['new']:
                pre[f] = None
                continue
            pre[f] = fetch(repo, revisions[path], f)
            target = fixture_path(path, f)
            if path == 'packages/apps/Launcher3':
                if not target.exists() or target.read_bytes() != pre[f]:
                    raise SystemExit(f'{target}: the Launcher3 fixture is not the pinned file')
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(pre[f])
        post, chain = apply_in_scratch(pre, patches)
        rows = []
        for f in files:
            row = {'path': f, 'pre': None if pre[f] is None else {'sha256': sha256(pre[f]), 'gitBlob': git_blob(pre[f])},
                'post': {'sha256': sha256(post[f]), 'gitBlob': git_blob(post[f])}, 'chain': chain[f]}
            if path == 'frameworks/base' and f == CONFIG:
                row['integrationRewrite'] = {
                    'by': 'scripts/apply-os-changes.py, the `if policy:` block, after patching',
                    'elements': list(CONFIG_MARKERS),
                    'generatingInputs': ['the background policy receipt (agent, targets, prototypeControl), whose '
                        'sha256 must equal the one bound for that tree outside this contract',
                        '--reference-display-address (absent unless given)', '--locked-control (false unless given)'],
                    'compare': 'scripts/launcher3-compile-contract.py verify_integrated_config: each element must be '
                        'exactly the text the patcher writes from those inputs, and only those texts are put back to '
                        'their markers before comparing with post; a missing input or binding, or any other '
                        'difference, refuses',
                }
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
        'provenance': 'every hash here is an expected value derived from pinned public sources and the committed '
            'patches; an adapter compares current bytes with them and never records current bytes as provenance',
        'candidatePatches': 'frameworks/base and build/release in apply-os-changes.py --background-policy order; '
            'Launcher3 adds rose-edge-launcher3.patch, which apply-os-changes.py does not register yet, so a tree '
            'integrated by the current patcher is a mixed state for this contract and is refused',
        'branches': {
            'verifyIntegrated': 'every affected file equals post (config.xml only through integrationRewrite with its '
                'bound inputs), every required overlay is present with its sha256, no patch is applied again',
            'reconstruct': 'every affected file equals pre (sha256 and gitBlob at revision), new files and every '
                'overlay target are absent beforehand, patches are applied in order and must give exactly post; '
                'required overlays are then copied',
            'otherwise': 'refuse: mixed pre/post, unknown hash, missing file, overlay, input or binding. A tree '
                'integrated by the current patcher (Launcher3 switcher only) is such a state and must not be completed '
                'by applying rose-edge-launcher3.patch alone. Never reset or trust a snapshot.',
        },
        'projects': projects,
        'overlays': overlays(),
        'unaffected': 'inside the three projects, every path not listed in files or overlays must equal the pinned '
            'revision\'s tree, and any extra file is refused; every other project the library or lunch needs must be '
            'byte-identical to its pinned revision (Infra provenance, not this contract)',
        'notInputs': ['packages/apps/PixelBackgroundControl and Agent.apk', 'the policy receipt in reconstruct (only '
            'verifyIntegrated needs it, bound, to check config.xml)', 'build/make patches', 'packages/apps/Updater',
            'packages/modules/Permission', 'preinstall APKs',
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
            'consumer': "requirement on Home, not implemented or tested in this repository yet: reply to "
                "gesture_nav_contract_v1 only through a fail-closed caller gate (#410 6594409930 section 4); no "
                "dedicated exported entry",
            'consumerImplemented': False,
        },
    }


def check(manifest):
    """Recompute everything from the committed fixtures and patches and compare with the manifest."""
    errors = []
    bound = [0, 0]
    revisions = pins()
    if [p['path'] for p in manifest['projects']] != [p for p, _, _ in PROJECTS]:
        return ['projects differ from PROJECTS']
    for project, (path, repo, patches) in zip(manifest['projects'], PROJECTS):
        if project['revision'] != revisions[path]:
            errors.append(f'{path}: revision {project["revision"]} is not the pinned {revisions[path]}')
        expected = [{'name': p, 'sha256': sha256((ROOT/'os/patches'/p).read_bytes())} for p in patches]
        if project['patches'] != expected:
            errors.append(f'{path}: patch list or bytes changed; regenerate with --write')
        files = touched(patches)
        if [r['path'] for r in project['files']] != list(files):
            errors.append(f'{path}: affected files differ from the patch headers')
            continue
        pre = {}
        for row in project['files']:
            if row['pre'] is None:
                if not files[row['path']]['new']:
                    errors.append(f'{path}/{row["path"]}: absent pre-image but the patch does not create it')
                pre[row['path']] = None
                continue
            fixture = fixture_path(path, row['path'])
            data = fixture.read_bytes() if fixture.exists() else None
            if data is None or sha256(data) != row['pre']['sha256'] or git_blob(data) != row['pre']['gitBlob']:
                errors.append(f'{path}/{row["path"]}: no committed fixture with the contract pre-image')
            pre[row['path']] = data
        if any(row['pre'] is not None and pre[row['path']] is None for row in project['files']):
            continue
        post, chain = apply_in_scratch(pre, patches)
        for row in project['files']:
            if sha256(post[row['path']]) != row['post']['sha256'] or git_blob(post[row['path']]) != row['post']['gitBlob']:
                errors.append(f'{path}/{row["path"]}: post-image differs from the contract')
            if row['chain'] != chain[row['path']]:
                errors.append(f'{path}/{row["path"]}: blob chain differs from the contract')
            for c in chain[row['path']]:
                bound[0 if c['base'] == 'exact' else 1] += c['base'] in ('exact', 'different')
            if ('integrationRewrite' in row) != (path == 'frameworks/base' and row['path'] == CONFIG):
                errors.append(f'{path}/{row["path"]}: only frameworks/base config.xml has an integration rewrite')
            elif 'integrationRewrite' in row and row['integrationRewrite']['elements'] != list(CONFIG_MARKERS):
                errors.append('config.xml: the rewritten elements are not exactly the five')
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
    print(f'{MANIFEST.relative_to(ROOT)} matches the committed bundle (sha256 {sha256(MANIFEST.read_bytes())}): {total} '
        f'files recomputed from pinned fixtures; {check.bound[0]} patch steps match the blobs their own index lines '
        f'record; {check.bound[1]} were authored on a different base and apply by context (recorded per file)')


if __name__ == '__main__':
    main()
