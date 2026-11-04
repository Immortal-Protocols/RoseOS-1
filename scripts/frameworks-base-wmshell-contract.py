#!/usr/bin/env python3
"""OS Product (#410): the input contract for a compile-only proof of OS Core's raised-edge Shell patch.

A sibling of scripts/launcher3-compile-contract.py, with the same machinery, branches and provenance rules; only the
inputs differ. The manifest, os/compile-contracts/frameworks-base-wmshell.json, names every affected file in the two
projects WindowManager-Shell and SystemUI-core compile from (frameworks/base and build/release). For each file it gives:
- the pinned pre-image;
- the post-image after the ordered patches, with the blob chain between them;
- the overlays copied into those projects. Every frameworks/base overlay is a required input here: the patch's
  RoseEdgeMicSlot calls the tray's AgentTrayLink.

The candidate is OS Core's os/patches/rose-edge-wmshell.patch: #516 and #523, merged at 01cf401 and unregistered.

Ownership:
- OS Core owns the patch and confirms the inputs and goals (the `confirmation` block). Until it confirms, the contract
  is pending and an adapter must refuse it.
- Release owns the adapter.
- Registration in apply-os-changes.py waits for a real compile of this contract, and this file never registers anything.

The Launcher3 contract stays separate. Launcher3QuickStepLib has no compile coupling to this patch: its client is
hand-written against the interface descriptor. A failure here therefore neither masks nor blocks that result.

Usage:
  scripts/frameworks-base-wmshell-contract.py          offline: recompute everything from the committed fixtures and
                                                       patches, and compare it with the manifest
  scripts/frameworks-base-wmshell-contract.py --write  regenerate, fetching pinned public sources the same way
                                                       launcher3-compile-contract.py --write does
"""
import argparse
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_base():
    spec = importlib.util.spec_from_file_location('launcher3_compile_contract',
        ROOT/'scripts/launcher3-compile-contract.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load_base()
L3 = {name: getattr(C, name) for name in ('PROJECTS', 'describe')}

C.MANIFEST = ROOT/'os/compile-contracts/frameworks-base-wmshell.json'
# The reviewed candidate: the merge on main holding the patch's final bytes (#516 then #523).
C.CANDIDATE = {'pr': 523, 'commit': '01cf401444392d64e18a520347180f7608c1137b'}
C.BUNDLE = C.FIXTURES/'candidate-pr523'
C.CANDIDATE_PATCHES = ['rose-edge-wmshell.patch']
# frameworks/base: apply-os-changes.py's --background-policy order (as in the Launcher3 contract), then the candidate.
# build/release: unchanged, because framework's classpath needs its aconfig declarations.
_FRAMEWORKS, _, _BUILD_RELEASE = L3['PROJECTS']
C.PROJECTS = [
    (_FRAMEWORKS[0], _FRAMEWORKS[1], _FRAMEWORKS[2] + C.CANDIDATE_PATCHES),
    _BUILD_RELEASE,
]
C.CANDIDATE_PROJECT = 0
C.REQUIRED_OVERLAYS = ('frameworks/base/', 'build/release/')
C.NOT_AN_INPUT = ()
C.COMPILE_TARGETS = ['WindowManager-Shell', 'SystemUI-core']
C.COMPILED_PROJECTS = ('frameworks/base', 'build/release')
# No flag decides what these goals compile: persist.wm.debug.rose_edge is read at runtime only. OS Core confirms this.
C.FLAG_EVIDENCE = []

CONFIRMATION = {
    'by': 'OS Core (owner of rose-edge-wmshell.patch, #516/#523)',
    'state': 'confirmed',
    'record': 'OS Core to OS Product, 2026-11-04 06:28 UTC, computed from source against main 1df12a7 (the patch bytes '
        'bound here). It confirmed every field below and listed all six after-images, which equal candidateDelta. It '
        'corrected one point: the required inputs are wider than the tray link, namely all four Shell overlays (Shell '
        'compiles src/**), frameworks-base.patch, and the SystemUI overlays for SystemUI-core. These are all required '
        'here. The self-update and background-control patches do not decide these goals; they stay bound because the '
        'base bundle applies them',
    'fields': [
        'candidateDelta: the patch bytes and its after images',
        'projects frameworks/base: WMShellModule.java pre is the pinned blob (no other patch touches it)',
        'overlayRoles.required: every frameworks/base overlay, AgentTrayLink among them',
        'overlayRoles.compileGoals: WindowManager-Shell and SystemUI-core',
        'flagEvidence: none',
    ],
    'rule': 'an adapter refuses this contract unless state is "confirmed"; any change to the bound patch or inputs '
        'regenerates the manifest and needs OS Core\'s confirmation again',
}

# Where each goal's sources come from, read from the pinned Android.bp files (committed byte for byte under
# build-graph/, each checked against the pinned tree listing). Release's report proves every post-image and required
# overlay under a goal directory against the module that compiles it. That module is not always the goal: Shell's
# .java reach WindowManager-Shell only through the protolog genrule, and its .aidl through WindowManager-Shell-aidls.
GOAL_DIRS = {'WindowManager-Shell': 'frameworks/base/libs/WindowManager/Shell',
    'SystemUI-core': 'frameworks/base/packages/SystemUI'}
BUILD_GRAPH = C.FIXTURES/'build-graph'
SHELL_BP, SYSUI_BP = 'libs/WindowManager/Shell/Android.bp', 'packages/SystemUI/Android.bp'
# (Android.bp, module, module type, text its stanza must contain, text it must not contain).
STANZAS = [
    (SHELL_BP, 'wm_shell-sources', 'filegroup', ['"src/**/*.java"'], []),
    (SHELL_BP, 'wm_shell-sources-kt', 'filegroup', ['"src/**/*.kt"'], []),
    (SHELL_BP, 'wm_shell_protolog_src', 'java_genrule', ['":wm_shell-sources"', 'transform-protolog-calls'], []),
    (SHELL_BP, 'WindowManager-Shell-aidls', 'java_library', ['"src/**/*.aidl"'], []),
    (SHELL_BP, 'WindowManager-Shell-defaults', 'java_defaults', ['"WindowManager-Shell-aidls"'], []),
    (SHELL_BP, 'WindowManager-Shell', 'android_library',
        ['"WindowManager-Shell-defaults"', '":wm_shell_protolog_src"', '":wm_shell-sources-kt"'], []),
    (SYSUI_BP, 'SystemUI-core', 'android_library', ['"src/**/*.kt"', '"src/**/*.java"', '"WindowManager-Shell"'],
        ['manifest:', 'asset_dirs:']),
]
# (goal, pattern relative to the goal directory, module whose lists hold the file, which list, how it reaches the goal).
SOURCE_RULES = [
    ('WindowManager-Shell', 'src/**/*.java', 'wm_shell_protolog_src', 'genrule inputs',
        'filegroup wm_shell-sources; protologtool transforms it and WindowManager-Shell compiles the resulting srcjar'),
    ('WindowManager-Shell', 'src/**/*.kt', 'WindowManager-Shell', 'kotlinc sources', 'filegroup wm_shell-sources-kt'),
    ('WindowManager-Shell', 'src/**/*.aidl', 'WindowManager-Shell-aidls', 'aidl sources',
        'a static_libs entry of WindowManager-Shell-defaults'),
    ('SystemUI-core', 'src/**/*.java', 'SystemUI-core', 'javac sources', 'srcs'),
    ('SystemUI-core', 'src/**/*.kt', 'SystemUI-core', 'kotlinc sources', 'srcs'),
    ('SystemUI-core', 'AndroidManifest.xml', 'SystemUI-core', 'manifest',
        'Soong\'s android_library default manifest (SystemUI-core sets no manifest)'),
    ('SystemUI-core', 'assets/**', 'SystemUI-core', 'assets',
        'Soong\'s android_library default asset directory (SystemUI-core sets no asset_dirs)'),
]


def glob_regex(pattern):
    out = ''
    for part in re.split(r'(\*\*/|\*\*|\*)', pattern):
        out += {'**/': '(?:.*/)?', '**': '.*', '*': '[^/]*'}.get(part, re.escape(part))
    return re.compile(out + '$')


def stanza(text, name):
    """(module type, block text) of the top-level module named name in an Android.bp."""
    m = re.search(r'^(\w+) \{\n(?:(?!^\}).*\n)*?    name: "%s",\n(?:(?!^\}).*\n)*?^\}' % re.escape(name), text, re.M)
    if not m:
        raise ValueError(f'{name}: no such module')
    return m.group(1), m.group(0)


def build_graph():
    """The pinned Android.bp files, and every stanza fact the source rules rest on."""
    listing = {p: blob for _, _, blob, p in C.read_listing('frameworks/base')}
    files = []
    for bp in (SHELL_BP, SYSUI_BP):
        data = (BUILD_GRAPH/'frameworks/base'/bp).read_bytes()
        if C.git_blob(data) != listing.get(bp):
            raise ValueError(f'frameworks/base/{bp}: the build-graph fixture is not the pinned tree\'s blob')
        files.append({'path': f'frameworks/base/{bp}', 'gitBlob': C.git_blob(data), 'sha256': C.sha256(data),
            'fixture': str((BUILD_GRAPH/'frameworks/base'/bp).relative_to(ROOT))})
    for bp, name, kind, needs, forbids in STANZAS:
        found, block = stanza((BUILD_GRAPH/'frameworks/base'/bp).read_text(), name)
        if found != kind or any(n not in block for n in needs) or any(f in block for f in forbids):
            raise ValueError(f'{bp} {name}: not a {kind} with {needs} and without {forbids}')
    return files


def source_proof(projects, overlays):
    """Each post-image and required overlay under a goal directory, with the module whose lists must hold it; files
    under no goal directory are listed as not source-proved. A file under a goal directory that no rule places refuses."""
    paths = sorted({f'{p["path"]}/{r["path"]}' for p in projects for r in p['files'] if r['post'] is not None}
        | {o['target'] for o in overlays if o['role'] == 'required'})
    rows, outside = [], []
    for path in paths:
        goal = next((g for g, d in GOAL_DIRS.items() if path.startswith(d + '/')), None)
        if goal is None:
            outside.append(path)
            continue
        relative = path[len(GOAL_DIRS[goal]) + 1:]
        rule = next((r for r in SOURCE_RULES if r[0] == goal and glob_regex(r[1]).match(relative)), None)
        if rule is None:
            raise ValueError(f'{path}: under {goal}\'s directory but placed by no source rule')
        rows.append({'file': path, 'goal': goal, 'module': rule[2], 'list': rule[3], 'via': rule[4]})
    return {'files': rows, 'notSourceProved': outside,
        'rule': 'each file must appear in its module\'s own Soong input list for that kind; absent, or present only '
            'in another module\'s, refuses. notSourceProved files sit under no goal directory and are proved by '
            'their tree images only'}


BASE_OVERLAY_ROLES = C.overlay_roles


def overlay_roles(projects, evidence):
    roles = BASE_OVERLAY_ROLES(projects, evidence)
    roles['goalDirs'] = dict(GOAL_DIRS)
    roles['buildGraph'] = build_graph()
    roles['sourceProof'] = source_proof(projects, C.overlays())
    return roles


C.overlay_roles = overlay_roles


def describe():
    """The stated fields, in the Launcher3 contract's order, with this contract's inputs and goals."""
    base = L3['describe']()
    branches = dict(base['branches'])
    branches['otherwise'] = ('refuse: mixed pre/post, unknown hash, missing file, overlay, input or binding. A tree '
        'integrated by the current patcher (no rose-edge-wmshell.patch) is such a state at the candidate\'s files and '
        'must not be completed by applying rose-edge-wmshell.patch alone outside verifyBaseApplyCandidate. Never reset '
        'or trust a snapshot.')
    return {
        'schemaVersion': base['schemaVersion'],
        'kind': 'rose-frameworks-base-wmshell-compile-inputs',
        'owner': 'OS Product (#410) states the inputs; OS Core owns the patch and confirms them; Release owns the '
            'adapter.',
        'targets': list(C.COMPILE_TARGETS),
        'confirmation': CONFIRMATION,
        'registration': {
            'state': 'unregistered',
            'rule': 'rose-edge-wmshell.patch enters apply-os-changes.py only after this contract compiles on a real '
                'tree (agreed on #410); this contract never registers it',
        },
        'proves': 'the Shell patch\'s controller, policy, mic-slot window and AIDL, the tray overlays it calls, and '
            'WMShellModule\'s bindings type-check and compile on the real classpath (WindowManager-Shell), and '
            'SystemUI\'s dagger graph accepts them (SystemUI-core). Not R8, APK packaging, signing or runtime.',
        'provenance': base['provenance'],
        'candidatePatches': 'frameworks/base in apply-os-changes.py --background-policy order, then '
            'rose-edge-wmshell.patch (candidateDelta), which apply-os-changes.py does not register; build/release as '
            'the background policy applies it. A tree integrated by any patcher today is not at these final images',
        'branches': branches,
        'projects': None,
        'overlays': None,
        'unaffected': 'inside the two projects, every path not listed in files or overlays must match that '
            'project\'s treePin, and any extra file is refused; every other project the goals or lunch need must be '
            'byte-identical to its pinned revision (Infra provenance, not this contract)',
        'notInputs': ['packages/apps/Launcher3 (Launcher3QuickStepLib is the sibling contract; no compile coupling)',
            'os/staged/rose-edge-resize/aidl (a record; the compiled AIDL is the patch\'s own)',
            'packages/apps/PixelBackgroundControl and Agent.apk', 'the policy receipt in reconstruct (only '
            'verifyIntegrated needs it, bound, to check config.xml)', 'build/make patches', 'packages/apps/Updater',
            'packages/modules/Permission', 'preinstall APKs'],
        'outputsToBind': [
            'Soong source lists of WindowManager-Shell and WindowManager-Shell-aidls contain every frameworks/base '
                'post-image and required Shell overlay at these hashes',
            'm WindowManager-Shell succeeds',
            'm SystemUI-core succeeds (its dagger graph includes WMShellModule\'s RoseEdgeController binding)',
            'lunch product, variant, BUILD_ID and release-config maps in effect',
        ],
        'base': None,
        'candidateDelta': None,
        'flagEvidence': None,
        'overlayRoles': None,
    }


C.describe = describe


def check(manifest):
    errors = C.check(manifest)
    if manifest.get('confirmation') != CONFIRMATION:
        errors.append('confirmation differs from the recorded OS Core confirmation')
    if manifest.get('registration', {}).get('state') != 'unregistered':
        errors.append('registration: this contract states only an unregistered candidate')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--write', action='store_true', help='regenerate from pinned public sources')
    a = parser.parse_args()
    if a.write:
        C.MANIFEST.write_text(json.dumps(C.build(), indent=1) + '\n')
    manifest = json.loads(C.MANIFEST.read_text())
    errors = check(manifest)
    for error in errors:
        print('FAIL:', error)
    if errors:
        raise SystemExit(1)
    total = sum(len(p['files']) for p in manifest['projects'])
    print(f'{C.MANIFEST.relative_to(ROOT)} matches the committed bundle (sha256 {C.sha256(C.MANIFEST.read_bytes())}): '
        f'{total} files recomputed from pinned fixtures; confirmation {manifest["confirmation"]["state"]}')


if __name__ == '__main__':
    main()
