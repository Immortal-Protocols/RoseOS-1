#!/usr/bin/env python3
"""OS Product (#410): the input contract for a compile-only Launcher3QuickStepLib proof of RoseOS's Launcher3 changes.

Release owns the adapter that uses it; this file only states the inputs. The manifest,
os/compile-contracts/launcher3-quickstep-lib.json, names every affected file in the three projects that change what
Launcher3QuickStepLib compiles against (frameworks/base, packages/apps/Launcher3, build/release). For each it gives the
pinned pre-image (sha256 and git blob id, or absent for a new file), the post-image after the ordered patches and the
blob chain between them, and it lists the overlays copied into those projects. Its hashes are expected values derived
from pinned public sources and the committed patches; an adapter compares current bytes with them and never records
the current bytes' hashes as provenance.

The candidate is #440 (draft, unregistered, default-off). Its own inputs are inert copies in the fixtures; the base
is what a bundle with the contents in base.bundleContent produces (Launcher3 with the switcher patch only). A tree
integrated by today's patcher is therefore at base, not at the candidate's final images.

The adapter takes exactly one of these branches, and refuses anything else:
- verifyIntegrated: every affected file already equals its candidate post-image, every required overlay is present
  with its hash, and no patch is applied again. config.xml is the one file the patcher rewrites afterwards: it is
  accepted only by the integrationRewrite rule with its separately bound generating inputs. It refuses a tree at base.
- reconstruct: every affected file equals its pinned pre-image (and its pinned git blob), new files are absent, and
  the patches are applied in order to give exactly the post-images. It refuses a tree at base.
- verifyBaseApplyCandidate (a proposal stated as data; any run needs separate authority): the tree is exactly at base,
  then the candidate delta is applied in an isolated copy only, and the result must be exactly the candidate.
- Any other state (some files pre and some post, an unknown hash, a missing input or binding) is refused. Nothing is
  reset; no snapshot is trusted.

Usage:
  scripts/launcher3-compile-contract.py          offline: recompute every pre-image, post-image and chain from the
                                                 committed fixtures and patches, and compare with the manifest
  scripts/launcher3-compile-contract.py --write  regenerate the manifest: fetches the pinned pre-images and tree
                                                 listings read-only from GrapheneOS's public GitHub at the pinned SHAs
                                                 (no host, no credentials) into the fixtures, applies the patches in
                                                 scratch; keeps the recorded base of the candidate's overlays
"""
import argparse
import gzip
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
# The committed os/ bundle (its patches and overlays) the contract is read against.
OS = ROOT/'os'
PINNED = ROOT/'os/pinned-manifest.xml'
# Pinned pre-images, byte for byte, under their project paths.
FIXTURES = ROOT/'os/tests/fixtures/launcher3-compile-contract'
# The candidate (#440, draft): its only inputs not in the base bundle, as inert copies (never applied or copied by any
# build: they are outside os/patches and os/overlays), bound to the reviewed commit by their digests.
CANDIDATE = {'pr': 440, 'commit': 'e361d1cb15abdb80795569f1558fb7e0bcf5635d'}
BUNDLE = FIXTURES/'candidate-pr440'
CANDIDATE_PATCHES = ['rose-edge-launcher3.patch']
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
# The Soong goals this contract compiles, the projects whose sources they compile (no notAnInput overlay may sit in
# one), and which PROJECTS entry the candidate's patches belong to. A sibling contract
# (scripts/frameworks-base-wmshell-contract.py) sets its own values for these and for the constants above.
COMPILE_TARGETS = ['Launcher3QuickStepLib']
COMPILED_PROJECTS = ('packages/apps/Launcher3', 'build/release')
CANDIDATE_PROJECT = 1
# The reviewed overlays that are not inputs of Launcher3QuickStepLib, by exact target (M4): WindowManager-Shell and
# SystemUI sources and assets, off the library's classpath. Any other overlay outside REQUIRED_OVERLAYS is refused, so a
# new one can never become an unpinned input by its location alone.
NOT_AN_INPUT = (
    'frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell/AgentStatusControls.java',
    'frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell/AgentTrayLink.java',
    'frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell/AgentTrayPrototype.java',
    'frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell/StatusVoiceGlow.java',
    'frameworks/base/packages/SystemUI/assets/fonts/Nunito.ttf',
    'frameworks/base/packages/SystemUI/assets/licenses/Nunito-OFL.txt',
    'frameworks/base/packages/SystemUI/src/com/android/systemui/statusbar/phone/PixelStatusBarFilter.java',
)
# The two flags the adapter must show from the build's own decoded aconfig output. Each comes from one
# aconfig_declarations module; Soong writes aconfig-cache.pb and its decoded aconfig-flags.txt (aconfigTextRule:
# dump-cache --dedup --format='{fully_qualified_name}:{permission}={state:bool}') to that module's output directory.
# No RoseOS patch touches these definition files; they are pinned here, read from public sources.
FLAG_EVIDENCE = [
    {'flag': 'android.security.content_uri_permission_apis', 'expectedLine': 'android.security.content_uri_permission_apis:READ_ONLY=true',
     'module': 'android.security.flags-aconfig', 'package': 'android.security', 'container': 'system',
     'definedIn': ('frameworks/base', 'platform_frameworks_base', 'AconfigFlags.bp'),
     'declaredIn': ('frameworks/base', 'platform_frameworks_base', 'core/java/android/security/responsible_apis_flags.aconfig'),
     'expectedOutputDir': 'out/soong/.intermediates/frameworks/base/android.security.flags-aconfig',
     'readBy': 'android.security.flags-aconfig-java (framework), which ActivityThread and ActivityRecord read'},
    {'flag': 'com.android.launcher3.enable_fallback_overview_in_window',
     'expectedLine': 'com.android.launcher3.enable_fallback_overview_in_window:READ_ONLY=false',
     'module': 'com_android_launcher3_flags', 'package': 'com.android.launcher3', 'container': 'system_ext',
     'definedIn': ('packages/apps/Launcher3', 'platform_packages_apps_Launcher3', 'aconfig/Android.bp'),
     'declaredIn': ('packages/apps/Launcher3', 'platform_packages_apps_Launcher3', 'aconfig/launcher.aconfig'),
     'expectedOutputDir': 'out/soong/.intermediates/packages/apps/Launcher3/aconfig/com_android_launcher3_flags',
     'readBy': 'com_android_launcher3_flags_lib (Launcher3QuickStepLib), which OverviewComponentObserver reads'},
]
SOONG_RULES = [('build/soong', 'platform_build_soong', 'aconfig/init.go'),
    ('build/soong', 'platform_build_soong', 'aconfig/aconfig_declarations.go')]
EVIDENCE_PROJECTS = ('frameworks/base', 'packages/apps/Launcher3', 'build/soong')

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
    for path in [p for p, _, _ in PROJECTS] + ['build/soong']:
        m = re.search(r'<project name="[^"]+" path="%s" [^>]*revision="([0-9a-f]{40})"' % re.escape(path), xml)
        if not m:
            raise SystemExit(f'{path}: no pinned revision in {PINNED}')
        out[path] = m.group(1)
    return out


def file_blocks(patch):
    """[(path, new, index)] for every file a patch changes, from its `--- a/` / `+++ b/` pairs (some patches here use
    plain unified headers without `diff --git`), with the `index a..b` line of the same block when there is one."""
    lines = bundle_or_os('patches/' + patch).read_text().split('\n')
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


def overlay_targets():
    """{target: bytes} the candidate copies into the three projects: the committed os/overlays, with the candidate's
    bundle copies taking precedence and added where the base has none."""
    out = {}
    for base in (OS/'overlays', BUNDLE/'overlays'):
        for file in sorted(base.rglob('*')) if base.exists() else []:
            if file.is_file():
                target = str(file.relative_to(base))
                if any(target.startswith(p + '/') for p, _, _ in PROJECTS):
                    out[target] = file.read_bytes()
    return dict(sorted(out.items()))


def overlays():
    """Required overlays with their sha256; overlays that are not inputs of the library by target only, so their contents
    can change without touching the contract (adding or removing one does change it)."""
    rows = []
    for t, data in overlay_targets().items():
        if t.startswith(REQUIRED_OVERLAYS):
            rows.append({'target': t, 'sha256': sha256(data), 'role': 'required'})
        elif t in NOT_AN_INPUT:
            rows.append({'target': t, 'role': 'notAnInput'})
        else:
            raise ValueError(f'{t}: an overlay that is neither required nor a reviewed notAnInput target')
    return rows


def overlay_roles(projects, evidence):
    """Required and notAnInput overlays by exact target, and the proof that no notAnInput target is an input: each is
    outside Launcher3 and build/release, excluded from its own project's treePin by exact path, and neither a compile goal
    nor a flagEvidence definition, declaration or output."""
    rows = overlays()
    required = [o['target'] for o in rows if o['role'] == 'required']
    not_input = [o['target'] for o in rows if o['role'] == 'notAnInput']
    goals = list(COMPILE_TARGETS) + [row['expectedOutputDir'] + '/aconfig-flags.txt' for row in evidence]
    evidence_paths = {f'{row[k]["project"]}/{row[k]["path"]}' for row in evidence for k in ('definedIn', 'declaredIn')}
    pinned = {p['path']: set(p['treePin']['unlisted']['excludedPaths']) for p in projects}
    for target in not_input:
        project = next((p for p in pinned if target.startswith(p + '/')), None)
        if project in COMPILED_PROJECTS or project is None:
            raise ValueError(f'{target}: a notAnInput overlay inside a compiled or unpinned project')
        if target[len(project) + 1:] not in pinned[project]:
            raise ValueError(f'{target}: not excluded from {project} treePin by exact path')
        if target in evidence_paths or target in goals:
            raise ValueError(f'{target}: used by a compile goal or flagEvidence')
    return {
        'required': {'targets': required, 'rule': 'present with exactly its sha256 in overlays'},
        'notAnInput': {'targets': not_input, 'rule': 'exact target only: present with any bytes, or absent; excluded '
            'from its project\'s treePin by exact path (treePin.unlisted.excludedPaths); never a source of '
            f'{" or ".join(COMPILE_TARGETS)}, a compileGoals entry or a flagEvidence input. Adding or removing one changes the '
            'contract'},
        'compileGoals': goals,
    }


def apply_in_scratch(pre, patches):
    """({path: bytes or None}, {path: chain}) after applying the patches in order with git apply to
    {path: bytes or None}."""
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
            subprocess.run(['git', '-C', str(tree), 'apply', str(bundle_or_os('patches/' + patch))], check=True)
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
        # None for a path no patch so far creates (a candidate's new file, before the candidate is applied).
        return {path: (tree/path).read_bytes() if (tree/path).exists() else None for path in pre}, chain
    finally:
        shutil.rmtree(tree, True)


def fixture_path(project, path):
    return FIXTURES/project/path


def bundle_or_os(relative):
    """An os/ input (patches/... or overlays/...) as this contract sees it: the candidate's inert copy when the bundle has
    one, else the committed os/ file."""
    return BUNDLE/relative if (BUNDLE/relative).exists() else OS/relative


def bundle_files():
    return sorted(str(f.relative_to(BUNDLE)) for f in BUNDLE.rglob('*') if f.is_file())


def fetch(repo, revision, path):
    url = f'https://raw.githubusercontent.com/GrapheneOS/{repo}/{revision}/{path}'
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def evidence_fixture(project, path):
    return FIXTURES/'flag-evidence'/project/path


def flag_evidence(revisions, fetch_missing):
    files = {}
    for entry in FLAG_EVIDENCE:
        for project, repo, path in (entry['definedIn'], entry['declaredIn']):
            files[(project, repo, path)] = None
    for project, repo, path in SOONG_RULES:
        files[(project, repo, path)] = None
    out = {}
    for project, repo, path in files:
        target = evidence_fixture(project, path)
        if fetch_missing:
            data = fetch(repo, revisions[project], path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        out[(project, path)] = target.read_bytes() if target.exists() else None
    return out


def evidence_rows(revisions, data):
    rows = []
    for entry in FLAG_EVIDENCE:
        row = {k: v for k, v in entry.items() if k not in ('definedIn', 'declaredIn')}
        for key in ('definedIn', 'declaredIn'):
            project, _, path = entry[key]
            row[key] = {'project': project, 'revision': revisions[project], 'path': path,
                'sha256': sha256(data[(project, path)])}
        row['outputs'] = ['aconfig-cache.pb', 'aconfig-flags.txt']
        row['produce'] = ('build ' + entry['expectedOutputDir'] + '/aconfig-flags.txt as an explicit goal with '
            f'{" and ".join(COMPILE_TARGETS)}, or decode aconfig-cache.pb with that tree\'s host aconfig using the identical '
            "dump-cache --dedup --format='{fully_qualified_name}:{permission}={state:bool}'")
        row['accept'] = ('exactly one line for the flag, equal to expectedLine, in the module\'s own output; '
            'absent, duplicated, another module or another value: refuse (identity flag unresolved, result incomplete)')
        rows.append(row)
    rules = [{'project': p, 'revision': revisions[p], 'path': path, 'sha256': sha256(data[(p, path)])}
        for p, _, path in SOONG_RULES]
    return rows, rules


def check_evidence(manifest, revisions):
    errors = []
    data = flag_evidence(revisions, False)
    if any(v is None for v in data.values()):
        return ['flag evidence: a pinned definition fixture is missing; regenerate with --write']
    rows, rules = evidence_rows(revisions, data)
    if manifest.get('flagEvidence') != {'flags': rows, 'producingRules': rules}:
        errors.append('flagEvidence differs from the pinned definitions; regenerate with --write')
    for entry in FLAG_EVIDENCE:
        bp = data[(entry['definedIn'][0], entry['definedIn'][2])].decode()
        stanza = re.search(r'aconfig_declarations \{\s*name: "%s",(.*?)\n\}' % re.escape(entry['module']), bp, re.S)
        if not stanza or f'package: "{entry["package"]}"' not in stanza.group(1) or \
                f'container: "{entry["container"]}"' not in stanza.group(1):
            errors.append(f'{entry["module"]}: not declared with package {entry["package"]} and container {entry["container"]}')
        declaration = entry['declaredIn'][2]
        if stanza and declaration.split('/')[-1] not in stanza.group(1) and '**/*.aconfig' not in stanza.group(1):
            errors.append(f'{entry["module"]}: does not take {declaration}')
        decl = data[(entry['declaredIn'][0], declaration)].decode()
        name = entry['flag'].rsplit('.', 1)[1]
        if not re.search(r'^package: "%s"' % re.escape(entry['package']), decl, re.M) or \
                not re.search(r'flag \{\s*name: "%s"' % name, decl):
            errors.append(f'{entry["flag"]}: not declared in {declaration}')
    rule = data[('build/soong', 'aconfig/init.go')].decode()
    if "dump-cache --dedup --format='{fully_qualified_name}:{permission}={state:bool}'" not in rule:
        errors.append('build/soong aconfigTextRule format changed')
    if 'assembleFileName(config, "aconfig-flags.txt")' not in data[('build/soong', 'aconfig/aconfig_declarations.go')].decode():
        errors.append('build/soong aconfig_declarations no longer writes aconfig-flags.txt')
    return errors


def image(data):
    return None if data is None else {'sha256': sha256(data), 'gitBlob': git_blob(data)}


def integration_rewrite():
    """The config.xml rule as complete data, for an adapter that does not import this file."""
    return {
        'by': 'scripts/apply-os-changes.py, the `if policy:` block, after patching',
        'elements': list(CONFIG_MARKERS),
        'markers': dict(CONFIG_MARKERS),
        'generatingInputs': {
            'policyReceipt': 'agent, targets, prototypeControl; its sha256 must equal the one bound for that tree outside '
                'this contract',
            'referenceDisplayAddress': '--reference-display-address, absent unless given; decimal, 0 < value < 2^63',
            'lockedControl': '--locked-control, false unless given',
        },
        'inputRules': {'packageName': PACKAGE.pattern, 'certificate': CERT.pattern,
            'rows': 'every agent and target row has a packageName and at least one certificate'},
        'templates': {
            'array': '    <string-array name="config_pixelComputerControl{name}" translatable="false">\n{entries}'
                '    </string-array>',
            'entry': '        <item>{packageName}:{certificate}</item>\n',
            'arrays': {'config_pixelComputerControlAgents': 'Agents: the agent row',
                'config_pixelComputerControlTargets': 'Targets: every target row, in receipt order',
                'config_pixelComputerControlPrototypeAgents': 'PrototypeAgents: the agent row, only when prototypeControl '
                    'is true; otherwise the marker'},
            'config_pixelComputerControlReferenceDisplayAddress': '    <string name="config_pixelComputerControl'
                'ReferenceDisplayAddress" translatable="false">{address}</string> when given; otherwise the marker',
            'config_pixelComputerControlLockedSessions': '    <bool name="config_pixelComputerControlLockedSessions">true'
                '</bool> when lockedControl; otherwise the marker',
        },
        'compare': [
            'each element name opens exactly once in the file',
            'each element is exactly its expected text (template from the bound inputs, or its marker)',
            'replacing exactly those texts with their markers gives post (sha256); nothing else is normalised',
            'a missing input or binding, a value outside inputRules, or any other difference refuses',
        ],
        'reference': 'scripts/launcher3-compile-contract.py verify_integrated_config implements exactly this',
    }


TREES = FIXTURES/'trees'


def tree_fixture(project):
    return TREES/(project.replace('/', '__') + '.ls-tree.txt.gz')


def read_listing(project):
    """[(mode, type, blob, path)] of the pinned revision, from `git ls-tree -r --full-tree` (gzipped fixture)."""
    text = gzip.decompress(tree_fixture(project).read_bytes()).decode()
    out = []
    for line in text.splitlines():
        meta, path = line.split('\t', 1)
        mode, kind, blob = meta.split(' ')
        out.append((mode, kind, blob, path))
    return out


def git_tree_hash(listing):
    """The git root tree id that a full recursive listing of blobs describes (recomputed, so the listing is exactly
    the pinned commit's tree)."""
    root = {}
    for mode, kind, blob, path in listing:
        if kind != 'blob':
            raise ValueError(f'{path}: {kind} entries are not expected')
        node = root
        parts = path.split('/')
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = (mode, blob)

    def write(node):
        entries = []
        for name, value in node.items():
            if isinstance(value, dict):
                entries.append((name + '/', b'40000 ' + name.encode() + b'\0' + bytes.fromhex(write(value))))
            else:
                entries.append((name, value[0].encode() + b' ' + name.encode() + b'\0' + bytes.fromhex(value[1])))
        body = b''.join(e for _, e in sorted(entries, key=lambda e: e[0].encode()))
        return hashlib.sha1(b'tree %d\0' % len(body) + body).hexdigest()
    return write(root)


# The exact blob-hash invocation (M1): git's collision-detecting SHA-1, no attribute filters, no object write.
HASH_OBJECT = ('git', 'hash-object', '-t', 'blob', '--no-filters', '--stdin')


def tree_pin(project, revision, root_tree, excluded):
    """The machine-readable pin of every path in the pinned tree that the contract does not list itself."""
    listing = read_listing(project)
    raw = gzip.decompress(tree_fixture(project).read_bytes())
    lines = sorted((path.encode(), f'{mode} {blob}\t{path}\n'.encode()) for mode, _, blob, path in listing
        if path not in excluded)
    return {
        'revision': revision,
        'rootTree': root_tree,
        'source': 'revision is the pinned commit; rootTree is that commit\'s tree, recomputed offline from the listing '
            '(every tree object rebuilt and hashed); neither is a host fact',
        'objectFormat': 'sha1',
        'listing': {'path': str(tree_fixture(project).relative_to(ROOT)), 'sha256': sha256(raw),
            'compressedSha256': sha256(tree_fixture(project).read_bytes()), 'paths': len(listing),
            'format': '<mode> SP <type> SP <oid> TAB <path> LF, as git ls-tree -r --full-tree <revision> prints it, '
                'gzip-compressed with mtime 0'},
        'unlisted': {
            'excludedPaths': sorted(excluded),
            'excludedAre': 'exactly this project\'s files paths and overlay targets, relative to it; no globs. A path the '
                'pinned tree lacks (a new file or an overlay target) must be absent from it and excludes nothing',
            'count': len(lines),
            'recordFormat': '<mode> SP <oid> TAB <path> LF, with path relative to the project',
            'order': 'the raw bytes of path',
            'digest': sha256(b''.join(line for _, line in lines)),
            'digestOf': 'the concatenated records, sha256',
        },
        'modes': sorted({mode for mode, _, _, _ in listing}),
        'gitlinks': 'none: generation refuses any mode 160000 entry',
        'blobHash': {
            'argv': list(HASH_OBJECT),
            'regularFile': 'git hash-object -t blob --no-filters --stdin, given the file\'s bytes',
            'symlink': 'git hash-object -t blob --no-filters --stdin, given readlink\'s bytes (no trailing newline); '
                'mode 120000',
            'never': ['-w (no object writes)', '--literally', '--path (no attribute filters)', 'a plain SHA-1'],
            'implementation': 'git\'s own collision-detecting SHA-1, through that invocation; the offline generator\'s '
                'blob and tree ids are confirmed against git hash-object by os/tests/test_launcher3_compile_contract.py',
        },
        'extraPaths': 'any file present in the source project that is neither in this tree, a listed affected file nor '
            'an overlay target is refused',
        'limitation': 'per-path sha256 would need every file\'s contents, which the bounded pinned fixtures do not hold; '
            'the pin is mode and git blob id per path from the pinned commit\'s own tree objects, with this sha256 '
            'digest over them',
    }


def excluded(project):
    prefix = project['path'] + '/'
    return {r['path'] for r in project['files']} | {o['target'][len(prefix):] for o in overlays()
        if o['target'].startswith(prefix)}


def project_rows(path, patches, pre):
    post, chain = apply_in_scratch(pre, patches)
    rows = []
    for f in pre:
        row = {'path': f, 'pre': image(pre[f]), 'post': image(post[f]) if post[f] is not None or pre[f] is not None
            else None, 'chain': chain[f]}
        if path == 'frameworks/base' and f == CONFIG:
            row['integrationRewrite'] = integration_rewrite()
        rows.append(row)
    return rows


def base_and_delta(projects, recorded_overlay_before=None):
    """(base, candidateDelta). The base is what the patcher at a bundle whose source contents are bundleContent
    produces: every project as the candidate, except Launcher3 without the candidate's patches. The delta is the
    candidate's patch and overlay changes from that base; applying it to the base must give exactly the candidate's
    final images (checked here, offline, from the fixtures)."""
    base_projects = []
    for project, (path, repo, patches) in zip(projects, PROJECTS):
        base_patches = [p for p in patches if p not in CANDIDATE_PATCHES]
        pre = {r['path']: None if r['pre'] is None else fixture_path(path, r['path']).read_bytes() for r in project['files']}
        if base_patches == patches:
            rows = project['files']
        else:
            created = touched(base_patches)
            post, chain = apply_in_scratch({f: pre[f] for f in pre}, base_patches)
            rows = []
            for f in pre:
                exists = pre[f] is not None or f in created
                row = {'path': f, 'pre': image(pre[f]), 'post': image(post[f]) if exists else None, 'chain': chain[f]}
                rows.append(row)
        base_projects.append(dict(project, patches=[{'name': p, 'sha256': sha256(bundle_or_os('patches/' + p).read_bytes())}
            for p in base_patches], files=rows))
    path = PROJECTS[CANDIDATE_PROJECT][0]
    launcher_base = {r['path']: r for r in base_projects[CANDIDATE_PROJECT]['files']}
    # Base bytes after the base patches, then the candidate's patches on top.
    after_base, _ = apply_in_scratch({f: fixture_path(path, f).read_bytes() if launcher_base[f]['pre'] else None
        for f in launcher_base}, [p for p in PROJECTS[CANDIDATE_PROJECT][2] if p not in CANDIDATE_PATCHES])
    created = touched([p for p in PROJECTS[CANDIDATE_PROJECT][2] if p not in CANDIDATE_PATCHES])
    base_images = {f: after_base[f] if launcher_base[f]['pre'] or f in created else None for f in launcher_base}
    final, _ = apply_in_scratch(base_images, CANDIDATE_PATCHES)
    for row in projects[CANDIDATE_PROJECT]['files']:
        if image(final[row['path']]) != row['post']:
            raise ValueError(f'{row["path"]}: base plus the candidate delta is not the candidate post-image')
    bundle_overlays = {f[len('overlays/'):]: (BUNDLE/f).read_bytes() for f in bundle_files() if f.startswith('overlays/')}
    delta_overlays = []
    for target, data in sorted(bundle_overlays.items()):
        if recorded_overlay_before is None:
            committed = OS/'overlays'/target
            before = sha256(committed.read_bytes()) if committed.exists() else None
        else:
            before = recorded_overlay_before[target]
        delta_overlays.append({'target': target, 'bundlePath': str((BUNDLE/'overlays'/target).relative_to(ROOT)),
            'before': before, 'after': sha256(data)})
    before = {o['target']: o['before'] for o in delta_overlays}
    base_overlays = []
    for row in overlays():
        if row['target'] in before:
            if before[row['target']] is not None:
                base_overlays.append(dict(row, sha256=before[row['target']]))
        else:
            base_overlays.append(row)
    base = {
        'bundleContent': {
            'patches': [{'project': p['path'], 'name': x['name'], 'sha256': x['sha256']} for p in base_projects
                for x in p['patches']],
            'overlays': [{'target': o['target'], 'sha256': o['sha256']} for o in base_overlays if o['role'] == 'required'],
            'notAnInputOverlays': [o['target'] for o in base_overlays if o['role'] != 'required'],
        },
        'match': 'the integrating bundle\'s source contents (the patches it applied to these projects and the overlays '
            'it copied into them, as Infra reports them) must equal bundleContent exactly; any other bundle does not '
            'match and refuses',
        'projects': base_projects,
        'overlays': base_overlays,
    }
    delta = {
        'pr': CANDIDATE['pr'], 'commit': CANDIDATE['commit'],
        'patches': [{'name': p, 'sha256': sha256((BUNDLE/'patches'/p).read_bytes()), 'project': path, 'strip': 1,
            'bundlePath': str((BUNDLE/'patches'/p).relative_to(ROOT))} for p in CANDIDATE_PATCHES],
        'application': {
            'where': 'only in the isolated copy, never in the source tree',
            'steps': ['git -C <copy>/<project> apply --check -p<strip> <bundlePath>',
                'git -C <copy>/<project> apply -p<strip> <bundlePath>'],
            'forbidden': ['--3way', '--reject', '--recount', '--unidiff-zero', '-C<n> (reduced context)',
                '--ignore-whitespace', '--ignore-space-change', '--whitespace=fix'],
            'note': 'whitespace is left unchanged; any failure of --check refuses before anything is applied',
        },
        'files': [{'path': f'{path}/{f}', 'before': image(base_images[f]), 'after': image(final[f])}
            for f in touched(CANDIDATE_PATCHES)],
        'overlays': delta_overlays,
        'bundle': [{'path': str((BUNDLE/f).relative_to(ROOT)), 'sha256': sha256((BUNDLE/f).read_bytes())}
            for f in bundle_files()],
        'pathConvention': 'files[].path and overlays[].target are relative to the source tree root (project path '
            'first); projects[].files[].path and base.projects[].files[].path are relative to their project; '
            'bundlePath and bundle[].path are relative to the repository root',
    }
    return base, delta


def text_listing(records, what):
    """The committed listing format from NUL-terminated `git ls-tree -r -z` records: one LF-terminated line per record.
    Refuses a gitlink (mode 160000) and any path git would C-quote in a text listing (control or non-ASCII bytes, '"' or
    '\\'), so every path in the fixture is its raw bytes."""
    if not records.endswith(b'\0'):
        raise SystemExit(f'{what}: not a NUL-terminated listing')
    out = []
    for record in records[:-1].split(b'\0'):
        meta, _, path = record.partition(b'\t')
        if meta.startswith(b'160000 '):
            raise SystemExit(f'{what}: a submodule (gitlink) entry; the pin does not support one')
        if not path or any(c < 0x20 or c > 0x7e or c in b'"\\' for c in path):
            raise SystemExit(f'{what}: a path git would quote; the pin supports plain paths only')
        out.append(record + b'\n')
    return b''.join(out)


def fetch_tree(repo, revision):
    """(root tree id, gzipped `git ls-tree -r --full-tree` listing) of a pinned commit, from a blobless, depth-1 fetch
    of GrapheneOS's public repository (tree objects only, no file contents)."""
    work = Path(tempfile.mkdtemp(prefix='l3-tree-'))
    try:
        subprocess.run(['git', 'init', '-q', str(work)], check=True)
        subprocess.run(['git', '-C', str(work), 'fetch', '-q', '--depth', '1', '--filter=blob:none',
            f'https://github.com/GrapheneOS/{repo}', revision], check=True)
        root = subprocess.check_output(['git', '-C', str(work), 'rev-parse', revision + '^{tree}'], text=True).strip()
        listing = text_listing(subprocess.check_output(['git', '-C', str(work), 'ls-tree', '-r', '-z', '--full-tree',
            revision]), f'{repo} {revision}')
        return root, gzip.compress(listing, 9, mtime=0)
    finally:
        shutil.rmtree(work, True)


def build():
    revisions = pins()
    projects = []
    roots = {}
    for path, repo, patches in PROJECTS:
        files = touched(patches)
        pre = {}
        for f, meta in files.items():
            if meta['new']:
                pre[f] = None
                continue
            pre[f] = fetch(repo, revisions[path], f)
            target = fixture_path(path, f)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(pre[f])
        roots[path], listing = fetch_tree(repo, revisions[path])
        tree_fixture(path).parent.mkdir(parents=True, exist_ok=True)
        tree_fixture(path).write_bytes(listing)
        projects.append({'path': path, 'repo': f'GrapheneOS/{repo}', 'revision': revisions[path],
            'patches': [{'name': p, 'sha256': sha256(bundle_or_os('patches/' + p).read_bytes())} for p in patches],
            'files': project_rows(path, patches, pre)})
    for project in projects:
        project['treePin'] = tree_pin(project['path'], project['revision'], roots[project['path']], excluded(project))
    recorded = None
    if MANIFEST.exists():
        recorded = {o['target']: o['before'] for o in json.loads(MANIFEST.read_text()).get('candidateDelta', {})
            .get('overlays', [])} or None
    if recorded is None:
        for f in bundle_files():
            committed = OS/f
            if f.startswith('overlays/') and committed.exists() and committed.read_bytes() == (BUNDLE/f).read_bytes():
                raise SystemExit(f'os/{f} already holds the candidate bytes: its base is unknown; record it first')
    base, delta = base_and_delta(projects, recorded)
    write_base_overlay_copies(delta)
    evidence, rules = evidence_rows(revisions, flag_evidence(revisions, True))
    manifest = describe()
    manifest.update({'projects': projects, 'overlays': overlays(), 'base': base, 'candidateDelta': delta,
        'flagEvidence': {'flags': evidence, 'producingRules': rules}, 'overlayRoles': overlay_roles(projects, evidence)})
    return manifest


def describe():
    """The contract's stated (not derived) fields, in manifest order; None marks a field build() derives. A sibling
    contract replaces this function."""
    return {
        'schemaVersion': 2,
        'kind': 'rose-launcher3-compile-inputs',
        'owner': 'OS Product (#410). Release owns the adapter; this states inputs only.',
        'target': 'Launcher3QuickStepLib',
        'proves': 'the patched handlers and overlays type-check and compile on the real classpath, plus the '
            'generated aconfig values. Not R8, manifest merge, APK resources, packaging, signing or runtime.',
        'provenance': 'every hash here is an expected value derived from pinned public sources and the committed '
            'patches; an adapter compares current bytes with them and never records current bytes as provenance',
        'candidatePatches': 'frameworks/base and build/release in apply-os-changes.py --background-policy order; '
            'Launcher3 adds rose-edge-launcher3.patch (candidateDelta), which apply-os-changes.py does not register, so '
            'a tree integrated by any patcher today is not at these final images',
        'branches': {
            'verifyIntegrated': 'every affected file equals post (config.xml only through integrationRewrite with its '
                'bound inputs), every required overlay is present with its sha256 (notAnInput overlays may be present '
                'with any bytes), every unlisted path matches its treePin, no extra path, no patch is applied again',
            'reconstruct': 'every affected file equals pre (sha256 and gitBlob at revision), new files and every '
                'overlay target are absent beforehand, patches are applied in order and must give exactly post; '
                'required overlays are then copied',
            'verifyBaseApplyCandidate': {
                'status': 'proposal, stated as data only: Release implements it, and any run needs separate authority',
                'conditions': [
                    {'id': 'base-exact', 'text': 'the integrating bundle matches base.bundleContent; every affected file '
                        'of every project equals base.projects post (config.xml only through integrationRewrite with its '
                        'bound inputs); every required base overlay is present with its sha256 (notAnInput overlays may be '
                        'present with any bytes); every unlisted path matches the project treePin; no extra path'},
                    {'id': 'delta-preimages', 'text': 'every candidateDelta file and overlay is at its before image, or '
                        'absent where before is null; anything at after, or neither, refuses'},
                    {'id': 'isolated-apply', 'text': 'in an isolated copy only, each candidateDelta patch (sha256-checked '
                        'bytes at bundlePath) passes candidateDelta.application, and each candidateDelta overlay is copied '
                        'from its sha256-checked bundlePath'},
                    {'id': 'final-exact', 'text': 'the copy then equals projects and overlays exactly, with the same '
                        'treePin and config.xml rules; only then does the compile proceed'},
                ],
                'otherwise': 'refuse at the first failed condition, naming its id; partial, already applied or unknown '
                    'state refuses',
            },
            'otherwise': 'refuse: mixed pre/post, unknown hash, missing file, overlay, input or binding. A tree '
                'integrated by the current patcher (Launcher3 switcher only) is such a state and must not be completed '
                'by applying rose-edge-launcher3.patch alone. Never reset or trust a snapshot.',
        },
        'projects': None,
        'overlays': None,
        'unaffected': 'inside the three projects, every path not listed in files or overlays must match that '
            'project\'s treePin, and any extra file is refused; every other project the library or lunch needs must be '
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
            'flagEvidence: each flag\'s expectedLine from its own aconfig_declarations module output',
            'lunch product, variant, BUILD_ID and release-config maps in effect',
        ],
        'base': None,
        'candidateDelta': None,
        'flagEvidence': None,
        'overlayRoles': None,
        'identitySharing': {
            'sites': ['quickstep/src/com/android/quickstep/FallbackSwipeHandler.java startHomeIntent',
                'quickstep/src/com/android/quickstep/window/RecentsWindowSwipeHandler.java (inactive here)'],
            'change': 'ActivityOptions.makeCustomAnimation(mContext, 0, 0).setShareIdentityEnabled(true)',
            'appliesWithEdgeOff': True,
            'consumer': "Home (workspace/rose-agent, #442): MainActivity.gestureContract replies to "
                "gesture_nav_contract_v1 only after GestureContract.caller accepts getCurrentCaller() as the "
                "config_recentsComponentName package with a shared uid, on SDK 35+, RectF only; no dedicated exported "
                "entry",
            'consumerImplemented': True,
        },
    }


def check(manifest):
    """Recompute everything from the committed fixtures and patches and compare with the manifest. A malformed
    manifest is reported as a failure, never a traceback."""
    try:
        return check_all(manifest)
    except (KeyError, ValueError, TypeError, AttributeError, IndexError) as error:
        check.bound = (0, 0)
        return [f'malformed manifest or refused input: {type(error).__name__}: {error}']


def check_all(manifest):
    errors = []
    bound = [0, 0]
    revisions = pins()
    if [p['path'] for p in manifest['projects']] != [p for p, _, _ in PROJECTS]:
        return ['projects differ from PROJECTS']
    for project, (path, repo, patches) in zip(manifest['projects'], PROJECTS):
        if project['revision'] != revisions[path]:
            errors.append(f'{path}: revision {project["revision"]} is not the pinned {revisions[path]}')
        expected = [{'name': p, 'sha256': sha256(bundle_or_os('patches/' + p).read_bytes())} for p in patches]
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
            elif 'integrationRewrite' in row and row['integrationRewrite'] != integration_rewrite():
                errors.append('config.xml: the integration rewrite rule differs from the patcher\'s five elements')
        errors += check_tree_pin(project)
    if manifest['overlays'] != overlays():
        errors.append('overlays changed; regenerate with --write')
    try:
        if manifest.get('overlayRoles') != overlay_roles(manifest['projects'], manifest['flagEvidence']['flags']):
            errors.append('overlayRoles differ from the overlays, treePins and flagEvidence; regenerate with --write')
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        errors.append(f'overlayRoles: {error}')
    errors += check_evidence(manifest, revisions)
    if not errors:
        errors += check_candidate(manifest)
    check.bound = tuple(bound)
    return errors


def check_tree_pin(project):
    path = project['path']
    pin = project.get('treePin') or {}
    if not tree_fixture(path).exists():
        return [f'{path}: no pinned tree listing fixture']
    listing = read_listing(path)
    errors = [f'{path}/{p}: a gitlink (mode 160000), which the pin does not support' if m == '160000' else
        f'{path}/{p}: a {k} entry, which a recursive blob listing never has' if k != 'blob' else
        f'{path}/{p}: a quoted path, which the pin does not support' for m, k, _, p in listing
        if m == '160000' or k != 'blob' or p.startswith('"')]
    if errors:
        return errors
    if git_tree_hash(listing) != pin.get('rootTree'):
        errors.append(f'{path}: the listing is not the pinned root tree {pin.get("rootTree")}')
    if pin != tree_pin(path, project['revision'], pin.get('rootTree'), excluded(project)):
        errors.append(f'{path}: treePin differs from the pinned listing; regenerate with --write')
    entries = {p: blob for _, _, blob, p in listing}
    for row in project['files']:
        if row['pre'] is None:
            if row['path'] in entries:
                errors.append(f'{path}/{row["path"]}: a new file that the pinned tree already has')
        elif entries.get(row['path']) != row['pre']['gitBlob']:
            errors.append(f'{path}/{row["path"]}: pre-image is not the pinned tree\'s blob')
    prefix = path + '/'
    for o in overlays():
        if o['target'].startswith(prefix) and o['target'][len(prefix):] in entries:
            errors.append(f'{o["target"]}: an overlay target the pinned tree already has')
    return errors


def base_overlay_copy(target):
    """The pinned copy of a candidate overlay's base image, in the same layout as the pre-image fixtures. It lets a
    consumer reach the base when the committed os/ is at the candidate (#440's branch), where os/overlays holds the
    candidate bytes instead."""
    return FIXTURES/target


def write_base_overlay_copies(delta):
    """--write: refresh each base copy from os/overlays while those are at the base image."""
    for o in delta['overlays']:
        committed = OS/'overlays'/o['target']
        if o['before'] is not None and committed.exists() and sha256(committed.read_bytes()) == o['before']:
            copy = base_overlay_copy(o['target'])
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(committed.read_bytes())


def check_candidate(manifest):
    errors = []
    delta = manifest.get('candidateDelta') or {}
    if (delta.get('pr'), delta.get('commit')) != (CANDIDATE['pr'], CANDIDATE['commit']):
        return ['candidateDelta does not bind the reviewed candidate commit']
    recorded = {o['target']: o['before'] for o in delta.get('overlays', [])}
    try:
        base, expected = base_and_delta(manifest['projects'], recorded)
    except (KeyError, ValueError) as error:
        return [f'candidate: {error}']
    if manifest.get('base') != base or delta != expected:
        errors.append('base or candidateDelta differs from what the fixtures and bundle give; regenerate with --write')
    for o in expected['overlays']:
        copy = base_overlay_copy(o['target'])
        if o['before'] is not None and (not copy.is_file() or sha256(copy.read_bytes()) != o['before']):
            errors.append(f'os/tests/fixtures/launcher3-compile-contract/{o["target"]}: no pinned copy of the base '
                f'overlay image {o["before"]}')
    # The committed os/ is wholly at the base (this bundle) or wholly at the candidate (#440's branch), never a mix.
    states = set()
    for o in expected['overlays']:
        committed = OS/'overlays'/o['target']
        now = sha256(committed.read_bytes()) if committed.exists() else None
        if now == o['before']:
            states.add('base')
        elif now == o['after']:
            states.add('candidate')
        else:
            errors.append(f'os/overlays/{o["target"]}: neither the base nor the candidate bytes')
    for p in CANDIDATE_PATCHES:
        committed = OS/'patches'/p
        if committed.exists():
            states.add('candidate')
            if committed.read_bytes() != (BUNDLE/'patches'/p).read_bytes():
                errors.append(f'os/patches/{p}: differs from the candidate bundle')
        else:
            states.add('base')
    if len(states) > 1:
        errors.append('os/ mixes base and candidate inputs: it must be wholly one or the other')
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
