#!/usr/bin/env python3
"""LAUNCHER3-COMPILE adapter: a compile-only proof of RoseOS's Launcher3 changes against OS Product's
input contract os/compile-contracts/launcher3-quickstep-lib.json (#410/#440). It reads the contract as data only and never
loads OS Product's checker (scripts/launcher3-compile-contract.py), which B's tests use for parity.

  check                       hosted and on the builder: the contract is the pinned version (MANIFEST_SHA256; the
                              text it states and this adapter implements, CONTRACT_TEXT_SHA256), the binding
                              os/build-only/launcher3-compile-lane.json is exact and names independent capacity, every input the selected branch
                              requires is bound and every input it forbids is null (branches.requires, forbids), and
                              no bound receipt or report is a file of the source tree or of a launcher3-compile run
                              root. Exit 4 while any required input is null. Reads no host path. The lunch is the
                              contract's lunch (definitions.lunch): the target of OS Product's lunch-input contract
                              os/compile-contracts/frankel-lunch-inputs.json at its pin (LUNCH_INPUTS_SHA256), which also
                              needs the binding's adevtoolGit (public-fetch) and that contract's own admission of the
                              public-fetch class for the pinned manifest's source, with its publicFetch and workingTree
                              exactly this adapter's mechanism; otherwise exit 4 (adevtool_git_missing or
                              adevtool_git_unadmitted), or 2 when the contract and this adapter disagree or the lunch
                              is not the contract's target.
  run --run-id --attempt      under the independent lane's nonblocking builder lock, into a new per-run root
                              <taskParent>/launcher3-compile-<run>-<attempt>/{tree,out,home,tmp,receipts}:
                              1. byte-copy the bound source tree (read only; alpha exclusion policy: outputs, .repo,
                                 .git, keys, releases; less exactly what vendor/adevtool's pinned .gitignore ignores),
                                 refused before writing when the selection projects above SOURCE_BOUND_BYTES or
                                 available minus the projection is below RESERVE_BYTES;
                              2. check the copy for the bound branch, refusing at the first failure by name:
                                 verifyIntegrated - every affected file equals its post-image (config.xml only through
                                 the contract's rewrite rules with the bound policy receipt and frankel's applyArgs),
                                 every required overlay is present at its sha256 and every other path of the three
                                 projects matches its tree pin (Git blob IDs recomputed from the copied bytes), with no
                                 extra path; nothing is applied;
                                 verifyBaseApplyCandidate - base-exact (the bound rose-integration-bundle-report v1
                                 is exactly base.bundleReport's form, the association it shows holds and its observed
                                 bundle equals base.bundleContent; then the copy equals the base images and base tree
                                 pins), delta-preimages (every delta file and overlay at its before image),
                                 isolated-apply (every digest-checked candidate patch passes `git apply --check` on the
                                 unchanged copy before any is applied, then each is applied with exactly the
                                 contract's steps and the candidate overlays installed, in the copy only, never the
                                 source tree), final-exact (as verifyIntegrated);
                                 reconstruct - needs an approved genuine-Git-object source, none is bound: refused;
                                 anything else (mixed, pre-images, unknown hashes, missing or extra files) - refused,
                                 nothing is reset;
                              3. lunch on its own (LUNCH_SECONDS; a failed lunch is reported and m is not run), then
                                 `m -j8 Launcher3QuickStepLib <flag caches>`, with OUT_DIR, HOME, TMPDIR and XDG_* inside
                                 the run root, no ccache, GIT_CEILING_DIRECTORIES at the copy root and no system or user
                                 git configuration (without an admitted adevtool fetch, or for a lunch other than the
                                 lunch-input contract's target, the run is refused before the copy); before that lunch,
                                 in the copy and with no network, the lunch inputs: vendor/adevtool exactly its pinned
                                 tree (every listed path at its mode and blob, nothing else, no .git), the version
                                 check's error form, the generated vendor files, the finder markers, the kernel module
                                 lists and the manifest links and copies; then the public fetch of exactly the pinned
                                 adevtool commit into a new vendor/adevtool/.git (no hooks, remote, alternates or
                                 inherited configuration; ADEVTOOL_FETCH_SECONDS, ADEVTOOL_GIT_BYTES), verified (HEAD,
                                 shallow, commit, root tree, full fsck, its tree equals the pinned listing, nothing
                                 redirects outside the copy) and removed again on any failure; sampled stops (every
                                 SAMPLE_SECONDS)
                                 on the output filesystem's growth (OUTPUT_STOP_BYTES) and the resident memory of the
                                 stage's whole session (MEMORY_STOP_BYTES), and a wall-clock COMPILE_SECONDS; a stop
                                 ends the stage's whole session (TERM with CONT, then KILL, to every process group in
                                 it: ninja gives each job its own), and a session that outlives its leader's normal
                                 exit is ended and stops the stage ('leftover'); each stage records whether its session
                                 was confirmed gone, and one that was not stops the run. A stage runs under deferral for
                                 its whole life: the outer timeout's TERM, or a cancel reaching this helper through the
                                 step's exec'd timeout (SIGTERM, SIGINT, SIGHUP, SIGQUIT), is noticed within
                                 POLL_SECONDS and ends the running stage's session within INTERRUPT_GRACE_SECONDS a
                                 phase, inside a cancel's 7.5 s before the runner's SIGTERM, and the report says
                                 interrupted and whether the session was confirmed gone. run holds receipts/run.lock for
                                 its whole life and names each running stage's session in receipts/stage.json, marked
                                 ended once run confirms it gone;
                              4. a sanitized report.json: the branch, the classification, the bound inputs (for the
                                 candidate branch the bundle report used and what of its association was taken on
                                 trust), the compile outcome, the source-list and classpath proof and both aconfig flag
                                 values; a refusal says whether any candidate write started. Never a raw log, never
                                 current bytes as provenance.

  finish --run-id --attempt --upload-dir
                              always after run: it first waits up to FINISH_LOCK_SECONDS for run's lock and ends a
                              recorded stage session that outlived run (or removes nothing and says so); then only
                              report.json and classification.json are copied for upload, and this run's tree, out, home
                              and tmp are removed (cleanup.json says what was removed or left); nothing outside the run
                              root is touched.

What a passing report shows: the patched handlers and overlays type-check and compile on the real classpath, and the
generated aconfig values. Not R8, manifest merge, APK resources, packaging, signing or runtime behaviour. The sampled
stops can overshoot and are not hard caps; containment is by paths and environment only (no cgroup or quota exists).
Bounds are first-measurement ceilings proposed by Infra, not measurements. Exit status: 0 report written, 2 invalid,
3 refused or interrupted, 4 unbound.
"""
import argparse
import contextlib
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from xml.etree import ElementTree
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import alpha_source  # noqa: E402  (the reviewed copy plan and per-file copy)
import launcher3_lane  # noqa: E402  (dedicated capacity; no cloud operations)

MANIFEST = ROOT/'os/compile-contracts/launcher3-quickstep-lib.json'
# OS Product's reviewed contract version (schema 3). Re-pinned 2026-11-07 to the live trusted patcher:
# #440's delta is empty, so base equals final, and branches.otherwise is Product's wording on #662.
# A different manifest is refused until this pin is updated.
MANIFEST_SHA256 = '6d36629819b7c560b99506b01944d10c22d363b3db6870710ea98be26565c787'
CONDITIONS = ['base-exact', 'delta-preimages', 'isolated-apply', 'final-exact']
# The binding inputs each branch requires and forbids, in the contract's own vocabulary (branches.requires, forbids):
# check() binds and refuses exactly these, so the contract and this adapter cannot drift.
# The contract projects' own Git data: the copy excludes every .git and .repo (cuttlefish-alpha anywhereNames); the
# lunch inputs' admitted publicFetch of vendor/adevtool/.git is outside the three projects.
CHECKED_TREE_GIT = ('Git metadata or objects of the three contract projects in the checked tree (the lunch inputs\' '
                    'admitted gitRequirement.publicFetch of vendor/adevtool/.git, outside them, excepted)')
BRANCH_REQUIRES = {'verifyIntegrated': ['sourceTree', 'lunch', 'policyReceipt'],
                   'reconstruct': ['sourceTree', 'lunch', 'reconstructSource'],
                   'verifyBaseApplyCandidate': ['sourceTree', 'lunch', 'policyReceipt', 'bundleReport']}
BRANCH_FORBIDS = {'verifyIntegrated': ['bundleReport', 'reconstructSource', CHECKED_TREE_GIT],
                  'reconstruct': ['bundleReport'], 'verifyBaseApplyCandidate': ['reconstructSource']}
# The candidate branch's bundle half reads Infra's report in exactly this form (base.bundleReport, schema 3).
BUNDLE_REPORT = ('rose-integration-bundle-report', 1)
BUNDLE_REPORT_FIELDS = {'producer': ['path', 'sha256', 'roseRevision'],
                        'run': ['runId', 'runAttempt', 'headSha', 'label', 'createdAt', 'updatedAt', 'bindingSha256'],
                        'receipt': ['path',
                                    'receiptSha256',
                                    'receiptType',
                                    'label',
                                    'at',
                                    {'patcher': ['patcher',
                                                 {'sha256': ['apply-os-changes.py', 'background_apks.py', 'integration_copy.py']}]}],
                        'candidate': ['commit', 'onMain', 'frankelConfigSha256', 'applyArgs', 'patcherKind'],
                        'registration': ['source', 'patcherMatchesPin', 'applyArgsMatchPin'],
                        'observed': [{'patches': ['project', 'name', 'sha256']},
                                     {'overlays': ['target', 'sha256']},
                                     'notAnInputOverlays',
                                     'unclassified']}
PATCHER_FILES = ('apply-os-changes.py', 'background_apks.py', 'integration_copy.py')
# What this adapter reads each bundle-report field as: the contract names the fields, and their types are this adapter's
# reading of the names and of the association text. REPORT_ROWS are the {name: subspec} keys that hold a list of
# objects; every other such key holds one object. Times are UTC seconds as GitHub run metadata and host-reconcile.py
# write them, so their text order is their time order.
REPORT_ROWS = ('observed.patches', 'observed.overlays')
REPORT_TYPES = {'producer': {'path': 'repoPath', 'sha256': 'sha256', 'roseRevision': 'commit'},
                'run': {'runId': 'count', 'runAttempt': 'count', 'headSha': 'commit', 'label': 'label',
                        'createdAt': 'time', 'updatedAt': 'time', 'bindingSha256': 'sha256'},
                'receipt': {'path': 'hostPath', 'receiptSha256': 'sha256', 'receiptType': 'text', 'label': 'label',
                            'at': 'time', 'patcher': 'object'},
                'candidate': {'commit': 'commit', 'onMain': 'bool', 'frankelConfigSha256': 'sha256',
                              'applyArgs': 'texts', 'patcherKind': 'text'},
                'registration': {'source': 'text', 'patcherMatchesPin': 'bool', 'applyArgsMatchPin': 'bool'},
                'observed': {'patches': 'rows', 'overlays': 'rows', 'notAnInputOverlays': 'texts', 'unclassified': 'list'}}
ROW_TYPES = {'patches': {'project': 'text', 'name': 'text', 'sha256': 'sha256'},
             'overlays': {'target': 'text', 'sha256': 'sha256'}}
KIND_NAMES = {'bool': 'a boolean', 'text': 'a non-empty string', 'label': 'a label ([A-Za-z0-9][A-Za-z0-9._-]*)',
              'repoPath': 'a normalized repository path', 'hostPath': 'a normalized absolute path',
              'time': 'a UTC time YYYY-MM-DDTHH:MM:SSZ', 'sha256': 'a lowercase sha256', 'commit': 'a full lowercase commit id',
              'count': 'a positive integer', 'texts': 'a list of non-empty strings', 'list': 'a list',
              'object': 'an object', 'rows': 'a list of objects'}
REPORT_TIME = '%Y-%m-%dT%H:%M:%SZ'
# base.bundleReport.producer: "never the adapter, the observer or this checker". The adapter and this contract's checker
# are named here; Release's lunch-input observer has no committed path yet.
NEVER_PRODUCERS = ('scripts/launcher3-compile.py', 'scripts/launcher3-compile-contract.py')
# The association base-exact cannot check from the report itself; report.json lists it as taken on trust.
BUNDLE_TRUSTED = ('the run fields, entered by review from GitHub run metadata, and the recipe lines at headSha that make '
                  'the run commit both the candidate and the trusted checkout',
                  'headSha reachable from main (only candidate.onMain is read)',
                  'exactly one *-<label>.json of that label, and receipt.receiptSha256 (the receipt itself is not read)',
                  'the patcher bytes, applyArgs, os/patches and os/overlays at headSha, and candidate.frankelConfigSha256',
                  'registration.source and run.bindingSha256, for which the contract states no value',
                  'the producer at a reviewed commit (its path is only checked not to be the adapter or the checker)')
# base.registration.applyArgsSource: where check() reads frankel's committed applyArgs.
APPLY_ARGS_SOURCE = {'path': 'os/release-pipeline/frankel.json', 'key': 'host.integrate.reconcile.applyArgs'}
# The canonical sha256 (contract_text_digest: json, sorted keys, compact separators) of the contract text this adapter
# implements: every branch rule, requirement and definition, base.bundleReport whole, the registration's applyArgs
# source, derivation and contract-derived note, the delta's application and path convention, the flag evidence's role
# and acceptance, the overlay role rules and the unaffected, notInputs, provenance, proves, candidatePatches and
# outputsToBind statements. A re-pinned contract that changes any of it is invalid until this adapter is reviewed
# against the new text, so a re-pin is never only a hash bump.
CONTRACT_TEXT_SHA256 = '5cf3f74ae875db0f7ce7433ef864d9707d01e98374a476ce088a32dce1ac3bd6'
APPLY_STEPS = ['git -C <copy>/<project> apply --check -p<strip> <bundlePath>', 'git -C <copy>/<project> apply -p<strip> <bundlePath>']
BLOB_ARGV = ['git', 'hash-object', '-t', 'blob', '--no-filters', '--stdin']
LISTING_BYTES = 64 << 20
PIN_SECONDS = 30 * 60
LUNCH_SECONDS = 20 * 60
# Products whose generated vendor makefile includes adevtool-version-check.mk, which runs
# `git -C vendor/adevtool rev-parse HEAD` in lunch and m and $(error)s on a mismatch (OS Product's pinned trace, #410:
# adevtool 17df3b79 src/blobs/build.ts writeVersionCheckFile, src/build/make.ts). The copy excludes every .git and no
# genuine adevtool gitdir copy exists here, so such a lunch is refused before anything is copied: never a fabricated,
# redirected or relaxed check.
VERSION_CHECKED_PRODUCTS = ('frankel',)
# Such a lunch needs OS Product's lunch-input contract (#410/#459; public-fetch, publicFetch and workingTree added in
# #531, f16ba64; no-auto-maintenance in 1193274), read as data only and never through its script. A different file is
# refused until this pin is updated.
LUNCH_INPUTS = 'os/compile-contracts/frankel-lunch-inputs.json'
LUNCH_INPUTS_SHA256 = '03b58acffac8b7c7d1e4b67daaf64d545307b804825f6360d86f3175c5a1dd2c'
PINNED_MANIFEST = 'os/pinned-manifest.xml'
# The only way this adapter gives vendor/adevtool its .git: the pinned commit fetched from the pinned manifest's public
# source into the copy, admitted only when the pinned lunch-input contract lists the class. Infra's proposed first
# ceilings (2026-11-04), not measured requirements and not runtime approval.
ADEVTOOL_GIT_CLASSES = ('public-fetch',)
ADEVTOOL_FETCH_SECONDS, ADEVTOOL_GIT_BYTES = 30 * 60, 1 << 30
LUNCH_INPUT_SECONDS = 30 * 60  # hashing vendor/adevtool's listed paths and reading the markers, in the copy
GIT_PATH = '/usr/bin:/bin'
# A new repository from `git init --template=` has exactly these local keys; any other one refuses.
GIT_CONFIG_KEYS = ('core.repositoryformatversion', 'core.filemode', 'core.bare', 'core.logallrefupdates')
GIT_REDIRECTS = ('objects/info/alternates', 'objects/info/http-alternates', 'commondir', 'gitdir', 'hooks', 'info/grafts')
# publicFetch.acceptance forbids these maintenance/gc markers even when the fetch itself succeeded.
MAINTENANCE_MARKS = ('gc.pid', 'gc.log', 'objects/maintenance.lock')
# The git variables the contained lunch and m environment sets itself; nothing else named GIT_* reaches them.
CONTAINED_GIT = ('GIT_CEILING_DIRECTORIES', 'GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL')
# The pinned adevtool .gitignore rules the copy omits (gitRequirement.workingTree.omittedFromCopy), implemented exactly by
# adevtool_ignored; a contract naming any other list is refused until this adapter implements it.
ADEVTOOL_IGNORED = ('/.idea/', '/dist/', '!/dl/', '/dl/*', '!/dl/.find-ignore', '/lib/', '/node_modules/')
# The option rows the admission requires exactly (#531): the public-fetch status, and fabrication carved out for exactly
# the publicFetch steps, with redirect, fabrication and relaxation refused.
PUBLIC_FETCH_STATUS = ('allowed only under a separate runtime approval for its source and bounds; the interface is '
                       'publicFetch; Release owns the mechanism')
FABRICATION_MEANS = 'any HEAD, ref, object or gitdir written other than by a genuine copy or by exactly the publicFetch steps'
OPTION_CLASSES = ('genuine-copy', 'redirect', 'public-fetch', 'fabrication', 'relaxation')
# The canonical sha256 of everything this adapter implements of the git route: the whole gitRequirement and every
# source's workingTreeCheck text (json, sorted keys, compact separators). Any change to them is invalid until this
# adapter is reviewed against the new text, so a re-pin is never only a hash bump.
GIT_REQUIREMENT_SHA256 = 'd79dd327439c43359cf427d0d0ade4fec94d3dbec3000d9778c8425a43ea66cc'
# The job environment the fetch keeps (network setup only); everything else, LD_* included, is left out.
FETCH_INHERITED = ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy', 'NO_PROXY', 'no_proxy', 'ALL_PROXY',
                   'all_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'LANG')
# gitRequirement.publicFetch.environment as this adapter sets it (fetch_env); the contract must state exactly this.
FETCH_ENV_SPEC = {'unset': 'GIT_*', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                  'HOME': 'an attempt-owned empty directory (also XDG_CONFIG_HOME)', 'GIT_TERMINAL_PROMPT': '0',
                  'GIT_ASKPASS': '/bin/false', 'SSH_ASKPASS': '/bin/false', 'GIT_ALLOW_PROTOCOL': 'https',
                  'GIT_CEILING_DIRECTORIES': 'the copy root', 'PATH': '/usr/bin:/bin'}
FINISH_SECONDS = 30 * 60
UPLOAD = ('report.json', 'classification.json')
WORK_DIRS = ('tree', 'out', 'home', 'tmp')
BINDING = launcher3_lane.CONFIG
BINDING_KEYS = {'schemaVersion', 'kind', 'basis', 'lane', 'taskParent', 'branch', 'sourceTree', 'lunch',
                'policyReceipt', 'bundleReport', 'reconstructSource', 'adevtoolGit'}
BRANCHES = ('verifyIntegrated', 'verifyBaseApplyCandidate', 'reconstruct')
TASK_NAME = 'launcher3-compile-{run_id}-{attempt}'
TARGET = 'Launcher3QuickStepLib'
JOBS = 8
GIB = 1 << 30
# Infra's first-measurement ceilings (2026-11-03), not measured requirements.
SOURCE_BOUND_BYTES, RESERVE_BYTES = 150 * GIB, 300 * GIB
PREPARE_SECONDS, COMPILE_SECONDS = 120 * 60, 120 * 60
OUTPUT_STOP_BYTES, MEMORY_STOP_BYTES = 150 * GIB, 64 * GIB
SAMPLE_SECONDS, KILL_GRACE_SECONDS = 5, 30
# A running stage notices an interrupt this often, and a session being ended is rescanned and re-signalled this often
# (a job that made a new process group since the last scan is not missed).
POLL_SECONDS = 0.25
# After the outer timeout's TERM or a cancel: TERM, then KILL, each phase this long at most, so the stage's session is
# gone and the report written before the runner's next step. On a cancel the runner sends SIGINT to the step's entry
# process, SIGTERM 7.5 s later, and kills the process tree 2.5 s after that (docs.github.com, Actions reference,
# workflow cancellation); the outer timeout's own kill-after is 30 s. A job's timeout-minutes is not described there;
# the same budget serves it.
INTERRUPT_GRACE_SECONDS = 2.5
CANCEL_SIGTERM_SECONDS = 7.5
INTERRUPT_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT)
# finish waits this long for run's lock (receipts/run.lock, held for run's whole life), so it never removes a tree a
# live run still uses: the kill-after and its cleanup, with margin.
FINISH_LOCK_SECONDS = 60
RUN_LOCK, STAGE_RECORD = 'run.lock', 'stage.json'
# interrupts() state: a signal while a stage runs, or while the report is written (deferred), is held: the stage takes
# it at its next poll, once it can end its own session, and the report once it is whole.
_INTERRUPT = {'deferring': 0, 'pending': None, 'raised': False}
# The aconfig flags the report binds come from the contract's flagEvidence: each flag's own aconfig_declarations module,
# its expectedOutputDir (out/... relative to OUT_DIR's parent naming) and its exact expectedLine
# ("<package>.<name>:<permission>=<state:bool>", the format of the module's aconfig-flags.txt).
FLAG_TEXT, FLAG_CACHE = 'aconfig-flags.txt', 'aconfig-cache.pb'
# build/make tools/aconfig/aconfig_protos/protos/aconfig.proto at b0f697df: parsed_flags.parsed_flag = 1;
# parsed_flag: package = 1, name = 2, state = 6, permission = 7; ENABLED = 1, DISABLED = 2; READ_ONLY = 1, READ_WRITE = 2.
STATE, PERMISSION = {1: 'ENABLED', 2: 'DISABLED'}, {1: 'READ_ONLY', 2: 'READ_WRITE'}
PROOF_ENTRIES = 20000
CLASSPATH_SYMBOL = (b'AutomatedPackageListener', b'onAutomatedDevicesChanged')
REPORT_ERRORS = 40


class Invalid(Exception):
    pass


class Unbound(Exception):
    pass


class Refused(Exception):
    pass


class Interrupted(BaseException):
    """The outer timeout's TERM or a cancel reached run (interrupts); no except Exception swallows it. stages lists
    each stage it stopped and whether that stage's session was confirmed gone."""
    def __init__(self, name):
        super().__init__(name)
        self.stages = []


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def git_blob(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


TREE_MODES = ('100644', '100755', '120000')


def git_blob_id(data, run=subprocess.run):
    """A Git blob ID from bytes with Git's collision-detecting SHA-1, exactly the contract's blobHash.argv
    (`git hash-object -t blob --no-filters --stdin`, never -w), outside any repository and configuration."""
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'GIT_CEILING_DIRECTORIES': '/', 'HOME': '/nonexistent',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'LC_ALL': 'C'}
    result = run(BLOB_ARGV, input=data, capture_output=True, env=env, cwd='/')
    oid = result.stdout.strip()
    if result.returncode or not re.fullmatch(rb'[0-9a-f]{40}', oid):
        raise Refused('git hash-object did not give a sha1 blob ID')
    return oid.decode()


def read_nofollow(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        chunks = []
        while True:
            block = os.read(fd, 1 << 20)
            if not block:
                break
            chunks.append(block)
        return b''.join(chunks), before
    finally:
        os.close(fd)


def tree_records(project, skipped, hasher=git_blob_id, deadline=None, clock=time.monotonic):
    """{relative path bytes: (mode, oid)} for every entry actually present under project (lstat walk, no symlink
    followed), skipping exactly the given relative paths (a skipped directory skips its subtree). Regular files are
    100755 when the owner-execute bit is set, else 100644, over bytes read here with O_NOFOLLOW; symlinks are 120000
    over their readlink bytes. Directories contribute only their entries (Git does not track empty ones); any other
    file type refuses."""
    project = Path(project)
    base = os.fsencode(project)
    skipped = {os.fsencode(p) for p in skipped}
    records, stack = {}, [b'']
    while stack:
        directory = stack.pop()
        with os.scandir(base + (b'/' + directory if directory else b'')) as it:
            names = sorted(e.name for e in it)
        for name in names:
            if deadline is not None and clock() > deadline:
                raise Refused(f'pin_timeout: hashing {project.name} exceeded {PIN_SECONDS} s')
            rel = directory + b'/' + name if directory else name
            if rel in skipped:
                continue
            path = base + b'/' + rel
            info = os.lstat(path)
            if stat.S_ISDIR(info.st_mode):
                stack.append(rel)
            elif stat.S_ISLNK(info.st_mode):
                records[rel] = ('120000', hasher(os.readlink(path)))
            elif stat.S_ISREG(info.st_mode):
                data, opened = read_nofollow(path)
                if (opened.st_ino, opened.st_mode, opened.st_size) != (info.st_ino, info.st_mode, len(data)):
                    raise Refused(f'{project.name}/{os.fsdecode(rel)} changed while it was hashed')
                records[rel] = ('100755' if info.st_mode & stat.S_IXUSR else '100644', hasher(data))
            else:
                raise Refused(f'{project.name}/{os.fsdecode(rel)}: unsupported file type for the tree pin')
    return records


def tree_serialization(records):
    """The agreed M2 form: `<mode> SP <oid> TAB <path> LF` per entry, sorted by raw path bytes."""
    return b''.join(b'%s %s\t%s\n' % (records[p][0].encode(), records[p][1].encode(), p) for p in sorted(records))


def tree_digest(records):
    return sha256_bytes(tree_serialization(records)), len(records)


def verify_tree_pin(project, expected, sha256, count, skipped, hasher=git_blob_id, deadline=None):
    """Refuse unless the actual copied entries (minus exactly skipped) equal the pinned records: same digest, count,
    and per path mode and blob ID. expected: {path bytes: (mode, oid)} from the verified listing, used for the digest
    cross-check and to name the first difference. Proves the copy's bytes match pinned Git blob IDs, nothing more."""
    if tree_digest(expected) != (sha256, count):
        raise Invalid(f'the pinned listing for {Path(project).name} does not match its pinned digest')
    actual = tree_records(project, skipped, hasher, deadline)
    if tree_digest(actual) == (sha256, count):
        return {'sha256': sha256, 'count': count}
    for path in sorted(set(actual) | set(expected)):
        if actual.get(path) != expected.get(path):
            kind = 'extra' if path not in expected else 'missing' if path not in actual else 'different'
            raise Refused(f'{Path(project).name}/{os.fsdecode(path)}: {kind} against the tree pin')
    raise Refused(f'{Path(project).name}: tree pin digest differs')


def listing_raw(listing, root=ROOT):
    """The decompressed bytes of a committed gzip listing, after both of its sha256 bindings."""
    path = (Path(root)/listing['path']).resolve()
    if Path(root).resolve() not in path.parents or path.is_symlink():
        raise Invalid(f'{listing["path"]}: the listing must be a file in this repository')
    packed = path.read_bytes()
    if sha256_bytes(packed) != listing['compressedSha256']:
        raise Invalid(f'{listing["path"]} is not the pinned compressed listing')
    with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
        raw = stream.read(LISTING_BYTES + 1)
    if len(raw) > LISTING_BYTES or sha256_bytes(raw) != listing['sha256']:
        raise Invalid(f'{listing["path"]} is not the pinned listing')
    return raw


def load_listing(pin, root=ROOT):
    """{path bytes: (mode, oid)} of the pinned commit's tree minus exactly unlisted.excludedPaths, from the committed
    gzip listing after both of its sha256 bindings; raw `<mode> blob <oid> TAB <path> LF` lines only (no quoting, no
    other type or mode). This is the pin's expected data, never a statement about host bytes."""
    listing, unlisted = pin['listing'], pin['unlisted']
    raw = listing_raw(listing, root)
    records = {}
    for line in raw.split(b'\n')[:-1] if raw.endswith(b'\n') else [None]:
        match = line is not None and re.fullmatch(rb'(100644|100755|120000) blob ([0-9a-f]{40})\t([^"\n][^\n]*)', line)
        if not match or match.group(3) in records or match.group(1).decode() not in pin['modes']:
            raise Invalid(f'{listing["path"]}: a line is not a raw unquoted blob entry of a pinned mode')
        records[match.group(3)] = (match.group(1).decode(), match.group(2).decode())
    if len(records) != listing['paths']:
        raise Invalid(f'{listing["path"]}: {len(records)} entries, the pin says {listing["paths"]}')
    for excluded in unlisted['excludedPaths']:
        records.pop(os.fsencode(excluded), None)
    return records


def tree_pins(tree, projects, root=ROOT, deadline=None):
    """verify_tree_pin for every project of a contract section, on the isolated copy."""
    return {project['path']: verify_tree_pin(Path(tree)/project['path'], load_listing(project['treePin'], root),
                                             project['treePin']['unlisted']['digest'],
                                             project['treePin']['unlisted']['count'],
                                             project['treePin']['unlisted']['excludedPaths'], deadline=deadline)
            for project in projects}


def sha256_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return None
    return sha256_bytes(path.read_bytes())


def image_is(path, image):
    """The file is exactly this {sha256, gitBlob} image (None: the file is absent)."""
    if image is None:
        return not (Path(path).exists() or Path(path).is_symlink())
    data = read_bytes(path)
    return data is not None and sha256_bytes(data) == image['sha256'] and git_blob(data) == image['gitBlob']


def delta_preconditions(tree, files, overlays):
    """verifyBaseApplyCandidate's delta-preimages: every delta file at its before image and every overlay target at
    its before image, or absent where before is null. Anything else (already partly or wholly applied, unknown) refuses;
    nothing is applied twice."""
    for item in files:
        if not image_is(Path(tree)/item['path'], item['before']):
            raise Refused(f'delta-preimages: {item["path"]} is not at its base image: already applied or unknown')
    for item in overlays:
        target = Path(tree)/item['target']
        present = target.exists() or target.is_symlink()
        if (item['before'] is None and present) or (item['before'] is not None and sha256_file(target) != item['before']):
            raise Refused(f'delta-preimages: {item["target"]} is not at its base overlay image: already applied or unknown')


def apply_candidate(tree, patches, overlays, bundle, files, git='git', run=subprocess.run, applied=None):
    """Apply the digest-bound candidate delta to the isolated copy only. Nothing is written until every patch and
    overlay source has its bound sha256 and every patch passes `git -C <copy>/<project> apply --check -p<strip>
    <bundlePath>` on the unchanged copy: the contract's application note, any failure of --check refuses before anything
    is applied (so a patch that needed an earlier one would refuse; the delta's patches touch disjoint files). Then each
    patch is applied with the same step without --check (no other option: no --3way, --reject, --recount, reduced
    context or whitespace handling), the patched files' copied modes are restored (git apply rewrites them under the
    umask; a delta file with no before image is new and has none), and each overlay is installed from its checked
    bytes. applied receives, in order, each patch name and overlay target as its write starts, so a refusal part-way
    says what the copy may hold. The caller checks the final images."""
    applied = [] if applied is None else applied
    tree, bundle = Path(tree), Path(bundle)
    modes = {item['path']: stat.S_IMODE(os.lstat(tree/item['path']).st_mode) for item in files if item['before'] is not None}
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'GIT_CEILING_DIRECTORIES': str(tree), 'HOME': '/nonexistent',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'LC_ALL': 'C'}
    for patch in patches:
        if sha256_file(bundle/patch['bundlePath']) != patch['sha256']:
            raise Refused(f'isolated-apply: {patch["bundlePath"]} is not the bound candidate patch; nothing was applied')
    contents = []
    for item in overlays:
        data = read_bytes(bundle/item['bundlePath'])
        if data is None or sha256_bytes(data) != item['after']:
            raise Refused(f'isolated-apply: {item["bundlePath"]} is not the bound candidate overlay; nothing was applied')
        contents.append(data)

    def step(patch, *check):
        argv = [git, '-C', str(tree/patch['project']), 'apply', *check, f'-p{patch["strip"]}', str(bundle/patch['bundlePath'])]
        return run(argv, capture_output=True, env=env).returncode == 0
    for patch in patches:
        if not step(patch, '--check'):
            raise Refused(f'isolated-apply: git apply --check of {patch["bundlePath"]} failed in {patch["project"]}; '
                          'nothing was applied')
    for patch in patches:
        applied.append(patch['name'])
        if not step(patch):
            raise Refused(f'isolated-apply: git apply of {patch["bundlePath"]} failed in {patch["project"]} after every '
                          f'--check passed; started in the copy: {", ".join(applied)}')
    for path, mode in modes.items():
        if stat.S_IMODE(os.lstat(tree/path).st_mode) != mode:
            os.chmod(tree/path, mode)
    for item, data in zip(overlays, contents):
        source, target = bundle/item['bundlePath'], tree/item['target']
        applied.append(item['target'])
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f'.{target.name}.rose-candidate')
        fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
        finally:
            os.close(fd)
        os.chmod(staging, stat.S_IMODE(os.stat(source).st_mode))
        os.replace(staging, target)


CONFIG_CERT = re.compile(r'[0-9A-F]{64}')
CONFIG_PACKAGE = re.compile(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+')
CONFIG_KINDS = {'array': 'string-array', 'string': 'string', 'bool': 'bool'}
CONFIG_ROWS = ('agent', 'targets')
CONFIG_SCALARS = ('referenceDisplayAddress', 'lockedControl')


CONFIG_ARRAYS = {  # element: (short name, rows, when, OS Product's stated meaning, bound verbatim)
    'config_pixelComputerControlAgents': ('Agents', 'agent', None, 'Agents: the agent row'),
    'config_pixelComputerControlTargets': ('Targets', 'targets', None, 'Targets: every target row, in receipt order'),
    'config_pixelComputerControlPrototypeAgents': ('PrototypeAgents', 'agent', 'prototypeControl',
        'PrototypeAgents: the agent row, only when prototypeControl is true; otherwise the marker'),
}
CONFIG_SCALAR_RULES = {  # element: (kind, input, placeholder in OS Product's text, suffix bound verbatim)
    'config_pixelComputerControlReferenceDisplayAddress': ('string', 'referenceDisplayAddress', '{address}', ' when given; otherwise the marker'),
    'config_pixelComputerControlLockedSessions': ('bool', 'lockedControl', None, ' when lockedControl; otherwise the marker'),
}
CONFIG_INPUT_RULES = {'packageName': CONFIG_PACKAGE.pattern, 'certificate': CONFIG_CERT.pattern,
                      'rows': 'every agent and target row has a packageName and at least one certificate'}


def config_rules(entry):
    """B-normalised rules for config.xml from OS Product's integrationRewrite data (markers, templates, inputRules).
    B never loads OS Product's checker at run time. Every semantic string is bound: the element list, each element's
    meaning and the input rules must be exactly what this parser implements, or the contract is Invalid (a changed
    rule needs a reviewed change here, never a silent reinterpretation)."""
    rewrite = entry.get('integrationRewrite')
    if not isinstance(rewrite, dict):
        raise Invalid('config.xml has no integration rewrite rules in the contract')
    names = list(CONFIG_ARRAYS) + list(CONFIG_SCALAR_RULES)
    templates = rewrite.get('templates') or {}
    if rewrite.get('elements') != names or set(rewrite.get('markers') or {}) != set(names) \
            or rewrite.get('inputRules') != CONFIG_INPUT_RULES \
            or set(templates) != {'array', 'entry', 'arrays', *CONFIG_SCALAR_RULES} \
            or templates['arrays'] != {n: meaning for n, (_, _, _, meaning) in CONFIG_ARRAYS.items()}:
        raise Invalid('config.xml rewrite rules are not the ones this parser implements')
    array, item = templates['array'], templates['entry']
    if not (isinstance(array, str) and array.count('{entries}') == 1 and array.count('{name}') == 1
            and isinstance(item, str) and item.count('{packageName}') == 1 and item.count('{certificate}') == 1):
        raise Invalid('config.xml array templates are malformed')
    head, tail = array.split('{entries}')
    rules = []
    for name, (short, rows, when, _) in CONFIG_ARRAYS.items():
        rules.append({'name': name, 'kind': 'array', 'marker': rewrite['markers'][name], 'rows': rows, 'when': when,
                      'open': head.replace('{name}', short), 'item': item.replace('{packageName}', '{package}'),
                      'close': tail.replace('{name}', short)})
    for name, (kind, input_, placeholder, suffix) in CONFIG_SCALAR_RULES.items():
        text = templates[name]
        if not isinstance(text, str) or not text.endswith(suffix):
            raise Invalid(f'{name}: the template is not the one this parser implements')
        template = text[:-len(suffix)]
        if placeholder is not None:
            if template.count(placeholder) != 1:
                raise Invalid(f'{name}: the template has no single {placeholder}')
            template = template.replace(placeholder, '{value}')
        rules.append({'name': name, 'kind': kind, 'marker': rewrite['markers'][name], 'input': input_, 'template': template})
    return check_rules(rules)


def check_rules(rows):
    """Validate B-normalised rules. One row per element: name, kind, marker, and for an array rows (agent|targets),
    when (None|a policy flag), open, item ({package}, {certificate}), close; for a string input referenceDisplayAddress
    and template ({value}); for a bool input lockedControl and template (the full true element). Malformed is Invalid."""
    if not isinstance(rows, list) or not rows:
        raise Invalid('config.xml has no integration rewrite rules in the contract')
    names = set()
    for row in rows:
        if not isinstance(row, dict) or row.get('kind') not in CONFIG_KINDS or not isinstance(row.get('name'), str) \
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', row['name']) or row['name'] in names:
            raise Invalid(f'config.xml rewrite rule {row!r} is malformed or repeated')
        names.add(row['name'])
        marker = row.get('marker')
        if not isinstance(marker, str) or f'<{CONFIG_KINDS[row["kind"]]} name="{row["name"]}"' not in marker:
            raise Invalid(f'{row["name"]}: the marker is not that element')
        if row['kind'] == 'array':
            keys = {'name', 'kind', 'marker', 'rows', 'when', 'open', 'item', 'close'}
            valid = row.get('rows') in CONFIG_ROWS and (row.get('when') is None or isinstance(row.get('when'), str)) \
                and all(isinstance(row.get(k), str) for k in ('open', 'item', 'close')) \
                and '{package}' in row['item'] and '{certificate}' in row['item']
        else:
            keys = {'name', 'kind', 'marker', 'input', 'template'}
            valid = isinstance(row.get('template'), str) and (
                (row['kind'] == 'string' and row.get('input') == 'referenceDisplayAddress' and '{value}' in row['template'])
                or (row['kind'] == 'bool' and row.get('input') == 'lockedControl' and '{' not in row['template']))
        opening = f'<{CONFIG_KINDS[row["kind"]]} name="{row["name"]}"'
        text = row.get('open') if row['kind'] == 'array' else row.get('template')
        if set(row) != keys or not valid or not text.startswith('    ' + opening) or text.count(opening) != 1:
            raise Invalid(f'{row["name"]}: the rewrite rule is not a complete {row["kind"]} rule for that element')
    return rows


def expected_config(rules, policy, reference=None, locked=False):
    """{element name: exact text} that integration writes from these bound inputs; an element whose input is absent
    stays at its marker. Refuses policy rows that are not a package with uppercase SHA-256 certificates."""
    agent, targets = policy['agent'], list(policy['targets'])
    for row in [agent] + targets:
        if not isinstance(row.get('packageName'), str) or not CONFIG_PACKAGE.fullmatch(row['packageName']) \
                or not row.get('certificates') or not all(isinstance(c, str) and CONFIG_CERT.fullmatch(c) for c in row['certificates']):
            raise ValueError(f'policy row {row.get("packageName")!r}: not a package with uppercase SHA-256 certificates')
    if reference is not None and not (str(reference).isdecimal() and 0 < int(reference) < 2**63):
        raise ValueError('reference display address: not a positive signed-64-bit number')
    out = {}
    for rule in rules:
        if rule['kind'] == 'array':
            if rule['when'] is not None and not policy.get(rule['when'], False):
                out[rule['name']] = rule['marker']
                continue
            items = [agent] if rule['rows'] == 'agent' else targets
            out[rule['name']] = rule['open'] + ''.join(rule['item'].replace('{package}', r['packageName']).replace(
                '{certificate}', c) for r in items for c in r['certificates']) + rule['close']
        elif rule['input'] == 'referenceDisplayAddress':
            out[rule['name']] = rule['marker'] if reference is None else rule['template'].replace('{value}', str(reference))
        else:
            out[rule['name']] = rule['template'] if locked else rule['marker']
    return out


def verify_config(integrated, post_sha256, rules, policy, policy_sha256, bound_policy_sha256, reference=None, locked=False):
    """Errors (empty when accepted) for an integrated config.xml: only with the policy receipt bound for this tree, each
    rule's element present exactly once and exactly as integration writes it from the bound inputs, and the text with
    exactly those elements put back to their markers equal to the post-image. Nothing else is normalised."""
    if not bound_policy_sha256 or policy is None or policy_sha256 != bound_policy_sha256:
        return ['the policy receipt is missing or not the one bound for this tree']
    try:
        expected = expected_config(rules, policy, reference, locked)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        return [f'policy inputs refused: {error}']
    try:
        text = integrated.decode('utf-8')
    except UnicodeDecodeError:
        return ['config.xml is not UTF-8']
    errors = []
    for rule in rules:
        name, wanted = rule['name'], expected[rule['name']]
        openings = re.findall(r'<(?:string-array|string|bool) name="%s"' % re.escape(name), text)
        if len(openings) != 1:
            errors.append(f'{name}: {len(openings)} elements, expected exactly one')
        elif text.count(wanted) != 1:
            errors.append(f'{name}: not the exact text integration writes from the bound inputs')
        else:
            text = text.replace(wanted, rule['marker'])
    if not errors and sha256_bytes(text.encode('utf-8')) != post_sha256:
        errors.append('config.xml differs from the post-image outside the policy elements')
    return errors


def contract_text_digest(manifest):
    """CONTRACT_TEXT_SHA256's canonical form, of this contract."""
    base, delta, flags = manifest['base'], manifest['candidateDelta'], manifest['flagEvidence']
    text = {'branches': manifest['branches'], 'bundleReport': base['bundleReport'],
            'registration': {k: base['registration'][k] for k in ('applyArgsSource', 'derivation', 'contractDerived')},
            'application': delta['application'], 'pathConvention': delta['pathConvention'],
            'flagEvidence': {'role': flags['role'],
                             'flags': [{k: f[k] for k in ('flag', 'expectedLine', 'produce', 'accept')} for f in flags['flags']]},
            'overlayRoles': {role: manifest['overlayRoles'][role]['rule'] for role in ('required', 'notAnInput')},
            **{k: manifest[k] for k in ('unaffected', 'notInputs', 'provenance', 'proves', 'candidatePatches', 'outputsToBind')}}
    return sha256_bytes(json.dumps(text, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())


def contract_shape(manifest):
    """The parts of the v3 contract this adapter implements, bound exactly: the four verifyBaseApplyCandidate condition
    IDs, the application steps, the blob hash invocation, sha1 objects and the pinned modes (a subset of the three), the
    config.xml rules, a candidate bundle that lists exactly the delta's patch and overlay sources, lunchInputs at this
    adapter's pin, branches.requires and forbids, base.bundleReport's kind, version and fields, the registration's
    trusted patcher pins, patch order and applyArgs source; and every text this adapter implements, by its digest
    (contract_text_digest, CONTRACT_TEXT_SHA256)."""
    vbac = manifest['branches']['verifyBaseApplyCandidate']
    if [c['id'] for c in vbac['conditions']] != CONDITIONS:
        raise Invalid('verifyBaseApplyCandidate conditions are not exactly ' + ', '.join(CONDITIONS))
    delta = manifest['candidateDelta']
    if delta['application']['steps'] != APPLY_STEPS:
        raise Invalid('candidateDelta.application.steps are not the two steps this adapter runs')
    for section in (manifest['projects'], manifest['base']['projects']):
        for project in section:
            pin = project['treePin']
            if (pin['objectFormat'], pin['blobHash']['argv']) != ('sha1', BLOB_ARGV) or not pin['modes'] \
                    or sorted(set(pin['modes'])) != pin['modes'] or not set(pin['modes']) <= set(TREE_MODES):
                raise Invalid(f'{project["path"]}: the tree pin is not sha1 with the agreed hash-object and modes')
            config = [f for f in project['files'] if 'integrationRewrite' in f]
            if project['path'] == 'frameworks/base' and [f['path'] for f in config] != ['core/res/res/values/config.xml']:
                raise Invalid('frameworks/base must rewrite exactly config.xml')
            for entry in config:
                config_rules(entry)
    if manifest.get('lunchInputs') != {'path': LUNCH_INPUTS, 'sha256': LUNCH_INPUTS_SHA256}:
        raise Invalid('lunchInputs must name exactly the lunch-input contract this adapter pins')
    branches = manifest['branches']
    if (branches.get('requires'), branches.get('forbids')) != (BRANCH_REQUIRES, BRANCH_FORBIDS):
        raise Invalid('branches.requires and forbids are not exactly the inputs this adapter binds and refuses')
    base = manifest['base']
    report, registration = base['bundleReport'], base['registration']
    if (report.get('kind'), report.get('schemaVersion'), report.get('fields')) != (*BUNDLE_REPORT, BUNDLE_REPORT_FIELDS):
        raise Invalid('base.bundleReport is not the rose-integration-bundle-report v1 this adapter reads')
    patcher = registration['patcher']
    if patcher.get('patcher') != 'trusted' or sorted(patcher.get('sha256') or {}) != sorted(PATCHER_FILES) \
            or not all(re.fullmatch(r'[0-9a-f]{64}', str(v)) for v in patcher['sha256'].values()):
        raise Invalid('base.registration.patcher must pin the trusted patcher\'s three files by sha256')
    order = [(o['project'], o['name']) for o in registration['order']]
    content = [(p['project'], p['name']) for p in base['bundleContent']['patches']]
    if sorted(order) != sorted(content) or len(set(order)) != len(order) or \
            any([n for p, n in order if p == project] != [n for p, n in content if p == project] for project, _ in order):
        raise Invalid('base.registration.order must hold exactly the bundle patches, in each project\'s bundle order')
    sources = {p['bundlePath']: p['sha256'] for p in delta['patches']}
    sources.update({o['bundlePath']: o['after'] for o in delta['overlays']})
    if {b['path']: b['sha256'] for b in delta['bundle']} != sources or len(delta['bundle']) != len(sources):
        raise Invalid('candidateDelta.bundle must list exactly the delta patch and overlay sources with their sha256')
    for patch in delta['patches']:
        if not isinstance(patch['strip'], int) or isinstance(patch['strip'], bool) or not 0 <= patch['strip'] <= 9:
            raise Invalid(f'{patch["name"]}: strip must be a small integer')
        if patch['project'] not in {p['path'] for p in manifest['projects']}:
            raise Invalid(f'{patch["name"]}: the delta patch project is not a contract project')
    if registration.get('applyArgsSource') != APPLY_ARGS_SOURCE:
        raise Invalid('base.registration.applyArgsSource is not the frankel applyArgs this adapter reads')
    if contract_text_digest(manifest) != CONTRACT_TEXT_SHA256:
        raise Invalid('the contract\'s rules, definitions and bundle-report statement are not the text this adapter was '
                      'reviewed against (CONTRACT_TEXT_SHA256)')


def load_manifest(path=MANIFEST, pinned=MANIFEST_SHA256):
    data = Path(path).read_bytes()
    if sha256_bytes(data) != pinned:
        raise Invalid(f'{Path(path).name} is not the reviewed contract version {pinned[:12]}')
    manifest = json.loads(data)
    if (manifest.get('schemaVersion'), manifest.get('kind'), manifest.get('target')) != (3, 'rose-launcher3-compile-inputs', TARGET):
        raise Invalid('the contract is not a rose-launcher3-compile-inputs v3 for Launcher3QuickStepLib')
    contract_shape(manifest)
    flags = (manifest.get('flagEvidence') or {}).get('flags') or []
    if {f.get('flag') for f in flags} != {'android.security.content_uri_permission_apis',
                                          'com.android.launcher3.enable_fallback_overview_in_window'}:
        raise Invalid('the contract must bind exactly the identity and fallback flags in flagEvidence')
    return manifest


def frankel_policy_args(root=ROOT):
    """(reference display address, locked control) exactly as frankel's committed applyArgs give them."""
    config = json.loads((root/'os/release-pipeline/frankel.json').read_text())
    args = config['host']['integrate']['reconcile']['applyArgs']
    if '--background-policy' not in args:
        raise Invalid('frankel applyArgs do not pass --background-policy: the contract post-images need it')
    reference = args[args.index('--reference-display-address') + 1] if '--reference-display-address' in args else None
    return reference, '--locked-control' in args


def lock_path(binding):
    return binding['lane']['lockPath']


def manifest_project(path, root=ROOT):
    """{'revision', 'url'} of one project of the pinned manifest: its remote's fetch prefix, which must be https, plus
    its name."""
    manifest = ElementTree.parse(Path(root)/PINNED_MANIFEST).getroot()
    remotes = {r.get('name'): r.get('fetch') for r in manifest.iter('remote')}
    default = manifest.find('default')
    rows = [p for p in manifest.iter('project') if p.get('path') == path]
    if len(rows) != 1:
        raise Invalid(f'{PINNED_MANIFEST} must name exactly one {path} project')
    remote = rows[0].get('remote') or (default.get('remote') if default is not None else None)
    prefix = remotes.get(remote) or ''
    if not prefix.startswith('https://') or not re.fullmatch(r'[0-9a-f]{40}', rows[0].get('revision') or ''):
        raise Invalid(f'{path} must be pinned to a full commit on an https remote')
    return {'revision': rows[0].get('revision'), 'url': prefix.rstrip('/') + '/' + rows[0].get('name')}


def load_lunch_inputs(path=None, pinned=LUNCH_INPUTS_SHA256, root=ROOT):
    """OS Product's lunch-input contract as data after its sha256 pin, with the parts this adapter implements bound
    exactly: the adevtool identity agrees with its own source row and with the pinned manifest."""
    data = Path(path or Path(root)/LUNCH_INPUTS).read_bytes()
    if sha256_bytes(data) != pinned:
        raise Invalid(f'frankel-lunch-inputs.json is not the reviewed lunch-input contract {pinned[:12]}')
    inputs = json.loads(data)
    if (inputs.get('schemaVersion'), inputs.get('kind')) != (1, 'rose-frankel-lunch-inputs'):
        raise Invalid('the lunch-input contract is not a rose-frankel-lunch-inputs v1')
    git = inputs['gitRequirement']
    rows = [s for s in inputs['sources'] if s.get('path') == git['project']]
    if (git['project'], git['path']) != ('vendor/adevtool', 'vendor/adevtool/.git') or len(rows) != 1 \
            or (rows[0]['revision'], rows[0]['rootTree']) != (git['revision'], git['rootTree']) \
            or not all(re.fullmatch(r'[0-9a-f]{40}', str(git[k])) for k in ('revision', 'rootTree')):
        raise Invalid('gitRequirement must name vendor/adevtool/.git at full ids agreeing with its own source row')
    if manifest_project(git['project'], root)['revision'] != git['revision']:
        raise Invalid('gitRequirement.revision is not the pinned manifest revision of vendor/adevtool')
    unset = inputs['environment']['unset']
    if 'GIT_*' not in unset or not all(n == 'GIT_*' or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', str(n)) for n in unset):
        raise Invalid('the lunch-input environment must unset GIT_* and otherwise name plain variables only')
    for row in inputs['manifestFiles']:
        if row.get('kind') not in ('linkfile', 'copyfile'):
            raise Invalid(f'manifestFiles: {row.get("kind")!r} is neither linkfile nor copyfile')
    return inputs


def public_fetch_option(inputs, url):
    """OS Product's admission of the public fetch: exactly one gitRequirement.options row of class public-fetch with an
    allowed status and this url (the pinned manifest's source), or None. The pinned contract (#531) admits it; a run
    still needs the binding's adevtoolGit, which Infra binds with the fetch's own runtime approval."""
    rows = [o for o in inputs['gitRequirement']['options'] if o.get('class') == 'public-fetch']
    if len(rows) == 1 and str(rows[0].get('status', '')).startswith('allowed') and rows[0].get('url') == url:
        return rows[0]
    return None


def lunch_plan(binding, root=ROOT, inputs_path=None, inputs_pin=LUNCH_INPUTS_SHA256):
    """{'inputs', 'url', 'exact'} for the contract's only lunch (definitions.lunch: the lunch inputs in lunchInputs,
    satisfied on the copy), the lunch-input contract's target, once that contract is pinned, the binding names the
    public fetch, the contract admits it and its publicFetch and workingTree are exactly this adapter's mechanism.
    Invalid for any other lunch or when the contract and this adapter disagree; Unbound (exit 4) until then."""
    inputs = load_lunch_inputs(inputs_path, inputs_pin, root)
    target = inputs['lunch']['target']
    if binding['lunch'] != target:
        raise Invalid(f'the lunch must be the lunch-input contract target {target} (definitions.lunch: the lunch inputs '
                      'in lunchInputs, satisfied on the copy)')
    if not version_checked(target):
        raise Invalid(f'{target} does not run adevtool-version-check.mk, the only lunch this adapter implements')
    if binding['adevtoolGit'] is None:
        raise Unbound(f'adevtool_git_missing: {binding["lunch"]} runs adevtool-version-check.mk (git -C vendor/adevtool '
                      'rev-parse HEAD) in lunch and m; the copy has no .git and adevtoolGit (public-fetch) is not bound')
    url = manifest_project(inputs['gitRequirement']['project'], root)['url']
    if public_fetch_option(inputs, url) is None:
        raise Unbound(f'adevtool_git_unadmitted: the pinned frankel-lunch-inputs.json ({inputs_pin[:12]}) admits no '
                      f'public-fetch class for {url}')
    problems = admission_problems(inputs, url, root)
    if problems:
        raise Invalid('the admitted public fetch is not the mechanism this adapter implements: ' + '; '.join(problems[:5]))
    return {'inputs': inputs, 'url': url, 'exact': exact_sources(inputs)}


def lunch_listing(inputs, project, root=ROOT, raw=False):
    """{path bytes: (mode, oid)} of one lunch-input source's pinned tree listing, after both of its sha256 bindings
    (raw: its decompressed bytes instead)."""
    rows = [s for s in inputs['sources'] if s.get('path') == project]
    if len(rows) != 1:
        raise Invalid(f'the lunch-input contract has no single {project} source')
    if raw:
        return listing_raw(rows[0]['listing'], root)
    return load_listing({'listing': rows[0]['listing'], 'unlisted': {'excludedPaths': []}, 'modes': list(TREE_MODES)}, root)


def fetch_steps(work, url, revision):
    """gitRequirement.publicFetch.steps, which are Blazer's git_meta_argv: a new .git with no templates (no hooks),
    exactly revision at depth 1 with no tags or auto maintenance (which can detach beyond the stage's session), and a
    detached HEAD by update-ref. Git without the maintenance flag refuses; none reads or writes the working tree."""
    return [['git', 'init', '-q', '--template=', str(work)],
            ['git', '-C', str(work), 'fetch', '--quiet', '--depth=1', '--no-tags', '--no-auto-maintenance', url, revision],
            ['git', '-C', str(work), 'update-ref', '--no-deref', 'HEAD', revision]]


def adevtool_ignored(relative, is_dir):
    """git check-ignore of exactly ADEVTOOL_IGNORED, for a path relative to vendor/adevtool: the top-level directories
    .idea, dist, lib and node_modules and everything below them, and every entry directly in dl except dl/.find-ignore
    and everything below those entries."""
    parts = relative.split('/')
    if parts[0] in ('.idea', 'dist', 'lib', 'node_modules') and (len(parts) > 1 or is_dir):
        return True
    return parts[0] == 'dl' and len(parts) > 1 and parts[1] != '.find-ignore'


def exact_sources(inputs):
    """The lunch-input sources whose working tree this adapter checks exactly: workingTreeCheck 'exact...' (vendor/adevtool
    through gitRequirement.workingTree); 'derivation only...' are not checked; anything else is invalid."""
    exact = []
    for row in inputs['sources']:
        check = str(row.get('workingTreeCheck', ''))
        if check.startswith('exact'):
            exact.append(row['path'])
        elif not check.startswith('derivation only'):
            raise Invalid(f'{row.get("path")}: workingTreeCheck must say exact or derivation only')
    if inputs['gitRequirement']['project'] not in exact:
        raise Invalid('vendor/adevtool must be checked exactly')
    return exact


def git_requirement_digest(inputs):
    """GIT_REQUIREMENT_SHA256's canonical form, of this contract."""
    semantics = {'gitRequirement': inputs['gitRequirement'],
                 'workingTreeCheck': {s.get('path'): s.get('workingTreeCheck') for s in inputs['sources']}}
    return sha256_bytes(json.dumps(semantics, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())


def admission_problems(inputs, url, root=ROOT):
    """Every way the admitted contract's publicFetch and workingTree differ from the mechanism this adapter implements;
    an admission is refused (invalid) on any. The contract cannot widen the adapter, and the adapter cannot drift."""
    git, problems = inputs['gitRequirement'], []
    fetch, tree = git.get('publicFetch') or {}, git.get('workingTree') or {}
    revision = git['revision']
    if git_requirement_digest(inputs) != GIT_REQUIREMENT_SHA256:
        problems.append('gitRequirement or a workingTreeCheck is not the text this adapter was reviewed against')
    options = {o.get('class'): o for o in git['options']}
    if [o.get('class') for o in git['options']] != list(OPTION_CLASSES):
        problems.append('gitRequirement.options are not exactly ' + ', '.join(OPTION_CLASSES))
    elif (options['public-fetch'].get('status'), options['fabrication'].get('means')) != (PUBLIC_FETCH_STATUS,
                                                                                            FABRICATION_MEANS) \
            or any(options[c].get('status') != 'refused' for c in ('redirect', 'fabrication', 'relaxation')):
        problems.append('the public-fetch status or the fabrication carve-out is not exactly the one this adapter '
                        'implements, or redirect, fabrication or relaxation is not refused')
    rendered = [' '.join(step) for step in fetch_steps('<copy>/vendor/adevtool', '<url>', revision)]
    if (fetch.get('url'), fetch.get('protocol')) != (url, 'https only'):
        problems.append('publicFetch.url and protocol are not the pinned manifest source over https only')
    if fetch.get('steps') != rendered:
        problems.append('publicFetch.steps are not exactly this adapter\'s three commands')
    if fetch.get('environment') != FETCH_ENV_SPEC:
        problems.append('publicFetch.environment is not exactly this adapter\'s fetch environment')
    ceiling = fetch.get('ceiling') or {}
    if (ceiling.get('seconds'), ceiling.get('bytes')) != (ADEVTOOL_FETCH_SECONDS, ADEVTOOL_GIT_BYTES):
        problems.append('publicFetch.ceiling is not this adapter\'s 1800 s and 1073741824 bytes')
    omitted = (tree.get('omittedFromCopy') or {}).get('gitignore')
    if (tree.get('project'), omitted, tree.get('untrackedAllowed')) != (git['project'], list(ADEVTOOL_IGNORED), []):
        problems.append('workingTree is not vendor/adevtool with exactly the omitted rules this adapter implements and '
                        'no untracked allowance')
    try:
        exact = exact_sources(inputs)
        listed = lunch_listing(inputs, git['project'], root)
        for name in listed:
            parts = os.fsdecode(name).split('/')
            if any(adevtool_ignored('/'.join(parts[:i]), i < len(parts)) for i in range(1, len(parts) + 1)):
                problems.append(f'the omitted rules would drop the tracked {os.fsdecode(name)}')
                break
        ignore = listed.get(b'.gitignore')
        if ignore is None or ignore[1] != (tree.get('omittedFromCopy') or {}).get('gitBlob'):
            problems.append('workingTree.omittedFromCopy.gitBlob is not the pinned tree\'s .gitignore')
        for project in exact:
            lunch_listing(inputs, project, root)
            row = next(s for s in inputs['sources'] if s['path'] == project)
            if manifest_project(project, root)['revision'] != row['revision']:
                problems.append(f'{project} is not at its pinned manifest revision')
    except Invalid as error:
        problems.append(str(error))
    return problems


def contained_bytes(tree, relative):
    """The bytes of a regular file at tree/relative reached through no link, or None."""
    path = Path(tree)
    for part in Path(relative).parts:
        path = path/part
        if path.is_symlink():
            return None
    if not path.is_file():
        return None
    data, opened = read_nofollow(path)
    return data if opened.st_size == len(data) else None


def manifest_file_problem(tree, row):
    """A repo linkfile is a link that resolves, inside the copy, to its project's source; a copyfile has its bytes."""
    tree = Path(tree)
    dest, source = tree/row['dest'], os.path.normpath(tree/row['project']/row['src'])
    if row['kind'] == 'linkfile':
        if not dest.is_symlink():
            return f'{row["dest"]} is not the manifest link to {row["project"]}/{row["src"]}'
        target = os.path.normpath(dest.parent/os.readlink(dest))
        if target != source or os.path.commonpath([str(tree), target]) != str(tree):
            return f'{row["dest"]} does not link to {row["project"]}/{row["src"]} inside the copy'
        if not os.path.exists(target):
            return f'{row["dest"]} dangles: {row["project"]}/{row["src"]} is missing in the copy'
        return None
    data = contained_bytes(tree, row['dest'])
    if data is None or data != contained_bytes(tree, f'{row["project"]}/{row["src"]}'):
        return f'{row["dest"]} is not the manifest copy of {row["project"]}/{row["src"]}'
    return None


def read_walked(path, info, label):
    """The bytes of the regular file the walk saw (info): opened without following a link and without blocking, and
    read only when the descriptor is that same regular file (inode, mode and size); the same again after reading."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        same = (opened.st_ino, opened.st_mode, opened.st_size) == (info.st_ino, info.st_mode, info.st_size)
        if not stat.S_ISREG(opened.st_mode) or not same:
            raise Refused(f'{label} changed while it was hashed')
        chunks = []
        while True:
            block = os.read(fd, 1 << 20)
            if not block:
                break
            chunks.append(block)
        data = b''.join(chunks)
        after = os.fstat(fd)
        if len(data) != info.st_size or (after.st_ino, after.st_mode, after.st_size) != (info.st_ino, info.st_mode, info.st_size):
            raise Refused(f'{label} changed while it was hashed')
        return data
    finally:
        os.close(fd)


def exact_tree(project, expected, skipped=(), hasher=git_blob_id, deadline=None, clock=time.monotonic):
    """An exact working tree: every listed path at its mode and blob and nothing else (minus exactly skipped), and no
    directory that holds no listed path. Paths, types and modes come from an lstat walk first, so an unlisted file is
    named and never read; then only the listed entries are hashed (regular files read without following links, links
    by their text). Refuses naming every missing, different and unlisted path, each list bounded at REPORT_ERRORS."""
    base, name = os.fsencode(project), Path(project).name
    skip = {os.fsencode(p) for p in skipped}
    listed_dirs = {b'/'.join(p.split(b'/')[:i]) for p in expected for i in range(1, p.count(b'/') + 1)}

    def check_time():
        if deadline is not None and clock() > deadline:
            raise Refused(f'pin_timeout: checking {name} passed its deadline')

    present, unlisted, stack = {}, [], [b'']
    while stack:
        directory = stack.pop()
        with os.scandir(base + (b'/' + directory if directory else b'')) as it:
            names = sorted(e.name for e in it)
        for entry in names:
            check_time()
            rel = directory + b'/' + entry if directory else entry
            if rel in skip:
                continue
            info = os.lstat(base + b'/' + rel)
            if stat.S_ISDIR(info.st_mode):
                (stack.append(rel) if rel in listed_dirs else unlisted.append(rel + b'/'))
            elif stat.S_ISLNK(info.st_mode):
                present[rel] = ('120000', info)
            elif stat.S_ISREG(info.st_mode):
                present[rel] = ('100755' if info.st_mode & stat.S_IXUSR else '100644', info)
            else:
                unlisted.append(rel)  # no other type is ever a listed entry
    unlisted += [p for p in present if p not in expected]
    missing = [p for p in expected if p not in present]
    different = [p for p in expected if p in present and present[p][0] != expected[p][0]]
    for rel in sorted(p for p in expected if p in present and present[p][0] == expected[p][0]):
        check_time()
        mode, info = present[rel]
        path = base + b'/' + rel
        if mode == '120000':
            oid = hasher(os.readlink(path))
        else:
            data = read_walked(path, info, f'{name}/{os.fsdecode(rel)}')
            oid = hasher(data)
        if oid != expected[rel][1]:
            different.append(rel)
    found = {'missing': sorted(missing), 'different': sorted(different), 'unlisted': sorted(unlisted)}
    if any(found.values()):
        named = '; '.join(f'{kind} {len(paths)}: ' + ', '.join(os.fsdecode(p) for p in paths[:REPORT_ERRORS])
                          for kind, paths in found.items() if paths)
        raise Refused(f'{name} is not exactly its pinned tree ({named})')
    return {'paths': len(expected), 'equalsListing': True}


def lunch_input_checks(tree, inputs, root=ROOT, deadline=None, hasher=git_blob_id, exact=None):
    """Before lunch, in the copy, no network: each exact source (vendor/adevtool and device/google/laguna-kernels/6.6
    in the amended contract) is exactly its pinned tree, naming every difference, with no .git yet; the contract's
    version check, generated vendor files, finder markers, kernel module lists and manifest files are as stated.
    Refuses by name. Never copies an expected value into the report."""
    tree = Path(tree)
    git = inputs['gitRequirement']
    work = tree/git['project']
    if work.is_symlink() or not work.is_dir() or os.path.lexists(work/'.git'):
        raise Refused('lunch-inputs: vendor/adevtool must be a plain directory with no .git before the fetch')
    trees = {}
    for project in (exact if exact is not None else exact_sources(inputs)):
        directory = tree/project
        if directory.is_symlink() or not directory.is_dir():
            raise Refused(f'lunch-inputs: {project} is not a plain directory in the copy')
        try:
            trees[project] = exact_tree(directory, lunch_listing(inputs, project, root), (), hasher, deadline)
        except Refused as error:
            raise Refused(f'lunch-inputs: {project}: {error}') from None
    version = inputs['versionCheck']
    data, form = contained_bytes(tree, version['path']), version['expected']['error']
    if data is None or (sha256_bytes(data), len(data)) != (form['sha256'], form['bytes']):
        raise Refused(f'lunch-inputs: {version["path"]} is not the error form of the adevtool version check')
    for row in inputs['generatedVendor']['observable']:
        data = contained_bytes(tree, row['path'])
        if data is None or sha256_bytes(data) != row['sha256']:
            raise Refused(f'lunch-inputs: {row["path"]} is not the generated vendor file the contract observes')
    for row in inputs['finderPrune']['markers']:
        data = contained_bytes(tree, row['path'])
        if data is None or hasher(data) != row['gitBlob']:
            raise Refused(f'lunch-inputs: the finder marker {row["path"]} is missing or different')
    for row in inputs['kernelModuleLists']['files']:
        data = contained_bytes(tree, row['path'])
        mode = data is not None and ('100755' if os.lstat(tree/row['path']).st_mode & stat.S_IXUSR else '100644')
        if data is None or (mode, hasher(data)) != (row['mode'], row['gitBlob']):
            raise Refused(f'lunch-inputs: the kernel module list {row["path"]} is missing or different')
    for row in inputs['manifestFiles']:
        problem = manifest_file_problem(tree, row)
        if problem:
            raise Refused(f'lunch-inputs: {problem}')
    return {'contractSha256': LUNCH_INPUTS_SHA256, 'exactTrees': trees, 'versionCheck': 'error form',
            'observable': len(inputs['generatedVendor']['observable']), 'finderMarkers': len(inputs['finderPrune']['markers']),
            'kernelModuleLists': len(inputs['kernelModuleLists']['files']), 'manifestFiles': len(inputs['manifestFiles'])}


def environment_problems(env, inputs):
    """The lunch and m environment against the contract's environment.unset: none of its names is present, and no
    GIT_* variable but the contained ones this adapter sets itself (discovery ceiling, no system or global config)."""
    names = [n for n in inputs['environment']['unset'] if n != 'GIT_*']
    return [n for n in names if n in env] + [n for n in env if n.startswith('GIT_') and n not in CONTAINED_GIT]


def fetch_env(tree, home, protocol='https'):
    """gitRequirement.publicFetch.environment (FETCH_ENV_SPEC), as Blazer's git-meta: no inherited GIT_*, no system or
    global configuration (so no insteadOf, credential helper or hook path), an attempt-owned empty HOME and
    XDG_CONFIG_HOME, no prompt, only the source's protocol, PATH /usr/bin:/bin and discovery bounded at the copy root.
    Of the job's environment only its network setup (FETCH_INHERITED: proxies, CA files, LANG) is kept."""
    env = {k: os.environ[k] for k in FETCH_INHERITED if k in os.environ}
    env.update(PATH=GIT_PATH, HOME=str(home), XDG_CONFIG_HOME=str(home), GIT_CONFIG_NOSYSTEM='1',
               GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0', GIT_ASKPASS='/bin/false', SSH_ASKPASS='/bin/false',
               GIT_ALLOW_PROTOCOL=protocol, GIT_CEILING_DIRECTORIES=str(tree))
    return env


def dir_bytes(path):
    """Bytes of the regular files under path (lstat walk, no link followed); 0 when absent."""
    total, stack = 0, [os.fsencode(path)]
    while stack:
        directory = stack.pop()
        with contextlib.suppress(FileNotFoundError, NotADirectoryError):
            with os.scandir(directory) as it:
                for entry in it:
                    with contextlib.suppress(FileNotFoundError):
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISDIR(info.st_mode):
                            stack.append(entry.path)
                        elif stat.S_ISREG(info.st_mode):
                            total += info.st_size
    return total


def proc_stat(entry):
    """(state, pgid, session, starttime) from /proc/<pid>/stat, or None when the process is gone. The comm field may hold
    any bytes, ')' included, so the fields are read after its last ')'; any other failure raises (fail closed)."""
    try:
        data = (Path(entry)/'stat').read_bytes()
    except (FileNotFoundError, ProcessLookupError):
        return None
    fields = data[data.rindex(b')') + 2:].split()
    return fields[0].decode('ascii'), int(fields[2]), int(fields[3]), int(fields[19])


def session_members(sid, proc=Path('/proc')):
    """{pid: pgid} of every live (not zombie) process in session sid, or None when the process table cannot be read
    whole (the caller then treats the session as alive: fail closed). While any member lives, sid names no other
    session, so it is never another's."""
    members = {}
    try:
        entries = [e for e in proc.iterdir() if e.name.isdigit()]
        for entry in entries:
            stat = proc_stat(entry)
            if stat is not None and stat[0] != 'Z' and stat[2] == sid:
                members[int(entry.name)] = stat[1]
    except (OSError, ValueError, IndexError, UnicodeDecodeError):
        return None
    return members


def signal_session(live, sig):
    """sig (and CONT after TERM, so a stopped member wakes to it) to every process group of a session's members. A
    group that is gone, or none of whose members may be signalled, is passed over: the caller's next scan decides."""
    for pgid in sorted(set(live.values())):
        for s in ((sig, signal.SIGCONT) if sig == signal.SIGTERM else (sig,)):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, s)


def take_interrupt():
    """Raises the held interrupt, once, where the caller can still end what it started."""
    if _INTERRUPT['pending'] and not _INTERRUPT['raised']:
        _INTERRUPT['raised'] = True
        raise Interrupted(_INTERRUPT['pending'])


def session_gone(sid, members=None, sleep=time.sleep):
    """Require two complete empty scans a poll apart. A member may fork and exit during the first enumeration; never accept
    that one snapshot as confirmation. This remains session containment, not a cgroup guarantee against setsid."""
    members = members or session_members
    if members(sid) != {}:
        return False
    sleep(POLL_SECONDS)
    return members(sid) == {}


def end_session(proc, clock=time.monotonic, sleep=time.sleep, grace=None, members=None):
    """End a stage's whole session (start_new_session makes its leader's pid the sid): TERM with CONT, then KILL, to
    every process group in it, the leader's own and those its children made (ninja gives each job its own). The session
    is rescanned every POLL_SECONDS: TERM goes once to each group as it is found, and KILL again to every group at
    each scan, so a group made since the last scan is not missed. Each phase lasts grace seconds at most, or
    INTERRUPT_GRACE_SECONDS once an interrupt is pending. True once confirmed gone; False while members
    remain after the KILL phase or the process table cannot be read. A session already gone is never signalled. grace
    defaults to KILL_GRACE_SECONDS, and members to session_members, as they are when called."""
    grace = KILL_GRACE_SECONDS if grace is None else grace
    members = members or session_members
    sid = proc.pid
    for sig in (signal.SIGTERM, signal.SIGKILL):
        start, told = clock(), set()
        while True:
            proc.poll()
            live = members(sid)
            if live == {} and proc.returncode is not None and session_gone(sid, members, sleep):
                return True
            targets = dict(live or {})
            if proc.returncode is None:
                targets[sid] = sid  # the leader's own group, even when the table cannot be read
            if sig == signal.SIGTERM:  # once to each group, so a member that handles TERM can finish doing so
                targets = {pid: pgid for pid, pgid in targets.items() if pgid not in told}
                told.update(targets.values())
            signal_session(targets, sig)
            if clock() - start >= (min(grace, INTERRUPT_GRACE_SECONDS) if _INTERRUPT['pending'] else grace):
                break
            sleep(POLL_SECONDS)
    proc.poll()
    return proc.returncode is not None and session_gone(sid, members, sleep)


def bounded_git(argv, env, cwd, deadline, gitdir, limit, popen=subprocess.Popen, clock=time.monotonic,
                sleep=time.sleep, measure=dir_bytes, stage=None):
    """(exit, stoppedBy or None, sessionEnded) for one git command in its own session, run under deferral for its whole
    life (see stage_ended): every second the stage deadline and the bytes under gitdir are compared with their limits,
    and a stop ends the session. Checked once more after exit (overshoot); a session that outlives its leader is ended
    ('leftover'). stage(pid, label), when given, records the session as soon as it exists."""
    if clock() >= deadline:  # the stage deadline already passed: nothing is started
        return None, 'timeout', True
    with deferred(lambda error: stage_failed(proc, error, 'adevtool-git', stage, clock, sleep, ended)):
        proc, ended = None, None
        try:
            proc = popen(argv, env=env, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
            if stage is not None:
                stage(proc.pid, 'adevtool-git')
            stopped, ended, sample = None, None, clock()
            while proc.poll() is None:
                take_interrupt()
                if clock() >= sample:
                    sample = clock() + 1
                    if clock() >= deadline:
                        stopped = 'timeout'
                    elif measure(gitdir) > limit:
                        stopped = 'gitBytes'
                    if stopped:
                        ended = end_session(proc, clock, sleep)
                        break
                sleep(POLL_SECONDS)
            code, stopped, ended = settled(proc, stopped, ended, clock, sleep)
            if not stopped and measure(gitdir) > limit:
                stopped = 'gitBytes'
            stage_ended(stage, proc, 'adevtool-git', ended)
            take_interrupt()
            return code, stopped, ended
        except BaseException as error:
            raise stage_failed(proc, error, 'adevtool-git', stage, clock, sleep, ended)  # noqa: B904


def settled(proc, stopped, ended, clock, sleep):
    """(exit, stoppedBy, sessionEnded) once the stage's leader has exited or a stop has ended its session: a session
    that outlives a normal exit is ended and stops the stage ('leftover': it could still change the outputs).
    sessionEnded is true only once the whole session is confirmed gone (a process table that cannot be read never
    confirms it); exit is None while the leader still lives."""
    if not stopped:
        if session_gone(proc.pid, sleep=sleep):
            ended = True
        else:
            ended = end_session(proc, clock, sleep)
            stopped = 'leftover'
    return proc.poll(), stopped, bool(ended)


def stage_ended(stage, proc, label, ended):
    """Mark only a confirmed end. finish rechecks the session before removing files, but never signals a marked ID."""
    if stage is not None and ended:
        with contextlib.suppress(OSError):
            stage(proc.pid, label, ended=True)


def stage_failed(proc, error, label, stage, clock, sleep, ended=None):
    """The exception a stage leaves with after error (an interrupt it took, or any failure): its session is ended first,
    within INTERRUPT_GRACE_SECONDS a phase. A stage runs under deferral, so no signal can cut this short; an interrupt
    that arrived while it failed is raised in its place, from it. A stop's existing result is reused, never a second
    shutdown budget. Both ordinary failures and interrupts carry the stage and its containment status."""
    # A stop already attempted both phases. Repeating them here would double a cancel's five-second budget.
    if proc is None:
        ended = True
    elif ended is None:
        try:
            ended = end_session(proc, clock, sleep, INTERRUPT_GRACE_SECONDS)
        except Exception:  # noqa: BLE001  (a failure while cleaning up never replaces the error being handled)
            ended = False
    if proc is not None:
        stage_ended(stage, proc, label, ended)
    if not isinstance(error, Interrupted) and _INTERRUPT['pending'] and not _INTERRUPT['raised']:
        _INTERRUPT['raised'] = True
        late = Interrupted(_INTERRUPT['pending'])
        late.__cause__ = error
        error = late
    if isinstance(error, Interrupted):
        error.stages.append({'stage': label, 'started': proc is not None, 'sessionEnded': ended})
    else:
        error.stage_status = {'stage': label, 'started': proc is not None, 'sessionEnded': ended}
    return error


def git_output(argv, env, cwd, deadline, clock=time.monotonic, run=subprocess.run):
    """(exit, stdout bytes) of a short read-only git command, bounded by the stage deadline."""
    left = deadline - clock()
    if left <= 0:
        return None, b''
    try:
        result = run(argv, env=env, cwd=cwd, capture_output=True, timeout=left)
    except subprocess.TimeoutExpired:
        return None, b''
    return result.returncode, result.stdout


def mount_points(text=None):
    """Every mount point in /proc/self/mountinfo (field 5, octal escapes decoded), bind mounts on one filesystem
    included; None when the table cannot be read."""
    if text is None:
        try:
            text = Path('/proc/self/mountinfo').read_text()
        except OSError:
            return None
    points = []
    for line in text.splitlines():
        fields = line.split(' ')
        if len(fields) > 4:
            points.append(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), fields[4]))
    return points


def gitdir_problems(tree, work, inputs, raw_listing, env, lunch_env, deadline, limit, clock=time.monotonic):
    """Every publicFetch.acceptance item on the new repository but the working tree (adevtool_public_fetch re-checks
    it): HEAD and shallow are exactly the revision; under the lunch environment (GIT_* unset but the ceiling at the copy
    root) rev-parse HEAD is the revision, it is a commit and its tree is exactly rootTree; a full fsck passes; ls-tree
    -r -z --full-tree with each NUL as LF is byte for byte the pinned listing; the local configuration is exactly a new
    repository's (no remote.*), there is no ref but HEAD, and nothing redirects outside the copy (a real .git directory
    resolving inside it; no alternates, commondir, gitdir file, hooks or grafts; no link, mount point, other filesystem
    or second hard link); no gc.pid, gc.log or objects/maintenance.lock is present."""
    git, problems = inputs['gitRequirement'], []
    revision, root_tree = git['revision'], git['rootTree']
    dotgit, tree = work/'.git', Path(tree)
    real_tree = os.path.realpath(tree)

    def out(*args, environment=env):
        code, data = git_output(['git', '-C', str(work), *args], environment, str(work), deadline, clock)
        return code, data.decode(errors='replace')

    if dotgit.is_symlink() or not dotgit.is_dir():
        return ['vendor/adevtool/.git is not a directory']
    for name in ('HEAD', 'shallow'):
        data = contained_bytes(tree, f'{git["project"]}/.git/{name}')
        if data != (revision + '\n').encode():
            problems.append(f'{name} is not exactly the pinned revision')
    code, head = out('rev-parse', 'HEAD', environment=lunch_env)
    if (code, head.strip()) != (0, revision):
        problems.append('under the lunch environment rev-parse HEAD is not the pinned revision')
    code, kind = out('cat-file', '-t', revision, environment=lunch_env)
    if (code, kind.strip()) != (0, 'commit'):
        problems.append('the pinned revision is not a commit')
    code, tree_id = out('rev-parse', '--verify', revision + '^{tree}', environment=lunch_env)
    if (code, tree_id.strip()) != (0, root_tree):
        problems.append('the commit tree is not the pinned rootTree')
    code, _ = out('fsck', '--no-dangling', '--no-progress')
    if code != 0:
        problems.append('a full fsck failed')
    code, listed = git_output(['git', '-C', str(work), 'ls-tree', '-r', '-z', '--full-tree', revision], env, str(work),
                              deadline, clock)
    if code != 0 or listed.replace(b'\0', b'\n') != raw_listing:
        problems.append('the commit tree is not byte for byte the pinned listing')
    code, config = out('config', '--local', '--list')
    keys = [line.split('=', 1)[0] for line in config.splitlines()]
    if code != 0 or sorted(keys) != sorted(GIT_CONFIG_KEYS):
        problems.append('the repository configuration is not exactly a new repository\'s')
    code, refs = out('for-each-ref', '--format=%(refname)')
    if code != 0 or refs.strip() or os.path.lexists(dotgit/'packed-refs'):
        problems.append('a ref other than HEAD exists')
    for args in (('rev-parse', '--absolute-git-dir'), ('rev-parse', '--git-common-dir')):
        code, where = out(*args)
        resolved = os.path.realpath(work/where.strip()) if code == 0 and where.strip() else ''
        if resolved != os.path.realpath(dotgit):
            problems.append(f'{args[1]} does not resolve to vendor/adevtool/.git')
    for name in GIT_REDIRECTS:
        if os.path.lexists(dotgit/name):
            problems.append(f'.git/{name} exists')
    for name in MAINTENANCE_MARKS:
        if os.path.lexists(dotgit/name):
            problems.append(f'.git/{name} exists: maintenance/gc marker is forbidden')
    device, stack = os.lstat(tree).st_dev, [dotgit]
    while stack:
        directory = stack.pop()
        if os.path.ismount(directory):
            problems.append('a mount point is inside the .git')
        for entry in os.scandir(directory):
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                problems.append('a link is inside the .git')
            elif info.st_dev != device:
                problems.append('a .git path is on another filesystem')
            elif stat.S_ISDIR(info.st_mode):
                stack.append(Path(entry.path))
            elif stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                problems.append('a .git file has a second hard link')
            elif not stat.S_ISREG(info.st_mode):
                problems.append('a .git entry is neither a file nor a directory')
    if not os.path.realpath(dotgit).startswith(real_tree + os.sep):
        problems.append('the .git does not resolve inside the copy')
    points, inside = mount_points(), os.path.realpath(dotgit)
    if points is None:
        problems.append('the mount table cannot be read')
    elif any(p == inside or p.startswith(inside + os.sep) for p in points):
        problems.append('a mount point is inside the .git')
    if dir_bytes(dotgit) > limit:
        problems.append(f'the .git holds more than {limit} bytes')
    return sorted(set(problems))


def adevtool_public_fetch(tree, plan, run_root, record, root=ROOT, protocol='https', seconds=ADEVTOOL_FETCH_SECONDS,
                          limit=ADEVTOOL_GIT_BYTES, popen=subprocess.Popen, clock=time.monotonic, sleep=time.sleep,
                          hasher=git_blob_id, stage=None):
    """The admitted public fetch (gitRequirement.publicFetch) into the copy: fetch_steps under fetch_env, then every
    acceptance item (gitdir_problems) and the working tree again, all within the attempt's ceiling. On failure the
    .git this attempt created is removed only after its session is confirmed gone. Otherwise it stays for finish;
    record (the report's adevtoolGit section) says what happened."""
    inputs, url = plan['inputs'], plan['url']
    git = inputs['gitRequirement']
    tree, run_root = Path(tree), Path(run_root)
    work = tree/git['project']
    dotgit = work/'.git'
    record.update(objectsSource='public-fetch', url=url, bounds={'seconds': seconds, 'gitBytes': limit,
                  'kind': 'first ceilings proposed by Infra, not measurements'})
    if not url.startswith(protocol + '://'):
        record.update(ok=False, error=f'the source is not {protocol}')
        raise Refused(f'adevtool-git: the source is not {protocol}')
    component = tree
    for part in Path(git['project']).parts:
        component = component/part
        if component.is_symlink():
            record.update(ok=False, error=f'{component.relative_to(tree)} is a link')
            raise Refused(f'adevtool-git: {component.relative_to(tree)} is a link; nothing is written through it')
    if tree.is_symlink() or not work.is_dir() or os.path.lexists(dotgit):
        record.update(ok=False, error='vendor/adevtool changed before the fetch')
        raise Refused('adevtool-git: vendor/adevtool must be a plain directory with no .git before the fetch')
    fetch_git = shutil.which('git', path=GIT_PATH)
    if fetch_git is None:
        record.update(ok=False, error='no git on /usr/bin:/bin')
        raise Refused('adevtool-git: no git on /usr/bin:/bin')
    raw_listing = lunch_listing(inputs, git['project'], root, raw=True)
    expected = lunch_listing(inputs, git['project'], root)
    home = run_root/'tmp/adevtool-git-home'
    if os.path.lexists(home):
        record.update(ok=False, error='the fetch home already exists')
        raise Refused('adevtool-git: the attempt-owned fetch home already exists')
    home.mkdir(mode=0o700, parents=True)
    env, lunch_env = fetch_env(tree, home, protocol), contained_env(run_root)
    deadline = clock() + seconds

    safe_to_remove = True

    def refuse(why):
        removed = None
        if safe_to_remove and os.path.lexists(dotgit):
            try:
                if dotgit.is_symlink():
                    dotgit.unlink()
                else:
                    shutil.rmtree(dotgit)
                removed = True
            except OSError:
                removed = False
        record.update(ok=False, error=why, createdRemoved=removed, sessionEnded=safe_to_remove)
        raise Refused(f'adevtool-git: {why}')

    try:
        for argv in fetch_steps(work, url, git['revision']):
            safe_to_remove = False  # includes an exception before the step can return its containment result
            code, stopped, ended = bounded_git(argv, env, str(work), deadline, dotgit, limit, popen, clock, sleep,
                                               stage=stage)
            safe_to_remove = ended
            if not ended:
                refuse(f'{argv[3] if argv[1] == "-C" else argv[1]}: its session could not be confirmed ended')
            if code or stopped:
                refuse(f'{argv[3] if argv[1] == "-C" else argv[1]} failed: {stopped or "exit " + str(code)}')
        problems = gitdir_problems(tree, work, inputs, raw_listing, env, lunch_env, deadline, limit, clock)
        if problems:
            refuse('; '.join(problems[:8]))
        try:
            exact_tree(work, expected, ('.git',), hasher, deadline, clock)
        except Refused as error:
            refuse(f'the working tree no longer holds: {error}')
        code, head = git_output(['git', '-C', str(work), 'rev-parse', 'HEAD'], lunch_env, str(work), deadline, clock)
        if (code, head.decode(errors='replace').strip()) != (0, git['revision']):
            refuse('the final rev-parse HEAD under the lunch environment did not give the revision')
        tools = {}
        for role, path in (('fetch', fetch_git), ('lunch', shutil.which('git', path=lunch_env['PATH']))):
            code, version = git_output([path, '--version'], lunch_env, str(work), deadline, clock) if path else (None, b'')
            version = version.decode(errors='replace').strip()
            if code != 0 or not version.startswith('git version '):
                refuse(f'the {role} git did not report its version')
            tools[role] = {'path': path, 'version': version}
        record.update(ok=True, observedHead=head.decode().strip(), gitDirBytes=dir_bytes(dotgit), shallow=True,
                      alternates=False, remote=False, workingTreeHolds=True, git=tools)
        return record
    except Refused:
        raise
    except Interrupted as error:
        if error.stages:
            safe_to_remove = all(s['sessionEnded'] for s in error.stages)
        with contextlib.suppress(Refused):
            refuse('interrupted (the outer timeout or a cancel)')
        raise
    except Exception as error:  # noqa: BLE001  (any other failure refuses, preserving unconfirmed work)
        if hasattr(error, 'stage_status'):
            safe_to_remove = error.stage_status['sessionEnded']
            record['stageFailure'] = error.stage_status
        refuse(f'{type(error).__name__} during the fetch or its checks')


def tree_file_problem(path, source, parent):
    """Why a bound receipt or report path names a file of a checked tree, or None: inside the source tree, or inside a
    launcher3-compile run root under the run root parent (each holds a copy of a checked tree). By path only: check()
    reads no host path, and run() passes the resolved paths."""
    path = Path(path)
    if source is not None and Path(source) in [path, *path.parents]:
        return 'is inside the source tree'
    parent = Path(parent)
    if parent in path.parents and path.relative_to(parent).parts[0].startswith('launcher3-compile-'):
        return 'is inside a launcher3-compile run root'
    return None


def check(binding_path=BINDING, root=ROOT, manifest_path=MANIFEST, inputs_path=None, inputs_pin=LUNCH_INPUTS_SHA256):
    """(manifest, binding, lunch plan). Invalid on a wrong shape, a forbidden input, a receipt or report inside a checked
    tree or a lunch other than the contract's; Unbound while the selected branch lacks an input or the lunch lacks its
    admitted adevtool fetch."""
    manifest = load_manifest(manifest_path)
    binding = launcher3_lane.load(binding_path)
    if not isinstance(binding, dict) or set(binding) != BINDING_KEYS or type(binding['schemaVersion']) is not int \
            or binding['schemaVersion'] != 4 or binding['kind'] != 'rose-launcher3-independent-compile':
        raise Invalid('the binding is not an exact independent Launcher3 v4 record; legacy host binding is retired')
    try:
        launcher3_lane.validate(binding)
    except launcher3_lane.Unbound as error:
        raise Unbound(str(error)) from None
    except (OSError, KeyError, ValueError) as error:
        raise Invalid(str(error)) from None
    if binding['adevtoolGit'] is not None and binding['adevtoolGit'] not in ADEVTOOL_GIT_CLASSES:
        raise Invalid('adevtoolGit must be null or ' + ', '.join(ADEVTOOL_GIT_CLASSES))
    if binding['branch'] not in BRANCHES:
        raise Invalid('branch must be ' + ', '.join(BRANCHES))
    receipt, bundle = binding['policyReceipt'], binding['bundleReport']
    for name, value in (('policyReceipt', receipt), ('bundleReport', bundle)):
        if not isinstance(value, dict) or set(value) != {'path', 'sha256'}:
            raise Invalid(f'{name} must be exactly {{path, sha256}}')
        if value['sha256'] is not None and not re.fullmatch(r'[0-9a-f]{64}', str(value['sha256'])):
            raise Invalid(f'{name}.sha256 must be a lowercase sha256')
    for name, value in (('sourceTree', binding['sourceTree']), ('policyReceipt.path', receipt['path']),
                        ('bundleReport.path', bundle['path'])):
        if value is not None and (not isinstance(value, str) or not value.startswith('/') or os.path.normpath(value) != value):
            raise Invalid(f'{name} must be a normalized absolute path')
    if binding['sourceTree'] is not None:
        source, parent = Path(binding['sourceTree']), Path(binding['taskParent'])
        if parent in [source, *source.parents]:
            raise Invalid('the source tree must not be under the run root parent (no Pro snapshot or earlier run root)')
        if source in parent.parents:
            raise Invalid('the run root parent must not be inside the source tree')
    for name, value in (('policyReceipt.path', receipt['path']), ('bundleReport.path', bundle['path'])):
        problem = None if value is None else tree_file_problem(value, binding['sourceTree'], binding['taskParent'])
        if problem:
            raise Invalid(f'{name} {problem}: no bound receipt or report is a file of a checked tree '
                          '(definitions.policyReceipt)')
    if binding['lunch'] is not None and not re.fullmatch(r'[a-z0-9_]+-[a-z0-9]+-(user|userdebug|eng)', str(binding['lunch'])):
        raise Invalid('lunch must be <product>-<release>-<variant>')
    frankel_policy_args(root)
    apply_args = json.loads((Path(root)/APPLY_ARGS_SOURCE['path']).read_text())
    for key in APPLY_ARGS_SOURCE['key'].split('.'):
        apply_args = apply_args[key]
    if manifest['base']['registration']['applyArgs'] != apply_args:
        raise Invalid('base.registration.applyArgs are not frankel\'s committed applyArgs')
    exclusions = json.loads((Path(root)/'os/release-pipeline/cuttlefish-alpha.json').read_text())['preparation']['exclusions']
    if not {'.git', '.repo'} <= set(exclusions['anywhereNames']):
        raise Invalid('the copy must exclude every .git and .repo, so the checked tree holds no Git data of the three '
                      f'contract projects ({CHECKED_TREE_GIT})')
    values = {'sourceTree': binding['sourceTree'], 'lunch': binding['lunch'], 'policyReceipt.path': receipt['path'],
              'policyReceipt.sha256': receipt['sha256'], 'bundleReport.path': bundle['path'],
              'bundleReport.sha256': bundle['sha256'], 'reconstructSource': binding['reconstructSource']}
    fields = {'policyReceipt': ['policyReceipt.path', 'policyReceipt.sha256'],
              'bundleReport': ['bundleReport.path', 'bundleReport.sha256']}
    for name in BRANCH_FORBIDS[binding['branch']]:
        if name != CHECKED_TREE_GIT and any(values[f] is not None for f in fields.get(name, [name])):
            raise Invalid(f'{binding["branch"]} forbids {name}; it must be null')
    needed = [f for name in BRANCH_REQUIRES[binding['branch']] for f in fields.get(name, [name])]
    unbound = [n for n in needed if values[n] is None]
    if unbound:
        raise Unbound(f'{binding["branch"]} needs inputs not yet bound in {Path(binding_path).name}: {", ".join(unbound)}')
    plan = lunch_plan(binding, root, inputs_path, inputs_pin)
    if binding['branch'] == 'reconstruct':
        raise Unbound('reconstruct needs an approved genuine-Git-object source; this adapter has none wired')
    return manifest, binding, plan


def read_bytes(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return None
    return path.read_bytes()


def classify(tree, section, policy=None, policy_sha256=None, bound_policy_sha256=None, reference=None, locked=False):
    """{'branch', 'files', 'overlays', 'problems'} for a contract section (the manifest itself for the final images, or
    manifest['base']): 'exact' only when every affected file is at its post-image and every required overlay is present
    at its sha256 (notAnInput overlays may be present with any bytes or absent); otherwise None with every reason. The
    tree pins are checked separately (tree_pins). Reads only; never applies."""
    tree, files, problems = Path(tree), [], []
    for project in section['projects']:
        for entry in project['files']:
            relative = f'{project["path"]}/{entry["path"]}'
            data = read_bytes(tree/relative)
            row = {'path': relative}
            if data is None:
                row['state'] = 'missing'
            elif 'integrationRewrite' in entry:
                errors = verify_config(data, entry['post']['sha256'], config_rules(entry), policy, policy_sha256,
                                       bound_policy_sha256, reference, locked)
                row['state'] = 'post' if not errors else 'other'
                if errors:
                    row['errors'] = errors[:5]
            elif sha256_bytes(data) == entry['post']['sha256'] and git_blob(data) == entry['post']['gitBlob']:
                row['state'] = 'post'
            elif entry['pre'] and sha256_bytes(data) == entry['pre']['sha256'] and git_blob(data) == entry['pre']['gitBlob']:
                row['state'] = 'pre'
            else:
                row['state'] = 'other'
            files.append(row)
    overlays = []
    for overlay in section['overlays']:
        if overlay['role'] != 'required':
            continue
        data = read_bytes(tree/overlay['target'])
        state = 'missing' if data is None else 'present' if sha256_bytes(data) == overlay['sha256'] else 'other'
        overlays.append({'path': overlay['target'], 'state': state})
    off = [f for f in files if f['state'] != 'post'] + [o for o in overlays if o['state'] != 'present']
    if not files or not overlays:
        problems.append('the contract names no files or no required overlays')
    for row in off:
        problems.append(f'{row["path"]}: {row["state"]}')
    if off and {f['state'] for f in files} >= {'post', 'pre'}:
        problems.insert(0, 'mixed state: some files are pre-images and some post-images; nothing is reset')
    return {'branch': None if problems else 'exact', 'files': files, 'overlays': overlays,
            'problems': problems}


# ---------------------------------------------------------------- aconfig cache (protobuf, read only)

def varint(data, i):
    value, shift = 0, 0
    while True:
        if i >= len(data) or shift > 63:
            raise ValueError('truncated varint')
        byte = data[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7


def fields(data):
    """[(number, wire type, value)] of one protobuf message (varint, 64-bit, length-delimited, 32-bit)."""
    out, i = [], 0
    while i < len(data):
        key, i = varint(data, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = varint(data, i)
        elif wire == 1:
            value, i = data[i:i + 8], i + 8
        elif wire == 2:
            length, i = varint(data, i)
            if i + length > len(data):
                raise ValueError('truncated field')
            value, i = data[i:i + length], i + length
        elif wire == 5:
            value, i = data[i:i + 4], i + 4
        else:
            raise ValueError(f'unsupported wire type {wire}')
        out.append((number, wire, value))
    return out


def flag_values(cache):
    """{(package, name): (state, permission)} of an aconfig parsed_flags cache."""
    values = {}
    for number, wire, flag in fields(cache):
        if number != 1 or wire != 2:
            continue
        record = {n: v for n, _, v in fields(flag)}
        key = (record.get(1, b'').decode(), record.get(2, b'').decode())
        values[key] = (STATE.get(record.get(6)), PERMISSION.get(record.get(7)))
    return values


def bounded_files(directory, suffixes, limit=PROOF_ENTRIES):
    found, seen = [], 0
    for base, dirs, names in os.walk(directory, followlinks=False):
        for name in sorted(names):
            seen += 1
            if seen > limit:
                return found, True
            if name.endswith(suffixes):
                found.append(Path(base)/name)
    return found, False


def flag_dirs(manifest, out):
    """[(flag evidence row, its module's output directory under this run's OUT_DIR)] from the contract."""
    rows = []
    for row in manifest['flagEvidence']['flags']:
        relative = row['expectedOutputDir']
        if not relative.startswith('out/soong/.intermediates/') or '..' in Path(relative).parts:
            raise Invalid(f'{row["flag"]}: expectedOutputDir must be under out/soong/.intermediates')
        rows.append((row, Path(out)/relative[len('out/'):]))
    return rows


def flag_line(package, name, value):
    state, permission = value
    return f'{package}.{name}:{permission}={"true" if state == "ENABLED" else "false" if state == "DISABLED" else state}'


def flag_report(out, manifest):
    """Each flag: exactly one line in its own module's aconfig-flags.txt equal to the expected line, and the same value
    decoded independently from that module's aconfig-cache.pb. Absent, duplicated, disagreeing or other: not ok."""
    rows = []
    for evidence, directory in flag_dirs(manifest, out):
        row = {'flag': evidence['flag'], 'module': evidence['module'], 'expected': evidence['expectedLine']}
        text_path, cache_path = directory/FLAG_TEXT, directory/FLAG_CACHE
        lines = []
        if text_path.is_file() and not text_path.is_symlink():
            lines = [l for l in text_path.read_text(errors='replace').splitlines() if l.startswith(evidence['flag'] + ':')]
        decoded = None
        if cache_path.is_file() and not cache_path.is_symlink():
            with contextlib.suppress(ValueError, OSError, UnicodeDecodeError):
                value = flag_values(cache_path.read_bytes()).get((evidence['package'], evidence['flag'][len(evidence['package']) + 1:]))
                decoded = flag_line(evidence['package'], evidence['flag'][len(evidence['package']) + 1:], value) if value else None
        row.update(textLines=lines[:3], decodedCache=decoded)
        if len(lines) != 1:
            row.update(ok=False, reason=f'{len(lines)} lines for the flag in {FLAG_TEXT}, expected exactly one')
        elif lines[0] != evidence['expectedLine']:
            row.update(ok=False, reason='the module text differs from the expected line')
        elif decoded != lines[0]:
            row.update(ok=False, reason='the decoded cache does not agree with the module text')
        else:
            row['ok'] = True
        rows.append(row)
    return rows


def source_proof(out, manifest):
    """Every Launcher3 post-image and required Launcher3 overlay appears in the library's Soong file lists (rsp)."""
    launcher = next(p for p in manifest['projects'] if p['path'] == 'packages/apps/Launcher3')
    wanted = [f'{launcher["path"]}/{f["path"]}' for f in launcher['files']]
    wanted += [o['target'] for o in manifest['overlays'] if o['role'] == 'required' and o['target'].startswith(launcher['path'] + '/')]
    lists, truncated = bounded_files(Path(out)/'soong/.intermediates/packages/apps/Launcher3'/TARGET, ('.rsp', '.list'))
    text = ''
    for item in lists:
        with contextlib.suppress(OSError):
            text += item.read_text(errors='replace') + '\n'
    missing = [w for w in wanted if w not in text]
    return {'lists': len(lists), 'expected': len(wanted), 'missing': missing[:REPORT_ERRORS], 'truncated': truncated,
            'ok': bool(lists) and not missing and not truncated}


def classpath_proof(out):
    """A framework jar on the library's classpath holds AutomatedPackageListener declaring onAutomatedDevicesChanged."""
    lists, truncated = bounded_files(Path(out)/'soong/.intermediates/packages/apps/Launcher3'/TARGET, ('.rsp',))
    jars = set()
    for item in lists:
        with contextlib.suppress(OSError):
            jars.update(re.findall(r'(\S*framework\S*\.jar)', item.read_text(errors='replace')))
    for jar in sorted(jars):
        path = Path(jar) if jar.startswith('/') else Path(out).parent/jar
        with contextlib.suppress(OSError, zipfile.BadZipFile):
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if name.endswith(CLASSPATH_SYMBOL[0].decode() + '.class'):
                        if CLASSPATH_SYMBOL[1] in archive.read(name):
                            return {'ok': True, 'jar': jar, 'class': name}
    return {'ok': False, 'jars': len(jars), 'truncated': truncated,
            'reason': 'no classpath jar holds AutomatedPackageListener with onAutomatedDevicesChanged'}


# ---------------------------------------------------------------- bounded processes

def session_rss(sid, proc=Path('/proc')):
    """Resident bytes of every process in a session: the stage's leader and every job group ninja makes (sampled; may
    miss short-lived processes)."""
    total, page = 0, os.sysconf('SC_PAGE_SIZE')
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        with contextlib.suppress(OSError, ValueError, IndexError):
            stat = proc_stat(entry)
            if stat is not None and stat[2] == sid:
                total += int((entry/'statm').read_bytes().split()[1]) * page
    return total


def used_bytes(path):
    fs = os.statvfs(path)
    return (fs.f_blocks - fs.f_bfree) * fs.f_frsize


def supervised(argv, env, cwd, seconds, out_dir, popen=subprocess.Popen, clock=time.monotonic, sleep=time.sleep,
               rss=session_rss, used=used_bytes, stage=None, label='stage'):
    """(exit, stoppedBy or None, sessionEnded). Own session, run under deferral for its whole life: an interrupting
    signal is held, taken at the next POLL_SECONDS poll, and the session ended before it leaves (stage_failed), as on
    any other exception. Every SAMPLE_SECONDS the output filesystem's growth and the session's RSS are compared with
    their stop thresholds; a stop or the timeout ends the whole session (end_session), and a session that outlives its
    leader's normal exit is ended and stops the stage ('leftover'). stage(pid, label), when given, records the session
    as soon as it exists, and marks it ended once confirmed gone."""
    with deferred(lambda error: stage_failed(proc, error, label, stage, clock, sleep, ended)):
        proc, ended = None, None
        try:
            proc = popen(argv, env=env, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
            if stage is not None:
                stage(proc.pid, label)
            start_used, deadline, stopped, ended, sample = used(out_dir), clock() + seconds, None, None, clock()
            while proc.poll() is None:
                take_interrupt()
                if clock() >= sample:
                    sample = clock() + SAMPLE_SECONDS
                    if clock() > deadline:
                        stopped = 'timeout'
                    elif used(out_dir) - start_used > OUTPUT_STOP_BYTES:
                        stopped = 'output'
                    elif rss(proc.pid) > MEMORY_STOP_BYTES:
                        stopped = 'memory'
                    if stopped:
                        ended = end_session(proc, clock, sleep)
                        break
                sleep(POLL_SECONDS)
            result = settled(proc, stopped, ended, clock, sleep)
            ended = result[2]
            stage_ended(stage, proc, label, result[2])
            take_interrupt()
            return result
        except BaseException as error:
            raise stage_failed(proc, error, label, stage, clock, sleep, ended)  # noqa: B904


def contained_env(run_root, path=os.environ.get('PATH', '/usr/bin:/bin')):
    env = {'PATH': path, 'HOME': str(run_root/'home'), 'TMPDIR': str(run_root/'tmp'), 'OUT_DIR': str(run_root/'out'),
           'XDG_CACHE_HOME': str(run_root/'home/.cache'), 'XDG_CONFIG_HOME': str(run_root/'home/.config'),
           'XDG_DATA_HOME': str(run_root/'home/.local/share'), 'GRADLE_USER_HOME': str(run_root/'home/.gradle'),
           'USE_CCACHE': '0', 'LANG': 'C.UTF-8',
           # Git discovery stops at the copy root (no parent repository can answer), and no system or user git
           # configuration is read.
           'GIT_CEILING_DIRECTORIES': str(run_root/'tree'), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
    # The runner's own job-end sweep ends processes that carry its tracking ID: a last backstop for anything that
    # escapes the session (best effort: a tool that rebuilds its environment drops it).
    if os.environ.get('RUNNER_TRACKING_ID'):
        env['RUNNER_TRACKING_ID'] = os.environ['RUNNER_TRACKING_ID']
    return env


def lunch_argv(lunch):
    return ['bash', '-c', 'set -eo pipefail; source build/envsetup.sh >/dev/null; lunch "$1" >/dev/null',
            'launcher3-lunch', lunch]


def version_checked(lunch):
    return lunch is not None and lunch.split('-', 1)[0] in VERSION_CHECKED_PRODUCTS


def compile_argv(lunch, targets):
    script = ('set -eo pipefail; source build/envsetup.sh >/dev/null; lunch "$1" >/dev/null; shift; '
              f'm -j{JOBS} "$@"')
    return ['bash', '-c', script, 'launcher3-compile', lunch, *targets]


def compile_targets(manifest, out):
    """The library plus each flag module's aconfig-flags.txt as an explicit output goal (the tree's own pinned rule
    produces the text; a module invocation alone is not assumed to)."""
    return [TARGET, *sorted(str(directory/FLAG_TEXT) for _, directory in flag_dirs(manifest, out))]


# ---------------------------------------------------------------- run

@contextlib.contextmanager
def shared_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('lock_held: the shared builder lock is held by another job') from None
        yield
    finally:
        os.close(fd)


def write_once(path, document):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w') as f:
        f.write(json.dumps(document, indent=2, sort_keys=True) + '\n')


def hold_run_lock(receipts):
    """run's own lock, receipts/run.lock, held for its whole life (the kernel releases it if python dies): finish
    waits for it, so it never removes a tree a live run still uses."""
    fd = os.open(Path(receipts)/RUN_LOCK, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def stage_recorder(receipts):
    """stage(pid, label, ended=False): receipts/stage.json names the running stage's session (its leader's pid is its
    sid) and the leader's start time, so finish can end a session that outlived run and never signal a reused process
    ID; ended=True marks it confirmed gone, and finish then signals nothing."""
    receipts = Path(receipts)
    started = {}

    def stage(pid, label, ended=False):
        if not ended:
            stat = proc_stat(Path('/proc')/str(pid))
            started[pid] = None if stat is None else stat[3]
        staging = receipts/f'.{STAGE_RECORD}.new'
        with contextlib.suppress(FileNotFoundError):
            staging.unlink()
        fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump({'sid': pid, 'stage': label, 'startTime': started.get(pid), 'ended': ended}, f)
        os.replace(staging, receipts/STAGE_RECORD)
    return stage


class deferred:
    """While a stage runs (from before its process exists until its session is confirmed gone) and while the report is
    written, an interrupting signal is held: the stage takes it at its next poll (take_interrupt), and anything still
    held is raised as Interrupted once the outermost deferral ends."""
    def __init__(self, on_interrupt=None):
        self.on_interrupt = on_interrupt

    def __enter__(self):
        _INTERRUPT['deferring'] += 1

    def __exit__(self, kind, value, traceback):
        try:
            if _INTERRUPT['deferring'] == 1:
                try:
                    take_interrupt()
                except Interrupted as error:
                    if self.on_interrupt is not None:
                        raise self.on_interrupt(error)
                    raise
        finally:
            _INTERRUPT['deferring'] -= 1
        return False


def task_root(binding, run_id, attempt):
    for label, value in (('run id', run_id), ('attempt', attempt)):
        if not (isinstance(value, str) and value.isdigit() and value[0] != '0' and len(value) <= 20):
            raise Invalid(f'a positive numeric {label} is required')
    return Path(binding['taskParent'])/TASK_NAME.format(run_id=run_id, attempt=attempt)


def copy_tree(source, destination, root=ROOT, statvfs=os.statvfs, planner=alpha_source.plan_copy,
              copier=alpha_source.copy_tree, seconds=PREPARE_SECONDS, omit=None):
    """The reviewed alpha copy (plan, then alpha_source.copy_tree, which also rebases links) of the selection, minus
    exactly what omit(path, is_dir) names (a frankel lunch: what vendor/adevtool's pinned .gitignore ignores).
    Refused before writing on plan problems, a kept link into an omitted path, the source bound or the reserve; a
    wall-clock timer stops the copy at `seconds` (the partial tree stays inside the run root and is reported, never
    used)."""
    policy = json.loads((root/'os/release-pipeline/cuttlefish-alpha.json').read_text())['preparation']['exclusions']
    plan = planner(source, policy)
    omitted = []
    if omit is not None and not plan['problems']:
        omitted = [e['path'] for e in plan['entries'] if omit(e['path'], e['type'] == 'dir')]
        dropped = set(omitted)
        kept = [e for e in plan['entries'] if e['path'] not in dropped]
        types = {e['path']: e['type'] for e in plan['entries']}
        plan = dict(plan, entries=kept, excluded=plan['excluded'] + [{'path': p, 'reason': 'omittedFromCopy'} for p in omitted],
                    problems=[f'{e["path"]} links into the omitted {e["target"]}' for e in kept
                              if e['type'] == 'link' and e.get('exists') and omit(e['target'], types.get(e['target']) == 'dir')])
    if plan['problems']:
        raise Refused(f'copy_refused: {len(plan["problems"])} problems, first: {plan["problems"][0]}')
    fs = statvfs(destination.parent)
    projected = sum(e.get('size', 0) for e in plan['entries'] if e['type'] == 'file')
    available = fs.f_bavail * fs.f_frsize
    if projected > SOURCE_BOUND_BYTES:
        raise Refused(f'copy_refused: the selection projects {projected} bytes, above {SOURCE_BOUND_BYTES}')
    if available - projected < RESERVE_BYTES:
        raise Refused(f'copy_refused: available {available} minus {projected} is below the {RESERVE_BYTES} reserve')

    def expired(signum, frame):
        raise Refused(f'copy_timeout: the copy exceeded {seconds} s; the partial tree is not used')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        copier(Path(source), destination, destination, plan)
    except alpha_source.PreparationFailed as error:
        raise Refused(f'copy_failed: {error}') from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    if omit is not None:  # a relative link whose text passes through an omitted path resolves in the source only
        dangling = [e['path'] for e in plan['entries']
                    if e['type'] == 'link' and e.get('exists') and not os.path.exists(Path(destination)/e['path'])]
        if dangling:
            raise Refused(f'copy_refused: {len(dangling)} kept links dangle in the copy, first: {dangling[0]}')
    return {'entries': len(plan['entries']), 'excluded': len(plan['excluded']), 'omitted': len(omitted),
            'projectedBytes': projected, 'availableBytesBefore': available}


def bound_json(path):
    """(document, sha256 of its bytes) of a bound host receipt; read without following a final symlink."""
    data, _ = read_nofollow(path)
    return json.loads(data), sha256_bytes(data)


def report_shape_problems(value, spec, where):
    """A report section against its base.bundleReport.fields spec: exactly these keys; a {name: subspec} entry holds
    one object matching subspec, or for REPORT_ROWS a list of them."""
    if not isinstance(value, dict):
        return [f'{where} is not an object']
    names = {}
    for item in spec:
        if isinstance(item, str):
            names[item] = None
        else:
            (key, sub), = item.items()
            names[key] = sub
    problems = [] if set(value) == set(names) else [f'{where} keys are not exactly {sorted(names)}']
    for key, sub in names.items():
        if sub is None or key not in value:
            continue
        label = f'{where}.{key}'
        if label not in REPORT_ROWS:
            problems += report_shape_problems(value[key], sub, label)
        elif not isinstance(value[key], list):
            problems.append(f'{label} is not a list of objects')
        else:
            for index, item in enumerate(value[key]):
                problems += report_shape_problems(item, sub, f'{label}[{index}]')
    return problems


def report_value_is(value, kind):
    """Whether a report field's value is of its REPORT_TYPES kind (KIND_NAMES); a boolean is never a count."""
    if kind == 'bool':
        return isinstance(value, bool)
    if kind == 'count':
        return (type(value) is int and value > 0) or (isinstance(value, str) and re.fullmatch(r'[1-9][0-9]*', value) is not None)
    if kind in ('texts', 'list', 'rows'):
        return isinstance(value, list) and (kind == 'list' or all(
            isinstance(v, str) and v != '' if kind == 'texts' else isinstance(v, dict) for v in value))
    if kind == 'object':
        return isinstance(value, dict)
    if not isinstance(value, str) or value == '':
        return False
    if kind == 'time':
        try:
            return time.strftime(REPORT_TIME, time.strptime(value, REPORT_TIME)) == value
        except ValueError:
            return False
    pattern = {'label': r'[A-Za-z0-9][A-Za-z0-9._-]*', 'sha256': r'[0-9a-f]{64}', 'commit': r'[0-9a-f]{40}'}.get(kind)
    if pattern:
        return re.fullmatch(pattern, value) is not None
    if kind == 'repoPath':
        return not value.startswith('/') and os.path.normpath(value) == value and value.split('/')[0] != '..'
    if kind == 'hostPath':
        return value.startswith('/') and os.path.normpath(value) == value
    return kind == 'text'


def report_type_problems(report):
    """Each field of a report of the right shape against REPORT_TYPES, and each observed row against ROW_TYPES."""
    problems = []
    for section, kinds in REPORT_TYPES.items():
        for name, kind in kinds.items():
            value = report[section][name]
            if not report_value_is(value, kind):
                problems.append(f'{section}.{name} is not {KIND_NAMES[kind]}')
            elif kind == 'rows':
                for index, row in enumerate(value):
                    problems += [f'{section}.{name}[{index}].{field} is not {KIND_NAMES[sub]}'
                                 for field, sub in ROW_TYPES[name].items() if not report_value_is(row[field], sub)]
    return problems


def bundle_report_problems(report, base):
    """Every way an Infra bundle report fails base.bundleReport against base.registration and base.bundleContent, each
    as a named problem, never an exception: its kind and every section and field exactly, each field of its type
    (REPORT_TYPES); the association the report itself shows (the run commit is the candidate and on main; a reconcile
    receipt of the run's label, named *-<label>.json, its at within [createdAt, updatedAt]; the trusted patcher at the
    registration's pins and the run's applyArgs equal to the registration's, with both pin flags true; a producer that
    is neither the adapter nor this contract's checker); nothing unclassified; observed patches in the registration
    order, and observed minus unclassified equal to bundleContent (patches per project in order, overlays and
    notAnInput targets). The rest of the association (BUNDLE_TRUSTED) is the producer's reviewed statement, which
    report.json names as taken on trust."""
    registration, content = base['registration'], base['bundleContent']
    if not isinstance(report, dict):
        return ['the bundle report is not an object']
    version = report.get('schemaVersion')
    problems = [] if (report.get('kind'), version) == BUNDLE_REPORT and type(version) is int else \
        ['the bundle report is not a rose-integration-bundle-report v1']
    sections = set(BUNDLE_REPORT_FIELDS)
    if set(report) != sections | {'kind', 'schemaVersion'}:
        return problems + ['the bundle report sections are not exactly ' + ', '.join(sorted(sections))]
    for section, spec in BUNDLE_REPORT_FIELDS.items():
        problems += report_shape_problems(report[section], spec, section)
    if problems:
        return problems
    problems = report_type_problems(report)
    if problems:
        return problems
    run, receipt, candidate, observed = report['run'], report['receipt'], report['candidate'], report['observed']
    if candidate['commit'] != run['headSha']:
        problems.append('candidate.commit is not run.headSha: the run commit is the candidate (association)')
    if candidate['onMain'] is not True:
        problems.append('candidate.onMain is not true: headSha is reachable from main (association)')
    if receipt['receiptType'] != 'reconcile':
        problems.append('receipt.receiptType is not reconcile (association)')
    name = receipt['path'].rsplit('/', 1)[-1]
    if receipt['label'] != run['label'] or not name.endswith(f'-{run["label"]}.json') or name == f'-{run["label"]}.json':
        problems.append('the receipt is not a *-<label>.json of the run\'s label (association)')
    if not run['createdAt'] <= receipt['at'] <= run['updatedAt']:
        problems.append('receipt.at is not within [run.createdAt, run.updatedAt] (association)')
    if report['producer']['path'] in NEVER_PRODUCERS:
        problems.append(f'the producer {report["producer"]["path"]} is the adapter or this contract\'s checker, never the '
                        'stated producer')
    if candidate['patcherKind'] != registration['patcher']['patcher']:
        problems.append('candidate.patcherKind is not the trusted patcher')
    if receipt['patcher'] != {'patcher': registration['patcher']['patcher'], 'sha256': registration['patcher']['sha256']}:
        problems.append('receipt.patcher is not the trusted patcher at the registration pins')
    if candidate['applyArgs'] != registration['applyArgs']:
        problems.append('candidate.applyArgs are not the registration applyArgs')
    if report['registration']['patcherMatchesPin'] is not True or report['registration']['applyArgsMatchPin'] is not True:
        problems.append('the report does not state that its patcher and applyArgs match the pins')
    if observed['unclassified'] != []:
        problems.append('observed.unclassified is not empty')
    if [(p['project'], p['name']) for p in observed['patches']] != [(o['project'], o['name']) for o in registration['order']]:
        problems.append('observed.patches are not the registration order')

    def by_project(patches):
        grouped = {}
        for p in patches:
            grouped.setdefault(p['project'], []).append((p['name'], p['sha256']))
        return grouped
    if by_project(observed['patches']) != by_project(content['patches']):
        problems.append('observed patches differ from base.bundleContent')
    pairs = lambda rows: sorted((r['target'], r['sha256']) for r in rows)  # noqa: E731
    if pairs(observed['overlays']) != pairs(content['overlays']) or \
            sorted(observed['notAnInputOverlays']) != sorted(content['notAnInputOverlays']):
        problems.append('observed overlays differ from base.bundleContent')
    return problems


def bundle_matches(report, report_sha256, bound_sha256, base):
    """base-exact's bundle half: the Infra bundle report (its bound sha256) is a matching rose-integration-bundle-report
    v1 (bundle_report_problems): a bare bundleContent or a report missing any section is refused, and so, by name, is
    one this adapter cannot read. Which bundle integrated the tree is Infra provenance; a hash of current bytes is
    not."""
    if not bound_sha256 or report_sha256 != bound_sha256:
        raise Refused('base-exact: the bundle report is missing or not the one bound for this tree')
    try:
        problems = bundle_report_problems(report, base)
    except (AttributeError, KeyError, TypeError, ValueError) as error:  # a defect: never a traceback in place of a refusal
        raise Refused(f'base-exact: the bundle report cannot be read as base.bundleReport ({type(error).__name__})') from None
    if problems:
        raise Refused('base-exact: the integrating bundle report does not match: ' + '; '.join(problems[:5]))


def bundle_record(report, sha256):
    """What report.json says of the bundle report base-exact used: its sha256, run, receipt and producer, and what of its
    association was taken on trust (BUNDLE_TRUSTED)."""
    return {'sha256': sha256, 'run': {k: report['run'][k] for k in ('runId', 'runAttempt', 'headSha', 'label')},
            'receipt': {k: report['receipt'][k] for k in ('path', 'receiptSha256', 'at')},
            'producer': dict(report['producer']), 'takenOnTrust': list(BUNDLE_TRUSTED)}


def checked(section, tree, args, label, pins, root, deadline):
    result = classify(tree, section, *args)
    if result['branch'] != 'exact':
        raise Refused(f'{label}: ' + '; '.join(result['problems'][:5]))
    return result, pins(tree, section['projects'], root, deadline)


def run(binding, manifest, run_id, attempt, root=ROOT, lock=None, copier=copy_tree, compiler=supervised,
        policy_loader=None, bundle_loader=None, pins=tree_pins, clock=time.monotonic, plan=None, inputs_check=None,
        fetcher=None, applier=None):
    """The adapter run; returns its exit status and leaves receipts/report.json naming each step's outcome."""
    run_root = task_root(binding, run_id, attempt)
    if Path(binding['taskParent']).is_symlink() or not Path(binding['taskParent']).is_dir():
        raise Refused('the run root parent must be an existing plain directory')
    run_root.mkdir(mode=0o700)
    receipts = run_root/'receipts'
    receipts.mkdir(mode=0o700)
    run_lock = hold_run_lock(receipts)  # held until run returns, or released by the kernel if python dies
    stage = stage_recorder(receipts)
    reference, locked = frankel_policy_args(root)
    report = {'schema': 'roseos.launcher3-compile-report.v1', 'target': TARGET, 'manifestSha256': MANIFEST_SHA256,
              'candidateSha': os.environ.get('GITHUB_SHA'), 'branchRequested': binding['branch'],
              'lunch': binding['lunch'], 'sourceTree': binding['sourceTree'],
              'policyInputs': {'receiptSha256': binding['policyReceipt']['sha256'], 'referenceDisplayAddress': reference,
                               'lockedControl': locked},
              'bounds': {'sourceBytes': SOURCE_BOUND_BYTES, 'reserveBytes': RESERVE_BYTES, 'prepareSeconds': PREPARE_SECONDS,
                         'compileSeconds': COMPILE_SECONDS, 'outputStopBytes': OUTPUT_STOP_BYTES,
                         'memoryStopBytes': MEMORY_STOP_BYTES, 'sampleSeconds': SAMPLE_SECONDS, 'jobs': JOBS,
                         'kind': 'first-measurement ceilings; sampled stops can overshoot; no hard cap'},
              'proves': manifest['proves'], 'unaffectedProjects': 'the three contract projects are checked against their tree pins (Git blob IDs of the copied bytes); every other project is not verified by this adapter (Infra provenance)',
              'identitySharing': manifest['identitySharing']}
    status, applied = 3, []
    try:
        try:
            if binding['adevtoolGit'] not in ADEVTOOL_GIT_CLASSES:
                raise Refused('adevtool_git_missing: the lunch runs adevtool-version-check.mk and no admitted adevtool public '
                              'fetch is bound; nothing copied')
            if plan is None or binding['lunch'] != plan['inputs']['lunch']['target']:
                raise Refused('lunch-inputs: no admitted lunch-input plan for this lunch (definitions.lunch: the lunch inputs '
                              'in lunchInputs, satisfied on the copy); nothing copied')
            real = os.path.realpath
            for name in ('policyReceipt', 'bundleReport'):
                path = binding[name]['path']
                problem = None if path is None else tree_file_problem(real(path), real(binding['sourceTree']),
                                                                        real(binding['taskParent']))
                if problem:
                    raise Refused(f'{name}: the bound path {problem} once resolved; nothing copied')
            with (lock or shared_lock)(lock_path(binding)):
                for name in ('home', 'tmp', 'out'):
                    (run_root/name).mkdir(mode=0o700)
                tree = run_root/'tree'
                project = plan['inputs']['gitRequirement']['project'] + '/'
                report['copy'] = copier(Path(binding['sourceTree']), tree, omit=lambda rel, is_dir: rel.startswith(project)
                                        and adevtool_ignored(rel[len(project):], is_dir))
                try:
                    policy, policy_sha = (policy_loader or bound_json)(binding['policyReceipt']['path'])
                except (OSError, ValueError):
                    raise Refused('policy_missing: the bound policy receipt cannot be read') from None
                args = (policy, policy_sha, binding['policyReceipt']['sha256'], reference, locked)
                deadline = clock() + PIN_SECONDS
                steps = report['conditions'] = []
                if binding['branch'] == 'verifyBaseApplyCandidate':
                    try:
                        bundle, bundle_sha = (bundle_loader or bound_json)(binding['bundleReport']['path'])
                    except (OSError, ValueError):
                        raise Refused('base-exact: the bound bundle report cannot be read') from None
                    bundle_matches(bundle, bundle_sha, binding['bundleReport']['sha256'], manifest['base'])
                    report['bundleReport'] = bundle_record(bundle, bundle_sha)
                    _, base_pins = checked(manifest['base'], tree, args, 'base-exact', pins, root, deadline)
                    steps.append({'id': 'base-exact', 'treePins': base_pins})
                    delta = manifest['candidateDelta']
                    delta_preconditions(tree, delta['files'], delta['overlays'])
                    steps.append({'id': 'delta-preimages'})
                    (applier or apply_candidate)(tree, delta['patches'], delta['overlays'], root, delta['files'], applied=applied)
                    steps.append({'id': 'isolated-apply', 'commit': delta['commit'],
                                  'patches': [{'name': p['name'], 'sha256': p['sha256']} for p in delta['patches']]})
                    deadline = clock() + PIN_SECONDS
                label = 'final-exact' if binding['branch'] == 'verifyBaseApplyCandidate' else 'state_refused'
                result, final_pins = checked(manifest, tree, args, label, pins, root, deadline)
                steps.append({'id': label if label == 'final-exact' else 'verifyIntegrated', 'treePins': final_pins})
                report['classification'] = {'branch': binding['branch'], 'files': len(result['files']),
                                            'requiredOverlays': len(result['overlays'])}
                write_once(receipts/'classification.json', result)
                out = run_root/'out'
                targets = compile_targets(manifest, out)
                env = contained_env(run_root)
                leaked = environment_problems(env, plan['inputs'])
                if leaked:
                    raise Refused('lunch-inputs: the lunch environment passes ' + ', '.join(leaked))
                try:
                    report['lunchInputs'] = (inputs_check or lunch_input_checks)(tree, plan['inputs'], root,
                                                                                  clock() + LUNCH_INPUT_SECONDS,
                                                                                  exact=plan.get('exact'))
                except (OSError, ValueError) as error:
                    raise Refused(f'lunch-inputs: {type(error).__name__} while reading the copy') from None
                report['lunchInputs']['bounds'] = {'seconds': LUNCH_INPUT_SECONDS}
                report['adevtoolGit'] = {}
                (fetcher or adevtool_public_fetch)(tree, plan, run_root, report['adevtoolGit'], root, stage=stage)
                code, stopped, ended = compiler(lunch_argv(binding['lunch']), env, str(tree), LUNCH_SECONDS, str(out),
                                                stage=stage, label='lunch')
                report['lunch'] = {'exit': code, 'stoppedBy': stopped, 'sessionEnded': ended}
                confirmed(ended, 'lunch')
                if code != 0 or stopped:
                    report['result'] = 'incomplete'
                    report['incomplete'] = 'lunch did not succeed; m was not run'
                    status = 0
                    return status
                code, stopped, ended = compiler(compile_argv(binding['lunch'], targets), env, str(tree), COMPILE_SECONDS,
                                                str(out), stage=stage, label='m')
                report['compile'] = {'exit': code, 'stoppedBy': stopped, 'sessionEnded': ended, 'targets': [TARGET, *[
                    str(Path(t).relative_to(out.parent)) for t in targets[1:]]]}
                confirmed(ended, 'm')
                report['sourceProof'] = source_proof(out, manifest)
                report['classpathProof'] = classpath_proof(out)
                report['flags'] = flag_report(out, manifest)
                passed = (code == 0 and not stopped and report['sourceProof']['ok'] and report['classpathProof']['ok']
                          and all(f['ok'] for f in report['flags']))
                report['result'] = 'compiled-and-proven' if passed else 'incomplete'
                status = 0
        except Refused as error:
            record_refusal(report, error, applied)
        except Exception as error:
            record_refusal(report, Refused(f'stage_error: {type(error).__name__}; see stage receipt for containment'), applied)
            if hasattr(error, 'stage_status'):
                report['stageFailure'] = error.stage_status
        except Interrupted as error:
            record_interruption(report, error, applied)
            status = 3
        finally:
            late = sys.exc_info()[1]  # an interrupt that came while a refusal was being recorded is leaving now
            if isinstance(late, Interrupted) and 'interrupted' not in report:
                if isinstance(late.__context__, Refused) and 'refused' not in report:
                    record_refusal(report, late.__context__, applied)
                record_interruption(report, late, applied)
            write_report(receipts/'report.json', report, applied)
    except Interrupted as error:
        if not os.path.lexists(receipts/'report.json'):
            record_interruption(report, error, applied)
            write_report(receipts/'report.json', report, applied)
        raise
    finally:
        os.close(run_lock)

    return status


def confirmed(ended, label):
    """A stage whose session could not be confirmed gone stops the run before anything else runs or reads its outputs."""
    if not ended:
        raise Refused(f'containment: {label}\'s session could not be confirmed ended, so nothing further ran and no '
                      'output was read; the shared lock is released with it unconfirmed (receipts/stage.json still '
                      'names it, and finish ends it or removes nothing)')


def record_refusal(report, error, applied):
    report['refused'] = str(error)
    report['nothingApplied'] = not applied  # no candidate patch or overlay write started in the copy
    if applied:
        report['applied'] = list(applied)  # in order, each write that started (the last may have failed)
    report['result'] = 'refused'


def record_interruption(report, error, applied):
    # stages: each stage this stopped and whether its session was confirmed gone (finish ends one that was not)
    report['interrupted'] = {'signal': str(error), 'stages': error.stages,
                             'by': 'the outer timeout or a cancel reaching this helper through the exec\'d timeout'}
    report['nothingApplied'] = not applied
    if applied:
        report['applied'] = list(applied)
    report.setdefault('result', 'interrupted')  # a result already reached stays; the interruption is beside it


def write_report(path, report, applied):
    """report.json, whole: a signal while it is written waits until it is (deferred). The first signal, arriving just
    as it is due, is recorded in it before it is written (every later signal is dropped), and then raised."""
    try:
        with deferred():
            write_once(path, report)
    except Interrupted as late:
        if not os.path.lexists(path):
            record_interruption(report, late, applied)
            write_once(path, report)
        raise


@contextlib.contextmanager
def interrupts(signals=INTERRUPT_SIGNALS):
    """While run runs: the first of these signals (the outer timeout's TERM; a cancel's SIGINT or SIGTERM, which the
    step's exec'd timeout forwards; SIGHUP; SIGQUIT) raises Interrupted where run is. While a stage runs, or the report
    is written, it is held (deferred): the stage takes it within POLL_SECONDS, ends its session and raises it, and the
    report raises it once whole. Every later signal is caught and dropped by a Python handler, never SIG_IGN, which a
    process started later would inherit; the outer timeout's KILL stays the backstop. The previous handlers and state
    are restored afterwards."""
    def drop(signum, frame):
        pass

    def stop(signum, frame):
        for sig in signals:
            signal.signal(sig, drop)
        name = signal.Signals(signum).name
        if _INTERRUPT['deferring']:
            _INTERRUPT['pending'] = name
            return
        _INTERRUPT['raised'] = True
        raise Interrupted(name)
    saved = dict(_INTERRUPT)
    _INTERRUPT.update(deferring=0, pending=None, raised=False)
    previous = {sig: signal.signal(sig, stop) for sig in signals}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            if handler is not None:
                signal.signal(sig, handler)
        _INTERRUPT.update(saved)


def wait_for_run(receipts, seconds, clock=time.monotonic, sleep=time.sleep):
    """(fd holding run's lock, waited seconds) once run has released it, within seconds; (None, seconds) while run still
    holds it. A run root with no lock file (run ended before taking it) is not waited for: (-1, 0)."""
    try:
        fd = os.open(Path(receipts)/RUN_LOCK, os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError:
        return -1, 0
    start = clock()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd, round(clock() - start)
        except BlockingIOError:
            if clock() - start >= seconds:
                os.close(fd)
                return None, seconds
            sleep(1)


def end_recorded(receipts, clock=time.monotonic, sleep=time.sleep, grace=None, members=None):
    """{stage, sid, ended} for the session receipts/stage.json names, ended if it outlived run (TERM with CONT, then
    KILL, to each of its process groups, rescanned every POLL_SECONDS, TERM once per group); None without a record. A record run
    marked ended, or a recorded ID that now names a process started at another time (someone else's), is never
    signalled. A record that names no stage session (sid 1 or below) or cannot be read is never confirmed ended."""
    grace = KILL_GRACE_SECONDS if grace is None else grace
    members = members or session_members
    try:
        data, _ = read_nofollow(Path(receipts)/STAGE_RECORD)
        record = json.loads(data)
        sid, label, started = int(record['sid']), str(record['stage']), record['startTime']
        marked = record.get('ended') is True
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {'stage': None, 'sid': None, 'ended': False, 'why': 'the stage record cannot be read'}
    if sid <= 1:
        return {'stage': label, 'sid': sid, 'ended': False, 'why': 'the stage record names no stage session'}
    stat = proc_stat(Path('/proc')/str(sid))
    if stat is not None and (not isinstance(started, int) or isinstance(started, bool) or started <= 0):
        return {'stage': label, 'sid': sid, 'ended': False, 'why': 'the leader start time was not recorded'}
    if stat is not None and stat[3] != started:
        return {'stage': label, 'sid': sid, 'ended': True, 'why': 'its ID now names another process'}
    if marked:
        gone = session_gone(sid, members, sleep)
        return {'stage': label, 'sid': sid, 'ended': gone,
                'why': 'run confirmed it ended; rechecked' if gone else 'ended mark could not be confirmed'}
    for sig in (signal.SIGTERM, signal.SIGKILL):
        start, told = clock(), set()
        while True:
            live = members(sid)
            if live == {} and session_gone(sid, members, sleep):
                return {'stage': label, 'sid': sid, 'ended': True}
            if live:
                targets = live
                if sig == signal.SIGTERM:
                    targets = {pid: pgid for pid, pgid in live.items() if pgid not in told}
                    told.update(targets.values())
                signal_session(targets, sig)
            if clock() - start >= grace:
                break
            sleep(POLL_SECONDS)
    return {'stage': label, 'sid': sid, 'ended': session_gone(sid, members, sleep)}


def finish(binding, run_id, attempt, upload_dir, rmtree=shutil.rmtree, clock=time.monotonic, sleep=time.sleep,
           wait=FINISH_LOCK_SECONDS):
    """After run, always. First it waits up to wait seconds for run's lock (receipts/run.lock): while run still holds
    it nothing is removed. Then it ends a recorded stage session that outlived run (receipts/stage.json), and if that
    session cannot be confirmed gone nothing is removed either. Then it copies only the allowlisted receipts (UPLOAD,
    regular files, never a link) into a new upload directory and removes this run's own work directories (WORK_DIRS:
    the copied tree, OUT_DIR, HOME, TMPDIR) under its run root, never anything else; receipts stay on the builder.
    Writes cleanup.json (removed, absent, left, run, stage) beside the uploads. A missing run root (the run never
    started) only records that."""
    run_root = task_root(binding, run_id, attempt)
    upload = Path(upload_dir)
    upload.mkdir(mode=0o700)
    report = {'schema': 'roseos.launcher3-compile-cleanup.v1', 'runRoot': str(run_root), 'uploaded': [], 'removed': [],
              'absent': [], 'left': []}
    if run_root.is_symlink() or (run_root.exists() and not run_root.is_dir()):
        report['left'].append({'path': str(run_root), 'reason': 'the run root is not a plain directory; nothing touched'})
    elif not run_root.exists():
        report['absent'].append(str(run_root))
    else:
        receipts = run_root/'receipts'
        lock, waited, state = None, 0, 'no plain receipts directory'
        if not receipts.is_symlink() and receipts.is_dir():
            try:
                lock, waited = wait_for_run(receipts, wait, clock, sleep)
                state = {-1: 'absent', None: 'held'}.get(lock, 'released')
            except OSError as error:  # a run.lock that cannot be opened or locked: run may still hold it
                lock, state = None, f'unreadable ({type(error).__name__})'
        report['run'] = {'lock': state, 'waitedSeconds': waited}
        safe = lock is not None
        if not safe:
            report['left'].append({'path': '.', 'reason': f'run\'s lock is {state} after {waited} s, so run may still '
                                   'use the tree; nothing removed'})
        else:
            try:
                report['stage'] = end_recorded(receipts, clock, sleep)
            except (OSError, ValueError, IndexError) as error:
                report['stage'] = {'stage': None, 'sid': None, 'ended': False, 'why': f'{type(error).__name__} while '
                                   'ending it'}
            if report['stage'] is not None and not report['stage']['ended']:
                safe = False
                report['left'].append({'path': '.', 'reason': 'a recorded stage session outlived run and could not be '
                                       'confirmed ended; nothing removed'})
        for name in UPLOAD:
            source = receipts/name
            if source.is_file() and not source.is_symlink() and not receipts.is_symlink():
                try:
                    data, _ = read_nofollow(source)
                    write_bytes_once(upload/name, data)
                    report['uploaded'].append(name)
                except OSError as error:
                    report['left'].append({'path': f'receipts/{name}', 'reason': f'not uploaded ({type(error).__name__})'})
        for name in WORK_DIRS if safe else ():
            target = run_root/name
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                report['left'].append({'path': name, 'reason': 'not a plain directory'})
            elif not target.exists():
                report['absent'].append(name)
            else:
                try:
                    rmtree(target)
                    report['removed'].append(name)
                except OSError as error:
                    report['left'].append({'path': name, 'reason': type(error).__name__})
        if lock not in (None, -1):
            os.close(lock)
    write_once(upload/'cleanup.json', report)
    return 0 if not report['left'] else 3


def write_bytes_once(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('check', 'run', 'finish'))
    p.add_argument('--run-id')
    p.add_argument('--attempt')
    p.add_argument('--upload-dir')
    p.add_argument('--binding', default=str(BINDING), help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    try:
        launcher3_lane.check_context(os.environ)
        manifest, binding, plan = check(a.binding)
        if a.command == 'check':
            if a.run_id or a.attempt or a.upload_dir:
                raise Invalid('check takes no other option')
            prepare = PREPARE_SECONDS + KILL_GRACE_SECONDS + (2 if binding['branch'] == 'verifyBaseApplyCandidate' else 1) * PIN_SECONDS
            # every lunch is the contract's (definitions.lunch): its inputs and the admitted fetch always come first
            outer = (prepare + LUNCH_INPUT_SECONDS + ADEVTOOL_FETCH_SECONDS + KILL_GRACE_SECONDS + LUNCH_SECONDS
                     + KILL_GRACE_SECONDS + COMPILE_SECONDS + KILL_GRACE_SECONDS + 600)
            finish_outer = FINISH_SECONDS + KILL_GRACE_SECONDS
            print(f'outer_seconds={outer}\nfinish_seconds={FINISH_SECONDS}\njob_minutes={-(-(outer + finish_outer) // 60) + 10}')
            return 0
        launcher3_lane.worker(binding, os.environ, a.run_id, a.attempt)
        if a.command == 'finish':
            if not a.upload_dir or not os.path.isabs(a.upload_dir):
                raise Invalid('finish needs an absolute --upload-dir')
            return finish(binding, a.run_id, a.attempt, a.upload_dir)
        if a.upload_dir:
            raise Invalid('run takes no --upload-dir')
        with interrupts():
            return run(binding, manifest, a.run_id, a.attempt, plan=plan)
    except Interrupted as error:  # before run's report exists, or after it is whole
        print(json.dumps({'verdict': 'INTERRUPTED', 'signal': str(error)}), file=sys.stderr)
        return 3
    except Unbound as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'error': str(error)}), file=sys.stderr)
        return 4
    except (Invalid, Refused, FileExistsError, KeyError, ValueError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
