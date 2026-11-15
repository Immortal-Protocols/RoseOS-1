#!/usr/bin/env python3
"""RoseOS's system authenticator (rose-signin-v1 D3/D8), read from source and compiled against the platform.

The vault runs on the JVM in os/tests/rose-authenticator/VaultTest.java. This file pins what that cannot see: the
package asks for no permission (no network) and keeps nothing in backups; the one activity claims otpauth://totp
links and the launcher; codes are never screenshotted; the shared client core is the rose-signin source byte for
byte; and the sources compile against the Android platform jar.
"""
import hashlib
import os
import re
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
    def test_no_network_permission_and_no_backup(self):
        manifest = (APP/'AndroidManifest.xml').read_text()
        self.assertIn('package="dev.pixel.rose.authenticator"', manifest)
        import re
        self.assertEqual(re.findall(r'<uses-permission android:name="([^"]+)"', manifest),
                         ['android.permission.HIDE_OVERLAY_WINDOWS'], 'only overlay hiding: no network, nothing else')
        self.assertEqual(manifest.count('<uses-permission'), 1)
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
        # App overlays are hidden over the activity and both dialogs; partly covered touches are ignored; the delay
        # starts again whenever the dialog window regains focus (a floating window does not pause the activity).
        self.assertEqual(activity.count('setHideOverlayWindows(true)'), 3)
        self.assertIn('FLAG_WINDOW_IS_PARTIALLY_OBSCURED', activity)
        self.assertIn('d.setMessage(message + COVERED)', activity, 'a dropped touch says why (PiP is not hidden)')
        self.assertIn('addOnWindowFocusChangeListener', activity)
        # The delay is re-armed whenever the activity is shown again, and stopped while it is covered.
        self.assertIn('tick.removeCallbacks(enable);', activity)
        self.assertIn('arm();\n    }\n\n    @Override protected void onPause()', activity)
        self.assertIn('disarm();\n        super.onPause();', activity)
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



class RoseAuthenticatorProductTest(unittest.TestCase):
    """The authenticator is in the product image: one PRODUCT_PACKAGES line beside the stock apps, registered with
    the Rose product's other build/make patches and mirrored by host reconcile. Source inclusion only."""
    PATCH = ROOT/'os/patches/rose-authenticator-build-make.patch'
    KEYBOARD = ROOT/'os/patches/rose-keyboard-build-make.patch'
    PINNED = ROOT/'os/tests/fixtures/cuttlefish-alpha/build-make/target/product/handheld_product.mk'

    def test_the_patch_adds_one_product_package_and_removes_nothing(self):
        text = self.PATCH.read_text()
        self.assertEqual(re.findall(r'^\+\+\+ b/(\S+)', text, re.M), ['target/product/handheld_product.mk'])
        added = [l for l in text.splitlines() if l.startswith('+') and not l.startswith('+++')]
        removed = [l for l in text.splitlines() if l.startswith('-') and not l.startswith('---')]
        self.assertEqual(added, ['+    RoseAuthenticator \\'])
        self.assertEqual(removed, [])

    def test_applies_to_the_pinned_makefile_after_the_keyboard(self):
        pinned = (ROOT/'os/pinned-manifest.xml').read_text()
        self.assertIn('path="build/make" remote="grapheneos" revision="b0f697df8cc9519408ca0a404e918cd51bde0735"', pinned)
        with tempfile.TemporaryDirectory(prefix='rose-authenticator-mk-') as d:
            tree = Path(d)
            target = tree/'target/product/handheld_product.mk'
            target.parent.mkdir(parents=True)
            target.write_bytes(self.PINNED.read_bytes())
            git = lambda *args: subprocess.run(['git', '-C', d, *args], capture_output=True, text=True)
            git('init', '-q')
            self.assertEqual(git('hash-object', str(target)).stdout.strip()[:7], '2a4602d', 'the pinned blob')
            # Alone and in the product sequence (after the keyboard, which edits the same file).
            self.assertEqual(git('apply', '--check', str(self.PATCH)).returncode, 0)
            for patch in (self.KEYBOARD, self.PATCH):
                r = git('apply', str(patch))
                self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(git('hash-object', str(target)).stdout.strip()[:7], '8fd097b')
            self.assertIn('index 4b66c5d..8fd097b', self.PATCH.read_text(), 'the blob chain after the keyboard')
            block = target.read_text().split('PRODUCT_PACKAGES += \\\n', 1)[1].split('\n\n', 1)[0]
            self.assertEqual(block.count('    RoseAuthenticator \\\n'), 1)
            self.assertEqual(block.count('    RoseKeyboard \\\n'), 1)

    def test_registered_in_the_product_build_after_the_keyboard_and_mirrored_by_reconcile(self):
        patcher = (ROOT/'scripts/apply-os-changes.py').read_text()
        line = "    projects['build/make'].append('rose-authenticator-build-make.patch')"
        self.assertEqual(patcher.count('rose-authenticator-build-make.patch'), 1)
        self.assertIn(line, patcher)
        self.assertLess(patcher.index("projects['build/make'].append('rose-keyboard-build-make.patch')"),
                        patcher.index(line))
        # Inside the background-policy (RoseOS product) block, like the keyboard: indented under `if a.background_policy:`.
        policy = patcher.index('if a.background_policy:')
        self.assertLess(policy, patcher.index(line))
        self.assertLess(patcher.index(line), patcher.index('preinstalls = preinstall_plan('))
        reconcile = (ROOT/'scripts/host-reconcile.py').read_text()
        self.assertIn("'rose-keyboard-build-make.patch',\n        'rose-authenticator-build-make.patch'),", reconcile)

    def test_the_overlay_is_copied_into_every_build(self):
        # apply-os-changes.py copies every overlay except the background and Updater ones; the app is neither.
        patcher = (ROOT/'scripts/apply-os-changes.py').read_text()
        for skipped in ("'packages/apps/PixelBackgroundControl/'", "'packages/apps/Updater/'"):
            self.assertIn(skipped, patcher)
        self.assertFalse(str(APP.relative_to(ROOT/'os/overlays')).startswith(
            ('packages/apps/PixelBackgroundControl/', 'packages/apps/Updater/')))
        self.assertTrue((APP/'Android.bp').is_file())
        self.assertIn('name: "RoseAuthenticator"', (APP/'Android.bp').read_text(), 'the module the product lists')


if __name__ == '__main__':
    unittest.main()
