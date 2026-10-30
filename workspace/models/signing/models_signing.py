#!/usr/bin/env python3
"""Rose Models (dev.pixel.models) signing adapter.

Three stages, each usable alone:

  plan    closed-schema profile + path checks. Reads no credentials, runs no tool.
  verify  binds a retained unsigned artifact to the profile's expected hashes and source/run,
          using workspace/models/android/tests/verify-artifact.sh unchanged, then checking every
          value against the profile (never against the artifact's own claims alone).
  sign    refuses unless the profile's signer and tool are bound; then checks the operator's
          private files, signs a private copy with scripts/sign-apk.py unchanged (through an
          injectable executor), inspects the signed APK independently and writes a nonsecret
          receipt. Never generates keys, logs in, fetches secrets, installs or publishes.

The adapter owns only the new output directory it is given. It never deletes or rewrites the
operator's keystore or password file.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
VERIFY_ARTIFACT = REPO / 'workspace/models/android/tests/verify-artifact.sh'
SIGN_APK = REPO / 'scripts/sign-apk.py'
SIGNER_TIMEOUT_SECONDS = 900


def _load_sibling(name):
    spec = importlib.util.spec_from_file_location(f'rose_models_{name}', Path(__file__).with_name(f'{name}.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DIAG = _load_sibling('sign_diagnostic')
VERIFY = _load_sibling('synthetic_sign_check')  # the shared verifier argv and strict scheme parser

PACKAGE = 'dev.pixel.models'
ALIAS = 'rose-models'
PURPOSE = 'sign-retained-unsigned-models-apk'
SCHEMA = 'rose-models-signing-profile/1'
NATIVE_ENTRY = 'lib/arm64-v8a/librose_chat_android.so'
SIGNED_NAME = 'rose-models-signed.apk'
INPUT_FILES = ('SHA256SUMS', 'apk-metadata.txt', 'artifact-receipt.txt', 'build-receipt.txt',
               'librose_chat_android.so', 'rose-models-unsigned.apk')

HEX64 = re.compile(r'[0-9a-f]{64}')
HEX40 = re.compile(r'[0-9a-f]{40}')
DIGITS = re.compile(r'[0-9]+')

EXPECTED_FIELDS = {
    'githubEvent': re.compile(r'push'),
    'githubRef': re.compile(r'refs/heads/main'),
    'sourceSha': HEX40,
    'runId': DIGITS,
    'runAttempt': DIGITS,
    'apkSha256': HEX64,
    'nativeSha256': HEX64,
    'artifactReceiptSha256': HEX64,
    'apkMetadataSha256': HEX64,
    'buildReceiptSha256': HEX64,
    'treeAndroid': HEX40,
    'treeRoseChatAndroid': HEX40,
    'treeRoseChatCore': HEX40,
    'treeContracts': HEX40,
    'cargoLockRoseChatAndroid': HEX64,
    'cargoLockRoseChatCore': HEX64,
}
# artifact-receipt.txt field -> profile expectedInput field
RECEIPT_BINDING = {
    'github_event': 'githubEvent', 'github_ref': 'githubRef', 'source_sha': 'sourceSha',
    'github_sha': 'sourceSha', 'github_run_id': 'runId', 'github_run_attempt': 'runAttempt',
    'apk_sha256': 'apkSha256', 'native_sha256': 'nativeSha256',
    'native_in_apk_sha256': 'nativeSha256', 'tree_android': 'treeAndroid',
    'tree_rose_chat_android': 'treeRoseChatAndroid', 'tree_rose_chat_core': 'treeRoseChatCore',
    'tree_contracts': 'treeContracts',
    'cargo_lock_rose_chat_android_sha256': 'cargoLockRoseChatAndroid',
    'cargo_lock_rose_chat_core_sha256': 'cargoLockRoseChatCore',
}


class Refused(Exception):
    """A check failed. The message is a fixed category plus nonsecret detail, safe to log."""


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _exact_keys(obj, keys, where):
    if not isinstance(obj, dict) or set(obj) != set(keys):
        raise Refused(f'profile_schema: {where} must have exactly {sorted(keys)}')


# ---- plan ---------------------------------------------------------------------------------------

def load_profile(path):
    """Loads and validates the closed profile schema. Unknown or missing fields are refused."""
    try:
        profile = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise Refused(f'profile_unreadable: {type(error).__name__}') from None
    _exact_keys(profile, ['schema', 'purpose', 'package', 'keyAlias', 'versionCode', 'versionName',
                          'abi', 'requiredSchemes', 'expectedInput', 'signer', 'tool'], 'profile')
    if profile['schema'] != SCHEMA or profile['purpose'] != PURPOSE:
        raise Refused('profile_schema: unsupported schema or purpose')
    if profile['package'] != PACKAGE or profile['keyAlias'] != ALIAS:
        raise Refused('wrong_package: this adapter signs only dev.pixel.models with alias rose-models')
    if profile['abi'] != 'arm64-v8a' or profile['requiredSchemes'] != ['v2', 'v3']:
        raise Refused('profile_schema: abi must be arm64-v8a and schemes exactly v2,v3')
    if not (isinstance(profile['versionCode'], str) and DIGITS.fullmatch(profile['versionCode'])
            and isinstance(profile['versionName'], str) and profile['versionName']):
        raise Refused('profile_schema: versionCode/versionName')
    expected = profile['expectedInput']
    _exact_keys(expected, EXPECTED_FIELDS, 'expectedInput')
    for key, pattern in EXPECTED_FIELDS.items():
        if not isinstance(expected[key], str) or not pattern.fullmatch(expected[key]):
            raise Refused(f'unbound_input: expectedInput.{key} is not an exact pinned value')
    signer, tool = profile['signer'], profile['tool']
    _exact_keys(signer, ['status', 'certificateSha256'], 'signer')
    _exact_keys(tool, ['status', 'apksignerSha256', 'aaptSha256'], 'tool')
    _check_binding(signer, 'signer', ['certificateSha256'])
    _check_binding(tool, 'tool', ['apksignerSha256', 'aaptSha256'])
    return profile


def _check_binding(block, name, fields):
    if block['status'] == 'unbound':
        if any(block[f] is not None for f in fields):
            raise Refused(f'profile_schema: unbound {name} must not carry values')
    elif block['status'] == 'bound':
        if not all(isinstance(block[f], str) and HEX64.fullmatch(block[f]) for f in fields):
            raise Refused(f'profile_schema: bound {name} needs full lower-case SHA-256 values')
    else:
        raise Refused(f'profile_schema: {name}.status must be bound or unbound')


def readiness(profile):
    """Reasons the live signing call must refuse. Empty means ready."""
    reasons = []
    if profile['signer']['status'] != 'bound':
        reasons.append('signer_unbound')
    if profile['tool']['status'] != 'bound':
        reasons.append('tool_unbound')
    return reasons


def plan(profile, artifact_dir, output_dir):
    """Cheap guard: paths only. Opens no credential, runs no tool, contacts no service."""
    artifact_dir, output_dir = Path(artifact_dir), Path(output_dir)
    if artifact_dir.is_symlink() or not artifact_dir.is_dir():
        raise Refused('unsafe_path: artifact directory must be a real directory')
    if output_dir.exists() or output_dir.is_symlink():
        raise Refused('output_collision: output directory already exists')
    parent = output_dir.parent.resolve(strict=False)
    if not parent.is_dir() or output_dir.parent.is_symlink():
        raise Refused('unsafe_path: output parent must be an existing real directory')
    if parent == REPO or REPO in parent.parents:
        raise Refused('unsafe_path: output must be outside the repository')
    return {'artifact': artifact_dir.resolve(), 'output': parent / output_dir.name,
            'blocked': readiness(profile)}


# ---- verify -------------------------------------------------------------------------------------

def _receipt_fields(path):
    fields = {}
    for line in Path(path).read_text().splitlines():
        key, sep, value = line.partition(': ')
        if sep:
            fields.setdefault(key, value)
    return fields


def _badging(text):
    package = re.search(r"^package: name='([^']*)' versionCode='([^']*)' versionName='([^']*)'", text, re.M)
    natives = re.findall(r"^native-code: (.*)$", text, re.M)
    if not package:
        return None
    return {'package': package.group(1), 'versionCode': package.group(2),
            'versionName': package.group(3), 'nativeCode': natives}


def verify_input(profile, artifact_dir, run_tool=subprocess.run, aapt=None):
    """Binds the artifact to the profile. Returns the input hashes it checked."""
    exp = profile['expectedInput']
    artifact_dir = Path(artifact_dir)
    for name in INPUT_FILES:
        f = artifact_dir / name
        if f.is_symlink() or not f.is_file():
            raise Refused(f'input_inventory: {name} missing or not a regular file')
    args = ['bash', str(VERIFY_ARTIFACT), str(artifact_dir), '--source', exp['sourceSha'],
            '--run', exp['runId'], '--attempt', exp['runAttempt']]
    if aapt:
        args += ['--aapt', str(aapt)]
    result = run_tool(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise Refused('input_verifier_failed: ' + (result.stderr.strip().splitlines() or ['?'])[-1][:200])
    # Independent of SHA256SUMS: every byte must equal the profile's pinned hash.
    actual = {
        'apkSha256': sha256(artifact_dir / 'rose-models-unsigned.apk'),
        'nativeSha256': sha256(artifact_dir / 'librose_chat_android.so'),
        'artifactReceiptSha256': sha256(artifact_dir / 'artifact-receipt.txt'),
        'apkMetadataSha256': sha256(artifact_dir / 'apk-metadata.txt'),
        'buildReceiptSha256': sha256(artifact_dir / 'build-receipt.txt'),
    }
    for key, value in actual.items():
        if value != exp[key]:
            raise Refused(f'input_hash_mismatch: {key}')
    with zipfile.ZipFile(artifact_dir / 'rose-models-unsigned.apk') as apk:
        if hashlib.sha256(apk.read(NATIVE_ENTRY)).hexdigest() != exp['nativeSha256']:
            raise Refused('input_hash_mismatch: native library inside the APK')
    fields = _receipt_fields(artifact_dir / 'artifact-receipt.txt')
    for receipt_key, profile_key in RECEIPT_BINDING.items():
        if fields.get(receipt_key) != exp[profile_key]:
            raise Refused(f'source_binding: {receipt_key}')
    badging = _badging((artifact_dir / 'apk-metadata.txt').read_text())
    _require_identity(profile, badging, 'input')
    return actual


def _require_identity(profile, badging, where):
    if not badging or badging['package'] != profile['package']:
        raise Refused(f'wrong_package: {where} is not {profile["package"]}')
    if (badging['versionCode'], badging['versionName']) != (profile['versionCode'], profile['versionName']):
        raise Refused(f'wrong_version: {where}')
    if badging['nativeCode'] != ["'" + profile['abi'] + "'"]:
        raise Refused(f'wrong_abi: {where}')


def _require_signer(report, pin):
    if report.get('signers') != [pin]:
        raise Refused('signer_mismatch: expected exactly the bound signer')


# ---- sign ---------------------------------------------------------------------------------------

def check_private_file(path):
    """An operator-supplied secret file: regular, owner-only, not a symlink, outside the repo."""
    p = Path(path)
    if p.is_symlink():
        raise Refused('unsafe_private_input: symlink')
    try:
        st = p.stat()
        resolved = p.resolve(strict=True)
    except OSError:
        raise Refused('unsafe_private_input: missing or unreadable') from None
    if not p.is_file() or st.st_mode & 0o077 or st.st_uid != os.getuid():
        raise Refused('unsafe_private_input: must be an owner-only regular file')
    if resolved == REPO or REPO in resolved.parents:
        raise Refused('unsafe_private_input: inside the repository')
    return resolved


def run_sign_apk(argv, env):
    """Default executor: one run of scripts/sign-apk.py. Its stdout/stderr are discarded, never retained or echoed;
    what happened is read only from the closed --diagnostic-out record and the exit status."""
    return subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=SIGNER_TIMEOUT_SECONDS)


def inspect_signed(apk, sdk_root):
    """Default inspector: apksigner verify + aapt badging from the pinned build-tools. Verification covers API 24 and
    up so v2 and v3 are both checked (VERIFY.verify_argv); one bounded start, strict per-scheme result."""
    bt = Path(sdk_root) / 'build-tools/android-15'
    v = DIAG.run_bounded(VERIFY.verify_argv(bt / 'apksigner', apk))
    b = subprocess.run([str(bt / 'aapt'), 'dump', 'badging', str(apk)], capture_output=True, text=True)
    schemes = VERIFY.schemes(v.stdout)
    return {'verified': v.outcome == 'ok', 'schemes': {s: schemes[s] for s in ('v1', 'v2', 'v3', 'v4')},
            'signers': VERIFY.SIGNERS.findall(v.stdout),
            'badging': _badging(b.stdout) if b.returncode == 0 else None}


def tool_hashes(sdk_root):
    bt = Path(sdk_root) / 'build-tools/android-15'
    return {'apksignerSha256': sha256(bt / 'lib/apksigner.jar'), 'aaptSha256': sha256(bt / 'aapt')}


def _write_json(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(json.dumps(data, indent=2, sort_keys=True) + '\n')


def sign(profile, artifact_dir, output_dir, keystore, password_file, sdk_root, *,
         executor=run_sign_apk, inspector=inspect_signed, tools=tool_hashes,
         run_tool=subprocess.run, evidence_tier='live'):
    """The live callable for Infra. Returns the success receipt dict; raises Refused otherwise."""
    planned = plan(profile, artifact_dir, output_dir)
    if planned['blocked']:
        # Before any private path is touched or any tool is started.
        raise Refused('not_ready: ' + ','.join(planned['blocked']))
    expected = verify_input(profile, planned['artifact'], run_tool=run_tool,
                            aapt=Path(sdk_root) / 'build-tools/android-15/aapt')
    if tools(sdk_root) != {k: profile['tool'][k] for k in ('apksignerSha256', 'aaptSha256')}:
        raise Refused('tool_mismatch: installed build-tools differ from the bound tool identity')
    keystore, password_file = check_private_file(keystore), check_private_file(password_file)

    out = planned['output']
    out.mkdir(mode=0o700)
    work = out / 'work'
    work.mkdir(mode=0o700)
    stage = 'prepare'
    produced = {}
    diagnostic = {'wrapperExit': None, 'record': None, 'problem': 'not_run'}
    try:
        unsigned = work / 'rose-models-unsigned.apk'
        shutil.copyfile(planned['artifact'] / 'rose-models-unsigned.apk', unsigned)
        os.chmod(unsigned, 0o600)
        if sha256(unsigned) != profile['expectedInput']['apkSha256']:
            raise Refused('input_changed: copy differs from the bound input')
        config = work / 'sign-config.json'
        _write_json(config, {'keyAlias': ALIAS, 'storePasswordFile': str(password_file),
                             'keyPasswordFile': str(password_file)})
        signed = out / SIGNED_NAME
        diag_path = work / 'sign-diagnostic.json'
        pin = profile['signer']['certificateSha256']
        argv = [sys.executable, str(SIGN_APK), str(unsigned), '--keystore', str(keystore),
                '--config', str(config), '--output', str(signed), '--expected-cert-sha256', pin,
                '--diagnostic-out', str(diag_path)]
        if DIAG.validate_invocation(argv, SIGN_APK):
            raise Refused('invocation_invalid: the sign-apk.py call is not the fixed shape')
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'ANDROID_SDK_ROOT': str(sdk_root)}
        stage = 'sign'
        try:
            result = executor(argv, env)  # the one and only signing invocation; never retried
        except subprocess.TimeoutExpired:
            raise Refused('signer_timeout: sign-apk.py exceeded its time bound') from None
        wrapper_exit = result.returncode if type(result.returncode) is int else None
        record, problem = DIAG.load(diag_path, profile['tool']['apksignerSha256'])
        diagnostic.update(wrapperExit=wrapper_exit, record=record, problem=problem)
        for f in (signed, signed.with_suffix('.apk.idsig'), signed.with_suffix('.receipt.json')):
            if f.exists():
                produced[f.name] = sha256(f)
        if wrapper_exit != 0:
            raise Refused('signer_failed: ' + DIAG.summary(record, problem, wrapper_exit))
        if record is None or record['stage'] != 'complete':
            # Fail closed: a zero wrapper exit without a valid 'complete' record is not accepted.
            raise Refused('signer_diagnostic_invalid: ' + DIAG.summary(record, problem, wrapper_exit))

        stage = 'inspect'
        for f in (planned['artifact'] / 'rose-models-unsigned.apk', unsigned):
            if sha256(f) != profile['expectedInput']['apkSha256']:
                raise Refused('input_changed: unsigned input changed during signing')
        if not signed.is_file() or signed.is_symlink():
            raise Refused('output_missing: no signed APK')
        signed_sha = sha256(signed)
        sign_receipt = json.loads(signed.with_suffix('.receipt.json').read_text())
        if (sign_receipt.get('sha256') != signed_sha or sign_receipt.get('inputSha256') != expected['apkSha256']
                or sign_receipt.get('certificateSha256') != [pin]):
            raise Refused('signer_receipt_mismatch')
        report = inspector(signed, sdk_root)
        if not report.get('verified'):
            raise Refused('signature_unverified')
        _require_signer(report, pin)
        missing = [s for s in profile['requiredSchemes'] if not report.get('schemes', {}).get(s)]
        if missing:
            raise Refused('scheme_missing: ' + ','.join(missing))
        _require_identity(profile, report.get('badging'), 'signed output')
        with zipfile.ZipFile(signed) as apk:
            if hashlib.sha256(apk.read(NATIVE_ENTRY)).hexdigest() != expected['nativeSha256']:
                raise Refused('output_native_mismatch')

        shutil.rmtree(work)  # adapter-owned copy and config (it names private paths)
        exp = profile['expectedInput']
        receipt = {
            'schema': 'rose-models-signing-receipt/1',
            'evidenceTier': evidence_tier,
            'notice': ('MOCK SOURCE TEST: no real signature, key or tool was used'
                       if evidence_tier != 'live' else 'signed by Infra-supplied private inputs'),
            'package': PACKAGE, 'keyAlias': ALIAS,
            'versionCode': profile['versionCode'], 'versionName': profile['versionName'], 'abi': profile['abi'],
            'source': {k: exp[k] for k in ('githubEvent', 'githubRef', 'sourceSha', 'runId', 'runAttempt')},
            'input': {k: exp[k] for k in ('apkSha256', 'nativeSha256', 'artifactReceiptSha256')},
            'output': {'apk': SIGNED_NAME, 'sha256': signed_sha, 'files': produced},
            'signerCertificateSha256': pin,
            'schemes': {s: bool(report['schemes'].get(s)) for s in ('v1', 'v2', 'v3', 'v4')},
            'recipe': {'signApkSha256': sha256(SIGN_APK), 'adapterSha256': sha256(Path(__file__)),
                       'profileSha256': hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()},
            'tool': {k: profile['tool'][k] for k in ('apksignerSha256', 'aaptSha256')},
            'signerDiagnostic': diagnostic['record'], 'signerWrapperExit': diagnostic['wrapperExit'],
            'installed': False, 'published': False,
        }
        _write_json(out / 'models-signing-receipt.json', receipt)
        return receipt
    except Exception as error:
        category = str(error) if isinstance(error, Refused) else f'internal_error: {type(error).__name__}'
        # No partial output may look complete: signed files leave, their hashes stay as evidence.
        signed = out / SIGNED_NAME
        for f in (signed, signed.with_suffix('.apk.idsig'), signed.with_suffix('.receipt.json')):
            f.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)
        _write_json(out / 'models-signing-failure.json', {
            'schema': 'rose-models-signing-failure/1', 'evidenceTier': evidence_tier,
            'stage': stage, 'category': category, 'removedOutputSha256': produced,
            'signerDiagnostic': diagnostic['record'], 'signerDiagnosticProblem': diagnostic['problem'],
            'signerWrapperExit': diagnostic['wrapperExit'],
            'inputApkSha256': profile['expectedInput']['apkSha256'], 'installed': False, 'published': False})
        raise Refused(category) from None


# ---- CLI ----------------------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('plan', 'verify', 'sign'):
        s = sub.add_parser(name)
        s.add_argument('--profile', type=Path, default=Path(__file__).with_name('profile.json'))
        s.add_argument('--artifact', type=Path, required=name != 'plan')
        if name in ('plan', 'sign'):
            s.add_argument('--output', type=Path, required=name == 'sign')
        if name == 'verify':
            s.add_argument('--aapt', type=Path)
        if name == 'sign':
            s.add_argument('--keystore', type=Path, required=True)
            s.add_argument('--password-file', type=Path, required=True)
            s.add_argument('--sdk-root', type=Path, required=True)
    a = p.parse_args(argv)
    try:
        profile = load_profile(a.profile)
        if a.command == 'plan':
            blocked = readiness(profile)
            if a.artifact and a.output:
                blocked = plan(profile, a.artifact, a.output)['blocked']
            print(json.dumps({'profile': 'valid', 'package': PACKAGE, 'blocked': blocked}))
            return 3 if blocked else 0
        if a.command == 'verify':
            print(json.dumps({'input': 'bound', **verify_input(profile, a.artifact, aapt=a.aapt)}))
            return 0
        print(json.dumps(sign(profile, a.artifact, a.output, a.keystore, a.password_file, a.sdk_root)))
        return 0
    except Refused as error:
        print('models signing refused: ' + str(error), file=sys.stderr)
        return 2
    except OSError as error:
        # Never echo paths: they may name private locations.
        print(f'models signing refused: missing_or_unreadable_input: {type(error).__name__}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
