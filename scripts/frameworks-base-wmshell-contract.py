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
    'state': 'pending',
    'fields': [
        'candidateDelta: the patch bytes and its after images',
        'projects frameworks/base: WMShellModule.java pre is the pinned blob (no other patch touches it)',
        'overlayRoles.required: every frameworks/base overlay, AgentTrayLink among them',
        'overlayRoles.compileGoals: WindowManager-Shell and SystemUI-core',
        'flagEvidence: none',
    ],
    'rule': 'an adapter refuses this contract unless state is "confirmed"; a confirmation that changes any field '
        'regenerates the manifest',
}


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
    if manifest.get('confirmation', {}).get('state') not in ('pending', 'confirmed'):
        errors.append('confirmation.state is neither pending nor confirmed')
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
