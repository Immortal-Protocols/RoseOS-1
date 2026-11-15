#!/usr/bin/env python3
"""RoseOS's system authenticator (rose-signin-v1 D3/D8), read from source and compiled against the platform.

The vault runs on the JVM in os/tests/rose-authenticator/VaultTest.java. This file pins what that cannot see: the
package asks for no permission (no network) and keeps nothing in backups; the one activity claims otpauth://totp
links and the launcher; codes are never screenshotted; the shared client core is the rose-signin source byte for
byte; and the sources compile against the Android platform jar.
"""
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT/'os/overlays/packages/apps/RoseAuthenticator'
SRC = APP/'src'
CORE = ROOT/'workspace/rose-signin/java/src/dev/pixel/rosesignin/RoseSigninCore.java'
ANDROID_JAR = Path(os.environ.get('ANDROID_SDK_ROOT', '/opt/roseos/android-sdk'))/'platforms/android-35/android.jar'


class RoseAuthenticatorPackageTest(unittest.TestCase):
    def test_no_permission_and_no_backup(self):
        manifest = (APP/'AndroidManifest.xml').read_text()
        self.assertIn('package="dev.pixel.rose.authenticator"', manifest)
        self.assertNotIn('<uses-permission', manifest, 'no permission: no network, nothing else')
        self.assertIn('android:allowBackup="false"', manifest)
        self.assertIn('android:fullBackupContent="false"', manifest)
        rules = (APP/'res/xml/no_extraction.xml').read_text()
        self.assertEqual(rules.count('<exclude domain="root" />'), 2, 'neither cloud backup nor device transfer')
        self.assertNotIn('<provider', manifest)
        self.assertNotIn('<service', manifest)
        self.assertNotIn('<receiver', manifest)

    def test_one_activity_for_the_launcher_and_otpauth_links(self):
        manifest = (APP/'AndroidManifest.xml').read_text()
        self.assertEqual(manifest.count('<activity'), 1)
        self.assertIn('<action android:name="android.intent.action.VIEW" />', manifest)
        self.assertIn('<data android:scheme="otpauth" android:host="totp" />', manifest)
        self.assertIn('<category android:name="android.intent.category.LAUNCHER" />', manifest)

    def test_built_from_source_with_platform_apis_only(self):
        bp = (APP/'Android.bp').read_text()
        self.assertIn('name: "RoseAuthenticator"', bp)
        self.assertIn('sdk_version: "current"', bp)
        self.assertIn('product_specific: true', bp)
        self.assertNotIn('certificate:', bp, 'signed like any app; never the platform key')
        self.assertNotIn('static_libs', bp)
        self.assertNotIn('privileged', bp)

    def test_secrets_stay_sealed_on_the_device(self):
        # The app's own package (the shared core is pinned to the reviewed rose-signin source below, and with no
        # permission the app cannot open a connection at all).
        code = '\n'.join(p.read_text() for p in sorted((SRC/'dev/pixel/rose/authenticator').glob('*.java')))
        for forbidden in ('java.net', 'HttpURLConnection', 'Socket', 'SharedPreferences', 'Log.', 'printStackTrace'):
            self.assertNotIn(forbidden, code, forbidden)
        sealer = (SRC/'dev/pixel/rose/authenticator/KeystoreSealer.java').read_text()
        self.assertIn('"AndroidKeyStore"', sealer)
        self.assertIn('setUnlockedDeviceRequired(true)', sealer, 'no codes while the phone is locked')
        self.assertIn('setKeySize(256)', sealer)
        self.assertIn('updateAAD(associatedData)', sealer)
        activity = (SRC/'dev/pixel/rose/authenticator/AuthenticatorActivity.java').read_text()
        self.assertIn('FLAG_SECURE', activity, 'no screenshots or recents thumbnails of codes')
        self.assertIn('EXTRA_IS_SENSITIVE', activity, 'a copied code is marked sensitive')
        self.assertIn('RoseSigninCore.parseOtpAuth(', activity, 'only the v1 profile enrolls')

    def test_enrollment_is_never_lost_overwritten_or_tapjacked(self):
        activity = (SRC/'dev/pixel/rose/authenticator/AuthenticatorActivity.java').read_text()
        # Two instances (launcher and a link in another app's task) share one vault file: every change re-reads it
        # under one process-wide lock, so neither writes back a stale copy.
        self.assertIn('private static final Object LOCK', activity)
        self.assertIn('Vault fresh = load(this);', activity)
        self.assertNotIn('vault.add(', activity, 'adds go through change(), which re-reads the file')
        # A link never replaces an existing account's secret.
        self.assertIn('A link cannot replace it.', activity)
        self.assertNotIn('Replace', activity)
        # The sender is named; the confirm button ignores obscured touches and starts disabled.
        # The sender is the one Android vouches for (getReferrer() is whatever the caller says), shown in the title.
        self.assertIn('getLaunchedFromPackage()', activity)
        self.assertNotIn('getReferrer', activity)
        self.assertIn('Vault.displayable(e.issuer)', activity, 'link-supplied names cannot fake dialog lines')
        self.assertIn('setFilterTouchesWhenObscured(true)', activity)
        # The delay is re-armed whenever the activity is shown again, and stopped while it is covered.
        self.assertIn('tick.removeCallbacks(enable);', activity)
        self.assertIn('arm();\n    }\n\n    @Override protected void onPause()', activity)
        # A link to a resumed activity is asked about at once; a Recents relaunch does not replay its link.
        self.assertIn('if (resumed) ask();', activity)
        self.assertIn('FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY', activity)
        # A pending link survives recreation, and the launch link is taken once.
        self.assertIn('out.putString(STATE_PENDING, pending);', activity)
        self.assertIn('if (!launchHandled)', activity)
        self.assertNotIn('setData(null)', activity)
        # Rows are built per vault change; each tick only updates text, so long presses survive.
        self.assertIn('updateCodes();\n            long nowMs', activity)
        # An unreadable file of any kind is set aside, never crashes the app or gets overwritten.
        self.assertIn('catch (IOException | RuntimeException bad)', activity)

    def test_the_client_core_is_the_rose_signin_source_byte_for_byte(self):
        copy = SRC/'dev/pixel/rosesignin/RoseSigninCore.java'
        self.assertEqual(hashlib.sha256(copy.read_bytes()).hexdigest(), hashlib.sha256(CORE.read_bytes()).hexdigest(),
                         'copy workspace/rose-signin/java/.../RoseSigninCore.java here unchanged')

    def test_compiles_against_the_platform_and_the_vault_passes(self):
        if not ANDROID_JAR.is_file():
            self.skipTest('Android platform jar not installed')
        out = Path(tempfile.mkdtemp(prefix='rose-authenticator-'))
        srcs = [str(p) for p in sorted(SRC.rglob('*.java'))]
        r = subprocess.run(['javac', '-source', '11', '-target', '11', '-Xlint:-options', '-cp', str(ANDROID_JAR),
                            '-d', str(out / 'app')] + srcs, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        pure = [str(SRC/'dev/pixel/rosesignin/RoseSigninCore.java'), str(SRC/'dev/pixel/rose/authenticator/Vault.java'),
                str(ROOT/'os/tests/rose-authenticator/VaultTest.java')]
        r = subprocess.run(['javac', '-source', '11', '-target', '11', '-Xlint:-options', '-d', str(out / 'jvm')] + pure,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run(['java', '-cp', str(out / 'jvm'), 'dev.pixel.rose.authenticator.VaultTest'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('checks passed', r.stdout)


if __name__ == '__main__':
    unittest.main()
