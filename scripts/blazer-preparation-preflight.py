#!/usr/bin/env python3
"""Blazer preparation admission report over explicit structured observations (OCT29-INFRA-S004).

This module decides whether a later, separately authorized `adevtool` preparation step for exactly one device, Blazer,
would be admissible. It is not a generator and it does not observe anything itself: it reads one JSON observation
document plus the checked-in contract (os/devices/pixel10.json), the pinned manifest (os/pinned-manifest.xml) and the
Frankel release pipeline (source root). It starts no process, opens no network connection and never probes a live
tree. Every fact it cannot see is UNKNOWN and every UNKNOWN blocks.

The verdict is BLOCKED with one reason per failing rule unless every rule passes. A document marked synthetic can at
most reach ADMISSIBLE_SYNTHETIC: a fixture is never a real host pass. Admission never selects a download, generation,
lunch or build; it only states that the bound observations satisfy the preconditions.

Rules (all must pass):
- device is `blazer` and the intended operation names `-d blazer` explicitly; an all-device default is refused;
- one observation id, recipe revision and source root bind every section; an observation older than the age limit,
  dated in the future, or bound to another recipe or source root is refused; original times and hashes are echoed;
- vendor/adevtool and vendor/state pins, and the adevtool dependency revision file, equal the contract;
- the Blazer factory archive is present and its sha256 equals the contract value (absence means no download was
  selected, which blocks);
- every collision path (generated vendor destination, archive temp, unpack directory, unpack final temp) is observed
  absent; `unknown` blocks;
- all 14 fixed host tools are present and executable, and usability is separately demonstrated; executable metadata
  alone is not usability;
- Node location and version are known and the version meets adevtool's floor;
- the raw .repo/manifest.xml bytes match the pinned manifest, or a separate resolved-manifest observation shows the
  wrapper with exactly one include whose bytes equal the pinned manifest and the required project revisions. This is
  a byte-level comparison, never a claim of host semantic equivalence; repo and its hooks are never run;
- space uses availableBytes on the filesystem holding the source root (never freeBytes or total size); a complete,
  bounded, Blazer-specific transient plus collision estimate must leave at least 322122547200 bytes. Frankel physical
  usage is proxy evidence only, and a provider disk size is not filesystem or free-space evidence.

The full-build allocation is reported separately as unknown and is not part of this admission.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = 'os/devices/pixel10.json'
MANIFEST_PATH = 'os/pinned-manifest.xml'
PIPELINE_PATH = 'os/release-pipeline/frankel.json'
SCHEMA = 'roseos.blazer-preparation-observations.v1'
REPORT_SCHEMA = 'roseos.blazer-preparation-report.v1'
DEVICE = 'blazer'
RESERVE_FLOOR_BYTES = 322122547200
MAX_OBSERVATION_AGE_SECONDS = 3600
NODE_MIN_MAJOR = 24
PIN_PATHS = ('vendor/adevtool', 'vendor/state')
HOST_TOOLS = ('aapt2', 'apksigner', 'aprotoc', 'arsclib', 'debugfs', 'dispol', 'fsck.erofs', 'lpdump', 'lpunpack', 'lz4',
    'ota_extractor', 'simg2img', 'sqlite3', 'toybox')
COLLISION_PATHS = ('vendorDestination', 'archiveTmp', 'unpack', 'unpackFinalTemp')
SECTIONS = ('pins', 'factory', 'adevtoolDeps', 'node', 'paths', 'manifest', 'space')
SHA1 = re.compile(r'[0-9a-f]{40}\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
MANIFEST_NEXT_OBSERVATION = ('no-follow read of .repo/manifest.xml recording each element; for each <include>, the '
    'sha256 of the no-follow bytes of .repo/manifests/<name>; repo and its hooks are not run')


class InvalidInput(ValueError):
    pass


def load_inputs(root=ROOT):
    """Read the checked-in contract, pinned manifest bytes and source root; nothing on a host is read."""
    contract = (root/CONTRACT_PATH).read_bytes()
    manifest = (root/MANIFEST_PATH).read_bytes()
    pipeline = json.loads((root/PIPELINE_PATH).read_text())
    return {'contract': json.loads(contract), 'contractSha256': hashlib.sha256(contract).hexdigest(),
        'manifestBytes': manifest, 'sourceRoot': pipeline['host']['sourceRoot']}


def manifest_projects(data):
    """Map project path to resolved revision from pinned manifest bytes (project, then remote, then default)."""
    tree = ET.fromstring(data)
    default = tree.find('default')
    default = {} if default is None else default.attrib
    remotes = {r.get('name'): r.attrib for r in tree.findall('remote')}
    projects = {}
    for p in tree.findall('project'):
        remote = p.get('remote', default.get('remote'))
        revision = p.get('revision') or remotes.get(remote, {}).get('revision') or default.get('revision')
        projects[p.get('path', p.get('name'))] = {'name': p.get('name'), 'remote': remote, 'revision': revision}
    return projects


def parse_time(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value):
        raise InvalidInput(f'time must be UTC YYYY-MM-DDTHH:MM:SSZ: {value!r}')
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def assess(obs, inputs, now):
    """Return the admission report for one observation document. Pure: no I/O."""
    if not isinstance(obs, dict) or obs.get('schema') != SCHEMA:
        raise InvalidInput(f'observation schema must be {SCHEMA}')
    contract = inputs['contract']
    target = contract['devices'].get(DEVICE, {})
    pins = contract['source']['projects']
    pinned_bytes = inputs['manifestBytes']
    pinned_sha = hashlib.sha256(pinned_bytes).hexdigest()
    pinned_projects = manifest_projects(pinned_bytes)
    assessed = parse_time(now)
    reasons, unknowns, notes = [], [], []

    def block(code, detail, unknown=False):
        reasons.append({'code': code, 'detail': detail})
        if unknown:
            unknowns.append(code)

    def section(name):
        value = obs.get(name)
        return value if isinstance(value, dict) else {}

    # Device and operation.
    if obs.get('device') is None:
        block('device-missing', 'no device named; preparation is admitted for blazer only', True)
    elif obs.get('device') != DEVICE:
        block('device-wrong', f'device {obs.get("device")!r} is not blazer')
    op = section('operation')
    args = op.get('deviceArgs')
    if op.get('allDevices') is not False:
        block('operation-all-devices', 'the all-device default is refused; allDevices must be explicitly false')
    if args != [DEVICE]:
        block('operation-device-arg', f'intended operation must pass exactly -d blazer; observed {args!r}')

    # Source binding.
    source = section('source')
    oid = source.get('observationId')
    observed_at = None
    if not oid:
        block('binding-missing', 'no observation id binds the sections', True)
    for name in SECTIONS:
        if name in obs and section(name).get('observationId') != oid:
            block('binding-inconsistent', f'section {name} is bound to {section(name).get("observationId")!r}, not {oid!r}')
    if not (isinstance(source.get('recipeSha'), str) and SHA1.match(source['recipeSha'])):
        block('binding-recipe', 'observed recipe revision is missing or not a full commit id', True)
    elif source['recipeSha'] != op.get('recipeSha'):
        block('binding-recipe', f'observations come from recipe {source["recipeSha"]}, operation names {op.get("recipeSha")!r}')
    if source.get('sourceRoot') != inputs['sourceRoot'] or op.get('sourceRoot') != inputs['sourceRoot']:
        block('binding-source-root', f'source root must be {inputs["sourceRoot"]} for both observations and operation')
    hashes = source.get('hashes') if isinstance(source.get('hashes'), dict) else {}
    if hashes.get('contractSha256') != inputs['contractSha256']:
        block('binding-contract', f'observations name contract {hashes.get("contractSha256")!r}, checked-in '
            f'{CONTRACT_PATH} is {inputs["contractSha256"]}')
    try:
        observed_at = parse_time(source.get('observedAt'))
    except InvalidInput:
        block('binding-time', 'observation time is missing or malformed', True)
    if observed_at is not None:
        age = (assessed - observed_at).total_seconds()
        if age < 0:
            block('binding-future', f'observation time {source["observedAt"]} is after assessment time {now}')
        elif age > MAX_OBSERVATION_AGE_SECONDS:
            block('binding-stale', f'observation is {int(age)} s old; limit {MAX_OBSERVATION_AGE_SECONDS} s')

    # Pins and dependency revision.
    observed_pins = section('pins')
    for path in PIN_PATHS:
        value = observed_pins.get(path)
        if value is None:
            block('pin-unknown', f'{path} revision not observed', True)
        elif value != pins[path] or pinned_projects.get(path, {}).get('revision') != pins[path]:
            block('pin-mismatch', f'{path} observed {value}, contract {pins[path]}')
    deps = section('adevtoolDeps')
    if deps.get('revision') is None:
        block('deps-revision-unknown', 'adevtool host dependency revision file not observed', True)
    elif deps.get('revision') != pins['vendor/adevtool']:
        block('deps-revision-mismatch', f'dependency revision {deps.get("revision")} is not {pins["vendor/adevtool"]}')

    # Factory archive.
    factory = section('factory')
    expected = target.get('vendorInputs', {}).get('factoryImage', {})
    if factory.get('status') != 'present':
        block('factory-absent', f'factory archive {expected.get("file")} is {factory.get("status", "unknown")}; no download is '
            'selected or performed here', factory.get('status') in (None, 'unknown'))
    elif factory.get('file') != expected.get('file') or factory.get('sha256') != expected.get('sha256'):
        block('factory-digest-mismatch', f'factory {factory.get("file")} sha256 {factory.get("sha256")} is not the contract '
            f'{expected.get("file")} {expected.get("sha256")}')

    # Collision paths.
    paths = section('paths')
    for name in COLLISION_PATHS:
        state = paths.get(name, {}).get('status', 'unknown') if isinstance(paths.get(name), dict) else 'unknown'
        if state == 'unknown':
            block('collision-unknown', f'{name} was not observed absent', True)
        elif state != 'absent':
            block('collision-occupied', f'{name} is {state}; nothing is deleted or reused')

    # Tools: metadata versus demonstrated usability.
    tools = deps.get('tools') if isinstance(deps.get('tools'), dict) else {}
    extra = sorted(set(tools) - set(HOST_TOOLS))
    if extra:
        block('tools-unexpected', f'unexpected tool observations {extra}')
    metadata_ok, demonstrated = [], []
    for name in HOST_TOOLS:
        tool = tools.get(name)
        if not isinstance(tool, dict):
            block('tool-unknown', f'{name} not observed', True)
            continue
        if tool.get('status') != 'present' or tool.get('executable') is not True:
            block('tool-not-executable', f'{name} is {tool.get("status")} executable={tool.get("executable")}')
            continue
        metadata_ok.append(name)
        if tool.get('usability') == 'demonstrated':
            demonstrated.append(name)
        else:
            block('tool-usability-unknown', f'{name} is present and executable but usability is not demonstrated', True)

    # Node.
    node = section('node')
    version = node.get('version')
    if node.get('location') in (None, 'unknown') or version in (None, 'unknown'):
        block('node-unknown', 'Node executable location and version are unknown', True)
    else:
        match = re.fullmatch(r'v?(\d+)\.\d+\.\d+', str(version))
        if not match or int(match[1]) < NODE_MIN_MAJOR:
            block('node-version', f'Node {version} does not meet >= {NODE_MIN_MAJOR}')

    # Manifest: raw comparison, then the separate resolved-manifest interface.
    manifest = section('manifest')
    resolved = obs.get('resolvedManifest')
    raw = manifest.get('comparison', 'unavailable')
    manifest_result = {'path': '.repo/manifest.xml', 'observedSha256': manifest.get('sha256'),
        'pinnedSha256': manifest.get('pinnedSha256'), 'checkedInPinnedSha256': pinned_sha, 'comparison': raw,
        'resolved': 'not-supplied'}
    if manifest.get('pinnedSha256') not in (None, pinned_sha):
        block('manifest-pin-stale', f'observation compared against {manifest.get("pinnedSha256")}, checked-in pin is {pinned_sha}')
    elif raw == 'match' and manifest.get('sha256') == pinned_sha:
        manifest_result['resolved'] = 'not-needed'
    elif raw not in ('match', 'mismatch') or not manifest.get('sha256'):
        block('manifest-unavailable', f'raw manifest comparison unavailable; next: {MANIFEST_NEXT_OBSERVATION}', True)
    elif resolved is None:
        block('manifest-mismatch', f'raw manifest {manifest.get("sha256")} differs from pinned {pinned_sha} and is not '
            f'accepted as a wrapper; next: {MANIFEST_NEXT_OBSERVATION}', True)
    else:
        problems = resolved_problems(resolved, manifest, oid, pinned_sha, pinned_projects, pins)
        manifest_result['resolved'] = 'rejected' if problems else 'byte-identical-include'
        for problem in problems:
            block('manifest-resolved', problem)
        if not problems:
            notes.append('resolved manifest: one include byte-identical to the pinned manifest; this is a byte comparison, '
                'not a claim of host semantic equivalence')

    # Space.
    space = section('space')
    fs = space.get('filesystem') if isinstance(space.get('filesystem'), dict) else {}
    estimate = space.get('estimate') if isinstance(space.get('estimate'), dict) else {}
    if 'apiDiskSizeBytes' in space or 'apiDiskSizeGiB' in space:
        notes.append('provider disk size ignored: it is not filesystem or free-space evidence')
    available = fs.get('availableBytes')
    space_result = {'availableBytes': available, 'reserveFloorBytes': RESERVE_FLOOR_BYTES,
        'estimateKind': estimate.get('kind'), 'peakBytes': None, 'marginBytes': None,
        'fullBuildAllocation': 'unknown'}
    if not is_int(available):
        if 'freeBytes' in fs:
            block('space-free-not-available', 'freeBytes is not admission evidence; availableBytes is required', True)
        else:
            block('space-available-unknown', 'availableBytes not observed', True)
    if fs.get('queriedPath') != inputs['sourceRoot']:
        block('space-filesystem', f'filesystem must be queried at the source root {inputs["sourceRoot"]}; observed '
            f'{fs.get("queriedPath")!r}')
    peak = None
    if estimate.get('kind') != 'blazer':
        block('space-estimate-proxy' if estimate.get('kind') else 'space-estimate-unknown',
            f'estimate kind {estimate.get("kind")!r}: Frankel physical usage is proxy evidence only; a Blazer estimate is '
            'required', True)
    elif estimate.get('complete') is not True or estimate.get('bounded') is not True:
        block('space-estimate-incomplete', 'Blazer estimate is not complete and bounded', True)
    elif not (is_int(estimate.get('transientBytes')) and is_int(estimate.get('collisionBytes'))):
        block('space-estimate-incomplete', 'transientBytes and collisionBytes must both be observed', True)
    else:
        peak = estimate['transientBytes'] + estimate['collisionBytes']
        space_result['peakBytes'] = peak
    if peak is not None and is_int(available):
        margin = available - peak
        space_result['marginBytes'] = margin
        if margin < RESERVE_FLOOR_BYTES:
            block('space-reserve', f'availableBytes {available} minus peak {peak} leaves {margin}, below the reserve '
                f'{RESERVE_FLOOR_BYTES}; no reserve is lowered and nothing is deleted')

    if reasons:
        verdict = 'BLOCKED'
    elif obs.get('synthetic') is not False:
        verdict = 'ADMISSIBLE_SYNTHETIC'
        notes.append('synthetic fixture: this is not a real host pass')
    else:
        verdict = 'ADMISSIBLE'
    return {'schema': REPORT_SCHEMA, 'verdict': verdict, 'device': DEVICE, 'assessedAt': now,
        'synthetic': obs.get('synthetic') is not False, 'provenance': obs.get('provenance'),
        'observation': {'id': oid, 'observedAt': source.get('observedAt'), 'recipeSha': source.get('recipeSha'),
            'sourceRoot': source.get('sourceRoot'), 'hashes': source.get('hashes')},
        'reasons': reasons, 'unknowns': sorted(set(unknowns)), 'notes': notes,
        'tools': {'expected': len(HOST_TOOLS), 'executableMetadata': len(metadata_ok), 'usabilityDemonstrated': len(demonstrated)},
        'manifest': manifest_result, 'space': space_result,
        'actions': 'none: no download, generation, lunch, build, deletion or reserve change is selected or performed'}


def resolved_problems(resolved, manifest, oid, pinned_sha, pinned_projects, pins):
    if not isinstance(resolved, dict):
        return ['resolved manifest observation is not an object']
    problems = []
    if resolved.get('observationId') != oid:
        problems.append(f'resolved manifest is bound to {resolved.get("observationId")!r}, not {oid!r}')
    if resolved.get('wrapperSha256') != manifest.get('sha256'):
        problems.append('resolved wrapper sha256 differs from the raw manifest observation')
    includes = resolved.get('includes')
    if not isinstance(includes, list) or len(includes) != 1 or not isinstance(includes[0], dict):
        problems.append(f'wrapper must contain exactly one include; observed {includes!r}')
    else:
        inc = includes[0]
        name = inc.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.xml', name):
            problems.append(f'include name {name!r} is not a plain file in .repo/manifests')
        if inc.get('sha256') != pinned_sha:
            problems.append(f'include {name} sha256 {inc.get("sha256")} is not the pinned manifest {pinned_sha}')
    if resolved.get('otherElements') != []:
        problems.append(f'wrapper has elements besides the include: {resolved.get("otherElements")!r}')
    projects = resolved.get('projects') if isinstance(resolved.get('projects'), dict) else {}
    for path, revision in sorted(pins.items()):
        if projects.get(path) != revision or pinned_projects.get(path, {}).get('revision') != revision:
            problems.append(f'resolved {path} revision {projects.get(path)!r} is not {revision}')
    return problems


def observations_from_readiness_receipt(receipt, receipt_sha256, operation=None):
    """Map a device-readiness receipt to observations. Facts the receipt does not hold stay unknown."""
    oid = receipt.get('attempt')
    blazer = receipt.get('factory', {}).get('blazer', {})
    deps = receipt.get('adevtoolDeps', {})
    node = receipt.get('prerequisites', {}).get('node', {})
    fs = dict(receipt.get('filesystem', {}))
    fs['queriedPath'] = receipt.get('sourceRoot')
    present = blazer.get('status') == 'present' and blazer.get('checksum') == 'validated'
    revision = deps.get('revision', {})
    return {'schema': SCHEMA, 'synthetic': False, 'device': DEVICE,
        'provenance': {'kind': 'retained-readiness-receipt', 'receiptSha256': receipt_sha256, 'attempt': oid},
        'operation': operation or {'deviceArgs': None, 'allDevices': None, 'recipeSha': None, 'sourceRoot': None},
        'source': {'observationId': oid, 'observedAt': receipt.get('at'), 'recipeSha': receipt.get('recipeSha'),
            'sourceRoot': receipt.get('sourceRoot'), 'hashes': {'receiptSha256': receipt_sha256,
                'contractSha256': receipt.get('contract', {}).get('sha256')}},
        'pins': dict({p: v.get('observed') for p, v in receipt.get('pins', {}).items()}, observationId=oid),
        'factory': {'observationId': oid, 'status': 'present' if present else blazer.get('status', 'unknown'),
            'file': Path(blazer.get('path', '')).name or None, 'sha256': blazer.get('expectedSha256') if present else None},
        'adevtoolDeps': {'observationId': oid,
            'revision': revision.get('observed'),
            'tools': {n: {'status': t.get('status'), 'executable': t.get('executable'), 'usability': 'unknown'}
                for n, t in deps.get('tools', {}).items()}},
        'node': {'observationId': oid, 'location': node.get('location', 'unknown'), 'version': node.get('version', 'unknown')},
        'paths': dict({n: {'status': receipt.get('blazerPaths', {}).get(n, {}).get('status', 'unknown')}
            for n in ('vendorDestination', 'archive') + COLLISION_PATHS[1:]}, observationId=oid),
        'manifest': dict({k: receipt.get('manifest', {}).get(k) for k in ('sha256', 'pinnedSha256', 'comparison',
            'sizeBytes')}, observationId=oid),
        'resolvedManifest': None,
        'space': {'observationId': oid, 'filesystem': fs, 'estimate': {'kind': 'frankel-proxy', 'complete': False,
            'bounded': False, 'proxy': {k: v.get('allocatedBytes') for k, v in receipt.get('capacity', {})
                .get('allocation', {}).items()}}}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--observations', required=True, type=Path)
    parser.add_argument('--now', help='assessment time, UTC YYYY-MM-DDTHH:MM:SSZ (default: current time)')
    args = parser.parse_args(argv)
    now = args.now or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        report = assess(json.loads(args.observations.read_text()), load_inputs(), now)
    except (InvalidInput, ValueError, OSError) as error:
        print(json.dumps({'schema': REPORT_SCHEMA, 'verdict': 'INVALID', 'error': str(error)}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report['verdict'] in ('ADMISSIBLE', 'ADMISSIBLE_SYNTHETIC') else 1


if __name__ == '__main__':
    sys.exit(main())
