"""#156: Rose Agent's work row in Launcher3's fallback overview. Applies os/patches/rose-switcher-launcher3.patch to
the pinned fallback layout (Launcher3 47accce), runs the pure RoseAgentWork decisions, and checks the view's
boundaries. Source and fixture logic only: no Launcher3 build, emulator or device."""
from pathlib import Path
import hashlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT/'os/tests/fixtures/rose-switcher'
PATCH = ROOT/'os/patches/rose-switcher-launcher3.patch'
OVERLAY = ROOT/'os/overlays/packages/apps/Launcher3/quickstep/src/com/android/quickstep/fallback'
LAYOUT = 'quickstep/res/layout/fallback_recents_activity.xml'
QUICKSTEP = 'quickstep/src/com/android/quickstep'
DIMENS = 'quickstep/res/values/rose_agent_strip.xml'
# Launcher3 47accce (os/pinned-manifest.xml), byte for byte from GrapheneOS platform_packages_apps_Launcher3.
PINNED = {'fallback_recents_activity.xml': '8df1879fb83e2a9f5b11b657a498e6153504171cb702f859d136f37ab968a9f7',
    'OverviewComponentObserver.java': 'a94703d973e14ba8e1f43ab07d7eacaa0e32f8b5668d16bff1c6843dd4877893',
    'BaseContainerInterface.java': 'a416df4aaa95b3cc954cef5ada99bc3f1078ddbdb56ccb4365be861eda6cf829',
    'FallbackActivityInterface.java': 'dfc02ef28da28e5fc82744588f6be235ad41eb9bdd79b8a471cb40c324cd4faa',
    'OverviewActionsView.java': '7f7d82fd09d7ad48d0ca0b2ef481b42e9445fe1d37de80a3b2cb45f8b908980f',
    'RecentsActivity.java': 'b9bd0251eef6b4cd3159dea08657881cfad74fbe42034f03b773c2fda8d8b804',
    'task.xml': '6fab27a7a95e9bda31d7fd4fb4a041ff49bf25c88e1744e30fff78dc50780b19',
    'icon_app_chip_view.xml': '39f08f41ca50a248942ec9c756d474659ee31fe91c486788ae1cfd5bd6306071'}
TARGETS = {'fallback_recents_activity.xml': LAYOUT, 'BaseContainerInterface.java': f'{QUICKSTEP}/BaseContainerInterface.java',
    'FallbackActivityInterface.java': f'{QUICKSTEP}/FallbackActivityInterface.java',
    'OverviewActionsView.java': f'{QUICKSTEP}/views/OverviewActionsView.java',
    'RecentsActivity.java': f'{QUICKSTEP}/RecentsActivity.java',
    'task.xml': 'quickstep/res/layout/task.xml', 'icon_app_chip_view.xml': 'quickstep/res/layout/icon_app_chip_view.xml'}
# The stock lines the patch replaces, and nothing else: #410's colour values of the human task cards' identity chip and
# focus/hover borders (attribute values only), the fallback's claimed space below the cards, and the phone actions
# bar's task-aligned bottom margin (now less the same space).
REPLACED = ['-    android:foreground="@drawable/app_chip_fg"',
    '-    android:background="@color/materialColorSurfaceBright"',
    '-    launcher:focusBorderColor="@color/materialColorSecondary">',
    '-        android:background="@drawable/icon_menu_arrow_background"',
    '-    launcher:focusBorderColor="@color/materialColorOutline"',
    '-                    overviewActionsClaimedSpace,',
    '-        // Align to bottom of task Rect.',
    '-                - mDp.getOverviewProfile().getActionsHeight();']

def patched_tree(test):
    tree = Path(tempfile.mkdtemp(prefix='launcher3-'))
    test.addCleanup(shutil.rmtree, tree, True)
    for name, relative in TARGETS.items():
        (tree/relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES/name, tree/relative)
    subprocess.run(['git', 'init', '-q', str(tree)], check=True)
    check = subprocess.run(['git', '-C', str(tree), 'apply', '--check', str(PATCH)], capture_output=True, text=True)
    test.assertEqual(check.returncode, 0, check.stderr)
    subprocess.run(['git', '-C', str(tree), 'apply', str(PATCH)], check=True)
    return tree

class RoseSwitcherTest(unittest.TestCase):
    def test_fixtures_are_the_pinned_files_and_rose_home_gets_the_fallback_overview(self):
        for name, digest in PINNED.items():
            self.assertEqual(hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest(), digest, name)
        observer = (FIXTURES/'OverviewComponentObserver.java').read_text()
        self.assertIn('// The default home app is a different launcher. Use the fallback Overview instead.', observer)
        self.assertIn('mOverviewIntent = mFallbackIntent;', observer)

    def test_the_patch_applies_cleanly_and_adds_only_the_agent_row(self):
        tree = patched_tree(self)
        layout = (tree/LAYOUT).read_text()
        self.assertIn('<com.android.quickstep.fallback.RoseAgentStrip', layout)
        self.assertLess(layout.index('android:id="@id/overview_panel"'), layout.index('RoseAgentStrip'),
            'the human carousel stays; the agent row is added beside it')
        removed = [l for l in PATCH.read_text().splitlines() if l.startswith('-') and not l.startswith('---')]
        self.assertEqual(removed, REPLACED, 'only these stock lines are replaced; nothing else of the overview is removed')

    def test_the_agent_row_sits_below_the_human_cards_and_above_the_actions(self):
        """The selected HiFi (ce126f56) order: human carousel, then the agent carousel. At 47accce the phone overview
        sizes cards from DeviceProfile and insets (BaseContainerInterface.calculateTaskSize, claimedSpaceBelow =
        dp.getOverviewActionsClaimedSpace()) and OverviewActionsView anchors the buttons to the task rect's bottom
        (getBottomMargin: heightPx - taskSize.bottom - actionsTopMargin - actionsHeight). The fallback overview now claims
        the row's height below the cards, the buttons keep that space free, and the row is placed on the buttons' top."""
        tree = patched_tree(self)
        layout = (tree/LAYOUT).read_text()
        strip = layout[layout.index('<com.android.quickstep.fallback.RoseAgentStrip'):]
        strip = strip[:strip.index('/>')]
        self.assertIn('android:layout_height="@dimen/rose_agent_strip_height"', strip)
        self.assertIn('android:layout_gravity="bottom"', strip)
        self.assertNotIn('layout_marginTop', strip)
        self.assertNotIn('rose_agent_strip_top_margin', PATCH.read_text(), 'no fixed offset from the top any more')
        import re
        dimens = dict(re.findall(r'<dimen name="(\w+)">(\d+)dp</dimen>', (tree/DIMENS).read_text()))
        self.assertEqual(set(dimens), {'rose_agent_strip_height'})
        base = (tree/f'{QUICKSTEP}/BaseContainerInterface.java').read_text()
        size = base[base.index('public final void calculateTaskSize('):base.index('private void calculateLargeTileSize(')]
        self.assertIn('/* claimedSpaceAbove= */0,', size, 'nothing claimed above the cards')
        self.assertIn('overviewActionsClaimedSpace + getOverviewClaimedSpaceBelow(context),', size)
        self.assertIn('protected int getOverviewClaimedSpaceBelow(Context context) {\n        return 0;\n    }', base)
        self.assertLess(size.index('isLargeScreen()'), size.index('getOverviewClaimedSpaceBelow'), 'phone path only')
        fallback = (tree/f'{QUICKSTEP}/FallbackActivityInterface.java').read_text()
        override = fallback[fallback.index('protected int getOverviewClaimedSpaceBelow(Context context) {'):]
        self.assertIn('return com.android.quickstep.fallback.RoseAgentStrip.rowHeight(context.getResources());',
            override[:override.index('}')], '#410: the claim is the row\'s measured height (font scale)')
        actions = (tree/f'{QUICKSTEP}/views/OverviewActionsView.java').read_text()
        margin = actions[actions.index('private int getBottomMargin() {'):actions.index('public void updateDimension(')]
        phone = margin[margin.index('// Align to bottom of task Rect.'):]
        self.assertIn('- RoseAgentStrip.reservedBelow(getContext());', phone)
        self.assertLess(margin.index('isLargeScreen()'), margin.index('RoseAgentStrip.reservedBelow'),
            'the large-screen margin is unchanged')
        self.assertIn('import com.android.quickstep.fallback.RoseAgentStrip;', actions)
        row = (OVERLAY/'RoseAgentStrip.java').read_text()
        reserved = row[row.index('public static int reservedBelow(Context context) {'):row.index('private final View.OnLayoutChangeListener')]
        self.assertIn('if (c instanceof RecentsActivity) {', reserved, 'only the fallback overview reserves it')
        self.assertIn('return rowHeight(c.getResources());', reserved)
        height = row[row.index('public static int rowHeight(Resources resources) {'):row.index('public static int reservedBelow(')]
        self.assertIn('resources.getDimensionPixelSize(R.dimen.rose_agent_strip_height));', height, 'the default is the minimum')
        self.assertEqual(height.count('TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP,'), 2,
            'the platform\'s own sp conversion (nonlinear at large scales) for the count and the status')
        self.assertIn('return 0;', reserved)
        anchor = row[row.index('private void anchor() {'):row.index('void refresh() {')]
        self.assertIn('parent.findViewById(R.id.action_buttons)', anchor)
        self.assertIn('buttons.getLocationInWindow(at);', anchor)
        self.assertIn('shift = (at[1] - origin[1]) - getBottom();', anchor, 'the row ends on the buttons top')
        self.assertIn('setTranslationY(shift)', anchor)
        for moved in ('setTranslationY', 'offsetTopAndBottom'):
            self.assertEqual(row.count(moved), 1 if moved == 'setTranslationY' else 0, 'only the row itself moves')
        self.assertIn('parent.addOnLayoutChangeListener(relayout);', row)
        self.assertIn('parent.removeOnLayoutChangeListener(relayout);', row)
        self.assertGreaterEqual(int(dimens['rose_agent_strip_height']), 12 + 22 + 146 + 18)
        layout_actions = layout.index('android:id="@+id/overview_actions_view"')
        self.assertLess(layout.index('android:id="@id/overview_panel"'), layout.index('RoseAgentStrip'))
        self.assertLess(layout_actions, layout.index('RoseAgentStrip'), 'declared after the actions so it draws above them')

    def test_labels_are_looked_up_only_for_kept_work_and_a_failure_names_only_its_step(self):
        strip = (OVERLAY/'RoseAgentStrip.java').read_text()
        read = strip[strip.index('private static RoseAgentWork read('):strip.index('private void show(')]
        tasks = read[read.index('ActivityTaskManager.getInstance().getTasks(TASK_LIMIT)'):read.index('RoseAgentWork.of(')]
        self.assertNotIn('getApplicationInfoAsUser', tasks, 'no label lookup for every running task')
        self.assertNotIn('getApplicationLabel', tasks)
        self.assertIn('RoseAgentWork.of(controllerUid, user, owners, sessionDevices, tasks).labelled(', read)
        self.assertLess(read.index('RoseAgentWork.of('), read.index('getApplicationInfoAsUser'))
        for step in ('CONTROLLER', 'OWNERSHIP', 'TASKS', 'DISPLAYS'):
            self.assertIn(f'RoseAgentWork.Step.{step}', read)
        self.assertIn('return RoseAgentWork.failed(step);', read)
        self.assertNotIn('getMessage', read, 'no exception text in the status or the log')
        self.assertNotIn('Log.w(TAG, "Agent work unavailable", e)', read, 'no stack trace with private text')

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
            'android/companion/virtual/VirtualDeviceManager.java': 'package android.companion.virtual; '
                'public final class VirtualDeviceManager { public void registerAutomatedPackageListener('
                'java.util.concurrent.Executor e, android.companion.virtual.computercontrol.AutomatedPackageListener l) {} '
                'public java.util.List<VirtualDevice> getVirtualDevices(){return null;} }',
            'android/window/TaskSnapshot.java': 'package android.window; public class TaskSnapshot { '
                'public boolean isBufferValid(){return false;} public android.graphics.ColorSpace getColorSpace(){return null;} '
                'public android.graphics.Bitmap wrapToBitmap(android.graphics.ColorSpace c){return null;} '
                'public void closeBuffer(){} }',
            'android/window/TaskSnapshotManager.java': 'package android.window; public class TaskSnapshotManager { '
                'public static TaskSnapshotManager getInstance(){return null;} '
                'public TaskSnapshot takeTaskSnapshot(int taskId, boolean updateCache, boolean lowResolution) '
                'throws android.os.RemoteException {return null;} }',
            'android/companion/virtual/VirtualDevice.java': 'package android.companion.virtual; '
                'public final class VirtualDevice { public static final int DEVICE_PROFILE_COMPUTER_CONTROL = 1; '
                'public int getDeviceProfile(){return 0;} public int getDeviceId(){return 0;} '
                'public int[] getDisplayIds(){return null;} }',
            'android/companion/virtual/computercontrol/AutomatedPackageListener.java':
                'package android.companion.virtual.computercontrol; public interface AutomatedPackageListener { '
                'void onAutomatedPackagesChanged(String automatingPackage, java.util.List<String> automatedPackages, '
                'android.os.UserHandle user); '
                'default void onAutomatedDevicesChanged(long generation, int[] deviceIds, String[] ownerPackages, '
                'int[] ownerUids) {} '
                'default void onAutomatedDeviceAppsChanged(long generation, int[] deviceIds, String[] ownerPackages, '
                'int[] ownerUids, boolean[] deviceComplete, int[] appDeviceIds, String[] appPackageNames) {} }',
            'android/content/pm/PackageManager.java': 'package android.content.pm; public abstract class PackageManager { '
                'public static class NameNotFoundException extends Exception {} '
                'public abstract int getPackageUidAsUser(String p, int u) throws NameNotFoundException; '
                'public abstract ApplicationInfo getApplicationInfoAsUser(String p, int f, int u) throws NameNotFoundException; '
                'public abstract CharSequence getApplicationLabel(ApplicationInfo i); }',
            'android/content/Context.java': 'package android.content; public abstract class Context { '
                'public static final int DEVICE_ID_DEFAULT = 0; '
                'public abstract Context getApplicationContext(); '
                'public abstract android.content.pm.PackageManager getPackageManager(); '
                'public abstract <T> T getSystemService(Class<T> c); '
                'public void startActivityAsUser(Intent i, android.os.UserHandle u) {} '
                'public abstract android.content.res.Resources getResources(); }',
            'android/os/UserHandle.java': 'package android.os; public final class UserHandle { '
                'public static UserHandle of(int u){return null;} public int getIdentifier(){return 0;} }',
            'com/android/launcher3/R.java': 'package com.android.launcher3; public final class R { '
                'public static final class dimen { public static final int rose_agent_strip_height = 1; } '
                'public static final class id { public static final int action_buttons = 2; '
                'public static final int scrim_view = 3; } }',
            # Launcher3 47accce src/com/android/launcher3/views/ScrimView.java: the members the field uses.
            'com/android/launcher3/views/ScrimView.java': 'package com.android.launcher3.views; '
                'public class ScrimView extends android.view.View { '
                'public ScrimView(android.content.Context c, android.util.AttributeSet a) { super(c, a); } '
                'public int getBackgroundColor() { return 0; } '
                'public void setDrawingController(ScrimDrawingController d) {} '
                'public interface ScrimDrawingController { '
                'default void drawOnScrimWithScale(android.graphics.Canvas c, float s) {} '
                'default void drawOnScrimWithBottomOffset(android.graphics.Canvas c, int b) {} '
                'void drawOnScrimWithScaleAndBottomOffset(android.graphics.Canvas c, float s, int b); } }',
            'com/android/quickstep/RecentsActivity.java': 'package com.android.quickstep; '
                'public class RecentsActivity extends android.app.Activity {}',
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
            str(OVERLAY/'RoseAgentStrip.java'), str(OVERLAY/'RoseAgentWork.java'), str(OVERLAY/'RoseAgentFan.java'),
            str(OVERLAY/'RoseFan.java'), str(OVERLAY/'RoseOxbloodField.java'), str(OVERLAY/'RoseEdgeGesture.java')],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_view_uses_only_existing_authority_and_opens_only_the_current_work(self):
        strip = (OVERLAY/'RoseAgentStrip.java').read_text()
        for banned in ('AgentProvider', 'getContentResolver', 'content://', 'requestPermissions', 'grantRuntimePermission',
                'checkSignatures', 'startActivityFromRecents', 'moveTaskToFront', 'dev.pixel.voice'):
            self.assertNotIn(banned, strip, banned)
        self.assertIn('ActivityTaskManager.getInstance().getTasks(TASK_LIMIT)', strip)
        self.assertIn('vdm.registerAutomatedPackageListener(Runnable::run, this);', strip)
        select = strip[strip.index('private void select('):strip.index('private TextView text(')]
        # #156 M004: the tap re-reads off the UI thread, the newest tap wins, the decision uses that fresh read, and
        # only a selection request (display + package) is sent: never an unbound viewer, never a task id as a Rose id.
        self.assertIn('int token = ++selection;', select)
        self.assertIn('UI_HELPER_EXECUTOR.execute(() -> {\n            RoseAgentWork platform = read(context);', select)
        self.assertIn('RoseAgentWork fresh = locked ? RoseAgentWork.locked() : platform;', select)
        self.assertIn('if (token != selection) {\n                    release(frames);\n                    return;', select)
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

class RoseSwitcherInteractionTest(unittest.TestCase):
    """#156 acceptance against the production overlay source (RoseAgentStrip/RoseAgentWork as shipped) and the patched
    pinned layout. Source contract only: scrolling, animation and selection on a device need a runtime recording."""
    STRIP = (OVERLAY/'RoseAgentStrip.java').read_text()
    # Selected HiFi ce126f56 (prototypes/rose-hifi/Rose-HiFi.html renderSwitcher): the link label and empty text.
    HIFI = {'in progress': " task in progress", 'plural': " tasks in progress", 'idle': "No active work",
        'empty': "No background work"}

    def body(self, signature, end):
        return self.STRIP[self.STRIP.index(signature):self.STRIP.index(end)]

    def test_the_agent_carousel_scrolls_on_its_own(self):
        # #410: the row's own scroller is the Oxblood fan, still a HorizontalScrollView of its own.
        self.assertEqual(self.STRIP.count('new RoseAgentFan(context)'), 1)
        self.assertIn('scroller.addView(cards);', self.STRIP)
        fan = (OVERLAY/'RoseAgentFan.java').read_text()
        self.assertIn('final class RoseAgentFan extends HorizontalScrollView {', fan)
        for code in (self.STRIP, fan):
            for coupled in ('RecentsView', 'PagedView', 'overview_panel', 'setOnScrollChangeListener', 'scrollBy('):
                self.assertNotIn(coupled, code, f'{coupled}: the agent row never drives the human carousel')
        tree = patched_tree(self)
        layout = (tree/LAYOUT).read_text()
        self.assertLess(layout.index('android:id="@id/overview_panel"'), layout.index('RoseAgentStrip'),
            'a sibling of the human carousel in the drag layer, not inside it')

    def test_every_entry_starts_fresh_and_leaving_stops_the_connector(self):
        visibility = self.body('protected void onWindowVisibilityChanged(int visibility)', 'private void setWorking(')
        self.assertLess(visibility.index('scroller.unplace();'), visibility.index('refresh();'),
            'every entry places the carousel afresh (RoseFan.initial), before the read that fills it')
        self.assertIn('setWorking(false);', visibility.split('} else {')[1])
        refresh = self.body('void refresh() {', 'private static RoseAgentWork read(')
        self.assertIn('int token = ++generation;', refresh)
        self.assertIn('if (token == generation) show(next, frames, locked);\n                else release(frames);', refresh,
            'the newest read wins on re-entry; an older one\'s frames are released, never shown')

    def test_the_connector_reflects_only_actual_work(self):
        show = self.body('private void show(RoseAgentWork next, Map<Integer, Bitmap> frames, boolean locked) {', 'private void select(')
        self.assertIn('link.setText(next.linkLabel());', show)
        self.assertIn('setWorking(next.working() > 0 && getWindowVisibility() == VISIBLE);', show)
        working = self.body('private void setWorking(boolean working) {', 'void refresh() {')
        # #410: the Oxblood switcher's pearl is static (lit or dim); nothing animates, so reduced motion has nothing to stop.
        self.assertIn('connector.setAlpha(working ? 1f : IDLE);', working)
        for animated in ('ObjectAnimator', 'setRepeatCount', 'ValueAnimator.INFINITE', 'pulse'):
            self.assertNotIn(animated, self.STRIP, animated)
        self.assertIn('if (task.topOfDisplay) edge.setStroke(Math.max(1, dp(1)), CURRENT_RING);', show,
            'the current screen of Rose\'s display is marked')
        self.assertIn('else edge.setStroke(Math.max(1, dp(1)), CARD_RING);', show)
        self.assertNotIn('RUBY_EDGE', self.STRIP, 'no running edge: Launcher3 cannot see authoritative running state')
        self.assertIn('Rose is working in it now', show)

    def test_empty_and_error_states_invent_nothing(self):
        show = self.body('private void show(RoseAgentWork next, Map<Integer, Bitmap> frames, boolean locked) {', 'private void select(')
        self.assertIn('for (RoseAgentWork.Task task : next.items) {', show, 'cards come only from the read')
        self.assertEqual(show.count('cards.addView('), 1)
        work = (OVERLAY/'RoseAgentWork.java').read_text()
        self.assertIn(f'static final String EMPTY = "{self.HIFI["empty"]}";', work)
        for key in ('in progress', 'plural', 'idle'):
            self.assertIn(self.HIFI[key], work, key)

    def test_back_home_and_selection_never_stop_or_move_work(self):
        for banned in ('removeTask', 'finishAndRemoveTask', 'killBackgroundProcesses', 'forceStopPackage', 'finish()',
                'moveTaskToFront', 'startActivityFromRecents', 'setFocusedRootTask', 'stopLockTask', 'onBackPressed'):
            self.assertNotIn(banned, self.STRIP, banned)
        select = self.body('private void select(', 'private TextView text(')
        self.assertEqual(select.count('startActivityAsUser('), 1, 'a selection only asks the viewer for that work')
        # The human carousel's selection is Launcher3's own: the patch removes nothing else of the overview.
        removed = [l for l in PATCH.read_text().splitlines() if l.startswith('-') and not l.startswith('---')]
        self.assertEqual(removed, REPLACED)

    def test_the_row_still_fits_its_claimed_height(self):
        # padding 12 + pearl row 22 + Oxblood Rose card 146 (RoseFan.CARD_HEIGHT_DP) + status ~18 dp, inside the claim.
        import re
        tree = patched_tree(self)
        height = int(re.search(r'name="rose_agent_strip_height">(\d+)dp', (tree/DIMENS).read_text())[1])
        self.assertGreaterEqual(height, 12 + 22 + 146 + 18)
        self.assertIn('LINK_ROW_DP = 22', self.STRIP)
        self.assertIn('static final int CARD_WIDTH_DP = 124, CARD_HEIGHT_DP = 146', (OVERLAY/'RoseFan.java').read_text())
        self.assertNotIn('title = text(', self.STRIP, 'the HiFi link row replaces the old static title')

class ControllerReadPermissionTest(unittest.TestCase):
    """Owner report: the switcher showed "Rose's work could not be read (controller)". The controller step was
    ActivityManager.getCurrentUser(), which reaches ActivityManagerService.getCurrentUserId() ->
    UserController.getCurrentUserIdChecked() -> checkGetCurrentUserPermissions() (frameworks/base 84183aff): a
    SecurityException unless the caller holds INTERACT_ACROSS_USERS or INTERACT_ACROSS_USERS_FULL. Launcher3 47accce
    requests neither (its pinned manifests below), so every read failed at that step."""
    MANIFESTS = {'Launcher3-AndroidManifest.xml': '72405b81a9b05834565dbe53018209896c82b82c7d6510ef34e898481c3d7d7b',
        'Launcher3-AndroidManifest-common.xml': 'a13db12d6b1656af6247356fc2f3b039dd2f0bb81a8837e8db5f4e2130bf3607',
        'Launcher3-quickstep-AndroidManifest.xml': '63aad650b57cc3953a3243bddcdae0f7b29fee9e0fa3a13b0de26ee0fad9213f'}

    @staticmethod
    def code():
        """RoseAgentStrip without comments: what actually runs."""
        import re
        text = (OVERLAY/'RoseAgentStrip.java').read_text()
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
        return '\n'.join(line.split('//')[0] for line in text.splitlines())

    def test_launcher3_holds_no_cross_user_permission(self):
        for name, digest in self.MANIFESTS.items():
            data = (FIXTURES/name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, name)
            for permission in ('android.permission.INTERACT_ACROSS_USERS"', 'INTERACT_ACROSS_USERS_FULL'):
                self.assertNotIn(permission.encode(), data, f'{name} would grant {permission}')
        self.assertIn(b'android.permission.QUERY_ALL_PACKAGES', (FIXTURES/'Launcher3-quickstep-AndroidManifest.xml').read_bytes(),
            'package visibility for the controller lookup is held')

    def test_the_row_reads_only_its_own_user_and_needs_no_cross_user_call(self):
        code = self.code()
        for needs_permission in ('getCurrentUser(', 'getCurrentUserId(', 'getUsers(', 'getProfiles(', 'UserHandle.ALL',
                'UserHandle.USER_ALL', 'createContextAsUser'):
            self.assertNotIn(needs_permission, code, needs_permission)
        read = code[code.index('private static RoseAgentWork read('):code.index('private void show(')]
        self.assertIn('int user = Process.myUserHandle().getIdentifier();', read)
        self.assertIn('pm.getPackageUidAsUser(RoseAgentWork.CONTROLLER, user)', read)
        self.assertIn('RoseAgentWork.of(controllerUid, user, owners, sessionDevices, tasks).labelled(', read)
        self.assertIn('RoseAgentWork.Owners owners = platform.owners();', read)
        # Labels and the viewer only for kept tasks, which of() restricts to that user (RoseAgentWorkTest).
        self.assertIn('getApplicationInfoAsUser(task.packageName, 0, task.userId)', read)
        self.assertIn('UserHandle.of(card.userId)', code)
        import re
        self.assertEqual(len(re.findall(r'AsUser\(', code)), 3, 'exactly these same-user calls')

class PrivateControllerDisplayTest(unittest.TestCase):
    """App Core's finding (#156): Rose's ComputerControl session display is private, so DisplayManager.getDisplays()
    never lists it to Launcher3 and the row read "No background work" while Rose worked (the old read keyed on that
    list; reproduced against eee21d1's RoseAgentWork). Verified here on frameworks/base 84183aff, byte for byte from
    GrapheneOS platform_frameworks_base. The fix leaves the display private and adds no permission: it uses the
    platform's own report to the recents component."""
    PINNED = {'Display.java': '266257a7e246a8db480c176520900c38f08bd62d9f480490c90b894b9936aca4',
        'VirtualDisplayAdapter.java': '61d80f52cb8cf90136c17c1ca0b8a434d294f9a6241b61397076e504995588d9',
        'AutomatedPackagesRepository.java': 'f2b05ab0175a387a29ae9c4c3ba1781df1e9b4d7c013eeeab3460365bbdeecc0',
        'AutomatedPackageListener.java': 'e39e3f93a046664ae045473c2bebd3a7c856cc58ff22a7019bd722ac2f3b0fe3',
        'VirtualDeviceManagerService.java': '2ce9b1b44835e5ed2ee3e66a1cfe49d5d976e9961f605787ba7bf116850afd2e',
        'VirtualDeviceImpl.java': 'c7ad5bcd9dfda3735522c6bf0e737d0c8e7fd36cf3450f37a049962e8b6c40c8',
        'RunningTasks.java': '5679fb6251dc7f5a6b38685bd47f03fdebe0ab2c11376bdf59e9fe0745618925'}
    SESSION = ROOT/'os/tests/fixtures/background-control/ComputerControlSessionImpl.java'

    @staticmethod
    def source(name):
        return (FIXTURES/name).read_text()

    @staticmethod
    def method(text, signature):
        """From the signature to the closing brace at the signature line's own indentation."""
        start = text.index(signature)
        indent = text[text.rindex('\n', 0, start) + 1:start]
        assert indent.strip() == '', 'the signature starts its line'
        return text[start:text.index('\n' + indent + '}\n', start)]

    def test_fixtures_are_the_pinned_files(self):
        for name, digest in self.PINNED.items():
            self.assertEqual(hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest(), digest, name)

    def test_the_session_display_is_private_and_hidden_from_launcher3(self):
        config = self.method(self.SESSION.read_text(), 'private static VirtualDisplayConfig createSessionDisplayConfig(')
        flags = config[config.index('final int displayFlags ='):config.index(';', config.index('final int displayFlags ='))]
        self.assertEqual(flags.split('=', 1)[1].split(), ['DisplayManager.VIRTUAL_DISPLAY_FLAG_TRUSTED', '|',
            'DisplayManager.VIRTUAL_DISPLAY_FLAG_ALWAYS_UNLOCKED'], 'no VIRTUAL_DISPLAY_FLAG_PUBLIC')
        self.assertIn('if ((mFlags & VIRTUAL_DISPLAY_FLAG_PUBLIC) == 0) {\n'
            '                    mInfo.flags |= DisplayDeviceInfo.FLAG_PRIVATE;', self.source('VirtualDisplayAdapter.java'))
        rule = self.method(self.source('Display.java'), 'public static boolean hasAccess(int uid, int flags, int ownerUid, int displayId)')
        for clause in ('(flags & Display.FLAG_PRIVATE) == 0', 'uid == ownerUid', 'uid == Process.SYSTEM_UID', 'uid == 0',
                'isUidPresentOnDisplay(uid, displayId)'):
            self.assertIn(clause, rule)
        self.assertEqual(rule.count('||'), 4, 'exactly those five ways to see a display')
        # Launcher3 is not the owner, system, root or on the display: under that rule it never sees display 7.
        def has_access(uid, private, owner, present):
            return not private or uid in (owner, 1000, 0) or uid in present
        self.assertFalse(has_access(10050, True, 10123, {10200}))
        self.assertTrue(has_access(10050, False, -1, set()), 'the default display stays listed')

    def test_the_row_no_longer_depends_on_the_display_list_and_keeps_it_private(self):
        code = ControllerReadPermissionTest.code()
        for gone in ('getDisplays(', 'getOwnerUid(', 'DisplayManager', 'RoseAgentWork.Display'):
            self.assertNotIn(gone, code, f'{gone}: the private display is never listed to Launcher3')
        for bypass in ('VIRTUAL_DISPLAY_FLAG_PUBLIC', 'FLAG_PRIVATE', 'isUidPresentOnDisplay', 'createVirtualDisplay',
                'ACCESS_COMPUTER_CONTROL', 'CREATE_VIRTUAL_DEVICE', 'ADD_TRUSTED_DISPLAY', 'CAPTURE_VIDEO_OUTPUT'):
            self.assertNotIn(bypass, code, bypass)
        patch = PATCH.read_text()
        self.assertNotIn('AndroidManifest', patch, 'no manifest, so no new permission')
        self.assertNotIn('uses-permission', patch)
        import re
        work = re.sub(r'/\*.*?\*/', '', (OVERLAY/'RoseAgentWork.java').read_text(), flags=re.S)
        self.assertNotIn('getDisplays(', work)

    def test_the_report_is_the_platforms_and_only_the_recents_component_receives_it(self):
        service = self.source('VirtualDeviceManagerService.java')
        register = self.method(service, 'public void registerAutomatedPackageListener(IAutomatedPackageListener listener) {')
        self.assertIn('checkCallerIsRecentsOrHomeRoleHolder();', register)
        check = self.method(service, 'private void checkCallerIsRecentsOrHomeRoleHolder() {')
        self.assertIn('if (mActivityTaskManagerInternal.isCallerRecents(callingUid)) {', check)
        self.assertIn('throw new SecurityException("Caller is neither recents, nor a HOME role holder.");', check)
        # Updated only for ComputerControl sessions, keyed by the device owner's package.
        changed = self.method(service, 'void onRunningAppsChanged(int deviceId, @NonNull String deviceOwnerPackageName,')
        self.assertIn('if (mComputerControlSessionProcessor.isComputerControlSession(deviceId)) {\n'
            '            mAutomatedPackagesRepository.update(deviceId, deviceOwnerPackageName, uidPackagePairs);', changed)
        device = self.source('VirtualDeviceImpl.java')
        self.assertIn('mOwnerPackageName = Objects.requireNonNull(attributionSource.getPackageName());', device)
        self.assertIn('mDeviceId, mOwnerPackageName, runningUids, newAllRunningUidPackagePairs);', device)
        repo = self.source('AutomatedPackagesRepository.java')
        snapshot = self.method(repo, 'public void registerAutomatedPackageListener(IAutomatedPackageListener listener) {')
        self.assertIn('// Immediately dispatch the currently automated packages.', snapshot)
        self.assertIn('listener.onAutomatedPackagesChanged(ownerPackage, packages, user);', snapshot)
        self.assertIn('notifyAutomatedPackagesChanged(deviceOwnerPackageName, packagesToReport, userId);', repo)
        self.assertIn('? Collections.emptyList()', repo, 'an ended session is reported as an empty list')
        listener = self.source('AutomatedPackageListener.java')
        self.assertIn('void onAutomatedPackagesChanged(\n            @NonNull String automatingPackage,', listener)
        # The display check needs no permission and returns only a device id.
        lookup = self.method(service, 'public int getDeviceIdForDisplayId(int displayId) {\n            VirtualDeviceImpl')
        self.assertNotIn('enforce', lookup)
        self.assertNotIn('check', lookup)
        # Tasks on a private display are listed to the recents component: no display-access filter in RunningTasks.
        tasks = self.source('RunningTasks.java')
        self.assertNotIn('hasAccess', tasks)
        self.assertNotIn('isPrivate', tasks)

    def test_only_the_platforms_device_ownership_is_kept_and_failures_stay_named(self):
        strip = (OVERLAY/'RoseAgentStrip.java').read_text()
        holder = strip[strip.index('private static final class Sessions implements AutomatedPackageListener {'):
            strip.index('private static final float IDLE')]
        packages = holder[holder.index('public void onAutomatedPackagesChanged('):holder.index('public void onAutomatedDevicesChanged(')]
        statements = [l.strip() for l in re.sub(r'//.*', '', packages.split('{', 1)[1]).splitlines() if l.strip()]
        self.assertEqual(statements[0], 'refreshStrips();', 'package reports only re-read the visible rows')
        self.assertEqual(statements[1], '}', 'and change no ownership or membership state')
        devices = holder[holder.index('public void onAutomatedDevicesChanged('):holder.index('synchronized RoseAgentWork.Owners owners()')]
        self.assertIn('if (!ownership.accept(snapshotGeneration, deviceIds, ownerPackages, ownerUids)) return;', devices)
        self.assertLess(devices.index('generation++;'), devices.index('strip.refresh();'), 'every applied change re-reads')
        register = holder[holder.index('synchronized void register() {'):holder.index('synchronized void watch(')]
        self.assertIn('if (registered) return;', register)
        self.assertIn('if (vdm == null) throw new IllegalStateException();', register)
        self.assertLess(register.index('ownership.reset();'), register.index('vdm.registerAutomatedPackageListener(Runnable::run, this);'),
            'each registration starts from nothing')
        constructor = strip[strip.index('public RoseAgentStrip(Context context, AttributeSet attrs) {'):
            strip.index('private static final class Sessions')]
        self.assertIn('try {\n            sessions.register();\n        } catch (RuntimeException e) {', constructor,
            'a refusal never fails the overview')
        read = strip[strip.index('private static RoseAgentWork read('):strip.index('private void show(')]
        self.assertLess(read.index('step = RoseAgentWork.Step.OWNERSHIP;'), read.index('platform.register();'),
            'a refused or missing registration is named at its own step on every read')
        self.assertLess(read.index('step = RoseAgentWork.Step.DISPLAYS;'), read.index('vdm.getVirtualDevices()'))
        work = (OVERLAY/'RoseAgentWork.java').read_text()
        self.assertIn('if (!owners.appsReceived) return unavailable(NOT_REPORTED);', work,
            'before the platform\'s apps snapshot nothing is shown as "No background work"')

class CrossDisplayAttributionTest(unittest.TestCase):
    """#156 disclosed gap after PR337: a package Rose automates can also run on another app's virtual display (or on
    another ComputerControl owner's session display) and was shown as Rose's card. Old-fails: PR337's of() took every
    virtual device's display (getDeviceIdForDisplayId != DEVICE_ID_DEFAULT) and attributed by package alone, so a cast
    of the same app became a second card and "2 tasks in progress" (reproduced against a2a51d6). The correction uses
    two platform facts on frameworks/base 84183aff, with no permission, PUBLIC display or guessed binding."""
    PINNED = {'VirtualDevice.java': '6a7602ceb3d7e9bfd6669d96861b288e7d0969ab4a84297c7ebc1dcdc56ec97f',
        'DisplayWindowPolicyControllerHelper.java': '699e7d4253a5fd7d293f48e44d2ce779fb151629883ec8c9581d3818daf1dd44'}
    method = staticmethod(PrivateControllerDisplayTest.method)

    def test_fixtures_are_the_pinned_files(self):
        for name, digest in self.PINNED.items():
            self.assertEqual(hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest(), digest, name)

    def test_only_the_session_path_makes_a_computer_control_device_and_anyone_may_list_its_displays(self):
        service = (FIXTURES/'VirtualDeviceManagerService.java').read_text()
        self.assertEqual(service.count('VirtualDevice.DEVICE_PROFILE_COMPUTER_CONTROL'), 1)
        factory = service[service.index('mComputerControlSessionProcessor =\n'):service.index('mComputerControlConsentManager =')]
        self.assertIn('VirtualDevice.DEVICE_PROFILE_COMPUTER_CONTROL)));', factory, 'set by the platform, not the app')
        self.assertIn('token, attributionSource, params, VirtualDevice.DEVICE_PROFILE_SHELL);', service)
        mapping = self.method(service, 'private static int getDeviceProfile(@NonNull AssociationInfo associationInfo) {')
        self.assertNotIn('COMPUTER_CONTROL', mapping, 'no companion association maps to it')
        listing = self.method(service, 'public List<VirtualDevice> getVirtualDevices() {')
        self.assertNotIn('enforce', listing)
        self.assertNotIn('check', listing)
        device = (FIXTURES/'VirtualDevice.java').read_text()
        self.assertIn('public static final int DEVICE_PROFILE_COMPUTER_CONTROL = 1;', device)
        self.assertIn('public int getDeviceProfile() {\n        return mProfile;', device)
        self.assertIn('dest.writeInt(mProfile);', device)
        displays = self.method((FIXTURES/'VirtualDeviceImpl.java').read_text(), 'public int[] getDisplayIds() {')
        self.assertIn('displayIds[i] = mVirtualDisplays.keyAt(i);', displays)
        self.assertNotIn('enforce', displays)

    def test_each_owners_report_is_the_activities_on_its_own_session_displays(self):
        helper = self.method((FIXTURES/'DisplayWindowPolicyControllerHelper.java').read_text(), 'void onRunningActivityChanged() {')
        self.assertIn('mDisplayContent.forAllActivities((r) -> {\n            if (!r.finishing) {', helper)
        self.assertIn('uidPackagePairs.add(new Pair<>(r.getUid(), r.info.packageName));', helper)
        device = (FIXTURES/'VirtualDeviceImpl.java').read_text()
        self.assertIn('mDeviceId, mOwnerPackageName, runningUids, newAllRunningUidPackagePairs);', device)
        repo = (FIXTURES/'AutomatedPackagesRepository.java').read_text()
        self.assertIn('// This aggregates packages from all virtual devices associated with the current\n'
            '        // deviceOwnerPackageName.', repo)
        # So a task on a session display is in its owner's report; reported by CONTROLLER alone, that owner is it.

    def test_the_row_uses_only_session_devices_and_attributes_by_device_owner(self):
        code = ControllerReadPermissionTest.code()
        read = code[code.index('private static RoseAgentWork read('):code.index('private void show(')]
        self.assertNotIn('getDeviceIdForDisplayId', code, 'any virtual device display is no longer enough')
        self.assertIn('if (device.getDeviceProfile() != VirtualDevice.DEVICE_PROFILE_COMPUTER_CONTROL) continue;', read)
        self.assertIn('for (int id : device.getDisplayIds()) if (id > 0) sessionDevices.put(id, device.getDeviceId());', read)
        work = (OVERLAY/'RoseAgentWork.java').read_text()
        self.assertIn('for (Map.Entry<Integer, Owner> e : owners.appsOwners.entrySet()) {', work,
            'the devices of the same atomic apps snapshot')
        self.assertIn('if (CONTROLLER.equals(o.packageName) && o.uid == controllerUid) rose.add(e.getKey());', work,
            'exact controller package and this user\'s controller uid')
        for heuristic in ('candidate', 'others', 'automation.packages'):
            self.assertNotIn(heuristic, re.sub(r'/\*.*?\*/', '', work, flags=re.S), f'{heuristic}: no package attribution')
        self.assertIn('if (fresh.state != State.AVAILABLE) return Open.UNATTRIBUTED;', work,
            'a card whose fresh read cannot confirm Rose\'s work is refused with that reason')
        show = code[code.index('private void show(RoseAgentWork next, Map<Integer, Bitmap> frames, boolean locked) {'):code.index('private void select(')]
        self.assertIn('for (RoseAgentWork.Task task : next.items) {', show, 'unconfirmed tasks are never cards')
        self.assertNotIn('unattributed', show)

class InitialLoadAttributionTest(unittest.TestCase):
    """#156 initial load after PR338: the platform posts one report per owner and user with no completion marker, so a
    card published from the controller's report before another owner's arrived was false attribution (old-fails on
    66dbe45: two cards for mail on displays 7 and 11). There is no existing barrier or attested owner/display channel
    (pinned below: upstream 84183aff). The agreed producer contract (onAutomatedDevicesChanged) adds complete,
    generation-numbered device-ownership snapshots; the row attributes only through them."""
    PINNED = {'IAutomatedPackageListener.aidl': 'f517dec31a738885227ffb7271be38f14f5acc904e3a164ca624e52ef153a5da',
        'IVirtualDevice.aidl': '421c9834ce90837521d7ee990353562509b5ed1c59f2fe9e8af0678289141521',
        'TaskInfo.java': '6c63deec3f2d8f23a5fb7cf616f803738a361909c506d33f68f933f3aedb2f8f',
        'VirtualDeviceManager.java': '71fcaaea4eb03c53c6f89b4803c56014fe31218acef5a3d396ffc143e7185e1f'}
    method = staticmethod(PrivateControllerDisplayTest.method)

    def test_fixtures_are_the_pinned_files(self):
        for name, digest in self.PINNED.items():
            self.assertEqual(hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest(), digest, name)

    def test_the_platform_offers_no_snapshot_barrier_sequence_or_owner_per_display(self):
        import re
        aidl = (FIXTURES/'IAutomatedPackageListener.aidl').read_text()
        self.assertIn('oneway interface IAutomatedPackageListener {', aidl)
        body = re.sub(r'/\*.*?\*/', '', aidl[aidl.index('oneway interface'):], flags=re.S)
        self.assertEqual(re.findall(r'void (\w+)\(', body), ['onAutomatedPackagesChanged'], 'one callback, no "complete"')
        self.assertEqual(re.sub(r'\s+', ' ', body[body.index('('):body.index(')') + 1]),
            '( in String automatingPackage, in List<String> automatedPackages, in UserHandle user)',
            'no device id, display id, sequence or generation')
        repo = (FIXTURES/'AutomatedPackagesRepository.java').read_text()
        snapshot = self.method(repo, 'public void registerAutomatedPackageListener(IAutomatedPackageListener listener) {')
        self.assertEqual(snapshot.count('mHandler.post('), 1, 'one post per (owner, user) entry, inside the loop')
        self.assertLess(snapshot.index('for (int j = 0; j < userToPackages.size(); ++j) {'), snapshot.index('mHandler.post('))
        client = (FIXTURES/'VirtualDeviceManager.java').read_text()
        delegate = client[client.index('private static class AutomatedPackageListenerDelegate'):
            client.index('private static class LocalComputerControlConsentManager')]
        self.assertIn('mExecutor.execute(() -> mListener.onAutomatedPackagesChanged(', delegate, 'forwarded as is')
        device = re.sub(r'/\*.*?\*/', '', (FIXTURES/'IVirtualDevice.aidl').read_text(), flags=re.S)
        for owner in ('getOwnerPackageName', 'getOwnerUid', 'getOwner('):
            self.assertNotIn(owner, device)
        public = (FIXTURES/'VirtualDevice.java').read_text()
        self.assertNotIn('getOwner', public)
        task = (FIXTURES/'TaskInfo.java').read_text()
        for field in ('launchedFrom', 'ownerUid', 'ownerPackage', 'deviceId'):
            self.assertNotIn(field, task, f'TaskInfo carries no {field}')

    def test_the_row_fails_closed_where_ownership_is_not_reported(self):
        work = (OVERLAY/'RoseAgentWork.java').read_text()
        decide = work[work.index('static RoseAgentWork of(int controllerUid'):work.index('static RoseAgentWork unconfirmed()')]
        self.assertIn('if (owners.malformed || owners.appsMalformed) return failed(Step.OWNERSHIP);', decide)
        self.assertIn('if (owners.incompleteDevices.contains(device)) return unavailable(UNCONFIRMED);', decide,
            'any of Rose\'s devices incomplete: cannot confirm')
        self.assertLess(decide.index('if (CONTROLLER.equals(pkg)) continue;'),
            decide.index('if (deviceOf.put(pkg, device) != null) return unavailable(DUPLICATE);'),
            'the controller is removed before the duplicate check; an app on two devices: cannot confirm')
        self.assertIn('if (deviceOf.isEmpty()) return new RoseAgentWork(State.EMPTY, new ArrayList<>(), EMPTY);', decide,
            'no controller device, or only the controller on them: confirmed empty')
        self.assertIn('members.remove(CONTROLLER);', decide, 'the controller itself is never a member')
        self.assertNotIn('rose.size()', decide, 'every complete Rose device counts: no device-count rule')
        self.assertIn('if (on == null || on.intValue() != device || t.userId != currentUser) continue;', decide,
            'a task attaches only to a member of its own device, for the current user')
        decision = re.sub(r'/\*.*?\*/', '', (OVERLAY/'RoseAgentWork.java').read_text(), flags=re.S)
        for timed in ('postDelayed', 'Thread.sleep', 'SystemClock', 'currentTimeMillis', 'nanoTime', 'Handler('):
            self.assertNotIn(timed, decision, f'{timed}: no time in the attribution decision')
        code = ControllerReadPermissionTest.code()
        for timed in ('Thread.sleep', 'SystemClock', 'currentTimeMillis', 'nanoTime', 'Handler('):
            self.assertNotIn(timed, code, f'{timed}: no guessed delay in the row')
        self.assertEqual(code.count('postDelayed('), 1, 'one cadence: the live-view re-capture while it is on screen')
        self.assertIn('if (getWindowVisibility() == VISIBLE && next.hasLiveView(locked)) postDelayed(previewTick, '
            'PREVIEW_INTERVAL_MS);', code)
        strip = (OVERLAY/'RoseAgentStrip.java').read_text()
        read = strip[strip.index('private static RoseAgentWork read('):strip.index('private void show(')]
        self.assertLess(read.index('long generation = platform.generation();'), read.index('platform.owners();'))
        self.assertLess(read.index('ActivityTaskManager.getInstance().getTasks(TASK_LIMIT)'), read.index('vdm.getVirtualDevices()'),
            'tasks first, then the devices, so a live session display a task is on is listed')
        self.assertLess(read.index('vdm.getVirtualDevices()'), read.index('if (platform.generation() != generation) return RoseAgentWork.unconfirmed();'))
        self.assertNotIn('released', read, 'tasks and displays never decide membership')

class AutomatedDevicesConsumerTest(unittest.TestCase):
    """#156 consumer of the contract agreed with OS Core (relayed 19:51:27Z): AutomatedPackageListener
    .onAutomatedDevicesChanged(long generation, int[] deviceIds, String[] ownerPackages, int[] ownerUids), a complete
    snapshot per callback, sent on registration even when empty, generation rising on every content change. OS Core owns
    the producer (background-control-modern-frameworks-base.patch, test_automated_devices_contract.py); this side owns
    RoseAgentStrip/RoseAgentWork and the consumer half of the paired scenarios."""
    SCENARIOS = FIXTURES/'automated-devices-scenarios.json'
    PRODUCER = ROOT/'os/patches/background-control-modern-frameworks-base.patch'

    @staticmethod
    def encode(values):
        if values is None:
            return '~'
        return f'{len(values)}:' + ','.join('^' if v is None else str(v) for v in values)

    def test_the_paired_scenarios_replay_through_the_production_decision(self):
        import json
        data = json.loads(self.SCENARIOS.read_text())
        lines = []
        for sc in data['scenarios']:
            lines.append(f"S\t{sc['name']}")
            for event in sc['events']:
                if event.get('reset'):
                    lines.append('R')
                    continue
                gen, ids, pkgs, uids = event['snapshot']
                lines.append('\t'.join(['G', str(gen), self.encode(ids), self.encode(pkgs), self.encode(uids)]))
                if event.get('appsMissing'):
                    continue  # the dispatch's second callback has not arrived
                # The same dispatch's per-device apps: given explicitly, or (when the producer is current) the
                # scenario's tasks of the owner's user on that device.
                if 'apps' in event:
                    pairs = event['apps']
                else:
                    owner_user = {d: (u // 100000) for d, u in zip(ids or [], uids or []) if isinstance(u, int)}
                    shown = {int(k): v for k, v in sc['sessionDevices'].items()}
                    pairs = sorted({(shown[t[1]], t[3]) for t in sc['tasks']
                        if t[1] in shown and shown[t[1]] in owner_user and t[2] == owner_user[shown[t[1]]]})
                    pairs = [list(x) for x in pairs]
                complete = event.get('complete', [True] * len(ids or []))
                agen = event.get('appsGeneration', gen)
                lines.append('\t'.join(['A', str(agen), self.encode(ids), self.encode(pkgs), self.encode(uids),
                    self.encode([str(c).lower() for c in complete] if complete is not None else None),
                    self.encode([a[0] for a in pairs]), self.encode([a[1] for a in pairs])]))
            for display, device in sc['sessionDevices'].items():
                lines.append(f'D\t{display}\t{device}')
            lines += [f'L\t{d}' for d in sc['released']]
            lines += ['\t'.join(['T'] + [str(v).lower() if isinstance(v, bool) else str(v) for v in t]) for t in sc['tasks']]
            lines.append('E')
        out = Path(tempfile.mkdtemp(prefix='rose-scenarios-'))
        self.addCleanup(shutil.rmtree, out, True)
        subprocess.run(['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '11', '-target', '11',
            '-Xlint:-options', '-d', str(out), str(OVERLAY/'RoseAgentWork.java'), str(ROOT/'os/tests/RoseAgentScenarios.java')],
            check=True, capture_output=True)
        result = subprocess.run(['java', '-cp', str(out), 'com.android.quickstep.fallback.RoseAgentScenarios',
            str(data['controllerUid']), str(data['user'])], input='\n'.join(lines) + '\n', capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        got = [l for l in result.stdout.splitlines() if '|' in l]
        self.assertEqual(len(got), len(data['scenarios']))
        for sc, line in zip(data['scenarios'], got):
            name, state, cards, held, reason, _members = line.split('|')
            want = sc['expect']
            with self.subTest(scenario=sc['name']):
                self.assertEqual(name, sc['name'])
                self.assertEqual(state, want['state'])
                self.assertEqual(cards, ','.join(map(str, want['cards'])))
                self.assertEqual(held, ','.join(map(str, want['unconfirmed'])))
                self.assertEqual(reason, '~' if want['reason'] is None else want['reason'])
        # The common contract: every scenario names its class, only 'cards' shows a card, and each class is covered.
        for sc in data['scenarios']:
            want = sc['expect']
            with self.subTest(contract=sc['name']):
                self.assertIn(want['contract'], ('cards', 'empty', 'loading', 'cannot_confirm', 'unreadable'))
                self.assertEqual(bool(want['cards']), want['contract'] == 'cards')
        self.assertEqual({sc['expect']['contract'] for sc in data['scenarios']},
            {'cards', 'empty', 'loading', 'cannot_confirm', 'unreadable'})
        names = ' '.join(sc['name'] for sc in data['scenarios'])
        for paired in ('1a ', '1b ', '2 ', '3 ', '4 ', '5 ', '6 ', '7 ', '8 ', '9 ', 'malformed'):
            self.assertIn(paired, names, f'paired scenario {paired.strip()} is covered')

    def test_the_consumer_overrides_exactly_the_agreed_callback(self):
        strip = (OVERLAY/'RoseAgentStrip.java').read_text()
        self.assertIn('@Override\n        public void onAutomatedDevicesChanged(long snapshotGeneration, int[] deviceIds, '
            'String[] ownerPackages,\n                int[] ownerUids) {', strip)
        self.assertEqual(strip.count('registerAutomatedPackageListener('), 1, 'the existing registration, nothing new')
        for absent in ('VIRTUAL_DISPLAY_FLAG_PUBLIC', 'getDisplays(', 'requestPermissions'):
            self.assertNotIn(absent, ControllerReadPermissionTest.code(), absent)

    def test_the_producer_patch_declares_the_agreed_callback_before_the_consumer_ships(self):
        """Merge gate: the consumer overrides onAutomatedDevicesChanged, so it must not reach a build whose platform
        does not declare it. Fails until OS Core's producer hunks are on this branch's base."""
        added = '\n'.join(l[1:] for l in self.PRODUCER.read_text().splitlines() if l.startswith('+') and not l.startswith('+++'))
        flat = re.sub(r'\s+', ' ', added)
        self.assertRegex(flat, r'void onAutomatedDevicesChanged\( ?long \w+, in int\[\] \w+, in String\[\] \w+, in int\[\] \w+\)',
            'IAutomatedPackageListener (oneway AIDL) declares the agreed callback')
        self.assertRegex(flat, r'default void onAutomatedDevicesChanged\( ?long \w+, (@NonNull )?int\[\] \w+, (@NonNull )?'
            r'String\[\] \w+, (@NonNull )?int\[\] \w+\) ?\{ ?\}',
            'AutomatedPackageListener declares it as a default no-op')
        self.assertRegex(flat, r'mListener\.onAutomatedDevicesChanged\(', 'the client delegate forwards it')
        # #156 parity (PR361): the same dispatch's per-device apps.
        self.assertRegex(flat, r'void onAutomatedDeviceAppsChanged\( ?long \w+, in int\[\] \w+, in String\[\] \w+, '
            r'in int\[\] \w+, in boolean\[\] \w+, in int\[\] \w+, in String\[\] \w+\)')
        self.assertRegex(flat, r'default void onAutomatedDeviceAppsChanged\( ?long \w+, (@NonNull )?int\[\] \w+, '
            r'(@NonNull )?String\[\] \w+, (@NonNull )?int\[\] \w+, (@NonNull )?boolean\[\] \w+, (@NonNull )?int\[\] \w+, '
            r'(@NonNull )?String\[\] \w+\) ?\{ ?\}')
        self.assertRegex(flat, r'mListener\.onAutomatedDeviceAppsChanged\(')
        self.assertRegex(flat, r'listener\.onAutomatedDevicesChanged\( generation, snapshot\.deviceIds, snapshot\.ownerPackages, '
            r'snapshot\.ownerUids\); listener\.onAutomatedDeviceAppsChanged\(generation,',
            'both callbacks of one dispatch, devices first, one generation')

class LivePreviewTest(unittest.TestCase):
    """#156 owner acceptance: each of Rose's cards shows an updating view of the app's actual contents, never an icon,
    canned image or stale screenshot labelled live; the preview never mutates the agent's snapshot or execution and
    never foregrounds the app; lock and secure-content protection hold; unavailable capture says so. The platform path
    (frameworks/base 84183aff, pinned) is TaskSnapshotManager.takeTaskSnapshot(taskId, updateCache false, low
    resolution), which Launcher3 Quickstep's existing MANAGE_ACTIVITY_TASKS + READ_FRAME_BUFFER already permit.
    Source and fixture evidence only: no frame was captured on a device here."""
    PINNED = {'SnapshotController.java': '6d690ac2fb74c42ef7163a06a90ee901e8cebfbd262438a58fdefd6ac2df0bc5',
        'AbsAppSnapshotController.java': '2730ed03693283d21b6404e363fe40fe3ac0fcbb62efc09a771c8c56c8a7ef28',
        'ScreenCaptureInternal.java': '57f806c099f803ce45d3b1981e3fb3cf531386b8918fd1897c99a3c04639e48e',
        'ScreenCapture.java': '2cf75e91d8ea82e5524e7b71e60bf4539ba3560dcd0bc2e0ac14f6c356a10d72',
        'TaskSnapshotManager.java': '0d3f536d81fb2ff1c3ae461e6749d4e6e889cc12ff9a99620f2c06885124b04a',
        'TaskSnapshot.java': '318ad5a2368bb6bc75665c36f7d45d3f704860d331257766663e10499230b31c',
        'ActivityManagerWrapper.java': '370515816e25ef854329b60dd21a1646749ad57ea7f1ccc7bf15b4c81f346dc4',
        'ActivityTaskManagerService.java': '28d200431fd4a7289e3bf8f27d60f0c9472a8912da0211cf3bd5773eee170330'}
    method = staticmethod(PrivateControllerDisplayTest.method)

    @staticmethod
    def source(name):
        return (FIXTURES/name).read_text()

    def test_fixtures_are_the_pinned_files(self):
        for name, digest in self.PINNED.items():
            self.assertEqual(hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest(), digest, name)

    def test_the_platform_path_is_permitted_fresh_uncached_redacting_and_visible_only(self):
        atms = self.source('ActivityTaskManagerService.java')
        gate = atms[atms.index('public ITaskSnapshotManager getTaskSnapshotManager() {') - 120:
            atms.index('public ITaskSnapshotManager getTaskSnapshotManager() {')]
        self.assertIn('@EnforcePermission(allOf = {MANAGE_ACTIVITY_TASKS, READ_FRAME_BUFFER})', gate)
        manifest = (FIXTURES/'Launcher3-quickstep-AndroidManifest.xml').read_text()
        for held in ('android.permission.MANAGE_ACTIVITY_TASKS', 'android.permission.READ_FRAME_BUFFER'):
            self.assertIn(f'<uses-permission android:name="{held}"/>', manifest, f'{held} is already held')
        client = self.source('TaskSnapshotManager.java')
        self.assertIn('return ActivityTaskManager.getService().getTaskSnapshotManager();', client)
        self.assertIn('return takeTaskSnapshot(taskId, updateCache, lowResolution, false /* includeDecors */);', client)
        take = self.method(self.source('SnapshotController.java'), 'public TaskSnapshot takeTaskSnapshot(int taskId, boolean updateCache,')
        self.assertIn('if (task == null || !task.isVisible()) {', take, 'a task that is not visible gives no frame')
        self.assertIn("snapshot will always be taken and the snapshot won't be put into", take)
        self.assertIn('freshSnapshot = mTaskSnapshotController.snapshot(task, includeDecors);', take.split('} else {')[1])
        self.assertIn('ScreenCaptureInternal.captureLayersExcluding(', self.source('AbsAppSnapshotController.java'))
        exclude = self.method(self.source('ScreenCaptureInternal.java'), 'public static ScreenshotHardwareBuffer captureLayersExcluding(')
        self.assertNotIn('setSecureContentPolicy', exclude, 'the default secure-content policy applies')
        self.assertIn('public static final int SECURE_CONTENT_POLICY_REDACT = 0;', self.source('ScreenCapture.java'),
            'the default (0) blacks out secure windows; they are never captured')
        snapshot = self.source('TaskSnapshot.java')
        for member in ('public Bitmap wrapToBitmap(@Nullable ColorSpace colorSpace) {', 'public void closeBuffer() {',
                'public boolean isBufferValid() {', 'public ColorSpace getColorSpace() {'):
            self.assertIn(member, snapshot)
        wrapper = self.method(self.source('ActivityManagerWrapper.java'), 'public ThumbnailData takeTaskThumbnail(int taskId) {')
        self.assertIn('true /* updateCache */', wrapper, 'Launcher3\'s own helper writes the recents cache, so it is not used')

    def test_the_row_captures_only_roses_front_cards_fresh_and_never_reuses_a_frame(self):
        code = ControllerReadPermissionTest.code()
        self.assertEqual(code.count('takeTaskSnapshot('), 1)
        self.assertIn('TaskSnapshotManager.getInstance().takeTaskSnapshot(taskId, false ,\n                    true );', code)
        for stale in ('takeTaskThumbnail', 'getTaskThumbnail', 'getTaskSnapshot(', 'ThumbnailCache', 'getApplicationIcon',
                'loadIcon', 'getActivityIcon', 'setImageResource', 'setImageDrawable'):
            self.assertNotIn(stale, code, f'{stale}: no cached thumbnail or icon is shown as a live view')
        capture = code[code.index('private static Map<Integer, Bitmap> capture('):code.index('private static Bitmap frame(')]
        self.assertIn('if (work.state != RoseAgentWork.State.AVAILABLE) return frames;', capture)
        self.assertIn('for (RoseAgentWork.Task task : work.items) {', capture, "only Rose's attested cards")
        self.assertIn('if (RoseAgentWork.preview(task, locked) != RoseAgentWork.Preview.CAPTURE) continue;', capture)
        frame = code[code.index('private static Bitmap frame('):code.index('private static void release(')]
        self.assertIn('} finally {\n            if (snapshot != null) snapshot.closeBuffer();', frame)
        self.assertIn('return null;', frame.split('catch')[1], 'a failed capture gives no frame')
        refresh = code[code.index('void refresh() {'):code.index('private static boolean locked(')]
        self.assertIn('RoseAgentWork next = locked ? RoseAgentWork.locked() : read(context);', refresh,
            'locked: no app is listed (the common contract), so nothing is read or captured')
        self.assertLess(refresh.index('RoseAgentWork next = locked ? RoseAgentWork.locked() : read(context);'),
            refresh.index('capture(next, locked, framePx)'),
            'frames are taken for the cards of this very read')
        show = code[code.index('private void show(RoseAgentWork next, Map<Integer, Bitmap> frames, boolean locked) {'):
            code.index('private void select(')]
        self.assertLess(show.index('clearCards();'), show.index('shownFrames = frames;'), 'the previous frames are released')
        self.assertIn('Bitmap frame = frames.get(task.taskId);', show)
        self.assertIn('String why = RoseAgentWork.previewText(RoseAgentWork.preview(task, locked), frame != null);', show)
        self.assertIn('if (why == null) {\n            ImageView live = new ImageView(getContext());', show)
        # #410 in-place rebind: the same cards keep their views, and the previous frames are released only after every
        # card has this read's frame or reason (never a frame from an earlier read, never a recycled one on screen).
        self.assertLess(show.index('bind(card, task, frames, locked);'), show.index('if (same) release(previous);'))
        self.assertIn('if (!same) clearCards();', show)
        visibility = code[code.index('protected void onWindowVisibilityChanged(int visibility) {'):code.index('private void setWorking(')]
        hidden = visibility.split('} else {')[1]
        for step in ('removeCallbacks(previewTick);', 'generation++;', 'selection++;', 'clearCards();'):
            self.assertIn(step, hidden, f'leaving the overview: {step}')
        detached = code[code.index('protected void onDetachedFromWindow() {'):code.index('private void anchor()')]
        self.assertIn('clearCards();', detached)
        self.assertIn('selection++;', detached, 'a tap whose read lands after leaving neither shows frames nor opens')
        locked = code[code.index('private static boolean locked(Context context) {'):code.index('private static Map<Integer, Bitmap> capture(')]
        self.assertIn('return keyguard == null || keyguard.isKeyguardLocked();', locked)
        self.assertIn('return true;', locked.split('catch')[1], 'an unreadable lock state counts as locked')
        for mutating in ('moveTaskToFront', 'startActivityFromRecents', 'setFocusedRootTask', 'requestScreenshot',
                'AgentProvider', 'getContentResolver'):
            self.assertNotIn(mutating, code, f'{mutating}: the preview never touches the agent or foregrounds the app')

    def test_the_hifi_card_keeps_its_place_and_auto_pip_stays_off(self):
        code = ControllerReadPermissionTest.code()
        self.assertNotIn('enterPictureInPictureMode', code)
        self.assertNotIn('PictureInPicture', code)
        tree = patched_tree(self)
        height = int(re.search(r'name="rose_agent_strip_height">(\d+)dp', (tree/DIMENS).read_text())[1])
        self.assertEqual(height, 200, '#410: the claimed space grows only to hold the Oxblood Rose card (146 dp)')

class DeviceAppsParityTest(unittest.TestCase):
    """#156 parity (PR361 on main): membership is the platform's per-device app list, front or hidden, the same source
    App Core's session-owner getAutomatedApps reads. Source contract for the consumer only."""

    def test_the_strip_applies_the_apps_callback_and_never_captures_a_member_without_a_task(self):
        strip = (OVERLAY/'RoseAgentStrip.java').read_text()
        self.assertIn('public void onAutomatedDeviceAppsChanged(long snapshotGeneration, int[] deviceIds, String[] ownerPackages,\n'
            '                int[] ownerUids, boolean[] deviceComplete, int[] appDeviceIds, String[] appPackageNames) {', strip)
        self.assertIn('if (!ownership.acceptApps(snapshotGeneration, deviceIds, ownerPackages, ownerUids, deviceComplete,', strip)
        self.assertIn('if (task.taskId == RoseAgentWork.NO_TASK) continue;', strip)
        work = re.sub(r'/\*.*?\*/', '', (OVERLAY/'RoseAgentWork.java').read_text(), flags=re.S)
        self.assertIn('if (members.contains(t.packageName) && attached.add(t.packageName)) items.add(t);', work,
            'tasks only attach to members; an unlisted task is never a card and holds nothing back')
        self.assertIn('if (!attached.contains(pkg)) items.add(new Task(NO_TASK, display, currentUser, pkg, pkg, false));', work)
        self.assertIn('if (card.taskId == NO_TASK) return Preview.NONE;', work, 'no visible task claims no preview reason')
        for history in ('completed', 'finished', 'history', 'lastResult', 'result'):
            self.assertNotIn(history, work.split('static RoseAgentWork of(')[1].split('static RoseAgentWork unconfirmed()')[0],
                f'{history}: membership never reads task history')

class CommonParityReplayTest(unittest.TestCase):
    """#156/#225: Launcher3's consumer replay of App Core's common parity fixture (qa/parity/current-apps-parity.json,
    owned by App Core; read here, never edited). Each case's platform input becomes the exact listener callbacks
    (onAutomatedDevicesChanged then onAutomatedDeviceAppsChanged, one generation) and runs through the production
    RoseAgentWork; the row's class (cards with their packages, genuine_empty, or cannot_confirm) must equal the case's
    expected. A case Launcher3 cannot replay (platform null) is never counted as a parity pass, and each of the fixture's
    knownGaps is replayed for Launcher3's own side and stays parity not_claimed."""
    FIXTURE = ROOT/'qa/parity/current-apps-parity.json'
    UID, OTHER_UID, CONTROLLER = 10123, 10456, 'dev.pixel.backgroundcontrol'
    enc = staticmethod(AutomatedDevicesConsumerTest.encode)

    def lines(self, name, platform, gen, reset=False):
        """The listener callbacks and platform reads Launcher3 would get for one platform input."""
        out = [f'S\t{name}'] if name else []
        if reset:
            out.append('R')
        if platform.get('locked'):
            return out + ['K']
        if platform.get('unavailable'):
            # A live session exists (its display is listed) but no ownership snapshot reached Launcher3.
            return out + ['D\t101\t1', 'T\t1\t101\t0\tcom.example.app\ttrue', 'E']
        if platform.get('malformed'):
            out.append('\t'.join(['G', str(gen), self.enc([1]), self.enc([self.CONTROLLER]), self.enc([self.UID])]))
            out.append('\t'.join(['A', str(gen), self.enc([1]), self.enc([self.CONTROLLER]), self.enc([self.UID]),
                self.enc(['true']), self.enc([1, 1]), self.enc(['com.example.app'])]))
            return out + ['D\t101\t1', 'E']
        devices = platform['devices']
        ids = list(range(1, len(devices) + 1))
        owners = [self.CONTROLLER if d['owner'] == 'controller' else 'com.other.agent' for d in devices]
        uids = [self.UID if d['owner'] == 'controller' else self.OTHER_UID for d in devices]
        complete = [str(bool(d['complete'])).lower() for d in devices]
        app_ids = [i for i, d in zip(ids, devices) for _ in d['apps']]
        app_pkgs = [a for d in devices for a in d['apps']]
        out.append('\t'.join(['G', str(gen), self.enc(ids), self.enc(owners), self.enc(uids)]))
        out.append('\t'.join(['A', str(gen), self.enc(ids), self.enc(owners), self.enc(uids), self.enc(complete),
            self.enc(app_ids), self.enc(app_pkgs)]))
        for i in ids:
            out.append(f'D\t{100 + i}\t{i}')
        return out + ['E']

    def run_lines(self, lines):
        out = Path(tempfile.mkdtemp(prefix='rose-parity-'))
        self.addCleanup(shutil.rmtree, out, True)
        subprocess.run(['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '11', '-target', '11',
            '-Xlint:-options', '-d', str(out), str(OVERLAY/'RoseAgentWork.java'), str(ROOT/'os/tests/RoseAgentScenarios.java')],
            check=True, capture_output=True)
        result = subprocess.run(['java', '-cp', str(out), 'com.android.quickstep.fallback.RoseAgentScenarios',
            str(self.UID), '0'], input='\n'.join(lines) + '\n', capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [l.split('|') for l in result.stdout.splitlines() if '|' in l]

    @staticmethod
    def category(state, members):
        if state == 'AVAILABLE':
            return 'cards', sorted(members.split(',')) if members else []
        if state == 'EMPTY':
            return 'genuine_empty', []
        return 'cannot_confirm', []

    def load(self):
        import json
        self.assertTrue(self.FIXTURE.is_file(), 'App Core\'s common fixture is on this base (PR370)')
        return json.loads(self.FIXTURE.read_text())

    def test_every_replayable_case_matches_and_the_rest_are_named_differences(self):
        data = self.load()
        self.assertEqual(set(data['categories']), {'cards', 'genuine_empty', 'cannot_confirm'})
        cases = [c for c in data['cases'] if c['platform'] is not None]
        not_replayable = [c['name'] for c in data['cases'] if c['platform'] is None]
        lines = []
        for c in cases:
            lines += self.lines(c['name'], c['platform'], 1)
        got = self.run_lines(lines)
        self.assertEqual([g[0] for g in got], [c['name'] for c in cases])
        for c, g in zip(cases, got):
            with self.subTest(case=c['name']):
                self.assertEqual(self.category(g[1], g[5]), (c['expected']['category'], sorted(c['expected']['apps'])))
        self.assertEqual(not_replayable, ['session_unreleased_cleanup_failed'],
            'only the controller-only case is not replayable here; any new one must be added to the fixture and resolved')
        gaps = ' '.join(g['description'] for g in data.get('knownGaps', []))
        self.assertIn('cleanup_failed', gaps, 'the controller-only case is a named known gap, not a parity pass')

    def test_sequences_replay_through_one_ledger(self):
        data = self.load()
        by_name = {c['name']: c for c in data['cases']}
        for seq in data['sequences']:
            lines, expected = [f'S\t{seq["name"]}'], []
            gen = 0
            for step in seq['steps']:
                lines.append('C')  # each step is a fresh read of the platform; the ledger is the same registration
                if step.get('reconnect'):
                    # A new registration: nothing reported yet while Rose's session display is still listed.
                    lines += ['R', 'D\t101\t1', 'T\t1\t101\t0\tcom.example.app\ttrue', 'E']
                else:
                    gen += 1
                    lines += self.lines(None, by_name[step['case']]['platform'], gen)
                expected.append((step['expected']['category'], sorted(step['expected']['apps'])))
            got = self.run_lines(lines)
            with self.subTest(sequence=seq['name']):
                self.assertEqual([self.category(g[1], g[5]) for g in got], expected)

    def test_each_known_gap_replays_to_launcher3s_stated_side_and_stays_not_claimed(self):
        """Each of the fixture's knownGaps with a platform input, whichever App Core lists: Launcher3's own side is
        replayed from the gap's platform input and must equal its launcherCategory (and launcherApps, when stated); the
        gap is parity not_claimed, with the two surfaces in different categories or, within the same category, naming
        different sets (launcherApps, homeApps). A gap is an open difference, never a parity pass."""
        data = self.load()
        gaps = [g for g in data.get('knownGaps', []) if g.get('platform') is not None]
        lines = []
        for g in gaps:
            lines += self.lines(g['name'], g['platform'], 1)
        got = self.run_lines(lines)
        self.assertEqual([row[0] for row in got], [g['name'] for g in gaps])
        for g, row in zip(gaps, got):
            with self.subTest(gap=g['name']):
                self.assertEqual(row[0], g['name'])
                launcher = self.category(row[1], row[5])
                self.assertEqual(launcher[0], g['launcherCategory'])
                if 'launcherApps' in g:
                    self.assertEqual(launcher[1], sorted(g['launcherApps']), 'Launcher3\'s stated set')
                self.assertEqual(g['parity'], 'not_claimed')
                # NOT parity: the surfaces differ in category, or in the set shown within the same category.
                self.assertNotEqual((g['launcherCategory'], sorted(g.get('launcherApps', []))),
                    (g['homeCategory'], sorted(g.get('homeApps', []))), 'NOT parity: a named, open difference')
                if g['launcherCategory'] == g['homeCategory']:
                    self.assertIn('launcherApps', g, 'a same-category gap names both sets')
                    self.assertIn('homeApps', g, 'a same-category gap names both sets')

class OxbloodRoseCarouselTest(unittest.TestCase):
    """#410: the switcher's Rose carousel as the frozen Oxblood HiFi (prototypes/rose-hifi at f6503a0d) draws it: a
    centred fan with depth (RoseFan, checked against the transforms Chromium computed for the pinned page) that snaps
    exactly on a card. Source and JVM checks only: touch feel, rendering and timing need a device recording."""
    FAN = (OVERLAY/'RoseAgentFan.java').read_text()
    STRIP = (OVERLAY/'RoseAgentStrip.java').read_text()

    def test_the_fan_geometry_and_placement_match_the_frozen_reference(self):
        out = Path(tempfile.mkdtemp(prefix='rose-fan-'))
        self.addCleanup(shutil.rmtree, out, True)
        subprocess.run(['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '11', '-target', '11',
            '-Xlint:-options', '-d', str(out), str(OVERLAY/'RoseFan.java'), str(ROOT/'os/tests/RoseFanTest.java')],
            check=True)
        result = subprocess.run(['java', '-cp', str(out), 'com.android.quickstep.fallback.RoseFanTest'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_release_cancel_and_fling_settles_exactly_on_a_card(self):
        touch = self.FAN[self.FAN.index('public boolean onTouchEvent('):self.FAN.index('public boolean executeKeyEvent(')]
        self.assertIn('if (action == MotionEvent.ACTION_DOWN) flung = false;', touch, 'each gesture starts unflung')
        self.assertIn('if (event.getActionMasked() == MotionEvent.ACTION_DOWN) flung = false;\n        return super.onInterceptTouchEvent(event);',
            self.FAN, 'also when the gesture starts on a card (onTouchEvent then never sees its ACTION_DOWN)')
        self.assertIn('if ((action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) && !flung) '
            'settle(centred());', touch, 'a drag released or cancelled (e.g. by the system gesture) settles on a card')
        fling = self.FAN[self.FAN.index('public void fling(int velocityX)'):self.FAN.index('public boolean onTouchEvent(')]
        self.assertIn('settle(centred() + RoseFan.orderStep(velocityX > 0 ? 1 : velocityX < 0 ? -1 : 0, rtl()));', fling,
            'a fling moves one card, never skipping work')
        self.assertNotIn('super.fling', fling, 'no free momentum that could stop between cards')

    def test_reduced_motion_moves_at_once_and_keeps_the_static_fan(self):
        settle = self.FAN[self.FAN.index('private void settle(int index)'):self.FAN.index('protected void onLayout(')]
        self.assertIn('if (ValueAnimator.areAnimatorsEnabled()) smoothScrollTo(x, 0);\n        else scrollTo(x, 0);', settle)
        for timed in ('postDelayed', 'Thread.sleep', 'SystemClock', 'currentTimeMillis', 'nanoTime', 'Handler(',
                'ObjectAnimator', 'INFINITE'):
            self.assertNotIn(timed, self.FAN, f'{timed}: no timer or endless animation in the carousel')

    def test_keys_step_one_card_clamped_and_accessibility_focus_centres(self):
        keys = self.FAN[self.FAN.index('public boolean executeKeyEvent('):self.FAN.index('void focused(View card)')]
        self.assertIn('case KeyEvent.KEYCODE_DPAD_LEFT: step = RoseFan.orderStep(-1, rtl()); break;', keys)
        self.assertIn('return event.getAction() == KeyEvent.ACTION_DOWN && move(step);', keys,
            'one card per key; at an end the key is not taken, so focus can leave the row')
        move = self.FAN[self.FAN.index('private boolean move(int orderStep)'):self.FAN.index('protected void onMeasure(')]
        self.assertIn('int from = focused != null ? cards.indexOfChild(focused) : centred();', move)
        self.assertIn('if (target == from) return false;', move)
        for action in ('ACTION_SCROLL_FORWARD) return move(RoseFan.orderStep(1, rtl()));',
                'ACTION_SCROLL_BACKWARD) return move(RoseFan.orderStep(-1, rtl()));',
                'accessibilityActionScrollRight) return move(RoseFan.orderStep(1, rtl()));'):
            self.assertIn(action, self.FAN, 'a screen reader scroll moves exactly one card')
        self.assertIn('move(RoseFan.orderStep(h > 0 ? 1 : -1, rtl()));', self.FAN, 'a wheel notch moves one card')
        self.assertIn('card.setOnFocusChangeListener(scroller.centreOnKeyFocus());', self.STRIP)
        self.assertIn('if (eventType == AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUSED) focused(host);', self.FAN)
        self.assertIn('card.setAccessibilityDelegate(scroller.centreOnFocus());', self.STRIP)
        self.assertIn('card.setContentDescription(', self.STRIP, 'every card keeps its spoken name and state')

    def test_the_fan_only_places_cards_it_is_given(self):
        for banned in ('getTasks', 'takeTaskSnapshot', 'startActivity', 'VirtualDeviceManager', 'RoseAgentWork.of',
                'getSystemService', 'removeView', 'addView('):
            self.assertNotIn(banned, self.FAN, f'{banned}: the fan never reads, captures, opens, adds or removes')
        self.assertIn('card.setTranslationZ(RoseFan.depth(k) * density / 2f);', self.FAN, 'the centre card draws and '
            'takes touches on top (ViewGroup orders both by Z)')

    def test_entry_places_afresh_and_a_refresh_never_removes_the_card_under_a_finger(self):
        show = self.STRIP[self.STRIP.index('private void show(RoseAgentWork next'):self.STRIP.index('private LinearLayout card()')]
        self.assertIn('boolean same = keys.equals(before) && cards.getChildCount() == keys.size();', show)
        self.assertIn('LinearLayout card = same ? (LinearLayout) cards.getChildAt(index) : card();', show)
        self.assertIn('if (!scroller.placed()) { if (!keys.isEmpty()) scroller.place(RoseFan.initial(keys.size())); }', show)
        self.assertIn('return RoseFan.scrollToCentre(centres[RoseFan.step(index, 0, centres.length)], getWidth());',
            self.FAN, 'index to scroll from where the card is laid out, so right to left places the right card')
        self.assertIn('else if (!same) scroller.place(RoseFan.keep(before, centred, keys));', show)
        self.assertIn('return task.displayId + "/" + task.userId + "/" + task.packageName;', self.STRIP,
            'a card is the same card by display, user and package, as RoseAgentWork.decide matches it')

    def test_status_and_count_stay_readable_at_every_supported_font_scale(self):
        """#410: the row is measured for the person's font size (RoseFan.rowHeightPx, checked over scales 0.85-2.0 in
        RoseFanTest): the overview claims that height, the row takes it, and the status keeps two full lines."""
        measure = self.STRIP[self.STRIP.index('protected void onMeasure('):self.STRIP.index('private final View.OnLayoutChangeListener')]
        self.assertIn('MeasureSpec.makeMeasureSpec(rowHeight(getResources()), MeasureSpec.EXACTLY)', measure)
        self.assertIn('status.setMaxLines(RoseFan.STATUS_LINES);', self.STRIP)
        self.assertIn('status = text("", STATUS_SP);', self.STRIP)
        self.assertIn('link = text("", LINK_SP);', self.STRIP)
        self.assertIn('LINK_SP = 13, STATUS_SP = 13;', self.STRIP)
        tree = patched_tree(self)
        fallback = (tree/f'{QUICKSTEP}/FallbackActivityInterface.java').read_text()
        self.assertIn('RoseAgentStrip.rowHeight(context.getResources())', fallback, 'the human cards make the same room')
        actions = (tree/f'{QUICKSTEP}/views/OverviewActionsView.java').read_text()
        self.assertIn('RoseAgentStrip.reservedBelow(getContext())', actions, 'the actions sit below the same height')

    def test_the_live_view_is_the_real_frame_at_the_cards_width_never_stretched(self):
        self.assertEqual(self.STRIP.count('int framePx = dp(RoseFan.CARD_WIDTH_DP);'), 2, 'the row and the tap read alike')
        self.assertIn('Bitmap scaled = Bitmap.createScaledBitmap(copy, framePx, height, true);', self.STRIP)
        self.assertIn('int height = Math.max(1, Math.round(copy.getHeight() * (framePx / (float) copy.getWidth())));',
            self.STRIP, 'the task keeps its own aspect')
        self.assertIn('live.setScaleType(ImageView.ScaleType.MATRIX);', self.STRIP, 'its top at 1:1, no crop-zoom')

    def test_oxblood_materials_match_homes_contract_key_for_key(self):
        """qa/oxblood/oxblood-contract.json is Home's (single writer); Launcher3 keeps its own copy and asks for changes
        on #410. A mismatch here is a coordination signal, never a reason to edit the contract from this side."""
        import json
        contract = json.loads((ROOT/'qa/oxblood/oxblood-contract.json').read_text())
        def argb(name):
            return int(re.search(rf'\b{name} = (0x[0-9A-F]{{8}})', self.STRIP)[1], 16)
        def hexval(value):
            return int(value.lstrip('#'), 16)
        self.assertEqual(argb('CARD_FILL'), hexval(contract['manager']['card']['fill']))
        self.assertEqual(argb('CARD_RING'), hexval(contract['manager']['card']['neutralRing']['color']))
        self.assertEqual(argb('TEXT'), hexval(contract['ink']['primary']))
        self.assertEqual(argb('TEXT_MUTED'), hexval(contract['ink']['secondary']))
        self.assertEqual(argb('CURRENT_RING'), hexval(contract['ink']['secondary']))
        colors = [int(c, 16) for c in re.search(r'PEARL_COLORS = \{([^}]*)\}', self.STRIP)[1].replace(' ', '').split(',')]
        stops = [float(v.rstrip('f')) for v in re.search(r'PEARL_STOPS = \{([^}]*)\}', self.STRIP)[1].replace(' ', '').split(',')]
        ruby = [(stop, hexval(color)) for stop, color in contract['pearl']['ruby']['stops'] if stop > 0]
        self.assertEqual(list(zip(stops, colors)), [(round(a, 2), b) for a, b in ruby])
        self.assertEqual(contract['pearl']['ruby']['stops'][0][1], contract['pearl']['ruby']['stops'][1][1],
            'the first stop holds the colour of the 0.07 stop (GradientDrawable clamps before its first offset)')
        self.assertIn(f"PEARL_DP = {contract['pearl']['sizes']['switcher']}", self.STRIP)
        self.assertIn('radius = distance to the farthest corner', contract['pearl']['ruby']['gradient'])
        self.assertIn('Math.hypot(0.66 * PEARL_DP, 0.72 * PEARL_DP)', self.STRIP)
        self.assertIn('runningEdge', contract['manager'])
        self.assertIn('authoritatively running only', contract['manager']['runningEdge']['when'])

    def test_the_rose_fan_card_and_pearl_match_homes_switcher_keys(self):
        """#423: Home added the switcher keys (fan, Rose row card, pearl) to the contract; RoseFan's own copy is
        compared key for key, so a later contract change is a coordination signal on #410."""
        import json
        switcher = json.loads((ROOT/'qa/oxblood/oxblood-contract.json').read_text())['switcher']
        fan = (OVERLAY/'RoseFan.java').read_text()
        def const(name):
            return float(re.search(rf'\b{name} = ([0-9.]+)f?\b', fan)[1])
        self.assertEqual(const('PERSPECTIVE_DP'), switcher['fan']['perspective'])
        self.assertEqual(const('MAX_OFFSET'), switcher['fan']['clamp'])
        rose = switcher['fan']['rose']
        self.assertEqual((const('SHIFT_DP'), const('TURN_DEG'), const('SHRINK')), (rose['shift'], rose['turnDeg'], rose['shrink']))
        card = switcher['rose']['card']
        self.assertEqual((const('CARD_WIDTH_DP'), const('CARD_HEIGHT_DP'), const('CARD_RADIUS_DP')),
            (card['width'], card['height'], card['radius']))
        self.assertIn('10 - round(|k| * 4)', switcher['fan']['depth'])
        self.assertIn('return 10 - Math.round(Math.abs(k) * 4);', fan)
        self.assertEqual(switcher['rose']['initialCard'], 'the second card when there are three or more')
        self.assertIn('static int initial(int count) { return count >= 3 ? 1 : 0; }', fan)
        self.assertIn(f"PEARL_DP = {switcher['pearl']['size']}", self.STRIP)
        self.assertEqual(switcher['snap']['reducedMotion'], 'instant')

    def test_oxblood_materials_and_the_pearl_show_no_mic_or_call_state(self):
        self.assertIn('static final int[] PEARL_COLORS = {0xFFFFE6E8, 0xFFE8606F, 0xFFC43048, 0xFF8A1428, 0xFF4A0612};',
            self.STRIP)
        for real in ('SensorPrivacy', 'setMicrophoneMute', 'isMicrophoneMute', 'RECORD_AUDIO', 'AudioManager'):
            self.assertNotIn(real, self.STRIP + self.FAN, f'{real}: the switcher pearl is not a mic control or indicator')

class OxbloodSwitcherBackdropTest(unittest.TestCase):
    """#410: the smallest safe alignment of the whole switcher surface. The fallback overview's own scrim (the colour
    layer RecentsState already draws behind both carousels, FLAG_SCRIM) becomes the Oxblood switcher base from Home's
    contract. It is a theme value applied by RecentsActivity after the stock fallback style: no task, thumbnail, live
    tile, gesture or launch code changes, and Launcher3's own in-launcher overview keeps its style."""

    def test_the_scrim_is_the_contracts_switcher_base_in_the_fallback_overview_only(self):
        import json
        contract = json.loads((ROOT/'qa/oxblood/oxblood-contract.json').read_text())
        tree = patched_tree(self)
        values = (tree/DIMENS).read_text()
        style = re.search(r'<style name="RoseOverviewOxblood">\s*<item name="overviewScrimColor">(#[0-9A-F]{8})</item>\s*</style>',
            values)
        self.assertIsNotNone(style, values)
        self.assertEqual(style[1], contract['fields']['screenBase'])
        activity = (tree/f'{QUICKSTEP}/RecentsActivity.java').read_text()
        stock = '        getTheme().applyStyle(R.style.OverviewBlurFallbackStyle, true);\n'
        rose = '        getTheme().applyStyle(R.style.RoseOverviewOxblood, true);\n'
        self.assertEqual(activity.count(rose), 1)
        self.assertLess(activity.index(stock), activity.index(rose), 'applied after the stock style, so it wins')
        self.assertLess(activity.index(rose), activity.index('inflateRootView(R.layout.fallback_recents_activity);'),
            'before the views read the theme')
        hunk = PATCH.read_text().split('RecentsActivity.java b/')[1].split('\ndiff --git')[0]
        self.assertFalse([l for l in hunk.splitlines() if l.startswith('-') and not l.startswith('---')],
            'RecentsActivity only gains the style; nothing of it is removed')
        stock_scrim = (FIXTURES/'RecentsActivity.java').read_text()
        self.assertIn(stock, stock_scrim)

class OxbloodSwitcherFieldTest(unittest.TestCase):
    """#410 S2: the switcher's Oxblood field (the contract's fields.rose, the pinned oxblood-rose.svg) drawn by OS
    Product's own Launcher3 renderer on the fallback overview's scrim, through ScrimView's public drawing-controller seam,
    at the scrim's own current opacity. Native renders and the comparison with Chromium's raster of the SVG are in
    os/tests/native-render-switcher (RoseOxbloodFieldNativeTest)."""
    STRIP = (OVERLAY/'RoseAgentStrip.java').read_text()
    FIELD = (OVERLAY/'RoseOxbloodField.java').read_text()

    def test_the_field_is_the_pinned_svg_shape_for_shape(self):
        svg = (ROOT/'qa/oxblood/oxblood-contract.json').read_text()
        import json
        contract = json.loads(svg)
        self.assertEqual(contract['fields']['rose']['source'], 'assets/oxblood-rose.svg')
        self.assertEqual(contract['switcher']['field']['kind'], 'rose')
        self.assertIn('scale = max(W/390, H/844); centred horizontally; top-aligned', contract['fields']['rose']['placement'])
        for value in ('SHEET = {390, 118, 390, 292, 295, 580}', 'PLANE = {295, 580, 390, 515, 390, 844, 123, 844, 206, 642}',
                'BASE = {0xFF120A0D, 0xFF0E080B, 0xFF12060A}', 'BASE_STOPS = {0f, .6f, 1f}',
                'SHEET_COLORS = {0x598A3A4C, 0x008A3A4C}', 'PLANE_COLORS = {0xE67A1428, 0xFF36020D, 0xFF1A0207}',
                'PLANE_STOPS = {0f, .45f, 1f}', 'GLINT = {0xE6FFF0F0, 0x00FF8A9A}',
                'GLINT_X = 295, GLINT_Y = 580, GLINT_R = 26, BLUR_STD_DEV = 3',
                'return Math.max(width / VIEW_W, height / VIEW_H);'):
            self.assertIn(value, self.FIELD, value)
        for forbidden in ('ValueAnimator', 'postDelayed', 'Handler(', 'SystemClock'):
            self.assertNotIn(forbidden, self.FIELD, f'{forbidden}: static artwork')

    def test_the_scrim_seam_is_the_pinned_upstream_api(self):
        """The compile stub's ScrimView members are the pinned Launcher3 47accce ones, byte-pinned here."""
        code = FIXTURES/'ScrimView.java'
        self.assertEqual(hashlib.sha256(code.read_bytes()).hexdigest(), 'be8859147c13b41e1e9505be3e6e0423d75a4a8a6efd957a6b87e84e1908dd39')
        source = code.read_text()
        for member in ('public int getBackgroundColor() {', 'public void setDrawingController(ScrimDrawingController drawingController) {',
                'public interface ScrimDrawingController {', 'void drawOnScrimWithScaleAndBottomOffset(\n                Canvas canvas, float scale, @Px int bottomOffsetPx);',
                'mDrawingController.drawOnScrimWithScale(canvas, mHeaderScale);'):
            self.assertIn(member, source, member)
        layout = (FIXTURES/'fallback_recents_activity.xml').read_text()
        self.assertIn('<com.android.launcher3.views.ScrimView', layout)
        self.assertIn('android:id="@+id/scrim_view"', layout)

    def test_the_field_rides_the_scrims_own_state_and_is_released(self):
        attach = self.STRIP[self.STRIP.index('protected void onAttachedToWindow() {'):self.STRIP.index('protected void onDetachedFromWindow()')]
        self.assertIn('View found = parent.findViewById(R.id.scrim_view);', attach)
        self.assertIn('scrim.setDrawingController(fieldOnScrim);', attach)
        self.assertIn('field.drawAsScrim(canvas, on.getWidth(), on.getHeight(), Color.alpha(on.getBackgroundColor()));',
            self.STRIP, 'composited once as the scrim\'s own fill, at its current opacity')
        self.assertIn('if (found instanceof ScrimView && reservedBelow(getContext()) > 0) {', attach,
            'only in the RecentsActivity overview, where the Oxblood scrim style is applied')
        self.assertIn('layer.setXfermode(new PorterDuffXfermode(PorterDuff.Mode.SRC));', self.FIELD)
        self.assertIn('if (scrimAlpha <= 0 || width <= 0 || height <= 0) return;', self.FIELD)
        detach = self.STRIP[self.STRIP.index('protected void onDetachedFromWindow() {'):self.STRIP.index('private void anchor()')]
        self.assertIn('scrim.setDrawingController(null);', detach)
        for state in ('RecentsState', 'setScrimColors', 'setBackgroundColor('):
            self.assertNotIn(state, self.STRIP, f'{state}: no new state logic; the scrim keeps driving itself')

class OxbloodHumanCardTest(unittest.TestCase):
    """#410: the smallest safe alignment of the human (top) carousel's own cards. Only colour values in the overview's
    task card layouts change: each card's app-identity chip (IconAppChipView, which clips itself to its rounded outline
    in code and never reads its background) becomes an Oxblood plate with an ivory name and chevron and an ivory
    press/hover overlay, and the card's keyboard-focus border takes the contract's focus ring (hover stays stock, so
    the two stay distinct). The chip's expanded menu (TaskMenuView) keeps its stock colours. No TaskView, live tile,
    touch, dismiss, launch or navigation code, and no layout structure, changes."""

    def test_the_human_cards_chip_and_borders_take_the_contracts_colours_and_nothing_else(self):
        import json
        contract = json.loads((ROOT/'qa/oxblood/oxblood-contract.json').read_text())
        tree = patched_tree(self)
        values = (tree/DIMENS).read_text()
        colors = dict(re.findall(r'<color name="(rose_oxblood_\w+)">(#[0-9A-F]{8})</color>', values))
        fill = contract['manager']['card']['fill']
        self.assertEqual(colors['rose_oxblood_chip'], '#F2' + fill[3:], 'the card fill, kept nearly opaque (95%)')
        self.assertEqual(colors['rose_oxblood_ink'], contract['ink']['primary'])
        self.assertEqual(colors['rose_oxblood_focus_ring'], contract['ink']['focusRing']['color'])
        self.assertNotIn('rose_oxblood_hover_ring', colors, 'hover keeps the stock colour, distinct from focus')
        chip = (tree/'quickstep/res/layout/icon_app_chip_view.xml').read_text()
        self.assertIn('android:background="@color/rose_oxblood_chip"', chip)
        self.assertIn('launcher:focusBorderColor="@color/rose_oxblood_focus_ring"', chip)
        self.assertIn('android:textColor="@color/rose_oxblood_ink"\n        style="@style/IconAppChipMenuTextStyle" />', chip,
            'the name\'s colour only; its size, font and fading stay the stock style\'s')
        task = (tree/'quickstep/res/layout/task.xml').read_text()
        self.assertIn('launcher:focusBorderColor="@color/rose_oxblood_focus_ring"', task)
        self.assertIn('launcher:hoverBorderColor="@color/materialColorPrimary">', task, 'stock hover: focus stays distinct')
        # The chip's arrow takes no stock light backing and an ivory chevron; its press/hover overlay is the ivory ink
        # (the stock OnSurface overlay would vanish on the dark plate), same states and alphas as stock.
        self.assertIn('android:background="@drawable/rose_oxblood_arrow_background"\n        '
            'android:tint="@color/rose_oxblood_ink"\n        android:src="@drawable/ic_chevron_down"', chip)
        arrow = (tree/'quickstep/res/drawable/rose_oxblood_arrow_background.xml').read_text()
        for line in ('android:startColor="#00000000"', 'android:centerX="0.25"',
                'android:centerColor="@color/rose_oxblood_chip"', 'android:endColor="@color/rose_oxblood_chip"',
                '<corners android:radius="@dimen/dialogCornerRadius" />', 'Copyright (C) 2023 The Android Open Source Project'):
            self.assertIn(line, arrow, 'the stock gradient that masks the title under the arrow, fading into the plate')
        self.assertIn('android:foreground="@drawable/rose_oxblood_chip_fg"', chip)
        overlay = (tree/'quickstep/res/color/rose_oxblood_chip_state_fg.xml').read_text()
        for state in ('android:alpha="0.15" android:color="@color/rose_oxblood_ink" android:state_enabled="true" '
                'android:state_pressed="true"', 'android:alpha="0.11" android:color="@color/rose_oxblood_ink" '
                'android:state_enabled="true" android:state_hovered="true"'):
            self.assertIn(state, overlay)
        self.assertIn('Copyright (C) 2025 The Android Open Source Project', overlay, 'the upstream notice is kept')
        self.assertIn('<solid android:color="@color/rose_oxblood_chip_state_fg" />',
            (tree/'quickstep/res/drawable/rose_oxblood_chip_fg.xml').read_text())
        # Only attribute values change in these layouts: the same elements, ids and attribute names, in order.
        for name in ('task.xml', 'icon_app_chip_view.xml'):
            stock = (FIXTURES/name).read_text()
            patched = (tree/f'quickstep/res/layout/{name}').read_text()
            def shape(text):
                return re.findall(r'<\s*([\w.]+)|\b([\w:]+)="', text)
            added = [a for a in shape(patched) if a not in shape(stock)]
            self.assertEqual(sorted(set(added)), [] if name == 'task.xml' else [('', 'android:textColor'), ('', 'android:tint')],
                name)
            self.assertEqual(re.findall(r'android:id="([^"]+)"', stock), re.findall(r'android:id="([^"]+)"', patched))

    def test_the_chip_code_never_depends_on_its_background(self):
        """The pinned IconAppChipView clips to its own outline (app_chip_round_corner_radius) and animates that
        outline; it never reads or casts its background, so a different colour value is all that changes."""
        code = FIXTURES/'IconAppChipView.kt'
        self.assertEqual(hashlib.sha256(code.read_bytes()).hexdigest(),
            '31a644a01e48b9cdeb66332a7a1fff9c865012b8466696195adc918e31731fcd', 'Launcher3 47accce, byte for byte')
        source = code.read_text()
        for touch in (r'getBackground\(', r'\.background\b', r'\bbackground\s*=', 'setBackground', 'backgroundTint'):
            self.assertIsNone(re.search(touch, source), f'{touch}: the chip code never reads or sets its background')
        self.assertIn('R.dimen.app_chip_round_corner_radius', source, 'it clips to its own outline')
        tree = patched_tree(self)
        chip = (tree/'quickstep/res/layout/icon_app_chip_view.xml').read_text()
        self.assertIn('android:clipToOutline="true"', chip)
        self.assertIn('<com.android.quickstep.views.IconAppChipView', chip)
        self.assertNotIn('setTextColor', source, 'nor its name\'s colour, so the layout attribute holds')

class RoseEdgeGestureTest(unittest.TestCase):
    """#410: the bottom-edge swipe-up's release rules and live preview geometry (RoseEdgeGesture), against values
    copied from the frozen reference's app.js/oxblood.css (as RoseFanTest does) and Home's homeEdge keys. Pure source and JVM checks: not wired into Launcher3's
    swipe handler yet, so no navigation changes; touch, timing and the real relayout need the builder and a device."""
    EDGE = (OVERLAY/'RoseEdgeGesture.java').read_text()

    def test_the_release_rules_and_preview_match_the_reference_values(self):
        out = Path(tempfile.mkdtemp(prefix='rose-edge-'))
        self.addCleanup(shutil.rmtree, out, True)
        subprocess.run(['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '11', '-target', '11',
            '-Xlint:-options', '-d', str(out), str(OVERLAY/'RoseEdgeGesture.java'),
            str(ROOT/'os/tests/RoseEdgeGestureTest.java')], check=True)
        result = subprocess.run(['java', '-cp', str(out), 'com.android.quickstep.fallback.RoseEdgeGestureTest'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_edge_geometry_is_homes_home_edge(self):
        import json
        edge = json.loads((ROOT/'qa/oxblood/oxblood-contract.json').read_text())['homeEdge']
        self.assertIn(f'EDGE_HEIGHT_DP = {edge["height"]}, EDGE_FLAT_TOP_DP = {edge["flatTop"]}', self.EDGE)
        self.assertIn(f'CORNER_DP = {edge["concaveFilletRadius"]}', self.EDGE)

    def test_only_the_edge_swipe_calls_it_and_it_has_no_android_or_platform_reach(self):
        quickstep = ROOT/'os/overlays/packages/apps/Launcher3'
        callers = sorted(p.name for p in quickstep.rglob('*.java') if p.name != 'RoseEdgeGesture.java'
            and 'RoseEdgeGesture' in p.read_text())
        self.assertEqual(callers, ['RoseEdgeSwipe.java'], 'its only callers are E2a\'s, behind the unregistered edge patch')
        self.assertNotIn('RoseEdgeGesture', PATCH.read_text())
        self.assertNotIn('import ', self.EDGE, 'pure: no Android, IPC, permission or sensor types')
        for reach in ('SensorPrivacy', 'AudioManager', 'Binder', 'Intent', 'Settings', 'System.', 'Thread'):
            self.assertNotIn(reach, self.EDGE)

EDGE_PATCH = ROOT/'os/patches/rose-edge-launcher3.patch'
# Launcher3 47accce, byte for byte: the swipe handlers the edge patch changes, and the classes whose members it uses.
EDGE_PINNED = {'AbsSwipeUpHandler.java': '7bd5815f17eed4c5e59cfe323f8f61ad6d3c5bfbdedc2d95a83183984350e0df',
    'FallbackSwipeHandler.java': '194138f4ee572d6edef485087a08ae3377ad941b06bf8b5342d63b23e9805161',
    'SwipeUpAnimationLogic.java': '9874ec5f07f78c0c255c50713046e9c74722dbc4ed53e1a168ee3c1d4c72afb3',
    'GestureState.java': '3ceb83b87e02cd0505cd7a1aa727aa0d672e3c1fc2d586119346373a485c94cb',
    'TransformParams.java': '22b960e05c9e6c694cbe725329b6ac6234fdccfd396523283055197a6a5d7eb2',
    'SurfaceTransaction.java': 'f5ba9a7c03444b39a6702c944b267ad4c5abd5d88be65897fb9327fb41c7083c',
    'TaskViewSimulator.java': '61642176403139f131ddfab56a61472cd6e7ff008729a43637210940d4dbb6e6'}
EDGE_TARGETS = {'AbsSwipeUpHandler.java': f'{QUICKSTEP}/AbsSwipeUpHandler.java',
    'FallbackSwipeHandler.java': f'{QUICKSTEP}/FallbackSwipeHandler.java'}

class RoseEdgeSwipePatchTest(unittest.TestCase):
    """#410 E2a: rose-edge-launcher3.patch wires RoseEdgeGesture into Launcher3's fallback swipe handler, off unless
    persist.wm.debug.rose_edge. Source checks only: the patch applies byte for byte to the pinned handlers, keeps
    stock behaviour in the base class, and uses only members the pinned sources declare. The patched handlers are NOT
    compiled here (that needs a real Launcher3 module build on the builder), and nothing is run on a device."""
    EDGE_SWIPE = (OVERLAY/'RoseEdgeSwipe.java').read_text()
    EDGE_STATE = (OVERLAY/'RoseEdgeState.java').read_text()

    def patched(self):
        tree = Path(tempfile.mkdtemp(prefix='launcher3-edge-'))
        self.addCleanup(shutil.rmtree, tree, True)
        for name, relative in EDGE_TARGETS.items():
            (tree/relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURES/name, tree/relative)
        subprocess.run(['git', 'init', '-q', str(tree)], check=True)
        check = subprocess.run(['git', '-C', str(tree), 'apply', '--check', str(EDGE_PATCH)], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        subprocess.run(['git', '-C', str(tree), 'apply', str(EDGE_PATCH)], check=True)
        return {name: (tree/relative).read_text() for name, relative in EDGE_TARGETS.items()}

    def test_fixtures_are_the_pinned_files(self):
        for name, digest in EDGE_PINNED.items():
            self.assertEqual(hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest(), digest, name)

    def test_the_patch_applies_and_changes_only_two_stock_lines(self):
        text = EDGE_PATCH.read_text()
        self.assertEqual(re.findall(r'^\+\+\+ b/(\S+)', text, re.M), list(EDGE_TARGETS.values()))
        removed = [l for l in text.splitlines() if l.startswith('-') and not l.startswith('---')]
        self.assertEqual(removed, ['-        final GestureEndTarget endTarget = calculateEndTarget(',
            '-                velocityPxPerMs, endVelocityPxPerMs, isFling, isCancel, horizontalTouchSlopPassed);',
            '-                taskViewSimulator.apply(transformParams);'])
        self.assertNotIn('\t', ''.join(l for l in text.splitlines() if l.startswith('+')), 'Launcher3 indents with spaces')
        files = set(re.findall(r'^\+\+\+ b/(\S+)', PATCH.read_text(), re.M))
        self.assertFalse(files & set(EDGE_TARGETS.values()), 'disjoint from rose-switcher-launcher3.patch')
        self.patched()

    def test_the_base_class_keeps_stock_behaviour(self):
        abs_ = self.patched()['AbsSwipeUpHandler.java']
        self.assertIn('final GestureEndTarget endTarget = refineEndTarget(calculateEndTarget(\n'
            '                velocityPxPerMs, endVelocityPxPerMs, isFling, isCancel, horizontalTouchSlopPassed),\n'
            '                isCancel, mIsMotionPaused);', abs_)
        self.assertIn('GestureEndTarget endTarget, boolean isCancel, boolean isMotionPaused) {\n        return endTarget;\n    }', abs_)
        self.assertIn('TaskViewSimulator taskViewSimulator, TransformParams transformParams) {\n'
            '        taskViewSimulator.apply(transformParams);\n    }', abs_)
        self.assertIn('return mHasMotionEverBeenPaused || mIsLikelyToStartNewTask;', abs_)
        self.assertEqual(abs_.count('applyTargetTransform(taskViewSimulator, transformParams);'), 1)
        self.assertNotIn('Rose', abs_.replace('RoseOS (#410)', ''), 'no Rose logic in the shared base class')

    def test_the_fallback_handler_maps_only_its_own_outcomes(self):
        fallback = self.patched()['FallbackSwipeHandler.java']
        refine = fallback[fallback.index('protected GestureEndTarget refineEndTarget('):fallback.index('protected void applyTargetTransform(')]
        self.assertIn('if (!mRoseEdge.enabled() || mGestureState.isHandlingAtomicEvent()) {\n            return endTarget;',
            refine, 'switch off (the default): stock end target, nothing else evaluated')
        self.assertIn('endTarget == GestureEndTarget.HOME, mCurrentDisplacement)', refine)
        self.assertIn('case RoseEdgeGesture.RAISE:\n            case RoseEdgeGesture.STAY:\n'
            '                refined = GestureEndTarget.LAST_TASK;', refine)
        self.assertIn('case RoseEdgeGesture.HOME:\n                refined = GestureEndTarget.HOME;', refine)
        self.assertIn('default:\n                break;', refine, "STOCK keeps Launcher3's own target")
        self.assertIn('mRoseEdge.settled(refined == GestureEndTarget.LAST_TASK);', refine)
        transform = fallback[fallback.index('protected void applyTargetTransform('):fallback.index('private boolean isRoseGestureNavigation()')]
        self.assertTrue(transform.split('\n')[2:6] == ['        if (!mRoseEdge.enabled()) {',
            '            super.applyTargetTransform(taskViewSimulator, transformParams);', '            return;', '        }'],
            'switch off (the default): stock transform first, nothing else evaluated')
        self.assertEqual(transform.count('super.applyTargetTransform(taskViewSimulator, transformParams);'), 2)
        self.assertIn('hasAttachedToRecents() || endTarget == GestureEndTarget.RECENTS\n'
            '                || endTarget == GestureEndTarget.NEW_TASK', transform, 'the switcher and quick switch stay stock')
        self.assertIn('mRunningOverHome || mGestureState.isTrackpadGesture()', fallback, 'never over Home or for a trackpad')
        self.assertIn('mRemoteTargetHandles.length == 1', fallback, 'one app only: never split screen')

    def test_every_launcher3_member_it_uses_is_declared_at_the_pinned_revision(self):
        src = {name: (FIXTURES/name).read_text() for name in EDGE_PINNED}
        declared = [('SwipeUpAnimationLogic.java', r'protected float mCurrentDisplacement;'),
            ('SwipeUpAnimationLogic.java', r'protected int mTransitionDragLength;'),
            ('SwipeUpAnimationLogic.java', r'protected DeviceProfile mDp;'),
            ('SwipeUpAnimationLogic.java', r'protected RemoteTargetHandle\[\] mRemoteTargetHandles;'),
            ('SwipeUpAnimationLogic.java', r'protected final GestureState mGestureState;'),
            ('AbsSwipeUpHandler.java', r'protected float getCurrentShiftValue\(\)'),
            ('AbsSwipeUpHandler.java', r'private boolean mIsMotionPaused;'),
            ('AbsSwipeUpHandler.java', r'private boolean mHasMotionEverBeenPaused;'),
            ('AbsSwipeUpHandler.java', r'private boolean mIsLikelyToStartNewTask;'),
            ('AbsSwipeUpHandler.java', r'mDp\.getDeviceProperties\(\)\.getDeviceConfiguration\(\)\.isGestureMode\(\)'),
            ('GestureState.java', r'public boolean isHandlingAtomicEvent\(\)'),
            ('GestureState.java', r'public boolean isTrackpadGesture\(\)'),
            ('GestureState.java', r'public GestureEndTarget getEndTarget\(\)'),
            ('GestureState.java', r'\bHOME\(true,'), ('GestureState.java', r'\bRECENTS\(true,'),
            ('GestureState.java', r'\bNEW_TASK\(false,'), ('GestureState.java', r'\bLAST_TASK\(false,'),
            ('TaskViewSimulator.java', r'public RecentsOrientedState getOrientationState\(\)'),
            ('TaskViewSimulator.java', r'mOrientationState\.getDisplayRotation\(\)'),
            ('TaskViewSimulator.java', r'mDp\.getDeviceProperties\(\)\.getWidthPx\(\), mDp\.getDeviceProperties\(\)\.getHeightPx\(\)'),
            ('TransformParams.java', r'public SurfaceTransaction createSurfaceParams\(BuilderProxy proxy\)'),
            ('TransformParams.java', r'public void applySurfaceParams\(SurfaceTransaction builder\)'),
            ('TransformParams.java', r'void onBuildTargetParams\(SurfaceProperties builder,\s+RemoteAnimationTarget app, TransformParams params\);'),
            ('SurfaceTransaction.java', r'public SurfaceProperties setMatrix\(Matrix matrix\)'),
            ('SurfaceTransaction.java', r'public SurfaceProperties setWindowCrop\(Rect windowCrop\)'),
            ('SurfaceTransaction.java', r'public SurfaceProperties setCornerRadius\(float radius\)'),
            ('FallbackSwipeHandler.java', r'private final boolean mRunningOverHome;'),
            ('FallbackSwipeHandler.java', r'import android\.graphics\.Rect;'),
            ('FallbackSwipeHandler.java', r'import android\.view\.Surface;'),
            ('FallbackSwipeHandler.java', r'import com\.android\.quickstep\.util\.TransformParams\.BuilderProxy;'),
            ('FallbackSwipeHandler.java', r'int displayId\) \{')]
        for name, pattern in declared:
            self.assertRegex(src[name], pattern, f'{name}: {pattern}')
        self.assertEqual(src['GestureState.java'].count('class GestureState'), 1)
        self.assertIn('package com.android.quickstep;', src['AbsSwipeUpHandler.java'])

    def test_the_overlay_classes_compile_against_the_sdk(self):
        import os
        sdk = os.environ.get('ANDROID_HOME') or '/opt/roseos/android-sdk'
        jars = sorted(Path(sdk).glob('platforms/android-*/android.jar'))
        if not jars:
            self.skipTest('no Android SDK platform')
        tmp = Path(tempfile.mkdtemp(prefix='rose-edge-compile-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        stub = tmp/'src/android/os/SystemProperties.java'
        stub.parent.mkdir(parents=True)
        # frameworks/base 84183aff core/java/android/os/SystemProperties.java (hidden; Launcher3 builds with platform APIs).
        stub.write_text('package android.os; public class SystemProperties { '
            'public static boolean getBoolean(String key, boolean def) { return def; } }')
        result = subprocess.run(['javac', '-source', '11', '-target', '11', '-Xlint:all', '-Xlint:-options',
            '-implicit:none', '-sourcepath', str(tmp/'src'), '-cp', str(jars[-1]), '-d', str(tmp/'out'),
            str(OVERLAY/'RoseEdgeSwipe.java'), str(OVERLAY/'RoseEdgeState.java'), str(OVERLAY/'RoseEdgeGesture.java')],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        warnings = [l for l in result.stderr.splitlines() if l.strip() and not l.startswith('Picked up JAVA_TOOL_OPTIONS')]
        self.assertEqual(warnings, [], 'no warnings')

    def test_it_is_off_by_default_and_reaches_nothing_new(self):
        self.assertIn('public static final String PROPERTY = "persist.wm.debug.rose_edge";', self.EDGE_SWIPE)
        self.assertIn('SystemProperties.getBoolean(PROPERTY, false)', self.EDGE_SWIPE)
        self.assertIn('return enabled && !excluded && gestureNavigation && naturalRotation\n'
            '                && (keyguard == null || !keyguard.isKeyguardLocked());', self.EDGE_SWIPE)
        for source in (self.EDGE_SWIPE, self.EDGE_STATE):
            code = re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', source, flags=re.S))
            for reach in ('SensorPrivacy', 'AudioManager', 'setMicrophoneMute', 'Binder', 'Intent', 'startActivity',
                    'ContentResolver', 'Settings', 'SystemProperties.set', 'WindowManager', 'permission'):
                self.assertNotIn(reach, code, reach)
        self.assertIn('Looper.myLooper() != Looper.getMainLooper()', self.EDGE_STATE)

    def test_no_build_applies_it_until_release_and_infra_agree(self):
        patcher = (ROOT/'scripts/apply-os-changes.py').read_text()
        self.assertNotIn('rose-edge-launcher3.patch', patcher, 'registered only with an agreed compile route')

if __name__ == '__main__':
    unittest.main()
