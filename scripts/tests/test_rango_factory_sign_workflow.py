"""FOLD-ARCHIVE-SIGN workflow wiring (scripts/rango-factory-sign-wiring.py, os-release.yml mode rango-factory-sign):
the hosted check passes Signing's own exit 4 through, sign runs exactly Signing's command for the retained ZIP and its
committed pin, report uploads only the signature and its receipt, and the job is the only factory job with a signer
login. Text, stand-ins and the real unbound check only: no host, lock, secret or signature."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
MODE = 'rango-factory-sign'
spec = importlib.util.spec_from_file_location('rango_factory_sign_wiring', ROOT/'scripts/rango-factory-sign-wiring.py')
wiring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wiring)


def jobs():
    body = WORKFLOW.split('\njobs:\n', 1)[1]
    return dict(re.findall(r'(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)', body))


def step(job, name):
    match = re.search(r'(?ms)^      - name: ' + re.escape(name) + r'\n(.*?)(?=^      - |\Z)', job)
    assert match, name
    return match[1]


def code(text):
    return '\n'.join(line for line in text.splitlines() if not line.strip().startswith('#'))


class Result:
    def __init__(self, code, out=''):
        self.returncode, self.stdout, self.stderr = code, out, ''


class Wiring(unittest.TestCase):
    def test_the_committed_bindings_are_unbound_and_signings_missing_list_passes_through(self):
        result = subprocess.run([sys.executable, str(ROOT/'scripts/rango-factory-sign-wiring.py'), 'check'],
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (4, ''))
        self.assertIn('factorySigning principal/publicKey', result.stderr)

    def test_bounds_and_a_ready_signer(self):
        self.assertEqual(wiring.check(run=lambda *a, **k: Result(0)), (2010, 44))
        with self.assertRaises(wiring.Unbound):
            wiring.check(run=lambda *a, **k: Result(4, '{"ready": false, "missing": ["x"]}'))
        with self.assertRaises(wiring.pkg.Invalid):
            wiring.check(run=lambda *a, **k: Result(2))

    def test_sign_is_exactly_signings_command_for_the_retained_zip(self):
        argv = wiring.sign_argv('41700000000', '1')
        self.assertEqual(argv[1:], [str(ROOT/'scripts/factory-archive-sign.py'), 'sign', '--archive',
            '/home/pixelbuild/rose-actions/rango-factory-package-41515240855-1/package/rango-install-2026103001.zip',
            '--archive-sha256', 'e37db904490a99857dc1fe106b2eac1db12c71b32ac23372cfda474ab70d3b48',
            '--lock', '/home/pixelbuild/frankel-build.lock', '--out', '/home/pixelbuild/rose-actions/rango-factory-sign-41700000000-1'])
        calls = []
        code_ = wiring.main(['sign', '--run-id', '41700000000', '--attempt', '1'],
                            run=lambda argv, **k: calls.append(argv) or Result(3 if len(calls) > 1 else 0))
        self.assertEqual((code_, calls[1]), (3, argv))  # Signing's own refusal is the job's result
        for bad in (['sign', '--run-id', '0', '--attempt', '1'], ['sign', '--run-id', 'x', '--attempt', '1'],
                    ['check', '--run-id', '1'], ['sign', '--run-id', '1', '--attempt', '1', '--upload-dir', '/u']):
            with self.subTest(bad=bad):
                self.assertEqual(wiring.main(bad, run=lambda *a, **k: Result(0)), 2)

    def test_report_uploads_only_the_signature_and_its_receipt(self):
        root = Path(tempfile.mkdtemp())/'sign'
        root.mkdir()
        (root/wiring.SIGNATURE).write_text('sig')
        (root/'work').mkdir()
        (root/'extra.log').write_text('never')
        outside = Path(tempfile.mkdtemp())/'r.json'
        outside.write_text('{}')
        (root/wiring.RECEIPT).symlink_to(outside)
        upload = Path(tempfile.mkdtemp())/'up'
        state = wiring.report(root, upload)
        self.assertEqual((state['uploaded'], state['absent']), ([wiring.SIGNATURE], [wiring.RECEIPT]))
        self.assertEqual(sorted(p.name for p in upload.iterdir()), [wiring.SIGNATURE])
        missing = wiring.report(Path(tempfile.mkdtemp())/'never', Path(tempfile.mkdtemp())/'up2')
        self.assertEqual(missing['uploaded'], [])


class Workflow(unittest.TestCase):
    def test_mode_check_job_login_and_ownership(self):
        self.assertIn('rango-factory-observe, launcher3-compile, blazer-compile, blazer-integrate, rango-factory-sign]\n', WORKFLOW)
        self.assertIn("      - name: Rango factory sign check\n        id: rango-factory-sign\n        if: inputs.mode == "
                      "'rango-factory-sign'\n        run: python3 scripts/rango-factory-sign-wiring.py check >> "
                      "\"$GITHUB_OUTPUT\"\n", jobs()['guard'])
        job = jobs()[MODE]
        self.assertEqual(code(job.split('    steps:\n', 1)[0]), "    needs: [guard, controller]\n    if: inputs.mode == "
            "'rango-factory-sign'\n    runs-on: [self-hosted, roseos-frankel-builder]\n"
            "    timeout-minutes: ${{ fromJSON(needs.guard.outputs.sign_job_minutes || '1') }}\n"
            "    permissions:\n      contents: read\n      id-token: write\n    env:\n"
            "      OUTER_SECONDS: ${{ needs.guard.outputs.sign_outer_seconds }}\n"
            "      GCP_WORKLOAD_IDENTITY_PROVIDER: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}\n"
            "      GCP_SIGNER_SERVICE_ACCOUNT: ${{ vars.GCP_SIGNER_SERVICE_ACCOUNT }}")
        body = code(job)
        for absent in ('secrets.', 'GCP_CONTROLLER', 'host-sign', 'host-factory-package', 'publish', 'rm -rf',
                       'download-artifact', 'blazer'):
            self.assertNotIn(absent, body)
        self.assertEqual(sorted(set(re.findall(r'python3 (\S+)', body))),
                         ['scripts/actions-gcp-login.py', 'scripts/rango-factory-sign-wiring.py'])
        names = re.findall(r'(?m)^      - name: (.+)$', job)
        self.assertEqual(names, ['Recheck the identity, binding and bounds on the builder',
                                 'Signer access for factory-archive signing',
                                 'Sign the retained installer (bounded, under the shared lock)', 'Drop signer access',
                                 'Allowlisted signature and receipt', 'Signature artifact'])
        for name in ('Drop signer access', 'Allowlisted signature and receipt', 'Signature artifact'):
            self.assertIn('        if: always()\n', step(job, name))
        self.assertIn('timeout --kill-after=30s "${OUTER_SECONDS}s" python3 scripts/rango-factory-sign-wiring.py sign '
                      '--run-id "$GITHUB_RUN_ID" --attempt "$GITHUB_RUN_ATTEMPT"\n', job)
        self.assertEqual(job.count('actions/upload-artifact@'), 1)
        for other in ('rango-factory-package', 'rango-factory-inspect', 'rango-factory-observe', 'blazer-prep', 'launcher3-compile'):
            self.assertNotIn('id-token', jobs()[other], other)
        own = "inputs.mode == 'blazer-integrate' || inputs.mode == 'rango-factory-sign'"
        self.assertEqual(jobs()['controller'].count(own), 2)
        self.assertEqual(jobs()['cleanup'].count(own), 1)
        self.assertIn('blazer-integrate, rango-factory-sign, app-builder', re.search(r'(?m)^    needs: (.+)$', jobs()['cleanup'])[1])


if __name__ == '__main__':
    unittest.main()
