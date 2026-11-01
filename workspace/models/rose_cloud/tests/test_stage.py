"""rose_cloud/stage.py against a local HTTP fixture server (OFFLINE: 127.0.0.1 only, synthetic files, test-only
hubBase/sources and an allowlist of 127.0.0.1). Nothing here touches the Hub, PyPI or a VM; the real hosts, CA store,
redirects and Range behaviour are unverified. The real manifest is used only to check the plan it produces.
"""

from __future__ import annotations

import copy
import hashlib
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


def gitsha1(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


class Server:
    """Serves FILES (path -> bytes) under /hub/<repo>/resolve/<rev>/<name> and /pypi/<name>, honouring Range.
    ``mode`` per path: 'ok', 'auth', 'ignore_range', 'extra', 'lie', 'hang', 'redirect_ok', 'redirect_bad',
    'slow' (``delay`` seconds per 64 KiB)."""

    def __init__(self):
        self.files, self.mode, self.requests, self.delay = {}, {}, [], 0.0
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a):
                pass

            def do_GET(self):
                path = self.path.split('?')[0]
                srv.requests.append((path, self.headers.get('Range'), self.headers.get('Authorization')))
                key = path.replace('/redirected', '')
                mode = srv.mode.get(key, 'ok')
                if mode.startswith('redirect') and not path.startswith('/redirected'):
                    host = '127.0.0.1' if mode == 'redirect_ok' else 'localhost'
                    self.send_response(302)
                    self.send_header('Location', f'http://{host}:{srv.port}/redirected{path}?sig=SECRET')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                if mode == 'auth':
                    self.send_response(401)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                data = srv.files.get(key)
                if data is None:
                    self.send_response(404)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                start = 0
                rng = self.headers.get('Range')
                if rng and mode != 'ignore_range':
                    start = int(rng.split('=')[1].rstrip('-'))
                    self.send_response(206)
                else:
                    self.send_response(200)
                body = data[start:] + (b'EXTRA' if mode == 'extra' else b'')
                if mode == 'extra':  # no length: the body runs until close, past the pinned size
                    self.send_header('Connection', 'close')
                    self.close_connection = True
                else:
                    self.send_header('Content-Length', str(len(body) + (7 if mode == 'lie' else 0)))
                self.end_headers()
                try:
                    if mode == 'hang':
                        self.wfile.write(body[:1000])
                        self.wfile.flush()
                        time.sleep(30)
                        return
                    for i in range(0, len(body), 65536):
                        self.wfile.write(body[i:i + 65536])
                        if srv.delay:
                            time.sleep(srv.delay)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Stage(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='stage-'))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.srv = Server()
        self.addCleanup(self.srv.close)
        blob = os.urandom(300_000)
        small = b'{"model_type": "fixture"}\n'
        wheel = os.urandom(50_000)
        self.data = {'model': blob, 'config.json': small, 'w.whl': wheel}
        self.srv.files = {'/hub/org/m/resolve/r1/model.safetensors': blob,
                          '/hub/org/m/resolve/r1/config.json': small, '/pypi/w.whl': wheel}
        self.manifest = {'stagingRoot': str(self.dir / 'rose-cloud'), 'groups': {
            'qwen': {'repo': 'org/m', 'revision': 'r1', 'dir': 'qwen', 'files': [
                ['config.json', len(small), 'gitsha1', gitsha1(small)],
                ['model.safetensors', len(blob), 'sha256', hashlib.sha256(blob).hexdigest()]]},
            'ptts': {'package': 'ptts', 'version': '0', 'dir': 'wheels', 'files': [
                ['w.whl', len(wheel), 'sha256', hashlib.sha256(wheel).hexdigest()]]}}}
        self.limits = {'deadlineS': 30, 'maxBytes': 10 << 20, 'allowHosts': ['127.0.0.1'], 'allowHttp': True,
                       'readTimeoutS': 5, 'hubBase': f'http://127.0.0.1:{self.srv.port}/hub',
                       'sources': {'ptts/w.whl': f'http://127.0.0.1:{self.srv.port}/pypi/w.whl'}}

    def path(self, group, name):
        return Path(self.manifest['stagingRoot'], self.manifest['groups'][group]['dir'], name)

    def run_stage(self, **limits):
        return S.stage(self.manifest, dict(self.limits, **limits))

    def test_downloads_verifies_and_promotes(self):
        r = self.run_stage()
        self.assertEqual(r['stop'], 'done')
        self.assertEqual(self.path('qwen', 'model.safetensors').read_bytes(), self.data['model'])
        self.assertEqual(self.path('ptts', 'w.whl').read_bytes(), self.data['w.whl'])
        self.assertEqual(r['groups']['qwen'], {'expected': 2, 'present': 0, 'downloaded': 2, 'resumed': 0,
                                               'failed': 0, 'bytesIn': 300_000 + len(self.data['config.json'])})
        self.assertEqual(r['bytesIn'], sum(len(v) for v in self.data.values()))
        self.assertTrue(all(a is None for _, _, a in self.srv.requests))  # never a credential
        again = self.run_stage()  # a second run verifies and keeps everything, fetching nothing
        self.assertEqual((again['stop'], again['bytesIn'], again['groups']['qwen']['present']), ('done', 0, 2))

    def test_existing_file_that_differs_refuses_the_whole_run(self):
        p = self.path('ptts', 'w.whl')
        p.parent.mkdir(parents=True)
        p.write_bytes(b'not the pinned wheel')
        r = self.run_stage()
        self.assertEqual((r['stop'], r['firstFailure']['ptts']), ('refused_existing', 'existing_differs'))
        self.assertEqual(self.srv.requests, [])  # nothing fetched
        self.assertEqual(p.read_bytes(), b'not the pinned wheel')  # nothing changed

    def test_resume_with_range_and_preserve_partial_when_range_is_ignored(self):
        p = self.path('qwen', 'model.safetensors')
        p.parent.mkdir(parents=True)
        Path(str(p) + '.partial').write_bytes(self.data['model'][:100_000])
        r = self.run_stage()
        self.assertEqual((r['stop'], r['groups']['qwen']['resumed']), ('done', 1))
        self.assertIn(('/hub/org/m/resolve/r1/model.safetensors', 'bytes=100000-', None), self.srv.requests)
        self.assertEqual(p.read_bytes(), self.data['model'])
        p.unlink()
        partial = Path(str(p) + '.partial')
        partial.write_bytes(self.data['model'][:100_000])
        self.srv.mode['/hub/org/m/resolve/r1/model.safetensors'] = 'ignore_range'
        r = self.run_stage()
        self.assertEqual((r['stop'], r['firstFailure']['qwen']), ('file_failures', 'range_refused'))
        self.assertEqual(partial.read_bytes(), self.data['model'][:100_000])  # preserved, untouched
        self.assertFalse(p.exists())

    def test_hash_mismatch_is_never_promoted(self):
        self.srv.files['/hub/org/m/resolve/r1/model.safetensors'] = os.urandom(300_000)  # same size, other bytes
        r = self.run_stage()
        self.assertEqual((r['stop'], r['firstFailure']['qwen']), ('file_failures', 'hash_mismatch'))
        self.assertFalse(self.path('qwen', 'model.safetensors').exists())
        self.assertEqual(Path(str(self.path('qwen', 'model.safetensors')) + '.partial').stat().st_size, 300_000)
        self.assertTrue(self.path('ptts', 'w.whl').exists())  # other files still staged

    def test_redirects_auth_and_size_lies(self):
        key = '/hub/org/m/resolve/r1/model.safetensors'
        self.srv.mode[key] = 'redirect_ok'
        self.assertEqual(self.run_stage()['stop'], 'done')  # an allowed host after a redirect
        self.path('qwen', 'model.safetensors').unlink()
        for mode, failure in (('redirect_bad', 'redirect_refused'), ('auth', 'auth_required'),
                              ('lie', 'size_mismatch'), ('extra', 'size_mismatch')):
            with self.subTest(mode):
                self.srv.mode[key] = mode
                self.srv.requests.clear()
                r = self.run_stage()
                self.assertEqual((r['stop'], r['firstFailure']['qwen']), ('file_failures', failure))
                self.assertFalse(self.path('qwen', 'model.safetensors').exists())
                if mode == 'redirect_bad':
                    self.assertNotIn('/redirected' + key, [p for p, _, _ in self.srv.requests])  # never followed
                part = Path(str(self.path('qwen', 'model.safetensors')) + '.partial')
                if part.exists():
                    self.assertLessEqual(part.stat().st_size, 300_000)
                    part.unlink()

    def test_only_allowed_hosts_and_https(self):
        f = S.Fetcher({'deadlineS': 1, 'maxBytes': 1})
        for url in ('http://huggingface.co/x', 'https://evil.example/x', 'https://user:pw@huggingface.co/x',
                    'https://cdn-lfs.hf.co/x', 'ftp://files.pythonhosted.org/x'):
            with self.assertRaises(S.FileFailure):
                f.check(url)
        for url in ('https://huggingface.co/x', 'https://us.aws.cdn.hf.co/x', 'https://files.pythonhosted.org/x'):
            f.check(url)

    def test_us_gcp_cdn_is_allowed_exactly_and_nothing_else_was_added(self):
        self.assertEqual(S.ALLOW_HOSTS, ('huggingface.co', 'us.aws.cdn.hf.co', 'us.gcp.cdn.hf.co',
                                         'files.pythonhosted.org'))
        f = S.Fetcher({'deadlineS': 1, 'maxBytes': 1})
        for url in ('https://us.gcp.cdn.hf.co/xet-bridge-us/abc?X-Amz-Signature=SECRET',
                    'https://US.GCP.CDN.HF.CO./x', 'https://us.gcp.cdn.hf.co:443/x'):
            f.check(url)
        self.assertEqual(f.refusals(), ([], 0))
        refused = (('http://us.gcp.cdn.hf.co/x', 'us.gcp.cdn.hf.co', 'scheme_not_https'),
                   ('https://user:SECRET@us.gcp.cdn.hf.co/x', 'us.gcp.cdn.hf.co', 'userinfo'),
                   ('https://x.us.gcp.cdn.hf.co/x', 'x.us.gcp.cdn.hf.co', 'host_not_allowed'),
                   ('https://us.gcp.cdn.hf.co.evil.example/x', 'us.gcp.cdn.hf.co.evil.example', 'host_not_allowed'),
                   ('https://evil-us.gcp.cdn.hf.co/x', 'evil-us.gcp.cdn.hf.co', 'host_not_allowed'),
                   ('https://eu.gcp.cdn.hf.co/x', 'eu.gcp.cdn.hf.co', 'host_not_allowed'),
                   ('https://us.gcp.cdn.hf.com/x', 'us.gcp.cdn.hf.com', 'host_not_allowed'),
                   ('https://gcp.cdn.hf.co/x', 'gcp.cdn.hf.co', 'host_not_allowed'),
                   ('https://cas-bridge.xethub.hf.co/x', 'cas-bridge.xethub.hf.co', 'host_not_allowed'))
        for url, host, reason in refused:
            with self.subTest(url=url):
                f.refused.clear()
                with self.assertRaises(S.FileFailure) as e:
                    f.check(url)
                self.assertEqual(str(e.exception), 'redirect_refused')
                self.assertEqual(f.refusals()[0], [{'host': host, 'reason': reason, 'count': 1}])
                self.assertNotIn('SECRET', json.dumps(f.refusals()))

    def test_byte_cap_and_deadline_keep_partials(self):
        r = self.run_stage(maxBytes=150_000)
        self.assertEqual(r['stop'], 'byte_cap')
        self.assertLessEqual(r['bytesIn'], 150_000)
        part = Path(str(self.path('qwen', 'model.safetensors')) + '.partial')
        self.assertTrue(part.exists() and 0 < part.stat().st_size < 300_000)
        size = part.stat().st_size
        self.srv.delay = 0.8  # 64 KiB per 0.8 s: the remaining ~150 KB takes ~2 s
        r = self.run_stage(deadlineS=1)
        self.assertEqual(r['stop'], 'deadline')
        self.assertGreaterEqual(part.stat().st_size, size)  # kept, and only ever grown
        self.srv.delay = 0
        self.assertEqual(self.run_stage()['stop'], 'done')  # and the next run finishes from it

    def test_insufficient_space_refuses_before_fetching(self):
        real = os.statvfs

        class Tiny:
            f_bavail, f_frsize = 10, 4096
        S.os.statvfs = lambda p: Tiny
        try:
            r = self.run_stage()
        finally:
            S.os.statvfs = real
        self.assertEqual((r['stop'], r['needBytes']), ('insufficient_space', sum(len(v) for v in self.data.values())))
        self.assertEqual(self.srv.requests, [])

    def test_receipt_is_fixed_tokens_and_bounded(self):
        self.srv.mode['/hub/org/m/resolve/r1/model.safetensors'] = 'redirect_bad'
        out = []
        real = sys.stdout.write
        try:
            sys.stdout.write = out.append
            S.main([json.dumps(self.manifest), json.dumps(self.limits)])
        finally:
            sys.stdout.write = real
        line = ''.join(out).strip()
        self.assertTrue(line.startswith(S.MARKER))
        for secret in ('SECRET', 'sig=', '/redirected', str(self.srv.port), '127.0.0.1', str(self.dir), 'http'):
            self.assertNotIn(secret, line)  # the refused HOST is reported (next test); nothing else of the URL
        r = S.sanitize(json.loads(line[len(S.MARKER):]), self.manifest)
        self.assertEqual(r['firstFailure']['qwen'], 'redirect_refused')
        self.assertLess(len(json.dumps(r)), 2048)
        hostile = S.sanitize({'stop': 'rm -rf', 'bytesIn': -1, 'groups': {'qwen': {'failed': 'x'}},
                              'firstFailure': {'qwen': 'https://evil/SECRET'}}, self.manifest)
        self.assertEqual((hostile['stop'], hostile['bytesIn'], hostile['groups']['qwen']['failed'],
                          hostile['firstFailure']['qwen']), (None, None, None, None))

    def test_refused_redirect_reports_the_sanitized_host_and_reason(self):
        key = '/hub/org/m/resolve/r1/model.safetensors'
        self.srv.mode[key] = 'redirect_bad'  # 302 to http://localhost:<port>/redirected...?sig=SECRET
        r = self.run_stage()
        self.assertEqual(r['firstFailure']['qwen'], 'redirect_refused')
        self.assertEqual((r['refusedHosts'], r['refusedOther']),
                         ([{'host': 'localhost', 'reason': 'host_not_allowed', 'count': 1}], 0))
        self.assertNotIn('/redirected' + key, [p for p, _, _ in self.srv.requests])  # still never followed
        self.assertEqual(S.sanitize(r, self.manifest)['refusedHosts'], r['refusedHosts'])

    def test_refusal_reasons_and_host_normalization(self):
        f = S.Fetcher({'deadlineS': 1, 'maxBytes': 1})
        cases = (
            ('https://cas-bridge.xethub.hf.co/x/y?X-Amz-Signature=SECRET#frag', 'cas-bridge.xethub.hf.co',
             'host_not_allowed'),
            ('https://EVIL.Example./a', 'evil.example', 'host_not_allowed'),
            ('http://huggingface.co/x', 'huggingface.co', 'scheme_not_https'),
            ('https://user:SECRET@huggingface.co/x', 'huggingface.co', 'userinfo'),
            ('https://:SECRET@evil.example/x', 'evil.example', 'userinfo'),
            ('ftp://files.pythonhosted.org/x', 'files.pythonhosted.org', 'scheme_not_https'),
            ('https://[2001:DB8::1]/x', '2001:db8::1', 'host_not_allowed'),
            ('https://10.0.0.7:8443/x', '10.0.0.7', 'host_not_allowed'),
            ('https://xn--bcher-kva.example/x', 'xn--bcher-kva.example', 'host_not_allowed'),
            ('https://bad_host!/SECRET', None, 'malformed'),
            ('https://%65vil.example/x', None, 'malformed'),
            ('https://huggingface.co:99999/x', None, 'malformed'),
            ('https://' + 'a' * 64 + '.example/x', None, 'malformed'),
            ('https://' + '.'.join(['a' * 63] * 5) + '/x', None, 'malformed'),
            ('https://[::1/x', None, 'malformed'),
            ('https://[fe80::1%25SECRET]/x', None, 'malformed'),  # a zone id is free text, never kept
            ('https://[fe80::1%SECRET]/x', None, 'malformed'),
            ('/relative/SECRET', None, 'malformed'),
            ('https://h.example/' + 'x' * 9000, None, 'malformed'),
        )
        for url, host, reason in cases:
            with self.subTest(url=url[:60]):
                f.refused.clear()
                with self.assertRaises(S.FileFailure) as e:
                    f.check(url)
                self.assertEqual(str(e.exception), 'redirect_refused')  # same refusal as before
                self.assertEqual(f.refusals()[0], [{'host': host, 'reason': reason, 'count': 1}])
                self.assertNotIn('SECRET', json.dumps(f.refusals()))
        with self.assertRaises(S.FileFailure):
            f.check(None)
        f.refused.clear()
        f.check('https://huggingface.co/ok')  # allowed: nothing recorded
        self.assertEqual(f.refusals(), ([], 0))

    def test_refusal_collection_is_bounded(self):
        f = S.Fetcher({'deadlineS': 1, 'maxBytes': 1})
        for i in range(7):
            for _ in range(i + 1):
                with self.assertRaises(S.FileFailure):
                    f.check(f'https://h{i}.example/x')
        hosts, other = f.refusals()
        self.assertEqual([h['host'] for h in hosts], ['h3.example', 'h2.example', 'h1.example', 'h0.example'])
        self.assertEqual([h['count'] for h in hosts], [4, 3, 2, 1])
        self.assertEqual(other, 5 + 6 + 7)  # the hosts past the first four, counted only

    def test_sanitize_drops_anything_that_is_not_a_host(self):
        raw = {'stop': 'file_failures', 'refusedOther': 2, 'refusedHosts': [
            {'host': 'https://evil.example/x?sig=SECRET', 'reason': 'host_not_allowed', 'count': 1},
            {'host': 'user:SECRET@evil.example', 'reason': 'userinfo', 'count': 1},
            {'host': 'evil.example:443', 'reason': 'host_not_allowed', 'count': 1},
            {'host': 'fe80::1%SECRET' + 'z' * 5000, 'reason': 'host_not_allowed', 'count': 1},
            {'host': 'ok.example', 'reason': 'not_a_reason', 'count': 1},
            {'host': 'ok.example', 'reason': 'host_not_allowed', 'count': -3},
            {'host': 'CDN.Example.', 'reason': 'host_not_allowed', 'count': 45},
            {'host': None, 'reason': 'malformed', 'count': 2},
            'https://SECRET',
        ] + [{'host': f'x{i}.example', 'reason': 'host_not_allowed', 'count': 1} for i in range(10)]}
        r = S.sanitize(raw, self.manifest)
        self.assertEqual(r['refusedHosts'][:2], [{'host': 'cdn.example', 'reason': 'host_not_allowed', 'count': 45},
                                                 {'host': None, 'reason': 'malformed', 'count': 2}])
        self.assertEqual(len(r['refusedHosts']), S.MAX_REFUSED_HOSTS)
        self.assertEqual(r['refusedOther'], 2)
        self.assertNotIn('SECRET', json.dumps(r))
        self.assertNotIn(':443', json.dumps(r))
        self.assertEqual(S.sanitize({'refusedHosts': 'https://SECRET'}, self.manifest)['refusedHosts'], [])
        worst = S.sanitize({'stop': 'file_failures', 'refusedOther': 1 << 40, 'refusedHosts': [
            {'host': '.'.join(['a' * 63] * 3) + '.' + 'b' * 61, 'reason': 'host_not_allowed', 'count': 1 << 40}] * 9},
            MANIFEST)
        self.assertEqual(len(worst['refusedHosts']), 4)
        self.assertLess(len(json.dumps(worst, separators=(',', ':'))), 2600)  # leaves room in the 4 KiB receipt

    def test_hung_transfer_is_killed_by_the_bootstrap_deadline_and_partial_kept(self):
        import bootstrap as B
        self.srv.mode['/hub/org/m/resolve/r1/model.safetensors'] = 'hang'
        argv = [sys.executable, '-c', SOURCE, json.dumps(self.manifest), json.dumps(dict(self.limits, readTimeoutS=60))]
        t = time.monotonic()
        code, out = B.Host().run(argv, 3)
        self.assertEqual(code, 124)
        self.assertLess(time.monotonic() - t, 6)
        part = Path(str(self.path('qwen', 'model.safetensors')) + '.partial')
        self.assertTrue(part.exists())  # preserved for the next run

    def test_plan_for_the_real_manifest(self):
        urls = [S.url_for(n, g, rel, {}) for n, g in MANIFEST['groups'].items() for rel, *_ in g['files']]
        self.assertEqual(len(urls), 59)
        self.assertEqual(sum(f[1] for g in MANIFEST['groups'].values() for f in g['files']), 37603795413)
        f = S.Fetcher({'deadlineS': 1, 'maxBytes': 1})
        for u in urls:
            f.check(u)  # every first request is https on an allowed host
        self.assertIn('https://huggingface.co/Qwen/Qwen3.6-35B-A3B-FP8/resolve/'
                      '95a723d08a9490559dae23d0cff1d9466213d989/config.json', urls)
        self.assertEqual(urls[-1], S.SOURCES[('ptts', MANIFEST['groups']['ptts']['files'][0][0])])
        self.assertTrue(hashlib.sha256((ROOT / 'rose_cloud' / 'manifest.json').read_bytes()).hexdigest()
                        .startswith('ebcfb082'))

    def test_stdlib_only_at_module_level(self):
        import ast
        top = [n for n in ast.parse(SOURCE).body if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertEqual({a.name for n in top for a in n.names},
                         {'hashlib', 'ipaddress', 'json', 'os', 're', 'sys', 'time'})


if __name__ == '__main__':
    unittest.main()
