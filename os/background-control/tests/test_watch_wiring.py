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
        self.assertRegex(create, r'synchronized \(Controller\.class\) \{[^}]*\}\s*if \(!binding\.allows\(Controller\.id, Controller\.taskId\)\) \{ unavailable\(\); return; \}')

    def test_every_attach_is_gated_and_rechecked(self):
        refresh = method('private final Runnable refresh')
        self.assertEqual(refresh.count('setSession.invoke(mirror, current)'), 1)
        attach = refresh.index('setSession.invoke(mirror, current)')
        # Read under the controller lock together with the session object.
        self.assertRegex(refresh, r'synchronized \(Controller\.class\) \{\s*current = Controller\.session;\s*allowed = binding\.allows\(Controller\.id, Controller\.taskId\);')
        self.assertLess(refresh.index('if (!allowed) { unavailable(); return; }'), attach)
        # A changed binding re-attaches even when the session object is the same.
        self.assertIn('if (current != attached || !binding.equals(attachedBinding))', refresh)
        # Re-checked after attaching, so a retag during the attach call is not shown.
        self.assertGreater(refresh.index('allowed = Controller.session == current && binding.allows(Controller.id, Controller.taskId);'), attach)

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
        self.assertNotRegex(SOURCE, r'Controller\.(stop|agentStop|command|start)\(|StopReceiver|StopLedger')
        # The viewer only reads controller state.
        self.assertNotRegex(SOURCE, r'Controller\.\w+\s*=[^=]')

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
        line = [l for l in controller.splitlines() if 'WatchBinding resolveSelection(' in l][0]
        self.assertIn('static synchronized WatchBinding resolveSelection(int display,String pkg)', line)
        self.assertIn('getMethod("getAccessibilityWindows").invoke(session)', line)
        self.assertIn('w.getDisplayId()', line)
        self.assertIn('return WatchBinding.fromSelection(display,pkg,session!=null,status,own<0?-1:own,target,id,taskId);', line)
        self.assertNotIn('AgentProvider', line)

if __name__ == '__main__':unittest.main()
