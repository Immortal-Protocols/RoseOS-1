"""FRANKEL-FACTORY: the manual frankel-factory-observe and frankel-factory-package modes of
.github/workflows/os-release.yml, scripts/frankel-factory-package-wiring.py and its committed binding
os/release-pipeline/frankel-factory.json.

Stand-ins only: no host, real lock, packaging, token or network. The observation runs on a synthetic builder in a
temporary directory: a real release-stage.py receipt chain, the sign and verification receipts, a small signed
target-files ZIP, the signing release directory host-sign.py leaves (its owner and signed markers and a small otatools
ZIP built from the pinned generator fixture), and the pinned release-script fixture. The committed sources the wiring
reads are copies in which only host.sourceRoot and lockPath point there. The binding values used below are shape-valid
test values for that synthetic build, never a claim about a real Frankel build. The helpers
(scripts/host-factory-package.py, scripts/factory-package.py, scripts/rango-firmware-observe.py) are never run here."""
import contextlib
import dataclasses
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os_release_shape as shape  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import factory_package_route as route_lib  # noqa: E402

WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
OBSERVE, PACKAGE = 'frankel-factory-observe', 'frankel-factory-package'
WIRING = ROOT/'scripts/frankel-factory-package-wiring.py'
PIPELINE = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
FLAVOR = json.loads((ROOT/'os/flavors/frankel.json').read_text())
PRODUCTION, LOCK = PIPELINE['host']['sourceRoot'], PIPELINE['host']['lockPath']
GENERATOR = FLAVOR['inventory']['generatorFixture']['files']
SCRIPT_PIN = FLAVOR['provenance']['releaseScript']['generate-release.sh']
GENERATOR_FIXTURE = ROOT/'scripts/tests/fixtures/factory-package/device_common-1f5ba267'
SCRIPT_FIXTURE = ROOT/'scripts/tests/fixtures/permission-apex/script/generate-release.sh'
# Shape-valid test values for the synthetic build only.
BUILD, STAMP, CANDIDATE, SOURCE_RUN = '2026110499', '1793900000', 'c0ffee00' * 5, '41700000000'
BOUND = {'build': BUILD, 'buildTimestamp': STAMP, 'candidate': CANDIDATE, 'sourceRun': SOURCE_RUN,
         'signedTargetFilesSha256': 'a' * 64, 'otatoolsSha256': 'b' * 64, 'fastbootSha256': 'c' * 64}
UNBOUND = dict.fromkeys(BOUND)
FORBIDDEN = ('id-token', 'secrets.', 'actions-gcp-login', 'GCP_', 'PIXEL_', 'publish', 'host-sign', 'signing-bootstrap',
             'ssh-keygen', 'download-artifact', 'release-pipeline.py', 'factory-package.log', 'target_files')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wiring = load('frankel_factory_package_wiring', WIRING)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def jobs():
    return shape.jobs(WORKFLOW)


def step(job, name):
    match = re.search(r'(?ms)^      - name: ' + re.escape(name) + r'\n(.*?)(?=^      - |\Z)', job)
    assert match, name
    return match[1]


def script(step_text):
    block = re.search(r'(?ms)^        run: \|\n(.*)', step_text)
    if block:
        lines = []
        for line in block[1].splitlines():
            if line.strip() and not line.startswith(' ' * 10):
                break
            lines.append(line[10:])
        return '\n'.join(lines) + '\n'
    return re.search(r'(?m)^        run: (.+)$', step_text)[1] + '\n'


def code(text):
    return '\n'.join(line for line in text.splitlines() if not line.strip().startswith('#'))


def wire(*argv):
    return subprocess.run([sys.executable, str(WIRING), *argv], capture_output=True, text=True, cwd=ROOT)


def checkout(parent, production=None, lock=None, raw=None, **binding):
    """A temporary root holding copies of the committed sources the wiring reads: the binding with BINDING values set
    (or RAW text), os/release-pipeline/frankel.json with host.sourceRoot/lockPath moved when given, and
    os/flavors/frankel.json unchanged."""
    root = Path(parent)/'checkout'
    (root/'os/release-pipeline').mkdir(parents=True, exist_ok=True)
    (root/'os/flavors').mkdir(parents=True, exist_ok=True)
    data = json.loads((ROOT/wiring.BINDING).read_text())
    data.update(binding)
    (root/wiring.BINDING).write_text(raw if raw is not None else json.dumps(data, indent=2))
    pipeline = json.loads(json.dumps(PIPELINE))
    if production is not None:
        pipeline['host']['sourceRoot'] = str(production)
    if lock is not None:
        pipeline['host']['lockPath'] = str(lock)
    (root/'os/release-pipeline/frankel.json').write_text(json.dumps(pipeline, indent=2))
    shutil.copyfile(ROOT/'os/flavors/frankel.json', root/'os/flavors/frankel.json')
    return root


class Binding(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_committed_binding_is_exact_and_every_value_unbound(self):
        binding = wiring.load_binding()
        self.assertEqual({k: binding[k] for k in BOUND}, UNBOUND)  # nothing is guessed or carried from another device
        self.assertEqual((binding['schemaVersion'], binding['kind']), (1, 'roseos.frankel-factory-package'))
        self.assertIn('unsigned intermediate', binding['basis'])
        self.assertEqual(wiring.NEEDS, {
            'observe': ('build', 'buildTimestamp', 'candidate', 'sourceRun', 'signedTargetFilesSha256'),
            'package': ('build', 'buildTimestamp', 'candidate', 'sourceRun', 'signedTargetFilesSha256', 'otatoolsSha256',
                        'fastbootSha256')})

    def test_unbound_stages_exit_4_before_any_host_step(self):
        for stage, missing in (('observe', 'build, buildTimestamp, candidate, sourceRun, signedTargetFilesSha256'),
                               ('package', 'build, buildTimestamp, candidate, sourceRun, signedTargetFilesSha256, '
                                           'otatoolsSha256, fastbootSha256')):
            result = wire('check', '--stage', stage)
            self.assertEqual((result.returncode, result.stdout), (4, ''), stage)
            self.assertEqual(json.loads(result.stderr), {'verdict': 'SETUP_MISSING',
                'error': f'os/release-pipeline/frankel-factory.json is unbound for {stage}: {missing}'})
        record = str(self.tmp/'frankel-factory-package-attempt-1-1.json')
        for argv in (('observe-inputs', '--run-id', '1', '--attempt', '1', '--out', str(self.tmp/'out')),
                     ('package', '--run-id', '1', '--attempt', '1', '--attempt-record', record),
                     ('observe', '--run-id', '1', '--attempt', '1'), ('firmware-observe', '--run-id', '1', '--attempt', '1'),
                     ('classify', '--run-id', '1', '--attempt', '1'), ('stage-installer', '--run-id', '1', '--attempt', '1'),
                     ('report', '--run-id', '1', '--attempt', '1', '--upload-dir', str(self.tmp/'upload'))):
            self.assertEqual(wire(*argv).returncode, 4, argv)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), [])  # nothing written, nothing created

    def test_malformed_bindings_refuse(self):
        good = json.loads((ROOT/wiring.BINDING).read_text())
        cases = [dict(good, extra=None), {k: v for k, v in good.items() if k != 'fastbootSha256'},
                 dict(good, schemaVersion=True), dict(good, schemaVersion='1'), dict(good, schemaVersion=2),
                 dict(good, kind='roseos.rango-factory-package'), dict(good, build='202611049'),
                 dict(good, build='２０２６１１０４９９'), dict(good, build=2026110499),
                 dict(good, buildTimestamp='0793900000'), dict(good, candidate=CANDIDATE.upper()),
                 dict(good, sourceRun='0'), dict(good, signedTargetFilesSha256='a' * 63), dict(good, otatoolsSha256='A' * 64)]
        for i, data in enumerate(cases):
            root = checkout(self.tmp/str(i), raw=json.dumps(data))
            with self.subTest(case=i), self.assertRaises(wiring.Invalid):
                wiring.load_binding(root)
        duplicate = (ROOT/wiring.BINDING).read_text().replace('"build": null,', '"build": null,\n  "build": null,', 1)
        with self.assertRaisesRegex(wiring.Invalid, 'repeats a key'):
            wiring.load_binding(checkout(self.tmp/'dup', raw=duplicate))

    def test_bound_check_prints_exactly_the_stage_bounds(self):
        root = checkout(self.tmp, **dict(BOUND, otatoolsSha256=None, fastbootSha256=None))
        self.assertEqual(wiring.check('observe', root), ({'observeInputsSeconds': 1020}, 28))
        with self.assertRaisesRegex(wiring.Unbound, r'unbound for package: otatoolsSha256, fastbootSha256$'):
            wiring.check('package', root)
        root = checkout(self.tmp/'bound', **BOUND)
        bounds, minutes = wiring.check('package', root)
        self.assertEqual(bounds, {'packageSeconds': 5100, 'observeSeconds': 600, 'firmwareSeconds': 570,
                                  'classifySeconds': 7200, 'stageSeconds': 900})
        self.assertEqual(minutes, 297)
        self.assertGreaterEqual(minutes * 60, sum(bounds.values()) + 5 * 30 + wiring.INSTALLER['uploadMinutes'] * 60)
        self.assertGreater(wiring.OBSERVE_BOUNDS['observeInputsSeconds'], wiring.LIMITS['seconds'])  # the wrapper is outer
        with self.assertRaises(wiring.Invalid):
            wiring.check('signing', root)
        self.assertIn('not measurements', wiring.__doc__)


class Sources(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_every_derived_pin_comes_from_a_committed_frankel_source(self):
        self.assertEqual(wiring.sources(), {
            'production': PRODUCTION, 'releaseScript': {'path': f'{PRODUCTION}/script/generate-release.sh', 'sha256': SCRIPT_PIN},
            'generator': GENERATOR, 'lock': LOCK, 'avbPublicKeySha256': PIPELINE['identities']['avbPublicKeySha256']})
        self.assertEqual(sha(SCRIPT_FIXTURE.read_bytes()), SCRIPT_PIN)
        for name, digest in GENERATOR.items():
            self.assertEqual(sha((GENERATOR_FIXTURE/name).read_bytes()), digest, name)

    def test_otatools_is_the_copy_signing_kept_for_that_build_not_the_shared_out_dir(self):
        pins = wiring.route(checkout(self.tmp, **BOUND)).pins
        self.assertEqual(pins['otatools'], {'path': f'{PRODUCTION}/releases/{BUILD}/frankel-otatools.zip', 'sha256': 'b' * 64})
        self.assertNotIn(PIPELINE['host']['outDir'], json.dumps(pins))
        signer = (ROOT/'scripts/host-sign.py').read_text()  # where scripts/host-sign.py keeps them when it signs a build
        for line in ("    releases = Path(source_root)/'releases'/build\n", "    marker = releases/'.rose-actions-owner.json'\n",
                     "    owner = {'candidate': sha, 'build': build, 'device': device}\n",
                     "    copy_fresh(otatools, releases/f'{device}-otatools.zip')\n",
                     "SIGNED = '.rose-actions-signed.json'\n",
                     "    marker = {'candidate': sha, 'build': build, 'outputs': receipt['outputs'], 'signedTargetFilesSha256': "
                     "receipt['signedTargetFilesSha256']}\n",
                     "'releaseScriptSha256': sha256(original)"):
            self.assertIn(line, signer)
        self.assertEqual((wiring.OWNER_MARKER, wiring.SIGNED_MARKER, wiring.OTATOOLS_COPY),
                         ('.rose-actions-owner.json', '.rose-actions-signed.json', '{device}-otatools.zip'))

    def test_no_rango_or_blazer_pin_is_transplanted(self):
        rango = load('rango_factory_package_wiring', ROOT/'scripts/rango-factory-package-wiring.py')
        values = [rango.BUILD, rango.PINS['fastbootSha256'], rango.PINS['avbPkmd']['sha256'],
                  *(rango.PINS[k][f] for k in ('targetFiles', 'otatools') for f in ('path', 'sha256'))]
        texts = [WIRING.read_text(), (ROOT/wiring.BINDING).read_text(), json.dumps(wiring.route(checkout(self.tmp, **BOUND)).pins)]
        for value in values:
            for text in texts:
                self.assertNotIn(str(value), text)
        self.assertNotIn('rango', texts[2])
        self.assertNotIn('blazer', texts[2])
        self.assertNotEqual(PIPELINE['identities']['avbPublicKeySha256'], rango.PINS['avbPkmd']['sha256'])

    def test_malformed_committed_sources_refuse(self):
        for production in ('relative/root', '/home/pixelbuild/x/../y', '/home/pixelbuild/rose-actions/tree', '/home/pixelbuild'):
            with self.subTest(production=production), self.assertRaises(wiring.Invalid):
                wiring.sources(checkout(self.tmp/str(len(production)), production=production))
        for path, value in ((('provenance', 'releaseScript', 'generate-release.sh'), 'x' * 64),
                            (('inventory', 'generatorFixture', 'files', 'generate-factory-images-common.sh'), None)):
            root = checkout(self.tmp/path[0])
            flavor = json.loads((root/'os/flavors/frankel.json').read_text())
            target = flavor
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            (root/'os/flavors/frankel.json').write_text(json.dumps(flavor))
            with self.subTest(path=path), self.assertRaisesRegex(wiring.Invalid, 'pin must be a sha256'):
                wiring.sources(root)


class Route(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.route = wiring.route(checkout(self.tmp, **BOUND))
        self.parent = self.tmp/'parent'
        self.parent.mkdir()
        self.root = route_lib.task_root(self.route, '41500000000', '1', parent=self.parent)
        self.tf = f'/home/pixelbuild/rose-actions/frankel-{BUILD}/release/signed-target_files.zip'
        self.ota = f'{PRODUCTION}/releases/{BUILD}/frankel-otatools.zip'

    def test_bound_route_names_only_frankel_and_carries_the_label(self):
        r = self.route
        self.assertEqual((r.device, r.build, r.install, r.mode, r.attempt_schema, r.firmware_attempt_schema, r.cleanup_schema),
                         ('frankel', BUILD, f'frankel-install-{BUILD}.zip', PACKAGE, 'roseos.frankel-factory-package-attempt.v1',
                          'roseos.frankel-firmware-observe-attempt.v1', 'roseos.frankel-factory-package-cleanup.v1'))
        self.assertEqual(r.pins, {'targetFiles': {'path': self.tf, 'sha256': 'a' * 64},
                                  'otatools': {'path': self.ota, 'sha256': 'b' * 64},
                                  'releaseScript': {'path': f'{PRODUCTION}/script/generate-release.sh', 'sha256': SCRIPT_PIN},
                                  'fastbootSha256': 'c' * 64})
        self.assertEqual((r.label, r.installer, r.task_parent), (wiring.LABEL, wiring.INSTALLER, '/home/pixelbuild/rose-actions'))
        for phrase in ('unsigned intermediate', 'signatureVerified missing', 'not factory-ready', 'not the delivered installer'):
            self.assertIn(phrase, wiring.LABEL)
        self.assertEqual(route_lib.task_root(r, '41500000000', '2'),
                         Path('/home/pixelbuild/rose-actions/frankel-factory-package-41500000000-2'))
        for run_id, attempt in (('', '1'), ('0', '1'), ('1', '01'), ('1x', '1'), ('-1', '1')):
            with self.assertRaises(wiring.Invalid):
                route_lib.task_root(r, run_id, attempt)

    def test_exact_helper_and_inspection_calls(self):
        source = f'os-release frankel-factory-package run 41500000000 attempt 1'
        self.assertEqual(route_lib.package_argv(self.route, self.root)[1:], [str(ROOT/'scripts/host-factory-package.py'),
            '--device', 'frankel', '--build', BUILD, '--target-files', self.tf, '--target-files-sha256', 'a' * 64,
            '--otatools', self.ota, '--otatools-sha256', 'b' * 64, '--release-script', f'{PRODUCTION}/script/generate-release.sh',
            '--release-script-sha256', SCRIPT_PIN, '--fastboot-sha256', 'c' * 64, '--lock', LOCK, '--out', str(self.root/'package')])
        self.assertEqual(route_lib.observe_argv(self.route, self.root, '41500000000', '1')[1:], [str(ROOT/'scripts/factory-package.py'),
            'observe', self.tf, '--flavor', 'frankel', '--build', BUILD, '--source', source,
            '--out', str(self.root/'inspection/target-files-observation.json')])
        self.assertEqual(route_lib.firmware_argv(self.route, self.root, '41500000000', '1')[1:],
            [str(ROOT/'scripts/rango-firmware-observe.py'), '--target-files', self.tf, '--target-files-sha256', 'a' * 64,
             '--device', 'frankel', '--build', BUILD, '--source', source,
             '--out', str(self.root/'inspection/firmware-observation.json')])
        self.assertEqual(route_lib.classify_argv(self.route, self.root)[1:], [str(ROOT/'scripts/factory-package.py'), 'classify',
            str(self.root/f'package/frankel-install-{BUILD}.zip'), '--flavor', 'frankel', '--target-files', self.tf,
            '--target-files-observation', str(self.root/'inspection/target-files-observation.json'),
            '--otatools', self.ota, '--otatools-sha256', 'b' * 64, '--work-dir', str(self.root/'classify-work'), '--build', BUILD])

    def declared(self, script_path):
        """The flags a helper's main() declares, recorded through argparse alone (nothing is run)."""
        import argparse
        declared, original = {}, argparse.ArgumentParser.add_argument

        def record(parser, *flags, **kwargs):
            declared[flags[0]] = kwargs.get('required', False)
            return original(parser, *flags, **kwargs)
        helper = load(script_path.stem.replace('-', '_') + '_declared', script_path)
        argparse.ArgumentParser.add_argument = record
        try:
            with open(os.devnull, 'w') as null, contextlib.redirect_stderr(null), self.assertRaises(SystemExit):
                helper.main([])
        finally:
            argparse.ArgumentParser.add_argument = original
        return declared

    def test_wiring_argv_is_exactly_the_helpers_declared_interface(self):
        for script_path, argv in ((ROOT/'scripts/host-factory-package.py', route_lib.package_argv(self.route, self.root)),
                                  (ROOT/'scripts/rango-firmware-observe.py',
                                   route_lib.firmware_argv(self.route, self.root, '1', '1'))):
            declared = self.declared(script_path)
            passed = [a for a in argv[2:] if a.startswith('--')]
            self.assertEqual(set(passed) - set(declared), set(), script_path.name)
            self.assertEqual({f for f, required in declared.items() if required} - set(passed), set(), script_path.name)

    def test_every_record_carries_the_unsigned_intermediate_label(self):
        refusal = json.dumps({'refused': 'input_changed: otatools sha256 differs from its pin'})
        record = self.tmp/'frankel-factory-package-attempt-41500000000-1.json'
        run = lambda argv, stdout=None, **kwargs: subprocess.CompletedProcess(argv, 3, stdout=refusal)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(route_lib.package(self.route, self.root, record, run), 3)
        self.assertEqual(json.loads(record.read_text()), {'schema': 'roseos.frankel-factory-package-attempt.v1',
            'step': 'package', 'taskRoot': str(self.root), 'label': wiring.LABEL, 'exit': 3, 'category': 'input_changed',
            'detail': 'input_changed: otatools sha256 differs from its pin'})
        (self.root/'package').mkdir()
        (self.root/'inspection').mkdir()
        archive = self.root/f'package/frankel-install-{BUILD}.zip'
        archive.write_bytes(b'PK-frankel')
        (self.root/'package/factory-package-receipt.json').write_text(json.dumps({'status': 'packaged', 'output': {
            'file': archive.name, 'size': 10, 'sha256': sha(b'PK-frankel')}}))
        (self.root/'inspection/classify.json').write_text(json.dumps({'signatureVerified': False}))
        staged = route_lib.stage_installer(self.route, self.root)
        self.assertEqual(staged, {'file': f'package/frankel-install-{BUILD}.zip', 'bytes': 10, 'sha256': sha(b'PK-frankel'),
                                  'capBytes': 8 << 30, 'retentionDays': 3, 'label': wiring.LABEL})
        state = route_lib.report(self.route, self.root, self.tmp/'upload', 'success', record)
        self.assertEqual(state['label'], wiring.LABEL)
        self.assertEqual(json.loads((self.tmp/'upload/cleanup.json').read_text())['label'], wiring.LABEL)
        self.assertEqual(sorted(p.name for p in (self.tmp/'upload').iterdir()),
                         ['classify.json', 'cleanup.json', 'factory-package-receipt.json', 'package-attempt.json'])
        rango = load('rango_factory_package_wiring_label', ROOT/'scripts/rango-factory-package-wiring.py')
        self.assertIsNone(rango.ROUTE.label)  # the Rango route's records are unchanged

    def test_stage_installer_prints_the_root_and_the_bound_file_name(self):
        bound = dataclasses.replace(self.route, task_parent=str(self.parent))
        root = route_lib.task_root(bound, '41500000000', '1')
        (root/'package').mkdir(parents=True)
        (root/'inspection').mkdir()
        (root/f'package/frankel-install-{BUILD}.zip').write_bytes(b'PK')
        (root/'package/factory-package-receipt.json').write_text(json.dumps({'status': 'packaged', 'output': {
            'file': f'frankel-install-{BUILD}.zip', 'size': 2, 'sha256': sha(b'PK')}}))
        (root/'inspection/classify.json').write_text('{}')
        original = wiring.route
        wiring.route = lambda root=None, binding=None: bound
        try:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(wiring.main(['stage-installer', '--run-id', '41500000000', '--attempt', '1']), 0)
        finally:
            wiring.route = original
        self.assertEqual(out.getvalue(), f'root={root}\nfile=package/frankel-install-{BUILD}.zip\n')
        self.assertEqual(json.loads(err.getvalue())['label'], wiring.LABEL)
        self.assertEqual((root/'SHA256SUMS').read_text(), f'{sha(b"PK")}  package/frankel-install-{BUILD}.zip\n')

    def test_cli_refuses_misplaced_options(self):
        for argv in (('check',), ('check', '--stage', 'observe', '--run-id', '1'), ('check', '--stage', 'publish'),
                     ('observe-inputs', '--run-id', '1', '--attempt', '1'),
                     ('observe-inputs', '--run-id', '1', '--attempt', '1', '--out', '/x', '--upload-dir', '/y'),
                     ('observe-inputs', '--run-id', '01', '--attempt', '1', '--out', '/x'),
                     ('package', '--stage', 'package', '--run-id', '1', '--attempt', '1')):
            self.assertIn(wire(*argv).returncode, (2,), argv)


class InstallerPins(unittest.TestCase):
    def test_retained_and_signature_are_null_until_bound_with_the_inspect_key_sets(self):
        inspect = load('rango_factory_inspect_wiring_keys', ROOT/'scripts/rango-factory-inspect-wiring.py')
        self.assertEqual(set(wiring.RETAINED), set(inspect.RETAINED))
        self.assertEqual(set(wiring.SIGNATURE), set(inspect.SIGNATURE))
        self.assertEqual(set(wiring.RETAINED.values()) | set(wiring.SIGNATURE.values()), {None})


class Observe(unittest.TestCase):
    """observe-inputs on a synthetic builder (see the module docstring)."""
    RUN, ATTEMPT = '41700000001', '1'

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.production = self.tmp/'grapheneos'
        (self.production/'script').mkdir(parents=True)
        shutil.copyfile(SCRIPT_FIXTURE, self.production/'script/generate-release.sh')
        self.lock = self.tmp/'frankel-build.lock'
        self.lock.write_text('')
        self.attempts = self.tmp/'rose-actions'
        self.free_calls = []
        self.signed = self.make_attempt()
        self.otatools = self.make_release()
        self.root = checkout(self.tmp, production=self.production, lock=self.lock, **dict(UNBOUND, build=BUILD,
            buildTimestamp=STAMP, candidate=CANDIDATE, sourceRun=SOURCE_RUN, signedTargetFilesSha256=self.signed))

    def stage(self, *args):
        result = subprocess.run([sys.executable, str(ROOT/'scripts/release-stage.py'), *map(str, args)], capture_output=True,
                                text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def make_attempt(self, stages=('build', 'bytes-verify', 'sign', 'ota-verify', 'transfer'), candidate=CANDIDATE):
        base = self.attempts/f'frankel-{BUILD}'
        if base.exists():
            shutil.rmtree(base)
        (base/'release/public').mkdir(parents=True)
        self.stage('init', '--state', base/'stages', '--source-revision', candidate, '--device', 'frankel', '--build-number', BUILD)
        for name in stages:
            self.stage('run', '--state', base/'stages', '--source-revision', candidate, name, '--', 'true')
        with zipfile.ZipFile(base/'release/signed-target_files.zip', 'w') as z:
            z.writestr('SYSTEM/build.prop', f'ro.build.version.incremental={BUILD}\n')
        signed = sha((base/'release/signed-target_files.zip').read_bytes())
        self.write_json(base/'release/sign-receipt.json', {'receiptType': 'os-signing', 'candidate': CANDIDATE, 'build': BUILD,
            'releaseScriptSha256': SCRIPT_PIN, 'outputs': {}, 'signedTargetFilesSha256': signed})
        self.write_json(base/'release/public/verification.json', {'receiptType': 'candidate-verification', 'simulated': False,
            'candidate': {'sha': CANDIDATE, 'build': BUILD, 'timestamp': STAMP, 'channel': 'beta'},
            'signedTargetFilesSha256': signed, 'published': False})
        return signed

    def make_release(self, members=None, owner=None, signed_marker=True):
        release = self.production/'releases'/BUILD
        if release.exists():
            shutil.rmtree(release)
        release.mkdir(parents=True)
        self.write_json(release/'.rose-actions-owner.json', owner or {'build': BUILD, 'candidate': CANDIDATE, 'device': 'frankel'})
        if signed_marker:
            self.write_json(release/'.rose-actions-signed.json', {'candidate': CANDIDATE, 'build': BUILD, 'outputs': {},
                                                                  'signedTargetFilesSha256': self.signed})
        if members is None:
            members = [('bin/fastboot', b'\x7fELF synthetic fastboot'), ('bin/lpunpack', b'\x7fELF synthetic lpunpack'),
                       *((f'device/common/{name}', (GENERATOR_FIXTURE/name).read_bytes()) for name in GENERATOR)]
        with zipfile.ZipFile(release/'frankel-otatools.zip', 'w', zipfile.ZIP_DEFLATED) as z:
            for name, data in members:
                z.writestr(name, data) if isinstance(name, str) else z.writestr(*name, data)
        return release/'frankel-otatools.zip'

    @staticmethod
    def write_json(path, value):
        Path(path).write_text(json.dumps(value, sort_keys=True) + '\n')

    def free(self, available=200 << 30):
        def statvfs(path):
            self.free_calls.append(path)
            return types.SimpleNamespace(f_bavail=available // 4096, f_frsize=4096)
        return statvfs

    def observe(self, out='out', root=None, clock=None, available=200 << 30):
        kwargs = {'clock': clock} if clock else {}
        status = wiring.observe_inputs(self.tmp/out, self.RUN, self.ATTEMPT, root=root or self.root,
                                       attempt_parent=self.attempts, free=self.free(available), **kwargs)
        return status, json.loads((self.tmp/out/'frankel-factory-inputs.json').read_text())

    def snapshot(self):
        return {str(p): (p.read_bytes() if p.is_file() else None, os.lstat(p).st_mtime_ns)
                for base in (self.attempts, self.production) for p in sorted(base.rglob('*'))}

    def test_complete_observation_measures_every_input_and_changes_nothing(self):
        before = self.snapshot()
        status, report = self.observe()
        self.assertEqual((status, report['complete'], report['problems']), (0, True, []))
        self.assertEqual(self.snapshot(), before)  # read-only
        self.assertEqual(sorted(p.name for p in (self.tmp/'out').iterdir()), ['frankel-factory-inputs.json'])
        self.assertFalse(os.lstat(self.lock).st_size)
        ota = self.otatools.read_bytes()
        members = {f'device/common/{name}': {'sha256': digest, 'size': len((GENERATOR_FIXTURE/name).read_bytes())}
                   for name, digest in GENERATOR.items()}
        members['bin/fastboot'] = {'sha256': sha(b'\x7fELF synthetic fastboot'), 'size': 23}
        self.assertEqual(report, {
            'schema': 'roseos.frankel-factory-inputs.v1', 'device': 'frankel', 'build': BUILD, 'candidate': CANDIDATE,
            'sourceRun': SOURCE_RUN, 'buildTimestamp': STAMP, 'label': wiring.LABEL, 'limits': wiring.LIMITS,
            'source': f'os-release frankel-factory-observe run {self.RUN} attempt {self.ATTEMPT}',
            'claims': 'read-only measurements of the inputs a frankel-factory-package run would use; nothing packaged',
            'binding': {'path': wiring.BINDING, 'sha256': sha((self.root/wiring.BINDING).read_bytes())},
            'lock': {'held': True},
            'attempt': {'path': str(self.attempts/f'frankel-{BUILD}'), 'chain': 'original attempt, complete through transfer'},
            'signReceipt': {'matches': True, 'releaseScriptMatchesPin': True}, 'verification': {'matches': True},
            'signedTargetFiles': {'path': str(self.attempts/f'frankel-{BUILD}/release/signed-target_files.zip'),
                                  'sha256': self.signed, 'bytes': len((self.attempts/f'frankel-{BUILD}/release/signed-target_files.zip').read_bytes()),
                                  'matchesBinding': True},
            'releaseDir': {'path': str(self.production/'releases'/BUILD), 'owner': {'matches': True}, 'signed': {'matches': True}},
            'otatools': {'path': str(self.otatools), 'sha256': sha(ota), 'bytes': len(ota), 'members': members,
                         'fastbootSha256': sha(b'\x7fELF synthetic fastboot'),
                         'matchesBinding': {'otatoolsSha256': True, 'fastbootSha256': True}},
            'releaseScript': {'path': str(self.production/'script/generate-release.sh'), 'sha256': SCRIPT_PIN,
                              'bytes': len(SCRIPT_FIXTURE.read_bytes()), 'matchesPin': True},
            'freeBytes': {'path': '/home/pixelbuild/rose-actions', 'available': 200 << 30, 'required': 128 << 30},
            'complete': True, 'problems': []})
        self.assertEqual(self.free_calls, ['/home/pixelbuild/rose-actions'])

    def test_bound_tool_values_are_compared_with_the_measurement(self):
        measured = sha(self.otatools.read_bytes())
        root = checkout(self.tmp/'same', production=self.production, lock=self.lock, **dict(wiring.load_binding(self.root),
                        otatoolsSha256=measured, fastbootSha256=sha(b'\x7fELF synthetic fastboot')))
        self.assertEqual(self.observe('same-out', root)[0], 0)
        root = checkout(self.tmp/'other', production=self.production, lock=self.lock, **dict(wiring.load_binding(self.root),
                        otatoolsSha256='d' * 64, fastbootSha256='e' * 64))
        status, report = self.observe('other-out', root)
        self.assertEqual((status, report['problems']), (3, ['otatools: the measured value differs from the bound otatoolsSha256',
                                                            'otatools: the measured value differs from the bound fastbootSha256']))

    def mutate(self, change):
        base, release = self.attempts/f'frankel-{BUILD}/release', self.production/'releases'/BUILD
        if change == 'signed target-files bytes':
            with zipfile.ZipFile(base/'signed-target_files.zip', 'a') as z:
                z.writestr('extra', b'x')
        elif change == 'sign receipt build':
            receipt = json.loads((base/'sign-receipt.json').read_text())
            self.write_json(base/'sign-receipt.json', dict(receipt, build='2026110498'))
        elif change == 'sign receipt release script':
            receipt = json.loads((base/'sign-receipt.json').read_text())
            self.write_json(base/'sign-receipt.json', dict(receipt, releaseScriptSha256='f' * 64))
        elif change in ('verification simulated', 'verification timestamp'):
            receipt = json.loads((base/'public/verification.json').read_text())
            if change == 'verification simulated':
                receipt['simulated'] = True
            else:
                receipt['candidate']['timestamp'] = '1793900001'
            self.write_json(base/'public/verification.json', receipt)
        elif change == 'incomplete chain':
            self.make_attempt(stages=('build', 'bytes-verify', 'sign', 'ota-verify'))
        elif change == 'another candidate chain':
            self.make_attempt(candidate='d' * 40)
        elif change == 'owner marker':
            self.make_release(owner={'build': BUILD, 'candidate': CANDIDATE, 'device': 'rango'})
        elif change == 'no signed marker':
            self.make_release(signed_marker=False)
        elif change == 'no otatools copy':
            self.otatools.unlink()
        elif change == 'generator changed':
            name = sorted(GENERATOR)[0]
            self.make_release(members=[('bin/fastboot', b'\x7fELF synthetic fastboot'), (f'device/common/{name}', b'other'),
                                       *((f'device/common/{n}', (GENERATOR_FIXTURE/n).read_bytes()) for n in sorted(GENERATOR)[1:])])
        elif change == 'no fastboot':
            self.make_release(members=[(f'device/common/{n}', (GENERATOR_FIXTURE/n).read_bytes()) for n in GENERATOR])
        elif change == 'linked fastboot':
            link = zipfile.ZipInfo('bin/fastboot')
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            self.make_release(members=[((link,), b'/usr/bin/fastboot'),
                                       *((f'device/common/{n}', (GENERATOR_FIXTURE/n).read_bytes()) for n in GENERATOR)])
        elif change == 'release script changed':
            (self.production/'script/generate-release.sh').write_text('#!/bin/bash\n')
        elif change == 'linked attempt release':
            shutil.move(base, base.parent/'release-real')
            base.symlink_to(base.parent/'release-real')
        else:
            raise AssertionError(change)

    def test_every_discrepancy_is_a_reported_problem_never_a_pass(self):
        cases = (('signed target-files bytes', ['signedTargetFiles: signed-target_files.zip is not the bound']),
                 ('sign receipt build', ['signReceipt: sign-receipt.json does not name']),
                 ('sign receipt release script', ['signReceipt: the build was not signed with the os/flavors/frankel.json']),
                 ('verification simulated', ['verification: no passing, non-simulated verification receipt']),
                 ('verification timestamp', ['verification: no passing, non-simulated verification receipt']),
                 ('incomplete chain', ['attempt: the attempt has not completed']),
                 ('another candidate chain', ['attempt: the attempt receipt chain belongs to another candidate or build']),
                 ('owner marker', ['releaseDir: .rose-actions-owner.json does not name']),
                 ('no signed marker', ['releaseDir: ']),
                 ('no otatools copy', ['otatools: ']),
                 ('generator changed', ['otatools device/common/']),
                 ('no fastboot', ['otatools bin/fastboot: 0 entries (exactly one is required)']),
                 ('linked fastboot', ['otatools bin/fastboot: not a plain stored or deflated member']),
                 ('release script changed', ['releaseScript: generate-release.sh is not the os/flavors/frankel.json provenance pin']),
                 ('linked attempt release', ['signReceipt: ', 'verification: ', 'signedTargetFiles: ']))
        for i, (change, expected) in enumerate(cases):
            with self.subTest(change=change):
                self.setUp()
                self.mutate(change)
                status, report = self.observe(f'out-{i}')
                self.assertEqual((status, report['complete']), (3, False))
                self.assertEqual(len(report['problems']), len(expected), report['problems'])
                for problem, prefix in zip(report['problems'], expected):
                    self.assertTrue(problem.startswith(prefix), (problem, prefix))
        self.setUp()
        status, report = self.observe('capacity', available=(128 << 30) - 4096)
        self.assertEqual((status, report['problems']), (3, [f'capacity: {(128 << 30) - 4096} bytes free under '
                                                            f'/home/pixelbuild/rose-actions, below {128 << 30}']))

    def test_a_held_absent_or_irregular_lock_reads_nothing(self):
        def unread(report):
            return not {'attempt', 'signReceipt', 'verification', 'signedTargetFiles', 'releaseDir', 'otatools',
                        'releaseScript', 'freeBytes'} & set(report)
        with open(self.lock) as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            status, report = self.observe('held')
        self.assertEqual((status, report['lock']['held'], unread(report)), (3, False, True))
        self.assertTrue(report['lock']['reason'].startswith('lock_held: '))
        self.lock.unlink()
        status, report = self.observe('absent')
        self.assertEqual((status, report['lock']['reason'][:12], unread(report)), (3, 'lock_absent:', True))
        self.assertFalse(os.path.lexists(self.lock))  # never created
        for kind in ('fifo', 'link', 'directory'):
            if kind == 'fifo':
                os.mkfifo(self.lock)  # opening it without O_NONBLOCK would hang this test
            elif kind == 'link':
                (self.tmp/'target.lock').write_text('')
                self.lock.symlink_to(self.tmp/'target.lock')
            else:
                self.lock.mkdir()
            status, report = self.observe(f'irregular-{kind}')
            self.assertEqual((status, report['lock']['reason'][:14], unread(report)), (3, 'lock_unusable:', True), kind)
            self.lock.rmdir() if kind == 'directory' else self.lock.unlink()

    def test_the_deadline_stops_the_observation(self):
        ticks = iter([0.0] + [10.0 ** 6] * 100)
        status, report = self.observe('late', clock=lambda: next(ticks))
        self.assertEqual((status, report['complete']), (3, False))
        self.assertIn('deadline: the observation exceeded its time bound', report['problems'])

    def test_write_once_unbound_and_bad_arguments_refuse_before_writing(self):
        (self.tmp/'taken').mkdir()
        with self.assertRaises(FileExistsError):
            self.observe('taken')
        with self.assertRaises(wiring.Invalid):
            self.observe('absent-parent/out')
        unbound = checkout(self.tmp/'unbound', production=self.production, lock=self.lock)
        with self.assertRaises(wiring.Unbound):
            wiring.observe_inputs(self.tmp/'never', self.RUN, self.ATTEMPT, root=unbound, attempt_parent=self.attempts)
        with self.assertRaises(wiring.Invalid):
            wiring.observe_inputs(self.tmp/'never', '0', self.ATTEMPT, root=self.root, attempt_parent=self.attempts)
        self.assertFalse((self.tmp/'never').exists())


class Guard(unittest.TestCase):
    def run_guard(self, mode, **change):
        guard = 'set -euo pipefail\n' + script(step(jobs()['guard'], 'Candidate is an exact commit on main'))
        env = {'PATH': os.environ['PATH'], **shape.dispatch_env(WORKFLOW, MODE=mode, PROMOTE='false', APP_PUBLISH='false',
                                                                 ALPHA_RECOVER='false', GITHUB_SHA='f' * 40), **change}
        return subprocess.run(['bash', '-c', guard], env=env, capture_output=True, cwd=ROOT).returncode

    def test_whole_guard_admits_exactly_the_bare_modes(self):
        for mode in (OBSERVE, PACKAGE):
            self.assertEqual(self.run_guard(mode), 0, mode)
            for change in ({'CANDIDATE_SHA': 'a' * 40}, {'BUILD_NUMBER': BUILD}, {'BUILD_TIMESTAMP': STAMP},
                           {'APPS': '[["dev.pixel.voice",1]]'}, {'PROMOTE': 'true'}, {'RESUME_STAGE': 'sign'},
                           {'RESUME_SOURCE_RUN': '1'}, {'RESUME_UNSIGNED_SHA256': 'a' * 64}, {'RESUME_SIGNED_SHA256': 'a' * 64},
                           {'CAMPAIGN': 'start'}, {'APP_PUBLISH': 'true'}, {'ROSE_RANGO_PUBLISH': 'true'},
                           {'ROSE_FIRST_USE_ENROLL': 'true'}, {'ROSE_RECOVER_RUN': '41000000000'},
                           {'BOOTSTRAP_PROFILE': 'fold-rango-factory-v1'}, {'MODELS_BOOTSTRAP_RUN': '1'}, {'REUSE_BINDING': '{}'},
                           {'ALPHA_RECOVER': 'true'}, {'ROSE_DEVICE': 'rango'}, {'ROSE_DEVICE': 'mustang'},
                           {'BLAZER_PREP_STAGE': 'source'}, {'MUSTANG_PUBLISH_OTA': 'true'}, {'MUSTANG_COMPILE_RUN': '1'}):
                with self.subTest(mode=mode, change=change):
                    self.assertNotEqual(self.run_guard(mode, **change), 0)

    def test_modes_are_manual_and_checked_hosted_before_the_controller(self):
        self.assertIn(OBSERVE, shape.modes(WORKFLOW))
        self.assertIn(PACKAGE, shape.modes(WORKFLOW))
        self.assertEqual(re.findall(r'(?m)^  (\w+):\n', WORKFLOW.split('\npermissions:', 1)[0].split('\non:\n', 1)[1]),
                         ['workflow_dispatch'])
        guard = jobs()['guard']
        for mode, stage in ((OBSERVE, 'observe'), (PACKAGE, 'package')):
            self.assertIn(f"      - name: Frankel factory {stage} check\n        id: {mode}\n        if: inputs.mode == '{mode}'\n"
                          f"        run: python3 scripts/frankel-factory-package-wiring.py check --stage {stage} >> "
                          '"$GITHUB_OUTPUT"\n', guard)
        outputs = {'frankel_observe_inputs_seconds': (OBSERVE, 'observe_inputs_seconds'),
                   'frankel_observe_job_minutes': (OBSERVE, 'job_minutes'),
                   'frankel_package_seconds': (PACKAGE, 'package_seconds'), 'frankel_observe_seconds': (PACKAGE, 'observe_seconds'),
                   'frankel_firmware_seconds': (PACKAGE, 'firmware_seconds'),
                   'frankel_classify_seconds': (PACKAGE, 'classify_seconds'), 'frankel_stage_seconds': (PACKAGE, 'stage_seconds'),
                   'frankel_package_job_minutes': (PACKAGE, 'job_minutes')}
        for name, (step_id, key) in outputs.items():
            self.assertIn(f'      {name}: ${{{{ steps.{step_id}.outputs.{key} }}}}\n', guard)
        self.assertEqual(re.search(r'(?m)^    needs: (.+)$', jobs()['controller'])[1], 'guard')
        self.assertTrue({OBSERVE, PACKAGE} <= shape.main_check_exempt_modes(WORKFLOW))


class Jobs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def head(self, job):
        return code(job.split('    steps:\n', 1)[0])

    def test_observe_job_is_read_only_bounded_and_uploads_only_its_receipt(self):
        job = jobs()[OBSERVE]
        self.assertEqual(self.head(job), "    needs: [guard, controller]\n    if: inputs.mode == 'frankel-factory-observe'\n"
            '    runs-on: [self-hosted, roseos-frankel-builder]\n'
            "    timeout-minutes: ${{ fromJSON(needs.guard.outputs.frankel_observe_job_minutes || '1') }}\n"
            '    permissions:\n      contents: read\n    env:\n'
            '      OBSERVE_INPUTS_SECONDS: ${{ needs.guard.outputs.frankel_observe_inputs_seconds }}')
        body = code(job)
        for absent in FORBIDDEN + ('.zip',):
            self.assertNotIn(absent, body)
        self.assertEqual(sorted(set(re.findall(r'python3 (\S+)', body))), ['scripts/frankel-factory-package-wiring.py'])
        self.assertIn('          persist-credentials: false\n', job)
        self.assertEqual(re.findall(r'(?m)^      - name: (.+)$', job), ['New task directory for this attempt',
            'Recheck the binding and bounds on the builder',
            'Observe the package inputs (bounded, read-only, under the shared lock)', 'Allowlisted JSON receipt only',
            'Receipts artifact', "Remove this attempt's task directory"])
        observe = step(job, 'Observe the package inputs (bounded, read-only, under the shared lock)')
        self.assertEqual(script(observe), 'timeout --kill-after=30s "${OBSERVE_INPUTS_SECONDS}s" python3 '
            'scripts/frankel-factory-package-wiring.py observe-inputs --run-id "$GITHUB_RUN_ID" --attempt "$GITHUB_RUN_ATTEMPT" '
            '--out "$TASK_OUTPUT"\n')
        self.assertNotIn('if:', observe)
        artifact = step(job, 'Receipts artifact')
        self.assertIn('        if: always()\n', artifact)
        self.assertIn('          name: frankel-factory-observe-${{ github.run_id }}-${{ github.run_attempt }}\n'
                      '          path: ${{ env.TASK_RECEIPTS }}\n          retention-days: 7\n', artifact)
        self.assertEqual(job.count('actions/upload-artifact@'), 1)

    def test_observe_receipt_step_copies_only_the_regular_receipt(self):
        text = 'set -euo pipefail\n' + script(step(jobs()[OBSERVE], 'Allowlisted JSON receipt only'))
        task = self.tmp/'task'
        (task/'output').mkdir(parents=True)
        (task/'output/frankel-factory-inputs.json').write_text('{}')
        (task/'output/other.json').write_text('{}')
        env = {'PATH': '/usr/bin:/bin', 'TASK_ROOT': str(task), 'TASK_OUTPUT': str(task/'output'),
               'TASK_RECEIPTS': str(task/'receipts')}
        self.assertEqual(subprocess.run(['bash', '-c', text], env=env).returncode, 0)
        self.assertEqual([p.name for p in (task/'receipts').iterdir()], ['frankel-factory-inputs.json'])
        shutil.rmtree(task/'receipts')
        (task/'output/frankel-factory-inputs.json').unlink()
        (task/'output/frankel-factory-inputs.json').symlink_to(task/'output/other.json')
        self.assertEqual(subprocess.run(['bash', '-c', text], env=env).returncode, 0)
        self.assertEqual(list((task/'receipts').iterdir()), [])
        self.assertEqual(subprocess.run(['bash', '-c', text], env={'PATH': '/usr/bin:/bin'}).returncode, 0)  # no task root

    def test_package_job_runs_the_shared_steps_with_frankel_names_only(self):
        job = jobs()[PACKAGE]
        self.assertEqual(self.head(job), "    needs: [guard, controller]\n    if: inputs.mode == 'frankel-factory-package'\n"
            '    runs-on: [self-hosted, roseos-frankel-builder]\n'
            "    timeout-minutes: ${{ fromJSON(needs.guard.outputs.frankel_package_job_minutes || '1') }}\n"
            '    permissions:\n      contents: read\n    env:\n'
            '      PACKAGE_SECONDS: ${{ needs.guard.outputs.frankel_package_seconds }}\n'
            '      OBSERVE_SECONDS: ${{ needs.guard.outputs.frankel_observe_seconds }}\n'
            '      FIRMWARE_SECONDS: ${{ needs.guard.outputs.frankel_firmware_seconds }}\n'
            '      CLASSIFY_SECONDS: ${{ needs.guard.outputs.frankel_classify_seconds }}\n'
            '      STAGE_SECONDS: ${{ needs.guard.outputs.frankel_stage_seconds }}')
        body = code(job)
        for absent in FORBIDDEN + ('.zip', 'rm -rf', 'rango', 'blazer', 'mustang', '2026103001'):
            self.assertNotIn(absent, body)
        self.assertEqual(sorted(set(re.findall(r'python3 (\S+)', body))), ['scripts/frankel-factory-package-wiring.py'])
        record = ' --attempt-record "$RUNNER_TEMP/frankel-factory-package-attempt-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT.json"'
        for name, var, command, extra in (("Package the install archive (bounded, under the helper's shared lock)", 'PACKAGE',
                                           'package', record),
                                          ('Observe the signed target-files (bounded)', 'OBSERVE', 'observe', ''),
                                          ('Observe the firmware-carried partitions (bounded)', 'FIRMWARE', 'firmware-observe', ''),
                                          ('Classify the install archive (bounded)', 'CLASSIFY', 'classify', ''),
                                          ('Stage the installer for the private artifact (bounded)', 'STAGE', 'stage-installer',
                                           ' >> "$GITHUB_OUTPUT"')):
            self.assertEqual(script(step(job, name)), f'timeout --kill-after=30s "${{{var}_SECONDS}}s" python3 '
                f'scripts/frankel-factory-package-wiring.py {command} --run-id "$GITHUB_RUN_ID" --attempt "$GITHUB_RUN_ATTEMPT"{extra}\n')
            self.assertNotIn('if:', step(job, name))  # each runs only after the previous step succeeded
        artifact = step(job, 'Private installer artifact (exactly SHA256SUMS and the ZIP; an unsigned intermediate)')
        self.assertEqual(artifact, '        id: installer-artifact\n        timeout-minutes: 45\n'
            '        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2\n        with:\n'
            '          name: frankel-install-unsigned-intermediate-${{ github.run_id }}-${{ github.run_attempt }}\n'
            '          path: |\n            ${{ steps.installer.outputs.root }}/SHA256SUMS\n'
            '            ${{ steps.installer.outputs.root }}/${{ steps.installer.outputs.file }}\n'
            '          retention-days: 3\n          if-no-files-found: error\n          compression-level: 0\n')
        report = step(job, 'Cleanup report and allowlisted receipts')
        self.assertIn('        if: always()\n', report)
        self.assertIn("          INSTALLER_UPLOAD: ${{ steps.installer-artifact.outcome || 'skipped' }}\n", report)
        self.assertEqual(script(report), 'python3 scripts/frankel-factory-package-wiring.py report --run-id "$GITHUB_RUN_ID" '
            '--attempt "$GITHUB_RUN_ATTEMPT" --upload-dir "$RUNNER_TEMP/frankel-factory-package-upload-$GITHUB_RUN_ID-'
            '$GITHUB_RUN_ATTEMPT" --installer-upload "$INSTALLER_UPLOAD"' + record + '\n')
        receipts = step(job, 'Receipts artifact')
        self.assertIn('        if: always()\n', receipts)
        self.assertIn('          name: frankel-factory-package-${{ github.run_id }}-${{ github.run_attempt }}\n'
                      '          path: ${{ runner.temp }}/frankel-factory-package-upload-${{ github.run_id }}-'
                      '${{ github.run_attempt }}\n          retention-days: 7\n', receipts)
        self.assertEqual(job.count('actions/upload-artifact@'), 2)
        self.assertEqual([n for n, b in jobs().items() if 'frankel-factory-package-wiring.py package' in b], [PACKAGE])
        self.assertEqual([n for n, b in jobs().items() if 'frankel-factory-package-wiring.py observe-inputs' in b], [OBSERVE])

    def recheck(self, mode, name, good, bounds, **change):
        bin_dir = self.tmp/'bin'
        bin_dir.mkdir(exist_ok=True)
        (bin_dir/'python3').write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$ARGS_FILE"\nprintf "%s\\n" "$CHECK_OUTPUT"\n')
        (bin_dir/'python3').chmod(0o755)
        task = self.tmp/'task'
        task.mkdir(exist_ok=True)
        env = {'PATH': f'{bin_dir}:/usr/bin:/bin', 'RUNNER_TEMP': str(self.tmp), 'TASK_ROOT': str(task), 'CHECK_OUTPUT': good,
               'ARGS_FILE': str(self.tmp/'args'), **bounds, **change}
        text = 'set -euo pipefail\n' + script(step(jobs()[mode], name))
        return subprocess.run(['bash', '-c', text], env=env, capture_output=True).returncode

    def test_recheck_on_the_builder_must_match_the_guard_bounds(self):
        good = 'observe_inputs_seconds=1020\njob_minutes=28'
        name = 'Recheck the binding and bounds on the builder'
        bounds = {'OBSERVE_INPUTS_SECONDS': '1020'}
        self.assertEqual(self.recheck(OBSERVE, name, good, bounds), 0)
        self.assertEqual((self.tmp/'args').read_text(), 'scripts/frankel-factory-package-wiring.py check --stage observe\n')
        for change in ({'OBSERVE_INPUTS_SECONDS': '900'}, {'OBSERVE_INPUTS_SECONDS': ''}, {'OBSERVE_INPUTS_SECONDS': '1020s'},
                       {'CHECK_OUTPUT': good.replace('1020', '900')}):
            self.assertNotEqual(self.recheck(OBSERVE, name, good, bounds, **change), 0, change)
        good = ('package_seconds=5100\nobserve_seconds=600\nfirmware_seconds=570\nclassify_seconds=7200\nstage_seconds=900\n'
                'job_minutes=297')
        name = 'Recheck the binding, pins and bounds on the builder'
        bounds = {'PACKAGE_SECONDS': '5100', 'OBSERVE_SECONDS': '600', 'FIRMWARE_SECONDS': '570', 'CLASSIFY_SECONDS': '7200',
                  'STAGE_SECONDS': '900'}
        self.assertEqual(self.recheck(PACKAGE, name, good, bounds), 0)
        self.assertEqual((self.tmp/'args').read_text(), 'scripts/frankel-factory-package-wiring.py check --stage package\n')
        for change in ({'PACKAGE_SECONDS': '3600'}, {'CLASSIFY_SECONDS': ''}, {'STAGE_SECONDS': '60'}, {'OBSERVE_SECONDS': '900s'},
                       {'CHECK_OUTPUT': good.replace('7200', '3600')}):
            self.assertNotEqual(self.recheck(PACKAGE, name, good, bounds, **change), 0, change)

    def test_both_modes_own_their_builder_start_and_join_cleanup(self):
        # owned_modes() reads every ownership condition (plain/owned start, ownership record, plain/owned stop) and
        # requires them to agree.
        self.assertTrue({OBSERVE, PACKAGE} <= shape.owned_modes(WORKFLOW))
        self.assertTrue({OBSERVE, PACKAGE} <= set(shape.cleanup_needs(WORKFLOW)))


if __name__ == '__main__':
    unittest.main()
