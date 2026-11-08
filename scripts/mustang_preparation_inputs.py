"""Read actual dedicated-disk preparation inputs against committed run evidence.

No guest identity probe, connection, mutation or executable invocation exists
here. The calling dedicated job must verify its guest and hold the canonical
lock. These readers do not admit OTA compilation or set production pins.
"""
import hashlib
import json
from pathlib import Path
import re
import time

import mustang_factory_input as factory
import mustang_hostdeps as deps

ROOT = Path(__file__).resolve().parents[1]


def evidence(relative, schema, root=ROOT):
    parts = str(relative).split('/')
    if any(p in ('', '.', '..') for p in parts) or parts[:2] != ['os', 'build-only'] or len(parts) < 3:
        raise ValueError('preparation evidence must be a committed build-only record')
    path = Path(*parts)
    record = json.loads(deps.readers.read_regular(str(root), str(Path(root)/path), 12 << 20))
    if record.get('schema') != schema or record.get('workflowConclusion') != 'success' \
            or record.get('cleanupConclusion') != 'success' \
            or record.get('stopObservation', {}).get('status') != 'TERMINATED' \
            or not re.fullmatch(r'[0-9a-f]{64}', record.get('receiptSha256', '')):
        raise ValueError('preparation evidence does not describe a completed successful run')
    return record


def receipt_on_disk(record, storage, kind, filename, identity):
    expected = record['receipt']; observed = expected['identity'];run = observed['run']
    if observed['builder'] != identity['builder'] or observed['diskUuid'] != identity['diskUuid'] \
            or record['stopObservation'].get('instanceId') != identity['builder']['instanceId'] \
            or not re.fullmatch(r'[1-9][0-9]{0,19}', run.get('runId', '')) \
            or run.get('runAttempt') != '1' or not re.fullmatch(r'[0-9a-f]{40}', run.get('source', '')) \
            or run.get('runId') != record.get('runId') or run.get('source') != record.get('source'):
        raise ValueError('preparation receipt belongs to another builder, disk or recorded run')
    path = storage/('mustang-'+kind+'-'+run['runId']+'-'+run['runAttempt'])/filename
    raw = deps.readers.read_regular(str(storage), str(path), 12 << 20)
    if hashlib.sha256(raw).hexdigest() != record['receiptSha256'] or json.loads(raw) != expected:
        raise ValueError('preparation receipt differs from the uploaded run evidence')
    if expected.get('success') is not True:
        raise ValueError('failed preparation is not an input')
    return expected


def host_dependencies(source, storage, record, identity, *, deadline=None, root=ROOT):
    """Rehash the entire actual host distribution; a marker alone is insufficient."""
    deadline = deadline if deadline is not None else time.monotonic()+900
    previous = receipt_on_disk(record, storage, 'hostdeps', 'hostdeps-receipt.json', identity)
    spec = deps.recipe(root)
    output = source/spec['outDir']
    if previous.get('schema') != 'roseos.mustang-hostdeps-run.v1' \
            or type(previous.get('makeExitCode')) is not int or previous.get('makeExitCode') != 0 \
            or previous.get('recipe') != spec \
            or previous.get('sourceRoot') != str(source) or previous.get('outDir') != str(output) \
            or previous.get('manifestSha256') != hashlib.sha256((Path(root)/'os/pinned-manifest.xml').read_bytes()).hexdigest() \
            or previous.get('source', {}).get('projectCount') != 1057 \
            or previous['source'].get('check') != 'project-heads-and-git-status':
        raise ValueError('host-dependency recipe, manifest or successful Make proof differs')
    measured=verify_host_distribution(source,previous,deadline=deadline,root=root)
    return dict(measured,receiptSha256=record['receiptSha256'],run=previous['identity']['run'])


def verify_host_distribution(source,previous,*,deadline=None,root=ROOT):
    """Rehash the original distribution or its isolated byte copy, without executing it."""
    deadline=deadline if deadline is not None else time.monotonic()+900
    spec=deps.recipe(root);output=source/spec['outDir']
    marker_path = output/'adevtool_revision'
    marker = deps.readers.read_regular(str(source), str(marker_path), 128)
    if marker != (spec['adevtoolRevision']+'\n').encode() \
            or deps.fingerprint(source, marker_path, deadline) != previous.get('revisionMarker'):
        raise ValueError('host-dependency revision marker differs')
    distribution = deps.inventory(source, output, deadline)
    if not distribution or distribution != previous.get('distribution'):
        raise ValueError('host-dependency distribution differs from its successful build')
    for name in spec['tools']:
        item = distribution.get('host/linux-x86/bin/'+name, {})
        if not item.get('sizeBytes') or not item.get('mode', 0) & 0o111:
            raise ValueError('host-dependency executable entrypoint is absent')
    return {'distributionEntries': len(distribution), 'distributionRehashed': True,
            'adevtoolRevision': spec['adevtoolRevision'], 'otaAdmitted': False}


def factory_archive(storage, record, identity, *, root=ROOT):
    """Rehash the exact actual official stock archive before vendor generation."""
    previous = receipt_on_disk(record, storage, 'factory', factory.RECEIPT, identity)
    pin = deps.readers.recipe(root)['factory']
    path = storage/factory.DIRECTORY/pin['file']
    downloaded = previous.get('download', {})
    if previous.get('schema') != 'roseos.mustang-factory-input.v1' \
            or downloaded.get('verified') is not True or downloaded.get('path') != str(path) \
            or downloaded.get('url') != pin['url'] or downloaded.get('sha256') != pin['sha256'] \
            or downloaded.get('downloadedBytes') != pin['sizeBytes'] \
            or previous.get('readback', {}).get('matchesPinnedFactory') is not True:
        raise ValueError('factory input does not bind the pinned official archive')
    measured = deps.readers.measure_factory(str(storage), str(path), root)
    if measured['matchesPinnedFactory'] is not True:
        raise ValueError('actual factory input differs from its verified download')
    return {'path': str(path), 'sha256': pin['sha256'], 'sizeBytes': pin['sizeBytes'],
            'receiptSha256': record['receiptSha256'], 'archiveRehashed': True,
            'run': previous['identity']['run'], 'otaAdmitted': False}
