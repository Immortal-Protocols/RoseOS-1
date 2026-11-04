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


class StorageAndContract(unittest.TestCase):
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
        self.assertEqual(configured_hosts(['Rose.Example.com','100.90.1.2']),['rose.example.com','100.90.1.2'])
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
                self.assertNotIn('PRIVATE_REASONING_FIXTURE',json.dumps(received))
                self.assertTrue(any(e['event']=='delta' for e in received))
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
