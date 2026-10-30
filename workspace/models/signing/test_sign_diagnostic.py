"""MODELS-SIGN-DIAG-001 source tests: the closed signing diagnostic, the opt-in scripts/sign-apk.py hook and the Models
adapter's consumption. A fake SDK (shell scripts standing in for zipalign/apksigner) prints unmistakable synthetic
password/path/URL/env sentinels; no real key, keystore, password, Java tool, network or signing is involved.
These prove the diagnostic and leak boundaries, not the cause of any real signing failure."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import models_signing as ms  # noqa: E402
import test_models_signing as tms  # noqa: E402

D = ms.DIAG
SIGN_APK = ms.SIGN_APK
SENTINELS = ('SENTINEL_PASSWORD_7c1e', 'SENTINEL-PATH', 'SENTINEL-URL', 'SENTINEL_TOKEN', 'SENTINEL_ENV_VALUE')
PIN = tms.FAKE_SIGNER

APKSIGNER = r'''#!/bin/sh
# Synthetic stand-in for apksigner in source tests. Never signs anything.
d=$(dirname "$0")/..
cmd=$1
echo "$cmd" >> "$d/calls.log"
mode=$(cat "$d/mode")
pw=""; out=""; prev=""
for arg in "$@"; do
  case "$prev" in --ks-pass) pw=${arg#file:};; --out) out=$arg;; esac
  prev=$arg
done
last=$(eval echo \${$#})
leak() { echo "password=$(cat "$pw" 2>/dev/null) file=$pw url=https://SENTINEL-URL.invalid/k?token=SENTINEL_TOKEN env=$SENTINEL_ENV"; }
if [ "$cmd" = sign ]; then
  case "$mode" in
    ok) leak >&2; cp "$last" "$out"; exit 0;;
    badpass) leak >&2; leak; echo "Keystore was tampered with, or password was incorrect" >&2; exit 1;;
    unknown) leak >&2; leak; exit 3;;
    signal) leak >&2; kill -9 $$;;
    big) leak >&2; head -c 400000 /dev/zero | tr '\0' 'A' >&2; exit 1;;
    flood) echo $$ > "$d/apksigner.pid"; while :; do leak; leak >&2; done;;
    eof) echo 'Failed to load signer "signer #1"' >&2
         echo "java.io.IOException: Failed to read Key \"rose-models\" password for signer #1 : end of file reached in $pw" >&2
         leak >&2; exit 2;;
    noalias) leak >&2; echo "Failed to load signer \"signer #1\": $pw.p12 entry \"SENTINEL_TOKEN\" does not contain a key" >&2; exit 2;;
    pwunreadable) leak >&2; printf 'Failed to load signer "signer #1"\njava.io.FileNotFoundException: %s (Permission denied)\n\tat java.base/java.io.FileInputStream.open0(Native Method)\n\tat com.android.apksigner.PasswordRetriever.getPasswords(PasswordRetriever.java:170)\n' "$pw" >&2; exit 2;;
  esac
fi
if [ "$cmd" = verify ]; then
  leak >&2
  echo "Verified using v2 scheme (APK Signature Scheme v2): true"
  echo "Signer #1 certificate SHA-256 digest: $(cat "$d/pin")"
  exit 0
fi
exit 2
'''
ZIPALIGN = r'''#!/bin/sh
d=$(dirname "$0")/..
[ "$(cat "$d/mode")" = badalign ] && { echo "alignment says SENTINEL_TOKEN" >&2; exit 1; }
exit 0
'''


def fake_sdk(root, mode='ok', pin=PIN, with_apksigner=True):
    """build-tools/android-15 with zipalign/apksigner scripts, plus a mode/pin file one level up (state, not tools)."""
    bt = Path(root) / 'build-tools/android-15'
    (bt / 'lib').mkdir(parents=True, exist_ok=True)
    (bt / 'lib/apksigner.jar').write_bytes(b'synthetic apksigner jar')
    for name, body in (('zipalign', ZIPALIGN), ('apksigner', APKSIGNER)):
        if name == 'apksigner' and not with_apksigner:
            continue
        (bt / name).write_text(body)
        os.chmod(bt / name, 0o755)
    (bt.parent / 'mode').write_text(mode)
    (bt.parent / 'pin').write_text(pin)
    return Path(root)


def sign_calls(sdk):
    log = Path(sdk) / 'build-tools/calls.log'
    return log.read_text().split().count('sign') if log.exists() else 0


class SentinelCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='sign-diag-SENTINEL-PATH-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.private = self.tmp / 'private'
        self.private.mkdir(mode=0o700)
        self.password = self.private / 'password'
        self.password.write_text('SENTINEL_PASSWORD_7c1e\n')
        self.keystore = self.private / 'models.p12'
        self.keystore.write_bytes(b'NOT A KEYSTORE')
        for f in (self.password, self.keystore):
            os.chmod(f, 0o600)
        env = mock.patch.dict(os.environ, {'SENTINEL_ENV': 'SENTINEL_ENV_VALUE'})
        env.start()
        self.addCleanup(env.stop)

    def assertNoSentinel(self, *texts):
        for text in texts:
            for sentinel in SENTINELS:
                self.assertNotIn(sentinel, text, f'{sentinel} leaked')

    def assertTreeClean(self, root):
        for path in Path(root).rglob('*'):
            if path.is_file() and not path.is_symlink():
                self.assertNoSentinel(path.read_bytes().decode('utf-8', 'replace'))


class SignApkHook(SentinelCase):
    """The real scripts/sign-apk.py with the fake SDK."""

    def run_sign_apk(self, mode='ok', diagnostic=True, keystore_mode=0o600, with_apksigner=True, diag_path=None):
        sdk = fake_sdk(self.tmp / f'sdk-{mode}', mode, with_apksigner=with_apksigner)
        os.chmod(self.keystore, keystore_mode)
        config = self.private / f'config-{mode}.json'
        if not config.exists():
            config.write_text(json.dumps({'keyAlias': 'rose-models', 'storePasswordFile': str(self.password),
                                          'keyPasswordFile': str(self.password)}))
            os.chmod(config, 0o600)
        apk = self.tmp / 'in.apk'
        apk.write_bytes(b'PK synthetic unsigned apk')
        out = self.tmp / f'out-{mode}' / 'signed.apk'
        argv = [sys.executable, str(SIGN_APK), str(apk), '--keystore', str(self.keystore), '--config', str(config),
                '--output', str(out), '--expected-cert-sha256', PIN]
        diag = None
        if diagnostic:
            ddir = self.tmp / f'diag-{mode}'
            ddir.mkdir(mode=0o700, exist_ok=True)
            diag = diag_path or ddir / 'record.json'
            argv += ['--diagnostic-out', str(diag)]
        result = subprocess.run(argv, env=dict(os.environ, ANDROID_SDK_ROOT=str(sdk)), capture_output=True, text=True)
        record = json.loads(Path(diag).read_text()) if diag and Path(diag).exists() else None
        return result, record, sdk

    def test_default_behavior_is_unchanged(self):
        result, record, sdk = self.run_sign_apk('badpass', diagnostic=False)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (1, '', 'APK signing stopped: signing_failed\n'))
        self.assertEqual(sign_calls(sdk), 1)
        ok, _, sdk = self.run_sign_apk('ok', diagnostic=False)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        receipt = json.loads(ok.stdout)
        self.assertEqual((receipt['certificateSha256'], receipt['signatureVerified']), ([PIN], True))
        self.assertEqual(sign_calls(sdk), 1)
        self.assertNoSentinel(result.stdout, result.stderr, ok.stdout, ok.stderr)

    def test_opt_in_records_inner_exit_and_fixed_category_without_leaks(self):
        cases = {
            'badpass': ('sign', 'exit', 1, None, 'keystore_password_incorrect'),
            'unknown': ('sign', 'exit', 3, None, 'unclassified'),
            'signal': ('sign', 'signal', None, 9, 'unclassified'),
            'big': ('sign', 'oversized', None, None, 'output_oversized'),
            'flood': ('sign', 'oversized', None, None, 'output_oversized'),
            'badalign': ('alignment', 'exit', 1, None, 'unclassified'),
            'eof': ('sign', 'exit', 2, None, 'password_file_exhausted'),
            'noalias': ('sign', 'exit', 2, None, 'alias_not_found'),
            'pwunreadable': ('sign', 'exit', 2, None, 'password_file_unreadable'),
        }
        for mode, (stage, outcome, child_exit, child_signal, category) in cases.items():
            with self.subTest(mode):
                result, record, sdk = self.run_sign_apk(mode)
                self.assertEqual(result.returncode, 1)  # the wrapper's own exit, distinct from the child's
                self.assertEqual(result.stdout, '')
                self.assertRegex(result.stderr, r'^APK signing stopped: (signing_failed|alignment_failed)\n$')
                self.assertEqual(list(record), list(D.FIELDS))
                self.assertEqual((record['stage'], record['outcome'], record['childExit'], record['childSignal'],
                                  record['category']), (stage, outcome, child_exit, child_signal, category))
                self.assertEqual(record['apksignerJarSha256'], hashlib.sha256(b'synthetic apksigner jar').hexdigest())
                self.assertIsNone(D.validate(record))
                self.assertEqual(sign_calls(sdk), 0 if mode == 'badalign' else 1)
                self.assertNoSentinel(result.stdout, result.stderr, json.dumps(record))
                if mode == 'flood':  # the endless writer was stopped and reaped, not left running
                    pid = int((sdk / 'build-tools/apksigner.pid').read_text())
                    self.assertRaises(ProcessLookupError, os.kill, pid, 0)

    def test_success_complete_record_and_pin_mismatch(self):
        result, record, sdk = self.run_sign_apk('ok')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((record['stage'], record['outcome'], record['category']), ('complete', 'ok', 'none'))
        self.assertEqual(sign_calls(sdk), 1)
        self.assertNoSentinel(result.stdout, result.stderr)
        sdk = fake_sdk(self.tmp / 'sdk-pin', 'ok', pin=tms.OTHER_SIGNER)
        apk = self.tmp / 'in.apk'
        diag = self.tmp / 'diag-pin'
        diag.mkdir(mode=0o700)
        config = self.private / 'config-ok.json'
        r = subprocess.run([sys.executable, str(SIGN_APK), str(apk), '--keystore', str(self.keystore), '--config',
                            str(config), '--output', str(self.tmp / 'out-pin/s.apk'), '--expected-cert-sha256', PIN,
                            '--diagnostic-out', str(diag / 'r.json')],
                           env=dict(os.environ, ANDROID_SDK_ROOT=str(sdk)), capture_output=True, text=True)
        record = json.loads((diag / 'r.json').read_text())
        self.assertEqual(r.stderr, 'APK signing stopped: certificate_pin_mismatch\n')
        self.assertEqual((record['stage'], record['outcome'], record['category']),
                         ('verify', 'rejected', 'certificate_pin_mismatch'))

    def test_missing_tool_and_rejected_invocation(self):
        result, record, sdk = self.run_sign_apk('ok', with_apksigner=False)
        self.assertEqual((record['stage'], record['outcome'], record['category']), ('sign', 'not_started', 'tool_missing'))
        result, record, sdk = self.run_sign_apk('badpass', keystore_mode=0o644)
        self.assertEqual(result.stderr, 'APK signing stopped: invalid_or_missing_private_input\n')
        self.assertEqual((record['stage'], record['outcome'], record['category']),
                         ('invocation', 'rejected', 'invocation_rejected'))
        self.assertEqual(sign_calls(sdk), 0)
        self.assertNoSentinel(result.stdout, result.stderr, json.dumps(record))

    def test_diagnostic_destination_is_validated_before_any_tool(self):
        existing = self.tmp / 'exists.json'
        existing.write_text('{}')
        loose = self.tmp / 'loose'
        loose.mkdir(mode=0o755)
        os.chmod(loose, 0o755)
        for name, path in (('existing', existing), ('relative', Path('record.json')), ('group-readable', loose / 'r.json')):
            with self.subTest(name):
                result, _, sdk = self.run_sign_apk('ok', diag_path=path)
                self.assertEqual((result.returncode, result.stderr), (1, 'APK signing stopped: diagnostic_output_invalid\n'))
                self.assertEqual(sign_calls(sdk), 0)
        self.assertEqual(existing.read_text(), '{}')


class Schema(unittest.TestCase):
    def good(self, **kw):
        record = D.record('sign', D.Result('exit', 1, None, '', ''), apksigner_jar_sha256='a' * 64)
        record.update(kw)
        return record

    def test_validate_closed_schema(self):
        self.assertIsNone(D.validate(self.good()))
        bad = {
            'extra field': dict(self.good(), note='SENTINEL_TOKEN'),
            'missing field': {k: v for k, v in self.good().items() if k != 'category'},
            'other schema': self.good(schema='other/1'),
            'unknown category': self.good(category='SENTINEL_TOKEN'),
            'unknown stage': self.good(stage='upload'),
            'exit as string': self.good(childExit='1'),
            'exit out of range': self.good(childExit=999),
            'bool exit': self.good(childExit=True),
            'signal with exit': self.good(childSignal=9),
            'exit without code': self.good(childExit=None),
            'ok with category': self.good(outcome='ok', childExit=0, category='unclassified'),
            'complete but failed': self.good(stage='complete'),
            'bad tool hash': self.good(apksignerJarSha256='SENTINEL-PATH'),
        }
        for name, record in bad.items():
            with self.subTest(name):
                self.assertIsNotNone(D.validate(record))

    def test_load_and_summary_never_echo_foreign_values(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        cases = {
            'extra': (json.dumps(dict(self.good(), leak='SENTINEL_PASSWORD_7c1e')), 'fields'),
            'malformed': ('{"schema": SENTINEL_TOKEN', 'malformed'),
            'oversized': (json.dumps(self.good()) + ' ' * D.RECORD_BYTES, 'oversized'),
            'wrong tool': (json.dumps(self.good(apksignerJarSha256='b' * 64)), 'tool_identity_mismatch'),
        }
        for name, (text, problem) in cases.items():
            with self.subTest(name):
                path = tmp / f'{name}.json'
                path.write_text(text)
                record, got = D.load(path, 'a' * 64)
                self.assertEqual((record, got), (None, problem))
                line = D.summary(record, got, 1)
                self.assertEqual(line, f'diagnostic=invalid:{problem} wrapper_exit=1')
        link = tmp / 'link.json'
        link.symlink_to(tmp / 'extra.json')
        self.assertEqual(D.load(link, 'a' * 64), (None, 'missing'))
        self.assertEqual(D.load(tmp / 'absent.json', 'a' * 64), (None, 'missing'))
        record, problem = D.load(self.write(tmp, self.good()), 'a' * 64)
        self.assertEqual(D.summary(record, problem, 1),
                         'stage=sign outcome=exit child_exit=1 category=unclassified wrapper_exit=1')
        self.assertEqual(D.summary(record, problem, 'SENTINEL_TOKEN'),
                         'stage=sign outcome=exit child_exit=1 category=unclassified wrapper_exit=unknown')

    def write(self, tmp, record):
        path = tmp / 'good.json'
        path.write_text(json.dumps(record))
        return path

    def test_pinned_tool_messages_classify(self):
        """Wording observed from build-tools r35 apksigner on synthetic fixtures (MODELS-SIGN-INVOKE-001); the variable
        parts carry sentinels, which must never reach a record or summary."""
        p = '/SENTINEL-PATH/SENTINEL_PASSWORD_7c1e'
        frames = ('\tat com.android.apksigner.SignerParams.loadPrivateKeyAndCertsFromKeyStore(SignerParams.java:354)\n'
                  '\tat com.android.apksigner.ApkSignerTool.main(ApkSignerTool.java:94)\n')
        messages = {
            'password_file_exhausted': 'Failed to load signer "signer #1"\njava.io.IOException: Failed to read Key '
                                       f'"SENTINEL_TOKEN" password for signer #1 : end of file reached in {p}\n'
                                       '\tat com.android.apksigner.PasswordRetriever.getPasswords(PasswordRetriever.java:175)\n'
                                       + frames,
            'alias_not_found': f'Failed to load signer "signer #1": {p}.p12 entry "SENTINEL_TOKEN" does not contain a key\n',
            'password_file_unreadable': f'Failed to load signer "signer #1"\njava.io.FileNotFoundException: {p} '
                                        '(Permission denied)\n\tat java.base/java.io.FileInputStream.open0(Native Method)\n'
                                        '\tat java.base/java.io.FileInputStream.open(FileInputStream.java:213)\n'
                                        '\tat java.base/java.io.FileInputStream.<init>(FileInputStream.java:152)\n'
                                        '\tat com.android.apksigner.PasswordRetriever.getPasswords(PasswordRetriever.java:170)\n'
                                        + frames,
            'keystore_password_incorrect': 'Failed to load signer "signer #1"\njava.io.IOException: keystore password was '
                                           'incorrect\n\tat java.base/sun.security.pkcs12.PKCS12KeyStore.engineLoad('
                                           'PKCS12KeyStore.java:2112)\n' + frames,
            # an output-side permission failure, not from PasswordRetriever, keeps its own category
            'output_write_failed': f'java.nio.file.AccessDeniedException: {p}/signed.apk\n'
                                   '\tat java.base/sun.nio.fs.UnixException.translateToIOException(UnixException.java:90)\n',
        }
        for category, text in messages.items():
            with self.subTest(category):
                result = D.Result('exit', 2, None, '', text)
                self.assertEqual(D.classify(result), category)
                record = D.record('sign', result, apksigner_jar_sha256='a' * 64)
                self.assertIsNone(D.validate(record))
                for sentinel in SENTINELS:
                    self.assertNotIn(sentinel, json.dumps(record) + D.summary(record, None, 1))
        self.assertIn('password_file_exhausted', D.CATEGORIES)
        self.assertEqual(D.classify(D.Result('exit', 2, None, '', 'Failed to load signer "signer #1"\n')), 'unclassified')

    def test_classifier_is_fixed_and_bounded(self):
        r = D.Result('exit', 1, None, '', 'java.security.UnrecoverableKeyException: Cannot recover key')
        self.assertEqual(D.classify(r), 'key_password_incorrect')
        self.assertEqual(D.classify(D.Result('exit', 1, None, 'nothing known', '')), 'unclassified')
        self.assertEqual(D.classify(D.Result('ok', 0)), 'none')
        timed = D.run_bounded(['sleep', '5'], timeout=0.5)
        self.assertEqual((timed.outcome, D.classify(timed)), ('timeout', 'timeout'))
        missing = D.run_bounded(['/nonexistent/SENTINEL-PATH/tool'])
        self.assertEqual((missing.outcome, D.classify(missing)), ('not_started', 'tool_missing'))
        self.assertEqual(D.record('sign', missing)['category'], 'tool_missing')
        starts = []
        real = subprocess.Popen
        D.run_bounded(['true'], popen=lambda *a, **k: starts.append(a) or real(*a, **k))
        self.assertEqual(len(starts), 1)

    def test_invocation_shape(self):
        argv = [sys.executable, str(SIGN_APK), '/x/in.apk', '--keystore', '/k', '--config', '/c', '--output', '/o',
                '--expected-cert-sha256', 'a' * 64, '--diagnostic-out', '/d']
        self.assertIsNone(D.validate_invocation(argv, SIGN_APK))
        for name, bad in {
            'inline password': argv + ['--ks-pass', 'pass:SENTINEL_PASSWORD_7c1e'],
            'missing diagnostic': argv[:-2],
            'duplicate option': argv[:-2] + ['--output', '/o2'],
            'other script': [argv[0], '/tmp/other.py'] + argv[2:],
            'option as value': argv[:-1] + ['--evil'],
        }.items():
            with self.subTest(name):
                self.assertIsNotNone(D.validate_invocation(bad, SIGN_APK))


class AdapterConsumption(SentinelCase, tms.Fixture):
    """models_signing.sign() reading the closed record: real sign-apk.py end to end, and mocked boundaries."""

    def setUp(self):
        tms.Fixture.setUp(self)  # artifact, SDK, profile and output under its own temporary directory
        fixture_tmp = self.tmp
        self.addCleanup(shutil.rmtree, fixture_tmp, True)
        SentinelCase.setUp(self)  # then the sentinel-bearing keystore/password files replace the plain ones

    def real_sdk(self, mode):
        sdk = fake_sdk(self.sdk, mode)
        self.profile = tms.profile_for(self.artifact, sdk)  # binds the fake jar/aapt hashes as the tool identity
        return sdk

    def test_end_to_end_failure_is_classified_and_nothing_leaks(self):
        sdk = self.real_sdk('badpass')
        with self.assertRaises(ms.Refused) as ctx:
            ms.sign(self.profile, self.artifact, self.out, self.keystore, self.password, sdk,
                    inspector=tms.mock_inspector(), evidence_tier='mock-source-test')
        message = str(ctx.exception)
        self.assertEqual(message, 'signer_failed: stage=sign outcome=exit child_exit=1 '
                                  'category=keystore_password_incorrect wrapper_exit=1')
        failure = json.loads((self.out / 'models-signing-failure.json').read_text())
        self.assertEqual((failure['signerWrapperExit'], failure['signerDiagnostic']['childExit'],
                          failure['signerDiagnostic']['category']), (1, 1, 'keystore_password_incorrect'))
        self.assertFalse((self.out / 'models-signing-receipt.json').exists())
        self.assertFalse((self.out / 'work').exists())
        self.assertEqual(sign_calls(sdk), 1)
        self.assertNoSentinel(message)
        self.assertTreeClean(self.out)
        self.assertEqual(self.password.read_text(), 'SENTINEL_PASSWORD_7c1e\n')  # operator file untouched

    def test_end_to_end_success_keeps_independent_checks(self):
        sdk = self.real_sdk('ok')
        receipt = ms.sign(self.profile, self.artifact, self.out, self.keystore, self.password, sdk,
                          inspector=tms.mock_inspector(), evidence_tier='mock-source-test')
        self.assertEqual((receipt['signerDiagnostic']['stage'], receipt['signerWrapperExit']), ('complete', 0))
        self.assertEqual(sign_calls(sdk), 1)
        self.assertTreeClean(self.out)
        # A clean record never rescues a failed independent check.
        shutil.rmtree(self.out)
        fake_sdk(self.sdk, 'ok')
        with self.assertRaisesRegex(ms.Refused, 'signer_mismatch'):
            ms.sign(self.profile, self.artifact, self.out, self.keystore, self.password, sdk,
                    inspector=tms.mock_inspector(signers=[tms.OTHER_SIGNER]), evidence_tier='mock-source-test')
        self.assertFalse((self.out / ms.SIGNED_NAME).exists())

    def mocked(self, executor):
        with self.assertRaises(ms.Refused) as ctx:
            self.sign(executor=executor)
        self.assertFalse((self.out / 'models-signing-receipt.json').exists())
        self.assertFalse((self.out / ms.SIGNED_NAME).exists())
        self.assertNoSentinel(str(ctx.exception))
        self.assertTreeClean(self.out)
        return str(ctx.exception)

    def test_zero_wrapper_exit_without_valid_complete_record_fails_closed(self):
        cases = {
            'absent': 'absent',
            'foreign field': json.dumps(dict(D.record('complete', category='none'), extra='SENTINEL_TOKEN')),
            'sign stage': D.record('sign', D.Result('exit', 1, None, '', '')),
            'wrong tool': D.record('complete', category='none', apksigner_jar_sha256='c' * 64),
        }
        for name, record in cases.items():
            with self.subTest(name):
                signer = tms.MockSigner(diagnostic=record)
                message = self.mocked(signer)
                self.assertTrue(message.startswith('signer_diagnostic_invalid: '), message)
                self.assertEqual(len(signer.calls), 1)
                shutil.rmtree(self.out)

    def test_wrapper_text_and_timeouts_are_never_echoed(self):
        def noisy(argv, env):
            Path(argv[argv.index('--output') + 1]).write_bytes(b'partial')
            return mock.Mock(returncode=7, stdout='SENTINEL_PASSWORD_7c1e', stderr='Traceback SENTINEL-PATH/x')
        message = self.mocked(noisy)
        self.assertEqual(message, 'signer_failed: diagnostic=invalid:missing wrapper_exit=7')
        shutil.rmtree(self.out)

        def slow(argv, env):
            raise subprocess.TimeoutExpired(argv, 1, output='SENTINEL_TOKEN')
        self.assertEqual(self.mocked(slow), 'signer_timeout: sign-apk.py exceeded its time bound')

    def test_default_executor_discards_output_with_a_time_bound(self):
        with mock.patch.object(ms.subprocess, 'run', return_value=mock.Mock(returncode=0)) as run:
            ms.run_sign_apk(['x'], {})
        kwargs = run.call_args.kwargs
        self.assertEqual((kwargs['stdout'], kwargs['stderr'], kwargs['stdin']),
                         (subprocess.DEVNULL, subprocess.DEVNULL, subprocess.DEVNULL))
        self.assertEqual(kwargs['timeout'], ms.SIGNER_TIMEOUT_SECONDS)
        self.assertNotIn('capture_output', kwargs)

    def test_unbound_profile_still_reads_and_starts_nothing(self):
        p = dict(self.profile, signer={'status': 'unbound', 'certificateSha256': None})
        executor = mock.Mock()
        with mock.patch.object(ms, 'check_private_file') as private:
            with self.assertRaisesRegex(ms.Refused, 'not_ready'):
                ms.sign(p, self.artifact, self.out, self.keystore, self.password, self.sdk, executor=executor)
        private.assert_not_called()
        executor.assert_not_called()

NOISY = r"""
import os, signal, subprocess, sys, time
mode, state = sys.argv[1], sys.argv[2]
with open(os.path.join(state, 'starts'), 'a') as f:
    f.write('%d\n' % os.getpid())
line = b'SENTINEL_PASSWORD_7c1e https://SENTINEL-URL.invalid/?t=SENTINEL_TOKEN\n'
if mode == 'flood':  # both streams, while alive, far past the cap; counts what the pipes accepted
    chunk, total = (line * 1024)[:16384], 0
    while total < 16 * 1024 * 1024:
        for fd in (1, 2):
            total += os.write(fd, chunk)
        with open(os.path.join(state, 'written.tmp'), 'w') as f:
            f.write(str(total))
        os.replace(os.path.join(state, 'written.tmp'), os.path.join(state, 'written'))
    time.sleep(30)
elif mode == 'exact':  # all of stderr first, then stdout: only concurrent draining avoids a pipe deadlock
    n_out, n_err, code = (int(v) for v in sys.argv[3:6])
    for fd, n in ((2, n_err), (1, n_out)):
        data = (line + b'A' * n)[:n]
        while data:
            data = data[os.write(fd, data):]
    sys.exit(code)
elif mode == 'quiet':
    os.write(2, line)
    time.sleep(30)
elif mode == 'deaf':  # ignores SIGTERM, so the stop must escalate to SIGKILL
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.write(1, line)
    time.sleep(30)
elif mode == 'orphan':  # leaves a grandchild holding both pipes after the tool itself exits
    subprocess.Popen(['sleep', '3'])
    os.write(1, line)
    sys.exit(3)
"""


def legacy_run_bounded(command, timeout=D.TIMEOUT_SECONDS, popen=subprocess.Popen):
    """The PR178 wait-then-read implementation, kept only as the negative control for the capture regressions."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            child = popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        except OSError:
            return D.Result('not_started')
        try:
            code = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
            return D.Result('timeout')
        sizes = []
        for f in (out, err):
            f.seek(0, os.SEEK_END)
            sizes.append(f.tell())
    if max(sizes) > D.CAPTURE_BYTES:
        return D.Result('oversized', code if code >= 0 else None, -code if code < 0 else None)
    return D.Result('ok' if code == 0 else 'exit', code, None, '', '')


class CaptureBounds(unittest.TestCase):
    """run_bounded with real local synthetic children. The cap holds while the child is alive; nothing outlives it."""

    def setUp(self):
        self.state = Path(tempfile.mkdtemp(prefix='capture-SENTINEL-PATH-'))
        self.addCleanup(shutil.rmtree, self.state, True)
        self.script = self.state / 'noisy.py'
        self.script.write_text(NOISY)

    def fds(self):
        return sorted(os.listdir('/proc/self/fd'))

    def run_child(self, mode, *args, timeout=10, run=None):
        """(result, elapsed, retained) with exactly one start, the child reaped and no descriptor left open."""
        run = run or D.run_bounded
        starts, reads, real_read = [], [], os.read
        def counting_read(fd, n):
            data = real_read(fd, n)
            reads.append((n, len(data)))
            return data
        before = self.fds()
        began = time.monotonic()
        with mock.patch('os.read', counting_read):
            result = run([sys.executable, '-I', str(self.script), mode, str(self.state), *map(str, args)],
                         timeout=timeout, popen=lambda *a, **k: starts.append(subprocess.Popen(*a, **k)) or starts[-1])
        elapsed = time.monotonic() - began
        self.assertEqual(len(starts), 1)  # still referenced here, so its pipes are closed by run_bounded, not by GC
        pids = (self.state / 'starts').read_text().split()
        self.assertEqual(len(pids), 1)  # the tool itself started exactly once
        self.assertRaises(ProcessLookupError, os.kill, int(pids[0]), 0)  # gone and reaped, not a zombie
        self.assertEqual(self.fds(), before)
        self.assertTrue(all(n <= D.CHUNK_BYTES for n, _ in reads))
        self.assertNoLeak(result)
        return result, elapsed, sum(got for _, got in reads)

    def assertNoLeak(self, result):
        """Record and summary carry fixed tokens only; a stopped child leaves no captured text in the Result."""
        record = D.record('complete' if result.outcome == 'ok' else 'sign', result, apksigner_jar_sha256='a' * 64)
        if result.outcome in ('oversized', 'timeout'):
            self.assertEqual((result.stdout, result.stderr), ('', ''))
        for sentinel in ('SENTINEL', 'https://'):
            self.assertNotIn(sentinel, json.dumps(record) + D.summary(record, None, 1))

    def check_flood(self, run=None):
        result, elapsed, retained = self.run_child('flood', timeout=6, run=run)
        self.assertEqual((result.outcome, D.classify(result)), ('oversized', 'output_oversized'))
        self.assertEqual((result.returncode, result.signum), (None, None))
        self.assertLess(elapsed, 3)  # detected during capture, not at exit or the deadline
        self.assertLessEqual(retained, 2 * (D.CAPTURE_BYTES + 1))
        written = int((self.state / 'written').read_text()) if (self.state / 'written').exists() else 0
        # what the writer could push: our cap plus each pipe's kernel buffer (at most the 1 MiB unprivileged max)
        self.assertLessEqual(written, 2 * (D.CAPTURE_BYTES + 1024 * 1024 + 16384))

    def test_noisy_child_is_stopped_during_capture_on_both_streams(self):
        self.check_flood()

    def test_negative_control_wait_then_read_fails_the_regression(self):
        with self.assertRaises(AssertionError):
            self.check_flood(run=legacy_run_bounded)

    def test_boundary_below_at_and_over_the_cap_per_stream(self):
        cap = D.CAPTURE_BYTES
        for n_out, n_err, outcome in ((cap - 1, cap - 1, 'exit'), (cap, cap, 'exit'),
                                      (cap + 1, 0, 'oversized'), (0, cap + 1, 'oversized')):
            with self.subTest(n_out=n_out, n_err=n_err):
                (self.state / 'starts').unlink(missing_ok=True)
                result, _, retained = self.run_child('exact', n_out, n_err, 4)
                self.assertEqual(result.outcome, outcome)
                self.assertLessEqual(retained, 2 * (cap + 1))
                if outcome == 'exit':
                    self.assertEqual((result.returncode, len(result.stdout), len(result.stderr)), (4, n_out, n_err))

    def test_large_stderr_before_stdout_does_not_deadlock(self):
        result, elapsed, _ = self.run_child('exact', 200000, 200000, 0)
        self.assertEqual((result.outcome, len(result.stdout), len(result.stderr)), ('ok', 200000, 200000))
        self.assertLess(elapsed, 5)

    def test_timeout_stops_a_quiet_child(self):
        result, elapsed, _ = self.run_child('quiet', timeout=1)
        self.assertEqual((result.outcome, D.classify(result)), ('timeout', 'timeout'))
        self.assertLess(elapsed, 1 + D.TERM_GRACE_SECONDS + 1)

    def test_timeout_escalates_to_kill_when_sigterm_is_ignored(self):
        result, elapsed, _ = self.run_child('deaf', timeout=1)
        self.assertEqual(result.outcome, 'timeout')
        self.assertGreaterEqual(elapsed, 1 + D.TERM_GRACE_SECONDS - 0.1)
        self.assertLess(elapsed, 1 + D.TERM_GRACE_SECONDS + D.REAP_SECONDS)

    def test_tool_exit_is_not_held_by_a_grandchild_on_the_pipes(self):
        result, elapsed, _ = self.run_child('orphan')
        self.assertEqual((result.outcome, result.returncode), ('exit', 3))
        self.assertLess(elapsed, 2.5)

    def test_exception_during_capture_stops_reaps_and_closes(self):
        real = D.selectors.DefaultSelector
        calls = []

        class Failing(real):
            def select(self, timeout=None):
                calls.append(1)
                if len(calls) == 3:
                    raise KeyboardInterrupt('SENTINEL_TOKEN')
                return super().select(timeout)

        with mock.patch.object(D.selectors, 'DefaultSelector', Failing):
            with self.assertRaises(KeyboardInterrupt):
                self.run_child('flood')
        pid = int((self.state / 'starts').read_text().split()[0])
        self.assertRaises(ProcessLookupError, os.kill, pid, 0)


# build-tools_r35_linux.zip (SHA-256 bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88) member hashes. The
# real-tool cases run only against an already installed tree whose files equal these; nothing is downloaded or substituted.
PINNED_BUILD_TOOLS = {
    'apksigner': 'b47549e373b895ce6ca620d0c7887e674d9615ffa837a86ac601dcfd04adb0f0',
    'lib/apksigner.jar': '00ef9948f843fe395d2440ae3ef41405b8040a6d5d46493bd1902ac0ee6deae7',
    'zipalign': 'a205d94a45c8522ca1b571dcbe190df145c818f0a29dfb366e4dd78eef4f56a7',
    'aapt': '393c4c87f675f9555c50921fb53be1f3893cacd29f2270c4863e2e3f99583c39',
}


def pinned_build_tools():
    """(build-tools dir, android.jar) of an installed tree matching PINNED_BUILD_TOOLS, else (None, reason)."""
    roots = [os.environ.get(k) for k in ('ANDROID_SDK_ROOT', 'ANDROID_HOME')] + ['/opt/roseos/android-sdk',
                                                                                 '/usr/local/lib/android/sdk']
    for root in dict.fromkeys(filter(None, roots)):
        for name in ('android-15', '35.0.0'):
            bt = Path(root) / 'build-tools' / name
            if all((bt / f).is_file() and D.sha256_file(bt / f) == h for f, h in PINNED_BUILD_TOOLS.items()):
                jars = sorted(Path(root).glob('platforms/android-3*/android.jar'))
                if jars:
                    return bt, jars[-1]
    return None, 'no installed build-tools tree equals the pinned r35 archive members (no download is attempted)'


class RealPinnedTool(unittest.TestCase):
    """MODELS-SIGN-INVOKE-001: the real pinned apksigner through scripts/sign-apk.py with a disposable synthetic PKCS12
    fixture (models-v0 recipe shape: RSA-4096, alias rose-models, one single-line passphrase for store and key). The
    JVM is whatever `java` is on PATH, NOT the pinned AOSP JDK21 tree. A synthetic signature is test evidence only."""

    @classmethod
    def setUpClass(cls):
        cls.bt, android_jar = pinned_build_tools()
        java = shutil.which('java')
        if cls.bt is None or not java:
            raise unittest.SkipTest(android_jar if cls.bt is None else 'no java on PATH')
        jdk_bin = Path(os.path.realpath(java)).parent
        if not (jdk_bin / 'keytool').is_file():
            raise unittest.SkipTest('no keytool beside java')
        cls.tmp = Path(tempfile.mkdtemp(prefix='invoke-SENTINEL-PATH-'))
        os.chmod(cls.tmp, 0o700)
        cls.sdk = cls.tmp / 'sdk'
        (cls.sdk / 'build-tools').mkdir(parents=True)
        (cls.sdk / 'build-tools/android-15').symlink_to(cls.bt)  # sign-apk.py's fixed layout
        cls.env = {'PATH': f'{jdk_bin}:/usr/bin:/bin', 'LC_ALL': 'C', 'HOME': str(cls.tmp), 'ANDROID_SDK_ROOT': str(cls.sdk),
                   'SENTINEL_ENV': 'SENTINEL_ENV_VALUE'}
        cls.phrase = 'SENTINEL_PASSWORD_7c1e-' + os.urandom(18).hex()
        cls.one_line = cls.private('pass-one-line', cls.phrase + '\n')
        cls.key_line = cls.private('pass-key', cls.phrase + '\n')
        cls.keystore = cls.tmp / 'ks.p12'
        cls.tool([str(jdk_bin / 'keytool'), '-genkeypair', '-keystore', str(cls.keystore), '-storetype', 'PKCS12',
                  '-alias', 'rose-models', '-keyalg', 'RSA', '-keysize', '4096', '-sigalg', 'SHA256withRSA',
                  '-dname', 'CN=Synthetic Test Fixture Not Rose Models', '-validity', '1',
                  '-storepass:file', str(cls.one_line), '-keypass:file', str(cls.one_line), '-noprompt'])
        os.chmod(cls.keystore, 0o600)
        pem = cls.tool([str(jdk_bin / 'keytool'), '-exportcert', '-alias', 'rose-models', '-keystore', str(cls.keystore),
                        '-storetype', 'PKCS12', '-storepass:file', str(cls.one_line)]).stdout
        cls.cert = hashlib.sha256(pem).hexdigest()
        manifest = cls.private('AndroidManifest.xml', '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                               'package="test.synthetic.invoke" android:versionCode="1" android:versionName="0"><uses-sdk '
                               'android:minSdkVersion="29"/><application android:hasCode="false"/></manifest>\n')
        cls.tool([str(cls.bt / 'aapt'), 'package', '-f', '-M', str(manifest), '-I', str(android_jar), '-F', str(cls.tmp / 'u.apk')])
        cls.tool([str(cls.bt / 'zipalign'), '-p', '-f', '4', str(cls.tmp / 'u.apk'), str(cls.tmp / 'in.apk')])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    @classmethod
    def private(cls, name, text, mode=0o600):
        path = cls.tmp / name
        path.write_text(text)
        os.chmod(path, mode)
        return path

    @classmethod
    def tool(cls, argv):
        result = subprocess.run(argv, env=cls.env, stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
        if result.returncode:
            raise AssertionError(f'fixture step failed: {Path(argv[0]).name}')
        return result

    def sign(self, name, store, key, alias='rose-models'):
        config = self.private(f'config-{name}.json', json.dumps({'keyAlias': alias, 'storePasswordFile': str(store),
                                                                 'keyPasswordFile': str(key)}))
        diag = self.tmp / f'diag-{name}'
        diag.mkdir(mode=0o700)
        out = self.tmp / f'out-{name}' / 'signed.apk'
        result = subprocess.run([sys.executable, '-I', str(SIGN_APK), str(self.tmp / 'in.apk'), '--keystore', str(self.keystore),
                                 '--config', str(config), '--output', str(out), '--diagnostic-out', str(diag / 'r.json')],
                                env=self.env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
        record = json.loads((diag / 'r.json').read_text())
        self.assertIsNone(D.validate(record))
        self.assertEqual(record['apksignerJarSha256'], PINNED_BUILD_TOOLS['lib/apksigner.jar'])
        for text in (result.stdout, result.stderr, json.dumps(record)):
            for sentinel in SENTINELS + (self.phrase, str(self.tmp)):
                self.assertNotIn(sentinel, text)
        return result, record, out

    def assertRefused(self, name, store, key, category, alias='rose-models'):
        result, record, out = self.sign(name, store, key, alias)
        self.assertEqual((result.returncode, result.stderr), (1, 'APK signing stopped: signing_failed\n'))
        self.assertEqual((record['stage'], record['outcome'], record['childExit'], record['category']),
                         ('sign', 'exit', 2, category))
        self.assertFalse(out.exists())

    def test_production_shape_one_line_file_for_store_and_key_is_password_file_exhausted(self):
        # apksigner reads a file named by both --ks-pass and --key-pass one line per password (its sign --help).
        self.assertRefused('same-file', self.one_line, self.one_line, 'password_file_exhausted')

    def test_separate_password_files_sign_and_verify(self):
        result, record, out = self.sign('separate', self.one_line, self.key_line)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((record['stage'], record['outcome'], record['category']), ('complete', 'ok', 'none'))
        self.assertEqual(json.loads(result.stdout)['certificateSha256'], [self.cert])
        verify = subprocess.run([str(self.bt / 'apksigner'), 'verify', str(out)], env=self.env, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=300)
        self.assertEqual(verify.returncode, 0)

    def test_missing_alias_is_alias_not_found(self):
        self.assertRefused('alias', self.one_line, self.key_line, 'alias_not_found', alias='SENTINEL_TOKEN')

    def test_unreadable_password_file_is_password_file_unreadable(self):
        if os.geteuid() == 0:
            self.skipTest('root reads a mode-000 file')
        unreadable = self.private('pass-unreadable', self.phrase + '\n', 0o000)
        self.assertRefused('unreadable', unreadable, unreadable, 'password_file_unreadable')


if __name__ == '__main__':
    unittest.main()
