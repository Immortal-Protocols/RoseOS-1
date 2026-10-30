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
  sign --consumer C --release-dir R
                       builder: host-sign.py's retained route under the shared lock (bound config, revalidated input)
  verify --consumer C --release-dir R
                       builder: release-pipeline verify_signed with the bound Rango expectations and identities
  stage-artifact --consumer C --release-dir R --artifact-dir A
                       builder: release-pipeline stage_artifact plus the nonsecret bindings for the publisher
  publish --artifact-dir A
                       hosted publisher: revalidate the bindings, release-pipeline publish (draft, download check), then
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


def tool_bindings(prof=None):
    """The four installed signing tools stay unbound until genuinely observed (source hashes are not installed identities)."""
    prof = prof or profile()
    return [f'signing.tools.{name}.installed' for name in TOOLS
        if not ((prof['tools'].get(name) or {}).get('installed') or {}).get('path')
        or not HEX64.fullmatch(str(((prof['tools'].get(name) or {}).get('installed') or {}).get('sha256')))]


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
    if receipt is None:
        missing.append('signing.bootstrapReceipt')
    else:
        signer_binding(receipt)
    missing += tool_bindings()
    for key in ('otatoolsProvenance', 'releaseScriptProvenance'):
        if cfg['signing'].get(key) is None:
            missing.append(f'signing.{key}')
    for key in ('feedChannel', 'notes'):
        if cfg['publication'].get(key) is None:
            missing.append(f'publication.{key}')
    if cfg['verification'].get('expected') is None:
        missing.append('verification.expected')
    return list(dict.fromkeys(missing))


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
    binding = signer_binding(bootstrap)
    if env.get('BUILD_NUMBER') != cfg['target']['build'] or env.get('BUILD_DATETIME') != str(receipt['build']['timestamp']):
        raise Refused('input_foreign: the signing build number and BUILD_DATETIME are not the retained build')
    bound = {'target': {'device': 'rango'}, 'identities': {'otaCertificateSha256': binding['otaCertificateSha256'],
            'avbPublicKeySha256': binding['avbPublicKeySha256']},
        'host': {'sign': {'project': profile(cfg)['project'], 'keysSecret': binding['secrets']['keys'],
            'passphraseSecret': binding['secrets']['passphrase'], 'secretVersion': binding['versions']['keys'],
            'passphraseSecretVersion': binding['versions']['passphrase'], 'otatools': cfg['input']['otatools'],
            'javaHome': jdk_home(), 'updateNotes': cfg['publication']['notes']}},
        'publication': {'channels': {'beta': {'feedChannel': cfg['publication']['feedChannel']}}}}
    data = Path(consumer)/'input'
    return bound, data/cfg['input']['targetFiles'], data/cfg['input']['otatools']


# ---------------------------------------------------------------- connected route (existing pipeline functions)

PIPELINE_SECTIONS_FORBIDDEN = ('target', 'publication', 'fixture')


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


def pipeline_config(cfg, binding):
    """A release-pipeline config for Rango built only from bound values: the verification expectations, the OTA and AVB
    identities of the genuine bootstrap, and the fixed rango-{build} never-Latest publication."""
    expected = cfg['verification'].get('expected')
    if not isinstance(expected, dict) or any(k in expected for k in PIPELINE_SECTIONS_FORBIDDEN) \
            or set(expected.get('identities') or {}) - {'appSignerSha256'} or not expected.get('variant'):
        raise Refused('config_invalid: verification.expected must not carry target, publication or OTA/AVB identities')
    config = json.loads(json.dumps(expected))
    config['target'] = {'device': 'rango', 'variant': expected['variant']}
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


def bound(env, consumer, cfg=None):
    """Everything bound and the consumer revalidated, or a refusal naming what is not."""
    cfg = check_config(cfg or config())
    receipt = bootstrap_receipt(env, cfg)
    missing = preflight(Path(consumer)/'input', receipt, cfg)
    if missing:
        raise SetupMissing(missing)
    input_receipt, _, _ = recheck(consumer, cfg)
    binding = signer_binding(receipt)
    apps = cfg['verification']['expected'].get('apps') or []
    rose = input_receipt['apps']['roseApk']
    if ['dev.pixel.voice', rose['versionCode']] not in [list(a) for a in apps]:
        raise Refused('input_foreign: the expected apps do not include the retained Rose Agent version')
    return cfg, input_receipt, binding, pipeline_config(cfg, binding), candidate(cfg, input_receipt)


class Args:
    def __init__(self, **values):
        self.__dict__.update(values)


def sign(env, consumer, release_dir, signer=None, lock=None):
    """host-sign.py's retained route under the shared global lock, cwd at the original source root."""
    cfg, input_receipt, _, _, cand = bound(env, consumer)
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
        cwd = os.getcwd()
        os.chdir(env.get('ROSE_RETAINED_SOURCE_ROOT') or cfg['signing']['sourceRoot'])
        try:
            signer(env=sign_env)
        finally:
            os.chdir(cwd)
    return {'stage': 'sign', 'candidate': cand}


def verify(env, consumer, release_dir, adapter=None):
    """release-pipeline verify_signed with the Rango expectations; its allowlist, feed and receipt logic are unchanged."""
    _, _, _, config, cand = bound(env, consumer)
    module = pipeline()
    return module.verify_signed(Args(release_dir=str(release_dir)), config, cand, adapter or module.Production())


def stage_artifact(env, consumer, release_dir, artifact_dir, adapter=None):
    """release-pipeline stage_artifact plus the nonsecret bindings the hosted publisher revalidates."""
    cfg, input_receipt, _, config, cand = bound(env, consumer)
    module = pipeline()
    apps = json.dumps(cfg['verification']['expected']['apps'])
    result = module.stage_artifact(Args(release_dir=str(release_dir), artifact_dir=str(artifact_dir), apps=apps), config, cand,
        adapter or module.Production())
    bindings = {'inputReceipt': input_receipt, 'bootstrapReceipt': bootstrap_receipt(env, cfg)}
    fd = os.open(Path(artifact_dir)/'rango-bindings.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w') as f:
        json.dump(bindings, f, indent=2, sort_keys=True)
    return result


def publish(artifact_dir, promote=False, adapter=None, cfg=None):
    """Hosted publisher: revalidate the bindings, then release-pipeline publish (draft, download check) and the adapter's
    never-Latest public finalization. promote refuses; Latest must not move."""
    cfg = check_config(cfg or config())
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
    for name in ('sign', 'verify', 'stage-artifact'):
        c = sub.add_parser(name)
        c.add_argument('--consumer', required=True)
        c.add_argument('--release-dir', required=True)
        if name == 'stage-artifact':
            c.add_argument('--artifact-dir', required=True)
    sub.add_parser('publish').add_argument('--artifact-dir', required=True)
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
        elif args.command == 'sign':
            result = sign(env, args.consumer, args.release_dir)
        elif args.command == 'verify':
            result = verify(env, args.consumer, args.release_dir)
        elif args.command == 'stage-artifact':
            result = stage_artifact(env, args.consumer, args.release_dir, args.artifact_dir)
        else:
            result = publish(args.artifact_dir)
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
        detail = str(error) if name in ('CheckFailed', 'AdapterFailed', 'PublishError') else name
        print(json.dumps({'verdict': 'FAIL', 'error': detail}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
