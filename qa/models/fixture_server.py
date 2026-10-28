#!/usr/bin/env python3
"""Loopback chat-completions fixture for Models QA (MODELS-QA-ONBOARD-001).

Standard library only. Binds 127.0.0.1 on an ephemeral port and answers
OpenAI-style POST .../chat/completions with scripted behaviour. A client selects
a scenario through its base URL: http://127.0.0.1:PORT/s/<scenario>/v1. Any
request path under /s/<scenario>/ is accepted, so the client's own path suffix
does not matter.

This proves fixture behaviour and, when a client points at it, client behaviour.
It never exercises a model or a serving runtime.

Every request is recorded (scenario, path, parsed body, auth classification,
timings, whether the client disconnected). The Authorization value itself is
never stored: it is classified as absent, sentinel or other.
GET /__qa/requests returns the record; POST /__qa/reset clears it.
"""
import argparse
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SENTINEL = 'QA-SENTINEL-NOT-A-SECRET'
FIXTURE_MODEL_HEADER = 'X-QA-Fixture'


def completion(model, content, finish_reason='stop', **message_extra):
    message = {'role': 'assistant', 'content': content, **message_extra}
    if content is ...:
        del message['content']
    return {
        'id': 'qa-fixture-completion',
        'object': 'chat.completion',
        'created': 0,
        'model': model,
        'choices': [{'index': 0, 'message': message, 'finish_reason': finish_reason}],
        'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
    }


def reply_text(body):
    """Deterministic reply: names the turn and how many messages were received."""
    messages = body.get('messages') if isinstance(body, dict) else None
    messages = messages if isinstance(messages, list) else []
    users = sum(1 for m in messages if isinstance(m, dict) and m.get('role') == 'user')
    return f'fixture reply {users} (received {len(messages)} messages)'


def error_body(status, kind, message):
    return {'error': {'message': message, 'type': kind, 'code': status}}


# name -> (description, handler). Handlers receive (handler, body, model).
SCENARIOS = {}


def scenario(name, description):
    def register(fn):
        SCENARIOS[name] = (description, fn)
        return fn
    return register


@scenario('ok', 'Valid completion; reply names the user-turn count')
def _ok(h, body, model):
    h.send_json(200, completion(model, reply_text(body)))


@scenario('length', 'Valid completion truncated with finish_reason=length')
def _length(h, body, model):
    h.send_json(200, completion(model, reply_text(body), finish_reason='length'))


@scenario('model-mismatch', 'Valid completion whose model field differs from the request')
def _mismatch(h, body, model):
    h.send_json(200, completion('qa-fixture-other-model', reply_text(body)))


@scenario('missing-content', 'choices[0].message has no content field')
def _missing_content(h, body, model):
    h.send_json(200, completion(model, ...))


@scenario('null-content', 'choices[0].message.content is null')
def _null_content(h, body, model):
    h.send_json(200, completion(model, None))


@scenario('empty-content', 'choices[0].message.content is an empty string')
def _empty_content(h, body, model):
    h.send_json(200, completion(model, ''))


@scenario('empty-choices', 'choices is an empty array')
def _empty_choices(h, body, model):
    payload = completion(model, 'unused')
    payload['choices'] = []
    h.send_json(200, payload)


@scenario('wrong-type', 'content is a number')
def _wrong_type(h, body, model):
    h.send_json(200, completion(model, 42))


@scenario('non-json', 'HTTP 200 with a plain-text body')
def _non_json(h, body, model):
    h.send_raw(200, b'this is not json\n', 'text/plain')


@scenario('html-502', 'HTTP 502 with an HTML proxy error page')
def _html_502(h, body, model):
    h.send_raw(502, b'<html><body><h1>502 Bad Gateway</h1></body></html>', 'text/html')


@scenario('truncated', 'Content-Length promises more JSON than is sent, then the connection closes')
def _truncated(h, body, model):
    data = json.dumps(completion(model, reply_text(body))).encode()
    h.send_response(200)
    h.send_header('Content-Type', 'application/json')
    h.send_header('Content-Length', str(len(data)))
    h.end_headers()
    h.wfile.write(data[: len(data) // 2])
    h.wfile.flush()
    h.close_connection = True
    h.connection.shutdown(socket.SHUT_RDWR)


def _status(status, kind, message, headers=None):
    def handler(h, body, model):
        h.send_json(status, error_body(status, kind, message), headers or {})
    return handler


for _name, _status_code, _kind, _msg, _headers in [
    ('401', 401, 'authentication_error', 'fixture: invalid credentials', None),
    ('403', 403, 'permission_error', 'fixture: forbidden', None),
    ('404', 404, 'not_found_error', 'fixture: no such model or route', None),
    ('400-context', 400, 'invalid_request_error',
     "fixture: This model's maximum context length is 32 tokens (context_length_exceeded)", None),
    ('429', 429, 'rate_limit_error', 'fixture: rate limited', {'Retry-After': '2'}),
    ('429-no-retry-after', 429, 'rate_limit_error', 'fixture: rate limited', None),
    ('500', 500, 'server_error', 'fixture: internal error', None),
    ('502', 502, 'server_error', 'fixture: bad gateway', None),
    ('503', 503, 'server_error', 'fixture: unavailable', {'Retry-After': '1'}),
    ('504', 504, 'server_error', 'fixture: gateway timeout', None),
]:
    SCENARIOS[_name] = (f'HTTP {_status_code} {_kind} JSON error', _status(_status_code, _kind, _msg, _headers))


@scenario('no-headers', 'Reads the request, then sends nothing for --hold seconds')
def _no_headers(h, body, model):
    h.wait_for_disconnect(h.server.hold)


@scenario('slow-body', 'Sends headers at once, then the JSON body one byte every --drip seconds')
def _slow_body(h, body, model):
    data = json.dumps(completion(model, reply_text(body))).encode()
    h.send_response(200)
    h.send_header('Content-Type', 'application/json')
    h.send_header('Content-Length', str(len(data)))
    h.end_headers()
    h.mark_first_byte()
    deadline = time.monotonic() + h.server.hold
    for i in range(len(data)):
        if time.monotonic() > deadline:
            break
        try:
            h.wfile.write(data[i:i + 1])
            h.wfile.flush()
        except OSError:
            h.record['client_disconnected'] = True
            return
        time.sleep(h.server.drip)
    h.close_connection = True


@scenario('stall-after-headers', 'Sends headers and half the body, then stalls for --hold seconds')
def _stall(h, body, model):
    data = json.dumps(completion(model, reply_text(body))).encode()
    h.send_response(200)
    h.send_header('Content-Type', 'application/json')
    h.send_header('Content-Length', str(len(data)))
    h.end_headers()
    h.wfile.write(data[: len(data) // 2])
    h.wfile.flush()
    h.mark_first_byte()
    h.wait_for_disconnect(h.server.hold)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'RoseModelsQAFixture/1'

    def log_message(self, fmt, *args):
        if self.server.verbose:
            sys.stderr.write('fixture: ' + (fmt % args) + '\n')

    def setup(self):
        super().setup()
        with self.server.lock:
            self.server.connections += 1

    # -- response helpers -------------------------------------------------
    def mark_first_byte(self):
        record = getattr(self, 'record', None)
        if record is not None and record.get('first_byte_s') is None:
            self.record['first_byte_s'] = round(time.monotonic() - self.started, 4)

    def send_raw(self, status, data, content_type, headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header(FIXTURE_MODEL_HEADER, '1')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.mark_first_byte()
        self.wfile.write(data)
        self.wfile.flush()

    def send_json(self, status, payload, headers=None):
        self.send_raw(status, json.dumps(payload).encode(), 'application/json', headers)

    def wait_for_disconnect(self, seconds):
        """Hold the socket open without writing; note if the client closes first."""
        end = time.monotonic() + seconds
        self.connection.settimeout(0.05)
        while time.monotonic() < end:
            try:
                if self.connection.recv(1, socket.MSG_PEEK) == b'':
                    self.record['client_disconnected'] = True
                    self.record['client_closed_after_s'] = round(time.monotonic() - self.started, 4)
                    break
            except socket.timeout:
                continue
            except OSError:
                self.record['client_disconnected'] = True
                break
        self.close_connection = True

    # -- request handling -------------------------------------------------
    def classify_auth(self):
        value = self.headers.get('Authorization')
        if value is None:
            return 'absent'
        return 'sentinel' if value == f'Bearer {SENTINEL}' else 'other'

    def do_GET(self):
        if self.path == '/__qa/requests':
            with self.server.lock:
                payload = {'connections': self.server.connections, 'requests': list(self.server.requests)}
            self.send_json(200, payload)
        elif self.path == '/__qa/scenarios':
            self.send_json(200, {name: desc for name, (desc, _) in SCENARIOS.items()})
        else:
            self.send_json(404, error_body(404, 'not_found_error', 'fixture: use POST /s/<scenario>/...'))

    def do_POST(self):
        self.started = time.monotonic()
        if self.path == '/__qa/reset':
            with self.server.lock:
                self.server.requests.clear()
                self.server.connections = 0
            self.send_json(200, {'reset': True})
            return
        parts = self.path.split('/')
        name = parts[2] if len(parts) > 2 and parts[1] == 's' else self.server.default_scenario
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b''
        try:
            body = json.loads(raw) if raw else None
            body_ok = True
        except (ValueError, UnicodeDecodeError):
            body, body_ok = None, False
        self.record = {
            'seq': None,
            'scenario': name,
            'path': self.path,
            'auth': self.classify_auth(),
            'content_type': self.headers.get('Content-Type'),
            'body_valid_json': body_ok,
            'body': body,
            'first_byte_s': None,
            'client_disconnected': False,
        }
        with self.server.lock:
            self.record['seq'] = len(self.server.requests) + 1
            self.server.requests.append(self.record)
        if name not in SCENARIOS:
            self.send_json(404, error_body(404, 'not_found_error', f'fixture: unknown scenario {name}'))
        elif self.server.require_auth and self.record['auth'] != 'sentinel':
            self.send_json(401, error_body(401, 'authentication_error', 'fixture: sentinel bearer required'))
        else:
            model = body.get('model') if isinstance(body, dict) and isinstance(body.get('model'), str) else 'qa-fixture-model'
            try:
                SCENARIOS[name][1](self, body, model)
            except (BrokenPipeError, ConnectionResetError):
                self.record['client_disconnected'] = True
                self.close_connection = True
        self.record['handled_s'] = round(time.monotonic() - self.started, 4)


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=0, default_scenario='ok', hold=30.0, drip=0.5, require_auth=False, verbose=False):
        super().__init__(('127.0.0.1', port), Handler)
        self.default_scenario = default_scenario
        self.hold = hold
        self.drip = drip
        self.require_auth = require_auth
        self.verbose = verbose
        self.lock = threading.Lock()
        self.requests = []
        self.connections = 0

    @property
    def base(self):
        return f'http://127.0.0.1:{self.server_address[1]}'

    def url(self, name):
        return f'{self.base}/s/{name}/v1'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--port', type=int, default=0, help='loopback port (default: ephemeral)')
    parser.add_argument('--scenario', default='ok', help='scenario for paths outside /s/<scenario>/')
    parser.add_argument('--hold', type=float, default=30.0, help='seconds to hold no-headers/stall scenarios')
    parser.add_argument('--drip', type=float, default=0.5, help='seconds between slow-body bytes')
    parser.add_argument('--require-auth', action='store_true', help=f'answer 401 unless Bearer {SENTINEL}')
    parser.add_argument('--list', action='store_true', help='list scenarios and exit')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args(argv)
    if args.list:
        for name, (desc, _) in SCENARIOS.items():
            print(f'{name:22} {desc}')
        return 0
    server = FixtureServer(args.port, args.scenario, args.hold, args.drip, args.require_auth, args.verbose)
    # One machine-readable line so a harness can discover the ephemeral port.
    print(json.dumps({'fixture': 'rose-models-qa', 'base': server.base, 'example': server.url('ok')}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
