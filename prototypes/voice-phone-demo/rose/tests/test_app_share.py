"""Level 3 share state per version (docs/APP-BUILDER.md §3.3): derived from the hosting metadata the Chariot owner
serves, never invented. The hosting answers are the Chariot owner's own fixture, copied byte for byte (its git blob
id is pinned); fixtures/share-summary-v1.json is the contract Home/App's ShareState pins."""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import share as S

HERE = Path(__file__).parent / 'fixtures'
DOC = json.loads((HERE / 'share-summary-v1.json').read_text(encoding='utf-8'))
CHARIOT_COPY = HERE / 'chariot' / 'metadata-responses.json'
CHARIOT = json.loads(CHARIOT_COPY.read_text(encoding='utf-8'))
CASES = {c['case']: c for c in DOC['cases']}


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def served_ok(s) -> bool:
    """Home/App's ShareState.parse: true when the phone reads it as what it says, false for "couldn't be read"."""
    if s is None:
        return True  # absent reads as unpublished
    if not isinstance(s, dict) or s.get('schemaVersion') != 1 or type(s.get('schemaVersion')) is not int:
        return False
    if s.get('status') == 'unpublished':
        return True
    if s.get('status') == 'unavailable':
        return isinstance(s.get('reason'), str)
    return s.get('status') == 'published' and S.link(s.get('versionUrl')) is not None \
        and S.link(s.get('latestUrl')) is not None


class Provenance(unittest.TestCase):
    def test_the_hosting_answers_are_the_chariot_owners_own(self):
        self.assertEqual(git_blob(CHARIOT_COPY.read_bytes()), DOC['source']['gitBlob'])
        for case in DOC['cases']:
            if case.get('answerFrom') and 'DERIVED' not in case.get('comment', ''):
                with self.subTest(case['case']):
                    want = {k: CHARIOT[case['answerFrom']][k] for k in ('status', 'headers', 'json')}
                    self.assertEqual(case['answer'], want)  # verbatim, not edited
                    record = case['record']  # and the lookup it answers is this record's
                    if not case.get('recordDiffers'):  # a mismatch case pairs a record with another's answer
                        self.assertEqual(CHARIOT[case['answerFrom']]['path'],
                                         f'/apps/{record["hostedAppId"]}/releases/{record["versionCode"]}/metadata')
            elif case.get('answer') is not None:
                self.assertIn('DERIVED', case.get('comment', ''), case['case'])  # anything else says so

    def test_the_record_is_the_hosted_release_it_describes(self):
        published = CHARIOT['published']['json']
        record = CASES['published']['record']
        self.assertEqual((record['hostedAppId'], record['packageName'], record['versionCode'],
                          record['signerSha256'], record['apk']), (
            published['app_id'], published['package_name'], published['version_code'], published['signer_sha256'],
            {'sha256': published['sha256'], 'size': published['size_bytes']}))


class Summary(unittest.TestCase):
    def test_every_case_is_served_exactly(self):
        for name, case in CASES.items():
            with self.subTest(name):
                self.assertEqual(S.summary(case['record'], case['answer']), case['share'])

    def test_the_required_cases_are_there(self):
        for name in ('published', 'absent', 'storage-unavailable', 'never-mapped', 'no-answer', 'hash-differs',
                     'size-differs', 'signer-differs', 'package-differs', 'version-differs', 'app-differs',
                     'record-unsigned', 'derived-null-signer', 'derived-storage-unavailable-for-long'):
            self.assertIn(name, CASES)

    def test_links_are_passed_through_as_hosting_returned_them(self):
        share = CASES['published']['share']
        links = CHARIOT['published']['json']['links']
        self.assertEqual((share['versionUrl'], share['latestUrl']), (links['release'], links['latest']))
        self.assertNotIn(links['download'], json.dumps(share))  # the raw APK is never what a phone shares

    def test_nothing_but_a_full_match_is_published(self):
        published = [n for n, c in CASES.items() if c['share']['status'] == 'published']
        self.assertEqual(published, ['published'])
        record, answer = CASES['published']['record'], CASES['published']['answer']
        for field in ('app_id', 'package_name', 'version_code', 'signer_sha256', 'sha256', 'size_bytes'):
            with self.subTest(field):
                changed = copy.deepcopy(answer)
                del changed['json'][field]
                self.assertEqual(S.summary(record, changed)['status'], 'unavailable')
        keys = {'package_name': 'packageName', 'signer_sha256': 'signerSha256'}
        for field in ('package_name', 'signer_sha256', 'sha256'):  # missing on both sides is no match
            with self.subTest(both_null=field):
                changed, rec = copy.deepcopy(answer), copy.deepcopy(record)
                changed['json'][field] = None
                if field == 'sha256':
                    rec['apk']['sha256'] = None
                else:
                    rec[keys[field]] = None
                self.assertNotEqual(S.summary(rec, changed)['status'], 'published')
        for value in (True, 1.0, 0):
            with self.subTest(version=value):
                rec = copy.deepcopy(record)
                rec['versionCode'] = value
                self.assertNotEqual(S.summary(rec, answer)['status'], 'published')
        for body in (None, [], 'x', {'links': []}):
            with self.subTest(body=body):
                self.assertEqual(S.summary(record, {'status': 200, 'headers': {}, 'json': body})['status'],
                                 'unavailable')

    def test_every_served_value_is_one_the_phone_reads(self):
        for name, case in CASES.items():
            with self.subTest(name):
                self.assertTrue(served_ok(case['share']))
        for case in DOC['clientUnreadable']['cases']:
            with self.subTest(case['case']):
                self.assertFalse(served_ok(case['share']))

    LINKS_GOOD = ['https://downloads.example.invalid/apps/x/releases/1?a=b', 'https://h', 'https://h:443/x',
                  'https://1.2.3.4/x', 'https://h/%41?x=%7e', 'https://h.example./x', "https://h/a-b_c~d!$&'()*+,;=:@"]
    LINKS_BAD = ['http://downloads.example.invalid/x', 'https://downloads.example.invalid/x#y', 'https://',
                 'https://u:p@h/x', 'https://h/a b', 'https://h/\u00e9', 'https://h/' + 'a' * 2048, '', 'ftp://h/x',
                 'https:///x', 'https://my_host.example.invalid/r/1', 'https://-bad.example/x', 'https://a.b-.c/x',
                 'https://example..com/x', 'https://1.2.3.4.5/x', 'https://exa%41mple.com/x', 'https://[v1.x]/x',
                 'https://[::1]/x', 'https://example.com:abc/x', 'https://example.com:-1/x', 'https://h/r/1%zz',
                 'https://example.com/a%2', 'https://h/r\\1', 'https://example.com/a|b', 'https://h/r/{1}',
                 'https://example.com/a^b', 'https://example.com/a`b', 'https://example.com/a"b',
                 'https://example.com/a<b>', 'https://example.com/a[b]', 'https://h/x?y#z', 'https://256.1.1.1/x']

    def test_the_link_rule(self):
        served = [c['share'][k] for c in DOC['cases'] for k in ('versionUrl', 'latestUrl') if k in c['share']]
        for good in self.LINKS_GOOD + served:
            with self.subTest(good=good):
                self.assertEqual(S.link(good), good)
        for bad in self.LINKS_BAD + [None, 7]:
            with self.subTest(bad=bad):
                self.assertIsNone(S.link(bad))

    @unittest.skipUnless(shutil.which('java') or os.environ.get('ROSE_REQUIRE_APP_BUILD'), 'needs a JDK')
    def test_the_phone_accepts_every_link_the_server_does(self):
        # The phone's own ShareState.link, compiled from workspace/rose-builder: whatever the server would call
        # published, the phone must show as a link (the server may be stricter; it is never looser).
        builder = Path(__file__).resolve().parents[4] / 'workspace/rose-builder/src/dev/pixel/builder'
        sources = [builder / f'{n}.java' for n in ('ShareState', 'ShareMessage', 'BuilderWire', 'Json', 'BuilderApi',
                                                  'ApiReport', 'BuildJob', 'InstallPlan')]
        corpus = self.LINKS_GOOD + self.LINKS_BAD + ['https://h/' + c for c in "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"]
        corpus += ['https://' + h + '/x' for h in ('a', 'a1', '1a', 'a-b', 'a.b', 'A.B', 'x.1', '1.2.3', 'h.', 'a..b')]
        corpus = [u for u in corpus if '\n' not in u]
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / 'LinkProbe.java'
            harness.write_text('package dev.pixel.builder;\npublic final class LinkProbe { public static void '
                               'main(String[] a) throws Exception { java.io.BufferedReader r = new java.io.'
                               'BufferedReader(new java.io.InputStreamReader(System.in, "UTF-8")); String s; while '
                               '((s = r.readLine()) != null) System.out.println(ShareState.link(s) != null); } }\n')
            subprocess.run(['javac', '-encoding', 'UTF-8', '-d', tmp, str(harness), *map(str, sources)], check=True,
                           capture_output=True)
            out = subprocess.run(['java', '-cp', tmp, 'dev.pixel.builder.LinkProbe'], input='\n'.join(corpus) + '\n',
                                 capture_output=True, text=True, encoding='utf-8', check=True).stdout.split()
        self.assertEqual(len(out), len(corpus))
        for url, phone in zip(corpus, out):
            with self.subTest(url=url):
                if S.link(url) is not None:
                    self.assertEqual(phone, 'true', 'the server would publish a link the phone refuses')


class Caching(unittest.TestCase):
    def test_how_long_an_answer_stands(self):
        self.assertEqual(S.cache_seconds(CASES['published']['answer']), 600)  # public, max-age=600
        self.assertEqual(S.cache_seconds(CASES['absent']['answer']), 0)  # no-store
        self.assertEqual(S.cache_seconds(CASES['storage-unavailable']['answer']), 0)
        longer = {'status': 200, 'headers': {'cache-control': 'public, max-age=86400'}}
        self.assertEqual(S.cache_seconds(longer), S.MAX_CACHE_S)  # re-checked within 10 minutes regardless
        for headers in ({}, {'cache-control': 'no-store'}, {'cache-control': 'max-age=600, no-cache'},
                        {'cache-control': 's-max-age=600'}):
            with self.subTest(headers):
                self.assertEqual(S.cache_seconds({'status': 200, 'headers': headers}), 0)
        self.assertEqual(S.cache_seconds(None), 0)

    def test_how_long_to_wait_after_storage_is_unavailable(self):
        self.assertEqual(S.backoff_seconds(CASES['storage-unavailable']['answer']), 60)  # retry-after: 60
        self.assertEqual(S.backoff_seconds({'status': 503, 'headers': {'retry-after': '999999'}}), S.MAX_BACKOFF_S)
        self.assertEqual(S.backoff_seconds({'status': 503, 'headers': {'retry-after': 'soon'}}), 60)
        for digits in ('\u00b2', '\u0661\u0662\u0660', '\U0001d7d7'):  # not ASCII: unreadable, never a crash
            with self.subTest(digits=digits):
                self.assertEqual(S.backoff_seconds({'status': 503, 'headers': {'retry-after': digits}}), 60)
        self.assertEqual(S.backoff_seconds(CASES['published']['answer']), 0)


if __name__ == '__main__':
    unittest.main()
