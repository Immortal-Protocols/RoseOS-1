#!/usr/bin/env python3
"""RELEASE020 M2 transport: the builder's signing-job package and acceptance of the signer's outputs (no network)."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rt = load('release_transport', 'scripts/release-transport.py')
sj = load('signing_job', 'scripts/signing-job.py')
OS_SIGN = (ROOT/'.github/workflows/os-sign.yml').read_text()
CANDIDATE = '5dd3d54ff84ff76646a01a5dad01929d0ecf7710'
IDENTITIES = {k: json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())['identities'][k]
              for k in ('otaCertificateSha256', 'avbPublicKeySha256')}


class Fixture:
    def __init__(self, test):
        self.dir = Path(tempfile.mkdtemp(prefix='rose-transport-'))
        test.addCleanup(shutil.rmtree, self.dir, True)
        (self.dir/'release').mkdir()
        (self.dir/'release/unsigned-target_files.zip').write_bytes(b'unsigned target files')
        (self.dir/'otatools.zip').write_bytes(b'otatools')
        (self.dir/'UPDATE.md').write_text('RoseOS Pixel 10 (frankel) Beta build 2026103004\n')
        self.out = self.dir/'out'

    def package(self, **change):
        args = dict(release_dir=self.dir/'release', otatools=self.dir/'otatools.zip', device='frankel', build='2026103004',
            timestamp='1793390000', candidate=CANDIDATE, notes=self.dir/'UPDATE.md', config='os/release-pipeline/frankel.json',
            job_id='frankel-2026103004-41112579187', out=self.out)
        args.update(change)
        return rt.package(**args)


class Package(unittest.TestCase):
    def test_manifest_is_a_signing_job_with_signer_inbox_paths(self):
        f = Fixture(self)
        result = f.package()
        job = f.out/'frankel-2026103004-41112579187'
        data = (job/'manifest.json').read_bytes()
        self.assertEqual(result['manifestSha256'], hashlib.sha256(data).hexdigest())
        m = json.loads(data)
        for key, name in rt.INPUTS:
            self.assertEqual(m['inputs'][key]['path'], f'/srv/rose-signing/inbox/frankel-2026103004-41112579187/{name}')
            self.assertEqual(m['inputs'][key]['sha256'], hashlib.sha256((job/name).read_bytes()).hexdigest())
        self.assertEqual(m['config'], {'path': 'os/release-pipeline/frankel.json',
            'sha256': hashlib.sha256((ROOT/'os/release-pipeline/frankel.json').read_bytes()).hexdigest()})
        # signing-job.py's own validator and config binding accept it (the signer-side contract, unchanged).
        manifest = job/'manifest.json'
        self.assertEqual(sj.load_manifest(manifest)['build'], '2026103004')
        self.assertEqual(sj.load_config(sj.load_manifest(manifest))['target']['device'], 'frankel')
        self.assertEqual(oct(job.stat().st_mode & 0o777), '0o700')

    def test_paths_match_the_os_sign_inbox_check(self):
        f = Fixture(self)
        f.package()
        m = json.loads((f.out/'frankel-2026103004-41112579187/manifest.json').read_text())
        pattern = re.escape('/srv/rose-signing/inbox') + r'/[A-Za-z0-9._-]+/manifest\.json'
        self.assertIn("re.fullmatch(re.escape(str(inbox)) + r'/[A-Za-z0-9._-]+/manifest\\.json', manifest)", OS_SIGN)
        self.assertIn('SIGNING_ROOT: /srv/rose-signing', OS_SIGN)
        manifest_path = '/srv/rose-signing/inbox/frankel-2026103004-41112579187/manifest.json'
        self.assertRegex(manifest_path, pattern)
        for spec in m['inputs'].values():
            self.assertEqual(str(Path(spec['path']).parent), str(Path(manifest_path).parent))

    def test_refusals(self):
        f = Fixture(self)
        for change, error in (({'device': 'Frankel'}, rt.Invalid), ({'build': '20261030'}, rt.Invalid),
                              ({'candidate': 'x' * 40}, rt.Invalid), ({'job_id': '../x'}, rt.Invalid),
                              ({'job_id': '..'}, rt.Invalid), ({'config': '/etc/passwd'}, rt.Invalid),
                              ({'signing_root': 'srv'}, rt.Invalid), ({'device': 'rango'}, rt.Refused),
                              ({'otatools': f.dir/'missing.zip'}, rt.Refused), ({'build': '2026103009'}, rt.Refused)):
            with self.subTest(change=change), self.assertRaises(error):
                f.package(**change)
        f.package()
        with self.assertRaisesRegex(rt.Refused, 'job_exists'):
            f.package()


def signed_outputs(test, f, **receipt_change):
    """The os-sign artifact layout: signing-receipt.json plus the public files, as signing-job.py commits them."""
    m = json.loads((f.out/'frankel-2026103004-41112579187/manifest.json').read_text())
    signed = f.dir/'signed'
    signed.mkdir()
    for name in rt.public_assets('frankel', '2026103004'):
        (signed/name).write_bytes((f.dir/'UPDATE.md').read_bytes() if name == 'UPDATE.md' else name.encode())
    receipt = {'schema': rt.RECEIPT_SCHEMA, 'jobId': 'frankel-2026103004', 'verdict': 'signed', **{k: m[k] for k in
        ('device', 'build', 'buildTimestamp', 'candidateSha', 'config', 'inputs')}, 'identities': IDENTITIES,
        'outputs': {'signed-target_files.zip': hashlib.sha256(b'signed target files').hexdigest(), **{f'public/{p.name}': rt.sha256_file(p) for p in signed.iterdir()}}}
    receipt.update(receipt_change)
    (signed/'signing-receipt.json').write_text(json.dumps(receipt))
    return signed


class Accept(unittest.TestCase):
    def test_bound_outputs_are_accepted(self):
        f = Fixture(self)
        f.package()
        result = rt.accept(f.out/'frankel-2026103004-41112579187', signed_outputs(self, f))
        self.assertEqual((result['jobId'], result['build'], result['identities']), ('frankel-2026103004', '2026103004', IDENTITIES))

    def test_unbound_or_changed_outputs_refuse(self):
        f = Fixture(self)
        f.package()
        job = f.out/'frankel-2026103004-41112579187'
        m = json.loads((job/'manifest.json').read_text())
        for change, error in (({'verdict': 'FAIL'}, 'verdict'), ({'build': '2026103005'}, 'binding'),
                              ({'candidateSha': 'c' * 40}, 'binding'),
                              ({'inputs': dict(m['inputs'], otatools=dict(m['inputs']['otatools'], sha256='d' * 64))}, 'binding'),
                              ({'schema': 'other'}, 'schema'),
                              ({'jobId': 'frankel-2026103004-41112579187'}, 'job_id'),
                              ({'identities': dict(IDENTITIES, otaCertificateSha256='0' * 64)}, 'identity')):
            signed = signed_outputs(self, f, **change)
            with self.subTest(change=list(change)), self.assertRaisesRegex(rt.Refused, error):
                rt.accept(job, signed)
            shutil.rmtree(signed)
        signed = signed_outputs(self, f)
        (signed/'frankel-stable').write_text('tampered')
        with self.assertRaisesRegex(rt.Refused, 'output_changed'):
            rt.accept(job, signed)
        shutil.rmtree(signed)
        signed = signed_outputs(self, f)
        (signed/'extra.bin').write_text('x')
        with self.assertRaisesRegex(rt.Refused, 'outputs'):
            rt.accept(job, signed)

    def test_main_exit_codes(self):
        f = Fixture(self)
        self.assertEqual(rt.main(['accept', '--job', str(f.dir/'none'), '--signed', str(f.dir/'none')]), 1)
        self.assertEqual(rt.main(['package', '--release-dir', str(f.dir/'release'), '--otatools', str(f.dir/'otatools.zip'),
            '--device', 'frankel', '--build', 'bad', '--timestamp', '1793390000', '--candidate', CANDIDATE, '--notes',
            str(f.dir/'UPDATE.md'), '--config', 'os/release-pipeline/frankel.json', '--job-id', 'j', '--out', str(f.out)]), 2)


def target_artifact(f, signed, data=b'signed target files'):
    """The private signed-target-<job>-<run> artifact: the signed target-files and the identical receipt copy."""
    target = f.dir/'target'
    target.mkdir()
    (target/'signed-target_files.zip').write_bytes(data)
    shutil.copyfile(signed/'signing-receipt.json', target/'signing-receipt.json')
    return target


class AcceptTarget(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(self)
        self.f.package()
        self.job = self.f.out/'frankel-2026103004-41112579187'
        self.signed = signed_outputs(self, self.f)

    def test_both_artifacts_bound_to_one_receipt(self):
        target = target_artifact(self.f, self.signed)
        result = rt.accept(self.job, self.signed, target)
        self.assertEqual(result['signedTargetFilesSha256'], hashlib.sha256(b'signed target files').hexdigest())

    def test_tampered_missing_extra_or_unequal_receipt_refuse(self):
        cases = [(lambda t: (t/'signed-target_files.zip').write_bytes(b'tampered'), 'differs from the signing receipt'),
                 (lambda t: (t/'signed-target_files.zip').unlink(), 'exactly'),
                 (lambda t: (t/'key.pem').write_text('x'), 'exactly'),
                 (lambda t: (t/'signing-receipt.json').write_text('{}'), 'copies differ')]
        for mutate, error in cases:
            target = target_artifact(self.f, self.signed)
            mutate(target)
            with self.subTest(error=error), self.assertRaisesRegex(rt.Refused, error):
                rt.accept(self.job, self.signed, target)
            shutil.rmtree(target)

    def test_target_in_the_public_artifact_is_pollution(self):
        shutil.copyfile(target_artifact(self.f, self.signed)/'signed-target_files.zip', self.signed/'signed-target_files.zip')
        with self.assertRaisesRegex(rt.Refused, 'outputs'):
            rt.accept(self.job, self.signed, self.f.dir/'target')

    def test_place_installs_exactly_what_verify_signed_reads(self):
        target = target_artifact(self.f, self.signed)
        release = self.f.dir/'release'
        result = rt.place(self.job, self.signed, target, release)
        self.assertEqual(result['signer'], 'dedicated')
        self.assertEqual(sorted(p.name for p in (release/'public').iterdir()), sorted(rt.public_assets('frankel', '2026103004')))
        self.assertEqual((release/'signed-target_files.zip').read_bytes(), b'signed target files')
        self.assertEqual((release/'dedicated-signing-receipt.json').read_bytes(), (self.signed/'signing-receipt.json').read_bytes())
        with self.assertRaisesRegex(rt.Refused, 'place_exists'):
            rt.place(self.job, self.signed, target, release)

    def test_place_never_installs_unaccepted_bytes(self):
        target = target_artifact(self.f, self.signed, data=b'other')
        release = self.f.dir/'release'
        with self.assertRaises(rt.Refused):
            rt.place(self.job, self.signed, target, release)
        self.assertFalse((release/'public').exists() or (release/'signed-target_files.zip').exists())


class Response:
    def __init__(self, status, body=None):
        self.status, self.body = status, body

    def read(self):
        return b'' if self.body is None else json.dumps(self.body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeActions:
    """os-sign runs appear after the dispatch and complete after `polls` listings."""

    def __init__(self, conclusion='success', polls=2, existing=(), dispatch=204, appear=True, artifacts=None):
        self.conclusion, self.polls, self.runs, self.dispatch, self.appear = conclusion, polls, list(existing), dispatch, appear
        self.artifacts, self.calls, self.lists = artifacts, [], 0

    def __call__(self, request, timeout):
        url, method = request.full_url, request.get_method()
        self.calls.append((method, url, json.loads(request.data) if request.data else None))
        assert request.headers['Authorization'] == 'Bearer short-lived'
        if method == 'POST':
            if isinstance(self.dispatch, Exception):
                raise self.dispatch
            if self.appear:
                body = self.calls[-1][2]
                self.runs.append({'id': 9001, 'display_title': rt.sign_run_name(body['inputs']['request_id']),
                    'status': 'queued', 'conclusion': None, 'head_branch': 'main', 'event': 'workflow_dispatch'})
            return Response(self.dispatch)
        if '/artifacts' in url:
            return Response(200, {'artifacts': self.artifacts or []})
        self.lists += 1
        for run in self.runs:
            if run['id'] == 9001 and self.lists > self.polls:
                run.update(status='completed', conclusion=self.conclusion)
        return Response(200, {'workflow_runs': self.runs})

    def posts(self):
        return [c for c in self.calls if c[0] == 'POST']


ENV = {'GITHUB_API_URL': 'https://api.github.com', 'GITHUB_REPOSITORY': 'Immortal-Protocols/RoseOS-1', 'GH_TOKEN': 'short-lived',
       'GITHUB_RUN_ID': '41115854376'}
INPUTS = {'source_run': '41115854376', 'artifact_id': '13001', 'artifact_digest': 'sha256:' + 'e' * 64,
          'manifest_sha256': 'f' * 64, 'request_id': '41115854376.1.frankel-2026103004'}


class Request(unittest.TestCase):
    def run_request(self, gh, inputs=INPUTS, **kw):
        ticks = iter(range(0, 100000, 30))
        return rt.request(ENV, dict(inputs), opener=gh, clock=lambda: next(ticks), pause=lambda s: None, **kw)

    def test_dispatches_once_and_waits_for_the_matching_run(self):
        gh = FakeActions()
        self.assertEqual(self.run_request(gh), '9001')
        (method, url, body), = gh.posts()
        self.assertTrue(url.endswith('/actions/workflows/os-sign.yml/dispatches'))
        self.assertEqual(body, {'ref': 'main', 'inputs': INPUTS})

    def test_existing_run_is_awaited_not_dispatched_again(self):
        run = {'id': 9001, 'display_title': rt.sign_run_name(INPUTS['request_id']), 'status': 'in_progress',
               'conclusion': None, 'head_branch': 'main', 'event': 'workflow_dispatch'}
        gh = FakeActions(existing=[run])
        self.assertEqual(self.run_request(gh), '9001')
        self.assertEqual(gh.posts(), [])

    def test_failed_duplicate_untrusted_and_timeout_refuse(self):
        with self.assertRaisesRegex(rt.Refused, 'signing_failed'):
            self.run_request(FakeActions(conclusion='failure'))
        twin = {'id': 9002, 'display_title': rt.sign_run_name(INPUTS['request_id']), 'status': 'queued',
                'conclusion': None, 'head_branch': 'main', 'event': 'workflow_dispatch'}
        with self.assertRaisesRegex(rt.Refused, 'duplicate'):
            self.run_request(FakeActions(existing=[twin, dict(twin, id=9003)]))
        with self.assertRaisesRegex(rt.Refused, 'signing_timeout'):
            self.run_request(FakeActions(appear=False), timeout=120)
        evil = {'id': 9001, 'display_title': rt.sign_run_name(INPUTS['request_id']), 'status': 'completed',
                'conclusion': 'success', 'head_branch': 'feature', 'event': 'workflow_dispatch'}
        with self.assertRaisesRegex(rt.Refused, 'signing_untrusted'):
            self.run_request(FakeActions(existing=[evil]))

    def test_refused_dispatch_is_not_retried(self):
        import io
        import urllib.error
        gh = FakeActions(dispatch=urllib.error.HTTPError('u', 403, 'Forbidden', {}, io.BytesIO(b'')))
        with self.assertRaisesRegex(rt.Refused, 'dispatch_refused: HTTP 403'):
            self.run_request(gh)
        self.assertEqual(len(gh.posts()), 1)

    def test_uncertain_dispatch_waits_and_never_redispatches(self):
        import urllib.error
        gh = FakeActions(dispatch=urllib.error.URLError('reset'), appear=False)
        with self.assertRaisesRegex(rt.Refused, 'signing_timeout'):
            self.run_request(gh, timeout=120)
        self.assertEqual(len(gh.posts()), 1)

    def test_bad_inputs(self):
        for change in ({'request_id': '1.1.frankel-2026103004'}, {'request_id': 'x'}, {'source_run': '1'},
                       {'artifact_id': 'abc'}, {'manifest_sha256': 'f' * 63}, {'artifact_digest': ''}):
            with self.subTest(change=change), self.assertRaises(rt.Invalid):
                self.run_request(FakeActions(), inputs=dict(INPUTS, **change))

    def test_signed_artifacts_are_the_two_canonical_ones(self):
        public, target = 'signed-frankel-2026103004-9001', 'signed-target-frankel-2026103004-9001'
        base = [{'id': 77, 'name': public, 'digest': 'sha256:' + 'a' * 64, 'expired': False},
                {'id': 79, 'name': target, 'digest': 'sha256:' + 'c' * 64, 'expired': False},
                {'id': 78, 'name': 'signed-frankel-2026103004-41115854376-9001', 'expired': False}]
        self.assertEqual(rt.signed_artifacts(ENV, '9001', 'frankel-2026103004', opener=FakeActions(artifacts=base)),
                         {'public': ('77', public, 'sha256:' + 'a' * 64), 'target': ('79', target, 'sha256:' + 'c' * 64)})
        for artifacts in (base[:1], base[1:], [dict(base[0], expired=True), base[1]], base + [dict(base[1], id=80)]):
            with self.subTest(artifacts=artifacts), self.assertRaisesRegex(rt.Refused, 'artifact_missing'):
                rt.signed_artifacts(ENV, '9001', 'frankel-2026103004', opener=FakeActions(artifacts=artifacts))
        # the run-suffixed producer directory name is never the signed job id
        with self.assertRaises(rt.Invalid):
            rt.signed_artifacts(ENV, '9001', 'frankel-2026103004-41115854376', opener=FakeActions(artifacts=base))


class Workflow(unittest.TestCase):
    def test_packaging_is_opt_in_local_and_before_signing(self):
        wf = (ROOT/'.github/workflows/os-release.yml').read_text()
        builder = wf.split('\n  builder:\n', 1)[1].split('\n  app-builder:\n', 1)[0]
        step = builder.split('      - name: Package the signing job for the dedicated signer (opt-in)\n', 1)[1].split('\n      - name:', 1)[0]
        self.assertIn("        if: inputs.mode == 'latest-beta' && (vars.ROSE_SIGNING_TRANSPORT == 'package' || vars.ROSE_SIGNING_TRANSPORT == 'dedicated')\n", step)
        self.assertIn('python3 scripts/release-transport.py package', step)
        for forbidden in ('secrets.', 'gcp-login', 'upload-artifact', 'scp ', 'ssh ', 'curl'):
            self.assertNotIn(forbidden, step)
        order = [builder.index(n) for n in ('- name: Verify the unsigned build', '- name: Package the signing job',
                                            '- name: Signer access for OS signing', '- name: Sign\n')]
        self.assertEqual(order, sorted(order))


class DedicatedWiring(unittest.TestCase):
    """os-release.yml M2c: producer -> request-signing -> signed-builder -> existing publisher, default-off."""

    def setUp(self):
        wf = (ROOT/'.github/workflows/os-release.yml').read_text()
        self.wf = wf
        body = wf.split('\njobs:\n', 1)[1]
        self.jobs = dict(re.findall(r'(?m)^  ([a-z][\w-]*):\n((?:(?:    .*|)\n)*)', body + '\n'))

    def test_dedicated_mode_is_default_off_and_latest_beta_only(self):
        self.assertIn("      ROSE_DEDICATED_SIGNER: ${{ inputs.mode == 'latest-beta' && vars.ROSE_SIGNING_TRANSPORT == 'dedicated' }}\n",
                      self.jobs['builder'])
        self.assertIn("    if: inputs.mode == 'latest-beta' && needs.builder.outputs.dedicated == 'true'\n", self.jobs['request-signing'])
        self.assertIn("    if: inputs.mode == 'latest-beta' && needs.request-signing.result == 'success'\n", self.jobs['signed-builder'])

    def test_builder_never_signs_on_the_dedicated_route(self):
        builder = self.jobs['builder']
        for step in ('Signer access for OS signing', 'Sign', 'Verify the signed candidate', 'Stage only public files and receipts',
                     'Transfer'):
            block = builder.split(f'      - name: {step}\n', 1)[1].split('\n      - ', 1)[0]
            self.assertIn("env.ROSE_DEDICATED_SIGNER != 'true'", block.split('\n')[0], step)
        self.assertIn('name: signing-job-${{ env.ROSE_DEVICE }}-${{ steps.candidate.outputs.build_number }}-${{ github.run_id }}', builder)

    def test_request_signing_is_secret_free_and_uses_the_exact_contract(self):
        job = self.jobs['request-signing']
        self.assertIn('    permissions:\n      actions: write\n      contents: read\n    env:', job)
        for forbidden in ('secrets.', 'id-token', 'gcp-login', 'self-hosted'):
            self.assertNotIn(forbidden, job)
        self.assertIn('--request-id "$GITHUB_RUN_ID.$GITHUB_RUN_ATTEMPT.$JOB_ID" --job-id "$JOB_ID"', job)
        self.assertIn('--artifact-digest "sha256:${{ needs.builder.outputs.signing_artifact_digest }}"', job)
        self.assertIn("      JOB_ID: ${{ inputs.device || 'frankel' }}-${{ needs.builder.outputs.build_number }}\n", job)
        self.assertIn('--target "$RUNNER_TEMP/accepted/target"', job)
        for key in ('public', 'target'):
            self.assertIn(f'artifact-ids: ${{{{ steps.sign.outputs.{key}_artifact_id }}}}', job)
        self.assertEqual(job.count('run-id: ${{ steps.sign.outputs.sign_run }}'), 2)

    def test_signed_builder_places_then_runs_the_unchanged_verification(self):
        job = self.jobs['signed-builder']
        self.assertIn('    permissions:\n      contents: read\n    env:', job)
        for forbidden in ('secrets.', 'id-token', 'gcp-login', 'actions: write'):
            self.assertNotIn(forbidden, job)
        order = [job.index(n) for n in ('sign -- \\\n            python3 scripts/release-transport.py place',
                                        'bash scripts/os-release-stage.sh ota-verify verify-signed',
                                        'bash scripts/os-release-stage.sh transfer stage-artifact', '      - name: Transfer')]
        self.assertEqual(order, sorted(order))
        self.assertIn('cmp "$SIGNING_JOB/manifest.json" "$RUNNER_TEMP/accepted/manifest.json"', job)

    def test_publisher_takes_the_dedicated_route_only_after_signed_builder(self):
        publisher = self.jobs['publisher']
        self.assertIn('    needs: [builder, signed-builder]\n', publisher)
        self.assertIn("(needs.signed-builder.result == 'success' || (needs.builder.outputs.dedicated != 'true' && "
                      "needs.signed-builder.result == 'skipped'))", publisher)
        self.assertIn('request-signing, signed-builder, publisher]', self.jobs['cleanup'])


if __name__ == '__main__':
    unittest.main()
