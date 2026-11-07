#!/usr/bin/env python3
"""One-job Mustang runner enrollment. No persistent runner and no live call by default.

A GitHub-hosted job mints one just-in-time config for runner group 5, then
places it in this VM's instance metadata. The guest unit, running as
mustang-runner, reads that key once, starts run.sh --jitconfig, and deletes
the local copy. Source stays outside the runner work directory. Cleanup and
the queue watchdog remove the metadata key. Nothing here mints a token unless
--execute is passed, and --execute is not used by the tests.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import urllib.request

import mustang_lane

ORG = 'Immortal-Protocols'
API = '/orgs/Immortal-Protocols/actions/runners/generate-jitconfig'
LABEL = 'roseos-mustang-builder'
METADATA_KEY = 'mustang-jitconfig'
QUEUE_LIMIT_SECONDS = 12 * 60
RUNNER_ROOT = Path('/mnt/disks/mustang-data/roseos/runner')
SOURCE_ROOT = Path('/mnt/disks/mustang-data/roseos/source')
NODE = {
    'version': 'v24.22.2',
    'lts': 'Krypton',
    'date': '2026-10-27',
    'file': 'node-v24.22.2-linux-x64.tar.xz',
    'sha256': '48cc0e21e048790e54c0cf5749c2725005313d652651c40b7a21feb9697aaf75',
    'url': 'https://nodejs.org/dist/v24.22.2/node-v24.22.2-linux-x64.tar.xz',
    'sums': 'https://nodejs.org/dist/v24.22.2/SHASUMS256.txt',
    'install': '/mnt/disks/mustang-data/roseos/tools/node/node-v24.22.2-linux-x64/bin/node'}
SDK = {
    'url': 'https://dl.google.com/android/repository/build-tools_r35_linux.zip',
    'sha256': 'bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88',
    'source': 'scripts/setup-android-sdk.sh and os/models-signing/models-sign.json',
    'aapt': '/mnt/disks/mustang-data/roseos/tools/android-sdk/build-tools/android-15/aapt',
    'apksigner': '/mnt/disks/mustang-data/roseos/tools/android-sdk/build-tools/android-15/apksigner'}
RUNNER_APP = {
    'version': '2.338.2',
    'file': 'actions-runner-linux-x64-2.338.2.tar.gz',
    'sha256': 'baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40',
    'url': 'https://github.com/actions/runner/releases/download/v2.338.2/actions-runner-linux-x64-2.338.2.tar.gz',
    'install': str(RUNNER_ROOT)}


def paths():
    work = RUNNER_ROOT/'_work'
    if SOURCE_ROOT == work or work in SOURCE_ROOT.parents or SOURCE_ROOT in work.parents:
        raise ValueError('Mustang source cache must stay outside the runner work directory')
    return {'runner': RUNNER_ROOT, 'work': work, 'source': SOURCE_ROOT}


def jit_body(run_id, group_id=5):
    if not re.fullmatch(r'[1-9][0-9]{0,19}', str(run_id)):
        raise ValueError('JIT runner name must be this run id')
    if group_id != 5:
        raise ValueError('JIT enrollment is only for Mustang runner group 5')
    paths()
    return {'name': f'mustang-{run_id}', 'runner_group_id': 5,
            'labels': [LABEL], 'work_folder': '_work'}


def accept_jit(body, run_id):
    """Keep the encoded config out of the returned summary."""
    if not isinstance(body, dict):
        raise ValueError('JIT response is not an object')
    encoded = body.get('encoded_jit_config')
    runner = body.get('runner') if isinstance(body.get('runner'), dict) else {}
    if not isinstance(encoded, str) or len(encoded) < 20 or any(c in encoded for c in '\n\r'):
        raise ValueError('JIT config is missing')
    if runner.get('runner_group_id') != 5 or runner.get('ephemeral') is not True \
            or runner.get('name') != f'mustang-{run_id}':
        raise ValueError('JIT runner is not an ephemeral group 5 runner for this run')
    return {'name': runner['name'], 'groupId': 5, 'ephemeral': True, 'configBytes': len(encoded)}


def metadata_argv(action, instance=None):
    vm = mustang_lane.configuration()['vm']
    if instance is None:
        instance = vm['instance']
    if instance != vm['instance'] or vm['instanceId'] != mustang_lane.CONFIRMED_INSTANCE_ID:
        raise ValueError('metadata change is only for the dedicated Mustang VM')
    base = ['gcloud', 'compute', 'instances', action, vm['instance'],
            '--project='+vm['project'], '--zone='+vm['zone'], '--quiet']
    if action == 'add-metadata':
        return base + ['--metadata-from-file='+METADATA_KEY]
    if action == 'remove-metadata':
        return base + ['--keys='+METADATA_KEY]
    raise ValueError('metadata action refused')


def queue_decision(jobs, elapsed_seconds, limit=QUEUE_LIMIT_SECONDS):
    """ready: builder has been accepted. wait: still inside the bound. stop: cancel this run."""
    if type(elapsed_seconds) is not int or elapsed_seconds < 0:
        raise ValueError('queue wait is invalid')
    matches = [job for job in jobs or [] if isinstance(job, dict) and job.get('name') == 'builder']
    status = matches[-1].get('status') if matches else None
    if status in ('in_progress', 'completed'):
        return 'ready'
    if elapsed_seconds >= limit:
        return 'stop'
    return 'wait'


def cancel_path(run_id, expected):
    if not re.fullmatch(r'[1-9][0-9]{0,19}', str(run_id)) or str(run_id) != str(expected):
        raise ValueError('refusing to cancel a different workflow run')
    return f'/repos/Immortal-Protocols/RoseOS-1/actions/runs/{run_id}/cancel'


def watch(fetch, sleep, clock, limit=QUEUE_LIMIT_SECONDS):
    start = clock()
    while True:
        elapsed = int(clock() - start)
        decision = queue_decision(fetch(), elapsed, limit)
        if decision != 'wait':
            return decision
        remaining = limit - elapsed
        if remaining <= 0:
            return 'stop'
        sleep(min(30, remaining))


def jobs(run_id):
    if not re.fullmatch(r'[1-9][0-9]{0,19}', str(run_id)):
        raise ValueError('refusing to read a different workflow run')
    raw = subprocess.check_output(['gh', 'api', f'/repos/Immortal-Protocols/RoseOS-1/actions/runs/{run_id}/jobs', '--jq', '.jobs'], text=True)
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError('runner queue response is not a job list')
    return value


def post_json(path, body, token):
    request = urllib.request.Request(
        'https://api.github.com'+path, data=json.dumps(body).encode(), method='POST',
        headers={'Authorization': 'Bearer '+token, 'Accept': 'application/vnd.github+json',
                 'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2026-03-10'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def issue(run_id, token, post, out):
    if not isinstance(token, str) or len(token) < 20:
        raise ValueError('runner registrar token is missing')
    summary, encoded = accept_pair(post(API, jit_body(run_id), token), run_id)
    written = write_secret_file(out, encoded)
    return dict(summary, file=written['path'])


def accept_pair(body, run_id):
    summary = accept_jit(body, run_id)
    return summary, body['encoded_jit_config']


def require_group5(cfg):
    runner = cfg.get('runner') or {}
    if runner.get('groupName') != LABEL or str(runner.get('groupId')) != '5':
        raise ValueError('runner group is not the read-back Mustang group 5')
    if runner.get('lifetime') not in (None, 'ephemeral') or runner.get('registrationEvidence') is not None:
        raise ValueError('persistent or pre-registered Mustang runner is refused')
    if cfg.get('automaticStopHours') != 4 or cfg.get('dataFilesystemUuid') != mustang_lane.CONFIRMED_UUID:
        raise ValueError('dry run identity does not match the dedicated disk and four-hour STOP')
    return runner


def write_secret_file(path, payload):
    """Mode 0600, no overwrite, caller deletes it. The payload is not returned."""
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise ValueError('JIT file already exists')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        path.unlink()
        raise ValueError('JIT file mode is not private')
    return {'path': str(path), 'bytes': len(payload)}


def emit_stop(decision):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as handle:
            handle.write('stop='+('true' if decision == 'stop' else 'false')+'\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'issue', 'clear-metadata', 'queue-watch'))
    parser.add_argument('--run-id')
    parser.add_argument('--out')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'plan':
            body = jit_body(args.run_id or '1')
            value = {'request': body, 'endpoint': API, 'metadata': metadata_argv('add-metadata'),
                     'clear': metadata_argv('remove-metadata'), 'tools': {'node': NODE, 'sdk': SDK, 'runner': RUNNER_APP},
                     'execute': False}
        elif args.action == 'issue':
            if not args.execute:
                raise ValueError('JIT minting requires --execute and is not the offline plan')
            if not args.out or not args.run_id:
                raise ValueError('JIT minting needs this run id and a private output file')
            value = issue(args.run_id, os.environ.get('GH_TOKEN', ''), post_json, args.out)
        elif args.action == 'queue-watch':
            if not args.execute:
                value = {'limitSeconds': QUEUE_LIMIT_SECONDS, 'execute': False}
            else:
                run = os.environ['GITHUB_RUN_ID']
                decision = watch(lambda: jobs(run), time.sleep, time.monotonic)
                emit_stop(decision)
                value = {'decision': decision, 'runId': run}
        else:
            if args.execute:
                subprocess.check_call(metadata_argv('remove-metadata'))
                value = {'cleared': METADATA_KEY, 'execute': True}
            else:
                value = {'clear': metadata_argv('remove-metadata'), 'execute': False}
        print(json.dumps({k: v for k, v in value.items() if k != 'encoded_jit_config'}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Mustang ephemeral enrollment stopped: '+(str(error) if isinstance(error, ValueError) else type(error).__name__),
              file=sys.stderr)
        return 4


if __name__ == '__main__':
    sys.exit(main())
