"""Mustang's local, write-once UTC-hour allocation; no cloud writer or signer.

YYMMDDHH00 uses the legacy generator's unused 00 suffix. Unknown legacy
allocations stay unknown. Ordering is proved against observed releases/local
state and this series, not an unpublished unknown legacy value.
"""
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import time
import urllib.request

import mustang_existing_lock
import mustang_hosttools as readers
import mustang_paths

ROOT = Path(__file__).resolve().parents[1]
POLICY = 'os/build-only/mustang-clock-numbering.json'
SCHEMA = 'roseos.mustang-clock-hour00.v1'
JOURNAL = 'mustang-clock-hour00-v1'
WORKFLOW = '.github/workflows/mustang-ota-dedicated.yml'
PROOF = 'os/tests/fixtures/mustang-numbering/sources.json'
PROOF_SHA = '5eb94c78b2646d9b618877257345df6a30651d55375f187b839578588d85ff59'
HEX = re.compile(r'[0-9a-f]{64}')


def canonical(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def utc(value):
    try:
        return dt.datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)
    except (ValueError, TypeError):
        raise ValueError('exact UTC timestamp required') from None


def build_for_hour(value):
    if value.tzinfo is None or value.utcoffset() != dt.timedelta(0) or not 2026 <= value.year <= 2099:
        raise ValueError('clock numbering requires UTC in 2026..2099; no century rollover')
    return value.strftime('%y%m%d%H') + '00'


def policy(lane, cfg, root=ROOT):
    root = Path(root)
    if cfg.get('clockNumbering') != POLICY:
        raise ValueError('explicit Mustang UTC-hour numbering selection required')
    value = json.loads((root / POLICY).read_text())
    expected = dict(schema=SCHEMA, format='YYMMDDHH00', timezone='UTC',
        minimumYear=2026, maximumYear=2099, minimumTimestamp='1793755347',
        legacyFailedRun='41884368820', legacyAllocationKnown=False,
        legacySourceProof=PROOF, legacySourceProofSha256=PROOF_SHA,
        initializationWorkflowId=408460629, initializationRunNumber=35,
        journal=JOURNAL, maximumReservations=100000)
    if value != expected or hashlib.sha256((root / PROOF).read_bytes()).hexdigest() != PROOF_SHA:
        raise ValueError('UTC-hour numbering policy/legacy source proof differs')
    state = json.loads((root / cfg['allocationState']).read_text())
    if state.get('failedRun') != '41884368820' or state.get('unknown') is not True \
            or state.get('allocatedBuild') is not None or state.get('consumedBuilds') is not None:
        raise ValueError('lost legacy allocation must remain unknown')
    if not cfg.get('dataFilesystemUuid') or not lane.get('vm'):
        raise ValueError('dedicated clock ledger requires builder and disk identity')
    return value


def actions_context(actual, env, value):
    repo = 'Immortal-Protocols/RoseOS-1/' + WORKFLOW + '@refs/heads/main'
    if actual.get('id') != int(env['GITHUB_RUN_ID']) or actual.get('run_attempt') != 1 \
            or actual.get('workflow_id') != value['initializationWorkflowId'] \
            or actual.get('head_sha') != env['GITHUB_SHA'] or actual.get('head_branch') != 'main' \
            or actual.get('path') != WORKFLOW or actual.get('event') != 'workflow_dispatch' \
            or env.get('GITHUB_WORKFLOW_REF') != repo or env.get('GITHUB_RUN_ATTEMPT') != '1' \
            or actual.get('status') != 'in_progress' or actual.get('conclusion') is not None \
            or any((actual.get(k) or {}).get('id') != 1428996648 for k in ('repository', 'head_repository')) \
            or type(actual.get('run_number')) is not int or actual['run_number'] < value['initializationRunNumber']:
        raise ValueError('UTC-hour Actions context differs')
    build_for_hour(utc(actual['created_at']))
    return dict(schema='roseos.mustang-clock-context.v1', runId=str(actual['id']), runAttempt='1',
        source=actual['head_sha'], workflowId=actual['workflow_id'], runNumber=actual['run_number'],
        createdAt=actual['created_at'], policySha256=digest(value))


def context(value, env):
    encoded = env.get('MUSTANG_CLOCK_CONTEXT', '')
    if not 0 < len(encoded) <= 8192:
        raise ValueError('hosted UTC-hour context missing')
    try:
        result = json.loads(base64.b64decode(encoded, validate=True))
    except (ValueError, TypeError):
        raise ValueError('hosted UTC-hour context malformed') from None
    if not re.fullmatch(r'[1-9][0-9]{0,19}', str(result.get('runId',''))) or not re.fullmatch(r'[0-9a-f]{40}', str(result.get('source',''))) \
            or set(result) != {'schema','runId','runAttempt','source','workflowId','runNumber','createdAt','policySha256'} \
            or result.get('schema') != 'roseos.mustang-clock-context.v1' \
            or result.get('runId') != env.get('GITHUB_RUN_ID') or result.get('runAttempt') != '1' \
            or env.get('GITHUB_RUN_ATTEMPT') != '1' or result.get('source') != env.get('GITHUB_SHA') \
            or result.get('workflowId') != value['initializationWorkflowId'] \
            or type(result.get('runNumber')) is not int or result['runNumber'] < value['initializationRunNumber'] \
            or result.get('policySha256') != digest(value):
        raise ValueError('hosted UTC-hour context binding differs')
    build_for_hour(utc(result['createdAt']))
    return result


def new_file(directory, name, document):
    """Anchored, exclusive, fsynced JSON; keep uncertain final state for review."""
    fd = readers._walk_dir(readers._body(str(directory)))
    temporary = '.pending-' + name
    created = False
    try:
        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        created = True
        with os.fdopen(out, 'wb') as stream:
            stream.write(canonical(document)); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.fsync(fd)
    finally:
        try:
            if created:
                os.unlink(temporary, dir_fd=fd)
        finally:
            os.close(fd)


def read(directory, name):
    raw = readers.read_regular(str(directory), str(Path(directory) / name), 1 << 20)
    return json.loads(raw)


def load_journal(directory, value, lane, cfg, ctx, *, initialize=True):
    parent = directory.parent
    fd = readers._walk_dir(readers._body(str(parent)))
    try:
        if not os.path.lexists(directory):
            if not initialize:
                raise ValueError('clock ledger absent; read-only validation cannot initialize it')
            # This one observed workflow number may initialize once. After loss,
            # later runs cannot manufacture an empty replacement authority.
            if ctx['runNumber'] != value['initializationRunNumber']:
                raise ValueError('clock ledger absent; recovery required, no automatic reinitialization')
            os.mkdir(directory.name, 0o700, dir_fd=fd); os.fsync(fd)
            os.mkdir(directory / 'reservations', 0o700)
            origin = dict(schema=SCHEMA, policySha256=digest(value), builder=lane['vm'],
                dataFilesystemUuid=cfg['dataFilesystemUuid'], initializedBy=ctx)
            new_file(directory, 'origin.json', origin)
            new_file(directory, 'head.json', dict(schema=SCHEMA, count=0, head=digest(origin),
                build=None, timestamp=value['minimumTimestamp']))
    finally:
        os.close(fd)
    rootfd = readers._walk_dir(readers._body(str(directory)))
    try:
        if os.fstat(rootfd).st_dev != os.stat(parent,follow_symlinks=False).st_dev:
            raise ValueError('clock journal crosses the dedicated data filesystem')
        if set(os.listdir(rootfd)) != {'origin.json', 'head.json', 'reservations'}:
            raise ValueError('clock journal has partial or unknown state; preserve it')
    finally:
        os.close(rootfd)
    origin, head = read(directory, 'origin.json'), read(directory, 'head.json')
    initial = origin.get('initializedBy', {})
    if origin.get('schema') != SCHEMA or origin.get('policySha256') != digest(value) \
            or origin.get('builder') != lane['vm'] or origin.get('dataFilesystemUuid') != cfg['dataFilesystemUuid'] \
            or initial.get('runNumber') != value['initializationRunNumber'] \
            or initial.get('workflowId') != value['initializationWorkflowId']:
        raise ValueError('clock journal origin/disk differs')
    reservations = directory / 'reservations'
    fd = readers._walk_dir(readers._body(str(reservations)))
    try:
        if os.fstat(fd).st_dev != os.stat(directory,follow_symlinks=False).st_dev:
            raise ValueError('clock reservation directory crosses filesystems')
        names = sorted(os.listdir(fd))
    finally:
        os.close(fd)
    if len(names) > value['maximumReservations']:
        raise ValueError('clock reservation bound exceeded')
    records = []
    total_bytes = 0
    deadline = time.monotonic() + 120
    previous = digest(origin)
    floor = value['minimumTimestamp']
    seen = set()
    for index, name in enumerate(names, 1):
        if not re.fullmatch(r'[1-9][0-9]{7}00\.json', name):
            raise ValueError('unknown clock reservation entry')
        if time.monotonic() > deadline:
            raise ValueError('clock journal deadline exceeded')
        record = read(reservations, name)
        total_bytes += len(canonical(record))
        if total_bytes > 256 << 20:
            raise ValueError('clock journal byte bound exceeded')
        require_allocation(record, lane, cfg, root=ROOT, value=value)
        proof = record['observed']['clock']
        stamp = int(utc(record['reservedAt']).timestamp())
        if record['build'] + '.json' != name or proof['sequence'] != index or proof['previous'] != previous \
                or proof['originSha256'] != digest(origin) or stamp <= int(floor) \
                or record['runId'] in seen or (records and record['build'] <= records[-1]['build']):
            raise ValueError('clock journal chain differs; no new allocation')
        records.append(record); seen.add(record['runId']); previous = digest(record); floor = str(stamp)
    expected = dict(schema=SCHEMA, count=len(records), head=previous,
        build=records[-1]['build'] if records else None, timestamp=floor)
    if type(head.get('count')) is not int or head != expected:
        raise ValueError('clock head/reservations differ; consumption uncertain, no retry')
    return origin, head, records


def compile_identity(op, wall, *, lane, cfg, root=ROOT, env=None):
    """Read-only compiler binding to this run's existing durable clock record.

    Clock IDs are YYMMDDHH00, not the old YYYYMMDDNN format. Their timestamp
    comes from the authenticated reservation, never from a guessed ID prefix.
    Missing/corrupt journal state is refused without initialization or repair.
    """
    env=os.environ if env is None else env
    number,stamp=op['buildNumber'],op['buildDatetime']
    facts=dict(buildNumber=number,buildDatetime=stamp);phase='policy'
    try:
        value=policy(lane,cfg,root);ctx=context(value,env);paths=mustang_paths.layout(lane)
        directory=paths['storageRoot']/JOURNAL
        phase='journal'
        with mustang_existing_lock.shared(paths['lockPath'],expected=str(paths['lockPath'])):
            _origin,_head,records=load_journal(directory,value,lane,cfg,ctx,initialize=False)
        phase='reservation'
        matches=[record for record in records if record['build']==number]
        if len(matches)!=1 or not records or records[-1]['build']!=number:
            return ['clock compile requires the current durable reservation'],facts
        record=matches[0]
        facts['reservation']=str(directory/'reservations'/(number+'.json'))
        require_allocation(record,lane,cfg,root=root,value=value,env=env,timestamp=stamp)
        if str(op['integrationDir'])!=record['integrationDir']:
            return ['clock compile integration directory differs from its reservation'],facts
        source=mustang_paths.attempt(lane,'prep',ctx['runId'],ctx['runAttempt'])/'source'
        if Path(op['sourceRoot'])!=source:
            return ['clock compile source directory differs from its reserved run'],facts
        phase='inputs'
        inputs=Path(record['workDir'])/'inputs/local-inputs.json'
        if Path(op['inputsReceipt'])!=inputs:
            return ['clock compile input receipt is outside its reserved work directory'],facts
        document=json.loads(readers.read_regular(str(paths['storageRoot']),str(inputs),4 << 20))
        bound=str((document.get('candidate') or {}).get('build'));facts['inputsReceiptBuild']=bound
        if bound!=number:return ['clock compile inputs differ from the reserved build'],facts
        phase='timestamp'
        when=int(stamp)
        if when>int(wall()):return ['clock compile timestamp is in the future'],facts
        facts.update(buildDatetimeUtc=record['reservedAt'],clockAllocationSha256=digest(record),
            clockSequence=record['observed']['clock']['sequence'])
        return [],facts
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as error:
        # Never expose arbitrary record contents or exception text.
        return ['clock compile identity refused at '+phase+': '+type(error).__name__],facts


def bind_compiler(engine,lane,cfg,*,root=ROOT,env=None):
    """Bind only this private Mustang engine; legacy/other-device engines stay independent."""
    if cfg.get('clockNumbering') is not None:
        import functools
        engine.identity_problems=functools.partial(compile_identity,lane=lane,cfg=cfg,root=root,env=env)
    return engine


def require_allocation(record, lane, cfg, *, root=ROOT, value=None, env=None, timestamp=None):
    value = value or policy(lane, cfg, root)
    proof = record.get('observed', {}).get('clock', {})
    ctx = proof.get('context', {})
    fake = dict(GITHUB_RUN_ID=record.get('runId'), GITHUB_RUN_ATTEMPT=str(record.get('runAttempt')),
        GITHUB_SHA=record.get('source'))
    encoded = base64.b64encode(canonical(ctx)).decode()
    checked = context(value, dict(fake, MUSTANG_CLOCK_CONTEXT=encoded))
    stamp = int(utc(record['reservedAt']).timestamp())
    paths = mustang_paths.layout(lane); base = paths['storageRoot']; build = record['build']
    if set(record) != {'schema','device','build','source','runId','runAttempt','reservedAt','workDir','integrationDir','observed'} \
            or record.get('schema') != 'roseos.mustang-allocation.v1' or record.get('device') != 'mustang' \
            or build != build_for_hour(utc(checked['createdAt'])) \
            or record.get('workDir') != str(base / ('mustang-integrate-' + build)) \
            or record.get('integrationDir') != str(base / ('mustang-reconcile-' + build)) \
            or set(proof) != {'schema','policySha256','context','sequence','previous','originSha256','legacyAllocationKnown','legacySuffixExcluded','publicTimestampFloor','publicChannelEvidence'} \
            or proof.get('schema') != SCHEMA or proof.get('policySha256') != digest(value) \
            or proof.get('legacyAllocationKnown') is not False or proof.get('legacySuffixExcluded') != '01..99' \
            or type(proof.get('sequence')) is not int or proof['sequence'] < 1 \
            or not HEX.fullmatch(str(proof.get('previous', ''))) \
            or not HEX.fullmatch(str(proof.get('originSha256', ''))) \
            or not int(value['minimumTimestamp']) < stamp < 2**63 \
            or not 0 <= stamp - int(utc(checked['createdAt']).timestamp()) < 14400 \
            or stamp <= int(proof.get('publicTimestampFloor', '0')):
        raise ValueError('clock allocation binding differs')
    if env is not None and any(fake[k] != env.get(k) for k in fake):
        raise ValueError('clock allocation is not this run/source')
    if timestamp is not None and str(stamp) != str(timestamp):
        raise ValueError('clock allocation and actual compiled timestamp differ')
    return proof


def public_floor(rows, initial, fetch_bytes):
    floor = int(initial); evidence = []
    for row in rows:
        tag = row.get('tag_name', '')
        if not re.fullmatch(r'mustang-[1-9][0-9]{9}', tag) or row.get('draft') is not False:
            continue
        assets = [a for a in row.get('assets', []) if a.get('name') == 'mustang-stable']
        if len(assets) != 1:
            raise ValueError('published Mustang channel metadata unavailable')
        asset = assets[0]
        url = 'https://github.com/Immortal-Protocols/pixel-ota/releases/download/' + tag + '/mustang-stable'
        if asset.get('browser_download_url') != url or not 0 < asset.get('size', 0) <= 1024:
            raise ValueError('published channel metadata identity differs')
        raw = fetch_bytes(url)
        if len(raw) != asset['size'] or 'sha256:' + hashlib.sha256(raw).hexdigest() != asset.get('digest'):
            raise ValueError('published channel digest differs')
        fields = raw.decode('ascii').split()
        if len(fields) != 4 or fields[0] != tag[8:] or fields[2:] != ['mustang', 'stable'] \
                or not re.fullmatch(r'[1-9][0-9]{0,18}', fields[1]):
            raise ValueError('published channel timestamp differs')
        floor = max(floor, int(fields[1]))
        evidence.append(dict(tag=tag, assetId=asset['id'], sha256=asset['digest'][7:], timestamp=fields[1]))
    return floor, evidence


def channel_bytes(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'roseos-mustang-clock', 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(1025)
    if len(raw) > 1024:
        raise ValueError('public channel byte bound exceeded')
    return raw


def reserve(lane, cfg, env, *, root=ROOT, clock=time.time, fetch=None, fetch_bytes=channel_bytes):
    value = policy(lane, cfg, root); ctx = context(value, env)
    paths = mustang_paths.layout(lane); directory = paths['storageRoot'] / JOURNAL
    spec = importlib.util.spec_from_file_location('mustang_clock_legacy_reader', Path(root) / 'scripts/mustang-allocate.py')
    legacy = importlib.util.module_from_spec(spec); spec.loader.exec_module(legacy)
    raw_rows = []
    def releases(url):
        rows = (fetch or legacy.allocator._fetch_json)(url)
        if not isinstance(rows, list):
            raise ValueError('release listing unavailable')
        raw_rows.extend(rows); return rows
    with mustang_existing_lock.shared(paths['lockPath'], expected=str(paths['lockPath'])):
        known, observed = legacy.observe(paths['home'], 'Immortal-Protocols/pixel-ota',
            'https://api.github.com', fetch=releases, root=root)
        floor, public = public_floor(raw_rows, value['minimumTimestamp'], fetch_bytes)
        origin, head, records = load_journal(directory, value, lane, cfg, ctx)
        build = build_for_hour(utc(ctx['createdAt']))
        now = dt.datetime.fromtimestamp(int(clock()), dt.timezone.utc)
        if build <= max(known | {r['build'] for r in records}, default='0'):
            raise ValueError('UTC dispatch hour already consumed or clock behind; wait for a fresh hour')
        if any(r['runId'] == ctx['runId'] for r in records):
            raise ValueError('this run already consumed a reservation; no retry')
        if int(now.timestamp()) <= max(floor, int(head['timestamp'])):
            raise ValueError('build timestamp is not above known public/local floor')
        for pattern in legacy.DERIVED:
            if os.path.lexists(paths['storageRoot'] / pattern.format(build=build)):
                raise ValueError('clock allocation output path already exists')
        observed['clock'] = dict(schema=SCHEMA, policySha256=digest(value), context=ctx,
            sequence=len(records) + 1, previous=head['head'], originSha256=digest(origin),
            legacyAllocationKnown=False, legacySuffixExcluded='01..99', publicTimestampFloor=str(floor),
            publicChannelEvidence=public)
        record = dict(schema='roseos.mustang-allocation.v1', device='mustang', build=build,
            source=ctx['source'], runId=ctx['runId'], runAttempt='1', reservedAt=now.strftime('%Y-%m-%dT%H:%M:%SZ'),
            workDir=str(paths['storageRoot'] / legacy.DERIVED[0].format(build=build)),
            integrationDir=str(paths['storageRoot'] / legacy.DERIVED[1].format(build=build)), observed=observed)
        require_allocation(record, lane, cfg, root=root, value=value, env=env)
        if len(records) >= value['maximumReservations']:
            raise ValueError('clock reservation bound exhausted')
        new_file(directory / 'reservations', build + '.json', record)
        updated = dict(schema=SCHEMA, count=len(records) + 1, head=digest(record),
            build=build, timestamp=str(int(now.timestamp())))
        new_file(directory, 'next-head.json', updated)
        fd = readers._walk_dir(readers._body(str(directory)))
        try:
            if read(directory, 'head.json') != head:
                raise ValueError('clock head changed; preserve uncertain reservation')
            os.replace('next-head.json', 'head.json', src_dir_fd=fd, dst_dir_fd=fd); os.fsync(fd)
        finally:
            os.close(fd)
        _origin, _head, reread = load_journal(directory, value, lane, cfg, ctx)
        if reread[-1] != record:
            raise ValueError('clock reservation readback differs')
        return record


def require_bundle(bundle, lane, cfg, root=ROOT):
    record = bundle.get('clockAllocation')
    if not isinstance(record, dict) or record.get('build') != bundle.get('build'):
        raise ValueError('UTC-hour allocation proof missing from signing/staging bundle')
    import mustang_retained
    run,attempt,source=mustang_retained.compile_context(bundle)
    env = dict(GITHUB_RUN_ID=run, GITHUB_RUN_ATTEMPT=attempt,GITHUB_SHA=source)
    return require_allocation(record, lane, cfg, root=root, env=env, timestamp=bundle['timestamp'])
