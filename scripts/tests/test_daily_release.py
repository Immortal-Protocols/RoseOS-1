"""1F daily release dispatcher: scripts/daily-release.py, .github/workflows/daily-release.yml and
os/release-pipeline/lanes/daily.json. Canned API answers only: no network, token, dispatch or host."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('daily_release', ROOT/'scripts/daily-release.py')
daily = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily)
WORKFLOW = (ROOT/'.github/workflows/daily-release.yml').read_text()
LANES = json.loads((ROOT/'os/release-pipeline/lanes/daily.json').read_text())
MAIN, OLD = 'a' * 40, 'b' * 40
ENV = {'GITHUB_API_URL': 'https://api.invalid', 'GITHUB_REPOSITORY': 'o/r', 'GH_TOKEN': 'x',
       'GITHUB_REF': 'refs/heads/main', 'DRY_RUN': 'false'}


def lane(name='mustang', enabled=True):
    found = copy.deepcopy(next(x for x in LANES['lanes'] if x['name'] == name))
    found['enabled'] = enabled
    return found


def run(id_, sha=MAIN, status='completed', conclusion='success', title='mustang-ota 1', path='os-release.yml'):
    return {'id': id_, 'head_sha': sha, 'status': status, 'conclusion': conclusion, 'display_title': title,
            'path': f'.github/workflows/{path}', 'head_branch': 'main'}


class Response:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b'' if self.body is None else json.dumps(self.body).encode()


class Api:
    """Canned GitHub API: {(method, path without query): (status, body)}; records every request."""
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, request, timeout):
        path = request.full_url.split('/repos/o/r', 1)[1].split('?')[0]
        self.calls.append((request.get_method(), path, json.loads(request.data) if request.data else None))
        status, body = self.answers.get((request.get_method(), path), (404, {'message': 'not found'}))
        if isinstance(status, Exception):
            raise status
        return Response(status, body)


def jobs(*pairs):
    return {'jobs': [{'name': n, 'conclusion': c} for n, c in pairs]}


class CommittedLanes(unittest.TestCase):
    def test_every_lane_is_off_and_names_its_existing_entry_and_last_good_rule(self):
        lanes = daily.load_lanes()
        self.assertEqual([x['name'] for x in lanes], ['frankel', 'mustang', 'yogi'])
        self.assertEqual({x['name']: x['enabled'] for x in lanes}, {'frankel': False, 'mustang': False, 'yogi': False})
        self.assertIsNone(LANES['window']['time'])  # the activation change writes Chief's daily window
        for x in lanes:
            entry = ROOT/'.github/workflows'/x['workflow']
            if entry.is_file():  # yogi-ota.yml arrives with the Yogi owner's PR #558
                daily.check_inputs(x, entry.read_text())
        frankel, mustang = lanes[0], lanes[1]
        self.assertEqual((frankel['workflow'], frankel['inputs'], frankel['deliveryJob']),
                         ('os-release.yml', {'mode': 'latest-beta'}, 'publisher'))
        self.assertEqual((mustang['workflow'], mustang['inputs'], mustang['deliveryJob']),
                         ('os-release.yml', {'mode': 'mustang-ota', 'mustang_publish_ota': 'true'}, 'mustang-ota-publisher'))

    def test_the_lane_titles_match_the_entries_run_names(self):
        os_release = (ROOT/'.github/workflows/os-release.yml').read_text()
        run_name = re.search(r'^run-name: (.*)$', os_release, re.M).group(1)
        self.assertIn("|| 'Build & publish latest Pixel 10' }}", run_name)  # latest-beta's title
        self.assertIn("startsWith(inputs.mode, 'mustang-') && format('{0} {1}', inputs.mode, github.run_id)", run_name)
        self.assertEqual(lane('frankel')['runTitle'], 'Build & publish latest Pixel 10')
        self.assertEqual(lane('mustang')['runTitlePrefix'], 'mustang-ota ')
        for name in ('publisher', 'mustang-ota-publisher'):
            self.assertRegex(os_release, rf'(?m)^  {name}:$')

    def test_invalid_lanes_are_refused(self):
        def broken(change):
            data = copy.deepcopy(LANES)
            change(data)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'lanes.json'
                path.write_text(json.dumps(data))
                with self.assertRaises(daily.Invalid):
                    daily.load_lanes(path)
        broken(lambda d: d.update(schema='other'))
        broken(lambda d: d.update(lanes=[]))
        broken(lambda d: d['lanes'].append(copy.deepcopy(d['lanes'][0])))
        broken(lambda d: d['lanes'][0].update(enabled='yes'))
        broken(lambda d: d['lanes'][0].update(workflow='../os-release.yml'))
        broken(lambda d: d['lanes'][0].update(inputs={'mode': True}))
        broken(lambda d: d['lanes'][0].update(inputs={}))
        broken(lambda d: d['lanes'][1].update(runTitle='x'))           # both a title and a prefix
        broken(lambda d: d['lanes'][0].pop('runTitle'))                 # neither
        broken(lambda d: d['lanes'][0].update(deliveryJob=''))
        broken(lambda d: d['lanes'][0].pop('lastGood'))

    def test_lane_inputs_must_exist_on_the_entry_with_allowed_values(self):
        os_release = (ROOT/'.github/workflows/os-release.yml').read_text()
        for inputs in ({'mode': 'latest-betaa'}, {'mode': 'mustang-ota', 'mustang_publish_ota': 'yes'},
                       {'mode': 'mustang-ota', 'no_such_input': 'x'}):
            with self.assertRaises(daily.Invalid):
                daily.check_inputs(dict(lane(), inputs=inputs), os_release)


class Decide(unittest.TestCase):
    def decide(self, runs, delivered_ids=(), name='mustang', enabled=True, present=True):
        return daily.decide(lane(name, enabled), MAIN, runs, lambda r: r['id'] in delivered_ids, present)

    def test_a_disabled_or_absent_lane_is_skipped_without_reading_runs(self):
        self.assertEqual(self.decide([run(1, OLD)], enabled=False)[0], 'skip')
        self.assertEqual(self.decide([], present=False), ('skip', 'absent: os-release.yml is not in this checkout'))

    def test_a_pending_run_of_the_lane_means_no_second_dispatch(self):
        for status in ('queued', 'in_progress', 'waiting', 'requested', 'pending'):
            self.assertEqual(self.decide([run(9, status=status, conclusion=None)])[0], 'skip', status)

    def test_an_identical_delivered_commit_is_never_rebuilt(self):
        self.assertEqual(self.decide([run(5, MAIN)], delivered_ids={5}), ('skip', f'delivered: run 5 already built {MAIN}'))
        # Newer main than the last delivery: dispatch.
        self.assertEqual(self.decide([run(5, OLD)], delivered_ids={5})[0], 'dispatch')
        # A failed or undelivered run of this commit does not count; the newest delivery of an older commit stops the look.
        self.assertEqual(self.decide([run(7, MAIN, conclusion='failure'), run(6, MAIN), run(5, OLD)], delivered_ids={5})[0],
                         'dispatch')
        self.assertEqual(self.decide([run(7, OLD), run(6, MAIN)], delivered_ids={6, 7})[0], 'dispatch')
        self.assertEqual(self.decide([])[0], 'dispatch')  # never delivered

    def test_only_this_lanes_runs_on_main_count(self):
        others = [run(1, status='in_progress', title='Build & publish latest Pixel 10'),   # frankel's, not mustang's
                  run(2, status='in_progress', path='yogi-ota.yml'),                     # another entry
                  dict(run(3, status='in_progress'), head_branch='feature'),            # not main
                  run(4, MAIN, title='mustang-build 4')]                                # another mustang mode
        self.assertEqual(self.decide(others, delivered_ids={4})[0], 'dispatch')
        exact = [run(8, status='queued', title='Build & publish latest Pixel 10 - campaign x')]
        self.assertEqual(self.decide(exact, name='frankel')[0], 'dispatch')  # an exact title, not a prefix


class RunLanes(unittest.TestCase):
    def answers(self, mustang_runs=(), mustang_jobs=None):
        return {('GET', '/commits/main'): (200, {'sha': MAIN}),
                ('GET', '/actions/workflows/os-release.yml/runs'): (200, {'workflow_runs': list(mustang_runs)}),
                ('GET', '/actions/runs/5/jobs'): (200, mustang_jobs or jobs(('mustang-ota-publisher', 'success'))),
                ('POST', '/actions/workflows/os-release.yml/dispatches'): (204, None)}

    def lanes(self, *enabled):
        return [dict(x, enabled=x['name'] in enabled) for x in daily.load_lanes()]

    def test_the_committed_lanes_dispatch_nothing_and_read_no_runs(self):
        api = Api(self.answers())
        results = daily.run_lanes(ENV, daily.load_lanes(), api)
        self.assertEqual([r['action'] for r in results], ['skip', 'skip', 'skip'])
        self.assertEqual(api.calls, [('GET', '/commits/main', None)])

    def test_an_enabled_undelivered_lane_dispatches_its_entry_once_on_main(self):
        api = Api(self.answers([run(5, OLD)]))
        results = daily.run_lanes(ENV, self.lanes('mustang'), api)
        self.assertEqual([r['action'] for r in results], ['skip', 'dispatch', 'skip'])
        posts = [c for c in api.calls if c[0] == 'POST']
        self.assertEqual(posts, [('POST', '/actions/workflows/os-release.yml/dispatches',
                                  {'ref': 'main', 'inputs': {'mode': 'mustang-ota', 'mustang_publish_ota': 'true'}})])

    def test_a_dry_run_decides_but_sends_nothing(self):
        api = Api(self.answers())
        results = daily.run_lanes(dict(ENV, DRY_RUN='true'), self.lanes('mustang'), api)
        self.assertEqual((results[1]['action'], results[1]['dispatched']), ('dispatch', False))
        self.assertFalse([c for c in api.calls if c[0] == 'POST'])
        api = Api(self.answers())
        daily.run_lanes({k: v for k, v in ENV.items() if k != 'DRY_RUN'}, self.lanes('mustang'), api)
        self.assertFalse([c for c in api.calls if c[0] == 'POST'])  # dry unless DRY_RUN is exactly 'false'

    def test_one_lanes_failure_never_stops_another(self):
        answers = self.answers()
        answers[('GET', '/actions/workflows/os-release.yml/runs')] = (500, {'message': 'boom'})
        api = Api(answers)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'.github/workflows').mkdir(parents=True)
            for name in ('os-release.yml',):
                (root/'.github/workflows'/name).write_text((ROOT/'.github/workflows'/name).read_text())
            (root/'.github/workflows/yogi-ota.yml').write_text(
                'on:\n  workflow_dispatch:\n    inputs:\n      stage:\n        type: choice\n        options: [ota, bootstrap]\n'
                '      publish:\n        type: boolean\njobs: {}\n')
            answers[('GET', '/actions/workflows/yogi-ota.yml/runs')] = (200, {'workflow_runs': []})
            answers[('POST', '/actions/workflows/yogi-ota.yml/dispatches')] = (204, None)
            results = daily.run_lanes(ENV, self.lanes('frankel', 'mustang', 'yogi'), api, root=root)
        self.assertEqual([r['action'] for r in results], ['error', 'error', 'dispatch'])
        self.assertIn(('POST', '/actions/workflows/yogi-ota.yml/dispatches',
                       {'ref': 'main', 'inputs': {'stage': 'ota', 'publish': 'true'}}), api.calls)

    def test_a_refused_dispatch_is_an_error_not_a_success(self):
        answers = self.answers()
        answers[('POST', '/actions/workflows/os-release.yml/dispatches')] = (422, {'message': 'bad'})
        results = daily.run_lanes(ENV, self.lanes('mustang'), Api(answers))
        self.assertEqual(results[1]['action'], 'error')
        self.assertNotIn('dispatched', results[1])

    def test_only_main_dispatches(self):
        with self.assertRaises(daily.Invalid):
            daily.run_lanes(dict(ENV, GITHUB_REF='refs/heads/feature'), self.lanes('mustang'), Api(self.answers()))

    def test_exit_status(self):
        out = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(out):
            self.assertEqual(daily.main(['dispatch'], env=ENV, opener=Api(self.answers())), 0)
        self.assertEqual([r['action'] for r in json.loads(out.getvalue())['results']], ['skip', 'skip', 'skip'])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(daily.main(['dispatch'], env=dict(ENV, GITHUB_REF='refs/heads/x'), opener=Api({})), 2)
        with contextlib.redirect_stderr(err):
            self.assertEqual(daily.main(['dispatch'], env=ENV, opener=Api({})), 1)  # main unreadable


class Workflow(unittest.TestCase):
    def test_source_only_no_schedule_manual_dry_run_by_default(self):
        triggers = WORKFLOW.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertNotIn('schedule', triggers)
        self.assertNotIn('workflow_run', triggers)
        self.assertNotIn('push', triggers)
        self.assertRegex(triggers, r'dry_run:\n(?:        .*\n)*        type: boolean\n        default: true')
        for workflow in (ROOT/'.github/workflows').glob('*.yml'):
            self.assertNotIn('daily-release.py', workflow.read_text() if workflow.name != 'daily-release.yml' else '')

    def test_least_privilege_main_only_pinned_and_secret_free(self):
        self.assertIn('\npermissions: {}\n', WORKFLOW)
        job = WORKFLOW.split('\njobs:\n', 1)[1]
        self.assertIn("    if: github.ref == 'refs/heads/main'\n", job)
        self.assertIn('    permissions:\n      actions: write\n      contents: read\n    steps:', job)
        self.assertIn('actions/checkout@11d5960a326750d5838078e36cf38b85af677262', job)
        self.assertIn('persist-credentials: false', job)
        self.assertNotIn('secrets.', WORKFLOW)
        self.assertEqual(re.findall(r'run: (.+)', job), ['python3 scripts/daily-release.py dispatch'])
        self.assertIn('DRY_RUN: ${{ inputs.dry_run }}', job)
        self.assertIn('group: daily-release-dispatcher', WORKFLOW)


if __name__ == '__main__':
    unittest.main()
