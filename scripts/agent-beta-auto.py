#!/usr/bin/env python3
"""Agent Beta auto-release dispatcher (AGENT-BETA-AUTO part B).

After a successful push-to-main run of the relevant CI ('App offline tests', which runs the Rose Agent suites), the
hosted job .github/workflows/agent-beta-auto.yml runs `dispatch` with only actions:write on its short-lived
GITHUB_TOKEN. It holds no stored token and no secret. It dispatches the existing os-release.yml app-only route once:
  {mode: app-only, candidate_sha: <the exact tested SHA>, apps: auto, app_publish: true} on ref main.
The builder then allocates a fresh Rose Agent version above every consumed one, or skips when these sources are already
published (scripts/release-allocate.py app). The shared os-release-frankel FIFO queue and host lock serialize it with
every OS run.

`dispatch` dispatches only when every condition holds; otherwise it exits 0 and says why:
  - the repository variable AGENT_BETA_AUTO is exactly 'on' (the activation switch; anything else is off);
  - the CI run, read back from the API rather than trusted from the event, is 'App offline tests' from
    .github/workflows/app-tests.yml, event push, branch main, completed with conclusion success;
  - no newer push-to-main run of that CI is pending or succeeded (coalescing: the newest tested SHA releases once);
  - no os-release run already carries this SHA's auto run-name (duplicate events).
It dispatches once. An uncertain outcome is looked for once and otherwise fails visibly; it never retries blindly.

  dispatch   env: GITHUB_API_URL, GITHUB_REPOSITORY, GH_TOKEN, CI_RUN_ID, AGENT_BETA_AUTO
Exit status: 0 dispatched or deliberately not dispatched, 1 refused or uncertain, 2 invalid input.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

WORKFLOW = 'os-release.yml'
CI_NAME, CI_PATH = 'App offline tests', '.github/workflows/app-tests.yml'
SHA = re.compile(r'[0-9a-f]{40}')


class Refused(Exception):
    pass


def run_name(sha):
    return f'Agent Beta auto {sha}'


def api(env, method, path, body=None, opener=urllib.request.urlopen):
    request = urllib.request.Request(f'{env["GITHUB_API_URL"]}/repos/{env["GITHUB_REPOSITORY"]}{path}', method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {env["GH_TOKEN"]}',
                 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'roseos-agent-beta-auto'})
    with opener(request, timeout=30) as response:
        status, data = response.status, response.read()
    return status, (json.loads(data) if data else None)


def read(env, path, opener, what):
    try:
        status, data = api(env, 'GET', path, opener=opener)
    except (urllib.error.URLError, OSError, ValueError):
        status, data = None, None
    if status != 200 or not isinstance(data, dict):
        raise Refused(f'{what}_unreadable: GET {path.split("?")[0]} failed; nothing dispatched')
    return data


def decide(ci, newer_runs, existing_titles, switch):
    """(sha, None) to dispatch, or (None, reason)."""
    if switch != 'on':
        return None, 'off: repository variable AGENT_BETA_AUTO is not on'
    if (ci.get('name'), ci.get('path'), ci.get('event'), ci.get('head_branch')) != (CI_NAME, CI_PATH, 'push', 'main'):
        return None, 'not relevant: only a push-to-main App offline tests run releases'
    if (ci.get('status'), ci.get('conclusion')) != ('completed', 'success'):
        return None, f'not healthy: CI {ci.get("status")}/{ci.get("conclusion")}'
    sha = ci.get('head_sha', '')
    if not SHA.fullmatch(sha):
        raise Refused('invalid: the CI run has no full head SHA')
    for run in newer_runs:
        if run.get('status') != 'completed' or run.get('conclusion') == 'success':
            return None, f'coalesced: newer main CI run {run.get("id")} ({str(run.get("head_sha"))[:12]}) releases instead'
    if run_name(sha) in existing_titles:
        return None, f'duplicate: {run_name(sha)} already exists'
    return sha, None


def dispatch(env, opener=urllib.request.urlopen, pause=time.sleep):
    run_id = env.get('CI_RUN_ID', '')
    if not run_id.isdigit():
        raise Refused('invalid: CI_RUN_ID')
    ci = read(env, f'/actions/runs/{run_id}', opener, 'ci_run')
    runs = read(env, '/actions/workflows/app-tests.yml/runs?branch=main&event=push&per_page=50', opener, 'ci_runs')
    newer = [r for r in runs.get('workflow_runs', []) if isinstance(r.get('id'), int) and r['id'] > int(run_id)]
    titles_of = lambda: {r.get('display_title') for r in read(env,
        f'/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&per_page=100', opener, 'release_runs').get('workflow_runs', [])}
    sha, reason = decide(ci, newer, titles_of(), env.get('AGENT_BETA_AUTO', ''))
    if sha is None:
        return None, reason
    body = {'ref': 'main', 'inputs': {'mode': 'app-only', 'candidate_sha': sha, 'apps': 'auto', 'app_publish': 'true'}}
    try:
        status, _ = api(env, 'POST', f'/actions/workflows/{WORKFLOW}/dispatches', body, opener=opener)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403, 404, 422):
            raise Refused(f'dispatch_refused: HTTP {error.code}; nothing was started') from None
        status = None
    except (urllib.error.URLError, OSError):
        status = None
    if status in (200, 204):
        return sha, None
    pause(15)
    if run_name(sha) in titles_of():
        return sha, None
    raise Refused(f'dispatch_uncertain: no {run_name(sha)} run is visible; not retried')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['dispatch'])
    p.parse_args(argv)
    import os
    try:
        sha, reason = dispatch(dict(os.environ))
    except Refused as error:
        print(f'refused: {error}', file=sys.stderr)
        return 2 if str(error).startswith('invalid') else 1
    print(json.dumps({'dispatched': sha} if sha else {'dispatched': None, 'reason': reason}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
