#!/usr/bin/env python3
"""Self-tests for the Models QA loopback fixture.

The probe below is a small Python HTTP client, not the Rust chat client. A pass
here proves the fixture serves each scenario as documented; it says nothing
about the Rust client or any model.
"""
import http.client
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixture_server import REASONING_SENTINEL, SCENARIOS, SENTINEL, FixtureServer  # noqa: E402

MODEL = 'qa-explicit-model-id'


class Fixture(unittest.TestCase):
    hold = 3.0
    drip = 0.01
    require_auth = False
    tls = (None, None)

    def setUp(self):
        self.server = FixtureServer(hold=self.hold, drip=self.drip, require_auth=self.require_auth, big_mib=2,
                                    tls_cert=self.tls[0], tls_key=self.tls[1])
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def post(self, name, body, auth=SENTINEL, timeout=5.0):
        url = urlsplit(self.server.url(name))
        conn = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)
        headers = {'Content-Type': 'application/json'}
        if auth is not None:
            headers['Authorization'] = f'Bearer {auth}'
        try:
            conn.request('POST', url.path + '/chat/completions', json.dumps(body).encode(), headers)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def records(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        try:
            conn.request('GET', '/__qa/requests')
            return json.loads(conn.getresponse().read())
        finally:
            conn.close()

    def wait_for(self, predicate, seconds=3.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()


class ChatHistory(Fixture):
    def converse(self, turns):
        history = [{'role': 'system', 'content': 'QA synthetic system prompt'}]
        for text in turns:
            history.append({'role': 'user', 'content': text})
            status, _, raw = self.post('ok', {'model': MODEL, 'messages': history})
            self.assertEqual(status, 200)
            reply = json.loads(raw)['choices'][0]['message']['content']
            history.append({'role': 'assistant', 'content': reply})
        return history

    def test_two_turn_request_carries_full_history_in_order(self):
        history = self.converse(['My codeword is N-7f3a. Reply OK.', 'What is my codeword?'])
        seen = self.records()['requests']
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1]['body']['messages'], history[:4])
        self.assertEqual([m['role'] for m in seen[1]['body']['messages']], ['system', 'user', 'assistant', 'user'])
        self.assertEqual(history[2]['content'], 'fixture reply 1 (received 2 messages)')
        self.assertEqual(history[4]['content'], 'fixture reply 2 (received 4 messages)')
        self.assertEqual({r['auth'] for r in seen}, {'sentinel'})

    def test_twelve_turns_grow_monotonically(self):
        self.converse([f'turn {i} 你好 שלום \U0001f339 "quote" \\ end' for i in range(12)])
        counts = [len(r['body']['messages']) for r in self.records()['requests']]
        self.assertEqual(counts, [2 + 2 * i for i in range(12)])

    def test_records_never_contain_the_auth_value(self):
        self.converse(['hello'])
        self.post('ok', {'model': MODEL, 'messages': []}, auth='some-other-value')
        dump = json.dumps(self.records())
        self.assertNotIn(SENTINEL, dump)
        self.assertNotIn('some-other-value', dump)
        self.assertEqual([r['auth'] for r in self.records()['requests']], ['sentinel', 'other'])

    def test_same_script_reproduces_identical_records(self):
        def normalized():
            out = []
            for r in self.records()['requests']:
                out.append({k: v for k, v in r.items() if not k.endswith('_s')})
            return out
        self.converse(['a', 'b', 'c'])
        first = normalized()
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        conn.request('POST', '/__qa/reset', b'')
        conn.getresponse().read()
        conn.close()
        self.converse(['a', 'b', 'c'])
        self.assertEqual(normalized(), first)

    def test_one_connection_per_request_is_counted(self):
        self.converse(['a', 'b'])
        snapshot = self.records()
        # Two chat requests plus this GET, each on its own connection.
        self.assertEqual(snapshot['connections'], 3)


class ResponseShapes(Fixture):
    def body(self, name):
        status, headers, raw = self.post(name, {'model': MODEL, 'messages': [{'role': 'user', 'content': 'x'}]})
        return status, headers, raw

    def test_valid_completion_echoes_requested_model(self):
        status, _, raw = self.body('ok')
        payload = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(payload['model'], MODEL)
        self.assertEqual(payload['choices'][0]['finish_reason'], 'stop')

    def test_malformed_and_unexpected_shapes(self):
        expectations = {
            'missing-content': lambda p: 'content' not in p['choices'][0]['message'],
            'null-content': lambda p: p['choices'][0]['message']['content'] is None,
            'empty-content': lambda p: p['choices'][0]['message']['content'] == '',
            'empty-choices': lambda p: p['choices'] == [],
            'wrong-type': lambda p: p['choices'][0]['message']['content'] == 42,
            'length': lambda p: p['choices'][0]['finish_reason'] == 'length',
            'model-mismatch': lambda p: p['model'] == 'qa-fixture-other-model',
        }
        for name, check in expectations.items():
            with self.subTest(name):
                status, _, raw = self.body(name)
                self.assertEqual(status, 200)
                self.assertTrue(check(json.loads(raw)))

    def test_non_json_and_html_error_pages(self):
        status, headers, raw = self.body('non-json')
        self.assertEqual((status, headers['Content-Type']), (200, 'text/plain'))
        self.assertRaises(ValueError, json.loads, raw)
        status, headers, raw = self.body('html-502')
        self.assertEqual((status, headers['Content-Type']), (502, 'text/html'))

    def test_http_error_statuses(self):
        for name, status_code, retry_after in [
            ('401', 401, None), ('403', 403, None), ('404', 404, None), ('400-context', 400, None),
            ('429', 429, '2'), ('429-no-retry-after', 429, None),
            ('500', 500, None), ('502', 502, None), ('503', 503, '1'), ('504', 504, None),
        ]:
            with self.subTest(name):
                status, headers, raw = self.body(name)
                self.assertEqual(status, status_code)
                self.assertEqual(headers.get('Retry-After'), retry_after)
                self.assertEqual(json.loads(raw)['error']['code'], status_code)
        status, _, raw = self.body('400-context')
        self.assertIn('context_length_exceeded', json.loads(raw)['error']['message'])

    def test_truncated_body_is_incomplete(self):
        with self.assertRaises(http.client.IncompleteRead):
            self.body('truncated')

    def test_unknown_scenario_is_404(self):
        status, _, _ = self.body('no-such-scenario')
        self.assertEqual(status, 404)

    def test_every_scenario_is_documented(self):
        self.assertTrue(all(desc for desc, _ in SCENARIOS.values()))
        self.assertGreaterEqual(len(SCENARIOS), 24)


class RequiredAuth(Fixture):
    require_auth = True

    def test_missing_or_wrong_auth_is_401(self):
        for auth in (None, 'wrong'):
            status, _, _ = self.post('ok', {'model': MODEL, 'messages': []}, auth=auth)
            self.assertEqual(status, 401)
        status, _, _ = self.post('ok', {'model': MODEL, 'messages': []})
        self.assertEqual(status, 200)


class Timing(Fixture):
    """Probe timings show the fixture holds or drips as documented."""

    def raw_request(self, name):
        sock = socket.create_connection(('127.0.0.1', self.server.server_address[1]))
        body = json.dumps({'model': MODEL, 'messages': [{'role': 'user', 'content': 'x'}]}).encode()
        sock.sendall(b'POST /s/' + name.encode() + b'/v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                     b'Content-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() +
                     b'\r\n\r\n' + body)
        return sock

    def test_no_headers_triggers_first_byte_timeout_and_sees_client_close(self):
        sock = self.raw_request('no-headers')
        sock.settimeout(0.5)
        start = time.monotonic()
        with self.assertRaises(socket.timeout):
            sock.recv(1)
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.45)
        self.assertLess(elapsed, 1.5)
        sock.close()
        self.assertTrue(self.wait_for(lambda: self.records()['requests'][0]['client_disconnected']))
        record = self.records()['requests'][0]
        self.assertIsNone(record['first_byte_s'])
        self.assertLess(record['client_closed_after_s'], self.hold)

    def test_slow_body_defeats_idle_timeout_but_not_total_deadline(self):
        sock = self.raw_request('slow-body')
        sock.settimeout(0.5)  # idle timeout: never fires, a byte arrives every 10 ms
        deadline = time.monotonic() + 1.0  # total deadline the client must enforce itself
        received = b''
        while time.monotonic() < deadline:
            chunk = sock.recv(4096)
            if not chunk:
                break
            received += chunk
        sock.close()
        head, _, body = received.partition(b'\r\n\r\n')
        length = int([line.split(b':')[1] for line in head.split(b'\r\n') if line.lower().startswith(b'content-length')][0])
        self.assertTrue(head.startswith(b'HTTP/1.1 200'))
        self.assertLess(len(body), length, 'body completed before the total deadline; drip is too fast')
        self.assertGreater(len(body), 10)
        self.assertTrue(self.wait_for(lambda: self.records()['requests'][0]['client_disconnected']))

    def test_stall_after_headers_sends_partial_body_then_holds(self):
        sock = self.raw_request('stall-after-headers')
        sock.settimeout(0.5)
        received = sock.recv(65536)
        while b'\r\n\r\n' not in received:
            received += sock.recv(65536)
        try:
            while True:
                received += sock.recv(65536)
        except socket.timeout:
            pass
        sock.close()
        head, _, body = received.partition(b'\r\n\r\n')
        self.assertTrue(head.startswith(b'HTTP/1.1 200'))
        self.assertGreater(len(body), 0)
        self.assertRaises(ValueError, json.loads, body)


class CycleBScenarios(Fixture):
    def body(self, name, path_suffix='/chat/completions'):
        url = urlsplit(self.server.url(name))
        conn = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
        try:
            conn.request('POST', url.path + path_suffix, json.dumps({'model': MODEL, 'messages': []}).encode(),
                         {'Content-Type': 'application/json'})
            r = conn.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            conn.close()

    def test_seq_selector_answers_each_request_in_order_and_repeats_last(self):
        statuses = [self.body('seq:ok,500,401')[0] for _ in range(4)]
        self.assertEqual(statuses, [200, 500, 401, 401])
        names = [r['scenario'] for r in self.records()['requests']]
        self.assertEqual(names, ['ok', '500', '401', '401'])

    def test_reasoning_is_separate_from_content(self):
        message = json.loads(self.body('reasoning')[2])['choices'][0]['message']
        self.assertTrue(message['content'].startswith('fixture reply'))
        self.assertIn(REASONING_SENTINEL, message['reasoning'])
        message = json.loads(self.body('reasoning-only')[2])['choices'][0]['message']
        self.assertIsNone(message['content'])
        self.assertIn(REASONING_SENTINEL, message['reasoning'])

    def test_invalid_utf8_body(self):
        status, _, raw = self.body('invalid-utf8')
        self.assertEqual(status, 200)
        self.assertIn(b'\xff\xfe', raw)
        self.assertRaises(UnicodeDecodeError, raw.decode, 'utf-8')

    def test_oversized_body_is_valid_json_of_configured_size(self):
        status, headers, raw = self.body('oversized')
        self.assertEqual(int(headers['Content-Length']), len(raw))
        self.assertEqual(len(json.loads(raw)['choices'][0]['message']['content']), 2 << 20)

    def test_redirects_point_at_ok_on_cross_and_same_origin(self):
        port = self.server.server_address[1]
        status, headers, _ = self.body('redirect-cross-origin')
        self.assertEqual((status, headers['Location']), (307, f'http://localhost:{port}/s/ok/v1/chat/completions'))
        status, headers, _ = self.body('redirect-same-origin')
        self.assertEqual(headers['Location'], f'http://127.0.0.1:{port}/s/ok/v1/chat/completions')

    def test_raw_predict_path_is_recorded_verbatim(self):
        self.body('ok', '/projects/p/locations/l/endpoints/e:rawPredict')
        self.assertEqual(self.records()['requests'][0]['path'], '/s/ok/v1/projects/p/locations/l/endpoints/e:rawPredict')


def make_self_signed(directory):
    cert, key = os.path.join(directory, 'cert.pem'), os.path.join(directory, 'key.pem')
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', cert,
                    '-days', '1', '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1'],
                   check=True, capture_output=True)
    return cert, key


@unittest.skipUnless(shutil.which('openssl'), 'openssl not available')
class UntrustedTls(Fixture):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='models-qa-tls-')
        cls.tls = make_self_signed(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def https(self, context):
        conn = http.client.HTTPSConnection('127.0.0.1', self.server.server_address[1], timeout=5, context=context)
        try:
            conn.request('POST', '/s/ok/v1/chat/completions', b'{}', {'Content-Type': 'application/json'})
            return conn.getresponse().status
        finally:
            conn.close()

    def test_default_verification_rejects_and_fixture_counts_the_failed_handshake(self):
        self.assertTrue(self.server.base.startswith('https://'))
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.https(ssl.create_default_context())
        self.assertTrue(self.wait_for(lambda: self.server.tls_handshake_failures == 1))
        self.assertEqual(self.server.requests, [])

    def test_explicitly_trusted_cert_is_served(self):
        self.assertEqual(self.https(ssl.create_default_context(cafile=self.tls[0])), 200)


if __name__ == '__main__':
    unittest.main(verbosity=2)
