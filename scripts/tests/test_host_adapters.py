"""host-sign.py / host-integrate.py with fake gcloud, SDK and recipes: pinned secret versions, owner-only
RAM copies removed on success and failure, archive and pin checks, apps signed before integration."""
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
PY = sys.executable
PIN = 'ab' * 32

def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), SCRIPTS/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    spec.loader.exec_module(module)
    return module

host_sign, host_integrate = load('host-sign'), load('host-integrate')

def script(path, body):
    path.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(body))
    path.chmod(0o755)
    return str(path)

def fake_gcloud(tools, secrets, log):
    # Writes the named secret to --out-file and logs argv (to prove no payload passes through it).
    return script(tools/'gcloud', f'''
        import json, shutil, sys
        a = sys.argv[1:]; open({str(log)!r}, 'a').write(json.dumps(a) + '\\n')
        name, out = a[a.index('--secret') + 1], a[a.index('--out-file') + 1]
        src = __import__('os').path.join({str(secrets)!r}, name)
        if a[:3] != ['secrets', 'versions', 'access'] or a[3] != '1' or not __import__('os').path.exists(src): sys.exit(1)
        shutil.copy(src, out)''')

def key_archive(path, cert_pem, avb, extra=None):
    with tarfile.open(path, 'w:gz') as tar:
        for name, data in [('frankel/releasekey.x509.pem', cert_pem), ('frankel/avb_pkmd.bin', avb),
                           ('frankel/releasekey.pk8', b'synthetic private key'), *(extra or [])]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.tools, self.secrets, self.src, self.release = (self.root/n for n in ('tools', 'secrets', 'src', 'release'))
        for d in (self.tools, self.secrets, self.src, self.release):
            d.mkdir()
        self.log = self.root/'gcloud.log'
        self.gcloud = fake_gcloud(self.tools, self.secrets, self.log)
        cwd = os.getcwd()
        os.chdir(self.src)
        self.addCleanup(os.chdir, cwd)

    def fetched_paths(self):
        return [Path(json.loads(l)[json.loads(l).index('--out-file') + 1]) for l in self.log.read_text().splitlines()]

class SignAdapterTest(Base):
    def setUp(self):
        super().setUp()
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(self.root/'k.pem'),
            '-subj', '/CN=synthetic', '-days', '1', '-out', str(self.root/'cert.pem')], check=True, capture_output=True)
        self.cert = (self.root/'cert.pem').read_bytes()
        self.avb = b'synthetic avb blob'
        key_archive(self.secrets/'KEYS', self.cert, self.avb)
        (self.secrets/'PASS').write_text('synthetic-passphrase')
        release = script(self.tools/'release.py', '''
            import os, sys; keys, passfile, build = sys.argv[1:4]
            assert os.path.exists(os.path.join(keys, 'frankel/releasekey.pk8')) and open(passfile).read()
            os.makedirs('releases/' + build, exist_ok=True); open('releases/keys-path', 'w').write(keys)
            if os.environ.get('FAKE_RELEASE_FAIL'): sys.exit(3)
            for n in ('signed.zip', 'ota.zip', 'stable', 'UPDATE.md', 'SHA256SUMS'): open('releases/' + build + '/' + n, 'w').write(n)
            import shutil; shutil.copy(os.path.join(keys, 'frankel/releasekey.x509.pem'), 'releases/' + build + '/cert.pem')
            shutil.copy(os.path.join(keys, 'frankel/avb_pkmd.bin'), 'releases/' + build + '/avb_pkmd.bin')''')
        der = base64.b64decode(''.join(self.cert.decode().split('-----')[2].split()))
        self.config = self.root/'config.json'
        self.write_config(hashlib.sha256(der).hexdigest(), hashlib.sha256(self.avb).hexdigest(), release)
        self.env = dict(os.environ, BUILD_NUMBER='2026102901', ROSE_RELEASE_DIR=str(self.release))

    def write_config(self, cert_pin, avb_pin, release):
        self.config.write_text(json.dumps({'target': {'device': 'frankel'}, 'identities': {'otaCertificateSha256': cert_pin,
            'avbPublicKeySha256': avb_pin}, 'host': {'sign': {'project': 'p', 'keysSecret': 'KEYS', 'passphraseSecret': 'PASS',
            'secretVersion': '1', 'keyDirectory': 'frankel',
            'releaseCommand': [PY, release, '{keys}', '{passphrase_file}', '{build}'],
            'outputs': {'signedTargetFiles': 'releases/{build}/signed.zip', 'public': {
                '{device}-ota_update-{build}.zip': 'releases/{build}/ota.zip', '{device}-stable': 'releases/{build}/stable',
                'releasekey.x509.pem': 'releases/{build}/cert.pem', 'avb_pkmd.bin': 'releases/{build}/avb_pkmd.bin',
                'UPDATE.md': 'releases/{build}/UPDATE.md', 'SHA256SUMS': 'releases/{build}/SHA256SUMS'}}}}}))

    def assert_secrets_gone(self):
        for path in self.fetched_paths():
            self.assertFalse(path.exists(), path)
        if (self.src/'releases/keys-path').exists():
            self.assertFalse(Path((self.src/'releases/keys-path').read_text()).exists())

    def test_signs_with_pinned_versions_and_leaves_only_public_outputs(self):
        self.assertEqual(host_sign.main(self.config, self.env, self.gcloud), 0)
        self.assertTrue((self.release/'signed-target_files.zip').is_file())
        self.assertEqual(sorted(p.name for p in (self.release/'public').iterdir()), sorted(['frankel-ota_update-2026102901.zip',
            'frankel-stable', 'releasekey.x509.pem', 'avb_pkmd.bin', 'UPDATE.md', 'SHA256SUMS']))
        calls = [json.loads(l) for l in self.log.read_text().splitlines()]
        self.assertEqual([c[3] for c in calls], ['1', '1'])
        self.assertTrue(all('synthetic-passphrase' not in ' '.join(c) for c in calls))
        everything = b''.join(p.read_bytes() for p in self.release.rglob('*') if p.is_file())
        self.assertNotIn(b'synthetic-passphrase', everything)
        self.assertNotIn(b'synthetic private key', everything)
        self.assert_secrets_gone()

    def test_pin_mismatch_refuses_and_cleans_up(self):
        self.write_config(PIN, PIN, json.loads(self.config.read_text())['host']['sign']['releaseCommand'][1])
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'established pin'):
            host_sign.main(self.config, self.env, self.gcloud)
        self.assertFalse((self.release/'public').exists())
        self.assert_secrets_gone()

    def test_unsafe_archive_member_is_refused(self):
        key_archive(self.secrets/'KEYS', self.cert, self.avb, extra=[('../escape', b'x')])
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'unexpected member'):
            host_sign.main(self.config, self.env, self.gcloud)
        self.assert_secrets_gone()

    def test_release_command_failure_still_cleans_up(self):
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'exited 3'):
            host_sign.main(self.config, dict(self.env, FAKE_RELEASE_FAIL='1'), self.gcloud)
        self.assert_secrets_gone()

class IntegrateAdapterTest(Base):
    def setUp(self):
        super().setUp()
        for name, data in (('KEYSTORE', b'synthetic keystore'), ('CONFIG', b'{"keyAlias": "rose"}'),
                           ('STORE', b'synthetic store pass'), ('KEY', b'synthetic key pass')):
            (self.secrets/name).write_bytes(data)
        sdk = self.root/'sdk/build-tools/android-15'
        sdk.mkdir(parents=True)
        apksigner = script(sdk/'apksigner', f'''
            import shutil, sys; a = sys.argv[1:]
            if a[0] == 'sign': shutil.copy(a[-1], a[a.index('--out') + 1])
            else: print('Signer #1 certificate SHA-256 digest: {PIN}')''')
        script(sdk/'zipalign', 'pass')
        aapt = script(self.tools/'aapt', '''
            import sys; pkg, ver = open(sys.argv[-1]).read().split(':')
            print("package: name='%s' versionCode='%s' versionName='x'" % (pkg, ver.strip()))''')
        build = script(self.tools/'build.py', '''
            import os, sys; d = os.path.join(sys.argv[1], 'unsigned'); os.makedirs(d)
            for f, c in (('voice', 'dev.pixel.voice:300'), ('agent', 'dev.pixel.backgroundcontrol:27'), ('calc', 'dev.pixel.calculator.agent:4')):
                open(os.path.join(d, f + '.apk'), 'w').write(c)''')
        reconcile = script(self.tools/'reconcile.py', '''
            import json, os, sys; policy, signed = sys.argv[1:3]
            assert json.load(open(policy))['sdk'] == 37 and len([n for n in os.listdir(signed) if n.endswith('.apk')]) == 3
            open('reconciled', 'w').write('ok')''')
        repo = script(self.tools/'repo', 'print("<manifest/>")')
        self.config = self.root/'config.json'
        self.config.write_text(json.dumps({'identities': {'appSignerSha256': PIN}, 'host': {'sourceRoot': str(self.src),
            'tools': {'aapt': aapt, 'apksigner': apksigner}, 'integrate': {'project': 'p', 'secretVersion': '1', 'sdk': 37,
            'appSecrets': {'keystore': 'KEYSTORE', 'config': 'CONFIG', 'storePassword': 'STORE', 'keyPassword': 'KEY'},
            'appSdkRoot': str(self.root/'sdk'), 'appBuildCommand': [PY, build, '{release_dir}'],
            'unsignedApks': {'voice': '{release_dir}/unsigned/voice.apk', 'agent': '{release_dir}/unsigned/agent.apk',
                'target:dev.pixel.calculator.agent': '{release_dir}/unsigned/calc.apk'},
            'reconcileCommand': [PY, reconcile, '{policy}', '{signed_apps}'], 'repo': repo}}}))
        head = subprocess.run(['git', '-C', str(SCRIPTS.parent), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        self.env = dict(os.environ, ROSE_CANDIDATE_SHA=head, ROSE_RELEASE_DIR=str(self.release), BUILD_NUMBER='2026102901',
            APPS=json.dumps([['dev.pixel.voice', 300], ['dev.pixel.backgroundcontrol', 27], ['dev.pixel.calculator.agent', 4]]))

    def test_apps_signed_before_integration_with_policy_and_resolved_manifest(self):
        self.assertEqual(host_integrate.main(self.config, self.env, self.gcloud), 0)
        policy = json.loads((self.release/'background-policy.json').read_text())
        self.assertEqual((policy['sdk'], policy['voice']['packageName'], policy['targets'][0]['packageName']),
            (37, 'dev.pixel.voice', 'dev.pixel.calculator.agent'))
        self.assertEqual((self.src/'reconciled').read_text(), 'ok')
        self.assertEqual((self.release/'resolved-manifest.xml').read_text().strip(), '<manifest/>')
        self.assertFalse((self.release/'candidate').exists(), 'candidate worktree removed')
        for path in self.fetched_paths():
            self.assertFalse(path.exists())
        self.assertNotIn(b'synthetic store pass', b''.join(p.read_bytes() for p in self.release.rglob('*') if p.is_file()))

    def test_version_mismatch_stops_before_integration(self):
        self.env['APPS'] = json.dumps([['dev.pixel.voice', 299], ['dev.pixel.backgroundcontrol', 27], ['dev.pixel.calculator.agent', 4]])
        with self.assertRaisesRegex(host_integrate.AdapterFailed, 'differs from the dispatch input'):
            host_integrate.main(self.config, self.env, self.gcloud)
        self.assertFalse((self.src/'reconciled').exists())
        for path in self.fetched_paths():
            self.assertFalse(path.exists())

class CheckedInConfigTest(unittest.TestCase):
    def test_checked_in_config_names_every_missing_recipe_input(self):
        for script_name, expected in (('host-integrate.py', 'host.integrate.reconcileCommand'), ('host-sign.py', 'host.sign.releaseCommand')):
            result = subprocess.run([PY, str(SCRIPTS/script_name), '--check'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 4, script_name)
            self.assertIn(expected, json.loads(result.stderr)['missing'])

if __name__ == '__main__':
    unittest.main()
