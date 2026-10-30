"""INFRA-ALPHA-120: alpha-export.py and alpha-runtime.py with explicitly SYNTHETIC fixtures only. No GitHub, cloud,
builder, Cuttlefish or adb is contacted. The GitHub API, the emulator-alpha module, gcloud and the runtime host (KVM,
processes, launch_cvd, adb, rose-verify) are stand-ins; the receipts, images and host package are small files whose
pins replace the measured ones. Nothing here is live acceptance or a boot result."""
import copy
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
import tarfile
import tempfile
import types
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS/file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

exporter = load('alpha_export_test', 'alpha-export.py')
runtime = load('alpha_runtime_test', 'alpha-runtime.py')
verify = load('rose_verify_test', 'rose-verify.py')
CONFIG = json.loads((ROOT/'os/release-pipeline/alpha-runtime.json').read_text())
ALPHA = json.loads((ROOT/'os/release-pipeline/cuttlefish-alpha.json').read_text())
SIGNER = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())['identities']['appSignerSha256']
RELEASE_WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
RUNTIME_WORKFLOW = (ROOT/'.github/workflows/alpha-runtime.yml').read_text()
SHA, APPS = CONFIG['originalCompile']['product'], CONFIG['originalCompile']['apps']
REPO = CONFIG['repository']
SUPER = b'synthetic super image'
APP_SHA = {'dev.pixel.voice': '1' * 64, 'dev.pixel.calculator.agent': '2' * 64, 'dev.pixel.backgroundcontrol': '3' * 64}

def sha(data):
    return hashlib.sha256(data).hexdigest()

def jobs(text):
    body = text.split('\njobs:\n', 1)[1]
    return {m[1]: m[2] for m in re.finditer(r'^  ([\w-]+):\n((?:(?:    .*)?\n)*)', body, re.M)}

def host_package(path, extra=None):
    with tarfile.open(path, 'w:gz') as tar:
        for name in ('bin/launch_cvd', 'bin/stop_cvd', 'bin/adb'):
            data = b'#!/bin/sh\nexit 0\n'
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
        for info, data in (extra or []):
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return path

class Fixture(unittest.TestCase):
    """A synthetic retained tree, the original verification receipt that measured it and a test config pinned to it."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='alpha-runtime-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        lunch = ALPHA['hostEvidence']['lunch']
        self.product_out, self.host_out = self.tmp/'tree'/lunch['PRODUCT_OUT'], self.tmp/'tree'/lunch['HOST_OUT']
        self.product_out.mkdir(parents=True)
        self.host_out.mkdir(parents=True)
        self.images = {'super.img': SUPER, 'boot.img': b'boot', 'vendor_boot.img': b'vendor boot', 'vbmeta.img': b'vbmeta', 'userdata.img': b'user'}
        for name, data in self.images.items():
            (self.product_out/name).write_bytes(data)
        (self.product_out/'android-info.txt').write_text('require board=vsoc_x86_64\n')  # never exported: not measured
        host_package(self.host_out/'cvd-host_package.tar.gz')
        self.config = copy.deepcopy(CONFIG)
        self.config['superImage'].update(size=len(SUPER), sha256=sha(SUPER))
        self.receipt = self.original_receipt()
        self.received = self.receipt_dir(self.receipt)

    def original_receipt(self):
        host = (self.host_out/'cvd-host_package.tar.gz').read_bytes()
        return {'receiptType': 'emulator-alpha-retained-verification', 'verdict': 'PASS', 'error': None,
            'verificationRun': '40777816699', 'verifierRecipe': CONFIG['retainedVerification']['recipe'],
            'originalCompile': {'runId': '40765631053', 'emulatorBuildId': 'rose-cf-alpha-40765631053', 'buildDatetime': '1793220134',
                'buildReceipt': None, 'note': 'no build receipt'},
            'candidate': SHA, 'lunch': ALPHA['hostEvidence']['lunch'], 'executedMake': False, 'signed': False, 'published': False,
            'testOnly': True, 'bootAttempted': False, 'bootVerified': False, 'imageVerified': True,
            'preimage': {'size': len(SUPER), 'sha256': sha(SUPER), 'matched': True},
            'images': {n: {'size': len(d), 'sha256': sha(d)} for n, d in self.images.items()},
            'hostOutputs': {'cvd-host_package.tar.gz': {'size': len(host), 'sha256': sha(host)}},
            'imageContent': {'verified': True, 'apps': [{'package': p, 'partition': ALPHA['imageVerification']['apps'][p].split(':')[0],
                'path': ALPHA['imageVerification']['apps'][p].split(':', 1)[1], 'sha256': APP_SHA[p], 'identity': [p, v],
                'signers': [SIGNER], 'present': True} for p, v in APPS]}}

    def receipt_dir(self, receipt, name='received', sums=None):
        directory = self.tmp/name
        directory.mkdir()
        (directory/'emulator-alpha-verification.json').write_text(json.dumps(receipt))
        digest = sha((directory/'emulator-alpha-verification.json').read_bytes())
        (directory/'SHA256SUMS').write_text(sums if sums is not None else f'{digest}  emulator-alpha-verification.json\n')
        return directory

    def api(self, run=None, artifact=None, listed=None):
        verification = CONFIG['retainedVerification']
        runs = {verification['runId']: dict({'id': int(verification['runId']), 'repository': {'full_name': REPO}, 'path': verification['workflowPath'],
            'head_branch': 'main', 'event': 'workflow_dispatch', 'head_sha': verification['recipe'], 'status': 'completed',
            'conclusion': 'success'}, **(run or {}))}
        artifacts = {verification['artifactId']: dict({'id': verification['artifactId'], 'name': verification['artifactName'],
            'workflow_run': {'id': int(verification['runId'])}, 'expired': False, 'digest': 'sha256:' + 'd' * 64, 'size_in_bytes': 900}, **(artifact or {}))}
        calls = []

        def get(path):
            calls.append(path)
            match = re.fullmatch(rf'/repos/{REPO}/actions/runs/(\d+)', path)
            if match:
                return runs.get(match[1], {'id': int(match[1]), 'repository': {'full_name': REPO}, 'path': verification['workflowPath'],
                    'head_branch': 'main', 'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success', 'head_sha': 'e' * 40, **(run or {})})
            match = re.fullmatch(rf'/repos/{REPO}/actions/artifacts/(\d+)', path)
            if match:
                return artifacts[int(match[1])]
            match = re.fullmatch(rf'/repos/{REPO}/actions/runs/(\d+)/artifacts\?name=(.+)', path)
            if match:
                return {'artifacts': listed if listed is not None else [{'id': 5, 'name': match[2], 'expired': False,
                    'digest': 'sha256:' + 'a' * 64, 'workflow_run': {'id': int(match[1])}, 'size_in_bytes': 5}]}
            raise AssertionError(path)
        get.calls = calls
        return get

class FakeAlpha:
    class CheckFailed(Exception): pass
    class InvalidInput(Exception): pass
    class SetupMissing(Exception):
        def __init__(self, missing):
            super().__init__(missing)
            self.missing = missing

    def __init__(self, tree, identity=(), pinned=()):
        self.tree, self.identity, self.pinned, self.events = tree, list(identity), list(pinned), []
    def load(self, path): return ALPHA
    def guard(self, config): self.events.append('guard')
    def check_dispatch(self, config, sha_, apps):
        if sha_ != SHA or apps != APPS:
            raise self.CheckFailed('not the bound base')
    def locked(self, config):
        self.events.append('lock')
        return types.SimpleNamespace(close=lambda: self.events.append('unlock'))
    def pinned_failures(self, config): return {}, self.tree, self.pinned
    def read_props(self, product_out): return {'system/build.prop': {}}
    def identity_failures(self, props, config, build, timestamp, device):
        self.events.append(('identity', build, timestamp, device))
        return self.identity

class OriginalCheckTest(Fixture):
    env = {'GITHUB_REPOSITORY': REPO, 'GITHUB_RUN_ID': '41000000001'}

    def test_the_original_run_artifact_and_receipt_are_checked_and_the_receipt_digest_carried(self):
        api = self.api()
        result = exporter.check_original(self.config, self.received, api, self.env)
        self.assertEqual(api.calls, [f'/repos/{REPO}/actions/runs/40777816699', f'/repos/{REPO}/actions/artifacts/12854389864'])
        self.assertEqual(result['receiptSha256'], sha((self.received/'emulator-alpha-verification.json').read_bytes()))
        self.assertEqual((result['artifact']['digest'], result['receiptSha256Pinned']), ('sha256:' + 'd' * 64, False))
        self.assertEqual(json.loads((self.received/exporter.CHECK).read_text()), result)

    def test_a_wrong_run_artifact_or_repository_is_refused(self):
        for index, (kwargs, message) in enumerate((
                (dict(run={'repository': {'full_name': 'someone/fork'}}), 'repository='),
                (dict(run={'path': '.github/workflows/alpha-runtime.yml'}), 'path='),
                (dict(run={'head_branch': 'feature'}), 'head_branch='),
                (dict(run={'event': 'push'}), 'event='),
                (dict(run={'head_sha': 'f' * 40}), 'head_sha='),
                (dict(run={'conclusion': 'failure'}), 'conclusion='),
                (dict(artifact={'name': 'emulator-alpha-verification-1'}), 'not the recorded'),
                (dict(artifact={'workflow_run': {'id': 1}}), 'not the recorded'),
                (dict(artifact={'expired': True}), 'not the recorded'),
                (dict(artifact={'digest': None}), 'not the recorded'))):
            with self.subTest(index=index), self.assertRaisesRegex(exporter.CheckFailed, message):
                exporter.check_original(self.config, self.received, self.api(**kwargs), self.env)
        with self.assertRaisesRegex(exporter.CheckFailed, 'this run is in'):
            exporter.check_original(self.config, self.received, self.api(), {'GITHUB_REPOSITORY': 'someone/fork'})
        self.assertFalse((self.received/exporter.CHECK).exists())

    def test_an_unknown_unsigned_or_altered_receipt_is_refused(self):
        cases = (
            (lambda r: dict(r, verdict='FAIL'), "verdict is 'FAIL'"),
            (lambda r: dict(r, receiptType='emulator-alpha-receipt'), 'receiptType'),
            (lambda r: dict(r, signed=True), 'signed is True'),
            (lambda r: dict(r, bootAttempted=True), 'bootAttempted'),
            (lambda r: dict(r, originalCompile=dict(r['originalCompile'], buildReceipt='invented.json')), 'buildReceipt must be recorded as null'),
            (lambda r: dict(r, originalCompile={k: v for k, v in r['originalCompile'].items() if k != 'buildReceipt'}), 'buildReceipt'),
            (lambda r: dict(r, verifierRecipe='0' * 40), 'verifierRecipe'),
            (lambda r: dict(r, candidate='0' * 40), 'candidate'),
            (lambda r: dict(r, preimage=dict(r['preimage'], matched=False)), 'preimage'),
            (lambda r: dict(r, images={k: v for k, v in r['images'].items() if k != 'vbmeta.img'}), "required images \\['vbmeta.img'\\]"),
            (lambda r: dict(r, images=dict(r['images'], **{'../x.img': {'size': 1, 'sha256': '0' * 64}})), 'not a measured top-level image'),
            (lambda r: dict(r, hostOutputs={'cvd-host_package.tar.gz': None}), 'host package'),
            (lambda r: dict(r, imageContent=dict(r['imageContent'], apps=[dict(a, signers=['0' * 64]) for a in r['imageContent']['apps']])),
             'signed by the approved app signer'),
            (lambda r: dict(r, imageContent=dict(r['imageContent'], apps=r['imageContent']['apps'][:2])), 'exactly the original apps'))
        for index, (edit, message) in enumerate(cases):
            directory = self.receipt_dir(edit(copy.deepcopy(self.receipt)), name=f'bad{index}')
            with self.subTest(index=index), self.assertRaisesRegex(exporter.CheckFailed, message):
                exporter.check_original(self.config, directory, self.api(), self.env)
        directory = self.receipt_dir(self.receipt, name='badsums', sums='0' * 64 + '  emulator-alpha-verification.json\n')
        with self.assertRaisesRegex(exporter.CheckFailed, 'SHA256SUMS does not cover exactly the receipt'):
            exporter.check_original(self.config, directory, self.api(), self.env)
        (self.received/'extra.txt').write_text('x')
        with self.assertRaisesRegex(exporter.CheckFailed, 'not exactly'):
            exporter.check_original(self.config, self.received, self.api(), self.env)
        (self.received/'extra.txt').unlink()
        self.config['retainedVerification']['receiptSha256'] = '0' * 64
        with self.assertRaisesRegex(exporter.CheckFailed, 'is not the configured'):
            exporter.check_original(self.config, self.received, self.api(), self.env)

class ExportTest(Fixture):
    def checked(self):
        exporter.check_original(self.config, self.received, self.api(), OriginalCheckTest.env)
        return self.received

    def tree_state(self):
        return {str(p): sha(p.read_bytes()) for p in sorted((self.tmp/'tree').rglob('*')) if p.is_file()}

    def run_export(self, ea=None, **kw):
        ea = ea or FakeAlpha(self.tmp/'tree')
        return exporter.export(self.config, ea, ALPHA, kw.get('receipts', self.received), self.tmp/'export', kw.get('run', '41000000002'),
            kw.get('sha', SHA), kw.get('apps', APPS), kw.get('ts', '1793220134')), ea

    def test_exports_exactly_the_measured_files_rehashed_under_the_lock(self):
        self.checked()
        before = self.tree_state()
        document, ea = self.run_export()
        export = self.tmp/'export'
        files = exporter.regular_files(export)
        self.assertEqual(files, sorted(['SHA256SUMS', 'alpha-export.json', 'host/cvd-host_package.tar.gz', 'original/emulator-alpha-verification.json',
            *[f'images/{n}' for n in self.images]]))
        self.assertNotIn('images/android-info.txt', files)
        self.assertEqual(self.tree_state(), before, 'the retained tree is only read')
        self.assertEqual(ea.events[-1], 'unlock')
        self.assertIn(('identity', 'rose-cf-alpha-40765631053', '1793220134', ALPHA['hostEvidence']['lunch']['PRODUCT_DEVICE']), ea.events)
        self.assertEqual((document['executedMake'], document['signed'], document['published'], document['bootAttempted']), (False, False, False, False))
        self.assertIsNone(document['originalCompile']['buildReceipt'])
        self.assertEqual(document['originalVerification']['receiptSha256'], sha((self.received/'emulator-alpha-verification.json').read_bytes()))
        self.assertEqual(exporter.read_sums(export), {n: sha((export/n).read_bytes()) for n in files if n != 'SHA256SUMS'})
        self.assertNotIn(str(self.tmp), json.dumps(document), 'no host paths in the export receipt')

    def test_missing_linked_or_changed_retained_files_fail_and_leave_no_export(self):
        self.checked()
        for index, change in enumerate((
                lambda: (self.product_out/'boot.img').unlink(),
                lambda: (self.product_out/'boot.img').write_bytes(b'BOOT'),
                lambda: ((self.product_out/'boot.img').unlink(), (self.product_out/'boot.img').symlink_to(self.product_out/'vbmeta.img')),
                lambda: (self.host_out/'cvd-host_package.tar.gz').write_bytes(b'other host package'),
                lambda: (self.host_out/'cvd-host_package.tar.gz').unlink())):
            saved = {n: (self.product_out/n).read_bytes() for n in self.images}
            host = (self.host_out/'cvd-host_package.tar.gz').read_bytes()
            change()
            with self.subTest(index=index), self.assertRaises(exporter.CheckFailed):
                self.run_export()
            self.assertFalse((self.tmp/'export').exists(), 'no partial export')
            for n, data in saved.items():
                if (self.product_out/n).is_symlink() or (self.product_out/n).exists():
                    (self.product_out/n).unlink()
                (self.product_out/n).write_bytes(data)
            (self.host_out/'cvd-host_package.tar.gz').write_bytes(host)

    def test_wrong_dispatch_identity_tree_or_handed_over_receipt_is_refused(self):
        self.checked()
        with self.assertRaisesRegex(exporter.InvalidInput, 'BUILD_DATETIME 1793220134'):
            self.run_export(ts='1793220605')
        with self.assertRaisesRegex(FakeAlpha.CheckFailed, 'not the bound base'):
            self.run_export(sha='0' * 40)
        with self.assertRaisesRegex(exporter.CheckFailed, 'not run 40765631053'):
            self.run_export(ea=FakeAlpha(self.tmp/'tree', identity=['system/build.prop: ro.build.date.utc=1']))
        with self.assertRaisesRegex(exporter.CheckFailed, 'tree drifted'):
            self.run_export(ea=FakeAlpha(self.tmp/'tree', pinned=['tree drifted']))
        receipt = json.loads((self.received/'emulator-alpha-verification.json').read_text())
        receipt['note'] = 'edited after the guard checked it'
        (self.received/'emulator-alpha-verification.json').write_text(json.dumps(receipt))
        (self.received/'SHA256SUMS').write_text(f'{sha((self.received/"emulator-alpha-verification.json").read_bytes())}  emulator-alpha-verification.json\n')
        with self.assertRaisesRegex(exporter.CheckFailed, 'is not the checked'):
            self.run_export()
        (self.tmp/'export').mkdir()
        with self.assertRaisesRegex(exporter.CheckFailed, 'already exists'):
            self.run_export()

class RuntimeFixture(Fixture):
    def setUp(self):
        super().setUp()
        exporter.check_original(self.config, self.received, self.api(), OriginalCheckTest.env)
        exporter.export(self.config, FakeAlpha(self.tmp/'tree'), ALPHA, self.received, self.tmp/'export', '41000000002', SHA, APPS, '1793220134')
        self.export = self.tmp/'export'
        self.receipt_sha = exporter.read_sums(self.export)['alpha-export.json']

    def rewrite_sums(self):
        files = [n for n in exporter.regular_files(self.export) if n != 'SHA256SUMS']
        (self.export/'SHA256SUMS').write_text(''.join(f'{sha((self.export/n).read_bytes())}  {n}\n' for n in files))
        return exporter.read_sums(self.export)['alpha-export.json']

class GuardTest(RuntimeFixture):
    ENV = {'ROSEOS_ALPHA_RUNTIME_READY': 'true', 'ROSEOS_RUNTIME_WORKLOAD_IDENTITY_PROVIDER': 'projects/1/locations/global/workloadIdentityPools/p/providers/r',
        'ROSEOS_RUNTIME_CONTROLLER_SERVICE_ACCOUNT': 'c@p.iam.gserviceaccount.com', 'GITHUB_REPOSITORY': REPO, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
        'GITHUB_WORKFLOW_REF': f'{REPO}/.github/workflows/alpha-runtime.yml@refs/heads/main'}

    def test_not_ready_or_unset_identity_stops_before_any_controller(self):
        for ready in (None, 'false', 'True', ''):
            env = dict(self.ENV)
            env.pop('ROSEOS_ALPHA_RUNTIME_READY')
            if ready is not None:
                env['ROSEOS_ALPHA_RUNTIME_READY'] = ready
            with self.subTest(ready=ready), self.assertRaises(runtime.SetupMissing) as caught:
                runtime.guard(self.config, env, '41000000002', 'a' * 64, self.receipt_sha)
            self.assertEqual(caught.exception.missing, ['ROSEOS_ALPHA_RUNTIME_READY'])
        env = {k: v for k, v in self.ENV.items() if k != 'ROSEOS_RUNTIME_CONTROLLER_SERVICE_ACCOUNT'}
        with self.assertRaises(runtime.SetupMissing) as caught:
            runtime.guard(self.config, env, '41000000002', 'a' * 64, self.receipt_sha)
        self.assertEqual(caught.exception.missing, ['ROSEOS_RUNTIME_CONTROLLER_SERVICE_ACCOUNT'])
        self.assertTrue(runtime.guard(self.config, self.ENV, '41000000002', 'a' * 64, self.receipt_sha)['ready'])

    def test_wrong_workflow_repository_event_or_inputs_are_refused(self):
        for key, value in (('GITHUB_WORKFLOW_REF', f'{REPO}/.github/workflows/os-release.yml@refs/heads/main'),
                           ('GITHUB_WORKFLOW_REF', f'{REPO}/.github/workflows/alpha-runtime.yml@refs/heads/feature'),
                           ('GITHUB_REPOSITORY', 'someone/fork'), ('GITHUB_EVENT_NAME', 'push')):
            with self.subTest(key=key, value=value), self.assertRaisesRegex(runtime.CheckFailed, key):
                runtime.guard(self.config, dict(self.ENV, **{key: value}), '41000000002', 'a' * 64, self.receipt_sha)
        for args in (('x', 'a' * 64, self.receipt_sha), ('41000000002', 'A' * 64, self.receipt_sha), ('41000000002', 'a' * 64, 'sha256:' + 'a' * 64)):
            with self.assertRaises(runtime.InvalidInput):
                runtime.guard(self.config, self.ENV, *args)

    def test_source_check_binds_the_export_run_and_artifact_digest(self):
        result = runtime.source_check(self.config, self.api(), '41000000002', 'a' * 64)
        self.assertEqual((result['artifact']['name'], result['artifact']['digest']), ('emulator-alpha-export-41000000002', 'sha256:' + 'a' * 64))
        for index, (kwargs, digest, message) in enumerate((
                (dict(run={'path': '.github/workflows/alpha-runtime.yml'}), 'a' * 64, 'path='),
                (dict(run={'head_branch': 'feature'}), 'a' * 64, 'head_branch='),
                (dict(run={'event': 'push'}), 'a' * 64, 'event='),
                (dict(run={'conclusion': 'failure'}), 'a' * 64, 'conclusion='),
                (dict(run={'repository': {'full_name': 'someone/fork'}}), 'a' * 64, 'repository='),
                (dict(listed=[]), 'a' * 64, 'holds 0 artifacts'),
                (dict(listed=[{'name': 'emulator-alpha-export-41000000002'}] * 2), 'a' * 64, 'holds 2 artifacts'),
                (dict(), 'b' * 64, 'not the dispatched digest'),
                (dict(listed=[{'id': 5, 'name': 'emulator-alpha-export-41000000002', 'expired': True, 'digest': 'sha256:' + 'a' * 64,
                    'workflow_run': {'id': 41000000002}}]), 'a' * 64, 'expired'))):
            with self.subTest(index=index), self.assertRaisesRegex(runtime.CheckFailed, message):
                runtime.source_check(self.config, self.api(**kwargs), '41000000002', digest)

class ControlTest(RuntimeFixture):
    def gcloud(self, states, fail_request=False):
        log, state_file = self.tmp/'gcloud.log', self.tmp/'states.json'
        state_file.write_text(json.dumps(states))
        path = self.tmp/'gcloud'
        path.write_text(f'''#!{sys.executable}
import json, sys
open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')
states = json.load(open({str(state_file)!r}))
if sys.argv[3] == 'describe':
    print(states.pop(0) if len(states) > 1 else states[0]); json.dump(states, open({str(state_file)!r}, 'w'))
else:
    sys.exit({1 if fail_request else 0})
''')
        path.chmod(0o755)
        return str(path), log

    def test_start_and_stop_address_only_the_runtime_instance_asynchronously(self):
        gcloud, log = self.gcloud(['TERMINATED', 'STAGING', 'RUNNING'])
        self.assertEqual(runtime.control(self.config, 'start', gcloud, sleep=lambda s: None), 'RUNNING')
        calls = [json.loads(l) for l in log.read_text().splitlines()]
        self.assertIn(['compute', 'instances', 'start', 'pixel-os-smoke-20261023-a', '--project=protocols-465723', '--zone=us-west2-a', '--async', '--quiet'], calls)
        self.assertEqual(sum(1 for c in calls if c[2] == 'start'), 1, 'never resubmitted')
        self.assertFalse([c for c in calls if 'immortal-os-builder' in c])
        gcloud, log = self.gcloud(['SUSPENDED'])
        with self.assertRaisesRegex(runtime.CheckFailed, 'refusing to start'):
            runtime.control(self.config, 'start', gcloud, sleep=lambda s: None)
        ticks = iter(range(0, 10000, 100))
        gcloud, _ = self.gcloud(['RUNNING', 'STOPPING'])
        with self.assertRaisesRegex(runtime.CheckFailed, 'did not reach TERMINATED'):
            runtime.control(self.config, 'stop', gcloud, timeout=300, sleep=lambda s: None, clock=lambda: next(ticks))
        builder = copy.deepcopy(self.config)
        builder['runtime']['host']['instance'] = 'immortal-os-builder'
        with self.assertRaisesRegex(runtime.CheckFailed, 'never be the builder'):
            runtime.control(builder, 'start', gcloud)

class ExportVerificationTest(RuntimeFixture):
    def test_the_export_is_checked_against_its_receipt_and_the_original_verification(self):
        receipt, original, digest = runtime.verify_export(self.config, self.export, self.receipt_sha)
        self.assertEqual(sorted(original['images']), sorted(self.images))
        self.assertEqual(digest, receipt['originalVerification']['receiptSha256'])

    def refused(self, message, receipt_sha=None):
        with self.assertRaisesRegex(runtime.CheckFailed, message):
            runtime.verify_export(self.config, self.export, receipt_sha or self.receipt_sha)

    def test_hash_receipt_and_contents_mismatches_are_refused(self):
        self.refused('not the dispatched export_receipt_sha256', '0' * 64)
        (self.export/'images/boot.img').write_bytes(b'BOOT')
        self.refused('images/boot.img does not match SHA256SUMS')
        self.refused('not the verified bytes', self.rewrite_sums())  # consistent sums over substituted image bytes
        (self.export/'images/boot.img').write_bytes(b'boot')
        self.rewrite_sums()
        (self.export/'images/extra.img').write_bytes(b'x')
        self.refused('SHA256SUMS does not cover exactly')
        self.refused('not exactly the measured images', self.rewrite_sums())
        (self.export/'images/extra.img').unlink()
        (self.export/'images/link.img').symlink_to(self.export/'images/boot.img')
        self.refused('not a regular file')
        (self.export/'images/link.img').unlink()
        original = json.loads((self.export/'original/emulator-alpha-verification.json').read_text())
        original['verdict'] = 'FAIL'
        (self.export/'original/emulator-alpha-verification.json').write_text(json.dumps(original))
        self.refused('original verification receipt is refused|is not the checked', self.rewrite_sums())

class ExtractionTest(RuntimeFixture):
    def test_entries_that_escape_or_are_special_are_refused(self):
        def link(name, target, kind=tarfile.SYMTYPE):
            info = tarfile.TarInfo(name)
            info.type, info.linkname = kind, target
            return info, None
        def regular(name, mode=0o644):
            info = tarfile.TarInfo(name)
            info.size, info.mode = 1, mode
            return info, b'x'
        def special(name, kind):
            info = tarfile.TarInfo(name)
            info.type = kind
            return info, None
        cases = ((regular('/etc/passwd'), 'escapes'), (regular('../outside'), 'escapes'), (link('bin/out', '../../outside'), 'outside the task root'),
                 (link('bin/abs', '/usr/bin/sh'), 'outside the task root'), (link('bin/hard', 'bin/adb', tarfile.LNKTYPE), 'not a regular file'),
                 (special('dev/kvm', tarfile.CHRTYPE), 'not a regular file'), (special('fifo', tarfile.FIFOTYPE), 'not a regular file'),
                 (regular('bin/su', 0o4755), 'setuid'))
        for index, (entry, message) in enumerate(cases):
            archive = host_package(self.tmp/f'bad{index}.tar.gz', [entry])
            with self.subTest(index=index), self.assertRaisesRegex(runtime.CheckFailed, message):
                runtime.safe_extract(archive, self.tmp/f'x{index}')
            self.assertFalse((self.tmp/'outside').exists())
        runtime.safe_extract(host_package(self.tmp/'good.tar.gz', [link('bin/cvd', 'launch_cvd')]), self.tmp/'good')
        self.assertTrue((self.tmp/'good/bin/launch_cvd').is_file())

class FakeHost(runtime.System):
    """The runtime host, synthetic: KVM, space, processes and every command are stand-ins; nothing is launched."""
    def __init__(self, tmp, kvm=True, rw=True, free=100, procs=(), locks=(), boot_after=2, launch_exit=0, verify_exit=0,
                 during_launch=None, leftover=False):
        self.kvm = str(tmp/f'kvm-{id(self)}')
        if kvm:
            Path(self.kvm).write_text('')
        self.rw, self.free, self.procs, self.lock_files = rw, free, list(procs), list(locks)
        self.boot_after, self.launch_exit, self.verify_exit, self.during_launch, self.leftover = boot_after, launch_exit, verify_exit, during_launch, leftover
        self.calls, self.now, self.root = [], 0.0, None

    def access(self, path, mode): return self.rw
    def free_gib(self, path): return self.free
    def processes(self):
        procs = list(self.procs)
        if self.leftover and self.root:
            procs.append((99, f'{self.root}/host/bin/run_cvd'))
        return procs
    def locks(self, patterns): return self.lock_files
    def sleep(self, seconds): self.now += seconds
    def clock(self): return self.now

    def run(self, argv, env, timeout, cwd=None):
        self.calls.append([Path(argv[0]).name, *argv[1:]])
        name = Path(argv[0]).name
        if name == 'launch_cvd':
            self.root = Path(env['HOME']).parent
            if self.during_launch:
                self.during_launch()
            return self.launch_exit, 'launched'
        if name == 'adb' and 'getprop' in argv:
            self.now += 60
            self.boot_after -= 1
            return 0, '1\n' if self.boot_after <= 0 else '\n'
        if name == 'adb':
            return 0, 'connected'
        if name == 'stop_cvd':
            return 0, 'stopped'
        if argv[1].endswith('rose-verify.py'):
            out = Path(argv[argv.index('--out') + 1])
            out.mkdir(parents=True)
            (out/'runtime.json').write_text('{"synthetic": true}')
            return self.verify_exit, ''
        raise AssertionError(f'unexpected command {argv}')

class RunTest(RuntimeFixture):
    def setUp(self):
        super().setUp()
        self.home = self.tmp/'home'
        (self.home/'cuttlefish_runtime').mkdir(parents=True)
        (self.home/'cuttlefish_runtime/userdata.img').write_bytes(b'historical userdata')

    def run_runtime(self, host, evidence='evidence', receipt_sha=None):
        return runtime.run(self.config, self.export, receipt_sha or self.receipt_sha, '41000000003', '1', self.tmp/evidence, self.home, host)

    def evidence(self, name='evidence'):
        return json.loads((self.tmp/name/runtime.EVIDENCE).read_text())

    def test_a_synthetic_boot_runs_rose_verify_with_a_generated_expectation_then_stops_and_cleans_up(self):
        host = FakeHost(self.tmp)
        result = self.run_runtime(host)
        self.assertEqual((result['verdict'], result['bootCompleted'], result['stopped'], result['leftoverProcesses'], result['taskRootRemoved'],
            result['historicalUnchanged']), ('PASS', True, True, 0, True, True))
        names = [c[0] for c in host.calls]
        self.assertEqual(names[0], 'launch_cvd')
        self.assertEqual(names[-1], 'stop_cvd')
        launch = host.calls[0]
        self.assertIn('--start_webrtc=false', launch)
        self.assertIn('--base_instance_num=21', launch)
        self.assertTrue(any(a.startswith('--system_image_dir=') and 'rose-alpha-runtime/41000000003-1/images' in a for a in launch))
        verify_call = next(c for c in host.calls if c[0] == Path(sys.executable).name)
        self.assertIn('--interactive', verify_call)
        for forbidden in ('sign', 'gcloud', 'make', 'm', 'publish', 'pair', 'pm', 'grant'):
            self.assertNotIn(forbidden, names)
        self.assertEqual((self.home/'cuttlefish_runtime/userdata.img').read_bytes(), b'historical userdata')
        self.assertFalse((self.home/'rose-alpha-runtime/41000000003-1').exists())
        expectation = verify.load_expectation(self.tmp/'evidence'/runtime.EXPECTATION)
        self.assertEqual([(a['package'], a['version'], a['sha256']) for a in expectation['apps']], [(p, v, APP_SHA[p]) for p, v in APPS])
        self.assertEqual((expectation['candidate'], expectation['appSignerSha256'], expectation['channel']), (SHA, SIGNER, 'alpha'))
        self.assertIn('no Pixel, Agent 308, OS PR70 Home role or PR85 tray claim', expectation['basis'])
        self.assertNotEqual(expectation['target']['actionsRun'], '40719075730')
        text = (self.tmp/'evidence'/runtime.EVIDENCE).read_text()
        self.assertNotIn('0.0.0.0:', text, 'no adb serial in the evidence')
        self.assertNotIn(str(self.tmp), text)
        files = exporter.regular_files(self.tmp/'evidence')
        self.assertEqual(exporter.read_sums(self.tmp/'evidence'), {n: sha((self.tmp/'evidence'/n).read_bytes()) for n in files if n != 'SHA256SUMS'})

    def test_boot_deadline_failure_still_stops_records_and_cleans_up(self):
        host = FakeHost(self.tmp, boot_after=10**6)
        with self.assertRaisesRegex(runtime.CheckFailed, 'sys.boot_completed was not 1 within 900s'):
            self.run_runtime(host)
        document = self.evidence()
        self.assertEqual((document['verdict'], document['bootCompleted'], document['stopped'], document['taskRootRemoved']), ('FAIL', False, True, True))
        self.assertIsNone(document['roseVerify'])
        self.assertEqual(host.calls[-1][0], 'stop_cvd')

    def test_launch_failure_and_rose_verify_failure_are_recorded_after_stopping(self):
        host = FakeHost(self.tmp, launch_exit=1)
        with self.assertRaisesRegex(runtime.CheckFailed, 'launch_cvd exited 1'):
            self.run_runtime(host)
        self.assertEqual((self.evidence()['launched'], self.evidence()['bootCompleted'], self.evidence()['stopped']), (True, None, True))
        host = FakeHost(self.tmp, verify_exit=1)
        with self.assertRaisesRegex(runtime.CheckFailed, 'rose-verify runtime exited 1'):
            self.run_runtime(host, evidence='evidence2')
        self.assertEqual((self.evidence('evidence2')['bootCompleted'], self.evidence('evidence2')['stopped']), (True, True))

    def test_missing_kvm_space_or_foreign_guests_stop_before_anything_is_written_on_the_host(self):
        for index, (host, error, message) in enumerate((
                (FakeHost(self.tmp, kvm=False), runtime.SetupMissing, '/dev/kvm'),
                (FakeHost(self.tmp, rw=False), runtime.SetupMissing, 'read/write'),
                (FakeHost(self.tmp, free=1), runtime.SetupMissing, '40 GiB'),
                (FakeHost(self.tmp, procs=[(7, '/usr/bin/crosvm run')]), runtime.CheckFailed, 'foreign Cuttlefish guest'),
                (FakeHost(self.tmp, locks=['/tmp/acloud_cvd_temp/local-instance-1.lock']), runtime.CheckFailed, 'foreign Cuttlefish guest'))):
            with self.subTest(index=index), self.assertRaises(error) as caught:
                self.run_runtime(host, evidence=f'e{index}')
            text = ', '.join(caught.exception.missing) if error is runtime.SetupMissing else str(caught.exception)
            self.assertIn(message, text)
            self.assertEqual(host.calls, [], 'nothing launched')
            self.assertFalse((self.home/'rose-alpha-runtime').exists() and any((self.home/'rose-alpha-runtime').iterdir()))
            document = self.evidence(f'e{index}')
            self.assertEqual((document['launched'], document['bootCompleted'], document['stopped'], document['taskRootRemoved']), (False, None, None, None),
                'unreached steps stay unknown, never claimed')
        (self.home/'rose-alpha-runtime/41000000003-1').mkdir(parents=True)
        (self.home/'rose-alpha-runtime/41000000003-1/userdata.img').write_bytes(b'someone else')
        with self.assertRaisesRegex(runtime.CheckFailed, 'never reuses another one'):
            self.run_runtime(FakeHost(self.tmp), evidence='reuse')
        self.assertEqual((self.home/'rose-alpha-runtime/41000000003-1/userdata.img').read_bytes(), b'someone else')

    def test_changed_historical_data_or_leftover_processes_fail_the_attempt(self):
        def touch():
            (self.home/'cuttlefish_runtime/userdata.img').write_bytes(b'historical userdata, changed')
        with self.assertRaisesRegex(runtime.CheckFailed, 'cleanup incomplete'):
            self.run_runtime(FakeHost(self.tmp, during_launch=touch))
        self.assertIs(self.evidence()['historicalUnchanged'], False)
        (self.home/'cuttlefish_runtime/userdata.img').write_bytes(b'historical userdata')
        with self.assertRaisesRegex(runtime.CheckFailed, 'cleanup incomplete'):
            self.run_runtime(FakeHost(self.tmp, leftover=True), evidence='left')
        self.assertEqual(self.evidence('left')['leftoverProcesses'], 1)

    def test_a_tampered_export_is_refused_before_preflight(self):
        (self.export/'images/super.img').write_bytes(b'other super')
        host = FakeHost(self.tmp)
        with self.assertRaisesRegex(runtime.CheckFailed, 'does not match SHA256SUMS'):
            self.run_runtime(host)
        self.assertEqual(host.calls, [])
        self.assertIsNone(self.evidence()['stopped'])

class CommandLineTest(RuntimeFixture):
    def test_exit_codes_and_the_manual_expectation(self):
        script = str(SCRIPTS/'alpha-runtime.py')
        env = {k: v for k, v in os.environ.items() if not k.startswith(('ROSEOS_', 'GITHUB_'))}
        result = subprocess.run([sys.executable, script, 'guard', '--export-run', '1', '--export-artifact-digest', 'a' * 64,
            '--export-receipt-sha256', 'b' * 64], capture_output=True, text=True, env=env)
        self.assertEqual((result.returncode, json.loads(result.stderr)['verdict']), (4, 'SETUP_MISSING'))
        self.assertIn('ROSEOS_ALPHA_RUNTIME_READY', json.loads(result.stderr)['missing'])
        result = subprocess.run([sys.executable, script, 'run', '--export-run', 'x'], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 2)
        config = self.tmp/'config.json'
        config.write_text(json.dumps(self.config))
        out = self.tmp/'expectation.json'
        result = subprocess.run([sys.executable, script, 'expectation', '--config', str(config), '--artifact-dir', str(self.export),
            '--export-receipt-sha256', self.receipt_sha, '--out', str(out)], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(verify.load_expectation(out)['apps'][0]['version'], 302)
        result = subprocess.run([sys.executable, script, 'expectation', '--config', str(config), '--artifact-dir', str(self.export),
            '--export-receipt-sha256', '0' * 64, '--out', str(self.tmp/'x.json')], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.tmp/'x.json').exists())

class SourceTest(unittest.TestCase):
    def test_sources_never_sign_build_publish_or_contact_providers(self):
        for file in ('alpha-runtime.py', 'alpha-export.py'):
            source = (SCRIPTS/file).read_text()
            for absent in ('sign-apk', 'apksigner sign', "'make'", 'build/envsetup.sh', 'PIXEL_OTA', 'fetch_secret', 'pm grant', 'appops', 'pairing-code',
                           'gh release', 'setcap', 'usermod', 'apt-get', 'mount '):
                self.assertNotIn(absent, source, (file, absent))

class WorkflowTest(unittest.TestCase):
    def test_the_runtime_workflow_is_separate_gated_and_uses_only_the_new_identity(self):
        job = jobs(RUNTIME_WORKFLOW)
        self.assertEqual(sorted(job), ['controller-start', 'controller-stop', 'guard', 'runtime'])
        self.assertIn('\npermissions: {}\n', RUNTIME_WORKFLOW)
        self.assertIn('  group: roseos-alpha-runtime\n  cancel-in-progress: false\n', RUNTIME_WORKFLOW)
        self.assertIn("    if: github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'\n", job['guard'])
        self.assertIn('ROSEOS_ALPHA_RUNTIME_READY: ${{ vars.ROSEOS_ALPHA_RUNTIME_READY }}', job['guard'])
        self.assertIn('    needs: guard\n', job['controller-start'])
        self.assertIn('    needs: controller-start\n', job['runtime'])
        for name in ('controller-start', 'controller-stop'):
            self.assertIn('GCP_WORKLOAD_IDENTITY_PROVIDER: ${{ vars.ROSEOS_RUNTIME_WORKLOAD_IDENTITY_PROVIDER }}', job[name])
            self.assertIn('GCP_CONTROLLER_SERVICE_ACCOUNT: ${{ vars.ROSEOS_RUNTIME_CONTROLLER_SERVICE_ACCOUNT }}', job[name])
            self.assertIn('runs-on: ubuntu-24.04', job[name])
        self.assertIn("    if: always() && needs.controller-start.result != 'skipped'\n", job['controller-stop'])
        self.assertIn('python3 scripts/alpha-runtime.py stop-host', job['controller-stop'])
        self.assertNotIn('vars.GCP_', RUNTIME_WORKFLOW, 'never the os-release provider or accounts')
        self.assertNotIn('secrets.', RUNTIME_WORKFLOW)
        self.assertEqual(re.findall(r'^    runs-on: (.+)$', RUNTIME_WORKFLOW, re.M),
            ['ubuntu-24.04', 'ubuntu-24.04', '[self-hosted, roseos-cuttlefish-runtime]', 'ubuntu-24.04'], 'never the builder runner')
        rt = job['runtime']
        self.assertIn('    runs-on: [self-hosted, roseos-cuttlefish-runtime]\n    timeout-minutes: 30\n    permissions:\n      contents: read\n      actions: read\n', rt)
        for absent in ('id-token', 'actions-gcp-login', 'PIXEL_OTA', 'create-github-app-token'):
            self.assertNotIn(absent, rt)
        self.assertIn('name: emulator-alpha-export-${{ inputs.export_run }}\n          run-id: ${{ inputs.export_run }}\n', rt)
        self.assertIn('      - name: Evidence\n        if: always()\n', rt)
        self.assertEqual(sorted(re.findall(r'^      (\w+):\n        description', RUNTIME_WORKFLOW, re.M)),
            ['export_artifact_digest', 'export_receipt_sha256', 'export_run'])
        self.assertNotIn('${{ inputs.', rt.split('run: ')[-1].split('\n')[0], 'inputs reach the script only through env')

    def test_os_release_export_mode_is_bounded_to_the_builder_lock_and_the_original_receipt(self):
        job = jobs(RELEASE_WORKFLOW)
        self.assertNotIn('roseos-cuttlefish-runtime', RELEASE_WORKFLOW)
        guard = job['guard']
        verification = CONFIG['retainedVerification']
        self.assertIn(f'          name: {verification["artifactName"]}\n          run-id: {verification["runId"]}\n          github-token: ${{{{ github.token }}}}\n', guard)
        self.assertIn('    permissions:\n      contents: read\n      actions: read', guard)
        self.assertIn("python3 scripts/alpha-export.py check-original --receipt-dir \"$RUNNER_TEMP/alpha-original\"", guard)
        export = job['alpha-export']
        self.assertIn("    needs: controller\n    if: inputs.mode == 'emulator-alpha-export'\n    runs-on: [self-hosted, roseos-frankel-builder]\n", export)
        self.assertIn('    permissions:\n      contents: read\n    steps:\n', export)
        runs = re.findall(r'^      (?:- |  )run: (.+)$', export, re.M)
        self.assertEqual(runs, ['python3 scripts/emulator-alpha.py preflight --job builder', '|',
            'python3 scripts/alpha-export.py export --receipt-dir "$ROSE_BASE/original" --export-dir "$ROSE_BASE/export" --candidate-sha "$CANDIDATE_SHA" '
            '--apps "$APPS" --run-id "$GITHUB_RUN_ID" --build-timestamp "$BUILD_TIMESTAMP"', 'test -z "${ROSE_BASE:-}" || rm -rf "$ROSE_BASE"'])
        for absent in ('id-token', 'secrets.', 'actions-gcp-login', 'os-release-stage', 'release-pipeline.py build', 'PIXEL_OTA', 'github-token'):
            self.assertNotIn(absent, export)
        self.assertIn('          name: emulator-alpha-export-${{ github.run_id }}\n', export)
        self.assertIn(', alpha-export, app-reuse, request-signing, signed-builder, publisher]\n', job['cleanup'])
        self.assertIn('app-reuse, emulator-alpha-export]\n', RELEASE_WORKFLOW)

if __name__ == '__main__':
    unittest.main()
