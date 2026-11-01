#!/usr/bin/env python3
"""FOLD-OBSERVE-001 source tests: synthetic fixtures (target-files, otatools with a hand-built x86-64 ELF fastboot,
marker and receipt) under a temporary tree, a stubbed recipe check and the real shared-lock code. No real retained
input, generator, signer, device or host is read or executed. The bounded `fastboot --version` runner is exercised only
against real synthetic child processes (Python scripts standing in for fastboot), never a real or retained fastboot."""
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import re
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('rango_factory_observe', ROOT/'scripts/rango-factory-observe.py')
obs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(obs)

RECIPE = 'c' * 40
PRODUCT = '02b21bc69f9afd281422d42e3dcf8a456e471de1'
AVB = 'aee947942b80c74dab9956209b7f7836a1824d9aeb1e9dd782dc157c63fd8f27'
OTA = '2613738b' + '0' * 52 + '1bbf'


def h(data):
    return hashlib.sha256(data).hexdigest()


def elf(needed=()):
    """A minimal x86-64 ELF64 with one PT_LOAD covering the file and a PT_DYNAMIC listing DT_NEEDED names."""
    strtab = b'\0' + b''.join(n.encode() + b'\0' for n in needed)
    offsets, pos = [], 1
    for n in needed:
        offsets.append(pos)
        pos += len(n) + 1
    phoff, dyn_off = 64, 64 + 2 * 56
    dyn = b''.join(struct.pack('<qQ', 1, o) for o in offsets)
    str_off = dyn_off + 16 * (len(needed) + 3)
    dyn += struct.pack('<qQ', 5, str_off) + struct.pack('<qQ', 10, len(strtab)) + struct.pack('<qQ', 0, 0)
    total = str_off + len(strtab)
    header = b'\x7fELF' + bytes([2, 1, 1]) + bytes(9) + struct.pack('<HHIQQQIHHHHHH', 3, 62, 1, 0, phoff, 0, 0, 64, 56, 2,
                                                                     64, 0, 0)
    load = struct.pack('<IIQQQQQQ', 1, 5, 0, 0, 0, total, total, 0x1000)
    dynamic = struct.pack('<IIQQQQQQ', 2, 6, dyn_off, dyn_off, dyn_off, len(dyn), len(dyn), 8)
    return header + load + dynamic + dyn + strtab


def zip_bytes(members, links=()):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            if name in links:
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, data)
    return buf.getvalue()


def target_files_members():
    return {
        'META/misc_info.txt': b'ab_update=true\nuse_dynamic_partitions=true\ndynamic_partition_list=system vendor\n',
        'META/ab_partitions.txt': b'boot\nsystem\nvendor\nabl\n',
        'IMAGES/boot.img': b'ANDROID!' + bytes(64),
        'RADIO/bootloader.img': b'synthetic bootloader',
        'RADIO/radio.img': b'synthetic radio',
        'OTA/android-info.txt': b'require board=rango\n',
        'VENDOR/firmware/dauntless/evt.ec.bin': b'synthetic gsc',
        'SYSTEM/build.prop': b'not selected',
    }


def target_files(extra=None, links=()):
    return zip_bytes(dict(target_files_members(), **(extra or {})), links)


# Android target-files carry symlink entries (mode S_IFLNK, data = link text) outside the members any script reads, as
# run 41262748192 observed (link-entry for VENDOR/bin/gpioinfo, VENDOR/bin/tail). Link texts here are synthetic.
ANDROID_LINKS = {'VENDOR/bin/gpioinfo': b'toybox_vendor', 'VENDOR/bin/tail': b'toybox_vendor', 'SYSTEM/bin/sh': b'mksh'}


def otatools(members=None, links=()):
    base = {'bin/fastboot': elf(['libbase.so', 'libc.so.6']), 'lib64/libbase.so': elf(['liblog.so', 'libm.so.6']),
            'lib64/liblog.so': elf(), 'lib64/libunused.so': elf(), 'bin/simg2img': elf()}
    if members is not None:
        base = members
    return zip_bytes(base, links)


class Tree:
    """Temporary host tree mirroring the committed fixed layout; the bindings point into it."""

    def __init__(self, test, tf=None, tools=None, marker=None, receipt=None):
        self.tmp = Path(tempfile.mkdtemp())
        test.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        src = self.tmp/'src/releases/2026103001'
        (src/'release-rango-2026103001').mkdir(parents=True)
        transport = self.tmp/'transport/input'
        transport.mkdir(parents=True)
        identity = self.tmp/'rango-identity'
        identity.mkdir()
        (identity/'first-use.json').write_text('{"never": "read"}\n')
        self.tf = src/'release-rango-2026103001/rango-target_files.zip'
        self.tf.write_bytes(target_files() if tf is None else tf)
        self.tools = transport/'rango-otatools.zip'
        self.tools.write_bytes(otatools() if tools is None else tools)
        self.marker = src/'.rose-actions-signed.json'
        outputs = {n: '1' * 64 for n in obs.PUBLIC_OUTPUTS}
        outputs.update({'avb_pkmd.bin': AVB, 'rango-ota_update-2026103001.zip': OTA})
        m = {'candidate': PRODUCT, 'build': '2026103001', 'outputs': outputs, 'signedTargetFilesSha256': h(self.tf.read_bytes())}
        self.marker.write_text(json.dumps(marker(m) if marker else m))
        self.receipt = transport/'rango-retained-input.json'
        r = {'artifacts': {'otatools': {'name': 'rango-otatools.zip', 'size': self.tools.stat().st_size,
                                        'sha256': h(self.tools.read_bytes())}}}
        self.receipt.write_text(json.dumps(receipt(r) if receipt else r))
        self.lock = self.tmp/'build.lock'
        self.out = self.tmp/'out/observe-1'
        self.out.parent.mkdir()
        self.b = {'lock': str(self.lock), 'marker': str(self.marker), 'targetFiles': str(self.tf),
                  'inputReceipt': {'path': str(self.receipt), 'sha256': h(self.receipt.read_bytes())},
                  'otatools': {'path': str(self.tools), 'sha256': h(self.tools.read_bytes()), 'bytes': self.tools.stat().st_size},
                  'product': PRODUCT, 'avbPublicKeySha256': AVB, 'otaPrefix': '2613738b', 'otaSuffix': '1bbf',
                  'excluded': [str(identity)]}
        self.identity = identity

    def retained(self):
        return {p: h(p.read_bytes()) if p.exists() else None
                for p in (self.tf, self.tools, self.marker, self.receipt, self.identity/'first-use.json')}

    def run(self, **kw):
        kw.setdefault('b', self.b)
        kw.setdefault('head', lambda root: RECIPE)
        self.version_calls = []
        kw.setdefault('version', lambda path: (self.version_calls.append(Path(path)), FAKE_VERSION)[1])
        return obs.run('41200000001', RECIPE, kw.pop('out', self.out), **kw)


FAKE_VERSION = {'argv': ['fastboot', '--version'], 'executed': True, 'category': 'ok', 'exitCode': 0,
                'version': '35.0.2-12147458', 'outputBytes': 20, 'outputSha256': '5' * 64}


class Observe(unittest.TestCase):
    def assertRefused(self, pattern, t, **kw):
        before = t.retained()
        with self.assertRaisesRegex(obs.Refused, pattern):
            t.run(**kw)
        self.assertFalse(t.out.exists(), 'the new directory is removed on refusal')
        self.assertEqual(t.retained(), before, 'retained bytes are never changed')
        self.assertEqual(getattr(t, 'version_calls', []), [], 'nothing runs unless every check before it passed')

    def test_positive_observation_binds_every_input_and_runs_only_the_staged_version_check(self):
        t = Tree(self)
        before = t.retained()
        with mock.patch('subprocess.run') as run, mock.patch('subprocess.Popen') as popen, mock.patch('os.execv') as execv:
            receipt = t.run()
        run.assert_not_called(), popen.assert_not_called(), execv.assert_not_called()  # only through the version runner
        self.assertEqual(t.version_calls, [t.out/'fastboot/bin/fastboot'])
        self.assertEqual(receipt['fastboot']['version'], FAKE_VERSION)
        self.assertEqual(t.retained(), before)
        self.assertEqual(sorted(os.listdir(t.out)), ['fastboot', 'firmware-members.json', 'rango-factory-observe.json',
                                                     'target-files-observation.json'])
        self.assertEqual(sorted(os.listdir(t.out/'fastboot/lib64')), ['libbase.so', 'liblog.so'])  # never libunused/simg2img
        self.assertEqual(receipt['fastboot']['systemLibraries'], ['libc.so.6', 'libm.so.6'])
        self.assertEqual(receipt['fastboot']['fastboot']['sha256'], h((t.out/'fastboot/bin/fastboot').read_bytes()))
        self.assertEqual(receipt['inputs']['signedTargetFiles']['sha256'], h(t.tf.read_bytes()))
        self.assertEqual(receipt['inputs']['otatools']['sha256'], t.b['otatools']['sha256'])
        self.assertEqual((receipt['runId'], receipt['recipeSha']), ('41200000001', RECIPE))
        self.assertEqual(receipt['factoryPackageSha256'], h((ROOT/'scripts/factory-package.py').read_bytes()))
        fw = json.loads((t.out/'firmware-members.json').read_text())
        self.assertEqual(sorted(fw['members']), ['OTA/android-info.txt', 'RADIO/bootloader.img', 'RADIO/radio.img',
                                                 'VENDOR/firmware/dauntless/evt.ec.bin'])
        self.assertEqual(fw['members']['RADIO/radio.img'], {'sha256': h(b'synthetic radio'), 'size': 15})
        tfo = json.loads((t.out/'target-files-observation.json').read_text())
        self.assertEqual((tfo['schema'], tfo['device'], tfo['build'], tfo['synthetic']),
                         ('roseos.target-files-observation.v2', 'rango', '2026103001', False))
        self.assertEqual(receipt['outputs']['firmware-members.json'], h((t.out/'firmware-members.json').read_bytes()))

    def test_first_use_record_and_other_excluded_paths_are_never_opened(self):
        t = Tree(self)
        opened, real = [], os.open
        with mock.patch('os.open', lambda path, *a, **k: (opened.append(str(path)), real(path, *a, **k))[1]):
            t.run()
        self.assertFalse([p for p in opened if p.startswith(str(t.identity))])
        with self.assertRaisesRegex(obs.Refused, 'path_refused'):
            obs.open_regular(t.identity/'first-use.json', 'x', t.b['excluded'])
        t = Tree(self)
        self.assertRefused('output_refused', t, out=t.identity/'out')
        self.assertFalse((t.identity/'out').exists())

    def test_wrong_hashes_refuse(self):
        t = Tree(self, marker=lambda m: dict(m, signedTargetFilesSha256='0' * 64))
        self.assertRefused('hash_mismatch: the signed target-files', t)
        t = Tree(self)
        t.b['otatools']['sha256'] = '0' * 64
        self.assertRefused('binding_conflict', t)  # the receipt still names the real archive
        t = Tree(self, receipt=lambda r: {'artifacts': {'otatools': dict(r['artifacts']['otatools'], sha256='0' * 64)}})
        t.b['otatools']['sha256'] = '0' * 64
        self.assertRefused('hash_mismatch: otatools', t)
        t = Tree(self)
        t.b['inputReceipt']['sha256'] = '0' * 64
        self.assertRefused('hash_mismatch: input receipt', t)
        t = Tree(self, receipt=lambda r: {'artifacts': {'otatools': dict(r['artifacts']['otatools'], size=1)}})
        self.assertRefused('binding_conflict', t)

    def test_marker_must_be_the_committed_retained_build(self):
        cases = {
            'product': lambda m: dict(m, candidate='a' * 40), 'build': lambda m: dict(m, build='2026103002'),
            'avb': lambda m: dict(m, outputs=dict(m['outputs'], **{'avb_pkmd.bin': '2' * 64})),
            'ota': lambda m: dict(m, outputs=dict(m['outputs'], **{'rango-ota_update-2026103001.zip': '3' * 64})),
            'outputs': lambda m: dict(m, outputs={k: v for k, v in m['outputs'].items() if k != 'UPDATE.md'}),
            'no signed': lambda m: {k: v for k, v in m.items() if k != 'signedTargetFilesSha256'},
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                self.assertRefused('marker_mismatch', Tree(self, marker=mutate))

    def test_names_only_link_entries_outside_read_paths_are_admitted_never_followed(self):
        # Fails on factory-package.py before the names-only link hunk with exactly run 41262748192's refusal.
        t = Tree(self, tf=target_files(ANDROID_LINKS, links=set(ANDROID_LINKS)))
        before = t.retained()
        receipt = t.run()
        self.assertEqual(t.retained(), before)
        tfo = json.loads((t.out/'target-files-observation.json').read_text())
        self.assertFalse(set(ANDROID_LINKS) & set(tfo['members']))  # names outside the observed prefixes stay out
        fw = json.loads((t.out/'firmware-members.json').read_text())
        self.assertFalse(set(ANDROID_LINKS) & set(fw['members']))
        staged = {str(p.relative_to(t.out)) for p in t.out.rglob('*')}
        self.assertFalse([n for n in staged if 'gpioinfo' in n or 'tail' in n or n.endswith('/sh')])  # nothing extracted
        self.assertEqual(receipt['inputs']['signedTargetFiles']['sha256'], h(t.tf.read_bytes()))

    def test_unsafe_link_entries_still_refuse(self):
        def dup(name, data):
            members = dict(target_files_members(), **{name: data})
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as z:
                for n, d in list(members.items()) + [(name, data)]:
                    info = zipfile.ZipInfo(n, (2026, 1, 1, 0, 0, 0))
                    if n == name:
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    with __import__('warnings').catch_warnings():
                        __import__('warnings').simplefilter('ignore')
                        z.writestr(info, d)
            return buf.getvalue()
        cases = {
            # a link where any script reads a member: refused whatever its text
            'RADIO image': ({'RADIO/radio.img': b'../../etc/passwd'}, 'link-entry:target-files:RADIO/radio.img'),
            'IMAGES image': ({'IMAGES/boot.img': b'boot.img'}, 'link-entry:target-files:IMAGES/boot.img'),
            'META': ({'META/misc_info.txt': b'/etc/passwd'}, 'link-entry:target-files:META/misc_info.txt'),
            'OTA info': ({'OTA/android-info.txt': b'x'}, 'link-entry:target-files:OTA/android-info.txt'),
            'GSC firmware': ({'VENDOR/firmware/dauntless/evt.ec.bin': b'x'}, 'link-entry:target-files:VENDOR/firmware/'),
            'build.prop': ({'SYSTEM/build.prop': b'x'}, 'link-entry:target-files:SYSTEM/build.prop'),
            # malformed link metadata: empty, oversized, or a directory entry
            'empty text': ({'VENDOR/bin/empty': b''}, 'link-entry:target-files:VENDOR/bin/empty'),
            'oversized text': ({'VENDOR/bin/big': b'a' * 4096}, 'link-entry:target-files:VENDOR/bin/big'),
            'directory link': ({'VENDOR/lnk/': b'x'}, 'link-entry:target-files:VENDOR/lnk/'),
            # names stay checked before any link decision
            'traversal name': ({'VENDOR/../../escape': b'toybox'}, 'unsafe-path:target-files:VENDOR/../../escape'),
            'absolute name': ({'/VENDOR/bin/abs': b'toybox'}, 'unsafe-path:target-files:/VENDOR/bin/abs'),
        }
        for name, (extra, pattern) in cases.items():
            with self.subTest(name):
                self.assertRefused('observe_refused: target-files refused: .*' + re.escape(pattern),
                                   Tree(self, tf=target_files(extra, links=set(extra))))
        with self.subTest('duplicate link'):
            self.assertRefused(re.escape('duplicate-entry:target-files:VENDOR/bin/tail'),
                               Tree(self, tf=dup('VENDOR/bin/tail', b'toybox_vendor')))
        with self.subTest('otatools stays link-free'):
            tools = otatools({'bin/fastboot': elf(), 'lib64/x.so': b'y'}, links={'bin/fastboot'})
            self.assertRefused('archive_unsafe: otatools bin/fastboot is not a regular member', Tree(self, tools=tools))

    def test_open_checked_admits_names_only_links_for_target_files_only(self):
        fp, _ = obs.factory_package()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
        path = tmp/'tf.zip'
        path.write_bytes(target_files(ANDROID_LINKS, links=set(ANDROID_LINKS)))
        strict = {'problems': [], 'missing': []}
        self.assertIsNone(fp.open_checked(path, 'artifact', strict))  # the default (install archive) stays link-free
        self.assertEqual(sorted(strict['problems']), sorted(f'link-entry:artifact:{n}' for n in ANDROID_LINKS))
        names_only = {'problems': [], 'missing': []}
        with fp.open_checked(path, 'target-files', names_only, names_only_links=True) as archive:
            self.assertIn('VENDOR/bin/gpioinfo', archive.namelist())
        self.assertEqual((names_only['problems'], names_only['linkEntries']), ([], len(ANDROID_LINKS)))

    def test_path_traversal_and_unsafe_members_refuse(self):
        tools = otatools(dict(json.loads('{}'), **{'bin/fastboot': elf(), '../escape': b'x'}))
        self.assertRefused('archive_unsafe: otatools member path', Tree(self, tools=tools))
        tools = otatools({'bin/fastboot': elf(['../../etc/libevil.so'])})
        self.assertRefused('unsafe library', Tree(self, tools=tools))
        tf = target_files({'RADIO/../../x': b'x'})
        self.assertRefused('archive_unsafe|observe_refused', Tree(self, tf=tf))
        t = Tree(self)
        self.assertRefused('output_refused', t, out=Path(str(t.out.parent) + '/../escape'))
        with self.assertRaisesRegex(obs.Refused, 'path_refused'):
            obs.open_regular('/tmp/../etc/passwd', 'x', [])

    def test_symlinks_refuse(self):
        t = Tree(self)
        real = t.tf.with_name('real.zip')
        t.tf.rename(real)
        t.tf.symlink_to(real)
        self.assertRefused('path_refused: signed target-files is a symlink', t)
        t = Tree(self)
        moved = t.tmp/'elsewhere'
        t.tools.parent.rename(moved)
        t.tools.parent.symlink_to(moved)
        self.assertRefused('path_refused: input receipt has a symlink', t)
        tools = otatools({'bin/fastboot': elf(['libbase.so']), 'lib64/libbase.so': b'/etc/passwd'}, links={'lib64/libbase.so'})
        self.assertRefused('archive_unsafe: otatools lib64/libbase.so is not a regular member', Tree(self, tools=tools))
        t = Tree(self)
        t.out.parent.rmdir()
        t.out.parent.symlink_to(t.tmp)
        self.assertRefused('output_refused', t)

    def test_occupied_lock_refuses_before_reading(self):
        t = Tree(self)
        holder = os.open(t.lock, os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        opened, real = [], os.open
        with mock.patch('os.open', lambda path, *a, **k: (opened.append(str(path)), real(path, *a, **k))[1]):
            self.assertRefused('lock_held', t)
        self.assertEqual([p for p in opened if p != str(t.lock)], [])

    def test_time_and_output_bounds(self):
        t = Tree(self)
        ticks = iter([0] + [10] * 3 + [10**6] * 1000)
        self.assertRefused('time_bound', t, clock=lambda: next(ticks))
        for key, value, pattern in (('fastbootBytes', 100, 'size_bound: fastboot'),
                                    ('fastbootLibs', 1, 'size_bound: more than 1 fastboot'),
                                    ('firmwareMembers', 2, 'size_bound: more than 2 firmware'),
                                    ('firmwareMemberBytes', 10, 'size_bound: firmware member'),
                                    ('markerBytes', 10, 'size_bound: signed marker')):
            with self.subTest(key), mock.patch.dict(obs.LIMITS, {key: value}):
                self.assertRefused(pattern, Tree(self))

    def test_missing_inputs_and_bindings_refuse(self):
        for attr, pattern in (('marker', 'input_missing: signed marker'), ('tf', 'input_missing: signed target-files'),
                              ('receipt', 'input_missing: input receipt'), ('tools', 'input_missing: otatools')):
            with self.subTest(attr):
                t = Tree(self)
                getattr(t, attr).unlink()
                self.assertRefused(pattern, t)
        self.assertRefused('archive_unsafe: otatools bin/fastboot is missing', Tree(self, tools=otatools({'bin/adb': elf()})))
        tools = otatools({'bin/fastboot': elf(['libmissing.so'])})
        self.assertRefused('fastboot_dependency_missing: libmissing.so', Tree(self, tools=tools))
        self.assertRefused('fastboot_invalid', Tree(self, tools=otatools({'bin/fastboot': b'#!/bin/sh\n'})))
        tf = zip_bytes({'META/misc_info.txt': b'ab_update=true\n', 'META/ab_partitions.txt': b'boot\n'})
        self.assertRefused('firmware_missing', Tree(self, tf=tf))
        t = Tree(self, receipt=lambda r: {})
        self.assertRefused('binding_missing: the input receipt', t)

    def test_cleanup_removes_only_the_new_directory(self):
        t = Tree(self)
        sibling = t.out.parent/'keep.txt'
        sibling.write_text('keep')
        with mock.patch.object(obs, 'firmware_members', side_effect=obs.Refused('firmware_missing: injected')):
            self.assertRefused('injected', t)
        self.assertEqual(sibling.read_text(), 'keep')
        t.out.mkdir()
        (t.out/'existing').write_text('mine')
        with self.assertRaisesRegex(obs.Refused, 'output_exists'):
            t.run()
        self.assertEqual((t.out/'existing').read_text(), 'mine')

    def test_recipe_and_run_id_are_exact(self):
        t = Tree(self)
        self.assertRefused('recipe_mismatch', t, head=lambda root: 'd' * 40)
        with self.assertRaisesRegex(obs.Refused, 'recipe_mismatch'):
            obs.run('1', 'short', t.out, b=t.b, head=lambda root: 'short')
        with self.assertRaisesRegex(obs.Refused, 'usage_refused'):
            obs.run('../1', RECIPE, t.out, b=t.b, head=lambda root: RECIPE)
        self.assertFalse(t.out.exists())


def alive(pid):
    """True while pid is a live (non-zombie) process."""
    try:
        state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != 'Z'


class VersionCheck(unittest.TestCase):
    """Real child processes: a Python script installed as the staged fastboot, in a staged fastboot/bin layout."""

    def child(self, body):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(root, ignore_errors=True))
        (root/'fastboot/bin').mkdir(parents=True)
        (root/'fastboot/lib64').mkdir()
        self.record = root/'record.json'
        path = root/'fastboot/bin/fastboot'
        path.write_text(f'#!{sys.executable}\nimport json, os, sys, time, subprocess\nRECORD = {str(self.record)!r}\n'
                        + body)
        path.chmod(0o700)
        return path

    def check(self, path, **limits):
        start = time.monotonic()
        result = obs.fastboot_version(path, limits)
        self.elapsed = time.monotonic() - start
        self.assertEqual(set(result), {'argv', 'executed', 'category', 'exitCode', 'version', 'outputBytes', 'outputSha256'})
        self.assertNotIn(str(path.parent), json.dumps(result))  # never the install path
        return result

    def test_success_reports_only_bounded_fields(self):
        path = self.child(
            "json.dump({'argv': sys.argv, 'env': dict(os.environ), 'pgid': os.getpgid(0), 'parent_pgid': os.getpgid(os.getppid()),"
            " 'cwd': os.getcwd()}, open(RECORD, 'w'))\n"
            "print('fastboot version 35.0.2-12147458')\nprint('Installed as ' + sys.argv[0])\n"
            "print('SECRET-TOKEN-should-never-be-reported')\n")
        secrets = {'GITHUB_TOKEN': 'ghs_x', 'ACTIONS_ID_TOKEN_REQUEST_TOKEN': 'y', 'ACTIONS_ID_TOKEN_REQUEST_URL': 'https://z',
                   'GOOGLE_APPLICATION_CREDENTIALS': '/creds.json', 'CLOUDSDK_AUTH_ACCESS_TOKEN': 'w', 'SSH_AUTH_SOCK': '/s'}
        with mock.patch.dict(os.environ, secrets):
            result = self.check(path)
        self.assertEqual((result['category'], result['exitCode'], result['version'], result['executed']),
                         ('ok', 0, '35.0.2-12147458', True))
        self.assertNotIn('SECRET-TOKEN', json.dumps(result))
        record = json.loads(self.record.read_text())
        self.assertEqual(record['argv'], [str(path), '--version'])  # exactly the staged copy and --version
        self.assertEqual(sorted(record['env']), ['HOME', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH', 'PATH'])
        self.assertEqual(record['env']['LD_LIBRARY_PATH'], str(path.parent.parent/'lib64'))
        self.assertNotEqual(record['pgid'], record['parent_pgid'])  # its own process group
        self.assertEqual(record['cwd'], str(path.parent.parent))
        self.assertEqual(result['outputSha256'], hashlib.sha256(
            f'fastboot version 35.0.2-12147458\nInstalled as {path}\nSECRET-TOKEN-should-never-be-reported\n'.encode()).hexdigest())

    def test_nonzero_exit_and_unparsed_output(self):
        result = self.check(self.child("print('fastboot version 1.0'); sys.exit(3)\n"))
        self.assertEqual((result['category'], result['exitCode']), ('nonzero_exit', 3))
        result = self.check(self.child("print('no version here')\n"))
        self.assertEqual((result['category'], result['exitCode'], result['version']), ('unparsed', 0, None))
        result = self.check(self.child("print('fastboot version ../../etc/$(id)')\n"))
        self.assertEqual((result['category'], result['version']), ('unparsed', None))  # never an unsafe token

    def test_hang_times_out_and_the_group_is_killed(self):
        path = self.child("json.dump({'pid': os.getpid()}, open(RECORD, 'w'))\n"
                          "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n")
        result = self.check(path, versionSeconds=1)
        self.assertEqual((result['category'], result['exitCode'], result['version']), ('timeout', None, None))
        self.assertLess(self.elapsed, 6)  # 1 s timeout + 1 s grace, then SIGKILL even though SIGTERM is ignored
        self.assertFalse(alive(json.loads(self.record.read_text())['pid']))

    def test_flood_overflows_the_cap_and_is_killed(self):
        path = self.child("json.dump({'pid': os.getpid()}, open(RECORD, 'w'))\n"
                          "while True:\n    sys.stdout.write('x' * 65536); sys.stdout.flush()\n")
        result = self.check(path)
        self.assertEqual((result['category'], result['exitCode']), ('output_overflow', None))
        self.assertLessEqual(result['outputBytes'], obs.LIMITS['versionOutputBytes'] + 1)
        self.assertLess(self.elapsed, 5)
        self.assertFalse(alive(json.loads(self.record.read_text())['pid']))
        result = self.check(self.child("sys.stderr.write('e' * 20000)\n"))  # stderr counts toward the same cap
        self.assertEqual(result['category'], 'output_overflow')

    def test_descendants_are_ended_with_the_group(self):
        # The child exits at once; one grandchild keeps the pipe open, another detaches it and ignores SIGTERM.
        path = self.child(
            "a = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "b = subprocess.Popen([sys.executable, '-c', 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN);"
            " time.sleep(60)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "json.dump({'pids': [a.pid, b.pid]}, open(RECORD, 'w'))\nprint('fastboot version 9.9', flush=True)\n")
        result = self.check(path)
        self.assertEqual((result['category'], result['exitCode'], result['version']), ('ok', 0, '9.9'))
        self.assertLess(self.elapsed, 6)
        deadline = time.monotonic() + 5
        pids = json.loads(self.record.read_text())['pids']
        while any(alive(p) for p in pids) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual([p for p in pids if alive(p)], [])

    def test_output_written_just_before_exit_is_still_read(self):
        # select never reports data, so the loop ends on the child's exit and the bytes arrive only through the drain.
        path = self.child("print('fastboot version 7.7', flush=True)\n")
        with mock.patch.object(obs.select, 'select', lambda r, w, x, t: (time.sleep(min(t, 0.01)), ([], [], []))[1]):
            result = self.check(path)
        self.assertEqual((result['category'], result['version']), ('ok', '7.7'))

    def test_spawn_refusal_and_failure(self):
        path = self.child("print('x')\n")
        link = path.with_name('linked')
        link.symlink_to(path)
        self.assertEqual(obs.fastboot_version(link)['category'], 'spawn_refused')
        self.assertFalse(obs.fastboot_version(link)['executed'])
        path.chmod(0o600)
        result = obs.fastboot_version(path)
        self.assertEqual((result['category'], result['executed']), ('spawn_failed', False))


class Bindings(unittest.TestCase):
    def test_committed_bindings_are_the_retained_build(self):
        b = obs.bindings()
        self.assertEqual(b['otatools']['sha256'], '22d62464ff3fa74fe3fa2b4c118bba116bc3a3aa504feddcdcf38fc3f3cbb664')
        self.assertEqual(b['otatools']['bytes'], 536798181)
        self.assertEqual(b['inputReceipt']['sha256'], '54d4cf665e9bc47d584abda8ab6c130687cc748b7752d156e81e94462bd2173e')
        self.assertEqual(b['targetFiles'],
                         '/home/pixelbuild/grapheneos-2026100600/releases/2026103001/release-rango-2026103001/rango-target_files.zip')
        self.assertEqual(b['marker'], '/home/pixelbuild/grapheneos-2026100600/releases/2026103001/.rose-actions-signed.json')
        self.assertEqual((b['avbPublicKeySha256'], b['otaPrefix'], b['otaSuffix']), (AVB, '2613738b', '1bbf'))
        self.assertEqual(b['excluded'], ['/home/pixelbuild/rose-actions/rango-identity'])
        self.assertEqual(b['lock'], '/home/pixelbuild/frankel-build.lock')

    def test_null_or_missing_bindings_refuse(self):
        release = json.loads((ROOT/'os/retained-rango/release.json').read_text())
        flavor = json.loads((ROOT/'os/flavors/rango.json').read_text())
        for name, mutate, pattern in (
                ('otatools sha', lambda r, f: r['signing']['otatoolsProvenance'].update(sha256=None), 'binding_missing'),
                ('lock', lambda r, f: r['signing'].pop('lockPath'), 'binding_missing: signing.lockPath'),
                ('avb', lambda r, f: f['identity'].update(avbPublicKeySha256=None), 'binding_missing'),
                ('retained', lambda r, f: f.update(retained=[]), 'binding_missing: flavor.retained'),
                ('relative', lambda r, f: r['signing'].update(sourceRoot='src'), 'binding_invalid'),
                ('build', lambda r, f: r['target'].update(build='2026103002'), 'binding_invalid'),
                ('abbrev', lambda r, f: f['retained'][0].update(otaSha256Abbrev='2613738b'), 'binding_invalid')):
            with self.subTest(name):
                tmp = Path(tempfile.mkdtemp())
                self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
                r, f = json.loads(json.dumps(release)), json.loads(json.dumps(flavor))
                mutate(r, f)
                (tmp/'os/retained-rango').mkdir(parents=True)
                (tmp/'os/flavors').mkdir()
                (tmp/'os/retained-rango/release.json').write_text(json.dumps(r))
                (tmp/'os/flavors/rango.json').write_text(json.dumps(f))
                with self.assertRaisesRegex(obs.Refused, pattern):
                    obs.bindings(tmp)

    def test_cli_check_and_refusal_exit_codes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(obs.main(['check']), 0)
        self.assertTrue(json.loads(out.getvalue())['checked'])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(obs.main(['run', '--run-id', '1', '--recipe-sha', 'nope', '--out', '/nonexistent/x']), 3)
        self.assertIn('recipe_mismatch', json.loads(out.getvalue())['refused'])

    def test_elf_parser_reads_only_needed_names(self):
        self.assertEqual(obs.elf_needed(elf(['a.so', 'libc.so.6']), 'x'), ['a.so', 'libc.so.6'])
        self.assertEqual(obs.elf_needed(elf(), 'x'), [])
        bad = bytearray(elf(['a.so']))
        bad[18] = 183  # aarch64
        with self.assertRaisesRegex(obs.Refused, 'not x86-64'):
            obs.elf_needed(bytes(bad), 'x')


if __name__ == '__main__':
    unittest.main()
