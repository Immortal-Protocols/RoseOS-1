"""FAM-BLAZER-INTEGRATE-005: scripts/blazer_integration.py admit, render and the compile binding.

Producer-consumer fixtures only: a synthetic signed bundle (text "APKs"), fake aapt/apksigner that report what the
bytes say, a fake git object view of product C and recipe R, and synthetic source trees. No key, network, host,
download, generation, lunch, compile or phone; a pass proves the admission and binding logic, never a real build."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import blazer_integration as bi  # noqa: E402

TEMPLATE = json.loads((ROOT/'os/build-only/blazer-reconcile.json').read_text())
PINS = TEMPLATE['candidateInputs']
SIGNER = PINS['appSignerSha256']
BLOB = PINS['androidManifestBlob']
PRODUCT = 'c' * 40
HEAD = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
BUILD = '2026103150'
VERSIONS = {'dev.pixel.backgroundcontrol': 29, 'dev.pixel.voice': 312, 'dev.pixel.calculator.agent': 4}
ROUTE = {'schema': 'roseos.blazer-updater-route.v1', 'device': 'blazer',
    'updaterUrl': 'https://github.com/Example-Owner/blazer-ota/releases/download/',
    'releaseIndex': 'https://api.github.com/repos/Example-Owner/blazer-ota/releases', 'publishedChannel': 'stable',
    'producerContract': dict(bi.PRODUCER), 'owner': 'synthetic release owner', 'approvalRef': 'synthetic-test-only'}

def sha(data):
    return hashlib.sha256(data).hexdigest()

class Fixture(unittest.TestCase):
    """Builds a bundle, route and tools; admit() yields real local-inputs.json; tree() a matching reconciled tree."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='blazer-integration-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        tools = self.tmp/'tools'
        tools.mkdir()
        # The synthetic APK text is "package versionCode signer"; the fakes report exactly that.
        (tools/'aapt').write_text('#!/bin/sh\nset -- $(cat "$3")\necho "package: name=\'$1\' versionCode=\'$2\'"\n')
        (tools/'apksigner').write_text('#!/bin/sh\nfor f; do :; done\nset -- $(cat "$f")\n'
            'echo "Signer #1 certificate SHA-256 digest: $3"\n')
        for tool in ('aapt', 'apksigner'):
            os.chmod(tools/tool, 0o755)
        self.tools = {'aapt': str(tools/'aapt'), 'apksigner': str(tools/'apksigner')}
        self.objects = {('rev-parse', 'HEAD'): HEAD, ('rev-parse', f'{PRODUCT}:os/pinned-manifest.xml'): BLOB,
            ('rev-parse', f'{HEAD}:os/pinned-manifest.xml'): BLOB}
        self.objects.update({('rev-parse', f'{PRODUCT}:{f}'): 'b' * 40 for f in PINS['candidateFiles']})
        self.git = lambda *args: self.objects.get(args)
        self.route = self.tmp/'route.json'
        self.route.write_text(json.dumps(ROUTE))
        self.bundle = self.make_bundle()

    def apk(self, package, version=None, signer=SIGNER):
        return f'{package} {version or VERSIONS[package]} {signer.upper()}\n'.encode()

    def make_bundle(self, apks=None, candidate=None, policy=None):
        bundle = self.tmp/'bundle'
        shutil.rmtree(bundle, ignore_errors=True)
        (bundle/'signed-apps').mkdir(parents=True)
        apks = apks or {p: self.apk(p) for p in VERSIONS}
        for package, data in apks.items():
            (bundle/'signed-apps'/f'{package}.apk').write_bytes(data)
            (bundle/'signed-apps'/f'{package}.receipt.json').write_text(json.dumps({'schemaVersion': 1,
                'apk': f'{package}.apk', 'sha256': sha(data), 'certificateSha256': [SIGNER], 'signatureVerified': True}))
        row = lambda p: {'packageName': p, 'versionCode': VERSIONS[p], 'apkSha256': sha(apks[p]),
            'certificates': [SIGNER.upper()]}
        written = {'schemaVersion': 1, 'sdk': 37, 'prototypeControl': True, 'agent': row('dev.pixel.backgroundcontrol'),
            'voice': row('dev.pixel.voice'), 'targets': [row('dev.pixel.calculator.agent')]}
        written.update(policy or {})
        (bundle/'background-policy.json').write_text(json.dumps(written))
        record = {'schema': PINS['schema'], 'device': 'blazer', 'build': BUILD, 'product': PRODUCT, 'recipe': HEAD,
            'androidManifestBlob': BLOB, 'apps': [[p, v] for p, v in VERSIONS.items()]}
        record.update(candidate or {})
        (bundle/'candidate.json').write_text(json.dumps(record))
        return bundle

    def admit(self, out=None, route='default', **kwargs):
        out = out or self.tmp/'inputs'
        return bi.admit(self.bundle, self.route if route == 'default' else route, out, self.tools,
            git_read=kwargs.pop('git_read', self.git), now=lambda: 0, **kwargs), out

    def tree(self, inputs):
        """A source tree as host-reconcile.py would leave it for these inputs, and the receipt describing it."""
        src = self.tmp/'src'
        files = {}
        for app in inputs['apps']:
            relative = bi.INSTALLED['agent'] if app['role'] == 'agent' else \
                bi.INSTALLED['preinstall'].format(module=app['package'].replace('.', '_'))
            files[relative] = Path(app['signedPath']).read_bytes()
        files['packages/apps/Launcher3/quickstep/res/layout/fallback_recents_activity.xml'] = b'<RoseAgentStrip/>\n'
        route = inputs['updaterRoute']
        files[bi.UPDATER_CONFIG] = (f'<string name="url" translatable="false">{route["updaterUrl"]}</string>\n'
            f'<string name="release_index" translatable="false">{route["releaseIndex"]}</string>\n').encode()
        policy = json.loads(Path(inputs['policy']['path']).read_text())
        items = lambda rows: ''.join(f'<item>{r["packageName"]}:{c}</item>' for r in rows for c in r['certificates'])
        files[bi.POLICY_CONFIG] = (items([policy['agent']]) + items(policy['targets']) +
            items([policy['agent']]) + '<bool name="config_pixelComputerControlLockedSessions">false</bool>').encode()
        for relative, data in files.items():
            (src/relative).parent.mkdir(parents=True, exist_ok=True)
            (src/relative).write_bytes(data)
        ours = {name: bi.sha256_file(ROOT/'scripts'/name) for name in ('apply-os-changes.py', 'background_apks.py',
            'integration_copy.py')}
        record = {'receiptType': 'reconcile', 'label': f'blazer-{BUILD}', 'patcher': {'patcher': 'trusted', 'sha256': ours},
            'entries': {relative: {'after': sha(data)} for relative, data in files.items()}}
        return src, record

class AdmitTest(Fixture):
    def test_admits_verified_bundle_with_route_and_writes_provenance_last(self):
        record, out = self.admit()
        self.assertEqual(record['candidate'], {'device': 'blazer', 'build': BUILD, 'product': PRODUCT, 'recipe': HEAD,
            'androidManifestBlob': BLOB})
        self.assertEqual([(a['package'], a['versionCode'], a['signerSha256']) for a in record['apps']],
            [(p, VERSIONS[p], SIGNER) for p in ('dev.pixel.backgroundcontrol', 'dev.pixel.voice', 'dev.pixel.calculator.agent')])
        self.assertEqual(record['policy']['prototypeControl'], True)
        self.assertEqual(record['updaterRoute']['updaterUrl'], ROUTE['updaterUrl'])
        self.assertEqual(json.loads((out/'local-inputs.json').read_text()), record)
        for app in record['apps']:
            self.assertEqual(bi.sha256_file(app['signedPath']), app['sha256'])

    def test_missing_route_refuses_and_names_the_operator_input(self):
        with self.assertRaisesRegex(bi.Failed, r'no blazer Updater route: supply --updater-route ROUTE.json from the release owner'):
            self.admit(route=None)
        self.assertFalse(os.path.lexists(self.tmp/'inputs'))

    def test_invalid_or_borrowed_routes_refuse(self):
        cases = (
            (dict(device='rango'), 'not blazer'),
            (dict(updaterUrl='https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/'), 'per-release'),
            (dict(releaseIndex='https://api.github.com/repos/Other/repo/releases'), 'per-release'),
            (dict(publishedChannel='beta'), 'producer contract'),
            (dict(producerContract=dict(bi.PRODUCER, tag='rango-{build}')), 'producer contract'),
            (dict(owner=' '), 'owner'),
            (dict(extra=1), 'must be exactly'))
        for change, pattern in cases:
            self.route.write_text(json.dumps(dict(ROUTE, **change)))
            with self.assertRaisesRegex(bi.Failed, pattern):
                self.admit()
        self.route.unlink()
        os.symlink(self.tmp/'tools/aapt', self.route)
        with self.assertRaisesRegex(bi.Failed, 'not a link'):
            self.admit()

    def test_wrong_device_revision_signer_hash_policy_and_inputs_refuse(self):
        other = 'f' * 64
        cases = (
            (dict(candidate=dict(device='rango')), 'device blazer'),
            (dict(candidate=dict(androidManifestBlob='0' * 40)), 'another Android manifest'),
            (dict(candidate=dict(recipe='0' * 40)), 'recipe R'),
            (dict(candidate=dict(apps=[['dev.pixel.voice', 312]])), 'apps must name each'),
            (dict(apks={**{p: self.apk(p) for p in VERSIONS}, 'dev.pixel.voice': self.apk('dev.pixel.voice', signer=other)}),
                'receipt|signer|policy'),
            (dict(apks={**{p: self.apk(p) for p in VERSIONS}, 'dev.pixel.voice': self.apk('dev.pixel.voice', version=311)}),
                'package/version/signer'),
            (dict(policy=dict(prototypeControl=False)), 'control policy'),
            (dict(policy=dict(sdk=36)), 'control policy|SDK'))
        for change, pattern in cases:
            self.bundle = self.make_bundle(**change)
            with self.assertRaisesRegex(bi.Failed, pattern):
                self.admit()
            self.assertFalse(os.path.lexists(self.tmp/'inputs'), 'no provenance on refusal')
        self.bundle = self.make_bundle()
        receipt = self.bundle/'signed-apps/dev.pixel.voice.receipt.json'
        receipt.write_text(json.dumps(dict(json.loads(receipt.read_text()), sha256='0' * 64)))
        with self.assertRaisesRegex(bi.Failed, 'does not match'):
            self.admit()
        self.bundle = self.make_bundle()
        (self.bundle/'signed-apps/dev.pixel.voice.apk').write_bytes(self.apk('dev.pixel.voice') + b' ')
        with self.assertRaisesRegex(bi.Failed, 'does not match'):
            self.admit()
        self.bundle = self.make_bundle()
        (self.bundle/'background-policy.json').unlink()
        with self.assertRaisesRegex(bi.Failed, 'missing'):
            self.admit()
        self.bundle = self.make_bundle()
        (self.bundle/'extra.txt').write_text('x')
        with self.assertRaisesRegex(bi.Failed, 'unexpected'):
            self.admit()
        self.bundle = self.make_bundle()
        del self.objects[('rev-parse', f'{PRODUCT}:os/patches/rose-switcher-launcher3.patch')]
        with self.assertRaisesRegex(bi.Failed, 'Launcher3/Updater integration'):
            self.admit()

    def test_output_collision_refuses(self):
        (self.tmp/'inputs').mkdir()
        with self.assertRaisesRegex(bi.Failed, 'exists'):
            self.admit()

class ApprovedRouteTest(Fixture):
    """FAM-BLAZER-ROUTE-006: the checked-in route is Nathan's approved Blazer Beta route (approvalRef below), checked by
    the same check_route and admitted end to end. A source route file only: not a published feed or runtime acceptance."""
    def test_checked_in_route_is_the_approved_blazer_route(self):
        path = ROOT/'os/build-only/blazer-updater-route.json'
        route, record = bi.check_route(path)
        self.assertEqual(route, {'schema': 'roseos.blazer-updater-route.v1', 'device': 'blazer',
            'updaterUrl': 'https://github.com/Immortal-Protocols/pixel-ota/releases/download/',
            'releaseIndex': 'https://api.github.com/repos/Immortal-Protocols/pixel-ota/releases', 'publishedChannel': 'stable',
            'producerContract': bi.PRODUCER, 'owner': 'Dev Infra / Release',
            'approvalRef': 'phone:6cc2a194-7c22-4b63-bc65-a3961d986858; reply-to:379e0772-6bb6-4790-92a2-6e84b7a8b4c4; '
                '2026-11-01T00:12:25Z'})
        self.assertEqual(record['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        # Frankel keeps its Latest route; Blazer never reads releases/latest.
        frankel = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())['host']['integrate']['reconcile']['applyArgs']
        self.assertEqual(frankel[frankel.index('--updater-url') + 1],
            'https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/')
        self.assertNotIn('/latest/', route['updaterUrl'])
        record, out = self.admit(route=path)
        self.assertEqual((record['updaterRoute']['updaterUrl'], record['updaterRoute']['sha256']),
            (route['updaterUrl'], hashlib.sha256(path.read_bytes()).hexdigest()))

class RenderTest(Fixture):
    def test_renders_the_host_reconcile_config_from_operator_paths(self):
        record, out = self.admit()
        src = self.tmp/'src'
        src.mkdir()
        config_path = self.tmp/'blazer-reconcile-config.json'
        config, command = bi.render(out/'local-inputs.json', str(src), str(self.tmp/'blazer.lock'),
            str(self.tmp/'reconcile'), str(self.tmp/'work'), config_path)
        self.assertEqual(json.loads(config_path.read_text()), config)
        reconcile = config['host']['integrate']['reconcile']
        self.assertIn('packages/apps/Launcher3', reconcile['projects'])
        args = reconcile['applyArgs']
        self.assertEqual(args[args.index('--updater-url') + 1], ROUTE['updaterUrl'])
        self.assertEqual(args[args.index('--updater-release-index') + 1], ROUTE['releaseIndex'])
        self.assertEqual(args[:2], ['--background-policy', '{policy}'])
        self.assertNotIn('--locked-control', args)
        # The rendered file is exactly what the existing host-reconcile.py main() reads and formats.
        apply_args = [part.format(policy='P', signed_apps='S') for part in args]
        self.assertIn('S/dev.pixel.voice.apk', apply_args)
        self.assertEqual((config['host']['sourceRoot'], reconcile['receiptDir']), (str(src), str(self.tmp/'reconcile')))
        self.assertEqual(command[-2:], ['--label', f'blazer-{BUILD}'])
        self.assertIn('host-reconcile.py', command[6])

    def test_operator_path_collisions_symlinks_and_reuse_refuse(self):
        _, out = self.admit()
        src = self.tmp/'src'
        src.mkdir()
        inputs = out/'local-inputs.json'
        base = dict(source_root=str(src), lock=str(self.tmp/'l'), receipt_dir=str(self.tmp/'r'), work_dir=str(self.tmp/'w'))
        for change, pattern in ((dict(receipt_dir='/home/pixelbuild/rose-actions/reconcile'), 'collides'),
                (dict(source_root='/srv/rose-rango/src'), 'collides'), (dict(work_dir='/srv'), 'collides'),
                (dict(lock='relative.lock'), 'absolute')):
            with self.assertRaisesRegex(bi.Failed, pattern):
                bi.render(inputs, out=self.tmp/'c.json', **dict(base, **change))
        os.symlink(src, self.tmp/'src-link')
        with self.assertRaisesRegex(bi.Failed, 'symlink'):
            bi.render(inputs, out=self.tmp/'c.json', **dict(base, source_root=str(self.tmp/'src-link')))
        bi.render(inputs, out=self.tmp/'c.json', **base)
        with self.assertRaisesRegex(bi.Failed, 'must be a new'):
            bi.render(inputs, out=self.tmp/'c.json', **base)

class SharedLockTest(Fixture):
    """--lock may be exactly Frankel's shared host.lockPath (blazer.json lockFrom), and nothing else may touch it."""
    SHARED = '/home/pixelbuild/frankel-build.lock'

    def paths(self):
        _, out = self.admit()
        src = self.tmp/'src'
        src.mkdir()
        return out/'local-inputs.json', dict(source_root=str(src), lock=str(self.tmp/'l'), receipt_dir=str(self.tmp/'r'),
                                             work_dir=str(self.tmp/'w'))

    def test_the_shared_frankel_lock_is_accepted_as_the_lock_only(self):
        inputs, base = self.paths()
        self.assertEqual(str(bi.shared_lock()), self.SHARED)
        self.assertEqual(bi.shared_lock(), Path(json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())['host']['lockPath']))
        self.assertEqual(json.loads((ROOT/'os/build-only/blazer.json').read_text())['preparation']['lockFrom'],
                         'os/release-pipeline/frankel.json host.lockPath', 'blazer serializes on the Frankel lock by design')
        config, command = bi.render(inputs, out=self.tmp/'c.json', **dict(base, lock=self.SHARED))
        self.assertEqual(config['host']['lockPath'], self.SHARED)
        self.assertEqual(command[:5], ['flock', '--nonblock', '--conflict-exit-code', '75', self.SHARED])

    def test_everything_else_near_the_shared_lock_or_other_builders_still_collides(self):
        inputs, base = self.paths()
        for change in (dict(lock=self.SHARED + '/inner'), dict(lock='/home/pixelbuild'),
                       dict(lock='/home/pixelbuild/grapheneos-2026100600/x.lock'), dict(lock='/srv/rose-rango/rango-build.lock'),
                       dict(lock='/home/pixelbuild/rose-actions/reconcile/blazer.lock'),
                       dict(receipt_dir=self.SHARED), dict(work_dir=self.SHARED), dict(source_root=self.SHARED),
                       dict(work_dir='/home/pixelbuild'), dict(receipt_dir=self.SHARED + '/r'),
                       dict(lock=self.SHARED, receipt_dir='/home/pixelbuild/rose-actions/reconcile')):
            with self.subTest(change):
                with self.assertRaisesRegex(bi.Failed, 'collides'):
                    bi.render(inputs, out=self.tmp/'c.json', **dict(base, **change))
        self.assertFalse((self.tmp/'c.json').exists(), 'nothing is written on a refusal')

    def test_the_exception_follows_the_checked_in_frankel_lock_only(self):
        inputs, base = self.paths()
        root = self.tmp/'root'
        for rel in bi.OTHER_BUILDERS:
            (root/rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/rel, root/rel)
        frankel = json.loads((root/bi.OTHER_BUILDERS[0]).read_text())
        frankel['host']['lockPath'] = '/home/pixelbuild/moved.lock'
        (root/bi.OTHER_BUILDERS[0]).write_text(json.dumps(frankel))
        self.assertEqual(bi.shared_lock(root), Path('/home/pixelbuild/moved.lock'))
        # The old path is no longer Frankel's lock (Rango's shared-builder route still names it): it now collides, and
        # only the moved, checked-in Frankel lock is accepted.
        self.assertIn(Path(self.SHARED), bi.other_builder_paths(root))
        with self.assertRaisesRegex(bi.Failed, 'collides'):
            bi.render(inputs, out=self.tmp/'c.json', root=root, **dict(base, lock=self.SHARED))
        config, _ = bi.render(inputs, out=self.tmp/'c.json', root=root, **dict(base, lock='/home/pixelbuild/moved.lock'))
        self.assertEqual(config['host']['lockPath'], '/home/pixelbuild/moved.lock')
        # Even the exact shared lock is still checked against every other builder path: one inside Rango's tree refuses.
        frankel['host']['lockPath'] = '/srv/rose-rango/src/frankel.lock'
        (root/bi.OTHER_BUILDERS[0]).write_text(json.dumps(frankel))
        with self.assertRaisesRegex(bi.Failed, 'collides'):
            bi.render(inputs, out=self.tmp/'c2.json', root=root, **dict(base, lock='/srv/rose-rango/src/frankel.lock'))

class CompileBindingTest(Fixture):
    def test_matching_receipt_and_tree_bind(self):
        _, out = self.admit()
        src, record = self.tree(json.loads((out/'local-inputs.json').read_text()))
        self.assertEqual(bi.compile_problems(record, src, out/'local-inputs.json'), [])

    def test_drift_foreign_labels_and_missing_installs_refuse(self):
        _, out = self.admit()
        inputs = json.loads((out/'local-inputs.json').read_text())
        src, record = self.tree(inputs)
        check = lambda r=record: ' '.join(bi.compile_problems(r, src, out/'local-inputs.json'))
        self.assertIn('not blazer-', check(dict(record, label='rango-2026103001')))
        agent = bi.INSTALLED['agent']
        entries = dict(record['entries'], **{agent: {'after': '0' * 64}})
        self.assertIn('did not install the admitted dev.pixel.backgroundcontrol', check(dict(record, entries=entries)))
        entries = {k: v for k, v in record['entries'].items() if not k.startswith('packages/apps/Launcher3/')}
        self.assertIn('no Launcher3 change', check(dict(record, entries=entries)))
        entries = {k: v for k, v in record['entries'].items() if k != bi.UPDATER_CONFIG}
        self.assertIn('routed Updater config', check(dict(record, entries=entries)))
        (src/bi.UPDATER_CONFIG).write_text('<string name="url" translatable="false">https://github.com/Immortal-Protocols/'
            'pixel-ota/releases/latest/download/</string>')
        self.assertIn('does not carry the admitted blazer route', check())
        config = src/bi.POLICY_CONFIG
        config.write_text(config.read_text().replace('false</bool>', 'true</bool>'))
        self.assertIn('locked sessions are on', check())
        config.write_text('')
        self.assertIn('PrototypeAgents pin', check())
        Path(inputs['policy']['path']).chmod(0o644)
        Path(inputs['policy']['path']).write_text('{}')
        self.assertIn('differs from the admitted one', check())
        self.assertIn('blazer local-inputs.json', ' '.join(bi.compile_problems(record, src, None)))

class TemplateTest(unittest.TestCase):
    def test_template_matches_shipped_identities_and_refuses_drift(self):
        bi.load_template()
        for change, pattern in ((lambda t: t['candidateInputs'].update(appSignerSha256='0' * 64), 'existing app signer'),
                (lambda t: t['candidateInputs'].update(prototypeControl=False), 'control policy'),
                (lambda t: t['reconcile']['applyArgs'].append('--locked-control'), 'must not lock'),
                (lambda t: t['reconcile']['applyArgs'].__setitem__(6, 'https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/'),
                    'admitted route'),
                (lambda t: t['reconcile']['projects'].remove('packages/apps/Launcher3'), 'Launcher3 must be reconciled')):
            template = json.loads(json.dumps(TEMPLATE))
            change(template)
            with tempfile.NamedTemporaryFile('w', suffix='.json') as handle:
                handle.write(json.dumps(template))
                handle.flush()
                with self.assertRaisesRegex(bi.Failed, pattern):
                    bi.load_template(handle.name)

if __name__ == '__main__':
    unittest.main()
