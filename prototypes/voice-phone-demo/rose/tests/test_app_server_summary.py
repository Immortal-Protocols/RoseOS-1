"""The library's server summary (docs/APP-BUILDER.md §3.3, slice 9), against its fixture for Home/App
(fixtures/server-summary-v1.json): what the ledger serves for each recorded history, and the client rules."""
import itertools
import json
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import ledger as L, server_summary as S

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/server-summary-v1.json').read_text(encoding='utf-8'))
SERVER = {'sha256': 'e' * 64, 'size': 1, 'target': 'x86_64-unknown-linux-musl'}


def history(case):
    return [(v['versionCode'], v['hasServer'], v['contract']) for v in case['versions']]


class Summary(unittest.TestCase):
    def test_each_case_is_what_the_summary_gives(self):
        for case in FIXTURE['cases']:
            with self.subTest(case['case']):
                self.assertEqual(S.summaries(history(case)),
                                 {int(k): v for k, v in case['server'].items() if v is not None})

    def test_every_served_summary_is_one_a_client_may_read(self):
        for case in FIXTURE['cases']:
            for code, summary in case['server'].items():
                if summary is not None:
                    self.assertTrue(S.readable(summary, int(code)), (case['case'], code))

    def test_what_a_client_must_not_read(self):
        for bad in FIXTURE['clientUnreadable']:
            with self.subTest(bad['why']):
                self.assertFalse(S.readable(bad['server'], bad['versionCode']))

    def test_edge_values_a_client_must_accept(self):
        for good in FIXTURE['clientReadable']:
            with self.subTest(good['why']):
                self.assertTrue(S.readable(good['server'], good['versionCode']))

    def test_any_history(self):
        # Every summary is readable, names no later version, and names only a version at the same api with
        # client-server versions at that api all the way up to it.
        for apis in itertools.product(('plain', 'none', 'bad', 1, 2), repeat=4):
            versions = [(i + 1, a != 'plain', {'none': None, 'bad': '{"api":true}'}.get(a, json.dumps({'api': a})))
                        for i, a in enumerate(apis)]
            out = S.summaries(versions)
            self.assertEqual(set(out), {i + 1 for i, a in enumerate(apis) if a != 'plain'})
            for code, summary in out.items():
                self.assertTrue(S.readable(summary, code))
                if summary['status'] == 'ok':
                    first = summary['minClientVersionCode']
                    self.assertEqual(summary['api'], apis[code - 1])
                    self.assertTrue(all(a == apis[code - 1] for a in apis[first - 1:code]), (apis, code))
                    self.assertTrue(first == 1 or apis[first - 2] != apis[code - 1], (apis, code))
                else:
                    self.assertIn(apis[code - 1], ('none', 'bad'))


class Library(unittest.TestCase):
    """The library (Ledger.projects) serves the fixture's summary for each case, recorded as a build delivers."""

    def test_the_library_serves_each_case(self):
        for case in FIXTURE['cases']:
            with self.subTest(case['case']), tempfile.TemporaryDirectory() as tmp:
                ledger = L.Ledger(Path(tmp) / 'b')
                try:
                    project = None
                    for v in case['versions']:
                        n = v['versionCode']
                        if project is None:
                            job = ledger.create('d', 'r0', 'x', 'X')
                            project = job['projectId']
                        else:
                            job = ledger.revise('d', project, f'r{n}', 'more')
                        ledger.advance(job['jobId'], 'building', 'b', 'b')
                        self.assertEqual(ledger.allocate_version(job['jobId']), n)
                        ledger.bind_signer(project, 'a' * 64)
                        extra = dict(profile=L.CLIENT_SERVER, server=SERVER, cargo_lock='v',
                                     contract=v['contract']) if v['hasServer'] else {}
                        self.assertTrue(ledger.deliver(job['jobId'], str(n), 'rev', f'{n:x}' * 64, 10, 'x', **extra))
                    [entry] = ledger.projects()
                    for v in entry['versions']:
                        want = case['server'][str(v['versionCode'])]
                        self.assertEqual(v.get('server'), want)
                        self.assertEqual('server' in v, want is not None)
                finally:
                    ledger.close()

    def test_a_record_too_deep_to_parse_is_unavailable_and_the_library_still_served(self):
        deep = '{"api":1,"x":' + '[' * 200000 + ']' * 200000 + '}'
        self.assertEqual(S.summaries([(1, True, deep)]), {1: {'schemaVersion': 1, 'status': 'unavailable'}})
        with tempfile.TemporaryDirectory() as tmp:
            ledger = L.Ledger(Path(tmp) / 'b')
            try:
                for request, contract in (('r1', deep), ('r2', json.dumps({'api': 1}))):
                    job = ledger.create('d', request, 'x', request)
                    ledger.advance(job['jobId'], 'building', 'b', 'b')
                    self.assertEqual(ledger.allocate_version(job['jobId']), 1)
                    ledger.bind_signer(job['projectId'], 'a' * 64)
                    self.assertTrue(ledger.deliver(job['jobId'], '1', 'rev', '1' * 64, 10, 'x', profile=L.CLIENT_SERVER,
                                                   server=SERVER, cargo_lock='v', contract=contract))
                self.assertEqual(sorted(p['versions'][0]['server']['status'] for p in ledger.projects()),
                                 ['ok', 'unavailable'])
            finally:
                ledger.close()


if __name__ == '__main__':
    unittest.main()
