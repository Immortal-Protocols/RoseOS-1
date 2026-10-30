#!/usr/bin/env python3
"""Guarded OS signing-key bootstrap adapter (OCT29-INFRA-S005).

One reviewed profile from os/signing-bootstrap/profiles.json selects what may be bootstrapped. fold-rango-v1 is the only
resource-bound profile; models-v0 is a separate interface that fails closed until its own resources exist. A profile is
not a release admission: it admits no device build, and os-release keeps admitting Frankel only.

guard      on the hosted guard job, before the controller starts: the mode, profile and options must be exactly a
           bootstrap of a ready profile, bound to this workflow commit on main from a manual dispatch.
preflight  on the builder before any login: the same profile readiness, the signer identity and the recipe binding.
run        on the builder, under the shared host lock, with the job's short-lived signer login. Order:
           readiness -> no previous attempt -> lock -> tool identities -> secret metadata (exists, no versions) ->
           protected workspace -> generate -> exact output projection -> archive and public identity checks ->
           add keys version -> add passphrase version -> cleanup -> public receipt.
plan       prints the exact later command, metadata operations and the permissions they need. Nothing is executed.

Fail-closed rules. A profile that is unbound, has an incomplete key inventory, an unapproved tool identity or
expiring access stops before any side effect. An existing version, unavailable or uncertain metadata, an unsafe archive,
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
avb.pem) as scripts/host-decrypt-keys.py expects. The checked-in fold recipe is null and its installed tool identities
are unmeasured, so readiness blocks before any process starts. Tests use mock executors and runners only.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import host_secrets  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROFILES_PATH = 'os/signing-bootstrap/profiles.json'
PIPELINE_PATH = 'os/release-pipeline/frankel.json'
MODE = 'signing-bootstrap'
RECEIPT_SCHEMA = 'roseos.signing-bootstrap-receipt.v1'
OUTPUTS = ('keys.tar.gz', 'passphrase')
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
    for tool, identity in sorted(profile.get('tools', {}).items()):
        installed = identity.get('installed') or {}
        if not approved_path(installed.get('path')) or not HEX64.fullmatch(str(installed.get('sha256'))):
            reasons.append(('tool-unapproved', f'{tool}: no measured installed path and sha256 ({identity.get("requiredObservation")})'))
    if not profile.get('tools'):
        reasons.append(('tool-unapproved', 'no tool identities'))
    reasons += [('source-unpinned', detail) for detail in source_problems(profile)]
    missing = (profile.get('generation') or {}).get('missing') or []
    if missing:
        reasons.append(('recipe-missing', '; '.join(m['fact'] if isinstance(m, dict) else str(m) for m in missing)))
    reasons += [('recipe-missing' if code == 'missing' else 'recipe-invalid', detail)
        for code, detail in recipe_problems(profile)]
    access = profile.get('access', {})
    margin = timedelta(seconds=access.get('minimumRemainingSeconds', 1800))
    for key in ('addVersionExpires', 'accessorExpires'):
        try:
            if parse_time(access[key]) - margin <= now:
                reasons.append(('access-expired', f'{key} {access[key]} is within {margin} of {now:%Y-%m-%dT%H:%M:%SZ}'))
        except (KeyError, TypeError, ValueError):
            reasons.append(('access-unknown', f'{key} is missing or malformed'))
    return reasons


SHA1 = re.compile(r'[0-9a-f]{40}')


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
SUBJECT = re.compile(r'(/[A-Za-z]{1,3}=[A-Za-z0-9 ._-]{1,64}){1,8}')
STEP_KEYS = {'tool', 'args', 'produces', 'stdin', 'exitStatus'}


def recipe_problems(profile):
    """Pure validation of profile.generation.recipe. Returns [(code, detail)]: ('missing', ...) or ('invalid', ...)."""
    recipe = (profile.get('generation') or {}).get('recipe')
    if recipe is None:
        return [('missing', 'no reviewed generation recipe: ' + str((profile.get('generation') or {}).get('note', '')))]
    inventory = profile.get('keyInventory', {})
    allowed = set(inventory.get('requiredByConsumer', [])) | set(inventory.get('optionalByConsumer', []))
    if not isinstance(recipe, list) or not recipe:
        return [('invalid', 'recipe must be a non-empty list of steps')]
    problems, produced = [], []
    for index, step in enumerate(recipe):
        if not isinstance(step, dict) or set(step) - STEP_KEYS or not {'tool', 'args', 'produces'} <= set(step):
            problems.append(('invalid', f'step {index}: only tool, args, produces, stdin and exitStatus are accepted'))
            continue
        if step['tool'] not in profile.get('tools', {}):
            problems.append(('invalid', f'step {index}: tool {step["tool"]!r} is not a profile tool'))
        if step.get('exitStatus', 0) not in (0, 1) or isinstance(step.get('exitStatus', 0), bool):
            problems.append(('invalid', f'step {index}: exitStatus must be 0 or 1 (make_key exits 1 through its EXIT trap)'))
        if step.get('stdin', 'none') not in ('none', 'passphrase'):
            problems.append(('invalid', f'step {index}: stdin must be none or passphrase'))
        for arg in step['args'] if isinstance(step['args'], list) else [None]:
            if not (isinstance(arg, str) and (arg in ('{out}', '{passphrase_file}', '{subject}')
                    or (arg.startswith('{out}/') and arg[6:] in allowed | {Path(f).stem for f in allowed})
                    or (LITERAL.fullmatch(arg) and '..' not in arg))):
                problems.append(('invalid', f'step {index}: argument {str(arg)[:40]!r} is not a placeholder or plain literal'))
        if not isinstance(step['produces'], list) or not set(step['produces']) <= allowed:
            problems.append(('invalid', f'step {index}: produces must name inventory files'))
        else:
            produced += step['produces']
    if len(produced) != len(set(produced)):
        problems.append(('invalid', 'a file is produced by more than one step'))
    missing = sorted(set(inventory.get('requiredByConsumer', [])) - set(produced))
    if missing:
        problems.append(('invalid', f'recipe does not produce {missing}'))
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
    problems = recipe_problems(profile)
    if problems:
        raise Blocked('recipe-missing' if problems[0][0] == 'missing' else 'recipe-invalid', problems[0][1])
    workspace = Path(workspace)
    passphrase = workspace/'passphrase'
    fd = os.open(passphrase, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write((token or base64.urlsafe_b64encode(os.urandom(36)).decode()) + '\n')
    out = workspace/'out'
    out.mkdir(mode=0o700)
    expected = []
    values = {'{out}': str(out), '{passphrase_file}': str(passphrase), '{subject}': str((profile.get('generation') or {}).get('subject'))}
    for index, step in enumerate(profile['generation']['recipe']):
        tool = profile['tools'][step['tool']]['installed']['path']
        argv = [values.get(a, str(out/a[6:]) if a.startswith('{out}/') else a) for a in step['args']]
        status = runner(tool, argv, str(out), str(passphrase) if step.get('stdin') == 'passphrase' else None)
        if status != step.get('exitStatus', 0):
            raise Blocked('generator-failed', f'step {index} ({step["tool"]}) exited {status}')
        expected += step['produces']
    names = sorted(os.listdir(out))
    if names != sorted(expected):
        raise Blocked('unexpected-output', f'generator wrote {names[:16]}, expected {sorted(expected)}')
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


def subprocess_runner(tool, argv, cwd, stdin_path, timeout=900):
    """Production process boundary: fixed argv, minimal environment, no output captured into logs."""
    env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'HOME': cwd}
    with (open(stdin_path, 'rb') if stdin_path else open(os.devnull, 'rb')) as stdin:
        try:
            return subprocess.run([tool, *argv], cwd=cwd, env=env, stdin=stdin, stdout=subprocess.DEVNULL,
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
    'tools', 'cleanup', 'payloadReadback', 'recoveryNeeded', 'operations'}


def public_receipt(receipt, private_values=()):
    """Allowlisted projection; refuses to emit anything that contains a private value."""
    out = {k: receipt[k] for k in RECEIPT_KEYS if k in receipt}
    text = json.dumps(out, sort_keys=True)
    for value in private_values:
        forms = [value] if isinstance(value, str) else [value.decode('utf-8', 'replace'), value.hex()]
        if len(value) >= 4 and any(form in text for form in forms):
            raise Blocked('receipt-leak', 'receipt would contain private material')
    return out


def run(name, profiles, executor, state_dir, lock_path, recipe_sha, now, workspace=None, frankel=None):
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
            return _locked(name, profile, executor, journal, receipt, private, stop, workspace,
                frankel or frankel_identities())
    except Blocked as error:
        return stop(error.code, error.detail, receipt['status'] if receipt['status'] != 'blocked' else 'blocked')


def _locked(name, profile, executor, journal, receipt, private, stop, workspace, frankel):
    for tool, identity in sorted(profile['tools'].items()):
        installed = identity['installed']
        observed = executor.tool_sha256(installed['path'])
        if observed != installed['sha256']:
            return stop('tool-identity', f'{tool} sha256 {observed} is not the measured {installed["sha256"]}')
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


def _inside(name, profile, executor, journal, receipt, private, stop, created, frankel):
    info = os.lstat(created)
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
        return stop('workspace-unprotected', 'workspace must be an owner-only directory')
    write_journal(journal, {'profile': name, 'status': 'generating'})
    executor.generate(created, profile)
    names = sorted(os.listdir(created))
    if names != sorted(OUTPUTS):
        return stop('unexpected-output', f'generator outputs {names[:16]} are not exactly {list(OUTPUTS)}', 'failed')
    for output in OUTPUTS:
        info = os.lstat(created/output)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            return stop('unexpected-output', f'{output} is not an owner-only regular file', 'failed')
    passphrase = (created/'passphrase').read_bytes()
    private.extend([passphrase, passphrase.strip()])
    if not passphrase.strip():
        return stop('passphrase-empty', 'passphrase output is empty', 'failed')
    try:
        identity = inspect_archive(created/'keys.tar.gz', profile)
    except Blocked as error:
        return stop(error.code, error.detail, 'failed')
    if identity['otaCertificateSha256'] == frankel.get('otaCertificateSha256') or \
            identity['avbPublicKeySha256'] == frankel.get('avbPublicKeySha256'):
        return stop('identity-collision', 'public identity equals the Frankel identity; keys are never copied', 'failed')
    receipt['publicIdentity'] = identity
    write_journal(journal, {'profile': name, 'status': 'importing', 'publicIdentity': identity})
    for role in ('keys', 'passphrase'):
        secret = profile['secrets'][role]
        entry = receipt['secrets'][role]
        file = created/('keys.tar.gz' if role == 'keys' else 'passphrase')
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


def _recovery(receipt, journal, name, stop, detail):
    receipt['recoveryNeeded'] = True
    write_journal(journal, {'profile': name, 'status': 'recovery-needed', 'publicIdentity': receipt.get('publicIdentity'),
        'secrets': receipt['secrets']})
    return stop('recovery-needed', detail + '; not retried, rolled back or deleted', 'recovery-needed')


class GcloudExecutor:
    """Production executor with the job's signer login: tool measurement, metadata, the recipe and version adds."""

    def __init__(self, gcloud='gcloud', timeout=120):
        self.gcloud, self.timeout = gcloud, timeout

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
        generate_keys(workspace, profile, subprocess_runner)

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
    return {'profile': name, 'status': profile.get('status'), 'missing': profile.get('missing'),
        'command': f'workflow os-release.yml mode={MODE} bootstrap_profile={name} candidate_sha=<that workflow commit on main>',
        'metadata': [dict(op, operation=op['operation'].format(secret=s, project=profile.get('project')))
            for s in secrets.values() for op in METADATA_OPERATIONS],
        'add': [dict(ADD_OPERATION, operation=ADD_OPERATION['operation'].format(secret=s, project=profile.get('project'),
            file='<workspace file>')) for s in secrets.values()],
        'readiness': [{'code': c, 'detail': d} for c, d in readiness(name, profiles, datetime.now(timezone.utc))],
        # Installed identities are host facts: source identity is not an installed executable hash.
        'laterHostPreflight': [{'tool': t, 'kind': i.get('kind'), 'source': i.get('source'), 'observe': i.get('requiredObservation')}
            for t, i in sorted(profile.get('tools', {}).items()) if not (i.get('installed') or {}).get('sha256')],
        'missingSources': ((profile.get('keyInventory') or {}).get('missing') or [])
            + ((profile.get('generation') or {}).get('missing') or [])}


def main(argv=None, env=None):
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('guard')
    for command in ('preflight', 'run', 'plan'):
        p = sub.add_parser(command)
        p.add_argument('--profile', required=True)
        if command != 'plan':
            p.add_argument('--recipe-sha', required=True)
        if command == 'run':
            p.add_argument('--state-dir', required=True, type=Path)
            p.add_argument('--receipt-dir', required=True, type=Path)
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
    failures = preflight(args.profile, profiles, env, args.recipe_sha, now)
    if failures:
        for failure in failures:
            print(f'::error::signing-bootstrap run: {failure}')
        return 4
    lock = json.loads((ROOT/PIPELINE_PATH).read_text())['host']['lockPath']
    args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipt, private = run(args.profile, profiles, GcloudExecutor(), args.state_dir, lock, args.recipe_sha, now)
    out = public_receipt(receipt, private)
    args.receipt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    (args.receipt_dir/'signing-bootstrap-receipt.json').write_text(json.dumps(out, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': out['status'], 'reasons': [r['code'] for r in out['reasons']]}))
    return 0 if out['status'] == 'complete' else 1


if __name__ == '__main__':
    sys.exit(main())
