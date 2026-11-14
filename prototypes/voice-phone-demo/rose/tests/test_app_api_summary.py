"""The library's per-version `apiReport` summary (docs/APP-BUILDER.md §3.3). The phone is shown only this, and
nothing the server cannot read, or never recorded, may look clean.

The cases in fixtures/api-report-summary-v1.json are the contract Home/App clients test against: each recorded
report and the summary the library serves for it, written out by hand rather than generated."""
import json
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import apilevel
from rose.app_builder import ledger as L
from rose.tests.test_app_apilevel import CALL, DB, dump

FIXTURE = Path(__file__).parent / 'fixtures' / 'api-report-summary-v1.json'
CASES = {c['case']: c for c in json.loads(FIXTURE.read_text(encoding='utf-8'))['cases']}
RAN = ('complete', 'incomplete')  # an analysis ran: the only statuses with counts
COUNTED = ('minSdk', 'counts', 'highestLevel', 'truncated', 'unlistedCount')


class Fixture(unittest.TestCase):
    def test_every_case_is_served_exactly(self):
        for name, case in CASES.items():
            with self.subTest(case=name):
                self.assertEqual(apilevel.wire_summary(case['stored']), case['apiReport'])
                if isinstance(case['stored'], dict):  # the ledger keeps the JSON text
                    self.assertEqual(apilevel.wire_summary(json.dumps(case['stored'], sort_keys=True)),
                                     case['apiReport'])

    def test_the_required_cases_are_there(self):
        self.assertLessEqual({'legacy-no-report', 'unavailable', 'incomplete-zero-counts', 'truncated',
                              'complete-unlisted-assumptions', 'unknown-schema', 'malformed-text'}, set(CASES))
        self.assertEqual(CASES['legacy-no-report']['stored'], None)
        self.assertEqual(CASES['incomplete-zero-counts']['apiReport']['counts'],
                         {'notGuardedHere': 0, 'unverified': 0, 'guarded': 0})
        self.assertGreater(CASES['truncated']['apiReport']['truncated'], 0)
        self.assertGreater(CASES['complete-unlisted-assumptions']['apiReport']['unlistedCount'], 0)

    def test_every_summary_says_advisory_and_not_device_proof(self):
        for name, case in CASES.items():
            with self.subTest(case=name):
                s = case['apiReport']
                self.assertEqual((s['schemaVersion'], s['advisory'], s['deviceProof']), (1, True, False))
                self.assertIn(s['status'], ('none', 'unavailable', *RAN))
                self.assertIn('not device compatibility proof', s['note'])
                self.assertIn('nothing here blocks a build or an install', s['note'])
                # Counts only for an analysis that ran, so `none` and `unavailable` never read as "zero found".
                self.assertEqual({k for k in COUNTED if k in s}, set(COUNTED) if s['status'] in RAN else set())
                self.assertEqual('reason' in s, s['status'] in ('incomplete', 'unavailable'))


class Unreadable(unittest.TestCase):
    """Whatever the record holds, a summary is either a faithful copy of a readable report or `unavailable`."""

    def setUp(self):
        self.good = CASES['complete-with-findings']['stored']

    def broken(self, change):
        report = json.loads(json.dumps(self.good))
        change(report)
        return apilevel.wire_summary(report)

    def test_a_damaged_record_is_never_clean(self):
        def drop(*keys):
            def change(r):
                for k in keys[:-1]:
                    r = r[k]
                del r[keys[-1]]
            return change

        def put(value, *keys):
            def change(r):
                for k in keys[:-1]:
                    r = r[k]
                r[keys[-1]] = value
            return change

        changes = {
            'no schema': drop('schemaVersion'), 'schema true': put(True, 'schemaVersion'),
            'schema text': put('1', 'schemaVersion'), 'schema 2': put(2, 'schemaVersion'),
            'not advisory': put(False, 'advisory'), 'advisory 1': put(1, 'advisory'),
            'unknown status': put('clean', 'status'), 'no status': drop('status'),
            'no counts': drop('counts'), 'a count missing': drop('counts', 'unverified'),
            'a negative count': put(-1, 'counts', 'guarded'), 'a bool count': put(True, 'counts', 'guarded'),
            'a text count': put('1', 'counts', 'guarded'), 'no findings': drop('findings'),
            'findings not a list': put({}, 'findings'), 'a finding not an object': put(['x'], 'findings'),
            'a finding with an unknown status': put('fine', 'findings', 0, 'status'),
            'a finding with no level': drop('findings', 0, 'level'),
            'a bool level': put(True, 'findings', 0, 'level'), 'a zero level': put(0, 'findings', 0, 'level'),
            'no truncated': drop('truncated'), 'a negative truncated': put(-1, 'truncated'),
            'counts above the findings': put(9, 'counts', 'guarded'),
            'more truncated than counted': put(1, 'truncated'),
            'no unlistedCount': drop('unlistedCount'), 'no unlisted list': drop('unlisted'),
            'more listed than counted': put(['a.B.c()V'], 'unlisted'),
            'no minSdk': drop('minSdk'), 'a bool minSdk': put(True, 'minSdk'),
            'incomplete without a reason': put('incomplete', 'status'),
        }
        for name, change in changes.items():
            with self.subTest(change=name):
                s = self.broken(change)
                self.assertEqual(s['status'], 'unavailable', s)
                self.assertFalse(set(COUNTED) & set(s), s)
        for stored in ('', 'null', '[]', '"complete"', '{}', b'\xff', 7, [], True):
            with self.subTest(stored=stored):
                self.assertEqual(apilevel.wire_summary(stored)['status'], 'unavailable')

    def test_an_unavailable_record_without_a_reason_is_still_unavailable(self):
        s = apilevel.wire_summary({'schemaVersion': 1, 'advisory': True, 'status': 'unavailable'})
        self.assertEqual((s['status'], s['reason']), ('unavailable', 'the recorded report could not be read'))

    def test_a_long_reason_is_cut(self):
        s = apilevel.wire_summary(apilevel.unavailable('x' * 5000, 26))
        self.assertEqual(len(s['reason']), apilevel.REASON_MAX)


class FromTheAnalysis(unittest.TestCase):
    """The summary of what analyse() and unavailable() actually record."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'api.xml'
        path.write_text(DB)
        self.db = apilevel.Database(path)

    def test_truncation_keeps_the_full_counts(self):
        methods = [f'0000: {CALL}\n0003: return-void'] * (apilevel.MAX_FINDINGS + 5)
        report = apilevel.analyse([dump(methods)], self.db, 'app.x', 26)
        self.assertEqual((len(report['findings']), report['truncated']), (apilevel.MAX_FINDINGS, 5))
        s = apilevel.wire_summary(json.dumps(report))
        self.assertEqual((s['status'], s['counts']['notGuardedHere'], s['truncated'], s['highestLevel']),
                         ('complete', apilevel.MAX_FINDINGS + 5, 5, 34))

    def test_unlisted_assumptions_are_counted(self):
        unlisted = '0000: invoke-virtual {v0}, Ljava/lang/Object;.unlisted:()V // method@0001\n0003: return-void'
        s = apilevel.wire_summary(apilevel.analyse([dump(unlisted)], self.db, 'app.x', 26))
        self.assertEqual((s['status'], s['counts'], s['highestLevel'], s['unlistedCount']),
                         ('complete', {'notGuardedHere': 0, 'unverified': 0, 'guarded': 0}, None, 1))

    def test_an_unfinished_analysis_with_nothing_found_is_incomplete(self):
        report = apilevel.analyse([dump(f'0000: {CALL}\n0003: return-void')], self.db, 'app.x', 26, budget_s=-1)
        s = apilevel.wire_summary(report)
        self.assertEqual((s['status'], s['counts'], s['highestLevel']),
                         ('incomplete', {'notGuardedHere': 0, 'unverified': 0, 'guarded': 0}, None))
        self.assertIn('budget', s['reason'])

    def test_unavailable_has_no_counts(self):
        s = apilevel.wire_summary(apilevel.unavailable('the pinned API database or dexdump is missing', 26))
        self.assertEqual(s, CASES['unavailable']['apiReport'])

    def test_a_guarded_reference_sets_no_level(self):
        guarded = ('0000: sget v0, Landroid/os/Build$VERSION;.SDK_INT:I // field@0001\n'
                   '0002: const/16 v1, #int 34 // #22\n0004: if-lt v0, v1, 000a // +0006\n'
                   f'0006: {CALL}\n0009: return-void\n000a: return-void')
        report = apilevel.analyse([dump(guarded)], self.db, 'app.x', 26)
        self.assertEqual([f['status'] for f in report['findings']], ['guarded'])
        self.assertIsNone(apilevel.wire_summary(report)['highestLevel'])


class Library(unittest.TestCase):
    """The ledger serves each version its own summary, scoped by project and versionCode."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.l = L.Ledger(Path(tmp.name) / 'builder')
        self.addCleanup(self.l.close)

    def deliver(self, job, project, n, report):
        self.l.advance(job, 'building', 'b', 'b')
        self.assertEqual(self.l.allocate_version(job), n)
        self.l.bind_signer(project, 'a' * 64)
        self.assertTrue(self.l.deliver(job, str(n), f'rev{n}', f'{n:x}' * 64, 10, 'x', api_report=report))

    def test_each_version_has_its_own_summary_and_none_without_a_report(self):
        a = self.l.create('d', 'ra', 'x', 'A')
        self.deliver(a['jobId'], a['projectId'], 1, None)  # a version from before the report existed
        r = self.l.revise('d', a['projectId'], 'ra2', 'more')
        self.deliver(r['jobId'], a['projectId'], 2, CASES['incomplete-zero-counts']['stored'])
        b = self.l.create('d', 'rb', 'y', 'B')
        self.deliver(b['jobId'], b['projectId'], 1, CASES['truncated']['stored'])
        by_project = {p['projectId']: [v['apiReport'] for v in p['versions']] for p in self.l.projects()}
        self.assertEqual(by_project[a['projectId']], [CASES['legacy-no-report']['apiReport'],
                                                      CASES['incomplete-zero-counts']['apiReport']])
        self.assertEqual(by_project[b['projectId']], [CASES['truncated']['apiReport']])
        self.assertEqual(self.l.versions(b['projectId'])[0]['apiReport'], CASES['truncated']['apiReport'])
        # The full report stays in the ledger, unchanged, for the version it describes.
        self.assertEqual(self.l.api_report(b['projectId'], 1), CASES['truncated']['stored'])
        self.assertIsNone(self.l.api_report(a['projectId'], 1))

    def test_a_damaged_record_is_served_as_unavailable(self):
        a = self.l.create('d', 'ra', 'x', 'A')
        self.deliver(a['jobId'], a['projectId'], 1, CASES['complete-with-findings']['stored'])
        with self.l.lock:
            self.l.db.execute('UPDATE version_api_reports SET report=? WHERE project_id=?',
                              (CASES['malformed-text']['stored'], a['projectId']))
        [version] = self.l.projects()[0]['versions']
        self.assertEqual(version['apiReport'], CASES['malformed-text']['apiReport'])
        self.assertEqual(version['versionCode'], 1)  # the rest of the library is still served


if __name__ == '__main__':
    unittest.main()
