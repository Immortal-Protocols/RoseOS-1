#!/usr/bin/env python3
"""Dedicated Mustang controller adapter; the retired shared-host route stays retired.

This adapter requires the common controller's explicit-builder API. It refuses
the old implicit Frankel target before any control call. The committed runtime
stays unbound, so a normal start refuses before any cloud call. An
infrastructure host check may start the same VM only when MUSTANG_DRY_RUN is
true and publication was not requested. That path does not admit inputs.
"""
import argparse
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import sys

import mustang_lane

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'roseos.mustang-controller-ownership.v1'


def common_controller():
    spec = importlib.util.spec_from_file_location('mustang_common_controller', ROOT/'scripts/release-pipeline.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Production()


def require_explicit_target(adapter):
    # Do not replace the shared module's BUILDER global or pass a default target.
    parameter = inspect.signature(adapter.control_builder).parameters.get('builder')
    if parameter is None or parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
        raise ValueError('common controller explicit builder API unavailable; dedicated Mustang lifecycle blocked')
    return adapter


def invocation(lane, env):
    mustang_lane.validate(lane)
    workflow = 'Immortal-Protocols/RoseOS-1/'+lane['workflow']+'@refs/heads/main'
    values = {key:env.get(key, '') for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA')}
    if env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch' or env.get('GITHUB_REF') != 'refs/heads/main' \
            or env.get('GITHUB_WORKFLOW_REF') != workflow or values['GITHUB_RUN_ATTEMPT'] != '1' \
            or not re.fullmatch(r'[1-9][0-9]{0,19}', values['GITHUB_RUN_ID']) \
            or not re.fullmatch(r'[0-9a-f]{40}', values['GITHUB_SHA']):
        raise ValueError('dedicated Mustang controller requires its exact main workflow and a fresh attempt')
    return dict(workflow=workflow, source=values['GITHUB_SHA'], runId=values['GITHUB_RUN_ID'], runAttempt='1')


def read_ownership(path, lane, run):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('dedicated Mustang ownership absent; STOP refused')
    try:
        record = json.loads(path.read_text())
        if set(record) != {'schema', 'run', 'owner'} or record['schema'] != SCHEMA or record['run'] != run:
            raise ValueError
        return mustang_lane.owned_start(record['owner'], lane)
    except (TypeError, KeyError, ValueError):
        raise ValueError('dedicated Mustang ownership foreign or malformed; STOP refused') from None


def start(adapter, lane, run, record):
    """One common-controller call; retain ownership on failure/uncertain effects."""
    mustang_lane.validate(lane)
    require_explicit_target(adapter)
    record = Path(record)
    if os.path.lexists(record):
        raise ValueError('dedicated Mustang ownership already exists; reconcile before another start')
    claimed = False

    def claim(started):
        nonlocal claimed
        if claimed or type(started) is not bool:
            raise ValueError('dedicated Mustang controller ownership claim invalid')
        value = {'schema':SCHEMA, 'run':run,
                 'owner':dict(vm=dict(lane['vm']), startedByThisOperation=started)}
        # Exclusive create, no symlink following or record replacement. Complete
        # and flush before the common controller may submit the start request.
        fd = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, sort_keys=True)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        claimed = True

    observed = adapter.control_builder('start', builder=dict(lane['vm']), claim=claim)
    if not claimed:
        raise ValueError('common controller returned without an ownership claim; STOP remains refused')
    return {'observed':observed, 'startedByThisOperation':read_ownership(record, lane, run)}


def stop(adapter, lane, run, record):
    mustang_lane.validate(lane)
    started = read_ownership(record, lane, run)
    if not started:
        return {'skipped':'dedicated Mustang VM was not started by this operation'}
    require_explicit_target(adapter)
    return {'observed':adapter.control_builder('stop', builder=dict(lane['vm']))}


def start_gate(runtime, env):
    """Admit a real build, or an infrastructure host check that writes no evidence."""
    if env.get('MUSTANG_DRY_RUN') == 'true':
        if env.get('MUSTANG_PUBLISH') == 'true':
            raise ValueError('dry run cannot publish')
        return 'infrastructure-host-check'
    publication = runtime.get('publication') or {}
    if runtime.get('status') != 'bound' or not runtime.get('appInputsRecord') \
            or not runtime.get('signingRouteEvidence') or not runtime.get('sourceEvidence') \
            or (publication.get('enabled') is True and not publication.get('authorityRecord')):
        raise ValueError('dedicated Mustang runtime is not admitted; refusing to start the VM')
    return 'admitted'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'plan', 'start', 'ownership', 'stop'))
    parser.add_argument('--ownership-record')
    args = parser.parse_args()
    try:
        lane = mustang_lane.validate(mustang_lane.configuration())
        if args.action == 'plan':
            result = mustang_lane.controller_plan(lane)
        elif args.action == 'check':
            require_explicit_target(common_controller())
            result = {'status':'source-bound', 'runtimeAuthorized':False}
        else:
            if args.action == 'start':
                runtime = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
                start_gate(runtime, os.environ)
            run = invocation(lane, os.environ)
            if not args.ownership_record:
                raise ValueError('dedicated Mustang controller needs --ownership-record')
            if args.action == 'ownership':
                result = {'startedByThisOperation':read_ownership(args.ownership_record, lane, run)}
            else:
                adapter = require_explicit_target(common_controller())
                result = (start if args.action == 'start' else stop)(adapter, lane, run, args.ownership_record)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, TypeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 4


if __name__ == '__main__':
    sys.exit(main())
