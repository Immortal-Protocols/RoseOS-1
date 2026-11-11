"""Production conversation/task adapter with synthetic Codex and no live phone."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from rose.tests.test_codex import FakeCodex, wait
from rose.bus import Bus
from rose.codex_conductor import CodexConductor, Session
from rose.life import Diary, Life
from rose.tasks import Task
from rose.transcripts import Transcripts


class LifeIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Transcripts(Path(self.temp.name) / 'transcripts')
        self.life = Life(Diary(Path(self.temp.name) / 'rosie-life'))
        self.bus = Bus()
        self.engine = CodexConductor(SimpleNamespace(connected=True), self.bus, None, self.store,
                                     client_factory=FakeCodex, life=self.life)
        self.engine.start()
    def tearDown(self):
        self.engine.stop(); self.store.close(); self.temp.cleanup()

    def test_conversation_tools_save_and_recall_without_claiming_execution(self):
        for args in ({'action': 'interest', 'interest': 'photography'}, {'action': 'resume'}):
            result, image = self.engine._conversation_tool('rosie_life', args)
            self.assertTrue(result['ok']); self.assertIsNone(image)
        result, _ = self.engine._conversation_tool('rosie_life', {'action': 'diary'})
        self.assertEqual(result['interests'], ['photography'])
        self.assertEqual(result['status'], 'waiting_for_executor')
        self.assertEqual(result['activities'], [])
        self.assertFalse(any(e['kind'] == 'rose_turn' for e in self.bus.history))

    def test_owner_turn_prioritizes_user_before_submit(self):
        calls = []
        self.life.user_active = lambda: calls.append('user_priority')
        original = self.engine.client.request
        def request(op, *args, **kwargs):
            if op == 'submit': calls.append('submit')
            return original(op, *args, **kwargs)
        self.engine.client.request = request
        self.engine._user_turn('What have you been doing?')
        self.assertEqual(calls[:2], ['user_priority', 'submit'])

    def test_receipt_needs_busy_bound_turn_not_open_workspace(self):
        task = Task(1, 'phone', 'Read about exposure')
        task.background = True
        session = Session('synthetic-app', task=task, conversation=self.engine.conversation)
        self.engine.sessions[session.name] = session
        try:
            self.assertFalse(self.engine.task_snapshot(task)['activity']['executing'])
            session.busy = True
            self.assertFalse(self.engine.task_snapshot(task)['activity']['executing'])
            session.turn = 'turn-1'
            self.assertTrue(self.engine.task_snapshot(task)['activity']['executing'])
            session.busy = False
            self.assertFalse(self.engine.task_snapshot(task)['activity']['executing'])
            session.busy = True; task.status = 'done'
            self.assertFalse(self.engine.task_snapshot(task)['activity']['executing'])
        finally:
            del self.engine.sessions[session.name]

    def test_heartbeat_does_not_publish_idle_app(self):
        task = Task(1, 'phone', 'Read about exposure'); task.background = True
        session = Session('synthetic-app', task=task, conversation=self.engine.conversation)
        self.engine.sessions[session.name] = session
        try:
            publish = self.engine._task_event = Mock()
            self.engine._activity_heartbeat_at = 0
            self.engine._activity_heartbeat(); publish.assert_not_called()
            session.busy = True; session.turn = 'turn-1'
            self.engine._activity_heartbeat_at = 0
            self.engine._activity_heartbeat(); publish.assert_called_once_with(session)
            self.engine._activity_heartbeat(); publish.assert_called_once()
        finally:
            del self.engine.sessions[session.name]

    def test_life_projects_real_binding_and_existing_targeted_stop(self):
        self.life.diary.interest('photography', 1)
        self.life.diary.set_enabled(True)
        job = self.life.diary.claim(1000, 1, 1800, 120)
        binding = None
        executing = [False]
        self.life.executor = SimpleNamespace(binding=lambda jid: binding if jid == job['id'] else None,
                                              executing=lambda jid: executing[0])
        self.engine._life_event({'task_id': job['id'], 'state': 'queued'})
        tid = next(iter(self.engine._life_tasks))
        task = self.engine.tasks[tid]
        self.assertFalse(self.engine.task_snapshot(task)['activity']['executing'])
        self.assertEqual(self.engine.task_snapshot(task)['workspace'], binding)
        self.life.diary.transition(job['id'], ('queued',), 'running', 1001)
        executing[0] = True
        self.engine._life_event({'task_id': job['id'], 'state': 'running'})
        self.assertTrue(self.engine.task_snapshot(task)['activity']['executing'])
        self.life.stop = Mock(return_value=True)
        self.assertTrue(self.engine.cancel(tid))
        self.life.stop.assert_called_once_with(job['id'], reason='owner_stopped')
        self.assertFalse(self.engine.cancel(tid + 1000))
        self.life.diary.transition(job['id'], ('running',), 'completed', 1002, result={'summary': 'A real source summary.'})
        self.engine._life_event({'task_id': job['id'], 'state': 'settled'})
        self.assertEqual(task.status, 'done')
        self.assertEqual(task.result, 'A real source summary.')
        self.assertFalse(self.engine.task_snapshot(task)['activity']['executing'])
        self.assertFalse(any(e['kind'] == 'rose_turn' for e in self.bus.history))

if __name__ == '__main__': unittest.main()
