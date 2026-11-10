#!/usr/bin/env python3
"""OS Product (#410): the input contract for a compile-only proof of OS Core's raised-edge Shell patch.

A sibling of scripts/launcher3-compile-contract.py, with the same machinery, branches and provenance rules; only the
inputs differ. The manifest, os/compile-contracts/frameworks-base-wmshell.json, names every affected file in the two
projects WindowManager-Shell and SystemUI-core compile from (frameworks/base and build/release). For each file it gives:
- the pinned pre-image;
- the post-image after the ordered patches, with the blob chain between them;
- the overlays copied into those projects. Every frameworks/base overlay is a required input here: the patch's
  RoseEdgeMicSlot calls the tray's AgentTrayLink.

The candidate is OS Core's os/patches/rose-edge-wmshell.patch: #516, #523 and #528, #540's javac fix (d8f19fe) and
#551's chin marker (77c9117, head b639c22).

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
L3 = {name: getattr(C, name) for name in ('PROJECTS', 'describe_base')}
L3['describe'] = L3['describe_base']
# This contract stays at schema 2 (its stated fields and base) until its own re-pin with Release.
C.SCHEMA = 2

C.MANIFEST = ROOT/'os/compile-contracts/frameworks-base-wmshell.json'
# The reviewed candidate: the commit holding the patch's current bytes (#516, #523, #528, #540's fix for the first
# real compile's javac error, then #551's chin marker on REGISTER for Rose Agent, F08).
C.CANDIDATE = {'pr': 734, 'commit': 'f0dcd60f047c856214ef7b1ddbef1c20c4d56c6b'}
C.BUNDLE = C.FIXTURES/'candidate-pr734'
C.CANDIDATE_PATCHES = ['rose-edge-wmshell.patch']
# frameworks/base: apply-os-changes.py's --background-policy order (as in the Launcher3 contract), then the candidate.
# build/release: unchanged, because framework's classpath needs its aconfig declarations.
_FRAMEWORKS, _, _BUILD_RELEASE = L3['PROJECTS']
# Exclude by name so a later frameworks/base patch in the inherited list still flows in.
# rose-edge-wmshell.patch is this contract's own candidate (C.CANDIDATE_PATCHES, appended below).
# rose-shade-systemui.patch is not modeled here: see CONFIRMATION['shaded'], which records that the
# shade include stays on the normal build and this contract does not model it.
C.PROJECTS = [
    (_FRAMEWORKS[0], _FRAMEWORKS[1],
     [x for x in _FRAMEWORKS[2] if x not in ('rose-edge-wmshell.patch', 'rose-shade-systemui.patch')] + C.CANDIDATE_PATCHES),
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
    'by': 'OS Core (owner of rose-edge-wmshell.patch, #516/#523/#528/#540/#551)',
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
    'reconfirmed': 'OS Core to OS Product, 2026-11-04 06:51 UTC, from merge bf6a9ed (#528, tested head e304a35): patch '
        'sha256 ba64ea3114f46925446c204261097aebf963a923eb355860a6f2e54bb7262be2. Only RoseEdgeMicSlot.java\'s after-image '
        'changes (blob a9c4a5dd62f652dcf1044b6b9774ed851083f989); the other after-images, the WMShellModule pre-image, '
        'the inputs and the goals are unchanged',
    'rebound': 'OS Core to OS Product, 2026-11-04 16:41 UTC, from #540 head d8f19fe (the javac fix for Frankel run '
        '41861964515: RoseEdgeMicSlot.Control\'s super(mContext) named View\'s own field): patch sha256 '
        'a10a73b983430d26d432d90c8555ab052dd6917142dfad1d5d2c2d9f0efe7f75. Only RoseEdgeMicSlot.java\'s after-image '
        'changes (sha256 0768a732126c0cf14bdc0aee7d08ed5ea67d286cf0968e3828a87df58004a54a, blob '
        '3a9ef4bf55ae44e3ea5a4fbd0112b9ead6e32833); the other after-images and the WMShellModule pre-image are unchanged; '
        'the inputs, the goals (WindowManager-Shell, SystemUI-core) and flagEvidence (none) are unchanged: a field '
        'rename inside one file, with no new imports, files, classpath inputs or flags',
    'rebased': 'OS Core to OS Product, 2026-11-05 01:31 UTC, from #551 head 77c9117 (F06 app takeover and the F08 chin '
        'marker), re-stated 01:52 UTC for head b639c22 (F06 target_not_standard guard): '
        'background-control-frameworks-base.patch sha256 '
        '42986c0bf5e26e97b41ca5d62145b9a0ce45dd94810afcb307be658446cd8979, only ComputerControlSessionImpl\'s after-image '
        'changing (1a5392ce..., blob 7a0a3757); rose-edge-wmshell.patch sha256 '
        '335fcb61c6054806143a08c2d1a0202b01d71c45948a02a51eb0128b7700a95c, only RoseEdgeMicSlot.java (a613e6ee..., blob '
        '0aa0b49d) and roseedge/RoseEdgePolicy.java (5a565842..., blob 2c664048) changing; the other after-images, the '
        'WMShellModule pre-image, the inputs, the goals and flagEvidence (none) unchanged; no new files, flags or '
        'classpath inputs (the only new imports, android.app.ActivityManager, android.app.ActivityTaskManager, '
        'android.util.SparseBooleanArray and the framework constants WindowConfiguration.ACTIVITY_TYPE_STANDARD and '
        'ACTIVITY_TYPE_UNDEFINED, are in ComputerControlSessionImpl)',
    'retrayed': 'OS Core to OS Product, 2026-11-05 02:23 UTC, from #563 head 6e2fb49 (F06 return refinements and the F08 '
        'tray reachability signal): background-control-frameworks-base.patch sha256 '
        '22fc3d7c92aa0ad6f3692dc31c2e33fc9816972be6f9899b14db9aaac447c6bd, only ComputerControlSessionImpl\'s after-image '
        'changing (eef0edf1..., blob 8b680261); the required Shell overlays AgentTrayPrototype.java (2982f3f1..., blob '
        '39576fcd), AgentTrayLink.java (fdfc3560..., blob 3dca283c) and AgentStatusControls.java (86e9ceb0..., blob '
        'b5eb7993) change; rose-edge-wmshell.patch and its after-images, the WMShellModule pre-image, the goals and '
        'flagEvidence (none) unchanged; no new files, flags or classpath inputs (new imports android.util.SparseArray, '
        'android.database.ContentObserver, android.os.SystemProperties and android.provider.Settings are framework '
        'classes)',
    'shaded': 'OS Core to OS Product, 2026-11-07 01:30 UTC, from #616 head e070e6d (1G, the Rose notification shade): '
        'nine new SystemUI-core sources under packages/SystemUI/src/com/android/systemui/roseshade/ (required here, as '
        'every frameworks/base overlay is; none is a WindowManager-Shell input) and the required Shell overlay '
        'AgentTrayPrototype.java (5d3d5697..., blob 85080752: a status-bar drag hook before the tray\'s own touch '
        'handling) change; rose-edge-wmshell.patch and its after-images, the WMShellModule pre-image, the goals and '
        'flagEvidence (none) unchanged; the new imports (Dagger, NotificationListener, CoreStartable, the Shell\'s '
        'AgentTrayPrototype) are already on SystemUI-core\'s classpath. rose-shade-systemui.patch (one include in '
        'ReferenceSystemUIModule.java) is registered in apply-os-changes.py for the normal build, which is the compile '
        'proof; this inert contract stays bound to #551\'s patch and does not model it',
    'refined': 'OS Core to OS Product, 2026-11-07 17:06 and 17:29 UTC, from #653 head 17adb26 (abe56cb, the keyboard '
        'handoff\'s rule 4 "Mic off" status-bar indicator, and the F09 shade interaction refinements): only required '
        'overlays change, AgentTrayPrototype.java (3d09bead..., blob 97719d16) and roseshade/RoseShadeController.java '
        '(3a0e0df4..., blob c60a8788), RoseShadeDrag.java (723db90e..., blob 3342c6cb), RoseShadeModel.java '
        '(c80f399e..., blob 26c258b6), RoseShadeReplies.java (c7ea0f10..., blob 82b1235b) and RoseShadeStartable.java '
        '(e6783f9f..., blob ae1e0ae0); no patch, no new file; rose-edge-wmshell.patch and its after-images, the '
        'WMShellModule pre-image, the goals and flagEvidence (none) unchanged; the new imports '
        '(android.animation.ValueAnimator, android.graphics.Rect, android.view.ViewTreeObserver, '
        'android.view.accessibility.AccessibilityManager and AccessibilityNodeInfo, android.graphics.Paint and RectF, '
        'java.util.HashSet, LinkedHashSet and Set) are framework or JDK classes',
    'displayonly': 'OS Core in #653, 2026-11-07 UTC, from carrier head 1184f14 at the owner\'s direction (the status-bar '
        'microphone is display-only; the Rose chin holds the only microphone control, F08 and keyboard rule 4): only '
        'required overlays change, AgentStatusControls.java (58fa34e2..., blob 92ab854c), whose tap mapping drops '
        'MUTE/UNMUTE and the mic glow\'s START, and AgentTrayPrototype.java (756f6f83..., blob 2d720337), its one call '
        'without the muted argument; no patch, no new file, no new import; rose-edge-wmshell.patch and its '
        'after-images, the WMShellModule pre-image, the goals and flagEvidence (none) unchanged',
    'chinoverkeyboard': 'OS Core to OS Product, 2026-11-07 18:05 and 18:10 UTC, commit 081164b (e638d34 on main '
        '735f990, then the RoseEdgePolicy.java index line corrected to its content\'s blob 6d9df5f; the '
        'Rose keyboard\'s rules 1-2, carried in #665 with OS Product\'s Launcher3 half): rose-edge-wmshell.patch '
        'sha256 03f7537f388369bc785432cd67125d55011113376374dd6576006e63a9a9be6b, only roseedge/RoseEdgePolicy.java\'s '
        'after-image changing (6e517856..., blob 6d9df5fa: no busy refusal for a visible IME, onImeVisibility keeps the '
        'chin, LOWERED_IME keeps its number unsent); the other after-images, the WMShellModule pre-image, the overlays, '
        'the goals and flagEvidence (none) unchanged; no new files, imports or flags; the patch edit is line-count '
        'neutral (hunk headers unchanged)',
    'javacfix': 'OS Core to OS Product, 2026-11-07 18:26 UTC, commit 31c00a7 on 081164b (the Frankel 2026110702 '
        'SystemUI-core javac error, roseshade/RoseShadeController.java:990: inside the SwipeFrame FrameLayout the bare '
        'mContext is View\'s own field and cannot be read before super): only the required overlay '
        'RoseShadeController.java changes (1ec7b902..., blob 16b634ee), its super call now RoseShadeController.this.'
        'mContext; OS Product checked every m-prefixed name used in SwipeFrame and the three anonymous View subclasses '
        'against the pinned View, ViewGroup, FrameLayout, LinearLayout, TextView and EditText fields, and none other '
        'resolves to a framework field; no patch, no new file, no new import; goals and flagEvidence (none) unchanged',
    'chininset': 'OS Core to OS Product, 2026-11-07 18:42 UTC, commit 8142ffb on 31c00a7 (the chin inset, items 3-4, '
        'with OS Product\'s RoseEdgeWindow systemOverlays() inset in the same carrier): only the required overlay '
        'RoseShadeController.java changes (7978a7c1..., blob 1d3d8ad9), its panel inset now ime() | navigationBars() | '
        'systemOverlays() and its scrim\'s bottom margin the systemOverlays() bottom, a no-op while the chin provides '
        'nothing; systemOverlays() is public API 34; no patch, no new file, no new import; goals and flagEvidence '
        '(none) unchanged',
    'exactreturn': 'OS Core in #734, 2026-11-10 UTC, commits 47c8208, 589407e then f0dcd60 (1C, the exact-task '
        'return, protocol v1 agreed with App Core and Home/App; Rose\'s HOME task counts as Home, per Home/App): '
        'rose-edge-wmshell.patch sha256 42fcc4fc3d2c93295b3b12805e6e580c2b1615d91df09ec64e05dc705cf26cbd, only '
        'roseedge/RoseEdgeController.java (429757cf..., blob 8fd02b96) and roseedge/RoseEdgePolicy.java (a9cd7747..., '
        'blob a4003dcc) changing; the '
        'required overlays AgentTrayPrototype.java (4bac21a0..., blob ba75ed1b) and AgentTrayLink.java (90e9b648..., '
        'blob 48495c62, now public for its in-process Return seam) change; the other after-images, the '
        'WMShellModule pre-image, the goals and flagEvidence (none) unchanged; no new file, flag or classpath input '
        '(the new imports, '
        'android.animation.Animator, AnimatorListenerAdapter and ValueAnimator, android.app.ActivityManager, '
        'android.content.ComponentName, android.content.pm.PackageManager, android.graphics.Point, '
        'android.view.animation.PathInterpolator, java.security.SecureRandom, the WindowManager TRANSIT_OPEN, '
        'TRANSIT_CLOSE, TRANSIT_TO_FRONT and TRANSIT_TO_BACK constants and com.android.wm.shell.AgentTrayLink, are '
        'framework, JDK or Shell classes)',
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
