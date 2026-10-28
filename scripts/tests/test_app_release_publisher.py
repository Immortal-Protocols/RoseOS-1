"""app_release_publisher.py and the app-publisher job against a local fake GitHub (releases API, uploads, storage
redirects and the public github.com download route): artifact and signer verification before any API call, a
separate prerelease that never becomes Latest, idempotent reruns, refusal to modify a different release, sanitized
public files and no token or response body in any output. Synthetic APKs carry a real-format v2 signing block.
The public receipt is also read by the app's own DirectUpdate parser (DirectUpdateReceiptProbe, compiled with javac from
the app sources) so the producer and the direct-GitHub client agree on schemaVersion 1 and its compatibility."""
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import re
import shutil
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

def apk_bytes(certificate, content=b'rose agent 306', scheme=0x7109871A, block=True, libs=()):
    """A ZIP with an APK signing block between the entries and the central directory, as apksigner writes it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('AndroidManifest.xml', content)
        for lib in libs:
            archive.writestr(lib, b'\x7fELF synthetic')
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
        self.releases, self.blobs, self.next_id, self.log, self.latest, self.fail = [], {}, 1, [], 'frankel-2026102805', None
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
        (directory/'dev.pixel.voice-306.apk').write_bytes(data)
        receipt = edit({'receiptType': 'app-release', 'simulated': False, 'candidate': CANDIDATE, 'packageName': 'dev.pixel.voice',
            'versionCode': 306, 'versionName': '306-direct-github-beta', 'apk': 'dev.pixel.voice-306.apk', 'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(), 'signerSha256': self.signer, 'minimumTestedOs': FRANKEL['appRelease']['minimumTestedOs'],
            'apkFacts': {'minSdk': 29, 'nativeAbis': []}, 'checks': ['builder checks']})
        (directory/'app-release-receipt.json').write_text(json.dumps(receipt))
        if extra:
            (directory/extra).write_text('extra')
        names = sums or ['app-release-receipt.json', 'dev.pixel.voice-306.apk']
        (directory/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256((directory/n).read_bytes()).hexdigest()}  {n}\n' for n in names))
        self.artifact = directory
        return directory

    def run_publisher(self, apps='[["dev.pixel.voice", 306]]', public='public', token=TOKEN, candidate=CANDIDATE):
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
            ('rose-agent-306', False, True, 'false'))
        self.assertEqual(sorted(a['name'] for a in release['assets']), ['SHA256SUMS', 'dev.pixel.voice-306.apk', 'dev.pixel.voice-306.receipt.json'])
        self.assertEqual(self.server.latest, 'frankel-2026102805', 'OTA Latest is untouched')
        self.assertEqual(release['make_latest'], 'false')
        apk = (self.artifact/'dev.pixel.voice-306.apk').read_bytes()
        published = {a['name']: self.server.blobs[a['id']] for a in release['assets']}
        self.assertEqual(published['dev.pixel.voice-306.apk'], apk)
        public = json.loads(published['dev.pixel.voice-306.receipt.json'])
        self.assertEqual(public, {'receiptType': 'app-publication', 'schemaVersion': 1, 'packageName': 'dev.pixel.voice', 'versionCode': 306,
            'versionName': '306-direct-github-beta', 'size': len(apk), 'sha256': hashlib.sha256(apk).hexdigest(), 'signerSha256': self.signer,
            'candidate': CANDIDATE, 'minimumTestedOs': FRANKEL['appRelease']['minimumTestedOs'], 'repository': 'Immortal-Protocols/pixel-ota',
            'tag': 'rose-agent-306', 'asset': 'dev.pixel.voice-306.apk', 'prerelease': True,
            'compatibility': {'deviceCodenames': ['frankel'], 'abis': ['universal'], 'minSdk': 29, 'minOsBuild': '2026102804',
                'minControllerVersion': 28},
            'apkUrl': 'https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-306/dev.pixel.voice-306.apk'})
        self.assertNotIn('checks', public, 'only the allowlisted public fields')
        self.assertNotIn('apkFacts', public)
        self.assertEqual(published['SHA256SUMS'].decode(), ''.join(f'{hashlib.sha256(published[n]).hexdigest()}  {n}\n'
            for n in ('dev.pixel.voice-306.apk', 'dev.pixel.voice-306.receipt.json')))
        # The public route was fetched anonymously; the token never reached storage or the public route.
        self.assertIn(('GET', '/Immortal-Protocols/pixel-ota/releases/download/rose-agent-306/dev.pixel.voice-306.apk', False), self.server.log)
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
        self.assertIn('published release rose-agent-306 differs from this app release; it is not modified', refused.stderr)
        self.assertEqual(json.dumps(self.server.releases), before)

    def test_a_leftover_draft_is_reconciled(self):
        self.server.releases.append({'id': 99, 'tag_name': 'rose-agent-306', 'draft': True, 'prerelease': True, 'make_latest': 'false',
            'assets': [{'id': 98, 'name': 'dev.pixel.voice-306.apk', 'size': 3, 'state': 'uploaded', 'digest': 'sha256:' + '0' * 64},
                       {'id': 97, 'name': 'unrelated.txt', 'size': 1, 'state': 'uploaded', 'digest': 'sha256:' + '1' * 64}]})
        self.server.blobs.update({98: b'old', 97: b'x'})
        result = self.run_publisher()
        self.assertEqual(result.returncode, 0, result.stderr)
        release, = self.server.releases
        self.assertEqual((release['draft'], sorted(a['name'] for a in release['assets'])),
            (False, ['SHA256SUMS', 'dev.pixel.voice-306.apk', 'dev.pixel.voice-306.receipt.json']))
        self.assertIn(('DELETE', REPO + '/releases/assets/98', True), self.server.log)
        self.assertIn(('DELETE', REPO + '/releases/assets/97', True), self.server.log)

    def test_artifact_and_signer_failures_stop_before_any_api_call(self):
        other = apk_bytes(b'another certificate')
        cases = (
            (dict(edit=lambda r: {**r, 'sha256': '0' * 64}), "sha256="),
            (dict(edit=lambda r: {**r, 'size': r['size'] + 1}), 'size='),
            (dict(edit=lambda r: {**r, 'packageName': 'dev.pixel.other'}), 'packageName='),
            (dict(edit=lambda r: {**r, 'versionCode': 305}), 'versionCode='),
            (dict(edit=lambda r: {**r, 'signerSha256': 'cd' * 32}), 'signerSha256='),
            (dict(edit=lambda r: {**r, 'simulated': True}), 'simulated='),
            (dict(edit=lambda r: {**r, 'candidate': 'e' * 40}), 'candidate='),
            (dict(apk=other), 'not signed by exactly the pinned app signer'),
            (dict(apk=apk_bytes(b'', block=False)), 'no APK signing block'),
            (dict(apk=apk_bytes(self.certificate, scheme=0x42726577)), 'no v2 or v3 signature'),
            (dict(extra='config.json'), 'not exactly'),
            (dict(sums=['dev.pixel.voice-306.apk']), 'SHA256SUMS does not cover exactly'),
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
        for apps, message in (('[["dev.pixel.voice", 304]]', 'with N above 304'), ('[["dev.pixel.voice", 305]]', 'reserved for the os-release route'),
                              ('[["dev.pixel.voice", 306], ["dev.pixel.backgroundcontrol", 28]]', 'exactly'),
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
        # The verified Beta 2805 is Latest (published 2026-10-28 18:34:04 UTC); the earlier 2804 or any other is refused.
        self.assertEqual(FRANKEL['appRelease']['publication']['preserveLatest'], 'frankel-2026102805')
        for latest in ('frankel-2026102804', 'frankel-2026102803', 'rose-agent-304', None):
            self.server.latest = latest
            result = self.run_publisher(public=f'refused-{latest}')
            self.assertEqual(result.returncode, 1)
            self.assertIn('Latest is not frankel-2026102805; nothing is published', result.stderr)
            self.assertEqual(self.api_writes(), [])
        self.server.latest = 'frankel-2026102805'
        self.server.move_latest_on_publish = True
        moved = self.run_publisher(public='public2')
        self.assertEqual(moved.returncode, 1)
        self.assertIn('Latest moved away from frankel-2026102805', moved.stderr)

    def test_publishing_306_preserves_2805_and_promotes_nothing(self):
        result = self.run_publisher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.server.latest, 'frankel-2026102805')
        release, = self.server.releases
        self.assertEqual((release['tag_name'], release['prerelease'], release['make_latest']), ('rose-agent-306', True, 'false'))
        latest_reads = [e for e in self.server.log if e[:2] == ('GET', REPO + '/releases/latest')]
        self.assertEqual(len(latest_reads), 2, 'Latest is read before and after publishing')
        # The support floor stays the 2804 Beta: the 2805 promotion moves Latest, not minOsBuild.
        self.assertEqual(json.loads(result.stdout)['compatibility']['minOsBuild'], '2026102804')

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

APP_SOURCES = ROOT/'workspace/rose-agent/app/src/dev/pixel/voice'
PROBE = SCRIPTS/'tests/java/dev/pixel/voice/DirectUpdateReceiptProbe.java'
RECEIPT_304 = SCRIPTS/'tests/fixtures/app-publication/dev.pixel.voice-304.receipt.json'

class DirectUpdateContractTest(unittest.TestCase):
    """The producer's public receipt read by the app's own DirectUpdate.offer/incompatibility (unchanged App #74 sources)."""
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.classes = Path(cls.temp.name)/'classes'
        sources = [APP_SOURCES/n for n in ('PublicApkDownload.java', 'MiniJson.java', 'DirectUpdate.java')] + [PROBE]
        subprocess.run([shutil.which('javac') or 'javac', '-source', '8', '-target', '8', '-Xlint:-options', '-d', str(cls.classes),
            *map(str, sources)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = json.loads(json.dumps(FRANKEL))
        self.count = 0

    def produce(self, libs=(), facts=None, policy=None):
        """public_files() for a 306 artifact verified exactly as main() verifies it; returns (document, receipt path)."""
        self.count += 1
        directory = self.root/f'artifact{self.count}'
        directory.mkdir()
        data = apk_bytes(b'cert', libs=libs)
        self.config['identities']['appSignerSha256'] = hashlib.sha256(b'cert').hexdigest()
        if policy is not None:
            self.config['appRelease']['compatibility'] = policy
        (directory/'dev.pixel.voice-306.apk').write_bytes(data)
        receipt = {'receiptType': 'app-release', 'simulated': False, 'candidate': CANDIDATE, 'packageName': 'dev.pixel.voice',
            'versionCode': 306, 'versionName': '306-direct-github-beta', 'apk': 'dev.pixel.voice-306.apk', 'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(), 'signerSha256': self.config['identities']['appSignerSha256'],
            'minimumTestedOs': FRANKEL['appRelease']['minimumTestedOs'], 'apkFacts': facts or {'minSdk': 29, 'nativeAbis': []}}
        (directory/'app-release-receipt.json').write_text(json.dumps(receipt))
        (directory/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256((directory/n).read_bytes()).hexdigest()}  {n}\n'
            for n in ('app-release-receipt.json', 'dev.pixel.voice-306.apk')))
        verified, apk = arp.verify_artifact(directory, self.config, CANDIDATE, [['dev.pixel.voice', 306]])
        document, _ = arp.public_files(verified, apk, self.config, self.root/f'public{self.count}')
        return document, self.root/f'public{self.count}/dev.pixel.voice-306.receipt.json'

    def probe(self, receipt, version=306, device='frankel', abis='arm64-v8a', sdk=37, os_build='2026102805', controller=28,
              installed=305, signer=None):
        signer = signer or self.config['identities']['appSignerSha256']
        result = subprocess.run(['java', '-cp', str(self.classes), 'dev.pixel.voice.DirectUpdateReceiptProbe', str(receipt), str(version),
            device, abis, str(sdk), os_build, str(controller), str(installed), signer], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def test_the_produced_receipt_is_eligible_exactly_where_the_policy_says(self):
        document, receipt = self.produce()
        self.assertEqual(self.probe(receipt), f'ELIGIBLE 306 {document["sha256"]} {document["size"]} universal 29 2026102804 28')
        self.assertEqual(self.probe(receipt, os_build='2026102804', abis='x86_64'), self.probe(receipt))  # floor and universal
        for kwargs, reason in ((dict(device='husky'), 'isn’t for this phone model'), (dict(os_build='2026102803'), 'needs Rose OS 2026102804'),
                (dict(os_build='-'), 'build couldn’t be read'), (dict(controller=27), 'newer background controller'),
                (dict(controller=-1), 'newer background controller'), (dict(sdk=28), 'newer Android version'),
                (dict(signer='0' * 64), 'isn’t signed like the installed')):
            self.assertIn(reason, self.probe(receipt, **kwargs), kwargs)

    def test_native_libraries_are_listed_exactly_and_never_universal(self):
        document, receipt = self.produce(libs=['lib/arm64-v8a/libx.so'], facts={'minSdk': 29, 'nativeAbis': ['arm64-v8a']})
        self.assertEqual(document['compatibility']['abis'], ['arm64-v8a'])
        self.assertTrue(self.probe(receipt).startswith('ELIGIBLE 306 '))
        self.assertIn('processor', self.probe(receipt, abis='x86_64'))
        for libs, facts, message in ((['lib/arm64-v8a/libx.so'], {'minSdk': 29, 'nativeAbis': []}, "not the receipt's []"),
                ([], {'minSdk': 29, 'nativeAbis': ['arm64-v8a']}, "native libraries for [], not the receipt's ['arm64-v8a']"),
                (['lib/riscv64/libx.so'], {'minSdk': 29, 'nativeAbis': ['riscv64']}, "ABIs the app cannot match: ['riscv64']"),
                ([], {'nativeAbis': []}, 'no measured minSdk'), ([], {'minSdk': '29', 'nativeAbis': []}, 'no measured minSdk')):
            with self.assertRaisesRegex(arp.CheckFailed, re.escape(message)):
                self.produce(libs=libs, facts=facts)

    def test_malformed_policy_is_refused_before_anything_is_written(self):
        good = FRANKEL['appRelease']['compatibility']
        for change, message in (({'schemaVersion': 2}, 'not schemaVersion 1'), ({'deviceCodenames': []}, 'deviceCodenames'),
                ({'deviceCodenames': ['Frankel']}, 'deviceCodenames'), ({'deviceCodenames': ['frankel', 'frankel']}, 'deviceCodenames'),
                ({'minOsBuild': '2804'}, 'ten-digit'), ({'minOsBuild': 2026102804}, 'ten-digit'),
                ({'minControllerVersion': 0}, 'positive integer or null'), ({'minControllerVersion': '28'}, 'positive integer or null')):
            with self.assertRaisesRegex(arp.CheckFailed, message):
                self.produce(policy={**good, **change})
        without = {k: v for k, v in good.items() if k != 'minControllerVersion'}
        with self.assertRaisesRegex(arp.CheckFailed, 'positive integer or null'):
            self.produce(policy=without)
        self.assertFalse(list(self.root.glob('public*')))
        document, receipt = self.produce(policy={**good, 'minControllerVersion': None})
        self.assertIsNone(document['compatibility']['minControllerVersion'])
        self.assertTrue(self.probe(receipt, controller=-1).startswith('ELIGIBLE 306 '))  # explicit null: no controller needed

    def test_the_consumer_rejects_any_drift_from_the_produced_schema(self):
        document, receipt = self.produce()
        for edit in (lambda d: d.pop('compatibility'), lambda d: d.pop('schemaVersion'), lambda d: d.update(schemaVersion=2),
                lambda d: d.update(v4Signature={}), lambda d: d['compatibility'].pop('minControllerVersion'),
                lambda d: d['compatibility'].update(abis=['universal', 'x86']), lambda d: d.update(apkUrl=d['apkUrl'] + '?x')):
            changed = json.loads(json.dumps(document))
            edit(changed)
            receipt.write_text(json.dumps(changed))
            self.assertTrue(self.probe(receipt).startswith('UNAVAILABLE '), changed)

    def test_the_published_304_receipt_stays_unavailable_and_unchanged(self):
        self.assertEqual(hashlib.sha256(RECEIPT_304.read_bytes()).hexdigest(), FRANKEL['appRelease']['built'][1]['publicReceiptSha256'])
        self.assertEqual(json.loads(RECEIPT_304.read_text())['sha256'], FRANKEL['appRelease']['built'][1]['sha256'])
        self.assertTrue(self.probe(RECEIPT_304, version=304, installed=303).startswith('UNAVAILABLE '))

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
