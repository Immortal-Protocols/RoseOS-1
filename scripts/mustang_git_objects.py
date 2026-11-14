"""Inactive, read-only codec for a narrow SHA-1 Git numbering journal.

Object integrity and checkpoint structure do not establish authority freshness.
There is no transport, writer, checkpoint provider, trust root or allocator here.
Only deterministic fixture getters/verifiers are supplied by the tests.
"""
from abc import abstractmethod
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Callable, Protocol

import mustang_numbering as numbering

OID = re.compile(r'[0-9a-f]{40}')
TREE_PREFIX = b'100644 journal.json\0'
MESSAGE = b'Mustang numbering journal\n'
OBJECT_LIMITS = {'commit':4096, 'tree':len(TREE_PREFIX)+20, 'blob':65536}


def canonical(value):
    return (json.dumps(value,sort_keys=True,separators=(',', ':'),allow_nan=False)+'\n').encode()


def object_oid(kind, data):
    """Git identity only; never an authentication or freshness assertion."""
    if kind not in OBJECT_LIMITS or not isinstance(data,bytes):
        raise ValueError('unsupported Git object kind/bytes')
    return hashlib.sha1(kind.encode()+b' '+str(len(data)).encode()+b'\0'+data).hexdigest()


def oid(value):
    if not isinstance(value,str) or not OID.fullmatch(value) or value=='0'*40:
        raise ValueError('nonzero SHA-1 Git object ID required; journal digests are separate')
    return value


def document(data):
    if not isinstance(data,bytes) or len(data)>OBJECT_LIMITS['blob']:
        raise ValueError('bounded journal/checkpoint JSON bytes required')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError('duplicate journal JSON field')
            result[key]=value
        return result
    value=json.loads(data.decode('utf-8'),object_pairs_hook=pairs)
    if canonical(value)!=data: raise ValueError('canonical journal JSON required')
    return value


@dataclass(frozen=True)
class Limits:
    commits: int
    total_bytes: int

    def validate(self):
        if type(self.commits) is not int or not 1<=self.commits<=numbering.CAPACITY+1 \
                or type(self.total_bytes) is not int or self.total_bytes<1:
            raise ValueError('explicit positive history bounds required')


@dataclass(frozen=True)
class History:
    """Decoded data, not a verified-authority or committed-consumption receipt."""
    git_oids: tuple[str,...]
    event_bytes: tuple[bytes,...]
    anchor_bytes: bytes
    bytes_read: int

    @property
    def events(self):
        return [document(value) for value in self.event_bytes]

    @property
    def anchor(self):
        return document(self.anchor_bytes)


@dataclass(frozen=True)
class Checkpoint:
    """Candidate checkpoint data. Constructing this type verifies nothing."""
    git_oid: str
    anchor_bytes: bytes


class CheckpointVerifier(Protocol):
    """Separately trusted code must establish provenance, freshness and controls.

    No implementation/provider exists in this module. A successful callback
    returns None; unknown provenance/freshness must raise. A transport-supplied
    flag, checksum or internally consistent old snapshot is insufficient.
    """
    @abstractmethod
    def __call__(self, checkpoint: Checkpoint, observed: History) -> None:
        raise NotImplementedError('checkpoint provenance/freshness verifier not implemented')


def commit_links(data):
    if b'\0' in data or b'\n\n' not in data: raise ValueError('malformed journal commit')
    headers,message=data.split(b'\n\n',1)
    rows=headers.split(b'\n')
    if message!=MESSAGE or not rows or not rows[0].startswith(b'tree '):
        raise ValueError('unsupported journal commit layout')
    tree=oid(rows.pop(0)[5:].decode('ascii'))
    parent=None
    if rows and rows[0].startswith(b'parent '): parent=oid(rows.pop(0)[7:].decode('ascii'))
    if len(rows)!=2: raise ValueError('single-parent journal commits required; extra headers refuse')
    for prefix,row in zip((b'author ',b'committer '),rows):
        if not row.startswith(prefix): raise ValueError('journal commit identity header differs')
        identity=row[len(prefix):]
        identity.decode('utf-8')
        if len(identity)>512 or any(c<32 or c==127 for c in identity) \
                or not re.fullmatch(rb'[^<>]+ <[^<>]+> [0-9]{1,12} [+-][0-9]{4}',identity):
            raise ValueError('unsupported journal commit identity header')
    return tree,parent


def decode_history(policy, head_oid, get_object: Callable, limits: Limits):
    """Decode complete raw history using get_object(oid, byte_limit)->(kind, bytes).

    The future getter must enforce limits while reading. Post-read checks here
    do not establish a transport's allocation bound, authentication or freshness.
    No ref read, mutation, retries, fallback, process or network call is supplied.
    """
    pin=numbering.policy(policy)
    limits.validate()
    cache={};used=0
    def read(identity,kind):
        nonlocal used
        oid(identity)
        if identity not in cache:
            allowance=min(OBJECT_LIMITS[kind],limits.total_bytes-used)
            if allowance<1: raise ValueError('Git journal byte bound exhausted')
            actual,data=get_object(identity,allowance)
            if actual!=kind or not isinstance(data,bytes) or len(data)>allowance:
                raise ValueError('Git object kind/byte bound differs')
            if object_oid(actual,data)!=identity: raise ValueError('Git object content identity differs')
            cache[identity]=(actual,data);used+=len(data)
        actual,data=cache[identity]
        if actual!=kind: raise ValueError('Git object reused under a different kind')
        return data
    rows=[];seen=set();current=oid(head_oid)
    while current is not None:
        if current in seen or len(rows)>=limits.commits: raise ValueError('Git ancestry cycle/history bound')
        seen.add(current)
        tree,parent=commit_links(read(current,'commit'))
        entries=read(tree,'tree')
        if len(entries)!=len(TREE_PREFIX)+20 or not entries.startswith(TREE_PREFIX):
            raise ValueError('journal tree must contain only regular journal.json')
        blob=entries[len(TREE_PREFIX):].hex()
        record=document(read(blob,'blob'))
        numbering.exact(record,{'schema','policySha256','event'},'Git journal record')
        if record['schema']!='roseos.mustang-git-event.v1' or record['policySha256']!=pin:
            raise ValueError('Git journal schema/policy differs')
        if (parent is None)!=(record['event'] is None): raise ValueError('complete journal genesis required')
        rows.append((current,record['event']))
        current=parent
    rows.reverse()
    events=[event for _,event in rows[1:]]
    anchor=numbering.expected_anchor(policy,events)
    return History(tuple(identity for identity,_ in rows),tuple(canonical(event) for event in events),
                   canonical(anchor),used)


def _check_checkpoint(policy, checkpoint, history, verifier: CheckpointVerifier | None=None):
    if not callable(verifier): raise ValueError('independent checkpoint verification unavailable')
    if type(checkpoint) is not Checkpoint or type(history) is not History:
        raise ValueError('checkpoint and history data required')
    oid(checkpoint.git_oid)
    anchor=document(checkpoint.anchor_bytes)
    numbering.exact(anchor,numbering.ANCHOR_KEYS,'checkpoint anchor')
    index=numbering.counter(anchor['counter'])
    if index>=len(history.git_oids) or history.git_oids[index]!=checkpoint.git_oid:
        raise ValueError('checkpoint is absent from ancestry; rollback/fork recovery refused')
    numbering.snapshot(policy,anchor,history.events[:index])
    # Passing structure checks does not authenticate/freshen this checkpoint.
    if verifier(checkpoint,history) is not None:
        raise ValueError('checkpoint verifier must raise on uncertainty; flags are not evidence')


def confirm_from_objects(policy, proposal, head_oid, get_object, limits, checkpoint, verifier=None):
    if not callable(verifier): raise ValueError('independent checkpoint verification unavailable')
    # Work from our own immutable object decoding, not a caller-constructed History.
    policy=json.loads(json.dumps(policy))
    observed=decode_history(policy,head_oid,get_object,limits)
    _check_checkpoint(policy,checkpoint,observed,verifier)
    return numbering.confirm(policy,proposal,observed.anchor,observed.events)


def reply_observation(reply):
    """Non-authoritative diagnostics; never classify an error as a known conflict."""
    if reply is None: return 'no-reply'
    if isinstance(reply,dict):
        if reply.get('errors'): return 'graphql-errors'
        body=reply.get('data')
        if isinstance(body,dict) and isinstance(body.get('updateRefs'),dict):
            return 'success-shaped-reply'
    return 'other-reply'


class UnresolvedRefWrite(ValueError):
    """Consumption is unresolved, not absent. This exception never permits reuse."""
    retry_allowed=False
    reallocation_allowed=False

    def __init__(self,attempted_oid,observed_oid,reply):
        self.attempted_oid=attempted_oid
        self.observed_oid=observed_oid
        self.reply_observation=reply_observation(reply)
        super().__init__('ref write consumption unresolved after '+self.reply_observation+
                         '; no automatic retry, new reservation or reuse')


def reconcile_ref_reply(policy, proposal, attempted_oid, reply, head_oid, get_object, limits,
                        checkpoint, verifier=None):
    """Read-only exact-commit reconciliation after any reply, including ambiguity.

    Success/error/absence in a transport reply does not establish consumption,
    nonconsumption, freshness or authorization. An unchanged origin or another
    writer's event remains unresolved. This function performs no mutation/retry
    and does not authorize any downstream operation or continued denied action.
    """
    oid(attempted_oid)
    if not callable(verifier): raise ValueError('independent checkpoint verification unavailable')
    policy=json.loads(json.dumps(policy))
    observed=decode_history(policy,head_oid,get_object,limits)
    _check_checkpoint(policy,checkpoint,observed,verifier)
    index=numbering.validate_proposal(policy,proposal,observed.events)
    if index>=len(observed.git_oids) or observed.git_oids[index]!=attempted_oid \
            or observed.events[index-1]!=proposal['reservation']:
        raise UnresolvedRefWrite(attempted_oid,head_oid,reply)
    return numbering.confirm(policy,proposal,observed.anchor,observed.events)


if __name__=='__main__':
    raise SystemExit('inactive read-only codec: no transport, checkpoint provider, authority writer or allocator')
