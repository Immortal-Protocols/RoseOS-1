#!/usr/bin/env python3
"""SIGN-021 .github/workflows/os-sign.yml: the approved trust surface, and its literal steps run against fixture signer-host
directories (validation before any credential, reuse of a committed job, refusal of conflicting inputs, public staging).
No credential, key, runner or network is used; the signer is the SYNTHETIC stand-in of test_signing_job.py."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
WORKFLOW = (ROOT/'.github/workflows/os-sign.yml').read_text()
sys.path.insert(0, str(SCRIPTS/'tests'))
import test_signing_job as fixtures  # noqa: E402

job = fixtures.job


def step(name):
    """The literal run block of the named step."""
    match = re.search(r'\n      - name: ' + re.escape(name) + r'\n(?:        if: .*\n)?        run: \|\n((?:          .*\n|\n)+)', WORKFLOW)
    if match:
        return '\n'.join(line[10:] for line in match.group(1).split('\n'))
    match = re.search(r'\n      - name: ' + re.escape(name) + r'\n(?:        if: .*\n)?        run: (.+)\n', WORKFLOW)
    return match.group(1)


class TrustSurface(unittest.TestCase):
    def test_only_a_manual_main_dispatch_on_the_dedicated_signer_group(self):
        triggers = WORKFLOW.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertEqual(re.findall(r'^  (\w+):', triggers, re.M), ['workflow_dispatch'])
        for absent in ('schedule', 'workflow_call', 'pull_request', 'push:', 'environment:', '${{ secrets.', 'PIXEL_OTA',
                       'create-github-app-token', 'actions: write', 'contents: write', 'vars.'):
            self.assertNotIn(absent, WORKFLOW)
        self.assertIn("    if: github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'\n", WORKFLOW)
        self.assertIn('    runs-on:\n      group: roseos-signing\n      labels: [self-hosted, linux, x64, roseos-dedicated-signer]\n', WORKFLOW)
        self.assertIn('\npermissions:\n  contents: read\n\n', WORKFLOW)
        self.assertIn('    permissions:\n      contents: read\n      id-token: write\n    steps:\n', WORKFLOW)
        self.assertIn('concurrency:\n  group: roseos-signing\n  cancel-in-progress: false\n', WORKFLOW)
        self.assertTrue((ROOT/'.github/workflows/os-sign.yml').is_file())  # workflow_ref ...os-sign.yml@refs/heads/main

    def test_hosted_ingress_is_secret_free_with_only_actions_read(self):
        ingress = WORKFLOW.split('\n  ingress:\n', 1)[1].split('\n  sign:\n', 1)[0]
        self.assertIn("    runs-on: ubuntu-24.04\n", ingress)
        self.assertIn('    permissions:\n      contents: read\n      actions: read\n    steps:\n', ingress)
        self.assertEqual(WORKFLOW.count('id-token: write'), 1)
        self.assertEqual(WORKFLOW.count('actions: read'), 1)
        self.assertNotIn('id-token', ingress)
        self.assertNotIn('actions-gcp-login', ingress)
        self.assertEqual(WORKFLOW.count('${{ github.token }}'), 1)  # only the ingress fetch
        self.assertIn('GH_TOKEN: ${{ github.token }}', ingress)
        sign = WORKFLOW.split('\n  sign:\n', 1)[1]
        self.assertTrue(sign.startswith('    needs: ingress\n'))
        self.assertNotIn('github.token', sign)
        self.assertNotIn('run-id:', WORKFLOW)  # same-run bundle only; the source artifact is read by id in ingress
        order = [WORKFLOW.index(s) for s in ('signing-job.py ingress --source-run', 'name: signing-ingress-${{ github.run_id }}',
            'needs: ingress', 'signing-job.py stage --bundle', 'name: Validate the manifest, inputs and config (before any credential)',
            'actions-gcp-login.py login')]
        self.assertEqual(order, sorted(order))
        self.assertIn('\nrun-name: OS signing ${{ inputs.request_id }}\n', WORKFLOW)
        self.assertIn('--request-id "$REQUEST_ID"', ingress)
        for name in ('source_run', 'artifact_id', 'artifact_digest', 'manifest_sha256', 'request_id'):
            self.assertIn(f'      {name}:\n', WORKFLOW)
        self.assertNotIn('      manifest:\n', WORKFLOW)

    def test_the_approved_federation_and_service_account(self):
        self.assertIn('  GCP_WORKLOAD_IDENTITY_PROVIDER: projects/1072799993443/locations/global/workloadIdentityPools/roseos-signing/providers/github\n',
            WORKFLOW)
        self.assertIn('  SIGNER_SERVICE_ACCOUNT: roseos-dedicated-signer@protocols-465723.iam.gserviceaccount.com\n', WORKFLOW)
        self.assertNotIn('roseos-build-signer', WORKFLOW)
        self.assertNotIn('roseos-actions', WORKFLOW)

    def test_private_signed_target_artifact_is_separate_and_after_logout(self):
        self.assertIn('          name: signed-target-${{ env.SIGNED_JOB }}-${{ github.run_id }}\n          path: ${{ runner.temp }}/signed-target\n'
            '          retention-days: 1\n', WORKFLOW)
        self.assertIn('          name: signed-${{ env.SIGNED_JOB }}-${{ github.run_id }}\n          path: ${{ runner.temp }}/signed-public\n', WORKFLOW)
        order = [WORKFLOW.index(s) for s in ('name: Drop signer access', 'signing-job.py check --output-dir', 'mkdir "$RUNNER_TEMP/signed-target"',
            'name: Public outputs and receipt', 'name: Signed target-files for verification (private)')]
        self.assertEqual(order, sorted(order))
        public = step('Check the committed outputs and stage the public files and receipt').split('mkdir "$RUNNER_TEMP/signed-target"')[0]
        self.assertNotIn('signed-target_files', public)

    def test_validation_precedes_access_and_access_is_always_dropped(self):
        order = [WORKFLOW.index(s) for s in ('name: Validate the manifest, inputs and config (before any credential)',
            'actions-gcp-login.py login "$SIGNER_SERVICE_ACCOUNT"', 'signing-job.py" run --manifest', 'name: Drop signer access',
            'signing-job.py check --output-dir', 'name: Public outputs and receipt')]
        self.assertEqual(order, sorted(order))
        self.assertIn('      - name: Drop signer access\n        if: always()\n        run: python3 scripts/actions-gcp-login.py logout\n', WORKFLOW)
        self.assertIn('        with:\n          persist-credentials: false\n', WORKFLOW)
        for uses in re.findall(r'uses: (\S+)', WORKFLOW):
            self.assertRegex(uses, r'@[0-9a-f]{40}$')



class LiteralSteps(fixtures.SigningJob):
    """The workflow's own run blocks against a fixture $SIGNING_ROOT (inbox/<job>, jobs, outputs, src)."""

    def setUp(self):
        super().setUp()
        self.signing = self.root/'signing'
        for name in ('jobs', 'outputs', 'src'):
            (self.signing/name).mkdir(parents=True)
        inbox = self.signing/'inbox/frankel-job'
        inbox.mkdir(parents=True)
        for key, name in (('targetFiles', 'unsigned.zip'), ('otatools', 'otatools')):
            shutil.copyfile(self.manifest['inputs'][key]['path'], inbox/name)
            self.manifest['inputs'][key]['path'] = str(inbox/name)
        self.manifest_path = inbox/'manifest.json'
        self.write(self.manifest)
        self.temp = self.root/'runner-temp'
        self.temp.mkdir()
        self.github_env = self.root/'github-env'
        self.github_env.write_text('')

    def write(self, manifest):
        self.manifest_path.write_text(json.dumps(manifest))
        return hashlib.sha256(self.manifest_path.read_bytes()).hexdigest()

    def bash(self, script, **env):
        base = {'PATH': os.environ['PATH'], 'SIGNING_ROOT': str(self.signing), 'MANIFEST': str(self.manifest_path),
            'MANIFEST_SHA256': hashlib.sha256(self.manifest_path.read_bytes()).hexdigest(), 'RUNNER_TEMP': str(self.temp),
            'GITHUB_WORKSPACE': str(ROOT), 'GITHUB_ENV': str(self.github_env)}
        return subprocess.run(['bash', '-c', script], cwd=ROOT, env=dict(base, **env), capture_output=True, text=True)

    def validate(self, **env):
        return self.bash(step('Validate the manifest, inputs and config (before any credential)'), **env)

    def test_validation_accepts_the_staged_job_and_refuses_everything_else(self):
        ok = self.validate()
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)['validated'], f'frankel-{fixtures.BUILD}')
        outside = self.root/'elsewhere.zip'
        outside.write_bytes(b'x')
        link = self.signing/'inbox/frankel-job/link.zip'
        link.symlink_to(outside)
        cases = [({'MANIFEST_SHA256': '0' * 64}, None, 'differs from manifest_sha256'),
                 ({'MANIFEST_SHA256': 'main'}, None, 'must be a sha256'),
                 ({'MANIFEST': str(self.root/'manifest.json')}, None, 'inbox/<job>/manifest.json'),
                 ({'MANIFEST': str(self.signing/'inbox/../inbox/frankel-job/manifest.json')}, None, 'inbox/<job>/manifest.json'),
                 ({}, {'targetFiles': dict(self.manifest['inputs']['targetFiles'], path=str(outside))}, 'plain file in the manifest directory'),
                 ({}, {'targetFiles': dict(self.manifest['inputs']['targetFiles'], path=str(link))}, 'plain file in the manifest directory'),
                 ({}, {'otatools': dict(self.manifest['inputs']['otatools'], sha256='0' * 64)}, 'input_changed: inputs.otatools'),
                 ({}, 'config', 'input_changed: the signing config')]
        for env, change, message in cases:
            with self.subTest(message=message):
                manifest = json.loads(json.dumps(self.manifest))
                if change == 'config':
                    manifest['config']['sha256'] = '0' * 64
                elif change:
                    manifest['inputs'].update(change)
                digest = self.write(manifest)
                result = self.validate(**dict({'MANIFEST_SHA256': digest}, **env))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
        self.write(self.manifest)
        shutil.rmtree(self.signing/'outputs')
        result = self.validate()
        self.assertIn('signer host setup missing', result.stderr)

    def test_committed_job_is_reused_checked_and_staged_and_conflicts_refuse(self):
        # The SYNTHETIC signer commits the job once (signing-job.run), exactly where the workflow's sign step looks.
        source = self.signing/'src'
        job.run(self.manifest_path, self.signing/'jobs', self.signing/'outputs', signer=self.signer, source_root=source,
            lock=lambda path: contextlib.nullcontext(), base_env={})
        self.assertEqual(len(self.calls), 1)
        sign = self.bash(step('Sign in an isolated job workspace under the signing lock'))
        self.assertEqual(sign.returncode, 0, sign.stderr)
        self.assertEqual(json.loads((self.temp/'signing-result.json').read_text())['verdict'], 'reused')  # no credential used
        stage = self.bash(step('Check the committed outputs and stage the public files and receipt'))
        self.assertEqual(stage.returncode, 0, stage.stderr)
        staged = sorted(p.name for p in (self.temp/'signed-public').iterdir())
        self.assertEqual(staged, sorted(job.public_assets('frankel', fixtures.BUILD) + ['signing-receipt.json']))  # unchanged
        target = self.temp/'signed-target'
        self.assertEqual(sorted(p.name for p in target.iterdir()), ['signed-target_files.zip', 'signing-receipt.json'])
        out = self.signing/f'outputs/frankel-{fixtures.BUILD}'  # canonical <device>-<build>, not the run-suffixed inbox name
        self.assertEqual((target/'signing-receipt.json').read_bytes(), (self.temp/'signed-public/signing-receipt.json').read_bytes())
        self.assertEqual((target/'signed-target_files.zip').read_bytes(), (out/'signed-target_files.zip').read_bytes())
        self.assertEqual(json.loads((target/'signing-receipt.json').read_text())['jobId'], f'frankel-{fixtures.BUILD}')
        self.assertIn(f'SIGNED_JOB=frankel-{fixtures.BUILD}', self.github_env.read_text())
        (self.signing/f'outputs/frankel-{fixtures.BUILD}/public/UPDATE.md').write_text('tampered\n')
        shutil.rmtree(self.temp/'signed-public')
        self.assertNotEqual(self.bash(step('Check the committed outputs and stage the public files and receipt')).returncode, 0)
        manifest = json.loads(json.dumps(self.manifest))
        manifest['buildTimestamp'] = '1793400001'
        self.write(manifest)
        conflict = self.bash(step('Sign in an isolated job workspace under the signing lock'))
        self.assertNotEqual(conflict.returncode, 0)
        self.assertEqual(len(self.calls), 1)


class Ingress(LiteralSteps):
    """SIGN-022: the hosted ingress against a SYNTHETIC GitHub API, then stage and the literal validation step."""

    def setUp(self):
        super().setUp()
        import zipfile
        self.zipfile = zipfile
        self.job_dir = self.signing/'inbox/frankel-job'
        self.members = {'unsigned.zip': (self.job_dir/'unsigned.zip').read_bytes(), 'otatools': (self.job_dir/'otatools').read_bytes()}
        self.manifest_bytes = json.dumps(self.manifest).encode()
        self.archive = self.root/'artifact.zip'
        self.build_archive()
        self.artifact = {'id': 77, 'workflow_run': {'id': 4242}, 'expired': False}
        self.run_meta = {'path': '.github/workflows/os-release.yml', 'repository': {'full_name': 'Immortal-Protocols/RoseOS-1'},
            'head_branch': 'main', 'status': 'in_progress', 'head_sha': 'e' * 40, 'run_attempt': 1}
        self.downloaded = []
        saved = dict(job.API)
        self.addCleanup(job.API.update, saved)
        job.API['get'] = self.fake_get
        self.download_source = self.archive
        job.API['download'] = lambda url, destination: (self.downloaded.append(url), shutil.copyfile(self.download_source, destination))
        self.env = {'GITHUB_REPOSITORY': 'Immortal-Protocols/RoseOS-1'}

    def build_archive(self, extra=(), manifest=None, members=None):
        with self.zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('manifest.json', manifest if manifest is not None else self.manifest_bytes)
            for name, data in (members or self.members).items():
                z.writestr(name, data)
            for info, data in extra:
                z.writestr(info, data)

    def fake_get(self, env, path, follow=True):
        if path.endswith('/zip'):
            self.assertFalse(follow)  # the token is never sent to the redirect target
            return 'https://blob.example/short-lived'
        if path.startswith('/actions/artifacts/'):
            return dict(self.artifact, digest='sha256:' + job.sha256_file(self.archive))
        return self.run_meta

    def ingress(self, **changes):
        args = dict(source_run='4242', artifact_id='77', artifact_digest='sha256:' + job.sha256_file(self.archive),
            manifest_sha256=hashlib.sha256(self.manifest_bytes).hexdigest(), out=self.root/f'bundle-{len(self.downloaded)}',
            signing_root=self.signing, request_id=f'4242.1.frankel-{fixtures.BUILD}')
        args.update(changes)
        return job.ingress(self.env, **args)

    def test_ingress_stage_and_validate_bind_the_artifact_end_to_end(self):
        shutil.rmtree(self.job_dir)  # ingress, not the caller, puts bytes on the signer
        provenance = self.ingress()
        self.assertEqual((provenance['sourceRun'], provenance['artifactId'], provenance['headSha']), ('4242', '77', 'e' * 40))
        self.assertEqual((provenance['requestId'], provenance['sourceAttempt']), (f'4242.1.frankel-{fixtures.BUILD}', '1'))
        bundle = self.root/'bundle-0'
        self.assertEqual(sorted(p.name for p in bundle.iterdir()), ['manifest.json', 'otatools', 'provenance.json', 'unsigned.zip'])
        manifest = job.stage(bundle, self.signing)
        self.assertEqual(manifest, str(self.job_dir/'manifest.json'))
        self.assertEqual(job.stage(bundle, self.signing), manifest)  # a retry with identical bytes
        ok = self.validate(MANIFEST=manifest)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        (self.job_dir/'otatools').write_bytes(b'other bytes')
        with self.assertRaisesRegex(job.Refused, 'stage_refused: the inbox job directory already holds other bytes'):
            job.stage(bundle, self.signing)

    def test_ingress_refuses_wrong_provenance_or_members(self):
        cases = [
            (lambda: self.artifact.update(workflow_run={'id': 1}), "named run's live artifact"),
            (lambda: self.artifact.update(expired=True), "named run's live artifact"),
            (lambda: self.run_meta.update(path='.github/workflows/os-sign.yml'), "os-release run on main"),
            (lambda: self.run_meta.update(head_branch='feature'), "os-release run on main"),
            (lambda: self.run_meta.update(repository={'full_name': 'fork/RoseOS-1'}), "os-release run on main"),
            (lambda: self.build_archive(extra=[('notes.txt', b'x')]), 'members must be exactly'),
            (lambda: self.build_archive(extra=[('sub/unsigned.zip', b'x')]), 'nested paths or duplicates'),
            (lambda: self.build_archive(members={'unsigned.zip': self.members['unsigned.zip']}), 'members must be exactly'),
            (lambda: self.build_archive(members=dict(self.members, otatools=b'changed')), 'member otatools differs'),
        ]
        for damage, message in cases:
            with self.subTest(message=message):
                self.setUp()
                damage()
                with self.assertRaisesRegex(job.Refused, message):
                    self.ingress()
        self.setUp()
        with self.assertRaisesRegex(job.Refused, 'differs from manifest_sha256'):
            self.ingress(manifest_sha256='0' * 64)
        other = self.root/'other.zip'
        shutil.copyfile(self.archive, other)
        with self.zipfile.ZipFile(other, 'a') as z:
            z.writestr('padding', b'x')
        self.download_source = other  # the API names one digest, the redirect serves other bytes
        with self.assertRaisesRegex(job.Refused, 'differs from the artifact digest'):
            self.ingress()
        self.download_source = self.archive
        for bad in ({'source_run': 'main'}, {'artifact_id': '0'}, {'artifact_digest': 'deadbeef'}, {'manifest_sha256': 'x'}):
            with self.subTest(bad=bad), self.assertRaisesRegex(job.Refused, 'is not exact'):
                self.ingress(**bad)
        good_manifest = self.manifest_bytes  # of the current fixture root
        two_dirs = json.loads(good_manifest)
        two_dirs['inputs']['otatools']['path'] = str(self.signing/'inbox/other/otatools')
        self.manifest_bytes = json.dumps(two_dirs).encode()
        self.build_archive(manifest=self.manifest_bytes)
        with self.assertRaisesRegex(job.Refused, 'one inbox job directory'):
            self.ingress()
        foreign = json.loads(good_manifest)
        foreign['source'] = {'run': '9', 'headSha': 'e' * 40}
        self.manifest_bytes = json.dumps(foreign).encode()
        self.build_archive(manifest=self.manifest_bytes)
        with self.assertRaisesRegex(job.Refused, 'names another source run'):
            self.ingress()

    def test_request_id_must_name_the_source_run_attempt_device_and_build(self):
        build = fixtures.BUILD
        for request, message in ((f'4243.1.frankel-{build}', 'names another source run'),
                                 (f'4242.2.frankel-{build}', 'another attempt of the source run'),
                                 (f'4242.1.rango-{build}', 'another device or build'),
                                 (f'4242.1.frankel-2026103199', 'another device or build'),
                                 (f'4242.1.frankel-{build}-4242', 'must be <source_run>'),
                                 ('OS signing 4242', 'must be <source_run>'), (None, 'must be <source_run>')):
            with self.subTest(request=request), self.assertRaisesRegex(job.Refused, message):
                self.ingress(request_id=request, out=self.root/f'r-{abs(hash(str(request)))}')
        self.run_meta['run_attempt'] = 2
        self.assertEqual(self.ingress(request_id=f'4242.2.frankel-{build}')['sourceAttempt'], '2')

    def test_symlink_and_duplicate_members_refuse(self):
        link = self.zipfile.ZipInfo('otatools')
        link.external_attr = (0o120777 << 16)
        self.build_archive(members={'unsigned.zip': self.members['unsigned.zip']}, extra=[(link, b'/etc/passwd')])
        with self.assertRaisesRegex(job.Refused, 'links'):
            self.ingress()
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            self.build_archive(extra=[('otatools', self.members['otatools'])])
        with self.assertRaisesRegex(job.Refused, 'duplicates'):
            self.ingress()


del fixtures.SigningJob  # its own tests run in test_signing_job.py
del fixtures.NotesInput

if __name__ == '__main__':
    unittest.main()
