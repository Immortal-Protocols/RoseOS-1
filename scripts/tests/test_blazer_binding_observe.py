"""BLAZER-BINDING-OBSERVE: scripts/blazer-binding-observe.py observes exactly the fixed files, names and resources and
nothing else: no link followed, no replacement missed, receipts and policy reduced to allowlisted identity fields,
names only from two directory levels, bounded output. Synthetic trees and fake /proc, cgroup and statvfs only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('blazer_binding_observe', ROOT/'scripts/blazer-binding-observe.py')
obs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(obs)
sha = lambda data: hashlib.sha256(data).hexdigest()


class FS:
    f_bavail, f_frsize, f_blocks = 1000, 4096, 5000


class Observe(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.files = {}
        for package in obs.PACKAGES:
            apk = self.tmp/f'release/signed-apps/{package}.apk'
            apk.parent.mkdir(parents=True, exist_ok=True)
            apk.write_bytes(f'apk {package}'.encode())
            receipt = apk.with_suffix('.receipt.json')
            receipt.write_text(json.dumps({'schemaVersion': 1, 'apk': apk.name, 'sha256': sha(apk.read_bytes()),
                                           'certificateSha256': ['a' * 64], 'signatureVerified': True,
                                           'keystorePath': '/secret/keystore.jks'}))
            self.files[f'apk:{package}'], self.files[f'receipt:{package}'] = apk, receipt
        policy = self.tmp/'release/background-policy.json'
        row = {'packageName': 'dev.pixel.voice', 'versionCode': 3, 'apkSha256': 'b' * 64, 'certificates': ['A' * 64],
               'note': 'not an identity field'}
        policy.write_text(json.dumps({'schemaVersion': 1, 'sdk': 37, 'prototypeControl': True, 'agent': row, 'voice': row,
                                      'targets': [row], 'operatorNote': 'never emitted'}))
        self.files['policy'] = policy
        for role in ('inventory', 'tool:aapt', 'tool:apksigner', 'tool:apksigner.jar', 'tool:java'):
            path = self.tmp/'tools'/role.replace(':', '_')
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(role.encode())
            self.files[role] = path
        self.home = self.tmp/'home'
        storage = self.home/'rose-actions'
        (storage/'blazer-integrate-2026110301').mkdir(parents=True)
        (storage/'blazer-integrate-2026110301/inside-never-listed').write_text('x')
        (storage/'frankel-2026110301').mkdir()
        (storage/'blazer-allocations').mkdir()
        (storage/'blazer-allocations/2026110301.json').write_text('{"secret": "never read"}')
        self.source = self.tmp/'source'
        for project in obs.GIT_META_PATHS:
            (self.source/project).mkdir(parents=True)
        (self.source/'vendor/adevtool/.git').mkdir()
        self.proc = self.tmp/'proc'
        (self.proc/'self').mkdir(parents=True)
        (self.proc/'meminfo').write_text('MemTotal:       263000000 kB\nMemAvailable:   200000000 kB\n')
        (self.proc/'self/cgroup').write_text('0::/system.slice/runner.service\n')
        cg = self.tmp/'cgroup/system.slice/runner.service'
        cg.mkdir(parents=True)
        (cg/'cpu.max').write_text('max 100000\n')
        (cg/'memory.max').write_text('max\n')

    def run_observe(self, **change):
        args = dict(host='immortal-os-builder', files=self.files, source=self.source, home=self.home, proc=self.proc,
                    cgroup_root=self.tmp/'cgroup', statvfs=lambda p: FS)
        args.update(change)
        return json.loads(obs.observe(**args))

    def test_a_complete_observation_reports_identities_and_nothing_else(self):
        report = self.run_observe()
        self.assertTrue(report['complete'], report['problems'])
        for role, path in self.files.items():
            self.assertEqual((report['files'][role]['state'], report['files'][role]['sha256']), ('present', sha(path.read_bytes())))
        text = json.dumps(report)
        for secret in ('/secret/keystore.jks', 'keystorePath', 'not an identity field', 'operatorNote', 'never read',
                       'inside-never-listed', 'frankel-2026110301'):
            self.assertNotIn(secret, text)
        receipt = report['identities']['receipt:dev.pixel.voice']
        self.assertEqual(set(receipt) - {'parsed'}, set(obs.RECEIPT_FIELDS))
        self.assertEqual(set(report['identities']['policy']['voice']), set(obs.POLICY_ROW_FIELDS))
        self.assertEqual(report['names']['storage'], {'state': 'listed', 'blazer': ['blazer-integrate-2026110301'], 'otherEntries': 2})  # frankel-…, blazer-allocations
        self.assertEqual(report['names']['reservations']['names'], ['2026110301.json'])
        self.assertEqual(report['gitMeta']['dotGit']['vendor/adevtool'], 'directory')
        self.assertEqual(report['gitMeta']['dotGit']['frameworks/base'], 'absent')
        resources = report['resources']
        self.assertEqual((resources['memTotalBytes'], resources['cgroup']['cpu_max'], resources['storage']['availableBytes']),
                         (263000000 * 1024, 'max 100000', 1000 * 4096))
        self.assertLessEqual(len(json.dumps(report)), obs.LIMITS['outputBytes'])

    def test_links_are_reported_never_followed(self):
        outside = self.tmp/'outside'
        outside.mkdir()
        (outside/'dev.pixel.voice.apk').write_bytes(b'elsewhere')
        apk = self.files['apk:dev.pixel.voice']
        apk.unlink()
        apk.symlink_to(outside/'dev.pixel.voice.apk')
        linked = self.tmp/'linked'
        linked.symlink_to(self.tmp/'tools')
        self.files['tool:aapt'] = linked/'tool_aapt'
        report = self.run_observe()
        self.assertFalse(report['complete'])
        for role in ('apk:dev.pixel.voice', 'tool:aapt'):
            self.assertEqual(report['files'][role]['state'], 'link')
            self.assertNotIn('sha256', report['files'][role])

    def test_replacement_size_and_type_races_are_reported(self):
        real_open, target = obs.os.open, self.files['tool:java']

        def swapping(path, flags, *a, **kw):
            if os.fsdecode(path) == target.name and kw.get('dir_fd') is not None:
                target.unlink()
                target.write_bytes(b'replaced after lstat')
            return real_open(path, flags, *a, **kw)
        obs.os.open = swapping
        try:
            report = self.run_observe()
        finally:
            obs.os.open = real_open
        self.assertEqual(report['files']['tool:java']['state'], 'changed')
        (self.files['tool:aapt']).unlink()
        (self.files['tool:aapt']).mkdir()
        saved = dict(obs.LIMITS)
        obs.LIMITS['fileBytes'] = 4
        try:
            report = self.run_observe()
        finally:
            obs.LIMITS.clear()
            obs.LIMITS.update(saved)
        self.assertEqual(report['files']['tool:aapt']['state'], 'not-regular')
        self.assertEqual(report['files']['policy']['state'], 'too-large')

    def swap_on_open(self, component, outside):
        """Replace directory COMPONENT by a link to OUTSIDE at the moment the walk opens it."""
        real_open = obs.os.open

        def swapping(path, flags, *a, **kw):
            name = os.fsdecode(path)
            if kw.get('dir_fd') is not None and name == component.name and component.is_dir() and not component.is_symlink():
                component.rename(component.with_name(component.name + '.moved'))
                component.symlink_to(outside)
            return real_open(path, flags, *a, **kw)
        return real_open, swapping

    def test_a_parent_replaced_by_a_link_during_a_file_read_is_never_traversed(self):
        outside = self.tmp/'outside'
        outside.mkdir()
        for package in obs.PACKAGES:
            (outside/f'{package}.apk').write_bytes(b'OUT-OF-SCOPE BYTES')
            (outside/f'{package}.receipt.json').write_text('{"apk": "out-of-scope"}')
        signed = self.files['apk:dev.pixel.voice'].parent
        real_open, swapping = self.swap_on_open(signed, outside)
        read = []
        real_read = obs.os.read
        obs.os.open = swapping
        obs.os.read = lambda fd, n: (lambda data: (read.append(data), data)[1])(real_read(fd, n))
        try:
            report = self.run_observe()
        finally:
            obs.os.open, obs.os.read = real_open, real_read
        for package in obs.PACKAGES:
            row = report['files'][f'apk:{package}']
            self.assertEqual((row['state'], row['component']), ('link', str(signed)))
            self.assertNotIn('sha256', row)
        self.assertFalse([d for d in read if b'OUT-OF-SCOPE' in d or b'out-of-scope' in d])
        self.assertNotIn('out-of-scope', json.dumps(report))
        self.assertFalse(report['complete'])

    def test_a_parent_replaced_by_a_link_during_enumeration_lists_nothing(self):
        outside = self.tmp/'outside-storage'
        outside.mkdir()
        (outside/'blazer-integrate-2099010101').mkdir()
        storage = self.home/'rose-actions'
        real_open, swapping = self.swap_on_open(storage, outside)
        obs.os.open = swapping
        try:
            report = self.run_observe()
        finally:
            obs.os.open = real_open
        self.assertEqual(report['names']['storage'], {'state': 'link', 'blazer': [], 'otherEntries': 0})
        self.assertNotIn('2099010101', json.dumps(report))
        self.assertIn('storage names: link', report['problems'])

    def test_git_types_are_labelled_presence_only(self):
        report = self.run_observe()
        self.assertIn('never a revision or a completed git-meta', report['gitMeta']['evidence'])
        (self.source/'packages/apps/Updater').rmdir()
        (self.source/'packages/apps/Updater').symlink_to(self.tmp)
        report = self.run_observe()
        self.assertEqual(report['gitMeta']['dotGit']['packages/apps/Updater'], 'project link')

    def test_absent_unbound_deadline_truncation_and_output_cap(self):
        self.files['inventory'] = None
        self.files['policy'].unlink()
        report = self.run_observe()
        self.assertEqual((report['files']['inventory']['state'], report['files']['policy']['state']), ('unbound', 'absent'))
        self.assertIn('inventory: unbound', report['problems'])
        ticks = iter(range(0, 10**6, 1000))
        report = self.run_observe(clock=lambda: next(ticks))
        self.assertFalse(report['complete'])
        self.assertIn('deadline', {row['state'] for row in report['files'].values()})
        for n in range(obs.LIMITS['reportedNames'] + 5):
            (self.home/'rose-actions'/f'blazer-reconcile-20261103{n:02d}' if n < 100 else
             self.home/'rose-actions'/f'blazer-reconcile-2026110{n:03d}').mkdir()
        report = self.run_observe()
        self.assertEqual(report['names']['storage']['state'], 'truncated')
        self.assertLessEqual(len(report['names']['storage']['blazer']), obs.LIMITS['reportedNames'])
        saved = obs.LIMITS['outputBytes']
        obs.LIMITS['outputBytes'] = 200
        try:
            report = self.run_observe()
        finally:
            obs.LIMITS['outputBytes'] = saved
        self.assertEqual((report['complete'], report['problems']), (False, ['output: the report exceeds outputBytes']))

    def test_the_fixed_targets_and_the_cli(self):
        files = obs.targets()
        self.assertEqual(str(files['policy']), '/home/pixelbuild/rose-actions/frankel-2026110301/release/background-policy.json')
        self.assertEqual(str(files['tool:apksigner.jar']), '/home/pixelbuild/rose-actions/tools/android-sdk/build-tools/android-15/lib/apksigner.jar')
        self.assertEqual(str(files['tool:java']), '/home/pixelbuild/grapheneos-2026100600/prebuilts/jdk/jdk21/linux-x86/bin/java')
        self.assertEqual(len(files), 12)
        self.assertEqual(obs.main(['observe', '--host', 'other']), 2)
        text = (ROOT/'scripts/blazer-binding-observe.py').read_text().split('"""', 2)[2]
        for absent in ('subprocess', 'os.walk', 'rglob', 'urlopen', 'O_WRONLY', 'unlink(', 'rename', 'chmod', 'linked_component'):
            self.assertNotIn(absent, text)
        # every open is relative to a walked descriptor, except the walk's own start at /
        import re as _re
        opens = _re.findall(r'os\.open\(([^,]+),', text)
        self.assertTrue(all(arg.strip() in ("'/'", 'part', 'leaf', 'path') for arg in opens), opens)
        self.assertEqual(text.count('os.open(path,'), 1)  # small_read: /proc and cgroup files only


if __name__ == '__main__':
    unittest.main()
