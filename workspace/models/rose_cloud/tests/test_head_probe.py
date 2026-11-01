"""rose_cloud/stage.py HEAD-only mode (CLOUD-REDIRECT-HEAD-001) against a local fixture server (OFFLINE: 127.0.0.1
only, test-only hubBase/sources and an allowlist of 127.0.0.1). Nothing here touches the Hub, PyPI or a VM.

What is proved: only HEAD requests are sent (on every hop); no response body is read, even when a misbehaving server
sends one; nothing is written anywhere (the staging root is never created or opened); a refused destination is
recorded as a sanitized hostname and never requested; at most MAX_REDIRECTS hops are followed; the deadline is
enforced inside a stuck request and by the real-time alarm of the script entry point; the receipt holds fixed tokens
only and is bounded.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT), str(ROOT.parent / 'serving' / 'service')]

from rose_cloud import stage as S  # noqa: E402

MANIFEST = json.loads((ROOT / 'rose_cloud' / 'manifest.json').read_text())
SOURCE = (ROOT / 'rose_cloud' / 'stage.py').read_text()
BODY = b'BODY-BYTES-SECRET' * 64  # what a misbehaving server sends after HEAD headers; must never be read


class Server:
    """HEAD fixture. ``mode`` per path: 'ok' (200 with the pinned length and a body anyway), 'hop' (302 to an allowed
    127.0.0.1 path), 'refuse' (302 to http://localhost/...?sig=SECRET, refused), 'chain' (``chain`` allowed hops),
    'auth' (401), 'lie' (wrong Content-Length), 'hang' (no response for 30 s), 'hop_hang' (allowed hop to a hang),
    'drip' (headers trickled for about 60 s, each byte well inside any socket timeout).
    Every request is recorded as (method, path); a GET gets 405 (except the instrumentation control)."""

    def __init__(self):
        self.mode, self.size, self.requests, self.chain = {}, {}, [], 0
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a):
                pass

            def reply(self, code, headers=(), body=b''):
                self.send_response(code)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    self.wfile.write(body)  # sent even for HEAD: the client must not read it
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True

            def do_GET(self):
                srv.requests.append(('GET', self.path))
                if self.path == '/control':  # only the instrumentation control test asks for a body
                    return self.reply(200, [('Content-Length', str(len(BODY)))], BODY)
                self.reply(405, [('Content-Length', '0')])

            def do_HEAD(self):
                srv.requests.append(('HEAD', self.path))
                path = self.path.split('?')[0]
                base = path
                for prefix in ('/hop', '/redirected'):
                    if base.startswith(prefix):
                        base = base[len(prefix):]
                if base.startswith('/chain/'):
                    n, base = base[len('/chain/'):].split('/', 1)
                    n, base = int(n), '/' + base
                    if n < srv.chain:
                        return self.reply(302, [('Location', f'/chain/{n + 1}{base}'), ('Content-Length', '0')])
                    return self.reply(200, [('Content-Length', str(srv.size[base]))], BODY)
                mode = srv.mode.get(base, 'ok')
                first = path == base
                if mode == 'hang' or (mode == 'hop_hang' and not first):
                    time.sleep(30)
                    return
                if mode == 'drip':  # headers one byte every 0.3 s: no single socket read ever times out
                    try:
                        for b in b'HTTP/1.1 200 OK\r\nX-Pad: ' + b'x' * 200:
                            self.wfile.write(bytes([b]))
                            self.wfile.flush()
                            time.sleep(0.3)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                if first and mode in ('hop', 'hop_hang'):
                    return self.reply(302, [('Location', f'http://127.0.0.1:{srv.port}/hop{base}'),
                                            ('Content-Length', '0')])
                if first and mode == 'refuse':
                    return self.reply(302, [('Location', f'http://alice:SECRET@LocalHost.:{srv.port}/redirected{base}'
                                                         '?X-Amz-Signature=SECRET#frag'),
                                            ('Content-Length', str(len(BODY)))], BODY)
                if first and mode == 'chain':
                    return self.reply(302, [('Location', f'/chain/1{base}'), ('Content-Length', '0')])
                if mode == 'auth':
                    return self.reply(401, [('Content-Length', str(len(BODY)))], BODY)
                if base not in srv.size:
                    return self.reply(404, [('Content-Length', '0')])
                length = srv.size[base] + (7 if mode == 'lie' else 0)
                self.reply(200, [('Content-Length', str(length))], BODY)

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class HeadProbe(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='head-'))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.srv = Server()
        self.addCleanup(self.srv.close)
        self.model, self.config, self.wheel = ('/hub/org/m/resolve/r1/model.safetensors',
                                               '/hub/org/m/resolve/r1/config.json', '/pypi/w.whl')
        self.srv.size = {self.model: 300_000, self.config: 26, self.wheel: 50_000}
        self.staging = self.dir / 'rose-cloud'
        self.manifest = {'stagingRoot': str(self.staging), 'groups': {
            'qwen': {'repo': 'org/m', 'revision': 'r1', 'dir': 'qwen', 'files': [
                ['config.json', 26, 'gitsha1', '0' * 40], ['model.safetensors', 300_000, 'sha256', '0' * 64]]},
            'ptts': {'package': 'ptts', 'version': '0', 'dir': 'wheels', 'files': [
                ['w.whl', 50_000, 'sha256', '0' * 64]]}}}
        self.limits = {'mode': 'head', 'deadlineS': 20, 'maxBytes': 0, 'readTimeoutS': 5,
                       'allowHosts': ['127.0.0.1'], 'allowHttp': True,
                       'hubBase': f'http://127.0.0.1:{self.srv.port}/hub',
                       'sources': {'ptts/w.whl': f'http://127.0.0.1:{self.srv.port}/pypi/w.whl'}}
        # Count every body byte any HTTPResponse hands back during a probe: it must stay 0.
        self.body_bytes = 0
        probe = self

        def counting(name):
            real = getattr(http.client.HTTPResponse, name)

            def wrapper(resp, *a, **k):
                got = real(resp, *a, **k)
                probe.body_bytes += got if isinstance(got, int) else len(got or b'')
                return got
            return real, wrapper

        for name in ('read', 'read1', 'readinto', 'peek', 'readline'):
            real, wrapper = counting(name)
            setattr(http.client.HTTPResponse, name, wrapper)
            self.addCleanup(setattr, http.client.HTTPResponse, name, real)

    def probe(self, **limits):
        return S.head_probe(self.manifest, dict(self.limits, **limits))

    def methods(self):
        return {m for m, _ in self.srv.requests}

    def assertNothingWritten(self):
        self.assertFalse(self.staging.exists())
        self.assertEqual(os.listdir(self.dir), [])

    def test_all_reachable_with_head_only_no_body_and_no_writes(self):
        r = self.probe()
        self.assertEqual(r['stop'], 'done')
        self.assertEqual(r['groups'], {'qwen': {'expected': 2, 'reachable': 2, 'refused': 0, 'failed': 0},
                                       'ptts': {'expected': 1, 'reachable': 1, 'refused': 0, 'failed': 0}})
        self.assertEqual(self.methods(), {'HEAD'})
        self.assertEqual(sorted(p for _, p in self.srv.requests), sorted([self.config, self.model, self.wheel]))
        self.assertEqual(self.body_bytes, 0)  # the server sent BODY after every 200; none of it was read
        self.assertNothingWritten()
        self.assertEqual((r['refusedHosts'], r['refusedOther']), ([], 0))
        import urllib.request  # control: the counter does see a body read when one happens
        with urllib.request.urlopen(f'http://127.0.0.1:{self.srv.port}/control', timeout=5) as resp:
            resp.read()
        self.assertEqual(self.body_bytes, len(BODY))

    def test_allowed_redirect_stays_head_and_refused_destination_is_named_never_followed(self):
        self.srv.mode[self.config] = 'hop'
        self.srv.mode[self.model] = 'refuse'
        r = self.probe()
        self.assertEqual(r['groups']['qwen'], {'expected': 2, 'reachable': 1, 'refused': 1, 'failed': 0})
        self.assertEqual(r['firstFailure']['qwen'], 'redirect_refused')
        self.assertEqual((r['refusedHosts'], r['refusedOther']),
                         ([{'host': 'localhost', 'reason': 'userinfo', 'count': 1}], 0))
        self.assertIn(('HEAD', '/hop' + self.config), self.srv.requests)  # the allowed hop: HEAD again
        self.assertFalse([p for _, p in self.srv.requests if p.startswith('/redirected')])  # refused: never requested
        self.assertEqual(self.methods(), {'HEAD'})
        self.assertEqual(self.body_bytes, 0)  # not even the refused 302's body
        self.assertNothingWritten()
        line = json.dumps(S.sanitize_head(r, self.manifest))
        for leak in ('SECRET', 'X-Amz', 'sig', 'frag', 'redirected', 'alice', str(self.srv.port), 'http', 'BODY'):
            self.assertNotIn(leak, line)

    def test_every_file_is_probed_and_hosts_are_counted(self):
        for p in (self.config, self.model, self.wheel):
            self.srv.mode[p] = 'refuse'
        r = self.probe()
        self.assertEqual(r['stop'], 'done')
        self.assertEqual(r['refusedHosts'], [{'host': 'localhost', 'reason': 'userinfo', 'count': 3}])
        self.assertEqual(sum(g['refused'] for g in r['groups'].values()), 3)
        self.assertEqual(len(self.srv.requests), 3)  # one HEAD per file, no retry, no follow

    def test_redirect_bound_is_max_redirects(self):
        self.srv.mode[self.config] = 'chain'
        self.srv.chain = S.MAX_REDIRECTS  # 5 hops after the first: allowed
        r = self.probe()
        self.assertEqual(r['groups']['qwen']['reachable'], 2)
        self.srv.requests.clear()
        self.srv.chain = S.MAX_REDIRECTS + 1  # one hop too many
        r = self.probe()
        self.assertEqual((r['groups']['qwen']['failed'], r['firstFailure']['qwen']), (1, 'redirect_limit'))
        config_requests = [p for _, p in self.srv.requests if p.endswith('config.json')]
        self.assertEqual(len(config_requests), 1 + S.MAX_REDIRECTS)  # the first request plus five followed hops
        self.assertEqual(self.methods(), {'HEAD'})
        self.assertEqual(self.body_bytes, 0)

    def test_failures_are_fixed_tokens(self):
        self.srv.mode[self.config] = 'auth'
        self.srv.mode[self.model] = 'lie'
        del self.srv.size[self.wheel]
        r = self.probe()
        self.assertEqual(r['groups']['qwen'], {'expected': 2, 'reachable': 0, 'refused': 0, 'failed': 2})
        self.assertEqual(r['firstFailure'], {'qwen': 'auth_required', 'ptts': 'http_error'})
        self.assertEqual(self.body_bytes, 0)
        self.srv.close()
        r = self.probe()
        self.assertEqual(r['firstFailure'], {'qwen': 'network', 'ptts': 'network'})
        self.assertNothingWritten()

    def test_deadline_bounds_a_stuck_request_and_a_stuck_hop(self):
        for mode in ('hang', 'hop_hang'):
            with self.subTest(mode):
                self.srv.mode[self.config] = mode
                t = time.monotonic()
                r = self.probe(deadlineS=2, readTimeoutS=30)
                self.assertLess(time.monotonic() - t, 5)  # the socket timeout is min(readTimeoutS, time left)
                self.assertEqual(r['firstFailure']['qwen'], 'network')
                self.assertLessEqual(r['elapsedS'], 3)
        clock = iter([0, 0, 100, 100, 100, 100, 100]).__next__
        r = S.head_probe(self.manifest, self.limits, clock=clock)  # deadline already passed before the next file
        self.assertEqual(r['stop'], 'deadline')
        self.assertNothingWritten()

    def test_script_alarm_and_marker_line(self):
        import bootstrap as B
        self.srv.mode[self.model] = 'refuse'
        argv = [sys.executable, '-I', '-c', SOURCE, json.dumps(self.manifest),
                json.dumps(dict(self.limits, deadlineS=60))]
        code, out = B.Host().run(argv, 30)
        self.assertEqual(code, 0)
        line = out.strip().splitlines()[-1]
        self.assertTrue(line.startswith(S.HEAD_MARKER))
        raw = json.loads(line[len(S.HEAD_MARKER):])
        self.assertEqual(S.sanitize_head(raw, self.manifest), raw)  # already only fixed tokens
        self.assertEqual(raw['refusedHosts'], [{'host': 'localhost', 'reason': 'userinfo', 'count': 1}])
        for leak in ('SECRET', 'X-Amz', 'redirected', str(self.srv.port), 'BODY'):
            self.assertNotIn(leak, out)
        # A request whose every socket read succeeds (headers trickled), so no socket timeout can end it: only the
        # real-time alarm of the script entry point stops it at the deadline.
        self.srv.mode[self.model] = 'drip'
        argv[-1] = json.dumps(dict(self.limits, deadlineS=2, readTimeoutS=60))
        t = time.monotonic()
        code, out = B.Host().run(argv, 20)
        self.assertEqual(code, 0)
        self.assertLess(time.monotonic() - t, 6)
        raw = json.loads(out.strip().splitlines()[-1][len(S.HEAD_MARKER):])
        self.assertEqual(raw['stop'], 'deadline')
        self.assertNothingWritten()

    def test_sanitize_head_is_an_allowlist(self):
        hostile = S.sanitize_head({'mode': 'head', 'stop': 'done; rm -rf /', 'elapsedS': 'SECRET',
                                   'groups': {'qwen': {'reachable': -1, 'refused': 'x'}, 'evil': {'expected': 1}},
                                   'firstFailure': {'qwen': 'https://evil/?sig=SECRET', 'ptts': 'redirect_limit'},
                                   'refusedOther': True, 'extra': 'SECRET',
                                   'refusedHosts': [{'host': 'https://cdn.example/x?sig=SECRET', 'reason':
                                                     'host_not_allowed', 'count': 1},
                                                    {'host': 'fe80::1%SECRET', 'reason': 'host_not_allowed',
                                                     'count': 1},
                                                    {'host': 'CDN.Example.', 'reason': 'host_not_allowed',
                                                     'count': 45}]}, self.manifest)
        self.assertEqual(hostile, {'mode': 'head', 'stop': None, 'elapsedS': None,
                                   'groups': {'qwen': dict.fromkeys(S.HEAD_KEYS), 'ptts': dict.fromkeys(S.HEAD_KEYS)},
                                   'firstFailure': {'qwen': None, 'ptts': 'redirect_limit'},
                                   'refusedHosts': [{'host': 'cdn.example', 'reason': 'host_not_allowed',
                                                     'count': 45}],
                                   'refusedOther': None})
        self.assertIsNone(S.sanitize_head({'stop': 'done'}, self.manifest))  # a staging line is not a probe line
        self.assertIsNone(S.sanitize_head('SECRET', self.manifest))
        long_host = '.'.join(['a' * 63] * 3) + '.' + 'b' * 61
        worst = S.sanitize_head({'mode': 'head', 'stop': 'deadline', 'elapsedS': 1 << 40, 'refusedOther': 1 << 40,
                                 'groups': {n: dict.fromkeys(S.HEAD_KEYS, 1 << 40) for n in MANIFEST['groups']},
                                 'firstFailure': dict.fromkeys(MANIFEST['groups'], 'redirect_refused'),
                                 'refusedHosts': [{'host': long_host, 'reason': 'host_not_allowed',
                                                   'count': 1 << 40}] * 9}, MANIFEST)
        self.assertLess(len(json.dumps(worst, separators=(',', ':'))), 2600)  # room in the 4 KiB receipt

    def test_real_manifest_plan_is_59_heads_to_allowed_hosts(self):
        urls = [S.url_for(n, g, rel, {}) for n, g in MANIFEST['groups'].items() for rel, *_ in g['files']]
        self.assertEqual(len(urls), 59)
        f = S.Fetcher({'mode': 'head', 'deadlineS': 1, 'maxBytes': 0})
        self.assertTrue(f.head_only)
        for u in urls:
            f.check(u)
        self.assertEqual(f.allow, S.ALLOW_HOSTS)
        self.assertEqual(S.ALLOW_HOSTS, ('huggingface.co', 'us.aws.cdn.hf.co', 'us.gcp.cdn.hf.co',
                                         'files.pythonhosted.org'))
        self.assertEqual(f.schemes, ('https',))
        self.assertEqual(S.MAX_REDIRECTS, 5)

    def test_stage_mode_is_unchanged_by_head_mode(self):
        f = S.Fetcher({'deadlineS': 1, 'maxBytes': 1})
        self.assertFalse(f.head_only)
        self.assertEqual(S.STOPS, ('done', 'refused_existing', 'insufficient_space', 'deadline', 'byte_cap',
                                   'file_failures', 'bad_manifest'))
        self.assertEqual(S.MARKER, 'ROSE-CLOUD-STAGE ')


if __name__ == '__main__':
    unittest.main()
