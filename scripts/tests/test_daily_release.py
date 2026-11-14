"""1E daily release dispatcher: scripts/daily-release.py, .github/workflows/daily-release.yml and
os/release-pipeline/lanes/daily.json. Canned API answers only: no network, token, dispatch or host."""
import contextlib
import copy
from datetime import date, datetime, timedelta, timezone
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
       'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch', 'DRY_RUN': 'false'}
SCHEDULED = dict(ENV, GITHUB_EVENT_NAME='schedule', DRY_RUN='')  # an absent input renders empty on a schedule
CRONS = re.findall(r"(?m)^    - cron: '([^']+)'$", WORKFLOW)
DEDICATED = 'mustang-ota-dedicated.yml'


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def lane(name='mustang', enabled=True):
    found = copy.deepcopy(next(x for x in LANES['lanes'] if x['name'] == name))
    found['enabled'] = enabled
    return found


def run(id_, sha=MAIN, status='completed', conclusion='success', title='mustang-dedicated-ota 1', path=DEDICATED):
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
    def test_only_frankel_is_on_and_every_lane_names_its_existing_entry_and_last_good_rule(self):
        lanes = daily.load_lanes()
        self.assertEqual([x['name'] for x in lanes], ['frankel', 'mustang', 'yogi'])
        # Frankel on (Chief/Nathan's go); Mustang and Yogi stay off until their owners admit automatic dispatch.
        self.assertEqual({x['name']: x['enabled'] for x in lanes}, {'frankel': True, 'mustang': False, 'yogi': False})
        window = daily.load_window()
        self.assertEqual((window['timeZone'], window['time']), ('America/Los_Angeles', '04:00'))  # 1E: 4 a.m. Pacific
        for x in lanes:
            daily.check_inputs(x, (ROOT/'.github/workflows'/x['workflow']).read_text())  # every entry is committed
        frankel, mustang, yogi = lanes
        self.assertEqual((frankel['workflow'], frankel['inputs'], frankel['deliveryJob']),
                         ('os-release.yml', {'mode': 'latest-beta'}, 'publisher'))
        # Each flavor on its own entry, builder and queue: Mustang's shared-host os-release route is retired.
        self.assertEqual((mustang['workflow'], mustang['inputs'], mustang['deliveryJob']),
                         (DEDICATED, {'publish': 'true'}, 'publisher'))
        self.assertEqual((yogi['workflow'], yogi['inputs'], yogi['deliveryJob']),
                         ('yogi-ota.yml', {'stage': 'ota', 'publish': 'true'}, 'publisher'))
        queues = {x['name']: re.search(r'(?m)^concurrency:\n(?:  #.*\n)*  group: (\S+)$',
                                       (ROOT/'.github/workflows'/x['workflow']).read_text())[1] for x in lanes}
        self.assertEqual(queues, {'frankel': 'os-release-frankel', 'mustang': 'mustang-ota', 'yogi': 'yogi-ota'})

    def test_the_lane_titles_match_the_entries_run_names(self):
        def entry(name):
            return (ROOT/'.github/workflows'/name).read_text()

        def run_name(name):
            return re.search(r'^run-name: (.*)$', entry(name), re.M).group(1)
        # Only latest-beta is titled exactly like the Frankel lane; latest-draft and every other mode are not.
        self.assertTrue(run_name('os-release.yml').endswith(
            "|| inputs.mode == 'latest-beta' && 'Build & publish latest Pixel 10' || "
            "format('os-release {0} {1}', inputs.mode, github.run_id) }}"))
        self.assertEqual(run_name('os-release.yml').count("'Build & publish latest Pixel 10'"), 1)
        self.assertEqual(lane('frankel')['runTitle'], 'Build & publish latest Pixel 10')
        self.assertEqual(run_name(DEDICATED), 'mustang-dedicated-ota ${{ github.run_id }}')
        self.assertEqual(lane('mustang')['runTitlePrefix'], 'mustang-dedicated-ota ')
        self.assertEqual(run_name('yogi-ota.yml'), 'yogi-ota ${{ inputs.stage }} ${{ github.run_id }}')
        self.assertEqual(lane('yogi')['runTitlePrefix'], f'yogi-ota {lane("yogi")["inputs"]["stage"]} ')
        for x in LANES['lanes']:
            self.assertRegex(entry(x['workflow']), rf'(?m)^  {x["deliveryJob"]}:$', x['name'])

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
        dedicated = (ROOT/'.github/workflows'/DEDICATED).read_text()
        for inputs in ({'publish': 'yes'}, {'mode': 'mustang-ota'}):
            with self.assertRaises(daily.Invalid):
                daily.check_inputs(dict(lane(), inputs=inputs), dedicated)


class Decide(unittest.TestCase):
    def decide(self, runs, delivered_ids=(), name='mustang', enabled=True, present=True):
        return daily.decide(lane(name, enabled), MAIN, runs, lambda r: r['id'] in delivered_ids, present)

    def test_a_disabled_or_absent_lane_is_skipped_without_reading_runs(self):
        self.assertEqual(self.decide([run(1, OLD)], enabled=False)[0], 'skip')
        self.assertEqual(self.decide([], present=False), ('skip', f'absent: {DEDICATED} is not in this checkout'))

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

    def test_a_delivery_counts_even_when_a_later_job_failed(self):
        # The delivery job succeeded (published) and only cleanup failed: delivered, so never rebuilt.
        self.assertEqual(self.decide([run(5, MAIN, conclusion='failure')], delivered_ids={5})[0], 'skip')
        # A successful run whose delivery job did not succeed (another mode under the same title) is not a delivery.
        self.assertEqual(self.decide([run(6, MAIN)], delivered_ids=())[0], 'dispatch')

    def test_only_a_bounded_number_of_completed_runs_is_examined(self):
        looked = []

        def delivered(r):
            looked.append(r['id'])
            return r['id'] == 999
        runs = [run(i, OLD) for i in range(1, daily.DELIVERY_LOOKS + 6)] + [run(999, MAIN)]
        self.assertEqual(daily.decide(lane(), MAIN, runs, delivered)[0], 'dispatch')
        self.assertEqual(len(looked), daily.DELIVERY_LOOKS)

    def test_only_this_lanes_runs_on_main_count(self):
        others = [run(1, status='in_progress', title='Build & publish latest Pixel 10'),   # frankel's, not mustang's
                  run(2, status='in_progress', path='yogi-ota.yml'),                     # another entry
                  dict(run(3, status='in_progress'), head_branch='feature'),            # not main
                  run(4, MAIN, title='mustang-build 4')]                                # another mustang mode
        self.assertEqual(self.decide(others, delivered_ids={4})[0], 'dispatch')
        exact = [run(8, status='queued', title='Build & publish latest Pixel 10 - campaign x', path='os-release.yml')]
        self.assertEqual(self.decide(exact, name='frankel')[0], 'dispatch')  # an exact title, not a prefix

    def test_frankel_counts_only_latest_beta_runs(self):
        # A latest-draft run's publisher success leaves an unpublished draft, and an advanced mode never delivers:
        # neither is a Frankel delivery nor holds the lane. Their runs are titled 'os-release <mode> <run id>'.
        others = [run(6, status='in_progress', title='os-release blazer-compile 6', path='os-release.yml'),
                  run(5, MAIN, title='os-release latest-draft 5', path='os-release.yml')]
        self.assertEqual(self.decide(others, delivered_ids={5}, name='frankel')[0], 'dispatch')
        beta = run(4, MAIN, title='Build & publish latest Pixel 10', path='os-release.yml')
        self.assertEqual(self.decide([beta], delivered_ids={4}, name='frankel')[0], 'skip')
        self.assertEqual(self.decide([dict(beta, status='queued')], name='frankel')[0], 'skip')


class RunLanes(unittest.TestCase):
    @staticmethod
    def answers(mustang_runs=(), mustang_jobs=None):
        return {('GET', '/commits/main'): (200, {'sha': MAIN}),
                ('GET', f'/actions/workflows/{DEDICATED}/runs'): (200, {'workflow_runs': list(mustang_runs)}),
                ('GET', '/actions/runs/5/jobs'): (200, mustang_jobs or jobs(('publisher', 'success'))),
                ('POST', f'/actions/workflows/{DEDICATED}/dispatches'): (204, None)}

    def lanes(self, *enabled):
        return [dict(x, enabled=x['name'] in enabled) for x in daily.load_lanes()]

    def test_the_committed_lanes_dispatch_only_frankel(self):
        answers = self.answers()
        answers[('GET', '/actions/workflows/os-release.yml/runs')] = (200, {'workflow_runs': []})
        answers[('POST', '/actions/workflows/os-release.yml/dispatches')] = (204, None)
        api = Api(answers)
        results = daily.run_lanes(ENV, daily.load_lanes(), api)
        self.assertEqual([r['action'] for r in results], ['dispatch', 'skip', 'skip'])
        self.assertEqual([c for c in api.calls if c[0] == 'POST'],
                         [('POST', '/actions/workflows/os-release.yml/dispatches', {'ref': 'main', 'inputs': {'mode': 'latest-beta'}})])

    def test_an_enabled_undelivered_lane_dispatches_its_entry_once_on_main(self):
        api = Api(self.answers([run(5, OLD)]))
        results = daily.run_lanes(ENV, self.lanes('mustang'), api)
        self.assertEqual([r['action'] for r in results], ['skip', 'dispatch', 'skip'])
        posts = [c for c in api.calls if c[0] == 'POST']
        self.assertEqual(posts, [('POST', f'/actions/workflows/{DEDICATED}/dispatches',
                                  {'ref': 'main', 'inputs': {'publish': 'true'}})])

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
        answers[('GET', f'/actions/workflows/{DEDICATED}/runs')] = (OSError('reset'), None)
        api = Api(answers)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'.github/workflows').mkdir(parents=True)
            for name in ('os-release.yml', DEDICATED):
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
        answers[('POST', f'/actions/workflows/{DEDICATED}/dispatches')] = (422, {'message': 'bad'})
        results = daily.run_lanes(ENV, self.lanes('mustang'), Api(answers))
        self.assertEqual(results[1]['action'], 'error')
        self.assertNotIn('dispatched', results[1])

    def test_lane_inputs_are_rechecked_against_the_entry_before_any_dispatch(self):
        lanes = self.lanes('mustang')
        lanes[1]['inputs'] = {'publish': 'yes'}
        api = Api(self.answers())
        results = daily.run_lanes(ENV, lanes, api)
        self.assertEqual(results[1]['action'], 'error')
        self.assertFalse([c for c in api.calls if c[0] == 'POST'])

    def test_only_main_dispatches(self):
        with self.assertRaises(daily.Invalid):
            daily.run_lanes(dict(ENV, GITHUB_REF='refs/heads/feature'), self.lanes('mustang'), Api(self.answers()))

    def test_exit_status(self):
        out = io.StringIO()
        import contextlib
        committed = self.answers()
        committed[('GET', '/actions/workflows/os-release.yml/runs')] = (200, {'workflow_runs': []})
        committed[('POST', '/actions/workflows/os-release.yml/dispatches')] = (204, None)
        with contextlib.redirect_stdout(out):
            self.assertEqual(daily.main(['dispatch'], env=ENV, opener=Api(committed)), 0)
        self.assertEqual([r['action'] for r in json.loads(out.getvalue())['results']], ['dispatch', 'skip', 'skip'])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(daily.main(['dispatch'], env=dict(ENV, GITHUB_REF='refs/heads/x'), opener=Api({})), 2)
        with contextlib.redirect_stderr(err):
            self.assertEqual(daily.main(['dispatch'], env=ENV, opener=Api({})), 1)  # main unreadable
        answers = self.answers()
        answers[('GET', f'/actions/workflows/{DEDICATED}/runs')] = (500, {'message': 'boom'})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'lanes.json'
            data = copy.deepcopy(LANES)
            data['lanes'][0]['enabled'], data['lanes'][1]['enabled'] = False, True
            path.write_text(json.dumps(data))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(daily.main(['dispatch', '--lanes', str(path)], env=ENV, opener=Api(answers)), 1)
        self.assertEqual([r['action'] for r in json.loads(out.getvalue())['results']], ['skip', 'error', 'skip'])


class Schedule(unittest.TestCase):
    """04:00 America/Los_Angeles through two UTC crons, decided by the cron that fired (1E)."""
    WINDOW = daily.load_window()

    def in_window(self, cron, now):
        dry, off = daily.trigger(dict(SCHEDULED, SCHEDULE=cron), self.WINDOW, now)
        self.assertFalse(dry)  # a scheduled run is never a dry run
        return off is None

    def test_exactly_one_cron_is_the_window_every_day_across_daylight_saving_changes(self):
        from zoneinfo import ZoneInfo
        pacific = ZoneInfo('America/Los_Angeles')
        day = date(2026, 1, 1)
        while day < date(2029, 1, 1):
            chosen = [c for c in CRONS if self.in_window(c, datetime(day.year, day.month, day.day, 13, tzinfo=timezone.utc))]
            daylight = bool(datetime(day.year, day.month, day.day, 11, tzinfo=timezone.utc).astimezone(pacific).dst())
            self.assertEqual(chosen, ['0 11 * * *' if daylight else '0 12 * * *'], day)
            day += timedelta(days=1)
        for when, cron in (('2026-03-07', '0 12 * * *'), ('2026-03-08', '0 11 * * *'),   # spring forward
                           ('2026-10-31', '0 11 * * *'), ('2026-11-01', '0 12 * * *'),   # fall back
                           ('2026-11-07', '0 12 * * *')):
            self.assertEqual([c for c in CRONS if self.in_window(c, utc(when + 'T12:30:00'))], [cron], when)

    def test_a_late_start_is_judged_by_the_cron_that_fired_not_the_clock(self):
        self.assertTrue(self.in_window('0 12 * * *', utc('2026-11-07T13:40:00')))   # 05:40 local, but the 04:00 run
        self.assertFalse(self.in_window('0 11 * * *', utc('2026-11-07T12:01:00')))  # 04:01 local, but the 03:00 cron

    def test_an_off_window_scheduled_run_skips_every_lane_without_any_api_call(self):
        api = Api({})
        lanes = [dict(x, enabled=True) for x in daily.load_lanes()]
        results = daily.run_lanes(dict(SCHEDULED, SCHEDULE='0 11 * * *'), lanes, api, now=utc('2026-11-07T11:04:00'))
        self.assertEqual([r['action'] for r in results], ['skip', 'skip', 'skip'])
        self.assertTrue(all(r['reason'].startswith('off-window: 0 11 * * * (UTC) is 03:00 America/Los_Angeles on '
                                                   '2026-11-07') for r in results), results)
        self.assertEqual(api.calls, [])

    def test_a_scheduled_run_in_the_window_dispatches_an_enabled_lane_for_real(self):
        answers = RunLanes.answers([run(5, OLD)])
        api = Api(answers)
        lanes = [dict(x, enabled=x['name'] == 'mustang') for x in daily.load_lanes()]
        results = daily.run_lanes(dict(SCHEDULED, SCHEDULE='0 12 * * *'), lanes, api, now=utc('2026-11-07T12:16:00'))
        self.assertEqual([(r['action'], r.get('dispatched')) for r in results],
                         [('skip', None), ('dispatch', True), ('skip', None)])
        self.assertEqual([c for c in api.calls if c[0] == 'POST'],
                         [('POST', f'/actions/workflows/{DEDICATED}/dispatches', {'ref': 'main', 'inputs': {'publish': 'true'}})])
        # The committed lanes: a real scheduled run dispatches Frankel's latest-beta only.
        answers[('GET', '/actions/workflows/os-release.yml/runs')] = (200, {'workflow_runs': []})
        answers[('POST', '/actions/workflows/os-release.yml/dispatches')] = (204, None)
        api = Api(answers)
        results = daily.run_lanes(dict(SCHEDULED, SCHEDULE='0 12 * * *'), daily.load_lanes(), api,
                                  now=utc('2026-11-07T12:16:00'))
        self.assertEqual([r['action'] for r in results], ['dispatch', 'skip', 'skip'])
        self.assertEqual([c for c in api.calls if c[0] == 'POST'],
                         [('POST', '/actions/workflows/os-release.yml/dispatches', {'ref': 'main', 'inputs': {'mode': 'latest-beta'}})])

    def test_an_unknown_trigger_cron_or_window_is_refused_before_any_api_call(self):
        lanes = daily.load_lanes()
        now = utc('2026-11-07T12:00:00')
        for env in (dict(ENV, GITHUB_EVENT_NAME='push'), {k: v for k, v in ENV.items() if k != 'GITHUB_EVENT_NAME'},
                    dict(SCHEDULED, SCHEDULE='30 11 * * *'), dict(SCHEDULED, SCHEDULE=''),
                    dict(SCHEDULED, SCHEDULE='0 13 * * *'), dict(SCHEDULED, SCHEDULE='0 12 * * 1-5')):
            api = Api({})
            with self.assertRaises(daily.Invalid, msg=env.get('SCHEDULE')):
                daily.run_lanes(env, lanes, api, now=now)
            self.assertEqual(api.calls, [])
        api = Api({})
        with self.assertRaises(daily.Invalid):  # no window time: a schedule cannot run
            daily.run_lanes(dict(SCHEDULED, SCHEDULE='0 12 * * *'), lanes, api, window=dict(self.WINDOW, time=None), now=now)
        self.assertEqual(daily.trigger(ENV, dict(self.WINDOW, time=None), now), (False, None))  # manual still works
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(daily.main(['dispatch'], env=dict(ENV, GITHUB_EVENT_NAME='push'), opener=Api({}), now=now), 2)

    def test_the_window_is_validated(self):
        def window(change):
            data = copy.deepcopy(LANES)
            change(data)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'lanes.json'
                path.write_text(json.dumps(data))
                return daily.load_window(path)
        for change in (lambda d: d['window'].update(time='4:00'), lambda d: d['window'].update(time='24:00'),
                       lambda d: d['window'].update(time=400), lambda d: d['window'].update(timeZone='Mars/Base'),
                       lambda d: d['window'].pop('timeZone'), lambda d: d.pop('window')):
            with self.assertRaises(daily.Invalid):
                window(change)
        self.assertIsNone(window(lambda d: d['window'].update(time=None))['time'])
        # A zone without daylight saving needs exactly one cron; Pacific needs the two committed ones.
        self.assertEqual(daily.window_crons({'timeZone': 'UTC', 'time': '04:00'}, 2026), {'0 4 * * *'})
        for year in (2026, 2027, 2028):
            self.assertEqual(daily.window_crons(self.WINDOW, year), {'0 11 * * *', '0 12 * * *'})


class Workflow(unittest.TestCase):
    def test_the_schedule_is_exactly_the_windows_utc_crons_and_a_manual_run_stays_dry_by_default(self):
        triggers = WORKFLOW.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertTrue(triggers.startswith("  schedule:\n    - cron: '0 11 * * *'\n    - cron: '0 12 * * *'\n"
                                            "  workflow_dispatch:\n"), triggers)
        self.assertEqual(set(CRONS), daily.window_crons(daily.load_window(), 2026))
        self.assertEqual(len(CRONS), 2)
        self.assertNotIn('workflow_run', triggers)
        self.assertNotIn('push', triggers)
        self.assertRegex(triggers, r'dry_run:\n(?:        .*\n)*        type: boolean\n        default: true')
        for workflow in (ROOT/'.github/workflows').glob('*.yml'):  # only this workflow ever runs the dispatcher
            if workflow.name != 'daily-release.yml':
                self.assertNotRegex(workflow.read_text(), r'python3? +scripts/daily-release\.py', workflow.name)

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
        self.assertIn('SCHEDULE: ${{ github.event.schedule }}', job)  # the cron that fired, not the start time
        self.assertIn('group: daily-release-dispatcher', WORKFLOW)


if __name__ == '__main__':
    unittest.main()
