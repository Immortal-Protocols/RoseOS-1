#!/usr/bin/env python3
"""The shared Rose Agent version ledger (decision D1, scripts/app_reservation_store.py).

A local bare git repository stands in for pixel-ota: GitReader reads it with real git (ls-remote, fetch, cat-file), and
a fake GitHub client creates its tags with git plumbing (mktag, then update-ref with an empty old value, which refuses
an existing ref exactly as the API's 422 does). No network, token or real ledger is touched.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_reservation  # noqa: E402
import app_reservation_store as store_module  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
COMMITTED = json.loads((ROOT/'os/release-pipeline/agent/hosted.json').read_text())['reservation']['store']
STORE = {'kind': 'github-annotated-tags', 'repository': 'Example/ledger',
         'reservationPrefix': 'refs/tags/app-reservations/dev.pixel.voice/', 'floorPrefix': 'refs/tags/app-floors/dev.pixel.voice/'}
OWNER = {'workflowRef': 'Immortal-Protocols/RoseOS-1/.github/workflows/os-release.yml@refs/heads/main',
         'runId': '42000000001', 'runAttempt': '1'}
ENV = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')


def record(version=369, **change):
    owner = change.pop('owner', OWNER)
    made = store_module.reservation('dev.pixel.voice', version, change.pop('source', 'a' * 40),
                                    change.pop('candidate', 'b' * 40), change.pop('allocation', 'c' * 64), owner, STORE)
    return made


def floor(through=368, run='42000000000', attempt='1'):
    return store_module.floor('dev.pixel.voice', through, 'd' * 64, dict(OWNER, runId=run, runAttempt=attempt), STORE)


class Remote:
    """A bare repository with one commit on main, standing in for the store's GitHub repository."""

    def __init__(self, test):
        self.path = Path(tempfile.mkdtemp(prefix='rose-ledger-remote-'))
        test.addCleanup(shutil.rmtree, self.path, True)
        work = self.path/'work'
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(work)], check=True, env=ENV)
        (work/'README.md').write_text('ota\n')
        subprocess.run(['git', '-C', str(work), 'add', '-A'], check=True, env=ENV)
        subprocess.run(['git', '-C', str(work), 'commit', '-q', '-m', 'ota'], check=True, env=ENV)
        self.bare = self.path/'remote.git'
        subprocess.run(['git', 'clone', '-q', '--bare', str(work), str(self.bare)], check=True, env=ENV)
        self.head = self.git('rev-parse', 'HEAD')

    def git(self, *args, data=None):
        return subprocess.run(['git', '-C', str(self.bare), *args], check=True, capture_output=True, text=True,
                              input=data, env=ENV).stdout.strip()

    def lightweight(self, ref):
        self.git('update-ref', ref, self.head)


class FakeClient:
    """The two API calls ApiWriter makes, applied to the bare repository with git plumbing."""
    api = 'https://api.example.test'

    def __init__(self, remote):
        self.remote, self.calls, self.objects = remote, [], {}

    def _request(self, method, url, label, body=None, expect=(200,), **_):
        self.calls.append((method, url, label, json.loads(json.dumps(body)), expect))
        if url.endswith('/git/tags'):
            text = (f'object {body["object"]}\ntype {body["type"]}\ntag {body["tag"]}\n'
                    f'tagger {body["tagger"]["name"]} <{body["tagger"]["email"]}> 1793000000 +0000\n\n{body["message"]}')
            sha = self.remote.git('mktag', data=text)
            return 201, None, {'sha': sha}
        if url.endswith('/git/refs'):
            result = subprocess.run(['git', '-C', str(self.remote.bare), 'update-ref', body['ref'], body['sha'], ''],
                                    capture_output=True, env=ENV)
            status = 201 if result.returncode == 0 else 422
            assert status in expect
            return status, None, {}
        raise AssertionError(url)


class Binding(unittest.TestCase):
    def test_the_committed_store_is_the_pixel_ota_tag_ledger(self):
        self.assertEqual(store_module.check_store(COMMITTED), COMMITTED)
        self.assertEqual(COMMITTED['repository'], 'Immortal-Protocols/pixel-ota')
        # Ledger tags are never release tags: rose-agent-{versionCode} names releases, not refs under these prefixes.
        for prefix in (COMMITTED['reservationPrefix'], COMMITTED['floorPrefix']):
            self.assertFalse(prefix[len('refs/tags/'):].startswith('rose-agent-'))

    def test_any_other_shape_refuses(self):
        for change in ({'kind': 'other'}, {'repository': 'no-slash'}, {'reservationPrefix': 'refs/heads/x/'},
                       {'floorPrefix': 'refs/tags/app-floors/dev.pixel.voice'}, {'extra': 1},
                       {'floorPrefix': 'refs/tags/app-reservations/dev.pixel.voice/floors/'},
                       {'reservationPrefix': 'refs/tags/../x/'}):
            with self.subTest(change=change), self.assertRaises(store_module.Refused):
                store_module.check_store(dict(STORE, **change))
        with self.assertRaises(store_module.Refused):
            store_module.check_store({k: v for k, v in STORE.items() if k != 'floorPrefix'})


class Records(unittest.TestCase):
    def test_a_reservation_is_the_shared_receipt_format(self):
        made = record()
        self.assertEqual(set(made), app_reservation.KEYS)
        self.assertEqual(made['reservationSha256'], app_reservation.digest(made))
        self.assertEqual(store_module.message(made), app_reservation.canonical(made).decode() + '\n')

    def test_tampered_or_foreign_records_refuse(self):
        good = record()
        for change in ({'versionCode': 370}, {'store': dict(STORE, repository='Other/ledger')}, {'versionCode': '369'},
                       {'sourceSha': 'A' * 40}, {'owner': {'workflowRef': 'x', 'runId': 'r', 'runAttempt': '1'}},
                       {'schema': 'other'}):
            bad = dict(good, **change)
            if 'versionCode' not in change or change['versionCode'] == '369':
                bad['reservationSha256'] = app_reservation.digest(bad)
            with self.subTest(change=change), self.assertRaises(store_module.Refused):
                store_module.check_reservation(bad, STORE)
        made = floor()
        self.assertEqual(store_module.check_floor(made, STORE), made)
        for change in ({'consumedThrough': 0}, {'consumedThrough': '368'}, {'packageName': 5}):
            bad = dict(made, **change)
            bad['floorSha256'] = store_module.floor_digest(bad)
            with self.subTest(change=change), self.assertRaises(store_module.Refused):
                store_module.check_floor(bad, STORE)
        with self.assertRaises(store_module.Refused):
            store_module.check_floor(dict(made, consumedThrough=999), STORE)  # digest no longer holds

    def test_parse_reads_exactly_the_ledger_and_refuses_anything_else(self):
        res, flo = record(), floor()
        tags = {store_module.reservation_ref(STORE, 369): ('tag', store_module.message(res)),
                store_module.floor_ref(STORE, flo['owner']): ('tag', store_module.message(flo))}
        parsed = store_module.parse(STORE, tags, 'dev.pixel.voice')
        self.assertEqual(parsed, {'reservations': {369: res}, 'floors': [flo]})
        self.assertEqual(store_module.consumed(parsed), set(range(1, 369)) | {369})
        for name, entry in ((store_module.reservation_ref(STORE, 369), ('commit', '')),   # a lightweight tag
                            (store_module.reservation_ref(STORE, 370), ('tag', store_module.message(res))),  # wrong name
                            (STORE['reservationPrefix'] + '0369', ('tag', store_module.message(res))),
                            (STORE['floorPrefix'] + '1-1', ('tag', store_module.message(flo))),
                            (store_module.reservation_ref(STORE, 369), ('tag', 'not json'))):
            with self.subTest(name=name, entry=entry[0]), self.assertRaises(store_module.Refused):
                store_module.parse(STORE, {name: entry}, 'dev.pixel.voice')
        with self.assertRaises(store_module.Refused):
            store_module.parse(STORE, {'refs/tags/rose-agent-369': ('tag', '')}, 'dev.pixel.voice')
        with self.assertRaises(store_module.Refused):  # another package's record under this package's prefix
            store_module.parse(STORE, tags, 'dev.pixel.calculator.agent')

    def test_owner_is_this_run(self):
        env = {'GITHUB_WORKFLOW_REF': OWNER['workflowRef'], 'GITHUB_RUN_ID': OWNER['runId'], 'GITHUB_RUN_ATTEMPT': '1'}
        self.assertEqual(store_module.owner(env), OWNER)
        for name in env:
            with self.subTest(missing=name), self.assertRaises(store_module.Refused):
                store_module.owner({k: v for k, v in env.items() if k != name})
        with self.assertRaises(store_module.Refused):
            store_module.owner(dict(env, GITHUB_RUN_ID='x'))


class GitLedger(unittest.TestCase):
    def setUp(self):
        self.remote = Remote(self)
        self.client = FakeClient(self.remote)
        self.ledger = store_module.Ledger(STORE, store_module.GitReader(STORE, url=str(self.remote.bare)),
                                          store_module.ApiWriter(STORE, self.client), sleep=lambda s: None)

    def test_an_empty_ledger_reads_empty(self):
        self.assertEqual(self.ledger.read('dev.pixel.voice'), {'reservations': {}, 'floors': []})

    def test_the_first_reservation_wins_and_a_second_run_is_refused(self):
        first = record()
        self.assertEqual(self.ledger.reserve(first), {'ref': store_module.reservation_ref(STORE, 369), 'created': True})
        self.assertEqual(self.ledger.read('dev.pixel.voice')['reservations'], {369: first})
        other = record(owner=dict(OWNER, runId='42000000002'))
        with self.assertRaisesRegex(store_module.Refused, 'already held by another record'):
            self.ledger.reserve(other)
        # The same record again (a lost response) reads back as held: nothing changes.
        self.assertEqual(self.ledger.reserve(first)['created'], False)
        self.assertEqual(self.ledger.read('dev.pixel.voice')['reservations'], {369: first})
        self.assertEqual(self.ledger.confirm(first), first)
        with self.assertRaises(store_module.Refused):
            self.ledger.confirm(other)

    def test_the_tag_is_an_annotated_tag_on_head_holding_the_canonical_record(self):
        made = record()
        self.ledger.reserve(made)
        ref = store_module.reservation_ref(STORE, 369)
        self.assertEqual(self.remote.git('cat-file', '-t', ref), 'tag')
        self.assertEqual(self.remote.git('rev-parse', ref + '^{commit}'), self.remote.head)
        body = self.remote.git('cat-file', 'tag', ref)
        self.assertEqual(body.split('\n\n', 1)[1], store_module.message(made).strip())
        tags = [c for c in self.client.calls if c[1].endswith('/git/tags')]
        self.assertEqual(tags[0][3], {'tag': ref[len('refs/tags/'):], 'message': store_module.message(made),
                                      'object': self.remote.head, 'type': 'commit', 'tagger': store_module.TAGGER})
        refs = [c for c in self.client.calls if c[1].endswith('/git/refs')]
        self.assertEqual((refs[0][0], refs[0][1], refs[0][4]), ('POST', 'https://api.example.test/repos/Example/ledger/git/refs',
                                                                (201, 422)))

    def test_floors_are_recorded_and_raise_the_consumed_set(self):
        self.ledger.record_floor(floor(368))
        self.ledger.record_floor(floor(370, run='42000000005'))
        read = self.ledger.read('dev.pixel.voice')
        self.assertEqual(sorted(f['consumedThrough'] for f in read['floors']), [368, 370])
        self.assertEqual(max(store_module.consumed(read)), 370)
        with self.assertRaises(store_module.Refused):  # one floor per run attempt
            self.ledger.record_floor(store_module.floor('dev.pixel.voice', 371, 'e' * 64,
                                                        dict(OWNER, runId='42000000005'), STORE))

    def test_a_lightweight_or_foreign_ref_under_a_prefix_stops_every_read(self):
        self.remote.lightweight(store_module.reservation_ref(STORE, 400))
        with self.assertRaisesRegex(store_module.Refused, 'unexpected reservation ref'):
            self.ledger.read('dev.pixel.voice')

    def test_a_read_only_ledger_never_writes(self):
        reader_only = store_module.Ledger(STORE, store_module.GitReader(STORE, url=str(self.remote.bare)))
        with self.assertRaisesRegex(store_module.Refused, 'read-only'):
            reader_only.reserve(record())
        self.assertEqual(self.client.calls, [])
        self.assertIsNone(store_module.ledger_from(STORE).writer)


class Readback(unittest.TestCase):
    def test_a_just_created_ref_is_read_again_until_visible(self):
        made, reads, waits = record(), [], []

        class Reader:
            def head(self):
                return 'f' * 40

            def read(self, package):
                reads.append(package)
                return {'reservations': {369: made} if len(reads) >= 3 else {}, 'floors': []}

        class Writer:
            def create(self, ref, rec, head):
                return True

        ledger = store_module.Ledger(STORE, Reader(), Writer(), sleep=waits.append)
        self.assertEqual(ledger.reserve(made)['created'], True)
        self.assertEqual((len(reads), waits), (3, [store_module.READBACK_DELAY] * 2))

    def test_a_ref_that_never_reads_back_refuses(self):
        class Reader:
            def head(self):
                return 'f' * 40

            def read(self, package):
                return {'reservations': {}, 'floors': []}

        class Writer:
            def create(self, ref, rec, head):
                return True

        with self.assertRaisesRegex(store_module.Refused, 'does not read back') as caught:
            store_module.Ledger(STORE, Reader(), Writer(), sleep=lambda s: None).reserve(record())
        self.assertNotIsInstance(caught.exception, store_module.Held)  # an uncertain write is never typed retryable

    def test_only_a_ref_another_record_holds_is_held(self):
        mine, theirs = record(), record(source='d' * 40)

        def ledger(created, holder):
            class Reader:
                def head(self):
                    return 'f' * 40

                def read(self, package):
                    return {'reservations': {} if holder is None else {369: holder}, 'floors': []}

            class Writer:
                def create(self, ref, rec, head):
                    return created
            return store_module.Ledger(STORE, Reader(), Writer(), sleep=lambda s: None)
        with self.assertRaisesRegex(store_module.Held, 'already held by another record'):
            ledger(False, theirs).reserve(mine)
        with self.assertRaisesRegex(store_module.Refused, 'already exists but holds no readable record') as caught:
            ledger(False, None).reserve(mine)
        self.assertNotIsInstance(caught.exception, store_module.Held)
        with self.assertRaisesRegex(store_module.Refused, 'does not read back') as caught:
            ledger(True, theirs).reserve(mine)
        self.assertNotIsInstance(caught.exception, store_module.Held)
        self.assertEqual(ledger(False, mine).reserve(mine)['created'], False)  # this run's own record, after a lost reply
        self.assertTrue(issubclass(store_module.Held, store_module.Refused))  # every existing refusal path still holds


if __name__ == '__main__':
    unittest.main()
