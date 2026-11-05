#!/usr/bin/env python3
"""Source/binding readiness only; no VM, native build, credentials or host probes."""
import json
import os
import sys

import mustang_controller
import mustang_lane
import mustang_paths


def check(lane, env):
    run = mustang_controller.invocation(lane, env)
    if lane['workflow'] != '.github/workflows/mustang-ota-dedicated.yml' or lane['queue'] != 'mustang-ota':
        raise ValueError('dedicated Mustang workflow/queue differ from this source route')
    paths = mustang_paths.layout(lane)
    mustang_controller.require_explicit_target(mustang_controller.common_controller())
    return {'status':'source-binding-check-passed', 'run':run,
            'paths':{key:str(value) for key,value in paths.items()},
            'nativePipelineReady':False, 'runtimeAuthorized':False,
            'remaining':['approved dedicated resources/runner/network and readback',
                         'fresh source and app/baseline/allocation evidence',
                         'complete dedicated build/sign/package workflow integration']}


if __name__ == '__main__':
    try:
        print(json.dumps(check(mustang_lane.configuration(), os.environ), sort_keys=True))
    except (OSError, TypeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(4)
