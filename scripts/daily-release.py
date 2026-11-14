#!/usr/bin/env python3
"""Daily release dispatcher (1E): build, sign, verify and publish each flavor once a day through its own entry.

Inert as committed: every lane in os/release-pipeline/lanes/daily.json is disabled. Enabling a lane is its own
reviewed change, approved by that lane's owner and Chief; a manual run is a dry run by default.

The daily window is os/release-pipeline/lanes/daily.json window (04:00 America/Los_Angeles). GitHub schedules are UTC,
so .github/workflows/daily-release.yml lists every UTC time that can be that local time (11:00 and 12:00 UTC for 04:00
Pacific). A scheduled run acts only when the cron that fired (github.event.schedule, not the start time, so a late
start still runs) is the window's local time on that UTC date; otherwise every lane is skipped with no API call. A
daylight-saving change therefore never runs the day twice or not at all.

The hosted job runs `dispatch` with only actions:write on its short-lived GITHUB_TOKEN, and no stored token or secret.
Each lane is decided on its own, in file order, so one lane's failure never stops another:
  - disabled: the committed lane is not enabled;
  - absent: its entry workflow file is not in this checkout;
  - pending: a run of the lane is queued, waiting or in progress, so that run delivers and nothing piles up;
  - delivered: the lane's newest completed run whose delivery job succeeded already built this main commit, so an
    identical commit is never rebuilt (a later job's failure, such as cleanup, does not undo a delivery);
  - otherwise the lane's entry is dispatched once, on ref main, with its committed inputs (manual dispatch stays).
A lane's runs are the entry's runs on main titled exactly runTitle, or starting with runTitlePrefix.
The dispatcher never publishes, retries, cancels or changes a release. Each lane's route keeps its last good release when
it fails (lane.lastGood).

  dispatch   env: GITHUB_API_URL, GITHUB_REPOSITORY, GITHUB_REF, GITHUB_EVENT_NAME, GH_TOKEN,
             SCHEDULE (the cron that fired, for schedule), DRY_RUN (workflow_dispatch only: 'false' dispatches)
  check      validate the lanes, the window and each present entry's inputs
Exit status: 0 every lane decided (dispatched or skipped, with the reason); 1 a lane errored (the others still ran);
2 invalid lanes, window, trigger or usage.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LANES = ROOT/'os/release-pipeline/lanes/daily.json'
SCHEMA = 'roseos.daily-lanes.v1'
NAME = re.compile(r'[a-z][a-z0-9-]{0,40}')
WORKFLOW = re.compile(r'[a-z0-9][a-z0-9-]{0,80}\.yml')
INPUT = re.compile(r'[a-z][a-z0-9_]{0,60}')
PENDING = {'queued', 'in_progress', 'waiting', 'requested', 'pending'}
DELIVERY_LOOKS = 20
CRON = re.compile(r'([0-9]|[1-5][0-9]) ([0-9]|1[0-9]|2[0-3]) \* \* \*')
CLOCK = re.compile(r'([01][0-9]|2[0-3]):([0-5][0-9])')


class Invalid(Exception):
    pass


def load_lanes(path=LANES):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise Invalid(f'unreadable lanes ({type(error).__name__})') from None
    if not isinstance(data, dict) or data.get('schema') != SCHEMA or not isinstance(data.get('lanes'), list):
        raise Invalid('not a daily-lanes document')
    lanes, names = [], set()
    for lane in data['lanes']:
        if not isinstance(lane, dict) or not NAME.fullmatch(str(lane.get('name'))) or lane['name'] in names:
            raise Invalid('every lane needs a unique plain name')
        names.add(lane['name'])
        what = lane['name']
        if not isinstance(lane.get('enabled'), bool):
            raise Invalid(f'{what}: enabled must be true or false')
        if not WORKFLOW.fullmatch(str(lane.get('workflow'))):
            raise Invalid(f'{what}: workflow must be a workflow file name')
        inputs = lane.get('inputs')
        if not (isinstance(inputs, dict) and inputs and all(INPUT.fullmatch(str(k)) and isinstance(v, str)
                                                             for k, v in inputs.items())):
            raise Invalid(f'{what}: inputs must map input names to strings')
        titles = [k for k in ('runTitle', 'runTitlePrefix') if k in lane]
        if len(titles) != 1 or not isinstance(lane[titles[0]], str) or not lane[titles[0]].strip():
            raise Invalid(f'{what}: exactly one of runTitle and runTitlePrefix identifies its runs')
        if not (isinstance(lane.get('deliveryJob'), str) and lane['deliveryJob'].strip()):
            raise Invalid(f'{what}: deliveryJob names the job whose success means delivered')
        if not (isinstance(lane.get('lastGood'), str) and lane['lastGood'].strip()):
            raise Invalid(f'{what}: lastGood says how its route keeps the last good release on failure')
        lanes.append(lane)
    if not lanes:
        raise Invalid('no lanes')
    return lanes


def load_window(path=LANES):
    """The committed daily window {timeZone, time}: an IANA zone and HH:MM local time, or time null (no schedule)."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise Invalid(f'unreadable lanes ({type(error).__name__})') from None
    window = data.get('window') if isinstance(data, dict) else None
    if not isinstance(window, dict) or not isinstance(window.get('timeZone'), str):
        raise Invalid('the daily window needs a timeZone')
    zone(window)
    if window.get('time') is not None and not CLOCK.fullmatch(str(window['time'])):
        raise Invalid('the daily window time must be HH:MM or null')
    return window


def zone(window):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(window['timeZone'])
    except (ImportError, ValueError, KeyError, OSError) as error:
        raise Invalid(f'unknown time zone {window.get("timeZone")!r} ({type(error).__name__})') from None


def local_time(window, utc_hour, utc_minute, day):
    """HH:MM in the window's zone at utc_hour:utc_minute UTC on the UTC date day."""
    instant = datetime(day.year, day.month, day.day, utc_hour, utc_minute, tzinfo=timezone.utc)
    return instant.astimezone(zone(window)).strftime('%H:%M')


def window_crons(window, year):
    """Every 'M H * * *' UTC cron that is the window's local time on some day of year (two for a zone with DST)."""
    hour, minute = (int(x) for x in window['time'].split(':'))
    crons, day = set(), date(year, 1, 1)
    while day.year == year:
        utc = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone(window)).astimezone(timezone.utc)
        if local_time(window, utc.hour, utc.minute, utc.date()) == window['time']:  # a local time that exists that day
            crons.add(f'{utc.minute} {utc.hour} * * *')
        day += timedelta(days=1)
    return crons


def trigger(env, window, now):
    """(dry, off-window reason or None) for this run. A scheduled run is real but acts only inside the window."""
    event = env.get('GITHUB_EVENT_NAME')
    if event == 'workflow_dispatch':
        return env.get('DRY_RUN', 'true') != 'false', None
    if event != 'schedule':
        raise Invalid('the daily dispatcher runs only from its schedule or a manual dispatch')
    if window.get('time') is None:
        raise Invalid('a scheduled run needs the committed daily window time')
    cron = env.get('SCHEDULE', '')
    match = CRON.fullmatch(cron)
    if not match or cron not in window_crons(window, now.year):
        raise Invalid(f'the schedule that fired is not a cron of the {window["time"]} {window["timeZone"]} window')
    minute, hour = (int(x) for x in match.groups())
    local = local_time(window, hour, minute, now.date())
    if local != window['time']:
        return False, (f'off-window: {cron} (UTC) is {local} {window["timeZone"]} on {now.date().isoformat()}, '
                       f'not the {window["time"]} window')
    return False, None


def dispatch_inputs(workflow_text):
    """{input name: (type, choice options or None)} of a workflow's workflow_dispatch inputs, read structurally."""
    lines = workflow_text.splitlines()
    found, inputs, current = False, {}, None
    for index, line in enumerate(lines):
        if line == '  workflow_dispatch:':
            found = True
            continue
        if not found:
            continue
        if line and not line.startswith('    '):
            break
        if line == '    inputs:':
            continue
        match = re.fullmatch(r'      ([a-z][a-z0-9_]*):', line)
        if match:
            current = match.group(1)
            inputs[current] = ['string', None]
            continue
        match = re.fullmatch(r'        type: (\w+)', line)
        if match and current:
            inputs[current][0] = match.group(1)
            continue
        match = re.fullmatch(r'        options: \[(.*)\]', line)
        if match and current:
            inputs[current][1] = [o.strip() for o in match.group(1).split(',')]
    return {k: tuple(v) for k, v in inputs.items()}


def check_inputs(lane, workflow_text):
    """The lane's inputs exist on its entry, choices are allowed options and booleans are 'true' or 'false'."""
    accepted = dispatch_inputs(workflow_text)
    for name, value in lane['inputs'].items():
        if name not in accepted:
            raise Invalid(f'{lane["name"]}: {lane["workflow"]} has no input {name}')
        kind, options = accepted[name]
        if kind == 'choice' and value not in (options or []):
            raise Invalid(f'{lane["name"]}: {value!r} is not an option of {name}')
        if kind == 'boolean' and value not in ('true', 'false'):
            raise Invalid(f'{lane["name"]}: {name} is a boolean')


def is_lane_run(lane, run):
    title = run.get('display_title') or ''
    named = title == lane['runTitle'] if 'runTitle' in lane else title.startswith(lane['runTitlePrefix'])
    return named and run.get('path') == f'.github/workflows/{lane["workflow"]}' and run.get('head_branch') == 'main'


def decide(lane, main_sha, runs, delivered, present=True):
    """('dispatch', reason) or ('skip', reason) for one lane. runs: the entry's recent runs, newest first;
    delivered(run): whether that run's delivery job succeeded."""
    if not lane['enabled']:
        return 'skip', 'disabled: the committed lane is not enabled'
    if not present:
        return 'skip', f'absent: {lane["workflow"]} is not in this checkout'
    mine = [run for run in runs if is_lane_run(lane, run)]
    pending = [run for run in mine if run.get('status') in PENDING]
    if pending:
        return 'skip', f'pending: run {pending[0].get("id")} has not finished and will deliver'
    # Delivered means the lane's delivery job succeeded, even if a later job (cleanup) failed. Only the newest
    # delivery counts, and at most DELIVERY_LOOKS completed runs are examined (one jobs read each).
    for run in [r for r in mine if r.get('status') == 'completed'][:DELIVERY_LOOKS]:
        if delivered(run):
            if run.get('head_sha') == main_sha:
                return 'skip', f'delivered: run {run.get("id")} already built {main_sha}'
            break
    return 'dispatch', f'dispatch: main {main_sha} is not yet delivered'


def api(env, method, path, body=None, opener=urllib.request.urlopen):
    request = urllib.request.Request(f'{env["GITHUB_API_URL"]}/repos/{env["GITHUB_REPOSITORY"]}{path}', method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {env["GH_TOKEN"]}',
                 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'roseos-daily-release'})
    with opener(request, timeout=30) as response:
        status, data = response.status, response.read()
    return status, (json.loads(data) if data else None)


def get(env, path, opener):
    status, data = api(env, 'GET', path, opener=opener)
    if status != 200 or not isinstance(data, dict):
        raise RuntimeError(f'GET {path.split("?")[0]} answered {status}')
    return data


def run_lanes(env, lanes, opener=urllib.request.urlopen, root=ROOT, window=None, now=None):
    if env.get('GITHUB_REF') != 'refs/heads/main':
        raise Invalid('the daily dispatcher runs only from main')
    window = load_window() if window is None else window
    dry, off_window = trigger(env, window, now or datetime.now(timezone.utc))
    if off_window:
        return [{'lane': lane['name'], 'workflow': lane['workflow'], 'action': 'skip', 'reason': off_window}
                for lane in lanes]
    main_sha = get(env, '/commits/main', opener).get('sha', '')
    if not re.fullmatch(r'[0-9a-f]{40}', main_sha):
        raise RuntimeError('main has no readable commit')
    results = []
    for lane in lanes:
        result = {'lane': lane['name'], 'workflow': lane['workflow'], 'main': main_sha}
        try:
            entry = Path(root)/'.github/workflows'/lane['workflow']
            present = entry.is_file()
            if present and lane['enabled']:
                check_inputs(lane, entry.read_text())
            runs = []
            if present and lane['enabled']:
                runs = get(env, f'/actions/workflows/{lane["workflow"]}/runs?branch=main&per_page=50', opener) \
                    .get('workflow_runs', [])

            def delivered(run):
                jobs = get(env, f'/actions/runs/{run["id"]}/jobs?filter=latest&per_page=100', opener).get('jobs', [])
                return any((j.get('name'), j.get('conclusion')) == (lane['deliveryJob'], 'success') for j in jobs)
            action, reason = decide(lane, main_sha, runs, delivered, present)
            result.update(action=action, reason=reason)
            if action == 'dispatch' and not dry:
                status, _ = api(env, 'POST', f'/actions/workflows/{lane["workflow"]}/dispatches',
                                {'ref': 'main', 'inputs': lane['inputs']}, opener)
                if status != 204:
                    raise RuntimeError(f'dispatch answered {status}')
                result['dispatched'] = True
            elif action == 'dispatch':
                result['dispatched'] = False
                result['reason'] += ' (dry run: nothing sent)'
        except (Invalid, RuntimeError, KeyError, urllib.error.URLError, OSError, ValueError) as error:
            result.update(action='error', reason=f'{type(error).__name__}: {str(error)[:200]}')
        results.append(result)
    return results


def main(argv=None, env=None, opener=urllib.request.urlopen, now=None):
    import os
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['dispatch', 'check'])
    p.add_argument('--lanes', type=Path, default=LANES)
    a = p.parse_args(argv)
    env = dict(os.environ) if env is None else env
    try:
        lanes = load_lanes(a.lanes)
        window = load_window(a.lanes)
        if a.command == 'check':
            for lane in lanes:
                entry = ROOT/'.github/workflows'/lane['workflow']
                if entry.is_file():
                    check_inputs(lane, entry.read_text())
            print(json.dumps({'window': {'timeZone': window['timeZone'], 'time': window.get('time')},
                              'lanes': [{'lane': lane['name'], 'enabled': lane['enabled']} for lane in lanes]}))
            return 0
        results = run_lanes(env, lanes, opener, window=window, now=now)
    except Invalid as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2
    except (RuntimeError, urllib.error.URLError, OSError, ValueError) as error:
        print(json.dumps({'verdict': 'ERROR', 'error': f'{type(error).__name__}: {str(error)[:200]}'}),
              file=sys.stderr)
        return 1
    print(json.dumps({'results': results}, indent=2))
    return 1 if any(r['action'] == 'error' for r in results) else 0


if __name__ == '__main__':
    sys.exit(main())
