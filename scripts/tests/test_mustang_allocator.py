"""Mocked Mustang allocation: races, crashes, replays, and corrupt journals."""
import json
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts')]
import mustang_allocator as allocator
import mustang_epoch as epoch

PATCH = '2026-10-05'
ROLLBACK = 1791158400


class MemoryStore:
    def __init__(self):
        self.objects = {}
        self.lock = threading.Lock()
        self.drop_after_create = False

    def get(self, name):
        with self.lock:
            if name not in self.objects:
                raise KeyError(name)
            body, generation = self.objects[name]
            return json.loads(json.dumps(body)), generation

    def create(self, name, body):
        with self.lock:
            if name in self.objects:
                raise allocator.PreconditionFailed(name)
            self.objects[name] = (json.loads(json.dumps(body)), 1)
            if self.drop_after_create:
                self.drop_after_create = False
                raise RuntimeError('response lost after create')
            return 1

    def list_prefix(self, prefix):
        with self.lock:
            return sorted(name for name in self.objects if name.startswith(prefix))

    def insert(self, name, body):
        with self.lock:
            self.objects[name] = (body, 1)


class AllocatorTest(unittest.TestCase):
    def reserve(self, store, run, attempt=1, timestamp=1793755348):
        return allocator.reserve(store, run, attempt, timestamp, ROLLBACK, PATCH, backend_established=True)

    def test_provision_is_unestablished_and_the_cli_issues_nothing(self):
        record = allocator.provision()
        self.assertFalse(record['backendEstablished'])
        self.assertFalse(record['bucketCreated'])
        self.assertEqual(record['permissions'], ['storage.objects.create', 'storage.objects.get', 'storage.objects.list'])
        self.assertIn('storage.objects.delete', record['notGranted'])
        self.assertEqual(allocator.main(), 4)

    def test_replay_returns_the_same_object_and_a_changed_retry_conflicts(self):
        store = MemoryStore()
        first = self.reserve(store, '501')
        again = self.reserve(store, '501')
        self.assertEqual(again, first)
        self.assertFalse(first['issued'])
        self.assertEqual(first['build'], '2026130001')
        self.assertEqual(len(store.objects), 1)
        with self.assertRaisesRegex(ValueError, 'replay differs'):
            self.reserve(store, '501', timestamp=1793755349)
        with self.assertRaisesRegex(ValueError, 'backend is not established'):
            allocator.reserve(store, '501', 1, 1793755348, ROLLBACK, PATCH, backend_established=False)
        self.assertEqual(len(store.objects), 1)

    def test_crash_after_create_reads_the_same_reservation_back(self):
        store = MemoryStore()
        store.drop_after_create = True
        got = self.reserve(store, '502')
        self.assertEqual(got['build'], '2026130001')
        self.assertFalse(got['issued'])
        self.assertEqual(self.reserve(store, '502'), got)

    def test_create_conflict_reads_the_winner_and_takes_the_next_number(self):
        side = MemoryStore()
        winner = self.reserve(side, '601', timestamp=1793755348)
        store = MemoryStore()
        state = {'armed': True}

        def create(name, body):
            if state['armed']:
                state['armed'] = False
                store.objects[allocator.object_name(winner['build'])] = (winner, 1)
                raise allocator.PreconditionFailed(name)
            return MemoryStore.create(store, name, body)

        store.create = create
        second = self.reserve(store, '602', timestamp=1793755349)
        self.assertEqual(second['build'], '2026130002')
        self.assertFalse(second['issued'])
        self.assertNotEqual(second['runId'], winner['runId'])
        chain = allocator.read_chain(store)
        self.assertEqual([item['build'] for item in chain], ['2026130001', '2026130002'])

    def test_corrupt_prefix_rejects_the_next_create(self):
        store = MemoryStore()
        store.insert(allocator.object_name('2026130001'), {'schema': 'broken'})
        with self.assertRaises(ValueError):
            self.reserve(store, '503')
        self.assertEqual(list(store.objects), [allocator.object_name('2026130001')])

    def test_transport_uses_create_only_and_readback(self):
        backing = MemoryStore()
        bucket = allocator.provision()['bucket']

        def transport(request):
            self.assertNotIn('delete', request['url'])
            if request['method'] == 'POST':
                self.assertIn('ifGenerationMatch=0', request['url'])
                name = request['url'].split('name=')[1].split('&')[0]
                name = name.replace('%2F', '/')
                try:
                    backing.create(name, request['body'])
                except allocator.PreconditionFailed:
                    return 412, {}
                return 200, {}
            if 'alt=media' in request['url']:
                name = request['url'].split('/o/')[1].split('?')[0].replace('%2F', '/')
                try:
                    body, _generation = backing.get(name)
                except KeyError:
                    return 404, {}
                return 200, body
            names = backing.list_prefix(allocator.PREFIX)
            return 200, {'names': names}

        store = allocator.TransportStore(transport, bucket)
        got = self.reserve(store, '504')
        self.assertEqual(got['status'], 'reserved')
        self.assertFalse(got['issued'])
        self.assertEqual(got['proposal']['status'], 'proposal')
        self.assertEqual(got['proposal']['epoch'], epoch.EPOCH)


if __name__ == '__main__':
    unittest.main()
