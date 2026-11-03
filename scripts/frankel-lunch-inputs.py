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
        'dl/.find-ignore', 'vendor-skels/.find-ignore',
    ]),
    ('build/make', 'platform_build', ['envsetup.sh', 'core/version_util.mk', 'core/build_id.mk',
        'core/product_config.mk']),
    (KERNEL, 'device_google_laguna-kernels_6.6', []),
]
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
            'paths': len(listing(path))}})
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
    inherited = sorted({f'BUILD_ID_{DEVICE}', f'PLATFORM_SECURITY_PATCH_{DEVICE}', 'TARGET_KERNEL_DIR',
        'USE_STOCK_KERNEL'})
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
                'form is also written when no comparison ran: generate-all --updateSpec, a working-tree change to '
                'vendor-specs/ or vendor-skels/ (the comparison reads the spec on disk), and collect-state\'s '
                'temporary module. So the error form shows only the version, and generatedVendor.observable must '
                'still be compared',
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
                'nothing redirects outside the copy: rev-parse --absolute-git-dir and --git-common-dir resolve inside '
                    'the copy root; vendor/adevtool/.git is a directory or a link whose target is inside the copy, '
                    'never a gitfile; no objects/info/alternates, commondir or gitdir file exists; every link in the '
                    'gitdir resolves inside the copy root',
                'none of these checks can tell a fabricated store that holds the commit and its tree from a genuine '
                    'one: only an approved byte-for-byte copy from the genuine source, under exact paths and bounds, '
                    'establishes it',
            ],
            'options': [
                {'class': 'genuine-copy', 'status': 'allowed only under a separate approval with exact paths and '
                    'bounds; Release owns the mechanism'},
                {'class': 'redirect', 'status': 'refused', 'means': 'GIT_DIR, a gitfile, a link, alternates or any '
                    'other way of pointing the copy at the production gitdir or object store'},
                {'class': 'fabrication', 'status': 'refused', 'means': 'any HEAD, ref, object or gitdir written '
                    'rather than copied'},
                {'class': 'relaxation', 'status': 'refused', 'means': 'regenerating the vendor module with --noVerify '
                    'or --updateSpec, a working-tree change under vendor/adevtool (including vendor-specs/ and '
                    'vendor-skels/), or editing, replacing or removing adevtool-version-check.mk or frankel.mk'},
            ],
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
                'that step and is not in the spec. Whether a comparison ran, and against which spec bytes, is not '
                'recorded in the module, so the observer compares observable itself',
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
        rows = json.loads(PROVENANCE.read_text())['files']
        expected = [(p, f) for p, _, files in SOURCES for f in files]
        if [(r['project'], r['path']) for r in rows] != expected:
            errors.append('PROVENANCE.json does not list exactly the SOURCES files')
        for row in rows:
            if trees[row['project']].get(row['path'], (None, None))[1] != row['gitBlob']:
                errors.append(f'{row["project"]}/{row["path"]}: not the pinned tree\'s blob')
            if git_blob(fixture_bytes(row['project'], row['path'])) != row['gitBlob']:
                errors.append(f'{row["fixture"]}: bytes differ from the pinned blob')
        if manifest != build():
            errors.append('the manifest differs from what the fixtures give; regenerate with --write')
    except (KeyError, ValueError, TypeError, AttributeError, OSError, json.JSONDecodeError) as error:
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
            files.append({'fixture': str(target.relative_to(FIXTURES)), 'project': project, 'path': path,
                'revision': revisions[project], 'gitBlob': tree[path][1],
                'kind': 'gzip' if (project, path) in GZIPPED else 'full'})
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
