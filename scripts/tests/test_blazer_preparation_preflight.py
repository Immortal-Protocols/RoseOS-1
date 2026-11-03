"""OCT29-INFRA-S004: scripts/blazer-preparation-preflight.py, the pure Blazer preparation admission report.

The positive fixture below is explicitly synthetic: it is not a real host pass and can only reach ADMISSIBLE_SYNTHETIC.
The retained fixture copies the fields the converter reads from the sanitized EXEC002 device-readiness receipt (run
40829906632, recipe 2f58260f, receipt sha256 3bdc2c30...) and must stay BLOCKED with individual reasons. Each negative
case changes one fact of the synthetic pass; mutation controls remove a guard from a copy of the module and show the
matching case would then be admitted, so the guards are load-bearing. Offline; no process, network or host access."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import contextlib
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
MODULE = SCRIPTS/'blazer-preparation-preflight.py'


def load(source=None):
    spec = importlib.util.spec_from_file_location('blazer_preparation_preflight', MODULE)
    module = importlib.util.module_from_spec(spec)
    if source is None:
        spec.loader.exec_module(module)
    else:
        exec(compile(source, str(MODULE), 'exec'), module.__dict__)
    return module


pre = load()
INPUTS = pre.load_inputs()
CONTRACT = INPUTS['contract']
PINS = CONTRACT['source']['projects']
PINNED_SHA = hashlib.sha256((ROOT/'os/pinned-manifest.xml').read_bytes()).hexdigest()
PRODUCTION = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())['host']['sourceRoot']
SOURCE_ROOT = '/home/pixelbuild/blazer-prep-work'  # an isolated work tree, never the production source root
PEAK = sum(json.loads((ROOT/'os/build-only/blazer.json').read_text())['preparation'][s]['boundBytes'] for s in ('download', 'vendor', 'lunch'))
FACTORY = CONTRACT['devices']['blazer']['vendorInputs']['factoryImage']
RECIPE = 'a' * 40
NOW = '2026-10-29T19:30:00Z'
OID = 'synthetic-0001'
GiB = 1 << 30


def synthetic():
    """A synthetic, fully satisfied observation document. Not a real host pass."""
    return {
        'schema': pre.SCHEMA, 'synthetic': True, 'device': 'blazer',
        'provenance': {'kind': 'synthetic-fixture', 'note': 'it is not a real host pass'},
        'operation': {'deviceArgs': ['blazer'], 'allDevices': False, 'recipeSha': RECIPE, 'sourceRoot': SOURCE_ROOT},
        'source': {'observationId': OID, 'observedAt': '2026-10-29T19:10:00Z', 'recipeSha': RECIPE,
            'sourceRoot': SOURCE_ROOT, 'hashes': {'contractSha256': INPUTS['contractSha256'], 'receiptSha256': 'b' * 64}},
        'pins': {'observationId': OID, 'vendor/adevtool': PINS['vendor/adevtool'], 'vendor/state': PINS['vendor/state']},
        'factory': {'observationId': OID, 'status': 'present', 'file': FACTORY['file'], 'sha256': FACTORY['sha256']},
        'adevtoolDeps': {'observationId': OID, 'revision': PINS['vendor/adevtool'],
            'tools': {t: {'status': 'present', 'executable': True, 'usability': 'demonstrated'} for t in pre.HOST_TOOLS}},
        'node': {'observationId': OID, 'location': 'synthetic/bin/node', 'version': 'v24.1.0'},
        'paths': {'observationId': OID, 'vendorDestination': {'status': 'absent'}, 'archiveTmp': {'status': 'absent'},
            'unpack': {'status': 'absent'}, 'unpackFinalTemp': {'status': 'absent'}},
        'manifest': {'observationId': OID, 'sha256': PINNED_SHA, 'pinnedSha256': PINNED_SHA, 'comparison': 'match'},
        'resolvedManifest': None,
        'space': {'observationId': OID,
            'filesystem': {'queriedPath': SOURCE_ROOT, 'availableBytes': 500 * GiB, 'freeBytes': 600 * GiB},
            'estimate': {'kind': 'blazer', 'complete': True, 'bounded': True, 'transientBytes': 40 * GiB,
                'collisionBytes': 0}}}


def resolved(obs):
    """Synthetic 500-byte wrapper case: raw mismatch plus a byte-identical single include."""
    obs['manifest'].update(sha256='3' * 64, comparison='mismatch')
    obs['resolvedManifest'] = {'observationId': OID, 'wrapperSha256': '3' * 64, 'otherElements': [],
        'includes': [{'name': 'default.xml', 'sha256': PINNED_SHA}], 'projects': dict(PINS)}
    return obs


def codes(report):
    return [r['code'] for r in report['reasons']]


def assess(obs, now=NOW, module=pre):
    return module.assess(obs, module.load_inputs(), now)


# Fields copied from the retained sanitized EXEC002 receipt (sha256 below); only what the converter reads.
RETAINED_SHA = '3bdc2c304446ce24c12d5652c3ac42e5008df6c88866bd04b48c952415cc7845'
RETAINED = {
    'attempt': '40829906632-1', 'at': '2026-10-29T06:35:44Z', 'recipeSha': '2f58260fbebea527bb78e270d110ae0010e2e71b',
    'sourceRoot': '/home/pixelbuild/grapheneos-2026100600',
    'contract': {'sha256': 'f1f16f27664796cde119551a8952af38053f25c85c5df3b46024d699b1b10c80'},
    'pins': {p: {'observed': PINS[p], 'matches': True} for p in ('vendor/adevtool', 'vendor/state')},
    'factory': {'blazer': {'status': 'missing', 'checksum': 'notValidated', 'expectedSha256': FACTORY['sha256'],
        'path': 'vendor/adevtool/dl/blazer-cp3a.261005.005-factory-cd7d12c2.zip'}},
    'adevtoolDeps': {'revision': {'observed': PINS['vendor/adevtool'], 'comparison': 'match'},
        'tools': {t: {'status': 'present', 'executable': True} for t in pre.HOST_TOOLS}},
    'prerequisites': {'node': {'location': 'unknown', 'version': 'unknown'}},
    'blazerPaths': {'vendorDestination': {'status': 'absent'}, 'archive': {'status': 'absent'},
        'archiveTmp': {'status': 'absent'}, 'unpack': {'status': 'absent'}, 'unpackFinalTemp': {'status': 'unknown'}},
    'manifest': {'sha256': '33d794c99c197c2eb0726ff2cc8c4bcd532e0f3fcd75eaea3fbd10bd53675d25', 'sizeBytes': 500,
        'pinnedSha256': '32d30e70ee59d373a17fe8090bdded898afa80651f27c331caca4578b3008888', 'comparison': 'mismatch'},
    'filesystem': {'availableBytes': 340356505600, 'freeBytes': 428518408192, 'totalBytes': 2166358695936,
        'mountPoint': '/', 'type': 'ext4'},
    'capacity': {'allocation': {'frankelUnpack': {'allocatedBytes': 9753948160},
        'frankelGeneratedVendor': {'allocatedBytes': 1819140096}}}}


class Positive(unittest.TestCase):
    def test_synthetic_pass_is_never_a_real_pass(self):
        report = assess(synthetic())
        self.assertEqual(report['verdict'], 'ADMISSIBLE_SYNTHETIC', report['reasons'])
        self.assertEqual(report['reasons'], [])
        self.assertIn('synthetic fixture: this is not a real host pass', report['notes'])
        self.assertEqual(report['space']['fullBuildAllocation'], 'unknown')
        self.assertEqual(report['tools'], {'expected': 14, 'executableMetadata': 14, 'usabilityDemonstrated': 14})
        missing = synthetic()
        del missing['synthetic']
        self.assertEqual(assess(missing)['verdict'], 'ADMISSIBLE_SYNTHETIC')

    def test_resolved_manifest_with_one_byte_identical_include(self):
        report = assess(resolved(synthetic()))
        self.assertEqual(report['verdict'], 'ADMISSIBLE_SYNTHETIC', report['reasons'])
        self.assertEqual(report['manifest']['resolved'], 'byte-identical-include')
        self.assertEqual(report['manifest']['comparison'], 'mismatch')
        self.assertTrue(any('not a claim of host semantic equivalence' in n for n in report['notes']))

    def test_pinned_manifest_parser_resolves_contract_projects(self):
        projects = pre.manifest_projects((ROOT/'os/pinned-manifest.xml').read_bytes())
        for path, revision in PINS.items():
            self.assertEqual(projects[path]['revision'], revision, path)
        self.assertEqual(projects['vendor/adevtool']['remote'], 'grapheneos')
        self.assertEqual(projects['vendor/state']['name'], 'vendor_state')
        self.assertNotIn('<include', (ROOT/'os/pinned-manifest.xml').read_text())


class Negative(unittest.TestCase):
    def blocked(self, obs, *expected, now=NOW):
        report = assess(obs, now)
        self.assertEqual(report['verdict'], 'BLOCKED')
        for code in expected:
            self.assertIn(code, codes(report))
        return report

    def test_device(self):
        obs = synthetic(); obs['device'] = 'frankel'
        self.blocked(obs, 'device-wrong')
        obs = synthetic(); del obs['device']
        self.assertIn('device-missing', self.blocked(obs, 'device-missing')['unknowns'])

    def test_operation_requires_explicit_d_blazer(self):
        obs = synthetic(); obs['operation']['deviceArgs'] = []
        self.blocked(obs, 'operation-device-arg')
        obs = synthetic(); del obs['operation']['deviceArgs']
        self.blocked(obs, 'operation-device-arg')
        obs = synthetic(); obs['operation']['deviceArgs'] = ['blazer', 'frankel']
        self.blocked(obs, 'operation-device-arg')
        obs = synthetic(); obs['operation']['allDevices'] = True
        self.blocked(obs, 'operation-all-devices')
        obs = synthetic(); del obs['operation']['allDevices']
        self.blocked(obs, 'operation-all-devices')

    def test_pins_dependency_revision_and_factory_digest(self):
        obs = synthetic(); obs['pins']['vendor/adevtool'] = 'c' * 40
        self.blocked(obs, 'pin-mismatch')
        obs = synthetic(); del obs['pins']['vendor/state']
        self.blocked(obs, 'pin-unknown')
        obs = synthetic(); obs['adevtoolDeps']['revision'] = PINS['vendor/state']
        self.blocked(obs, 'deps-revision-mismatch')
        obs = synthetic(); obs['factory']['sha256'] = 'd' * 64
        self.blocked(obs, 'factory-digest-mismatch')
        obs = synthetic(); obs['factory']['file'] = 'frankel-cp3a.261005.005-factory-3721e6ac.zip'
        self.blocked(obs, 'factory-digest-mismatch')
        obs = synthetic(); obs['factory'] = {'observationId': OID, 'status': 'missing'}
        self.blocked(obs, 'factory-absent')

    def test_unknown_node_usability_and_final_temp_block(self):
        obs = synthetic(); obs['node'].update(location='unknown', version='unknown')
        self.assertIn('node-unknown', self.blocked(obs, 'node-unknown')['unknowns'])
        obs = synthetic(); obs['node']['version'] = 'v22.9.0'
        self.blocked(obs, 'node-version')
        obs = synthetic(); obs['adevtoolDeps']['tools']['lpunpack']['usability'] = 'unknown'
        report = self.blocked(obs, 'tool-usability-unknown')
        self.assertEqual(report['tools'], {'expected': 14, 'executableMetadata': 14, 'usabilityDemonstrated': 13})
        obs = synthetic(); obs['adevtoolDeps']['tools']['debugfs']['executable'] = False
        self.blocked(obs, 'tool-not-executable')
        obs = synthetic(); del obs['adevtoolDeps']['tools']['toybox']
        self.blocked(obs, 'tool-unknown')
        obs = synthetic(); obs['adevtoolDeps']['tools']['curl'] = {'status': 'present', 'executable': True}
        self.blocked(obs, 'tools-unexpected')
        obs = synthetic(); obs['paths']['unpackFinalTemp'] = {'status': 'unknown'}
        self.blocked(obs, 'collision-unknown')
        obs = synthetic(); del obs['paths']['archiveTmp']
        self.blocked(obs, 'collision-unknown')

    def test_occupied_destination(self):
        for name in pre.COLLISION_PATHS:
            obs = synthetic(); obs['paths'][name] = {'status': 'present'}
            self.blocked(obs, 'collision-occupied')

    def test_stale_or_inconsistent_source_binding(self):
        self.blocked(synthetic(), 'binding-stale', now='2026-10-29T20:10:01Z')
        self.blocked(synthetic(), 'binding-future', now='2026-10-29T19:09:59Z')
        obs = synthetic(); obs['factory']['observationId'] = 'other'
        self.blocked(obs, 'binding-inconsistent')
        obs = synthetic(); obs['operation']['recipeSha'] = 'e' * 40
        self.blocked(obs, 'binding-recipe')
        obs = synthetic(); obs['source']['recipeSha'] = 'short'
        self.blocked(obs, 'binding-recipe')
        obs = synthetic(); obs['source']['sourceRoot'] = '/home/pixelbuild/other'
        self.blocked(obs, 'binding-source-root')
        for root in (PRODUCTION, PRODUCTION + '/nested', '/home/pixelbuild', '/'):
            obs = synthetic(); obs['source']['sourceRoot'] = obs['operation']['sourceRoot'] = root
            obs['space']['filesystem']['queriedPath'] = root
            self.blocked(obs, 'source-root-production')
        obs = synthetic(); obs['source']['hashes']['contractSha256'] = 'f' * 64
        self.blocked(obs, 'binding-contract')
        obs = synthetic(); del obs['source']['observedAt']
        self.blocked(obs, 'binding-time')
        obs = synthetic(); obs['manifest']['pinnedSha256'] = 'a' * 64
        self.blocked(obs, 'manifest-pin-stale')

    def test_manifest_mismatch_versus_unavailable(self):
        obs = synthetic(); obs['manifest'].update(sha256='3' * 64, comparison='mismatch')
        mismatch = self.blocked(obs, 'manifest-mismatch')
        self.assertNotIn('manifest-unavailable', codes(mismatch))
        self.assertIn('.repo/manifests/<name>', mismatch['reasons'][0]['detail'])
        self.assertEqual(mismatch['manifest']['observedSha256'], '3' * 64)
        self.assertEqual(mismatch['manifest']['pinnedSha256'], PINNED_SHA)
        obs = synthetic(); obs['manifest'] = {'observationId': OID, 'comparison': 'unavailable', 'pinnedSha256': PINNED_SHA}
        unavailable = self.blocked(obs, 'manifest-unavailable')
        self.assertNotIn('manifest-mismatch', codes(unavailable))
        obs = synthetic(); obs['manifest']['sha256'] = '3' * 64
        self.blocked(obs, 'manifest-mismatch')

    def test_resolved_manifest_rejections(self):
        def case(change):
            obs = resolved(synthetic()); change(obs['resolvedManifest'])
            report = self.blocked(obs, 'manifest-resolved')
            self.assertEqual(report['manifest']['resolved'], 'rejected')
        case(lambda r: r['includes'].append({'name': 'extra.xml', 'sha256': PINNED_SHA}))
        case(lambda r: r['includes'][0].update(sha256='9' * 64))
        case(lambda r: r['includes'][0].update(name='../escape.xml'))
        case(lambda r: r.update(otherElements=['remove-project vendor/adevtool']))
        case(lambda r: r.update(otherElements=None))
        case(lambda r: r['projects'].update({'build/make': '0' * 40}))
        case(lambda r: r.update(wrapperSha256='4' * 64))
        case(lambda r: r.update(observationId='other'))

    def test_available_free_confusion_and_filesystem(self):
        obs = synthetic(); del obs['space']['filesystem']['availableBytes']
        self.blocked(obs, 'space-free-not-available')
        obs = synthetic(); obs['space']['filesystem'] = {'queriedPath': SOURCE_ROOT, 'totalBytes': 2100 * GiB}
        self.blocked(obs, 'space-available-unknown')
        obs = synthetic(); obs['space']['filesystem']['queriedPath'] = '/'
        self.blocked(obs, 'space-filesystem')
        obs = synthetic(); obs['space']['filesystem'] = {'queriedPath': SOURCE_ROOT, 'availableBytes': True}
        self.blocked(obs, 'space-available-unknown')

    def test_reserve_and_api_disk_size(self):
        self.assertEqual(PEAK, 136 * GiB + 11362467847)  # vendor 128 + lunch 8 GiB + the exact factory archive size
        obs = synthetic(); obs['space']['filesystem']['availableBytes'] = pre.RESERVE_FLOOR_BYTES + PEAK - 1
        report = self.blocked(obs, 'space-reserve')
        self.assertEqual((report['space']['peakBytes'], report['space']['peakKind']), (PEAK, 'proposal'))
        self.assertEqual(report['space']['marginBytes'], pre.RESERVE_FLOOR_BYTES - 1)
        obs['space']['filesystem']['availableBytes'] += 1
        self.assertEqual(assess(obs)['verdict'], 'ADMISSIBLE_SYNTHETIC')
        obs = synthetic(); obs['space']['filesystem']['availableBytes'] = 200 * GiB
        obs['space']['apiDiskSizeGiB'] = 2100
        report = self.blocked(obs, 'space-reserve')
        self.assertIn('provider disk size ignored: it is not filesystem or free-space evidence', report['notes'])
        self.assertEqual(pre.RESERVE_FLOOR_BYTES, 322122547200)

    def test_no_prior_blazer_measurement_is_required(self):
        for estimate in ({'kind': 'frankel-proxy', 'complete': True, 'bounded': True, 'transientBytes': 9753948160},
                         {'kind': 'blazer', 'complete': False}, None):
            obs = synthetic()
            if estimate is None:
                del obs['space']['estimate']
            else:
                obs['space']['estimate'] = estimate
            report = assess(obs)
            self.assertEqual(report['verdict'], 'ADMISSIBLE_SYNTHETIC', estimate)
            self.assertEqual(report['space']['peakBytes'], PEAK)
            self.assertNotIn('space-estimate', ' '.join(codes(report)))
            if estimate:
                self.assertIn('not a prior measurement', ' '.join(report['notes']))

    def test_invalid_input(self):
        with self.assertRaises(pre.InvalidInput):
            assess({'schema': 'other'})
        with self.assertRaises(pre.InvalidInput):
            assess(synthetic(), now='yesterday')

    def test_real_flag_reaches_admissible_only_when_explicitly_false(self):
        obs = synthetic(); obs['synthetic'] = False
        self.assertEqual(assess(obs)['verdict'], 'ADMISSIBLE')
        obs['synthetic'] = 'no'
        self.assertEqual(assess(obs)['verdict'], 'ADMISSIBLE_SYNTHETIC')


class Retained(unittest.TestCase):
    def test_retained_exec002_receipt_stays_blocked_with_individual_reasons(self):
        obs = pre.observations_from_readiness_receipt(copy.deepcopy(RETAINED), RETAINED_SHA)
        self.assertFalse(obs['synthetic'])
        report = assess(obs, now='2026-10-29T06:40:00Z')
        self.assertEqual(report['verdict'], 'BLOCKED')
        got = set(codes(report))
        # binding-contract: the retained receipt names the contract as it was then; os/devices/pixel10.json has since
        # declared packages/apps/Launcher3 (#156), so the old binding is truthfully stale. Its hash is not rebound.
        for code in ('operation-all-devices', 'operation-device-arg', 'binding-recipe', 'binding-source-root',
                'factory-absent', 'collision-unknown', 'tool-usability-unknown', 'node-unknown', 'manifest-mismatch',
                'source-root-production', 'space-reserve', 'binding-contract'):
            self.assertIn(code, got)
        for code in ('pin-mismatch', 'pin-unknown', 'deps-revision-mismatch', 'collision-occupied',
                'binding-inconsistent', 'space-free-not-available', 'tool-not-executable', 'manifest-pin-stale'):
            self.assertNotIn(code, got)
        self.assertEqual(report['tools'], {'expected': 14, 'executableMetadata': 14, 'usabilityDemonstrated': 0})
        self.assertEqual(report['observation']['observedAt'], '2026-10-29T06:35:44Z')
        self.assertEqual(report['observation']['hashes']['receiptSha256'], RETAINED_SHA)
        self.assertEqual(report['manifest']['observedSha256'], RETAINED['manifest']['sha256'])
        self.assertEqual(report['space']['availableBytes'], 340356505600)
        self.assertEqual(report['space']['fullBuildAllocation'], 'unknown')
        self.assertEqual(report['space']['peakBytes'], PEAK)
        self.assertNotIn('space-estimate-proxy', got)

    def test_retained_receipt_with_operation_is_still_blocked_and_stale_later(self):
        op = {'deviceArgs': ['blazer'], 'allDevices': False, 'recipeSha': RETAINED['recipeSha'], 'sourceRoot': SOURCE_ROOT}
        obs = pre.observations_from_readiness_receipt(copy.deepcopy(RETAINED), RETAINED_SHA, op)
        report = assess(obs, now=NOW)
        self.assertEqual(report['verdict'], 'BLOCKED')
        self.assertIn('binding-stale', codes(report))
        self.assertNotIn('operation-device-arg', codes(report))
        self.assertNotIn('binding-recipe', codes(report))
        # The retained receipt observed the production tree: preparation never runs there, and its headroom does not
        # cover the proposal peak plus the reserve.
        self.assertGreater(RETAINED['filesystem']['availableBytes'], pre.RESERVE_FLOOR_BYTES)
        self.assertIn('source-root-production', codes(report))
        self.assertIn('space-reserve', codes(report))


class Cli(unittest.TestCase):
    def run_cli(self, obs, now=NOW):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'obs.json'
            path.write_text(json.dumps(obs))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = pre.main(['--observations', str(path), '--now', now])
        return code, json.loads(out.getvalue())

    def test_exit_codes(self):
        self.assertEqual(self.run_cli(synthetic())[0], 0)
        obs = synthetic(); obs['device'] = 'rango'
        code, report = self.run_cli(obs)
        self.assertEqual((code, report['verdict']), (1, 'BLOCKED'))
        code, report = self.run_cli({'schema': 'x'})
        self.assertEqual((code, report['verdict']), (2, 'INVALID'))

    def test_module_is_pure(self):
        text = MODULE.read_text()
        for forbidden in ('subprocess', 'socket', 'urllib', 'http.client', 'os.system', 'os.popen', 'os.walk', 'os.stat',
                'os.statvfs', 'shutil', 'requests'):
            self.assertNotIn(forbidden, text)


# Mutation controls: (guard text, replacement, case that must then be wrongly admitted).
def _device(o): o['device'] = 'frankel'
def _alldev(o): o['operation'].update(allDevices=True)
def _darg(o): o['operation'].update(deviceArgs=[])
def _pin(o): o['pins']['vendor/state'] = 'c' * 40
def _factory(o): o['factory']['sha256'] = 'd' * 64
def _usable(o): o['adevtoolDeps']['tools']['aapt2']['usability'] = 'unknown'
def _node(o): o['node'].update(location='unknown')
def _temp(o): o['paths']['unpackFinalTemp'] = {'status': 'unknown'}
def _occupied(o): o['paths']['vendorDestination'] = {'status': 'present'}
def _stale(o): o['source']['observedAt'] = '2026-10-29T06:35:44Z'
def _manifest(o): o['manifest'].update(sha256='3' * 64, comparison='mismatch')
def _include(o): resolved(o)['resolvedManifest']['includes'][0]['sha256'] = '9' * 64
def _free(o): del o['space']['filesystem']['availableBytes']
def _reserve(o): o['space']['filesystem']['availableBytes'] = 300 * GiB
def _production(o):
    o['source']['sourceRoot'] = o['operation']['sourceRoot'] = o['space']['filesystem']['queriedPath'] = PRODUCTION


MUTATIONS = [
    ("elif obs.get('device') != DEVICE:", "elif False:", _device),
    ("if op.get('allDevices') is not False:", "if False:", _alldev),
    ("if args != [DEVICE]:", "if False:", _darg),
    ("elif value != pins[path] or", "elif False and", _pin),
    ("elif factory.get('file') != expected.get('file') or", "elif False and", _factory),
    ("if tool.get('usability') == 'demonstrated':", "if True:", _usable),
    ("if node.get('location') in (None, 'unknown') or version in (None, 'unknown'):", "if False:", _node),
    ("if state == 'unknown':", "if state == 'unknown':\n            continue\n        if False:", _temp),
    ("elif state != 'absent':", "elif False:", _occupied),
    ("elif age > MAX_OBSERVATION_AGE_SECONDS:", "elif False:", _stale),
    ("    elif resolved is None:\n", "    elif resolved is None:\n        pass\n    elif False:\n", _manifest),
    ("if inc.get('sha256') != pinned_sha:", "if False:", _include),
    ("available = fs.get('availableBytes')", "available = fs.get('availableBytes', fs.get('freeBytes'))", _free),
    ("if margin < RESERVE_FLOOR_BYTES:", "if False:", _reserve),
    ("if not isolated(work_root, inputs['productionSourceRoot']):", "if False:", _production),
]


class MutationControls(unittest.TestCase):
    def test_each_guard_is_load_bearing(self):
        source = MODULE.read_text()
        for needle, replacement, change in MUTATIONS:
            with self.subTest(guard=needle):
                self.assertEqual(source.count(needle), 1, needle)
                obs = synthetic(); change(obs)
                self.assertEqual(assess(obs)['verdict'], 'BLOCKED')
                mutated = load(source.replace(needle, replacement))
                self.assertEqual(assess(copy.deepcopy(obs), module=mutated)['verdict'], 'ADMISSIBLE_SYNTHETIC',
                    f'mutation did not admit: {needle}')

    def test_synthetic_cap_is_load_bearing(self):
        source = MODULE.read_text()
        needle = "elif obs.get('synthetic') is not False:"
        self.assertEqual(source.count(needle), 1)
        mutated = load(source.replace(needle, 'elif False:'))
        self.assertEqual(assess(synthetic(), module=mutated)['verdict'], 'ADMISSIBLE')


if __name__ == '__main__':
    unittest.main()
