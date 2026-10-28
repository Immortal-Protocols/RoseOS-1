"""Permission APEX gate (INFRA-HOME-098): the checked-in permissionApex expectations derived from the pinned upstream
files, the OS #70 patch and the existing signing route; the gate through release-pipeline.check_permission_apex on
synthetic target-files (apex_fixture.py: real zip, protobuf and ext4/debugfs, stand-in AVB footer, container signature,
dex and erofs); and negative controls for a prebuilt, an unpatched module, wrong keys, signers, versions and formats."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
import zipfile

import apex_fixture as fx

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
FIXTURES = SCRIPTS/'tests/fixtures/permission-apex'
spec = importlib.util.spec_from_file_location('release_pipeline', SCRIPTS/'release-pipeline.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)
CONFIG = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
APEX = CONFIG['permissionApex']
PATH = 'SYSTEM/apex/com.android.permission.apex'
OTA_CERT, AVB_KEY = 'c' * 64, b'synthetic avb.pem public key'

def textproto_value(path):
    return re.search(r'_value:\s*"?([^"\s}]+)"?', path.read_text()).group(1)

class DerivationTest(unittest.TestCase):
    def test_module_key_version_and_jar_come_from_the_pinned_sources(self):
        bp = (FIXTURES/'Permission/Android.bp').read_text()
        self.assertIn(f'apex {{\n    name: "{APEX["module"]}",', bp)
        self.assertIn('public_key: "com.android.permission.avbpubkey"', bp)
        self.assertIn('contents: ["service-permission"]', bp)  # systemserverclasspath -> javalib/service-permission.jar
        self.assertEqual(APEX['payloadJar'], 'javalib/service-permission.jar')
        self.assertEqual(APEX['sourcePayloadKeySha256'], hashlib.sha256(fx.SOURCE_KEY).hexdigest())
        self.assertIn('"version": 0', (FIXTURES/'Permission/apex_manifest.json').read_text())  # Soong fills the default
        release = FIXTURES/'build-release'
        self.assertEqual(CONFIG['target']['lunch'], 'frankel-cur-user')
        self.assertIn('inherits: "aosp_current"', (release/'release_configs/cur.textproto').read_text())
        self.assertRegex((release/'release_config_map.textproto').read_text(), r'name: "aosp_current"\s+target: "cp2a"')
        self.assertEqual(int(textproto_value(release/'flag_values/cp2a/RELEASE_DEFAULT_UPDATABLE_MODULE_VERSION.textproto')),
            APEX['version'])
        self.assertEqual(APEX['paths'], [f'SYSTEM/apex/{APEX["module"]}.apex', f'SYSTEM/apex/{APEX["module"]}.capex'])

    def test_source_build_is_the_default_and_the_prebuilt_only_a_gms_map(self):
        release = FIXTURES/'build-release'
        self.assertEqual(textproto_value(release/'flag_declarations/RELEASE_DEFAULT_MODULE_BUILD_FROM_SOURCE.textproto'), 'true')
        self.assertNotIn('value', (release/'flag_declarations/RELEASE_APEX_CONTRIBUTIONS_PERMISSION.textproto').read_text())
        self.assertEqual(textproto_value(release/'gms_mainline/required/flag_values/cp2a/RELEASE_APEX_CONTRIBUTIONS_PERMISSION.textproto'),
            'permission.google.contributions.prebuilt')
        self.assertNotIn('module_sdk', (ROOT/'os/pinned-manifest.xml').read_text())

    def test_signed_expectations_are_the_existing_route_and_pins(self):
        script = (FIXTURES/'script/generate-release.sh').read_text()
        self.assertIn('--extra_apks com.android.permission.apex="$KEY_DIR/releasekey" \\\n'
            '    --extra_apex_payload_key com.android.permission.apex="$KEY_DIR/avb.pem" \\\n', script)
        self.assertIn('--avb_vbmeta_key "$KEY_DIR/avb.pem"', script)
        self.assertEqual((APEX['containerSigner'], APEX['payloadKey']), ('otaCertificate', 'avbPublicKey'))
        self.assertEqual(CONFIG['identities']['otaCertificateSha256'], '2ce41f9e7efb04cd7fa61b32e09091719304f7101405c3ef7ffcf0f0f5ec268a')
        self.assertEqual(CONFIG['identities']['avbPublicKeySha256'], '7c19d7fa197a2febb9b472fd27059443be9bfd4f20c22909966ebd7206168822')

    def test_markers_are_the_patch_constants_and_absent_upstream(self):
        patch = (ROOT/'os/patches/rose-home-role-permission.patch').read_bytes()
        self.assertEqual(hashlib.sha256(patch).hexdigest(), '82393552da5143697e557c6aeb4838761011caa7c8341ad6f276bfce304e9d18')
        upstream = (ROOT/'os/tests/fixtures/rose-home-role/RoleService.java').read_bytes()
        self.assertEqual(hashlib.sha256(upstream).hexdigest(), '8bc87856050126743959e24467f7b27a7258d95e9f216d0a26b06d0e8f1b3415')
        added = '\n'.join(l for l in patch.decode().splitlines() if l.startswith('+') and not l.startswith('+++'))
        self.assertEqual(APEX['markers'], list(fx.MARKERS))
        for marker in APEX['markers']:
            self.assertIn(f'"{marker}"', added)
            self.assertNotIn(marker.encode(), upstream)

class GateTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        tools = self.root/'tools'
        tools.mkdir()
        self.config = {'host': {'sourceRoot': str(self.root), 'tools': {'avbtool': fx.fake_avbtool(tools/'avbtool.py'),
            'debugfs': fx.DEBUGFS, 'fsckErofs': fx.fake_fsck_erofs(tools/'fsck.erofs')}},
            'identities': {'otaCertificateSha256': OTA_CERT, 'avbPublicKeySha256': hashlib.sha256(AVB_KEY).hexdigest()},
            'permissionApex': dict(APEX)}
        self.apksigner = fx.fake_apksigner(tools/'apksigner')

    def target_files(self, entries):
        path = self.root/f'tf-{len(list(self.root.glob("tf-*")))}.zip'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('SYSTEM/build.prop', 'ro.build.type=user\n')
            for name, data in entries.items():
                z.writestr(name, data)
        return path

    def check(self, entries, signed=False, config=None):
        used = {}
        failures, receipt = pipeline.check_permission_apex(self.target_files(entries), config or self.config, used,
            self.apksigner if signed else None)
        return failures, receipt, used

    def signed_apex(self, **kwargs):
        return fx.apex(**{'key': AVB_KEY, 'signer': OTA_CERT, **kwargs})

    def test_unsigned_source_build_passes_with_a_receipt(self):
        data = fx.apex()
        failures, receipt, used = self.check({PATH: data})
        self.assertEqual(failures, [])
        self.assertEqual(receipt, {'path': PATH, 'sha256': hashlib.sha256(data).hexdigest(), 'container': 'apex',
            'name': 'com.android.permission', 'version': 370399999, 'payloadKeySha256': APEX['sourcePayloadKeySha256'],
            'payloadSignature': 'verified', 'payloadFs': 'ext4', 'payloadJarSha256': hashlib.sha256(fx.service_jar()).hexdigest(),
            'markers': list(fx.MARKERS)})
        self.assertEqual(sorted(used), ['avbtool', 'debugfs'])

    def test_signed_build_passes_with_the_release_signer_and_avb_key(self):
        failures, receipt, _ = self.check({PATH: self.signed_apex()}, signed=True)
        self.assertEqual(failures, [])
        self.assertEqual((receipt['containerSigners'], receipt['payloadKeySha256']),
            ([OTA_CERT], self.config['identities']['avbPublicKeySha256']))

    def test_compressed_apex_checks_its_original_apex(self):
        capex_path = PATH.replace('.apex', '.capex')
        failures, receipt, _ = self.check({capex_path: fx.capex(self.signed_apex(), key=AVB_KEY, signer=OTA_CERT)}, signed=True)
        self.assertEqual(failures, [])
        self.assertEqual((receipt['container'], receipt['original']['version'], receipt['containerSigners']), ('capex', 370399999, [OTA_CERT]))
        failures, _, _ = self.check({capex_path: fx.capex(self.signed_apex(), key=AVB_KEY, version=1, signer=OTA_CERT)}, signed=True)
        self.assertEqual(failures, ['apex apex_manifest.pb differs from its original_apex'])
        failures, _, _ = self.check({capex_path: fx.capex(self.signed_apex(signer=None), key=AVB_KEY, signer=OTA_CERT)}, signed=True)
        self.assertEqual(failures, ['apex original_apex container is not signed by the expected certificate'])

    def test_prebuilt_absent_or_second_copy_fails(self):
        google = 'SYSTEM/apex/com.google.android.permission.apex'
        self.assertIn('no prebuilt or second copy', self.check({PATH: fx.apex(), google: b'prebuilt'})[0][0])
        self.assertIn("found ['SYSTEM/apex/com.google.android.permission.apex']", self.check({google: b'prebuilt'})[0][0])
        self.assertIn('no Permission APEX in the target-files', self.check({})[0][0])
        self.assertIn('no prebuilt or second copy', self.check({PATH: fx.apex(), PATH.replace('.apex', '.capex'): b'x'})[0][0])
        self.assertIn('no prebuilt or second copy', self.check({'SYSTEM_EXT/apex/com.android.permission.apex': fx.apex()})[0][0])

    def test_unpatched_or_prebuilt_contents_fail(self):
        cases = (
            (fx.apex(files={'javalib/service-permission.jar': fx.service_jar(markers=())}),
                "apex javalib/service-permission.jar dex lacks the RoleService migration markers ['rose_home_role_selected', "
                "'dev.pixel.voice']; the APEX is not built from the patched source"),
            (fx.apex(files={'javalib/service-permission.jar': fx.service_jar(markers=('rose_home_role_selected',))}),
                "apex javalib/service-permission.jar dex lacks the RoleService migration markers ['dev.pixel.voice']; "
                "the APEX is not built from the patched source"),
            (fx.apex(version=361099999), 'apex version is 361099999, expected 370399999 (the pinned source default)'),
            (fx.apex(name='com.google.android.permission'), 'apex is com.google.android.permission, expected com.android.permission'),
            (fx.apex(key=b'google module key'), 'apex payload key is not the expected key'),
            (fx.apex(files={}), 'apex payload has no javalib/service-permission.jar'),
            (fx.apex(files={'javalib/service-permission.jar': b'not a jar'}), 'apex javalib/service-permission.jar is not a readable jar'),
            (fx.apex(omit=('apex_pubkey',)), 'apex has no apex_pubkey'),
            (b'not an archive', 'apex is not a readable APEX archive'))
        for data, message in cases:
            self.assertEqual(self.check({PATH: data})[0], [message], message)

    def test_payload_signature_and_key_binding_fail_closed(self):
        self.assertEqual(self.check({PATH: fx.apex(verified=False)})[0],
            ['apex payload is not signed by its apex_pubkey', 'apex payload AVB signature or hashtree does not verify'])
        self.assertEqual(self.check({PATH: fx.apex(payload_key=b'another key')})[0], ['apex payload is not signed by its apex_pubkey'])

    def test_signed_stage_requires_the_release_keys(self):
        self.assertEqual(self.check({PATH: fx.apex(signer=OTA_CERT)}, signed=True)[0], ['apex payload key is not the expected key'])
        self.assertEqual(self.check({PATH: self.signed_apex(signer=None)}, signed=True)[0],
            ['apex container is not signed by the expected certificate'])
        self.assertEqual(self.check({PATH: fx.apex(key=AVB_KEY, signer='d' * 64)}, signed=True)[0],
            ['apex container is not signed by the expected certificate'])
        # The unsigned stage never accepts release keys in place of the module's own key.
        self.assertEqual(self.check({PATH: self.signed_apex()})[0], ['apex payload key is not the expected key'])

    def test_erofs_payload_uses_the_configured_tool_and_unknown_formats_fail(self):
        failures, receipt, used = self.check({PATH: fx.apex(fs='erofs')})
        self.assertEqual((failures, receipt['payloadFs'], sorted(used)), ([], 'erofs', ['avbtool', 'fsckErofs']))
        self.assertEqual(self.check({PATH: fx.apex(fs='f2fs')})[0], ['apex payload filesystem is neither ext4 nor erofs; not read'])
        config = json.loads(json.dumps(self.config))
        del config['host']['tools']['fsckErofs']
        with self.assertRaises(pipeline.SetupMissing) as missing:
            self.check({PATH: fx.apex(fs='erofs')}, config=config)
        self.assertEqual(missing.exception.missing, ['host.tools.fsckErofs'])

    def test_missing_pins_are_setup_inputs(self):
        for key in ('version', 'markers', 'sourcePayloadKeySha256'):
            config = json.loads(json.dumps(self.config))
            config['permissionApex'][key] = None if key != 'markers' else []
            with self.assertRaises(pipeline.SetupMissing) as missing:
                self.check({PATH: fx.apex()}, config=config)
            self.assertEqual(missing.exception.missing, [f'permissionApex.{key}'])
        without = {k: v for k, v in CONFIG.items() if k != 'permissionApex'}
        self.assertIn('permissionApex.module', pipeline.preflight('guard', without, {}, 'production'))
        self.assertNotIn('permissionApex.module', pipeline.preflight('guard', CONFIG, {}, 'production'))

if __name__ == '__main__':
    unittest.main()
