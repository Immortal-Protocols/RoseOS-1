"""No rules on what a generated app may do (Nathan, 2026-11-14: "we don't want to impose any rules on what people can
ask to build or be built"). Any permission, component or role an app declares is built; Android and the phone's owner
decide what it is granted. What remains are technical checks of the app's identity and update path. No model,
network or phone; the build test needs the pinned SDK."""
import os
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import template, toolchain
from rose.app_builder.service import Builder
from rose.tests.test_app_builder import HAS_SDK, JAVA_HOME, SDK

P = 'app.rose.gen.p1'
CAPABILITIES = ('<uses-permission android:name="android.permission.READ_SMS"/>'
                '<uses-permission android:name="android.permission.SYSTEM_ALERT_WINDOW"/>'
                '<uses-permission android:name="android.permission.QUERY_ALL_PACKAGES"/>'
                '<uses-permission-sdk-23 android:name="android.permission.ACCESS_BACKGROUND_LOCATION"/>')
SERVICES = ('<service android:name=".A" android:exported="true" '
            'android:permission="android.permission.BIND_ACCESSIBILITY_SERVICE"/>'
            '<service android:name=".V" android:exported="true" android:permission="android.permission.BIND_VPN_SERVICE"/>'
            '<receiver android:name=".D" android:exported="true" android:permission="android.permission.BIND_DEVICE_ADMIN"/>')


def source_with(tmp, inner='', application='', root_extra=''):
    source = Path(tmp) / 'source'
    for rel, text in template.skeleton(P, 'x').items():
        (source / rel).parent.mkdir(parents=True, exist_ok=True)
        (source / rel).write_text(text)
    os.chmod(source, 0o700)
    m = source / 'AndroidManifest.xml'
    text = m.read_text().replace('<application', inner + '<application').replace('</application>',
                                                                              application + '</application>')
    m.write_text(text.replace(f'package="{P}"', f'package="{P}"{root_extra}'))
    return source


class NoCapabilityRules(unittest.TestCase):
    def test_any_permission_or_role_passes_the_source_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            template.check(source_with(tmp, CAPABILITIES + '<permission android:name="other.app.PERM"/>', SERVICES), P)

    def test_identity_and_update_checks_still_refuse(self):
        cases = [('', '', ' android:versionCode="7"'), ('', '', ' android:sharedUserId="android.uid.system"'),
                 ('<uses-sdk android:minSdkVersion="21"/>', '', ''), ('<key-sets/>', '', ''),
                 ('<original-package android:name="com.example"/>', '', '')]
        for inner, application, root_extra in cases:
            with tempfile.TemporaryDirectory() as tmp, self.subTest(case=inner or root_extra):
                with self.assertRaises(template.TemplateRefused):
                    template.check(source_with(tmp, inner, application, root_extra), P)
        with tempfile.TemporaryDirectory() as tmp:  # a namespaced element anywhere fails closed
            source = source_with(tmp)
            m = source / 'AndroidManifest.xml'
            m.write_text(m.read_text().replace('<manifest ', '<manifest xmlns:x="http://example.com/x" ').replace(
                '<application', '<x:thing/><application'))
            with self.assertRaises(template.TemplateRefused):
                template.check(source, P)
        with tempfile.TemporaryDirectory() as tmp:
            source = source_with(tmp)
            m = source / 'AndroidManifest.xml'
            m.write_text(m.read_text().replace('<application', '<application android:debuggable="true"'))
            with self.assertRaises(template.TemplateRefused):
                template.check(source, P)


@unittest.skipUnless(HAS_SDK or os.environ.get('ROSE_REQUIRE_APP_BUILD'), 'needs the pinned Android SDK and a JDK')
class BuildsWhateverItDeclares(unittest.TestCase):
    """Compilation and signature verification with throwaway keys; nothing is installed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def declare(source, *a):
        m = source / 'AndroidManifest.xml'
        m.write_text(m.read_text().replace('<application', CAPABILITIES + '<application')
                     .replace('</application>', SERVICES + '</application>'))

    def test_an_app_with_sms_accessibility_vpn_and_device_admin_is_built_and_signed(self):
        tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=300, key_creation=True)
        b = Builder(Path(self.tmp.name) / 'b', tools, self.declare)
        self.addCleanup(b.ledger.close)
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'an SMS and VPN helper'})
        self.assertTrue(b.run_next())
        job = b.job(out['jobId'])
        self.assertEqual(job['state'], 'ready', job)
        self.assertEqual(len(b.projects()[0]['versions']), 1)


if __name__ == '__main__':
    unittest.main()
