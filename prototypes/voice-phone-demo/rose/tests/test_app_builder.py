"""Project 1 build-job core (docs/APP-BUILDER.md §2-3): identity stays with the builder, jobs are durable and honest.

The real build runs only where the pinned Android SDK and a JDK exist (ANDROID_SDK_ROOT); it proves compilation and
signature verification in this environment, not installation on any phone.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import ledger as L, template, toolchain
from rose.app_builder.service import Builder, Refused

SDK = Path(os.environ.get('ANDROID_SDK_ROOT', '/nonexistent'))
JAVA_HOME = Path(os.environ['JAVA_HOME']) if os.environ.get('JAVA_HOME') else None
HAS_SDK = (SDK / 'build-tools/android-15/aapt').exists() and bool(shutil.which('keytool') or JAVA_HOME)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        self.t += 1
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'builder'

    def tearDown(self):
        self.tmp.cleanup()


class LedgerTest(Base):
    def setUp(self):
        super().setUp()
        self.l = L.Ledger(self.root, clock=Clock())

    def tearDown(self):
        self.l.close()
        super().tearDown()

    def test_private_files_and_idempotent_create(self):
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / 'builder.sqlite3').stat().st_mode & 0o777, 0o600)
        a = self.l.create('dev1', 'req-1', 'a timer', 'Timer')
        self.assertEqual(self.l.create('dev1', 'req-1', 'a timer', 'Timer'), a)
        self.assertNotEqual(self.l.create('dev2', 'req-1', 'a timer', 'Timer'), a)  # another device's request
        p = self.l.project(a['projectId'])
        self.assertEqual(p['packageName'], L.PACKAGE_PREFIX + a['projectId'])
        self.assertIsNone(p['signerSha256'])

    def test_versions_grow_and_signer_is_fixed(self):
        a = self.l.create('d', 'r', 'x', 'X')
        with self.assertRaises(L.LedgerError):
            self.l.revise('d', a['projectId'], 'r2', 'more')  # nothing delivered yet
        for state in ('preparing', 'building'):
            self.assertTrue(self.l.advance(a['jobId'], state, state, state))
        self.assertEqual(self.l.allocate_version(a['jobId']), 1)
        self.assertEqual(self.l.allocate_version(a['jobId']), 1)  # fixed for the attempt
        self.l.bind_signer(a['projectId'], 'a' * 64)
        with self.assertRaises(L.LedgerError):
            self.l.bind_signer(a['projectId'], 'b' * 64)
        self.assertTrue(self.l.deliver(a['jobId'], '1', 'rev1', 'c' * 64, 10, 'x'))
        self.assertEqual(self.l.job(a['jobId'])['state'], 'ready')
        r = self.l.revise('d', a['projectId'], 'r2', 'more')
        self.assertEqual(self.l.revise('d', a['projectId'], 'r2', 'more'), r)
        with self.assertRaises(L.LedgerError):
            self.l.revise('d', a['projectId'], 'r3', 'again')  # one build per project at a time
        self.l.advance(r['jobId'], 'building', 'b', 'b')
        self.assertEqual(self.l.allocate_version(r['jobId']), 2)
        self.l.deliver(r['jobId'], '2', 'rev2', 'd' * 64, 11, 'more')
        lib = self.l.projects()
        self.assertEqual([v['versionCode'] for v in lib[0]['versions']], [1, 2])
        self.assertEqual(lib[0]['signerSha256'], 'a' * 64)
        with self.assertRaises(L.LedgerError):
            self.l.revise('d', 'other', 'r2', 'x')  # a clientRequestId belongs to one project

    def test_cancel_and_restart_are_truthful(self):
        a = self.l.create('d', 'r', 'x', 'X')
        self.assertEqual(self.l.cancel('nope'), 'unknown_job')
        self.assertEqual(self.l.cancel(a['jobId']), 'accepted')
        self.assertEqual(self.l.job(a['jobId'])['state'], 'cancelled')  # queued: stopped at once
        self.assertEqual(self.l.cancel(a['jobId']), 'not_running')
        b = self.l.create('d', 'r2', 'y', 'Y')
        self.l.advance(b['jobId'], 'building', 'b', 'b')
        self.assertEqual(self.l.cancel(b['jobId']), 'accepted')
        self.assertEqual(self.l.job(b['jobId'])['state'], 'building')  # accepted means stopping, not stopped
        self.assertFalse(self.l.advance(b['jobId'], 'signing', 's', 's'))
        c = self.l.create('d', 'r3', 'z', 'Z')
        self.l.advance(c['jobId'], 'signing', 's', 's')
        q = self.l.create('d', 'r4', 'q', 'Q')
        self.l.close()
        self.l = L.Ledger(self.root, clock=Clock())
        self.assertEqual(self.l.recover(), 2)
        self.assertEqual(self.l.job(b['jobId'])['state'], 'cancelled')
        failure = self.l.job(c['jobId'])['failure']
        self.assertEqual((failure['category'], failure['retryable']), ('internal', True))
        self.assertIn('outcome unknown', failure['summary'])
        self.assertEqual(self.l.job(q['jobId'])['state'], 'queued')  # never started: still queued
        retried = self.l.retry(c['jobId'])
        self.assertEqual((retried['state'], retried['attempt']), ('queued', 2))
        with self.assertRaises(L.LedgerError):
            self.l.retry(b['jobId'])  # cancelled is not retryable


class TemplateTest(Base):
    PKG = 'app.rose.gen.pabc'

    def source(self):
        s = self.root / 'src'
        template.write_skeleton(s, self.PKG, 'Demo & <test>')
        return s

    def test_skeleton_passes(self):
        template.check(self.source(), self.PKG)

    def test_labels_are_escaped_for_aapt(self):
        self.assertEqual(template.android_string("Don't @x"), "Don\\'t @x")
        self.assertEqual(template.android_string('@home'), '\\@home')
        self.assertEqual(template.android_string('?q "a" & <b>'), '\\?q \\"a\\" &amp; &lt;b&gt;')

    def test_refusals(self):
        cases = {
            'package': lambda s: (s / 'AndroidManifest.xml').write_text(
                (s / 'AndroidManifest.xml').read_text().replace(self.PKG, 'dev.pixel.voice')),
            'version': lambda s: (s / 'AndroidManifest.xml').write_text(
                (s / 'AndroidManifest.xml').read_text().replace('package=', 'android:versionCode="9" package=')),
            'shared uid': lambda s: (s / 'AndroidManifest.xml').write_text(
                (s / 'AndroidManifest.xml').read_text().replace('package=', 'android:sharedUserId="x.y" package=')),
            'uses-sdk': lambda s: (s / 'AndroidManifest.xml').write_text(
                (s / 'AndroidManifest.xml').read_text().replace('<application', '<uses-sdk/><application')),
            'debuggable': lambda s: (s / 'AndroidManifest.xml').write_text(
                (s / 'AndroidManifest.xml').read_text().replace('allowBackup', 'debuggable="true" android:allowBackup')),
            'gradle': lambda s: (s / 'build.gradle').write_text('x'),
            'native': lambda s: (s / 'res/raw').mkdir() or (s / 'res/raw/lib.so').write_text('x'),
            'script': lambda s: (s / 'src/run.sh').write_text('x'),
            'symlink': lambda s: (s / 'res/link').symlink_to('/etc/passwd'),
            'other package': lambda s: (s / 'src/Other.java').write_text('package dev.pixel.voice;\nclass Other {}'),
            'bad xml': lambda s: (s / 'AndroidManifest.xml').write_text('<manifest'),
            'prv namespace': lambda s: (s / 'AndroidManifest.xml').write_text(
                (s / 'AndroidManifest.xml').read_text().replace(
                    'package=', 'xmlns:p="http://schemas.android.com/apk/prv/res/android" p:sharedUserId="x.y" package=')),
            'git dir': lambda s: (s / '.git').mkdir(),
            'nested git': lambda s: (s / 'res/.git').mkdir(),
            'gitignore': lambda s: (s / 'res/.gitignore').write_text('*'),
            'gitattributes': lambda s: (s / 'src/.gitattributes').write_text('* filter=x'),
            'fifo': lambda s: os.mkfifo(s / 'src/Stuck.java'),
            # Raw text shows the app's package; javac decodes \\u002a to '*' and reads dev.pixel.voice instead.
            'unicode escape hides package': lambda s: next(s.glob('src/**/MainActivity.java')).write_text(
                '/*\\u002a/ package dev.pixel.voice; /* */ package ' + self.PKG + '; class A {}'),
            'hard link': lambda s: os.link(s / 'res/values/strings.xml', s / 'res/values/copy.xml'),
            'comment hides package': lambda s: next(s.glob('src/**/MainActivity.java')).write_text(
                '/* package ' + self.PKG + '; */ package dev.pixel.voice; class A {}'),
        }
        for name, change in cases.items():
            s = self.source()
            change(s)
            with self.assertRaises(template.TemplateRefused, msg=name):
                template.check(s, self.PKG)
            shutil.rmtree(s)


class ApiTest(Base):
    def setUp(self):
        super().setUp()
        self.b = Builder(self.root, toolchain.Tools(SDK), generate=lambda *a: None)
        self.addCleanup(self.b.ledger.close)

    def test_strict_v1_bodies(self):
        for body in (None, {}, {'schemaVersion': 2, 'clientRequestId': 'r', 'description': 'x'},
                     {'schemaVersion': 1, 'clientRequestId': 'r'}, {'schemaVersion': 1, 'clientRequestId': 'r',
                                                                   'description': ' ', 'extra': 1},
                     {'schemaVersion': 1, 'clientRequestId': 'x' * 65, 'description': 'd'}):
            with self.assertRaises(Refused, msg=body):
                self.b.create('d', body)
        out = self.b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'A tip calculator'})
        job = self.b.job(out['jobId'])
        self.assertEqual((job['state'], job['attempt']), ('queued', 1))
        self.assertEqual(self.b.cancel(out['jobId'], {'schemaVersion': 1}), {'status': 'accepted'})
        with self.assertRaises(Refused):
            self.b.revise('d', out['projectId'], {'schemaVersion': 1, 'clientRequestId': 'r2', 'request': 'x'})
        self.assertIsNone(self.b.artifact('../../etc/passwd'))
        self.assertIsNone(self.b.job('nope'))
        with self.assertRaises(Refused):
            self.b.install('d', {'schemaVersion': 1, 'projectId': out['projectId'], 'packageName': 'dev.pixel.voice',
                                 'versionCode': 1, 'signerSha256': 'a' * 64, 'status': 'installed', 'detail': 'ok'})
        pkg = self.b.projects()[0]['packageName']
        receipt = {'schemaVersion': 1, 'projectId': out['projectId'], 'packageName': pkg, 'versionCode': 1,
                   'signerSha256': None, 'status': 'failed', 'detail': ''}
        self.assertEqual(self.b.install('d', receipt), {'recorded': True})  # nothing installed: no signer
        for bad in ({'signerSha256': 'A' * 64}, {'signerSha256': 'x'}, {'status': 'done'}, {'detail': 'x' * 501}):
            with self.assertRaises(Refused, msg=bad):
                self.b.install('d', receipt | bad)
        with self.assertRaises(Refused):
            self.b.install('d', {k: v for k, v in receipt.items() if k != 'signerSha256'})  # must be stated

    def test_library_shows_the_active_job(self):
        out = self.b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'Tip calculator'})
        self.assertEqual(self.b.projects()[0]['activeJob'], {'jobId': out['jobId'], 'state': 'queued'})
        self.b.cancel(out['jobId'], {'schemaVersion': 1})
        self.assertIsNone(self.b.projects()[0]['activeJob'])


@unittest.skipUnless(HAS_SDK or os.environ.get('ROSE_REQUIRE_APP_BUILD'), 'needs the pinned Android SDK (ANDROID_SDK_ROOT) and a JDK')
class BuildTest(Base):
    """Compilation and signature verification only: nothing is installed."""

    def builder(self, generate, key_creation=True):
        tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=300, key_creation=key_creation)
        b = Builder(self.root, tools, generate)
        self.addCleanup(b.ledger.close)
        return b

    @staticmethod
    def label_generator(text):
        def generate(source, kind, request, cancelled):
            notes = source / 'res/values/notes.xml'
            notes.write_text(f'<?xml version="1.0" encoding="utf-8"?>\n<resources><string name="note">{text}</string>'
                             '</resources>\n')
        return generate

    def test_create_revise_same_identity(self):
        b = self.builder(self.label_generator('one'))
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'Notes app'})
        self.assertTrue(b.run_next())
        job = b.job(out['jobId'])
        self.assertEqual(job['state'], 'ready', job)
        lib = b.projects()[0]
        v1 = lib['versions'][0]
        self.assertEqual(v1['versionCode'], 1)
        self.assertTrue(b.artifact(v1['apk']['sha256']).is_file())
        keys = self.root / 'keys' / out['projectId']
        self.assertEqual(keys.stat().st_mode & 0o777, 0o700)
        self.assertEqual((keys / 'app.p12').stat().st_mode & 0o777, 0o600)
        b.generate = self.label_generator('two')
        rev = b.revise('d', out['projectId'], {'schemaVersion': 1, 'clientRequestId': 'r2', 'request': 'add a note'})
        self.assertTrue(b.run_next())
        self.assertEqual(b.job(rev['jobId'])['state'], 'ready', b.job(rev['jobId']))
        lib = b.projects()[0]
        self.assertEqual([v['versionCode'] for v in lib['versions']], [1, 2])
        self.assertNotEqual(lib['versions'][0]['sourceRevision'], lib['versions'][1]['sourceRevision'])
        self.assertEqual(len(lib['signerSha256']), 64)
        self.assertEqual(lib['packageName'], L.PACKAGE_PREFIX + out['projectId'])
        # A changed key is refused, never delivered as an update the phone would have to uninstall for.
        shutil.rmtree(keys)
        b.generate = self.label_generator('three')
        rev3 = b.revise('d', out['projectId'], {'schemaVersion': 1, 'clientRequestId': 'r3', 'request': 'again'})
        b.run_next()
        job = b.job(rev3['jobId'])
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'signing'), job)
        self.assertEqual(len(b.projects()[0]['versions']), 2)

    def test_identity_change_fails_and_source_resets(self):
        def hostile(source, kind, request, cancelled):
            m = source / 'AndroidManifest.xml'
            m.write_text(m.read_text().replace('package="', 'package="evil.'))
        b = self.builder(hostile)
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'x'})
        b.run_next()
        job = b.job(out['jobId'])
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'compile'), job)
        manifest = (self.root / 'projects' / out['projectId'] / 'source/AndroidManifest.xml').read_text()
        self.assertNotIn('evil.', manifest)  # the failed attempt did not carry over

    def test_compile_error_is_reported(self):
        def broken(source, kind, request, cancelled):
            (next(source.glob('src/**/MainActivity.java'))).write_text('package ' + kind + ' nonsense')
        b = self.builder(broken)
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'x'})
        b.run_next()
        self.assertEqual(b.job(out['jobId'])['failure']['category'], 'compile')

    def test_no_key_creation_signs_nothing(self):
        b = self.builder(self.label_generator('x'), key_creation=False)
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'x'})
        b.run_next()
        job = b.job(out['jobId'])
        self.assertEqual(job['failure']['category'], 'signing')
        self.assertIn('not enabled', job['failure']['summary'])
        self.assertFalse((self.root / 'keys' / out['projectId']).exists())
        self.assertEqual(b.projects()[0]['versions'], [])

    def test_generated_git_metadata_never_runs_or_records_keys(self):
        marker = self.root.parent / 'hook-ran'
        b = self.builder(self.label_generator('one'))
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': "Don't forget: reminders"})
        b.run_next()
        self.assertEqual(b.job(out['jobId'])['state'], 'ready', b.job(out['jobId']))  # an apostrophe builds

        def hostile(source, kind, request, cancelled):
            hooks = source / '.git/hooks'
            hooks.mkdir(parents=True)
            (hooks / 'pre-commit').write_text('#!/bin/sh\ntouch ' + str(marker) + '\n')
            (hooks / 'pre-commit').chmod(0o755)
            (source / '.git/config').write_text('[core]\n\tworktree = ' + str(self.root) + '\n')
        b.generate = hostile
        rev = b.revise('d', out['projectId'], {'schemaVersion': 1, 'clientRequestId': 'r2', 'request': 'x'})
        b.run_next()
        job = b.job(rev['jobId'])
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'compile'), job)
        self.assertFalse(marker.exists())
        source = self.root / 'projects' / out['projectId'] / 'source'
        self.assertFalse((source / '.git').exists())  # dropped on reset
        from rose.app_builder.service import _git
        tracked = _git(source, 'ls-tree', '-r', '--name-only', 'HEAD').split()
        self.assertFalse([p for p in tracked if 'keys' in p or p.endswith('.p12') or 'sqlite' in p], tracked)

    def test_an_interrupted_key_creation_is_cleared_before_any_signer(self):
        b = self.builder(self.label_generator('x'))
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'x'})
        keys = self.root / 'keys' / out['projectId']
        keys.mkdir(parents=True)
        (keys / 'store.pass').write_text('left over\n')
        b.run_next()
        self.assertEqual(b.job(out['jobId'])['state'], 'ready', b.job(out['jobId']))

    def test_shutdown_is_retryable_not_an_owner_cancel(self):
        holder = {}

        def stop(source, kind, request, cancelled):
            holder['b'].stopping.set()
        b = self.builder(stop)
        holder['b'] = b
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'x'})
        b.run_next()
        job = b.job(out['jobId'])
        self.assertEqual((job['state'], job['failure']['category'], job['failure']['retryable']),
                         ('failed', 'internal', True), job)

    def test_a_failing_reset_still_records_the_failure(self):
        def broken(source, kind, request, cancelled):
            raise toolchain.StepFailed('compile', 'nope')
        b = self.builder(broken)
        b._reset = lambda *a: (_ for _ in ()).throw(RuntimeError('git gone'))
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'x'})
        b.run_next()
        self.assertEqual(b.job(out['jobId'])['state'], 'failed')

    def test_a_nested_repository_never_wedges_later_revisions(self):
        b = self.builder(self.label_generator('one'))
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'x'})
        b.run_next()

        def nested(source, kind, request, cancelled):
            git = source / 'res/.git'
            for d in ('objects', 'refs'):
                (git / d).mkdir(parents=True)
            (git / 'HEAD').write_text('ref: refs/heads/main\n')
        b.generate = nested
        rev = b.revise('d', out['projectId'], {'schemaVersion': 1, 'clientRequestId': 'r2', 'request': 'x'})
        b.run_next()
        self.assertEqual(b.job(rev['jobId'])['state'], 'failed')
        b.generate = self.label_generator('two')
        rev = b.revise('d', out['projectId'], {'schemaVersion': 1, 'clientRequestId': 'r3', 'request': 'y'})
        b.run_next()
        self.assertEqual(b.job(rev['jobId'])['state'], 'ready', b.job(rev['jobId']))

    def test_a_retryable_failure_can_be_retried_as_the_next_attempt(self):
        calls = []

        def flaky(source, kind, request, cancelled):
            calls.append(1)
            if len(calls) == 1:
                raise toolchain.StepFailed('timeout', 'slow', retryable=True)
        b = self.builder(flaky)
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'x'})
        b.run_next()
        self.assertEqual(b.job(out['jobId'])['failure']['retryable'], True)
        self.assertEqual(b.retry(out['jobId'], {'schemaVersion': 1}), {'jobId': out['jobId'], 'attempt': 2})
        b.run_next()
        job = b.job(out['jobId'])
        self.assertEqual((job['state'], job['attempt']), ('ready', 2), job)
        with self.assertRaises(Refused):
            b.retry(out['jobId'], {'schemaVersion': 1})  # only failed, retryable jobs

    def test_a_different_valid_key_is_refused_by_the_pin(self):
        b = self.builder(self.label_generator('one'))
        a = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'a', 'description': 'A'})
        b.run_next()
        other = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'b', 'description': 'B'})
        b.run_next()
        keys = self.root / 'keys'
        shutil.rmtree(keys / a['projectId'])
        shutil.copytree(keys / other['projectId'], keys / a['projectId'])  # a complete key, but not A's
        rev = b.revise('d', a['projectId'], {'schemaVersion': 1, 'clientRequestId': 'a2', 'request': 'more'})
        b.run_next()
        job = b.job(rev['jobId'])
        self.assertEqual(job['failure']['category'], 'signing', job)
        self.assertIn('different key', job['failure']['summary'])

    def test_cancel_during_generation(self):
        holder = {}

        def slow(source, kind, request, cancelled):
            holder['b'].ledger.cancel(holder['job'])
        b = self.builder(slow)
        holder['b'] = b
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'x'})
        holder['job'] = out['jobId']
        b.run_next()
        job = b.job(out['jobId'])
        self.assertEqual((job['state'], job['failure']['category']), ('cancelled', 'cancelled'), job)


if __name__ == '__main__':
    unittest.main()
