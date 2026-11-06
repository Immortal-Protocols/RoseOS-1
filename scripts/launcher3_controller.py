"""Dedicated Launcher3 lifecycle adapter for Infra's existing explicit-builder API.

No default target or mutation of the common BUILDER global. The caller persists
run/binding/VM ownership before a start request and refuses every foreign STOP.
The checked-in binding is unbound. Source checks do not authorize runtime.
"""
import argparse
import importlib.util
import inspect
import json
import os
from pathlib import Path
import stat
import sys

import launcher3_lane as lane

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'roseos.launcher3-controller-ownership.v1'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require_explicit_target(adapter):
    parameters = inspect.signature(adapter.control_builder).parameters
    if 'builder' not in parameters or parameters['builder'].kind != inspect.Parameter.KEYWORD_ONLY \
            or 'claim' not in parameters:
        raise lane.Invalid('common controller explicit-builder/ownership API unavailable; no fallback')
    return adapter


def ownership(binding, run, started):
    return {'schema': SCHEMA, 'run': run, 'bindingSha256': lane.digest(binding),
            'vm': dict(binding['lane']['vm']), 'startedByThisOperation': started}


def read_ownership(path, binding, run):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
            raise lane.Invalid('ownership must be a bounded regular file with one link; STOP refused')
        record = lane.strict_json(source.read(65537))
    if not isinstance(record, dict) or type(record.get('startedByThisOperation')) is not bool \
            or record != ownership(binding, run, record['startedByThisOperation']):
        raise lane.Invalid('ownership absent, changed or foreign to this run/binding/VM; STOP refused')
    return record['startedByThisOperation']


def start(adapter, binding, env, path):
    lane.validate(binding)
    run = lane.invocation(env)
    require_explicit_target(adapter)
    path = Path(path)
    if os.path.lexists(path):
        raise lane.Invalid('ownership already exists; reconcile the previous start before another attempt')
    claimed = False

    def claim(started):
        nonlocal claimed
        if claimed or type(started) is not bool:
            raise lane.Invalid('invalid or repeated ownership claim')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(ownership(binding, run, started), output, sort_keys=True)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        claimed = True

    observed = adapter.control_builder('start', builder=dict(binding['lane']['vm']), claim=claim)
    if not claimed:
        raise lane.Invalid('common controller returned without a durable ownership claim')
    return {'observed': observed, 'startedByThisOperation': read_ownership(path, binding, run)}


def stop(adapter, binding, env, path):
    lane.validate(binding)
    run = lane.invocation(env)
    if not read_ownership(path, binding, run):
        return {'skipped': 'this operation did not start the dedicated VM'}
    require_explicit_target(adapter)
    return {'observed': adapter.control_builder('stop', builder=dict(binding['lane']['vm']))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'start', 'ownership', 'stop'))
    parser.add_argument('--ownership-record')
    parser.add_argument('--binding', default=str(lane.CONFIG), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        lane.check_context(os.environ)
        binding = lane.load(args.binding)
        lane.validate(binding)
        # Starting additionally requires the compile input contract and source bindings.
        # STOP must remain possible even if an unrelated compile prerequisite has drifted.
        if args.action in ('check', 'start'):
            compile_adapter = load_module('launcher3_compile_controller_check', 'scripts/launcher3-compile.py')
            try:
                compile_adapter.check(args.binding)
            except (compile_adapter.Invalid, compile_adapter.Unbound, compile_adapter.Refused) as error:
                raise lane.Invalid(str(error)) from None
        if args.action != 'check' and not args.ownership_record:
            raise lane.Invalid('dedicated lifecycle needs --ownership-record')
        if args.action == 'ownership':
            result = {'startedByThisOperation': read_ownership(args.ownership_record, binding,
                                                              lane.invocation(os.environ))}
        else:
            common = load_module('launcher3_common_controller', 'scripts/release-pipeline.py')
            adapter = require_explicit_target(common.Production())
            if args.action == 'check':
                result = {'status': 'source-bound', 'runtimeAuthorized': False, 'bindingSha256': lane.digest(binding),
                          **{key: binding['lane'][key] for key in ('workflow', 'runnerName', 'runnerLabel', 'queue',
                                                                  'vm', 'controller')}}
            else:
                try:
                    result = (start if args.action == 'start' else stop)(adapter, binding, os.environ, args.ownership_record)
                except (common.InvalidInput, common.CheckFailed, common.SetupMissing) as error:
                    raise lane.Invalid(str(error)) from None
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, KeyError, TypeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 4


if __name__ == '__main__':
    sys.exit(main())
