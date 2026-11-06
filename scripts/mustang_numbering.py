"""Inactive Mustang reservation/recovery contract; no allocator, storage or network adapter.

Only an external authority's fresh, authenticated head plus its immutable journal
can confirm consumption. Proposals are not reservations. The committed policy is
unbound and no production entrypoint imports this module. All I/O/CAS/transport
and epoch-creation authority remain outside this contract and unimplemented.
"""
import hashlib
import json
from pathlib import Path
import re
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
LEGACY_MAX = 9999123199
CAPACITY = 9999999999 - LEGACY_MAX
FAILED_RUN = '41884368820'
FAILED_SOURCE = '6be302942f348b58f8a0ce2406c7202f2dd676bf'
WORKFLOW = '.github/workflows/mustang-ota-dedicated.yml'
HEX = re.compile(r'[0-9a-f]{64}')
SHA = re.compile(r'[0-9a-f]{40}')
POSITIVE = re.compile(r'[1-9][0-9]{0,19}')
POLICY_KEYS = {'schema', 'device', 'status', 'epoch', 'authority', 'minimumTimestamp',
               'legacyCeiling', 'failedRun', 'failedSource', 'legacyAllocationKnown', 'legacyProofSha256'}
ANCHOR_KEYS = {'schema', 'policySha256', 'epoch', 'authority', 'counter', 'head'}
EVENT_KEYS = {'schema', 'policySha256', 'epoch', 'counter', 'build', 'previous', 'run', 'buildTimestamp'}
RUN_KEYS = {'repositoryId', 'workflow', 'runId', 'attempt', 'source'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(label+' fields differ')


def counter(value):
    if type(value) is not int or not 0 <= value <= CAPACITY:
        raise ValueError('counter outside the ten-digit namespace')
    return value


def timestamp(value):
    if not isinstance(value, str) or not POSITIVE.fullmatch(value) or int(value) >= 2**63:
        raise ValueError('explicit positive signed-64-bit build timestamp required')
    return int(value)


def policy(value, root=ROOT):
    exact(value, POLICY_KEYS, 'numbering policy')
    if value['schema'] != 'roseos.mustang-numbering-policy.v1' or value['device'] != 'mustang' \
            or value['status'] != 'bound':
        raise ValueError('Mustang numbering policy is unbound')
    if not isinstance(value['epoch'], str) or not HEX.fullmatch(value['epoch']):
        raise ValueError('an independently established immutable epoch is required')
    authority = value['authority']
    if not isinstance(authority, str): raise ValueError('independent authority identity required')
    url = urllib.parse.urlsplit(authority)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('independent authority identity required')
    timestamp(value['minimumTimestamp'])
    proof = Path(root)/'os/tests/fixtures/mustang-numbering/sources.json'
    if value['legacyCeiling'] != str(LEGACY_MAX) or value['failedRun'] != FAILED_RUN \
            or value['failedSource'] != FAILED_SOURCE or value['legacyAllocationKnown'] is not False \
            or value['legacyProofSha256'] != hashlib.sha256(proof.read_bytes()).hexdigest():
        raise ValueError('legacy quarantine source proof differs; lost allocation stays unknown')
    return digest(value)


def run_context(value):
    exact(value, RUN_KEYS, 'run')
    if value['repositoryId'] != '1428996648' or value['workflow'] != WORKFLOW \
            or type(value['attempt']) is not int or value['attempt'] != 1 \
            or not isinstance(value['runId'], str) or not POSITIVE.fullmatch(value['runId']) \
            or not isinstance(value['source'], str) or not SHA.fullmatch(value['source']):
        raise ValueError('fresh dedicated Mustang run/source required; reruns cannot allocate')


def genesis(value):
    return digest({'schema':'roseos.mustang-numbering-genesis.v1', 'policySha256':policy(value)})


def build_for_counter(value):
    """Pure representation boundary; this does not reserve or consume a number."""
    counter(value)
    if value == 0: raise ValueError('genesis consumes no build ID')
    return str(LEGACY_MAX + value)


def expected_anchor(value, events):
    """Validate journal contents and compute the expected head; never issue/authenticate one."""
    pin = policy(value)
    if not isinstance(events, list) or len(events) > CAPACITY:
        raise ValueError('bounded complete authority journal required')
    head = genesis(value)
    prior_time = timestamp(value['minimumTimestamp'])
    runs = set()
    for index, event in enumerate(events, 1):
        exact(event, EVENT_KEYS, 'reservation')
        if event['schema'] != 'roseos.mustang-counter-reservation.v1' or event['policySha256'] != pin \
                or event['epoch'] != value['epoch'] or type(event['counter']) is not int \
                or event['counter'] != index or event['build'] != build_for_counter(index) \
                or event['previous'] != head:
            raise ValueError('reservation chain, epoch, counter or identity differs')
        run_context(event['run'])
        if event['run']['runId'] in runs: raise ValueError('run already consumes a counter')
        runs.add(event['run']['runId'])
        epoch = timestamp(event['buildTimestamp'])
        if epoch <= prior_time: raise ValueError('actual build timestamp must increase independently of the ID')
        prior_time = epoch
        head = digest(event)
    counter(len(events))
    return {'schema':'roseos.mustang-counter-anchor.v1', 'policySha256':pin, 'epoch':value['epoch'],
            'authority':value['authority'], 'counter':len(events), 'head':head}


def snapshot(value, anchor, events):
    """Caller must obtain anchor freshly from the independently trusted authority.

    A dictionary, checksum or a local backup cannot authenticate/freshen it.
    This function only checks consistency against that required external input.
    """
    exact(anchor, ANCHOR_KEYS, 'independent anchor')
    counter(anchor['counter'])
    observed = expected_anchor(value, events)
    if anchor != observed:
        raise ValueError('authority head/journal differs; stale, incomplete or wrong-epoch recovery refused')
    return observed


def propose(value, anchor, events, run, build_timestamp):
    """Return an uncommitted CAS proposal, never an allocation receipt."""
    previous = snapshot(value, anchor, events)
    run_context(run)
    if any(row['run']['runId'] == run['runId'] for row in events):
        raise ValueError('run already consumes a counter; recover its existing receipt')
    epoch = timestamp(build_timestamp)
    floor = timestamp(events[-1]['buildTimestamp'] if events else value['minimumTimestamp'])
    if epoch <= floor: raise ValueError('actual build timestamp must increase independently of the ID')
    sequence = previous['counter'] + 1
    event = {'schema':'roseos.mustang-counter-reservation.v1', 'policySha256':previous['policySha256'],
             'epoch':value['epoch'], 'counter':sequence, 'build':build_for_counter(sequence),
             'previous':previous['head'], 'run':dict(run), 'buildTimestamp':build_timestamp}
    return {'schema':'roseos.mustang-counter-proposal.v1', 'expected':dict(previous), 'reservation':event}


def confirm(value, proposal, anchor, events):
    """Confirm a proposal only after authoritative commit/readback, including uncertain outcomes."""
    exact(proposal, {'schema', 'expected', 'reservation'}, 'proposal')
    if proposal['schema'] != 'roseos.mustang-counter-proposal.v1': raise ValueError('proposal schema differs')
    snapshot(value, anchor, events)
    event = proposal['reservation']
    exact(event, EVENT_KEYS, 'reservation')
    sequence = counter(event['counter'])
    if sequence == 0 or sequence > len(events) or events[sequence-1] != event:
        raise ValueError('proposal not committed by the authority; do not consume its build ID')
    snapshot(value, proposal['expected'], events[:sequence-1])
    # Revalidate the proposal at its original prefix, not merely a matching numeric ID.
    reconstructed = propose(value, proposal['expected'], events[:sequence-1],event['run'],event['buildTimestamp'])
    if proposal != reconstructed: raise ValueError('proposal content differs')
    return json.loads(json.dumps(event))


def recovery_suffix(value, anchor, authority_events, local_events):
    """Return already consumed receipts missing from a replica; never issue a new counter."""
    snapshot(value, anchor, authority_events)
    expected_anchor(value, local_events)
    if len(local_events) > len(authority_events) or local_events != authority_events[:len(local_events)]:
        raise ValueError('replica is not an exact authority prefix; no automatic reconciliation')
    return json.loads(json.dumps(authority_events[len(local_events):]))


if __name__ == '__main__':
    raise SystemExit('source-only contract: no allocator, epoch creation, restore or authority adapter is enabled')
