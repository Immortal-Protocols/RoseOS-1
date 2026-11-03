"""Source tests for the Rose Models signing adapter. Synthetic fixtures, a fake SDK, a public fake
fingerprint and mocked signer/inspector only: no key, keystore, password, real apksigner or network.
These tests prove adapter logic, not a real signature or an installed host tool."""
import copy
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import models_signing as ms

FAKE_SIGNER = '0123456789abcdef' * 4          # public, obviously fake
OTHER_SIGNER = 'fedcba9876543210' * 4
SOURCE = 'a' * 40
RUN, ATTEMPT = '424242', '1'
ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def h(data):
    return hashlib.sha256(data).hexdigest()


def write_apk(path, native, extra=None):
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in [('AndroidManifest.xml', b'synthetic manifest'), ('classes.dex', b'dex'),
                           (ms.NATIVE_ENTRY, native)] + (extra or []):
            info = zipfile.ZipInfo(name, ZIP_TIME)
            z.writestr(info, data)


def badging(package='dev.pixel.models', version=('4', '0.4.0'), abi='arm64-v8a'):
    return (f"package: name='{package}' versionCode='{version[0]}' versionName='{version[1]}' "
            "platformBuildVersionName='15'\nsdkVersion:'29'\ntargetSdkVersion:'35'\n"
            "uses-permission: name='android.permission.INTERNET'\napplication-label:'Rose Models'\n"
            f"native-code: '{abi}'\n")


def make_artifact(root, native=b'synthetic arm64 library', **badging_args):
    """A retained-artifact directory shaped exactly like stage-artifact.sh output."""
    d = Path(root)
    d.mkdir()
    write_apk(d / 'rose-models-unsigned.apk', native)
    (d / 'librose_chat_android.so').write_bytes(native)
    (d / 'apk-metadata.txt').write_text(badging(**badging_args))
    apk_sha, so_sha = h((d / 'rose-models-unsigned.apk').read_bytes()), h(native)
    (d / 'build-receipt.txt').write_text(f'apk_sha256: {apk_sha}\n')
    fields = {
        'schema': 'rose-models-unsigned-artifact/1', 'source_sha': SOURCE, 'source_dirty_models_files': '0',
        'github_event': 'push', 'github_ref': 'refs/heads/main', 'github_sha': SOURCE,
        'github_run_id': RUN, 'github_run_attempt': ATTEMPT,
        'tree_android': 'b' * 40, 'tree_rose_chat_android': 'c' * 40, 'tree_rose_chat_core': 'd' * 40,
        'tree_contracts': 'e' * 40, 'cargo_lock_rose_chat_android_sha256': '1' * 64,
        'cargo_lock_rose_chat_core_sha256': '2' * 64,
        'apk_sha256': apk_sha, 'native_sha256': so_sha, 'native_in_apk_sha256': so_sha,
    }
    (d / 'artifact-receipt.txt').write_text(''.join(f'{k}: {v}\n' for k, v in fields.items()))
    rehash(d)
    return d


def rehash(d):
    names = ['rose-models-unsigned.apk', 'librose_chat_android.so', 'apk-metadata.txt',
             'build-receipt.txt', 'artifact-receipt.txt']
    (d / 'SHA256SUMS').write_text(''.join(f'{h((d / n).read_bytes())}  {n}\n' for n in names))


def make_sdk(root, metadata_text):
    """Fake build-tools: aapt prints the fixture badging, apksigner.jar is inert bytes."""
    bt = Path(root) / 'build-tools/android-15'
    (bt / 'lib').mkdir(parents=True)
    (bt / 'aapt').write_text('#!/bin/sh\ncat <<\'EOF\'\n' + metadata_text + 'EOF\n')
    os.chmod(bt / 'aapt', 0o755)
    (bt / 'lib/apksigner.jar').write_bytes(b'synthetic apksigner jar')
    return Path(root)


def profile_for(artifact, sdk, signer=FAKE_SIGNER):
    p = ms.load_profile(Path(ms.__file__).with_name('profile.json'))
    p = copy.deepcopy(p)
    receipt = ms._receipt_fields(artifact / 'artifact-receipt.txt')
    p['expectedInput'].update({
        'sourceSha': SOURCE, 'runId': RUN, 'runAttempt': ATTEMPT,
        'apkSha256': h((artifact / 'rose-models-unsigned.apk').read_bytes()),
        'nativeSha256': h((artifact / 'librose_chat_android.so').read_bytes()),
        'artifactReceiptSha256': h((artifact / 'artifact-receipt.txt').read_bytes()),
        'apkMetadataSha256': h((artifact / 'apk-metadata.txt').read_bytes()),
        'buildReceiptSha256': h((artifact / 'build-receipt.txt').read_bytes()),
        'treeAndroid': receipt['tree_android'], 'treeRoseChatAndroid': receipt['tree_rose_chat_android'],
        'treeRoseChatCore': receipt['tree_rose_chat_core'], 'treeContracts': receipt['tree_contracts'],
        'cargoLockRoseChatAndroid': receipt['cargo_lock_rose_chat_android_sha256'],
        'cargoLockRoseChatCore': receipt['cargo_lock_rose_chat_core_sha256'],
    })
    p['signer'] = {'status': 'bound', 'certificateSha256': signer}
    p['tool'] = {'status': 'bound', **ms.tool_hashes(sdk)}
    return p


class MockSigner:
    """Stands in for scripts/sign-apk.py. Records calls; never touches key material. Like the real opt-in hook it
    writes one closed diagnostic record to --diagnostic-out."""

    def __init__(self, returncode=0, mutate_input=None, receipt_signers=None, diagnostic=None):
        self.calls, self.returncode = [], returncode
        self.mutate_input, self.receipt_signers, self.diagnostic = mutate_input, receipt_signers, diagnostic

    def write_diagnostic(self, argv, default):
        path = Path(argv[argv.index('--diagnostic-out') + 1])
        record = self.diagnostic if self.diagnostic is not None else default
        if record != 'absent':
            path.write_text(record if isinstance(record, str) else json.dumps(record))

    def __call__(self, argv, env):
        self.calls.append((argv, env))
        unsigned = Path(argv[2])
        out = Path(argv[argv.index('--output') + 1])
        pin = argv[argv.index('--expected-cert-sha256') + 1]
        if self.mutate_input:
            self.mutate_input()
        if self.returncode:
            out.write_bytes(b'partial')
            self.write_diagnostic(argv, ms.DIAG.record('sign', ms.DIAG.Result('exit', 1, None, '', ''),
                                                       apksigner_jar_sha256=None))
            return SimpleNamespace(returncode=self.returncode)
        with zipfile.ZipFile(unsigned) as src, zipfile.ZipFile(out, 'w') as dst:
            for info in src.infolist():
                dst.writestr(info, src.read(info))
            dst.writestr(zipfile.ZipInfo('META-INF/MOCK-NOT-A-SIGNATURE', ZIP_TIME), b'mock')
        out.with_suffix('.apk.idsig').write_bytes(b'mock idsig')
        out.with_suffix('.receipt.json').write_text(json.dumps({
            'sha256': h(out.read_bytes()), 'inputSha256': h(unsigned.read_bytes()),
            'certificateSha256': self.receipt_signers or [pin]}))
        self.write_diagnostic(argv, ms.DIAG.record('complete', category='none', apksigner_jar_sha256=None))
        return SimpleNamespace(returncode=0)


def mock_inspector(signers=None, schemes=None, badging_text=None):
    calls = []

    def inspect(apk, sdk_root):
        calls.append(apk)
        return {'verified': True, 'signers': [FAKE_SIGNER] if signers is None else signers,
                'schemes': schemes or {'v1': False, 'v2': True, 'v3': True, 'v4': True},
                'badging': ms._badging(badging_text or badging())}
    inspect.calls = calls
    return inspect


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='models-signing-test-'))
        self.artifact = make_artifact(self.tmp / 'artifact')
        self.sdk = make_sdk(self.tmp / 'sdk', badging())
        self.profile = profile_for(self.artifact, self.sdk)
        secrets = self.tmp / 'operator'
        secrets.mkdir(mode=0o700)
        self.keystore, self.password = secrets / 'models.p12', secrets / 'password'
        self.keystore.write_bytes(b'NOT A KEYSTORE: placeholder bytes for path checks only')
        self.password.write_text('NOT A PASSWORD\n')
        os.chmod(self.keystore, 0o600)
        os.chmod(self.password, 0o600)
        self.out = self.tmp / 'signed'

    def tearDown(self):
        subprocess.run(['rm', '-rf', str(self.tmp)])

    def sign(self, profile=None, executor=None, inspector=None, out=None, keystore=None):
        return ms.sign(profile or self.profile, self.artifact, out or self.out, keystore or self.keystore,
                       self.password, self.sdk, executor=executor or MockSigner(),
                       inspector=inspector or mock_inspector(), evidence_tier='mock-source-test')

    def assertRefused(self, category, fn, *args, **kw):
        with self.assertRaises(ms.Refused) as ctx:
            fn(*args, **kw)
        self.assertTrue(str(ctx.exception).startswith(category), str(ctx.exception))
        return ctx.exception

    def assertNoSuccess(self):
        self.assertFalse((self.out / 'models-signing-receipt.json').exists())
        self.assertFalse((self.out / ms.SIGNED_NAME).exists())

    def assertOperatorFilesIntact(self):
        self.assertEqual(self.keystore.read_bytes(), b'NOT A KEYSTORE: placeholder bytes for path checks only')
        self.assertEqual(self.password.read_text(), 'NOT A PASSWORD\n')


# MODELS-DEMO-SIGN-BIND-001: the selected 0.4.0 input, exactly as the producer's own verify-artifact job (run 41497558818
# attempt 1, job 125457241910) printed SHA256SUMS and artifact-receipt.txt for artifact 13167466300. The tree and
# Cargo.lock values also equal git at the source commit.
V4_INPUT = {
    'githubEvent': 'push',
    'githubRef': 'refs/heads/main',
    'sourceSha': 'c0362876f7473de1874b37a985356216be331fd1',
    'runId': '41497558818',
    'runAttempt': '1',
    'apkSha256': '5d3fac36d3dbf569c247c5e960cfd30df8d090ff54f02ec666a4e5558faec68c',
    'nativeSha256': 'a0a448e17d40349e25cc6a38d7dc4fbc1e09ebf8983ad36221d4d5ad6c632c6b',
    'artifactReceiptSha256': '3b41e174d669a0df598e627d2f10cbabc4dc10bb603ddc49653444dabc028508',
    'apkMetadataSha256': 'acd83a83ced043352507fadec9bf43261aea50d22dfb8cdda22d969de7aa3090',
    'buildReceiptSha256': '08c5a0f510e70a5429787529b0ebb3d1a72fcd715fbf1f3f47d48aa62013f187',
    'treeAndroid': 'c5ed69978ef993e55bd68e8facde0a3496f91bfb',
    'treeRoseChatAndroid': '09859c9a84892d1336f1ae5b69bb68f877030bb8',
    'treeRoseChatCore': 'c64964b6d773a2a6160f54b71c531aef24e473a5',
    'treeContracts': 'b682015250b4fed7105fec86e1ebde47a6fc779a',
    'cargoLockRoseChatAndroid': '8b843cce02f19bf86a31554018bd56e94c4d0b0acf847c364545cdf4b1349139',
    'cargoLockRoseChatCore': '95378f5c99aa716c2ea6b505570e5869c9db24b6159cd03fcef2def8bd388861',
}
# The superseded v2 binding (run 41106159024, 0.2.0, no Qwen selection); none of it may be accepted again.
V2_INPUT = {
    'sourceSha': '5bc2d03fbc77a3f3569b0536a6ca87949a166837',
    'runId': '41106159024',
    'apkSha256': '5a48d30dc50c3247a65141a7aa123bbe845da63015d6faffde6c52c3d206faf4',
    'nativeSha256': 'f3aad0226f99a8b0853663d08bd0e623559741338dd54915443f1daac811d060',
    'artifactReceiptSha256': '1b389e38a6407776ccfb0cd82f9d31aabdb6dadf9d80c5bd6610ef61ff0fbb8b',
    'apkMetadataSha256': '4660b8a6d0b9e4dada6b6c8dba0e1ccf401d5fd0626ad74e61f3ccde7309ad27',
    'buildReceiptSha256': 'e10f5e78092cc7cbcf7d39d6c929d55b59f540a77c8fd918b61826c4a095b65d',
    'treeAndroid': 'a473151ad8c30e28c79bc8c245e25ca36ce33e89',
    'treeRoseChatCore': '665ffe46a629877a12cb87ca567ce74f40616a5f',
    'treeContracts': 'f6aa5f32fb1b2f04e1ebe25a736eae3e5337c83f',
}
# The superseded v1 binding (run 40960062067); none of it may be accepted again.
V1_INPUT = {
    'sourceSha': '8ff67d6bcfabad015dae7d370325cf8bf21690d2', 'runId': '40960062067',
    'apkSha256': '7c8fc53c59517da7cd466c7e9695d26846485aced74de53ee764e71b11dfae15',
    'artifactReceiptSha256': 'df269566539dec21dd0d4f7e8e33fdcfb5975f262cdf4860b19719c66af94d54',
    'apkMetadataSha256': 'cc1c42104c1e2116e9bdfe8b049a73a9cf47f9757edead87134bb1a5f2e78ddc',
    'buildReceiptSha256': 'b3819357931761b7b147e7827e1fb5cdc723ecd3ad53bf7be04a2fd2b9f7a544',
    'treeAndroid': '7b8b79783bc12db7f89b763b5f107fb38654a3a6',
    'treeContracts': '10a4dbdf3a2e7147bc64f0d3b9686b87b7c29964',
}


class ProfileTests(Fixture):
    def test_committed_profile_is_valid_and_unbound(self):
        p = ms.load_profile(Path(ms.__file__).with_name('profile.json'))
        self.assertEqual(p['package'], 'dev.pixel.models')
        self.assertEqual((p['versionCode'], p['versionName'], p['abi']), ('4', '0.4.0', 'arm64-v8a'))
        self.assertEqual(p['requiredSchemes'], ['v2', 'v3'])
        self.assertEqual(ms.readiness(p), ['signer_unbound', 'tool_unbound'])
        self.assertEqual(p['expectedInput'], V4_INPUT)
        for old_input in (V1_INPUT, V2_INPUT):
            for key, old in old_input.items():
                self.assertNotEqual(p['expectedInput'][key], old, key)

    def test_old_or_wrong_input_binding_refuses_before_signing(self):
        # The artifact's own hashes and receipt fields are checked against the profile, independent of SHA256SUMS
        # and of verify-artifact.sh (stubbed here so only the adapter's pinned bindings can refuse).
        verifier = lambda *a, **k: SimpleNamespace(returncode=0, stdout='', stderr='')
        bound = profile_for(self.artifact, self.sdk)
        ms.verify_input(bound, self.artifact, run_tool=verifier)
        hashes = {'apkSha256', 'artifactReceiptSha256', 'apkMetadataSha256', 'buildReceiptSha256'}
        cases = [(k, val) for k, val in dict(V1_INPUT, nativeSha256='0' * 64, cargoLockRoseChatAndroid='f' * 64,
                                             treeRoseChatCore='9' * 40).items()] + list(V2_INPUT.items())
        for key, value in cases:
            with self.subTest(key):
                p = copy.deepcopy(bound)
                p['expectedInput'][key] = value
                category = 'input_hash_mismatch' if key in hashes | {'nativeSha256'} else 'source_binding'
                self.assertRefused(category, ms.verify_input, p, self.artifact, run_tool=verifier)

    def write(self, data):
        f = self.tmp / 'p.json'
        f.write_text(json.dumps(data))
        return f

    def test_closed_schema(self):
        cases = {
            'unknown field': lambda p: p.update(command='rm -rf /'),
            'missing field': lambda p: p.pop('tool'),
            'other purpose': lambda p: p.update(purpose='sign-anything'),
            'voice package': lambda p: p.update(package='dev.pixel.voice'),
            'other alias': lambda p: p.update(keyAlias='rose-agent'),
            'floating source': lambda p: p['expectedInput'].update(sourceSha='main'),
            'short hash': lambda p: p['expectedInput'].update(apkSha256='7c8fc53c'),
            'pull request ref': lambda p: p['expectedInput'].update(githubRef='refs/pull/135/merge'),
            'unknown expected field': lambda p: p['expectedInput'].update(extra='x'),
            'unbound with value': lambda p: p['signer'].update(certificateSha256=FAKE_SIGNER),
            'bound without value': lambda p: p['signer'].update(status='bound'),
            'upper-case pin': lambda p: p['signer'].update(status='bound', certificateSha256=FAKE_SIGNER.upper()),
            'v1 only': lambda p: p.update(requiredSchemes=['v1']),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                p = copy.deepcopy(self.profile)
                p['signer'] = {'status': 'unbound', 'certificateSha256': None}
                mutate(p)
                self.assertRaises(ms.Refused, ms.load_profile, self.write(p))

    def test_plan_reads_nothing_private(self):
        with mock.patch.object(ms, 'check_private_file') as private:
            planned = ms.plan(self.profile, self.artifact, self.out)
        private.assert_not_called()
        self.assertEqual(planned['blocked'], [])
        self.assertFalse(self.out.exists())

    def test_plan_path_guards(self):
        self.out.mkdir()
        self.assertRefused('output_collision', ms.plan, self.profile, self.artifact, self.out)
        link = self.tmp / 'link'
        link.symlink_to(self.artifact)
        self.assertRefused('unsafe_path', ms.plan, self.profile, link, self.tmp / 'o2')
        self.assertRefused('unsafe_path', ms.plan, self.profile, self.artifact, ms.REPO / 'out-signed')

    def test_cli_plan_refuses_unbound_committed_profile(self):
        r = subprocess.run(['python3', str(Path(ms.__file__)), 'plan'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(json.loads(r.stdout)['blocked'], ['signer_unbound', 'tool_unbound'])


class SignTests(Fixture):
    def test_positive_mock_path(self):
        signer, inspector = MockSigner(), mock_inspector()
        receipt = self.sign(executor=signer, inspector=inspector)
        self.assertEqual(len(signer.calls), 1)
        argv, env = signer.calls[0]
        self.assertEqual(argv[1], str(ms.SIGN_APK))
        self.assertEqual(set(env), {'PATH', 'ANDROID_SDK_ROOT'})
        self.assertNotIn('NOT A PASSWORD', ' '.join(argv) + json.dumps(env))
        self.assertEqual(receipt['evidenceTier'], 'mock-source-test')
        self.assertIn('MOCK', receipt['notice'])
        self.assertEqual(receipt['signerCertificateSha256'], FAKE_SIGNER)
        self.assertEqual(receipt['input']['apkSha256'], self.profile['expectedInput']['apkSha256'])
        self.assertEqual(receipt['output']['sha256'], h((self.out / ms.SIGNED_NAME).read_bytes()))
        self.assertFalse((self.out / 'work').exists(), 'adapter-owned config/copy removed')
        written = ''.join(f.read_text(errors='replace') for f in self.out.iterdir() if f.suffix == '.json')
        self.assertNotIn(str(self.tmp / 'operator'), written, 'no private path in receipts')
        self.assertEqual(stat.S_IMODE((self.out / 'models-signing-receipt.json').stat().st_mode), 0o600)
        self.assertOperatorFilesIntact()

    def test_unbound_profile_touches_nothing(self):
        for block in ('signer', 'tool'):
            with self.subTest(block):
                p = copy.deepcopy(self.profile)
                p[block] = ms.load_profile(Path(ms.__file__).with_name('profile.json'))[block]
                signer, inspector = MockSigner(), mock_inspector()
                run_tool, tools = mock.Mock(), mock.Mock()
                with mock.patch.object(ms, 'check_private_file') as private:
                    self.assertRefused('not_ready', ms.sign, p, self.artifact, self.out, self.keystore,
                                       self.password, self.sdk, executor=signer, inspector=inspector,
                                       tools=tools, run_tool=run_tool)
                private.assert_not_called()
                run_tool.assert_not_called()
                tools.assert_not_called()
                self.assertEqual((signer.calls, inspector.calls), ([], []))
                self.assertFalse(self.out.exists())

    def test_wrong_package_version_abi_in_input(self):
        # verify-artifact.sh (unchanged) already rejects a foreign package or ABI on input; the
        # adapter's own identity check is what catches a version change, and every output change.
        for name, kw, category in [('voice', {'package': 'dev.pixel.voice'}, 'input_verifier_failed'),
                                   ('old v1', {'version': ('1', '0.1.0-dev-unsigned')}, 'wrong_version'),
                                   ('old v2', {'version': ('2', '0.2.0')}, 'wrong_version'),
                                   ('versionName only', {'version': ('4', '0.4.1')}, 'wrong_version'),
                                   ('abi', {'abi': 'x86_64'}, 'input_verifier_failed')]:
            with self.subTest(name):
                art = make_artifact(self.tmp / f'art-{name}', **kw)
                sdk = make_sdk(self.tmp / f'sdk-{name}', badging(**kw))
                p = profile_for(art, sdk)  # every hash matches: only the identity check can refuse
                signer = MockSigner()
                self.assertRefused(category, ms.sign, p, art, self.tmp / f'o-{name}', self.keystore,
                                   self.password, sdk, executor=signer, inspector=mock_inspector())
                self.assertEqual(signer.calls, [])

    def test_mutated_input_files(self):
        for name in ('rose-models-unsigned.apk', 'librose_chat_android.so', 'artifact-receipt.txt'):
            with self.subTest(name):
                art = make_artifact(self.tmp / f'm-{name}')
                f = art / name
                f.write_bytes(f.read_bytes() + b'!')
                signer = MockSigner()
                self.assertRefused('input_verifier_failed', ms.sign, self.profile, art, self.tmp / f'o-{name}',
                                   self.keystore, self.password, self.sdk, executor=signer,
                                   inspector=mock_inspector())
                self.assertEqual(signer.calls, [])

    def test_consistent_but_swapped_artifact_needs_external_binding(self):
        # A self-consistent artifact with a different library passes verify-artifact.sh on its
        # own; only the profile's external expected hashes stop it.
        swapped = make_artifact(self.tmp / 'swapped', native=b'some other library')
        r = subprocess.run(['bash', str(ms.VERIFY_ARTIFACT), str(swapped), '--source', SOURCE,
                            '--run', RUN, '--attempt', ATTEMPT], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        signer = MockSigner()
        self.assertRefused('input_hash_mismatch', ms.sign, self.profile, swapped, self.out,
                           self.keystore, self.password, self.sdk, executor=signer, inspector=mock_inspector())
        self.assertEqual(signer.calls, [])

    def test_wrong_source_run_attempt_tree(self):
        for key, value, category in [('sourceSha', 'f' * 40, 'input_verifier_failed'),
                                     ('runId', '1', 'input_verifier_failed'),
                                     ('runAttempt', '2', 'input_verifier_failed'),
                                     ('treeRoseChatCore', '9' * 40, 'source_binding'),
                                     ('cargoLockRoseChatAndroid', '9' * 64, 'source_binding')]:
            with self.subTest(key):
                p = copy.deepcopy(self.profile)
                p['expectedInput'][key] = value
                self.assertRefused(category, ms.sign, p, self.artifact, self.tmp / f'o-{key}', self.keystore,
                                   self.password, self.sdk, executor=MockSigner(), inspector=mock_inspector())

    def test_wrong_missing_multiple_signers(self):
        for name, signers in [('wrong', [OTHER_SIGNER]), ('missing', []),
                              ('multiple', [FAKE_SIGNER, OTHER_SIGNER])]:
            with self.subTest(name):
                out = self.tmp / f'o-{name}'
                self.assertRefused('signer_mismatch', self.sign, executor=MockSigner(),
                                   inspector=mock_inspector(signers=signers), out=out)
                self.assertFalse((out / 'models-signing-receipt.json').exists())
                self.assertFalse((out / ms.SIGNED_NAME).exists())
                failure = json.loads((out / 'models-signing-failure.json').read_text())
                self.assertEqual(failure['stage'], 'inspect')
                self.assertIn(ms.SIGNED_NAME, failure['removedOutputSha256'])
        self.assertRefused('signer_receipt_mismatch', self.sign,
                           executor=MockSigner(receipt_signers=[OTHER_SIGNER]), out=self.tmp / 'o-receipt')

    def test_missing_scheme_and_identity_change_in_output(self):
        self.assertRefused('scheme_missing', self.sign,
                           inspector=mock_inspector(schemes={'v2': True, 'v3': False}), out=self.tmp / 'o1')
        self.assertRefused('wrong_package', self.sign,
                           inspector=mock_inspector(badging_text=badging(package='dev.pixel.voice')),
                           out=self.tmp / 'o2')

    def test_signer_command_failure(self):
        self.assertRefused('signer_failed', self.sign, executor=MockSigner(returncode=1))
        self.assertNoSuccess()
        failure = json.loads((self.out / 'models-signing-failure.json').read_text())
        self.assertEqual((failure['stage'], failure['category']),
                         ('sign', 'signer_failed: stage=sign outcome=exit child_exit=1 category=unclassified wrapper_exit=1'))
        self.assertIn(ms.SIGNED_NAME, failure['removedOutputSha256'])
        self.assertFalse((self.out / 'work').exists())
        self.assertOperatorFilesIntact()

    def test_input_mutation_during_execution(self):
        apk = self.artifact / 'rose-models-unsigned.apk'
        signer = MockSigner(mutate_input=lambda: apk.write_bytes(apk.read_bytes() + b'late change'))
        self.assertRefused('input_changed', self.sign, executor=signer)
        self.assertNoSuccess()

    def test_private_input_guards(self):
        cases = []
        loose = self.tmp / 'operator/loose.p12'
        loose.write_bytes(b'x')
        os.chmod(loose, 0o644)
        cases.append(('group/other readable', loose))
        link = self.tmp / 'operator/link.p12'
        link.symlink_to(self.keystore)
        cases.append(('symlink', link))
        cases.append(('missing', self.tmp / 'operator/none.p12'))
        for name, keystore in cases:
            with self.subTest(name):
                signer = MockSigner()
                with self.assertRaises(ms.Refused):
                    self.sign(executor=signer, keystore=keystore, out=self.tmp / f'o-{name}')
                self.assertEqual(signer.calls, [])
                self.assertFalse((self.tmp / f'o-{name}').exists())
        self.assertOperatorFilesIntact()

    def test_tool_identity_mismatch(self):
        (self.sdk / 'build-tools/android-15/lib/apksigner.jar').write_bytes(b'a different tool')
        signer = MockSigner()
        self.assertRefused('tool_mismatch', self.sign, executor=signer)
        self.assertEqual(signer.calls, [])

    def test_output_collision_with_existing_files(self):
        self.out.mkdir()
        (self.out / 'keep.txt').write_text('unrelated')
        self.assertRefused('output_collision', self.sign)
        self.assertEqual((self.out / 'keep.txt').read_text(), 'unrelated')


TRUE_LINES = ('Verified using v1 scheme (JAR signing): false\n'
              'Verified using v2 scheme (APK Signature Scheme v2): true\n'
              'Verified using v3 scheme (APK Signature Scheme v3): true\n'
              'Verified using v3.1 scheme (APK Signature Scheme v3.1): false\n'
              'Verified using v4 scheme (APK Signature Scheme v4): false\n')
# What the pinned verifier printed for a v2+v3-signed minSdk 29 APK over its default range (MODELS-SCHEME-DIAG-001).
DEFAULT_RANGE_LINES = TRUE_LINES.replace('v2): true', 'v2): false')


class RealInspectorTests(Fixture):
    """MODELS-SCHEME-FIX-001: the real default inspector (not mock_inspector) against a fake apksigner whose output is
    fixed per test. The real pinned-tool cases are in test_sign_diagnostic.RealPinnedTool."""

    def apksigner(self, stdout, code=0):
        bt = self.sdk / 'build-tools/android-15'
        (bt / 'verify-out').write_text(stdout)
        (bt / 'apksigner').write_text('#!/bin/sh\nd=$(dirname "$0")\nprintf \'%s\\n\' "$@" > "$d/argv"\n'
                                      f'cat "$d/verify-out"\nexit {code}\n')
        os.chmod(bt / 'apksigner', 0o755)
        return bt

    def signed(self, stdout, code=0, signer=FAKE_SIGNER):
        return stdout + f'Signer #1 certificate SHA-256 digest: {signer}\n', code

    def test_verifier_covers_api_24_and_reports_strict_schemes(self):
        bt = self.apksigner(*self.signed(TRUE_LINES))
        apk = self.tmp / 'any.apk'
        report = ms.inspect_signed(apk, self.sdk)
        self.assertEqual((bt / 'argv').read_text().split('\n')[:-1],
                         ['verify', '--verbose', '--print-certs', '--min-sdk-version', '24', str(apk)])
        self.assertEqual((report['verified'], report['signers']), (True, [FAKE_SIGNER]))
        self.assertEqual(report['schemes'], {'v1': False, 'v2': True, 'v3': True, 'v4': False})
        self.assertEqual(report['badging']['package'], 'dev.pixel.models')

    def test_sign_refuses_every_non_affirmative_scheme_result(self):
        v2 = 'Verified using v2 scheme (APK Signature Scheme v2): true\n'
        cases = {
            'default range v2 false': ('scheme_missing: v2', DEFAULT_RANGE_LINES),
            'v3 false': ('scheme_missing: v3', TRUE_LINES.replace('v3): true', 'v3): false')),
            'v2 missing': ('scheme_missing: v2', TRUE_LINES.replace(v2, '')),
            'v2 duplicate': ('scheme_missing: v2', TRUE_LINES + v2),
            'v2 contradictory': ('scheme_missing: v2', DEFAULT_RANGE_LINES + v2),
            'v2 wrong label': ('scheme_missing: v2', TRUE_LINES.replace('(APK Signature Scheme v2)', '(APK Signature Scheme v3)')),
            'v2 prefixed': ('scheme_missing: v2', TRUE_LINES.replace(v2, 'Not ' + v2)),
            'v2 suffixed': ('scheme_missing: v2', TRUE_LINES.replace('v2): true', 'v2): true (not checked)')),
            'nonzero exit with true text': ('signature_unverified', TRUE_LINES, 1),
        }
        for i, (name, (category, stdout, *code)) in enumerate(cases.items()):
            with self.subTest(name):
                self.apksigner(*self.signed(stdout, *code))
                out = self.tmp / f'o{i}'
                with self.assertRaises(ms.Refused) as ctx:
                    self.sign(inspector=ms.inspect_signed, out=out)
                self.assertEqual(str(ctx.exception), category)
                self.assertFalse((out / 'models-signing-receipt.json').exists())
                self.assertFalse((out / ms.SIGNED_NAME).exists())

    def test_sign_accepts_single_true_v2_and_v3_with_the_exact_signer_only(self):
        self.apksigner(*self.signed(TRUE_LINES, signer=OTHER_SIGNER))
        self.assertRefused('signer_mismatch', self.sign, inspector=ms.inspect_signed, out=self.tmp / 'o-signer')
        self.apksigner(*self.signed(TRUE_LINES))
        receipt = self.sign(inspector=ms.inspect_signed)
        self.assertEqual(receipt['schemes'], {'v1': False, 'v2': True, 'v3': True, 'v4': False})
        self.assertEqual(receipt['signerCertificateSha256'], FAKE_SIGNER)


class NegativeControls(Fixture):
    """Disabling an essential check must make the matching test case pass through, which shows
    the tests above depend on that check and not on an accident."""

    def test_removing_identity_check_lets_foreign_output_through(self):
        # Signed output that inspects as dev.pixel.voice is refused (test_missing_scheme_and_...);
        # with the identity check removed it would be accepted and mislabelled as Models.
        voice = mock_inspector(badging_text=badging(package='dev.pixel.voice'))
        with mock.patch.object(ms, '_require_identity', lambda *a: None):
            receipt = self.sign(inspector=voice)
        self.assertEqual(receipt['package'], 'dev.pixel.models')

    def test_removing_signer_pin_check_lets_wrong_signer_through(self):
        with mock.patch.object(ms, '_require_signer', lambda *a: None):
            receipt = self.sign(inspector=mock_inspector(signers=[OTHER_SIGNER]))
        self.assertEqual(receipt['signerCertificateSha256'], FAKE_SIGNER)  # would claim the bound signer

    def test_removing_version_check_lets_wrong_version_through(self):
        art = make_artifact(self.tmp / 'v1', version=('1', '0.1.0-dev-unsigned'))
        sdk = make_sdk(self.tmp / 'sdk-v1', badging(version=('1', '0.1.0-dev-unsigned')))
        with mock.patch.object(ms, '_require_identity', lambda *a: None):
            ms.sign(profile_for(art, sdk), art, self.out, self.keystore, self.password, sdk,
                    executor=MockSigner(), inspector=mock_inspector(), evidence_tier='mock-source-test')

    def test_removing_external_binding_lets_swapped_artifact_through(self):
        swapped = make_artifact(self.tmp / 'swapped', native=b'some other library')
        loose = copy.deepcopy(self.profile)
        with mock.patch.object(ms, 'verify_input',
                               lambda p, d, **kw: {'apkSha256': h((d / 'rose-models-unsigned.apk').read_bytes()),
                                                   'nativeSha256': h(b'some other library')}):
            loose['expectedInput']['apkSha256'] = h((swapped / 'rose-models-unsigned.apk').read_bytes())
            loose['expectedInput']['nativeSha256'] = h(b'some other library')
            receipt = ms.sign(loose, swapped, self.out, self.keystore, self.password, self.sdk,
                              executor=MockSigner(), inspector=mock_inspector(), evidence_tier='mock-source-test')
        self.assertNotEqual(receipt['input']['apkSha256'], self.profile['expectedInput']['apkSha256'])


if __name__ == '__main__':
    unittest.main()
