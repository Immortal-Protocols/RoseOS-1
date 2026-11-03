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
from unittest import mock
import zipfile

SCRIPT = Path(__file__).resolve().parents[1]/'rose-verify.py'
spec = importlib.util.spec_from_file_location('rose_verify', SCRIPT)
rv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rv)
CONTRACT = json.loads(rv.CONTRACT.read_text())
EXPECTATIONS = rv.ROOT/'qa/verification/expectations'
ALPHA = rv.load_expectation(EXPECTATIONS/'alpha-40719075730.json')
BETA = rv.load_expectation(EXPECTATIONS/'beta-frankel-2026102804.json')

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

    def test_short_component(self):
        self.assertEqual(rv.short_component('dev.pixel.voice/dev.pixel.voice.MainActivity'), 'dev.pixel.voice/.MainActivity')
        self.assertEqual(rv.short_component('dev.pixel.voice/.MainActivity'), 'dev.pixel.voice/.MainActivity')
        self.assertEqual(rv.short_component('app.grapheneos.setupwizard/app.grapheneos.setupwizard.view.activity.WelcomeActivity'),
                         'app.grapheneos.setupwizard/.view.activity.WelcomeActivity')
        self.assertEqual(rv.short_component('android/com.android.internal.app.ResolverActivity'),
                         'android/com.android.internal.app.ResolverActivity')

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


class WallpaperReferenceTests(unittest.TestCase):
    """APP-OS-VISUAL-1030-M002: the reference is the selected HiFi wallpaper, a fixed expectation; equality only."""
    SELECTED = '67e3975453b7caa1fc344b12f1f82088747c3aa5730621af72b7ecf02d0463c5'
    FOLD_V3 = '409c0dbfa309dbd56f97a59b04223532e9931ae5c5c4625d3567b77a9c56d8c5'
    FOLD_V3_BLOB = '0d46158cb03c5add4d16ad0125add805e649cce3'

    def check_with(self, data):
        real = rv.git_show
        with mock.patch.object(rv, 'git_show', side_effect=lambda rev, path: data if path == rv.WALLPAPER else real(rev, path)):
            r = rv.source('HEAD')
        return r['wallpaperSha256'], next(c for c in r['checks'] if c['check'] == 'Reference wallpaper bytes')['result']

    def test_fixed_expectation_and_resource_bytes(self):
        # A literal, not computed from the asset under test.
        self.assertEqual(rv.REFERENCE['wallpaperSha256'], self.SELECTED)
        data = (rv.ROOT/rv.WALLPAPER).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), self.SELECTED)
        self.assertEqual(len(data), 286252)
        self.assertIn('ce126f56c8967008b0a5db889137f224ac9ad36c', rv.REFERENCE['prototype'])
        self.assertIn('1d99a767629749ee23a08487a0352d214ef29720', rv.REFERENCE['wallpaperBasis'])
        self.assertIn(self.FOLD_V3, rv.REFERENCE['wallpaperBasis'])

    def test_selected_bytes_pass(self):
        digest, result = self.check_with((rv.ROOT/rv.WALLPAPER).read_bytes())
        self.assertEqual((digest, result), (self.SELECTED, 'pass'))

    def test_tampered_bytes_fail(self):
        data = bytearray((rv.ROOT/rv.WALLPAPER).read_bytes())
        data[len(data) // 2] ^= 1
        digest, result = self.check_with(bytes(data))
        self.assertNotEqual(digest, self.SELECTED)
        self.assertEqual(result, 'fail')

    def test_previous_fold_v3_image_fails(self):
        p = subprocess.run(['git', '-C', str(rv.ROOT), 'cat-file', 'blob', self.FOLD_V3_BLOB], capture_output=True)
        if p.returncode:
            self.skipTest('shallow checkout without the previous wallpaper blob')
        self.assertEqual(hashlib.sha256(p.stdout).hexdigest(), self.FOLD_V3)
        digest, result = self.check_with(p.stdout)
        self.assertEqual((digest, result), (self.FOLD_V3, 'fail'))


class HomeCropTests(unittest.TestCase):
    """APP-OS-VISUAL-1030-M004: compare crops the wallpaper as production Home draws it (HomeAppLayout.cover at
    62% 30%, Java float arithmetic and Math.round), not centred. Expected boxes come from hand calculation, a
    coordinate-coded image and the production Java itself, never from the helper under test."""

    def test_known_boxes(self):
        # Portrait phone over the 1024 x 1536 HiFi image: crop 1536*1080/2400 = 691.2 -> 691 wide,
        # left (1024-691)*.62 = 206.46 -> 206.
        self.assertEqual(rv.cover_box(1024, 1536, 1080, 2400), (206, 0, 897, 1536))
        # Landscape: crop 1024*1080/2400 = 460.8 -> 461 tall, top (1536-461)*.30 = 322.5(+) -> 323.
        self.assertEqual(rv.cover_box(1024, 1536, 2400, 1080), (0, 323, 1024, 784))
        # Equal aspect: the whole image.
        self.assertEqual(rv.cover_box(1024, 1536, 512, 768), (0, 0, 1024, 1536))
        # Rounding-sensitive: 15 * .3f = 4.5000002 rounds half up to 5 (Python's round(15 * .3) would give 4).
        self.assertEqual(rv.cover_box(20, 35, 20, 20), (0, 5, 20, 25))
        # Odd sizes: 11 * 7 / 9 = 8.56 -> 9 wide, left (13-9)*.62 = 2.48 -> 2.
        self.assertEqual(rv.cover_box(13, 11, 7, 9), (2, 0, 11, 11))
        self.assertEqual(rv.cover_box(0, 0, 1080, 2400), (0, 0, 0, 0))
        self.assertEqual(rv.HOME_CROP['x'], .62)
        self.assertEqual(rv.HOME_CROP['y'], .30)

    def test_centred_crop_is_not_home(self):
        # Negative control: the previous centred crop puts the portrait band at 166, not Home's 206.
        bw, bh, w, h = 1024, 1536, 1080, 2400
        crop = round(bh * w / h)
        self.assertNotEqual(rv.cover_box(bw, bh, w, h)[0], (bw - crop) // 2)

    def test_constants_match_production_home(self):
        layout = (rv.ROOT/'workspace/rose-agent/app/src/dev/pixel/voice/HomeAppLayout.java').read_text()
        self.assertIn('WALL_X = .62f, WALL_Y = .30f', layout)
        # #410: Home draws the Oxblood field (OxbloodField.HOME, MainActivity's pager background), no longer this image;
        # the crop constants remain HomeAppLayout's, and rose-verify's Home screenshot reference needs its own update.
        main = (rv.ROOT/'workspace/rose-agent/app/src/dev/pixel/voice/MainActivity.java').read_text()
        self.assertIn('launcherPager.setBackground(new OxbloodField(OxbloodField.HOME));', main)

    @unittest.skipUnless(HAVE_PIL, 'needs Pillow')
    def test_coordinate_coded_image_crops_at_the_home_origin(self):
        from PIL import Image
        bw, bh = 64, 96
        image = Image.new('RGB', (bw, bh))
        image.putdata([(x, y, 0) for y in range(bh) for x in range(bw)])
        for (w, h), origin in {(43, 96): (13, 0), (64, 29): (0, 20), (64, 96): (0, 0)}.items():
            # Areas equal to the crop size, so resizing is the identity and each pixel still names its source.
            out = rv.cover_crop(image, w, h)
            self.assertEqual(out.getpixel((0, 0))[:2], origin, (w, h))
            self.assertEqual(out.getpixel((w - 1, h - 1))[:2], (origin[0] + w - 1, origin[1] + h - 1), (w, h))

    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'needs a JDK')
    def test_matches_production_java(self):
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out)
        driver = out/'dev/pixel/voice/CoverDriver.java'
        driver.parent.mkdir(parents=True)
        driver.write_text('package dev.pixel.voice;\npublic class CoverDriver { public static void main(String[] a) {\n'
                          ' for (int i = 0; i < a.length; i += 4) { int[] b = HomeAppLayout.cover(Integer.parseInt(a[i]),'
                          ' Integer.parseInt(a[i + 1]), Float.parseFloat(a[i + 2]), Float.parseFloat(a[i + 3]),'
                          ' HomeAppLayout.WALL_X, HomeAppLayout.WALL_Y);\n'
                          '  System.out.println(b[0] + " " + b[1] + " " + b[2] + " " + b[3]); } } }\n')
        subprocess.run(['javac', '-d', str(out), str(rv.ROOT/'workspace/rose-agent/app/src/dev/pixel/voice/HomeAppLayout.java'),
                        str(driver)], check=True, capture_output=True)
        cases = [(bw, bh, w, h) for bw, bh in ((1024, 1536), (461, 1024), (13, 11), (20, 35), (1, 1), (999, 1001))
                 for w, h in ((1080, 2400), (2400, 1080), (1440, 3120), (2208, 1840), (7, 9), (20, 20), (411, 914), (1, 3))]
        args = [str(v) for case in cases for v in case]
        lines = subprocess.run(['java', '-cp', str(out), 'dev.pixel.voice.CoverDriver', *args], check=True,
                               capture_output=True, text=True).stdout.split('\n')
        for case, line in zip(cases, lines):
            self.assertEqual(rv.cover_box(*case), tuple(int(v) for v in line.split()), case)


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

    def test_expectation_binds_candidate_build_and_device(self):
        e = json.loads(Path(BETA['path']).read_text())
        e.update(candidate='a'*40, build='2026102899')
        path = self.tmp/'e.json'
        path.write_text(json.dumps(e))
        r = rv.release(self.rel, expectation=rv.load_expectation(path))
        self.assertTrue(all(c['result'] == 'pass' for c in r['checks']), r['checks'])
        self.assertEqual(r['expectation']['candidate'], 'a'*40)
        e.update(build='2026102804', target={'device': 'vsoc_x86_64_only'})
        path.write_text(json.dumps(e))
        failed = {c['check'] for c in rv.release(self.rel, expectation=rv.load_expectation(path))['checks']
                  if c['result'] == 'fail'}
        self.assertEqual(failed, {'candidate build', 'OTA device equals expectation target'})


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

    def test_cli_side_by_side_is_named_after_each_screenshot(self):
        a = self.save(self.ref, 'home.png')
        b = self.save(self.ImageOps.invert(self.ref), 'conversation.png')
        for shot in (a, b):
            self.assertIn(run_cli('compare', str(shot), '--out', str(self.tmp/'o'))[0], (0, 1))
        self.assertEqual(sorted(p.name for p in (self.tmp/'o').iterdir()),
                         ['conversation-vs-reference.png', 'home-vs-reference.png'])

    def test_black_screen_fails_cli(self):
        shot = self.save(self.Image.new('RGB', (540, 1200), (0, 0, 0)), 'black.png')
        self.assertEqual(run_cli('compare', str(shot), '--out', str(self.tmp/'o'))[0], 1)


FAKE_ADB = r'''#!/usr/bin/env python3
import json, sys
state = json.load(open(sys.argv[0] + '.json'))
assert sys.argv[3] in ('shell', 'exec-out'), sys.argv
cmd = sys.argv[4] if len(sys.argv) > 4 else ''
log = open(sys.argv[0] + '.log', 'a'); log.write(cmd + '\n')
words = cmd.split()
user = words[words.index('--user') + 1] if '--user' in words else None
if cmd.startswith(('pm path', 'cmd package resolve-activity', 'cmd role', 'am start', 'settings')) and user is None:
    sys.exit(3)  # every user-scoped command must name the tested user
if cmd.startswith('getprop '):
    print(state['props'].get(words[1], ''))
elif cmd == 'am get-current-user':
    print(state['current'])
elif cmd == 'pm list users':
    print('Users:')
    for uid, name, flags, running in state['users']:
        print('\tUserInfo{%s:%s:%s}%s' % (uid, name, flags, ' running' if running else ''))
elif cmd.startswith('pm path '):
    pkg = words[-1]
    if int(user) in state['installed'].get(pkg, []): print('package:' + state['paths'][pkg])
elif cmd.startswith('dumpsys package '):
    pkg = words[2]
    print('Packages:\n  Package [%s]\n    versionCode=%s minSdk=29 targetSdk=35\n    versionName=x\n    pkgFlags=[ SYSTEM HAS_CODE ]' % (pkg, state['versions'].get(pkg, 0)))
    for uid, _, _, _ in state['users']:
        print('    User %s: ceDataInode=1 installed=%s hidden=false suspended=false stopped=false enabled=0'
              % (uid, 'true' if uid in state['installed'].get(pkg, []) else 'false'))
elif cmd.startswith('sha256sum '):
    print(state['digests'][words[1]] + '  ' + words[1])
elif cmd.startswith('cmd package resolve-activity'):
    print('priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true\n' + state['home'][user])
elif cmd.startswith('cmd role get-role-holders'):
    print(state['roles'][user])
elif cmd.startswith('settings ') and words[3:5] == ['get', 'secure']:
    print(state['markers'].get(user, 'null'))
elif cmd.startswith('screencap'):
    sys.stdout.buffer.write(open(state['screenshot'], 'rb').read())
elif cmd.startswith('cat /data/local/tmp/rose-verify-ui.xml'):
    ui = state['ui']
    if isinstance(ui, list):
        print(ui.pop(0))
        json.dump(state, open(sys.argv[0] + '.json', 'w'))
    else:
        print(ui)
elif cmd.startswith('dumpsys activity activities'):
    print('  topResumedActivity=ActivityRecord{1a2b u0 %s t7}' % state['focus'].pop(0))
    json.dump(state, open(sys.argv[0] + '.json', 'w'))
elif words[0] in ('input', 'am', 'uiautomator', 'rm'):
    pass
else:
    sys.exit(3)
'''
READONLY_PREFIXES = ('getprop', 'am get-current-user', 'pm list users', 'pm path', 'dumpsys package', 'sha256sum',
                     'cmd package resolve-activity', 'cmd role get-role-holders', 'settings --user 0 get secure ',
                     'settings --user 10 get secure ')


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.adb = self.tmp/'adb'
        self.adb.write_text(FAKE_ADB.replace('#!/usr/bin/env python3', '#!' + sys.executable))
        self.adb.chmod(self.adb.stat().st_mode | stat.S_IEXEC)
        rose = 'dev.pixel.voice/.MainActivity'
        self.state = {'props': {'ro.product.device': 'vsoc_x86_64_only', 'sys.boot_completed': '1',
                                'persist.wm.debug.pixel_agent_tray': '1', 'ro.product.cpu.abi': 'x86_64'},
                      'current': 0, 'users': [[0, 'Private Owner Name', 'c13', True], [10, 'Private Work', '1030', False]],
                      'installed': {}, 'paths': {}, 'versions': {}, 'digests': {},
                      'home': {'0': rose, '10': rose}, 'roles': {'0': 'dev.pixel.voice', '10': 'dev.pixel.voice'},
                      'markers': {'0': '1'}}
        for a in ALPHA['apps']:
            path = f'/product/app/{a["package"]}/{a["package"]}.apk'
            self.state['installed'][a['package']] = [0, 10]
            self.state['paths'][a['package']] = path
            self.state['versions'][a['package']] = a['version']
            self.state['digests'][path] = a['sha256']

    def runtime(self, **kw):
        Path(str(self.adb) + '.json').write_text(json.dumps(self.state))
        return rv.runtime('emulator-0', kw.get('handback', 'INFRA-HANDBACK-TEST'), kw.get('out', self.tmp/'out'),
                          kw.get('expectation', ALPHA), user=kw.get('user', 'current'),
                          interactive=kw.get('interactive', False), physical=kw.get('physical', False),
                          adb_path=str(self.adb), settle=0)

    def commands(self):
        log = Path(str(self.adb) + '.log')
        return log.read_text().splitlines() if log.exists() else []

    def failed(self, r):
        return {c['check'] for c in r['checks'] if c['result'] == 'fail'}

    ROSE_UI = ('<hierarchy><node text="" content-desc="Home" bounds="[0,0][540,1200]" package="dev.pixel.voice"/>'
               '<node text="" content-desc="Open Rose" bounds="[170,450][370,650]" package="dev.pixel.voice"/></hierarchy>')
    OTHER_UI = '<hierarchy><node text="Welcome" content-desc="" bounds="[0,0][540,1200]" package="app.grapheneos.setupwizard"/></hierarchy>'
    STEPS = ['home-key', 'explicit-rose-home', 'home-to-conversation', 'conversation-back', 'launcher-icon-entry',
             'home-key-again']

    def interactive_state(self):
        from PIL import Image
        shot = self.tmp/'screen.png'
        rv.cover_crop(Image.open(rv.ROOT/rv.WALLPAPER).convert('RGB'), 540, 1200).save(shot)
        self.state.update(screenshot=str(shot), ui=self.ROSE_UI,
                          focus=['dev.pixel.voice/.MainActivity', 'dev.pixel.voice/.MainActivity',
                                 'dev.pixel.voice/.RoseConversationActivity', 'dev.pixel.voice/.MainActivity',
                                 'dev.pixel.voice/.PairingActivity', 'dev.pixel.voice/.MainActivity'])

    @unittest.skipUnless(HAVE_PIL, 'needs Pillow')
    def test_interactive_home_conversation_and_launcher_entry(self):
        self.interactive_state()
        r = self.runtime(interactive=True)
        self.assertEqual(r['kind'], 'runtime-interactive')
        self.assertEqual(self.failed(r), set())
        self.assertEqual([s['step'] for s in r['steps']], self.STEPS)
        self.assertEqual({s['user'] for s in r['steps']}, {0})
        self.assertEqual(r['steps'][0]['comparison']['verdict'], 'wallpaper-consistent')
        self.assertIn('does not prove the HOME default', r['steps'][1]['entry'])
        self.assertEqual(r['steps'][4]['focus'], 'dev.pixel.voice/.PairingActivity')
        commands = self.commands()
        self.assertIn('input tap 270 550', commands)
        self.assertIn('rm -f /data/local/tmp/rose-verify-ui.xml', commands)
        self.assertIn('am start --user 0 -W -a android.intent.action.MAIN -c android.intent.category.HOME '
                      '-n dev.pixel.voice/.MainActivity', commands)
        self.assertEqual(commands.count('am get-current-user'), 2, 'foreground user read before and after')

    @unittest.skipUnless(HAVE_PIL, 'needs Pillow')
    def test_other_home_holder_fails_system_home_but_explicit_rose_home_is_labeled(self):
        self.interactive_state()
        setup = 'app.grapheneos.setupwizard/.view.activity.WelcomeActivity'
        self.state['home']['0'] = setup
        self.state['roles']['0'] = 'app.grapheneos.setupwizard'
        self.state['ui'] = [self.OTHER_UI] + [self.ROSE_UI] * 4 + [self.OTHER_UI]
        self.state['focus'][0] = self.state['focus'][5] = setup
        r = self.runtime(interactive=True)
        self.assertEqual(self.failed(r), {'HOME role held by Rose Agent for user 0',
                                          'unqualified MAIN/HOME resolves to Rose Agent for user 0',
                                          'system HOME key shows Rose Home page'})
        passed = {c['check'] for c in r['checks'] if c['result'] == 'pass'}
        self.assertTrue({'explicit Rose HOME shows Rose Home page (explicit component)',
                         'Rose entry opens RoseConversationActivity',
                         'Back from the conversation returns to Rose Home'} <= passed)
        self.assertFalse(r['ok'], 'an explicit launch never makes up for the system HOME result')
        self.assertEqual(r['steps'][0]['focus'], setup)

    def test_host_without_pillow_keeps_screenshots_and_reports_comparison_unavailable(self):
        if HAVE_PIL:
            self.interactive_state()
        else:
            shot = self.tmp/'screen.png'
            shot.write_bytes(b'\x89PNG fake')
            self.state.update(screenshot=str(shot), ui=self.ROSE_UI,
                              focus=['dev.pixel.voice/.MainActivity', 'dev.pixel.voice/.MainActivity',
                                     'dev.pixel.voice/.RoseConversationActivity', 'dev.pixel.voice/.MainActivity',
                                     'dev.pixel.voice/.PairingActivity', 'dev.pixel.voice/.MainActivity'])
        with mock.patch.object(rv, 'compare', side_effect=rv.Fail('screenshot comparison needs Pillow', 4)):
            r = self.runtime(interactive=True)
        self.assertEqual(self.failed(r), set())
        self.assertEqual({s['comparison']['verdict'] for s in r['steps'] if 'comparison' in s}, {'unavailable'})
        self.assertEqual([c for c in r['unmeasured'] if c.endswith('screenshot not black')],
                         ['home-key screenshot not black', 'explicit-rose-home screenshot not black',
                          'launcher-icon-entry screenshot not black'])
        self.assertTrue((self.tmp/'out/home-key.png').is_file())

    def test_requires_handback_expectation_and_external_output(self):
        with self.assertRaisesRegex(rv.Fail, 'handback'):
            self.runtime(handback=' ')
        with self.assertRaisesRegex(rv.Fail, '--expect'):
            self.runtime(expectation=None)
        with self.assertRaisesRegex(rv.Fail, 'outside the repository'):
            self.runtime(out=rv.ROOT/'out/runtime')
        with self.assertRaisesRegex(rv.Fail, 'numeric user id'):
            self.runtime(user='owner')
        self.assertEqual(self.commands(), [], 'no adb call before the gates pass')

    def test_readonly_capture_binds_every_observation_to_the_current_user(self):
        r = self.runtime()
        self.assertEqual(r['kind'], 'runtime-readonly')
        self.assertEqual(self.failed(r), set())
        self.assertEqual(r['device']['serial'], 'not recorded')
        self.assertEqual((r['user']['tested'], r['user']['selection'], r['user']['foreground']), (0, 'current', 0))
        self.assertEqual(r['user']['users'], [{'id': 0, 'flags': 'c13', 'running': True},
                                              {'id': 10, 'flags': '1030', 'running': False}])
        self.assertNotIn('Private', json.dumps(r), 'user names are never recorded')
        self.assertEqual({a['user'] for a in r['apps']} | {r['home']['user']}, {0})
        self.assertEqual((r['home']['rose_home_role_selected'], {a['systemApp'] for a in r['apps']}), ('1', {True}))
        self.assertEqual([c['result'] for c in r['checks'] if c['check'] == 'user 10 observations'], ['unavailable'])
        self.assertEqual((r['ok'], r['unmeasured']), (True, ['user 10 observations']))
        self.assertEqual(r['expectation']['label'], ALPHA['label'])
        self.assertTrue(r['method'].startswith('rose-verify r3'))
        self.assertTrue((self.tmp/'out/runtime-receipt.json').is_file())
        commands = self.commands()
        self.assertEqual([c for c in commands if not c.startswith(READONLY_PREFIXES)], [],
                         'read-only mode issues no input, launch, dump or removal')
        scoped = [c for c in commands if c.startswith(('pm path', 'cmd package resolve-activity', 'cmd role', 'settings'))]
        self.assertTrue(scoped and all('--user 0 ' in c for c in scoped), scoped)

    def test_explicit_background_user_is_read_only_and_interactive_unavailable(self):
        self.state['installed'][ALPHA['apps'][0]['package']] = [0]
        self.state['home']['10'] = 'com.android.launcher3/.uioverrides.QuickstepLauncher'
        self.state['roles']['10'] = 'com.android.launcher3'
        r = self.runtime(user='10', interactive=True)
        self.assertEqual((r['user']['tested'], r['user']['selection'], r['user']['notTested']), (10, 'explicit --user', [0]))
        pkg = ALPHA['apps'][0]['package']
        self.assertEqual(self.failed(r), {f'{pkg} installed for user 10',
                                          f'{pkg} active bytes equal expectation (path for the tested user)', 'HOME role held by Rose Agent for user 10',
                                          'unqualified MAIN/HOME resolves to Rose Agent for user 10'})
        self.assertEqual([c['result'] for c in r['checks'] if c['check'] == 'interactive observations for user 10'],
                         ['unavailable'])
        self.assertNotIn('steps', r)
        self.assertIsNone(r['home']['rose_home_role_selected'], 'user 10 has no marker; user 0 is not read for it')
        self.assertEqual([c for c in self.commands() if not c.startswith(READONLY_PREFIXES)], [])

    def test_unknown_user_or_unreadable_current_user_fails_closed(self):
        with self.assertRaisesRegex(rv.Fail, 'user 11 is not on the device'):
            self.runtime(user='11')
        self.state['current'] = ''
        with self.assertRaisesRegex(rv.Fail, 'current user is unavailable'):
            self.runtime()

    def test_wrong_bytes_and_other_home_are_reported(self):
        pkg = ALPHA['apps'][0]['package']
        self.state['digests'][self.state['paths'][pkg]] = '0'*64
        self.state['home']['0'] = 'com.android.launcher3/.uioverrides.QuickstepLauncher'
        self.state['roles']['0'] = 'com.android.launcher3'
        r = self.runtime()
        self.assertEqual(self.failed(r), {f'{pkg} active bytes equal expectation (path for the tested user)', 'HOME role held by Rose Agent for user 0',
                                          'unqualified MAIN/HOME resolves to Rose Agent for user 0'})
        self.assertEqual(r['home']['roleHolders'], ['com.android.launcher3'])

    def test_guest_must_match_expectation_target(self):
        self.state['props']['ro.product.device'] = 'vsoc_x86_64'
        self.assertEqual(self.failed(self.runtime()), {'guest device equals expectation target'})

    def test_release_extraction_without_image_path_fails_closed(self):
        e = json.loads(Path(BETA['path']).read_text())
        for app in e['apps']:
            del app['imagePath']
        path = self.tmp/'e.json'
        path.write_text(json.dumps(e))
        self.assertEqual(rv.image_location(rv.load_expectation(path)['apps'][0]), None)

    def test_physical_device_needs_explicit_flag(self):
        self.state['props']['ro.product.device'] = 'frankel'
        with self.assertRaisesRegex(rv.Fail, 'not a Cuttlefish guest'):
            self.runtime()
        r = self.runtime(physical=True, expectation=BETA)
        self.assertTrue(r['device']['privateEvidence'])
        self.assertEqual(self.failed(r), set())


    # #410: runtime compares Home screenshots with the reference the expectation names.
    def test_oxblood_expectation_selects_the_oxblood_reference(self):
        if not HAVE_PIL:
            self.skipTest('needs Pillow')
        from PIL import Image
        self.interactive_state()
        Image.new('RGB', (540, 1200), (60, 12, 20)).save(self.state['screenshot'])
        r = self.runtime(interactive=True, expectation=dict(ALPHA, home='oxblood'))
        self.assertEqual(r['homeReference'], {'surface': 'oxblood', 'selection': 'expectation home'})
        verdicts = {s['comparison']['home'] + ':' + s['comparison']['verdict'] for s in r['steps'] if 'comparison' in s}
        self.assertEqual(verdicts, {'oxblood:not-compared'})
        self.assertEqual(self.failed(r), set())
        self.assertNotIn('home-key screenshot not black', r['unmeasured'])

    def test_expectation_without_home_keeps_the_wallpaper_reference(self):
        if not HAVE_PIL:
            self.skipTest('needs Pillow')
        self.interactive_state()
        r = self.runtime(interactive=True)
        self.assertEqual(r['homeReference']['surface'], 'hifi-wallpaper')
        self.assertIn('names no home', r['homeReference']['selection'])
        self.assertEqual(r['steps'][0]['comparison']['verdict'], 'wallpaper-consistent')


class ExpectationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def write(self, obj):
        path = self.tmp/'e.json'
        path.write_text(json.dumps(obj))
        return path

    def test_checked_in_expectations_equal_the_existing_pins(self):
        pipeline = json.loads(rv.PIPELINE.read_text())
        for e in (ALPHA, BETA):
            self.assertEqual([{k: a[k] for k in ('package', 'version', 'sha256')} for a in e['apps']], CONTRACT['apps'])
            self.assertEqual(e['candidate'], CONTRACT['base']['candidate'])
            self.assertEqual(e['appSignerSha256'], pipeline['identities']['appSignerSha256'])
            self.assertEqual([a['imagePath'] for a in e['apps']], ['/'.join(rv.APP_PATHS[a['package']]) for a in e['apps']])
        self.assertEqual(ALPHA['target']['device'], CONTRACT['target']['device'])
        self.assertEqual(ALPHA['target']['lunch'], CONTRACT['target']['lunch'])
        self.assertEqual((BETA['build'], BETA['target']['device']), ('2026102804', pipeline['target']['device']))
        self.assertEqual(BETA['avbPublicKeySha256'], pipeline['identities']['avbPublicKeySha256'])

    def test_app_304_expectation_is_its_own_candidate(self):
        app = rv.load_expectation(EXPECTATIONS/'app-rose-agent-304.json')
        self.assertEqual((app['channel'], app['candidate'], [a['version'] for a in app['apps']]),
                         ('app', 'a3d839e6805b514aa6cad6f86c15e5db622e10cc', [304]))
        self.assertNotIn('device', app['target'], 'app-only: no device target')
        self.assertNotEqual(app['apps'][0]['sha256'], ALPHA['apps'][0]['sha256'])
        self.assertEqual(app['appSignerSha256'], ALPHA['appSignerSha256'])

    def test_malformed_expectations_are_rejected(self):
        base = {k: v for k, v in json.loads(Path(ALPHA['path']).read_text()).items()}
        cases = {
            'unknown key appSigner': dict(base, appSigner='x'),
            'appSignerSha256 must be 64': {k: v for k, v in base.items() if k != 'appSignerSha256'},
            'candidate must be a full 40-hex': dict(base, candidate=base['candidate'][:8]),
            'sha256 must be 64': dict(base, apps=[dict(base['apps'][0], sha256=None)]),
            'version must be an integer': dict(base, apps=[dict(base['apps'][0], version='305')]),
            'repeated': dict(base, apps=[base['apps'][0], base['apps'][0]]),
            'apps must be a non-empty list': dict(base, apps=[]),
            'channel must be': dict(base, channel='prod'),
            'imagePath must be': dict(base, apps=[dict(base['apps'][0], imagePath='/data/app/x.apk')]),
            'build must be a digit string': dict(base, build=2026102804),
        }
        for message, obj in cases.items():
            with self.subTest(message):
                with self.assertRaisesRegex(rv.Fail, message):
                    rv.load_expectation(self.write(obj))
        self.assertEqual(run_cli('expected', '--expect', str(self.tmp/'missing.json'))[0], 2)

    def test_release_extraction_needs_expectation_and_consistent_source(self):
        rel = self.tmp/'rel'
        rel.mkdir()
        (rel/'SHA256SUMS').write_text('')
        self.assertEqual(run_cli('release', str(rel), '--extract-apps', str(self.tmp/'x'))[0], 2)
        code, _, err = run_cli('release', str(rel), '--expect', ALPHA['path'], '--expect-source', 'b'*40)
        self.assertEqual(code, 2)
        self.assertIn('contradicts', err)


class SourceTests(unittest.TestCase):
    def test_head_manifest_and_routing(self):
        r = rv.source('HEAD')
        self.assertEqual(r['kind'], 'source-inspection')
        self.assertEqual(r['package'], 'dev.pixel.voice')
        self.assertIn('dev.pixel.voice.MainActivity', r['homeActivities'])
        self.assertTrue(r['routing']['launcherPages'])
        self.assertIn('CATEGORY_HOME', r['routing']['entryMethod'])
        self.assertFalse(r['runtime']['performed'])

    def test_beta_302_routing_when_history_is_available(self):
        beta = 'ae9cfd4d9fafd2db4f2bc6aa76b7d025d6473fb9'
        if subprocess.run(['git', '-C', str(rv.ROOT), 'cat-file', '-e', beta + '^{commit}']).returncode:
            self.skipTest('shallow checkout without the beta commit')
        r = rv.source(beta)
        self.assertEqual((r['versionCode'], r['routing']['homeEntryRules']), (302, None))
        self.assertIn('showPage(homePage,false)', r['routing']['entryMethod'])


OXBLOOD_CONTRACT = json.loads((rv.ROOT/'qa/oxblood/oxblood-contract.json').read_text())
# PR417's MainActivity line (#410 M1): Home's pager background is the Oxblood field, not the wallpaper.
OXBLOOD_MAIN_LINE = 'launcherPager.setBackground(new OxbloodField(OxbloodField.HOME));'


def git_blob(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


class OxbloodReferenceTests(unittest.TestCase):
    """#410: Oxblood builds implement the frozen f6503a0 Oxblood reference (contract #414), not the HiFi wallpaper."""

    def test_pins_are_the_contract_reference_and_the_frozen_field(self):
        ref = OXBLOOD_CONTRACT['reference']
        self.assertIn(ref['commit'], rv.OXBLOOD['prototype'])
        self.assertIn(ref['artifactBlob'], rv.OXBLOOD['prototype'])
        self.assertTrue(rv.OXBLOOD['fieldPath'].endswith('/' + OXBLOOD_CONTRACT['fields']['home']['source']))
        self.assertIn('OxbloodField.java (HOME)', OXBLOOD_CONTRACT['fields']['home']['nativeRenderer'])
        # Literals, checked against the tree's bytes (unchanged since f6503a0: same Git blob).
        self.assertEqual(rv.OXBLOOD['fieldBlob'], 'e07c4586ad2c9c26a19dce6d9434a0f35dbc6ea5')
        data = (rv.ROOT/rv.OXBLOOD['fieldPath']).read_bytes()
        self.assertEqual(git_blob(data), rv.OXBLOOD['fieldBlob'])
        self.assertEqual(hashlib.sha256(data).hexdigest(), rv.OXBLOOD['fieldSha256'])
        self.assertEqual(rv.expected()['oxbloodReference'], rv.OXBLOOD)
        self.assertEqual(rv.expected()['reference'], rv.REFERENCE, 'the wallpaper reference stays for its builds')

    def test_crop_is_the_contract_placement(self):
        placement = OXBLOOD_CONTRACT['fields']['home']['placement']
        self.assertIn('centred horizontally', placement)
        self.assertIn('top-aligned', placement)
        self.assertEqual((rv.OXBLOOD_CROP['x'], rv.OXBLOOD_CROP['y']), (.5, 0.))
        # Hand calculation, 390 x 1600 field over 540 x 1200: crop 390*1200/540 = 866.7 -> 867 tall, top 0 (top-aligned);
        # 1170 x 2000 over 540 x 1200: crop 2000*540/1200 = 900 wide, left (1170-900)/2 = 135 (centred).
        self.assertEqual(rv.cover_box(390, 1600, 540, 1200, .5, 0.), (0, 0, 390, 867))
        self.assertEqual(rv.cover_box(1170, 2000, 540, 1200, .5, 0.), (135, 0, 1035, 2000))

    def test_source_names_the_home_surface(self):
        real = rv.git_show
        main_path = 'workspace/rose-agent/app/src/dev/pixel/voice/MainActivity.java'
        main = real('HEAD', main_path).decode()

        def with_main(text):
            with mock.patch.object(rv, 'git_show', side_effect=lambda rev, path:
                                   text.encode() if path == main_path else real(rev, path)):
                return rv.source('HEAD')['homeSurface']
        self.assertEqual(with_main(main.replace(OXBLOOD_MAIN_LINE, '')), 'not-oxblood')
        self.assertEqual(with_main(main + '\n// ' + OXBLOOD_MAIN_LINE + '\n'), 'oxblood')


class OxbloodApkTests(unittest.TestCase):
    """The APK's own classes say whether its Home can be the Oxblood field; an expectation's home must agree."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def inspect(self, classes, home=None):
        apk = self.tmp/'rose.apk'
        with zipfile.ZipFile(apk, 'w') as z:
            z.writestr('classes.dex', b'dex\n' + b''.join(f'Ldev/pixel/voice/{c};'.encode() for c in classes))
        outputs = {'badging': BADGING.replace('dev.example.voice', 'dev.pixel.voice'),
                   'xmltree': XMLTREE.replace('dev.example.voice', 'dev.pixel.voice'), 'resources': ''}
        fake = lambda argv, check_rc=True, binary=False: mock.Mock(stdout=outputs[argv[2]], returncode=0)
        expectation = None
        if home is not None:
            expectation = dict(ALPHA, home=home, apps=[dict(ALPHA['apps'][0], sha256=sha256(apk))])
        with mock.patch.object(rv, 'run', side_effect=fake), \
                mock.patch.object(rv, 'signer', return_value={'verified': True, 'certSha256': [ALPHA['appSignerSha256']]}):
            return rv.inspect_apk(Path(self.tmp), apk, expectation)

    def surface_check(self, r):
        return next((c['result'] for c in r['checks'] if c['check'].startswith('expected Home surface')), None)

    def test_dex_reports_the_surface(self):
        home = ['MainActivity', 'RoseHomeView', 'RoseConversationActivity']
        self.assertEqual(self.inspect(home)['homeSurfaceFromDex'], 'not-oxblood')
        self.assertEqual(self.inspect(home + ['OxbloodField'])['homeSurfaceFromDex'], 'oxblood')
        self.assertIsNone(self.surface_check(self.inspect(home + ['OxbloodField'])), 'no expectation, no check')

    def test_expectation_home_must_match_the_apk(self):
        home = ['MainActivity', 'RoseHomeView', 'RoseConversationActivity']
        self.assertEqual(self.surface_check(self.inspect(home + ['OxbloodField'], 'oxblood')), 'pass')
        self.assertEqual(self.surface_check(self.inspect(home, 'oxblood')), 'fail')
        self.assertEqual(self.surface_check(self.inspect(home + ['OxbloodField'], 'hifi-wallpaper')), 'fail')
        self.assertEqual(self.surface_check(self.inspect(home, 'hifi-wallpaper')), 'pass')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@unittest.skipUnless(HAVE_PIL, 'needs Pillow')
class OxbloodCompareTests(unittest.TestCase):
    """An Oxblood Home is compared with a raster of its field at OxbloodField's placement, never with the wallpaper."""

    def setUp(self):
        from PIL import Image, ImageDraw
        self.Image, self.ImageDraw = Image, ImageDraw
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        # A synthetic dark field (Oxblood-like luminance, never black) with 220 px bands. Taller than the screenshot,
        # so the vertical crop counts: the wallpaper's 30% puts the top at (1600-867)*.3 = 220, half a band period off.
        field = Image.new('RGB', (390, 1600), (23, 1, 4))
        draw = ImageDraw.Draw(field)
        for top in range(0, 1600, 440):
            draw.rectangle((0, top, 390, top + 219), fill=(110, 30, 44))
        self.field = self.save(field, 'field.png')
        self.home = rv.cover_crop(field, 540, 1200, rv.OXBLOOD_CROP)

    def save(self, image, name):
        path = self.tmp/name
        image.save(path)
        return path

    def oxblood_home(self):
        shot = self.home.copy()
        draw = self.ImageDraw.Draw(shot)
        draw.rectangle((0, 1080, 540, 1200), fill=(74, 20, 26))   # edge plate
        draw.rectangle((60, 720, 120, 780), fill=(40, 30, 32))    # a tile
        draw.text((33, 65), 'Mon 2', fill=(234, 219, 208))        # date
        return self.save(shot, 'home.png')

    def test_oxblood_home_matches_its_field_not_the_wallpaper(self):
        shot = self.oxblood_home()
        r = rv.compare(shot, self.tmp/'out', self.field, label='home', home='oxblood')
        self.assertEqual((r['home'], r['verdict']), ('oxblood', 'field-consistent'), r)
        self.assertEqual(r['referenceCrop']['box'], [0, 0, 390, 867])
        self.assertEqual(r['referenceBasis'], rv.OXBLOOD)
        self.assertIn('caller-supplied', r['referenceProvenance'])
        # The pre-#410 default reference calls the same correct Oxblood Home different: the stale expectation.
        legacy = rv.compare(shot, self.tmp/'out', label='legacy')
        self.assertEqual((legacy['home'], legacy['verdict']), ('hifi-wallpaper', 'differs-from-reference'))
        # Its own field at the wallpaper's 62% 30% crop misaligns the bands: placement matters.
        misplaced = rv.compare(shot, self.tmp/'out', self.field, label='misplaced')
        self.assertEqual(misplaced['verdict'], 'differs-from-reference')

    def test_without_a_field_raster_only_black_screen_is_detected(self):
        r = rv.compare(self.oxblood_home(), self.tmp/'out', label='home', home='oxblood')
        self.assertEqual((r['verdict'], r['reference']), ('not-compared', None))
        self.assertNotIn('wallpaperCellCoverage', r)
        self.assertGreater(r['meanLuminance'], 20)
        black = rv.compare(self.save(self.Image.new('RGB', (540, 1200), (3, 3, 3)), 'black.png'), self.tmp/'out',
                           home='oxblood')
        self.assertEqual(black['verdict'], 'black-screen')
        unrelated = rv.compare(self.save(self.Image.new('RGB', (540, 1200), (240, 240, 230)), 'white.png'),
                               self.tmp/'out', self.field, home='oxblood')
        self.assertEqual(unrelated['verdict'], 'differs-from-reference')
        with self.assertRaises(rv.Fail) as err:
            rv.compare(self.oxblood_home(), self.tmp/'out', home='fold')
        self.assertEqual(err.exception.code, 2)

    def test_cli_home_oxblood(self):
        shot = str(self.oxblood_home())
        code, out, _ = run_cli('compare', shot, '--out', str(self.tmp/'o'), '--home', 'oxblood',
                               '--reference', str(self.field))
        self.assertEqual((code, json.loads(out)['verdict']), (0, 'field-consistent'))
        code, out, _ = run_cli('compare', shot, '--out', str(self.tmp/'o'), '--home', 'oxblood')
        self.assertEqual((code, json.loads(out)['verdict']), (0, 'not-compared'))
        black = str(self.save(self.Image.new('RGB', (540, 1200), (0, 0, 0)), 'black.png'))
        self.assertEqual(run_cli('compare', black, '--out', str(self.tmp/'o'), '--home', 'oxblood')[0], 1)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as err:
            rv.main(['compare', shot, '--out', str(self.tmp/'o'), '--home', 'fold'])
        self.assertEqual(err.exception.code, 2)


class OxbloodExpectationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def write(self, obj):
        path = self.tmp/'e.json'
        path.write_text(json.dumps(obj))
        return path

    def test_home_is_optional_and_closed(self):
        base = json.loads(Path(ALPHA['path']).read_text())
        self.assertNotIn('home', base, 'recorded expectations predate #410')
        for home in rv.HOME_SURFACES:
            loaded = rv.load_expectation(self.write(dict(base, home=home)))
            self.assertEqual((loaded['home'], rv.expectation_summary(loaded)['home']), (home, home))
        for bad in ('fold', '', None, 1):
            with self.subTest(bad), self.assertRaisesRegex(rv.Fail, 'home must be'):
                rv.load_expectation(self.write(dict(base, home=bad)))

if __name__ == '__main__':
    unittest.main()
