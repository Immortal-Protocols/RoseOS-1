#!/usr/bin/env python3
"""RELEASE020 back-to-back campaign: decisions, the GitHub calls, and the workflow wiring (no network)."""
import importlib.util
import io
import json
from pathlib import Path
import unittest
import re
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('release_chain', ROOT/'scripts/release-chain.py')
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()

START = 1793380000
OK = {'builder': 'success', 'publisher': 'success', 'cleanup': 'success'}


def decide(campaign='start', mode='latest-beta', attempt='1', now=START + 1800, results=OK, stop='', titles=()):
    return rc.decide(mode, campaign, '41103495650', attempt, START, now, results, stop, set(titles))


class Decide(unittest.TestCase):
    def test_single_run_never_chains(self):
        self.assertEqual(decide(campaign=''), (None, 'single run: no campaign'))

    def test_start_fixes_id_and_24h_deadline(self):
        self.assertEqual(decide(), (f'41103495650.{START + 86400}.2', None))

    def test_successor_carries_deadline_and_increments_generation(self):
        self.assertEqual(decide(campaign=f'7.{START + 3600}.5'), (f'7.{START + 3600}.6', None))

    def test_deadline_cannot_extend_beyond_24h(self):
        with self.assertRaisesRegex(rc.Refused, 'campaign_deadline'):
            decide(campaign=f'7.{START + 86401}.2')

    def test_deadline_reached_stops_quietly(self):
        successor, reason = decide(campaign=f'7.{START + 60}.3', now=START + 60)
        self.assertIsNone(successor)
        self.assertIn('deadline', reason)

    def test_unhealthy_predecessor_never_chains(self):
        for job in ('builder', 'publisher', 'cleanup'):
            for result in ('failure', 'cancelled', 'skipped', None):
                with self.subTest(job=job, result=result), self.assertRaisesRegex(rc.Refused, 'predecessor_unhealthy'):
                    decide(results={k: v for k, v in dict(OK, **{job: result}).items() if v is not None})

    def test_rerun_attempt_never_dispatches(self):
        with self.assertRaisesRegex(rc.Refused, 'rerun'):
            decide(attempt='2')

    def test_stop_variable(self):
        for stop in ('all', '41103495650', ' all\n'):
            self.assertIn('stopped', decide(stop=stop)[1])
        self.assertIsNotNone(decide(stop='999')[0])

    def test_existing_successor_is_a_duplicate(self):
        successor = f'41103495650.{START + 86400}.2'
        with self.assertRaisesRegex(rc.Refused, 'duplicate'):
            decide(titles=[rc.run_name(successor)])

    def test_other_modes_and_bad_inputs(self):
        with self.assertRaisesRegex(rc.Refused, 'campaign_mode'):
            decide(mode='os-release')
        for bad in ('x', '1.2.3', f'7.{START}.1', f'0.{START}.2', f'7.{START}.2;', 'start ', f'7.{START}.2\n'):
            with self.subTest(bad=bad), self.assertRaises(rc.Invalid):
                rc.parse(bad)


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
    def __init__(self, dispatch=204, titles=(), after_dispatch_titles=None, started='2026-10-30T17:06:40Z'):
        self.dispatch, self.titles, self.after = dispatch, list(titles), after_dispatch_titles
        self.started, self.calls = started, []

    def __call__(self, request, timeout):
        url, method = request.full_url, request.get_method()
        self.calls.append((method, url, json.loads(request.data) if request.data else None))
        assert request.headers['Authorization'] == 'Bearer short-lived'
        if method == 'GET' and '/actions/runs/' in url and '/workflows/' not in url:
            return Response(200, {'run_started_at': self.started})
        if method == 'GET':
            dispatched = any(m == 'POST' for m, _, _ in self.calls)
            names = self.after if dispatched and self.after is not None else self.titles
            return Response(200, {'workflow_runs': [{'display_title': t} for t in names]})
        if isinstance(self.dispatch, Exception):
            raise self.dispatch
        return Response(self.dispatch)

    def posts(self):
        return [c for c in self.calls if c[0] == 'POST']


def env(**extra):
    base = {'GITHUB_API_URL': 'https://api.github.com', 'GITHUB_REPOSITORY': 'Immortal-Protocols/RoseOS-1',
        'GITHUB_REF': 'refs/heads/main', 'GITHUB_RUN_ID': '41103495650', 'GITHUB_RUN_ATTEMPT': '1', 'GH_TOKEN': 'short-lived',
        'MODE': 'latest-beta', 'CAMPAIGN': 'start', 'RESULT_BUILDER': 'success', 'RESULT_PUBLISHER': 'success',
        'RESULT_CLEANUP': 'success', 'CAMPAIGN_STOP': ''}
    base.update(extra)
    return base


class NextRun(unittest.TestCase):
    def run_next(self, gh, **extra):
        return rc.next_run(env(**extra), opener=gh, clock=lambda: START + 1800, pause=lambda s: None)

    def test_dispatches_exactly_one_fresh_latest_beta_on_main(self):
        gh = FakeGitHub()
        successor, _ = self.run_next(gh)
        self.assertEqual(successor, f'41103495650.{START + 86400}.2')
        (method, url, body), = gh.posts()
        self.assertTrue(url.endswith('/repos/Immortal-Protocols/RoseOS-1/actions/workflows/os-release.yml/dispatches'))
        # No pins: the successor captures fresh main and allocates fresh versions at its own start.
        self.assertEqual(body, {'ref': 'main', 'inputs': {'mode': 'latest-beta', 'campaign': successor}})

    def test_single_run_makes_no_call(self):
        gh = FakeGitHub()
        self.assertEqual(self.run_next(gh, CAMPAIGN=''), (None, 'single run: no campaign'))
        self.assertEqual(gh.calls, [])

    def test_unhealthy_or_stopped_makes_no_dispatch(self):
        for extra in ({'RESULT_CLEANUP': 'failure'}, {'RESULT_PUBLISHER': 'cancelled'}, {'RESULT_BUILDER': ''},
                      {'GITHUB_RUN_ATTEMPT': '2'}):
            gh = FakeGitHub()
            with self.subTest(extra=extra), self.assertRaises(rc.Refused):
                self.run_next(gh, **extra)
            self.assertEqual(gh.posts(), [])
        gh = FakeGitHub()
        self.assertIn('stopped', self.run_next(gh, CAMPAIGN_STOP='all')[1])
        self.assertEqual(gh.posts(), [])

    def test_not_from_main(self):
        with self.assertRaisesRegex(rc.Refused, 'ref'):
            self.run_next(FakeGitHub(), GITHUB_REF='refs/heads/feature')

    def test_uncertain_dispatch_is_looked_up_once_and_never_retried(self):
        successor = f'41103495650.{START + 86400}.2'
        found = FakeGitHub(dispatch=urllib.error.URLError('reset'), after_dispatch_titles=[rc.run_name(successor)])
        self.assertEqual(self.run_next(found)[0], successor)
        self.assertEqual(len(found.posts()), 1)
        lost = FakeGitHub(dispatch=500, after_dispatch_titles=[])
        with self.assertRaisesRegex(rc.Refused, 'dispatch_uncertain'):
            self.run_next(lost)
        self.assertEqual(len(lost.posts()), 1)

    def test_refused_dispatch_is_reported_not_retried(self):
        gh = FakeGitHub(dispatch=urllib.error.HTTPError('u', 403, 'Forbidden', {}, io.BytesIO(b'')))
        with self.assertRaisesRegex(rc.Refused, 'dispatch_refused: HTTP 403'):
            self.run_next(gh)
        self.assertEqual(len(gh.posts()), 1)

    def test_main_exit_codes(self):
        self.assertEqual(rc.main(['check', '--campaign', '']), 0)
        self.assertEqual(rc.main(['check', '--campaign', 'start']), 0)
        self.assertEqual(rc.main(['check', '--campaign', 'nope']), 2)


class Workflow(unittest.TestCase):
    """Text checks of os-release.yml (no YAML parser dependency)."""

    def jobs(self):
        body = WORKFLOW.split('\njobs:\n', 1)[1]
        return dict(re.findall(r'(?m)^  ([a-z][\w-]*):\n((?:(?:    .*|)\n)*)', body + '\n'))

    def test_only_the_final_job_can_dispatch_workflows(self):
        jobs = self.jobs()
        job = jobs['next-release']
        self.assertIn('    permissions:\n      actions: write\n      contents: read\n    env:', job)
        # Owner-approved: the final campaign link and (19:41:06 UTC) the default-off mid-run os-sign dispatch/wait.
        self.assertEqual([n for n, b in jobs.items() if 'actions: write' in b], ['request-signing', 'next-release'])
        for name in ('request-signing', 'next-release'):
            self.assertIn('    permissions:\n      actions: write\n      contents: read\n    env:', jobs[name])
            self.assertNotIn('secrets.', jobs[name])
            self.assertNotIn('id-token', jobs[name])
        self.assertIn('\npermissions:\n  contents: read\n', WORKFLOW)
        self.assertNotIn('secrets.', job)
        self.assertNotIn('id-token', job)

    def test_final_job_waits_for_builder_publisher_and_cleanup(self):
        job = self.jobs()['next-release']
        self.assertIn('    needs: [builder, publisher, cleanup]\n', job)
        self.assertIn("    if: always() && inputs.mode == 'latest-beta' && inputs.campaign != ''\n", job)
        for name in ('builder', 'publisher', 'cleanup'):
            self.assertIn(f'      RESULT_{name.upper()}: ${{{{ needs.{name}.result }}}}\n', job)
        self.assertIn('      GH_TOKEN: ${{ github.token }}\n', job)
        self.assertIn('      - run: python3 scripts/release-chain.py next\n', job)

    def test_no_schedule_and_manual_button_unchanged(self):
        self.assertIn('\non:\n  workflow_dispatch:\n', WORKFLOW)
        self.assertNotRegex(WORKFLOW, r'(?m)^  (schedule|push|workflow_run|repository_dispatch):')
        inputs = WORKFLOW.split('    inputs:\n', 1)[1].split('\npermissions:', 1)[0]
        names = re.findall(r'(?m)^      ([a-z_]+):$', inputs)
        self.assertLessEqual(len(names), 25)  # GitHub's workflow_dispatch input limit
        self.assertIn('campaign', names)
        self.assertRegex(inputs, r"campaign:\n        description: [^\n]+\n        type: string\n        default: ''\n")

    def test_run_name_matches_the_successor_lookup(self):
        # AGENT-BETA-AUTO: an apps=auto app-only run is named for its tested SHA (the dispatcher's duplicate check).
        # Mustang build/delivery duplicate checks also use their exact run names.
        # Keep the whole expression pinned, including branch order and the legacy fallback.
        self.assertIn("\nrun-name: ${{ inputs.mode == 'mustang-build' && inputs.candidate_sha != '' && "
            "format('mustang-build auto-{0}', inputs.candidate_sha) || inputs.mode == 'mustang-delivery' && "
            "inputs.mustang_publish_ota && format('mustang-delivery ota-{0}', inputs.mustang_compile_run) || "
            "startsWith(inputs.mode, 'mustang-') && format('{0} {1}', inputs.mode, github.run_id) || "
            "inputs.campaign && format('%s - campaign {0}', inputs.campaign) || inputs.mode == 'app-only' && "
            "inputs.apps == 'auto' && format('Agent Beta auto {0}', inputs.candidate_sha) || '%s' }}\n" % (rc.TITLE, rc.TITLE), WORKFLOW)
        self.assertEqual(rc.run_name('7.1793380000.2'), f'{rc.TITLE} - campaign 7.1793380000.2')

    def test_guard_checks_the_campaign_before_any_host(self):
        guard = self.jobs()['guard']
        self.assertIn('python3 scripts/release-chain.py check --campaign "${CAMPAIGN:-}"', guard)
        self.assertIn('[[ -z "${CAMPAIGN:-}" || "$MODE" == latest-beta ]]', guard)
        self.assertIn('  CAMPAIGN: ${{ inputs.campaign }}\n', WORKFLOW)


class Concurrency(unittest.TestCase):
    """One run at a time in os-release-frankel, and every waiting run keeps its place: with the default single pending
    slot a campaign successor queued behind a running gen cancels an unrelated pending run (e.g. models-sign)."""

    def test_the_shared_group_queues_every_pending_run_and_never_cancels_one_in_progress(self):
        block = re.search(r'(?m)^concurrency:\n((?:  .*\n)+)', WORKFLOW)[1]
        settings = dict(re.findall(r'(?m)^  ([\w-]+): (.*)$', block))
        self.assertEqual(settings, {'group': 'os-release-frankel', 'cancel-in-progress': 'false', 'queue': 'max'})
        self.assertEqual(re.findall(r'(?m)^ +concurrency:', WORKFLOW), [])  # no job overrides the workflow group

    def test_actionlint_ignores_only_the_unknown_queue_key_in_this_workflow(self):
        config = (ROOT/'.github/actionlint.yaml').read_text()
        paths = config[config.index('\npaths:\n'):]
        self.assertEqual(re.findall(r"(?m)^  (\S+):$", paths), ['.github/workflows/os-release.yml'])
        self.assertEqual(re.findall(r"(?m)^      - '(.*)'$", paths),
            ['unexpected key "queue" for "concurrency" section\\. expected one of "cancel-in-progress", "group"'])


class RunnerLabels(unittest.TestCase):
    def test_every_self_hosted_label_is_declared_for_actionlint(self):
        declared = set(re.findall(r'(?m)^    - ([\w-]+)$', (ROOT/'.github/actionlint.yaml').read_text()))
        builtin = {'self-hosted', 'linux', 'x64', 'ubuntu-24.04'}
        used = set()
        for path in (ROOT/'.github/workflows').glob('*.yml'):
            text = path.read_text()
            for flow in re.findall(r'runs-on: \[([^\]]+)\]', text) + re.findall(r'labels: \[([^\]]+)\]', text):
                used |= {label.strip() for label in flow.split(',')}
        self.assertIn('roseos-dedicated-signer', used)
        self.assertEqual(used - builtin - declared, set())


if __name__ == '__main__':
    unittest.main()
