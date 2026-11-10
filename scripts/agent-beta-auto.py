#!/usr/bin/env python3
"""Agent Beta auto-release dispatcher (AGENT-BETA-AUTO part B).

After a successful push-to-main run of the relevant CI ('App offline tests', which runs the Rose Agent suites), the
hosted job .github/workflows/agent-beta-auto.yml runs `dispatch` with only actions:write on its short-lived
GITHUB_TOKEN. It holds no stored token and no secret. It dispatches the existing os-release.yml hosted Agent route
(1F) once:
  {mode: agent-hosted, candidate_sha: <main's head: the tested SHA or a proven equivalent>, app_publish: true} on
ref main. agent-hosted builds its own dispatch commit, so its guard admits the pin only as exactly that commit, and
this dispatcher pins only the head it read last. The route then holds one fresh Rose Agent
version in the shared pixel-ota ledger for that run, or skips when these sources are already published
(scripts/agent-hosted.py reserve). It runs on GitHub-hosted runners in os-release's own hosted Agent queue
(os-release-agent-hosted), with no builder, host lock or OS run; the shared ledger's create-if-absent ref, not a queue,
keeps its version apart from every builder allocation. The builder's app-only apps=auto route is unchanged and stays
available to a manual dispatch.

`dispatch` dispatches only when every condition holds; otherwise it exits 0 and says why:
  - the repository variable AGENT_BETA_AUTO is exactly 'on' (the activation switch; anything else is off, and off
    is decided first: no API request is made at all);
  - the CI run, read back from the API rather than trusted from the event, is 'App offline tests' from
    .github/workflows/app-tests.yml, event push, branch main, completed with conclusion success;
  - no newer push-to-main run of that CI is pending or succeeded (coalescing: the newest tested SHA releases once);
  - no os-release run already carries this SHA's auto run-name (duplicate events);
  - Rose Agent sources (os/latest-beta/allocation.json, dev.pixel.voice) changed since the newest successful
    'Agent Beta auto <sha>' run of either route, read from the compare API; unchanged content is skipped before any
    runner starts. A first auto release, an unreadable or truncated (300+ files) comparison or a non-ancestor
    dispatches anyway: the hosted reserve still skips sources that are already published;
  - main's head, read last, right before the one request, is the tested SHA, or a descendant proven to carry exactly
    what that CI checked (equivalence(), below), and is then dispatched pinned to itself, the commit the run builds.
    A newer commit that changed a checked path is superseded: it is that change's own App offline tests run, which
    comes because every checked path is on that workflow's trigger, that decides.
It dispatches once. An uncertain outcome is looked for once and otherwise fails visibly; it never retries blindly.

Equivalence fails closed: main's head H counts as the tested T only when T's own App offline tests push paths
(.github/workflows/app-tests.yml at T) and Rose Agent sources (os/latest-beta/allocation.json at T), both read exactly,
plus those two files, show no change in a complete comparison T...H (H a descendant of T, every changed path listed,
a rename by both names), and main is still at H when read again just before the request. Anything that cannot be read
completely refuses with nothing sent; it is never treated as equivalent.

  dispatch   env: GITHUB_API_URL, GITHUB_REPOSITORY, GH_TOKEN, CI_RUN_ID, AGENT_BETA_AUTO
The transport (api, read, Refused) is shared with scripts/mustang-ota-auto.py and is unchanged by the hosted route.
Exit status: 0 dispatched or deliberately not dispatched, 1 refused or uncertain, 2 invalid input.
"""
import argparse
import base64
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

WORKFLOW = 'os-release.yml'
CI_NAME, CI_PATH = 'App offline tests', '.github/workflows/app-tests.yml'
SHA = re.compile(r'[0-9a-f]{40}')
ROOT = Path(__file__).resolve().parents[1]
ALLOCATION = 'os/latest-beta/allocation.json'
AUTO_PREFIX = 'Agent Beta auto '
MODE = 'agent-hosted'
OFF = 'off: repository variable AGENT_BETA_AUTO is not on'
APP_TESTS = '.github/workflows/app-tests.yml'
COMPARE_FILES_MAX = 300  # the compare API lists at most 300 changed files: a list this long may be incomplete
UNREAD_GLOB = re.compile(r'[!?+\[\]{}\\]')  # negation, ?, classes, braces, escapes: not read, so never equivalent


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
        return None, OFF
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


def voice_sources(root=ROOT):
    cfg = json.loads((Path(root)/ALLOCATION).read_text())
    return next(p['sources'] for p in cfg['packages'] if p['packageName'] == 'dev.pixel.voice')


def unchanged_since(env, sha, release_runs, sources, opener):
    """The SHA of the newest successful auto run when nothing under the Rose Agent sources changed since it, else None."""
    done = [r for r in release_runs if isinstance(r.get('id'), int) and r.get('status') == 'completed'
            and r.get('conclusion') == 'success' and str(r.get('display_title', '')).startswith(AUTO_PREFIX)]
    if not done:
        return None
    previous = max(done, key=lambda r: r['id'])['display_title'][len(AUTO_PREFIX):]
    if not SHA.fullmatch(previous):
        return None
    try:
        data = read(env, f'/compare/{previous}...{sha}', opener, 'compare')
    except Refused:
        return None
    files = data.get('files')
    if data.get('status') not in ('ahead', 'identical') or not isinstance(files, list) or len(files) >= 300:
        return None
    roots = [s.rstrip('/') for s in sources]
    changed = [f.get('filename', '') for f in files if any(f.get('filename', '') == r or f.get('filename', '').startswith(r + '/') for r in roots)]
    return None if changed else previous


def main_head(env, opener):
    """The commit main points at now: the one a dispatch on ref main runs, and so the one agent-hosted builds."""
    found = read(env, '/git/ref/heads/main', opener, 'main_ref').get('object')
    sha = found.get('sha') if isinstance(found, dict) else None
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise Refused('main_ref_unreadable: main has no full head SHA; nothing dispatched')
    return sha


def unproven(why):
    return Refused(f'unproven: {why}; nothing dispatched')


def text_at(env, path, ref, opener):
    """PATH exactly as committed at REF, through the contents API, or Refused: never a copy from another commit."""
    try:
        data = read(env, f'/contents/{path}?ref={ref}', opener, 'contents')
    except Refused:
        raise unproven(f'{path} at {ref[:12]} is unreadable') from None
    if data.get('type') != 'file' or data.get('encoding') != 'base64' or not isinstance(data.get('content'), str):
        raise unproven(f'{path} at {ref[:12]} is not a readable file')
    try:
        return base64.b64decode(data['content']).decode()
    except ValueError:  # binascii.Error and UnicodeDecodeError
        raise unproven(f'{path} at {ref[:12]} is not text') from None


def push_paths(text):
    """App offline tests' push paths filter, read exactly: under `on:`, the one `push:` trigger holding `branches:
    [main]` and a block `paths:` list (quoted or bare items; full-line comments and blank lines anywhere). Any other
    key, shape or pattern syntax (negation, ?, classes, braces) refuses: a filter this does not read proves nothing."""
    lines = text.splitlines()
    if lines.count('on:') != 1:
        raise unproven(f'{APP_TESTS} has no single on: block')
    block = []
    for line in lines[lines.index('on:') + 1:]:
        if line.strip() and not line.startswith(' ') and not line.lstrip().startswith('#'):
            break
        block.append(line)
    push = [i for i, line in enumerate(block) if line == '  push:']
    if len(push) != 1:
        raise unproven(f'{APP_TESTS} has no single push trigger')
    branches, patterns, listing = None, [], False
    for line in block[push[0] + 1:]:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not line.startswith('    '):
            break  # the next trigger
        item = re.fullmatch(r"""      - (?:'([^']*)'|"([^"]*)"|([^'"\s#][^\s#]*))(?:\s+#.*)?""", line)
        if item and listing:
            pattern = next(group for group in item.groups() if group is not None)
            if not pattern or pattern.startswith('/') or UNREAD_GLOB.search(pattern):
                raise unproven(f'{APP_TESTS} has a push path pattern this does not read: {pattern!r}')
            patterns.append(pattern)
            continue
        key = re.fullmatch(r'    ([\w-]+):(.*)', line)
        name, value = (key[1], key[2].strip()) if key else (None, None)
        if name == 'branches' and value == '[main]' and branches is None:
            branches, listing = ['main'], False
        elif name == 'paths' and value == '' and not patterns and not listing:
            listing = True
        else:
            raise unproven(f'{APP_TESTS} has a push trigger line this does not read: {line.strip()[:80]!r}')
    if branches != ['main'] or not patterns:
        raise unproven(f'{APP_TESTS} push is not exactly branches [main] with a paths list')
    return patterns


def glob_regex(pattern):
    """A path-filter glob as a regex: ** crosses directories (**/ also matches none), * stays within one."""
    return ''.join({'**/': '(?:.*/)?', '**': '.*', '*': '[^/]*'}.get(token) or re.escape(token)
                   for token in re.findall(r'\*\*/|\*\*|.', pattern, re.S))


def sources_at(text):
    """dev.pixel.voice's sources named in an allocation.json text, or Refused."""
    try:
        sources = next(p['sources'] for p in json.loads(text)['packages'] if p['packageName'] == 'dev.pixel.voice')
    except (ValueError, KeyError, TypeError, StopIteration):
        raise unproven(f'{ALLOCATION} names no dev.pixel.voice sources') from None
    if not isinstance(sources, list) or not sources or not all(isinstance(s, str) and s.strip('/') for s in sources):
        raise unproven(f'{ALLOCATION} names no dev.pixel.voice sources')
    return [s.rstrip('/') for s in sources]


def equivalence(env, tested, head, opener):
    """None when main's head HEAD carries exactly what App offline tests checked at TESTED. Otherwise the deliberate
    reason it does not: a checked path changed after TESTED. Refused (unproven) when that cannot be shown completely.

    Checked paths: TESTED's own App offline tests push filter and Rose Agent sources, read at TESTED, and those two files.
    The comparison TESTED...HEAD must be complete: HEAD a descendant, every changed file listed (fewer than the compare
    API's 300), and a renamed file counted by both its names."""
    patterns = [re.compile(glob_regex(p)) for p in push_paths(text_at(env, APP_TESTS, tested, opener))]
    sources = sources_at(text_at(env, ALLOCATION, tested, opener)) + [ALLOCATION, APP_TESTS]
    try:
        data = read(env, f'/compare/{tested}...{head}', opener, 'compare')
    except Refused:
        raise unproven(f'the comparison {tested[:12]}...{head[:12]} is unreadable') from None
    if data.get('status') != 'ahead':
        raise unproven(f'main {head[:12]} is not a descendant of the tested {tested[:12]} (status {data.get("status")!r})')
    files = data.get('files')
    if not isinstance(files, list) or len(files) >= COMPARE_FILES_MAX:
        raise unproven(f'the comparison {tested[:12]}...{head[:12]} does not list every changed file')
    for entry in files:
        if not isinstance(entry, dict):
            raise unproven('the comparison has an unreadable file entry')
        for name in [entry.get('filename')] + ([entry.get('previous_filename')] if 'previous_filename' in entry else []):
            if not isinstance(name, str) or not name:
                raise unproven('the comparison has a file entry without a path')
            ci = any(p.fullmatch(name) for p in patterns)
            source = any(name == s or name.startswith(s + '/') for s in sources)
            if ci or source:
                what = ' and '.join(w for w, hit in (('an App offline tests path', ci), ('a Rose Agent source', source)) if hit)
                then = ("that change's own App offline tests run decides" if ci else "App offline tests never runs for "
                        "it, so it waits for that workflow's next run or a manual agent-hosted dispatch")
                return f'superseded: main moved to {head[:12]} after the tested {tested[:12]}, and {name} ({what}) changed; {then}'
    return None


def dispatch(env, opener=urllib.request.urlopen, pause=time.sleep, sources=None):
    """(the tested SHA, None) when that SHA was dispatched; (main's head, basis) when a proven equivalent head was;
    (None, reason) when nothing was. Refused when it refuses or cannot be sure, with nothing sent unless it says so."""
    if env.get('AGENT_BETA_AUTO', '') != 'on':
        return None, OFF  # the switch first: while it is off, nothing is read and nothing is sent
    run_id = env.get('CI_RUN_ID', '')
    if not run_id.isdigit():
        raise Refused('invalid: CI_RUN_ID')
    ci = read(env, f'/actions/runs/{run_id}', opener, 'ci_run')
    runs = read(env, '/actions/workflows/app-tests.yml/runs?branch=main&event=push&per_page=50', opener, 'ci_runs')
    newer = [r for r in runs.get('workflow_runs', []) if isinstance(r.get('id'), int) and r['id'] > int(run_id)]
    release_runs = lambda: read(env, f'/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&per_page=100', opener,
        'release_runs').get('workflow_runs', [])
    titles_of = lambda: {r.get('display_title') for r in release_runs()}
    runs_now = release_runs()
    sha, reason = decide(ci, newer, {r.get('display_title') for r in runs_now}, env.get('AGENT_BETA_AUTO', ''))
    if sha is None:
        return None, reason
    previous = unchanged_since(env, sha, runs_now, voice_sources() if sources is None else sources, opener)
    if previous:
        return None, f'unchanged: no Rose Agent source changed since {AUTO_PREFIX}{previous}'
    head = main_head(env, opener)  # last, right before the one request: the run builds the commit main is at
    basis = None
    if head != sha:
        why = equivalence(env, sha, head, opener)
        if why:
            return None, why
        if run_name(head) in {r.get('display_title') for r in runs_now}:
            return None, f'duplicate: {run_name(head)} already exists'
        again = main_head(env, opener)  # read last again: the proof covers exactly this head, and nothing is retried
        if again != head:
            raise unproven(f'main moved to {again[:12]} while {head[:12]} was being compared with the tested {sha[:12]}')
        basis = f'equivalent: main {head[:12]} carries exactly what App offline tests checked at {sha[:12]} (run {run_id})'
        sha = head
    body = {'ref': 'main', 'inputs': {'mode': MODE, 'candidate_sha': sha, 'app_publish': 'true'}}
    try:
        status, _ = api(env, 'POST', f'/actions/workflows/{WORKFLOW}/dispatches', body, opener=opener)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403, 404, 422):
            raise Refused(f'dispatch_refused: HTTP {error.code}; nothing was started') from None
        status = None
    except (urllib.error.URLError, OSError):
        status = None
    if status in (200, 204):
        return sha, basis
    pause(15)
    if run_name(sha) in titles_of():
        return sha, basis
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
    if sha:
        print(json.dumps({'dispatched': sha, **({'basis': reason} if reason else {})}))
    else:
        print(json.dumps({'dispatched': None, 'reason': reason}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
