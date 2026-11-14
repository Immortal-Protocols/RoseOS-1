"""Mustang's raised-edge proof binding; uses Release's shared scanner and Product's spec.

Only target-files supplied by the existing adapters are read. This is feature
inclusion evidence, never signing-result or physical-behaviour verification.
"""
import hashlib
import json
from pathlib import Path
import re
import stat

import edge_artifact

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = 'os/release-pipeline/frankel.json'
CHECKER_PATH = 'scripts/edge_artifact.py'
DIAG = 'persist.wm.debug.rose_edge_diag'
SCHEMA = 'roseos.mustang-edge-proof.v1'
HEX = re.compile(r'[0-9a-f]{64}')
L3 = 'Lcom/android/quickstep/fallback/'
CLASSES = (L3+'RoseEdgeGesture;', L3+'RoseEdgeSwipe;',
           'Lcom/android/wm/shell/roseedge/RoseEdgeController;',
           'Lcom/android/wm/shell/RoseEdgeMicSlot;')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def configuration():
    spec = json.loads((ROOT/SPEC_PATH).read_text()).get('roseEdge')
    edge_artifact.validate(spec)
    classes = {row['class']: row for row in spec['classes']}
    if not {'persist.wm.debug.rose_edge=true', 'persist.wm.debug.pixel_agent_tray=1'} <= set(spec['props']) \
            or not set(CLASSES) <= set(classes) \
            or any(classes[name]['method'] is not None for name in CLASSES) \
            or {'apk': classes[CLASSES[1]]['apk'], 'literal': DIAG} not in spec.get('literals', []):
        raise ValueError('Mustang requires the configured raised-edge checks and independent #548 Launcher3 marker')
    identity = {'checker': CHECKER_PATH, 'checkerSha256': digest(ROOT/CHECKER_PATH),
                'spec': SPEC_PATH,
                'specSha256': hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    return spec, identity


def binding(candidate_source, recipe_source, build, epoch, run_id, attempt, compile_run, compile_attempt):
    value = dict(device='mustang', candidateSource=candidate_source, recipeSource=recipe_source,
                 build=build, timestamp=epoch, runId=run_id, runAttempt=attempt,
                 compileRunId=compile_run, compileRunAttempt=compile_attempt)
    if any(not re.fullmatch(r'[0-9a-f]{40}', str(value[k])) for k in ('candidateSource', 'recipeSource')) \
            or not re.fullmatch(r'[1-9][0-9]{9}', str(build)) \
            or any(not re.fullmatch(r'[1-9][0-9]*', str(value[k]))
                   for k in ('timestamp', 'runId', 'runAttempt', 'compileRunId', 'compileRunAttempt')):
        raise ValueError('invalid Mustang raised-edge source/build/run binding')
    return {k: str(v) for k, v in value.items()}


def signing_binding(record):
    try:
        return binding(record['candidateSource'], record['recipeSource'], record['build'], record['timestamp'],
                       record['runId'], record['runAttempt'], record['compileRunId'], record['compileRunAttempt'])
    except (KeyError, TypeError):
        raise ValueError('Mustang raised-edge source/build/run binding missing') from None


def archive_identity(row):
    try:
        value = {k: row[k] for k in ('sha256', 'sizeBytes')}
        if not isinstance(value['sha256'], str) or not HEX.fullmatch(value['sha256']) \
                or type(value['sizeBytes']) is not int or value['sizeBytes'] <= 0:
            raise ValueError
        return value
    except (KeyError, TypeError, ValueError):
        raise ValueError('Mustang raised-edge archive identity missing or invalid') from None


def observed_archive(path):
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('Mustang raised-edge input must be a regular target-files archive')
    return dict(sha256=digest(path), sizeBytes=path.stat().st_size)


def inspect(path, stage, bound, expected):
    """Always return a failure proof for a checker/input error; caller persists it before refusing."""
    proof = dict(schema=SCHEMA, stage=stage, binding=bound, status='failed',
                 targetFiles=archive_identity(expected), failures=[], evidence=None, checker=None)
    try:
        spec, proof['checker'] = configuration()
        if stage not in ('unsigned', 'signed') or observed_archive(path) != proof['targetFiles']:
            raise ValueError('admitted target-files bytes changed')
        failures, evidence = edge_artifact.check(path, spec)
        proof.update(failures=failures, evidence=evidence)
        if observed_archive(path) != proof['targetFiles']:
            raise ValueError('target-files bytes changed during raised-edge inspection')
        if not failures:
            proof['status'] = 'passed'
    except Exception as error:
        proof.update(status='failed', error=type(error).__name__)
        proof['failures'].append('raised-edge input/checker failed: '+type(error).__name__)
    return proof


def require(proof, stage, bound, archive):
    """Validate the actual scanner rows, not just a pass flag, before any consumer proceeds."""
    spec, checker = configuration()
    try:
        if proof['schema'] != SCHEMA or proof['stage'] != stage or proof['binding'] != bound \
                or proof['checker'] != checker or proof['targetFiles'] != archive_identity(archive) \
                or proof['status'] != 'passed' or proof['failures'] != [] or proof.get('error'):
            raise ValueError
        evidence = proof['evidence']
        if len(evidence['props']) != len(spec['props']) or len(evidence['classes']) != len(spec['classes']) \
                or len(evidence['literals']) != len(spec['literals']):
            raise ValueError
        for row, prop in zip(evidence['props'], spec['props']):
            if row['prop'] != prop or row['pass'] is not True or row['conflicting'] != [] \
                    or not row['files'] or not set(row['files']) <= set(spec['propFiles']):
                raise ValueError
        hashes = evidence['apkSha256']
        if not hashes or any(not isinstance(h, str) or not HEX.fullmatch(h) for h in hashes.values()):
            raise ValueError
        for rows, entries, is_class in ((evidence['classes'], spec['classes'], True),
                                       (evidence['literals'], spec['literals'], False)):
            for row, entry in zip(rows, entries):
                if row['pass'] is not True or not row['apks'] \
                        or any(not re.fullmatch(entry['apk'], apk) or apk not in hashes for apk in row['apks']):
                    raise ValueError
                apk, dex = row['where'].rsplit('!', 1)
                if apk not in row['apks'] or not edge_artifact.DEX_NAME.fullmatch(dex):
                    raise ValueError
                if is_class:
                    if any(row[k] != entry[k] for k in ('class', 'method', 'literal')) \
                            or row['by'] not in ('class', 'literal') \
                            or (row['by'] == 'literal' and not entry['literal']):
                        raise ValueError
                elif row['literal'] != entry['literal']:
                    raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError(f'complete current {stage} Mustang raised-edge proof required') from None
    return proof


def require_pair(record):
    """Signing and staged receipts expose the same independently bound two-archive proof."""
    bound = signing_binding(record)
    try:
        archives, proofs = record['edgeTargetFiles'], record['roseEdge']
        if set(archives) != {'unsigned', 'signed'} or set(proofs) != {'unsigned', 'signed'}:
            raise ValueError
        for stage in ('unsigned', 'signed'):
            require(proofs[stage], stage, bound, archives[stage])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('both current Mustang raised-edge proofs required: '+str(error)) from None
    return proofs


def require_signing(record):
    proofs = require_pair(record)
    try:
        if record['edgeTargetFiles'] != {
                'unsigned': archive_identity(record['compileArtifacts']['targetFiles']),
                'signed': archive_identity(record['outputs']['targetFiles'])}:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError('raised-edge proofs do not bind the consumed/produced Mustang target-files') from None
    return proofs
