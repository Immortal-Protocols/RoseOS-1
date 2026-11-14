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
import re

from rose.workspace_tasks import Unavailable, WorkspaceTasks, check, connect

GOOD = {'schemaVersion': 1, 'requestId': 'r1', 'actionId': 'task:grocery', 'label': 'Add missing items',
        'goal': 'Compare the recipe with my Notes list and add what is missing.', 'confirmed': True}


class Starter:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, label, goal, request_id, task_id):
        if self.fail:
            raise Unavailable()
        self.calls.append((label, goal, request_id, task_id))


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

    def test_accepted_once_per_request_with_a_stable_task_id_even_across_a_restart(self):
        first = self.tasks.submit('d', GOOD)
        self.assertEqual((first['schemaVersion'], first['status']), (1, 'accepted'))
        self.assertRegex(first['taskId'], r'^wt-[0-9a-f]{16}$')
        self.assertEqual(self.start.calls, [('Add missing items', GOOD['goal'], 'r1', first['taskId'])])
        again = self.tasks.submit('d', GOOD)
        self.assertEqual((again['status'], again['taskId']), ('duplicate', first['taskId']))
        self.tasks.close()
        self.tasks = WorkspaceTasks(self.root / 'ws', self.start)
        self.assertEqual(self.tasks.submit('d', GOOD)['taskId'], first['taskId'])
        self.assertEqual(len(self.start.calls), 1)
        self.assertEqual(self.tasks.submit('other-device', GOOD)['status'], 'accepted')  # another device's request
        self.assertEqual(((self.root / 'ws' / 'workspace-tasks.sqlite3').stat().st_mode & 0o777), 0o600)

    def test_unavailable_starts_nothing_records_nothing_and_can_be_retried(self):
        self.tasks.start = Starter(fail=True)
        self.assertEqual(self.tasks.submit('d', GOOD), {'schemaVersion': 1, 'status': 'unavailable'})
        self.assertEqual(self.tasks.recent(), [])
        self.tasks.start = self.start
        self.assertEqual(self.tasks.submit('d', GOOD)['status'], 'accepted')


class States(unittest.TestCase):
    """The page task's state from its turn and its phone tasks; acceptance is never completion."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.events = []
        self.tasks = WorkspaceTasks(Path(self.tmp.name), Starter(), emit=lambda task, device: self.events.append(task))
        self.id = self.tasks.submit('d', GOOD)['taskId']

    def tearDown(self):
        self.tasks.close()
        self.tmp.cleanup()

    def state(self):
        return self.tasks.get(self.id)['state']

    def test_acceptance_is_running_never_done(self):
        t = self.tasks.get(self.id)
        self.assertEqual((t['state'], t['requestId'], t['actionId'], t['phoneTasks']), ('running', 'r1', 'task:grocery', []))
        self.assertEqual([e['state'] for e in self.events], ['running'])

    def test_a_turn_that_starts_no_phone_work_is_done_when_it_completes(self):
        self.tasks.turn_ended(self.id, 'completed')
        self.assertEqual(self.state(), 'done')

    def test_phone_tasks_decide_after_the_turn(self):
        self.tasks.attach(self.id, 7)
        self.tasks.turn_ended(self.id, 'completed')
        self.assertEqual(self.state(), 'running')  # the turn ended, its phone task has not
        self.tasks.phone_task(7, 'done')
        t = self.tasks.get(self.id)
        self.assertEqual((t['state'], t['phoneTasks']), ('done', [{'id': 7, 'status': 'done'}]))

    def test_failed_and_cancelled(self):
        for setup, expected in (([('attach', 1), ('phone', 1, 'failed'), ('turn', 'completed')], 'failed'),
                                ([('attach', 1), ('phone', 1, 'cancelled'), ('turn', 'completed')], 'cancelled'),
                                ([('turn', 'interrupted')], 'cancelled'), ([('turn', 'failed')], 'failed'),
                                ([('attach', 1), ('attach', 2), ('phone', 1, 'cancelled'), ('phone', 2, 'failed'),
                                  ('turn', 'completed')], 'failed')):
            with self.subTest(setup=setup):
                tid = self.tasks.submit('d', {**GOOD, 'requestId': 'x' + str(len(self.events))})['taskId']
                for step in setup:
                    if step[0] == 'attach':
                        self.tasks.attach(tid, step[1])
                    elif step[0] == 'phone':
                        self.tasks.phone_task(step[1], step[2])
                    else:
                        self.tasks.turn_ended(tid, step[1])
                self.assertEqual(self.tasks.get(tid)['state'], expected)

    def test_a_final_state_never_changes_and_unrelated_tasks_are_ignored(self):
        self.tasks.turn_ended(self.id, 'completed')
        self.tasks.turn_ended(self.id, 'failed')
        self.tasks.attach(self.id, 3)
        self.tasks.phone_task(3, 'failed')
        self.assertEqual((self.state(), self.tasks.get(self.id)['phoneTasks']), ('done', []))
        other = self.tasks.submit('d', {**GOOD, 'requestId': 'r2'})['taskId']
        self.tasks.phone_task(99, 'failed')  # never attached to anything
        self.assertEqual(self.tasks.get(other)['state'], 'running')

    def test_a_call_in_flight_keeps_the_task_running_until_it_returns(self):
        self.tasks.call_started(self.id)
        self.tasks.turn_ended(self.id, 'completed')  # the turn ended while its phone_task call was still starting
        self.assertEqual(self.state(), 'running')
        self.tasks.attach(self.id, 5)
        self.tasks.call_ended(self.id)
        self.assertEqual(self.state(), 'running')  # its phone task runs
        self.tasks.phone_task(5, 'done')
        self.assertEqual(self.state(), 'done')
        other = self.tasks.submit('d', {**GOOD, 'requestId': 'r2'})['taskId']
        self.tasks.call_started(other)
        self.tasks.turn_ended(other, 'completed')
        self.tasks.call_ended(other)  # a call that started nothing
        self.assertEqual(self.tasks.get(other)['state'], 'done')
        self.assertEqual([e['state'] for e in self.events if e['taskId'] == other], ['running', 'done'])

    def test_a_failed_record_leaves_no_open_transaction(self):
        import rose.workspace_tasks as module
        real = module.new_task_id
        module.new_task_id = lambda: self.id  # an id already taken: the second INSERT fails after the first
        try:
            with self.assertRaises(Exception):
                self.tasks.submit('d', {**GOOD, 'requestId': 'r2'})
        finally:
            module.new_task_id = real
        self.assertFalse(self.tasks.db.in_transaction)
        self.assertEqual(self.tasks.submit('d', {**GOOD, 'requestId': 'r2'})['status'], 'accepted')  # not a duplicate

    def test_a_restart_makes_unfinished_tasks_uncertain_and_they_stay_so(self):
        done = self.tasks.submit('d', {**GOOD, 'requestId': 'r2'})['taskId']
        self.tasks.turn_ended(done, 'completed')
        self.tasks.close()
        events = []
        self.tasks = WorkspaceTasks(Path(self.tmp.name), Starter(), emit=lambda task, device: events.append(task))
        self.assertEqual((self.state(), self.tasks.get(done)['state']), ('uncertain', 'done'))
        self.assertEqual([e['taskId'] for e in events], [self.id])
        self.tasks.turn_ended(self.id, 'completed')  # a late report cannot turn it into done
        self.assertEqual(self.state(), 'uncertain')

    def test_updated_at_never_goes_back(self):  # amendment A
        times = iter([1000.0, 900.0, 800.0, 700.0])
        self.tasks.clock = lambda: next(times)
        before = self.tasks.get(self.id)['updatedAt']
        self.tasks.attach(self.id, 1)
        self.tasks.turn_ended(self.id, 'completed')
        self.tasks.phone_task(1, 'done')
        stamps = [e['updatedAt'] for e in self.events]
        self.assertEqual(stamps, sorted(stamps))
        self.assertGreaterEqual(self.tasks.get(self.id)['updatedAt'], before)
        self.assertEqual(self.state(), 'done')

    def test_at_most_eight_phone_tasks_shown_but_all_counted(self):  # amendment B
        for i in range(1, 11):
            self.tasks.attach(self.id, i)
        self.tasks.turn_ended(self.id, 'completed')
        for i in range(2, 11):
            self.tasks.phone_task(i, 'done')
        t = self.tasks.get(self.id)
        self.assertEqual([p['id'] for p in t['phoneTasks']], list(range(3, 11)))  # the newest eight
        self.assertEqual(t['state'], 'running')  # task 1 is still running, though not shown
        self.tasks.phone_task(1, 'done')
        self.assertEqual(self.state(), 'done')

    def test_device_scope(self):  # amendment C
        self.assertIsNotNone(self.tasks.get(self.id, 'd'))
        self.assertIsNone(self.tasks.get(self.id, 'another-device'))
        other = self.tasks.submit('e', {**GOOD, 'requestId': 'r1'})['taskId']  # same requestId, another device
        self.assertNotEqual(other, self.id)
        self.assertEqual([t['taskId'] for t in self.tasks.recent('d')], [self.id])
        self.assertEqual([t['taskId'] for t in self.tasks.recent('e')], [other])
        self.assertEqual(len(self.tasks.recent()), 2)

    def test_reads(self):
        for bad in (None, 7, 'wt-xyz', 'wt-' + 'g' * 16, 'task:grocery', 'wt-' + '0' * 16):
            self.assertIsNone(self.tasks.get(bad))
        for i in range(25):
            self.tasks.submit('d', {**GOOD, 'requestId': f'n{i}'})
        recent = self.tasks.recent()
        self.assertEqual(len(recent), 20)
        self.assertEqual(recent[-1]['requestId'], 'n24')  # oldest first, newest last
        self.assertTrue(all(set(t) == {'schemaVersion', 'taskId', 'requestId', 'actionId', 'state', 'detail',
                                       'phoneTasks', 'createdAt', 'updatedAt'} for t in recent))


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
        self.assertIsNone(start('Add missing items', 'Compare the recipe with my Notes list.', 'r1', 'wt-' + '1' * 16))
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
            start('x', 'y', 'r', 'wt-' + '2' * 16)
        s.busy = False
        self.engine.stop()
        with self.assertRaises(Unavailable):
            start('x', 'y', 'r', 'wt-' + '3' * 16)
        self.assertFalse(any(op == 'submit' for op, _, p in self.engine.client.calls))


class PageTaskFlow(unittest.TestCase):
    """The real conductor with the simulated Codex and phone (test_codex.Integration's fixtures): a page task's state
    follows its own turn and the phone tasks that turn starts, and nothing else."""

    def setUp(self):
        from rose.tests import test_codex
        self.fixtures = test_codex
        test_codex.Integration.setUp(self)
        self.addCleanup(test_codex.Integration.tearDown, self)
        self.tasks = connect(self.engine, WorkspaceTasks(Path(self.tmp.name) / 'ws', None))
        self.addCleanup(self.tasks.close)
        self.conversation = self.engine.sessions['conversation']

    call = property(lambda self: self.fixtures.Integration.call.__get__(self))

    def submit(self, request_id='r1'):
        reply = self.tasks.submit('d', {**GOOD, 'requestId': request_id})
        self.assertEqual(reply['status'], 'accepted')
        self.assertTrue(self.fixtures.wait(lambda: self.conversation.turn is not None and self.conversation.busy))
        return reply['taskId']

    def end_turn(self, status='completed'):
        self.engine._event({'event': 'done', 'session': 'conversation',
                            'turn': {'id': self.conversation.turn, 'status': status}})

    def events(self, task_id):
        return [m['task']['state'] for m in list(self.bus.history)
                if m.get('kind') == 'workspace_task' and m['task']['taskId'] == task_id]

    def test_phone_work_started_by_the_turn_decides_completion(self):
        task_id = self.submit()
        started = self.call(self.conversation, 'phone_task', app='Calculator', goal='Open Calculator')
        self.assertTrue(started['ok'])
        self.end_turn()
        self.assertEqual(self.tasks.get(task_id)['state'], 'running')  # Rose replied; the phone work goes on
        app = self.engine.sessions['app-' + str(started['task_id'])]
        self.assertTrue(self.fixtures.wait(lambda: app.turn is not None))
        self.assertTrue(self.call(app, 'open_app', app='Calculator')['ok'])
        app.text = 'Calculator is open.'
        self.engine._event({'event': 'done', 'session': app.name, 'turn': {'id': app.turn, 'status': 'completed'}})
        t = self.tasks.get(task_id)
        self.assertEqual((t['state'], t['phoneTasks']), ('done', [{'id': started['task_id'], 'status': 'done'}]))
        self.assertEqual(self.events(task_id)[0], 'running')
        self.assertEqual(self.events(task_id)[-1], 'done')
        self.assertNotIn('done', self.events(task_id)[:-1])

    def test_a_reply_only_turn_is_done_and_a_superseded_turn_is_cancelled(self):
        first = self.submit('r1')
        self.end_turn()
        self.assertEqual(self.tasks.get(first)['state'], 'done')
        second = self.submit('r2')
        self.engine.inject_text_turn('something else')  # the owner speaks before Rose finished
        self.assertTrue(self.fixtures.wait(lambda: self.tasks.get(second)['state'] != 'running'))
        self.assertEqual(self.tasks.get(second)['state'], 'cancelled')
        self.end_turn()  # the new turn's end is not the page task's
        self.assertEqual(self.tasks.get(second)['state'], 'cancelled')

    def test_a_failed_turn_fails_the_page_task(self):
        task_id = self.submit()
        self.engine._failed(self.conversation, RuntimeError('codex'))
        self.assertEqual(self.tasks.get(task_id)['state'], 'failed')

    def slow_phone_call(self):
        """A phone_task call of the page turn that is still inside start_phone (allocating a workspace)."""
        import threading
        gate, entered, out = threading.Event(), threading.Event(), {}
        real = self.engine.start_phone

        def slow(*a, **k):
            entered.set()
            gate.wait(5)
            return real(*a, **k)
        self.engine.start_phone = slow
        thread = threading.Thread(target=lambda: out.update(r=self.call(self.conversation, 'phone_task',
                                                                         app='Calculator', goal='Open Calculator')))
        thread.start()
        self.assertTrue(entered.wait(2))

        def finish():
            gate.set()
            thread.join(5)
            self.engine.start_phone = real
            return out['r']
        return finish

    def finish_phone(self, phone_id):
        app = self.engine.sessions['app-' + str(phone_id)]
        self.assertTrue(self.fixtures.wait(lambda: app.turn is not None))
        self.assertTrue(self.call(app, 'open_app', app='Calculator')['ok'])
        app.text = 'Calculator is open.'
        self.engine._event({'event': 'done', 'session': app.name, 'turn': {'id': app.turn, 'status': 'completed'}})

    def test_phone_work_whose_call_returns_after_the_turn_ended_still_counts(self):
        task_id = self.submit()
        finish = self.slow_phone_call()
        self.end_turn()  # the bridge answered the slow call as failed; the turn completed
        self.assertEqual(self.tasks.get(task_id)['state'], 'running')
        started = finish()
        t = self.tasks.get(task_id)
        self.assertEqual((t['state'], t['phoneTasks']), ('running', [{'id': started['task_id'], 'status': 'running'}]))
        self.finish_phone(started['task_id'])
        self.assertEqual(self.tasks.get(task_id)['state'], 'done')
        self.assertNotIn('done', self.events(task_id)[:-1])

    def test_phone_work_of_a_superseded_turn_stays_with_its_own_page_task(self):
        first = self.submit('r1')
        finish = self.slow_phone_call()
        self.engine._user_turn('wait, what time is it?')  # the owner speaks while the call is starting
        self.assertEqual(self.tasks.get(first)['state'], 'running')
        started = finish()
        t = self.tasks.get(first)
        self.assertEqual((t['state'], t['phoneTasks']), ('running', [{'id': started['task_id'], 'status': 'running'}]))
        self.assertIsNone(self.conversation.workspace_task)  # the spoken turn serves no page task
        self.finish_phone(started['task_id'])
        self.assertEqual(self.tasks.get(first)['state'], 'cancelled')  # its turn was cut off

    def test_a_voice_hold_or_reset_ends_the_page_task(self):
        held = self.submit('r1')
        turn = self.conversation.turn
        self.engine._interrupt_reply('voice hold')
        self.assertEqual(self.tasks.get(held)['state'], 'cancelled')
        self.assertIsNone(self.conversation.workspace_task)
        self.engine._event({'event': 'done', 'session': 'conversation', 'turn': {'id': turn, 'status': 'interrupted'}})
        self.assertEqual(self.tasks.get(held)['state'], 'cancelled')
        reset = self.submit('r2')
        self.engine.reset()
        self.assertEqual(self.tasks.get(reset)['state'], 'cancelled')
        self.assertIsNone(self.engine.sessions['conversation'].workspace_task)

    def test_reconnect_snapshot_and_events_reach_the_bus(self):
        task_id = self.submit()
        self.assertEqual(self.events(task_id), ['running'])
        self.assertEqual([t['taskId'] for t in self.tasks.recent()], [task_id])


class Events(unittest.TestCase):
    """/ws/events (rose.app's Server.serve_events) shows a phone only its own page tasks (amendment C), without the
    internal device field, in the replay, the reconnect snapshot and live."""

    def test_only_this_devices_page_tasks(self):
        import threading
        from fastapi import FastAPI, WebSocket
        from fastapi.testclient import TestClient
        from rose.app import Server
        from rose.bus import Bus
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bus = Bus()
        tasks = WorkspaceTasks(Path(tmp.name), Starter(), emit=lambda t, d: bus.emit('workspace_task', task=t, device=d))
        self.addCleanup(tasks.close)
        mine = tasks.submit('phone', GOOD)['taskId']
        tasks.submit('tablet', GOOD)
        server = SimpleNamespace(bus=bus, snapshot=lambda: {'tasks': []}, _stale=lambda m: False, workspace_tasks=tasks)
        app = FastAPI()

        @app.websocket('/events')
        async def events(ws: WebSocket):
            import asyncio
            bus.loop = asyncio.get_running_loop()  # as rose.app does at startup, so live events are delivered
            ws.scope['rose_device'] = 'phone'  # what the guarded remote route sets
            await Server.serve_events(server, ws)
        with TestClient(app).websocket_connect('/events') as ws:
            frames = [ws.receive_json() for _ in range(4)]  # hello, one replay, replay_done, one snapshot
            later = tasks.submit('tablet', {**GOOD, 'requestId': 'r9'})  # not shown live either
            tasks.turn_ended(mine, 'completed')
            frames.append(ws.receive_json())
            ws.close()
        kinds = [f['kind'] for f in frames]
        self.assertEqual(kinds, ['hello', 'workspace_task', 'replay_done', 'workspace_task', 'workspace_task'])
        page = [f for f in frames if f['kind'] == 'workspace_task']
        self.assertEqual({f['task']['taskId'] for f in page}, {mine})
        self.assertTrue(all('device' not in f for f in page))
        self.assertEqual(page[-1]['task']['state'], 'done')
        self.assertNotEqual(later['taskId'], mine)


class Reconnect(unittest.TestCase):
    def test_a_change_already_in_the_snapshot_is_not_sent_after_it(self):
        from fastapi import FastAPI, WebSocket
        from fastapi.testclient import TestClient
        from rose.app import Server
        from rose.bus import Bus
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bus = Bus()
        tasks = WorkspaceTasks(Path(tmp.name), Starter())
        self.addCleanup(tasks.close)
        connect(SimpleNamespace(bus=bus, sessions={}), tasks)
        tasks.start = Starter()
        task_id = tasks.submit('phone', GOOD)['taskId']
        bus.history.clear()
        real = tasks.snapshot

        def snapshot(device=None):
            # A change still queued for this socket when the snapshot is read (it is already in the snapshot).
            bus.emit('workspace_task', task={**tasks.get(task_id), 'state': 'running'}, device='phone',
                     rev=tasks.revision)
            return real(device)
        tasks.snapshot = snapshot
        tasks.turn_ended(task_id, 'completed')
        bus.history.clear()
        server = SimpleNamespace(bus=bus, snapshot=lambda: {'tasks': []}, _stale=lambda m: False, workspace_tasks=tasks)
        app = FastAPI()

        @app.websocket('/events')
        async def events(ws: WebSocket):
            import asyncio
            bus.loop = asyncio.get_running_loop()
            ws.scope['rose_device'] = 'phone'
            await Server.serve_events(server, ws)
        with TestClient(app).websocket_connect('/events') as ws:
            frames = [ws.receive_json() for _ in range(3)]  # hello, replay_done, the snapshot
            later = tasks.submit('phone', {**GOOD, 'requestId': 'r2'})['taskId']
            frames.append(ws.receive_json())  # the next live change, not the stale one
            ws.close()
        self.assertEqual([f['kind'] for f in frames], ['hello', 'replay_done', 'workspace_task', 'workspace_task'])
        self.assertEqual(frames[2]['task']['state'], 'done')
        self.assertEqual(frames[3]['task']['taskId'], later)
        self.assertTrue(all('rev' not in f and 'device' not in f for f in frames[2:]))


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
        self.assertEqual((r.status_code, r.json()['status']), (200, 'accepted'))
        task_id = r.json()['taskId']
        self.assertRegex(task_id, r'^wt-[0-9a-f]{16}$')
        self.assertEqual(self.post(GOOD).json(), {'schemaVersion': 1, 'status': 'duplicate', 'taskId': task_id})
        self.assertEqual(len(self.start.calls), 1)

    def test_another_devices_task_is_unknown(self):  # amendment C
        task_id = self.post(GOOD).json()['taskId']
        other = Device()
        self.remote.devices.enroll(other.spki, 'tablet', 'Pixel Tablet', '100.64.0.3')
        path = '/workspace/v1/tasks/' + task_id
        r = self.client.get(path, headers=other.headers('GET', path, self.remote.identity.pin))
        self.assertEqual((r.status_code, r.json()), (404, {'schemaVersion': 1, 'status': 'unknown'}))

    def test_signed_status_read(self):
        task_id = self.post(GOOD).json()['taskId']
        path = '/workspace/v1/tasks/' + task_id
        r = self.client.get(path, headers=self.device.headers('GET', path, self.remote.identity.pin))
        self.assertEqual((r.status_code, r.json()['state'], r.json()['taskId']), (200, 'running', task_id))
        self.assertEqual(self.client.get(path).status_code, 401)  # unsigned
        unknown = '/workspace/v1/tasks/wt-' + '0' * 16
        r = self.client.get(unknown, headers=self.device.headers('GET', unknown, self.remote.identity.pin))
        self.assertEqual((r.status_code, r.json()), (404, {'schemaVersion': 1, 'status': 'unknown'}))

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
