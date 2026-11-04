"""Mustang public-receipt admission for normal output collection and one recorded lookup fault.

No cloud login, secrets, compilation or mutation of the original attempt.
"""
import datetime
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BASE = Path('/home/pixelbuild/rose-actions')
RECOVERY = ROOT/'os/build-only/mustang-collection-recovery.json'


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def recovery_record():
    return read(RECOVERY)


def recovery_run(run):
    r = recovery_record()
    return str(run.get('id')) == r['runId'] and run.get('run_attempt') == r['runAttempt'] \
        and run.get('head_sha') == r['source'] and run.get('path') == r['workflow'] \
        and run.get('display_title') == r['displayTitle'] and run.get('conclusion') == r['conclusion']


def admit(path, env):
    """Validate the genuine producer receipt; an incomplete make command is never admitted."""
    path = Path(path)
    v = read(path)
    if v.get('schema') != 'roseos.mustang-candidate-run.v1' or v.get('device') != 'mustang' \
            or v.get('source') != env.get('EXPECTED_COMPILE_SOURCE') \
            or str(v.get('runId')) != env.get('EXPECTED_COMPILE_RUN') or str(v.get('runAttempt')) != '1':
        raise ValueError('Mustang compile receipt differs from the hosted producer binding')
    if not re.fullmatch(r'[0-9a-f]{40}', str(v['source'])):
        raise ValueError('compile source SHA absent')
    a = v['allocation']
    build = str(a['build'])
    if not re.fullmatch(r'[1-9][0-9]{9}', build) or a.get('device') != 'mustang' \
            or a.get('source') != v['source'] or str(a.get('runId')) != str(v['runId']) \
            or str(a.get('runAttempt')) != '1':
        raise ValueError('allocation/source/device/run binding differs')
    source = BASE/f'mustang-prep-{v["runId"]}-1'/'source'
    if v['root'] != str(source.parent):
        raise ValueError('foreign source root')
    epoch = str(int(datetime.datetime.strptime(a['reservedAt'], '%Y-%m-%dT%H:%M:%SZ')
                    .replace(tzinfo=datetime.timezone.utc).timestamp()))
    integrate = v['stages']['integrate']
    c = v['stages']['compile']['result']
    if integrate.get('source') != v['source'] or integrate['result'].get('returncode') != 0 \
            or integrate['result'].get('stopped') or c.get('device') != 'mustang' \
            or c.get('stage') != 'compile' or c.get('returncode') != 0 or c.get('stopped') \
            or c.get('buildIdentity') != {'buildNumber':build, 'buildDatetime':epoch}:
        raise ValueError('integration/compile source or successful make outcome absent')
    if c != read(path.parent/'compile-completed.json'):
        raise ValueError('compile receipt body differs from its separate completed receipt')
    recover = env.get('EXPECTED_COMPILE_RECOVERY') == 'true'
    if recover:
        r = recovery_record()
        if str(v['runId']) != r['runId'] or v['source'] != r['source'] or build != r['build']:
            raise ValueError('not the exact admitted collection-only failure')
        for name, expected in r['receiptSha256'].items():
            if digest(path.parent/name) != expected:
                raise ValueError('recorded collection-fault receipt bytes differ')
        if v.get('failedStage') != 'compile' or v.get('unsignedCompileComplete') \
                or c.get('ok') is not False or c.get('error') != r['compileError']:
            raise ValueError('failure was not the admitted post-make output lookup')
        artifacts = c['outputs']['artifacts']
        if artifacts['otatools'].get('state') != 'missing' \
                or {k:row for k,row in artifacts.items() if row.get('state') == 'collected'} != r['preservedOutputs']:
            raise ValueError('original missing/preserved output cohort differs')
    elif v.get('unsignedCompileComplete') is not True or v.get('failedStage') \
            or c.get('ok') is not True or c['outputs'].get('verdict') != 'complete':
        raise ValueError('completed Mustang compile receipt required')
    return v, source, build, epoch, recover
