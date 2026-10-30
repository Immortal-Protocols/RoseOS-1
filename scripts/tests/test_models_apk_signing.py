"""OCT30-INFRA-S013: scripts/host-models-sign.py and the os-release models-sign wiring.

Synthetic, non-secret fixtures only: a hand-made zip stands in for the Google build-tools archive, input files are
placeholder bytes, the bootstrap receipt and journal are synthetic, keytool, secret fetching and the PR #149 adapter
are mocked. No key, signature, host, secret, API or network use."""
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('host_models_sign', SCRIPTS/'host-models-sign.py')
hms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hms)
BOOTSTRAP = hms.models_profile()
TRUST = BOOTSTRAP['tools']['keytool']['trust']
CERT = 'c' * 64
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()


def receipt(**change):
    data = {'schema': 'roseos.signing-bootstrap-receipt.v1', 'profile': 'models-v0', 'status': 'complete', 'recoveryNeeded': False,
        'recipeSha': 'a' * 40,
        'secrets': {'keys': {'name': 'PIXEL_MODELS_SIGNING_KEYSTORE_V1', 'committedVersion': '1', 'state': 'committed'},
                    'passphrase': {'name': 'PIXEL_MODELS_SIGNING_PASSPHRASE_V1', 'committedVersion': '1', 'state': 'committed'}},
        'publicIdentity': dict(BOOTSTRAP['generation']['identity'], certificateSha256=CERT, notBefore='2026-10-30T02:14:00Z'),
        'tools': {'keytool': {'trustBasis': 'pinned-git-tree', 'revision': TRUST['revision'], 'treeId': TRUST['treeId'],
                              'toolBlob': TRUST['toolBlob']}},
        'cleanup': {'workspaceCreated': True, 'workspaceRemoved': True}}
    for key, value in change.items():
        data[key] = value
    return data


class Tmp:
    def __init__(self, test):
        self.root = Path(tempfile.mkdtemp(prefix='models-sign-'))
        test.addCleanup(shutil.rmtree, self.root, True)

    def receipt_dir(self, data):
        d = self.root/'receipt'
        d.mkdir(exist_ok=True)
        (d/'signing-bootstrap-receipt.json').write_text(json.dumps(data))
        return d

    def journal(self, data, mode=0o600):
        path = self.root/'home'/'rose-signing-bootstrap'/'models-v0.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        os.chmod(path, mode)
        return self.root/'home'


def journal(**change):
    data = {'profile': 'models-v0', 'status': 'complete', 'committed': {'keys': '1', 'passphrase': '1'},
        'publicIdentity': dict(BOOTSTRAP['generation']['identity'], certificateSha256=CERT)}
    data.update(change)
    return data


class Receipt(unittest.TestCase):
    def test_complete_receipt_binds_versions_certificate_and_alias(self):
        t = Tmp(self)
        binding = hms.check_receipt(t.receipt_dir(receipt()), '40990434375', is_on_main=lambda sha: True)
        self.assertEqual(binding['versions'], {'keys': '1', 'passphrase': '1'})
        self.assertEqual((binding['certificateSha256'], binding['alias'], binding['runId']), (CERT, 'rose-models', '40990434375'))

    def test_incomplete_or_foreign_receipts_refuse(self):
        secrets = receipt()['secrets']
        cases = {
            'profile': receipt(profile='fold-rango-v1'),
            'status': receipt(status='recovery-needed'),
            'recovery': receipt(recoveryNeeded=True),
            'floating version': receipt(secrets=dict(secrets, keys=dict(secrets['keys'], committedVersion='latest'))),
            'uncommitted': receipt(secrets=dict(secrets, passphrase=dict(secrets['passphrase'], state='uncertain'))),
            'wrong secret': receipt(secrets=dict(secrets, keys=dict(secrets['keys'], name='PIXEL_OS_SIGNING_KEYS_RANGO_V1'))),
            'no certificate': receipt(publicIdentity=dict(receipt()['publicIdentity'], certificateSha256=None)),
            'wrong alias': receipt(publicIdentity=dict(receipt()['publicIdentity'], alias='other')),
            'wrong key size': receipt(publicIdentity=dict(receipt()['publicIdentity'], keySize=2048)),
            'other jdk proof': receipt(tools={'keytool': {'trustBasis': 'installed-hash'}}),
            'cleanup unknown': receipt(cleanup={'workspaceRemoved': None}),
            'no recipe': receipt(recipeSha=None),
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaisesRegex(hms.Refused, 'bootstrap_receipt_invalid'):
                    hms.check_receipt(Tmp(self).receipt_dir(data), '1', is_on_main=lambda sha: True)
        with self.assertRaisesRegex(hms.Refused, 'not on main'):
            hms.check_receipt(Tmp(self).receipt_dir(receipt()), '1', is_on_main=lambda sha: False)
        with self.assertRaisesRegex(hms.Refused, 'bootstrap_receipt_missing'):
            hms.check_receipt(Tmp(self).root/'none', '1')

    def test_journal_must_agree_with_the_receipt(self):
        binding = hms.check_receipt(Tmp(self).receipt_dir(receipt()), '1', is_on_main=lambda sha: True)
        t = Tmp(self)
        self.assertRegex(hms.check_journal(t.journal(journal()), binding), '^[0-9a-f]{64}$')
        for label, data, code in (
            ('foreign', journal(profile='fold-rango-v1'), 'bootstrap_journal_foreign'),
            ('incomplete', journal(status='recovery-needed'), 'bootstrap_journal_foreign'),
            ('versions', journal(committed={'keys': '2', 'passphrase': '1'}), 'bootstrap_version_mismatch'),
            ('certificate', journal(publicIdentity=dict(journal()['publicIdentity'], certificateSha256='d' * 64)), 'bootstrap_identity_mismatch'),
        ):
            with self.subTest(label):
                with self.assertRaisesRegex(hms.Refused, code):
                    hms.check_journal(Tmp(self).journal(data), binding)
        with self.assertRaisesRegex(hms.Refused, 'bootstrap_journal_unsafe'):
            hms.check_journal(Tmp(self).journal(journal(), mode=0o644), binding)
        with self.assertRaisesRegex(hms.Refused, 'bootstrap_journal_missing'):
            hms.check_journal(Tmp(self).root/'no-home', binding)
        t = Tmp(self)
        home = t.journal(journal())
        real = home/'rose-signing-bootstrap/models-v0.json'
        os.rename(real, real.with_name('real.json'))
        os.symlink(real.with_name('real.json'), real)
        with self.assertRaisesRegex(hms.Refused, 'bootstrap_journal_unsafe'):
            hms.check_journal(home, binding)


class Input(unittest.TestCase):
    def setup(self):
        t = Tmp(self)
        d = t.root/'admitted'
        d.mkdir()
        profile = json.loads((ROOT/'workspace/models/signing/profile.json').read_text())
        for name, key in hms.INPUT_FILES.items():
            (d/name).write_bytes(f'synthetic {name}'.encode())
            if key:
                profile['expectedInput'][key] = hashlib.sha256(f'synthetic {name}'.encode()).hexdigest()
        cfg = copy.deepcopy(hms.config())
        cfg['input']['admittedDirectory'] = str(d)
        return d, profile, cfg

    def test_exact_six_files_pass_and_drift_refuses(self):
        d, profile, cfg = self.setup()
        self.assertEqual(hms.check_input(profile, cfg), d)
        (d/'librose_chat_android.so').write_bytes(b'drifted')
        with self.assertRaisesRegex(hms.Refused, 'input_changed'):
            hms.check_input(profile, cfg)

    def test_missing_or_unadmitted_input_is_unavailable(self):
        self.assertIsNone(hms.config()['input']['admittedDirectory'])
        with self.assertRaisesRegex(hms.Refused, 'input_unavailable'):
            hms.check_input(json.loads((ROOT/'workspace/models/signing/profile.json').read_text()))
        d, profile, cfg = self.setup()
        (d/'extra.txt').write_text('x')
        with self.assertRaisesRegex(hms.Refused, 'input_unavailable'):
            hms.check_input(profile, cfg)
        (d/'extra.txt').unlink()
        (d/'SHA256SUMS').unlink()
        with self.assertRaisesRegex(hms.Refused, 'input_unavailable'):
            hms.check_input(profile, cfg)
        link = d.parent/'alias'
        os.symlink(d, link)
        cfg['input']['admittedDirectory'] = str(link)
        with self.assertRaisesRegex(hms.Refused, 'input_unavailable'):
            hms.check_input(profile, cfg)


def zip_member(archive, name, data, mode=0o644, link=False):
    info = zipfile.ZipInfo(name)
    info.external_attr = ((stat.S_IFLNK | 0o777) if link else (stat.S_IFREG | mode)) << 16
    archive.writestr(info, data)


class Archive(unittest.TestCase):
    def setup(self, extra=()):
        t = Tmp(self)
        sdk = t.root/'sdk'
        installed = sdk/'build-tools/android-15'
        (installed/'lib').mkdir(parents=True)
        (installed/'lib64').mkdir()
        files = {'lib/apksigner.jar': (b'PK synthetic apksigner.jar', 0o644), 'aapt': (b'\x7fELF synthetic aapt', 0o755),
            'apksigner': (b'#!/bin/sh synthetic launcher', 0o755), 'lib64/libc++.so': (b'\x7fELF synthetic libc++', 0o644)}
        archive_path = sdk/'downloads/build-tools_r35_linux.zip'
        archive_path.parent.mkdir()
        with zipfile.ZipFile(archive_path, 'w') as z:
            for rel, (data, mode) in files.items():
                zip_member(z, f'android-15/{rel}', data, mode)
                (installed/rel).write_bytes(data)
                os.chmod(installed/rel, mode)
            zip_member(z, 'android-15/lib64/libcxx.so', b'libc++.so', link=True)
            os.symlink('libc++.so', installed/'lib64/libcxx.so')
            for name, data, kwargs in extra:
                zip_member(z, name, data, **kwargs)
        cfg = copy.deepcopy(hms.config())
        cfg['sdk'].update(root=str(sdk), archive=str(archive_path), archiveSha256=hashlib.sha256(archive_path.read_bytes()).hexdigest())
        return cfg, installed, files

    def test_archive_members_bind_the_installed_tree(self):
        cfg, installed, files = self.setup()
        tools = hms.archive_proof(cfg)
        self.assertEqual(tools['apksignerSha256'], hashlib.sha256(files['lib/apksigner.jar'][0]).hexdigest())
        self.assertEqual(tools['aaptSha256'], hashlib.sha256(files['aapt'][0]).hexdigest())
        self.assertEqual(hms.config()['sdk']['archiveSha256'], 'bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88')

    def test_installed_drift_refuses(self):
        for label, change in {
            'altered jar': lambda i: (i/'lib/apksigner.jar').write_bytes(b'altered'),
            'altered aapt': lambda i: (i/'aapt').write_bytes(b'altered'),
            'extra override': lambda i: (i/'java').write_bytes(b'override'),
            'missing library': lambda i: (i/'lib64/libc++.so').unlink(),
            'mode change': lambda i: os.chmod(i/'aapt', 0o644),
            'retargeted link': lambda i: ((i/'lib64/libcxx.so').unlink(), os.symlink('../aapt', i/'lib64/libcxx.so')),
        }.items():
            with self.subTest(label):
                cfg, installed, _ = self.setup()
                change(installed)
                with self.assertRaisesRegex(hms.Refused, 'sdk_installed_mismatch'):
                    hms.archive_proof(cfg)

    def test_wrong_or_unsafe_archive_refuses(self):
        cfg, installed, _ = self.setup()
        cfg['sdk']['archiveSha256'] = '0' * 64
        with self.assertRaisesRegex(hms.Refused, 'sdk_archive_mismatch'):
            hms.archive_proof(cfg)
        for label, extra in (('traversal', [('android-15/../evil', b'x', {})]), ('other root', [('android-16/aapt', b'x', {})]),
                             ('escaping link', [('android-15/escape', b'../../etc/passwd', {'link': True})])):
            with self.subTest(label):
                cfg, _, _ = self.setup(extra)
                with self.assertRaisesRegex(hms.Refused, 'sdk_archive_unsafe'):
                    hms.archive_proof(cfg)
        cfg, _, _ = self.setup()
        cfg['sdk']['archive'] = cfg['sdk']['archive'] + '.missing'
        with self.assertRaisesRegex(hms.Refused, 'sdk_archive_missing'):
            hms.archive_proof(cfg)


class Jdk(unittest.TestCase):
    def test_pinned_tree_required(self):
        self.assertEqual(hms.jdk_proof(lambda root: TRUST['treeId'])['treeId'], TRUST['treeId'])
        with self.assertRaisesRegex(hms.Refused, 'jdk_mismatch'):
            hms.jdk_proof(lambda root: '0' * 40)

    def test_java_environment_is_sanitized(self):
        saved = dict(os.environ)
        try:
            os.environ.update(JAVA_TOOL_OPTIONS='-javaagent:x', LD_PRELOAD='/tmp/x.so', CLASSPATH='/tmp')
            env = hms.java_environment('/jdk/linux-x86')
            self.assertEqual(env['PATH'], '/jdk/linux-x86/bin:/usr/bin:/bin')
            for name in hms.INJECTION:
                self.assertNotIn(name, os.environ)
        finally:
            os.environ.clear()
            os.environ.update(saved)


class FakeAdapter:
    class Refused(Exception):
        pass

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def sign(self, profile, artifact, output, keystore, password, sdk_root):
        self.calls.append((profile, artifact, output, Path(keystore).name, Path(password).name, sdk_root, dict(os.environ)))
        if self.fail:
            raise self.Refused('signature_unverified')
        Path(output).mkdir()
        return {'schema': 'rose-models-signing-receipt/1', 'signerCertificateSha256': profile['signer']['certificateSha256']}


class Run(unittest.TestCase):
    def setup(self, cert=CERT, adapter=None):
        t = Tmp(self)
        rdir = t.receipt_dir(receipt())
        binding = hms.check_receipt(rdir, '40990434375', is_on_main=lambda sha: True)
        checked = {'adapterProfile': json.loads((ROOT/'workspace/models/signing/profile.json').read_text()), 'binding': binding,
            'input': str(t.root/'admitted'), 'journalSha256': 'e' * 64,
            'tools': {'apksignerSha256': '1' * 64, 'aaptSha256': '2' * 64, 'archiveSha256': hms.config()['sdk']['archiveSha256']},
            'jdk': {'root': '/jdk/linux-x86', 'treeId': TRUST['treeId'], 'revision': TRUST['revision']}}
        self.fetched, self.private_dirs = [], []
        saved_preflight = hms.preflight
        hms.preflight = lambda *a, **k: checked
        self.addCleanup(setattr, hms, 'preflight', saved_preflight)
        saved_env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved_env)))

        def fetch(project, name, version, destination):
            self.fetched.append((project, name, version))
            Path(destination).write_bytes(b'synthetic secret placeholder')
            os.chmod(destination, 0o600)
            return Path(destination)

        def keytool(argv, **kw):
            pem = '-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n'
            Path(argv[argv.index('-file') + 1]).write_text(pem)
            self.keytool_argv, self.keytool_env = argv, kw['env']
            return type('R', (), {'returncode': 0})()

        @contextlib.contextmanager
        def private(prefix):
            path = Path(tempfile.mkdtemp(prefix=prefix, dir=t.root))
            os.chmod(path, 0o700)
            self.private_dirs.append(path)
            try:
                yield path
            finally:
                shutil.rmtree(path)

        saved_cert = hms.host_secrets.certificate_sha256
        hms.host_secrets.certificate_sha256 = lambda path: cert
        self.addCleanup(setattr, hms.host_secrets, 'certificate_sha256', saved_cert)
        adapter = adapter or FakeAdapter()
        out = t.root/'signed'
        kwargs = dict(fetch=fetch, keytool_runner=keytool, adapter=adapter, lock=contextlib.nullcontext, private_directory=private)
        return rdir, out, adapter, kwargs

    def test_exact_versions_certificate_check_and_bound_profile(self):
        rdir, out, adapter, kwargs = self.setup()
        public = hms.run(rdir, out, **kwargs)
        self.assertEqual(self.fetched, [('protocols-465723', 'PIXEL_MODELS_SIGNING_KEYSTORE_V1', '1'),
            ('protocols-465723', 'PIXEL_MODELS_SIGNING_PASSPHRASE_V1', '1')])
        profile = adapter.calls[0][0]
        self.assertEqual(profile['signer'], {'status': 'bound', 'certificateSha256': CERT})
        self.assertEqual(profile['tool'], {'status': 'bound', 'apksignerSha256': '1' * 64, 'aaptSha256': '2' * 64})
        committed = json.loads((ROOT/'workspace/models/signing/profile.json').read_text())
        self.assertEqual(committed['signer']['status'], 'unbound')  # the committed profile stays unbound
        self.assertEqual(self.keytool_argv[self.keytool_argv.index('-storepass:file') + 1].split('/')[-1], 'passphrase')
        self.assertEqual(self.keytool_argv[0], '/jdk/linux-x86/bin/keytool')
        env = adapter.calls[0][6]
        self.assertTrue(env['PATH'].startswith('/jdk/linux-x86/bin:'))
        self.assertFalse(set(hms.INJECTION) & set(env))
        self.assertFalse(any(p.exists() for p in self.private_dirs))
        text = json.dumps(public)
        self.assertNotIn('synthetic secret placeholder', text)
        self.assertEqual(public['bootstrap']['versions'], {'keys': '1', 'passphrase': '1'})
        self.assertTrue((out/'models-sign-receipt.json').exists())

    def test_certificate_mismatch_refuses_before_signing(self):
        rdir, out, adapter, kwargs = self.setup(cert='d' * 64)
        with self.assertRaisesRegex(hms.Refused, 'signer_mismatch'):
            hms.run(rdir, out, **kwargs)
        self.assertEqual(adapter.calls, [])
        self.assertFalse(any(p.exists() for p in self.private_dirs))

    def test_adapter_refusal_and_cancellation_clean_up(self):
        rdir, out, adapter, kwargs = self.setup(adapter=FakeAdapter(fail=True))
        with self.assertRaisesRegex(hms.Refused, 'adapter_refused: signature_unverified'):
            hms.run(rdir, out, **kwargs)
        self.assertFalse(any(p.exists() for p in self.private_dirs))
        self.assertFalse((out/'models-sign-receipt.json').exists())
        rdir, out, adapter, kwargs = self.setup()

        def interrupted(*a):
            raise KeyboardInterrupt
        kwargs['fetch'] = interrupted
        with self.assertRaises(KeyboardInterrupt):
            hms.run(rdir, out, **kwargs)
        self.assertFalse(any(p.exists() for p in self.private_dirs))

    def test_preflight_refuses_before_any_secret_when_input_is_missing(self):
        t = Tmp(self)
        rdir = t.receipt_dir(receipt())
        fetched = []
        with self.assertRaisesRegex(hms.Refused, 'input_unavailable'):
            hms.run(rdir, t.root/'out', fetch=lambda *a: fetched.append(a), lock=contextlib.nullcontext)
        self.assertEqual(fetched, [])


def jobs(text):
    body = text.split('\njobs:\n', 1)[1]
    return {m[1]: m[2] for m in re.finditer(r'^  ([\w-]+):\n((?:(?:    .*)?\n)*)', body, re.M)}


class Workflow(unittest.TestCase):
    def test_models_sign_job_is_isolated_and_ordered(self):
        j = jobs(WORKFLOW)
        job = j['models-signer']
        self.assertIn("    needs: controller\n    if: inputs.mode == 'models-sign'\n", job)
        self.assertIn('    timeout-minutes: 20\n', job)
        order = [job.index(s) for s in ('host-models-sign.py preflight', 'actions-gcp-login.py login', 'host-models-sign.py run',
                 'actions-gcp-login.py logout')]
        self.assertEqual(order, sorted(order))
        self.assertIn("      - name: Drop signer access\n        if: always()\n", job)
        for word in ('secrets.', 'PIXEL_OTA', 'create-github-app-token', 'os-release-stage', 'host-sign.py', 'host-app', 'publish'):
            self.assertNotIn(word, job, word)
        self.assertIn('models-signer', re.search(r'    needs: \[([^\]]*)\]', j['cleanup'])[1].split(', '))
        for name, body in j.items():
            if name in ('guard', 'controller', 'cleanup', 'models-signer', 'publisher'):
                continue
            condition = re.search(r'    if: ([^\n]*(?:\n      [^\n]*)*)\n', body)
            self.assertNotIn("'models-sign'", condition[1] if condition else '', name)
        self.assertIn('host-models-sign.py check-receipt', j['guard'])
        self.assertIn('[[ "$MODE" != models-sign || "$CANDIDATE_SHA" == "$GITHUB_SHA" ]]', j['guard'])

    def test_actual_ci_runs_this_suite(self):
        ci = (ROOT/'.github/workflows/release-verification.yml').read_text()
        self.assertIn('          python3 scripts/tests/test_models_apk_signing.py\n', ci)
        self.assertIn('python3 scripts/tests/test_models_apk_signing.py\n', (ROOT/'scripts/test-local.sh').read_text())
        for path in ('scripts/host-models-sign.py', 'scripts/tests/test_models_apk_signing.py', 'os/models-signing/**'):
            self.assertEqual(ci.count(f"      - '{path}'\n"), 2, path)


if __name__ == '__main__':
    unittest.main()
