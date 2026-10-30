"""MODELS-SIGN-PRECHECK-001 tests for synthetic_sign_check.py: explicit roots only, no skips, bounded tools, owned
cleanup on every path and a closed result without sentinels. Fake JDK trees (shell scripts) and mocks drive the
failure paths; the real pinned-tool run is the required CLI step in models-signing-verification.yml."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import synthetic_sign_check as C  # noqa: E402

SENTINELS = ('SENTINEL_PASSWORD_7c1e', 'SENTINEL-PATH', 'SENTINEL_TOKEN', 'SENTINEL_ENV_VALUE')
KEYTOOL = r'''#!/bin/sh
# Synthetic keytool for failure-path tests: logs only variable NAMES, prints sentinels, then follows its mode.
d=$(dirname "$0")/..
env | cut -d= -f1 | sort > "$d/env-names"
echo "$PATH" | cut -d: -f1 > "$d/path-head"
echo "keytool says SENTINEL_TOKEN at $(pwd)/SENTINEL-PATH" >&2
case "$(cat "$d/mode")" in
  fail) out=""; prev=""; for a in "$@"; do [ "$prev" = -keystore ] && out=$a; prev=$a; done
        [ -n "$out" ] && echo partial > "$out"; exit 1;;
  slow) echo $$ > "$d/pid"; exec sleep 30;;
esac
exit 1
'''


def fake_tree(root, mode='fail', keytool=True):
    """<root>/jdk/bin/{java,keytool} and <root>/sdk/build-tools/android-15/{apksigner,zipalign,lib/apksigner.jar}."""
    jdk, bt = Path(root) / 'jdk', Path(root) / 'sdk/build-tools/android-15'
    (jdk / 'bin').mkdir(parents=True)
    (bt / 'lib').mkdir(parents=True)
    (bt / 'lib/apksigner.jar').write_bytes(b'synthetic jar')
    tools = [(jdk / 'bin/java', '#!/bin/sh\nexit 1\n'), (bt / 'apksigner', '#!/bin/sh\nexit 1\n'),
             (bt / 'zipalign', '#!/bin/sh\nexit 1\n')]
    if keytool:
        tools.append((jdk / 'bin/keytool', KEYTOOL))
    for path, body in tools:
        path.write_text(body)
        os.chmod(path, 0o755)
    (jdk / 'mode').write_text(mode)
    return jdk, Path(root) / 'sdk'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='precheck-test-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.parent = self.tmp / 'owned-SENTINEL-PATH'
        self.parent.mkdir()
        env = mock.patch.dict(os.environ, {'SENTINEL_ENV': 'SENTINEL_ENV_VALUE', 'JAVA_TOOL_OPTIONS': '-XX:+Poison',
                                           'PATH': f'{self.tmp}/poison-SENTINEL-PATH:{os.environ["PATH"]}'})
        env.start()
        self.addCleanup(env.stop)

    def assertClosed(self, result, category):
        self.assertEqual((result['result'], result['category'], result['skipped']), ('fail', category, 0))
        self.assertIn(result['category'], C.CATEGORIES)
        text = json.dumps(result)
        for sentinel in SENTINELS + (str(self.tmp),):
            self.assertNotIn(sentinel, text)
        self.assertEqual(list(self.parent.iterdir()), [])  # nothing owned is left behind


class Inputs(Base):
    def test_roots_are_explicit_absolute_directories(self):
        jdk, sdk = fake_tree(self.tmp)
        for name, (j, s) in {'missing jdk': (None, sdk), 'missing sdk': (jdk, ''), 'relative': ('jdk', sdk),
                             'absent': (self.tmp / 'nope', sdk), 'file as root': (jdk / 'mode', sdk)}.items():
            with self.subTest(name):
                self.assertClosed(C.check(j and str(j), s and str(s), parent=self.parent), 'invalid_roots')

    def test_missing_tool_fails_instead_of_skipping_or_searching(self):
        jdk, sdk = fake_tree(self.tmp, keytool=False)
        poison = self.tmp / 'poison-SENTINEL-PATH'
        poison.mkdir()
        (poison / 'keytool').write_text('#!/bin/sh\nexit 0\n')  # a keytool on PATH is never looked up
        os.chmod(poison / 'keytool', 0o755)
        self.assertClosed(C.check(str(jdk), str(sdk), parent=self.parent), 'tool_missing')
        jdk, sdk = fake_tree(self.tmp / 'b')
        (sdk / 'build-tools/android-15/zipalign').unlink()
        self.assertClosed(C.check(str(jdk), str(sdk), parent=self.parent), 'tool_missing')
        os.chmod(jdk / 'bin/keytool', 0o644)
        self.assertClosed(C.check(str(jdk), str(sdk), parent=self.parent), 'tool_missing')

    def test_fixture_directory_inside_the_repository_is_refused_before_any_tool(self):
        jdk, sdk = fake_tree(self.tmp)
        inside = Path(tempfile.mkdtemp(prefix='precheck-inside-', dir=HERE))
        self.addCleanup(shutil.rmtree, inside, True)
        with mock.patch.object(C, 'fixture') as fixture:
            result = C.check(str(jdk), str(sdk), parent=inside)
        self.assertEqual((result['result'], result['category']), ('fail', 'temp_inside_repository'))
        fixture.assert_not_called()
        self.assertEqual(list(inside.iterdir()), [])

    def test_cli_requires_both_roots_and_returns_nonzero(self):
        with self.assertRaises(SystemExit):
            C.main(['--jdk-home', '/x'])
        jdk, sdk = fake_tree(self.tmp)
        with mock.patch('sys.stdout') as out:
            self.assertEqual(C.main(['--jdk-home', 'relative', '--sdk-root', str(sdk)]), 1)
        self.assertEqual(json.loads(out.write.call_args_list[0].args[0])['category'], 'invalid_roots')


class Paths(Base):
    def test_partial_fixture_fails_and_is_removed_with_sanitized_environment(self):
        jdk, sdk = fake_tree(self.tmp, 'fail')
        self.assertClosed(C.check(str(jdk), str(sdk), parent=self.parent), 'fixture_failed')
        names = set((jdk / 'env-names').read_text().split()) - {'PWD', 'SHLVL', '_'}  # set by the shell itself
        self.assertEqual(names, set(C.environment(jdk, sdk, self.tmp)))
        self.assertNotIn('JAVA_TOOL_OPTIONS', names)
        self.assertNotIn('SENTINEL_ENV', names)
        self.assertEqual((jdk / 'path-head').read_text().strip(), f'{jdk}/bin')

    def test_fixture_tool_timeout_is_bounded_and_reaped(self):
        jdk, sdk = fake_tree(self.tmp, 'slow')
        began = time.monotonic()
        with mock.patch.object(C, 'TOOL_SECONDS', 1):
            self.assertClosed(C.check(str(jdk), str(sdk), parent=self.parent), 'fixture_failed')
        self.assertLess(time.monotonic() - began, 1 + C.D.TERM_GRACE_SECONDS + C.D.REAP_SECONDS)
        self.assertRaises(ProcessLookupError, os.kill, int((jdk / 'pid').read_text()), 0)

    def test_sign_or_verify_failure_is_case_failed_for_every_case(self):
        jdk, sdk = fake_tree(self.tmp)
        with mock.patch.object(C, 'fixture', return_value={}), \
                mock.patch.object(C, 'sign_case', return_value=(True, False)) as case:
            result = C.check(str(jdk), str(sdk), parent=self.parent)
        self.assertClosed(result, 'case_failed')
        self.assertEqual((result['executed'], result['passed'], result['failed'], case.call_count), (3, 0, 3, 3))
        self.assertEqual(result['correctedInvocation'], {'signed': True, 'verified': False})
        paths = dict.fromkeys(('p12', 'jks', 'one', 'key', 'two'), '/x')
        paths.update({'cert-PKCS12': 'a' * 64, 'cert-JKS': 'b' * 64})
        with mock.patch.object(C, 'fixture', return_value=paths), \
                mock.patch.object(C.D, 'run_bounded', return_value=C.D.Result('exit', 2)) as tool:
            result = C.check(str(jdk), str(sdk), parent=self.parent)
        self.assertClosed(result, 'case_failed')
        self.assertEqual(tool.call_count, 3)  # one sign-apk.py start per case; no verify after a failed sign

    def test_all_cases_must_pass(self):
        jdk, sdk = fake_tree(self.tmp)
        with mock.patch.object(C, 'fixture', return_value={}), \
                mock.patch.object(C, 'sign_case', side_effect=[(True, True), (True, True), (False, False)]):
            self.assertClosed(C.check(str(jdk), str(sdk), parent=self.parent), 'case_failed')
        with mock.patch.object(C, 'fixture', return_value={}), mock.patch.object(C, 'sign_case', return_value=(True, True)):
            result = C.check(str(jdk), str(sdk), parent=self.parent)
        self.assertEqual((result['result'], result['category'], result['executed'], result['passed']), ('pass', 'none', 3, 3))

    def test_interruption_removes_the_owned_directory(self):
        jdk, sdk = fake_tree(self.tmp)
        with mock.patch.object(C, 'fixture', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                C.check(str(jdk), str(sdk), parent=self.parent)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_cleanup_failure_is_reported_not_passed(self):
        jdk, sdk = fake_tree(self.tmp)
        with mock.patch.object(C, 'fixture', return_value={}), mock.patch.object(C, 'sign_case', return_value=(True, True)), \
                mock.patch.object(C.shutil, 'rmtree'):
            result = C.check(str(jdk), str(sdk), parent=self.parent)
        self.assertEqual((result['result'], result['category']), ('fail', 'cleanup_failed'))
        for leftover in self.parent.iterdir():
            shutil.rmtree(leftover)

    def test_sigterm_to_the_cli_cleans_up_and_reaps(self):
        jdk, sdk = fake_tree(self.tmp, 'slow')
        cli = subprocess.Popen([sys.executable, '-I', str(HERE / 'synthetic_sign_check.py'), '--jdk-home', str(jdk),
                                '--sdk-root', str(sdk)], stdout=subprocess.PIPE, text=True,
                               env=dict(os.environ, TMPDIR=str(self.parent)))
        deadline = time.monotonic() + 20
        while not (jdk / 'pid').exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        cli.send_signal(signal.SIGTERM)
        out, _ = cli.communicate(timeout=20)
        self.assertEqual((cli.returncode, json.loads(out)['category']), (1, 'interrupted'))
        self.assertRaises(ProcessLookupError, os.kill, int((jdk / 'pid').read_text()), 0)
        self.assertEqual(list(self.parent.iterdir()), [])


class Manifest(unittest.TestCase):
    def test_binary_manifest_is_a_closed_axml_document(self):
        data = C.binary_manifest()
        self.assertEqual(data[:4], b'\x03\x00\x08\x00')
        self.assertEqual(int.from_bytes(data[4:8], 'little'), len(data))
        self.assertIn('minSdkVersion'.encode('utf-16-le'), data)
        self.assertIn((0x0101020C).to_bytes(4, 'little'), data)


if __name__ == '__main__':
    unittest.main()
