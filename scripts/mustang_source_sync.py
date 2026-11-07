#!/usr/bin/env python3
"""Reviewed Mustang source-sync recipe. This does not run repo or bind the runtime."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT/'os/build-only/mustang-source-sync-recipe-20261107.json'
REVISION = '2ac24c5475de121ebe4e9e6a5c1a404ef44aa9c8'
SOURCE = '/mnt/disks/mustang-data/roseos/source'
FORBIDDEN = ('immortal-os-builder', '/home/pixelbuild', 'yogi', 'frankel', 'ota_from_target_files', ' m ')


def _load(path):
    return json.loads(Path(path).read_text())


def validate(record, root=ROOT):
    root = Path(root)
    if not isinstance(record, dict) or record.get('schema') != 'roseos.mustang-source-sync-recipe.v1':
        raise ValueError('source sync recipe schema differs')
    requirement = _load(root/'os/build-only/mustang-source-requirement-20261107.json')
    factory = _load(root/'os/build-only/mustang-factory-box-20261107.json')
    manifest = hashlib.sha256((root/'os/pinned-manifest.xml').read_bytes()).hexdigest()
    if record.get('roseRevision') != REVISION or requirement.get('roseRevision') != REVISION \
            or requirement.get('upstream', {}).get('roseRevision') != REVISION:
        raise ValueError('source sync revision differs from the pinned RoseOS commit')
    if record.get('manifestSha256') != manifest or requirement.get('upstream', {}).get('defaultXmlSha256') != manifest:
        raise ValueError('source sync manifest hash differs')
    if record.get('adevtool') != requirement.get('upstream', {}).get('adevtool'):
        raise ValueError('source sync adevtool revision differs')
    boxed = record.get('factory') or {}
    if factory.get('match') is not True or boxed.get('sha256') != factory.get('sha256') \
            or boxed.get('sizeBytes') != factory.get('sizeBytes') or boxed.get('extracted') is not False \
            or boxed.get('vendorGenerated') is not False or boxed.get('copiedToDedicatedDisk') is not False \
            or boxed.get('location') != 'box-only':
        raise ValueError('source sync factory input differs from the verified box object')
    if record.get('sourceRoot') != SOURCE or not SOURCE.startswith('/mnt/disks/mustang-data/'):
        raise ValueError('source checkout must stay on the Mustang data disk')
    host = record.get('checkoutHost') or {}
    if host.get('instance') != 'roseos-mustang-builder-20261107-a' \
            or host.get('instanceId') != '5585425566393384049' \
            or host.get('diskUuid') != '4183bff8-6be4-46bb-a641-2193d19813ee':
        raise ValueError('source checkout host differs from the dedicated Mustang VM')
    if any(name not in record.get('forbiddenHosts', []) for name in ('immortal-os-builder', 'frankel', 'yogi')):
        raise ValueError('source sync must keep Frankel and Yogi out of the checkout')
    for flag in ('compileAuthorized', 'signAuthorized', 'publishAuthorized', 'vendorGenerateAuthorized',
                 'allocateAuthorized', 'runtimeBound', 'executed'):
        if record.get(flag) is not False:
            raise ValueError('source sync cannot authorize compile, signing, or publication')
    commands = record.get('commands')
    if not isinstance(commands, list) or not commands:
        raise ValueError('source sync commands are missing')
    text = '\n'.join(commands)
    if any(token in text for token in FORBIDDEN) or 'repo sync -c -j4 --fail-fast' not in commands:
        raise ValueError('source sync commands leave the checkout')
    if f'ROSE_REVISION={REVISION} repo init -u https://github.com/Immortal-Protocols/RoseOS-1.git' not in text:
        raise ValueError('source sync init does not pin the reviewed revision')
    if record.get('syncAuthorized') not in (False, True):
        raise ValueError('source sync authorization flag differs')
    return record


def load(root=ROOT):
    record = validate(_load(Path(root)/'os/build-only/mustang-source-sync-recipe-20261107.json'), root)
    if record.get('syncAuthorized') is not False:
        raise ValueError('committed source sync recipe is not authorized')
    return record


def execute(record):
    """Return the reviewed commands. This never starts repo."""
    checked = validate(record)
    if checked.get('syncAuthorized') is not True:
        raise ValueError('source sync is not authorized')
    return {'executed': False, 'commands': list(checked['commands']), 'sourceRoot': SOURCE}


def main():
    record = load()
    print(json.dumps({'syncAuthorized': record['syncAuthorized'], 'executed': False,
                      'roseRevision': record['roseRevision'], 'sourceRoot': record['sourceRoot']}, sort_keys=True))
    return 4


if __name__ == '__main__':
    raise SystemExit(main())
