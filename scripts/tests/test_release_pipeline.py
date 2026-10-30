"""os-release pipeline with the fake adapter: synthetic target-files, OTA, APKs, throwaway keys and fake
tools exercise the real verification code end to end, plus fail-closed production preflight."""
import base64
import hashlib
import io
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

import apex_fixture

SCRIPTS = Path(__file__).resolve().parents[1]
PIPELINE, STAGE = SCRIPTS/'release-pipeline.py', SCRIPTS/'release-stage.py'
PY = sys.executable
SHA, BUILD, STAMP = '34fa48ff16d046e16c7d680731bf01058d794279', '2026103001', '1793377800'
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
APPS = [['dev.pixel.voice', 312], ['dev.pixel.calculator.agent', 4], ['dev.pixel.backgroundcontrol', 28]]
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

# Fixture layout for the Updater gate; the real paths come from the OS product inclusion (config updater block).
UPDATER_APK = 'SYSTEM_EXT/priv-app/Updater/Updater.apk'
UPDATER_XML = ('SYSTEM_EXT/etc/permissions/privapp-permissions-updater.xml',
    'SYSTEM_EXT/etc/default-permissions/default-permissions-updater.xml', 'SYSTEM_EXT/etc/sysconfig/updater.xml')
UPDATER_URL = 'https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/'
PERMISSION_APEX = 'SYSTEM/apex/com.android.permission.apex'
PERMISSION_SPEC = json.loads((SCRIPTS.parent/'os/release-pipeline/frankel.json').read_text())['permissionApex']
SYSTEMUI_SPEC = json.loads((SCRIPTS.parent/'os/release-pipeline/frankel.json').read_text())['systemUiTray']
SYSTEMUI_APK = SYSTEMUI_SPEC['apkPath']

def updater_apk(rose_channels=True):
    """A stand-in Updater APK: an archive whose classes.dex names the RoseChannels type when it is compiled in."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as apk:
        types = b'Lapp/seamlessupdate/client/Service;' + (b'Lapp/seamlessupdate/client/RoseChannels;' if rose_channels else b'')
        apk.writestr(zipfile.ZipInfo('classes.dex', (2009, 1, 1, 0, 0, 0)), b'dex\n035\0' + types)
        apk.writestr(zipfile.ZipInfo('AndroidManifest.xml', (2009, 1, 1, 0, 0, 0)), b'binary manifest')
    return buffer.getvalue()

UPDATER_APK_BYTES = updater_apk()

def systemui_apk(markers=tuple(SYSTEMUI_SPEC['markers']), dex=True):
    """A stand-in SystemUI APK: classes.dex holds only the dex strings the tray gate reads, plus an unrelated second dex.
    dex=False is a dexpreopted APK whose code is not in the archive."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as apk:
        if dex:
            apk.writestr(zipfile.ZipInfo('classes.dex', (2009, 1, 1, 0, 0, 0)), b'dex\n039\0Lcom/android/systemui/SystemUIApplication;')
            apk.writestr(zipfile.ZipInfo('classes2.dex', (2009, 1, 1, 0, 0, 0)), b'dex\n039\0' + b''.join(m.encode() + b'\0' for m in markers))
        apk.writestr(zipfile.ZipInfo('AndroidManifest.xml', (2009, 1, 1, 0, 0, 0)), b'binary manifest')
    return buffer.getvalue()

SYSTEMUI_APK_BYTES = systemui_apk()
# The 2026102805 SystemUI: the tray prototype and its service name, without the screen-off lifecycle (AgentTrayLink, OS #85).
SYSTEMUI_2805_BYTES = systemui_apk(markers=tuple(SYSTEMUI_SPEC['markers'][:2]))

def write_updater(z, apk_paths=(UPDATER_APK,), xml=UPDATER_XML, package='app.seamlessupdate.client', rose_channels=True):
    for apk in apk_paths:
        z.writestr(apk, updater_apk(rose_channels))
    for name in xml:
        z.writestr(name, f'<permissions><privapp-permissions package="{package}"/></permissions>\n')

def target_files(path, keys, policy_apks, stale_misc=False, image_override=None, apex=None, systemui=SYSTEMUI_APK_BYTES):
    """Minimal target-files: build props, misc_info AVB fingerprints, presigned APKs, framework-res, images."""
    # As the 2806 build recorded them (run 40778491188): the system partition is the shared generic system image, every
    # other partition names the device.
    fp = f'google/frankel/frankel:17/CP3A.261005.005/{BUILD}:user/{keys}'
    system_fp = f'Android/generic_system/generic:17/CP3A.261005.005/{BUILD}:user/{keys}'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('SYSTEM/build.prop', f'ro.build.version.incremental={BUILD}\nro.build.date.utc={STAMP}\n'
            f'ro.build.type=user\nro.product.system.device=generic\nro.system.build.fingerprint={system_fp}\n')
        z.writestr('VENDOR/build.prop', f'ro.vendor.build.version.incremental={BUILD}\nro.product.vendor.device=frankel\n'
            f'ro.vendor.build.fingerprint={fp}\n')
        misc_fp = system_fp.replace(BUILD, '2026102701') if stale_misc else system_fp
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
        if not os.environ.get('FAKE_NO_UPDATER'):  # 077 shipped no Updater at all
            write_updater(z)
            embedded[UPDATER_APK] = UPDATER_APK_BYTES
        if systemui is not None:
            z.writestr(SYSTEMUI_APK, systemui)
            embedded[SYSTEMUI_APK] = systemui
        embedded.update(image_override or {})
        z.writestr('IMAGES/system_ext.img', image({'/' + k[len('SYSTEM_EXT/'):]: v for k, v in embedded.items() if k.startswith('SYSTEM_EXT/')}))
        z.writestr('IMAGES/product.img', image({'/' + k[len('PRODUCT/'):]: v for k, v in embedded.items() if k.startswith('PRODUCT/')}, sparse=True))
        system = {'/system/framework/framework-res.apk': b'framework'}
        if apex is not None:  # the Permission APEX built from the patched source (apex_fixture.py)
            z.writestr(PERMISSION_APEX, apex)
            system['/system/apex/com.android.permission.apex'] = apex
        system.update({'/system/' + k[len('SYSTEM/'):]: v for k, v in (image_override or {}).items() if k.startswith('SYSTEM/')})
        z.writestr('IMAGES/system.img', image(system))

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
        target_files(fixtures/'unsigned.zip', 'test-keys', self.placed, apex=apex_fixture.apex())
        self.signed_apex = apex_fixture.apex(key=b'synthetic avb key blob', signer=cert_pin)
        target_files(fixtures/'signed.zip', 'release-keys', self.placed, apex=self.signed_apex)
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
        vbmeta_avbtool = script(tools/'vbmeta_avbtool.py', f'''
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
        apex_fixture.fake_avbtool(tools/'avbtool.py', passthrough=vbmeta_avbtool)
        apex_fixture.fake_apksigner(tools/'apksigner', passthrough_digest=SIGNER.lower())
        # The real `aapt dump --values resources` layout (see FRAMEWORK_DUMP): spec rows before value blocks.
        script(tools/'aapt', '''
            import os, sys
            if 'updater-' in os.path.basename(sys.argv[-1]):
                # Updater APK: badging and the real `aapt dump --values resources` string/array layout.
                if sys.argv[2] == 'badging':
                    print("package: name='%s' versionCode='1' versionName='1'" % os.environ.get('FAKE_UPDATER_PACKAGE', 'app.seamlessupdate.client'))
                    sys.exit(0)
                def string(number, name, value):
                    print("        resource 0x7f0e000%d app.seamlessupdate.client:string/%s: t=0x03 d=0x0000000%d (s=0x0008 r=0x00)" % (number, name, number))
                    print('          (string8) "%s"' % value)
                def array(number, name, items):
                    print("        resource 0x7f03000%d app.seamlessupdate.client:array/%s: <bag>" % (number, name))
                    print("          Parent=0x00000000(Resolved=0x00000000), Count=%d" % len(items))
                    for i, t in enumerate(items): print('          #%d (Key=0x01000001): (string8) "%s"' % (i, t))
                print("      spec resource 0x7f0e0001 app.seamlessupdate.client:string/url: flags=0x00000000")
                print("      config (default) flags=0x02 [offset16]:")
                string(1, 'url', os.environ.get('FAKE_UPDATER_URL', 'UPDATER_URL_PLACEHOLDER'))
                string(2, 'channel_default', os.environ.get('FAKE_UPDATER_DEFAULT', 'prod'))
                array(1, 'channel_published', os.environ.get('FAKE_UPDATER_PUBLISHED', 'stable').split(','))
                array(2, 'channel_values', ['prod', 'stable', 'alpha'])
                sys.exit(0)
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
            print("          (boolean) false")'''.replace('UPDATER_URL_PLACEHOLDER', UPDATER_URL))
        script(tools/'simg2img', '''
            import sys; data = open(sys.argv[1], 'rb').read(); assert data[:4] == b'\\x3a\\xff\\x26\\xed'
            open(sys.argv[2], 'wb').write(data[4:])''')
        # Partition stand-ins are JSON; a real ext4 image (the APEX payload) goes to the real debugfs.
        script(tools/'debugfs', f'''
            import base64, json, subprocess, sys; inside, out = sys.argv[2].split()[-2:]
            try:
                files = json.load(open(sys.argv[3]))
            except ValueError:
                sys.exit(subprocess.run([{apex_fixture.DEBUGFS!r}, *sys.argv[1:]], capture_output=True).returncode)
            if inside in files: open(out, 'wb').write(base64.b64decode(files[inside]))''')
        self.config = root/'config.json'
        self.config.write_text(json.dumps({'schemaVersion': 1, 'fixture': True,
            'target': {'device': 'frankel', 'variant': 'user', 'lunch': 'frankel-cur-user'},
            'host': {'sourceRoot': str(src), 'outDir': 'out', 'lockPath': str(root/'build.lock'), 'installclean': True,
                'makeJobs': 2, 'makeGoals': ['target-files-package'], 'integrateCommand': [PY, integrate], 'signCommand': [PY, sign],
                'integrate': {'reconcile': {'applyArgs': ['--agent-tray', '--updater-url', UPDATER_URL]}},
                'fakeMake': [PY, fake_make],
                'tools': {'checkOtaSignature': str(tools/'check_ota.py'), 'avbtool': str(tools/'avbtool.py'),
                    'apksigner': str(tools/'apksigner'), 'aapt': str(tools/'aapt'),
                    'simg2img': str(tools/'simg2img'), 'debugfs': str(tools/'debugfs'),
                    'otatoolsBin': 'releases/{build}/release-{device}-{build}/bin'}},
            'versionBaselines': json.loads((SCRIPTS.parent/'os/release-pipeline/frankel.json').read_text())['versionBaselines'],
            'identities': {'appSignerSha256': SIGNER.lower(), 'otaCertificateSha256': cert_pin,
                'avbPublicKeySha256': hashlib.sha256(b'synthetic avb key blob').hexdigest()},
            'framework': {'computerControlTargets': ['dev.pixel.calculator.agent'], 'prototypeAgentsEmpty': True, 'lockedSessions': False},
            'updater': {'packageName': 'app.seamlessupdate.client', 'channelDefault': 'prod', 'channelValues': ['prod', 'stable', 'alpha'],
                'apkPath': UPDATER_APK, 'signer': SIGNER.lower(), 'requiredFiles': list(UPDATER_XML)},
            'permissionApex': PERMISSION_SPEC, 'systemUiTray': SYSTEMUI_SPEC,
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
        env = dict(os.environ, ROSE_BASE=str(self.base), ROSE_DEVICE='frankel', CANDIDATE_SHA=SHA, BUILD_NUMBER=BUILD, BUILD_TIMESTAMP=STAMP,
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
        self.assertEqual(sorted(verifier), ['avbtool', 'checkOtaSignature', 'debugfs', 'delta_generator'])  # debugfs: APEX payload
        apex = json.loads((artifact/'assets/verification.json').read_text())['permissionApex']
        self.assertEqual((apex['path'], apex['version'], apex['markers'], apex['payloadFs'], apex['sha256']),
            (PERMISSION_APEX, 370399999, PERMISSION_SPEC['markers'], 'ext4', hashlib.sha256(self.signed_apex).hexdigest()))
        tray = json.loads((artifact/'assets/verification.json').read_text())['systemUiTray']
        self.assertEqual((tray['apkPath'], tray['sha256'], tray['dexFiles'], tray['markers']),
            (SYSTEMUI_APK, hashlib.sha256(SYSTEMUI_APK_BYTES).hexdigest(), 2, SYSTEMUI_SPEC['markers']))
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
        return dict(os.environ, HOME=str(self.root/'home'), ROSE_DEVICE='frankel', CANDIDATE_SHA=SHA, BUILD_NUMBER=BUILD, BUILD_TIMESTAMP=STAMP,
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

    def test_permission_apex_must_be_the_patched_source_build(self):
        # bytes-verify: an unpatched module (no RoleService markers) or a Google prebuilt stops before signing.
        target_files(self.root/'fixtures/unsigned.zip', 'test-keys', self.placed,
            apex=apex_fixture.apex(files={'javalib/service-permission.jar': apex_fixture.service_jar(markers=())}))
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'bytes-verify')
        self.assertIn('dex lacks the RoleService migration markers', result.stderr)
        self.assertFalse((self.base/'release/signed-target_files.zip').exists())

    def test_signed_permission_apex_needs_the_release_signer(self):
        target_files(self.root/'fixtures/signed.zip', 'release-keys', self.placed, apex=apex_fixture.apex(key=b'synthetic avb key blob',
            signer='d' * 64))
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'ota-verify')
        self.assertIn('apex container is not signed by the expected certificate', result.stderr)

    def test_signed_permission_apex_image_bytes_must_match(self):
        target_files(self.root/'fixtures/signed.zip', 'release-keys', self.placed, apex=self.signed_apex,
            image_override={PERMISSION_APEX: b'stale apex in the image'})
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'ota-verify')
        self.assertIn(f'image bytes of {PERMISSION_APEX} differ from the checked target-files copy', result.stderr)

    def test_systemui_must_carry_the_agent_tray_lifecycle(self):
        # bytes-verify: the 2026102805 SystemUI (tray without AgentTrayLink), no SystemUI, or no readable dex stop before signing.
        for systemui, message in ((SYSTEMUI_2805_BYTES, "SystemUI dex lacks ['Lcom/android/wm/shell/AgentTrayLink;', 'Voice binding already released']"),
                (None, f'SystemUI APK {SYSTEMUI_APK} is absent from the target-files'),
                (systemui_apk(dex=False), 'SystemUI APK carries no classes*.dex'),
                (b'not an apk', 'SystemUI APK is not a readable APK archive')):
            with self.subTest(message=message):
                target_files(self.root/'fixtures/unsigned.zip', 'test-keys', self.placed, apex=apex_fixture.apex(), systemui=systemui)
                failed, result = self.run_builder_job()
                self.assertEqual(failed, 'bytes-verify')
                self.assertIn(message, result.stderr)
                self.assertFalse((self.base/'release/signed-target_files.zip').exists())
                shutil.rmtree(self.base)

    def test_signed_systemui_must_carry_the_tray_and_match_the_image(self):
        # ota-verify: an old SystemUI in the signed target-files, or image bytes that are not the checked APK.
        for kwargs, message in (({'systemui': SYSTEMUI_2805_BYTES}, 'SystemUI dex lacks'),
                ({'image_override': {SYSTEMUI_APK: SYSTEMUI_2805_BYTES}}, f'image bytes of {SYSTEMUI_APK} differ from the checked target-files copy')):
            with self.subTest(message=message):
                target_files(self.root/'fixtures/signed.zip', 'release-keys', self.placed, apex=self.signed_apex, **kwargs)
                failed, result = self.run_builder_job()
                self.assertEqual(failed, 'ota-verify')
                self.assertIn(message, result.stderr)
                shutil.rmtree(self.base)

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

    def updater_zip(self, **kwargs):
        path = self.root/'updater-target_files.zip'
        with zipfile.ZipFile(path, 'w') as z:
            write_updater(z, **kwargs)
        return path

    def check_updater(self, path, apksigner=True, config=None):
        return release_pipeline.check_updater(path, config or json.loads(self.config.read_text()), str(self.root/'tools/aapt'),
            str(self.root/'tools/apksigner') if apksigner else None)

    def test_077_omission_stops_the_build_before_signing(self):
        os.environ['FAKE_NO_UPDATER'] = '1'
        self.addCleanup(os.environ.pop, 'FAKE_NO_UPDATER', None)
        for name, keys in (('unsigned.zip', 'test-keys'), ('signed.zip', 'release-keys')):
            target_files(self.root/'fixtures'/name, keys, self.placed)
        failed, result = self.run_builder_job()
        self.assertEqual(failed, 'bytes-verify')
        self.assertIn(f'Updater APK {UPDATER_APK} is absent from the target-files (no Updater APK anywhere)', result.stderr)

    def test_updater_gate_accepts_the_configured_layout(self):
        self.assertEqual(self.check_updater(self.updater_zip()), [])

    def test_updater_gate_refuses_wrong_place_duplicates_and_missing_or_foreign_xml(self):
        moved = 'PRODUCT/priv-app/Updater/Updater.apk'
        self.assertEqual(self.check_updater(self.updater_zip(apk_paths=(moved,))),
            [f"Updater APK {UPDATER_APK} is absent from the target-files (found ['{moved}'])"])
        self.assertIn('more than one Updater APK', self.check_updater(self.updater_zip(apk_paths=(UPDATER_APK, moved)))[0])
        self.assertEqual(self.check_updater(self.updater_zip(xml=UPDATER_XML[:2])), [f'Updater supporting file {UPDATER_XML[2]} is absent'])
        failures = self.check_updater(self.updater_zip(package='com.example.other'))
        self.assertEqual(len(failures), 3)
        self.assertTrue(all('does not name app.seamlessupdate.client' in f for f in failures))

    def test_updater_gate_refuses_wrong_identity_config_or_signer(self):
        path = self.updater_zip()
        for variable, value, message in (
                ('FAKE_UPDATER_PACKAGE', 'com.example.updater', 'Updater package is com.example.updater, expected app.seamlessupdate.client'),
                ('FAKE_UPDATER_URL', 'https://releases.grapheneos.org/', "Updater compiled url is 'https://releases.grapheneos.org/'"),
                ('FAKE_UPDATER_DEFAULT', 'stable', "Updater compiled channel_default is 'stable', expected 'prod'"),
                ('FAKE_UPDATER_PUBLISHED', 'stable,alpha', "Updater compiled channel_published is ['stable', 'alpha'], expected ['stable']")):
            os.environ[variable] = value
            try:
                failures = self.check_updater(path)
                self.assertEqual(len(failures), 1, failures)
                self.assertTrue(failures[0].startswith(message), failures)
            finally:
                os.environ.pop(variable)
        config = json.loads(self.config.read_text())
        config['updater']['signer'] = 'otaCertificate'  # the fixture APK is signed by the app signer instead
        self.assertEqual(self.check_updater(path, config=config), ['Updater is not signed by the expected certificate'])
        self.assertEqual(self.check_updater(path, apksigner=False, config=config), [])  # unsigned stage: no signer check

    def test_updater_without_the_rose_channel_code_fails(self):
        self.assertEqual(self.check_updater(self.updater_zip(rose_channels=False)),
            ['Updater dex has no app.seamlessupdate.client.RoseChannels; the RoseOS channel code is not compiled in'])

    # OCT30-INFRA-S018: a retained --keep-upstream-updater build is verified against its own evidenced upstream contract.
    GRAPHENEOS_URL = 'https://releases.grapheneos.org/'

    def upstream(self, **changes):
        config = json.loads(self.config.read_text())
        args = ['--agent-tray', '--keep-upstream-updater']
        config['target']['device'] = 'rango'
        config['host']['integrate']['reconcile']['applyArgs'] = args
        rose = config['updater']
        config['updater'] = {'mode': 'upstream', 'recipeApplyArgs': args, 'included': True, 'apkPath': rose['apkPath'],
            'packageName': rose['packageName'], 'requiredFiles': rose['requiredFiles'], 'signer': rose['signer'],
            'resources': {'string/url': self.GRAPHENEOS_URL, 'string/channel_default': 'prod'}}
        for key, value in changes.items():
            if value is None:
                config['updater'].pop(key, None)
            else:
                config['updater'][key] = value
        return config

    def upstream_env(self, url=None):
        os.environ['FAKE_UPDATER_URL'] = url or self.GRAPHENEOS_URL
        self.addCleanup(os.environ.pop, 'FAKE_UPDATER_URL', None)

    def test_upstream_updater_passes_with_its_evidenced_contract_and_no_rose_code(self):
        self.upstream_env()
        path = self.updater_zip(rose_channels=False)
        self.assertEqual(self.check_updater(path, apksigner=False, config=self.upstream()), [])
        self.assertEqual(self.check_updater(path, config=self.upstream()), [])
        record = release_pipeline.updater_record(self.upstream())
        self.assertEqual((record['mode'], record['roseDiscovery'], record['included']), ('upstream', False, True))
        self.assertEqual(release_pipeline.updater_record(json.loads(self.config.read_text()))['roseDiscovery'], True)

    def test_upstream_updater_refuses_wrong_url_package_path_xml_signer_or_rose_hybrid(self):
        self.upstream_env()
        upstream = self.upstream()
        self.assertEqual(self.check_updater(self.updater_zip(rose_channels=True), config=upstream),
            ['Updater dex has the Rose channel code in an upstream-mode build (Rose/upstream hybrid)'])
        path = self.updater_zip(rose_channels=False)
        os.environ['FAKE_UPDATER_URL'] = UPDATER_URL  # the Rose endpoint compiled into an upstream-mode build
        self.assertEqual(self.check_updater(path, config=upstream),
            [f"Updater compiled url is {UPDATER_URL!r}, expected {self.GRAPHENEOS_URL!r}"])
        os.environ['FAKE_UPDATER_URL'] = self.GRAPHENEOS_URL
        self.assertIn("Updater compiled channel_default is 'prod', expected 'beta'",
            self.check_updater(path, config=self.upstream(resources={'string/url': self.GRAPHENEOS_URL, 'string/channel_default': 'beta'})))
        self.assertIn('absent_setting has 0 default values', ' '.join(self.check_updater(path, config=self.upstream(
            resources={'string/url': self.GRAPHENEOS_URL, 'string/absent_setting': 'x'}))))
        os.environ['FAKE_UPDATER_PACKAGE'] = 'com.example.updater'
        self.addCleanup(os.environ.pop, 'FAKE_UPDATER_PACKAGE', None)
        self.assertIn('Updater package is com.example.updater, expected app.seamlessupdate.client', self.check_updater(path, config=upstream))
        os.environ.pop('FAKE_UPDATER_PACKAGE')
        moved = 'PRODUCT/priv-app/Updater/Updater.apk'
        self.assertIn('more than one Updater APK', self.check_updater(self.updater_zip(apk_paths=(UPDATER_APK, moved), rose_channels=False),
            config=upstream)[0])
        self.assertEqual(self.check_updater(self.updater_zip(apk_paths=(moved,), rose_channels=False), config=upstream),
            [f"Updater APK {UPDATER_APK} is absent from the target-files (found ['{moved}'])"])
        self.assertEqual(self.check_updater(self.updater_zip(xml=UPDATER_XML[:2], rose_channels=False), config=upstream),
            [f'Updater supporting file {UPDATER_XML[2]} is absent'])
        self.assertIn(f'does not name app.seamlessupdate.client', ' '.join(self.check_updater(
            self.updater_zip(package='com.example.other', rose_channels=False), config=upstream)))
        self.assertEqual(self.check_updater(self.updater_zip(rose_channels=False), config=self.upstream(signer='0' * 64)),
            ['Updater is not signed by the expected certificate'])

    def test_upstream_absence_contract(self):
        absent = self.upstream(included=False, signer=None, resources=None)
        empty = self.root/'no-updater.zip'
        with zipfile.ZipFile(empty, 'w') as z:
            z.writestr('SYSTEM/build.prop', 'ro.build.type=user\n')
        self.assertEqual(self.check_updater(empty, config=absent), [])
        self.assertEqual(release_pipeline.updater_record(absent)['included'], False)
        self.assertIn('upstream Updater expected absent', self.check_updater(self.updater_zip(rose_channels=False), config=absent)[0])
        with zipfile.ZipFile(empty, 'a') as z:
            z.writestr(UPDATER_XML[0], '<permissions/>')  # a leftover supporting file is not absence
        self.assertIn(UPDATER_XML[0], self.check_updater(empty, config=absent)[0])

    def test_upstream_mode_refuses_unknown_unbound_conflicting_or_misplaced_contracts(self):
        path = self.updater_zip(rose_channels=False)
        unbound = self.upstream(included=None)
        with self.assertRaises(release_pipeline.SetupMissing) as caught:
            self.check_updater(path, config=unbound)
        self.assertEqual(caught.exception.missing, ['updater.included'])
        with self.assertRaises(release_pipeline.SetupMissing) as caught:
            self.check_updater(path, config=self.upstream(resources={'string/channel_default': 'prod'}))
        self.assertEqual(caught.exception.missing, ['updater.resources.string/url'])
        with self.assertRaises(release_pipeline.SetupMissing) as caught:
            self.check_updater(path, config=self.upstream(signer=None))
        self.assertEqual(caught.exception.missing, ['updater.signer'])
        frankel = self.upstream()
        frankel['target']['device'] = 'frankel'
        rose_recipe = self.upstream()
        rose_recipe['host']['integrate']['reconcile']['applyArgs'] = rose_recipe['updater']['recipeApplyArgs'] = ['--updater-url', UPDATER_URL]
        differing = self.upstream()
        differing['host']['integrate']['reconcile']['applyArgs'] = ['--keep-upstream-updater']
        unknown = json.loads(self.config.read_text())
        unknown['updater']['mode'] = 'skip'
        for config, message in ((unknown, "updater.mode 'skip'"), (frankel, 'never frankel'),
                                (self.upstream(recipeApplyArgs=None), 'needs the --keep-upstream-updater recipe'),
                                (rose_recipe, 'needs the --keep-upstream-updater recipe'),
                                (self.upstream(recipeApplyArgs=['--keep-upstream-updater', '--updater-published-channel', 'stable']),
                                 'needs the --keep-upstream-updater recipe'),
                                (differing, 'differ from the reconcile applyArgs'),
                                (self.upstream(channelValues=['prod']), 'hybrid'),
                                (self.upstream(included='yes'), 'must be true or false'),
                                (self.upstream(included=False, resources=None), 'conflicting upstream contract'),
                                (self.upstream(resources={'url': self.GRAPHENEOS_URL}), 'kind/name'),
                                (self.upstream(resources=[self.GRAPHENEOS_URL]), 'kind/name')):
            with self.subTest(message=message), self.assertRaisesRegex(release_pipeline.InvalidInput, re.escape(message)):
                self.check_updater(path, config=config)

    def test_rose_mode_without_its_channel_url_refuses_instead_of_crashing(self):
        config = json.loads(self.config.read_text())
        config['host']['integrate']['reconcile']['applyArgs'] = ['--agent-tray', '--keep-upstream-updater']
        with self.assertRaisesRegex(release_pipeline.InvalidInput, 'updater.mode rose needs --updater-url'):
            self.check_updater(self.updater_zip(), config=config)

    def test_updater_gate_fails_closed_without_the_os_layout(self):
        config = json.loads(self.config.read_text())
        for key in ('apkPath', 'signer', 'requiredFiles'):
            broken = json.loads(json.dumps(config))
            broken['updater'][key] = None
            with self.assertRaises(release_pipeline.SetupMissing) as caught:
                self.check_updater(self.updater_zip(), config=broken)
            self.assertEqual(caught.exception.missing, [f'updater.{key}'])

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
