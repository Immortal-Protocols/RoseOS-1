"""AGENT-BETA-AUTO part B: scripts/agent-beta-auto.py and .github/workflows/agent-beta-auto.yml.

A fake GitHub API only: no network, token, dispatch or release."""
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import unittest
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os_release_shape as shape  # noqa: E402

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
    def __init__(self, ci=CI, ci_runs=(), release_runs=(), dispatch=204, appear=True, compare=None, main=SHA):
        self.ci, self.ci_runs, self.release_runs, self.dispatch, self.appear = ci, list(ci_runs), list(release_runs), dispatch, appear
        self.compare, self.main = compare, main
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
        if url.endswith('/git/ref/heads/main') and self.main is not None:
            return Response(200, {'ref': 'refs/heads/main', 'object': {'sha': self.main, 'type': 'commit'}})
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

    def test_a_healthy_main_ci_dispatches_the_hosted_route_once_for_the_tested_sha(self):
        gh = FakeGitHub()
        self.assertEqual(self.run_dispatch(gh), (SHA, None))
        (method, url, body), = gh.posts()
        self.assertTrue(url.endswith('/actions/workflows/os-release.yml/dispatches'))
        # Publication policy unchanged: an automatic Agent release publishes its own prerelease, as before.
        self.assertEqual(body, {'ref': 'main', 'inputs': {'mode': 'agent-hosted', 'candidate_sha': SHA, 'app_publish': 'true'}})
        # main's head is read last, right before the one request.
        self.assertTrue(gh.calls[-2][1].endswith('/git/ref/heads/main'), gh.calls[-2])
        self.assertEqual([c[0] for c in gh.calls[:-1]], ['GET'] * (len(gh.calls) - 1))

    def test_a_moved_main_dispatches_nothing_untested(self):
        # agent-hosted builds its dispatch commit, so a tested SHA that is no longer main's head is never dispatched.
        gh = FakeGitHub(main='b' * 40)
        self.assertEqual(self.run_dispatch(gh), (None, f'superseded: main moved to {"b" * 12} after the tested {SHA[:12]}; '
                                                       'agent-hosted builds only its dispatch commit, so nothing is dispatched'))
        self.assertEqual(gh.posts(), [])
        for main, why in ((None, 'unreadable'), ('main', 'not a SHA'), (SHA.upper(), 'not a lowercase SHA')):
            with self.subTest(why):
                gh = FakeGitHub(main=main)
                with self.assertRaisesRegex(aba.Refused, 'main_ref_unreadable'):
                    self.run_dispatch(gh)
                self.assertEqual(gh.posts(), [])

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
                if reason == 'off':
                    self.assertEqual(gh.calls, [], 'off reads and sends nothing')

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
        # Both routes' automatic runs share one title, so the duplicate and unchanged checks span the move to agent-hosted.
        run_name = re.search(r'(?m)^run-name: (.+)$', OS_RELEASE)[1]
        builder = "inputs.mode == 'app-only' && inputs.apps == 'auto' && format('Agent Beta auto {0}', inputs.candidate_sha)"
        hosted = "inputs.mode == 'agent-hosted' && inputs.candidate_sha != '' && format('Agent Beta auto {0}', inputs.candidate_sha)"
        self.assertIn(f'{builder} || {hosted} || ', run_name)
        self.assertEqual(run_name.count("format('Agent Beta auto {0}'"), 2)  # the probe and a bare dispatch are not auto runs
        self.assertLess(run_name.index(hosted), run_name.index("format('os-release {0} {1}'"))
        self.assertEqual(aba.run_name(SHA), f'Agent Beta auto {SHA}')
        self.assertEqual(aba.MODE, 'agent-hosted')


def job_starts(condition, context):
    """GitHub's verdict on a job `if:` of `&&` terms: `false`, `true` or `<context path> == '<literal>'`."""
    for term in condition.split(' && '):
        match = re.fullmatch(r"([\w.]+) == '([^']*)'", term)
        if term not in ('true', 'false') and not match:
            raise AssertionError(f'a term this reader does not evaluate: {term!r}')
        if term == 'false' or match and context.get(match.group(1)) != match.group(2):
            return False
    return True


class TemporaryHold(unittest.TestCase):
    """TEMPORARY HOLD (owner-approved, 2026-11-04): automatic Agent Beta dispatch is paused in source while the shared
    builder's storage is recovered; the AGENT_BETA_AUTO variable is left as it is. Resuming is a reviewed change that
    deletes `false && ` from the dispatch job's `if:` and this class."""
    ORIGINAL = "github.event.workflow_run.event == 'push' && github.event.workflow_run.conclusion == 'success'"

    def condition(self):
        return re.search(r'(?m)^    if: (.*)$', WORKFLOW).group(1)

    def test_the_checked_in_hold_starts_no_dispatch_even_when_the_switch_is_on(self):
        # The dispatch job is the workflow's only job and holds its only request-sending command.
        self.assertEqual(re.findall(r'(?m)^  ([\w-]+):$', WORKFLOW.split('\njobs:\n', 1)[1]), ['dispatch'])
        self.assertEqual(re.findall(r'(?m)^    if: ', WORKFLOW), ['    if: '])
        self.assertEqual(self.condition(), 'false && ' + self.ORIGINAL)
        for event in ('push', 'pull_request'):
            for conclusion in ('success', 'failure'):
                for switch in ('on', 'off', ''):
                    context = {'github.event.workflow_run.event': event, 'github.event.workflow_run.conclusion': conclusion,
                               'vars.AGENT_BETA_AUTO': switch}
                    self.assertFalse(job_starts(self.condition(), context), context)

    def test_the_hold_is_a_literal_that_only_a_reviewed_change_removes(self):
        held, _, rest = self.condition().partition(' && ')
        self.assertEqual(held, 'false')  # no variable, input, date or expression can lift it
        self.assertNotIn('vars.', self.condition())
        self.assertEqual(rest, self.ORIGINAL)
        # Deleting `false && ` restores exactly the gate the Dispatch tests cover.
        self.assertTrue(job_starts(rest, {'github.event.workflow_run.event': 'push',
                                          'github.event.workflow_run.conclusion': 'success'}))
        self.assertIn('AGENT_BETA_AUTO: ${{ vars.AGENT_BETA_AUTO }}', WORKFLOW)

    def test_manual_releases_never_pass_through_the_hold(self):
        # Manual app-only, Frankel and Mustang releases are os-release.yml dispatches; neither it nor the Mustang
        # dispatcher runs this workflow or the dispatcher script, and neither carries a held job.
        for path in ('.github/workflows/os-release.yml', '.github/workflows/mustang-ota-auto.yml'):
            text = (ROOT/path).read_text()
            self.assertNotIn('agent-beta-auto', text, path)
            self.assertIsNone(re.search(r'(?m)^\s+if: false\b', text), path)
        self.assertIn('\n  workflow_dispatch:\n', OS_RELEASE)
        runners = [p.name for p in sorted((ROOT/'.github/workflows').glob('*.yml'))
                   if 'python3 scripts/agent-beta-auto.py' in p.read_text()]
        self.assertEqual(runners, ['agent-beta-auto.yml'])  # the only place the dispatcher runs

    def test_the_reader_evaluates_conditions_as_github_does(self):
        context = {'a.b': 'push'}
        self.assertTrue(job_starts("a.b == 'push'", context))
        self.assertFalse(job_starts("a.b == 'pull'", context))
        self.assertFalse(job_starts("false && a.b == 'push'", context))
        self.assertTrue(job_starts("true && a.b == 'push'", context))
        with self.assertRaises(AssertionError):
            job_starts("a.b != 'push'", context)


class BothHolds(unittest.TestCase):
    """The source hold and the AGENT_BETA_AUTO switch both stay, and each one alone keeps the retargeted dispatcher from
    sending anything: lifting either one is not enough."""
    HELD = re.search(r'(?m)^    if: (.*)$', WORKFLOW)[1]

    def test_the_source_hold_still_guards_the_only_place_the_retargeted_dispatcher_runs(self):
        jobs = dict(re.findall(r'(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)', WORKFLOW.split('\njobs:\n', 1)[1]))
        self.assertEqual(list(jobs), ['dispatch'])
        self.assertIn('        run: python3 scripts/agent-beta-auto.py dispatch\n', jobs['dispatch'])
        self.assertTrue(self.HELD.startswith('false && '), self.HELD)
        for switch in ('on', 'off', ''):  # the variable cannot lift it
            self.assertFalse(job_starts(self.HELD, {'github.event.workflow_run.event': 'push',
                                                    'github.event.workflow_run.conclusion': 'success',
                                                    'vars.AGENT_BETA_AUTO': switch}))
        self.assertIn("'mode': MODE, 'candidate_sha': sha, 'app_publish': 'true'", (ROOT/'scripts/agent-beta-auto.py').read_text())

    def test_without_the_source_hold_the_switch_alone_still_sends_nothing(self):
        lifted = self.HELD.partition(' && ')[2]  # what deleting `false && ` would leave
        self.assertTrue(job_starts(lifted, {'github.event.workflow_run.event': 'push',
                                            'github.event.workflow_run.conclusion': 'success'}))
        for switch in ('', 'off', 'On', 'ON', 'true', '1', 'yes', ' on', 'on ', None):
            with self.subTest(switch=switch):
                env = dict(ENV)
                if switch is None:
                    env.pop('AGENT_BETA_AUTO')
                else:
                    env['AGENT_BETA_AUTO'] = switch
                gh = FakeGitHub()
                self.assertEqual(aba.dispatch(env, opener=gh, pause=lambda s: None), (None, aba.OFF))
                self.assertEqual(gh.calls, [], 'not one request, read or write')
        self.assertIn('          AGENT_BETA_AUTO: ${{ vars.AGENT_BETA_AUTO }}\n', WORKFLOW)  # the switch is the variable

    def test_the_cli_reads_the_switch_before_any_request(self):
        import os
        import subprocess
        env = {k: v for k, v in os.environ.items() if k != 'AGENT_BETA_AUTO'}
        env.update({k: v for k, v in ENV.items() if k != 'AGENT_BETA_AUTO'}, GITHUB_API_URL='http://127.0.0.1:9')
        result = subprocess.run([sys.executable, str(ROOT/'scripts/agent-beta-auto.py'), 'dispatch'], env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, json.loads(result.stdout)), (0, {'dispatched': None, 'reason': aba.OFF}))


class RealGuard(unittest.TestCase):
    """The whole os-release guard step 'Candidate is an exact commit on main', run with the dispatcher's own payload.
    git is a stub that answers only the exact-commit-on-main ancestry check (MAIN_SHAS); everything else is the real
    step and the real helpers it calls (release-pipeline resolve_device, release-chain check)."""
    # os-release.yml workflow_dispatch input defaults, as the workflow's top-level env sees them.
    # The dispatch's workflow env with every input at its declared default, plus the guard step's own env.
    DEFAULTS = shape.dispatch_env(PROMOTE='false', APP_PUBLISH='false', ALPHA_RECOVER='false')
    INPUT_ENV = {'mode': 'MODE', 'candidate_sha': 'CANDIDATE_SHA', 'apps': 'APPS', 'app_publish': 'APP_PUBLISH',
                 'build_number': 'BUILD_NUMBER', 'build_timestamp': 'BUILD_TIMESTAMP', 'promote': 'PROMOTE',
                 'campaign': 'CAMPAIGN', 'resume_failed_stage': 'RESUME_STAGE'}

    def step(self):
        guard = OS_RELEASE.split('\n  guard:\n', 1)[1]
        match = re.search(r'      - name: Candidate is an exact commit on main\n.*?        run: \|\n((?:          [^\n]*\n)+)', guard, re.S)
        self.assertIn('git merge-base --is-ancestor "$CANDIDATE_SHA" origin/main', match[1], 'the exact-commit check is part of the step')
        return 'set -euo pipefail\n' + '\n'.join(line[10:] for line in match[1].splitlines()) + '\n'

    def run_guard(self, inputs, on_main=(SHA,), dispatch_sha=SHA, attempt='1'):
        """DISPATCH_SHA is the commit ref main pointed at when the dispatch was made: the run's GITHUB_SHA."""
        import os
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            git = Path(tmp)/'git'
            git.write_text('#!/bin/sh\n[ "$1 $2 $4" = "merge-base --is-ancestor origin/main" ] || exit 97\n'
                           'case " $MAIN_SHAS " in *" $3 "*) exit 0;; *) exit 1;; esac\n')
            git.chmod(0o755)
            env = dict(self.DEFAULTS, PATH=f'{tmp}:{os.environ["PATH"]}', GITHUB_SHA=dispatch_sha, MAIN_SHAS=' '.join(on_main),
                       GITHUB_RUN_ATTEMPT=attempt)
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
        # Dispatched on main while main's head is the tested SHA (the dispatcher's last check).
        self.assertEqual(self.payload(), {'mode': 'agent-hosted', 'candidate_sha': SHA, 'app_publish': 'true'})
        self.assertEqual(self.run_guard(self.payload()), 0)

    def test_stale_malformed_or_pinned_auto_requests_are_refused(self):
        moved = 'f' * 40
        for name, inputs, on_main, dispatch_sha in (
                ('main moved between the read and the dispatch', self.payload(), (SHA, moved), moved),
                ('dispatch commit not on main (stale or foreign)', self.payload(), (), SHA),
                ('short sha', self.payload(candidate_sha=SHA[:39]), (SHA,), SHA),
                ('branch name', self.payload(candidate_sha='main'), (SHA,), SHA),
                ('uppercase sha', self.payload(candidate_sha=SHA.upper()), (SHA,), SHA),
                ('the probe takes no pin', self.payload(mode='agent-hosted-probe', app_publish='false'), (SHA,), SHA),
                ('apps on the hosted route', self.payload(apps='auto'), (SHA,), SHA),
                ('build pin on auto', self.payload(build_number='2026110101'), (SHA,), SHA),
                ('timestamp pin on auto', self.payload(build_timestamp='1793550000'), (SHA,), SHA),
                ('auto outside the agent routes', self.payload(mode='os-release'), (SHA,), SHA),
                ('promotion', self.payload(promote='true'), (SHA,), SHA),
                ('campaign', self.payload(campaign='start'), (SHA,), SHA),
                ('resume', self.payload(resume_failed_stage='sign'), (SHA,), SHA)):
            with self.subTest(name):
                self.assertNotEqual(self.run_guard(inputs, on_main, dispatch_sha), 0, name)

    def test_a_re_run_of_an_automatic_run_is_refused(self):
        self.assertEqual(self.run_guard(self.payload(), attempt='1'), 0)
        self.assertNotEqual(self.run_guard(self.payload(), attempt='2'), 0)  # its reservation is owned by attempt 1

    def test_the_builder_auto_route_is_unchanged_for_a_manual_dispatch(self):
        # The builder's app-only apps=auto route (no longer dispatched automatically) still admits an exact main commit.
        builder = {'mode': 'app-only', 'candidate_sha': SHA, 'apps': 'auto', 'app_publish': 'true'}
        self.assertEqual(self.run_guard(builder, on_main=(SHA, 'f' * 40), dispatch_sha='f' * 40), 0)
        self.assertNotEqual(self.run_guard(builder, on_main=('f' * 40,), dispatch_sha='f' * 40), 0)

    def test_explicit_app_only_keeps_its_pins(self):
        explicit = {'mode': 'app-only', 'candidate_sha': SHA, 'apps': '[["dev.pixel.voice", 400]]'}
        self.assertNotEqual(self.run_guard(explicit), 0, 'explicit app-only still needs its build pins')
        self.assertEqual(self.run_guard(dict(explicit, build_number='2026110101', build_timestamp='1793550000')), 0)


if __name__ == '__main__':
    unittest.main()
