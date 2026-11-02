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
        self.assertGreaterEqual(int(dimens['rose_agent_strip_height']), 12 + 18 + 96 + 18)
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
        self.assertIn('if (token == generation) show(next, frames, locked);\n                else release(frames);', refresh,
            'the newest read wins on re-entry; an older one\'s frames are released, never shown')

    def test_the_connector_reflects_only_actual_work(self):
        show = self.body('private void show(RoseAgentWork next, Map<Integer, Bitmap> frames, boolean locked) {', 'private void select(')
        self.assertIn('link.setText(next.linkLabel());', show)
        self.assertIn('setWorking(next.working() > 0 && getWindowVisibility() == VISIBLE);', show)
        working = self.body('private void setWorking(boolean working) {', 'void refresh() {')
        self.assertIn('pulse.setRepeatCount(ValueAnimator.INFINITE);', working)
        self.assertIn('if (pulse != null) pulse.cancel();', working.split('} else {')[1])
        self.assertIn('connector.setAlpha(IDLE);', working)
        self.assertIn('task.topOfDisplay ? 0x55FFFFFF : 0x33FFFFFF', show, 'the current card is marked')
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
        # padding 12 + link row ~18 + card 96 (live view 64 + label) + status ~18 dp, inside the claimed height.
        import re
        tree = patched_tree(self)
        height = int(re.search(r'name="rose_agent_strip_height">(\d+)dp', (tree/DIMENS).read_text())[1])
        self.assertGreaterEqual(height, 12 + 18 + 96 + 18)
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
        self.assertIn('if (why == null) {\n                ImageView live = new ImageView(getContext());', show)
        visibility = code[code.index('protected void onWindowVisibilityChanged(int visibility) {'):code.index('private void setWorking(')]
        hidden = visibility.split('} else {')[1]
        for step in ('removeCallbacks(previewTick);', 'generation++;', 'clearCards();'):
            self.assertIn(step, hidden, f'leaving the overview: {step}')
        detached = code[code.index('protected void onDetachedFromWindow() {'):code.index('private void anchor()')]
        self.assertIn('clearCards();', detached)
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
        self.assertEqual(height, 160, 'the claimed space grows only by the preview body')

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

if __name__ == '__main__':
    unittest.main()
