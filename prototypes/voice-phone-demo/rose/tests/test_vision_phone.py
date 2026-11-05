"""Terra orchestration with a phone that refuses every accessibility inspection."""
import time
import unittest
from unittest.mock import patch

from rose.tests import test_codex as fixtures
from rose.tests.test_background import Clock, DISPLAY


class PixelsOnly(fixtures.FixtureController):
    def op_command(self, p):
        action = p['action']
        if action == 'inspect_ui':
            raise AssertionError('Vision control must never inspect accessibility nodes')
        if action not in ('screen_gesture', 'screen_text'):
            return super().op_command(p)
        if p.get('sessionId') != self.id or p.get('taskId') != self.task:
            return {'status': 'stale_task'}
        if self.status != 'ready' or p.get('snapshotId') != self.snapshot or self.clock.now() - self.snap_time > 30:
            return {'status': 'stale_screen'}
        if action == 'screen_text':
            self.apps[self.target]['note'] = p['text'] if p['replace'] else self.apps[self.target]['note'] + p['text']
        self.ops += 1
        self._clear()
        return {**self.state(), 'status': 'action_accepted'}


class Vision(unittest.TestCase):
    call = fixtures.Integration.call
    task = fixtures.Integration.task

    def setUp(self):
        fixtures.Integration.setUp(self)
        self.phone.vision = True
        self.controller.slots = [PixelsOnly(Clock(), start_polls=0) for _ in range(2)]
        original = self.controller.__class__.__call__
        self.has_text = True
        self.cap_patch = patch.object(self.controller.__class__, '__call__',
            lambda controller, op, p: {'schemaVersion': 4, 'multiSession': True, 'maxSessions': 2,
                                       'visionText': self.has_text} if op == 'capabilities' else original(controller, op, p))
        self.cap_patch.start()
        self.settle = patch('rose.vision_phone.time.sleep')
        self.settle.start()

    def tearDown(self):
        fixtures.Integration.tearDown(self)
        self.cap_patch.stop()
        self.settle.stop()

    def opened(self, app='Calculator'):
        session = self.task(app)
        self.assertTrue(self.call(session, 'open_app', app=app)['ok'])
        self.assertIsNotNone(session.view.image)
        return session

    def test_open_tap_swipe_type_and_verify_without_a_single_node(self):
        s = self.opened()
        opening = next(p for op, name, p in self.engine.client.calls if op == 'open' and name == s.name)
        tools = {t['name'] for t in opening['tools']}
        self.assertEqual(tools, {'open_app', 'observe', 'tap_point', 'swipe', 'type_text', 'back', 'complete_task'})
        self.assertNotIn('UI tree', opening['instructions'])
        first = s.view.frame_id
        tapped = self.call(s, 'tap_point', frame_id=first, x=140, y=934, target='Work on Cloud', effect='interact')
        self.assertTrue(tapped['ok'])
        self.assertNotEqual(first, tapped['frame_id'])
        raw = [p for op, p in self.controller.calls if op == 'command' and p['action'] == 'screen_gesture'][-1]
        self.assertEqual(raw['points'], [151, 2242])
        self.assertNotIn('nodeId', raw)
        self.assertTrue(self.call(s, 'type_text', frame_id=s.view.frame_id, text='Product feedback\nSecond line', replace=True)['ok'])
        self.assertTrue(self.call(s, 'swipe', frame_id=s.view.frame_id, x1=500, y1=800, x2=500, y2=200)['ok'])
        self.assertTrue(self.call(s, 'back', frame_id=s.view.frame_id)['ok'])
        self.assertTrue(all(p.get('action') != 'inspect_ui' for op, p in self.controller.calls))
        self.assertTrue(self.call(s, 'complete_task', status='succeeded', summary='Feedback is visible in the draft.')['ok'])
        self.engine._event({'event': 'done', 'session': s.name, 'turn': {'id': s.turn, 'status': 'completed'}})
        self.assertEqual(s.task.status, 'done')
        self.assertEqual(s.task.result, 'Feedback is visible in the draft.')

    def test_old_or_other_session_frame_never_dispatches_and_points_are_bounded(self):
        a, b = self.opened(), self.opened('Clock')
        before = len(self.controller.calls)
        for frame in ('old', b.view.frame_id):
            self.assertFalse(self.call(a, 'tap_point', frame_id=frame, x=500, y=500, target='input', effect='interact')['ok'])
        for x in (-1, 1001, float('nan'), True):
            self.assertFalse(self.call(a, 'tap_point', frame_id=a.view.frame_id, x=x, y=500, target='input', effect='interact')['ok'])
        self.assertEqual(len(self.controller.calls), before)
        self.assertTrue(self.call(a, 'tap_point', frame_id=a.view.frame_id, x=1000, y=1000, target='corner', effect='interact')['ok'])
        raw = [p for op, p in self.controller.calls if op == 'command' and p['action'] == 'screen_gesture'][-1]
        self.assertEqual(raw['points'], [DISPLAY[0]-1, DISPLAY[1]-1])

    def test_missing_phone_text_support_reports_update_without_accessibility_fallback(self):
        self.has_text = False
        s = self.opened()
        before = len(self.controller.calls)
        result = self.call(s, 'type_text', frame_id=s.view.frame_id, text='feedback', replace=True)
        self.assertFalse(result['ok'])
        self.assertIn('controller needs', result['result'])
        self.assertEqual(len(self.controller.calls), before)
        self.call(s, 'complete_task', status='blocked', summary='The phone needs the vision input update.')
        self.engine._event({'event': 'done', 'session': s.name, 'turn': {'id': s.turn, 'status': 'completed'}})
        self.assertEqual(s.task.status, 'failed')
        self.assertIn('vision input update', s.task.result)

    def test_owner_stop_lock_expired_frame_and_missing_image_prevent_input(self):
        s = self.opened()
        s.view.frame_at = time.monotonic() - 31
        self.assertFalse(self.call(s, 'back', frame_id=s.view.frame_id)['ok'])
        self.call(s, 'observe')
        slot = next(x for x in self.controller.slots if x.id == s.view.workspace.bound_id)
        slot.locked = True
        self.assertFalse(self.call(s, 'tap_point', frame_id=s.view.frame_id, x=500, y=500, target='input', effect='interact')['ok'])
        self.assertIsNone(s.view.image)
        self.assertFalse(self.call(s, 'back', frame_id='missing')['ok'])
        slot.locked = False
        self.call(s, 'observe')
        slot.owner_stop()
        self.assertFalse(self.call(s, 'back', frame_id=s.view.frame_id)['ok'])
        self.assertFalse(self.call(s, 'complete_task', status='succeeded', summary='All done')['ok'])

    def test_no_outcome_and_forbidden_submissions_never_report_success(self):
        s = self.opened()
        for effect in ('send_message', 'purchase', 'publish', 'delete', 'security'):
            self.assertFalse(self.call(s, 'tap_point', frame_id=s.view.frame_id, x=500, y=500, target='submit', effect=effect)['ok'])
        self.engine._event({'event': 'done', 'session': s.name, 'turn': {'id': s.turn, 'status': 'completed'}})
        self.assertEqual(s.task.status, 'failed')


if __name__ == '__main__':
    unittest.main()
