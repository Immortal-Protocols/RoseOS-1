"""Source tests for the isolated build-tools r35 preparation (MODELS-SDK-PREP-001) and its models-sign preflight
integration. Synthetic archives and mocked transport only: no network, vendor download, secret, key, login or signing.
These prove preparation logic, not that Google's archive was fetched on a real host."""
import contextlib
import copy
import hashlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
import urllib.error
import zipfile

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prep = load('models_sdk_prepare_under_test', ROOT/'scripts/models-sdk-prepare.py')
hms = load('host_models_sign_under_test', ROOT/'scripts/host-models-sign.py')
ZIP_TIME = (2026, 1, 1, 0, 0, 0)
LIMITS = {'deadlineSeconds': 120, 'archiveBytes': 1 << 20, 'archiveMembers': 50, 'memberBytes': 1 << 16,
          'expandedBytes': 1 << 18}


def entry(name, data=b'', mode=0o644, kind=stat.S_IFREG):
    info = zipfile.ZipInfo(name, ZIP_TIME)
    info.external_attr = (kind | mode) << 16
    return info, data


def archive_bytes(entries=None, extra=()):
    """A synthetic build-tools archive shaped like Google's (android-15/ prefix, both required members)."""
    entries = entries if entries is not None else [
        entry('android-15/', kind=stat.S_IFDIR, mode=0o755),
        entry('android-15/aapt', b'#!/bin/false synthetic aapt\n', 0o755),
        entry('android-15/lib/apksigner.jar', b'synthetic apksigner jar'),
        entry('android-15/NOTICE.txt', b'notice'),
        entry('android-15/lib64/libc++.so', b'synthetic lib'),
        entry('android-15/apksigner', b'lib/apksigner.jar', 0o777, stat.S_IFLNK),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for info, data in list(entries) + list(extra):
            z.writestr(info, data)
    return buf.getvalue()


class Transport:
    def __init__(self, payload, before=None):
        self.payload, self.calls, self.before = payload, [], before

    def __call__(self, url, out, deadline, max_bytes):
        self.calls.append((url, deadline, max_bytes))
        if self.before:
            self.before()
        out.write(self.payload)
        return len(self.payload)


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='models-sdk-prep-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tools = self.tmp/'tools'
        self.tools.mkdir(0o755)
        self.shared = self.tools/'android-sdk'
        (self.shared/'build-tools/android-15').mkdir(parents=True)
        (self.shared/'build-tools/android-15/aapt').write_bytes(b'shared aapt, never touched')
        self.root = self.tools/'models-sdk-r35'
        self.payload = archive_bytes()
        self.pins = {'url': prep.URL, 'sha256': hashlib.sha256(self.payload).hexdigest()}
        self.cfg = self.config(self.pins['sha256'])

    def config(self, sha):
        cfg = copy.deepcopy(hms.config())
        cfg['sdk'].update(root=str(self.root), archive=str(self.root/'downloads/build-tools_r35_linux.zip'),
                          archiveSha256=sha)
        cfg['sdk']['prepare'].update(LIMITS)
        cfg['limits'].update(archiveBytes=1 << 20, archiveMembers=50, memberBytes=1 << 16)
        return cfg

    def prepare(self, transport=None, cfg=None, pins=None, **kw):
        return prep.prepare(cfg or self.cfg, transport=transport or Transport(self.payload), pins=pins or self.pins,
                            shared=str(self.shared), **kw)

    def assertRefused(self, category, fn, *a, **kw):
        with self.assertRaises(prep.Refused) as ctx:
            fn(*a, **kw)
        self.assertTrue(str(ctx.exception).startswith(category), str(ctx.exception))
        return ctx.exception

    def staging_left(self):
        return [p.name for p in self.tools.iterdir() if '.staging-' in p.name]


class Pins(Case):
    def test_committed_config_is_the_fixed_pin_and_isolated(self):
        cfg = hms.config()
        root, limits = prep.settings(cfg)
        self.assertEqual(str(root), '/home/pixelbuild/rose-actions/tools/models-sdk-r35')
        self.assertEqual(cfg['sdk']['prepare']['url'], 'https://dl.google.com/android/repository/build-tools_r35_linux.zip')
        self.assertEqual((limits['deadlineSeconds'], limits['archiveBytes']), (120, 256 << 20))
        pin = re.search(r'^fetch build-tools_r35_linux\.zip ([0-9a-f]{64}) ', (ROOT/'scripts/setup-android-sdk.sh').read_text(), re.M)
        self.assertEqual(cfg['sdk']['archiveSha256'], pin[1])
        self.assertEqual(prep.SHA256, pin[1])
        self.assertNotIn('android-sdk', cfg['sdk']['root'])

    def test_unpinned_or_overlapping_config_refuses(self):
        cases = {
            'other url': lambda c: c['sdk']['prepare'].update(url='https://mirror.example/build-tools.zip'),
            'other digest': lambda c: c['sdk'].update(archiveSha256='0' * 64),
            'long deadline': lambda c: c['sdk']['prepare'].update(deadlineSeconds=600),
            'big archive': lambda c: c['sdk']['prepare'].update(archiveBytes=1 << 30),
            'shared root': lambda c: c['sdk'].update(root=str(self.shared), archive=str(self.shared/'downloads/build-tools_r35_linux.zip')),
            'inside shared': lambda c: c['sdk'].update(root=str(self.shared/'x'), archive=str(self.shared/'x/downloads/build-tools_r35_linux.zip')),
            'archive elsewhere': lambda c: c['sdk'].update(archive=str(self.tmp/'elsewhere.zip')),
            'other prefix': lambda c: c['sdk'].update(archivePrefix='android-16'),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                cfg = copy.deepcopy(self.cfg)
                mutate(cfg)
                transport = Transport(self.payload)
                with self.assertRaises(prep.Refused):
                    self.prepare(transport, cfg)
                self.assertEqual(transport.calls, [])


class Prepare(Case):
    def test_fresh_prepare_then_reuse(self):
        transport = Transport(self.payload)
        proof = self.prepare(transport)
        self.assertEqual(proof['state'], 'prepared')
        self.assertEqual(transport.calls, [(prep.URL, 120, 1 << 20)])
        self.assertEqual(sorted(os.listdir(self.root)), ['build-tools', 'downloads'])
        installed = self.root/'build-tools/android-15'
        self.assertEqual(os.readlink(installed/'apksigner'), 'lib/apksigner.jar')
        self.assertTrue(os.stat(installed/'aapt').st_mode & stat.S_IXUSR)
        self.assertEqual(proof['apksignerSha256'], hashlib.sha256(b'synthetic apksigner jar').hexdigest())
        self.assertEqual(self.staging_left(), [])
        again = Transport(self.payload)
        reused = self.prepare(again)
        self.assertEqual((reused['state'], again.calls), ('reused', []))
        self.assertEqual({k: reused[k] for k in ('apksignerSha256', 'aaptSha256')},
                         {k: proof[k] for k in ('apksignerSha256', 'aaptSha256')})

    def test_existing_revalidation_agrees_with_the_prepared_cache(self):
        proof = self.prepare()
        tools = hms.archive_proof(self.cfg)  # the signed run's own, unchanged revalidation
        self.assertEqual(tools, {'apksignerSha256': proof['apksignerSha256'], 'aaptSha256': proof['aaptSha256'],
                                 'archiveSha256': self.pins['sha256']})

    def test_digest_checked_before_extraction(self):
        bad = archive_bytes(extra=[entry('android-15/extra', b'x')])
        with mock.patch.object(prep, 'inspect_archive', side_effect=AssertionError('extracted before digest')):
            self.assertRefused('sdk_archive_mismatch', self.prepare, Transport(bad))
        self.assertFalse(self.root.exists())
        self.assertEqual(self.staging_left(), [])

    def test_unsafe_archives_refuse(self):
        base = [entry('android-15/aapt', b'a', 0o755), entry('android-15/lib/apksigner.jar', b'j')]
        cases = {
            'traversal': base + [entry('android-15/../escape', b'x')],
            'absolute': base + [entry('/android-15/abs', b'x')],
            'other prefix': base + [entry('android-16/x', b'x')],
            'dot component': base + [entry('android-15/./x', b'x')],
            'backslash': base + [entry('android-15\\x', b'x')],
            'duplicate': base + [entry('android-15/aapt', b'b', 0o755)],
            'escaping link': base + [entry('android-15/out', b'../../etc/passwd', 0o777, stat.S_IFLNK)],
            'absolute link': base + [entry('android-15/out', b'/etc/passwd', 0o777, stat.S_IFLNK)],
            'under a link': base + [entry('android-15/d', b'lib', 0o777, stat.S_IFLNK), entry('android-15/d/x', b'x')],
            'fifo': base + [entry('android-15/pipe', b'', 0o644, stat.S_IFIFO)],
            'setuid': base + [entry('android-15/su', b'x', 0o4755)],
            'member too big': base + [entry('android-15/big', b'x' * ((1 << 16) + 1))],
            'expansion': base + [entry(f'android-15/f{i}', b'x' * (1 << 15)) for i in range(9)],
            'too many members': base + [entry(f'android-15/m{i}', b'') for i in range(60)],
            'aapt missing': [entry('android-15/lib/apksigner.jar', b'j')],
            'aapt is a link': [entry('android-15/lib/apksigner.jar', b'j'),
                               entry('android-15/aapt', b'lib/apksigner.jar', 0o777, stat.S_IFLNK)],
        }
        for name, entries in cases.items():
            with self.subTest(name):
                payload = archive_bytes(entries)
                pins = {'url': prep.URL, 'sha256': hashlib.sha256(payload).hexdigest()}
                self.assertRefused('sdk_archive_unsafe', self.prepare, Transport(payload), self.config(pins['sha256']), pins)
                self.assertFalse(self.root.exists())
                self.assertEqual(self.staging_left(), [])
        compressed = io.BytesIO()
        with zipfile.ZipFile(compressed, 'w') as z:
            for info, data in (entry('android-15/aapt', b'a', 0o755), entry('android-15/lib/apksigner.jar', b'j'),
                               entry('android-15/bomb', b'\0' * (1 << 20))):  # tiny compressed, large expanded
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, data)
        payload = compressed.getvalue()
        pins = {'url': prep.URL, 'sha256': hashlib.sha256(payload).hexdigest()}
        self.assertLess(len(payload), 1 << 16)
        self.assertRefused('sdk_archive_unsafe', self.prepare, Transport(payload), self.config(pins['sha256']), pins)

    def test_existing_wrong_cache_is_refused_and_left_untouched(self):
        self.prepare()
        cases = {
            'extra file': lambda: (self.root/'downloads/notes.txt').write_text('unknown'),
            'installed drift': lambda: (self.root/'build-tools/android-15/NOTICE.txt').write_text('changed'),
            'archive changed': lambda: (self.root/'downloads/build-tools_r35_linux.zip').write_bytes(b'not the archive'),
            'group writable': lambda: os.chmod(self.root/'build-tools', 0o775),
        }
        for name, damage in cases.items():
            with self.subTest(name):
                snapshot = self.tmp/'snapshot'
                shutil.copytree(self.root, snapshot, symlinks=True)
                damage()
                damaged = {str(p.relative_to(self.root)): (p.read_bytes() if p.is_file() and not p.is_symlink() else None)
                           for p in self.root.rglob('*')}
                transport = Transport(self.payload)
                self.assertRefused('sdk_cache_unexpected', self.prepare, transport)
                self.assertEqual(transport.calls, [])
                after = {str(p.relative_to(self.root)): (p.read_bytes() if p.is_file() and not p.is_symlink() else None)
                         for p in self.root.rglob('*')}
                self.assertEqual(after, damaged, 'refused cache must not be modified or deleted')
                shutil.rmtree(self.root)
                shutil.copytree(snapshot, self.root, symlinks=True)
                os.chmod(self.root/'build-tools', 0o755)
                shutil.rmtree(snapshot)

    def test_wrong_owner_symlinked_ancestor_and_unsafe_parent(self):
        self.prepare()
        self.assertRefused('sdk_cache_unexpected', self.prepare, uid=os.getuid() + 1)
        shutil.rmtree(self.root)
        real = self.tmp/'real-tools'
        real.mkdir(0o755)
        link = self.tmp/'link-tools'
        link.symlink_to(real)
        cfg = copy.deepcopy(self.cfg)
        cfg['sdk'].update(root=str(link/'models-sdk-r35'), archive=str(link/'models-sdk-r35/downloads/build-tools_r35_linux.zip'))
        transport = Transport(self.payload)
        self.assertRefused('sdk_parent_unsafe', self.prepare, transport, cfg)
        self.assertEqual((transport.calls, os.listdir(real)), ([], []))
        os.chmod(self.tools, 0o777)
        try:
            self.assertRefused('sdk_parent_unsafe', self.prepare, Transport(self.payload))
        finally:
            os.chmod(self.tools, 0o755)
        self.assertFalse(self.root.exists())

    def test_collision_during_staging_is_not_replaced(self):
        transport = Transport(self.payload, before=lambda: self.root.mkdir(0o755))
        self.assertRefused('sdk_cache_collision', self.prepare, transport)
        self.assertEqual(os.listdir(self.root), [], 'the appeared directory is left as it was')
        self.assertEqual(self.staging_left(), [])

    def test_partial_staging_is_removed_and_nothing_committed(self):
        def partial(plan, installed):
            os.mkdir(installed)
            (installed/'aapt').write_bytes(b'half')
            raise OSError('disk full')
        with mock.patch.object(prep, '_extract', partial):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertFalse(self.root.exists())
        self.assertEqual(self.staging_left(), [])

    def test_no_shared_sdk_write_no_subprocess_no_secret(self):
        before = {p: p.read_bytes() for p in self.shared.rglob('*') if p.is_file()}
        mtime = os.stat(self.shared).st_mtime_ns
        forbid = mock.Mock(side_effect=AssertionError('process started during preparation'))
        with mock.patch.object(subprocess, 'run', forbid), mock.patch.object(subprocess, 'Popen', forbid), \
                mock.patch.object(os, 'system', forbid), mock.patch.object(os, 'execv', forbid), \
                mock.patch.object(os, 'execve', forbid):
            self.prepare()
        forbid.assert_not_called()
        self.assertEqual({p: p.read_bytes() for p in self.shared.rglob('*') if p.is_file()}, before)
        self.assertEqual(os.stat(self.shared).st_mtime_ns, mtime)
        source = (ROOT/'scripts/models-sdk-prepare.py').read_text()
        for word in ('host_secrets', 'fetch_secret', 'gcloud', 'keytool', 'subprocess', 'actions-gcp-login'):
            self.assertNotIn(word, source, word)


class Transfer(unittest.TestCase):
    """The default transport against a mocked opener: fixed URL, no retry, deadline, byte limit, HTTPS-only redirects."""

    def opener(self, response=None, error=None):
        opened = []

        class Opener:
            def open(self, url, timeout):
                opened.append((url, timeout))
                if error:
                    raise error
                return response
        return Opener(), opened

    def test_denied_is_not_retried(self):
        opener, opened = self.opener(error=urllib.error.HTTPError(prep.URL, 403, 'Forbidden', {}, None))
        with mock.patch.object(prep.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(prep.Refused, 'sdk_download_denied: HTTP 403'):
                prep.https_download(prep.URL, io.BytesIO(), 120, 1 << 20)
        self.assertEqual(len(opened), 1)

    def test_timeout_and_size_limits(self):
        class Slow(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        ticks = iter([0, 50, 121, 200])
        opener, _ = self.opener(Slow(b'x' * (3 << 20)))
        with mock.patch.object(prep.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(prep.Refused, 'sdk_download_timeout'):
                prep.https_download(prep.URL, io.BytesIO(), 120, 1 << 30, clock=lambda: next(ticks))
        opener, _ = self.opener(Slow(b'x' * (3 << 20)))
        with mock.patch.object(prep.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(prep.Refused, 'sdk_download_too_large'):
                prep.https_download(prep.URL, io.BytesIO(), 120, 1 << 20)
        opener, _ = self.opener(error=TimeoutError())
        with mock.patch.object(prep.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(prep.Refused, 'sdk_download_timeout'):
                prep.https_download(prep.URL, io.BytesIO(), 120, 1 << 20)

    def test_redirect_must_stay_https(self):
        handler = prep._HttpsOnlyRedirect()
        with self.assertRaisesRegex(prep.Refused, 'redirect away from HTTPS'):
            handler.redirect_request(mock.Mock(), None, 302, 'Found', {}, 'http://dl.google.com/x.zip')


class Integration(Case):
    """host-models-sign.py: preparation only in the CLI preflight under the lock, revalidation only in run."""

    def stub_identity_checks(self):
        patches = [mock.patch.object(hms, 'check_receipt', return_value={'versions': {}, 'certificateSha256': '1' * 64,
                                                                        'alias': 'rose-models', 'runId': '1'}),
                   mock.patch.object(hms, 'check_input', return_value={'directory': self.tmp, 'files': {}}),
                   mock.patch.object(hms, 'check_journal', return_value='j' * 64),
                   mock.patch.object(hms, 'jdk_proof', return_value={'root': '/jdk', 'treeId': 't', 'revision': 'r'}),
                   mock.patch.object(hms, 'config', return_value=self.cfg)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_cli_preflight_prepares_under_the_lock_before_revalidation(self):
        self.stub_identity_checks()
        order = []

        @contextlib.contextmanager
        def lock():
            order.append('lock')
            yield
            order.append('unlock')
        transport = Transport(self.payload)
        with mock.patch.object(hms, 'prepare_sdk', lambda: order.append('prepare') or
                               prep.prepare(self.cfg, transport=transport, pins=self.pins, shared=str(self.shared))), \
                mock.patch.object(hms, 'shared_lock', lock):
            real_proof = hms.archive_proof
            with mock.patch.object(hms, 'archive_proof', lambda cfg=None: order.append('revalidate') or real_proof(self.cfg)):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = hms.main(['preflight', '--receipt-dir', str(self.tmp), '--input-dir', str(self.tmp),
                                     '--ingress-receipt', str(self.tmp/'r.json')])
        self.assertEqual(code, 0)
        self.assertEqual(order, ['lock', 'prepare', 'unlock', 'revalidate'])
        self.assertEqual(json.loads(out.getvalue())['sdkPreparation']['state'], 'prepared')
        self.assertEqual(len(transport.calls), 1)

    def test_failed_preparation_fails_preflight_before_revalidation(self):
        self.stub_identity_checks()
        revalidated = []
        with mock.patch.object(hms, 'prepare_sdk', side_effect=hms.Refused('sdk_download_denied: HTTP 403')), \
                mock.patch.object(hms, 'shared_lock', contextlib.nullcontext), \
                mock.patch.object(hms, 'archive_proof', lambda *a: revalidated.append(a)):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = hms.main(['preflight', '--receipt-dir', str(self.tmp)])
        self.assertEqual(code, 1)  # the workflow step fails, so the login step never runs
        self.assertIn('sdk_download_denied', err.getvalue())
        self.assertEqual(revalidated, [])

    def test_signed_run_never_prepares_or_touches_the_network(self):
        self.stub_identity_checks()
        no_network = mock.Mock(side_effect=AssertionError('network during the signed run'))
        fetched = []
        for state in ('absent', 'corrupt'):
            with self.subTest(state):
                if state == 'corrupt':
                    self.prepare()
                    (self.root/'downloads/build-tools_r35_linux.zip').write_bytes(b'corrupt')
                with mock.patch.object(hms, 'prepare_sdk', no_network), mock.patch.object(prep, 'https_download', no_network), \
                        mock.patch('socket.socket', no_network), mock.patch('urllib.request.urlopen', no_network):
                    expected = 'sdk_archive_missing' if state == 'absent' else 'sdk_archive_mismatch'
                    with self.assertRaisesRegex(hms.Refused, expected):
                        hms.run(self.tmp, self.tmp/'out', fetch=lambda *a: fetched.append(a), lock=contextlib.nullcontext)
                no_network.assert_not_called()
        self.assertEqual(fetched, [])
        self.assertNotIn('prepare', inspect.getsource(hms.run))

    def test_production_preflight_wiring(self):
        source = inspect.getsource(hms.main)
        self.assertIn('prepare=locked_prepare', source)
        self.assertIn('with (lock or shared_lock)():', inspect.getsource(hms.locked_prepare))
        self.assertNotIn('shared_lock', inspect.getsource(hms.preflight))  # run() already holds it: never nested


if __name__ == '__main__':
    unittest.main()
