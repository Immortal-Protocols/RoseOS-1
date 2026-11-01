"""AGENT-BETA-AUTO part B: scripts/agent-beta-auto.py and .github/workflows/agent-beta-auto.yml.

A fake GitHub API only: no network, token, dispatch or release."""
import importlib.util
import io
import json
from pathlib import Path
import re
import unittest
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('agent_beta_auto', ROOT/'scripts/agent-beta-auto.py')
aba = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aba)
WORKFLOW = (ROOT/'.github/workflows/agent-beta-auto.yml').read_text()
OS_RELEASE = (ROOT/'.github/workflows/os-release.yml').read_text()
SHA = 'a' * 40
CI = {'id': 500, 'name': 'App offline tests', 'path': '.github/workflows/app-tests.yml', 'event': 'push', 'head_branch': 'main',
      'status': 'completed', 'conclusion': 'success', 'head_sha': SHA}
ENV = {'GITHUB_API_URL': 'https://api.github.com', 'GITHUB_REPOSITORY': 'Immortal-Protocols/RoseOS-1', 'GH_TOKEN': 'short-lived',
       'CI_RUN_ID': '500', 'AGENT_BETA_AUTO': 'on'}


class Response:
    def __init__(self, status, body=None):
        self.status, self.body = status, body

    def read(self):
        return b'' if self.body is None else json.dumps(self.body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeGitHub:
    def __init__(self, ci=CI, ci_runs=(), release_runs=(), dispatch=204, appear=True, compare=None):
        self.ci, self.ci_runs, self.release_runs, self.dispatch, self.appear = ci, list(ci_runs), list(release_runs), dispatch, appear
        self.compare = compare
        self.calls = []

    def __call__(self, request, timeout):
        url, method = request.full_url, request.get_method()
        self.calls.append((method, url, json.loads(request.data) if request.data else None))
        assert request.headers['Authorization'] == 'Bearer short-lived'
        if method == 'POST':
            if self.appear:  # the run exists even when the response was lost
                self.release_runs.append({'display_title': aba.run_name(self.calls[-1][2]['inputs']['candidate_sha'])})
            if isinstance(self.dispatch, Exception):
                raise self.dispatch
            return Response(self.dispatch)
        if url.endswith('/actions/runs/500'):
            return Response(200, self.ci)
        if '/actions/workflows/app-tests.yml/runs' in url:
            return Response(200, {'workflow_runs': [CI, *self.ci_runs]})
        if '/actions/workflows/os-release.yml/runs' in url:
            return Response(200, {'workflow_runs': self.release_runs})
        if '/compare/' in url and self.compare is not None:
            return Response(200, self.compare)
        return Response(404)

    def posts(self):
        return [c for c in self.calls if c[0] == 'POST']


class Dispatch(unittest.TestCase):
    def run_dispatch(self, gh, **env):
        return aba.dispatch(dict(ENV, **env), opener=gh, pause=lambda s: None)

    def test_unchanged_rose_agent_sources_since_the_last_auto_release_are_skipped_before_any_host(self):
        prev = 'c' * 40
        done = {'id': 900, 'display_title': aba.run_name(prev), 'status': 'completed', 'conclusion': 'success'}
        docs = {'status': 'ahead', 'files': [{'filename': 'README.md'}, {'filename': 'os/overlays/x.java'}]}
        gh = FakeGitHub(release_runs=[done], compare=docs)
        self.assertEqual(self.run_dispatch(gh), (None, f'unchanged: no Rose Agent source changed since {aba.run_name(prev)}'))
        self.assertEqual(gh.posts(), [])
        self.assertTrue(any(c[1].endswith(f'/compare/{prev}...{SHA}') for c in gh.calls))
        self.assertEqual(aba.voice_sources(), ['workspace/rose-agent', 'workspace/pixel-clipboard', 'workspace/experiments',
                                               'scripts/host-app-build.py'])
        for compare, runs, why in (
                ({'status': 'ahead', 'files': [{'filename': 'workspace/rose-agent/app/Main.java'}]}, [done], 'changed'),
                ({'status': 'ahead', 'files': [{'filename': 'scripts/host-app-build.py'}]}, [done], 'build script'),
                ({'status': 'ahead', 'files': [{'filename': 'README.md'}] * 300}, [done], 'truncated'),
                ({'status': 'diverged', 'files': []}, [done], 'not an ancestor'),
                (None, [done], 'unreadable comparison'),
                (docs, [dict(done, conclusion='failure')], 'the last auto run failed'),
                (docs, [], 'first auto release')):
            with self.subTest(why=why):
                gh = FakeGitHub(release_runs=list(runs), compare=compare)
                self.assertEqual(self.run_dispatch(gh), (SHA, None), why)
                self.assertEqual(len(gh.posts()), 1)

    def test_a_healthy_main_ci_dispatches_the_app_route_once_for_the_tested_sha(self):
        gh = FakeGitHub()
        self.assertEqual(self.run_dispatch(gh), (SHA, None))
        (method, url, body), = gh.posts()
        self.assertTrue(url.endswith('/actions/workflows/os-release.yml/dispatches'))
        self.assertEqual(body, {'ref': 'main', 'inputs': {'mode': 'app-only', 'candidate_sha': SHA, 'apps': 'auto', 'app_publish': 'true'}})

    def test_off_irrelevant_or_unhealthy_ci_never_dispatches(self):
        for change, env, reason in (
                ({}, {'AGENT_BETA_AUTO': ''}, 'off'), ({}, {'AGENT_BETA_AUTO': 'true'}, 'off'),
                ({'event': 'pull_request'}, {}, 'not relevant'), ({'head_branch': 'feature'}, {}, 'not relevant'),
                ({'name': 'Release verification'}, {}, 'not relevant'), ({'path': '.github/workflows/x.yml'}, {}, 'not relevant'),
                ({'conclusion': 'failure'}, {}, 'not healthy'), ({'status': 'in_progress', 'conclusion': None}, {}, 'not healthy')):
            with self.subTest(change=change, env=env):
                gh = FakeGitHub(ci=dict(CI, **change))
                sha, why = self.run_dispatch(gh, **env)
                self.assertIsNone(sha)
                self.assertTrue(why.startswith(reason), why)
                self.assertEqual(gh.posts(), [])

    def test_a_newer_pending_or_successful_main_ci_coalesces(self):
        for newer, dispatched in (({'id': 501, 'status': 'in_progress', 'conclusion': None, 'head_sha': 'b' * 40}, False),
                                  ({'id': 501, 'status': 'completed', 'conclusion': 'success', 'head_sha': 'b' * 40}, False),
                                  ({'id': 501, 'status': 'completed', 'conclusion': 'failure', 'head_sha': 'b' * 40}, True),
                                  ({'id': 499, 'status': 'in_progress', 'conclusion': None, 'head_sha': 'c' * 40}, True)):
            with self.subTest(newer=newer):
                gh = FakeGitHub(ci_runs=[newer])
                sha, why = self.run_dispatch(gh)
                self.assertEqual(sha is not None, dispatched, why)
                self.assertEqual(len(gh.posts()), int(dispatched))

    def test_duplicate_event_never_dispatches_again(self):
        gh = FakeGitHub(release_runs=[{'display_title': aba.run_name(SHA)}])
        self.assertEqual(self.run_dispatch(gh), (None, f'duplicate: {aba.run_name(SHA)} already exists'))
        self.assertEqual(gh.posts(), [])

    def test_refused_or_uncertain_dispatch_is_never_retried(self):
        gh = FakeGitHub(dispatch=urllib.error.HTTPError('u', 403, 'Forbidden', {}, io.BytesIO(b'')), appear=False)
        with self.assertRaisesRegex(aba.Refused, 'dispatch_refused: HTTP 403'):
            self.run_dispatch(gh)
        self.assertEqual(len(gh.posts()), 1)
        gh = FakeGitHub(dispatch=urllib.error.URLError('reset'), appear=False)
        with self.assertRaisesRegex(aba.Refused, 'dispatch_uncertain'):
            self.run_dispatch(gh)
        self.assertEqual(len(gh.posts()), 1)
        gh = FakeGitHub(dispatch=urllib.error.URLError('reset'))
        self.assertEqual(self.run_dispatch(gh), (SHA, None), 'the run became visible: dispatched once')

    def test_unreadable_state_refuses(self):
        with self.assertRaisesRegex(aba.Refused, 'invalid: CI_RUN_ID'):
            self.run_dispatch(FakeGitHub(), CI_RUN_ID='x')
        with self.assertRaisesRegex(aba.Refused, 'invalid: the CI run has no full head SHA'):
            self.run_dispatch(FakeGitHub(ci=dict(CI, head_sha='main')))
        class Down(FakeGitHub):
            def __call__(self, request, timeout):
                raise urllib.error.URLError('down')
        with self.assertRaisesRegex(aba.Refused, 'ci_run_unreadable'):
            self.run_dispatch(Down())


class Workflow(unittest.TestCase):
    def test_trigger_permissions_and_switch(self):
        self.assertIn("on:\n  workflow_run:\n    workflows: ['App offline tests']\n    types: [completed]\n    branches: [main]\n", WORKFLOW)
        self.assertIn('\npermissions: {}\n', WORKFLOW)
        self.assertEqual(re.findall(r'(?m)^      ([\w-]+): (read|write)\b', WORKFLOW), [('actions', 'write'), ('contents', 'read')])
        for absent in ('secrets.', 'id-token', 'contents: write', 'pull_request_target', 'workflow_call'):
            self.assertNotIn(absent, WORKFLOW)
        self.assertIn('AGENT_BETA_AUTO: ${{ vars.AGENT_BETA_AUTO }}', WORKFLOW)
        self.assertIn('      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4\n        with:\n          ref: main\n'
            '          persist-credentials: false\n', WORKFLOW)
        self.assertEqual(re.findall(r'python3 (\S+)', WORKFLOW), ['scripts/agent-beta-auto.py'])

    def test_os_release_names_auto_app_runs_for_dedupe(self):
        self.assertIn("inputs.mode == 'app-only' && inputs.apps == 'auto' && format('Agent Beta auto {0}', inputs.candidate_sha)", OS_RELEASE)
        self.assertEqual(aba.run_name(SHA), f'Agent Beta auto {SHA}')


class RealGuard(unittest.TestCase):
    """The whole os-release guard step 'Candidate is an exact commit on main', run with the dispatcher's own payload.
    git is a stub that answers only the exact-commit-on-main ancestry check (MAIN_SHAS); everything else is the real
    step and the real helpers it calls (release-pipeline resolve_device, release-chain check)."""
    # os-release.yml workflow_dispatch input defaults, as the workflow's top-level env sees them.
    DEFAULTS = {'MODE': 'latest-beta', 'ROSE_DEVICE': 'frankel', 'CANDIDATE_SHA': '', 'BUILD_NUMBER': '', 'BUILD_TIMESTAMP': '',
                'CHANNEL': 'beta', 'APPS': '', 'RESUME_STAGE': 'none', 'RESUME_UNSIGNED_SHA256': '', 'RESUME_SOURCE_RUN': '',
                'RESUME_SIGNED_SHA256': '', 'REUSE_BINDING': '', 'BOOTSTRAP_PROFILE': 'none', 'MODELS_BOOTSTRAP_RUN': '',
                'ROSE_RANGO_PUBLISH': 'false', 'ROSE_FIRST_USE_ENROLL': 'false', 'ROSE_RECOVER_RUN': '', 'CAMPAIGN': '',
                'PROMOTE': 'false', 'APP_PUBLISH': 'false', 'ALPHA_RECOVER': 'false'}
    INPUT_ENV = {'mode': 'MODE', 'candidate_sha': 'CANDIDATE_SHA', 'apps': 'APPS', 'app_publish': 'APP_PUBLISH',
                 'build_number': 'BUILD_NUMBER', 'build_timestamp': 'BUILD_TIMESTAMP', 'promote': 'PROMOTE',
                 'campaign': 'CAMPAIGN', 'resume_failed_stage': 'RESUME_STAGE'}

    def step(self):
        guard = OS_RELEASE.split('\n  guard:\n', 1)[1]
        match = re.search(r'      - name: Candidate is an exact commit on main\n.*?        run: \|\n((?:          [^\n]*\n)+)', guard, re.S)
        self.assertIn('git merge-base --is-ancestor "$CANDIDATE_SHA" origin/main', match[1], 'the exact-commit check is part of the step')
        return 'set -euo pipefail\n' + '\n'.join(line[10:] for line in match[1].splitlines()) + '\n'

    def run_guard(self, inputs, on_main=(SHA,)):
        import os
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            git = Path(tmp)/'git'
            git.write_text('#!/bin/sh\n[ "$1 $2 $4" = "merge-base --is-ancestor origin/main" ] || exit 97\n'
                           'case " $MAIN_SHAS " in *" $3 "*) exit 0;; *) exit 1;; esac\n')
            git.chmod(0o755)
            env = dict(self.DEFAULTS, PATH=f'{tmp}:{os.environ["PATH"]}', GITHUB_SHA='f' * 40, MAIN_SHAS=' '.join(on_main))
            for key, value in inputs.items():
                env[self.INPUT_ENV[key]] = value
            return subprocess.run(['bash', '-c', self.step()], env=env, cwd=ROOT, capture_output=True, text=True).returncode

    def payload(self, **change):
        gh = FakeGitHub()
        aba.dispatch(dict(ENV), opener=gh, pause=lambda s: None)
        (_, _, body), = gh.posts()
        self.assertEqual(body['ref'], 'main')
        return dict(body['inputs'], **change)

    def test_the_dispatchers_exact_payload_passes_the_real_guard(self):
        self.assertEqual(self.run_guard(self.payload()), 0)

    def test_stale_malformed_or_pinned_auto_requests_are_refused(self):
        for name, inputs, on_main in (
                ('candidate not on main (stale or foreign)', self.payload(), ()),
                ('short sha', self.payload(candidate_sha=SHA[:39]), (SHA[:39],)),
                ('branch name', self.payload(candidate_sha='main'), ('main',)),
                ('uppercase sha', self.payload(candidate_sha=SHA.upper()), (SHA.upper(),)),
                ('empty candidate', self.payload(candidate_sha=''), ('',)),
                ('build pin on auto', self.payload(build_number='2026110101'), (SHA,)),
                ('timestamp pin on auto', self.payload(build_timestamp='1793550000'), (SHA,)),
                ('auto outside app-only', self.payload(mode='os-release'), (SHA,)),
                ('promotion', self.payload(promote='true'), (SHA,)),
                ('campaign', self.payload(campaign='start'), (SHA,)),
                ('resume', self.payload(resume_failed_stage='sign'), (SHA,))):
            with self.subTest(name):
                self.assertNotEqual(self.run_guard(inputs, on_main), 0, name)

    def test_explicit_app_only_keeps_its_pins(self):
        explicit = {'mode': 'app-only', 'candidate_sha': SHA, 'apps': '[["dev.pixel.voice", 400]]'}
        self.assertNotEqual(self.run_guard(explicit), 0, 'explicit app-only still needs its build pins')
        self.assertEqual(self.run_guard(dict(explicit, build_number='2026110101', build_timestamp='1793550000')), 0)


if __name__ == '__main__':
    unittest.main()
