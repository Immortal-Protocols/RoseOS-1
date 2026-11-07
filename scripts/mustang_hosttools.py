#!/usr/bin/env python3
"""Bounded same-source host-tools contract for Mustang otatools-package.

The synced tree has no Pixel product makefiles. lunch mustang-cur-user needs
vendor/google_devices/mustang from adevtool generate-all, which needs the
factory image on the dedicated disk and the adevtool host-tool closure. Those
records are absent, so this module does not admit a compile and does not set
the production otatools pin.

It also does not start the VM. The builder is stopped. A later run that has
the missing proofs would use the existing controller START, enroll, and
cleanup STOP. Acknowledging that lifecycle is not a power-on.
"""
import json
from pathlib import Path
import re
import sys

import mustang_lane
import mustang_route as route

ROOT = Path(__file__).resolve().parents[1]
TARGET = 'otatools-package'
PRODUCT_GOALS = ('vendorbootimage', 'vendorkernelbootimage', 'target-files-package', 'droid', 'dist')
HELD_OTATOOLS = (
    '4e13f55b',
    'f8fc065e87083a51f64c74be57f4f06611721645273b9ae45081148203cebc22',
)
MAX_JOBS = 8
MAX_TIMEOUT = 7200
GENERATED_PREFIX = 'vendor/google_devices/mustang/'
OUT_NAME = re.compile(r'out-mustang-hosttools-[0-9]{8,}\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
LIFECYCLE = (
    'The dedicated VM is stopped. A later host-tools run has to START it through '
    'scripts/mustang_controller.py, enroll with the existing group-5 path, and STOP '
    'in the existing cleanup job. This module does not start, enroll, compile, or stop.'
)


def _add(problems, code):
    if code not in problems:
        problems.append(code)


def pins_null(root=ROOT):
    pin = json.loads((Path(root)/'os/build-only/mustang-hosted-sign-script.json').read_text())
    return all(pin.get(name) is None for name in ('generateReleaseSha256', 'jdkSha256', 'otatoolsSha256', 'otatoolsExecutable'))


def deps_required(root=ROOT):
    recipe = json.loads((Path(root)/'os/build-only/mustang.json').read_text())
    return tuple(recipe['adevtool']['depsTools'])


def current_observation(root=ROOT):
    """What the committed records actually show. Missing proofs stay missing."""
    factory = json.loads((Path(root)/'os/build-only/mustang-factory-box-20261107.json').read_text())
    identity = route.ACCEPTED_SOURCE_SYNC
    return {
        'instanceId': identity['instanceId'],
        'diskUuid': identity['diskUuid'],
        'sourceRoot': identity['sourceRoot'],
        'roseRevision': identity['roseRevision'],
        'manifestSha256': identity['manifestSha256'],
        'projectCount': identity['projectCount'],
        'symlinks': [],
        'outDir': None,
        'outExists': False,
        'outEmpty': True,
        'priorProductOutputs': False,
        'targets': [TARGET],
        'jobs': 4,
        'timeoutSeconds': 3600,
        'factoryOnDedicatedDisk': factory.get('transferred') is True and factory.get('location') != 'box-only',
        'factoryExtracted': factory.get('extracted') is True,
        'factorySha256': factory.get('sha256'),
        'factoryPath': factory.get('path'),
        'vendorGenerated': factory.get('vendorGenerated') is True,
        'generated': [],
        'dependencies': [],
        'startAcknowledged': False,
        'otatoolsSha256': None,
        'integration': None,
    }


def _held(value):
    if not isinstance(value, str):
        return False
    return any(value == held or value.startswith(held) for held in HELD_OTATOOLS)


def _generated_ok(rows):
    if not isinstance(rows, list) or not rows:
        return False
    for row in rows:
        path = row.get('path') if isinstance(row, dict) else None
        digest = row.get('sha256') if isinstance(row, dict) else None
        if not isinstance(path, str) or path.startswith('/') or '..' in Path(path).parts \
                or not path.startswith(GENERATED_PREFIX) or not SHA.fullmatch(digest or ''):
            return False
    return True


def _deps_ok(rows, root):
    if not isinstance(rows, list):
        return False
    found = {}
    for row in rows:
        if not isinstance(row, dict) or not SHA.fullmatch(row.get('sha256') or ''):
            return False
        found[row.get('name')] = row['sha256']
    return all(name in found for name in deps_required(root))


def problems_for(obs, root=ROOT):
    problems = []
    identity = route.ACCEPTED_SOURCE_SYNC
    record = json.loads((Path(root)/identity['path']).read_text())
    if not route.accepted_source_sync(record, identity['path'], root):
        _add(problems, 'source-receipt-unbound')
    if obs.get('instanceId') != identity['instanceId']:
        _add(problems, 'wrong-vm')
    if obs.get('diskUuid') != identity['diskUuid']:
        _add(problems, 'wrong-disk')
    if obs.get('sourceRoot') != identity['sourceRoot']:
        _add(problems, 'wrong-tree')
    if obs.get('roseRevision') != identity['roseRevision'] or obs.get('manifestSha256') != identity['manifestSha256'] \
            or obs.get('projectCount') != identity['projectCount']:
        _add(problems, 'source-changed')
    links = obs.get('symlinks') or []
    if not isinstance(links, list) or any(name in links for name in ('source', 'manifest', 'out', 'factory')):
        _add(problems, 'symlink')
    out = obs.get('outDir')
    if not isinstance(out, str) or not OUT_NAME.fullmatch(out) or '/' in out or out.startswith('out-mustang-build-'):
        _add(problems, 'out-dir-unspecified')
    elif obs.get('outExists') is not False or obs.get('outEmpty') is not True or obs.get('priorProductOutputs') is not False:
        _add(problems, 'reused-out-dir')
    targets = obs.get('targets')
    if targets != [TARGET] or any(goal in (obs.get('argv') or []) for goal in PRODUCT_GOALS):
        _add(problems, 'untrusted-target')
    argv = ' '.join(obs.get('argv') or [])
    if any(goal in argv for goal in PRODUCT_GOALS) or any(word in argv for word in (' sign', 'publish', 'secret', 'fastboot', '/keys')):
        _add(problems, 'forbidden-path')
    jobs, timeout = obs.get('jobs'), obs.get('timeoutSeconds')
    if type(jobs) is not int or type(timeout) is not int or not 1 <= jobs <= MAX_JOBS \
            or not 1 <= timeout <= MAX_TIMEOUT or timeout >= mustang_lane.CONFIRMED_STOP['seconds']:
        _add(problems, 'bounds')
    if obs.get('factoryOnDedicatedDisk') is not True or str(obs.get('factoryPath') or '').startswith('/workspace/'):
        _add(problems, 'factory-not-on-dedicated-disk')
    if obs.get('factoryExtracted') is not True:
        _add(problems, 'factory-not-extracted')
    recipe = json.loads((Path(root)/'os/build-only/mustang.json').read_text())
    if obs.get('factorySha256') != recipe['factory']['sha256']:
        _add(problems, 'factory-hash-differs')
    if obs.get('vendorGenerated') is not True or not _generated_ok(obs.get('generated')):
        _add(problems, 'vendor-not-generated')
        _add(problems, 'mustang-product-config-absent')
    if not _deps_ok(obs.get('dependencies'), root):
        _add(problems, 'host-dependency-closure-absent')
    integration = obs.get('integration')
    if integration not in (None, {'deliberate': False}):
        if not isinstance(integration, dict) or integration.get('deliberate') is not True \
                or not isinstance(integration.get('paths'), list):
            _add(problems, 'unrecorded-mutation')
    if _held(obs.get('otatoolsSha256')):
        _add(problems, 'held-otatools-substitute')
    if obs.get('requireExistingOtatoolsPin') is True or (obs.get('otatoolsSha256') not in (None,) and not _held(obs.get('otatoolsSha256'))):
        _add(problems, 'circular-pin')
    if not pins_null(root):
        _add(problems, 'production-pin-changed')
    proof_gaps = [code for code in problems if code in {
        'factory-not-on-dedicated-disk', 'factory-not-extracted', 'factory-hash-differs',
        'vendor-not-generated', 'mustang-product-config-absent', 'host-dependency-closure-absent',
        'source-receipt-unbound', 'source-changed', 'wrong-vm', 'wrong-disk', 'wrong-tree',
        'symlink', 'out-dir-unspecified', 'reused-out-dir', 'untrusted-target', 'forbidden-path',
        'bounds', 'unrecorded-mutation', 'held-otatools-substitute', 'circular-pin', 'production-pin-changed'}]
    if not proof_gaps and obs.get('startAcknowledged') is not True:
        _add(problems, 'start-not-acknowledged')
    return problems


def allowlisted_argv(obs):
    return {
        'cwd': obs['sourceRoot'],
        'env': {'OUT_DIR': obs['outDir'], 'PATH': '/usr/bin:/bin'},
        'argv': ['bash', '-c', 'source build/envsetup.sh && lunch mustang-cur-user && m -j%d %s' % (obs['jobs'], TARGET)],
        'timeoutSeconds': obs['timeoutSeconds'],
        'mutation': 'create only this empty OUT_DIR',
        'cleanup': 'delete only this OUT_DIR; the source tree stays; VM STOP stays on the existing cleanup job',
    }


def decide(obs, root=ROOT):
    found = problems_for(obs, root)
    ready = not found
    return {
        'schema': 'roseos.mustang-hosttools-contract.v1',
        'target': TARGET,
        'execute': False,
        'startAuthorized': False,
        'readyForControlledStart': ready,
        'problems': found,
        'argv': allowlisted_argv(obs) if ready else None,
        'lifecycle': LIFECYCLE,
        'productionPins': 'null',
        'heldOtatools': HELD_OTATOOLS[0],
    }


def main(argv=None):
    parser_out = decide(current_observation())
    json.dump(parser_out, sys.stdout, sort_keys=True)
    sys.stdout.write('\n')
    return 4


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
