"""OCT30-INFRA-S011: the fixed signing-tool observation in scripts/device-readiness.py.

Fixture trees only (no builder, host, network or cloud). Four fixed public executables are hashed through the
existing no-follow open; nothing is executed; avb_openssl is presence-only; the receipt and job summary carry only
allowlisted fields. An unobserved tool leaves signing-bootstrap refusing."""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import types
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS/file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


readiness = load('device_readiness_signing', 'device-readiness.py')
bootstrap = load('signing_bootstrap_observation', 'signing-bootstrap.py')
TOOLS = {'make_key': b'#!/bin/bash\n# synthetic make_key\n', 'decrypt-keys': b'#!/bin/bash\n# synthetic decrypt-keys\n',
    'keytool': b'\x7fELF synthetic keytool', 'openssl': b'\x7fELF synthetic openssl'}


class Deadline:
    def budget(self, seconds):
        return seconds


class Tree:
    def __init__(self, test):
        self.tmp = Path(tempfile.mkdtemp(prefix='signing-tools-'))
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.source, self.system = self.tmp/'src', self.tmp/'system'
        for name, (where, rel) in readiness.SIGNING_TOOLS.items():
            path = (self.source if where == 'source' else self.system)/rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(TOOLS[name])
            path.chmod(0o755)
        (self.source/'external/avb').mkdir(parents=True)
        self.host = types.SimpleNamespace(source_root=self.source, system_root=self.system, clock=lambda: 0.0)

    def observe(self):
        return readiness.signing_tools(self.host, Deadline())

    def path(self, name):
        where, rel = readiness.SIGNING_TOOLS[name]
        return (self.source if where == 'source' else self.system)/rel


@contextlib.contextmanager
def no_execution_or_network():
    def refuse(*a, **k):
        raise AssertionError('no process or network use is allowed')
    saved = (subprocess.Popen, subprocess.run, socket.create_connection, os.system)
    subprocess.Popen = subprocess.run = socket.create_connection = os.system = refuse
    try:
        yield
    finally:
        subprocess.Popen, subprocess.run, socket.create_connection, os.system = saved


class Observation(unittest.TestCase):
    def test_known_files_are_hashed_without_execution(self):
        t = Tree(self)
        with no_execution_or_network():
            observed = t.observe()
        for name, data in TOOLS.items():
            record = observed['tools'][name]
            self.assertEqual((record['status'], record['sha256'], record['sizeBytes'], record['executable']),
                ('present', hashlib.sha256(data).hexdigest(), len(data), True), name)
            self.assertEqual(record['path'], str(t.path(name)))
        self.assertEqual(set(observed['tools']), {'make_key', 'decrypt-keys', 'keytool', 'openssl'})
        self.assertEqual(readiness.SIGNING_TOOLS['openssl'], ('system', 'usr/bin/openssl'))
        self.assertEqual(readiness.SIGNING_TOOLS['keytool'], ('source', 'prebuilts/jdk/jdk21/linux-x86/bin/keytool'))

    def test_decrypt_keys_source_and_runtime_wrapper_stay_separate(self):
        observed = Tree(self).observe()
        wrapper = observed['decryptKeysRuntimeWrapper']
        self.assertEqual(wrapper['sha256'], hashlib.sha256((ROOT/'scripts/host-decrypt-keys.py').read_bytes()).hexdigest())
        self.assertNotEqual(wrapper['sha256'], observed['tools']['decrypt-keys']['sha256'])

    def test_missing_oversized_not_regular_and_slow(self):
        t = Tree(self)
        t.path('keytool').unlink()
        t.path('openssl').unlink()
        t.path('openssl').mkdir()
        observed = t.observe()
        self.assertEqual(observed['tools']['keytool']['status'], 'missing')
        self.assertEqual(observed['tools']['openssl']['status'], 'not-regular')
        self.assertNotIn('sha256', observed['tools']['keytool'])
        saved = readiness.SIGNING_TOOL_BYTES
        readiness.SIGNING_TOOL_BYTES = 8
        try:
            big = t.observe()['tools']['make_key']
        finally:
            readiness.SIGNING_TOOL_BYTES = saved
        self.assertEqual(big['status'], 'too-large-not-hashed')
        self.assertNotIn('sha256', big)
        ticks = iter([0.0] + [100.0] * 50)
        t.host.clock = lambda: next(ticks)
        self.assertEqual(readiness.tool_record(t.source, 'development/tools/make_key', 1, t.host.clock)['status'], 'hash-unavailable')
        t.host.system_root = t.tmp/'absent-root'
        self.assertTrue(t.observe()['tools']['openssl']['status'].startswith('unreadable'))

    def test_symlink_and_ancestor_escapes_are_refused_not_hashed(self):
        t = Tree(self)
        secret = t.tmp/'keys/releasekey.pk8'
        secret.parent.mkdir()
        secret.write_bytes(b'SYNTHETIC SECRET BYTES')
        t.path('make_key').unlink()
        os.symlink(secret, t.path('make_key'))  # final component to a secret file
        jdk = t.source/'prebuilts/jdk'
        shutil.rmtree(jdk)
        outside = t.tmp/'outside-jdk'
        (outside/'jdk21/linux-x86/bin').mkdir(parents=True)
        (outside/'jdk21/linux-x86/bin/keytool').write_bytes(b'outside')
        os.symlink(outside, jdk)  # an ancestor pointing outside the root
        usr = t.system/'usr'
        shutil.rmtree(usr)
        os.symlink(t.tmp/'keys', usr)  # system ancestor into a secret directory
        observed = t.observe()
        for name in ('make_key', 'keytool', 'openssl'):
            self.assertEqual(observed['tools'][name]['status'], 'symlink-refused', name)
            self.assertNotIn('sha256', observed['tools'][name])
        text = json.dumps(observed)
        self.assertNotIn(hashlib.sha256(b'SYNTHETIC SECRET BYTES').hexdigest(), text)
        self.assertNotIn(hashlib.sha256(b'outside').hexdigest(), text)

    def test_avb_openssl_is_presence_only(self):
        t = Tree(self)
        self.assertEqual(t.observe()['avbOpenssl']['status'], 'absent')
        self.assertIn('openssl from PATH', t.observe()['avbOpenssl']['effect'])
        (t.source/readiness.AVB_OPENSSL).write_bytes(b'\x7fELF avb_openssl')
        avb = t.observe()['avbOpenssl']
        self.assertEqual(avb['status'], 'present')
        self.assertNotIn('sha256', avb)
        (t.source/readiness.AVB_OPENSSL).unlink()
        os.symlink('/usr/bin/openssl', t.source/readiness.AVB_OPENSSL)
        self.assertEqual(t.observe()['avbOpenssl']['status'], 'symlink-refused')

    def test_reused_public_2901_evidence_is_labelled(self):
        public = Tree(self).observe()['reusedPublic2901']
        self.assertEqual(public['assetSha256'], '0c65767734ae68bff03ccbfd32cbf49387146ae7a82150a01b07b8d36e124e32')
        self.assertEqual(public['tools']['avbtool']['sha256'], '1aa6f2b462aa67aa3fdf488bee83ab517de8ad9d8228c7b45c35267ab2cf6781')
        self.assertIn('must revalidate', public['note'])


class ReceiptAndSummary(unittest.TestCase):
    def receipt(self, observed, **extra):
        return {'attempt': '4081-1', 'recipeSha': 'e' * 40, 'at': '2026-10-30T01:00:00Z', 'signingTools': observed,
            'filesystem': {'availableBytes': 1}, 'pins': {'x': 'y'}, **extra}

    def test_receipt_is_allowlisted(self):
        observed = Tree(self).observe()
        observed['tools']['make_key']['junk'] = 'not allowed'
        signing = readiness.signing_receipt(self.receipt(observed))
        self.assertEqual(set(signing) - set(readiness.SIGNING_RECEIPT_KEYS), set())
        self.assertNotIn('filesystem', signing)
        self.assertNotIn('pins', signing)
        self.assertTrue(all(set(r) <= set(readiness.TOOL_FIELDS) for r in signing['tools'].values()))
        self.assertEqual(signing['status'], 'observed')

    def test_secret_like_text_is_refused(self):
        observed = Tree(self).observe()
        observed['tools']['openssl']['path'] = '/home/x/api_key/openssl'
        with self.assertRaises(readiness.CheckFailed):
            readiness.signing_receipt(self.receipt(observed))

    def test_refused_inspection_writes_a_not_observed_receipt(self):
        signing = readiness.signing_receipt({'attempt': '1-1', 'recipeSha': 'e' * 40, 'refusal': 'CheckFailed: lock held'})
        self.assertEqual(signing['status'], 'not-observed (CheckFailed: lock held)')
        self.assertNotIn('tools', signing)
        self.assertIn('not-observed', readiness.signing_summary(signing))

    def test_summary_has_fixed_fields_only(self):
        observed = Tree(self).observe()
        summary = readiness.signing_summary(readiness.signing_receipt(self.receipt(observed)))
        self.assertIn('| tool | path | status | bytes | executable | sha256 |', summary)
        for name in ('make_key', 'decrypt-keys', 'keytool', 'openssl'):
            self.assertIn(f'| {name} |', summary)
        self.assertIn('avb_openssl: absent', summary)
        self.assertNotIn('availableBytes', summary)

    def test_unknown_identity_still_refuses_bootstrap(self):
        from datetime import datetime, timezone
        reasons = dict(bootstrap.readiness('fold-rango-v1', bootstrap.load_profiles(), datetime.now(timezone.utc)))
        self.assertIn('tool-unapproved', reasons)  # an observation receipt alone binds nothing


class Wiring(unittest.TestCase):
    def test_actual_ci_runs_this_suite(self):
        ci = (ROOT/'.github/workflows/release-verification.yml').read_text()
        self.assertIn('          python3 scripts/tests/test_signing_tool_observation.py\n', ci)
        self.assertIn('python3 scripts/tests/test_signing_tool_observation.py\n', (ROOT/'scripts/test-local.sh').read_text())
        self.assertEqual(ci.count("      - 'scripts/tests/test_signing_tool_observation.py'\n"), 2)


if __name__ == '__main__':
    unittest.main()
