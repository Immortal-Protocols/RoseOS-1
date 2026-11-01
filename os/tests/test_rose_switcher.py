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
QUICKSTEP = 'quickstep/src/com/android/quickstep'
DIMENS = 'quickstep/res/values/rose_agent_strip.xml'
# Launcher3 47accce (os/pinned-manifest.xml), byte for byte from GrapheneOS platform_packages_apps_Launcher3.
PINNED = {'fallback_recents_activity.xml': '8df1879fb83e2a9f5b11b657a498e6153504171cb702f859d136f37ab968a9f7',
    'OverviewComponentObserver.java': 'a94703d973e14ba8e1f43ab07d7eacaa0e32f8b5668d16bff1c6843dd4877893',
    'BaseContainerInterface.java': 'a416df4aaa95b3cc954cef5ada99bc3f1078ddbdb56ccb4365be861eda6cf829',
    'FallbackActivityInterface.java': 'dfc02ef28da28e5fc82744588f6be235ad41eb9bdd79b8a471cb40c324cd4faa',
    'OverviewActionsView.java': '7f7d82fd09d7ad48d0ca0b2ef481b42e9445fe1d37de80a3b2cb45f8b908980f'}
TARGETS = {'fallback_recents_activity.xml': LAYOUT, 'BaseContainerInterface.java': f'{QUICKSTEP}/BaseContainerInterface.java',
    'FallbackActivityInterface.java': f'{QUICKSTEP}/FallbackActivityInterface.java',
    'OverviewActionsView.java': f'{QUICKSTEP}/views/OverviewActionsView.java'}
# The stock lines the patch replaces, and nothing else: the fallback's claimed space below the cards, and the phone
# actions bar's task-aligned bottom margin (now less the same space).
REPLACED = ['-                    overviewActionsClaimedSpace,',
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
        self.assertIn('R.dimen.rose_agent_strip_height', override[:override.index('}')])
        self.assertIn('import com.android.launcher3.R;', fallback)
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
        self.assertIn('R.dimen.rose_agent_strip_height', reserved)
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
        self.assertGreaterEqual(int(dimens['rose_agent_strip_height']), 12 + 18 + 72 + 18)
        layout_actions = layout.index('android:id="@+id/overview_actions_view"')
        self.assertLess(layout.index('android:id="@id/overview_panel"'), layout.index('RoseAgentStrip'))
        self.assertLess(layout_actions, layout.index('RoseAgentStrip'), 'declared after the actions so it draws above them')

    def test_labels_are_looked_up_only_for_kept_work_and_a_failure_names_only_its_step(self):
        strip = (OVERLAY/'RoseAgentStrip.java').read_text()
        read = strip[strip.index('private static RoseAgentWork read('):strip.index('private void show(')]
        tasks = read[read.index('ActivityTaskManager.getInstance().getTasks(64)'):read.index('RoseAgentWork.of(')]
        self.assertNotIn('getApplicationInfoAsUser', tasks, 'no label lookup for every running task')
        self.assertNotIn('getApplicationLabel', tasks)
        self.assertIn('RoseAgentWork.of(controllerUid, user, displays, tasks).labelled(', read)
        self.assertLess(read.index('RoseAgentWork.of('), read.index('getApplicationInfoAsUser'))
        for step in ('CONTROLLER', 'DISPLAYS', 'TASKS'):
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
            'com/android/launcher3/R.java': 'package com.android.launcher3; public final class R { '
                'public static final class dimen { public static final int rose_agent_strip_height = 1; } '
                'public static final class id { public static final int action_buttons = 2; } }',
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
        self.assertEqual(self.STRIP.count('new HorizontalScrollView(context)'), 1)
        self.assertIn('scroller.addView(cards);', self.STRIP)
        for coupled in ('RecentsView', 'PagedView', 'overview_panel', 'setOnScrollChangeListener', 'scrollBy('):
            self.assertNotIn(coupled, self.STRIP, f'{coupled}: the agent row never drives the human carousel')
        tree = patched_tree(self)
        layout = (tree/LAYOUT).read_text()
        self.assertLess(layout.index('android:id="@id/overview_panel"'), layout.index('RoseAgentStrip'),
            'a sibling of the human carousel in the drag layer, not inside it')

    def test_every_entry_starts_fresh_and_leaving_stops_the_connector(self):
        visibility = self.body('protected void onWindowVisibilityChanged(int visibility)', 'private void setWorking(')
        self.assertLess(visibility.index('scroller.scrollTo(0, 0);'), visibility.index('refresh();'))
        self.assertIn('setWorking(false);', visibility.split('} else {')[1])
        refresh = self.body('void refresh() {', 'private static RoseAgentWork read(')
        self.assertIn('int token = ++generation;', refresh)
        self.assertIn('if (token == generation) show(next);', refresh, 'the newest read wins on re-entry')

    def test_the_connector_reflects_only_actual_work(self):
        show = self.body('private void show(RoseAgentWork next) {', 'private void select(')
        self.assertIn('link.setText(next.linkLabel());', show)
        self.assertIn('setWorking(next.working() > 0 && getWindowVisibility() == VISIBLE);', show)
        working = self.body('private void setWorking(boolean working) {', 'void refresh() {')
        self.assertIn('pulse.setRepeatCount(ValueAnimator.INFINITE);', working)
        self.assertIn('if (pulse != null) pulse.cancel();', working.split('} else {')[1])
        self.assertIn('connector.setAlpha(IDLE);', working)
        self.assertIn('task.topOfDisplay ? 0x55FFFFFF : 0x33FFFFFF', show, 'the current card is marked')
        self.assertIn('Rose is working in it now', show)

    def test_empty_and_error_states_invent_nothing(self):
        show = self.body('private void show(RoseAgentWork next) {', 'private void select(')
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
        # padding 12 + link row ~18 + card 72 + status ~18 dp, inside the claimed rose_agent_strip_height.
        import re
        tree = patched_tree(self)
        height = int(re.search(r'name="rose_agent_strip_height">(\d+)dp', (tree/DIMENS).read_text())[1])
        self.assertGreaterEqual(height, 12 + 18 + 72 + 18)
        self.assertNotIn('title = text(', self.STRIP, 'the HiFi link row replaces the old static title')

if __name__ == '__main__':
    unittest.main()
