#!/usr/bin/env python3
"""Retained Rango signing, verification and separate publication (OCT30-INFRA-S015).

One retained, already-compiled Rango (Pixel 10 Pro Fold) build is signed with the Frankel primitives (host-sign.py,
host_secrets.py) and published as its own pixel-ota release that never becomes Latest. Nothing here compiles, rebuilds
apps, generates or imports keys, runs a bootstrap or moves input between hosts. os/retained-rango/release.json names
every real binding; a null one is unbound and the route refuses before any controller, login or secret access.

  preflight [--input-dir D] [--bootstrap-receipt R]
                       names every unbound binding (exit 4) or refuses a wrong one (exit 1); with D the input receipt,
                       target-files and otatools are validated too; with R the bootstrap receipt is checked
  stage --input-dir D --consumer C
                       validate the local input directory and copy it into a fresh task-owned consumer directory, then
                       revalidate the copy (the only input interface; no remote URL, credential or held route)
  recheck --consumer C revalidate a staged consumer directory (run again immediately before secret access)
  prove --proof-dir P  builder, before login, under the shared lock: the admitted S016 helper proves the four Fold tools;
                       a nonsecret proof bound to this recipe, run and attempt
  sign --consumer C --release-dir R --proof-file P/fold-tool-proof.json
                       builder: the same-run proof rechecked under the shared lock, then host-sign.py's retained route
                       (bound config, revalidated input)
  verify --consumer C --release-dir R
                       builder: release-pipeline verify_signed with the bound Rango expectations and identities
  stage-artifact --consumer C --release-dir R --artifact-dir A
                       builder: release-pipeline stage_artifact plus the nonsecret bindings for the publisher
  publish --artifact-dir A
                       hosted publisher, only with ROSE_RANGO_PUBLISH=true (workflow rango_publish): revalidate the bindings, release-pipeline publish (draft, download check), then
                       public with make_latest=false; Latest must be unchanged and promote is refused

Exit status: 0 done, 1 refused (category on stderr), 2 invalid input, 4 setup missing.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT/'os/retained-rango/release.json'
PROFILES_PATH = ROOT/'os/signing-bootstrap/profiles.json'
FRANKEL_PATH = ROOT/'os/release-pipeline/frankel.json'
HEX64 = re.compile(r'[0-9a-f]{64}')
SHA1 = re.compile(r'[0-9a-f]{40}')
VERSION = re.compile(r'[1-9][0-9]{0,9}')
TOOLS = ('generator', 'decrypt-keys', 'avbtool', 'openssl')
CONSUMER_RECEIPT = 'consumer-receipt.json'


class Refused(Exception):
    pass


class SetupMissing(Exception):
    def __init__(self, missing):
        super().__init__(', '.join(missing))
        self.missing = missing


def config():
    return json.loads(CONFIG_PATH.read_text())


def profile(cfg=None):
    return json.loads(PROFILES_PATH.read_text())['profiles'][(cfg or config())['signing']['bootstrapProfile']]


def frankel_identities():
    return json.loads(FRANKEL_PATH.read_text())['identities']


def check_config(cfg):
    """Fixed facts a mutated config cannot change: Rango only, a rango- tag and never Latest."""
    target, publication = cfg.get('target') or {}, cfg.get('publication') or {}
    if target.get('device') != 'rango' or not SHA1.fullmatch(str(target.get('product'))) or not re.fullmatch(r'\d{10}', str(target.get('build'))):
        raise Refused('config_invalid: the retained route is Rango with a pinned product commit and build')
    if publication.get('tag') != 'rango-{build}' or publication.get('latest') is not False:
        raise Refused('config_invalid: Rango publishes only as rango-{build} and never as Latest')
    if (cfg.get('signing') or {}).get('bootstrapProfile') != 'fold-rango-v1':
        raise Refused('config_invalid: Rango signs only with the fold-rango-v1 identities')
    return cfg


# ---------------------------------------------------------------- input receipt

def _artifact(value, name, limit, where, unbound):
    if value is None:
        unbound.append(where)
        return
    if not isinstance(value, dict) or value.get('name') != name or not isinstance(value.get('size'), int) \
            or not 0 < value['size'] <= limit or not HEX64.fullmatch(str(value.get('sha256'))):
        raise Refused(f'input_invalid: {where} must name {name} with its size (at most {limit}) and sha256')


def check_receipt(receipt, cfg=None):
    """A strict, versioned description of one retained Rango build. Returns the unbound fields; refuses wrong ones."""
    cfg = cfg or config()
    target, spec = cfg['target'], cfg['input']
    if not isinstance(receipt, dict) or receipt.get('schema') != spec['receiptSchema']:
        raise Refused('input_invalid: not a retained Rango input receipt')
    if receipt.get('device') != target['device'] or receipt.get('product') != target['product']:
        raise Refused('input_foreign: the receipt names another device or product')
    build = receipt.get('build') or {}
    if build.get('number') != target['build']:
        raise Refused(f'input_foreign: the receipt names build {build.get("number")!r}, not {target["build"]}')
    unbound = []
    if build.get('timestamp') is None:
        unbound.append('input.build.timestamp')
    elif not isinstance(build['timestamp'], int) or not 10**9 <= build['timestamp'] < 10**10:
        raise Refused('input_invalid: build.timestamp must be a ten-digit UTC epoch')
    source = receipt.get('source') or {}
    for key in ('manifestSha256', 'configSha256', 'compileReceiptSha256'):
        if source.get(key) is None:
            unbound.append(f'input.source.{key}')
        elif not HEX64.fullmatch(str(source[key])):
            raise Refused(f'input_invalid: source.{key} must be a sha256')
    overlays = source.get('overlays')
    if overlays is None:
        unbound.append('input.source.overlays')
    elif not isinstance(overlays, list) or not overlays or any(not isinstance(o, dict) or not HEX64.fullmatch(str(o.get('sha256')))
            or not re.fullmatch(r'[\w.-]+(/[\w.-]+)*', str(o.get('path'))) or '..' in str(o.get('path')).split('/') for o in overlays) \
            or len({o['path'] for o in overlays}) != len(overlays):
        raise Refused('input_invalid: source.overlays must list distinct relative paths with sha256')
    if source.get('compileExit') != 0:
        raise Refused('input_invalid: the retained build did not record a successful compile (exit 0)')
    artifacts = receipt.get('artifacts') or {}
    _artifact(artifacts.get('targetFiles'), spec['targetFiles'], spec['limits']['targetFilesBytes'], 'input.artifacts.targetFiles', unbound)
    _artifact(artifacts.get('otatools'), spec['otatools'], spec['limits']['otatoolsBytes'], 'input.artifacts.otatools', unbound)
    apps = receipt.get('apps') or {}
    rose = apps.get('roseApk')
    if rose is None:
        unbound.append('input.apps.roseApk')
    elif not isinstance(rose, dict) or rose.get('packageName') != 'dev.pixel.voice' or not isinstance(rose.get('versionCode'), int) \
            or not HEX64.fullmatch(str(rose.get('sha256'))) or not HEX64.fullmatch(str(rose.get('signerSha256'))):
        raise Refused('input_invalid: apps.roseApk must bind dev.pixel.voice versionCode, sha256 and signer')
    if apps.get('policySha256') is None:
        unbound.append('input.apps.policySha256')
    elif not HEX64.fullmatch(str(apps['policySha256'])):
        raise Refused('input_invalid: apps.policySha256 must be a sha256')
    producer = receipt.get('producer')
    if producer is None:
        unbound.append('input.producer')
    elif not isinstance(producer, dict) or producer.get('route') != 'rango-build-only' or not producer.get('operator') \
            or not producer.get('host'):
        raise Refused('input_foreign: the receipt was not produced by the rango-build-only route')
    return unbound


def _regular(path):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise Refused(f'input_unsafe: {Path(path).name} is not a regular file')
    return info


def _hash(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    digest, total = hashlib.sha256(), 0
    with os.fdopen(fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise Refused(f'input_unsafe: {Path(path).name} is not a regular file')
        for block in iter(lambda: f.read(8 << 20), b''):
            total += len(block)
            if total > limit:
                raise Refused(f'input_drift: {Path(path).name} exceeds its recorded size')
            digest.update(block)
    return total, digest.hexdigest()


def _plain_directory(path):
    path = Path(path)
    if not path.is_absolute() or os.path.realpath(path) != str(path) or not path.is_dir():
        raise Refused('input_unsafe: the input directory must be a plain absolute directory')
    return path


def check_directory(directory, cfg=None):
    """Exactly the receipt, target-files, otatools, resolved manifest and background policy, each a regular file matching
    the receipt's size and hash (the manifest and policy by their receipt sha256 once bound)."""
    cfg = cfg or config()
    spec = cfg['input']
    directory = _plain_directory(directory)
    names = sorted(os.listdir(directory))
    if names != sorted([spec['receiptFile'], spec['targetFiles'], spec['otatools'], spec['resolvedManifest'], spec['policy']]):
        raise Refused('input_inventory: the directory must hold exactly the receipt, target-files, otatools, resolved manifest and policy')
    _regular(directory/spec['receiptFile'])
    fd = os.open(directory/spec['receiptFile'], os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        try:
            receipt = json.load(f)
        except ValueError:
            raise Refused('input_invalid: unreadable receipt') from None
    unbound = check_receipt(receipt, cfg)
    if any(name.startswith('input.artifacts.') for name in unbound):
        raise SetupMissing(unbound)
    observed = {}
    for key in ('targetFiles', 'otatools'):
        expected = receipt['artifacts'][key]
        _regular(directory/expected['name'])
        size, digest = _hash(directory/expected['name'], expected['size'])
        if (size, digest) != (expected['size'], expected['sha256']):
            raise Refused(f'input_drift: {expected["name"]} differs from the receipt')
        observed[expected['name']] = digest
    for name, bound in ((spec['resolvedManifest'], (receipt.get('source') or {}).get('manifestSha256')),
                        (spec['policy'], (receipt.get('apps') or {}).get('policySha256'))):
        _regular(directory/name)
        _, digest = _hash(directory/name, spec['limits']['metadataBytes'])
        if bound is not None and digest != bound:
            raise Refused(f'input_drift: {name} differs from the receipt')
        observed[name] = digest
    receipt_sha = hashlib.sha256((directory/spec['receiptFile']).read_bytes()).hexdigest()
    return receipt, unbound, {'receiptSha256': receipt_sha, 'files': observed}


def stage(input_dir, consumer, cfg=None):
    """Validate, copy into a fresh owner-only consumer directory, and revalidate the copy (never the source again)."""
    cfg = cfg or config()
    receipt, unbound, before = check_directory(input_dir, cfg)
    consumer = Path(consumer)
    if consumer.exists() or consumer.is_symlink():
        raise Refused('consumer_exists: the consumer directory must be fresh')
    consumer.mkdir(mode=0o700)
    data = consumer/'input'
    data.mkdir(mode=0o700)
    for name in os.listdir(input_dir):
        src = os.open(Path(input_dir)/name, os.O_RDONLY | os.O_NOFOLLOW)
        dst = os.open(data/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(src, 'rb') as s, os.fdopen(dst, 'wb') as d:
            shutil.copyfileobj(s, d, 8 << 20)
    _, _, after = check_directory(data, cfg)
    if after != before:
        raise Refused('input_drift: the staged copy differs from the validated input')
    record = dict(after, unbound=unbound, build=receipt['build'], device=receipt['device'], product=receipt['product'])
    fd = os.open(consumer/CONSUMER_RECEIPT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(record, f, indent=2, sort_keys=True)
    return record


def recheck(consumer, cfg=None):
    consumer = Path(consumer)
    try:
        fd = os.open(consumer/CONSUMER_RECEIPT, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as f:
            recorded = json.load(f)
    except (OSError, ValueError):
        raise Refused('consumer_unavailable: no staged consumer receipt') from None
    receipt, unbound, observed = check_directory(consumer/'input', cfg)
    if {k: recorded.get(k) for k in observed} != observed:
        raise Refused('input_drift: the consumer directory changed after staging')
    return receipt, unbound, observed


# ---------------------------------------------------------------- signer and tool bindings

def signer_binding(bootstrap_receipt, prof=None, frankel=None):
    """Exact secret versions and public identities from a genuine completed fold-rango-v1 bootstrap receipt; never a
    placeholder and never the Frankel identity."""
    prof, frankel = prof or profile(), frankel or frankel_identities()
    receipt = bootstrap_receipt
    problems = []
    if not isinstance(receipt, dict) or receipt.get('profile') != 'fold-rango-v1' or receipt.get('device') != 'rango' \
            or receipt.get('status') != 'complete' or receipt.get('recoveryNeeded'):
        raise Refused('signer_unbound: not a complete fold-rango-v1 bootstrap receipt')
    versions = {}
    for role, name in prof['secrets'].items():
        entry = (receipt.get('secrets') or {}).get(role) or {}
        if entry.get('name') != name or entry.get('state') != 'committed' or not VERSION.fullmatch(str(entry.get('committedVersion'))):
            problems.append(f'{role} secret {name} has no committed exact version')
        else:
            versions[role] = str(entry['committedVersion'])
    identity = receipt.get('publicIdentity') or {}
    for key in ('otaCertificateSha256', 'avbPublicKeySha256'):
        if not HEX64.fullmatch(str(identity.get(key))):
            problems.append(f'publicIdentity.{key} missing')
    if (receipt.get('cleanup') or {}).get('workspaceRemoved') is not True:
        problems.append('bootstrap cleanup not confirmed')
    if problems:
        raise Refused('signer_unbound: ' + '; '.join(problems))
    if identity['otaCertificateSha256'] == frankel.get('otaCertificateSha256') or identity['avbPublicKeySha256'] == frankel.get('avbPublicKeySha256'):
        raise Refused('signer_collision: the Rango public identity equals the Frankel identity')
    return {'secrets': dict(prof['secrets']), 'versions': versions, 'otaCertificateSha256': identity['otaCertificateSha256'],
        'avbPublicKeySha256': identity['avbPublicKeySha256'], 'receiptSha256': hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()}


_BOOTSTRAP = []


def bootstrap_module():
    """signing-bootstrap.py, loaded once: its admitted local-proof functions (OCT30-INFRA-S016) are reused, not copied."""
    if not _BOOTSTRAP:
        spec = importlib.util.spec_from_file_location('signing_bootstrap_for_rango', ROOT/'scripts/signing-bootstrap.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _BOOTSTRAP.append(module)
    return _BOOTSTRAP[0]


def tool_bindings(prof=None):
    """OCT30-INFRA-S017: the four Fold tools are never trusted from checked-in installed hashes. They are proven per run
    by the admitted S016 helper; this names what keeps that proof from being possible in source (helper admission and
    spec), or nothing."""
    prof = prof or profile()
    if not prof.get('localProof'):
        return ['signing.localProof']
    sb = bootstrap_module()
    return [f'signing.localProof ({code})' for code, _ in sb.proof_spec_problems(prof)]


PROOF_FILE = 'fold-tool-proof.json'


def prove(env, proof_dir, cfg=None, lock=None):
    """Builder, before signer login, under the shared lock: the admitted helper proves the four Fold tools on this host;
    the bounded nonsecret proof is bound to this recipe, run and attempt (the bootstrap's own binding format)."""
    cfg = check_config(cfg or config())
    prof = profile(cfg)
    missing = tool_bindings(prof)
    if missing:
        raise SetupMissing(missing)
    sb = bootstrap_module()
    try:
        helper, blob = sb.load_proof_helper(prof)
    except sb.Blocked as error:
        raise Refused(f'tool_proof_unavailable: {error.code}') from None
    with (lock or shared_lock)(cfg['signing']['lockPath']):
        try:
            proof = helper.verify_local(prof['localProof']['spec'], root=env.get('ROSE_PROOF_ROOT', '/'))
        except helper.Refused as error:
            raise Refused(f'tool_proof_refused: {sb._code(error)}') from None
    problems = sb.proof_problems(cfg['signing']['bootstrapProfile'], prof, proof)
    if problems:
        raise Refused(f'tool_proof_refused: {problems[0]}')
    binding = {'schema': sb.PROOF_BINDING_SCHEMA, 'profile': cfg['signing']['bootstrapProfile'], 'recipeSha': env.get('GITHUB_SHA'),
        'runId': env.get('GITHUB_RUN_ID'), 'runAttempt': env.get('GITHUB_RUN_ATTEMPT'), 'helperBlob': blob,
        'helperRevision': prof['localProof']['admittedRevision'],
        'proofSha256': hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest(), 'proof': proof}
    Path(proof_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(Path(proof_dir)/PROOF_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(binding, f, sort_keys=True)
    return {'stage': 'prove', 'proofSha256': binding['proofSha256'], 'helperBlob': blob}


def bind_tool_proof(env, cfg):
    """Inside the signing lock: only this run's proof, rechecked by the helper now; refusal before any secret."""
    sb = bootstrap_module()
    path = env.get('ROSE_RETAINED_PROOF', '')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as f:
            binding = json.load(f)
    except (OSError, ValueError):
        binding = None
    try:
        _, rows, summary = sb.bind_proof(cfg['signing']['bootstrapProfile'], profile(cfg), binding, env.get('GITHUB_SHA'),
            (env.get('GITHUB_RUN_ID'), env.get('GITHUB_RUN_ATTEMPT')), env.get('ROSE_PROOF_ROOT', '/'))
    except sb.Blocked as error:
        raise Refused(f'tool_proof_{error.code.replace("proof-", "")}: {error.detail}') from None
    return rows, {k: summary[k] for k in ('proofSha256', 'helperBlob', 'helperRevision')}


def preflight(input_dir=None, bootstrap_receipt=None, cfg=None, inputs=True):
    """Every unbound binding of the route, before any controller, login or secret. Wrong values refuse instead."""
    cfg = check_config(cfg or config())
    missing = []
    transport = cfg['input'].get('transport')
    if not inputs:
        pass
    elif transport is None:
        missing.append('input.transport')
    elif not isinstance(transport, dict) or not str(transport.get('directory', '')).startswith('/') or not transport.get('kind'):
        raise Refused('config_invalid: input.transport must name its kind and an absolute builder directory')
    input_dir = input_dir or (transport or {}).get('directory')
    if not inputs:
        pass
    elif input_dir:
        try:
            _, unbound, _ = check_directory(input_dir, cfg)
        except SetupMissing as error:
            unbound = error.missing
        missing += unbound
    else:
        missing.append('input.directory')
    receipt = bootstrap_receipt if bootstrap_receipt is not None else cfg['signing'].get('bootstrapReceipt')
    if identity_policy(cfg) == 'first-use-containers':  # refuses unless explicitly selected
        contract = cfg['signing']['identityPolicy']['firstUse']
        missing += [f'signing.identityPolicy.firstUse.{k}' for k in ('bootstrapRun', 'bootstrapRecipe', 'windowStart', 'windowEnd',
            'recordDir') if not contract.get(k)]
        # Its identity is derived later, inside the protected signing operation; nothing is required to exist before it.
    elif receipt is None:
        missing.append('signing.bootstrapReceipt')
    else:
        signer_binding(receipt)
    missing += tool_bindings()
    for key in ('otatoolsProvenance', 'releaseScriptProvenance'):
        if cfg['signing'].get(key) is None:
            missing.append(f'signing.{key}')
    if cfg['publication'].get('feedChannel') is None:
        missing.append('publication.feedChannel')
    notes = notes_binding(cfg)
    if notes is None:
        missing.append('publication.notes')
    elif inputs:
        notes_bytes(notes)  # read from the pinned commit and re-hashed before any login
    if cfg['verification'].get('expected') is None:
        missing.append('verification.expected')
    missing += ['verification.' + name for name in updater_contract(cfg, check=True)]
    return list(dict.fromkeys(missing))


# ---------------------------------------------------------------- independent first-use identity (contract, disabled)
# OCT30-INFRA-S017: a proposed alternative to the bootstrap receipt as the initial identity authority. It is a tested
# contract only: release.json signing.identityPolicy.firstUse.selected is false and the route refuses while it is not
# explicitly selected. Nothing here reads a secret; it judges version metadata and public identities handed to it.

VERSION_NAME = re.compile(r'projects/([a-z][a-z0-9-]{4,28}[a-z0-9])/secrets/([A-Za-z0-9_-]{1,255})/versions/([1-9][0-9]{0,9})')


def identity_policy(cfg):
    """The selected identity authority: 'bootstrap-receipt' (default) or an explicitly selected first-use contract."""
    policy = (cfg['signing'].get('identityPolicy') or {})
    mode = policy.get('mode', 'bootstrap-receipt')
    if mode == 'bootstrap-receipt':
        return mode
    if mode == 'first-use-containers':
        if (policy.get('firstUse') or {}).get('selected') is not True:
            raise Refused('identity_policy_unselected: the first-use container policy is a proposal, not selected')
        return mode
    raise Refused(f'config_invalid: unknown identity policy {mode!r}')


def _utc(value):
    import datetime
    try:
        return datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def first_use_admission(metadata, contract, prof=None):
    """Exactly one ENABLED version in each fixed container, created inside the bootstrap window: returns the immutable
    numeric version ids to pin before any payload access. Anything else refuses; no latest alias, no inferred version 1."""
    prof = prof or profile()
    if not isinstance(metadata, dict) or sorted(metadata) != sorted(prof['secrets']):
        raise Refused('identity_metadata_incomplete: both fixed containers must be listed')
    start, end = _utc(contract.get('windowStart')), _utc(contract.get('windowEnd'))
    if not start or not end or end < start:
        raise Refused('config_invalid: firstUse needs the bootstrap window')
    pinned = {}
    for role, secret in sorted(prof['secrets'].items()):
        versions = metadata[role]
        if not isinstance(versions, list) or len(versions) != 1:
            raise Refused(f'identity_versions_ambiguous: {secret} must hold exactly one version')
        version = versions[0] if isinstance(versions[0], dict) else {}
        match = VERSION_NAME.fullmatch(str(version.get('name')))
        if not match or match[1] != prof['project'] or match[2] != secret:
            raise Refused(f'identity_versions_ambiguous: {secret} version is not a numeric version of the fixed container')
        if version.get('state') != 'ENABLED':
            raise Refused(f'identity_versions_partial: {secret} version is {version.get("state")!r}, not ENABLED')
        created = _utc(version.get('createTime'))
        if not created or not start <= created <= end:
            raise Refused(f'identity_versions_untimely: {secret} version was not created in the bootstrap window')
        pinned[role] = match[3]
    return pinned


FIRST_USE_RECORD = 'first-use.json'
FIRST_USE_PARTIAL = 'first-use.json.partial'
# Long-lived identity fields compared on every later run; same-run audit fields (enrolledBy) are recorded, never compared.
IDENTITY_FIELDS = ('policy', 'bootstrapRun', 'bootstrapRecipe', 'versions', 'otaCertificateSha256', 'avbPublicKeySha256')


def record_dir(cfg, env):
    return Path(env.get('ROSE_FIRST_USE_DIR') or cfg['signing']['identityPolicy']['firstUse']['recordDir'])


def read_record(directory):
    """The durable first-use record, or None when never enrolled. A partial write, a link or an unreadable or malformed
    record refuses: it can never become, or hide, a binding."""
    directory = Path(directory)
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise Refused('identity_record_unsafe: the record directory is not a plain directory')
    if os.path.lexists(directory/FIRST_USE_PARTIAL):
        raise Refused('identity_record_partial: an interrupted enrolment left a partial record; reconcile explicitly')
    try:
        fd = os.open(directory/FIRST_USE_RECORD, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:
        raise Refused('identity_record_unsafe: the record is not a readable regular file') from None
    with os.fdopen(fd) as f:
        try:
            record = json.load(f)
        except ValueError:
            raise Refused('identity_record_partial: the record is not complete JSON') from None
    if not isinstance(record, dict) or any(k not in record for k in IDENTITY_FIELDS):
        raise Refused('identity_record_partial: the record lacks identity fields')
    return record


def first_use_record(directory, binding, enroll, frankel=None):
    """Explicit enrolment writes the record once, atomically (complete file fsynced, then hard-linked into place, which
    fails if any record exists); every later run must match its identity fields exactly. Missing after enrolment,
    re-enrolment, partial or conflicting state refuse; nothing is ever overwritten."""
    frankel = frankel or frankel_identities()
    for key in ('otaCertificateSha256', 'avbPublicKeySha256'):
        if not HEX64.fullmatch(str(binding.get(key))):
            raise Refused(f'identity_invalid: {key} is not a sha256')
    if binding['otaCertificateSha256'] == frankel.get('otaCertificateSha256') or binding['avbPublicKeySha256'] == frankel.get('avbPublicKeySha256'):
        raise Refused('signer_collision: the derived Rango identity equals the Frankel identity')
    directory = Path(directory)
    existing = read_record(directory)
    identity = {k: binding[k] for k in IDENTITY_FIELDS}
    if existing is not None:
        if enroll:
            raise Refused('identity_already_enrolled: a first-use record exists; enrolment is never repeated')
        if {k: existing[k] for k in IDENTITY_FIELDS} != identity:
            raise Refused('identity_conflict: the recorded first-use binding differs; explicit reconciliation is required')
        return 'matched'
    if not enroll:
        raise Refused('identity_record_missing: no first-use record; only an explicit enrolment may create one')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = directory/FIRST_USE_PARTIAL
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w') as f:
        json.dump(binding, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(partial, directory/FIRST_USE_RECORD)
    except FileExistsError:
        raise Refused('identity_record_race: another enrolment completed first') from None
    os.unlink(partial)
    dfd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return 'enrolled'


def check_bootstrap_run(run, contract, repository):
    """Permitted run metadata of the completed bootstrap: this repository, os-release.yml, the exact recipe, success."""
    problems = []
    if str((run or {}).get('id')) != str(contract.get('bootstrapRun')):
        problems.append('run id')
    if (run or {}).get('head_sha') != contract.get('bootstrapRecipe'):
        problems.append('recipe')
    if (run or {}).get('path') != '.github/workflows/os-release.yml' or (run or {}).get('event') != 'workflow_dispatch':
        problems.append('workflow')
    if (run or {}).get('status') != 'completed' or (run or {}).get('conclusion') != 'success':
        problems.append('result')
    if ((run or {}).get('repository') or {}).get('full_name') != repository:
        problems.append('repository')
    if problems:
        raise Refused('identity_provenance: the bootstrap run is not the completed os-release recipe (' + ', '.join(problems) + ')')
    return {'runId': str(run['id']), 'recipe': run['head_sha'], 'repository': repository}


def github_run(env, run_id):
    """Read-only Actions metadata of one run in this repository (hosted guard token); errors carry no token or body."""
    import urllib.request
    request = urllib.request.Request(f"{env.get('GITHUB_API_URL', 'https://api.github.com')}/repos/{env['GITHUB_REPOSITORY']}"
        f"/actions/runs/{int(run_id)}", headers={'Authorization': f"Bearer {env.get('GH_TOKEN', '')}",
        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read(1 << 20))
    except Exception:  # noqa: BLE001 - never echo a token-bearing error
        raise Refused('identity_provenance: the bootstrap run metadata could not be read') from None


RUN_READER = [github_run]


def bootstrap_provenance(env, cfg=None):
    """Hosted guard step: in bootstrap-receipt mode records that no check applies (no call); with first-use selected,
    checks the permitted bootstrap run and records this run's receipt for the builder."""
    cfg = cfg or config()
    if identity_policy(cfg) == 'bootstrap-receipt':
        return {'policy': 'bootstrap-receipt', 'checked': False, 'consumerRunId': env.get('GITHUB_RUN_ID')}
    contract = cfg['signing']['identityPolicy']['firstUse']
    checked = check_bootstrap_run(RUN_READER[0](env, contract['bootstrapRun']), contract, env.get('GITHUB_REPOSITORY'))
    return dict(checked, policy='first-use-containers', checked=True, consumerRunId=env.get('GITHUB_RUN_ID'))


def provenance_receipt(env, contract):
    """The hosted guard's same-run check of the bootstrap run (the builder holds no Actions read access)."""
    try:
        fd = os.open(env.get('ROSE_FIRST_USE_PROVENANCE', ''), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as f:
            receipt = json.load(f)
    except (OSError, ValueError):
        raise Refused('identity_provenance: no same-run bootstrap provenance receipt') from None
    if receipt.get('consumerRunId') != env.get('GITHUB_RUN_ID') or receipt.get('checked') is not True \
            or receipt.get('runId') != str(contract.get('bootstrapRun')) or receipt.get('recipe') != contract.get('bootstrapRecipe'):
        raise Refused('identity_provenance: the provenance receipt is not this run\'s check of the bootstrap run')
    return receipt


def gcloud_versions(project, secret):
    """Complete version metadata of one fixed container (all states); no payload, stderr never echoed."""
    import subprocess
    try:
        result = subprocess.run(['gcloud', 'secrets', 'versions', 'list', secret, '--project', project, '--format=json', '--quiet'],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('identity_metadata_unavailable: version metadata could not be listed') from None
    if result.returncode:
        raise Refused('identity_metadata_unavailable: version metadata could not be listed')
    try:
        return [{k: v.get(k) for k in ('name', 'state', 'createTime')} for v in json.loads(result.stdout or '[]')]
    except (ValueError, AttributeError):
        raise Refused('identity_metadata_unavailable: version metadata is not exact') from None


def openssl_runner(argv):
    """Fixed-argv public-half extraction with the proven host OpenSSL (fixed PATH); only public key PEM is captured."""
    import subprocess
    result = subprocess.run(argv, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, stdin=subprocess.DEVNULL, capture_output=True,
        timeout=60)
    if result.returncode:
        raise Refused('identity_keys_invalid: a key does not decrypt with the passphrase or is malformed')
    return result.stdout


# Replaceable boundaries (tests substitute synthetic metadata and a mocked OpenSSL); never secret payload readers.
METADATA = [gcloud_versions]
KEY_RUNNER = [openssl_runner]


def validate_keys(key_dir, passfile, prof, runner):
    """Inside host-sign's protected transient workspace: exact inventory, every pk8 decrypting with the passphrase to the
    public key of its certificate, the AVB key decrypting too; then the public identity from the verified directory."""
    key_dir = Path(key_dir)
    inventory = prof['keyInventory']
    present = {p.name for p in key_dir.iterdir() if p.is_file() and not p.is_symlink()}
    if set(inventory['requiredByConsumer']) - present or present - set(inventory['requiredByConsumer']) - set(inventory.get('optionalByConsumer', [])):
        raise Refused('identity_keys_inventory: the key set is not the fold-rango-v1 inventory')
    passin = 'file:' + str(passfile)
    for role in inventory.get('roles', []):
        keys = [f for f in role.get('files', []) if f.endswith('.pk8')]
        certs = [f for f in role.get('files', []) if f.endswith('.x509.pem')]
        if len(keys) == 1 and len(certs) == 1:
            private_half = runner(['openssl', 'pkey', '-inform', 'DER', '-in', str(key_dir/keys[0]), '-passin', passin, '-pubout'])
            public_half = runner(['openssl', 'x509', '-in', str(key_dir/certs[0]), '-pubkey', '-noout'])
            if not private_half or private_half.strip() != public_half.strip():
                raise Refused(f'identity_keys_pair: {keys[0]} does not match {certs[0]}')
    runner(['openssl', 'pkey', '-in', str(key_dir/'avb.pem'), '-passin', passin, '-pubout'])
    sb = bootstrap_module()
    return {'otaCertificateSha256': sb.certificate_sha256((key_dir/'releasekey.x509.pem').read_text()),
        'avbPublicKeySha256': hashlib.sha256((key_dir/'avb_pkmd.bin').read_bytes()).hexdigest()}


def first_use_signing(env, cfg):
    """Selected first-use signing, under the signing lock and before any payload: provenance, exact version metadata
    pinned by numeric id, record state consistent with enrol/match, metadata rechecked; returns the pinned versions and
    the hook host-sign calls inside its transient workspace to validate keys, derive and record the identity."""
    contract = cfg['signing']['identityPolicy']['firstUse']
    prof = profile(cfg)
    provenance = provenance_receipt(env, contract)
    directory, enroll = record_dir(cfg, env), env.get('ROSE_FIRST_USE_ENROLL') == 'true'

    def admitted():
        return first_use_admission({role: METADATA[0](prof['project'], secret) for role, secret in prof['secrets'].items()},
            contract, prof)
    pinned = admitted()
    existing = read_record(directory)
    if existing is None and not enroll:
        raise Refused('identity_record_missing: no first-use record; only an explicit enrolment may create one')
    if existing is not None and enroll:
        raise Refused('identity_already_enrolled: a first-use record exists; enrolment is never repeated')
    if existing is not None and existing['versions'] != pinned:
        raise Refused('identity_conflict: the container versions differ from the recorded binding')
    if admitted() != pinned:
        raise Refused('identity_versions_race: version metadata changed during admission')

    def derive(extracted, passfile):
        if admitted() != pinned:  # after the fetch, before any use
            raise Refused('identity_versions_race: version metadata changed before use')
        dirs = {p.parent for p in Path(extracted).rglob('releasekey.x509.pem') if p.is_file() and not p.is_symlink()}
        if len(dirs) != 1:
            raise Refused('identity_keys_inventory: the archive does not hold exactly one key directory')
        identity = validate_keys(dirs.pop(), passfile, prof, KEY_RUNNER[0])
        binding = dict(identity, policy='first-use-containers', bootstrapRun=contract['bootstrapRun'],
            bootstrapRecipe=contract['bootstrapRecipe'], versions=pinned,
            enrolledBy={'runId': env.get('GITHUB_RUN_ID'), 'recipe': env.get('GITHUB_SHA'), 'provenance': provenance['runId']})
        first_use_record(directory, binding, enroll)
        return identity
    return pinned, derive


def resolve_binding(env, cfg, phase='verify'):
    """The one identity resolver: the genuine bootstrap receipt, or (selected first-use) the durable record written by
    this policy's signing operation. Never a fabricated bootstrap receipt."""
    if identity_policy(cfg) == 'bootstrap-receipt':
        return signer_binding(bootstrap_receipt(env, cfg))
    record = read_record(record_dir(cfg, env))
    if record is None:
        raise Refused('identity_record_missing: no first-use record; only an explicit enrolment may create one')
    return first_use_binding(cfg, record)


def first_use_binding(cfg, record, frankel=None):
    contract, frankel = cfg['signing']['identityPolicy']['firstUse'], frankel or frankel_identities()
    if record.get('policy') != 'first-use-containers' or record.get('bootstrapRun') != contract['bootstrapRun'] \
            or record.get('bootstrapRecipe') != contract['bootstrapRecipe'] or not isinstance(record.get('versions'), dict):
        raise Refused('identity_conflict: the first-use record does not belong to the selected contract')
    for key in ('otaCertificateSha256', 'avbPublicKeySha256'):
        if not HEX64.fullmatch(str(record.get(key))):
            raise Refused('identity_record_partial: the first-use record lacks its public identity')
    if record['otaCertificateSha256'] == frankel.get('otaCertificateSha256') or record['avbPublicKeySha256'] == frankel.get('avbPublicKeySha256'):
        raise Refused('signer_collision: the recorded Rango identity equals the Frankel identity')
    return {'secrets': dict(profile(cfg)['secrets']), 'versions': dict(record['versions']), 'policy': 'first-use-containers',
        'otaCertificateSha256': record['otaCertificateSha256'], 'avbPublicKeySha256': record['avbPublicKeySha256'],
        'record': {k: record[k] for k in IDENTITY_FIELDS}}


def bootstrap_receipt(env, cfg):
    """The bound public bootstrap receipt: inline in the config, or (tests and reruns) a file named by the environment."""
    if env.get('ROSE_RETAINED_BOOTSTRAP_RECEIPT'):
        try:
            return json.loads(Path(env['ROSE_RETAINED_BOOTSTRAP_RECEIPT']).read_text())
        except (OSError, ValueError):
            return None
    return cfg['signing'].get('bootstrapReceipt')


def jdk_home():
    trust = json.loads(PROFILES_PATH.read_text())['profiles']['models-v0']['tools']['keytool']['trust']
    return str(Path(trust['root'])/trust['treePath'])


def sign_inputs(env):
    """host-sign.py's retained route: the bound config and the revalidated consumer inputs, or a refusal."""
    cfg = check_config(config())
    consumer = env.get('ROSE_RETAINED_INPUT', '')
    bootstrap = bootstrap_receipt(env, cfg)
    missing = preflight(Path(consumer)/'input' if consumer else None, bootstrap, cfg)
    if missing:
        raise SetupMissing(missing)
    receipt, _, _ = recheck(consumer, cfg)
    if env.get('BUILD_NUMBER') != cfg['target']['build'] or env.get('BUILD_DATETIME') != str(receipt['build']['timestamp']):
        raise Refused('input_foreign: the signing build number and BUILD_DATETIME are not the retained build')
    secrets = profile(cfg)['secrets']
    if identity_policy(cfg) == 'first-use-containers':
        versions, derive = first_use_signing(env, cfg)
        identities = None  # derived and recorded inside host-sign's transient workspace by `derive`
    else:
        binding = signer_binding(bootstrap)
        versions, derive = binding['versions'], None
        identities = {'otaCertificateSha256': binding['otaCertificateSha256'], 'avbPublicKeySha256': binding['avbPublicKeySha256']}
    bound = {'target': {'device': 'rango'}, 'identities': identities,
        'host': {'sign': {'project': profile(cfg)['project'], 'keysSecret': secrets['keys'],
            'passphraseSecret': secrets['passphrase'], 'secretVersion': versions['keys'],
            'passphraseSecretVersion': versions['passphrase'], 'otatools': cfg['input']['otatools'],
            'javaHome': jdk_home(), 'updateNotes': notes_binding(cfg)}},
        'publication': {'channels': {'beta': {'feedChannel': cfg['publication']['feedChannel']}}}}
    if derive:
        bound['deriveIdentities'] = derive
    data = Path(consumer)/'input'
    return bound, data/cfg['input']['targetFiles'], data/cfg['input']['otatools']


# ---------------------------------------------------------------- connected route (existing pipeline functions)

PIPELINE_SECTIONS_FORBIDDEN = ('target', 'publication', 'fixture', 'updater')


_PIPELINE = []


def pipeline():
    """release-pipeline.py, loaded once: the route calls its functions, it does not copy them."""
    if not _PIPELINE:
        sys.path.insert(0, str(ROOT/'scripts'))
        spec = importlib.util.spec_from_file_location('release_pipeline_for_rango', ROOT/'scripts/release-pipeline.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _PIPELINE.append(module)
    return _PIPELINE[0]


NOTES_REPO = [ROOT]  # the repository holding the pinned notes commit (tests substitute a synthetic history)
NOTES_PATH = re.compile(r'os/release-notes/[\w.-]+\.md')


def notes_binding(cfg):
    """publication.notes: UPDATE.md pinned separately from the immutable product commit (whose notes directory has no
    entry for this build): the reviewed commit, the relative path, its sha256 and the exact build and device."""
    notes = cfg['publication'].get('notes')
    if notes is None:
        return None
    target = cfg['target']
    if not isinstance(notes, dict) or sorted(notes) != ['build', 'commit', 'device', 'path', 'sha256'] \
            or not re.fullmatch(r'[0-9a-f]{40}', str(notes['commit'])) or not NOTES_PATH.fullmatch(str(notes['path'])) \
            or '..' in str(notes['path']).split('/') or not HEX64.fullmatch(str(notes['sha256'])):
        raise Refused('notes_invalid: publication.notes must pin commit, os/release-notes/*.md path, sha256, build and device')
    if (notes['device'], notes['build']) != (target['device'], target['build']):
        raise Refused(f'notes_foreign: the pinned notes are for {notes["device"]} {notes["build"]}, not {target["device"]} {target["build"]}')
    if notes['commit'] == target['product']:
        raise Refused('notes_invalid: the notes are pinned separately from the product commit')
    return notes


def notes_bytes(notes):
    """The pinned UPDATE.md bytes, read from the notes commit (never the working tree) and re-hashed."""
    import subprocess
    result = subprocess.run(['git', '-C', str(NOTES_REPO[0]), 'show', f'{notes["commit"]}:{notes["path"]}'], capture_output=True)
    if result.returncode:
        raise Refused(f'notes_unavailable: commit {notes["commit"][:12]} has no {notes["path"]}')
    if hashlib.sha256(result.stdout).hexdigest() != notes['sha256']:
        raise Refused(f'notes_drift: {notes["path"]} at {notes["commit"][:12]} differs from its pinned sha256')
    if notes['build'].encode() not in result.stdout:
        raise Refused(f'notes_foreign: {notes["path"]} does not name build {notes["build"]}')
    return result.stdout


def check_notes_asset(path, notes):
    """The UPDATE.md that leaves the builder is exactly the pinned notes."""
    try:
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        raise Refused('notes_drift: no UPDATE.md to compare with the pinned notes') from None
    if digest != notes['sha256']:
        raise Refused('notes_drift: UPDATE.md is not the pinned release notes')


def updater_contract(cfg, check=False):
    """OCT30-INFRA-S018: verification.updater for release-pipeline, mode upstream only. The retained build was compiled
    with --keep-upstream-updater, so its recipe evidence is the pinned reconcile file's own applyArgs (path and sha256),
    never a typed-in argument list. check=True returns the unbound names instead of the contract."""
    spec = cfg['verification'].get('updater')
    if not isinstance(spec, dict) or spec.get('mode') != 'upstream':
        raise Refused('config_invalid: verification.updater must be the upstream contract of the keep-upstream-updater build')
    recipe = spec.get('recipe') or {}
    path = str(recipe.get('path', ''))
    if not re.fullmatch(r'[\w.-]+(/[\w.-]+)*', path) or '..' in path.split('/') or not HEX64.fullmatch(str(recipe.get('sha256'))):
        raise Refused('config_invalid: verification.updater.recipe must name a repository file and its sha256')
    try:
        data = (ROOT/path).read_bytes()
        args = json.loads(data)['host']['integrate']['reconcile']['applyArgs']
    except (OSError, ValueError, KeyError, TypeError):
        raise Refused('config_invalid: verification.updater.recipe is not a readable reconcile config') from None
    if hashlib.sha256(data).hexdigest() != recipe['sha256']:
        raise Refused('config_invalid: verification.updater.recipe differs from its pinned sha256')
    contract = {k: v for k, v in spec.items() if k not in ('recipe', 'basis')}
    contract['recipeApplyArgs'] = args
    module = pipeline()
    probe = {'target': {'device': 'rango'}, 'updater': contract}
    try:
        missing = module.updater_missing(probe)
    except module.InvalidInput as error:
        raise Refused(f'config_invalid: {error}') from None
    return missing if check else contract


def pipeline_config(cfg, binding):
    """A release-pipeline config for Rango built only from bound values: the verification expectations, the OTA and AVB
    identities of the genuine bootstrap, and the fixed rango-{build} never-Latest publication."""
    expected = cfg['verification'].get('expected')
    if not isinstance(expected, dict) or any(k in expected for k in PIPELINE_SECTIONS_FORBIDDEN) \
            or set(expected.get('identities') or {}) - {'appSignerSha256'} or not expected.get('variant'):
        raise Refused('config_invalid: verification.expected must not carry target, publication or OTA/AVB identities')
    config = json.loads(json.dumps(expected))
    config['target'] = {'device': 'rango', 'variant': expected['variant']}
    config['updater'] = updater_contract(cfg)
    config['identities'] = dict(expected.get('identities') or {}, otaCertificateSha256=binding['otaCertificateSha256'],
        avbPublicKeySha256=binding['avbPublicKeySha256'])
    publication = cfg['publication']
    config['publication'] = {'repository': publication['repository'], 'tag': publication['tag'], 'api': publication['api'],
        'uploads': publication['uploads'],
        'channels': {'beta': {'promoteLatest': False, 'finalizePublic': True, 'feedChannel': publication['feedChannel']}}}
    return config


def candidate(cfg, receipt):
    return {'sha': cfg['target']['product'], 'build': cfg['target']['build'], 'timestamp': str(receipt['build']['timestamp']),
        'channel': 'beta', 'device': 'rango'}


def bound(env, consumer, cfg=None, identity=True):
    """Everything bound and the consumer revalidated, or a refusal naming what is not. identity=False (signing) leaves a
    first-use identity to the protected signing operation; verify and staging resolve the same recorded binding."""
    cfg = check_config(cfg or config())
    receipt = bootstrap_receipt(env, cfg)
    missing = preflight(Path(consumer)/'input', receipt, cfg)
    if missing:
        raise SetupMissing(missing)
    input_receipt, _, _ = recheck(consumer, cfg)
    binding = resolve_binding(env, cfg) if identity or identity_policy(cfg) == 'bootstrap-receipt' else None
    apps = cfg['verification']['expected'].get('apps') or []
    rose = input_receipt['apps']['roseApk']
    if ['dev.pixel.voice', rose['versionCode']] not in [list(a) for a in apps]:
        raise Refused('input_foreign: the expected apps do not include the retained Rose Agent version')
    return cfg, input_receipt, binding, binding and pipeline_config(cfg, binding), candidate(cfg, input_receipt)


class Args:
    def __init__(self, **values):
        self.__dict__.update(values)


def sign(env, consumer, release_dir, signer=None, lock=None):
    """host-sign.py's retained route under the shared global lock, cwd at the original source root."""
    cfg, input_receipt, _, _, cand = bound(env, consumer, identity=False)
    release_dir = Path(release_dir)
    release_dir.mkdir(parents=True, exist_ok=False)
    data = Path(consumer)/'input'
    shutil.copyfile(data/cfg['input']['policy'], release_dir/'background-policy.json')
    shutil.copyfile(data/cfg['input']['resolvedManifest'], release_dir/'resolved-manifest.xml')
    sign_env = dict(env, ROSE_RETAINED_INPUT=str(consumer), ROSE_CANDIDATE_SHA=cand['sha'], BUILD_NUMBER=cand['build'],
        BUILD_DATETIME=cand['timestamp'], ROSE_RELEASE_DIR=str(release_dir))
    if signer is None:
        spec = importlib.util.spec_from_file_location('host_sign_for_rango', ROOT/'scripts/host-sign.py')
        host_sign = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(host_sign)
        signer = host_sign.main
    with (lock or shared_lock)(cfg['signing']['lockPath']):
        tools, proof = bind_tool_proof(env, cfg)  # rechecked under the same lock, immediately before any secret
        cwd = os.getcwd()
        os.chdir(env.get('ROSE_RETAINED_SOURCE_ROOT') or cfg['signing']['sourceRoot'])
        try:
            signer(env=sign_env)
        finally:
            os.chdir(cwd)
    return {'stage': 'sign', 'candidate': cand, 'tools': tools, 'toolProof': proof}


def verify(env, consumer, release_dir, adapter=None):
    """release-pipeline verify_signed with the Rango expectations; its allowlist, feed and receipt logic are unchanged."""
    _, _, _, config, cand = bound(env, consumer)
    module = pipeline()
    return module.verify_signed(Args(release_dir=str(release_dir)), config, cand, adapter or module.Production())


def stage_artifact(env, consumer, release_dir, artifact_dir, adapter=None):
    """release-pipeline stage_artifact plus the nonsecret bindings the hosted publisher revalidates."""
    cfg, input_receipt, binding, config, cand = bound(env, consumer)
    notes = notes_binding(cfg)
    check_notes_asset(Path(release_dir)/'public/UPDATE.md', notes)
    module = pipeline()
    apps = json.dumps(cfg['verification']['expected']['apps'])
    result = module.stage_artifact(Args(release_dir=str(release_dir), artifact_dir=str(artifact_dir), apps=apps), config, cand,
        adapter or module.Production())
    bindings = {'inputReceipt': input_receipt, 'bootstrapReceipt': bootstrap_receipt(env, cfg)}
    if binding.get('policy') == 'first-use-containers':
        bindings = {'inputReceipt': input_receipt, 'firstUseBinding': binding['record']}
    bindings['notesSource'] = notes  # recorded separately from the product commit
    fd = os.open(Path(artifact_dir)/'rango-bindings.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w') as f:
        json.dump(bindings, f, indent=2, sort_keys=True)
    return result


def publish(artifact_dir, promote=False, adapter=None, cfg=None, selected=False):
    """Hosted publisher, only on an explicit selection (OCT30-INFRA-S017: the workflow's rango_publish, default false):
    revalidate the bindings, then release-pipeline publish (draft, download check) and the adapter's never-Latest public
    finalization. promote refuses; Latest must not move. Signing-only runs never reach this."""
    cfg = check_config(cfg or config())
    if selected is not True:
        raise Refused('publication_unselected: a Rango release is published only when rango_publish is explicitly true')
    if promote:
        raise Refused('latest_refused: a Rango release is never promoted to Latest')
    try:
        bindings = json.loads((Path(artifact_dir)/'rango-bindings.json').read_text())
    except (OSError, ValueError):
        raise Refused('artifact_invalid: no Rango bindings in the transferred artifact') from None
    missing = check_receipt(bindings.get('inputReceipt'), cfg)
    missing += preflight(None, bindings.get('bootstrapReceipt'), cfg, inputs=False)
    if missing:
        raise SetupMissing(missing)
    if bindings.get('notesSource') != notes_binding(cfg):
        raise Refused('notes_foreign: the artifact names other release notes than the pinned ones')
    check_notes_asset(Path(artifact_dir)/'assets/UPDATE.md', bindings['notesSource'])
    if identity_policy(cfg) == 'first-use-containers':
        record = bindings.get('firstUseBinding')
        if not isinstance(record, dict) or any(k not in record for k in IDENTITY_FIELDS):
            raise Refused('identity_record_partial: the artifact carries no first-use binding')
        binding = first_use_binding(cfg, record)
    else:
        binding = signer_binding(bindings['bootstrapReceipt'])
    release_config, cand = pipeline_config(cfg, binding), candidate(cfg, bindings['inputReceipt'])
    module = pipeline()
    return module.publish(Args(artifact_dir=str(artifact_dir), promote=False, allocation=None), release_config, cand,
        adapter or module.Production())


def shared_lock(path):
    import contextlib
    import fcntl

    @contextlib.contextmanager
    def held():
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused('lock_held: the shared builder lock is held by another job') from None
            yield
        finally:
            os.close(fd)
    return held()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    sub = p.add_subparsers(dest='command', required=True)
    f = sub.add_parser('preflight')
    f.add_argument('--job', choices=['guard', 'builder'], default='guard')
    f.add_argument('--input-dir')
    f.add_argument('--bootstrap-receipt')
    s = sub.add_parser('stage')
    s.add_argument('--input-dir', help='default: the bound input.transport directory')
    s.add_argument('--consumer', required=True)
    sub.add_parser('recheck').add_argument('--consumer', required=True)
    sub.add_parser('prove').add_argument('--proof-dir', required=True)
    for name in ('sign', 'verify', 'stage-artifact'):
        c = sub.add_parser(name)
        c.add_argument('--consumer', required=True)
        c.add_argument('--release-dir', required=True)
        if name == 'stage-artifact':
            c.add_argument('--artifact-dir', required=True)
        if name == 'sign':
            c.add_argument('--proof-file', required=True)
    sub.add_parser('publish').add_argument('--artifact-dir', required=True)
    sub.add_parser('check-bootstrap-run').add_argument('--out', required=True)
    args = p.parse_args(argv)
    env = dict(os.environ)
    try:
        if args.command == 'preflight':
            receipt = json.loads(Path(args.bootstrap_receipt).read_text()) if args.bootstrap_receipt else None
            missing = preflight(args.input_dir, receipt)
            if missing:
                raise SetupMissing(missing)
            result = {'preflight': 'ready'}
        elif args.command == 'stage':
            transport = config()['input'].get('transport') or {}
            if not (args.input_dir or transport.get('directory')):
                raise SetupMissing(['input.transport'])
            result = stage(args.input_dir or transport['directory'], args.consumer)
        elif args.command == 'recheck':
            _, unbound, observed = recheck(args.consumer)
            result = dict(observed, unbound=unbound)
        elif args.command == 'prove':
            result = prove(env, args.proof_dir)
        elif args.command == 'sign':
            result = sign(dict(env, ROSE_RETAINED_PROOF=args.proof_file), args.consumer, args.release_dir)
        elif args.command == 'verify':
            result = verify(env, args.consumer, args.release_dir)
        elif args.command == 'check-bootstrap-run':
            result = bootstrap_provenance(env)
            fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, 'w') as f:
                json.dump(result, f, sort_keys=True)
        elif args.command == 'stage-artifact':
            result = stage_artifact(env, args.consumer, args.release_dir, args.artifact_dir)
        else:
            result = publish(args.artifact_dir, selected=env.get('ROSE_RANGO_PUBLISH') == 'true')
    except SetupMissing as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
        return 4
    except Refused as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error)}), file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - pipeline CheckFailed/SetupMissing and adapter failures, without payloads
        name = type(error).__name__
        if name == 'SetupMissing' and hasattr(error, 'missing'):
            print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
            return 4
        # 'Refused' here is host-sign's own copy of this module (the first-use hook); its messages carry no payload.
        detail = str(error) if name in ('CheckFailed', 'AdapterFailed', 'PublishError', 'Refused', 'InvalidInput') else name
        print(json.dumps({'verdict': 'FAIL', 'error': detail}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
