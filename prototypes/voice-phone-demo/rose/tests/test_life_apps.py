"""Real conductor/allocator with synthetic Codex and two Android controllers."""
import json
from pathlib import Path
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rose.tests import test_codex as fixtures
wait=fixtures.wait
from rose.life_world import WorldLife,WorldStore,Conflict

class AppLife(unittest.TestCase):
    call=fixtures.Integration.call
    def setUp(self):
        fixtures.Integration.setUp(self)
        self.life=WorldLife(WorldStore(Path(self.tmp.name)/'life'))
        self.engine.life=self.life;self.life.bind(self.engine)
        self.life.diary.settings(enabled=True)
        self.executor=self.life.executor
    def tearDown(self):
        self.executor._run=None
        fixtures.Integration.tearDown(self)
    def run_context(self):
        job=self.life.diary.claim(manual='test')
        self.life.diary.intention(job,'Look at a relevant installed app')
        run={'job':job,'cancel':threading.Event(),'settled':False}
        self.executor._run=run
        return run
    def start(self,run,app='Calculator'):
        result=self.executor._tool(run,'app_work',{'app':app,'goal':'Read the current screen'})
        self.assertTrue(result['ok'],result)
        s=self.engine.sessions['app-'+str(result['task_id'])]
        self.assertTrue(wait(lambda:s.turn is not None))
        return s

    def test_host_bound_observation_commit_and_quiet_completion(self):
        run=self.run_context();s=self.start(run)
        self.assertEqual(s.origin,'autonomous')
        result=self.call(s,'open_app',app=s.app)
        self.assertTrue(result['ok'],result)
        ref=result['observation_id']
        observation=self.life.context()['observations'][0]
        self.assertEqual(observation['id'],ref)
        self.assertEqual(observation['session_id'],s.view.workspace.bound_id)
        self.assertEqual(observation['wake_id'],run['job']['id'])
        self.engine._event({'event':'done','session':s.name,'turn':{'id':s.turn,'status':'completed'}})
        self.assertEqual(s.task.status,'done')
        self.assertTrue(self.engine._announcements.empty())
        result=self.executor._tool(run,'settle',{'proposal':{'changes':{},'events':[
            {'kind':'digital','occurred_at':self.life.clock(),'body':'Opened Calculator; actual screen is recorded.','observations':[ref]}]}})
        self.assertEqual(result['state'],'committed')
        self.assertFalse(any(e['kind']=='rose_turn' for e in self.bus.history))

    def test_user_edit_fences_next_dispatch_and_replan_reuses_receipts(self):
        run=self.run_context();s=self.start(run)
        self.assertTrue(self.call(s,'open_app',app=s.app)['ok'])
        before=len(self.controller.calls)
        self.life.record_user('Choose a quieter café')
        result=self.call(s,'observe')
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'],'wake_context_changed_or_stopped')
        # A controller Stop watcher may poll, but no extra UI command is sent.
        self.assertFalse(any(op=='command' for op,_ in self.controller.calls[before:]))
        self.executor._tool(run,'memory',{})
        original=self.life.context()['observations'][0]['id']
        existing=self.executor._tool(run,'app_work',{'app':'Calculator','goal':'Read the current screen'})
        self.assertIn('existing_action',existing)
        self.assertEqual(len(self.life.context()['observations']),1)
        self.assertEqual(self.life.context()['observations'][0]['id'],original)

    def test_autonomous_cannot_reuse_user_worker_and_takeover_is_preserved(self):
        user=self.engine.start_phone('Calculator','User calculation')
        self.assertTrue(wait(lambda:self.engine.sessions['app-'+str(user.id)].turn is not None))
        run=self.run_context()
        result=self.executor._tool(run,'app_work',{'app':'Clock','goal':'Read clock'})
        self.assertEqual(result['reason'],'user_priority')
        self.engine.cancel(user.id);self.assertTrue(wait(lambda:self.engine.sessions['app-'+str(user.id)].closed))
        s=self.start(run,'Clock')
        self.engine._conversation_tool('update_task',{'task_id':s.task.id,'instruction':'I am taking over this task'})
        self.assertEqual(s.origin,'user');self.assertIsNone(s.wake_id)
        self.executor.cancel(run['job']['id'])
        self.assertFalse(s.cancelled.is_set())

    def test_internal_wake_is_not_a_user_message_and_can_settle_quietly(self):
        self.assertTrue(self.life.tick(manual='internal'))
        self.assertTrue(wait(lambda:bool(self.engine._wake_sinks)))
        name=next(iter(self.engine._wake_sinks))
        def emit(name_,arguments):
            count=len(self.engine.client.results)
            self.engine._event({'event':'tool','session':name,'callId':name_,
                'params':{'tool':name_,'arguments':arguments,'turnId':'internal','callId':name_}})
            self.assertTrue(wait(lambda:len(self.engine.client.results)>count))
            result=json.loads(self.engine.client.results[-1]['result']['contentItems'][0]['text'])
            self.assertTrue(result['ok'],result)
        emit('intention',{'text':'Leave an ordinary evening quiet'})
        emit('settle',{'proposal':{'changes':{},'events':[],'reason':'Nothing meaningful to add'}})
        self.engine._event({'event':'done','session':name,'turn':{'id':'internal','status':'completed'}})
        self.assertTrue(wait(lambda:self.life._job is None))
        self.assertEqual(self.life.context()['wakes'][0]['state'],'noop')
        with self.life.diary._db() as db:
            self.assertEqual(db.execute('SELECT state FROM life_cleanup').fetchone()[0],'confirmed')
        self.assertFalse(any(e['kind'] in ('user_turn','rose_turn') for e in self.bus.history))

    def test_rename_reset_and_fresh_conversation_get_committed_memory(self):
        self.life.record_user('Maya prefers tea')
        agent=self.life.context()['meta']['agent_id']
        self.engine._conversation_tool('rename_assistant',{'name':'Ember'})
        self.engine.reset();self.engine._user_turn('What does Maya prefer?')
        submit=[p for op,name,p in self.engine.client.calls if op=='submit' and name=='conversation'][-1]
        context=json.loads(submit['text'])['committed_character_context']
        self.assertEqual(context['meta']['agent_id'],agent)
        self.assertTrue(any('Maya prefers tea' in d['text'] for d in context['world']['directions']))
        self.assertEqual(self.engine.identity.name,'Ember')

    def test_recovery_stops_only_recorded_exact_pair_and_preserves_receipt(self):
        run=self.run_context();s=self.start(run)
        result=self.call(s,'open_app',app=s.app);self.assertTrue(result['ok'])
        binding=s.view.workspace.bound_id
        self.life.diary.release();self.life.diary.acquire()
        self.assertEqual(self.life.context()['pending_work'][0]['state'],'uncertain')
        self.executor.reconcile()
        stops=[p for op,p in self.controller.calls if op=='stop']
        self.assertEqual([p['sessionId'] for p in stops],[binding])
        self.assertEqual(stops[0]['taskId'],s.view.workspace.task_id)
        self.assertEqual(self.life.context()['observations'][0]['id'],result['observation_id'])

    def test_definitive_locked_refusal_prevents_repeated_inputs(self):
        run=self.run_context();s=self.start(run)
        with patch.object(self.engine,'_app_tool',return_value=({'ok':False,'action_status':'locked','result':'Phone locked'},None)) as action:
            self.assertFalse(self.call(s,'open_app',app=s.app)['ok'])
            self.assertEqual(s.blocked_reason,'locked')
            self.assertFalse(self.call(s,'observe')['ok'])
            self.assertFalse(self.call(s,'open_app',app=s.app)['ok'])
            self.assertEqual(action.call_count,1)

    def test_inflight_observation_survives_user_takeover_and_wake_finish(self):
        run=self.run_context();s=self.start(run)
        self.assertTrue(self.call(s,'open_app',app=s.app)['ok'])
        entered,release=threading.Event(),threading.Event();original=self.engine._app_tool
        def slow(*args):
            entered.set();release.wait(2);return original(*args)
        result=[]
        with patch.object(self.engine,'_app_tool',side_effect=slow):
            thread=threading.Thread(target=lambda:result.append(self.call(s,'observe')))
            thread.start();self.assertTrue(entered.wait(1))
            self.engine._conversation_tool('update_task',{'task_id':s.task.id,'instruction':'Continue this for me'})
            self.executor._run=None  # original wake may finish after ownership changes
            release.set();thread.join(3)
        self.assertTrue(result[0]['ok']);self.assertIn('observation_id',result[0])
        self.assertEqual(self.life.context()['observations'][0]['wake_id'],run['job']['id'])

    def test_stop_before_open_does_not_manufacture_uncertain_cleanup(self):
        entered,release=threading.Event(),threading.Event()
        with patch.object(self.executor,'reconcile',side_effect=lambda:(entered.set(),release.wait(2))):
            self.assertTrue(self.life.tick(manual='stop-before-open'))
            self.assertTrue(entered.wait(1));self.life.stop();release.set()
            self.assertTrue(wait(lambda:self.life._job is None))
        self.assertTrue(self.executor.available)
        self.assertFalse(any(op=='open' and name.startswith('wake-') for op,name,_ in self.engine.client.calls))

    def test_uncertain_coordinator_close_blocks_replacement_wake(self):
        original=self.engine.client.request
        def request(op,session,**kwargs):
            if op=='close' and session.startswith('wake-'):return {'stopped':False}
            return original(op,session,**kwargs)
        with patch.object(self.engine.client,'request',side_effect=request):
            self.assertTrue(self.life.tick(manual='uncertain-close'))
            self.assertTrue(wait(lambda:bool(self.engine._wake_sinks)))
            name=next(iter(self.engine._wake_sinks))
            self.engine._event({'event':'done','session':name,'turn':{'id':'test','status':'failed'}})
            self.assertTrue(wait(lambda:self.life._job is None))
        self.assertFalse(self.executor.available)
        self.assertFalse(self.life.tick(manual='must-not-overlap'))
        self.assertEqual(self.life.status()['status'],'cleanup_uncertain')

    def test_app_worker_memory_is_read_only_and_does_not_create_phone_evidence(self):
        run=self.run_context();s=self.start(run)
        result=self.call(s,'life_memory',query='')
        self.assertTrue(result['ok']);self.assertIn('Maya',result['world']['cast'])
        self.assertEqual(self.life.context()['observations'],[])
        with self.life.diary._db() as db:self.assertEqual(db.execute('SELECT count(*) FROM life_steps').fetchone()[0],0)

if __name__=='__main__':unittest.main()
