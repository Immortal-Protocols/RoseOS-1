#!/usr/bin/env python3
"""MUSTANG-OTA one-button chain binding (Release-owned) for .github/workflows/os-release.yml mode mustang-ota.

One manual dispatch runs the existing Mustang producer and then the existing signing and publication jobs in the same
workflow run: mode mustang-ota, mustang_publish_ota true, every other input at its default. This helper replaces the
producer run id that a mustang-delivery dispatch takes by hand. It binds the delivery to exactly this run's own
successful producer.

  bind --out NEW.json     in the hosted mustang-ota-bind job, after this run's mustang-build job succeeded

It refuses (exit 2), in this order, unless:
1. the Mustang signing read-access window is approved and has at least 30 minutes left (scripts/mustang_ota.py guard).
   This is the second of the chain's three window checks. The guard job makes the first, and the signer job makes the
   third right before signer login;
2. this is attempt 1 of a workflow_dispatch of os-release.yml on main, titled `mustang-ota <run id>`. Its head must be
   GITHUB_SHA and an ancestor of origin/main. The producer's receipts and the collector bind attempt 1 only, so a
   re-run is never a producer;
3. exactly one job named mustang-build exists in this run's latest attempt, and it completed with conclusion success;
4. exactly one artifact named mustang-receipts-<run id>-1 exists in this run, and it is unexpired;
5. the bootstrap is the committed original (os/ota/mustang.json bootstrapRun), and its run metadata matches the
   committed public receipt, exactly as scripts/mustang-delivery.py guard checks it for an OTA delivery.

There is no make=0 collection-fault recovery here, so compile_recovery is always false. That recorded exception stays
with the manual mustang-delivery mode. Nothing is retried.

Output:
- the binding, written once to NEW.json;
- compile_source, compile_recovery=false and bootstrap_source, written to GITHUB_OUTPUT. The existing signer job checks
  them through EXPECTED_COMPILE_SOURCE, EXPECTED_COMPILE_RECOVERY and EXPECTED_BOOTSTRAP_SOURCE, and its collector
  checks EXPECTED_COMPILE_RUN (this run) and attempt 1.

It reads GitHub run metadata with the job's own read-only token through scripts/mustang-delivery.py request. It reads
no secret and makes no write or dispatch. The Mustang helpers it imports are unchanged.
Exit status: 0 bound, 2 refused.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import mustang_ota  # noqa: E402

WORKFLOW = '.github/workflows/os-release.yml'
MODE = 'mustang-ota'
PRODUCER_JOB = 'mustang-build'
BOOTSTRAP_RECEIPT = 'os/signing-bootstrap/mustang-bootstrap-receipt.json'
SHA = re.compile(r'[0-9a-f]{40}')


class Refused(Exception):
    pass


def delivery():
    """scripts/mustang-delivery.py, loaded unchanged (its request and number helpers)."""
    spec = importlib.util.spec_from_file_location('mustang_delivery_for_chain', ROOT/'scripts/mustang-delivery.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def on_main(sha, root=ROOT):
    return subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', sha, 'origin/main']).returncode == 0


def run_id_of(value, number):
    try:
        return number(value)
    except ValueError:
        raise Refused('a positive numeric run id is required') from None


def bind(env=None, request=None, number=None, ancestor=on_main, cfg=None, now=None, root=ROOT):
    """The same-run producer binding, or Refused. No external effect."""
    env = os.environ if env is None else env
    if request is None or number is None:
        module = delivery()
        request, number = request or module.request, number or module.number
    cfg = mustang_ota.config() if cfg is None else cfg
    try:
        mustang_ota.guard(cfg, access=True, now=now)  # the chain's second signing-window check
    except ValueError as error:
        raise Refused(f'signing access: {error}') from None
    if env.get('GITHUB_REF') != 'refs/heads/main' or env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch':
        raise Refused('the chain binds only a manual workflow_dispatch run on main')
    run_id = run_id_of(env.get('GITHUB_RUN_ID'), number)
    if env.get('GITHUB_RUN_ATTEMPT') != '1':
        raise Refused('the chain binds attempt 1 only: a re-run is never a producer (deliver it by hand with mustang-delivery)')
    sha = env.get('GITHUB_SHA', '')
    if not SHA.fullmatch(sha):
        raise Refused('GITHUB_SHA is not a full commit id')
    try:
        run = request(f'/actions/runs/{run_id}')
        if (run.get('path'), run.get('head_branch'), run.get('event'), run.get('run_attempt'), run.get('display_title'),
                run.get('head_sha')) != (WORKFLOW, 'main', 'workflow_dispatch', 1, f'{MODE} {run_id}', sha):
            raise Refused('this run is not attempt 1 of the manual mustang-ota dispatch of os-release.yml on main')
        if not ancestor(sha):
            raise Refused('the run commit is not an ancestor of origin/main')
        jobs = request(f'/actions/runs/{run_id}/jobs?filter=latest&per_page=100')
        if jobs.get('total_count', 0) > 100:
            raise Refused('the job listing is incomplete')
        producers = [job for job in jobs.get('jobs', []) if job.get('name') == PRODUCER_JOB]
        if len(producers) != 1 or (producers[0].get('status'), producers[0].get('conclusion'),
                                   producers[0].get('run_attempt')) != ('completed', 'success', 1):
            raise Refused('this run has no single successful attempt-1 mustang-build producer job')
        artifacts = request(f'/actions/runs/{run_id}/artifacts?per_page=100')
        if artifacts.get('total_count', 0) > 100:
            raise Refused('the artifact listing is incomplete')
        name = f'mustang-receipts-{run_id}-1'
        receipts = [row for row in artifacts.get('artifacts', []) if row.get('name') == name]
        if len(receipts) != 1 or receipts[0].get('expired') is not False:
            raise Refused(f'exactly one unexpired {name} artifact is required')
        bootstrap_run = run_id_of(cfg.get('bootstrapRun'), number)
        receipt = json.loads((Path(root)/BOOTSTRAP_RECEIPT).read_text())
        original = request(f'/actions/runs/{bootstrap_run}')
    except (OSError, ValueError) as error:  # urllib errors are OSErrors; malformed JSON is a ValueError
        raise Refused(f'run metadata unavailable: {type(error).__name__}') from None
    if (original.get('head_sha'), original.get('conclusion'), original.get('status'), original.get('run_attempt'),
            original.get('path'), original.get('head_branch')) != (receipt['recipeSha'], 'success', 'completed', 1, WORKFLOW,
                                                                   'main'):
        raise Refused('the original completed bootstrap metadata differs from its committed public receipt')
    row = receipts[0]
    return {'mode': MODE, 'recipeSource': sha,
            'compile': {'runId': run_id, 'runAttempt': 1, 'job': PRODUCER_JOB, 'jobId': producers[0].get('id'),
                        'source': sha, 'artifactId': row.get('id'), 'artifactName': name, 'artifactDigest': row.get('digest'),
                        'expiresAt': row.get('expires_at'), 'collectionRecovery': False},
            'bootstrap': {'runId': bootstrap_run, 'source': receipt['recipeSha'], 'receipt': BOOTSTRAP_RECEIPT},
            'signingAccessExpiresAt': cfg['signingAccess']['expiresAt']}


def write(binding, out, env=None):
    env = os.environ if env is None else env
    with Path(out).open('x') as f:  # write-once
        json.dump(binding, f, indent=2, sort_keys=True)
        f.write('\n')
    if env.get('GITHUB_OUTPUT'):
        with open(env['GITHUB_OUTPUT'], 'a') as f:
            f.write(f'compile_source={binding["compile"]["source"]}\n')
            f.write('compile_recovery=false\n')
            f.write(f'bootstrap_source={binding["bootstrap"]["source"]}\n')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('action', choices=('bind',))
    p.add_argument('--out', required=True)
    a = p.parse_args(argv)
    try:
        binding = bind()
        write(binding, a.out)
    except (Refused, FileExistsError, KeyError, TypeError) as error:
        print(json.dumps({'refused': f'{type(error).__name__}: {str(error)[:480]}'}))
        return 2
    print(json.dumps(binding, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
