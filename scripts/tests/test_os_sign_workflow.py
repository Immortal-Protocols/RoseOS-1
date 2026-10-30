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

    def test_the_approved_federation_and_service_account(self):
        self.assertIn('  GCP_WORKLOAD_IDENTITY_PROVIDER: projects/1072799993443/locations/global/workloadIdentityPools/roseos-signing/providers/github\n',
            WORKFLOW)
        self.assertIn('  SIGNER_SERVICE_ACCOUNT: roseos-dedicated-signer@protocols-465723.iam.gserviceaccount.com\n', WORKFLOW)
        self.assertNotIn('roseos-build-signer', WORKFLOW)
        self.assertNotIn('roseos-actions', WORKFLOW)

    def test_validation_precedes_access_and_access_is_always_dropped(self):
        order = [WORKFLOW.index(s) for s in ('name: Validate the manifest, inputs and config (before any credential)',
            'actions-gcp-login.py login "$SIGNER_SERVICE_ACCOUNT"', 'signing-job.py" run --manifest', 'name: Drop signer access',
            'signing-job.py check --output-dir', 'name: Public outputs and receipt')]
        self.assertEqual(order, sorted(order))
        self.assertIn('      - name: Drop signer access\n        if: always()\n        run: python3 scripts/actions-gcp-login.py logout\n', WORKFLOW)
        self.assertIn('        with:\n          persist-credentials: false\n', WORKFLOW)
        for uses in re.findall(r'uses: (\S+)', WORKFLOW):
            self.assertRegex(uses, r'@[0-9a-f]{40}$')
        self.assertNotIn('signed-target_files', step('Check the committed outputs and stage the public files and receipt'))


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
        self.assertEqual(staged, sorted(job.public_assets('frankel', fixtures.BUILD) + ['signing-receipt.json']))
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


del fixtures.SigningJob  # its own tests run in test_signing_job.py

if __name__ == '__main__':
    unittest.main()
