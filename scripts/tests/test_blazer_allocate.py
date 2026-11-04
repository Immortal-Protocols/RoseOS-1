"""BLAZER-ALLOCATE: scripts/blazer-allocate.py reserves one fresh blazer build under the shared lock from every consumed
identity (public blazer-{build} release tags, blazer-integrate/-reconcile storage entries, earlier reservations), writes
the reservation write-once and derives the integration's new directories. Fake release listing, fake lock and a
temporary home only: no network, host or lock."""
import contextlib
import datetime
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('blazer_allocate', ROOT/'scripts/blazer-allocate.py')
alloc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alloc)
NOW = datetime.datetime(2026, 11, 3, 9, 45, tzinfo=datetime.timezone.utc).timestamp()
SOURCE = 'c' * 40


class Allocate(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home/'rose-actions/blazer-prep-41473133543-1').mkdir(parents=True)
        (self.home/'rose-actions/frankel-2026110301').mkdir()
        self.releases = [{'tag_name': 'frankel-2026110301', 'draft': False, 'prerelease': False},
                         {'tag_name': 'rango-2026103001', 'draft': False, 'prerelease': False}]
        self.locks = []

    def fetch(self, url):
        return self.releases if url.endswith('page=1') else []

    @contextlib.contextmanager
    def lock(self, path):
        self.locks.append(path)
        yield

    def reserve(self, **change):
        args = dict(home=self.home, run_id='41700000000', run_attempt='1', source=SOURCE, fetch=self.fetch,
                    clock=lambda: NOW, lock=self.lock)
        args.update(change)
        return alloc.reserve(**args)

    def test_a_first_reservation_is_the_days_first_build_with_derived_directories(self):
        record = self.reserve()
        self.assertEqual(record['build'], '2026110301')  # frankel and rango builds are other devices' identities
        self.assertEqual((record['workDir'], record['integrationDir']),
                         (str(self.home/'rose-actions/blazer-integrate-2026110301'),
                          str(self.home/'rose-actions/blazer-reconcile-2026110301')))
        self.assertEqual(self.locks, ['/home/pixelbuild/frankel-build.lock'])
        saved = json.loads((self.home/'rose-actions/blazer-allocations/2026110301.json').read_text())
        self.assertEqual(saved, record)
        self.assertFalse(os.path.lexists(record['workDir']))

    def test_every_consumed_identity_moves_the_build_forward(self):
        self.reserve()
        self.assertEqual(self.reserve(run_attempt='2')['build'], '2026110302')  # an earlier reservation
        (self.home/'rose-actions/blazer-reconcile-2026110305').mkdir()
        self.assertEqual(self.reserve(run_attempt='3')['build'], '2026110306')  # storage
        self.releases.append({'tag_name': 'blazer-2026110309', 'draft': True, 'prerelease': True})
        self.assertEqual(self.reserve(run_attempt='4')['build'], '2026110310')  # a release tag, drafts included

    def test_unknown_or_unreadable_state_refuses_and_reserves_nothing(self):
        cases = {
            'storage': lambda: (self.home/'rose-actions/blazer-integrate-latest').mkdir(),
            'reservation': lambda: ((self.home/'rose-actions/blazer-allocations').mkdir(),
                                    (self.home/'rose-actions/blazer-allocations/2026110301.json').write_text('{}')),
            'linked dir': lambda: (self.home/'rose-actions/blazer-allocations').symlink_to(self.home),
            'clock': lambda: self.releases.append({'tag_name': 'blazer-2026110401', 'draft': False, 'prerelease': False}),
            'listing': lambda: setattr(self, 'fetch', lambda url: {'message': 'rate limited'}),
        }
        for name, change in cases.items():
            with self.subTest(name=name):
                self.setUp()
                change()
                with self.assertRaises(alloc.Refused):
                    self.reserve()
                listed = sorted((self.home/'rose-actions').iterdir())
                self.assertFalse([p for p in listed if p.name.startswith('blazer-integrate-2026110301')])

    def test_inputs_refuse(self):
        for change in ({'run_id': '0'}, {'run_attempt': 'x'}, {'source': 'abc'}):
            with self.subTest(change=change), self.assertRaises(alloc.Refused):
                self.reserve(**change)
        self.assertEqual(self.locks, [])

    def test_the_tag_comes_from_the_committed_blazer_route(self):
        self.assertEqual(alloc.tag_pattern(), 'blazer-{build}')


if __name__ == '__main__':
    unittest.main()
