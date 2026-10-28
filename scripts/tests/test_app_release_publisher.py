"""app_release_publisher.py and the app-publisher job against a local fake GitHub (releases API, uploads, storage
redirects and the public github.com download route): artifact and signer verification before any API call, a
separate prerelease that never becomes Latest, idempotent reruns, refusal to modify a different release, sanitized
public files and no token or response body in any output. Synthetic APKs carry a real-format v2 signing block."""
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
import app_release_publisher as arp  # noqa: E402
import github_release_publisher as gp  # noqa: E402

TOKEN = 'ghs_synthetic_app_publisher_token'
FRANKEL = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
SIGNER = FRANKEL['identities']['appSignerSha256'].lower()
CANDIDATE = 'f' * 40
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
REPO = '/repos/Immortal-Protocols/pixel-ota'

def signing_block(certificate, scheme=0x7109871A):
    lp = lambda b: struct.pack('<I', len(b)) + b
    signed = lp(lp(lp(b'digest'))) + lp(lp(certificate)) + lp(b'')
    signer = lp(signed) + lp(lp(b'signature')) + lp(b'public-key')
    value = lp(lp(signer))
    pairs = struct.pack('<QI', len(value) + 4, scheme) + value
    size = len(pairs) + 8 + 16
    return struct.pack('<Q', size) + pairs + struct.pack('<Q', size) + b'APK Sig Block 42'

def apk_bytes(certificate, content=b'rose agent 304', scheme=0x7109871A, block=True):
    """A ZIP with an APK signing block between the entries and the central directory, as apksigner writes it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('AndroidManifest.xml', content)
    data = buffer.getvalue()
    eocd = data.rfind(b'PK\x05\x06')
    directory = struct.unpack_from('<I', data, eocd + 16)[0]
    if not block:
        return data
    inserted = signing_block(certificate, scheme)
    tail = bytearray(data[directory:])
    struct.pack_into('<I', tail, eocd - directory + 16, directory + len(inserted))
    return data[:directory] + inserted + bytes(tail)

class FakeGitHub(http.server.ThreadingHTTPServer):
    def __init__(self):
        super().__init__(('127.0.0.1', 0), Handler)
        self.releases, self.blobs, self.next_id, self.log, self.latest, self.fail = [], {}, 1, [], 'frankel-2026102804', None
        self.move_latest_on_publish = False

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
        s, url = self.server, urllib.parse.urlsplit(self.path)
        s.log.append((self.command, url.path, bool(self.headers.get('Authorization'))))
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        find = lambda rid: next(r for r in s.releases if r['id'] == rid)
        if url.path.startswith('/storage/'):
            if self.headers.get('Authorization'):
                return self.reply(400, {'message': 'token forwarded to storage'})
            data = s.blobs[int(url.path.rsplit('/', 1)[1])]
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        public = re.fullmatch(r'/Immortal-Protocols/pixel-ota/releases/download/([^/]+)/([^/]+)', url.path)
        if public:  # anonymous github.com route: published releases only
            if self.headers.get('Authorization'):
                return self.reply(400, {'message': 'token sent to the public route'})
            release = next((r for r in s.releases if r['tag_name'] == public[1] and not r['draft']), None)
            asset = next((a for a in (release or {}).get('assets', []) if a['name'] == public[2]), None)
            return self.reply(302, headers={'Location': f'{s.url()}/storage/{asset["id"]}'}) if asset else self.reply(404, {})
        if self.headers.get('Authorization') != 'Bearer ' + TOKEN:
            return self.reply(401, {'message': 'Bad credentials'})
        if s.fail:
            return self.reply(500, {'message': 'internal SECRETBODY ' + TOKEN})
        if self.command == 'GET' and url.path == REPO + '/releases/latest':
            return self.reply(200, {'tag_name': s.latest}) if s.latest else self.reply(404, {})
        if self.command == 'GET' and url.path == REPO + '/releases':
            return self.reply(200, s.releases)
        if self.command == 'POST' and url.path == REPO + '/releases':
            data = json.loads(body)
            release = {'id': s.next_id, 'tag_name': data['tag_name'], 'draft': data['draft'], 'prerelease': data['prerelease'],
                'make_latest': data['make_latest'], 'assets': []}
            s.next_id += 1
            s.releases.append(release)
            return self.reply(201, release)
        match = re.fullmatch(REPO + r'/releases/(\d+)', url.path)
        if match and self.command == 'GET':
            return self.reply(200, find(int(match[1])))
        if match and self.command == 'PATCH':
            release, data = find(int(match[1])), json.loads(body)
            release.update(draft=data['draft'], prerelease=data['prerelease'], make_latest=data['make_latest'])
            if s.move_latest_on_publish:
                s.latest = release['tag_name']
            return self.reply(200, release)
        match = re.fullmatch(REPO + r'/releases/(\d+)/assets', url.path)
        if match and self.command == 'POST':
            name = urllib.parse.parse_qs(url.query)['name'][0]
            asset = {'id': s.next_id, 'name': name, 'size': len(body), 'state': 'uploaded', 'digest': 'sha256:' + hashlib.sha256(body).hexdigest()}
            s.blobs[s.next_id] = body
            s.next_id += 1
            find(int(match[1]))['assets'].append(asset)
            return self.reply(201, asset)
        match = re.fullmatch(REPO + r'/releases/assets/(\d+)', url.path)
        if match and self.command == 'DELETE':
            for release in s.releases:
                release['assets'] = [a for a in release['assets'] if a['id'] != int(match[1])]
            return self.reply(204)
        if match and self.command == 'GET':
            return self.reply(302, headers={'Location': f'{s.url()}/storage/{match[1]}'})
        return self.reply(404, {'message': 'not found'})

    do_GET = do_POST = do_PATCH = do_DELETE = handle_any

class PublisherTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.server = FakeGitHub()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        config = json.loads(json.dumps(FRANKEL))
        config['fixture'] = True
        config['publication'].update(api=self.server.url(), uploads=self.server.url(), web=self.server.url())
        self.config_path = self.root/'frankel.json'
        self.config_path.write_text(json.dumps(config))
        self.config = config
        self.certificate = b'synthetic DER certificate'
        self.signer = hashlib.sha256(self.certificate).hexdigest()
        config['identities']['appSignerSha256'] = self.signer
        self.config_path.write_text(json.dumps(config))
        self.make_artifact()

    def make_artifact(self, apk=None, edit=lambda r: r, name='artifact', sums=None, extra=None):
        directory = self.root/name
        directory.mkdir()
        data = apk if apk is not None else apk_bytes(self.certificate)
        (directory/'dev.pixel.voice-304.apk').write_bytes(data)
        receipt = edit({'receiptType': 'app-release', 'simulated': False, 'candidate': CANDIDATE, 'packageName': 'dev.pixel.voice',
            'versionCode': 304, 'versionName': '304-app-beta', 'apk': 'dev.pixel.voice-304.apk', 'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(), 'signerSha256': self.signer, 'minimumTestedOs': FRANKEL['appRelease']['minimumTestedOs'],
            'checks': ['builder checks']})
        (directory/'app-release-receipt.json').write_text(json.dumps(receipt))
        if extra:
            (directory/extra).write_text('extra')
        names = sums or ['app-release-receipt.json', 'dev.pixel.voice-304.apk']
        (directory/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256((directory/n).read_bytes()).hexdigest()}  {n}\n' for n in names))
        self.artifact = directory
        return directory

    def run_publisher(self, apps='[["dev.pixel.voice", 304]]', public='public', token=TOKEN, candidate=CANDIDATE):
        env = dict(os.environ, PIXEL_OTA_TOKEN=token)
        return subprocess.run([sys.executable, str(SCRIPTS/'app_release_publisher.py'), '--artifact-dir', str(self.artifact),
            '--candidate-sha', candidate, '--apps', apps, '--public-dir', str(self.root/public), '--config', str(self.config_path)],
            capture_output=True, text=True, env=env)

    def api_writes(self):
        return [entry for entry in self.server.log if entry[0] in ('POST', 'PATCH', 'DELETE')]

    def test_publishes_a_separate_prerelease_with_only_the_allowlisted_files(self):
        result = self.run_publisher()
        self.assertEqual(result.returncode, 0, result.stderr)
        release, = self.server.releases
        self.assertEqual((release['tag_name'], release['draft'], release['prerelease'], release['make_latest']),
            ('rose-agent-304', False, True, 'false'))
        self.assertEqual(sorted(a['name'] for a in release['assets']), ['SHA256SUMS', 'dev.pixel.voice-304.apk', 'dev.pixel.voice-304.receipt.json'])
        self.assertEqual(self.server.latest, 'frankel-2026102804', 'OTA Latest is untouched')
        apk = (self.artifact/'dev.pixel.voice-304.apk').read_bytes()
        published = {a['name']: self.server.blobs[a['id']] for a in release['assets']}
        self.assertEqual(published['dev.pixel.voice-304.apk'], apk)
        public = json.loads(published['dev.pixel.voice-304.receipt.json'])
        self.assertEqual(public, {'receiptType': 'app-publication', 'packageName': 'dev.pixel.voice', 'versionCode': 304,
            'versionName': '304-app-beta', 'size': len(apk), 'sha256': hashlib.sha256(apk).hexdigest(), 'signerSha256': self.signer,
            'candidate': CANDIDATE, 'minimumTestedOs': FRANKEL['appRelease']['minimumTestedOs'], 'repository': 'Immortal-Protocols/pixel-ota',
            'tag': 'rose-agent-304', 'asset': 'dev.pixel.voice-304.apk', 'prerelease': True,
            'apkUrl': 'https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/dev.pixel.voice-304.apk'})
        self.assertNotIn('checks', public, 'only the allowlisted public fields')
        self.assertEqual(published['SHA256SUMS'].decode(), ''.join(f'{hashlib.sha256(published[n]).hexdigest()}  {n}\n'
            for n in ('dev.pixel.voice-304.apk', 'dev.pixel.voice-304.receipt.json')))
        # The public route was fetched anonymously; the token never reached storage or the public route.
        self.assertIn(('GET', '/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/dev.pixel.voice-304.apk', False), self.server.log)
        self.assertFalse([e for e in self.server.log if (e[1].startswith('/storage/') or e[1].startswith('/Immortal-Protocols/')) and e[2]])
        self.assertEqual(json.loads(result.stdout), public)
        self.assertEqual(json.loads((self.root/'public/publication.json').read_text()), {**public, 'releaseId': release['id']})
        self.assertEqual(sorted(p.name for p in (self.root/'public/assets').iterdir()), sorted(published))
        for text in (result.stdout, result.stderr, *(p.read_text() for p in (self.root/'public').glob('*.json'))):
            self.assertNotIn(TOKEN, text)
            self.assertNotIn(str(self.root), text, 'no internal paths')

    def test_rerun_is_idempotent_and_a_different_published_release_is_never_modified(self):
        self.assertEqual(self.run_publisher().returncode, 0)
        writes = len(self.api_writes())
        rerun = self.run_publisher(public='public2')
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        self.assertEqual(len(self.api_writes()), writes, 'identical verified content: no writes')
        self.server.releases[0]['assets'][0]['size'] += 1
        before = json.dumps(self.server.releases)
        refused = self.run_publisher(public='public3')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('published release rose-agent-304 differs from this app release; it is not modified', refused.stderr)
        self.assertEqual(json.dumps(self.server.releases), before)

    def test_a_leftover_draft_is_reconciled(self):
        self.server.releases.append({'id': 99, 'tag_name': 'rose-agent-304', 'draft': True, 'prerelease': True, 'make_latest': 'false',
            'assets': [{'id': 98, 'name': 'dev.pixel.voice-304.apk', 'size': 3, 'state': 'uploaded', 'digest': 'sha256:' + '0' * 64},
                       {'id': 97, 'name': 'unrelated.txt', 'size': 1, 'state': 'uploaded', 'digest': 'sha256:' + '1' * 64}]})
        self.server.blobs.update({98: b'old', 97: b'x'})
        result = self.run_publisher()
        self.assertEqual(result.returncode, 0, result.stderr)
        release, = self.server.releases
        self.assertEqual((release['draft'], sorted(a['name'] for a in release['assets'])),
            (False, ['SHA256SUMS', 'dev.pixel.voice-304.apk', 'dev.pixel.voice-304.receipt.json']))
        self.assertIn(('DELETE', REPO + '/releases/assets/98', True), self.server.log)
        self.assertIn(('DELETE', REPO + '/releases/assets/97', True), self.server.log)

    def test_artifact_and_signer_failures_stop_before_any_api_call(self):
        other = apk_bytes(b'another certificate')
        cases = (
            (dict(edit=lambda r: {**r, 'sha256': '0' * 64}), "sha256="),
            (dict(edit=lambda r: {**r, 'size': r['size'] + 1}), 'size='),
            (dict(edit=lambda r: {**r, 'packageName': 'dev.pixel.other'}), 'packageName='),
            (dict(edit=lambda r: {**r, 'versionCode': 303}), 'versionCode='),
            (dict(edit=lambda r: {**r, 'signerSha256': 'cd' * 32}), 'signerSha256='),
            (dict(edit=lambda r: {**r, 'simulated': True}), 'simulated='),
            (dict(edit=lambda r: {**r, 'candidate': 'e' * 40}), 'candidate='),
            (dict(apk=other), 'not signed by exactly the pinned app signer'),
            (dict(apk=apk_bytes(b'', block=False)), 'no APK signing block'),
            (dict(apk=apk_bytes(self.certificate, scheme=0x42726577)), 'no v2 or v3 signature'),
            (dict(extra='config.json'), 'not exactly'),
            (dict(sums=['dev.pixel.voice-304.apk']), 'SHA256SUMS does not cover exactly'),
        )
        for index, (kwargs, message) in enumerate(cases):
            self.make_artifact(name=f'bad{index}', **kwargs)
            result = self.run_publisher(public=f'p{index}')
            self.assertEqual(result.returncode, 1, kwargs)
            self.assertIn(message, result.stderr, kwargs)
            self.assertEqual(self.server.log, [], 'nothing reached the API')
            self.assertFalse((self.root/f'p{index}').exists())
        self.make_artifact(name='v3', apk=apk_bytes(self.certificate, scheme=0xF05368C0))
        self.assertEqual(self.run_publisher(public='v3').returncode, 0, 'a v3-only signing block is read too')

    def test_wrong_dispatch_wrong_candidate_or_missing_token_fail_closed(self):
        for apps, message in (('[["dev.pixel.voice", 303]]', 'with N above 303'), ('[["dev.pixel.voice", 304], ["dev.pixel.backgroundcontrol", 28]]', 'exactly'),
                              ('[["dev.pixel.calculator.agent", 5]]', 'exactly')):
            result = self.run_publisher(apps=apps, public='x')
            self.assertEqual(result.returncode, 1)
            self.assertIn(message, result.stderr)
        result = self.run_publisher(candidate='abc', public='y')
        self.assertIn('full candidate SHA', result.stderr)
        missing = self.run_publisher(token='', public='z')
        self.assertEqual((missing.returncode, json.loads(missing.stderr)['verdict']), (4, 'SETUP_MISSING'))
        self.assertEqual(self.server.log, [])

    def test_latest_must_stay_the_ota_release(self):
        self.server.latest = 'frankel-2026102803'
        result = self.run_publisher()
        self.assertEqual(result.returncode, 1)
        self.assertIn('Latest is not frankel-2026102804; nothing is published', result.stderr)
        self.assertEqual(self.api_writes(), [])
        self.server.latest = 'frankel-2026102804'
        self.server.move_latest_on_publish = True
        moved = self.run_publisher(public='public2')
        self.assertEqual(moved.returncode, 1)
        self.assertIn('Latest moved away from frankel-2026102804', moved.stderr)

    def test_api_errors_never_print_bodies_or_the_token(self):
        self.server.fail = True
        result = self.run_publisher()
        self.assertEqual(result.returncode, 1)
        self.assertIn('GET get latest release returned HTTP 500', result.stderr)
        for secret in ('SECRETBODY', TOKEN):
            self.assertNotIn(secret, result.stdout + result.stderr)

    def test_public_download_refuses_an_unexpected_redirect_host_in_production(self):
        client = gp.GitHubReleases('Immortal-Protocols/pixel-ota', TOKEN)
        with self.assertRaisesRegex(gp.PublishError, 'not on github.com'):
            client.public_download('https://example.com/x.apk', self.root/'x', 1)

class WorkflowAppPublisherTest(unittest.TestCase):
    def setUp(self):
        body = WORKFLOW.split('\njobs:\n', 1)[1]
        self.jobs = {m[1]: m[2] for m in re.finditer(r'^  ([\w-]+):\n((?:(?:    .*)?\n)*)', body, re.M)}

    def test_only_app_only_with_app_publish_runs_the_hosted_app_publisher(self):
        job = self.jobs['app-publisher']
        self.assertIn("    needs: app-builder\n    if: inputs.mode == 'app-only' && inputs.app_publish\n    runs-on: ubuntu-24.04\n", job)
        self.assertNotIn('self-hosted', job)
        self.assertIn('          name: app-release-dev.pixel.voice-${{ github.run_id }}\n', job)
        self.assertIn('uses: actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510 # v3.3.0', job)
        self.assertIn('          repositories: pixel-ota\n          permission-contents: write\n', job)
        self.assertIn('PIXEL_OTA_TOKEN: ${{ steps.app.outputs.token }}', job)
        self.assertIn('python3 scripts/app_release_publisher.py --artifact-dir "$RUNNER_TEMP/app-artifact" --candidate-sha "$CANDIDATE_SHA" '
            '--apps "$APPS" --public-dir "$RUNNER_TEMP/app-public"', job)
        for other in ('builder', 'app-builder', 'alpha-builder', 'controller', 'cleanup'):
            self.assertNotIn('PIXEL_OTA_APP_PRIVATE_KEY }}\n', self.jobs[other].replace('!= \'\'', ''), other)
            self.assertNotIn('create-github-app-token', self.jobs[other], other)
        self.assertIn('      app_publish:\n        description: app-only only', WORKFLOW)
        guard = self.jobs['guard']
        self.assertIn('APP_PUBLISH: ${{ inputs.app_publish }}', guard)
        self.assertIn('[[ "$APP_PUBLISH" == false || "$MODE" == app-only ]]', guard)
        self.assertIn("    if: inputs.mode == 'emulator-alpha'\n", self.jobs['alpha-builder'])
        self.assertIn('    needs: builder\n', self.jobs['publisher'])

if __name__ == '__main__':
    unittest.main()
