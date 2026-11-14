"""Project 2 workspace task handoff v1 (docs/APP-BUILDER.md §4.3): only a confirmed, well-formed page task starts,
exactly once per request; the route exists only when enabled and only for signed requests from a paired device."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from rose.remote import Remote, build_remote_app
from rose.tests.test_builder_routes import Device, server
from rose.workspace_tasks import Unavailable, WorkspaceTasks, check

GOOD = {'schemaVersion': 1, 'requestId': 'r1', 'actionId': 'task:grocery', 'label': 'Add missing items',
        'goal': 'Compare the recipe with my Notes list and add what is missing.', 'confirmed': True}


class Starter:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, label, goal, request_id):
        if self.fail:
            raise Unavailable()
        self.calls.append((label, goal, request_id))
        return f'task-{len(self.calls)}'


class Handoff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.start = Starter()
        self.tasks = WorkspaceTasks(self.root / 'ws', self.start)

    def tearDown(self):
        self.tasks.close()
        self.tmp.cleanup()

    def test_refusals_start_nothing(self):
        for bad in (None, [], {**GOOD, 'schemaVersion': 2}, {**GOOD, 'schemaVersion': True},
                    {**GOOD, 'confirmed': False}, {k: v for k, v in GOOD.items() if k != 'confirmed'},
                    {**GOOD, 'actionId': 'app:com.x/.Main'}, {**GOOD, 'actionId': 'task:'}, {**GOOD, 'label': ''},
                    {**GOOD, 'label': 'x' * 61}, {**GOOD, 'goal': 'x' * 301}, {**GOOD, 'goal': 'a\ud800'},
                    {**GOOD, 'extra': 1}, {**GOOD, 'requestId': 'x' * 65}):
            self.assertIsNotNone(check(bad), bad)
            self.assertEqual(self.tasks.submit('d', bad)['status'], 'refused', bad)
        self.assertEqual(self.start.calls, [])

    def test_accepted_once_per_request_even_across_a_restart(self):
        first = self.tasks.submit('d', GOOD)
        self.assertEqual(first, {'schemaVersion': 1, 'status': 'accepted', 'taskId': 'task-1'})
        self.assertEqual(self.start.calls, [('Add missing items', GOOD['goal'], 'r1')])
        again = self.tasks.submit('d', GOOD)
        self.assertEqual((again['status'], again['taskId']), ('duplicate', 'task-1'))
        self.tasks.close()
        self.tasks = WorkspaceTasks(self.root / 'ws', self.start)
        self.assertEqual(self.tasks.submit('d', GOOD)['status'], 'duplicate')
        self.assertEqual(len(self.start.calls), 1)
        self.assertEqual(self.tasks.submit('other-device', GOOD)['status'], 'accepted')  # another device's request
        self.assertEqual(((self.root / 'ws' / 'workspace-tasks.sqlite3').stat().st_mode & 0o777), 0o600)

    def test_unavailable_starts_nothing_and_can_be_retried(self):
        self.tasks.start = Starter(fail=True)
        self.assertEqual(self.tasks.submit('d', GOOD), {'schemaVersion': 1, 'status': 'unavailable'})
        self.tasks.start = self.start
        self.assertEqual(self.tasks.submit('d', GOOD)['status'], 'accepted')


class ConversationStarter(unittest.TestCase):
    def setUp(self):
        from rose.tests import test_codex as fixtures
        self.fixtures = fixtures
        self.tmp = tempfile.TemporaryDirectory()
        self.store = fixtures.Transcripts(Path(self.tmp.name) / 't')
        self.engine = fixtures.CodexConductor(fixtures.RealPhone(), fixtures.Bus(), None, self.store,
                                              client_factory=fixtures.FakeCodex)
        self.engine.start()

    def tearDown(self):
        self.engine.stop()
        self.store.close()
        self.tmp.cleanup()

    def test_a_confirmed_task_becomes_the_owners_request(self):
        from rose.workspace_tasks import PREFIX, conversation_starter
        start = conversation_starter(self.engine)
        self.assertIsNone(start('Add missing items', 'Compare the recipe with my Notes list.', 'r1'))
        s = self.engine.sessions['conversation']
        self.assertTrue(self.fixtures.wait(lambda: any(
            op == 'submit' and PREFIX + 'Add missing items. Compare the recipe with my Notes list.' in p.get('text', '')
            for op, _, p in self.engine.client.calls)))
        self.assertTrue(self.fixtures.wait(lambda: s.turn is not None or s.busy))

    def test_busy_or_stopped_is_unavailable(self):
        from rose.workspace_tasks import conversation_starter
        start = conversation_starter(self.engine)
        s = self.engine.sessions['conversation']
        s.busy = True
        with self.assertRaises(Unavailable):
            start('x', 'y', 'r')
        s.busy = False
        self.engine.stop()
        with self.assertRaises(Unavailable):
            start('x', 'y', 'r')
        self.assertFalse(any(op == 'submit' for op, _, p in self.engine.client.calls))


class Route(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / 'remote').mkdir(mode=0o700)
        self.remote = Remote(directory=root / 'remote', name='test', hosts=lambda: [])
        self.device = Device()
        self.remote.devices.enroll(self.device.spki, 'phone', 'Pixel', '100.64.0.2')
        self.start = Starter()
        self.tasks = WorkspaceTasks(root / 'ws', self.start)
        self.addCleanup(self.tasks.close)
        self.client = TestClient(build_remote_app(server(workspace_tasks=self.tasks), self.remote))

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, body, signed=True):
        h = self.device.headers('POST', '/workspace/v1/tasks', self.remote.identity.pin) if signed else {}
        return self.client.post('/workspace/v1/tasks', content=json.dumps(body), headers=h)

    def test_not_mounted_without_workspace_tasks(self):
        bare = TestClient(build_remote_app(server(), self.remote))
        self.assertEqual(bare.post('/workspace/v1/tasks', json=GOOD).status_code, 404)

    def test_signed_confirmed_request_starts_one_task(self):
        r = self.post(GOOD)
        self.assertEqual((r.status_code, r.json()['status'], r.json()['taskId']), (200, 'accepted', 'task-1'))
        self.assertEqual(self.post(GOOD).json()['status'], 'duplicate')
        self.assertEqual(len(self.start.calls), 1)

    def test_unsigned_or_unconfirmed_is_refused(self):
        r = self.post(GOOD, signed=False)
        self.assertEqual((r.status_code, r.json()['schemaVersion']), (401, 1))
        r = self.post({**GOOD, 'requestId': 'r2', 'confirmed': False})
        self.assertEqual((r.status_code, r.json()['status']), (400, 'refused'))
        h = self.device.headers('POST', '/workspace/v1/tasks', self.remote.identity.pin)
        self.assertEqual(self.client.post('/workspace/v1/tasks', content=b'[' * 3000, headers=h).status_code, 400)
        self.assertEqual(self.start.calls, [])


if __name__ == '__main__':
    unittest.main()
