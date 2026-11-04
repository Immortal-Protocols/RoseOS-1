#!/usr/bin/env python3
"""Inactive-by-default Mustang dispatcher. No GCP identity, secrets or phone access.

Re-read trusted CI/run metadata; dispatch only existing os-release.yml. An API
denial stops; an uncertain dispatch is never submitted twice. Global release FIFO
and builder locks serialize actual work; this dispatcher coalesces pending events.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import urllib.error

import mustang_ota as ota

transport = ota.module('mustang_auto_transport', 'agent-beta-auto.py')
REPO = 'Immortal-Protocols/RoseOS-1'
CI_PATH, CI_NAME = '.github/workflows/mustang-ota-checks.yml', 'Mustang OTA checks'
WORKFLOW = 'os-release.yml'


def unchanged(published, fingerprint):
    eligible = [r for r in published if re.fullmatch(r'mustang-[0-9]{10}', r.get('tag_name', '')) and not r.get('draft')]
    latest = max(eligible, key=lambda r: r['tag_name']) if eligible else None
    return latest is not None and 'Content fingerprint: '+fingerprint in (latest.get('body') or '')


def decide(trigger, ci, main_sha, runs, published, fingerprint, cfg, validation_fingerprint=None):
    """Return workflow inputs, or a precise deliberate-skip reason. No external effects."""
    if (trigger.get('head_repository') or {}).get('full_name') != REPO or trigger.get('head_branch') != 'main':
        return None, 'foreign repository or branch'
    if trigger.get('status') != 'completed' or trigger.get('conclusion') != 'success' or trigger.get('run_attempt') != 1:
        return None, 'only a successful first attempt is eligible'
    if any(r.get('status') != 'completed' for r in runs):
        return None, 'existing OS work remains queued or active; completion event will reconsider'
    title = trigger.get('display_title', '')
    checked = ci is not None and (ci.get('name'), ci.get('path'), ci.get('event'), ci.get('head_branch'),
                                 ci.get('status'), ci.get('conclusion')) == (CI_NAME, CI_PATH, 'push', 'main', 'completed', 'success') \
        and (ci.get('head_repository') or {}).get('full_name') == REPO
    validation = validation_fingerprint or fingerprint
    if not checked or validation(ci['head_sha']) != validation(main_sha):
        return None, 'current relevant main checks are unavailable or differ from main'
    current = fingerprint(main_sha)
    if unchanged(published, current):
        return None, 'unchanged relevant content already published'
    if trigger.get('path') == '.github/workflows/os-release.yml' and title == 'mustang-build auto-'+trigger.get('head_sha', '') \
            and fingerprint(trigger['head_sha']) == fingerprint(main_sha):
        if trigger.get('mustangCompileSucceeded') is not True:
            return None, 'no successful Mustang compile job; a no-op run is not a producer'
        target = 'mustang-delivery ota-'+str(trigger['id'])
        if any(r.get('display_title') == target for r in runs):
            return None, 'delivery already attempted; diagnose before any retry'
        return {'mode': 'mustang-delivery', 'mustang_compile_run': str(trigger['id']),
                'mustang_bootstrap_run': cfg['bootstrapRun'], 'mustang_publish_ota': 'true'}, None
    title = 'mustang-build auto-'+main_sha
    if any(r.get('display_title') == title for r in runs):
        return None, 'source already attempted; failed work is not automatically retried'
    for run in runs:
        attempted = re.fullmatch(r'mustang-build auto-([0-9a-f]{40})', run.get('display_title', ''))
        if attempted and fingerprint(attempted[1]) == current:
            return None, 'same relevant content already attempted; diagnose before retrying'
    return {'mode': 'mustang-build', 'candidate_sha': main_sha, 'mustang_publish_ota': 'true'}, None


def os_runs(env):
    runs = []
    for page in range(1, 11):
        data = transport.read(env, f'/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&per_page=100&page={page}',
                              transport.urllib.request.urlopen, 'OS runs')
        rows = data['workflow_runs']
        runs.extend(rows)
        if len(rows) < 100:
            return runs
    raise ValueError('release run listing incomplete; nothing dispatched')


def skip_admitted(sha, run_id, runs, published, fingerprint):
    if unchanged(published, fingerprint(sha)):
        return True
    for run in runs:
        prior = re.fullmatch(r'mustang-build auto-([0-9a-f]{40})', run.get('display_title', ''))
        if prior and int(run['id']) < int(run_id) and fingerprint(prior[1]) == fingerprint(sha):
            return True
    return False


def dispatch(env):
    cfg = ota.config()
    # Off means no API reads or writes and no credential requirement.
    if env.get('MUSTANG_OTA_AUTO') != 'on' or not cfg['automationEnabled'] or not cfg['publicationEnabled']:
        return {'dispatched': False, 'reason': 'Mustang OTA automation is off'}
    ota.guard(cfg, automatic=True, access=True)
    if env.get('GITHUB_REPOSITORY') != REPO or env.get('GITHUB_EVENT_NAME') != 'workflow_run':
        raise ValueError('only the existing repository workflow_run dispatcher is admitted')
    run_id = env.get('CI_RUN_ID', '')
    if not re.fullmatch(r'[1-9][0-9]*', run_id):
        raise ValueError('positive trigger run id required')
    def get(path):
        return transport.read(env, path, transport.urllib.request.urlopen, 'Mustang scheduler metadata')
    trigger = get('/actions/runs/'+run_id)
    if trigger.get('path') == '.github/workflows/os-release.yml' and trigger.get('display_title', '').startswith('mustang-build auto-'):
        jobs = get('/actions/runs/'+run_id+'/jobs?per_page=100')
        if jobs['total_count'] > 100:
            raise ValueError('producer job listing incomplete')
        matching = [j for j in jobs['jobs'] if j['name'] == 'mustang-build']
        trigger['mustangCompileSucceeded'] = len(matching) == 1 and matching[0]['conclusion'] == 'success'
    main_sha = get('/git/ref/heads/main')['object']['sha']
    runs = os_runs(env)
    rows = get('/actions/workflows/mustang-ota-checks.yml/runs?branch=main&event=push&per_page=100')['workflow_runs']
    # A newer pending/failed CI is not replaced with an older passing result.
    ci = max(rows, key=lambda x: x['id']) if rows else None
    client = ota.publisher.GitHubReleases(ota.REPOSITORY, env['GH_TOKEN'])
    published = ota.release_rows(client)
    inputs, reason = decide(trigger, ci, main_sha, runs, published, ota.fingerprint, cfg, ota.validation_fingerprint)
    if inputs is None:
        return {'dispatched': False, 'reason': reason}
    try:
        status, _ = transport.api(env, 'POST', '/actions/workflows/os-release.yml/dispatches',
                                  {'ref': 'main', 'inputs': inputs})
    except urllib.error.HTTPError as error:
        if error.code in (401,403,404,422):
            raise ValueError(f'dispatch refused with HTTP {error.code}; not retried') from None
        raise ValueError(f'dispatch outcome uncertain after HTTP {error.code}; inspect runs before retrying') from None
    except (OSError, urllib.error.URLError):
        raise ValueError('dispatch outcome uncertain; inspect runs before any retry') from None
    if status not in (200, 204):
        raise ValueError('dispatch outcome uncertain; inspect runs before any retry')
    return {'dispatched': True, 'inputs': inputs, 'triggerRun': run_id}


def admit_build(env):
    """After acquiring the global workflow FIFO, recheck publication before starting a VM."""
    cfg = ota.config()
    ota.guard(cfg, automatic=True, access=True)
    if env.get('MUSTANG_OTA_AUTO') != 'on' or env.get('GITHUB_REF') != 'refs/heads/main' \
            or env.get('CANDIDATE_SHA') != env.get('GITHUB_SHA'):
        raise ValueError('automatic build must pin approved, tested main source')
    client = ota.publisher.GitHubReleases(ota.REPOSITORY, env['GH_TOKEN'])
    skip = skip_admitted(env['GITHUB_SHA'], env['GITHUB_RUN_ID'], os_runs(env), ota.release_rows(client), ota.fingerprint)
    with Path(env['GITHUB_OUTPUT']).open('a') as output:
        output.write('skip='+str(skip).lower()+'\n')
    return {'skip': skip, 'reason': 'rechecked published content after FIFO admission'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='dispatch', choices=('dispatch','admit-build'))
    args = parser.parse_args()
    try:
        print(json.dumps(admit_build(dict(os.environ)) if args.action == 'admit-build' else dispatch(dict(os.environ))))
    except (ValueError, KeyError, ota.publisher.PublishError, transport.Refused, ota.subprocess.SubprocessError) as error:
        print('Mustang OTA dispatcher stopped: '+str(error), file=sys.stderr)
        sys.exit(1)
