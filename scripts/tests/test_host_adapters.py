"""host-sign.py / host-integrate.py with fake gcloud, SDK and recipes: pinned secret versions, owner-only
RAM copies removed on success and failure, archive and pin checks, apps signed before integration."""
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
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

def key_archive(path, files):
    with tarfile.open(path, 'w:gz') as tar:
        for name, data in files:
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

# Shape of the real script/generate-release.sh: RAM key directory, a release-local `keys` link with KEY_DIR=keys for
# reproducible certificate paths, then signing whose APEX re-pack opens the payload key from another directory.
RELEASE_SCRIPT = r"""#!/bin/bash
set -o errexit -o nounset -o pipefail
source "$(dirname ${BASH_SOURCE[0]})/common.sh"
[[ $# -eq 2 ]] || exit 1
DEVICE=$1
BUILD=$2
PERSISTENT_KEY_DIR=keys/$DEVICE
RELEASE_OUT=releases/$BUILD/release-$DEVICE-$BUILD
KEY_DIR=$(mktemp -d /dev/shm/generate-release.XXXXXXXXXX)
trap "rm -rf \"$KEY_DIR\" && rm -f \"$PWD/$RELEASE_OUT/keys\"" EXIT
cp "$PERSISTENT_KEY_DIR"/* "$KEY_DIR"
script/decrypt-keys "$KEY_DIR"
openssl pkcs8 -inform DER -nocrypt -in "$KEY_DIR/releasekey.pk8" -out /dev/null
[[ $COMMON_LOADED == yes ]]
rm -rf $RELEASE_OUT
mkdir -p $RELEASE_OUT
cmp releases/$BUILD/$DEVICE-otatools.zip "$OUT_DIR/host/otatools.zip"
cp releases/$BUILD/$DEVICE-target_files.zip "$RELEASE_OUT/$DEVICE-target_files.zip"
cd $RELEASE_OUT
# reproducible key path for otacerts.zip
ln -s "$KEY_DIR" keys
KEY_DIR=keys
[[ -f $KEY_DIR/releasekey.x509.pem ]]
echo "otacerts $KEY_DIR/releasekey.x509.pem" > otacerts.txt
for arg in --extra_apex_payload_key com.android.mediaprovider.apex="$KEY_DIR/avb.pem"; do
  [[ $arg == --* ]] && continue
  # Like apexer under sign_target_files_apks: the key is opened from a temporary working directory.
  (cd "$(mktemp -d)" && openssl pkey -in "${arg#*=}" -noout)
done
echo "ota $BUILD" > "$DEVICE-ota_update-$BUILD.zip"
img_from_target_files "$DEVICE-target_files.zip" factory.zip
"""

class SignAdapterTest(Base):
    def setUp(self):
        super().setUp()
        (self.src/'script').mkdir()
        (self.src/'script/generate-release.sh').write_text(RELEASE_SCRIPT)
        (self.src/'script/common.sh').write_text('COMMON_LOADED=yes\n')
        script(self.src/'script/decrypt-keys', f'open({str(self.root/"interactive-decrypt-ran")!r}, "w")')
        (self.src/'out/host').mkdir(parents=True)
        (self.src/'out/host/otatools.zip').write_bytes(b'otatools package')
        (self.release/'unsigned-target_files.zip').write_bytes(b'unsigned target files')
        keys = self.root/'k'
        keys.mkdir()
        (keys/'pass').write_text('synthetic-passphrase')
        run = lambda *a: subprocess.run(a, check=True, capture_output=True, cwd=keys)
        run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'k.pem', '-subj', '/CN=synthetic', '-days', '1', '-out', 'cert.pem')
        run('openssl', 'pkcs8', '-topk8', '-inform', 'PEM', '-outform', 'DER', '-in', 'k.pem', '-out', 'releasekey.pk8', '-passout', 'file:pass')
        run('openssl', 'pkcs8', '-topk8', '-in', 'k.pem', '-out', 'avb.pem', '-passout', 'file:pass')
        self.cert, self.avb = (keys/'cert.pem').read_bytes(), b'synthetic avb blob'
        self.members = [('release/keys/frankel/releasekey.x509.pem', self.cert), ('release/keys/frankel/avb_pkmd.bin', self.avb),
            ('release/keys/frankel/releasekey.pk8', (keys/'releasekey.pk8').read_bytes()), ('release/keys/frankel/avb.pem', (keys/'avb.pem').read_bytes())]
        key_archive(self.secrets/'KEYS', self.members)
        (self.secrets/'PASS').write_text('synthetic-passphrase')
        der = base64.b64decode(''.join(self.cert.decode().split('-----')[2].split()))
        self.config = self.root/'config.json'
        self.write_config(hashlib.sha256(der).hexdigest(), hashlib.sha256(self.avb).hexdigest())
        self.head = subprocess.run(['git', '-C', str(SCRIPTS.parent), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        self.env = dict(os.environ, BUILD_NUMBER='2026102802', BUILD_DATETIME='1793200000', OUT_DIR='out',
            ROSE_CANDIDATE_SHA=self.head, ROSE_RELEASE_DIR=str(self.release))

    def write_config(self, cert_pin, avb_pin, notes='os/release-notes/{device}-{build}.md'):
        self.config.write_text(json.dumps({'target': {'device': 'frankel'}, 'identities': {'otaCertificateSha256': cert_pin,
            'avbPublicKeySha256': avb_pin}, 'publication': {'channels': {'beta': {'feedChannel': 'stable'}}},
            'host': {'sign': {'project': 'p', 'keysSecret': 'KEYS', 'passphraseSecret': 'PASS', 'secretVersion': '1',
                'otatools': 'host/otatools.zip', 'javaHome': '/usr', 'updateNotes': notes}}}))

    def assert_secrets_gone(self):
        for path in self.fetched_paths():
            self.assertFalse(path.exists(), path)

    def test_signs_with_derived_recipe_and_maps_exact_outputs(self):
        self.assertEqual(host_sign.main(self.config, self.env, self.gcloud), 0)
        public = self.release/'public'
        self.assertEqual(sorted(p.name for p in public.iterdir()), sorted(['frankel-ota_update-2026102802.zip', 'frankel-stable',
            'releasekey.x509.pem', 'avb_pkmd.bin', 'UPDATE.md']))
        self.assertEqual((public/'frankel-stable').read_text(), '2026102802 1793200000 frankel stable\n')
        self.assertEqual((public/'UPDATE.md').read_bytes(), subprocess.run(['git', '-C', str(SCRIPTS.parent), 'show',
            f'{self.head}:os/release-notes/frankel-2026102802.md'], capture_output=True).stdout)
        self.assertEqual((self.release/'signed-target_files.zip').read_bytes(), b'unsigned target files')
        receipt = json.loads((self.release/'sign-receipt.json').read_text())
        self.assertEqual(receipt['keyDirectoryInArchive'], 'release/keys/frankel')
        self.assertEqual(receipt['releaseScriptSha256'], hashlib.sha256(RELEASE_SCRIPT.encode()).hexdigest())
        self.assertNotEqual(receipt['derivedScriptSha256'], receipt['releaseScriptSha256'])
        self.assertFalse((self.root/'interactive-decrypt-ran').exists(), 'script/decrypt-keys must be replaced')
        self.assertEqual([json.loads(l)[3] for l in self.log.read_text().splitlines()], ['1', '1'])
        everything = b''.join(p.read_bytes() for p in self.release.rglob('*') if p.is_file())
        self.assertNotIn(b'synthetic-passphrase', everything)
        self.assertTrue((self.src/'releases/2026102802/.rose-actions-owner.json').is_file())
        self.assert_secrets_gone()

    def test_release_notes_must_come_from_the_candidate_and_name_the_build(self):
        self.write_config(*json.loads(self.config.read_text())['identities'].values(), notes='README.md')
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'do not name build 2026102802'):
            host_sign.main(self.config, self.env, self.gcloud)
        self.write_config(*json.loads(self.config.read_text())['identities'].values(), notes='os/release-notes/absent-{build}.md')
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'has no os/release-notes/absent-2026102802.md'):
            host_sign.main(self.config, self.env, self.gcloud)
        self.assertEqual(self.fetched_paths() if self.log.exists() else [], [])

    def test_foreign_or_completed_release_directory_is_not_touched(self):
        (self.src/'releases/2026102802').mkdir(parents=True)
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'not this task'):
            host_sign.main(self.config, self.env, self.gcloud)
        self.assertEqual(self.fetched_paths() if self.log.exists() else [], [])

    def test_failed_partial_release_moves_aside_only_on_an_explicit_sign_resume(self):
        releases = self.src/'releases/2026102802'
        (releases/'release-frankel-2026102802').mkdir(parents=True)
        (releases/'release-frankel-2026102802/partial').write_text('failed attempt output')
        (releases/'frankel-target_files.zip').write_bytes(b'copied input')
        owner = {'candidate': self.head, 'build': '2026102802', 'device': 'frankel'}
        (releases/'.rose-actions-owner.json').write_text(json.dumps(owner))
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'refusing to overwrite'):
            host_sign.main(self.config, self.env, self.gcloud)
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'failed-sign-<run id>'):
            host_sign.main(self.config, dict(self.env, ROSE_SIGN_RESUME_ARCHIVE='../elsewhere'), self.gcloud)
        self.assertTrue((releases/'release-frankel-2026102802/partial').is_file())
        self.assertEqual(host_sign.main(self.config, dict(self.env, ROSE_SIGN_RESUME_ARCHIVE='failed-sign-77'), self.gcloud), 0)
        archived = self.src/'releases/2026102802.failed-sign-77'
        self.assertEqual((archived/'release-frankel-2026102802/partial').read_text(), 'failed attempt output')
        self.assertEqual((archived/'frankel-target_files.zip').read_bytes(), b'copied input')
        self.assertEqual(json.loads((archived/'.rose-actions-owner.json').read_text()), owner)
        signed = json.loads((releases/'.rose-actions-signed.json').read_text())
        self.assertEqual(signed['candidate'], self.head)
        self.assert_secrets_gone()
        shutil.rmtree(self.release/'public')
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'successfully signed release'):
            host_sign.main(self.config, dict(self.env, ROSE_SIGN_RESUME_ARCHIVE='failed-sign-78'), self.gcloud)
        self.assertFalse((self.src/'releases/2026102802.failed-sign-78').exists())

    def test_sign_resume_never_moves_a_foreign_directory(self):
        (self.src/'releases/2026102802/release-frankel-2026102802').mkdir(parents=True)
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'not this task'):
            host_sign.main(self.config, dict(self.env, ROSE_SIGN_RESUME_ARCHIVE='failed-sign-77'), self.gcloud)
        self.assertFalse((self.src/'releases/2026102802.failed-sign-77').exists())

    def test_key_discovery_requires_one_pinned_directory(self):
        key_archive(self.secrets/'KEYS', self.members + [('other/releasekey.x509.pem', self.cert)])
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'exactly one'):
            host_sign.main(self.config, self.env, self.gcloud)
        self.assert_secrets_gone()
        key_archive(self.secrets/'KEYS', self.members)
        self.write_config(PIN, PIN)
        shutil.rmtree(self.src/'releases')
        shutil.rmtree(self.release/'public')
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'established pin'):
            host_sign.main(self.config, self.env, self.gcloud)
        self.assert_secrets_gone()

    def test_unsafe_archive_member_is_refused(self):
        key_archive(self.secrets/'KEYS', self.members + [('../escape', b'x')])
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'unexpected member'):
            host_sign.main(self.config, self.env, self.gcloud)
        self.assert_secrets_gone()

    def test_release_script_anchors_must_each_occur_once(self):
        derive = lambda text: host_sign.derive_release_script(text, '/R/script/common.sh', '/dev/shm/k', '/dev/shm/p',
            '/R/releases/2026102802/release-frankel-2026102802/keys')
        derived = derive(RELEASE_SCRIPT)
        self.assertNotIn('img_from_target_files', derived)
        self.assertIn("source /R/script/common.sh", derived)
        self.assertIn('PERSISTENT_KEY_DIR=/dev/shm/k', derived)
        self.assertIn('host-decrypt-keys.py', derived)
        self.assertNotIn('script/decrypt-keys', derived)
        # Only the APEX payload key becomes absolute; KEY_DIR=keys and the certificate paths stay relative.
        self.assertIn('com.android.mediaprovider.apex="/R/releases/2026102802/release-frankel-2026102802/keys/avb.pem"', derived)
        self.assertIn('\nKEY_DIR=keys\n', derived)
        self.assertIn('echo "otacerts $KEY_DIR/releasekey.x509.pem"', derived)
        for text, message in ((RELEASE_SCRIPT.replace('="$KEY_DIR/avb.pem"', '="$OTHER/avb.pem"'), 'does not use'),
                              (RELEASE_SCRIPT.replace('--extra_apex_payload_key ', '--other '), 'found 0 times'),
                              (RELEASE_SCRIPT.replace('\nKEY_DIR=keys\n', '\n'), 'anchor')):
            with self.assertRaisesRegex(host_sign.AdapterFailed, message):
                derive(text)
        for broken in (RELEASE_SCRIPT.replace('PERSISTENT_KEY_DIR=keys/$DEVICE', 'PERSISTENT_KEY_DIR=keys/$DEVICE\nPERSISTENT_KEY_DIR=keys/$DEVICE'),
                       RELEASE_SCRIPT.replace('img_from_target_files', 'image_from_target'),
                       RELEASE_SCRIPT.replace('script/decrypt-keys', 'script/unlock-keys')):
            with self.assertRaisesRegex(host_sign.AdapterFailed, 'anchor'):
                derive(broken)

    def test_decrypt_helper_refuses_directories_outside_ram(self):
        result = subprocess.run([PY, str(SCRIPTS/'host-decrypt-keys.py'), str(self.root), str(self.root/'k/pass')],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('outside a temporary RAM directory', result.stderr)

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
        self.env = dict(os.environ, ROSE_CANDIDATE_SHA=head, ROSE_RELEASE_DIR=str(self.release), BUILD_NUMBER='2026102802',
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
        self.assertEqual(subprocess.run([PY, str(SCRIPTS/'host-integrate.py'), '--check']).returncode, 0)
        self.assertEqual(subprocess.run([PY, str(SCRIPTS/'host-sign.py'), '--check']).returncode, 0)

if __name__ == '__main__':
    unittest.main()
