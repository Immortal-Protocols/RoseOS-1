"""Source checks that the PiP viewer enforces the Watch task/session binding at every attach; no device needed."""
from pathlib import Path
import re
import unittest

SOURCE = (Path(__file__).resolve().parents[1]/'agent-pip/src/dev/pixel/backgroundcontrol/AgentPipActivity.java').read_text()

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
        self.assertIn('binding = WatchBinding.next(binding, fromIntent(intent));', intent)
        self.assertIn('if (binding.mode == WatchBinding.Mode.INVALID) { unavailable(); return; }', intent)
        self.assertIn('handler.post(refresh)', intent)
        saved = method('protected void onSaveInstanceState(Bundle out)')
        for key in ('KEY_MODE', 'KEY_TASK', 'KEY_SESSION'):self.assertIn(key, saved)
        extras = method('static WatchBinding fromIntent(Intent intent)')
        self.assertIn('intent.hasExtra(WatchBinding.EXTRA_TASK), intent.getStringExtra(WatchBinding.EXTRA_TASK)', extras)
        self.assertIn('intent.hasExtra(WatchBinding.EXTRA_SESSION), intent.getStringExtra(WatchBinding.EXTRA_SESSION)', extras)

    def test_closing_the_viewer_never_stops_work(self):
        close = method('private void unavailable()')
        self.assertIn('finishAndRemoveTask();', close)
        self.assertNotRegex(SOURCE, r'Controller\.(stop|agentStop|command|start)\(|StopReceiver|StopLedger')
        # The viewer only reads controller state.
        self.assertNotRegex(SOURCE, r'Controller\.\w+\s*=[^=]')

if __name__ == '__main__':unittest.main()
