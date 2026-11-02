"""host-app-reuse.py (INFRA-APP-118) against a synthetic original os-release attempt: a real release-stage.py receipt chain,
target-files zips holding the Rose Agent preinstall, the signing and verification receipts, the background policy and the
retained signed APK (a ZIP with an APK signing block). aapt and apksigner are stand-ins reading the APK's manifest entry
and printing a configured certificate digest. Nothing is built or signed; the original attempt must stay byte-identical."""
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
PY = sys.executable
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS/'tests'))
from test_app_release_publisher import signing_block  # noqa: E402
import app_release_publisher as publisher  # noqa: E402

def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), SCRIPTS/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

reuse = load('host-app-reuse')
FRANKEL = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
BUILD, STAMP, RUN = '2026102806', '1793220605', 40778491188
def candidate_declaring_308():
    """A commit in this checkout whose Rose Agent manifest declares 308, as the 2806 candidate 93ad1c2b does. The source has
    moved on (AGENT309-DELIVERY-001 declares 309) and CI checkouts are shallow, so this is this checkout's HEAD tree with
    only the manifest's version fields set back to 308: an unreferenced object, never a branch or a release candidate."""
    git = lambda *a, **k: subprocess.run(['git', '-C', str(ROOT), *a], capture_output=True, text=True, check=True, **k).stdout.strip()
    manifest = git('show', 'HEAD:workspace/rose-agent/app/AndroidManifest.xml')
    declared = re.sub(r'android:versionCode="\d+" android:versionName="[^"]*"', 'android:versionCode="308" android:versionName="308-home-tray-beta"', manifest, count=1)
    if declared == manifest:
        return git('rev-parse', 'HEAD')
    blob = git('hash-object', '-w', '--stdin', input=declared + '\n')
    with tempfile.TemporaryDirectory() as temp:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(temp, 'index'))
        git('read-tree', 'HEAD', env=env)
        git('update-index', '--cacheinfo', f'100644,{blob},workspace/rose-agent/app/AndroidManifest.xml', env=env)
        tree = git('write-tree', env=env)
    return git('-c', 'user.name=test', '-c', 'user.email=test@invalid', 'commit-tree', tree, '-m', 'test-only candidate declaring 308',
        env=dict(os.environ, GIT_AUTHOR_DATE='2026-10-28T00:00:00Z', GIT_COMMITTER_DATE='2026-10-28T00:00:00Z'))

HEAD = candidate_declaring_308()
CERT = b'synthetic DER app certificate'
SIGNER = hashlib.sha256(CERT).hexdigest()
PREINSTALL = 'PRODUCT/app/RosePreinstall_dev_pixel_voice/RosePreinstall_dev_pixel_voice.apk'
DELIVERED_308 = 'f4163ca86cd836861206139be74cc2cbe6a82f644bc8afd5533ab1dc6c6e7a41'

def apk(identity='dev.pixel.voice:308:308-home-tray-beta', libs=(), certificate=CERT):
    """An APK-shaped ZIP (manifest entry read by the stand-in aapt) with a v2 signing block for `certificate`."""
    import io, struct
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(zipfile.ZipInfo('AndroidManifest.xml', (2009, 1, 1, 0, 0, 0)), identity)
        for lib in libs:
            archive.writestr(zipfile.ZipInfo(lib, (2009, 1, 1, 0, 0, 0)), b'\x7fELF synthetic')
    data = buffer.getvalue()
    eocd = data.rfind(b'PK\x05\x06')
    directory = struct.unpack_from('<I', data, eocd + 16)[0]
    inserted = signing_block(certificate)
    tail = bytearray(data[directory:])
    struct.pack_into('<I', tail, eocd - directory + 16, directory + len(inserted))
    return data[:directory] + inserted + bytes(tail)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def script(path, body):
    path.write_text('#!' + PY + '\n' + textwrap.dedent(body))
    path.chmod(0o755)
    return str(path)

def stage(state, *args):
    result = subprocess.run([PY, str(SCRIPTS/'release-stage.py'), *map(str, args)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result

def target_files(path, preinstall, presigned=True):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('SYSTEM/build.prop', f'ro.build.version.incremental={BUILD}\n')
        if preinstall is not None:
            z.writestr(PREINSTALL, preinstall)
        z.writestr('META/apkcerts.txt', f'name="RosePreinstall_dev_pixel_voice.apk" certificate="{"PRESIGNED" if presigned else "build/make/target/product/security/platform.x509.pem"}" private_key=""\n')
    return sha(path.read_bytes())

class AppReuseTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        tools = self.root/'tools'
        tools.mkdir()
        self.verify_cert = self.root/'verify-cert'
        self.verify_cert.write_text(SIGNER)
        apksigner = script(tools/'apksigner', f'''
            import sys
            open({str(self.root/'apksigner.log')!r}, 'a').write(sys.argv[-1] + '\\n')
            print('Signer #1 certificate SHA-256 digest: ' + open({str(self.verify_cert)!r}).read())''')
        aapt = script(tools/'aapt', '''
            import re, sys, zipfile; z = zipfile.ZipFile(sys.argv[-1]); pkg, ver, name = z.read('AndroidManifest.xml').decode().split(':')
            print("package: name='%s' versionCode='%s' versionName='%s'" % (pkg, ver, name))
            print("sdkVersion:'29'")
            abis = sorted({n.split('/')[1] for n in z.namelist() if re.fullmatch(r'lib/[^/]+/.+\\.so', n)})
            if abis: print('native-code: ' + ' '.join("'%s'" % a for a in abis))''')
        config = json.loads(json.dumps(FRANKEL))
        config['identities']['appSignerSha256'] = SIGNER
        self.lock = self.root/'frankel-build.lock'
        config['host'].update(sourceRoot=str(self.root/'src'), lockPath=str(self.lock))
        config['host']['tools'].update(aapt=aapt, apksigner=apksigner)
        # The reservation names the candidate; HEAD here is a commit declaring 308 like 93ad1c2b, always present.
        row = next(r for r in config['appRelease']['reserved'] if r['versionCode'] == 308)
        self.assertEqual((row['candidate'], row['osBuild'], row['buildTimestamp']), ('93ad1c2bd7094d6b65c695ba239d05d272b87c57', BUILD, int(STAMP)))
        row['candidate'] = HEAD
        # 308 is delivered: the reservation pins the delivered bytes. The synthetic APK's digest stands in for them here.
        self.assertEqual((row['sha256'], row['delivered']['publicRelease']), (DELIVERED_308, True))
        self.apk = apk()
        row['sha256'] = sha(self.apk)
        self.config_data = config
        self.config = self.root/'frankel.json'
        self.config.write_text(json.dumps(config))
        self.attempts = self.root/'rose-actions'
        self.binding = self.make_attempt()

    def make_attempt(self, build=BUILD, candidate=None, stages=reuse.COMPLETED, retained=None, preinstall=None, policy=None,
                     presigned=True, simulated=False, verified_timestamp=STAMP):
        base = self.attempts/f'frankel-{build}'
        release = base/'release'
        (release/'signed-apps').mkdir(parents=True)
        (release/'public').mkdir()
        stage(base, 'init', '--state', base/'stages', '--source-revision', candidate or HEAD, '--device', 'frankel', '--build-number', build)
        for name in stages:
            stage(base, 'run', '--state', base/'stages', '--source-revision', candidate or HEAD, name, '--', 'true')
        retained = self.apk if retained is None else retained
        (release/'signed-apps/dev.pixel.voice.apk').write_bytes(retained)
        preinstall = retained if preinstall is None else preinstall
        unsigned = target_files(release/'unsigned-target_files.zip', preinstall, presigned)
        signed = target_files(release/'signed-target_files.zip', preinstall, presigned)
        (release/'sign-receipt.json').write_text(json.dumps({'receiptType': 'os-signing', 'candidate': candidate or HEAD, 'build': build,
            'outputs': {}, 'signedTargetFilesSha256': signed}))
        (release/'public/verification.json').write_text(json.dumps({'receiptType': 'candidate-verification', 'simulated': simulated,
            'candidate': {'sha': candidate or HEAD, 'build': build, 'timestamp': verified_timestamp, 'channel': 'beta'},
            'signedTargetFilesSha256': signed, 'published': False}))
        (release/'background-policy.json').write_text(json.dumps({'schemaVersion': 1, 'sdk': 37,
            'agent': {'packageName': 'dev.pixel.backgroundcontrol', 'certificates': [SIGNER.upper()], 'apkSha256': 'c' * 64},
            'voice': policy or {'packageName': 'dev.pixel.voice', 'certificates': [SIGNER.upper()], 'apkSha256': sha(retained).upper()},
            'targets': []}))
        return {'sourceRun': RUN, 'apkSha256': sha(retained), 'unsignedTargetFilesSha256': unsigned, 'signedTargetFilesSha256': signed}

    def tree(self):
        return {str(p.relative_to(self.attempts)): sha(p.read_bytes()) for p in sorted(self.attempts.rglob('*')) if p.is_file()}

    def run_reuse(self, apps=(('dev.pixel.voice', 308),), binding=None, build=BUILD, stamp=STAMP, sha_=None, artifact='artifact'):
        env = dict(os.environ, CANDIDATE_SHA=sha_ or HEAD, BUILD_NUMBER=build, BUILD_TIMESTAMP=stamp, APPS=json.dumps([list(a) for a in apps]),
            REUSE_BINDING=binding if isinstance(binding, str) else json.dumps(binding or self.binding), ROSE_ATTEMPTS=str(self.attempts))
        return reuse.main(self.root/artifact, self.config, env)

    def refused(self, message, **kwargs):
        before = self.tree()
        with self.assertRaisesRegex(reuse.AdapterFailed, message):
            self.run_reuse(**kwargs)
        self.assertFalse((self.root/kwargs.get('artifact', 'artifact')).exists(), 'no partial artifact')
        self.assertEqual(self.tree(), before, 'the original attempt is only read')

    def test_copies_exactly_the_os_bundled_bytes_and_the_publisher_accepts_them(self):
        before = self.tree()
        receipt = self.run_reuse()
        artifact = self.root/'artifact'
        self.assertEqual(sorted(p.name for p in artifact.iterdir()), ['SHA256SUMS', 'app-release-receipt.json', 'dev.pixel.voice-308.apk'])
        self.assertEqual((artifact/'dev.pixel.voice-308.apk').read_bytes(), self.apk)
        self.assertEqual(self.tree(), before, 'the original attempt is only read')
        self.assertEqual((receipt['versionCode'], receipt['versionName'], receipt['sha256'], receipt['candidate'], receipt['signerSha256']),
            (308, '308-home-tray-beta', sha(self.apk), HEAD, SIGNER))
        self.assertEqual(receipt['source'], {'route': 'os-release-reuse', 'osBuild': BUILD, 'buildTimestamp': int(STAMP), 'sourceRun': RUN,
            'unsignedTargetFilesSha256': self.binding['unsignedTargetFilesSha256'],
            'signedTargetFilesSha256': self.binding['signedTargetFilesSha256'], 'targetFilesPath': PREINSTALL})
        self.assertEqual(receipt['apkFacts'], {'minSdk': 29, 'nativeAbis': []})
        self.assertNotIn('Rose Agent built alone', receipt['checks'])
        self.assertIn("not rebuilt or re-signed: the OS candidate's signed bytes", receipt['checks'])
        self.assertEqual((self.root/'apksigner.log').read_text().split(), [str(self.attempts/f'frankel-{BUILD}/release/signed-apps/dev.pixel.voice.apk')])
        text = json.dumps(receipt)
        for private in (str(self.root), 'rose-actions', 'background-policy'):
            self.assertNotIn(private, text)
        # The hosted publisher's offline checks accept this artifact in reuse mode only, and only on the schema 1 route:
        # a reuse artifact never carries a v4 sidecar, so the checked-in schema 2 (APP-IDSIG-1030-M005) refuses it.
        config = json.loads(self.config.read_text())
        self.assertEqual(config['appRelease']['compatibility']['schemaVersion'], 2)
        with self.assertRaisesRegex(publisher.CheckFailed, "schemaVersion 2 needs this APK's retained v4 signature"):
            publisher.verify_artifact(artifact, config, HEAD, [['dev.pixel.voice', 308]],
                {'build': BUILD, 'timestamp': STAMP, 'binding': self.binding})
        config['appRelease']['compatibility']['schemaVersion'] = 1
        checked, _ = publisher.verify_artifact(artifact, config, HEAD, [['dev.pixel.voice', 308]],
            {'build': BUILD, 'timestamp': STAMP, 'binding': self.binding})
        self.assertEqual(checked['sha256'], sha(self.apk))
        with self.assertRaisesRegex(publisher.CheckFailed, 'reserved for the os-release route'):
            publisher.verify_artifact(artifact, config, HEAD, [['dev.pixel.voice', 308]])

    def test_only_the_reserved_os_bundled_version_of_this_candidate_build_and_timestamp(self):
        self.refused('must exceed 307', apps=(('dev.pixel.voice', 307),))
        self.refused('309 is not reserved for an OS candidate', apps=(('dev.pixel.voice', 309),))
        self.refused('305 must exceed 307', apps=(('dev.pixel.voice', 305),))  # an earlier OS candidate's, consumed
        self.refused('exactly', apps=(('dev.pixel.voice', 308), ('dev.pixel.backgroundcontrol', 28)))
        self.refused('exactly', apps=(('dev.pixel.calculator.agent', 308),))
        self.refused(r"reserved for another OS candidate \(differs in \['buildTimestamp'\]\)", stamp='1793220606')
        self.refused(r"differs in \['osBuild'\]", build='2026102807')
        self.refused(r"differs in \['candidate'\]", sha_='0' * 40)

    def test_binding_must_be_explicit_and_match_the_original_outputs(self):
        self.refused('REUSE_BINDING is not JSON', binding='{')
        self.refused('exactly', binding={k: v for k, v in self.binding.items() if k != 'sourceRun'})
        self.refused('positive run id', binding=dict(self.binding, sourceRun='40778491188'))
        self.refused('lowercase sha256', binding=dict(self.binding, apkSha256=self.binding['apkSha256'].upper()))
        self.refused('versionCode 308 was delivered as', binding=dict(self.binding, apkSha256='0' * 64))
        self.refused('unsignedTargetFilesSha256', binding=dict(self.binding, unsignedTargetFilesSha256='0' * 64))
        self.refused('signedTargetFilesSha256', binding=dict(self.binding, signedTargetFilesSha256='0' * 64))

    def fresh(self, **attempt):
        """Replace the original attempt with one changed as given; returns its binding. Retained bytes other than the
        fixture's are re-pinned as the delivered 308 digest, so the deeper checks (not the pin) are what refuse them."""
        import shutil
        shutil.rmtree(self.attempts)
        config = json.loads(json.dumps(self.config_data))
        next(r for r in config['appRelease']['reserved'] if r['versionCode'] == 308)['sha256'] = sha(attempt.get('retained', self.apk))
        self.config.write_text(json.dumps(config))
        return self.make_attempt(**attempt)

    def test_incomplete_or_foreign_attempts_are_refused(self):
        binding = self.fresh(stages=reuse.COMPLETED[:-1])
        self.refused(r"has not completed \['build', 'bytes-verify', 'sign', 'ota-verify', 'transfer'\]", binding=binding)
        binding = self.fresh(stages=())
        self.refused('has not completed', binding=binding)
        binding = self.fresh(candidate='1' * 40)
        self.refused('another candidate or build', binding=binding)
        binding = self.fresh(simulated=True)
        self.refused('no passing, non-simulated verification receipt', binding=binding)
        binding = self.fresh(verified_timestamp='1793220604')
        self.refused('no passing, non-simulated verification receipt', binding=binding)
        (self.attempts/f'frankel-{BUILD}/release/public/verification.json').unlink()
        with self.assertRaises(FileNotFoundError):
            self.run_reuse(binding=binding)
        self.assertFalse((self.root/'artifact').exists())

    def test_an_edited_receipt_chain_is_refused(self):
        receipt = sorted((self.attempts/f'frankel-{BUILD}/stages').glob('*-completed.json'))[0]  # covered by its successor's previousSha256
        receipt.write_text(receipt.read_text().replace('"exitCode": 0', '"exitCode": 0 '))
        self.refused('the attempt receipt chain is not valid: receipt chain broken')

    def test_the_signing_receipt_must_name_the_signed_target_files(self):
        path = self.attempts/f'frankel-{BUILD}/release/sign-receipt.json'
        path.write_text(json.dumps(dict(json.loads(path.read_text()), signedTargetFilesSha256='0' * 64)))
        self.refused('the signing receipt does not name this candidate')

    def test_alternate_308_bytes_are_never_published(self):
        other = apk(libs=('lib/arm64-v8a/libother.so',))
        # Retained bytes that the target-files does not preinstall (the image carries the original 308).
        binding = self.fresh(retained=other, preinstall=self.apk)
        self.refused('preinstalls other Rose Agent bytes than the retained APK', binding=binding)
        # A policy that pins different bytes or another signer than the retained APK.
        binding = self.fresh(policy={'packageName': 'dev.pixel.voice', 'certificates': [SIGNER], 'apkSha256': 'd' * 64})
        self.refused('background policy does not pin the retained APK', binding=binding)
        binding = self.fresh(policy={'packageName': 'dev.pixel.voice', 'certificates': ['E' * 64], 'apkSha256': sha(self.apk)})
        self.refused('background policy does not pin the retained APK', binding=binding)
        binding = self.fresh(presigned=False)
        self.refused('does not mark the Rose Agent preinstall PRESIGNED', binding=binding)
        binding = self.fresh(retained=apk(identity='dev.pixel.voice:308:308-other'))
        self.refused("expected dev.pixel.voice 308 '308-home-tray-beta'", binding=binding)
        binding = self.fresh(retained=apk(identity='dev.pixel.voice:309:308-home-tray-beta'))
        self.refused('the retained APK is dev.pixel.voice 309', binding=binding)

    def test_the_delivered_308_digest_refuses_any_other_bytes_and_other_devices(self):
        # The checked-in pin is the delivered 2806/rose-agent-308 APK: synthetic or rebuilt 308 bytes are refused before
        # the original attempt is read, even with a fully consistent attempt and binding.
        config = json.loads(json.dumps(self.config_data))
        next(r for r in config['appRelease']['reserved'] if r['versionCode'] == 308)['sha256'] = DELIVERED_308
        self.config.write_text(json.dumps(config))
        self.refused(f'versionCode 308 was delivered as {DELIVERED_308}; no other bytes may carry it')
        # Only the config's admitted device is reused; a dispatched device that differs, or a config for a device the
        # delivery chain does not admit, is refused.
        self.config.write_text(json.dumps(self.config_data))
        env = dict(os.environ, CANDIDATE_SHA=HEAD, BUILD_NUMBER=BUILD, BUILD_TIMESTAMP=STAMP, APPS='[["dev.pixel.voice", 308]]',
            REUSE_BINDING=json.dumps(self.binding), ROSE_ATTEMPTS=str(self.attempts))
        with self.assertRaisesRegex(reuse.AdapterFailed, "dispatched device 'blazer' is not the config device frankel"):
            reuse.main(self.root/'artifact', self.config, dict(env, ROSE_DEVICE='blazer'))
        other = json.loads(json.dumps(self.config_data))
        other['target']['device'] = 'blazer'
        self.config.write_text(json.dumps(other))
        with self.assertRaisesRegex(reuse.AdapterFailed, "device 'blazer' is not admitted for app reuse"):
            reuse.main(self.root/'artifact', self.config, env)
        self.assertFalse((self.root/'artifact').exists())
        self.config.write_text(json.dumps(self.config_data))
        self.assertEqual(reuse.main(self.root/'artifact', self.config, dict(env, ROSE_DEVICE='frankel'))['sha256'], sha(self.apk))

    def test_apksigner_runs_again_on_the_retained_bytes(self):
        self.verify_cert.write_text('e' * 64)
        self.refused('not signed by exactly the pinned app signer')

    def test_held_lock_and_existing_artifact_directory_fail_before_reading(self):
        import fcntl
        with open(self.lock, 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.refused('lock is held by another job')
        (self.root/'artifact').mkdir()
        with self.assertRaisesRegex(reuse.AdapterFailed, 'already exists'):
            self.run_reuse()
        self.assertEqual(list((self.root/'artifact').iterdir()), [])
        with self.assertRaisesRegex(reuse.AdapterFailed, 'outside the original attempt'):
            self.run_reuse(artifact=f'rose-actions/frankel-{BUILD}/artifact')

    def test_missing_configuration_fails_closed_by_name(self):
        config = json.loads(self.config.read_text())
        config['host']['tools']['apksigner'] = ''
        del config['appRelease']['reserved']
        self.assertEqual(reuse.missing_inputs(config), ['host.tools.apksigner', 'appRelease.reserved'])
        result = subprocess.run([PY, str(SCRIPTS/'host-app-reuse.py'), '--check'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_source_never_builds_signs_or_reads_secrets(self):
        source = (SCRIPTS/'host-app-reuse.py').read_text()
        for absent in ('fetch_secret', 'sign-apk', 'gcloud', 'host-app-build', 'worktree', 'release-pipeline.py', 'write_once', 'unlink', 'rmtree(base'):
            self.assertNotIn(absent, source)

WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()

class WorkflowAppReuseTest(unittest.TestCase):
    def setUp(self):
        body = WORKFLOW.split('\njobs:\n', 1)[1]
        self.jobs = {m[1]: m[2] for m in __import__('re').finditer(r'^  ([\w-]+):\n((?:(?:    .*)?\n)*)', body, __import__('re').M)}

    def test_reuse_job_reads_the_original_attempt_without_signing_or_cloud_identity(self):
        import re
        job = self.jobs['app-reuse']
        self.assertIn("    needs: controller\n    if: inputs.mode == 'app-reuse'\n    runs-on: [self-hosted, roseos-frankel-builder]\n", job)
        self.assertIn('    permissions:\n      contents: read\n    steps:\n', job)
        runs = re.findall(r'^      (?:- |  )run: (.+)$', job, re.M)
        self.assertEqual(runs, ['python3 scripts/host-app-reuse.py --check', '|',
            'python3 scripts/host-app-reuse.py --artifact-dir "$ROSE_BASE/artifact"', 'test -z "${ROSE_BASE:-}" || rm -rf "$ROSE_BASE"'])
        self.assertIn('base="$HOME/rose-actions/app-reuse-$GITHUB_RUN_ID"', job)
        for absent in ('id-token', 'secrets.', 'PIXEL_OTA', 'GCP_', 'actions-gcp-login', 'os-release-stage', 'release-pipeline.py',
                       'host-app-release', 'sign-apk', 'frankel-$BUILD_NUMBER'):
            self.assertNotIn(absent, job)
        self.assertIn('          name: app-release-dev.pixel.voice-${{ github.run_id }}\n          path: ${{ env.ROSE_BASE }}/artifact\n', job)
        self.assertIn('      - name: Remove the reuse attempt from the builder (the original attempt is untouched)\n        if: always()\n', job)
        self.assertIn('alpha-verify-retained, alpha-export, app-reuse, request-signing, signed-builder, publisher]\n', self.jobs['cleanup'])
        self.assertIn('emulator-alpha-verify-retained, app-reuse, emulator-alpha-export, rango-factory-observe]\n', WORKFLOW)
        self.assertIn('  REUSE_BINDING: ${{ inputs.reuse_binding }}\n', WORKFLOW)
        self.assertIn("    if: inputs.mode == 'os-release' || inputs.mode == 'latest-beta'\n", self.jobs['builder'])
        self.assertIn("    if: inputs.mode == 'app-only'\n", self.jobs['app-builder'])

    def test_guard_admits_only_rose_agent_and_a_well_formed_binding_in_reuse_mode(self):
        import re
        guard = self.jobs['guard']
        self.assertIn('[[ "$APP_PUBLISH" == false || "$MODE" == app-only || "$MODE" == app-reuse ]]', guard)
        self.assertIn('[[ -z "$REUSE_BINDING" || "$MODE" == app-reuse ]]', guard)
        self.assertIn('[[ "$MODE" == os-release || ( "$RESUME_STAGE" == none && -z "$RESUME_SOURCE_RUN" && "$PROMOTE" == false ) ]]', guard)
        check = re.search(r"\[\[ \"\$MODE\" != app-reuse \]\] \|\| python3 -c '(.+)'", guard)[1]
        good = {'sourceRun': RUN, 'apkSha256': 'a' * 64, 'unsignedTargetFilesSha256': 'b' * 64, 'signedTargetFilesSha256': 'c' * 64}
        for apps, binding, ok in (([['dev.pixel.voice', 308]], good, True),
                                  ([['dev.pixel.voice', 308], ['dev.pixel.backgroundcontrol', 28]], good, False),
                                  ([['dev.pixel.calculator.agent', 4]], good, False),
                                  ([['dev.pixel.voice', 308]], dict(good, sourceRun='1'), False),
                                  ([['dev.pixel.voice', 308]], dict(good, sourceRun=0), False),
                                  ([['dev.pixel.voice', 308]], dict(good, apkSha256='A' * 64), False),
                                  ([['dev.pixel.voice', 308]], dict(good, extra='x'), False),
                                  ([['dev.pixel.voice', 308]], {k: v for k, v in good.items() if k != 'apkSha256'}, False)):
            result = subprocess.run([PY, '-c', check], env=dict(os.environ, APPS=json.dumps(apps), REUSE_BINDING=json.dumps(binding)),
                capture_output=True)
            self.assertEqual(result.returncode == 0, ok, (apps, binding))
        result = subprocess.run([PY, '-c', check], env=dict(os.environ, APPS='[["dev.pixel.voice", 308]]', REUSE_BINDING='not json'),
            capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_publisher_gets_the_reuse_arguments_only_in_reuse_mode(self):
        import re
        block = re.search(r'        run: \|\n((?:          .*\n)+)', self.jobs['app-publisher'])[1]
        script = '\n'.join(line[10:] for line in block.splitlines()).replace('python3 scripts/app_release_publisher.py', 'printf "%s\\n"')
        for mode, extra in (('app-only', []), ('app-reuse', ['--reuse-binding', '{"sourceRun": 1}', '--build-number', BUILD,
                                                            '--build-timestamp', STAMP])):
            env = dict(os.environ, MODE=mode, REUSE_BINDING='{"sourceRun": 1}', BUILD_NUMBER=BUILD, BUILD_TIMESTAMP=STAMP,
                RUNNER_TEMP='/t', CANDIDATE_SHA=HEAD, APPS='[["dev.pixel.voice", 308]]')
            out = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True, check=True).stdout.splitlines()
            self.assertEqual(out, ['--artifact-dir', '/t/app-artifact', '--candidate-sha', HEAD, '--apps', '[["dev.pixel.voice", 308]]',
                '--public-dir', '/t/app-public', *extra], mode)

if __name__ == '__main__':
    unittest.main()
