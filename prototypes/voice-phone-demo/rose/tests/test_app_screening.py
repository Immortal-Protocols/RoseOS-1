"""Request screening before a build job exists, and the capability policy that holds whatever the request said
(docs/APP-BUILDER.md §3.6). No model, network or phone; the build tests need the pinned SDK."""
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from rose.app_builder import screening, template, toolchain
from rose.app_builder.service import Builder, Refused
from rose.tests.test_app_builder import HAS_SDK, JAVA_HOME, SDK

REFUSED = {
    'platform': ['An iPhone app for my grocery list', 'make me a website for my bakery', 'Build a web app for notes',
                 'a Chrome extension that blocks ads', 'a Windows app to rename files', 'a Wear OS watch face',
                 'an Android TV app for photos', 'ＩＰＨＯＮＥ app'],
    'capability': ['an accessibility service that taps buttons for me', 'make my app a device admin',
                   'read my SMS and forward them to email', 'record phone calls automatically', 'a call log viewer',
                   'a VPN that blocks trackers', 'a floating bubble over other apps', 'read all my notifications',
                   'a custom keyboard app', 'an app lock for other apps', 'block other apps after 9pm',
                   'a system app with system permissions', 'needs root access', 'use the platform key'],
    'harm': ['spy on my wife', 'read my girlfriend’s messages', 'secretly record audio in the room',
             'track my son\'s location without him knowing', 'a hidden camera app', 'a keylogger',
             'a fake bank login page', 'capture other people\'s passwords', 'an app that looks exactly like the '
             'PayPal sign-in screen', 'write a virus', 'ransomware for a prank'],
}
ACCEPTED = ['A tip calculator', 'I spy game for kids', 'A secret diary app that only I can read',
            'photo overlay frames app', 'a template app for text messages I copy and paste',
            'limit my screen time with a simple timer', 'build an app that opens my favorite website',
            'a log in my diary of what I ate', 'track my own runs with GPS', 'a biology quiz about viruses',
            'a flashlight with SOS', 'a stopwatch with laps', 'a habit tracker that reminds me at 8pm',
            'a recipe box with photos from my camera', 'a server tip calculator', 'a timer for my steak']


class Screen(unittest.TestCase):
    def test_refusals_by_category(self):
        for category, texts in REFUSED.items():
            for text in texts:
                r = screening.screen(text)
                self.assertEqual((r['ok'], r.get('category')), (False, category), text)
                self.assertTrue(r['message'].endswith('Nothing was started.'), text)
                self.assertNotIn(text.lower(), r['message'].lower())  # never echoes the request

    def test_ordinary_requests_pass(self):
        for text in ACCEPTED:
            self.assertTrue(screening.screen(text)['ok'], text)

    def test_notes_say_what_happens_instead(self):
        r = screening.screen('A notes app with Firebase and cloud sync')
        self.assertTrue(r['ok'])
        self.assertEqual(len(r['notes']), 2)
        self.assertIn('firebase', r['notes'][0])
        self.assertIn('only on this phone', r['notes'][1])
        self.assertEqual(screening.screen('A tip calculator')['notes'], [])

    def test_normalization(self):
        self.assertEqual(screening.normalize('Ａ ’VPN’\n\tapp​'), "a 'vpn' app")
        # A zero-width character becomes a space, so a split word does not match: the screen is a courtesy, and the
        # manifest policy below is what holds.
        self.assertTrue(screening.screen('a V​PN')['ok'])


class BuilderScreening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        self.b = Builder(Path(self.tmp.name), toolchain.Tools(SDK), generate=lambda *a: self.calls.append(a))
        self.addCleanup(self.b.ledger.close)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_refused_create_records_nothing_and_starts_nothing(self):
        with self.assertRaises(Refused) as cm:
            self.b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'a VPN app'})
        self.assertEqual(cm.exception.details, {'code': 'out_of_scope', 'category': 'capability'})
        self.assertEqual(self.b.projects(), [])
        self.assertFalse(self.b.run_next())
        self.assertEqual(self.calls, [])

    def test_accepted_requests_carry_notes_and_a_refused_revise_adds_no_job(self):
        out = self.b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'chess with Firebase'})
        self.assertEqual(set(out), {'projectId', 'jobId', 'notes'})
        self.assertEqual(len(out['notes']), 1)
        self.b.ledger.finish_failed(out['jobId'], 'internal', 'x', True)
        with self.assertRaises(Refused) as cm:
            self.b.revise('d', out['projectId'], {'schemaVersion': 1, 'clientRequestId': 'r2',
                                                  'request': 'also make it an iPhone app'})
        self.assertEqual(cm.exception.details['category'], 'platform')
        self.assertFalse(self.b.ledger.known_request('d', 'r2'))
        self.assertIsNone(self.b.projects()[0]['activeJob'])

    def test_an_accepted_request_stays_idempotent_if_the_rules_change(self):
        body = {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'a stopwatch'}
        first = self.b.create('d', body)
        refuse_all = lambda text: {'ok': False, 'category': 'harm', 'message': 'no', 'notes': []}
        with mock.patch.object(screening, 'screen', refuse_all):
            again = self.b.create('d', body)  # the retry of an accepted request returns the same ids
            with self.assertRaises(Refused):
                self.b.create('d', {**body, 'clientRequestId': 'r2'})
        self.assertEqual((again['projectId'], again['jobId']), (first['projectId'], first['jobId']))


def manifest(package, inner, application=''):
    return ET.fromstring(f'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{package}">'
                         f'{inner}<application>{application}</application></manifest>')


class Capabilities(unittest.TestCase):
    P = 'app.rose.gen.p1'

    def check(self, inner='', application=''):
        template.check_capabilities(manifest(self.P, inner, application), self.P)

    def test_allowed(self):
        self.check('<uses-permission android:name="android.permission.INTERNET"/>'
                   '<uses-permission android:name="android.permission.CAMERA" android:maxSdkVersion="40"/>'
                   f'<uses-permission android:name="{self.P}.MINE"/><permission android:name="{self.P}.MINE"/>',
                   '<service android:name=".J" android:permission="android.permission.BIND_JOB_SERVICE"/>'
                   f'<provider android:name=".P" android:authorities="a" android:readPermission="{self.P}.MINE"/>')

    def test_refused(self):
        cases = [
            ('<uses-permission android:name="android.permission.READ_SMS"/>', ''),
            ('<uses-permission android:name="android.permission.SYSTEM_ALERT_WINDOW"/>', ''),
            ('<uses-permission android:name="android.permission.QUERY_ALL_PACKAGES"/>', ''),
            ('<uses-permission android:name="android.permission.ACCESS_BACKGROUND_LOCATION"/>', ''),
            ('<uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES"/>', ''),
            ('<uses-permission android:name="dev.pixel.voice.permission.CONTROL"/>', ''),
            ('<uses-permission android:name="@string/p"/>', ''),
            ('<uses-permission/>', ''),
            (f'<uses-permission android:name="{self.P}"/>', ''),  # the package itself is not an own permission
            (f'<uses-permission android:name="{self.P}x.Y"/>', ''),
            ('<permission android:name="android.permission.MINE"/>', ''),
            ('', '<service android:name=".A" android:permission="android.permission.BIND_ACCESSIBILITY_SERVICE"/>'),
            ('', '<receiver android:name=".D" android:permission="android.permission.BIND_DEVICE_ADMIN"/>'),
            ('', '<service android:name=".V" android:permission="android.permission.BIND_VPN_SERVICE"/>'),
            ('', '<service android:name=".N" '
                 'android:permission="android.permission.BIND_NOTIFICATION_LISTENER_SERVICE"/>'),
            ('', '<service android:name=".K" android:permission="android.permission.BIND_INPUT_METHOD"/>'),
            ('', '<provider android:name=".P" android:authorities="a" '
                 'android:writePermission="android.permission.INTERNET"/>'),
            ('', '<activity android:name=".M" android:permission="?attr/p"/>'),
        ]
        for inner, application in cases:
            with self.assertRaises(template.TemplateRefused, msg=inner + application):
                self.check(inner, application)

    def test_template_check_applies_the_policy_before_any_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'
            for rel, text in template.skeleton(self.P, 'x').items():
                (source / rel).parent.mkdir(parents=True, exist_ok=True)
                (source / rel).write_text(text)
            os.chmod(source, 0o700)
            template.check(source, self.P)
            m = source / 'AndroidManifest.xml'
            m.write_text(m.read_text().replace('<application', '<uses-permission '
                                               'android:name="android.permission.READ_CALL_LOG"/><application'))
            with self.assertRaises(template.TemplateRefused) as cm:
                template.check(source, self.P)
            self.assertIn('READ_CALL_LOG', str(cm.exception))

    BINARY = '''N: android=http://schemas.android.com/apk/res/android
  E: manifest (line=2)
    E: uses-permission (line=3)
      A: android:name(0x01010003)="{perm}" (Raw: "{perm}")
    E: application (line=4)
      A: android:label(0x01010001)=@0x7f010000
      E: service (line=5)
        A: android:name(0x01010003)=".S" (Raw: ".S")
        A: android:permission(0x01010006){service}
'''

    def binary(self, perm='android.permission.INTERNET', service='="android.permission.BIND_JOB_SERVICE" (Raw: "x")'):
        return toolchain.binary_permission_problem(self.BINARY.format(perm=perm, service=service), self.P)

    def test_built_manifest(self):
        self.assertIsNone(self.binary())
        self.assertIn('READ_SMS', self.binary(perm='android.permission.READ_SMS'))
        self.assertIn('BIND_ACCESSIBILITY_SERVICE', self.binary(
            service='="android.permission.BIND_ACCESSIBILITY_SERVICE" (Raw: "x")'))
        self.assertIsNotNone(self.binary(service='=@0x7f020000'))  # a reference, whatever it resolves to
        self.assertIsNone(self.binary(service=f'="{self.P}.MINE" (Raw: "x")'))


@unittest.skipUnless(HAS_SDK or os.environ.get('ROSE_REQUIRE_APP_BUILD'), 'needs the pinned Android SDK and a JDK')
class BuildPolicy(unittest.TestCase):
    """A writer that adds a refused capability gets a failed job, whether the source check or the built-APK check
    catches it. Compilation only; nothing is signed or installed by a refused job."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def accessibility(source, *a):
        m = source / 'AndroidManifest.xml'
        m.write_text(m.read_text().replace('</application>', '<service android:name=".A" android:exported="true" '
                     'android:permission="android.permission.BIND_ACCESSIBILITY_SERVICE"/></application>'))

    def run_job(self):
        tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=300, key_creation=True)
        b = Builder(Path(self.tmp.name) / 'b', tools, self.accessibility)
        self.addCleanup(b.ledger.close)
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'a stopwatch'})
        b.run_next()
        return b, b.job(out['jobId'])

    def test_source_check_refuses(self):
        b, job = self.run_job()
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'compile'), job)
        self.assertIn('BIND_ACCESSIBILITY_SERVICE', job['failure']['summary'])
        self.assertEqual(b.projects()[0]['versions'], [])

    def test_built_apk_check_refuses_when_the_source_check_is_bypassed(self):
        with mock.patch.object(template, 'check_capabilities', lambda *a: None):
            b, job = self.run_job()
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'compile'), job)
        self.assertIn('BIND_ACCESSIBILITY_SERVICE', job['failure']['summary'])
        self.assertEqual(b.projects()[0]['versions'], [])


if __name__ == '__main__':
    unittest.main()
