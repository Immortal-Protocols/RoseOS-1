#!/usr/bin/env python3
"""Guarded OS signing-key bootstrap adapter (OCT29-INFRA-S005).

One reviewed profile from os/signing-bootstrap/profiles.json selects what may be bootstrapped: fold-rango-v1 (a tar of
the Fold key files) or models-v0 (OCT30-INFRA-S012: one PKCS12 store, alias rose-models, made with the measured
keytool through its password-file options, its reloaded public listing and certificate checked before import, and the
two secrets rechecked empty immediately before the first add). Both are resource-bound: fold-rango-v1's tools are
proven by the same-run local tool proof and models-v0's keytool by its pinned git tree, recomputed before use; both
refuse while their recorded access is missing or expired. FOLD-FACTORY-IDENTITY adds archive.format "openssh-ed25519"
(one passphrase-encrypted factory-archive SSH key made through scripts/factory_signing.py's pinned askpass route): its
profile fold-rango-factory-v1 is resource-bound, and pro-blazer-factory-v1 stays unbound until its approvals,
containers and secretAccessor exist and its one bootstrap run is approved (the 4 h bootstrap grant is made at action
time). A profile that opts in with access.liveCheck needs no recorded expiries, but must recheck before the import: the
run's credentialed describe and versions-list calls check its access (readiness; the profile's access.policy states
what a later failure costs). A resource-bound profile lists nothing missing. A profile is not a release admission: it admits no device build, and os-release keeps admitting Frankel only.

guard      on the hosted guard job, before the controller starts: the mode, profile and options must be exactly a
           bootstrap of a ready profile, bound to this workflow commit on main from a manual dispatch.
preflight  on the builder before any login: the same profile readiness, the signer identity and the recipe binding.
run        on the builder, under the shared host lock, with the job's short-lived signer login. Order:
           readiness -> no previous attempt -> lock -> tool identities -> secret metadata (exists, no versions) ->
           protected workspace -> generate -> exact output projection -> archive and public identity checks ->
           add keys version -> add passphrase version -> cleanup -> public receipt.
plan       prints the exact later command, metadata operations and the permissions they need. Nothing is executed.

Fail-closed rules. A profile that is unbound, has an incomplete key inventory, an unapproved tool identity or
expiring recorded access stops before any side effect, and so does a failed first metadata check (secret missing,
occupied or unavailable). Access that fails after that check, at the pre-import recheck or as a denied add (which
cannot be tested beforehand), uses up the run. An existing version, unavailable or uncertain metadata, an unsafe archive,
an unexpected output, a public identity equal to Frankel's, or any previous attempt journal stops without adding.
The two-secret import is not atomic: each committed version id is journaled, and an uncertain add or a failure after
the first add leaves recovery-needed, never complete, never retried, rolled back or deleted. The receipt holds only
public identity, resource and version ids, tool identities, statuses and cleanup booleans; payload readback is not
performed. Secret material never enters argv, logs, environment, the receipt, the repository or an Actions artifact.
Transient files live in an owner-only directory (RAM-backed when /dev/shm is writable) that is overwritten and removed;
this is best effort, not guaranteed erasure.

Generation (OCT29-INFRA-S006) runs a reviewed closed-vocabulary recipe (profile generation.recipe) through one mockable
process boundary with fixed argv and a minimal environment, then packs exactly the inventory files as <device>/<file>
into a deterministic keys.tar.gz. Private keys must be encrypted (PKCS#8 DER EncryptedPrivateKeyInfo, encrypted PEM
avb.pem) as scripts/host-decrypt-keys.py expects. The checked-in fold recipe is reviewed, and its tools are proven by
the same-run local tool proof before any login (prove). Tests use mock executors and runners only.
"""
import argparse
import base64
import contextlib
from datetime import datetime, timedelta, timezone
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import host_secrets  # noqa: E402
import factory_signing  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROFILES_PATH = 'os/signing-bootstrap/profiles.json'
PIPELINE_PATH = 'os/release-pipeline/frankel.json'
MODE = 'signing-bootstrap'
RECEIPT_SCHEMA = 'roseos.signing-bootstrap-receipt.v1'
OUTPUTS = ('keys.tar.gz', 'passphrase')
PKCS12_OUTPUTS = ('keystore.p12', 'passphrase')
OPENSSH_OUTPUTS = (factory_signing.KEY_FILE, 'passphrase')


def profile_outputs(profile):
    """The exact workspace projection a generator must leave: a tar of key files (Fold), one PKCS12 store (Models) or
    one encrypted OpenSSH key (factory-archive signing)."""
    kind = (profile.get('archive') or {}).get('format')
    return PKCS12_OUTPUTS if kind == 'pkcs12' else OPENSSH_OUTPUTS if kind == 'openssh-ed25519' else OUTPUTS
MEMBER = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}')
SECRET_NAME = re.compile(r'[A-Za-z0-9_-]{1,255}')
HEX64 = re.compile(r'[0-9a-f]{64}')
MAX_ARCHIVE_BYTES = 64 << 20
MAX_MEMBERS = 64
# What the collision check needs and which predefined role grants it. The add-only (secretVersionAdder) and accessor
# (secretAccessor) grants on these secrets do not include either permission, so metadata stays unavailable until an
# owner grants them; this adapter never changes IAM.
METADATA_OPERATIONS = (
    {'operation': 'gcloud secrets describe {secret} --project {project} --format=json', 'permission': 'secretmanager.secrets.get',
     'grantedBy': 'roles/secretmanager.viewer'},
    {'operation': 'gcloud secrets versions list {secret} --project {project} --filter=state!=DESTROYED --format=json',
     'permission': 'secretmanager.versions.list', 'grantedBy': 'roles/secretmanager.viewer'},
)
ADD_OPERATION = {'operation': 'gcloud secrets versions add {secret} --project {project} --data-file {file} --format=json',
    'permission': 'secretmanager.versions.add', 'grantedBy': 'roles/secretmanager.secretVersionAdder'}


class Blocked(Exception):
    def __init__(self, code, detail):
        super().__init__(f'{code}: {detail}')
        self.code, self.detail = code, detail


class Unavailable(Exception):
    pass


class Uncertain(Exception):
    """An add whose outcome is unknown (timeout, lost or unparsable response)."""


class AddFailed(Exception):
    """An add that definitively did not create a version."""


def load_profiles(root=ROOT):
    return json.loads((root/PROFILES_PATH).read_text())


def parse_time(value):
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def readiness(name, profiles, now):
    """Static reasons this profile cannot run yet. Pure: reads only the checked-in profile."""
    profile = profiles['profiles'].get(name)
    if profile is None:
        return [('profile-unknown', f'{name!r} is not a reviewed profile')]
    if profile.get('status') != 'resource-bound':
        return [('profile-unbound', f'{name} is {profile.get("status")}; missing: ' + '; '.join(profile.get('missing', [])))]
    if profile.get('missing'):  # status alone never admits a profile that still lists unmet prerequisites
        return [('profile-unbound', f'{name} is resource-bound but still lists missing: '
                 + '; '.join(str(m) for m in profile['missing']))]
    reasons = []
    secrets = profile.get('secrets', {})
    if sorted(secrets) != ['keys', 'passphrase'] or not all(SECRET_NAME.fullmatch(str(v)) for v in secrets.values()):
        reasons.append(('profile-secrets', 'exactly a keys and a passphrase secret name are required'))
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', str(profile.get('project'))):
        reasons.append(('profile-project', 'project id is missing or malformed'))
    if not str(profile.get('signer', '')).endswith('.iam.gserviceaccount.com'):
        reasons.append(('profile-signer', 'signer must be a service-account email'))
    inventory = profile.get('keyInventory', {})
    if inventory.get('complete') is not True:
        missing = inventory.get('missing') or ['key inventory not established']
        reasons.append(('inventory-incomplete', '; '.join(m['fact'] if isinstance(m, dict) else str(m) for m in missing)))
    proven = set(PROOF_TOOLS) if profile.get('localProof') else set()
    for tool, identity in sorted(profile.get('tools', {}).items()):
        if identity.get('kind') == 'pinned-tree':
            reasons += [('tool-unapproved', f'{tool}: {problem}') for problem in pinned_tree_problems(identity.get('trust') or {})]
        elif tool in proven:
            continue  # its installed identity comes from the same-run local proof (OCT30-INFRA-S016), never a checked-in hash
        elif not tool_approved(identity):
            reasons.append(('tool-unapproved', f'{tool}: no measured installed path and sha256 ({identity.get("requiredObservation")})'))
    if not profile.get('tools'):
        reasons.append(('tool-unapproved', 'no tool identities'))
    reasons += [('source-unpinned', detail) for detail in source_problems(profile)]
    if profile.get('localProof'):
        reasons += proof_spec_problems(profile)
    missing = (profile.get('generation') or {}).get('missing') or []
    if missing:
        reasons.append(('recipe-missing', '; '.join(m['fact'] if isinstance(m, dict) else str(m) for m in missing)))
    if (profile.get('archive') or {}).get('format') != 'openssh-ed25519':  # fixed generate_openssh, no recipe vocabulary
        reasons += [('recipe-missing' if code == 'missing' else 'recipe-invalid', detail)
            for code, detail in recipe_problems(profile)]
    else:
        reasons += openssh_problems(profile, now)
    access = profile.get('access', {})
    margin = timedelta(seconds=access.get('minimumRemainingSeconds', 1800))
    live = access.get('liveCheck') is True  # opted in: access is checked live by the run (owner decision 2026-11-03)
    if live and profile.get('recheckEmptyBeforeImport') is not True:
        # Without recorded expiries there is no margin: the pre-import recheck is the last access and emptiness check
        # before any add, so a live-check profile must make it.
        reasons.append(('profile-invalid', 'access.liveCheck needs recheckEmptyBeforeImport true'))
    for key in ('addVersionExpires', 'accessorExpires'):
        if live and access.get(key) is None:
            continue  # checked live by the run's first credentialed metadata calls, before any key (access.policy)
        try:
            if parse_time(access[key]) - margin <= now:
                reasons.append(('access-expired', f'{key} {access[key]} is within {margin} of {now:%Y-%m-%dT%H:%M:%SZ}'))
        except (KeyError, TypeError, ValueError):
            reasons.append(('access-unknown', f'{key} is missing or malformed'))
    return reasons


# ---------------------------------------------------------------- local tool proof (OCT30-INFRA-S016)
# The Fold tools have no checked-in installed identity. The Models Product helper named by profile.localProof proves them
# locally: validate_spec(spec) -> problem codes (pure guard), verify_local(spec, root) -> closed proof or Refused(code),
# recheck(proof, root) -> Refused on drift. Only the exact admitted helper blob is loaded; the excluded custom-crypto
# helper (PR #190) is refused even when present on main.

PROOF_TOOLS = ('generator', 'decrypt-keys', 'avbtool', 'openssl')
PROOF_FILE = 'fold-tool-proof.json'
PROOF_BINDING_SCHEMA = 'roseos.fold-tool-proof-binding.v1'
RUNNER_ENVIRONMENT = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}  # subprocess_runner's fixed environment
CODE = re.compile(r'[a-z][a-z0-9_-]{0,59}')


def _helper_module(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('fold_tool_proof_admitted', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_proof_helper(profile, root=ROOT):
    """The admitted helper module, or Blocked with a fixed code. Admission is the exact git blob of a reviewed merge."""
    local = profile.get('localProof') or {}
    path = root/str(local.get('helper', ''))
    try:
        blob = git_blob_id(path, time.monotonic() + 60, time.monotonic)
    except OSError:
        raise Blocked('proof-helper-missing', f'{local.get("helper")} is not present') from None
    if blob in {str(e.get('gitBlob')) for e in local.get('excluded', [])}:
        raise Blocked('proof-helper-excluded', f'{local.get("helper")} is an excluded helper revision (blob {blob[:12]})')
    if not SHA1.fullmatch(str(local.get('admittedRevision'))) or not SHA1.fullmatch(str(local.get('admittedBlob'))):
        raise Blocked('proof-helper-unadmitted', 'no exact tested helper merge is admitted')
    if blob != local['admittedBlob']:
        raise Blocked('proof-helper-mismatch', f'{local.get("helper")} blob {blob[:12]} is not the admitted {local["admittedBlob"][:12]}')
    return _helper_module(path), blob


def proof_spec_problems(profile):
    """Static guard: an admitted helper and a spec it accepts (no observation-only installed fields needed)."""
    local = profile['localProof']
    try:
        helper, _ = load_proof_helper(profile)
    except Blocked as error:
        return [(error.code, error.detail)]
    if not isinstance(local.get('spec'), dict):
        return [('proof-spec-missing', 'profile.localProof.spec is not set')]
    try:
        codes = helper.validate_spec(local['spec'])
    except Exception as error:  # noqa: BLE001 - a helper fault is a refusal, never a pass
        return [('proof-spec-invalid', f'validate_spec failed ({type(error).__name__})')]
    return [('proof-spec-invalid', str(c) if CODE.fullmatch(str(c)) else 'unrecognised-code') for c in codes or []]


def proof_subject(name, profile, profiles):
    """(subject, problem). The admitted helper proves the tool identities of the profile its spec pins
    (localProof.spec.profile; the helper's validate_spec accepts only its own subject), and every proof names that
    subject. A profile other than the subject may consume such a proof only when its localProof and its whole tool
    set are exactly the subject's checked-in ones, so the proof covers precisely the tools this profile runs. Anything
    else, including no reviewed profiles to compare with, is 'proof-subject'."""
    subject = ((profile.get('localProof') or {}).get('spec') or {}).get('profile')
    if subject == name:
        return subject, None
    reviewed = ((profiles or {}).get('profiles') or {}).get(subject) if isinstance(subject, str) else None
    if not isinstance(reviewed, dict) or reviewed.get('localProof') != profile.get('localProof') \
            or reviewed.get('tools') != profile.get('tools'):
        return subject, 'proof-subject'
    return subject, None


def proof_problems(name, profile, proof, profiles=None):
    """The closed proof fields this integration consumes: every fixed tool with an absolute path and sha256, the subject
    this profile's admitted spec pins (proof_subject), and exactly the runner's fixed minimal environment. The run,
    attempt, recipe and helper binding are checked separately (bind_proof) and always name this profile."""
    if not isinstance(proof, dict) or not proof.get('schema') or not isinstance(proof.get('tools'), dict):
        return ['proof-malformed']
    problems = []
    subject, problem = proof_subject(name, profile, profiles)
    if problem:
        problems.append(problem)
    if not isinstance(subject, str) or proof.get('profile') != subject:
        problems.append('proof-foreign')
    if sorted(proof['tools']) != sorted(PROOF_TOOLS):
        problems.append('proof-tools')
    for tool in PROOF_TOOLS:
        entry = proof['tools'].get(tool) or {}
        if not approved_path(entry.get('path')) or not HEX64.fullmatch(str(entry.get('sha256'))):
            problems.append('proof-tools')
    if proof.get('environment') != RUNNER_ENVIRONMENT:
        problems.append('proof-environment')
    return list(dict.fromkeys(problems))


def _code(error):
    code = str(getattr(error, 'code', '') or error)
    return code if CODE.fullmatch(code) else 'proof-refused'


def prove(name, profiles, env, recipe_sha, proof_dir, lock_path, root='/', now=None):
    """Before any login, under the shared lock: the admitted helper proves the four tools on this host; the bounded,
    nonsecret proof is bound to this profile, recipe, run and attempt. Returns (status, codes)."""
    profile = profiles['profiles'].get(name, {})
    if not profile.get('localProof'):
        return 'not-required', []
    failures = preflight(name, profiles, env, recipe_sha, now or datetime.now(timezone.utc))
    if failures:
        return 'blocked', [f.split(':', 1)[0] for f in failures]
    try:
        helper, blob = load_proof_helper(profile)
    except Blocked as error:
        return 'blocked', [error.code]
    lock = shared_lock(lock_path)
    try:
        lock.__enter__()
    except Blocked as error:
        return 'blocked', [error.code]
    except OSError:
        return 'blocked', ['lock-unavailable']
    try:
        proof = helper.verify_local(profile['localProof']['spec'], root=root)
    except helper.Refused as error:
        return 'refused', [_code(error)]
    except Exception:  # noqa: BLE001 - a helper fault is a refusal, never a proof
        return 'refused', ['proof-helper-fault']
    finally:
        lock.__exit__(None, None, None)
    problems = proof_problems(name, profile, proof, profiles)
    if problems:
        return 'refused', problems
    binding = {'schema': PROOF_BINDING_SCHEMA, 'profile': name, 'recipeSha': recipe_sha, 'runId': env.get('GITHUB_RUN_ID'),
        'runAttempt': env.get('GITHUB_RUN_ATTEMPT'), 'helperBlob': blob,
        'helperRevision': profile['localProof']['admittedRevision'],
        'proofSha256': hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest(), 'proof': proof}
    Path(proof_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(Path(proof_dir)/PROOF_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(binding, f, sort_keys=True)
    return 'proven', []


def bind_proof(name, profile, binding, recipe_sha, run_identity, root='/', profiles=None):
    """Inside the lock, before any secret metadata: only this run's own proof, rechecked by the helper now. Returns a
    run-scoped profile whose tools use the proven paths (the checked-in profile is never rewritten) and receipt rows.
    `profiles` (the reviewed set) lets a profile that reuses another profile's admitted spec prove its identity."""
    if not isinstance(binding, dict):
        raise Blocked('proof-missing', 'no same-run local tool proof')
    helper, blob = load_proof_helper(profile)
    proof = binding.get('proof')
    expected = {'schema': PROOF_BINDING_SCHEMA, 'profile': name, 'recipeSha': recipe_sha, 'runId': run_identity[0],
        'runAttempt': run_identity[1], 'helperBlob': blob}
    if any(binding.get(k) != v for k, v in expected.items()) or \
            binding.get('proofSha256') != hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest():
        raise Blocked('proof-binding', 'the proof is not this run\'s, this recipe\'s and this helper\'s')
    problems = proof_problems(name, profile, proof, profiles)
    if problems:
        raise Blocked(problems[0], 'the bound proof does not name the fixed tools, subject and environment')
    try:
        helper.recheck(proof, root=root)
    except helper.Refused as error:
        raise Blocked('proof-drift', _code(error)) from None
    bound = json.loads(json.dumps(profile))
    rows = {}
    for tool in PROOF_TOOLS:
        entry = proof['tools'][tool]
        bound['tools'][tool] = dict(bound['tools'][tool], kind='proof-bound', installed={'path': entry['path'], 'sha256': entry['sha256']})
        rows[tool] = {'trustBasis': 'local-proof', 'path': entry['path'], 'sha256': entry['sha256']}
    return bound, rows, {'proofSha256': binding['proofSha256'], 'helperBlob': blob, 'helperRevision': binding.get('helperRevision'),
        'proof': proof}


SHA1 = re.compile(r'[0-9a-f]{40}')
TREE_PART = re.compile(r'[A-Za-z0-9_.+-]{1,128}')
MANIFEST_PATH = 'os/pinned-manifest.xml'


def tool_approved(identity):
    """A tool may run when its trust basis is complete: a measured installed (path, sha256) (legacy mode) or a pinned
    git tree (OCT30-INFRA-S012) that is recomputed locally before use."""
    if identity.get('kind') == 'pinned-tree':
        return not pinned_tree_problems(identity.get('trust') or {})
    installed = identity.get('installed') or {}
    return approved_path(installed.get('path')) and bool(HEX64.fullmatch(str(installed.get('sha256'))))


def tool_path(identity):
    if identity.get('kind') == 'pinned-tree':
        trust = identity['trust']
        return str(Path(trust['root'])/trust['treePath']/trust['tool'])
    return identity['installed']['path']


def manifest_revision(path, root=ROOT):
    match = re.search(rf'<project [^>]*path="{re.escape(path)}"[^>]*revision="([0-9a-f]{{40}})"', (root/MANIFEST_PATH).read_text())
    return match[1] if match else None


def pinned_tree_problems(trust):
    """Static checks of a pinned-git-tree trust basis: full ids, safe relative paths, and the revision equal to the
    checked-in pinned manifest's revision for that project path."""
    problems = []
    if trust.get('basis') != 'pinned-git-tree':
        problems.append('trust basis is not pinned-git-tree')
    for key in ('revision', 'treeId', 'toolBlob'):
        if not SHA1.fullmatch(str(trust.get(key))):
            problems.append(f'trust.{key} is not a full git object id')
    if not approved_path(trust.get('root')):
        problems.append('trust.root is not an absolute path')
    for key in ('treePath', 'tool'):
        parts = str(trust.get(key, '')).split('/')
        if not all(TREE_PART.fullmatch(p) and p not in ('.', '..') for p in parts):
            problems.append(f'trust.{key} is not a safe relative path')
    if trust.get('projectPath') and manifest_revision(trust['projectPath']) != trust.get('revision'):
        problems.append(f'trust.revision is not the pinned-manifest revision of {trust.get("projectPath")}')
    if not trust.get('projectPath'):
        problems.append('trust.projectPath is missing')
    return problems


def git_blob_id(path, end, clock):
    size = os.lstat(path).st_size
    digest = hashlib.sha1(b'blob %d\0' % size)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            if clock() > end:
                raise Blocked('tool-identity', 'tree verification exceeded its time bound')
            digest.update(block)
    return digest.hexdigest()


def git_tree_id(directory, seconds=600, max_entries=20000, clock=time.monotonic):
    """The git tree object id of a local directory, computed the way git does (modes 100644/100755/120000/40000,
    names sorted with trees as name + '/', empty directories omitted). No index, .git metadata or host checksum list is
    read. A symlink anywhere above the directory, a symlink target that is absolute or leaves the tree, or any special
    file refuses."""
    base = Path(directory)
    if os.path.realpath(base) != str(base) or not base.is_dir():
        raise Blocked('tool-identity', f'{base} is not a plain directory (symlinked ancestor or missing)')
    end, count = clock() + seconds, [0]

    def tree(path):
        entries = []
        for entry in os.scandir(path):
            count[0] += 1
            if count[0] > max_entries or clock() > end:
                raise Blocked('tool-identity', 'tree verification exceeded its entry or time bound')
            if not TREE_PART.fullmatch(entry.name):
                raise Blocked('tool-identity', f'unexpected name {entry.name[:40]!r}')
            info = os.lstat(entry.path)
            if stat.S_ISDIR(info.st_mode):
                sub = tree(entry.path)
                if sub is not None:
                    entries.append((entry.name + '/', '40000', entry.name, sub))
            elif stat.S_ISREG(info.st_mode):
                mode = '100755' if info.st_mode & stat.S_IXUSR else '100644'
                entries.append((entry.name, mode, entry.name, git_blob_id(entry.path, end, clock)))
            elif stat.S_ISLNK(info.st_mode):
                target = os.readlink(entry.path)
                resolved = os.path.normpath(os.path.join(os.path.dirname(entry.path), target))
                if os.path.isabs(target) or not (resolved + '/').startswith(str(base) + '/'):
                    raise Blocked('tool-identity', f'symlink {entry.name} leaves the pinned tree')
                data = target.encode()
                entries.append((entry.name, '120000', entry.name, hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()))
            else:
                raise Blocked('tool-identity', f'special file {entry.name} in the pinned tree')
        if not entries:
            return None
        body = b''.join(f'{mode} {name}'.encode() + b'\0' + bytes.fromhex(oid) for _, mode, name, oid in sorted(entries))
        return hashlib.sha1(b'tree %d\0' % len(body) + body).hexdigest()
    return tree(base) or hashlib.sha1(b'tree 0\0').hexdigest()


def source_problems(profile):
    """Every source-script tool must carry its pinned source identity (repository, path, 40-hex revision and git blob,
    sha256 of the bytes). A source identity never stands in for the installed identity."""
    problems = []
    for tool, identity in sorted(profile.get('tools', {}).items()):
        if identity.get('kind') != 'source-script':
            continue
        source = identity.get('source') or {}
        if not (source.get('path') and source.get('repository') and SHA1.fullmatch(str(source.get('revision')))
                and SHA1.fullmatch(str(source.get('gitBlob'))) and HEX64.fullmatch(str(identity.get('sourceSha256')))):
            problems.append(f'{tool}: source identity is not pinned (repository, path, revision, gitBlob, sourceSha256)')
    return problems


def verify_source(identity, data):
    """Compare source bytes with a recorded identity ({gitBlob, sha256} or a tool's source/sourceSha256)."""
    blob = identity.get('gitBlob') or (identity.get('source') or {}).get('gitBlob')
    digest = identity.get('sha256') or identity.get('sourceSha256')
    observed_blob = hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()
    problems = []
    if observed_blob != blob:
        problems.append(f'git blob {observed_blob} is not the pinned {blob}')
    if hashlib.sha256(data).hexdigest() != digest:
        problems.append(f'sha256 {hashlib.sha256(data).hexdigest()} is not the pinned {digest}')
    return problems


def approved_path(path):
    return isinstance(path, str) and path.startswith('/') and '..' not in PurePosixPath(path).parts and '\0' not in path


# Closed recipe vocabulary for generate_keys. A step names an approved tool and a list of arguments; each argument is
# exactly one placeholder or a plain literal. Nothing else (no shell, path, environment or command text) is accepted.
LITERAL = re.compile(r'[A-Za-z0-9_.,:=+@-][A-Za-z0-9_.,:=+@-]{0,127}')
SUBJECT = re.compile(r'(/[A-Za-z]{1,3}=[A-Za-z0-9 ._-]{1,64}){1,8}/?')
STEP_KEYS = {'tool', 'args', 'produces', 'stdin', 'exitStatus', 'removes', 'stdout'}
PLACEHOLDERS = ('{out}', '{passphrase_file}', '{passphrase_file_arg}', '{subject}', '{dname}')
DNAME = re.compile(r'[A-Z]{1,2}=[A-Za-z0-9 ._-]{1,64}(, ?[A-Z]{1,2}=[A-Za-z0-9 ._-]{1,64}){0,7}')
INTERMEDIATE = re.compile(r'[a-z0-9_][a-z0-9_-]*(\.[a-z0-9_]+){1,3}')


def recipe_problems(profile):
    """Pure validation of profile.generation.recipe. Returns [(code, detail)]: ('missing', ...) or ('invalid', ...)."""
    recipe = (profile.get('generation') or {}).get('recipe')
    if recipe is None:
        return [('missing', 'no reviewed generation recipe: ' + str((profile.get('generation') or {}).get('note', '')))]
    inventory = profile.get('keyInventory', {})
    allowed = set(inventory.get('requiredByConsumer', [])) | set(inventory.get('optionalByConsumer', []))
    intermediates = set((profile.get('generation') or {}).get('intermediates') or [])
    public = set((profile.get('generation') or {}).get('publicOutputs') or [])
    if not isinstance(recipe, list) or not recipe:
        return [('invalid', 'recipe must be a non-empty list of steps')]
    problems, produced, live = [], [], set()
    if intermediates & allowed or not all(INTERMEDIATE.fullmatch(str(n)) for n in intermediates):
        problems.append(('invalid', 'generation.intermediates must be plain file names outside the inventory'))
    if public & (allowed | intermediates) or not all(INTERMEDIATE.fullmatch(str(n)) for n in public):
        problems.append(('invalid', 'generation.publicOutputs must be plain file names outside the inventory and intermediates'))
    for index, step in enumerate(recipe):
        if not isinstance(step, dict) or set(step) - STEP_KEYS or not {'tool', 'args', 'produces'} <= set(step):
            problems.append(('invalid', f'step {index}: only tool, args, produces, stdin, stdout, exitStatus and removes are accepted'))
            continue
        if step['tool'] not in profile.get('tools', {}):
            problems.append(('invalid', f'step {index}: tool {step["tool"]!r} is not a profile tool'))
        if step.get('exitStatus', 0) not in (0, 1) or isinstance(step.get('exitStatus', 0), bool):
            problems.append(('invalid', f'step {index}: exitStatus must be 0 or 1 (make_key exits 1 through its EXIT trap)'))
        if step.get('stdin', 'none') not in ('none', 'passphrase'):
            problems.append(('invalid', f'step {index}: stdin must be none or passphrase'))
        for arg in step['args'] if isinstance(step['args'], list) else [None]:
            if not (isinstance(arg, str) and (arg in PLACEHOLDERS
                    or (arg.startswith('{out}/') and arg[6:] in allowed | intermediates | public | {Path(f).stem for f in allowed})
                    or (LITERAL.fullmatch(arg) and '..' not in arg))):
                problems.append(('invalid', f'step {index}: argument {str(arg)[:40]!r} is not a placeholder or plain literal'))
        if not isinstance(step['produces'], list) or not set(step['produces']) <= allowed | intermediates | public:
            problems.append(('invalid', f'step {index}: produces must name inventory files or declared intermediates'))
        elif 'stdout' in step and step['stdout'] not in set(step['produces']) & public:
            problems.append(('invalid', f'step {index}: stdout may only be captured into a public output this step produces'))
        else:
            produced += step['produces']
            live |= set(step['produces']) & intermediates
        removes = step.get('removes', [])
        if not isinstance(removes, list) or not set(removes) <= live:
            problems.append(('invalid', f'step {index}: removes may name only intermediates produced earlier'))
        else:
            live -= set(removes)
    if len(produced) != len(set(produced)):
        problems.append(('invalid', 'a file is produced by more than one step'))
    if live:
        problems.append(('invalid', f'intermediates {sorted(live)} are never removed'))
    missing = sorted(set(inventory.get('requiredByConsumer', [])) - set(produced))
    if missing:
        problems.append(('invalid', f'recipe does not produce {missing}'))
    if any('{dname}' in (step.get('args') or []) for step in recipe if isinstance(step, dict)) and \
            not DNAME.fullmatch(str((profile.get('generation') or {}).get('dname'))):
        problems.append(('invalid', 'generation.dname must be a plain K=value distinguished name'))
    missing_public = sorted(public - set(produced))
    if missing_public:
        problems.append(('invalid', f'public outputs {missing_public} are never produced'))
    if any('{subject}' in (step.get('args') or []) for step in recipe if isinstance(step, dict)) and \
            not SUBJECT.fullmatch(str((profile.get('generation') or {}).get('subject'))):
        problems.append(('invalid', 'generation.subject must be a plain /K=value certificate subject'))
    return problems


def generate_keys(workspace, profile, runner, token=None):
    """Run the reviewed recipe inside the owner-only workspace and leave exactly keys.tar.gz and passphrase.

    runner(tool_path, argv, cwd, stdin_path) returns an exit status; it is the only process boundary and is mocked in
    tests. Tool output is never captured into logs or the receipt."""
    missing = (profile.get('generation') or {}).get('missing') or []
    if missing:
        raise Blocked('recipe-missing', '; '.join(m['fact'] if isinstance(m, dict) else str(m) for m in missing))
    if (profile.get('archive') or {}).get('format') == 'openssh-ed25519':
        return generate_openssh(workspace, profile, token=token)
    problems = recipe_problems(profile)
    if problems:
        raise Blocked('recipe-missing' if problems[0][0] == 'missing' else 'recipe-invalid', problems[0][1])
    for step in profile['generation']['recipe']:
        if not tool_approved(profile['tools'][step['tool']]):
            raise Blocked('tool-unapproved', f'{step["tool"]}: no complete trust basis; nothing is run')
    workspace = Path(workspace)
    passphrase = workspace/'passphrase'
    fd = os.open(passphrase, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write((token or base64.urlsafe_b64encode(os.urandom(36)).decode()) + '\n')
    out = workspace/'out'
    out.mkdir(mode=0o700)
    expected = []
    values = {'{out}': str(out), '{passphrase_file}': str(passphrase), '{passphrase_file_arg}': 'file:' + str(passphrase),
        '{subject}': str((profile.get('generation') or {}).get('subject')), '{dname}': str((profile.get('generation') or {}).get('dname'))}
    for index, step in enumerate(profile['generation']['recipe']):
        tool = tool_path(profile['tools'][step['tool']])
        argv = [values.get(a, str(out/a[6:]) if a.startswith('{out}/') else a) for a in step['args']]
        stdin = str(passphrase) if step.get('stdin') == 'passphrase' else None
        if step.get('stdout'):  # a public listing (keytool -list -v), never key material
            status = runner(tool, argv, str(out), stdin, stdout_path=str(out/step['stdout']))
        else:
            status = runner(tool, argv, str(out), stdin)
        if status != step.get('exitStatus', 0):
            raise Blocked('generator-failed', f'step {index} ({step["tool"]}) exited {status}')
        expected += step['produces']
        for name in step.get('removes', []):
            # An intermediate (such as the unencrypted AVB key before encryption) is overwritten, then removed.
            target = out/name
            if target.is_file() and not target.is_symlink():
                target.write_bytes(b'\0' * target.stat().st_size)
            target.unlink()
            expected.remove(name)
    names = sorted(os.listdir(out))
    if names != sorted(expected):
        raise Blocked('unexpected-output', f'generator wrote {names[:16]}, expected {sorted(expected)}')
    if (profile.get('archive') or {}).get('format') == 'pkcs12':
        return _project_pkcs12(workspace, out, names, profile)
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode='wb', mtime=0) as gz, tarfile.open(fileobj=gz, mode='w', format=tarfile.PAX_FORMAT) as tar:
        for name in names:
            info = os.lstat(out/name)
            if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
                raise Blocked('unexpected-output', f'{name} is not a non-empty regular file')
            member = tarfile.TarInfo(f'{profile["device"]}/{name}')
            member.size, member.mode, member.mtime = info.st_size, 0o600, 0
            with open(out/name, 'rb') as f:
                tar.addfile(member, f)
    for name in names:
        with contextlib.suppress(OSError):
            (out/name).write_bytes(b'\0' * (out/name).stat().st_size)
        (out/name).unlink()
    out.rmdir()
    fd = os.open(workspace/'keys.tar.gz', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(buffer.getvalue())


def _project_pkcs12(workspace, out, names, profile):
    """Verify the reloaded store's public identity from the recipe's public outputs, then leave exactly keystore.p12
    and passphrase in the workspace. Returns the public identity (never key, passphrase or store bytes)."""
    generation = profile['generation']
    cert_name, listing_name = generation['publicOutputs']
    store = out/'keystore.p12'
    for name in names:
        info = os.lstat(out/name)
        if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
            raise Blocked('unexpected-output', f'{name} is not a non-empty regular file')
    if store.read_bytes()[:1] != b'\x30':
        raise Blocked('pkcs12-invalid', 'keystore.p12 is not a DER PKCS12 structure')
    identity = pkcs12_identity((out/cert_name).read_bytes(), (out/listing_name).read_text(errors='replace'), generation)
    for name in generation['publicOutputs']:
        (out/name).unlink()
    fd = os.open(workspace/'keystore.p12', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(store.read_bytes())
    store.write_bytes(b'\0' * store.stat().st_size)
    store.unlink()
    out.rmdir()
    return identity


def _der(data, offset):
    """(tag, value start, value end) of the DER element at offset."""
    tag, length, offset = data[offset], data[offset + 1], offset + 2
    if length & 0x80:
        count = length & 0x7f
        if not 0 < count <= 4:
            raise ValueError('unsupported DER length')
        length, offset = int.from_bytes(data[offset:offset + count], 'big'), offset + count
    if offset + length > len(data):
        raise ValueError('truncated DER')
    return tag, offset, offset + length


def _children(data, start, end):
    items = []
    while start < end:
        tag, vs, ve = _der(data, start)
        items.append((tag, vs, ve))
        start = ve
    return items


def _oid(raw):
    first, rest = divmod(raw[0], 40) if raw[0] < 80 else (2, raw[0] - 80)
    parts, value = [first, rest], 0
    for byte in raw[1:]:
        value = (value << 7) | (byte & 0x7f)
        if not byte & 0x80:
            parts.append(value)
            value = 0
    return '.'.join(map(str, parts))


def _time(data, tag, vs, ve):
    text = data[vs:ve].decode('ascii')
    if tag == 0x17:  # UTCTime YYMMDDHHMMSSZ
        year = int(text[:2])
        text = ('19' if year >= 50 else '20') + text
    return datetime.strptime(text, '%Y%m%d%H%M%SZ').replace(tzinfo=timezone.utc)


def _name(data, vs, ve):
    parts = []
    for _, rs, re_ in _children(data, vs, ve):  # RDN sets
        for _, as_, ae in _children(data, rs, re_):
            (_, os_, oe), (_, ss, se) = _children(data, as_, ae)
            label = {'2.5.4.3': 'CN', '2.5.4.10': 'O', '2.5.4.11': 'OU', '2.5.4.6': 'C'}.get(_oid(data[os_:oe]), _oid(data[os_:oe]))
            parts.append(f'{label}={data[ss:se].decode("utf-8", "replace")}')
    return ', '.join(parts)


SIGNATURE_OIDS = {'1.2.840.113549.1.1.11': 'SHA256withRSA', '1.2.840.113549.1.1.12': 'SHA384withRSA',
    '1.2.840.113549.1.1.5': 'SHA1withRSA'}


def certificate_facts(der):
    """Public facts of an X.509 certificate by minimal DER reading (no signature verification is claimed)."""
    _, cs, ce = _der(der, 0)
    tbs, sig_alg = _children(der, cs, ce)[:2]
    fields = _children(der, tbs[1], tbs[2])
    if fields[0][0] == 0xa0:
        fields = fields[1:]
    _, sig_oid_s, sig_oid_e = _children(der, sig_alg[1], sig_alg[2])[0]
    not_before, not_after = [(_time(der, t, s, e)) for t, s, e in _children(der, fields[3][1], fields[3][2])]
    spki = _children(der, fields[5][1], fields[5][2])
    _, alg_s, alg_e = _children(der, spki[0][1], spki[0][2])[0]
    bits = None
    if _oid(der[alg_s:alg_e]) == '1.2.840.113549.1.1.1':
        _, ks, ke = spki[1]
        _, rs, re_ = _der(der, ks + 1)  # skip the BIT STRING unused-bits byte
        _, ms, me = _children(der, rs, re_)[0]
        modulus = der[ms:me].lstrip(b'\0')
        bits = len(modulus) * 8 - (8 - modulus[0].bit_length())
    return {'signatureAlgorithm': SIGNATURE_OIDS.get(_oid(der[sig_oid_s:sig_oid_e]), _oid(der[sig_oid_s:sig_oid_e])),
        'issuer': _name(der, fields[2][1], fields[2][2]), 'subject': _name(der, fields[4][1], fields[4][2]),
        'keyAlgorithm': 'RSA' if bits else _oid(der[alg_s:alg_e]), 'keySize': bits,
        'validityDays': (not_after - not_before).days, 'notBefore': not_before.strftime('%Y-%m-%dT%H:%M:%SZ')}


def pkcs12_identity(cert_pem, listing, generation):
    """Check the reloaded store: exactly one alias, a PrivateKeyEntry, keystore type PKCS12, the listed SHA-256
    fingerprint equal to the exported certificate, and the certificate's alias/algorithm/subject/validity contract."""
    expected = generation['identity']
    try:
        pem = cert_pem.decode('ascii')
        der = base64.b64decode(''.join(pem.split('-----BEGIN CERTIFICATE-----')[1].split('-----END CERTIFICATE-----')[0].split()),
            validate=True)
        facts = certificate_facts(der)
    except (ValueError, IndexError, UnicodeDecodeError) as error:
        raise Blocked('pkcs12-invalid', f'exported certificate cannot be read ({type(error).__name__})')
    fingerprint = hashlib.sha256(der).hexdigest()
    aliases = re.findall(r'^Alias name: (.+)$', listing, re.M)
    listed = [re.sub(r'[^0-9a-f]', '', m.lower()) for m in re.findall(r'^\s*SHA256: ([0-9A-Fa-f:]+)\s*$', listing, re.M)]
    problems = []
    if not re.search(r'^Keystore type: PKCS12$', listing, re.M):
        problems.append('keystore type is not PKCS12')
    if aliases != [expected['alias']] or not re.search(r'^Your keystore contains 1 entry$', listing, re.M):
        problems.append(f'aliases {aliases} are not exactly [{expected["alias"]}]')
    if not re.search(r'^Entry type: PrivateKeyEntry$', listing, re.M):
        problems.append('the entry is not a PrivateKeyEntry')
    if listed != [fingerprint]:
        problems.append('the listed SHA-256 fingerprint is not the exported certificate')
    for key in ('keyAlgorithm', 'keySize', 'signatureAlgorithm', 'subject', 'validityDays'):
        if facts[key] != expected[key]:
            problems.append(f'{key} {facts[key]!r} is not {expected[key]!r}')
    if facts['issuer'] != facts['subject']:
        problems.append('the certificate is not self-issued')
    if problems:
        raise Blocked('pkcs12-identity', '; '.join(problems))
    return {'certificateSha256': fingerprint, 'alias': expected['alias'], **{k: facts[k] for k in
        ('keyAlgorithm', 'keySize', 'signatureAlgorithm', 'subject', 'validityDays', 'notBefore')}}


# Approved-proposal ceilings for the factory-archive identity's recorded IAM expiries. They are checked only against
# expiries the profile records from an IAM readback; a null expiry is never treated as either duration. It is
# access-unknown unless the profile opts in with access.liveCheck, where the run's credentialed calls check access.
OPENSSH_BOOTSTRAP_MAX = timedelta(hours=4)
OPENSSH_ACCESSOR_MAX = timedelta(days=30)


def openssh_problems(profile, now):
    """Closed shape of an openssh-ed25519 profile, plus the ceilings on any recorded expiry. Nothing is invented: a
    missing tool identity or public identity stays a refusal, and so does a missing expiry unless the profile opts in
    with access.liveCheck (readiness)."""
    problems = []
    if not factory_signing.PRINCIPAL.fullmatch(str(profile.get('principal'))):
        problems.append(('profile-invalid', 'an openssh-ed25519 profile needs a plain principal'))
    inventory = profile.get('keyInventory') or {}
    if inventory.get('requiredByConsumer') != [factory_signing.KEY_FILE] or inventory.get('optionalByConsumer'):
        problems.append(('profile-invalid', 'keyInventory must be exactly id_ed25519'))
    if sorted(profile.get('tools') or {}) != ['ssh-keygen'] or (profile['tools']['ssh-keygen'].get('kind') != 'installed'):
        problems.append(('profile-invalid', 'tools must be exactly a measured installed ssh-keygen'))
    if profile.get('recheckEmptyBeforeImport') is not True:
        problems.append(('profile-invalid', 'recheckEmptyBeforeImport must be true'))
    if profile.get('localProof') or (profile.get('generation') or {}).get('recipe') is not None:
        problems.append(('profile-invalid', 'no local proof or recipe: generation is the fixed generate_openssh'))
    # With access.liveCheck (owner decision, 2026-11-03), access is checked live, not from a recorded timestamp: after the
    # signer login and under the lock, the run's metadata calls on both secrets use the same credentials and stop it
    # (metadata-unavailable, which with the gcloud executor also covers a missing secret, or secret-occupied) before any
    # key is generated or journaled, whatever ended
    # the access (expiry, revocation, a missing get or list grant). They are repeated before the import, where a failure,
    # like a denied versions.add (which cannot be tested beforehand), uses up the run (failed-before-commit or
    # recovery-needed). Without recorded expiries the 4 h / 30 d ceilings are the IAM conditions' alone. Expiries are then
    # optional; any that are recorded are still held to the approved ceilings here, need a basis, and must leave the
    # 30-minute margin (readiness).
    access = profile.get('access') or {}
    recorded = {}
    for key in ('addVersionExpires', 'accessorExpires'):
        if access.get(key) is not None:
            try:
                recorded[key] = parse_time(access[key])
            except (TypeError, ValueError):
                return problems  # readiness reports the malformed value as access-unknown
    if recorded and not access.get('basis'):
        problems.append(('access-unknown', 'recorded expiries need the IAM readback basis'))
    add, accessor = recorded.get('addVersionExpires'), recorded.get('accessorExpires')
    if (add and add - now > OPENSSH_BOOTSTRAP_MAX) or (accessor and accessor - now > OPENSSH_ACCESSOR_MAX) \
            or (add and accessor and add > accessor):
        problems.append(('access-window', f'recorded expiries exceed the approved {OPENSSH_BOOTSTRAP_MAX} bootstrap / '
                         f'{OPENSSH_ACCESSOR_MAX.days} d accessor ceilings or are out of order'))
    return problems


def generate_openssh(workspace, profile, runner=None, token=None):
    """Factory-archive signing key: no recipe vocabulary; exactly factory_signing.generate_key, after the measured
    ssh-keygen identity is approved. The passphrase reaches ssh-keygen only through the pinned askpass helper."""
    identity = (profile.get('tools') or {}).get('ssh-keygen') or {}
    if not tool_approved(identity):
        raise Blocked('tool-unapproved', 'ssh-keygen: no measured installed path and sha256; nothing is run')
    runner = runner or factory_signing.run_bounded
    try:
        public = factory_signing.generate_key(workspace, tool_path(identity), runner, token=token)
    except factory_signing.Refused as error:
        raise Blocked('generator-failed', str(error)) from None
    try:
        checked = factory_signing.self_check(workspace, tool_path(identity), public, profile['principal'], runner)
    except factory_signing.Refused as error:
        raise Blocked('selfcheck-failed', str(error)) from None
    return dict(public, principal=profile['principal'], namespace=factory_signing.NAMESPACE, selfCheck=checked)


def subprocess_runner(tool, argv, cwd, stdin_path, timeout=900, stdout_path=None):
    """Production process boundary: fixed argv, minimal environment, nothing captured into logs. stdout goes to an
    owner-only file only for a declared public listing."""
    env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'HOME': cwd}
    with contextlib.ExitStack() as stack:
        stdin = stack.enter_context(open(stdin_path, 'rb') if stdin_path else open(os.devnull, 'rb'))
        stdout = subprocess.DEVNULL
        if stdout_path:
            stdout = stack.enter_context(os.fdopen(os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600), 'wb'))
        try:
            return subprocess.run([tool, *argv], cwd=cwd, env=env, stdin=stdin, stdout=stdout,
                stderr=subprocess.DEVNULL, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            return 'timeout'


def guard(env, profiles, now):
    """Hosted guard: the dispatch must be exactly a bootstrap of a ready profile. Returns failure strings."""
    failures = []
    if env.get('MODE') != MODE:
        return ['not the signing-bootstrap mode']
    expected = {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main', 'ROSE_DEVICE': 'frankel',
        'PROMOTE': 'false', 'APP_PUBLISH': 'false', 'ALPHA_RECOVER': 'false', 'RESUME_STAGE': 'none'}
    failures += [f'{k} must be {v}' for k, v in expected.items() if env.get(k) != v]
    failures += [f'{k} must be empty' for k in ('RESUME_SOURCE_RUN', 'RESUME_UNSIGNED_SHA256', 'RESUME_SIGNED_SHA256',
        'REUSE_BINDING') if env.get(k)]
    if not re.fullmatch(r'[0-9a-f]{40}', env.get('CANDIDATE_SHA', '')) or env.get('CANDIDATE_SHA') != env.get('GITHUB_SHA'):
        failures.append('candidate must be this workflow commit')
    failures += [f'{code}: {detail}' for code, detail in readiness(env.get('BOOTSTRAP_PROFILE', 'none'), profiles, now)]
    return failures


def preflight(name, profiles, env, recipe_sha, now):
    failures = [f'{code}: {detail}' for code, detail in readiness(name, profiles, now)]
    profile = profiles['profiles'].get(name, {})
    if env.get('GCP_SIGNER_SERVICE_ACCOUNT') != profile.get('signer'):
        failures.append('GCP_SIGNER_SERVICE_ACCOUNT is not the profile signer')
    if recipe_sha != env.get('GITHUB_SHA') or not re.fullmatch(r'[0-9a-f]{40}', recipe_sha or ''):
        failures.append('recipe must be this workflow commit')
    return failures


def certificate_sha256(pem):
    body = pem.split('-----BEGIN CERTIFICATE-----')[1].split('-----END CERTIFICATE-----')[0]
    return hashlib.sha256(base64.b64decode(''.join(body.split()), validate=True)).hexdigest()


def encrypted_pkcs8_der(data):
    """EncryptedPrivateKeyInfo is SEQUENCE { SEQUENCE algorithm, OCTET STRING }; a plain PrivateKeyInfo starts with
    INTEGER version. scripts/host-decrypt-keys.py requires the encrypted form (openssl pkcs8 -inform DER -passin)."""
    if len(data) < 4 or data[0] != 0x30:
        return False
    length = data[1]
    offset = 2 + (length & 0x7f if length & 0x80 else 0)
    return len(data) > offset and data[offset] == 0x30


def inspect_archive(path, profile):
    """Validate the archive without extracting it and return its public identity. Private members are never read."""
    inventory = profile['keyInventory']
    allowed = set(inventory['requiredByConsumer']) | set(inventory.get('optionalByConsumer', []))
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise Blocked('archive-unsafe', 'archive exceeds the size bound')
    try:
        with tarfile.open(path, 'r:gz') as tar:
            members = tar.getmembers()
            if len(members) > MAX_MEMBERS:
                raise Blocked('archive-unsafe', 'too many members')
            names, files = set(), {}
            for member in members:
                parts = PurePosixPath(member.name).parts
                if (member.name.startswith('/') or '..' in parts or not (member.isfile() or member.isdir())
                        or member.name in names or not all(MEMBER.fullmatch(p) for p in parts) or len(parts) > 2):
                    raise Blocked('archive-unsafe', f'unexpected member {member.name[:80]!r}')
                names.add(member.name)
                if member.isfile():
                    files[member.name] = member
            dirs = {str(PurePosixPath(n).parent) for n in files}
            if len(dirs) != 1:
                raise Blocked('archive-unsafe', 'key files must share one directory')
            base = PurePosixPath(dirs.pop())
            if str(base) != profile.get('device'):
                raise Blocked('archive-unsafe', f'key directory {str(base)[:40]!r} is not the profile device')
            present = {PurePosixPath(n).name for n in files}
            missing = sorted(set(inventory['requiredByConsumer']) - present)
            extra = sorted(present - allowed)
            if missing or extra:
                raise Blocked('archive-inventory', f'missing {missing}, unexpected {extra}')

            def read(name):
                with tar.extractfile(files[str(base/name)]) as f:
                    return f.read(1 << 20)
            for name in sorted(present):
                if name.endswith('.pk8') and not encrypted_pkcs8_der(read(name)):
                    raise Blocked('archive-unencrypted', f'{name} is not an encrypted PKCS#8 DER key')
            if b'-----BEGIN ENCRYPTED PRIVATE KEY-----' not in read('avb.pem'):
                raise Blocked('archive-unencrypted', 'avb.pem is not an encrypted PEM private key')
            cert = certificate_sha256(read('releasekey.x509.pem').decode('ascii'))
            avb = hashlib.sha256(read('avb_pkmd.bin')).hexdigest()
    except Blocked:
        raise
    except (tarfile.TarError, OSError, EOFError, UnicodeDecodeError, ValueError, IndexError) as error:
        raise Blocked('archive-unsafe', f'archive cannot be validated ({type(error).__name__})')
    return {'otaCertificateSha256': cert, 'avbPublicKeySha256': avb, 'members': sorted(present)}


def frankel_identities(root=ROOT):
    pipeline = json.loads((root/PIPELINE_PATH).read_text())
    return json.loads((root/pipeline['versionBaselines']['installedEvidence']['identities']).read_text())


@contextlib.contextmanager
def shared_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Blocked('lock-held', f'{path} is held by another job')
        yield
    finally:
        os.close(fd)


def write_journal(path, record):
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(record, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


RECEIPT_KEYS = {'schema', 'profile', 'device', 'project', 'recipeSha', 'status', 'reasons', 'secrets', 'publicIdentity',
    'tools', 'cleanup', 'payloadReadback', 'recoveryNeeded', 'operations', 'localProof'}


def public_receipt(receipt, private_values=()):
    """Allowlisted projection; refuses to emit anything that contains a private value."""
    out = {k: receipt[k] for k in RECEIPT_KEYS if k in receipt}
    text = json.dumps(out, sort_keys=True)
    for value in private_values:
        forms = [value] if isinstance(value, str) else [value.decode('utf-8', 'replace'), value.hex()]
        if len(value) >= 4 and any(form in text for form in forms):
            raise Blocked('receipt-leak', 'receipt would contain private material')
    return out


def run(name, profiles, executor, state_dir, lock_path, recipe_sha, now, workspace=None, frankel=None, proof=None,
        run_identity=(None, None), proof_root='/'):
    """The bootstrap state machine. Returns (receipt, private_values_for_redaction_check)."""
    profile = profiles['profiles'].get(name, {})
    receipt = {'schema': RECEIPT_SCHEMA, 'profile': name, 'device': profile.get('device'), 'project': profile.get('project'),
        'recipeSha': recipe_sha, 'status': 'blocked', 'reasons': [], 'recoveryNeeded': False,
        'payloadReadback': 'not performed', 'tools': {}, 'operations': [],
        'secrets': {role: {'name': secret, 'committedVersion': None, 'state': 'untouched'}
            for role, secret in profile.get('secrets', {}).items()},
        'cleanup': {'workspaceCreated': False, 'workspaceRemoved': None}}
    private = []

    def stop(code, detail, status='blocked'):
        receipt['reasons'].append({'code': code, 'detail': detail})
        receipt['status'] = status
        return receipt, private

    reasons = readiness(name, profiles, now)
    if reasons:
        for code, detail in reasons:
            receipt['reasons'].append({'code': code, 'detail': detail})
        return receipt, private
    if profile.get('localProof') and not isinstance(proof, dict):
        return stop('proof-missing', 'no same-run local tool proof; nothing is locked, read or created')
    journal = Path(state_dir)/f'{name}.json'
    if journal.exists() or journal.is_symlink():
        try:
            previous = json.loads(journal.read_text()).get('status')
        except (OSError, ValueError):
            previous = 'unreadable'
        receipt['recoveryNeeded'] = previous != 'complete'
        return stop('previous-attempt', f'journal {journal.name} records {previous}; no regeneration or duplicate add')
    try:
        with shared_lock(lock_path):
            if profile.get('localProof'):
                profile, receipt['tools'], receipt['localProof'] = bind_proof(name, profile, proof, recipe_sha, run_identity,
                    proof_root, profiles)
            return _locked(name, profile, executor, journal, receipt, private, stop, workspace,
                frankel or frankel_identities())
    except Blocked as error:
        return stop(error.code, error.detail, receipt['status'] if receipt['status'] != 'blocked' else 'blocked')


def _locked(name, profile, executor, journal, receipt, private, stop, workspace, frankel):
    for tool, identity in sorted(profile['tools'].items()):
        if identity.get('kind') == 'pinned-tree':
            problem = pinned_tree_mismatch(executor, identity['trust'])
            if problem:
                return stop('tool-identity', f'{tool}: {problem}')
            trust = identity['trust']
            receipt['tools'][tool] = {'trustBasis': 'pinned-git-tree', 'repository': trust.get('repository'),
                'revision': trust['revision'], 'treeId': trust['treeId'], 'toolBlob': trust['toolBlob']}
            continue
        installed = identity['installed']
        observed = executor.tool_sha256(installed['path'])
        if observed != installed['sha256']:
            return stop('tool-identity', f'{tool} sha256 {observed} is not the measured {installed["sha256"]}')
        if identity.get('kind') == 'proof-bound':
            continue  # the receipt row is the proof's; the executor re-hash above must agree with it
        receipt['tools'][tool] = {'installedSha256': observed, 'source': identity.get('source')}
    project = profile['project']
    for role, secret in sorted(profile['secrets'].items()):
        try:
            state = executor.secret_state(project, secret)
        except Unavailable as error:
            needs = ', '.join(op['permission'] for op in METADATA_OPERATIONS)
            return stop('metadata-unavailable', f'{secret}: {error}; collision check needs {needs}')
        if not isinstance(state, dict) or state.get('exists') not in (True, False) or not isinstance(state.get('versions'), list):
            return stop('metadata-uncertain', f'{secret}: metadata response is not exact')
        if not state['exists']:
            return stop('secret-missing', f'{secret} does not exist; the adapter never creates secrets')
        if state['versions']:
            return stop('secret-occupied', f'{secret} already has versions {state["versions"]}; nothing is added')
    factory = workspace or (lambda: host_secrets.private_directory('rose-bootstrap-'))
    try:
        manager = factory()
        created = Path(manager.__enter__())
    except OSError as error:
        return stop('workspace-unavailable', f'protected workspace could not be created ({type(error).__name__})')
    receipt['cleanup']['workspaceCreated'] = True
    outcome = None
    try:
        outcome = _inside(name, profile, executor, journal, receipt, private, stop, created, frankel)
    finally:
        try:
            manager.__exit__(*sys.exc_info())
            removed = not os.path.lexists(created)
        except OSError:
            removed = False
        receipt['cleanup']['workspaceRemoved'] = removed
        if not removed:
            receipt['reasons'].append({'code': 'cleanup-failed', 'detail': 'workspace removal failed or it still exists'})
            if receipt['status'] == 'complete':
                receipt['status'] = 'cleanup-failed'
    return outcome


def pinned_tree_mismatch(executor, trust):
    """Recompute the local tree and the tool blob; any difference from the pins is a refusal reason."""
    observed = executor.tool_tree(trust)
    if not isinstance(observed, dict):
        return 'pinned tree could not be verified'
    if observed.get('treeId') != trust['treeId']:
        return f'local {trust["treePath"]} tree {observed.get("treeId")} is not the pinned {trust["treeId"]}'
    if observed.get('toolBlob') != trust['toolBlob']:
        return f'local {trust["tool"]} blob {observed.get("toolBlob")} is not the pinned {trust["toolBlob"]}'
    return None


def _inside(name, profile, executor, journal, receipt, private, stop, created, frankel):
    info = os.lstat(created)
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
        return stop('workspace-unprotected', 'workspace must be an owner-only directory')
    if profile_outputs(profile) == OPENSSH_OUTPUTS:
        problem = factory_signing.workspace_problem(created)
        if problem:  # before the journal: nothing is generated or recorded, so the approved run is not used up
            return stop('workspace-noexec', problem)
    # Runtime revalidation: pinned tool trees are recomputed again immediately before anything runs.
    for tool in sorted({step['tool'] for step in profile['generation'].get('recipe') or []}):
        identity = profile['tools'][tool]
        if identity.get('kind') == 'pinned-tree':
            problem = pinned_tree_mismatch(executor, identity['trust'])
            if problem:
                return stop('tool-identity', f'{tool} changed before generation: {problem}')
    write_journal(journal, {'profile': name, 'status': 'generating'})
    generated = executor.generate(created, profile)
    outputs = profile_outputs(profile)
    names = sorted(os.listdir(created))
    if names != sorted(outputs):
        return stop('unexpected-output', f'generator outputs {names[:16]} are not exactly {list(outputs)}', 'failed')
    for output in outputs:
        info = os.lstat(created/output)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            return stop('unexpected-output', f'{output} is not an owner-only regular file', 'failed')
    passphrase = (created/'passphrase').read_bytes()
    private.extend([passphrase, passphrase.strip()])
    if not passphrase.strip():
        return stop('passphrase-empty', 'passphrase output is empty', 'failed')
    if outputs == OPENSSH_OUTPUTS:
        try:
            header = factory_signing.inspect_private(created/factory_signing.KEY_FILE)
        except factory_signing.Refused as error:
            return stop('unexpected-output', str(error), 'failed')
        if not isinstance(generated, dict) or generated.get('publicKey') != header['publicKey'] \
                or generated.get('principal') != profile.get('principal'):
            return stop('unexpected-output', 'the reported public identity is not the generated key', 'failed')
        if generated.get('selfCheck') != {'decrypt': True, 'signVerify': True, 'negativeRefused': True}:
            return stop('selfcheck-failed', 'no complete decrypt/sign/verify self-check before import; nothing added', 'failed')
        identity = dict(header, principal=profile['principal'], namespace=factory_signing.NAMESPACE,
                        selfCheck=generated['selfCheck'])
    elif outputs == PKCS12_OUTPUTS:
        try:
            identity = verified_pkcs12(generated, created/'keystore.p12', profile)
        except Blocked as error:
            return stop(error.code, error.detail, 'failed')
        existing = {frankel.get('otaCertificateSha256'), frankel.get('appSignerSha256')} | {
            app.get('signerSha256') for app in frankel.get('apps', []) if isinstance(app, dict)}
        if identity['certificateSha256'] in existing - {None}:
            return stop('identity-collision', 'certificate equals an existing Frankel/Agent identity; keys are never copied', 'failed')
    else:
        try:
            identity = inspect_archive(created/'keys.tar.gz', profile)
        except Blocked as error:
            return stop(error.code, error.detail, 'failed')
        if identity['otaCertificateSha256'] == frankel.get('otaCertificateSha256') or \
                identity['avbPublicKeySha256'] == frankel.get('avbPublicKeySha256'):
            return stop('identity-collision', 'public identity equals the Frankel identity; keys are never copied', 'failed')
    receipt['publicIdentity'] = identity
    if profile.get('recheckEmptyBeforeImport'):
        for role, secret in sorted(profile['secrets'].items()):
            try:
                state = executor.secret_state(profile['project'], secret)
            except Unavailable as error:
                write_journal(journal, {'profile': name, 'status': 'failed-before-commit', 'publicIdentity': identity})
                return stop('metadata-unavailable', f'{secret}: {error} at the pre-import recheck; nothing added', 'failed')
            if not isinstance(state, dict) or state.get('exists') is not True or state.get('versions') != []:
                write_journal(journal, {'profile': name, 'status': 'failed-before-commit', 'publicIdentity': identity})
                return stop('secret-occupied', f'{secret} is no longer an existing empty secret at the pre-import recheck; nothing added', 'failed')
    write_journal(journal, {'profile': name, 'status': 'importing', 'publicIdentity': identity})
    for role in ('keys', 'passphrase'):
        secret = profile['secrets'][role]
        entry = receipt['secrets'][role]
        file = created/(outputs[0] if role == 'keys' else 'passphrase')
        receipt['operations'].append(ADD_OPERATION['operation'].format(secret=secret, project=profile['project'],
            file=f'<workspace>/{file.name}'))
        try:
            version = executor.add_version(profile['project'], secret, file)
            if not re.fullmatch(r'[1-9][0-9]*', str(version)):
                raise Uncertain('version id is not exact')
        except Uncertain as error:
            entry['state'] = 'uncertain'
            return _recovery(receipt, journal, name, stop, f'{secret} add outcome unknown ({error})')
        except AddFailed as error:
            entry['state'] = 'not-committed'
            if role == 'keys':
                write_journal(journal, {'profile': name, 'status': 'failed-before-commit', 'publicIdentity': identity})
                return stop('add-failed', f'{secret} add failed ({error}); nothing committed', 'failed')
            return _recovery(receipt, journal, name, stop, f'{secret} add failed after keys were committed')
        except BaseException:
            entry['state'] = 'uncertain'
            _recovery(receipt, journal, name, stop, f'{secret} add interrupted')
            raise
        entry['committedVersion'], entry['state'] = str(version), 'committed'
        write_journal(journal, {'profile': name, 'status': 'importing', 'publicIdentity': identity,
            'committed': {r: e['committedVersion'] for r, e in receipt['secrets'].items()}})
    receipt['status'] = 'complete'
    write_journal(journal, {'profile': name, 'status': 'complete', 'publicIdentity': identity,
        'committed': {r: e['committedVersion'] for r, e in receipt['secrets'].items()}})
    return receipt, private


def verified_pkcs12(generated, store, profile):
    """The generator's verified public identity, rechecked against the profile contract and the stored file."""
    if not isinstance(generated, dict) or not HEX64.fullmatch(str(generated.get('certificateSha256'))):
        raise Blocked('pkcs12-identity', 'the generator returned no verified public identity')
    expected = profile['generation']['identity']
    for key in ('alias', 'keyAlgorithm', 'keySize', 'signatureAlgorithm', 'subject', 'validityDays'):
        if generated.get(key) != expected[key]:
            raise Blocked('pkcs12-identity', f'{key} {generated.get(key)!r} is not {expected[key]!r}')
    if store.read_bytes()[:1] != b'\x30':
        raise Blocked('pkcs12-invalid', 'keystore.p12 is not a DER PKCS12 structure')
    return {k: generated[k] for k in ('certificateSha256', 'alias', 'keyAlgorithm', 'keySize', 'signatureAlgorithm',
        'subject', 'validityDays', 'notBefore') if k in generated}


def _recovery(receipt, journal, name, stop, detail):
    receipt['recoveryNeeded'] = True
    write_journal(journal, {'profile': name, 'status': 'recovery-needed', 'publicIdentity': receipt.get('publicIdentity'),
        'secrets': receipt['secrets']})
    return stop('recovery-needed', detail + '; not retried, rolled back or deleted', 'recovery-needed')


class GcloudExecutor:
    """Production executor with the job's signer login: tool measurement, metadata, the recipe and version adds."""

    def __init__(self, gcloud='gcloud', timeout=120):
        self.gcloud, self.timeout = gcloud, timeout

    def tool_tree(self, trust):
        root = Path(trust['root'])/trust['treePath']
        tree = git_tree_id(root)
        end = time.monotonic() + 120
        return {'treeId': tree, 'toolBlob': git_blob_id(root/trust['tool'], end, time.monotonic)}

    def tool_sha256(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise Blocked('tool-identity', f'{path} is not a regular file')
            return hashlib.file_digest(f, 'sha256').hexdigest()

    def _json(self, args):
        try:
            result = subprocess.run([self.gcloud, *args, '--format=json', '--quiet'], capture_output=True, text=True,
                timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Unavailable(type(error).__name__)
        if result.returncode:
            raise Unavailable(f'gcloud exited {result.returncode}')  # its stderr is not echoed
        return json.loads(result.stdout or 'null')

    def secret_state(self, project, secret):
        self._json(['secrets', 'describe', secret, '--project', project])
        versions = self._json(['secrets', 'versions', 'list', secret, '--project', project, '--filter=state!=DESTROYED'])
        return {'exists': True, 'versions': [str(v.get('name', '')).rsplit('/', 1)[-1] for v in versions or []]}

    def generate(self, workspace, profile):
        return generate_keys(workspace, profile, subprocess_runner)

    def add_version(self, project, secret, file):
        try:
            result = subprocess.run([self.gcloud, 'secrets', 'versions', 'add', secret, '--project', project,
                '--data-file', str(file), '--format=json', '--quiet'], capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise Uncertain('timeout')
        if result.returncode:
            # Not distinguishable from a lost response without metadata, so never treated as definitely absent.
            raise Uncertain(f'gcloud exited {result.returncode}')
        try:
            return json.loads(result.stdout)['name'].rsplit('/', 1)[-1]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise Uncertain('unparsable response')


def plan(name, profiles):
    profile = profiles['profiles'].get(name, {})
    secrets = profile.get('secrets', {})
    live = (profile.get('access') or {}).get('liveCheck') is True
    # A live-check profile is granted one binding per secret (grant), never the split predefined roles of the rows.
    one = {'grantedBy': 'the one bootstrap binding on this secret (grant)'} if live else {}
    return {'profile': name, 'status': profile.get('status'), 'missing': profile.get('missing'),
        'command': f'workflow os-release.yml mode={MODE} bootstrap_profile={name} candidate_sha=<that workflow commit on main>',
        'metadata': [dict(op, operation=op['operation'].format(secret=s, project=profile.get('project')), **one)
            for s in secrets.values() for op in METADATA_OPERATIONS],
        'grant': ('one binding per secret, holding secretmanager.secrets.get, versions.list and versions.add with a single '
                  'expiry of at most 4 h (a custom role), made at action time; the secretAccessor is a separate expiring '
                  'binding on the same two secrets; versions.add cannot be checked before the add, so split or partial grants '
                  'can use up the run' if live else None),
        'add': [dict(ADD_OPERATION, operation=ADD_OPERATION['operation'].format(secret=s, project=profile.get('project'),
            file='<workspace file>'), **one) for s in secrets.values()],
        'readiness': [{'code': c, 'detail': d} for c, d in readiness(name, profiles, datetime.now(timezone.utc))],
        # Installed identities are host facts: source identity is not an installed executable hash.
        'laterHostPreflight': [{'tool': t, 'kind': i.get('kind'), 'source': i.get('source'), 'observe': i.get('requiredObservation')}
            for t, i in sorted(profile.get('tools', {}).items()) if not (i.get('installed') or {}).get('sha256')],
        'missingSources': ((profile.get('keyInventory') or {}).get('missing') or [])
            + ((profile.get('generation') or {}).get('missing') or [])}


def interrupted(signum):
    raise Blocked('interrupted', f'signal {signum}: stopped; the workspace is removed and any add is journaled for recovery')


def main(argv=None, env=None):
    env = os.environ if env is None else env
    factory_signing.stop_on_signals(interrupted)  # a stop still removes the RAM workspace and records recovery
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('guard')
    for command in ('preflight', 'prove', 'run', 'plan'):
        p = sub.add_parser(command)
        p.add_argument('--profile', required=True)
        if command != 'plan':
            p.add_argument('--recipe-sha', required=True)
        if command == 'prove':
            p.add_argument('--proof-dir', required=True, type=Path)
        if command == 'run':
            p.add_argument('--state-dir', required=True, type=Path)
            p.add_argument('--receipt-dir', required=True, type=Path)
            p.add_argument('--proof-file', type=Path)
    args = parser.parse_args(argv)
    profiles = load_profiles()
    now = datetime.now(timezone.utc)
    if args.command == 'plan':
        print(json.dumps(plan(args.profile, profiles), indent=2))
        return 0
    if args.command in ('guard', 'preflight'):
        failures = guard(env, profiles, now) if args.command == 'guard' else preflight(args.profile, profiles, env,
            args.recipe_sha, now)
        for failure in failures:
            print(f'::error::signing-bootstrap {args.command}: {failure}')
        return 4 if failures else 0
    lock = json.loads((ROOT/PIPELINE_PATH).read_text())['host']['lockPath']
    if args.command == 'prove':
        # Fixed summary only: a status word and fixed codes; never helper output, paths or file content.
        status, codes = prove(args.profile, profiles, env, args.recipe_sha, args.proof_dir, lock, now=now)
        for code in codes:
            print(f'::error::signing-bootstrap prove: {code}')
        print(json.dumps({'status': status, 'codes': codes}))
        return 0 if status in ('proven', 'not-required') else 4
    failures = preflight(args.profile, profiles, env, args.recipe_sha, now)
    if failures:
        for failure in failures:
            print(f'::error::signing-bootstrap run: {failure}')
        return 4
    binding = None
    if args.proof_file and profiles['profiles'].get(args.profile, {}).get('localProof'):
        try:
            fd = os.open(args.proof_file, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as f:
                binding = json.load(f)
        except (OSError, ValueError):
            binding = None
    args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipt, private = run(args.profile, profiles, GcloudExecutor(), args.state_dir, lock, args.recipe_sha, now, proof=binding,
        run_identity=(env.get('GITHUB_RUN_ID'), env.get('GITHUB_RUN_ATTEMPT')))
    out = public_receipt(receipt, private)
    args.receipt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    (args.receipt_dir/'signing-bootstrap-receipt.json').write_text(json.dumps(out, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': out['status'], 'reasons': [r['code'] for r in out['reasons']]}))
    return 0 if out['status'] == 'complete' else 1


if __name__ == '__main__':
    sys.exit(main())
