#!/usr/bin/env python3
"""1M: the Rose keyboard's package, build inclusion and boundaries, read from source.

The model (RoseKeys, ported from the selected design's prototypes/rose-keyboard/src/keyboard.js at 818a032) runs its
own 37-check specification in os/tests/rose-keyboard/RoseKeysTest.java; the session and view run on Robolectric in
os/tests/native-rose-keyboard (opt-in). This file pins what neither can see: the package asks for no permission and
has no microphone, network or clipboard access; the normal OS build includes it beside the stock keyboard; and the one
default selection lives in Launcher3's existing edge window class, so no compile-contract input changes.
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT/'os/overlays/packages/apps/RoseKeyboard'
SRC = APP/'src/dev/pixel/rose/keyboard'
PATCH = ROOT/'os/patches/rose-keyboard-build-make.patch'
WINDOW = ROOT/'os/overlays/packages/apps/Launcher3/quickstep/src/com/android/quickstep/fallback/RoseEdgeWindow.java'


class RoseKeyboardPackageTest(unittest.TestCase):
    def test_one_input_method_service_and_no_permission(self):
        manifest = (APP/'AndroidManifest.xml').read_text()
        self.assertIn('package="dev.pixel.rose.keyboard"', manifest)
        self.assertNotIn('<uses-permission', manifest, 'the keyboard asks for no permission')
        self.assertNotIn('<activity', manifest)
        self.assertEqual(manifest.count('<service'), 1)
        self.assertIn('android:permission="android.permission.BIND_INPUT_METHOD"', manifest)
        self.assertIn('<action android:name="android.view.InputMethod" />', manifest)
        self.assertIn('android:directBootAware="true"', manifest, 'it types the first unlock password too')
        method = (APP/'res/xml/method.xml').read_text()
        self.assertIn('android:imeSubtypeMode="keyboard"', method)
        self.assertIn('android:isDefault="true"', method)

    def test_built_from_source_with_platform_apis_only(self):
        bp = (APP/'Android.bp').read_text()
        self.assertIn('android_app {', bp)
        self.assertIn('name: "RoseKeyboard"', bp)
        self.assertIn('sdk_version: "current"', bp)
        self.assertIn('product_specific: true', bp)
        self.assertNotIn('certificate:', bp, 'the build signs it like any app; never the platform key')
        self.assertNotIn('static_libs', bp)
        self.assertNotIn('privileged', bp)

    def test_no_microphone_network_or_clipboard(self):
        code = '\n'.join(p.read_text() for p in sorted(SRC.glob('*.java')))
        for forbidden in ('AudioRecord', 'MediaRecorder', 'SpeechRecognizer', 'java.net', 'HttpURLConnection',
                'ClipboardManager', 'Settings.Secure', 'SharedPreferences'):
            self.assertNotIn(forbidden, code, forbidden)
        layouts = (SRC/'RoseKeys.java').read_text()
        self.assertNotIn('"mic"', layouts, 'no keyboard microphone: voice and mute stay on the Rose chin')
        # Cut, Copy, Paste, Select all, Undo and Redo are the app's own context actions.
        view = (SRC/'RoseKeyboardView.java').read_text()
        for action in ('cut', 'copy', 'paste', 'selectAll', 'undo', 'redo'):
            self.assertIn(f'android.R.id.{action}', view)

    def test_the_design_geometry(self):
        view = (SRC/'RoseKeyboardView.java').read_text()
        self.assertIn('STRIP_DP = 44, KEY_DP = 46, ROW_GAP_DP = 8, KEY_GAP_DP = 6, PAD_DP = 8', view)
        for token in ('0xFF3A2A2E', '0xFF2C1F22', '0xFF1E1417', '0xFF1D1114', '0xFF150C0F', '0xFFC8304A',
                '0xFF97182E', '0xFFF2E6D9', '0xFFE04A62'):
            self.assertIn(token, view, token)
        model = (SRC/'RoseKeys.java').read_text()
        self.assertIn('row("q w e r t y u i o p")', model, 'letters first: no number row')


class RoseKeyboardBuildTest(unittest.TestCase):
    def test_the_product_lists_it_beside_the_stock_keyboard(self):
        text = PATCH.read_text()
        self.assertEqual(re.findall(r'^\+\+\+ b/(\S+)', text, re.M), ['target/product/handheld_product.mk'])
        added = [l for l in text.splitlines() if l.startswith('+') and not l.startswith('+++')]
        removed = [l for l in text.splitlines() if l.startswith('-') and not l.startswith('---')]
        self.assertEqual(added, ['+    RoseKeyboard \\'])
        self.assertEqual(removed, [], 'LatinIME stays installed as the fallback')
        self.assertIn('     LatinIME \\', text)

    def test_registered_in_the_background_policy_build_and_mirrored_by_reconcile(self):
        patcher = (ROOT/'scripts/apply-os-changes.py').read_text()
        line = "    projects['build/make'].append('rose-keyboard-build-make.patch')"
        self.assertEqual(patcher.count('rose-keyboard-build-make.patch'), 1)
        self.assertIn(line, patcher)
        self.assertLess(patcher.index("projects['build/make'].append('rose-edge-build-make.patch')"),
            patcher.index(line))
        reconcile = (ROOT/'scripts/host-reconcile.py').read_text()
        self.assertIn("'rose-edge-build-make.patch', 'rose-keyboard-build-make.patch')", reconcile)

    def test_the_patch_applies_to_the_pinned_makefile(self):
        pinned = ROOT/'os/pinned-manifest.xml'
        self.assertIn('path="build/make" remote="grapheneos" revision="b0f697df8cc9519408ca0a404e918cd51bde0735"',
            pinned.read_text(), 'the patch was authored on this revision (blob 2a4602d)')
        self.assertIn('index 2a4602d..', PATCH.read_text())


class RoseKeyboardDefaultTest(unittest.TestCase):
    def test_selected_once_from_the_edge_window_with_existing_permission(self):
        window = WINDOW.read_text()
        self.assertIn('KeyboardDefault.applyOnce(mContext);', window)
        self.assertIn('static final String ID = PACKAGE + "/.RoseKeyboardService";', window)
        self.assertIn('(app.flags & ApplicationInfo.FLAG_SYSTEM) != 0', window, 'only a system-installed copy')
        self.assertIn('createDeviceProtectedStorageContext()', window)
        # No new Launcher3 overlay file: the compile contract's inputs and digest stay as they are.
        result = subprocess.run(['python3', str(ROOT/'scripts/launcher3-compile-contract.py')], capture_output=True,
            text=True)
        self.assertEqual(result.returncode, 0, result.stdout[-600:])


class RoseKeyboardModelTest(unittest.TestCase):
    def test_the_designs_37_model_checks(self):
        with tempfile.TemporaryDirectory() as out:
            subprocess.run(['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '8', '-target', '8',
                '-Xlint:-options', '-encoding', 'UTF-8', '-d', out, str(SRC/'RoseKeys.java'), str(SRC/'FieldEdit.java'),
                str(ROOT/'os/tests/rose-keyboard/RoseKeysTest.java')], check=True, capture_output=True)
            result = subprocess.run(['java', '-cp', out, 'dev.pixel.rose.keyboard.RoseKeysTest'], capture_output=True,
                text=True)
        self.assertEqual(result.returncode, 0, result.stdout[-800:])
        self.assertIn('PASS the same 37 checks as model-check.cjs', result.stdout)


if __name__ == '__main__':
    unittest.main()
