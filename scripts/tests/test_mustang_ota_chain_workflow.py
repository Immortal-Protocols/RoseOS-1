"""MUSTANG-OTA one-button chain: os-release.yml mode mustang-ota and scripts/mustang-ota-chain.py (Release-owned).

Stand-ins only: no network, token, host, signing or dispatch.
- The binding runs on canned GitHub run metadata, the committed os/ota/mustang.json and the committed bootstrap
  receipt. A fixed `now` falls inside the recorded signing window, so no configuration is invented.
- The workflow is read from the file itself. The existing Mustang modes and helpers are unchanged, and their own tests
  still cover them."""
import contextlib
import copy
import datetime
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os_release_shape as shape  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = shape.text()
HELPER = ROOT/'scripts/mustang-ota-chain.py'
spec = importlib.util.spec_from_file_location('mustang_ota_chain', HELPER)
chain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chain)

RUN, SHA, BOOTSTRAP = '41800000000', 'ab' * 20, '41719073037'
CFG = json.loads((ROOT/'os/ota/mustang.json').read_text())
RECIPE = json.loads((ROOT/'os/signing-bootstrap/mustang-bootstrap-receipt.json').read_text())['recipeSha']
# The committed access is the verified unconditional grant (no window). A recorded window still bounds signing: the
# earlier windowed approval, as a synthetic configuration.
WINDOWED = copy.deepcopy(CFG)
WINDOWED['signingAccess'].update(expiresAt='2026-11-05T06:20:18Z', approvalRef='synthetic windowed approval')
EXPIRY = datetime.datetime.fromisoformat(WINDOWED['signingAccess']['expiresAt'].replace('Z', '+00:00'))
INSIDE = EXPIRY - datetime.timedelta(hours=1)  # an hour left: the 30-minute rule passes
ENV = {'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_RUN_ID': RUN,
       'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_SHA': SHA}
# The first step of mustang-build and of mustang-signer in mode mustang-ota.
ONCE = ("    steps:\n      - name: mustang-ota runs once (a re-run attempt never {verb})\n"
        "        if: inputs.mode == 'mustang-ota'\n        run: |\n"
        '          if [[ "$GITHUB_RUN_ATTEMPT" != 1 ]]; then\n'
        '            echo "::error::mustang-ota runs once: re-run attempts are refused; dispatch mustang-ota again"\n'
        '            exit 1\n          fi\n')


def responses():
    return {
        f'/actions/runs/{RUN}': {'path': '.github/workflows/os-release.yml', 'head_branch': 'main', 'event': 'workflow_dispatch',
                                 'run_attempt': 1, 'display_title': f'mustang-ota {RUN}', 'head_sha': SHA,
                                 'status': 'in_progress'},
        f'/actions/runs/{RUN}/jobs?filter=latest&per_page=100': {'total_count': 3, 'jobs': [
            {'name': 'guard', 'status': 'completed', 'conclusion': 'success', 'run_attempt': 1, 'id': 1},
            {'name': 'mustang-build', 'status': 'completed', 'conclusion': 'success', 'run_attempt': 1, 'id': 7},
            {'name': 'mustang-ota-bind', 'status': 'in_progress', 'conclusion': None, 'run_attempt': 1, 'id': 8}]},
        f'/actions/runs/{RUN}/artifacts?per_page=100': {'total_count': 2, 'artifacts': [
            {'name': f'mustang-receipts-{RUN}-1', 'expired': False, 'id': 9, 'digest': 'sha256:' + 'c' * 64,
             'expires_at': '2026-11-11T00:00:00Z'},
            {'name': f'mustang-ota-baseline-prebuild-{RUN}-1', 'expired': False, 'id': 10}]},
        f'/actions/runs/{BOOTSTRAP}': {'head_sha': RECIPE, 'conclusion': 'success', 'status': 'completed', 'run_attempt': 1,
                                       'path': '.github/workflows/os-release.yml', 'head_branch': 'main'}}


class Binding(unittest.TestCase):
    def setUp(self):
        self.api, self.calls = responses(), []

    def request(self, path):
        self.calls.append(path)
        if isinstance(self.api.get(path), Exception):
            raise self.api[path]
        return copy.deepcopy(self.api[path])

    def bind(self, env=None, cfg=None, now=INSIDE, ancestor=lambda sha: True):
        return chain.bind(env=dict(ENV, **(env or {})), request=self.request, ancestor=ancestor,
                          cfg=CFG if cfg is None else cfg, now=now)

    def refused(self, message, **kwargs):
        with self.assertRaisesRegex(chain.Refused, message):
            self.bind(**kwargs)

    def test_binds_exactly_this_runs_successful_producer_and_the_original_bootstrap(self):
        binding = self.bind()
        self.assertEqual(binding, {
            'mode': 'mustang-ota', 'recipeSource': SHA,
            'compile': {'runId': RUN, 'runAttempt': 1, 'job': 'mustang-build', 'jobId': 7, 'source': SHA, 'artifactId': 9,
                        'artifactName': f'mustang-receipts-{RUN}-1', 'artifactDigest': 'sha256:' + 'c' * 64,
                        'expiresAt': '2026-11-11T00:00:00Z', 'collectionRecovery': False},
            'bootstrap': {'runId': BOOTSTRAP, 'source': RECIPE, 'receipt': 'os/signing-bootstrap/mustang-bootstrap-receipt.json'},
            'signingAccessExpiresAt': CFG['signingAccess']['expiresAt']})
        self.assertEqual(CFG['bootstrapRun'], BOOTSTRAP)  # the committed original, never an input
        with tempfile.TemporaryDirectory() as tmp:
            out, outputs = Path(tmp)/'binding.json', Path(tmp)/'outputs'
            chain.write(binding, out, env={'GITHUB_OUTPUT': str(outputs)})
            self.assertEqual(json.loads(out.read_text()), binding)
            self.assertEqual(outputs.read_text(), f'compile_source={SHA}\ncompile_recovery=false\nbootstrap_source={RECIPE}\n')
            with self.assertRaises(FileExistsError):  # write-once
                chain.write(binding, out, env={})

    def test_the_signing_window_is_checked_before_any_metadata_is_read(self):
        for now in (EXPIRY - datetime.timedelta(minutes=29), EXPIRY, EXPIRY + datetime.timedelta(days=1)):
            with self.subTest(now=now):
                self.refused('signing access: approved signing read-access window expired', now=now, cfg=WINDOWED)
        self.assertEqual(self.calls, [])
        self.bind(now=INSIDE, cfg=WINDOWED)
        self.calls.clear()
        no_record = copy.deepcopy(CFG)
        no_record['signingAccess']['approvalRef'] = WINDOWED['signingAccess']['approvalRef']
        self.refused('signing access: non-expiring Mustang signing read access requires a recorded unconditional',
                     cfg=no_record)
        self.assertEqual(self.calls, [])
        unapproved = copy.deepcopy(CFG)
        unapproved['signingAccess']['status'] = 'requested'
        self.refused('signing access: existing Mustang version-1 signing read access requires an approved', cfg=unapproved)
        inactive = dict(CFG, publicationEnabled=False)
        self.refused('signing access: Mustang public OTA activation remains pending', cfg=inactive)
        self.assertEqual(self.calls, [])

    def test_the_verified_unconditional_access_binds_after_the_old_window(self):
        binding = self.bind(now=EXPIRY + datetime.timedelta(days=1))
        self.assertIsNone(binding['signingAccessExpiresAt'])
        self.assertIsNone(CFG['signingAccess']['expiresAt'])

    def test_only_attempt_1_of_a_manual_main_dispatch_is_bound(self):
        for env, message in (({'GITHUB_REF': 'refs/heads/feature'}, 'manual workflow_dispatch run on main'),
                             ({'GITHUB_EVENT_NAME': 'workflow_run'}, 'manual workflow_dispatch run on main'),
                             ({'GITHUB_RUN_ATTEMPT': '2'},
                              r'attempt 1 only: a re-run is never a producer \(dispatch mustang-ota again\)'),
                             ({'GITHUB_RUN_ID': '0'}, 'positive numeric run id'),
                             ({'GITHUB_RUN_ID': 'x1'}, 'positive numeric run id'),
                             ({'GITHUB_SHA': 'ab' * 19}, 'full commit id')):
            with self.subTest(env=env):
                self.refused(message, env=env)
        self.assertEqual(self.calls, [])

    def test_the_run_must_be_this_mustang_ota_dispatch_on_main(self):
        for key, value in (('display_title', f'mustang-build {RUN}'), ('display_title', f'mustang-ota {RUN}1'),
                           ('path', '.github/workflows/other.yml'), ('head_branch', 'feature'), ('event', 'push'),
                           ('run_attempt', 2), ('head_sha', 'cd' * 20)):
            with self.subTest(key=key, value=value):
                self.setUp()
                self.api[f'/actions/runs/{RUN}'][key] = value
                self.refused('not attempt 1 of the manual mustang-ota dispatch')
        self.setUp()
        self.refused('not an ancestor of origin/main', ancestor=lambda sha: False)

    def test_exactly_one_successful_attempt_1_producer_job(self):
        jobs = f'/actions/runs/{RUN}/jobs?filter=latest&per_page=100'
        for change in ({'conclusion': 'failure'}, {'conclusion': 'skipped'}, {'conclusion': 'cancelled'},
                       {'status': 'in_progress', 'conclusion': None}, {'run_attempt': 2}, {'name': 'mustang-signer'}):
            with self.subTest(change=change):
                self.setUp()
                self.api[jobs]['jobs'][1].update(change)
                self.refused('no single successful attempt-1 mustang-build producer job')
        self.setUp()
        self.api[jobs]['jobs'].append(dict(self.api[jobs]['jobs'][1], id=70))
        self.refused('no single successful attempt-1 mustang-build producer job')
        self.setUp()
        self.api[jobs]['total_count'] = 101
        self.refused('job listing is incomplete')

    def test_exactly_one_unexpired_receipts_artifact_of_this_run(self):
        artifacts = f'/actions/runs/{RUN}/artifacts?per_page=100'
        for change in ({'expired': True}, {'expired': None}, {'name': f'mustang-receipts-{RUN}-2'},
                       {'name': 'mustang-receipts-41799999999-1'}):
            with self.subTest(change=change):
                self.setUp()
                self.api[artifacts]['artifacts'][0].update(change)
                self.refused('exactly one unexpired mustang-receipts-')
        self.setUp()
        self.api[artifacts]['artifacts'].append(dict(self.api[artifacts]['artifacts'][0], id=11))
        self.refused('exactly one unexpired mustang-receipts-')
        self.setUp()
        self.api[artifacts]['total_count'] = 101
        self.refused('artifact listing is incomplete')

    def test_the_bootstrap_is_the_committed_original_exactly_as_the_delivery_checks_it(self):
        for key, value in (('head_sha', 'ef' * 20), ('conclusion', 'failure'), ('status', 'in_progress'), ('run_attempt', 2),
                           ('path', '.github/workflows/other.yml'), ('head_branch', 'feature')):
            with self.subTest(key=key, value=value):
                self.setUp()
                self.api[f'/actions/runs/{BOOTSTRAP}'][key] = value
                self.refused('original completed bootstrap metadata differs')
        self.setUp()
        self.refused('positive numeric run id', cfg=dict(CFG, bootstrapRun='0'))

    def test_unavailable_metadata_refuses_without_echoing_details(self):
        for error in (urllib.error.URLError('down'), ValueError('workflow token required'), OSError('reset')):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                self.api[f'/actions/runs/{RUN}'] = error
                self.refused(f'run metadata unavailable: {type(error).__name__}$')

    def test_the_cli_refuses_with_exit_2_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'binding.json'
            env = {k: v for k, v in os.environ.items() if not k.startswith('GITHUB_') and k not in ('GH_TOKEN',)}
            result = subprocess.run([sys.executable, str(HELPER), 'bind', '--out', str(out)], capture_output=True, text=True,
                                    cwd=ROOT, env=env)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('"refused"', result.stdout)
            self.assertFalse(out.exists())
            self.assertEqual(subprocess.run([sys.executable, str(HELPER), 'bind'], capture_output=True).returncode, 2)

    def test_the_manual_delivery_mode_never_delivers_a_chain_run(self):
        # The documented failure handling relies on this: mustang-delivery pins only mustang-build producer runs, so a
        # chain run (titled mustang-ota <run>) is never delivered by hand and its compile is never reused.
        source = (ROOT/'scripts/mustang-delivery.py').read_text()
        self.assertIn("f'mustang-receipts-{number(compile_run)}-1','mustang-build ',allow_collection_recovery=True)", source)
        module = chain.delivery()
        run = dict(self.api[f'/actions/runs/{RUN}'], status='completed', conclusion='success')
        module.request = lambda path: copy.deepcopy(run)
        with self.assertRaisesRegex(ValueError, 'not the completed single-attempt main workflow'):
            module.pin_run(RUN, '.github/workflows/os-release.yml', f'mustang-receipts-{RUN}-1', 'mustang-build ',
                           allow_collection_recovery=True)
        docs = (ROOT/'docs/OS-RELEASE.md').read_text()
        self.assertIn('The manual `mustang-delivery` mode cannot deliver a `mustang-ota` run', docs)
        self.assertNotIn('deliver it by hand', docs + HELPER.read_text())

    def test_a_configuration_failure_is_a_refusal_with_exit_2(self):
        original = chain.mustang_ota.config
        for error in (ValueError('unsupported Mustang OTA configuration'), FileNotFoundError('os/ota/mustang.json')):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as tmp:
                def broken(error=error):
                    raise error
                chain.mustang_ota.config = broken
                try:
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout):
                        code = chain.main(['bind', '--out', str(Path(tmp)/'binding.json')])
                finally:
                    chain.mustang_ota.config = original
                self.assertEqual(code, 2)
                self.assertEqual(list(json.loads(stdout.getvalue())), ['refused'])
                self.assertFalse((Path(tmp)/'binding.json').exists())

    def test_the_helper_imports_the_mustang_helpers_unchanged_and_writes_nothing_else(self):
        source = HELPER.read_text()
        self.assertIn("ROOT/'scripts/mustang-delivery.py'", source)
        self.assertIn('mustang_ota.guard(cfg, access=True, now=now)', source)
        for absent in ('actions/workflows', 'dispatches', "method='POST'", 'urlopen', 'secrets', 'gcloud'):
            self.assertNotIn(absent, source)


class Guard(unittest.TestCase):
    BASE = shape.dispatch_env(WORKFLOW, MODE='mustang-ota', MUSTANG_PUBLISH_OTA='true', PROMOTE='false', APP_PUBLISH='false',
                              ALPHA_RECOVER='false', GITHUB_SHA=SHA, GITHUB_RUN_ATTEMPT='1')

    def run_guard(self, **change):
        body = re.search(r'- name: Candidate is an exact commit on main\n(?:.*\n)*?        run: \|\n((?: {10}.*\n)+)',
                         shape.jobs(WORKFLOW)['guard'])[1]
        script = '\n'.join(line[10:] for line in body.splitlines() if 'git merge-base' not in line)
        env = {'PATH': os.environ['PATH'], **self.BASE, **change}
        return subprocess.run(['bash', '-euo', 'pipefail', '-c', script], env=env, capture_output=True, text=True, cwd=ROOT)

    def test_admits_exactly_the_bare_publishing_dispatch(self):
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        for change in ({'MUSTANG_PUBLISH_OTA': 'false'}, {'CANDIDATE_SHA': SHA}, {'CANDIDATE_SHA': 'cd' * 20},
                       {'MUSTANG_COMPILE_RUN': '41800000001'}, {'MUSTANG_BOOTSTRAP_RUN': BOOTSTRAP}, {'BUILD_NUMBER': '2026110501'},
                       {'BUILD_TIMESTAMP': '1793900000'}, {'APPS': '[["dev.pixel.voice",1]]'}, {'PROMOTE': 'true'},
                       {'RESUME_STAGE': 'sign'}, {'RESUME_SOURCE_RUN': '1'}, {'CAMPAIGN': 'start'}, {'APP_PUBLISH': 'true'},
                       {'ROSE_RANGO_PUBLISH': 'true'}, {'ROSE_FIRST_USE_ENROLL': 'true'}, {'BOOTSTRAP_PROFILE': 'pro-mustang-v1'},
                       {'REUSE_BINDING': '{}'}, {'ALPHA_RECOVER': 'true'}, {'ROSE_DEVICE': 'rango'},
                       {'BLAZER_PREP_STAGE': 'source'}):
            with self.subTest(change=change):
                self.assertNotEqual(self.run_guard(**change).returncode, 0)

    def test_a_re_run_attempt_is_refused_before_anything_starts(self):
        for attempt in ('2', '3', ''):
            with self.subTest(attempt=attempt):
                self.assertNotEqual(self.run_guard(GITHUB_RUN_ATTEMPT=attempt).returncode, 0)
        # the existing modes keep their own re-run behaviour
        self.assertEqual(self.run_guard(MODE='mustang-build', GITHUB_RUN_ATTEMPT='2').returncode, 0)
        self.assertEqual(self.run_guard(MODE='mustang-delivery', MUSTANG_COMPILE_RUN='41750459835',
                                        MUSTANG_BOOTSTRAP_RUN=BOOTSTRAP, GITHUB_RUN_ATTEMPT='2').returncode, 0)

    def test_the_existing_mustang_modes_are_admitted_as_before(self):
        self.assertEqual(self.run_guard(MODE='mustang-build', CANDIDATE_SHA=SHA).returncode, 0)  # the automatic producer
        self.assertEqual(self.run_guard(MODE='mustang-build').returncode, 0)  # the manual producer
        self.assertEqual(self.run_guard(MODE='mustang-delivery', MUSTANG_COMPILE_RUN='41750459835',
                                        MUSTANG_BOOTSTRAP_RUN=BOOTSTRAP).returncode, 0)
        self.assertNotEqual(self.run_guard(MODE='mustang-delivery').returncode, 0)  # still needs both run ids
        self.assertNotEqual(self.run_guard(MODE='mustang-bootstrap').returncode, 0)  # publication is OTA modes only

    def test_the_first_window_check_and_the_hosted_producer_check_run_before_the_builder(self):
        guard = shape.jobs(WORKFLOW)['guard']
        self.assertIn('      - name: Mustang OTA activation and installed baseline preflight\n'
                      '        if: inputs.mustang_publish_ota\n        run: python3 scripts/mustang_ota.py guard\n', guard)
        self.assertIn("      - name: Mustang source configuration\n        if: inputs.mode == 'mustang-build' || "
                      "inputs.mode == 'mustang-ota'\n        run: python3 scripts/mustang-pipeline.py describe\n", guard)
        # the hand-typed producer binding and the automatic admission stay with their own modes
        self.assertIn("        if: inputs.mode == 'mustang-bootstrap' || inputs.mode == 'mustang-delivery'\n", guard)
        self.assertIn("        if: inputs.mode == 'mustang-build' && inputs.candidate_sha != ''\n", guard)
        self.assertEqual(re.search(r'(?m)^    needs: (.+)$', shape.jobs(WORKFLOW)['controller'])[1], 'guard')

    def test_the_mode_is_manual_selectable_and_titled_for_the_binding(self):
        self.assertIn('mustang-ota', shape.modes(WORKFLOW))
        self.assertIn('mustang-ota (manual only, the Mustang one-button OTA)', WORKFLOW)
        run_name = re.search(r'(?m)^run-name: (.+)$', WORKFLOW)[1]
        # neither the automatic producer title nor the delivery title can be a mustang-ota run's title
        self.assertIn("inputs.mode == 'mustang-build' && inputs.candidate_sha != '' && format('mustang-build auto-{0}'", run_name)
        self.assertIn("inputs.mode == 'mustang-delivery' && inputs.mustang_publish_ota && format('mustang-delivery ota-{0}'",
                      run_name)
        self.assertIn("startsWith(inputs.mode, 'mustang-') && format('{0} {1}', inputs.mode, github.run_id)", run_name)
        self.assertIn("f'{MODE} {run_id}'", HELPER.read_text())  # the title the binding requires


class Jobs(unittest.TestCase):
    def setUp(self):
        self.jobs = shape.jobs(WORKFLOW)

    def test_the_producer_is_the_existing_job_with_its_fresh_allocation(self):
        job = self.jobs['mustang-build']
        self.assertIn("    if: (inputs.mode == 'mustang-build' || inputs.mode == 'mustang-ota') && "
                      "needs.guard.outputs.mustang_skip != 'true'\n", job)
        self.assertIn('        run: python3 scripts/mustang-pipeline.py run --run-id "$GITHUB_RUN_ID" --run-attempt '
                      '"$GITHUB_RUN_ATTEMPT"\n', job)
        self.assertIn('          name: mustang-receipts-${{ github.run_id }}-${{ github.run_attempt }}\n', job)

    def test_the_bind_job_is_hosted_read_only_and_runs_only_after_a_successful_producer(self):
        job = self.jobs['mustang-ota-bind']
        head = '\n'.join(line for line in job.split('    steps:\n', 1)[0].splitlines() if not line.strip().startswith('#'))
        self.assertEqual(head, "    needs: [guard, mustang-build]\n    if: inputs.mode == 'mustang-ota' && "
                         "needs.mustang-build.result == 'success'\n    runs-on: ubuntu-24.04\n    timeout-minutes: 10\n"
                         '    permissions:\n      contents: read\n      actions: read\n    outputs:\n'
                         '      compile_source: ${{ steps.bind.outputs.compile_source }}\n'
                         '      compile_recovery: ${{ steps.bind.outputs.compile_recovery }}\n'
                         '      bootstrap_source: ${{ steps.bind.outputs.bootstrap_source }}')
        self.assertIn('        run: python3 scripts/mustang-ota-chain.py bind --out "$RUNNER_TEMP/mustang-ota-chain-binding.json"\n',
                      job)
        self.assertIn('          fetch-depth: 0\n          persist-credentials: false\n', job)
        for absent in ('id-token', 'secrets.', 'actions-gcp-login', 'GCP_', 'self-hosted', 'actions: write'):
            self.assertNotIn(absent, job)

    def test_the_signer_binds_this_runs_producer_and_checks_the_window_again_right_before_login(self):
        job = self.jobs['mustang-signer']
        self.assertIn('    needs: [guard, controller, mustang-ota-bind]\n    if: >-\n'
                      "      !cancelled() && needs.guard.result == 'success' && needs.controller.result == 'success' &&\n"
                      "      (inputs.mode == 'mustang-delivery' || (inputs.mode == 'mustang-ota' && "
                      "needs.mustang-ota-bind.result == 'success'))\n", job)
        for name, ota, manual in (('EXPECTED_COMPILE_RUN', 'github.run_id', 'inputs.mustang_compile_run'),
                                  ('EXPECTED_COMPILE_SOURCE', 'needs.mustang-ota-bind.outputs.compile_source',
                                   'needs.guard.outputs.mustang_compile_source'),
                                  ('EXPECTED_COMPILE_RECOVERY', 'needs.mustang-ota-bind.outputs.compile_recovery',
                                   'needs.guard.outputs.mustang_compile_recovery'),
                                  ('EXPECTED_BOOTSTRAP_SOURCE', 'needs.mustang-ota-bind.outputs.bootstrap_source',
                                   'needs.guard.outputs.mustang_bootstrap_source')):
            self.assertIn(f"      {name}: ${{{{ inputs.mode == 'mustang-ota' && {ota} || {manual} }}}}\n", job)
        self.assertIn("          name: mustang-receipts-${{ inputs.mode == 'mustang-ota' && github.run_id || "
                      "inputs.mustang_compile_run }}-1\n          run-id: ${{ inputs.mode == 'mustang-ota' && github.run_id || "
                      'inputs.mustang_compile_run }}\n', job)
        check = ("      - name: Signing read-access window still open (the chain's third check, right before signer login)\n"
                 "        if: inputs.mode == 'mustang-ota'\n        run: python3 scripts/mustang_ota.py guard\n"
                 '      - run: python3 scripts/actions-gcp-login.py login "$GCP_SIGNER_SERVICE_ACCOUNT"\n')
        self.assertIn(check, job)
        self.assertEqual(job.count('actions-gcp-login.py login'), 1)
        self.assertLess(job.index('Observe actual installed-baseline package and OTA trust before login'), job.index(check))
        self.assertIn('    permissions:\n      contents: read\n      actions: read\n      id-token: write\n', job)  # unchanged

    def test_every_chain_job_refuses_a_re_run_attempt(self):
        # "Re-run all jobs" stops in the guard; "re-run failed jobs" stops in the first chain job it would repeat.
        self.assertIn('            [[ "$MODE" != mustang-ota || "${GITHUB_RUN_ATTEMPT:-}" == 1 ]]\n', self.jobs['guard'])
        self.assertIn("    if env.get('GITHUB_RUN_ATTEMPT') != '1':\n", HELPER.read_text())  # mustang-ota-bind
        for name, verb in (('mustang-build', 'compiles'), ('mustang-signer', 'signs')):
            step = ONCE.format(verb=verb)
            self.assertIn(step, self.jobs[name])  # its first step: before checkout, collection or login
            script = '\n'.join(line[10:] for line in step.splitlines()[4:])
            for attempt, code in (('1', 0), ('2', 1), ('', 1)):
                with self.subTest(job=name, attempt=attempt):
                    result = subprocess.run(['bash', '-e', '-c', script], capture_output=True, text=True,
                                            env={'PATH': os.environ['PATH'], 'GITHUB_RUN_ATTEMPT': attempt})
                    self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        # the publisher needs this attempt's own staged OTA, which only a signer of this attempt uploads
        self.assertIn('          name: mustang-ota-${{ github.run_id }}-${{ github.run_attempt }}\n',
                      self.jobs['mustang-ota-publisher'])

    def test_publication_and_packaging_routes_are_the_existing_jobs_with_explicit_results(self):
        self.assertIn("    needs: mustang-signer\n    if: >-\n      !cancelled() && needs.mustang-signer.result == 'success' &&\n"
                      "      (inputs.mode == 'mustang-delivery' || inputs.mode == 'mustang-ota') && inputs.mustang_publish_ota\n",
                      self.jobs['mustang-ota-publisher'])
        self.assertIn("    needs: mustang-signer\n    if: >-\n      !cancelled() && needs.mustang-signer.result == 'success' && "
                      "inputs.mode == 'mustang-delivery' && inputs.mustang_publish_ota != true\n", self.jobs['mustang-packager'])
        self.assertIn("    needs: mustang-packager\n    if: >-\n      !cancelled() && needs.mustang-packager.result == 'success' && "
                      "inputs.mode == 'mustang-delivery'\n", self.jobs['mustang-delivered'])

    def test_no_permission_is_added_anywhere(self):
        # actions: write stays only where it already was (request-signing and next-release); no Mustang job has it
        self.assertEqual(sorted(name for name, body in self.jobs.items() if 'actions: write' in body),
                         ['next-release', 'request-signing'])
        self.assertNotIn('id-token', self.jobs['mustang-ota-bind'])
        tokens = sorted(name for name, body in self.jobs.items() if name.startswith('mustang') and 'id-token: write' in body)
        self.assertEqual(tokens, ['mustang-bootstrap', 'mustang-signer'])  # the existing signer logins only

    def test_owned_start_and_stop_and_cleanup_cover_the_chain(self):
        self.assertIn('mustang-ota', shape.owned_modes(WORKFLOW))  # all five ownership conditions agree
        self.assertIn('mustang-ota-bind', shape.cleanup_needs(WORKFLOW))
        self.assertIn('mustang-ota', shape.main_check_exempt_modes(WORKFLOW))  # its own branch checks GITHUB_SHA is on main
        self.assertIn('            git merge-base --is-ancestor "$GITHUB_SHA" origin/main\n', self.jobs['guard'])

    def test_the_automatic_dispatcher_is_untouched(self):
        dispatcher = (ROOT/'.github/workflows/mustang-ota-auto.yml').read_text()
        self.assertIn("    if: vars.MUSTANG_OTA_AUTO == 'on' && github.event.workflow_run.conclusion == 'success'\n", dispatcher)
        source = (ROOT/'scripts/mustang-ota-auto.py').read_text()
        self.assertNotIn('mustang-ota ', source)  # it chains only its own automatic titles, never a manual chain run
        self.assertIn("title == 'mustang-build auto-'", source)


TOKEN = re.compile(r"\s*(?:(\|\||&&|==|!=|!|\(|\))|'((?:[^']|'')*)'|([A-Za-z_][\w.-]*(?:\(\))?))")
STATUS = ('success()', 'always()', 'cancelled()', 'failure()')


def evaluate(expression, context, status):
    """A GitHub expression of the operators these job conditions use (|| && == != ! parentheses, 'strings',
    true/false, dotted context paths and the status functions), with GitHub's value-returning && and ||."""
    tokens, position = [], 0
    while position < len(expression):
        match = TOKEN.match(expression, position)
        if not match or match.end() == position:
            if expression[position:].strip():
                raise ValueError(f'cannot parse {expression[position:]!r}')
            break
        tokens.append(match.groups())
        position = match.end()
    tokens = [(op, None if text is None else text.replace("''", "'"), name) for op, text, name in tokens]
    index = 0

    def peek():
        return tokens[index][0] if index < len(tokens) else None

    def take():
        nonlocal index
        index += 1
        return tokens[index - 1]

    def truthy(value):
        return value not in (None, False, '', 0)

    def either():
        left = both()
        while peek() == '||':
            take()
            right = both()
            left = left if truthy(left) else right
        return left

    def both():
        left = compare()
        while peek() == '&&':
            take()
            right = compare()
            left = right if truthy(left) else left
        return left

    def compare():
        left = unary()
        while peek() in ('==', '!='):
            op = take()[0]
            right = unary()
            same = left.lower() == right.lower() if isinstance(left, str) and isinstance(right, str) else left == right
            left = same if op == '==' else not same
        return left

    def unary():
        if peek() == '!':
            take()
            return not truthy(unary())
        if peek() == '(':
            take()
            value = either()
            assert take()[0] == ')'
            return value
        op, text, name = take()
        assert op is None, op
        if text is not None:
            return text
        if name in ('true', 'false'):
            return name == 'true'
        if name.endswith('()'):
            return status[name]
        value = context
        for part in name.split('.'):
            value = value.get(part) if isinstance(value, dict) else None
        return value

    value = either()
    assert index == len(tokens), tokens[index:]
    return truthy(value)


class Graph(unittest.TestCase):
    """Which Mustang jobs run in each mode, read from the workflow's own needs: and if: lines. GitHub's documented rule
    for jobs.<job_id>.needs: a job whose if: uses no status function is gated by success(), and a failure or skip
    applies to every job in the dependency chain from that point on. Every job that runs is taken to succeed."""
    ORDER = ('guard', 'controller', 'mustang-build', 'mustang-ota-bind', 'mustang-bootstrap', 'mustang-signer',
             'mustang-ota-publisher', 'mustang-packager', 'mustang-delivered')

    @staticmethod
    def needs_and_condition(body):
        needs = re.search(r'(?m)^    needs: (.+)$', body)
        needs = [] if not needs else [n.strip() for n in needs[1].strip('[]').split(',')]
        lines = body.splitlines()
        for i, line in enumerate(lines):
            if line.startswith('    if: '):
                value = line[len('    if: '):]
                if value == '>-':
                    block = []
                    for following in lines[i + 1:]:
                        if not following.startswith('      '):
                            break
                        block.append(following.strip())
                    value = ' '.join(block)
                return needs, value
        return needs, ''

    def plan(self, mode, publish, workflow=WORKFLOW):
        jobs, results = shape.jobs(workflow), {}

        def ancestors(name):
            seen, stack = set(), list(graph[name][0])
            while stack:
                job = stack.pop()
                if job not in seen:
                    seen.add(job)
                    stack.extend(graph[job][0])
            return seen

        graph = {name: self.needs_and_condition(jobs[name]) for name in self.ORDER}
        for name in self.ORDER:
            needs, condition = graph[name]
            status = {'success()': all(results[job] == 'success' for job in ancestors(name)), 'always()': True,
                      'cancelled()': False, 'failure()': False}
            if not any(function in condition for function in STATUS):
                condition = f'success() && ({condition or "true"})'
            context = {'inputs': {'mode': mode, 'mustang_publish_ota': publish},
                       'github': {'event_name': 'workflow_dispatch', 'ref': 'refs/heads/main'},
                       'needs': {job: {'result': results[job], 'outputs': {'mustang_skip': ''}} for job in needs}}
            results[name] = 'success' if evaluate(condition, context, status) else 'skipped'
        return {name for name, result in results.items() if result == 'success'}

    def test_each_mode_runs_exactly_its_own_jobs(self):
        base = {'guard', 'controller'}
        expected = {
            ('mustang-ota', True): base | {'mustang-build', 'mustang-ota-bind', 'mustang-signer', 'mustang-ota-publisher'},
            ('mustang-delivery', True): base | {'mustang-signer', 'mustang-ota-publisher'},
            ('mustang-delivery', False): base | {'mustang-signer', 'mustang-packager', 'mustang-delivered'},
            ('mustang-build', True): base | {'mustang-build'},
            ('mustang-build', False): base | {'mustang-build'},
            ('mustang-bootstrap', False): base | {'mustang-bootstrap'},
            ('latest-beta', False): base}
        for (mode, publish), jobs in expected.items():
            with self.subTest(mode=mode, publish=publish):
                self.assertEqual(self.plan(mode, publish), jobs)

    def test_the_model_sees_a_skip_carried_down_the_chain(self):
        # the defect the explicit results fix: with implicit success() below the signer, a manual mustang-delivery
        # would sign and then skip publication, packaging and the delivered receipt
        implicit = WORKFLOW.replace("      !cancelled() && needs.mustang-signer.result == 'success' &&\n      (inputs.mode", '      (inputs.mode')
        implicit = implicit.replace("      !cancelled() && needs.mustang-signer.result == 'success' && inputs.mode == 'mustang-delivery' && "
                                    'inputs.mustang_publish_ota != true', '      inputs.mustang_publish_ota != true')
        implicit = implicit.replace("      !cancelled() && needs.mustang-packager.result == 'success' && inputs.mode == 'mustang-delivery'",
                                    '      true')
        self.assertNotEqual(implicit, WORKFLOW)
        self.assertEqual(self.plan('mustang-delivery', True, implicit), {'guard', 'controller', 'mustang-signer'})
        self.assertEqual(self.plan('mustang-delivery', False, implicit), {'guard', 'controller', 'mustang-signer'})

    def test_the_evaluator_follows_github_operators(self):
        status = {'success()': True, 'always()': True, 'cancelled()': False, 'failure()': False}
        context = {'inputs': {'mode': 'mustang-ota', 'mustang_publish_ota': True}, 'needs': {'a': {'result': 'skipped'}}}
        for expression, value in (("inputs.mode == 'MUSTANG-OTA'", True), ("!cancelled() && needs.a.result == 'success'", False),
                                  ("(inputs.mode == 'x' || inputs.mustang_publish_ota) && true", True),
                                  ('inputs.mustang_publish_ota != true', False), ("needs.b.result != 'skipped'", True),
                                  ("'it''s' == 'it''s'", True)):
            with self.subTest(expression=expression):
                self.assertEqual(evaluate(expression, context, status), value)


if __name__ == '__main__':
    unittest.main()
