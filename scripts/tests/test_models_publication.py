"""MODELS-PUBLISH-001: scripts/models-publication.py and .github/workflows/models-publish.yml.

Fixtures replace the pinned input identities (the real APK is not on main); one test pins the real values. No network,
credential or SDK is used: the API, build-tools and release client are fakes."""
import base64
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('models_publication', ROOT/'scripts/models-publication.py')
mp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mp)
WORKFLOW = (ROOT/'.github/workflows/models-publish.yml').read_text()

NATIVE = b'fixture rose chat library'
COMMIT, TREE = 'c' * 40, 'd' * 40


def blob_sha(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def make_apk(entries=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('AndroidManifest.xml', b'manifest')
        for name, data in (entries if entries is not None else {mp.NATIVE_ENTRY: NATIVE}).items():
            z.writestr(name, data)
    return buffer.getvalue()


class Fixture:
    """Pins patched to a synthetic APK and binding with the real binding's shape."""

    def __init__(self, test, apk=None):
        self.apk = apk or make_apk()
        self.binding_doc = copy.deepcopy(mp.BINDING)
        self.binding_doc['apk'].update(bytes=len(self.apk), sha256=hashlib.sha256(self.apk).hexdigest(),
                                       nativeLibrarySha256=hashlib.sha256(NATIVE).hexdigest())
        self.binding = json.dumps(self.binding_doc, indent=2, sort_keys=True).encode() + b'\n'
        self.files = {mp.APK_NAME: self.apk, mp.BINDING_NAME: self.binding}
        pins = {n: (blob_sha(d), len(d), hashlib.sha256(d).hexdigest()) for n, d in self.files.items()}
        for name, value in (('INPUT_FILES', pins), ('BINDING', self.binding_doc), ('INPUT_COMMIT', COMMIT),
                            ('INPUT_TREE', TREE), ('NATIVE_SHA256', hashlib.sha256(NATIVE).hexdigest())):
            patcher = mock.patch.object(mp, name, value)
            patcher.start()
            test.addCleanup(patcher.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix='models-publication-test-'))
        test.addCleanup(shutil.rmtree, self.tmp, True)

    def objects(self):
        commit = {'sha': COMMIT, 'tree': {'sha': TREE}, 'parents': []}
        tree = {'sha': TREE, 'truncated': False, 'tree': [
            {'path': 'publication-inputs', 'mode': '040000', 'type': 'tree', 'sha': 'e' * 40},
            {'path': mp.INPUT_DIR, 'mode': '040000', 'type': 'tree', 'sha': 'f' * 40},
            *({'path': f'{mp.INPUT_DIR}/{n}', 'mode': '100644', 'type': 'blob', 'sha': blob_sha(d), 'size': len(d)}
              for n, d in self.files.items())]}
        blobs = {blob_sha(d): {'sha': blob_sha(d), 'encoding': 'base64', 'size': len(d),
                               'content': base64.encodebytes(d).decode()} for d in self.files.values()}
        return commit, tree, blobs

    def fetch(self, commit=None, tree=None, blobs=None):
        c, t, b = self.objects()
        c, t, b = commit or c, tree or t, blobs or b
        def fetch(path):
            if path == f'git/commits/{COMMIT}':
                return c
            if path == f'git/trees/{TREE}?recursive=1':
                return t
            if path.startswith('git/blobs/') and path[10:] in b:
                return b[path[10:]]
            raise mp.Refused('input_unreadable: GET returned HTTP 404')
        return fetch

    def input_dir(self):
        directory = self.tmp/'input'
        directory.mkdir()
        for name, data in self.files.items():
            (directory/name).write_bytes(data)
        return directory


class Ingest(unittest.TestCase):
    def test_reads_exactly_the_two_pinned_files(self):
        f = Fixture(self)
        out = f.tmp/'in'
        self.assertEqual(mp.ingest(out, f.fetch())['apkSha256'], hashlib.sha256(f.apk).hexdigest())
        self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, f.files)

    def test_wrong_commit_tree_or_blob_refuses(self):
        f = Fixture(self)
        commit, tree, blobs = f.objects()
        apk_blob = blob_sha(f.apk)
        extra = dict(tree, tree=tree['tree'] + [{'path': f'{mp.INPUT_DIR}/run.sh', 'mode': '100755', 'type': 'blob', 'sha': '1' * 40}])
        exec_mode = dict(tree, tree=[dict(e, mode='100755') if e['type'] == 'blob' else e for e in tree['tree']])
        tampered = dict(blobs, **{apk_blob: dict(blobs[apk_blob], content=base64.b64encode(f.apk[:-1] + b'x').decode())})
        cases = (
            ({'commit': dict(commit, sha='0' * 40)}, 'input_commit'),
            ({'commit': dict(commit, tree={'sha': '0' * 40})}, 'input_commit'),
            ({'commit': dict(commit, parents=[{'sha': '1' * 40}])}, 'input_commit'),
            ({'tree': extra}, 'input_tree'),
            ({'tree': exec_mode}, 'input_tree'),
            ({'tree': dict(tree, truncated=True)}, 'input_tree'),
            ({'blobs': tampered}, 'input_blob'),
            ({'blobs': dict(blobs, **{apk_blob: dict(blobs[apk_blob], size=1)})}, 'input_blob'))
        for change, category in cases:
            with self.subTest(category=category, change=list(change)), self.assertRaisesRegex(mp.Refused, category):
                shutil.rmtree(f.tmp/'in', ignore_errors=True)
                mp.ingest(f.tmp/'in', f.fetch(**change))

    def test_api_denial_is_reported_without_retry(self):
        f = Fixture(self)
        calls = []
        def denied(path):
            calls.append(path)
            raise mp.Refused('input_unreadable: GET git/commits returned HTTP 403')
        with self.assertRaisesRegex(mp.Refused, 'HTTP 403'):
            mp.ingest(f.tmp/'in', denied)
        self.assertEqual(len(calls), 1)


class Files(unittest.TestCase):
    def test_hash_size_extra_file_and_binding_refuse(self):
        f = Fixture(self)
        good = f.input_dir()
        mp.check_files(good)
        cases = {
            'input_hash_mismatch': lambda d: (d/mp.APK_NAME).write_bytes(f.apk + b'\0'),
            'input_files': lambda d: (d/'extra.txt').write_text('x'),
            'binding_malformed': lambda d: None,
            'binding_mismatch': lambda d: None,
        }
        for category, change in cases.items():
            with self.subTest(category=category):
                d = f.tmp/category
                shutil.copytree(good, d)
                change(d)
                if category.startswith('binding'):
                    data = b'{not json' if category == 'binding_malformed' else json.dumps(dict(f.binding_doc, schema='x')).encode()
                    (d/mp.BINDING_NAME).write_bytes(data)
                    with mock.patch.object(mp, 'INPUT_FILES', dict(mp.INPUT_FILES, **{mp.BINDING_NAME: (
                            blob_sha(data), len(data), hashlib.sha256(data).hexdigest())})):
                        with self.assertRaisesRegex(mp.Refused, category):
                            mp.check_files(d)
                    continue
                with self.assertRaisesRegex(mp.Refused, category):
                    mp.check_files(d)

    def test_real_pins_are_the_owner_input(self):
        self.assertEqual((mp.INPUT_REPOSITORY, mp.INPUT_COMMIT, mp.INPUT_TREE),
            ('Immortal-Protocols/RoseOS-1', '9e30d0cdfa4f5bafe343502a18eceb9d7c760d2f', '0a1bb3ae4f6112e91ea228eace49073e4a559e6b'))
        self.assertEqual(mp.INPUT_FILES[mp.APK_NAME][1:], (971360, '998d63d2d23cce93cb7341ced3e3d028f58f48d06907222787d3606e66f86aee'))
        self.assertEqual(mp.INPUT_FILES[mp.BINDING_NAME][2], 'ffdf202c92eadb657005ea8d3b9638fd82064ada99d1926b43db290195d12e20')
        self.assertEqual((mp.NATIVE_SHA256, mp.SIGNER_SHA256), ('f3aad0226f99a8b0853663d08bd0e623559741338dd54915443f1daac811d060',
            '066c66d62518d1db6c8a7d40795fcca580412049d2c5a5cce89b7673540108da'))
        self.assertEqual(mp.TOOLS, {'archiveSha256': 'bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88',
            'apksignerSha256': '00ef9948f843fe395d2440ae3ef41405b8040a6d5d46493bd1902ac0ee6deae7',
            'aaptSha256': '393c4c87f675f9555c50921fb53be1f3893cacd29f2270c4863e2e3f99583c39'})
        self.assertEqual(mp.DESTINATION, {'repository': 'Immortal-Protocols/pixel-ota', 'releaseId': 429690780,
            'tag': 'rose-models-2', 'assetName': 'rose-models-signed.apk', 'prerelease': True, 'makeLatest': False})
        self.assertEqual(mp.BINDING['destination'], {k: mp.DESTINATION[k] for k in mp.BINDING['destination']})
        self.assertEqual(hashlib.sha256(json.dumps(mp.BINDING, indent=2, sort_keys=True).encode() + b'\n').hexdigest(),
            mp.INPUT_FILES[mp.BINDING_NAME][2])  # the pinned document is byte-for-byte the committed binding


def verifier_output(scheme, signers=(mp.SIGNER_SHA256,), ok=True):
    lines = ['Verifies'] + [f'Verified using {s} scheme ({label}): {"true" if s == scheme and ok else "false"}'
        for s, label in (('v1', 'JAR signing'), ('v2', 'APK Signature Scheme v2'), ('v3', 'APK Signature Scheme v3'),
                         ('v3.1', 'APK Signature Scheme v3.1'), ('v4', 'APK Signature Scheme v4'))]
    lines += [f'Number of signers: {len(signers)}'] + [f'Signer #{i + 1} certificate SHA-256 digest: {s}' for i, s in enumerate(signers)]
    return '\n'.join(lines) + '\n'


BADGING = ("package: name='dev.pixel.models' versionCode='2' versionName='0.2.0' platformBuildVersionName='15'\n"
           "sdkVersion:'29'\ntargetSdkVersion:'35'\nnative-code: 'arm64-v8a'\n")


class Verify(unittest.TestCase):
    def run_verify(self, f, tools=None, prepare_result=None, directory=None):
        out = dict({'v2': (True, verifier_output('v2')), 'v3': (True, verifier_output('v3')), 'aapt': (True, BADGING)}, **(tools or {}))
        calls = []
        def tool_runner(apk, sdk_root):
            calls.append(apk)
            return out
        result = mp.verify(directory or f.input_dir(), f.tmp/'sdk', prepare=lambda cfg: prepare_result or dict(mp.TOOLS),
                           tools=tool_runner)
        return result, calls

    def test_valid_apk_passes_with_v2_over_24_27_and_v3(self):
        f = Fixture(self)
        result, _ = self.run_verify(f)
        self.assertEqual((result['schemes'], result['minSdk'], result['signerSha256']),
            ({'v2': 'API 24-27', 'v3': 'API 29+'}, '29', mp.SIGNER_SHA256))

    def test_identity_scheme_signer_and_tool_failures_refuse(self):
        f = Fixture(self)
        other = 'a' * 64
        cases = (
            ({'v2': (True, verifier_output('v3'))}, 'scheme_unverified: v2 over API 24-27'),
            ({'v3': (True, verifier_output('v2'))}, 'scheme_unverified: v3'),
            ({'v3': (False, '')}, 'scheme_unverified: v3'),
            ({'v2': (True, verifier_output('v2', signers=(other,)))}, 'signer_mismatch'),
            ({'v3': (True, verifier_output('v3', signers=(mp.SIGNER_SHA256, other)))}, 'signer_mismatch'),
            ({'aapt': (True, BADGING.replace('dev.pixel.models', 'dev.pixel.voice'))}, 'wrong_package'),
            ({'aapt': (True, BADGING.replace("versionCode='2'", "versionCode='3'"))}, 'wrong_version'),
            ({'aapt': (True, BADGING.replace("'0.2.0'", "'0.2.1'"))}, 'wrong_version'),
            ({'aapt': (True, BADGING.replace("'arm64-v8a'", "'arm64-v8a' 'x86_64'"))}, 'wrong_abi'),
            ({'aapt': (True, BADGING.replace("sdkVersion:'29'", "sdkVersion:'24'"))}, 'wrong_min_sdk'),
            ({'aapt': (False, '')}, 'wrong_package'))
        for tools, category in cases:
            with self.subTest(category=category), self.assertRaisesRegex(mp.Refused, category):
                shutil.rmtree(f.tmp/'input', ignore_errors=True)
                self.run_verify(f, tools)
        shutil.rmtree(f.tmp/'input', ignore_errors=True)
        with self.assertRaisesRegex(mp.Refused, 'sdk_tools_mismatch'):
            self.run_verify(f, prepare_result=dict(mp.TOOLS, aaptSha256='0' * 64))

    def test_native_library_and_abi_are_read_from_the_apk_before_any_tool(self):
        for entries, category in (({mp.NATIVE_ENTRY: b'other library'}, 'native_mismatch'),
                                  ({mp.NATIVE_ENTRY: NATIVE, 'lib/x86_64/librose_chat_android.so': NATIVE}, 'wrong_abi'),
                                  ({}, 'wrong_abi')):
            with self.subTest(category=category):
                f = Fixture(self, apk=make_apk(entries))
                with self.assertRaisesRegex(mp.Refused, category):
                    _, calls = self.run_verify(f)
                with mock.patch.object(mp, 'prepare_sdk', side_effect=AssertionError('tools reached')):
                    with self.assertRaisesRegex(mp.Refused, category):
                        mp.verify(f.tmp/'input', f.tmp/'sdk', tools=lambda *a: self.fail('tools reached'))

    def test_tool_environment_passes_nothing_injectable(self):
        with mock.patch.dict('os.environ', {'JAVA_HOME': '/opt/jdk', 'LD_PRELOAD': '/x.so', 'JAVA_TOOL_OPTIONS': '-Dx',
                                            '_JAVA_OPTIONS': '-Dy', 'CLASSPATH': '/c'}, clear=True):
            self.assertEqual(mp.tool_environment(), {'PATH': '/opt/jdk/bin:/usr/bin:/bin', 'LC_ALL': 'C', 'JAVA_HOME': '/opt/jdk'})


class FakeClient:
    repository = 'Immortal-Protocols/pixel-ota'

    def __init__(self, f, release=None, latest=('frankel-2026103106', 'frankel-2026103106'), upload_error=None,
                 stored=None, public=None, digest=True, tag_owner=None):
        self.f, self.calls, self.latest = f, [], list(latest)
        self.release = release or {'id': 429690780, 'tag_name': 'rose-models-2', 'draft': True, 'prerelease': True, 'assets': []}
        self.upload_error, self.stored, self.public, self.digest = upload_error, stored, public, digest
        self.tag_owner = tag_owner

    def latest_tag(self):
        self.calls.append('latest')
        return self.latest.pop(0)

    def get_release(self, rid):
        self.calls.append(('get', rid))
        return copy.deepcopy(self.release) if rid == self.release['id'] else {}

    def find_release(self, tag):
        self.calls.append(('find', tag))
        return self.tag_owner if self.tag_owner is not None else (copy.deepcopy(self.release) if tag == self.release['tag_name'] else None)

    def upload(self, rid, path):
        self.calls.append(('upload', rid, path.name))
        if self.upload_error:
            raise self.upload_error
        data = path.read_bytes() if self.stored is None else self.stored
        self.data = data
        self.release['assets'] = [{'id': 7, 'name': path.name, 'state': 'uploaded', 'size': len(data),
            'digest': ('sha256:' + hashlib.sha256(data).hexdigest()) if self.digest else None,
            'browser_download_url': 'https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-models-2/' + path.name}]

    def download(self, asset_id, destination, size):
        self.calls.append(('download', asset_id))
        Path(destination).write_bytes(self.data)

    def publish_prerelease(self, rid):
        self.calls.append(('publish', rid))
        self.release.update(draft=False, prerelease=True)
        return copy.deepcopy(self.release)

    def public_download(self, url, destination, size):
        self.calls.append(('public', url))
        Path(destination).write_bytes(self.data if self.public is None else self.public)


class Publish(unittest.TestCase):
    def publish(self, f, client):
        work = f.tmp/'work'
        work.mkdir(exist_ok=True)
        return mp.publish(f.tmp/'input', client, work)

    def test_valid_path_publishes_a_prerelease_and_preserves_latest(self):
        f = Fixture(self)
        f.input_dir()
        client = FakeClient(f)
        outcome = self.publish(f, client)
        self.assertEqual((outcome['outcome'], outcome['tag'], outcome['latestBefore'], outcome['latestAfter'], outcome['publicReadback']),
            ('published', 'rose-models-2', 'frankel-2026103106', 'frankel-2026103106', 'verified'))
        self.assertIn('not established', outcome['caveat'])
        names = [c if isinstance(c, str) else c[0] for c in client.calls]
        self.assertEqual(names, ['latest', 'get', 'find', 'upload', 'get', 'download', 'publish', 'get', 'latest', 'public'])
        self.assertEqual(sum(1 for c in client.calls if c[0] == 'upload'), 1)

    def test_latest_may_advance_to_a_newer_os_release_but_never_to_this_tag(self):
        f = Fixture(self)
        f.input_dir()
        self.assertEqual(self.publish(f, FakeClient(f, latest=('frankel-2026103106', 'frankel-2026103107')))['latestAfter'],
                         'frankel-2026103107')
        for after in ('rose-models-2', None):
            with self.subTest(after=after), self.assertRaisesRegex(mp.Refused, 'latest_changed'):
                f2 = Fixture(self)
                f2.input_dir()
                self.publish(f2, FakeClient(f2, latest=('frankel-2026103106', after)))

    def test_destination_state_refuses_before_any_upload(self):
        f = Fixture(self)
        f.input_dir()
        draft = {'id': 429690780, 'tag_name': 'rose-models-2', 'draft': True, 'prerelease': True, 'assets': []}
        cases = (
            ({'release': dict(draft, assets=[{'id': 1, 'name': 'rose-models-signed.apk'}])}, 'destination_occupied'),
            ({'release': dict(draft, tag_name='rose-models-3')}, 'destination_unexpected'),
            ({'release': dict(draft, draft=False)}, 'destination_unexpected'),
            ({'release': dict(draft, prerelease=False)}, 'destination_unexpected'),
            ({'tag_owner': dict(draft, id=1)}, 'destination_collision'),
            ({'latest': ('rose-agent-311', 'rose-agent-311')}, 'latest_unexpected'),
            ({'latest': (None, None)}, 'latest_unexpected'))
        for kw, category in cases:
            with self.subTest(category=category):
                client = FakeClient(f, **kw)
                with self.assertRaisesRegex(mp.Refused, category):
                    self.publish(f, client)
                self.assertFalse([c for c in client.calls if c[0] in ('upload', 'publish')])
        client = FakeClient(f)
        client.repository = 'Immortal-Protocols/RoseOS-1'
        with self.assertRaisesRegex(mp.Refused, 'destination_unexpected'):
            self.publish(f, client)
        self.assertEqual(client.calls, [])

    def test_upload_failure_or_unverified_upload_stops_before_publishing(self):
        f = Fixture(self)
        f.input_dir()
        cases = (
            ({'upload_error': OSError('reset')}, 'upload_failed: reset; stopped without retry'),
            ({'stored': f.apk + b'\0'}, 'upload_unverified: the draft does not hold'),
            ({'digest': False, 'stored': None}, None),
        )
        for kw, category in cases:
            with self.subTest(kw=list(kw)):
                client = FakeClient(f, **kw)
                if category is None:  # an asset without a digest is still verified by downloading it
                    self.publish(f, client)
                    self.assertIn(('download', 7), client.calls)
                    continue
                with self.assertRaisesRegex(mp.Refused, category):
                    self.publish(f, client)
                self.assertFalse([c for c in client.calls if c[0] == 'publish'])
                self.assertEqual(sum(1 for c in client.calls if c[0] == 'upload'), 1)

    def test_downloaded_or_public_bytes_that_differ_refuse(self):
        f = Fixture(self)
        f.input_dir()
        client = FakeClient(f)
        client.download = lambda asset_id, destination, size: Path(destination).write_bytes(b'other')
        with self.assertRaisesRegex(mp.Refused, 'upload_unverified: the uploaded bytes differ'):
            self.publish(f, client)
        self.assertFalse([c for c in client.calls if c[0] == 'publish'])
        with self.assertRaisesRegex(mp.Refused, 'public_readback_mismatch'):
            self.publish(f, FakeClient(f, public=b'other'))

    def test_changed_input_refuses_before_any_api_call(self):
        f = Fixture(self)
        (f.input_dir()/mp.APK_NAME).write_bytes(b'tampered')
        client = FakeClient(f)
        with self.assertRaisesRegex(mp.Refused, 'input_hash_mismatch'):
            self.publish(f, client)
        self.assertEqual(client.calls, [])


class Cli(unittest.TestCase):
    def test_ingest_reads_only_this_repository_with_a_job_token(self):
        with mock.patch.dict('os.environ', {'GITHUB_REPOSITORY': 'Immortal-Protocols/pixel-ota', 'GITHUB_TOKEN': 't'}, clear=True):
            self.assertEqual(mp.main(['ingest', '--out', '/nonexistent/x']), 1)
        with mock.patch.dict('os.environ', {'GITHUB_REPOSITORY': 'Immortal-Protocols/RoseOS-1'}, clear=True):
            self.assertEqual(mp.main(['ingest', '--out', '/nonexistent/x']), 1)

    def test_publish_without_a_token_refuses(self):
        f = Fixture(self)
        f.input_dir()
        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertEqual(mp.main(['publish', '--input', str(f.tmp/'input')]), 1)


class Workflow(unittest.TestCase):
    def steps(self):
        return re.split(r'\n      - ', WORKFLOW.split('\n    steps:\n', 1)[1])

    def test_manual_main_only_hosted_single_job_with_read_permissions(self):
        self.assertEqual(re.search(r'(?ms)^on:\n(.*?)\n\n', WORKFLOW)[1], '  workflow_dispatch:')
        self.assertEqual(re.search(r'(?ms)^permissions:\n(.*?)\n\n', WORKFLOW)[1], '  contents: read')
        self.assertEqual(re.findall(r'(?m)^  ([\w-]+):$', WORKFLOW.split('\njobs:\n', 1)[1]), ['publish'])
        for line in ('    if: github.ref == \'refs/heads/main\'\n', '    runs-on: ubuntu-24.04\n', '    timeout-minutes: 15\n',
                     '          persist-credentials: false\n'):
            self.assertIn(line, WORKFLOW)
        for absent in ('id-token', 'actions:', 'self-hosted', 'workflow_call', 'download-artifact', 'upload-artifact',
                       'pull_request', 'schedule', 'push:', 'inputs:', 'GITHUB_OUTPUT', 'GITHUB_ENV'):
            self.assertNotIn(absent, WORKFLOW)

    def test_every_check_runs_before_the_token_and_only_publish_sees_it(self):
        steps = self.steps()
        keys = ('actions/checkout@', 'models-publication.py ingest', 'models-publication.py verify', 'create-github-app-token@',
                'models-publication.py publish', 'rm -rf')
        self.assertEqual([[k for k in keys if k in s] for s in steps], [[k] for k in keys])
        token = steps[3]
        for line in ('uses: actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510', 'app-id: ${{ vars.PIXEL_OTA_APP_ID }}',
                     'private-key: ${{ secrets.PIXEL_OTA_APP_PRIVATE_KEY }}', 'owner: Immortal-Protocols',
                     'repositories: pixel-ota', 'permission-contents: write'):
            self.assertIn(line, token)
        self.assertNotIn('skip-token-revoke', WORKFLOW)  # the action revokes the token when the job ends
        self.assertEqual([i for i, s in enumerate(steps) if 'steps.app.outputs.token' in s], [4])
        self.assertEqual(re.findall(r'secrets\.(\w+)', WORKFLOW), ['PIXEL_OTA_APP_PRIVATE_KEY'])
        self.assertIn('GITHUB_TOKEN: ${{ github.token }}', steps[1])
        self.assertIn('if: always()', steps[5])

    def test_no_input_branch_is_checked_out_or_executed(self):
        self.assertNotIn('ref:', WORKFLOW)
        self.assertNotIn('models-publication/', WORKFLOW)
        self.assertEqual(re.findall(r'python3 (\S+)', WORKFLOW), ['scripts/models-publication.py'] * 3)


if __name__ == '__main__':
    unittest.main()
