"""rango-local-inputs.py and rango-reconcile.json (OS-RANGO-BUILD-003) against synthetic OTAs, images and fake aapt,
apksigner and fsck.erofs: no real release, download, network, device, signer, publisher or cloud call. A pass says
nothing about an actual extraction of the public 2901 OTA or a Rango build."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rose_verify import make_payload, MKE2FS, DEBUGFS  # noqa: E402

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS/file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

li = load('rango_local_inputs', 'rango-local-inputs.py')
hr = load('host_reconcile', 'host-reconcile.py')
CONFIG = json.loads((ROOT/'os/build-only/rango-reconcile.json').read_text())
FRANKEL = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())['host']['integrate']['reconcile']
CERT = CONFIG['inputs']['appSignerSha256']
BLOCK = 4096

FAKE_AAPT = '''#!/usr/bin/env python3
import json, sys, zipfile
assert sys.argv[1:3] == ['dump', 'badging'], sys.argv
row = json.loads(zipfile.ZipFile(sys.argv[3]).read('identity.json'))
print(f"package: name='{row['package']}' versionCode='{row['version']}' versionName='x'")
'''
FAKE_APKSIGNER = '''#!/usr/bin/env python3
import json, sys, zipfile
assert sys.argv[1:7] == ['verify', '--print-certs', '--min-sdk-version', '37', '--max-sdk-version', '37'], sys.argv
row = json.loads(zipfile.ZipFile(sys.argv[7]).read('identity.json'))
print(f"Signer #1 certificate SHA-256 digest: {row['cert']}")
'''
# erofs stand-in: "extracts" the staged tree recorded beside the tool; never follows links (copytree symlinks=True).
FAKE_FSCK_EROFS = '''#!/usr/bin/env python3
import shutil, sys
from pathlib import Path
staged = Path(__file__).with_name('erofs-staged')
assert sys.argv[1].startswith('--extract='), sys.argv
shutil.copytree(staged, sys.argv[1].split('=', 1)[1], symlinks=True)
'''

def apk(package, version, cert=CERT, extra=b''):
    path = Path(tempfile.mkstemp(suffix='.apk')[1])
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('identity.json', json.dumps({'package': package, 'version': version, 'cert': cert.upper()}))
        z.writestr('classes.dex', b'dex\n' + os.urandom(64) + extra)
    data = path.read_bytes()
    path.unlink()
    return data

def erofs_image():
    image = bytearray(4 * BLOCK)
    image[1024:1028] = b'\xe2\xe1\xf5\xe0'
    return bytes(image)

@unittest.skipUnless(MKE2FS and DEBUGFS, 'needs mke2fs and debugfs (e2fsprogs)')
class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='rango-inputs-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tools = self.tmp/'tools'
        self.tools.mkdir()
        for name, body in (('aapt', FAKE_AAPT), ('apksigner', FAKE_APKSIGNER), ('fsck.erofs', FAKE_FSCK_EROFS)):
            (self.tools/name).write_text(body)
            (self.tools/name).chmod(0o755)
        self.apks = {'dev.pixel.backgroundcontrol': apk('dev.pixel.backgroundcontrol', 28),
            'dev.pixel.voice': apk('dev.pixel.voice', 310), 'dev.pixel.calculator.agent': apk('dev.pixel.calculator.agent', 4)}
        self.paths = {a['package']: a['imagePath'] for a in CONFIG['inputs']['apps']}
        self.stage_product()
        self.system_ext = self.ext4({self.paths['dev.pixel.backgroundcontrol']: self.apks['dev.pixel.backgroundcontrol']})
        self.downloads = self.tmp/'downloads'
        self.downloads.mkdir()
        self.verification_doc = {'candidate': {'sha': '02b21bc69f9afd281422d42e3dcf8a456e471de1', 'build': '2026102901',
            'timestamp': '1793311111'}, 'signedTargetFilesSha256': CONFIG['inputs']['signedTargetFilesSha256']}
        self.write_release()

    def stage_product(self, link=None):
        staged = self.tools/'erofs-staged'
        shutil.rmtree(staged, ignore_errors=True)
        for package in ('dev.pixel.voice', 'dev.pixel.calculator.agent'):
            inner = self.paths[package].split('/', 1)[1]
            (staged/inner).parent.mkdir(parents=True, exist_ok=True)
            (staged/inner).write_bytes(self.apks[package])
        if link:
            link(staged)

    def ext4(self, files, symlink=None):
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        for image_path, data in files.items():
            inner = root/image_path.split('/', 1)[1]
            inner.parent.mkdir(parents=True, exist_ok=True)
            if symlink:
                inner.symlink_to(symlink)
            else:
                inner.write_bytes(data)
        image = self.tmp/f'{root.name}.img'
        subprocess.run([MKE2FS, '-q', '-t', 'ext4', '-d', str(root), str(image), '8M'], check=True, capture_output=True,
            env=dict(os.environ, E2FSPROGS_FAKE_TIME='1'))
        # make_payload ships the first half as data and ZEROs the rest, so pad with an equal run of zeros.
        data = image.read_bytes()
        return data + bytes(len(data))

    def write_release(self, ota_entries=None, device='frankel', corrupt=False, product=None):
        payload = make_payload({'product': product or erofs_image(), 'system_ext': self.system_ext}, corrupt=corrupt)
        self.ota = self.downloads/CONFIG['inputs']['ota']['file']
        with zipfile.ZipFile(self.ota, 'w') as z:
            z.writestr('META-INF/com/android/metadata', f'ota-type=AB\npre-device={device}\npost-build=x\n')
            z.writestr('payload_properties.txt', 'FILE_HASH=x\n')
            z.writestr(zipfile.ZipInfo('payload.bin'), payload, compress_type=zipfile.ZIP_STORED)
            for name, data in (ota_entries or []):
                z.writestr(zipfile.ZipInfo(name), data)
        self.verification = self.downloads/'verification.json'
        self.verification.write_text(json.dumps(self.verification_doc))
        self.pin()

    def pin(self):
        """A config copy whose inputs pins are the synthetic files' sizes and hashes; everything else as shipped."""
        config = copy.deepcopy(CONFIG)
        for key, path in (('ota', self.ota), ('verification', self.verification)):
            config['inputs'][key].update(sizeBytes=path.stat().st_size, sha256=li.sha256_file(path))
        self.config = self.tmp/'rango-reconcile.json'
        self.config.write_text(json.dumps(config))

    def prepare(self, out=None, ota=None, verification=None):
        tools = {'fsck_erofs': self.tools/'fsck.erofs', 'debugfs': Path(DEBUGFS), 'aapt': self.tools/'aapt',
            'apksigner': self.tools/'apksigner'}
        return li.prepare(ota or self.ota, verification or self.verification, out or self.tmp/'inputs', tools, self.config)

    def fails(self, expect, **kwargs):
        with self.assertRaises((li.Failed, li.rv.Fail)) as caught:
            self.prepare(**kwargs)
        self.assertIn(expect, str(caught.exception))

class PrepareTest(Fixture):
    def test_verifies_extracts_unchanged_and_writes_the_mapped_policy_offline(self):
        def no_network(*args, **kwargs):
            raise AssertionError('network attempted')
        with mock.patch.object(socket.socket, 'connect', no_network), \
                mock.patch.object(socket, 'create_connection', no_network):
            record = self.prepare()
        out = self.tmp/'inputs'
        self.assertEqual(json.loads((out/'local-inputs.json').read_text()), record)
        for app in record['apps']:
            data = Path(app['signedPath']).read_bytes()
            self.assertEqual(data, self.apks[app['package']], 'original bytes preserved')
            self.assertEqual(app['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(Path(app['signedPath']).name, app['package'] + '.apk')
            self.assertEqual(os.stat(app['signedPath']).st_mode & 0o777, 0o444)
        self.assertEqual({a['package']: (a['role'], a['versionCode'], a['filesystem']) for a in record['apps']},
            {'dev.pixel.backgroundcontrol': ('agent', 28, 'ext4'), 'dev.pixel.voice': ('voice', 310, 'erofs'),
             'dev.pixel.calculator.agent': ('target', 4, 'erofs')})
        policy = json.loads((out/'background-policy.json').read_text())
        self.assertEqual((policy['sdk'], policy['prototypeControl']), (37, False))
        self.assertEqual(policy['agent']['packageName'], 'dev.pixel.backgroundcontrol')
        self.assertEqual(policy['voice']['packageName'], 'dev.pixel.voice')
        self.assertEqual([t['packageName'] for t in policy['targets']], ['dev.pixel.calculator.agent'])
        self.assertEqual(policy['agent']['certificates'], [CERT.upper()])
        self.assertEqual(policy['agent']['apkSha256'], hashlib.sha256(self.apks['dev.pixel.backgroundcontrol']).hexdigest())
        self.assertEqual(record['policy']['sha256'], li.sha256_file(out/'background-policy.json'))
        self.assertNotIn('--prototype-control', record['policy']['command'])
        self.assertEqual(record['provenance']['signedTargetFilesMatchesPin'], True)
        self.assertEqual(record['otaMetadata']['pre-device'], 'frankel')
        self.assertEqual(sorted(record['partitions']), ['product', 'system_ext'])
        self.assertEqual((record['network'], record['signed'], record['published']), (False, False, False))
        self.assertEqual(record['product']['commit'], '02b21bc69f9afd281422d42e3dcf8a456e471de1')
        self.fails('exists; choose a new inputs directory')

    def test_ota_and_receipt_identity_mismatch(self):
        config = json.loads(self.config.read_text())
        config['inputs']['ota']['sha256'] = CONFIG['inputs']['ota']['sha256']
        self.config.write_text(json.dumps(config))
        self.fails(CONFIG['inputs']['ota']['sha256'])
        self.pin()
        renamed = self.downloads/'frankel-ota_update-2026102701.zip'
        shutil.copyfile(self.ota, renamed)
        self.fails('not frankel-ota_update-2026102901.zip', ota=renamed)
        self.verification.write_text(json.dumps(self.verification_doc) + ' ')
        self.fails('--verification verification.json')
        self.assertFalse((self.tmp/'inputs').exists(), 'nothing written before every input is verified')

    def test_verification_naming_another_build_or_target_files(self):
        self.verification_doc['candidate']['build'] = '2026102701'
        self.write_release()
        self.fails('names build 2026102701')
        self.verification_doc['candidate']['build'] = '2026102901'
        self.verification_doc['signedTargetFilesSha256'] = '0' * 64
        self.write_release()
        self.fails('different signed target-files hash')

    def test_apk_identity_mismatch(self):
        for package, bad, expect in (('dev.pixel.voice', apk('dev.pixel.voice', 309), '309'),
                ('dev.pixel.calculator.agent', apk('dev.pixel.calculator.agent', 4, cert='ab' * 32), 'ABAB'),
                ('dev.pixel.voice', apk('dev.pixel.other', 310), 'dev.pixel.other')):
            with self.subTest(expect=expect):
                good = self.apks[package]
                self.apks[package] = bad
                self.stage_product()
                self.apks[package] = good
                self.write_release()
                shutil.rmtree(self.tmp/'inputs', ignore_errors=True)
                self.fails(expect)
        self.system_ext = self.ext4({self.paths['dev.pixel.backgroundcontrol']: apk('dev.pixel.backgroundcontrol', 27)})
        self.stage_product()
        self.write_release()
        shutil.rmtree(self.tmp/'inputs', ignore_errors=True)
        self.fails("('dev.pixel.backgroundcontrol', 27")

    def test_malicious_archives_are_refused(self):
        for entries, device, expect in (([('../../etc/passwd', b'x')], 'frankel', 'unsafe entry names'),
                ([('/abs/path', b'x')], 'frankel', 'unsafe entry names'),
                ([('META-INF\\..\\x', b'x')], 'frankel', 'unsafe entry names'),
                ([('payload.bin', b'second')], 'frankel', 'duplicate entries'),
                ([], 'rango', "pre-device is 'rango'")):
            with self.subTest(expect=expect):
                with mock.patch('warnings.warn'):
                    self.write_release(entries, device)
                self.fails(expect)
        self.write_release(corrupt=True)
        self.fails('operation data hash mismatch')

    def test_compressed_payload_is_refused(self):
        payload = make_payload({'product': erofs_image(), 'system_ext': self.system_ext})
        with zipfile.ZipFile(self.ota, 'w') as z:
            z.writestr('META-INF/com/android/metadata', 'pre-device=frankel\n')
            z.writestr(zipfile.ZipInfo('payload.bin'), payload, compress_type=zipfile.ZIP_DEFLATED)
        self.pin()
        self.fails('not stored uncompressed')

    def test_path_and_symlink_escapes_are_refused(self):
        link = self.tmp/'ota-link.zip'
        link.symlink_to(self.ota)
        self.fails('must be a regular file, not a link', ota=link)
        self.fails('absolute path', ota=Path(os.path.relpath(self.ota)))
        self.fails('without ..', out=self.tmp/'x/../inputs')
        outside = self.tmp/'outside'
        outside.mkdir()
        (outside/'RosePreinstall_dev_pixel_voice.apk').write_bytes(self.apks['dev.pixel.voice'])
        def escape(staged):
            directory = staged/'app/RosePreinstall_dev_pixel_voice'
            shutil.rmtree(directory)
            directory.symlink_to(outside)
        self.stage_product(escape)
        self.fails('is a symlink in the image', out=self.tmp/'inputs-1')
        def file_link(staged):
            target = staged/self.paths['dev.pixel.calculator.agent'].split('/', 1)[1]
            target.unlink()
            target.symlink_to('/etc/hostname')
        self.stage_product(file_link)
        self.fails('is a symlink in the image', out=self.tmp/'inputs-2')
        self.stage_product()
        self.system_ext = self.ext4({self.paths['dev.pixel.backgroundcontrol']: None}, symlink='/etc/hostname')
        self.write_release()
        self.fails('is not a regular entry (a symlink or other) in the image', out=self.tmp/'inputs-3')

    def test_cli_needs_explicit_tool_paths(self):
        with self.assertRaises(SystemExit):
            li.main(['prepare', '--ota', str(self.ota), '--verification', str(self.verification), '--out', str(self.tmp/'o')])
        code = li.main(['prepare', '--config', str(self.config), '--ota', str(self.ota), '--verification',
            str(self.verification), '--out', str(self.tmp/'o'), '--fsck-erofs', 'fsck.erofs', '--debugfs', DEBUGFS,
            '--aapt', str(self.tools/'aapt'), '--apksigner', str(self.tools/'apksigner')])
        self.assertEqual(code, 1, 'a bare tool name is never looked up on PATH')

class StaticTest(unittest.TestCase):
    def test_the_helper_reaches_no_network_signer_publisher_or_cloud(self):
        code = (SCRIPTS/'rango-local-inputs.py').read_text()
        for banned in ('urllib', 'http.client', 'import requests', 'import socket', 'gcloud', 'gsutil', 'secretmanager',
                'host_secrets', 'host-sign', 'github_release_publisher', "'ota_extractor'", "'mount'", 'sign_target_files',
                "'sign'", 'releases/latest'):
            self.assertNotIn(banned, code, banned)

class ReconcileConfigTest(unittest.TestCase):
    def test_rango_reconcile_keeps_the_integration_and_none_of_frankel_state(self):
        settings = CONFIG['host']['integrate']['reconcile']
        for key in ('projects', 'integrationOwnedRoots', 'sparsePaths', 'patcher'):
            self.assertEqual(settings[key], FRANKEL[key], key)
        self.assertNotIn('installedIdentities', settings, 'Frankel 2701 identities do not describe the Fold')
        args = ' '.join(settings['applyArgs'])
        for banned in ('--updater-url', 'releases/latest', '--installed-identities', '--locked-control', 'frankel',
                '--reference-display-address'):
            self.assertNotIn(banned, args, banned)
        self.assertIn('--keep-upstream-updater', settings['applyArgs'])
        frankel_args = [a for a in FRANKEL['applyArgs']]
        for kept in ('--background-policy', '--background-agent', '--agent-tray', '--preinstall-apk'):
            self.assertIn(kept, settings['applyArgs'])
            self.assertIn(kept, frankel_args)
        isolated = json.loads((ROOT/'os/build-only/rango-isolated.json').read_text())['host']
        self.assertEqual(CONFIG['host']['sourceRoot'], isolated['sourceRoot'])
        self.assertEqual(CONFIG['host']['lockPath'], isolated['lockPath'])
        self.assertEqual(settings['receiptDir'], isolated['reconcileReceiptDir'])
        for path in (CONFIG['host']['sourceRoot'], CONFIG['host']['lockPath'], CONFIG['host']['workDir'],
                CONFIG['host']['inputsDir'], settings['receiptDir']):
            self.assertTrue(path.startswith('/srv/rose-rango/'), path)
        self.assertEqual(CONFIG['inputs']['prototypeControl'], False)
        self.assertEqual({a['package']: (a['role'], a['versionCode']) for a in CONFIG['inputs']['apps']},
            {'dev.pixel.backgroundcontrol': ('agent', 28), 'dev.pixel.voice': ('voice', 310),
             'dev.pixel.calculator.agent': ('target', 4)})
        self.assertEqual(CONFIG['product']['apps'], {'dev.pixel.voice': 310, 'dev.pixel.calculator.agent': 4,
            'dev.pixel.backgroundcontrol': 28})
        self.assertEqual((CONFIG['inputs']['ota']['sizeBytes'], CONFIG['inputs']['ota']['sha256']),
            (1678971619, 'c97027f1b88d518a7f9b40d182c30795410be89df257b12c270cc6d7b3db5019'))
        self.assertEqual((CONFIG['inputs']['verification']['sizeBytes'], CONFIG['inputs']['verification']['sha256']),
            (4599, '0c65767734ae68bff03ccbfd32cbf49387146ae7a82150a01b07b8d36e124e32'))
        self.assertNotIn('latest', CONFIG['inputs']['release']['baseUrl'])
        self.assertTrue(CONFIG['inputs']['release']['baseUrl'].endswith('/releases/download/frankel-2026102901/'))

    def test_host_reconcile_reads_this_config_unchanged(self):
        captured = {}
        def fake(source, candidate, release_dir, apply_args, receipt_dir, projects, owned, extra, label, mode, reason, **kw):
            captured.update(source=source, args=apply_args, receipt_dir=receipt_dir, projects=projects, label=label,
                patcher=kw['patcher'])
            return {'changed': []}
        with mock.patch.object(hr, 'reconcile', fake):
            code = hr.main(['apply', '/srv/rose-rango/work/product', '/srv/rose-rango/work/reconcile-1',
                '/srv/rose-rango/inputs/p.json', '/srv/rose-rango/inputs/signed-apps', '--config',
                str(ROOT/'os/build-only/rango-reconcile.json'), '--label', 'rango-2026103001'])
        self.assertEqual(code, 0)
        self.assertEqual(captured['source'], '/srv/rose-rango/src')
        self.assertEqual(captured['receipt_dir'], '/srv/rose-rango/reconcile')
        self.assertEqual(captured['patcher'], 'trusted')
        self.assertEqual(captured['args'], ['--background-policy', '/srv/rose-rango/inputs/p.json', '--background-agent',
            '/srv/rose-rango/inputs/signed-apps/dev.pixel.backgroundcontrol.apk', '--agent-tray', '--keep-upstream-updater',
            '--preinstall-apk', '/srv/rose-rango/inputs/signed-apps/dev.pixel.voice.apk',
            '--preinstall-apk', '/srv/rose-rango/inputs/signed-apps/dev.pixel.calculator.agent.apk'])

    def test_owner_changes_conflict_and_integration_roots_install(self):
        settings = CONFIG['host']['integrate']['reconcile']
        tmp = Path(tempfile.mkdtemp(prefix='rango-plan-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        project = tmp/'frameworks/base'
        (project/'core/res/res/values').mkdir(parents=True)
        config_xml = project/'core/res/res/values/config.xml'
        config_xml.write_text('<resources />\n')
        git = lambda *a: subprocess.run(['git', '-C', str(project), *a], check=True, capture_output=True)
        git('init', '-q')
        git('add', '.')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'head')
        config_xml.write_text('<resources><!-- owner edit --></resources>\n')
        changes = {'frameworks/base/core/res/res/values/config.xml': (b'<resources>policy</resources>\n', 0o644),
            'packages/apps/RosePreinstall/RosePreinstall_dev_pixel_voice/app.apk': (b'voice 310', 0o644)}
        (tmp/'packages/apps/RosePreinstall/RosePreinstall_dev_pixel_voice').mkdir(parents=True)
        (tmp/'packages/apps/RosePreinstall/RosePreinstall_dev_pixel_voice/app.apk').write_bytes(b'stale')
        actions, conflicts = hr.plan(tmp, settings['projects'], settings['integrationOwnedRoots'], changes, {'entries': {}})
        self.assertEqual(conflicts, ['frameworks/base/core/res/res/values/config.xml'], 'owner edit is never overwritten')
        self.assertEqual(list(actions), ['packages/apps/RosePreinstall/RosePreinstall_dev_pixel_voice/app.apk'])
        config_xml.write_text('<resources />\n')
        actions, conflicts = hr.plan(tmp, settings['projects'], settings['integrationOwnedRoots'], changes, {'entries': {}})
        self.assertEqual((sorted(actions), conflicts), (sorted(changes), []), 'pristine HEAD files install')

if __name__ == '__main__':
    unittest.main()
