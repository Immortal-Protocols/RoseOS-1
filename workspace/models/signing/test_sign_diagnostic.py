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
            'big': ('sign', 'oversized', 1, None, 'output_oversized'),
            'badalign': ('alignment', 'exit', 1, None, 'unclassified'),
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


if __name__ == '__main__':
    unittest.main()
