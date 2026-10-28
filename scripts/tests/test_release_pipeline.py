"""os-release pipeline with the fake adapter: synthetic target-files, OTA, APKs, throwaway keys and fake
tools exercise the real verification code end to end, plus fail-closed production preflight."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
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
# Exact nonsecret excerpt of the real dump of build 2026102802's framework-res.apk (sha256 88dc574b...89ef9aa),
# including the spec rows before the values and the unrelated resources after them (ACTIONS068 evidence).
FRAMEWORK_DUMP = '''\
      spec resource 0x01070156 android:array/config_pixelComputerControlPrototypeAgents: flags=0x00000000
      spec resource 0x01070157 android:array/config_pixelComputerControlTargets: flags=0x00000000
      config (default) flags=0x02 [offset16]:
        resource 0x01070156 android:array/config_pixelComputerControlPrototypeAgents: <bag>
          Parent=0x00000000(Resolved=0x00000000), Count=0
        resource 0x01070157 android:array/config_pixelComputerControlTargets: <bag>
          Parent=0x00000000(Resolved=0x00000000), Count=1
          #0 (Key=0x01000001): (string8) "dev.pixel.calculator.agent:650D47F91B7F5AF3C530FEF53FE92018AA4521EFBA065BCB1D6FC9B699060BFB"
      config watch flags=0x01 [sparse]:
      spec resource 0x01110330 android:bool/config_pixelComputerControlLockedSessions: flags=0x00000000
      config (default) flags=0x02 [offset16]:
        resource 0x01110330 android:bool/config_pixelComputerControlLockedSessions: t=0x12 d=0x00000000 (s=0x0008 r=0x00)
          (boolean) false
        resource 0x01110331 android:bool/config_useSystemWallpaperDuringUserSwitch: t=0x12 d=0x00000000 (s=0x0008 r=0x00)
          (boolean) false
'''
APPS = [['dev.pixel.voice', 300], ['dev.pixel.calculator.agent', 4], ['dev.pixel.backgroundcontrol', 27]]
WORKFLOW_TEXT = (SCRIPTS.parent/'.github/workflows/os-release.yml').read_text()
PREPARE = re.search(r'- name: Prepare stage receipts\n\s+run: \|\n((?: {10}.*\n)+)', WORKFLOW_TEXT).group(1)
WORKFLOW_STAGES = re.findall(r'^\s+run: (bash scripts/os-release-stage\.sh .+)$',
    (SCRIPTS.parent/'.github/workflows/os-release.yml').read_text(), re.M)

def script(path, body):
    path.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(body))
    path.chmod(0o755)
    return str(path)

SPARSE = b'\x3a\xff\x26\xed'

def image(files, sparse=False):
    """Stand-in partition image read by the fake debugfs: JSON of path -> base64 bytes (sparse adds the magic)."""
    data = json.dumps({k: base64.b64encode(v).decode() for k, v in files.items()}).encode()
    return SPARSE + data if sparse else data

def ota(path, build, stamp, post_build=None, bad_properties=False):
    """OTA zip with real-format payload.bin header and payload_properties.txt."""
    manifest = b'synthetic manifest' * 4
    header = b'CrAU' + (2).to_bytes(8, 'big') + len(manifest).to_bytes(8, 'big') + (0).to_bytes(4, 'big')
    payload = header + manifest + b'synthetic partition data' * 100
    properties = {'FILE_HASH': base64.b64encode(hashlib.sha256(payload).digest()).decode(), 'FILE_SIZE': str(len(payload)),
        'METADATA_HASH': base64.b64encode(hashlib.sha256(header + manifest).digest()).decode(),
        'METADATA_SIZE': str(len(header) + len(manifest))}
    if bad_properties:
        properties['FILE_SIZE'] = '1'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('META-INF/com/android/metadata', f'post-build={post_build or "google/frankel/frankel:17/CP3A/" + build + ":user/release-keys"}\n'
            f'post-timestamp={stamp}\npre-device=frankel\n')
        z.writestr('payload.bin', payload)
        z.writestr('payload_properties.txt', ''.join(f'{k}={v}\n' for k, v in properties.items()))

def target_files(path, keys, policy_apks, stale_misc=False, image_override=None):
    """Minimal target-files: build props, misc_info AVB fingerprints, presigned APKs, framework-res, images."""
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
        if not os.environ.get('FAKE_NO_BOOT_IMG'):
            z.writestr('IMAGES/boot.img', b'boot')
        embedded = {prefix + name: data for prefix, name, data in policy_apks}
        embedded.update(image_override or {})
        z.writestr('IMAGES/system_ext.img', image({'/' + k[len('SYSTEM_EXT/'):]: v for k, v in embedded.items() if k.startswith('SYSTEM_EXT/')}))
        z.writestr('IMAGES/product.img', image({'/' + k[len('PRODUCT/'):]: v for k, v in embedded.items() if k.startswith('PRODUCT/')}, sparse=True))
        z.writestr('IMAGES/system.img', image({'/system/framework/framework-res.apk': b'framework'}))

spec = __import__('importlib.util').util.spec_from_file_location('release_pipeline', PIPELINE)
release_pipeline = __import__('importlib.util').util.module_from_spec(spec)
spec.loader.exec_module(release_pipeline)
EXPECTED = {'computerControlTargets': ['dev.pixel.calculator.agent'], 'prototypeAgentsEmpty': True, 'lockedSessions': False}
REAL_SIGNER = '650d47f91b7f5af3c530fef53fe92018aa4521efba065bcb1d6fc9b699060bfb'

class FrameworkDumpTest(unittest.TestCase):
    """check_framework's parser against the real aapt output of the 2026102802 candidate and edits of it."""
    def failures(self, dump, expected=EXPECTED, signer=REAL_SIGNER):
        return release_pipeline.framework_failures(dump, expected, signer)

    def test_real_dump_passes_and_reads_values_not_spec_rows(self):
        self.assertEqual(self.failures(FRAMEWORK_DUMP), [])
        self.assertEqual(release_pipeline.default_value(FRAMEWORK_DUMP, 'array', 'config_pixelComputerControlTargets'),
            (['dev.pixel.calculator.agent:650D47F91B7F5AF3C530FEF53FE92018AA4521EFBA065BCB1D6FC9B699060BFB'], None))
        self.assertEqual(release_pipeline.default_value(FRAMEWORK_DUMP, 'array', 'config_pixelComputerControlPrototypeAgents'), ([], None))
        self.assertEqual(release_pipeline.default_value(FRAMEWORK_DUMP, 'bool', 'config_pixelComputerControlLockedSessions'), (False, None))
        # Only the locked-session block is read: flipping the following unrelated boolean changes nothing.
        neighbour = FRAMEWORK_DUMP.rsplit('(boolean) false', 1)
        self.assertEqual(self.failures('(boolean) true'.join(neighbour)), [])

    def test_wrong_values_fail(self):
        locked_on = FRAMEWORK_DUMP.replace('(boolean) false', '(boolean) true', 1)
        self.assertEqual(self.failures(locked_on), ['compiled locked-session switch differs from config'])
        prototype = FRAMEWORK_DUMP.replace('Count=0\n', 'Count=1\n          #0 (Key=0x01000001): (string8) "dev.pixel.backgroundcontrol:AA"\n')
        self.assertEqual(self.failures(prototype), ['compiled prototype agents are not empty'])
        self.assertIn('compiled computer-control targets are', self.failures(FRAMEWORK_DUMP, signer='b' * 64)[0])
        other = FRAMEWORK_DUMP.replace('dev.pixel.calculator.agent:', 'com.example.other:')
        self.assertIn('compiled computer-control targets are', self.failures(other)[0])
        empty = FRAMEWORK_DUMP.replace('Count=1\n          #0 (Key=0x01000001): (string8) "dev.pixel.calculator.agent:650D47F91B7F5AF3C530FEF53FE92018AA4521EFBA065BCB1D6FC9B699060BFB"\n', 'Count=0\n')
        self.assertEqual(self.failures(empty), ['compiled computer-control targets are []'])

    def test_missing_malformed_or_overridden_values_fail_closed(self):
        lines = FRAMEWORK_DUMP.splitlines(keepends=True)
        without_value = ''.join(l for l in lines if 'resource 0x01110330' not in l or 'spec resource' in l).replace(
            '          (boolean) false\n        resource 0x01110331', '        resource 0x01110331')
        self.assertEqual(self.failures(without_value), ['config_pixelComputerControlLockedSessions has 0 default values in framework-res'])
        only_spec = ''.join(l for l in lines if 'config_pixelComputerControlTargets' not in l or 'spec resource' in l)
        self.assertIn('config_pixelComputerControlTargets has 0 default values in framework-res', self.failures(only_spec))
        self.assertIn('config_pixelComputerControlLockedSessions is not a well-formed boolean',
            self.failures(FRAMEWORK_DUMP.replace('(boolean) false', '(bool) false', 1)))
        self.assertIn('config_pixelComputerControlTargets is not a well-formed string array',
            self.failures(FRAMEWORK_DUMP.replace('Count=1', 'Count=2')))
        override = FRAMEWORK_DUMP.replace('      config watch flags=0x01 [sparse]:\n', '      config watch flags=0x01 [sparse]:\n'
            '        resource 0x01070156 android:array/config_pixelComputerControlPrototypeAgents: <bag>\n'
            '          Parent=0x00000000(Resolved=0x00000000), Count=0\n')
        self.assertIn("config_pixelComputerControlPrototypeAgents is overridden in config ['watch']", self.failures(override))
        self.assertEqual(len(self.failures('')), 3)

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
        ota(fixtures/'ota.zip', BUILD, STAMP)
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
            open(os.path.join(p, 'UPDATE.md'), 'w').write('# synthetic\\n')
            if os.environ.get('FAKE_SIGN_FAIL'): raise SystemExit('synthetic signing failure')
            import hashlib, json; digest = lambda f: hashlib.sha256(open(f, 'rb').read()).hexdigest()
            open(os.path.join(os.environ['ROSE_RELEASE_DIR'], 'sign-count'), 'a').write('signed\\n')
            json.dump({{'receiptType': 'os-signing', 'outputs': {{n: digest(os.path.join(p, n)) for n in sorted(os.listdir(p))}},
                'signedTargetFilesSha256': digest(os.path.join(d, 'signed-target_files.zip'))}}, open(os.path.join(d, 'sign-receipt.json'), 'w'))''')
        # CLI contract of build/make/tools/releasetools/check_ota_package_signature.py: CERT PACKAGE; the whole-package
        # check, then the payload check through delta_generator found on PATH (FileNotFoundError when it is absent).
        script(tools/'check_ota.py', '''
            import os, subprocess, sys
            assert len(sys.argv) == 3 and sys.argv[1].endswith('.x509.pem') and os.path.isfile(sys.argv[2])
            if os.environ.get("FAKE_OTA_BAD"): raise AssertionError("whole package signature mismatch")
            print("Whole package signature VERIFIED")
            subprocess.run(["delta_generator", "--in_file=" + sys.argv[2]], check=True)''')
        for build in (BUILD, '2026102801'):
            (src/f'releases/{build}/release-frankel-{build}/bin').mkdir(parents=True)
            script(src/f'releases/{build}/release-frankel-{build}/bin/delta_generator',
                'import os, sys; sys.exit(1 if os.environ.get("FAKE_PAYLOAD_BAD") else 0)')
        avb_sha1 = hashlib.sha1(b'synthetic avb key blob').hexdigest()
        # avbtool contract: info_image lists the referenced partitions; verify_image --follow_chain_partitions needs
        # each <partition>.img beside vbmeta.img.
        script(tools/'avbtool.py', f'''
            import os, sys
            image = sys.argv[sys.argv.index('--image') + 1]
            if sys.argv[1] == 'info_image':
                print("Public key (sha1): " + os.environ.get("FAKE_AVB_KEY_SHA1", "{avb_sha1}"))
                print("    Hash descriptor:\\n      Partition Name:        boot")
            elif sys.argv[1] == 'verify_image':
                assert '--follow_chain_partitions' in sys.argv
                if not os.path.exists(os.path.join(os.path.dirname(image), 'boot.img')):
                    sys.exit("boot.img: No such file or directory")
                sys.exit(1 if os.environ.get("FAKE_AVB_BAD") else 0)''')
        script(tools/'apksigner', f'print("Signer #1 certificate SHA-256 digest: {SIGNER.lower()}")')
        # The real `aapt dump --values resources` layout (see FRAMEWORK_DUMP): spec rows before value blocks.
        script(tools/'aapt', '''
            import os
            def bag(number, name, items):
                print("        resource 0x0107015%d android:array/%s: <bag>" % (number, name))
                print("          Parent=0x00000000(Resolved=0x00000000), Count=%d" % len(items))
                for i, t in enumerate(items): print('          #%d (Key=0x01000001): (string8) "%s"' % (i, t))
            targets = os.environ.get("FAKE_TARGETS", "dev.pixel.calculator.agent:" + "A" * 64).split(",")
            prototype = list(filter(None, os.environ.get("FAKE_PROTOTYPE", "").split(",")))
            print("      spec resource 0x01070156 android:array/config_pixelComputerControlPrototypeAgents: flags=0x00000000")
            print("      spec resource 0x01070157 android:array/config_pixelComputerControlTargets: flags=0x00000000")
            print("      config (default) flags=0x02 [offset16]:")
            if not os.environ.get("FAKE_NO_PROTOTYPE"): bag(6, "config_pixelComputerControlPrototypeAgents", prototype)
            bag(7, "config_pixelComputerControlTargets", targets)
            print("      spec resource 0x01110330 android:bool/config_pixelComputerControlLockedSessions: flags=0x00000000")
            print("      config (default) flags=0x02 [offset16]:")
            print("        resource 0x01110330 android:bool/config_pixelComputerControlLockedSessions: t=0x12 d=0x00000000 (s=0x0008 r=0x00)")
            print("          (boolean) false")''')
        script(tools/'simg2img', '''
            import sys; data = open(sys.argv[1], 'rb').read(); assert data[:4] == b'\\x3a\\xff\\x26\\xed'
            open(sys.argv[2], 'wb').write(data[4:])''')
        script(tools/'debugfs', '''
            import base64, json, sys; inside, out = sys.argv[2].split()[-2:]
            files = json.load(open(sys.argv[3]))
            if inside in files: open(out, 'wb').write(base64.b64decode(files[inside]))''')
        self.config = root/'config.json'
        self.config.write_text(json.dumps({'schemaVersion': 1, 'fixture': True,
            'target': {'device': 'frankel', 'variant': 'user', 'lunch': 'frankel-cur-user'},
            'host': {'sourceRoot': str(src), 'outDir': 'out', 'lockPath': str(root/'build.lock'), 'installclean': True,
                'makeJobs': 2, 'makeGoals': ['target-files-package'], 'integrateCommand': [PY, integrate], 'signCommand': [PY, sign],
                'fakeMake': [PY, fake_make],
                'tools': {'checkOtaSignature': str(tools/'check_ota.py'), 'avbtool': str(tools/'avbtool.py'),
                    'apksigner': str(tools/'apksigner'), 'aapt': str(tools/'aapt'),
                    'simg2img': str(tools/'simg2img'), 'debugfs': str(tools/'debugfs'),
                    'otatoolsBin': 'releases/{build}/release-{device}-{build}/bin'}},
            'versionBaselines': json.loads((SCRIPTS.parent/'os/release-pipeline/frankel.json').read_text())['versionBaselines'],
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

    def run_builder_job(self, apps=APPS):
        """The builder job's stage steps exactly as os-release.yml runs them, through the real os-release-stage.sh with
        the workflow's candidate environment; only the fake adapter and fixture config are appended."""
        subprocess.run([PY, str(STAGE), 'init', '--state', str(self.base/'stages'), '--source-revision', SHA, '--device', 'frankel',
            '--build-number', BUILD], check=True, capture_output=True)
        env = dict(os.environ, ROSE_BASE=str(self.base), CANDIDATE_SHA=SHA, BUILD_NUMBER=BUILD, BUILD_TIMESTAMP=STAMP,
            CHANNEL='beta', APPS=json.dumps(apps), PATH=f'{Path(PY).parent}:{os.environ["PATH"]}')
        if apps is None:
            env.pop('APPS')
        fake = ' '.join(shlex.quote(str(x)) for x in self.common[10:])
        for line in WORKFLOW_STAGES:
            result = subprocess.run(['bash', '-c', f'{line} {fake}'], cwd=SCRIPTS.parent, env=env, capture_output=True,
                text=True, timeout=120)
            if result.returncode:
                return line.split()[2], result
        return None, result

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
        tools = json.loads((artifact/'assets/verification.json').read_text())['imageTools']
        self.assertEqual(sorted(tools), ['debugfs', 'simg2img'])  # the fixture product image is sparse
        verifier = json.loads((artifact/'assets/verification.json').read_text())['verifierTools']
        self.assertEqual(sorted(verifier), ['avbtool', 'checkOtaSignature', 'delta_generator'])
        self.assertEqual(tools['debugfs']['sha256'], hashlib.sha256((self.root/'tools/debugfs').read_bytes()).hexdigest())
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
        os.environ['FAKE_TARGETS'] = 'dev.pixel.calculator.agent:' + SIGNER + ',com.example.other:' + SIGNER
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
        self.assertIn('config_pixelComputerControlPrototypeAgents has 0 default values', result.stderr)

    def test_bad_ota_signature_fails_after_signing(self):
        os.environ['FAKE_OTA_BAD'] = '1'
        self.addCleanup(os.environ.pop, 'FAKE_OTA_BAD', None)
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'ota-verify')
        self.assertIn('OTA package or payload signature verification failed', result.stderr)

    def test_workflow_stages_pass_the_dispatched_apps_to_every_command(self):
        self.assertEqual([l.split()[2:4] for l in WORKFLOW_STAGES], [['build', 'build'], ['bytes-verify', 'verify-build'],
            ['sign', 'sign'], ['ota-verify', 'verify-signed'], ['transfer', 'stage-artifact']])
        failed, result = self.run_builder_job(apps=[['dev.pixel.voice', 299], *APPS[1:]])
        self.assertEqual(failed, 'build')
        self.assertIn('dev.pixel.voice versionCode 299 must exceed retained 2026102801 versionCode 299', result.stderr)
        self.assertNotIn('versionCode None', result.stderr)

    def test_wrapper_refuses_to_run_without_apps(self):
        failed, result = self.run_builder_job(apps=None)
        self.assertEqual(failed, 'build')
        self.assertIn('APPS is required', result.stderr)

    def workflow_env(self):
        """The builder job's environment for running os-release.yml's prepare block and stage lines directly."""
        return dict(os.environ, HOME=str(self.root/'home'), CANDIDATE_SHA=SHA, BUILD_NUMBER=BUILD, BUILD_TIMESTAMP=STAMP,
            CHANNEL='beta', APPS=json.dumps(APPS), PATH=f'{Path(PY).parent}:{os.environ["PATH"]}', GITHUB_RUN_ID='4242',
            RESUME_STAGE='none', RESUME_UNSIGNED_SHA256='', RESUME_SOURCE_RUN='', GITHUB_ENV=str(self.root/'github-env'))

    def prepare(self, env, ok=True):
        (self.root/'github-env').write_text('')
        result = subprocess.run(['bash', '-c', PREPARE], cwd=SCRIPTS.parent, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        if ok:
            env['ROSE_BASE'] = (self.root/'github-env').read_text().strip().split('=', 1)[1]

    def workflow_stage(self, env, line):
        fake = ' '.join(shlex.quote(str(x)) for x in self.common[10:])
        return subprocess.run(['bash', '-c', f'{line} {fake}'], cwd=SCRIPTS.parent, env=env, capture_output=True, text=True)

    def built_attempt(self, env):
        self.prepare(env)
        built = self.workflow_stage(env, WORKFLOW_STAGES[0])
        self.assertEqual(built.returncode, 0, built.stderr)
        return json.loads(built.stderr[built.stderr.index('{'):built.stderr.rindex('}') + 1])['unsignedTargetFiles']

    def chain_status(self, base):
        return json.loads(subprocess.run([PY, str(STAGE), 'status', '--state', f'{base}/stages'], capture_output=True, text=True).stdout)

    def test_workflow_resume_reverifies_the_preserved_build_without_rebuilding(self):
        env = self.workflow_env()
        unsigned = self.built_attempt(env)
        first = Path(env['ROSE_BASE'])
        env['FAKE_TARGETS'] = 'com.example.other:' + SIGNER  # stands in for the parser defect: verification fails
        self.assertNotEqual(self.workflow_stage(env, WORKFLOW_STAGES[1]).returncode, 0)
        env.pop('FAKE_TARGETS')
        before = {p.name: p.read_bytes() for p in (first/'stages').iterdir()}
        makes = (self.root/'fake/events.log').read_text().count('make')
        env.update(RESUME_STAGE='bytes-verify', RESUME_UNSIGNED_SHA256='0' * 64)
        self.prepare(env, ok=False)  # a wrong expected hash resumes nothing
        shutil.rmtree(Path(f'{first}-resume-4242'))
        env['RESUME_UNSIGNED_SHA256'] = unsigned
        self.prepare(env)
        self.assertEqual(env['ROSE_BASE'], f'{first}-resume-4242')
        for line in WORKFLOW_STAGES[1:]:
            result = self.workflow_stage(env, line)
            self.assertEqual(result.returncode, 0, line + result.stderr)
        self.assertEqual({p.name: p.read_bytes() for p in (first/'stages').iterdir()}, before)
        self.assertEqual((self.root/'fake/events.log').read_text().count('make'), makes)  # nothing was rebuilt
        status = self.chain_status(env['ROSE_BASE'])
        self.assertEqual((status['state'], status['completedStages']), ('AWAITING_EXTERNAL',
            ['build', 'bytes-verify', 'sign', 'ota-verify', 'transfer']))
        self.assertTrue((Path(env['ROSE_BASE'])/'artifact/assets/verification.json').is_file())

    def test_workflow_sign_resume_continues_a_resumed_attempt_without_reverifying_or_rebuilding(self):
        # Mirrors 2802: the original attempt failed bytes-verify, its resume (run 4242) passed bytes-verify and failed
        # signing, and a sign resume (run 5151) names run 4242 as its source.
        env = self.workflow_env()
        unsigned = self.built_attempt(env)
        first = Path(env['ROSE_BASE'])
        env['FAKE_TARGETS'] = 'com.example.other:' + SIGNER
        self.assertNotEqual(self.workflow_stage(env, WORKFLOW_STAGES[1]).returncode, 0)
        env.pop('FAKE_TARGETS')
        env.update(RESUME_STAGE='bytes-verify', RESUME_UNSIGNED_SHA256=unsigned)
        self.prepare(env)
        resumed = Path(env['ROSE_BASE'])
        self.assertEqual(self.workflow_stage(env, WORKFLOW_STAGES[1]).returncode, 0)
        env['FAKE_SIGN_FAIL'] = '1'
        self.assertNotEqual(self.workflow_stage(env, WORKFLOW_STAGES[2]).returncode, 0)
        env.pop('FAKE_SIGN_FAIL')
        before = {base: {p.name: p.read_bytes() for p in (base/'stages').iterdir()} for base in (first, resumed)}
        makes = (self.root/'fake/events.log').read_text().count('make')
        env.update(RESUME_STAGE='sign', GITHUB_RUN_ID='5151')
        self.prepare(env, ok=False)  # without naming run 4242 the source is the original chain, which failed bytes-verify
        shutil.rmtree(Path(f'{first}-resume-5151'))
        env['RESUME_SOURCE_RUN'] = '4242'
        self.prepare(env)
        self.assertEqual(env['ROSE_BASE'], f'{first}-resume-5151')
        status = self.chain_status(env['ROSE_BASE'])
        self.assertEqual((status['stage'], status['completedStages']), ('sign', ['build', 'bytes-verify']))
        # The workflow skips bytes-verify on a sign resume and runs sign onward.
        for line in WORKFLOW_STAGES[2:]:
            result = self.workflow_stage(env, line)
            self.assertEqual(result.returncode, 0, line + result.stderr)
        self.assertEqual({base: {p.name: p.read_bytes() for p in (base/'stages').iterdir()} for base in (first, resumed)}, before)
        self.assertEqual((self.root/'fake/events.log').read_text().count('make'), makes)
        self.assertEqual(self.chain_status(env['ROSE_BASE'])['state'], 'AWAITING_EXTERNAL')
        self.assertIn("if: inputs.resume_failed_stage == 'none' || inputs.resume_failed_stage == 'bytes-verify'\n"
            "        run: bash scripts/os-release-stage.sh bytes-verify", WORKFLOW_TEXT)
        self.assertIn("ROSE_SIGN_RESUME_ARCHIVE: ${{ inputs.resume_failed_stage == 'sign' && format('failed-sign-{0}', github.run_id) || '' }}",
            WORKFLOW_TEXT)

    def test_workflow_ota_verify_resume_reuses_the_successful_signed_outputs_without_signing(self):
        # Mirrors 2802: the original attempt failed bytes-verify, its resume (run 4242) signed successfully and failed
        # ota-verify because of a verifier defect; the ota-verify resume (run 5151) names run 4242 and the signed hash.
        env = self.workflow_env()
        unsigned = self.built_attempt(env)
        first = Path(env['ROSE_BASE'])
        env['FAKE_TARGETS'] = 'com.example.other:' + SIGNER
        self.assertNotEqual(self.workflow_stage(env, WORKFLOW_STAGES[1]).returncode, 0)
        env.pop('FAKE_TARGETS')
        env.update(RESUME_STAGE='bytes-verify', RESUME_UNSIGNED_SHA256=unsigned)
        self.prepare(env)
        signed_attempt = Path(env['ROSE_BASE'])
        self.assertEqual(self.workflow_stage(env, WORKFLOW_STAGES[1]).returncode, 0)
        signed = self.workflow_stage(env, WORKFLOW_STAGES[2])
        self.assertEqual(signed.returncode, 0, signed.stderr)
        signed_sha = json.loads(signed.stderr[signed.stderr.index('{'):signed.stderr.rindex('}') + 1])['signedTargetFiles']
        env['FAKE_PAYLOAD_BAD'] = '1'  # stands in for the verifier defect
        self.assertNotEqual(self.workflow_stage(env, WORKFLOW_STAGES[3]).returncode, 0)
        env.pop('FAKE_PAYLOAD_BAD')
        preserved = {p: p.read_bytes() for base in (first, signed_attempt) for p in base.rglob('*') if p.is_file()}
        env.update(RESUME_STAGE='ota-verify', RESUME_SOURCE_RUN='4242', RESUME_SIGNED_SHA256='0' * 64, GITHUB_RUN_ID='5151')
        self.prepare(env, ok=False)  # the signed hash from the original log must match
        shutil.rmtree(Path(f'{first}-resume-5151'))
        tampered = signed_attempt/'release/public/UPDATE.md'
        original_notes = tampered.read_bytes()
        tampered.write_bytes(b'tampered\n')
        env['RESUME_SIGNED_SHA256'] = signed_sha
        self.prepare(env, ok=False)  # every public output must match the signing receipt
        tampered.write_bytes(original_notes)
        shutil.rmtree(Path(f'{first}-resume-5151'))
        self.prepare(env)
        status = self.chain_status(env['ROSE_BASE'])
        self.assertEqual((status['stage'], status['completedStages']), ('ota-verify', ['build', 'bytes-verify', 'sign']))
        for line in WORKFLOW_STAGES[3:]:  # the workflow skips build, bytes-verify, signer login and sign
            result = self.workflow_stage(env, line)
            self.assertEqual(result.returncode, 0, line + result.stderr)
        self.assertEqual({p: p.read_bytes() for p in preserved}, preserved)  # every earlier file is byte-identical
        self.assertFalse((Path(env['ROSE_BASE'])/'release/sign-count').exists())
        self.assertEqual((signed_attempt/'release/sign-count').read_text(), 'signed\n')  # signed exactly once, ever
        self.assertEqual(self.chain_status(env['ROSE_BASE'])['state'], 'AWAITING_EXTERNAL')
        for step in ("      - name: Signer access for OS signing\n        if: inputs.resume_failed_stage != 'ota-verify'",
                     "      - name: Sign\n        if: inputs.resume_failed_stage != 'ota-verify'"):
            self.assertIn(step, WORKFLOW_TEXT)

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

    def test_image_embedded_bytes_must_match_the_policy(self):
        apk = 'PRODUCT/app/RosePreinstall_dev_pixel_voice/RosePreinstall_dev_pixel_voice.apk'
        target_files(self.root/'fixtures/signed.zip', 'release-keys', self.placed, image_override={apk: b'stale bytes in the image'})
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'ota-verify')
        self.assertIn('image bytes of ' + apk + ' differ from the policy pin', result.stderr)

    def test_payload_properties_must_describe_payload(self):
        ota(self.root/'fixtures/ota.zip', BUILD, STAMP, bad_properties=True)
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'ota-verify')
        self.assertIn('payload_properties FILE_SIZE does not match payload.bin', result.stderr)

    def verify_fails(self, variable, message, value='1'):
        os.environ[variable] = value
        self.addCleanup(os.environ.pop, variable, None)
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'ota-verify', result.stderr)
        self.assertIn(message, result.stderr)
        return result

    def test_missing_delta_generator_is_a_dependency_failure_not_a_pass(self):
        (self.root/f'src/releases/{BUILD}/release-frankel-{BUILD}/bin/delta_generator').unlink()
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'ota-verify')
        self.assertIn('verifier dependency missing: delta_generator in the retained otatools bin', result.stderr)

    def test_delta_generator_absent_from_path_is_named_as_a_dependency_failure(self):
        bin_dir = self.root/f'src/releases/{BUILD}/release-frankel-{BUILD}/bin'
        (bin_dir/'delta_generator').rename(bin_dir/'delta_generator.off')
        (bin_dir/'delta_generator').mkdir()  # present by name but not runnable: the checker's lookup fails
        failed, result = self.run_builder_job()
        self.assertIn('verifier dependency missing', result.stderr)

    def test_missing_sibling_image_is_named_and_nothing_is_called_verified(self):
        os.environ['FAKE_NO_BOOT_IMG'] = '1'
        self.addCleanup(os.environ.pop, 'FAKE_NO_BOOT_IMG', None)
        target_files(self.root/'fixtures/signed.zip', 'release-keys', self.placed)  # signed images without boot.img
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'ota-verify', result.stderr)
        self.assertIn('verifier input missing: IMAGES/boot.img referenced by vbmeta', result.stderr)
        self.assertNotIn('vbmeta chain verification failed', result.stderr)

    def test_invalid_payload_or_vbmeta_signature_and_wrong_avb_identity_fail(self):
        self.verify_fails('FAKE_PAYLOAD_BAD', 'OTA package or payload signature verification failed')

    def test_invalid_vbmeta_chain_fails(self):
        self.verify_fails('FAKE_AVB_BAD', 'vbmeta chain verification failed')

    def test_vbmeta_signed_by_another_key_fails(self):
        self.verify_fails('FAKE_AVB_KEY_SHA1', 'vbmeta is not signed by the published AVB key', value='0' * 40)

    def test_checksums_are_written_after_verification_and_cover_it(self):
        failed, _ = self.run_builder_job()
        self.assertIsNone(failed)
        sums = (self.base/'artifact/assets/SHA256SUMS').read_text()
        self.assertIn('  verification.json', sums)
        self.assertEqual(len(sums.splitlines()), 6)

    def test_ota_metadata_parser_accepts_real_p1_values(self):
        # Values from the published P1 OTA (frankel 2026102801); fixture bytes, real metadata strings.
        spec = __import__('importlib.util').util.spec_from_file_location('pipeline', PIPELINE)
        pipeline = __import__('importlib.util').util.module_from_spec(spec)
        sys.path.insert(0, str(SCRIPTS))
        spec.loader.exec_module(pipeline)
        real = self.root/'p1-ota.zip'
        ota(real, '2026102801', '1793147782', post_build='google/frankel/frankel:17/CP3A.261005.005/2026102801:user/release-keys')
        config = json.loads(self.config.read_text())
        failures = pipeline.check_ota(config, {'build': '2026102801', 'timestamp': '1793147782'}, real, self.root/'fixtures', {})
        self.assertEqual(failures, [])
        self.assertEqual(pipeline.check_payload(real), [])
        self.assertTrue(pipeline.check_ota(config, {'build': '2026102802', 'timestamp': '1793147782'}, real, self.root/'fixtures', {}))

    def test_production_preflight_fails_closed_naming_every_missing_input(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('GCP_', 'ROSEOS_', 'PIXEL_OTA'))}
        code, out = self.pipeline('preflight', '--job', 'guard', env=env)
        self.assertEqual(code, 4)
        missing = json.loads(out)['missing']
        # Only the proposed admin setup remains; the host recipe in the checked-in config is complete.
        self.assertEqual(missing, ['ROSEOS_BUILDER_RUNNER_READY', 'GCP_WORKLOAD_IDENTITY_PROVIDER', 'GCP_CONTROLLER_SERVICE_ACCOUNT',
            'GCP_SIGNER_SERVICE_ACCOUNT', 'PIXEL_OTA_PUBLISHER (must be github-app)'])
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
