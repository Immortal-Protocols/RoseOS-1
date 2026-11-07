#!/usr/bin/env python3
"""One-job Mustang runner enrollment. No persistent runner and no live call by default.

A GitHub-hosted job mints one just-in-time config for runner group 5, then
places it in this VM's instance metadata. The guest unit, running as
mustang-runner, reads that key once, starts run.sh with the encoded config
in ACTIONS_RUNNER_INPUT_JITCONFIG, and deletes
the local copy. Source stays outside the runner work directory. Cleanup and
the queue watchdog remove the metadata key. Nothing here mints a token unless
--execute is passed, and --execute is not used by the tests.
"""
import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

import mustang_lane

ORG = 'Immortal-Protocols'
API = '/orgs/Immortal-Protocols/actions/runners/generate-jitconfig'
GROUP_RUNNERS = '/orgs/Immortal-Protocols/actions/runner-groups/5/runners'
LABEL = 'roseos-mustang-builder'
READ_ONLY_LABELS = {'self-hosted', 'Linux', 'X64', 'linux', 'x64'}
ONE_JOB_DOC = 'https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/remove-runners'
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
    'source': 'scripts/setup-android-sdk.sh',
    'aapt': '/mnt/disks/mustang-data/roseos/tools/android-sdk/build-tools/android-15/aapt',
    'apksigner': '/mnt/disks/mustang-data/roseos/tools/android-sdk/build-tools/android-15/apksigner'}
RUNNER_APP = {
    'version': '2.338.2',
    'file': 'actions-runner-linux-x64-2.338.2.tar.gz',
    'sha256': 'baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40',
    'digestSource': 'GitHub release asset digest for actions/runner v2.338.2 actions-runner-linux-x64-2.338.2.tar.gz',
    'url': 'https://github.com/actions/runner/releases/download/v2.338.2/actions-runner-linux-x64-2.338.2.tar.gz',
    'install': str(RUNNER_ROOT)}
APP_PERMISSION = {
    'name': 'Organization permissions for Self-hosted runners',
    'access': 'write',
    'appId': '5657668',
    'installationId': '179610845',
    'token': 'GitHub App installation access token',
    'endpoint': 'POST /orgs/{org}/actions/runners/generate-jitconfig',
    'readback': 'GET /orgs/{org}/actions/runners/{runner_id}',
    'listGroup': 'GET /orgs/{org}/actions/runner-groups/5/runners',
    'delete': 'DELETE /orgs/{org}/actions/runners/{runner_id}',
    'source': 'https://docs.github.com/en/rest/actions/self-hosted-runners#create-configuration-for-a-just-in-time-runner-for-an-organization',
    'oneJob': 'GitHub documents that a just-in-time runner can only run a single job. The generate-jitconfig endpoint has no persistent mode.',
    'ephemeralField': 'The runner schema lists ephemeral as an optional boolean. A live readback can omit it as null. Null is not proof, and false is rejected.',
    'groupField': 'runner_group_id is also optional on that schema. Membership is GET /orgs/{org}/actions/runner-groups/5/runners, not a missing field.',
    'removal': ONE_JOB_DOC,
    'notUsed': 'repository permissions and admin:org user token',
    'installation': 'Organization installation 179610845 of app 5657668. It is not installed on the VM.'}


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


def positive_int(value, label):
    if isinstance(value, bool) or type(value) is not int or value < 1:
        raise ValueError(label+' must be a positive int')
    return value


def label_names(labels):
    if not isinstance(labels, list) or not labels:
        raise ValueError('JIT runner labels differ')
    names = []
    for label in labels:
        if isinstance(label, str):
            name, kind = label, None
        elif isinstance(label, dict) and type(label.get('name')) is str:
            name, kind = label['name'], label.get('type')
        else:
            raise ValueError('JIT runner labels differ')
        if kind == 'custom' and name != LABEL:
            raise ValueError('JIT runner labels differ')
        if kind != 'read-only' and name not in READ_ONLY_LABELS | {LABEL}:
            raise ValueError('JIT runner labels differ')
        names.append(name)
    if LABEL not in names:
        raise ValueError('JIT runner labels differ')
    return names


def accept_jit(body, run_id):
    """Prove one job from the JIT endpoint. A null ephemeral field is not that proof."""
    if not isinstance(body, dict):
        raise ValueError('JIT response is not an object')
    if 'encoded_jit_config' not in body and 'token' in body:
        raise ValueError('registration token is not a just-in-time runner')
    encoded = body.get('encoded_jit_config')
    runner = body.get('runner') if isinstance(body.get('runner'), dict) else {}
    if not isinstance(encoded, str) or len(encoded) < 20 or any(c in encoded for c in '\n\r'):
        raise ValueError('JIT config is missing')
    name = f'mustang-{run_id}'
    if 'runner_group_id' in runner:
        reported_group = runner.get('runner_group_id')
        if isinstance(reported_group, bool) or type(reported_group) is not int or reported_group != 5:
            raise ValueError('JIT runner is not an ephemeral group 5 runner for this run')
    else:
        reported_group = None
    if runner.get('name') != name or runner.get('status') != 'offline' or runner.get('busy') is not False \
            or type(runner.get('os')) is not str or not runner.get('os'):
        raise ValueError('JIT runner is not an ephemeral group 5 runner for this run')
    label_names(runner.get('labels'))
    runner_id = positive_int(runner.get('id'), 'runner id')
    reported = runner.get('ephemeral', None)
    if reported is not None and reported is not True and reported is not False:
        raise ValueError('JIT runner is not an ephemeral group 5 runner for this run')
    config_agrees(encoded, run_id, runner_id)
    return {'name': name, 'id': runner_id, 'responseGroupId': reported_group,
            'ephemeral': True if reported is True else (False if reported is False else None),
            'oneJob': reported is not False, 'oneJobProof': 'POST '+API, 'configBytes': len(encoded)}


def config_agrees(encoded, run_id, runner_id):
    """Reject a decoded config that names a different runner. Never return its credential fields."""
    try:
        parsed = json.loads(base64.b64decode(encoded, validate=True).decode())
    except (binascii.Error, ValueError, json.JSONDecodeError, UnicodeError):
        return
    if not isinstance(parsed, dict):
        raise ValueError('JIT config is missing')
    expected = f'mustang-{run_id}'
    for key in ('runnerName', 'runner_name', 'name'):
        if key in parsed and parsed.get(key) != expected:
            raise ValueError('JIT config name differs')
    for key in ('runnerId', 'runner_id', 'id'):
        if key in parsed and parsed.get(key) != runner_id:
            raise ValueError('JIT config runner id differs')
    inner = parsed.get('runner')
    if isinstance(inner, dict):
        if 'name' in inner and inner.get('name') != expected:
            raise ValueError('JIT config name differs')
        if 'id' in inner and inner.get('id') != runner_id:
            raise ValueError('JIT config runner id differs')


def confirm_readback(readback, run_id, runner_id):
    """The get-runner schema can omit ephemeral. False is a contradiction, not a missing field."""
    if not isinstance(readback, dict):
        raise ValueError('JIT runner readback is not an object')
    if readback.get('name') != f'mustang-{run_id}' or positive_int(readback.get('id'), 'runner id') != runner_id:
        raise ValueError('JIT runner readback does not match this run')
    if readback.get('ephemeral') is False:
        raise ValueError('JIT runner is not an ephemeral group 5 runner for this run')
    if readback.get('ephemeral') is not None and readback.get('ephemeral') is not True:
        raise ValueError('JIT runner readback ephemeral field is not a boolean')
    if readback.get('busy') is not False or readback.get('status') != 'offline':
        raise ValueError('JIT runner readback is not an offline unused runner')
    label_names(readback.get('labels'))
    if 'runner_group_id' in readback and positive_int(readback.get('runner_group_id'), 'runner group') != 5:
        raise ValueError('JIT runner readback is not group 5')
    return {'ephemeral': readback.get('ephemeral', None), 'omitted': readback.get('ephemeral', None) is None}


def group_page_path(page):
    if isinstance(page, bool) or type(page) is not int or page < 1:
        raise ValueError('runner list page is incomplete')
    return GROUP_RUNNERS+'?per_page=100&page='+str(page)


def collect_pages(fetch):
    """Read every group 5 page. A short page is not membership and not a delete list."""
    collected = []
    total = None
    for page in range(1, 21):
        payload = fetch(page)
        if not isinstance(payload, dict) or not isinstance(payload.get('runners'), list) \
                or isinstance(payload.get('total_count'), bool) or type(payload.get('total_count')) is not int:
            raise ValueError('runner list page is incomplete')
        if total is None:
            total = payload['total_count']
        elif payload['total_count'] != total:
            raise ValueError('runner list page is incomplete')
        if payload['total_count'] < len(collected):
            raise ValueError('runner list page is incomplete')
        collected.extend(payload['runners'])
        if len(collected) == total:
            return collected
        if not payload['runners'] or len(collected) > total:
            raise ValueError('runner list page is incomplete')
    raise ValueError('runner list page is incomplete')


def group5_member(runners, run_id, runner_id):
    """Membership is an entry in the group 5 list. A missing group field is not membership."""
    if not isinstance(runners, list):
        raise ValueError('JIT runner is not a member of group 5')
    name = f'mustang-{run_id}'
    hits = [runner for runner in runners if isinstance(runner, dict)
            and (runner.get('id') == runner_id or runner.get('name') == name)]
    if len(hits) != 1:
        raise ValueError('JIT runner is not a member of group 5')
    runner = hits[0]
    if positive_int(runner.get('id'), 'runner id') != runner_id or runner.get('name') != name:
        raise ValueError('JIT runner is not a member of group 5')
    label_names(runner.get('labels'))
    if 'runner_group_id' in runner and (isinstance(runner.get('runner_group_id'), bool)
                                        or type(runner.get('runner_group_id')) is not int
                                        or runner.get('runner_group_id') != 5):
        raise ValueError('JIT runner group readback contradicts group 5')
    return runner


def orphan_ids(runners, run_id):
    """Offline, idle, exact-name leftovers. A busy or online runner with this name is left alone."""
    if not isinstance(runners, list):
        raise ValueError('runner list is not a list')
    name = f'mustang-{run_id}'
    named = [runner for runner in runners if isinstance(runner, dict) and runner.get('name') == name]
    if any(runner.get('busy') is not False or runner.get('status') != 'offline' for runner in named):
        return []
    found = []
    for runner in named:
        try:
            label_names(runner.get('labels'))
            if 'runner_group_id' in runner and positive_int(runner.get('runner_group_id'), 'runner group') != 5:
                continue
            found.append(positive_int(runner.get('id'), 'runner id'))
        except ValueError:
            continue
    return found


def contains_secret(text, secret):
    """True when the encoded JIT config or its decoded bytes appear in text."""
    if not secret:
        return False
    if secret in text:
        return True
    try:
        decoded = base64.b64decode(secret, validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(decoded) < 8:
        return False
    return decoded.decode('latin-1') in text


def guest_attribute_argv():
    vm = mustang_lane.configuration()['vm']
    return ['gcloud', 'compute', 'instances', 'get-guest-attributes', vm['instance'],
            '--project='+vm['project'], '--zone='+vm['zone'],
            '--query-path=mustang/jit-fetched', '--format=json']


def metadata_keys_argv():
    vm = mustang_lane.configuration()['vm']
    # items.key without a slice is a null projection. items[].key is the key list.
    return ['gcloud', 'compute', 'instances', 'describe', vm['instance'],
            '--project='+vm['project'], '--zone='+vm['zone'],
            '--format=json(metadata.items[].key)']


def metadata_key_names(described):
    """Known gcloud shapes only. Null and unknown documents are not an empty key list."""
    if isinstance(described, list):
        if any(type(item) is not str or not item for item in described):
            raise ValueError('metadata key readback is not a key list')
        return list(described)
    if not isinstance(described, dict) or 'metadata' not in described or not isinstance(described.get('metadata'), dict) \
            or 'items' not in described['metadata']:
        raise ValueError('metadata key readback is not a key list')
    items = described['metadata']['items']
    if isinstance(items, list):
        names = []
        for item in items:
            if not isinstance(item, dict) or type(item.get('key')) is not str or not item['key']:
                raise ValueError('metadata key readback is not a key list')
            names.append(item['key'])
        return names
    if isinstance(items, dict) and isinstance(items.get('key'), list) \
            and all(type(item) is str and item for item in items['key']):
        return list(items['key'])
    raise ValueError('metadata key readback is not a key list')


def metadata_is_present(described):
    return METADATA_KEY in metadata_key_names(described)


def parse_metadata_document(raw):
    if type(raw) is not str or not raw.strip():
        raise ValueError('metadata key readback is not a key list')
    try:
        described = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError('metadata key readback is not a key list') from None
    return metadata_key_names(described)


def config_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def acknowledgement(payload, expected):
    """True only for this config. A stale value is not ready. A bad document is an error."""
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('guest acknowledgement is not bound to this config')
    if not isinstance(payload, list):
        raise ValueError('guest attribute readback is not a list')
    matches = []
    for item in payload:
        if not isinstance(item, dict) or type(item.get('key')) is not str or not item.get('key') \
                or type(item.get('namespace')) is not str or not item.get('namespace') \
                or type(item.get('value')) is not str:
            raise ValueError('guest attribute readback is not a list')
        if item['namespace'] != 'mustang':
            raise ValueError('guest acknowledgement namespace differs')
        if item['key'] != 'jit-fetched':
            raise ValueError('guest acknowledgement key differs')
        matches.append(item['value'])
    if len(matches) > 1:
        raise ValueError('guest acknowledgement is duplicated')
    if not matches:
        return False
    return matches[0] == expected


# Readonly gcloud stderr for a missing guest attribute is one line: HTTPError 404,
# resource '<namespace>/<key>' of type 'Guest Attribute', then the active-account sentence.
# The probe that established the shape used mustang/nonexistent-probe. This query accepts
# only mustang/jit-fetched. The account must be the operator who captured that line or the
# dedicated controller the Actions enroll job uses. The stream is not returned.
GUEST_ATTRIBUTE_404_HEADER = (
    'ERROR: (gcloud.compute.instances.get-guest-attributes) HTTPError 404: '
    "The resource '%s' of type 'Guest Attribute' was not found. "
    'This command is authenticated as ')
GUEST_ATTRIBUTE_404_SUFFIX = ' which is the active account specified by the [core/account] property.'
GUEST_ATTRIBUTE_ACCOUNTS = (
    'grok-bots@protocols-465723.iam.gserviceaccount.com',
    'mustang-controller@protocols-465723.iam.gserviceaccount.com',
)


def missing_guest_attribute(result):
    """True only for exit 1, empty stdout, and the captured Guest Attribute 404 for this key."""
    if getattr(result, 'returncode', None) != 1:
        return False
    stderr = getattr(result, 'stderr', None)
    stdout = getattr(result, 'stdout', None)
    if type(stderr) is not str or type(stdout) is not str or stdout.strip():
        return False
    text = stderr.strip()
    prefix = GUEST_ATTRIBUTE_404_HEADER % 'mustang/jit-fetched'
    if not text.startswith(prefix) or not text.endswith(GUEST_ATTRIBUTE_404_SUFFIX):
        return False
    account = text[len(prefix):-len(GUEST_ATTRIBUTE_404_SUFFIX)]
    return account in GUEST_ATTRIBUTE_ACCOUNTS and text == prefix + account + GUEST_ATTRIBUTE_404_SUFFIX


def watch_decision(enroll_result, jobs, elapsed_seconds, limit=QUEUE_LIMIT_SECONDS):
    """Enroll failure stops immediately. A queued builder stops only after the bound."""
    if enroll_result != 'success':
        return 'stop'
    return queue_decision(jobs, elapsed_seconds, limit)


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


def selected_guest_jobs(jobs):
    """The unselected guest job is completed with conclusion skipped. That is not acceptance."""
    found = []
    for job in jobs or []:
        if not isinstance(job, dict) or job.get('name') not in ('builder', 'source'):
            continue
        if job.get('status') == 'skipped' or job.get('conclusion') == 'skipped':
            continue
        found.append(job)
    return found


def queue_decision(jobs, elapsed_seconds, limit=QUEUE_LIMIT_SECONDS):
    """ready: the selected guest job was accepted. wait: still inside the bound. stop: cancel this run."""
    if type(elapsed_seconds) is not int or elapsed_seconds < 0:
        raise ValueError('queue wait is invalid')
    rows = selected_guest_jobs(jobs)
    if any(job.get('status') in ('in_progress', 'completed') for job in rows):
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


def allowed_path(method, path):
    if path.endswith('/registration-token'):
        raise ValueError('registration token is not a just-in-time runner')
    if method == 'POST' and path == API:
        return
    if method == 'GET' and path.split('?', 1)[0] == GROUP_RUNNERS:
        query = path[len(GROUP_RUNNERS):]
        if query == '' or re.fullmatch(r'\?per_page=100&page=[1-9][0-9]*', query):
            return
        raise ValueError('runner request leaves the Mustang organization runners API')
    if method in ('GET', 'DELETE') and re.fullmatch(r'/orgs/Immortal-Protocols/actions/runners/[1-9][0-9]*', path):
        return
    if method == 'POST' and re.fullmatch(r'/repos/Immortal-Protocols/RoseOS-1/actions/runs/[1-9][0-9]*/cancel', path):
        return
    raise ValueError('runner request leaves the Mustang organization runners API')


def api(method, path, token, body=None):
    if not isinstance(token, str) or len(token) < 20:
        raise ValueError('runner registrar token is missing')
    allowed_path(method, path)
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        'https://api.github.com'+path, data=data, method=method,
        headers={'Authorization': 'Bearer '+token, 'Accept': 'application/vnd.github+json',
                 'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2026-03-10'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise ValueError('JIT request failed with HTTP '+str(error.code)) from None
    if not raw:
        return {}
    value = json.loads(raw.decode())
    if not isinstance(value, dict):
        raise ValueError('JIT response is not an object')
    return value


def post_json(path, body, token):
    return api('POST', path, token, body)


def get_json(path, token):
    return api('GET', path, token)


def delete_json(path, token):
    return api('DELETE', path, token)


def issue(run_id, token, post, out, group, delete=None):
    if not isinstance(token, str) or len(token) < 20:
        raise ValueError('runner registrar token is missing')
    if group is None:
        raise ValueError('JIT runner is not a member of group 5')
    body = post(API, jit_body(run_id), token)
    proven = None
    try:
        summary, encoded = accept_pair(body, run_id)
        member = group5_member(collect_pages(lambda page: group(page, token)), run_id, summary['id'])
        if member.get('busy') is not False or member.get('status') != 'offline':
            raise ValueError('JIT runner readback is not an offline unused runner')
        proven = summary['id']
        if summary['ephemeral'] is False or member.get('ephemeral') is False:
            raise ValueError('JIT runner is not an ephemeral group 5 runner for this run')
        checked = confirm_readback(member, run_id, summary['id'])
        summary = dict(summary, groupId=5, groupProof='GET '+GROUP_RUNNERS,
                       readbackEphemeral=checked['ephemeral'], readbackEphemeralOmitted=checked['omitted'])
        written = write_secret_file(out, encoded)
    except Exception:
        if delete is not None and proven is not None:
            try:
                delete('/orgs/Immortal-Protocols/actions/runners/'+str(proven), token)
            except Exception:
                pass
        path = Path(out)
        if path.exists() and not path.is_symlink():
            path.unlink()
        raise
    return dict(summary, file=written['path'])


def sweep(run_id, token, group, delete):
    if not re.fullmatch(r'[1-9][0-9]{0,19}', str(run_id)):
        raise ValueError('JIT runner name must be this run id')
    runners = collect_pages(lambda page: group(page, token))
    deleted = []
    for runner_id in orphan_ids(runners, run_id):
        delete('/orgs/Immortal-Protocols/actions/runners/'+str(runner_id), token)
        deleted.append(runner_id)
    return {'deleted': deleted, 'runId': str(run_id), 'groupId': 5}


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


def public_error(error):
    if not isinstance(error, ValueError):
        return type(error).__name__
    return re.sub(r'\S{20,}', '[redacted]', str(error))


def private_jit_file(value):
    """A 0600 regular file. The bytes are not read and are not part of any error."""
    if type(value) is not str or not value or any(char in value for char in '\n\r\x00') or '..' in Path(value).parts:
        raise ValueError('JIT metadata file must be a private regular file')
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('JIT metadata file must be a private regular file')
    try:
        info = path.lstat()
    except OSError:
        raise ValueError('JIT metadata file must be a private regular file') from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('JIT metadata file must be a private regular file')
    return path


def emit_stop(decision):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as handle:
            handle.write('stop='+('true' if decision == 'stop' else 'false')+'\n')


def main(argv=None, transport=None, sleep=None, clock=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'issue', 'put-metadata', 'wait-fetched', 'clear-metadata', 'queue-watch', 'cancel', 'sweep'))
    parser.add_argument('--run-id')
    parser.add_argument('--out')
    parser.add_argument('--file')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.file is not None and args.action not in ('put-metadata', 'wait-fetched'):
            raise ValueError('private JIT file is only accepted by put-metadata and wait-fetched')
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
            value = issue(args.run_id, os.environ.get('GH_TOKEN', ''), post_json, args.out,
                          lambda page, tok: get_json(group_page_path(page), tok), delete_json)
        elif args.action == 'put-metadata':
            if not args.execute or not args.file:
                raise ValueError('metadata delivery requires --execute and the private file')
            path = private_jit_file(args.file)
            argv = metadata_argv('add-metadata')
            argv[-1] = argv[-1]+'='+str(path)
            if transport is None:
                subprocess.check_call(argv)
            else:
                result = transport(argv)
                if getattr(result, 'returncode', 0):
                    raise subprocess.CalledProcessError(result.returncode, argv)
            value = {'delivered': METADATA_KEY, 'execute': True}
        elif args.action == 'wait-fetched':
            if not args.execute:
                value = {'query': 'mustang/jit-fetched', 'execute': False}
            else:
                if not args.file:
                    raise ValueError('metadata delivery requires --execute and the private file')
                path = private_jit_file(args.file)
                expected = config_digest(path)
                pause = sleep or time.sleep
                now = clock or time.monotonic
                deadline = now() + 180
                seen = False
                while now() <= deadline:
                    query = guest_attribute_argv()
                    if transport is None:
                        result = subprocess.run(query, capture_output=True, text=True)
                    else:
                        result = transport(query)
                    if result.returncode != 0:
                        # A missing key is not ready yet. Any other failure stops without echoing the stream.
                        if not missing_guest_attribute(result):
                            raise ValueError('guest attribute query failed: exit %s' % result.returncode)
                        payload = []
                    else:
                        try:
                            payload = json.loads(result.stdout) if result.stdout.strip() else []
                        except json.JSONDecodeError:
                            raise ValueError('guest attribute readback is not a list') from None
                    if acknowledgement(payload, expected):
                        seen = True
                        break
                    if now() >= deadline:
                        break
                    pause(5)
                if not seen:
                    raise ValueError('guest acknowledgement did not match this config before the bound')
                value = {'fetched': True, 'execute': True}
        elif args.action == 'queue-watch':
            enroll_result = os.environ.get('ENROLL_RESULT', 'success')
            if not args.execute:
                value = {'limitSeconds': QUEUE_LIMIT_SECONDS, 'execute': False,
                         'enrollFailureStops': True}
            elif enroll_result != 'success':
                emit_stop('stop')
                value = {'decision': 'stop', 'runId': os.environ.get('GITHUB_RUN_ID', '')}
            else:
                run = os.environ['GITHUB_RUN_ID']
                decision = watch(lambda: jobs(run), time.sleep, time.monotonic)
                emit_stop(decision)
                value = {'decision': decision, 'runId': run}
        elif args.action == 'sweep':
            if not args.execute:
                value = {'groupId': 5, 'execute': False, 'runId': args.run_id}
            else:
                if not args.run_id:
                    raise ValueError('runner sweep needs this run id')
                value = sweep(args.run_id, os.environ.get('GH_TOKEN', ''),
                              lambda page, tok: get_json(group_page_path(page), tok), delete_json)
        elif args.action == 'cancel':
            if not args.execute:
                raise ValueError('cancel requires --execute')
            run = os.environ['GITHUB_RUN_ID']
            post_json(cancel_path(run, run), {}, os.environ.get('GH_TOKEN', ''))
            value = {'cancelled': run}
        else:
            if args.execute:
                query = metadata_keys_argv()

                def read_keys():
                    if transport is None:
                        raw = subprocess.check_output(query, text=True)
                    else:
                        raw = transport(query).stdout
                    return parse_metadata_document(raw)

                if METADATA_KEY not in read_keys():
                    value = {'cleared': METADATA_KEY, 'state': 'absent'}
                else:
                    remove = metadata_argv('remove-metadata')
                    if transport is None:
                        subprocess.check_call(remove)
                    else:
                        result = transport(remove)
                        if getattr(result, 'returncode', 0):
                            raise subprocess.CalledProcessError(result.returncode, remove)
                    if METADATA_KEY in read_keys():
                        raise ValueError('JIT metadata key remained after removal')
                    value = {'cleared': METADATA_KEY, 'state': 'removed'}
            else:
                value = {'clear': metadata_argv('remove-metadata'), 'execute': False}
        print(json.dumps({k: v for k, v in value.items() if k != 'encoded_jit_config'}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print('Mustang ephemeral enrollment stopped: '+public_error(error), file=sys.stderr)
        return 4


if __name__ == '__main__':
    sys.exit(main())
