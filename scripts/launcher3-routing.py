#!/usr/bin/env python3
"""Release's routing for the dedicated Launcher3 workflow (.github/workflows/launcher3-compile.yml).

  guard   the hosted guard's last step, after `launcher3-compile.py check`. It runs the harness owner's
          `launcher3_controller.py check` (the validated, Infra-evidenced binding of
          os/build-only/launcher3-compile-lane.json; no cloud operation) and admits it only for this workflow's own
          routing: the binding names this workflow, its queue is this workflow's concurrency group, and its controller
          variables are the ones the hosted controller and cleanup jobs read. It then prints the outputs the later
          jobs route on (GitHub output lines):
            runs_on          ["self-hosted","<the binding's runner label>"], the worker's only runner;
            runner_name      the binding's runner name, which the adapter checks again on the worker;
            binding_sha256   the canonical binding digest that the controller's ownership record binds.

The label is never written into the workflow: it exists only once Infra's verified readback is bound, and until
then nothing is routed. Exit 4 while the binding is unbound or invalid (the controller check's own refusal, passed
through), 2 when a bound binding names other routing than this workflow's. Nothing is started or stopped here.
"""
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = '.github/workflows/launcher3-compile.yml'
QUEUE = 'launcher3-compile'  # the workflow's concurrency group; never a release, Yogi or Agent queue
CONTROLLER = {'identityVariable': 'LAUNCHER3_CONTROLLER_SERVICE_ACCOUNT',
              'providerVariable': 'LAUNCHER3_CONTROLLER_WORKLOAD_IDENTITY_PROVIDER'}
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}')  # launcher3_lane's runner pattern
HEX64 = re.compile(r'[0-9a-f]{64}')


class Mismatch(ValueError):
    pass


def routing(lane):
    """The outputs for the controller check's JSON, or Mismatch when it names other routing than this workflow's."""
    if not isinstance(lane, dict) or lane.get('status') != 'source-bound' or lane.get('runtimeAuthorized') is not False:
        raise Mismatch('the controller check did not report a source-bound binding')
    if lane.get('workflow') != WORKFLOW:
        raise Mismatch(f'the binding names workflow {lane.get("workflow")!r}, not {WORKFLOW}')
    if lane.get('queue') != QUEUE:
        raise Mismatch(f'the binding queue {lane.get("queue")!r} is not this workflow\'s concurrency group {QUEUE}')
    if lane.get('controller') != CONTROLLER:
        raise Mismatch('the binding controller variables are not the ones the controller and cleanup jobs read: '
                       + json.dumps(CONTROLLER, sort_keys=True))
    label, name, digest = lane.get('runnerLabel'), lane.get('runnerName'), lane.get('bindingSha256')
    if not all(isinstance(v, str) and NAME.fullmatch(v) for v in (label, name)) or label.casefold() == 'self-hosted':
        raise Mismatch('the binding runner name or label is not one exact name')
    if not isinstance(digest, str) or not HEX64.fullmatch(digest):
        raise Mismatch('the controller check returned no binding digest')
    return {'runs_on': json.dumps(['self-hosted', label], separators=(',', ':')), 'runner_name': name,
            'binding_sha256': digest}


def guard(run=subprocess.run):
    result = run([sys.executable, str(ROOT/'scripts/launcher3_controller.py'), 'check'], capture_output=True, text=True)
    if result.returncode:
        print(result.stderr.strip()[-2000:], file=sys.stderr)
        return result.returncode
    try:
        outputs = routing(json.loads(result.stdout))
    except (Mismatch, ValueError) as error:
        print(json.dumps({'verdict': 'MISMATCH', 'error': str(error)}), file=sys.stderr)
        return 2
    print(''.join(f'{key}={value}\n' for key, value in outputs.items()), end='')
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ['guard']:
        print('usage: launcher3-routing.py guard', file=sys.stderr)
        return 2
    return guard()


if __name__ == '__main__':
    sys.exit(main())
