"""Source checks that the PiP viewer enforces the Watch task/session binding at every attach; no device needed."""
from pathlib import Path
import re
import unittest

SOURCE = (Path(__file__).resolve().parents[1]/'agent-pip/src/dev/pixel/backgroundcontrol/AgentPipActivity.java').read_text()
PRESENTATION = (Path(__file__).resolve().parents[1]/'agent-pip/src/dev/pixel/backgroundcontrol/ViewerPresentation.java').read_text()
AUTO = (Path(__file__).resolve().parents[3]/'workspace/rose-agent/app/src/dev/pixel/voice/AutoAgentPip.java').read_text()

def method(signature):
    start = SOURCE.index(signature)
    depth, i = 0, SOURCE.index('{', start)
    while True:
        depth += {'{': 1, '}': -1}.get(SOURCE[i], 0)
        if depth == 0:return SOURCE[start:i + 1]
        i += 1

class WatchWiringTest(unittest.TestCase):
    def test_entry_binds_before_anything_is_shown(self):
        create = method('public void onCreate(Bundle saved)')
        self.assertLess(create.index('restore(saved) : fromIntent(getIntent())'), create.index('new FrameLayout(this)'))
        self.assertIn('if (binding.mode == WatchBinding.Mode.INVALID) { unavailable(); return; }', create)
        # Replacement before attach: the selected pair must be the controller's current one.
        # ... of the viewer's own slot (Controller.viewed): never another session, never a guess between two.
        self.assertIn('Controller.Slot viewed = Controller.viewed(binding);\n            if (viewed == null) { unavailable(); return; }', create)
        lock = create.index('synchronized (Controller.class) {')
        self.assertLess(lock, create.index('if (viewed.session == null) {'))
        self.assertLess(create.index('if (viewed.session == null) {'), create.index('if (!binding.allows(viewed.id, viewed.taskId)) { unavailable(); return; }'))
        self.assertLess(create.index('if (!binding.allows(viewed.id, viewed.taskId)) { unavailable(); return; }'), create.index('new FrameLayout(this)'))

    def test_every_attach_is_gated_and_rechecked(self):
        refresh = method('private final Runnable refresh')
        self.assertEqual(refresh.count('setSession.invoke(mirror, current)'), 1)
        attach = refresh.index('setSession.invoke(mirror, current)')
        # Read under the controller lock together with the session object.
        self.assertRegex(refresh, r'synchronized \(Controller\.class\) \{\s*Controller\.Slot viewed = Controller\.viewed\(binding\);\s*ambiguous = viewed == null;\s*current = viewed == null \? null : viewed\.session;\s*allowed = viewed != null && binding\.allows\(viewed\.id, viewed\.taskId\);')
        self.assertLess(refresh.index('if (ambiguous) { unavailable(); return; }'), attach)
        self.assertLess(refresh.index('if (!allowed) { unavailable(); return; }'), attach)
        # A changed binding re-attaches even when the session object is the same.
        self.assertIn('if (current != attached || !binding.equals(attachedBinding))', refresh)
        # Re-checked after attaching, so a retag during the attach call is not shown.
        self.assertGreater(refresh.index('allowed = viewed != null && viewed.session == current && binding.allows(viewed.id, viewed.taskId);'), attach)

    def test_new_intents_and_recreation(self):
        intent = method('protected void onNewIntent(Intent intent)')
        self.assertIn('WatchBinding incoming = fromIntent(intent);', intent)
        self.assertIn('binding = WatchBinding.next(binding, incoming);', intent)
        self.assertIn('if (binding.mode == WatchBinding.Mode.INVALID) { unavailable(); return; }', intent)
        self.assertIn('handler.post(refresh)', intent)
        saved = method('protected void onSaveInstanceState(Bundle out)')
        for key in ('KEY_MODE', 'KEY_TASK', 'KEY_SESSION'):self.assertIn(key, saved)
        extras = method('static WatchBinding fromIntent(Intent intent)')
        self.assertIn('intent.hasExtra(WatchBinding.EXTRA_TASK), intent.getStringExtra(WatchBinding.EXTRA_TASK)', extras)
        self.assertIn('intent.hasExtra(WatchBinding.EXTRA_SESSION), intent.getStringExtra(WatchBinding.EXTRA_SESSION)', extras)

    def test_explicit_watch_is_full_screen_and_automatic_pip_is_off(self):
        # APP-WATCH-1031-C: a bound entry is expanded; AUTO-PIP-OFF-330: so is every other entry, and only PiP asks for PiP.
        create = method('public void onCreate(Bundle saved)')
        self.assertIn('ViewerPresentation.forEntry(binding.mode == WatchBinding.Mode.BOUND, null)', create)
        self.assertIn('if (ViewerPresentation.entersPip(presentation)) frame.post(() -> enterFloatingView());', create)
        self.assertLess(create.index('if (binding.mode == WatchBinding.Mode.INVALID) { unavailable(); return; }'),
            create.index('presentation = '), 'an invalid entry closes before any presentation is chosen')
        intent = method('protected void onNewIntent(Intent intent)')
        self.assertIn('presentation = ViewerPresentation.forEntry(incoming.mode == WatchBinding.Mode.BOUND, presentation);', intent)
        self.assertIn('if (frame != null && ViewerPresentation.entersPip(presentation)) frame.post(() -> enterFloatingView());', intent)
        self.assertNotIn('if (frame != null) frame.post(() -> enterFloatingView());', intent, 'no unconditional return to PiP')
        # The presentation survives recreation with the binding; an earlier build's saved PiP is restored full screen.
        self.assertIn('out.putString(KEY_PRESENTATION, presentation.name());', method('protected void onSaveInstanceState(Bundle out)'))
        self.assertIn('ViewerPresentation.restore(saved.getString(KEY_PRESENTATION))', create)
        # Leaving a full-screen viewer never auto-enters the floating window, and only a floating presentation needs the
        # picture-in-picture feature.
        params = method('private PictureInPictureParams parameters()')
        self.assertIn('.setAutoEnterEnabled(ViewerPresentation.AUTOMATIC_PIP)', params)
        self.assertNotIn('.setAutoEnterEnabled(true)', SOURCE)
        self.assertIn('if (ViewerPresentation.entersPip(presentation)\n                && !getPackageManager().hasSystemFeature('
            'PackageManager.FEATURE_PICTURE_IN_PICTURE)) {', create)
        self.assertIn('static final boolean AUTOMATIC_PIP = false;', PRESENTATION)
        # Privacy and lock guards are unchanged: the view is secure and never shown over the keyguard.
        self.assertIn('getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);', create)
        self.assertNotRegex(SOURCE, r'setShowWhenLocked|FLAG_SHOW_WHEN_LOCKED|setTurnScreenOn|requestDismissKeyguard')

    def test_rose_agent_never_launches_the_viewer_into_pip_on_its_own(self):
        # AUTO-PIP-OFF-330: AutoAgentPip launched the viewer into picture-in-picture whenever a session became ready. It
        # is off and in memory only (fresh install and upgrade alike); the session and its controls are untouched.
        observe = AUTO[AUTO.index('static synchronized void observe('):]
        self.assertIn('static final boolean ENABLED = false;', AUTO)
        self.assertLess(observe.index('if (!ENABLED) return;'), observe.index('makeLaunchIntoPip'))
        self.assertIn('.put("autoShow", ENABLED)', AUTO)
        self.assertNotRegex(AUTO, r'SharedPreferences|getSharedPreferences|Controller|agentStop|"stop"')

    def test_closing_the_viewer_never_stops_work(self):
        close = method('private void unavailable()')
        self.assertIn('finishAndRemoveTask();', close)
        self.assertNotRegex(SOURCE, r'Controller\.(stop|agentStop|command|start|ownerStop|stopAll)\(|StopReceiver|StopLedger')
        # The viewer never writes controller state directly.
        self.assertNotRegex(SOURCE, r'Controller\.\w+\s*=[^=]')
        # #410 (Nathan-approved, amended deliberately): closing, Back, Home, picture-in-picture, errors and every lifecycle
        # path still stop nothing; none of them reaches the viewer's Stop.
        for signature in ('private void unavailable()', 'public void onCreate(Bundle saved)', 'protected void onNewIntent(Intent intent)',
                          'private final Runnable refresh', 'private void detach()', 'private void fail(String message, Throwable error)',
                          'protected void onStart()', 'protected void onStop()', 'protected void onDestroy()',
                          'public void onConfigurationChanged(Configuration config)',
                          'public void onPictureInPictureModeChanged(boolean inPip, Configuration config)', 'private void updateStop()',
                          'private void confirmStop()', 'private void dismissConfirm()'):
            self.assertNotIn('viewerStop', method(signature), signature)
            if signature != 'private void confirmStop()':
                self.assertNotIn('stopConfirmed(', method(signature), signature)
        self.assertNotRegex(SOURCE, r'onBackPressed|OnBackInvokedCallback|onUserLeaveHint')

    def test_only_the_confirmed_stop_reaches_the_bound_pair(self):
        # The one exception (#410): the confirmation's positive button, once, for the session object seen when it was asked.
        self.assertEqual(SOURCE.count('Controller.viewerStop('), 1)
        self.assertEqual(SOURCE.count('stopConfirmed('), 2, 'its definition and the positive button only')
        confirm = method('private void confirmStop()')
        self.assertIn('if (stopping || attached == null || (confirm != null && confirm.isShowing())) return;', confirm)
        self.assertIn('Object seen = attached;', confirm)
        self.assertIn('WatchBinding asked = binding;', confirm)
        self.assertIn('.setNegativeButton(ViewerStop.KEEP, null)', confirm)
        self.assertIn('.setPositiveButton(ViewerStop.BUTTON, (dialog, which) -> stopConfirmed(seen, asked))', confirm)
        self.assertIn('confirm.getButton(AlertDialog.BUTTON_POSITIVE).setFilterTouchesWhenObscured(true);', confirm)
        self.assertIn('confirm.getButton(AlertDialog.BUTTON_NEGATIVE).setFilterTouchesWhenObscured(true);', confirm)
        stop = method('private void stopConfirmed(Object seen, WatchBinding asked)')
        self.assertLess(stop.index('if (stopping || isFinishing()) return;'), stop.index('stopping = true;'))
        self.assertLess(stop.index('stopping = true;'), stop.index('Controller.viewerStop('))
        # The pair it asked about, never the binding at tap time; a viewer re-bound or re-attached since passes no session
        # object, so the controller refuses (replaced). A rebinding intent also dismisses the question.
        self.assertIn('Controller.viewerStop(this, asked, seen == attached && asked.equals(binding) ? seen : null)', stop)
        self.assertIn('if (!WatchBinding.next(binding, incoming).equals(binding)) dismissConfirm();', method('protected void onNewIntent(Intent intent)'))
        self.assertIn('ViewerStop.Outcome outcome = ViewerStop.of(status, confirmed);', stop)
        self.assertIn('if (outcome.closeViewer) { detach(); finishAndRemoveTask(); return; }', stop)

    def test_the_stop_button_is_only_for_a_bound_full_screen_pair(self):
        create = method('public void onCreate(Bundle saved)')
        self.assertIn('if (binding.mode == WatchBinding.Mode.BOUND) addStopButton();', create)
        self.assertEqual(SOURCE.count('addStopButton();'), 1)
        add = method('private void addStopButton()')
        self.assertIn('stopButton.setFilterTouchesWhenObscured(true);', add)
        self.assertIn('stopButton.setVisibility(View.GONE);', add)
        self.assertIn('stopButton.setOnClickListener(v -> confirmStop());', add)
        update = method('private void updateStop()')
        self.assertIn('boolean show = attached != null && !stopping && !isInPictureInPictureMode() && !isFinishing();', update)
        # The question never outlives the view it asked about.
        for signature in ('private void unavailable()', 'protected void onStop()', 'protected void onDestroy()'):
            self.assertIn('dismissConfirm();', method(signature), signature)
        self.assertIn('if (inPip) dismissConfirm();', method('public void onPictureInPictureModeChanged(boolean inPip, Configuration config)'))
        # Unchanged secure viewer: FLAG_SECURE stays and it never shows over the lock screen (checked above).
        self.assertIn('getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);', create)

class SelectionWiringTest(unittest.TestCase):
    """#156 M004: an overview selection is resolved by the controller against its own live session."""
    def test_entries_go_through_the_selection_resolver(self):
        entry = method('static WatchBinding fromIntent(Intent intent)')
        self.assertIn('return WatchBinding.fromEntry(', entry)
        self.assertIn('Controller::resolveSelection);', entry)
        self.assertIn('extras.get(WatchBinding.EXTRA_SELECT_DISPLAY)', entry)
        self.assertIn('extras.get(WatchBinding.EXTRA_SELECT_PACKAGE)', entry)

    def test_the_controller_resolves_from_its_own_session_under_its_lock(self):
        controller = (Path(__file__).resolve().parents[1]/'agent-pip/src/dev/pixel/backgroundcontrol/Controller.java').read_text()
        # Every slot is tried, each through its own live session only (resolveSelectionOne).
        self.assertIn('static synchronized WatchBinding resolveSelection(int display,String pkg){', controller)
        line = [l for l in controller.splitlines() if 'WatchBinding resolveSelectionOne(' in l][0]
        self.assertIn('static synchronized WatchBinding resolveSelectionOne(int display,String pkg)', line)
        self.assertIn('getMethod("getAccessibilityWindows").invoke(session)', line)
        self.assertIn('w.getDisplayId()', line)
        self.assertIn('return WatchBinding.fromSelection(display,pkg,session!=null,status,own<0?-1:own,target,id,taskId);', line)
        self.assertNotIn('AgentProvider', line)

if __name__ == '__main__':unittest.main()
