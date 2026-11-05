"""F06 (OS Core platform side, agreed with App Core): Rose takes over an app the owner already has open.

Applies the background-control patch sequence (scripts/background_apks.py BACKGROUND_FRAMEWORK_PATCHES) to the pinned
ComputerControl sources (GrapheneOS platform_frameworks_base 84183aff) and checks ComputerControlSessionImpl's launch and
close paths for the four states of the target app:

- closed: a new task on the session's display, as before;
- open in the owner's workspace but not visible: Android reuses that task and reparents it onto the session's display
  (stock ActivityStarter.setTargetRootTaskIfNeeded: intentTask.reparent(..., "reparentToTargetRootTask")), the same task
  with its state;
- the absolute foreground: formerly refused by our own patch ("target_in_foreground"); now taken over exactly like the
  background case, under the same allowlist, target, user and locked-owner gates (F06 forbids a blanket refusal);
- already in a workspace: in this session it is brought to front; shown in another live workspace it is refused with
  "target_in_other_session" (the race backstop behind the controller's own app_in_other_session check);
- a home, recents, assistant or dream app: refused with "target_not_standard". The launch uses the launcher intent,
  whose start has activity type UNDEFINED and so is compatible with any task type (stock
  ConfigurationContainer.isCompatibleActivityType); Android's reuse could otherwise reparent the HOME holder's own
  task (Rose Agent's singleTask MainActivity is both HOME and LAUNCHER) off the main display.

The session's display is private (no VIRTUAL_DISPLAY_FLAG_PUBLIC), and Android destroys a private display's tasks when
it is removed (stock DisplayWindowSettings.getRemoveContentModeLocked). So a task the owner had open, once taken over,
would have been destroyed by any close without hand-over (owner Stop, deadline, caller death). The session records every
task the app owned on the main display before the launch (Android chooses which one it reuses), and before its display
closes moves each one that is on its display back to the main display. A task goes back on top only if it was in front
(by task, not by package) and the owner is still on the task its move revealed; otherwise it goes in at the bottom, so
it never covers what the owner opened since. The platform cannot tell the owner's Stop from an idle or deadline close.
Hand-over already moves every task back on top (stock moveAllTasks: reparentTasks(..., onTop=true)).

The Android behaviour cited above is source-traced at 84183aff, not executed. Source and fixture logic only: no
framework build, emulator or device, and not phone proof."""
from pathlib import Path
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from background_apks import BACKGROUND_FRAMEWORK_PATCHES  # noqa: E402

FIXTURES = ROOT/'os/tests/fixtures/background-control'
SOURCE = 'services/companion/java/com/android/server/companion/virtual/computercontrol'
SESSION = 'ComputerControlSessionImpl.java'
PINNED = '66447d84e061d2a0304f51f9e06fa45032db3f2a1e5ac035689cb4522fd0f735'


def method(source, signature):
    """The body of the first method whose declaration contains signature, by brace matching."""
    start = source.index(signature)
    open_brace = source.index('{', start)
    depth = 0
    for i in range(open_brace, len(source)):
        depth += {'{': 1, '}': -1}.get(source[i], 0)
        if depth == 0:
            return source[start:i + 1]
    raise ValueError(signature)


def flat(text):
    return re.sub(r'\s+', ' ', text)


class AnyAppTakeoverPlatformTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = Path(tempfile.mkdtemp(prefix='takeover-'))
        (cls.tree/SOURCE).mkdir(parents=True)
        shutil.copyfile(FIXTURES/SESSION, cls.tree/SOURCE/SESSION)
        subprocess.run(['git', 'init', '-q', str(cls.tree)], check=True)
        cls.applied = []
        for patch in BACKGROUND_FRAMEWORK_PATCHES:
            result = subprocess.run(['git', '-C', str(cls.tree), 'apply', f'--include={SOURCE}/{SESSION}',
                str(ROOT/'os/patches'/patch)], capture_output=True, text=True)
            cls.applied.append((patch, result.returncode, result.stderr))
        cls.pinned = (FIXTURES/SESSION).read_text()
        cls.session = (cls.tree/SOURCE/SESSION).read_text()
        cls.launch = method(cls.session, 'public void launchApplication(')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tree, True)

    def test_the_pinned_source_and_the_patch_sequence(self):
        self.assertEqual(hashlib.sha256((FIXTURES/SESSION).read_bytes()).hexdigest(), PINNED)
        for patch, code, stderr in self.applied:
            self.assertEqual(code, 0, f'{patch}: {stderr}')

    def test_the_foreground_app_is_never_refused(self):
        # The former blanket refusal is gone from the session and from every patch that builds it.
        self.assertNotIn('target_in_foreground', self.session)
        for patch in BACKGROUND_FRAMEWORK_PATCHES:
            added = [l for l in (ROOT/'os/patches'/patch).read_text().splitlines() if l.startswith('+')]
            self.assertFalse([l for l in added if 'target_in_foreground' in l], patch)
        # Being in front only shapes the task's return; it is never a reason to throw. In front is by task: the main
        # display's top visible task is the app's own, whoever's activity (a payment screen, say) is on top of it.
        self.assertIn('final int topTask = foreground.isEmpty() || foreground.get(0) == null ? INVALID_TASK_ID : '
            'foreground.get(0).getTaskId();', flat(self.launch))
        self.assertNotIn('if (topTask', self.launch)
        # An unknown foreground still refuses rather than guessing.
        self.assertIn('if (foreground == null) { throw new IllegalStateException("foreground_state_unavailable"); }',
            flat(self.launch))

    def test_every_launch_gate_still_runs_first(self):
        start = self.launch.index('Binder.withCleanCallingIdentity(() -> {')
        for gate in ('mAllowlistController.isPackageAutomatable(packageName, this)',
                '!mAllowlistedPackages.contains(packageName)', 'isOwnerGatedWhileLocked(packageName)',
                'if (!isSessionActive()) {'):
            self.assertLess(self.launch.index(gate), start, gate)
        # The launch itself is unchanged: Android's own task reuse moves an open app, keeping its task.
        self.assertIn('ActivityOptions.makeBasic().setLaunchDisplayId(mVirtualDisplayId).toBundle();', self.launch)
        self.assertEqual(self.launch.count('mActivityTaskManagerInternal.startActivityAsUser('), 1)
        self.assertIn('Intent.FLAG_ACTIVITY_NEW_TASK, options, mRequest.ownerUserId());', self.launch)

    def test_an_app_shown_in_another_workspace_stays_there(self):
        loop = flat(self.launch[self.launch.index('for (int displayId : mDisplayManagerGlobal.getDisplayIds())'):])
        self.assertIn('if (displayId != mMainDisplayId && displayId != mVirtualDisplayId && showsPackage( '
            'mActivityTaskManagerInternal.getTopVisibleActivities(displayId), packageName)) { '
            'throw new IllegalStateException("target_in_other_session: " + packageName); }', loop)
        # Checked before anything moves.
        self.assertLess(self.launch.index('target_in_other_session'), self.launch.index('startActivityAsUser('))
        shows = flat(method(self.session, 'private static boolean showsPackage('))
        self.assertIn('if (activities == null) { return false; }', shows)
        self.assertIn('packageName.equals(activity.getComponentName().getPackageName())', shows)

    def test_a_home_recents_assistant_or_dream_task_never_moves(self):
        guard = flat(self.launch[self.launch.index('final List<ActivityManager.RunningTaskInfo> before = '):])
        self.assertIn('final List<ActivityManager.RunningTaskInfo> before = tasksOn(mMainDisplayId); '
            'for (ActivityManager.RunningTaskInfo task : before) { if (ownsTask(task, '
            'packageName) && task.getActivityType() != ACTIVITY_TYPE_STANDARD && task.getActivityType() != '
            'ACTIVITY_TYPE_UNDEFINED) { throw new IllegalStateException("target_not_standard: " + packageName); } }',
            guard)
        # Any such task of the package refuses, before its tasks are recorded or anything starts.
        self.assertLess(self.launch.index('target_not_standard'), self.launch.index('owned.add(task.taskId);'))
        self.assertLess(self.launch.index('target_not_standard'), self.launch.index('startActivityAsUser('))
        owns = flat(method(self.session, 'private boolean ownsTask('))
        self.assertIn('return task.baseActivity != null && packageName.equals(task.baseActivity.getPackageName()) && '
            'task.userId == mRequest.ownerUserId();', owns)

    def test_every_open_task_of_the_app_is_recorded_with_its_place(self):
        launch = flat(self.launch)
        # All of the app's main-display tasks before the launch, and the first other task (what a move reveals).
        self.assertIn('for (ActivityManager.RunningTaskInfo task : before) { if (ownsTask(task, packageName)) { '
            'owned.add(task.taskId); } else if (below == INVALID_TASK_ID) { below = task.taskId; } }', launch)
        order = [self.launch.index(s) for s in ('owned.add(task.taskId);', 'startActivityAsUser(',
            'mTakenOverTasks.put(taskId, new Takeover(taskId == topTask, below));')]
        self.assertEqual(order, sorted(order))
        self.assertIn('synchronized (mTakenOverTasks) { for (int taskId : owned) { '
            'mTakenOverTasks.put(taskId, new Takeover(taskId == topTask, below)); } }', launch)
        self.assertIn('final boolean sameSession = owned.isEmpty() && taskOf(packageName, mVirtualDisplayId) != '
            'INVALID_TASK_ID;', launch)
        # Only the owner's own task of that app (by its root activity) on the main display.
        task = flat(method(self.session, 'private int taskOf('))
        self.assertIn('for (ActivityManager.RunningTaskInfo task : tasksOn(displayId))', task)
        self.assertIn('if (ownsTask(task, packageName)) { return task.taskId; }', task)
        tasks = flat(method(self.session, 'private static List<ActivityManager.RunningTaskInfo> tasksOn('))
        self.assertIn('ActivityTaskManager.getService().getTasks( Integer.MAX_VALUE, '
            '/* filterOnlyVisibleRecents= */ false, /* keepIntentExtra= */ false, displayId);', tasks)
        self.assertIn('@GuardedBy("mTakenOverTasks") private final SparseArray<Takeover> mTakenOverTasks',
            flat(self.session))
        self.assertIn('Takeover(boolean foreground, int below) { this.foreground = foreground; this.below = below; }',
            flat(self.session))

    def test_the_disposition_log_is_content_free(self):
        log = flat(self.launch[self.launch.index('Slog.i(TAG, "launch disposition="'):])
        self.assertIn('Slog.i(TAG, "launch disposition=" + (!owned.isEmpty() ? (owned.contains(topTask) ? '
            '"foreground" : "background") : sameSession ? "same_session" : "closed") + " tasks=" + owned + " from=" + '
            'mMainDisplayId + " to=" + mVirtualDisplayId);', log)
        self.assertNotIn('packageName', log[:log.index(');') + 2])

    def test_taken_over_tasks_return_before_the_private_display_closes(self):
        # Why: the session display has no VIRTUAL_DISPLAY_FLAG_PUBLIC, so Android destroys its content on removal.
        config = flat(method(self.pinned, 'private static VirtualDisplayConfig createSessionDisplayConfig('))
        self.assertIn('final int displayFlags = DisplayManager.VIRTUAL_DISPLAY_FLAG_TRUSTED | '
            'DisplayManager.VIRTUAL_DISPLAY_FLAG_ALWAYS_UNLOCKED;', config)
        self.assertNotIn('VIRTUAL_DISPLAY_FLAG_PUBLIC', self.session)
        release = method(self.session, 'private void releaseResources()')
        self.assertLess(release.index('returnTakenOverTasks();'), release.index('mVirtualDevice.close();'))
        back = flat(method(self.session, 'private void returnTakenOverTasks()'))
        self.assertIn('taken = mTakenOverTasks.clone(); mTakenOverTasks.clear();', back)
        # Only a task on this display moves, to the main display: on top only if it was in front and the owner is
        # still on the task its move revealed; otherwise at the bottom, never over what the owner opened since.
        self.assertIn('final List<ActivityManager.RunningTaskInfo> here = tasksOn(mVirtualDisplayId);', back)
        self.assertIn('if (here.stream().noneMatch(task -> task.taskId == taskId)) { continue; }', back)
        self.assertIn('final int topNow = front == null || front.isEmpty() || front.get(0) == null ? INVALID_TASK_ID : '
            'front.get(0).getTaskId();', back)
        self.assertIn('final boolean onTop = takeover.foreground && takeover.below != INVALID_TASK_ID && '
            'topNow == takeover.below;', back)
        self.assertIn('ActivityTaskManager.getService().moveRootTaskToDisplayOnTopOrBottom(taskId, mMainDisplayId, '
            'onTop);', back)
        # A failure to return one task never blocks the rest of the close.
        self.assertIn('catch (RemoteException | RuntimeException e) { Slog.w(TAG, "Could not return task=" + '
            'taskId', back)

    def test_hand_over_still_moves_every_task_back_on_top(self):
        hand = flat(method(self.session, 'public void handOverApplications()'))
        self.assertEqual(hand, flat(method(self.pinned, 'public void handOverApplications()')))
        self.assertLess(hand.index('moveAllTasks(mVirtualDisplayId, mMainDisplayId)'),
            hand.index('close(CLOSE_REASON_SESSION_EMPTY)'))


if __name__ == '__main__':
    unittest.main()
