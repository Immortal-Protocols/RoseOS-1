"""Offline integration: simulated Codex + controller, real orchestration/storage/phone routing.
No model inference, real voice, device, credentials or deployed service.
"""
import json
import importlib.util
import os
from unittest.mock import patch
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

from rose.cloud_audio import prepare
if importlib.util.find_spec('mlx') is None:
    prepare()
from rose.bus import Bus
from rose.codex_conductor import CodexConductor
from rose.transcripts import Transcripts, redact
from rose.realphone import RealPhone
from rose.scoped_workspace import ScopedWorkspace
from rose.remote import configured_hosts
from rose.tests.test_background import Clock, FakeController, APPS, CALC, NOTES

CLOCK = 'com.google.android.deskclock'
APPS.append({'label':'Clock','packageName':CLOCK})

class FixtureController(FakeController):
    def _screen(self):
        rows = super()._screen()
        if self.target == CLOCK:
            rows[1]['text'] = 'Timer running' if self.apps[CLOCK].get('timer') else 'Clock'
            rows.append({**rows[0], 'id': 2, 'nodeId': 2, 'class':'android.widget.Button', 'className':'android.widget.Button', 'text':'Start timer',
                         'bounds':(0,900,1080,1100), 'clickable':True})
        return rows

    def _tapped(self, app, node):
        if self.target == CLOCK:
            app['timer'] = True
        else:
            super()._tapped(app, node)


def wait(predicate, seconds=4):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(.01)
    return False


class MultiController:
    """Two schema-4 slots dispatching to the existing controller fixture."""
    connected = True
    model = 'simulated phone'
    def __init__(self):
        self.slots = [FixtureController(Clock(), start_polls=0) for _ in range(2)]
        self.calls = []
        self.lock = threading.Lock()

    def call(self, action, **args):
        if action == 'list_apps':
            return {'status': 'ok', 'apps': APPS}
        assert action == 'app_command', action
        return self(args['operation'], args['params'])

    def __call__(self, op, p):
        with self.lock:
            self.calls.append((op, dict(p)))
            if op == 'capabilities':
                return {'schemaVersion': 4, 'multiSession': True, 'maxSessions': 2}
            if op == 'start':
                assert p['mode'] == 'alongside'
                slot = next((s for s in self.slots if s.status in ('idle', 'stopped')), None)
                if slot is None:
                    return {'status': 'session_limit'}
            else:
                assert p.get('sessionId'), 'no unscoped read, action or stop'
                slot = next((s for s in self.slots if s.id == p['sessionId'] or s.stopped_session == p['sessionId']), None)
                if slot is None:
                    return {'status': 'stale_session'}
            return slot(op, p)


class FakeCodex:
    def __init__(self, on_event):
        self.event, self.cwd, self.calls, self.results = on_event, Path('/tmp'), [], []
        self.n = 0
        self.opens = 0

    def request(self, op, session, **p):
        self.calls.append((op, session, p))
        if op == 'open':
            self.opens += 1
            return {'threadId': f'thread-{session}-{self.opens}', 'model': 'gpt-6.1-terra', 'serviceTier': 'fast', 'authMode': 'chatgpt'}
        if op == 'submit':
            self.n += 1
            tid = str(self.n)
            self.event({'event': 'started', 'session': session, 'turnId': tid})
            return {'turn': {'id': tid}}
        return {}

    def send(self, message):
        self.results.append(message)

    def close(self):
        pass


class FakeUtterance:
    def __init__(self, rid, auto_finish=True):
        self.id, self.auto_finish = rid, auto_finish
        self.text, self.interrupted = '', False
        self.done = threading.Event()

    def add(self, text):
        self.text += text

    def finish(self):
        if self.auto_finish:
            self.done.set()

    def cancel(self):
        self.interrupted = True
        self.done.set()

    def heard(self):
        return self.text


class FakeSpeaker:
    audible = False

    def __init__(self, auto_finish=True):
        self.auto_finish, self.utterances = auto_finish, []

    def utterance(self, rid, started):
        utterance = FakeUtterance(rid, self.auto_finish)
        self.utterances.append(utterance)
        return utterance

    def stop(self):
        for utterance in self.utterances:
            utterance.cancel()


class Integration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Transcripts(self.tmp.name)
        self.phone = RealPhone()
        self.controller = MultiController()
        self.phone.link = self.controller
        self.phone.vision = False
        self.phone._apps, self.phone._apps_at = APPS, time.monotonic()
        self.bus = Bus()
        self.engine = CodexConductor(self.phone, self.bus, None, self.store, client_factory=FakeCodex)
        self.engine.start()

    def tearDown(self):
        self.engine.stop()
        wait(lambda: all(s.closed for s in self.engine.sessions.values() if s.task))
        self.store.close()
        self.tmp.cleanup()

    def call(self, session, name, **args):
        e = {'callId': 'ipc-'+str(len(self.engine.client.results)), 'params': {
            'tool': name, 'arguments': args, 'turnId': session.turn, 'callId': 'tool-'+name}}
        self.engine._tool(session, e)
        return json.loads(self.engine.client.results[-1]['result']['contentItems'][0]['text'])

    def task(self, app):
        task = self.engine.start_phone(app, 'Open '+app)
        s = self.engine.sessions['app-'+str(task.id)]
        self.assertTrue(wait(lambda: s.turn is not None))
        return s

    def test_two_apps_targeted_actions_cancel_one_and_retrieve_after_restart(self):
        a, b = self.task('Calculator'), self.task('Clock')
        self.assertTrue(self.call(a, 'open_app', app='Calculator')['ok'])
        self.assertTrue(self.call(b, 'open_app', app='Clock')['ok'])
        aid, bid = a.view.workspace.bound_id, b.view.workspace.bound_id
        self.assertNotEqual(aid, bid)
        self.assertTrue(self.call(a, 'press_keys', keys='1+2=')['ok'])
        button = next(e.mark for e in b.view.screen.elements if e.label == 'Start timer')
        self.assertTrue(self.call(b, 'tap', id=button)['ok'])
        self.assertIn('3', a.view.ui_tree())
        self.assertIn('Timer running', b.view.ui_tree())
        self.assertTrue(self.call(a, 'observe')['ok'])
        self.assertTrue(self.call(b, 'observe')['ok'])
        self.engine.cancel(a.task.id)
        self.assertTrue(wait(lambda: a.closed))
        self.assertEqual(a.task.status, 'cancelled')
        self.assertFalse(b.cancelled.is_set())
        self.assertTrue(self.call(b, 'observe')['ok'])
        self.assertFalse(self.call(a, 'open_app', app='Calculator')['ok'])
        stop_ids = [p['sessionId'] for op, p in self.controller.calls if op == 'stop']
        self.assertIn(aid, stop_ids)
        self.assertNotIn(bid, stop_ids)
        cid = self.engine.conversation
        with_view = [e for e in self.bus.history if e['kind']=='task' and e['task'].get('workspace')]
        self.assertTrue(with_view)
        self.assertEqual(with_view[-1]['task']['server'], cid)
        # Independent database connection simulates retrieval by a restarted process.
        reopened = Transcripts(self.tmp.name)
        events = reopened.export(cid)
        reopened.close()
        self.assertTrue(any(e['kind']=='tool_result' for e in events))
        self.assertTrue(any(e['kind']=='cancellation_acknowledged' for e in events))
        self.assertTrue(all(e['timestamp'] and e['model']=='gpt-6.1-terra' for e in events))

    def test_barge_in_only_interrupts_conversation(self):
        a = self.task('Calculator')
        self.engine._user_turn('Hello')
        self.engine._interrupt_reply('barge in')
        self.assertFalse(a.cancelled.is_set())
        self.assertTrue(a.busy)
        self.assertIn(('interrupt', 'conversation', {}), self.engine.client.calls)

    def test_slow_phone_action_publishes_progress_then_bound_snapshot(self):
        from rose.app import Server
        a = self.task('Calculator')
        entered, release = threading.Event(), threading.Event()
        send = a.view.workspace.send
        def slow(op, params):
            if op == 'start':
                entered.set()
                release.wait(3)
            return send(op, params)
        a.view.workspace.send = slow
        worker = threading.Thread(target=self.call, args=(a, 'open_app'), kwargs={'app': a.app})
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
            latest = [e['task'] for e in self.bus.history if e['kind'] == 'task'][-1]
            self.assertEqual(latest['status'], 'running')
            self.assertEqual(latest['detail'], 'Opening Calculator.')
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        state = SimpleNamespace(mode='live', tasks=self.engine, _floors=(0, 0))
        snapshot = Server.task_list(state)[0]
        latest = [e['task'] for e in self.bus.history if e['kind'] == 'task'][-1]
        for key in ('id', 'server', 'contract', 'workspace', 'detail'):
            self.assertEqual(snapshot[key], latest[key])
        self.assertEqual(snapshot['workspace']['sessionId'], a.view.workspace.bound_id)

    def complete(self, s):
        self.assertTrue(self.call(s, 'open_app', app=s.app)['ok'])
        s.text = s.app + ' is open.'
        self.engine._event({'event': 'done', 'session': s.name,
                            'turn': {'id': s.turn, 'status': 'completed'}})
        self.assertEqual(s.task.status, 'done')

    def test_third_app_releases_oldest_completed_session_and_preserves_results(self):
        a, b = self.task('Calculator'), self.task('Clock')
        self.complete(a)
        self.complete(b)
        old = a.task.to_dict()
        aid, bid = a.view.workspace.bound_id, b.view.workspace.bound_id
        c = self.task('Keep Notes')
        self.assertTrue(self.call(c, 'open_app', app=c.app)['ok'])
        self.assertTrue(a.closed)
        self.assertFalse(b.closed)
        self.assertEqual(a.task.to_dict(), old)
        stopped = [p['sessionId'] for op,p in self.controller.calls if op == 'stop']
        self.assertIn(aid, stopped)
        self.assertNotIn(bid, stopped)
        self.assertTrue(any(e['kind'] == 'session_released'
                            for e in self.store.export(self.engine.conversation)))
        retained_thread = b.thread
        followup = self.engine.start_phone('Clock', 'Observe Clock again')
        self.assertTrue(wait(lambda: b.busy))
        self.assertIs(followup, b.task)
        self.assertEqual(b.thread, retained_thread)

    def test_new_app_reclaims_only_completed_task_while_other_app_runs(self):
        a, b = self.task('Calculator'), self.task('Clock')
        self.assertTrue(self.call(a, 'open_app', app=a.app)['ok'])
        self.complete(b)
        aid = a.view.workspace.bound_id
        c = self.task('Keep Notes')
        self.assertTrue(self.call(c, 'open_app', app=c.app)['ok'])
        self.assertFalse(a.cancelled.is_set())
        self.assertTrue(a.busy)
        self.assertTrue(self.call(a, 'observe')['ok'])
        self.assertNotIn(aid, [p['sessionId'] for op,p in self.controller.calls if op == 'stop'])

    def test_two_running_apps_report_capacity_without_generic_tool_failure(self):
        a, b = self.task('Calculator'), self.task('Clock')
        result = self.call(self.engine.sessions['conversation'], 'phone_task',
                           app='Keep Notes', goal='Open Keep Notes')
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'app_sessions_busy')
        self.assertIn('running', result['message'])
        self.assertTrue(a.busy and b.busy)
        self.assertFalse(any(op == 'stop' for op,_ in self.controller.calls))

    def test_foreground_app_refusal_keeps_actionable_reason_in_failed_result(self):
        a = self.task('Calculator')
        send = a.view.workspace.send
        def refuse_foreground(op, params):
            if op == 'start':
                return {'status': 'failed', 'sessionId': 'refused', 'targetPackage': CALC,
                        'reason': 'target_in_foreground', 'error': 'target_in_foreground: '+CALC}
            return send(op, params)
        a.view.workspace.send = refuse_foreground
        result = self.call(a, 'open_app', app=a.app)
        self.assertFalse(result['ok'])
        self.assertIn('open on your phone screen', result['result'])
        self.assertIn("this RoseOS build can't take over an app you can see yet", result['result'])
        self.assertIn('nothing was opened', result['result'])
        self.assertNotIn('switch', result['result'].lower())  # a retry from the background may lose the app's state here
        a.text = 'The app could not open.'
        self.engine._event({'event': 'done', 'session': a.name,
                            'turn': {'id': a.turn, 'status': 'completed'}})
        self.assertEqual(a.task.status, 'failed')
        self.assertEqual(a.task.result, result['result'])
        self.assertFalse(any(op == 'start' for op,_ in self.controller.calls))

    def test_platform_other_session_backstop_reads_as_the_controllers_refusal(self):
        a = self.task('Calculator')
        send = a.view.workspace.send
        def refuse_other_session(op, params):
            if op == 'start':
                return {'status': 'failed', 'sessionId': 'refused', 'targetPackage': CALC, 'reason': '',
                        'error': 'java.lang.IllegalStateException: target_in_other_session: '+CALC}
            return send(op, params)
        a.view.workspace.send = refuse_other_session
        result = self.call(a, 'open_app', app=a.app)
        self.assertFalse(result['ok'])
        self.assertIn('open on another screen or workspace', result['result'])
        self.assertIn('nothing was opened', result['result'])

    def test_refusal_codes_count_only_for_this_starts_own_failure(self):
        from rose.realphone import _ws_why
        stale = {'status': 'background_unavailable', 'packageName': CLOCK, 'details': {
            'status': 'failed', 'targetPackage': CALC, 'reason': '',
            'error': 'java.lang.IllegalStateException: target_in_other_session: '+CALC}}
        self.assertNotIn('another screen', _ws_why(stale, 'Clock'))  # Calculator's earlier failure, not Clock's
        other_pkg = {'status': 'background_unavailable', 'packageName': CLOCK, 'details': {
            'status': 'failed', 'targetPackage': CLOCK, 'error': 'java.lang.IllegalStateException: target_not_standard: '+CALC}}
        self.assertNotIn("can't take over", _ws_why(other_pkg, 'Clock'))
        launch_race = {'status': 'background_unavailable', 'packageName': CALC, 'details': {
            'status': 'failed', 'targetPackage': CALC, 'reason': '', 'error': 'java.lang.IllegalStateException: app_in_other_session'}}
        self.assertIn("open in Rose's workspace for another of her tasks", _ws_why(launch_race, 'Calculator'))
        not_failed = {'status': 'background_unavailable', 'packageName': CALC, 'details': {
            'status': 'starting', 'targetPackage': CALC, 'error': 'java.lang.IllegalStateException: target_in_other_session: '+CALC}}
        self.assertNotIn('another screen', _ws_why(not_failed, 'Calculator'))

    def test_home_or_recents_target_is_refused_with_its_own_reason(self):
        for refusal in ({'status': 'target_not_allowed', 'reason': 'target_not_standard', 'actionExecuted': False},
                        {'status': 'failed', 'sessionId': 'refused', 'targetPackage': CALC, 'reason': '',
                         'error': 'java.lang.IllegalStateException: target_not_standard: '+CALC}):
            with self.subTest(refusal=refusal['status']):
                a = self.task('Calculator')
                send = a.view.workspace.send
                a.view.workspace.send = lambda op, params, send=send, refusal=refusal: dict(refusal) if op == 'start' else send(op, params)
                result = self.call(a, 'open_app', app=a.app)
                self.assertFalse(result['ok'])
                self.assertIn("can't take over Calculator while the phone uses it as the Home screen, recents", result['result'])
                self.assertIn('nothing was opened', result['result'])
                self.engine.cancel(a.task.id)

    def test_unconfirmed_slot_release_blocks_replacement_and_preserves_task_result(self):
        a, b = self.task('Calculator'), self.task('Clock')
        self.complete(a)
        self.complete(b)
        old = a.task.to_dict()
        a.view.workspace.close = lambda reason: {'status': 'timeout'}
        for _ in range(2):
            result = self.call(self.engine.sessions['conversation'], 'phone_task',
                               app='Keep Notes', goal='Open Keep Notes')
            self.assertFalse(result['ok'])
            self.assertEqual(result['reason'], 'session_cleanup_unconfirmed')
        self.assertEqual(a.task.to_dict(), old)
        self.assertEqual(len(self.engine.tasks), 2)
        self.assertTrue(any(e['kind'] == 'session_release_uncertain'
                            for e in self.store.export(self.engine.conversation)))

    def test_standalone_okay_is_a_turn_and_in_reply_okay_is_a_backchannel(self):
        turns = []
        self.engine.inject_text_turn = turns.append
        self.engine.on_listen(SimpleNamespace(kind='speech_start',text=''))
        self.engine.on_listen(SimpleNamespace(kind='turn_end',text='okay'))
        self.assertEqual(turns,['okay'])
        s = self.engine.sessions['conversation']
        s.busy = True
        self.engine.on_listen(SimpleNamespace(kind='speech_start',text=''))
        self.engine.on_listen(SimpleNamespace(kind='turn_end',text='okay'))
        self.assertEqual(turns,['okay'])
        self.assertFalse(s.cancelled.is_set())
        s.busy = False

    def test_expired_login_is_visible_and_persisted_without_fallback(self):
        request = self.engine.client.request
        def expired(op, session, **p):
            if op == 'submit':
                raise RuntimeError('chatgpt_login_required')
            return request(op, session, **p)
        self.engine.client.request = expired
        self.engine._user_turn('Hello')
        self.assertTrue(any(e['kind']=='rose_turn' and 'Sign in with ChatGPT' in e['text'] for e in self.bus.history))
        self.assertTrue(any(e['kind']=='failure' and e['data']['reason']=='chatgpt_login_required' for e in self.store.export(self.engine.conversation)))

    def test_failure_and_late_completion_do_not_revive_cancelled_task(self):
        a = self.task('Calculator')
        turn = a.turn
        self.engine.cancel(a.task.id)
        self.engine._event({'event':'done','session':a.name,'turn':{'id':turn,'status':'completed'}})
        self.assertTrue(wait(lambda: a.task.status=='cancelled'))
        b = self.task('Keep Notes')
        self.engine._event({'event':'failure','session':b.name})
        self.assertEqual(b.task.status,'failed')
        self.assertTrue(any(e['kind']=='failure' for e in self.store.export(self.engine.conversation)))

    def test_conversation_continues_same_thread_then_restart_is_explicit_new_conversation(self):
        self.engine._user_turn('Remember the example')
        first = self.engine.sessions['conversation'].thread
        self.engine._user_turn('Continue')
        self.assertEqual(first, self.engine.sessions['conversation'].thread)
        cid = self.engine.conversation
        other = CodexConductor(self.phone, Bus(), None, self.store, client_factory=FakeCodex)
        other.start()
        self.assertNotEqual(other.conversation,cid)
        self.assertEqual(len([e for e in self.store.export(cid) if e['kind']=='user_message']),2)
        other.stop()

    def test_reset_discards_already_queued_events_from_old_thread(self):
        self.engine._user_turn('Hello')
        old = self.engine.sessions['conversation'].thread
        self.engine.reset()
        current = self.engine.sessions['conversation']
        self.assertNotEqual(old, current.thread)
        before = self.store.export(self.engine.conversation)
        for event in ({'event':'started','turnId':'old-turn'},
                      {'event':'message','turnId':'old-turn','text':'stale reply'},
                      {'event':'done','turn':{'id':'old-turn','status':'completed'}}):
            self.engine._event(dict(event, session='conversation', threadId=old))
        self.assertIsNone(current.turn)
        self.assertEqual(self.store.export(self.engine.conversation), before)

    def test_completed_task_followup_returns_new_controllable_id(self):
        a = self.task('Calculator')
        self.call(a, 'open_app', app='Calculator')
        self.engine._event({'event':'done','session':a.name,'turn':{'id':a.turn,'status':'completed'}})
        old = a.task
        self.assertEqual(old.status,'done')
        result, _ = self.engine._conversation_tool('update_task', {'task_id':old.id,'instruction':'Continue in Calculator'})
        self.assertTrue(result['ok'])
        self.assertNotEqual(result['task_id'],old.id)
        self.assertTrue(wait(lambda: a.turn is not None and a.busy))
        self.assertTrue(self.engine.cancel(result['task_id']))
        self.assertTrue(wait(lambda: a.closed))
        self.assertEqual(a.task.status,'cancelled')
        self.assertEqual(old.status,'done')

    def test_lost_workspace_start_reply_never_claims_confirmed_stop(self):
        a = self.task('Calculator')
        send = a.view.workspace.send
        def lost(op,p):
            result = send(op,p)
            return {'status':'timeout'} if op=='start' else result
        a.view.workspace.send = lost
        self.assertFalse(self.call(a,'open_app',app='Calculator')['ok'])
        self.assertTrue(a.view.workspace.uncertain)
        self.engine.cancel(a.task.id)
        self.assertTrue(wait(lambda: a.closed))
        self.assertEqual(a.task.status,'failed')
        events = self.store.export(self.engine.conversation)
        self.assertTrue(any(e['kind']=='cancellation_uncertain' for e in events))
        self.assertFalse(any(e['kind']=='cancellation_acknowledged' for e in events))
        self.assertFalse(any(op=='stop' for op,_ in self.controller.calls))

    def test_queued_task_announcement_keeps_its_result_after_followup(self):
        self.engine.sessions['conversation'].busy = True
        a = self.task('Calculator')
        self.call(a, 'open_app', app='Calculator')
        a.text = 'Calculator is open.'
        self.engine._event({'event':'done','session':a.name,'turn':{'id':a.turn,'status':'completed'}})
        completed_thread, completed_turn = a.thread, a.turn
        self.engine.update(a.task.id, 'Continue in Calculator')
        self.assertTrue(wait(lambda: a.busy))
        speaker = FakeSpeaker()
        with patch.object(self.engine, 'speaker', speaker):
            self.engine.sessions['conversation'].busy = False
            self.assertTrue(wait(lambda: any(e['kind'] == 'assistant_heard'
                                           for e in self.store.export(self.engine.conversation))))
        self.assertEqual([u.text for u in speaker.utterances], ['Calculator is open.'])
        heard = next(e for e in self.store.export(self.engine.conversation) if e['kind'] == 'assistant_heard')
        self.assertEqual((heard['thread_id'], heard['turn_id']), (completed_thread, completed_turn))
        self.assertTrue(self.engine._worker.is_alive())

    def test_two_task_results_do_not_stall_conversation_or_overlap_pending_speech(self):
        self.engine.sessions['conversation'].busy = True
        a, b = self.task('Calculator'), self.task('Clock')
        for s in (a, b):
            self.call(s, 'open_app', app=s.app)
            s.text = s.app + ' is open.'
            self.engine._event({'event': 'done', 'session': s.name,
                                'turn': {'id': s.turn, 'status': 'completed'}})
        speaker = FakeSpeaker(auto_finish=False)
        with patch.object(self.engine, 'speaker', speaker):
            self.engine.sessions['conversation'].busy = False
            self.assertTrue(wait(lambda: len(speaker.utterances) == 1))
            # No audio has reached the transport yet, so audible remains false.
            self.assertFalse(wait(lambda: len(speaker.utterances) > 1, seconds=.2))
            speaker.utterances[0].done.set()
            self.assertTrue(wait(lambda: len(speaker.utterances) == 2))
            speaker.utterances[1].done.set()
            self.engine._user_turn('Are you still there?')
            s = self.engine.sessions['conversation']
            self.assertTrue(wait(lambda: any(e['kind'] == 'turn_started' and e['turn_id'] == s.turn
                                           for e in self.store.export(self.engine.conversation))))
            self.assertTrue(self.engine._worker.is_alive())
        self.assertEqual([u.text for u in speaker.utterances[:2]], ['Calculator is open.', 'Clock is open.'])

    def test_result_returned_to_conversation_is_not_announced_again(self):
        self.engine.sessions['conversation'].busy = True
        a, b = self.task('Calculator'), self.task('Clock')
        self.call(a, 'open_app', app=a.app)
        a.text = 'Calculator is open.'
        self.engine._event({'event': 'done', 'session': a.name,
                            'turn': {'id': a.turn, 'status': 'completed'}})
        result, _ = self.engine._conversation_tool('list_tasks', {})
        self.assertEqual(result['tasks'][0]['result'], a.text)
        speaker = FakeSpeaker()
        with patch.object(self.engine, 'speaker', speaker):
            self.engine.sessions['conversation'].busy = False
            self.assertTrue(wait(self.engine._announcements.empty))
            self.assertEqual(speaker.utterances, [])
            # A result that was still running during list_tasks still needs its announcement.
            self.call(b, 'open_app', app=b.app)
            b.text = 'Clock is open.'
            self.engine._event({'event': 'done', 'session': b.name,
                                'turn': {'id': b.turn, 'status': 'completed'}})
            self.assertTrue(wait(lambda: any(e['kind'] == 'assistant_heard'
                                           for e in self.store.export(self.engine.conversation))))
        self.assertEqual([u.text for u in speaker.utterances], ['Clock is open.'])
        self.assertTrue(self.engine._worker.is_alive())

    def test_announcement_error_does_not_kill_event_worker(self):
        announce = self.engine._announce
        attempts = []
        def fail_once():
            attempts.append(True)
            if len(attempts) == 1:
                raise RuntimeError('simulated speech failure')
            announce()
        with patch.object(self.engine, '_announce', side_effect=fail_once):
            self.assertTrue(wait(lambda: attempts))
            self.engine._user_turn('Hello after the speech failure')
            self.assertTrue(wait(lambda: any(e['kind'] == 'turn_started'
                                           for e in self.store.export(self.engine.conversation))))
            self.assertTrue(self.engine._worker.is_alive())

    def test_model_image_resize_does_not_mutate_cached_phone_observation(self):
        from PIL import Image
        original = Image.new('RGB',(1080,1920))
        self.phone.look = lambda: original
        self.call(self.engine.sessions['conversation'],'look_at_screen')
        self.assertEqual(original.size,(1080,1920))
        self.assertEqual(self.engine.client.results[-1]['result']['contentItems'][1]['type'],'inputImage')

    def test_native_owner_stop_interrupts_only_matching_codex_thread(self):
        a, b = self.task('Calculator'), self.task('Clock')
        self.call(a,'open_app',app='Calculator')
        self.call(b,'open_app',app='Clock')
        slot=next(s for s in self.controller.slots if s.id==a.view.workspace.bound_id)
        slot.owner_stop()
        self.assertTrue(wait(lambda: a.cancelled.is_set()))
        self.assertTrue(wait(lambda: a.closed))
        self.assertFalse(b.cancelled.is_set())
        self.assertTrue(self.call(b,'observe')['ok'])

    def test_cancel_during_initialization_never_starts_turn_or_leaks_thread(self):
        entered,release=threading.Event(),threading.Event()
        request=self.engine.client.request
        def slow(op,session,**p):
            if op=='open' and session!='conversation':
                entered.set();release.wait(3)
            return request(op,session,**p)
        self.engine.client.request=slow
        task=self.engine.start_phone('Calculator','Open Calculator')
        self.assertTrue(entered.wait(3))
        self.engine.cancel(task.id)
        self.assertEqual(task.status,'running')
        release.set()
        session=self.engine.sessions['app-'+str(task.id)]
        self.assertTrue(wait(lambda: session.closed))
        self.assertEqual(task.status,'cancelled')
        self.assertFalse(any(op=='submit' and name==session.name for op,name,_ in self.engine.client.calls))

    def test_other_app_cannot_steal_session(self):
        a = self.task('Calculator')
        self.assertFalse(self.call(a,'open_app',app='Keep Notes')['ok'])
        self.assertFalse(self.call(a,'set_timer',minutes=1,label='test')['ok'])
        self.assertFalse(self.call(a,'search_places',query='test')['ok'])
        self.assertFalse(any(op=='start' for op,_ in self.controller.calls))

    def test_unreadable_observation_cannot_complete_task_successfully(self):
        a = self.task('Calculator')
        self.assertFalse(self.call(a,'observe')['ok'])
        self.engine._event({'event':'done','session':a.name,'turn':{'id':a.turn,'status':'completed'}})
        self.assertEqual(a.task.status,'failed')
        self.assertTrue(any(e['kind']=='task_finished' and e['data']['status']=='failed'
                            for e in self.store.export(self.engine.conversation)))

    def test_backend_interrupt_failure_still_stops_phone_and_records_uncertainty(self):
        a = self.task('Calculator')
        self.call(a, 'open_app', app='Calculator')
        binding = a.view.workspace.bound_id
        request = self.engine.client.request
        def failing(op, session, **p):
            if op == 'interrupt' and session == a.name:
                raise RuntimeError('synthetic connection loss')
            return request(op, session, **p)
        self.engine.client.request = failing
        self.engine.cancel(a.task.id)
        self.assertTrue(wait(lambda: a.closed))
        self.assertEqual(a.task.status, 'failed')
        self.assertTrue(any(op == 'stop' and p['sessionId'] == binding for op,p in self.controller.calls))
        self.assertTrue(any(e['kind'] == 'cancellation_uncertain' for e in self.store.export(self.engine.conversation)))

    def test_cancel_during_batch_allows_only_already_dispatched_action(self):
        a = self.task('Calculator')
        self.call(a, 'open_app', app='Calculator')
        entered, release = threading.Event(), threading.Event()
        send = a.view.workspace.send
        taps = []
        def blocking(op, p):
            if op == 'command' and p.get('action') == 'tap':
                taps.append(p)
                entered.set()
                release.wait(3)
            return send(op,p)
        a.view.workspace.send = blocking
        worker = threading.Thread(target=self.call, args=(a,'press_keys'), kwargs={'keys':'1+2='})
        worker.start()
        self.assertTrue(entered.wait(3))
        self.engine.cancel(a.task.id)
        release.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(wait(lambda: a.closed))
        self.assertEqual(len(taps),1)
        self.assertEqual(a.task.status,'cancelled')


EMULATE = object()


class ClipboardLink:
    """The paired phone for F07: a scripted reply, or Rose Agent's ClipboardWrite rules by default."""
    connected = True
    def __init__(self, reply=EMULATE):
        self.reply, self.sent = reply, []

    def call(self, action, **args):
        self.sent.append((action, dict(args)))
        if self.reply is not EMULATE:
            return self.reply
        import hashlib
        if hashlib.sha256(args['text'].encode('utf-8')).hexdigest() != args['sha256']:
            return {'status': 'text_mismatch', 'action': action, 'written': False}
        return {'status': 'copied', 'action': action, 'written': True, 'verified': 'readback',
                'characters': len(args['text']), 'sensitive': True}


class PhoneClipboard(Integration):
    """F07: copy the owner's exact text to the paired phone clipboard; ok only after the phone's confirmed write."""
    # The same vectors as workspace/rose-agent/app/tests/ClipboardWriteTest.java: UTF-8 SHA-256 on both ends.
    VECTORS = {'hello': '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824',
               '  Leading and trailing  \n': '64d2885b881530b8d94ee79bd99c8f8c088059959b92f74478da940971d4531c',
               'Caf\u00e9 \u2014 \u6771\u4eac \U0001f44b\U0001f3fd': 'c2aee1934bea3aadedcd7e062ea6bd7d3da53fe178c5b476b17cb4749adb08fe',
               'line1\r\nline2\ttab': '9482e91e3d68743aa2719aaf71d6964cb7c22e25ae9b8ce61c6e86e298b5c3d9'}

    def copy(self, text, reply=EMULATE):
        self.phone.link = link = ClipboardLink(reply)
        return self.call(self.engine.sessions['conversation'], 'copy_to_phone_clipboard', text=text), link

    def test_exact_text_is_sent_with_its_digest_and_copied_only_on_the_phones_confirmation(self):
        for text, digest in self.VECTORS.items():
            with self.subTest(text=text):
                result, link = self.copy(text)
                self.assertEqual(link.sent, [('clipboard_write', {'text': text, 'sha256': digest})])
                self.assertEqual(result, {'ok': True, 'status': 'copied', 'verified': 'readback',
                                          'message': 'Copied to the phone clipboard.', 'characters': len(text)})
                self.assertTrue(self.engine.client.results[-1]['result']['success'])
        # Neither the model's tool result nor the transcript keeps a second copy of the text.
        events = self.store.export(self.engine.conversation)
        calls = [e['data'] for e in events if e['kind'] == 'tool_call' and e['data']['name'] == 'copy_to_phone_clipboard']
        self.assertEqual([c['arguments'] for c in calls], [{'text_characters': len(t)} for t in self.VECTORS])
        dumped = json.dumps([e['data'] for e in events if e['kind'] in ('tool_call', 'tool_result')])
        for text, digest in self.VECTORS.items():
            self.assertNotIn(json.dumps(text)[1:-1], dumped)
            self.assertNotIn(digest, dumped)

    def test_only_the_phones_own_written_copy_is_success(self):
        cases = {
            'clipboard_write_denied': ({'status': 'clipboard_write_denied', 'action': 'clipboard_write', 'written': False}, False),
            'text_mismatch': ({'status': 'text_mismatch', 'action': 'clipboard_write', 'written': False}, False),
            'clipboard_wrong_device': ({'status': 'clipboard_wrong_device', 'action': 'clipboard_write', 'written': False}, False),
            'clipboard_unavailable': ({'status': 'clipboard_unavailable', 'action': 'clipboard_write', 'written': False}, False),
            'routine_busy': ({'status': 'routine_busy'}, False),
            'expired': ({'status': 'expired'}, False),
            'offline': ({'status': 'offline', 'message': 'Rose Agent is not connected.'}, False),
            'clipboard_unsupported': ({'status': 'error', 'message': 'IllegalArgumentException: Unsupported phone action'}, False),
            'clipboard_replaced': ({'status': 'clipboard_replaced', 'action': 'clipboard_write'}, 'unconfirmed'),
            'clipboard_write_unconfirmed': ({'status': 'clipboard_write_unconfirmed', 'action': 'clipboard_write'}, 'unconfirmed'),
            'timeout': ({'status': 'timeout', 'message': 'The phone did not answer clipboard_write in time.'}, 'unconfirmed'),
            'running': ({'status': 'running'}, 'unconfirmed'),
        }
        for status, (reply, copied) in cases.items():
            with self.subTest(status=status):
                result, _ = self.copy('hello', reply)
                self.assertEqual((result['ok'], result['status'], result['copied']), (False, status, copied))
                self.assertTrue(result['message'])
                self.assertFalse(self.engine.client.results[-1]['result']['success'])
        # Shapes that are not the phone's confirmed write are never success, whatever their status says.
        for reply in ({'status': 'action_accepted'}, {'status': 'copied'}, {'status': 'copied', 'written': True},
                      {'status': 'copied', 'written': 'yes', 'action': 'clipboard_write'},
                      {'status': 'clipboard_write_failed', 'reason': 'DeadSystemException'}, {}, None, 'copied'):
            with self.subTest(reply=reply):
                result, _ = self.copy('hello', reply)
                self.assertEqual((result['ok'], result['status'], result['copied']), (False, 'clipboard_write_failed', 'unconfirmed'))

    def test_text_the_phone_cannot_take_exactly_is_refused_before_anything_is_sent(self):
        from rose.clipboard import MAX_BYTES
        for text, status in (('', 'empty_text'), ('a\ud83d', 'malformed_text'), ('\udc4b', 'malformed_text'),
                             ('x' * (MAX_BYTES + 1), 'text_too_large'), ('\u00e9' * (MAX_BYTES // 2 + 1), 'text_too_large')):
            with self.subTest(status=status, size=len(text)):
                result, link = self.copy(text)
                self.assertEqual((result['ok'], result['status'], result['copied']), (False, status, False))
                self.assertEqual(link.sent, [])
        result, link = self.copy('x' * MAX_BYTES)
        self.assertTrue(result['ok'])
        self.assertEqual(len(link.sent), 1)
        # Not a string: refused, nothing sent.
        self.phone.link = link = ClipboardLink()
        result = self.call(self.engine.sessions['conversation'], 'copy_to_phone_clipboard', text=7)
        self.assertEqual((result['ok'], result['status']), (False, 'empty_text'))
        self.assertEqual(link.sent, [])

    def test_a_stale_or_cancelled_turn_never_writes(self):
        conversation = self.engine.sessions['conversation']
        self.phone.link = link = ClipboardLink()
        e = {'callId': 'ipc-stale', 'params': {'tool': 'copy_to_phone_clipboard', 'arguments': {'text': 'hello'},
                                                'turnId': 'not-the-current-turn', 'callId': 'tool-stale'}}
        conversation.turn = 'current-turn'
        self.engine._tool(conversation, e)
        self.assertEqual(link.sent, [])
        self.assertFalse(self.engine.client.results[-1]['result']['success'])

    def copy_call(self, call_id, turn='turn-1'):
        return {'callId': call_id, 'params': {'tool': 'copy_to_phone_clipboard', 'arguments': {'text': 'hello'},
                                               'turnId': turn, 'callId': 'tool-' + call_id}}

    def submitted(self):
        return [c[2]['text'] for c in self.engine.client.calls if c[0] == 'submit' and c[1] == 'conversation']

    def test_a_copy_the_bridge_answered_as_cancelled_is_reported_next_turn(self):
        # Either order: the bridge's report before or after the phone's answer.
        for report_first in (False, True):
            with self.subTest(report_first=report_first):
                engine, conversation = self.engine, self.engine.sessions['conversation']
                turn = 'turn-late-%d' % report_first  # each subtest's own turn: an interrupted one is stale
                conversation.turn, conversation.busy = turn, False
                self.phone.link = link = ClipboardLink()
                call = 'ipc-late-%d' % report_first
                if report_first:
                    engine._event({'event': 'tool_cancelled', 'callId': call})
                engine._tool(conversation, self.copy_call(call, turn))
                if not report_first:
                    self.assertEqual(engine._late_notes, [])  # not yet known to be lost
                    engine._event({'event': 'tool_cancelled', 'callId': call})
                self.assertEqual(len(link.sent), 1)
                self.assertEqual(len(engine._late_notes), 1)
                self.assertEqual(engine._late_notes[0][0], engine.conversation)
                self.assertIn('Copied to the phone clipboard.', engine._late_notes[0][1])
                self.assertNotIn('hello', engine._late_notes[0][1])
                engine._user_turn('Did it work?')
                text = self.submitted()[-1]
                self.assertTrue(text.startswith('[Before this message, your copy_to_phone_clipboard call that was answered as cancelled finished'))
                self.assertTrue(text.endswith('Did it work?'))
                self.assertEqual(engine._late_notes, [])
                user = [e['data']['text'] for e in self.store.export(engine.conversation) if e['kind'] == 'user_message']
                self.assertEqual(user[-1], 'Did it work?')  # the transcript keeps the owner's words only

    def test_a_result_the_bridge_accepted_is_never_a_late_note(self):
        # The reverse race: Python saw the interruption, but the bridge took the real result first.
        engine, conversation = self.engine, self.engine.sessions['conversation']
        conversation.turn = 'turn-accepted'
        class InterruptedLink(ClipboardLink):
            def call(self, action, **args):
                conversation.cancelled.set()  # the interruption lands while the phone writes, after the guard
                return super().call(action, **args)
        self.phone.link = link = InterruptedLink()
        engine._tool(conversation, self.copy_call('ipc-accepted', 'turn-accepted'))
        self.assertEqual(len(link.sent), 1)  # the copy ran
        self.assertIn('ipc-accepted', engine._copy_outcomes)  # held until the bridge says it was lost
        self.assertEqual(engine._late_notes, [])
        conversation.cancelled.clear()
        engine._user_turn('next')  # no tool_cancelled: the bridge accepted the real result
        self.assertEqual(self.submitted()[-1], 'next')
        self.assertEqual(engine._late_notes, [])

    def test_a_barge_in_during_a_copy_learns_its_outcome_on_the_next_submit(self):
        engine, conversation = self.engine, self.engine.sessions['conversation']
        conversation.turn = 'turn-1'
        writing, release = threading.Event(), threading.Event()
        class SlowLink(ClipboardLink):
            def call(self, action, **args):
                writing.set()
                release.wait(5)  # the phone is still writing when the owner speaks
                return super().call(action, **args)
        self.phone.link = SlowLink()
        copy = threading.Thread(target=engine._tool, args=(conversation, self.copy_call('ipc-slow')))
        copy.start()
        self.assertTrue(writing.wait(2))
        started = time.monotonic()
        engine._user_turn('Did it work?')  # submitted at once, never held for the copy
        engine._event({'event': 'tool_cancelled', 'callId': 'ipc-slow'})  # the bridge failed the pending call
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.submitted()[-1], 'Did it work?')
        release.set()
        copy.join(5)
        self.assertEqual(len(engine._late_notes), 1)
        engine.sessions['conversation'].busy = False
        engine._user_turn('And now?')
        text = self.submitted()[-1]
        self.assertTrue(text.startswith('[Before this message, your copy_to_phone_clipboard call that was answered as cancelled finished: Copied'))
        self.assertTrue(text.endswith('And now?'))
        self.assertEqual(engine._late_notes, [])

    def test_a_reset_drops_notes_of_the_old_conversation(self):
        engine = self.engine
        old = engine.sessions['conversation']
        old.turn = 'turn-1'
        class ResettingLink(ClipboardLink):
            def call(self, action, **args):
                engine.reset()  # the owner resets while the phone writes
                engine._event({'event': 'tool_cancelled', 'callId': 'ipc-reset'})
                return super().call(action, **args)
        self.phone.link = ResettingLink()
        engine._tool(old, self.copy_call('ipc-reset'))
        self.assertEqual(engine._late_notes, [])
        engine._user_turn('Hi')
        self.assertEqual(self.submitted()[-1], 'Hi')

    def test_notes_survive_a_failed_submit(self):
        engine = self.engine
        conversation = engine.sessions['conversation']
        engine._late_notes.append((engine.conversation, '[note]'))
        request = engine.client.request
        def failing(op, session, **p):
            if op == 'submit':
                raise RuntimeError('bridge busy')
            return request(op, session, **p)
        engine.client.request = failing
        with self.assertRaises(RuntimeError):
            engine._submit(conversation, 'first')
        self.assertEqual(engine._late_notes, [(engine.conversation, '[note]')])
        engine.client.request = request
        conversation.busy = False
        engine._submit(conversation, 'second')
        self.assertEqual(self.submitted()[-1], '[note]\nsecond')
        self.assertEqual(engine._late_notes, [])

    def test_conversation_tool_and_policy_but_not_an_app_thread_tool(self):
        from rose.codex_conductor import CONVERSATION_TOOLS, CONVERSATION_POLICY
        tool = next(t for t in CONVERSATION_TOOLS if t['name'] == 'copy_to_phone_clipboard')
        self.assertEqual(tool['inputSchema']['required'], ['text'])
        self.assertIn('Only when the owner asks', tool['description'])
        self.assertIn('Never copy on your own initiative', CONVERSATION_POLICY)
        a = self.task('Calculator')
        opened = [c for c in self.engine.client.calls if c[0] == 'open' and c[1] == a.name]
        self.assertTrue(opened)
        self.assertNotIn('copy_to_phone_clipboard', [t['name'] for t in opened[-1][2]['tools']])
        self.phone.link = link = ClipboardLink()
        result = self.call(a, 'copy_to_phone_clipboard', text='hello')
        self.assertFalse(result['ok'])
        self.assertEqual(link.sent, [])

    def test_simulated_phone_has_the_same_contract(self):
        from rose.phone import Phone
        fake = Phone()
        for text in self.VECTORS:
            result = fake.copy_to_clipboard(text)
            self.assertEqual((result['ok'], result['status']), (True, 'copied'))
            self.assertEqual(fake.clipboard, text)
        self.assertEqual(fake.copy_to_clipboard('a\ud83d')['status'], 'malformed_text')
        self.assertEqual(fake.clipboard, list(self.VECTORS)[-1])


class StorageAndContract(unittest.TestCase):
    def test_events_connection_restores_current_tasks_without_replaying_actions(self):
        from fastapi import FastAPI, WebSocket
        from fastapi.testclient import TestClient
        from rose.app import Server
        task = {'id': 1, 'kind': 'phone', 'status': 'running', 'goal': 'Synthetic task',
                'started': 1, 'detail': 'Checking app.', 'server': 'synthetic', 'contract': 1}
        bus = Bus()
        bus.emit('user_turn', id=1, text='Historical request')
        server = SimpleNamespace(bus=bus, snapshot=lambda: {'tasks': [task]}, _stale=lambda msg: False)
        app = FastAPI()
        closed = threading.Event()
        @app.websocket('/events')
        async def events(ws: WebSocket):
            try:
                await Server.serve_events(server, ws)
            finally:
                closed.set()
        with TestClient(app).websocket_connect('/events') as ws:
            self.assertEqual(ws.receive_json()['kind'], 'hello')
            replay = ws.receive_json()
            self.assertEqual(replay['kind'], 'user_turn')
            self.assertTrue(replay['replay'])
            self.assertEqual(ws.receive_json()['kind'], 'replay_done')
            restored = ws.receive_json()
            self.assertEqual(restored['kind'], 'task')
            self.assertNotIn('replay', restored)
            self.assertEqual(restored['task'], task)
            ws.close()
            self.assertTrue(closed.wait(2))

    def test_definitive_slot_refusals_keep_their_codes(self):
        for status in ('session_limit', 'app_in_other_session', 'locked', 'target_not_allowed'):
            with self.subTest(status=status):
                ws = ScopedWorkspace(lambda op,p: {'status': status}, timers=False)
                self.assertEqual(ws.call('start', {'packageName': CALC})['status'], status)
                self.assertFalse(ws.uncertain)
                self.assertIsNone(ws.bound_id)

    def test_shutdown_keeps_phone_transport_and_event_loop_alive_for_cleanup(self):
        import asyncio
        from rose.app import serve_all, STOP_SIGNALS
        async def run():
            loop = asyncio.get_running_loop()
            handlers, checks = {}, []
            class Transport:
                should_exit = force_exit = False
                def __init__(self):
                    self.started = asyncio.Event()
                    self.closed = False
                async def serve(self, sockets=None):
                    self.started.set()
                    while not self.should_exit:
                        await asyncio.sleep(.001)
                    self.closed = True
            transports = [Transport(), Transport()]
            async def phone_ack():
                checks.append(all(not s.should_exit and not s.closed for s in transports))
            def cleanup():
                asyncio.run_coroutine_threadsafe(phone_ack(), loop).result(timeout=2)
            with patch.object(loop, 'add_signal_handler', side_effect=lambda sig,fn: handlers.update({sig:fn})), \
                    patch.object(loop, 'remove_signal_handler'):
                task = asyncio.create_task(serve_all([(s,None) for s in transports], shutdown=cleanup))
                for s in transports:
                    await s.started.wait()
                handlers[STOP_SIGNALS[0]]()
                await asyncio.wait_for(task, timeout=3)
            self.assertEqual(checks, [True])
            self.assertTrue(all(s.closed for s in transports))
        asyncio.run(run())

    def test_transcript_api_is_loopback_only_and_stop_checks_conversation(self):
        from fastapi.testclient import TestClient
        from rose.app import build_app
        from rose.remote import build_remote_app
        with tempfile.TemporaryDirectory() as tmp:
            store = Transcripts(tmp)
            cid = store.new('gpt-6.1-terra')
            store.append(cid, 'conversation', 'user_message', {'text':'token=private-value hello'})
            stops = []
            server = SimpleNamespace(args=SimpleNamespace(port=8091), transcripts=store,
                                     conductor=SimpleNamespace(conversation=cid),
                                     tasks=SimpleNamespace(cancel=lambda tid: stops.append(tid) or True),
                                     serve_audio=lambda ws: None, serve_events=lambda ws: None)
            try:
                client = TestClient(build_app(server), base_url='http://127.0.0.1:8091')
                self.assertEqual(client.get('/api/transcripts').json()[0]['id'], cid)
                exported = client.get('/api/transcripts/'+cid)
                self.assertEqual(exported.status_code, 200)
                self.assertEqual(exported.json()[0]['conversation_id'], cid)
                self.assertNotIn('private-value', exported.text)
                self.assertEqual(client.get('/api/transcripts', headers={'Origin':'https://untrusted.example'}).status_code,403)
                self.assertEqual(client.get('/api/transcripts', headers={'Host':'untrusted.example'}).status_code,403)
                self.assertEqual(client.post('/api/tasks/1/stop', json={'conversation_id':'old'}).status_code,409)
                self.assertEqual(stops, [])
                self.assertTrue(client.post('/api/tasks/1/stop', json={'conversation_id':cid}).json()['ok'])
                self.assertEqual(stops,[1])
                remote = TestClient(build_remote_app(server, None))
                self.assertEqual(remote.get('/api/transcripts/'+cid).status_code,404)
                self.assertEqual(remote.post('/api/tasks/1/stop',json={'conversation_id':cid}).status_code,404)
            finally:
                store.close()

    def test_redaction_and_no_reasoning_media(self):
        data = {'text':'token=private-value Bearer abc123 sk-1234567890 <think>private chain</think> hi',
                'nested':{'authorization':'secret','image':'pixels','reasoning':'private'},
                'result':'data:image/png;base64,abcdef',
                'output':'{"api_key": "private key with spaces"} roseagent://pair?v=1&code=qrsecret'}
        saved = json.dumps(redact(data))
        for secret in ('private-value','abc123','sk-1234567890','private chain','pixels','abcdef','private key with spaces','qrsecret'):
            self.assertNotIn(secret,saved)
        self.assertIn('hi',saved)

    def test_no_capability_never_starts_or_falls_back(self):
        calls=[]
        def send(op,p):
            calls.append(op)
            return {'multiSession':False,'maxSessions':1}
        ws=ScopedWorkspace(send,timers=False)
        self.assertEqual(ws.start(CALC)['status'],'background_unavailable')
        self.assertEqual(calls,['capabilities'])

    def test_cloud_hosts_use_existing_qr_grammar(self):
        self.assertEqual(configured_hosts(['Rose.tail1.ts.net','100.90.1.2']),['rose.tail1.ts.net','100.90.1.2'])
        for host in ('https://example.com','user:password@example.com','a/b','999.1.1.1'):
            with self.assertRaises(ValueError):configured_hosts([host])


class StdioBridge(unittest.TestCase):
    def test_actual_python_node_adapter_ipc_tools_cancel_and_process_exit(self):
        from rose.codex_client import CodexClient
        import queue
        events = queue.Queue()
        root = Path(__file__).resolve().parents[4]
        fixture = root / 'workspace/rose-agent/server/test-fixtures/voice-codex.mjs'
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp)/'codex.mjs'
            fd = os.open(fake, os.O_WRONLY | os.O_CREAT, 0o700)
            with os.fdopen(fd,'w') as f:
                f.write(fixture.read_text())
            with patch.dict(os.environ, {'PIXEL_CODEX_BIN': str(fake)}):
                client = CodexClient(events.put)
            try:
                for name in ('a','b'):
                    opened=client.request('open',name,cwd=tmp,model='gpt-6.1-terra',instructions='fixture',
                                          tools=[{'name':'observe','description':'fixture','inputSchema':{'type':'object'}}])
                    self.assertEqual(opened['model'],'gpt-6.1-terra')
                    self.assertEqual(opened['serviceTier'],'fast')
                    self.assertEqual(opened['authMode'],'chatgpt')
                client.request('submit','a',text='tool')
                tool_event=None
                while not tool_event:
                    e=events.get(timeout=5)
                    if e['event']=='tool':tool_event=e
                self.assertEqual(tool_event['threadId'], tool_event['params']['threadId'])
                client.send({'op':'tool_result','callId':tool_event['callId'],
                             'result':{'success':True,'contentItems':[{'type':'inputText','text':'ok'}]}})
                received=[]
                while not any(e['event']=='done' for e in received):received.append(events.get(timeout=5))
                self.assertNotIn('tool_cancelled',[e['event'] for e in received])  # an accepted result is never reported lost
                self.assertNotIn('PRIVATE_REASONING_FIXTURE',json.dumps(received))
                self.assertTrue(any(e['event']=='delta' for e in received))
                # An interrupt answers a pending call fail() and reports which call, so rose.app can tell the model
                # later that a phone action still finished (F07 late outcome).
                client.request('submit','a',text='tool')
                pending=None
                while not pending:
                    e=events.get(timeout=5)
                    if e['event']=='tool':pending=e
                client.request('interrupt','a')
                reported=None
                while not reported:
                    e=events.get(timeout=5)
                    if e['event']=='tool_cancelled':reported=e
                self.assertEqual((reported['session'],reported['callId']),('a',pending['callId']))
                client.request('submit','a',text='hang')
                client.request('submit','b',text='hang')
                client.request('interrupt','a')
                client.request('submit','b',text='still running')
                client.request('close','a')
                client.request('interrupt','b')
            finally:
                client.close()
            self.assertIsNotNone(client.process.poll())


if __name__=='__main__':
    unittest.main()
