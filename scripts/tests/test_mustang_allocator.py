"""GCS-shaped Mustang allocation: pages, generations, conflicts, and closed admission."""
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts')]
import mustang_allocator as allocator
import mustang_epoch as epoch

PATCH = '2026-10-05'
ROLLBACK = 1791158400
BUCKET = 'roseos-mustang-allocation-1072799993443'


class GcsFake:
    def __init__(self):
        self.objects = {}
        self.next_gen = 1700000000000000
        self.page_size = 1
        self.lose_create = False
        self.tamper = None
        self.hide_list_once = False
        self.requests = []

    def __call__(self, request):
        self.requests.append(json.loads(json.dumps(request)))
        if request['method'] == 'POST':
            return self._create(request)
        url = request['url']
        if 'alt=media' in url:
            return self._media(url)
        if '/o?' in url:
            return self._list(url)
        return self._metadata(url)

    def _name(self, url):
        return unquote(url.split('/o/')[1].split('?')[0])

    def _create(self, request):
        self.assert_precondition = 'ifGenerationMatch=0' in request['url']
        if not self.assert_precondition:
            return 400, {'error': {'code': 400}}
        name = unquote(parse_qs(urlsplit(request['url']).query)['name'][0])
        if name in self.objects:
            return 412, {'error': {'code': 412, 'message': 'Precondition Failed'}}
        generation = str(self.next_gen)
        self.next_gen += 1
        self.objects[name] = {'body': json.loads(json.dumps(request['body'])), 'generation': generation}
        if self.lose_create:
            self.lose_create = False
            raise allocator.TransportLost('response lost after the object was stored')
        return 200, {'kind': 'storage#object', 'name': name, 'generation': generation, 'metageneration': '1'}

    def _list(self, url):
        if self.hide_list_once:
            self.hide_list_once = False
            return 200, {'kind': 'storage#objects'}
        query = parse_qs(urlsplit(url).query)
        prefix = query['prefix'][0]
        start = int(query['pageToken'][0]) if 'pageToken' in query else 0
        names = sorted(name for name in self.objects if name.startswith(prefix))
        page = names[start:start+self.page_size]
        payload = {'kind': 'storage#objects',
                   'items': [{'kind': 'storage#object', 'name': name,
                              'generation': self.objects[name]['generation']} for name in page]}
        if start+self.page_size < len(names):
            payload['nextPageToken'] = str(start+self.page_size)
        return 200, payload

    def _metadata(self, url):
        name = self._name(url)
        if name not in self.objects:
            return 404, {'error': {'code': 404, 'message': 'Not Found'}}
        generation = self.objects[name]['generation']
        if self.tamper == 'metadata':
            generation = str(int(generation)+10)
        elif self.tamper == 'small':
            generation = '1'
        elif self.tamper == 'float':
            generation = 1700000000000000
        return 200, {'kind': 'storage#object', 'name': name, 'generation': generation, 'metageneration': '1'}

    def _media(self, url):
        name = self._name(url)
        if name not in self.objects:
            return 404, {'error': {'code': 404}}
        match = parse_qs(urlsplit(url).query).get('ifGenerationMatch', [None])[0]
        if match != self.objects[name]['generation']:
            return 412, {'error': {'code': 412, 'message': 'Precondition Failed'}}
        if self.tamper == 'media-sequence':
            body = json.loads(json.dumps(self.objects[name]['body']))
            body['sequence'] = 0.0
            return 200, body
        return 200, json.loads(json.dumps(self.objects[name]['body']))


class AllocatorTest(unittest.TestCase):
    def store(self, fake=None):
        fake = fake or GcsFake()
        return allocator.TransportStore(fake, BUCKET), fake

    def reserve(self, store, run, attempt=1, timestamp=1793755348, observed=()):
        return allocator.reserve(store, run, attempt, timestamp, ROLLBACK, PATCH, observed,
                                 backend_established=True)

    def test_provision_is_closed_and_list_is_not_an_object_prefix_grant(self):
        record = allocator.provision()
        self.assertFalse(record['backendEstablished'])
        self.assertFalse(record['networkAdmitted'])
        self.assertFalse(record['issueAdmitted'])
        self.assertTrue(allocator.prove_iam(record))
        broken = json.loads(json.dumps(record))
        broken['bindings'][0]['condition'] = broken['bindings'][1]['condition']
        with self.assertRaisesRegex(ValueError, 'dedicated bucket'):
            allocator.prove_iam(broken)
        self.assertEqual(allocator.main([]), 4)
        self.assertEqual(allocator.main(['reserve']), 4)
        self.assertEqual(allocator.main(['issue']), 4)

    def test_closed_admission_does_not_open_a_socket(self):
        def boom(*args, **kwargs):
            raise AssertionError('network')
        with self.assertRaisesRegex(ValueError, 'not established'):
            allocator.open_admitted_transport(allocator.provision(), lambda: 'ya29.example-token-value', boom)
        seen = []

        def http(request):
            seen.append(request)
            return 200, {'kind': 'storage#objects'}

        admitted = {'backendEstablished': True, 'networkAdmitted': True, 'issueAdmitted': False}
        transport = allocator.AuthorizedTransport(http, lambda: 'ya29.example-token-value', admitted)
        self.assertEqual(allocator.list_objects(transport, BUCKET, allocator.NUMBER_PREFIX), [])
        self.assertEqual(seen[0]['headers']['Authorization'], 'Bearer ya29.example-token-value')
        with self.assertRaisesRegex(ValueError, 'not established'):
            allocator.list_objects(allocator.AuthorizedTransport(http, lambda: 'ya29.example-token-value',
                                                                allocator.provision()), BUCKET, allocator.NUMBER_PREFIX)

    def test_pages_generations_replay_and_a_lost_create(self):
        store, fake = self.store()
        first = self.reserve(store, '501', observed=['2026110401'])
        self.assertEqual(first['build'], '2026130001')
        self.assertFalse(first['issued'])
        self.assertEqual(first['observed'], ['2026110401'])
        self.assertGreaterEqual(fake.objects[allocator.object_name(first['build'])]['generation'], '1700000000000000')
        self.assertNotEqual(fake.objects[allocator.object_name(first['build'])]['generation'], '1')
        media_urls = [item['url'] for item in fake.requests if 'alt=media' in item['url']]
        self.assertTrue(media_urls)
        self.assertIn('ifGenerationMatch=1700000000000000', media_urls[0])
        self.assertEqual(self.reserve(store, '501', observed=['2026110401']), first)
        fake.lose_create = True
        second = self.reserve(store, '502', timestamp=1793755349, observed=['2026110401'])
        self.assertEqual(second['build'], '2026130002')
        self.assertEqual([item['build'] for item, _gen in zip(*allocator.read_chain(store, ['2026110401']))],
                         ['2026130001', '2026130002'])
        self.assertGreater(len([item for item in fake.requests if 'pageToken=' in item['url']]), 0)

    def test_precondition_loss_reads_the_winner_and_takes_the_next_number(self):
        store, fake = self.store()
        winner = self.reserve(store, '601', observed=['2026110401'])
        fake.hide_list_once = True
        second = self.reserve(store, '602', timestamp=1793755349, observed=['2026110401'])
        self.assertEqual(winner['build'], '2026130001')
        self.assertEqual(second['build'], '2026130002')
        posts = [item for item in fake.requests if item['method'] == 'POST' and 'ifGenerationMatch=0' in item['url']]
        self.assertGreaterEqual(len(posts), 3)

    def test_published_reservation_does_not_wedge_the_next_number(self):
        store, fake = self.store()
        first = self.reserve(store, '501', observed=['2026110401'])
        self.assertEqual(self.reserve(store, '501', observed=['2026110401']), first)
        posts = [item for item in fake.requests if item['method'] == 'POST']
        second = self.reserve(store, '502', timestamp=1793755349, observed=['2026110401', '2026130001'])
        self.assertEqual(second['build'], '2026130002')
        self.assertEqual(second['observed'], ['2026110401', '2026130001'])
        self.assertGreater(len([item for item in fake.requests if item['method'] == 'POST']), len(posts))
        before = len([item for item in fake.requests if item['method'] == 'POST'])
        with self.assertRaisesRegex(ValueError, 'not strictly newer'):
            self.reserve(store, '503', timestamp=1793755350,
                         observed=['2026110401', '2026130001', '2027010101'])
        self.assertEqual(len([item for item in fake.requests if item['method'] == 'POST']), before)
        with self.assertRaisesRegex(ValueError, 'dropped or reordered'):
            self.reserve(store, '504', timestamp=1793755350, observed=['2026130001'])
        self.assertEqual(allocator.parse_generation('17'), 17)
        with self.assertRaises(ValueError):
            allocator.parse_generation(1)

    def test_opener_timeout_after_write_is_recovered_from_the_stored_generation(self):
        import urllib.error
        from urllib.parse import parse_qs, unquote, urlsplit
        generation = '17'
        objects = {}

        class Response:
            def __init__(self, status, body):
                self.status = status
                self._body = body
            def read(self):
                return self._body
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        lost = {'armed': True}

        def opener(request, timeout):
            method = request.get_method()
            url = request.full_url
            if method == 'POST':
                name = unquote(parse_qs(urlsplit(url).query)['name'][0])
                objects[name] = json.loads(request.data.decode())
                if lost['armed']:
                    lost['armed'] = False
                    raise urllib.error.URLError('timed out after write')
                return Response(200, json.dumps({'kind': 'storage#object', 'name': name, 'generation': generation}).encode())
            if 'alt=media' in url:
                name = unquote(url.split('/o/')[1].split('?')[0])
                match = parse_qs(urlsplit(url).query).get('ifGenerationMatch', [None])[0]
                if match != generation:
                    return Response(412, b'{"error":{"code":412}}')
                return Response(200, json.dumps(objects[name]).encode())
            if '/o?' in url:
                items = [{'kind': 'storage#object', 'name': name, 'generation': generation} for name in sorted(objects)]
                return Response(200, json.dumps({'kind': 'storage#objects', 'items': items}).encode())
            name = unquote(url.split('/o/')[1].split('?')[0])
            return Response(200, json.dumps({'kind': 'storage#object', 'name': name, 'generation': generation}).encode())

        def boom(request, timeout):
            raise urllib.error.URLError('timed out')

        with self.assertRaises(allocator.TransportLost):
            allocator.urlopen_http({'method': 'GET', 'url': 'https://storage.googleapis.com/storage/v1/b/x/o/y'}, boom)
        transport = lambda request: allocator.urlopen_http(request, opener)
        store = allocator.TransportStore(transport, BUCKET)
        got = self.reserve(store, '701', observed=['2026110401'])
        self.assertEqual(got['build'], '2026130001')
        self.assertEqual(int(generation), allocator.parse_generation(generation))

    def test_higher_observed_id_fails_before_a_write(self):
        store, fake = self.store()
        with self.assertRaisesRegex(ValueError, 'not strictly newer'):
            self.reserve(store, '501', observed=['2027010101'])
        self.assertEqual(fake.objects, {})
        self.assertFalse(any(item['method'] == 'POST' for item in fake.requests))

    def test_generation_and_sequence_tampering_fail_closed(self):
        store, fake = self.store()
        self.reserve(store, '501', observed=['2026110401'])
        for mode in ('metadata', 'small', 'float', 'media-sequence'):
            fake.tamper = mode
            with self.assertRaises(ValueError):
                self.reserve(store, '503', timestamp=1793755349, observed=['2026110401'])
            fake.tamper = None
        self.assertEqual(list(fake.objects), [allocator.object_name('2026130001')])

    def test_issue_is_a_separate_object_and_stays_closed_by_default(self):
        store, fake = self.store()
        reserved = self.reserve(store, '501', observed=['2026110401'])
        with self.assertRaisesRegex(ValueError, 'not admitted'):
            allocator.issue(store, '501', 1, ['2026110401'], admission=allocator.provision())
        self.assertNotIn(allocator.ISSUE_PREFIX+reserved['build']+'.json', fake.objects)
        issued = allocator.issue(store, '501', 1, ['2026110401'], admission={
            'backendEstablished': True, 'networkAdmitted': True, 'issueAdmitted': True})
        self.assertTrue(issued['issued'])
        self.assertEqual(issued['reservationGeneration'],
                         int(fake.objects[allocator.object_name(reserved['build'])]['generation']))
        with self.assertRaisesRegex(ValueError, 'run identity'):
            allocator.issue(store, '501', True, ['2026110401'], admission={
                'backendEstablished': True, 'networkAdmitted': True, 'issueAdmitted': True})
        with self.assertRaisesRegex(ValueError, 'run identity'):
            allocator.issue(store, 501, 1, ['2026110401'], admission={
                'backendEstablished': True, 'networkAdmitted': True, 'issueAdmitted': True})
        self.assertFalse(fake.objects[allocator.object_name(reserved['build'])]['body']['issued'])
        self.assertEqual(issued['reservationSha256'], allocator.digest(reserved))
        self.assertEqual(allocator.issue(store, '501', 1, ['2026110401'], admission={
            'backendEstablished': True, 'networkAdmitted': True, 'issueAdmitted': True}), issued)

    def test_urlopen_parser_reads_a_gcs_json_body(self):
        class Response:
            status = 200
            def read(self):
                return (b'{"kind":"storage#objects","items":[{"name":"epoch-202613/numbers/2026130001.json",'
                        b'"generation":"1700000000000001"}]}')
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        status, payload = allocator.urlopen_http(
            {'method': 'GET', 'url': 'https://storage.googleapis.com/storage/v1/b/x/o?prefix=epoch'},
            lambda request, timeout: Response())
        self.assertEqual(status, 200)
        self.assertEqual(payload['items'][0]['generation'], '1700000000000001')
        self.assertIs(type(payload['items'][0]['generation']), str)

    def test_names_schema_is_rejected(self):
        def http(request):
            return 200, {'names': ['epoch-202613/numbers/2026130001.json']}
        with self.assertRaisesRegex(ValueError, 'items schema'):
            allocator.list_objects(http, BUCKET, allocator.NUMBER_PREFIX)

    def test_epoch_proposal_remains_unissued(self):
        store, _fake = self.store()
        got = self.reserve(store, '504', observed=[])
        self.assertEqual(got['proposal']['status'], 'proposal')
        self.assertFalse(got['proposal']['issued'])
        self.assertEqual(got['proposal']['epoch'], epoch.EPOCH)
        self.assertIs(type(got['sequence']), int)


if __name__ == '__main__':
    unittest.main()
