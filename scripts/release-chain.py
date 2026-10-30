#!/usr/bin/env python3
"""Back-to-back release campaign for 'Build & publish latest Pixel 10' (RELEASE020).

A campaign is a chain of normal latest-beta runs. Each run pins its own fresh main and allocates fresh versions at its
own start (scripts/release-allocate.py), unchanged main included. After a healthy run, the workflow's final job runs
`next` with only actions:write on the job's short-lived GITHUB_TOKEN and dispatches exactly one successor
workflow_dispatch on main.

The `campaign` input:
  ''                         a single run (the manual button, unchanged); never chains
  'start'                    the first run of a new campaign: id = this run id, deadline = this run's start + 24h
  '<id>.<deadline>.<gen>'    a successor: the campaign id, its fixed UTC-epoch deadline and generation >= 2, carried
                             unchanged except gen + 1, so a campaign cannot extend itself

`next` dispatches only when every condition holds:
  - mode latest-beta and a campaign input;
  - the builder, publisher and cleanup jobs all succeeded (failed, cancelled, skipped or unknown never chains);
  - run attempt 1 (a re-run of any job never dispatches again);
  - before the deadline, and the repository variable RELEASE_CAMPAIGN_STOP is neither 'all' nor this campaign id;
  - no os-release run already carries this campaign's next generation (duplicate terminal events).
It dispatches once. If the dispatch's outcome is uncertain (network error, unexpected status), it looks for the
successor once and otherwise fails visibly; it never retries blindly. A dispatched successor is a queued run, not
evidence that a build started.

  check --campaign C                       guard: the input's shape (exit 0 valid, 2 invalid)
  next                                     final job (env: GITHUB_*, GH_TOKEN, MODE, CAMPAIGN, RESULT_<JOB>, CAMPAIGN_STOP)
Exit status: 0 dispatched or deliberately not chained, 1 refused or uncertain, 2 invalid input.
"""
import argparse
import calendar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

WORKFLOW = 'os-release.yml'
TITLE = 'Build & publish latest Pixel 10'
WINDOW = 24 * 3600
SUCCESSOR = re.compile(r'([1-9][0-9]{0,19})\.([0-9]{10})\.([1-9][0-9]{0,5})')
REQUIRED_JOBS = ('builder', 'publisher', 'cleanup')


class Invalid(Exception):
    pass


class Refused(Exception):
    pass


def parse(campaign):
    """None (single run), ('start',) or (id, deadline, gen) with gen >= 2."""
    if campaign == '':
        return None
    if campaign == 'start':
        return ('start',)
    m = SUCCESSOR.fullmatch(campaign or '')
    if not m or int(m.group(3)) < 2:
        raise Invalid("campaign must be '', 'start' or '<id>.<deadline>.<generation >= 2>'")
    return (m.group(1), int(m.group(2)), int(m.group(3)))


def run_name(campaign):
    """The run-name the workflow derives from its campaign input; successors are found by it."""
    return f'{TITLE} - campaign {campaign}' if campaign else TITLE


def decide(mode, campaign, run_id, run_attempt, started_epoch, now, results, stop, existing_titles):
    """(successor campaign input, None) to dispatch, or (None, reason) not to. Raises Refused for an unhealthy chain."""
    parsed = parse(campaign)
    if parsed is None:
        return None, 'single run: no campaign'
    if mode != 'latest-beta':
        raise Refused(f'campaign_mode: a campaign chains latest-beta only, not {mode}')
    if parsed == ('start',):
        cid, deadline, gen = str(run_id), int(started_epoch) + WINDOW, 1
    else:
        cid, deadline, gen = parsed
        if deadline - int(started_epoch) > WINDOW:
            raise Refused('campaign_deadline: the deadline is more than 24h after this run started')
    unhealthy = {job: results.get(job, 'unknown') for job in REQUIRED_JOBS if results.get(job) != 'success'}
    if unhealthy:
        raise Refused('predecessor_unhealthy: ' + ', '.join(f'{j}={r}' for j, r in sorted(unhealthy.items())))
    if str(run_attempt) != '1':
        raise Refused(f'rerun: attempt {run_attempt} never dispatches a successor')
    if stop and stop.strip() in ('all', cid):
        return None, f'stopped: RELEASE_CAMPAIGN_STOP={stop.strip()}'
    if now >= deadline:
        return None, f'deadline: campaign {cid} ended at {deadline}'
    successor = f'{cid}.{deadline}.{gen + 1}'
    if run_name(successor) in existing_titles:
        raise Refused(f'duplicate: a run for campaign {successor} already exists')
    return successor, None


# ---------------------------------------------------------------- GitHub API (the job's GITHUB_TOKEN only)

def api(env, method, path, body=None, opener=urllib.request.urlopen):
    request = urllib.request.Request(f'{env["GITHUB_API_URL"]}/repos/{env["GITHUB_REPOSITORY"]}{path}', method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {env["GH_TOKEN"]}',
                 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'roseos-release-chain'})
    with opener(request, timeout=30) as response:
        status, data = response.status, response.read()
    return status, (json.loads(data) if data else None)


def titles(env, opener):
    status, data = api(env, 'GET', f'/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&per_page=100', opener=opener)
    if status != 200 or not isinstance(data, dict) or not isinstance(data.get('workflow_runs'), list):
        raise Refused('runs_unreadable: cannot list os-release runs to rule out a duplicate successor')
    return {run.get('display_title') for run in data['workflow_runs']}


def started(env, opener):
    status, data = api(env, 'GET', f'/actions/runs/{env["GITHUB_RUN_ID"]}', opener=opener)
    stamp = (data or {}).get('run_started_at') if status == 200 else None
    if not stamp:
        raise Refused('run_unreadable: cannot read this run\'s start time')
    return calendar.timegm(time.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ'))


def next_run(env, opener=urllib.request.urlopen, clock=time.time, pause=time.sleep):
    if env.get('GITHUB_REF') != 'refs/heads/main':
        raise Refused('ref: a campaign chains from main only')
    results = {job: env.get(f'RESULT_{job.upper()}', 'unknown') for job in REQUIRED_JOBS}
    if parse(env.get('CAMPAIGN', '')) is None:
        return None, 'single run: no campaign'
    successor, reason = decide(env.get('MODE'), env.get('CAMPAIGN', ''), env['GITHUB_RUN_ID'], env.get('GITHUB_RUN_ATTEMPT'),
        started(env, opener), int(clock()), results, env.get('CAMPAIGN_STOP', ''), titles(env, opener))
    if successor is None:
        return None, reason
    try:
        status, _ = api(env, 'POST', f'/actions/workflows/{WORKFLOW}/dispatches',
            {'ref': 'main', 'inputs': {'mode': 'latest-beta', 'campaign': successor}}, opener=opener)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403, 404, 422):
            raise Refused(f'dispatch_refused: HTTP {error.code}; no successor was started') from None
        status = None
    except (urllib.error.URLError, OSError):
        status = None
    if status in (200, 204):
        return successor, None
    # Uncertain: the request may or may not have created the run. Look once; never dispatch again.
    pause(15)
    if run_name(successor) in titles(env, opener):
        return successor, None
    raise Refused(f'dispatch_uncertain: no run for campaign {successor} is visible; not retried')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['check', 'next'])
    p.add_argument('--campaign')
    args = p.parse_args(argv)
    try:
        if args.command == 'check':
            parse(args.campaign if args.campaign is not None else '')
            print(json.dumps({'campaign': args.campaign or '', 'valid': True}))
            return 0
        successor, reason = next_run(dict(os.environ))
        print(json.dumps({'dispatched': successor, 'reason': reason}, sort_keys=True))
        return 0
    except Invalid as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2
    except Refused as error:
        print(json.dumps({'verdict': 'REFUSED', 'error': str(error)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
