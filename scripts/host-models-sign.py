#!/usr/bin/env python3
"""Protected signing of the retained unsigned Models APK (OCT30-INFRA-S013).

Signs dev.pixel.models only through the PR #149 adapter (workspace/models/signing/models_signing.py) with the
dedicated identity the protected models-v0 bootstrap committed. It never generates or imports keys, downloads input,
publishes or installs; input arrives only through scripts/models-input-ingress.py.

  check-receipt --receipt-dir D --run-id N   hosted guard: the bootstrap run's public receipt must record a complete
                                             models-v0 bootstrap: committed numeric version of each Models secret,
                                             public certificate, alias and contract, pinned JDK tree proof, cleanup
                                             done, no recovery needed, and a recipe on main.
  preflight --receipt-dir D --input-dir I --ingress-receipt R
                                             builder, before any login: the guard-verified input handed to this run
                                             holds exactly the six pinned files equal to the profile and the guard's
                                             ingress receipt (else input_unavailable); the on-host bootstrap journal
                                             agrees with the receipt (versions, certificate, alias); the retained SDK
                                             archive has its pinned sha256 and its android-15 subtree equals the
                                             installed build-tools/android-15 file by file; the JDK21 tree equals the
                                             models-v0 pin.
  run --receipt-dir D --output-dir O --input-dir I --ingress-receipt R
                                             builder, after signer login, under the shared lock: the same proofs again,
                                             the adapter's input binding with the proven aapt, then exactly the
                                             committed secret versions into owner-only RAM, the
                                             keystore certificate (pinned keytool, password file) must equal the
                                             bootstrap certificate before signing, then the adapter signs with an
                                             ephemeral profile bound only from these proofs and checks the final signer
                                             itself. Private copies are overwritten and removed on every outcome.

Exit status: 0 done, 1 refused (a fixed category on stderr), 2 invalid input.
"""
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import host_secrets  # noqa: E402

CONFIG_PATH = ROOT/'os/models-signing/models-sign.json'
PROFILES_PATH = ROOT/'os/signing-bootstrap/profiles.json'
PIPELINE_PATH = ROOT/'os/release-pipeline/frankel.json'
INPUT_FILES = {'SHA256SUMS': None, 'apk-metadata.txt': 'apkMetadataSha256', 'artifact-receipt.txt': 'artifactReceiptSha256',
    'build-receipt.txt': 'buildReceiptSha256', 'librose_chat_android.so': 'nativeSha256', 'rose-models-unsigned.apk': 'apkSha256'}
HEX64 = re.compile(r'[0-9a-f]{64}')
SHA1 = re.compile(r'[0-9a-f]{40}')
VERSION = re.compile(r'[1-9][0-9]{0,9}')
# Variables that let a JVM or the dynamic loader load foreign code; none reaches keytool, apksigner or aapt.
INJECTION = ('JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS', 'CLASSPATH', 'JAVA_OPTS', 'LD_PRELOAD',
    'LD_LIBRARY_PATH', 'LD_AUDIT')


class Refused(Exception):
    pass


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config():
    return json.loads(CONFIG_PATH.read_text())


def models_profile():
    return json.loads(PROFILES_PATH.read_text())['profiles']['models-v0']


def sha256_file(path, limit=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    digest, total = hashlib.sha256(), 0
    with os.fdopen(fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise Refused(f'not_regular: {Path(path).name}')
        for block in iter(lambda: f.read(8 << 20), b''):
            total += len(block)
            if limit and total > limit:
                raise Refused(f'too_large: {Path(path).name}')
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------- bootstrap receipt and journal

def check_receipt(receipt_dir, run_id, bootstrap=None, is_on_main=None):
    """The public bootstrap receipt of run_id must record one complete models-v0 bootstrap. Returns the binding."""
    bootstrap = bootstrap or models_profile()
    cfg = config()['bootstrap']
    path = Path(receipt_dir)/cfg['receiptFile']
    try:
        receipt = json.loads(path.read_text())
    except (OSError, ValueError):
        raise Refused('bootstrap_receipt_missing: no readable public receipt from the bootstrap run')
    problems = []
    if receipt.get('profile') != cfg['profile'] or receipt.get('status') != 'complete' or receipt.get('recoveryNeeded'):
        problems.append('not a complete models-v0 bootstrap')
    if not SHA1.fullmatch(str(receipt.get('recipeSha'))):
        problems.append('recipeSha missing')
    elif is_on_main and not is_on_main(receipt['recipeSha']):
        problems.append('recipeSha is not on main')
    versions = {}
    for role, name in bootstrap['secrets'].items():
        entry = (receipt.get('secrets') or {}).get(role) or {}
        if entry.get('name') != name or entry.get('state') != 'committed' or not VERSION.fullmatch(str(entry.get('committedVersion'))):
            problems.append(f'{role} secret {name} has no committed exact version')
        else:
            versions[role] = str(entry['committedVersion'])
    identity = receipt.get('publicIdentity') or {}
    expected = bootstrap['generation']['identity']
    if not HEX64.fullmatch(str(identity.get('certificateSha256'))):
        problems.append('publicIdentity.certificateSha256 missing')
    for key, value in expected.items():
        if identity.get(key) != value:
            problems.append(f'publicIdentity.{key} is {identity.get(key)!r}, not {value!r}')
    tool = (receipt.get('tools') or {}).get('keytool') or {}
    trust = bootstrap['tools']['keytool']['trust']
    if tool.get('trustBasis') != 'pinned-git-tree' or tool.get('treeId') != trust['treeId'] or tool.get('revision') != trust['revision']:
        problems.append('keytool proof is not the pinned JDK21 tree')
    if (receipt.get('cleanup') or {}).get('workspaceRemoved') is not True:
        problems.append('bootstrap cleanup not confirmed')
    if problems:
        raise Refused('bootstrap_receipt_invalid: ' + '; '.join(problems))
    return {'runId': str(run_id), 'recipeSha': receipt['recipeSha'], 'versions': versions,
        'certificateSha256': identity['certificateSha256'], 'alias': identity['alias'],
        'receiptSha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def check_journal(home, binding):
    """The on-host write-once journal of the protected bootstrap must agree with the public receipt exactly."""
    path = Path(home)/config()['bootstrap']['journal']
    try:
        info = os.lstat(path)
    except OSError:
        raise Refused('bootstrap_journal_missing: the protected bootstrap journal is not on this host')
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Refused('bootstrap_journal_unsafe: not an owner-only regular file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        try:
            journal = json.load(f)
        except ValueError:
            raise Refused('bootstrap_journal_unsafe: unreadable')
    identity = journal.get('publicIdentity') or {}
    committed = {k: str(v) for k, v in (journal.get('committed') or {}).items()}
    if journal.get('profile') != config()['bootstrap']['profile'] or journal.get('status') != 'complete':
        raise Refused('bootstrap_journal_foreign: not a complete models-v0 journal')
    if committed != binding['versions']:
        raise Refused('bootstrap_version_mismatch: journal and receipt versions differ')
    if identity.get('certificateSha256') != binding['certificateSha256'] or identity.get('alias') != binding['alias']:
        raise Refused('bootstrap_identity_mismatch: journal and receipt certificate or alias differ')
    return hashlib.sha256(json.dumps(journal, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------- input and tool proofs

def check_input(input_dir, ingress_receipt, consumer_run=None):
    """The guard-verified original input, rechecked after the same-run handoff: exactly the six files, each equal to
    the profile and to the guard's ingress receipt for this run; else input_unavailable / input_changed."""
    ingress = load('models_input_ingress', ROOT/'scripts/models-input-ingress.py')
    try:
        return ingress.recheck(input_dir, ingress_receipt, consumer_run or os.environ.get('GITHUB_RUN_ID'))
    except ingress.Refused as error:
        raise Refused(str(error)) from None


def _member_link(info):
    return stat.S_ISLNK(info.external_attr >> 16)


def archive_proof(cfg=None):
    """Rehash the retained Google archive, then require its android-15 subtree to equal the installed
    build-tools/android-15 tree file by file (bytes, executable bit, in-tree links). Returns the member hashes."""
    cfg = cfg or config()
    sdk, limits = cfg['sdk'], cfg['limits']
    try:
        if sha256_file(sdk['archive'], limits['archiveBytes']) != sdk['archiveSha256']:
            raise Refused('sdk_archive_mismatch: the retained archive is not the pinned build-tools_r35_linux.zip')
    except FileNotFoundError:
        raise Refused('sdk_archive_missing: the pinned build-tools archive is not retained on this host')
    prefix, installed = sdk['archivePrefix'], Path(sdk['root'])/sdk['installedDirectory']
    if os.path.realpath(installed) != str(installed) or not installed.is_dir():
        raise Refused('sdk_installed_unsafe: build-tools/android-15 is missing or reached through a symlink')
    expected, members = {}, {}
    with zipfile.ZipFile(sdk['archive']) as archive:
        infos = archive.infolist()
        if len(infos) > limits['archiveMembers']:
            raise Refused('sdk_archive_unsafe: too many members')
        for info in infos:
            parts = PurePosixPath(info.filename).parts
            if info.filename.startswith('/') or '..' in parts or not parts or parts[0] != prefix:
                raise Refused(f'sdk_archive_unsafe: unexpected member {info.filename[:60]!r}')
            rel = '/'.join(parts[1:])
            if info.is_dir() or not rel:
                continue
            if info.file_size > limits['memberBytes']:
                raise Refused(f'sdk_archive_unsafe: {rel} too large')
            data = archive.read(info)
            if _member_link(info):
                target = data.decode()
                resolved = os.path.normpath(os.path.join(os.path.dirname(rel), target))
                if os.path.isabs(target) or resolved.startswith('..'):
                    raise Refused(f'sdk_archive_unsafe: link {rel} leaves the tree')
                expected[rel] = ('link', target)
            else:
                expected[rel] = ('file', hashlib.sha256(data).hexdigest(), bool((info.external_attr >> 16) & 0o100))
            if rel in sdk['members']:
                members[rel] = expected[rel]
    for rel in sdk['members']:
        if rel not in members or members[rel][0] != 'file':
            raise Refused(f'sdk_archive_unsafe: {rel} is not a regular archive member')
    observed = {}
    for dirpath, dirnames, filenames in os.walk(installed, followlinks=False):
        for name in dirnames + filenames:
            path = Path(dirpath)/name
            rel = str(path.relative_to(installed))
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                observed[rel] = ('link', os.readlink(path))
            elif stat.S_ISREG(info.st_mode):
                observed[rel] = ('file', sha256_file(path), bool(info.st_mode & stat.S_IXUSR))
            elif not stat.S_ISDIR(info.st_mode):
                raise Refused(f'sdk_installed_unsafe: special file {rel}')
    if observed != expected:
        drift = sorted(set(observed) ^ set(expected)) + sorted(k for k in set(observed) & set(expected) if observed[k] != expected[k])
        raise Refused(f'sdk_installed_mismatch: installed build-tools/android-15 differs from the pinned archive ({drift[:5]})')
    return {'apksignerSha256': members['lib/apksigner.jar'][1], 'aaptSha256': members['aapt'][1],
        'archiveSha256': sdk['archiveSha256']}


def jdk_proof(tree_fn=None):
    """The models-v0 pinned JDK21 tree (S012): recomputed locally, never a host checksum list."""
    trust = models_profile()['tools']['keytool']['trust']
    sb = load('signing_bootstrap_for_models_sign', ROOT/'scripts/signing-bootstrap.py')
    root = Path(trust['root'])/trust['treePath']
    try:
        observed = (tree_fn or sb.git_tree_id)(root)
    except sb.Blocked as error:
        raise Refused(f'jdk_unverified: {error.detail}')
    if observed != trust['treeId']:
        raise Refused('jdk_mismatch: the local JDK21 tree is not the pinned tree')
    return {'root': str(Path(trust['root'])/trust['treePath']), 'treeId': trust['treeId'], 'revision': trust['revision']}


def java_environment(jdk_home):
    """The only environment keytool, apksigner and aapt see: explicit pinned JDK first, nothing injectable."""
    for name in INJECTION:
        os.environ.pop(name, None)
    os.environ.update(PATH=f'{jdk_home}/bin:/usr/bin:/bin', JAVA_HOME=jdk_home, LC_ALL='C')
    return {k: os.environ[k] for k in ('PATH', 'JAVA_HOME', 'LC_ALL')}


def preflight(receipt_dir, input_dir=None, ingress_receipt=None, home=None, tree_fn=None):
    adapter = json.loads((ROOT/config()['adapterProfile']).read_text())
    binding = check_receipt(receipt_dir, os.environ.get('MODELS_BOOTSTRAP_RUN', 'unrecorded'))
    ingress = check_input(input_dir, ingress_receipt)
    journal = check_journal(home or os.environ['HOME'], binding)
    tools = archive_proof()
    jdk = jdk_proof(tree_fn)
    return {'adapterProfile': adapter, 'binding': binding, 'input': ingress['directory'], 'ingress': ingress,
        'journalSha256': journal, 'tools': tools, 'jdk': jdk}


# ---------------------------------------------------------------- run

def keystore_certificate(keytool, keystore, password_file, private, runner=subprocess.run):
    """Public certificate of the fetched store through the pinned keytool and its password-file option."""
    cert = Path(private)/'rose-models.x509.pem'
    result = runner([keytool, '-exportcert', '-rfc', '-alias', 'rose-models', '-keystore', str(keystore), '-storetype', 'PKCS12',
        '-storepass:file', str(password_file), '-file', str(cert)], env=dict(os.environ), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 0 or not cert.is_file():
        raise Refused('signer_unreadable: the fetched keystore did not yield the rose-models certificate')
    return host_secrets.certificate_sha256(cert)


def bound_profile(adapter_profile, binding, tools):
    """An ephemeral adapter profile bound only from the validated proofs; the committed profile stays unbound."""
    profile = json.loads(json.dumps(adapter_profile))
    profile['signer'] = {'status': 'bound', 'certificateSha256': binding['certificateSha256']}
    profile['tool'] = {'status': 'bound', 'apksignerSha256': tools['apksignerSha256'], 'aaptSha256': tools['aaptSha256']}
    return profile


@contextlib.contextmanager
def shared_lock():
    path = json.loads(PIPELINE_PATH.read_text())['host']['lockPath']
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('lock_held: the shared builder lock is held by another job')
        yield
    finally:
        os.close(fd)


def run(receipt_dir, output_dir, input_dir=None, ingress_receipt=None, home=None, tree_fn=None, fetch=host_secrets.fetch_secret,
        keytool_runner=subprocess.run, adapter=None, lock=shared_lock, private_directory=host_secrets.private_directory,
        run_tool=subprocess.run):
    with lock():
        checked = preflight(receipt_dir, input_dir, ingress_receipt, home, tree_fn)
        bootstrap = models_profile()
        binding, tools = checked['binding'], checked['tools']
        adapter = adapter or load('models_signing_adapter', ROOT/'workspace/models/signing/models_signing.py')
        env = java_environment(checked['jdk']['root'])
        profile = bound_profile(checked['adapterProfile'], binding, tools)
        # Before any secret: the adapter's own input binding, now with the archive-proven aapt (badging included).
        try:
            adapter.verify_input(profile, checked['input'], run_tool=run_tool,
                aapt=Path(config()['sdk']['root'])/config()['sdk']['installedDirectory']/'aapt')
        except adapter.Refused as error:
            raise Refused(f'input_binding: {error}')
        with private_directory('rose-models-sign-') as private:
            keystore = fetch(bootstrap['project'], bootstrap['secrets']['keys'], binding['versions']['keys'], private/'keystore.p12')
            password = fetch(bootstrap['project'], bootstrap['secrets']['passphrase'], binding['versions']['passphrase'],
                private/'passphrase')
            observed = keystore_certificate(str(Path(checked['jdk']['root'])/'bin/keytool'), keystore, password, private,
                keytool_runner)
            if observed != binding['certificateSha256']:
                raise Refused('signer_mismatch: the fetched keystore is not the bootstrap certificate')
            try:
                receipt = adapter.sign(profile, checked['input'], output_dir, keystore, password, config()['sdk']['root'])
            except adapter.Refused as error:
                raise Refused(f'adapter_refused: {error}')
    public = {'schema': 'roseos.models-sign-receipt.v1', 'adapterReceipt': receipt,
        'input': {'ingressReceiptSha256': checked['ingress']['receiptSha256'], 'files': checked['ingress']['files'],
            'original': checked['ingress']['original']},
        'bootstrap': {'runId': binding['runId'], 'recipeSha': binding['recipeSha'], 'receiptSha256': binding['receiptSha256'],
            'journalSha256': checked['journalSha256'], 'versions': binding['versions'], 'certificateSha256': binding['certificateSha256'],
            'alias': binding['alias']},
        'tools': {'archive': tools['archiveSha256'], 'apksignerSha256': tools['apksignerSha256'], 'aaptSha256': tools['aaptSha256'],
            'jdkTree': checked['jdk']['treeId'], 'jdkRevision': checked['jdk']['revision']},
        'environment': sorted(env), 'published': False, 'installed': False}
    fd = os.open(Path(output_dir)/'models-sign-receipt.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(public, f, indent=2, sort_keys=True)
    return public


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    sub = parser.add_subparsers(dest='command', required=True)
    c = sub.add_parser('check-receipt')
    c.add_argument('--receipt-dir', required=True)
    c.add_argument('--run-id', required=True)
    p = sub.add_parser('preflight')
    p.add_argument('--receipt-dir', required=True)
    r = sub.add_parser('run')
    r.add_argument('--receipt-dir', required=True)
    r.add_argument('--output-dir', required=True)
    for command in (p, r):
        command.add_argument('--input-dir')
        command.add_argument('--ingress-receipt')
    args = parser.parse_args(argv)
    try:
        if args.command == 'check-receipt':
            if not re.fullmatch(r'[1-9][0-9]{0,19}', args.run_id):
                print('invalid: --run-id must be a numeric run id', file=sys.stderr)
                return 2
            on_main = lambda sha: subprocess.run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', sha, 'origin/main'],
                capture_output=True).returncode == 0
            result = check_receipt(args.receipt_dir, args.run_id, is_on_main=on_main)
        elif args.command == 'preflight':
            checked = preflight(args.receipt_dir, args.input_dir, args.ingress_receipt)
            result = {k: checked[k] for k in ('binding', 'journalSha256', 'tools', 'jdk')}
            result['input'] = checked['ingress']['files']
        else:
            result = run(args.receipt_dir, args.output_dir, args.input_dir, args.ingress_receipt)
    except Refused as error:
        print(f'refused: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
