"""#156: Rose Agent's work row in Launcher3's fallback overview. Applies os/patches/rose-switcher-launcher3.patch to
the pinned fallback layout (Launcher3 47accce), runs the pure RoseAgentWork decisions, and checks the view's
boundaries. Source and fixture logic only: no Launcher3 build, emulator or device."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT/'os/tests/fixtures/rose-switcher'
PATCH = ROOT/'os/patches/rose-switcher-launcher3.patch'
OVERLAY = ROOT/'os/overlays/packages/apps/Launcher3/quickstep/src/com/android/quickstep/fallback'
LAYOUT = 'quickstep/res/layout/fallback_recents_activity.xml'
PINNED = {'fallback_recents_activity.xml': '8df1879fb83e2a9f5b11b657a498e6153504171cb702f859d136f37ab968a9f7',
    'OverviewComponentObserver.java': 'a94703d973e14ba8e1f43ab07d7eacaa0e32f8b5668d16bff1c6843dd4877893'}

class RoseSwitcherTest(unittest.TestCase):
    def test_fixtures_are_the_pinned_files_and_rose_home_gets_the_fallback_overview(self):
        for name, digest in PINNED.items():
            self.assertEqual(hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest(), digest, name)
        observer = (FIXTURES/'OverviewComponentObserver.java').read_text()
        self.assertIn('// The default home app is a different launcher. Use the fallback Overview instead.', observer)
        self.assertIn('mOverviewIntent = mFallbackIntent;', observer)

    def test_the_patch_applies_cleanly_and_adds_only_the_agent_row(self):
        tree = Path(tempfile.mkdtemp(prefix='launcher3-'))
        self.addCleanup(shutil.rmtree, tree, True)
        (tree/LAYOUT).parent.mkdir(parents=True)
        shutil.copyfile(FIXTURES/'fallback_recents_activity.xml', tree/LAYOUT)
        subprocess.run(['git', 'init', '-q', str(tree)], check=True)
        check = subprocess.run(['git', '-C', str(tree), 'apply', '--check', str(PATCH)], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        subprocess.run(['git', '-C', str(tree), 'apply', str(PATCH)], check=True)
        layout = (tree/LAYOUT).read_text()
        self.assertIn('<com.android.quickstep.fallback.RoseAgentStrip', layout)
        self.assertLess(layout.index('android:id="@id/overview_panel"'), layout.index('RoseAgentStrip'),
            'the human carousel stays; the agent row is added beside it')
        removed = [l for l in PATCH.read_text().splitlines() if l.startswith('-') and not l.startswith('---')]
        self.assertEqual(removed, [], 'nothing of the stock overview is removed')

    def test_agent_work_decisions(self):
        out = Path(tempfile.mkdtemp(prefix='rose-agent-work-'))
        self.addCleanup(shutil.rmtree, out, True)
        subprocess.run(['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '11', '-target', '11',
            '-Xlint:-options', '-d', str(out), str(OVERLAY/'RoseAgentWork.java'), str(ROOT/'os/tests/RoseAgentWorkTest.java')],
            check=True)
        result = subprocess.run(['java', '-cp', str(out), 'com.android.quickstep.fallback.RoseAgentWorkTest'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_view_compiles_against_the_sdk_plus_the_platform_members_it_uses(self):
        """Launcher3 Quickstep builds with platform_apis: true. These stubs declare only the hidden or SystemApi
        members RoseAgentStrip uses, with their pinned signatures; everything else comes from the public SDK."""
        import os
        sdk = os.environ.get('ANDROID_HOME') or '/opt/roseos/android-sdk'
        jars = sorted(Path(sdk).glob('platforms/android-*/android.jar'))
        if not jars:
            self.skipTest('no Android SDK platform')
        stubs = {
            'android/app/ActivityTaskManager.java': 'package android.app; public class ActivityTaskManager { '
                'public static ActivityTaskManager getInstance(){return null;} '
                'public java.util.List<ActivityManager.RunningTaskInfo> getTasks(int maxNum){return null;} }',
            'android/app/ActivityManager.java': 'package android.app; public class ActivityManager { '
                'public static int getCurrentUser(){return 0;} public static class RunningTaskInfo extends TaskInfo {} }',
            'android/app/TaskInfo.java': 'package android.app; public class TaskInfo { public int userId, taskId, '
                'displayId; public android.content.ComponentName topActivity; }',
            'android/view/Display.java': 'package android.view; public final class Display { '
                'public int getDisplayId(){return 0;} public int getOwnerUid(){return 0;} }',
            'android/content/pm/PackageManager.java': 'package android.content.pm; public abstract class PackageManager { '
                'public static class NameNotFoundException extends Exception {} '
                'public abstract int getPackageUidAsUser(String p, int u) throws NameNotFoundException; '
                'public abstract ApplicationInfo getApplicationInfoAsUser(String p, int f, int u) throws NameNotFoundException; '
                'public abstract CharSequence getApplicationLabel(ApplicationInfo i); }',
            'android/content/Context.java': 'package android.content; public abstract class Context { '
                'public abstract android.content.pm.PackageManager getPackageManager(); '
                'public abstract <T> T getSystemService(Class<T> c); '
                'public void startActivityAsUser(Intent i, android.os.UserHandle u) {} '
                'public abstract android.content.res.Resources getResources(); }',
            'android/os/UserHandle.java': 'package android.os; public final class UserHandle { '
                'public static UserHandle of(int u){return null;} }',
            'com/android/launcher3/util/Executors.java': 'package com.android.launcher3.util; public class Executors { '
                'public static final java.util.concurrent.Executor UI_HELPER_EXECUTOR = Runnable::run; }',
        }
        tmp = Path(tempfile.mkdtemp(prefix='rose-strip-compile-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        for relative, code in stubs.items():
            (tmp/'src'/relative).parent.mkdir(parents=True, exist_ok=True)
            (tmp/'src'/relative).write_text(code)
        result = subprocess.run(['javac', '-source', '11', '-target', '11', '-Xlint:-options', '-implicit:none',
            '-sourcepath', str(tmp/'src'), '-cp', str(jars[-1]), '-d', str(tmp/'out'),
            str(OVERLAY/'RoseAgentStrip.java'), str(OVERLAY/'RoseAgentWork.java')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_view_uses_only_existing_authority_and_opens_only_the_current_work(self):
        strip = (OVERLAY/'RoseAgentStrip.java').read_text()
        for banned in ('AgentProvider', 'getContentResolver', 'content://', 'requestPermissions', 'grantRuntimePermission',
                'checkSignatures', 'startActivityFromRecents', 'moveTaskToFront', 'dev.pixel.voice'):
            self.assertNotIn(banned, strip, banned)
        self.assertIn('ActivityTaskManager.getInstance().getTasks(64)', strip)
        self.assertIn('d.getOwnerUid()', strip)
        select = strip[strip.index('private void select('):strip.index('private TextView text(')]
        # #156 M004: the tap re-reads off the UI thread, the newest tap wins, the decision uses that fresh read, and
        # only a selection request (display + package) is sent: never an unbound viewer, never a task id as a Rose id.
        self.assertIn('int token = ++selection;', select)
        self.assertIn('UI_HELPER_EXECUTOR.execute(() -> {\n            RoseAgentWork fresh = read(context);', select)
        self.assertIn('if (token != selection) return;', select)
        self.assertLess(select.index('RoseAgentWork.decide(card, fresh)'), select.index('startActivityAsUser'))
        self.assertIn('if (notice != null) {', select)
        self.assertIn('.putExtra(RoseAgentWork.EXTRA_SELECT_DISPLAY, card.displayId)', select)
        self.assertIn('.putExtra(RoseAgentWork.EXTRA_SELECT_PACKAGE, card.packageName)', select)
        self.assertNotIn('WATCH_', select)
        self.assertNotIn('taskId)', select.split('startActivityAsUser')[1])
        self.assertIn('UserHandle.of(card.userId)', select)

    def test_the_request_names_match_the_controller(self):
        watch = (ROOT/'os/background-control/agent-pip/src/dev/pixel/backgroundcontrol/WatchBinding.java').read_text()
        work = (OVERLAY/'RoseAgentWork.java').read_text()
        for name in ('EXTRA_SELECT_DISPLAY', 'EXTRA_SELECT_PACKAGE'):
            line = [l.strip() for l in work.splitlines() if f'String {name} =' in l][0]
            self.assertIn(line, watch, name)

    def test_apply_os_changes_registers_it_with_the_agent_controller_and_the_contract_declares_it(self):
        patcher = (ROOT/'scripts/apply-os-changes.py').read_text()
        block = patcher[patcher.index('if a.background_policy:'):patcher.index('preinstalls = preinstall_plan(')]
        self.assertIn("projects['packages/apps/Launcher3'] = ['rose-switcher-launcher3.patch']", block)
        import json
        contract = json.loads((ROOT/'os/devices/pixel10.json').read_text())
        self.assertIn('packages/apps/Launcher3', contract['roseInclusion']['projects'])

if __name__ == '__main__':
    unittest.main()
