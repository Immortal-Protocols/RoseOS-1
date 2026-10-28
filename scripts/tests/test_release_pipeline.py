"""os-release pipeline with the fake adapter: synthetic target-files, OTA, APKs, throwaway keys and fake
tools exercise the real verification code end to end, plus fail-closed production preflight."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
PIPELINE, STAGE = SCRIPTS/'release-pipeline.py', SCRIPTS/'release-stage.py'
PY = sys.executable
SHA, BUILD, STAMP = '34fa48ff16d046e16c7d680731bf01058d794279', '2026102901', '1793200000'
SIGNER = 'A'*64
APPS = [['dev.pixel.voice', 300], ['dev.pixel.calculator.agent', 4], ['dev.pixel.backgroundcontrol', 27]]

def script(path, body):
    path.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(body))
    path.chmod(0o755)
    return str(path)

def target_files(path, keys, policy_apks, stale_misc=False):
    """Minimal target-files: build props, misc_info AVB fingerprints, presigned APKs, framework-res, vbmeta."""
    fp = f'google/frankel/frankel:17/CP3A.261005.005/{BUILD}:user/{keys}'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('SYSTEM/build.prop', f'ro.build.version.incremental={BUILD}\nro.build.date.utc={STAMP}\n'
            f'ro.build.type=user\nro.product.system.device=frankel\nro.system.build.fingerprint={fp}\nro.build.fingerprint={fp}\n')
        z.writestr('VENDOR/build.prop', f'ro.vendor.build.version.incremental={BUILD}\nro.vendor.build.fingerprint={fp}\n')
        misc_fp = fp.replace(BUILD, '2026102701') if stale_misc else fp
        z.writestr('META/misc_info.txt', f'avb_system_add_hashtree_footer_args=--prop com.android.build.system.fingerprint:{misc_fp}\n'
            f'avb_vendor_add_hashtree_footer_args=--prop com.android.build.vendor.fingerprint:{fp}\n')
        certs = []
        for prefix, name, data in policy_apks:
            z.writestr(prefix + name, data)
            certs.append(f'name="{name}" certificate="PRESIGNED" private_key=""')
        z.writestr('META/apkcerts.txt', '\n'.join(certs) + '\n')
        z.writestr('SYSTEM/framework/framework-res.apk', b'framework')
        z.writestr('IMAGES/vbmeta.img', b'vbmeta')

class PipelineTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = self.root = Path(temp.name)
        src, tools, fixtures = root/'src', root/'tools', root/'fixtures'
        for d in (src, tools, fixtures):
            d.mkdir()
        # Synthetic signed APKs and the policy receipt that pins them.
        apks = {'agent': b'controller apk', 'voice': b'rose agent apk', 'calc': b'calculator apk'}
        policy = {'schemaVersion': 1, 'sdk': 37, 'prototypeControl': False,
            'agent': {'packageName': 'dev.pixel.backgroundcontrol', 'certificates': [SIGNER], 'apkSha256': hashlib.sha256(apks['agent']).hexdigest()},
            'voice': {'packageName': 'dev.pixel.voice', 'certificates': [SIGNER], 'apkSha256': hashlib.sha256(apks['voice']).hexdigest()},
            'targets': [{'packageName': 'dev.pixel.calculator.agent', 'certificates': [SIGNER], 'apkSha256': hashlib.sha256(apks['calc']).hexdigest()}]}
        (fixtures/'policy.json').write_text(json.dumps(policy))
        self.placed = [('SYSTEM_EXT/priv-app/PixelBackgroundControl/', 'PixelBackgroundControl.apk', apks['agent']),
            ('PRODUCT/app/RosePreinstall_dev_pixel_voice/', 'RosePreinstall_dev_pixel_voice.apk', apks['voice']),
            ('PRODUCT/app/RosePreinstall_dev_pixel_calculator_agent/', 'RosePreinstall_dev_pixel_calculator_agent.apk', apks['calc'])]
        # Throwaway OTA certificate and AVB key blob; pins in the fixture config come from them.
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(root/'k.pem'), '-subj',
            '/CN=synthetic', '-days', '1', '-out', str(fixtures/'releasekey.x509.pem')], check=True, capture_output=True)
        pem = (fixtures/'releasekey.x509.pem').read_text()
        cert_pin = hashlib.sha256(base64.b64decode(''.join(pem.split('-----')[2].split()))).hexdigest()
        (fixtures/'avb_pkmd.bin').write_bytes(b'synthetic avb key blob')
        target_files(fixtures/'unsigned.zip', 'test-keys', self.placed)
        target_files(fixtures/'signed.zip', 'release-keys', self.placed)
        with zipfile.ZipFile(fixtures/'ota.zip', 'w') as z:
            z.writestr('META-INF/com/android/metadata', f'post-build=google/frankel/frankel:17/CP3A/{BUILD}:user/release-keys\n'
                f'post-timestamp={STAMP}\npre-device=frankel\n')
        # Lead-managed entry points and tools, replaced by harmless fixtures.
        integrate = script(tools/'integrate.py', f'''
            import os, shutil; d = os.environ['ROSE_RELEASE_DIR']
            open(os.path.join(d, 'resolved-manifest.xml'), 'w').write('<manifest/>')
            shutil.copy({str(fixtures/'policy.json')!r}, os.path.join(d, 'background-policy.json'))''')
        fake_make = script(tools/'make.py', f'''
            import os, shutil; d = os.path.join({str(src)!r}, os.environ['OUT_DIR'], 'target/product/frankel/obj/PACKAGING/target_files_intermediates')
            os.makedirs(d, exist_ok=True); shutil.copy({str(fixtures/'unsigned.zip')!r}, os.path.join(d, 'frankel-target_files.zip'))''')
        sign = script(tools/'sign.py', f'''
            import os, shutil; d = os.environ['ROSE_RELEASE_DIR']; b = os.environ['BUILD_NUMBER']; p = os.path.join(d, 'public')
            shutil.copy({str(fixtures/'signed.zip')!r}, os.path.join(d, 'signed-target_files.zip')); os.makedirs(p)
            shutil.copy({str(fixtures/'ota.zip')!r}, os.path.join(p, 'frankel-ota_update-' + b + '.zip'))
            for n in ('releasekey.x509.pem', 'avb_pkmd.bin'): shutil.copy(os.path.join({str(fixtures)!r}, n), p)
            open(os.path.join(p, 'frankel-stable'), 'w').write(b + ' ' + os.environ['BUILD_DATETIME'] + ' frankel stable\\n')
            open(os.path.join(p, 'UPDATE.md'), 'w').write('# synthetic\\n'); open(os.path.join(p, 'SHA256SUMS'), 'w').write('x\\n')''')
        script(tools/'check_ota.py', 'import os, sys; sys.exit(1 if os.environ.get("FAKE_OTA_BAD") else 0)')
        avb_sha1 = hashlib.sha1(b'synthetic avb key blob').hexdigest()
        script(tools/'avbtool.py', f'import sys; print("Public key (sha1): {avb_sha1}") if sys.argv[1] == "info_image" else None')
        script(tools/'apksigner', f'print("Signer #1 certificate SHA-256 digest: {SIGNER.lower()}")')
        script(tools/'aapt', '''
            import os
            targets = os.environ.get("FAKE_TARGETS", "dev.pixel.calculator.agent:AAAA")
            print("      resource 0x01070001 android:array/config_pixelComputerControlTargets: t=0x10")
            for t in targets.split(","): print('        (string8) "%s"' % t)
            if not os.environ.get("FAKE_NO_PROTOTYPE"):
                print("      resource 0x01070002 android:array/config_pixelComputerControlPrototypeAgents: t=0x10")
                for t in filter(None, os.environ.get("FAKE_PROTOTYPE", "").split(",")): print('        (string8) "%s"' % t)
            print("      resource 0x01050001 android:bool/config_pixelComputerControlLockedSessions: t=0x12 d=0x0 (s=0x0008 r=0x00)")
            print("        (bool) false")''')
        self.config = root/'config.json'
        self.config.write_text(json.dumps({'schemaVersion': 1, 'fixture': True,
            'target': {'device': 'frankel', 'variant': 'user', 'lunch': 'frankel-cur-user'},
            'host': {'sourceRoot': str(src), 'outDir': 'out', 'lockPath': str(root/'build.lock'), 'installclean': True,
                'makeJobs': 2, 'makeGoals': ['target-files-package'], 'integrateCommand': [PY, integrate], 'signCommand': [PY, sign],
                'fakeMake': [PY, fake_make],
                'tools': {'checkOtaSignature': str(tools/'check_ota.py'), 'avbtool': str(tools/'avbtool.py'),
                    'apksigner': str(tools/'apksigner'), 'aapt': str(tools/'aapt')}},
            'identities': {'appSignerSha256': SIGNER.lower(), 'otaCertificateSha256': cert_pin,
                'avbPublicKeySha256': hashlib.sha256(b'synthetic avb key blob').hexdigest()},
            'framework': {'computerControlTargets': ['dev.pixel.calculator.agent'], 'prototypeAgentsEmpty': True, 'lockedSessions': False},
            'publication': {'repository': 'example/none', 'tag': 'frankel-{build}',
                'channels': {'beta': {'promoteLatest': True, 'feedChannel': 'stable'}, 'prod': None}}}))
        self.base = root/'actions'
        self.common = ['--candidate-sha', SHA, '--build-number', BUILD, '--build-timestamp', STAMP, '--channel', 'beta',
            '--release-dir', str(self.base/'release'), '--config', str(self.config), '--adapter', 'fake', '--fake-state', str(root/'fake')]

    def pipeline(self, *args, env=None):
        result = subprocess.run([PY, str(PIPELINE), *map(str, args)], capture_output=True, text=True, env=env, timeout=120)
        return result.returncode, (result.stdout + result.stderr)

    def stage(self, name, command, *extra):
        argv = [PY, str(STAGE), 'run', '--state', str(self.base/'stages'), '--source-revision', SHA, name, '--',
            PY, str(PIPELINE), command, *self.common, *extra]
        return subprocess.run(argv, capture_output=True, text=True, timeout=120)

    def run_builder_job(self):
        subprocess.run([PY, str(STAGE), 'init', '--state', str(self.base/'stages'), '--source-revision', SHA, '--device', 'frankel',
            '--build-number', BUILD], check=True, capture_output=True)
        for name, command in (('build', 'build'), ('bytes-verify', 'verify-build'), ('sign', 'sign'), ('ota-verify', 'verify-signed')):
            result = self.stage(name, command)
            if result.returncode:
                return name, result
        result = self.stage('transfer', 'stage-artifact', '--artifact-dir', str(self.base/'artifact'), '--apps', json.dumps(APPS))
        return ('transfer', result) if result.returncode else (None, result)

    def status(self):
        return json.loads(subprocess.run([PY, str(STAGE), 'status', '--state', str(self.base/'stages')], capture_output=True, text=True).stdout)

    def test_fake_end_to_end_builds_verifies_transfers_and_publishes(self):
        failed, result = self.run_builder_job()
        self.assertIsNone(failed, result.stdout + result.stderr)
        status = self.status()
        self.assertEqual((status['state'], status['stage'], status['builderComputeNeeded']), ('AWAITING_EXTERNAL', 'publish', False))
        artifact = self.base/'artifact'
        self.assertEqual(sorted(p.name for p in (artifact/'assets').iterdir()), sorted([f'frankel-ota_update-{BUILD}.zip',
            'frankel-stable', 'releasekey.x509.pem', 'avb_pkmd.bin', 'SHA256SUMS', 'UPDATE.md', 'verification.json']))
        self.assertTrue(json.loads((artifact/'pipeline-receipt.json').read_text())['simulated'])
        code, out = self.pipeline('publish', *self.common[:8], '--config', self.config, '--artifact-dir', artifact,
            '--adapter', 'fake', '--fake-state', self.root/'fake', '--promote')
        self.assertEqual(code, 0, out)
        self.assertTrue(json.loads(out)['promoted'])
        self.assertTrue((self.root/f'fake/releases/frankel-{BUILD}/latest/verification.json').exists())

    def test_stale_misc_info_fingerprint_stops_before_signing(self):
        target_files(self.root/'fixtures/unsigned.zip', 'test-keys', self.placed, stale_misc=True)
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'bytes-verify')
        self.assertIn('misc_info.txt fingerprint', result.stderr)
        self.assertEqual(self.status()['state'], 'TERMINAL')

    def test_compiled_framework_policy_mismatch_fails(self):
        os.environ['FAKE_TARGETS'] = 'dev.pixel.calculator.agent:AAAA,com.example.other:BBBB'
        self.addCleanup(os.environ.pop, 'FAKE_TARGETS', None)
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'bytes-verify')
        self.assertIn('computer-control targets', result.stderr)

    def test_compiled_prototype_agent_fails(self):
        os.environ['FAKE_PROTOTYPE'] = 'dev.pixel.backgroundcontrol:AAAA'
        self.addCleanup(os.environ.pop, 'FAKE_PROTOTYPE', None)
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'bytes-verify')
        self.assertIn('compiled prototype agents are not empty', result.stderr)

    def test_missing_prototype_array_fails_closed(self):
        os.environ['FAKE_NO_PROTOTYPE'] = '1'
        self.addCleanup(os.environ.pop, 'FAKE_NO_PROTOTYPE', None)
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'bytes-verify')
        self.assertIn('compiled prototype agents not found', result.stderr)

    def test_bad_ota_signature_fails_after_signing(self):
        os.environ['FAKE_OTA_BAD'] = '1'
        self.addCleanup(os.environ.pop, 'FAKE_OTA_BAD', None)
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'ota-verify')
        self.assertIn('OTA package or payload signature verification failed', result.stderr)

    def test_held_lock_refuses_the_build(self):
        import fcntl
        holder = open(self.root/'build.lock', 'a')
        fcntl.flock(holder, fcntl.LOCK_EX)
        self.addCleanup(holder.close)
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'build')
        self.assertIn('lock is held', result.stderr)

    def test_publisher_refuses_simulated_tampered_or_unconfigured(self):
        failed, _ = self.run_builder_job()
        self.assertIsNone(failed)
        artifact = self.base/'artifact'
        base_args = ['publish', *self.common[:8], '--artifact-dir', artifact]
        code, out = self.pipeline(*base_args, '--config', SCRIPTS.parent/'os/release-pipeline/frankel.json')
        self.assertEqual(code, 1)
        self.assertIn('refusing to publish a simulated artifact', out)
        code, out = self.pipeline(*base_args[:7], '--channel', 'prod', '--artifact-dir', artifact, '--config', self.config,
            '--adapter', 'fake', '--fake-state', self.root/'fake')
        self.assertEqual(code, 1, out)  # prod candidate differs from the beta-bound receipt
        with open(artifact/'assets/UPDATE.md', 'a') as f:
            f.write('tampered\n')
        code, out = self.pipeline(*base_args, '--config', self.config, '--adapter', 'fake', '--fake-state', self.root/'fake')
        self.assertEqual(code, 1)
        self.assertIn('verify-published failed', out)
        self.assertFalse((self.root/f'fake/releases/frankel-{BUILD}').exists())

    def test_production_preflight_fails_closed_naming_every_missing_input(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('GCP_', 'ROSEOS_', 'PIXEL_OTA'))}
        code, out = self.pipeline('preflight', '--job', 'guard', env=env)
        self.assertEqual(code, 4)
        missing = json.loads(out)['missing']
        for name in ('ROSEOS_BUILDER_RUNNER_READY', 'GCP_WORKLOAD_IDENTITY_PROVIDER', 'GCP_CONTROLLER_SERVICE_ACCOUNT',
                     'GCP_SIGNER_SERVICE_ACCOUNT', 'host.integrate.reconcileCommand', 'host.sign.releaseCommand', 'host.tools.apksigner'):
            self.assertIn(name, missing)
        self.assertEqual(len(missing), len(set(missing)))
        self.assertTrue(any(m.startswith('PIXEL_OTA_PUBLISHER') for m in missing))
        code, out = self.pipeline('preflight', '--job', 'publisher', env=dict(env, PIXEL_OTA_PUBLISHER='github-app'))
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(out)['missing'], ['PIXEL_OTA_APP_ID', 'PIXEL_OTA_APP_PRIVATE_KEY'])
        code, out = self.pipeline('preflight', '--job', 'publisher', env=dict(env, PIXEL_OTA_PUBLISHER='github-app',
            PIXEL_OTA_APP_ID='123', PIXEL_OTA_APP_KEY_PRESENT='true'))
        self.assertEqual(code, 0, out)
        code, out = self.pipeline('preflight', '--job', 'builder', '--config', self.config)
        self.assertEqual(code, 2)
        self.assertIn('fixture config cannot be used for production', out)

if __name__ == '__main__':
    unittest.main()
