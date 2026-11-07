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
        self.phone.vision_control = True  # --vision-control
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

    def commands(self):
        # The owner-Stop watcher independently polls get_state. Only UI commands
        # would indicate an input or accessibility fallback from these refusals.
        return [p for op, p in self.controller.calls if op == 'command']

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
        self.assertTrue(self.call(s, 'type_text', frame_id=s.view.frame_id, text='Product feedback, second line', replace=True)['ok'])
        self.assertTrue(self.call(s, 'swipe', frame_id=s.view.frame_id, x1=500, y1=800, x2=500, y2=200)['ok'])
        self.assertTrue(self.call(s, 'back', frame_id=s.view.frame_id)['ok'])
        self.assertTrue(all(p.get('action') != 'inspect_ui' for op, p in self.controller.calls))
        self.assertTrue(self.call(s, 'complete_task', status='succeeded', summary='Feedback is visible in the draft.')['ok'])
        self.engine._event({'event': 'done', 'session': s.name, 'turn': {'id': s.turn, 'status': 'completed'}})
        self.assertEqual(s.task.status, 'done')
        self.assertEqual(s.task.result, 'Feedback is visible in the draft.')

    def test_old_or_other_session_frame_never_dispatches_and_points_are_bounded(self):
        a, b = self.opened(), self.opened('Clock')
        before = len(self.commands())
        for frame in ('old', b.view.frame_id):
            self.assertFalse(self.call(a, 'tap_point', frame_id=frame, x=500, y=500, target='input', effect='interact')['ok'])
        for x in (-1, 1001, float('nan'), True):
            self.assertFalse(self.call(a, 'tap_point', frame_id=a.view.frame_id, x=x, y=500, target='input', effect='interact')['ok'])
        self.assertEqual(len(self.commands()), before)
        self.assertTrue(self.call(a, 'tap_point', frame_id=a.view.frame_id, x=1000, y=1000, target='corner', effect='interact')['ok'])
        raw = [p for op, p in self.controller.calls if op == 'command' and p['action'] == 'screen_gesture'][-1]
        self.assertEqual(raw['points'], [DISPLAY[0]-1, DISPLAY[1]-1])

    def test_missing_phone_text_support_reports_update_without_accessibility_fallback(self):
        self.has_text = False
        s = self.opened()
        before = len(self.commands())
        result = self.call(s, 'type_text', frame_id=s.view.frame_id, text='feedback', replace=True)
        self.assertFalse(result['ok'])
        self.assertIn('controller needs', result['result'])
        self.assertEqual(len(self.commands()), before)
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

    def test_line_breaks_are_refused_before_any_input(self):
        s = self.opened()
        before = len(self.commands())
        for text in ('first\nsecond', 'sent\r', 'a\u2028b', 'a\u2029b', 'a\x85b'):
            result = self.call(s, 'type_text', frame_id=s.view.frame_id, text=text, replace=True)
            self.assertFalse(result['ok'])
            self.assertIn('single line', result['result'])
        self.assertEqual(len(self.commands()), before)
        self.assertIsNotNone(s.view.frame_id)  # a refusal never consumes the frame

    FINAL = ('Send', 'Buy now', 'Place order', 'Pay $20', 'Submit', 'Publish', 'Confirm order', 'Checkout button',
             'the Send button', 'Send icon', 'Send arrow', 'blue send arrow at bottom right', '"Send" button', 'Tap Send',
             'Send to Sam', 'green send icon', 'the Submit button', 'Submit review', 'Post', 'Tweet', 'Tap Post',
             'the Post button', 'Post comment', 'Post reply button', 'Post now', 'Post in the top right corner',
             'Post (top right)', 'Post, top-right corner', 'Tweet in the top right corner', 'the blue Post text button',
             'Post button (top right)', 'the blue Post button at the top right', 'Payment methods')
    # Ordinary controls, including those naming a final control as a landmark, a field, a setting or a feed item.
    ORDINARY = ('Reply', 'paper airplane DM icon at top right', 'Message field', 'Search', 'Sent folder', 'Settings gear',
                'Back arrow', 'the latest post', 'Open the first post', 'Like button on the post', 'Comment on the post',
                'Tap the post to open it', 'the post comment box', 'New post', 'Create post', 'the new post button',
                'the Create post button', 'the compose tweet button', 'the + new post button', 'the tweet by NASA',
                'Like button on the tweet', 'Send feedback', 'Send feedback row', "the 'Send message' field",
                'the Send a chat text box', 'Send read receipts toggle', 'Enter is send toggle',
                'the message field next to the send button', 'the emoji icon beside the send button',
                'the camera icon next to the send button', 'the Share via paper plane icon')
    # Once a draft is typed, "Reply" and the paper plane send it.
    DRAFT_FINAL = ('Reply', 'the Reply button', 'paper plane icon', 'paper airplane icon', 'paper-airplane button')

    def test_final_target_reads_the_models_description(self):
        from rose.vision_phone import final_target
        for target in self.FINAL:
            self.assertTrue(final_target(target, False), target)
            self.assertTrue(final_target(target, True), target)
        for target in self.ORDINARY:
            self.assertFalse(final_target(target, False), target)
        for target in self.DRAFT_FINAL:
            self.assertTrue(final_target(target, True), target)
        self.assertFalse(final_target('the reply text field', True))

    def test_a_final_control_called_interact_is_still_the_owners(self):
        s = self.opened()
        before = len(self.commands())
        for target in ('', '   ', 'the Send button', 'Post (top right)', 'Buy now'):
            result = self.call(s, 'tap_point', frame_id=s.view.frame_id, x=500, y=500, target=target, effect='interact')
            self.assertFalse(result['ok'], target)
        self.assertEqual(len(self.commands()), before)
        self.assertTrue(self.call(s, 'tap_point', frame_id=s.view.frame_id, x=500, y=500, target='Reply', effect='interact')['ok'])
        self.assertTrue(self.call(s, 'type_text', frame_id=s.view.frame_id, text='On my way', replace=True)['ok'])
        before = len(self.commands())
        self.assertFalse(self.call(s, 'tap_point', frame_id=s.view.frame_id, x=500, y=500, target='Reply', effect='interact')['ok'])
        self.assertEqual(len(self.commands()), before)
        self.assertTrue(self.call(s, 'tap_point', frame_id=s.view.frame_id, x=500, y=500, target='the message field next to the send button', effect='interact')['ok'])

    def test_an_unanswered_type_still_counts_as_a_draft(self):
        s = self.opened()
        with patch.object(s.view.workspace, 'command', return_value={'status': 'timeout'}):
            self.assertFalse(self.call(s, 'type_text', frame_id=s.view.frame_id, text='On my way', replace=True)['ok'])
        s.view.refresh()
        self.assertFalse(self.call(s, 'tap_point', frame_id=s.view.frame_id, x=500, y=500, target='Reply', effect='interact')['ok'])

    def test_a_refusal_the_task_moved_past_is_not_its_result(self):
        s = self.opened()
        self.assertFalse(self.call(s, 'tap_point', frame_id='old', x=500, y=500, target='input', effect='interact')['ok'])
        self.assertTrue(self.call(s, 'tap_point', frame_id=s.view.frame_id, x=500, y=500, target='input', effect='interact')['ok'])
        self.engine._event({'event': 'done', 'session': s.name, 'turn': {'id': s.turn, 'status': 'completed'}})
        self.assertEqual(s.task.status, 'failed')
        self.assertEqual(s.task.result, 'No verified task outcome was reported.')

    def test_a_refused_open_names_the_app_and_survives_as_the_task_result(self):
        s = self.task('Calculator')
        with patch.object(s.view.workspace, 'start', return_value={'status': 'background_target_not_allowed'}):
            result = self.call(s, 'open_app', app='Calculator')
        self.assertFalse(result['ok'])
        self.assertIn('Calculator', result['result'])
        self.assertNotIn('that app', result['result'])
        self.engine._event({'event': 'done', 'session': s.name, 'turn': {'id': s.turn, 'status': 'completed'}})
        self.assertEqual(s.task.status, 'failed')
        self.assertEqual(s.task.result, result['result'])


class VisionOptIn(unittest.TestCase):
    """Vision routing is the owner's opt-in (--vision-control); by default app threads keep the accessibility path."""
    call = fixtures.Integration.call
    task = fixtures.Integration.task

    def setUp(self):
        fixtures.Integration.setUp(self)
        self.phone.vision = True  # RealPhone's default: screenshots with the UI tree

    def tearDown(self):
        fixtures.Integration.tearDown(self)

    def test_default_real_phone_keeps_the_accessibility_session(self):
        from rose.realphone import RealPhone
        from rose.vision_phone import VisionSession
        self.assertFalse(RealPhone.vision_control)
        s = self.task('Calculator')
        self.assertNotIsInstance(s.view, VisionSession)
        self.assertFalse(getattr(s.view, 'visual_only', False))
        opening = next(p for op, name, p in self.engine.client.calls if op == 'open' and name == s.name)
        tools = {t['name'] for t in opening['tools']}
        self.assertIn('tap', tools)  # the accessibility tools, with their send/purchase guard
        self.assertNotIn('complete_task', tools)

    def test_the_flag_needs_codex_and_the_real_phone(self):
        from rose import app
        self.assertTrue(app.parse_args(['--brain', 'codex', '--phone', 'real', '--vision-control']).vision_control)
        self.assertFalse(app.parse_args(['--brain', 'codex', '--phone', 'real']).vision_control)
        for argv in (['--brain', 'codex', '--vision-control'], ['--phone', 'real', '--vision-control']):
            with self.assertRaises(SystemExit), patch('sys.stderr'):
                app.parse_args(argv)


if __name__ == '__main__':
    unittest.main()
