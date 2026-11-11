"""Synthetic receipts only. No cloud provider, phone, auth or deployment."""
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from rose.life import Diary, Life, verified_result


class Clock:
    def __init__(self): self.now = 1000.
    def __call__(self): return self.now


class Executor:
    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls, self.cancelled = [], []
        self.stopped = threading.Event()
        self.confirm_stop = True
        self.fail = False
        self.omit_start = False
    def research(self, job, cancelled, started):
        self.calls.append(job)
        if not self.omit_start and not started(): return None
        self.entered.set()
        self.release.wait(3)
        if self.fail: raise RuntimeError('private error not disclosed')
        body = 'A sourced public photography note.'
        return {'task_id': job['id'], 'action': 'public_research', 'summary': 'Researched exposure.', 'body': body,
                'sources': [{'title': 'Exposure', 'url': 'https://example.org/exposure', 'read_at': job['started']}],
                'server_note': {'task_id': job['id'], 'verified': True, 'sha256': hashlib.sha256(body.encode()).hexdigest(),
                               'note_id': 'life-'+job['id'], 'storage': 'server', 'namespace': 'rosie-server-life-v1', 'version': 1, 'saved_at': job['started']},
                'unfinished': 'Compare aperture examples.'}
    def cancel(self, task_id):
        self.cancelled.append(task_id)
        self.stopped.set()
        return self.confirm_stop


class LifeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.diary = Diary(Path(self.temp.name) / 'life')
        self.executor = Executor()
        self.events = []
        self.life = Life(self.diary, self.executor, clock=self.clock, emit=self.events.append,
                         idle_seconds=10, interval=100, budget=20)
        self.life.call({'action': 'interest', 'interest': 'photography'})
        self.life.call({'action': 'resume'})
    def tearDown(self):
        self.executor.release.set()
        if self.life._worker: self.life._worker.join(3)
        self.temp.cleanup()
    def start(self):
        self.assertTrue(self.life.tick(connected=True))
        self.assertTrue(self.executor.entered.wait(2))
    def finish(self):
        self.executor.release.set()
        self.life._worker.join(3)
        self.assertFalse(self.life._worker.is_alive())
    def latest(self): return self.diary.snapshot()['activities'][0]

    def test_persisted_private_diary_and_recall(self):
        self.start(); self.finish()
        fresh = Life(Diary(self.diary.directory), clock=self.clock)
        result = fresh.call({'action': 'diary'})
        self.assertEqual(result['activities'][0]['state'], 'completed')
        self.assertIn('exposure', result['activities'][0]['result']['summary'])
        self.assertEqual(result['activities'][0]['result']['unfinished'], 'Compare aperture examples.')
        self.assertTrue(result['activities'][0]['result']['content_is_untrusted'])
        self.assertEqual(self.diary.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.diary.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual([e['state'] for e in self.events], ['queued', 'running', 'settled'])

    def test_unconfigured_never_fabricates_work(self):
        life = Life(self.diary, clock=self.clock)
        self.assertEqual(life.call({'action': 'resume'})['status'], 'waiting_for_executor')
        self.assertFalse(life.tick(connected=True))
        self.assertEqual(life.status()['activities'], [])

    def test_waiting_disconnected_busy_and_paused_are_still(self):
        self.assertFalse(self.life.tick(connected=False))
        self.assertFalse(self.life.tick(connected=True, user_busy=True))
        self.life.call({'action': 'pause'})
        self.assertFalse(self.life.tick(connected=True))
        self.assertEqual(self.events, [])

    def test_user_priority_cancels_only_background_task(self):
        self.start(); tid = self.latest()['id']
        self.life.user_active()
        self.assertTrue(self.life._cancel.is_set())
        self.finish()
        self.assertIn(self.latest()['state'], ('interrupted', 'stopping'))
        self.assertTrue(self.executor.stopped.wait(2))
        self.assertEqual(self.executor.cancelled, [tid])
        self.assertFalse(self.life.tick(connected=True))

    def test_duplicate_tick_and_process_claim_do_not_dispatch_twice(self):
        self.start()
        second = Diary(self.diary.directory)
        self.assertIsNone(second.claim(self.clock(), 10, 100, 20))
        for _ in range(20): self.assertFalse(self.life.tick(connected=True))
        self.assertEqual(len(self.executor.calls), 1)
        self.finish()
        self.assertFalse(self.life.tick(connected=True))

    def test_stop_target_and_repeat(self):
        self.start()
        self.assertFalse(self.life.stop('other-task'))
        tid = self.latest()['id']
        self.assertTrue(self.life.stop(tid)); self.assertTrue(self.life.stop(tid))
        self.finish()
        self.assertTrue(self.executor.stopped.wait(2))
        self.assertEqual(self.executor.cancelled, [tid])
        self.assertTrue(self.latest()['result']['server_note']['verified'])

    def test_pause_persists_across_restart(self):
        self.life.call({'action': 'pause'})
        self.assertFalse(Diary(self.diary.directory).snapshot()['enabled'])
        self.assertEqual(self.life.call({'action': 'resume'})['status'], 'waiting')

    def test_deadline_rejects_late_success(self):
        self.start(); self.clock.now += 21
        self.life.tick(connected=True); self.finish()
        self.assertEqual(self.latest()['state'], 'interrupted')
        self.assertTrue(self.latest()['result']['server_note']['verified'])

    def test_crash_claim_is_not_replayed(self):
        job = self.diary.claim(self.clock(), 10, 100, 20)
        self.assertIsNotNone(job)
        self.clock.now += 200
        fresh = Diary(self.diary.directory)
        self.assertIsNone(fresh.claim(self.clock(), 10, 100, 20))
        self.assertFalse(fresh.snapshot()['enabled'])
        self.assertEqual(fresh.snapshot()['activities'][0]['reason'], 'execution_receipt_expired')

    def test_next_interest_and_bounded_catalog(self):
        self.life.call({'action': 'interest', 'interest': 'Photography'})
        self.assertEqual(len(self.life.status()['interests']), 1)
        self.life.call({'action': 'interest', 'interest': 'astronomy'})
        self.start(); self.finish(); self.clock.now += 100
        self.start(); self.finish()
        self.assertEqual({j['interest'] for j in self.executor.calls}, {'photography', 'astronomy'})
        for i in range(6): self.life.call({'action': 'interest', 'interest': 'topic ' + str(i)})
        self.assertFalse(self.life.call({'action': 'interest', 'interest': 'extra'})['ok'])

    def test_invalid_interests_and_actions(self):
        for interest in (None, '', 2, 'a\nb', 'x' * 81):
            self.assertFalse(self.life.call({'action': 'interest', 'interest': interest})['ok'])
        self.assertFalse(self.life.call({'action': 'delete_user_memory'})['ok'])

    def test_no_start_no_completed_receipt(self):
        self.executor.omit_start = True
        self.start(); self.finish()
        self.assertEqual(self.latest()['state'], 'failed')
        self.assertEqual([e['state'] for e in self.events], ['queued', 'settled'])

    def test_adapter_exception_is_bounded_and_redacted(self):
        self.executor.fail = True
        self.start(); self.finish()
        self.assertEqual(self.latest()['state'], 'failed')
        self.assertEqual(self.latest()['reason'], 'research_failed')

    def test_receipt_requires_sources_and_own_server_note(self):
        job = {'id': 'one', 'started': 1000., 'deadline': 1020.}
        self.executor.release.set()
        good = self.executor.research(job, threading.Event(), lambda: True)
        self.assertIsNotNone(verified_result(good, job, 1001.))
        for field, value in [('task_id', 'other'), ('action', 'visited_cafe'), ('body', ''), ('sources', []),
                             ('server_note', None), ('summary', 'x'*1201)]:
            self.assertIsNone(verified_result({**good, field: value}, job, 1001.), field)
        for field, value in [('verified', False), ('task_id', 'other'), ('sha256', 'fake'), ('saved_at', float('nan')),
                             ('saved_at', 2000), ('storage', 'phone'), ('namespace', 'other'), ('version', 0)]:
            self.assertIsNone(verified_result({**good, 'server_note': {**good['server_note'], field: value}}, job, 1001.))
        for url in ('file:///private/user', 'https://user:secret@example.org', 'javascript:alert(1)', 'https://'):
            self.assertIsNone(verified_result({**good, 'sources': [{**good['sources'][0], 'url': url}]}, job, 1001.))

    def test_uncertain_stop_stays_visible_until_worker_returns(self):
        self.executor.confirm_stop = False
        self.start(); self.life.stop()
        self.assertFalse(self.life.tick(connected=True))
        self.finish()
        self.assertEqual(self.latest()['state'], 'interrupted')
        self.assertTrue(self.latest()['result']['server_note']['verified'])

    def test_shutdown_waits_for_worker_and_marks_timeout_uncertain(self):
        self.start()
        self.assertFalse(self.life.shutdown(timeout=.01))
        self.assertFalse(self.diary.snapshot()['enabled'])
        self.assertEqual(self.latest()['state'], 'stopping')
        self.assertEqual(self.latest()['reason'], 'shutdown_unconfirmed')
        self.finish()
        self.assertTrue(self.life.shutdown(timeout=.01))

    def test_sql_text_is_data(self):
        self.life.call({'action': 'interest', 'interest': "photography'); DROP TABLE activities; --"})
        self.assertEqual(self.life.status()['activities'], [])
        self.assertEqual(len(self.life.status()['interests']), 2)


if __name__ == '__main__': unittest.main()
