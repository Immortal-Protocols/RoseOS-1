"""GitHub release publisher against a local fake API: token handling, draft reconcile, conflict refusal."""
import hashlib
import http.server
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.parse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import github_release_publisher as gp

TOKEN = 'ghs_synthetic_publisher_token'

class FakeGitHub(http.server.ThreadingHTTPServer):
    def __init__(self):
        super().__init__(('127.0.0.1', 0), Handler)
        self.releases, self.next_id, self.log, self.blobs, self.fail = [], 1, [], {}, None

    def url(self):
        return f'http://127.0.0.1:{self.server_address[1]}'

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else b''
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_any(self):
        s, path = self.server, urllib.parse.urlsplit(self.path)
        s.log.append((self.command, path.path))
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        if path.path.startswith('/storage/'):
            if self.headers.get('Authorization'):
                return self.reply(400, {'message': 'token forwarded to storage'})
            data = s.blobs[int(path.path.rsplit('/', 1)[1])]
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if self.headers.get('Authorization') != 'Bearer ' + TOKEN:
            return self.reply(401, {'message': 'Bad credentials'})
        if s.fail:
            return self.reply(500, {'message': 'internal SECRETBODY ' + TOKEN})
        base = '/repos/Immortal-Protocols/pixel-ota/releases'
        find = lambda rid: next(r for r in s.releases if r['id'] == rid)
        if self.command == 'GET' and path.path == base:
            return self.reply(200, s.releases)
        if self.command == 'POST' and path.path == base:
            data = json.loads(body)
            release = {'id': s.next_id, 'tag_name': data['tag_name'], 'draft': data['draft'], 'assets': []}
            s.next_id += 1
            s.releases.append(release)
            return self.reply(201, release)
        if self.command == 'POST' and path.path.startswith(base + '/') and path.path.endswith('/assets'):
            release = find(int(path.path.split('/')[-2]))
            name = urllib.parse.parse_qs(path.query)['name'][0]
            aid = s.next_id
            s.next_id += 1
            s.blobs[aid] = body
            release['assets'].append({'id': aid, 'name': name, 'size': len(body), 'state': 'uploaded',
                'digest': 'sha256:' + hashlib.sha256(body).hexdigest()})
            return self.reply(201, release['assets'][-1])
        if self.command == 'GET' and path.path.startswith(base + '/assets/'):
            return self.reply(302, headers={'Location': s.url() + '/storage/' + path.path.rsplit('/', 1)[1]})
        if self.command == 'DELETE' and path.path.startswith(base + '/assets/'):
            aid = int(path.path.rsplit('/', 1)[1])
            for r in s.releases:
                r['assets'] = [a for a in r['assets'] if a['id'] != aid]
            return self.reply(204)
        if self.command == 'GET' and path.path.startswith(base + '/'):
            return self.reply(200, find(int(path.path.rsplit('/', 1)[1])))
        if self.command == 'PATCH' and path.path.startswith(base + '/'):
            release = find(int(path.path.rsplit('/', 1)[1]))
            release['draft'] = json.loads(body)['draft']
            return self.reply(200, release)
        return self.reply(404, {'message': 'not found'})

    do_GET = do_POST = do_DELETE = do_PATCH = handle_any

class PublisherTest(unittest.TestCase):
    def setUp(self):
        self.server = FakeGitHub()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.assets = self.root/'assets'
        self.assets.mkdir()
        rows = []
        for name in ('frankel-ota_update-2026102901.zip', 'frankel-stable', 'releasekey.x509.pem', 'avb_pkmd.bin',
                     'SHA256SUMS', 'UPDATE.md', 'verification.json'):
            data = (name * 50).encode()
            (self.assets/name).write_bytes(data)
            rows.append({'name': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        self.manifest = {'assets': rows}
        self.client = gp.GitHubReleases('Immortal-Protocols/pixel-ota', TOKEN, self.server.url(), self.server.url(), allow_http=True)
        self.runs = 0

    def publish(self):
        self.runs += 1
        return gp.publish_release(self.client, 'frankel-2026102901', 'frankel-2026102901', 'notes', self.manifest,
            self.assets, self.root/f'download-{self.runs}')

    def mutations(self):
        return [entry for entry in self.server.log if entry[0] in ('POST', 'DELETE', 'PATCH')]

    def test_creates_draft_uploads_exact_set_and_verifies_downloads_without_promoting(self):
        release, download, already = self.publish()
        self.assertFalse(already)
        self.assertTrue(self.server.releases[0]['draft'])
        self.assertEqual(sorted(p.name for p in download.iterdir()), sorted(r['name'] for r in self.manifest['assets']))
        self.assertEqual(len([m for m in self.mutations() if m[1].endswith('/assets')]), 7)
        promoted = self.client.promote(release['id'])
        self.assertFalse(promoted['draft'])

    def test_rerun_on_complete_draft_uploads_nothing(self):
        self.publish()
        before = len(self.mutations())
        self.publish()
        self.assertEqual(len(self.mutations()), before)

    def test_draft_reconcile_replaces_a_wrong_asset_and_removes_extras(self):
        self.publish()
        draft = self.server.releases[0]
        wrong = next(a for a in draft['assets'] if a['name'] == 'UPDATE.md')
        wrong['digest'] = 'sha256:' + '0' * 64
        draft['assets'].append({'id': 999, 'name': 'extra.bin', 'size': 1, 'state': 'uploaded'})
        self.publish()
        names = sorted(a['name'] for a in self.server.releases[0]['assets'])
        self.assertEqual(names, sorted(r['name'] for r in self.manifest['assets']))
        self.assertIn(('DELETE', f'/repos/Immortal-Protocols/pixel-ota/releases/assets/{wrong["id"]}'), self.server.log)

    def test_mismatched_published_release_is_never_modified(self):
        release, _, _ = self.publish()
        self.client.promote(release['id'])
        self.server.releases[0]['assets'][0]['digest'] = 'sha256:' + '1' * 64
        before = len(self.mutations())
        with self.assertRaisesRegex(gp.PublishError, 'is not modified'):
            self.publish()
        self.assertEqual(len(self.mutations()), before)

    def test_identical_published_release_is_an_idempotent_verified_no_op(self):
        release, _, _ = self.publish()
        self.client.promote(release['id'])
        before = len(self.mutations())
        _, download, already = self.publish()
        self.assertTrue(already)
        self.assertEqual(len(self.mutations()), before)
        self.assertEqual(len(list(download.iterdir())), 7)

    def test_errors_are_sanitized_and_token_never_reaches_storage(self):
        self.server.fail = True
        with self.assertRaises(gp.PublishError) as caught:
            self.publish()
        message = str(caught.exception)
        self.assertEqual(message, 'GET list releases returned HTTP 500')
        self.assertNotIn(TOKEN, message)
        self.assertNotIn('SECRETBODY', message)
        self.server.fail = None
        self.publish()  # downloads pass only because storage requests carry no Authorization header
        bad = gp.GitHubReleases('Immortal-Protocols/pixel-ota', 'wrong', self.server.url(), self.server.url(), allow_http=True)
        with self.assertRaisesRegex(gp.PublishError, 'HTTP 401'):
            bad.find_release('frankel-2026102901')

    def test_refuses_plain_http_and_missing_token_outside_fixtures(self):
        with self.assertRaisesRegex(gp.PublishError, 'https'):
            gp.GitHubReleases('Immortal-Protocols/pixel-ota', TOKEN, 'http://example.invalid', 'https://uploads.github.com')
        with self.assertRaisesRegex(gp.PublishError, 'no publisher token'):
            gp.GitHubReleases('Immortal-Protocols/pixel-ota', '')

if __name__ == '__main__':
    unittest.main()
