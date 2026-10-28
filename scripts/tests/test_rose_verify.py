"""rose-verify: synthetic releases, payloads, images, screenshots and a fake adb; no network, device or SDK."""
import contextlib
import hashlib
import importlib.util
import io
import json
import lzma
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1]/'rose-verify.py'
spec = importlib.util.spec_from_file_location('rose_verify', SCRIPT)
rv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rv)
CONTRACT = json.loads(rv.CONTRACT.read_text())

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False
MKE2FS = shutil.which('mke2fs') or ('/usr/sbin/mke2fs' if Path('/usr/sbin/mke2fs').exists() else None)
DEBUGFS = shutil.which('debugfs') or ('/usr/sbin/debugfs' if Path('/usr/sbin/debugfs').exists() else None)

BADGING = """package: name='dev.example.voice' versionCode='302' versionName='302-test' platformBuildVersionName='15'
minSdkVersion:'29'
targetSdkVersion:'35'
application-label:'Example'
launchable-activity: name='dev.example.voice.MainActivity'  label='' icon=''
native-code: 'arm64-v8a' 'x86_64'
"""
XMLTREE = """N: android=http://schemas.android.com/apk/res/android (line=1)
  E: manifest (line=1)
    A: package="dev.example.voice" (Raw: "dev.example.voice")
    E: application (line=3)
      E: activity (line=4)
        A: http://schemas.android.com/apk/res/android:name(0x01010003)=".MainActivity" (Raw: ".MainActivity")
        A: http://schemas.android.com/apk/res/android:exported(0x01010010)=(type 0x12)0xffffffff
        E: intent-filter (line=5)
          E: action (line=6)
            A: http://schemas.android.com/apk/res/android:name(0x01010003)="android.intent.action.MAIN" (Raw: "android.intent.action.MAIN")
          E: category (line=7)
            A: http://schemas.android.com/apk/res/android:name(0x01010003)="android.intent.category.LAUNCHER" (Raw: "android.intent.category.LAUNCHER")
        E: intent-filter (line=8)
          E: action (line=9)
            A: http://schemas.android.com/apk/res/android:name(0x01010003)="android.intent.action.MAIN" (Raw: "android.intent.action.MAIN")
          E: category (line=10)
            A: http://schemas.android.com/apk/res/android:name(0x01010003)="android.intent.category.HOME" (Raw: "android.intent.category.HOME")
      E: activity (line=12)
        A: http://schemas.android.com/apk/res/android:name(0x01010003)="dev.example.voice.Other" (Raw: "dev.example.voice.Other")
"""


def varint(n):
    out = b''
    while True:
        b = n & 0x7f
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def field(no, value):
    if isinstance(value, int):
        return varint(no << 3) + varint(value)
    return varint(no << 3 | 2) + varint(len(value)) + value


def make_payload(partitions, block=4096, corrupt=False):
    """A full-OTA payload.bin: per partition, a REPLACE_XZ of the first half and a ZERO of the rest."""
    data, parts = b'', b''
    for name, image in partitions.items():
        blocks = len(image) // block
        half = blocks // 2
        blob = lzma.compress(image[:half * block])
        ops = field(8, field(1, 8) + field(2, len(data)) + field(3, len(blob)) +
                    field(6, field(1, 0) + field(2, half)) + field(8, hashlib.sha256(blob).digest()))
        ops += field(8, field(1, 6) + field(6, field(1, half) + field(2, blocks - half)))
        data += blob if not corrupt else blob[:-1] + bytes([blob[-1] ^ 1])
        info = field(1, len(image)) + field(2, hashlib.sha256(image).digest())
        parts += field(13, field(1, name.encode()) + field(7, info) + ops)
    manifest = field(3, block) + parts
    return b'CrAU' + struct.pack('>QQI', 2, len(manifest), 0) + manifest + data


def make_ota(path, payload, device='frankel'):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('META-INF/com/android/metadata', f'ota-type=AB\npre-device={device}\npost-build=test/{device}:17/X/1:user/release-keys\n'
                                                     'post-sdk-level=37\npost-timestamp=1\n')
        z.writestr('payload_properties.txt', 'FILE_HASH=x\nFILE_SIZE=1\n')
        z.writestr(zipfile.ZipInfo('payload.bin'), payload, compress_type=zipfile.ZIP_STORED)


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = rv.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ParseTests(unittest.TestCase):
    def test_badging(self):
        b = rv.parse_badging(BADGING)
        self.assertEqual((b['packageName'], b['versionCode'], b['versionName']), ('dev.example.voice', 302, '302-test'))
        self.assertEqual((b['minSdk'], b['targetSdk']), (29, 35))
        self.assertEqual(b['nativeCode'], ['arm64-v8a', 'x86_64'])
        self.assertEqual(b['launchableActivity'], 'dev.example.voice.MainActivity')

    def test_manifest_home_and_launcher(self):
        components = rv.manifest_components(rv.parse_xmltree(XMLTREE), 'dev.example.voice')
        self.assertEqual(rv.with_category(components, rv.HOME), ['dev.example.voice.MainActivity'])
        self.assertEqual(rv.with_category(components, rv.LAUNCHER), ['dev.example.voice.MainActivity'])
        self.assertEqual([c['name'] for c in components], ['dev.example.voice.MainActivity', 'dev.example.voice.Other'])

    def test_expected_names_both_targets_and_reference(self):
        e = rv.expected()
        self.assertEqual([a['package'] for a in e['apps']], [a['package'] for a in CONTRACT['apps']])
        self.assertIn('never bootable', e['targets']['beta']['runtime'])
        self.assertEqual(e['reference']['wallpaperSha256'],
                         hashlib.sha256((rv.ROOT/rv.WALLPAPER).read_bytes()).hexdigest())


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.rel = self.tmp/'release'
        self.rel.mkdir()
        pipeline = json.loads(rv.PIPELINE.read_text())
        (self.rel/'verification.json').write_text(json.dumps(
            {'candidate': {'build': '2026102899', 'sha': 'a'*40, 'timestamp': '1793180700'}, 'published': False}))
        (self.rel/'frankel-stable').write_text('2026102899 1793180700 frankel stable\n')
        make_ota(self.rel/'frankel-ota_update-2026102899.zip', make_payload({'product': bytes(4096*4)}))
        sums = [f'{rv.sha256(p)}  {p.name}' for p in sorted(self.rel.iterdir())]
        sums.append(f'{pipeline["identities"]["avbPublicKeySha256"]}  avb_pkmd.bin')
        (self.rel/'SHA256SUMS').write_text('\n'.join(sums) + '\n')

    def test_release_checks_and_arm_runtime_boundary(self):
        r = rv.release(self.rel, expect_source='a'*40)
        self.assertTrue(all(c['result'] == 'pass' for c in r['checks']), r['checks'])
        self.assertFalse(r['files']['avb_pkmd.bin'].get('present', True))
        self.assertEqual(r['ota']['metadata']['pre-device'], 'frankel')
        self.assertIs(r['runtime']['cuttlefishBootable'], False)
        self.assertFalse(r['runtime']['performed'])

    def test_tampered_file_and_wrong_source_fail(self):
        (self.rel/'frankel-stable').write_text('2026102899 1 frankel stable\n')
        code, out, _ = run_cli('release', str(self.rel), '--expect-source', 'b'*40)
        self.assertEqual(code, 1)
        failed = {c['check'] for c in json.loads(out)['checks'] if c['result'] == 'fail'}
        self.assertEqual(failed, {'SHA256SUMS frankel-stable', 'candidate source', 'frankel-stable line matches candidate'})


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_partition_hashes_are_enforced(self):
        image = os.urandom(4096) * 3 + bytes(4096 * 5)
        for corrupt in (False, True):
            ota = self.tmp/f'ota-{corrupt}.zip'
            make_ota(ota, make_payload({'product': image}, corrupt=corrupt))
            payload = rv.read_payload(ota)
            self.assertEqual([(p['name'], p['size']) for p in payload['partitions']], [('product', len(image))])
            if corrupt:
                with self.assertRaisesRegex(rv.Fail, 'operation data hash mismatch'):
                    rv.extract_partition(ota, payload, 'product', self.tmp/'bad.img')
            else:
                got = rv.extract_partition(ota, payload, 'product', self.tmp/'product.img')
                self.assertEqual((self.tmp/'product.img').read_bytes(), image)
                self.assertEqual(got['sha256'], hashlib.sha256(image).hexdigest())

    @unittest.skipUnless(MKE2FS and DEBUGFS, 'needs mke2fs and debugfs (e2fsprogs)')
    def test_ext4_reads_app_and_build_prop_and_lists_dirs(self):
        root = self.tmp/'root'
        (root/'app/RosePreinstall_x').mkdir(parents=True)
        (root/'etc').mkdir()
        apk = os.urandom(5000)
        (root/'app/RosePreinstall_x/RosePreinstall_x.apk').write_bytes(apk)
        (root/'etc/build.prop').write_text('# comment\nro.product.cpu.abi=arm64-v8a\nro.product.build.version.sdk=37\nro.other=1\n')
        img = self.tmp/'product.img'
        subprocess.run([MKE2FS, '-q', '-t', 'ext4', '-d', str(root), str(img), '8M'], check=True, capture_output=True,
                       env=dict(os.environ, E2FSPROGS_FAKE_TIME='1'))
        self.assertEqual(rv.image_read(img, 'app/RosePreinstall_x/RosePreinstall_x.apk'), ('ext4', apk))
        self.assertEqual(rv.image_read(img, 'app/missing.apk'), ('ext4', None))
        self.assertEqual(rv.build_props(img, 'product'),
                         {'ro.product.build.version.sdk': '37', 'ro.product.cpu.abi': 'arm64-v8a'})
        self.assertEqual(rv.image_list(img, 'app'), ['RosePreinstall_x'])


@unittest.skipUnless(HAVE_PIL, 'needs Pillow')
class CompareTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image, ImageDraw, ImageOps
        self.Image, self.ImageDraw, self.ImageOps = Image, ImageDraw, ImageOps
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.ref = rv.cover_crop(Image.open(rv.ROOT/rv.WALLPAPER).convert('RGB'), 540, 1200)

    def save(self, image, name):
        path = self.tmp/name
        image.save(path)
        return path

    def test_home_with_overlays_is_consistent(self):
        shot = self.ref.copy()
        draw = self.ImageDraw.Draw(shot)
        draw.rectangle((0, 0, 540, 40), fill='black')            # status bar
        draw.ellipse((170, 450, 370, 650), fill=(240, 240, 230))  # presence
        draw.rectangle((40, 1050, 500, 1150), fill='white')       # dock
        r = rv.compare(self.save(shot, 'home.png'), self.tmp/'out', label='home')
        self.assertEqual(r['verdict'], 'wallpaper-consistent', r)
        self.assertTrue(Path(r['sideBySide']).is_file())

    def test_black_and_unrelated_screens(self):
        black = rv.compare(self.save(self.Image.new('RGB', (540, 1200), (3, 3, 3)), 'black.png'), self.tmp/'out', label='b')
        self.assertEqual(black['verdict'], 'black-screen')
        other = rv.compare(self.save(self.ImageOps.invert(self.ref), 'inv.png'), self.tmp/'out', label='i')
        self.assertEqual(other['verdict'], 'differs-from-reference')

    def test_black_screen_fails_cli(self):
        shot = self.save(self.Image.new('RGB', (540, 1200), (0, 0, 0)), 'black.png')
        self.assertEqual(run_cli('compare', str(shot), '--out', str(self.tmp/'o'))[0], 1)


FAKE_ADB = r'''#!/usr/bin/env python3
import json, sys
state = json.load(open(sys.argv[0] + '.json'))
assert sys.argv[3] in ('shell', 'exec-out'), sys.argv
cmd = sys.argv[4] if len(sys.argv) > 4 else ''
log = open(sys.argv[0] + '.log', 'a'); log.write(cmd + '\n')
if cmd.startswith('getprop '):
    print(state['props'].get(cmd.split()[1], ''))
elif cmd.startswith('pm path '):
    pkg = cmd.split()[2]
    if pkg in state['paths']: print('package:' + state['paths'][pkg])
elif cmd.startswith('dumpsys package '):
    print('Packages:\n  Package [%s]\n    versionCode=%s minSdk=29 targetSdk=35\n    versionName=x' % (cmd.split()[2], state['versions'].get(cmd.split()[2], 0)))
elif cmd.startswith('sha256sum '):
    print(state['digests'][cmd.split()[1]] + '  ' + cmd.split()[1])
elif cmd.startswith('cmd package resolve-activity'):
    print('priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true\n' + state['home'])
elif cmd.startswith('cmd role get-role-holders'):
    print(state['home'].split('/')[0])
elif cmd.startswith('screencap'):
    sys.stdout.buffer.write(open(state['screenshot'], 'rb').read())
elif cmd.startswith('cat /data/local/tmp/rose-verify-ui.xml'):
    print(state['ui'])
elif cmd.startswith('dumpsys activity activities'):
    print('  topResumedActivity=ActivityRecord{1a2b u0 %s t7}' % state['focus'].pop(0))
    json.dump(state, open(sys.argv[0] + '.json', 'w'))
elif cmd.split()[0] in ('input', 'am', 'uiautomator', 'rm'):
    pass
else:
    sys.exit(3)
'''


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.adb = self.tmp/'adb'
        self.adb.write_text(FAKE_ADB.replace('#!/usr/bin/env python3', '#!' + sys.executable))
        self.adb.chmod(self.adb.stat().st_mode | stat.S_IEXEC)
        self.state = {'props': {'ro.product.device': 'vsoc_x86_64_only', 'sys.boot_completed': '1',
                                'persist.wm.debug.pixel_agent_tray': '1', 'ro.product.cpu.abi': 'x86_64'},
                      'paths': {}, 'versions': {}, 'digests': {}, 'home': 'dev.pixel.voice/.MainActivity'}
        for a in CONTRACT['apps']:
            path = f'/product/app/{a["package"]}/{a["package"]}.apk'
            self.state['paths'][a['package']] = path
            self.state['versions'][a['package']] = a['version']
            self.state['digests'][path] = a['sha256']

    def runtime(self, **kw):
        Path(str(self.adb) + '.json').write_text(json.dumps(self.state))
        return rv.runtime('emulator-0', kw.get('handback', 'INFRA-HANDBACK-TEST'), kw.get('out', self.tmp/'out'),
                          interactive=kw.get('interactive', False), physical=kw.get('physical', False),
                          adb_path=str(self.adb), settle=0)

    @unittest.skipUnless(HAVE_PIL, 'needs Pillow')
    def test_interactive_home_conversation_and_launcher_entry(self):
        from PIL import Image
        shot = self.tmp/'screen.png'
        rv.cover_crop(Image.open(rv.ROOT/rv.WALLPAPER).convert('RGB'), 540, 1200).save(shot)
        self.state.update(screenshot=str(shot), ui='<hierarchy><node text="" content-desc="Home" bounds="[0,0][540,1200]" '
                          'package="dev.pixel.voice"/><node text="" content-desc="Open Rose" bounds="[170,450][370,650]" '
                          'package="dev.pixel.voice"/></hierarchy>',
                          focus=['dev.pixel.voice/.MainActivity', 'dev.pixel.voice/.RoseConversationActivity',
                                 'dev.pixel.voice/.MainActivity', 'dev.pixel.voice/.PairingActivity',
                                 'dev.pixel.voice/.MainActivity'])
        r = self.runtime(interactive=True)
        self.assertEqual(r['kind'], 'runtime-interactive')
        self.assertTrue(r['ok'], [c for c in r['checks'] if c['result'] != 'pass'])
        self.assertEqual([s['step'] for s in r['steps']], ['home-key', 'home-to-conversation', 'conversation-back',
                                                            'launcher-icon-entry', 'home-key-again'])
        self.assertEqual(r['steps'][0]['comparison']['verdict'], 'wallpaper-consistent')
        self.assertEqual(r['steps'][3]['focus'], 'dev.pixel.voice/.PairingActivity')
        commands = Path(str(self.adb) + '.log').read_text()
        self.assertIn('input tap 270 550', commands)
        self.assertIn('rm -f /data/local/tmp/rose-verify-ui.xml', commands)

    def test_requires_handback_and_external_output(self):
        with self.assertRaisesRegex(rv.Fail, 'handback'):
            self.runtime(handback=' ')
        with self.assertRaisesRegex(rv.Fail, 'outside the repository'):
            self.runtime(out=rv.ROOT/'out/runtime')
        self.assertFalse(Path(str(self.adb) + '.log').exists(), 'no adb call before the gates pass')

    def test_readonly_capture_on_cuttlefish(self):
        r = self.runtime()
        self.assertEqual(r['kind'], 'runtime-readonly')
        self.assertTrue(r['ok'], [c for c in r['checks'] if c['result'] != 'pass'])
        self.assertEqual(r['device']['serial'], 'not recorded')
        self.assertTrue((self.tmp/'out/runtime-receipt.json').is_file())
        commands = Path(str(self.adb) + '.log').read_text().splitlines()
        mutating = [c for c in commands if c.split()[0] in ('input', 'am', 'uiautomator', 'screencap', 'rm', 'pm') and
                    not c.startswith('pm path')]
        self.assertEqual(mutating, [], 'read-only mode issues no input, launch, dump or removal')

    def test_wrong_bytes_and_other_home_are_reported(self):
        pkg = CONTRACT['apps'][0]['package']
        self.state['digests'][self.state['paths'][pkg]] = '0'*64
        self.state['home'] = 'com.android.launcher3/.uioverrides.QuickstepLauncher'
        r = self.runtime()
        failed = {c['check'] for c in r['checks'] if c['result'] == 'fail'}
        self.assertEqual(failed, {f'{pkg} active bytes equal the contract', 'HOME resolves to Rose Agent'})
        self.assertEqual(r['home']['roleHolders'], ['com.android.launcher3'])

    def test_physical_device_needs_explicit_flag(self):
        self.state['props']['ro.product.device'] = 'frankel'
        with self.assertRaisesRegex(rv.Fail, 'not a Cuttlefish guest'):
            self.runtime()
        self.assertTrue(self.runtime(physical=True)['device']['privateEvidence'])


class SourceTests(unittest.TestCase):
    def test_head_manifest_and_routing(self):
        r = rv.source('HEAD')
        self.assertEqual(r['kind'], 'source-inspection')
        self.assertEqual(r['package'], 'dev.pixel.voice')
        self.assertIn('dev.pixel.voice.MainActivity', r['homeActivities'])
        self.assertTrue(r['routing']['launcherPages'])
        self.assertFalse(r['runtime']['performed'])


if __name__ == '__main__':
    unittest.main()
