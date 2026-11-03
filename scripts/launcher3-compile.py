#!/usr/bin/env python3
"""LAUNCHER3-COMPILE adapter (Release-owned): a compile-only proof of RoseOS's Launcher3 changes against OS Product's
input contract os/compile-contracts/launcher3-quickstep-lib.json (#410/#440). It reads the contract as data only and never
loads OS Product's checker (scripts/launcher3-compile-contract.py), which B's tests use for parity.

  check                       hosted and on the builder: the contract is the pinned version (MANIFEST_SHA256), the
                              binding os/build-only/launcher3-compile-host.json is exact, and every input the selected
                              branch needs is bound. Exit 4 while any is null. Reads no host path.
  run --run-id --attempt      under the nonblocking shared builder lock, into a new per-run root
                              <taskParent>/launcher3-compile-<run>-<attempt>/{tree,out,home,tmp,receipts}:
                              1. byte-copy the bound source tree (read only; alpha exclusion policy: outputs, .repo,
                                 .git, keys, releases), refused before writing when the selection projects above
                                 SOURCE_BOUND_BYTES or available minus the projection is below RESERVE_BYTES;
                              2. check the copy for the bound branch, refusing at the first failure by name:
                                 verifyIntegrated - every affected file equals its post-image (config.xml only through
                                 the contract's rewrite rules with the bound policy receipt and frankel's applyArgs),
                                 every required overlay is present at its sha256 and every other path of the three
                                 projects matches its tree pin (Git blob IDs recomputed from the copied bytes), with no
                                 extra path; nothing is applied;
                                 verifyBaseApplyCandidate - base-exact (the bound Infra bundle report equals
                                 base.bundleContent and the copy equals the base images and base tree pins),
                                 delta-preimages (every delta file and overlay at its before image), isolated-apply (the
                                 digest-checked edge patch with exactly the contract's git apply steps and the candidate
                                 overlays, in the copy only, never the source tree), final-exact (as verifyIntegrated);
                                 reconstruct - needs an approved genuine-Git-object source, none is bound: refused;
                                 anything else (mixed, pre-images, unknown hashes, missing or extra files) - refused,
                                 nothing is reset;
                              3. lunch and `m -j8 Launcher3QuickStepLib <flag caches>` with OUT_DIR, HOME, TMPDIR and
                                 XDG_* inside the run root, no ccache and no GIT_*; sampled stops (every SAMPLE_SECONDS)
                                 on the output filesystem's growth (OUTPUT_STOP_BYTES) and the process group's resident
                                 memory (MEMORY_STOP_BYTES), and a wall-clock COMPILE_SECONDS; TERM, then KILL;
                              4. a sanitized report.json: the branch, the classification, the bound inputs, the compile
                                 outcome, the source-list and classpath proof and both aconfig flag values. Never a raw
                                 log, never current bytes as provenance.

What a passing report shows: the patched handlers and overlays type-check and compile on the real classpath, and the
generated aconfig values. Not R8, manifest merge, APK resources, packaging, signing or runtime behaviour. The sampled
stops can overshoot and are not hard caps; containment is by paths and environment only (no cgroup or quota exists).
Bounds are first-measurement ceilings proposed by Infra, not measurements. Exit status: 0 report written, 2 invalid,
3 refused, 4 unbound.
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
import signal
import stat
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import alpha_source  # noqa: E402  (the reviewed copy plan and per-file copy)

MANIFEST = ROOT/'os/compile-contracts/launcher3-quickstep-lib.json'
# OS Product's reviewed contract version (schema 2, #446 253fd98). A different manifest is refused until this pin is updated.
MANIFEST_SHA256 = '69d7ccd59174c9a85aad1e0c9dc6da77a287f9276d5dc3f3cdc68ae0f6801a71'
CONDITIONS = ['base-exact', 'delta-preimages', 'isolated-apply', 'final-exact']
APPLY_STEPS = ['git -C <copy>/<project> apply --check -p<strip> <bundlePath>', 'git -C <copy>/<project> apply -p<strip> <bundlePath>']
BLOB_ARGV = ['git', 'hash-object', '-t', 'blob', '--no-filters', '--stdin']
LISTING_BYTES = 64 << 20
PIN_SECONDS = 30 * 60
BINDING = ROOT/'os/build-only/launcher3-compile-host.json'
BINDING_KEYS = {'schemaVersion', 'kind', 'basis', 'host', 'lockFrom', 'taskParent', 'branch', 'sourceTree', 'lunch',
                'policyReceipt', 'bundleReport', 'reconstructSource'}
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


def load_listing(pin, root=ROOT):
    """{path bytes: (mode, oid)} of the pinned commit's tree minus exactly unlisted.excludedPaths, from the committed
    gzip listing after both of its sha256 bindings; raw `<mode> blob <oid> TAB <path> LF` lines only (no quoting, no
    other type or mode). This is the pin's expected data, never a statement about host bytes."""
    listing, unlisted = pin['listing'], pin['unlisted']
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


def apply_candidate(tree, patches, overlays, bundle, files, git='git', run=subprocess.run):
    """Apply the digest-bound candidate delta to the isolated copy only: each patch checked against its sha256, then
    exactly the contract's application steps, `git -C <copy>/<project> apply --check -p<strip> <bundlePath>` and the
    same without --check (no other option: no --3way, --reject, --recount, reduced context or whitespace handling), the
    patched files' copied modes restored (git apply rewrites them under the umask), and each overlay installed from the
    bundle after its sha256 check. The caller checks the final images."""
    tree, bundle = Path(tree), Path(bundle)
    modes = {item['path']: stat.S_IMODE(os.lstat(tree/item['path']).st_mode) for item in files}
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'GIT_CEILING_DIRECTORIES': str(tree), 'HOME': '/nonexistent',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'LC_ALL': 'C'}
    for patch in patches:
        source = bundle/patch['bundlePath']
        if sha256_file(source) != patch['sha256']:
            raise Refused(f'isolated-apply: {patch["bundlePath"]} is not the bound candidate patch')
        for check in (['--check'], []):
            result = run([git, '-C', str(tree/patch['project']), 'apply', *check, f'-p{patch["strip"]}', str(source)],
                         capture_output=True, env=env)
            if result.returncode:
                raise Refused(f'isolated-apply: git apply {" ".join(check)} of {patch["bundlePath"]} failed in {patch["project"]}')
    for path, mode in modes.items():
        if stat.S_IMODE(os.lstat(tree/path).st_mode) != mode:
            os.chmod(tree/path, mode)
    for item in overlays:
        source, target = bundle/item['bundlePath'], tree/item['target']
        data = read_bytes(source)
        if data is None or sha256_bytes(data) != item['after']:
            raise Refused(f'isolated-apply: {item["bundlePath"]} is not the bound candidate overlay')
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


def contract_shape(manifest):
    """The parts of the v2 contract this adapter implements, bound exactly: the four verifyBaseApplyCandidate
    condition IDs, the application steps, the blob hash invocation, sha1 objects and the pinned modes (a subset of the three), the config.xml
    rules, and a candidate bundle that lists exactly the delta's patch and overlay sources."""
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
    sources = {p['bundlePath']: p['sha256'] for p in delta['patches']}
    sources.update({o['bundlePath']: o['after'] for o in delta['overlays']})
    if {b['path']: b['sha256'] for b in delta['bundle']} != sources or len(delta['bundle']) != len(sources):
        raise Invalid('candidateDelta.bundle must list exactly the delta patch and overlay sources with their sha256')
    for patch in delta['patches']:
        if not isinstance(patch['strip'], int) or isinstance(patch['strip'], bool) or not 0 <= patch['strip'] <= 9:
            raise Invalid(f'{patch["name"]}: strip must be a small integer')
        if patch['project'] not in {p['path'] for p in manifest['projects']}:
            raise Invalid(f'{patch["name"]}: the delta patch project is not a contract project')


def load_manifest(path=MANIFEST, pinned=MANIFEST_SHA256):
    data = Path(path).read_bytes()
    if sha256_bytes(data) != pinned:
        raise Invalid(f'{Path(path).name} is not the reviewed contract version {pinned[:12]}')
    manifest = json.loads(data)
    if (manifest.get('schemaVersion'), manifest.get('kind'), manifest.get('target')) != (2, 'rose-launcher3-compile-inputs', TARGET):
        raise Invalid('the contract is not a rose-launcher3-compile-inputs v2 for Launcher3QuickStepLib')
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


def lock_path(root=ROOT):
    return json.loads((root/'os/release-pipeline/frankel.json').read_text())['host']['lockPath']


def check(binding_path=BINDING, root=ROOT, manifest_path=MANIFEST):
    """(manifest, binding). Invalid on a wrong shape, Unbound while the selected branch lacks an input."""
    manifest = load_manifest(manifest_path)
    binding = json.loads(Path(binding_path).read_text())
    if set(binding) != BINDING_KEYS or binding['schemaVersion'] != 2 or binding['kind'] != 'rose-launcher3-compile-host':
        raise Invalid('the binding is not an exact rose-launcher3-compile-host v2 record')
    if binding['host'] != 'immortal-os-builder' or binding['lockFrom'] != 'os/release-pipeline/frankel.json host.lockPath':
        raise Invalid('the binding must name the existing builder and its shared lock')
    if binding['taskParent'] != '/home/pixelbuild/rose-actions':
        raise Invalid('the run root parent must be /home/pixelbuild/rose-actions')
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
    if binding['lunch'] is not None and not re.fullmatch(r'[a-z0-9_]+-[a-z0-9]+-(user|userdebug|eng)', str(binding['lunch'])):
        raise Invalid('lunch must be <product>-<release>-<variant>')
    frankel_policy_args(root)
    needed = ['sourceTree', 'lunch']
    if binding['branch'] == 'reconstruct':
        needed += ['reconstructSource']
    else:
        needed += ['policyReceipt.path', 'policyReceipt.sha256']
    if binding['branch'] == 'verifyBaseApplyCandidate':
        needed += ['bundleReport.path', 'bundleReport.sha256']
    values = {'sourceTree': binding['sourceTree'], 'lunch': binding['lunch'], 'policyReceipt.path': receipt['path'],
              'policyReceipt.sha256': receipt['sha256'], 'bundleReport.path': bundle['path'],
              'bundleReport.sha256': bundle['sha256'], 'reconstructSource': binding['reconstructSource']}
    unbound = [n for n in needed if values[n] is None]
    if unbound:
        raise Unbound(f'{binding["branch"]} needs inputs not yet bound in {Path(binding_path).name}: {", ".join(unbound)}')
    if binding['branch'] == 'reconstruct':
        raise Unbound('reconstruct needs an approved genuine-Git-object source; this adapter has none wired')
    return manifest, binding


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

def group_rss(pgid, proc=Path('/proc')):
    """Resident bytes of every process in a process group (sampled; may miss short-lived processes)."""
    total, page = 0, os.sysconf('SC_PAGE_SIZE')
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        with contextlib.suppress(OSError, ValueError, IndexError):
            stat = (entry/'stat').read_text()
            if int(stat[stat.rindex(')') + 2:].split()[2]) == pgid:
                total += int((entry/'statm').read_text().split()[1]) * page
    return total


def used_bytes(path):
    fs = os.statvfs(path)
    return (fs.f_blocks - fs.f_bfree) * fs.f_frsize


def supervised(argv, env, cwd, seconds, out_dir, popen=subprocess.Popen, clock=time.monotonic, sleep=time.sleep,
               rss=group_rss, used=used_bytes):
    """(exit, stoppedBy or None). Own session; every SAMPLE_SECONDS the output filesystem's growth and the group's RSS
    are compared with their stop thresholds; a stop or the timeout ends the group with TERM, then KILL."""
    proc = popen(argv, env=env, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    start_used, deadline, stopped = used(out_dir), clock() + seconds, None
    while proc.poll() is None:
        if clock() > deadline:
            stopped = 'timeout'
        elif used(out_dir) - start_used > OUTPUT_STOP_BYTES:
            stopped = 'output'
        elif rss(proc.pid) > MEMORY_STOP_BYTES:
            stopped = 'memory'
        if stopped:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, sig)
                end = clock() + KILL_GRACE_SECONDS
                while proc.poll() is None and clock() < end:
                    sleep(1)
                if proc.poll() is not None:
                    break
            break
        sleep(SAMPLE_SECONDS)
    return proc.wait(), stopped


def contained_env(run_root, path=os.environ.get('PATH', '/usr/bin:/bin')):
    env = {'PATH': path, 'HOME': str(run_root/'home'), 'TMPDIR': str(run_root/'tmp'), 'OUT_DIR': str(run_root/'out'),
           'XDG_CACHE_HOME': str(run_root/'home/.cache'), 'XDG_CONFIG_HOME': str(run_root/'home/.config'),
           'XDG_DATA_HOME': str(run_root/'home/.local/share'), 'GRADLE_USER_HOME': str(run_root/'home/.gradle'),
           'USE_CCACHE': '0', 'LANG': 'C.UTF-8'}
    return env


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


def task_root(binding, run_id, attempt):
    for label, value in (('run id', run_id), ('attempt', attempt)):
        if not (isinstance(value, str) and value.isdigit() and value[0] != '0' and len(value) <= 20):
            raise Invalid(f'a positive numeric {label} is required')
    return Path(binding['taskParent'])/TASK_NAME.format(run_id=run_id, attempt=attempt)


def copy_tree(source, destination, root=ROOT, statvfs=os.statvfs, planner=alpha_source.plan_copy,
              copier=alpha_source.copy_tree, seconds=PREPARE_SECONDS):
    """The reviewed alpha copy (plan, then alpha_source.copy_tree, which also rebases links) of the selection.
    Refused before writing on plan problems, the source bound or the reserve; a wall-clock timer stops the copy at
    `seconds` (the partial tree stays inside the run root and is reported, never used)."""
    policy = json.loads((root/'os/release-pipeline/cuttlefish-alpha.json').read_text())['preparation']['exclusions']
    plan = planner(source, policy)
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
    return {'entries': len(plan['entries']), 'excluded': len(plan['excluded']), 'projectedBytes': projected,
            'availableBytesBefore': available}


def bound_json(path):
    """(document, sha256 of its bytes) of a bound host receipt; read without following a final symlink."""
    data, _ = read_nofollow(path)
    return json.loads(data), sha256_bytes(data)


def bundle_matches(report, report_sha256, bound_sha256, content):
    """base-exact's bundle half: the Infra-reported integrating bundle (its bound sha256) states exactly the contract's
    base.bundleContent: the same patches per project in order, the same overlays with their sha256 and the same
    notAnInput overlay targets. Which bundle integrated the tree is Infra provenance; a hash of current bytes is not."""
    if not bound_sha256 or report_sha256 != bound_sha256:
        raise Refused('base-exact: the bundle report is missing or not the one bound for this tree')
    if report != content:
        raise Refused('base-exact: the integrating bundle does not match base.bundleContent')


def checked(section, tree, args, label, pins, root, deadline):
    result = classify(tree, section, *args)
    if result['branch'] != 'exact':
        raise Refused(f'{label}: ' + '; '.join(result['problems'][:5]))
    return result, pins(tree, section['projects'], root, deadline)


def run(binding, manifest, run_id, attempt, root=ROOT, lock=None, copier=copy_tree, compiler=supervised,
        policy_loader=None, bundle_loader=None, pins=tree_pins, clock=time.monotonic):
    """The adapter run; returns its exit status and leaves receipts/report.json naming each step's outcome."""
    run_root = task_root(binding, run_id, attempt)
    if Path(binding['taskParent']).is_symlink() or not Path(binding['taskParent']).is_dir():
        raise Refused('the run root parent must be an existing plain directory')
    run_root.mkdir(mode=0o700)
    receipts = run_root/'receipts'
    receipts.mkdir(mode=0o700)
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
    status = 3
    try:
        with (lock or shared_lock)(lock_path(root)):
            for name in ('home', 'tmp', 'out'):
                (run_root/name).mkdir(mode=0o700)
            tree = run_root/'tree'
            report['copy'] = copier(Path(binding['sourceTree']), tree)
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
                bundle_matches(bundle, bundle_sha, binding['bundleReport']['sha256'], manifest['base']['bundleContent'])
                _, base_pins = checked(manifest['base'], tree, args, 'base-exact', pins, root, deadline)
                steps.append({'id': 'base-exact', 'treePins': base_pins})
                delta = manifest['candidateDelta']
                delta_preconditions(tree, delta['files'], delta['overlays'])
                steps.append({'id': 'delta-preimages'})
                apply_candidate(tree, delta['patches'], delta['overlays'], root, delta['files'])
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
            code, stopped = compiler(compile_argv(binding['lunch'], targets), contained_env(run_root),
                                     str(run_root/'tree'), COMPILE_SECONDS, str(out))
            report['compile'] = {'exit': code, 'stoppedBy': stopped, 'targets': [TARGET, *[
                str(Path(t).relative_to(out.parent)) for t in targets[1:]]]}
            report['sourceProof'] = source_proof(out, manifest)
            report['classpathProof'] = classpath_proof(out)
            report['flags'] = flag_report(out, manifest)
            passed = (code == 0 and not stopped and report['sourceProof']['ok'] and report['classpathProof']['ok']
                      and all(f['ok'] for f in report['flags']))
            report['result'] = 'compiled-and-proven' if passed else 'incomplete'
            status = 0
    except Refused as error:
        report['refused'] = str(error)
        report['nothingApplied'] = not any(c['id'] == 'isolated-apply' for c in report.get('conditions', []))
        report['result'] = 'refused'
    finally:
        write_once(receipts/'report.json', report)
    return status


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('check', 'run'))
    p.add_argument('--run-id')
    p.add_argument('--attempt')
    p.add_argument('--binding', default=str(BINDING), help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    try:
        manifest, binding = check(a.binding)
        if a.command == 'check':
            if a.run_id or a.attempt:
                raise Invalid('check takes no other option')
            prepare = PREPARE_SECONDS + KILL_GRACE_SECONDS + (2 if binding['branch'] == 'verifyBaseApplyCandidate' else 1) * PIN_SECONDS
            outer = prepare + COMPILE_SECONDS + KILL_GRACE_SECONDS + 600
            print(f'outer_seconds={outer}\njob_minutes={-(-outer // 60) + 10}')
            return 0
        return run(binding, manifest, a.run_id, a.attempt)
    except Unbound as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'error': str(error)}), file=sys.stderr)
        return 4
    except (Invalid, Refused, FileExistsError, KeyError, ValueError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
