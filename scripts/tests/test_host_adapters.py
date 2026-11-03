"""host-sign.py / host-integrate.py with fake gcloud, SDK and recipes: pinned secret versions, owner-only
RAM copies removed on success and failure, archive and pin checks, apps signed before integration."""
import base64
import hashlib
import importlib.util
import io
import json
import contextlib
import os
import re
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import threading
import time
import unittest
from unittest import mock
import zipfile

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
sign_trace = host_sign.sign_trace

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
mkdir bin
# Stand-ins for the otatools signapk (java) and avbtool: they open every key file named on their command line.
printf '#!/bin/bash\nfor a in "$@"; do case $a in *.pem|*.pk8) [[ -r $a ]] || { echo "$0: $a not found" >&2; exit 1; };; esac; done\n' > bin/java
cp bin/java bin/avbtool
chmod +x bin/java bin/avbtool
# reproducible key path for otacerts.zip
ln -s "$KEY_DIR" keys
KEY_DIR=keys
export PATH="$PWD/bin:$PATH"
[[ -f $KEY_DIR/releasekey.x509.pem ]]
# Top-level APK: signapk from this directory, relative keys as releasetools passes them.
java -jar signapk.jar $KEY_DIR/releasekey.x509.pem $KEY_DIR/releasekey.pk8 in.apk out.apk
for arg in --extra_apex_payload_key com.android.mediaprovider.apex="$KEY_DIR/avb.pem"; do
  [[ $arg == --* ]] && continue
  # Like apexer under sign_target_files_apks: the payload key is opened from a temporary working directory.
  (cd "$(mktemp -d)" && openssl pkey -in "${arg#*=}" -noout)
done
[[ -z ${ROSE_TEST_PAUSE:-} ]] || { : > "$ROSE_TEST_PAUSE.ready"; while [[ ! -e $ROSE_TEST_PAUSE.go ]]; do sleep 0.05; done; }
# Like an APK nested in an APEX and the payload re-pack: same relative keys, another working directory.
(cd "$(mktemp -d)" && java -jar signapk.jar $KEY_DIR/releasekey.x509.pem $KEY_DIR/releasekey.pk8 in.apk out.apk)
(cd "$(mktemp -d)" && avbtool add_hashtree_footer --key $KEY_DIR/avb.pem --image payload.img)
# Like WriteOtacerts: the entry name is the key path as given, so it must stay keys/releasekey.x509.pem.
python3 -c 'import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w") as z:
    info = zipfile.ZipInfo(sys.argv[2], (2009, 1, 1, 0, 0, 0))
    z.writestr(info, open(sys.argv[2], "rb").read())' "$DEVICE-ota_update-$BUILD.zip" $KEY_DIR/releasekey.x509.pem
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
        shm_before = set(os.listdir('/dev/shm'))
        self.assertEqual(host_sign.main(self.config, self.env, self.gcloud), 0)
        public = self.release/'public'
        # Nested signers ran from other directories, yet the certificate archive keeps the reproducible entry name.
        with zipfile.ZipFile(public/'frankel-ota_update-2026102802.zip') as otacerts:
            self.assertEqual([(i.filename, i.date_time) for i in otacerts.infolist()],
                [('keys/releasekey.x509.pem', (2009, 1, 1, 0, 0, 0))])
            self.assertEqual(otacerts.read('keys/releasekey.x509.pem'), self.cert)
        # RAM key directory, release-local keys link and the key-path wrappers are all gone.
        self.assertEqual(set(os.listdir('/dev/shm')) - shm_before, set())
        self.assertFalse(os.path.lexists(self.src/'releases/2026102802/release-frankel-2026102802/keys'))
        self.assertFalse((self.release/'key-path-tools').exists())
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

    def trace(self):
        return [json.loads(line) for line in (self.release/'sign-trace.jsonl').read_text().splitlines()]

    def hold_until_traced(self, pause, then=lambda: None):
        """Release the synthetic script's pause point once the watcher has sampled the key family present, so a fast
        machine cannot finish the script inside one sampling interval."""
        def release():
            trace = self.release/'sign-trace.jsonl'
            for _ in range(400):
                if Path(f'{pause}.ready').exists() and trace.exists() and '"ramTarget": true' in trace.read_text():
                    break
                time.sleep(0.05)
            then()
            Path(f'{pause}.go').write_text('')
        thread = threading.Thread(target=release)
        thread.start()
        return thread

    def test_trace_shows_the_key_family_readable_at_every_tool_start_and_removed_only_by_the_exit_trap(self):
        pause = self.root/'pause'
        holder = self.hold_until_traced(pause)
        self.assertEqual(host_sign.main(self.config, dict(self.env, ROSE_TEST_PAUSE=str(pause)), self.gcloud), 0)
        holder.join()
        records = self.trace()
        events = [r['event'] for r in records]
        tools = [r for r in records if r['event'] == 'tool']
        self.assertEqual([t['tool'] for t in tools], ['java', 'openssl', 'java', 'avbtool'])
        self.assertTrue(all(t['keys'] and all(t['keys'].values()) for t in tools), tools)
        self.assertEqual(tools[0]['keys'], {'releasekey.x509.pem': True, 'releasekey.pk8': True})
        linked = next(i for i, r in enumerate(records) if r['event'] == 'state' and r['state']['releaseLink'])
        first_lost = next(i for i, r in enumerate(records) if i > linked and r['event'] == 'state' and not r['state']['releaseLink'])
        self.assertLess(events.index('exit-trap'), first_lost)
        self.assertLess(max(i for i, e in enumerate(events) if e == 'tool'), events.index('exit-trap'))
        private_gone = next(i for i, r in enumerate(records) if r['event'] == 'state' and not r['state']['privateDir'])
        self.assertLess(events.index('script-exited'), private_gone)
        text = (self.release/'sign-trace.jsonl').read_text()
        for secret in ('synthetic-passphrase', '/dev/shm', 'generate-release.', str(self.src), 'BEGIN'):
            self.assertNotIn(secret, text)
        self.assertTrue(sign_trace.summary(self.release/'sign-trace.jsonl')['keyFamilyLost']['scriptExiting'])

    def test_external_removal_while_the_script_runs_is_named_in_the_failure_summary(self):
        pause = self.root/'pause'
        link = self.src/'releases/2026102802/release-frankel-2026102802/keys'
        def remove_like_an_external_cleaner():
            shutil.rmtree(os.path.realpath(link))  # e.g. a logind RemoveIPC sweep of /dev/shm
            time.sleep(0.5)
        remover = self.hold_until_traced(pause, remove_like_an_external_cleaner)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaisesRegex(host_sign.AdapterFailed, 'derived release script exited'):
            host_sign.main(self.config, dict(self.env, ROSE_TEST_PAUSE=str(pause)), self.gcloud)
        remover.join()
        summary = json.loads(stderr.getvalue().strip().splitlines()[-1])
        lost = summary['keyFamilyLost']
        self.assertTrue(lost['scriptAlive'])
        self.assertFalse(lost['scriptExiting'])
        self.assertIn('ramTarget', lost['roles'])
        self.assertIn('removeIPC', summary['facts'])
        self.assertEqual(summary['firstUnreadableToolStart']['keys'], {'releasekey.x509.pem': False, 'releasekey.pk8': False})
        events = [r['event'] for r in self.trace()]
        loss = next(i for i, r in enumerate(self.trace()) if r['event'] == 'state' and r['state']['releaseLink'] and not r['state']['ramTarget'])
        self.assertTrue('exit-trap' not in events[:loss], 'the loss happened before the script began to exit')
        self.assert_secrets_gone()

    def test_nested_signers_fail_without_the_key_path_wrappers(self):
        # Regression evidence: without the PATH wrappers the nested signapk call cannot open keys/releasekey.x509.pem.
        original = host_sign.derive_release_script
        def unwrapped(*args, **kwargs):
            return re.sub(r'export PATH=\S*key-path-tools:"\$PATH"\n', '', original(*args, **kwargs))
        host_sign.derive_release_script = unwrapped
        self.addCleanup(setattr, host_sign, 'derive_release_script', original)
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'derived release script exited'):
            host_sign.main(self.config, self.env, self.gcloud)
        self.assert_secrets_gone()

    def test_key_path_wrapper_rewrites_only_key_arguments(self):
        tools = self.root/'fake-tools'
        tools.mkdir()
        (tools/'openssl').write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
        (tools/'openssl').chmod(0o755)
        host_sign.write_key_path_wrappers(self.root/'wrap', '/R/rel/keys')
        result = subprocess.run([str(self.root/'wrap/openssl'), 'pkcs8', '-in', 'keys/releasekey.pk8', '--private_key=keys/a b.pk8',
            'otherkeys/x', 'out/keys/y', '--flag'], capture_output=True, text=True, cwd='/',
            env=dict(os.environ, PATH=f'{self.root/"wrap"}:{tools}:/usr/bin:/bin'))
        self.assertEqual(result.stdout.splitlines(), ['pkcs8', '-in', '/R/rel/keys/releasekey.pk8',
            '--private_key=/R/rel/keys/a b.pk8', 'otherkeys/x', 'out/keys/y', '--flag'])
        missing = subprocess.run([str(self.root/'wrap/avbtool')], capture_output=True, text=True,
            env=dict(os.environ, PATH=f'{self.root/"wrap"}:/nonexistent'))
        self.assertEqual(missing.returncode, 127)

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
            '/R/releases/2026102802/release-frankel-2026102802/keys', '/dev/shm/w')
        derived = derive(RELEASE_SCRIPT)
        self.assertNotIn('img_from_target_files', derived)
        self.assertIn("source /R/script/common.sh", derived)
        self.assertIn('PERSISTENT_KEY_DIR=/dev/shm/k', derived)
        self.assertIn('host-decrypt-keys.py', derived)
        self.assertNotIn('script/decrypt-keys', derived)
        # Only the APEX payload key becomes absolute; KEY_DIR=keys and the certificate paths stay relative.
        self.assertIn('com.android.mediaprovider.apex="/R/releases/2026102802/release-frankel-2026102802/keys/avb.pem"', derived)
        self.assertIn('\nKEY_DIR=keys\n', derived)
        # The key-path wrappers go first on PATH right after KEY_DIR=keys and again after the script's own PATH export.
        self.assertIn('\nKEY_DIR=keys\nexport PATH=/dev/shm/w:"$PATH"\nexport PATH="$PWD/bin:$PATH"\nexport PATH=/dev/shm/w:"$PATH"\n', derived)
        self.assertIn('java -jar signapk.jar $KEY_DIR/releasekey.x509.pem', derived)
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

# The factory tail of the real generate-release.sh (GrapheneOS/script 09d65184), verbatim after img_from_target_files.
FACTORY_TAIL = r"""
BUILD_NUMBER=$BUILD
source device/common/generate-factory-images-common.sh

if [[ $DEVICE == @(yogi|kodiak|grizzly|cubs) ]]; then
    MAX_DOWNLOAD_SIZE=0x20000000
elif [[ $DEVICE == @(rango|mustang|blazer|frankel) ]]; then
    MAX_DOWNLOAD_SIZE=0x10000000
else
    MAX_DOWNLOAD_SIZE=0xf900000
fi

# Second arg to optimize-factory-image is the name of outer zip directory.
# Output zip name defaults to <outer zip dir name>.zip
fastboot -S $MAX_DOWNLOAD_SIZE optimize-factory-image $DEVICE-factory-$BUILD_NUMBER.zip $DEVICE-install-$BUILD_NUMBER

if [[ -f "$KEY_DIR/id_ed25519" ]]; then
    export PATH="$OLD_PATH"
    ssh-keygen -Y sign -n "factory images" -f "$KEY_DIR/id_ed25519" $DEVICE-install-$BUILD_NUMBER.zip
fi
"""

class SignFactoryTest(SignAdapterTest):
    """Opt-in factory stage: synthetic stand-ins for img_from_target_files, the factory images generator and the otatools
    fastboot (no real generator, signer or fastboot runs), plus an id_ed25519 in the key archive and an ssh-keygen on PATH
    that must never run."""
    def setUp(self):
        super().setUp()
        (self.src/'script/generate-release.sh').write_text(RELEASE_SCRIPT + FACTORY_TAIL)
        self.calls = self.root/'calls.log'
        self.fastboot = script(self.root/'fastboot-src', f"""
            import sys, zipfile
            open({str(self.calls)!r}, 'a').write('fastboot ' + ' '.join(sys.argv[1:]) + '\\n')
            with zipfile.ZipFile(sys.argv[-1] + '.zip', 'w') as z: z.writestr(sys.argv[-1] + '/flash-all.sh', 'synthetic')""")
        fastboot_bytes = Path(self.fastboot).read_bytes()
        self.fastboot_sha = hashlib.sha256(fastboot_bytes).hexdigest()
        # Like the otatools unzip: the generator source and bin/fastboot appear in the release directory.
        script(self.tools/'img_from_target_files', f"""
            import os, pathlib, shutil, sys
            open({str(self.calls)!r}, 'a').write('img_from_target_files ' + ' '.join(sys.argv[1:]) + '\\n')
            pathlib.Path(sys.argv[2]).write_bytes(b'img')
            pathlib.Path('device/common').mkdir(parents=True)
            pathlib.Path('device/common/generate-factory-images-common.sh').write_text(
                'printf "factory\\\\n" > "$DEVICE-factory-$BUILD_NUMBER.zip"; echo "generator $DEVICE $BUILD_NUMBER" >> {self.calls}\\n')
            shutil.copy({self.fastboot!r}, 'bin/fastboot'); os.chmod('bin/fastboot', 0o755)""")
        script(self.tools/'ssh-keygen', f"open({str(self.calls)!r}, 'a').write('ssh-keygen RAN\\n')")
        self.members.append(('release/keys/frankel/id_ed25519', b'synthetic unbound factory key'))
        key_archive(self.secrets/'KEYS', self.members)
        self.env['PATH'] = f'{self.tools}:' + self.env['PATH']
        self.set_factory({'fastbootSha256': self.fastboot_sha})

    def set_factory(self, factory):
        config = json.loads(self.config.read_text())
        config['host']['sign']['factory'] = factory
        self.config.write_text(json.dumps(config))

    def test_factory_stage_maps_the_install_archive_unsigned_beside_the_unchanged_public_outputs(self):
        self.assertEqual(host_sign.main(self.config, self.env, self.gcloud), 0)
        calls = self.calls.read_text().splitlines()
        self.assertEqual(calls, ['img_from_target_files frankel-target_files.zip factory.zip', 'generator frankel 2026102802',
            'fastboot -S 0x10000000 optimize-factory-image frankel-factory-2026102802.zip frankel-install-2026102802'])
        install = self.release/'factory/frankel-install-2026102802.zip'
        self.assertEqual(zipfile.ZipFile(install).namelist(), ['frankel-install-2026102802/flash-all.sh'])
        self.assertEqual(sorted(os.listdir(self.release/'factory')), ['frankel-install-2026102802.zip'])  # no .sig
        public = sorted(p.name for p in (self.release/'public').iterdir())
        self.assertEqual(public, sorted(['frankel-ota_update-2026102802.zip', 'frankel-stable', 'releasekey.x509.pem',
                                         'avb_pkmd.bin', 'UPDATE.md']))
        receipt = json.loads((self.release/'sign-receipt.json').read_text())
        marker = json.loads((self.src/'releases/2026102802/.rose-actions-signed.json').read_text())
        self.assertEqual(sorted(receipt['outputs']), public)
        self.assertEqual(sorted(marker['outputs']), public)  # recovery's exact five public outputs are unchanged
        record = {'file': 'frankel-install-2026102802.zip', 'sha256': hashlib.sha256(install.read_bytes()).hexdigest(),
                  'size': install.stat().st_size, 'fastbootSha256': self.fastboot_sha, 'signature': None,
                  'signatureBasis': 'unsigned: os/flavors/frankel.json factorySigning principal and publicKey are not bound'}
        self.assertEqual((receipt['factory'], marker['factory']), (record, record))
        self.assertEqual((self.src/'releases/2026102802/factory/frankel-install-2026102802.zip').read_bytes(), install.read_bytes())
        self.assertNotIn('ssh-keygen', self.calls.read_text())
        self.assert_secrets_gone()

    def test_ota_only_without_the_opt_in(self):
        config = json.loads(self.config.read_text())
        del config['host']['sign']['factory']
        self.config.write_text(json.dumps(config))
        self.assertEqual(host_sign.main(self.config, self.env, self.gcloud), 0)
        self.assertNotIn('img_from_target_files', self.calls.read_text() if self.calls.exists() else '')
        self.assertFalse((self.release/'factory').exists())
        self.assertNotIn('factory', json.loads((self.release/'sign-receipt.json').read_text()))

    def test_missing_or_invalid_factory_inputs_refuse_before_any_secret(self):
        for factory, error, pattern in (({}, host_sign.AdapterSetupMissing, None),
                                        ({'fastbootSha256': 'abc'}, host_sign.AdapterFailed, 'must be a sha256'),
                                        ({'fastbootSha256': self.fastboot_sha, 'archiveSigning': {'principal': 'x'}},
                                         host_sign.AdapterFailed, 'takes only fastbootSha256 and signing'),
                                        ({'fastbootSha256': self.fastboot_sha, 'signing': {'project': 'p'}},
                                         host_sign.AdapterFailed, 'factorySigning principal and publicKey are not bound')):
            with self.subTest(factory):
                self.set_factory(factory)
                with self.assertRaises(error) as ctx:
                    host_sign.main(self.config, self.env, self.gcloud)
                if pattern:
                    self.assertIn(pattern, str(ctx.exception))
                else:
                    self.assertEqual(ctx.exception.missing, ['host.sign.factory.fastbootSha256'])
                self.assertFalse(self.log.exists(), 'no secret is read')

    def test_wrong_fastboot_stops_before_optimize_factory_image(self):
        self.set_factory({'fastbootSha256': '0' * 64})
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaisesRegex(host_sign.AdapterFailed, 'derived release script exited'):
            host_sign.main(self.config, self.env, self.gcloud)
        self.assertNotIn('fastboot -S', self.calls.read_text())
        self.assertFalse((self.release/'factory').exists())
        self.assert_secrets_gone()

    def test_generator_output_must_exist_and_stay_unsigned(self):
        script(self.root/'fastboot-src', f"open({str(self.calls)!r}, 'a').write('fastboot made nothing\\n')")
        self.set_factory({'fastbootSha256': hashlib.sha256((self.root/'fastboot-src').read_bytes()).hexdigest()})
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'did not produce frankel-install-2026102802.zip'):
            host_sign.main(self.config, self.env, self.gcloud)
        self.assertFalse((self.src/'releases/2026102802/.rose-actions-signed.json').exists())

    def test_a_signature_beside_the_install_archive_refuses(self):
        script(self.root/'fastboot-src', """
            import sys, zipfile
            with zipfile.ZipFile(sys.argv[-1] + '.zip', 'w') as z: z.writestr('x', 'y')
            open(sys.argv[-1] + '.zip.sig', 'w').write('unexpected signature')""")
        self.set_factory({'fastbootSha256': hashlib.sha256((self.root/'fastboot-src').read_bytes()).hexdigest()})
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'frankel-install-2026102802.zip.sig exists'):
            host_sign.main(self.config, self.env, self.gcloud)
        self.assertFalse((self.release/'factory').exists())

    # ---- FOLD-FACTORY-IDENTITY: signing only behind a bound pin (mock pin, mock ssh-keygen runner, synthetic secrets)
    def bind_pin(self):
        public = 'ssh-ed25519 ' + base64.b64encode(b'\0\0\0\x0bssh-ed25519\0\0\0 ' + bytes(range(32))).decode()
        pin = {'namespace': 'factory images', 'principal': 'roseos-factory-images-rango-v1', 'publicKey': public, 'status': 'bound'}
        original = host_sign.factory_contract
        host_sign.factory_contract = lambda device: ('{device}-install-{build}.zip', pin)
        self.addCleanup(setattr, host_sign, 'factory_contract', original)
        self.sshkeygen = self.root/'ssh-keygen-stand-in'
        self.sshkeygen.write_bytes(b'synthetic stand-in bytes, never executed')
        (self.secrets/'FACTORY_KEY').write_bytes(b'synthetic factory key bytes')
        (self.secrets/'FACTORY_PASS').write_text('SYNTHETIC-factory-passphrase\n')
        self.signing = {'project': 'p', 'keySecret': 'FACTORY_KEY', 'keyVersion': '1', 'passphraseSecret': 'FACTORY_PASS',
                        'passphraseVersion': '1', 'sshKeygen': str(self.sshkeygen),
                        'sshKeygenSha256': hashlib.sha256(self.sshkeygen.read_bytes()).hexdigest()}
        return pin

    def mock_keygen(self, verify='ok'):
        self.keygen_calls = []

        def runner(argv, env, cwd, seconds, stdin_path=None):
            self.keygen_calls.append({'argv': argv, 'env': dict(env), 'keyPresent': (Path(cwd)/'id_ed25519').is_file(),
                                      'helper': Path(env['SSH_ASKPASS']).read_bytes() if 'SSH_ASKPASS' in env else None})
            if argv[1:3] == ['-Y', 'sign']:
                Path(argv[-1] + '.sig').write_text('-----BEGIN SSH SIGNATURE-----\nc3lu\n-----END SSH SIGNATURE-----\n')
                return {'exit': 0, 'category': 'ok', 'outputBytes': 0, 'outputSha256': None}
            return {'exit': 0 if verify == 'ok' else 255, 'category': verify, 'outputBytes': 0, 'outputSha256': None}
        original = host_sign.FACTORY_RUNNER[0]
        host_sign.FACTORY_RUNNER[0] = runner
        self.addCleanup(host_sign.FACTORY_RUNNER.__setitem__, 0, original)

    def test_bound_pin_signs_and_verifies_the_install_archive_with_the_pinned_key(self):
        pin = self.bind_pin()
        self.mock_keygen()
        self.set_factory({'fastbootSha256': self.fastboot_sha, 'signing': self.signing})
        self.assertEqual(host_sign.main(self.config, self.env, self.gcloud), 0)
        sign, verify = self.keygen_calls
        install = 'frankel-install-2026102802.zip'
        self.assertEqual(sign['argv'][:6], [str(self.sshkeygen), '-Y', 'sign', '-n', 'factory images', '-f'])
        self.assertTrue(sign['argv'][-1].endswith(install) and sign['keyPresent'])
        self.assertEqual(sorted(sign['env']), ['LANG', 'PATH', 'SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE'])
        self.assertRegex(sign['helper'].decode(), r'^#!/bin/sh\nexec cat /\S+/passphrase\n$')
        self.assertEqual(verify['argv'][verify['argv'].index('-I') + 1], pin['principal'])
        self.assertEqual(sorted(os.listdir(self.release/'factory')), [install, install + '.sig'])
        record = json.loads((self.release/'sign-receipt.json').read_text())['factory']
        self.assertEqual((record['signature']['principal'], record['signature']['namespace'], record['signature']['publicKey']),
                         (pin['principal'], 'factory images', pin['publicKey']))
        self.assertEqual(record['signatureBasis'], 'signed and verified against os/flavors/frankel.json factorySigning')
        fetched = [json.loads(l)[json.loads(l).index('--secret') + 1] for l in self.log.read_text().splitlines()]
        self.assertEqual(fetched, ['KEYS', 'PASS', 'FACTORY_KEY', 'FACTORY_PASS'])
        self.assert_secrets_gone()  # the factory key and passphrase copies are removed too
        everything = b''.join(p.read_bytes() for p in self.release.rglob('*') if p.is_file())
        self.assertNotIn(b'SYNTHETIC-factory-passphrase', everything)
        self.assertNotIn('SYNTHETIC-factory-passphrase', json.dumps(self.keygen_calls, default=str))
        self.assertNotIn('ssh-keygen RAN', self.calls.read_text())  # the generator's own signing step stays removed

    def test_signing_prerequisites_refuse_before_any_secret(self):
        self.bind_pin()
        self.mock_keygen()
        self.set_factory({'fastbootSha256': self.fastboot_sha})
        with self.assertRaises(host_sign.AdapterSetupMissing) as ctx:
            host_sign.main(self.config, self.env, self.gcloud)
        self.assertIn('host.sign.factory.signing.sshKeygen', ctx.exception.missing)
        for change, pattern in (({'sshKeygenSha256': '0' * 64}, 'not the pinned regular file'),
                                ({'keyVersion': 'latest'}, 'pinned numeric versions'), ({'extra': 'x'}, 'pinned numeric versions')):
            with self.subTest(change):
                self.set_factory({'fastbootSha256': self.fastboot_sha, 'signing': dict(self.signing, **change)})
                with self.assertRaisesRegex(host_sign.AdapterFailed, pattern):
                    host_sign.main(self.config, self.env, self.gcloud)
        self.assertFalse(self.log.exists(), 'no secret is read')
        self.assertEqual(self.keygen_calls, [])

    def test_failed_verification_maps_nothing_and_writes_no_marker(self):
        self.bind_pin()
        self.mock_keygen(verify='nonzero_exit')
        self.set_factory({'fastbootSha256': self.fastboot_sha, 'signing': self.signing})
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'verify_failed'):
            host_sign.main(self.config, self.env, self.gcloud)
        self.assertFalse((self.release/'factory').exists())
        self.assertFalse((self.src/'releases/2026102802/.rose-actions-signed.json').exists())
        self.assertFalse(list((self.src/'releases/2026102802').rglob('*.sig')))
        self.assert_secrets_gone()

    def test_factory_anchor_drift_refuses(self):
        args = ('/c/common.sh', '/k', '/p', '/R/rel/keys', '/R/wrap')
        factory = {'fastbootSha256': '1' * 64, 'installTemplate': '{device}-install-{build}.zip'}
        full = RELEASE_SCRIPT + FACTORY_TAIL
        derived = host_sign.derive_release_script(full, *args, factory=factory)
        self.assertIn("printf '%s  bin/fastboot\\n' " + '1' * 64 + ' | sha256sum --check --status', derived)
        self.assertIn('\nbin/fastboot -S $MAX_DOWNLOAD_SIZE optimize-factory-image', derived)
        self.assertNotIn('ssh-keygen', derived)
        self.assertNotIn('id_ed25519', derived)
        self.assertNotIn('img_from_target_files', host_sign.derive_release_script(full, *args))  # OTA-only cut unchanged
        fastboot_line = 'fastboot -S $MAX_DOWNLOAD_SIZE optimize-factory-image $DEVICE-factory-$BUILD_NUMBER.zip $DEVICE-install-$BUILD_NUMBER\n'
        for name, text in (('no fastboot', full.replace(fastboot_line, '')),
                           ('two fastboot', full.replace(fastboot_line, fastboot_line * 2)),
                           ('no generator', full.replace('source device/common/generate-factory-images-common.sh\n', '')),
                           ('changed ssh block', full.replace('    export PATH="$OLD_PATH"\n', '')),
                           ('extra signing', full + 'ssh-keygen -Y sign -f "$KEY_DIR/id_ed25519" x.zip\n')):
            with self.subTest(name), self.assertRaises(host_sign.AdapterFailed):
                host_sign.derive_release_script(text, *args, factory=factory)


# The OTA-only tests run once, in SignAdapterTest; SignFactoryTest reuses only its fixture.
for _name in [n for n in vars(SignAdapterTest) if n.startswith('test_')]:
    setattr(SignFactoryTest, _name, None)

# ---- FOLD-FACTORY-PACKAGE: packaging-only install archive from a retained signed target-files (mocked runner only)
_spec = importlib.util.spec_from_file_location('host_factory_package', SCRIPTS/'host-factory-package.py')
hfp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hfp)
RELEASE_FIXTURE = SCRIPTS/'tests/fixtures/permission-apex/script/generate-release.sh'
GENERATOR_FIXTURE = SCRIPTS/'tests/fixtures/factory-package/device_common-1f5ba267'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def synthetic_vbmeta(public, auth=64, aux=128, pk_offset=0, magic=b'AVB0', pk_size=None):
    """An AVB vbmeta layout around PUBLIC (random bytes standing in for a public key; no key pair exists)."""
    header = bytearray(256)
    header[:4] = magic
    header[12:28] = auth.to_bytes(8, 'big') + aux.to_bytes(8, 'big')
    header[64:80] = pk_offset.to_bytes(8, 'big') + (len(public) if pk_size is None else pk_size).to_bytes(8, 'big')
    aux_block = bytearray(aux)
    aux_block[pk_offset:pk_offset + len(public)] = public[:max(0, aux - pk_offset)]
    return bytes(header) + bytes(auth) + bytes(aux_block)


def target_files_zip(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        for name, data in members:
            if isinstance(data, zipfile.ZipInfo):
                z.writestr(data, b'x')
            else:
                z.writestr(name, data)
    return buffer.getvalue()


@contextlib.contextmanager
def warnings_ignored():
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        yield


class FactoryPackageTest(unittest.TestCase):
    """No bash, img_from_target_files, generator or fastboot runs: the runner is a mock that checks the derived script
    and the work directory, then writes a stand-in install archive."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.retained = self.root/'releases/2026103001'
        (self.retained/'release-rango-2026103001').mkdir(parents=True)
        self.tf = self.retained/'release-rango-2026103001/rango-target_files.zip'
        self.public = os.urandom(64)
        self.vbmeta = synthetic_vbmeta(self.public)
        self.tf.write_bytes(target_files_zip([('META/misc_info.txt', b'ab_update=true\n'), ('IMAGES/vbmeta.img', self.vbmeta)]))
        self.generator = {n: (GENERATOR_FIXTURE/n).read_bytes() for n in
                          ('clear-factory-images-variables.sh', 'generate-factory-images-common.sh')}
        self.otatools = self.retained/'rango-otatools.zip'
        self.write_otatools()
        self.out = self.root/'package-run-1'
        self.before = {p: p.read_bytes() for p in (self.tf, self.otatools)}
        self.listing = sorted(str(p) for p in self.retained.rglob('*'))
        original = hfp.flavor_pins
        hfp.flavor_pins = lambda device, root=hfp.ROOT: {'install': '{device}-install-{build}.zip',
            'avb': sha(self.public), 'generator': {n: sha(b) for n, b in self.generator.items()}}
        self.addCleanup(setattr, hfp, 'flavor_pins', original)
        self.calls = []

    def write_otatools(self, generator=None, extra=(), link=None):
        with zipfile.ZipFile(self.otatools, 'w') as z:
            for name, data in (generator or self.generator).items():
                z.writestr(f'device/common/{name}', data)
            z.writestr('bin/fastboot', b'synthetic fastboot stand-in, never executed')
            for name, data in extra:
                z.writestr(name, data)
            if link:
                info = zipfile.ZipInfo(link)
                info.external_attr = (0o120777 << 16)
                z.writestr(info, 'target')

    def args(self, **change):
        values = {'device': 'rango', 'build': '2026103001', 'target_files': str(self.tf),
                  'target_files_sha256': sha(self.tf.read_bytes()), 'otatools': str(self.otatools),
                  'otatools_sha256': sha(self.otatools.read_bytes()), 'release_script': str(RELEASE_FIXTURE),
                  'release_script_sha256': sha(RELEASE_FIXTURE.read_bytes()),
                  'fastboot_sha256': 'f18fdc8bf467c4bb361ffc62db5f50ca981aa5f37ffb6cb9fd4ac9ff3c4e47a9', 'out': str(self.out),
                  'lock': str(self.root/'builder.lock')}
        values.update(change)
        return type('Args', (), values)

    def runner(self, category='ok', produce=True, sig=False, mutate=False):
        def run(script, cwd, log):
            self.calls.append({'script': Path(script).read_text(), 'cwd': Path(cwd),
                               'files': sorted(str(p.relative_to(cwd)) for p in Path(cwd).rglob('*') if p.is_file())})
            if mutate:
                self.tf.write_bytes(b'changed')
            if produce:
                (Path(cwd)/'rango-install-2026103001.zip').write_bytes(b'synthetic install archive')
            if sig:
                (Path(cwd)/'rango-install-2026103001.zip.sig').write_text('x')
            return category, (0 if category == 'ok' else 1)
        return run

    def package(self, args=None, **kw):
        return hfp.package(args or self.args(), runner=kw.pop('runner', self.runner()), free=kw.pop('free', lambda p: 200 << 30))

    def assertRetained(self):
        self.assertEqual({p: p.read_bytes() for p in (self.tf, self.otatools)}, self.before)
        self.assertEqual(sorted(str(p) for p in self.retained.rglob('*')), self.listing)

    def test_packages_into_a_fresh_root_and_never_touches_the_retained_release(self):
        receipt = self.package()
        self.assertRetained()
        self.assertEqual(sorted(os.listdir(self.out)), ['avb_pkmd.bin', 'factory-package-receipt.json',
                                                        'rango-install-2026103001.zip'])
        self.assertEqual((self.out/'avb_pkmd.bin').read_bytes(), self.public)  # only the derived public bytes
        call = self.calls[0]
        self.assertEqual(call['script'], hfp._module('hs', SCRIPTS/'host-sign.py').derive_factory_only_script(
            RELEASE_FIXTURE.read_text(), 'rango', '2026103001', self.out/'avb_pkmd.bin', self.args().fastboot_sha256))
        self.assertEqual(receipt['inputs']['avbPkmd'], {'path': str(self.out/'avb_pkmd.bin'), 'sha256': sha(self.public),
            'derivedFrom': {'member': 'IMAGES/vbmeta.img', 'memberSha256': sha(self.vbmeta), 'memberSize': len(self.vbmeta),
                            'targetFiles': sha(self.tf.read_bytes())}})
        self.assertIn('rango-target_files.zip', call['files'])  # a copy inside work/, not the retained path
        self.assertIn('device/common/generate-factory-images-common.sh', call['files'])
        self.assertEqual(call['cwd'], self.out/'work')
        self.assertEqual((receipt['status'], receipt['output']['sha256'], receipt['signature'], receipt['workRemoved']),
                         ('packaged', sha(b'synthetic install archive'), None, True))
        self.assertEqual(json.loads((self.out/'factory-package-receipt.json').read_text())['status'], 'packaged')

    def test_wrong_inputs_or_hashes_refuse_before_any_output(self):
        for change, pattern in (({'target_files_sha256': '0' * 64}, 'hash_mismatch: target-files'),
                                ({'otatools_sha256': '0' * 64}, 'hash_mismatch: otatools'),
                                ({'release_script_sha256': '0' * 64}, 'hash_mismatch: release script'),
                                ({'fastboot_sha256': 'x'}, 'usage_refused'),
                                ({'target_files': 'relative.zip'}, 'input_refused'),
                                ({'target_files': str(self.root/'missing.zip')}, 'input_missing')):
            with self.subTest(change), self.assertRaisesRegex(hfp.Refused, pattern):
                self.package(self.args(**change))
            self.assertFalse(self.out.exists())
        link = self.root/'tf-link.zip'
        link.symlink_to(self.tf)
        with self.assertRaisesRegex(hfp.Refused, 'input_missing'):
            self.package(self.args(target_files=str(link)))
        self.assertEqual(self.calls, [])
        self.assertRetained()

    def test_held_shared_lock_refuses_before_anything_is_written(self):
        import fcntl
        holder = os.open(self.root/'builder.lock', os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaisesRegex(hfp.Refused, 'lock_held'):
            self.package()
        self.assertFalse(self.out.exists())
        self.assertEqual(self.calls, [])
        with self.assertRaisesRegex(hfp.Refused, 'usage_refused: --lock'):
            self.package(self.args(lock='relative.lock'))
        self.assertRetained()

    def test_receipt_records_measured_phase_timings(self):
        timings = self.package()['timings']
        self.assertEqual(sorted(timings), ['generateSeconds', 'recheckSeconds', 'stageSeconds', 'verifySeconds'])
        self.assertTrue(all(isinstance(v, float) and v >= 0 for v in timings.values()))

    def tf_with(self, members):
        self.tf.write_bytes(target_files_zip(members))
        return self.args(target_files_sha256=sha(self.tf.read_bytes()))

    def test_public_avb_key_comes_only_from_the_pinned_target_files_vbmeta(self):
        link = zipfile.ZipInfo('IMAGES/vbmeta.img')
        link.external_attr = 0o120777 << 16
        cases = (
            ('wrong public key', [('IMAGES/vbmeta.img', synthetic_vbmeta(os.urandom(64)))], 'avb_mismatch'),
            ('missing member', [('META/misc_info.txt', b'x')], 'vbmeta_missing'),
            ('duplicate member', [('IMAGES/vbmeta.img', self.vbmeta), ('IMAGES/vbmeta.img', self.vbmeta)], 'vbmeta_missing'),
            ('link member', [('IMAGES/vbmeta.img', link)], 'is not a regular member'),
            ('oversized member', [('IMAGES/vbmeta.img', self.vbmeta + bytes(1 << 20))], 'exceeds'),
            ('not AVB', [('IMAGES/vbmeta.img', synthetic_vbmeta(self.public, magic=b'XXXX'))], 'not an AVB vbmeta'),
            ('short image', [('IMAGES/vbmeta.img', self.vbmeta[:200])], 'not an AVB vbmeta'),
            ('blocks beyond image', [('IMAGES/vbmeta.img', synthetic_vbmeta(self.public)[:300])], 'block sizes exceed'),
            ('key outside aux', [('IMAGES/vbmeta.img', synthetic_vbmeta(self.public, pk_offset=96))], 'outside the auxiliary'),
            ('zero-size key', [('IMAGES/vbmeta.img', synthetic_vbmeta(self.public, pk_size=0))], 'outside the auxiliary'),
            ('unsafe name', [('IMAGES/vbmeta.img', self.vbmeta), ('../escape', b'x')], 'target_files_unsafe'),
            ('absolute name', [('IMAGES/vbmeta.img', self.vbmeta), ('/abs', b'x')], 'target_files_unsafe'),
        )
        for name, members, pattern in cases:
            with self.subTest(name), warnings_ignored():
                args = self.tf_with(members)
                with self.assertRaisesRegex(hfp.Refused, pattern):
                    self.package(args)
                self.assertFalse(self.out.exists(), 'refused before NEW_ROOT exists')
        self.tf.write_bytes(b'not a zip')
        with self.assertRaisesRegex(hfp.Refused, 'target_files_invalid'):
            self.package(self.args(target_files_sha256=sha(b'not a zip')))
        with self.assertRaisesRegex(hfp.Refused, 'hash_mismatch: target-files'):
            self.package(self.args(target_files_sha256='0' * 64))  # the vbmeta is never read from an unpinned file
        self.assertEqual(self.calls, [])

    def test_extraction_matches_the_existing_signing_layout(self):
        spec = importlib.util.spec_from_file_location('signing_job_for_layout', SCRIPTS/'signing-job.py')
        job = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(job)
        for vbmeta in (self.vbmeta, synthetic_vbmeta(os.urandom(520), auth=576, aux=1088, pk_offset=64)):
            self.assertEqual(hfp.avb_public_key(vbmeta), job.avb_public_key(vbmeta))

    def test_output_collisions_and_capacity_refuse(self):
        self.out.mkdir()
        (self.out/'keep').write_text('mine')
        with self.assertRaisesRegex(hfp.Refused, 'output_exists'):
            self.package()
        self.assertEqual((self.out/'keep').read_text(), 'mine')
        inside = self.retained/'release-rango-2026103001/new-run'
        with self.assertRaisesRegex(hfp.Refused, 'inside the directory of rango-target_files.zip'):
            self.package(self.args(out=str(inside)))
        with self.assertRaisesRegex(hfp.Refused, 'capacity_unavailable'):
            self.package(self.args(out=str(self.root/'run-2')), free=lambda p: (128 << 30) - 1)
        self.assertFalse((self.root/'run-2').exists())
        self.assertRetained()

    def test_generator_and_otatools_must_be_the_pinned_plain_files(self):
        changed = dict(self.generator, **{'generate-factory-images-common.sh': self.generator['generate-factory-images-common.sh'] + b'\n# drift\n'})
        for n, (kw, pattern) in enumerate((({'generator': changed}, 'generator_mismatch'),
                                           ({'extra': [('../escape', b'x')]}, 'otatools_unsafe'),
                                           ({'link': 'lib64/liblink.so'}, 'otatools_unsafe'))):
            with self.subTest(n):
                self.write_otatools(**kw)
                out = self.root/f'run-unsafe-{n}'
                with self.assertRaisesRegex(hfp.Refused, pattern):
                    self.package(self.args(otatools_sha256=sha(self.otatools.read_bytes()), out=str(out)))
                receipt = json.loads((out/'factory-package-receipt.json').read_text())
                self.assertEqual((receipt['status'], receipt['workRemoved']), ('failed', True))
                self.assertEqual(sorted(os.listdir(out)), ['avb_pkmd.bin', 'factory-package-receipt.json'])
        self.assertEqual(self.calls, [])

    def test_failed_generation_maps_nothing_and_cleans_up(self):
        for n, (runner, pattern) in enumerate(((self.runner('timeout'), 'ended timeout'),
                                               (self.runner('nonzero_exit'), 'ended nonzero_exit'),
                                               (self.runner('log_overflow'), 'ended log_overflow'),
                                               (self.runner(produce=False), 'output_missing'),
                                               (self.runner(sig=True), 'output_unexpected'),
                                               (self.runner(mutate=True), 'input_changed'))):
            with self.subTest(pattern):
                out = self.root/f'fail-{n}'
                with self.assertRaisesRegex(hfp.Refused, pattern):
                    self.package(self.args(out=str(out)), runner=runner)
                self.assertEqual(sorted(os.listdir(out)), ['avb_pkmd.bin', 'factory-package-receipt.json'])
                self.assertEqual(json.loads((out/'factory-package-receipt.json').read_text())['status'], 'failed')
                if pattern != 'input_changed':
                    self.assertRetained()

    def test_factory_only_script_keeps_exactly_the_variables_and_the_tail(self):
        derive = hfp._module('hs', SCRIPTS/'host-sign.py').derive_factory_only_script
        original = RELEASE_FIXTURE.read_text()
        script = derive(original, 'rango', '2026103001', '/srv/public/avb_pkmd.bin', '1' * 64)
        for kept in ('source device/common/clear-factory-images-variables.sh', 'BOOTLOADER=$(get_radio_image bootloader)',
                     'AVB_PKMD=/srv/public/avb_pkmd.bin', 'img_from_target_files $TARGET_FILES $DEVICE-img-$BUILD_NUMBER.zip',
                     'source device/common/generate-factory-images-common.sh', 'MAX_DOWNLOAD_SIZE=0x10000000',
                     "printf '%s  bin/fastboot\\n' " + '1' * 64 + ' | sha256sum --check --status',
                     'bin/fastboot -S $MAX_DOWNLOAD_SIZE optimize-factory-image'):
            self.assertIn(kept, script)
        for dropped in ('KEY_DIR', 'decrypt-keys', 'sign_target_files_apks', 'ota_from_target_files', 'ssh-keygen',
                        'generate-metadata', 'rm -rf $RELEASE_OUT'):
            self.assertNotIn(dropped, script)
        self.assertTrue(script.endswith('optimize-factory-image $DEVICE-factory-$BUILD_NUMBER.zip $DEVICE-install-$BUILD_NUMBER\n'))
        for name, text in (('no AVB_PKMD', original.replace('AVB_PKMD="$KEY_DIR/avb_pkmd.bin"\n', '')),
                           ('two img', original.replace('img_from_target_files $TARGET_FILES', 'img_from_target_files $TARGET_FILES $DEVICE-img-$BUILD_NUMBER.zip\nimg_from_target_files $TARGET_FILES', 1)),
                           ('no signing anchor', original.replace('sign_target_files_apks ', 'signed_elsewhere ')),
                           ('key in variables', original.replace('AVB_ALGORITHM=SHA256_RSA4096', 'AVB_ALGORITHM=SHA256_RSA4096 # $KEY_DIR'))):
            with self.subTest(name), self.assertRaises(host_sign.AdapterFailed):
                derive(text, 'rango', '2026103001', '/srv/public/avb_pkmd.bin', '1' * 64)
        with self.assertRaises(host_sign.AdapterFailed):
            derive(original, 'rango', '2026103001', 'relative/avb_pkmd.bin', '1' * 64)

    def test_run_script_bounds_and_group_cleanup(self):
        class Proc:
            def __init__(self, data, exits):
                r, w = os.pipe()
                self.stdout, self.pid, self.returncode, self.exits = os.fdopen(r, 'rb'), 4242, None, exits
                os.write(w, data)
                if exits:
                    os.close(w)
                else:
                    self.w = w

            def poll(self):
                return 0 if self.exits else None

            def wait(self, timeout=None):
                if hasattr(self, 'w'):
                    os.close(self.w)
                    del self.w
                self.returncode = 0 if self.exits else -9
                return self.returncode
        log = self.root/'run.log'
        for proc, clock, expected in ((Proc(b'ok\n', True), None, 'ok'), (Proc(b'', False), iter([0, 0] + [10 ** 6] * 9), 'timeout')):
            kills = []
            with mock.patch.object(hfp.os, 'killpg', lambda pid, sig: kills.append(sig)):
                kw = {'clock': (lambda c=clock: next(c))} if clock else {}
                category, _ = hfp.run_script(self.root/'s.sh', self.root, log, popen=lambda argv, **k: proc, **kw)
            self.assertEqual(category, expected)
            self.assertEqual(kills, [hfp.signal.SIGTERM, hfp.signal.SIGKILL])
        with mock.patch.dict(hfp.LIMITS, {'logBytes': 10}), mock.patch.object(hfp.os, 'killpg', lambda pid, sig: None):
            category, _ = hfp.run_script(self.root/'s.sh', self.root, log, popen=lambda argv, **k: Proc(b'x' * 100, False))
        self.assertEqual(category, 'log_overflow')


# ---- FOLD-FIRMWARE-OBSERVE: FBPK firmware-carried partition observation (synthetic containers, no real firmware)
_spec = importlib.util.spec_from_file_location('rango_firmware_observe', SCRIPTS/'rango-firmware-observe.py')
rfo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rfo)
FBPK_FIXTURE = SCRIPTS/'tests/fixtures/fbpack-adevtool-17df3b79'


def fbpk_v2(entries, platform=b'synthetic', pack=b'synthetic-1', header=112, entry_size=104, total=None, version=2):
    """entries: (type, name, payload, product, slotted) or with an explicit (offset, size) as the 6th item."""
    table_end = 112 + len(entries) * 104
    body, rows, cursor = b'', [], table_end
    for entry in entries:
        kind, name, payload, product, slotted = entry[:5]
        offset, size = entry[5] if len(entry) > 5 else (cursor, len(payload))
        if len(entry) <= 5:
            body += payload
            cursor += len(payload)
        rows.append(struct.pack('<I36s40sQQII', kind, name.encode(), product.encode(), offset, size, slotted, 0))
    data_len = table_end + len(body)
    head = b'FBPK' + struct.pack('<III16s64sIIII', version, header, entry_size, platform, pack, 0, 0, len(entries),
                                 data_len if total is None else total)
    return head + b''.join(rows) + body


def fbpk_v1(entries, unknown=0, cut_chain=False):
    blob, at = b'', 84
    parts = []
    for i, (kind, name, payload) in enumerate(entries):
        start = at + 56
        following = start + len(payload) if not (cut_chain and i == 0) else 10 ** 9
        parts.append(struct.pack('<I32sIIIII', kind, name.encode(), unknown, len(payload), 0, following, 0) + payload)
        at = start + len(payload)
    body = b''.join(parts)
    return b'FBPK' + struct.pack('<I64sIII', 1, b'synthetic', 0, len(entries), 84 + len(body)) + body


class FirmwareObserveTest(unittest.TestCase):
    AB = ['abl', 'bl31', 'boot', 'modem', 'system', 'tzsw']

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.parts = {p: os.urandom(64 + i) for i, p in enumerate(('abl', 'bl31', 'tzsw', 'modem'))}
        self.bootloader = fbpk_v2([(0, 'partition-table', b'gpt', '', 0)] +
                                  [(1, p, self.parts[p], 'rango', 1) for p in ('abl', 'bl31', 'tzsw')])
        self.radio = fbpk_v1([(1, 'modem', self.parts['modem'])])

    def tf(self, bootloader=None, radio=None, extra=(), drop=(), ab=None):
        members = [('META/ab_partitions.txt', '\n'.join(ab or self.AB).encode() + b'\n'),
                   ('IMAGES/boot.img', b'b'), ('IMAGES/system.img', b's'),
                   ('RADIO/bootloader.img', self.bootloader if bootloader is None else bootloader),
                   ('RADIO/radio.img', self.radio if radio is None else radio)]
        members += [(f'RADIO/{p}.img', b) for p, b in self.parts.items()]
        members = [m for m in members if m[0] not in drop] + list(extra)
        path = self.root/'tf.zip'
        with warnings_ignored():
            path.write_bytes(target_files_zip(members))
        return path

    def observe(self, path=None, **kw):
        path = path or self.tf(**kw)
        return rfo.observe(path, sha(path.read_bytes()), 'rango', '2026103001', 'synthetic run 1 attempt 1')

    def test_maps_each_container_and_satisfies_the_classifier_contract(self):
        fw = self.observe()
        self.assertEqual((fw['schema'], fw['device'], fw['build'], fw['synthetic']),
                         ('roseos.factory-firmware-observation.v1', 'rango', '2026103001', False))
        self.assertEqual(fw['bootloader']['partitions'], ['abl', 'bl31', 'tzsw'])
        self.assertEqual(fw['radio']['partitions'], ['modem'])
        self.assertEqual((fw['bootloader']['sha256'], fw['radio']['sha256']), (sha(self.bootloader), sha(self.radio)))
        self.assertEqual((fw['withoutImage'], fw['uncarried']), (['abl', 'bl31', 'modem', 'tzsw'], []))
        self.assertEqual((fw['bootloader']['otherEntries'], fw['bootloader']['fbpk']['version'], fw['radio']['fbpk']['version']),
                         (1, 2, 1))
        self.assertEqual(fw['bootloader']['entries'][0]['sha256'], sha(self.parts['abl']))
        # The classifier's own coverage check accepts it when bound to the same container digests.
        fp = hfp._module('factory_package_for_fw', SCRIPTS/'factory-package.py')
        result = {'missing': [], 'problems': [], 'inventory': {}, 'evidence': {}}
        fp.check_firmware_carried(fw, fw['withoutImage'], {'bootloader': sha(self.bootloader), 'radio': sha(self.radio)},
                                  fp.load_flavor('rango'), '2026103001', result, False)
        self.assertEqual((result['missing'], result['problems']), ([], []))
        self.assertEqual(result['inventory']['firmwareCarried'], {'abl': 'bootloader', 'bl31': 'bootloader',
                                                                  'modem': 'radio', 'tzsw': 'bootloader'})

    def test_uncarried_partitions_are_reported_not_padded(self):
        bootloader = fbpk_v2([(1, p, self.parts[p], 'rango', 1) for p in ('abl', 'bl31')])
        fw = self.observe(bootloader=bootloader)
        self.assertEqual(fw['uncarried'], ['tzsw'])
        fp = hfp._module('factory_package_for_fw2', SCRIPTS/'factory-package.py')
        result = {'missing': [], 'problems': [], 'inventory': {}, 'evidence': {}}
        fp.check_firmware_carried(fw, fw['withoutImage'], {'bootloader': sha(bootloader), 'radio': sha(self.radio)},
                                  fp.load_flavor('rango'), '2026103001', result, False)
        self.assertEqual(result['missing'], ['firmware-carried evidence for tzsw'])

    def test_malformed_unsupported_or_ambiguous_inputs_refuse(self):
        p = self.parts
        cases = {
            'raw radio': ({'radio': p['modem']}, 'format_unsupported'),
            'version 3': ({'radio': fbpk_v2([(1, 'modem', p['modem'], '', 0)], version=3)}, 'version 3 is not 1 or 2'),
            'short v2': ({'bootloader': b'FBPK' + struct.pack('<I', 2) + bytes(20)}, 'shorter than the FBPK v2 header'),
            'total size': ({'bootloader': fbpk_v2([(1, 'abl', p['abl'], '', 0)], total=5)}, 'totalSize'),
            'header size': ({'bootloader': fbpk_v2([(1, 'abl', p['abl'], '', 0)], header=100)}, 'header/entry sizes'),
            'entry size': ({'bootloader': fbpk_v2([(1, 'abl', p['abl'], '', 0)], entry_size=96)}, 'header/entry sizes'),
            'out of range': ({'bootloader': fbpk_v2([(1, 'abl', b'', '', 0, (220, 10 ** 6))])}, 'outside the container'),
            'into table': ({'bootloader': fbpk_v2([(1, 'abl', p['abl'], '', 0), (1, 'bl31', b'', '', 0, (0, 8))])}, 'outside the container'),
            'overlap': ({'bootloader': fbpk_v2([(1, 'abl', p['abl'], '', 0), (1, 'bl31', b'', '', 0, (320, 32))])}, 'overlaps another'),
            'product variants': ({'bootloader': fbpk_v2([(1, 'abl', p['abl'], 'rango', 1), (1, 'abl', p['abl'], 'other', 1)])},
                                 'more than one abl entry'),
            'not partition data': ({'bootloader': fbpk_v2([(2, 'abl', p['abl'], '', 0)])}, 'not PartitionData'),
            'in both': ({'radio': fbpk_v1([(1, 'modem', p['modem']), (1, 'abl', p['abl'])])}, 'in both'),
            'bytes differ': ({'bootloader': fbpk_v2([(1, 'abl', os.urandom(64), '', 0)])}, 'entry_mismatch'),
            'no standalone': ({'drop': ('RADIO/tzsw.img',)}, 'has no RADIO/tzsw.img'),
            'v1 reserved': ({'radio': fbpk_v1([(1, 'modem', p['modem'])], unknown=1)}, 'nonzero reserved'),
            'v1 chain': ({'radio': fbpk_v1([(1, 'modem', p['modem']), (0, 'x', b'y')], cut_chain=True)}, 'ends the chain'),
            'missing container': ({'drop': ('RADIO/radio.img',)}, 'member_missing'),
            'duplicate container': ({'extra': [('RADIO/radio.img', b'FBPK')]}, 'member_missing'),
            'unsafe name': ({'extra': [('../escape', b'x')]}, 'target_files_unsafe'),
            'repeated ab': ({'ab': self.AB + ['abl']}, 'repeats a partition'),
        }
        for name, (kw, pattern) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(rfo.Refused, pattern):
                self.observe(**kw)
        with mock.patch.dict(rfo.LIMITS, {'containerBytes': 100}), self.assertRaisesRegex(rfo.Refused, 'exceeds 100 bytes'):
            self.observe()
        path = self.tf()
        with self.assertRaisesRegex(rfo.Refused, 'hash_mismatch'):
            rfo.observe(path, '0' * 64, 'rango', '2026103001', 'x')

    def test_cli_writes_once_or_refuses_with_one_line_and_no_file(self):
        path, out = self.tf(), self.root/'fw.json'
        argv = ['--target-files', str(path), '--target-files-sha256', sha(path.read_bytes()), '--device', 'rango',
                '--build', '2026103001', '--source', 'synthetic', '--out', str(out)]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(rfo.main(argv), 0)
        self.assertEqual(json.loads(out.read_text())['radio']['partitions'], ['modem'])
        for change in (['--out', str(out)], ['--target-files-sha256', '0' * 64, '--out', str(self.root/'fw2.json')]):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(rfo.main(argv[:-2] + change if change[0] == '--out' else argv[:2] + change[:2] + argv[4:-2] + change[2:]), 3)
            self.assertEqual(list(json.loads(stdout.getvalue())), ['refused'])
        self.assertFalse((self.root/'fw2.json').exists())

    def test_format_fixture_is_the_pinned_upstream_source(self):
        provenance = json.loads((FBPK_FIXTURE/'PROVENANCE.json').read_text())
        self.assertEqual(provenance['revision'], '17df3b79a73e67ad68387b35c7d41c85e1d8103c')
        for entry in provenance['files']:
            data = (FBPK_FIXTURE/entry['fixture']).read_bytes()
            self.assertEqual(hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest(), entry['gitBlob'])
            self.assertEqual(sha(data), entry['sha256'])
        source = (FBPK_FIXTURE/'fastboot-pack.ts').read_text()
        for anchor in ("magic !== 'FBPK'", 'case 1:', 'case 2:', 'name: str(b, 36)', 'product: str(b, 40)',
                       'offset: u64(b)', 'PartitionData = 1', 'name: str(b, 32)'):
            self.assertIn(anchor, source)
        self.assertIn('assert(entry.type === EntryType.PartitionData', (FBPK_FIXTURE/'firmware.ts').read_text())
        self.assertEqual(rfo.FORMAT['gitBlob'], provenance['files'][0]['gitBlob'])


# ---- FOLD-ARCHIVE-SIGN: detached signature of a retained install ZIP (mocked secrets and ssh-keygen only)
_spec = importlib.util.spec_from_file_location('factory_archive_sign', SCRIPTS/'factory-archive-sign.py')
fas = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fas)


def openssh_fixture(raw=None, cipher=b'aes256-ctr'):
    """An openssh-key-v1 layout with random bytes for the public key and body (not a key pair); returns (bytes, pub)."""
    raw = os.urandom(32) if raw is None else raw
    s = lambda b: struct.pack('>I', len(b)) + b
    public = s(b'ssh-ed25519') + s(raw)
    blob = (b'openssh-key-v1\0' + s(cipher) + s(b'bcrypt') + s(s(os.urandom(16)) + struct.pack('>I', 100))
            + struct.pack('>I', 1) + s(public) + s(os.urandom(144)))
    body = base64.b64encode(blob).decode()
    text = '-----BEGIN OPENSSH PRIVATE KEY-----\n' + '\n'.join(body[i:i + 70] for i in range(0, len(body), 70)) \
        + '\n-----END OPENSSH PRIVATE KEY-----\n'
    return text.encode(), 'ssh-ed25519 ' + base64.b64encode(public).decode()


class FactoryArchiveSignTest(unittest.TestCase):
    """No secret is read and no ssh-keygen runs: fetch writes byte fixtures, the runner stands in for ssh-keygen."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.retained = self.root/'rango-factory-package-1/package'
        self.retained.mkdir(parents=True)
        self.archive = self.retained/'rango-install-2026103001.zip'
        self.archive.write_bytes(b'synthetic install archive bytes')
        self.before = sorted(os.listdir(self.retained))
        self.tool = self.root/'ssh-keygen-stand-in'
        self.tool.write_bytes(b'stand-in, never executed')
        self.key, self.public = openssh_fixture()
        self.pin = {'namespace': 'factory images', 'principal': 'roseos-factory-images-rango-v1', 'publicKey': self.public,
                    'status': 'bound'}
        self.fetched, self.calls = [], []

    def args(self, **change):
        values = {'device': 'rango', 'build': '2026103001', 'archive': str(self.archive),
                  'archive_sha256': sha(self.archive.read_bytes()), 'project': 'protocols-465723',
                  'key_secret': 'PIXEL_OS_FACTORY_SIGNING_KEY_RANGO_V1', 'key_version': '1',
                  'passphrase_secret': 'PIXEL_OS_FACTORY_SIGNING_PASSPHRASE_RANGO_V1', 'passphrase_version': '1',
                  'ssh_keygen': str(self.tool), 'ssh_keygen_sha256': sha(self.tool.read_bytes()),
                  'lock': str(self.root/'builder.lock'), 'out': str(self.root/'sign-run-1')}
        values.update(change)
        return type('Args', (), values)

    def fetch(self, key=None):
        def fetch(project, name, version, destination):
            self.fetched.append((project, name, version))
            Path(destination).write_bytes((key or self.key) if name.endswith('KEY_RANGO_V1') else b'SYNTHETIC-pass\n')
            os.chmod(destination, 0o600)
            return Path(destination)
        return fetch

    def runner(self, verify='ok', mutate=False):
        def run(argv, env, cwd, seconds, stdin_path=None):
            self.calls.append({'argv': argv, 'env': dict(env), 'stdin': stdin_path})
            if argv[1:3] == ['-Y', 'sign']:
                Path(argv[-1] + '.sig').write_text('-----BEGIN SSH SIGNATURE-----\nc3lu\n-----END SSH SIGNATURE-----\n')
                if mutate:
                    self.archive.write_bytes(b'changed')
                return {'exit': 0, 'category': 'ok', 'outputBytes': 0, 'outputSha256': None}
            return {'exit': 0 if verify == 'ok' else 255, 'category': verify, 'outputBytes': 0, 'outputSha256': None}
        return run

    def sign(self, args=None, pin='bound', **kw):
        signing = self.pin if pin == 'bound' else pin
        return fas.sign(args or self.args(), contract=lambda device: ('{device}-install-{build}.zip', signing),
                        fetch=kw.pop('fetch', self.fetch()), runner=kw.pop('runner', self.runner()), **kw)

    def test_signs_the_retained_archive_without_touching_it(self):
        receipt = self.sign()
        out = self.root/'sign-run-1'
        self.assertEqual(sorted(os.listdir(out)), ['factory-archive-sign-receipt.json', 'rango-install-2026103001.zip.sig'])
        self.assertEqual(sorted(os.listdir(self.retained)), self.before)  # no .sig beside the retained archive
        self.assertEqual(self.archive.read_bytes(), b'synthetic install archive bytes')
        sign, verify = self.calls
        self.assertEqual(sign['argv'][:6], [str(self.tool), '-Y', 'sign', '-n', 'factory images', '-f'])
        self.assertEqual(sign['argv'][-1], str(out/'work/rango-install-2026103001.zip'))
        self.assertEqual(verify['argv'][verify['argv'].index('-I') + 1], 'roseos-factory-images-rango-v1')
        self.assertEqual(self.fetched, [('protocols-465723', 'PIXEL_OS_FACTORY_SIGNING_KEY_RANGO_V1', '1'),
                                        ('protocols-465723', 'PIXEL_OS_FACTORY_SIGNING_PASSPHRASE_RANGO_V1', '1')])
        self.assertEqual((receipt['status'], receipt['workRemoved'], receipt['identity']['publicKey']),
                         ('signed', True, self.public))
        self.assertEqual(receipt['signature']['sha256'], sha((out/'rango-install-2026103001.zip.sig').read_bytes()))
        self.assertNotIn('SYNTHETIC-pass', (out/'factory-archive-sign-receipt.json').read_text())

    def test_unbound_identity_refuses_before_reading_anything(self):
        for flavor in ('rango', 'blazer'):
            with self.subTest(flavor), self.assertRaisesRegex(fas.Refused, 'identity_unbound'):
                fas.sign(self.args(device=flavor), fetch=self.fetch(), runner=self.runner())  # the committed null pins
        with self.assertRaisesRegex(fas.Refused, 'identity_invalid'):
            self.sign(pin=dict(self.pin, status='missing'))
        self.assertEqual((self.fetched, self.calls), ([], []))
        self.assertFalse((self.root/'sign-run-1').exists())

    def test_wrong_inputs_refuse_before_any_secret(self):
        link = self.root/'link.zip'
        link.symlink_to(self.archive)
        other = self.retained/'rango-install-2026103002.zip'
        other.write_bytes(b'x')
        (self.root/'exists').mkdir()
        for change, pattern in (({'archive_sha256': '0' * 64}, 'hash_mismatch: archive'),
                                ({'archive': str(link), 'archive_sha256': sha(self.archive.read_bytes())}, 'input_missing'),
                                ({'archive': str(other), 'archive_sha256': sha(b'x')}, 'archive_refused'),
                                ({'ssh_keygen_sha256': '0' * 64}, 'hash_mismatch: ssh-keygen'),
                                ({'key_version': 'latest'}, 'pinned numeric version'),
                                ({'out': str(self.root/'exists')}, 'output_exists'),
                                ({'out': str(self.retained/'sig')}, 'inside the archive directory'),
                                ({'build': '26'}, 'usage_refused')):
            with self.subTest(change), self.assertRaisesRegex(fas.Refused, pattern):
                self.sign(self.args(**change))
        self.assertEqual((self.fetched, self.calls), ([], []))

    def test_held_lock_refuses_before_any_secret(self):
        import fcntl
        holder = os.open(self.root/'builder.lock', os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaisesRegex(fas.Refused, 'lock_held'):
            self.sign()
        self.assertEqual((self.fetched, self.calls), ([], []))
        self.assertFalse((self.root/'sign-run-1').exists())

    def test_wrong_key_failed_verify_or_changed_archive_leave_no_signature(self):
        other_key, _ = openssh_fixture()
        unencrypted, _ = openssh_fixture(cipher=b'none')
        for n, (kw, pattern, signs) in enumerate(((({'fetch': self.fetch(other_key)}), 'key_mismatch', False),
                                                  (({'fetch': self.fetch(unencrypted)}), 'key_invalid', False),
                                                  (({'runner': self.runner(verify='nonzero_exit')}), 'signature_failed', True),
                                                  (({'runner': self.runner(mutate=True)}), 'input_changed', True))):
            with self.subTest(pattern):
                self.calls.clear()
                self.archive.write_bytes(b'synthetic install archive bytes')
                out = self.root/f'fail-{n}'
                with self.assertRaisesRegex(fas.Refused, pattern):
                    self.sign(self.args(out=str(out), archive_sha256=sha(b'synthetic install archive bytes')), **kw)
                self.assertEqual(sorted(os.listdir(out)), ['factory-archive-sign-receipt.json'])
                self.assertEqual(bool(self.calls), signs)
                self.assertEqual(json.loads((out/'factory-archive-sign-receipt.json').read_text())['status'], 'failed')
                self.assertEqual(sorted(os.listdir(self.retained)), sorted(self.before + ['rango-install-2026103002.zip'])
                                 if (self.retained/'rango-install-2026103002.zip').exists() else self.before)

    def binding(self, **change):
        tool = {'path': str(self.tool), 'sha256': sha(self.tool.read_bytes())}
        value = {'schema': 'roseos.factory-archive-signing-binding.v1', 'device': 'rango', 'build': '2026103001',
                 'project': 'protocols-465723', 'keySecret': 'PIXEL_OS_FACTORY_SIGNING_KEY_RANGO_V1', 'keyVersion': '1',
                 'passphraseSecret': 'PIXEL_OS_FACTORY_SIGNING_PASSPHRASE_RANGO_V1', 'passphraseVersion': '1', 'sshKeygen': tool}
        value.update(change)
        return value

    def test_committed_binding_is_unbound_and_check_exits_4(self):
        committed = json.loads((SCRIPTS.parent/'os/retained-rango/factory-signing.json').read_text())
        # The bootstrap receipt values stay unbound until the key setup; the measured ssh-keygen identity is checked
        # against its committed evidence by test_signing_bootstrap.SshKeygenEvidence.
        self.assertEqual((committed['keyVersion'], committed['passphraseVersion']), (None, None))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(fas.main(['check']), 4)
        report = json.loads(stdout.getvalue())
        self.assertFalse(report['ready'])
        self.assertIn('os/flavors/rango.json factorySigning principal/publicKey (null)', report['missing'])
        self.assertIn('binding.keyVersion (from the bootstrap receipt)', report['missing'])
        bound = lambda device: ('{device}-install-{build}.zip', self.pin)
        self.assertEqual(fas.binding_problems(self.binding(), bound), [])
        for change, missing in (({'keyVersion': None}, 'binding.keyVersion'), ({'passphraseVersion': 'latest'}, 'binding.passphraseVersion'),
                                ({'sshKeygen': {'path': None, 'sha256': None}}, 'binding.sshKeygen'),
                                ({'project': 'X'}, 'binding.project'), ({'schema': 'other'}, 'binding: not')):
            with self.subTest(change):
                self.assertTrue(any(m.startswith(missing) for m in fas.binding_problems(self.binding(**change), bound)))

    def restore_signals(self):
        saved = {each: signal.getsignal(each) for each in fas.factory_signing.STOP_SIGNALS}
        self.addCleanup(lambda: [signal.signal(each, handler) for each, handler in saved.items()])

    def test_stop_on_signals_raises_once_then_ignores(self):
        self.restore_signals()

        class Stop(Exception):
            pass

        def raise_stop(signum):
            raise Stop(signum)
        fas.factory_signing.stop_on_signals(raise_stop)
        with self.assertRaises(Stop):
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(1)
        self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_IGN)  # cleanup is never interrupted again
        self.assertEqual(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
        os.kill(os.getpid(), signal.SIGTERM)  # ignored

    def test_the_cli_installs_the_stop_handlers(self):
        self.restore_signals()
        for each in fas.factory_signing.STOP_SIGNALS:
            signal.signal(each, signal.SIG_DFL)
        with contextlib.redirect_stdout(io.StringIO()):
            fas.main(['check'])
        self.assertTrue(all(callable(signal.getsignal(each)) for each in fas.factory_signing.STOP_SIGNALS))

    def test_a_stop_during_signing_removes_the_secrets_and_the_partial_signature(self):
        self.restore_signals()
        fas.factory_signing.stop_on_signals(fas.stop)
        used = []

        @contextlib.contextmanager
        def private(prefix):
            with fas.host_secrets.private_directory(prefix) as path:
                used.append(path)
                yield path

        def runner(argv, env, cwd, seconds, stdin_path=None):
            if argv[1:3] == ['-Y', 'sign']:
                Path(argv[-1] + '.sig').write_text('partial')
                os.kill(os.getpid(), signal.SIGTERM)  # the outer timeout fires mid-signature
                time.sleep(1)
            return {'exit': 0, 'category': 'ok', 'outputBytes': 0, 'outputSha256': None}
        with self.assertRaisesRegex(fas.Refused, '^interrupted: signal 15'):
            self.sign(private=private, runner=runner)
        out = self.root/'sign-run-1'
        self.assertEqual(len(used), 1)
        self.assertFalse(used[0].exists())  # the RAM directory with the key and passphrase is gone
        self.assertFalse((out/f'{self.archive.name}.sig').exists())
        self.assertFalse((out/'work').exists())
        receipt = json.loads((out/'factory-archive-sign-receipt.json').read_text())
        self.assertEqual((receipt['status'], receipt['refused'], receipt['workRemoved']), ('failed', 'interrupted: signal 15', True))

    def test_secret_fetches_and_archive_hashes_are_bounded(self):
        calls = []
        with mock.patch.object(fas.host_secrets, 'fetch_secret', side_effect=lambda *a, **k: calls.append(k) or Path(a[3])):
            fas.bounded_fetch('protocols-465723', 'PIXEL_OS_FACTORY_SIGNING_KEY_RANGO_V1', '1', self.root/'k')
        self.assertEqual(calls, [{'timeout': fas.BOUNDS['fetchSeconds']}])
        with mock.patch.object(fas.host_secrets.subprocess, 'run', side_effect=subprocess.TimeoutExpired('gcloud', 120)):
            with self.assertRaisesRegex(fas.host_secrets.AdapterFailed, 'timed out reading secret'):
                fas.host_secrets.fetch_secret('protocols-465723', 'PIXEL_OS_FACTORY_SIGNING_KEY_RANGO_V1', '1', self.root/'k',
                                              timeout=120)
        ticks = iter([0, 0, 10 ** 6, 10 ** 6])
        big = self.root/'big.bin'
        big.write_bytes(b'x' * (20 << 20))  # three 8 MiB blocks
        with self.assertRaisesRegex(fas.Refused, '^hash_timeout'):
            fas.sha256_file(big, seconds=60, clock=lambda: next(ticks))
        self.assertEqual(fas.sha256_file(big), sha(big.read_bytes()))  # unbounded callers are unchanged
        self.assertEqual(set(fas.BOUNDS), {'hashSeconds', 'fetchSeconds', 'signSeconds', 'verifySeconds', 'killGraceSeconds'})
        self.assertEqual(fas.BOUNDS['signSeconds'], fas.factory_signing.LIMITS['signSeconds'])

    def test_a_noexec_workspace_refuses_before_any_secret(self):
        bound = lambda device: ('{device}-install-{build}.zip', self.pin)
        fetched = []
        with self.assertRaisesRegex(fas.Refused, '^workspace_noexec'):
            fas.sign_from_binding(self.binding(), str(self.archive), sha(self.archive.read_bytes()),
                                  str(self.root/'builder.lock'), str(self.root/'nx-run'), contract=bound,
                                  fetch=lambda *a: fetched.append(a), runner=self.runner(),
                                  workspace_check=lambda path: 'workspace_noexec: synthetic')
        self.assertEqual(fetched, [])
        self.assertFalse((self.root/'nx-run'/f'{self.archive.name}.sig').exists())

    def test_sign_from_binding_and_cli_refusal_is_one_line(self):
        bound = lambda device: ('{device}-install-{build}.zip', self.pin)
        receipt = fas.sign_from_binding(self.binding(), str(self.archive), sha(self.archive.read_bytes()),
                                        str(self.root/'builder.lock'), str(self.root/'b-run'), contract=bound,
                                        fetch=self.fetch(), runner=self.runner())
        self.assertEqual(receipt['status'], 'signed')
        with self.assertRaisesRegex(fas.Refused, 'binding_unbound'):
            fas.sign_from_binding(self.binding(keyVersion=None), str(self.archive), sha(self.archive.read_bytes()),
                                  str(self.root/'builder.lock'), str(self.root/'c-run'), contract=bound,
                                  fetch=self.fetch(), runner=self.runner())
        self.assertFalse((self.root/'c-run').exists())
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(fas.main(['sign', '--archive', str(self.archive), '--archive-sha256', '0' * 64,
                                       '--lock', str(self.root/'l'), '--out', str(self.root/'o')]), 3)
        self.assertIn('binding_unbound', json.loads(stdout.getvalue())['refused'])
        self.assertFalse((self.root/'o').exists())


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
