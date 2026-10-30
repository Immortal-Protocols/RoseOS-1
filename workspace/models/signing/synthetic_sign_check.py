#!/usr/bin/env python3
"""Required synthetic sign/verify check of the corrected Models signing invocation (MODELS-SIGN-PRECHECK-001).

    python3 workspace/models/signing/synthetic_sign_check.py --jdk-home <JDK root> --sdk-root <SDK root>

The caller passes roots it has ALREADY proven (the pinned JDK tree; the SDK whose build-tools/android-15 equals the
pinned build-tools_r35_linux.zip). This check proves nothing about their identity: it reports observed hashes only.
It uses exactly <jdk>/bin/java and keytool and <sdk>/build-tools/android-15/apksigner and zipalign, with an explicit
minimal environment (no PATH search beyond <jdk>/bin then /usr/bin:/bin for the apksigner launcher's shell utilities,
no JAVA_TOOL_OPTIONS or other inherited variable). Nothing is skipped: a missing or failing piece is a failure.

In a fresh owner-only directory (removed on success, failure and interruption) it creates disposable synthetic keys
and a synthetic APK whose binary manifest is generated here (no aapt, no android.jar), then signs through the real
scripts/sign-apk.py with --diagnostic-out and verifies each output independently with apksigner verify:
  pkcs12_shared_one_line   the production shape: one single-line password file for store and key (PR185 fix)
  jks_separate_files       a key password that differs from the store password, in its own file
  jks_shared_two_lines     the same two passwords as two lines of one shared file
Every tool start is bounded by sign_diagnostic.run_bounded. stdout carries one closed JSON result; exit 0 only when all
cases signed and verified and cleanup succeeded. No password, key, path, tool output or environment value is reported.
A pass is synthetic evidence for the given tools, never a production signature or proof of a production cause.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
SIGN_APK = HERE.parents[2] / 'scripts/sign-apk.py'
_spec = importlib.util.spec_from_file_location('sign_diagnostic', HERE / 'sign_diagnostic.py')
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

SCHEMA = 'rose-models-synthetic-sign-check/1'
CASES = ('pkcs12_shared_one_line', 'jks_separate_files', 'jks_shared_two_lines')
CATEGORIES = ('none', 'invalid_roots', 'tool_missing', 'temp_inside_repository', 'fixture_failed', 'case_failed',
              'cleanup_failed', 'interrupted', 'internal_error')
TOOL_SECONDS = 300  # each keytool/zipalign/apksigner verify start
SIGN_SECONDS = 900  # one sign-apk.py start (its own tools are bounded at 300 s each)
BUILD_TOOLS = 'build-tools/android-15'
REQUIRED = {'java': 'bin/java', 'keytool': 'bin/keytool'}
REQUIRED_SDK = {'apksigner': 'apksigner', 'apksignerJar': 'lib/apksigner.jar', 'zipalign': 'zipalign'}
SIGNERS = re.compile(r'^Signer #\d+ certificate SHA-256 digest: ([0-9a-f]{64})$', re.M)

# Shared verification contract (MODELS-SCHEME-FIX-001), also used by models_signing.inspect_signed().
# apksigner verify checks the range from the manifest minSdk by default. From API 28 up it accepts v3 alone and prints
# v2 as false even when a v2 block is present (pinned r35, synthetic minSdk 29). Starting the verified range at API 24,
# where v2 was introduced, makes it check v2 and v3 cryptographically. This is verifier coverage only: the APK's
# declared minSdk, the signing arguments and the required schemes are unchanged, and it claims no API 24 support.
VERIFY_MIN_SDK = '24'
REQUIRED_SCHEMES = ('v2', 'v3')
SCHEME_LABELS = {'v1': 'JAR signing', 'v2': 'APK Signature Scheme v2', 'v3': 'APK Signature Scheme v3',
                 'v3.1': 'APK Signature Scheme v3.1', 'v4': 'APK Signature Scheme v4'}


def verify_argv(apksigner, apk):
    return [str(apksigner), 'verify', '--verbose', '--print-certs', '--min-sdk-version', VERIFY_MIN_SDK, str(apk)]


def schemes(stdout):
    """{scheme: bool}. True only when exactly one output line names the scheme and that line is exactly
    'Verified using <scheme> scheme (<label>): true'. A false, missing, duplicate, contradictory, wrong-label or
    prefixed/suffixed line gives False."""
    lines = stdout.splitlines()
    return {s: [line for line in lines if f'using {s} scheme' in line] == [f'Verified using {s} scheme ({label}): true']
            for s, label in SCHEME_LABELS.items()}


def accepted(returncode, stdout, cert):
    """Verify exit 0, exactly one signer equal to `cert`, and every required scheme strictly true."""
    found = schemes(stdout)
    return returncode == 0 and SIGNERS.findall(stdout) == [cert] and all(found[s] for s in REQUIRED_SCHEMES)


class Failed(Exception):
    def __init__(self, category):
        super().__init__(category)
        self.category = category


def binary_manifest(package='test.synthetic.precheck', min_sdk=29, target_sdk=35, version_code=1,
                    version_name='0.1.0-dev-unsigned'):
    """A minimal compiled AndroidManifest.xml in the Models production shape (minSdk 29, targetSdk 35, versionCode 1,
    versionName 0.1.0-dev-unsigned) with a synthetic package:
    <manifest versionCode versionName package><uses-sdk minSdkVersion targetSdkVersion/></manifest>."""
    attributes = ('versionCode', 'versionName', 'minSdkVersion', 'targetSdkVersion')  # resource-map order
    strings = list(attributes) + ['android', 'http://schemas.android.com/apk/res/android', 'manifest', 'uses-sdk',
                                  'package', package, version_name]
    index = {s: i for i, s in enumerate(strings)}
    none = 0xFFFFFFFF
    pool = b''.join(struct.pack('<H', len(s)) + s.encode('utf-16-le') + b'\0\0' for s in strings)
    pool += b'\0' * (-len(pool) % 4)
    offsets, at = [], 0
    for s in strings:
        offsets.append(at)
        at += 2 + 2 * len(s) + 2
    head = 28 + 4 * len(strings)
    string_pool = struct.pack('<HHIIIIII', 0x0001, 28, head + len(pool), len(strings), 0, 0, head, 0)
    string_pool += struct.pack(f'<{len(offsets)}I', *offsets) + pool
    ids = (0x0101021B, 0x0101021C, 0x0101020C, 0x01010270)  # android:versionCode/versionName/minSdk/targetSdk
    resource_map = struct.pack('<HHI', 0x0180, 8, 8 + 4 * len(ids)) + struct.pack(f'<{len(ids)}I', *ids)
    ns = index['http://schemas.android.com/apk/res/android']

    def element(name, attributes):
        body = struct.pack('<IIHHHHHH', none, name, 20, 20, len(attributes), 0, 0, 0)
        for a_ns, attr, raw, kind, data in attributes:
            body += struct.pack('<IIIHBBI', a_ns, attr, raw, 8, 0, kind, data)
        return struct.pack('<HHIII', 0x0102, 16, 16 + len(body), 1, none) + body

    def end(name):
        return struct.pack('<HHIIIII', 0x0103, 16, 24, 1, none, none, name)

    manifest = [(ns, index['versionCode'], none, 0x10, version_code),
                (ns, index['versionName'], index[version_name], 0x03, index[version_name]),
                (none, index['package'], index[package], 0x03, index[package])]
    uses_sdk = [(ns, index['minSdkVersion'], none, 0x10, min_sdk), (ns, index['targetSdkVersion'], none, 0x10, target_sdk)]
    namespace = struct.pack('<HHIIIII', 0x0100, 16, 24, 1, none, index['android'], ns)
    body = (string_pool + resource_map + namespace + element(index['manifest'], manifest)
            + element(index['uses-sdk'], uses_sdk) + end(index['uses-sdk']) + end(index['manifest'])
            + struct.pack('<HHIIIII', 0x0101, 16, 24, 1, none, index['android'], ns))
    return struct.pack('<HHI', 0x0003, 8, 8 + len(body)) + body


def roots(jdk_home, sdk_root):
    """Exact tool paths under the two explicit roots, or Failed. No search, no fallback."""
    jdk, sdk = Path(jdk_home or ''), Path(sdk_root or '')
    if not jdk_home or not sdk_root or not jdk.is_absolute() or not sdk.is_absolute() or not jdk.is_dir() \
            or not sdk.is_dir():
        raise Failed('invalid_roots')
    tools = {k: jdk / v for k, v in REQUIRED.items()}
    tools.update({k: sdk / BUILD_TOOLS / v for k, v in REQUIRED_SDK.items()})
    for name, path in tools.items():
        if not path.is_file() or (name != 'apksignerJar' and not os.access(path, os.X_OK)):
            raise Failed('tool_missing')
    return tools


def environment(jdk_home, sdk_root, home):
    return {'PATH': f'{jdk_home}/bin:/usr/bin:/bin', 'JAVA_HOME': str(jdk_home), 'ANDROID_SDK_ROOT': str(sdk_root),
            'LC_ALL': 'C', 'HOME': str(home)}


def run(argv, env, seconds, category):
    """One bounded start (PR181 collector) with exactly `env`. Raw output stays here; only the fixed category leaves."""
    result = D.run_bounded([str(a) for a in argv], timeout=seconds,
                           popen=lambda *a, **k: subprocess.Popen(*a, env=env, **k))
    if result.outcome != 'ok':
        raise Failed(category)
    return result


def private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(text)
    return path


def fixture(work, tools, env):
    """Disposable synthetic keys, password files and an aligned unsigned APK inside `work`."""
    store = os.urandom(24).hex()
    key = os.urandom(24).hex()
    f = {'one': private(work / 'one-line', store + '\n'), 'key': private(work / 'key', key + '\n'),
         'two': private(work / 'two-lines', store + '\n' + key + '\n'), 'p12': work / 'store.p12', 'jks': work / 'store.jks'}
    for kind, path, size, key_file in (('PKCS12', f['p12'], '4096', f['one']), ('JKS', f['jks'], '2048', f['key'])):
        run([tools['keytool'], '-genkeypair', '-keystore', path, '-storetype', kind, '-alias', 'rose-models',
             '-keyalg', 'RSA', '-keysize', size, '-sigalg', 'SHA256withRSA', '-validity', '1',
             '-dname', 'CN=Synthetic Precheck Fixture Not Rose Models', '-storepass:file', f['one'],
             '-keypass:file', key_file, '-noprompt'], env, TOOL_SECONDS, 'fixture_failed')
        run([tools['keytool'], '-exportcert', '-alias', 'rose-models', '-keystore', path, '-storetype', kind,
             '-storepass:file', f['one'], '-file', work / f'{kind}.der'], env, TOOL_SECONDS, 'fixture_failed')
        os.chmod(path, 0o600)
        f[f'cert-{kind}'] = hashlib.sha256((work / f'{kind}.der').read_bytes()).hexdigest()
    with zipfile.ZipFile(work / 'unaligned.apk', 'w', zipfile.ZIP_DEFLATED) as apk:
        apk.writestr('AndroidManifest.xml', binary_manifest())
    run([tools['zipalign'], '-p', '-f', '4', work / 'unaligned.apk', work / 'input.apk'], env, TOOL_SECONDS,
        'fixture_failed')
    return f


def sign_case(name, work, tools, env, f):
    """One sign-apk.py start with the diagnostic record, then one independent apksigner verify. (signed, verified)."""
    keystore, store, key, cert = {
        'pkcs12_shared_one_line': (f['p12'], f['one'], f['one'], f['cert-PKCS12']),
        'jks_separate_files': (f['jks'], f['one'], f['key'], f['cert-JKS']),
        'jks_shared_two_lines': (f['jks'], f['two'], f['two'], f['cert-JKS']),
    }[name]
    config = private(work / f'{name}.json', json.dumps({'keyAlias': 'rose-models', 'storePasswordFile': str(store),
                                                       'keyPasswordFile': str(key)}))
    diag_dir = work / f'{name}-diag'
    diag_dir.mkdir(mode=0o700)
    out = work / f'{name}-out' / 'signed.apk'
    signed = verified = False
    try:
        run([sys.executable, '-I', SIGN_APK, work / 'input.apk', '--keystore', keystore, '--config', config,
             '--output', out, '--expected-cert-sha256', cert, '--diagnostic-out', diag_dir / 'record.json'],
            env, SIGN_SECONDS, 'case_failed')
        record, problem = D.load(diag_dir / 'record.json', D.sha256_file(tools['apksignerJar']))
        signed = problem is None and record['stage'] == 'complete' and out.is_file()
        if signed:
            result = run(verify_argv(tools['apksigner'], out), env, TOOL_SECONDS, 'case_failed')
            verified = accepted(result.returncode, result.stdout, cert)
    except Failed:
        pass
    return signed, verified


def check(jdk_home, sdk_root, parent=None, cases=CASES):
    """The closed result for the explicit roots. Never raises for a check failure; KeyboardInterrupt/SystemExit still
    remove the owned directory first."""
    result = {'schema': SCHEMA, 'result': 'fail', 'category': 'internal_error', 'executed': 0, 'passed': 0,
              'failed': 0, 'skipped': 0, 'cases': [], 'correctedInvocation': {'signed': False, 'verified': False},
              'observedToolSha256': {}, 'evidence': 'synthetic fixtures with caller-proven roots; not a production '
                                                    'signature, key or JDK-equivalence result'}
    work = None
    try:
        tools = roots(jdk_home, sdk_root)
        result['observedToolSha256'] = {k: D.sha256_file(v) for k, v in sorted(tools.items())}
        work = Path(tempfile.mkdtemp(prefix='models-precheck-', dir=parent))
        if work.resolve().is_relative_to(SIGN_APK.resolve().parents[1]):
            raise Failed('temp_inside_repository')  # sign-apk.py refuses private inputs inside the repository
        env = environment(Path(jdk_home), Path(sdk_root), work)
        f = fixture(work, tools, env)
        for name in cases:
            signed, verified = sign_case(name, work, tools, env, f)
            ok = signed and verified
            result['cases'].append({'name': name, 'signed': signed, 'verified': verified})
            result['executed'] += 1
            result['passed' if ok else 'failed'] += 1
            if name == 'pkcs12_shared_one_line':
                result['correctedInvocation'] = {'signed': signed, 'verified': verified}
        passed = result['executed'] == len(CASES) and result['passed'] == len(CASES)
        result['result'], result['category'] = ('pass', 'none') if passed else ('fail', 'case_failed')
    except Failed as error:
        result['category'] = error.category
    except (KeyboardInterrupt, SystemExit):
        result['category'] = 'interrupted'
        raise
    except Exception:
        result['category'] = 'internal_error'
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
            if os.path.lexists(work):
                result['result'], result['category'] = 'fail', 'cleanup_failed'
    if result['result'] != 'pass':
        result['result'] = 'fail'
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description='Required synthetic sign/verify check (no skips, no tool search).')
    p.add_argument('--jdk-home', required=True)
    p.add_argument('--sdk-root', required=True)
    a = p.parse_args(argv)

    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        result = check(a.jdk_home, a.sdk_root)
    except KeyboardInterrupt:
        print(json.dumps({'schema': SCHEMA, 'result': 'fail', 'category': 'interrupted'}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result['result'] == 'pass' else 1


if __name__ == '__main__':
    sys.exit(main())
