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
        self.assertEqual(result['status'], 'waiting_for_phone_executor')
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

if __name__ == '__main__': unittest.main()
