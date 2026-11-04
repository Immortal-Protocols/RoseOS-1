#!/usr/bin/env python3
"""os/compile-contracts/frankel-lunch-inputs.json: what `lunch frankel-cur-user` (and the product configuration `m`
repeats) needs beyond the three projects of launcher3-quickstep-lib.json, read from pinned public sources (#410).

OS Product owns this data. Release's adapter and observer read it as data, never this script. It states expected values
only: the adevtool version check frankel's generated product makefile includes, the genuine Git identity that check
needs, the other lunch markers, and the generated-vendor files an observer may compare. No value here is a host fact,
and none may be copied into any receipt or report.

  (default)  recompute the manifest offline from the committed fixtures in os/tests/fixtures/frankel-lunch-inputs/
             and the pinned manifest, and compare it with the committed one.
  --write    refetch the fixtures and tree listings from GrapheneOS's public sources at the pinned revisions (no
             credentials), then regenerate the manifest.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT/'os/compile-contracts/frankel-lunch-inputs.json'
PINNED = ROOT/'os/pinned-manifest.xml'
FIXTURES = ROOT/'os/tests/fixtures/frankel-lunch-inputs'
TREES = FIXTURES/'trees'
PROVENANCE = FIXTURES/'PROVENANCE.json'

LUNCH = {'target': 'frankel-cur-user', 'product': 'frankel', 'release': 'cur', 'variant': 'user'}
DEVICE = 'frankel'
VENDOR = f'vendor/google_devices/{DEVICE}'
VERSION_CHECK = f'{VENDOR}/adevtool-version-check.mk'
ADEVTOOL = 'vendor/adevtool'
KERNEL = 'device/google/laguna-kernels/6.6'
SKEL = f'vendor-skels/google_devices/{DEVICE}'
SPEC = f'vendor-specs/google_devices/{DEVICE}.yml'
# The generated build definitions an observer may compare with the reference FileTreeSpec (all small but frankel.mk).
OBSERVABLE = ['frankel.mk', 'AndroidProducts.mk', 'BoardConfig.mk', 'Android.mk', 'cmds-for-envsetup.sh']
EMPTY_BLOB = 'e69de29bb2d1d6434b8b29ae775ad8c2e48c5391'

# (project path, GrapheneOS repository, files). Every fixture is the whole pinned file, kept byte for byte with its
# upstream notices; the large ones (GZIPPED) are stored gzip-compressed and checked after decompression.
SOURCES = [
    (ADEVTOOL, 'adevtool', [
        'LICENSE', 'src/blobs/build.ts', 'src/config/paths.ts', 'src/util/file-tree-spec.ts', 'src/build/make.ts',
        'src/commands/generate-all.ts', 'src/commands/collect-state.ts',
        f'{SKEL}/{DEVICE}.mk', f'{SKEL}/AndroidProducts.mk', f'{SKEL}/BoardConfig.mk', f'{SKEL}/Android.mk',
        f'{SKEL}/cmds-for-envsetup.sh', SPEC,
        f'config/mk/google_devices/device/{DEVICE}/device.mk',
        'config/mk/google_devices/common/BoardConfig-common.mk',
        'config/mk/google_devices/common/BoardConfig-common-gs201-plus.mk',
        'config/mk/google_devices/platform/laguna/BoardConfig-common.mk',
        'dl/.find-ignore', 'vendor-skels/.find-ignore', '.gitignore',
    ]),
    ('build/make', 'platform_build', ['envsetup.sh', 'core/version_util.mk', 'core/build_id.mk',
        'core/product_config.mk']),
    (KERNEL, 'device_google_laguna-kernels_6.6', []),
]
# How an adapter checks each project's working tree in the copy against its listing.
WORKING_TREE_CHECK = {
    ADEVTOOL: 'exact: gitRequirement.workingTree',
    'build/make': 'derivation only: apply-os-changes.py patches build/make (agent tray, updater, background control), '
        'so the integrated working tree is not this listing; those post-images belong to an integration report',
    KERNEL: 'exact: no RoseOS patch or overlay touches it, so every listed path is present with exactly its mode and '
        'blob and nothing else is',
}
GZIPPED = {(ADEVTOOL, f'{SKEL}/{DEVICE}.mk'), (ADEVTOOL, SPEC)}
# The kernel module lists BoardConfig-common-gs201-plus.mk reads with $(shell cat ...) at lunch.
MODULE_LISTS = ['vendor_kernel_boot.modules.load', 'vendor_dlkm.modules.load', 'system_dlkm.modules.load']


def contract():
    spec = importlib.util.spec_from_file_location('launcher3_compile_contract', ROOT/'scripts/launcher3-compile-contract.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = contract()
sha256, git_blob, git_tree_hash, text_listing = C.sha256, C.git_blob, C.git_tree_hash, C.text_listing


def pins():
    xml = PINNED.read_text()
    out = {}
    for path, repo, _ in SOURCES:
        m = re.search(r'<project name="%s" path="%s" [^>]*revision="([0-9a-f]{40})"' % (re.escape(repo),
            re.escape(path)), xml)
        if not m:
            raise SystemExit(f'{path}: no pinned revision in {PINNED}')
        out[path] = m.group(1)
    return out


def remote_url(path):
    """The pinned manifest's own fetch URL for a project: its remote's fetch prefix plus its name."""
    xml = PINNED.read_text()
    project = re.search(r'<project name="([^"]+)" path="%s" remote="([^"]+)"' % re.escape(path), xml)
    remote = project and re.search(r'<remote name="%s" fetch="(https://[^"]+/)"' % re.escape(project.group(2)), xml)
    if not remote:
        raise SystemExit(f'{path}: no pinned https remote in {PINNED}')
    return remote.group(1) + project.group(1)


def working_tree():
    """The adevtool working tree in the copy: exactly its pinned listing. The paths its pinned .gitignore ignores are
    never lunch inputs (lunch runs no adevtool code), so the copy omits them instead of admitting unpinned content."""
    rules = [l for l in fixture_bytes(ADEVTOOL, '.gitignore').decode().splitlines() if l and not l.startswith('#')]
    return {
        'project': ADEVTOOL,
        'rule': f'every path of sources[{ADEVTOOL}].listing is present with exactly its mode and blob, and nothing '
            'else is, except .git (gitRequirement)',
        'omittedFromCopy': {'gitignore': rules, 'gitBlob': listing(ADEVTOOL)['.gitignore'][1],
            'means': 'the copy leaves out exactly what these pinned .gitignore rules ignore (git check-ignore '
                'semantics, from the project root); a tracked path is never omitted'},
        'untrackedAllowed': [],
        'why': 'the product finder prunes only dl/ and vendor-skels/ (finderPrune), so untracked content anywhere '
            'else, such as node_modules/, would be scanned at lunch as an unpinned input',
        'onUnlisted': 'refuse, naming every unlisted path found (bounded), so a first run reports what a real tree '
            'holds instead of admitting it',
    }


def tree_fixture(project):
    return TREES/(project.replace('/', '__') + '.ls-tree.txt.gz')


def listing(project):
    """{path: (mode, blob)} of the pinned tree, from the committed `git ls-tree -r --full-tree` listing."""
    out = {}
    for line in gzip.decompress(tree_fixture(project).read_bytes()).decode().splitlines():
        meta, path = line.split('\t', 1)
        mode, kind, blob = meta.split(' ')
        if kind != 'blob' or path.startswith('"'):
            raise ValueError(f'{project}/{path}: not a plain blob entry')
        out[path] = (mode, blob)
    return out


def root_tree(project):
    return git_tree_hash([(mode, 'blob', blob, path) for path, (mode, blob) in listing(project).items()])


def fixture(project, path):
    """The committed fixture file (gzip-compressed for GZIPPED)."""
    return FIXTURES/project/(path + '.gz' if (project, path) in GZIPPED else path)


def fixture_bytes(project, path):
    data = fixture(project, path).read_bytes()
    return gzip.decompress(data) if (project, path) in GZIPPED else data


def lines_of(project, path):
    """{line number: text} of a fixture."""
    return {i + 1: line for i, line in enumerate(fixture_bytes(project, path).decode().split('\n'))}


def find(project, path, needle):
    hits = [(n, t) for n, t in sorted(lines_of(project, path).items()) if needle in t]
    if not hits:
        raise ValueError(f'{project}/{path}: no line contains {needle!r}')
    return hits


def cite(project, path, needle, revision):
    n, text = find(project, path, needle)[0]
    return {'project': project, 'revision': revision, 'path': path, 'line': n, 'text': text.strip()}


def version_check_bytes(revision, warn):
    """The exact bytes writeVersionCheckFile writes for frankel, interpreted from the pinned template (only the
    placeholders it uses; anything else refuses)."""
    source = fixture_bytes(ADEVTOOL, 'src/blobs/build.ts').decode()
    body = source[source.index('let contents =') + len('let contents ='):source.index('await fs.writeFile(getVersionCheckFilePath')]
    parts = re.findall(r'`((?:[^`\\]|\\.)*)`', body)
    if len(parts) != 3:
        raise ValueError('build.ts: the version-check template is not the pinned three parts')
    relative = re.search(r"export const RELATIVE_ADEVTOOL_PATH = '([^']+)'", fixture_bytes(ADEVTOOL, 'src/config/paths.ts')
        .decode()).group(1)
    values = {'RELATIVE_ADEVTOOL_PATH': relative, 'adevtoolRevision': revision, 'deviceName': DEVICE,
        "warnOnly ? 'warning' : 'error'": 'warning' if warn else 'error'}
    out = []
    for part in parts:
        def substitute(m):
            if m.group(1) not in values:
                raise ValueError(f'build.ts: unexpected placeholder {m.group(1)}')
            return values[m.group(1)]
        text = re.sub(r'\$\{([^}]*)\}', substitute, part)
        out.append(text.replace('\\n', '\n').replace('\\`', '`'))
    return ''.join(out).encode()


def template_lines():
    text = fixture_bytes(ADEVTOOL, 'src/blobs/build.ts').decode().split('\n')
    start = next(i for i, t in enumerate(text) if 'let contents =' in t)
    end = next(i for i, t in enumerate(text) if 'await fs.writeFile(getVersionCheckFilePath' in t)
    return [t.strip() for t in text[start:end]]


def image(data):
    return {'sha256': sha256(data), 'gitBlob': git_blob(data), 'bytes': len(data)}


def manifest_files():
    """Every linkfile and copyfile of the pinned manifest: lunch needs them at the source root (e.g. the root
    Android.bp and build/envsetup.sh links)."""
    import xml.etree.ElementTree as ET
    out = []
    for project in ET.parse(PINNED).getroot().iter('project'):
        for child in project:
            if child.tag in ('linkfile', 'copyfile'):
                out.append({'kind': child.tag, 'project': project.get('path') or project.get('name'),
                    'src': child.get('src'), 'dest': child.get('dest')})
    return sorted(out, key=lambda r: r['dest'])


def build():
    revisions = pins()
    sources = []
    for path, repo, _ in SOURCES:
        raw = gzip.decompress(tree_fixture(path).read_bytes())
        sources.append({'path': path, 'repo': f'GrapheneOS/{repo}', 'revision': revisions[path],
            'rootTree': root_tree(path), 'listing': {'path': str(tree_fixture(path).relative_to(ROOT)),
            'sha256': sha256(raw), 'compressedSha256': sha256(tree_fixture(path).read_bytes()),
            'paths': len(listing(path))}, 'workingTreeCheck': WORKING_TREE_CHECK[path]})
    trees = {p: listing(p) for p, _, _ in SOURCES}
    provenance = json.loads(PROVENANCE.read_text())
    adev = revisions[ADEVTOOL]
    make = revisions['build/make']

    spec = {}
    for text in lines_of(ADEVTOOL, SPEC).values():
        if text:
            name, _, digest = text.partition(': ')
            spec[name] = digest
    observable = []
    for name in OBSERVABLE:
        data = fixture_bytes(ADEVTOOL, f'{SKEL}/{name}')
        if sha256(data) != spec[name]:
            raise ValueError(f'{SKEL}/{name}: the skeleton is not the reference FileTreeSpec entry')
        observable.append({'path': f'{VENDOR}/{name}', 'sha256': spec[name], 'skeleton': f'{ADEVTOOL}/{SKEL}/{name}',
            'skeletonGitBlob': trees[ADEVTOOL][f'{SKEL}/{name}'][1]})

    product = f'{SKEL}/{DEVICE}.mk'
    include = cite(ADEVTOOL, product, 'adevtool-version-check.mk', adev)
    if include['text'] != f'include {VERSION_CHECK}':
        raise ValueError(f'{product}: the version check is not included as {VERSION_CHECK}')
    build_id = re.search(r'ifneq \(\$\(BUILD_ID\),([^)]+)\)', cite(ADEVTOOL, product, 'BUILD_ID', adev)['text']).group(1)
    envsetup_cmds = fixture_bytes(ADEVTOOL, f'{SKEL}/cmds-for-envsetup.sh').decode()
    exported = re.search(r'export BUILD_ID_%s="([^"]+)"' % DEVICE, envsetup_cmds).group(1)
    if exported != build_id:
        raise ValueError('cmds-for-envsetup.sh does not export the BUILD_ID frankel.mk checks')
    default = cite('build/make', 'core/build_id.mk', 'BUILD_ID=', make)
    if default['text'] == f'BUILD_ID={build_id}':
        raise ValueError("the build/make default BUILD_ID would pass frankel.mk's check")
    device_mk = f'config/mk/google_devices/device/{DEVICE}/device.mk'
    kernel_dir = re.search(r'TARGET_KERNEL_DIR \?= (\S+)', fixture_bytes(ADEVTOOL, device_mk).decode()).group(1)
    if not kernel_dir.startswith(KERNEL + '/'):
        raise ValueError(f'TARGET_KERNEL_DIR {kernel_dir} is not in {KERNEL}')
    stock = cite(ADEVTOOL, product, 'USE_STOCK_KERNEL ?=', adev)
    gs201 = 'config/mk/google_devices/common/BoardConfig-common-gs201-plus.mk'
    gs201_text = fixture_bytes(ADEVTOOL, gs201).decode()
    modules = []
    for name in MODULE_LISTS:
        relative = kernel_dir[len(KERNEL) + 1:] + '/' + name
        mode, blob = trees[KERNEL][relative]
        if blob == EMPTY_BLOB:
            raise ValueError(f'{KERNEL}/{relative}: empty')
        guarded = f'$(error {name} not found or empty)' in gs201_text
        modules.append({'path': f'{kernel_dir}/{name}', 'mode': mode, 'gitBlob': blob,
            'readBy': cite(ADEVTOOL, gs201, f'$(KERNEL_MODULE_DIR)/{name}', adev),
            'lunchGuard': cite(ADEVTOOL, gs201, f'$(error {name} not found or empty)', adev) if guarded else None,
            'rule': 'a missing or empty file stops lunch with $(error)' if guarded else 'read at lunch with no '
                'guard: a missing or empty file does not stop lunch, so an observer checks it is present at this blob'})
    markers = []
    for marker in ('dl/.find-ignore', 'vendor-skels/.find-ignore'):
        markers.append({'path': f'{ADEVTOOL}/{marker}', 'gitBlob': trees[ADEVTOOL][marker][1]})
    skels_with_products = sorted(p for p in trees[ADEVTOOL] if p.startswith('vendor-skels/')
        and p.endswith('/AndroidProducts.mk'))
    error, warning = version_check_bytes(adev, False), version_check_bytes(adev, True)
    inherited = sorted({f'BUILD_ID_{DEVICE}', 'TARGET_KERNEL_DIR', 'USE_STOCK_KERNEL'})
    return {
        'schemaVersion': 1,
        'kind': 'rose-frankel-lunch-inputs',
        'owner': 'OS Product (#410). Release owns the adapter, the observer and the workflow; they read this as data.',
        'lunch': LUNCH,
        'provenance': 'every value is an expected value derived from the pinned public sources below, except '
            'gitRequirement.genuineSource, which is marked historical context. None is a host fact. An observer or '
            'adapter compares current bytes with them and never copies them into any receipt, bundle report or binding',
        'sources': sources,
        'fixtures': provenance['files'],
        'environment': {
            'unset': ['GIT_*'] + inherited,
            'when': 'before envsetup and lunch; lunch then exports BUILD_ID_frankel from cmds-for-envsetup.sh into the '
                'shell that runs m, which needs it',
            'setByLunchOnly': f'BUILD_ID_{DEVICE} comes only from {VENDOR}/cmds-for-envsetup.sh during lunch',
            'why': 'git reads GIT_* and stops at GIT_CEILING_DIRECTORIES (the copy root); BUILD_ID_frankel inherited '
                'from the caller hides a missing cmds-for-envsetup.sh; TARGET_KERNEL_DIR or USE_STOCK_KERNEL=true '
                'switches the kernel directory away from kernelModuleLists.dir and lunch still passes',
            'sources': [cite(ADEVTOOL, device_mk, 'TARGET_KERNEL_DIR ?=', adev), stock,
                cite('build/make', 'core/version_util.mk', 'BUILD_ID := $(BUILD_ID_$(TARGET_PRODUCT))', make)],
        },
        'versionCheck': {
            'path': VERSION_CHECK,
            'writer': {**cite(ADEVTOOL, 'src/blobs/build.ts', 'export async function writeVersionCheckFile', adev),
                'template': template_lines()},
            'expected': {'revision': adev, 'form': 'error', 'error': image(error), 'warning': image(warning)},
            'formRule': 'the error form is necessary, not sufficient. generate-all writes the warning form only with '
                '--noVerify, which skips the FileTreeSpec comparison, so a warning-form module is refused. The error '
                'form is also written when no comparison ran (generate-all --updateSpec, and collect-state\'s '
                'temporary module) and when the comparison ran against an edited spec (a working-tree change to '
                'vendor-specs/, since it reads the spec on disk). So the error form shows only the version, and '
                'generatedVendor.observable must still be compared',
            'formSources': [cite(ADEVTOOL, 'src/commands/generate-all.ts', 'writeVersionCheckFile(', adev),
                cite(ADEVTOOL, 'src/commands/generate-all.ts', 'if (flags.updateSpec) {', adev),
                cite(ADEVTOOL, 'src/commands/generate-all.ts', 'path.join(VENDOR_MODULE_SPECS_DIR', adev),
                cite(ADEVTOOL, 'src/commands/collect-state.ts', 'writeVersionCheckFile(', adev)],
            'includedFrom': {'path': f'{VENDOR}/{DEVICE}.mk', 'line': include['line'], 'text': include['text']},
            'selectedBy': {'path': f'{VENDOR}/AndroidProducts.mk',
                'text': find(ADEVTOOL, f'{SKEL}/AndroidProducts.mk', 'PRODUCT_MAKEFILES')[0][1].strip(),
                'productConfig': cite('build/make', 'core/product_config.mk', 'current_product_makefile', make)},
            'executes': {'make': '$(shell git -C vendor/adevtool rev-parse HEAD)',
                'argv': ['git', '-C', ADEVTOOL, 'rev-parse', 'HEAD'], 'cwd': 'the source root',
                'compare': '$(shell) stdout, trailing newline removed, must equal expected.revision exactly'},
            'runsIn': ['lunch: build_build_var_cache runs soong_ui --dumpvars-mode, whose product configuration '
                'imports frankel.mk for TARGET_PRODUCT=frankel',
                'm: the same product configuration again (its dumpvars and the main kati run)'],
            'lunchSource': cite('build/make', 'envsetup.sh', '--dumpvars-mode', make),
            'skipSwitch': None,
            'inFileTreeSpec': 'adevtool-version-check.mk' in spec,
            'withoutGit': 'git exits non-zero with nothing on stdout, the comparison fails and the error form stops '
                'lunch with "frankel vendor module is outdated"',
        },
        'gitRequirement': {
            'project': ADEVTOOL, 'revision': adev, 'rootTree': root_tree(ADEVTOOL), 'path': f'{ADEVTOOL}/.git',
            'functionalMinimum': 'git accepts a directory with a valid HEAD and enterable objects/ and refs/ as a '
                'repository, and rev-parse HEAD on a detached HEAD reads no object; so a lunch that passes the check '
                'does not show that the Git data is genuine (os/tests/test_frankel_lunch_inputs.py demonstrates it)',
            'genuineSource': {'link': f'{ADEVTOOL}/.git', 'linkText': '../../.repo/projects/vendor/adevtool.git',
                'gitdir': '.repo/projects/vendor/adevtool.git', 'kind': 'historical',
                'basis': 'repo checks a project out with .git as a link into .repo/projects; observation 41566744429 '
                    'recorded this link text on the production tree. Historical context, not an expected value and '
                    'not current state'},
            'acceptance': [
                'HEAD is exactly revision plus one newline',
                'in the copy, with GIT_* unset and GIT_CEILING_DIRECTORIES at the copy root, git -C vendor/adevtool '
                    'rev-parse HEAD prints exactly revision, cat-file -t revision is commit and revision^{tree} is '
                    'rootTree',
                'nothing redirects outside the copy: vendor/adevtool/.git resolves to a directory inside the copy (a '
                    'gitfile, or a link to one, is refused); rev-parse --absolute-git-dir and --git-common-dir resolve '
                    'inside the copy root; no objects/info/alternates, commondir or gitdir file exists; every link in '
                    'the gitdir resolves inside the copy root; every path in the gitdir is on the copy root\'s '
                    'filesystem and none is a mount point; no regular file in it has more than one hard link',
                'items 1-3 alone cannot tell a hand-written gitdir from a genuine one: a HEAD file and an object store '
                    'holding only the commit pass them. The objects are genuine only when they come from the genuine '
                    'source, either as an approved byte-for-byte copy (genuine-copy) or fetched from the pinned public '
                    'remote with every publicFetch.acceptance check passing. Object ids are content hashes, git hashes '
                    'each fetched object as it indexes it, and connectivity at rootTree then shows the store holds the '
                    'genuine commit and its whole tree',
            ],
            'options': [
                {'class': 'genuine-copy', 'status': 'allowed only under a separate approval with exact paths and '
                    'bounds; Release owns the mechanism'},
                {'class': 'redirect', 'status': 'refused', 'means': 'GIT_DIR, a gitfile, a link, alternates or any '
                    'other way of pointing the copy at the production gitdir or object store'},
                {'class': 'public-fetch', 'status': 'allowed only under a separate runtime approval for its '
                    'source and bounds; the interface is publicFetch; Release owns the mechanism',
                    'url': remote_url(ADEVTOOL)},
                {'class': 'fabrication', 'status': 'refused', 'means': 'any HEAD, ref, object or gitdir written '
                    'other than by a genuine copy or by exactly the publicFetch steps'},
                {'class': 'relaxation', 'status': 'refused', 'means': 'regenerating the vendor module with --noVerify '
                    'or --updateSpec, a working-tree change under vendor/adevtool (including vendor-specs/ and '
                    'vendor-skels/), or editing, replacing or removing adevtool-version-check.mk or frankel.mk'},
            ],
            'publicFetch': {
                'basis': 'Infra\'s assessment of adevtool_git_missing (2026-11-04): the isolated copy excludes every '
                    '.git, so lunch needs genuine metadata for this one project. This follows Blazer\'s git-meta steps '
                    '(scripts/blazer-build-only.py git_meta_argv and git_meta) with a stricter tree check',
                'url': remote_url(ADEVTOOL), 'protocol': 'https only',
                'target': f'<copy>/{ADEVTOOL}/.git: a new directory, created by the attempt inside the isolated copy '
                    'only, after the copy\'s own checks; never the source tree',
                'steps': [
                    f'git init -q --template= <copy>/{ADEVTOOL}',
                    f'git -C <copy>/{ADEVTOOL} fetch --quiet --depth=1 --no-tags <url> {adev}',
                    f'git -C <copy>/{ADEVTOOL} update-ref --no-deref HEAD {adev}',
                ],
                'environment': {'unset': 'GIT_*', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                    'HOME': 'an attempt-owned empty directory (also XDG_CONFIG_HOME)', 'GIT_TERMINAL_PROMPT': '0',
                    'GIT_ASKPASS': '/bin/false', 'SSH_ASKPASS': '/bin/false', 'GIT_ALLOW_PROTOCOL': 'https',
                    'GIT_CEILING_DIRECTORIES': 'the copy root', 'PATH': '/usr/bin:/bin'},
                'never': ['--reference, --shared, --dissociate or --separate-git-dir', 'objects/info/alternates',
                    'a configured remote', 'inherited system or global config', 'hooks or templates',
                    'any command that reads or writes the working tree (checkout, reset, status, add)',
                    'tags or any ref other than HEAD'],
                'acceptance': [
                    'gitRequirement.acceptance items 1-3, unchanged',
                    'rev-parse --verify revision^{tree} is exactly rootTree (Blazer checks only that it exists)',
                    'git fsck --no-dangling --no-progress succeeds: a full check, so every object\'s hash and every link '
                        'from the commit are verified (not --connectivity-only)',
                    'git ls-tree -r -z --full-tree revision, each NUL replaced by LF, is byte for byte the decompressed '
                        f'sources[{ADEVTOOL}].listing (its sha256)',
                    'shallow is exactly revision plus one newline',
                    'no objects/info/alternates and no remote.* key in git config --local --list',
                    'workingTree holds',
                ],
                'establishes': 'the genuine objects of public commit revision, by content addressing (commit, tree, '
                    'blobs, each hash-checked); not provenance from the production gitdir, which only genuine-copy '
                    'shows. Tree equality, a full fsck and working-tree equality are stronger than the functional '
                    'minimum',
                'cleanup': 'on any failure, remove only the .git this attempt created; nothing else in the copy, and '
                    'never anything in the source tree',
                'ceiling': {'seconds': 1800, 'bytes': 1073741824, 'kind': 'Infra\'s proposed execution ceiling for '
                    'the attempt; neither a measured requirement nor a runtime approval'},
                'unbound': 'sourceTree, lunch, policy and integration-report bindings stay unbound; without an approved '
                    'attempt that passes, the adapter still refuses with adevtool_git_missing',
            },
            'workingTree': working_tree(),
            'environment': {'GIT_CEILING_DIRECTORIES': 'the copy root', 'unset': 'GIT_*',
                'why': 'without a ceiling git searches above the copy, and a parent repository would answer with a '
                    'foreign HEAD'},
            'otherGit': 'none: in the pinned public sources nothing else needs .git or a git process for this lunch '
                'and Launcher3QuickStepLib (every *.mk and *.bp of the pinned manifest was searched, #410); content '
                'the generated vendor module adds beyond its reference is not covered',
        },
        'buildId': {
            'expected': build_id,
            'checkedBy': {'path': f'{VENDOR}/{DEVICE}.mk', **{k: v for k, v in cite(ADEVTOOL, product, 'BUILD_ID', adev)
                .items() if k in ('line', 'text')}},
            'setBy': {'path': f'{VENDOR}/cmds-for-envsetup.sh', 'text': f'export BUILD_ID_{DEVICE}="{exported}"'},
            'sourcedBy': cite('build/make', 'envsetup.sh', 'local EXTRA_CMDS_ARR=', make),
            'readBy': cite('build/make', 'core/version_util.mk', 'BUILD_ID := $(BUILD_ID_$(TARGET_PRODUCT))', make),
            'defaultWithout': default,
            'rule': f'with BUILD_ID_{DEVICE} unset beforehand (environment.unset): no vendor/*/frankel/'
                'cmds-for-envsetup.sh leaves BUILD_ID at the default and frankel.mk stops lunch; more than one waits on '
                'stdin (read -s) and then exits the sourcing shell. The variable is exported into the shell that runs m',
        },
        'kernelModuleLists': {
            'dir': kernel_dir,
            'setBy': cite(ADEVTOOL, device_mk, 'TARGET_KERNEL_DIR', adev),
            'switchedBy': stock,
            'files': modules,
            'rule': 'each file states its own rule; dir holds only with TARGET_KERNEL_DIR and USE_STOCK_KERNEL unset '
                '(environment.unset)',
        },
        'finderPrune': {
            'markers': markers,
            'why': f'{ADEVTOOL}/vendor-skels holds an AndroidProducts.mk for {len(skels_with_products)} devices; without '
                'its .find-ignore the product finder would also list a second frankel product',
        },
        'manifestFiles': manifest_files(),
        'generatedVendor': {
            'path': VENDOR,
            'generator': {'project': ADEVTOOL, 'revision': adev, 'command': f'generate-all -d {DEVICE}'},
            'reference': {'path': f'{ADEVTOOL}/{SPEC}', 'gitBlob': trees[ADEVTOOL][SPEC][1],
                'kind': 'FileTreeSpec: sha256 of every regular file of the module, "dir" for each directory'},
            'verification': 'generate-all compares the generated module with the spec file on disk only without '
                '--noVerify and without --updateSpec, and fails on any difference; the version check is written after '
                'that step and is not in the spec. The warning form records --noVerify, but the error form does not '
                'record whether a comparison ran or against which spec bytes, so the observer compares observable '
                'itself',
            'verificationSource': cite(ADEVTOOL, 'src/commands/generate-all.ts', 'compareToReferenceFileTreeSpec(', adev),
            'observable': observable,
            'observableScope': 'these five build definitions only; the rest of the module (proprietary files, '
                'overlays, sepolicy) is not covered here',
            'notEstablished': 'the host\'s generated bytes, which flags generated them, and whether vendor/adevtool\'s '
                'working tree matched the pin at the time',
        },
        'branchUse': 'every branch of launcher3-quickstep-lib.json runs this lunch, so each needs versionCheck satisfied '
            'by gitRequirement, environment applied and the markers above present; none of this is a bundle report, '
            'a policy receipt or evidence about the three contract projects',
    }


def check(manifest):
    """Every fixture is its pinned blob (gzip fixtures after decompression), and the manifest is exactly what the
    fixtures give. A malformed manifest or fixture set is reported, never raised."""
    errors = []
    try:
        trees = {}
        for path, _, _ in SOURCES:
            if not tree_fixture(path).exists():
                return [f'{path}: no pinned tree listing']
            trees[path] = listing(path)
        document = json.loads(PROVENANCE.read_text())
        rows = document['files']
        if PROVENANCE.read_text() != json.dumps(document, indent=1) + '\n' or set(document) != {'files'}:
            errors.append('PROVENANCE.json is not exactly its canonical {"files": [...]} form')
        expected = [(p, f) for p, _, files in SOURCES for f in files]
        if [(r['project'], r['path']) for r in rows] != expected:
            errors.append('PROVENANCE.json does not list exactly the SOURCES files')
        present = sorted(str(f.relative_to(FIXTURES)) for f in FIXTURES.rglob('*') if f.is_file() or f.is_symlink())
        wanted = sorted([r['fixture'] for r in rows] + ['PROVENANCE.json'] +
            [str(tree_fixture(p).relative_to(FIXTURES)) for p, _, _ in SOURCES])
        if present != wanted:
            errors.append('the fixture directory holds files other than its listed fixtures: '
                + ', '.join(sorted(set(present) ^ set(wanted))))
        for row in rows:
            if row['kind'] == 'gzip' and sha256(fixture(row['project'], row['path']).read_bytes()) != row.get(
                    'compressedSha256'):
                errors.append(f'{row["fixture"]}: the compressed bytes are not the recorded ones')
        for row in rows:
            if trees[row['project']].get(row['path'], (None, None))[1] != row['gitBlob']:
                errors.append(f'{row["project"]}/{row["path"]}: not the pinned tree\'s blob')
            if git_blob(fixture_bytes(row['project'], row['path'])) != row['gitBlob']:
                errors.append(f'{row["fixture"]}: bytes differ from the pinned blob')
        if manifest != build():
            errors.append('the manifest differs from what the fixtures give; regenerate with --write')
    except (KeyError, ValueError, TypeError, AttributeError, OSError, EOFError, zlib.error) as error:
        errors.append(f'fixtures: {type(error).__name__}: {error}')
    return errors


def fetch_tree(repo, revision):
    work = Path(tempfile.mkdtemp(prefix='lunch-tree-'))
    try:
        subprocess.run(['git', 'init', '-q', str(work)], check=True)
        subprocess.run(['git', '-C', str(work), 'fetch', '-q', '--depth', '1', '--filter=blob:none',
            f'https://github.com/GrapheneOS/{repo}', revision], check=True)
        return gzip.compress(text_listing(subprocess.check_output(['git', '-C', str(work), 'ls-tree', '-r', '-z',
            '--full-tree', revision]), f'{repo} {revision}'), 9, mtime=0)
    finally:
        shutil.rmtree(work, True)


def fetch(repo, revision, path):
    with urllib.request.urlopen(f'https://raw.githubusercontent.com/GrapheneOS/{repo}/{revision}/{path}', timeout=60) as r:
        return r.read()


def write():
    revisions = pins()
    if FIXTURES.exists():
        shutil.rmtree(FIXTURES)
    TREES.mkdir(parents=True)
    files = []
    for project, repo, paths in SOURCES:
        tree_fixture(project).write_bytes(fetch_tree(repo, revisions[project]))
        tree = listing(project)
        for path in paths:
            data = fetch(repo, revisions[project], path)
            if git_blob(data) != tree[path][1]:
                raise SystemExit(f'{repo}/{path}: fetched bytes are not the pinned blob')
            target = fixture(project, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gzip.compress(data, 9, mtime=0) if (project, path) in GZIPPED else data)
            row = {'fixture': str(target.relative_to(FIXTURES)), 'project': project, 'path': path,
                'revision': revisions[project], 'gitBlob': tree[path][1],
                'kind': 'gzip' if (project, path) in GZIPPED else 'full'}
            if (project, path) in GZIPPED:
                row['compressedSha256'] = sha256(target.read_bytes())
            files.append(row)
    PROVENANCE.write_text(json.dumps({'files': files}, indent=1) + '\n')
    MANIFEST.write_text(json.dumps(build(), indent=1) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--write', action='store_true', help='refetch fixtures from pinned public sources')
    if parser.parse_args().write:
        write()
    errors = check(json.loads(MANIFEST.read_text()))
    for error in errors:
        print('FAIL:', error)
    if errors:
        raise SystemExit(1)
    print(f'{MANIFEST.relative_to(ROOT)} recomputes from its pinned fixtures (sha256 {sha256(MANIFEST.read_bytes())})')


if __name__ == '__main__':
    main()
