"""Synthetic cloud/phone boundary tests, no live provider or device."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest

from rose.life import Diary, Life, verified_result
from rose.life_executor import CodexResearch, PhoneResearchExecutor, ExecutionUnavailable, ResearchStopped, NOTES, CONTROLLER

JOB = {'id': 'abc123', 'interest': 'photography', 'started': 1000., 'deadline': 1120.}
CONTENT = {'summary': 'Read about shutter speed.', 'body': 'A sourced finding: https://example.com/shutter',
           'unfinished': '', 'sources': [{'url': 'https://example.com/shutter', 'title': 'example.com', 'read_at': 1000.}]}


class Research:
    def run(self, job, check, started):
        check()
        if not started():
            raise ResearchStopped()
        return dict(CONTENT)


class Phone:
    connected = True
    def __init__(self):
        self.link = self
        self.calls, self.note = [], None
        self.session = None
        self.task_id = JOB['id']
        self.capable, self.multi, self.owner_stop, self.bad_read, self.lost_write = True, True, False, False, False
        self.refused, self.lost_start, self.lost_stop = False, False, False
    def call(self, action, **kw):
        assert action == 'app_command'
        package, op, p = kw['packageName'], kw['operation'], kw['params']
        self.calls.append((package, op, dict(p)))
        if package == NOTES:
            if op == 'life_note_capabilities':
                return {'status': 'ok', 'version': 1 if self.capable else 0, 'namespace': 'rosie-life-v1',
                        'target_package': NOTES, 'workspace_binding': 'controller_session_id',
                        'immutable': True, 'owner_data_access': False}
            if op == 'life_note_put':
                self.note = dict(p, namespace='rosie-life-v1', version=1, note_id='life-'+p['task_id'],
                                 status='verified', verified=True, saved_at=1000.)
                if self.lost_write:
                    raise TimeoutError()
                return self.note
            if op == 'life_note_read':
                note = dict(self.note or {})
                if self.bad_read:
                    note['body'] = 'changed'
                return note
        assert package == CONTROLLER
        if op == 'capabilities':
            return {'schemaVersion': 4, 'multiSession': self.multi}
        if op == 'start':
            assert p == {'packageName': NOTES, 'taskId': self.task_id, 'mode': 'alongside'}
            if self.refused:
                return {'status': 'session_limit'}
            self.session = 'real-session-1'
            if self.lost_start:
                return {'status': 'uncertain'}
        if op == 'stop':
            assert p == {'sessionId': self.session, 'taskId': self.task_id}
            if self.lost_stop:
                return {'status': 'uncertain'}
            return {'status': 'stopped', 'sessionId': self.session, 'taskId': self.task_id}
        result = {'status': 'stopped' if self.owner_stop else 'ready', 'sessionId': self.session,
                  'taskId': self.task_id, 'targetPackage': NOTES, 'blocked': False, 'foregroundFallback': False}
        if op == 'get_state' and not p:
            return {'sessions': [result, {'taskId': 'other', 'sessionId': 'owner-task', 'targetPackage': 'owner.notes'}]}
        return result


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.phone = Phone()
        self.executor = PhoneResearchExecutor(self.phone, self.temp.name, research=Research(), clock=lambda: 1000., sleep=lambda _: None)
        self.cancelled = threading.Event()
    def tearDown(self):
        self.temp.cleanup()
    def run_job(self, started=lambda: True):
        return self.executor.research(dict(JOB), self.cancelled, started)
    def test_real_session_bound_note_and_verified_readback(self):
        result = self.run_job()
        self.assertIsNotNone(verified_result(result, JOB, 1000.))
        self.assertEqual(result['phone_note']['workspace_id'], 'real-session-1')
        self.assertEqual(result['phone_note']['sha256'], hashlib.sha256(CONTENT['body'].encode()).hexdigest())
        self.assertEqual([o for _, o, _ in self.phone.calls].count('life_note_put'), 1)
        self.assertEqual(self.phone.calls[-1][1], 'stop')
        self.assertIsNone(self.executor.binding(JOB['id']))
    def test_missing_note_capability_has_no_start_research_or_write(self):
        self.phone.capable = False
        with self.assertRaisesRegex(ExecutionUnavailable, 'private_notes_unavailable'):
            self.run_job()
        self.assertEqual(len(self.phone.calls), 1)
    def test_old_single_session_controller_does_not_replace_owner_task(self):
        self.phone.multi = False
        with self.assertRaisesRegex(ExecutionUnavailable, 'scoped_workspaces_unavailable'):
            self.run_job()
        self.assertFalse(any(o == 'start' for _, o, _ in self.phone.calls))
    def test_refused_slot_does_not_stop_another_task(self):
        self.phone.refused = True
        with self.assertRaisesRegex(ExecutionUnavailable, 'workspace_start_refused'):
            self.run_job()
        self.assertFalse(any(o == 'stop' for _, o, _ in self.phone.calls))
    def test_lost_start_is_reconciled_by_exact_task_never_restarted(self):
        self.phone.lost_start = True
        self.run_job()
        self.assertEqual([o for _, o, _ in self.phone.calls].count('start'), 1)
        self.assertTrue(all(p.get('sessionId') != 'owner-task' for _, _, p in self.phone.calls))
    def test_lost_write_reads_back_once_without_retry(self):
        self.phone.lost_write = True
        self.assertIsNotNone(verified_result(self.run_job(), JOB, 1000.))
        self.assertEqual([o for _, o, _ in self.phone.calls].count('life_note_put'), 1)
    def test_modified_readback_is_uncertain_and_never_success(self):
        self.phone.bad_read = True
        with self.assertRaises(ExecutionUnavailable) as caught:
            self.run_job()
        self.assertTrue(caught.exception.uncertain)
    def test_unknown_cleanup_never_claims_finished(self):
        self.phone.lost_stop = True
        with self.assertRaisesRegex(ExecutionUnavailable, 'workspace_cleanup_unconfirmed'):
            self.run_job()
    def test_owner_stop_prevents_note_write(self):
        def began():
            self.phone.owner_stop = True
            return True
        with self.assertRaisesRegex(ExecutionUnavailable, 'workspace_not_ready'):
            self.run_job(began)
        self.assertFalse(any(o == 'life_note_put' for _, o, _ in self.phone.calls))
    def test_cancel_before_start_sends_nothing(self):
        self.cancelled.set()
        with self.assertRaises(ResearchStopped):
            self.run_job()
        self.assertEqual(self.phone.calls, [])
    def test_expiry_sends_nothing(self):
        self.executor.clock = lambda: 1120.
        with self.assertRaises(ResearchStopped):
            self.run_job()
        self.assertEqual(self.phone.calls, [])
    def test_start_callback_refusal_closes_only_own_workspace(self):
        with self.assertRaises(ResearchStopped):
            self.run_job(lambda: False)
        self.assertEqual(self.phone.calls[-1][1], 'stop')
        self.assertFalse(any(o == 'life_note_put' for _, o, _ in self.phone.calls))
    def test_note_written_then_cancelled_still_reads_back_exact_write(self):
        original = self.phone.call
        def call(action, **kw):
            reply = original(action, **kw)
            if kw['operation'] == 'life_note_put': self.cancelled.set()
            return reply
        self.phone.call = call
        result = self.run_job()
        self.assertTrue(self.cancelled.is_set())
        self.assertIsNotNone(verified_result(result, JOB, 1000.))
        self.assertEqual([o for _, o, _ in self.phone.calls].count('life_note_put'), 1)
        self.assertEqual([o for _, o, _ in self.phone.calls].count('life_note_read'), 1)
    def test_uncertain_execution_disables_scheduler_across_restart(self):
        self.phone.bad_read = True
        diary = Diary(Path(self.temp.name)/'diary')
        diary.interest('photography', 1); diary.set_enabled(True)
        job = diary.claim(1000., 1, 1800, 120)
        self.executor.research = lambda *a: (_ for _ in ()).throw(ExecutionUnavailable('note_write_unconfirmed', uncertain=True))
        life = Life(diary, self.executor, clock=lambda: 1000.)
        life._job = job
        life._run(job, threading.Event())
        state = Diary(diary.directory).snapshot()
        self.assertFalse(state['enabled'])
        self.assertEqual(state['activities'][0]['reason'], 'note_write_unconfirmed')

    def test_full_scheduler_to_phone_readback_and_restart_recall(self):
        diary = Diary(Path(self.temp.name)/'diary')
        diary.interest('photography', 1); diary.set_enabled(True)
        job = diary.claim(1000., 1, 1800, 120)
        self.phone.task_id = job['id']
        life = Life(diary, self.executor, clock=lambda: 1000.)
        life._job = job
        life._run(job, threading.Event())
        recalled = Life(Diary(diary.directory)).call({'action': 'diary'})['activities'][0]
        self.assertEqual(recalled['state'], 'completed')
        self.assertEqual(recalled['result']['body'], self.phone.note['body'])
        self.assertEqual(recalled['result']['phone_note']['workspace_id'], 'real-session-1')
        self.assertTrue(recalled['result']['content_is_untrusted'])

    def test_cancelled_unconfirmed_cleanup_is_failed_not_confirmed_stopped(self):
        diary = Diary(Path(self.temp.name)/'diary')
        diary.interest('photography', 1); diary.set_enabled(True)
        job = diary.claim(1000., 1, 1800, 120)
        self.phone.task_id = job['id']; self.phone.lost_stop = True
        original = self.phone.call
        cancelled = threading.Event()
        def call(action, **kw):
            reply = original(action, **kw)
            if kw['operation'] == 'life_note_put': cancelled.set()
            return reply
        self.phone.call = call
        life = Life(diary, self.executor, clock=lambda: 1000.)
        life._job = job
        life._run(job, cancelled)
        state = diary.snapshot()
        self.assertFalse(state['enabled'])
        self.assertEqual(state['activities'][0]['state'], 'failed')
        self.assertEqual(state['activities'][0]['reason'], 'workspace_cleanup_unconfirmed')
        self.assertTrue(state['activities'][0]['result']['phone_note']['verified'])


class CloudTests(unittest.TestCase):
    def run_cloud(self, changes=None, *, close_ack=True, open_error=False):
        record = []
        class Client:
            def __init__(self, emit): self.emit = emit
            def request(self, op, session, **kwargs):
                record.append((op, session, kwargs))
                if op == 'open':
                    if open_error: raise RuntimeError('research_isolation_unavailable')
                    return {'threadId': 'research-thread', 'model': 'gpt-6.1-terra'}
                if op == 'interrupt': return {'status': 'interrupted'}
                if op == 'close': return {'stopped': True}
                events = [dict(event='started'), dict(event='research_source', source={
                    'url': 'https://example.com/shutter', 'evidence': 'completed_public_page_open'}),
                    dict(event='message', phase='final_answer', text=json.dumps({k: v for k, v in CONTENT.items() if k != 'sources'})),
                    dict(event='done', turn={'status': 'completed'})]
                if changes: events = changes(events)
                for event in events: self.emit(dict(session=session, threadId='research-thread', **event))
                return {'turn': {'id': 'turn'}}
            def close(self): record.append(('closed',)); return close_ack
        with tempfile.TemporaryDirectory() as directory:
            researcher = CodexResearch(directory, client_factory=Client, clock=lambda: 1000.)
            try: return researcher.run(JOB, lambda: None, lambda: True), record
            finally: self.assertEqual(record[-1][0], 'closed')
    def test_restricted_route_with_observed_sources_and_no_owner_context(self):
        result, calls = self.run_cloud()
        self.assertEqual(calls[0][2]['purpose'], 'public_research')
        self.assertEqual(calls[0][2]['tools'], [])
        self.assertEqual(result['sources'][0]['read_at'], 1000.)
        self.assertIn('Observed public sources', result['body'])
    def test_model_citation_without_source_event_is_not_receipt(self):
        with self.assertRaisesRegex(ExecutionUnavailable, 'research_unverified'):
            self.run_cloud(lambda es: [e for e in es if e['event'] != 'research_source'])
    def test_model_supplied_receipts_rejected(self):
        def changed(es):
            es[2]['text'] = json.dumps(dict(CONTENT, phone_note={'verified': True}))
            return es
        with self.assertRaisesRegex(ExecutionUnavailable, 'research_unverified'):
            self.run_cloud(changed)
    def test_failure_not_claimed_complete(self):
        def changed(es): es[-1]['turn']['status'] = 'failed'; return es
        with self.assertRaises(ExecutionUnavailable): self.run_cloud(changed)

    def test_bridge_force_exit_or_startup_cleanup_unknown_is_uncertain(self):
        for open_error in (False, True):
            with self.assertRaisesRegex(ExecutionUnavailable, 'research_stop_unconfirmed') as caught:
                self.run_cloud(close_ack=False, open_error=open_error)
            self.assertTrue(caught.exception.uncertain)

    def test_isolation_failure_with_confirmed_cleanup_keeps_precise_reason(self):
        with self.assertRaisesRegex(ExecutionUnavailable, 'research_isolation_unavailable') as caught:
            self.run_cloud(open_error=True)
        self.assertFalse(caught.exception.uncertain)


if __name__ == '__main__': unittest.main()
